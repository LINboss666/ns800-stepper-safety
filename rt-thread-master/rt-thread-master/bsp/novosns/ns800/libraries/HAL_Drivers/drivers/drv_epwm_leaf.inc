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

/* ---- CP6 §2: prepare 的换算按**纳秒**与**真实计数关系**重做 ------------------
 *
 * 为什么必须改(不是风格问题): 调用方传的是 ns —— RT-Thread 的 PWM 契约
 * (components/drivers/include/drivers/dev_pwm.h 示例 period=500000 => 0.5ms)、
 * motor.c 的 `1000000000u / hz` 与 step_pwm.c 的 `1000000000u / hz` 都是 ns。
 * 而 SDK 原式 `1.0f / (period_ns / 1000000.0f)` 把 period 当**微秒**用, 频率小了 1000 倍;
 * 默认 UP_DOWN 下 1kHz 请求的周期计数因此是 5.0e7 而不是 1.0e5, 再直接窄化成 uint16_t
 * 就是**未定义行为**(C 不保证"模 65536"), 编译器观察到 61568 只是巧合。
 *
 * 现在的算法(全部整数 uint64, 不做任何越界窄化):
 *   counts(period) = period_ns * clk_hz / (div * 1e9)        div = CLKDIV因子 * HSPCLKDIV因子
 *   UP_DOWN : TBPRD = round(counts / 2)      (一个完整周期走 0→TBPRD→0 = 2*TBPRD 个计数)
 *   UP/DOWN : TBPRD = round(counts) - 1      (一个周期 = TBPRD+1 个计数)
 *   比较值:  UP   : CMP = round(duty * (TBPRD+1))            (ZERO 置高, 上计数到 CMPA 置低)
 *            DOWN : CMP = (TBPRD+1) - round(duty * (TBPRD+1))
 *            UP_DOWN: CMP = TBPRD - round(duty * TBPRD)      (高电平跨 UP/DOWN 两段, 各 TBPRD-CMP)
 *   50% 占空比在三种模式下都保持 50%(UP_DOWN: TBPRD=50000 => CMP=25000)。
 *
 * 分频因子表按 SDK 枚举原文(EPWMH:61-70 与 EPWMH:80-89):
 *   CLKDIV    n = 0..7 -> 1,2,4,8,16,32,64,128   (2^n)
 *   HSPCLKDIV n = 0..7 -> 1,2,4,6,8,10,12,14
 * 计数模式仍取**驱动配置值**, 本函数不切模式; 选择分频会作用于整个 EPWM 实例
 * (同实例其它通道用途待确认的 M-2 硬件边界保持不变, 本轮不据此翻任何宏)。
 *
 * 可表达性: 先试驱动原配分频(能表达就沿用, 不引入无关变化); 不行则在**全部分频组合里
 * 取最小的可用总分频**(周期分辨率最高优先), 使 20Hz 斜坡首拍与 1Hz 这类合法低频也能被
 * 表达 —— 正常启动不得因范围错误而变成停机。找不到任何组合才报错: valid=0、零寄存器写。
 * 本函数只回填**枚举与原值**(clock_div / hs_clock_div / tb_period / cmp_val / period_ns /
 * pulse_ns); 周期的舍入界限不在结构里另设字段, 由调用方(或 C10 夹具)按同一张分因子表
 * 与计数关系自行反推校验 —— 这样"请求 ns"与"寄存器里的计数"之间是可独立核对的关系,
 * 而不是"生产公式自证"。 */

/* SDK 枚举的分频因子(EPWMH:61-70 / :80-89 原文数值) */
static const rt_uint32_t ns800_epwm_clkdiv_factor[8] =
    { 1U, 2U, 4U, 8U, 16U, 32U, 64U, 128U };
static const rt_uint32_t ns800_epwm_hsdiv_factor[8] =
    { 1U, 2U, 4U, 6U, 8U, 10U, 12U, 14U };

#define NS800_EPWM_NS_PER_SEC   1000000000ULL
#define NS800_EPWM_TBPRD_MAX    65535U

/* 一条周期能否用给定总分频表达: 返回 1 表示 TBPRD/CMP 都在可表示范围内。
 * 全部用 uint64; 每一步先判溢出/下限, 绝不做"先转窄再检查"。
 * 极限占空比的口径(明确, 不靠越界转换): 比较值一律取"贴边"整数, 然后与 16 位可表示范围
 *   一起判定 —— 需要 CMP=TBPRD+1=65536 的那一端(UP 的 100% / DOWN 的 0%)在 TBPRD 已用到
 *   65535 时**拒绝**(返回 FALSE), 而不是截断成 0(那会把"常高"说成"常低", 语义正好相反)。
 *   其余极限(UP 的 0%=CMP=0、DOWN/UP_DOWN 的 0%与 100%)只要落在 0..65535 就支持。
 * 正常 STEP 的 50% 在三种模式下都是 50%, 不受这条边界影响。 */
static rt_bool_t ns800_epwm_try_counts(rt_uint64_t clk_hz, rt_uint64_t period_ns,
                                       rt_uint64_t pulse_ns, rt_uint32_t mode,
                                       rt_uint64_t div, rt_uint32_t *tb_out,
                                       rt_uint32_t *cmp_out)
{
    rt_uint64_t denom = div * NS800_EPWM_NS_PER_SEC;
    rt_uint64_t num, counts, tb, scale, off;
    rt_uint32_t cmpv;

    if (clk_hz == 0U || div == 0U || period_ns == 0U || period_ns > 1000000000ULL)
    {
        return RT_FALSE;                       /* 时钟无效 / 超出本驱动的 ns 域 */
    }
    /* num = period_ns * clk_hz: 上面已限 period_ns<=1e9 且 clk 为 uint32, 最大 4.3e18 */
    num = period_ns * clk_hz;
    /* 半计数舍入: counts = round(num / denom) */
    counts = (num + (denom / 2U)) / denom;

    if (mode == (rt_uint32_t)EPWM_COUNTER_MODE_UP_DOWN)
    {
        if (counts < 4U) return RT_FALSE;               /* TBPRD>=2 才有 0→PRD→0 的波形 */
        tb = (counts + 1U) / 2U;                        /* round(counts/2) */
        scale = tb;                                     /* 高电平 = 2*(TBPRD-CMP) */
    }
    else
    {
        if (counts < 2U) return RT_FALSE;               /* TBPRD>=1 */
        tb = counts - 1U;                               /* 周期 = TBPRD+1 个计数 */
        scale = tb + 1U;
    }
    if (tb > NS800_EPWM_TBPRD_MAX) return RT_FALSE;

    /* off = round(pulse * scale / period) —— 与窄化无关, 先算再判范围 */
    off = ((rt_uint64_t)pulse_ns * scale + (period_ns / 2U)) / period_ns;
    if (off > scale) off = scale;                       /* pulse==period 时贴边 */

    if (mode == (rt_uint32_t)EPWM_COUNTER_MODE_UP)
    {
        cmpv = (rt_uint32_t)off;                      /* 高电平 = CMP 个计数 */
    }
    else
    {
        cmpv = (rt_uint32_t)(scale - off);            /* DOWN 与 UP_DOWN: 高电平 = scale-CMP */
    }

    if (cmpv > NS800_EPWM_TBPRD_MAX) return RT_FALSE;

    *tb_out = (rt_uint32_t)tb;
    *cmp_out = cmpv;
    return RT_TRUE;
}

int ns800_epwm_prepare(struct rt_device_pwm *device, rt_uint32_t channel,
                       rt_uint32_t period_ns, rt_uint32_t pulse_ns,
                       struct ns800_epwm_prepared *out)
{
    struct ns800_epwm *epwm_dev;
    const struct ns800_epwm_config *cfg;
    rt_uint32_t mode, cd, hd, i, j;
    rt_uint32_t tb = 0U, cmp = 0U, chosen_cd = 0U, chosen_hd = 0U;
    rt_uint64_t clk_hz;
    rt_bool_t found = RT_FALSE;

    if (out == RT_NULL || device == RT_NULL)
    {
        return -RT_EINVAL;
    }
    out->valid = 0U;                          /* 只有全部校验通过后才置 1 */

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

    mode = (rt_uint32_t)cfg->count_mode;
    if (mode != (rt_uint32_t)EPWM_COUNTER_MODE_UP &&
        mode != (rt_uint32_t)EPWM_COUNTER_MODE_DOWN &&
        mode != (rt_uint32_t)EPWM_COUNTER_MODE_UP_DOWN)
    {
        return -RT_EINVAL;                    /* 非法计数模式(含 STOP_FREEZE): 不准备写 */
    }
    cd = (rt_uint32_t)cfg->clock_div;
    hd = (rt_uint32_t)cfg->hs_clock_div;
    if (cd > 7U || hd > 7U)
    {
        return -RT_EINVAL;
    }
    clk_hz = (rt_uint64_t)RCC_getPclk1Frequency();
    if (clk_hz == 0U || clk_hz > 0xFFFFFFFFULL)
    {
        return -RT_EINVAL;
    }

    /* 1) 原配分频能表达就沿用(不引入无关变化)。 */
    if (ns800_epwm_try_counts(clk_hz, (rt_uint64_t)period_ns, (rt_uint64_t)pulse_ns,
                              mode, (rt_uint64_t)ns800_epwm_clkdiv_factor[cd] *
                                    ns800_epwm_hsdiv_factor[hd], &tb, &cmp) == RT_TRUE)
    {
        chosen_cd = cd;
        chosen_hd = hd;
        found = RT_TRUE;
    }
    else
    {
        /* 2) 否则在全部组合里取**最小可用总分频**(分辨率最高、偏离原配置最小优先)。 */
        rt_uint64_t best = 0U;

        for (i = 0U; i < 8U; ++i)
        {
            for (j = 0U; j < 8U; ++j)
            {
                rt_uint64_t div = (rt_uint64_t)ns800_epwm_clkdiv_factor[i] *
                                  ns800_epwm_hsdiv_factor[j];

                if (best != 0U && div >= best) continue;
                if (ns800_epwm_try_counts(clk_hz, (rt_uint64_t)period_ns,
                                          (rt_uint64_t)pulse_ns, mode, div,
                                          &tb, &cmp) == RT_TRUE)
                {
                    best = div;
                    chosen_cd = i;
                    chosen_hd = j;
                    found = RT_TRUE;
                }
            }
        }
    }
    if (found != RT_TRUE)
    {
        return -RT_EINVAL;                    /* 完全不可表达: valid 保持 0, 零寄存器写 */
    }

    out->channel         = channel;
    out->count_mode      = mode;
    out->clock_div       = chosen_cd;
    out->hs_clock_div    = chosen_hd;
    out->tb_period       = (rt_uint16_t)tb;
    out->cmp_val         = (rt_uint16_t)cmp;
    out->dead_band_val   = 0U;
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
