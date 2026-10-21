/*
 * motor.h - 步进电机运动服务正式 API (Phase 7-B, Fix A 门禁/回读加固)
 *
 * 硬件(冻结): STEP=PA0/EPWM1_A(channel 0), DIR=PA.2(J4-16),
 *             MCU_DRV_ENABLE 请求脚=PC.23(J4-21)。
 *
 * ⚠ 硬件使能链状态: MOTOR_HARDWARE_ENABLE_PATH_VALIDATED = RT_FALSE
 *   （事实更正 2026-10-18: MCU_DRV_ENABLE 节点在新扩展板上**已引出**; 当前阻塞项是
 *    级联的 U5 与门损坏 + 急停非门损坏, TMC_ENN 实测约 0V。因此"MCU 焊盘写 LOW 并
 *    回读成功"只能证明 MCU 侧请求, 证明不了 TMC_ENN 已禁能 —— 任何依赖"焊盘 LOW =
 *    驱动已禁能"的许可都不成立, 包括 STEP 诊断借用门。)
 *   因此 motor_arm() 一律拒绝 —— 这是刻意的失效安全门，不得绕过。
 *   解除条件：使能链真机验收（含 ENN 端实测）+ 用户确认后，将下方宏置 RT_TRUE 并留证。
 *
 * EPWM1 ch0 所有权: 正式 Motor Service 是唯一生产 owner。诊断命令 pwm_test
 * 必须经 motor_pwm_grant_to_diag() 借用门(见下)。
 *
 * STEP 诊断许可(F2b P0-A): 使能链未验收期间, 任何能输出 STEP 的诊断命令一律拒绝,
 * 除非操作员在**隔离环境**(TMC 模块拔出 / 功率级断开)下人工确认并置
 * STEP_DIAG_ISOLATED_ENV_CONFIRMED = RT_TRUE。默认 RT_FALSE。
 *
 * 斜坡: 线性 current_hz → target_hz，accel_hz_s/decel_hz_s 可调，10ms 节拍。
 *
 * 运行期驱动配置守卫(F2b P1-E / F2c R03·R05): armed 期间按固定周期(200ms)复核
 * TMC 配置完整性, 失联/复位/配置丢失 → 停 STEP + 请求 DRV_ENABLE LOW(回读确认) +
 * FAULT + EVT_SOFT_FAULT; motor_start 起转前额外持配置事务做一次芯片实值复核。
 * ⚠ 这是软件层第二响应, 检测延迟最坏 ≈ 周期 + 一轮复核(≈400ms 量级, 未上板实测),
 *   不能当作停机时延指标, 也不能替代硬件独立切断。详见 motor.c 的 R05 记录。
 *
 * ENN 状态反馈适配(2026-10-21, 用户确认 PC.6 = 扩展板 H6-5/TMC_ENN + RevA 图纸核对):
 *   - 判定按**当前许可请求态**做, 不看 READY/IDLE 单一状态(motor_stop 停脉冲但仍 armed):
 *       请求禁能 → 期望 焊盘 LOW + 反馈 HIGH
 *       请求使能 → 期望 焊盘 HIGH + 反馈 LOW
 *     因此 arm 前(禁能侧)与 arm 后(start 前, 使能侧)两阶段各自成立, 不存在
 *     "PC6 必须一直为高否则永远 start 失败"的阶段死锁。
 *   - 接到实际决策点: 门掩码/臂成前/臂成后确认/start 前/armed 期每拍(无 UART 路径)/
 *     disarm·紧急停机的报告/故障清除(fault_reset)/TMC 静止写门/诊断 STEP 借用门。
 *   - 反馈读不到时**照旧**停 STEP、撤销许可并锁存故障, 只是不允许声称整链已安全。
 *   - 反馈 LOW 不证明有转矩; 反馈 HIGH 不证明模块真的断电; 线路完整性仍属硬件验收。
 *   - 有界确认窗口(safety_gpio 的 DRV_FB_TRIES/SETTLE_MS)是**测试可见常数**,
 *     不是停机时延上界; RISK-03/03a 继续开放, 本轮不重构线程。
 *   - 行程限位由 SAFETY_LIMIT_INPUTS_ENABLED(project_board.h)决定是否进入活动输入集,
 *     与"硬件验证宏"职责不同, 互不替代。
 */
#ifndef MOTOR_H
#define MOTOR_H

#include <rtthread.h>
#include "app_health.h"

/* ⚠ 硬件使能链验收开关(见上)。RT_FALSE = motor_arm 拒绝。 */
#ifndef MOTOR_HARDWARE_ENABLE_PATH_VALIDATED
#define MOTOR_HARDWARE_ENABLE_PATH_VALIDATED   RT_FALSE
#endif

/* ⚠ STEP 诊断隔离环境人工确认开关(F2b P0-A)。
 * RT_FALSE = pwm_test 等任何能输出 STEP 的诊断路径一律拒绝: 现板与门/非门损坏,
 * "DRV_ENABLE 焊盘 LOW"证明不了 TMC_ENN 已禁能, 所以 STEP 一旦输出就可能真实驱动
 * 电机。只有在 TMC 模块拔出 / 功率级断开、只为示波器量 STEP 频率时, 才允许由操作员
 * 人工翻转本宏并在验证记录里写明隔离条件 —— 它不能替代使能链验收(那仍是
 * MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)。两个宏都为 TRUE 时也不需要本宏。 */
#ifndef STEP_DIAG_ISOLATED_ENV_CONFIRMED
#define STEP_DIAG_ISOLATED_ENV_CONFIRMED       RT_FALSE
#endif

/* motor_arm() 门禁位定义(motor_arm_gate_mask()/motor_get_gate_fail_mask 用)。
 * bit0..bit4 是既有五门, **含义不得重排或复用**; bit5 是 2026-10-21 **追加**的
 * ENN 反馈门(第 4 门内部已按新的活动输入集合适配, 不是把就绪写成恒真)。 */
#define MOT_GATE_ENABLE_PATH   (1u << 0)   /* 硬件使能链未验收 */
#define MOT_GATE_NOT_READY     (1u << 1)   /* 安全状态机不在 READY */
#define MOT_GATE_UNHEALTHY     (1u << 2)   /* 本子系统不健康/无 PWM 设备 */
#define MOT_GATE_NOT_PROTECTED (1u << 3)   /* Fix A: Safety 保护链未就绪 */
#define MOT_GATE_NO_DRV_CONFIG (1u << 4)   /* Phase 8-F2: TMC 驱动参数未下发/下发失败 */
#define MOT_GATE_FB_MISMATCH   (1u << 5)   /* 追加: 最终 ENN 反馈与当前许可请求不一致 */

/* 运行期配置守卫(F2b P1-E)导致停机的原因码, 写入快照 guard_reason。
 * 0..3 为既有编码; 4 是 2026-10-21 追加的反馈不一致(不复用旧值)。 */
#define MOT_GUARD_NONE          0u
#define MOT_GUARD_CFG_LOST      1u   /* 驱动配置状态/快照已不再有效 */
#define MOT_GUARD_INTEGRITY     2u   /* 复核读不到 或 现值偏离已下发值(断电/复位) */
#define MOT_GUARD_TXN_BUSY      3u   /* 配置事务连续占用超过上限, 无法复核 */
#define MOT_GUARD_ENN_FEEDBACK  4u   /* 追加: 请求与 TMC_ENN 反馈不一致(反馈, 不是急停按钮) */
#define MOT_GUARD_BUSY_MAX      3u   /* 上面这条的上限(单位: 守卫周期) */

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
    rt_uint16_t guard_stops;    /* F2b P1-E: 运行期配置守卫触发的停机次数 */
    rt_uint8_t  guard_reason;   /* 最近一次守卫停机的原因码(MOT_GUARD_*) */
    subsys_health_t health;
} motor_snapshot_t;

/* 幂等初始化: 查找 epwm1、建立斜坡线程、状态 IDLE、
 * DRV_ENABLE 写 LOW 并回读确认(确认失败 → health=FAILED, 返回错误)。 */
rt_err_t motor_init(void);

/* 申请使能。门禁(五项全部满足才放行, 2026-10-21 追加第 6 位反馈条件):
 *   1. MOTOR_HARDWARE_ENABLE_PATH_VALIDATED == RT_TRUE   (硬件链验收)
 *   2. safety 状态机处于 READY                            (状态机放行)
 *   3. 本子系统健康且 PWM 设备可用                        (软件健康)
 *   4. safety_protection_ready() == TRUE                  (IRQ 门 OPEN +
 *      极性操作员 CONFIRM + **活动输入集合**逐路实测安全电平;
 *      限位宏关闭时该集合只有 DIAG, 且 PC.6 不再当作急停输入)
 *   5. TMC2209 驱动参数已 CONFIGURED                      (Phase 8-F2: 电流档位
 *      未知/未下发时不得使能; apply 失败即长期占住此门)
 *   6. (追加 MOT_GATE_FB_MISMATCH)最终 ENN 反馈与当前许可请求一致
 * 全部门禁无条件求值(均为无副作用只读), 拒绝原因以位掩码保留, 便于真机确认各门
 * 确实参与判定。arm 内部还额外做: 请求前确认禁能侧反馈一致 → 写 HIGH+回读 →
 * 有界确认使能侧反馈一致, 任一步不成就回滚撤销许可并投递反馈不一致事件。 */
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

/* 只读计算五门当前状态: 返回"失败门"位掩码(0 = 全通, 允许 arm)。
 * 无副作用, 供 runtime_selftest/MSH 证明第 4、5 门确实参与判定。 */
rt_uint32_t motor_get_gate_fail_mask(void);

/* 诊断命令(pwm_test)借用 EPWM1 ch0 的许可门(F2b P0-A 收紧)。
 * 第一道: STEP 输出许可 —— MOTOR_HARDWARE_ENABLE_PATH_VALIDATED 或
 * STEP_DIAG_ISOLATED_ENV_CONFIRMED 至少一个为 TRUE(现板两个都是 FALSE → 一律拒绝),
 * 因为焊盘 LOW 在门电路损坏的板子上证明不了驱动已禁能。
 * 之后才轮到所有权条件: 未 armed、state==IDLE、current_hz==0、target_hz==0、
 * 且 DRV_ENABLE 焊盘实测 LOW。全部满足才返回 RT_EOK, 拒绝原因由本函数打印。
 * 正式 Motor Service 才是 EPWM 的生产 owner。 */
rt_err_t motor_pwm_grant_to_diag(void);

/* F2b P1-E 用: 运行期守卫的纯判定函数(不碰硬件、不产生总线流量)。
 * in->due=0 表示本轮未到复核周期。动作只有 NONE / STOP 两种, STOP 由调用方
 * (斜坡线程)执行: 停 STEP + 请求 DRV_ENABLE LOW + armed=0 + FAULT + 事件。
 * 导出给 MSH 自测 motor_drv_guard_selftest 做确定性真值表验证。 */
typedef struct
{
    rt_uint8_t armed;            /* 0/1 */
    rt_uint8_t due;              /* 1 = 本轮已做复核, 0 = 未到周期 */
    rt_uint8_t cfg_state_ok;     /* tmc2209_drive_config_ok_for_arm() */
    rt_err_t   integrity;        /* tmc2209_config_integrity_check() 返回值 */
    rt_uint8_t busy_streak;      /* 连续 -RT_EBUSY 次数(输入) */
} motor_guard_in_t;

typedef struct
{
    rt_uint8_t stop;             /* 1 = 必须立即停机 */
    rt_uint8_t reason;           /* MOT_GUARD_* */
    rt_uint8_t busy_streak;      /* 更新后的连续 busy 次数 */
} motor_guard_out_t;

void motor_guard_decide(const motor_guard_in_t *in, motor_guard_out_t *out);

#endif /* MOTOR_H */
