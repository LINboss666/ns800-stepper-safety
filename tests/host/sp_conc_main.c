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
#include <pthread.h>
#include <unistd.h>

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
int s2_logs_in_isr;                     /* K5: ISR/中断上下文里的打印次数(必须恒 0) */

/* ---- 共享状态与前置声明(定义分散在本文件后面各小节) ---- */
static __thread int s2_tls_nest;        /* >0 = 正在 ISR 交付上下文 */
static int s_isr_nest;                  /* 显式注入"调用点本身在 ISR 里" */
#define S2_SEQ_CONTEND 20               /* 固定序列位: 有线程在门外等业务锁 */
void s2_seq_set(int bit);
static void s2_run_hook(const char *where);

static void ps_kprintf(const char *fmt, ...)
{
    /* 控制台在目标上是逐字符忙等(每字符 ≤BSP_NS800_UART_TX_TIMEOUT=6000ms), 属于
     * 短窗口内的禁止项; 这里只**观察**(掩码为 1 才计数), 不改任何行为。 */
    ps_win_note("rt_kprintf");
    if (s2_tls_nest || s_isr_nest) s2_logs_in_isr++;
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

/* ---------- S2: 真 pthread 互斥量(生产代码等的就是这把锁, 不模拟成功) ----------
 * rt_mutex_init 把 (name -> 对象指针) 登记下来, 于是"另一线程真实持 ss_lock/mot_lock"
 * 用的是**同一个** struct rt_mutex 实例, 而不是同名副本。
 * ⚠ 在已屏蔽窗口内 take 仍然计数为禁止项(ps_win_note), 但这里**真的**去阻塞 ——
 *   那样"窗口内不许取锁"一旦破功就会死锁, 由 runner 的墙钟 timeout 抓到。 */
#define S2_MAX_MTX 8
/* RT-Thread 的 rt_mutex 是**按持有线程递归计数**的(同线程重入合法) —— 主机模型必须
 * 一致, 否则"持 ss_lock 时又触发一次要 ss_lock 的慢整理"会自我死锁
 * (2026-10-24 在 C12/C14 上实测到)。owned/count 只在 s2_mtx_tab 之外由锁所有者改,
 * 与 real 互斥量的持有关系一致。 */
typedef struct {
    struct rt_mutex *key;
    const char      *name;
    pthread_mutex_t  real;
    pthread_t        owner;
    int              owned;
    int              count;
} s2_slot;
static s2_slot s2_mtx[S2_MAX_MTX];
static int s2_mtx_n;
static s2_slot *s2_mtx_slot_of(struct rt_mutex *m);
static pthread_mutex_t s2_mtx_tab = PTHREAD_MUTEX_INITIALIZER;
static const char *s2_lock_fail_name;          /* 注入: 该名字的锁一律返回 -RT_EIO */
static int s2_lock_fail_hits;

static pthread_mutex_t *s2_mtx_bind(struct rt_mutex *m, const char *name)
{
    int i;
    pthread_mutex_t *out = RT_NULL;

    pthread_mutex_lock(&s2_mtx_tab);
    for (i = 0; i < s2_mtx_n; ++i)
        if (s2_mtx[i].key == m) { out = &s2_mtx[i].real; break; }
    if (out == RT_NULL && s2_mtx_n < S2_MAX_MTX && m != RT_NULL)
    {
        s2_mtx[s2_mtx_n].key  = m;
        s2_mtx[s2_mtx_n].name = name;
        s2_mtx[s2_mtx_n].owned = 0;
        s2_mtx[s2_mtx_n].count = 0;
        pthread_mutex_init(&s2_mtx[s2_mtx_n].real, RT_NULL);
        out = &s2_mtx[s2_mtx_n].real;
        s2_mtx_n++;
    }
    pthread_mutex_unlock(&s2_mtx_tab);
    return out;
}

struct rt_mutex *s2_mtx_by_name(const char *name);   /* 夹具侧: 按名字找到生产那把锁 */

/* 竞争观测: "有线程在门外等某把业务锁"的次数与名字 —— 用例据此证明慢整理确实
 * 排在锁之后, 而不是看起来像。S2_SEQ_CONTEND 是序列位里的固定一位。 */
#define S2_SEQ_CONTEND 20
void s2_seq_set(int bit);                      /* 定义在后面"序列点"小节 */
int  s2_contend_hits;
const char *s2_contend_name = "(none)";
void s2_contend_clear(void) { s2_contend_hits = 0; s2_contend_name = "(none)"; }
int  s2_seq_contend_seen(void) { return s2_contend_hits; }

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{
    (void)flags;
    if (m) { m->name = name; m->inited = RT_TRUE; s2_mtx_bind(m, name); }
    return RT_EOK;
}

rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t t)
{
    pthread_mutex_t *real;
    s2_slot *slot;
    int e;

    c_mutex_take++;
    ps_win_note("rt_mutex_take");
    if (s2_lock_fail_name && m != RT_NULL && m->name != RT_NULL &&
        strcmp(m->name, s2_lock_fail_name) == 0)
    {
        s2_lock_fail_hits++;
        return -RT_EIO;
    }
    real = s2_mtx_bind(m, m ? m->name : RT_NULL);
    if (real == RT_NULL) return -RT_EINVAL;
    slot = s2_mtx_slot_of(m);
    if (slot != RT_NULL && slot->owned && pthread_equal(slot->owner, pthread_self()))
    {
        slot->count++;                       /* 同线程重入: 与 RT-Thread 语义一致 */
        return RT_EOK;
    }
    if (t == RT_WAITING_NO)
    {
        if (pthread_mutex_trylock(real) != 0) return -RT_EBUSY;
        if (slot != RT_NULL)
        {
            slot->owner = pthread_self(); slot->owned = 1; slot->count = 1;
        }
        return RT_EOK;
    }
    /* 竞争观测点: trylock 拿不到 = **确实有别的线程持有**这把业务锁, 此时才记一次
     * "门外等待"并置序列位, 然后真阻塞。之前把 trylock 探测去掉(一律记)会让
     * holder 把自己的取锁当成竞争者, 观察时机被提前(2026-10-24 实测)。 */
    e = pthread_mutex_trylock(real);
    if (e != 0)
    {
        s2_contend_hits++;
        s2_contend_name = (m->name ? m->name : "?");
        s2_seq_set(S2_SEQ_CONTEND);
        e = pthread_mutex_lock(real);              /* 真阻塞到拿到为止 */
    }
    if (e == 0 && slot != RT_NULL)
    {
        slot->owner = pthread_self(); slot->owned = 1; slot->count = 1;
    }
    return (e == 0) ? RT_EOK : RT_ERROR;
}

rt_err_t rt_mutex_release(struct rt_mutex *m)
{
    pthread_mutex_t *real = s2_mtx_bind(m, m ? m->name : RT_NULL);
    s2_slot *slot = s2_mtx_slot_of(m);

    c_mutex_release++;
    if (real == RT_NULL) return -RT_EINVAL;
    if (slot != RT_NULL && slot->owned)
    {
        if (!pthread_equal(slot->owner, pthread_self()))
            return -RT_EPERM;                    /* 不是自己的锁: 如实失败, 不偷放 */
        if (--slot->count > 0) return RT_EOK;    /* 递归计数还没放完 */
        slot->owned = 0;
    }
    return pthread_mutex_unlock(real) == 0 ? RT_EOK : RT_ERROR;
}

static s2_slot *s2_mtx_slot_of(struct rt_mutex *m)
{
    int i;
    s2_slot *out = RT_NULL;

    pthread_mutex_lock(&s2_mtx_tab);
    for (i = 0; i < s2_mtx_n; ++i)
        if (s2_mtx[i].key == m) { out = &s2_mtx[i]; break; }
    pthread_mutex_unlock(&s2_mtx_tab);
    return out;
}
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }

struct rt_mutex *s2_mtx_by_name(const char *name)
{
    int i;
    struct rt_mutex *out = RT_NULL;

    pthread_mutex_lock(&s2_mtx_tab);
    for (i = 0; i < s2_mtx_n; ++i)
        if (s2_mtx[i].name && strcmp(s2_mtx[i].name, name) == 0) { out = s2_mtx[i].key; break; }
    pthread_mutex_unlock(&s2_mtx_tab);
    return out;
}

void s2_set_lock_fail(const char *name) { s2_lock_fail_name = name; }
int  s2_lock_fail_count(void)           { return s2_lock_fail_hits; }
void s2_lock_fail_clear(void)           { s2_lock_fail_name = RT_NULL; s2_lock_fail_hits = 0; }

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag)
{ (void)flag; if (e) { e->name = name; e->inited = RT_TRUE; } return RT_EOK; }
int s2_event_send_fail = 0;                  /* 注入: event 发送一律失败 */
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event)
{
    (void)e;
    c_event_send++; c_event_bits |= event; c_event_last = event;
    if (s2_event_send_fail) return -RT_ERROR;      /* ISR 里"发送失败"那条路径 */
    return RT_EOK;
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
    /* 钩子点①: 真实危险写(时间基冻结)之前 —— 这个点**在屏蔽窗口之内**, 所以只允许挂
     * "请求一次中断"这种纯入队动作(C04), 不许挂业务/等待/日志(那会破坏模型本身)。 */
    s2_run_hook("rt_pwm_disable");
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

    /* 钩子点④: ENN 有界确认读 —— safety_run_selftest 的必需项真的经过这里(C15c 用)。
     * 选它而不是随便造一个"接缝": 自检在捕获 epoch0 之后、写 READY 之前必然做一次
     * 反馈确认, 这就是审查观察 old-selftest-ready-after-new-run 的真实交错位置。 */
    s2_run_hook("drv_feedback_confirm");

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
    /* 钩子点③: 前段完整性复核(UART 慢步骤, 必然在窗口外)。K2 的"预约前发生恢复"与
     * "旧自检比较完之后换代"都安排在这里。 */
    s2_run_hook("integrity_check");
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
/* CP4 §9 关键修正: 掩码与嵌套深度必须是**线程本地**的 —— 多线程共用一个全局深度会让
 * "谁持有全局独占锁"记账错乱, 表现为解锁不属于自己的互斥量并死锁(2026-10-24 实测)。
 * s2_win_live 仍是全局事实(只有持锁线程能改它, 且都在锁内读写)。 */
static __thread volatile int s_intr_masked;   /* 1 = 本线程当前处于屏蔽窗口 */
static __thread int          s_intr_depth;
static int          s_win_violations;   /* 窗口内禁止项命中次数 */

/* ---------- S2 的核心: 窗口 = 跨线程真独占 ----------
 * 目标上 rt_hw_interrupt_disable() 屏蔽 PRIMASK 之后, 单核既不会被别的线程抢占,
 * 也不会响应可屏蔽中断 => 窗口是原子的。主机上用一把**全局互斥量**复现同一件事:
 *   谁开了窗口, 别的线程(含夹具里的"另一条业务线程"和 ISR 交付线程)一律进不来;
 *   嵌套沿用"保存/恢复进入前值"的 PRIMASK 语义(线程本地深度)。
 * ⚠ 副作用: 生产代码若在窗口里取业务锁, 本模型会真的死锁 —— 这正是"窗口内不许
 *   取锁"这条纪律的执行方式, 由 runner 的墙钟 timeout 抓到(不是静默通过)。 */
static pthread_mutex_t s2_atomic = PTHREAD_MUTEX_INITIALIZER;
static int s2_win_live;                        /* 有线程正在窗口内(受 s2_atomic 保护) */

/* ---------- S2: 被屏蔽期间排队、窗口结束后交付的 ISR 模型 ----------
 * 目标事实: PRIMASK=1 时可屏蔽中断只能挂在中断线上, 退出临界区后立即响应。
 * 这里把 s2_isr_raise() 做成同一形状: 窗口开着 => 只入队(记一次"已请求未交付");
 * 最外层 rt_hw_interrupt_enable() => 由**同一个线程**同步交付, 交付期间
 * rt_interrupt_get_nest() 报告非 0(等价 ISR 上下文)。 */
#define S2_ISR_QUEUE 8
static void (*s2_isr_fn[S2_ISR_QUEUE])(void *);
static void *s2_isr_arg[S2_ISR_QUEUE];
static int s2_isr_head, s2_isr_tail, s2_isr_queued, s2_isr_delivered, s2_isr_seq;

static void s2_dispatch_pending(void)
{
    while (s2_isr_head != s2_isr_tail)
    {
        void (*fn)(void *) = s2_isr_fn[s2_isr_head];
        void *arg          = s2_isr_arg[s2_isr_head];

        s2_isr_fn[s2_isr_head] = RT_NULL;
        s2_isr_arg[s2_isr_head] = RT_NULL;
        s2_isr_head = (s2_isr_head + 1) % S2_ISR_QUEUE;
        s2_tls_nest++;
        s2_isr_seq++;
        fn(arg);
        s2_tls_nest--;
        s2_isr_delivered++;
    }
}

rt_base_t rt_hw_interrupt_disable(void)
{
    rt_base_t old = (rt_base_t)s_intr_depth;

    if (s_intr_depth == 0)
    {
        pthread_mutex_lock(&s2_atomic);
        s2_win_live = 1;
    }
    s_intr_depth++;
    s_intr_masked = 1;
    return old;
}

void rt_hw_interrupt_enable(rt_base_t level)
{
    s_intr_depth = (int)level;
    if (s_intr_depth == 0)
    {
        s_intr_masked = 0;
        s2_win_live = 0;
        pthread_mutex_unlock(&s2_atomic);
        /* 窗口一结束就响应挂着的中断(与目标上"退出临界区后立即进 ISR"同序)。
         * ⚠ 必须在解锁**之后**交付: 被交付的 ISR 自己会再开一个短临界区。 */
        s2_dispatch_pending();
    }
    else s_intr_masked = 1;
}

/* 夹具侧发起一次"中断请求"。窗口开着 => 只能排队; 否则当场交付。 */
void s2_isr_raise(void (*fn)(void *), void *arg)
{
    if (s2_win_live)
    {
        int next = (s2_isr_tail + 1) % S2_ISR_QUEUE;
        if (next == s2_isr_head) return;               /* 队列满: 如实丢弃(用例断言不会走到) */
        s2_isr_fn[s2_isr_tail]  = fn;
        s2_isr_arg[s2_isr_tail] = arg;
        s2_isr_tail = next;
        s2_isr_queued++;
        return;
    }
    s2_tls_nest++;
    s2_isr_seq++;
    fn(arg);
    s2_tls_nest--;
    s2_isr_delivered++;
}
int  s2_isr_queued_n(void)    { return s2_isr_queued; }
int  s2_isr_delivered_n(void) { return s2_isr_delivered; }
int  s2_isr_seq_n(void)       { return s2_isr_seq; }
void s2_isr_stats_clear(void)
{
    s2_isr_head = s2_isr_tail = s2_isr_queued = s2_isr_delivered = s2_isr_seq = 0;
    memset(s2_isr_fn, 0, sizeof(s2_isr_fn));
    memset(s2_isr_arg, 0, sizeof(s2_isr_arg));
}
int  s2_win_live_now(void)  { return s2_win_live; }

/* ---------- 接缝钩子与线程序列点(CP4 §9: "用显式屏障安排接缝") ----------
 * s2_set_hook(fn, where): 下一次命中钩子点时, 在**同一个线程**里执行 fn 一次。
 * 钩子点只放在**窗口外**的生产慢步骤上(许可回读 / 前段完整性复核 / 真实危险写之前),
 * 因为目标上屏蔽窗口里不允许等待/日志/IPC; 不在已屏蔽窗口里强行执行 ISR。
 * s2_seq_set/wait: 线程之间的先后顺序用显式标志点安排; 墙钟预算**只防死锁**,
 * 不作为任何时延结论(任务书 §9 原文)。超时一律返回 -1, 调用方按前置不成立处理。
 */
static void (*s2_hook_once)(void);
static int s2_hook_fires;
static const char *s2_hook_last;

void s2_set_hook(void (*fn)(void), const char *where)
{
    s2_hook_once = fn;
    s2_hook_last = where;
}
int  s2_hook_fires_count(void) { return s2_hook_fires; }
const char *s2_hook_where(void) { return s2_hook_last ? s2_hook_last : "(none)"; }

static void s2_run_hook(const char *where)
{
    if (s2_hook_once)
    {
        void (*h)(void) = s2_hook_once;

        s2_hook_once = RT_NULL;
        s2_hook_fires++;
        s2_hook_last = where;
        h();
    }
}

#define S2_SEQ_BITS 24   /* 其余序列/竞争观测点见端口层与"序列点"小节 */
static pthread_mutex_t s2_seq_m = PTHREAD_MUTEX_INITIALIZER;
static unsigned int s2_seq_flags;

void s2_seq_reset(void)
{
    pthread_mutex_lock(&s2_seq_m);
    s2_seq_flags = 0u;
    pthread_mutex_unlock(&s2_seq_m);
}
static unsigned int s2_seq_get_bit(int bit)
{
    unsigned int v;

    pthread_mutex_lock(&s2_seq_m);
    v = (s2_seq_flags >> bit) & 1u;
    pthread_mutex_unlock(&s2_seq_m);
    return v;
}
void s2_seq_set(int bit)
{
    pthread_mutex_lock(&s2_seq_m);
    s2_seq_flags |= (1u << bit);
    pthread_mutex_unlock(&s2_seq_m);
}
/* 0=等到了; -1=墙钟超时(调用方必须按"前置不成立"记 FAIL, 不许当通过) */
int  s2_seq_wait(int bit, int ms)
{
    int us = 0;

    while (us < ms * 1000)
    {
        if (s2_seq_get_bit(bit)) return 0;
        usleep(1000);
        us += 1000;
    }
    return s2_seq_get_bit(bit) ? 0 : -1;
}

/* ---------- 真线程 spawn(夹具侧的"另一条业务线程") ---------- */
static pthread_t s2_thr[6];
static int s2_thr_n;

int s2_spawn(void (*fn)(void *), void *arg)
{
    if (s2_thr_n >= (int)(sizeof(s2_thr) / sizeof(s2_thr[0]))) return -1;
    if (pthread_create(&s2_thr[s2_thr_n], RT_NULL, (void *(*)(void *))fn, arg) != 0)
        return -1;
    s2_thr_n++;
    return 0;
}
void s2_join_all(void)
{
    int i;

    for (i = 0; i < s2_thr_n; ++i) pthread_join(s2_thr[i], RT_NULL);
    s2_thr_n = 0;
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
rt_bool_t rt_interrupt_get_nest(void) { return (s2_tls_nest || s_isr_nest) ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void)
{
    /* 窗口内没有可等待的线程上下文; 交付 ISR 时同样按"中断上下文"处理。 */
    return (s_intr_masked || s2_tls_nest || s_isr_nest) ? RT_NULL : &c_thread_store[0];
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
    /* 钩子点②: 带回读的许可确认(窗口外的慢步骤)。arm/start/人工恢复都走这里。 */
    s2_run_hook("permit_confirm_read");
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


/* ===================================================================== */
/*  CP4 §9: S2 并发夹具 —— C01..C21 的真实线程 / 真互斥量 / 排队 ISR 用例    */
/* ===================================================================== */
/* 与被审方自述的边界(如实登记, 不越界声称):
 *   - 短临界区在主机上是**一把全局互斥量**, 复现"单核 + PRIMASK 屏蔽期内不可抢占";
 *     不模拟真实中断响应时延, 不模拟 NMI/HardFault, 也不模拟别的总线主设备。
 *   - 生产自己的斜坡/Safety 线程不由 rt_thread_startup 真起(与既有四链一致),
 *     由用例显式驱动 motor_ramp_tick / safety_reconcile_trip;
 *     "另一条业务线程"是夹具用 pthread **真起、真持有 ss_lock/mot_lock** 的线程 ——
 *     C01/C02/C14 的阻塞与 C04 的排队都是真阻塞/真排队, 不是单线程重入。
 *   - 墙钟超时(5s)只防死锁: 命中即记 PRE-FAIL/FAIL, 不作为时延结论, 也不算通过。
 *   - 真实寄存器/驱动叶子(drv_epwm.c 的 TBCTL 接缝、drv_uart.c 的
 *     ns800_uart_write_budgeted 等待循环、TMC cfg/xfer 叶锁与 RX 洪泛)不在本链,
 *     对应 C10/C16/C17/C18 —— 见 run_stop_priority_s2.sh 的 LEAF 段与交接登记。
 */
#include "stop_priority.c"
#include "step_pwm.c"
#include "motor.c"
#include "safety_state.c"

/* ---------------- 台账 ---------------- */
static int g_run, g_pass, g_fail, g_pre, g_skip;

static void report(const char *id, int ok, const char *detail)
{
    g_run++;
    if (ok) { g_pass++; printf("CASE|%s|PASS|%s\n", id, detail); }
    else    { g_fail++; printf("CASE|%s|FAIL|%s\n", id, detail); }
}
#define CHECK(id, cond, detail) report((id), (cond) ? 1 : 0, (detail))
/* 需要"成功输出前提"的 ID 在 target 变体里**不登记为用例**, 只声明跳过原因。 */
#define SKIPV(id, why) do { g_skip++; printf("SKIP|%s|%s\n", (id), (why)); } while (0)

static char g_buf[420];
/* 台账打印的格式/实参由编译器核对(2026-10-24 C05d 少一个实参直接段错误)。
     * 只在主机夹具上加, 产品代码不动。 */
__attribute__((format(printf, 1, 2)))
static const char *fm(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_buf, sizeof(g_buf), fmt, ap);
    va_end(ap);
    return g_buf;
}
static int pre_ok(const char *what, int cond)
{
    if (!cond) { g_pre++; printf("PRE-FAIL|%s\n", what); }
    return cond;
}
static int seq_or_fail(const char *what, int bit)
{
    int r = s2_seq_wait(bit, 5000);
    if (r != 0) { g_pre++; printf("SEQ-TIMEOUT|%s\n", what); }
    return r == 0;
}

/* ---------------- 公共前置 ---------------- */
#define BIT_HELD     0
#define BIT_SEEN     1
#define BIT_RELEASE  2
#define BIT_ERR      3

static int gates_open(void)
{
    return (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED &&
            TMC_DRIVE_CONFIG_HW_VALIDATED) ? 1 : 0;
}
static int diag_gates_open(void)
{
    return (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ||
            STEP_DIAG_ISOLATED_ENV_CONFIRMED) ? 1 : 0;
}

static void wire_and_boot(void)
{
    ps_sim_reset();
    ps_sim_add_pin(PIN_NAME_DRV_ENABLE, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIR,    PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIAG,   PIN_LOW);
    ps_sim_add_pin(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);
    ps_sim_dev_set_present(1);
    ps_sim_set_pad_stuck(0);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    ps_set_enable_write_rc(RT_EOK);
    ps_set_integrity(RT_EOK);
    ps_set_cfg_ok(RT_TRUE, TMC_DRV_CONFIGURED);
    ps_set_tmc_health(SUBSYS_OK);
    ps_set_protect(1, 1, 1, 1);
    ps_set_confirm_fail_after(-1);
    s2_seq_reset();
    s2_isr_stats_clear();
    s2_lock_fail_clear();
    s2_contend_clear();
    s2_event_send_fail = 0;
    s2_logs_in_isr = 0;
    s2_set_hook(RT_NULL, "reset");
    ps_isr_set(0);
    ps_win_violations_clear();
    (void)safety_state_boot();               /* 幂等 */
    (void)motor_init();                      /* 幂等: 已建线程直接 EOK */
    /* 生产静态量(sp 的 inhibit/锁存、SS 状态)跨用例存续: 先带回 READY 再跑本用例。 */
    if (safety_state_get() != SAFETY_READY)
    {
        sp_proof_t pf2;
        rt_memset(&pf2, 0, sizeof(pf2));
        if (!sp_epoch_exhausted())
        {
            (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf2);
            (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
            fault_reset();
        }
        (void)safety_startup_selftest();
    }
    (void)sp_bind_pwm((struct rt_device_pwm *)rt_device_find(EPWM_STEP_DEV_NAME), 0);
}

/* 一次干净的"故障 -> 整理 -> 人工恢复 -> 自检回 READY"(全走生产入口) */
static void trip_and_reset(void)
{
    sp_proof_t pf;
    rt_memset(&pf, 0, sizeof(pf));
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    fault_reset();
    (void)safety_startup_selftest();
}

static motor_snapshot_t snap_now(void)
{
    motor_snapshot_t s;
    rt_memset(&s, 0, sizeof(s));
    (void)motor_get_snapshot(&s);
    return s;
}

static void msh_pwm(const char *arg)
{
    char *av[2];
    av[0] = (char *)"pwm_test";
    av[1] = (char *)arg;
    pwm_test(2, av);
}

/* ===================================================================== */
/* C01 / C02: 另一线程**真持有** ss_lock / mot_lock                        */
/* ===================================================================== */
static const char *g_hold_name;
static int  g_hold_saw_disable, g_hold_saw_inhibit, g_hold_saw_pending;
static int  g_hold_saw_ss_state, g_hold_saw_mot_state;
static int  g_hold_contend, g_hold_done;

static void holder_thread(void *a)
{
    struct rt_mutex *m;

    (void)a;
    m = s2_mtx_by_name(g_hold_name);
    if (m == RT_NULL) { s2_seq_set(BIT_ERR); return; }
    (void)rt_mutex_take(m, RT_WAITING_FOREVER);          /* 真持有 */
    s2_seq_set(BIT_HELD);
    if (seq_or_fail("holder sees contender", S2_SEQ_CONTEND))
    {
        sp_snapshot_t s;

        sp_get(&s);
        g_hold_saw_disable   = ps_sim_pwm_disable_calls();
        g_hold_saw_inhibit   = s.inhibit ? 1 : 0;
        g_hold_saw_pending   = s.metadata_pending ? 1 : 0;
        g_hold_saw_ss_state  = (int)ss_state;
        g_hold_saw_mot_state = (int)mot.state;
        g_hold_contend       = s2_contend_hits;
    }
    s2_seq_set(BIT_SEEN);
    (void)rt_mutex_release(m);
    g_hold_done = 1;
}

static void run_contended(const char *name, int via_emergency)
{
    g_hold_name = name;
    g_hold_saw_disable = -1; g_hold_saw_inhibit = -1; g_hold_saw_pending = -1;
    g_hold_saw_ss_state = -1; g_hold_saw_mot_state = -1;
    g_hold_contend = 0; g_hold_done = 0;
    s2_seq_reset();
    s2_contend_clear();
    s2_spawn(holder_thread, RT_NULL);
    if (!seq_or_fail("holder holds", BIT_HELD)) { s2_join_all(); return; }
    if (via_emergency) (void)motor_emergency_stop();
    else               safety_force_shutdown(FAULT_SOFT);
    if (!seq_or_fail("holder observed", BIT_SEEN)) { s2_join_all(); return; }
    s2_join_all();
}

static void cases_lock(void)
{
    sp_snapshot_t s;
    int d0, bb0, tk0, hits;

    /* ---- C01: 另一线程真持 ss_lock; 快保护先完成, 慢整理真等锁 ---- */
    wire_and_boot();
    if (!pre_ok("C01 READY", safety_state_get() == SAFETY_READY)) return;
    d0 = ps_sim_pwm_disable_calls();
    bb0 = ps_bb_triggers();
    tk0 = ps_sim_mutex_takes();
    run_contended("ss", 0);
    sp_get(&s);
    CHECK("C01-fast-before-lock-then-wait",
          g_hold_saw_disable == d0 + 1 && g_hold_saw_inhibit == 1 &&
          g_hold_saw_pending == 1 && g_hold_contend >= 1 &&
          g_hold_saw_ss_state != (int)SAFETY_FAULT_LATCHED && g_hold_done == 1,
          fm("观察时: disable 已到 %d(=%d+1) inhibit=%d pending=%d 等锁命中=%d(%s)"
             " ss_state=%s(尚未落位)", g_hold_saw_disable, d0, g_hold_saw_inhibit,
             g_hold_saw_pending, g_hold_contend, s2_contend_name,
             safety_state_name((safety_state_t)g_hold_saw_ss_state)));
    CHECK("C01b-commits-after-release-no-second-close",
          s.metadata_pending == RT_FALSE && ps_bb_triggers() == bb0 + 1 &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          ps_sim_pwm_disable_calls() == d0 + 1 && ps_sim_mutex_takes() > tk0,
          fm("放锁后整理完成: pending=%s blackbox %d->%d disable %d->%d"
             " mutex_take 增量=%d", s.metadata_pending ? "YES" : "no", bb0,
             ps_bb_triggers(), d0, ps_sim_pwm_disable_calls(),
             ps_sim_mutex_takes() - tk0));

    /* ---- C01c: ss_lock 取用报错 => 不 ack / 不落位 / 不触发黑匣子(K1) ---- */
    wire_and_boot();
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    bb0 = ps_bb_triggers();
    s2_set_lock_fail("ss");
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    hits = s2_lock_fail_count();                 /* clear 会连计数一起清零, 先取走 */
    s2_lock_fail_clear();
    sp_get(&s);
    CHECK("C01c-ss-lock-error-no-ack",
          s.metadata_pending == RT_TRUE && s.inhibit == RT_TRUE &&
          ps_bb_triggers() == bb0 && hits >= 1,
          fm("报错 %d 次: pending=%s inhibit=%s blackbox %d->%d",
             hits, s.metadata_pending ? "YES" : "no",
             s.inhibit ? "YES" : "no", bb0, ps_bb_triggers()));

    /* ---- C02: 另一线程真持 mot_lock(显式急停的慢整理等锁) ---- */
    wire_and_boot();
    if (!pre_ok("C02 READY", safety_state_get() == SAFETY_READY)) return;
    d0 = ps_sim_pwm_disable_calls();
    bb0 = ps_bb_triggers();
    run_contended("mot", 1);
    /* 整理义务的唯一消费者是 Safety 侧的统一慢整理(§3.2): motor_emergency_stop 只做
     * 自己那一路的影子落位, **不** ack pending(否则同一条事实会被整理两次、黑匣子
     * 触发两次)。所以放锁之后再走一次消费者入口, 并断言 ack/黑匣子属于那一方。 */
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    sp_get(&s);
    CHECK("C02-mot-lock-held-then-commits",
          g_hold_saw_disable == d0 + 1 && g_hold_contend >= 1 &&
          g_hold_saw_mot_state != (int)MOTOR_FAULT && g_hold_done == 1 &&
          mot.state == MOTOR_FAULT &&
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          s.metadata_pending == RT_FALSE && ps_bb_triggers() == bb0 + 1,
          fm("观察: disable %d(=%d+1) 取锁记录=%d(%s) mot.state=%d(锁未放时未落位)"
             " 放锁后 state=%d; 消费者整理后: pending=%s blackbox %d->%d",
             g_hold_saw_disable, d0, g_hold_contend, s2_contend_name,
             g_hold_saw_mot_state, (int)mot.state,
             s.metadata_pending ? "YES" : "no", bb0, ps_bb_triggers()));

    /* ---- C02b: mot_lock 报错 => 不落位 / 不 ack / 不黑匣子 ---- */
    wire_and_boot();
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    bb0 = ps_bb_triggers();
    s2_set_lock_fail("mot");
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    hits = s2_lock_fail_count();                 /* 同上: 先取走再清零 */
    s2_lock_fail_clear();
    sp_get(&s);
    CHECK("C02b-mot-lock-error-no-place",
          s.metadata_pending == RT_TRUE && mot.state != MOTOR_FAULT &&
          ps_bb_triggers() == bb0 && hits >= 1,
          fm("报错 %d 次: pending=%s mot.state=%d blackbox %d->%d",
             hits, s.metadata_pending ? "YES" : "no",
             (int)mot.state, bb0, ps_bb_triggers()));
}

/* ===================================================================== */
/* C03: 首请求来自 ISR 上下文 / 事件未建 / 发送失败 —— 只降险, 不打日志      */
/* ===================================================================== */
static void cases_isr(void)
{
    rt_uint32_t ns = 0, sf = 0, ls = 0;
    sp_snapshot_t s;
    rt_err_t rc;
    int d0, low0;

    /* ---- C03a: 事件系统尚未建立(线程未起) + ISR 上下文的首条故障 ---- */
    ps_sim_reset();
    ps_sim_add_pin(PIN_NAME_DRV_ENABLE, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIR, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIAG, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);
    ps_sim_dev_set_present(0);                 /* 设备还没绑: 模拟"最早的一条请求" */
    s2_seq_reset();
    s2_isr_stats_clear();
    s2_lock_fail_clear();
    ps_win_violations_clear();
    s2_logs_in_isr = 0;
    low0 = ps_enable_write_calls(0);
    ps_isr_set(1);                             /* 整个调用都在中断上下文里 */
    rc = safety_post_event(EVT_ESTOP);
    ps_isr_set(0);
    sp_get(&s);
    safety_isr_event_stats(&ns, &sf, &ls);
    CHECK("C03a-isr-early-protected-quiet",
          rc == -RT_ERROR && ns >= 1 && s.inhibit == RT_TRUE &&
          s.metadata_pending == RT_TRUE &&
          ps_enable_write_calls(0) == low0 + 1 &&
          s.pwm.result == MOT_STOP_DEV_UNAVAILABLE && s.pwm.raw_rc == -RT_ENOSYS &&
          s2_logs_in_isr == 0 && ps_win_violations() == 0 && ls >= 1,
          fm("rc=%d no_system=%u log_suppressed=%u ISR 内打印=%d 窗口禁止项=%d"
             " LOW %d->%d pwm=%s(%d) inhibit=%s pending=%s", (int)rc, ns, ls,
             s2_logs_in_isr, ps_win_violations(), low0, ps_enable_write_calls(0),
             mot_stop_result_name(s.pwm.result), (int)s.pwm.raw_rc,
             s.inhibit ? "YES" : "no", s.metadata_pending ? "YES" : "no"));

    /* ---- C03b: 事件已建但发送失败, 仍在 ISR 上下文 => 计数不打印 ---- */
    wire_and_boot();
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    s2_event_send_fail = 1;
    ps_isr_set(1);
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_GUARD_STOP, RT_NULL);
    (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
    ps_isr_set(0);
    s2_event_send_fail = 0;
    safety_isr_event_stats(&ns, &sf, &ls);
    CHECK("C03b-isr-send-fail-counted",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          ps_enable_write_calls(0) == low0 + 1 && sf >= 1 &&
          s2_logs_in_isr == 0 && ps_win_violations() == 0 &&
          safety_trip_pending() == RT_TRUE,
          fm("disable %d->%d LOW %d->%d send_fail=%u ISR 打印=%d 禁止项=%d"
             " pending=%s", d0, ps_sim_pwm_disable_calls(), low0,
             ps_enable_write_calls(0), sf, s2_logs_in_isr, ps_win_violations(),
             safety_trip_pending() ? "YES" : "no"));

    /* ---- C03c: 线程上下文里同一失败必须仍可查(不是静默吞 rc) ---- */
    s2_event_send_fail = 1;
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
    s2_event_send_fail = 0;
    safety_isr_event_stats(&ns, &sf, &ls);
    CHECK("C03c-thread-context-observable",
          sf >= 1 && safety_trip_pending() == RT_TRUE,
          fm("send_fail 累计=%u pending=%s (原始 rc 由返回值交回)", sf,
             safety_trip_pending() ? "YES" : "no"));
}

/* ===================================================================== */
/* C04: 真实保护窗口内请求 ISR => 只能排队, 窗口结束后交付且真的动作        */
/* ===================================================================== */
static int c04_q_delta, c04_d_delta, c04_seq_at_probe;

static void isr_soft_request(void *a)
{
    (void)a;
    c04_seq_at_probe = s2_isr_seq_n();
    (void)safety_post_event(EVT_SOFT_FAULT);      /* ISR 里的第一条故障请求 */
}

static void raise_inside_window(void)
{
    int q0, dl0;
    q0  = s2_isr_queued_n();
    dl0 = s2_isr_delivered_n();
    s2_isr_raise(isr_soft_request, RT_NULL);
    c04_q_delta = s2_isr_queued_n() - q0;
    c04_d_delta = s2_isr_delivered_n() - dl0;
}

static void cases_isr_window(void)
{
    sp_snapshot_t s;
    rt_uint32_t e0;
    int d0, dl0, live_at_raise;

    wire_and_boot();
    trip_and_reset();
    e0 = sp_epoch();
    d0 = ps_sim_pwm_disable_calls();
    dl0 = s2_isr_delivered_n();
    c04_q_delta = -9; c04_d_delta = -9; live_at_raise = -9;
    s2_set_hook(raise_inside_window, "rt_pwm_disable");
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    live_at_raise = s2_win_live_now();
    sp_get(&s);
    CHECK("C04a-queued-only-inside-window",
          c04_q_delta == 1 && c04_d_delta == 0,
          fm("窗口内: 入队 %+d 交付 %+d(必须 0, 只能排队) queued=%d delivered=%d",
             c04_q_delta, c04_d_delta, s2_isr_queued_n(), s2_isr_delivered_n()));
    CHECK("C04b-delivered-after-window-and-acted",
          s2_isr_delivered_n() == dl0 + 1 &&
          ps_sim_pwm_disable_calls() == d0 + 2 &&
          s.epoch > e0 + 1u && s.inhibit == RT_TRUE &&
          s.metadata_pending == RT_TRUE && live_at_raise == 0 &&
          s2_win_live_now() == 0 && ps_win_violations() == 0,
          fm("交付后真的动作: disable %d->%d epoch %u->%u inhibit=%s pending=%s"
             " 交付序号=%d 窗口内禁止项=%d", d0, ps_sim_pwm_disable_calls(),
             (unsigned)e0, (unsigned)s.epoch, s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", s2_isr_seq_n(), ps_win_violations()));
}

/* ===================================================================== */
/* C05 / C06: 关断矩阵与 UNKNOWN 之后的新显式停止                          */
/* ===================================================================== */
static void cases_off_matrix(void)
{
    sp_snapshot_t s;
    sp_proof_t pf;
    rt_err_t e;
    int d0, low0;

    /* C05a: disable 失败 + LOW 成功 => 两个 raw 分列, PWM 错误优先, 不第二次关断 */
    wire_and_boot();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    rt_memset(&pf, 0, sizeof(pf));
    e = sp_off_attempt_for(sp_epoch(), SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT,
                           EVT_SOFT_FAULT, 1, &pf);
    sp_get(&s);
    CHECK("C05a-disable-fail-raw-separated",
          e == -RT_EIO && ps_sim_pwm_disable_calls() == d0 + 1 &&
          ps_enable_write_calls(0) == low0 + 1 &&
          s.pwm.result == MOT_STOP_API_FAILED && s.permit.write_rc == RT_EOK &&
          pf.epoch == s.pwm.epoch && pf.epoch == s.permit.write_epoch &&
          s.inhibit == RT_TRUE && s.metadata_pending == RT_TRUE,
          fm("rc=%d disable %d->%d LOW %d->%d pwm=%s permit_rc=%d 同代 %u/%u/%u",
             (int)e, d0, ps_sim_pwm_disable_calls(), low0,
             ps_enable_write_calls(0), mot_stop_result_name(s.pwm.result),
             (int)s.permit.write_rc, (unsigned)pf.epoch, (unsigned)s.pwm.epoch,
             (unsigned)s.permit.write_epoch));

    /* C05b: 未取得设备句柄 => DEV_UNAVAILABLE/未尝试, 不碰 NULL 句柄, LOW 仍独立尝试 */
    wire_and_boot();
    /* DEV_UNAVAILABLE 的真相是"协调层没绑到句柄", 不是端口里的 dev_present:
     * 用生产自己的绑定入口临时解绑(跑完立刻绑回), 不给产品开后门。 */
    (void)sp_bind_pwm(RT_NULL, 0);
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    rt_memset(&pf, 0, sizeof(pf));
    e = sp_off_attempt_for(sp_epoch(), SP_MOTOR, MOT_STOP_SRC_INIT, FAULT_SOFT,
                           EVT_SOFT_FAULT, 0, &pf);
    sp_get(&s);
    CHECK("C05b-no-device-low-still-tried",
          e == -RT_ENOSYS && ps_sim_pwm_disable_calls() == d0 &&
          ps_sim_pwm_null_calls() == 0 && ps_enable_write_calls(0) == low0 + 1 &&
          s.pwm.result == MOT_STOP_DEV_UNAVAILABLE &&
          s.motor_stop_latched == RT_TRUE && s.motor_output_active == RT_TRUE,
          fm("rc=%d disable %d->%d NULL 句柄=%d LOW %d->%d result=%s latch=%s",
             (int)e, d0, ps_sim_pwm_disable_calls(), ps_sim_pwm_null_calls(), low0,
             ps_enable_write_calls(0), mot_stop_result_name(s.pwm.result),
             s.motor_stop_latched ? "YES" : "no"));
    (void)sp_bind_pwm((struct rt_device_pwm *)rt_device_find(EPWM_STEP_DEV_NAME), 0);

}

/* C05c/C05d 需要"成功 arm"的前提 => 单独一组, 只在 host_ovr 变体真执行 */
static void cases_off_arm(void)
{
    sp_snapshot_t s;
    rt_err_t e;

    if (!gates_open())
    {
        SKIPV("C05c", "target 变体宏全 FALSE, arm 不可达(在 host_ovr 真执行)");
        SKIPV("C05d", "target 变体宏全 FALSE, arm 不可达(在 host_ovr 真执行)");
    }
    else
    {
        /* C05c: LOW 裸写失败而焊盘碰巧 LOW => 对外绝不回 EOK(K4 §6.2) */
        wire_and_boot();          /* 上一支留下抑制/锁存, 必须先带回 READY */
        if (pre_ok("C05c arm", motor_arm() == RT_EOK))
        {
            (void)motor_set_target_hz(500);
            /* 注入: 裸写请求返回 -RT_EIO, 但焊盘本来就是 LOW —— "碰巧为 LOW"不能
             * 被消费成成功(K4 §6.2)。 */
            ps_set_enable_write_rc(-RT_EIO);
            ps_sim_set_pad_level(PIN_LOW);
            e = motor_disarm();
            sp_get(&s);
            ps_set_enable_write_rc(RT_EOK);
            CHECK("C05c-low-write-error-not-eok",
                  e != RT_EOK && s.permit.write_rc == -RT_EIO &&
                  s.metadata_pending == RT_TRUE && s.inhibit == RT_TRUE &&
                  s.permit.write_epoch == s.pwm.epoch &&
                  s.pwm.epoch == s.epoch,
                  fm("disarm rc=%d permit.write_rc=%d pending=%s inhibit=%s"
                     " 同代 pwm=%u permit=%u 当前=%u", (int)e,
                     (int)s.permit.write_rc, s.metadata_pending ? "YES" : "no",
                     s.inhibit ? "YES" : "no", (unsigned)s.pwm.epoch,
                     (unsigned)s.permit.write_epoch, (unsigned)s.epoch));
        }
        /* C05d: 正常 disarm 的 LOW 确认真实提交得进去(K4 §6.1) */
        wire_and_boot();
        if (pre_ok("C05d arm", motor_arm() == RT_EOK))
        {
            (void)motor_set_target_hz(500);
            e = motor_disarm();
            sp_get(&s);
            CHECK("C05d-disarm-low-confirm-commits-same-gen",
                  e == RT_EOK && s.permit.written == RT_TRUE &&
                  s.permit.request_level == 0u && s.permit.confirmed == RT_TRUE &&
                  s.permit.confirm_rc == RT_EOK &&
                  s.permit.confirm_epoch == s.permit.write_epoch &&
                  s.permit.write_epoch == s.epoch &&
                  s.metadata_pending == RT_FALSE && s.inhibit == RT_FALSE,
                  fm("disarm rc=%d confirmed=%s confirm=%u write=%u 当前=%u"
                     " pending=%s inhibit=%s", (int)e,
                     s.permit.confirmed ? "YES" : "no",
                     (unsigned)s.permit.confirm_epoch,
                     (unsigned)s.permit.write_epoch, (unsigned)s.epoch,
                     s.metadata_pending ? "YES" : "no",
                     s.inhibit ? "YES" : "no"));
        }
    }
}

static void cases_unknown(void)
{
    sp_snapshot_t s;
    motor_snapshot_t m;
    int d0;

    wire_and_boot();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    (void)motor_emergency_stop();
    sp_get(&s);
    if (!pre_ok("C06 unknown", s.owner == SP_HELD_UNKNOWN &&
                s.motor_stop_latched == RT_TRUE)) return;
    d0 = ps_sim_pwm_disable_calls();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_emergency_stop();                 /* 新的显式停止: 必须真的再调一次 */
    sp_get(&s);
    m = snap_now();
    CHECK("C06-explicit-stop-retries-and-latch-persists",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          s.pwm.result == MOT_STOP_API_ACCEPTED &&
          s.motor_stop_latched == RT_TRUE && m.state == MOTOR_FAULT &&
          s.inhibit == RT_TRUE,
          fm("disable %d->%d 当前 result=%s 历史锁存=%s state=%d inhibit=%s",
             d0, ps_sim_pwm_disable_calls(), mot_stop_result_name(s.pwm.result),
             s.motor_stop_latched ? "YES" : "no", (int)m.state,
             s.inhibit ? "YES" : "no"));
}

/* ===================================================================== */
/* C07 / C08 / C09: 开启方向的身份与迟到动作                               */
/* ===================================================================== */
static void c07_recover_inside_command(void)
{
    sp_proof_t pf;
    rt_memset(&pf, 0, sizeof(pf));
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    fault_reset();
    (void)safety_startup_selftest();
}

static void c08_diag_grab(void)
{
    trip_and_reset();
    msh_pwm("1000");                         /* 恢复之后诊断真实起转 */
}

static void cases_identity(void)
{
    sp_snapshot_t s;
    sp_token_t oldtok, nt;
    sp_proof_t pf;
    rt_err_t e;
    int d0, high0;
    rt_uint32_t fresh, old_e;

    if (!gates_open())
    {
        SKIPV("C07", "target 变体宏全 FALSE, arm/HIGH 路径不可达");
        SKIPV("C08", "target 变体宏全 FALSE, arm/诊断不可达");
        SKIPV("C09", "target 变体宏全 FALSE, arm/disarm 不可达");
        return;
    }
    if (!pre_ok("C07 gates+diag gates", diag_gates_open())) return;

    /* ---- C07a: 抑制期间一次 HIGH 都不发; ---- */
    wire_and_boot();
    d0 = ps_sim_pwm_disable_calls();
    if (!pre_ok("C07 arm", motor_arm() == RT_EOK)) return;
    high0 = ps_enable_write_calls(1);          /* 基线 = 这次**成功** arm 之后的 HIGH 计数 */
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    e = motor_arm();
    sp_get(&s);
    CHECK("C07a-no-high-while-inhibited",
          e != RT_EOK && ps_enable_write_calls(1) == high0 &&
          s.inhibit == RT_TRUE && ps_sim_pwm_disable_calls() == d0 + 1,
          fm("arm rc=%d HIGH 写 %d->%d(不变) inhibit=%s disable=%d(只有那一次保护)",
             (int)e, high0, ps_enable_write_calls(1), s.inhibit ? "YES" : "no",
             ps_sim_pwm_disable_calls()));

    /* ---- C07b: arm 预约之前发生恢复 => 旧命令不得新使能 ---- */
    trip_and_reset();
    high0 = ps_enable_write_calls(1);
    d0 = ps_sim_pwm_disable_calls();
    s2_set_hook(c07_recover_inside_command, "integrity_check");
    e = motor_arm();
    sp_get(&s);
    CHECK("C07b-arm-refuses-post-recovery-reservation",
          e != RT_EOK && mot.armed == 0 &&
          ps_enable_write_calls(1) == high0 && pad_level == PIN_LOW &&
          s.owner != SP_MOTOR && s.inhibit == RT_FALSE,
          fm("arm rc=%d armed=%d HIGH %d->%d pad=%d owner=%s inhibit=%s epoch=%u"
             " 钩子=%s", (int)e, (int)mot.armed, high0, ps_enable_write_calls(1),
             (int)pad_level, sp_owner_name(s.owner), s.inhibit ? "YES" : "no",
             (unsigned)s.epoch, s2_hook_where()));

    /* ---- C08a: arm 的慢确认里发生"恢复 + 新诊断输出" => 旧回滚不停新诊断 ---- */
    wire_and_boot();
    s2_set_hook(c08_diag_grab, "permit_confirm_read");
    e = motor_arm();
    sp_get(&s);
    CHECK("C08a-old-rollback-cannot-cut-new-diag",
          e != RT_EOK && s.owner == SP_DIAG && s.phase == SP_RUNNING &&
          s.diag_output_active == RT_TRUE && ps_sim_pwm_running() == 1 &&
          s.inhibit == RT_FALSE,
          fm("arm rc=%d owner=%s phase=%s diag_active=%s 输出在跑=%d inhibit=%s"
             " epoch=%u", (int)e, sp_owner_name(s.owner), sp_phase_name(s.phase),
             s.diag_output_active ? "YES" : "no", ps_sim_pwm_running(),
             s.inhibit ? "YES" : "no", (unsigned)s.epoch));

    /* ---- C08b: 代次失配的确认不污染新记录 ---- */
    fresh = sp_epoch();
    e = sp_note_permit_confirmed_for(fresh + 777u, 0u, RT_EOK);
    sp_get(&s);
    CHECK("C08b-stale-confirm-rejected",
          e == -RT_EINTR && s.epoch == fresh,
          fm("rc=%d epoch 仍=%u stale_dropped=%u", (int)e, (unsigned)s.epoch,
             (unsigned)s.stale_dropped));

    /* ---- C09: 同 owner 正常释放后再预约; 旧 commit/release/确认/预约全拒 ---- */
    trip_and_reset();
    if (!pre_ok("C09 arm", motor_arm() == RT_EOK)) return;
    oldtok = mot_res;
    old_e  = oldtok.epoch;
    d0 = ps_sim_pwm_disable_calls();
    e = motor_disarm();
    sp_get(&s);
    fresh = s.epoch;
    CHECK("C09a-close-proof-permit-confirm-same-gen",
          e == RT_EOK && s.pwm.epoch == s.permit.write_epoch &&
          s.permit.write_epoch == fresh && s.permit.confirm_epoch == fresh &&
          s.permit.confirmed == RT_TRUE && s.metadata_pending == RT_FALSE &&
          s.inhibit == RT_FALSE,
          fm("disarm rc=%d pwm=%u permit=%u confirm=%u 当前=%u", (int)e,
             (unsigned)s.pwm.epoch, (unsigned)s.permit.write_epoch,
             (unsigned)s.permit.confirm_epoch, (unsigned)fresh));

    high0 = ps_sim_pwm_disable_calls();
    rt_memset(&pf, 0, sizeof(pf));
    {
        rt_err_t e1 = sp_off_attempt_for(old_e, SP_MOTOR, MOT_STOP_SRC_ARM_ROLLBACK,
                                         FAULT_SOFT, EVT_SOFT_FAULT, 1, &pf);
        rt_err_t e2 = sp_release(&oldtok);
        rt_err_t e3 = sp_note_permit_confirmed_for(old_e, 0u, RT_EOK);
        rt_err_t e4 = sp_reserve_for(old_e, SP_MOTOR, &nt);
        sp_get(&s);
        CHECK("C09b-late-actions-rejected-no-write",
              e1 == -RT_EINTR && e2 == -RT_EPERM && e3 == -RT_EINTR &&
              e4 == -RT_EINTR && ps_sim_pwm_disable_calls() == high0 &&
              s.epoch == fresh,
              fm("late off=%d release=%d confirm=%d reserve_for=%d disable %d->%d"
                 " epoch=%u", (int)e1, (int)e2, (int)e3, (int)e4, high0,
                 ps_sim_pwm_disable_calls(), (unsigned)s.epoch));
    }
    (void)sp_reserve(SP_MOTOR, &nt);
    CHECK("C09c-re-reserve-new-identity",
          nt.epoch != old_e && nt.epoch > fresh,
          fm("新 epoch=%u(旧=%u 释放后=%u)", (unsigned)nt.epoch,
             (unsigned)old_e, (unsigned)fresh));
    (void)sp_release(&nt);
    (void)d0;
}

/* ===================================================================== */
/* C11: 诊断 grant/prepare/commit/enable 失败出口与竞争                    */
/* ===================================================================== */
static void cases_diag(void)
{
    sp_snapshot_t s;
    int d0, en0;

    if (!diag_gates_open()) { SKIPV("C11", "target 变体无 STEP 输出许可"); return; }
    wire_and_boot();
    if (!pre_ok("C11 no motor arm needed", 1)) return;

    /* (a) prepare 失败: 预约归还, 不 enable, 不半截持有 */
    en0 = ps_sim_pwm_enable_calls();
    ps_sim_pwm_set_rc(-RT_EIO, RT_EOK, RT_EOK);
    msh_pwm("1000");
    sp_get(&s);
    CHECK("C11a-prepare-fail-releases-no-enable",
          s.owner == SP_NONE && ps_sim_pwm_enable_calls() == en0 &&
          step_pwm_output_active() == RT_FALSE && s.diag_output_active == RT_FALSE,
          fm("owner=%s enable %d->%d output_active=%s", sp_owner_name(s.owner),
             en0, ps_sim_pwm_enable_calls(),
             step_pwm_output_active() ? "YES" : "no"));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);

    /* (b) 诊断持有期间 Motor 被挡 */
    msh_pwm("1000");
    if (!pre_ok("C11b diag running", ps_sim_pwm_running() &&
                step_pwm_output_active())) return;
    {
        rt_err_t me = gates_open() ? motor_arm() : -RT_EPERM;
        CHECK("C11b-motor-blocked-while-diag-owns",
              me != RT_EOK && step_pwm_output_active() == RT_TRUE,
              fm("arm rc=%d diag output_active=%s", (int)me,
                 step_pwm_output_active() ? "YES" : "no"));
    }
    /* (c) 正常交回 */
    d0 = ps_sim_pwm_disable_calls();
    msh_pwm("stop");
    sp_get(&s);
    CHECK("C11c-diag-stop-releases",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          step_pwm_output_active() == RT_FALSE && s.owner == SP_NONE &&
          step_pwm_off_confirmed() == RT_TRUE &&
          s.diag_pwm.result == MOT_STOP_API_ACCEPTED,
          fm("disable %d->%d owner=%s off_confirmed=%s diag_pwm=%s",
             d0, ps_sim_pwm_disable_calls(), sp_owner_name(s.owner),
             step_pwm_off_confirmed() ? "YES" : "no",
             mot_stop_result_name(s.diag_pwm.result)));

    /* (d) 关断失败: 所有权不释放 + 恰好一次 disable + Motor 与新借用都被挡 */
    msh_pwm("1000");
    d0 = ps_sim_pwm_disable_calls();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    msh_pwm("stop");
    sp_get(&s);
    CHECK("C11d-diag-fail-keeps-owner-single-close",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          step_pwm_output_active() == RT_TRUE && s.diag_stop_latched == RT_TRUE &&
          s.inhibit == RT_TRUE && s.metadata_pending == RT_TRUE,
          fm("disable %d->%d active=%s latch=%s inhibit=%s pending=%s",
             d0, ps_sim_pwm_disable_calls(), step_pwm_output_active() ? "YES" : "no",
             s.diag_stop_latched ? "YES" : "no", s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no"));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
}

/* ===================================================================== */
/* C12 / C13 / C14: 人工恢复的换代、票据与两线程整体提交                     */
/* ===================================================================== */
static void c12_new_fault_during_reset(void)
{
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
}

static void fault_reset_thread(void *a)
{
    int *rc = (int *)a;
    fault_reset();
    *rc = 1;
}

static void latch_then_prepare(int low_fail)
{
    wire_and_boot();
    if (low_fail) ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    if (low_fail) ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    (void)pre_ok("prepared latch", safety_state_get() == SAFETY_FAULT_LATCHED);
}

static void cases_recovery(void)
{
    sp_snapshot_t s;
    rt_uint32_t e0;
    int d0, r0 = 0, r1 = 0;

    /* C12: 恢复复核之后落下新故障 => 换代 + 保持锁存, 旧票据不清新故障 */
    latch_then_prepare(0);
    e0 = sp_epoch();
    s2_set_hook(c12_new_fault_during_reset, "permit_confirm_read");
    fault_reset();
    sp_get(&s);
    CHECK("C12-new-fault-during-reset-keeps-latch",
          s.epoch > e0 && s.inhibit == RT_TRUE &&
          ss_state != SAFETY_MANUAL_CLEAR,
          fm("epoch %u->%u inhibit=%s pending=%s ss=%s first_code=%u",
             (unsigned)e0, (unsigned)s.epoch, s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", safety_state_name(ss_state),
             (unsigned)s.first_fault_code));

    /* C12b: 恢复复核中 LOW 裸写单独失败 => 换代并保持抑制(票据失效) */
    latch_then_prepare(0);
    e0 = sp_epoch();
    ps_set_enable_write_rc(-RT_EIO);
    ps_sim_set_pad_level(PIN_LOW);   /* 只让裸写失败; 电平/回读仍是 LOW 的事实 */
    fault_reset();
    ps_set_enable_write_rc(RT_EOK);
    sp_get(&s);
    CHECK("C12b-low-only-fail-advances",
          s.epoch != e0 && s.inhibit == RT_TRUE &&
          ss_state != SAFETY_MANUAL_CLEAR && s.permit.write_rc == -RT_EIO,
          fm("epoch %u->%u inhibit=%s pending=%s ss=%s permit.write_rc=%d",
             (unsigned)e0, (unsigned)s.epoch, s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", safety_state_name(ss_state),
             (int)s.permit.write_rc));

    /* C13: 历史锁存早于票据 => 真实复核可以清; 不永久卡死, 不自动开启 */
    latch_then_prepare(1);
    d0 = ps_sim_pwm_disable_calls();
    sp_get(&s);
    if (pre_ok("C13 latched first", s.motor_stop_latched == RT_TRUE))
    {
        fault_reset();
        sp_get(&s);
        CHECK("C13-latched-cleared-by-real-recheck",
              s.motor_stop_latched == RT_FALSE && s.inhibit == RT_FALSE &&
              ss_state == SAFETY_MANUAL_CLEAR &&
              ps_sim_pwm_disable_calls() == d0 + 1 &&
              mot.state == MOTOR_IDLE && mot.armed == 0 && pad_level == PIN_LOW,
              fm("latch=%s inhibit=%s ss=%s disable %d->%d state=%d armed=%d pad=%d",
                 s.motor_stop_latched ? "YES" : "no", s.inhibit ? "YES" : "no",
                 safety_state_name(ss_state), d0, ps_sim_pwm_disable_calls(),
                 (int)mot.state, (int)mot.armed, (int)pad_level));
    }

    /* C14: 两条真 reset 线程 + 最终窗口前后的新请求 => 整体提交可线性化 */
    latch_then_prepare(0);
    s2_spawn(fault_reset_thread, &r0);
    s2_spawn(fault_reset_thread, &r1);
    usleep(3000);                            /* 让两条线程进到复核/等待里 */
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_GUARD_STOP, RT_NULL);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    s2_join_all();
    sp_get(&s);
    CHECK("C14-two-reset-threads-new-fault-wins",
          r0 == 1 && r1 == 1 && s.inhibit == RT_TRUE &&
          ss_state != SAFETY_MANUAL_CLEAR,
          fm("两线程完成 r=%d/%d inhibit=%s ss=%s epoch=%u (旧 MANUAL_CLEAR 不覆盖"
             "新故障: 最终窗口按票据换代整笔放弃)", r0, r1,
             s.inhibit ? "YES" : "no", safety_state_name(ss_state),
             (unsigned)s.epoch));
}

/* ===================================================================== */
/* C15: pending 空分支 / 迟到身份 / 旧自检                                  */
/* ===================================================================== */
/* C15c 的对手线程: 自检走到自己的慢步骤(ENN 有界确认读)时被唤醒, 在**另一个线程**上
 * 完成"停机 + 人工恢复 + 新一轮 arm/start"。自检与它之间只有真实pthread 锁与协调层
 * 状态相交, 不是同线程重入(§9 明令禁止的形状)。 */
static volatile int c15_go, c15_done;
static int          c15_mid_ss;

static void c15_rival(void *arg)
{
    (void)arg;
    while (!c15_go) usleep(200);
    trip_and_reset();                      /* 停机 + 恢复 + 自检回 READY */
    (void)motor_arm();
    (void)motor_set_target_hz(500);        /* start 前先给目标, 否则"target_hz=0"被拒 */
    (void)motor_start();                   /* 新运行: SS=RUN, owner=MOTOR, 又换一代 */
    c15_mid_ss = (int)ss_state;
    c15_done = 1;
}

static void c15_new_cycle(void)
{
    c15_go = 1;
    while (!c15_done) usleep(200);         /* 只等对线程做完, 不在这里改任何状态 */
}

static void cases_stale(void)
{
    sp_snapshot_t s;
    safety_state_t ss_before;
    int rc0, st0, bb0;

    wire_and_boot();

    /* (a) 空 pending 的重复通知什么都不写(K1 删除的旁路) */
    trip_and_reset();                          /* 现在 SS=READY, 无 pending */
    ss_before = ss_state;
    rc0 = (int)ss_fault_code;
    st0 = (int)mot.state;
    bb0 = ps_bb_triggers();
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    CHECK("C15a-empty-pending-writes-nothing",
          ss_state == ss_before && (int)ss_fault_code == rc0 &&
          (int)mot.state == st0 && ps_bb_triggers() == bb0,
          fm("ss=%s code=%d state=%d blackbox %d->%d (全部不变)",
             safety_state_name(ss_state), (int)ss_fault_code, (int)mot.state,
             bb0, ps_bb_triggers()));

    if (!gates_open())
    {
        SKIPV("C15b", "target 变体宏全 FALSE, 旧身份/新 arm 不可达");
        SKIPV("C15c", "target 变体宏全 FALSE, 诊断新预约不可达");
        return;
    }
    /* (b) 旧身份的一次关断被拒, 一个寄存器写都不发 */
    wire_and_boot();
    if (pre_ok("C15b arm", motor_arm() == RT_EOK))
    {
        sp_token_t stale = mot_res;
        (void)motor_set_target_hz(1000);
        trip_and_reset();
        if (pre_ok("C15b new arm", motor_arm() == RT_EOK))
        {
            int d0 = ps_sim_pwm_disable_calls();
            rt_err_t e = sp_off_attempt_for(stale.epoch, SP_MOTOR,
                                            MOT_STOP_SRC_FAIL_CLOSED, FAULT_SOFT,
                                            EVT_SOFT_FAULT, 0, RT_NULL);
            sp_get(&s);
            CHECK("C15b-old-identity-rejected-no-write",
                  e == -RT_EINTR && ps_sim_pwm_disable_calls() == d0 &&
                  s.inhibit == RT_FALSE && ss_state == SAFETY_READY,
                  fm("rc=%d disable %d->%d inhibit=%s ss=%s", (int)e, d0,
                     ps_sim_pwm_disable_calls(), s.inhibit ? "YES" : "no",
                     safety_state_name(ss_state)));
        }
    }
    /* (c) 旧自检在换代后不得写 READY */
    wire_and_boot();
    if (pre_ok("C15c to SELF_TEST", safety_transition(SAFETY_SELF_TEST) == RT_EOK))
    {
        rt_err_t e;
        int fb0 = ps_fb_confirm_calls();

        c15_go = 0; c15_done = 0; c15_mid_ss = -1;
        s2_spawn(c15_rival, RT_NULL);
        s2_set_hook(c15_new_cycle, "drv_feedback_confirm");
        e = safety_startup_selftest();
        s2_join_all();
        sp_get(&s);
        /* 前置: 换代必须由对线程在自检的慢步骤里真实完成, 且它做完时 SS 已经不是
         * READY —— 否则"旧自检验不回 READY"这句话没有可观测的落点。 */
        if (!pre_ok("C15c seam+rival", ps_fb_confirm_calls() > fb0 &&
                    s2_hook_fires_count() >= 1 && c15_done == 1 &&
                    c15_mid_ss >= 0 && c15_mid_ss != (int)SAFETY_READY)) return;
        CHECK("C15c-stale-selftest-cannot-write-ready",
              e != RT_EOK && (int)ss_state == c15_mid_ss,
              fm("旧自检 rc=%d ss=%s(新运行后应为 %s) owner=%s epoch=%u"
                 " 接缝=%s 反馈读 %d->%d", (int)e, safety_state_name(ss_state),
                 safety_state_name((safety_state_t)c15_mid_ss),
                 sp_owner_name(s.owner), (unsigned)s.epoch,
                 s2_hook_where(), fb0, ps_fb_confirm_calls()));
    }
}

/* ===================================================================== */
/* C19: 计数边界                                                          */
/* ===================================================================== */
static void cases_boundary(void)
{
    sp_token_t tok;
    sp_proof_t pf;
    sp_snapshot_t s;
    rt_err_t e, off, rel;
    rt_uint32_t ticket;
    rt_bool_t rec;
    int d0, adv0;

    wire_and_boot();
    sp.epoch = SP_EPOCH_MAX - 2u;              /* 边界种子: 唯一用途是接近 UINT32_MAX */
    e = sp_reserve(SP_MOTOR, &tok);
    sp_get(&s);
    adv0 = (int)s.advance_failed;
    d0 = ps_sim_pwm_disable_calls();
    rt_memset(&pf, 0, sizeof(pf));
    off = sp_off_attempt_for(tok.epoch, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT,
                             EVT_SOFT_FAULT, 1, &pf);
    sp_get(&s);
    rel = sp_release(&tok);
    rec = sp_recovery_start(&ticket);
    sp_get(&s);
    CHECK("C19a-boundary-non-eok-keeps-inhibit",
          (e == RT_EOK || e == -RT_EPERM) && off != RT_EOK && rel != RT_EOK &&
          rec == RT_FALSE && s.inhibit == RT_TRUE && s.exhausted == RT_TRUE &&
          s.advance_failed > (rt_uint32_t)adv0 &&
          pf.epoch == s.pwm.epoch && pf.epoch == s.permit.write_epoch &&
          ps_sim_pwm_disable_calls() == d0 + 1,
          fm("reserve=%d off=%d release=%d recovery=%d epoch=%u exhausted=%s"
             " advance_failed %d->%u proof/pwm/permit=%u/%u/%u disable %d->%d",
             (int)e, (int)off, (int)rel, (int)rec, (unsigned)s.epoch,
             s.exhausted ? "YES" : "no", adv0, (unsigned)s.advance_failed,
             (unsigned)pf.epoch, (unsigned)s.pwm.epoch,
             (unsigned)s.permit.write_epoch, d0, ps_sim_pwm_disable_calls()));

    d0 = ps_sim_pwm_disable_calls();
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    sp_get(&s);
    CHECK("C19b-explicit-stop-still-closes-at-boundary",
          ps_sim_pwm_disable_calls() == d0 + 1 && s.trips > 0 &&
          s.exhausted == RT_TRUE && s.inhibit == RT_TRUE &&
          safety_state_get() == SAFETY_FAULT_LATCHED,
          fm("disable %d->%d trips=%u exhausted=%s inhibit=%s ss=%s",
             d0, ps_sim_pwm_disable_calls(), (unsigned)s.trips,
             s.exhausted ? "YES" : "no", s.inhibit ? "YES" : "no",
             safety_state_name(safety_state_get())));
}

/* ===================================================================== */
/* C20: 正常全周期 + 分 owner 视图一致                                     */
/* ===================================================================== */
static void ramp_until_zero(int n)
{
    int i;
    for (i = 0; i < n; ++i) motor_ramp_tick();
}

static void cases_healthy(void)
{
    sp_snapshot_t s;
    motor_snapshot_t m;
    mot_stop_result_t dres;
    rt_err_t drc;
    rt_bool_t dlat;

    if (!gates_open()) { SKIPV("C20", "target 变体宏全 FALSE, 正常开启周期不可达");
                         SKIPV("C20d", "target 变体宏全 FALSE"); return; }
    wire_and_boot();
    if (!pre_ok("C20 arm", motor_arm() == RT_EOK)) return;
    if (!pre_ok("C20 target", motor_set_target_hz(1000) == RT_EOK)) return;
    if (!pre_ok("C20 start", motor_start() == RT_EOK)) return;
    ramp_until_zero(6);
    m = snap_now();
    sp_get(&s);
    if (!pre_ok("C20 running", s.phase == SP_RUNNING && m.current_hz > 0))
    {
        CHECK("C20a-healthy-run", 0, fm("phase=%s hz=%u", sp_phase_name(s.phase),
                                        (unsigned)m.current_hz));
        return;
    }
    CHECK("C20a-healthy-run",
          s.phase == SP_RUNNING && m.armed == 1 && s.owner == SP_MOTOR &&
          s.permit.request_level == 1u && s.permit.confirmed == RT_TRUE &&
          s.permit.enn_ok == RT_TRUE && pad_level == PIN_HIGH &&
          s.metadata_pending == RT_FALSE,
          fm("phase=%s armed=%d owner=%s permit req=%u confirmed=%s enn=%s pad=%d",
             sp_phase_name(s.phase), (int)m.armed, sp_owner_name(s.owner),
             (unsigned)s.permit.request_level, s.permit.confirmed ? "YES" : "no",
             s.permit.enn_ok ? "YES" : "no", (int)pad_level));

    /* 受控减速到 0: 归零那一拍就把 IDLE + RUN->READY 一起提交(K2 §4.3) */
    (void)motor_stop();
    ramp_until_zero(80);
    m = snap_now();
    sp_get(&s);
    CHECK("C20b-ramp-zero-ready-keeps-armed",
          m.current_hz == 0 && m.state == MOTOR_IDLE &&
          safety_state_get() == SAFETY_READY && m.armed == 1 &&
          s.phase == SP_ARMED && s.inhibit == RT_FALSE,
          fm("hz=%u state=%d ss=%s armed=%d phase=%s inhibit=%s",
             (unsigned)m.current_hz, (int)m.state,
             safety_state_name(safety_state_get()), (int)m.armed,
             sp_phase_name(s.phase), s.inhibit ? "YES" : "no"));

    if (pre_ok("C20 re-target", motor_set_target_hz(900) == RT_EOK) &&
        pre_ok("C20 restart", motor_start() == RT_EOK))
    {
        motor_ramp_tick();
        CHECK("C20c-restart-after-ready",
              sp_get_phase() == SP_RUNNING && mot.state == MOTOR_ACCEL,
              fm("phase=%s state=%d", sp_phase_name(sp_get_phase()),
                 (int)mot.state));
        (void)motor_stop();
        ramp_until_zero(80);
    }
    if (pre_ok("C20 disarm", motor_disarm() == RT_EOK))
        CHECK("C20d-disarm-then-rearm", motor_arm() == RT_EOK && mot.armed == 1,
              fm("rearm rc ok armed=%d", (int)mot.armed));

    /* 诊断一轮之后再让 Motor 正常运行: 各 owner 视图一致(K6.1) */
    if (!diag_gates_open()) { SKIPV("C20e", "本变体无 STEP 输出许可"); return; }
    (void)motor_disarm();
    trip_and_reset();
    msh_pwm("1000");
    msh_pwm("stop");
    if (!pre_ok("C20 arm after diag", motor_arm() == RT_EOK)) return;
    (void)motor_set_target_hz(800);
    if (!pre_ok("C20 start after diag", motor_start() == RT_EOK)) return;
    motor_ramp_tick();
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    sp_get(&s);
    CHECK("C20e-owner-views-consistent",
          s.phase == SP_RUNNING && s.owner == SP_MOTOR &&
          s.diag_output_active == RT_FALSE && dres == MOT_STOP_API_ACCEPTED &&
          dlat == RT_FALSE && step_pwm_off_confirmed() == RT_TRUE &&
          sp_diag_off_confirmed() == RT_TRUE &&
          s.diag_pwm.result == MOT_STOP_API_ACCEPTED,
          fm("motor phase=%s owner=%s | diag active=%s result=%s latch=%s"
             " off_confirmed=%s diag_pwm=%s", sp_phase_name(s.phase),
             sp_owner_name(s.owner), s.diag_output_active ? "YES" : "no",
             mot_stop_result_name(dres), dlat ? "YES" : "no",
             step_pwm_off_confirmed() ? "YES" : "no",
             mot_stop_result_name(s.diag_pwm.result)));
    (void)motor_stop();
    ramp_until_zero(80);
    (void)motor_disarm();
}

/* 目标宏全 FALSE: 开启方向必须被拒, 关闭方向始终可用 */
static void cases_target_reject(void)
{
    sp_snapshot_t s;
    rt_err_t e;
    int d0;

    if (gates_open() || diag_gates_open())
    {
        SKIPV("C21", "host_ovr 变体不跑目标拒绝");
        return;
    }
    wire_and_boot();
    d0 = ps_sim_pwm_disable_calls();
    e = motor_arm();
    sp_get(&s);
    CHECK("C21a-target-false-refuses-open",
          e != RT_EOK && mot.armed == 0 && ps_enable_write_calls(1) == 0 &&
          ps_sim_pwm_enable_calls() == 0 && s.owner == SP_NONE,
          fm("arm rc=%d armed=%d HIGH 写=%d enable 调用=%d owner=%s",
             (int)e, (int)mot.armed, ps_enable_write_calls(1),
             ps_sim_pwm_enable_calls(), sp_owner_name(s.owner)));
    (void)motor_disarm();
    sp_get(&s);
    CHECK("C21b-close-always-available",
          ps_sim_pwm_disable_calls() >= d0 + 1 &&
          ps_sim_pwm_null_calls() == 0 && s.pwm.attempted == RT_TRUE,
          fm("disable %d->%d NULL 句柄=%d pwm.attempted=%s", d0,
             ps_sim_pwm_disable_calls(), ps_sim_pwm_null_calls(),
             s.pwm.attempted ? "YES" : "no"));
}

/* ===================================================================== */
/* main: 一个进程跑完全部适用 ID(与既有四链同一台账纪律)                    */
/* ===================================================================== */
struct s2_grp { const char *name; void (*fn)(void); };
static const struct s2_grp grps[] = {
    { "lock", cases_lock },           { "isr", cases_isr },
    { "isr_window", cases_isr_window }, { "off_matrix", cases_off_matrix },
    { "off_arm", cases_off_arm },     { "unknown", cases_unknown },
    { "identity", cases_identity },
    { "diag", cases_diag },            { "recovery", cases_recovery },
    { "stale", cases_stale },          { "boundary", cases_boundary },
    { "healthy", cases_healthy },      { "target_reject", cases_target_reject },
};

/* 每组**一个独立进程**: 生产静态量(sp 的 exhausted/inhibit、SS 状态、历史锁存)在同一
 * 进程内存续, C19 的边界种子会把之后的用例全堵住 —— 分组跑既是隔离也是台账要求。 */
int main(int argc, char **argv)
{
    size_t i, hit = (size_t)-1;

    if (argc != 2) { printf("USAGE|sp_conc <group>\n"); return 2; }
    for (i = 0; i < sizeof(grps)/sizeof(grps[0]); ++i)
        if (strcmp(grps[i].name, argv[1]) == 0) hit = i;
    if (hit == (size_t)-1) { printf("UNKNOWN_GROUP|%s\n", argv[1]); return 2; }
    printf("VARIANT|group=%s|gates=%d|diag_gates=%d\n", argv[1],
           gates_open(), diag_gates_open());
    grps[hit].fn();
    printf("SUMMARY|cases=%d|pass=%d|fail=%d|pre_bad=%d|skips=%d\n",
           g_run, g_pass, g_fail, g_pre, g_skip);
    fflush(stdout);
    return g_fail ? 1 : 0;
}
