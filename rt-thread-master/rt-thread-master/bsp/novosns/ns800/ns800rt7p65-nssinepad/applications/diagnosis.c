/*
 * diagnosis.c - 多源异常诊断引擎 (Phase 7-C)
 *
 * 节拍 100Hz(prio 9 线程, 消费 sensor_service 最新帧)。
 * 引擎核心 diag_step() 为纯状态推进, diag_selftest 注入合成输入做确定性验证。
 *
 * 阈值(内嵌默认; 本阶段第二提交改为 project_config 持久化):
 *   SG_RESULT(datasheet): 数值越低=负载越高, 低于带阈值=负载抬升, 更低=堵转特征
 *   速度分带: <200Hz 低速 / 200~1000Hz 中速 / >1000Hz 高速
 *   电流: 高于带阈值 warn/stall
 *   振动: 幅值含 ~1g 重力基线, 超过 impact 阈值且短持续 = IMPACT
 *
 * 反误报纪律(全部落实在 diag_step):
 *   单 sample 不判 severe; SG 阈值按速度分带; 无效数据冻结特征只累计 bad;
 *   加减速/静止相位抑制 SG 与电流堵转判据; CONFIRMED 需 SG+电流多源一致;
 *   恢复需连续干净帧(hysteresis)。
 *
 * 模式: MONITOR_ONLY 默认; ACTIVE_PROTECTION 需电流已标定才允许开启,
 *       severe 边沿 → safety_post_event(EVT_MULTI_FAULT) 交 Safety 处理
 *       (引擎不直接操作 Flash, 硬件 ESTOP/LIMIT/DIAG 不经过本引擎)。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "app_health.h"
#include "sensor_service.h"
#include "motor.h"
#include "safety_state.h"
#include "current_adc.h"
#include "project_config.h"
#include "diagnosis.h"

/* ---------- 阈值(Phase 7-C-2: 由 project_config 持久化驱动) ----------
 * dt 每帧从 project_config 同步; 配置非法时 config 层保证为安全默认值。 */
typedef struct
{
    rt_uint32_t band_hz[2];
    rt_int32_t  sg_warn[3], sg_stall[3];
    float       cur_warn_ma[3], cur_stall_ma[3];
    rt_uint32_t vib_impact_mg;
    rt_uint32_t persist_warn;
    rt_uint32_t persist_stall;
    rt_uint32_t impact_frames;
    rt_uint32_t hysteresis_frames;
} diag_thresholds_t;

static diag_thresholds_t dt;

static void diag_sync_config(void)
{
    project_config_t cs;
    if (project_config_get_snapshot(&cs) != RT_EOK) return;
    const project_config_t *c = &cs;

    dt.band_hz[0] = c->band_hz[0];  dt.band_hz[1] = c->band_hz[1];
    rt_memcpy(dt.sg_warn,  c->sg_warn,  sizeof(dt.sg_warn));
    rt_memcpy(dt.sg_stall, c->sg_stall, sizeof(dt.sg_stall));
    rt_memcpy(dt.cur_warn_ma,  c->cur_warn_ma,  sizeof(dt.cur_warn_ma));
    rt_memcpy(dt.cur_stall_ma, c->cur_stall_ma, sizeof(dt.cur_stall_ma));
    dt.vib_impact_mg    = c->vib_impact_mg;
    dt.persist_warn     = c->persist_warn;
    dt.persist_stall    = c->persist_stall;
    dt.impact_frames    = c->impact_frames;
    dt.hysteresis_frames = c->hysteresis_frames;
}

/* ---------- 引擎状态 ---------- */
#define DIAG_VIB_WIN 16

typedef struct
{
    float sg_filt, cur_filt;             /* EMA 平滑 */
    float sg_delta, cur_delta;           /* 滤波值帧间差 */
    rt_uint32_t vib_win[DIAG_VIB_WIN];   /* 振动幅值滑窗(含重力基线) */
    rt_uint8_t  vib_idx;
    float vib_rms, vib_peak;
    rt_int32_t speed_band;               /* 0/1/2, -1=stopped */
    rt_uint32_t sg_below_warn_cnt, sg_below_stall_cnt;
    rt_uint32_t cur_above_warn_cnt, cur_above_stall_cnt;
    rt_uint32_t vib_impact_cnt;
    rt_uint32_t sensor_bad_cnt;
    rt_uint32_t clean_cnt;               /* 滞回恢复计数 */
    rt_uint32_t last_seq;                /* Fix B: 已消费的源帧序号(引擎内去重) */
    diag_verdict_t verdict;
} diag_state_t;

static diag_state_t ds;
static diag_mode_t d_mode = DIAG_MODE_MONITOR_ONLY;
static rt_bool_t d_severe_latched = RT_FALSE;   /* severe 事件只发上升沿 */
static rt_thread_t d_tid = RT_NULL;
static subsys_health_t d_health = SUBSYS_UNINIT;

/* 整数平方根(Newton) */
static rt_uint32_t d_isqrt(rt_uint32_t n)
{
    rt_uint32_t x = n, y = 1;
    if (n == 0) return 0;
    while (x > y) { x = (x + y) / 2; y = n / x; }
    return x;
}

static rt_uint32_t d_isqrt64(rt_uint64_t n)   /* P1-14: 64bit 版本 */
{
    rt_uint64_t x = n, y = 1;
    if (n == 0) return 0;
    while (x > y) { x = (x + y) / 2; y = n / x; }
    return (rt_uint32_t)x;
}

rt_uint32_t diag_vib_magnitude(rt_int16_t mg_x, rt_int16_t mg_y, rt_int16_t mg_z)
{
    rt_int64_t x = mg_x, y = mg_y, z = mg_z;   /* P1-14: 64bit 中间量 */
    return d_isqrt64((rt_uint64_t)(x * x + y * y + z * z));
}

static const char *verdict_name(diag_verdict_t v)
{
    switch (v)
    {
    case DIAG_NORMAL:         return "NORMAL";
    case DIAG_LOAD_WARNING:   return "LOAD_WARNING";
    case DIAG_IMPACT:         return "IMPACT";
    case DIAG_OVERLOAD:       return "OVERLOAD";
    case DIAG_STALL_SUSPECT:  return "STALL_SUSPECT";
    case DIAG_STALL_CONFIRMED:return "STALL_CONFIRMED";
    case DIAG_SENSOR_FAULT:   return "SENSOR_FAULT";
    default:                  return "?";
    }
}

/* ---------- 引擎核心 ---------- */

void diag_reset(void)
{
    rt_memset(&ds, 0, sizeof(ds));       /* 含 Fix B: ds.last_seq = 0 */
    ds.speed_band = -1;
    ds.verdict = DIAG_NORMAL;
    d_severe_latched = RT_FALSE;
}

static rt_int32_t band_of(rt_uint32_t hz)
{
    if (hz == 0) return -1;
    if (hz < dt.band_hz[0]) return 0;
    if (hz < dt.band_hz[1]) return 1;
    return 2;
}

/* severe 边沿上报(ACTIVE 模式): 交 Safety 处理 */
static void diag_report_severe(diag_verdict_t v)
{
    if (d_mode != DIAG_MODE_ACTIVE_PROTECTION) return;
    if (!d_severe_latched)
    {
        d_severe_latched = RT_TRUE;
        rt_kprintf("[DIAG] ACTIVE: severe %s -> post EVT_MULTI_FAULT\n",
                   verdict_name(v));
        safety_post_event(EVT_MULTI_FAULT);   /* 引擎不直接操作 Flash */
    }
}

diag_verdict_t diag_step(const diag_input_t *in)
{
    rt_int32_t band;
    rt_uint8_t cruise;

    /* ---- Fix B: 同帧去重放进引擎(P1-5 的真正落点) ----
     * 旧实现只在 Diagnosis Thread 里判重, 于是 diag_selftest 直接调 diag_step
     * 时根本测不到这一层; 而且线程用 continue 跳过了循环尾部的 mdelay, 造成
     * prio 9 忙等。现在: 重复 seq 不推进任何特征/计数器/persistence, 直接返回
     * 当前判定; seq==0 视为"调用方未提供序号"(如合成注入), 不做去重。 */
    if (in->seq != 0 && in->seq == ds.last_seq)
        return ds.verdict;
    if (in->seq != 0) ds.last_seq = in->seq;

    /* ---- sensor missing: 冻结特征, 只累计 bad(禁止当 0 参与判据) ---- */
    if (!in->sg_valid || !in->cur_valid || !in->imu_valid)
    {
        ds.sensor_bad_cnt++;
        ds.clean_cnt = 0;
        if (ds.sensor_bad_cnt >= dt.persist_warn)
            ds.verdict = DIAG_SENSOR_FAULT;
        return ds.verdict;
    }
    ds.sensor_bad_cnt = 0;

    /* ---- 特征: EMA 滤波 + Δ(P1-6: 保存旧值再更新, Δ 才是真实帧间变化) ---- */
    float old_sg  = ds.sg_filt;
    float old_cur = ds.cur_filt;
    ds.sg_filt  += ((float)in->sg        - ds.sg_filt)  * 0.2f;
    ds.cur_filt += (in->current_ma       - ds.cur_filt) * 0.2f;
    ds.sg_delta  = ds.sg_filt  - old_sg;
    ds.cur_delta = ds.cur_filt - old_cur;

    /* ---- 振动滑窗: RMS + peak ---- */
    ds.vib_win[ds.vib_idx] = (rt_uint32_t)(in->vib_mg < 0 ? 0 : in->vib_mg);
    ds.vib_idx = (rt_uint8_t)((ds.vib_idx + 1) % DIAG_VIB_WIN);
    {
        rt_uint32_t i, peak = 0;
        rt_uint64_t acc = 0;                 /* P1-14: 64bit 防平方溢出 */
        for (i = 0; i < DIAG_VIB_WIN; ++i)
        {
            rt_uint64_t s2 = (rt_uint64_t)ds.vib_win[i] * ds.vib_win[i];
            acc += s2;
            if (ds.vib_win[i] > peak) peak = ds.vib_win[i];
        }
        ds.vib_rms  = (float)d_isqrt64(acc / DIAG_VIB_WIN);
        ds.vib_peak = (float)peak;
    }

    /* ---- 速度分带 + 巡航相位 ---- */
    ds.speed_band = band = band_of(in->step_hz);
    cruise = (in->motor_state == (rt_uint8_t)MOTOR_CRUISE && in->step_hz > 0);

    /* ---- persistence 计数 ----
     * SG/电流堵转判据仅在 CRUISE 有效: 斜坡期 SG 漂移、电流自然高; 静止无 BEMF */
    if (cruise && band >= 0)
    {
        if (ds.sg_filt < (float)dt.sg_warn[band]) ds.sg_below_warn_cnt++;
        else if (ds.sg_below_warn_cnt) ds.sg_below_warn_cnt--;
        if (ds.sg_filt < (float)dt.sg_stall[band]) ds.sg_below_stall_cnt++;
        else if (ds.sg_below_stall_cnt) ds.sg_below_stall_cnt--;

        if (ds.cur_filt > dt.cur_warn_ma[band]) ds.cur_above_warn_cnt++;
        else if (ds.cur_above_warn_cnt) ds.cur_above_warn_cnt--;
        if (ds.cur_filt > dt.cur_stall_ma[band]) ds.cur_above_stall_cnt++;
        else if (ds.cur_above_stall_cnt) ds.cur_above_stall_cnt--;
    }
    else
    {
        if (ds.sg_below_warn_cnt) ds.sg_below_warn_cnt--;
        if (ds.sg_below_stall_cnt) ds.sg_below_stall_cnt--;
        if (ds.cur_above_warn_cnt) ds.cur_above_warn_cnt--;
        if (ds.cur_above_stall_cnt) ds.cur_above_stall_cnt--;
    }

    /* ---- IMPACT 计数 ---- */
    if (ds.vib_peak >= (float)dt.vib_impact_mg) ds.vib_impact_cnt++;
    else if (ds.vib_impact_cnt) ds.vib_impact_cnt--;

    /* ---- 判定(优先级: sensor > severe > warn; 单 sample 永不 severe) ---- */
    if (ds.sensor_bad_cnt >= dt.persist_warn)
    {
        ds.verdict = DIAG_SENSOR_FAULT;
    }
    else if (ds.sg_below_stall_cnt >= dt.persist_stall &&
             ds.cur_above_warn_cnt  >= dt.persist_warn)
    {
        /* 多源一致: SG 崩 + 电流升, 双计数同时满足才 CONFIRMED */
        ds.verdict = DIAG_STALL_CONFIRMED;
    }
    else if (ds.cur_above_stall_cnt >= dt.persist_stall)
    {
        ds.verdict = DIAG_OVERLOAD;
    }
    else if (ds.vib_impact_cnt >= dt.impact_frames)
    {
        ds.verdict = DIAG_IMPACT;
    }
    else if (ds.sg_below_warn_cnt >= dt.persist_warn && cruise)
    {
        ds.verdict = DIAG_STALL_SUSPECT;
    }
    else if (ds.cur_above_warn_cnt >= dt.persist_warn)
    {
        ds.verdict = DIAG_LOAD_WARNING;
    }
    else
    {
        /* hysteresis: 非 NORMAL 状态需要连续干净帧才回落(IMPACT 除外, 短事件) */
        if (ds.verdict != DIAG_NORMAL && ds.verdict != DIAG_IMPACT)
        {
            ds.clean_cnt++;
            if (ds.clean_cnt < dt.hysteresis_frames)
                return ds.verdict;
        }
        ds.clean_cnt = 0;
        ds.verdict = DIAG_NORMAL;
    }

    /* severe 边沿上报 */
    if (ds.verdict == DIAG_STALL_CONFIRMED || ds.verdict == DIAG_OVERLOAD)
        diag_report_severe(ds.verdict);
    else
        d_severe_latched = RT_FALSE;

    return ds.verdict;
}

/* ---------- 线程: 消费最新 sensor_frame ----------
 * Fix B: 循环尾部无条件 mdelay(10)。旧实现用 "if (f.seq == d_last_seq) continue;"
 * 跳过 mdelay, 在同帧未更新时会把 prio 9 线程变成忙等, 饿死 prio >= 9 的
 * bblog(18)/ui(20)/tshell(20)。同帧去重现在由 diag_step() 负责。 */

static void diag_thread_entry(void *param)
{
    sensor_frame_t f;
    diag_input_t in;

    (void)param;

    while (1)
    {
        diag_sync_config();              /* 阈值跟随持久化配置(每帧同步) */
        if (sensor_service_get_latest(&f) == RT_EOK && f.seq != 0)
        {
            in.seq         = f.seq;
            in.timestamp   = f.timestamp;
            in.sg_valid    = f.valid_sg;
            in.sg          = f.sg_result;
            in.cur_valid   = f.valid_current;
            in.current_ma  = f.current_ma;
            in.imu_valid   = f.valid_imu;
            in.vib_mg      = f.vib_mg;
            in.motor_state = f.motor_state;
            in.step_hz     = f.step_hz;
            diag_step(&in);              /* 重复 seq 由引擎直接返回当前判定 */
        }
        rt_thread_mdelay(10);               /* 100Hz */
    }
}

/* ---------- 正式 API (diagnosis.h) ---------- */

rt_err_t diagnosis_init(void)
{
    if (d_tid != RT_NULL) return RT_EOK;    /* 幂等 */

    diag_sync_config();                     /* 初始阈值来自持久化配置 */
    diag_reset();
    d_tid = rt_thread_create("diag", diag_thread_entry, RT_NULL,
                             1024, 9, 10);  /* prio 9, 100Hz */
    if (d_tid == RT_NULL) { d_health = SUBSYS_FAILED; return -RT_ERROR; }
    rt_thread_startup(d_tid);

    d_health = SUBSYS_OK;
    rt_kprintf("[DIAG] thread started (prio 9, mode=%s)\n",
               d_mode == DIAG_MODE_MONITOR_ONLY ? "MONITOR_ONLY" : "ACTIVE");
    return RT_EOK;
}

rt_err_t diagnosis_set_mode(diag_mode_t mode)
{
    if (mode == DIAG_MODE_ACTIVE_PROTECTION)
    {
        /* P1-7 标定门禁: 只有 MEASURED 来源才允许 ACTIVE_PROTECTION;
         * 理论默认值(THEORETICAL)不得驱动保护动作 */
        if (current_adc_cal_source() != CURRENT_ADC_CAL_MEASURED)
        {
            rt_kprintf("[DIAG] ACTIVE REFUSED: calibration source=%d"
                       " (need MEASURED)\n", current_adc_cal_source());
            return -RT_EPERM;
        }
        diag_reset();
    }
    d_mode = mode;
    rt_kprintf("[DIAG] mode=%s\n",
               d_mode == DIAG_MODE_ACTIVE_PROTECTION ? "ACTIVE_PROTECTION"
                                                     : "MONITOR_ONLY");
    return RT_EOK;
}

diag_mode_t diagnosis_get_mode(void) { return d_mode; }
diag_verdict_t diagnosis_get_verdict(void) { return ds.verdict; }

void diag_get_features(float *sg_filt, float *sg_delta,
                       float *cur_filt, float *cur_delta)
{
    if (sg_filt)  *sg_filt  = ds.sg_filt;
    if (sg_delta) *sg_delta = ds.sg_delta;
    if (cur_filt) *cur_filt = ds.cur_filt;
    if (cur_delta) *cur_delta = ds.cur_delta;
}
subsys_health_t diagnosis_get_health(void) { return d_health; }

/* ---------- MSH ---------- */

static void diag_status(void)
{
    rt_kprintf("[DIAG] verdict=%s mode=%s health=%s\n",
               verdict_name(ds.verdict),
               d_mode == DIAG_MODE_ACTIVE_PROTECTION ? "ACTIVE" : "MONITOR_ONLY",
               subsys_health_name(d_health));
    rt_kprintf("[DIAG] sg_filt=%d delta=%d cur_filt=%d mA delta=%d mA\n",
               (int)ds.sg_filt, (int)ds.sg_delta,
               (int)ds.cur_filt, (int)ds.cur_delta);
    rt_kprintf("[DIAG] vib rms=%d peak=%d mg band=%d\n",
               (int)ds.vib_rms, (int)ds.vib_peak, ds.speed_band);
    rt_kprintf("[DIAG] cnt sg<warn=%u sg<stall=%u cur>warn=%u cur>stall=%u"
               " impact=%u bad=%u clean=%u\n",
               ds.sg_below_warn_cnt, ds.sg_below_stall_cnt,
               ds.cur_above_warn_cnt, ds.cur_above_stall_cnt,
               ds.vib_impact_cnt, ds.sensor_bad_cnt, ds.clean_cnt);
}
MSH_CMD_EXPORT(diag_status, show diagnosis engine features and verdict);

static void diag_mode(int argc, char **argv)
{
    if (argc != 2)
    {
        rt_kprintf("usage: diag_mode monitor|active (active needs calibration)\n");
        rt_kprintf("current: %s\n",
                   d_mode == DIAG_MODE_ACTIVE_PROTECTION ? "ACTIVE" : "MONITOR_ONLY");
        return;
    }
    if (rt_strcmp(argv[1], "monitor") == 0)
        diagnosis_set_mode(DIAG_MODE_MONITOR_ONLY);
    else if (rt_strcmp(argv[1], "active") == 0)
        diagnosis_set_mode(DIAG_MODE_ACTIVE_PROTECTION);
    else
        rt_kprintf("unknown mode\n");
}
MSH_CMD_EXPORT(diag_mode, switch diagnosis mode: diag_mode monitor|active);

/* ---------- diag_selftest: 确定性合成输入(非硬件验证) ---------- */

static rt_uint32_t s_feed_seq = 0;
static void s_feed(rt_uint16_t sg, float ma, rt_int16_t vib,
                   rt_uint8_t mstate, rt_uint32_t hz)
{
    diag_input_t in;
    in.seq = ++s_feed_seq;
    in.timestamp = 0;
    in.sg_valid = 1; in.sg = sg;
    in.cur_valid = 1; in.current_ma = ma;
    in.imu_valid = 1; in.vib_mg = vib;
    in.motor_state = mstate; in.step_hz = hz;
    diag_step(&in);
}

static void s_feed_missing(void)
{
    diag_input_t in;
    in.seq = ++s_feed_seq;
    in.timestamp = 0;
    in.sg_valid = 0; in.sg = 0;
    in.cur_valid = 0; in.current_ma = 0;
    in.imu_valid = 0; in.vib_mg = 0;
    in.motor_state = (rt_uint8_t)MOTOR_CRUISE; in.step_hz = 500;
    diag_step(&in);
}

static void diag_selftest(void)
{
    int pass = 1;
    int i;
    diag_verdict_t v;

    rt_kprintf("[SELFTEST-D] synthetic input injection (软件级, 非硬件验证)\n");

    /* 场景0: NORMAL —— 中速巡航健康值 */
    diag_reset();
    for (i = 0; i < 150; ++i)
        s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 0.normal: %s\n", verdict_name(v));
    if (v != DIAG_NORMAL) pass = 0;

    /* 场景1: 单 sample 不判 severe —— 一帧 stall 特征后立即恢复 */
    s_feed(30, 100.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 1.single-sample-no-severe: %s\n", verdict_name(v));
    if (v == DIAG_STALL_CONFIRMED || v == DIAG_OVERLOAD) pass = 0;

    /* 场景2: IMPACT —— 振动尖峰短持续, 之后自动恢复 */
    diag_reset();
    for (i = 0; i < 50; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    s_feed(500, 150.0f, 3000, (int)MOTOR_CRUISE, 500);
    s_feed(500, 150.0f, 3000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 2.impact: %s\n", verdict_name(v));
    if (v != DIAG_IMPACT) pass = 0;
    for (i = 0; i < 5; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 2.impact-recovery: %s\n", verdict_name(v));
    if (v != DIAG_NORMAL) pass = 0;

    /* 场景3: 慢过载 LOAD_WARNING —— 电流超 warn 持续 */
    diag_reset();
    for (i = 0; i < 80; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    for (i = 0; i < 80; ++i) s_feed(400, 600.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 3.slow-overload: %s\n", verdict_name(v));
    if (v != DIAG_LOAD_WARNING) pass = 0;

    /* 场景3b(P1-6 重写): Δ 真实性 = 稳定基线 → 单帧阶跃 → 立即断言幅度与符号。
     * 旧实现是在 80 帧高值"已经收敛之后"才检查 cur_delta>0: float32 在 600 附近
     * 的 ULP 约 6e-5, 约 67 帧后 EMA 步进小于半 ULP 而完全停住, Δ 恒等于 0.0f,
     * 该断言必然失败且无法证明 Δ 计算正确 —— 属假测试。 */
    {
        float sgf, sgd, cf, cd, base_cd;

        diag_reset();
        for (i = 0; i < 60; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
        diag_get_features(&sgf, &sgd, &cf, &base_cd);   /* 基线: Δ 应已停住 */
        if (base_cd >= 1.0f || base_cd <= -1.0f)
        { rt_kprintf("[SELFTEST-D] 3b baseline not stable (d=%d)\n", (int)base_cd);
          pass = 0; }
        s_feed(500, 600.0f, 1000, (int)MOTOR_CRUISE, 500);   /* 单帧上升阶跃 */
        diag_get_features(&sgf, &sgd, &cf, &cd);
        rt_kprintf("[SELFTEST-D] 3b.rise-delta: base=%d then %d (expect ~+90)\n",
                   (int)base_cd, (int)cd);
        if (cd < 50.0f)
        { rt_kprintf("[SELFTEST-D] 3b rise FAIL\n"); pass = 0; }

        diag_reset();
        for (i = 0; i < 60; ++i) s_feed(500, 900.0f, 1000, (int)MOTOR_CRUISE, 500);
        s_feed(500, 200.0f, 1000, (int)MOTOR_CRUISE, 500);   /* 单帧下降阶跃 */
        diag_get_features(&sgf, &sgd, &cf, &cd);
        rt_kprintf("[SELFTEST-D] 3b.fall-delta: %d (expect ~-140)\n", (int)cd);
        if (cd > -50.0f)
        { rt_kprintf("[SELFTEST-D] 3b fall FAIL\n"); pass = 0; }
    }

    /* 场景4: 堵转 —— SG 崩 + 电流升, 先 SUSPECT 后 CONFIRMED(persistence) */
    diag_reset();
    for (i = 0; i < 50; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    for (i = 0; i < 60; ++i) s_feed(80, 600.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 4.stall-suspect: %s\n", verdict_name(v));
    if (v != DIAG_STALL_SUSPECT) pass = 0;
    for (i = 0; i < 80; ++i) s_feed(20, 900.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 4.stall-confirmed: %s\n", verdict_name(v));
    if (v != DIAG_STALL_CONFIRMED) pass = 0;

    /* 场景4b: hysteresis —— 条件消失后保持一段(不立即 NORMAL) */
    s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 4b.hysteresis-hold: %s\n", verdict_name(v));
    if (v == DIAG_NORMAL) { rt_kprintf("[SELFTEST-D] 4b FAIL\n"); pass = 0; }

    /* 场景4c(P1-5 重写): 同帧去重 —— 现在测的是真去重层(diag_step 内部)。
     * 旧实现只断言"不 CONFIRMED", 而去重当时写在线程里, diag_step 根本看不到
     * seq, 于是 100 次同 seq 调用会正常累计 persistence 并判成 CONFIRMED ——
     * 该用例在正确代码上必挂。现在额外要求: 首帧之后的 99 次重复调用
     * 完全不得改变滤波值(一次算术都不做)。 */
    diag_reset();
    for (i = 0; i < 60; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    {
        diag_input_t dup;
        float sgf1, sgd1, cf1, cd1, sgf2, sgd2, cf2, cd2;

        dup.seq = 90210; dup.timestamp = 0;
        dup.sg_valid = 1; dup.sg = 20;              /* stall 特征 */
        dup.cur_valid = 1; dup.current_ma = 900.0f;
        dup.imu_valid = 1; dup.vib_mg = 1000;
        dup.motor_state = (rt_uint8_t)MOTOR_CRUISE; dup.step_hz = 500;

        diag_step(&dup);                            /* 第 1 次: 正常消费 */
        diag_get_features(&sgf1, &sgd1, &cf1, &cd1);
        for (i = 0; i < 99; ++i) diag_step(&dup);   /* 同 seq 再喂 99 次 */
        diag_get_features(&sgf2, &sgd2, &cf2, &cd2);

        rt_kprintf("[SELFTEST-D] 4c.dedup: cur_filt %d vs %d (must be identical)\n",
                   (int)cf1, (int)cf2);
        if (cf1 != cf2 || sgf1 != sgf2 || cd1 != cd2)
        { rt_kprintf("[SELFTEST-D] 4c FAIL: duplicate frame was re-processed\n");
          pass = 0; }

        v = diagnosis_get_verdict();
        rt_kprintf("[SELFTEST-D] 4c.dedup verdict: %s (expect not CONFIRMED)\n",
                   verdict_name(v));
        if (v == DIAG_STALL_CONFIRMED)
        { rt_kprintf("[SELFTEST-D] 4c FAIL: persistence accumulated on one frame\n");
          pass = 0; }

        /* 对照: 每帧换新 seq 必须立刻重新被处理并累计到 CONFIRMED
         * (证明去重不是"永远不干活") */
        for (i = 0; i < 150; ++i)
        { dup.seq = (rt_uint32_t)(90211 + i); diag_step(&dup); }
        v = diagnosis_get_verdict();
        rt_kprintf("[SELFTEST-D] 4c.control new-seq verdict: %s (expect CONFIRMED)\n",
                   verdict_name(v));
        if (v != DIAG_STALL_CONFIRMED)
        { rt_kprintf("[SELFTEST-D] 4c control FAIL: engine stopped consuming\n");
          pass = 0; }
    }

    /* 场景5: sensor missing —— 不当 0, 持续后 SENSOR_FAULT */
    diag_reset();
    for (i = 0; i < 50; ++i) s_feed(500, 150.0f, 1000, (int)MOTOR_CRUISE, 500);
    for (i = 0; i < 80; ++i) s_feed_missing();
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 5.sensor-missing: %s\n", verdict_name(v));
    if (v != DIAG_SENSOR_FAULT) pass = 0;

    /* 场景6: ACCEL 相位抑制 —— 斜坡期 SG 低不判堵转 */
    diag_reset();
    for (i = 0; i < 120; ++i)
        s_feed(20, 900.0f, 1000, (int)MOTOR_ACCEL, 300);
    v = diagnosis_get_verdict();
    rt_kprintf("[SELFTEST-D] 6.accel-inhibit: %s\n", verdict_name(v));
    if (v == DIAG_STALL_SUSPECT || v == DIAG_STALL_CONFIRMED) pass = 0;

    diag_reset();
    rt_kprintf("[SELFTEST-D] %s\n", pass ? "ALL PASS" : "FAILED");
}
MSH_CMD_EXPORT(diag_selftest, deterministic synthetic-input diagnosis selftest);
