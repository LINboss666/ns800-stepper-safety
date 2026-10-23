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
static rt_uint32_t c_event_bits;
static struct rt_thread c_thread_store[4];

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{ (void)flags; if (m) { m->name = name; m->inited = RT_TRUE; } return RT_EOK; }
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t t)
{ (void)m; (void)t; c_mutex_take++; return RT_EOK; }
rt_err_t rt_mutex_release(struct rt_mutex *m)
{ (void)m; c_mutex_release++; return RT_EOK; }
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag)
{ (void)flag; if (e) { e->name = name; e->inited = RT_TRUE; } return RT_EOK; }
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event)
{ (void)e; c_event_send++; c_event_bits |= event; return RT_EOK; }
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
{ (void)p; (void)ch; (void)per; (void)pul; calls_set++; return rc_set; }

rt_err_t rt_pwm_enable(struct rt_device_pwm *p, int ch)
{
    (void)p; (void)ch; calls_enable++;
    if (rc_enable == RT_EOK) out_running = 1;     /* 只有 enable 成功才认为"输出在跑" */
    return rc_enable;
}

/* ⚠ 桩语义核心: disable 失败时**不动** out_running —— 桩不替固件把输出清掉。
 * "软件自称已停但模拟侧还在跑"会被 ps_sim_pwm_running() 直接照出来。 */
rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    (void)p; (void)ch; calls_disable++;
    if (rc_disable == RT_EOK) out_running = 0;
    return rc_disable;
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
void ps_event_bits_clear(void)         { c_event_bits = 0; }
void ps_set_protect(int r, int i, int p, int g)
{ protect_ready = r; irq_attached = i; polarity_ok = p; gpio_ready = g; }
void ps_set_integrity(rt_err_t e) { f_integrity = e; }
void ps_set_cfg_ok(rt_bool_t ok, tmc_drv_cfg_state_t st) { f_cfg_ok = ok; f_cfg_state = st; }
void ps_set_tmc_health(subsys_health_t h) { f_tmc_health = h; }
int  ps_cfg_txn_begins(void) { return cfg_txn_begins; }
int  ps_cfg_txn_ends(void)   { return cfg_txn_ends; }
int  ps_fake_bus_reads(void) { return fake_bus_reads; }

void ps_sim_reset(void)
{
    n_pins = 0;
    dev_present = 1;
    rc_set = rc_enable = rc_disable = RT_EOK;
    calls_set = calls_enable = calls_disable = 0;
    out_running = 0;
    enable_write_rc = RT_EOK; last_enable_ret = RT_EOK; pad_level = PIN_LOW; pad_stuck = 0;
    writes_low = writes_high = 0;
    fb_dis = fb_en = SAFETY_FB_OK; fb_confirm_rc = RT_EOK;
    fb_confirm_calls = fb_now_calls = 0; confirm_fail_after = -1;
    bb_triggers = 0;
    c_mutex_take = c_mutex_release = c_event_send = 0; c_event_bits = 0;
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
rt_err_t  tmc2209_config_integrity_check(void) { fake_bus_reads++; return f_integrity; }
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
    rt_err_t e;
    mot_stop_result_t dres;
    rt_err_t drc;
    rt_bool_t dlat;
    int d0;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    d0 = ps_sim_pwm_disable_calls();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);   /* 就算注入失败也不该被尝试 */
    e = step_pwm_force_stop();
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS05h-not-held-close_is_not_a_failure",
          e == RT_EOK && dlat == RT_FALSE && dres == MOT_STOP_NOT_ATTEMPTED &&
          step_pwm_output_active() == RT_FALSE &&
          step_pwm_off_confirmed() == RT_TRUE &&
          ps_sim_pwm_disable_calls() == d0,
          fm("rc=%d disable %d->%d last=%s latched=%d active=%d off_confirmed=%d",
             (int)e, d0, ps_sim_pwm_disable_calls(), mot_stop_result_name(dres),
             (int)dlat, (int)step_pwm_output_active(),
             (int)step_pwm_off_confirmed()));

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
    int en0, d0;

    /* 前置不成立时**不跳过本组用例**: 记一次 PRE-FAIL 让后续断言自己判 FAIL,
     * 这样"必需 id 缺席"永远不会发生(缺例按纪律是基础设施故障)。 */
    if (!recover_clean()) g_pre_bad++;
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    msh_pwm_test("1000");                         /* 真持有输出 */
    if (step_pwm_output_active() != RT_TRUE || !ps_sim_pwm_running())
    { g_pre_bad++; printf("PRE-FAIL|diag-did-not-own\n"); }   /* 继续: 让 PS05* 自己 FAIL */

    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    e = step_pwm_force_stop();
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    CHECK("PS05a-force_stop_fail_keeps_owner",
          e == -RT_EIO && step_pwm_output_active() == RT_TRUE &&
          step_pwm_off_confirmed() == RT_FALSE && dlat == RT_TRUE &&
          dres == MOT_STOP_API_FAILED && ps_sim_pwm_disable_calls() == d0 + 1,
          fm("rc=%d active=%d off_confirmed=%d latched=%d last=%s",
             (int)e, (int)step_pwm_output_active(), (int)step_pwm_off_confirmed(),
             (int)dlat, mot_stop_result_name(dres)));

    /* 再试一次仍然失败 => 所有权仍未交回(不谎报 "released") */
    (void)step_pwm_force_stop();
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

    /* 借用门在六道门之外因锁存而拒绝(把 Motor 侧/许可/反馈都保持干净) */
    e = motor_pwm_grant_to_diag();
    CHECK("PS05f-grant_refused_by_diag_latch",
          e == -RT_EPERM && step_pwm_off_confirmed() == RT_FALSE,
          fm("grant rc=%d off_confirmed=%d (宏全开也不放行未复核的关断失败)",
             (int)e, (int)step_pwm_off_confirmed()));

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

    /* 交回通道: 一次成功的关断被接受 => 释放所有权(锁存仍在, 由人工恢复清除) */
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    if (step_pwm_force_stop() != RT_EOK)
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
    else want_dis = 2;
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
    if (step_pwm_force_stop() != RT_EOK)
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
    if (step_pwm_force_stop() != RT_EOK)
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
    else want_dis = 2;
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
    if (step_pwm_force_stop() != RT_EOK)
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
    int high0, en0, dis0, wr0, bus0;

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
    e = motor_pwm_grant_to_diag();
    CHECK("PS11b-diag_step_denied",
          e == -RT_EPERM && ps_sim_pwm_enable_calls() == en0,
          fm("grant rc=%d enable 次数 %d->%d", (int)e, en0,
             ps_sim_pwm_enable_calls()));

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
    CHECK("PS07c-apply_fail_failclosed_consumes",
          snap_state() == MOTOR_FAULT &&
          ps_sim_pwm_disable_calls() >= d0 + 2 &&
          (src == MOT_STOP_SRC_APPLY_FAIL || src == MOT_STOP_SRC_FAIL_CLOSED) &&
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

/* ===================================================================== */
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

    /* 顺序是刻意的: cases_init 之前还不能有正常运行的斜坡线程(PS01c 才判得住) */
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
#ifdef PS_GATE_HOST_OVERRIDE
    cases_ramp();
#else
    cases_target_gates();
#endif

    printf("SUMMARY|cases=%d pass=%d fail=%d pre_bad=%d\n",
           g_run, g_pass, g_fail, g_pre_bad);
    /* 退出码合同: 0=全部 PASS; 1=业务失败(含"生产恢复路径没能把系统带回干净起点" ——
     * 那也是被测行为的一部分, 用例本身照样判 FAIL, 不会出现"缺例")。
     * 只有夹具自身跑不起来(FATAL)才用 2, 那种情况 runner 一律判基础设施故障。 */
    if (g_pre_bad) printf("RESULT|RECOVERY-NOT-ESTABLISHED (前置按业务失败计)\n");
    return (g_fail || g_pre_bad) ? 1 : 0;
}
