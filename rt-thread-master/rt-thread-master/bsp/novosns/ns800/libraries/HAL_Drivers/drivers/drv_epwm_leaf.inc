/* drv_epwm_leaf.inc —— CP5 §5: EPWM"算/写分离"叶子的**唯一**生产实现体。
 *
 * 为什么是私有 include 而不是副本: 任务书 §5 要求主机与目标共用同一实现。这里放的是
 * **原来就在 drv_epwm.c 里的那三个函数**(prepare / configure_regs / commit_prepared_held),
 * 逐字搬入, 换算与寄存器写序一行未改; 目标由 drv_epwm.c 在原位置 include, 主机夹具 TU
 * 在提供真实布局的寄存器模型(shim_leaf/drv_common.h)与共用私有结构(drv_epwm_leaf.h)之后
 * include **同一个文件**。因此 C10 数到的"寄存器写次数"来自生产写序本身, 不是桩的计数。
 *
 * 包含者必须先提供: drv_epwm_leaf.h(struct ns800_epwm / ns800_signal_params)、
 * drv_epwm.h(struct ns800_epwm_prepared 与两个公开原型)、EPWM_* / RCC_getPclk1Frequency。
 * 对象表(ns800_epwm_obj / epwm_conf)仍留在 drv_epwm.c: 叶子只通过设备句柄反查。
 */

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
