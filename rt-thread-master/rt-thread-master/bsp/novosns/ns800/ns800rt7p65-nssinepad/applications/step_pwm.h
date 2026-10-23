/*
 * step_pwm.h - STEP 输出控制对外接口
 *
 * 2026-10-23 S2: 诊断侧的实际关断、所有权与失败锁存的 canonical 记账统一在
 * stop_priority(共享 epwm1 时间基只有一处真相)。本模块只保留①借用门内的
 * prepare→门→commit/enable 流程, ②给 Motor Service 与状态命令用的语义视图,
 * ③统一停机的诊断侧**慢整理**(不再另关一次共享模块)。
 * 因此原 `step_pwm_force_stop()` 与 `step_pwm_clear_stop_latch()` 两个对外入口
 * 在本轮被替换: 前者会被 safety_force_shutdown()/fault_reset 重复关断, 后者会
 * 绕开人工恢复的最终提交 —— 两者的职责分别由下面两个函数承担。
 */
#ifndef STEP_PWM_H
#define STEP_PWM_H

#include <rtthread.h>
#include "stop_result.h"
#include "stop_priority.h"   /* 2026-10-23 S2 CP3: sp_proof_t(最终提交窗口内的镜像写) */

/* Fix A: 诊断命令 pwm_test 当前是否占用 EPWM1 ch0。
 * S1 判据(保守)在 S2 改为读 canonical: RESERVED/RUNNING/HELD_UNKNOWN/历史失败锁存
 * 任一成立都返回 TRUE, Motor Service 必须拒绝接管。 */
rt_bool_t step_pwm_output_active(void);

/* 只读语义视图(判定全在 canonical): 最近一次诊断侧关断结果与原始 rc;
 * *out_latched = 关断曾失败/未取得设备且未经人工恢复复核(一次后续成功**不**清该位)。 */
void step_pwm_get_stop_state(mot_stop_result_t *out_result, rt_err_t *out_rc,
                             rt_bool_t *out_latched);

/* 只读: 诊断通道是否已经有**有效**的关断结果(API 接受)。
 * 从未启用过诊断输出且从未尝试关断 → 返回 TRUE(该通道没被诊断占用过);
 * 尝试过但失败/未取得设备/仍带着未复核的失败锁存 → 返回 FALSE。 */
rt_bool_t step_pwm_off_confirmed(void);

/* 统一停机(safety_force_shutdown)与人工恢复的**最终提交窗口内**的诊断侧落位:
 * 共享模块的真实关断已由那一次短保护做过, 本函数**不产生任何硬件动作**、
 * **从不投递事件**(S1 返修 R03 的自激禁止继续成立), 只把 canonical 结果抄进镜像。
 * ⚠ 调用方必须已在同一个短临界区内(身份检查与镜像写同窗); 原无身份的
 *   step_pwm_reconcile_after_trip() 已删除 —— 它是"检查在内、作用在外"的旧形状。 */

/* 慢整理/恢复**最终提交窗口内**的诊断镜像写(调用方已持短临界区)。 */
void step_pwm_shadow_from_proof_held(const sp_proof_t *pf);

/* 人工恢复**最终提交窗口内**的诊断字段复位。
 * ⚠ 调用方必须已在同一个短临界区内(rt_hw_interrupt_disable 之后)且已确认恢复票据
 *   有效; 不取锁、不打印、不碰硬件。canonical 的锁存由 sp_finish_recovery_held()
 *   在同一窗口清除 —— 没有第二条恢复路径, 也不导出 MSH 命令。 */
void step_pwm_recovery_fields_held(void);

#endif /* STEP_PWM_H */
