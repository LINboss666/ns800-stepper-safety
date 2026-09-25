/*
 * motor.h - 步进电机运动服务正式 API (Phase 7-B, Fix A 门禁/回读加固)
 *
 * 硬件(冻结): STEP=PA0/EPWM1_A(channel 0), DIR=PA.2(J4-16),
 *             MCU_DRV_ENABLE 请求脚=PC.23(J4-21)。
 *
 * ⚠ 硬件使能链状态: MOTOR_HARDWARE_ENABLE_PATH_VALIDATED = RT_FALSE
 *   （ENN 链最终验收未完成：J4-21 万用表终验 + 扩展板使能链 PCB 漏画。
 *    因此 motor_arm() 一律拒绝 —— 这是刻意的失效安全门，不得绕过。
 *    解除条件：使能链真机验收 + 用户确认后，将下方宏置 RT_TRUE 并留证。）
 *
 * EPWM1 ch0 所有权: 正式 Motor Service 是唯一生产 owner。诊断命令 pwm_test
 * 必须经 motor_pwm_grant_to_diag() 借用门(见下)。
 *
 * pwm_test 仅为诊断命令（允许在 Motor 完全静止且使能脚实测 LOW 时手动探测
 * EPWM 波形），不是正式运动 API。
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

/* motor_arm() 门禁位定义(motor_arm_gate_mask()/motor_get_gate_fail_mask 用) */
#define MOT_GATE_ENABLE_PATH   (1u << 0)   /* 硬件使能链未验收 */
#define MOT_GATE_NOT_READY     (1u << 1)   /* 安全状态机不在 READY */
#define MOT_GATE_UNHEALTHY     (1u << 2)   /* 本子系统不健康/无 PWM 设备 */
#define MOT_GATE_NOT_PROTECTED (1u << 3)   /* Fix A: Safety 保护链未就绪 */

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
    rt_uint8_t  drv_en_request; /* 最近一次"写+回读确认成功"的电平(不是裸写请求) */
    subsys_health_t health;
} motor_snapshot_t;

/* 幂等初始化: 查找 epwm1、建立斜坡线程、状态 IDLE、
 * DRV_ENABLE 写 LOW 并回读确认(确认失败 → health=FAILED, 返回错误)。 */
rt_err_t motor_init(void);

/* 申请使能。门禁(四项全部满足才放行, Fix A):
 *   1. MOTOR_HARDWARE_ENABLE_PATH_VALIDATED == RT_TRUE   (硬件链验收)
 *   2. safety 状态机处于 READY                            (状态机放行)
 *   3. 本子系统健康且 PWM 设备可用                        (软件健康)
 *   4. safety_protection_ready() == TRUE                  (IRQ 门 OPEN +
 *      极性操作员 CONFIRM + 四路输入实测安全电平)
 * 四项全部无条件求值(均为无副作用只读), 拒绝原因以位掩码保留, 便于真机确认
 * 第 4 门确实参与判定。 */
rt_err_t motor_arm(void);

/* 解除使能: 停脉冲 + DRV_ENABLE 写 LOW 并回读确认 + 回 IDLE(不经 DECEL)。
 * 返回 DRV_ENABLE 写确认结果(-RT_EIO = 焊盘未确认 LOW)。 */
rt_err_t motor_disarm(void);

/* 方向: dir=0/1 (PA.2 电平)。
 * Fix A: 只允许在完全静止时设置 —— current_hz==0 且 state ∈ {IDLE, FAULT}。
 * 运动中(斜坡/巡航)一律拒绝 -RT_EBUSY: 开环步进运行中翻 DIR 等同注入丢步/堵转。
 * 静止 armed(state==IDLE 且已 arm)允许设置, 供"接线定方向后再生成"流程。 */
rt_err_t motor_set_direction(rt_uint8_t dir);

/* 设置目标步频(armed 后由斜坡趋近; 未 armed 仅记录)。0 等效减速停。 */
rt_err_t motor_set_target_hz(rt_uint32_t hz);

/* 开始运动: 需已 arm 且 target_hz>0, 且诊断 pwm_test 未占用 STEP 输出。 */
rt_err_t motor_start(void);

/* 受控停止: target=0, 线性减速到 0 后回 IDLE(仍保持 armed)。 */
rt_err_t motor_stop(void);

/* 紧急停机(唯一被 safety_force_shutdown 调用的停机入口):
 * 立即关 PWM 输出 + DRV_ENABLE 写 LOW 并回读确认 + state=FAULT。幂等。
 * 返回值 = DRV_ENABLE 回读确认结果; 非 RT_EOK 表示焊盘未确认 LOW,
 * 调用方必须记录 CRITICAL(状态机仍会锁存 FAULT_LATCHED)。 */
rt_err_t motor_emergency_stop(void);

/* 快照(线程安全, 内部互斥拷贝)。 */
rt_err_t motor_get_snapshot(motor_snapshot_t *snap);

subsys_health_t motor_get_health(void);

/* ---------- Fix A: 门禁可观测性 + EPWM 所有权借用门 ---------- */

/* 只读计算四门当前状态: 返回"失败门"位掩码(0 = 全通, 允许 arm)。
 * 无副作用, 供 runtime_selftest/MSH 证明第 4 门确实参与判定。 */
rt_uint32_t motor_get_gate_fail_mask(void);

/* 诊断命令(pwm_test)借用 EPWM1 ch0 的许可门: 全部满足才返回 RT_EOK 并打印
 * 具体拒绝原因 —— 未 armed、current_hz==0、target_hz==0、state==IDLE、
 * 且 DRV_ENABLE 焊盘实测 LOW。正式 Motor Service 才是 EPWM 的生产 owner。 */
rt_err_t motor_pwm_grant_to_diag(void);

#endif /* MOTOR_H */
