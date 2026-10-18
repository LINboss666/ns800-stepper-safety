/*
 * host_main.c - 主机(GCC/MinGW)执行夹具: 用**真实固件源码**跑单元测试
 *
 * 做法: 本文件先把夹具(RT-Thread API + 被依赖模块的桩)准备好, 然后
 *   #include 真实的 applications/tmc2209.c 与 applications/motor.c,
 *   再在 main() 里调用固件自带的自测(tmc_calc_selftest / tmc_cfg_selftest /
 *   motor_drv_guard_selftest)以及夹具级断言(H 系列与 C 系列)。
 * 因此执行的是与烧录镜像同一份源码, 不是移植/重写的副本。
 *
 * 变体:
 *   V1(默认, 与发布一致): 三个安全宏保持源码里的 FALSE, 只允许"拒绝"方向的路径。
 *   V2(-DHOST_GATE_OVERRIDE): 在主机上把宏当 TRUE 编译, 用来覆盖 arm→start→运行期
 *      守卫→停机 这条在真机上目前**不可达**的正向路径。V2 不是发布配置, 输出里
 *      会打印横幅; 仓库与审核包内的源码三个宏仍然是 FALSE。
 *
 * F2d 新增(不改 F2c 的 A/H/C 系列判据):
 *   帧级观察(READ 请求 vs WRITE 帧分开数) + 设备级假芯片(shim_chip_enabled 打开
 *   才应答, 默认关 = 与 F2c 夹具行为逐字节一致)。据此断言 probe 只读、并发拒绝、
 *   失败路径零流量、GSTAT 纯解码, 以及"已提交快照后 probe 不让芯片与快照分离"。
 */
#include <rtthread.h>
#include <rtdevice.h>
#include <stdio.h>
#include <string.h>

/* ======================= 夹具: 计数器与可控输入 ======================= */
int shim_dev_find_calls, shim_dev_open_calls, shim_dev_read_calls;
int shim_dev_write_calls, shim_pwm_enable_calls, shim_pwm_disable_calls;
int shim_pwm_set_calls, shim_pin_write_calls, shim_transition_calls;
int shim_transition_to_run, shim_post_event_calls, shim_drv_enable_write_calls;
int shim_last_drv_enable_level = 0;

rt_bool_t shim_pad_low = RT_TRUE;
rt_bool_t shim_state_ready = RT_TRUE;
rt_bool_t shim_fault_none = RT_TRUE;
rt_bool_t shim_protection_ready = RT_TRUE;
rt_bool_t shim_read_returns_data = RT_FALSE;

/* ---------- F2d 新增: 帧级观察 + 设备级假芯片 ----------
 * 为什么必须加: rt_device_write() 既承载 READ 请求(4 字节, byte2 bit7=0)也承载
 * WRITE 帧(8 字节, byte2 bit7=1)。F2c 夹具只数"总调用次数", 分不清读了什么/写了
 * 什么, 而 F2d 的核心断言正是"诊断命令一个 WRITE 帧都不许发"。
 * 假芯片按 TMC2209 语义建模: 只有 WRITE 帧让 IFCNT(0x02) 自增(手册: 读不改变它),
 * 并把写值落到寄存器表里, 读请求从表里回一帧带 CRC 的应答。
 * ⚠ 这只存在于主机夹具: 真机上 uart2 后面是哑的 shim, 不会有任何应答。 */
int shim_frame_read_reqs, shim_frame_write_frames;
int shim_write_frames_by_reg[0x80];
unsigned shim_chip[0x80];
int shim_chip_enabled;              /* 0 = 完全保持 F2c 夹具行为(永远无应答) */

static rt_uint8_t shim_q[512];
static int shim_qn;                 /* 队列中未取走的字节数 */

/* 与固件同一算法的独立实现, 用来给假芯片的应答补 CRC。
 * 先按官方向量自检(READ IFCNT=0x8F / READ GCONF=0x48), 不自检就没有资格当观测工具。 */
static rt_uint8_t shim_crc8(const rt_uint8_t *d, int n)
{
    rt_uint8_t crc = 0;
    int i, b;

    for (i = 0; i < n; ++i)
    {
        rt_uint8_t curr = d[i];
        for (b = 0; b < 8; ++b)
        {
            if ((crc >> 7) ^ (curr & 1)) crc = (rt_uint8_t)((crc << 1) ^ 0x07);
            else                         crc = (rt_uint8_t)(crc << 1);
            curr >>= 1;
        }
    }
    return crc;
}

static void shim_q_push(const rt_uint8_t *p, int n)
{
    int i;
    for (i = 0; i < n && shim_qn < (int)sizeof(shim_q); ++i) shim_q[shim_qn++] = p[i];
}

static void shim_chip_boot(void)
{
    memset(shim_chip, 0, sizeof(shim_chip));
    shim_chip[0x00] = 0x00000101u;      /* GCONF   复位默认(手册 bit0/bit8=1) */
    shim_chip[0x01] = 0x00000001u;      /* GSTAT   reset=1(上电后未被读过) */
    shim_chip[0x02] = 0x00000000u;      /* IFCNT   还没收到任何写 */
    shim_chip[0x06] = 0x21000040u;      /* IOIN    VERSION=0x21(真机读回过 0x40/0x41) */
    shim_chip[0x6C] = 0x15010053u;      /* CHOPCONF 本轮真机上电基线 */
    shim_qn = 0;
    shim_frame_read_reqs = 0;
    shim_frame_write_frames = 0;
    memset(shim_write_frames_by_reg, 0, sizeof(shim_write_frames_by_reg));
}

/* ---- RT-Thread IPC 夹具 ---- */
/* 故障注入开关(R02/R03 的失败方向要用; 默认全关 = 正常回归) */
const char *shim_mutex_init_fail_prefix   = 0;
const char *shim_thread_create_fail_name  = 0;
const char *shim_thread_startup_fail_name = 0;

static int shim_name_matches(const char *rule, const char *name)
{
    return (rule != 0 && name != 0 &&
            strncmp(name, rule, strlen(rule)) == 0) ? 1 : 0;
}

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{
    (void)flags;
    if (shim_name_matches(shim_mutex_init_fail_prefix, name))
    {
        printf("  [INJECT] rt_mutex_init(\"%s\") 强制失败(夹具注入)\n", name ? name : "?");
        return -RT_ENOSYS;
    }
    InitializeCriticalSection(&m->cs);
    m->inited = RT_TRUE;
    return RT_EOK;
}
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout)
{
    if (!m->inited) return -RT_EINVAL;
    if (timeout == RT_WAITING_NO)
        return TryEnterCriticalSection(&m->cs) ? RT_EOK : -RT_EBUSY;
    EnterCriticalSection(&m->cs);
    return RT_EOK;
}
rt_err_t rt_mutex_release(struct rt_mutex *m)
{
    if (!m->inited) return -RT_EINVAL;
    LeaveCriticalSection(&m->cs);
    return RT_EOK;
}
rt_err_t rt_mutex_detach(struct rt_mutex *m)
{
    if (!m->inited) return -RT_EINVAL;
    DeleteCriticalSection(&m->cs);
    m->inited = RT_FALSE;
    return RT_EOK;
}

rt_sem_t rt_sem_create(const char *name, rt_uint32_t value, rt_uint8_t flag)
{
    rt_sem_t s;
    (void)name; (void)flag;
    s = (rt_sem_t)calloc(1, sizeof(*s));
    if (s == RT_NULL) return RT_NULL;
    s->h = CreateSemaphoreA(RT_NULL, (LONG)value, 0x7FFFFFFF, RT_NULL);
    if (s->h == RT_NULL) { free(s); return RT_NULL; }
    return s;
}
rt_err_t rt_sem_take(rt_sem_t s, rt_int32_t timeout)
{
    DWORD r;
    if (s == RT_NULL) return -RT_EINVAL;
    r = WaitForSingleObject(s->h, timeout == RT_WAITING_FOREVER ? INFINITE
                                                                 : (DWORD)timeout);
    return (r == WAIT_OBJECT_0) ? RT_EOK : -RT_ETIMEOUT;
}
rt_err_t rt_sem_release(rt_sem_t s)
{
    if (s == RT_NULL) return -RT_EINVAL;
    return ReleaseSemaphore(s->h, 1, RT_NULL) ? RT_EOK : -RT_ERROR;
}
rt_err_t rt_sem_delete(rt_sem_t s)
{
    if (s == RT_NULL) return -RT_EINVAL;
    CloseHandle(s->h);
    free(s);
    return RT_EOK;
}

static DWORD WINAPI shim_thread_thunk(LPVOID p)
{
    struct rt_thread_obj *t = (struct rt_thread_obj *)p;
    t->entry(t->param);
    return 0;
}
rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick)
{
    struct rt_thread_obj *t;
    (void)stack_size; (void)prio; (void)tick;
    /* R03 负例注入: 只掐掉指定名字的线程(motor 斜坡线程用别的名字, 不受影响) */
    if (shim_name_matches(shim_thread_create_fail_name, name))
    {
        printf("  [INJECT] rt_thread_create(\"%s\") 强制失败(夹具注入)\n", name ? name : "?");
        return RT_NULL;
    }
    t = (struct rt_thread_obj *)calloc(1, sizeof(*t));
    if (t == RT_NULL) return RT_NULL;
    t->name = name;
    t->entry = entry;
    t->param = param;
    t->h = RT_NULL;
    return t;
}
rt_err_t rt_thread_startup(rt_thread_t t)
{
    DWORD id;
    if (t == RT_NULL) return -RT_EINVAL;
    if (shim_name_matches(shim_thread_startup_fail_name, t->name))
    {
        printf("  [INJECT] rt_thread_startup(\"%s\") 强制失败(夹具注入)\n",
               t->name ? t->name : "?");
        return -RT_ERROR;
    }
    t->h = CreateThread(RT_NULL, 0, shim_thread_thunk, t, 0, &id);
    return (t->h != RT_NULL) ? RT_EOK : -RT_ERROR;
}
rt_err_t rt_thread_delete(rt_thread_t t)
{
    if (t == RT_NULL) return -RT_EINVAL;
    if (t->h != RT_NULL) { WaitForSingleObject(t->h, 2000); CloseHandle(t->h); }
    free(t);
    return RT_EOK;
}

/* ---- 设备/PWM/引脚夹具 ---- */
static struct rt_device       shim_fake_serial;
static struct rt_device_pwm   shim_fake_pwm;

rt_device_t rt_device_find(const char *name)
{
    shim_dev_find_calls++;
    if (strcmp(name, "uart2") == 0) return &shim_fake_serial;
    if (strcmp(name, "epwm1") == 0)  return (rt_device_t)&shim_fake_pwm;
    return RT_NULL;
}
rt_err_t rt_device_open(rt_device_t dev, rt_uint16_t oflag)
{
    (void)oflag;
    shim_dev_open_calls++;
    return (dev == RT_NULL) ? -RT_ERROR : RT_EOK;
}
rt_err_t rt_device_close(rt_device_t dev) { (void)dev; return RT_EOK; }
rt_size_t rt_device_read(rt_device_t dev, rt_size_t pos, void *buf, rt_size_t size)
{
    rt_uint8_t *out = (rt_uint8_t *)buf;
    int n = 0;

    (void)dev; (void)pos;
    shim_dev_read_calls++;
    while (size > 0 && shim_qn > 0)
    {
        out[n++] = shim_q[0];
        memmove(shim_q, shim_q + 1, (size_t)--shim_qn);
        size--;
    }
    return (rt_size_t)n;              /* 默认队列为空 = 芯片失联(与 F2c 夹具一致) */
}
rt_size_t rt_device_write(rt_device_t dev, rt_size_t pos, const void *buf, rt_size_t size)
{
    const rt_uint8_t *f = (const rt_uint8_t *)buf;
    unsigned reg;

    (void)dev; (void)pos;
    shim_dev_write_calls++;
    if (f != RT_NULL && size >= 3)
    {
        reg = (unsigned)(f[2] & 0x7Fu);
        if (f[2] & 0x80u)                 /* WRITE 帧: 8 字节, bit7=1 */
        {
            unsigned v;
            shim_frame_write_frames++;
            if (reg < 0x80u) shim_write_frames_by_reg[reg]++;
            if (shim_chip_enabled && size >= 8)
            {
                v = ((unsigned)f[3] << 24) | ((unsigned)f[4] << 16) |
                    ((unsigned)f[5] << 8) | (unsigned)f[6];
                shim_chip[reg] = v;
                shim_chip[0x02] = (shim_chip[0x02] + 1u) & 0xFFu;  /* IFCNT 只对写自增 */
            }
        }
        else                              /* READ 请求: 4 字节, bit7=0 */
        {
            shim_frame_read_reqs++;
            if (shim_chip_enabled && size >= 4)
            {
                rt_uint8_t rep[8];
                unsigned v = shim_chip[reg];
                rep[0] = 0x05; rep[1] = 0xFF; rep[2] = (rt_uint8_t)reg;
                rep[3] = (rt_uint8_t)(v >> 24); rep[4] = (rt_uint8_t)(v >> 16);
                rep[5] = (rt_uint8_t)(v >> 8);  rep[6] = (rt_uint8_t)v;
                rep[7] = shim_crc8(rep, 7);
                shim_q_push(rep, 8);
            }
        }
    }
    return size;
}
rt_err_t rt_device_control(rt_device_t dev, int cmd, void *arg)
{
    (void)dev; (void)cmd; (void)arg; return RT_EOK;
}
rt_err_t rt_pwm_set(struct rt_device_pwm *pwm, int ch, rt_uint32_t period, rt_uint32_t pulse)
{
    (void)pwm; (void)ch; (void)period; (void)pulse;
    shim_pwm_set_calls++;
    return RT_EOK;
}
rt_err_t rt_pwm_enable(struct rt_device_pwm *pwm, int ch)
{
    (void)pwm; (void)ch;
    shim_pwm_enable_calls++;
    return RT_EOK;
}
rt_err_t rt_pwm_disable(struct rt_device_pwm *pwm, int ch)
{
    (void)pwm; (void)ch;
    shim_pwm_disable_calls++;
    return RT_EOK;
}
void rt_pin_write(rt_base_t pin, rt_base_t value)
{
    (void)pin; (void)value;
    shim_pin_write_calls++;
}

/* ---- 被依赖模块的桩(safety_gpio / safety_state / safety_thread / step_pwm) ---- */
#include "app_health.h"
#include "safety_state.h"
#include "safety_gpio.h"
#include "safety_thread.h"
#include "step_pwm.h"

static safety_state_t shim_state = SAFETY_READY;
static rt_uint32_t    shim_fault = FAULT_NONE;
static rt_bool_t      shim_step_owned_by_diag = RT_FALSE;

rt_bool_t  safety_gpio_ready(void) { return RT_TRUE; }
rt_err_t   safety_gpio_boot(void)  { return RT_EOK; }
rt_base_t  safety_pin(const char *name) { (void)name; return 23; }

rt_err_t safety_drv_enable_write(rt_uint8_t level)
{
    shim_drv_enable_write_calls++;
    shim_last_drv_enable_level = level;
    shim_pad_low = (level == 0) ? RT_TRUE : RT_FALSE;
    return RT_EOK;                       /* 桩: 写+回读总是一致 */
}
rt_bool_t safety_drv_enable_is_low(void) { return shim_pad_low ? RT_TRUE : RT_FALSE; }

const char *safety_state_name(safety_state_t s)
{
    switch (s)
    {
    case SAFETY_BOOT: return "BOOT";
    case SAFETY_INIT: return "INIT";
    case SAFETY_SELF_TEST: return "SELF_TEST";
    case SAFETY_READY: return "READY";
    case SAFETY_RUN: return "RUN";
    case SAFETY_LOAD_WARNING: return "LOAD_WARNING";
    case SAFETY_ABNORMAL: return "ABNORMAL";
    case SAFETY_FAULT_LATCHED: return "FAULT_LATCHED";
    case SAFETY_ESTOP: return "ESTOP";
    case SAFETY_MANUAL_CLEAR: return "MANUAL_CLEAR";
    default: return "?";
    }
}
safety_state_t safety_state_get(void) { return shim_state; }
rt_uint32_t    safety_fault_get(void) { return shim_fault; }

rt_err_t safety_transition(safety_state_t next)
{
    shim_transition_calls++;
    if (next == SAFETY_RUN) shim_transition_to_run++;
    /* 只放行本夹具需要走到的合法转换, 其余按白名单外拒绝 */
    if ((shim_state == SAFETY_READY && next == SAFETY_RUN) ||
        (shim_state == SAFETY_RUN && next == SAFETY_READY)  ||
        (next == SAFETY_FAULT_LATCHED))
    { shim_state = next; return RT_EOK; }
    return -RT_EPERM;
}
void safety_force_shutdown(rt_uint32_t code) { shim_state = SAFETY_FAULT_LATCHED; shim_fault = code; }
void safety_enter_fault(rt_uint32_t code)    { shim_state = SAFETY_FAULT_LATCHED; shim_fault = code; }
rt_err_t safety_post_event(rt_uint32_t event)
{
    shim_post_event_calls++;
    (void)event;
    return RT_EOK;
}
rt_err_t safety_run_selftest(void)     { return RT_EOK; }
rt_err_t safety_startup_selftest(void) { return RT_EOK; }
rt_err_t safety_state_boot(void)       { return RT_EOK; }
void     safety_fault_reset_manual(void) { shim_fault = FAULT_NONE; shim_state = SAFETY_MANUAL_CLEAR; }
struct rt_event *safety_event_handle(void) { return RT_NULL; }
rt_bool_t safety_events_ready(void)    { return RT_TRUE; }

rt_err_t  safety_thread_init(void)      { return RT_EOK; }
rt_err_t  safety_irq_attach(void)       { return RT_EOK; }
rt_bool_t safety_irq_attached(void)     { return RT_TRUE; }
rt_bool_t safety_polarity_validated(void) { return shim_protection_ready ? RT_TRUE : RT_FALSE; }
rt_bool_t safety_protection_ready(void) { return shim_protection_ready ? RT_TRUE : RT_FALSE; }
subsys_health_t safety_thread_get_health(void) { return SUBSYS_OK; }

void      step_pwm_force_stop(void)     { shim_step_owned_by_diag = RT_FALSE; }
rt_bool_t step_pwm_output_active(void)  { return shim_step_owned_by_diag ? RT_TRUE : RT_FALSE; }

/* ======================= 真实的固件源码 ======================= */
#include "tmc2209.c"
#include "motor.c"

/* ======================= 断言脚手架 ======================= */
static int g_pass, g_fail;
#define CHECK(cond, fmt, ...) do {                                         \
    if (cond) { g_pass++; printf("  [PASS] " fmt "\n", ##__VA_ARGS__); }   \
    else      { g_fail++; printf("  [FAIL] " fmt "\n", ##__VA_ARGS__); }   \
} while (0)

static int counters_bus_reads(void) { return shim_dev_read_calls + shim_dev_write_calls; }

/* ======================= R02: 固件自测终态的机器可判定采集 =======================
 * 固件里的 tmc_calc_selftest / tmc_cfg_selftest / motor_drv_guard_selftest 都是 void,
 * 只用 rt_kprintf 打一行终态。上一轮夹具因此只核对了 UART 流量, 自测真失败也照样
 * ALL PASS(独立复审篡改算术锚点复现了这一点)。现在:
 *   1) rt_kprintf 走 shim_kprintf —— 在捕获窗口开启时把每一行留一份(纯旁路记录,
 *      不改变任何输出内容);
 *   2) 自测跑完后取"该标签前缀的最后一行"作终态: 只有 `] ALL PASS` 算通过;
 *      `] FAILED`、含 INCOMPLETE/SKIP、或压根没有合法终态(未执行/中途 return)都算不通过;
 *   3) 必需用例(T14/T18/T21 等)另外要求捕获里出现其**执行标记**, 缺席记 MISSING。
 * ⚠ 绝不粗暴匹配 "FAILED" 字符串: 配置生命周期的负向用例会合法打印
 *    `[TMC-CFG] FAILED at ...`, 那是被测行为; 终态判据只认同一个标签前缀的最后一行。 */
#define CAP_MAX   512
#define CAP_LINE  320
static char g_cap[CAP_MAX][CAP_LINE];
static int  g_cap_n, g_cap_on;

int shim_kprintf(const char *fmt, ...)
{
    va_list ap;
    int n;

    va_start(ap, fmt);
    n = vfprintf(stdout, fmt, ap);
    va_end(ap);
    if (g_cap_on)
    {
        va_start(ap, fmt);
        if (g_cap_n < CAP_MAX)
        {
            vsnprintf(g_cap[g_cap_n], CAP_LINE, fmt, ap);
            g_cap_n++;
        }
        va_end(ap);
    }
    return n;
}

static void cap_begin(void) { g_cap_n = 0; g_cap_on = 1; }
static void cap_end(void)   { g_cap_on = 0; }

static const char *cap_last_with(const char *prefix)
{
    int i, hit = -1;
    size_t len = strlen(prefix);

    for (i = 0; i < g_cap_n; ++i)
        if (strncmp(g_cap[i], prefix, len) == 0) hit = i;
    return (hit < 0) ? RT_NULL : g_cap[hit];
}

static int cap_seen(const char *prefix, const char *frag)
{
    int i;
    size_t len = strlen(prefix);

    for (i = 0; i < g_cap_n; ++i)
        if (strncmp(g_cap[i], prefix, len) == 0 && strstr(g_cap[i], frag) != RT_NULL)
            return 1;
    return 0;
}

typedef enum { VERDICT_PASS = 0, VERDICT_FAIL, VERDICT_INCOMPLETE, VERDICT_NO_END } verdict_t;

static const char *verdict_name(verdict_t v)
{
    switch (v)
    {
    case VERDICT_PASS:       return "ALL PASS";
    case VERDICT_FAIL:       return "FAILED";
    case VERDICT_INCOMPLETE: return "INCOMPLETE/SKIP";
    default:                 return "无合法终态行(未执行或中途返回)";
    }
}

static verdict_t cap_terminal(const char *prefix)
{
    const char *line = cap_last_with(prefix);

    if (line == RT_NULL)                       return VERDICT_NO_END;
    if (strstr(line, "INCOMPLETE") != RT_NULL ||
        strstr(line, "SKIP:")      != RT_NULL) return VERDICT_INCOMPLETE;
    if (strstr(line, "] ALL PASS") != RT_NULL) return VERDICT_PASS;
    if (strstr(line, "] FAILED")   != RT_NULL) return VERDICT_FAIL;
    return VERDICT_NO_END;
}

static int g_bad_result;      /* 必需自测终态非 PASS 的数量 */
static int g_missing_case;    /* 必需用例未执行/标记缺席的数量 */

static void require_verdict(const char *what, const char *tag, verdict_t v)
{
    if (v == VERDICT_PASS)
        printf("  [PASS] 自测终态 %s = %s\n", what, verdict_name(v));
    else
    {
        g_bad_result++;
        printf("  [BADRESULT] 自测终态 %s = %s  <-- 计入总判定, 不得算通过\n",
               what, verdict_name(v));
    }
}

static void require_case(const char *what, const char *prefix, const char *frag)
{
    if (cap_seen(prefix, frag))
        printf("  [PASS] 必需用例已执行: %s\n", what);
    else
    {
        g_missing_case++;
        printf("  [MISSING] 必需用例未执行: %s (在捕获里找不到 \"%s ... %s\")\n",
               what, prefix, frag);
    }
}

static void require_ran(const char *what, int ran)
{
    if (ran) printf("  [PASS] 必需用例已执行: %s\n", what);
    else   { g_missing_case++; printf("  [MISSING] 必需用例未执行: %s\n", what); }
}

#ifdef HOST_GATE_OVERRIDE
/* ---- V2 用: 主机侧把生产实例接到假总线上, 并给出可开的门禁 ---- */
static rt_uint32_t g_host_gate_mask = 0u;
static rt_uint32_t host_gate_open(void *priv) { (void)priv; return g_host_gate_mask; }
static tmc_fx_t    g_fx;

static void host_prod_use_fake_chip(void)
{
    tmc_fx_reset(&g_fx);
    tmc_cfg_prod.bus  = &tmc_cfg_bus_fake;
    tmc_cfg_prod.priv = &g_fx;
    tmc_cfg_prod.gate = host_gate_open;
    g_host_gate_mask  = 0u;
}

#endif

/* R03: 关键并发用例是否真的执行过(前置条件成立才算执行, "清理没报错"不算跑过) */
static int g_fd4_executed, g_c7_executed;

/* R01/R02/R03 负例注入开关(编译期 -D, 默认全关 = 正常回归; 只作用于主机夹具) */
#ifdef HOST_INJECT_COMPILE_ERROR
#error "注入: 故意让本变体编译失败(HOST_INJECT_COMPILE_ERROR), 用于验证 runner 不因旧二进制蒙混"
#endif

int main(void)
{
    int reads_before, run_before;
    rt_err_t e;

#ifdef HOST_INJECT_VARIANT_FAIL
    /* R01 负例: 让指定变体必然判失败, 检查外层 runner 是否真的传播非零 */
    CHECK(0, "注入: 本变体故意判失败(HOST_INJECT_VARIANT_FAIL=%d)",
          (int)HOST_INJECT_VARIANT_FAIL);
#endif
#ifdef HOST_INJECT_NO_HELPER_THREAD
    shim_thread_create_fail_name = "fxcHold";      /* R03: 辅助线程建不起来 */
#endif
#ifdef HOST_INJECT_NO_THREAD_STARTUP
    shim_thread_startup_fail_name = "fxcHold";     /* R03: 起不来 -> 无法确认另一线程持锁 */
#endif
#ifdef HOST_INJECT_TEST_CTX_LOCK_FAIL
    /* R02: 让自测的局部实例锁建不起来 -> 自测走 INCOMPLETE 分支且不打印终态 PASS。
     * 前缀只匹配 tfxCfg*(生产实例用 tmcCfg*, 不受影响)。 */
    shim_mutex_init_fail_prefix = "tfxCfg";
#endif

    printf("=========================================================\n");
#ifdef HOST_GATE_OVERRIDE
    printf("Phase8-F2d HOST TEST  变体 V2: HOST_GATE_OVERRIDE (仅主机覆盖路径)\n");
    printf("!! 这不是发布配置: 仅用于覆盖真机上当前不可达的 arm/start/守卫正向路径 !!\n");
#else
    printf("Phase8-F2d HOST TEST  变体 V1: 与发布一致(三个安全宏 = FALSE)\n");
#endif
    printf("gcc host build, 真实源码: tmc2209.c + motor.c (含夹具 shim)\n");
    printf("=========================================================\n");

    tmc2209_init();
    motor_init();

    /* ---------- A1..A3: 固件自带自测原样执行 + 终态汇总(R02) ---------- */
    printf("\n[A] 固件自带离线自测(源码原文执行; 终态计入总判定)\n");
    printf("---- A1 tmc_calc_selftest ----\n");
    reads_before = counters_bus_reads();
    cap_begin();
    tmc_calc_selftest();
    cap_end();
    CHECK(counters_bus_reads() == reads_before, "A1 tmc_calc_selftest 零总线流量");
    require_verdict("A1 tmc_calc_selftest", "[TMC-ST]", cap_terminal("[TMC-ST]"));

    printf("---- A2 tmc_cfg_selftest (T1..T21) ----\n");
    reads_before = counters_bus_reads();
    {
        int finds_before = shim_dev_find_calls;

        cap_begin();
        tmc_cfg_selftest();
        cap_end();
        CHECK(counters_bus_reads() == reads_before && shim_dev_find_calls == finds_before,
              "A2 tmc_cfg_selftest 全程未碰 uart2 (read+write=%d find=%d 未变)",
              counters_bus_reads(), shim_dev_find_calls);
        require_verdict("A2 tmc_cfg_selftest", "[TMC-CFG-ST]", cap_terminal("[TMC-CFG-ST]"));
        /* R03: 这三条都是"必须真的跑过"的关键用例 —— 它们各自的执行标记缺席时,
         * 不允许因为"整体没报错"就算通过。T14 的 SKIP 分支会在终态之前打印,
         * 所以既要看到 OK 标记, 也不能只看到 SKIP。 */
        require_case("T14 跨线程占用 txn(fail closed)", "[TMC-CFG-ST]",
                     "T14 跨线程占用 -> -RT_EBUSY fail closed OK");
        require_case("T18 生产 ctx 未被自测改动", "[TMC-CFG-ST]", "T18 生产 ctx 未被改动");
        require_case("T21 锁不可用时全链拒绝", "[TMC-CFG-ST]", "T21 锁不可用 -> ENOSYS 拒绝");
        if (cap_seen("[TMC-CFG-ST]", "T14 SKIP"))
        {
            g_missing_case++;
            printf("  [MISSING] T14 走了 SKIP 分支: 关键并发用例未实际执行\n");
        }
    }

    printf("---- A3 motor_drv_guard_selftest (G1..G10) ----\n");
    reads_before = counters_bus_reads();
    cap_begin();
    motor_drv_guard_selftest();
    cap_end();
    CHECK(counters_bus_reads() == reads_before,
          "A3 R04 验证: 真值表自测零 UART 流量(read+write=%d 未变)", counters_bus_reads());
    require_verdict("A3 motor_drv_guard_selftest", "[MOT-GUARD]", cap_terminal("[MOT-GUARD]"));
    require_case("G1..G10 十条真值表全打过", "[MOT-GUARD]", "G10.lost-beats-busy");

#ifndef HOST_GATE_OVERRIDE
    /* ================= V1: 发布配置下"必须拒绝"的方向 ================= */
    printf("\n[H] V1 发布配置方向断言(三个安全宏 = FALSE)\n");

    int open_before, en_before;

    /* H1 P0-A: STEP 诊断输出许可必须为拒 */
    en_before = shim_pwm_enable_calls;
    CHECK(motor_pwm_grant_to_diag() != RT_EOK,
          "H1 P0-A: 使能链未验收且未确认隔离环境 -> STEP 借用被拒(pwm_enable=%d 未变)",
          en_before == shim_pwm_enable_calls);

    /* H2 P0-B: 生产 apply 必须一位都不写 */
    reads_before = counters_bus_reads();
    open_before  = shim_dev_open_calls;
    e = tmc2209_apply_config();
    CHECK(e == -RT_EPERM && counters_bus_reads() == reads_before &&
          shim_dev_open_calls == open_before,
          "H2 P0-B: 生产 apply=%d, 零 open/零总线动作, state=%s ok_for_arm=%d",
          (int)e, tmc_drv_cfg_state_name(tmc2209_get_config_state()),
          (int)tmc2209_drive_config_ok_for_arm());

    /* H3 R03 前置: 未 CONFIGURED 时复核必须拒绝, 且不产生 UART */
    reads_before = counters_bus_reads();
    e = tmc2209_config_integrity_check();
    CHECK(e == -RT_EPERM && counters_bus_reads() == reads_before,
          "H3: 未配置时 integrity=%d(应 -RT_EPERM) 且零总线动作", (int)e);

    /* H4 R02: **注入真实的 rt_mutex_init 失败**(第一轮执行发现的正确问题: 原先
     * 只是手动 detach, 而 apply 里会懒重建, 于是测到的是 -RT_EPERM 而不是 -ENOSYS)。
     * 现在让建锁真的失败, 再验证"能力缺失"方向一律拒绝。 */
    {
        rt_uint32_t mask;

        (void)rt_mutex_detach(&tmc_cfg_prod.lock);
        (void)rt_mutex_detach(&tmc_cfg_prod.txn);
        tmc_cfg_prod.lock_ok = RT_FALSE;
        tmc_cfg_prod.txn_ok  = RT_FALSE;
        tmc_cfg_prod_ready   = RT_FALSE;
        shim_mutex_init_fail_prefix = "tmcCfg";   /* 只让生产实例的锁建不起来 */
        CHECK(tmc_cfg_prod_sync() == -RT_ENOSYS, "H40 R02: 建锁失败被如实上报");
        tmc_cfg_prod.state      = TMC_DRV_CONFIGURED;   /* 假装以前成功过 */
        tmc_cfg_prod.done_valid = RT_TRUE;

        mask = tmc2209_get_write_gate_mask();
        e = tmc2209_apply_config();
        reads_before = counters_bus_reads();
        CHECK((mask & TMC_WGATE_NOT_SYNCED) != 0u,
              "H4a R02: 无锁实例的门禁掩码带 NOT_SYNCED (mask=0x%X)", (unsigned)mask);
        CHECK(e == -RT_ENOSYS, "H4b R02: apply=%d 应为 -RT_ENOSYS", (int)e);
        CHECK(tmc2209_config_integrity_check() == -RT_ENOSYS,
              "H4c R02: integrity 也拒绝(-RT_ENOSYS)");
        CHECK(tmc2209_drive_config_ok_for_arm() == RT_FALSE,
              "H4d R02: 无锁时 arm 判定必须为假(即便 state 仍写着 CONFIGURED)");
        CHECK(tmc2209_cfg_txn_begin() == -RT_ENOSYS, "H4e R02: cfg_txn_begin 也拒绝");
        CHECK(counters_bus_reads() == reads_before, "H4f R02: 全程零总线动作");
        shim_mutex_init_fail_prefix = 0;
        tmc_cfg_prod_ready   = RT_FALSE;
        CHECK(tmc_cfg_prod_sync() == RT_EOK, "H4g R02: 恢复后锁可用");
        CHECK(tmc2209_cfg_txn_begin() == RT_EOK, "H4h 恢复后 txn_begin 可用");
        tmc2209_cfg_txn_end();
    }

    /* H5: arm 必须在请求使能之前就拒绝 */
    en_before = shim_drv_enable_write_calls;
    e = motor_arm();
    CHECK(e == -RT_EPERM && shim_drv_enable_write_calls == en_before,
          "H5: motor_arm=%d 被拒, 且从未发出 DRV_ENABLE 写(调用次数=%d)",
          (int)e, en_before);

    /* H6: start 在未 armed 时必须拒绝, 且不得进 RUN */
    run_before = shim_transition_to_run;
    e = motor_start();
    CHECK(e != RT_EOK && shim_transition_to_run == run_before,
          "H6: motor_start=%d 被拒, READY->RUN 未发生", (int)e);

    /* ================= F2d: 诊断写入口收口(V1 发布配置 = 三宏 FALSE) =================
     * 断言的主体从"总调用次数"升级为"帧级": WRITE 帧数为 0 才是"没有写"。 */

    /* FD1 先证明这台仪器看得见 WRITE 帧 —— 否则后面所有"0 个写帧"都可能是瞎报。
     * 手法: 启用假芯片, 直接调用本文件私有的确认写入口(它正是旧 probe 走的那步:
     * 一次真实写)。夹具侧只写主机的内存模型, 不存在任何硬件。 */
    shim_chip_boot();
    shim_chip_enabled = 1;
    {
        int rr0 = shim_frame_read_reqs, wf0 = shim_frame_write_frames;
        unsigned ifcnt0 = shim_chip[0x02];

        e = tmc2209_write_register_confirmed(TMC_ADDR_DEFAULT, TMC_REG_GCONF,
                                             shim_chip[0x00]);
        CHECK(e == RT_EOK &&
              shim_frame_read_reqs - rr0 == 2 &&          /* IFCNT 前后各读一次 */
              shim_frame_write_frames - wf0 == 1 &&        /* 恰好 1 个 WRITE 帧 */
              shim_write_frames_by_reg[0x00] == 1 &&
              shim_chip[0x02] == ((ifcnt0 + 1u) & 0xFFu),  /* 芯片侧 IFCNT +1 */
              "FD1 仪器自检: 确认写入口 = 2 次读请求 + 1 个 WRITE 帧(IFCNT %u->%u), "
              "旧 probe 的写回正是这个形状", (unsigned)ifcnt0, (unsigned)shim_chip[0x02]);
    }

    /* FD2 链路死了: probe 第一条读就失败即退出 -> 1 个读请求, 0 个 WRITE 帧。
     * ⚠ 这只证明"从一开始就完全无应答"这一条路径的帧形状; **不**构成占用时长上界
     *   (读路径先无限等 tmc_xfer_lock, IOIN 成功后 GSTAT/IFCNT 仍可能各走满一次超时),
     *   守卫/motor_arm 会不会被长时间挡住仍未测量 => 保持 RISK-03。F2d-R2 勘误。 */
    shim_chip_enabled = 0;
    {
        int rr0 = shim_frame_read_reqs, wf0 = shim_frame_write_frames;

        tmc_uart_probe();
        CHECK(shim_frame_write_frames == wf0 && shim_frame_read_reqs - rr0 == 1,
              "FD2 probe(死链路): WRITE 帧 %d 个(应 0), 读请求 %d 个后 fail-closed 退出",
              shim_frame_write_frames - wf0, shim_frame_read_reqs - rr0);
    }

    /* FD3 链路活着: probe 走完三条读, 仍然 0 个 WRITE 帧, 芯片寄存器与 IFCNT 分毫
     * 未动(IFCNT 只对 WRITE 自增, 所以它没涨 = 确实没有写帧到达, 不是"写了没生效") */
    shim_chip_boot();
    shim_chip_enabled = 1;
    {
        int rr0 = shim_frame_read_reqs, wf0 = shim_frame_write_frames;
        unsigned g0 = shim_chip[0x00], ifc0 = shim_chip[0x02], gs0 = shim_chip[0x01];

        tmc_uart_probe();
        CHECK(shim_frame_write_frames - wf0 == 0 && shim_frame_read_reqs - rr0 == 3 &&
              shim_chip[0x00] == g0 && shim_chip[0x01] == gs0 &&
              shim_chip[0x02] == ifc0,
              "FD3 probe(活链路): 读请求 %d(应 3) WRITE 帧 %d(应 0) "
              "芯片 GCONF=%08X->%08X IFCNT=%u->%u",
              shim_frame_read_reqs - rr0, shim_frame_write_frames - wf0,
              (unsigned)g0, (unsigned)shim_chip[0x00],
              (unsigned)ifc0, (unsigned)shim_chip[0x02]);
    }

    /* FD4 并发: 另一线程真的持有 prod 的 txn 时, probe 必须一条总线都不碰。
     * 这就是"探测与配置交错"的结构性封堵 —— 交错不发生, 而不是交错发生了但无害。
     * R03: 辅助线程/持锁确认任一环节没成, 关键断言根本没跑过, 必须记 MISSING,
     * 不能由"FD4b 清理没报错"顶替"并发用例已执行"。 */
    {
        int infra = 1, busy = tmc_fx_busy_setup(&tmc_cfg_prod, &infra);
        int rr0 = shim_frame_read_reqs, wf0 = shim_frame_write_frames,
            op0 = shim_dev_open_calls;

        if (busy)
        {
            g_fd4_executed = 1;
            tmc_uart_probe();
            CHECK(shim_frame_read_reqs == rr0 && shim_frame_write_frames == wf0 &&
                  shim_dev_open_calls == op0,
                  "FD4 txn 被占时 probe: 读请求/写帧/open 全部不变(%d/%d/%d) 拒绝而非插入",
                  shim_frame_read_reqs - rr0, shim_frame_write_frames - wf0,
                  shim_dev_open_calls - op0);
            if (!tmc_fx_hold_release_wait()) infra = 0;
        }
        CHECK(infra == 1, "FD4b 辅助线程清理已确认(不则判 FAIL, 不把夹具竞态当业务)");
        require_ran("FD4 probe 遇 txn 占用(前置: 另一线程已确认持锁)", g_fd4_executed);
    }

    /* FD5 失败路径: 事务锁不可用(能力缺失)时 probe 同样 fail-closed, 零总线流量 */
    {
        int rr0 = shim_frame_read_reqs, wf0 = shim_frame_write_frames;

        (void)rt_mutex_detach(&tmc_cfg_prod.lock);
        (void)rt_mutex_detach(&tmc_cfg_prod.txn);
        tmc_cfg_prod.lock_ok = RT_FALSE;
        tmc_cfg_prod.txn_ok  = RT_FALSE;
        tmc_cfg_prod_ready   = RT_FALSE;
        tmc_uart_probe();
        CHECK(shim_frame_read_reqs == rr0 && shim_frame_write_frames == wf0,
              "FD5 无事务锁时 probe 零总线动作(读 %d 写 %d 未变)",
              shim_frame_read_reqs - rr0, shim_frame_write_frames - wf0);
        shim_mutex_init_fail_prefix = 0;
        CHECK(tmc_cfg_prod_sync() == RT_EOK, "FD5b 恢复后事务锁可用");
    }

    /* FD6 GSTAT 解码真值表: 纯解码, 不写寄存器(F2c-NEW-02)。
     * 期望值逐条来自官方 Rev.1.09 **印刷第 24 页** GSTAT 寄存器表(bit0 reset /
     * bit1 drv_err / bit2 uv_cp; 只定义低 3 位; 三位都是写 1 清除, 不是读清),
     * 证据等级 DATASHEET-CONFIRMED, 非本板实测。TMC2209 的 SG_RESULT 是 StallGuard4
     * 的结果, 与 GSTAT 无关(F2d-R1 勘误: 上一版这里写的"第 25 页"是页码笔误)。 */
    {
        static const unsigned din[] = {
            0x00000000u, 0x00000001u, 0x00000002u, 0x00000004u, 0x00000007u,
            0x00000008u, 0x000000FFu, 0x80000004u, 0x00000100u
        };
        static const unsigned char exp[9][4] = {
            { 0, 0, 0, 0 }, { 1, 0, 0, 0 }, { 0, 1, 0, 0 }, { 0, 0, 1, 0 },
            { 1, 1, 1, 0 }, { 0, 0, 0, 1 }, { 1, 1, 1, 1 }, { 0, 0, 1, 1 },
            { 0, 0, 0, 1 }
        };
        int rr0 = shim_frame_read_reqs, wf0 = shim_frame_write_frames;
        int i, bad = 0;

        for (i = 0; i < 9; ++i)
        {
            tmc_gstat_dec_t gs;

            tmc_gstat_decode(din[i], &gs);
            if (gs.reset != exp[i][0] || gs.drv_err != exp[i][1] ||
                gs.uv_cp != exp[i][2] || gs.unknown != exp[i][3])
            {
                printf("    [DECODE] %08X -> r=%u d=%u uv=%u unk=%u (期望 %u/%u/%u/%u)\n",
                       din[i], gs.reset, gs.drv_err, gs.uv_cp, gs.unknown,
                       exp[i][0], exp[i][1], exp[i][2], exp[i][3]);
                bad++;
            }
        }
        CHECK(bad == 0 && shim_frame_read_reqs == rr0 &&
              shim_frame_write_frames == wf0,
              "FD6 GSTAT 解码 9/9 且零总线动作(bit2 名为 uv_cp, 不再是 sg2) 错 %d 条", bad);
    }
#else
    /* ================= V2: 主机侧正向覆盖(仅主机, 非发布配置) ================= */
    printf("\n[C] V2 正向覆盖(主机把三个宏当 TRUE 编译, 生产实例接假芯片)\n");

    CHECK(motor_pwm_grant_to_diag() == RT_EOK,
          "C1 P0-A: 隔离环境确认后 STEP 借用被允许(证明拒绝来自那两条宏)");

    host_prod_use_fake_chip();
    e = tmc2209_apply_config();
    CHECK(e == RT_EOK && tmc2209_get_config_state() == TMC_DRV_CONFIGURED &&
          tmc2209_drive_config_ok_for_arm() == RT_TRUE,
          "C2: 门禁全通 + 假芯片 -> apply=%d, state=%s, ok_for_arm=%d",
          (int)e, tmc_drv_cfg_state_name(tmc2209_get_config_state()),
          (int)tmc2209_drive_config_ok_for_arm());

    e = motor_arm();
    CHECK(e == RT_EOK && shim_last_drv_enable_level == 1,
          "C3: motor_arm=%d, 桩侧最后请求电平=%d(应为 1)", (int)e, shim_last_drv_enable_level);

    (void)motor_set_target_hz(1000u);        /* 不设目标就永远轮不到复核(第一轮执行发现) */
    run_before = shim_transition_to_run;
    e = motor_start();
    CHECK(e == RT_EOK && shim_transition_to_run == run_before + 1,
          "C4 R03: 起转前实值复核通过 -> 进入 RUN(to_run %d -> %d)",
          run_before, shim_transition_to_run);

    /* C5: 运行期守卫 —— 注入"模块断电重上电(寄存器回默认)", 等守卫发现并停机 */
    {
        DWORD t0, dt_ms;
        int   i, pdis_before, evt_before;

        pdis_before = shim_pwm_disable_calls;
        evt_before  = shim_post_event_calls;
        g_fx.gconf = 0x00000101u;            /* 模拟 TMC 模块断电后重新上电 */
        g_fx.chop  = 0x15010053u;
        t0 = GetTickCount();
        for (i = 0; i < 60; ++i)             /* 最多等 6s */
        {
            motor_snapshot_t s;
            motor_get_snapshot(&s);
            if (s.state == MOTOR_FAULT && s.armed == 0) break;
            rt_thread_mdelay(100);
        }
        dt_ms = GetTickCount() - t0;
        CHECK(mot.guard_stops >= 1u && mot.guard_reason == MOT_GUARD_INTEGRITY,
              "C5a P1-E: 守卫发现芯片复位 -> 停机(stops=%u reason=%u)",
              (unsigned)mot.guard_stops, (unsigned)mot.guard_reason);
        CHECK(mot.armed == 0 && mot.current_hz == 0 && mot.target_hz == 0,
              "C5b: armed=0 hz=0 target=0");
        CHECK(shim_pwm_disable_calls > pdis_before,
              "C5c: STEP 已关(pwm_disable %d -> %d)", pdis_before, shim_pwm_disable_calls);
        CHECK(shim_last_drv_enable_level == 0,
              "C5d: DRV_ENABLE 请求 LOW(桩实测电平=%d)", shim_last_drv_enable_level);
        CHECK(shim_post_event_calls > evt_before,
              "C5e: 已上报软故障事件(post_event %d -> %d)", evt_before, shim_post_event_calls);
        CHECK(tmc2209_drive_config_ok_for_arm() == RT_FALSE,
              "C5f: 第 5 门随之闭合(ok_for_arm=0, state=%s)",
              tmc_drv_cfg_state_name(tmc2209_get_config_state()));
        printf("  [INFO] R05 相位测量(主机): 注入复位 -> 守卫停机耗时 %u ms"
               " (周期=%ums, 主机假总线瞬时, 真机还要叠加 UART 超时预算)\n",
               (unsigned)dt_ms, (unsigned)MOTOR_DRV_GUARD_PERIOD_MS);
    }

    run_before = shim_transition_to_run;
    e = motor_start();
    CHECK(e != RT_EOK && shim_transition_to_run == run_before,
          "C6: 守卫停机后 start 被拒(%d), 不会自己复活运动", (int)e);

    /* C7: 事务被占时 motor_arm 必须 fail closed(真起一个持锁者)。
     * R03: 同 FD4 —— 前置条件没成立就是没执行, 必须记 MISSING。 */
    {
        int infra = 1, busy;

        busy = tmc_fx_busy_setup(&tmc_cfg_prod, &infra);
        if (busy)
        {
            g_c7_executed = 1;
            e = motor_arm();
            CHECK(e == -RT_EBUSY || e == -RT_ENOSYS,
                  "C7 R02/R03: 配置事务被另一线程占用时 motor_arm=%d 被拒", (int)e);
            if (!tmc_fx_hold_release_wait()) infra = 0;
        }
        CHECK(infra == 1, "C7b R06: 辅助线程清理已确认(IPC 未泄漏)");
        require_ran("C7 motor_arm 遇 txn 占用(前置: 另一线程已确认持锁)", g_c7_executed);
    }

    /* ============ FD7/F2d(V2): 配置已提交时, 诊断不得让快照与芯片分离 ============
     * 把生产实例接回**真实总线**(夹具的假芯片会应答), 只把运行态门禁置全通。
     * 于是 apply 的四次写真的变成 4 个 WRITE 帧落到内存模型上, probe 的读也真的走
     * 协议层。这里能成立的东西仍然只是逻辑时序, 不是实时性(主机无 UART 超时)。 */
    {
        int wf0, rr0;
        unsigned gchip;

        shim_chip_boot();
        shim_chip_enabled = 1;
        tmc_cfg_prod.bus  = &tmc_cfg_bus_real;
        tmc_cfg_prod.priv = RT_NULL;
        tmc_cfg_prod.gate = host_gate_open;        /* 仅主机: 门禁全通 */
        g_host_gate_mask  = 0u;
        tmc_cfg_prod.state = TMC_DRV_UNCONFIGURED;
        tmc_cfg_prod.done_valid = RT_FALSE;

        e = tmc2209_apply_config();
        CHECK(e == RT_EOK && tmc_cfg_prod.state == TMC_DRV_CONFIGURED &&
              tmc_cfg_prod.done_gconf == TMC_CFG_TGT_GCONF &&
              shim_chip[0x00] == (unsigned)TMC_CFG_TGT_GCONF &&
              shim_frame_write_frames == 4 && shim_write_frames_by_reg[0x00] == 1,
              "FD7a 真总线+假芯片: apply=%d 提交 4 个 WRITE 帧, 芯片 GCONF=%08X = 快照",
              (int)e, (unsigned)shim_chip[0x00]);

        wf0 = shim_frame_write_frames;
        rr0 = shim_frame_read_reqs;
        gchip = shim_chip[0x00];
        tmc_uart_probe();
        CHECK(shim_frame_write_frames == wf0 && shim_chip[0x00] == gchip &&
              shim_frame_read_reqs - rr0 == 3,
              "FD7b 已提交快照后再 probe: 写帧增量 %d(应 0) 读请求 %d(应 3) "
              "芯片 GCONF 仍 =%08X", shim_frame_write_frames - wf0,
              shim_frame_read_reqs - rr0, (unsigned)shim_chip[0x00]);
        CHECK(tmc2209_config_integrity_check() == RT_EOK &&
              tmc2209_drive_config_ok_for_arm() == RT_TRUE,
              "FD7c probe 之后复核仍通过, 第 5 门仍放行(快照与芯片未分离)");
    }

    /* ============ FD8/F2d(V2): 旧故障形状的复现与可检测性 ============
     * 用现在**只有本 TU 能碰到**的确认写入口, 把并发交错时旧 probe 会写回的那个
     * 旧值 0x101 写下去(等价于"F2c 的 probe 与 apply 交错"造成的后果)。快照立刻
     * 与芯片现值分离 -> 复核必须照出来并把门闭合。F2d 之后诊断命令到不了这一步,
     * 因为写帧的唯一发起点是文件私有的, 且只在持有 txn + 过完门禁的序列里被调用。 */
    {
        e = tmc2209_write_register_confirmed(TMC_ADDR_DEFAULT, TMC_REG_GCONF,
                                             0x00000101u);
        CHECK(e == RT_EOK && shim_chip[0x00] == 0x00000101u,
              "FD8a 旧值写回成功(芯片=%08X, 快照=%08X) —— 分离确实发生了",
              (unsigned)shim_chip[0x00], (unsigned)tmc_cfg_prod.done_gconf);
        CHECK(tmc2209_config_integrity_check() == -RT_EIO &&
              tmc_cfg_prod.state == TMC_DRV_FAILED &&
              tmc_cfg_prod.done_valid == RT_FALSE &&
              tmc2209_drive_config_ok_for_arm() == RT_FALSE,
              "FD8b 复核抓到分离: integrity=-RT_EIO state=%s 快照作废 第 5 门闭合",
              tmc_drv_cfg_state_name(tmc_cfg_prod.state));
        e = tmc2209_apply_config();                 /* 重新下发即恢复(与 T20 同形) */
        CHECK(e == RT_EOK && tmc_cfg_prod.state == TMC_DRV_CONFIGURED &&
              shim_chip[0x00] == (unsigned)TMC_CFG_TGT_GCONF,
              "FD8c 重新下发恢复: apply=%d 芯片=%08X 快照=%08X",
              (int)e, (unsigned)shim_chip[0x00], (unsigned)tmc_cfg_prod.done_gconf);
    }
#endif

    /* R01/R02/R03: 进程返回码把三类问题分开, 任何一类非零都不许报"通过"。
     *   0=通过; 1=有判据失败; 2=必需自测终态不合规(FAILED/INCOMPLETE/无终态);
     *   3=必需用例没执行(含关键并发的前置没成立); 4=一项都没跑成。 */
    {
        int rc = 0;

        printf("\n=========================================================\n");
        printf("HOST TEST SUMMARY: CHECK_PASS=%d CHECK_FAIL=%d BAD_SELFTEST=%d MISSING_CASE=%d\n",
               g_pass, g_fail, g_bad_result, g_missing_case);
        if (g_fail == 0 && g_bad_result == 0 && g_missing_case == 0 && g_pass > 0)
        {
            printf("结论: ALL PASS (判据 + 固件自测终态 + 必需用例齐备性 全部满足)\n");
        }
        else
        {
            printf("结论: NOT PASSED ->");
            if (g_fail)         { printf(" 判据失败 %d 项;", g_fail); rc = 1; }
            if (g_bad_result)   { printf(" 必需自测终态不合规 %d 项;", g_bad_result);
                                  if (!rc) rc = 2; }
            if (g_missing_case) { printf(" 必需用例未执行 %d 项;", g_missing_case);
                                  if (!rc) rc = 3; }
            if (!rc)            { rc = 4; printf(" 没有任何通过项(rc=4);"); }
            printf(" 详见上方 [FAIL]/[BADRESULT]/[MISSING] 行\n");
        }
        printf("PROCESS RC=%d (0=通过)\n", rc);
        printf("=========================================================\n");
        fflush(stdout);
        return rc;
    }
}
