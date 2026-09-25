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
    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    ss_fault_code = code;

    /* ① 停 STEP(幂等; 含 pwm disable) */
    motor_emergency_stop();
    step_pwm_force_stop();

    /* ② DRV_ENABLE 请求 LOW; 引脚解析失败时大告警(只剩硬件 ENN 链兜底) */
    {
        rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);
        if (en >= 0)
            rt_pin_write(en, PIN_LOW);
        else
            rt_kprintf("[SS] !! DRV_ENABLE unresolved, hardware ENN chain"
                       " is the only guard !!\n");
    }

    /* ③ 黑匣子触发: O(1) 置标志, Safety 线程绝不等待 Flash */
    blackbox_trigger(code);

    /* ④ 锁存(旁路转换: 安全动作不受白名单限制) */
    ss_state = SAFETY_FAULT_LATCHED;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] ==> FAULT_LATCHED code=%u (STEP stopped, DRV_ENABLE=LOW)\n",
               code);
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

    /* ---- required ③ DRV_ENABLE 请求脚必须实测 LOW ---- */
    {
        rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);
        if (en < 0 || rt_pin_read(en) != PIN_LOW)
        {
            rt_kprintf("[SS] [REQ FAIL] DRV_ENABLE not LOW (read=%d)\n",
                       (en >= 0) ? rt_pin_read(en) : -1);
            required_fail = -RT_ERROR;
        }
        else rt_kprintf("[SS] [REQ OK] DRV_ENABLE=LOW\n");
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
    if (active)
    {
        rt_kprintf("[SS] fault_reset REFUSED: source still active\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }

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

/* ---------- 初始化: BOOT → INIT → 自检(自动启动) → READY/FAULT ---------- */

rt_err_t safety_state_boot(void)
{
    if (ss_inited) return RT_EOK;           /* P1-8: 幂等 */

    if (rt_event_init(&ss_event, "ss_evt", RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ERROR;
    if (rt_mutex_init(&ss_lock, "ss", RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ERROR;
    ss_lock_ok = RT_TRUE;
    ss_inited = RT_TRUE;

    ss_state = SAFETY_INIT;
    rt_kprintf("[SS] state machine inited, running startup self-test\n");

    if (safety_transition(SAFETY_SELF_TEST) != RT_EOK) return -RT_ERROR;

    if (safety_run_selftest() == RT_EOK)
        safety_transition(SAFETY_READY);
    else
        safety_force_shutdown(FAULT_SELF_TEST);

    safety_state();
    return RT_EOK;
}
