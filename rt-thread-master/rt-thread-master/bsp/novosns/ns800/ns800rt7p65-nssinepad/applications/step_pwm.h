/*
 * step_pwm.h - STEP 输出控制对外接口
 */
#ifndef STEP_PWM_H
#define STEP_PWM_H

#include <rtthread.h>

/* 强制停止 STEP 输出(安全停机统一入口使用, 幂等, 任何状态可调) */
void step_pwm_force_stop(void);

/* Fix A: 诊断命令 pwm_test 当前是否占用 EPWM1 ch0。
 * TRUE 时 Motor Service 必须拒绝 start(避免与诊断输出双写同一通道)。 */
rt_bool_t step_pwm_output_active(void);

#endif /* STEP_PWM_H */
