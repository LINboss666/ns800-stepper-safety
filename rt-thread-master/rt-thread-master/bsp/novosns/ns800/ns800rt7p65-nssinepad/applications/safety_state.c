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
#include "safety_state.h"
#include "project_board.h"
#include "safety_gpio.h"

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

void safety_enter_fault(rt_uint32_t code)
{
    ss_fault_code = code;
    /* 停 STEP + 驱动禁止由调用方(Motor/Safety Thread)执行; 这里锁状态 */
    ss_state = SAFETY_FAULT_LATCHED;
    rt_kprintf("[SS] ==> FAULT_LATCHED code=%u\n", code);
}

safety_state_t safety_state_get(void) { return ss_state; }
rt_uint32_t    safety_fault_get(void) { return ss_fault_code; }

/* 显式故障清除: 仅在 FAULT_LATCHED 且故障源已消失时允许 */
static void fault_reset(void)
{
    if (ss_state != SAFETY_FAULT_LATCHED)
    {
        rt_kprintf("[SS] fault_reset: not latched (state=%s)\n",
                   safety_state_name(ss_state));
        return;
    }
    ss_fault_code = FAULT_NONE;
    ss_state = SAFETY_MANUAL_CLEAR;
    rt_kprintf("[SS] ==> MANUAL_CLEAR (人工确认机械恢复后可重新 arm)\n");
}
MSH_CMD_EXPORT(fault_reset, clear latched fault (manual confirm required));

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
