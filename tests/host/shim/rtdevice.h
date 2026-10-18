/*
 * shim/rtdevice.h - 设备/PWM/引脚 API 的夹具实现(全部只记录调用, 不碰硬件)
 *
 * ⚠ 这里没有真实设备: rt_device_find() 是否返回"设备"由测试用例决定,
 *   串口读默认永远拿不到应答(= 芯片失联), 因此凡是走真实 UART 的路径都会
 *   以超时失败收场 —— 这正是主机侧想要覆盖的"不可信就拒绝"方向。
 */
#ifndef SHIM_RTDEVICE_H
#define SHIM_RTDEVICE_H

#include <rtthread.h>

struct rt_device { int fake; };
typedef struct rt_device *rt_device_t;

struct rt_device_pwm { int fake; };

struct serial_configure
{
    rt_uint32_t baud_rate;
    rt_uint32_t data_bits;
    rt_uint32_t parity;
    rt_uint32_t stop_bits;
    rt_uint32_t buf_size;
};
#define RT_SERIAL_CONFIG_DEFAULT { 115200, 8, 0, 1, 64 }

#define RT_DEVICE_OFLAG_RDWR   0x03
#define RT_DEVICE_FLAG_INT_RX  0x100
#define RT_DEVICE_CTRL_CONFIG  4

#define PIN_HIGH 1
#define PIN_LOW  0

rt_device_t rt_device_find(const char *name);
rt_err_t    rt_device_open(rt_device_t dev, rt_uint16_t oflag);
rt_err_t    rt_device_close(rt_device_t dev);
rt_size_t   rt_device_read(rt_device_t dev, rt_size_t pos, void *buffer, rt_size_t size);
rt_size_t   rt_device_write(rt_device_t dev, rt_size_t pos, const void *buffer, rt_size_t size);
rt_err_t    rt_device_control(rt_device_t dev, int cmd, void *arg);

rt_err_t    rt_pwm_set(struct rt_device_pwm *pwm, int channel,
                       rt_uint32_t period, rt_uint32_t pulse);
rt_err_t    rt_pwm_enable(struct rt_device_pwm *pwm, int channel);
rt_err_t    rt_pwm_disable(struct rt_device_pwm *pwm, int channel);

void        rt_pin_write(rt_base_t pin, rt_base_t value);

#endif /* SHIM_RTDEVICE_H */
