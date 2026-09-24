/*
 * motor.h - 步进电机运动服务正式 API (Phase 7-B)
 *
 * 硬件(冻结): STEP=PA0/EPWM1_A(channel 0), DIR=PA.2(J4-16),
 *             MCU_DRV_ENABLE 请求脚=PC.23(J4-21)。
 *
 * ⚠ 硬件使能链状态: MOTOR_HARDWARE_ENABLE_PATH_VALIDATED = RT_FALSE
 *   （ENN 链最终验收未完成：J4-21 万用表终验 + 扩展板使能链缺失(PCB 漏画)。
 *    因此 motor_arm() 一律拒绝 —— 这是刻意的失效安全门，不得绕过。
 *    解除条件：使能链真机验收 + 用户确认后，将下方宏置 RT_TRUE 并留证。）
 *
 * pwm_test 仅为诊断命令（允许在 IDLE 态手动探测 EPWM 波形），不是正式运动 API；
 * motor 非 IDLE 时 pwm_test 会拒绝执行。
 *
 * 斜坡: 线性 current_hz → target_hz，accel_hz_s/decel_hz_s 可调，10ms 节拍。
 */
#ifndef MOTOR_H
#define MOTOR_H

#include <rtthread.h>
#include "app_health.h"

/* ⚠ 硬件使能链验收开关(见上)。RT_FALSE = motor_arm 拒绝。 */
#ifndef MOTOR_HARDWARE_ENABLE_PATH_VALIDATED
#define MOTOR_HARDWARE_ENABLE_PATH_VALIDATED   RT_FALSE
#endif

typedef enum
{
    MOTOR_IDLE = 0,     /* 未 armed 或已减速到 0 */
    MOTOR_ACCEL,
    MOTOR_CRUISE,
    MOTOR_DECEL,
    MOTOR_FAULT,        /* 紧急停机后锁定 */
} motor_state_t;

typedef struct
{
    motor_state_t state;
    rt_uint32_t current_hz;     /* 当前输出步频(0=静止) */
    rt_uint32_t target_hz;      /* 目标步频 */
    rt_uint32_t accel_hz_s;     /* 加速斜率 Hz/s */
    rt_uint32_t decel_hz_s;     /* 减速斜率 Hz/s */
    rt_uint8_t  dir;            /* 0/1 */
    rt_uint8_t  armed;          /* 使能许可(软件层) */
    rt_uint8_t  drv_en_request; /* MCU_DRV_ENABLE 请求脚当前输出 */
    subsys_health_t health;
} motor_snapshot_t;

/* 幂等初始化: 查找 epwm1、建立斜坡线程、状态 IDLE、DRV_ENABLE 请求=LOW。 */
rt_err_t motor_init(void);

/* 申请使能。门禁(全部通过才允许):
 *   1. MOTOR_HARDWARE_ENABLE_PATH_VALIDATED == RT_TRUE
 *   2. safety 状态机处于 READY(由状态机自检放行)
 *   3. 当前无 FAULT */
rt_err_t motor_arm(void);

/* 解除使能: 停脉冲 + DRV_ENABLE 请求=LOW + 回 IDLE(不经 DECEL)。 */
rt_err_t motor_disarm(void);

/* 方向: dir=0/1 (PA.2 电平)。任何时候可调(armed 与否均可)。 */
rt_err_t motor_set_direction(rt_uint8_t dir);

/* 设置目标步频(armed 后由斜坡趋近; 未 armed 仅记录)。0 等效减速停。 */
rt_err_t motor_set_target_hz(rt_uint32_t hz);

/* 开始运动: 需已 arm 且 target_hz>0。 */
rt_err_t motor_start(void);

/* 受控停止: target=0, 线性减速到 0 后回 IDLE(仍保持 armed)。 */
rt_err_t motor_stop(void);

/* 紧急停机: 立即关 PWM 输出 + DRV_ENABLE 请求=LOW + state=FAULT。
 * 任何上下文可调(安全线程/MSH), 幂等。仅 fault_reset 流程可离开 FAULT。 */
rt_err_t motor_emergency_stop(void);

/* 快照(线程安全, 内部临界区拷贝)。 */
rt_err_t motor_get_snapshot(motor_snapshot_t *snap);

subsys_health_t motor_get_health(void);

#endif /* MOTOR_H */
