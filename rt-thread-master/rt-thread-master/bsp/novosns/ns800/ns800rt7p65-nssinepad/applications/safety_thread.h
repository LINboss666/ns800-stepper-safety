/*
 * safety_thread.h - 安全线程与事件链 (Phase 7-B)
 *
 * 线程: 优先级 4(最高应用优先级), 消费 rt_event 事件并执行统一停机。
 * ISR 纪律: 回调内只允许 safety_post_event(), 禁止 SPI/UART/Flash/delay/复杂打印。
 *
 * IRQ 运行门(防悬空输入 interrupt storm):
 *   safety_irq_attach() 必须由人工命令显式调用(默认不注册任何 EXTI)。
 *   仅在对应输入已按 NC+GND 方案接线后使用。
 */
#ifndef SAFETY_THREAD_H
#define SAFETY_THREAD_H

#include <rtthread.h>
#include "app_health.h"

/* 创建 Safety Thread(幂等)。 */
rt_err_t safety_thread_init(void);

/* IRQ 运行门: 为 ESTOP_SENSE(PC.6/J4-40) 注册 EXTI6 上升沿中断
 * (NC 触点断开=触发)。⚠ 仅在输入已按 NC+GND 接线后调用;
 * 悬空输入注册中断可能造成 interrupt storm。 */
rt_err_t safety_irq_attach(void);

/* 门控状态查询(已注册=1) */
rt_bool_t safety_irq_attached(void);

subsys_health_t safety_thread_get_health(void);

#endif /* SAFETY_THREAD_H */
