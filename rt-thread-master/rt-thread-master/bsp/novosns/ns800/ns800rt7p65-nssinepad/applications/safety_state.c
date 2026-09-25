/*
 * safety_state.c - 安全状态机 (Phase 7-B 正式实现)
 *
 * 转换白名单(见 safety_state.h):
 *   BOOT→INIT→SELF_TEST→(READY | FAULT_LATCHED)
 *   READY↔RUN; RUN→LOAD_WARNING→(RUN | ABNORMAL); ABNORMAL→FAULT_LATCHED
 *   RUN/LOAD_WARNING/ABNORMAL/READY → FAULT_LATCHED / ESTOP(急停类事件)
 *   FAULT_LATCHED/ESTOP → MANUAL_CLEAR(仅 fault_reset: 实测源安全+人工命令)
 *   MANUAL_CLEAR → READY(内部重跑自检)
 *
 * ss_state 为私有; 业务代码一律走 safety_transition()。
 *
 * Fix A 启动编排: safety_state_boot() 只建事件系统/互斥并推进 BOOT→INIT→
 * SELF_TEST; 启动自检与最终 READY 决策由 supervisor_boot stage 11 调
 * safety_startup_selftest() 完成 —— Safety 线程必须先于慢速 Flash 扫描存在。
 *
 * Fix A 唯一停机路径: 硬件事件(ESTOP/LIMIT/DIAG)与软件故障(MULTI/SOFT)都只调
 * safety_force_shutdown(); 停 STEP 与 DRV_ENABLE "写+回读"由 motor.c 的
 * motor_emergency_stop() 负责, 本模块只做独立焊盘复核(不重复写)。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "safety_state.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "adxl345.h"
#include "current_adc.h"
#include "tmc2209.h"
#include "ns_flash.h"
#include "motor.h"
#include "step_pwm.h"
#include "blackbox.h"

/* ---------- 状态机私有状态 ---------- */
static volatile safety_state_t ss_state = SAFETY_BOOT;
static volatile rt_uint32_t    ss_fault_code = FAULT_NONE;
static struct rt_event         ss_event;
static rt_bool_t               ss_inited = RT_FALSE;
static rt_uint8_t              ss_degraded_mask = 0;   /* 自检降级位图 */

#define SS_DEG_IMU    (1u << 0)
#define SS_DEG_ADC    (1u << 1)
#define SS_DEG_FLASH  (1u << 2)

const char *safety_state_name(safety_state_t s)
{
    switch (s)
    {
    case SAFETY_BOOT:         return "BOOT";
    case SAFETY_INIT:         return "INIT";
    case SAFETY_SELF_TEST:    return "SELF_TEST";
    case SAFETY_READY:        return "READY";
    case SAFETY_RUN:          return "RUN";
    case SAFETY_LOAD_WARNING: return "LOAD_WARNING";
    case SAFETY_ABNORMAL:     return "ABNORMAL";
    case SAFETY_FAULT_LATCHED:return "FAULT_LATCHED";
    case SAFETY_ESTOP:        return "ESTOP";
    case SAFETY_MANUAL_CLEAR: return "MANUAL_CLEAR";
    default:                  return "?";
    }
}

/* P0-1: 状态修改串行化 —— transition/force_shutdown/fault_reset 全部
 * 经 ss_lock; FAULT_LATCHED/ESTOP 建立后普通业务 transition 无法覆盖
 * (白名单 + 锁内重读双重防护)。ISR 不持锁(仍只 post event)。 */
static struct rt_mutex         ss_lock;
static rt_bool_t               ss_lock_ok = RT_FALSE;

/* 转换白名单 */
static rt_bool_t transition_allowed(safety_state_t from, safety_state_t to)
{
    switch (from)
    {
    case SAFETY_BOOT:      return (to == SAFETY_INIT);
    case SAFETY_INIT:      return (to == SAFETY_SELF_TEST);
    case SAFETY_SELF_TEST: return (to == SAFETY_READY || to == SAFETY_FAULT_LATCHED);
    case SAFETY_READY:     return (to == SAFETY_RUN || to == SAFETY_SELF_TEST ||
                                   to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_RUN:       return (to == SAFETY_LOAD_WARNING || to == SAFETY_READY ||
                                   to == SAFETY_ABNORMAL ||
                                   to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_LOAD_WARNING:
                           return (to == SAFETY_RUN || to == SAFETY_ABNORMAL ||
                                   to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_ABNORMAL:  return (to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_FAULT_LATCHED:
                           return (to == SAFETY_MANUAL_CLEAR || to == SAFETY_ESTOP);
    case SAFETY_ESTOP:     return (to == SAFETY_MANUAL_CLEAR);
    case SAFETY_MANUAL_CLEAR:
                           return (to == SAFETY_READY);
    default:               return RT_FALSE;
    }
}

rt_err_t safety_transition(safety_state_t next)
{
    safety_state_t from;

    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    from = ss_state;                       /* P0-1: 锁内重读, 消除检查/写竞争 */

    if (from == next)
    {
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return RT_EOK;
    }

    if (!transition_allowed(from, next))
    {
        rt_kprintf("[SS] transition REFUSED: %s -> %s (not allowed)\n",
                   safety_state_name(from), safety_state_name(next));
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return -RT_EPERM;
    }

    ss_state = next;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] %s -> %s\n", safety_state_name(from), safety_state_name(next));
    return RT_EOK;
}

/* 统一安全停机(唯一旁路): 顺序不可变 */
void safety_force_shutdown(rt_uint32_t code)
{
    rt_err_t estop_e;
    rt_bool_t pad_low;

    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    ss_fault_code = code;

    /* ① 停 STEP + DRV_ENABLE 请求 LOW(写+回读, 由 Motor Service 统一持有;
     *    幂等)。返回值 = 使能脚写入是否经回读确认(Fix A: 不再吞掉)。 */
    estop_e = motor_emergency_stop();
    step_pwm_force_stop();

    /* ② 独立复核焊盘电平(不复写): 不知道=不安全 */
    pad_low = safety_drv_enable_is_low();
    if (!pad_low)
        rt_kprintf("[SS] !! CRITICAL: DRV_ENABLE pad NOT confirmed LOW (write_err=%d)"
                   " - hardware ENN chain is the only guard !!\n", (int)estop_e);

    /* ③ 黑匣子触发: O(1) 置标志, Safety 线程绝不等待 Flash */
    blackbox_trigger(code);

    /* ④ 锁存(旁路转换: 安全动作不受白名单限制)。
     * 即使 ② 复核失败也必须锁存 —— 回读失败绝不等于可以继续运动。 */
    ss_state = SAFETY_FAULT_LATCHED;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] ==> FAULT_LATCHED code=%u (STEP stopped, DRV_ENABLE LOW %s)\n",
               code, pad_low ? "verified" : "UNVERIFIED!!");
}

void safety_enter_fault(rt_uint32_t code) { safety_force_shutdown(code); }

rt_err_t safety_post_event(rt_uint32_t event)
{
    if (!ss_inited) return -RT_ERROR;
    return rt_event_send(&ss_event, event);
}

safety_state_t safety_state_get(void)
{
    safety_state_t s;
    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    s = ss_state;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    return s;
}

struct rt_event *safety_event_handle(void) { return &ss_event; }
rt_bool_t safety_events_ready(void) { return ss_inited; }
rt_uint32_t    safety_fault_get(void) { return ss_fault_code; }

/* ---------- 启动/重跑自检 ---------- */

/* required 全过 = RT_EOK; degraded 结果写入 ss_degraded_mask */
rt_err_t safety_run_selftest(void)
{
    rt_err_t required_fail = RT_EOK;
    rt_uint32_t v = 0;

    ss_degraded_mask = 0;
    rt_kprintf("[SS] self-test start\n");

    /* ---- required ① GPIO 安全态 ---- */
    if (!safety_gpio_ready())
    {
        rt_kprintf("[SS] [REQ FAIL] GPIO safety init\n");
        required_fail = -RT_ERROR;
    }
    else rt_kprintf("[SS] [REQ OK] GPIO\n");

    /* ---- required ② PWM 设备 ---- */
    if (motor_init() != RT_EOK)
    {
        rt_kprintf("[SS] [REQ FAIL] PWM/EPWM1 device\n");
        required_fail = -RT_ERROR;
    }
    else rt_kprintf("[SS] [REQ OK] PWM device\n");

    /* ---- required ③ DRV_ENABLE 必须"写 LOW + 回读确认"(Fix A) ----
     * 只读不够: 写不进/被顶高的焊盘读起来可能是 LOW 但不可控。回读只证明
     * MCU 焊盘电平, 不替代 ENN 整链硬件验收。 */
    {
        rt_err_t de = safety_drv_enable_write(0);
        if (de != RT_EOK)
        {
            rt_kprintf("[SS] [REQ FAIL] DRV_ENABLE LOW not verified (err=%d)\n",
                       (int)de);
            required_fail = -RT_ERROR;
        }
        else rt_kprintf("[SS] [REQ OK] DRV_ENABLE=LOW (write+readback verified)\n");
    }

    /* ---- required ④ TMC2209 链路(初始化 + IOIN 读) ---- */
    if (tmc2209_init() != RT_EOK ||
        tmc2209_read_register(TMC_ADDR_DEFAULT, TMC_REG_IOIN, &v) != RT_EOK)
    {
        rt_kprintf("[SS] [REQ FAIL] TMC2209 link\n");
        required_fail = -RT_ERROR;
    }
    else rt_kprintf("[SS] [REQ OK] TMC2209 (IOIN=0x%08X)\n", v);

    /* ---- degraded: IMU ---- */
    if (adxl345_init() != RT_EOK)
    {
        ss_degraded_mask |= SS_DEG_IMU;
        rt_kprintf("[SS] [DEGRADED] IMU missing/fault\n");
    }
    else rt_kprintf("[SS] [OK] IMU\n");

    /* ---- degraded: ADC ---- */
    if (current_adc_init() != RT_EOK)
    {
        ss_degraded_mask |= SS_DEG_ADC;
        rt_kprintf("[SS] [DEGRADED] ADC missing/fault\n");
    }
    else rt_kprintf("[SS] [OK] ADC\n");

    /* ---- degraded: Flash/存储(JEDEC 只读探测) ---- */
    {
        rt_uint8_t id[3] = {0}, sr[3] = {0};
        if (ns_flash_init() != RT_EOK || ns_flash_identify(id, sr) != RT_EOK ||
            id[0] != 0xEF)
        {
            ss_degraded_mask |= SS_DEG_FLASH;
            rt_kprintf("[SS] [DEGRADED] Flash/storage\n");
        }
        else rt_kprintf("[SS] [OK] Flash (JEDEC %02X %02X %02X)\n",
                        id[0], id[1], id[2]);
    }

    rt_kprintf("[SS] self-test done: required=%s degraded=0x%02X\n",
               required_fail == RT_EOK ? "PASS" : "FAIL", ss_degraded_mask);
    return required_fail;
}

/* ---------- MSH 命令 ---------- */

/* 显式故障清除: 仅 FAULT_LATCHED/ESTOP 且实测故障源安全 → MANUAL_CLEAR。
 * 之后必须 MANUAL_CLEAR→READY(重跑自检)→ 重新 arm。 */
static void fault_reset(void)
{
    struct { const char *name; const char *pin; int safe; } src[] = {
        { "ESTOP",     PIN_NAME_ESTOP,     0 },
        { "LIMIT_MIN", PIN_NAME_LIMIT_MIN, 0 },
        { "LIMIT_MAX", PIN_NAME_LIMIT_MAX, 0 },
        { "TMC_DIAG",  PIN_NAME_TMC_DIAG,  0 },
    };
    int i;
    rt_err_t e;
    rt_bool_t active = RT_FALSE;
    safety_state_t st;

    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    st = ss_state;
    if (st != SAFETY_FAULT_LATCHED && st != SAFETY_ESTOP)
    {
        rt_kprintf("[SS] fault_reset: not latched (state=%s)\n",
                   safety_state_name(st));
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }

    /* BUG-011-2: 实测故障源, 不看状态位猜 */
    for (i = 0; i < (int)(sizeof(src) / sizeof(src[0])); ++i)
    {
        rt_base_t pin = safety_pin(src[i].pin);
        int level;

        if (pin < 0)
        {
            rt_kprintf("[SS] %s pin unresolved - treat as ACTIVE\n", src[i].name);
            active = RT_TRUE;
            continue;
        }
        level = rt_pin_read(pin);
        rt_kprintf("[SS] %s level=%d (safe=%d) %s\n",
                   src[i].name, level, src[i].safe,
                   level == src[i].safe ? "safe" : "!!ACTIVE!!");
        if (level != src[i].safe) active = RT_TRUE;
    }
    /* Fix A: 安全关键输出无法确认 LOW 时一律拒绝清除 —— 清除之后就是重新
     * 自检→READY→arm 的路径, 使能脚不可控就不允许走通。 */
    if (!safety_drv_enable_is_low())
    {
        rt_kprintf("[SS] fault_reset REFUSED: DRV_ENABLE pad not confirmed LOW\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    if (active)
    {
        rt_kprintf("[SS] fault_reset REFUSED: source still active\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }

    /* O1: 接受故障清除的同时, 必须把 Motor Service 一起带回 IDLE。
     * 否则会出现 Safety=READY / Motor=MOTOR_FAULT 的不一致状态(真机
     * runtime_selftest 复现过)。走正式 disarm: 清 armed/target_hz/current_hz、
     * 停 STEP、写 DRV_ENABLE LOW 并回读。回读不确认就不进 MANUAL_CLEAR,
     * 也就同时关掉了 "重跑自检 -> READY -> 重新 arm" 这条路。
     * 锁序 ss_lock -> mot_lock 与 safety_force_shutdown() 现有路径一致。 */
    e = motor_disarm();
    if (e != RT_EOK)
    {
        rt_kprintf("[SS] fault_reset REFUSED: motor recovery disarm failed (%d)"
                   " - DRV_ENABLE LOW not re-verified, staying latched\n", (int)e);
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    rt_kprintf("[SS] motor recovered: state=IDLE, DRV_ENABLE LOW verified\n");

    ss_fault_code = FAULT_NONE;
    ss_state = SAFETY_MANUAL_CLEAR;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] ==> MANUAL_CLEAR (重跑自检进 READY 后仍需重新 arm)\n");
}
MSH_CMD_EXPORT(fault_reset, measure fault sources then clear latched fault);

void safety_fault_reset_manual(void) { fault_reset(); }

static void safety_state(void)
{
    rt_kprintf("[SS] state=%s fault_code=%u degraded=0x%02X\n",
               safety_state_name(ss_state), ss_fault_code, ss_degraded_mask);
}
MSH_CMD_EXPORT(safety_state, dump safety state machine state);

/* 软件级联调: 强制停机 → 锁死 → 实测源 → 清除(历史命令, 保留) */
static void ss_test(void)
{
    safety_force_shutdown(FAULT_SELF_TEST);
    safety_state();
    fault_reset();
    safety_state();
}
MSH_CMD_EXPORT(ss_test, exercise shutdown-latch-measure-clear chain);

/* ---------- Fix A: 启动分两段 ----------
 * stage 2: safety_state_boot()      事件系统 + 互斥 + BOOT→INIT→SELF_TEST
 *          (必须早于任何慢速 Flash/存储动作, 让 Safety 事件消费者尽早就位)
 * stage 11: safety_startup_selftest() required 自检 + 最终 READY 决策 */

rt_err_t safety_state_boot(void)
{
    if (ss_inited) return RT_EOK;           /* P1-8: 幂等 */

    if (rt_mutex_init(&ss_lock, "ss", RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ERROR;
    ss_lock_ok = RT_TRUE;

    if (rt_event_init(&ss_event, "ss_evt", RT_IPC_FLAG_PRIO) != RT_EOK)
    {
        rt_mutex_detach(&ss_lock);
        ss_lock_ok = RT_FALSE;
        return -RT_ERROR;
    }
    ss_inited = RT_TRUE;

    rt_kprintf("[SS] state machine inited (event+lock), BOOT->INIT->SELF_TEST\n");

    /* 转换失败(理论上不可能: 静态初值就是 BOOT)不撤销事件系统 ——
     * 状态机停在非 SELF_TEST 状态本身就是 fail closed, 但 Safety 线程
     * 仍需一个可用的事件消费者。 */
    if (safety_transition(SAFETY_INIT) != RT_EOK) return -RT_ERROR;
    if (safety_transition(SAFETY_SELF_TEST) != RT_EOK) return -RT_ERROR;
    return RT_EOK;
}

/* bootstrap stage 11 / system_selftest 共用: required 自检 + READY 决策 */
rt_err_t safety_startup_selftest(void)
{
    rt_err_t required = safety_run_selftest();

    if (required == RT_EOK)
    {
        if (safety_transition(SAFETY_READY) != RT_EOK)
        {
            rt_kprintf("[SS] !! SELF_TEST->READY refused (state=%s) - fail closed\n",
                       safety_state_name(safety_state_get()));
            return -RT_ERROR;
        }
    }
    else
    {
        safety_force_shutdown(FAULT_SELF_TEST);
    }

    safety_state();
    return required;
}
