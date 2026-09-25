/*
 * safety_thread.h - 安全线程与事件链 (Phase 7-B/C, Fix A IRQ 事务化)
 *
 * 线程: 优先级 4(最高应用优先级), 消费 rt_event 事件并执行统一停机。
 * ISR 纪律: 回调内只允许 safety_post_event(), 禁止 SPI/UART/Flash/delay/复杂打印。
 *
 * 唯一停机路径(Fix A): 六类事件一律只调 safety_force_shutdown(); 本模块不
 * 直接调用 motor_emergency_stop() —— 停 STEP 与 DRV_ENABLE 写+回读由
 * safety_force_shutdown 统一负责。
 *
 * IRQ 运行门(防悬空输入 interrupt storm):
 *   默认不注册任何 EXTI。门只能由人工 MSH 打开:
 *     safety_irq_status   - 查看门/极性/四路 raw 电平
 *     safety_polarity_confirm CONFIRM  - 操作员声明极性已实测(软件不推断)
 *     safety_irq_enable   - 未 CONFIRM 一律拒绝; 通过后注册四路 EXTI
 *     safety_irq_disable  - 逆序 disable+detach, 门回 CLOSED
 *   一次注册四路输入(EXTI 线与冻结表一致, 无冲突):
 *     ESTOP_SENSE  = PC.6  → EXTI6  (EXTI 6)
 *     LIMIT_MIN    = PF.14 → EXTI14 (EXTI14)
 *     LIMIT_MAX    = PF.15 → EXTI15 (EXTI15)
 *     TMC_DIAG     = PA.3  → EXTI3  (EXTI 3)
 *   极性(NC 常闭/上升沿触发)仍为 HARDWARE-PENDING; attach 中途失败会逆序
 *   disable+detach 已注册项, 门保持 CLOSED, 可安全重复调用。
 */
#ifndef SAFETY_THREAD_H
#define SAFETY_THREAD_H

#include <rtthread.h>
#include "app_health.h"

/* 创建 Safety Thread(幂等)。 */
rt_err_t safety_thread_init(void);

/* IRQ 运行门: 为四路安全输入事务化注册 EXTI 上升沿中断(见文件头)。
 * ⚠ 仅在输入接线确认后调用; 默认门关闭。任一步失败 → 全部回滚 + 返回错误。
 * 幂等: 门已开时直接返回 RT_EOK。 */
rt_err_t safety_irq_attach(void);

/* 门控状态查询(已注册=1) */
rt_bool_t safety_irq_attached(void);

/* Fix A: 操作员极性声明状态(内存态; 不等同于硬件验收完成) */
rt_bool_t safety_polarity_validated(void);

/* P0-2 / A3: Safety 保护就绪(motor_arm 第 4 门禁):
 * IRQ gate OPEN + 极性操作员 CONFIRM + 四路输入可解析且当前全部处于安全电平。
 * 两个前置标志默认均 FALSE(HARDWARE-PENDING), 只能人工按实测结果置位。 */
rt_bool_t safety_protection_ready(void);

subsys_health_t safety_thread_get_health(void);

#endif /* SAFETY_THREAD_H */
