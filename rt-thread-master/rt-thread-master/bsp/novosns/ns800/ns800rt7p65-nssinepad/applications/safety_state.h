/*
 * safety_state.h - 安全状态机 (Phase 7-B 正式实现)
 *
 * 状态: BOOT → INIT → SELF_TEST → READY → RUN → LOAD_WARNING → ABNORMAL
 *       → FAULT_LATCHED / ESTOP → MANUAL_CLEAR → READY
 *
 * 纪律:
 *   - 业务代码禁止直接写状态, 全部经 safety_transition()(白名单转换表)
 *   - 唯一旁路: safety_force_shutdown()(安全停机动作, 任何业务状态可进 FAULT)
 *   - 故障后: STEP 停 + DRV_ENABLE 请求 LOW + 锁存, 禁止自动重启
 *   - fault_reset: 实测故障源安全 + 人工命令才允许 → MANUAL_CLEAR;
 *     MANUAL_CLEAR → READY 需重跑自检, 之后仍必须重新 arm
 *   - 启动自检: required(GPIO/PWM/DRV_ENABLE_LOW/TMC)全过才 READY;
 *     degraded(IMU/ADC/Flash)只降级不阻塞
 */
#ifndef SAFETY_STATE_H
#define SAFETY_STATE_H

#include <rtthread.h>
#include "app_health.h"

typedef enum
{
    SAFETY_BOOT = 0,
    SAFETY_INIT,
    SAFETY_SELF_TEST,
    SAFETY_READY,          /* 自检通过, 电机未 armed */
    SAFETY_RUN,            /* 已 armed 运行 */
    SAFETY_LOAD_WARNING,   /* 单源弱异常(Phase 7-C 接入判据) */
    SAFETY_ABNORMAL,       /* 多源一致异常 */
    SAFETY_FAULT_LATCHED,  /* 锁死, 禁止自动重启 */
    SAFETY_ESTOP,          /* 急停锁存 */
    SAFETY_MANUAL_CLEAR,   /* 人工确认清除, 需重自检+重新 arm */
} safety_state_t;

/* 故障码 */
#define FAULT_NONE          0u
#define FAULT_ESTOP         1u
#define FAULT_LIMIT_MIN     2u
#define FAULT_LIMIT_MAX     3u
#define FAULT_TMC_DIAG      4u
#define FAULT_MULTI_SOURCE  5u
#define FAULT_TMC_COMM      6u
#define FAULT_IMU_COMM      7u
#define FAULT_SELF_TEST     8u
#define FAULT_SOFT          9u   /* 软件/自检触发 */

/* 事件位(rt_event; ISR 内只允许 safety_post_event) */
#define EVT_ESTOP        (1u << 0)
#define EVT_LIMIT_MIN    (1u << 1)
#define EVT_LIMIT_MAX    (1u << 2)
#define EVT_TMC_DIAG     (1u << 3)
#define EVT_MULTI_FAULT  (1u << 4)
#define EVT_FAULT_CLEAR  (1u << 5)
#define EVT_SOFT_FAULT   (1u << 6)

/* 受保护转换: 白名单外一律拒绝并打印。返回 RT_EOK = 已切换。 */
rt_err_t safety_transition(safety_state_t next);

/* 统一安全停机(唯一旁路): 停 STEP → DRV_ENABLE 请求 LOW → 锁存 FAULT_LATCHED。
 * 任何故障路径必须走这里, 禁止业务代码只改状态不停输出。 */
void safety_force_shutdown(rt_uint32_t code);

/* 兼容别名 */
void safety_enter_fault(rt_uint32_t code);

/* 事件置位: ISR 与线程均可调用(rt_event_send 实现, ISR 安全)。 */
rt_err_t safety_post_event(rt_uint32_t event);

/* 启动/重跑自检: required 全过 → READY; required 失败 → FAULT_LATCHED。
 * degraded(IMU/ADC/Flash)失败只标记降级。返回 RT_EOK = 进入 READY。 */
rt_err_t safety_run_selftest(void);

/* P1-8: bootstrap 显式调用(幂等): 事件系统 + BOOT->INIT + 启动自检 */
rt_err_t safety_state_boot(void);

/* 人工故障清除(实测故障源安全才放行; 供 MSH 与 runtime_selftest) */
void safety_fault_reset_manual(void);

const char    *safety_state_name(safety_state_t s);
safety_state_t safety_state_get(void);

/* 事件句柄(Safety Thread 的 rt_event_recv 用; 置位统一走 safety_post_event) */
struct rt_event *safety_event_handle(void);
rt_bool_t safety_events_ready(void);   /* 事件系统已初始化(Safety Thread 启动门) */
rt_uint32_t    safety_fault_get(void);

#endif /* SAFETY_STATE_H */
