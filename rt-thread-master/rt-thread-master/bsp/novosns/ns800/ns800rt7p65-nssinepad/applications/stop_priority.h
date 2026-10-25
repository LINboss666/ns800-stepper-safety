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
 *       · 旧实现里 drv_epwm.c 的 set 路径会**先抓 TBCTL 旧模式再写回**，跨窗口的"读-算-写"
 *         会把已冻结的时间基恢复成 RUN。该写回在 S2/CP5 之后已取消：现在
 *         drv_epwm.c:293-318 走 ns800_epwm_prepare() + ns800_epwm_configure_regs()，
 *         而 drv_epwm_leaf.inc:232 起明确"**不写 TBCTL 的计数模式**"（见同文件 :18 与
 *         drv_epwm.c:301 的实现注释）。窗口纪律本身保留（窗口内仍只许固定字段赋值与
 *         固定寄存器写），但依据不再是"set 会写回计数模式"。
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

/* 许可(PC.23)动作: **裸写已发出的请求电平**、**写请求结果** 与 **本次回读确认**
 * 是三件不同的事(R1 之后确认必须绑定"请求电平 + 那一代 epoch"):
 * rt_pin_write 返回 void ⇒ written=TRUE/OK 只表示写请求已发给寄存器。 */
typedef struct {
    rt_bool_t    written;
    rt_uint8_t   request_level;     /* 0=LOW(禁能请求) 1=HIGH(使能请求); 2=无请求 */
    rt_err_t     write_rc;
    rt_uint8_t   src;
    rt_uint32_t  write_epoch;
    rt_bool_t    confirmed;
    rt_err_t     confirm_rc;
    rt_uint32_t  confirm_epoch;
    rt_uint8_t  confirmed_level;    /* 本次确认针对的电平 */
    /* CP4 §8.2: ARMED 的最终提交除"焊盘回读确认"外, 还要有**同代 ENN 反馈复核成功**
     * 与"授权这次 HIGH 的那份配置有效性摘要仍是同一代"两条证据; 四条都绑 epoch。 */
    rt_bool_t    enn_ok;
    rt_err_t     enn_rc;
    rt_uint32_t  enn_epoch;
    rt_uint8_t   enn_level;
    rt_uint32_t  cfg_view_gen;       /* 授权这次 HIGH 的配置摘要代次 */
} sp_permit_rec_t;

#define SP_PERMIT_LEVEL_NONE  2u    /* 尚无许可请求记录 */

/* 协调层完整快照（只读复制，供状态命令与主机用例判定）。
 * R6(2026-10-23)区分三件事: ①模块**当前输出事实**(pwm/phase/owner);
 * ②各 owner **自己的**历史关闭结果与失败锁存(motor 与 diag 两路分别记);
 * ③**首个未恢复**的 PWM/许可失败原始证据(valid 位使其可判定)。
 * Motor 的新一轮运行不得改写已归还给诊断的历史视图。 */
typedef struct {
    rt_uint32_t      epoch;
    sp_owner_t       owner;
    sp_phase_t       phase;
    rt_bool_t        inhibit;
    rt_bool_t        exhausted;
    rt_bool_t        motor_stop_latched;
    rt_bool_t        diag_stop_latched;
    rt_bool_t        motor_output_active;   /* Motor 自己那一路是否仍持有/未确认交回 */
    rt_bool_t        diag_output_active;    /* 诊断那一路同上(与 Motor 当前输出无关) */
    rt_bool_t        diag_ever_reserved;
    rt_bool_t        metadata_pending;
    rt_uint32_t      pending_epoch;         /* pending 属于哪一代(peek/ack 用) */
    rt_uint32_t      first_fault_code;
    rt_uint32_t      reason_events;
    rt_uint32_t      trips;            /* 已完成的短保护次数 */
    rt_uint32_t      merged;           /* 同一请求内被合并掉的重复次数 */
    rt_uint32_t      stale_dropped;    /* 身份失配被丢弃的晚到结果次数 */
    rt_uint32_t      advance_failed;   /* 计数到顶(饱和)次数 */
    sp_pwm_rec_t     pwm;              /* 模块最近一次实际关断 */
    sp_pwm_rec_t     diag_pwm;         /* K6.1: 诊断那一路自己的最近一次实际关断 */
    rt_uint32_t      last_trip_epoch;  /* 最近一次短保护**建立**的那一代(K3 三态判定) */
    sp_permit_rec_t  permit;           /* 最近一次许可动作 */
    sp_pwm_rec_t     first_unrecovered;        /* 首个未恢复的 **PWM** 失败 */
    rt_bool_t        first_unrecovered_valid;
    sp_permit_rec_t  first_permit_unrecovered; /* 首个未恢复的 **许可** 失败 */
    rt_bool_t        first_permit_valid;
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
    /* R1(2026-10-23 CP3): 两路各自"未复核的关断失败锁存"也随 proof 一起带走,
     * 这样慢整理与人工恢复的判定**只用这一代的数字**, 不必再去读活的 canonical
     * (读活值 = 给旧动作续命的另一种写法)。 */
    /* CP4 K3/K4(§6.2/§6.3): 这次动作**自己那一代**是否带着抑制义务。消费方据此
     * 决定走 pending 门还是同代门 —— 一次"清理成功但许可 LOW 失败"的结果已经形成
     * 故障义务, 不能再按正常关闭落位。 */
    rt_bool_t         inhibit;
    rt_bool_t         motor_stop_latched;
    rt_bool_t         diag_stop_latched;
    rt_uint32_t       first_fault_code;
    rt_uint32_t       reason_events;
} sp_proof_t;

/* ---------- 绑定（不重置 epoch / 锁存 / inhibit） ---------- */
rt_err_t sp_bind_pwm(struct rt_device_pwm *pwm, int channel);
rt_err_t sp_bind_permit_pin(rt_base_t numeric_pin);
void     sp_get_bound(struct rt_device_pwm **out_pwm, int *out_ch, rt_base_t *out_pin);

/* K3(§5.3): 一次"为某条操作做短保护"的请求有三种**完全不同**的结果, 调用方必须
 * 分开处理, 不能把 valid=TRUE 一律读成"这次操作已经保护完":
 *   BY_THIS_CALL      —— 本次调用自己完成了换代 + 两条降险动作;
 *   ALREADY_PROTECTED —— 当前代仍是一条待整理的故障(可能由同一次操作里更底层做过,
 *                        也可能是另一条并发故障): 本次**不做**第二轮关闭, 只能用
 *                        带回的当前事实整理自己那一路, 且落位仍要过同一代门;
 *   SUPERSEDED        —— 已经换到无抑制的新健康代(人工恢复/新预约/正式释放): 本次
 *                        结论**过期丢弃**, 既不称"已保护", 也不得据此写任何状态。 */
typedef enum {
    SP_PROTECT_NONE = 0,
    SP_PROTECT_BY_THIS_CALL,
    SP_PROTECT_ALREADY_PROTECTED,
    SP_PROTECT_SUPERSEDED
} sp_protect_outcome_t;

/* ---------- 短保护（新显式请求；不依赖 owner，不做认领） ---------- */
/* 无条件执行一次短保护：换 epoch + 置 inhibit + 一次实际关断 + 独立许可 LOW + 记账。
 * 可在任意线程上下文调用；返回"PWM 原始错误优先，否则许可裸写的原始错误"。 */
rt_err_t sp_trip_fast(rt_uint32_t code, rt_uint32_t events, rt_uint8_t src,
                      sp_proof_t *out);

/* 同一次操作（UART/配置事务）的各层共用开始时的 expected_epoch：
 * 失配 => 返回 -RT_EINTR **并且把当前 canonical 事实复制进 out**(调用方据此整理自己
 * 那一路, 不再重复关闭, 也不会拿自己的旧数字冒充)。匹配才执行整个短保护。
 * R2(2026-10-23): out 在两条路径上都可用, 因为调用方(motor 守卫)必须能对
 * "底层已保护"这一事实做同样的状态整理。 */
rt_err_t sp_trip_for_operation(rt_uint32_t expected_epoch, rt_uint32_t code,
                               rt_uint32_t events, rt_uint8_t src, sp_proof_t *out,
                               sp_protect_outcome_t *outcome);

/* §4.3：普通 ramp-zero / apply 回退 / disarm / pwm_test stop —— 实际 disable 与登记
 * **同窗**；该次失败时在同一窗口内追加抑制/换 epoch/许可 LOW，且**不做第二次 disable**。
 * 成功时只登记结果，不形成 inhibit。返回该次 disable 的原始 rc。 */
/* release_on_ok=TRUE: 成功时在同一短提交里再写许可 LOW 并撤销预约(换 epoch, **不**形成
 *   inhibit) —— 用于 motor_disarm / pwm_test stop。
 * release_on_ok=FALSE: 成功只登记结果并保留预约(Motor 回到 ARMED) —— 用于受控减速到零。
 * 失败时两条都一样: 同窗追加抑制/换 epoch/许可 LOW，且**绝不**做第二次 rt_pwm_disable。 */
/* CP4 K3(§5.1)/K4(§6.1): 这条**必须**带上原操作的代次身份 expected_epoch(命令入口
 * 第一次等待之前采的快照)。检查在窗口内、在任何 rt_pwm_disable / 许可 LOW 真实写
 * **之前**完成: 失配 => 一个寄存器写都不发, 不清新 owner, 不写新影子, 不产生新 pending,
 * 只把当前事实带回(旧回滚无权动新预约)。
 * 成功那次动作的 proof.epoch / pwm.epoch / permit.write_epoch 与之后的确认
 * expected_epoch **同代**(§6.1: 不允许"关断记在旧代、proof 发在新代")。
 * 返回值优先序(§6.2): PWM 失败 > 许可失败 > 换代/边界失败; 各 raw 字段独立留档。 */
rt_err_t sp_off_attempt_for(rt_uint32_t expected_epoch, sp_owner_t who, rt_uint8_t src,
                            rt_uint32_t code, rt_uint32_t events, int release_on_ok,
                            sp_proof_t *out);

/* ---------- 预约 / 阶段 / 释放 ---------- */
rt_err_t sp_reserve(sp_owner_t want, sp_token_t *out);      /* NONE -> RESERVED, 换 epoch */
/* CP4 K2(§4.1): **受检预约**。在同一窗口里先核对"当前 epoch 仍是这条命令入口处采到的
 * expected_epoch"(命令原身份), 再做 inhibit/owner/锁存/饱和检查, 才分配新 epoch 并
 * 发出新 token。失配返回 -RT_EINTR 并且**不产生预约、不产生任何硬件动作** ——
 * 于是"旧 arm 在保护/恢复之后接着把新预约拿走"这条路从根上断掉。
 * ⚠ 调用方不得把失配重试成"再去取一个新 token"。 */
rt_err_t sp_reserve_for(rt_uint32_t expected_epoch, sp_owner_t want, sp_token_t *out);
rt_err_t sp_release(const sp_token_t *t);                    /* 正式释放, 换 epoch */
/* 原 sp_phase_set() 已删除(§8.2): 无生产调用者的通用 phase 写等于"手动升级 ARMED/RUNNING"
 * 的旁路。phase 只能由 sp_reserve/sp_reserve_for(RESERVED)、sp_arm_commit*(ARMED, 需要
 * 同代 HIGH 已确认 + ENN 复核成功 + 配置摘要同代)与 sp_pwm_start_held(RUNNING, 需要真实
 * rt_pwm_enable 成功)建立。 */

/* ---------- 真正的 RUN-capable 提交（资格判定与实际写同窗） ----------
 * R5(2026-10-23): 这些新增的生产接口**自己**也守编译期许可, 不依赖"每个调用者永远
 * 不出错": HIGH 只允许 SP_MOTOR 且要求 MOTOR_HARDWARE_ENABLE_PATH_VALIDATED
 * 与 TMC_DRIVE_CONFIG_HW_VALIDATED, 并要求**同代无等待配置有效性摘要**仍然放行
 * (CP4 §8.2: 读取不取锁/不碰 UART/不延时); Motor RUN 还要同一许可 + 本预约 phase 已
 * 到 ARMED; ARMED 本身只能由"同 token 的 HIGH 已请求且已确认 + 同代 ENN 复核成功 +
 * 授权那次 HIGH 的配置摘要代次未变"提交。诊断 RUN 保留原双宏合同
 * (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED 或 STEP_DIAG_ISOLATED_ENV_CONFIRMED)。
 * stop/LOW/保护在所有宏 FALSE 时继续允许 —— 朝安全方向的动作用永不设许可门。
 * 非法 owner 取值(既不是 MOTOR 也不是 DIAG)一律拒绝。 */
rt_err_t sp_enter_token(const sp_token_t *t, rt_base_t *saved);   /* 成功=仍在窗口内 */
void     sp_leave_token(rt_base_t saved);
rt_err_t sp_commit_permit_high(const sp_token_t *t, sp_proof_t *out);
rt_err_t sp_commit_pwm_start(const sp_token_t *t, sp_proof_t *out);
/* 已经在门内的调用方(如 motor 的"配置提交 + 启动"合成一个窗口)用这两条:
 * 它们不再自己开关中断, 因此不会把两次窗口夹成一次可抢占的缝隙。 */
rt_err_t sp_pwm_start_held(const sp_token_t *t);
/* HIGH 裸写成功**只**表示禁能请求被撤销, 不等于 ARMED。只有 GPIO 回读 + ENN 反馈
 * 确认之后, 由 armed 提交(与 mot.armed=1 同一窗口)才把 phase 抬到 SP_ARMED。 */
rt_err_t sp_arm_commit(const sp_token_t *t);
rt_err_t sp_arm_commit_held(const sp_token_t *t);
/* 取本 owner 当前有效的身份(用于把预约跨多拍传下去): 未抑制、未饱和、owner 相符才成功。
 * ⚠ R2: 这条**不能**用来给一个已经开始的旧操作"续命"—— 旧操作必须只用它在第一次
 * 慢动作之前拿到的那份快照。 */
rt_err_t sp_token_current(sp_owner_t want, sp_token_t *out);
/* 慢路径把 canonical 状态抄进各自快照用的镜像入口(不产生任何硬件动作)。 */
void     sp_mirror_motor(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                         rt_bool_t *latched);
/* PWM 寄存器提交由调用点在门内直接完成：
 *   sp_enter_token() → ns800_epwm_commit_prepared_held()（drv_epwm.h，固定整数写，
 *   不写回 count mode）→ __DSB() → sp_leave_token()。
 * 计算必须在门外（prepare），窗口内不许有浮点/循环/IPC/日志。 */
/* R1(2026-10-23)确认提交必须带身份: 只有"请求电平相符 + epoch 仍是那一代"的确认才写
 * confirmed 影子。晚到的旧 LOW/旧 HIGH 确认一律返回 -RT_EINTR, 既不改新记录,
 * 也**不**反向写 LOW 去破坏新的健康预约。 */
rt_err_t sp_note_permit_confirmed_for(rt_uint32_t expected_epoch,
                                      rt_uint8_t requested_level, rt_err_t rc);
/* token 版: 同一 token 仍有效且请求电平相符才提交确认; 失配返回 -RT_EINTR。
 * ⚠ 返回码**必须被消费** —— ce=EOK 但本函数返回 -RT_EINTR 时, 调用方不得把
 * drv_en_request/armed 标成成功(R2)。 */
rt_err_t sp_note_permit_confirmed(const sp_token_t *t, rt_uint8_t requested_level,
                                  rt_err_t rc);
/* CP4 K6.2(§8.2): ENN 反馈复核的成功也要**绑到那一代的那次请求**上才作数 ——
 * ARMED 的最终提交(sp_arm_commit_held)同时核对 HIGH 确认与本条, 二者都必须是
 * token 那一代。失配只计数(stale_dropped), 不写新记录。 */
rt_err_t sp_note_enn_confirmed_for(rt_uint32_t expected_epoch, rt_uint8_t requested_level,
                                   rt_err_t rc);
/* epoch 还能不能前进(饱和预检): 恢复/预约的最终提交在改字段之前用它。 */
rt_bool_t  sp_can_advance(void);

/* ---------- pending 生命周期（R1: 义务只在故障时产生, 取得与提交同窗） ----------
 * 语义: pending = "有一次故障/失败事实已经保护过, 但慢整理还没做完"。
 * 普通成功的 disarm/确认/状态查询**不**产生 pending。
 * peek 只读不清; 调用方拿到锁后在**同一个短窗口**里 gate+ack, 失配整笔丢弃。 */
rt_bool_t sp_pending_peek(sp_proof_t *proof, rt_uint32_t *code, rt_uint32_t *events);
/* R3(2026-10-23): **调用方已在短窗口内**时用的 canonical 一次性复制(不再开一层窗口)。
 * motor_set_direction 要"全部静止/可信关闭复核 + 真实 DIR 写"同窗, 只拿 inhibit 一个位
 * 是偏薄的条件; 在窗口里再调带窗口的读取函数在非递归互斥的主机模型里还会自锁。 */
void      sp_peek_held(sp_snapshot_t *out);
/* 调用方已在窗口内: 只有 expected_epoch 仍是当前故障那一代才 ack(清 pending)。
 * 较新请求留下的 pending 不会被旧的 ack 吞掉。 */
rt_bool_t sp_pending_ack_held(rt_uint32_t expected_epoch);
/* 调用方已在窗口内: 本次整理是否有资格提交(当前 epoch 相符 / 仍 inhibit /
 * pending 仍属这一代)。FALSE 时调用方**不得**写任何镜像或状态。 */
rt_bool_t sp_reconcile_gate_held(rt_uint32_t epoch);
/* 同代门(R1): 只要求"还是 proof 那一代"。用于**没有形成故障事实**的单次关断
 * (arm 回滚里 disable 被接受 / disarm 成功): 那时既无 inhibit 也无 pending,
 * 用 sp_reconcile_gate_held 会把合法的落位也挡掉。换过代 => 整笔放弃。 */
rt_bool_t sp_op_current_held(rt_uint32_t epoch);
rt_bool_t sp_op_current(rt_uint32_t epoch);
/* 兼容保留的"取走"入口已删除: 先清后整理会在取锁失败时把唯一义务丢掉。 */

/* ---------- 观测（无等待快照；不因查询而执行保护） ---------- */
void        sp_get(sp_snapshot_t *out);
rt_bool_t   sp_inhibited(void);
/* 已处于窗口内的调用方专用(不再自己开关中断): 供"复核+一次寄存器写"同窗的场合,
 * 例如 motor_set_direction 在同一个窗口里重核全部静止条件再写 DIR。 */
rt_bool_t   sp_inhibited_held(void);
/* CP4 K5(§7): 供上层判断"当前上下文能不能打印"。判据与窗口的存在条件同一套事实:
 * 目标上直接读 PRIMASK(任何来源的屏蔽都算); 主机夹具里由本模块自己的嵌套深度给出
 * (夹具的 rt_hw_interrupt_disable/enable 替身不暴露 PRIMASK)。 */
rt_bool_t   sp_intr_masked(void);
/* CP4 K5(§7): 供上层判断"当前上下文能不能打印"。判据与窗口的存在条件同一套事实:
 * 目标上直接读 PRIMASK(任何来源的屏蔽都算); 主机夹具里由本模块自己的嵌套深度给出
 * (夹具的 rt_hw_interrupt_disable/enable 替身不暴露 PRIMASK)。 */
rt_bool_t   sp_intr_masked(void);
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
rt_bool_t   sp_diag_output_active_held(void);
rt_bool_t   sp_motor_output_active(void);

/* ---------- 人工恢复（fault_reset 专用；唯一清 inhibit/锁存的入口） ---------- */
/* 最终提交用的成对窗口原语(把 PRIMASK 与 MMIO 后的屏障留在本模块内):
 * ⚠ 窗口内只允许固定数目的字段赋值, 不得取锁/打印/延时/碰 UART。 */
rt_base_t   sp_win_begin(void);
void        sp_win_end(rt_base_t saved);
rt_bool_t   sp_recovery_start(rt_uint32_t *ticket);        /* 需 inhibit 且非 exhausted */
rt_err_t    sp_recovery_probe_off(rt_uint32_t ticket, sp_proof_t *out);
/* CP4 K4(§6.3): 恢复复核**之后**在窗口外发现的问题(LOW 回读未确认、确认提交被拒、
 * ENN 再确认失败)同样是新失败 —— 本函数在票据仍成立的那个代里换 epoch + 保持抑制 +
 * 留 pending, 使旧票据自然失效(不是只有 pe!=EOK 才换代)。
 * 票据已被更新的一代取代时返回 -RT_EINTR 且**不改任何状态**。
 * 返回: EOK=已按新失败换代; -RT_EPERM=撞顶(抑制与 exhausted 保持); -RT_EINTR=过期。 */
rt_err_t    sp_recovery_note_failure(rt_uint32_t ticket, rt_uint32_t code,
                                     rt_uint32_t events, sp_proof_t *out);
rt_bool_t   sp_recovery_valid(rt_uint32_t ticket, sp_proof_t *proof);
/* 已在窗口内的调用方专用的两个视图(不再自己开关中断): 慢整理的最终提交窗口要
 * "身份检查 + 抄镜像"同窗, 视图本身不得再开一层窗口。 */
void        sp_view_motor_held(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                               rt_bool_t *latched);
void        sp_view_diag_held(mot_stop_result_t *res, rt_err_t *rc, rt_bool_t *latched);
/* 以下 _held 版本要求调用方**已在同一窗口内**（fault_reset 的最终提交）：
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
