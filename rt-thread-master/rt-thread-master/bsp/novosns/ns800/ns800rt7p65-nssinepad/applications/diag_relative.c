/*
 * diag_relative.c - SG/ADC 相对异常检测与主动保护(M8)的实现
 *
 * 单位纪律: 全部判据量都在源自身的码域里 —— SG_RESULT 0..1023, ADC raw 0..4095。
 *   mA 可以显示, 但本模式从不读它, 也不从它推任何安培边界(见 §3.3: 不写死"堵转必涨电流")。
 * 时间纪律: 持续时间一律用 tick 差累计, 不用帧数; SG 10Hz 与 ADC 100Hz 混在帧数里
 *   会把"持续 500ms"变成两个不同长度的假并度。
 * 证据纪律: 一个条件要被记成"持续", 必须真的跨过了 >= min_new_sg 个**独立成功采样**
 *   的 SG(靠 sg_seq 前进证明, 不是靠调用次数), 且时间跨度由这些样本的 tick 张成。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "sensor_service.h"
#include "safety_state.h"
#include "tmc2209.h"
#include "motor.h"
#include "project_config.h"
#include "stop_priority.h"      /* sp_intr_masked: 入口上下文自检 */
#include "diag_relative.h"

/* tick 差: then 在 now 之后(未来样本 / 未初始化 0)按"不可信"上限返回。 */
#define REL_TICK_FUTURE 0x80000000u

rt_uint32_t diag_rel_tick_diff(rt_uint32_t now, rt_uint32_t then)
{
    rt_uint32_t d = (rt_uint32_t)(now - then);
    return (d >= REL_TICK_FUTURE) ? REL_TICK_FUTURE : d;
}

const char *rel_reason_name(rt_uint8_t r)
{
    switch (r)
    {
    case REL_REASON_NONE:        return "NONE";
    case REL_SG_SEVERE:          return "REL_SG_SEVERE";
    case REL_ADC_SEVERE:         return "REL_ADC_SEVERE";
    case REL_FUSED_SEVERE:       return "REL_FUSED_SEVERE";
    case REL_SENSOR_UNAVAILABLE: return "REL_SENSOR_UNAVAILABLE";
    case REL_OUT_OF_WINDOW:      return "REL_OUT_OF_WINDOW";
    case REL_COVERAGE_LOST:      return "REL_COVERAGE_LOST";
    case REL_IMPACT:             return "REL_IMPACT";
    default:                     return "?";
    }
}

const char *rel_cov_name(rt_uint8_t c)
{
    switch (c)
    {
    case RELCOV_OK:             return "COVERED";
    case RELCOV_LOCK_FAIL:      return "NOT-ACTIVE(config-lock)";
    case RELCOV_ABSENT:         return "NOT-ACTIVE(no-profile)";
    case RELCOV_SIG_MISMATCH:   return "NOT-ACTIVE(sig-mismatch)";
    case RELCOV_NOFRAME:        return "NOT-ACTIVE(no-frame)";
    case RELCOV_ENGINE_ERR:     return "NOT-ACTIVE(profile-rejected)";
    case RELCOV_MOTION_UNKNOWN: return "MOTION-UNKNOWN";
    default:                    return "NOT-ACTIVE(?)";
    }
}

/* 相对模式下全部严重原因都要求真实保护动作(单源不冒称双源确认, 也不借用
 * ACTIVE 的 STALL_CONFIRMED 名字)。 */
rt_bool_t rel_reason_is_severe(rt_uint8_t r)
{
    return (r == REL_SG_SEVERE || r == REL_ADC_SEVERE || r == REL_FUSED_SEVERE ||
            r == REL_SENSOR_UNAVAILABLE || r == REL_OUT_OF_WINDOW ||
            r == REL_COVERAGE_LOST || r == REL_IMPACT) ? RT_TRUE : RT_FALSE;
}

/* ---------- 有界在线统计(不在栈上开 N 帧数组) ---------- */

void rel_acc_reset(rel_acc_t *a)
{
    a->n = 0; a->min = 0; a->max = 0; a->sum = 0; a->sum_sq = 0;
}

void rel_acc_add(rel_acc_t *a, rt_uint32_t v)
{
    if (a->n == 0) { a->min = v; a->max = v; }
    else
    {
        if (v < a->min) a->min = v;
        if (v > a->max) a->max = v;
    }
    a->sum += v;
    a->sum_sq += (rt_uint64_t)v * (rt_uint64_t)v;
    a->n++;
}

rt_bool_t rel_acc_mean_x1000(const rel_acc_t *a, rt_uint32_t *out)
{
    if (a == RT_NULL || out == RT_NULL || a->n == 0) return RT_FALSE;
    *out = (rt_uint32_t)((a->sum * 1000ull) / (rt_uint64_t)a->n);
    return RT_TRUE;
}

/* ---------- profile 校验(逐条具名, 不用宏遮蔽) ---------- */

static rt_err_t prof_reject(const char **why, const char *name)
{
    if (why) *why = name;
    return -RT_EINVAL;
}

static rt_bool_t mean_in_band(rt_uint32_t mean_x1000, rt_uint32_t lo, rt_uint32_t hi)
{
    return (mean_x1000 >= lo * 1000u) && (mean_x1000 <= hi * 1000u);
}

rt_err_t rel_profile_validate(const diag_rprof_t *p, const char **why)
{
    if (why) *why = RT_NULL;
    if (p == RT_NULL) return prof_reject(why, "NULL");
    if (p->magic == 0) return prof_reject(why, "ABSENT(no_profile_committed)");
    if (p->magic != DIAG_RPROF_MAGIC)  return prof_reject(why, "MAGIC");
    if (p->sampling_def != DIAG_RPROF_SAMPLING_DEF) return prof_reject(why, "SAMPLING_DEF");
    if (p->abi != DIAG_RPROF_ABI)      return prof_reject(why, "ABI");
    if ((p->flags & RPF_COMMITTED) == 0) return prof_reject(why, "NOT_COMMITTED");
    if (p->profile_id == 0)            return prof_reject(why, "PROFILE_ID_ZERO");
    if (p->signature == 0)             return prof_reject(why, "SIGNATURE_ZERO");
    if (!(p->hz_low >= 1u && p->hz_low <= p->hz_nominal &&
          p->hz_nominal <= p->hz_high && p->hz_high <= 20000u))
        return prof_reject(why, "SPEED_WINDOW");
    if (p->dir_mask == 0 || (p->dir_mask & ~0x3u) != 0) return prof_reject(why, "DIR_MASK");
    if ((p->phase_mask & ~((1u << MOTOR_ACCEL) | (1u << MOTOR_CRUISE) |
                           (1u << MOTOR_DECEL))) != 0 ||
        (p->phase_mask & RPPH_CRUISE) == 0)
        return prof_reject(why, "PHASE_MASK");
    if (p->zero_max > 4095u || p->run_adc_max > 4095u || p->run_sg_max > 1023u)
        return prof_reject(why, "RANGE_OVER_FLOW");
    if (p->zero_min > p->zero_max || p->run_adc_min > p->run_adc_max ||
        p->run_sg_min > p->run_sg_max)
        return prof_reject(why, "BAND_ORDER");
    if (!mean_in_band(p->zero_mean_x1000, p->zero_min, p->zero_max) ||
        !mean_in_band(p->run_adc_mean_x1000, p->run_adc_min, p->run_adc_max) ||
        !mean_in_band(p->run_sg_mean_x1000, p->run_sg_min, p->run_sg_max))
        return prof_reject(why, "MEAN_OUT_OF_BAND");
    if (p->zero_n < 32u || p->run_adc_n < 8u || p->run_sg_n < 8u)
        return prof_reject(why, "SAMPLE_COUNT");
    if (p->run_adc_noise > 4095u) return prof_reject(why, "NOISE");
    if (!(p->adc_dev_warn >= 0 && (rt_int32_t)p->adc_dev_warn < p->adc_dev_trip) ||
        !(p->sg_drop_warn > 0 && (rt_int32_t)p->sg_drop_warn < p->sg_drop_trip))
        return prof_reject(why, "THRESHOLD_ORDER");
    if (p->adc_dev_trip > 4095 || p->sg_drop_trip > 1023) return prof_reject(why, "THRESHOLD_BOUND");
    /* trip 必须落在可达区间内: SG 掉到 0 仍达不到 trip, 或 ADC 满量程偏离达不到
     * trip, 都说明这套阈值是假的(永远不触发也算"保护有效")。 */
    if ((rt_int32_t)p->sg_drop_trip > (rt_int32_t)p->run_sg_min)
        return prof_reject(why, "SG_TRIP_UNREACHABLE");
    /* ADC 可达性按**运行时真正使用的扩张带**核定(M8-R1 R4.3): 判据用的是
     * [min-noise, max+noise], 可达偏离上限就是这个扩张带两侧的余量; 拿未扩张的
     * 带去校验、执行时又用扩张带, 会产生"永不触发却合格"的 profile。 */
    {
        rt_int32_t blo = (rt_int32_t)p->run_adc_min - (rt_int32_t)p->run_adc_noise;
        rt_int32_t bhi = (rt_int32_t)p->run_adc_max + (rt_int32_t)p->run_adc_noise;
        rt_int32_t rup, rdn;
        if (blo < 0) blo = 0;
        if (bhi > 4095) bhi = 4095;
        rup = 4095 - bhi;
        rdn = blo;
        /* 两端分别看: 堵转**不**保证母线电流上涨, 因此"只有一侧可达"是合法工况,
         * 但两侧都够不到 trip 就是假阈值。 */
        if ((rt_int32_t)p->adc_dev_trip > rup && (rt_int32_t)p->adc_dev_trip > rdn)
            return prof_reject(why, "ADC_TRIP_UNREACHABLE");
    }
    if (!(p->dur_sg_ms >= 100u && p->dur_sg_ms <= 5000u) ||
        !(p->dur_adc_ms >= 50u && p->dur_adc_ms <= 5000u) ||
        !(p->dur_fused_ms >= 50u && p->dur_fused_ms <= 5000u) ||
        !(p->dur_recover_ms >= 50u && p->dur_recover_ms <= 5000u))
        return prof_reject(why, "DURATIONS");
    if (!(p->age_sg_ms >= 100u && p->age_sg_ms <= 2000u) ||
        !(p->age_frame_ms >= 20u && p->age_frame_ms <= 1000u))
        return prof_reject(why, "SOURCE_AGE_LIMITS");
    if (p->min_new_sg < 3u || p->min_new_sg > 64u) return prof_reject(why, "MIN_NEW_SG");
    /* R6 碰撞门: 冲击阈值必须明显高于 ~1g 静止基线(沿用项目 vib_impact_mg 的
     * 1200..8000 口径), 证据数 >=2 个**独立新采样**, 且年龄/窗口都有界。 */
    if (!(p->impact_peak_mg >= 1200u && p->impact_peak_mg <= 8000u))
        return prof_reject(why, "IMPACT_PEAK");
    if (p->impact_new_min < 2u || p->impact_new_min > 16u)
        return prof_reject(why, "IMPACT_NEW_MIN");
    if (!(p->dur_impact_ms >= 20u && p->dur_impact_ms <= 2000u))
        return prof_reject(why, "DUR_IMPACT");
    if (!(p->age_imu_ms >= 20u && p->age_imu_ms <= 1000u))
        return prof_reject(why, "AGE_IMU");
    if (!(p->learn_sg_seq_last >= p->learn_sg_seq_first &&
          (p->learn_sg_seq_last - p->learn_sg_seq_first + 1u) >= p->run_sg_n))
        return prof_reject(why, "PROVENANCE");
    return RT_EOK;
}

/* 只覆盖工况/驱动配置/采样定义身份字段; id/rev/flags/阈值不进签名。 */
rt_uint32_t rel_profile_signature(const diag_rprof_t *p)
{
    rt_uint32_t id[10];
    rt_uint32_t h = 2166136261u, i;
    const rt_uint8_t *b;

    if (p == RT_NULL) return 0;
    id[0] = p->tmc_cfg_state;  id[1] = p->mstep_source;
    id[2] = ((rt_uint32_t)p->tmc_irun << 24) | ((rt_uint32_t)p->tmc_ihold << 16) |
            ((rt_uint32_t)p->tmc_mres << 8)  | (rt_uint32_t)p->tmc_vsense;
    id[3] = p->sampling_def;
    id[4] = 0; id[5] = 0; id[6] = 0; id[7] = 0; id[8] = 0; id[9] = 0;
    b = (const rt_uint8_t *)id;
    for (i = 0; i < sizeof(id); ++i) { h ^= b[i]; h = (h << 5) - h + 0x01000193u; }
    return h ? h : 1u;
}

/* 用**当前**驱动配置与计划参数填一个候选 profile 的身份字段(供比较签名/学习起点)。 */
void rel_live_identity(diag_rprof_t *probe)
{
    tmc_drv_config_t tc;

    if (probe == RT_NULL) return;
    rt_memset(probe, 0, sizeof(*probe));
    tmc2209_get_planned_config(&tc);
    probe->tmc_cfg_state = (rt_uint8_t)tmc2209_get_config_state();
    probe->mstep_source  = tc.mstep_reg_select;
    probe->tmc_irun      = tc.irun;
    probe->tmc_ihold     = tc.ihold;
    probe->tmc_mres      = tc.mres;
    probe->tmc_vsense    = tc.vsense;
    /* 速度窗/方向/阶段由命令显式给出; 采样定义由采集器固定(每个成功样本算一个)。 */
    probe->sampling_def = DIAG_RPROF_SAMPLING_DEF;
}

rt_uint32_t rel_live_signature(void)
{
    diag_rprof_t probe;
    rel_live_identity(&probe);
    return rel_profile_signature(&probe);
}

/* ---------- 引擎 ---------- */

/* 已显式武装却拿不到判据覆盖时, 允许连续失去这么多毫秒仍不动作, 超过就必须降险
 * (M8-R1 R2.1 的"有界容错")。**软件设计参数**, 不是实测物理时延。
 * 故意不放进 profile: profile 本身可能正是丢掉的那个东西, 用它自己的字段当门
 * 等于没有门(也等于"把 profile 删了就关掉保护")。 */
#define REL_COVERAGE_LOST_MS  500u

/* 清"证据类"累计, 但保留**时间类**累计、样本身份、代际与事故 latch。
 *
 * 为什么 age_bad_ms / out_run_ms / cov_lost_ms 必须留住: 这三个计数器本身就是
 * "坏状态已经持续了多久"。离窗/斜坡分支每帧都会调本函数, 早期版本把它们一起抹掉,
 * 于是
 *   - REL_OUT_OF_WINDOW 永远凑不满 dur(每帧都从 0 重新开始), "离开被批准的工况
 *     持续 300ms 就该降险"这条判据在真机上从不成立;
 *   - 源失效(age_bad_ms)同理 —— 而文件头注释却宣称"源失效保护在上面已经查过,
 *     仍然有效", 注释与代码相反。
 *   - M8-R1 新增的 cov_lost_ms 同属这一类: 每清一次就永远到不了降险上界。
 * 三者都由正常路径显式清零(进窗 ⇒ out=0; 数据重新新鲜 ⇒ age=0; 覆盖恢复 ⇒ cov=0),
 * 所以保留它们不会把上一工况的坏时间带进新工况。
 *
 * 为什么 last_*_seq / prev_*_tick 也必须留住: 它们是"这个样本我见过没有"的身份
 * 依据。抹掉 last_sg_seq 会让下一帧把**同一个旧 SG** 重新算成一次新采样, 等于
 * 凭空造证据 —— 那正是本任务要消除的那类假"独立样本数"。
 * gen 是武装代际(命令线程 bump、本线程消费), 更不是"证据", 一律保留。
 * latch 是一次事故的首因, 只有操作员显式重新武装才允许清。 */
void rel_engine_reset_counters(rel_engine_t *e)
{
    rt_uint32_t keep_fs   = e->last_frame_seq;
    rt_uint32_t keep_now  = e->last_now_tick;
    rt_uint8_t  keep_have = e->have_now;
    rt_uint32_t keep_age  = e->age_bad_ms;
    rt_uint32_t keep_out  = e->out_run_ms;
    rt_uint32_t keep_cov  = e->cov_lost_ms;
    rt_uint32_t keep_gen  = e->gen;
    rt_uint32_t keep_sgs  = e->last_sg_seq, keep_crs = e->last_cur_seq, keep_ims = e->last_imu_seq;
    rt_uint32_t keep_sgt  = e->prev_sg_tick, keep_crt = e->prev_cur_tick, keep_imt = e->prev_imu_tick;
    rel_latch_t latch     = e->latch;          /* 一次事故的首次原因不得被抹掉 */

    rt_memset(e, 0, sizeof(*e));
    e->latch          = latch;
    e->last_frame_seq = keep_fs;
    e->last_now_tick  = keep_now;
    e->have_now       = keep_have;
    e->age_bad_ms     = keep_age;
    e->out_run_ms     = keep_out;
    e->cov_lost_ms    = keep_cov;
    e->gen            = keep_gen;
    e->last_sg_seq    = keep_sgs;
    e->last_cur_seq   = keep_crs;
    e->last_imu_seq   = keep_ims;
    e->prev_sg_tick   = keep_sgt;
    e->prev_cur_tick  = keep_crt;
    e->prev_imu_tick  = keep_imt;
    e->verdict        = REL_REASON_NONE;
}

void rel_engine_wipe(rel_engine_t *e)
{
    rt_memset(e, 0, sizeof(*e));
}

/* M8-R2 R2-B2: 新一代开始 = **这一代的判据证据整体作废**。
 * 与 rel_engine_reset_counters() 的区别正是这里要清的东西: 严重状态与活动原因、
 * 两级持续与样本数、三源身份与 prev tick、age/out/cov 累计、latch 与 verdict。
 * 只保留时间基线(last_now_tick/have_now, 否则下一帧的 dt 会算出一个假时长)与代际。
 * 不保留 last_frame_seq/last_*_seq: 保留它们就等于允许"上一代攒完的异常"在新一代
 * 第一帧被兑现(REARM_REUSES_OLD_ENGINE_EVIDENCE 的根因)。 */
void rel_engine_new_generation(rel_engine_t *e, rt_uint32_t gen)
{
    rt_uint32_t keep_now  = e->last_now_tick;
    rt_uint8_t  keep_have = e->have_now;

    rt_memset(e, 0, sizeof(*e));
    e->last_now_tick = keep_now;
    e->have_now      = keep_have;
    e->gen           = gen;
    e->verdict       = REL_REASON_NONE;
}

static rt_uint8_t ramp_phase(rt_uint8_t st)
{
    return (st == (rt_uint8_t)MOTOR_ACCEL || st == (rt_uint8_t)MOTOR_DECEL);
}

static rt_uint8_t motion_active(rt_uint8_t st)
{
    return (st == (rt_uint8_t)MOTOR_ACCEL || st == (rt_uint8_t)MOTOR_CRUISE ||
            st == (rt_uint8_t)MOTOR_DECEL);
}

/* 在批准的巡航工况内? 四个条件全查: 签名(驱动配置+工况+采样定义)、方向、阶段、速度窗。 */
static rt_uint8_t window_ok(const rel_step_arg_t *a)
{
    const diag_input_t *in = &a->in;
    const diag_rprof_t *p = &a->prof;

    if (p->signature == 0 || in->live_signature == 0) return 0;
    if (p->signature != in->live_signature) return 0;
    if ((p->dir_mask & (1u << (in->dir & 1u))) == 0) return 0;
    if ((p->phase_mask & (1u << in->motor_state)) == 0) return 0;
    if (in->step_hz < p->hz_low || in->step_hz > p->hz_high) return 0;
    return 1;
}

/* warn 与 strong 两级**各自独立**的跨度/样本数累计(M8-R1 A1/A2 的修法):
 * 命中就把这一级的时间与独立样本数往前推, 没命中就整级清零重来。
 * 严重条件因此只能用自己攒够的证据触发 —— 之前的中等警告替它填时长/样本数,
 * 就是"第一笔更强样本立刻严重"那个反例的根因。 */
static void ev_pair(rt_uint32_t *run, rt_uint32_t *cnt, rt_uint32_t *first,
                    rt_uint32_t tick, rt_uint8_t hit)
{
    if (!hit) { *run = 0; *cnt = 0; *first = 0; return; }
    if (*cnt == 0) *first = tick;
    (*cnt)++;
    {
        rt_uint32_t span = diag_rel_tick_diff(tick, *first);
        if (span > *run) *run = span;
    }
}

/* 回绕一致的"谁在时间上更靠后"(M8-R1 R1.4): 融合起点原来写成裸 lo > hi, 那在
 * tick 跨过 0 时与 diag_rel_tick_diff 的语义相反 —— 会把起点算到半个周期之前。
 * 现在两路都走同一个回绕语义: 只有正向可达(未过半程)的那一侧才算"在后面"。 */
static rt_uint32_t tick_later(rt_uint32_t a, rt_uint32_t b)
{
    rt_uint32_t ab = diag_rel_tick_diff(a, b);    /* b 之后多久才是 a */
    rt_uint32_t ba = diag_rel_tick_diff(b, a);
    if (ab >= REL_TICK_FUTURE && ba < REL_TICK_FUTURE) return b;
    return a;
}

/* 恢复段: 任一新鲜反向证据都把整段清零(不是只清某一路)。 */
static void ok_clear(rel_engine_t *e)
{
    e->ok_run_ms = 0; e->ok_new_cnt = 0; e->ok_span_first = 0; e->ok_src_seen = 0;
}

/* 一次"新鲜且正常"的采样对恢复段的贡献。返回 1 表示恢复条件已凑满:
 * 跨度 >= dur_recover_ms, 独立正常样本数 >= min_new_sg, **且两路源都各自见过
 * 新鲜正常样本**(ok_src_seen==3)。最后一项就是 R1.3 要的"足够独立的正常证据"。 */
static rt_bool_t ok_tally(rel_engine_t *e, const diag_rprof_t *p, rt_uint32_t now)
{
    if (e->ok_new_cnt == 0) e->ok_span_first = now;
    e->ok_new_cnt++;
    {
        rt_uint32_t span = diag_rel_tick_diff(now, e->ok_span_first);
        if (span > e->ok_run_ms) e->ok_run_ms = span;
    }
    return (e->ok_run_ms >= p->dur_recover_ms && e->ok_new_cnt >= p->min_new_sg &&
            e->ok_src_seen == 3u) ? RT_TRUE : RT_FALSE;
}

rel_reason_t rel_engine_step(rel_engine_t *e, const rel_step_arg_t *a)
{
    const diag_input_t *in = &a->in;
    const diag_rprof_t *p = &a->prof;
    rt_uint32_t now = a->now_tick;
    rt_uint32_t dt = 10u, frame_age, sg_age, adc_age, imu_age, gap, span;
    rt_int32_t band_lo, band_hi, adc_dev = 0, sg_drop = 0;
    rt_uint8_t fresh_sg, fresh_adc, fresh_imu, win, stale, cov, sg_warn_hit, sg_strong_hit;
    rt_uint8_t adc_warn_hit, adc_strong_hit, imp_hit;
    rt_bool_t armed, released = RT_FALSE;

    /* ---- 0) 判据覆盖档位: 调用方给的 + 引擎自己独立复核的 ----
     * 引擎不轻信"OK": profile 为空或被同一套校验拒绝, 一律就地降级成对应非 OK 档。
     * 这样"没有可用判据却继续运行"永远是一条**有界的降险义务**, 而不是静默跳过
     * (M8-R1 B2/B3 的根因就是生产分支非 OK 时直接不步进)。
     * 没有 profile 的 MONITOR_ONLY 仍然只披露、不误触发: 降险义务只在操作员**显式**
     * 开启相对保护(armed)时才存在。 */
    cov = a->coverage;
    if (cov == RELCOV_OK)
    {
        if (p->magic == 0)                                   cov = RELCOV_ABSENT;
        else if (rel_profile_validate(p, RT_NULL) != RT_EOK) cov = RELCOV_ENGINE_ERR;
    }
    /* M8-R2 R2-B3: 外壳帧是新的**不等于**运动事实是新的。运动快照取失败(motion_known=0)
     * 或它已经过期时, 帧里的 motor_state 只是继承值 —— 拿继承来的 IDLE 当"仍静止"
     * 就是无限免保护证据。事实未知一律并入有界降险, 不进任何单源判据。 */
    if (cov == RELCOV_OK)
    {
        if (!in->motion_known)                             cov = RELCOV_MOTION_UNKNOWN;
        else if (diag_rel_tick_diff(now, in->motion_tick) > p->age_frame_ms)
            cov = RELCOV_MOTION_UNKNOWN;
    }
    e->coverage = cov;
    armed = (a->mode == (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION);

    if (e->have_now)
    {
        rt_uint32_t d = diag_rel_tick_diff(now, e->last_now_tick);
        if (d < REL_TICK_FUTURE && d <= 1000u) dt = d;
    }
    e->last_now_tick = now;
    e->have_now = 1;

    if (cov != RELCOV_OK)
    {
        e->cov_lost_ms += dt;
        /* 只有在"证得的静止"下才免降险义务: 取不到运动事实(motion_known=0)时
         * 不知道在不在动, 不能宣称覆盖仍然有效(M8-R1 R2.2)。 */
        if (armed && e->cov_lost_ms >= REL_COVERAGE_LOST_MS &&
            (in->motion_known ? motion_active(in->motor_state) : 1))
        {
            e->verdict = REL_COVERAGE_LOST;
            e->severe_active = 1; e->active_reason = (rt_uint8_t)e->verdict;
            return e->verdict;
        }
        if (e->severe_active && e->active_reason != (rt_uint8_t)REL_REASON_NONE)
        {
            e->verdict = (rel_reason_t)e->active_reason;   /* 已触发: 首因继续可见 */
            return e->verdict;
        }
        e->verdict = REL_REASON_NONE;
        return e->verdict;                                 /* 不宣称任何单源结论 */
    }
    e->cov_lost_ms = 0;

    /* ---- 1) 源年龄: 帧 / SG / ADC 三个源**分别**判, 而且都在帧去重之前 ----
     * M8-R1 B1: 外壳帧 timestamp 在前进**不代表 ADC 又采过一次**。cur_tick 冻结、
     * cur_valid=0、未来 tick(样本比诊断时钟还新)都与 SG 冻结同等对待。
     * IMU 的年龄只影响下面第 8 节的碰撞判据 —— R6 要求"IMU 缺失不得使 SG/ADC
     * 一概不可用", 所以它**不**参与这里的 stale。 */
    frame_age = diag_rel_tick_diff(now, in->timestamp);
    sg_age  = (in->sg_tick  == 0) ? REL_TICK_FUTURE : diag_rel_tick_diff(now, in->sg_tick);
    adc_age = (in->cur_tick == 0) ? REL_TICK_FUTURE : diag_rel_tick_diff(now, in->cur_tick);
    stale = (frame_age > p->age_frame_ms) || (sg_age > p->age_sg_ms) ||
            (adc_age > p->age_frame_ms) || !in->cur_valid || !in->sg_valid;
    if (motion_active(in->motor_state) && stale)
    {
        e->age_bad_ms += dt;
        if (e->age_bad_ms >= p->dur_adc_ms)
        {
            e->verdict = REL_SENSOR_UNAVAILABLE; e->severe_active = 1;
            e->active_reason = (rt_uint8_t)e->verdict;
            return e->verdict;
        }
    }
    else e->age_bad_ms = 0;         /* 静止, 或三个源都又采到了: 时间重新起算 */

    /* ---- 2) 重复帧: 不推进任何统计/证据计数(年龄已判) ---- */
    if (in->seq != 0 && in->seq == e->last_frame_seq) return e->verdict;
    if (in->seq != 0) e->last_frame_seq = in->seq;

    /* ---- 3) 工况门 ---- */
    win = window_ok(a);
    if (!win)
    {
        if (ramp_phase(in->motor_state))
        {
            /* 正常启动加速/计划内减速 = 阶段例外: 只抑制 SG/负载判据, 不报错;
             * 源失效保护在上面已经查过, 仍然有效。 */
            rel_engine_reset_counters(e);
            e->verdict = REL_REASON_NONE;
            return e->verdict;
        }
        if (e->in_window_prev) e->out_run_ms = 0;      /* 刚离窗, 从此刻起算 */
        e->in_window_prev = 0;
        e->out_run_ms += dt;
        rel_engine_reset_counters(e);
        if (motion_active(in->motor_state) && e->out_run_ms >= p->dur_adc_ms)
        {
            e->verdict = REL_OUT_OF_WINDOW; e->severe_active = 1;
            e->active_reason = (rt_uint8_t)e->verdict;
            return e->verdict;
        }
        e->verdict = REL_REASON_NONE;
        return e->verdict;
    }
    e->out_run_ms = 0;
    if (!e->in_window_prev)
    {
        rel_engine_reset_counters(e);     /* 新工况: 上一工况的计数一律不带进来 */
        e->in_window_prev = 1;
    }

    /* ---- 4) 独立样本身份(三源) ---- */
    fresh_sg  = (in->sg_valid  && in->sg_seq  != 0 && in->sg_seq  != e->last_sg_seq);
    fresh_adc = (in->cur_valid && in->cur_seq != 0 && in->cur_seq != e->last_cur_seq);
    fresh_imu = (in->imu_valid && in->imu_seq != 0 && in->imu_seq != e->last_imu_seq);

    /* ---- 5) 判据量(与统计量同单位同定义) ---- */
    band_lo = (rt_int32_t)p->run_adc_min - (rt_int32_t)p->run_adc_noise;
    band_hi = (rt_int32_t)p->run_adc_max + (rt_int32_t)p->run_adc_noise;
    if (band_lo < 0) band_lo = 0;
    if (band_hi > 4095) band_hi = 4095;
    if (fresh_adc)
    {
        rt_int32_t v = (rt_int32_t)in->cur_raw;
        if (v < band_lo) adc_dev = band_lo - v;
        else if (v > band_hi) adc_dev = v - band_hi;
    }
    if (fresh_sg)
    {
        sg_drop = (rt_int32_t)p->run_sg_min - (rt_int32_t)in->sg;
        if (sg_drop < 0) sg_drop = 0;
    }
    sg_warn_hit    = (sg_drop >= (rt_int32_t)p->sg_drop_warn) ? 1u : 0u;
    sg_strong_hit  = (sg_drop >= (rt_int32_t)p->sg_drop_trip) ? 1u : 0u;
    adc_warn_hit   = (adc_dev >= (rt_int32_t)p->adc_dev_warn) ? 1u : 0u;
    adc_strong_hit = (adc_dev >= (rt_int32_t)p->adc_dev_trip) ? 1u : 0u;

    /* ---- 6) SG 条件: 只由新 SG 样本推进; warn 与 strong 两级各自累计 ---- */
    if (fresh_sg)
    {
        /* 间断两端不得凑成连续异常(R1.2): 这次样本与**上一次成功样本**的间隔超过
         * SG 年龄上限, 说明中间那段根本没有 SG 证据, 两级都从头攒。 */
        gap = (e->prev_sg_tick == 0) ? 0u : diag_rel_tick_diff(in->sg_tick, e->prev_sg_tick);
        if (gap > p->age_sg_ms)
        {
            e->sg_run_ms = 0; e->sg_new_cnt = 0; e->sg_span_first = 0;
            e->sg_strong_run_ms = 0; e->sg_strong_cnt = 0; e->sg_strong_span_first = 0;
        }
        e->prev_sg_tick = in->sg_tick;
        e->last_sg_seq  = in->sg_seq;
        ev_pair(&e->sg_run_ms, &e->sg_new_cnt, &e->sg_span_first, in->sg_tick, sg_warn_hit);
        ev_pair(&e->sg_strong_run_ms, &e->sg_strong_cnt, &e->sg_strong_span_first,
                in->sg_tick, sg_strong_hit);
    }
    /* !fresh_sg: 旧样本既不加证也不撤证(它不是"恢复正常"的证据) */

    /* ---- 7) ADC 条件: 与 SG 完全同纪律, 年龄上限用帧/ADC 的那一个 ---- */
    if (fresh_adc)
    {
        gap = (e->prev_cur_tick == 0) ? 0u : diag_rel_tick_diff(in->cur_tick, e->prev_cur_tick);
        if (gap > p->age_frame_ms)
        {
            e->adc_run_ms = 0; e->adc_new_cnt = 0; e->adc_span_first = 0;
            e->adc_strong_run_ms = 0; e->adc_strong_cnt = 0; e->adc_strong_span_first = 0;
        }
        e->prev_cur_tick = in->cur_tick;
        e->last_cur_seq  = in->cur_seq;
        ev_pair(&e->adc_run_ms, &e->adc_new_cnt, &e->adc_span_first, in->cur_tick, adc_warn_hit);
        ev_pair(&e->adc_strong_run_ms, &e->adc_strong_cnt, &e->adc_strong_span_first,
                in->cur_tick, adc_strong_hit);
    }

    /* ---- 8) R6 碰撞: 只认"这一次真的采到了"的 IMU 样本 ----
     * 三条纪律:
     *   - 静止 ~1g 基线**不是**碰撞: 触发门 impact_peak_mg 必须明显高于 1g
     *     (校验限 1200..8000 mg, 沿用项目 vib_impact_mg 的同一口径);
     *   - 每次计数都必须由 imu_seq 前进证明是**一次新采样**。原 DIAG_IMPACT 用的是
     *     16 帧滑窗 vib_peak + 每帧 +/-1 的计数, 一次撞击会把"峰值还在窗里"说成
     *     "又撞了一次" —— 这里不读滑窗, 只读本帧 vib_mg 与新样本身份;
     *   - 计数落在 dur_impact_ms 的**有界窗口**内, 超窗的旧样本先踢出窗口再计数。
     * IMU 这一路不可用(读失败/年龄超限)时只作废冲击判据并如实披露, SG/ADC 判据
     * 照常走 —— 无关源的缺失不得把两个主判据一并抹掉。 */
    imu_age = (in->imu_tick == 0) ? REL_TICK_FUTURE : diag_rel_tick_diff(now, in->imu_tick);
    e->imu_usable = (in->imu_valid && imu_age <= p->age_imu_ms) ? 1u : 0u;
    imp_hit = 0u;
    if (e->imu_usable && fresh_imu)
    {
        imp_hit = ((rt_int32_t)in->vib_mg >= (rt_int32_t)p->impact_peak_mg) ? 1u : 0u;
        gap = (e->prev_imu_tick == 0) ? 0u : diag_rel_tick_diff(in->imu_tick, e->prev_imu_tick);
        if (gap > p->age_imu_ms) { e->imp_cnt = 0; e->imp_run_ms = 0; e->imp_span_first = 0; }
        e->prev_imu_tick = in->imu_tick;
        e->last_imu_seq  = in->imu_seq;
        if (imp_hit)
        {
            if (e->imp_cnt == 0) e->imp_span_first = in->imu_tick;
            span = diag_rel_tick_diff(in->imu_tick, e->imp_span_first);
            if (span > p->dur_impact_ms)     /* 旧样本出窗: 重开一段证据 */
            { e->imp_span_first = in->imu_tick; e->imp_cnt = 0; span = 0; }
            e->imp_cnt++;
            e->imp_run_ms = span;
            if (e->imp_cnt >= p->impact_new_min)
            {
                e->verdict = REL_IMPACT; e->severe_active = 1;
                e->active_reason = (rt_uint8_t)e->verdict;
                return e->verdict;           /* 独立触发, 不要求 SG/ADC 同时异常 */
            }
        }
        else { e->imp_cnt = 0; e->imp_run_ms = 0; e->imp_span_first = 0; }
    }
    /* IMU 这一路暂时不可用(读失败/超龄): **不再新增证据, 也不抹掉已有的一组**。
     * 清零是错的 —— 那会让"这一路没采到"变成一次撤证, 而冲退出窗本来就由
     * dur_impact_ms 的窗口规则负责(下一次新样本落在窗外时自己重开一段)。
     * R6 要求这里只做披露(imu_usable=0), 不得因此影响 SG/ADC 判据。 */

    /* ---- 9) 融合条件: 两个中等异常必须**时间上重叠**且各自凑够独立样本 ----
     * 只读 warn 级证据(sg_run/adc_run), 不读陈旧强证据: 严重级已有自己的计数器。
     * 重叠起点用回绕一致的 tick_later(), 不再是裸 lo > hi。 */
    if (sg_warn_hit && adc_warn_hit && e->sg_span_first != 0 && e->adc_span_first != 0)
    {
        rt_uint32_t start = tick_later(e->sg_span_first, e->adc_span_first);
        e->fused_span_first = start;
        span = diag_rel_tick_diff(now, start);
        if (span > e->fused_run_ms) e->fused_run_ms = span;
    }
    else e->fused_run_ms = 0;

    /* ---- 10) severe 判定(单源不得冒称双源确认) ---- */
    if (sg_strong_hit &&
        e->sg_strong_run_ms >= p->dur_sg_ms && e->sg_strong_cnt >= p->min_new_sg)
    {
        e->verdict = REL_SG_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }
    if (adc_strong_hit &&
        e->adc_strong_run_ms >= p->dur_adc_ms && e->adc_strong_cnt >= 2u)
    {
        e->verdict = REL_ADC_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }
    if (sg_warn_hit && adc_warn_hit &&
        e->fused_run_ms >= p->dur_fused_ms && e->sg_new_cnt >= p->min_new_sg &&
        e->adc_new_cnt >= 2u)
    {
        e->verdict = REL_FUSED_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }

    /* ---- 11) 回落迟滞: 每一路新鲜样本单独记账(M8-R1 R1.3) ----
     * (a) 任一**新鲜反向证据**(新采样仍在警告以上, 或一次新鲜冲击)立刻打断整段
     *     恢复; 反过来, 推进恢复必须**同时**见过两路的新鲜正常样本(ok_src_seen==3)
     *     —— "这一帧恰好不是 fresh_sg+fresh_adc 同时成立"不能成为把中间那次异常
     *     忽略掉的理由。
     * (b) 时间仍按 tick 跨度累计而不是按事件次数加 dt: SG 与 ADC 是 10:1 分频,
     *     每事件加一个帧间 dt 会把 500ms 的恢复窗口拉成约 5s(实测偏差 10 倍)。
     * (c) 没有新证据的帧: 既不累计也不清零; 长期冻结由第 1 节年龄门兜住。 */
    if (fresh_sg)
    {
        if (sg_warn_hit || imp_hit) ok_clear(e);
        else { e->ok_src_seen |= 1u; if (ok_tally(e, p, now)) released = RT_TRUE; }
    }
    if (fresh_adc)
    {
        if (adc_warn_hit || imp_hit) ok_clear(e);
        else { e->ok_src_seen |= 2u; if (ok_tally(e, p, now)) released = RT_TRUE; }
    }
    if (released)
    {
        rt_uint8_t was = e->active_reason;

        /* 只有"真的处于严重"时才有解除这回事。severe_active=0 时这段累计只是
         * "没有观察到异常", 顺手把所有证据组清零是错的: 那会把一次还没成组的冲击
         * 计数抹掉(状态行因此看不见它), 而冲击证据组的出窗本来就由 dur_impact_ms
         * 的窗口规则负责, 不需要恢复路径代劳。 */
        if (e->severe_active)
        {
            e->severe_active = 0;
            e->active_reason = (rt_uint8_t)REL_REASON_NONE;
            e->sg_run_ms = 0; e->adc_run_ms = 0; e->fused_run_ms = 0;
            e->sg_strong_run_ms = 0; e->adc_strong_run_ms = 0;
            e->sg_new_cnt = 0; e->adc_new_cnt = 0;
            e->sg_strong_cnt = 0; e->adc_strong_cnt = 0;
            e->sg_span_first = 0; e->adc_span_first = 0;
            e->sg_strong_span_first = 0; e->adc_strong_span_first = 0;
            /* 只清"被解除的那个原因"自己的证据组: 解除 SG 严重不该顺带把
             * 还没凑够两枚的冲击组也删掉。 */
            if (was == (rt_uint8_t)REL_IMPACT)
            { e->imp_cnt = 0; e->imp_run_ms = 0; e->imp_span_first = 0; }
        }
        ok_clear(e);
    }

    /* ---- 12) 粘性严重原因 ----
     * 触发条件只在"新 SG 样本那一帧"成立(10Hz), 其余 9 帧里 sg_drop 恒为 0。
     * 早期版本在这些帧末尾把 verdict 写回 NONE, 于是 MONITOR_ONLY 下用 diag_status
     * 读"若启用会怎样"有九成概率读到 NORMAL —— 评估证据本身不稳定。
     * 这里在恢复条件凑满 dur_recover_ms 之前保持原原因; 解除路径只有上面 (11),
     * 所以粘性不会掩盖一次真实发生的恢复。 */
    if (e->severe_active && e->active_reason != (rt_uint8_t)REL_REASON_NONE)
    {
        e->verdict = (rel_reason_t)e->active_reason;
        return e->verdict;
    }
    e->verdict = REL_REASON_NONE;
    return e->verdict;
}

/* ---------- 保护请求: 先真实保护, 再留证据(打印只能在这之后) ---------- */

rt_err_t rel_request_protection(rel_engine_t *e, const rel_step_arg_t *a, rt_uint8_t mode)
{
    rt_err_t rc;

    if (e->latch.latched) return RT_EOK;               /* 首次原因不可覆盖 */

    rc = safety_post_event(EVT_MULTI_FAULT);           /* FIRST: 实际短保护 */
    e->latch.latched       = 1;
    e->latch.reason        = (rt_uint8_t)e->verdict;
    e->latch.mode_at_trip  = mode;
    e->latch.post_rc       = (rt_int16_t)rc;           /* 投递失败也留下 pending 事实 */
    e->latch.profile_id    = a->prof.profile_id;
    e->latch.revision      = a->prof.revision;
    e->latch.trig_tick     = a->now_tick;
    e->latch.first_sg_seq  = a->in.sg_seq;
    e->latch.first_cur_seq = a->in.cur_seq;
    e->latch.first_sg_tick = a->in.sg_tick;
    e->latch.first_cur_tick = a->in.cur_tick;
    e->latch.first_imu_seq = a->in.imu_seq;
    /* 首因记的是**引擎复核后**的档位, 不是调用方递交的原始档位: R2-B3 之后"外壳帧
     * 新但运动事实未知"这类降级只有在这里看得见, 记原始值会把降险原因写成 COVERED。 */
    e->latch.coverage_at_trip = e->coverage;
    switch (e->verdict)
    {
    case REL_SG_SEVERE:
        e->latch.value_raw = (rt_int32_t)a->in.sg;
        e->latch.threshold_raw = (rt_int32_t)a->prof.sg_drop_trip;
        e->latch.persisted_ms = e->sg_strong_run_ms;
        e->latch.severe_cnt = (rt_uint8_t)e->sg_strong_cnt;
        break;
    case REL_ADC_SEVERE:
        e->latch.value_raw = (rt_int32_t)a->in.cur_raw;
        e->latch.threshold_raw = (rt_int32_t)a->prof.adc_dev_trip;
        e->latch.persisted_ms = e->adc_strong_run_ms;
        e->latch.severe_cnt = (rt_uint8_t)e->adc_strong_cnt;
        break;
    case REL_FUSED_SEVERE:
        e->latch.value_raw = (rt_int32_t)a->in.sg;
        e->latch.threshold_raw = (rt_int32_t)a->prof.sg_drop_warn;
        e->latch.persisted_ms = e->fused_run_ms;
        e->latch.severe_cnt = (rt_uint8_t)e->sg_new_cnt;
        break;
    case REL_IMPACT:
        e->latch.value_raw = (rt_int32_t)a->in.vib_mg;
        e->latch.threshold_raw = (rt_int32_t)a->prof.impact_peak_mg;
        e->latch.persisted_ms = e->imp_run_ms;
        e->latch.severe_cnt = (rt_uint8_t)e->imp_cnt;
        break;
    case REL_COVERAGE_LOST:
        /* 这里的"值"就是失去覆盖的毫秒数: 保护义务来自"没有判据", 不来自任何量纲。 */
        e->latch.value_raw = (rt_int32_t)e->cov_lost_ms;
        e->latch.threshold_raw = (rt_int32_t)REL_COVERAGE_LOST_MS;
        e->latch.persisted_ms = e->cov_lost_ms;
        e->latch.severe_cnt = 0;
        break;
    case REL_SENSOR_UNAVAILABLE:
        e->latch.value_raw = (rt_int32_t)e->age_bad_ms;
        e->latch.threshold_raw = (rt_int32_t)a->prof.dur_adc_ms;
        e->latch.persisted_ms = e->age_bad_ms;
        e->latch.severe_cnt = 0;
        break;
    default:                    /* REL_OUT_OF_WINDOW */
        e->latch.value_raw = (rt_int32_t)e->out_run_ms;
        e->latch.threshold_raw = (rt_int32_t)a->prof.dur_adc_ms;
        e->latch.persisted_ms = e->out_run_ms;
        e->latch.severe_cnt = 0;
        break;
    }
    return rc;
}

/* ---------- 生产上下文 ---------- */

/* 唯一写者纪律与"初始化≠持有"的合同见 diag_relative.h 的 rel_prod_* 注释块。 */
static rel_engine_t   d_rel_prod;
static struct rt_mutex d_ctl_lock;                  /* 只保护下面三个小结构体 */
static rel_ctl_t       d_ctl;
static rel_incident_t  d_incident;
static rel_state_t     d_pub;                       /* 写者整份发布, 读者整份取回 */
static rt_bool_t       d_pub_have   = RT_FALSE;
static rt_bool_t       d_ctl_inited = RT_FALSE;     /* 只回答"锁初始化成功了吗" */
static rt_uint32_t     d_pub_fail   = 0;            /* 写者私有: 发布失败次数 */

#define REL_CTL_TAKE_MS  20

rt_err_t rel_ctl_init(void)                         /* 由 diagnosis_init 调用一次 */
{
    rt_err_t rc;
    if (d_ctl_inited) return RT_EOK;
    rc = rt_mutex_init(&d_ctl_lock, "rel_ctl", RT_IPC_FLAG_PRIO);
    if (rc == RT_EOK) d_ctl_inited = RT_TRUE;
    return rc;
}

rt_bool_t rel_ctl_ready(void) { return d_ctl_inited; }

/* R2-A.1: take 必须把结果还给调用方。失败**不再**是"以后都不用取锁",
 * 也不再是"这一次无锁写"。 */
rt_err_t rel_ctl_take(void)
{
    if (!d_ctl_inited) return -RT_EINVAL;
    return (rt_mutex_take(&d_ctl_lock, REL_CTL_TAKE_MS) == RT_EOK) ? RT_EOK : -RT_EBUSY;
}

/* 只允许本次 rel_ctl_take() 返回 RT_EOK 的路径调用: 不 release 自己没持有的锁。 */
void rel_ctl_give_held(void) { (void)rt_mutex_release(&d_ctl_lock); }

/* 初始化专用全清: 只能在线程起来之前调用(R2-A.5 —— 运行期"顺手清生产上下文"
 * 不是命令线程的权力, 那是唯一写者的职责)。 */
void rel_prod_init_wipe(void)
{
    rt_memset(&d_ctl, 0, sizeof(d_ctl));
    rt_memset(&d_incident, 0, sizeof(d_incident));
    rt_memset(&d_pub, 0, sizeof(d_pub));
    d_pub_have = RT_FALSE;
    d_pub_fail = 0;
    rel_engine_wipe(&d_rel_prod);
}

/* R2-B1: 模式与代际共同的提交协议。调用方(命令线程)此刻仍持有 motor 静止门;
 * 抢不到控制锁 => 两个字段一个都不动, 并把错误交回命令方去拒绝这次改模式。 */
rt_err_t rel_prod_commit(rt_uint8_t mode, rt_bool_t bump_gen, rt_uint32_t *gen_out)
{
    rt_err_t rc = rel_ctl_take();
    if (rc != RT_EOK) return rc;
    if (mode != (rt_uint8_t)REL_CTL_MODE_KEEP) { d_ctl.mode = mode; d_ctl.mode_valid = RT_TRUE; }
    if (bump_gen) d_ctl.gen_want++;
    if (gen_out) *gen_out = d_ctl.gen_want;
    rel_ctl_give_held();
    return RT_EOK;
}

/* 显式重新武装(命令线程): 只 bump 期望代际。失败时代际完全不变。 */
rt_err_t rel_prod_request_rearm(void)
{
    return rel_prod_commit((rt_uint8_t)REL_CTL_MODE_KEEP, RT_TRUE, RT_NULL);
}

rt_err_t rel_prod_get_ctl(rel_ctl_t *out)
{
    rt_err_t rc;
    if (out == RT_NULL) return -RT_EINVAL;
    rc = rel_ctl_take();
    if (rc != RT_EOK) { rt_memset(out, 0, sizeof(*out)); return rc; }
    *out = d_ctl;
    rel_ctl_give_held();
    return RT_EOK;
}

rt_err_t rel_prod_get_incident(rel_incident_t *out)
{
    rt_err_t rc;
    if (out == RT_NULL) return -RT_EINVAL;
    rc = rel_ctl_take();
    if (rc != RT_EOK) { rt_memset(out, 0, sizeof(*out)); return rc; }
    *out = d_incident;
    /* rearm_required 住在 d_ctl 里(它是"本次武装的票据"状态, 不是事故内容)。
     * 快照必须把它一起带出去, 否则消费者读到"有首因但看起来还能再保护" ——
     * 那正是 C6 那种"界面说还在保护、引擎已不再发票"的镜像错误。 */
    out->rearm_required = d_ctl.rearm_required;
    rel_ctl_give_held();
    return RT_EOK;
}

rt_bool_t rel_prod_get_latch(rel_latch_t *out)
{
    rel_incident_t inc;

    if (out == RT_NULL) return RT_FALSE;
    if (rel_prod_get_incident(&inc) != RT_EOK) return RT_FALSE;   /* 读不到=不可用, 不猜 */
    if (!inc.have_current) return RT_FALSE;
    *out = inc.current;
    return RT_TRUE;
}

rt_bool_t rel_writer_latch(rel_latch_t *out)
{
    if (out == RT_NULL) return RT_FALSE;
    if (!d_rel_prod.latch.latched) return RT_FALSE;
    *out = d_rel_prod.latch;
    return RT_TRUE;
}

/* 由写者把引擎状态收成一份完整快照(不取锁: 引擎只有本线程写)。 */
static void rel_state_collect(const rel_engine_t *e, rel_state_t *s)
{
    s->sg_run_ms         = e->sg_run_ms;
    s->sg_strong_run_ms  = e->sg_strong_run_ms;
    s->adc_run_ms        = e->adc_run_ms;
    s->adc_strong_run_ms = e->adc_strong_run_ms;
    s->fused_run_ms      = e->fused_run_ms;
    s->ok_run_ms         = e->ok_run_ms;
    s->imp_run_ms        = e->imp_run_ms;
    s->age_bad_ms        = e->age_bad_ms;
    s->out_run_ms        = e->out_run_ms;
    s->cov_lost_ms       = e->cov_lost_ms;
    s->sg_new_cnt        = e->sg_new_cnt;
    s->sg_strong_cnt     = e->sg_strong_cnt;
    s->adc_new_cnt       = e->adc_new_cnt;
    s->adc_strong_cnt    = e->adc_strong_cnt;
    s->ok_new_cnt        = e->ok_new_cnt;
    s->imp_cnt           = e->imp_cnt;
    s->last_sg_seq       = e->last_sg_seq;
    s->last_cur_seq      = e->last_cur_seq;
    s->last_imu_seq      = e->last_imu_seq;
    s->imu_usable        = e->imu_usable;
    s->coverage          = e->coverage;
    s->verdict           = (rt_uint8_t)e->verdict;
    s->severe_active     = e->severe_active;
}

/* R2-A.3: 读者要么拿到**整份**已发布快照(带自己的 snap_seq), 要么拿到错误且
 * out 被清零。"标量不会撕裂"不等于"整份一致", 所以这里不再逐字段跨线程拼。 */
rt_err_t rel_prod_get_snapshot(rel_state_t *out)
{
    rt_err_t rc;
    if (out == RT_NULL) return -RT_EINVAL;
    rc = rel_ctl_take();
    if (rc != RT_EOK) { rt_memset(out, 0, sizeof(*out)); return rc; }
    if (!d_pub_have) { rel_ctl_give_held(); rt_memset(out, 0, sizeof(*out)); return -RT_EINVAL; }
    *out = d_pub;
    rel_ctl_give_held();
    return RT_EOK;
}

static void rel_prod_publish(const rel_step_arg_t *a)
{
    rel_state_t s;

    rt_memset(&s, 0, sizeof(s));
    rel_state_collect(&d_rel_prod, &s);
    s.snap_seq     = a->in.seq;
    s.pub_fail_cnt = d_pub_fail;      /* 写者私有计数, 随这一份一起发布 */
    if (rel_ctl_take() == RT_EOK)
    {
        d_pub = s;
        d_pub_have = RT_TRUE;
        /* 发布视图与行为视图在这里对齐: 票据的真值在引擎(唯一写者)手里,
         * d_ctl 只是给人读的副本。 */
        d_ctl.rearm_required = d_rel_prod.rearm_required;
        d_ctl.generation     = d_rel_prod.gen;
        /* R2-A.2: 首因的**权威副本在引擎里**(闩锁一旦落下不可改写), 发布视图只是
         * 它的镜像。锁当时拿不到 => 这一帧镜像不成, 但下一帧仍会重试: 于是"取锁失败
         * 期间记录不到首因"不会变成"首因永久丢失"。新一代的 wipe 同时清引擎闩锁,
         * 所以这里的重试不可能把旧事故冒充成新事故的证据。 */
        if (d_rel_prod.latch.latched && !d_incident.have_current)
        {
            d_incident.current      = d_rel_prod.latch;
            d_incident.have_current = RT_TRUE;
            d_incident.generation   = d_rel_prod.gen;
        }
        rel_ctl_give_held();
    }
    else d_pub_fail++;                /* 只有本线程会加它, 读者只读已发布副本 */
}

/* 代际同步 + R2-B2 的整代证据清零。返回非 RT_EOK 表示"这一帧拿不到一致的控制
 * 状态", 调用方必须按覆盖丢失降险, 不能带着可疑的旧证据继续宣称有效。
 * 清零必须在步进**之前**、由唯一写者自己做: 命令线程只 bump 期望代际, 于是
 * "命令擦掉在途义务"结构上不可能(R3.3), 而"新一代一笔旧帧立刻触发"也不可能(R2-B2)。 */
static rt_err_t rel_prod_sync_generation(rt_uint8_t *mode_out, rt_uint8_t *committed_out)
{
    rt_uint32_t g;
    rt_bool_t need_wipe = RT_FALSE;
    rt_err_t rc = rel_ctl_take();

    if (rc != RT_EOK) return rc;
    g = d_ctl.gen_want;
    if (mode_out) *mode_out = d_ctl.mode_valid ? d_ctl.mode : (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION;
    if (committed_out) *committed_out = d_ctl.mode_valid ? 1u : 0u;
    if (g != d_ctl.generation)
    {
        if (d_rel_prod.latch.latched)
        {
            d_incident.archived = d_rel_prod.latch;   /* 上一轮首因挪进 archived, 不得被本轮覆盖 */
            d_incident.have_archived = RT_TRUE;
        }
        rt_memset(&d_incident.current, 0, sizeof(d_incident.current));
        d_incident.have_current = RT_FALSE;
        d_incident.generation   = g;
        d_ctl.generation        = g;
        d_ctl.rearm_required    = 0;
        need_wipe = RT_TRUE;
    }
    rel_ctl_give_held();
    if (need_wipe) rel_engine_new_generation(&d_rel_prod, g);
    return RT_EOK;
}

/* 相对判据的唯一生产入口: 同步代际 -> 步进 -> 若 severe 则先保护 -> 发布快照 ->
 * 返回原因给调用方(调用方只能在保护之后打印)。 */
rel_reason_t rel_prod_evaluate(const rel_step_arg_t *a, rt_uint8_t mode,
                               rt_err_t *post_rc, rt_bool_t *acted)
{
    rel_step_arg_t ar;
    rel_reason_t r;
    rt_err_t rc = RT_EOK;
    rt_err_t sync;
    rt_bool_t did = RT_FALSE;
    rt_uint8_t ctl_mode = mode, ctl_committed = 0;

    sync = rel_prod_sync_generation(&ctl_mode, &ctl_committed);
    ar = *a;
    if (sync != RT_EOK)
    {
        /* 控制状态读不到: 这一帧不许给出任何单源结论, 走有界降险(RELCOV_LOCK_FAIL)。
         * 保护义务本身不因此消失 —— 已在途的闩锁仍由引擎粘住。 */
        ar.coverage = RELCOV_LOCK_FAIL;
        ar.mode = mode;
    }
    else
    {
        /* R2-B1: 两个模式视图必须**一致**才允许发生保护动作。
         *   - 命令线程 commit 失败 => d_ctl.mode 仍是旧值, 而 d_mode 已按拒绝处理:
         *     不一致 => 不保护(绝不因为"镜像里写着 RELATIVE"就动轴)。
         *   - 触发后的内部降险 commit 失败 => d_mode 退回 MONITOR 而 d_ctl.mode 还是
         *     RELATIVE: 不一致 => 同样不动(票据本来也已消耗, 这里是第二道)。
         * 未提交过(mode_valid=0)时沿用调用方给的值 —— 主机链与初始化前的上下文。 */
        if (ctl_committed && ctl_mode != mode)
            mode = (rt_uint8_t)DIAG_MODE_MONITOR_ONLY;
        ar.mode = mode;
    }

    r = rel_engine_step(&d_rel_prod, &ar);

    if (rel_reason_is_severe((rt_uint8_t)r))
    {
        /* M8-R1 R2.5: 副作用只能由"是否已经锁存/是否相对模式"决定, **绝不**能由
         * 调用方要不要读输出参数决定 —— 旧写法把整个保护调用挂在
         * `if (post_rc)` 里, 传 NULL 就等于悄悄放弃保护。
         * M8-R2 R2-A.2: 票据由引擎私有字段把关(行为侧不依赖控制锁), 摘要发布失败
         * 也不会让"一次武装只保护一次"这件事变得不可靠。 */
        if (!d_rel_prod.latch.latched && !d_rel_prod.rearm_required &&
            mode == (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION)
        {
            d_rel_prod.rearm_required = 1;    /* 先落票据: 这一步不需要任何锁 */
            rc = rel_request_protection(&d_rel_prod, &ar, mode);
            did = RT_TRUE;
            /* 首因镜像与快照在下面的 rel_prod_publish() 里一并发布: 取锁失败只会
             * 让"这一帧的镜像"推迟, 不会让已经执行的保护或引擎里的首因消失。 */
        }
    }
    rel_prod_publish(&ar);
    if (post_rc) *post_rc = rc;
    if (acted)   *acted   = did;
    return r;
}

/* 采集上下文(零点/学习): 只由命令线程使用。 */
typedef struct
{
    rt_uint8_t  active;
    rt_uint8_t  kind;
    rt_uint8_t  tier;             /* 生成阈值用的档位(随 base 一起进入) */
    rt_uint32_t want;
    rt_uint32_t last_cur_seq, last_sg_seq, last_imu_seq;
    rel_acc_t   adc, sg;
    diag_rprof_t base;
    rt_uint32_t sg_seq_first, sg_seq_last;
    rt_uint8_t  window_broken;
} rel_cap_t;

static rel_cap_t d_cap;

/* ---------- 采集(零点 / 学习) ---------- */

rt_err_t rel_cap_start(rt_uint8_t kind, rt_uint32_t want, const diag_rprof_t *base)
{
    if (kind != (rt_uint8_t)REL_CAP_ZERO && kind != (rt_uint8_t)REL_CAP_LEARN)
        return -RT_EINVAL;
    if (want < 8u || want > 256u) return -RT_EINVAL;
    if (d_cap.active) return -RT_EBUSY;

    rt_memset(&d_cap, 0, sizeof(d_cap));
    d_cap.active = 1;
    d_cap.kind = kind;
    d_cap.want = want;
    /* 档位从候选里带进来; 0 或越界(未按档)按默认档 2 走, 保证 learn 总能给出确定结果。 */
    d_cap.tier = (base && base->threshold_tier >= 1u && base->threshold_tier <= 3u)
                 ? base->threshold_tier : 2u;
    rel_acc_reset(&d_cap.adc);
    rel_acc_reset(&d_cap.sg);
    if (base) d_cap.base = *base;
    return RT_EOK;
}

/* 每帧调用(命令线程在拿到新帧时)。只认真样本: 重复帧/旧 SG 一律不计。 */
rt_err_t rel_cap_feed(const diag_input_t *in)
{
    rt_bool_t new_adc, new_sg;

    if (!d_cap.active) return -RT_EPERM;   /* 状态不允许: 没有采集在进行 */
    if (in == RT_NULL) return -RT_EINVAL;

    if (d_cap.kind == (rt_uint8_t)REL_CAP_LEARN)
    {
        rel_step_arg_t a;

        rt_memset(&a, 0, sizeof(a));
        a.in = *in;
        a.prof = d_cap.base;
        a.now_tick = in->timestamp;
        if (d_cap.base.signature != 0 && !window_ok(&a))
        {
            d_cap.window_broken = 1;
            return -RT_EPERM;                       /* 离开工况: 学习失败, 旧 profile 不动 */
        }
    }

    new_adc = (in->cur_valid && in->cur_seq != 0 && in->cur_seq != d_cap.last_cur_seq);
    new_sg  = (in->sg_valid  && in->sg_seq  != 0 && in->sg_seq  != d_cap.last_sg_seq);
    if (new_adc)
    {
        d_cap.last_cur_seq = in->cur_seq;
        rel_acc_add(&d_cap.adc, in->cur_raw);
    }
    if (new_sg)
    {
        d_cap.last_sg_seq = in->sg_seq;
        rel_acc_add(&d_cap.sg, in->sg);
        if (d_cap.sg_seq_first == 0) d_cap.sg_seq_first = in->sg_seq;
        d_cap.sg_seq_last = in->sg_seq;
    }
    if (!new_adc && !new_sg) return RT_EOK;         /* 重放帧: 不推进, 也不算失败 */
    return RT_EOK;
}

rt_bool_t rel_cap_done(void)
{
    if (!d_cap.active) return RT_FALSE;
    if (d_cap.kind == (rt_uint8_t)REL_CAP_ZERO) return (d_cap.adc.n >= d_cap.want) ? RT_TRUE : RT_FALSE;
    return (d_cap.adc.n >= d_cap.want && d_cap.sg.n >= d_cap.want) ? RT_TRUE : RT_FALSE;
}

void rel_cap_abort(void) { rt_memset(&d_cap, 0, sizeof(d_cap)); }

/* ---------- 生成候选 profile ----------
 *
 * M8-R1 R4.1: 固定 32/96 裕量覆盖不了本项目实际出现过的**低 SG 正常区** —— 稳定
 * SG=64 时生成的 drop_trip=96 已经大于"SG 最多只能掉 64"这个码域上限, 于是这套
 * 阈值永不触发, 却还能通过校验并提交。现在按"正常波动 + 码域可达余量"分层生成:
 *   - 余量够: warn/trip 都落在正常波动之上、并按档位张开;
 *   - 余量不够张开整档: 把 trip 压到可达上限、warn 取中点, 仍要求 warn<trip;
 *   - 连一档都塞不进(或正常波动本身就吃掉大半余量): **具名拒绝并给数据**,
 *     而不是把所有低 SG 工况一律拒绝, 也不是删掉可达性检查来过关。
 */
#define REL_TIER_SEP_SG    8u     /* SG 码域(0..1023)里一档的裕量 */
#define REL_TIER_SEP_ADC  32u     /* ADC 码域(0..4095)里一档的裕量 */

/* tier 越大越激进(更容易触发)。下标 1..3, 0 是"未选档"的占位。 */
static const rt_uint8_t k_tier_w[4] = { 0u, 4u, 2u, 1u };
static const rt_uint8_t k_tier_t[4] = { 0u, 8u, 4u, 3u };

/* SG 一路最大可能掉量: 判据是 run_sg_min - sg, sg 最低到 0。 */
static rt_uint32_t rel_sg_headroom(const diag_rprof_t *p)
{
    return (rt_uint32_t)p->run_sg_min;
}

/* ADC 一路按**运行时真正使用的扩张带**[min-noise, max+noise] 算两侧可达偏离。 */
void rel_adc_reach(const diag_rprof_t *p, rt_uint32_t *up, rt_uint32_t *dn)
{
    rt_int32_t blo = (rt_int32_t)p->run_adc_min - (rt_int32_t)p->run_adc_noise;
    rt_int32_t bhi = (rt_int32_t)p->run_adc_max + (rt_int32_t)p->run_adc_noise;
    if (blo < 0) blo = 0;
    if (bhi > 4095) bhi = 4095;
    if (up) *up = (rt_uint32_t)(4095 - bhi);
    if (dn) *dn = (rt_uint32_t)blo;
}

static rt_bool_t tier_ok(rt_uint8_t tier) { return (tier >= 1u && tier <= 3u) ? RT_TRUE : RT_FALSE; }

/* 在一档(tier)的松紧下生成一对 warn/trip。返回 0 = 这个工况分不开。
 * noise 是正常波动跨度, headroom 是该方向的码域可达上限。 */
static rt_bool_t gen_pair(rt_uint32_t noise, rt_uint32_t sep, rt_uint8_t tier,
                          rt_uint32_t headroom, rt_uint32_t *out_w, rt_uint32_t *out_t)
{
    rt_uint32_t w, t;

    if (!tier_ok(tier) || sep == 0u) return RT_FALSE;
    if (headroom < noise + sep) return RT_FALSE;   /* 连一档都塞不进: 无可分空间 */
    if (noise * 2u >= headroom) return RT_FALSE;   /* 正常波动已吃掉大半余量 */
    w = noise + (rt_uint32_t)k_tier_w[tier] * sep;
    t = noise + (rt_uint32_t)k_tier_t[tier] * sep;
    if (t > headroom) t = headroom;                /* 贴到可达上限, 不再要求永不触发的阈值 */
    if (w >= t) w = noise + ((t - noise) / 2u);
    if (w < noise + 1u) w = noise + 1u;
    if (!(w >= 1u && w < t && t <= headroom)) return RT_FALSE;
    *out_w = w; *out_t = t;
    return RT_TRUE;
}

rt_err_t rel_profile_gen_thresholds(diag_rprof_t *p, rt_uint8_t tier, const char **why)
{
    rt_uint32_t sw, st, aw, at, rup, rdn, ahead;
    rt_uint32_t sg_noise = (rt_uint32_t)p->run_sg_max - (rt_uint32_t)p->run_sg_min;
    rt_uint32_t adc_noise = (rt_uint32_t)p->run_adc_noise;

    if (why) *why = RT_NULL;
    if (!tier_ok(tier)) { if (why) *why = "TIER_RANGE"; return -RT_EINVAL; }

    if (!gen_pair(sg_noise, REL_TIER_SEP_SG, tier, rel_sg_headroom(p), &sw, &st))
    {
        if (why) *why = "SG_HEADROOM_INSUFFICIENT";
        return -RT_EPERM;
    }
    rel_adc_reach(p, &rup, &rdn);
    ahead = (rup > rdn) ? rup : rdn;      /* 两端分别可达即可: 不假设堵转必涨母线电流 */
    if (!gen_pair(adc_noise, REL_TIER_SEP_ADC, tier, ahead, &aw, &at))
    {
        if (why) *why = "ADC_HEADROOM_INSUFFICIENT";
        return -RT_EPERM;
    }
    p->sg_drop_warn   = (rt_uint16_t)sw;
    p->sg_drop_trip   = (rt_uint16_t)st;
    p->adc_dev_warn   = (rt_int16_t)aw;
    p->adc_dev_trip   = (rt_int16_t)at;
    p->threshold_tier = tier;
    return RT_EOK;
}

rt_err_t rel_cap_finish(diag_rprof_t *out, const char **why)
{
    rt_uint32_t am, sm;

    if (why) *why = RT_NULL;
    if (!d_cap.active || out == RT_NULL) return -RT_EPERM;
    if (d_cap.window_broken) { if (why) *why = "WINDOW_BROKEN"; return -RT_EPERM; }

    rt_memset(out, 0, sizeof(*out));
    *out = d_cap.base;
    out->magic = DIAG_RPROF_MAGIC;
    out->abi   = DIAG_RPROF_ABI;

    if (!rel_acc_mean_x1000(&d_cap.adc, &am))
    { if (why) *why = "ADC_ACCUM_EMPTY"; return -RT_EINVAL; }
    if (d_cap.kind == (rt_uint8_t)REL_CAP_ZERO)
    {
        out->zero_mean_x1000 = am;
        out->zero_min  = (rt_uint16_t)d_cap.adc.min;
        out->zero_max  = (rt_uint16_t)d_cap.adc.max;
        out->zero_n    = (rt_uint16_t)d_cap.adc.n;
    }
    else
    {
        if (!rel_acc_mean_x1000(&d_cap.sg, &sm))
        { if (why) *why = "SG_ACCUM_EMPTY"; return -RT_EINVAL; }
        out->run_adc_mean_x1000 = am;
        out->run_adc_min   = (rt_uint16_t)d_cap.adc.min;
        out->run_adc_max   = (rt_uint16_t)d_cap.adc.max;
        /* noise 就是这个带在运行时向两侧扩张的量, 可达性与判据用的是同一个数。 */
        out->run_adc_noise = (rt_uint16_t)(d_cap.adc.max - d_cap.adc.min);
        out->run_adc_n     = (rt_uint16_t)d_cap.adc.n;
        out->run_sg_mean_x1000 = sm;
        out->run_sg_min = (rt_uint16_t)d_cap.sg.min;
        out->run_sg_max = (rt_uint16_t)d_cap.sg.max;
        out->run_sg_n   = (rt_uint16_t)d_cap.sg.n;
        out->learn_sg_seq_first = d_cap.sg_seq_first;
        out->learn_sg_seq_last  = d_cap.sg_seq_last;
        if (rel_profile_gen_thresholds(out, d_cap.tier, why) != RT_EOK)
            return -RT_EPERM;
    }
    /* 学习完成 != 现场验证: 自动生成的阈值一律 FIELD_UNVERIFIED。 */
    out->flags &= (rt_uint16_t)~RPF_FIELD_VERIFIED;
    return RT_EOK;
}

/* ================= MSH: diag_profile =================
 *
 *   diag_profile show
 *   diag_profile zero N              N=32..256  静止且可信禁能, 采 N 个**不同**有效 ADC 帧
 *   diag_profile learn N LO HI DIR   N=8..32 DIR=0|1  指定巡航窗内采 N 个独立 SG + N 个 ADC
 *   diag_profile tier T              T=1..3   用已存正常统计按档重算 warn/trip(只改候选)
 *   diag_profile commit              静止事务内一次性发布整套候选
 *   diag_profile clear CONFIRM       静止作废 profile, 回 MONITOR_ONLY
 *
 * 事务纪律(M8-R1 R3, 逐条对应被复现的反例):
 *   - 每个入口**先**查合法线程上下文(ISR / PRIMASK 屏蔽窗口 / 无线程对象一律静默拒绝,
 *     连参数错误都不打印), 再解析参数、再取锁 —— 沿用 current_cal.c 的同一惯例(R3.7)。
 *   - zero/learn 都在**私有候选**上工作, 并在入口立刻作废这次尝试的成功凭据; 只有
 *     完整成功才发布 pending。失败的重学因此不能复用上一轮的 learn 资格, 更不能把
 *     新窗口套在旧统计上(C4)。
 *   - zero 的静止/禁能资格在采集期间**每帧**复核, 完成时再在同一个静止事务里复核
 *     一次; 中途 arm/运动/失去证明就立即 abort 本次候选, 不会把运动 raw 吸进零点(C5)。
 *   - commit/tier/clear 的最终资格复核全部发生在 motor_calibration_static_begin()
 *     之内(该门本身已检查 armed=0/IDLE/hz=0/SAFETY_READY/停机协调器空闲/PWM 已关
 *     且输出不活动/DRV_ENABLE 为低 = 可信禁能)。**入口先读一份快照不能替代最终
 *     复核**(C2 的旧实现就是用分离快照代替了这道门)。
 *   - learn 只在 MONITOR_ONLY 下进行, 且只允许单向基线(本轮 DIR=2 具名拒绝 ——
 *     只采一个方向却宣称双向基线, 是第二条假证据链)。
 *   - 等待只用 rt_thread_mdelay + 12s 总 deadline; 全程不跨 UART/Flash/打印/等待采样
 *     持 mot_lock 或 cfg_lock; 打印一律放在静止事务关闭之后。
 * 参数严格解析: 非纯数字、越界、多余参数一律拒绝且**不产生任何配置副作用**。
 */

static diag_rprof_t d_pending;              /* 未发布候选(不落 Flash, 重启即失) */
static rt_bool_t    d_pending_zero  = RT_FALSE;
static rt_bool_t    d_pending_learn = RT_FALSE;

#define REL_CMD_DEADLINE_MS 12000u
#define REL_TIER_DEFAULT    2u

/* 合法上下文: 与 current_cal.c 的 cc_ctx_ok() 同一判据、同一惯例。 */
static rt_bool_t rel_ctx_ok(void)
{
    return (rt_interrupt_get_nest() == 0 && !sp_intr_masked() &&
            rt_thread_self() != RT_NULL) ? RT_TRUE : RT_FALSE;
}

/* 严格十进制解析: 只允许 1..9 位数字, 且落在 [lo, hi]。 */
static rt_bool_t rel_parse_u32(const char *s, rt_uint32_t lo, rt_uint32_t hi, rt_uint32_t *out)
{
    rt_uint32_t v = 0;
    int n = 0;

    if (s == RT_NULL || *s == 0) return RT_FALSE;
    for (; *s; ++s, ++n)
    {
        if (*s < '0' || *s > '9') return RT_FALSE;
        if (n >= 9) return RT_FALSE;
        v = v * 10u + (rt_uint32_t)(*s - '0');
    }
    if (v < lo || v > hi) return RT_FALSE;
    *out = v;
    return RT_TRUE;
}

static rt_bool_t rel_static_and_ready(void)
{
    motor_snapshot_t ms;

    if (motor_get_snapshot(&ms) != RT_EOK) return RT_FALSE;
    if (ms.state != MOTOR_IDLE || ms.armed != 0 || ms.current_hz != 0 || ms.target_hz != 0)
        return RT_FALSE;
    if (safety_state_get() != SAFETY_READY) return RT_FALSE;
    return RT_TRUE;
}

/* 源健康(资格用): 必须已经有一帧, 且 SG/ADC 两路都标着有效。
 * IMU 只披露不进门 —— R6 要求无关源的缺失不得把两个主判据一并抹掉。 */
static rt_bool_t rel_sources_present(void)
{
    sensor_frame_t f;

    if (sensor_service_get_latest(&f) != RT_EOK || f.seq == 0) return RT_FALSE;
    return (f.valid_sg && f.valid_current) ? RT_TRUE : RT_FALSE;
}

/* 静止事务: 打开它=证得"静止 + 可信禁能 + 停机协调器空闲"。
 * 锁顺序(M8-R1 R3.3 记录): mot(静止事务) -> cfg。事务内只允许取 cfg_lock 这一层,
 * 且 cfg 的临界区里没有 UART/Flash/打印/mdelay; 反向(cfg -> mot)在本模块不存在。 */
static rt_err_t rel_txn_open(const char *who)
{
    rt_err_t e = motor_calibration_static_begin();

    if (e != RT_EOK)
        rt_kprintf("[DIAG] %s REFUSED: static txn rc=%d (idle+trusted-disable not proven)\n",
                   who, (int)e);
    return e;
}
static void rel_txn_close(void) { motor_calibration_static_end(); }

/* 把最新一帧转成诊断输入的一份**一致**副本(单一快照, 不逐字段跨线程拼)。 */
static rt_bool_t rel_frame_to_input(diag_input_t *in, rt_uint32_t *seen_seq)
{
    sensor_frame_t f;

    if (sensor_service_get_latest(&f) != RT_EOK) return RT_FALSE;
    if (f.seq == 0 || f.seq == *seen_seq) return RT_FALSE;
    *seen_seq = f.seq;

    in->seq            = f.seq;
    in->timestamp      = f.timestamp;
    in->sg_valid       = f.valid_sg;
    in->sg             = f.sg_result;
    in->cur_valid      = f.valid_current;
    in->current_ma     = f.current_ma;
    in->imu_valid      = f.valid_imu;
    in->vib_mg         = f.vib_mg;
    in->motor_state    = f.motor_state;
    in->step_hz        = f.step_hz;
    in->sg_seq         = f.sg_seq;
    in->sg_tick        = f.sg_tick;
    in->cur_seq        = f.cur_seq;
    in->cur_tick       = f.cur_tick;
    in->cur_raw        = f.current_raw;
    in->imu_seq        = f.imu_seq;
    in->imu_tick       = f.imu_tick;
    in->dir            = f.dir;
    /* 取到帧本身就是一次成功的运动事实读取(帧里的 motor_state 来自 motor 快照)。 */
    in->motion_known   = 1;
    in->live_signature = rel_live_signature();
    return RT_TRUE;
}

static void rel_show_one(const char *tag, const diag_rprof_t *p)
{
    const char *why = "?";
    rt_uint32_t rup = 0, rdn = 0;

    if (p->magic == 0) { rt_kprintf("[DIAG] %s: none\n", tag); return; }
    rel_adc_reach(p, &rup, &rdn);
    rt_kprintf("[DIAG] %s id=%u rev=%u abi=%u flg=%04X %s\n", tag,
               (unsigned)p->profile_id, (unsigned)p->revision, (unsigned)p->abi,
               (unsigned)p->flags,
               rel_profile_validate(p, &why) == RT_EOK ? "VALID" : why);
    rt_kprintf("[DIAG] %s win hz=%u[%u..%u] dir=%u ph=%u tmc=%u/%u/%u/%u mst=%u def=%u\n",
               tag, (unsigned)p->hz_nominal, (unsigned)p->hz_low, (unsigned)p->hz_high,
               p->dir_mask, p->phase_mask, (unsigned)p->tmc_cfg_state,
               (unsigned)p->tmc_irun, (unsigned)p->tmc_ihold, (unsigned)p->tmc_vsense,
               (unsigned)p->mstep_source, (unsigned)p->sampling_def);
    rt_kprintf("[DIAG] %s zero n=%u [%u..%u] mean=%u\n", tag,
               (unsigned)p->zero_n, (unsigned)p->zero_min, (unsigned)p->zero_max,
               (unsigned)p->zero_mean_x1000);
    rt_kprintf("[DIAG] %s adc n=%u [%u..%u] nz=%u sg n=%u [%u..%u] seq=%u..%u\n",
               tag, (unsigned)p->run_adc_n, (unsigned)p->run_adc_min,
               (unsigned)p->run_adc_max, (unsigned)p->run_adc_noise,
               (unsigned)p->run_sg_n, (unsigned)p->run_sg_min, (unsigned)p->run_sg_max,
               (unsigned)p->learn_sg_seq_first, (unsigned)p->learn_sg_seq_last);
    rt_kprintf("[DIAG] %s thr dev=%d/%d drop=%u/%u tier=%u reach=%u/%u\n", tag,
               (int)p->adc_dev_warn, (int)p->adc_dev_trip,
               (unsigned)p->sg_drop_warn, (unsigned)p->sg_drop_trip,
               (unsigned)p->threshold_tier, (unsigned)rup, (unsigned)rdn);
    rt_kprintf("[DIAG] %s dur=%u/%u/%u rec=%u age=%u/%u/%u mns=%u\n", tag,
               (unsigned)p->dur_sg_ms, (unsigned)p->dur_adc_ms, (unsigned)p->dur_fused_ms,
               (unsigned)p->dur_recover_ms, (unsigned)p->age_sg_ms,
               (unsigned)p->age_frame_ms, (unsigned)p->age_imu_ms,
               (unsigned)p->min_new_sg);
    rt_kprintf("[DIAG] %s imp peak=%u mg n=%u win=%u age=%u\n", tag,
               (unsigned)p->impact_peak_mg, (unsigned)p->impact_new_min,
               (unsigned)p->dur_impact_ms, (unsigned)p->age_imu_ms);
}

static void rel_cmd_zero(rt_uint32_t n_req)
{
    diag_input_t in;
    diag_rprof_t cand;
    rt_uint32_t deadline, seen = 0;
    const char *why = RT_NULL;
    rt_err_t e, fin;

    if (!rel_static_and_ready())
    {
        rt_kprintf("[DIAG] zero REFUSED: not static+READY (no capture started)\n");
        return;
    }
    /* 私有候选: 本次尝试在它上面攒, 成功才发布(R3.5)。入口先作废这次尝试的凭据 ——
     * "zero 后直接 commit"必须带着 learn=FALSE 被拒, 失败重学也不能拿旧 learn 资格
     * 去发布新窗口(C4)。 */
    rt_memset(&cand, 0, sizeof(cand));
    rel_live_identity(&cand);
    cand.phase_mask     = RPPH_CRUISE;
    cand.dir_mask       = 0u;            /* 方向由 learn 明确给出, 零点不猜 */
    cand.sampling_def   = DIAG_RPROF_SAMPLING_DEF;
    cand.threshold_tier = d_pending.threshold_tier ? d_pending.threshold_tier
                                                   : REL_TIER_DEFAULT;
    d_pending_learn = RT_FALSE;
    d_pending_zero  = RT_FALSE;
    /* 签名此刻就能算(只覆盖驱动配置/采样定义身份, 不含速度窗): 有了它, 学习阶段
     * 才有"是否还在被批准的工况里"的判据 —— rel_cap_feed 的窗口检查在
     * base.signature==0 时是**关闭**的, 不在这里算就等于没有门禁。 */
    cand.signature = rel_profile_signature(&cand);
    if (rel_cap_start((rt_uint8_t)REL_CAP_ZERO, n_req, &cand) != RT_EOK)
    {
        rt_kprintf("[DIAG] zero REFUSED: another capture is active\n");
        return;
    }

    deadline = rt_tick_get() + REL_CMD_DEADLINE_MS;
    rt_memset(&in, 0, sizeof(in));
    for (;;)
    {
        /* 采集期间同样要静止/禁能: 中途起转或失去证明 ⇒ 本次候选立即作废(C5)。 */
        if (!rel_static_and_ready())
        {
            rel_cap_abort();
            rt_kprintf("[DIAG] zero VOIDED: motion/arm/proof lost during capture\n");
            return;
        }
        if (rel_frame_to_input(&in, &seen)) (void)rel_cap_feed(&in);
        if (rel_cap_done()) break;
        if ((rt_int32_t)(deadline - rt_tick_get()) <= 0) break;
        rt_thread_mdelay(5);
    }
    if (!rel_cap_done())
    {
        rel_cap_abort();
        rt_kprintf("[DIAG] zero FAILED: 12s timeout, need %u distinct valid ADC frames\n",
                   (unsigned)n_req);
        return;
    }
    /* 完成时: 在同一静止事务里做最终资格复核, 通过才发布候选。 */
    e = rel_txn_open("zero");
    if (e != RT_EOK) { rel_cap_abort(); return; }
    fin = rel_cap_finish(&cand, &why);
    if (fin == RT_EOK)
    {
        d_pending = cand;
        d_pending_zero = RT_TRUE;
    }
    rel_cap_abort();
    rel_txn_close();
    if (fin != RT_EOK)
    { rt_kprintf("[DIAG] zero FAILED: %s\n", why ? why : "accumulator rejected"); return; }
    rt_kprintf("[DIAG] zero OK n=%u raw %u..%u mean=%u (cal source untouched)\n",
               (unsigned)d_pending.zero_n, (unsigned)d_pending.zero_min,
               (unsigned)d_pending.zero_max, (unsigned)d_pending.zero_mean_x1000);
}

static void rel_cmd_learn(rt_uint32_t n_req, rt_uint32_t lo, rt_uint32_t hi, rt_uint32_t dir)
{
    diag_input_t in;
    diag_rprof_t cand;
    rt_uint32_t deadline, seen = 0;
    const char *why = RT_NULL;
    rt_err_t feed, fin;

    if (dir > 1u)
    {
        /* 本轮只允许单向基线(R3.6): 两个方向要各自学一次、各存一版。
         * 只采一个方向却打 dir_mask=3 会对外宣称"双向都验过", 那是第二条假证据链。 */
        rt_kprintf("[DIAG] learn REFUSED: DIR=2 not supported this round (learn 0 or 1)\n");
        return;
    }
    if (!d_pending_zero)
    { rt_kprintf("[DIAG] learn REFUSED: run 'diag_profile zero N' first\n"); return; }
    /* 学习期间保护必须是关着的: 否则"正在学习的判据"与"正在保护的判据"是两套状态,
     * 而操作员看到的只有一个 mode。 */
    if (diagnosis_get_mode() != DIAG_MODE_MONITOR_ONLY)
    { rt_kprintf("[DIAG] learn REFUSED: mode must be MONITOR_ONLY\n"); return; }

    cand = d_pending;                    /* 私有候选: 从已验证的零点接着攒 */
    d_pending_learn = RT_FALSE;          /* 先作废这次尝试的凭据(C4) */
    cand.hz_low     = lo;
    cand.hz_high    = hi;
    cand.dir_mask   = (rt_uint32_t)(1u << dir);
    cand.signature  = rel_profile_signature(&cand);
    if (rel_cap_start((rt_uint8_t)REL_CAP_LEARN, n_req, &cand) != RT_EOK)
    { rt_kprintf("[DIAG] learn REFUSED: another capture is active\n"); return; }

    deadline = rt_tick_get() + REL_CMD_DEADLINE_MS;
    rt_memset(&in, 0, sizeof(in));
    for (;;)
    {
        if (rel_frame_to_input(&in, &seen))
        {
            feed = rel_cap_feed(&in);
            if (feed != RT_EOK)
            {
                rel_cap_abort();
                rt_kprintf("[DIAG] learn FAILED: left the approved window or source lost\n");
                rt_kprintf("[DIAG] learn last: st=%d hz=%u dir=%u sig=%u sg_seq=%u\n",
                           (int)in.motor_state, (unsigned)in.step_hz, (unsigned)in.dir,
                           (unsigned)in.live_signature, (unsigned)in.sg_seq);
                return;
            }
        }
        if (rel_cap_done()) break;
        if ((rt_int32_t)(deadline - rt_tick_get()) <= 0) break;
        rt_thread_mdelay(5);
    }
    if (!rel_cap_done())
    {
        rel_cap_abort();
        rt_kprintf("[DIAG] learn FAILED: 12s timeout, need %u independent SG and ADC samples\n",
                   (unsigned)n_req);
        return;
    }
    fin = rel_cap_finish(&cand, &why);
    rel_cap_abort();
    if (fin != RT_EOK)
    {
        rt_kprintf("[DIAG] learn REFUSED: %s\n", why ? why : "accumulator");
        rt_kprintf("[DIAG] learn data: sg[%u..%u] adc[%u..%u] nz=%u tier=%u\n",
                   (unsigned)cand.run_sg_min, (unsigned)cand.run_sg_max,
                   (unsigned)cand.run_adc_min, (unsigned)cand.run_adc_max,
                   (unsigned)cand.run_adc_noise, (unsigned)cand.threshold_tier);
        return;
    }
    d_pending = cand;
    d_pending_learn = RT_TRUE;
    rt_kprintf("[DIAG] learn OK adc[%u..%u]n%u nz%u sg[%u..%u]n%u seq%u..%u\n",
               (unsigned)d_pending.run_adc_min, (unsigned)d_pending.run_adc_max,
               (unsigned)d_pending.run_adc_n, (unsigned)d_pending.run_adc_noise,
               (unsigned)d_pending.run_sg_min, (unsigned)d_pending.run_sg_max,
               (unsigned)d_pending.run_sg_n,
               (unsigned)d_pending.learn_sg_seq_first,
               (unsigned)d_pending.learn_sg_seq_last);
    rt_kprintf("[DIAG] learn thr tier=%u dev=%d/%d drop=%u/%u FIELD_UNVERIFIED\n",
               (unsigned)d_pending.threshold_tier, (int)d_pending.adc_dev_warn,
               (int)d_pending.adc_dev_trip, (unsigned)d_pending.sg_drop_warn,
               (unsigned)d_pending.sg_drop_trip);
}

/* 按档重算候选阈值(M8-R1 R4.1/R4.4): 只用**已存的正常统计**, 不引入新数据源、
 * 不加新配置框架; 静止事务内改, 失败一个字段都不动。 */
static void rel_cmd_tier(rt_uint32_t tier)
{
    diag_rprof_t cand;
    const char *why = RT_NULL;
    rt_uint32_t rup = 0, rdn = 0;
    rt_err_t e;

    if (!d_pending_zero || !d_pending_learn)
    { rt_kprintf("[DIAG] tier REFUSED: pending incomplete (zero=%d learn=%d)\n",
                 (int)d_pending_zero, (int)d_pending_learn); return; }
    if (rel_txn_open("tier") != RT_EOK) return;
    cand = d_pending;
    e = rel_profile_gen_thresholds(&cand, (rt_uint8_t)tier, &why);
    if (e == RT_EOK) d_pending = cand;
    rel_txn_close();

    rel_adc_reach(&cand, &rup, &rdn);
    if (e != RT_EOK)
    {
        rt_kprintf("[DIAG] tier %u REFUSED: %s\n", (unsigned)tier, why ? why : "?");
        rt_kprintf("[DIAG] tier data: sg[%u..%u] adc[%u..%u] nz=%u room up=%u dn=%u\n",
                   (unsigned)cand.run_sg_min, (unsigned)cand.run_sg_max,
                   (unsigned)cand.run_adc_min, (unsigned)cand.run_adc_max,
                   (unsigned)cand.run_adc_noise, (unsigned)rup, (unsigned)rdn);
        return;
    }
    rt_kprintf("[DIAG] tier %u: dev=%d/%d drop=%u/%u FIELD_UNVERIFIED (commit to publish)\n",
               (unsigned)tier, (int)d_pending.adc_dev_warn, (int)d_pending.adc_dev_trip,
               (unsigned)d_pending.sg_drop_warn, (unsigned)d_pending.sg_drop_trip);
}

/* 提交借用既有静止事务门做**最终**资格复核; 校验不过 ⇒ 一个字段都不写。 */
static void rel_cmd_commit(void)
{
    const char *why = "?";
    diag_rprof_t cand, cur;
    rt_uint32_t live;
    rt_bool_t src_ok;
    rt_err_t fin;

    if (!d_pending_zero || !d_pending_learn)
    {
        rt_kprintf("[DIAG] commit REFUSED: pending incomplete (zero=%d learn=%d)\n",
                   (int)d_pending_zero, (int)d_pending_learn);
        return;
    }
    if (rel_txn_open("commit") != RT_EOK) return;

    /* 事务内的最终复核(入口那份快照不能替代): 静止+禁能已由 begin 证明, 这里再查
     * 配置可取、live 身份未漂移、源健康、整套阈值自洽。 */
    fin = project_config_get_rprof(&cur);
    if (fin != RT_EOK) { fin = -RT_EIO; why = "config-lock"; goto out; }
    if (!rel_static_and_ready()) { fin = -RT_EPERM; why = "not-static"; goto out; }
    if (!rel_sources_present())  { fin = -RT_EPERM; why = "source-unhealthy"; goto out; }
    cand = d_pending;
    cand.magic = DIAG_RPROF_MAGIC;
    cand.abi   = DIAG_RPROF_ABI;
    cand.flags = (rt_uint16_t)((cand.flags & ~RPF_FIELD_VERIFIED) | RPF_COMMITTED | RPF_FROM_LEARN);
    cand.profile_id = cur.profile_id ? cur.profile_id + 1u : 1u;
    cand.revision   = cur.revision   ? cur.revision   + 1u : 1u;
    /* 设计参数(任务书 §3.2 的初值, 非实测物理上界)。 */
    cand.dur_sg_ms = 500; cand.dur_adc_ms = 300; cand.dur_fused_ms = 300;
    cand.dur_recover_ms = 500; cand.age_sg_ms = 350; cand.age_frame_ms = 100;
    cand.min_new_sg = 3;
    cand.sampling_def = DIAG_RPROF_SAMPLING_DEF;
    cand.hz_nominal = (cand.hz_low + cand.hz_high) / 2u;
    /* R6 碰撞门沿用项目既有振动阈值来源, 不新造一套学习系统。 */
    {
        project_config_t pc;
        if (project_config_get_snapshot(&pc) == RT_EOK)
            cand.impact_peak_mg = (rt_uint16_t)pc.vib_impact_mg;
        else
            cand.impact_peak_mg = 2500u;   /* 与 cfg_fill_defaults 同一初值 */
    }
    cand.impact_new_min = 2; cand.dur_impact_ms = 100; cand.age_imu_ms = 100;
    cand.signature = rel_profile_signature(&cand);
    live = rel_live_signature();
    src_ok = rel_static_and_ready();
    if (!src_ok) { fin = -RT_EPERM; why = "not-static-2"; goto out; }
    if (cand.signature != live)
    { fin = -RT_EPERM; why = "drive-config-drifted"; goto out; }
    if (rel_profile_validate(&cand, &why) != RT_EOK) { fin = -RT_EINVAL; goto out; }
    fin = project_config_set_rprof(&cand);
    if (fin == RT_EOK) d_pending = cand;

out:
    rel_txn_close();
    if (fin != RT_EOK)
    { rt_kprintf("[DIAG] commit REFUSED: (%s) rc=%d\n", why, (int)fin); return; }
    rt_kprintf("[DIAG] commit OK id=%u rev=%u flags=%04X FIELD_UNVERIFIED\n",
               (unsigned)cand.profile_id, (unsigned)cand.revision, (unsigned)cand.flags);
    rt_kprintf("[DIAG] commit next: config_save to persist, then arm explicitly\n");
}

static void rel_cmd_clear(rt_bool_t confirmed)
{
    rt_err_t e, fin;

    if (!confirmed)
    { rt_kprintf("[DIAG] clear needs: diag_profile clear CONFIRM\n"); return; }
    if (rel_txn_open("clear") != RT_EOK) return;
    fin = project_config_clear_rprof();
    rel_txn_close();
    if (fin != RT_EOK) { rt_kprintf("[DIAG] clear FAILED rc=%d\n", (int)fin); return; }

    d_pending_zero = RT_FALSE;
    d_pending_learn = RT_FALSE;
    rt_memset(&d_pending, 0, sizeof(d_pending));
    /* 关保护也是"人工模式改变": 走同一道运动门(静止事务刚刚证得静止, 这里必定过)。 */
    e = diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    rt_kprintf("[DIAG] profile voided; mode=%s (fault state and logs untouched)\n",
               e == RT_EOK ? "MONITOR_ONLY" : "REFUSED-CHANGE");
}

static void diag_profile(int argc, char **argv)
{
    diag_rprof_t live;
    rt_uint32_t n, lo, hi, dir;

    /* R3.7: 上下文检查在**任何**参数解析、取锁与打印之前 —— 非法上下文静默拒绝,
     * 连"用法错误"都不打印(ISR 里打印本身就是第二条故障路径)。 */
    if (!rel_ctx_ok()) return;

    if (argc >= 2 && rt_strcmp(argv[1], "show") == 0 && argc == 2)
    {
        rt_uint32_t sig = rel_live_signature();

        live = d_pending;
        if (project_config_get_rprof(&live) != RT_EOK)
        { rt_kprintf("[DIAG] show: config lock failed\n"); return; }
        rel_show_one("profile", &live);
        rel_show_one("pending", &d_pending);
        rt_kprintf("[DIAG] live sig=%u match=%s cred zero=%d learn=%d\n",
                   (unsigned)sig,
                   (live.magic && live.signature == sig) ? "YES" : "NO",
                   (int)d_pending_zero, (int)d_pending_learn);
        return;
    }
    if (argc == 3 && rt_strcmp(argv[1], "zero") == 0)
    {
        if (!rel_parse_u32(argv[2], 32, 256, &n))
        { rt_kprintf("[DIAG] zero N must be 32..256\n"); return; }
        rel_cmd_zero(n);
        return;
    }
    if (argc == 6 && rt_strcmp(argv[1], "learn") == 0)
    {
        if (!rel_parse_u32(argv[2], 8, 32, &n) || !rel_parse_u32(argv[3], 1, 20000, &lo) ||
            !rel_parse_u32(argv[4], 1, 20000, &hi) || !rel_parse_u32(argv[5], 0, 1, &dir) ||
            lo > hi)
        { rt_kprintf("[DIAG] learn args: N=8..32 LO HI(LO<=HI<=20000) DIR=0|1\n"); return; }
        rel_cmd_learn(n, lo, hi, dir);
        return;
    }
    if (argc == 3 && rt_strcmp(argv[1], "tier") == 0)
    {
        if (!rel_parse_u32(argv[2], 1, 3, &n))
        { rt_kprintf("[DIAG] tier T must be 1..3\n"); return; }
        rel_cmd_tier(n);
        return;
    }
    if (argc == 2 && rt_strcmp(argv[1], "commit") == 0)
    { rel_cmd_commit(); return; }
    if (argc == 3 && rt_strcmp(argv[1], "clear") == 0)
    { rel_cmd_clear(rt_strcmp(argv[2], "CONFIRM") == 0); return; }

    rt_kprintf("usage: diag_profile show|zero N|learn N LO HI DIR|tier T|commit|clear CONFIRM\n");
}
MSH_CMD_EXPORT(diag_profile, relative protection profile show/zero/learn/tier/commit/clear);