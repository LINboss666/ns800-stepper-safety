/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author       Notes
 * 2026-06-15     Eric    Initial version.
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "syscon.h"
#include "drv_epwm.h"

#if defined(BSP_USING_EPWM)

#define DBG_TAG     "drv.epwm"
#define DBG_LVL     DBG_INFO
#include <rtdbg.h>

enum
{
#ifdef BSP_USING_EPWM1
    EPWM1_INDEX,
#endif
#ifdef BSP_USING_EPWM2
    EPWM2_INDEX,
#endif
#ifdef BSP_USING_EPWM3
    EPWM3_INDEX,
#endif
#ifdef BSP_USING_EPWM4
    EPWM4_INDEX,
#endif
#ifdef BSP_USING_EPWM5
    EPWM5_INDEX,
#endif
#ifdef BSP_USING_EPWM6
    EPWM6_INDEX,
#endif
#ifdef BSP_USING_EPWM7
    EPWM7_INDEX,
#endif
#ifdef BSP_USING_EPWM8
    EPWM8_INDEX,
#endif
#ifdef BSP_USING_EPWM9
    EPWM9_INDEX,
#endif
#ifdef BSP_USING_EPWM10
    EPWM10_INDEX,
#endif
#ifdef BSP_USING_EPWM11
    EPWM11_INDEX,
#endif
#ifdef BSP_USING_EPWM12
    EPWM12_INDEX,
#endif
#ifdef BSP_USING_EPWM13
    EPWM13_INDEX,
#endif
#ifdef BSP_USING_EPWM14
    EPWM14_INDEX,
#endif
#ifdef BSP_USING_EPWM15
    EPWM15_INDEX,
#endif
#ifdef BSP_USING_EPWM16
    EPWM16_INDEX,
#endif
#ifdef BSP_USING_EPWM17
    EPWM17_INDEX,
#endif
#ifdef BSP_USING_EPWM18
    EPWM18_INDEX,
#endif
    /* keep the enum non-empty when no EPWM channel is enabled */
    NS800_EPWM_IDX_PLACEHOLDER = 0,
};

struct ns800_epwm_pin_config
{
    GPIO_TypeDef *port_a;
    GPIO_PinNum pin_a;
    GPIO_AltFunc mux_a;
    GPIO_TypeDef *port_b;
    GPIO_PinNum pin_b;
    GPIO_AltFunc mux_b;
};

struct ns800_epwm_config
{
    const char *name;
    EPWM_TypeDef *instance;
    EPWM_TimeBaseCountMode count_mode;
    EPWM_ClockDivider clock_div;
    EPWM_HSClockDivider hs_clock_div;
};

struct ns800_signal_params
{
    float   signal_freq;        /*!< Desired Signal Frequency(in Hz) */
    float   duty_val;           /*!< Desired ePWMxA/B Signal Duty */
    float   sys_clk_freq;       /*!< SYSCLK Frequency(in Hz) */
    float   dead_band_val;
    rt_bool_t invert_signal_b;
    EPWM_TimeBaseCountMode  count_mode;
    EPWM_ClockDivider clock_div;
    EPWM_HSClockDivider hs_clock_div;
};

struct ns800_epwm
{
    struct rt_device_pwm epwm_device;
    const struct ns800_epwm_pin_config *pin_config;
    const struct ns800_epwm_config *epwm_config;
    rt_uint32_t current_period;
    rt_uint32_t current_pulse;
};

static const struct ns800_epwm_pin_config epwm_pins[] =
{
#ifdef BSP_USING_EPWM1
    [EPWM1_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_0, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_1, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM2
    [EPWM2_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_2, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_3, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM3
    [EPWM3_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_4, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_5, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM4
    [EPWM4_INDEX] = EPWM_PIN_INFO(GPIOB, GPIO_PIN_14, ALT1_FUNCTION,
                                GPIOB, GPIO_PIN_15, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM5
    [EPWM5_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_8, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_9, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM6
    [EPWM6_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_10, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_11, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM7
    [EPWM7_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_12, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_13, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM8
    [EPWM8_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_14, ALT1_FUNCTION,
                                GPIOA, GPIO_PIN_15, ALT1_FUNCTION),
#endif
#ifdef BSP_USING_EPWM9
    [EPWM9_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_16, ALT5_FUNCTION,
                                GPIOA, GPIO_PIN_17, ALT5_FUNCTION),
#endif
#ifdef BSP_USING_EPWM10
    [EPWM10_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_18, ALT5_FUNCTION,
                                GPIOA, GPIO_PIN_19, ALT5_FUNCTION),
#endif
#ifdef BSP_USING_EPWM11
    [EPWM11_INDEX] = EPWM_PIN_INFO(GPIOA, GPIO_PIN_20, ALT5_FUNCTION,
                                GPIOA, GPIO_PIN_21, ALT5_FUNCTION),
#endif
#ifdef BSP_USING_EPWM12 /*failed*/
    [EPWM12_INDEX] = EPWM_PIN_INFO(GPIOF, GPIO_PIN_12, ALT5_FUNCTION,
                                GPIOA, GPIO_PIN_23, ALT5_FUNCTION),
#endif
#ifdef BSP_USING_EPWM13
    [EPWM13_INDEX] = EPWM_PIN_INFO(GPIOF, GPIO_PIN_14, ALT13_FUNCTION,
                                GPIOF, GPIO_PIN_15, ALT13_FUNCTION),
#endif
#ifdef BSP_USING_EPWM14
    [EPWM14_INDEX] = EPWM_PIN_INFO(GPIOF, GPIO_PIN_16, ALT13_FUNCTION,
                                GPIOF, GPIO_PIN_17, ALT13_FUNCTION),
#endif
#ifdef BSP_USING_EPWM15
    [EPWM15_INDEX] = EPWM_PIN_INFO(GPIOF, GPIO_PIN_18, ALT13_FUNCTION,
                                GPIOF, GPIO_PIN_19, ALT13_FUNCTION),
#endif
#ifdef BSP_USING_EPWM16
    [EPWM16_INDEX] = EPWM_PIN_INFO(GPIOF, GPIO_PIN_20, ALT13_FUNCTION,
                                GPIOB, GPIO_PIN_23, ALT3_FUNCTION),
#endif
#ifdef BSP_USING_EPWM17
    [EPWM17_INDEX] = EPWM_PIN_INFO(GPIOB, GPIO_PIN_24, ALT3_FUNCTION,
                                GPIOB, GPIO_PIN_25, ALT3_FUNCTION),
#endif
#ifdef BSP_USING_EPWM18
    [EPWM18_INDEX] = EPWM_PIN_INFO(GPIOD, GPIO_PIN_8, ALT2_FUNCTION,
                                GPIOD, GPIO_PIN_9, ALT2_FUNCTION),
#endif
};

static const struct ns800_epwm_config epwm_conf[] =
{
#ifdef BSP_USING_EPWM1
    [EPWM1_INDEX] = EPWM_DRV_INIT(1, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM2
    [EPWM2_INDEX] = EPWM_DRV_INIT(2, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM3
    [EPWM3_INDEX] = EPWM_DRV_INIT(3, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM4
    [EPWM4_INDEX] = EPWM_DRV_INIT(4, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM5
    [EPWM5_INDEX] = EPWM_DRV_INIT(5, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM6
    [EPWM6_INDEX] = EPWM_DRV_INIT(6, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM7
    [EPWM7_INDEX] = EPWM_DRV_INIT(7, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM8
    [EPWM8_INDEX] = EPWM_DRV_INIT(8, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM9
    [EPWM9_INDEX] = EPWM_DRV_INIT(9, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM10
    [EPWM10_INDEX] = EPWM_DRV_INIT(10, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM11
    [EPWM11_INDEX] = EPWM_DRV_INIT(11, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM12
    [EPWM12_INDEX] = EPWM_DRV_INIT(12, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM13
    [EPWM13_INDEX] = EPWM_DRV_INIT(13, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM14
    [EPWM14_INDEX] = EPWM_DRV_INIT(14, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM15
    [EPWM15_INDEX] = EPWM_DRV_INIT(15, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM16
    [EPWM16_INDEX] = EPWM_DRV_INIT(16, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM17
    [EPWM17_INDEX] = EPWM_DRV_INIT(17, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
#ifdef BSP_USING_EPWM18
    [EPWM18_INDEX] = EPWM_DRV_INIT(18, EPWM_COUNTER_MODE_UP_DOWN,
                        EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1),
#endif
};

static struct ns800_epwm ns800_epwm_obj[sizeof(epwm_conf) / sizeof(epwm_conf[0])];

static void ns800_epwm_gpio_init(const struct ns800_epwm_pin_config *pin_config)
{
    GPIO_setAnalogMode(pin_config->port_a, pin_config->pin_a, GPIO_ANALOG_DISABLED);
    GPIO_setPinConfig(pin_config->port_a, pin_config->pin_a, pin_config->mux_a);
    GPIO_setPadConfig(pin_config->port_a, pin_config->pin_a, GPIO_PIN_TYPE_STD);
    GPIO_setQualificationMode(pin_config->port_a, pin_config->pin_a, GPIO_QUAL_SYNC);

    GPIO_setAnalogMode(pin_config->port_b, pin_config->pin_b, GPIO_ANALOG_DISABLED);
    GPIO_setPinConfig(pin_config->port_b, pin_config->pin_b, pin_config->mux_b);
    GPIO_setPadConfig(pin_config->port_b, pin_config->pin_b, GPIO_PIN_TYPE_STD);
    GPIO_setQualificationMode(pin_config->port_b, pin_config->pin_b, GPIO_QUAL_SYNC);
}

static void ns800_epwm_hw_init(EPWM_TypeDef *epwm)
{
    EPWM_setClockPrescaler(epwm, EPWM_CLOCK_DIVIDER_1, EPWM_HSCLOCK_DIVIDER_2);
    EPWM_setTimeBasePeriod(epwm, 0);
    EPWM_setTimeBaseCounter(epwm, 0);
    EPWM_setTimeBaseCounterMode(epwm, EPWM_COUNTER_MODE_STOP_FREEZE);
    EPWM_disablePhaseShiftLoad(epwm);
    EPWM_setPhaseShift(epwm, 0);
    EPWM_setCounterCompareValue(epwm, EPWM_COUNTER_COMPARE_A, 0);
    EPWM_setCounterCompareShadowLoadMode(epwm, EPWM_COUNTER_COMPARE_A, EPWM_COMP_LOAD_ON_CNTR_ZERO);
    EPWM_setCounterCompareValue(epwm, EPWM_COUNTER_COMPARE_B, 0);
    EPWM_setCounterCompareShadowLoadMode(epwm, EPWM_COUNTER_COMPARE_B, EPWM_COMP_LOAD_ON_CNTR_ZERO);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_PERIOD);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPA);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPA);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_PERIOD);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPA);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPA);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB);
    EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_NO_CHANGE, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB);
    EPWM_setRisingEdgeDelayCountShadowLoadMode(epwm, EPWM_RED_LOAD_ON_CNTR_ZERO);
    EPWM_disableRisingEdgeDelayCountShadowLoadMode(epwm);
    EPWM_setFallingEdgeDelayCountShadowLoadMode(epwm, EPWM_FED_LOAD_ON_CNTR_ZERO);
    EPWM_disableFallingEdgeDelayCountShadowLoadMode(epwm);
}

/* ---- S2(2026-10-23): 把"算"与"写"拆开 ------------------------------------------------
 *
 * 旧形状(问题): configure_signal() 既做浮点换算又写寄存器, 而它的入参里带着**在
 * configuration_convert() 时抓到的计数模式**, 于是"设频率"这件事本身包含一次
 * "把 TBCTL 写回运行模式"的动作。停机已经把时间基写成 STOP_FREEZE 之后, 一次横跨
 * 读-算-写的旧 set 会把运行模式恢复回来。
 *
 * 新形状: ns800_epwm_prepare() 只做校验与换算(无寄存器写、无副作用, 可以慢慢算);
 * ns800_epwm_configure_regs() 只做固定数目的整数寄存器写, **不写计数模式**;
 * ns800_epwm_commit_prepared_held() 是给 token 门内用的提交入口。
 * 启动运行只能由 ns800_epwm_drv_enable() 显式完成。
 * ------------------------------------------------------------------ */

int ns800_epwm_prepare(struct rt_device_pwm *device, rt_uint32_t channel,
                       rt_uint32_t period_ns, rt_uint32_t pulse_ns,
                       struct ns800_epwm_prepared *out)
{
    struct ns800_epwm *epwm_dev;
    const struct ns800_epwm_config *cfg;
    struct ns800_signal_params sp;
    float time_base_clock = 0.0F;
    rt_uint16_t tb_period = 0U, cmp_val = 0U;

    if (out == RT_NULL || device == RT_NULL)
    {
        return -RT_EINVAL;
    }
    out->valid = 0U;

    epwm_dev = rt_container_of(device, struct ns800_epwm, epwm_device);
    if (epwm_dev == RT_NULL || epwm_dev->epwm_config == RT_NULL)
    {
        return -RT_EINVAL;
    }
    cfg = epwm_dev->epwm_config;

    if ((channel != (rt_uint32_t)EPWMX_A) && (channel != (rt_uint32_t)EPWMX_B))
    {
        return -RT_EINVAL;
    }
    if ((pulse_ns > period_ns) || (period_ns == 0U))
    {
        return -RT_EINVAL;
    }

    /* 与旧 configuration_convert 同一套换算, 但计数模式取**驱动配置值**(见上文注释)。 */
    sp.count_mode       = cfg->count_mode;
    sp.clock_div        = cfg->clock_div;
    sp.hs_clock_div     = cfg->hs_clock_div;
    sp.signal_freq      = 1.0f / ((float)period_ns / 1000000.0f);
    if (cfg->count_mode != (uint32_t)EPWM_COUNTER_MODE_UP_DOWN)
    {
        sp.signal_freq /= 2.0f;
    }
    sp.duty_val         = (float)pulse_ns / (float)period_ns;
    sp.sys_clk_freq     = (float)RCC_getPclk1Frequency();
    sp.invert_signal_b  = RT_FALSE;
    sp.dead_band_val    = 0;

    time_base_clock = ((float)sp.sys_clk_freq / (float)(1U << (uint16_t)sp.clock_div));
    if (sp.hs_clock_div <= EPWM_HSCLOCK_DIVIDER_4)
    {
        time_base_clock /= (float)(1U << (uint16_t)sp.hs_clock_div);
    }
    else
    {
        time_base_clock /= (float)(2U * (uint16_t)sp.hs_clock_div);
    }

    if (sp.count_mode == (uint32_t)EPWM_COUNTER_MODE_UP)
    {
        tb_period = (uint16_t)((time_base_clock / sp.signal_freq) - 1.0f);
        cmp_val   = (uint16_t)(sp.duty_val * (float)(tb_period + 1U));
    }
    else if (sp.count_mode == (uint32_t)EPWM_COUNTER_MODE_DOWN)
    {
        tb_period = (uint16_t)((time_base_clock / sp.signal_freq) - 1.0f);
        cmp_val   = (uint16_t)((float)(tb_period + 1U) - (sp.duty_val * (float)(tb_period + 1U)));
    }
    else
    {
        tb_period = (uint16_t)(time_base_clock / (2.0f * sp.signal_freq));
        cmp_val   = (uint16_t)(((float)tb_period - (sp.duty_val * (float)tb_period)) + 0.5f);
    }

    out->channel         = channel;
    out->count_mode      = sp.count_mode;
    out->clock_div       = sp.clock_div;
    out->hs_clock_div    = sp.hs_clock_div;
    out->tb_period       = tb_period;
    out->cmp_val         = cmp_val;
    out->dead_band_val   = (rt_uint16_t)sp.dead_band_val;
    out->invert_signal_b = RT_FALSE;
    out->period_ns       = period_ns;
    out->pulse_ns        = pulse_ns;
    out->valid           = 1U;
    return RT_EOK;
}

/* 固定数目的整数寄存器写。**不写 TBCTL 的计数模式** => 提交配置不可能把已冻结的时间基
 * 恢复成运行; 分支只依赖 prepare 已算好的整数, 窗口内没有浮点/循环/IPC/日志。 */
static void ns800_epwm_configure_regs(EPWM_TypeDef *epwm,
                                      const struct ns800_epwm_prepared *p)
{
    EPWM_setClockPrescaler(epwm, (EPWM_ClockDivider)p->clock_div,
                           (EPWM_HSClockDivider)p->hs_clock_div);

    EPWM_setTimeBasePeriod(epwm, p->tb_period);
    EPWM_disablePhaseShiftLoad(epwm);
    EPWM_setPhaseShift(epwm, 0U);
    EPWM_setTimeBaseCounter(epwm, 0U);

    if ((int)p->channel == EPWMX_A)
    {
        EPWM_setCounterCompareShadowLoadMode(epwm, EPWM_COUNTER_COMPARE_A, EPWM_COMP_LOAD_ON_CNTR_ZERO);
        EPWM_setCounterCompareValue(epwm, EPWM_COUNTER_COMPARE_A, p->cmp_val);
    }
    else if ((int)p->channel == EPWMX_B)
    {
        EPWM_setCounterCompareShadowLoadMode(epwm, EPWM_COUNTER_COMPARE_B, EPWM_COMP_LOAD_ON_CNTR_ZERO);
        EPWM_setCounterCompareValue(epwm, EPWM_COUNTER_COMPARE_B, p->cmp_val);
    }

    if (p->count_mode == (uint32_t)EPWM_COUNTER_MODE_UP)
    {
        if ((int)p->channel == EPWMX_A)
        {
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPA);
        }
        else if ((int)p->channel == EPWMX_B)
        {
            if (p->invert_signal_b == (rt_uint32_t)RT_TRUE)
            {
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB);
            }
            else
            {
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB);
            }
        }
    }
    else if (p->count_mode == (uint32_t)EPWM_COUNTER_MODE_DOWN)
    {
        if ((int)p->channel == EPWMX_A)
        {
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPA);
        }
        else if ((int)p->channel == EPWMX_B)
        {
            if (p->invert_signal_b == (rt_uint32_t)RT_TRUE)
            {
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB);
            }
            else
            {
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB);
            }
        }
    }
    else /* UP_DOWN: 与旧实现同一分支 */
    {
        if ((int)p->channel == EPWMX_A)
        {
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPA);
            EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_A, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPA);
        }
        else if ((int)p->channel == EPWMX_B)
        {
            if (p->invert_signal_b == (rt_uint32_t)RT_TRUE)
            {
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB);
            }
            else
            {
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_HIGH, EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB);
                EPWM_setActionQualifierAction(epwm, EPWM_AQ_OUTPUT_B, EPWM_AQ_OUTPUT_LOW, EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB);
            }
        }
    }

    EPWM_setDeadBandDelayPolarity(epwm, EPWM_DB_RED, EPWM_DB_POLARITY_ACTIVE_LOW);
    EPWM_setDeadBandDelayPolarity(epwm, EPWM_DB_FED, EPWM_DB_POLARITY_ACTIVE_LOW);

    EPWM_setDeadBandDelayMode(epwm, EPWM_DB_RED, RT_TRUE);
    EPWM_setRisingEdgeDelayCountShadowLoadMode(epwm, EPWM_RED_LOAD_ON_CNTR_ZERO);
    EPWM_disableRisingEdgeDelayCountShadowLoadMode(epwm);
    EPWM_setRisingEdgeDelayCount(epwm, p->dead_band_val);

    EPWM_setDeadBandDelayMode(epwm, EPWM_DB_FED, RT_TRUE);
    EPWM_setFallingEdgeDelayCountShadowLoadMode(epwm, EPWM_FED_LOAD_ON_CNTR_ZERO);
    EPWM_disableFallingEdgeDelayCountShadowLoadMode(epwm);
    EPWM_setFallingEdgeDelayCount(epwm, p->dead_band_val);
}

/* token 门内的提交入口: 调用方已经屏蔽中断并通过资格检查; 本函数只做固定数目的寄存器写
 * 与两个驱动侧字段赋值。窗口结束后由调用方 __DSB() 再恢复中断。 */
int ns800_epwm_commit_prepared_held(struct rt_device_pwm *device,
                                    const struct ns800_epwm_prepared *prepared)
{
    struct ns800_epwm *epwm_dev;

    if (device == RT_NULL || prepared == RT_NULL || prepared->valid == 0U)
    {
        return -RT_EINVAL;
    }
    epwm_dev = rt_container_of(device, struct ns800_epwm, epwm_device);

    ns800_epwm_configure_regs(epwm_dev->epwm_config->instance, prepared);

    epwm_dev->current_period = prepared->period_ns;
    epwm_dev->current_pulse  = prepared->pulse_ns;
    return RT_EOK;
}

static rt_err_t ns800_epwm_drv_set(struct ns800_epwm *device, struct rt_pwm_configuration *rt_conf)
{
    rt_err_t ret;

    RT_ASSERT(RT_NULL != device);
    RT_ASSERT(RT_NULL != device->epwm_config);
    RT_ASSERT(RT_NULL != rt_conf);

    /* S2: 通用 set 路径同样不再写回计数模式(见 ns800_epwm_configure_regs), 因此它
     * 不再是一次"可启动输出"的写。换算在 prepare 里做, 寄存器提交与旧实现同一套写。 */
    {
        struct ns800_epwm_prepared prepared;

        ret = ns800_epwm_prepare(&device->epwm_device, (rt_uint32_t)rt_conf->channel,
                                 rt_conf->period, rt_conf->pulse, &prepared);
        if (ret != RT_EOK)
        {
            return ret;
        }
        ns800_epwm_configure_regs(device->epwm_config->instance, &prepared);
        device->current_period = prepared.period_ns;
        device->current_pulse  = prepared.pulse_ns;
    }

    return ret;
}

static rt_err_t ns800_epwm_drv_get(struct ns800_epwm *device, struct rt_pwm_configuration *rt_conf)
{
    RT_ASSERT(RT_NULL != device);
    RT_ASSERT(RT_NULL != rt_conf);

    rt_conf->period = device->current_period;
    rt_conf->pulse = device->current_pulse;

    return RT_EOK;
}

/**
 *  @brief  Configure EPWM slave sync phase offset
 *  @param  epwm_slave   EPWM device pointer to a rt_epwm_slave_phase_ext structure
 *  @param  ext          Extended slave phase config parameter
 *  @return RT_EOK for success or error number
 */
static rt_err_t ns800_epwm_set_slave_phase(struct ns800_epwm *epwm_slave,
                                struct rt_epwm_slave_phase_ext *ext)
{
    struct ns800_epwm *epwm_master = RT_NULL;
    struct rt_device_pwm *master_dev = RT_NULL;
    EPWM_TypeDef *master_base = RT_NULL;
    EPWM_TypeDef *slave_base = RT_NULL;
    rt_uint32_t period_val = 0, phase_val = 0;

    RT_ASSERT(RT_NULL != epwm_slave);
    RT_ASSERT(RT_NULL != ext);
    RT_ASSERT(RT_NULL != ext->master_dev);

    if (ext->phase_angle > 360)
    {
        return -RT_EINVAL;
    }

    master_dev = (struct rt_device_pwm *)(ext->master_dev);
    epwm_master = rt_container_of(master_dev, struct ns800_epwm, epwm_device);
    master_base = epwm_master->epwm_config->instance;
    slave_base = epwm_slave->epwm_config->instance;

    /* Read Period value to calculate value for Phase Register */
    period_val = EPWM_getTimeBasePeriod(master_base);
    /* Caluclate phase register values based on Time Base counter mode */
    if (slave_base->TBCTL.BIT.CTRMODE == EPWM_COUNTER_MODE_UP_DOWN)
    {
        phase_val = (2U * period_val * ext->phase_angle) / 360U;
    }
    else if (slave_base->TBCTL.BIT.CTRMODE < EPWM_COUNTER_MODE_UP_DOWN)
    {
        phase_val = (period_val * ext->phase_angle) / 360U;
    }

    EPWM_selectPeriodLoadEvent(slave_base, EPWM_SHADOW_LOAD_MODE_SYNC);
    EPWM_disablePhaseShiftLoad(slave_base);
    EPWM_setPhaseShift(slave_base, phase_val);
    EPWM_enablePhaseShiftLoad(slave_base);
    EPWM_setTimeBaseCounter(slave_base, phase_val);

    return RT_EOK;
}

static rt_err_t ns800_epwm_drv_enable(struct ns800_epwm *device)
{
    const struct ns800_epwm_config *epwm_config = device->epwm_config;
    EPWM_setTimeBaseCounterMode(epwm_config->instance, epwm_config->count_mode);
    return RT_EOK;
}

static rt_err_t ns800_epwm_drv_disable(struct ns800_epwm *device)
{
    const struct ns800_epwm_config *epwm_config = device->epwm_config;
    EPWM_setTimeBaseCounterMode(epwm_config->instance, EPWM_COUNTER_MODE_STOP_FREEZE);
    return RT_EOK;
}

static rt_err_t ns800_drv_pwm_control(struct rt_device_pwm *device, int cmd, void *arg)
{
    struct rt_pwm_configuration *conf = RT_NULL;
    struct rt_epwm_slave_phase_ext *epwm_slave = RT_NULL;
    struct ns800_epwm *epwm = RT_NULL;
    rt_err_t ret = RT_EOK;

    epwm = rt_container_of(device, struct ns800_epwm, epwm_device);

    switch (cmd)
    {
    case PWM_CMD_ENABLE:
        ret = ns800_epwm_drv_enable(epwm);
        break;
    case PWM_CMD_DISABLE:
        ret = ns800_epwm_drv_disable(epwm);
        break;
    case PWM_CMD_SET:
        conf = (struct rt_pwm_configuration *)arg;
        ret = ns800_epwm_drv_set(epwm, conf);
        break;
    case PWM_CMD_GET:
        conf = (struct rt_pwm_configuration *)arg;
        ret = ns800_epwm_drv_get(epwm, conf);
        break;
    case PWM_CMD_EXT_SET_SLAVE_PHASE:
        epwm_slave = (struct rt_epwm_slave_phase_ext *)arg;
        ret = ns800_epwm_set_slave_phase(epwm, epwm_slave);
        break;
    default:
        ret = -RT_EINVAL;
        break;
    }

    return ret;
}

static const struct rt_pwm_ops drv_ops =
{
    ns800_drv_pwm_control
};

int ns800_epwm_init(void)
{
    rt_err_t result = RT_EOK;
    rt_size_t i;
    struct ns800_epwm *device = RT_NULL;

    SYSCON_UNLOCK;
    SYSCON_setTbClkSync(SYSCON, false);
    SYSCON_LOCK;

    for (i = 0; i < (sizeof(epwm_conf) / sizeof(epwm_conf[0])); i++)
    {
        device = &ns800_epwm_obj[i];
        device->pin_config = &epwm_pins[i];
        device->epwm_config = &epwm_conf[i];
        device->current_period = 0;
        device->current_pulse = 0;

        ns800_epwm_gpio_init(device->pin_config);
        ns800_epwm_hw_init(device->epwm_config->instance);

        result = rt_device_pwm_register(&ns800_epwm_obj[i].epwm_device,
                                        epwm_conf[i].name,
                                        &drv_ops,
                                        &ns800_epwm_obj[i]);
        RT_ASSERT(result == RT_EOK);
    }

    SYSCON_UNLOCK;
    SYSCON_setTbClkSync(SYSCON, true);
    SYSCON_LOCK;

    return result;
}
INIT_DEVICE_EXPORT(ns800_epwm_init);

#endif  /* #if defined(BSP_USING_EPWM) */