/*
 * current_adc.c - 母线电流采样 (ADCA CH15 / PH2 / J1-3)
 *
 * 前端(用户自搭, 2026-09-13 接入 J1-3; 方案默认拓扑 §7.4):
 *   30mΩ 分流 + NSCSA240A1(20V/V) + 1.65V 中点
 *   Vadc ≈ 1.65V + Ibus × 0.03Ω × 20  →  Ibus(mA) ≈ (Vmv - 1650) / 0.6
 *   用户前端的实际分流/增益待确认 → 标定前 read_ma 结果为理论默认换算
 *   (health=DEGRADED), 标定后视为实测。
 *
 * 正式 API 见 current_adc.h; current_raw 为薄封装 MSH 命令。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "app_health.h"
#include "current_adc.h"

#define CUR_ADC_DEV     CURRENT_ADC_DEVICE_NAME   /* "adc0" */
#define CUR_ADC_CH      CURRENT_ADC_CHANNEL       /* 15 */
#define CUR_SAMPLES     64
#define CUR_VREF_MV     3300
#define CUR_RAW_MAX     4095
#define CUR_EMA_ALPHA_PCT 25    /* EMA: new = old + (sample-old)*25% */

static rt_adc_device_t cur_adc = RT_NULL;
static rt_bool_t cur_enabled = RT_FALSE;
static subsys_health_t cur_health = SUBSYS_UNINIT;

/* 标定 */
static float cur_offset_mv = 1650.0f;     /* 理论默认: 1.65V 中点 */
static float cur_gain_v_per_a = 0.6f;     /* 理论默认: 30mΩ × 20V/V */
static rt_bool_t cur_calibrated = RT_FALSE;
static current_adc_cal_source_t cur_cal_source = CURRENT_ADC_CAL_NONE;

/* 轻量滤波状态 */
static rt_uint32_t cur_ema_raw = 0;
static rt_bool_t cur_ema_valid = RT_FALSE;

static rt_err_t cur_sample_mean(rt_uint32_t *mean_out,
                                rt_uint32_t *min_out, rt_uint32_t *max_out)
{
    rt_uint32_t raw, sum = 0, min = 0xFFFFFFFF, max = 0;
    int i;

    if (cur_adc == RT_NULL || !cur_enabled) return -RT_ERROR;

    for (i = 0; i < CUR_SAMPLES; ++i)
    {
        raw = rt_adc_read(cur_adc, CUR_ADC_CH);
        sum += raw;
        if (raw < min) min = raw;
        if (raw > max) max = raw;
        rt_thread_mdelay(1);
    }
    *mean_out = sum / CUR_SAMPLES;
    if (min_out) *min_out = min;
    if (max_out) *max_out = max;
    return RT_EOK;
}

/* ---------- 正式 API (current_adc.h) ---------- */

rt_err_t current_adc_init(void)
{
    if (cur_health == SUBSYS_OK || cur_health == SUBSYS_DEGRADED)
        return RT_EOK;                      /* 幂等 */

    cur_adc = (rt_adc_device_t)rt_device_find(CUR_ADC_DEV);
    if (cur_adc == RT_NULL)
    {
        cur_health = SUBSYS_FAILED;
        return -RT_ERROR;
    }

    if (rt_adc_enable(cur_adc, CUR_ADC_CH) != RT_EOK)
    {
        cur_health = SUBSYS_FAILED;
        return -RT_ERROR;
    }
    cur_enabled = RT_TRUE;

    /* 已使能可读: MEASURED 标定=OK, 理论换算=DEGRADED */
    cur_health = (cur_cal_source == CURRENT_ADC_CAL_MEASURED) ? SUBSYS_OK
                                                              : SUBSYS_DEGRADED;
    return RT_EOK;
}

rt_err_t current_adc_read_measurement(rt_uint32_t *raw, float *mv, float *ma)
{
    rt_uint32_t sample;

    if (cur_adc == RT_NULL || !cur_enabled) return -RT_ERROR;

    /* 单次快读; EMA 一帧只推一次(P1-13: raw 与换算同源) */
    sample = rt_adc_read(cur_adc, CUR_ADC_CH);

    if (!cur_ema_valid) { cur_ema_raw = sample; cur_ema_valid = RT_TRUE; }
    else
    {
        rt_int32_t diff = (rt_int32_t)sample - (rt_int32_t)cur_ema_raw;
        cur_ema_raw = (rt_uint32_t)((rt_int32_t)cur_ema_raw +
                                    (diff * CUR_EMA_ALPHA_PCT) / 100);
    }

    *raw = sample;
    if (mv != RT_NULL) *mv = current_adc_raw_to_mv(sample);
    if (ma != RT_NULL) *ma = current_adc_raw_to_ma(sample);
    return RT_EOK;
}

rt_err_t current_adc_read_raw(rt_uint32_t *raw)
{
    return current_adc_read_measurement(raw, RT_NULL, RT_NULL);
}

float current_adc_raw_to_mv(rt_uint32_t raw)
{
    return (float)raw * CUR_VREF_MV / CUR_RAW_MAX;
}

float current_adc_raw_to_ma(rt_uint32_t raw)
{
    return (current_adc_raw_to_mv(raw) - cur_offset_mv)
           / cur_gain_v_per_a * 1000.0f;
}

rt_err_t current_adc_read_mv(float *mv)
{
    rt_uint32_t raw;
    return current_adc_read_measurement(&raw, mv, RT_NULL);
}

rt_err_t current_adc_read_ma(float *ma)
{
    rt_uint32_t raw;
    return current_adc_read_measurement(&raw, RT_NULL, ma);
}

static rt_err_t cal_apply(float offset_mv, float gain_v_per_a,
                          current_adc_cal_source_t source)
{
    if (gain_v_per_a <= 0.0f) return -RT_EINVAL;

    cur_offset_mv = offset_mv;
    cur_gain_v_per_a = gain_v_per_a;
    cur_cal_source = source;
    cur_calibrated = (source != CURRENT_ADC_CAL_NONE) ? RT_TRUE : RT_FALSE;
    if (cur_enabled)
        cur_health = (source == CURRENT_ADC_CAL_MEASURED) ? SUBSYS_OK
                                                          : SUBSYS_DEGRADED;
    return RT_EOK;
}

rt_err_t current_adc_set_calibration(float offset_mv, float gain_v_per_a)
{
    /* 理论默认值来源: 明确标 THEORETICAL, 绝不冒充实测(P1-7) */
    return cal_apply(offset_mv, gain_v_per_a, CURRENT_ADC_CAL_THEORETICAL);
}

rt_err_t current_adc_set_calibration_ex(float offset_mv, float gain_v_per_a,
                                        current_adc_cal_source_t source)
{
    return cal_apply(offset_mv, gain_v_per_a, source);
}

rt_bool_t current_adc_is_calibrated(void) { return cur_calibrated; }
current_adc_cal_source_t current_adc_cal_source(void) { return cur_cal_source; }
rt_uint32_t current_adc_get_filtered_raw(void) { return cur_ema_raw; }
subsys_health_t current_adc_get_health(void) { return cur_health; }

/* ---------- MSH 命令 (薄封装, current_raw 保留) ---------- */

static int current_adc_init_msh(void)
{
    rt_err_t e = current_adc_init();

    if (e != RT_EOK)
        rt_kprintf("[CUR] init FAILED (%d), health=%s\n",
                   e, subsys_health_name(current_adc_get_health()));
    else
        rt_kprintf("[CUR] adc0 ch15 enabled, calibrated=%s, health=%s\n",
                   current_adc_is_calibrated() ? "YES" : "NO(theoretical only)",
                   subsys_health_name(current_adc_get_health()));
    return RT_EOK;
}
INIT_APP_EXPORT(current_adc_init_msh);

static void current_raw(void)
{
    rt_uint32_t raw, mean, min = 0xFFFFFFFF, max = 0;
    int i, n = CUR_SAMPLES;
    float mv, ma;

    if (current_adc_init() != RT_EOK)
    {
        rt_kprintf("[CUR] adc not ready, health=%s\n",
                   subsys_health_name(current_adc_get_health()));
        return;
    }

    /* 保留原 min/max 展示: 单独采一轮 */
    for (i = 0; i < n; ++i)
    {
        raw = rt_adc_read(cur_adc, CUR_ADC_CH);
        if (raw < min) min = raw;
        if (raw > max) max = raw;
        rt_thread_mdelay(1);
    }

    if (current_adc_read_measurement(&raw, &mv, &ma) != RT_EOK)
    {
        rt_kprintf("[CUR] read failed\n");
        return;
    }
    mean = raw;

    rt_kprintf("[CUR] raw mean=%u min=%u max=%u ema=%u (n=%d)\n",
               mean, min, max, current_adc_get_filtered_raw(), n);
    rt_kprintf("[CUR] %s: %d.%03d mV, %d mA (health=%s)\n",
               current_adc_is_calibrated() ? "calibrated" : "theoretical",
               (int)mv, (int)(mv * 1000) % 1000, (int)ma,
               subsys_health_name(current_adc_get_health()));
}
MSH_CMD_EXPORT(current_raw, sample bus current ADC ch15 with min/max/mean);
