/*
 * safety_state.c/h - 安全状态机骨架 (Phase 7, 交接文档 §18 / 方案 docx §12)
 *
 * 状态: BOOT → INIT → SELF_TEST → READY → RUN → WARN → ABNORMAL → FAULT_LATCHED → MANUAL_CLEAR
 * 原则:
 *   - 故障后禁止自动重启电机, 必须显式 fault_reset + 重新 arm
 *   - ESTOP/LIMIT/DIAG 事件由 Safety Thread(最高应用优先级)处理, ISR 只发事件
 *   - 本文件先建骨架: 状态枚举/事件置位/查询命令; 事件源接线后在 Phase 7/8 接入
 *
 * 黑匣子采样帧结构同步定义(供 Sensor/Diagnosis/Logger 共用),
 * 存储复用 ns_storage 事件分区, 方案 docx §13。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "safety_state.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "step_pwm.h"

/* ---------- 状态机 ---------- */
static volatile safety_state_t ss_state = SAFETY_BOOT;
static volatile rt_uint32_t    ss_fault_code = FAULT_NONE;
static struct rt_event         ss_event;
static rt_bool_t               ss_inited = RT_FALSE;

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

/* 事件置位: ISR 与线程均可调用 */
rt_err_t safety_post_event(rt_uint32_t event)
{
    if (!ss_inited) return -RT_ERROR;
    return rt_event_send(&ss_event, event);
}

/* BUG-011-1: 统一安全停机入口。任何故障路径都必须走这里,
 * 禁止"只改状态不停输出"——堵住忘关 STEP/使能的路径。
 * 步骤: ①停 STEP 输出 ②MCU_DRV_ENABLE=LOW ③锁存故障 ④打印 */
void safety_force_shutdown(rt_uint32_t code)
{
    ss_fault_code = code;

    /* ① 停 STEP(幂等, 设备未注册时内部跳过) */
    step_pwm_force_stop();

    /* ② 驱动使能拉低; 引脚解析失败时大告警(此时只能靠硬件 ENN 链兜底) */
    {
        rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);
        if (en >= 0)
            rt_pin_write(en, PIN_LOW);
        else
            rt_kprintf("[SS] !! DRV_ENABLE unresolved, hardware ENN chain"
                       " is the only guard !!\n");
    }

    /* ③④ 锁存 */
    ss_state = SAFETY_FAULT_LATCHED;
    rt_kprintf("[SS] ==> FAULT_LATCHED code=%u (STEP stopped, DRV_ENABLE=LOW)\n",
               code);
}

/* 兼容旧接口: 进故障 = 统一停机 */
void safety_enter_fault(rt_uint32_t code)
{
    safety_force_shutdown(code);
}

safety_state_t safety_state_get(void) { return ss_state; }
rt_uint32_t    safety_fault_get(void) { return ss_fault_code; }

/* BUG-011-2: 清故障前必须实测故障源, 不能只看状态位。
 * 安全电平定义: 接线时按扩展板实际电路冻结(ESTOP/限位常闭释放态)。
 * ⚠ 当前输入未接线, 浮空读数不可信——接线后第一件事确认下方 SAFE 电平! */
#define INPUT_SAFE_LEVEL_ESTOP      0
#define INPUT_SAFE_LEVEL_LIMIT_MIN  0
#define INPUT_SAFE_LEVEL_LIMIT_MAX  0
#define INPUT_SAFE_LEVEL_TMC_DIAG   0

static rt_bool_t fault_source_still_active(void)
{
    struct { const char *name; const char *pin; int safe; } src[] = {
        { "ESTOP",     PIN_NAME_ESTOP,     INPUT_SAFE_LEVEL_ESTOP },
        { "LIMIT_MIN", PIN_NAME_LIMIT_MIN, INPUT_SAFE_LEVEL_LIMIT_MIN },
        { "LIMIT_MAX", PIN_NAME_LIMIT_MAX, INPUT_SAFE_LEVEL_LIMIT_MAX },
        { "TMC_DIAG",  PIN_NAME_TMC_DIAG,  INPUT_SAFE_LEVEL_TMC_DIAG },
    };
    int i;
    rt_bool_t active = RT_FALSE;

    for (i = 0; i < (int)(sizeof(src) / sizeof(src[0])); ++i)
    {
        rt_base_t pin = safety_pin(src[i].pin);
        int level;

        if (pin < 0)
        {
            rt_kprintf("[SS] %s pin unresolved - treat as ACTIVE\n", src[i].name);
            active = RT_TRUE;           /* 不知道=按不安全处理 */
            continue;
        }
        level = rt_pin_read(pin);
        rt_kprintf("[SS] %s level=%d (safe=%d) %s\n",
                   src[i].name, level, src[i].safe,
                   level == src[i].safe ? "safe" : "!!ACTIVE!!");
        if (level != src[i].safe) active = RT_TRUE;
    }
    return active;
}

/* 显式故障清除: 仅在 FAULT_LATCHED 且实测故障源已消失时允许 */
static void fault_reset(void)
{
    if (ss_state != SAFETY_FAULT_LATCHED)
    {
        rt_kprintf("[SS] fault_reset: not latched (state=%s)\n",
                   safety_state_name(ss_state));
        return;
    }

    if (fault_source_still_active())
    {
        rt_kprintf("[SS] fault_reset REFUSED: source still active\n");
        return;
    }

    ss_fault_code = FAULT_NONE;
    ss_state = SAFETY_MANUAL_CLEAR;
    rt_kprintf("[SS] ==> MANUAL_CLEAR (人工确认机械恢复后可重新 arm)\n");
}
MSH_CMD_EXPORT(fault_reset, clear latched fault (measures sources first));

static void safety_state(void);

/* 骨架联调命令: 完整走一遍 统一停机→锁死→实测源→清除 链路 */
static void ss_test(void)
{
    safety_force_shutdown(FAULT_SELF_TEST);
    safety_state();
    fault_reset();
    safety_state();
}
MSH_CMD_EXPORT(ss_test, exercise shutdown-latch-measure-clear chain);

static void safety_state(void)
{
    rt_kprintf("[SS] state=%s fault_code=%u\n",
               safety_state_name(ss_state), ss_fault_code);
}
MSH_CMD_EXPORT(safety_state, dump safety state machine state);

static int safety_state_init(void)
{
    if (rt_event_init(&ss_event, "ss_evt", RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ERROR;
    ss_inited = RT_TRUE;
    ss_state = SAFETY_INIT;
    rt_kprintf("[SS] state machine inited (骨架版, 事件源待接线接入)\n");
    return RT_EOK;
}
INIT_APP_EXPORT(safety_state_init);
