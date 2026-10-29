/*
 * cc_main.c - M7 current calibration host chain (real current_adc.c,
 * project_config.c and current_cal.c; motor/sensor/storage are explicit
 * injectable fakes). No real serial/DAP/ADC/Flash object is constructed.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <math.h>

#include "rtthread.h"
#include "rtdevice.h"
#include "app_health.h"
#include "current_adc.h"
#include "project_config.h"
#include "sensor_service.h"
#include "motor.h"
#include "stop_priority.h"
#include "ns_storage.h"

int cc_kprintf(const char *fmt, ...);

static char g_cap[16384];
static int g_cap_len;
static int g_nul_seen;
static int g_nest, g_masked, g_thread_ok = 1;
static rt_tick_t g_tick;
static int g_crit_depth;
static int g_mutex_takes, g_mutex_releases;
static struct rt_thread g_fake_thread;
static struct rt_mutex g_fake_mutex;

static sensor_frame_t g_frames[512];
static int g_frame_count, g_frame_idx;
static int g_advance_on_tick = 1;
static void (*g_mdelay_cb)(void);

static int g_motor_rc = RT_EOK;
static int g_motor_begin, g_motor_end, g_motor_held;
static int g_save_calls, g_load_calls, g_save_fail, g_load_fail;
static project_config_t g_store;
static int g_store_valid;

static struct rt_device g_dev;
static struct rt_adc_device g_adc;
static rt_uint32_t g_adc_raw = 2000;
static int g_adc_reads, g_adc_enables;

static int cal_eq(const current_adc_cal_t *a, const current_adc_cal_t *b)
{
    return a->offset_mv == b->offset_mv && a->gain_v_per_a == b->gain_v_per_a &&
           a->source == b->source && a->version == b->version;
}

static int cfg_eq(const project_config_t *a, const project_config_t *b)
{
    return a->magic == b->magic && a->version == b->version &&
           a->cur_offset_mv == b->cur_offset_mv &&
           a->cur_gain_v_per_a == b->cur_gain_v_per_a &&
           a->cur_cal_source == b->cur_cal_source &&
           a->band_hz[0] == b->band_hz[0] && a->band_hz[1] == b->band_hz[1] &&
           a->pre_fault_ms == b->pre_fault_ms && a->post_fault_ms == b->post_fault_ms;
}

int cc_kprintf(const char *fmt, ...)
{
    char tmp[1024];
    va_list ap;
    int n;

    va_start(ap, fmt);
    n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0) return n;
    if (n > (int)sizeof(tmp) - 1) n = (int)sizeof(tmp) - 1;
    fputs(tmp, stdout);
    if (g_cap_len + n >= (int)sizeof(g_cap)) n = (int)sizeof(g_cap) - 1 - g_cap_len;
    if (n > 0)
    {
        memcpy(g_cap + g_cap_len, tmp, (size_t)n);
        g_cap_len += n;
        g_cap[g_cap_len] = '\0';
    }
    return n;
}

static void cap_reset(void)
{
    g_cap_len = 0;
    g_cap[0] = '\0';
    g_nul_seen = 0;
}

static int cap_has(const char *s) { return strstr(g_cap, s) != NULL; }

static int cap_line_max(const char *prefix)
{
    int i = 0, best = -1;
    size_t pl = strlen(prefix);
    while (i < g_cap_len)
    {
        int j = i;
        while (j < g_cap_len && g_cap[j] != '\n') j++;
        if (j - i >= (int)pl && memcmp(g_cap + i, prefix, pl) == 0 && j - i > best)
            best = j - i;
        i = j + 1;
    }
    return best;
}

static void cap_check_nul(void)
{
    int i;
    for (i = 0; i < g_cap_len; ++i) if (g_cap[i] == '\0') g_nul_seen = 1;
}

static void frame_reset(void)
{
    memset(g_frames, 0, sizeof(g_frames));
    g_frame_count = 0;
    g_frame_idx = 0;
    g_advance_on_tick = 1;
}

static void frame_add(rt_uint32_t seq, rt_uint32_t tick, rt_uint32_t raw,
                      rt_uint8_t valid, rt_uint8_t fresh,
                      rt_uint8_t src, float ma)
{
    sensor_frame_t *f;
    if (g_frame_count >= (int)(sizeof(g_frames) / sizeof(g_frames[0]))) return;
    f = &g_frames[g_frame_count++];
    f->seq = seq;
    f->timestamp = tick;
    f->current_raw = raw;
    f->valid_current = valid;
    f->fresh_current = fresh;
    f->current_cal_source = src;
    f->current_ma = ma;
}

/* ---------- RT-Thread / device / storage fakes ---------- */
rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{ (void)flags; m->name = name; m->inited = RT_TRUE; return RT_EOK; }
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout)
{ (void)timeout; if (!m->inited) return -RT_EIO; g_mutex_takes++; return RT_EOK; }
rt_err_t rt_mutex_release(struct rt_mutex *m)
{ if (!m->inited) return -RT_EIO; g_mutex_releases++; return RT_EOK; }
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }

void rt_enter_critical(void) { g_crit_depth++; }
void rt_exit_critical(void) { if (g_crit_depth > 0) g_crit_depth--; }
rt_bool_t rt_interrupt_get_nest(void) { return g_nest; }
rt_thread_t rt_thread_self(void) { return g_thread_ok ? &g_fake_thread : RT_NULL; }
rt_bool_t sp_intr_masked(void) { return g_masked; }

rt_tick_t rt_tick_get(void) { return g_tick; }
void rt_thread_mdelay(rt_int32_t ms)
{
    if (ms > 0) g_tick += (rt_tick_t)ms;
    if (g_advance_on_tick && g_frame_count > 0 &&
        g_frame_idx < g_frame_count - 1)
        g_frame_idx++;
    if (g_mdelay_cb != NULL) { void (*cb)(void) = g_mdelay_cb; g_mdelay_cb = NULL; cb(); }
}

rt_device_t rt_device_find(const char *name) { (void)name; return (rt_device_t)&g_dev; }
rt_err_t rt_adc_enable(rt_adc_device_t dev, int ch)
{ (void)dev; (void)ch; g_adc_enables++; return RT_EOK; }
rt_uint32_t rt_adc_read(rt_adc_device_t dev, int ch)
{ (void)dev; (void)ch; g_adc_reads++; return g_adc_raw; }

rt_err_t ns_storage_init(void) { return RT_EOK; }
rt_err_t ns_params_save(const void *data, rt_size_t length)
{
    g_save_calls++;
    if (g_save_fail) return -RT_EIO;
    if (data == RT_NULL || length > sizeof(g_store)) return -RT_EINVAL;
    memcpy(&g_store, data, length);
    g_store_valid = 1;
    return RT_EOK;
}
rt_err_t ns_params_load(void *data, rt_size_t capacity, rt_size_t *length)
{
    g_load_calls++;
    if (g_load_fail) return -RT_EIO;
    if (!g_store_valid) return -RT_ENOSYS;
    if (length) *length = sizeof(g_store);
    if (data == RT_NULL || capacity < sizeof(g_store)) return -RT_EINVAL;
    memcpy(data, &g_store, sizeof(g_store));
    return RT_EOK;
}

rt_err_t sensor_service_get_latest(sensor_frame_t *out)
{
    if (out == RT_NULL) return -RT_EINVAL;
    if (g_frame_count == 0) { memset(out, 0, sizeof(*out)); return RT_EOK; }
    *out = g_frames[g_frame_idx];
    return RT_EOK;
}

rt_err_t motor_calibration_static_begin(void)
{
    g_motor_begin++;
    if (g_motor_rc != RT_EOK) return g_motor_rc;
    g_motor_held = 1;
    return RT_EOK;
}
void motor_calibration_static_end(void)
{
    g_motor_end++;
    g_motor_held = 0;
}

/* ---------- real product translation units ---------- */
#include "current_adc.c"
#include "project_config.c"
#include "current_cal.c"

static void cc_change_cal_once(void)
{
    (void)current_adc_set_calibration_ex(1652.0f, 0.6f,
                                         CURRENT_ADC_CAL_THEORETICAL);
}

/* ---------- test harness ---------- */
static int g_run, g_pass, g_fail, g_pre_bad;

static void check(const char *id, int ok, const char *detail)
{
    g_run++;
    if (ok) g_pass++; else g_fail++;
    printf("CASE|%s|%s|%s\n", id, ok ? "PASS" : "FAIL", detail);
}

static void setup(void)
{
    frame_reset();
    g_cap_len = 0; g_cap[0] = '\0';
    g_nest = g_masked = 0; g_thread_ok = 1;
    g_tick = 0; g_crit_depth = 0;
    g_mutex_takes = g_mutex_releases = 0;
    g_motor_rc = RT_EOK; g_motor_begin = g_motor_end = g_motor_held = 0;
    g_save_calls = g_load_calls = 0; g_save_fail = g_load_fail = 0;
    g_store_valid = 0;
    memset(&g_store, 0, sizeof(g_store));
    g_adc_raw = 2000; g_adc_reads = g_adc_enables = 0;

    if (current_adc_init() != RT_EOK) { printf("FATAL|adc_init\n"); exit(2); }
    if (project_config_boot() != RT_EOK) { printf("FATAL|cfg_boot\n"); exit(2); }
    config_default();  /* real project_config command: valid + THEORETICAL */
    cap_reset();
}

static int run_cmd(int argc, const char **argv)
{
    char *a[8];
    int i;
    for (i = 0; i < argc && i < 8; ++i) a[i] = (char *)argv[i];
    current_cal_cmd(argc, a);
    return 0;
}

static void fill_frames(int n, rt_uint32_t raw_base)
{
    int i;
    frame_reset();
    for (i = 0; i < n; ++i)
        frame_add((rt_uint32_t)(i + 1), (rt_uint32_t)(i + 1),
                  raw_base + (rt_uint32_t)(i % 100), 1, 1,
                  CURRENT_ADC_CAL_THEORETICAL, 20.0f);
}

static void test_show(void)
{
    current_adc_cal_t c0, c1;
    project_config_t p0, p1;
    rt_uint32_t ver0;
    int save0, m0, adc0;

    setup();
    fill_frames(3, 2000);
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    ver0 = c0.version; save0 = g_save_calls; m0 = g_motor_begin; adc0 = g_adc_reads;
    cap_reset();
    run_cmd(2, (const char *[]){ "current_cal", "show" });
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    check("CC01-show_no_side_effects",
          cap_has("[CAL] adc health=") && cap_has("[CAL] sample seq=") &&
          cap_has("[CAL] note:") && cal_eq(&c0, &c1) &&
          cfg_eq(&p0, &p1) && g_save_calls == save0 &&
          g_motor_begin == m0 && g_adc_reads == adc0,
          "show must not change cal/cfg/Flash/motor or sample ADC");
}

static void test_sample_ok(void)
{
    current_adc_cal_t c0, c1;
    project_config_t p0, p1;
    int save0, m0, adc0;

    setup();
    fill_frames(128, 2000);
    g_tick = 0;
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    save0 = g_save_calls; m0 = g_motor_begin; adc0 = g_adc_reads;
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "128" });
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    check("CC02-sample_128_normal",
          cap_has("n_valid=128") && cap_has("fit=READY reason=OK") &&
          cal_eq(&c0, &c1) && cfg_eq(&p0, &p1) && g_save_calls == save0 &&
          g_motor_begin == m0 && g_adc_reads == adc0,
          "sample is observation only; no cal/cfg/Flash/motor/ADC write");
}

static void test_sample_bad(void)
{
    setup();
    frame_reset();
    /* duplicate seq + invalid + gap; never reaches 32 valid */
    frame_add(1, 1, 2000, 1, 1, CURRENT_ADC_CAL_THEORETICAL, 20.0f);
    frame_add(1, 2, 2001, 1, 1, CURRENT_ADC_CAL_THEORETICAL, 20.0f);
    frame_add(4, 4, 2002, 0, 0, CURRENT_ADC_CAL_THEORETICAL, 0.0f);
    frame_add(10, 10, 2003, 1, 1, CURRENT_ADC_CAL_THEORETICAL, 20.0f);
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "32" });
    check("CC03-sample_dup_invalid_skip",
          cap_has("dup=") && cap_has("invalid=") && cap_has("skip=") &&
          cap_has("fit=NO reason=INSUFFICIENT"),
          "duplicate/invalid/gap counted; insufficient rejected");

    setup();
    fill_frames(32, 4000);
    g_frames[31].current_raw = 4095;
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "32" });
    check("CC04-sample_endpoint_rejected",
          cap_has("fit=NO reason=ENDPOINT"), "endpoint not fit-ready");

    setup(); /* no frames: bounded deadline, no FROREVER */
    g_tick = 0;
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "32" });
    check("CC05-sample_no_frames_bounded",
          g_tick > 0 && g_tick <= 1200 && cap_has("fit=NO reason=INSUFFICIENT"),
          "no-frame wait bounded by deadline");
}

static void test_context_and_args(void)
{
    setup();
    fill_frames(64, 2000);
    g_nest = 1;
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "bad" });
    check("CC06-isr_silent", g_cap_len == 0 && g_frame_idx == 0,
          "ISR context silent, no frame wait");
    g_nest = 0; g_masked = 1;
    cap_reset();
    run_cmd(2, (const char *[]){ "current_cal", "show" });
    check("CC07-masked_silent", g_cap_len == 0, "PRIMASK context silent");
    g_masked = 0; g_thread_ok = 0;
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "apply", "1650000", "600000", "CONFIRM_MEASURED" });
    check("CC08-no_thread_silent", g_cap_len == 0 && g_motor_begin == 0,
          "no thread object silent, no static gate");
    g_thread_ok = 1;

    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "31" });
    check("CC09-bad_n_usage", cap_has("usage: current_cal sample 32..256"),
          "N below range rejected");
    cap_reset();
    run_cmd(4, (const char *[]){ "current_cal", "apply", "1650000", "600000" });
    check("CC10-missing_confirm_usage", cap_has("usage: current_cal apply"),
          "apply missing confirmation rejected");
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "nominal", "confirm" });
    check("CC11-bad_nominal_confirm", cap_has("usage: current_cal nominal"),
          "nominal confirm is case-sensitive");
}

static void test_numeric_reject(void)
{
    current_adc_cal_t c0, c1;
    project_config_t p0, p1;
    const char *bad[][6] = {
        { "current_cal", "apply", "1650000", "0", "CONFIRM_MEASURED" },
        { "current_cal", "apply", "1650000", "100000001", "CONFIRM_MEASURED" },
        { "current_cal", "apply", "3300001", "600000", "CONFIRM_MEASURED" },
        { "current_cal", "apply", "-1", "600000", "CONFIRM_MEASURED" },
        { "current_cal", "apply", "1650000", "600000x", "CONFIRM_MEASURED" },
        { "current_cal", "apply", "1650000", "600000", "CONFIRM_MEASURED", "x" },
    };
    int i;

    setup();
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    for (i = 0; i < (int)(sizeof(bad) / sizeof(bad[0])); ++i)
    {
        int argc = (i == 5) ? 6 : 5;
        cap_reset();
        run_cmd(argc, bad[i]);
        if (g_motor_begin != 0) { g_pre_bad++; break; }
    }
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    check("CC12-apply_numeric_reject",
          g_motor_begin == 0 && g_motor_end == 0 &&
          cal_eq(&c0, &c1) && cfg_eq(&p0, &p1),
          "bad numeric/extra args rejected before static gate; tuple unchanged");
}

static void test_static_and_apply(void)
{
    current_adc_cal_t c0, c1;
    project_config_t p0, p1, p2;
    rt_uint32_t v0;
    int save0;

    setup();
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    g_motor_rc = -RT_EBUSY;
    cap_reset();
    run_cmd(5, (const char *[]){ "current_cal", "apply", "1650000", "600000", "CONFIRM_MEASURED" });
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    check("CC13-apply_static_refused",
          cap_has("REFUSED") && g_motor_begin == 1 && g_motor_end == 0 &&
          cal_eq(&c0, &c1) && cfg_eq(&p0, &p1),
          "static gate refusal leaves tuple/cfg unchanged");

    setup();
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    v0 = c0.version; save0 = g_save_calls;
    g_motor_rc = RT_EOK;
    cap_reset();
    run_cmd(5, (const char *[]){ "current_cal", "apply", "1651000", "610000", "CONFIRM_MEASURED" });
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    check("CC14-apply_success_measured",
          cap_has("apply OK src=MEASURED") && c1.source == CURRENT_ADC_CAL_MEASURED &&
          c1.version == v0 + 1 && c1.offset_mv > 1650.9f && c1.offset_mv < 1651.1f &&
          p1.cur_cal_source == CURRENT_ADC_CAL_MEASURED &&
          p1.cur_offset_mv == c1.offset_mv && g_save_calls == save0 &&
          g_motor_begin == 1 && g_motor_end == 1,
          "apply changes tuple+cfg, RAM only, one static transaction");
    check("CC15-apply_other_cfg_kept",
          p1.band_hz[0] == p0.band_hz[0] && p1.sg_warn[0] == p0.sg_warn[0] &&
          p1.cur_warn_ma[0] == p0.cur_warn_ma[0] &&
          p1.pre_fault_ms == p0.pre_fault_ms,
          "apply/nominal only touch cal fields");

    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "nominal", "CONFIRM" });
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p2);
    check("CC16-nominal_theoretical",
          cap_has("nominal OK src=THEORETICAL") &&
          c1.source == CURRENT_ADC_CAL_THEORETICAL &&
          c1.offset_mv > 1649.9f && c1.offset_mv < 1650.1f &&
          c1.gain_v_per_a > 0.5999f && c1.gain_v_per_a < 0.6001f &&
          p2.cur_cal_source == CURRENT_ADC_CAL_THEORETICAL &&
          p2.band_hz[0] == p0.band_hz[0],
          "nominal restores only cal/source");
}

static void test_config_order_save_load(void)
{
    current_adc_cal_t c;
    project_config_t a, b, out;
    rt_err_t e;

    setup();
    project_config_get_snapshot(&a);
    a.cur_offset_mv = 1700.0f; a.cur_gain_v_per_a = 0.7f;
    a.cur_cal_source = CURRENT_ADC_CAL_THEORETICAL;
    b = a; b.cur_offset_mv = 1800.0f; b.cur_gain_v_per_a = 0.8f;
    e = project_config_set(&a);
    if (e == RT_EOK) e = project_config_set(&b);
    current_adc_get_calibration(&c);
    project_config_get_snapshot(&out);
    check("CC17-config_publish_order",
          e == RT_EOK && out.cur_offset_mv == 1800.0f &&
          out.cur_gain_v_per_a == 0.8f && c.offset_mv == 1800.0f &&
          c.gain_v_per_a == 0.8f,
          "runtime follows the last published tuple");

    setup();
    e = project_config_set_calibration(1750.0f, 0.75f, CURRENT_ADC_CAL_MEASURED);
    if (e == RT_EOK) e = project_config_save();
    if (e == RT_EOK) project_config_defaults();
    if (e == RT_EOK) e = project_config_load();
    current_adc_get_calibration(&c);
    check("CC18-save_load_preserves_measured",
          e == RT_EOK && g_save_calls == 1 && c.source == CURRENT_ADC_CAL_MEASURED &&
          c.offset_mv > 1749.9f && c.offset_mv < 1750.1f &&
          c.gain_v_per_a > 0.7499f && c.gain_v_per_a < 0.7501f,
          "save/load preserves source+coefficients");
    g_load_fail = 1;
    e = project_config_load();
    current_adc_get_calibration(&c);
    check("CC19-load_failure_theoretical",
          e != RT_EOK && c.source == CURRENT_ADC_CAL_THEORETICAL &&
          c.offset_mv > 1649.9f && c.offset_mv < 1650.1f,
          "load failure returns safe THEORETICAL");
}

static void test_snapshot_consistency(void)
{
    current_adc_cal_t a, got;
    float mv, ma;
    rt_uint32_t raw;

    setup();
    if (current_adc_set_calibration_ex(1700.0f, 0.7f,
                                       CURRENT_ADC_CAL_THEORETICAL) != RT_EOK)
        g_pre_bad++;
    g_adc_raw = 2048;
    if (current_adc_read_measurement_cal(&raw, &mv, &ma, &got) != RT_EOK)
        g_pre_bad++;
    current_adc_get_calibration(&a);
    {
        char dbg[160];
        snprintf(dbg, sizeof(dbg), "got v=%u s=%u o=%.4f g=%.4f a v=%u s=%u o=%.4f g=%.4f raw=%u mv=%.4f",
                 (unsigned)got.version, (unsigned)got.source, got.offset_mv, got.gain_v_per_a,
                 (unsigned)a.version, (unsigned)a.source, a.offset_mv, a.gain_v_per_a,
                 (unsigned)raw, mv);
        check("CC20-cal_snapshot_same_read",
              got.version == a.version && got.source == a.source &&
              got.offset_mv == a.offset_mv && got.gain_v_per_a == a.gain_v_per_a &&
              raw == 2048 && mv > 1650.39f && mv < 1650.42f,
              dbg);
    }
}

static void test_sample_cal_changed(void)
{
    setup();
    fill_frames(64, 2000);
    g_mdelay_cb = cc_change_cal_once;
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "32" });
    check("CC23-sample_cal_changed",
          cap_has("changed=1") && cap_has("fit=NO reason=CAL_CHANGED"),
          "mid-sample cal/version change rejects fit");
}

static void test_sample_256(void)
{
    setup();
    fill_frames(256, 2000);
    cap_reset();
    run_cmd(3, (const char *[]){ "current_cal", "sample", "256" });
    check("CC24-sample_256_boundary",
          cap_has("n_valid=256") && cap_has("fit=READY reason=OK"),
          "256-point boundary accepted and bounded");
}

static void test_numeric_nan_inf(void)
{
    current_adc_cal_t c0, c1;
    project_config_t p0, p1;

    setup();
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    if (current_adc_set_calibration_ex(NAN, 0.6f, CURRENT_ADC_CAL_MEASURED) == RT_EOK ||
        current_adc_set_calibration_ex(1650.0f, INFINITY, CURRENT_ADC_CAL_MEASURED) == RT_EOK ||
        current_adc_set_calibration_ex(1650.0f, 0.0005f, CURRENT_ADC_CAL_MEASURED) == RT_EOK ||
        current_adc_set_calibration_ex(-1.0f, 0.6f, CURRENT_ADC_CAL_MEASURED) == RT_EOK)
        g_pre_bad++;
    p1 = p0;
    p1.cur_gain_v_per_a = NAN;
    if (project_config_set(&p1) == RT_EOK) g_pre_bad++;
    p1 = p0;
    p1.cur_offset_mv = INFINITY;
    if (project_config_set(&p1) == RT_EOK) g_pre_bad++;
    p1 = p0;
    p1.cur_gain_v_per_a = 0.0005f;
    if (project_config_set(&p1) == RT_EOK) g_pre_bad++;
    if (project_config_set_calibration(1650.0f, 0.6f, CURRENT_ADC_CAL_NONE) == RT_EOK)
        g_pre_bad++;
    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    check("CC25-nan_inf_range_reject",
          cal_eq(&c0, &c1) && cfg_eq(&p0, &p1),
          "NaN/Inf/min-gain/offset/source rejected without tuple change");
}

static void test_api_none(void)
{
    current_adc_cal_t c;
    setup();
    check("CC26-api_none_legal_config_rejects",
          current_adc_set_calibration_ex(1650.0f, 0.6f,
                                         CURRENT_ADC_CAL_NONE) == RT_EOK &&
          current_adc_get_calibration(&c) == RT_EOK &&
          c.source == CURRENT_ADC_CAL_NONE &&
          project_config_set_calibration(1650.0f, 0.6f,
                                         CURRENT_ADC_CAL_NONE) == -RT_EINVAL,
          "NONE remains legal in ADC API; config only accepts THEORETICAL/MEASURED");
    config_default();
}

static void test_export_and_lines(void)
{
    int n;
    setup();
    fill_frames(3, 2000);
    cap_reset();
    run_cmd(2, (const char *[]){ "current_cal", "show" });
    run_cmd(3, (const char *[]){ "current_cal", "sample", "32" });
    cap_check_nul();
    n = cap_line_max("[CAL]");
    check("CC21-key_lines_ascii_110",
          n > 0 && n <= 110 && g_nul_seen == 0,
          "all [CAL] lines <=110B and no NUL");
    check("CC22-msh_export_name",
          ps_mshref_current_cal_cmd != RT_NULL,
          "current_cal alias is exported");
}

/* ---- M7R1 复审新增接缝 ----------------------------------------------------
 * CC27 = R1b(取锁失败不得发布), CC28/CC29 = R2(µA 导出的范围与标记)。
 * 三条都判**真实产品函数的实际输出/副作用**, 不判自写的期望常量。 */

/* 从捕获缓冲里取 "prefix=<token>" 的第一段非空白/非换行 token。 */
static int cap_field(const char *prefix, char *out, size_t n)
{
    const char *p = strstr(g_cap, prefix);
    size_t i = 0;

    if (p == NULL) return 0;
    p += strlen(prefix);
    if (*p != '=') return 0;
    p++;
    while (*p != '\0' && *p != ' ' && *p != '\n' && i + 1 < n) out[i++] = *p++;
    out[i] = '\0';
    return i > 0;
}

static void test_cfg_lock_unavailable(void)
{
    current_adc_cal_t c0, c1;
    project_config_t p0, p1;
    rt_err_t e;
    int rel0, rel1, takes0;

    setup();
    current_adc_get_calibration(&c0);
    project_config_get_snapshot(&p0);
    takes0 = g_mutex_takes;
    rel0 = g_mutex_releases;

    /* 让 cfg 互斥量的 take 明确失败(夹具里 inited=FALSE => -RT_EIO)。
     * cfg_lock 来自本 TU 内的真实 project_config.c。 */
    cfg_lock.inited = RT_FALSE;
    e = project_config_set_calibration(1600.0f, 0.7f, CURRENT_ADC_CAL_MEASURED);
    cfg_lock.inited = RT_TRUE;
    rel1 = g_mutex_releases;          /* 必须在后面的读操作之前取, 否则 snapshot 自己的
                                        * 正常取放会被算成"未持有就 release"。 */

    current_adc_get_calibration(&c1);
    project_config_get_snapshot(&p1);
    /* 拆成三条可判身份, 而不是一个"与"起来的大条件: 复审要的是"拒绝且不写"这一整条
     * 合同, 但台账必须能说清到底是哪一半没守住。 */
    check("CC27-cfg_lock_unavailable_rejects", e != RT_EOK,
          "take failure must be reported to the caller as an error");
    check("CC27b-cfg_lock_unavailable_no_mutation",
          cal_eq(&c0, &c1) && cfg_eq(&p0, &p1) && c0.version == c1.version,
          "no ADC tuple, cfg working set or version changed on the rejected call");
    check("CC27c-cfg_lock_unavailable_no_release", rel1 == rel0,
          "release is never attempted for a lock that was not acquired");
    (void)takes0;
    /* 对照: 锁恢复正常后同一组参数必须真的发布 —— 否则上面三条可能只是恒拒绝。 */
    e = project_config_set_calibration(1600.0f, 0.7f, CURRENT_ADC_CAL_MEASURED);
    current_adc_get_calibration(&c1);
    check("CC27d-cfg_lock_available_publishes",
          e == RT_EOK && c1.version == c0.version + 1 &&
          fabs((double)c1.offset_mv - 1600.0) < 1e-3 &&
          fabs((double)c1.gain_v_per_a - 0.7) < 1e-6 &&
          c1.source == CURRENT_ADC_CAL_MEASURED,
          "control: once the lock is available the same call publishes and bumps version");
}

static void test_microamp_export_bounds(void)
{
    current_adc_cal_t cal;
    float ma = 0.0f, mv = 0.0f;
    rt_uint32_t raw = 0;
    char tok[32];
    double expect, got;
    int ok;

    setup();
    /* 合法参数: offset=0 mV, gain=0.001 V/A(下界)。raw 取接近满量程的最大值。 */
    if (project_config_set_calibration(0.0f, 0.001f, CURRENT_ADC_CAL_MEASURED) != RT_EOK)
        g_pre_bad++;
    g_adc_raw = 4094;
    if (current_adc_read_measurement_cal(&raw, &mv, &ma, &cal) != RT_EOK) g_pre_bad++;
    frame_add(1, 1, raw, 1, 1, cal.source, ma);
    cap_reset();
    run_cmd(2, (const char *[]){ "current_cal", "show" });

    ok = cap_field("sample ma_uA", tok, sizeof(tok)) != 0;
    got = atof(tok);
    expect = (double)ma * 1000.0;
    /* 关键判据: 打印值必须等于真实 mA 的 1000 倍(相对误差 1e-6 内), 且**大于** int32
     * 上界 —— 说明它没有被截断/环绕成 int32 里的假数。 */
    check("CC28-microamp_export_no_int32_wrap",
          ok && expect > 2147483647.0 && fabs(got - expect) <= expect * 1e-6,
          "legal minimum gain + near-full-scale raw exports the full >int32 microamp "
          "value instead of a wrapped/truncated number");
    cap_check_nul();
    check("CC28b-microamp_line_still_short",
          cap_line_max("[CAL]") > 0 && cap_line_max("[CAL]") <= 110 && !g_nul_seen,
          "the wide value keeps every [CAL] line <=110B with no NUL");
    /* 负方向(实测 mv < offset)必须带符号, 不能被当成无符号量。
     * 夹具的 get_latest 取 g_frames[g_frame_idx], 不清表就会读到上一段那条正数帧。 */
    frame_reset();
    if (project_config_set_calibration(1650.0f, 0.6f, CURRENT_ADC_CAL_MEASURED) != RT_EOK)
        g_pre_bad++;
    g_adc_raw = 100;
    if (current_adc_read_measurement_cal(&raw, &mv, &ma, &cal) != RT_EOK) g_pre_bad++;
    frame_add(2, 2, raw, 1, 1, cal.source, ma);
    cap_reset();
    run_cmd(2, (const char *[]){ "current_cal", "show" });
    ok = cap_field("sample ma_uA", tok, sizeof(tok)) != 0;
    check("CC28c-microamp_negative_signed",
          ok && ma < 0.0f && tok[0] == '-' &&
          fabs(atof(tok) - (double)ma * 1000.0) <= fabs((double)ma * 1000.0) * 1e-6,
          "negative derived current prints with its sign and correct magnitude");
}

static void test_scaled_dec_marker(void)
{
    char buf[24];
    int bad = 0;

    setup();
    /* 越界/NaN/Inf 必须在**转换之前**就被判掉, 并给出明确标记, 而不是一个数字。 */
    if (cc_scaled_dec(1.0e30f, 1000, buf, sizeof(buf)) != -RT_EINVAL ||
        strcmp(buf, "OUT_OF_RANGE") != 0) bad = 1;
    if (cc_scaled_dec(-1.0e30f, 1000, buf, sizeof(buf)) != -RT_EINVAL ||
        strcmp(buf, "OUT_OF_RANGE") != 0) bad = 1;
    if (cc_scaled_dec(NAN, 1000, buf, sizeof(buf)) != -RT_EINVAL ||
        strcmp(buf, "OUT_OF_RANGE") != 0) bad = 1;
    if (cc_scaled_dec(INFINITY, 1000, buf, sizeof(buf)) != -RT_EINVAL ||
        strcmp(buf, "OUT_OF_RANGE") != 0) bad = 1;
    /* 恰好越过 int32 边界的值必须原样发出去(这正是原缺陷会环绕的地方)。 */
    if (cc_scaled_dec(2147484.0f, 1000, buf, sizeof(buf)) != RT_EOK ||
        strcmp(buf, "2147484000") != 0) bad = 1;
    if (cc_scaled_dec(-2147484.0f, 1000, buf, sizeof(buf)) != RT_EOK ||
        strcmp(buf, "-2147484000") != 0) bad = 1;
    if (cc_scaled_dec(0.0f, 1000, buf, sizeof(buf)) != RT_EOK ||
        strcmp(buf, "0") != 0) bad = 1;
    /* 四舍五入远离零: .5 边界。 */
    if (cc_scaled_dec(0.0005f, 1000, buf, sizeof(buf)) != RT_EOK ||
        strcmp(buf, "1") != 0) bad = 1;
    if (cc_scaled_dec(-0.0005f, 1000, buf, sizeof(buf)) != RT_EOK ||
        strcmp(buf, "-1") != 0) bad = 1;
    check("CC29-scaled_dec_range_marker",
          !bad, "out-of-range/NaN/Inf emit the explicit marker; >int32 and negative "
                "values and zero round-trip exactly");
}

int main(void)
{
    printf("VARIANT|cc_host\n");
    test_show();
    test_sample_ok();
    test_sample_bad();
    test_context_and_args();
    test_numeric_reject();
    test_static_and_apply();
    test_config_order_save_load();
    test_snapshot_consistency();
    test_sample_cal_changed();
    test_sample_256();
    test_numeric_nan_inf();
    test_api_none();
    test_cfg_lock_unavailable();
    test_microamp_export_bounds();
    test_scaled_dec_marker();
    test_export_and_lines();
    printf("SUMMARY|cases=%d pass=%d fail=%d pre_bad=%d\n",
           g_run, g_pass, g_fail, g_pre_bad);
    return (g_fail || g_pre_bad) ? 1 : 0;
}
