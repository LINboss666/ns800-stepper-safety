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
#define DIAG_RPROF_ABI     1u

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
    rt_uint16_t age_sg_ms, age_frame_ms, min_new_sg;
    rt_uint8_t  sampling_def;      /* = DIAG_RPROF_SAMPLING_DEF, 见下 */
    rt_uint8_t  rsv8;

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
} rel_reason_t;

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
    rt_int32_t  value_raw;            /* 触发时的量(SG 或 ADC raw) */
    rt_int32_t  threshold_raw;
    rt_uint32_t persisted_ms;         /* 该条件已持续的毫秒 */
} rel_latch_t;

/* 判定用的一致输入(由调用方在同一临界区取好后传入; 引擎内部不取锁不做 I/O) */
typedef struct
{
    diag_input_t in;          /* 含 sg_seq/sg_tick/cur_seq/cur_tick/cur_raw */
    diag_rprof_t prof;        /* 一致快照副本 */
    rt_uint32_t  now_tick;    /* 诊断当前 tick(与帧 tick 必须分开) */
} rel_step_arg_t;

/* 相对引擎上下文(可实例化: 生产一份, 自检/主机测试各自栈上私有) */
typedef struct
{
    rt_uint32_t last_frame_seq;         /* 已消费帧号(重复帧不推进统计, 但年龄仍判) */
    rt_uint32_t last_sg_seq, last_cur_seq;
    rt_uint32_t last_now_tick;
    rt_uint8_t  have_now;
    rt_uint32_t age_bad_ms;             /* 源年龄超限的连续毫秒 */
    rt_uint32_t out_run_ms;             /* 持续离开被批准工况的毫秒 */
    rt_uint32_t sg_run_ms, adc_run_ms, fused_run_ms, ok_run_ms;
    rt_uint32_t ok_span_first;            /* 干净证据段的起始 tick */
    rt_uint32_t sg_new_cnt, adc_new_cnt, ok_new_cnt;
    rt_uint32_t sg_span_first, adc_span_first, fused_span_first;
    rt_uint8_t  in_window_prev;
    rt_uint8_t  severe_active;
    rt_uint8_t  active_reason;            /* 导致 severe_active 的那次原因(粘住用) */
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

/* ---------- 生产上下文(只由 Diagnosis Thread 推进; 测试请用私有上下文) ---------- */
void rel_prod_wipe(void);                        /* 全清: 只允许人工清故障时调用 */
/* acted: 本次调用是否真的执行了保护请求(供调用方在**保护之后**打印首次摘要) */
rel_reason_t rel_prod_evaluate(const rel_step_arg_t *a, rt_uint8_t mode,
                                  rt_err_t *post_rc, rt_bool_t *acted);
rt_bool_t    rel_prod_get_latch(rel_latch_t *out);
void         rel_prod_get_state(rt_uint32_t *sg_run_ms, rt_uint32_t *adc_run_ms,
                                 rt_uint32_t *fused_run_ms, rt_uint32_t *age_bad_ms,
                                 rt_uint32_t *out_run_ms, rt_uint32_t *ok_run_ms,
                                 rt_uint32_t *sg_new_cnt, rt_uint32_t *adc_new_cnt);
/* 运行期用现场驱动配置/工况算 live 身份并填入候选 profile 的身份字段(供签名比较)。 */
void      rel_live_identity(diag_rprof_t *probe);
/* 上面两者的合成: 当前驱动配置/采样定义的 live 签名(0 表示取不到身份)。
 * Diagnosis Thread 每帧调用它来判"profile 是否仍属于当前工况"。 */
rt_uint32_t rel_live_signature(void);
/* 学习/零点累计器(生产上下文持有, 命令通过它写入)。 */
typedef enum { REL_CAP_ZERO = 0, REL_CAP_LEARN = 1 } rel_cap_kind_t;
rt_err_t  rel_cap_start(rt_uint8_t kind, rt_uint32_t want, const diag_rprof_t *base);
rt_err_t  rel_cap_feed(const diag_input_t *in);         /* 每帧调用(去重后), 只认真样本 */
rt_bool_t rel_cap_done(void);
rt_err_t  rel_cap_finish(diag_rprof_t *out);             /* 样本不足/重叠失败即具名拒绝 */
void      rel_cap_abort(void);


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
