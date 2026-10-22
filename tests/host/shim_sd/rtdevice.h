/*
 * shim_sd/rtdevice.h - 语义链用的 GPIO 兼容层
 *
 * 只声明被测代码真正用到的东西; 全部**实现**在 sd_main.c, 因为用例要靠它们计数:
 *   sd_sim_set_level() 注入任意返回值(含 -1/2 这类非法值, 固件必须拒绝)
 *   sd_sim_write_count() 证明采样/转换路径**一次 GPIO 写都没有**
 *   sd_sim_read_count()  证明配置关闭的限位连读都没读
 * 真机上 rt_pin_read 可能返回 0/1 之外的值, 所以这里不把它收窄成布尔。
 */
#ifndef SHIM_SD_RTDEVICE_H
#define SHIM_SD_RTDEVICE_H

#include "rtthread.h"

#define PIN_INPUT          0
#define PIN_OUTPUT         1
#define PIN_MODE_INPUT     0
#define PIN_MODE_OUTPUT    1
#define PIN_MODE_INPUT_PULLUP   2
#define PIN_MODE_INPUT_PULLDOWN 3
#define PIN_MODE_OUTPUT_OD 4

#define PIN_LOW            0x00
#define PIN_HIGH           0x01

#define PIN_IRQ_MODE_RISING         0
#define PIN_IRQ_MODE_FALLING        1
#define PIN_IRQ_MODE_RISING_FALLING 2

rt_base_t rt_pin_get(const char *name);
rt_base_t rt_pin_read(rt_base_t pin);
void      rt_pin_write(rt_base_t pin, rt_base_t value);
void      rt_pin_mode(rt_base_t pin, rt_base_t mode);
rt_err_t  rt_pin_attach_irq(rt_base_t pin, rt_uint32_t mode,
                            void (*irq_cb)(void *args), void *irq_args);
rt_err_t  rt_pin_irq_enable(rt_base_t pin, rt_uint32_t enable);

#endif /* SHIM_SD_RTDEVICE_H */
