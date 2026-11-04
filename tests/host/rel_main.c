/*
 * rel_main.c - M8 SG/ADC 相对异常检测与主动保护的主机验证链(离线, 无设备)
 *
 * 真编译并执行的生产模块(不是桩):
 *   applications/sensor_service.c   —— SG/ADC **样本身份**(sg_seq/cur_seq/tick)的生成
 *   applications/project_config.c   —— v3 profile 持久化 + v2→v3 迁移 + 半套拒绝
 *   applications/diag_relative.c    —— 相对判定引擎、profile 资格、先保护后证据
 *   applications/diagnosis.c        —— RELATIVE 开启门禁、线程覆盖披露、状态行
 * 其余依赖(adxl345/current_adc/tmc2209/motor/safety_state/safety_gpio/ns_storage)
 * 是本文件里的显式可注入桩 —— 证明的是判据与时序逻辑, 不是整机行为。
 *
 * ============================ 时间模型(必须先读) ============================
 * rt_tick_get() = 虚拟时钟 g_now。生产线程体由 run_thread(role, entry, iters) 驱动:
 * 每次循环尾部的 rt_thread_mdelay() 回到调度器(setjmp/longjmp), 因此**跑的是真实
 * 循环体**, 不是照抄的等价代码。
 *   sensor 体的 mdelay ⇒ 时钟 +10ms(100Hz 节拍源, 也就是真机 SENSOR_PERIOD_MS)
 *   diag   体的 mdelay ⇒ 时钟 +10ms 并泵一帧 sensor
 *   ⇒ 两线程严格对在同一 10ms tick 上; SG 仍是"每 10 帧一次"= 100ms(与真机同);
 *     diag 每帧读到 age=10ms 的新鲜帧, dt=10ms。
 * 已知偏差(不得越界解释): 真机上 diag 与 sensor 是抢占式并发, 帧龄在 0..10ms 抖动;
 * 本模型恒为 10ms。用例只依赖"停止喂帧/停止重采样 ⇒ 年龄增长", 不依赖调度抖动,
 * 因此**不能**据此宣称时序精度或真实并发已验证(并发纪律由 M7/S2 链负责)。
 *
 * 判定纪律: 每个用例 id 恰好执行一次, 打印 CASE|<id>|PASS|FAIL|detail;
 * SUMMARY 自报 cases/pass/fail/pre_bad/executed; 进程 rc: 0=全通过, 1=有 FAIL,
 * 4=基础设施故障(未知选择 id 等)。文本断言只用于"输出本身就是被测行为"(短行、
 * 不刷屏、打印次序), 其余一律用返回值、计数器和 latch 字段。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>

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
#include "diag_relative.h"
#include "ns_storage.h"

/* 被测 TU 的真实代码(依赖在前) */
#include "sensor_service.c"
#include "project_config.c"
#include "diag_relative.c"
#include "diagnosis.c"

/* ======================= 捕获式 kprintf ======================= */

#define CAP_BUF 65536
static char   g_cap[CAP_BUF];
static size_t g_cap_len;
/* 裸 NUL 检测: vsnprintf 报的字节数与 strlen 不一致, 只可能发生在输出里含 0x00 时
 * (0x00 是合法 UTF-8, 解码门抓不到它, 必须显式比计数)。 */
static int    g_cap_truncated;

int rel_kprintf(const char *fmt, ...)
{
    va_list ap;
    char tmp[512];
    int n;
    size_t l;

    va_start(ap, fmt);
    n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0) return 0;
    fputs(tmp, stdout);
    l = strlen(tmp);
    if (n != (int)l) g_cap_truncated = 1;      /* 输出里出现过裸 0x00 */
    if (g_cap_len + l + 1u < CAP_BUF) { memcpy(g_cap + g_cap_len, tmp, l); g_cap_len += l; g_cap[g_cap_len] = 0; }
    return n;
}
void rel_capture_reset(void) { g_cap[0] = 0; g_cap_len = 0; g_cap_truncated = 0; }
const char *rel_capture_text(void) { return g_cap; }

static void cap_reset(void) { rel_capture_reset(); }
static const char *cap_text(void) { return g_cap; }
static int cap_has(const char *needle) { return strstr(g_cap, needle) != RT_NULL; }
static int cap_count(const char *needle)
{
    int c = 0;
    const char *p = g_cap;
    while ((p = strstr(p, needle)) != RT_NULL) { c++; p += strlen(needle); }
    return c;
}
/* 最长一行的字节数(RT-Thread 控制台 128B 裁剪的先例) */
static int cap_max_line_len(void)
{
    int best = 0, cur = 0, i;
    for (i = 0; g_cap[i]; ++i)
    {
        if (g_cap[i] == '\n') { if (cur > best) best = cur; cur = 0; }
        else cur++;
    }
    if (cur > best) best = cur;
    return best;
}
/* 捕获区间内是否全是 ASCII(ASCII 是 UTF-8 的真子集, 因此这一步同时证明
 * "无残缺多字节序列"; 裸 NUL 由 g_cap_truncated 单独判, 因为 strlen 会在它那里停下)。 */
static int cap_all_ascii(void)
{
    size_t i;
    for (i = 0; i < g_cap_len; ++i)
        if ((unsigned char)g_cap[i] >= 0x80u || g_cap[i] == 0) return 0;
    return 1;
}

/* ======================= 虚拟时钟 + 协作式线程驱动 ======================= */

enum { ROLE_NONE = 0, ROLE_MAIN, ROLE_SENSOR, ROLE_DIAG };

struct vframe { int role; int budget; int armed; };
#define VDEPTH 4
static struct vframe g_stk[VDEPTH];
static jmp_buf       g_stop[VDEPTH];
static int           g_depth;
static rt_tick_t     g_now;
static long          g_mdelay_calls;
static int           g_ticks;                 /* diag 侧走过的 tick 数 */
static void        (*g_tick_hook)(int tick);  /* 在 diag 节拍里注入状态变化 */
static int           g_cmd_pump;              /* 1 = 命令等待循环也泵 sensor 帧 */
static void        (*g_pump_hook)(int n);     /* 在命令泵帧节拍里注入状态变化 */
static int           g_pump_n;
static int           g_stop_sensor;           /* 1 = 传感器服务一帧都没出过 */

rt_tick_t rel_tick_get(void) { return g_now; }

static void run_thread(int role, void (*entry)(void *), void *param, int iters);

void rel_mdelay(rt_int32_t ms)
{
    int d = g_depth - 1;
    int role = (d >= 0) ? g_stk[d].role : ROLE_NONE;

    g_mdelay_calls++;
    if (role == ROLE_SENSOR)
    {
        g_now += 10u;                     /* 100Hz 节拍源 */
    }
    else if (role == ROLE_DIAG)
    {
        g_now += 10u;
        /* g_stop_sensor: 传感器服务一次都没出过帧(s_seq 保持 0)那种运行期状态。
         * 时钟照走, 但 latest 帧永远取不到 —— REL34 用它验证"取不到帧也必须推进
         * 失效监督", 而不是每次 sleep 直接跳过。 */
        if (!g_stop_sensor) run_thread(ROLE_SENSOR, sensor_thread_entry, RT_NULL, 1);
        g_ticks++;
        if (g_tick_hook) g_tick_hook(g_ticks);
    }
    else
    {
        /* ROLE_MAIN(diag_profile 命令的等待循环)需要真实帧, 否则永远等不到样本;
         * ROLE_NONE(主上下文直接 mdelay)按请求时长走。 */
        if (role == ROLE_MAIN && g_cmd_pump)
        {
            run_thread(ROLE_SENSOR, sensor_thread_entry, RT_NULL, 1);   /* 内部推进 10ms */
            g_pump_n++;
            if (g_pump_hook) g_pump_hook(g_pump_n);
        }
        else
        {
            g_now += (rt_tick_t)(ms > 0 ? ms : 1);
        }
    }
    if (d >= 0 && g_stk[d].armed && --g_stk[d].budget <= 0)
    {
        g_stk[d].armed = 0;
        longjmp(g_stop[d], 1);
    }
}

static void run_thread(int role, void (*entry)(void *), void *param, int iters)
{
    int d = g_depth, jumped;

    if (d >= VDEPTH) return;
    g_depth++;
    g_stk[d].role = role;
    g_stk[d].budget = iters;
    g_stk[d].armed = (iters > 0) ? 1 : 0;
    jumped = setjmp(g_stop[d]);
    if (jumped == 0) entry(param);
    g_depth--;
}

/* 主上下文里驱动命令等待循环: mdelay 推进时钟并泵 sensor */
static void pump(int diag_iters) { run_thread(ROLE_DIAG, diag_thread_entry, RT_NULL, diag_iters); }
static void pump_sensor(int n)   { run_thread(ROLE_SENSOR, sensor_thread_entry, RT_NULL, n); }

/* ======================= 互斥量桩(可注入取锁失败) ======================= */

static int g_mutex_init_fail;
static int g_mutex_take_fail;
static int g_mutex_take_calls;
static int g_mutex_release_calls;
/* -1 = 关闭; >=0 = 只让**第 N 次之后**的 take 失败, 用来精确打中"拿到了采集器之后
 * 才在凭据写入上撞锁"这种中途失败支路(g_mutex_take_fail 会把第一步就拒掉)。
 * 计数只针对 R7D1 的 rel_ctx 这把锁: 同一条命令里 cfg/ctl 锁也会 take, 混着数就
 * 无法确定"第 3 次"落在哪一行代码上。 */
static int g_ctx_take_fail_after = -1;
static int g_ctx_take_calls;
/* 只让名为 "cfg" 的锁取失败: 用来单判 config 写失败支路(全局 take_fail 会先把
 * R7D1 的 ctx 门拒掉, 那条支路就走不到了)。
 * A16 需要的是"同一条命令里前面几次 cfg 取锁都要成, 只让第 3 次(project_config_set_rprof)
 * 失败", 所以再给一个按次计数的口: -1=关, >=0=只让第 N 次之后的 cfg take 失败。 */
static int g_cfg_take_fail;
static int g_cfg_take_calls;
static int g_cfg_take_fail_after = -1;

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint8_t flag)
{
    (void)flag;
    if (g_mutex_init_fail) return -RT_ERROR;
    memset(m, 0, sizeof(*m));
    m->initialized = 1;
    strncpy((char *)m->name, name ? name : "", RT_NAME_MAX - 1);
    return RT_EOK;
}
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t time)
{
    (void)time;
    g_mutex_take_calls++;
    if (g_mutex_take_fail) return -RT_EINTR;
    if (strcmp((const char *)m->name, "rel_ctx") == 0)
    {
        g_ctx_take_calls++;
        if (g_ctx_take_fail_after >= 0 && g_ctx_take_calls > g_ctx_take_fail_after)
            return -RT_EINTR;
    }
    if (strcmp((const char *)m->name, "cfg") == 0)
    {
        g_cfg_take_calls++;
        if (g_cfg_take_fail) return -RT_EINTR;
        if (g_cfg_take_fail_after >= 0 && g_cfg_take_calls > g_cfg_take_fail_after)
            return -RT_EINTR;
    }
    if (!m->initialized) return -RT_EINVAL;
    m->held = 1;
    return RT_EOK;
}
rt_err_t rt_mutex_release(struct rt_mutex *m)
{
    g_mutex_release_calls++;
    m->held = 0;
    return RT_EOK;
}

static int g_enter_critical_calls, g_exit_critical_calls;
void rt_enter_critical(void) { g_enter_critical_calls++; }
void rt_exit_critical(void)  { g_exit_critical_calls++; }
rt_base_t rt_hw_interrupt_disable(void) { return 0; }
void rt_hw_interrupt_enable(rt_base_t level) { (void)level; }

/* ======================= 假线程(不真起) ======================= */

static struct rt_thread g_fake_thread;
static int g_thread_create_calls;

rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick)
{
    (void)stack_size; (void)prio; (void)tick;
    g_thread_create_calls++;
    g_fake_thread.name = name;
    g_fake_thread.entry = entry;
    g_fake_thread.param = param;
    g_fake_thread.started = 0;
    return &g_fake_thread;
}
rt_err_t rt_thread_startup(rt_thread_t t) { if (t) t->started = 1; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t) { (void)t; return RT_EOK; }

/* ======================= 上下文注入(M8-R1 R3.7) =======================
 * diag_profile 的入口先查"ISR 嵌套 / PRIMASK 屏蔽窗口 / 有没有线程对象"。
 * 三个都可以注入, 用例据此断言非法上下文**静默**拒绝(连 usage 都不打印)。 */
static int g_isr_nest, g_primask_masked, g_no_thread;
rt_bool_t rt_interrupt_get_nest(void) { return (rt_bool_t)g_isr_nest; }
rt_bool_t sp_intr_masked(void)        { return (rt_bool_t)g_primask_masked; }
rt_thread_t rt_thread_self(void)      { return g_no_thread ? RT_NULL : &g_fake_thread; }

/* ======================= GPIO / safety_pin 桩 ======================= */

struct sim_pin { const char *name; rt_base_t handle; rt_int32_t level; int reads; };
static struct sim_pin g_pins[] = {
    { PIN_NAME_ENN_FEEDBACK, 40, PIN_HIGH, 0 },
    { PIN_NAME_LIMIT_MIN,    41, PIN_HIGH, 0 },
    { PIN_NAME_LIMIT_MAX,    42, PIN_HIGH, 0 },
    { PIN_NAME_TMC_DIAG,     43, PIN_HIGH, 0 },
};
static int g_pin_n = 4;

rt_base_t safety_pin(const char *name)
{
    int i;
    for (i = 0; i < g_pin_n; ++i) if (strcmp(g_pins[i].name, name) == 0) return g_pins[i].handle;
    return -1;
}
rt_base_t rt_pin_get(const char *name) { return safety_pin(name); }
rt_base_t rt_pin_read(rt_base_t pin)
{
    int i;
    for (i = 0; i < g_pin_n; ++i) if (g_pins[i].handle == pin) { g_pins[i].reads++; return g_pins[i].level; }
    return -1;
}
void rt_pin_write(rt_base_t pin, rt_base_t v) { (void)pin; (void)v; }
void rt_pin_mode(rt_base_t pin, rt_base_t m) { (void)pin; (void)m; }
rt_err_t rt_pin_attach_irq(rt_base_t pin, rt_uint32_t mode, void (*cb)(void *), void *a)
{ (void)pin; (void)mode; (void)cb; (void)a; return RT_EOK; }
rt_err_t rt_pin_irq_enable(rt_base_t pin, rt_uint32_t e) { (void)pin; (void)e; return RT_EOK; }

/* ======================= 传感器源桩(全部可注入) ======================= */

struct sim_src
{
    rt_int16_t mx, my, mz;  int imu_ok;
    rt_uint32_t raw, ema; float ma; int adc_ok;
    rt_uint16_t sg;         int sg_ok;  int sg_reads;
    motor_state_t mstate;   rt_uint32_t mhz; rt_uint8_t mdir, armed;
    tmc_drv_cfg_state_t cfg_state;
    tmc_drv_config_t   planned;
    safety_state_t safety;
};
static struct sim_src S;

static void src_defaults(void)
{
    memset(&S, 0, sizeof(S));
    S.mx = 700; S.my = 700; S.mz = 700; S.imu_ok = 1;
    S.raw = 2000; S.ema = 2000; S.ma = 300.0f; S.adc_ok = 1;
    S.sg = 500; S.sg_ok = 1;
    S.mstate = MOTOR_CRUISE; S.mhz = 500; S.mdir = 0; S.armed = 1;
    S.cfg_state = TMC_DRV_CONFIGURED;
    S.planned.irun = 20; S.planned.ihold = 10; S.planned.mres = 0;
    S.planned.vsense = 1; S.planned.mstep_reg_select = 1;
    S.safety = SAFETY_READY;
}

rt_err_t adxl345_read_mg(rt_int16_t *x, rt_int16_t *y, rt_int16_t *z)
{
    if (!S.imu_ok) return -RT_EIO;
    if (x) *x = S.mx; if (y) *y = S.my; if (z) *z = S.mz;
    return RT_EOK;
}
rt_err_t current_adc_read_measurement_cal(rt_uint32_t *raw, float *mv, float *ma,
                                          current_adc_cal_t *cal)
{
    if (!S.adc_ok) return -RT_EIO;
    if (raw) *raw = S.raw;
    if (mv)  *mv  = (float)S.raw * 3300.0f / 4095.0f;
    if (ma)  *ma  = S.ma;
    if (cal)
    {
        cal->offset_mv = 1650.0f; cal->gain_v_per_a = 0.6f;
        cal->source = (rt_uint8_t)CURRENT_ADC_CAL_THEORETICAL; cal->version = 1;
    }
    return RT_EOK;
}
rt_uint32_t current_adc_get_filtered_raw(void) { return S.ema; }
rt_bool_t current_adc_is_calibrated(void) { return RT_TRUE; }
current_adc_cal_source_t current_adc_cal_source(void) { return CURRENT_ADC_CAL_THEORETICAL; }
subsys_health_t current_adc_get_health(void) { return SUBSYS_DEGRADED; }
subsys_health_t adxl345_get_health(void)     { return S.imu_ok ? SUBSYS_OK : SUBSYS_FAILED; }
subsys_health_t tmc2209_get_health(void)     { return S.sg_ok ? SUBSYS_OK : SUBSYS_FAILED; }
subsys_health_t motor_get_health(void)       { return SUBSYS_OK; }

static int g_cal_ex_applies;
static current_adc_cal_source_t g_cal_ex_last;
rt_err_t current_adc_set_calibration_ex(float o, float g, current_adc_cal_source_t s)
{
    (void)o; (void)g; g_cal_ex_applies++; g_cal_ex_last = s; return RT_EOK;
}

/* SG 源值可以由夹具按"第几次成功读"变化 —— 正常基线本身是波动的, R4 的正/负控
 * 必须靠**源输出**造出这个波动, 而不是手填统计量。 */
static int (*g_sg_hook)(int read_no);
rt_err_t tmc2209_read_sg_result(rt_uint16_t *sg)
{
    S.sg_reads++;
    if (!S.sg_ok) return -RT_EIO;
    if (g_sg_hook) S.sg = (rt_uint16_t)g_sg_hook(S.sg_reads);
    if (sg) *sg = S.sg;
    return RT_EOK;
}
tmc_drv_cfg_state_t tmc2209_get_config_state(void) { return S.cfg_state; }
void tmc2209_get_planned_config(tmc_drv_config_t *out) { if (out) *out = S.planned; }

static int g_snap_fail;                       /* 1 = 连运动事实都取不到 */
rt_err_t motor_get_snapshot(motor_snapshot_t *snap)
{
    if (!snap) return -RT_EINVAL;
    if (g_snap_fail) return -RT_EIO;
    memset(snap, 0, sizeof(*snap));
    snap->state = S.mstate;
    snap->current_hz = S.mhz;
    snap->target_hz = S.mhz;
    snap->dir = S.mdir;
    snap->armed = S.armed;
    snap->health = SUBSYS_OK;
    return RT_EOK;
}
static int g_static_begin_calls, g_static_end_calls, g_static_begin_busy;
static size_t g_static_begin_cap_len, g_static_end_cap_len;
/* R2-B1 的确定接缝: 在**仍然持着静止门的最后一刻**回调一次。用例在这里读"模式与代际
 * 落定了没有", 而不是靠 usleep 猜窗口 —— 产品若把提交留在释放之后, 这里必然读到旧值。
 * 一次性(进回调前先摘掉), 回调里不得打印。 */
static void (*g_static_end_hook)(void);
/* 真实 motor_calibration_static_begin() 在锁内重查 armed/IDLE/hz=0/target=0/
 * stop_fail/SAFETY_READY/停机协调器空闲/PWM 已关/DRV_ENABLE 低。夹具按同样的
 * **环境事实**回答(取到的是环境状态, 不是产品逻辑), 因此"命令有没有把最终复核
 * 放进这道门"是用例可判定的: C2/C5 的复现点就在这里。 */
rt_err_t motor_calibration_static_begin(void)
{
    g_static_begin_calls++;
    g_static_begin_cap_len = g_cap_len;
    if (g_static_begin_busy) return -RT_EBUSY;
    if (S.armed || S.mstate != MOTOR_IDLE || S.mhz != 0 ||
        S.safety != SAFETY_READY) return -RT_EPERM;
    return RT_EOK;
}
void motor_calibration_static_end(void)
{
    void (*h)(void) = g_static_end_hook;
    g_static_end_cap_len = g_cap_len;      /* 门内字节数: 与 begin 相等 => 门内没打印 */
    g_static_end_calls++;
    if (h != RT_NULL) { g_static_end_hook = RT_NULL; h(); }
}

safety_state_t safety_state_get(void) { return S.safety; }

/* --------- safety_post_event: 记录"保护发生在哪个打印位置之前" --------- */
static int   g_post_calls;
static size_t g_post_cap_len;
static int   g_post_reason_lines_at_call;
static int   g_post_active_lines_at_call;
static rt_err_t g_post_rc_inject = RT_EOK;

rt_err_t safety_post_event(rt_uint32_t event)
{
    (void)event;
    g_post_calls++;
    g_post_cap_len = g_cap_len;
    g_post_reason_lines_at_call = cap_count("[DIAG] REL TRIP r=");
    g_post_active_lines_at_call = cap_count("[DIAG] ACTIVE: severe");
    return g_post_rc_inject;
}

/* ======================= ns_storage 内存环(复刻真实语义) ======================= */

#define BLOB_MAX 512
static rt_uint8_t g_blob[BLOB_MAX];
static rt_size_t  g_blob_len;
static int        g_blob_valid;
static int        g_ns_save_rc, g_ns_load_rc;
static int        g_storage_init_rc = RT_EOK;

rt_err_t ns_params_save(const void *data, rt_size_t length)
{
    if (length > BLOB_MAX) return -RT_EFULL;
    if (g_ns_save_rc != RT_EOK) return g_ns_save_rc;
    memcpy(g_blob, data, length);
    g_blob_len = length;
    g_blob_valid = 1;
    return RT_EOK;
}
rt_err_t ns_params_load(void *data, rt_size_t cap, rt_size_t *n)
{
    *n = 0;
    if (!g_blob_valid) return -RT_EEMPTY;
    if (g_ns_load_rc != RT_EOK) return g_ns_load_rc;
    *n = g_blob_len;
    if (cap < *n) return -RT_EFULL;          /* 与真实 ns_params_load 同判据 */
    memcpy(data, g_blob, *n);
    return RT_EOK;
}
rt_err_t ns_storage_init(void) { return g_storage_init_rc; }

/* ======================= 用例台账 ======================= */

static int g_pass, g_fail, g_pre_bad, g_executed;
static int g_checks, g_bad;

#define CHECK(cond, ...) do { g_checks++; if (!(cond)) { g_bad++; printf("    ! "); \
    printf(__VA_ARGS__); printf("\n"); } } while (0)

static void case_begin(const char *id) { (void)id; g_checks = 0; g_bad = 0; g_executed++; cap_reset(); }
static void case_end(const char *id)
{
    if (g_bad == 0) { g_pass++; printf("CASE|%s|PASS|checks=%d\n", id, g_checks); }
    else            { g_fail++; printf("CASE|%s|FAIL|checks=%d bad=%d\n", id, g_checks, g_bad); }
}

/* ======================= profile 构造 ======================= */

static diag_rprof_t make_profile(void)
{
    diag_rprof_t p;
    memset(&p, 0, sizeof(p));
    p.magic = DIAG_RPROF_MAGIC;
    p.abi = DIAG_RPROF_ABI;
    p.flags = RPF_COMMITTED | RPF_FROM_LEARN;
    p.profile_id = 1; p.revision = 1;
    p.hz_nominal = 500; p.hz_low = 400; p.hz_high = 600;
    p.dir_mask = 1u;
    p.phase_mask = RPPH_CRUISE;
    p.tmc_cfg_state = (rt_uint8_t)TMC_DRV_CONFIGURED;
    p.mstep_source = 1; p.tmc_irun = 20; p.tmc_ihold = 10; p.tmc_mres = 0; p.tmc_vsense = 1;
    p.sampling_def = DIAG_RPROF_SAMPLING_DEF;
    p.zero_mean_x1000 = 1660000u; p.zero_min = 1650; p.zero_max = 1670; p.zero_n = 64;
    p.run_adc_mean_x1000 = 2000000u; p.run_adc_min = 1950; p.run_adc_max = 2050;
    p.run_adc_noise = 20; p.run_adc_n = 16;
    p.run_sg_mean_x1000 = 500000u; p.run_sg_min = 480; p.run_sg_max = 520; p.run_sg_n = 12;
    p.learn_sg_seq_first = 1; p.learn_sg_seq_last = 12;
    p.adc_dev_warn = 52; p.adc_dev_trip = 116;
    p.sg_drop_warn = 32; p.sg_drop_trip = 96;
    p.dur_sg_ms = 500; p.dur_adc_ms = 300; p.dur_fused_ms = 300; p.dur_recover_ms = 500;
    p.age_sg_ms = 350; p.age_frame_ms = 100; p.min_new_sg = 3;
    /* R6: 碰撞门沿用项目 vib_impact_mg 默认 2500 mg(明显高于 ~1g 基线)。 */
    p.impact_peak_mg = 2500; p.impact_new_min = 2;
    p.dur_impact_ms = 100; p.age_imu_ms = 100;
    p.threshold_tier = 2;
    p.signature = rel_profile_signature(&p);
    return p;
}

static void install_profile(const diag_rprof_t *p)
{
    cfg_take();
    cfg.rprof = *p;
    cfg_valid = RT_TRUE;
    cfg_health = SUBSYS_OK;
    cfg_release();
}
static void clear_profile(void) { cfg_take(); memset(&cfg.rprof, 0, sizeof(cfg.rprof)); cfg_release(); }

/* ======================= 世界复位 ======================= */

/* 与生产同一条提交协议改变"已武装模式"(M8-R2 R2-B1/R2-A):
 * 单写 d_mode 已经不算数 —— rel_prod_evaluate 只认 rel_prod_commit 落下的模式,
 * 于是"命令线程提交失败却仍然按相对模式判"这种组合在夹具里也造不出来。 */
static void arm(rt_uint8_t m)
{
    if (rel_prod_commit(m, RT_FALSE, RT_NULL) != RT_EOK)
    { g_pre_bad++; printf("PRE|arm_commit_failed m=%u\n", (unsigned)m); }
    d_mode = (diag_mode_t)m;
}

static void world_reset(void)
{
    src_defaults();
    g_post_calls = 0; g_post_cap_len = 0; g_post_reason_lines_at_call = 0;
    g_post_active_lines_at_call = 0;
    g_post_rc_inject = RT_EOK;
    g_cal_ex_applies = 0;
    g_static_begin_calls = 0; g_static_end_calls = 0; g_static_begin_busy = 0;
    g_static_begin_cap_len = 0; g_static_end_cap_len = 0; g_static_end_hook = RT_NULL;
    g_mutex_take_fail = 0; g_mutex_take_calls = 0; g_mutex_release_calls = 0;
    g_ctx_take_fail_after = -1; g_ctx_take_calls = 0; g_cfg_take_fail = 0;
    g_cfg_take_calls = 0; g_cfg_take_fail_after = -1;
    g_mutex_init_fail = 0;
    g_enter_critical_calls = 0; g_exit_critical_calls = 0;
    g_thread_create_calls = 0;
    g_ns_save_rc = RT_EOK; g_ns_load_rc = RT_EOK; g_storage_init_rc = RT_EOK;
    g_blob_valid = 0; g_blob_len = 0;
    g_mdelay_calls = 0;
    g_ticks = 0; g_tick_hook = RT_NULL;
    g_isr_nest = 0; g_primask_masked = 0; g_no_thread = 0;
    g_cmd_pump = 0; g_pump_hook = RT_NULL; g_pump_n = 0;
    g_stop_sensor = 0; g_snap_fail = 0; g_sg_hook = RT_NULL;
    g_depth = 0;

    memset(&s_frame, 0, sizeof(s_frame));
    memset(&s_prev, 0, sizeof(s_prev));
    s_seq = 0; s_tid = RT_NULL; s_health = SUBSYS_UNINIT;

    /* d_mode 与已提交模式在下面 ctl 初始化里一起落(见 rel_prod_commit) */
    d_mode = DIAG_MODE_MONITOR_ONLY;
    d_relative_confirm_given = RT_FALSE;
    memset(&d_prod, 0, sizeof(d_prod));
    diag_engine_reset(&d_prod);
    memset(&d_rel_prod, 0, sizeof(d_rel_prod));
    memset(&d_ctl, 0, sizeof(d_ctl));
    memset(&d_incident, 0, sizeof(d_incident));
    memset(&d_cap, 0, sizeof(d_cap));
    memset(&d_pending, 0, sizeof(d_pending));
    d_pending_zero = RT_FALSE; d_pending_learn = RT_FALSE;
    /* M8-R7D1: 任务层与采集所有权同样是**每例重建**的运行状态。不重置会让上一例
     * 留在 RUNNING 的任务把下一例的 zero 全部 BUSY 拒绝——那不是被测行为, 是夹具泄漏。 */
    memset(&d_job, 0, sizeof(d_job));
    d_job.why = "IDLE";
    d_cap_owner = (rt_uint8_t)REL_CAP_FREE;
    d_job_next_id = 1u;
    d_zero_cleanup = 0u;            /* ARD2 的延迟清理旗: 不重置会把上一例的旗带进下一例 */

    /* M8-R2 R2-A.4: 控制锁**真实创建**。旧 44 例从未调用 diagnosis_init/
     * rel_ctl_lock_init, 于是所有 rel_prod_* 都在"锁不存在"的分支上空跑,
     * 失败/互斥/释放配对与快照一致性从来没有一条判据看到过。
     * 与配置层同一做法: 每例重新初始化, 初始化失败按基础设施故障处理,
     * 而不是降级成"当作有锁"。 */
    d_ctl_inited = RT_FALSE;
    memset(&d_ctl_lock, 0, sizeof(d_ctl_lock));
    /* R7D1 的任务/候选叶锁与控制锁同一次初始化(rel_ctl_init 内部), 这里把"还没初始化"
     * 的状态也一并复位, 否则 rel_ctx_take 会跨例复用一把夹具里从未真正存在过的锁。 */
    d_ctx_inited = RT_FALSE;
    memset(&d_ctx_lock, 0, sizeof(d_ctx_lock));
    rel_prod_init_wipe();
    if (rel_ctl_init() != RT_EOK)
    { printf("PRE|ctl_lock_init_failed\n"); g_pre_bad++; }
    else if (rel_prod_commit((rt_uint8_t)DIAG_MODE_MONITOR_ONLY, RT_FALSE, RT_NULL) != RT_EOK)
    { printf("PRE|ctl_commit_failed\n"); g_pre_bad++; }

    /* 配置层: 重建锁 + 安全默认(等价于一次干净启动, 不走 Flash) */
    rt_mutex_init(&cfg_lock, "cfg", RT_IPC_FLAG_PRIO);
    cfg_lock_ok = RT_TRUE;
    cfg_fill_defaults_nolock();
    cfg_valid = RT_FALSE;
    cfg_health = SUBSYS_DEGRADED;

    g_now = 1000u;                    /* 非 0 起点: tick==0 是"从未采到"的哨兵值 */
    diag_sync_config();
}

/* ======================= 引擎级喂帧助手 ======================= */

struct feed
{
    rel_engine_t *e;
    rel_step_arg_t a;
    rt_uint32_t seq, sg_seq, cur_seq, imu_seq;
    rt_uint32_t t;
    int sg_fresh_every;
    int motion_freeze;      /* 1 = 运动快照身份**不**跟着帧走(REL49 的过期支专用) */
};

static void feed_init(rel_engine_t *e, const diag_rprof_t *p, struct feed *f)
{
    memset(e, 0, sizeof(*e));
    memset(f, 0, sizeof(*f));
    memset(&f->a, 0, sizeof(f->a));
    f->e = e;
    f->a.prof = *p;
    f->a.in.live_signature = p->signature;
    f->a.in.sg_valid = 1; f->a.in.cur_valid = 1; f->a.in.imu_valid = 1;
    f->a.in.motor_state = (rt_uint8_t)MOTOR_CRUISE;
    f->a.in.step_hz = 500;
    f->a.in.dir = 0;
    f->a.in.motion_known = 1;
    /* 引擎级用例默认按"操作员已显式武装"评估: 覆盖丢失的降险义务只在武装时存在,
     * MONITOR_ONLY 的"没有 profile 也只披露"由 REL04/REL15 在生产入口单独测。 */
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);   /* 引擎只认已提交模式(R2-B1) */
    f->a.mode = (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION;
    f->t = 1000u;
    f->seq = 0; f->sg_seq = 0; f->cur_seq = 0; f->imu_seq = 0;
    f->sg_fresh_every = 1;
}

/* 走一帧(10ms), 并可给一次**新的 IMU 采样**(R6 碰撞证据独立于 SG/ADC)。
 * imu_fresh=0 时 imu_seq/imu_tick 一律不动 —— 那正是"外壳帧前进但这一路没采过"。 */
static rel_reason_t feed_step_imu(struct feed *f, rt_uint16_t sg, rt_uint32_t raw,
                                  int sg_fresh, int adc_fresh, int sg_valid, int adc_valid,
                                  rt_int16_t vib, int imu_fresh)
{
    f->t += 10u;
    f->a.now_tick = f->t;
    f->a.in.seq = ++f->seq;
    f->a.in.timestamp = f->t;
    /* 运动快照身份与 sg/cur/imu 走同一条纪律(sensor_service.c 只在 motor_get_snapshot
     * 成功时推进): known=1 的帧, motion_tick 就是本帧时间戳。REL49 的"过期支"用
     * motion_freeze 挡住这一行, 因为生产者按构造不会自己造出"成功但 tick 很老"。 */
    if (f->a.in.motion_known && !f->motion_freeze) f->a.in.motion_tick = f->t;
    if (sg_fresh)  { f->a.in.sg_seq  = ++f->sg_seq;  f->a.in.sg_tick  = f->t; f->a.in.sg = sg; }
    if (adc_fresh) { f->a.in.cur_seq = ++f->cur_seq; f->a.in.cur_tick = f->t; f->a.in.cur_raw = raw; }
    if (imu_fresh) { f->a.in.imu_seq = ++f->imu_seq; f->a.in.imu_tick = f->t; f->a.in.vib_mg = vib; }
    f->a.in.sg_valid = (rt_uint8_t)sg_valid;
    f->a.in.cur_valid = (rt_uint8_t)adc_valid;
    f->a.in.imu_valid = 1;
    return rel_engine_step(f->e, &f->a);
}

/* 走一帧(10ms)。sg/cur 新鲜度、有效位、值都由调用方给; IMU 保持"从未采到"。 */
static rel_reason_t feed_step(struct feed *f, rt_uint16_t sg, rt_uint32_t raw,
                              int sg_fresh, int adc_fresh, int sg_valid, int adc_valid)
{
    return feed_step_imu(f, sg, raw, sg_fresh, adc_fresh, sg_valid, adc_valid, 1200, 0);
}

/* ======================= 用例 ======================= */

/* REL01 样本身份只在真实成功时前进(SG 10Hz / ADC 100Hz) */
static void c_REL01(void)
{
    sensor_frame_t f;
    int i, sg_adv = 0, cur_adv = 0;
    rt_uint32_t psg = 0, pcur = 0;
    int bad_tick = 0;

    world_reset();
    pump_sensor(30);
    for (i = 0; i < 20; ++i)
    {
        pump_sensor(1);
        sensor_service_get_latest(&f);
        if (f.sg_seq != psg)  { sg_adv++; if (f.sg_tick != f.timestamp) bad_tick++; }
        if (f.cur_seq != pcur) cur_adv++;
        psg = f.sg_seq; pcur = f.cur_seq;
    }
    CHECK(sg_adv == 2, "20 帧里 SG 独立样本数=%d(期望 2, SENSOR_SG_DIV=%d)", sg_adv, SENSOR_SG_DIV);
    CHECK(cur_adv == 20, "20 帧里 ADC 独立样本数=%d(期望 20)", cur_adv);
    CHECK(psg == 5, "sg_seq 累计=%u(期望 5)", psg);
    CHECK(pcur == 50, "cur_seq 累计=%u(期望 50)", pcur);
    CHECK(bad_tick == 0, "有 SG 样本的 sg_tick 不等于帧 tick(%d 次)", bad_tick);
    CHECK(f.cur_tick == f.timestamp, "cur_tick=%u != 帧 tick=%u", f.cur_tick, f.timestamp);
}

/* REL02 SG UART 失败 ⇒ 身份冻结 + valid 清零 + tick 不前进 */
static void c_REL02(void)
{
    sensor_frame_t a, b;
    world_reset();
    pump_sensor(21);
    sensor_service_get_latest(&a);
    S.sg_ok = 0;
    pump_sensor(15);
    sensor_service_get_latest(&b);
    CHECK(b.sg_seq == a.sg_seq, "sg_seq 从 %u 变 %u(失败读不得推进身份)", a.sg_seq, b.sg_seq);
    CHECK(b.sg_tick == a.sg_tick, "sg_tick 从 %u 变 %u(失败读不得刷新时间戳)", a.sg_tick, b.sg_tick);
    CHECK(b.valid_sg == 0, "失败期间 valid_sg=%d(期望 0)", b.valid_sg);
    CHECK(b.fresh_sg == 0, "失败期间 fresh_sg=%d(期望 0)", b.fresh_sg);
    CHECK(b.sg_result == a.sg_result, "失败帧把值字段改写了(应保留上次成功值)");
    CHECK(b.cur_seq > a.cur_seq, "SG 失败不该影响 ADC 身份");
}

/* REL03 ADC 失败 ⇒ 同样冻结, 恢复后 +1, 且不写 0 冒充 */
static void c_REL03(void)
{
    sensor_frame_t a, b;
    world_reset();
    pump_sensor(11);
    sensor_service_get_latest(&a);
    S.raw = 4000;
    S.adc_ok = 0;
    pump_sensor(5);
    sensor_service_get_latest(&b);
    CHECK(b.valid_current == 0, "adc_ok=0 期间 valid_current=%d", b.valid_current);
    CHECK(b.cur_seq == a.cur_seq, "cur_seq 从 %u 变 %u", a.cur_seq, b.cur_seq);
    CHECK(b.current_raw == a.current_raw, "失败帧清了零(期望保留 %u)", a.current_raw);
    pump_sensor(1);
    sensor_service_get_latest(&b);
    CHECK(b.valid_current == 0, "仍然失败时 valid 又变成 %d", b.valid_current);
    S.adc_ok = 1;
    pump_sensor(1);
    sensor_service_get_latest(&b);
    CHECK(b.valid_current == 1 && b.cur_seq == a.cur_seq + 1u,
          "恢复后 valid=%d cur_seq=%u(期望 %u)", b.valid_current, b.cur_seq, a.cur_seq + 1u);
    CHECK(b.current_raw == 4000, "恢复后 raw=%u(期望 4000)", b.current_raw);
    CHECK(b.cur_tick == b.timestamp, "恢复后 cur_tick 没刷新");
}

/* REL04 没有有效 profile ⇒ 不宣称任何覆盖, 也绝不 post */
static void c_REL04(void)
{
    rel_engine_t e;
    struct feed f;
    rel_reason_t r;
    diag_rprof_t p;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    memset(&f.a.prof, 0, sizeof(f.a.prof));            /* 无 profile */
    r = feed_step(&f, 5, 4000, 1, 1, 1, 1);            /* 极端"异常"值 */
    CHECK(r == REL_REASON_NONE, "无 profile 时原因=%s(期望 NONE)", rel_reason_name(r));
    r = rel_engine_step(&e, &f.a);
    CHECK(r == REL_REASON_NONE, "重复喂入产生 %s", rel_reason_name(r));
    CHECK(g_post_calls == 0, "无 profile 却 post 了 %d 次", g_post_calls);

    /* 半套 profile 同样不能宣称覆盖 */
    feed_init(&e, &p, &f);
    f.a.prof.run_sg_n = 0;
    f.a.prof.signature = rel_profile_signature(&f.a.prof);
    r = feed_step(&f, 5, 4000, 1, 1, 1, 1);
    CHECK(r == REL_REASON_NONE, "半套 profile 竟然给出 %s", rel_reason_name(r));
}

/* REL05 profile 资格矩阵: 每一类残缺都必须**具名**拒绝 */
static void c_REL05(void)
{
    diag_rprof_t p;
    const char *why = RT_NULL;
    rt_err_t v;

    world_reset();
    v = rel_profile_validate(RT_NULL, &why);
    CHECK(v == -RT_EINVAL && why && strcmp(why, "NULL") == 0, "NULL profile 拒绝理由不对(%s)",
          why ? why : "(null)");

#define REJECT(field_assign, expect) do {                                       \
        p = make_profile(); field_assign;                                       \
        v = rel_profile_validate(&p, &why);                                     \
        CHECK(v == -RT_EINVAL, "%s: 未被拒绝(rc=%d)", expect, v);                \
        CHECK(why && strcmp(why, expect) == 0, "%s: 实际理由=%s", expect,        \
              why ? why : "(null)");                                            \
    } while (0)

    REJECT(p.magic = 0x1234u,                 "MAGIC");
    REJECT(p.sampling_def = 9,                "SAMPLING_DEF");
    REJECT(p.abi = 9,                         "ABI");
    REJECT(p.flags = RPF_FROM_LEARN,          "NOT_COMMITTED");
    REJECT(p.profile_id = 0,                  "PROFILE_ID_ZERO");
    REJECT(p.signature = 0,                   "SIGNATURE_ZERO");
    REJECT(p.hz_low = 700,                    "SPEED_WINDOW");
    REJECT(p.hz_high = 30000,                 "SPEED_WINDOW");
    REJECT(p.dir_mask = 0,                    "DIR_MASK");
    REJECT(p.phase_mask = 0,                  "PHASE_MASK");
    REJECT(p.run_adc_max = 5000,              "RANGE_OVER_FLOW");
    REJECT(p.run_adc_min = 3000,              "BAND_ORDER");
    REJECT(p.run_adc_mean_x1000 = 999999000u, "MEAN_OUT_OF_BAND");
    REJECT(p.run_sg_n = 2,                    "SAMPLE_COUNT");
    REJECT(p.adc_dev_warn = 200,              "THRESHOLD_ORDER");
    REJECT(p.adc_dev_trip = 5000,             "THRESHOLD_BOUND");
    REJECT(p.sg_drop_trip = 600,              "SG_TRIP_UNREACHABLE");
    REJECT(p.dur_sg_ms = 50,                  "DURATIONS");
    REJECT(p.age_sg_ms = 50,                  "SOURCE_AGE_LIMITS");
    REJECT(p.min_new_sg = 1,                  "MIN_NEW_SG");
    REJECT(p.learn_sg_seq_last = 1,           "PROVENANCE");
#undef REJECT

    p = make_profile();
    CHECK(rel_profile_validate(&p, &why) == RT_EOK, "基准 profile 被拒(%s)", why ? why : "?");
    /* 只有 magic==0 才叫"没有 profile", 其它残缺不得共用这个名 */
    p = make_profile(); p.abi = 9;
    CHECK(rel_profile_validate(&p, &why) == -RT_EINVAL && strcmp(why, "ABI") == 0,
          "残缺 profile 被误标成 ABSENT(no_profile_committed)");
}

/* REL06 live 签名绑定驱动配置 */
static void c_REL06(void)
{
    diag_rprof_t p;
    rt_uint32_t base;
    world_reset();
    p = make_profile();
    base = rel_live_signature();
    CHECK(p.signature == base, "基准 profile 签名=%u 但 live=%u", p.signature, base);
    S.planned.irun = 21;
    CHECK(rel_live_signature() != base, "改 irun 后 live 签名没变");
    S.planned.irun = 20;
    CHECK(rel_live_signature() == base, "还原后签名不稳定");
    S.planned.mstep_reg_select = 0;
    CHECK(rel_live_signature() != base, "mstep 来源变化未进签名");
    S.planned.mstep_reg_select = 1;
    S.planned.vsense = 0;
    CHECK(rel_live_signature() != base, "vsense 变化未进签名");
    S.planned.vsense = 1;
    S.cfg_state = TMC_DRV_FAILED;
    CHECK(rel_live_signature() != base, "TMC 配置状态变化未进签名");
    S.cfg_state = TMC_DRV_CONFIGURED;
    CHECK(rel_live_signature() == base, "全部还原后签名应还原");
    p.adc_dev_trip = 90; p.profile_id = 77; p.revision = 5; p.hz_high = 900;
    CHECK(rel_profile_signature(&p) == base, "阈值/id/速度窗被算进了签名");
}

/* REL07 SG 强异常: 独立样本数与毫秒跨度两个条件缺一不可 */
static void c_REL07(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    /* SG 每 10 帧一个新样本(=100ms), 值掉到 trip 以下; ADC 每帧都新且带内 */
    for (i = 1; i <= 100; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_SG_SEVERE, "1000ms 后原因=%s(期望 REL_SG_SEVERE)", rel_reason_name(r));
    CHECK(e.sg_new_cnt >= p.min_new_sg, "独立 SG 样本数=%u < min=%u", e.sg_new_cnt, p.min_new_sg);
    CHECK(e.sg_run_ms >= p.dur_sg_ms, "sg_run_ms=%u < %u", e.sg_run_ms, p.dur_sg_ms);

    /* 未到 500ms 之前不得判严重 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 40; ++i)
    {
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 10) == 0, 1, 1, 1);
        CHECK(r != REL_SG_SEVERE, "400ms(%d 帧)就判 SG severe —— 持续时间判据失效", i);
    }
}

/* REL08 独立纪律: SG 身份不前进 ⇒ 既不加证也不撤证, 由年龄门接手 */
static void c_REL08(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000, 1, 1, 1, 1); /* 第 1 个新样本 */
    for (i = 0; i < 100; ++i)
    {
        r = feed_step(&f, 0, 2000, 0, 1, 1, 1);        /* SG 身份/时间戳冻结, 帧号继续走 */
        CHECK(r != REL_SG_SEVERE, "旧 SG 样本被累计成持续异常(帧 %d)", i);
    }
    CHECK(e.sg_new_cnt <= 1, "冻结的 SG 被算成 %u 个独立样本", e.sg_new_cnt);
    CHECK(r == REL_SENSOR_UNAVAILABLE, "SG 冻结 1000ms 后原因=%s(期望 REL_SENSOR_UNAVAILABLE)",
          rel_reason_name(r));
    CHECK(e.age_bad_ms >= p.dur_adc_ms, "age_bad_ms=%u < %u", e.age_bad_ms, p.dur_adc_ms);
}

/* REL09 ADC 相对偏离: 上偏与下偏都必须能触发 */
static void c_REL09(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r;
    rt_uint32_t hi, lo;
    int k, i;

    world_reset();
    p = make_profile();
    hi = (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_trip + 5u);
    lo = (p.run_adc_min > (rt_uint16_t)(p.run_adc_noise + p.adc_dev_trip + 5u))
         ? (rt_uint32_t)(p.run_adc_min - p.run_adc_noise - p.adc_dev_trip - 5u) : 0u;
    CHECK(hi <= 4095u, "上偏测试值 %u 超出 ADC 码域", hi);
    for (k = 0; k < 2; ++k)
    {
        feed_init(&e, &p, &f);
        r = REL_REASON_NONE;
        for (i = 1; i <= 100; ++i)
        {
            r = feed_step(&f, 500, (k == 0) ? hi : lo, (i % 10) == 0, 1, 1, 1);
            if (i < 31) CHECK(r != REL_ADC_SEVERE, "%s偏方向在 %d 帧(%dms)前就 severe —— "
                              "dur_adc_ms=%u 的持续窗口没生效", k ? "下" : "上", i, i * 10,
                              p.dur_adc_ms);
        }
        CHECK(r == REL_ADC_SEVERE, "%s偏方向最终原因=%s(期望 REL_ADC_SEVERE)",
              k ? "下" : "上", rel_reason_name(r));
        CHECK(e.latch.latched == 0, "引擎自己落了 latch(应由 rel_request_protection 负责)");
        CHECK(e.verdict == REL_ADC_SEVERE, "verdict=%s 与原因不一致", rel_reason_name(e.verdict));
    }
}

/* REL10 同一个 ADC 样本反复出现不得构成"持续" */
static void c_REL10(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r;
    rt_uint32_t hi;
    int i;

    world_reset();
    p = make_profile();
    hi = (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_trip + 5u);
    feed_init(&e, &p, &f);
    feed_step(&f, 500, hi, 1, 1, 1, 1);               /* 第一个新 ADC 样本 */
    for (i = 0; i < 70; ++i)
    {
        r = feed_step(&f, 500, 0, (i % 10) == 0, 0, 1, 1);   /* cur 身份冻结 */
        CHECK(r != REL_ADC_SEVERE, "同一个旧 ADC 样本被判成持续异常(帧 %d)", i);
    }
    CHECK(e.adc_new_cnt <= 1, "旧 ADC 样本被算成 %u 个", e.adc_new_cnt);
}

/* REL11 中等融合: 两个中等异常必须时间重叠且各自凑够独立样本 */
static void c_REL11(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r;
    rt_uint32_t adc_w, sg_w;
    int i;

    world_reset();
    p = make_profile();
    sg_w = (rt_uint32_t)(p.run_sg_min - p.sg_drop_warn - 1);
    adc_w = (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_warn + 1u);
    feed_init(&e, &p, &f);
    for (i = 1; i <= 60; ++i)
    {
        r = feed_step(&f, (rt_uint16_t)sg_w, adc_w, (i % 10) == 0, 1, 1, 1);
        if (i < 40) CHECK(r != REL_FUSED_SEVERE, "%d 帧(%dms)就判融合 severe", i, i * 10);
    }
    CHECK(r == REL_FUSED_SEVERE, "融合最终原因=%s(期望 REL_FUSED_SEVERE)", rel_reason_name(r));
    CHECK(e.sg_new_cnt >= p.min_new_sg && e.adc_new_cnt >= 2,
          "融合成立但证据不足(sg=%u adc=%u)", e.sg_new_cnt, e.adc_new_cnt);
    CHECK(e.severe_active == 1, "severe_active=%d", e.severe_active);

    /* 只有 SG 中等、ADC 全程带内 ⇒ 永不融合 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 100; ++i)
    {
        r = feed_step(&f, (rt_uint16_t)sg_w, 2000, (i % 10) == 0, 1, 1, 1);
        CHECK(r != REL_FUSED_SEVERE, "单源中等被判成融合(帧 %d)", i);
    }
    /* 只有 ADC 中等、SG 全程带内 ⇒ 也不融合 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 100; ++i)
    {
        r = feed_step(&f, 500, adc_w, (i % 10) == 0, 1, 1, 1);
        CHECK(r != REL_FUSED_SEVERE, "另一单源中等被判成融合(帧 %d)", i);
    }
}

/* REL12 原因不得冒称双源确认; 旧引擎判定不被污染 */
static void c_REL12(void)
{
    diag_rprof_t p;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    S.raw = 2000;                                   /* 电流在带内: 单源 SG 也必须动作 */
    pump(120);
    CHECK(g_post_calls == 1, "post 次数=%d(期望恰好 1)", g_post_calls);
    CHECK(rel_prod_get_latch(&l), "latch 未落");
    if (l.latched)
    {
        CHECK(l.reason == REL_SG_SEVERE, "原因=%s(期望 REL_SG_SEVERE)", rel_reason_name(l.reason));
        CHECK(l.threshold_raw == (rt_int32_t)p.sg_drop_trip, "阈值=%d(期望 %u)",
              l.threshold_raw, p.sg_drop_trip);
        CHECK(l.value_raw == (rt_int32_t)S.sg, "触发量=%d 不是 SG 码域的值(期望 %d)",
              l.value_raw, (int)S.sg);
        CHECK(l.profile_id == p.profile_id && l.revision == p.revision, "latch 没绑定 profile 身份");
        CHECK(l.first_sg_seq != 0 && l.first_cur_seq != 0, "latch 没记录样本身份");
        CHECK(l.persisted_ms >= p.dur_sg_ms, "persisted_ms=%u < %u", l.persisted_ms, p.dur_sg_ms);
    }
    CHECK(d_prod.st.verdict != DIAG_STALL_CONFIRMED, "旧引擎被污染成 STALL_CONFIRMED");
    /* 状态行必须自称 REL_SG_SEVERE 而不是 STALL_CONFIRMED */
    cap_reset();
    diag_relative_status_lines();
    CHECK(cap_has("rel LATCH r=REL_SG_SEVERE"), "状态行没写 REL_SG_SEVERE: [%.80s]", cap_text());
    CHECK(cap_has("FIELD_UNVERIFIED"), "学习生成的阈值没披露 FIELD_UNVERIFIED");
}

/* REL13 先保护后打印 */
static void c_REL13(void)
{
    diag_rprof_t p;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(120);
    CHECK(g_post_calls == 1, "post=%d", g_post_calls);
    CHECK(g_post_reason_lines_at_call == 0,
          "post 时捕获里已有 %d 条 reason 行 —— 打印跑到了保护前面", g_post_reason_lines_at_call);
    CHECK(cap_has("[DIAG] REL TRIP r=REL_SG_SEVERE"), "保护后没打印触发摘要");
    CHECK(strncmp(g_cap + g_post_cap_len, "[DIAG] REL TRIP r=REL_SG_SEVERE",
                  strlen("[DIAG] REL TRIP r=REL_SG_SEVERE")) == 0,
          "post 之后的第一段不是摘要: [%.40s]", g_cap + g_post_cap_len);
    CHECK(cap_max_line_len() <= 110, "最长行 %d 字节超过 110B 短行预算", cap_max_line_len());
}

/* REL14 首次原因不可覆盖; 同一次武装只 post 一次; post 失败也留事实 */
static void c_REL14(void)
{
    diag_rprof_t p;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(150);
    CHECK(g_post_calls == 1, "持续严重 150 帧后 post=%d(期望仍为 1)", g_post_calls);
    CHECK(cap_count("[DIAG] REL TRIP r=REL_SG_SEVERE") == 1, "摘要打印了 %d 次(期望 1)",
          cap_count("[DIAG] REL TRIP r=REL_SG_SEVERE"));
    /* 换成 ADC 严重也不得改写首次原因 */
    S.sg = 500;
    S.raw = p.run_adc_max + p.run_adc_noise + p.adc_dev_trip + 5u;
    pump(200);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_SG_SEVERE,
          "首次原因被覆盖成 %s", rel_reason_name(l.reason));
    CHECK(g_post_calls == 1, "第二次严重事件又 post(总 %d)", g_post_calls);

    /* post 返回错误也必须如实留在 latch 里 */
    world_reset();
    g_post_rc_inject = -RT_EIO;
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(120);
    CHECK(rel_prod_get_latch(&l), "post 失败时连 latch 都没有");
    CHECK(l.post_rc == (rt_int16_t)(-RT_EIO), "post_rc=%d 没被如实记录", l.post_rc);
    CHECK(cap_has("post_rc="), "摘要里没有 post_rc");
}

/* REL15 MONITOR_ONLY: 给原因但不行动 */
static void c_REL15(void)
{
    diag_rprof_t p;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_MONITOR_ONLY);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(200);
    CHECK(g_post_calls == 0, "MONITOR_ONLY 下 post=%d(必须 0)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) == RT_FALSE, "MONITOR_ONLY 落了假 latch");
    CHECK(d_rel_prod.verdict == REL_SG_SEVERE,
          "MONITOR_ONLY 连原因都不给(verdict=%s) —— 无法评估", rel_reason_name(d_rel_prod.verdict));
    CHECK(cap_count("[DIAG] REL TRIP r=REL_SG_SEVERE") == 0, "MONITOR_ONLY 也打了行动摘要(%d 次)",
          cap_count("[DIAG] REL TRIP r=REL_SG_SEVERE"));
    cap_reset();
    diag_status();
    CHECK(cap_has("mode=MONITOR_ONLY"), "状态行没写清模式");
}

/* REL16 RELATIVE 开启门禁: 每一项单独拒绝且模式原样不动 */
static void c_REL16(void)
{
    diag_rprof_t p;
    rt_err_t e;

    world_reset();
    p = make_profile();
    install_profile(&p);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;    /* 静止 + READY 的合格基线 */

    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "无 CONFIRM 却接受了 relative(rc=%d)", e);
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "拒绝后模式变成 %d", (int)d_mode);

    clear_profile();
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EPERM && d_mode == DIAG_MODE_MONITOR_ONLY, "无 profile 却开启(rc=%d)", e);

    /* 残缺 profile(资格校验不过)必须被具名拒绝 */
    p = make_profile();
    p.run_sg_n = 2;
    p.signature = rel_profile_signature(&p);
    install_profile(&p);
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EINVAL, "残缺 profile 却开启(rc=%d) —— 资格校验被绕过", e);
    CHECK(cap_has("(SAMPLE_COUNT)"), "拒绝原因没具名: [%.80s]", cap_text());

    /* 工况失配 */
    p = make_profile();
    install_profile(&p);
    S.planned.irun = 31;
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "签名失配却开启(rc=%d)", e);
    S.planned.irun = 20;

    /* 运动中不得开启 */
    p = make_profile();
    install_profile(&p);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500;
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "电机在跑却开启(rc=%d)", e);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;

    /* 安全链未就绪 */
    p = make_profile();
    install_profile(&p);
    S.safety = SAFETY_FAULT_LATCHED;
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "安全未就绪却开启(rc=%d)", e);
    S.safety = SAFETY_READY;

    /* 全部满足(源健康现在也是资格之一: 必须先有真帧) */
    pump_sensor(2);
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == RT_EOK && d_mode == DIAG_MODE_RELATIVE_PROTECTION, "合格条件被拒绝(rc=%d)", e);
    CHECK(cap_has("thresholds=FIELD_UNVERIFIED"), "没披露阈值来源是学习裕量");
    /* 凭据一次性 */
    (void)diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "CONFIRM 凭据被复用成功(rc=%d, 应当一次性)", e);

    /* 取不到配置快照 ⇒ fail-closed, 且绝不能 release 一把没持有的锁 */
    g_mutex_take_fail = 1;
    {
        int rel_before = g_mutex_release_calls;
        diagnosis_take_relative_confirm();
        e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
        CHECK(e != RT_EOK && d_mode == DIAG_MODE_MONITOR_ONLY,
              "取不到 profile 却开启(rc=%d mode=%d)", e, (int)d_mode);
        CHECK(g_mutex_release_calls == rel_before,
              "取锁失败后仍然 release 了(%d 次) —— 未持有就 release 是 UB",
              g_mutex_release_calls - rel_before);
    }
    g_mutex_take_fail = 0;
}

/* REL17 原 ACTIVE 的 MEASURED 门禁保持不变 */
static void c_REL17(void)
{
    rt_err_t e;
    world_reset();
    /* M8-R1 R3.1: 任何人工模式改变现在都先过静止门。本例测的是"既有的 MEASURED
     * 门禁没有因为 RELATIVE 的加入而放宽", 所以先把静止条件摆正, 让标定来源成为
     * 唯一的拒绝理由(静止门由 REL16 与新的 C1 用例单独覆盖)。 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    e = diagnosis_set_mode(DIAG_MODE_ACTIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "THEORETICAL 标定下 ACTIVE 竟然通过(rc=%d)", e);
    CHECK(cap_has("[DIAG] ACTIVE REFUSED: calibration source="), "ACTIVE 拒绝理由文本变了(既有语义)");
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "ACTIVE 拒绝后模式变了");
    e = diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    CHECK(e == RT_EOK && cap_has("[DIAG] mode=MONITOR_ONLY"), "MONITOR_ONLY 模式行文本变了");
    /* RELATIVE 模式绝不能顶掉 ACTIVE 的门禁 */
    e = diagnosis_set_mode(DIAG_MODE_ACTIVE_PROTECTION);
    CHECK(e == -RT_EPERM, "加了 RELATIVE 之后 ACTIVE 门禁失效(rc=%d)", e);
}

/* REL18 斜坡阶段是例外: 抑制判据但不报错 */
static void c_REL18(void)
{
    diag_rprof_t p;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_ACCEL; S.mhz = 100;
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    S.raw = p.run_adc_max + p.run_adc_noise + p.adc_dev_trip + 5u;
    pump(150);
    CHECK(g_post_calls == 0, "加速段被当成异常并 post %d 次(斜坡例外失效)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) == RT_FALSE, "加速段落了 latch");
    /* 斜坡例外仍然成立: 不得因为"加速段不在批准阶段里"就报成离窗/覆盖丢失。
     * 开机头一帧的 no-frame 披露是另一件事(只在状态变化时打一次), 不属于本判据。 */
    CHECK(cap_has("REL_OUT_OF_WINDOW") == 0, "斜坡阶段被报成离窗: [%.80s]", cap_text());
    CHECK(cap_count("coverage=NOT-ACTIVE") <= 1,
          "no-frame 披露刷屏(%d 次, 期望 <=1)", cap_count("coverage=NOT-ACTIVE"));
    S.mstate = MOTOR_DECEL;
    pump(100);
    CHECK(g_post_calls == 0, "减速段 post %d 次", g_post_calls);
    /* 回到巡航后同样的异常必须能触发(例外不是永久屏蔽) */
    S.mstate = MOTOR_CRUISE; S.mhz = 500;
    pump(150);
    CHECK(g_post_calls == 1, "回到巡航后没有触发(%d 次)", g_post_calls);
}

/* REL19 离窗: 运动中的窗格外才严重; 静止时不宣称覆盖也不行动 */
static void c_REL19(void)
{
    diag_rprof_t p;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_CRUISE; S.mhz = 5000u;          /* 窗外速度 */
    pump(10);
    CHECK(d_rel_prod.verdict == REL_REASON_NONE,
          "离窗 100ms 就报 %s(应先给抑制窗口)", rel_reason_name(d_rel_prod.verdict));
    pump(40);
    CHECK(d_rel_prod.verdict == REL_OUT_OF_WINDOW,
          "持续离窗后 verdict=%s(期望 REL_OUT_OF_WINDOW)", rel_reason_name(d_rel_prod.verdict));
    CHECK(g_post_calls == 1, "离窗保护 post=%d(期望 1)", g_post_calls);

    /* 方向不在批准集合内也算离窗 */
    world_reset();
    install_profile(&p);                              /* p.dir_mask = 1(只 dir0) */
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mdir = 1;
    pump(50);
    CHECK(d_rel_prod.verdict == REL_OUT_OF_WINDOW, "未批准方向没被判离窗(%s)",
          rel_reason_name(d_rel_prod.verdict));

    /* 静止时不得行动 */
    world_reset();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    pump(100);
    CHECK(g_post_calls == 0, "静止时因离窗 post %d 次", g_post_calls);
    CHECK(d_rel_prod.verdict == REL_REASON_NONE, "静止时 verdict=%s",
          rel_reason_name(d_rel_prod.verdict));
}

/* REL20 重放同一帧也必须让年龄超时(年龄检查在帧去重之前) */
static void c_REL20(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    rt_uint32_t trip_at = 0u;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    /* 一次真实成功把 sg/cur 身份落定; 之后**帧环继续跑**(seq/timestamp/motion_tick 都
     * 新鲜), 但 SG/ADC 两路的样本身份一个字都不动 —— 真机对应"传感线程活着, 这两路
     * 读不到新样本"。运动快照若一起冻住, 那是 R2-B3 的覆盖问题(REL49 单独测)。 */
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    for (i = 0; i < 120; ++i)
    {
        f.t += 10u;
        f.a.now_tick = f.t;
        f.a.in.seq = ++f.seq;
        f.a.in.timestamp = f.t;
        f.a.in.motion_tick = f.t;              /* 帧是新的: 唯一老的是两路样本 */
        r = rel_engine_step(&e, &f.a);
        if (i < 30) CHECK(r != REL_SENSOR_UNAVAILABLE, "年龄门提前动作(第 %d 帧)", i);
        if (r == REL_SENSOR_UNAVAILABLE && trip_at == 0u) trip_at = f.seq;
    }
    CHECK(r == REL_SENSOR_UNAVAILABLE, "样本冻结 1200ms 后原因=%s(期望 REL_SENSOR_UNAVAILABLE)",
          rel_reason_name(r));
    CHECK(e.age_bad_ms >= p.dur_adc_ms, "age_bad_ms=%u < %u", e.age_bad_ms, p.dur_adc_ms);
    /* 触发发生在"帧号一直在前进"的途中: 去重只挡重复帧, 没把新帧也吞掉;
     * 触发后引擎按首因早退, 帧号因此停在触发那一帧。 */
    CHECK(trip_at >= 20u && e.last_frame_seq == trip_at - 1u,
          "新帧没被认出来(触发帧=%u last=%u)", (unsigned)trip_at, (unsigned)e.last_frame_seq);
}

/* REL21 恢复必须是新证据: ADC 冻结不得被当成"恢复正常" */
static void c_REL21(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r;
    rt_uint32_t sg_w, adc_w;
    int i;

    world_reset();
    p = make_profile();
    sg_w = (rt_uint32_t)(p.run_sg_min - p.sg_drop_warn - 1);
    adc_w = (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_warn + 1u);
    feed_init(&e, &p, &f);
    for (i = 1; i <= 60; ++i) r = feed_step(&f, (rt_uint16_t)sg_w, adc_w, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_FUSED_SEVERE, "前置融合未成立(r=%s), 无法验证恢复", rel_reason_name(r));
    CHECK(e.severe_active == 1, "severe_active=%d", e.severe_active);
    /* 现在: SG 新鲜且正常, ADC 身份冻结(没有新样本) ⇒ 恢复不得完成 */
    for (i = 0; i < 80; ++i)
    {
        r = feed_step(&f, 500, 0, (i % 10) == 0, 0, 1, 1);
        CHECK(e.severe_active == 1, "ADC 停止出新样本后 severe_active 被清(帧 %d)", i);
    }
    CHECK(e.ok_run_ms < p.dur_recover_ms, "ok_run_ms=%u 竟凑满了恢复窗口(无新证据=恢复?)",
          e.ok_run_ms);
    /* 正例: 两个源都新且都回带内 ⇒ 必须能恢复 */
    for (i = 1; i <= 70; ++i) feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(e.severe_active == 0, "两源都新鲜正常 70 帧后 severe_active 仍=%d(恢复卡死)",
          e.severe_active);
}

/* REL23 profile 持久化: v3 往返 + v2→v3 迁移 + 半套拒绝 + 不改可信度 */
static void c_REL23(void)
{
    diag_rprof_t p, back;
    project_config_v2_t v2;
    project_config_t cs;
    int i;

    world_reset();
    p = make_profile();
    install_profile(&p);
    CHECK(project_config_save() == RT_EOK, "保存失败");
    CHECK(g_blob_len == sizeof(project_config_t), "写入长度=%u != sizeof=%u",
          (unsigned)g_blob_len, (unsigned)sizeof(project_config_t));
    clear_profile();
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.magic == 0, "内存清 profile 失败");
    CHECK(project_config_load() == RT_EOK, "v3 回读失败");
    CHECK(project_config_get_rprof(&back) == RT_EOK, "取 profile 失败");
    CHECK(memcmp(&back, &p, sizeof(p)) == 0, "v3 往返后 profile 字节不一致");
    CHECK(project_config_migrated_from_v2() == RT_FALSE, "v3 载入被误标成迁移");

    /* v2 旧 blob(104B): 逐字段迁移 + profile 置无效 + 如实标记 */
    world_reset();
    memset(&v2, 0, sizeof(v2));
    v2.magic = PROJ_CFG_MAGIC; v2.version = PROJ_CFG_VERSION_V2;
    v2.cur_offset_mv = 1661.5f; v2.cur_gain_v_per_a = 0.62f;
    v2.cur_cal_source = (rt_uint8_t)CURRENT_ADC_CAL_MEASURED;
    v2.band_hz[0] = 250; v2.band_hz[1] = 900;
    for (i = 0; i < 3; ++i) { v2.sg_warn[i] = 40 + i; v2.sg_stall[i] = 20 + i; }
    for (i = 0; i < 3; ++i) { v2.cur_warn_ma[i] = 400.f + i; v2.cur_stall_ma[i] = 900.f + i; }
    v2.vib_impact_mg = 3000; v2.persist_warn = 55; v2.persist_stall = 111;
    v2.impact_frames = 3; v2.hysteresis_frames = 123;
    v2.pre_fault_ms = 1500; v2.post_fault_ms = 900;
    memcpy(g_blob, &v2, sizeof(v2));
    g_blob_len = sizeof(v2);
    g_blob_valid = 1;
    CHECK(sizeof(project_config_v2_t) == 104u, "v2 尺寸=%u(期望 104)",
          (unsigned)sizeof(project_config_v2_t));
    CHECK(project_config_load() == RT_EOK, "v2 迁移载入失败");
    CHECK(project_config_migrated_from_v2() == RT_TRUE, "迁移标志未置位");
    CHECK(project_config_get_snapshot(&cs) == RT_EOK, "快照失败");
    CHECK(cs.cur_offset_mv == 1661.5f && cs.cur_gain_v_per_a == 0.62f &&
          cs.cur_cal_source == CURRENT_ADC_CAL_MEASURED, "迁移丢了标定");
    CHECK(cs.band_hz[0] == 250 && cs.band_hz[1] == 900 && cs.persist_warn == 55 &&
          cs.hysteresis_frames == 123 && cs.pre_fault_ms == 1500 && cs.post_fault_ms == 900,
          "迁移丢了阈值/窗口字段");
    CHECK(cs.rprof.magic == 0, "迁移后凭空有了 profile");
    CHECK(cs.version == PROJ_CFG_VERSION, "迁移后版本没升级(0x%08X)", cs.version);
    CHECK(g_cal_ex_last == CURRENT_ADC_CAL_MEASURED, "迁移后 MEASURED 标定被降级成 %d",
          (int)g_cal_ex_last);

    /* 半套 profile 必须整套拒绝, 现有 profile 一字不动 */
    world_reset();
    p = make_profile();
    install_profile(&p);
    p.run_sg_n = 0;
    p.signature = rel_profile_signature(&p);
    CHECK(project_config_set_rprof(&p) == -RT_EINVAL, "半套 profile 被接受(应当拒绝)");
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.run_sg_n == 12,
          "被拒的写入仍改掉了现有 profile");
    /* 写 profile 不得把整套配置说成"可信" */
    world_reset();
    cfg_take(); cfg_valid = RT_FALSE; cfg_health = SUBSYS_DEGRADED; cfg_release();
    p = make_profile();
    CHECK(project_config_set_rprof(&p) == RT_EOK, "合法 profile 发布失败");
    CHECK(project_config_is_valid() == RT_FALSE, "写 profile 把 DEGRADED 刷成了 valid");
    CHECK(project_config_get_health() == SUBSYS_DEGRADED, "健康度被改成了 %d",
          (int)project_config_get_health());
    /* 取不到锁: 读和写都必须报错, 且绝不能 release 未持有的锁 */
    g_mutex_take_fail = 1;
    CHECK(project_config_get_rprof(&back) == -RT_EIO, "取锁失败时 get_rprof 没报错");
    CHECK(project_config_set_rprof(&p) != RT_EOK, "取锁失败时 set_rprof 没报错");
    CHECK(project_config_clear_rprof() != RT_EOK, "取锁失败时 clear 却成功了");
    g_mutex_take_fail = 0;
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.run_sg_n == 12,
          "取锁失败的那次写入生效了(n=%u)", back.run_sg_n);
    /* 存储失败必须如实传播 */
    g_ns_save_rc = -RT_EIO;
    CHECK(project_config_save() == -RT_EIO, "Flash 写失败被吞掉");
    g_ns_save_rc = RT_EOK;
    /* v2/v3 尺寸关系(迁移判据依赖"新结构更大") */
    CHECK(sizeof(project_config_t) > sizeof(project_config_v2_t), "新结构不比 v2 大, 迁移判据不成立");
}

/* REL24 命令行严格解析: 非法参数零副作用 */
static void c_REL24(void)
{
    static char *argv_zero_bad[]  = { "diag_profile", "zero", "abc" };
    static char *argv_zero_lo[]   = { "diag_profile", "zero", "31" };
    static char *argv_zero_hi[]   = { "diag_profile", "zero", "257" };
    static char *argv_zero_neg[]  = { "diag_profile", "zero", "-1" };
    static char *argv_zero_sfx[]  = { "diag_profile", "zero", "32x" };
    static char *argv_zero_sp[]   = { "diag_profile", "zero", " 32" };
    static char *argv_learn_few[] = { "diag_profile", "learn", "8", "500", "600" };
    static char *argv_learn_n[]   = { "diag_profile", "learn", "7", "500", "600", "0" };
    static char *argv_learn_inv[] = { "diag_profile", "learn", "8", "600", "500", "0" };
    static char *argv_learn_dir[] = { "diag_profile", "learn", "8", "500", "600", "3" };
    static char *argv_clear[]     = { "diag_profile", "clear" };
    static char *argv_clear_no[]  = { "diag_profile", "clear", "yes" };
    static char *argv_commit[]    = { "diag_profile", "commit" };
    static char *argv_extra[]     = { "diag_profile", "show", "extra" };
    diag_rprof_t back;

    world_reset();
    diag_profile(3, argv_zero_bad);
    diag_profile(3, argv_zero_lo);
    diag_profile(3, argv_zero_hi);
    diag_profile(3, argv_zero_neg);
    diag_profile(3, argv_zero_sfx);
    diag_profile(3, argv_zero_sp);
    diag_profile(5, argv_learn_few);
    diag_profile(6, argv_learn_n);
    diag_profile(6, argv_learn_inv);
    diag_profile(6, argv_learn_dir);
    diag_profile(2, argv_clear);
    diag_profile(3, argv_clear_no);
    diag_profile(2, argv_commit);
    diag_profile(3, argv_extra);

    CHECK(d_cap.active == 0, "非法参数却启动了采集(active=%d)", d_cap.active);
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.magic == 0, "非法参数改掉了已提交 profile");
    CHECK(d_pending_zero == RT_FALSE && d_pending_learn == RT_FALSE, "非法参数置了 pending 标志");
    CHECK(g_static_begin_calls == 0, "非法参数借用了静止事务门(%d 次)", g_static_begin_calls);
    CHECK(cap_has("commit REFUSED: pending incomplete"), "无 pending 的 commit 未具名拒绝");
    CHECK(cap_has("clear needs"), "clear 缺 CONFIRM 未被拒绝");
    CHECK(cap_has("yes") == 0 || cap_has("clear needs"), "clear 的非 CONFIRM 口令被当成确认");
    /* 静止门禁: 运动中 zero/learn/commit/clear 一律拒绝 */
    world_reset();
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500;
    diag_profile(3, argv_zero_lo);          /* 参数合法, 状态非法 */
    diag_profile(6, argv_learn_inv);
    diag_profile(2, argv_commit);
    cap_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    diag_profile(2, argv_commit);
    CHECK(cap_has("commit REFUSED"), "静止条件恢复后 commit 却接受了(应当先拒绝 pending)");
}

/* ---- diag_profile 命令桥(在 ROLE_MAIN 帧泵模式下执行真实命令循环) ---- */

static void cmd_zero(void *p)   { char *av[] = { "diag_profile", "zero", "32" }; (void)p; g_cmd_pump = 1; diag_profile(3, av); g_cmd_pump = 0; }
static void cmd_learn(void *p)  { char *av[] = { "diag_profile", "learn", "8", "450", "550", "0" }; (void)p; g_cmd_pump = 1; diag_profile(6, av); g_cmd_pump = 0; }
static void cmd_commit(void *p) { char *av[] = { "diag_profile", "commit" }; (void)p; g_cmd_pump = 1; diag_profile(2, av); g_cmd_pump = 0; }
static void cmd_clear(void *p)  { char *av[] = { "diag_profile", "clear", "CONFIRM" }; (void)p; g_cmd_pump = 1; diag_profile(3, av); g_cmd_pump = 0; }

/* 命令必须作为 ROLE_MAIN"线程帧"运行: 它的等待循环靠 rt_thread_mdelay 推进,
 * 而只有在该 role 下调度器才会一边推进虚拟时钟一边泵 sensor 帧。 */
static void do_cmd(void (*fn)(void *)) { run_thread(ROLE_MAIN, fn, RT_NULL, 0); }

static int g_break_at_tick;
static void hook_break_window(int tick)
{
    if (g_break_at_tick && tick == g_break_at_tick)
    { S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; }   /* 离开被批准的巡航工况 */
}
static void hook_flip_dir(int tick)
{
    if (g_break_at_tick && tick == g_break_at_tick)
        S.mdir = 1u;                                     /* 学到一半换了方向 */
}

/* ======================= M8-R7D1: 异步任务的夹具侧推进 =======================
 *
 * 产品命令线程不再等样本, 于是"等学习任务跑完"这件事挪到夹具里: job_wait 每次驱动
 * 一帧**真实**的 diag_thread_entry(也就是真实 rel_job_advance), 直到任务进终态或
 * 预算用尽。返回负数 = 预算内没终结 / 快照读不到, 用例必须按失败处理, 不能当成
 * "还没轮到"而放过。
 * 这套协作式推进证明的是状态机与交错顺序, 不是板端抢占并发, 也不是真实时延。 */
static int job_wait(int max_ticks)
{
    rel_job_view_t v;
    int i;

    for (i = 0; i < max_ticks; ++i)
    {
        pump(1);
        memset(&v, 0, sizeof(v));
        if (rel_job_status(&v) != RT_EOK) return -3;
        if (v.state != (rt_uint8_t)REL_JOB_RUNNING &&
            v.state != (rt_uint8_t)REL_JOB_CANCEL_PENDING) return (int)v.state;
    }
    return -1;
}

static rel_job_view_t job_snap(void)
{
    rel_job_view_t v;

    memset(&v, 0, sizeof(v));
    (void)rel_job_status(&v);
    return v;
}

/* 有没有"活动中的任务": 终态历史会一直保留 job_id, 所以不能拿 job_id==0 当"没有任务"。 */
static int job_active(void)
{
    rel_job_view_t v = job_snap();

    return (v.state == (rt_uint8_t)REL_JOB_RUNNING ||
            v.state == (rt_uint8_t)REL_JOB_CANCEL_PENDING) ? 1 : 0;
}

/* 受理 + 推进到终态, 返回终态。用例仍各自断言凭据、样本数与已发布 profile。 */
static int learn_run(int max_ticks)
{
    do_cmd(cmd_learn);
    return job_wait(max_ticks);
}

static void cmd_learn_status(void *p)
{
    char *av[] = { "diag_profile", "learn_status" };
    (void)p;
    diag_profile(2, av);
}

static rt_uint32_t g_cancel_id = 1u;
static void cmd_learn_cancel(void *p)
{
    char idbuf[12];
    char *av[3];

    (void)p;
    snprintf(idbuf, sizeof(idbuf), "%u", (unsigned)g_cancel_id);
    av[0] = "diag_profile"; av[1] = "learn_cancel"; av[2] = idbuf;
    diag_profile(3, av);
}

static void cmd_learn_window_break(void *p)
{
    char *av[] = { "diag_profile", "learn", "8", "450", "550", "0" };
    (void)p;
    diag_profile(6, av);
}

/* REL25 zero→learn→commit→clear 全流程(真实采集链 + 真实命令循环) */
static void c_REL25(void)
{
    diag_rprof_t back, saved, p;
    project_config_t before, after;
    int got_before;

    /* 1) 运动中: 拒绝且不启动采集 */
    world_reset();
    S.mstate = MOTOR_CRUISE;
    do_cmd(cmd_zero);
    CHECK(d_cap.active == 0 && d_pending_zero == RT_FALSE, "运动中竟然开始了零点采集");
    CHECK(cap_has("zero REFUSED: not static+READY"), "静止门禁没披露");
    CHECK(g_mdelay_calls == 0, "被拒绝的命令里竟然发生了等待(%ld 次 mdelay)", g_mdelay_calls);

    /* 2) 静止 + READY: 由真实 sensor 链采 32 个**不同**有效 ADC 帧 */
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    S.raw = 1660;
    do_cmd(cmd_zero);
    CHECK(d_pending_zero == RT_TRUE, "零点采集没产出 pending: [%.80s]", cap_text());
    CHECK(d_cap.active == 0, "采集结束后状态机还挂着");
    CHECK(d_pending.zero_n == 32, "zero_n=%u(期望 32)", d_pending.zero_n);
    CHECK(d_pending.zero_min == 1660 && d_pending.zero_max == 1660, "零点带 [%u..%u]",
          d_pending.zero_min, d_pending.zero_max);
    CHECK(d_pending.zero_mean_x1000 == 1660000u, "零点均值=%u", d_pending.zero_mean_x1000);
    CHECK(d_pending.signature != 0 && d_pending.signature == rel_live_signature(),
          "零点阶段没算出可比较的签名(sig=%u live=%u)", d_pending.signature,
          rel_live_signature());
    /* 重复帧不得凑数: ADC 失败时采集必须停在自己的进度上 */
    CHECK(g_mdelay_calls >= 32 && g_mdelay_calls < 200, "等待节拍数=%ld", g_mdelay_calls);

    /* 3) 巡航学习: 8 个独立 SG + 8 个 ADC */
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500;
    S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    CHECK(learn_run(400) == REL_JOB_DONE, "后台学习没在预算内 DONE: st=%d",
          (int)job_snap().state);
    CHECK(d_pending_learn == RT_TRUE, "学习没产出 pending: [%.90s]", cap_text());
    CHECK(d_pending.run_adc_n >= 8 && d_pending.run_sg_n >= 8, "样本数 adc=%u sg=%u",
          d_pending.run_adc_n, d_pending.run_sg_n);
    CHECK(d_pending.learn_sg_seq_last >= d_pending.learn_sg_seq_first, "出处区间反了");
    CHECK(d_pending.run_adc_noise == 0, "同一 raw 反复采样, noise 应为 0(=%u)",
          d_pending.run_adc_noise);
    CHECK((d_pending.flags & RPF_COMMITTED) == 0,
          "未 commit 的 pending 却自称 COMMITTED(flags=%04X)", d_pending.flags);
    /* FROM_LEARN 是 commit 时随整套发布一起置位的(pending 阶段还没有可发布的阈值) */
    CHECK((d_pending.flags & RPF_FIELD_VERIFIED) == 0, "pending 自称 FIELD_VERIFIED");
    CHECK((d_pending.flags & RPF_FIELD_VERIFIED) == 0, "学习产物自称 FIELD_VERIFIED");

    /* 4) commit(静止 + 配置未漂移) ⇒ 发布 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit OK") || cap_has("commit REFUSED"), "commit 没有任何结论行: [%.80s]",
          cap_text());
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.magic == DIAG_RPROF_MAGIC,
          "commit 没有发布 profile: [%.90s]", cap_text());
    CHECK((back.flags & RPF_FIELD_VERIFIED) == 0, "发布后竟然自称 FIELD_VERIFIED");
    CHECK((back.flags & RPF_COMMITTED) != 0, "发布后没有 COMMITTED 位");
    CHECK((back.flags & RPF_FROM_LEARN) != 0, "阈值来自正常基线裕量生成却没标 FROM_LEARN");
    CHECK(back.dur_sg_ms == 500 && back.dur_adc_ms == 300 && back.dur_fused_ms == 300 &&
          back.dur_recover_ms == 500 && back.age_sg_ms == 350 && back.age_frame_ms == 100 &&
          back.min_new_sg == 3, "设计初值没落到 profile 里(%u/%u/%u)",
          back.dur_sg_ms, back.dur_adc_ms, back.min_new_sg);
    CHECK(back.signature == rel_live_signature(), "发布后签名与 live 不一致");
    CHECK(g_static_begin_calls == g_static_end_calls && g_static_begin_calls >= 1,
          "静止事务门不配对(begin=%d end=%d)", g_static_begin_calls, g_static_end_calls);
    CHECK(back.adc_dev_warn > back.run_adc_noise && back.adc_dev_trip > back.adc_dev_warn,
          "ADC 阈值没有裕量层次(warn=%d trip=%d)", back.adc_dev_warn, back.adc_dev_trip);
    CHECK(back.sg_drop_warn < back.sg_drop_trip && back.sg_drop_trip <= back.run_sg_min,
          "SG 阈值层次不对(%u/%u, run_sg_min=%u)", back.sg_drop_warn, back.sg_drop_trip,
          back.run_sg_min);
    CHECK(rel_profile_validate(&back, RT_NULL) == RT_EOK, "发布出去的 profile 自己不合格");

    /* 5) 学习中途离窗 ⇒ 具名失败, 已发布的 profile 不动 */
    world_reset();
    p = make_profile();
    install_profile(&p);
    project_config_get_rprof(&saved);
    CHECK(saved.magic == DIAG_RPROF_MAGIC, "前置: 已发布 profile 没装上");
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500;
    cap_reset();
    pump_sensor(2);
    /* R7D1: 帧流由 Diagnosis Thread 的推进带进来, 所以"学习中途离窗"改在 diag 节拍里注入。 */
    g_break_at_tick = 20;
    g_tick_hook = hook_break_window;
    do_cmd(cmd_learn_window_break);
    CHECK(job_wait(400) == REL_JOB_FAILED, "离窗学习没在预算内终结: st=%d",
          (int)job_snap().state);
    g_tick_hook = RT_NULL;
    CHECK(strcmp(job_snap().why, "WINDOW_BROKEN") == 0, "离窗终结原因不是 WINDOW_BROKEN: [%s]",
          job_snap().why);
    CHECK(d_pending_learn == RT_FALSE, "失败的学习却置了 pending");
    CHECK(d_cap.active == 0 && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "失败后采集状态机/所有权没回收(owner=%u)", (unsigned)d_cap_owner);
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.magic == saved.magic &&
          back.profile_id == saved.profile_id, "失败的学习动了已发布 profile");

    /* 6) learn 之后驱动配置漂移 ⇒ commit 必须拒绝 */
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000;
    pump_sensor(2);
    do_cmd(cmd_learn);
    CHECK(job_wait(400) == REL_JOB_DONE, "前置: 漂移前的学习没 DONE st=%d",
          (int)job_snap().state);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    S.planned.irun = 27;
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED"), "配置漂移后 commit 却成功了");
    S.planned.irun = 20;

    /* 7) clear CONFIRM: 作废 profile, 回 MONITOR_ONLY, 其它字段一字不动 */
    world_reset();
    p = make_profile();
    install_profile(&p);
    project_config_get_snapshot(&before);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_clear);
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.magic == 0, "clear 没作废 profile");
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "clear 后模式=%d(期望 MONITOR_ONLY)", (int)d_mode);
    CHECK(d_pending_zero == RT_FALSE && d_pending_learn == RT_FALSE, "clear 后 pending 还在");
    CHECK(project_config_get_snapshot(&after) == RT_EOK, "clear 后取快照失败");
    got_before = memcmp(&before.rprof, &after.rprof, sizeof(after.rprof)) != 0;
    CHECK(got_before, "rprof 没被清掉");
    CHECK(memcmp(&before, &after, offsetof(project_config_t, rprof)) == 0,
          "clear 改动了 profile 以外的字段");
    CHECK(cap_has("profile voided"), "clear 的结论行缺失");
}

/* REL26 阈值生成规则与量程一致性(纯函数级) */
static void c_REL26(void)
{
    diag_rprof_t p;
    world_reset();
    p = make_profile();
    /* ADC 码域上界: trip 必须可从带内到达(否则阈值是假的) */
    CHECK((rt_int32_t)p.adc_dev_trip <= 4095 - (rt_int32_t)p.run_adc_max ||
          (rt_int32_t)p.adc_dev_trip <= (rt_int32_t)p.run_adc_min,
          "trip=%u 两侧都到不了(上界 %d / 下界 %d)", p.adc_dev_trip,
          4095 - (int)p.run_adc_max, (int)p.run_adc_min);
    /* tick 回绕差: 未来样本一律当"不可信" */
    CHECK(diag_rel_tick_diff(1000u, 2000u) == 0x80000000u, "未来 tick 没有被标成不可信");
    CHECK(diag_rel_tick_diff(0u, 0xFFFFFFF0u) == 16u, "回绕差算错(%u)",
          diag_rel_tick_diff(0u, 0xFFFFFFF0u));
    CHECK(diag_rel_tick_diff(1500u, 1000u) == 500u, "普通差算错");
    /* 严重性归类: 只有这五种原因要求行动 */
    CHECK(rel_reason_is_severe(REL_SG_SEVERE) && rel_reason_is_severe(REL_ADC_SEVERE) &&
          rel_reason_is_severe(REL_FUSED_SEVERE) && rel_reason_is_severe(REL_SENSOR_UNAVAILABLE) &&
          rel_reason_is_severe(REL_OUT_OF_WINDOW), "严重原因集合不完整");
    CHECK(rel_reason_is_severe(REL_REASON_NONE) == RT_FALSE, "NONE 被当成需要行动");
    /* 名字必须是短行可打印的枚举名, 不能是 %d */
    CHECK(strcmp(rel_reason_name(REL_SG_SEVERE), "REL_SG_SEVERE") == 0, "原因名不稳定");
}

/* REL27 旧 ACTIVE 链的严重边沿同样是"先保护后打印"(M8-PRE-3 的修后对照),
 * 且旧链的多源一致判据本身不得被 M8 改动。 */
static void c_REL27(void)
{
    diag_engine_t t;
    diag_input_t in;
    diag_verdict_t v = DIAG_NORMAL;
    rt_uint32_t seq = 0;
    int i;

    world_reset();
    memset(&t, 0, sizeof(t));
    t.may_report = RT_TRUE;
    diag_engine_reset(&t);
    arm((rt_uint8_t)DIAG_MODE_ACTIVE_PROTECTION);      /* 只是状态; 开启门禁由 REL17 单独测 */
    for (i = 0; i < 200; ++i)
    {
        memset(&in, 0, sizeof(in));
        in.seq = ++seq;
        in.sg_valid = 1; in.sg = 30;                    /* SG 崩(band1 stall=50) */
        in.cur_valid = 1; in.current_ma = 600.0f;       /* 且电流升过 warn=500 */
        in.imu_valid = 1; in.vib_mg = 1000;
        in.motor_state = (rt_uint8_t)MOTOR_CRUISE; in.step_hz = 500;
        v = diag_engine_step(&t, &in);
    }
    CHECK(v == DIAG_STALL_CONFIRMED, "旧链多源一致判据被改坏(verdict=%s)", verdict_name(v));
    CHECK(g_post_calls == 1, "post=%d(期望恰好 1)", g_post_calls);
    CHECK(g_post_active_lines_at_call == 0,
          "post 时捕获里已有 %d 条 severe 行 —— 打印又跑到了保护前面",
          g_post_active_lines_at_call);
    CHECK(cap_has("[DIAG] ACTIVE: severe STALL_CONFIRMED -> post EVT_MULTI_FAULT"),
          "severe 摘要文本变了(现场证据按它检索)");
    CHECK(strncmp(g_cap + g_post_cap_len, "[DIAG] ACTIVE: severe", 21) == 0,
          "保护之后的第一段不是 severe 摘要: [%.40s]", g_cap + g_post_cap_len);
    CHECK(cap_max_line_len() <= 126, "severe 摘要 %d 字节会撞控制台裁剪", cap_max_line_len());
    /* 单 sample 不判 severe 的既有纪律也必须还在 */
    memset(&t, 0, sizeof(t));
    t.may_report = RT_TRUE;
    diag_engine_reset(&t);
    g_post_calls = 0;
    memset(&in, 0, sizeof(in));
    in.seq = ++seq; in.sg_valid = 1; in.sg = 10; in.cur_valid = 1; in.current_ma = 2000.0f;
    in.imu_valid = 1; in.motor_state = (rt_uint8_t)MOTOR_CRUISE; in.step_hz = 500;
    v = diag_engine_step(&t, &in);
    CHECK(v != DIAG_STALL_CONFIRMED && v != DIAG_OVERLOAD, "单帧就把严重判据放出去了");
    CHECK(g_post_calls == 0, "单帧严重判据竟然 post 了 %d 次", g_post_calls);
}

/* ======================= M8-R1 追加用例 =======================
 * 每例对应任务书 R1..R6 的一条被复现缺陷(A1..E1)或其健康对照。
 * 纪律: 断言走**真实产品入口**(rel_engine_step / rel_prod_evaluate /
 * diag_thread_entry / diag_profile / project_config_*), 不复制判定算法自证。
 */

/* ---- 注入钩子(只改"环境事实", 不碰产品判据) ---- */
static void r1_hook_sig_break(int tick)        /* 运行中驱动身份漂移一次 */
{
    diag_rprof_t q;
    if (tick != 10) return;
    project_config_get_rprof(&q);
    q.signature ^= 0x5Au;
    cfg_take(); cfg.rprof = q; cfg_release();
}
static void r1_hook_lock_sticky(int tick)      /* profile 快照从此取不到锁 */
{
    if (tick == 10) g_mutex_take_fail = 1;
}
static void r1_hook_lock_blink(int tick)       /* 瞬态: 11 帧(110ms)后恢复 */
{
    if (tick == 10) g_mutex_take_fail = 1;
    if (tick == 21) g_mutex_take_fail = 0;
}
static void r1_hook_arm_midway(int n)          /* zero 中途被 arm/起转 */
{
    if (n == 5) { S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 3500; }
}
static int r1_sg_646872(int n) { return 64 + 4 * ((n - 1) % 3); }
static int r1_sg_wide(int n)
{
    static const int v[4] = { 60, 150, 300, 480 };
    return v[(n - 1) % 4];
}
static void cmd_learn_far_window(void *p)
{
    char *av[] = { "diag_profile", "learn", "8", "900", "1100", "0" };
    (void)p;
    g_cmd_pump = 1;
    diag_profile(6, av);
    g_cmd_pump = 0;
}

/* REL28 严重判据只能用自己的证据(A1/A2), 且持续强异常仍然必须触发 */
static void c_REL28(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    for (i = 1; i <= 60; ++i)                            /* 600ms 中等警告 */
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_warn - 1), 2000,
                      (i % 10) == 0, 1, 1, 1);
    CHECK(e.sg_run_ms >= p.dur_sg_ms && e.sg_new_cnt >= p.min_new_sg,
          "反例前提不成立: 警告级自己没攒够(%ums/%u)", e.sg_run_ms, e.sg_new_cnt);
    r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000, 1, 1, 1, 1);
    CHECK(r != REL_SG_SEVERE, "第一笔更强样本就判严重(借了警告的 %ums)", e.sg_run_ms);
    CHECK(e.sg_strong_run_ms == 0u && e.sg_strong_cnt == 1u,
          "严重级自己=run %u cnt %u(期望 0/1)", e.sg_strong_run_ms, e.sg_strong_cnt);
    for (i = 1; i <= 60; ++i)                            /* 正控: 强条件自己攒 */
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_SG_SEVERE, "持续强 SG 异常 600ms 后=%s(期望 REL_SG_SEVERE)",
          rel_reason_name(r));
    CHECK(e.sg_strong_run_ms >= p.dur_sg_ms && e.sg_strong_cnt >= p.min_new_sg,
          "严重成立但自己的证据不足(run=%u cnt=%u)", e.sg_strong_run_ms, e.sg_strong_cnt);

    world_reset();                                       /* A2: ADC 同类 */
    p = make_profile();
    feed_init(&e, &p, &f);
    for (i = 1; i <= 40; ++i)
        r = feed_step(&f, 500, (rt_uint32_t)(p.run_adc_max + p.run_adc_noise
                                             + p.adc_dev_warn + 1),
                      (i % 10) == 0, 1, 1, 1);
    CHECK(e.adc_run_ms >= p.dur_adc_ms, "ADC 警告没攒够(%ums)", e.adc_run_ms);
    r = feed_step(&f, 500, (rt_uint32_t)(p.run_adc_max + p.run_adc_noise
                                         + p.adc_dev_trip + 1), 0, 1, 1, 1);
    CHECK(r != REL_ADC_SEVERE, "ADC 第一笔更强样本就判严重(借了警告的 %ums)", e.adc_run_ms);
    CHECK(e.adc_strong_run_ms == 0u && e.adc_strong_cnt == 1u,
          "ADC 严重级自己=run %u cnt %u", e.adc_strong_run_ms, e.adc_strong_cnt);
    for (i = 1; i <= 40; ++i)
        r = feed_step(&f, 500, (rt_uint32_t)(p.run_adc_max + p.run_adc_noise
                                             + p.adc_dev_trip + 1),
                      (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_ADC_SEVERE, "ADC 持续强偏离后=%s(期望 REL_ADC_SEVERE)", rel_reason_name(r));
    CHECK(e.adc_strong_run_ms >= p.dur_adc_ms && e.adc_strong_cnt >= 2u,
          "ADC 严重自己的证据(run=%ums cnt=%u)", e.adc_strong_run_ms, e.adc_strong_cnt);
}

/* REL29 ADC 身份冻结/无效/未来 tick 都必须给具名失效结果(B1) */
static void c_REL29(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    for (i = 1; i <= 120; ++i)
        r = feed_step(&f, 500, 2000, (i % 10) == 0, 0, 1, 1);
    CHECK(r == REL_SENSOR_UNAVAILABLE, "ADC 冻结 1200ms 后=%s(期望 REL_SENSOR_UNAVAILABLE)",
          rel_reason_name(r));
    CHECK((f.a.now_tick - f.a.in.cur_tick) > p.age_frame_ms,
          "反例前提不成立: cur_age=%u <= %u", f.a.now_tick - f.a.in.cur_tick,
          p.age_frame_ms);
    CHECK(e.age_bad_ms >= p.dur_adc_ms, "age_bad_ms=%u < %u", e.age_bad_ms, p.dur_adc_ms);
    /* 外壳帧年龄明明是 10ms, 单查 frame 就是查不出来 —— 这正是 B1 的机理 */
    CHECK((f.a.now_tick - f.a.in.timestamp) <= p.age_frame_ms,
          "帧龄本身已超上限(%u), 这一例没能把 ADC 与帧分开",
          f.a.now_tick - f.a.in.timestamp);

    world_reset();
    feed_init(&e, &p, &f);
    for (i = 1; i <= 120; ++i)
        r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 0);   /* ADC 一直读失败 */
    CHECK(r == REL_SENSOR_UNAVAILABLE, "ADC 持续无效时=%s", rel_reason_name(r));

    world_reset();                                           /* 未来 tick */
    feed_init(&e, &p, &f);
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    f.a.in.cur_tick = f.t + 4000u;
    for (i = 1; i <= 120; ++i)
        r = feed_step(&f, 500, 2000, (i % 10) == 0, 0, 1, 1);
    CHECK(r == REL_SENSOR_UNAVAILABLE, "未来 ADC tick 被当成新鲜样本(%s)", rel_reason_name(r));
}

/* REL30 源间断的两端不得凑成一段连续异常(R1.2) */
static void c_REL30(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    for (i = 1; i <= 40; ++i)                                /* 4 枚强样本, 跨度 300ms */
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 10) == 0, 1, 1, 1);
    CHECK(e.sg_strong_cnt == 4u, "前置: 强样本数=%u(期望 4)", e.sg_strong_cnt);
    CHECK(e.sg_strong_run_ms == 300u, "前置: 强跨度=%u(期望 300)", e.sg_strong_run_ms);
    CHECK(r != REL_SG_SEVERE, "300ms 就判严重了");
    for (i = 1; i <= 36; ++i)                                /* 360ms 没有任何 SG 样本 */
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000, 0, 1, 1, 1);
    CHECK(r != REL_SG_SEVERE, "间断中提前触发=%s", rel_reason_name(r));
    r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000, 1, 1, 1, 1);
    CHECK(e.sg_strong_cnt == 1u, "间断后样本数=%u(期望重开为 1)", e.sg_strong_cnt);
    CHECK(e.sg_strong_run_ms == 0u, "间断后跨度=%u(期望 0, 不得凑成 700ms)",
          e.sg_strong_run_ms);
    CHECK(r != REL_SG_SEVERE, "凑出来的严重=%s", rel_reason_name(r));
    /* 间断两端各自继续攒, 到真正连续 500ms 时才触发 */
    for (i = 1; i <= 60; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_SG_SEVERE, "间断之后重新积累到 600ms 仍不触发(%s)", rel_reason_name(r));
}

/* REL31 完整引擎序列: tick 回绕 + 采样率与年龄上限的绑定(R1.4) */
static void c_REL31(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    f.t = 0xFFFFFFF0u;                        /* 下一步就跨过 0 */
    for (i = 1; i <= 8; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 4) == 0, 1, 1, 1);
    CHECK(e.sg_new_cnt == 2u, "跨回绕后 SG 警告级样本数=%u(期望 2)", e.sg_new_cnt);
    /* ADC 全程在带内, 警告级计数为 0 才是对的; 这里要证明的是"帧被继续消费、
     * 身份继续推进", 所以看 last_cur_seq 而不是警告计数。 */
    CHECK(e.last_cur_seq == 8u, "跨回绕后 ADC 身份=%u(期望 8, 帧没被继续消费)",
          e.last_cur_seq);
    CHECK(e.adc_new_cnt == 0u, "ADC 在带内却攒了警告级样本(%u)", e.adc_new_cnt);
    CHECK(r != REL_SENSOR_UNAVAILABLE, "回绕把新鲜样本算成了过期(%s)", rel_reason_name(r));
    CHECK(r != REL_SG_SEVERE, "两枚样本就判严重");

    world_reset();                            /* 200ms 采样率: 在年龄上限内 */
    feed_init(&e, &p, &f);
    /* 200ms 一枚样本: 第 1 枚在 200ms, 要凑满 dur_sg_ms=500ms 需要第 4 枚(=800ms),
     * 也就是第 4 帧样本。判据按**真实时间**走, 与帧数无关。 */
    for (i = 1; i <= 60; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 20) == 0, 1, 1, 1);
    CHECK(e.sg_strong_cnt == 3u && e.sg_strong_run_ms == 400u,
          "200ms 采样率下 60 帧的证据=cnt %u run %u(期望 3/400)",
          e.sg_strong_cnt, e.sg_strong_run_ms);
    CHECK(r != REL_SG_SEVERE, "只过了 400ms 就判严重");
    for (; i <= 100; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 20) == 0, 1, 1, 1);
    CHECK(r == REL_SG_SEVERE, "200ms 采样率到 800ms 后=%s(期望触发)", rel_reason_name(r));

    world_reset();                            /* 500ms 采样率: 超过年龄上限 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 120; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                      (i % 50) == 0, 1, 1, 1);
    CHECK(e.sg_strong_cnt <= 1u,
          "500ms 间隔(> age_sg_ms=%u)却被当成连续证据(cnt=%u)", p.age_sg_ms,
          e.sg_strong_cnt);
    CHECK(r == REL_SENSOR_UNAVAILABLE, "采样慢到超出绑定定义时=%s(期望具名降险)",
          rel_reason_name(r));

    world_reset();                            /* 融合起点跨回绕 */
    feed_init(&e, &p, &f);
    f.t = 0xFFFFFF00u;
    feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_warn - 1), 2000, 1, 0, 1, 1);
    CHECK(e.sg_span_first != 0u, "SG 警告段没起点");
    for (i = 1; i <= 20; ++i)                 /* 跨过 0 之后 ADC 才开始警告 */
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_warn - 1),
                      (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_warn + 1),
                      0, 1, 1, 1);
    CHECK(e.adc_span_first != 0u, "ADC 警告段没起点");
    CHECK(e.fused_run_ms < 300u,
          "融合时长=%u: 裸 lo>hi 的回绕比较会把起点算到半个周期之前", e.fused_run_ms);
    CHECK(r != REL_FUSED_SEVERE, "回绕处凑出假融合严重(%s)", rel_reason_name(r));
}

/* REL32 恢复段: 任一新鲜反向证据打断; 推进需要两路独立正常证据(R1.3) */
static void c_REL32(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    for (i = 1; i <= 100 && e.severe_active == 0; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_warn - 1),
                      (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_warn + 1),
                      (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_FUSED_SEVERE && e.severe_active == 1,
          "前置融合没成立(r=%s severe=%d)", rel_reason_name(r), e.severe_active);

    for (i = 1; i <= 100; ++i)                 /* SG 回带内, ADC 仍警告 */
        r = feed_step(&f, 500,
                      (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_warn + 1),
                      (i % 10) == 0, 1, 1, 1);
    CHECK(e.severe_active == 1, "中间那次 ADC 异常被忽略了(严重已解除)");
    CHECK(e.ok_run_ms == 0u, "恢复段没被反向证据清零(ok=%ums)", e.ok_run_ms);

    for (i = 1; i <= 80; ++i)                  /* 正控: 两路都新鲜正常 */
        r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(e.severe_active == 0u, "两路新鲜正常 800ms 后仍粘着严重");
    CHECK(r == REL_REASON_NONE, "解除后原因=%s(期望 NONE)", rel_reason_name(r));

    world_reset();                             /* 单路正常不得冒充恢复 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 100 && e.severe_active == 0; ++i)
        r = feed_step(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_warn - 1),
                      (rt_uint32_t)(p.run_adc_max + p.run_adc_noise + p.adc_dev_warn + 1),
                      (i % 10) == 0, 1, 1, 1);
    CHECK(e.severe_active == 1, "前置严重没成立, 无法验证单路恢复");
    for (i = 1; i <= 200; ++i)                 /* SG 新鲜正常, ADC 身份冻结 */
        r = feed_step(&f, 500, 2000, (i % 10) == 0, 0, 1, 1);
    CHECK(e.ok_src_seen != 3u || e.severe_active == 1,
          "ADC 停止出新样本却完成了恢复(ok=%ums seen=%d)", e.ok_run_ms, (int)e.ok_src_seen);
    CHECK(r == REL_SENSOR_UNAVAILABLE || e.severe_active == 1,
          "单源解除严重(%s)", rel_reason_name(r));
}

/* REL22 覆盖丢失的合同(勘误版): 已武装却失去判据 = 先保护, 后披露
 * 原 REL22 把"签名失配仍然 post=0"当成正确期望, 那是**错误的合同**; M8-R1 明确
 * 更正: 失配必须走真实短保护, 状态打印只能排在保护之后。身份 REL22 保留。 */
static void c_REL22(void)
{
    diag_rprof_t p;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    g_tick_hook = r1_hook_sig_break;
    pump(200);
    g_tick_hook = RT_NULL;
    CHECK(g_post_calls == 1, "签名漂移后 post=%d(期望恰好 1 次真实保护)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_COVERAGE_LOST,
          "保护原因=%s(期望 REL_COVERAGE_LOST)", rel_reason_name(l.reason));
    CHECK(l.coverage_at_trip == RELCOV_SIG_MISMATCH, "档位=%u(期望 SIG_MISMATCH)",
          (unsigned)l.coverage_at_trip);
    CHECK(g_post_reason_lines_at_call == 0,
          "post 时已有 %d 条触发摘要 —— 打印跑到了保护前面", g_post_reason_lines_at_call);
    CHECK(cap_has("NOT-ACTIVE(sig-mismatch)"), "覆盖状态没披露");
    CHECK(cap_count("REL coverage=") <= 3, "覆盖行刷屏(%d 条)",
          cap_count("REL coverage="));
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "触发后模式=%d(期望退回 MONITOR_ONLY)",
          (int)d_mode);
    CHECK(cap_max_line_len() <= 110, "覆盖披露行 %d 字节(预算 110)", cap_max_line_len());

    world_reset();                             /* MONITOR_ONLY 无 profile: 只披露不触发 */
    cap_reset();
    pump(200);
    CHECK(g_post_calls == 0, "MONITOR_ONLY 无 profile 却 post %d 次", g_post_calls);
    CHECK(cap_count("REL coverage=") == 0, "MONITOR_ONLY 占了串口(%d 条)",
          cap_count("REL coverage="));
}

/* REL33 取锁失败与"没有 profile"同样是有界降险(B3 + R2.1) */
static void c_REL33(void)
{
    diag_rprof_t p;
    rel_latch_t l;
    rel_state_t st;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    g_tick_hook = r1_hook_lock_sticky;
    pump(120);
    g_tick_hook = RT_NULL;
    CHECK(g_post_calls == 1, "取锁失败后 post=%d(期望 1)", g_post_calls);
    /* R2-A.1/A.3: 锁取不到期间, 读取侧只能"整份拿到"或"明确拒绝", 不许拼出半份身份;
     * 保护本身不受影响(上面 post=1 已证)。 */
    memset(&l, 0xFF, sizeof(l));
    CHECK(rel_prod_get_latch(&l) == RT_FALSE, "控制锁取不到却读到了首因");
    memset(&st, 0xFF, sizeof(st));
    CHECK(rel_prod_get_snapshot(&st) != RT_EOK, "控制锁取不到却读到了快照");
    CHECK(cap_has("NOT-ACTIVE(config-lock)"), "取锁失败没披露");
    /* 首因的权威副本在引擎(唯一写者)手里: 锁恢复后镜像必须补得上, 失败次数如实记账。 */
    g_mutex_take_fail = 0;
    pump(5);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_COVERAGE_LOST &&
          l.coverage_at_trip == RELCOV_LOCK_FAIL,
          "身份不对(reason=%s cov=%u)", rel_reason_name(l.reason),
          (unsigned)l.coverage_at_trip);
    CHECK(rel_prod_get_snapshot(&st) == RT_EOK && st.pub_fail_cnt > 0u,
          "发布失败没计数(pub=%u)", (unsigned)st.pub_fail_cnt);

    world_reset();                             /* 有界容错: 110ms 的瞬态不得触发 */
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    g_tick_hook = r1_hook_lock_blink;
    pump(120);
    g_tick_hook = RT_NULL;
    CHECK(g_post_calls == 0, "瞬态锁失败就保护了(post=%d)", g_post_calls);
    CHECK(d_rel_prod.cov_lost_ms == 0u, "覆盖恢复后 cov_lost_ms 没清零(%u)",
          d_rel_prod.cov_lost_ms);

    world_reset();                             /* 已武装但一个 profile 都没有 */
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    pump(120);
    CHECK(g_post_calls == 1, "武装态无 profile 未降险(post=%d)", g_post_calls);
    CHECK(cap_has("NOT-ACTIVE(no-profile)"), "无 profile 没披露");
}

/* REL34 取不到帧也要推进监督; 运动事实不可得时不得宣称覆盖有效(R2.2) */
static void c_REL34(void)
{
    diag_rprof_t p;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    g_stop_sensor = 1;                        /* 从未出过帧 */
    pump(80);
    CHECK(g_post_calls == 1, "持续无帧时 post=%d(期望 1)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_COVERAGE_LOST &&
          l.coverage_at_trip == RELCOV_NOFRAME,
          "无帧降险身份不对(reason=%s cov=%u)", rel_reason_name(l.reason),
          (unsigned)l.coverage_at_trip);
    CHECK(cap_has("NOT-ACTIVE(no-frame)"), "无帧状态没披露");

    world_reset();                             /* 连运动事实也取不到 */
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    g_stop_sensor = 1; g_snap_fail = 1;
    pump(80);
    CHECK(g_post_calls == 1, "无帧且无运动事实时 post=%d(不知道在不在动就不能宣称有效)",
          g_post_calls);
    g_snap_fail = 0;

    world_reset();                             /* 证得静止: 没有需要保护的运动 */
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    g_stop_sensor = 1;
    pump(200);
    CHECK(g_post_calls == 0, "证得静止仍然触发(post=%d)", g_post_calls);
    g_stop_sensor = 0;
}

/* REL35 事故生命周期: 一次武装一张票据, 人工恢复不自动再武装(C6/R2.4) */
static void c_REL35(void)
{
    diag_rprof_t p;
    rel_latch_t l;
    rel_incident_t inc;
    rt_uint32_t gen_before;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(120);
    CHECK(g_post_calls == 1, "第一次事故 post=%d", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_SG_SEVERE, "首因=%s",
          rel_reason_name(l.reason));
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "触发后没退回 MONITOR_ONLY(%d)", (int)d_mode);
    rel_prod_get_incident(&inc);
    CHECK(inc.rearm_required == 1, "票据没被标记为已消耗");
    CHECK(cap_has("rearm required"), "没把'需要重新武装'说出来: [%.120s]", cap_text());

    S.safety = SAFETY_FAULT_LATCHED; S.mstate = MOTOR_FAULT; S.armed = 0; S.mhz = 0;
    pump(5);
    S.safety = SAFETY_READY; S.mstate = MOTOR_IDLE; S.mhz = 0; S.raw = 2000; S.sg = 500;
    pump(60);
    CHECK(g_post_calls == 1, "人工恢复过程里多 post 了(%d)", g_post_calls);
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "相对保护被自动恢复了(%d)", (int)d_mode);
    CHECK(d_relative_confirm_given == RT_FALSE, "恢复路径凭空造出了 CONFIRM 凭据");

    rel_prod_get_incident(&inc);
    gen_before = inc.generation;
    diagnosis_take_relative_confirm();
    CHECK(diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION) == RT_EOK &&
          d_mode == DIAG_MODE_RELATIVE_PROTECTION, "显式重新武装失败");
    CHECK(cap_has("REL re-arm prior r=REL_SG_SEVERE"), "旧事故首因没被宣布: [%.120s]",
          cap_text());
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500;
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(140);
    CHECK(g_post_calls == 2, "新代际里第二次事故 post 总数=%d(期望 2)", g_post_calls);
    rel_prod_get_incident(&inc);
    CHECK(inc.have_archived && inc.archived.reason == REL_SG_SEVERE,
          "旧事故归档丢了(%d %s)", (int)inc.have_archived, rel_reason_name(inc.archived.reason));
    CHECK(inc.current.reason == REL_SG_SEVERE &&
          inc.current.trig_tick > inc.archived.trig_tick, "本轮首因没独立记录");
    CHECK(inc.generation > gen_before, "显式再武装后代际没前进(%u -> %u)",
          (unsigned)gen_before, (unsigned)inc.generation);
    CHECK(inc.rearm_required == 1, "第二次事故后票据没被消耗");
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "第二次触发后模式=%d", (int)d_mode);
}

/* REL36 输出参数为 NULL 也不能跳过真实保护(R2.5) */
static void c_REL36(void)
{
    diag_rprof_t p;
    rel_step_arg_t a;
    rel_state_t s;
    rel_latch_t l;
    int i;
    int snap_rc;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    memset(&a, 0, sizeof(a));
    a.prof = p;
    a.mode = (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION;
    a.coverage = RELCOV_ABSENT;
    a.in.motion_known = 1;
    a.in.motor_state = (rt_uint8_t)MOTOR_CRUISE;
    for (i = 0; i < 100; ++i)
    {
        a.now_tick = 1000u + (rt_uint32_t)i * 10u;
        a.in.seq = (rt_uint32_t)i + 1u;
        a.in.timestamp = a.now_tick;
        (void)rel_prod_evaluate(&a, (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION,
                                RT_NULL, RT_NULL);
    }
    CHECK(g_post_calls == 1, "NULL 输出参数把保护吞掉了(post=%d)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_COVERAGE_LOST, "首因=%s",
          rel_reason_name(l.reason));
    snap_rc = rel_prod_get_snapshot(&s);
    CHECK(snap_rc == RT_EOK &&
          s.coverage == RELCOV_ABSENT && s.cov_lost_ms >= 500u,
          "快照没反映覆盖丢失(rc=%d cov=%u ms=%u)", (int)snap_rc,
          (unsigned)s.coverage, (unsigned)s.cov_lost_ms);
}

/* REL37 事件投递失败仍然先保护、首因与票据都留着(R2.6) */
static void c_REL37(void)
{
    diag_rprof_t p;
    rel_latch_t l;
    rel_incident_t inc;

    world_reset();
    p = make_profile();
    g_post_rc_inject = -RT_EIO;
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(200);
    CHECK(g_post_calls == 1, "投递失败后重复投递(%d 次)", g_post_calls);
    CHECK(rel_prod_get_latch(&l), "投递失败却连 latch 都没有");
    CHECK(l.post_rc == (rt_int16_t)(-RT_EIO), "post_rc=%d 没如实记录", (int)l.post_rc);
    CHECK(l.reason == REL_SG_SEVERE && l.value_raw == (rt_int32_t)S.sg,
          "首因/触发量丢了(%s val=%d)", rel_reason_name(l.reason), (int)l.value_raw);
    rel_prod_get_incident(&inc);
    CHECK(inc.rearm_required == 1, "以'成功排队'替代'实际已保护': rearm=%d",
          (int)inc.rearm_required);
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "投递失败后模式没降(%d)", (int)d_mode);
}

/* REL38 人工模式与 profile 变更在活动运行中一律拒绝(C1), 开启必须过最终静止门(C2) */
static void c_REL38(void)
{
    diag_rprof_t p, back;
    rt_err_t e;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    cap_reset();
    e = diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    CHECK(e != RT_EOK, "运动中关掉了相对保护(rc=%d)", (int)e);
    CHECK(d_mode == DIAG_MODE_RELATIVE_PROTECTION, "被拒绝的切换仍然改了模式(%d)", (int)d_mode);
    CHECK(cap_has("needs static/idle"), "拒绝理由没披露: [%.90s]", cap_text());
    e = diagnosis_set_mode(DIAG_MODE_ACTIVE_PROTECTION);
    CHECK(e != RT_EOK, "运动中还能切 ACTIVE(rc=%d)", (int)e);

    world_reset();                             /* C2: 静止事务被占用 ⇒ 拒绝且问过门 */
    install_profile(&p);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    g_static_begin_busy = 1;
    diagnosis_take_relative_confirm();
    e = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(e != RT_EOK, "静止事务被占用时却启用成功(rc=%d)", (int)e);
    CHECK(g_static_begin_calls == 1, "根本没问静止事务门(calls=%d)", g_static_begin_calls);
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "拒绝后模式变成 %d", (int)d_mode);
    CHECK(cap_has("static txn not granted"), "没披露门被拒: [%.90s]", cap_text());
    g_static_begin_busy = 0;

    world_reset();                             /* clear 在运动中不得生效 */
    install_profile(&p);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500;
    cap_reset();
    {
        char *av[] = { "diag_profile", "clear", "CONFIRM" };
        diag_profile(3, av);
    }
    CHECK(cap_has("clear REFUSED"), "运动中却作废了 profile: [%.90s]", cap_text());
    CHECK(project_config_get_rprof(&back) == RT_EOK && back.magic == DIAG_RPROF_MAGIC,
          "被拒绝的 clear 仍然改了配置");
}

/* REL39 zero 全程静止(C5) + 失败重学不得复用旧凭据(C4) */
static void c_REL39(void)
{
    diag_rprof_t good, back;

    world_reset();                             /* C5 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660; S.sg = 500;
    g_pump_hook = r1_hook_arm_midway; g_pump_n = 0; g_cmd_pump = 1;
    run_thread(ROLE_MAIN, cmd_zero, RT_NULL, 0);
    g_cmd_pump = 0; g_pump_hook = RT_NULL;
    CHECK(d_pending_zero == RT_FALSE, "中途起转的零点却发布了凭据");
    CHECK(d_cap.active == 0, "作废后采集状态机还挂着");
    CHECK(cap_has("zero VOIDED"), "没有具名作废行: [%.90s]", cap_text());
    CHECK(d_pending.zero_n == 0u, "作废的候选里留了零点数(%u, 带 %u..%u)",
          (unsigned)d_pending.zero_n, (unsigned)d_pending.zero_min,
          (unsigned)d_pending.zero_max);

    world_reset();                             /* C4 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    CHECK(d_pending_zero == RT_TRUE, "前置零点没成功: [%.80s]", cap_text());
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    CHECK(learn_run(400) == REL_JOB_DONE, "前置学习没成功: [%.80s]", cap_text());
    CHECK(d_pending_learn == RT_TRUE, "前置学习没产出凭据: [%.80s]", cap_text());
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_learn_far_window);
    /* R7D1: 离窗重学的失败发生在后台推进里; 结论行由 learn_status 从快照打印。 */
    CHECK(job_wait(400) == REL_JOB_FAILED, "离窗重学没在预算内终结: st=%d",
          (int)job_snap().state);
    CHECK(strcmp(job_snap().why, "WINDOW_BROKEN") == 0,
          "离窗重学的终结原因不是 WINDOW_BROKEN: [%s]", job_snap().why);
    do_cmd(cmd_learn_status);
    CHECK(cap_has("st=FAILED") && cap_has("why=WINDOW_BROKEN"),
          "learn_status 没把失败终态与原因打出来: [%.90s]", cap_text());
    CHECK(d_pending_learn == RT_FALSE, "失败的重学却保留了旧 learn 凭据");
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED"), "失败重学之后竟然提交了: [%.90s]", cap_text());
    {
        int got = (project_config_get_rprof(&back) == RT_EOK &&
                   back.magic == DIAG_RPROF_MAGIC);
        CHECK(got == 0, "发布出了 %u..%u", (unsigned)back.hz_low, (unsigned)back.hz_high);
    }

    world_reset();                             /* 已提交的 profile 不被失败尝试改坏 */
    good = make_profile();
    install_profile(&good);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    do_cmd(cmd_learn_far_window);
    CHECK(job_wait(400) == REL_JOB_FAILED, "已发布 profile 前置: 失败学习没终结 st=%d",
          (int)job_snap().state);
    project_config_get_rprof(&back);
    CHECK(back.magic == DIAG_RPROF_MAGIC && back.profile_id == good.profile_id &&
          back.hz_low == good.hz_low, "失败的学习改了已发布 profile(id=%u lo=%u)",
          (unsigned)back.profile_id, (unsigned)back.hz_low);
}

/* REL40 配置取锁失败: 不写 Flash、不 release 未持有的锁、字段不动(C3/R3.8) */
static void c_REL40(void)
{
    diag_rprof_t p, after;
    project_config_t snap;
    rt_err_t rc;
    int rel_before;

    world_reset();
    p = make_profile();
    install_profile(&p);
    rel_before = g_mutex_release_calls;
    g_mutex_take_fail = 1;
    rc = project_config_save();
    CHECK(rc != RT_EOK, "取锁失败却返回 rc=%d", (int)rc);
    CHECK(g_blob_valid == 0, "取锁失败仍然写了 Flash 副本");
    CHECK(g_mutex_release_calls == rel_before,
          "未持有却 release 了(%d 次)", g_mutex_release_calls - rel_before);
    rc = project_config_get_snapshot(&snap);
    CHECK(rc != RT_EOK, "取锁失败却给了整套快照(rc=%d)", (int)rc);
    rc = project_config_get_rprof(&after);
    CHECK(rc != RT_EOK, "取锁失败却给了 profile(rc=%d)", (int)rc);
    rc = project_config_set_rprof(&p);
    CHECK(rc != RT_EOK, "取锁失败却发布了 profile(rc=%d)", (int)rc);
    rc = project_config_clear_rprof();
    CHECK(rc != RT_EOK, "取锁失败却作废了 profile(rc=%d)", (int)rc);
    rc = project_config_load();
    CHECK(rc != RT_EOK, "取锁失败却改了工作集(rc=%d)", (int)rc);
    rc = project_config_set(&snap);
    CHECK(rc != RT_EOK, "取锁失败却接受了整套设置(rc=%d)", (int)rc);
    CHECK(cap_has("defaults REFUSED") == 0, "本例没调 defaults 却报了名");
    project_config_defaults();
    CHECK(cap_has("defaults REFUSED"), "回默认没披露被锁挡住: [%.120s]", cap_text());
    g_mutex_take_fail = 0;
    rel_before = g_mutex_release_calls;
    CHECK(project_config_save() == RT_EOK && g_blob_valid == 1,
          "恢复锁之后仍然存不了");
    CHECK(project_config_get_rprof(&after) == RT_EOK &&
          memcmp(&after, &p, sizeof(p)) == 0, "一路失败之后 profile 字段被改动了");
}

/* REL41 非法上下文静默拒绝(R3.7): 不解析、不取锁、不打印 */
static void c_REL41(void)
{
    diag_rprof_t after;
    int got;
    char *av_show[]  = { "diag_profile", "show" };
    char *av_commit[] = { "diag_profile", "commit" };
    char *av_junk[]  = { "diag_profile", "nonsense" };

    world_reset();
    {
        diag_rprof_t p = make_profile();
        install_profile(&p);
    }
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;

    g_isr_nest = 1;
    cap_reset();
    diag_profile(2, av_show);
    CHECK(cap_text()[0] == 0, "ISR 上下文里竟然打印了: [%.80s]", cap_text());
    CHECK(g_static_begin_calls == 0, "ISR 上下文里竟然动了静止事务门(%d)",
          g_static_begin_calls);
    g_isr_nest = 0;

    g_primask_masked = 1;
    cap_reset();
    diag_profile(2, av_commit);
    CHECK(cap_text()[0] == 0, "PRIMASK 窗口里竟然打印了: [%.80s]", cap_text());
    g_primask_masked = 0;

    g_no_thread = 1;
    cap_reset();
    diag_profile(2, av_junk);
    CHECK(cap_text()[0] == 0, "无线程对象时连 usage 都打印了: [%.80s]", cap_text());
    g_no_thread = 0;

    cap_reset();                               /* 合法上下文必须照常给结论 */
    diag_profile(2, av_show);
    CHECK(cap_has("profile id="), "合法上下文却什么都没打印: [%.80s]", cap_text());
    got = (project_config_get_rprof(&after) == RT_EOK && after.magic == DIAG_RPROF_MAGIC);
    CHECK(got, "前置 profile 丢了");
}

/* REL42 低 SG 基线要能生成可达阈值(D1), 分不开的工况具名拒绝并给数据(R4.1/R4.3) */
static void c_REL42(void)
{
    diag_rprof_t back, p, q;
    const char *why = RT_NULL;
    rt_uint32_t rup = 0, rdn = 0, t;
    int got;

    world_reset();                             /* D1 同前置: 稳定 SG=64 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660; S.sg = 64;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 64;
    pump_sensor(2);
    CHECK(learn_run(400) == REL_JOB_DONE, "稳定低 SG 学不出候选: st=%d [%.90s]",
          (int)job_snap().state, cap_text());
    CHECK(d_pending_learn == RT_TRUE, "稳定低 SG 没产出凭据: [%.90s]", cap_text());
    CHECK(d_pending.run_sg_min == 64u, "基线=%u(期望 64)", (unsigned)d_pending.run_sg_min);
    CHECK(d_pending.sg_drop_trip <= d_pending.run_sg_min,
          "生成的 trip=%u 仍大于码域上限 %u", (unsigned)d_pending.sg_drop_trip,
          (unsigned)d_pending.run_sg_min);
    CHECK(d_pending.sg_drop_warn > 0u && d_pending.sg_drop_warn < d_pending.sg_drop_trip,
          "层次不对(%u/%u)", (unsigned)d_pending.sg_drop_warn,
          (unsigned)d_pending.sg_drop_trip);
    /* 结构自洽先看层次(上面三条), 完整 rel_profile_validate 要等 commit 之后
     * 对**已发布**的那一份做 —— COMMITTED/profile_id/设计初值都是 commit 赋的,
     * 拿它们去校验一个还没发布的候选是在校验不存在的东西。 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(!cap_has("SG_TRIP_UNREACHABLE"), "低 SG 基线仍然被判不可达: [%.90s]", cap_text());
    got = (project_config_get_rprof(&back) == RT_EOK && back.magic == DIAG_RPROF_MAGIC);
    CHECK(got, "稳定低 SG 提交不了: [%.90s]", cap_text());
    CHECK((back.flags & RPF_COMMITTED) != 0, "提交后没有 COMMITTED 位");
    CHECK((back.flags & RPF_FIELD_VERIFIED) == 0, "自动生成的阈值自称 FIELD_VERIFIED");
    CHECK(back.threshold_tier >= 1u && back.threshold_tier <= 3u, "档位没落盘(%u)",
          (unsigned)back.threshold_tier);
    why = RT_NULL;
    CHECK(rel_profile_validate(&back, &why) == RT_EOK, "发布出去的 profile 不合格(%s)",
          why ? why : "?");

    world_reset();                             /* 正常波动 64/68/72 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660; S.sg = 64;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000;
    g_sg_hook = r1_sg_646872;
    pump_sensor(2);
    CHECK(learn_run(400) == REL_JOB_DONE, "小幅正常波动学不出: st=%d [%.90s]",
          (int)job_snap().state, cap_text());
    CHECK(d_pending_learn == RT_TRUE, "小幅正常波动没产出凭据: [%.90s]", cap_text());
    CHECK(d_pending.run_sg_min == 64u && d_pending.run_sg_max == 72u,
          "波动带 [%u..%u]", (unsigned)d_pending.run_sg_min, (unsigned)d_pending.run_sg_max);
    CHECK(d_pending.sg_drop_warn > (rt_uint16_t)(d_pending.run_sg_max - d_pending.run_sg_min),
          "警告层没盖住正常波动(warn=%u span=%u)", (unsigned)d_pending.sg_drop_warn,
          (unsigned)(d_pending.run_sg_max - d_pending.run_sg_min));
    g_sg_hook = RT_NULL;

    world_reset();                             /* 负控: SG 接近 0 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660; S.sg = 1;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 1;
    pump_sensor(2);
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(job_wait(400) == REL_JOB_FAILED, "近零基线竟然学出了候选: st=%d",
          (int)job_snap().state);
    do_cmd(cmd_learn_status);
    CHECK(cap_has("why=SG_HEADROOM_INSUFFICIENT"),
          "近零基线没被具名拒绝: [%.120s]", cap_text());
    CHECK(cap_has("sg[1..1]"), "拒绝行没带数据依据: [%.120s]", cap_text());
    CHECK(d_pending_learn == RT_FALSE, "被拒绝的候选却置了凭据");

    world_reset();                             /* 负控: 噪声吃掉大半余量 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660; S.sg = 300;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000;
    g_sg_hook = r1_sg_wide;
    pump_sensor(2);
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(job_wait(400) == REL_JOB_FAILED, "噪声过大的工况没被拒绝: st=%d",
          (int)job_snap().state);
    do_cmd(cmd_learn_status);
    CHECK(cap_has("st=FAILED"), "拒绝的终态没打出来: [%.120s]", cap_text());
    CHECK(cap_has("SG_HEADROOM_INSUFFICIENT"), "拒绝原因没具名: [%.120s]", cap_text());
    g_sg_hook = RT_NULL;

    p = make_profile();                        /* R4.3: 可达性按扩张带核定 */
    p.run_adc_min = 100; p.run_adc_max = 4000; p.run_adc_noise = 500;
    p.run_adc_mean_x1000 = 2000000u;
    p.adc_dev_warn = 90; p.adc_dev_trip = 100;
    p.signature = rel_profile_signature(&p);
    rel_adc_reach(&p, &rup, &rdn);
    CHECK(rup == 0u && rdn == 0u, "扩张带两侧余量=%u/%u(带已被撑满, 期望 0/0)",
          (unsigned)rup, (unsigned)rdn);
    why = RT_NULL;
    CHECK(rel_profile_validate(&p, &why) == -RT_EINVAL && why &&
          strcmp(why, "ADC_TRIP_UNREACHABLE") == 0,
          "拿未扩张的带校验、执行时却用扩张带: why=%s", why ? why : "(null)");

    p = make_profile();                        /* 三档都在健康基线上张开 */
    for (t = 1; t <= 3; ++t)
    {
        why = RT_NULL;
        q = p;
        CHECK(rel_profile_gen_thresholds(&q, (rt_uint8_t)t, &why) == RT_EOK,
              "档 %u 在健康基线上被拒(%s)", (unsigned)t, why ? why : "?");
        CHECK(q.sg_drop_warn > (rt_uint16_t)(q.run_sg_max - q.run_sg_min) &&
              q.sg_drop_warn < q.sg_drop_trip && q.sg_drop_trip <= q.run_sg_min,
              "档 %u 的 SG 层次不对(%u/%u, min=%u)", (unsigned)t,
              (unsigned)q.sg_drop_warn, (unsigned)q.sg_drop_trip, (unsigned)q.run_sg_min);
        CHECK(q.adc_dev_warn > (rt_int16_t)q.run_adc_noise &&
              q.adc_dev_warn < q.adc_dev_trip, "档 %u 的 ADC 层次不对", (unsigned)t);
    }
    why = RT_NULL;
    CHECK(rel_profile_gen_thresholds(&p, 0, &why) != RT_EOK, "档 0 没被拒绝");
    why = RT_NULL;
    CHECK(rel_profile_gen_thresholds(&p, 4, &why) != RT_EOK && why &&
          strcmp(why, "TIER_RANGE") == 0, "档 4 没具名拒绝");
}

/* REL43 关键行在合法最大值/最长原因/负错误码下仍然完整(E1) */
static void c_REL43(void)
{
    diag_rprof_t p;
    rel_state_t s;
    int mx;

    world_reset();
    p = make_profile();
    p.profile_id = 0xFFFFFFFFu;
    p.revision = 0xFFFFFFFFu;
    p.signature = rel_profile_signature(&p);
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    s_prev.sg_seq = 4000000000u; s_prev.cur_seq = 4000000000u;
    s_prev.imu_seq = 4000000000u;
    g_post_rc_inject = -RT_EIO;
    pump(140);
    cap_reset();
    diag_relative_status_lines();
    mx = cap_max_line_len();
    CHECK(mx <= 110, "状态行最长 %d 字节(预算 110)", mx);
    CHECK(cap_has("id=4294967295") && cap_has("rev=4294967295"), "最大身份没导出完整");
    CHECK(cap_has("post_rc=-"), "负错误码没导出: [%.120s]", cap_text());
    CHECK(cap_has("r=REL_SG_SEVERE"), "首因没出现在状态行: [%.120s]", cap_text());
    CHECK(cap_has("warn=") && cap_has("strong="), "warn/strong 两级没分别导出");
    CHECK(cap_has("reach=") && cap_has("tier="), "可达余量/档位没导出");
    CHECK(cap_has("imu_use="), "IMU 覆盖没披露");
    CHECK(cap_has("cov=") && cap_has("gen="), "覆盖档位/代际没披露");
    rel_prod_get_snapshot(&s);
    CHECK(s.last_sg_seq != 0u && s.last_cur_seq != 0u, "样本身份没进快照");

    world_reset();                             /* 最长档位名 + 触发摘要三行 */
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    g_mutex_take_fail = 1;
    cap_reset();
    pump(70);
    mx = cap_max_line_len();
    CHECK(mx <= 110, "config-lock 档与触发摘要最长 %d 字节(预算 110)", mx);
    CHECK(cap_has("NOT-ACTIVE(config-lock)"), "config-lock 档没披露");
    /* R2-A.2: 取锁失败只让"给人读的镜像"待补, 不能让摘要整体消失 —— 保护确实发生了。 */
    CHECK(cap_count("[DIAG] REL TRIP r=REL_COVERAGE_LOST") == 1, "触发摘要打成 %d 份",
          cap_count("[DIAG] REL TRIP r="));
    CHECK(cap_count("[DIAG] REL TRIP val=") == 1, "触发摘要第二行没跟着出");
    CHECK(cap_count("[DIAG] REL TRIP sg=") == 1, "触发摘要第三行没跟着出");
    /* 镜像读不到时, 状态行必须具名披露 snap/inc 失败, 而不是拼半份身份(REL43 的 17B
     * 原因名在下面锁恢复之后才可能出现在状态行里 —— 锁死的时候出现才是说谎)。 */
    cap_reset();
    diag_relative_status_lines();
    CHECK(cap_has("snap=FAIL") && cap_has("inc=FAIL"),
          "取锁失败没披露读数不可用: [%.120s]", cap_text());
    CHECK(cap_has("pubfail="), "发布失败次数没导出");
    CHECK(cap_max_line_len() <= 110, "降级状态行最长 %d 字节(预算 110)", cap_max_line_len());
    g_mutex_take_fail = 0;
    pump(5);                                   /* 镜像补发一次(REL33 同一条重试) */
    cap_reset();
    diag_relative_status_lines();
    CHECK(cap_has("r=REL_COVERAGE_LOST"), "覆盖丢失原因名没导出(最长原因 17B)");
    CHECK(cap_has("snap=OK") && cap_has("inc=OK"), "锁恢复后状态行没回到可用读数");
    CHECK(cap_max_line_len() <= 110, "最长原因名状态行 %d 字节(预算 110)", cap_max_line_len());
}

/* REL44 碰撞独立保护(R6): 只认新鲜有效 IMU 证据 */
static void c_REL44(void)
{
    rel_engine_t e;
    struct feed f;
    diag_rprof_t p;
    rel_reason_t r = REL_REASON_NONE;
    rel_latch_t l;
    int i;

    world_reset();
    p = make_profile();
    p.impact_peak_mg = 2500; p.impact_new_min = 2;
    p.dur_impact_ms = 100; p.age_imu_ms = 100;
    p.signature = rel_profile_signature(&p);

    feed_init(&e, &p, &f);                     /* 静止 ~1g 连续 200 帧 */
    for (i = 1; i <= 200; ++i)
        r = feed_step_imu(&f, 500, 2000, (i % 10) == 0, 1, 1, 1, 1212, 1);
    CHECK(r == REL_REASON_NONE, "静止重力被当成碰撞(%s)", rel_reason_name(r));
    CHECK(e.imp_cnt == 0u, "冲击计数=%u(期望 0)", e.imp_cnt);

    feed_init(&e, &p, &f);                     /* 两枚新样本过门 */
    r = feed_step_imu(&f, 500, 2000, 1, 1, 1, 1, 3000, 1);
    CHECK(r == REL_REASON_NONE && e.imp_cnt == 1u, "第一枚冲击后计数=%u", e.imp_cnt);
    r = feed_step_imu(&f, 500, 2000, 0, 1, 1, 1, 2900, 1);
    CHECK(r == REL_IMPACT, "两枚独立冲击后=%s(期望 REL_IMPACT)", rel_reason_name(r));
    CHECK(e.severe_active == 1 && e.imp_run_ms <= p.dur_impact_ms,
          "冲击窗口=%u(上限 %u)", e.imp_run_ms, p.dur_impact_ms);

    feed_init(&e, &p, &f);                     /* 旧 peak 不冒充第二次冲击 */
    feed_step_imu(&f, 500, 2000, 0, 1, 1, 1, 3000, 1);
    for (i = 1; i <= 200; ++i)
        r = feed_step_imu(&f, 500, 2000, (i % 10) == 0, 1, 1, 1, 3000, 0);
    CHECK(e.imp_cnt == 1u, "冻结身份把一次冲击数成了 %u 次", e.imp_cnt);
    CHECK(r != REL_IMPACT, "滑窗里留着的 peak 被当成又撞一次(%s)", rel_reason_name(r));

    feed_init(&e, &p, &f);                     /* 中间夹着新鲜正常样本 = 打断这一组 */
    feed_step_imu(&f, 500, 2000, 0, 1, 1, 1, 3000, 1);
    for (i = 1; i <= 20; ++i)
        r = feed_step_imu(&f, 500, 2000, 0, 1, 1, 1, 1000, 1);
    CHECK(r == REL_REASON_NONE, "相隔 200ms 的两枚样本被判成一组冲击(%s)",
          rel_reason_name(r));
    CHECK(e.imp_cnt == 0u, "中间的正常样本没打断这一组(imp_cnt=%u)", e.imp_cnt);
    /* 打断之后必须能重新起算: 连续两枚过门样本仍然触发 */
    r = feed_step_imu(&f, 500, 2000, 0, 1, 1, 1, 3000, 1);
    CHECK(r == REL_REASON_NONE && e.imp_cnt == 1u, "重起算后第一枚=%u", e.imp_cnt);
    r = feed_step_imu(&f, 500, 2000, 0, 1, 1, 1, 3100, 1);
    CHECK(r == REL_IMPACT, "重新起算的连续两枚没有触发(%s)", rel_reason_name(r));

    world_reset();                             /* IMU 缺失不影响 SG/ADC 判据 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 200; ++i)
        r = feed_step_imu(&f, (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1), 2000,
                          (i % 10) == 0, 1, 1, 1, 1000, 0);
    CHECK(r == REL_SG_SEVERE, "IMU 缺失让 SG 判据也不可用了(%s)", rel_reason_name(r));
    CHECK(e.imu_usable == 0u, "imu_usable 没如实置 0");

    world_reset();                             /* 生产入口: 恰好一次真实保护 */
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mx = 2000; S.my = 2000; S.mz = 400;      /* 合成幅值约 2857mg > 2500 */
    pump(40);
    CHECK(g_post_calls == 1, "碰撞后 post=%d(期望恰好 1)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_IMPACT, "碰撞原因没进首因(%s)",
          rel_reason_name(l.reason));
    if (l.latched)
    {
        CHECK(l.value_raw >= (rt_int32_t)p.impact_peak_mg, "触发量=%d 不是振动 mg",
              (int)l.value_raw);
        CHECK(l.threshold_raw == (rt_int32_t)p.impact_peak_mg, "阈值=%d",
              (int)l.threshold_raw);
        CHECK(l.first_imu_seq != 0u, "首因没记 IMU 样本身份");
    }
    CHECK(d_mode == DIAG_MODE_MONITOR_ONLY, "碰撞后没退 MONITOR(%d)", (int)d_mode);

    world_reset();                             /* MONITOR 下只评估不行动 */
    cap_reset();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_MONITOR_ONLY);
    S.mx = 2000; S.my = 2000; S.mz = 400;
    pump(60);
    CHECK(g_post_calls == 0, "MONITOR_ONLY 下碰撞也 post 了(%d)", g_post_calls);
    CHECK(d_rel_prod.verdict == REL_IMPACT, "MONITOR 下读不到假设结果: %s",
          rel_reason_name(d_rel_prod.verdict));
    CHECK(cap_count("[DIAG] REL TRIP r=") == 0, "MONITOR 却打了行动摘要");
}

/* ======================= M8-R2 新增: 控制层/静止门/代际隔离/运动事实 ======================= */

/* REL45 取锁失败必须真的拒绝: 一个字段都不写、不 release 没持有的锁、错误回到调用方,
 * 并且下一次仍然**尝试**取锁(失败不等于"以后都不用锁了")。
 * 对应审核反例 NEW|CTL_LOCK_FAIL_WRITES 与 NEW|CTL_LOCK_FAILURE_PERMANENT_BYPASS。 */
static void c_REL45(void)
{
    rel_ctl_t c;
    rt_uint32_t gen = 0x5A5A5A5Au;
    rt_err_t rc;
    int takes, releases;

    world_reset();                             /* 锁是真的(rel_ctl_init 已建好) */
    CHECK(rel_ctl_ready() == RT_TRUE, "夹具里控制锁没真的初始化");
    CHECK(d_ctl.mode_valid == RT_TRUE && d_ctl.gen_want == 0u,
          "前置被改写(valid=%d gen_want=%u)", (int)d_ctl.mode_valid, (unsigned)d_ctl.gen_want);

    g_mutex_take_fail = 1;
    takes = g_mutex_take_calls; releases = g_mutex_release_calls;
    rc = rel_prod_commit((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION, RT_TRUE, &gen);
    CHECK(rc != RT_EOK, "取锁失败却返回成功(旧代码先无锁写再旁路)");
    CHECK(d_ctl.gen_want == 0u && d_ctl.mode_valid == RT_TRUE &&
          d_ctl.mode == (rt_uint8_t)DIAG_MODE_MONITOR_ONLY,
          "取锁失败仍然写了字段(gen_want=%u mode=%u valid=%d)",
          (unsigned)d_ctl.gen_want, (unsigned)d_ctl.mode, (int)d_ctl.mode_valid);
    CHECK(gen == 0x5A5A5A5Au, "失败路径改了出参(%u)", (unsigned)gen);
    CHECK(g_mutex_take_calls == takes + 1, "这次根本没去取锁(calls=%d)",
          g_mutex_take_calls - takes);
    CHECK(g_mutex_release_calls == releases, "释放了没持有的锁(+%d)",
          g_mutex_release_calls - releases);
    CHECK(d_ctl_lock.held == 0, "失败后锁还挂着(held=%d)", (int)d_ctl_lock.held);

    memset(&c, 0xFF, sizeof(c));
    rc = rel_prod_get_ctl(&c);
    CHECK(rc != RT_EOK, "取锁失败却把 ctl 交出去了");
    CHECK(c.gen_want == 0u && c.mode_valid == 0u, "失败时 out 没清零(want=%u valid=%u)",
          (unsigned)c.gen_want, (int)c.mode_valid);

    rc = rel_prod_request_rearm();
    CHECK(rc != RT_EOK && d_ctl.gen_want == 0u, "再武装在锁失败时仍然 bump 了代际");
    g_mutex_take_fail = 0;

    /* R2-A.1 的另一半: 失败**不是**永久旁路 —— 下一次调用必须重新真的取锁。 */
    takes = g_mutex_take_calls;
    rc = rel_prod_request_rearm();
    CHECK(rc == RT_EOK, "锁恢复后仍然拒绝(rc=%d)", (int)rc);
    CHECK(g_mutex_take_calls > takes, "失败一次之后就绕过锁了(take 没再发生)");
    CHECK(d_ctl.gen_want == 1u, "代际没前进(gen_want=%u)", (unsigned)d_ctl.gen_want);
    CHECK(d_ctl_lock.held == 0, "临界区没配对释放(held=%d)", (int)d_ctl_lock.held);

    /* 模式与代际是同一次提交: 失败时两者都不动(上面已证 gen_want=0/mode 不变)。 */
    g_mutex_take_fail = 1;
    rc = rel_prod_commit((rt_uint8_t)DIAG_MODE_ACTIVE_PROTECTION, RT_TRUE, RT_NULL);
    g_mutex_take_fail = 0;
    CHECK(rc != RT_EOK && d_ctl.mode == (rt_uint8_t)DIAG_MODE_MONITOR_ONLY &&
          d_ctl.gen_want == 1u, "半个提交(mode=%u gen_want=%u rc=%d)",
          (unsigned)d_ctl.mode, (unsigned)d_ctl.gen_want, (int)rc);
}

/* REL46 锁没初始化 => "初始化过没有"与"这次拿到了吗"分开答:
 * 未初始化时一次 rt_mutex_take 都不许发生, 但保护义务与降险披露照旧(不是静默旁路)。 */
static void c_REL46(void)
{
    diag_rprof_t p;
    rel_step_arg_t a;
    rel_state_t s;
    rel_latch_t l;
    rt_err_t prc, rc;
    rt_bool_t acted;
    int i, takes;

    world_reset();
    p = make_profile();
    install_profile(&p);
    d_ctl_inited = RT_FALSE;                   /* 回到"init 从未成功"的世界 */
    memset(&d_ctl_lock, 0, sizeof(d_ctl_lock));
    rel_prod_init_wipe();
    g_mutex_init_fail = 1;
    rc = rel_ctl_init();
    CHECK(rc != RT_EOK, "互斥量初始化失败却被当成成功");
    CHECK(rel_ctl_ready() == RT_FALSE, "就绪位与初始化结果不一致");
    takes = g_mutex_take_calls;
    rc = rel_prod_commit((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION, RT_TRUE, RT_NULL);
    CHECK(rc == -RT_EINVAL, "未初始化时应当具名拒绝(rc=%d)", (int)rc);
    CHECK(g_mutex_take_calls == takes, "未初始化却去 take 了(动过没建好的锁)");
    CHECK(d_ctl.gen_want == 0u, "未初始化时代际被 bump(gen_want=%u)",
          (unsigned)d_ctl.gen_want);
    CHECK(rel_prod_get_snapshot(&s) != RT_EOK, "未初始化却交出了快照");

    /* 关键: 锁不可用**不能**变成"不判据、不保护"。按 MONITOR 提交态评估也不许 post;
     * 这里以调用方 mode=RELATIVE 复现"未提交过 => 沿用调用方"的合同。 */
    memset(&a, 0, sizeof(a));
    a.prof = p;
    a.mode = (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION;
    a.coverage = RELCOV_ABSENT;
    a.in.motion_known = 1;
    a.in.motor_state = (rt_uint8_t)MOTOR_CRUISE;
    for (i = 0; i < 100; ++i)
    {
        a.now_tick = 1000u + (rt_uint32_t)i * 10u;
        a.in.seq = (rt_uint32_t)i + 1u;
        a.in.timestamp = a.now_tick;
        (void)rel_prod_evaluate(&a, (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION, &prc, &acted);
    }
    CHECK(g_post_calls == 1, "锁没初始化就把保护也旁路了(post=%d)", g_post_calls);
    CHECK(rel_writer_latch(&l) && l.coverage_at_trip == RELCOV_LOCK_FAIL,
          "锁不可用没进首因身份(cov=%u)", (unsigned)l.coverage_at_trip);
    CHECK(d_pub_fail > 0u, "镜像发布失败没记账(%u)", (unsigned)d_pub_fail);

    g_mutex_init_fail = 0;
    rc = rel_ctl_init();
    CHECK(rc == RT_EOK && rel_ctl_ready() == RT_TRUE, "恢复后仍然初始化不了");
    rc = rel_prod_commit((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION, RT_TRUE, RT_NULL);
    CHECK(rc == RT_EOK && d_ctl.gen_want == 1u, "恢复后提交仍然失败(rc=%d gen_want=%u)",
          (int)rc, (unsigned)d_ctl.gen_want);
}

/* REL47 静止门内的确定接缝: 模式与代际必须在**释放静止门之前**一起落定, 门内不得打印。
 * 对应审核反例 NEW|MODE_PUBLISHED_AFTER_STATIC_RELEASE。 */
static int g_hook_rc, g_hook_mode, g_hook_valid, g_hook_ctl_mode, g_hook_gen, g_hook_held;
static size_t g_hook_cap_len;
static void r2_hook_inside_gate(void)
{
    rel_ctl_t c;
    g_hook_rc = (int)rel_prod_get_ctl(&c);
    g_hook_mode = (int)d_mode;
    g_hook_valid = (int)c.mode_valid;
    g_hook_ctl_mode = (int)c.mode;
    g_hook_gen = (int)c.gen_want;
    g_hook_held = (int)d_ctl_lock.held;
    g_hook_cap_len = g_cap_len;
}
static void c_REL47(void)
{
    diag_rprof_t p;
    rt_err_t rc;

    world_reset();
    p = make_profile();
    install_profile(&p);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.safety = SAFETY_READY;
    pump_sensor(2);                            /* 出一帧, 让"源健康"门能判 */
    cap_reset();
    g_static_end_hook = r2_hook_inside_gate;
    diagnosis_take_relative_confirm();
    rc = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(rc == RT_EOK, "健康前置下开启相对模式被拒(rc=%d)", (int)rc);
    /* 门内最后一刻: 模式与代际都已经落定 => 提交发生在释放之前。 */
    CHECK(g_hook_rc == RT_EOK, "接缝里读不到 ctl(rc=%d)", g_hook_rc);
    CHECK(g_hook_mode == (int)DIAG_MODE_RELATIVE_PROTECTION && g_hook_valid == 1 &&
          g_hook_ctl_mode == (int)DIAG_MODE_RELATIVE_PROTECTION,
          "释放静止门时模式还没落定(d_mode=%d ctl=%d valid=%d)",
          g_hook_mode, g_hook_ctl_mode, g_hook_valid);
    CHECK(g_hook_gen == 1, "静止门内代际还没 bump(gen_want=%d)", g_hook_gen);
    CHECK(g_hook_held == 0, "控制临界区跨在静止事务里还没释放(held=%d)", g_hook_held);
    /* 门内一个字节的打印都不许有: 串口/Flash 等待不能在持门时发生。 */
    CHECK(g_static_end_cap_len == g_static_begin_cap_len,
          "持静止门期间打印了 %u 字节", (unsigned)(g_static_end_cap_len - g_static_begin_cap_len));
    CHECK(cap_has("armed") || cap_has("RELATIVE"), "释放之后没有披露(打印被留在了门内?)");

    /* 拒绝路径: 静止门被占 => 模式与代际都不动, 首因视图不变, 且没有半份提交。 */
    world_reset();
    install_profile(&p);
    rc = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);   /* 没给凭据: 入口就拒 */
    CHECK(rc != RT_EOK && d_mode == DIAG_MODE_MONITOR_ONLY && d_ctl.gen_want == 0u &&
          d_ctl.mode == (rt_uint8_t)DIAG_MODE_MONITOR_ONLY,
          "无凭据却改了状态(rc=%d mode=%d gen_want=%u)", (int)rc, (int)d_mode,
          (unsigned)d_ctl.gen_want);
    diagnosis_take_relative_confirm();
    g_static_begin_busy = 1;                   /* 门拿不到 */
    rc = diagnosis_set_mode(DIAG_MODE_RELATIVE_PROTECTION);
    CHECK(rc != RT_EOK && d_mode == DIAG_MODE_MONITOR_ONLY && d_ctl.gen_want == 0u,
          "静止门被占仍然提交了模式或代际(rc=%d mode=%d gen_want=%u)",
          (int)rc, (int)d_mode, (unsigned)d_ctl.gen_want);
    CHECK(d_ctl.mode_valid == RT_TRUE && d_ctl.mode == (rt_uint8_t)DIAG_MODE_MONITOR_ONLY,
          "被拒后提交态被改成 %u", (unsigned)d_ctl.mode);
    g_static_begin_busy = 0;

    /* 锁失败路径: 与静止门被占同一道最终门 —— 关闭/切换/开启走同一个提交协议。 */
    world_reset();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    diagnosis_take_relative_confirm();
    g_mutex_take_fail = 1;
    rc = diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    g_mutex_take_fail = 0;
    CHECK(rc != RT_EOK, "控制锁失败时关保护竟然成功(rc=%d)", (int)rc);
    CHECK(d_mode == DIAG_MODE_RELATIVE_PROTECTION,
          "提交失败却先把 d_mode 挪走了(mode=%d)", (int)d_mode);
    CHECK(d_ctl.mode == (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION,
          "提交失败但提交态被改成 %u", (unsigned)d_ctl.mode);
    CHECK(g_static_begin_calls == 1 && g_static_end_calls == 1,
          "拒绝路径的门不配对(begin=%d end=%d)", g_static_begin_calls, g_static_end_calls);
}

/* REL48 新一代必须清掉上一代的全部判据证据(审核反例 REARM_REUSES_OLD_ENGINE_EVIDENCE):
 * 旧证据不足以让新一代的第一帧立刻触发; 只有新的一整段持续异常才允许再保护。 */
static void c_REL48(void)
{
    diag_rprof_t p;
    rel_incident_t inc;
    rel_latch_t l;

    world_reset();
    p = make_profile();
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_MONITOR_ONLY);
    S.sg = (rt_uint16_t)(p.run_sg_min - p.sg_drop_trip - 1);
    pump(60);                                  /* MONITOR 下攒出 600ms 级严重证据 */
    CHECK(g_post_calls == 0, "MONITOR 下就保护了(post=%d)", g_post_calls);
    CHECK(d_rel_prod.sg_strong_run_ms >= 500u,
          "前置证据没攒起来(strong=%u)", (unsigned)d_rel_prod.sg_strong_run_ms);

    /* 武装 = 同一静止事务里的"模式 + 新一代"提交: 代际一 bump, 旧证据必须整份作废。 */
    if (rel_prod_commit((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION, RT_TRUE, RT_NULL) != RT_EOK)
    { g_pre_bad++; }
    d_mode = DIAG_MODE_RELATIVE_PROTECTION;
    pump(2);                                   /* 新一代的头两帧(20ms) */
    CHECK(g_post_calls == 0, "新一代第一帧就用了旧证据(post=%d)", g_post_calls);
    CHECK(d_rel_prod.sg_strong_run_ms < 300u,
          "新代际里旧判据证据没清零(strong=%u)", (unsigned)d_rel_prod.sg_strong_run_ms);
    CHECK(d_rel_prod.gen == 1u, "引擎还停在旧代际(gen=%u)", (unsigned)d_rel_prod.gen);

    pump(80);                                  /* 新一代自己攒够一整段 */
    CHECK(g_post_calls == 1, "新一段持续异常没触发(post=%d)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_SG_SEVERE, "首因不是 SG 严重");
    CHECK(rel_prod_get_incident(&inc) == RT_EOK && inc.generation == 1u &&
          inc.rearm_required == 1u, "事故没绑到新一代(gen=%u rearm=%d)",
          (unsigned)inc.generation, (int)inc.rearm_required);

    /* 再走一次武装: 旧首因挪进归档、当前首因清空, 新一代不能把旧事故当成本轮的证据。 */
    if (rel_prod_request_rearm() != RT_EOK) { g_pre_bad++; }
    d_mode = DIAG_MODE_RELATIVE_PROTECTION;
    pump(2);
    CHECK(rel_prod_get_incident(&inc) == RT_EOK && inc.have_current == RT_FALSE &&
          inc.have_archived == RT_TRUE && inc.generation == 2u,
          "换代后事故视图不对(cur=%d arch=%d gen=%u)", (int)inc.have_current,
          (int)inc.have_archived, (unsigned)inc.generation);
    CHECK(d_rel_prod.latch.latched == 0u && d_rel_prod.rearm_required == 0u,
          "新一代仍然粘着旧票据(latched=%d rearm=%d)",
          (int)d_rel_prod.latch.latched, (int)d_rel_prod.rearm_required);
}

/* REL49 新帧 != 新运动事实(R2-B3, 审核反例 FRAME_FALSE_MOTION_KNOWN):
 * 外壳帧一直新, 但运动快照取不到/过期时, 缓存的 IDLE 不是"证得静止"。 */
static void c_REL49(void)
{
    diag_rprof_t p;
    rel_engine_t e;
    struct feed f;
    rel_latch_t l;
    rel_state_t s;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);                     /* 引擎级: 健康对照 */
    for (i = 1; i <= 60; ++i) r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
    CHECK(e.coverage == RELCOV_OK && r == REL_REASON_NONE,
          "健康对照都不覆盖完好(cov=%u reason=%s)", (unsigned)e.coverage, rel_reason_name(r));

    f.a.in.motion_known = 0;                   /* 帧照旧前进, 运动事实未知 */
    r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
    CHECK(e.coverage == RELCOV_MOTION_UNKNOWN,
          "运动事实未知却仍算覆盖完好(cov=%u)", (unsigned)e.coverage);
    for (i = 1; i <= 60; ++i) r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
    CHECK(r == REL_COVERAGE_LOST, "持续运动未知没降险(reason=%s)", rel_reason_name(r));

    world_reset();                             /* 过期支: motion_ok=1 但 tick 老 */
    feed_init(&e, &p, &f);
    for (i = 1; i <= 30; ++i) r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
    CHECK(e.coverage == RELCOV_OK, "前置没进入覆盖完好(cov=%u)", (unsigned)e.coverage);
    /* 冻住运动身份, 再把 tick 显式改老一份以上(生产者的 known+老 tick 组合只能这样造出来) */
    f.motion_freeze = 1;
    f.a.in.motion_tick = f.t - (p.age_frame_ms / 10u + 5u) * 10u;
    r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
    CHECK(e.coverage == RELCOV_MOTION_UNKNOWN,
          "运动快照过期却算新鲜(cov=%u age_lim=%u)", (unsigned)e.coverage,
          (unsigned)p.age_frame_ms);
    f.motion_freeze = 0;
    r = feed_step(&f, 500, 2000, 1, 1, 1, 1);
    CHECK(e.coverage == RELCOV_OK,
          "运动快照重新新鲜后档位没回来(cov=%u)", (unsigned)e.coverage);

    world_reset();                             /* 生产入口: 帧一直出, 快照取不到 */
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;   /* 缓存会一直说"静止" */
    g_snap_fail = 1;
    pump(80);
    diag_relative_status_lines();              /* 档位名与预算一起验(短行是现场唯一可见证据) */
    g_snap_fail = 0;
    CHECK(g_post_calls == 1,
          "运动事实取不到却靠缓存 IDLE 免保护(post=%d)", g_post_calls);
    CHECK(rel_prod_get_latch(&l) && l.reason == REL_COVERAGE_LOST &&
          l.coverage_at_trip == RELCOV_MOTION_UNKNOWN,
          "降险身份不对(reason=%s cov=%u)", rel_reason_name(l.reason),
          (unsigned)l.coverage_at_trip);
    CHECK(rel_prod_get_snapshot(&s) == RT_EOK && s.coverage == RELCOV_MOTION_UNKNOWN,
          "快照没披露运动未知档位(rc=%d cov=%u)", (int)rel_prod_get_snapshot(&s),
          (unsigned)s.coverage);
    CHECK(cap_has("cov=MOTION-UNKNOWN"), "未知档位没有具名原因名");
    CHECK(cap_max_line_len() <= 110, "运动未知一档最长 %d 字节(预算 110)",
          cap_max_line_len());

    world_reset();                             /* 对照: 快照正常 => 缓存 IDLE 是真静止 */
    install_profile(&p);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    pump(120);
    CHECK(g_post_calls == 0, "运动快照正常时误降险(post=%d)", g_post_calls);
}

/* REL50 过期的"静止"事实不是现在的静止豁免(R3-A, 审核反例 STALE_IDLE_EXEMPTS_FOREVER)。
 * 覆盖丢失分支的豁免只认**当前可信**的静止: 运动快照过期、事实未知、或另一覆盖门先失败时,
 * 帧里继承来的 IDLE 一律不豁免; profile 年龄无效/缺失时按既有帧设计值 100 ms 判新鲜度。 */
static void c_REL50(void)
{
    diag_rprof_t p;
    rel_engine_t e;
    struct feed f;
    rel_reason_t r = REL_REASON_NONE;
    int i;

    /* a) 审核包同输入第一条: 帧/SG/ADC 继续更新, 运动快照冻在 1200 ms 前, 事实说 IDLE */
    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    f.motion_freeze = 1;
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_COVERAGE_LOST && e.coverage == RELCOV_MOTION_UNKNOWN &&
          e.cov_lost_ms == 1100u && (f.a.now_tick - f.a.in.motion_tick) == 1200u,
          "过期 IDLE 仍被当成现在的静止(reason=%s cov=%u lost=%u age=%u)",
          rel_reason_name(r), (unsigned)e.coverage, (unsigned)e.cov_lost_ms,
          (unsigned)(f.a.now_tick - f.a.in.motion_tick));

    /* b) 同输入的对照: 事实说 CRUISE 时同样降险(豁免只由"当前可信静止"决定, 不由 IDLE 名字决定) */
    world_reset();
    feed_init(&e, &p, &f);
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    f.motion_freeze = 1;
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_COVERAGE_LOST && e.coverage == RELCOV_MOTION_UNKNOWN &&
          e.cov_lost_ms == 1100u,
          "CRUISE 对照没给出同一档位降险(reason=%s cov=%u lost=%u)",
          rel_reason_name(r), (unsigned)e.coverage, (unsigned)e.cov_lost_ms);

    /* c) 新鲜 IDLE 不误停: 运动快照每帧都成功, 静止是现在证得的 */
    world_reset();
    feed_init(&e, &p, &f);
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_REASON_NONE && e.coverage == RELCOV_OK && e.cov_lost_ms == 0u,
          "真静止被误降险(reason=%s cov=%u lost=%u)",
          rel_reason_name(r), (unsigned)e.coverage, (unsigned)e.cov_lost_ms);

    /* d) motion_known=0 的 IDLE: 取不到事实就不知道在不在动, 不豁免(REL49 的静止侧) */
    world_reset();
    feed_init(&e, &p, &f);
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    f.a.in.motion_known = 0;
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_COVERAGE_LOST && e.coverage == RELCOV_MOTION_UNKNOWN,
          "运动事实未知却靠 IDLE 免降险(reason=%s cov=%u)",
          rel_reason_name(r), (unsigned)e.coverage);

    /* e) profile 年龄无效(5000 ms 超出 20..1000)时按既有帧设计值 100 ms 判新鲜度:
     *    快照 1200 ms 前 => 不可信 => 覆盖先失败(ENGINE_ERR)也要降险。 */
    world_reset();
    p = make_profile();
    p.age_frame_ms = 5000u;
    feed_init(&e, &p, &f);
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    f.motion_freeze = 1;
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(e.coverage == RELCOV_ENGINE_ERR && r == REL_COVERAGE_LOST,
          "无效年龄没退回 100 ms 设计限(cov=%u reason=%s)",
          (unsigned)e.coverage, rel_reason_name(r));

    /* f) 同一条无效 profile, 但运动快照每帧都新 => 现在是真静止, 不误停 */
    world_reset();
    feed_init(&e, &p, &f);
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_REASON_NONE && e.coverage == RELCOV_ENGINE_ERR,
          "无效 profile 下新鲜静止被误停(reason=%s cov=%u)",
          rel_reason_name(r), (unsigned)e.coverage);

    /* g) 另一覆盖门先失败(调用方给 LOCK_FAIL)时, 过期 IDLE 同样不豁免:
     *    新鲜度判断排在其他门之前, 不被它们的短路绕过。 */
    world_reset();
    p = make_profile();
    feed_init(&e, &p, &f);
    f.a.coverage = RELCOV_LOCK_FAIL;
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    f.motion_freeze = 1;
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(e.coverage == RELCOV_LOCK_FAIL && r == REL_COVERAGE_LOST,
          "锁失败档里过期 IDLE 免了降险(cov=%u reason=%s)",
          (unsigned)e.coverage, rel_reason_name(r));

    /* h) 同一条 LOCK_FAIL 档, 运动快照新鲜 => 豁免成立(证明上面不是靠删掉豁免变绿的) */
    world_reset();
    feed_init(&e, &p, &f);
    f.a.coverage = RELCOV_LOCK_FAIL;
    f.a.in.motor_state = (rt_uint8_t)MOTOR_IDLE;
    f.a.in.step_hz = 0;
    feed_step(&f, 500, 2000, 1, 1, 1, 1);
    for (i = 1; i <= 120; ++i) r = feed_step(&f, 500, 2000, (i % 10) == 0, 1, 1, 1);
    CHECK(r == REL_REASON_NONE && e.coverage == RELCOV_LOCK_FAIL,
          "锁失败档里新鲜静止没保持豁免(reason=%s cov=%u)",
          rel_reason_name(r), (unsigned)e.coverage);
}

/* ======================= main ======================= */

struct case_ent { const char *id; void (*fn)(void); };
/* ======================= M8-R7D1 新验收组 A01..A12 =======================
 *
 * 这一组判的是**异步合同本身**: 受理不等待、后台按拍推进、取消与完成的先后可线性化、
 * 陈旧 id 影响不了新任务、超时不缩短、互斥在副作用之前、打印有界。
 * 全部在离线协作式调度与虚拟 tick 模型里执行真实产品 C —— 据此不能宣称板端抢占
 * 并发、真实 UART 响应时间或物理保护已验证(motor 在本链是桩, 见 rel_main.c 头部)。
 */

static void cmd_show(void *p)         { char *av[] = { "diag_profile", "show" }; (void)p; diag_profile(2, av); }
static void cmd_learn_bad_n(void *p)  { char *av[] = { "diag_profile", "learn", "7", "450", "550", "0" }; (void)p; diag_profile(6, av); }
static void cmd_learn_inv_win(void *p){ char *av[] = { "diag_profile", "learn", "8", "600", "500", "0" }; (void)p; diag_profile(6, av); }
static void cmd_learn_dir3(void *p)   { char *av[] = { "diag_profile", "learn", "8", "450", "550", "3" }; (void)p; diag_profile(6, av); }
static void cmd_learn_max(void *p)    { char *av[] = { "diag_profile", "learn", "32", "20000", "20000", "1" }; (void)p; diag_profile(6, av); }
static void cmd_cancel_bad(void *p)   { char *av[] = { "diag_profile", "learn_cancel", "abc" }; (void)p; diag_profile(3, av); }
static void cmd_tier2(void *p)        { char *av[] = { "diag_profile", "tier", "2" }; (void)p; diag_profile(3, av); }

/* 前置到"零点已成立 + 巡航在窗内", 但**不**受理学习。 */
static void prep_zero_cruise(void)
{
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    if (d_pending_zero != RT_TRUE) { g_pre_bad++; printf("PRE|zero_prep_failed\n"); }
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
}

/* A01 受理立即返回: 不消耗等待、不推进时钟, STARTED 不等于 learn OK */
static void c_A01(void)
{
    rel_job_view_t v;
    rt_tick_t t0;

    prep_zero_cruise();
    cap_reset();
    g_mdelay_calls = 0;
    t0 = g_now;
    do_cmd(cmd_learn);
    CHECK(cap_has("learn STARTED id=1 n=8 win=450..550 dir=0"),
          "受理后没有 STARTED/任务号/参数行: [%.120s]", cap_text());
    CHECK(!cap_has("job thr tier=") && !cap_has("job DONE"),
          "受理路径就打出了完成态候选/阈值行: [%.120s]", cap_text());
    CHECK(cap_has("NOT learn OK yet"), "受理行没有披露'还不算成功': [%.90s]", cap_text());
    CHECK(cap_has("FIELD_UNVERIFIED"), "受理行丢了 FIELD_UNVERIFIED 口径");
    CHECK(g_mdelay_calls == 0, "受理里发生了 %ld 次等待(异步合同要求 0)", g_mdelay_calls);
    CHECK(g_now == t0, "受理把虚拟时钟推了 %u tick(说明在等采样)", (unsigned)(g_now - t0));
    CHECK(d_pending_learn == RT_FALSE, "受理却置了 learn 凭据");
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_RUNNING && v.job_id == 1u && v.want == 8u,
          "受理后 job_id=%u state=%s want=%u", (unsigned)v.job_id,
          rel_job_state_name(v.state), (unsigned)v.want);
    CHECK(d_cap.active == 1u && d_cap_owner == (rt_uint8_t)REL_CAP_BY_JOB,
          "采集器没被任务接管(active=%u owner=%u)", (unsigned)d_cap.active,
          (unsigned)d_cap_owner);
    CHECK(v.left_ms == (rt_int32_t)REL_CMD_DEADLINE_MS, "余量=%d(期望 %u ms)",
          (int)v.left_ms, (unsigned)REL_CMD_DEADLINE_MS);

    /* 同一 shell 随后就能受理别的命令(不被 12s 等待堵住)。
     * 把世界摆回静止, 好让 zero 走到"采集器被占"这一支, 而不是先被静止门拒掉。
     * 这一段不泵 diag 节拍, 所以任务仍停在 RUNNING —— 判的正是活动期间的行为。 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_learn_status);
    CHECK(cap_has("st=RUNNING"), "学习期间 learn_status 没答复: [%.90s]", cap_text());
    cap_reset();
    do_cmd(cmd_zero);
    CHECK(cap_has("zero REFUSED: capture busy"),
          "zero 没在副作用之前 BUSY 拒绝: [%.90s]", cap_text());
    CHECK(d_pending_zero == RT_TRUE, "被拒绝的 zero 却清了零点凭据");
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED: learn job or capture active"),
          "commit 没被 BUSY 门挡住: [%.90s]", cap_text());
    CHECK(g_mdelay_calls == 0, "拒绝路径发生了等待(%ld)", g_mdelay_calls);
}

/* A02 只有独立样本攒够才发布整份候选, 原口径(样本数/出处/签名/阈值/FIELD_UNVERIFIED)保持 */
static void c_A02(void)
{
    rel_job_view_t v;

    prep_zero_cruise();
    CHECK(learn_run(400) == (int)REL_JOB_DONE, "学习没 DONE: st=%d", (int)job_snap().state);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_DONE && v.cand_valid == 1u,
          "DONE 却没读到整份候选(cand_valid=%u)", (unsigned)v.cand_valid);
    CHECK(v.got_adc >= 8u && v.got_sg >= 8u, "独立样本数 adc=%u sg=%u",
          (unsigned)v.got_adc, (unsigned)v.got_sg);
    /* SG 比 ADC 慢一个数量级: 轮询次数/外壳帧数不可能把 SG 凑够, 这条把"凑数"钉死 */
    CHECK(v.got_sg <= v.got_adc, "SG 样本数(%u)超过 ADC(%u)与采样比矛盾",
          (unsigned)v.got_sg, (unsigned)v.got_adc);
    CHECK(v.cand.run_adc_n == v.got_adc && v.cand.run_sg_n == v.got_sg,
          "发布候选里的样本数(%u/%u)与任务进度(%u/%u)不一致",
          (unsigned)v.cand.run_adc_n, (unsigned)v.cand.run_sg_n,
          (unsigned)v.got_adc, (unsigned)v.got_sg);
    CHECK(v.cand.learn_sg_seq_first != 0u &&
          v.cand.learn_sg_seq_last >= v.cand.learn_sg_seq_first,
          "出处区间不成立(%u..%u)", (unsigned)v.cand.learn_sg_seq_first,
          (unsigned)v.cand.learn_sg_seq_last);
    CHECK(v.cand.magic == DIAG_RPROF_MAGIC && v.cand.abi == DIAG_RPROF_ABI,
          "发布的候选没带 magic/abi");
    CHECK(v.cand.signature == rel_live_signature(), "候选签名与 live 身份脱钩");
    CHECK(v.cand.threshold_tier >= 1u && v.cand.threshold_tier <= 3u,
          "档位越界(%u)", (unsigned)v.cand.threshold_tier);
    CHECK(v.cand.sg_drop_warn < v.cand.sg_drop_trip, "SG 层次没张开");
    CHECK((v.cand.flags & RPF_FIELD_VERIFIED) == 0u, "学习产物自称 FIELD_VERIFIED");
    CHECK((v.cand.flags & RPF_COMMITTED) == 0u, "未 commit 的候选自称 COMMITTED");
    CHECK(d_pending_learn == RT_TRUE && d_pending_zero == RT_TRUE, "凭据没随发布更新");
    /* DONE 之后资源必须已经归还 */
    CHECK(d_cap.active == 0u && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "DONE 后采集器/所有权没回收(owner=%u)", (unsigned)d_cap_owner);
    CHECK(v.cap_owner == (rt_uint8_t)REL_CAP_FREE, "快照里的所有权=%u",
          (unsigned)v.cap_owner);
}

/* A03 learn_status 给一致快照, 读状态不改变进度与凭据 */
static void c_A03(void)
{
    rel_job_view_t a, b;
    rt_uint32_t sg_before;

    prep_zero_cruise();
    do_cmd(cmd_learn);
    a = job_snap();
    sg_before = a.got_sg;
    cap_reset();
    do_cmd(cmd_learn_status);
    do_cmd(cmd_learn_status);
    b = job_snap();
    CHECK(cap_count("[DIAG] job id=") == 2, "两次查询应当各打一组状态行: [%.90s]", cap_text());
    CHECK(cap_has("st=RUNNING") && cap_has("owner=2"),
          "活动任务的状态行不完整: [%.120s]", cap_text());
    CHECK(cap_has("cred=1/0"), "状态行的凭据披露不对: [%.120s]", cap_text());
    /* 同一拍上没推进后台: 两份快照必须逐字节相同(不是"看起来差不多") */
    CHECK(memcmp(&a, &b, sizeof(a)) == 0, "同拍两次快照不一致(left=%d/%d 进度=%u/%u)",
          (int)a.left_ms, (int)b.left_ms, (unsigned)a.got_sg, (unsigned)b.got_sg);
    CHECK(d_pending_learn == RT_FALSE && d_pending_zero == RT_TRUE,
          "读状态改了凭据(zero=%d learn=%d)", (int)d_pending_zero, (int)d_pending_learn);
    CHECK(d_cap.sg.n == sg_before, "读状态推进了采集进度(%u->%u)",
          (unsigned)sg_before, (unsigned)d_cap.sg.n);
    CHECK(d_cap.active == 1u, "读状态把采集器弄掉了");
    /* 推进之后进度才动: 证明上面那份"不变"不是因为快照本来就不更新 */
    pump(40);
    b = job_snap();
    CHECK(b.got_sg >= sg_before && b.state == (rt_uint8_t)REL_JOB_RUNNING,
          "推进 40 拍后 state=%s sg=%u", rel_job_state_name(b.state), (unsigned)b.got_sg);
}

/* A04 严格参数与 BUSY 拒绝都发生在副作用之前: 活动任务/已提交 profile 不被改写 */
static void c_A04(void)
{
    diag_rprof_t p, back;
    rel_job_view_t v;

    prep_zero_cruise();
    p = make_profile();
    install_profile(&p);
    project_config_get_rprof(&back);
    CHECK(back.magic == DIAG_RPROF_MAGIC, "前置: 已发布 profile 没装上");

    cap_reset();
    do_cmd(cmd_learn_bad_n);
    CHECK(cap_has("learn args:"), "N=7 没走参数拒绝: [%.90s]", cap_text());
    do_cmd(cmd_learn_inv_win);
    CHECK(cap_has("learn args:"), "LO>HI 没走参数拒绝");
    do_cmd(cmd_learn_dir3);
    CHECK(cap_has("learn args:"), "DIR=3 没走严格参数拒绝");
    CHECK(!job_active() && d_cap_owner == (rt_uint8_t)REL_CAP_FREE &&
          d_cap.active == 0u, "非法参数竟然创建了活动任务或采集器");
    CHECK(d_pending_zero == RT_TRUE && d_pending_learn == RT_FALSE,
          "非法参数动了两级凭据");
    CHECK(d_job_next_id == 1u, "被拒绝的 start 消耗了任务号(%u)", (unsigned)d_job_next_id);
    do_cmd(cmd_cancel_bad);
    CHECK(cap_has("learn_cancel JOB_ID must be"), "非数字 JOB_ID 没被拒");
    CHECK(job_snap().job_id == 0u, "非法 cancel 参数却改了任务状态");

    /* 重复 start: 不覆盖活动任务, 不建第二个采集器 */
    do_cmd(cmd_learn);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_RUNNING && v.job_id == 1u, "前置受理失败");
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("learn REFUSED: capture busy"),
          "重复 start 没被拒: [%.90s]", cap_text());
    {
        rel_job_view_t w = job_snap();
        CHECK(w.job_id == v.job_id && w.want == v.want && w.hz_lo == v.hz_lo &&
              w.hz_hi == v.hz_hi, "重复 start 覆盖了活动任务参数");
        CHECK(d_cap.sg.n == v.got_sg, "重复 start 重置了活动采集器");
    }
    CHECK(d_job_next_id == 2u, "被拒绝的重复 start 又消耗了任务号(%u)",
          (unsigned)d_job_next_id);
    /* 先让这一份任务终结: 后面 clear / 无凭据 / 模式不符三段各自判自己的路径 */
    CHECK(job_wait(400) == (int)REL_JOB_DONE, "前置任务没终结: st=%d",
          (int)job_snap().state);
    /* 没有零点凭据时: 明确拒绝且不建任务。
     * R7D1 返修(ARD3)之后 clear 必须先过静止事务门, 巡航态下它整条被拒、连配置都不碰,
     * 所以 clear 不再能当"只清凭据"的开关用。这里只顺手再证一次拒绝的无害性, 落凭据用
     * 夹具既有写法(本例后面几段同样这么落 d_pending_zero); clear 自己的成功路径由 A15 专判,
     * 免得把"已提交 profile 被这套拒绝/受理序列改动"这件事混进本例的最后一条判据。 */
    cap_reset();
    do_cmd(cmd_clear);
    CHECK(cap_has("clear REFUSED: static txn"), "巡航态的 clear 没被静止门拒绝: [%.90s]",
          cap_text());
    CHECK(d_pending_zero == RT_TRUE && d_pending_learn == RT_TRUE,
          "被拒绝的 clear 动了凭据: zero=%d learn=%d",
          (int)d_pending_zero, (int)d_pending_learn);
    d_pending_zero = RT_FALSE;
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("run 'diag_profile zero N' first"), "无零点凭据时没具名拒绝: [%.90s]",
          cap_text());
    CHECK(!job_active() && d_cap_owner == (rt_uint8_t)REL_CAP_FREE && d_cap.active == 0u,
          "被拒绝的 learn 留下了活动任务或采集器");
    /* 模式不符: 受理之前就拒, 不改已发布 profile */
    d_pending_zero = RT_TRUE;
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("mode must be MONITOR_ONLY"), "模式门没拒: [%.90s]", cap_text());
    CHECK(!job_active() && d_cap_owner == (rt_uint8_t)REL_CAP_FREE && d_cap.active == 0u,
          "模式不符的 learn 仍然受理了");
    arm((rt_uint8_t)DIAG_MODE_MONITOR_ONLY);
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    d_pending_zero = RT_FALSE;
    do_cmd(cmd_zero);                    /* 重新攒一个干净的零点前置 */
    CHECK(d_pending_zero == RT_TRUE, "恢复零点凭据失败: [%.80s]", cap_text());
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    CHECK(learn_run(400) == (int)REL_JOB_DONE, "收尾前置学习没 DONE: st=%d",
          (int)job_snap().state);
    project_config_get_rprof(&back);
    CHECK(back.magic == DIAG_RPROF_MAGIC && back.profile_id == p.profile_id &&
          back.hz_low == p.hz_low && back.sg_drop_trip == p.sg_drop_trip,
          "这一组拒绝/受理把已提交 profile 改了(id=%u lo=%u trip=%u)",
          (unsigned)back.profile_id, (unsigned)back.hz_low, (unsigned)back.sg_drop_trip);
}

/* A05 匹配的 cancel 在一次可执行推进里终结; 没有新帧也能取消; 终结后资源可再用 */
static void c_A05(void)
{
    rel_job_view_t v;
    int st;

    prep_zero_cruise();
    do_cmd(cmd_learn);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_RUNNING, "前置受理失败: st=%s",
          rel_job_state_name(v.state));
    g_stop_sensor = 1;                   /* 从这里起一帧新样本都没有 */
    g_mdelay_calls = 0;
    cap_reset();
    g_cancel_id = v.job_id;
    do_cmd(cmd_learn_cancel);
    CHECK(cap_has("cancel id=1 REQUESTED st=CANCEL_PENDING"),
          "取消的答复不对: [%.120s]", cap_text());
    CHECK(cap_has("motor untouched"), "取消答复没披露它不碰电机");
    CHECK(g_mdelay_calls == 0, "取消答复等了 %ld 次(合同要求不等后台收尾)", g_mdelay_calls);
    CHECK(job_snap().cap_owner == (rt_uint8_t)REL_CAP_BY_JOB,
          "刚答应取消就把采集器放开了");
    st = job_wait(1);                    /* 一次可执行推进 */
    CHECK(st == (int)REL_JOB_CANCELLED, "一次推进后没终结: st=%d", st);
    v = job_snap();
    CHECK(strcmp(v.why, "CANCELLED") == 0, "终结原因=%s", v.why);
    CHECK(d_cap.active == 0u && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "取消后资源没释放");
    CHECK(d_pending_learn == RT_FALSE, "取消的任务置了 learn 凭据");
    /* 终结结果保留: 再查仍然是 CANCELLED */
    cap_reset();
    do_cmd(cmd_learn_status);
    CHECK(cap_has("st=CANCELLED"), "终态没保留: [%.90s]", cap_text());
    /* 资源可再次使用 */
    g_stop_sensor = 0;
    do_cmd(cmd_learn);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_RUNNING && v.job_id == 2u,
          "重开的新任务 id=%u st=%s", (unsigned)v.job_id, rel_job_state_name(v.state));
    CHECK(d_cap.adc.n == 0u && d_cap.sg.n == 0u, "重开的采集器带着上一轮的样本");
}

/* A06 取消与完成的两种交错都线性化: 终态只出现一次 */
static void c_A06(void)
{
    diag_rprof_t saved;
    rel_job_view_t v;
    rt_bool_t saved_learn;

    /* 交错 A: 取消先被受理 => 完成不能再把终态翻成 DONE */
    prep_zero_cruise();
    do_cmd(cmd_learn);
    g_cancel_id = 1u;
    do_cmd(cmd_learn_cancel);
    CHECK(job_wait(400) == (int)REL_JOB_CANCELLED, "A: 终态不是 CANCELLED st=%d",
          (int)job_snap().state);
    pump(60);                            /* 本可以攒够样本的节拍 */
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_CANCELLED, "A: 取消后又变成 %s",
          rel_job_state_name(v.state));
    CHECK(d_pending_learn == RT_FALSE, "A: 取消的任务仍然发布了 learn 凭据");
    CHECK(v.cand_valid == 0u, "A: 取消的任务却给出了已发布候选");

    /* 交错 B: 完成先发生 => 取消必须明说"已终结", 不能删掉完成的候选 */
    prep_zero_cruise();
    do_cmd(cmd_learn);
    CHECK(job_wait(400) == (int)REL_JOB_DONE, "B: 前置完成失败 st=%d", (int)job_snap().state);
    saved_learn = d_pending_learn;
    saved = d_pending;
    cap_reset();
    g_cancel_id = 1u;
    do_cmd(cmd_learn_cancel);
    CHECK(cap_has("NOT-CANCELLED: already DONE"),
          "B: 完成后的取消没承认已终结: [%.120s]", cap_text());
    CHECK(!cap_has("REQUESTED"), "B: 完成后的取消谎称受理成功");
    CHECK(d_pending_learn == saved_learn && memcmp(&d_pending, &saved, sizeof(saved)) == 0,
          "B: 取消删掉了已完成的候选/凭据");
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_DONE && v.cand_valid == 1u, "B: 终态被推翻");
    pump(60);
    CHECK(job_snap().state == (rt_uint8_t)REL_JOB_DONE, "B: 后续推进把终态改了");
}

/* A07 陈旧 job_id 与旧在途推进影响不了新任务; 退出确认之前不复用采集器 */
static void c_A07(void)
{
    rel_job_view_t v;

    prep_zero_cruise();
    do_cmd(cmd_learn);                   /* id=1 */
    g_cancel_id = 1u;
    do_cmd(cmd_learn_cancel);            /* CANCEL_PENDING, 后台还没确认退出 */
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("learn REFUSED: capture busy"),
          "旧推进未确认退出就允许复用采集器: [%.90s]", cap_text());
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_CANCEL_PENDING && v.job_id == 1u,
          "被拒绝的重开把任务搬到了 st=%s id=%u", rel_job_state_name(v.state),
          (unsigned)v.job_id);
    CHECK(job_wait(400) == (int)REL_JOB_CANCELLED, "旧任务没在推进里确认退出");
    do_cmd(cmd_learn);
    v = job_snap();
    CHECK(v.job_id == 2u && v.state == (rt_uint8_t)REL_JOB_RUNNING,
          "新任务 id=%u st=%s", (unsigned)v.job_id, rel_job_state_name(v.state));
    /* 陈旧 id 不得取消新任务 */
    cap_reset();
    g_cancel_id = 1u;
    do_cmd(cmd_learn_cancel);
    CHECK(cap_has("no such job id=1 current=2"),
          "陈旧 id 的答复不含拒绝身份: [%.120s]", cap_text());
    CHECK(job_snap().state == (rt_uint8_t)REL_JOB_RUNNING, "陈旧 job_id 取消了新任务");
    /* 再取消一次陈旧 id 也不得改写终态历史 */
    g_cancel_id = 0u;
    cap_reset();
    do_cmd(cmd_learn_cancel);
    CHECK(cap_has("learn_cancel JOB_ID must be"), "JOB_ID=0 没被严格参数解析拒掉");
    CHECK(job_snap().state == (rt_uint8_t)REL_JOB_RUNNING, "JOB_ID=0 的拒绝却改了任务");
    /* 新任务照常完成, 且从干净的采集器起步 */
    CHECK(d_cap.adc.n == 0u && d_cap.sg.n == 0u, "新任务复用了旧累加器");
    CHECK(job_wait(400) == (int)REL_JOB_DONE, "新任务没能完成 st=%d",
          (int)job_snap().state);
    v = job_snap();
    CHECK(v.cand_valid == 1u && v.got_sg >= 8u && d_pending_learn == RT_TRUE,
          "新任务的发布不完整(sg=%u learn=%d cand=%u)", (unsigned)v.got_sg,
          (int)d_pending_learn, (unsigned)v.cand_valid);
}

/* A08 无帧仍然推进总超时(含 tick 回绕); 超时不发布候选也不缩短预算 */
static void c_A08(void)
{
    rel_job_view_t v;
    rt_tick_t t0;

    CHECK(REL_CMD_DEADLINE_MS == 12000u, "总预算被改成了 %u ms", (unsigned)REL_CMD_DEADLINE_MS);
    CHECK(REL_MS_TO_TICK(REL_CMD_DEADLINE_MS) == 12000u,
          "ms->tick 折出来是 %u(应为 12000)", (unsigned)REL_MS_TO_TICK(REL_CMD_DEADLINE_MS));

    prep_zero_cruise();
    do_cmd(cmd_learn);
    t0 = g_now;
    g_stop_sensor = 1;                   /* 采不到任何新样本 */
    CHECK(job_wait((int)(REL_CMD_DEADLINE_MS / 10u) + 10) == (int)REL_JOB_TIMEOUT,
          "无帧时没走到 TIMEOUT: st=%d", (int)job_snap().state);
    v = job_snap();
    CHECK(strcmp(v.why, "DEADLINE") == 0, "超时原因=%s", v.why);
    CHECK((rt_uint32_t)(g_now - t0) >= REL_CMD_DEADLINE_MS,
          "超时提前了: 只走了 %u ms", (unsigned)(g_now - t0));
    CHECK(v.got_adc <= 1u, "无新帧却攒到 adc=%u(旧帧在凑数)", (unsigned)v.got_adc);
    CHECK(v.cand_valid == 0u && d_pending_learn == RT_FALSE, "超时却发布了候选/凭据");
    CHECK(d_cap.active == 0u && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "超时后资源没释放");

    /* 2b) 真一帧都没出过的世界(与 world_reset 同一形态): 超时仍然必须推进 */
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    CHECK(d_pending_zero == RT_TRUE, "2b) 前置零点没成功: [%.80s]", cap_text());
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    do_cmd(cmd_learn);
    g_stop_sensor = 1;
    memset(&s_frame, 0, sizeof(s_frame)); memset(&s_prev, 0, sizeof(s_prev));
    s_seq = 0;                               /* sensor 服务一帧都没交出来 */
    CHECK(job_wait((int)(REL_CMD_DEADLINE_MS / 10u) + 10) == (int)REL_JOB_TIMEOUT,
          "2b) 从没出过帧时超时没推进: st=%d", (int)job_snap().state);
    CHECK(job_snap().got_adc == 0u, "2b) 无帧世界却攒到样本 adc=%u",
          (unsigned)job_snap().got_adc);

    /* 2c) tick 回绕: 到期判定必须跟着有符号差走, 不能把"还剩一点"当成"已过" */
    g_stop_sensor = 1;
    g_now = 0xFFFFFFF0u;
    do_cmd(cmd_learn);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_RUNNING && v.job_id == 2u,
          "回绕前的受理失败(id=%u st=%s)", (unsigned)v.job_id, rel_job_state_name(v.state));
    CHECK(v.left_ms == (rt_int32_t)REL_CMD_DEADLINE_MS, "回绕前余量=%d", (int)v.left_ms);
    g_now = 5u;                          /* 跨过 0, 仍在预算内 */
    pump(1);
    CHECK(job_snap().state == (rt_uint8_t)REL_JOB_RUNNING,
          "跨回绕把还没到期的任务判成超时了");
    g_now = REL_CMD_DEADLINE_MS + 16u;   /* 已经走过 deadline */
    pump(1);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_TIMEOUT, "跨回绕后到期没被判出: st=%s",
          rel_job_state_name(v.state));
    g_stop_sensor = 0;
}

/* A09 停止/disarm/故障/离窗/方向或签名改变都终结活动任务; 旧帧不补成成功 */
static void c_A09(void)
{
    rel_job_view_t v;

    /* a) 学习中途停机(运动离开巡航) */
    prep_zero_cruise();
    g_break_at_tick = 15;
    g_tick_hook = hook_break_window;
    do_cmd(cmd_learn);
    CHECK(job_wait(400) == (int)REL_JOB_FAILED, "a) 停机没终结任务: st=%d",
          (int)job_snap().state);
    v = job_snap();
    CHECK(strcmp(v.why, "WINDOW_BROKEN") == 0, "a) 原因=%s", v.why);
    CHECK(d_pending_learn == RT_FALSE, "a) 停机后的旧帧补成了成功");
    CHECK(v.got_sg < 8u || v.cand_valid == 0u, "a) 停机之后仍发布了完整候选");
    g_tick_hook = RT_NULL;

    /* b) 方向改变 */
    prep_zero_cruise();
    g_break_at_tick = 15;
    g_tick_hook = hook_flip_dir;
    do_cmd(cmd_learn);
    CHECK(job_wait(400) == (int)REL_JOB_FAILED, "b) 换向没终结任务");
    CHECK(strcmp(job_snap().why, "WINDOW_BROKEN") == 0, "b) 原因=%s", job_snap().why);
    g_tick_hook = RT_NULL;

    /* c) 驱动签名漂移: 一帧新样本都没有, 也必须在下一拍终结 */
    prep_zero_cruise();
    do_cmd(cmd_learn);
    g_stop_sensor = 1;
    S.planned.irun = 27;
    pump(1);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_FAILED && strcmp(v.why, "SIG_CHANGED") == 0,
          "c) 签名漂移没被就地终结: st=%s why=%s", rel_job_state_name(v.state), v.why);
    S.planned.irun = 20;
    g_stop_sensor = 0;

    /* d) 诊断模式被改走: 不允许"边保护边学习" */
    prep_zero_cruise();
    do_cmd(cmd_learn);
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    pump(1);
    v = job_snap();
    CHECK(v.state == (rt_uint8_t)REL_JOB_FAILED && strcmp(v.why, "MODE_CHANGED") == 0,
          "d) 模式改了却没终结: st=%s why=%s", rel_job_state_name(v.state), v.why);
    arm((rt_uint8_t)DIAG_MODE_MONITOR_ONLY);

    /* e) 同一 shell 不需要先等学习: 停止/禁能命令的入口在本链是桩, 这里能证的是
     *    "受理后 shell 立刻可继续受理命令", 真实 motor_stop/motor_disarm 行为
     *    不由本用例宣称(见交付的边界登记)。 */
    prep_zero_cruise();
    g_mdelay_calls = 0;
    do_cmd(cmd_learn);
    g_cancel_id = job_snap().job_id;
    do_cmd(cmd_learn_status);
    do_cmd(cmd_learn_cancel);
    CHECK(g_mdelay_calls == 0, "受理+查询+取消合计等待 %ld 次", g_mdelay_calls);
    CHECK(job_wait(1) == (int)REL_JOB_CANCELLED, "取消没在一次推进内收尾");
}

/* A10 失败重学/取消/超时都不复用旧 learn 资格; 完整成功后的 commit 仍走原静止事务 */
static void c_A10(void)
{
    diag_rprof_t saved, back;

    /* 1) 完整成功 => 显式 commit(静止事务门内) */
    prep_zero_cruise();
    CHECK(learn_run(400) == (int)REL_JOB_DONE, "1) 前置学习没成功");
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit OK"), "1) 完整成功之后 commit 没走通: [%.90s]", cap_text());
    project_config_get_rprof(&back);
    CHECK(back.magic == DIAG_RPROF_MAGIC && (back.flags & RPF_COMMITTED) != 0u,
          "1) commit 没发布整套");
    CHECK(g_static_begin_calls == g_static_end_calls && g_static_begin_calls >= 1,
          "1) 静止事务门不配对(%d/%d)", g_static_begin_calls, g_static_end_calls);

    /* 1b) 已经有 learn 资格时, 一条**被拒绝**的重学不能先烧掉它: 作废只发生在受理成立时 */
    arm((rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("mode must be MONITOR_ONLY"), "1b) 模式门没拒: [%.90s]", cap_text());
    CHECK(d_pending_learn == RT_TRUE, "1b) 被拒绝的重学先把旧 learn 资格烧掉了");
    CHECK(!job_active() && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "1b) 被拒绝的重学却建了任务或占了采集器");
    arm((rt_uint8_t)DIAG_MODE_MONITOR_ONLY);

    /* 1c) 受理**成立**那一刻就得作废旧 learn 资格(§5.2): 任务还在跑的时候不得留着
     *     上一轮的资格, 否则"重学失败 + 旧资格"就能提交一个没人学过的新窗口。 */
    do_cmd(cmd_learn);
    CHECK(job_active(), "1c) 受理没建任务: st=%s", rel_job_state_name(job_snap().state));
    CHECK(d_pending_learn == RT_FALSE, "1c) 受理新学习之后旧 learn 资格还留着");
    job_wait(400);

    /* 2) 失败重学: 旧 learn 资格作废, 不能据此提交新窗口 */
    saved = back;
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000;
    pump_sensor(2);
    do_cmd(cmd_learn_far_window);
    CHECK(job_wait(400) == (int)REL_JOB_FAILED, "2) 离窗重学没失败");
    CHECK(d_pending_learn == RT_FALSE, "2) 失败重学保留了旧 learn 资格");
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED"), "2) 失败重学之后竟然提交了: [%.90s]", cap_text());
    project_config_get_rprof(&back);
    CHECK(back.profile_id == saved.profile_id && back.hz_low == saved.hz_low &&
          back.sg_drop_trip == saved.sg_drop_trip, "2) 失败的尝试改了已提交 profile");

    /* 3) 取消与超时同样不能翻出 learn 资格 */
    prep_zero_cruise();
    do_cmd(cmd_learn);
    g_cancel_id = job_snap().job_id;
    do_cmd(cmd_learn_cancel);
    job_wait(2);
    CHECK(d_pending_learn == RT_FALSE, "3) 取消后 learn 凭据却是活的");
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED: pending incomplete"), "3) 取消后 commit 没按缺凭据拒绝");
    g_stop_sensor = 1;
    do_cmd(cmd_learn);
    CHECK(job_wait((int)(REL_CMD_DEADLINE_MS / 10u) + 10) == (int)REL_JOB_TIMEOUT,
          "3) 超时前置失败");
    CHECK(d_pending_learn == RT_FALSE, "3) 超时后 learn 凭据却是活的");
    cap_reset();
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED: pending incomplete"), "3) 超时后 commit 没按缺凭据拒绝");
    g_stop_sensor = 0;
}

/* A11 活动学习与 zero/tier/commit/clear 互斥; 锁失败/未就绪不出现半写与错误解锁 */
static void c_A11(void)
{
    diag_rprof_t p;

    /* 1) 互斥: 四个改写入口都在副作用之前拒绝 */
    prep_zero_cruise();
    do_cmd(cmd_learn);
    /* 把世界摆回静止, 好让 zero 走到"采集器被占"这一支而不是先被静止门拒掉。
     * 这一段不泵 diag 节拍, 所以任务仍然停在 RUNNING —— 判的正是活动期间的互斥。 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset(); do_cmd(cmd_zero);
    CHECK(cap_has("zero REFUSED: capture busy"), "zero 没被互斥门挡住: [%.90s]", cap_text());
    CHECK(d_pending_zero == RT_TRUE, "1) 被拒的 zero 清了零点凭据");
    cap_reset(); do_cmd(cmd_tier2);
    CHECK(cap_has("tier REFUSED: learn job or capture active"),
          "tier 没被互斥门挡住: [%.90s]", cap_text());
    cap_reset(); do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED: learn job or capture active"),
          "commit 没被互斥门挡住: [%.90s]", cap_text());
    cap_reset(); do_cmd(cmd_clear);
    CHECK(cap_has("clear REFUSED: learn job or capture active"),
          "clear 没被互斥门挡住: [%.90s]", cap_text());
    CHECK(d_pending_zero == RT_TRUE && d_pending_learn == RT_FALSE,
          "1) 互斥拒绝之后凭据被改动(zero=%d learn=%d)",
          (int)d_pending_zero, (int)d_pending_learn);
    project_config_get_rprof(&p);
    CHECK(p.magic == 0u, "1) 被拒的 clear 竟然作废了 profile");
    /* show 与 learn_status 读到的凭据必须来自同一份状态 */
    cap_reset();
    do_cmd(cmd_show);
    do_cmd(cmd_learn_status);
    CHECK(cap_has("cred zero=1 learn=0"), "1) show 的凭据快照不对: [%.120s]", cap_text());
    CHECK(cap_has("cred=1/0"), "1) learn_status 的凭据快照与 show 不一致: [%.120s]",
          cap_text());

    /* 让 1) 的任务先终结(静止世界里走到 WINDOW_BROKEN), 再单独判锁失败支路 */
    job_wait(400);
    d_pending_zero = RT_TRUE; d_pending_learn = RT_FALSE;
    g_mutex_take_fail = 1;
    g_mutex_release_calls = 0;
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("learn REFUSED: ctx lock"), "2) 锁失败时 learn 没具名拒绝: [%.90s]", cap_text());
    do_cmd(cmd_zero);
    CHECK(cap_has("zero REFUSED: ctx lock (nothing cleared)"),
          "2) 锁失败时 zero 的拒绝身份不对: [%.90s]", cap_text());
    CHECK(d_pending_zero == RT_TRUE && d_pending_learn == RT_FALSE,
          "2) 锁失败却动了凭据(zero=%d learn=%d)", (int)d_pending_zero, (int)d_pending_learn);
    CHECK(d_cap.active == 0u && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "2) 锁失败却拿到了采集器");
    do_cmd(cmd_tier2);
    CHECK(cap_has("tier REFUSED: ctx lock"), "2) tier 的锁失败身份不对: [%.90s]", cap_text());
    do_cmd(cmd_commit);
    CHECK(cap_has("commit REFUSED: ctx lock"), "2) commit 的锁失败身份不对: [%.90s]", cap_text());
    do_cmd(cmd_clear);
    CHECK(cap_has("clear REFUSED: ctx lock"), "2) clear 的锁失败身份不对: [%.90s]", cap_text());
    cap_reset();
    do_cmd(cmd_learn_status);
    CHECK(cap_has("learn_status REFUSED: ctx lock (no half snapshot)"),
          "2) 状态查询没拒绝");
    CHECK(job_snap().why != RT_NULL && job_snap().job_id == 0u,
          "2) 锁失败时快照被拼出半份");
    g_cancel_id = 1u;
    cap_reset();
    do_cmd(cmd_learn_cancel);
    CHECK(cap_has("cancel REFUSED: ctx lock (nothing changed)"), "2) 取消没拒绝");
    CHECK(g_mutex_release_calls == 0, "2) 拒绝路径释放了没持有的锁(%d)",
          g_mutex_release_calls);
    g_mutex_take_fail = 0;

    /* 3) 后台未就绪: 受理/查询/取消各自给出"未初始化"的身份, 且都不建任务 */
    d_ctx_inited = RT_FALSE;
    cap_reset();
    do_cmd(cmd_learn);
    CHECK(cap_has("learn REFUSED: ctx not ready"), "3) 未就绪时 learn 没说未就绪: [%.90s]",
          cap_text());
    do_cmd(cmd_zero);
    CHECK(cap_has("zero REFUSED: ctx not ready (nothing cleared)"),
          "3) 未就绪时 zero 的拒绝身份不对");
    do_cmd(cmd_tier2);
    CHECK(cap_has("tier REFUSED: ctx not ready"), "3) 未就绪时 tier 没说未就绪");
    cap_reset();
    do_cmd(cmd_learn_status);
    CHECK(cap_has("learn_status REFUSED: ctx not ready"), "3) 未就绪时状态查询身份不对");
    cap_reset();
    g_cancel_id = 1u;
    do_cmd(cmd_learn_cancel);
    CHECK(cap_has("cancel REFUSED: job ctx not initialized"), "3) 未就绪时取消身份不对");
    CHECK(d_job.state != (rt_uint8_t)REL_JOB_RUNNING && d_cap.active == 0u &&
          d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "3) 未就绪路径留下了活动任务或采集器(st=%u owner=%u)",
          (unsigned)d_job.state, (unsigned)d_cap_owner);
    d_ctx_inited = RT_TRUE;

    /* 4) 非法上下文仍然整体静默(R3.7 保持): 学习层不得成为第二条打印路径 */
    prep_zero_cruise();
    cap_reset();
    g_isr_nest = 1;
    do_cmd(cmd_learn);
    g_isr_nest = 0;
    CHECK(cap_text()[0] == 0, "4) ISR 上下文里学习命令打印了: [%.80s]", cap_text());
    CHECK(job_snap().job_id == 0u && d_cap_owner == (rt_uint8_t)REL_CAP_FREE,
          "4) 非法上下文却受理了任务");
}

/* A12 本轮新增/改动的打印逐调用限长 120B(含 LF), 且无裸 NUL、UTF-8 完整 */
static void c_A12(void)
{
    static const char *needles[] = {
        "learn STARTED id=999999999 n=32 win=20000..20000 dir=1",
        "learn async: query",
        "learn REFUSED: capture busy (job or zero owns it)",
        "learn REFUSED: ctx lock",
        "learn REFUSED: ctx not ready",
        "learn args: N=8..32",
        "job id=999999999 st=CANCEL_PENDING why=CANCEL_REQUESTED owner=2 cred=1/1",
        "job zero-cleanup DEFERRED: diag thread will free owner=1",
        "job n=4294967295/4294967295 want=32 win=20000..20000 dir=1",
        "job left=2000000000ms of 12000ms software budget",
        "job data adc[65535..65535]n65535 nz65535 sg[65535..65535]n65535 seq4294967295..4294967295",
        "job thr tier=3 dev=-32768/-32768 drop=65535/65535 FIELD_UNVERIFIED",
        "job DONE result; cred zero=1 learn=1 commit is separate",
        "learn_status REFUSED: ctx lock (no half snapshot)",
        "learn_status REFUSED: ctx not ready (no half snapshot)",
        "cancel id=999999999 REQUESTED st=CANCEL_PENDING (motor untouched)",
        "cancel id=999999999 NOT-CANCELLED: already CANCELLED why=CANCELLED",
        "cancel REFUSED: no such job id=999999999 current=1",
        "cancel REFUSED: job ctx not initialized",
        "zero REFUSED: capture busy (job or zero owns it)",
        "zero FAILED: ctx lock on invalidate",
        "zero REFUSED: ctx not ready (nothing cleared)",
        "tier REFUSED: ctx lock",
        "tier REFUSED: ctx not ready",
        "commit REFUSED: ctx lock",
        "clear REFUSED: ctx lock (profile kept)",
        "clear FAILED rc=",
        "show REFUSED: ctx lock (no half snapshot)",
        "learn_status REFUSED: ctx not ready (no half snapshot)",
        "learn_cancel JOB_ID must be 1..999999999",
        "usage: diag_profile show|zero N|learn N LO HI DIR|tier T|commit|clear CONFIRM|learn_status|learn_cancel ID",
        NULL
    };
    int k, miss = 0, mx;

    /* 同一段捕获贯穿全部分支: cap_max_line_len() 取的是**累计**最长行, 针也对针
     * 只在最后扯一次, 避免"每段各自看一眼"漏掉跨段的长行。 */
    world_reset();
    cap_reset();

    /* 1) 受理 / 重复受理 / 参数拒绝 / usage: 参数与 id 都推到合法上界 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    CHECK(d_pending_zero == RT_TRUE, "1) 前置零点没成功: [%.80s]", cap_text());
    S.mstate = MOTOR_CRUISE; S.armed = 1; S.mhz = 500; S.raw = 2000; S.sg = 500;
    pump_sensor(2);
    d_job_next_id = 999999999u;                 /* CLI 十进制解析器能输入的最大 id */
    do_cmd(cmd_learn_max);                      /* STARTED + async 披露行 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    do_cmd(cmd_learn);                          /* learn 的 busy 拒绝行 */
    do_cmd(cmd_zero);                           /* zero 的 busy 拒绝行 */
    do_cmd(cmd_learn_bad_n);                    /* 参数行 */
    {
        char *av[] = { "diag_profile", "bogus" };
        diag_profile(2, av);                    /* usage 行(本轮扩了两个子命令) */
    }

    /* 2) 取消的三种答复 + learn_status 的全部分支 */
    g_cancel_id = 999999999u;
    do_cmd(cmd_learn_cancel);                   /* REQUESTED st=CANCEL_PENDING */
    /* 把快照字段推到**声明类型**的十进制上界: 只为长度界限, 不声称这些运行值可达 */
    d_job.got_adc = 4294967295u; d_job.got_sg = 4294967295u;
    d_job.want = 32u; d_job.dir = 1u;
    d_job.hz_lo = 20000u; d_job.hz_hi = 20000u;
    d_job.why = "CANCEL_REQUESTED";             /* 最长的活动态原因 */
    d_job.deadline = g_now + 2000000000u;
    d_cap_owner = (rt_uint8_t)REL_CAP_BY_JOB;
    d_pending_zero = RT_TRUE; d_pending_learn = RT_TRUE;
    memset(&d_job.out, 0, sizeof(d_job.out));
    d_job.out.run_adc_min = 65535u; d_job.out.run_adc_max = 65535u;
    d_job.out.run_adc_n = 65535u;   d_job.out.run_adc_noise = 65535u;
    d_job.out.run_sg_min = 65535u;  d_job.out.run_sg_max = 65535u;
    d_job.out.run_sg_n = 65535u;
    d_job.out.learn_sg_seq_first = 4294967295u;
    d_job.out.learn_sg_seq_last = 4294967295u;
    d_job.out.threshold_tier = 3u;
    d_job.out.adc_dev_warn = -32768; d_job.out.adc_dev_trip = -32768;
    d_job.out.sg_drop_warn = 65535u; d_job.out.sg_drop_trip = 65535u;
    d_job.data_valid = 1u;
    do_cmd(cmd_learn_status);                   /* st=CANCEL_PENDING 那一组(含 left 行) */
    d_job.state = (rt_uint8_t)REL_JOB_DONE;
    d_job.out_valid = 1u;
    d_cap_owner = (rt_uint8_t)REL_CAP_FREE;
    do_cmd(cmd_learn_status);                   /* thr + result 那一组 */
    d_job.state = (rt_uint8_t)REL_JOB_CANCELLED;
    d_job.why = "CANCELLED";
    do_cmd(cmd_learn_cancel);                   /* NOT-CANCELLED: already CANCELLED */
    d_job.job_id = 1u;                          /* 陈旧 id 去取消别人的任务 */
    do_cmd(cmd_learn_cancel);                   /* no such job ... current=1 */
    /* ARD2 的延迟清理披露行: 让它在**最宽 owner 取值**下也走一遍(长度判据要覆盖每条新行) */
    d_zero_cleanup = 1u;
    d_cap_owner = (rt_uint8_t)REL_CAP_BY_ZERO;
    do_cmd(cmd_learn_status);                   /* + job zero-cleanup DEFERRED 那一行 */
    d_zero_cleanup = 0u;
    d_cap_owner = (rt_uint8_t)REL_CAP_FREE;

    /* 3) 锁失败与后台未就绪两条拒绝身份 */
    g_mutex_take_fail = 1;
    do_cmd(cmd_learn);
    do_cmd(cmd_learn_status);
    do_cmd(cmd_tier2);
    do_cmd(cmd_commit);
    do_cmd(cmd_clear);
    do_cmd(cmd_show);
    do_cmd(cmd_zero);
    g_mutex_take_fail = 0;
    /* 门过了、写入时才撞锁: 这条是"不发布半份状态"的另一种身份 */
    g_ctx_take_calls = 0; g_ctx_take_fail_after = 1;   /* 第 1 次=门(过), 第 2 次=作废写入(失败) */
    do_cmd(cmd_clear);
    g_ctx_take_fail_after = -1;
    g_cfg_take_fail = 1;
    do_cmd(cmd_clear);                        /* 配置写失败: clear FAILED rc=... */
    g_cfg_take_fail = 0;
    d_ctx_inited = RT_FALSE;
    do_cmd(cmd_learn);
    do_cmd(cmd_learn_status);
    do_cmd(cmd_tier2);
    do_cmd(cmd_zero);
    do_cmd(cmd_learn_cancel);
    d_ctx_inited = RT_TRUE;
    do_cmd(cmd_cancel_bad);                   /* 非数字 JOB_ID */

    /* 4) zero 的"拿到采集器之后才撞锁"支路(第 3 次 take 失败) */
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    do_cmd(cmd_zero);
    CHECK(d_pending_zero == RT_TRUE, "4) 前置零点没成功: [%.80s]", cap_text());
    g_ctx_take_calls = 0;
    g_ctx_take_fail_after = 2;        /* 1=读快照 2=拿采集器 3=作废凭据 */
    do_cmd(cmd_zero);
    g_ctx_take_fail_after = -1;
    CHECK(cap_has("zero FAILED: ctx lock on invalidate"),
          "4) 中途撞锁支路没走到: [%.120s]", cap_text());
    /* 这条支路里连 close 也拿不到锁, 所有权留在 BY_ZERO: 如实复位夹具状态 */
    d_cap_owner = (rt_uint8_t)REL_CAP_FREE;
    memset(&d_cap, 0, sizeof(d_cap));

    /* 5) 判据: 逐调用长度(含源 LF)、无裸 NUL、每条结论行必须真出现过 */
    mx = cap_max_line_len();
    CHECK(mx + 1 <= 120, "本轮打印里最长一次调用 %d B(含 LF %d B)超过 120 B 预算",
          mx, mx + 1);
    CHECK(g_cap_truncated == 0, "捕获里出现裸 NUL(vsnprintf 计数与实际长度不一致)");
    for (k = 0; needles[k] != NULL; ++k)
    {
        if (!cap_has(needles[k])) { miss++; printf("    缺结论行: %s\n", needles[k]); }
    }
    CHECK(miss == 0, "驱动过的结论行有 %d 条没出现(长度判据不能空转)", miss);
    CHECK(cap_all_ascii() == 1, "捕获区间里出现了非 ASCII 字节(UTF-8 完整性不成立)");
}


/* ======================= M8-R7D1 返修组 A13..A16(A 复审四项) =======================
 *
 * 这四例逐条对应 A 在 NS800_R7D1_A_Review_Changes_Required_4d50310 里登记的 ARD1..ARD4。
 * 判据一律是"业务后果": 锁有没有留在手上、采集器归属有没有滞留、被拒绝的命令有没有
 * 暗改状态、REFUSED 的 commit 是否真的什么都没动。不去钉死修复后的取锁次序(那会把
 * 实现细节当成合同), 而是用实际状态 + 披露文本判定。
 * 仍然只是离线主机模型: 不构成板端抢占并发、真实停机时延或物理保护的证据。 */

/* A13(ARD1): NULL 快照在取锁之前就被拒绝; 之后查询/推进/取消仍然可用。 */
static void c_A13(void)
{
    rel_job_view_t v;
    rt_err_t rc;
    int rel_before, takes_before;

    world_reset();
    rel_before = g_mutex_release_calls;
    takes_before = g_ctx_take_calls;
    rc = rel_job_status(RT_NULL);
    CHECK(rc == -RT_EINVAL, "NULL 快照没被具名拒绝: rc=%d", (int)rc);
    CHECK(d_ctx_lock.held == 0u, "非法参数之后叶锁还挂着(held=%u)", (unsigned)d_ctx_lock.held);
    CHECK(g_mutex_release_calls == rel_before,
          "非法参数的拒绝路径放锁 %d 次(应为 0: 它根本没拿过锁)",
          g_mutex_release_calls - rel_before);
    CHECK(g_ctx_take_calls == takes_before,
          "非法参数之前已经取了 %d 次 ctx 锁(拒绝必须排在取锁之前)",
          g_ctx_take_calls - takes_before);
    /* 拒绝不能把后面的人一起锁死: 合法查询、受理、推进、取消都要走得通 */
    memset(&v, 0, sizeof(v));
    CHECK(rel_job_status(&v) == RT_EOK, "NULL 拒绝把后续合法查询也锁死了");
    prep_zero_cruise();
    do_cmd(cmd_learn);
    CHECK(job_active(), "NULL 拒绝之后 learn 受理不成立: [%.90s]", cap_text());
    g_cancel_id = job_snap().job_id;
    do_cmd(cmd_learn_cancel);
    CHECK(job_wait(50) == REL_JOB_CANCELLED, "NULL 拒绝之后取消走不通: st=%d",
          (int)job_snap().state);
    g_cancel_id = 0u;
}

/* A14(ARD2): zero 的退出清理拿不到锁时必须有明确状态 + 真实推进者, 且归还之前不交给第二个采集者。 */
static void c_A14(void)
{
    world_reset();
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0; S.raw = 1660;
    /* 第 1/2 次取锁(pending 快照、采集器受理)成功, 第 3 次起(作废凭据、退出清理)全部超时 */
    g_ctx_take_fail_after = 2;
    cap_reset();
    do_cmd(cmd_zero);
    CHECK(cap_has("zero FAILED: ctx lock on invalidate"), "作废失败没披露: [%.90s]", cap_text());
    CHECK(cap_has("zero cleanup DEFERRED"), "清理被推迟却没说出来: [%.90s]", cap_text());
    CHECK(d_cap_owner == (rt_uint8_t)REL_CAP_BY_ZERO && d_cap.active != 0u,
          "清理没拿到锁时归属不该被抹掉: owner=%u active=%u",
          (unsigned)d_cap_owner, (unsigned)d_cap.active);
    /* 旗子还立着的时候, 查询本身要能读得到它 —— 但读得到要先把瞬时锁故障撤掉 */
    g_ctx_take_fail_after = -1;
    CHECK(job_snap().cleanup_pending == 1u, "延迟清理没有可查的明确状态: cleanup=%u",
          (unsigned)job_snap().cleanup_pending);
    cap_reset();
    do_cmd(cmd_learn_status);
    CHECK(cap_has("job zero-cleanup DEFERRED"), "延迟清理在状态查询里没有披露: [%.90s]",
          cap_text());
    /* 推进者还没跑: 这时候第二个采集者仍然进不来(不偷别人的锁, 也不抹 owner) */
    cap_reset();
    do_cmd(cmd_zero);
    CHECK(cap_has("capture busy"), "清理未完成却放进了第二个采集者: [%.90s]", cap_text());
    /* 真正的收口靠 Diagnosis Thread 的一拍(rel_job_advance), 不是夹具重置 */
    pump(3);
    CHECK(d_cap_owner == (rt_uint8_t)REL_CAP_FREE && d_cap.active == 0u,
          "有界延迟清理没被推进者收口: owner=%u active=%u",
          (unsigned)d_cap_owner, (unsigned)d_cap.active);
    CHECK(job_snap().cleanup_pending == 0u, "收口之后旗子还立着");
    cap_reset();
    do_cmd(cmd_zero);
    CHECK(d_pending_zero == RT_TRUE, "故障消失后 zero 仍采不到: [%.90s]", cap_text());
    CHECK(!cap_has("capture busy"), "恢复之后仍报 busy: [%.90s]", cap_text());
}

/* A15(ARD3): 被静止门拒绝的 clear 一个字都不能改; 门禁消失后同一条命令仍要真的清干净。 */
static void c_A15(void)
{
    diag_rprof_t before;
    int zb, lb;

    prep_zero_cruise();
    CHECK(learn_run(400) == REL_JOB_DONE && d_pending_learn == RT_TRUE, "前置: 没学到候选");
    before = d_pending;
    zb = (int)d_pending_zero; lb = (int)d_pending_learn;
    g_static_begin_busy = 1;                  /* 还在 armed+cruise: 静止事务必须拒绝 */
    cap_reset();
    do_cmd(cmd_clear);
    g_static_begin_busy = 0;
    CHECK(cap_has("clear REFUSED: static txn"), "静止门没拒绝 clear: [%.90s]", cap_text());
    CHECK(memcmp(&before, &d_pending, sizeof(before)) == 0, "被拒绝的 clear 改写了候选");
    CHECK((int)d_pending_zero == zb && (int)d_pending_learn == lb,
          "被拒绝的 clear 把凭据清了: zero=%d learn=%d(应当仍是 1/1)",
          (int)d_pending_zero, (int)d_pending_learn);
    /* 拒绝不是把命令废掉: 静止之后同一条 clear 应当成功并真的清零 */
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    cap_reset();
    do_cmd(cmd_clear);
    CHECK(cap_has("profile voided"), "静止之后的 clear 没成功: [%.90s]", cap_text());
    CHECK(d_pending_zero == RT_FALSE && d_pending_learn == RT_FALSE, "成功 clear 之后凭据还在");
}

/* A16(ARD4): commit 的结果必须与事务完整性一致 —— REFUSED 就什么都没动, OK 就配置与候选同版。
 * 两段注入各打一条失败路径: ①候选发布取不到 ctx ②配置发布本身失败(要求回滚已发布的候选)。
 * "配置发布是一次 commit 里最后一次 cfg 取锁"这个事实不写死次数: 先用一次干净 commit 量出来。 */
static void c_A16(void)
{
    diag_rprof_t before, after, pend_before;
    int zb, lb, refused, cfg_changed, pend_changed, creds_kept, base, steps;

    prep_zero_cruise();
    CHECK(learn_run(400) == REL_JOB_DONE && d_pending_learn == RT_TRUE, "前置: 没有可提交的候选");
    S.mstate = MOTOR_IDLE; S.armed = 0; S.mhz = 0;
    CHECK(project_config_get_rprof(&before) == RT_EOK, "前置: 读不到已提交 profile");
    pend_before = d_pending;
    zb = (int)d_pending_zero; lb = (int)d_pending_learn;

    /* ① 让这条命令里的第 4 次 ctx 取锁失败(修复前后落在哪一步都算, 结论只看状态) */
    g_ctx_take_calls = 0;
    g_ctx_take_fail_after = 3;
    cap_reset();
    do_cmd(cmd_commit);
    g_ctx_take_fail_after = -1;
    memset(&after, 0, sizeof(after));
    CHECK(project_config_get_rprof(&after) == RT_EOK, "①回读已提交 profile 失败");
    refused = cap_has("commit REFUSED");
    cfg_changed = memcmp(&before, &after, sizeof(before)) != 0;
    pend_changed = memcmp(&pend_before, &d_pending, sizeof(pend_before)) != 0;
    creds_kept = ((int)d_pending_zero == zb && (int)d_pending_learn == lb);
    CHECK(refused && !cfg_changed && !pend_changed && creds_kept,
          "发布取锁失败时 REFUSED 却已经改了状态: refused=%d cfg_changed=%d pend_changed=%d creds=%d",
          refused, cfg_changed, pend_changed, creds_kept);
    CHECK(!cap_has("commit OK"), "同一次 commit 既 REFUSED 又 OK");

    /* ② 干净的一轮: 顺带量出这条命令一共取了几次 cfg 锁, 并证 OK 时配置与候选同版 */
    base = g_cfg_take_calls;
    cap_reset();
    do_cmd(cmd_commit);
    steps = g_cfg_take_calls - base;
    CHECK(cap_has("commit OK"), "干净世界里的 commit 没成功: [%.90s]", cap_text());
    CHECK(steps >= 1, "这条 commit 一次 cfg 锁都没取(注入无法定位)");
    memset(&after, 0, sizeof(after));
    CHECK(project_config_get_rprof(&after) == RT_EOK &&
          after.magic == DIAG_RPROF_MAGIC &&
          memcmp(&after, &d_pending, sizeof(after)) == 0, "commit OK 之后配置与候选不一致");

    /* ③ 只把最后一次 cfg 取锁(= 配置发布)打坏: 已发布的候选必须退回, 且如实披露 */
    before = after;
    pend_before = d_pending;
    g_cfg_take_calls = base;
    g_cfg_take_fail_after = base + steps - 1;
    cap_reset();
    do_cmd(cmd_commit);
    g_cfg_take_fail_after = -1;
    CHECK(cap_has("commit REFUSED"), "配置写失败却没按失败披露: [%.90s]", cap_text());
    CHECK(cap_has("config-write-rolled-back"), "配置写失败没走回滚披露: [%.110s]", cap_text());
    memset(&after, 0, sizeof(after));
    CHECK(project_config_get_rprof(&after) == RT_EOK &&
          memcmp(&before, &after, sizeof(before)) == 0, "配置写失败却留下了新配置");
    CHECK(memcmp(&pend_before, &d_pending, sizeof(pend_before)) == 0, "回滚没把候选退回原值");
}

static const struct case_ent TAB[] = {
    { "REL01-frame-identity-advances-only-on-success", c_REL01 },
    { "REL02-sg-read-fail-freezes-identity",           c_REL02 },
    { "REL03-adc-read-fail-freezes-identity",          c_REL03 },
    { "REL04-no-profile-no-claim",                     c_REL04 },
    { "REL05-profile-qualification-matrix",            c_REL05 },
    { "REL06-live-signature-binds-drive-config",       c_REL06 },
    { "REL07-sg-severe-needs-samples-and-span",        c_REL07 },
    { "REL08-stale-sg-cannot-accumulate",              c_REL08 },
    { "REL09-adc-severe-both-directions",              c_REL09 },
    { "REL10-single-adc-sample-not-duration",          c_REL10 },
    { "REL11-fused-needs-overlap",                     c_REL11 },
    { "REL12-reason-not-stall-confirmed",              c_REL12 },
    { "REL13-protect-before-print",                    c_REL13 },
    { "REL14-first-latch-immutable",                   c_REL14 },
    { "REL15-monitor-only-never-acts",                 c_REL15 },
    { "REL16-relative-mode-arm-gate",                  c_REL16 },
    { "REL17-active-measured-gate-unchanged",          c_REL17 },
    { "REL18-ramp-phase-is-exception",                 c_REL18 },
    { "REL19-out-of-window-only-in-motion",            c_REL19 },
    { "REL20-age-check-before-dedup",                  c_REL20 },
    { "REL21-recovery-needs-fresh-evidence",           c_REL21 },
    { "REL22-coverage-loss-protects-first",            c_REL22 },
    { "REL23-profile-persist-and-v2-migration",        c_REL23 },
    { "REL24-cli-strict-args-no-side-effects",         c_REL24 },
    { "REL25-cli-capture-and-publish",                 c_REL25 },
    { "REL26-tick-diff-and-threshold-bounds",          c_REL26 },
    { "REL27-active-severe-protect-before-print",      c_REL27 },
    { "REL28-strong-needs-own-evidence",               c_REL28 },
    { "REL29-frozen-adc-age-degrades",                 c_REL29 },
    { "REL30-source-gap-restarts-evidence",            c_REL30 },
    { "REL31-tick-wrap-and-rate-binding",              c_REL31 },
    { "REL32-recovery-breaks-on-counter-evidence",     c_REL32 },
    { "REL33-lock-fail-bounded-degrade",               c_REL33 },
    { "REL34-no-frame-still-supervises",               c_REL34 },
    { "REL35-incident-ticket-lifecycle",               c_REL35 },
    { "REL36-null-outparams-still-protects",           c_REL36 },
    { "REL37-post-failure-keeps-first-cause",          c_REL37 },
    { "REL38-mode-gate-and-static-transaction",        c_REL38 },
    { "REL39-zero-static-and-failed-relearn",          c_REL39 },
    { "REL40-config-lock-failure-writes-nothing",      c_REL40 },
    { "REL41-illegal-context-silent",                  c_REL41 },
    { "REL42-low-sg-committable-and-named-reject",     c_REL42 },
    { "REL43-max-value-lines-fit-console",             c_REL43 },
    { "REL44-impact-fresh-evidence-only",              c_REL44 },
    { "REL45-ctl-take-failure-writes-nothing",         c_REL45 },
    { "REL46-ctl-uninit-take-and-protect",             c_REL46 },
    { "REL47-commit-inside-static-gate",               c_REL47 },
    { "REL48-new-generation-clears-evidence",          c_REL48 },
    { "REL49-frame-not-same-as-motion-known",          c_REL49 },
    { "REL50-stale-idle-not-static-exemption",         c_REL50 },
    { "A01-start-returns-without-sample-wait",         c_A01 },
    { "A02-independent-samples-publish-candidate",     c_A02 },
    { "A03-consistent-read-only-status",               c_A03 },
    { "A04-strict-reject-and-busy-before-side-effects", c_A04 },
    { "A05-cancel-terminates-without-new-frames",      c_A05 },
    { "A06-cancel-versus-completion-linearized",       c_A06 },
    { "A07-stale-id-and-quiescence-guard",             c_A07 },
    { "A08-timeout-advances-without-frames-and-wrap",  c_A08 },
    { "A09-motion-mode-signature-change-aborts",       c_A09 },
    { "A10-failed-relearn-invalidates-credential",     c_A10 },
    { "A11-mutator-exclusion-and-lock-context-failures", c_A11 },
    { "A12-bounded-calls-utf8-no-nul",                 c_A12 },
    { "A13-null-status-rejected-without-lock-leak",     c_A13 },
    { "A14-zero-cleanup-deferred-then-recovered",       c_A14 },
    { "A15-refused-clear-keeps-pending-and-creds",      c_A15 },
    { "A16-commit-refused-keeps-config-and-candidate",  c_A16 },
};
#define NTAB ((int)(sizeof(TAB)/sizeof(TAB[0])))

int main(int argc, char **argv)
{
    const char *only = (argc > 1) ? argv[1] : RT_NULL;
    int i, run;

    printf("SIZES|project_config_t=%u|diag_rprof_t=%u|sensor_frame_t=%u|diag_input_t=%u|v2=%u\n",
           (unsigned)sizeof(project_config_t), (unsigned)sizeof(diag_rprof_t),
           (unsigned)sizeof(sensor_frame_t), (unsigned)sizeof(diag_input_t),
           (unsigned)sizeof(project_config_v2_t));

    /* R7F1(任务书 §3): 本链测的是**运行中**的 SG/ADC 样本身份与相对判定, 前提就是
     * supervisor 已经过了 stage 11。启动期 SG 门(sg_seq 只有真读成功才推进)默认是关的,
     * 所以这里按 stage 11 之后的事实把门打开一次; 门本身的行为由 SD13a/b/c 在
     * run_safety_data_semantics.sh 那条链上判(那里同样是真实 sensor_service.c)。 */
    sensor_service_sg_startup_release();

    if (only != RT_NULL && only[0] != 0)
    {
        for (i = 0; i < NTAB; ++i)
        {
            run = (strstr(only, TAB[i].id) != RT_NULL);
            printf("SELECTED|%s|%s\n", TAB[i].id, run ? "YES" : "NO");
            if (!run) continue;
            case_begin(TAB[i].id);
            TAB[i].fn();
            case_end(TAB[i].id);
        }
        {
            char buf[4096];
            char *tok;
            strncpy(buf, only, sizeof(buf) - 1); buf[sizeof(buf) - 1] = 0;
            for (tok = strtok(buf, " ,"); tok; tok = strtok(NULL, " ,"))
            {
                int known = 0;
                for (i = 0; i < NTAB; ++i) if (strcmp(tok, TAB[i].id) == 0) known = 1;
                if (!known) { printf("INFRA|UNKNOWN-CASE-ID|%s\n", tok); g_pre_bad++; }
            }
        }
    }
    else
    {
        for (i = 0; i < NTAB; ++i)
        {
            printf("SELECTED|%s|YES\n", TAB[i].id);
            case_begin(TAB[i].id);
            TAB[i].fn();
            case_end(TAB[i].id);
        }
    }
    for (i = 0; i < NTAB; ++i)
        if (only != RT_NULL && only[0] != 0 && strstr(only, TAB[i].id) == RT_NULL)
            printf("NOT_RUN|%s\n", TAB[i].id);
    printf("SUMMARY|cases=%d pass=%d fail=%d pre_bad=%d executed=%d\n",
           (only != RT_NULL && only[0]) ? g_executed : NTAB, g_pass, g_fail, g_pre_bad, g_executed);
    if (g_pre_bad) return 4;
    return g_fail ? 1 : 0;
}
