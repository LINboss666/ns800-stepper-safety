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
    default:                     return "?";
    }
}

rt_bool_t rel_reason_is_severe(rt_uint8_t r)
{
    return (r == REL_SG_SEVERE || r == REL_ADC_SEVERE || r == REL_FUSED_SEVERE ||
            r == REL_SENSOR_UNAVAILABLE || r == REL_OUT_OF_WINDOW) ? RT_TRUE : RT_FALSE;
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
    if ((rt_int32_t)p->adc_dev_trip > 4095 - (rt_int32_t)p->run_adc_max &&
        (rt_int32_t)p->adc_dev_trip > (rt_int32_t)p->run_adc_min)
        return prof_reject(why, "ADC_TRIP_UNREACHABLE");
    if (!(p->dur_sg_ms >= 100u && p->dur_sg_ms <= 5000u) ||
        !(p->dur_adc_ms >= 50u && p->dur_adc_ms <= 5000u) ||
        !(p->dur_fused_ms >= 50u && p->dur_fused_ms <= 5000u) ||
        !(p->dur_recover_ms >= 50u && p->dur_recover_ms <= 5000u))
        return prof_reject(why, "DURATIONS");
    if (!(p->age_sg_ms >= 100u && p->age_sg_ms <= 2000u) ||
        !(p->age_frame_ms >= 20u && p->age_frame_ms <= 1000u))
        return prof_reject(why, "SOURCE_AGE_LIMITS");
    if (p->min_new_sg < 3u || p->min_new_sg > 64u) return prof_reject(why, "MIN_NEW_SG");
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

/* 清"证据类"累计, 但保留**时间类**累计与事故 latch。
 *
 * 为什么 age_bad_ms / out_run_ms 必须留住: 这两个计数器本身就是"坏状态已经持续了
 * 多久"。离窗/斜坡分支每帧都会调本函数, 早期版本把它们一起抹掉, 于是
 *   - REL_OUT_OF_WINDOW 永远凑不满 dur(每帧都从 0 重新开始), "离开被批准的工况
 *     持续 300ms 就该降险"这条判据在真机上从不成立;
 *   - 源失效(age_bad_ms)同理 —— 而文件头注释却宣称"源失效保护在上面已经查过,
 *     仍然有效", 注释与代码相反。
 * 二者都由正常路径显式清零(进窗 ⇒ out=0; 数据重新新鲜 ⇒ age=0), 所以保留它们
 * 不会把上一工况的坏时间带进新工况。last_now_tick/have_now 是时钟基准, 同理保留。 */
void rel_engine_reset_counters(rel_engine_t *e)
{
    rt_uint32_t keep_fs   = e->last_frame_seq;
    rt_uint32_t keep_now  = e->last_now_tick;
    rt_uint8_t  keep_have = e->have_now;
    rt_uint32_t keep_age  = e->age_bad_ms;
    rt_uint32_t keep_out  = e->out_run_ms;
    rel_latch_t latch     = e->latch;          /* 一次事故的首次原因不得被抹掉 */

    rt_memset(e, 0, sizeof(*e));
    e->latch          = latch;
    e->last_frame_seq = keep_fs;
    e->last_now_tick  = keep_now;
    e->have_now       = keep_have;
    e->age_bad_ms     = keep_age;
    e->out_run_ms     = keep_out;
    e->verdict        = REL_REASON_NONE;
}

void rel_engine_wipe(rel_engine_t *e)
{
    rt_memset(e, 0, sizeof(*e));
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

rel_reason_t rel_engine_step(rel_engine_t *e, const rel_step_arg_t *a)
{
    const diag_input_t *in = &a->in;
    const diag_rprof_t *p = &a->prof;
    rt_uint32_t now = a->now_tick;
    rt_uint32_t dt = 10u, frame_age, sg_age, span;
    rt_int32_t band_lo, band_hi, adc_dev = 0, sg_drop = 0;
    rt_uint8_t fresh_sg, fresh_adc, win, stale;

    if (rel_profile_validate(p, RT_NULL) != RT_EOK)
    {
        e->verdict = REL_REASON_NONE;      /* 无有效 profile: 不宣称任何覆盖 */
        return e->verdict;
    }

    if (e->have_now)
    {
        rt_uint32_t d = diag_rel_tick_diff(now, e->last_now_tick);
        if (d < REL_TICK_FUTURE && d <= 1000u) dt = d;
    }
    e->last_now_tick = now;
    e->have_now = 1;

    /* ---- 1) 源年龄: 必须在帧去重 return 之前判, 否则重放同一帧永远不超时 ---- */
    frame_age = diag_rel_tick_diff(now, in->timestamp);
    sg_age = (in->sg_tick == 0) ? REL_TICK_FUTURE : diag_rel_tick_diff(now, in->sg_tick);
    stale = (frame_age > p->age_frame_ms) || (sg_age > p->age_sg_ms) ||
            !in->cur_valid || !in->sg_valid;
    if (motion_active(in->motor_state) && stale)
    {
        e->age_bad_ms += dt;
        if (e->age_bad_ms >= p->dur_adc_ms)
        {
            e->verdict = REL_SENSOR_UNAVAILABLE;
            return e->verdict;
        }
    }
    else if (!motion_active(in->motor_state)) e->age_bad_ms = 0;
    else if (!stale) e->age_bad_ms = 0;

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
            e->verdict = REL_OUT_OF_WINDOW;
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

    /* ---- 4) 独立样本身份 ---- */
    fresh_sg  = (in->sg_valid  && in->sg_seq  != 0 && in->sg_seq  != e->last_sg_seq);
    fresh_adc = (in->cur_valid && in->cur_seq != 0 && in->cur_seq != e->last_cur_seq);

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

    /* ---- 6) SG 条件: 只由新 SG 样本推进; 跨度由样本 tick 张成 ---- */
    if (fresh_sg)
    {
        if (e->sg_new_cnt == 0) e->sg_span_first = in->sg_tick;
        e->last_sg_seq = in->sg_seq;
        e->sg_new_cnt++;
        if (sg_drop >= (rt_int32_t)p->sg_drop_warn)
        {
            span = diag_rel_tick_diff(in->sg_tick, e->sg_span_first);
            if (span > e->sg_run_ms) e->sg_run_ms = span;
        }
        else { e->sg_run_ms = 0; e->sg_new_cnt = 0; e->sg_span_first = 0; }
    }
    /* !fresh_sg: 旧样本既不加证也不撤证(它不是"恢复正常"的证据) */

    /* ---- 7) ADC 条件 ---- */
    if (fresh_adc)
    {
        if (e->adc_new_cnt == 0) e->adc_span_first = in->cur_tick;
        e->last_cur_seq = in->cur_seq;
        e->adc_new_cnt++;
        if (adc_dev >= p->adc_dev_warn)
        {
            span = diag_rel_tick_diff(in->cur_tick, e->adc_span_first);
            if (span > e->adc_run_ms) e->adc_run_ms = span;
        }
        else { e->adc_run_ms = 0; e->adc_new_cnt = 0; e->adc_span_first = 0; }
    }

    /* ---- 8) 融合条件: 两个中等异常必须**时间上重叠**且各自凑够独立样本 ---- */
    if (sg_drop >= (rt_int32_t)p->sg_drop_warn && adc_dev >= p->adc_dev_warn)
    {
        rt_uint32_t lo = e->sg_span_first, hi = e->adc_span_first;
        rt_uint32_t start = (lo > hi) ? lo : hi;          /* 重叠起点 = 较晚的那个 */
        if (e->fused_run_ms == 0) e->fused_span_first = start;
        span = diag_rel_tick_diff(now, start);
        if (span > e->fused_run_ms) e->fused_run_ms = span;
    }
    else e->fused_run_ms = 0;

    /* ---- 9) severe 判定(单源不得冒称双源确认) ---- */
    if (sg_drop >= (rt_int32_t)p->sg_drop_trip &&
        e->sg_run_ms >= p->dur_sg_ms && e->sg_new_cnt >= p->min_new_sg)
    {
        e->verdict = REL_SG_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }
    if (adc_dev >= p->adc_dev_trip &&
        e->adc_run_ms >= p->dur_adc_ms && e->adc_new_cnt >= 2)
    {
        e->verdict = REL_ADC_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }
    if (sg_drop >= (rt_int32_t)p->sg_drop_warn && adc_dev >= p->adc_dev_warn &&
        e->fused_run_ms >= p->dur_fused_ms && e->sg_new_cnt >= p->min_new_sg &&
        e->adc_new_cnt >= 2)
    {
        e->verdict = REL_FUSED_SEVERE; e->severe_active = 1;
        e->active_reason = (rt_uint8_t)e->verdict;
        return e->verdict;
    }

    /* ---- 10) 回落迟滞: 恢复必须是"新样本证明的正常", 而且按 tick 跨度计 ----
     * (a) 为什么要有 fresh 门: sg_drop/adc_dev 在 !fresh_* 时恒为 0, 若不加门, SG
     *     冻结在 10Hz 分频下会把 ok_run_ms 一路加满并解除 severe —— 而"最后一个 SG
     *     样本仍然超限"这件事根本没有被推翻。两个量都必须带来新样本才允许累计恢复;
     *     无新证据时保持(既不累计也不清零), 长期冻结由年龄门 REL_SENSOR_UNAVAILABLE 兜。
     * (b) 为什么按跨度而不是按次数加 dt: SG 与 ADC 是 10:1 分频, "成对新鲜"的帧只有
     *     1/10, 每事件加一个帧间 dt 会把 500ms 的恢复窗口拉成约 5s(实测偏差 10 倍),
     *     那是把"恢复慢"当成保守 —— 实际是把严重状态粘在系统上不放。 */
    if (fresh_sg && fresh_adc)
    {
        if (sg_drop < (rt_int32_t)p->sg_drop_warn && adc_dev < p->adc_dev_warn)
        {
            if (e->ok_new_cnt == 0) e->ok_span_first = now;
            e->ok_new_cnt++;
            span = diag_rel_tick_diff(now, e->ok_span_first);
            if (span > e->ok_run_ms) e->ok_run_ms = span;
            if (e->ok_run_ms >= p->dur_recover_ms && e->ok_new_cnt >= p->min_new_sg)
            {
                e->severe_active = 0;
                e->active_reason = (rt_uint8_t)REL_REASON_NONE;
                e->sg_run_ms = 0; e->adc_run_ms = 0; e->fused_run_ms = 0;
                e->sg_new_cnt = 0; e->adc_new_cnt = 0;
                e->sg_span_first = 0; e->adc_span_first = 0;
            }
        }
        else
        {
            e->ok_run_ms = 0; e->ok_new_cnt = 0; e->ok_span_first = 0;
        }
    }

    /* ---- 11) 粘性严重原因 ----
     * 触发条件只在"新 SG 样本那一帧"成立(10Hz), 其余 9 帧里 sg_drop 恒为 0。
     * 早期版本在这些帧末尾把 verdict 写回 NONE, 于是 MONITOR_ONLY 下用 diag_status
     * 读"若启用会怎样"有九成概率读到 NORMAL —— 评估证据本身不稳定。
     * 这里在恢复条件凑满 dur_recover_ms 之前保持原原因; 解除路径只有上面 (10),
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
    e->latch.value_raw = (e->verdict == REL_SG_SEVERE || e->verdict == REL_FUSED_SEVERE)
                         ? (rt_int32_t)a->in.sg : (rt_int32_t)a->in.cur_raw;
    e->latch.threshold_raw = (e->verdict == REL_SG_SEVERE)  ? (rt_int32_t)a->prof.sg_drop_trip :
                             (e->verdict == REL_ADC_SEVERE) ? (rt_int32_t)a->prof.adc_dev_trip :
                             (e->verdict == REL_FUSED_SEVERE) ? (rt_int32_t)a->prof.sg_drop_warn : 0;
    e->latch.persisted_ms = (e->verdict == REL_SG_SEVERE)   ? e->sg_run_ms :
                            (e->verdict == REL_ADC_SEVERE)  ? e->adc_run_ms :
                            (e->verdict == REL_FUSED_SEVERE) ? e->fused_run_ms :
                            (e->verdict == REL_SENSOR_UNAVAILABLE) ? e->age_bad_ms : e->out_run_ms;
    return rc;
}

/* ---------- 生产上下文 ---------- */

static rel_engine_t d_rel_prod;

/* 采集上下文(零点/学习): 只由命令线程使用。 */
typedef struct
{
    rt_uint8_t  active;
    rt_uint8_t  kind;
    rt_uint32_t want;
    rt_uint32_t last_cur_seq, last_sg_seq;
    rel_acc_t   adc, sg;
    diag_rprof_t base;
    rt_uint32_t sg_seq_first, sg_seq_last;
    rt_uint8_t  window_broken;
} rel_cap_t;

static rel_cap_t d_cap;

void rel_prod_wipe(void)
{
    rel_engine_wipe(&d_rel_prod);
}

rt_bool_t rel_prod_get_latch(rel_latch_t *out)
{
    if (out == RT_NULL) return RT_FALSE;
    if (!d_rel_prod.latch.latched) return RT_FALSE;
    *out = d_rel_prod.latch;
    return RT_TRUE;
}

void rel_prod_get_state(rt_uint32_t *sg_run, rt_uint32_t *adc_run, rt_uint32_t *fused_run,
                        rt_uint32_t *age_bad, rt_uint32_t *out_run, rt_uint32_t *ok_run,
                        rt_uint32_t *sg_new, rt_uint32_t *adc_new)
{
    if (sg_run)   *sg_run   = d_rel_prod.sg_run_ms;
    if (adc_run)  *adc_run  = d_rel_prod.adc_run_ms;
    if (fused_run)*fused_run= d_rel_prod.fused_run_ms;
    if (age_bad)  *age_bad  = d_rel_prod.age_bad_ms;
    if (out_run)  *out_run  = d_rel_prod.out_run_ms;
    if (ok_run)   *ok_run   = d_rel_prod.ok_run_ms;
    if (sg_new)   *sg_new   = d_rel_prod.sg_new_cnt;
    if (adc_new)  *adc_new  = d_rel_prod.adc_new_cnt;
}

/* 相对判据的唯一生产入口: 步进 -> 若 severe 则先保护 -> 返回原因给调用方打印。 */
rel_reason_t rel_prod_evaluate(const rel_step_arg_t *a, rt_uint8_t mode,
                               rt_err_t *post_rc, rt_bool_t *acted)
{
    rel_reason_t r = rel_engine_step(&d_rel_prod, a);

    if (post_rc) *post_rc = RT_EOK;
    if (acted)   *acted   = RT_FALSE;
    if (!rel_reason_is_severe((rt_uint8_t)r)) return r;
    if (d_rel_prod.latch.latched) return r;         /* 已锁存: 不重复 post, 也不覆盖首次原因 */
    if (mode != (rt_uint8_t)DIAG_MODE_RELATIVE_PROTECTION) return r;  /* 只有相对模式动作 */
    if (post_rc) *post_rc = rel_request_protection(&d_rel_prod, a, mode);
    if (acted)   *acted   = RT_TRUE;
    return r;
}

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

/* 生成候选 profile(阈值 = 正常带 + 保守裕量), 一律标 FIELD_UNVERIFIED。 */
#define REL_MARGIN_ADC_RAW   32
#define REL_MARGIN_SG_DROP   32
#define REL_TRIP_MULTI       3

rt_err_t rel_cap_finish(diag_rprof_t *out)
{
    rt_uint32_t am, sm;

    if (!d_cap.active || out == RT_NULL) return -RT_EPERM;
    if (d_cap.window_broken) return -RT_EPERM;

    rt_memset(out, 0, sizeof(*out));
    *out = d_cap.base;
    out->magic = DIAG_RPROF_MAGIC;
    out->abi   = DIAG_RPROF_ABI;

    if (!rel_acc_mean_x1000(&d_cap.adc, &am)) { rel_cap_abort(); return -RT_EINVAL; }
    if (d_cap.kind == (rt_uint8_t)REL_CAP_ZERO)
    {
        out->zero_mean_x1000 = am;
        out->zero_min  = (rt_uint16_t)d_cap.adc.min;
        out->zero_max  = (rt_uint16_t)d_cap.adc.max;
        out->zero_n    = (rt_uint16_t)d_cap.adc.n;
    }
    else
    {
        if (!rel_acc_mean_x1000(&d_cap.sg, &sm)) { rel_cap_abort(); return -RT_EINVAL; }
        if (!rel_acc_mean_x1000(&d_cap.adc, &am)) { rel_cap_abort(); return -RT_EINVAL; }
        out->run_adc_mean_x1000 = am;
        out->run_adc_min   = (rt_uint16_t)d_cap.adc.min;
        out->run_adc_max   = (rt_uint16_t)d_cap.adc.max;
        out->run_adc_noise = (rt_uint16_t)(d_cap.adc.max - d_cap.adc.min);
        out->run_adc_n     = (rt_uint16_t)d_cap.adc.n;
        out->run_sg_mean_x1000 = sm;
        out->run_sg_min = (rt_uint16_t)d_cap.sg.min;
        out->run_sg_max = (rt_uint16_t)d_cap.sg.max;
        out->run_sg_n   = (rt_uint16_t)d_cap.sg.n;
        out->learn_sg_seq_first = d_cap.sg_seq_first;
        out->learn_sg_seq_last  = d_cap.sg_seq_last;
        /* 保守裕量生成的待验证阈值: 未见过真实异常样本, 因此只能是 UNVERIFIED。 */
        out->adc_dev_warn = (rt_int16_t)(out->run_adc_noise + REL_MARGIN_ADC_RAW);
        out->adc_dev_trip = (rt_int16_t)(out->run_adc_noise + REL_TRIP_MULTI * REL_MARGIN_ADC_RAW);
        out->sg_drop_warn = (rt_uint16_t)REL_MARGIN_SG_DROP;
        out->sg_drop_trip = (rt_uint16_t)(REL_TRIP_MULTI * REL_MARGIN_SG_DROP);
    }
    rel_cap_abort();
    out->flags &= (rt_uint8_t)~RPF_FIELD_VERIFIED;   /* 学习完成 != 现场验证 */
    return RT_EOK;
}

/* ================= MSH: diag_profile =================
 *
 *   diag_profile show
 *   diag_profile zero N              N=32..256  静止且可信禁能, 采 N 个**不同**有效 ADC 帧
 *   diag_profile learn N LO HI DIR   N=8..32    指定巡航窗内采 N 个独立 SG + N 个 ADC
 *   diag_profile commit               静止时一次性发布整套候选
 *   diag_profile clear CONFIRM        静止作废 profile, 回 MONITOR_ONLY
 *
 * 等待只用 rt_thread_mdelay + 12s 总 deadline; 全程不持 mot_lock/cfg_lock;
 * 复用已有 Sensor 帧, 不开第二个 ADC 句柄、不发额外 SG 读、不加探针命令。
 * 参数严格解析: 非纯数字、越界、多余参数一律拒绝且**不产生任何配置副作用**。
 */

static diag_rprof_t d_pending;              /* 未发布候选(不落 Flash, 重启即失) */
static rt_bool_t    d_pending_zero  = RT_FALSE;
static rt_bool_t    d_pending_learn = RT_FALSE;

#define REL_CMD_DEADLINE_MS 12000u

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
    in->dir            = f.dir;
    in->live_signature = rel_live_signature();
    return RT_TRUE;
}

static void rel_show_one(const char *tag, const diag_rprof_t *p)
{
    const char *why = "?";

    if (p->magic == 0) { rt_kprintf("[DIAG] %s: none\n", tag); return; }
    rt_kprintf("[DIAG] %s id=%u rev=%u abi=%u flags=%04X %s\n", tag,
               (unsigned)p->profile_id, (unsigned)p->revision, (unsigned)p->abi,
               (unsigned)p->flags,
               rel_profile_validate(p, &why) == RT_EOK ? "VALID" : why);
    rt_kprintf("[DIAG] %s win hz=%u[%u..%u] dir=%u phase=%u tmc=%u/%u/%u/%u mstep=%u def=%u\n",
               tag, (unsigned)p->hz_nominal, (unsigned)p->hz_low, (unsigned)p->hz_high,
               p->dir_mask, p->phase_mask, (unsigned)p->tmc_cfg_state,
               (unsigned)p->tmc_irun, (unsigned)p->tmc_ihold, (unsigned)p->tmc_vsense,
               (unsigned)p->mstep_source, (unsigned)p->sampling_def);
    rt_kprintf("[DIAG] %s zero n=%u [%u..%u] mean=%u\n", tag,
               (unsigned)p->zero_n, (unsigned)p->zero_min, (unsigned)p->zero_max,
               (unsigned)p->zero_mean_x1000);
    rt_kprintf("[DIAG] %s adc n=%u [%u..%u] noise=%u sg n=%u [%u..%u] seq=%u..%u\n",
               tag, (unsigned)p->run_adc_n, (unsigned)p->run_adc_min,
               (unsigned)p->run_adc_max, (unsigned)p->run_adc_noise,
               (unsigned)p->run_sg_n, (unsigned)p->run_sg_min, (unsigned)p->run_sg_max,
               (unsigned)p->learn_sg_seq_first, (unsigned)p->learn_sg_seq_last);
    rt_kprintf("[DIAG] %s thr dev=%d/%d drop=%u/%u dur=%u/%u/%u rec=%u age=%u/%u min_sg=%u\n",
               tag, (int)p->adc_dev_warn, (int)p->adc_dev_trip,
               (unsigned)p->sg_drop_warn, (unsigned)p->sg_drop_trip,
               (unsigned)p->dur_sg_ms, (unsigned)p->dur_adc_ms, (unsigned)p->dur_fused_ms,
               (unsigned)p->dur_recover_ms, (unsigned)p->age_sg_ms,
               (unsigned)p->age_frame_ms, (unsigned)p->min_new_sg);
}

static rt_err_t rel_cmd_zero(rt_uint32_t n_req)
{
    diag_input_t in;
    rt_uint32_t deadline, seen = 0;

    if (!rel_static_and_ready())
    {
        rt_kprintf("[DIAG] zero REFUSED: not static+READY (no capture started)\n");
        return -RT_EPERM;
    }
    rel_live_identity(&d_pending);
    d_pending.phase_mask   = RPPH_CRUISE;
    d_pending.dir_mask     = 3u;
    d_pending.sampling_def = DIAG_RPROF_SAMPLING_DEF;
    /* rel_live_identity 已把整套 run_* 清零: 上一轮的 learn 凭据必须同时作废,
     * 否则 "zero 后直接 commit" 会带着 learn=TRUE 的旧标记去发布一套只有零点的
     * 候选。次序固定为 zero -> learn -> commit, 每一步都重新要求。 */
    d_pending_learn = RT_FALSE;
    /* 签名此刻就能算(只覆盖驱动配置/采样定义身份, 不含速度窗): 有了它, 学习阶段
     * 才有"是否还在被批准的工况里"的判据 —— rel_cap_feed 的窗口检查在
     * base.signature==0 时是**关闭**的, 不在这里算就等于没有门禁。 */
    d_pending.signature = rel_profile_signature(&d_pending);
    if (rel_cap_start((rt_uint8_t)REL_CAP_ZERO, n_req, &d_pending) != RT_EOK)
    {
        rt_kprintf("[DIAG] zero REFUSED: another capture is active\n");
        return -RT_EBUSY;
    }

    deadline = rt_tick_get() + REL_CMD_DEADLINE_MS;
    rt_memset(&in, 0, sizeof(in));
    for (;;)
    {
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
        return -RT_ETIMEOUT;
    }
    if (rel_cap_finish(&d_pending) != RT_EOK)
    {
        rt_kprintf("[DIAG] zero FAILED: accumulator rejected\n");
        return -RT_EINVAL;
    }
    d_pending_zero = RT_TRUE;
    rt_kprintf("[DIAG] zero OK n=%u raw %u..%u mean_x1000=%u (cal source untouched)\n",
               (unsigned)d_pending.zero_n, (unsigned)d_pending.zero_min,
               (unsigned)d_pending.zero_max, (unsigned)d_pending.zero_mean_x1000);
    return RT_EOK;
}

static rt_err_t rel_cmd_learn(rt_uint32_t n_req, rt_uint32_t lo, rt_uint32_t hi,
                              rt_uint32_t dir)
{
    diag_input_t in;
    rt_uint32_t deadline, seen = 0;
    rt_err_t feed;

    if (!d_pending_zero)
    { rt_kprintf("[DIAG] learn REFUSED: run 'diag_profile zero N' first\n"); return -RT_EPERM; }

    d_pending.hz_low  = lo;
    d_pending.hz_high = hi;
    d_pending.dir_mask = (dir == 2u) ? 3u : (rt_uint32_t)(1u << dir);
    if (rel_cap_start((rt_uint8_t)REL_CAP_LEARN, n_req, &d_pending) != RT_EOK)
    { rt_kprintf("[DIAG] learn REFUSED: another capture is active\n"); return -RT_EBUSY; }

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
                return -RT_EPERM;
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
        return -RT_ETIMEOUT;
    }
    if (rel_cap_finish(&d_pending) != RT_EOK)
    { rt_kprintf("[DIAG] learn FAILED: accumulator rejected\n"); return -RT_EINVAL; }
    d_pending_learn = RT_TRUE;
    d_pending.signature = rel_profile_signature(&d_pending);
    rt_kprintf("[DIAG] learn OK adc[%u..%u]n%u noise%u sg[%u..%u]n%u seq%u..%u\n",
               (unsigned)d_pending.run_adc_min, (unsigned)d_pending.run_adc_max,
               (unsigned)d_pending.run_adc_n, (unsigned)d_pending.run_adc_noise,
               (unsigned)d_pending.run_sg_min, (unsigned)d_pending.run_sg_max,
               (unsigned)d_pending.run_sg_n,
               (unsigned)d_pending.learn_sg_seq_first,
               (unsigned)d_pending.learn_sg_seq_last);
    return RT_EOK;
}

/* 提交借用既有静止事务门做最终资格复核; 校验不过 ⇒ 一个字段都不写。 */
static rt_err_t rel_cmd_commit(void)
{
    const char *why = "?";
    diag_rprof_t cand, cur;
    rt_err_t e;

    if (!d_pending_zero || !d_pending_learn)
    {
        rt_kprintf("[DIAG] commit REFUSED: pending incomplete (zero=%d learn=%d)\n",
                   (int)d_pending_zero, (int)d_pending_learn);
        return -RT_EPERM;
    }
    if (!rel_static_and_ready())
    { rt_kprintf("[DIAG] commit REFUSED: not static+READY\n"); return -RT_EPERM; }
    if (project_config_get_rprof(&cur) != RT_EOK)
    { rt_kprintf("[DIAG] commit REFUSED: config lock failed\n"); return -RT_EIO; }

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
    cand.signature = rel_profile_signature(&cand);

    if (cand.signature != rel_live_signature())
    { rt_kprintf("[DIAG] commit REFUSED: drive config drifted while learning\n"); return -RT_EPERM; }
    if (rel_profile_validate(&cand, &why) != RT_EOK)
    { rt_kprintf("[DIAG] commit REFUSED: profile invalid (%s)\n", why); return -RT_EINVAL; }

    if (motor_calibration_static_begin() != RT_EOK)
    { rt_kprintf("[DIAG] commit REFUSED: static transaction gate busy\n"); return -RT_EBUSY; }
    e = project_config_set_rprof(&cand);
    motor_calibration_static_end();

    if (e != RT_EOK)
    { rt_kprintf("[DIAG] commit FAILED rc=%d (nothing changed)\n", e); return e; }
    d_pending = cand;
    rt_kprintf("[DIAG] commit OK id=%u rev=%u flags=%04X FIELD_UNVERIFIED (config_save to persist)\n",
               (unsigned)cand.profile_id, (unsigned)cand.revision, (unsigned)cand.flags);
    return RT_EOK;
}

static rt_err_t rel_cmd_clear(rt_bool_t confirmed)
{
    rt_err_t e;

    if (!confirmed)
    { rt_kprintf("[DIAG] clear needs: diag_profile clear CONFIRM\n"); return -RT_EINVAL; }
    if (!rel_static_and_ready())
    { rt_kprintf("[DIAG] clear REFUSED: not static+READY\n"); return -RT_EPERM; }
    if (motor_calibration_static_begin() != RT_EOK)
    { rt_kprintf("[DIAG] clear REFUSED: static gate busy\n"); return -RT_EBUSY; }
    e = project_config_clear_rprof();
    motor_calibration_static_end();
    if (e != RT_EOK) { rt_kprintf("[DIAG] clear FAILED rc=%d\n", e); return e; }

    d_pending_zero = RT_FALSE;
    d_pending_learn = RT_FALSE;
    rt_memset(&d_pending, 0, sizeof(d_pending));
    (void)diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    rt_kprintf("[DIAG] profile voided; mode=MONITOR_ONLY (fault state and logs untouched)\n");
    return RT_EOK;
}

static void diag_profile(int argc, char **argv)
{
    diag_rprof_t live;
    rt_uint32_t n, lo, hi, dir;

    if (argc >= 2 && rt_strcmp(argv[1], "show") == 0 && argc == 2)
    {
        live = d_pending;
        if (project_config_get_rprof(&live) != RT_EOK)
        { rt_kprintf("[DIAG] show: config lock failed\n"); return; }
        rel_show_one("profile", &live);
        rel_show_one("pending", &d_pending);
        rt_kprintf("[DIAG] live sig=%u match=%s\n", (unsigned)rel_live_signature(),
                   (live.magic && live.signature == rel_live_signature()) ? "YES" : "NO");
        return;
    }
    if (argc == 3 && rt_strcmp(argv[1], "zero") == 0)
    {
        if (!rel_parse_u32(argv[2], 32, 256, &n))
        { rt_kprintf("[DIAG] zero N must be 32..256\n"); return; }
        (void)rel_cmd_zero(n);
        return;
    }
    if (argc == 6 && rt_strcmp(argv[1], "learn") == 0)
    {
        if (!rel_parse_u32(argv[2], 8, 32, &n) || !rel_parse_u32(argv[3], 1, 20000, &lo) ||
            !rel_parse_u32(argv[4], 1, 20000, &hi) || !rel_parse_u32(argv[5], 0, 2, &dir) ||
            lo > hi)
        { rt_kprintf("[DIAG] learn args: N=8..32 LO HI(LO<=HI<=20000) DIR=0|1|2\n"); return; }
        (void)rel_cmd_learn(n, lo, hi, dir);
        return;
    }
    if (argc == 2 && rt_strcmp(argv[1], "commit") == 0)
    { (void)rel_cmd_commit(); return; }
    if (argc == 3 && rt_strcmp(argv[1], "clear") == 0)
    { (void)rel_cmd_clear(rt_strcmp(argv[2], "CONFIRM") == 0); return; }

    rt_kprintf("usage: diag_profile show | zero N | learn N LO HI DIR | commit | clear CONFIRM\n");
}
MSH_CMD_EXPORT(diag_profile, relative protection profile show/zero/learn/commit/clear);
