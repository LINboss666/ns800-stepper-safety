/*
 * current_adc.c - 母线电流采样 (ADCA CH15 / PH2 / J1-3)
 *
 * 前端(用户自搭, 2026-09-13 接入 J1-3; 方案默认拓扑 §7.4):
 *   30mΩ 分流 + NSCSA240A1(20V/V) + 1.65V 中点
 *   Vadc ≈ 1.65V + Ibus × 0.03Ω × 20  →  Ibus(mA) ≈ (Vmv - 1650) / 0.6
 *   用户前端的实际分流/增益待确认 → 标定前 read_ma 结果为理论默认换算
 *   (health=DEGRADED), 标定后视为实测。
 *
 * Fix B: 标定来源(NONE/THEORETICAL/MEASURED)是配置的一部分。project_config 现在
 *   通过 current_adc_set_calibration_ex() 原样还原来源, 所以保存为 MEASURED 的
 *   标定重启后仍是 MEASURED; 加载失败/未标定时显式回到 THEORETICAL。
 *   ⚠ 目前没有任何代码路径会把来源写成 MEASURED —— 真实零点/增益标定流程仍属
 *   实验阶段(HARDWARE-PENDING), 需要开发板与已知负载。
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

/* ---------- boot (P1-8: bootstrap 显式调用, 不用 INIT_APP) ---------- */

rt_err_t current_adc_boot(void)
{
    rt_err_t e = current_adc_init();

    if (e != RT_EOK)
        rt_kprintf("[CUR] init FAILED (%d), health=%s\n",
                   e, subsys_health_name(current_adc_get_health()));
    else
        rt_kprintf("[CUR] adc0 ch15 enabled, cal_source=%d, health=%s\n",
                   current_adc_cal_source(),
                   subsys_health_name(current_adc_get_health()));
    return e;
}

/* ---------- MSH 命令 (薄封装, current_raw 保留) ----------
 * Fix B: mv/ma 必须由真实采样换算后才打印。旧实现声明了 mv/ma 却从未赋值,
 * 直接 (int)mv / (int)ma 打印未初始化栈值(未定义行为), 因此历史上该命令给出
 * 的任何 mV/mA 数字都不成立, 只有 raw 列可信。
 *
 * Fix D: 标定标签一律按 current_adc_cal_source() 判定, 不用 is_calibrated()。
 * 因为 Fix B 把 defaults 的来源从 NONE 改成 THEORETICAL 之后,
 * is_calibrated()(= source != NONE) 变成 TRUE —— 拿它当"已标定"标签会让理论
 * 默认系数冒充实测, 正是本项目禁止的那类误导。 */

static const char *cur_cal_label(void)
{
    switch (current_adc_cal_source())
    {
    case CURRENT_ADC_CAL_MEASURED:    return "MEASURED";
    case CURRENT_ADC_CAL_THEORETICAL: return "THEORETICAL(NOT measured)";
    default:                          return "NO-CALIBRATION";
    }
}

static void current_raw(void)
{
    rt_uint32_t raw = 0, sum = 0, min = 0xFFFFFFFF, max = 0, mean;
    int i, n = CUR_SAMPLES;
    float mv, ma, mv_mean, ma_mean;

    if (current_adc_init() != RT_EOK)
    {
        rt_kprintf("[CUR] adc not ready, health=%s\n",
                   subsys_health_name(current_adc_get_health()));
        return;
    }

    /* 一次 64 点突发同时得到 sum/min/max(旧实现把同一个突发跑了两遍) */
    for (i = 0; i < n; ++i)
    {
        raw = (rt_uint32_t)rt_adc_read(cur_adc, CUR_ADC_CH);
        sum += raw;
        if (raw < min) min = raw;
        if (raw > max) max = raw;
        rt_thread_mdelay(1);
    }
    mean = sum / (rt_uint32_t)n;

    /* 换算基于真实采样值(全部先算后打印) */
    mv      = current_adc_raw_to_mv(raw);
    ma      = current_adc_raw_to_ma(raw);
    mv_mean = current_adc_raw_to_mv(mean);
    ma_mean = current_adc_raw_to_ma(mean);

    rt_kprintf("[CUR] latest=%u burst_mean=%u min=%u max=%u ema=%u (n=%d)\n",
               raw, mean, min, max, current_adc_get_filtered_raw(), n);
    rt_kprintf("[CUR] cal source=%s health=%s\n",
               cur_cal_label(), subsys_health_name(current_adc_get_health()));
    rt_kprintf("[CUR] latest-sample : %d.%03d mV, %d mA\n",
               (int)mv, ((int)(mv * 1000.0f)) % 1000, (int)ma);
    rt_kprintf("[CUR] burst-mean    : %d.%03d mV, %d mA\n",
               (int)mv_mean, ((int)(mv_mean * 1000.0f)) % 1000, (int)ma_mean);
    if (current_adc_cal_source() != CURRENT_ADC_CAL_MEASURED)
        rt_kprintf("[CUR] !! mA above is a %s conversion, NOT a measured current."
                   " Do not use it as protection evidence until MEASURED.\n",
                   current_adc_cal_source() == CURRENT_ADC_CAL_THEORETICAL ?
                   "theoretical-default" : "no-calibration");
}
MSH_CMD_EXPORT(current_raw, sample bus current ADC ch15 with min/max/mean);
