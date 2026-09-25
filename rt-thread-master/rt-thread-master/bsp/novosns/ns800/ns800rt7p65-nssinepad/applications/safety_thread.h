/*
 * safety_thread.h - 安全线程与事件链 (Phase 7-B/C, 审查修复 P0-3)
 *
 * 线程: 优先级 4(最高应用优先级), 消费 rt_event 事件并执行统一停机。
 * ISR 纪律: 回调内只允许 safety_post_event(), 禁止 SPI/UART/Flash/delay/复杂打印。
 *
 * IRQ 运行门(防悬空输入 interrupt storm):
 *   safety_irq_attach() 必须由人工命令显式调用(默认不注册任何 EXTI)。
 *   一次注册四路输入(EXTI 线与冻结表一致, 无冲突):
 *     ESTOP_SENSE  = PC.6  → EXTI6  (EXTI 6)
 *     LIMIT_MIN    = PF.14 → EXTI14 (EXTI14)
 *     LIMIT_MAX    = PF.15 → EXTI15 (EXTI15)
 *     TMC_DIAG     = PA.3  → EXTI3  (EXTI 3)
 *   极性(NC 常闭/上升沿触发)仍为 HARDWARE-PENDING;
 *   悬空输入注册中断可能造成 interrupt storm —— 接线确认后再开门。
 */
#ifndef SAFETY_THREAD_H
#define SAFETY_THREAD_H

#include <rtthread.h>
#include "app_health.h"

/* 创建 Safety Thread(幂等)。 */
rt_err_t safety_thread_init(void);

/* IRQ 运行门: 为四路安全输入注册 EXTI 上升沿中断(见文件头)。
 * ⚠ 仅在输入接线确认后调用; 默认门关闭。 */
rt_err_t safety_irq_attach(void);

/* 门控状态查询(已注册=1) */
rt_bool_t safety_irq_attached(void);

subsys_health_t safety_thread_get_health(void);

#endif /* SAFETY_THREAD_H */
