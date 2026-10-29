/* shim_cc/rtdevice.h - minimal ADC device view for the real current_adc.c. */
#ifndef SHIM_CC_RTDEVICE_H
#define SHIM_CC_RTDEVICE_H

#include "rtthread.h"

struct rt_device { int dummy; };
typedef struct rt_device *rt_device_t;
struct rt_adc_device { int dummy; };
typedef struct rt_adc_device *rt_adc_device_t;
struct rt_device_pwm { int dummy; };

rt_device_t rt_device_find(const char *name);
rt_err_t rt_adc_enable(rt_adc_device_t dev, int channel);
rt_uint32_t rt_adc_read(rt_adc_device_t dev, int channel);

#endif /* SHIM_CC_RTDEVICE_H */
