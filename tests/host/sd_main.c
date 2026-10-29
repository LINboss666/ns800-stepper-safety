/*
 * sd_main.c - 安全数据语义链 (phase8-safety-data-semantics, 2026-10-22)
 *
 * 真编译并执行的生产模块(不是桩):
 *   applications/sensor_service.c  —— s_collect()/sensor_snapshot()/自检隔离
 *   applications/blackbox.c        —— bb_sample()/bb_to_sample()/版本解释/blackbox_dump()
 *
 * 显式可注入桩(全部在本文件定义, 用例判定靠它们的返回值与计数器):
 *   RT-Thread 原语            = shim_sd/rtthread.h(线程**不真起**, kprintf 捕获)
 *   GPIO(rt_pin_*)           = 本文件(注入任意读数, 含 -1/2; 计读/写/mode/attach)
 *   safety_pin()             = 本文件(按名字解析, 可注入解析失败; 计数)
 *   safety_state_get()       = 本文件
 *   adxl345_/current_adc_/tmc2209_/motor_ 服务 = 本文件假服务(UART 读计数)
 *   project_config_get_snapshot() = 本文件
 *   ns_storage/ns_log      = 本文件**内存环**模拟(不需要真实 Flash 驱动)
 *
 * 不在本链编译: safety_gpio.c/safety_thread.c/safety_state.c/motor.c/tmc2209.c/
 * diagnosis.c/supervisor.c/ns_storage.c —— 由桩替代, 证明的是**数据语义与离线逻辑**。
 *
 * 判定纪律: 每个用例 ID 恰好执行一次并打印 CASE|<id>|PASS|FAIL|detail;
 * 结尾 SUMMARY 自报 pass/fail, 任何 FAIL 或非零异常都让进程 rc≠0。
 * 文本断言只用于 SD10/"输出本身即行为"这一类, 其余一律用掩码/返回值/计数器。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>

#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "safety_state.h"
#include "safety_io_semantics.h"
#include "sensor_service.h"
#include "motor.h"
#include "tmc2209.h"
#include "adxl345.h"
#include "current_adc.h"
#include "project_config.h"
#include "ns_storage.h"
#include "blackbox.h"

/* 被测 TU 的真实代码路径 */
#include "sensor_service.c"
#include "blackbox.c"

/* ======================= 捕获式 kprintf ======================= */

#define CAP_BUF 8192
static char  g_cap[CAP_BUF];
static size_t g_cap_len;

int sd_kprintf(const char *fmt, ...)
{
    va_list ap;
    int n;
    char tmp[512];

    va_start(ap, fmt);
    n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0) return 0;
    fputs(tmp, stdout);
    if (g_cap_len + strlen(tmp) < CAP_BUF) { strcat(g_cap, tmp); g_cap_len += strlen(tmp); }
    return n;
}

static void cap_reset(void) { g_cap[0] = '\0'; g_cap_len = 0; }
static const char *cap_text(void) { return g_cap; }
static int cap_has(const char *needle) { return strstr(g_cap, needle) != RT_NULL; }

/* ======================= GPIO / safety_pin 桩 ======================= */

#define SIM_PIN_MAX 8
struct sim_pin
{
    const char *name;
    rt_base_t   handle;         /* <0 = 解析失败 */
    rt_int32_t  level;          /* 注入值, 允许 -1/2/7 等非法读数 */
    int         resolve_calls;
    int         read_calls;
    int         write_calls;
    int         mode_calls;
    int         attach_calls;
};

static struct sim_pin g_pins[SIM_PIN_MAX] =
{
    { PIN_NAME_ENN_FEEDBACK, 40, PIN_HIGH, 0, 0, 0, 0, 0 },
    { PIN_NAME_LIMIT_MIN,    41, PIN_HIGH, 0, 0, 0, 0, 0 },
    { PIN_NAME_LIMIT_MAX,    42, PIN_HIGH, 0, 0, 0, 0, 0 },
    { PIN_NAME_TMC_DIAG,     43, PIN_HIGH, 0, 0, 0, 0, 0 },
};
static int g_pin_n = 4;
static int g_gpio_write_total, g_gpio_mode_total, g_gpio_attach_total;

static struct sim_pin *pin_by_name(const char *name)
{
    int i;
    for (i = 0; i < g_pin_n; ++i)
        if (strcmp(g_pins[i].name, name) == 0) return &g_pins[i];
    return RT_NULL;
}
static struct sim_pin *pin_by_handle(rt_base_t h)
{
    int i;
    for (i = 0; i < g_pin_n; ++i)
        if (g_pins[i].handle == h) return &g_pins[i];
    return RT_NULL;
}

static void sim_pin_reset(void)
{
    int i;
    for (i = 0; i < g_pin_n; ++i)
    {
        g_pins[i].resolve_calls = 0; g_pins[i].read_calls = 0;
        g_pins[i].write_calls = 0;   g_pins[i].mode_calls = 0;
        g_pins[i].attach_calls = 0;
        g_pins[i].handle = (rt_base_t)(40 + i);
        g_pins[i].level = PIN_HIGH;
    }
    g_gpio_write_total = 0; g_gpio_mode_total = 0; g_gpio_attach_total = 0;
}

/* 生产代码里的 safety_pin() 来自 safety_gpio.c, 本链按桩提供(可注入解析失败) */
rt_base_t safety_pin(const char *name)
{
    struct sim_pin *p = pin_by_name(name);
    if (p == RT_NULL) return -1;
    p->resolve_calls++;
    return p->handle;               /* handle<0 表示"这颗脚解析不出来" */
}

rt_base_t rt_pin_get(const char *name) { return safety_pin(name); }

rt_base_t rt_pin_read(rt_base_t pin)
{
    struct sim_pin *p = pin_by_handle(pin);
    if (p == RT_NULL) return -1;
    p->read_calls++;
    return p->level;
}

void rt_pin_write(rt_base_t pin, rt_base_t value)
{
    struct sim_pin *p = pin_by_handle(pin);
    (void)value;
    if (p != RT_NULL) p->write_calls++;
    g_gpio_write_total++;
}

void rt_pin_mode(rt_base_t pin, rt_base_t mode)
{
    struct sim_pin *p = pin_by_handle(pin);
    (void)mode;
    if (p != RT_NULL) p->mode_calls++;
    g_gpio_mode_total++;
}

rt_err_t rt_pin_attach_irq(rt_base_t pin, rt_uint32_t mode,
                           void (*irq_cb)(void *args), void *irq_args)
{
    struct sim_pin *p = pin_by_handle(pin);
    (void)mode; (void)irq_cb; (void)irq_args;
    if (p != RT_NULL) p->attach_calls++;
    g_gpio_attach_total++;
    return RT_EOK;
}

rt_err_t rt_pin_irq_enable(rt_base_t pin, rt_uint32_t enable)
{
    (void)pin; (void)enable;
    return RT_EOK;
}

static void sim_inject_unresolved(const char *name)
{
    struct sim_pin *p = pin_by_name(name);
    if (p != RT_NULL) p->handle = -1;
}
/* 恢复解析(注入失败的逆操作): handle 回到原来的编号 */
static void sim_restore_resolved(const char *name)
{
    int i;
    for (i = 0; i < g_pin_n; ++i)
        if (strcmp(g_pins[i].name, name) == 0) { g_pins[i].handle = (rt_base_t)(40 + i); return; }
}
static void sim_inject_level(const char *name, rt_int32_t lv)
{
    struct sim_pin *p = pin_by_name(name);
    if (p != RT_NULL) p->level = lv;
}
static int sim_resolve(const char *name)
{
    struct sim_pin *p = pin_by_name(name);
    return p ? p->resolve_calls : -1;
}
static int sim_reads(const char *name)
{
    struct sim_pin *p = pin_by_name(name);
    return p ? p->read_calls : -1;
}
static int sim_writes(const char *name)
{
    struct sim_pin *p = pin_by_name(name);
    return p ? p->write_calls : -1;
}

/* ======================= 其他生产模块的假服务 ======================= */

static safety_state_t g_safety_state = SAFETY_READY;
safety_state_t safety_state_get(void) { return g_safety_state; }

static int g_imu_calls, g_adc_calls, g_uart_sg_calls, g_snap_calls;
static rt_bool_t g_imu_ok = RT_TRUE, g_adc_ok = RT_TRUE, g_sg_ok = RT_TRUE;

rt_err_t adxl345_read_mg(rt_int16_t *x, rt_int16_t *y, rt_int16_t *z)
{
    g_imu_calls++;
    if (!g_imu_ok) return -RT_EIO;
    *x = 12; *y = -34; *z = 998;
    return RT_EOK;
}
subsys_health_t adxl345_get_health(void) { return g_imu_ok ? SUBSYS_OK : SUBSYS_FAILED; }

static rt_uint32_t g_filtered = 4321;
static current_adc_cal_source_t g_cal_source = CURRENT_ADC_CAL_THEORETICAL;
static rt_uint32_t g_cal_version = 1;
rt_err_t current_adc_read_measurement_cal(rt_uint32_t *raw, float *mv, float *ma,
                                          current_adc_cal_t *cal)
{
    g_adc_calls++;
    (void)mv;
    if (!g_adc_ok) return -RT_EIO;
    *raw = 2048;
    *ma = 512.0f;
    if (cal != RT_NULL)
    {
        cal->offset_mv = 1650.0f;
        cal->gain_v_per_a = 0.6f;
        cal->source = (rt_uint8_t)g_cal_source;
        cal->version = g_cal_version;
    }
    return RT_EOK;
}
rt_err_t current_adc_read_measurement(rt_uint32_t *raw, float *mv, float *ma)
{ return current_adc_read_measurement_cal(raw, mv, ma, RT_NULL); }
rt_uint32_t current_adc_get_filtered_raw(void) { return g_filtered; }
current_adc_cal_source_t current_adc_cal_source(void) { return g_cal_source; }
subsys_health_t current_adc_get_health(void) { return g_adc_ok ? SUBSYS_DEGRADED : SUBSYS_FAILED; }

rt_err_t tmc2209_read_sg_result(rt_uint16_t *sg)
{
    g_uart_sg_calls++;
    if (!g_sg_ok) return -RT_ETIMEOUT;
    *sg = 777;
    return RT_EOK;
}
subsys_health_t tmc2209_get_health(void) { return g_sg_ok ? SUBSYS_OK : SUBSYS_FAILED; }

static rt_uint8_t g_motor_state = 4;      /* MOTOR_FAULT: 本轮没有任何运动请求 */
rt_err_t motor_get_snapshot(motor_snapshot_t *snap)
{
    g_snap_calls++;
    rt_memset(snap, 0, sizeof(*snap));
    snap->state = (motor_state_t)g_motor_state;
    snap->current_hz = 0;
    snap->dir = 0;
    snap->health = SUBSYS_OK;
    return RT_EOK;
}
subsys_health_t motor_get_health(void) { return SUBSYS_OK; }

rt_err_t project_config_get_snapshot(project_config_t *out)
{
    rt_memset(out, 0, sizeof(*out));
    out->pre_fault_ms = 2000;
    out->post_fault_ms = 1000;
    return RT_EOK;
}

/* ---------- 内存版 ns_storage / ns_log ---------- */

#define SIM_LOG_SLOTS 64
static ns_fault_sample_t g_log[SIM_LOG_SLOTS];
static rt_uint32_t g_log_used, g_log_valid, g_log_submit_calls;

rt_err_t ns_storage_init(void) { return RT_EOK; }
rt_err_t ns_log_append(const ns_fault_sample_t *s) { return ns_log_submit(s); }
rt_err_t ns_log_submit(const ns_fault_sample_t *s)
{
    g_log_submit_calls++;
    if (g_log_used >= SIM_LOG_SLOTS) return -RT_EBUSY;
    g_log[g_log_used++] = *s;
    g_log_valid++;
    return RT_EOK;
}
rt_err_t ns_log_flush(int timeout_ms) { (void)timeout_ms; return RT_EOK; }
rt_err_t ns_log_stats(ns_log_stats_t *out)
{
    rt_memset(out, 0, sizeof(*out));
    out->used_slots = g_log_used;
    out->valid_records = g_log_valid;
    out->accepted = g_log_submit_calls;
    out->last_error = RT_EOK;
    return RT_EOK;
}
rt_err_t ns_log_read_slot(rt_uint32_t slot, ns_fault_sample_t *out, rt_uint32_t *sequence)
{
    if (slot >= g_log_used) return -RT_EINVAL;
    *out = g_log[slot];
    if (sequence) *sequence = slot;
    return RT_EOK;
}
rt_err_t ns_log_clear(void) { g_log_used = 0; g_log_valid = 0; return RT_EOK; }

/* ======================= RT-Thread 桩(假线程) ======================= */

static int g_thread_create_calls;
static struct rt_thread g_fake_thread;
static CRITICAL_SECTION g_cs;
static int g_crit_depth;

rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick)
{
    (void)stack_size; (void)prio; (void)tick;
    g_thread_create_calls++;
    g_fake_thread.name = name;
    g_fake_thread.entry = entry;
    g_fake_thread.param = param;
    g_fake_thread.h = NULL;                   /* 不真起线程: 测试单线程且确定 */
    return &g_fake_thread;
}
rt_err_t rt_thread_startup(rt_thread_t t) { (void)t; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t) { (void)t; return RT_EOK; }

void rt_enter_critical(void) { g_crit_depth++; }
void rt_exit_critical(void) { if (g_crit_depth > 0) g_crit_depth--; }
rt_base_t rt_hw_interrupt_disable(void) { return 0; }
void rt_hw_interrupt_enable(rt_base_t level) { (void)level; }

/* ======================= 用例框架 ======================= */

static int g_cases, g_pass, g_fail;

static void check(const char *id, int ok, const char *detail)
{
    g_cases++;
    if (ok) g_pass++; else g_fail++;
    printf("CASE|%s|%s|%s\n", id, ok ? "PASS" : "FAIL", detail);
    fflush(stdout);
}

#define ACTIVE_NOW   ((rt_uint8_t)SAI_ACTIVE_MASK)

/* 把生产上下文复位到"刚初始化"状态, 走的是真实 init 代码 */
static void reset_all(void)
{
    sim_pin_reset();
    g_imu_calls = g_adc_calls = g_uart_sg_calls = g_snap_calls = 0;
    g_imu_ok = g_adc_ok = g_sg_ok = RT_TRUE;
    g_safety_state = SAFETY_READY;
    g_log_used = g_log_valid = g_log_submit_calls = 0;
    g_thread_create_calls = 0;
    g_cal_source = CURRENT_ADC_CAL_THEORETICAL;
    g_cal_version = 1;
    cap_reset();
    (void)sensor_service_init();
    (void)blackbox_init();
    s_seq = 0;
    rt_memset(&s_prev, 0, sizeof(s_prev));
    rt_memset(&s_frame, 0, sizeof(s_frame));
    bb_last_seq_seen = 0;
    bb_state = BB_IDLE;
    bb_session = 0;
    bb_session_fault_code = 0;
    bb_ring_idx = 0; bb_ring_count = 0; bb_post_idx = 0; bb_last_pre_n = 0;
}

/* 用一帧采集结果当作"生产最新帧", 供真实 bb_sample() 读取 */
static void push_frame(const sensor_frame_t *f)
{
    rt_enter_critical();
    s_frame = *f;
    rt_exit_critical();
    s_seq = f->seq;
}

static int active_of(const sensor_frame_t *f) { return (int)f->sai_active; }

/* ======================= 用例 ======================= */

static void sd01_off(void)
{
    sensor_frame_t f, prev;
    rt_uint32_t seq = 0;
    int ok;
    char d[256];

    reset_all();
    rt_memset(&prev, 0, sizeof(prev));
    s_collect(&f, &prev, &seq);

    ok = (f.sai_active == (SAI_MASK_ENN | SAI_MASK_DIAG)) &&
         (f.sai_active == 0x09u) &&
         (f.sai_valid == f.sai_active) && (f.valid_safety == 1) &&
         (sim_resolve(PIN_NAME_LIMIT_MIN) == 0) &&
         (sim_resolve(PIN_NAME_LIMIT_MAX) == 0) &&
         (sim_reads(PIN_NAME_LIMIT_MIN) == 0) &&
         (sim_reads(PIN_NAME_LIMIT_MAX) == 0) &&
         (sim_writes(PIN_NAME_LIMIT_MIN) == 0) &&
         (g_gpio_mode_total == 0) && (g_gpio_attach_total == 0);
    snprintf(d, sizeof(d), "active=%02X valid=%02X group=%d lim_min_res=%d lim_max_res=%d "
             "lim_reads=%d/%d mode=%d attach=%d",
             f.sai_active, f.sai_valid, f.valid_safety,
             sim_resolve(PIN_NAME_LIMIT_MIN), sim_resolve(PIN_NAME_LIMIT_MAX),
             sim_reads(PIN_NAME_LIMIT_MIN), sim_reads(PIN_NAME_LIMIT_MAX),
             g_gpio_mode_total, g_gpio_attach_total);
    check("SD01-off-masks-limits-never-touched", ok, d);

    /* 同一帧的输出必须把两路限位显示成"配置关闭", 而不是"安全/有效/0" */
    push_frame(&f);
    cap_reset();
    sensor_snapshot();
    ok = cap_has("lim_min=0[DISABLED_BY_CONFIG]") &&
         cap_has("lim_max=0[DISABLED_BY_CONFIG]") &&
         cap_has("enn_fb=1[VALID]") &&
         cap_has("active=09 valid=09") &&
         !cap_has("estop");
    snprintf(d, sizeof(d), "snapshot 三态输出检查: %s", ok ? "OK" : "MISS");
    check("SD01b-off-output-disabled-not-safe", ok, d);
}

static void sd02_on(void)
{
    sensor_frame_t f, prev;
    rt_uint32_t seq = 0;
    int ok;
    char d[256];

    reset_all();
    /* 限位处于"触发态"(LOW=触发)也必须算有效样本, 不能混成读失败 */
    sim_inject_level(PIN_NAME_LIMIT_MIN, PIN_LOW);
    sim_inject_level(PIN_NAME_LIMIT_MAX, PIN_HIGH);
    rt_memset(&prev, 0, sizeof(prev));
    s_collect(&f, &prev, &seq);

    ok = (f.sai_active == 0x0Fu) && (f.sai_valid == 0x0Fu) && (f.valid_safety == 1) &&
         (f.limit_min == PIN_LOW) &&
         (sim_resolve(PIN_NAME_LIMIT_MIN) == 1) &&
         (sim_reads(PIN_NAME_LIMIT_MIN) == 1) &&
         (sim_reads(PIN_NAME_LIMIT_MAX) == 1);
    snprintf(d, sizeof(d), "active=%02X valid=%02X group=%d lim_min=%d res=%d rd=%d",
             f.sai_active, f.sai_valid, f.valid_safety, f.limit_min,
             sim_resolve(PIN_NAME_LIMIT_MIN), sim_reads(PIN_NAME_LIMIT_MIN));
    check("SD02-on-all-four-active-limit-low-still-valid", ok, d);

    push_frame(&f);
    cap_reset();
    sensor_snapshot();
    ok = cap_has("lim_min=0[VALID]") && cap_has("active=0F valid=0F") && !cap_has("estop");
    snprintf(d, sizeof(d), "ON 变体输出: %s", ok ? "OK" : "MISS");
    check("SD02b-on-output-trigger-state-is-valid", ok, d);
}

static void sd03_parse_fail_per_source(void)
{
    struct src { const char *name; rt_uint32_t bit; } srcs[4] =
    {
        { PIN_NAME_ENN_FEEDBACK, SAI_MASK_ENN },
        { PIN_NAME_LIMIT_MIN,    SAI_MASK_LIMIT_MIN },
        { PIN_NAME_LIMIT_MAX,    SAI_MASK_LIMIT_MAX },
        { PIN_NAME_TMC_DIAG,     SAI_MASK_DIAG },
    };
    sensor_frame_t f, prev;
    rt_uint32_t seq;
    int i, bad = 0, tested = 0;
    char d[256];
    int first_fail = -1;

    for (i = 0; i < 4; ++i)
    {
        rt_uint32_t bit = srcs[i].bit;
        if ((ACTIVE_NOW & bit) == 0) continue;        /* 本变体不活动的源: 不适用 */

        reset_all();
        seq = 0;
        rt_memset(&prev, 0, sizeof(prev));
        sim_inject_unresolved(srcs[i].name);           /* 只让这一路解析失败 */
        s_collect(&f, &prev, &seq);
        tested++;

        if (f.sai_active != ACTIVE_NOW) bad++;          /* active 不得缩水 */
        if ((f.sai_valid & bit) != 0) bad++;            /* 失败源必须清位 */
        if ((f.sai_valid & (ACTIVE_NOW & ~bit)) != (ACTIVE_NOW & ~bit)) bad++;
        if (f.valid_safety != 0) bad++;                 /* 聚合必须为 0 */
        if (first_fail < 0 && bad) first_fail = i;
    }
    snprintf(d, sizeof(d), "适用活动源=%d 个, 违规判定数=%d (首个失败源索引=%d)",
             tested, bad, first_fail);
    check("SD03-parse-fail-clears-only-that-valid-bit", (bad == 0 && tested > 0), d);
}

static void sd04_illegal_levels_rejected(void)
{
    const rt_int32_t bad_lv[3] = { -1, 2, 7 };
    const rt_uint32_t bits[4] =
        { SAI_MASK_ENN, SAI_MASK_LIMIT_MIN, SAI_MASK_LIMIT_MAX, SAI_MASK_DIAG };
    const char *names[4] =
        { PIN_NAME_ENN_FEEDBACK, PIN_NAME_LIMIT_MIN, PIN_NAME_LIMIT_MAX, PIN_NAME_TMC_DIAG };
    sensor_frame_t f, prev;
    rt_uint32_t seq;
    int k, i, bad = 0, tested = 0;
    char d[256];

    for (k = 0; k < 3; ++k)
    {
        for (i = 0; i < 4; ++i)
        {
            if ((ACTIVE_NOW & bits[i]) == 0) continue;
            reset_all();
            seq = 0;
            rt_memset(&prev, 0, sizeof(prev));
            prev.enn_feedback = 0; prev.limit_min = 0; prev.limit_max = 0; prev.tmc_diag = 0;
            sim_inject_level(names[i], bad_lv[k]);
            s_collect(&f, &prev, &seq);
            tested++;
            if ((f.sai_valid & bits[i]) != 0) bad++;           /* 非法值不得算有效 */
            if (f.sai_active != ACTIVE_NOW) bad++;
            /* 值字段保留基帧的 0, 绝不能被布尔转换写成 1 */
            if (bits[i] == SAI_MASK_ENN && f.enn_feedback != 0) bad++;
            if (bits[i] == SAI_MASK_LIMIT_MIN && f.limit_min != 0) bad++;
            if (bits[i] == SAI_MASK_LIMIT_MAX && f.limit_max != 0) bad++;
            if (bits[i] == SAI_MASK_DIAG && f.tmc_diag != 0) bad++;
        }
    }
    snprintf(d, sizeof(d), "注入(源,非法值)组合=%d, 被判有效/被写脏的次数=%d", tested, bad);
    check("SD04-illegal-reading-never-becomes-valid-high", (bad == 0 && tested > 0), d);
}

static void sd05_dirty_base_frame(void)
{
    sensor_frame_t f, prev;
    rt_uint32_t seq = 0;
    int ok, ok2, ok3;
    char d[256];

    reset_all();
    rt_memset(&prev, 0xAB, sizeof(prev));       /* 脏基帧: 旧掩码/旧 valid 全是 0xAB */
    prev.sai_active = 0xFF;                      /* 冒充"上帧四路都活动" */
    prev.sai_valid = 0xFF;                       /* 冒充"上帧四路都有效" */
    prev.valid_safety = 1;
    sim_inject_level(PIN_NAME_TMC_DIAG, -1);     /* 本帧 DIAG 实际读不到 */
    s_collect(&f, &prev, &seq);

    ok = (f.sai_active == ACTIVE_NOW) && (f.sai_valid != 0xFF) &&
         ((f.sai_valid & SAI_MASK_DIAG) == 0) &&
         ((f.sai_valid & SAI_MASK_ENN) != 0) && (f.valid_safety == 0);
    snprintf(d, sizeof(d), "脏基帧后 active=%02X valid=%02X group=%d (基帧曾是 FF/FF)",
             f.sai_active, f.sai_valid, f.valid_safety);
    check("SD05-dirty-baseframe-does-not-pollute", ok, d);

    /* OFF 变体额外要求: 限位旧值(0xAB 非零)不得被当成本帧有效读数。
     * ON 变体不适用(限位本来就活动), 因此**不发**这条 id, 由 runner 的变体台账区分。 */
    if (ACTIVE_NOW == 0x09u)
    {
        ok2 = ((f.sai_valid & SAI_MASK_LIMIT_MIN) == 0) &&
              ((f.sai_valid & SAI_MASK_LIMIT_MAX) == 0);
        snprintf(d, sizeof(d), "限位陈旧值不被当有效: %s (valid=%02X active=%02X)",
                 ok2 ? "OK" : "MISS", f.sai_valid, f.sai_active);
        check("SD05b-stale-limit-values-not-valid", ok2, d);
    }

    /* 采样失败后恢复: 重新可读 → 该位重新变有效 */
    reset_all();
    seq = 0;
    rt_memset(&prev, 0, sizeof(prev));
    sim_inject_unresolved(PIN_NAME_ENN_FEEDBACK);
    s_collect(&f, &prev, &seq);
    ok3 = ((f.sai_valid & SAI_MASK_ENN) == 0);
    sim_inject_level(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);      /* 恢复读数 */
    sim_restore_resolved(PIN_NAME_ENN_FEEDBACK);            /* 恢复解析(注入的逆操作) */
    prev = f;
    s_collect(&f, &prev, &seq);
    ok3 = ok3 && ((f.sai_valid & SAI_MASK_ENN) != 0) && (f.valid_safety == 1);
    snprintf(d, sizeof(d), "失败后重新读到即恢复有效: %s", ok3 ? "OK" : "MISS");
    check("SD05c-recovery-makes-source-valid-again", ok3, d);
}

static void sd06_production_context_isolation(void)
{
    sensor_frame_t f, prev;
    sensor_frame_t prev_before;
    rt_uint32_t seq_before, seq_after_prod;
    rt_uint32_t priv_seq = 0;
    rt_uint32_t seq;
    int i;
    int ok;
    char d[256];

    reset_all();
    /* 先真跑几帧生产采集(走真实 s_collect + SG 分频节拍) */
    rt_memset(&prev, 0, sizeof(prev));
    for (i = 0; i < 25; ++i) { s_collect(&f, &prev, &s_seq); s_frame = f; }

    seq_before = s_seq;
    prev_before = s_prev;
    rt_memset(&f, 0xAB, sizeof(f));
    rt_memset(&prev, 0, sizeof(prev));
    for (i = 0; i < 25; ++i) s_collect(&f, &prev, &priv_seq);   /* 私有上下文 */
    seq_after_prod = s_seq;

    ok = (seq_after_prod == seq_before) &&
         (memcmp(&prev_before, &s_prev, sizeof(s_prev)) == 0) &&
         (priv_seq == 25u) && (f.seq == 25u) &&
         /* 原分频语义保持: 私有上下文里 SG 每 10 帧真读一次 UART
          * (两轮各 25 帧, 命中 seq%10==0 的 0/10/20 ⇒ 各 3 次) */
         (g_uart_sg_calls == 3 /* 生产 25 帧 */ + 3 /* 私有 25 帧 */);
    snprintf(d, sizeof(d), "live seq %u->%u, prev 变化=%d, 私有 seq=%u, uart sg 调用=%d",
             seq_before, seq_after_prod,
             memcmp(&prev_before, &s_prev, sizeof(s_prev)) != 0, priv_seq, g_uart_sg_calls);
    check("SD06-private-context-isolated-and-divider-kept", ok, d);

    /* valid/fresh 三源语义保持: 非分频帧 fresh_sg=0 但 valid_sg 沿用 */
    reset_all();
    rt_memset(&prev, 0, sizeof(prev));
    seq = 10;                                   /* 下一帧命中分频点 */
    s_collect(&f, &prev, &seq);                 /* seq 变成 11(已采样 SG) */
    s_collect(&f, &prev, &seq);                 /* 12: 非分频帧 */
    ok = (f.fresh_sg == 0) && (f.valid_sg == 1) && (f.fresh_imu == 1) && (f.valid_imu == 1) &&
         (f.fresh_current == 1) && (f.valid_current == 1);
    snprintf(d, sizeof(d), "非分频帧 sg[v=%d f=%d] imu[v=%d f=%d] cur[v=%d f=%d]",
             f.valid_sg, f.fresh_sg, f.valid_imu, f.fresh_imu, f.valid_current, f.fresh_current);
    check("SD06b-valid-fresh-division-semantics-kept", ok, d);
}

static void sd07_source_frame_propagation(void)
{
    sensor_frame_t sf, prev;
    bb_frame_t bf;
    ns_fault_sample_t s;
    rt_uint32_t seq = 0;
    int ok;
    char d[256];

    reset_all();
    /* DIAG 注入解析失败, ENN 读到 HIGH, 限位按配置 */
    sim_inject_unresolved(PIN_NAME_TMC_DIAG);
    sim_inject_level(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);
    rt_memset(&prev, 0, sizeof(prev));
    s_collect(&sf, &prev, &seq);
    push_frame(&sf);

    rt_memset(&bf, 0, sizeof(bf));
    bb_sample(&bf);                             /* 真实拷贝路径 */
    bb_to_sample(&bf, &s);                      /* 真实打包路径 */
    ok = (bf.seq == sf.seq) && (bf.sai_active == sf.sai_active) &&
         (bf.sai_valid == sf.sai_valid) &&
         (s.flags == (BBFL_PACK_FAULT(0) |
                      BBFL_PACK(((rt_uint32_t)(sf.enn_feedback & 1u) << SAI_BIT_ENN) |
                                ((rt_uint32_t)(sf.limit_min  & 1u) << SAI_BIT_LIMIT_MIN) |
                                ((rt_uint32_t)(sf.limit_max  & 1u) << SAI_BIT_LIMIT_MAX) |
                                ((rt_uint32_t)(sf.tmc_diag   & 1u) << SAI_BIT_DIAG),
                                BBFL_SHIFT_LEVEL) |
                      BBFL_PACK(sf.sai_active, BBFL_SHIFT_ACTIVE) |
                      BBFL_PACK(bf.motor_state, BBFL_SHIFT_MOTOR) |
                      BBFL_PACK(bf.safety_state, BBFL_SHIFT_SAFETY) |
                      BBFL_PACK(sf.sai_valid, BBFL_SHIFT_VALID) |
                      BBFL_PACK(BBFL_SCHEMA_CURRENT, BBFL_SHIFT_SCHEMA))) &&
         (blackbox_sai_schema(s.flags) == BB_SAI_SCHEMA_V1) &&
         (blackbox_sai_valid(s.flags) & SAI_MASK_DIAG) == 0 &&
         blackbox_sai_read_state(s.flags, SAI_MASK_DIAG) == BB_SAI_READ_INVALID;
    snprintf(d, sizeof(d), "flags=%08X active=%02X valid=%02X schema=%s diag_state=%d",
             s.flags, blackbox_sai_active(s.flags), blackbox_sai_valid(s.flags),
             bb_sai_schema_name(blackbox_sai_schema(s.flags)),
             (int)blackbox_sai_read_state(s.flags, SAI_MASK_DIAG));
    check("SD07-same-source-frame-propagates", ok, d);

    /* 关键: 捕获之后现场配置/读数变了, 落盘的解释仍必须是**捕获时**的掩码 */
    sim_inject_level(PIN_NAME_TMC_DIAG, PIN_HIGH);      /* DIAG 现在"可读了" */
    bb_to_sample(&bf, &s);
    ok = ((blackbox_sai_valid(s.flags) & SAI_MASK_DIAG) == 0) &&
         blackbox_sai_read_state(s.flags, SAI_MASK_DIAG) == BB_SAI_READ_INVALID;
    snprintf(d, sizeof(d), "捕获帧不变: valid=%02X (DIAG 仍 INVALID)",
             blackbox_sai_valid(s.flags));
    check("SD07b-no-recompute-at-write-time", ok, d);
    (void)active_of(&sf);
}

static void sd08_flags_vector_and_legacy_fields(void)
{
    bb_frame_t bf;
    ns_fault_sample_t s;
    int ok;
    char d[256];

    reset_all();
    rt_memset(&bf, 0, sizeof(bf));
    bf.fault_code   = 7u;
    bf.enn_feedback = 1u; bf.limit_min = 1u; bf.limit_max = 0u; bf.tmc_diag = 1u;
    bf.sai_active   = (rt_uint8_t)ACTIVE_NOW;
    bf.sai_valid    = (rt_uint8_t)ACTIVE_NOW;
    bf.motor_state  = 4u;
    bf.safety_state = 3u;
    bf.valid_sg = 1; bf.valid_current = 1; bf.valid_imu = 1;
    bf.seq = 1234u;
    bb_to_sample(&bf, &s);

    /* 独立期望值(手算, 不来自被测代码):
     *   [31:28]=1 schema  [27:24]=active/valid(OFF=9, ON=F)  [23:20]=3 safety_state
     *   [19:16]=4 motor   [15:12]=active  [11:8]=1011b=0xB 电平  [7:0]=0x07 故障码 */
    {
        rt_uint32_t nib = (ACTIVE_NOW == 0x09u) ? 0x09u : 0x0Fu;
        rt_uint32_t expect = 0x10000000u | (nib << 24) | (3u << 20) | (4u << 16) |
                             (nib << 12) | 0x0B00u | 0x07u;
        ok = (s.flags == expect);
        snprintf(d, sizeof(d), "flags=%08X 期望=%08X", s.flags, expect);
    }
    check("SD08-flags-position-vector-against-independent-expectation", ok, d);

    /* 未被重排的旧字段: valid 位仍只描述模拟量/STEP; event 与 pre/post 职责保持 */
    ok = (s.valid == (NS_VALID_SG | NS_VALID_CURRENT | NS_VALID_ACCEL |
                      NS_VALID_VIBRATION | NS_VALID_STEP)) &&
         (s.session_id == 0u);
    snprintf(d, sizeof(d), "valid=%02X (STEP 未被借用作安全输入), session=%u", s.valid,
             s.session_id);
    check("SD08b-legacy-valid-and-event-fields-unchanged", ok, d);

    /* pre 帧 event=0, post 帧 event=会话故障码(真实 bb_sample 按状态决定) */
    reset_all();
    {
        sensor_frame_t sf, prev;
        rt_uint32_t seq = 0;
        rt_memset(&prev, 0, sizeof(prev));
        s_collect(&sf, &prev, &seq);
        push_frame(&sf);
        rt_memset(&bf, 0, sizeof(bf));
        bb_state = BB_IDLE;
        bb_sample(&bf);
        ok = (bf.fault_code == 0u);
        bb_state = BB_POST_COLLECT;
        bb_session_fault_code = 11u;
        bb_last_seq_seen = 0;
        bb_sample(&bf);
        ok = ok && (bf.fault_code == 11u);
        bb_to_sample(&bf, &s);
        ok = ok && (s.event == 11u) && ((s.flags & 0xFFu) == 11u);
        bb_state = BB_IDLE;
    }
    snprintf(d, sizeof(d), "pre event=0 / post event=11 且 flags[7:0]=11: %s",
             ok ? "OK" : "MISS");
    check("SD08c-pre-post-event-responsibility-kept", ok, d);
}

static void sd09_legacy_and_unknown_schema(void)
{
    ns_fault_sample_t s;
    int ok;
    char d[256];

    reset_all();
    rt_memset(&s, 0, sizeof(s));
    s.session_id = 9; s.event = 0; s.valid = NS_VALID_STEP;
    s.flags = 0x00340507u;                 /* 版本 nibble = 0: 旧记录, 从未带版本 */
    ns_log_submit(&s);
    rt_memset(&s, 0, sizeof(s));
    s.session_id = 9; s.event = 0; s.valid = NS_VALID_STEP;
    s.flags = 0xE0340507u;                 /* 版本 nibble = E: 未知 schema */
    ns_log_submit(&s);

    ok = (blackbox_sai_schema(0x00340507u) == BB_SAI_SCHEMA_LEGACY) &&
         (blackbox_sai_schema(0xE0340507u) == BB_SAI_SCHEMA_UNKNOWN) &&
         (blackbox_sai_schema(0x19340507u) == BB_SAI_SCHEMA_V1) &&
         (strcmp(bb_sai_schema_name(BB_SAI_SCHEMA_LEGACY), "LEGACY_UNVERSIONED") == 0) &&
         (strcmp(bb_sai_schema_name(BB_SAI_SCHEMA_UNKNOWN), "UNKNOWN_SCHEMA") == 0);
    snprintf(d, sizeof(d), "版本判定: legacy=%d unknown=%d v1=%d",
             (int)blackbox_sai_schema(0x00340507u),
             (int)blackbox_sai_schema(0xE0340507u),
             (int)blackbox_sai_schema(0x19340507u));
    check("SD09-schema-dispatch-by-record-not-by-board", ok, d);

    /* 真实 dump 路径: 旧/未知记录只保留 raw, 不得被解释成"已确认的 ENN/急停状态" */
    cap_reset();
    blackbox_dump(1, (char *[]){ (char *)"blackbox_dump", (char *)"5" });
    ok = cap_has("sai_schema=LEGACY_UNVERSIONED") &&
         cap_has("sai_schema=UNKNOWN_SCHEMA") &&
         cap_has("raw_flags=00340507") &&
         cap_has("raw_flags=E0340507") &&
         !cap_has("active=09") &&          /* 旧记录不允许被补出 active */
         !cap_has("[VALID]");              /* 也不允许出现逐路有效判定 */
    snprintf(d, sizeof(d), "dump 文本: legacy=%d unknown=%d 未被补解释=%d",
             cap_has("sai_schema=LEGACY_UNVERSIONED"), cap_has("sai_schema=UNKNOWN_SCHEMA"),
             (!cap_has("active=09") && !cap_has("[VALID]")));
    check("SD09b-dump-keeps-legacy-raw-uninterpreted", ok, d);
}

static void sd10_output_distinguishes_states(void)
{
    sensor_frame_t f, prev;
    bb_frame_t bf;
    ns_fault_sample_t s;
    rt_uint32_t seq = 0;
    int ok;
    char d[256];

    reset_all();
    /* 同时凑出四种状态: ENN 有效 / DIAG 活动但读失败 / 限位(OFF 时禁用) */
    sim_inject_unresolved(PIN_NAME_TMC_DIAG);
    rt_memset(&prev, 0, sizeof(prev));
    s_collect(&f, &prev, &seq);
    push_frame(&f);
    cap_reset();
    sensor_snapshot();

    ok = cap_has("enn_fb=1[VALID]") && cap_has("diag=0[INVALID_NO_SAMPLE]");
    if (ACTIVE_NOW == 0x09u)
        ok = ok && cap_has("lim_min=0[DISABLED_BY_CONFIG]");
    else
        ok = ok && cap_has("lim_min=1[VALID]");
    ok = ok && !cap_has("estop") && !cap_has("急停按钮");
    snprintf(d, sizeof(d), "snapshot: enn/diag/限位三态齐备且无急停虚构 = %s",
             ok ? "OK" : "MISS");
    check("SD10a-snapshot-distinguishes-valid-invalid-disabled", ok, d);

    /* 新格式记录 + 旧格式记录同屏, dump 必须分别标注 */
    reset_all();
    rt_memset(&bf, 0, sizeof(bf));
    bf.fault_code = 5u; bf.enn_feedback = 1u; bf.limit_max = 1u;
    bf.sai_active = (rt_uint8_t)ACTIVE_NOW;
    bf.sai_valid  = (rt_uint8_t)(ACTIVE_NOW & (rt_uint8_t)~SAI_MASK_DIAG);
    bb_to_sample(&bf, &s);
    ns_log_submit(&s);
    rt_memset(&s, 0, sizeof(s));
    s.flags = 0x00340507u;                        /* 旧记录 */
    ns_log_submit(&s);

    cap_reset();
    blackbox_dump(1, (char *[]){ (char *)"blackbox_dump", (char *)"5" });
    ok = cap_has("sai_schema=V1") && cap_has("sai_schema=LEGACY_UNVERSIONED") &&
         cap_has("diag=0[INVALID_NO_SAMPLE]") &&
         (cap_has("enn_fb=1[VALID]") || cap_has("lim_max=1[VALID]")) &&
         !cap_has("estop");
    snprintf(d, sizeof(d), "dump: V1 与旧格式并存且逐路标注可读性 = %s", ok ? "OK" : "MISS");
    check("SD10b-dump-shows-version-and-per-channel-readability", ok, d);
}

static void sd12_cal_snapshot_source(void)
{
    sensor_frame_t sf, prev;
    rt_uint32_t seq = 0;
    int ok;
    char d[160];

    reset_all();
    g_cal_source = CURRENT_ADC_CAL_MEASURED;
    g_cal_version = 7;
    rt_memset(&prev, 0, sizeof(prev));
    s_collect(&sf, &prev, &seq);
    ok = (sf.current_cal_source == CURRENT_ADC_CAL_MEASURED) &&
         sf.valid_current && sf.fresh_current;
    g_cal_source = CURRENT_ADC_CAL_THEORETICAL;
    g_cal_version = 8;
    ok = ok && sf.current_cal_source == CURRENT_ADC_CAL_MEASURED;
    snprintf(d, sizeof(d), "frame_src=%u live_src=%u", (unsigned)sf.current_cal_source,
             (unsigned)g_cal_source);
    check("SD12-cal-source-same-snapshot", ok, d);
}

static void sd11_readonly_and_layout(void)
{
    sensor_frame_t f, prev;
    bb_frame_t bf;
    ns_fault_sample_t s;
    rt_uint32_t seq;
    int i, sg_before, ok;
    char d[320];

    reset_all();
    rt_memset(&prev, 0, sizeof(prev));
    seq = 0;
    sg_before = g_uart_sg_calls;
    for (i = 0; i < 20; ++i) { s_collect(&f, &prev, &seq); push_frame(&f); }
    for (i = 0; i < 5; ++i)
    {
        s_collect(&f, &prev, &seq);
        push_frame(&f);
        rt_memset(&bf, 0, sizeof(bf));
        bb_sample(&bf);
        bb_to_sample(&bf, &s);
        ns_log_submit(&s);
    }

    /* GPIO: 一次写都没有; 也不 mode/attach_irq(限位关闭时连解析都没有, 见 SD01) */
    ok = (g_gpio_write_total == 0) && (g_gpio_mode_total == 0) && (g_gpio_attach_total == 0);
    snprintf(d, sizeof(d), "gpio writes=%d mode=%d attach=%d (必须全 0)",
             g_gpio_write_total, g_gpio_mode_total, g_gpio_attach_total);
    check("SD11a-no-gpio-write-in-sampling-or-conversion", ok, d);

    /* UART: 只有 SG_RESULT 的分频读, 没有任何配置写(本链根本不提供写入口) */
    ok = (g_uart_sg_calls - sg_before) == 3;      /* 25 帧里命中 3 次分频点 */
    snprintf(d, sizeof(d), "sg UART 读=%d (25 帧, 分频 10 ⇒ 3 次; 无写入口可用)",
             g_uart_sg_calls - sg_before);
    check("SD11b-uart-only-the-divided-sg-read", ok, d);

    /* 布局: **应用载荷结构与外层存储格式都没动**。
     * ns_fault_sample_t 是 48B、flags 仍是末字段(@44); ns_storage.c 把它序列化到
     * 页面偏移 56(12B 记录头 + @44), 该文件本轮未修改, 也不在本链编译范围内 ——
     * 页面偏移/CRC/分区不变性由"文件未改 + 结构不变"共同保证(交付处逐条核对)。 */
    ok = (sizeof(ns_fault_sample_t) == 48u) &&
         (offsetof(ns_fault_sample_t, flags) == 44u) &&
         (sizeof(bb_frame_t) == 48u);
    snprintf(d, sizeof(d), "sizeof(ns_fault_sample_t)=%u offsetof(flags)=%u "
             "sizeof(bb_frame_t)=%u sizeof(sensor_frame_t)=%u",
             (unsigned)sizeof(ns_fault_sample_t), (unsigned)offsetof(ns_fault_sample_t, flags),
             (unsigned)sizeof(bb_frame_t), (unsigned)sizeof(sensor_frame_t));
    check("SD11c-layout-and-storage-offsets-unchanged", ok, d);
}

/* ======================= main ======================= */

int main(void)
{
    InitializeCriticalSection(&g_cs);

    printf("VARIANT|limit_inputs=%s active_mask=%02X schema=%u\n",
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
           "ON",
#else
           "OFF",
#endif
           (unsigned)SAI_ACTIVE_MASK, (unsigned)BBFL_SCHEMA_CURRENT);

#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
    sd02_on();                       /* ON 变体专属(证明限位触发态仍是有效样本) */
#else
    sd01_off();                      /* OFF 变体专属(证明禁用输入连解析都没有) */
#endif
    sd03_parse_fail_per_source();
    sd04_illegal_levels_rejected();
    sd05_dirty_base_frame();
    sd06_production_context_isolation();
    sd07_source_frame_propagation();
    sd08_flags_vector_and_legacy_fields();
    sd09_legacy_and_unknown_schema();
    sd10_output_distinguishes_states();
    sd12_cal_snapshot_source();
    sd11_readonly_and_layout();

    printf("SUMMARY|cases=%d pass=%d fail=%d\n", g_cases, g_pass, g_fail);
    fflush(stdout);
    DeleteCriticalSection(&g_cs);
    return (g_fail == 0 && g_cases > 0) ? 0 : 1;
}
