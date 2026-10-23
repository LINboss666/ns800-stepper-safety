/*
 * stop_priority.h - 停机优先协调层 (Phase 8 S2 集中实现, 2026-10-23)
 *
 * 解决的问题（任务书 phase8-stop-priority-s2-integrated §0）：
 *   1) 停机请求的"第一次降险动作"原先排在可 FOREVER 等待的 ss_lock/mot_lock、可逐字符忙等
 *      的控制台以及带延时的许可回读之后；
 *   2) 使能/开启是一条跨多步、无序列化边界的序列，停机请求落在间隙里时，旧操作仍可把
 *      STEP 时间基或驱动许可重新拉起来。
 *
 * 协议要点（与设计 v2 一致，形式按实现任务书的裁定）：
 *   - 一个 32 位 **epoch** + 显式 **owner/phase** 状态；每次故障请求、预约、正式释放、
 *     成功人工恢复都换 epoch。所有相关字段的读写走同一个**短临界区**
 *     （rt_hw_interrupt_disable/enable，Cortex-M7 上即 PRIMASK）。
 *   - **短保护在一个临界区内同步完成两条彼此独立的降险动作**（STEP 冻结 + 许可 LOW）
 *     并就地记录两个原始结果；不使用"发布/认领/执行"三段式，也不用 PENDING→RUNNING
 *     的窗口外认领（那会留下被抢占的空洞）。
 *   - 真正的 RUN-capable 动作（许可 HIGH、PWM enable、PWM 寄存器提交、armed/运行态提交、
 *     诊断 enabled 提交）必须在**资格判定所在的同一个窗口内**执行；禁止"门内检查、门外写"。
 *   - 窗口内禁止：取任何锁、rt_thread_mdelay、UART、rt_kprintf、动态分配、循环重试、
 *     浮点计算。原因有两条源码事实支撑：
 *       · drv_uart.c:403-427 的 TX 等待按 rt_tick_get() 判超时，而 PRIMASK 屏蔽 SysTick
 *         ⇒ 窗口内 tick 不推进，任何按 tick 超时的等待永不退出；
 *       · HAL/drv_epwm.c:318-321 与 :370-371 的 set 路径会**先抓 TBCTL 旧模式再写回**
 *         ⇒ 跨窗口的"读-算-写"会把已冻结的时间基恢复成 RUN。
 *     窗口内允许：固定数目的寄存器写与固定数目的字段赋值。
 *   - MMIO 之后先 __DSB() 再恢复中断（CMSIS核心头，见 drv_common.c 的包含链）。
 *
 * ⚠ 模型边界（不得越界声称）：本机制只协调**本核的普通可屏蔽上下文**。它不涵盖 NMI /
 *   HardFault、不涵盖另一核或其它总线主设备、不涵盖未走本协议的维护通道（通用 `pin` 命令、
 *   pin_status 的主动重写、selftest 的初始化写）。因此**不得**声称"任意 shell 操作下全局安全"。
 *   epoch 饱和（0xFFFFFFFF）后禁止新的预约与人工恢复，但每次显式保护仍照做关闭。
 */
#ifndef STOP_PRIORITY_H
#define STOP_PRIORITY_H

#include <rtthread.h>
#include <rtdevice.h>
#include "stop_result.h"

typedef enum {
    SP_NONE = 0,        /* 通道无人持有 */
    SP_MOTOR,           /* Motor Service 持有 */
    SP_DIAG,            /* 诊断 pwm_test 持有 */
    SP_HELD_UNKNOWN     /* 关断未确认: 谁都不许接管, 也不许声称已交回 */
} sp_owner_t;

typedef enum {
    SP_RESERVED = 0,    /* 已预约, 还没开启任何东西 */
    SP_ARMED,           /* Motor: 许可已 HIGH 且已确认 */
    SP_RUNNING          /* 时间基已启动(输出中) */
} sp_phase_t;

/* 一次开启流程的身份。owner 与 epoch 必须同时匹配才算有效；
 * "同 owner 正常释放后再预约"会得到新 epoch，因此旧 token 不会误命中新预约。 */
typedef struct {
    rt_uint32_t epoch;
    sp_owner_t  owner;
} sp_token_t;

/* 一次**实际** PWM 关断尝试的记账（attempted/result/raw_rc/src/epoch）。
 * 从未占用过该通道的诊断路径保持 attempted=FALSE + result=NOT_ATTEMPTED，
 * 绝不伪造一次 API 调用。 */
typedef struct {
    rt_bool_t         attempted;
    mot_stop_result_t result;
    rt_err_t          raw_rc;
    rt_uint8_t        src;
    rt_uint32_t       epoch;
} sp_pwm_rec_t;

/* 许可(PC.23)动作: **裸写已发出** 与 **本次回读确认** 是两个独立事实。
 * rt_pin_write 返回 void ⇒ written=TRUE/OK 只表示写请求已发给寄存器。 */
typedef struct {
    rt_bool_t    written;
    rt_err_t     write_rc;
    rt_uint8_t   src;
    rt_uint32_t  write_epoch;
    rt_bool_t    confirmed;
    rt_err_t     confirm_rc;
    rt_uint32_t  confirm_epoch;
} sp_permit_rec_t;

/* 协调层完整快照（只读复制，供状态命令与主机用例判定）。 */
typedef struct {
    rt_uint32_t      epoch;
    sp_owner_t       owner;
    sp_phase_t       phase;
    rt_bool_t        inhibit;
    rt_bool_t        exhausted;
    rt_bool_t        motor_stop_latched;
    rt_bool_t        diag_stop_latched;
    rt_bool_t        diag_ever_reserved;
    rt_bool_t        metadata_pending;
    rt_uint32_t      first_fault_code;
    rt_uint32_t      reason_events;
    rt_uint32_t      trips;            /* 已完成的短保护次数 */
    rt_uint32_t      merged;           /* 同一请求内被合并掉的重复次数 */
    rt_uint32_t      stale_dropped;    /* 身份失配被丢弃的晚到结果次数 */
    sp_pwm_rec_t     pwm;              /* 最近一次实际关断 */
    sp_permit_rec_t  permit;           /* 最近一次许可动作 */
    sp_pwm_rec_t     first_unrecovered;/* **首个未恢复**的失败(不是最后一笔成功) */
} sp_snapshot_t;

/* 一次关断/复核的凭证：两条输出路径各自的语义视图 + 许可结果。 */
typedef struct {
    rt_bool_t         valid;
    rt_uint32_t       epoch;
    sp_pwm_rec_t      pwm;
    sp_permit_rec_t   permit;
    rt_bool_t         motor_off_ok;    /* Motor 侧本次有有效关断结果 */
    rt_bool_t         diag_off_ok;     /* 诊断侧本次有效(含"从未占用") */
    rt_bool_t         diag_attempted;
    mot_stop_result_t diag_result;
    rt_err_t          diag_rc;
} sp_proof_t;

/* ---------- 绑定（不重置 epoch / 锁存 / inhibit） ---------- */
rt_err_t sp_bind_pwm(struct rt_device_pwm *pwm, int channel);
rt_err_t sp_bind_permit_pin(rt_base_t numeric_pin);
void     sp_get_bound(struct rt_device_pwm **out_pwm, int *out_ch, rt_base_t *out_pin);

/* ---------- 短保护（新显式请求；不依赖 owner，不做认领） ---------- */
/* 无条件执行一次短保护：换 epoch + 置 inhibit + 一次实际关断 + 独立许可 LOW + 记账。
 * 可在任意线程上下文调用；返回"PWM 原始错误优先，否则许可裸写的原始错误"。 */
rt_err_t sp_trip_fast(rt_uint32_t code, rt_uint32_t events, rt_uint8_t src,
                      sp_proof_t *out);

/* 同一次操作（UART/配置事务）的各层共用开始时的 expected_epoch：
 * 失配 => 返回 -RT_EINTR 且**不**改变任何状态（不重复关闭、不制造新故障）。 */
rt_err_t sp_trip_for_operation(rt_uint32_t expected_epoch, rt_uint32_t code,
                               rt_uint32_t events, rt_uint8_t src, sp_proof_t *out);

/* §4.3：普通 ramp-zero / apply 回退 / disarm / pwm_test stop —— 实际 disable 与登记
 * **同窗**；该次失败时在同一窗口内追加抑制/换 epoch/许可 LOW，且**不做第二次 disable**。
 * 成功时只登记结果，不形成 inhibit。返回该次 disable 的原始 rc。 */
/* release_on_ok=TRUE: 成功时在同一短提交里再写许可 LOW 并撤销预约(换 epoch, **不**形成
 *   inhibit) —— 用于 motor_disarm / pwm_test stop。
 * release_on_ok=FALSE: 成功只登记结果并保留预约(Motor 回到 ARMED) —— 用于受控减速到零。
 * 失败时两条都一样: 同窗追加抑制/换 epoch/许可 LOW，且**绝不**做第二次 rt_pwm_disable。 */
rt_err_t sp_off_attempt(sp_owner_t who, rt_uint8_t src, rt_uint32_t code,
                        rt_uint32_t events, int release_on_ok, sp_proof_t *out);

/* ---------- 预约 / 阶段 / 释放 ---------- */
rt_err_t sp_reserve(sp_owner_t want, sp_token_t *out);      /* NONE -> RESERVED, 换 epoch */
rt_err_t sp_phase_set(const sp_token_t *t, sp_phase_t phase);
rt_err_t sp_release(const sp_token_t *t);                    /* 正式释放, 换 epoch */

/* ---------- 真正的 RUN-capable 提交（资格判定与实际写同窗） ---------- */
rt_err_t sp_enter_token(const sp_token_t *t, rt_base_t *saved);   /* 成功=仍在窗口内 */
void     sp_leave_token(rt_base_t saved);
rt_err_t sp_commit_permit_high(const sp_token_t *t, sp_proof_t *out);
rt_err_t sp_commit_pwm_start(const sp_token_t *t, sp_proof_t *out);
/* 已经在门内的调用方(如 motor 的"配置提交 + 启动"合成一个窗口)用这两条:
 * 它们不再自己开关中断, 因此不会把两次窗口夹成一次可抢占的缝隙。 */
rt_err_t sp_pwm_start_held(const sp_token_t *t);
/* 取本 owner 当前有效的身份(用于把预约跨多拍传下去): 未抑制、未饱和、owner 相符才成功。 */
rt_err_t sp_token_current(sp_owner_t want, sp_token_t *out);
/* 慢路径把 canonical 状态抄进各自快照用的镜像入口(不产生任何硬件动作)。 */
void     sp_mirror_motor(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                         rt_bool_t *latched);
/* PWM 寄存器提交由调用点在门内直接完成：
 *   sp_enter_token() → ns800_epwm_commit_prepared_held()（drv_epwm.h，固定整数写，
 *   不写回 count mode）→ __DSB() → sp_leave_token()。
 * 计算必须在门外（prepare），窗口内不许有浮点/循环/IPC/日志。 */
/* 门外确认结果带回：只有同一 token 仍有效才提交 confirmed 影子；晚到的旧确认
 * 一律丢弃，且**不**反向写 LOW 去破坏新预约。 */
rt_err_t sp_note_permit_confirmed(const sp_token_t *t, rt_err_t confirm_rc);
rt_err_t sp_note_low_confirmed(rt_err_t confirm_rc);

/* ---------- 观测（无等待快照；不因查询而执行保护） ---------- */
void        sp_get(sp_snapshot_t *out);
rt_bool_t   sp_inhibited(void);
/* 已处于窗口内的调用方专用(不再自己开关中断): 供"复核+一次寄存器写"同窗的场合,
 * 例如 motor_set_direction 在写 DIR 前重核停机位。 */
rt_bool_t   sp_inhibited_held(void);
/* epoch 已饱和: 禁止新的预约与人工恢复(每次显式保护仍照做关闭)。 */
rt_bool_t   sp_epoch_exhausted(void);
rt_uint32_t sp_epoch(void);
rt_bool_t   sp_owner_is(sp_owner_t o);
sp_phase_t  sp_get_phase(void);
rt_bool_t   sp_motor_stop_latched(void);
rt_bool_t   sp_diag_stop_latched(void);
rt_bool_t   sp_diag_ever_reserved(void);
/* S1 语义视图：motor.c / step_pwm.c 用它维持既有 API 的返回与快照含义不变。 */
void        sp_view_motor(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                          rt_bool_t *latched);
void        sp_view_diag(mot_stop_result_t *res, rt_err_t *rc, rt_bool_t *latched);
rt_bool_t   sp_diag_off_confirmed(void);
rt_bool_t   sp_diag_output_active(void);
rt_bool_t   sp_take_pending(rt_uint32_t *code, rt_uint32_t *events,
                           sp_proof_t *out);   /* 慢整理取走一次待处理事实 */

/* ---------- 人工恢复（fault_reset 专用；唯一清 inhibit/锁存的入口） ---------- */
/* 最终提交用的成对窗口原语(把 PRIMASK 与 MMIO 后的屏障留在本模块内):
 * ⚠ 窗口内只允许固定数目的字段赋值, 不得取锁/打印/延时/碰 UART。 */
rt_base_t   sp_win_begin(void);
void        sp_win_end(rt_base_t saved);
rt_bool_t   sp_recovery_start(rt_uint32_t *ticket);        /* 需 inhibit 且非 exhausted */
rt_err_t    sp_recovery_probe_off(rt_uint32_t ticket, sp_proof_t *out);
rt_bool_t   sp_recovery_valid(rt_uint32_t ticket, sp_proof_t *proof);
/* 以下三个 _held 版本要求调用方**已在同一窗口内**（fault_reset 的最终提交）：
 * 固定字段赋值，不取锁、不打印。窗口内嵌套调用是安全的(rt_hw_interrupt_enable 恢复的是
 * 保存下来的 PRIMASK=1, 不会中途放开中断), 但按任务书 §7 只允许开**一次**短临界区,
 * 因此最终提交用 _held 版本。 */
rt_bool_t   sp_recovery_valid_held(rt_uint32_t ticket, sp_proof_t *proof);
rt_bool_t   sp_finish_recovery_held(rt_uint32_t ticket);   /* FALSE = 有更新的一代请求 */
void        sp_clear_latches_held(void);

/* 供主机夹具与状态命令显示的名字。 */
const char *sp_owner_name(sp_owner_t o);
const char *sp_phase_name(sp_phase_t p);

#endif /* STOP_PRIORITY_H */
