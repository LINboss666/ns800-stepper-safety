/*
 * ps_main.c - 关断结果可信链 (phase8-stop-result-s1, 2026-10-23)
 *
 * 真编译并执行**生产的**三个模块(任务书 §6 要求的"真实路径"):
 *     applications/step_pwm.c     STEP 诊断输出 + 唯一的 checked 关断路径
 *     applications/motor.c        斜坡/运行守卫/arm/disarm/急停 + 关断结果记账
 *     applications/safety_state.c 统一停机 + 故障锁存 + 人工恢复清除
 * 其余依赖(RT 原语、安全 GPIO、安全线程、TMC、传感器、存储、黑匣子)是本文件里的
 * **显式可注入假服务**。与另外三条链并列, 不复用它们的夹具。
 *
 * 桩的三条关键纪律(任务书 §6):
 *   1. PWM 每个 API 的返回码可独立注入, 并且**独立**保存"模拟输出是否仍在运行":
 *      rt_pwm_disable 失败时桩保留输出运行 —— 桩绝不替固件把输出清掉。
 *      motor.c 与 step_pwm.c 拿到同一个 epwm1/ch0 句柄, 所以这个 running 标志就是
 *      那条物理通道的单一真相, 能照出"两路都想关但有一路没关掉"。
 *   2. 许可(ENABLE)写结果、许可脚实测电平、ENN 反馈判定是**三个独立注入量**
 *      (ps_set_enable_write_rc / ps_sim_set_pad_level / ps_set_fb*),
 *      不用同一个布尔同时代表所有"成功"。
 *   3. 前置"已关断"一律由生产路径自己拿到(真调 force_stop/disarm 并让桩返回成功),
 *      不直接改写私有状态伪造成功过门。锁存的解除只走 fault_reset() 的人工恢复。
 *
 * 判定纪律: 只用生产 API 返回值/状态 + ps_sim_* 计数。两处例外是任务书明文要求的
 * "输出文本本身就是被测行为": PS05 的 step_status 不得写 "no(安全)"、
 * PS06 的 force_shutdown 不得写 "STEP stopped"。除此之外不做全局文本搜索。
 *
 * 本链**单线程、确定性**: rt_thread_create 只计次数、不起线程; 斜坡的一拍由本文件直接
 * 调用生产 motor_ramp_tick() 单步执行(等价性论证见 motor.c 的 S1 注释)。
 * 因此本链不模拟调度竞争、不测停机时延 —— 那是 S2, 本轮不声称已验证。
 *
 * 变体(runner 用 -D 传入, 不改生产配置):
 *   target    四道硬件宏全部 RT_FALSE = 目标发布配置
 *   host_ovr  -DPS_GATE_HOST_OVERRIDE + 三宏 RT_TRUE(仅主机, 用于走通 arm/start/斜坡)
 *
 * 退出码: 0=全部 PASS; 1=检出业务失败(有用例 FAIL); 2=夹具前置不自洽(不该发生)
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>

#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "safety_state.h"
#include "safety_thread.h"
#include "stop_result.h"
#include "step_pwm.h"
#include "motor.h"
#include "tmc2209.h"
#include "adxl345.h"
#include "current_adc.h"
#include "ns_flash.h"
#include "blackbox.h"

/* ===================================================================== */
/* rt_kprintf 重定向: 照常打印 + 落入捕获缓冲(只给两条"输出即行为"的用例)  */
/* ===================================================================== */
#undef rt_kprintf
#define rt_kprintf ps_kprintf

#define PS_CAP_SIZE 8192
static char ps_cap[PS_CAP_SIZE];
static int  ps_cap_len;

static void ps_cap_clear(void) { ps_cap_len = 0; ps_cap[0] = '\0'; }

static void ps_kprintf(const char *fmt, ...)
{
    /* 控制台在目标上是逐字符忙等(每字符 ≤BSP_NS800_UART_TX_TIMEOUT=6000ms), 属于
     * 短窗口内的禁止项; 这里只**观察**(掩码为 1 才计数), 不改任何行为。 */
    ps_win_note("rt_kprintf");
    va_list ap;
    char tmp[1024];
    int n;

    va_start(ap, fmt);
    vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    fputs(tmp, stdout);
    n = (int)strlen(tmp);
    if (ps_cap_len + n >= PS_CAP_SIZE)
    {
        int keep = PS_CAP_SIZE / 2;
        memmove(ps_cap, ps_cap + (ps_cap_len - keep), (size_t)keep);
        ps_cap_len = keep;
    }
    memcpy(ps_cap + ps_cap_len, tmp, (size_t)n);
    ps_cap_len += n;
    ps_cap[ps_cap_len] = '\0';
}

static int ps_cap_has(const char *needle) { return strstr(ps_cap, needle) != RT_NULL; }

/* ===================================================================== */
/* RT 原语(单线程夹具: 不起线程、不阻塞)                                  */
/* ===================================================================== */
static int c_mutex_take, c_mutex_release, c_event_send, c_thread_create, c_thread_startup;
static rt_uint32_t c_event_bits, c_event_last;
static struct rt_thread c_thread_store[4];

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{ (void)flags; if (m) { m->name = name; m->inited = RT_TRUE; } return RT_EOK; }
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t t)
{ (void)m; (void)t; c_mutex_take++; ps_win_note("rt_mutex_take"); return RT_EOK; }
rt_err_t rt_mutex_release(struct rt_mutex *m)
{ (void)m; c_mutex_release++; return RT_EOK; }
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag)
{ (void)flag; if (e) { e->name = name; e->inited = RT_TRUE; } return RT_EOK; }
static rt_err_t s_event_send_rc = RT_EOK;
void ps_set_event_send_rc(rt_err_t rc) { s_event_send_rc = rc; }
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event)
{
    rt_err_t r = s_event_send_rc;
    (void)e; c_event_send++; c_event_bits |= event; c_event_last = event;
    s_event_send_rc = RT_EOK;
    return r;
}
rt_err_t rt_event_recv(struct rt_event *e, rt_uint32_t ev, rt_uint32_t fl,
                       rt_int32_t to, rt_uint32_t *out)
{ (void)e; (void)ev; (void)fl; (void)to; if (out) *out = 0; return -RT_ETIMEOUT; }
rt_err_t rt_event_detach(struct rt_event *e) { (void)e; return RT_EOK; }

rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick)
{
    (void)stack_size; (void)prio; (void)tick;
    if (c_thread_create >= (int)(sizeof(c_thread_store)/sizeof(c_thread_store[0])))
        return RT_NULL;                       /* 名额用完: 真失败也如实返回 */
    c_thread_store[c_thread_create].name  = name;
    c_thread_store[c_thread_create].entry = entry;
    c_thread_store[c_thread_create].param = param;
    c_thread_create++;                        /* 只计数: 本链不真的起线程 */
    return &c_thread_store[c_thread_create - 1];
}
rt_err_t rt_thread_startup(rt_thread_t t) { (void)t; c_thread_startup++; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t)  { (void)t; return RT_EOK; }

/* ===================================================================== */
/* 设备 / PWM / GPIO 仿真                                                  */
/* ===================================================================== */
#define PS_MAX_PINS 10

struct ps_pin
{
    const char *name;
    rt_int32_t  level;
    rt_int32_t  last_write;
    rt_base_t   last_mode;
    int         writes, reads, present;
};
static struct ps_pin pins[PS_MAX_PINS];
static int n_pins;

static struct rt_device_pwm g_pwm_dev;
static int       dev_present = 1;
static rt_err_t  rc_set = RT_EOK, rc_enable = RT_EOK, rc_disable = RT_EOK;
static int       calls_set, calls_enable, calls_disable;
static int       out_running;          /* 模拟输出是否仍在跑(桩的独立真相) */
static int       disable_seq_fail_left;/* R04: 前 N 次 disable 强制返回 disable_seq_rc */
static rt_err_t  disable_seq_rc = -RT_EIO;
static int       pwm_null_calls;         /* 生产代码拿 NULL 句柄调 PWM 的次数(必须恒 0) */

/* 许可写 / 许可脚实测 / ENN 反馈: 三个独立注入量 */
static rt_err_t  enable_write_rc = RT_EOK;
static rt_err_t  last_enable_ret = RT_EOK;   /* 桩留档: 上一次许可写的真实返回值 */
static rt_int32_t pad_level = PIN_LOW;
static int       pad_stuck;
static int       writes_low, writes_high;
static safety_fb_verdict_t fb_dis = SAFETY_FB_OK, fb_en = SAFETY_FB_OK;
static rt_err_t  fb_confirm_rc = RT_EOK;
static int       fb_confirm_calls, fb_now_calls;
static int       confirm_fail_after = -1;    /* 前 N 次成功, 之后失败(造"后置复核才变坏") */

static int       bb_triggers;
static int       protect_ready = 1, irq_attached = 1, polarity_ok = 1, gpio_ready = 1;
static rt_bool_t f_cfg_ok = RT_TRUE;
static tmc_drv_cfg_state_t f_cfg_state = TMC_DRV_CONFIGURED;
static subsys_health_t     f_tmc_health = SUBSYS_OK;
static rt_err_t  f_integrity = RT_EOK;
static int       cfg_txn_begins, cfg_txn_ends, fake_bus_reads;
static tmc_drv_config_t f_applied;

rt_device_t rt_device_find(const char *name)
{
    if (dev_present && rt_strcmp(name, EPWM_STEP_DEV_NAME) == 0)
        return (rt_device_t)&g_pwm_dev;
    return RT_NULL;
}

rt_err_t rt_pwm_set(struct rt_device_pwm *p, int ch, rt_uint32_t per, rt_uint32_t pul)
{ (void)ch; (void)per; (void)pul; calls_set++; if (p == RT_NULL) pwm_null_calls++; return rc_set; }

rt_err_t rt_pwm_enable(struct rt_device_pwm *p, int ch)
{
    (void)ch; calls_enable++; if (p == RT_NULL) pwm_null_calls++;
    if (rc_enable == RT_EOK) out_running = 1;     /* 只有 enable 成功才认为"输出在跑" */
    return rc_enable;
}

/* ⚠ 桩语义核心: disable 失败时**不动** out_running —— 桩不替固件把输出清掉。
 * "软件自称已停但模拟侧还在跑"会被 ps_sim_pwm_running() 直接照出来。
 * R04(2026-10-23 返修)追加"前 N 次失败、其后成功"的逐次序列(默认关闭), 用来
 * 照出"同一个停机动作里第二次关断覆盖第一次失败"的形状。 */
rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    rt_err_t e = rc_disable;

    (void)p; (void)ch; calls_disable++;
    if (p == RT_NULL) pwm_null_calls++;
    if (disable_seq_fail_left > 0)
    {
        disable_seq_fail_left--;
        e = disable_seq_rc;
    }
    if (e == RT_EOK) out_running = 0;
    return e;
}

static int pin_find(const char *name)
{
    int i;
    for (i = 0; i < n_pins; ++i)
        if (pins[i].present && rt_strcmp(pins[i].name, name) == 0) return i;
    return -1;
}

rt_base_t rt_pin_get(const char *name)
{
    int i = pin_find(name);
    return (i < 0) ? -RT_EINVAL : (rt_base_t)i;
}
void rt_pin_mode(rt_base_t pin, rt_base_t mode)
{ if (pin >= 0 && pin < (rt_base_t)n_pins) pins[pin].last_mode = mode; }
void rt_pin_write(rt_base_t pin, rt_base_t value)
{
    if (pin < 0 || pin >= (rt_base_t)n_pins) return;
    pins[pin].writes++;
    pins[pin].last_write = value;
    if (rt_strcmp(pins[pin].name, PIN_NAME_DRV_ENABLE) == 0 && !pad_stuck)
        pad_level = value;                      /* 焊盘跟着写走; stuck 时不跟 */
}
rt_base_t rt_pin_read(rt_base_t pin)
{
    if (pin < 0 || pin >= (rt_base_t)n_pins) return -RT_EINVAL;
    pins[pin].reads++;
    return pins[pin].level;
}

/* ---------- 测试侧控制与观测 ---------- */
void ps_sim_add_pin(const char *name, rt_int32_t lv)
{
    if (n_pins >= PS_MAX_PINS) return;
    pins[n_pins].name = name; pins[n_pins].level = lv;
    pins[n_pins].last_write = -1; pins[n_pins].last_mode = -999;
    pins[n_pins].writes = 0; pins[n_pins].reads = 0; pins[n_pins].present = 1;
    n_pins++;
}
void ps_sim_remove_pin(const char *name)
{ int i = pin_find(name); if (i >= 0) pins[i].present = 0; }
void ps_sim_set_level(const char *name, rt_int32_t lv)
{ int i = pin_find(name); if (i >= 0) pins[i].level = lv; }
rt_int32_t ps_sim_level(const char *name)
{ int i = pin_find(name); return (i < 0) ? -1 : pins[i].level; }

void ps_sim_dev_set_present(int on)  { dev_present = on; }
void ps_sim_pwm_set_rc(rt_err_t s, rt_err_t e, rt_err_t d)
{ rc_set = s; rc_enable = e; rc_disable = d; }
int  ps_sim_pwm_set_calls(void)      { return calls_set; }
int  ps_sim_pwm_enable_calls(void)   { return calls_enable; }
int  ps_sim_pwm_disable_calls(void)  { return calls_disable; }
int  ps_sim_pwm_running(void)        { return out_running; }
void ps_sim_pwm_force_running(int on){ out_running = on; }
/* R04 用: 接下来 n 次 rt_pwm_disable 返回 fail_rc, 之后的回到 ps_sim_pwm_set_rc 的值 */
void ps_sim_pwm_disable_seq(int n, rt_err_t fail_rc)
{ disable_seq_fail_left = n; disable_seq_rc = fail_rc; }
int ps_sim_pwm_disable_seq_left(void) { return disable_seq_fail_left; }
int ps_sim_pwm_null_calls(void)       { return pwm_null_calls; }

void ps_sim_set_pad_stuck(int on)     { pad_stuck = on; }
void ps_sim_set_pad_level(rt_int32_t lv) { pad_level = lv; }
int  ps_sim_pin_write_calls(const char *name)
{ int i = pin_find(name); return (i < 0) ? 0 : pins[i].writes; }
rt_int32_t ps_sim_last_write(const char *name)
{ int i = pin_find(name); return (i < 0) ? -1 : pins[i].last_write; }
int  ps_sim_pin_read_calls(const char *name)
{ int i = pin_find(name); return (i < 0) ? 0 : pins[i].reads; }
int  ps_sim_thread_create_calls(void)  { return c_thread_create; }
int  ps_sim_thread_startup_calls(void) { return c_thread_startup; }
int  ps_sim_mutex_takes(void)          { return c_mutex_take; }

void ps_set_enable_write_rc(rt_err_t rc) { enable_write_rc = rc; }
int  ps_enable_write_calls(int want_high) { return want_high ? writes_high : writes_low; }
void ps_set_fb(rt_uint8_t want_enabled, safety_fb_verdict_t v)
{ if (want_enabled) fb_en = v; else fb_dis = v; }
void ps_set_fb_confirm_rc(rt_err_t rc) { fb_confirm_rc = rc; }
void ps_set_confirm_fail_after(int n)  { confirm_fail_after = n; fb_confirm_calls = 0; }
int  ps_fb_confirm_calls(void)         { return fb_confirm_calls; }
int  ps_bb_triggers(void)              { return bb_triggers; }
int  ps_event_sends(void)              { return c_event_send; }
rt_uint32_t ps_event_bits(void)        { return c_event_bits; }
rt_uint32_t ps_event_last(void)        { return c_event_last; }
void ps_event_bits_clear(void)         { c_event_bits = 0; }
void ps_set_protect(int r, int i, int p, int g)
{ protect_ready = r; irq_attached = i; polarity_ok = p; gpio_ready = g; }
void ps_set_integrity(rt_err_t e) { f_integrity = e; }
void ps_set_cfg_ok(rt_bool_t ok, tmc_drv_cfg_state_t st) { f_cfg_ok = ok; f_cfg_state = st; }
void ps_set_tmc_health(subsys_health_t h) { f_tmc_health = h; }
int  ps_cfg_txn_begins(void) { return cfg_txn_begins; }
int  ps_cfg_txn_ends(void)   { return cfg_txn_ends; }
int  ps_fake_bus_reads(void) { return fake_bus_reads; }

static int s_isr_nest;      /* 由 ps_isr_set() 显式置位: 模拟 ISR 上下文 */
static int s_thread_null;   /* 由 ps_thread_null_set() 显式置位: 无线程对象 */

void ps_sim_reset(void)
{
    n_pins = 0;
    dev_present = 1;
    rc_set = rc_enable = rc_disable = RT_EOK;
    calls_set = calls_enable = calls_disable = 0;
    out_running = 0;
    disable_seq_fail_left = 0; disable_seq_rc = -RT_EIO; pwm_null_calls = 0;
    enable_write_rc = RT_EOK; last_enable_ret = RT_EOK; pad_level = PIN_LOW; pad_stuck = 0;
    writes_low = writes_high = 0;
    fb_dis = fb_en = SAFETY_FB_OK; fb_confirm_rc = RT_EOK;
    fb_confirm_calls = fb_now_calls = 0; confirm_fail_after = -1;
    bb_triggers = 0;
    c_mutex_take = c_mutex_release = c_event_send = 0; c_event_bits = 0; c_event_last = 0;
    s_event_send_rc = RT_EOK;
    s_isr_nest = 0;
    s_thread_null = 0;
    c_thread_create = c_thread_startup = 0;
    protect_ready = irq_attached = polarity_ok = gpio_ready = 1;
    f_cfg_ok = RT_TRUE; f_cfg_state = TMC_DRV_CONFIGURED; f_tmc_health = SUBSYS_OK;
    f_integrity = RT_EOK; cfg_txn_begins = cfg_txn_ends = fake_bus_reads = 0;
    ps_cap_clear();
}

/* ===================================================================== */
/* 假服务: safety_gpio.c / safety_thread.c / tmc2209.c / 传感器 / 存储 / 黑匣子 */
/* ===================================================================== */
rt_bool_t safety_gpio_ready(void) { return gpio_ready ? RT_TRUE : RT_FALSE; }
rt_err_t  safety_gpio_boot(void)  { return RT_EOK; }

rt_base_t safety_pin(const char *name)
{
    int i = pin_find(name);
    return (i < 0) ? (rt_base_t)-1 : (rt_base_t)i;
}

/* 许可脚"写 + 回读确认": 写结果与焊盘实测是两个独立量 */
rt_err_t safety_drv_enable_write(rt_uint8_t level)
{
    rt_base_t p = safety_pin(PIN_NAME_DRV_ENABLE);
    rt_err_t ret;

    if (level) writes_high++; else writes_low++;
    if (p < 0) return (last_enable_ret = -RT_EINVAL);
    rt_pin_write(p, level);
    if (enable_write_rc != RT_EOK) ret = enable_write_rc;
    else ret = (pad_level == (rt_int32_t)level) ? RT_EOK : -RT_EIO;
    last_enable_ret = ret;
    return ret;
}

/* 桩侧留档的**许可写**原始返回值(与快照里的 stop_rc 分列, 两者互不替代) */
rt_err_t ps_last_enable_write_rc(void) { return last_enable_ret; }

rt_bool_t safety_drv_enable_is_low(void)
{
    return (pad_level == PIN_LOW) ? RT_TRUE : RT_FALSE;
}

const char *safety_fb_verdict_name(safety_fb_verdict_t v)
{
    switch (v)
    {
    case SAFETY_FB_OK:                return "OK";
    case SAFETY_FB_TRANSITION:        return "TRANSITION";
    case SAFETY_FB_CUT_BY_HARDWARE:   return "CUT_BY_HARDWARE";
    case SAFETY_FB_UNEXPECTED_ENABLE: return "UNEXPECTED_ENABLE";
    case SAFETY_FB_PAD_MISMATCH:      return "PAD_MISMATCH";
    case SAFETY_FB_NO_SAMPLE:         return "NO_SAMPLE";
    default:                          return "?";
    }
}

safety_fb_verdict_t safety_fb_now(rt_uint8_t want_enabled)
{
    fb_now_calls++;
    return want_enabled ? fb_en : fb_dis;
}

rt_err_t safety_drv_feedback_confirm(rt_uint8_t want_enabled, safety_fb_verdict_t *out)
{
    safety_fb_verdict_t v = want_enabled ? fb_en : fb_dis;
    int seq = fb_confirm_calls++;

    if (confirm_fail_after >= 0 && seq >= confirm_fail_after)
    {
        if (out) *out = SAFETY_FB_NO_SAMPLE;
        return -RT_EIO;
    }
    if (out) *out = v;
    if (fb_confirm_rc != RT_EOK) return fb_confirm_rc;
    return (v == SAFETY_FB_OK) ? RT_EOK : -RT_EIO;
}

void safety_fb_snapshot(rt_int32_t *req, rt_int32_t *pad, rt_int32_t *fb, rt_bool_t *ok)
{
    if (req) *req = pad_level;
    if (pad) *pad = pad_level;
    if (fb)  *fb  = (fb_dis == SAFETY_FB_NO_SAMPLE) ? -1
               : (fb_dis == SAFETY_FB_OK ? PIN_HIGH : PIN_LOW);
    if (ok)  *ok  = (fb_dis != SAFETY_FB_NO_SAMPLE) ? RT_TRUE : RT_FALSE;
}

rt_bool_t safety_limit_inputs_enabled(void)
{
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
    return RT_TRUE;
#else
    return RT_FALSE;
#endif
}

/* 本链不编译 safety_gpio.c, 这个纯函数只为其头文件契约存在(不参与任何判定)。 */
safety_fb_verdict_t safety_fb_evaluate(rt_uint8_t want, rt_int32_t pad, rt_bool_t pad_ok,
                                       rt_int32_t fb, rt_bool_t fb_ok)
{
    (void)want; (void)pad; (void)pad_ok; (void)fb; (void)fb_ok;
    return SAFETY_FB_OK;
}

rt_err_t safety_thread_init(void)         { return RT_EOK; }
rt_err_t safety_irq_attach(void)          { return RT_EOK; }
rt_bool_t safety_irq_attached(void)       { return irq_attached ? RT_TRUE : RT_FALSE; }
rt_bool_t safety_polarity_validated(void) { return polarity_ok ? RT_TRUE : RT_FALSE; }
rt_bool_t safety_protection_ready(void)   { return protect_ready ? RT_TRUE : RT_FALSE; }
subsys_health_t safety_thread_get_health(void) { return SUBSYS_OK; }

rt_bool_t tmc2209_drive_config_ok_for_arm(void) { return f_cfg_ok; }
tmc_drv_cfg_state_t tmc2209_get_config_state(void) { return f_cfg_state; }
subsys_health_t     tmc2209_get_health(void)       { return f_tmc_health; }
rt_err_t  tmc2209_init(void) { return RT_EOK; }
rt_err_t  tmc2209_read_register(rt_uint8_t a, rt_uint8_t r, rt_uint32_t *v)
{ (void)a; fake_bus_reads++; if (v) *v = 0x12340000u | r; return RT_EOK; }
rt_err_t  tmc2209_cfg_txn_begin(void) { cfg_txn_begins++; return RT_EOK; }
void      tmc2209_cfg_txn_end(void)   { cfg_txn_ends++; }
/* CP4 K3(§5.3): 生产侧现在有"带调用方原身份 + 本次结果摘要"的复核版本。
 * 桩保持同一事实源(f_integrity / fake_bus_reads), 并把摘要按"本次未保护"回填 ——
 * 保护与否由生产侧 sp 决定, 桩不冒充配置层做过保护。 */
rt_uint32_t f_cfg_view_gen = 1u;
rt_uint32_t tmc2209_config_view_generation(void) { return f_cfg_view_gen; }
void        ps_set_cfg_view_gen(rt_uint32_t g)   { f_cfg_view_gen = g; }

rt_err_t tmc2209_config_integrity_check_for(rt_uint32_t expected_epoch,
                                           tmc_op_result_t *out)
{
    fake_bus_reads++;
    if (out != NULL)
    {
        memset(out, 0, sizeof(*out));
        out->prod      = RT_TRUE;
        out->op_epoch  = expected_epoch;
        out->rc        = f_integrity;
        out->protect   = SP_PROTECT_NONE;
        out->proof.valid = RT_FALSE;
    }
    return f_integrity;
}

rt_err_t  tmc2209_config_integrity_check(void)
{
    return tmc2209_config_integrity_check_for((rt_uint32_t)sp_epoch(), NULL);
}
rt_bool_t tmc2209_get_applied_config(rt_uint32_t *g, rt_uint32_t *c, tmc_drv_config_t *o)
{
    if (!f_cfg_ok) return RT_FALSE;
    if (g) *g = 0x00000C00u;
    if (c) *c = 0x10000053u;
    if (o) *o = f_applied;
    return RT_TRUE;
}
rt_uint32_t tmc2209_get_write_gate_mask(void)
{
    /* 生产 tmc_cfg_gate_prod() 不在本链(未编译 tmc2209.c): 这里给等价语义的假实现,
     * 用来证明宏门确实参与求值; 真实位的接入由 tmc2209.c 静态检查 + 旧链覆盖。 */
    rt_uint32_t bad = 0;
    if (!TMC_DRIVE_CONFIG_HW_VALIDATED)      bad |= TMC_WGATE_HW_NOT_VALIDATED;
    if (!safety_drv_enable_is_low())         bad |= TMC_WGATE_DRV_PAD_NOT_LOW;
    if (safety_fb_now(0u) != SAFETY_FB_OK)   bad |= TMC_WGATE_ENN_FB_NOT_DISABLED;
    if (safety_state_get() != SAFETY_READY)  bad |= TMC_WGATE_NOT_READY;
    return bad;
}
const char *tmc_drv_cfg_state_name(tmc_drv_cfg_state_t s) { (void)s; return "CFG"; }

rt_err_t adxl345_init(void) { return RT_EOK; }
subsys_health_t adxl345_get_health(void) { return SUBSYS_OK; }
rt_err_t current_adc_init(void) { return RT_EOK; }
subsys_health_t current_adc_get_health(void) { return SUBSYS_OK; }
rt_err_t ns_flash_init(void) { return RT_EOK; }
rt_err_t ns_flash_identify(rt_uint8_t id[3], rt_uint8_t sr[3])
{ id[0] = 0xEF; id[1] = 0x40; id[2] = 0x17; sr[0] = 0; return RT_EOK; }
void blackbox_trigger(rt_uint32_t code) { (void)code; bb_triggers++; }
subsys_health_t blackbox_get_health(void) { return SUBSYS_OK; }

/* ===================================================================== */
/* 真实的被测生产源码                                                      */
/* ===================================================================== */

/* ===================================================================== */
/* CP3 §9 第 1/2/5 项: stop_priority 与真实 drv_epwm.h 需要的可编程端口    */
/*   这一节全部是**夹具侧端口**, 不改生产代码; 每类观察量都是独立注入量:   */
/*     raw 写请求结果(enable_write_rc) / 实际焊盘电平(pad_level) /          */
/*     确认返回(safety_drv_permit_confirm_read 只看 pad_level) /            */
/*     ENN 反馈(fb_dis/fb_en/fb_confirm_rc) 四者互不代替。                  */
/*   中断掩码按"递归保存"的真实语义模拟: disable 返回**进入前**的值,        */
/*   enable 把掩码恢复成传入的那个值 —— 与 Cortex-M 的 PRIMASK 一致。       */
/*   窗口内的禁止项(取锁/睡眠/控制台/设备读写)由 ps_win_violations() 计数,  */
/*   任何非 0 都表示"生产代码在临界区里做了不许做的事"。                    */
/* ===================================================================== */
static volatile int s_intr_masked;      /* 1 = 已屏蔽(PRIMASK 语义) */
static int          s_intr_depth;
static int          s_win_violations;   /* 窗口内禁止项命中次数 */

rt_base_t rt_hw_interrupt_disable(void)
{
    rt_base_t old = (rt_base_t)s_intr_masked;
    s_intr_masked = 1;
    s_intr_depth++;
    return old;
}

void rt_hw_interrupt_enable(rt_base_t level)
{
    s_intr_masked = (int)level;
    if (s_intr_depth > 0) s_intr_depth--;
}

int  ps_win_masked(void)      { return s_intr_masked; }
int  ps_win_depth(void)       { return s_intr_depth; }
int  ps_win_violations(void)  { return s_win_violations; }
static const char *s_win_last;
/* 只在"当前确实屏蔽着中断"时计数; 生产路径在门外调用同一个函数则不记。 */
void ps_win_note(const char *what)
{
    if (s_intr_masked) { s_win_violations++; s_win_last = what; }
}
const char *ps_win_last_item(void) { return s_win_last ? s_win_last : "(none)"; }
void ps_win_violations_clear(void) { s_win_violations = 0; s_win_last = RT_NULL; }

/* ---- CP4 K5(§7): 上下文门的两条输入在夹具里的等价物 ----
 * s_isr_nest 只由测试显式置位(模拟"首条故障来自 ISR"的情境); rt_thread_self()
 * 在屏蔽窗口内返回空 —— 与目标上"窗口里没有可等待上下文"同一事实。
 * ⚠ 这不是完整 ISR 队列/nest 模型(那属于 S2 并发链的 C03/C04)。 */
void ps_isr_set(int v) { s_isr_nest = v; }
void ps_thread_null_set(int v) { s_thread_null = v; }
rt_bool_t rt_interrupt_get_nest(void) { return s_isr_nest ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void)
{
    return (s_intr_masked || s_isr_nest || s_thread_null) ? RT_NULL : &c_thread_store[0];
}

/* ---- 许可脚(PC.23 的等价物): 裸写请求 vs 实际焊盘 vs 回读确认, 三者分列 ---- */
rt_base_t safety_drv_permit_pin(void) { return safety_pin(PIN_NAME_DRV_ENABLE); }

static rt_err_t permit_raw_write(rt_uint8_t level)
{
    rt_base_t p = safety_pin(PIN_NAME_DRV_ENABLE);

    if (p < 0) return -RT_ENOSYS;
    if (level) writes_high++; else writes_low++;
    rt_pin_write(p, level);                 /* 桩按 pin 表更新 pad_level */
    last_enable_ret = enable_write_rc;      /* 裸写请求的返回结果(独立注入) */
    return enable_write_rc;
}

rt_err_t safety_drv_permit_low_raw_held(void)  { return permit_raw_write(0u); }
rt_err_t safety_drv_permit_high_raw_held(void) { return permit_raw_write(1u); }

/* 确认 = 只看**实际焊盘电平**, 与"写请求是否被接受"互不代替 */
rt_err_t safety_drv_permit_confirm_read(rt_uint8_t level)
{
    return (pad_level == (rt_int32_t)level) ? RT_EOK : -RT_EIO;
}

rt_err_t safety_drv_permit_confirm_low(void) { return safety_drv_permit_confirm_read(0u); }

/* ---- 真实 drv_epwm.h 的 prepare/commit 端口(门外纯计算 + 门内整数写) ---- */
#include "drv_epwm.h"          /* 真实公开头(struct ns800_epwm_prepared 的唯一定义处) */
static rt_err_t rc_prepare = RT_EOK;
void ps_sim_epwm_set_prepare_rc(rt_err_t r) { rc_prepare = r; }

int ns800_epwm_prepare(struct rt_device_pwm *device, rt_uint32_t channel,
                       rt_uint32_t period_ns, rt_uint32_t pulse_ns,
                       struct ns800_epwm_prepared *out)
{
    (void)device;
    if (out == RT_NULL) return -RT_EINVAL;
    if (rc_prepare != RT_EOK) return (int)rc_prepare;
    memset(out, 0, sizeof(*out));
    out->channel      = channel;
    out->count_mode   = 2u;              /* EPWM_COUNTER_MODE_UP_DOWN(桩侧固定值) */
    out->clock_div    = 1u;              /* EPWM_CLOCK_DIVIDER_2 */
    out->hs_clock_div = 0u;              /* EPWM_HSCLOCK_DIVIDER_1 */
    out->period_ns    = period_ns;
    out->pulse_ns     = pulse_ns;
    out->tb_period    = (rt_uint16_t)(period_ns / 10u);
    out->cmp_val      = (rt_uint16_t)(pulse_ns / 10u);
    out->valid        = 1u;
    return RT_EOK;
}

/* 门内提交: 只记一次"整数写"并复用 rt_pwm_set 的注入结果(不写回计数模式)。
 * ⚠ 这是**端口桩**, 不等于真实 HAL 叶子; C10 用另一条链真编译 drv_epwm.c。 */
int ns800_epwm_commit_prepared_held(struct rt_device_pwm *device,
                                    const struct ns800_epwm_prepared *prepared)
{
    if (prepared == RT_NULL || prepared->valid != 1u) return -RT_EINVAL;
    return (int)rt_pwm_set(device, (int)prepared->channel,
                           prepared->period_ns, prepared->pulse_ns);
}

#include "stop_priority.c"

#include "step_pwm.c"
#include "motor.c"
#include "safety_state.c"

/* ===================================================================== */
/* 用例骨架                                                                */
/* ===================================================================== */
static int g_run, g_pass, g_fail, g_pre_bad;

static void report(const char *id, rt_bool_t ok, const char *detail)
{
    g_run++;
    if (ok) { g_pass++; printf("CASE|%s|PASS|%s\n", id, detail); }
    else    { g_fail++; printf("CASE|%s|FAIL|%s\n", id, detail); }
}
#define CHECK(id, cond, detail) report((id), (cond) ? RT_TRUE : RT_FALSE, (detail))

static char g_buf[320];
static const char *fm(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_buf, sizeof(g_buf), fmt, ap);
    va_end(ap);
    return g_buf;
}

typedef void (*ps_cmd2_t)(int, char **);
typedef void (*ps_cmd0_t)(void);
extern const void *ps_mshref_pwm_test;
extern const void *ps_mshref_step_status;
extern const void *ps_mshref_cmd_motor_status;
extern const void *ps_mshref_safety_state;

static void msh_pwm_test(const char *arg)
{
    char *argv[2];
    argv[0] = (char *)"pwm_test";
    argv[1] = (char *)arg;
    ((ps_cmd2_t)ps_mshref_pwm_test)(2, argv);
}

static void wire_pins(void)
{
    ps_sim_add_pin(PIN_NAME_DRV_ENABLE,   PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIAG,     PIN_LOW);
    ps_sim_add_pin(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);
    ps_sim_add_pin(PIN_NAME_TMC_DIR,      PIN_LOW);
}

/* 读一次关断状态(生产 API, 不碰私有字段) */
static void stop_state(mot_stop_result_t *r, rt_uint8_t *s, rt_err_t *c, rt_bool_t *l)
{
    motor_get_stop_state(r, s, c, l);
}

static rt_bool_t gate_bit(rt_uint32_t bit)
{
    return (motor_get_gate_fail_mask() & bit) ? RT_TRUE : RT_FALSE;
}

static int snap_state(void)
{
    motor_snapshot_t sn;
    return (motor_get_snapshot(&sn) == RT_EOK) ? (int)sn.state : -1;
}

/* 用**生产路径**回到干净起点: 注入全 healthy -> 统一停机 -> fault_reset 人工恢复 ->
 * READY。S1 的关断失败锁存只能这样解除(夹具不直接改私有状态, 也不调用 clear helper)。 */
static rt_bool_t recover_clean(void)
{
    mot_stop_result_t r;
    rt_bool_t lat;
    int i;

    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    ps_set_enable_write_rc(RT_EOK);
    ps_sim_set_pad_stuck(0);
    ps_sim_set_pad_level(PIN_LOW);
    ps_set_fb(0u, SAFETY_FB_OK);
    ps_set_fb(1u, SAFETY_FB_OK);
    ps_set_fb_confirm_rc(RT_EOK);
    ps_set_confirm_fail_after(-1);
    ps_set_protect(1, 1, 1, 1);
    ps_set_cfg_ok(RT_TRUE, TMC_DRV_CONFIGURED);
    ps_set_integrity(RT_EOK);
    ps_set_tmc_health(SUBSYS_OK);
    ps_sim_set_level(PIN_NAME_TMC_DIAG, PIN_LOW);

    if (safety_state_get() == SAFETY_RUN) (void)safety_transition(SAFETY_READY);
    if (safety_state_get() != SAFETY_FAULT_LATCHED &&
        safety_state_get() != SAFETY_ESTOP &&
        safety_state_get() != SAFETY_MANUAL_CLEAR)
        safety_force_shutdown(FAULT_SOFT);
    if (safety_state_get() == SAFETY_FAULT_LATCHED ||
        safety_state_get() == SAFETY_ESTOP)
        safety_fault_reset_manual();
    for (i = 0; i < 4 && safety_state_get() == SAFETY_MANUAL_CLEAR; ++i)
        (void)safety_transition(SAFETY_READY);
    stop_state(&r, RT_NULL, RT_NULL, &lat);
    if (safety_state_get() != SAFETY_READY || lat != RT_FALSE)
    {
        g_pre_bad++;
        printf("PRE-FAIL|recover_clean|state=%s latched=%d stop=%s\n",
               safety_state_name(safety_state_get()), (int)lat,
               mot_stop_result_name(r));
        return RT_FALSE;
    }
    return RT_TRUE;
}

/* ---------------------------------------------------------------------- */
/* PS01: motor_init 的关断失败(必须在还没有正常运行线程时先跑)             */
/* ---------------------------------------------------------------------- */
static void cases_init(void)
{
    rt_err_t e;
    mot_stop_result_t res;
    rt_uint8_t src;
    rt_err_t rc;
    rt_bool_t lat;
    int tc0 = ps_sim_thread_create_calls();
    int ts0 = ps_sim_thread_startup_calls();
    int low0 = ps_enable_write_calls(0);

    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);      /* 关断失败 */
    e = motor_init();
    CHECK("PS01a-init_disable_fail_rc", e == -RT_EIO,
          fm("init rc=%d (期望透传 rt_pwm_disable 的 -RT_EIO, 不称成功)", (int)e));
    CHECK("PS01b-init_disable_fail_still_low",
          ps_enable_write_calls(0) > low0 &&
          ps_sim_last_write(PIN_NAME_DRV_ENABLE) == PIN_LOW,
          fm("关断失败仍尝试撤销许可: LOW 写 %d->%d, 焊盘实测=%d",
             low0, ps_enable_write_calls(0), (int)ps_sim_last_write(PIN_NAME_DRV_ENABLE)));
    CHECK("PS01c-init_no_ramp_thread_not_healthy",
          ps_sim_thread_create_calls() == tc0 &&
          ps_sim_thread_startup_calls() == ts0 &&
          motor_get_health() == SUBSYS_FAILED,
          fm("thread create=%d(startup=%d) health=%s",
             ps_sim_thread_create_calls(), ps_sim_thread_startup_calls(),
             subsys_health_name(motor_get_health())));
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS01d-init_records_unknown_and_latches",
          res == MOT_STOP_API_FAILED && src == MOT_STOP_SRC_INIT &&
          rc == -RT_EIO && lat == RT_TRUE,
          fm("stop=%s src=%s rc=%d latched=%d", mot_stop_result_name(res),
             mot_stop_src_name(src), (int)rc, (int)lat));

    /* 对照: 同一份代码, 关断成功 => init 成功 + 建线程; 但锁存**不**因此被清 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    e = motor_init();
    CHECK("PS01e-init_success_control_creates_thread",
          e == RT_EOK && ps_sim_thread_create_calls() == tc0 + 1 &&
          ps_sim_thread_startup_calls() == ts0 + 1 &&
          motor_get_health() == SUBSYS_OK,
          fm("init rc=%d create=%d startup=%d health=%s", (int)e,
             ps_sim_thread_create_calls(), ps_sim_thread_startup_calls(),
             subsys_health_name(motor_get_health())));
    stop_state(&res, RT_NULL, RT_NULL, &lat);
    CHECK("PS01f-init_success_updates_but_keeps_latch",
          res == MOT_STOP_API_ACCEPTED && lat == RT_TRUE,
          fm("stop=%s latched=%d (一次成功只更新当前结果, 不清锁存)",
             mot_stop_result_name(res), (int)lat));
}

/* 前置专用 helper: "诊断侧最近一次关断被驱动接受"(不等于 off_confirmed 那个收紧门;
 * 锁存位只能由人工恢复清除, 用它当前置会把恢复前的这条路永久堵死)。 */
static rt_bool_t step_pwm_get_off_accepted_for_pre(void)
{
    mot_stop_result_t r;
    rt_err_t          rc;
    rt_bool_t         lat;

    step_pwm_get_stop_state(&r, &rc, &lat);
    return (r == MOT_STOP_API_ACCEPTED) ? RT_TRUE : RT_FALSE;
}

/* ---------------------------------------------------------------------- */
/* PS02/PS03: emergency_stop 的两步结果                                   */
/* ---------------------------------------------------------------------- */
static void cases_estop(void)
{
    rt_err_t e;
    mot_stop_result_t res;
    rt_uint8_t src;
    rt_err_t rc;
    rt_bool_t lat;
    int d0, low0;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    ps_sim_pwm_force_running(1);              /* 前置: 输出确实在跑 */
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    e = motor_emergency_stop();
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS02a-estop_pwmfail_returns_pwm_rc", e == -RT_EIO && rc == -RT_EIO,
          fm("rc=%d stop_rc=%d (PWM 失败优先返回)", (int)e, (int)rc));
    CHECK("PS02b-estop_pwmfail_low_action_counted",
          ps_enable_write_calls(0) == low0 + 1 &&
          ps_sim_pwm_disable_calls() == d0 + 1,
          fm("disable %d->%d, LOW 写 %d->%d", d0, ps_sim_pwm_disable_calls(),
             low0, ps_enable_write_calls(0)));
    CHECK("PS02c-estop_pwmfail_output_still_running", ps_sim_pwm_running() == 1,
          fm("桩侧模拟输出仍在运行=%d (disable 失败时桩不清输出)", ps_sim_pwm_running()));
    CHECK("PS02d-estop_pwmfail_records_unknown",
          res == MOT_STOP_API_FAILED && src == MOT_STOP_SRC_ESTOP && lat == RT_TRUE,
          fm("stop=%s src=%s latched=%d", mot_stop_result_name(res),
             mot_stop_src_name(src), (int)lat));

    /* PS03: 两者都失败保留两项 rc; 只有 GPIO 失败保留其错误; 全成功对照 */
    ps_set_enable_write_rc(-RT_EPERM);        /* 许可写失败 */
    e = motor_emergency_stop();               /* 关断仍失败(上一步没改) */
    stop_state(&res, RT_NULL, &rc, RT_NULL);
    CHECK("PS03a-both_fail_keeps_both_rc",
          e == -RT_EIO && rc == -RT_EIO && ps_last_enable_write_rc() == -RT_EPERM,
          fm("outer=%d pwm_stop_rc=%d gpio_write_rc=%d (两项原始 rc 都留档)",
             (int)e, (int)rc, (int)ps_last_enable_write_rc()));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK); /* 只留 GPIO 失败 */
    e = motor_emergency_stop();
    stop_state(&res, RT_NULL, RT_NULL, RT_NULL);
    CHECK("PS03b-gpio_only_fail_keeps_gpio_rc",
          e == -RT_EPERM && res == MOT_STOP_API_ACCEPTED,
          fm("outer=%d stop=%s (只有许可写失败时返回它的 rc)", (int)e,
             mot_stop_result_name(res)));
    ps_set_enable_write_rc(RT_EOK);
    e = motor_emergency_stop();
    stop_state(&res, RT_NULL, &rc, RT_NULL);
    CHECK("PS03c-all_ok_returns_eok_only_api_accepted",
          e == RT_EOK && rc == RT_EOK && res == MOT_STOP_API_ACCEPTED &&
          ps_sim_pwm_running() == 0 && snap_state() == MOTOR_FAULT,
          fm("rc=%d stop=%s running=%d state=%d (EOK 只称驱动接受了关断请求)",
             (int)e, mot_stop_result_name(res), ps_sim_pwm_running(), snap_state()));
}

static int snap_armed(void)
{
    motor_snapshot_t sn;
    return (motor_get_snapshot(&sn) == RT_EOK) ? (int)sn.armed : -1;
}

static rt_uint32_t snap_cur_hz(void)
{
    motor_snapshot_t sn;
    return (motor_get_snapshot(&sn) == RT_EOK) ? sn.current_hz : 0xFFFFFFFFu;
}

/* ---------------------------------------------------------------------- */
/* PS04: disarm 失败不回"正常可启动 IDLE", 后续 arm/start 被拒             */
/* ---------------------------------------------------------------------- */
static void cases_disarm(void)
{
    rt_err_t e;
    mot_stop_result_t res;
    rt_uint8_t src;
    rt_err_t rc;
    rt_bool_t lat;
    rt_uint32_t m;
    int high0;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    e = motor_disarm();
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS04a-disarm_fail_rc_is_pwm",
          e == -RT_EIO && rc == -RT_EIO && res == MOT_STOP_API_FAILED,
          fm("disarm rc=%d stop_rc=%d stop=%s (不得解释成只是 GPIO 失败)",
             (int)e, (int)rc, mot_stop_result_name(res)));
    CHECK("PS04b-disarm_fail_not_clean_idle",
          snap_state() == MOTOR_FAULT && snap_armed() == 0 && lat == RT_TRUE &&
          src == MOT_STOP_SRC_DISARM,
          fm("state=%d(FAULT=%d) armed=%d latched=%d src=%s - 不回正常可启动 IDLE",
             snap_state(), MOTOR_FAULT, snap_armed(), (int)lat,
             mot_stop_src_name(src)));

    high0 = ps_enable_write_calls(1);
    e = motor_arm();
    m = motor_get_gate_fail_mask();
    CHECK("PS04c-arm_refused_with_bit7",
          e != RT_EOK && (m & MOT_GATE_STOP_UNCONFIRMED) != 0u,
          fm("arm rc=%d mask=0x%X bit7=%d", (int)e, (unsigned)m,
             (m & MOT_GATE_STOP_UNCONFIRMED) ? 1 : 0));
    (void)motor_set_target_hz(500);
    e = motor_start();
    CHECK("PS04d-no_high_write_on_refused_takeover",
          e != RT_EOK && ps_enable_write_calls(1) == high0 &&
          snap_armed() == 0,
          fm("start rc=%d HIGH 写次数 %d->%d armed=%d (拒绝路径不得请求使能)",
             (int)e, high0, ps_enable_write_calls(1), snap_armed()));
    (void)motor_set_target_hz(0);
}

/* ---------------------------------------------------------------------- */
/* PS05(两变体共有): 诊断侧**未持有**输出时的语义                          */
/*   未持有 != 一次成功的硬件关断, 也不该新置失败锁存(合同 4.3 的区分要求)  */
/* ---------------------------------------------------------------------- */
static void cases_diag_never_held(void)
{
    mot_stop_result_t dres;
    rt_err_t drc;
    rt_bool_t dlat;
    int d0;
    int ev0;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    d0 = ps_sim_pwm_disable_calls();
    ev0 = ps_event_sends();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);   /* 就算注入失败也不该被尝试 */
    /* CP3 §9 第 3 项: 原 step_pwm_force_stop() 是被点名删除的**旁路**(它会让统一停机
     * 与 fault_reset 各关一次共享模块)。同一业务断言改走**生产入口** `pwm_test stop`
     * —— 它内部就是那条唯一的 checked 关断路径, "诊断未持有"分支必须: 不动共享模块、
     * 不新置失败锁存、不伪造一次成功、**也不投任何事件**(无自激)。 */
    msh_pwm_test("stop");
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS05h-not-held-close_is_not_a_failure",
          dlat == RT_FALSE && dres == MOT_STOP_NOT_ATTEMPTED &&
          step_pwm_output_active() == RT_FALSE &&
          step_pwm_off_confirmed() == RT_TRUE &&
          ps_sim_pwm_disable_calls() == d0 &&
          ps_event_sends() == ev0 &&
          ps_cap_has("无需关断动作") == 1,
          fm("disable %d->%d last=%s latched=%d active=%d off_confirmed=%d"
             " event=%d->%d 无动作日志=%d",
             d0, ps_sim_pwm_disable_calls(), mot_stop_result_name(dres),
             (int)dlat, (int)step_pwm_output_active(),
             (int)step_pwm_off_confirmed(), ev0, ps_event_sends(),
             ps_cap_has("无需关断动作")));

    ps_cap_clear();
    ((ps_cmd0_t)ps_mshref_step_status)();
    CHECK("PS05i-never-held_status_no_false_claim",
          ps_cap_has("no(安全)") == 0 && ps_cap_has("UNKNOWN") == 0 &&
          ps_cap_has("not-attempted") == 1,
          fm("捕获: no(安全)=%d UNKNOWN=%d not-attempted=%d",
             ps_cap_has("no(安全)"), ps_cap_has("UNKNOWN"),
             ps_cap_has("not-attempted")));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
}

#ifdef PS_GATE_HOST_OVERRIDE
/* ---------------------------------------------------------------------- */
/* PS05(仅覆盖变体): 诊断**持有**输出后关断失败 => 所有权不释放            */
/*   前置"诊断持有输出"由 pwm_test 真实 enable 成功得到(目标宏下不可达,     */
/*   所以这一组 ID 只在 host_ovr 登记)                                     */
/* ---------------------------------------------------------------------- */
static void cases_diag_ovr(void)
{
    rt_err_t e;
    mot_stop_result_t dres;
    rt_err_t drc;
    rt_bool_t dlat;
    int en0, d0, ev0 = 0;
    rt_uint32_t epoch0;
    sp_token_t gtok;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("1000");                         /* 真持有输出 */
    if (step_pwm_output_active() != RT_TRUE || !ps_sim_pwm_running())
    { g_pre_bad++; printf("PRE-FAIL|diag-did-not-own\n"); }   /* 继续: 让 PS05* 自己 FAIL */

    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    ev0 = ps_event_sends();
    epoch0 = sp_epoch();
    /* CP3 §9 第 3 项: 原 step_pwm_force_stop() 是会造成重复 disable 的旁路, 已删除。
     * 同一业务断言改走**生产入口** `pwm_test stop`(内部就是那条唯一的 checked 关断
     * 路径); 该入口返回 void, 所以"关断失败"这一事实改由 canonical 镜像的原始 rc
     * 来判(drc), 与输出是否仍持有/锁存/结果分列的判据一字未减。 */
    msh_pwm_test("stop");
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS05a-force_stop_fail_keeps_owner",
          drc == -RT_EIO && step_pwm_output_active() == RT_TRUE &&
          step_pwm_off_confirmed() == RT_FALSE && dlat == RT_TRUE &&
          dres == MOT_STOP_API_FAILED &&
          /* CP4 K3(§5.2)改了这里的动作次数合同, 逐项解释(计数变化不是放宽门禁):
           *   原 CP3 形状 = ①checked 关断那一次 disable + ②失败分支再走
           *   safety_post_event() 的"新显式保护请求"(又一次 disable + LOW), 共 2 次;
           *   现在 = ①那一次关断**失败时已在同一个短临界区内**追加抑制/换 epoch/
           *   留 pending(sp_off_attempt_for), 失败分支只**通知**既有链, 不再补第二轮
           *   关断 —— 于是 disable 恰好 1 次。任务书 §5.2 原文: "正常关闭自身失败时
           *   升级抑制复用**刚才那一次** disable 结果, 不第二次 disable …
           *   不能用加一轮关断掩盖错误"。
           * 保留并加强的业务判据: 失败不释放所有权 / 锁存 / 结果与原始 rc 分列 /
           * **换代确实发生**(epoch 前进) / **义务真的留下了**(pending) /
           * **既有链确实收到一次**(event 恰好 +1)。 */
          ps_sim_pwm_disable_calls() == d0 + 1 && sp_epoch() > epoch0 &&
          safety_trip_pending() == RT_TRUE && ps_event_sends() == ev0 + 1,
          fm("diag_rc=%d active=%d off_confirmed=%d latched=%d last=%s"
             " disable %d->%d epoch %u->%u pending=%s event %d->%d",
             (int)drc, (int)step_pwm_output_active(), (int)step_pwm_off_confirmed(),
             (int)dlat, mot_stop_result_name(dres), d0,
             ps_sim_pwm_disable_calls(), (unsigned)epoch0, (unsigned)sp_epoch(),
             safety_trip_pending() ? "YES" : "no", ev0, ps_event_sends()));

    /* 再试一次仍然失败 => 所有权仍未交回(不谎报 "released") */
    msh_pwm_test("stop");
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS05b-repeat_stop_still_not_released",
          step_pwm_output_active() == RT_TRUE && drc == -RT_EIO &&
          dlat == RT_TRUE && ps_sim_pwm_running() == 1,
          fm("active=%d last_rc=%d latched=%d running=%d",
             (int)step_pwm_output_active(), (int)drc, (int)dlat,
             ps_sim_pwm_running()));

    /* 新的诊断 enable 被拒: 不能用一次 enable 覆盖未处理的关断失败 */
    en0 = ps_sim_pwm_enable_calls();
    msh_pwm_test("2000");
    CHECK("PS05c-new_diag_enable_refused", ps_sim_pwm_enable_calls() == en0,
          fm("enable 次数 %d->%d (必须不变)", en0, ps_sim_pwm_enable_calls()));

    /* 借用门在六道门之外因锁存而拒绝(把 Motor 侧/许可/反馈都保持干净)。
     * CP3 §9 第 4 项: 真借用调用必须带 out_token; 被拒时身份不得被写出。 */
    gtok.owner = SP_NONE;
    gtok.epoch = 0u;
    e = motor_pwm_grant_to_diag(&gtok);
    CHECK("PS05f-grant_refused_by_diag_latch",
          e == -RT_EPERM && step_pwm_off_confirmed() == RT_FALSE &&
          gtok.owner == SP_NONE && gtok.epoch == 0u,
          fm("grant rc=%d off_confirmed=%d token=(owner=%d,epoch=%u)"
             " (宏全开也不放行未复核的关断失败)",
             (int)e, (int)step_pwm_off_confirmed(), (int)gtok.owner,
             (unsigned)gtok.epoch));

    /* Motor 接管被拒(所有权保守判据) */
    e = motor_arm();
    CHECK("PS05d-motor_takeover_refused",
          e != RT_EOK && step_pwm_output_active() == RT_TRUE && snap_armed() == 0,
          fm("arm rc=%d active=%d armed=%d", (int)e,
             (int)step_pwm_output_active(), snap_armed()));

    /* 状态显示不得把"未持有/未确认"写成 no(安全) —— 判据就是输出本身(PS05 明文) */
    ps_cap_clear();
    ((ps_cmd0_t)ps_mshref_step_status)();
    CHECK("PS05e-step_status_not_reported_safe",
          ps_cap_has("no(安全)") == 0 && ps_cap_has("UNKNOWN") == 1,
          fm("捕获含 no(安全)=%d 含 UNKNOWN=%d",
             ps_cap_has("no(安全)"), ps_cap_has("UNKNOWN")));

    /* 交回通道: 一次成功的关断被接受 => 释放所有权(锁存仍在, 由人工恢复清除)。
     * CP3 §9 第 3 项: 走**生产入口** `pwm_test stop`(内部即那条唯一的 checked 关断),
     * 前置是否成立改由 canonical 的诊断结果判定, 不依赖已删除的旁路返回码。 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("stop");
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    if (dres != MOT_STOP_API_ACCEPTED)
    { g_pre_bad++; printf("PRE-FAIL|diag-owner-not-released\n"); }
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS05g-owner_released_success_latch_kept",
          step_pwm_output_active() == RT_TRUE && dlat == RT_TRUE &&
          dres == MOT_STOP_API_ACCEPTED,
          fm("active=%d latched=%d last=%s (成功后所有权交回, 但锁存保留)",
             (int)step_pwm_output_active(), (int)dlat, mot_stop_result_name(dres)));
}
#endif /* PS_GATE_HOST_OVERRIDE */

/* ---------------------------------------------------------------------- */
/* PS06: 统一停机有失败时仍走完其余关闭动作, 且不谎报、不自激              */
/* ---------------------------------------------------------------------- */
static void cases_shutdown(void)
{
    int d0, low0, bb0, ev0, want_dis;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    want_dis = 1;                                 /* Motor 侧一定尝试一次 */
#ifdef PS_GATE_HOST_OVERRIDE
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("1000");                         /* 诊断真实持有输出 */
    if (step_pwm_output_active() != RT_TRUE)
    {
        g_pre_bad++;
        printf("PRE-FAIL|shutdown-diag-not-owner (诊断未能持有通道)\n");
        want_dis = 1;                             /* 那就只要求 Motor 侧那一次 */
    }
    /* S2(CP3 §10.4"共享实际动作导致计数变化逐项解释"): 统一停机的短保护在**一个**
     * 窗口里对共享的 epwm1 时间基做**一次** rt_pwm_disable, Motor 侧与诊断侧看的
     * 是同一次实际动作(§4.2 明确禁止同一波停机里的第二次 disable, 那会让后一次的
     * 成功掩盖前一次的失败)。所以 want_dis 从 S1 的"+2(两路各一次)"改成"+1(一次共享)"。
     * 两路各自的**结果/所有权/锁存**判据一字未减: PS06c/PS10b/PS05x/PS14x 仍然分列断言。 */
    else want_dis = 1;
#endif
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);   /* 两路关断都会失败 */
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    bb0 = ps_bb_triggers();
    ev0 = ps_event_sends();
    ps_event_bits_clear();
    ps_cap_clear();

    safety_force_shutdown(FAULT_SOFT);

    CHECK("PS06a-motor_close_and_low_always_attempted",
          ps_sim_pwm_disable_calls() == d0 + want_dis &&
          ps_enable_write_calls(0) == low0 + 1,
          fm("disable %d->%d (want +%d: motor%s), LOW 写 %d->%d", d0,
             ps_sim_pwm_disable_calls(), want_dis,
             want_dis == 2 ? " + diag" : "", low0, ps_enable_write_calls(0)));
    CHECK("PS06b-latches_with_one_blackbox_trigger",
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() == (rt_uint32_t)FAULT_SOFT &&
          ps_bb_triggers() == bb0 + 1,
          fm("state=%s code=%u bb %d->%d", safety_state_name(safety_state_get()),
             (unsigned)safety_fault_get(), bb0, ps_bb_triggers()));
    CHECK("PS06c-no_false_step_stopped",
          ps_cap_has("STEP stopped") == 0 && ps_cap_has("NOT-confirmed") == 1,
          fm("捕获含 'STEP stopped'=%d 含 'NOT-confirmed'=%d",
             ps_cap_has("STEP stopped"), ps_cap_has("NOT-confirmed")));
    CHECK("PS06d-no_event_self_excitation",
          ps_event_sends() == ev0 && ps_event_bits() == 0u,
          fm("停机内部 event send %d->%d bits=0x%X", ev0, ps_event_sends(),
             (unsigned)ps_event_bits()));
#ifdef PS_GATE_HOST_OVERRIDE
    /* 收口: 让诊断侧真实成功一次以交回所有权(失败锁存**不**在这里清, 由 PS09 的
     * 人工恢复路径负责) —— 否则后面的用例会被"还占着通道"假性挡住。 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("stop");
    /* 同 PS09 的理由: 锁存仍在时 output_active 的保守判据仍为真, 前置要看的是
     * "这次诊断关断被驱动接受 + 预约身份已交回", 不是那个收紧的门禁位。 */
    if (step_pwm_get_off_accepted_for_pre() != RT_TRUE ||
        sp_owner_is(SP_DIAG) == RT_TRUE)
    { g_pre_bad++; printf("PRE-FAIL|shutdown-diag-still-owned\n"); }
#endif
}

/* ---------------------------------------------------------------------- */
/* PS08/PS09: 失败保持锁存 / 只有人工恢复才能清除                          */
/* ---------------------------------------------------------------------- */
static void cases_clear(void)
{
    mot_stop_result_t res, dres;
    rt_uint8_t src;
    rt_err_t rc, drc;
    rt_bool_t lat, dlat;
    int conf0, en0, high0;

    /* PS08a: 只有 PWM 失败, GPIO/ENN 全部正常 => 仍保持锁存与非零故障码 */
    ps_set_enable_write_rc(RT_EOK);
    ps_sim_set_pad_stuck(0);
    ps_sim_set_pad_level(PIN_LOW);
    ps_set_fb(0u, SAFETY_FB_OK);
    ps_set_fb(1u, SAFETY_FB_OK);
    ps_set_fb_confirm_rc(RT_EOK);
    ps_set_protect(1, 1, 1, 1);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    conf0 = ps_fb_confirm_calls();
    safety_fault_reset_manual();
    CHECK("PS08a-clear_refused_when_pwm_uncertain",
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() != (rt_uint32_t)FAULT_NONE,
          fm("state=%s code=%u (保持锁存与非零故障码)",
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get()));
    CHECK("PS08b-clear_still_runs_input_and_enn_checks",
          ps_fb_confirm_calls() >= conf0 + 1 &&
          ps_sim_pin_read_calls(PIN_NAME_TMC_DIAG) > 0,
          fm("ENN 有界确认 %d->%d, DIAG 实测读=%d (原检查没被跳过)",
             conf0, ps_fb_confirm_calls(),
             ps_sim_pin_read_calls(PIN_NAME_TMC_DIAG)));

    /* PS08c: 一轮后续成功的普通 stop 不自动恢复 Safety、不自动使能 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_disarm();                          /* Motor 侧关断现在成功了 */
    high0 = ps_enable_write_calls(1);
    stop_state(&res, RT_NULL, RT_NULL, &lat);
    CHECK("PS08c-successful_stop_no_auto_recover",
          res == MOT_STOP_API_ACCEPTED && lat == RT_TRUE &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          gate_bit(MOT_GATE_STOP_UNCONFIRMED) == RT_TRUE &&
          motor_arm() != RT_EOK && ps_enable_write_calls(1) == high0,
          fm("stop=%s latched=%d state=%s bit7=%d HIGH 写 %d->%d",
             mot_stop_result_name(res), (int)lat,
             safety_state_name(safety_state_get()),
             (int)gate_bit(MOT_GATE_STOP_UNCONFIRMED), high0,
             ps_enable_write_calls(1)));

    /* 前置: 诊断侧也由生产路径自己取得一次成功关断(不伪造) */
    /* CP3 §9 第 3 项: 原 step_pwm_force_stop() 是被点名删除的旁路(会和统一停机/
     * fault_reset 各关一次共享模块)。同一前置改走**生产入口** `pwm_test stop`,
     * 判据换成"诊断侧确有有效关断结果且不谎称持有"。 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("stop");
    /* CP3 §10.4 适配说明: 这里**不能**用 step_pwm_off_confirmed() 当前置。它按合同把
     * "未复核的历史关断失败锁存"也算无效(那是给 arm/start/借用门的收紧判据), 而锁存
     * 只能由 fault_reset 的人工恢复清除 —— 拿它当前置会把这条路永久堵死(S2-C13 反例)。
     * 前置真正要的是"这次诊断关断被驱动接受 + 预约身份已交回"。 */
    /* 前置要的是"诊断侧不再欠着这个通道":
     *  - host_ovr: 诊断真持有过, 这次关断被驱动接受且预约已交回;
     *  - target  : 诊断借用门在宏 FALSE 下从未放行(NOT_ATTEMPTED, 也没有失败锁存),
     *              此时"要求一次 API_ACCEPTED"反而会把合法的前置判成失败。
     * 两个变体共同的、也是这条前置真正要保护的事实 = owner 已不是 SP_DIAG。 */
    if (sp_owner_is(SP_DIAG) == RT_TRUE)
    { g_pre_bad++; printf("PRE-FAIL|diag_retry_not_accepted\n"); }
    en0 = ps_sim_pwm_enable_calls();
    safety_fault_reset_manual();
    stop_state(&res, &src, &rc, &lat);
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS09a-manual_clear_after_off_confirmed",
          safety_state_get() == SAFETY_MANUAL_CLEAR &&
          safety_fault_get() == (rt_uint32_t)FAULT_NONE &&
          lat == RT_FALSE && dlat == RT_FALSE &&
          res == MOT_STOP_API_ACCEPTED &&
          (dres == MOT_STOP_API_ACCEPTED ||
           (dres == MOT_STOP_NOT_ATTEMPTED && step_pwm_output_active() == RT_FALSE)),
          fm("state=%s code=%u motor_latched=%d diag_latched=%d stop=%s src=%s rc=%d"
             " diag=%s",
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get(),
             (int)lat, (int)dlat, mot_stop_result_name(res),
             mot_stop_src_name(src), (int)rc, mot_stop_result_name(dres)));
    CHECK("PS09b-no_auto_output_or_enable_after_clear",
          ps_sim_pwm_enable_calls() == en0 && snap_state() == MOTOR_IDLE &&
          snap_armed() == 0 && safety_state_get() != SAFETY_READY,
          fm("enable %d->%d motor_state=%d armed=%d safety=%s (仍需自检+重新 arm)",
             en0, ps_sim_pwm_enable_calls(), snap_state(), snap_armed(),
             safety_state_name(safety_state_get())));
}

/* ---------------------------------------------------------------------- */
/* PS10: ENN 无样本仍尝试关断与 LOW; 后置复核没被新 PWM 逻辑替代            */
/* ---------------------------------------------------------------------- */
static void cases_enn(void)
{
    int d0, low0, want_dis;
    mot_stop_result_t res;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    want_dis = 1;
#ifdef PS_GATE_HOST_OVERRIDE
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("1000");                         /* 诊断持有输出, 两路都要尝试 */
    if (step_pwm_output_active() != RT_TRUE)
    {
        g_pre_bad++;
        printf("PRE-FAIL|enn-diag-not-owner (诊断未能持有通道)\n");
        want_dis = 1;
    }
    /* S2(CP3 §10.4"共享实际动作导致计数变化逐项解释"): 统一停机的短保护在**一个**
     * 窗口里对共享的 epwm1 时间基做**一次** rt_pwm_disable, Motor 侧与诊断侧看的
     * 是同一次实际动作(§4.2 明确禁止同一波停机里的第二次 disable, 那会让后一次的
     * 成功掩盖前一次的失败)。所以 want_dis 从 S1 的"+2(两路各一次)"改成"+1(一次共享)"。
     * 两路各自的**结果/所有权/锁存**判据一字未减: PS06c/PS10b/PS05x/PS14x 仍然分列断言。 */
    else want_dis = 1;
#endif
    ps_set_fb(0u, SAFETY_FB_NO_SAMPLE);
    ps_set_fb(1u, SAFETY_FB_NO_SAMPLE);
    ps_set_fb_confirm_rc(-RT_EIO);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    safety_force_shutdown(FAULT_SOFT);
    CHECK("PS10a-nosample_still_attempts_both_closes",
          ps_sim_pwm_disable_calls() == d0 + want_dis &&
          ps_enable_write_calls(0) == low0 + 1 &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() == (rt_uint32_t)FAULT_SOFT,
          fm("disable %d->%d(want +%d) LOW 写 %d->%d state=%s code=%u",
             d0, ps_sim_pwm_disable_calls(), want_dis, low0,
             ps_enable_write_calls(0), safety_state_name(safety_state_get()),
             (unsigned)safety_fault_get()));

    /* 反馈读不到与 PWM 关断失败是**两个不同结果**, 各自留档、互不替代 */
    stop_state(&res, RT_NULL, RT_NULL, RT_NULL);
    CHECK("PS10b-enn_unknown_and_pwm_fail_recorded_separately",
          res == MOT_STOP_API_FAILED &&
          safety_drv_enable_is_low() == RT_TRUE,
          fm("stop=%s pad_low=%d (ENN 无样本没有抹掉 PWM 失败, 反之亦然)",
             mot_stop_result_name(res), (int)safety_drv_enable_is_low()));

    /* PS10c: 前置确认通过、**disarm 之后**才无样本 => 仍拒绝清除(IO70-NEW-01) */
    ps_set_fb(0u, SAFETY_FB_OK);
    ps_set_fb(1u, SAFETY_FB_OK);
    ps_set_fb_confirm_rc(RT_EOK);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
#ifdef PS_GATE_HOST_OVERRIDE
    /* 先把诊断侧的关断结果做成"有效"(真实成功一次), 这样拒绝原因就只剩后置复核 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("stop");               /* 同上: 生产入口, 不再用已删除的旁路 */
    if (step_pwm_get_off_accepted_for_pre() != RT_TRUE ||
        sp_owner_is(SP_DIAG) == RT_TRUE)
    {
        g_pre_bad++;
        printf("PRE-FAIL|enn10c-diag-not-off (诊断侧关断没能重取成功结果)\n");
    }
#endif
    ps_set_confirm_fail_after(1);          /* 第 1 次(前置)成功, 第 2 次(后置)失败 */
    safety_fault_reset_manual();
    ps_set_confirm_fail_after(-1);
    CHECK("PS10c-post_disarm_recheck_still_bites",
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() != (rt_uint32_t)FAULT_NONE,
          fm("state=%s code=%u (后置 ENN 复核仍然拒绝清除)",
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get()));
}

/* ---------------------------------------------------------------------- */
/* PS11(仅目标变体): 硬件宏 FALSE 时门仍拒绝, 状态查询零副作用              */
/* ---------------------------------------------------------------------- */
#ifndef PS_GATE_HOST_OVERRIDE
static void cases_target_gates(void)
{
    rt_err_t e;
    rt_uint32_t m;
    rt_uint8_t src;
    rt_err_t rc;
    mot_stop_result_t res;
    motor_snapshot_t sn;
    sp_token_t gtok;
    int high0, en0, dis0, wr0, bus0, en1, dis1;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    high0 = ps_enable_write_calls(1);
    e = motor_arm();
    m = motor_get_gate_fail_mask();
    CHECK("PS11a-arm_refused_by_enable_path_macro",
          e == -RT_EPERM && (m & MOT_GATE_ENABLE_PATH) != 0u &&
          ps_enable_write_calls(1) == high0,
          fm("arm rc=%d mask=0x%X HIGH 写=%d", (int)e, (unsigned)m,
             ps_enable_write_calls(1)));

    en0 = ps_sim_pwm_enable_calls();
    /* CP3 §9 第 4 项: 借用入口现在**返回真实预约身份**(sp_token_t), 目标宏下被拒时
     * 不得留下任何预约(owner 仍是 NONE)。同时新增只读体检入口 motor_diag_gate_check():
     * 状态查询一类路径只能用它, 不许在查询里产生预约。 */
    gtok.owner = SP_NONE;
    gtok.epoch = 0u;
    e = motor_pwm_grant_to_diag(&gtok);
    CHECK("PS11b-diag_step_denied",
          e == -RT_EPERM && ps_sim_pwm_enable_calls() == en0 &&
          gtok.owner == SP_NONE && gtok.epoch == 0u &&
          sp_owner_is(SP_NONE) == RT_TRUE,
          fm("grant rc=%d enable 次数 %d->%d token=(owner=%d,epoch=%u) owner_is_none=%d",
             (int)e, en0, ps_sim_pwm_enable_calls(), (int)gtok.owner,
             (unsigned)gtok.epoch, (int)sp_owner_is(SP_NONE)));
    /* 只读体检: 同一拒绝条件, 但**不产生预约**(逐项零副作用) */
    en1 = ps_sim_pwm_enable_calls();
    dis1 = ps_sim_pwm_disable_calls();
    e = motor_diag_gate_check();
    CHECK("PS11b2-gate_check_is_read_only",
          e == -RT_EPERM && ps_sim_pwm_enable_calls() == en1 &&
          ps_sim_pwm_disable_calls() == dis1 && sp_owner_is(SP_NONE) == RT_TRUE,
          fm("diag_gate_check rc=%d enable %d->%d disable=%d owner_is_none=%d",
             (int)e, en1, ps_sim_pwm_enable_calls(), dis1,
             (int)sp_owner_is(SP_NONE)));

    m = tmc2209_get_write_gate_mask();
    CHECK("PS11c-tmc_write_gate_has_macro_bit",
          m != 0u && (m & TMC_WGATE_HW_NOT_VALIDATED) != 0u,
          fm("write gate mask=0x%X", (unsigned)m));

    /* 状态查询一律零输出写、零总线(PS11 要求"状态查询无输出写/UART") */
    dis0 = ps_sim_pwm_disable_calls();
    wr0 = ps_sim_pin_write_calls(PIN_NAME_DRV_ENABLE);
    bus0 = ps_fake_bus_reads();
    ((ps_cmd0_t)ps_mshref_cmd_motor_status)();
    ((ps_cmd0_t)ps_mshref_safety_state)();
    ((ps_cmd0_t)ps_mshref_step_status)();
    (void)motor_get_gate_fail_mask();
    motor_get_stop_state(&res, &src, &rc, RT_NULL);
    CHECK("PS11d-status_queries_have_no_side_effects",
          ps_sim_pwm_disable_calls() == dis0 &&
          ps_sim_pwm_enable_calls() == en0 &&
          ps_sim_pin_write_calls(PIN_NAME_DRV_ENABLE) == wr0 &&
          ps_fake_bus_reads() == bus0,
          fm("disable %d->%d enable %d->%d write %d->%d bus %d->%d", dis0,
             ps_sim_pwm_disable_calls(), en0, ps_sim_pwm_enable_calls(), wr0,
             ps_sim_pin_write_calls(PIN_NAME_DRV_ENABLE), bus0, ps_fake_bus_reads()));

    /* 快照与只读接口对同一个关断结果必须自洽 */
    if (motor_get_snapshot(&sn) != RT_EOK) { g_pre_bad++; printf("PRE-FAIL|snap\n"); }
    stop_state(&res, &src, &rc, RT_NULL);
    CHECK("PS11e-snapshot_and_readonly_agree",
          (mot_stop_result_t)sn.stop_result == res && sn.stop_src == src &&
          (rt_err_t)sn.stop_rc == rc && sn.armed == 0 &&
          sn.stop_fail_latched == 0,
          fm("snap stop=%u src=%u rc=%d latched=%u armed=%u vs api stop=%u src=%u rc=%d",
             (unsigned)sn.stop_result, (unsigned)sn.stop_src, (int)sn.stop_rc,
             (unsigned)sn.stop_fail_latched, (unsigned)sn.armed,
             (unsigned)res, (unsigned)src, (int)rc));
}
#endif /* !PS_GATE_HOST_OVERRIDE */

#ifdef PS_GATE_HOST_OVERRIDE
/* ---------------------------------------------------------------------- */
/* PS07(仅覆盖变体): 四条生产路径都真的走 checked 关断并消费错误            */
/* ---------------------------------------------------------------------- */
static rt_bool_t arm_for_ramp(void)
{
    if (!recover_clean()) return RT_FALSE;
    if (motor_arm() != RT_EOK)
    {
        g_pre_bad++;
        printf("PRE-FAIL|arm_for_ramp|mask=0x%X\n",
               (unsigned)motor_get_gate_fail_mask());
        return RT_FALSE;
    }
    return RT_TRUE;
}

static void cases_ramp(void)
{
    rt_err_t e;
    mot_stop_result_t res;
    rt_uint8_t src;
    rt_err_t rc;
    rt_bool_t lat;
    int i, d0, ev0;

    /* --- PS07a/b: 斜坡减到 0 时关断失败 => 不进正常 IDLE, 不做 RUN->READY --- */
    if (!arm_for_ramp()) g_pre_bad++;              /* 继续跑: 让本组判据自己 FAIL */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_set_target_hz(100);
    if (motor_start() != RT_EOK) { g_pre_bad++; printf("PRE-FAIL|start\n"); }
    motor_ramp_tick();                           /* 0->20Hz: 输出真的在跑 */
    if (!ps_sim_pwm_running()) { g_pre_bad++; printf("PRE-FAIL|not-running\n"); }
    d0 = ps_sim_pwm_disable_calls();
    ev0 = ps_event_sends();
    (void)motor_stop();                          /* target=0: 只是软件命令 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);  /* 这一拍起关断失败 */
    motor_ramp_tick();                           /* 20->0, 关断失败 */
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS07a-ramp_zero_fail_not_normal_idle",
          snap_state() == MOTOR_FAULT &&
          safety_state_get() != SAFETY_READY &&
          ps_sim_pwm_running() == 1 && snap_cur_hz() == 0u &&
          ps_sim_pwm_disable_calls() == d0 + 1,
          fm("state=%d safety=%s running=%d current_hz=%u(命令值) disable %d->%d",
             snap_state(), safety_state_name(safety_state_get()),
             ps_sim_pwm_running(), (unsigned)snap_cur_hz(), d0,
             ps_sim_pwm_disable_calls()));
    CHECK("PS07b-ramp_zero_fail_consumes_error",
          res == MOT_STOP_API_FAILED && src == MOT_STOP_SRC_RAMP_ZERO &&
          rc == -RT_EIO && lat == RT_TRUE && ps_event_sends() == ev0 + 1,
          fm("stop=%s src=%s rc=%d latched=%d event %d->%d",
             mot_stop_result_name(res), mot_stop_src_name(src), (int)rc,
             (int)lat, ev0, ps_event_sends()));

    /* --- PS07c: apply 失败回退 + fail-closed 消费关断错误 --- */
    if (!arm_for_ramp()) g_pre_bad++;              /* 继续跑: 让本组判据自己 FAIL */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_set_target_hz(100);
    if (motor_start() != RT_EOK) { g_pre_bad++; printf("PRE-FAIL|start2\n"); }
    d0 = ps_sim_pwm_disable_calls();
    ev0 = ps_event_sends();
    ps_sim_pwm_set_rc(-RT_EIO, RT_EOK, -RT_EIO); /* set 失败 + 关断也失败 */
    motor_ramp_tick();                           /* 20Hz 的 apply 失败 -> fail-closed */
    stop_state(&res, &src, RT_NULL, &lat);
    /* 2026-10-23 返修 R04 更新本条: 复审指出"回退里连关两次并用第二次的结果覆盖
     * 第一次的失败"不是产品要求, 动作合同改为**一次 checked 关断 + 携带其结果**
     * 完成 LOW/事件。原来的 `>= 2` 次数断言不是业务判据, 现按要求改为恰好一次,
     * 并保留/加强"失败被消费、来源正确、LOW 动作、锁存/FAULT、事件次数"这些判据。 */
    CHECK("PS07c-apply_fail_failclosed_consumes",
          snap_state() == MOTOR_FAULT &&
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          src == MOT_STOP_SRC_APPLY_FAIL &&
          res == MOT_STOP_API_FAILED && lat == RT_TRUE &&
          ps_event_sends() == ev0 + 1,
          fm("state=%d disable +%d src=%s stop=%s latched=%d event +%d",
             snap_state(), ps_sim_pwm_disable_calls() - d0, mot_stop_src_name(src),
             mot_stop_result_name(res), (int)lat, ps_event_sends() - ev0));

    /* --- PS07d: 运行守卫停机走生产 checked 关断(先成功一次, 再失败一次) --- */
    if (!arm_for_ramp()) g_pre_bad++;              /* 继续跑: 让本组判据自己 FAIL */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    ps_set_integrity(-RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    for (i = 0; i < (int)MOTOR_DRV_GUARD_TICKS; ++i) motor_ramp_tick();
    stop_state(&res, &src, RT_NULL, &lat);
    CHECK("PS07d-guard_stop_uses_checked_off",
          snap_state() == MOTOR_FAULT && src == MOT_STOP_SRC_GUARD_STOP &&
          ps_sim_pwm_disable_calls() >= d0 + 1 && lat == RT_FALSE &&
          res == MOT_STOP_API_ACCEPTED,
          fm("state=%d src=%s disable %d->%d stop=%s latched=%d", snap_state(),
             mot_stop_src_name(src), d0, ps_sim_pwm_disable_calls(),
             mot_stop_result_name(res), (int)lat));
    /* 第二次: 关断失败 => 必须锁存。守卫只在 armed 时跑, 所以先按生产路径重新 arm。 */
    if (!arm_for_ramp()) g_pre_bad++;              /* 继续跑: 让本组判据自己 FAIL */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    ps_set_integrity(-RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    for (i = 0; i < (int)MOTOR_DRV_GUARD_TICKS; ++i) motor_ramp_tick();
    stop_state(&res, &src, RT_NULL, &lat);
    CHECK("PS07d2-guard_stop_latches_on_fail",
          res == MOT_STOP_API_FAILED && src == MOT_STOP_SRC_GUARD_STOP &&
          lat == RT_TRUE && ps_sim_pwm_disable_calls() == d0 + 1,
          fm("stop=%s src=%s latched=%d disable %d->%d", mot_stop_result_name(res),
             mot_stop_src_name(src), (int)lat, d0, ps_sim_pwm_disable_calls()));
    ps_set_integrity(RT_EOK);

    /* --- PS07e: arm 失败回滚 —— PWM 错误优先于许可写错误返回 --- */
    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    ps_set_enable_write_rc(-RT_EPERM);            /* HIGH 写不进 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);   /* 回滚时的关断也失败 */
    e = motor_arm();
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS07e-arm_rollback_pwm_rc_first",
          e == -RT_EIO && rc == -RT_EIO &&
          src == MOT_STOP_SRC_ARM_ROLLBACK && lat == RT_TRUE &&
          ps_enable_write_calls(0) >= 1 && snap_state() == MOTOR_FAULT,
          fm("arm rc=%d stop_rc=%d src=%s latched=%d LOW 写=%d",
             (int)e, (int)rc, mot_stop_src_name(src), (int)lat,
             ps_enable_write_calls(0)));
    ps_set_enable_write_rc(RT_EOK);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);

    /* --- PS09c 对照: 人工恢复之后 bit7 释放, arm 重新成功(只收紧, 不永久锁死) --- */
    if (!recover_clean()) g_pre_bad++;
    e = motor_arm();
    CHECK("PS09c-arm_works_again_after_recovery",
          e == RT_EOK && gate_bit(MOT_GATE_STOP_UNCONFIRMED) == RT_FALSE,
          fm("arm rc=%d mask=0x%X", (int)e,
             (unsigned)motor_get_gate_fail_mask()));
    (void)motor_disarm();
}
#endif /* PS_GATE_HOST_OVERRIDE */

/* ---------------------------------------------------------------------- */
/* R01(=S1-NEW-01): 关断失败/未知期间不得把"软件 0Hz"当物理静止写 DIR       */
/* ---------------------------------------------------------------------- */
static void cases_direction(void)
{
    rt_err_t e;
    rt_bool_t lat;
    mot_stop_result_t res;
    int w0;

    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    safety_force_shutdown(FAULT_SOFT);            /* 生产统一停机: 关断失败 */
    motor_get_stop_state(&res, RT_NULL, RT_NULL, &lat);
    w0 = ps_sim_pin_write_calls(PIN_NAME_TMC_DIR);
    e = motor_set_direction(1);
    CHECK("PS12a-dir_refused_while_stop_uncertain",
          e != RT_EOK && ps_sim_pin_write_calls(PIN_NAME_TMC_DIR) == w0 &&
          lat == RT_TRUE && snap_state() == MOTOR_FAULT && snap_cur_hz() == 0u,
          fm("dir rc=%d DIR 写 %d->%d latch=%d state=%d cur=%u(命令值) -"
             " 已知关断失败不得据 0Hz 改方向", (int)e, w0,
             ps_sim_pin_write_calls(PIN_NAME_TMC_DIR), (int)lat,
             snap_state(), (unsigned)snap_cur_hz()));

    /* 对照: 两条路径都关断成功 + 许可 LOW + 锁存已人工复核清除 => 静止仍可设方向 */
    if (!recover_clean()) g_pre_bad++;
    motor_get_stop_state(&res, RT_NULL, RT_NULL, &lat);
    w0 = ps_sim_pin_write_calls(PIN_NAME_TMC_DIR);
    e = motor_set_direction(1);
    CHECK("PS12b-dir_still_allowed_when_all_closes_ok",
          lat == RT_FALSE && res == MOT_STOP_API_ACCEPTED && e == RT_EOK &&
          ps_sim_pin_write_calls(PIN_NAME_TMC_DIR) == w0 + 1 &&
          ps_sim_last_write(PIN_NAME_TMC_DIR) == PIN_HIGH,
          fm("dir rc=%d DIR 写 %d->%d last=%d latch=%d - 正常静止能力保留(只收紧)",
             (int)e, w0, ps_sim_pin_write_calls(PIN_NAME_TMC_DIR),
             (int)ps_sim_last_write(PIN_NAME_TMC_DIR), (int)lat));
    (void)motor_set_direction(0);
}

#ifdef PS_GATE_HOST_OVERRIDE
static rt_bool_t run_to_real_output(int hz)
{
    if (!arm_for_ramp()) return RT_FALSE;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_set_target_hz((rt_uint32_t)hz);
    if (motor_start() != RT_EOK)
    { g_pre_bad++; printf("PRE-FAIL|run_to_real_output start\n"); return RT_FALSE; }
    motor_ramp_tick();
    return ps_sim_pwm_running() ? RT_TRUE : RT_FALSE;
}

static void cases_direction_ovr(void)
{
    rt_err_t e;
    rt_bool_t lat;
    int w0;

    /* 真实 arm/start/斜坡让模拟 PWM 确实在跑, 然后注入"关断失败 + 许可 LOW 失败 +
     * 焊盘仍 HIGH + 禁能侧反馈矛盾", 走生产统一停机, 再看 DIR。 */
    if (!run_to_real_output(100))
        g_pre_bad++;   /* 前置不成立也继续: 必需 id 不能缺席(缺例=基础设施故障) */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    ps_set_enable_write_rc(-RT_EIO);
    ps_sim_set_pad_level(PIN_HIGH);
    ps_set_fb(0u, SAFETY_FB_PAD_MISMATCH);
    safety_force_shutdown(FAULT_SOFT);
    w0 = ps_sim_pin_write_calls(PIN_NAME_TMC_DIR);
    e = motor_set_direction(1);
    motor_get_stop_state(RT_NULL, RT_NULL, RT_NULL, &lat);
    CHECK("PS12c-dir_refused_while_real_output_running",
          e != RT_EOK && ps_sim_pin_write_calls(PIN_NAME_TMC_DIR) == w0 &&
          ps_sim_pwm_running() == 1 && lat == RT_TRUE &&
          safety_state_get() == SAFETY_FAULT_LATCHED,
          fm("dir rc=%d DIR 写 %d->%d running=%d latch=%d safety=%s pad=%d",
             (int)e, w0, ps_sim_pin_write_calls(PIN_NAME_TMC_DIR),
             ps_sim_pwm_running(), (int)lat,
             safety_state_name(safety_state_get()),
             (int)ps_sim_level(PIN_NAME_DRV_ENABLE)));
    /* 诊断侧 owner 未交回时同样不得借"FAULT+0Hz"改方向(只收紧, 不动正常能力) */
    ps_set_enable_write_rc(RT_EOK);
    ps_set_fb(0u, SAFETY_FB_OK);
}
#endif /* PS_GATE_HOST_OVERRIDE */

/* ---------------------------------------------------------------------- */
/* R02(=S1-NEW-02): init 找不到 PWM 设备的出口仍要尝试撤销许可             */
/*   ⚠ 本组必须在任何"成功 init"之前跑(motor_init 幂等, 线程建起来后走不到该出口) */
/* ---------------------------------------------------------------------- */
static void cases_init_missing(void)
{
    rt_err_t e;
    mot_stop_result_t res;
    rt_uint8_t src;
    rt_err_t rc;
    rt_bool_t lat;
    int tc0, low0, nul0;

    ps_sim_set_pad_level(PIN_HIGH);               /* 许可脚初态 HIGH */
    ps_sim_dev_set_present(0);                    /* 设备取不到 */
    tc0 = ps_sim_thread_create_calls();
    low0 = ps_enable_write_calls(0);
    nul0 = ps_sim_pwm_null_calls();
    e = motor_init();
    motor_get_stop_state(&res, &src, &rc, &lat);
    CHECK("PS13a-missing_dev_still_requests_low",
          ps_enable_write_calls(0) == low0 + 1 &&
          ps_sim_last_write(PIN_NAME_DRV_ENABLE) == PIN_LOW &&
          ps_sim_level(PIN_NAME_DRV_ENABLE) == PIN_LOW,
          fm("LOW 写 %d->%d last=%d 焊盘实测=%d - 错误出口也要降级",
             low0, ps_enable_write_calls(0),
             (int)ps_sim_last_write(PIN_NAME_DRV_ENABLE),
             (int)ps_sim_level(PIN_NAME_DRV_ENABLE)));
    CHECK("PS13b-missing_dev_keeps_both_rc_no_thread_no_null_pwm",
          e == -RT_ERROR && rc == -RT_ENOSYS && res == MOT_STOP_DEV_UNAVAILABLE &&
          src == MOT_STOP_SRC_INIT && lat == RT_TRUE &&
          ps_sim_thread_create_calls() == tc0 &&
          motor_get_health() == SUBSYS_FAILED &&
          ps_sim_pwm_null_calls() == nul0,
          fm("init rc=%d stop=%s stop_rc=%d latch=%d threads=%d null_pwm_calls=%d"
             " health=%s", (int)e, mot_stop_result_name(res), (int)rc, (int)lat,
             ps_sim_thread_create_calls() - tc0, ps_sim_pwm_null_calls() - nul0,
             subsys_health_name(motor_get_health())));

    /* 双失败对照: 连许可 LOW 也写不进 => 两个原始结果各自留档, 仍然不建线程 */
    ps_set_enable_write_rc(-RT_EIO);
    ps_sim_set_pad_level(PIN_HIGH);
    low0 = ps_enable_write_calls(0);
    e = motor_init();
    motor_get_stop_state(&res, RT_NULL, &rc, RT_NULL);
    CHECK("PS13c-missing_dev_and_low_both_fail_kept",
          e == -RT_ERROR && ps_enable_write_calls(0) == low0 + 1 &&
          rc == -RT_ENOSYS && ps_last_enable_write_rc() == -RT_EIO &&
          res == MOT_STOP_DEV_UNAVAILABLE && motor_get_health() == SUBSYS_FAILED &&
          ps_sim_thread_create_calls() == tc0,
          fm("init rc=%d stop_rc=%d low_rc=%d LOW 写=%d - 不被互相掩盖",
             (int)e, (int)rc, (int)ps_last_enable_write_rc(),
             ps_enable_write_calls(0)));
    ps_set_enable_write_rc(RT_EOK);

    /* 设备重新出现: 只能按既有初始化条件重来, 且不得沿用陈旧 dev_ok(必须真实调一次
     * 关断且从不带 NULL 句柄) */
    ps_sim_dev_set_present(1);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    nul0 = ps_sim_pwm_null_calls();
    low0 = ps_sim_pwm_disable_calls();
    e = motor_init();
    motor_get_stop_state(&res, RT_NULL, &rc, RT_NULL);
    CHECK("PS13d-device_back_re-attempts_real_close_no_stale_flag",
          e == -RT_EIO && res == MOT_STOP_API_FAILED && rc == -RT_EIO &&
          ps_sim_pwm_disable_calls() == low0 + 1 &&
          ps_sim_pwm_null_calls() == nul0 &&
          ps_sim_thread_create_calls() == tc0,
          fm("init rc=%d stop=%s rc=%d disable %d->%d null=%d threads=%d"
             " - 陈旧 mot_dev_ok 会在这里现形", (int)e, mot_stop_result_name(res),
             (int)rc, low0, ps_sim_pwm_disable_calls(),
             ps_sim_pwm_null_calls() - nul0, ps_sim_thread_create_calls() - tc0));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
}

/* ---------------------------------------------------------------------- */
/* R03(=S1-NEW-03): 普通 disarm 失败要交接给既有故障/人工恢复链            */
/*   被测代码只负责**投事件**; Safety 消费者(safety_thread.c 未在本链编译)   */
/*   是用 safety_force_shutdown 显式模拟的**桩**, 不冒称新线程端到端测试。   */
/* ---------------------------------------------------------------------- */
static void cases_fault_handoff(void)
{
    rt_err_t e;
    rt_bool_t lat;
    mot_stop_result_t res;
    int ev0, bits0;

    /* ① 干净 READY 起步, 只用普通外部入口; 失败必须自己把故障交出去 */
    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    ev0 = ps_event_sends();
    bits0 = (int)ps_event_bits();
    e = motor_disarm();
    motor_get_stop_state(&res, RT_NULL, RT_NULL, &lat);
    /* S2(2026-10-23 CP3 适配, 任务书 §8.2): 状态查询**先读 canonical 抑制位**,
     * 所以一次失败关断留下的抑制必须"立刻可观测"为 FAULT_LATCHED —— 原先 S1 断言的
     * "disarm 之后 Safety 仍是 READY" 描述的是 disarm 不**直接写** SS 状态, 这一点
     * 仍然成立(写 SS 状态的仍然是消费者), 但视图已按抑制位收紧。
     * 判据因此换成三条更强/更具体的事实: ①状态可观测为 FAULT_LATCHED;
     * ②该事实来自 canonical 抑制(sp_inhibited()); ③待整理义务确实留给了消费者
     * (safety_trip_pending()), 由 PS14b 的消费者真正落位并只触发一次黑匣子。 */
    CHECK("PS14a-disarm_pwmfail_posts_software_fault_once",
          e == -RT_EIO && lat == RT_TRUE && res == MOT_STOP_API_FAILED &&
          ps_event_sends() == ev0 + 1 && ps_event_last() == (rt_uint32_t)EVT_SOFT_FAULT &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          sp_inhibited() == RT_TRUE && safety_trip_pending() == RT_TRUE,
          fm("disarm rc=%d event %d->%d last=0x%X bits0=0x%X safety=%s latch=%d"
             " inhibited=%d trip_pending=%d",
             (int)e, ev0, ps_event_sends(), (unsigned)ps_event_last(),
             (unsigned)bits0, safety_state_name(safety_state_get()), (int)lat,
             (int)sp_inhibited(), (int)safety_trip_pending()));

    /* ② 既有消费者接管(桩): 统一停机 -> 合法故障态, 人工恢复入口从此可达 */
    safety_force_shutdown(FAULT_SOFT);            /* == safety_thread 消费 EVT_SOFT_FAULT */
    CHECK("PS14b-consumer_latches_so_manual_recovery_is_reachable",
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() == (rt_uint32_t)FAULT_SOFT,
          fm("safety=%s code=%u", safety_state_name(safety_state_get()),
             (unsigned)safety_fault_get()));

    /* ③ 一次普通成功不自动清锁存(仍然不能绕过人工恢复门) */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_disarm();
    motor_get_stop_state(&res, RT_NULL, RT_NULL, &lat);
    CHECK("PS14c-plain_success_after_handoff_does_not_self_recover",
          res == MOT_STOP_API_ACCEPTED && lat == RT_TRUE &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          gate_bit(MOT_GATE_STOP_UNCONFIRMED) == RT_TRUE,
          fm("stop=%s latch=%d safety=%s", mot_stop_result_name(res), (int)lat,
             safety_state_name(safety_state_get())));

    /* ④ 坏条件仍然拒绝(限位宏关闭 => 活动输入只有 DIAG; 这里把 DIAG 置成触发) */
    ps_sim_set_level(PIN_NAME_TMC_DIAG, PIN_HIGH);
    safety_fault_reset_manual();
    CHECK("PS14d-active_input_still_refuses_recovery",
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() != (rt_uint32_t)FAULT_NONE,
          fm("safety=%s code=%u (DIAG 触发时不得清除)",
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get()));
    ps_sim_set_level(PIN_NAME_TMC_DIAG, PIN_LOW);

    /* ⑤ 人工复核通过后确实恢复(不需要额外再制造一次故障) */
    safety_fault_reset_manual();
    motor_get_stop_state(&res, RT_NULL, RT_NULL, &lat);
    CHECK("PS14e-manual_recovery_clears_after_plain_failure",
          safety_state_get() == SAFETY_MANUAL_CLEAR &&
          safety_fault_get() == (rt_uint32_t)FAULT_NONE && lat == RT_FALSE,
          fm("safety=%s code=%u latch=%d stop=%s",
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get(),
             (int)lat, mot_stop_result_name(res)));

    /* ⑥ 不自激: 统一停机 + force_stop 失败期间不产生任何新事件 */
    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    ev0 = ps_event_sends();
    safety_force_shutdown(FAULT_SOFT);
    CHECK("PS14f-unified_shutdown_never_posts_events",
          ps_event_sends() == ev0 &&
          safety_state_get() == SAFETY_FAULT_LATCHED,
          fm("event %d->%d safety=%s (停机内部零投递)", ev0, ps_event_sends(),
             safety_state_name(safety_state_get())));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    if (!recover_clean()) g_pre_bad++;
}

#ifdef PS_GATE_HOST_OVERRIDE
/* 诊断侧同一个形状: pwm_test stop 失败 => 投事件; step_pwm_force_stop 本身不投 */
static void cases_fault_handoff_diag(void)
{
    mot_stop_result_t dres;
    rt_err_t drc;
    rt_bool_t dlat;
    int ev0;

    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("1000");
    if (step_pwm_output_active() != RT_TRUE)
    { g_pre_bad++; printf("PRE-FAIL|diag-handoff-not-owner\n"); }
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    ev0 = ps_event_sends();
    msh_pwm_test("stop");
    CHECK("PS14g-diag_stop_fail_posts_software_fault_once",
          ps_event_sends() == ev0 + 1 &&
          ps_event_last() == (rt_uint32_t)EVT_SOFT_FAULT &&
          step_pwm_output_active() == RT_TRUE,
          fm("event %d->%d last=0x%X owner=%d", ev0, ps_event_sends(),
             (unsigned)ps_event_last(), (int)step_pwm_output_active()));

    /* 统一停机内部调 force_stop 时**不得**再投一次(否则自激) */
    ev0 = ps_event_sends();
    safety_force_shutdown(FAULT_SOFT);
    CHECK("PS14h-force_stop_inside_shutdown_stays_silent",
          ps_event_sends() == ev0 &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          step_pwm_output_active() == RT_TRUE,
          fm("event %d->%d safety=%s owner=%d", ev0, ps_event_sends(),
             safety_state_name(safety_state_get()),
             (int)step_pwm_output_active()));

    /* 从此人工恢复入口可达: 成功一次关断 -> reset -> MANUAL_CLEAR + 锁存清零 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    safety_fault_reset_manual();
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS14i-diag_failure_manual_recovers",
          safety_state_get() == SAFETY_MANUAL_CLEAR &&
          safety_fault_get() == (rt_uint32_t)FAULT_NONE &&
          dlat == RT_FALSE && dres == MOT_STOP_API_ACCEPTED,
          fm("safety=%s code=%u diag_latched=%d diag=%s",
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get(),
             (int)dlat, mot_stop_result_name(dres)));
    if (!recover_clean()) g_pre_bad++;
}
#endif /* PS_GATE_HOST_OVERRIDE */

/* ---------------------------------------------------------------------- */
/* R04(=S1-NEW-04): apply 失败回退只走一次 checked 关断, 首错不被覆盖       */
/* ---------------------------------------------------------------------- */
#ifdef PS_GATE_HOST_OVERRIDE
static void cases_apply_sequence(void)
{
    mot_stop_result_t res;
    rt_uint8_t src;
    rt_err_t rc;
    rt_bool_t lat;
    int d0, ev0, low0, high0;

    /* 序列: 本次动作的第一次 disable 返回 -EIO, 之后回到成功 —— 修前的形状是
     * "连关两次 + 用第二次的成功覆盖第一次的失败"。 */
    if (!run_to_real_output(100))
        g_pre_bad++;   /* 前置不成立也继续: 必需 id 不能缺席(缺例=基础设施故障) */
    d0 = ps_sim_pwm_disable_calls();
    ev0 = ps_event_sends();
    low0 = ps_enable_write_calls(0);
    ps_sim_pwm_set_rc(-RT_EIO, RT_EOK, RT_EOK);   /* apply(set) 失败 */
    ps_sim_pwm_disable_seq(1, -RT_EIO);           /* 第一次关断失败, 之后成功 */
    motor_ramp_tick();                            /* 走生产回退路径 */
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS15a-apply_fail_single_checked_disable_keeps_first_error",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          ps_sim_pwm_disable_seq_left() == 0 &&
          rc == -RT_EIO && res == MOT_STOP_API_FAILED &&
          src == MOT_STOP_SRC_APPLY_FAIL && lat == RT_TRUE &&
          ps_enable_write_calls(0) == low0 + 1 && ps_event_sends() == ev0 + 1 &&
          snap_state() == MOTOR_FAULT,
          fm("disable %d->%d seq_left=%d stop_rc=%d stop=%s src=%s latch=%d"
             " LOW %d->%d event +%d state=%d", d0, ps_sim_pwm_disable_calls(),
             ps_sim_pwm_disable_seq_left(), (int)rc, mot_stop_result_name(res),
             mot_stop_src_name(src), (int)lat, low0, ps_enable_write_calls(0),
             ps_event_sends() - ev0, snap_state()));

    /* 之后一次真正成功的关断: 可以更新"当前结果", 但首错证据(锁存/门禁/事件计数)
     * 不被抹掉 —— 成功把 state 带回 IDLE 是既有语义, 关键是锁存仍需人工恢复。 */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    ev0 = ps_event_sends();
    high0 = ps_enable_write_calls(1);
    (void)motor_disarm();
    stop_state(&res, RT_NULL, &rc, &lat);
    CHECK("PS15b-later_success_does_not_erase_first_error",
          res == MOT_STOP_API_ACCEPTED && rc == RT_EOK && lat == RT_TRUE &&
          gate_bit(MOT_GATE_STOP_UNCONFIRMED) == RT_TRUE &&
          motor_arm() != RT_EOK && ps_enable_write_calls(1) == high0 &&
          ps_event_sends() == ev0,
          fm("stop=%s rc=%d latch=%d bit7=%d arm rc=%d HIGH 写 %d->%d event +%d",
             mot_stop_result_name(res), (int)rc, (int)lat,
             (int)gate_bit(MOT_GATE_STOP_UNCONFIRMED), (int)motor_arm(),
             high0, ps_enable_write_calls(1), ps_event_sends() - ev0));

    /* 对照: 关断本来就成功 => 一次调用、ACCEPTED、无锁存 */
    if (!run_to_real_output(100))
        g_pre_bad++;   /* 前置不成立也继续: 必需 id 不能缺席(缺例=基础设施故障) */
    d0 = ps_sim_pwm_disable_calls();
    ps_sim_pwm_set_rc(-RT_EIO, RT_EOK, RT_EOK);
    motor_ramp_tick();
    stop_state(&res, &src, &rc, &lat);
    CHECK("PS15c-apply_fail_control_all_ok_single_close",
          ps_sim_pwm_disable_calls() == d0 + 1 && rc == RT_EOK &&
          res == MOT_STOP_API_ACCEPTED && src == MOT_STOP_SRC_APPLY_FAIL &&
          lat == RT_FALSE && snap_state() == MOTOR_FAULT,
          fm("disable %d->%d stop=%s rc=%d latch=%d", d0,
             ps_sim_pwm_disable_calls(), mot_stop_result_name(res), (int)rc,
             (int)lat));
}
#endif /* PS_GATE_HOST_OVERRIDE */

/* ===================================================================== */
/* M5: safety_trip CONFIRM host contract                                    */
/* ===================================================================== */
static void cases_safety_trip(void)
{
    char *ok[]   = { "safety_trip", "CONFIRM" };
    char *bad1[] = { "safety_trip" };
    char *bad2[] = { "safety_trip", "CONFIRM", "extra" };
    char *bad3[] = { "safety_trip", "confirm" };
    char *bad4[] = { "safety_trip", "CONFIRMx" };
    int ev0;

    ps_cap_clear();
    ev0 = ps_event_sends();
    safety_trip_cmd(2, ok);
    CHECK("PS17a-safety_trip_valid_single_event",
          ps_event_sends() == ev0 + 1 &&
          ps_cap_has("EVT_SOFT_FAULT posted rc=0") &&
          !ps_cap_has("persisted") && !ps_cap_has("written") &&
          !ps_cap_has("stall detected"),
          fm("event %d->%d cap=%s", ev0, ps_event_sends(), ps_cap));

    ps_cap_clear();
    ev0 = ps_event_sends();
    safety_trip_cmd(1, bad1);
    safety_trip_cmd(3, bad2);
    safety_trip_cmd(2, bad3);
    safety_trip_cmd(2, bad4);
    CHECK("PS17b-safety_trip_bad_args_zero_event",
          ps_event_sends() == ev0 &&
          ps_cap_has("usage: safety_trip CONFIRM"),
          fm("event unchanged=%d", ps_event_sends() - ev0));

    ps_isr_set(1);
    ps_cap_clear();
    ev0 = ps_event_sends();
    safety_trip_cmd(2, ok);
    CHECK("PS17c-safety_trip_isr_silent_zero_event",
          ps_event_sends() == ev0 && ps_cap_len == 0,
          fm("event +%d cap_len=%d", ps_event_sends() - ev0, ps_cap_len));
    ps_isr_set(0);

    {
        rt_base_t lvl;
        ps_cap_clear();
        ev0 = ps_event_sends();
        lvl = sp_win_begin();
        safety_trip_cmd(2, ok);
        sp_win_end(lvl);
        CHECK("PS17d-safety_trip_masked_silent_zero_event",
              ps_event_sends() == ev0 && ps_cap_len == 0,
              fm("event +%d cap_len=%d", ps_event_sends() - ev0, ps_cap_len));
    }

    /* Invalid context must be silent even when the arguments are also invalid:
     * context is checked before argument parsing/usage output. */
    ps_isr_set(1);
    ps_cap_clear();
    ev0 = ps_event_sends();
    safety_trip_cmd(3, bad2);
    safety_trip_cmd(2, bad3);
    CHECK("PS17f-safety_trip_isr_bad_args_silent",
          ps_event_sends() == ev0 && ps_cap_len == 0,
          fm("event +%d cap_len=%d", ps_event_sends() - ev0, ps_cap_len));
    ps_isr_set(0);

    {
        rt_base_t lvl;
        ps_cap_clear();
        ev0 = ps_event_sends();
        lvl = sp_win_begin();
        safety_trip_cmd(3, bad2);
        safety_trip_cmd(2, bad3);
        sp_win_end(lvl);
        CHECK("PS17g-safety_trip_masked_bad_args_silent",
              ps_event_sends() == ev0 && ps_cap_len == 0,
              fm("event +%d cap_len=%d", ps_event_sends() - ev0, ps_cap_len));
    }

    ps_thread_null_set(1);
    ps_cap_clear();
    ev0 = ps_event_sends();
    safety_trip_cmd(2, ok);
    safety_trip_cmd(3, bad2);
    CHECK("PS17h-safety_trip_no_thread_silent",
          ps_event_sends() == ev0 && ps_cap_len == 0,
          fm("event +%d cap_len=%d", ps_event_sends() - ev0, ps_cap_len));
    ps_thread_null_set(0);

    /* Post failure: protection still runs, but the summary must not claim
     * persistence.  The short-protection pending fact is independent of the
     * failed event send. */
    ps_set_event_send_rc(-RT_EIO);
    ps_cap_clear();
    ev0 = ps_event_sends();
    safety_trip_cmd(2, ok);
    {
        char expect_rc[32];
        snprintf(expect_rc, sizeof(expect_rc), "posted rc=%d", (int)-RT_EIO);
        CHECK("PS17e-safety_trip_post_fail_not_persisted",
              ps_event_sends() == ev0 + 1 &&
              ps_cap_has("posted rc=") && ps_cap_has(expect_rc) &&
              !ps_cap_has("persisted") && !ps_cap_has("written") &&
              safety_trip_pending() == RT_TRUE,
              fm("event +%d pending=%d cap=%s", ps_event_sends() - ev0,
                 (int)safety_trip_pending(), ps_cap));
    }
    ps_set_event_send_rc(RT_EOK);
}

/* ===================================================================== */
/* M6 C1: strict CLI argument contracts (real static command functions)      */
/* ===================================================================== */
static rt_uint32_t snap_tgt_hz(void)
{
    motor_snapshot_t sn;
    return (motor_get_snapshot(&sn) == RT_EOK) ? sn.target_hz : 0xFFFFFFFFu;
}

static int cap_line_max(const char *prefix)
{
    int i = 0, best = -1;
    size_t pl = strlen(prefix);
    while (i < ps_cap_len)
    {
        int j = i;
        while (j < ps_cap_len && ps_cap[j] != '\n') j++;
        if (j - i >= (int)pl && memcmp(ps_cap + i, prefix, pl) == 0 && j - i > best)
            best = j - i;
        i = j + 1;
    }
    return best;
}

static int cap_has_nul_bytes(void)
{
    int i;
    for (i = 0; i < ps_cap_len; ++i) if (ps_cap[i] == '\0') return 1;
    return 0;
}

static void cases_cli(void)
{
    char *d1[] = { "motor_dir", "1" };
    char *d0[] = { "motor_dir", "0" };
    char *d10[] = { "motor_dir", "10" };
    char *djunk[] = { "motor_dir", "1junk" };
    char *dneg[] = { "motor_dir", "-1" };
    char *dplus[] = { "motor_dir", "+1" };
    char *dhex[] = { "motor_dir", "0x1" };
    char *done[] = { "motor_dir" };
    char *dextra[] = { "motor_dir", "1", "x" };
    char *tneg[] = { "motor_target", "-1" };
    char *tjunk[] = { "motor_target", "100x" };
    char *tover[] = { "motor_target", "4294967296" };
    char *tmax1[] = { "motor_target", "20001" };  /* MOTOR_MAX_HZ=20000 */
    char *tmax[] = { "motor_target", "20000" };
    char *tzero[] = { "motor_target", "0" };
    char *t300[] = { "motor_target", "300" };
    char *a1[] = { "motor_arm" };
    char *a2[] = { "motor_arm", "x" };
    char *s1[] = { "motor_start" };
    char *s2[] = { "motor_start", "x" };
    rt_uint32_t t0;
    int w0, en0, ev0;
    rt_int32_t last0;

    /* DIR: invalid forms must not write; 0/1 must write exactly one level. */
    w0 = ps_sim_pin_write_calls(PIN_NAME_TMC_DIR);
    last0 = ps_sim_last_write(PIN_NAME_TMC_DIR);
    cmd_motor_dir(1, done);
    cmd_motor_dir(3, dextra);
    cmd_motor_dir(2, d10);
    cmd_motor_dir(2, djunk);
    cmd_motor_dir(2, dneg);
    cmd_motor_dir(2, dplus);
    cmd_motor_dir(2, dhex);
    CHECK("PS18a-motor_dir_cli_strict",
          ps_sim_pin_write_calls(PIN_NAME_TMC_DIR) == w0 &&
          ps_sim_last_write(PIN_NAME_TMC_DIR) == last0,
          fm("DIR writes %d->%d last=%d", w0,
             ps_sim_pin_write_calls(PIN_NAME_TMC_DIR),
             (int)ps_sim_last_write(PIN_NAME_TMC_DIR)));
    cmd_motor_dir(2, d1);
    if (ps_sim_pin_write_calls(PIN_NAME_TMC_DIR) != w0 + 1 ||
        ps_sim_last_write(PIN_NAME_TMC_DIR) != PIN_HIGH)
        g_pre_bad++;
    cmd_motor_dir(2, d0);
    CHECK("PS18a2-motor_dir_valid_writes",
          ps_sim_pin_write_calls(PIN_NAME_TMC_DIR) == w0 + 2 &&
          ps_sim_last_write(PIN_NAME_TMC_DIR) == PIN_LOW,
          fm("DIR valid writes %d level=%d", ps_sim_pin_write_calls(PIN_NAME_TMC_DIR),
             (int)ps_sim_last_write(PIN_NAME_TMC_DIR)));

    /* TARGET: invalid forms leave the target unchanged. */
    cmd_motor_target(2, tzero);
    t0 = snap_tgt_hz();
    cmd_motor_target(1, done);
    cmd_motor_target(3, dextra);
    cmd_motor_target(2, tneg);
    cmd_motor_target(2, tjunk);
    cmd_motor_target(2, tover);
    cmd_motor_target(2, tmax1);
    CHECK("PS18b-motor_target_cli_strict",
          snap_tgt_hz() == t0,
          fm("target %u->%u (must stay)", (unsigned)t0, (unsigned)snap_tgt_hz()));
    cmd_motor_target(2, tmax);
    if (snap_tgt_hz() != MOTOR_MAX_HZ) g_pre_bad++;
    cmd_motor_target(2, tzero);
    CHECK("PS18b2-motor_target_valid_bounds",
          snap_tgt_hz() == 0,
          fm("target max accepted then 0; now=%u", (unsigned)snap_tgt_hz()));

    /* ARM/START: extra arguments must not write HIGH, arm, start or post. */
    en0 = ps_enable_write_calls(1);
    ev0 = ps_event_sends();
    cmd_motor_arm(2, a2);
    cmd_motor_start(2, s2);
    CHECK("PS18c-arm_start_extra_args_zero_action",
          ps_enable_write_calls(1) == en0 && ps_event_sends() == ev0 &&
          snap_armed() == 0 && snap_state() == MOTOR_IDLE,
          fm("HIGH写 %d->%d event +%d armed=%d state=%d",
             en0, ps_enable_write_calls(1), ps_event_sends() - ev0,
             snap_armed(), snap_state()));

#ifdef PS_GATE_HOST_OVERRIDE
    /* Positive control: arm first (target must be 0), then target/start. */
    cmd_motor_arm(1, a1);
    if (snap_armed() != 1) g_pre_bad++;
    cmd_motor_target(2, t300);
    cmd_motor_start(1, s1);
    CHECK("PS18d-arm_start_valid_reaches_api",
          snap_armed() == 1 && (snap_state() == MOTOR_ACCEL || snap_state() == MOTOR_CRUISE) &&
          snap_tgt_hz() == 300,
          fm("armed=%d state=%d tgt=%u", snap_armed(), snap_state(),
             (unsigned)snap_tgt_hz()));
    cmd_motor_stop();
    {
        int i;
        for (i = 0; i < 4000 && snap_state() != MOTOR_IDLE; ++i) motor_ramp_tick();
    }
    cmd_motor_disarm();
    CHECK("PS18e-valid_cli_cleanup",
          snap_armed() == 0 && snap_state() == MOTOR_IDLE && snap_cur_hz() == 0,
          fm("after stop/disarm armed=%d state=%d cur=%u", snap_armed(),
             snap_state(), (unsigned)snap_cur_hz()));
#else
    /* Target build: no gate override, valid arm is refused by the macro gate. */
    cmd_motor_arm(1, a1);
    CHECK("PS18d-target_gate_refuses_valid_arm",
          snap_armed() == 0 && ps_enable_write_calls(1) == en0,
          fm("armed=%d HIGH写=%d", snap_armed(), ps_enable_write_calls(1)));
    CHECK("PS18e-valid_cli_cleanup", snap_state() == MOTOR_IDLE, "idle");
#endif

    /* C3: key machine-field lines stay short and complete (max target). */
    cmd_motor_target(2, tmax);
    ps_cap_clear();
    cmd_motor_status();
    step_status();
    safety_state();
    CHECK("PS18f-key_status_lines_no_truncation",
          !cap_has_nul_bytes() &&
          cap_line_max("[MOT] cur_hz=") > 0 && cap_line_max("[MOT] cur_hz=") <= 110 &&
          cap_line_max("[MOT] armed=") > 0 && cap_line_max("[MOT] armed=") <= 110 &&
          cap_line_max("[STEP] dev=") > 0 && cap_line_max("[STEP] dev=") <= 110 &&
          cap_line_max("[SS] state=") > 0 && cap_line_max("[SS] state=") <= 110,
          fm("max lens mot=%d armed=%d step=%d ss=%d nul=%d",
             cap_line_max("[MOT] cur_hz="), cap_line_max("[MOT] armed="),
             cap_line_max("[STEP] dev="), cap_line_max("[SS] state="),
             cap_has_nul_bytes()));
    cmd_motor_target(2, tzero);

    /* C3: a negative rc in the stop-result machine line must survive intact.
     * Inject one STEP-disable failure so the recorded stop_rc is -EIO, then
     * read the line back from the real status command. */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    cmd_motor_disarm();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    ps_cap_clear();
    cmd_motor_status();
    {
        char expect_rc[32];
        snprintf(expect_rc, sizeof(expect_rc), "rc=%d", (int)-RT_EIO);
        CHECK("PS18h-negative_rc_status_line",
              !cap_has_nul_bytes() &&
              cap_line_max("[MOT] stop result=") > 0 &&
              cap_line_max("[MOT] stop result=") <= 110 &&
              ps_cap_has("stop result=api-failed") &&
              ps_cap_has("src=disarm") && ps_cap_has(expect_rc),
              fm("len=%d has_rc=%d cap=%s",
                 cap_line_max("[MOT] stop result="), ps_cap_has(expect_rc), ps_cap));
    }

    /* Longest safety enum line. */
    safety_force_shutdown(FAULT_SELF_TEST);
    ps_cap_clear();
    safety_state();
    CHECK("PS18g-safety_longest_enum_line",
          cap_line_max("[SS] state=FAULT_LATCHED") > 0 &&
          cap_line_max("[SS] state=FAULT_LATCHED") <= 110 && !cap_has_nul_bytes(),
          fm("len=%d nul=%d", cap_line_max("[SS] state=FAULT_LATCHED"),
             cap_has_nul_bytes()));
}

/* ===================================================================== */
#ifdef PS_CLI_ONLY
int main(void)
{
    ps_sim_reset();
    wire_pins();
    if (safety_state_boot() != RT_EOK) { printf("FATAL|boot\n"); return 2; }
    if (safety_transition(SAFETY_READY) != RT_EOK) { printf("FATAL|ready\n"); return 2; }
    if (motor_init() != RT_EOK) { printf("FATAL|motor_init\n"); return 2; }
    cases_cli();
    printf("SUMMARY|cases=%d pass=%d fail=%d pre_bad=%d\n",
           g_run, g_pass, g_fail, g_pre_bad);
    return (g_fail || g_pre_bad) ? 1 : 0;
}
#else
int main(void)
{
    ps_sim_reset();
    wire_pins();

    if (safety_state_boot() != RT_EOK) { printf("FATAL|safety_state_boot\n"); return 2; }
    if (safety_transition(SAFETY_READY) != RT_EOK)
    {
        printf("FATAL|transition state=%s\n", safety_state_name(safety_state_get()));
        return 2;
    }

#ifdef PS_GATE_HOST_OVERRIDE
    printf("VARIANT|host_ovr\n");
#else
    printf("VARIANT|target\n");
#endif

    /* 顺序是刻意的: cases_init_missing/cases_init 之前还不能有正常运行的斜坡线程
     * (PS01c 与 PS13b 的"线程=0"才判得住); R03/R04 组放在最后, 各自先走生产恢复。 */
    cases_init_missing();
    cases_init();
    cases_estop();
    cases_disarm();
    cases_diag_never_held();
#ifdef PS_GATE_HOST_OVERRIDE
    cases_diag_ovr();
#endif
    cases_shutdown();
    cases_clear();
    cases_enn();
    cases_direction();
    cases_fault_handoff();
#ifdef PS_GATE_HOST_OVERRIDE
    cases_direction_ovr();
    cases_fault_handoff_diag();
    cases_apply_sequence();
    cases_ramp();
#else
    cases_target_gates();
#endif

    /* CP3 §9 第 2/5 项的总账: 整条链跑完之后, "在 PRIMASK 窗口内取锁/睡眠/打印"
     * 必须一次都没有发生过。计数由端口在每个可疑入口按当前掩码状态累加。 */
    cases_safety_trip();
    CHECK("PS16a-window_forbidden_calls_zero",
          ps_win_violations() == 0,
          fm("窗口内禁止项命中=%d 最后一次=%s 结束时掩码=%d 深度=%d",
             ps_win_violations(), ps_win_last_item(),
             ps_win_masked(), ps_win_depth()));

    printf("SUMMARY|cases=%d pass=%d fail=%d pre_bad=%d\n",
           g_run, g_pass, g_fail, g_pre_bad);
    /* 退出码合同: 0=全部 PASS; 1=业务失败(含"生产恢复路径没能把系统带回干净起点" ——
     * 那也是被测行为的一部分, 用例本身照样判 FAIL, 不会出现"缺例")。
     * 只有夹具自身跑不起来(FATAL)才用 2, 那种情况 runner 一律判基础设施故障。 */
    if (g_pre_bad) printf("RESULT|RECOVERY-NOT-ESTABLISHED (前置按业务失败计)\n");
    return (g_fail || g_pre_bad) ? 1 : 0;
}
#endif /* PS_CLI_ONLY */
