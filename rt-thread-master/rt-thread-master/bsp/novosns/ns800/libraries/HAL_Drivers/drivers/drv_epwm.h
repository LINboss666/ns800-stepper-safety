/**************************************************************************//**
*
* @copyright (C) 2026 Novosense Technology Corp. All rights reserved.
*
* SPDX-License-Identifier: Apache-2.0
*
* Change Logs:
* Date            Author           Notes
* 2026-6-15        Eric           First version
*
******************************************************************************/

#ifndef __DRV_EPWM_H__
#define __DRV_EPWM_H__

#include <rtdef.h>

/* custom command to configure phase */
#define PWM_CMD_EXT_SET_SLAVE_PHASE     (RT_DEVICE_CTRL_BASE(PWM) + 99)

#define EPWM_PIN_INFO(epwm_port_a, epwm_pin_a, pin_mux_a,   \
                    epwm_port_b, epwm_pin_b, pin_mux_b)     \
    {                                           \
        .port_a = epwm_port_a,                  \
        .pin_a = epwm_pin_a,                    \
        .mux_a = pin_mux_a,                     \
        .port_b = epwm_port_b,                  \
        .pin_b = epwm_pin_b,                    \
        .mux_b = pin_mux_b                      \
    }

#define EPWM_DRV_INIT(num, cnt_mode, clk_div, hs_clk_div)   \
    {                                       \
        .name = "epwm"#num ,                \
        .instance = EPWM##num,              \
        .count_mode = cnt_mode,             \
        .clock_div = clk_div,               \
        .hs_clock_div = hs_clk_div          \
    }

/**
 *  @brief  EPWMx channel
 */
enum
{
    EPWMX_A = 0,
    EPWMX_B
};

/**
 *  @brief  EPWMx phase configuration
 */
struct rt_epwm_slave_phase_ext
{
    /*  EPWM master reference device,
        slave pwm phase compare with this master devcie */
    rt_device_t master_dev;
    /* unit: degree, 0~360 */
    rt_uint32_t phase_angle;
};

/**
 *  @brief  准备阶段产物 (S2 停机优先, 2026-10-23)
 *
 * 为什么要拆：原来的 rt_pwm_set 路径先在本文件 configuration_convert() 里**抓取 TBCTL
 * 当前的计数模式**，再由 configure_signal() 把它**写回** TBCTL（旧 :370-371）。停机把时间基
 * 写成 STOP_FREEZE 之后，一次横跨"读→算→写"的旧 set 会把运行模式恢复回来 —— 也就是说
 * "设个频率"本身是一次可启动输出的写。
 *
 * 于是拆成两步：
 *   - prepare：**不写任何寄存器、不改驱动状态、不产生输出**，只做整数/浮点换算。计数模式
 *     一律取**驱动配置里的正常模式**（不再取实时 TBCTL），因此冻结状态下也不会走错波形分支。
 *   - commit_prepared_held：**只做固定数目的整数寄存器写**，调用方必须已经在 token 门内
 *     （见 applications/stop_priority.h）；**不写 count mode**，因此提交配置永远不能把已冻结
 *     的时间基恢复成 RUN。启动运行只能由 ns800_epwm_drv_enable() 显式完成。
 *
 * 字段全部是已算好的整数与固定枚举值，不含浮点，也不含指向 SDK 私有结构的指针。
 */
struct ns800_epwm_prepared
{
    rt_uint32_t channel;          /* EPWMX_A / EPWMX_B */
    rt_uint32_t count_mode;       /* 驱动配置里的正常计数模式，仅用于选分支，不写寄存器 */
    rt_uint32_t clock_div;        /* EPWM_CLOCK_DIVIDER_* */
    rt_uint32_t hs_clock_div;     /* EPWM_HSCLOCK_DIVIDER_* */
    rt_uint16_t tb_period;
    rt_uint16_t cmp_val;
    rt_uint16_t dead_band_val;
    rt_uint32_t invert_signal_b;  /* RT_TRUE / RT_FALSE */
    rt_uint32_t period_ns;        /* 提交时同步驱动侧 current_period 用 */
    rt_uint32_t pulse_ns;
    rt_uint32_t valid;            /* 1 = 已由 prepare 填好 */
};

/**
 *  @brief  纯计算准备一次 PWM 配置（无寄存器写、无副作用）
 *  @return RT_EOK / -RT_EINVAL(参数或通道非法)
 */
int ns800_epwm_prepare(struct rt_device_pwm *device, rt_uint32_t channel,
                       rt_uint32_t period_ns, rt_uint32_t pulse_ns,
                       struct ns800_epwm_prepared *out);

/**
 *  @brief  在调用方已持有的短临界区(token 门)内提交 prepare 的整数写
 *  @note   调用方约定：窗口内不得有循环/IPC/日志/浮点；本函数不写 TBCTL 的计数模式，
 *          因此不可能把已冻结的时间基恢复为运行。结束后由调用方 __DSB() 再开中断。
 */
int ns800_epwm_commit_prepared_held(struct rt_device_pwm *device,
                                    const struct ns800_epwm_prepared *prepared);

#endif
