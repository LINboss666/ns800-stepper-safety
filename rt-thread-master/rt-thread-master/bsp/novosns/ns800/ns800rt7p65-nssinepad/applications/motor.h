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
 * ⚠ 这是软件层第二响应。检测延迟的量级估计是"周期 + 一轮复核(≈400ms 量级)":
 *   **不是最坏停机时延** —— 它没计入 ss_lock/mot_lock/UART xfer 的等待(本轮未改),
 *   也没有上板实测(2026-10-23 措辞收窄, 详见 motor.c 的 R05/S1 记录与 S2 待办)。
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
 *
 * STEP 关断结果可信(2026-10-23, phase8-stop-result-s1 / S1):
 *   - `rt_pwm_disable()` 的返回值不再被吞掉: 每个关断调用点都走 checked 路径并把真实 rc
 *     记进快照(stop_result/stop_src/stop_rc, 见 stop_result.h)。
 *   - 关断失败或未取得设备句柄 => stop_fail_latched=1 + 第 7 门 MOT_GATE_STOP_UNCONFIRMED,
 *     拒绝新的 arm/start/诊断借用; 该位**只新增、只收紧**, 前六道门的条件一字未改。
 *   - 解除锁存只允许走 fault_reset() 的人工恢复复核(全部门查完才清); 一次后续成功的
 *     普通 stop 只更新"当前关断结果", 不清锁存、不解 Safety 故障、不自动重启。
 *   - API 返回 RT_EOK 只称"驱动接受了关断请求", **不是**示波器证明无脉冲;
 *     "current_hz==0 / target_hz==0"只称软件命令值。
 *   - 本轮(S1)不处理锁等待、UART 阻塞、在途 arm/enable 与停机的竞争 —— 那是 S2;
 *     不重构线程, 不宣称任何停机时延上界。
 */
#ifndef MOTOR_H
#define MOTOR_H

#include <rtthread.h>
#include "app_health.h"
#include "stop_result.h"
#include "stop_priority.h"   /* 2026-10-23 S2: sp_token_t(诊断借用预约身份) */

/* ⚠ 硬件使能链验收开关(见上)。RT_FALSE = motor_arm 拒绝。 */
#ifndef MOTOR_HARDWARE_ENABLE_PATH_VALIDATED
#define MOTOR_HARDWARE_ENABLE_PATH_VALIDATED   RT_FALSE
#endif

/* ⚠ STEP 诊断隔离环境人工确认开关(F2b P0-A)。
 * RT_FALSE = pwm_test 等任何能输出 STEP 的诊断路径一律拒绝。理由的当前口径
 * (2026-10-23 S1 更正): "DRV_ENABLE 焊盘 LOW"证明不了 TMC_ENN 已禁能 —— 这是**使能链
 * 尚未完成验收**(未烧录新版、未做波形/瞬态验收, 属授权范围); 维修前记录的"与门/非门
 * 损坏"不是当前已证实故障(静态电气与现有 SW1 功能已由用户确认 PASS (USER_CONFIRMED))。
 * 因为 STEP 一旦输出就可能真实驱动电机。只有在 TMC 模块拔出 / 功率级断开、只为示波器
 * 量 STEP 频率时, 才允许由操作员人工翻转本宏并在验证记录里写明隔离条件 —— 它不能替代
 * 使能链验收(那仍是 MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)。两个宏都为 TRUE 时也不需要本宏。
 * ⚠ 2026-10-23 S1 又追加了一层与宏无关的条件: 关断失败锁存期间连借用申请都直接拒绝
 *   (见 motor_pwm_grant_to_diag), 翻宏也不能越过未复核的关断失败。 */
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
/* 追加第 7 位(2026-10-23 S1, 只新增不复用旧位): STEP 关断结果不可信 —— 曾有
 * rt_pwm_disable 失败/未取得设备, 且未经人工恢复复核。这一位**只收紧**拒绝条件:
 * 它不替代前六道门, 也不因"频率字段已经是 0"而清零。 */
#define MOT_GATE_STOP_UNCONFIRMED (1u << 6)

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
    rt_uint32_t current_hz;     /* 当前输出步频(0=软件命令值为 0, 见 stop_result) */
    rt_uint32_t target_hz;      /* 目标步频 */
    rt_uint32_t accel_hz_s;     /* 加速斜率 Hz/s */
    rt_uint32_t decel_hz_s;     /* 减速斜率 Hz/s */
    rt_uint8_t  dir;            /* 0/1 */
    rt_uint8_t  armed;          /* 使能许可(软件层) */
    rt_uint8_t  drv_en_request; /* 最近一次"写+回读确认成功"的电平(不是裸写请求) */
    rt_uint16_t guard_stops;    /* F2b P1-E: 运行期配置守卫触发的停机次数 */
    rt_uint8_t  guard_reason;   /* 最近一次守卫停机的原因码(MOT_GUARD_*) */
    subsys_health_t health;
    /* ---- 2026-10-23 S1 追加(只追加在末尾, 旧字段次序不变) ---- *
     * stop_result/stop_src/stop_rc 描述**最近一次 STEP 关断尝试**;
     * current_hz==0 不等于 stop_result==MOT_STOP_API_ACCEPTED。
     * stop_fail_latched 一旦置位只能由人工恢复复核清除(见 safety_state.c)。 */
    rt_uint8_t  stop_result;        /* mot_stop_result_t */
    rt_uint8_t  stop_src;           /* MOT_STOP_SRC_* */
    rt_int16_t  stop_rc;            /* 最近一次 rt_pwm_disable() 的原始返回值 */
    rt_uint8_t  stop_fail_latched;  /* 1 = 曾有关断失败/未取得设备, 未经人工恢复 */
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
 *   7. (追加 2026-10-23 S1 MOT_GATE_STOP_UNCONFIRMED)STEP 关断结果可信: 曾有关断
 *      失败/未取得设备且未经 fault_reset 人工恢复复核时拒绝。arm 内部还额外做:
 *      请求前确认禁能侧反馈一致 → 写 HIGH+回读 → 有界确认使能侧反馈一致, 任一步
 *      不成就回滚撤销许可(回滚里的关断也走 checked 路径并记账)并投递事件;
 *      回滚时若 PWM 也失败, arm 返回 PWM 错误(许可错误其次)。 */
rt_err_t motor_arm(void);

/* 解除使能: 停脉冲 + DRV_ENABLE 写 LOW 并回读确认 + 回 IDLE(不经斜坡)。
 * 2026-10-23 S1: 返回值 = **PWM 关断结果与许可写确认结果两者中较早出现的失败**
 * (两者都失败时返回 PWM 错误); 许可失败返回 -RT_EIO, 关断失败返回 rt_pwm_disable 的错误。
 * 两个原始 rc 分别保存在快照 stop_rc 与日志里, 调用方不得把任何非零都解释成
 * "只是 GPIO 失败"。失败时函数仍已照做两步关闭动作, 不回退。
 * 追加(2026-10-23 返修 R03 = S1-NEW-03): 关断失败时本函数**投一次**
 * EVT_SOFT_FAULT 交给既有链(Safety 线程消费 → safety_force_shutdown → 统一停机 +
 * FAULT_LATCHED + 黑匣子), 使"按提示执行 fault_reset"的人工恢复入口从此可达;
 * 本函数不直接改 Safety 状态、不拿 ss_lock。统一停机/紧急停机的失败分支不投事件
 * (不自激)。 */
rt_err_t motor_disarm(void);

/* 方向: dir=0/1 (PA.2 电平)。
 * Fix A: 只允许在完全静止时设置 —— current_hz==0 且 state ∈ {IDLE, FAULT}。
 * 运动中(斜坡/巡航)一律拒绝 -RT_EBUSY: 开环步进运行中翻 DIR 等同注入丢步/堵转。
 * 静止 armed(state==IDLE 且已 arm)允许设置, 供"接线定方向后再生成"流程。
 * ⚠ 2026-10-23 返修 R01(=S1-NEW-01)追加一层**只收紧**的判据: STEP 关断失败/未取得
 * 设备(stop_fail_latched)或诊断侧 owner 未交回(step_pwm_off_confirmed()==FALSE)期间,
 * "0Hz + FAULT"只是软件命令值, 不能当物理静止 —— 一律拒绝并返回 -RT_EPERM, 一次 DIR
 * 写都不产生; 解除只能走 fault_reset 的人工恢复复核。两条原判据与非静止拒绝码不变。 */
rt_err_t motor_set_direction(rt_uint8_t dir);

/* 设置目标步频(armed 后由斜坡趋近; 未 armed 仅记录)。0 等效减速停。 */
rt_err_t motor_set_target_hz(rt_uint32_t hz);

/* 开始运动: 需已 arm 且 target_hz>0, 且诊断 pwm_test 未占用 STEP 输出。 */
rt_err_t motor_start(void);

/* 受控停止: target=0, 线性减速到 0 后回 IDLE(仍保持 armed)。 */
rt_err_t motor_stop(void);

/* 紧急停机(唯一被 safety_force_shutdown 调用的停机入口):
 * 立即关 PWM 输出 + DRV_ENABLE 写 LOW 并回读确认 + state=FAULT。幂等。
 * 2026-10-23 S1: 返回值 = PWM 关断失败优先、否则许可写确认结果; 两者原始 rc 均另存。
 * 非 RT_EOK 表示"关断或许可至少有一步未确认成功", **绝不**等于"输出仍在运行"的确认,
 * 也绝不因为某一步失败就跳过另一步。调用方必须记录 CRITICAL 并仍锁存 FAULT_LATCHED。 */
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
 * 且 DRV_ENABLE 焊盘实测 LOW、两侧关断锁存都已复核。
 * 2026-10-23 S2(任务书 §9): 全部通过后本函数**再取一个真正的 SP_DIAG 预约身份**
 * 写进 *out_token —— 只返回 EOK 的旧只读判定不能当预约(那正是"停机落在借用序列
 * 间隙里之后, 旧流程照样能 enable"的洞)。拒绝原因由本函数自己打印。
 * 正式 Motor Service 才是 EPWM 的生产 owner。 */
rt_err_t motor_pwm_grant_to_diag(sp_token_t *out_token);

/* 只读借用门(不做预约、不产生任何硬件动作), 供 runtime_selftest 一类"体检"路径使用。
 * ⚠ 它**不**代表取得通道权; 要输出必须走上面的 motor_pwm_grant_to_diag()。 */
rt_err_t motor_diag_gate_check(void);

/* ---------- 2026-10-23 S2 CP3 R1 / CP4 K1: 慢整理拆成"观察"与"身份受检提交"两段 ----------
 * 原 motor_reconcile_after_trip(pwm_rc) 无身份: 它清 mot_res/armed/target/current 之前
 * 只比过一次 inhibit, 人工恢复 + 新预约之后旧慢整理仍能把新状态写回故障影子
 * (审查探针 stale-slow-reconcile)。现在两段分开:
 *   motor_trip_observe()  窗口外的带回读确认(有 settle/重试, 不能在临界区里跑)。
 *                         CP4 K1(§3): **两个**结论都要能拿到 —— 返回值是"确认是否
 *                         提交进了 proof 那一代"(EOK / -RT_EINTR / -RT_EINVAL),
 *                         *out_read_rc 是带回读的**原始** rc。原来只回 read rc, 于是
 *                         "回读成功但提交被身份门拒绝"在调用方那里仍然是"LOW 已确认",
 *                         并且存在向新记录补写旧结果的风险(现在由 sp 的门挡住, 不写)。
 *   motor_commit_off()    mot_lock 在窗口**外**取得(取不到就不提交) + **一个**短窗口里
 *                         做身份门与字段落位。返回 FALSE = 整笔放弃(不写新影子、
 *                         不动新预约/新 pending), 调用方如实记录。
 * 统一慢整理(safety_state 的 ss_reconcile_once)在自己的窗口里直接调
 * motor_shadow_from_proof_held() + motor_fault_fields_held(), 不经过本函数。 */
rt_err_t  motor_trip_observe(const sp_proof_t *pf, rt_err_t *out_read_rc);

#define MOT_COMMIT_TRIP     0   /* 短保护/守卫/显式急停: pending 门 + MOTOR_FAULT */
#define MOT_COMMIT_ROLLBACK 1   /* arm 回滚那次关断: 同代门 + MOTOR_FAULT */
#define MOT_COMMIT_DISARM   2   /* disarm: 同代门, 关断被接受回 IDLE, 否则 FAULT */
rt_bool_t motor_commit_off(const sp_proof_t *pf, int mode);

/* ---------- 2026-10-23 S2: 人工恢复的最终提交接入点(任务书 §7) ----------
 * fault_reset 需要在**一个**短临界区里同时落 Motor 影子、Safety 状态与 canonical,
 * 因此这里给三个专用入口(锁在窗口**外**取放, 窗口内只有固定字段赋值):
 *   motor_lock_take_checked()    窗口外取 mot_lock; 互斥量不可用返回 -RT_EIO,
 *                                调用方一律**不提交**(fail closed)。
 *   motor_recovery_fields_held() 调用方必须已在同一窗口内且已确认恢复票据有效。
 *   motor_shadow_from_proof_held() 同窗口内把一次复核带回的 proof 抄进 Motor 影子。
 *   motor_lock_give_checked()    窗口外释放。
 * ⚠ 唯一合法调用点 = safety_state.c 的 fault_reset(); 不导出 MSH 命令, 没有第二条
 *   恢复路径。原 motor_clear_stop_latch() 因此删除(无身份的公共清除口 = 旁路)。 */
/* CP4 K1(§3): 必须返回 rt_mutex_take 的**真实**结果。原来只核 mot_lock_ok, 然后把
 * take 的返回值丢掉、一律回 EOK —— "取锁失败不 ack / 不落位 / 不触发黑匣子"的调用方
 * 因此永远收不到错误(审查探针 reconcile-ignores-lock-error)。 */
rt_err_t motor_lock_take_checked(void);
void     motor_recovery_fields_held(void);
void     motor_shadow_from_proof_held(const sp_proof_t *pf);
/* 短窗口内的 Motor 故障字段落位(调用方已持 mot_lock 且已过身份门)。 */
void     motor_fault_fields_held(void);
void     motor_lock_give_checked(void);

/* ---------- 2026-10-23 S1: 关断结果的可观测性(只读, 不产生总线流量) ----------
 * out_result/out_src/out_rc 反映**最近一次** STEP 关断尝试; *out_latched 是
 * "曾有关断失败或未取得设备"的锁存位, 只能由 fault_reset 的复核路径清零。
 * 快照里的 current_hz/target_hz 为 0 **不能**替代本函数的结论。 */
void motor_get_stop_state(mot_stop_result_t *out_result, rt_uint8_t *out_src,
                          rt_err_t *out_rc, rt_bool_t *out_latched);

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
