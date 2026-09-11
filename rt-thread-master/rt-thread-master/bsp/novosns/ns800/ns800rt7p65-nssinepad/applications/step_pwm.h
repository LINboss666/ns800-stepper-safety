/*
 * step_pwm.h - STEP 输出控制对外接口
 */
#ifndef STEP_PWM_H
#define STEP_PWM_H

#include <rtthread.h>

/* 强制停止 STEP 输出(安全停机统一入口使用, 幂等, 任何状态可调) */
void step_pwm_force_stop(void);

#endif /* STEP_PWM_H */
