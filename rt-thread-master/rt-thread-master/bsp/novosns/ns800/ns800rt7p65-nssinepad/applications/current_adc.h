/*
 * current_adc.h - 母线电流采样正式 API (Phase 7-A)
 *
 * 硬件: ADCA CH15 / PH2 / J1-3。前端(用户自搭, 拓扑待确认): 方案默认为
 *       30mΩ 分流 + NSCSA240A1(20V/V) + 1.65V 中点 → Ibus(mA) ≈ (Vmv - 1650)/0.6
 *
 * 约定(见 app_health.h):
 *   - 读函数非 RT_EOK 时输出参数无效
 *   - 标定: current_adc_set_calibration() 设置 offset/gain 并置 calibration_valid;
 *     未标定时(read_ma)使用理论默认值换算, health=DEGRADED —— 数值仅供参考,
 *     调用方必须检查 current_adc_is_calibrated(), 禁止把理论值当实测电流
 *   - 轻量滤波: 内部维护 raw 的 EMA(指数滑动平均), 每次 read_raw 更新
 */
#ifndef CURRENT_ADC_H
#define CURRENT_ADC_H

#include <rtthread.h>
#include "app_health.h"

/* 标定来源(P1-7): 理论默认值绝不能冒充实测标定 */
typedef enum
{
    CURRENT_ADC_CAL_NONE = 0,
    CURRENT_ADC_CAL_THEORETICAL,
    CURRENT_ADC_CAL_MEASURED,
} current_adc_cal_source_t;

/* 幂等初始化: 查找 adc0 并使能 CH15。RT_EOK = 可读(raw 层面)。
 * P1-8: bootstrap 显式调用, 不再用 INIT_APP。 */
rt_err_t current_adc_init(void);
rt_err_t current_adc_boot(void);

/* 读取 CH15 原始码(12bit, 0~4095, 单次快读 + EMA 状态更新; 适合 100Hz 线程)。
 * RT_EOK 时 *raw 有效; 滤波值用 current_adc_get_filtered_raw()。 */
rt_err_t current_adc_read_raw(rt_uint32_t *raw);

/* P1-13: 一次采样完成 raw/mv/ma, 保证同一采样周期, EMA 只推一次。
 * mv/ma 传 RT_NULL 可省略。RT_EOK 时非 NULL 输出全部有效。 */
rt_err_t current_adc_read_measurement(rt_uint32_t *raw, float *mv, float *ma);

/* 纯换算(不采样): raw → mv / ma(用当前 offset/gain) */
float current_adc_raw_to_mv(rt_uint32_t raw);
float current_adc_raw_to_ma(rt_uint32_t raw);

/* 原始码 → 毫伏(线性: raw × 3300 / 4095)。RT_EOK 时 *mv 有效。 */
rt_err_t current_adc_read_mv(float *mv);

/* 毫伏 → 电流 mA(用当前 offset/gain, 未标定时为理论默认)。
 * RT_EOK 时 *ma 有效但可能是理论值 —— 用 current_adc_is_calibrated() 区分。 */
rt_err_t current_adc_read_ma(float *ma);

/* 设置标定(source=THEORETICAL): 理论默认值, 不得冒充实测。 */
rt_err_t current_adc_set_calibration(float offset_mv, float gain_v_per_a);

/* 设置标定(显式来源): 只有 MEASURED 才允许 diagnosis ACTIVE_PROTECTION。 */
rt_err_t current_adc_set_calibration_ex(float offset_mv, float gain_v_per_a,
                                        current_adc_cal_source_t source);

/* 是否已标定(来源 != NONE; 区分来源用 current_adc_cal_source()) */
rt_bool_t current_adc_is_calibrated(void);

/* 当前标定来源 */
current_adc_cal_source_t current_adc_cal_source(void);

/* 获取最近一次 EMA 滤波后的 raw 值(诊断/记录用) */
rt_uint32_t current_adc_get_filtered_raw(void);

/* 子系统健康:
 *   UNINIT 未初始化; OK 已使能且已标定;
 *   DEGRADED 已使能但未标定(可用,数值为理论值); FAILED 设备缺失/使能失败 */
subsys_health_t current_adc_get_health(void);

#endif /* CURRENT_ADC_H */
