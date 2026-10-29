/*
 * current_cal.c - M7 bus-current calibration command surface
 *
 * Scope: show / sample / apply / nominal only. It reuses Sensor's latest
 * frames (no new sampling thread, no ADC reconfiguration) and the existing
 * project_config persistence (apply is RAM-only; config_save stays explicit).
 *
 * Safety gates:
 *   - every entry first checks thread/non-ISR/non-PRIMASK context; illegal
 *     context is silent and never waits for a Sensor frame;
 *   - apply/nominal hold motor_calibration_static_begin() across the config
 *     publication; that keeps mot_lock and blocks arm/diag from acquiring the
 *     output between the static check and the coefficient update;
 *   - source MEASURED is a human responsibility confirmation only; it is not
 *     a chip/instrument verification and does not enable ACTIVE protection.
 */

#include <rtthread.h>
#include <float.h>
#include "current_adc.h"
#include "project_config.h"
#include "sensor_service.h"
#include "motor.h"
#include "stop_priority.h"

#define CC_SAMPLE_MIN       32u
#define CC_SAMPLE_MAX       256u
#define CC_OFFSET_MAX_UV    3300000u    /* 0..3300 mV */
#define CC_GAIN_MIN_UV_A    1000u       /* 0.001 V/A */
#define CC_GAIN_MAX_UV_A    100000000u  /* 100 V/A */
#define CC_DEADLINE_CAP_MS  12000u

static rt_bool_t cc_ctx_ok(void)
{
    return (rt_interrupt_get_nest() == 0 && !sp_intr_masked() &&
            rt_thread_self() != RT_NULL) ? RT_TRUE : RT_FALSE;
}

static rt_bool_t cc_parse_u32(const char *s, rt_uint32_t *out)
{
    rt_uint64_t v = 0;

    if (s == RT_NULL || *s == '\0' || out == RT_NULL) return RT_FALSE;
    while (*s != '\0')
    {
        if (*s < '0' || *s > '9') return RT_FALSE;
        v = v * 10u + (rt_uint32_t)(*s - '0');
        if (v > 0xFFFFFFFFu) return RT_FALSE;
        s++;
    }
    *out = (rt_uint32_t)v;
    return RT_TRUE;
}

static void cc_u64_dec(rt_uint64_t v, char *out, rt_size_t n)
{
    char tmp[21];
    int i = 0, j;

    if (out == RT_NULL || n == 0) return;
    do
    {
        tmp[i++] = (char)('0' + (int)(v % 10u));
        v /= 10u;
    } while (v != 0u && i < (int)sizeof(tmp));
    if (i >= (int)n) i = (int)n - 1;
    for (j = 0; j < i; ++j) out[j] = tmp[i - 1 - j];
    out[i] = '\0';
}

static rt_int32_t cc_round_scaled(float v, rt_int32_t mult)
{
    float a = v * (float)mult;
    return (a >= 0.0f) ? (rt_int32_t)(a + 0.5f) : (rt_int32_t)(a - 0.5f);
}

static const char *cc_src_name(rt_uint8_t src)
{
    switch (src)
    {
    case CURRENT_ADC_CAL_MEASURED:    return "MEASURED";
    case CURRENT_ADC_CAL_THEORETICAL: return "THEORETICAL";
    default:                          return "NONE";
    }
}

static void cc_usage(void)
{
    rt_kprintf("usage: current_cal show\n");
    rt_kprintf("usage: current_cal sample 32..256\n");
    rt_kprintf("usage: current_cal apply <offset_uV> <gain_uV_per_A> CONFIRM_MEASURED\n");
    rt_kprintf("usage: current_cal nominal CONFIRM\n");
}

static void cc_show(void)
{
    current_adc_cal_t cal;
    project_config_t cfg;
    sensor_frame_t f;
    rt_int32_t off_uv, gain_uv;
    rt_bool_t cfg_valid;

    if (current_adc_get_calibration(&cal) != RT_EOK ||
        project_config_get_snapshot(&cfg) != RT_EOK ||
        sensor_service_get_latest(&f) != RT_EOK)
    {
        rt_kprintf("[CAL] show: snapshot unavailable\n");
        return;
    }
    cfg_valid = project_config_is_valid();
    off_uv = cc_round_scaled(cal.offset_mv, 1000);
    gain_uv = cc_round_scaled(cal.gain_v_per_a, 1000000);

    rt_kprintf("[CAL] adc health=%s src=%s ver=%u\n",
               subsys_health_name(current_adc_get_health()),
               cc_src_name(cal.source), (unsigned)cal.version);
    rt_kprintf("[CAL] adc offset_uV=%d gain_uV_per_A=%d measured=%s\n",
               (int)off_uv, (int)gain_uv,
               cal.source == CURRENT_ADC_CAL_MEASURED ? "YES" : "NO");
    rt_kprintf("[CAL] cfg valid=%s src=%u offset_uV=%d gain_uV_per_A=%d\n",
               cfg_valid ? "YES" : "NO", (unsigned)cfg.cur_cal_source,
               (int)cc_round_scaled(cfg.cur_offset_mv, 1000),
               (int)cc_round_scaled(cfg.cur_gain_v_per_a, 1000000));
    rt_kprintf("[CAL] sample seq=%u tick=%u raw=%u valid=%u fresh=%u\n",
               (unsigned)f.seq, (unsigned)f.timestamp,
               (unsigned)f.current_raw, (unsigned)f.valid_current,
               (unsigned)f.fresh_current);
    if (f.valid_current && f.fresh_current)
        rt_kprintf("[CAL] sample ma_uA=%d src=%s\n",
                   (int)cc_round_scaled(f.current_ma, 1000),
                   cc_src_name(f.current_cal_source));
    else
        rt_kprintf("[CAL] sample ma_uA=N/A (no valid fresh frame)\n");
    rt_kprintf("[CAL] note: show/sample do not change cal/motion/flash\n");
}

static void cc_sample(rt_uint32_t n_req)
{
    sensor_frame_t f;
    current_adc_cal_t live;
    rt_uint32_t n_seen = 0, n_valid = 0, n_dup = 0, n_invalid = 0, n_skip = 0;
    rt_uint32_t raw_sum = 0, raw_min = 4095u, raw_max = 0;
    rt_uint64_t raw_sq = 0;
    rt_uint32_t first_seq = 0, last_seq = 0, first_tick = 0, last_tick = 0;
    rt_uint32_t start_ver, end_ver;
    rt_uint8_t start_src, last_src = 0;
    rt_bool_t have_seq = RT_FALSE, have_valid = RT_FALSE;
    rt_bool_t changed = RT_FALSE, endpoint = RT_FALSE, range_bad = RT_FALSE;
    rt_bool_t seq_reset = RT_FALSE, sensor_err = RT_FALSE;
    rt_uint32_t wait_ms, wait_ticks;
    rt_tick_t deadline;
    char mean_buf[24], sq_buf[24];
    const char *fit = "NO", *why = "INSUFFICIENT";

    start_ver = current_adc_cal_version();
    start_src = (rt_uint8_t)current_adc_cal_source();
    wait_ms = n_req * 20u + 500u;
    if (wait_ms > CC_DEADLINE_CAP_MS) wait_ms = CC_DEADLINE_CAP_MS;
    wait_ticks = (wait_ms * RT_TICK_PER_SECOND) / 1000u;
    deadline = rt_tick_get() + wait_ticks;

    while (n_valid < n_req && (rt_int32_t)(rt_tick_get() - deadline) < 0)
    {
        if (sensor_service_get_latest(&f) != RT_EOK)
        {
            sensor_err = RT_TRUE;
            break;
        }
        if (!have_seq || f.seq != last_seq)
        {
            if (have_seq)
            {
                if (f.seq > last_seq + 1u) n_skip += f.seq - last_seq - 1u;
                else if (f.seq < last_seq) seq_reset = RT_TRUE;
            }
            if (!have_seq)
            {
                first_seq = f.seq;
                first_tick = f.timestamp;
            }
            have_seq = RT_TRUE;
            n_seen++;
            last_seq = f.seq;
            last_tick = f.timestamp;

            if (f.valid_current && f.fresh_current)
            {
                if (current_adc_get_calibration(&live) != RT_EOK)
                {
                    changed = RT_TRUE;
                }
                else
                {
                    if (live.version != start_ver ||
                        (have_valid && live.source != last_src))
                        changed = RT_TRUE;
                    last_src = live.source;
                }
                if (f.current_raw > 4095u) range_bad = RT_TRUE;
                if (f.current_raw == 0u || f.current_raw == 4095u)
                    endpoint = RT_TRUE;
                raw_sum += f.current_raw;
                raw_sq += (rt_uint64_t)f.current_raw * f.current_raw;
                if (f.current_raw < raw_min) raw_min = f.current_raw;
                if (f.current_raw > raw_max) raw_max = f.current_raw;
                n_valid++;
                have_valid = RT_TRUE;
            }
            else
            {
                n_invalid++;
            }
        }
        else
        {
            n_dup++;
        }
        rt_thread_mdelay(1);
    }

    end_ver = current_adc_cal_version();
    if (end_ver != start_ver ||
        (rt_uint8_t)current_adc_cal_source() != start_src)
        changed = RT_TRUE;

    if (sensor_err)          { why = "SENSOR_READ"; }
    else if (changed)        { why = "CAL_CHANGED"; }
    else if (seq_reset)      { why = "SEQ_RESET"; }
    else if (n_valid < n_req){ why = "INSUFFICIENT"; }
    else if (range_bad)      { why = "RAW_RANGE"; }
    else if (endpoint)       { why = "ENDPOINT"; }
    else                     { fit = "READY"; why = "OK"; }

    cc_u64_dec(n_valid ? (rt_uint64_t)raw_sum * 1000u / n_valid : 0u,
               mean_buf, sizeof(mean_buf));
    cc_u64_dec(raw_sq, sq_buf, sizeof(sq_buf));

    rt_kprintf("[CAL] sample n_req=%u n_seen=%u n_valid=%u dup=%u invalid=%u skip=%u\n",
               (unsigned)n_req, (unsigned)n_seen, (unsigned)n_valid,
               (unsigned)n_dup, (unsigned)n_invalid, (unsigned)n_skip);
    rt_kprintf("[CAL] sample seq=%u..%u tick=%u..%u ver=%u..%u reset=%u\n",
               (unsigned)first_seq, (unsigned)last_seq,
               (unsigned)first_tick, (unsigned)last_tick,
               (unsigned)start_ver, (unsigned)end_ver,
               (unsigned)seq_reset);
    rt_kprintf("[CAL] raw sum=%u min=%u max=%u mean_x1000=%s\n",
               (unsigned)raw_sum, (unsigned)(n_valid ? raw_min : 0u),
               (unsigned)raw_max, mean_buf);
    rt_kprintf("[CAL] raw sum_sq=%s\n", sq_buf);
    rt_kprintf("[CAL] sample cal src=%s changed=%u fit=%s reason=%s\n",
               cc_src_name(start_src), (unsigned)changed, fit, why);
    rt_kprintf("[CAL] note: sample is observation only; no cal/flash/motion change\n");
}

static void cc_apply_or_nominal(int argc, char **argv, rt_bool_t nominal)
{
    rt_uint32_t off_uv, gain_uv;
    rt_err_t e;
    float off_mv, gain_vpa;
    current_adc_cal_t cal;

    if (nominal)
    {
        if (argc != 3 || rt_strcmp(argv[2], "CONFIRM") != 0)
        { cc_usage(); return; }
        off_uv = 1650000u;
        gain_uv = 600000u;
    }
    else
    {
        if (argc != 5 || rt_strcmp(argv[4], "CONFIRM_MEASURED") != 0 ||
            !cc_parse_u32(argv[2], &off_uv) || !cc_parse_u32(argv[3], &gain_uv))
        { cc_usage(); return; }
        if (off_uv > CC_OFFSET_MAX_UV ||
            gain_uv < CC_GAIN_MIN_UV_A || gain_uv > CC_GAIN_MAX_UV_A)
        {
            rt_kprintf("[CAL] %s rejected: numeric range\n",
                       nominal ? "nominal" : "apply");
            return;
        }
    }

    off_mv = (float)off_uv / 1000.0f;
    gain_vpa = (float)gain_uv / 1000000.0f;

    e = motor_calibration_static_begin();
    if (e != RT_EOK)
    {
        rt_kprintf("[CAL] %s REFUSED: static gate rc=%d\n",
                   nominal ? "nominal" : "apply", (int)e);
        return;
    }
    e = project_config_set_calibration(off_mv, gain_vpa,
                                       nominal ? CURRENT_ADC_CAL_THEORETICAL
                                               : CURRENT_ADC_CAL_MEASURED);
    motor_calibration_static_end();

    if (e != RT_EOK)
    {
        rt_kprintf("[CAL] %s FAILED rc=%d (old tuple unchanged)\n",
                   nominal ? "nominal" : "apply", (int)e);
        return;
    }
    (void)current_adc_get_calibration(&cal);
    rt_kprintf("[CAL] %s OK src=%s offset_uV=%u gain_uV_per_A=%u RAM-only\n",
               nominal ? "nominal" : "apply",
               cc_src_name(cal.source), (unsigned)off_uv, (unsigned)gain_uv);
    if (!nominal)
        rt_kprintf("[CAL] apply: confirmation is responsibility only;"
                   " use config_save to persist\n");
}

static void current_cal_cmd(int argc, char **argv)
{
    /* Context first: illegal context is silent even for bad argument combos. */
    if (!cc_ctx_ok()) return;

    if (argc < 2)
    { cc_usage(); return; }

    if (rt_strcmp(argv[1], "show") == 0)
    {
        if (argc != 2) { cc_usage(); return; }
        cc_show();
        return;
    }
    if (rt_strcmp(argv[1], "sample") == 0)
    {
        rt_uint32_t n;
        if (argc != 3 || !cc_parse_u32(argv[2], &n) ||
            n < CC_SAMPLE_MIN || n > CC_SAMPLE_MAX)
        { cc_usage(); return; }
        cc_sample(n);
        return;
    }
    if (rt_strcmp(argv[1], "apply") == 0)
    {
        cc_apply_or_nominal(argc, argv, RT_FALSE);
        return;
    }
    if (rt_strcmp(argv[1], "nominal") == 0)
    {
        cc_apply_or_nominal(argc, argv, RT_TRUE);
        return;
    }
    cc_usage();
}
MSH_CMD_EXPORT_ALIAS(current_cal_cmd, current_cal,
                     bus current calibration: show/sample/apply/nominal);
