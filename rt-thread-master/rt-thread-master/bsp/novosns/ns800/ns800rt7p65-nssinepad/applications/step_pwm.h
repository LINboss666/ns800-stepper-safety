/*
 * step_pwm.h - STEP 输出控制对外接口
 */
#ifndef STEP_PWM_H
#define STEP_PWM_H

#include <rtthread.h>
#include "stop_result.h"

/* 强制停止 STEP 输出(安全停机统一入口使用, 任何状态可调)。
 * 2026-10-23 S1: 返回 rt_pwm_disable 的真实结果 —— 设备未取得返回 -RT_ENOSYS,
 * 关断失败返回驱动错误码并**保留诊断所有权**(不谎报已释放)。幂等, 不自动重试。
 * 返回 RT_EOK 仅表示驱动 API 接受了关断请求, 不是"示波器证明无脉冲"。 */
rt_err_t step_pwm_force_stop(void);

/* Fix A: 诊断命令 pwm_test 当前是否占用 EPWM1 ch0。
 * 2026-10-23 S1: 判据保守化 —— 输出仍启用 **或** 最近一次关断失败/未知 都返回 TRUE,
 * Motor Service 必须拒绝接管(不能把"未取得设备"或"关断没确认"当成通道已空闲)。 */
rt_bool_t step_pwm_output_active(void);

/* 只读: 诊断侧最近一次关断结果与原始 rc; *out_latched = 关断曾失败/未取得设备且未经
 * 人工恢复复核(一次后续成功**不**清该位)。 */
void step_pwm_get_stop_state(mot_stop_result_t *out_result, rt_err_t *out_rc,
                             rt_bool_t *out_latched);

/* 只读: 诊断通道是否已经有**有效**的关断结果(API 接受)。
 * 从未启用过诊断输出且从未尝试关断 → 返回 TRUE(该通道没被诊断占用过);
 * 尝试过但失败/未取得设备/仍带着未复核的失败锁存 → 返回 FALSE。 */
rt_bool_t step_pwm_off_confirmed(void);

/* ⚠ 解除诊断侧关断失败锁存。**唯一合法调用点 = safety_state.c 的 fault_reset()**,
 * 在全部人工恢复复核成立之后; 不导出 MSH 命令, 没有第二条恢复路径。 */
void step_pwm_clear_stop_latch(void);

#endif /* STEP_PWM_H */
