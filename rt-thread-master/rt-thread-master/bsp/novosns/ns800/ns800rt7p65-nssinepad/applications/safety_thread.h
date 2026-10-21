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
 *     safety_irq_status   - 查看门/极性/活动输入 raw 电平
 *     safety_polarity_confirm CONFIRM  - 操作员声明极性已实测(软件不推断)
 *     safety_irq_enable   - 未 CONFIRM 一律拒绝; 通过后注册**活动**输入
 *     safety_irq_disable  - 逆序 disable+detach, 门回 CLOSED
 *   2026-10-21 输入职责更正(用户确认 PC.6 接扩展板 H6-5=TMC_ENN + RevA 图纸核对):
 *     活动保护输入集合由 SAFETY_LIMIT_INPUTS_ENABLED 决定 ——
 *       ENABLED: LIMIT_MIN_OK = PF.14/EXTI14、LIMIT_MAX_OK = PF.15/EXTI15
 *                (经 U12/U11 反相: **HIGH=安全、LOW=触发**, 注册**下降沿**),
 *                再加 TMC_DIAG = PA.3/EXTI3(原有语义, 不受限位宏影响)。
 *       DISABLED(目标默认): 两路限位**不解析、不配置、不读取、不注册**, 也不参与
 *                就绪与故障清除; 只有 TMC_DIAG 在活动集合里。
 *     PC.6(GPIO70/EXTI6)**不再是急停输入**: 它是 TMC_ENN 状态反馈的被动读脚,
 *     不注册 IRQ; 板上急停是纯硬件链(常闭→U13→U5→Q1), 图纸上没有送进 MCU 的网络,
 *     因此软件不存在独立急停输入。EVT_ESTOP / FAULT_ESTOP / SAFETY_ESTOP 与限位事件位、
 *     故障编号为黑匣子兼容原样保留(限位宏关闭时它们没有 GPIO 生产者)。
 *   极性仍为 HARDWARE-PENDING; attach 中途失败会逆序 disable+detach 已注册项,
 *   门保持 CLOSED, 可安全重复调用。
 */
#ifndef SAFETY_THREAD_H
#define SAFETY_THREAD_H

#include <rtthread.h>
#include "app_health.h"

/* 创建 Safety Thread(幂等)。 */
rt_err_t safety_thread_init(void);

/* IRQ 运行门: 为**活动保护输入集合**事务化注册 EXTI(限位=下降沿, DIAG=上升沿, 见文件头)。
 * ⚠ 仅在输入接线确认后调用; 默认门关闭。任一步失败 → 全部回滚 + 返回错误。
 * 幂等: 门已开时直接返回 RT_EOK。 */
rt_err_t safety_irq_attach(void);

/* 门控状态查询(已注册=1) */
rt_bool_t safety_irq_attached(void);

/* Fix A: 操作员极性声明状态(内存态; 不等同于硬件验收完成) */
rt_bool_t safety_polarity_validated(void);

/* P0-2 / A3: Safety 保护就绪(motor_arm 第 4 门禁):
 * IRQ gate OPEN + 极性操作员 CONFIRM + gpio ready + **活动输入集合**逐路处于各自安全电平
 * (限位 ENABLED 时 HIGH=安全; DIAG 沿用 LOW=安全)，再加上"静止侧条件": 当前没有已确认的
 * 使能请求(请求为 LOW 或从未确认)时, ENN 反馈必须在禁能侧一致。
 * ⚠ 已请求使能时不再要求反馈为 HIGH —— 否则 arm 之后正常的 ENN LOW 会让 start 永远失败;
 *   使能侧一致性由 motor.c 在 arm/start/运行守卫按请求态判定。
 * 两个前置标志默认均 FALSE(HARDWARE-PENDING), 只能人工按实测结果置位; 复位后不自动恢复。 */
rt_bool_t safety_protection_ready(void);

subsys_health_t safety_thread_get_health(void);

#endif /* SAFETY_THREAD_H */
