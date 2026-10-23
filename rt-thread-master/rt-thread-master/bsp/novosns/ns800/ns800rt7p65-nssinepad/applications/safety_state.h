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

/* 故障码
 * ⚠ 已用编号与数值是黑匣子持久化格式的一部分, **不得重排/复用**; 只允许追加。
 * 2026-10-21 追加 FAULT_DRV_FEEDBACK: ENN 状态反馈与驱动请求不一致(说停没停 /
 * 要动没让动 / 样本不可得)。它不代表"读到了急停按钮状态" —— 板上急停是纯硬件链,
 * 软件没有独立急停输入(见 safety_thread.h 文件头)。 */
#define FAULT_NONE          0u
#define FAULT_ESTOP         1u   /* 历史码: 保留给软件停机 API/黑匣子兼容, 现无 GPIO 生产者 */
#define FAULT_LIMIT_MIN     2u   /* 限位宏 ENABLED 时由 EXTI14 下降沿产生 */
#define FAULT_LIMIT_MAX     3u   /* 限位宏 ENABLED 时由 EXTI15 下降沿产生 */
#define FAULT_TMC_DIAG      4u
#define FAULT_MULTI_SOURCE  5u
#define FAULT_TMC_COMM      6u
#define FAULT_IMU_COMM      7u
#define FAULT_SELF_TEST     8u
#define FAULT_SOFT          9u   /* 软件/自检触发 */
#define FAULT_BOOT          10u  /* Fix A: supervisor_boot required stage 失败 */
#define FAULT_DRV_FEEDBACK  11u  /* 追加(2026-10-21): 驱动请求与 ENN 反馈不一致 */

/* 事件位(rt_event; ISR 内只允许 safety_post_event) */
#define EVT_ESTOP        (1u << 0)   /* 历史位: 保留, 当前没有 GPIO 生产者 */
#define EVT_LIMIT_MIN    (1u << 1)   /* 仅限位宏 ENABLED 时有生产者 */
#define EVT_LIMIT_MAX    (1u << 2)   /* 仅限位宏 ENABLED 时有生产者 */
#define EVT_TMC_DIAG     (1u << 3)
#define EVT_MULTI_FAULT  (1u << 4)
#define EVT_FAULT_CLEAR  (1u << 5)
#define EVT_SOFT_FAULT   (1u << 6)
#define EVT_DRV_FEEDBACK (1u << 7)   /* 追加(2026-10-21): 反馈不一致, 由 motor 守卫/决策点投递 */

/* 受保护转换: 白名单外一律拒绝并打印。返回 RT_EOK = 已切换。 */
rt_err_t safety_transition(safety_state_t next);

/* 统一安全停机(唯一旁路): 停 STEP → DRV_ENABLE 请求 LOW → 锁存 FAULT_LATCHED。
 * 任何故障路径必须走这里, 禁止业务代码只改状态不停输出。
 * 2026-10-23 S2 顺序变更(任务书 §8.1): **先**在取 ss_lock/打印之前做一次短保护
 * (两条独立降险动作 + 抑制 + 换 epoch + 两个原始结果都在同一个不依赖锁的窗口里),
 * **再**做线程慢整理。慢整理不再另关一次共享模块(那会形成同一波停机里的第二次
 * disable, 让第一次的失败被第二次的成功掩盖)。 */
void safety_force_shutdown(rt_uint32_t code);

/* 兼容别名 */
void safety_enter_fault(rt_uint32_t code);

/* 事件置位: ISR 与线程均可调用(rt_event_send 实现, ISR 安全)。 */
rt_err_t safety_post_event(rt_uint32_t event);

/* ---------- 2026-10-23 S2: "先保护, 再通知"的两条入口(任务书 §8.1) ----------
 * safety_post_event() 对**故障类**事件现在做两件事: ①在当前上下文同步执行一次
 * 短保护(不等任何锁、不打印、不碰 UART, 两条独立降险动作在同一窗口内完成),
 * ②再把事件交给 Safety 线程做慢整理。因此:
 *   - "投递成功"不再等于"已停机"(停机在投递方手里已经做过);
 *   - "投递失败"也不再能让停机漏做(动作先于投递)。
 * SAFETY_EVT_FAULT_MASK 显式列出故障类事件位; EVT_FAULT_CLEAR 不在其中
 * (人工清除继续不作为自动恢复入口)。 */
#define SAFETY_EVT_FAULT_MASK   (EVT_ESTOP | EVT_LIMIT_MIN | EVT_LIMIT_MAX | \
                                 EVT_TMC_DIAG | EVT_MULTI_FAULT | EVT_SOFT_FAULT | \
                                 EVT_DRV_FEEDBACK)

/* 只通知"同一操作里**已经**做过的短保护", 供 §4.3 的失败回退、守卫与 disarm 使用。
 * 它**绝不**再调一次关断: 消费者(Safety 线程)也不得把这条通知重新解释成一次新的
 * 保护请求 —— 这正是"消费事件→停机再失败→再投同事件"自激的禁止方式。
 * epoch 只用于日志/审计配对, 不参与判定。不导出 MSH 命令。 */
rt_err_t safety_notify_trip_done(rt_uint32_t event, rt_uint32_t epoch);

/* Safety 线程的**慢整理**入口(事件已经由投递方保护过): 只整理当前仍有效的 pending
 * 事实(状态锁存、回读复核、反馈采样、日志、黑匣子一次), 不重复关共享模块。 */
void safety_reconcile_trip(rt_uint32_t code, rt_uint32_t event);

/* 只读: 协调层是否还压着一次"已保护但未被整理"的事实(通知失败/没有 Safety 线程时
 * 状态命令必须能看见它, 不得打印"全部整理完成")。 */
rt_bool_t safety_trip_pending(void);

/* required 项检查(不改状态机): required 全过返回 RT_EOK。
 * degraded(IMU/ADC/Flash)失败只标记降级, 不阻塞。 */
rt_err_t safety_run_selftest(void);

/* Fix A: bootstrap stage 11 使用 —— 跑 required 自检并做最终 READY 决策:
 * 通过 → SELF_TEST→READY; 失败 → safety_force_shutdown(FAULT_SELF_TEST)。
 * 返回 RT_EOK 仅代表已进入 READY(状态机可能已被更高优先故障改写)。 */
rt_err_t safety_startup_selftest(void);

/* Fix A: bootstrap 显式调用(幂等), 只做事件系统 + 互斥 + BOOT→INIT→SELF_TEST。
 * 不再内联跑启动自检(那属于 stage 11, 见 safety_startup_selftest)。 */
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
