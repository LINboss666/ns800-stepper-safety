/*
 * shim_leaf/rtdevice.h —— CP5 §5: 本链专用的串口设备层声明(叠加在 shim_ps 上)。
 *
 * 真实生产路径需要它们(tmc2209.c:252-285 / :293-352 / :527):
 *   rt_device_find(TMC_UART_NAME) -> 设备句柄
 *   rt_device_control(RT_DEVICE_CTRL_CONFIG, &struct serial_configure)
 *   rt_device_open(RT_DEVICE_OFLAG_RDWR | RT_DEVICE_FLAG_INT_RX)
 *   rt_device_read(...)      —— RX 洪泛/应答搜索的字节来源(夹具受控)
 * 以及真实叶子 drv_uart_txleaf.inc 的**设备查表**:
 *   for (i..) if (&uart_obj[i].serial.parent == dev) uart = &uart_obj[i];
 * 所以 struct rt_serial_device 的第一个成员必须是 struct rt_device parent(与目标
 * drivers/drv_uart.h:62-69 的 struct ns800_uart 里 serial 的用法一致), 否则叶子那条
 * 查表就不是"真执行"。
 *
 * ⚠ 本文件只给**声明**; 实现全部在夹具 tests/host/leaf_main.c(受控 RX/TX 与寄存器模型),
 *   五条既有链不包含本文件, 行为逐字不变。
 */
#ifndef NS800_LEAF_RTDEVICE_H
#define NS800_LEAF_RTDEVICE_H

#include "../shim_ps/rtdevice.h"
#include "drv_common.h"       /* UART_TypeDef / EPWM_TypeDef 寄存器模型与 SDK 内联叶子 */

typedef rt_int32_t rt_off_t;

struct rt_serial_device
{
    struct rt_device parent;
};

struct serial_configure
{
    rt_uint32_t baud_rate;
    rt_uint8_t  data_bits;
    rt_uint8_t  stop_bits;
    rt_uint8_t  parity;
};

#define RT_SERIAL_CONFIG_DEFAULT          { 0, 0, 0, 0 }
#define RT_DEVICE_CTRL_CONFIG             0x04
#define RT_DEVICE_OFLAG_RDWR              0x03
#define RT_DEVICE_FLAG_INT_RX             0x40
#define RT_DEVICE_FLAG_INT_TX             0x80

rt_err_t  rt_device_control(rt_device_t dev, int cmd, void *arg);
rt_err_t  rt_device_open(rt_device_t dev, rt_uint16_t oflag);
rt_err_t  rt_device_close(rt_device_t dev);
rt_size_t rt_device_read(rt_device_t dev, rt_off_t pos, void *buffer, rt_size_t size);
rt_size_t rt_device_write(rt_device_t dev, rt_off_t pos, const void *buffer, rt_size_t size);

#endif /* NS800_LEAF_RTDEVICE_H */
