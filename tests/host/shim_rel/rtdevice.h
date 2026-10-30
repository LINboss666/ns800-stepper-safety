/*
 * shim_rel/rtdevice.h - M8 链的 GPIO 兼容层(与 shim_sd/rtdevice.h 同一组常量)
 *
 * 全部**实现**在 rel_main.c: 用例要靠读数注入(含 -1/2 非法值)与调用计数判定,
 * 所以这里只声明, 不给内联假实现。rt_pin_read 不收窄成布尔(真机会返回别的值)。
 */
#ifndef SHIM_REL_RTDEVICE_H
#define SHIM_REL_RTDEVICE_H

#include "rtthread.h"

/* stop_priority.h 里有以 struct rt_device_pwm* 为参数的声明。真实 rtdevice.h 在
 * pwm 子头里定义它, 本 shim 不实现 PWM 设备 —— 前置声明只为让"指针形参"有作用域,
 * 不引入任何新语义(被测代码从不解引用它)。 */
struct rt_device_pwm;

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

#endif /* SHIM_REL_RTDEVICE_H */
