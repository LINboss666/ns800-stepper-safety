/*
 * sp_measure_a0.h - 目标机测量 A0：短保护窗口的记录器接口（任务 phase8-target-measurement-a0）
 *
 * 这件事**只**打开"给已经存在的关闭动作配一份可导出的原始时间戳/身份快照"这一条路。
 * 它不是安全门禁：四个目标安全宏（motor.h:69/:83、tmc2209.h:100、project_board.h:51）
 * 在所有变体里继续 RT_FALSE，本开关既不解除它们，也不允许 HIGH/RUN/STEP 输出。
 *
 * 默认目标（NS800_TARGET_MEASUREMENT_A0 == 0）必须**完全绕开**测量路径：
 *   - 本头文件此时只给出四个展开成原语的空宏；
 *   - 不声明任何类型、不分配记录 RAM、不产生计数器/PPB 访问、不导出任何 shell 命令；
 *   - stop_priority.c 的三个入口回到原来的 sp_win()/sp_end(l) 序列。
 * 这是编译期事实，不是"运行期 if 包住了新增调用"——默认镜像里连符号都不存在。
 *
 * 有效性口径沿用 reports/plans/phase8-target-measurement-plan.md §2.5：
 *   clock_basis 三层（本批最多到 CONFIG_READBACK，CLI 没有把它改成 EXTERNAL_VERIFIED 的入口）、
 *   counter_status 三值（未检查 / 不可用 / 已证递增）、逐条 body_valid + invalid_reason。
 * "主体区间"用 SysTick COUNTFLAG 证明期间无重装（见 §5.2 的实现前提），**不是**用 VAL 模差分；
 * "包围区间"本批没有独立时基与完整防混叠依据 ⇒ 恒无效，只保留原始四时间戳。
 *
 * schema=2（r1）相对 schema=1 的判据收紧，全部由复审 A0-02 提出：
 *   - counter_status 的"曾检查通过"只是**能力缓存**，不再充当每条样本的充分条件；
 *     每条样本自己核起点门控与终点门控（DEMCR.TRCENA + DWT_CTRL.CYCCNTENA 四位），
 *     记在 counter_gates 里，缺任一即 SPM_IV_GATE_LOST。
 *   - body_end == body_begin 记 SPM_IV_NO_PROGRESS：停转的计数器给出的 0 差值不是"0 耗时"，
 *     不得进入时长分布（原始值仍逐字保留）。
 *   - 起点读已经把历史 COUNTFLAG 读清，那次**之前**的旗不再单独否定当前主体
 *     （SPM_IV_FLAG_PRE 退役，只留 SPM_GD_FLAG_PRE_SET 观测位）；主体内的真实重装
 *     仍由终点读的 SPM_IV_RELOAD_IN_BODY 判无效。
 *
 * 命令接缝（复审 A0-01）：目标上的命令名是 **spm**，由
 *   MSH_CMD_EXPORT_ALIAS(spm_cmd, spm, ...) 生成 __fsym_spm{name="spm", func=&spm_cmd}。
 *   MSH_CMD_EXPORT 会把函数名本身当命令名（finsh.h:101-112/142-168），而 msh.c:232-249
 *   按**完整名字**查表、不会自动去掉 _cmd 后缀，所以别名必须是 ALIAS。
 *
 * 控制面并发合同：spm_cmd/spm_init_counter/spm_stop_accepting/spm_clear_records 只由
 *   **单个 msh 控制台线程**调用（RT-Thread 的 tshell 只有一个控制台会话）；被测入口
 *   spm_open/win_note/body_end/publish 才是与业务线程/ISR 并发的部分。因此"静止态"
 *   （state=IDLE 且 in_flight=0 且 records=0 且 guard_depth=0）是控制者负责维持的前提，
 *   记录器不额外为多控制台会话做仲裁。
 */
#ifndef SP_MEASURE_A0_H
#define SP_MEASURE_A0_H

#include <rtthread.h>

/* ---------------- 功能开关（默认关闭） ---------------- */
#ifndef NS800_TARGET_MEASUREMENT_A0
#define NS800_TARGET_MEASUREMENT_A0 0
#endif

#define SPM_SCHEMA_VERSION   2u
#define SPM_SLOTS            64u      /* 单批次只追加、不覆盖、不循环复用 */

/* 被测入口身份（与任务书 §3 的三个入口一一对应）。 */
#define SPM_ENTRY_TRIP_FAST       1u
#define SPM_ENTRY_TRIP_FOR_OP     2u
#define SPM_ENTRY_OFF_ATTEMPT     3u

/* 出口编号：每条早退都必须留下"本次是否做了动作"，不能靠 canonical 历史值冒充。 */
#define SPM_BR_TRIP_FAST_DONE        10u
#define SPM_BR_TRIP_OP_STALE         11u
#define SPM_BR_TRIP_OP_PROTECTED     12u
#define SPM_BR_OFF_BAD_OWNER         13u
#define SPM_BR_OFF_STALE             14u
#define SPM_BR_OFF_RELEASE_PE        15u
#define SPM_BR_OFF_RELEASE_LE        16u
#define SPM_BR_OFF_RELEASE_OK        17u
#define SPM_BR_OFF_RESERVE_PE        18u
#define SPM_BR_OFF_RESERVE_ZERO_OK   19u

/* counter_status：未做检查与检查后确认不可用是**两种不同**事实。 */
#define SPM_CNT_UNCHECKED        0u
#define SPM_CNT_OK               1u
#define SPM_CNT_UNAVAILABLE      2u

/* clock_basis（计划 §2.5）；本批运行时最多停在 CONFIG_READBACK。 */
#define SPM_CLK_SOURCE_ASSUMED       0u
#define SPM_CLK_CONFIG_READBACK      1u
#define SPM_CLK_EXTERNAL_VERIFIED    2u

/* invalid_reason 位（可叠加；记录保留全部命中原因，不做单一归因）。
 * 0x0004 在 schema=1 里叫 FLAG_PRE（"窗口前旗已置位"）。它否定的是一次**已经被起点读
 * 读清**的历史事件，而不是当前 body 内发生的事，因此 schema=2 起退役为纯观测位
 * （guard_metadata 的 SPM_GD_FLAG_PRE_SET 保留），不再单独使当前主体无效（复审 A0-02b）。 */
#define SPM_IV_NONE                 0x0000u
#define SPM_IV_COUNTER_NOT_OK       0x0001u   /* 能力缓存 UNCHECKED / UNAVAILABLE => 没有有效计数差 */
#define SPM_IV_GUARD_UNAVAILABLE    0x0002u   /* SysTick 未启用 / 非处理器时钟 / LOAD 不可读 */
#define SPM_IV_RELOAD_IN_BODY       0x0008u   /* 窗口内发生重装 => 主体 >= 一圈, 混叠不可排除 */
#define SPM_IV_COUNTER_WRAPPED      0x0010u   /* 32 位计数差跨回绕或 end < begin => 保守判无效 */
#define SPM_IV_NESTED_GUARD         0x0020u   /* 内层记录清掉了外层旗 => 外层不可判 */
#define SPM_IV_CFG_CHANGED          0x0040u   /* 两次读之间 LOAD/CTRL 配置发生变化 */
#define SPM_IV_NO_PROGRESS          0x0080u   /* body_end == body_begin => 本次计数没往前走 */
#define SPM_IV_ENCLOSING_UNPROVEN   0x0100u   /* 包围区间: 无独立时基/完整防混叠依据 */
#define SPM_IV_GENERATION_STALE     0x0200u   /* 发布时槽代次与预约代次不一致 */
#define SPM_IV_SLOT_LOST            0x0400u   /* 批次已满被丢弃(记在批计数器上, 不落槽) */
#define SPM_IV_GATE_LOST            0x0800u   /* 本次样本自己的 DEMCR.TRCENA/DWT.CYCCNTENA 门控不成立 */

/* guard_metadata 位：本次判据用到的现场事实，逐条留档而不是只写"理论可行"。 */
#define SPM_GD_SYSTICK_ENABLED      0x01u
#define SPM_GD_CLKSOURCE_PROCESSOR  0x02u
#define SPM_GD_FLAG_PRE_SET         0x04u
#define SPM_GD_FLAG_POST_SET        0x08u
#define SPM_GD_LOAD_CAPTURED        0x10u
#define SPM_GD_DWT_TRCENA           0x20u
#define SPM_GD_DWT_CYCCNTENA        0x40u

/* counter_gates 字节：逐样本的计数器门控事实（不是"init 曾经成功过"）。
 * bit0/bit1 = 本次**起点**读到 DEMCR.TRCENA / DWT_CTRL.CYCCNTENA 已置位；
 * bit2/bit3 = 本次**终点**同样两位已置位。四个位都成立才允许本次计数差算有效。 */
#define SPM_GT_BEFORE_TRCENA        0x01u
#define SPM_GT_BEFORE_CYCCNTENA     0x02u
#define SPM_GT_AFTER_TRCENA         0x04u
#define SPM_GT_AFTER_CYCCNTENA      0x08u
#define SPM_GT_BEFORE_MASK          (SPM_GT_BEFORE_TRCENA | SPM_GT_BEFORE_CYCCNTENA)
#define SPM_GT_AFTER_MASK           (SPM_GT_AFTER_TRCENA  | SPM_GT_AFTER_CYCCNTENA)
#define SPM_GT_ALL_MASK             (SPM_GT_BEFORE_MASK | SPM_GT_AFTER_MASK)

/* 控制状态机（命令侧）。 */
#define SPM_STATE_IDLE        0u
#define SPM_STATE_ACTIVE      1u
#define SPM_STATE_STOPPING    2u

#if NS800_TARGET_MEASUREMENT_A0

/* 一条记录 = 一次在途调用的全部可导出事实。全部固定宽度类型。 */
typedef struct {
    rt_uint32_t schema;                  /* SPM_SCHEMA_VERSION */
    rt_uint32_t capture_generation;      /* 本次预约的全局代次 */
    rt_uint8_t  slot;                    /* 0..SPM_SLOTS-1 */
    rt_uint8_t  entry_id;                /* SPM_ENTRY_* */
    rt_uint8_t  context_isr;             /* 1 = 中断嵌套上下文 */
    rt_uint8_t  saved_primask;           /* 本次窗口保存下来的 PRIMASK 原值 */
    rt_uint8_t  input_release_on_ok;     /* off_attempt 的原参数 */
    rt_uint8_t  proof_out_null;          /* out==NULL 也要固定本次证据 */
    rt_uint32_t input_expected_epoch;
    rt_uint8_t  input_owner;
    rt_uint8_t  pad0[3];
    rt_uint32_t epoch_before;
    rt_uint32_t epoch_after;
    rt_uint8_t  owner_after;
    rt_uint8_t  phase_after;
    rt_uint8_t  inhibit;
    rt_uint8_t  pending;                 /* metadata_pending */
    rt_uint32_t pending_epoch;
    rt_uint8_t  motor_stop_latched;
    rt_uint8_t  diag_stop_latched;
    rt_uint32_t trips_before;            /* 本次进入窗口时的计数 */
    rt_uint32_t trips_after;             /* 本次钉快照时的计数(差值即本次是否算过一次保护) */
    rt_uint32_t observed_pwm_epoch;
    rt_uint8_t  observed_pwm_attempted;
    rt_uint8_t  observed_pwm_result;
    rt_int32_t  observed_pwm_raw_rc;
    rt_uint32_t observed_permit_epoch;
    rt_uint8_t  observed_permit_written;
    rt_uint8_t  observed_permit_level;
    rt_int32_t  observed_permit_write_rc;
    rt_int32_t  call_outcome_or_branch;  /* 本次对外的返回码(原合同值, 未被记录改写) */
    rt_uint8_t  branch_id;
    rt_uint8_t  pwm_called_by_this_call;
    rt_uint8_t  low_called_by_this_call;
    rt_uint8_t  counter_gates;           /* SPM_GT_* 逐样本门控事实(schema=2 起) */
    rt_uint32_t pre;                     /* 被测 disable 之前 */
    rt_uint32_t body_begin;              /* 进入原窗口之后 */
    rt_uint32_t body_end;                /* 原关闭/记账 + 原最终 __DSB 之后, 恢复中断之前 */
    rt_uint32_t post;                    /* 恢复原 PRIMASK 之后尽早 */
    rt_uint8_t  counter_status;
    rt_uint8_t  clock_basis;
    rt_uint8_t  body_valid;
    rt_uint8_t  enclosing_valid;
    rt_uint16_t invalid_reason;
    rt_uint8_t  guard_metadata;
    rt_uint8_t  pad3[1];
    rt_uint32_t guard_load_seen;         /* 判据所依据的 SysTick->LOAD(0=未采) */
    rt_uint32_t dropped_at_publish;      /* 本次发布时批次累计丢弃数 */
    rt_uint8_t  complete;                /* 0 = RESERVED, 1 = COMPLETE */
    rt_uint8_t  pad4[3];
} spm_rec_t;

/* 一次在途调用的句柄：放在调用者的栈上，槽满被丢弃时 rec 为空但流程照常。 */
typedef struct {
    spm_rec_t  *rec;                     /* RT_NULL = 本次未落槽(批次满/状态非 ACTIVE) */
    rt_uint32_t capture_generation;
    rt_uint8_t  entry_id;
    rt_uint8_t  guard_owner;             /* 1 = 本次持有 COUNTFLAG 判据(嵌套时内层置位) */
    rt_uint8_t  counter_status;
    rt_uint8_t  clock_basis;
    rt_uint8_t  guard_metadata;
    rt_uint32_t guard_load_seen;
    rt_uint32_t pre;
    rt_uint32_t body_begin;
    rt_uint32_t body_end;
    rt_uint32_t post;
    rt_uint16_t invalid_reason;
    rt_uint8_t  saved_primask;
    rt_uint8_t  context_isr;
    rt_uint8_t  open_ok;                 /* 1 = 已预约(或已确认丢弃), 供 SPM_END 判断 */
    rt_uint8_t  gates;                   /* SPM_GT_*: 本次起点/终点的计数器门控 */
} spm_ctx_t;

/* ---- 生命周期（只由 stop_priority.c 的三个入口使用） ----
 * spm_open()      : 窗口外预约 + 采 pre（不递归调用 sp_win, 不取锁, 不分配内存）。
 * spm_win_note()  : 原 sp_win() 之后在窗口内调用 —— COUNTFLAG 起点读 + body_begin；
 *                   epoch_before/trips_before 由 stop_priority.c 在同一窗口内逐字段钉。
 * spm_body_end()  : 原最终 __DSB 之后、恢复原 PRIMASK 之前 —— body_end + COUNTFLAG 终点读。
 * spm_publish()   : 恢复中断之后 —— post + 有效性判定 + RESERVED→COMPLETE。
 * 这四个函数在开关为 0 时根本不存在；SPM_* 宏直接落回原语 sp_win()/sp_end(l)。 */
void       spm_open(spm_ctx_t *c, rt_uint8_t entry_id, rt_uint32_t input_expected_epoch,
                    rt_uint8_t input_owner, rt_uint8_t release_on_ok, const void *proof_out);
void       spm_win_note(spm_ctx_t *c, rt_uint8_t saved_primask_set);
void       spm_body_end(spm_ctx_t *c);
void       spm_publish(spm_ctx_t *c, rt_int32_t api_rc, rt_uint8_t branch_id,
                       rt_uint8_t pwm_called, rt_uint8_t low_called);

/* ---- 观测/控制（命令侧与主机夹具共用同一实现） ---- */
rt_uint32_t spm_state(void);
rt_uint32_t spm_records_used(void);
rt_uint32_t spm_in_flight(void);
rt_uint32_t spm_dropped(void);
rt_uint8_t  spm_counter_status(void);
rt_uint8_t  spm_clock_basis(void);
rt_uint32_t spm_guard_load_value(void);
rt_uint32_t spm_generation_next(void);
/* 只读访问第 i 槽(供导出与主机核对; 不复制、不加锁、不改变任何状态)。
 * i 超界返回 RT_NULL。complete 字段告诉调用方这槽是否还是 RESERVED(不得当完整样本)。 */
const spm_rec_t *spm_record_at(rt_uint32_t i);
const char *spm_counter_status_name(rt_uint8_t v);
const char *spm_clock_basis_name(rt_uint8_t v);
const char *spm_entry_name(rt_uint8_t v);
/* 命令入口：返回本次动作的文本结论码（0=已执行, 非 0=拒绝原因编号）。全部只接受
 * 普通、未屏蔽的线程上下文；非法上下文一律拒绝且不在其中打印。 */
int  spm_cmd(int argc, char **argv);
/* 供主机夹具/未来 A 包核实的三个真实寄存器侧动作。 */
int  spm_init_counter(void);
void spm_stop_accepting(void);
int  spm_dump_records(void);
int  spm_clear_records(void);
int  spm_trip_once(void);            /* 只做一次正式 safety_force_shutdown(FAULT_SELF_TEST) */
#endif /* NS800_TARGET_MEASUREMENT_A0 */

/* ---------------- 编译期适配（默认目标只走原语） ----------------
 * ⚠ SPM_WIN/SPM_END 在开启时展开成本文件内的私有 measured-enter/exit
 *   (spm_win_rt/spm_end_rt)，它们只在 stop_priority.c 里使用：那里才有 sp 状态与
 *   sp_win_nest。默认(SPM 关)时展开成原 sp_win()/sp_end(l)，一个符号都不引入。 */
#if NS800_TARGET_MEASUREMENT_A0
#define SPM_SCOPE(m, entry_id, exp_ep, who, rel_ok, proof_out) \
    spm_ctx_t (m); spm_open(&(m), (entry_id), (exp_ep), (who), (rel_ok), (proof_out))
#define SPM_WIN(m)        spm_win_rt(&(m))
#define SPM_END(l, m, api_rc, br, pwm_called, low_called) \
    spm_end_rt((l), &(m), (api_rc), (br), (pwm_called), (low_called))
#else
/* 展开为空：不声明局部对象、不引用任何 spm 符号、不改原语顺序。 */
#define SPM_SCOPE(m, entry_id, exp_ep, who, rel_ok, proof_out)
#define SPM_WIN(m)        sp_win()
#define SPM_END(l, m, api_rc, br, pwm_called, low_called)  sp_end(l)
#endif

#endif /* SP_MEASURE_A0_H */
