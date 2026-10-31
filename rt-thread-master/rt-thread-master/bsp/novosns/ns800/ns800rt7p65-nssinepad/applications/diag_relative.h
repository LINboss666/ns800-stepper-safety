/*
 * diag_relative.h - SG/ADC 相对异常检测与主动保护 (M8)
 *
 * 为什么存在: 原 ACTIVE_PROTECTION 的严重判定要求"SG 崩 **且** 电流升"双计数
 * 同时成立(diagnosis.c 的 STALL_CONFIRMED), 而现有数据并不证明堵转一定使 VM 母线
 * 电流升高; 同时它的开启门禁是"电流标定来源 = MEASURED", 未实测标定时永远开不了。
 * 本模块**并行新增**一条不依赖绝对安培标定、只用 raw 码带与 SG 相对变化的保护:
 *   - 判据单位是 ADC raw 码(0..4095)与 SG_RESULT(0..1023), 不是 mA;
 *   - mA 继续可以按 THEORETICAL 显示, 但绝不参与本模式的判定, 也不构成安培承诺;
 *   - 原 MONITOR_ONLY 与原 ACTIVE_PROTECTION 的 MEASURED 语义一字不改地保留。
 *
 * 三个必须守住的身份纪律(缺任何一条, "持续 500ms"就是假的):
 *   1) SG 证据只认**独立成功采样**: in->sg_seq 必须是新值(该计数只在真实 UART
 *      读成功后 +1, 见 sensor_service.h 的 M8 注释); 帧序号前进但 sg_seq 不变 ⇒
 *      同一个旧 SG, 不推进 SG 统计、不累计 SG 持续时间。
 *   2) 源年龄按 **tick** 判, 且年龄检查在帧去重之前: 一直重放同一帧也必须能走
 *      "数据已过期"降险, 否则失效源会被当成"一直在正常采样"。
 *   3) 严重判定的原因必须自称 REL_SG_SEVERE / REL_ADC_SEVERE / REL_FUSED_SEVERE /
 *      REL_SENSOR_UNAVAILABLE, **不得**冒称双源确认的 STALL_CONFIRMED。
 *
 * 保护动作不新建链: 只复用 safety_post_event(EVT_MULTI_FAULT)(它内部已同步先做
 * stop_priority 短保护再通知 Safety 线程), 且**先请求保护、后打印摘要**。
 */
#ifndef DIAG_RELATIVE_H
#define DIAG_RELATIVE_H

#include <rtthread.h>
#include "diagnosis.h"
#include "motor.h"                 /* motor_state_t(RPPH_* 位定义要用) */

/* ---------- 持久化 profile(嵌进 project_config_t, 受 NS_PARAM_MAX=224 约束) ---------- */

#define DIAG_RPROF_MAGIC   0x4E535250u    /* "NSRP" */
/* ABI 2 (M8-R1/R6): 追加碰撞(RL_IMPACT)阈值四字段。ABI 不匹配一律具名拒绝,
 * 不做"猜测旧布局"的兼容 —— 现场没有已提交的 ABI1 profile。 */
#define DIAG_RPROF_ABI     2u

/* flags 位 */
#define RPF_COMMITTED      (1u << 0)      /* 已通过 set/commit 完整发布 */
#define RPF_FIELD_VERIFIED (1u << 1)      /* 现场验证过(只有现场证据才可置 1) */
#define RPF_FROM_LEARN     (1u << 2)      /* 阈值由正常基线+保守裕量生成 */

/* 允许的运行阶段位(与 motor_state_t 数值对应) */
#define RPPH_CRUISE        (1u << MOTOR_CRUISE)

/* 采样定义身份: ADC/帧 100Hz 与 SG 10Hz 分频(sensor_service.c 的
 * SENSOR_PERIOD_MS/SENSOR_SG_DIV)一起决定了"一个 raw 带"的物理含义。
 * 学习出来的 profile 只在同一采样定义下有效 —— 定义变了必须重学。 */
#define DIAG_RPROF_SAMPLING_DEF   1u

typedef struct
{
    rt_uint32_t magic;                /* 0 = 无 profile */
    rt_uint16_t abi;
    rt_uint16_t flags;                /* RPF_* */
    rt_uint32_t profile_id;
    rt_uint32_t revision;
    rt_uint32_t signature;            /* 工况+驱动配置签名(失配即不可用) */

    /* 适用工况 */
    rt_uint32_t hz_nominal;
    rt_uint32_t hz_low, hz_high;      /* 窄速度窗(含端点) */
    rt_uint8_t  dir_mask;             /* bit0=dir0, bit1=dir1 */
    rt_uint8_t  phase_mask;           /* 允许阶段(本任务只允许 CRUISE) */
    rt_uint8_t  tmc_cfg_state;        /* tmc_drv_cfg_state_t 快照 */
    rt_uint8_t  mstep_source;         /* 1=MRES 寄存器, 0=MS1/MS2 引脚 */
    rt_uint8_t  tmc_irun, tmc_ihold, tmc_mres, tmc_vsense;

    /* 静止零点(raw) */
    rt_uint32_t zero_mean_x1000;
    rt_uint16_t zero_min, zero_max, zero_n;
    /* 正常巡航 ADC raw 带 */
    rt_uint32_t run_adc_mean_x1000;
    rt_uint16_t run_adc_min, run_adc_max, run_adc_noise, run_adc_n;
    /* 正常巡航 SG 带 */
    rt_uint32_t run_sg_mean_x1000;
    rt_uint16_t run_sg_min, run_sg_max, run_sg_n;

    /* 阈值(raw 码单位, 与统计量同单位同定义) */
    rt_int16_t  adc_dev_warn, adc_dev_trip;     /* |raw-带| 偏离, 可上偏或下偏 */
    rt_uint16_t sg_drop_warn, sg_drop_trip;     /* run_sg_min - sg */
    rt_uint16_t dur_sg_ms, dur_adc_ms, dur_fused_ms, dur_recover_ms;
    /* age_sg_ms: SG 独立样本的最大年龄(10Hz 分频, 默认比帧宽一个数量级)。
     * age_frame_ms: **帧与 ADC** 的最大年龄 —— 两者同为 100Hz 节拍, M8-R1 B1 之后
     *   ADC 的 cur_tick 也按这个上限判, 外壳帧前进不代表又采过一次。 */
    rt_uint16_t age_sg_ms, age_frame_ms, min_new_sg;
    rt_uint8_t  sampling_def;      /* = DIAG_RPROF_SAMPLING_DEF, 见下 */
    /* M8-R1 R4: 这套 warn/trip 是按哪一档松紧从正常统计生成的(1 最宽 .. 3 最激进)。
     * 0 = 未按档生成(手工构造/旧数据), 校验与提交都必须重算。 */
    rt_uint8_t  threshold_tier;
    /* R6: 碰撞(独立于 SG/ADC)。振动量沿用项目已有 vib_impact_mg 口径(含 ~1g 基线),
     * 但**证据规则不同**: 必须是 impact_new_min 个"新采样成功的 IMU 帧"(imu_seq 前进
     * 证明), 在 dur_impact_ms 内出现; 滑窗里的旧 peak 不能冒充"又撞了一次"。 */
    rt_uint16_t impact_peak_mg;    /* 触发门(mg, 含重力基线) */
    rt_uint16_t impact_new_min;    /* 需要的独立 IMU 样本数, >=2 */
    rt_uint16_t dur_impact_ms;     /* 这些独立样本必须落在多长时间内 */
    rt_uint16_t age_imu_ms;        /* IMU 样本年龄上限(超 ⇒ 该源不可用, 不判碰撞) */

    /* 出处(证明这套基线来自哪些真实独立 SG 样本) */
    rt_uint32_t learn_sg_seq_first, learn_sg_seq_last;
} diag_rprof_t;

/* ---------- 运行期判定 ---------- */

typedef enum
{
    REL_REASON_NONE = 0,
    REL_SG_SEVERE,            /* 单源强 SG 异常: 独立降险, 不宣称双源确认 */
    REL_ADC_SEVERE,           /* 单源强 ADC 相对偏离(上偏或下偏) */
    REL_FUSED_SEVERE,         /* 中等 SG + 中等 ADC 在窗口内重叠且各自持续 */
    REL_SENSOR_UNAVAILABLE,   /* 运行中源年龄超限/冻结帧(独立原因, 不当 0 用) */
    REL_OUT_OF_WINDOW,        /* 离开被批准工况(不宣称覆盖) */
    REL_COVERAGE_LOST,        /* M8-R1: 已武装却失去判据覆盖(profile/签名/快照/帧) */
    REL_IMPACT                /* M8-R1/R6: 独立的新鲜 IMU 冲击证据 */
} rel_reason_t;

/* 覆盖状态由调用方(生产线程)算好后交给引擎 —— 引擎因此能对自己"没有有效 profile"
 * 的情形也做**有界降险**, 而不是静默跳过判据(M8-R1 B2/B3 的根因)。 */
#define RELCOV_OK          0u     /* profile 在、校验通过、签名与 live 一致 */
#define RELCOV_LOCK_FAIL   1u     /* 取不到配置快照 */
#define RELCOV_ABSENT      2u     /* 没有已提交 profile */
#define RELCOV_SIG_MISMATCH 3u    /* profile 属于别的驱动配置/采样定义 */
#define RELCOV_NOFRAME     4u     /* 取不到传感器帧(seq==0 或读失败) */
#define RELCOV_ENGINE_ERR  5u     /* profile 存在但校验不通过(运行中被改坏) */
#define RELCOV_MOTION_UNKNOWN 6u  /* M8-R2 R2-B3: 本帧没取到运动快照(或它已过期) */

const char *rel_cov_name(rt_uint8_t c);

/* 一次事故的不可覆盖快照(先保护, 再把这个打印/暴露出去)。 */
typedef struct
{
    rt_uint8_t  latched;              /* 1 = 已有首次触发, 后续帧不得覆盖 */
    rt_uint8_t  reason;               /* rel_reason_t */
    rt_uint8_t  mode_at_trip;         /* diag_mode_t */
    rt_int16_t  post_rc;              /* safety_post_event() 原始返回码 */
    rt_uint32_t profile_id, revision;
    rt_uint32_t trig_tick;            /* 触发时 now_tick */
    rt_uint32_t first_sg_seq, first_cur_seq;   /* 触发所用样本身份 */
    rt_uint32_t first_sg_tick, first_cur_tick;
    rt_int32_t  value_raw;            /* 触发时的量(SG / ADC raw / 振动 mg) */
    rt_int32_t  threshold_raw;
    rt_uint32_t persisted_ms;         /* 该条件已持续的毫秒 */
    /* ---- M8-R1 追加(只在末尾追加, 上面字段含义不变; latch 只在 RAM 里, 不落 Flash) ---- */
    rt_uint32_t first_imu_seq;        /* REL_IMPACT 用的是哪一次独立 IMU 采样 */
    rt_uint8_t  coverage_at_trip;     /* REL_COVERAGE_LOST 时处于哪个非 OK 档 */
    rt_uint8_t  severe_cnt;           /* 该严重条件累计到的独立样本数(证据数) */
} rel_latch_t;

/* 判定用的一致输入(由调用方在同一临界区取好后传入; 引擎内部不取锁不做 I/O) */
typedef struct
{
    diag_input_t in;          /* 含 sg_seq/cur_seq/imu_seq 与各 tick、motion_known */
    diag_rprof_t prof;        /* 一致快照副本 */
    rt_uint32_t  now_tick;    /* 诊断当前 tick(与帧 tick 必须分开) */
    rt_uint8_t   coverage;    /* RELCOV_*; 非 OK 时引擎走降险而不是判据 */
    /* M8-R1/R2: 引擎需要知道"这次武装是不是显式相对保护"。没有 profile 的
     * MONITOR_ONLY 只披露、不得误触发; 已显式 RELATIVE 的覆盖丢失才是降险义务。 */
    rt_uint8_t   mode;        /* diag_mode_t */
} rel_step_arg_t;

/* 相对引擎上下文(可实例化: 生产一份, 自检/主机测试各自栈上私有) */
typedef struct
{
    rt_uint32_t last_frame_seq;         /* 已消费帧号(重复帧不推进统计, 但年龄仍判) */
    rt_uint32_t last_sg_seq, last_cur_seq, last_imu_seq;
    /* 每个源"上一次成功样本"的 tick: 新样本到来时先与上一次比**间隔**。超过该源
     * 年龄上限的间隔就是间断 —— 间断两端不得凑成一段连续异常(M8-R1 R1.2)。 */
    rt_uint32_t prev_sg_tick, prev_cur_tick, prev_imu_tick;
    rt_uint32_t last_now_tick;
    rt_uint8_t  have_now;
    rt_uint32_t age_bad_ms;             /* 源年龄超限的连续毫秒 */
    rt_uint32_t out_run_ms;             /* 持续离开被批准工况的毫秒 */
    rt_uint32_t cov_lost_ms;            /* 持续失去判据覆盖的毫秒(M8-R1 R2) */
    rt_uint32_t gen;                    /* 本次武装的代际(与命令线程的期望值同步) */
    /* warn 级与 strong 级的时间/样本数**分开**累计: M8-R1 A1/A2 的根因就是
     * 严重判据借用了 warn 级的 500ms/3 样本, 一笔更强样本立刻顶替了强条件自身
     * 尚未成立的持续时间。 */
    rt_uint32_t sg_run_ms, sg_strong_run_ms;
    rt_uint32_t adc_run_ms, adc_strong_run_ms;
    rt_uint32_t fused_run_ms, ok_run_ms;
    rt_uint32_t imp_run_ms;
    rt_uint32_t sg_new_cnt, adc_new_cnt, ok_new_cnt, sg_strong_cnt, adc_strong_cnt, imp_cnt;
    rt_uint32_t sg_span_first, adc_span_first, fused_span_first, ok_span_first;
    rt_uint32_t sg_strong_span_first, adc_strong_span_first, imp_span_first;
    /* 恢复必须同时见过两个源的**新鲜正常**证据(M8-R1 R1.3: 不得因为某一帧恰好
     * 不是 fresh_sg+fresh_adc 同时成立, 就把中间那次新鲜异常样本忽略掉)。
     * bit0=本恢复段内见过新鲜正常 SG, bit1=见过新鲜正常 ADC;
     * 任一新鲜反向证据都把整段清零重来。 */
    rt_uint8_t  ok_src_seen;
    rt_uint8_t  coverage;             /* 引擎自己复核后的档位(不轻信调用方) */
    rt_uint8_t  imu_usable;           /* R6: IMU 这一路是否有新鲜有效样本 */
    rt_uint8_t  in_window_prev;
    rt_uint8_t  severe_active;
    rt_uint8_t  active_reason;          /* 导致 severe_active 的那次原因(粘住用) */
    /* M8-R2 R2-A.2: 本次武装的票据由**唯一写者自己**保存。
     * 旧写法把票据只存在 d_ctl 里、靠控制锁写, 于是摘要锁一失败就等于保护义务
     * 一起丢; 现在行为侧读这个私有字段(不需要锁), 发布侧把它同步进 d_ctl/d_pub。
     * 代际切换时随整代证据一起清零。 */
    rt_uint8_t  rearm_required;
    rel_reason_t verdict;
    rel_latch_t latch;
} rel_engine_t;

/* tick 差(无符号回绕安全; then 在 now 之后 = 0x80000000 表示"不可信/未来样本") */
rt_uint32_t diag_rel_tick_diff(rt_uint32_t now, rt_uint32_t then);

/* 名字(短行打印用) */
const char *rel_reason_name(rt_uint8_t r);

/* 复位: 保留 latch(事故证据不得被"复位计数"抹掉)之外的全部累计。 */
void rel_engine_reset_counters(rel_engine_t *e);
void rel_engine_wipe(rel_engine_t *e);
/* R2-B2: 新一代开始。与 reset_counters 的区别是**这一代的判据证据全部作废**:
 * 严重状态/活动原因、两级持续与样本数、三源身份与 prev tick、age/out/cov 累计、
 * latch 与 verdict 一起清, 只保留时间基线(last_now_tick/have_now)与新代际。
 * 于是"上一代攒出来的异常"不可能被下一代一笔旧帧立刻兑现。 */
void rel_engine_new_generation(rel_engine_t *e, rt_uint32_t gen);

/* profile 完整性/范围校验。返回 RT_EOK 才可用; why 可为 NULL。 */
rt_err_t rel_profile_validate(const diag_rprof_t *p, const char **why);

/* 由工况+驱动配置身份字段算签名(不含 id/rev/阈值, 故 live 值可直接比较)。 */
rt_uint32_t rel_profile_signature(const diag_rprof_t *p);

/* 单步判定(纯计算, 不打印、不取锁、不碰 UART/Flash)。返回本次原因。
 * severe 原因(SG/ADC/FUSED/SENSOR_UNAVAILABLE/OUT_OF_WINDOW)时**只返回原因**,
 * 由调用方决定何时 rel_request_protection() —— 生产上下文必须走
 * rel_prod_evaluate(), 它保证"先保护、后打印"。 */
rel_reason_t rel_engine_step(rel_engine_t *e, const rel_step_arg_t *a);

/* 保护请求(唯一的严重出口): 先 safety_post_event, 再落 latch, 打印由调用方做。 */
rt_err_t rel_request_protection(rel_engine_t *e, const rel_step_arg_t *a, rt_uint8_t mode);

/* ---------- 生产上下文(只由 Diagnosis Thread 推进; 测试请用私有上下文) ----------
 *
 * 并发所有权(M8-R1 R3.3 + M8-R2 R2-A)—— rel_prod_* 的字段分两类写者:
 *   d_rel_prod(引擎上下文)   : **唯一写者 = Diagnosis Thread**。命令线程永不直接
 *                              擦它; 想开始新一轮武装只 bump 期望代际(d_ctl.gen_want),
 *                              由线程自己在**步进之前**执行整代证据清零。
 *   d_ctl / d_incident / d_pub: 一把互斥量 d_ctl_lock 保护, 临界区内**不**取 cfg 锁、
 *                              不碰 UART/Flash/打印/mdelay。锁顺序固定:
 *                              mot(静止事务) -> ctl, 且 ctl 与 cfg 不同时持有。
 *
 * R2-A 的关键更正: "锁已初始化"和"本次真的抢到了"是两件事。
 *   - 旧写法用同一个 d_ctl_lock_ok 既代表初始化成功又代表可跳过 take,
 *     于是一次 take 失败 = 这次无锁写 + 以后永远不取锁(单向阀), 还可能让别的
 *     持有者被跳过 release。
 *   - 现在所有 take 都必须返回状态: 抢不到就**不复制、不发布、不改代际、不抹首因**,
 *     也绝不 release 自己没有持有的锁。
 *   - 读侧要么拿到整份, 要么拿到错误; "标量不会撕裂"不等于"整份一致"。 */
typedef struct
{
    rt_uint32_t gen_want;       /* 命令线程 bump: 期望的新代际 */
    rt_uint32_t generation;     /* 线程已完成同步的代际 */
    rt_uint8_t  rearm_required; /* 1 = 本次武装的票据已被真实触发消耗 */
    rt_uint8_t  mode;           /* R2-B1: 与代际同一临界区提交的诊断模式 */
    rt_uint8_t  mode_valid;     /* 0 = 还没提交过(读侧不得当作 MONITOR 假定) */
    rt_uint8_t  rsv;
} rel_ctl_t;

/* rel_prod_commit() 的 mode 参数: 表示"这次只动代际, 不动已提交模式"。 */
#define REL_CTL_MODE_KEEP   0xFFu

/* 事故首因的对外的只读快照(供状态行/CLI 打印; 首次原因不得被后续帧覆盖)。 */
typedef struct
{
    rt_uint32_t generation;
    rt_uint8_t  rearm_required;
    rt_uint8_t  have_current, have_archived;
    rel_latch_t current;        /* 本轮武装内的首次触发 */
    rel_latch_t archived;       /* 上一轮事故的首因(归档, 不被本轮覆盖) */
} rel_incident_t;

/* 初始化专用全清(只能在线程起来之前调用: diagnosis_init/自检起点)。
 * 运行期不得再用它"顺手清生产上下文" —— 那是唯一写者的职责(R2-A.5)。 */
void rel_prod_init_wipe(void);
/* d_ctl_lock 初始化: 返回 mutex_init 的真实结果。失败时 rel_ctl_take() 一律返回
 * 错误(而不是"当作有锁"), 所有写侧必须因此不动状态。 */
rt_err_t rel_ctl_init(void);
rt_bool_t rel_ctl_ready(void);                    /* 只回答"锁是否初始化成功" */
/* 抢锁: RT_EOK=本次确实持有(随后**必须** rel_ctl_give_held); 未初始化 -RT_EINVAL,
 * 超时/失败 -RT_EBUSY, 且调用方不得复制/发布/改代际/抹首因。 */
rt_err_t rel_ctl_take(void);
void     rel_ctl_give_held(void);
/* R2-B1: 模式与代际共同的提交协议。命令线程在**仍持有静止门**时调用一次:
 * 同一临界区内落 d_ctl.mode/mode_valid 与(可选)gen_want++。
 * 抢不到锁就返回错误, 两个字段都不变 => 不可能出现"模式已启用而代际仍旧"。 */
rt_err_t rel_prod_commit(rt_uint8_t mode, rt_bool_t bump_gen, rt_uint32_t *gen_out);
/* 兼容入口: 只 bump 代际、不动已提交模式; 失败时代际完全不变。 */
rt_err_t rel_prod_request_rearm(void);
rt_err_t rel_prod_get_ctl(rel_ctl_t *out);
rt_err_t rel_prod_get_incident(rel_incident_t *out);
/* acted: 本次调用是否真的执行了保护请求(供调用方在**保护之后**打印首次摘要)。
 * 步进前若读不到一致的控制状态, 本帧按 RELCOV_LOCK_FAIL 走有界降险,
 * 绝不带着可疑的旧证据继续宣称有效; 保护义务本身不因摘要锁失败而消失。 */
rel_reason_t rel_prod_evaluate(const rel_step_arg_t *a, rt_uint8_t mode,
                                  rt_err_t *post_rc, rt_bool_t *acted);
/* 运行期状态的一次性快照(M8-R1 R4.5 + M8-R2 R2-A.3)。
 * **写者**在每帧末把整份 rel_state_t 在短临界区里发布; **读者**要么拿到完整的
 * 一份(带它自己的 snap_seq), 要么拿到错误并且 out 被清零。snap_seq 与当前帧号之差
 * 就是"这份状态有多旧"的可见证据。 */
typedef struct
{
    rt_uint32_t sg_run_ms, sg_strong_run_ms;
    rt_uint32_t adc_run_ms, adc_strong_run_ms;
    rt_uint32_t fused_run_ms, ok_run_ms, imp_run_ms;
    rt_uint32_t age_bad_ms, out_run_ms, cov_lost_ms;
    rt_uint32_t sg_new_cnt, sg_strong_cnt;
    rt_uint32_t adc_new_cnt, adc_strong_cnt, ok_new_cnt, imp_cnt;
    rt_uint32_t last_sg_seq, last_cur_seq, last_imu_seq;
    rt_uint8_t  imu_usable;           /* R6: IMU 覆盖如实披露(不影响 SG/ADC 判据) */
    rt_uint8_t  coverage;             /* 引擎自己复核后的档位 */
    rt_uint8_t  verdict;
    rt_uint8_t  severe_active;
    rt_uint32_t snap_seq;             /* 发布时的帧号(0 = 还没发布过) */
    rt_uint32_t pub_fail_cnt;         /* 截至本次发布为止的发布失败次数 */
} rel_state_t;

rt_bool_t    rel_prod_get_latch(rel_latch_t *out);
/* 唯一写者(Diagnosis Thread)专用的首因读取: 闩锁由本线程写入、落下后不再改写,
 * 所以这里不需要、也**不允许**借控制锁去"保护"它。跨线程读者一律走
 * rel_prod_get_latch()/get_incident() —— 那两个是镜像, 要么整份要么明确读不到。
 * 触发摘要必须在保护之后打印, 而镜像可能正因为取不到锁而缺一份: 只拿镜像打印会把
 * "保护已经发生、摘要却打成空"当成正常状态。 */
rt_bool_t    rel_writer_latch(rel_latch_t *out);
rt_err_t     rel_prod_get_snapshot(rel_state_t *out);
/* 运行期用现场驱动配置/工况算 live 身份并填入候选 profile 的身份字段(供签名比较)。 */
void      rel_live_identity(diag_rprof_t *probe);
/* 上面两者的合成: 当前驱动配置/采样定义的 live 签名(0 表示取不到身份)。
 * Diagnosis Thread 每帧调用它来判"profile 是否仍属于当前工况"。 */
rt_uint32_t rel_live_signature(void);
/* 学习/零点累计器(生产上下文持有, 命令通过它写入)。档位(tier)随 base->threshold_tier
 * 一起进入采集器: rel_cap_finish 生成阈值时用同一档。 */
typedef enum { REL_CAP_ZERO = 0, REL_CAP_LEARN = 1 } rel_cap_kind_t;
rt_err_t  rel_cap_start(rt_uint8_t kind, rt_uint32_t want, const diag_rprof_t *base);
rt_err_t  rel_cap_feed(const diag_input_t *in);         /* 每帧调用(去重后), 只认真样本 */
rt_bool_t rel_cap_done(void);
rt_err_t  rel_cap_finish(diag_rprof_t *out, const char **why);  /* 分不开就具名拒绝 */
void      rel_cap_abort(void);

/* M8-R1 R4: 按档从**已存正常统计**重算 warn/trip(不引入新数据源)。
 * 返回非 RT_EOK 时一个字段都不改, why 给出具名原因与数据由调用方打印。 */
rt_err_t rel_profile_gen_thresholds(diag_rprof_t *p, rt_uint8_t tier, const char **why);
/* ADC 两侧的码域可达余量(按运行时扩张带 min-noise..max+noise 计)。 */
void     rel_adc_reach(const diag_rprof_t *p, rt_uint32_t *up, rt_uint32_t *dn);


/* ---------- 有界在线统计(零/学习采集用; 不在栈上开 N 帧数组) ---------- */
typedef struct
{
    rt_uint32_t n;
    rt_uint32_t min, max;
    rt_uint64_t sum;
    rt_uint64_t sum_sq;
} rel_acc_t;

void      rel_acc_reset(rel_acc_t *a);
void      rel_acc_add(rel_acc_t *a, rt_uint32_t v);
rt_bool_t rel_acc_mean_x1000(const rel_acc_t *a, rt_uint32_t *out);

/* 该原因是否要求真实保护动作(相对模式下的全部严重原因)。 */
rt_bool_t rel_reason_is_severe(rt_uint8_t r);

#endif /* DIAG_RELATIVE_H */
