/*
 * drv_epwm_leaf.h —— CP5 §5: EPWM 叶子的**私有结构/声明**(唯一一份)。
 *
 * 为什么单独成头: 任务书 §5 要求"需要的私有结构/整数类型共用声明, 不复制出两套不同
 * 布局"。主机夹具要真编译 drv_epwm_leaf.inc, 而 ns800_epwm_prepare()/
 * commit_prepared_held() 用 rt_container_of(device, struct ns800_epwm, epwm_device)
 * 取配置 —— 只要夹具与本文件各写一份 struct ns800_epwm, 布局就可能不同, 那个
 * container_of 就会读错偏移。因此**目标与主机都从这里取同一份声明**。
 *
 * 本头不是公开 API(公开头仍是 drv_epwm.h), 也不引入新类型: 它装的是原本就写在
 * drv_epwm.c 里的三个私有结构, 逐字搬入。
 */
#ifndef __DRV_EPWM_LEAF_H__
#define __DRV_EPWM_LEAF_H__

/* 包含者必须先提供(目标: drv_epwm.c 已有的 include 顺序; 主机: shim_leaf/drv_common.h):
 * EPWM_TypeDef / EPWM_TimeBaseCountMode / EPWM_ClockDivider / EPWM_HSClockDivider /
 * struct rt_device_pwm 与 rtdef 定型类型, 以及 drv_epwm.h(struct ns800_epwm_prepared)。 */

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

#endif /* __DRV_EPWM_LEAF_H__ */
