/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author       Notes
 * 2018-10-30     SummerGift   first version
 */

#ifndef __DRV_USART_H__
#define __DRV_USART_H__

#include <rtthread.h>
#include "rtdevice.h"
#include <rthw.h>
#include <drv_common.h>

#define UART_CTRL_SET_BLOCK_TIMEOUT     0x20

/* ns800 config class */
struct ns800_uart_config
{
    const char *name;
    UART_TypeDef *Instance;
    IRQn_Type rx_irq_type;
    IRQn_Type tx_irq_type;
    void (*irq_handler)(void);
    GPIO_TypeDef *rx_port;
    GPIO_PinNum rx_pin;
    GPIO_AltFunc rx_mux;
    rt_uint32_t rx_pad;
    GPIO_Direction rx_direction;
    rt_bool_t rx_drive_max;
    GPIO_TypeDef *tx_port;
    GPIO_PinNum tx_pin;
    GPIO_AltFunc tx_mux;
    rt_uint32_t tx_pad;
    GPIO_Direction tx_direction;
    rt_bool_t tx_drive_max;
    rt_uint32_t tx_block_timeout;
    rt_uint16_t rx_bufsz;
    rt_uint16_t tx_bufsz;
};

typedef struct {
    UART_TypeDef *Instance;

    rt_uint32_t baud_rate;

    rt_uint8_t data_bits;
    rt_uint8_t stop_bits;
    rt_uint8_t parity;
    rt_uint8_t bit_order;
    rt_uint8_t invert;
    rt_uint8_t bufsz;
    rt_uint8_t flowcontrol;
    rt_uint8_t reserved;
}UART_HandleTypeDef;

/* ns800 uart dirver class */
struct ns800_uart
{
    UART_HandleTypeDef handle;
    struct ns800_uart_config *config;
    rt_uint32_t DR_mask;
    rt_uint32_t tx_block_timeout;
    struct rt_serial_device serial;
};


int rt_hw_uart_init(void);

/* 2026-10-23 S2(任务书 §10.3): 整帧带预算发送, 仅供 TMC 生产路径调用。
 * start/total 用**调用者那一次操作的同一个 start**(不得在被抢占后重开等待),
 * 返回 RT_EOK 当且仅当 *written == size; 预算耗尽返回 -RT_ETIMEOUT 并把已经写出的
 * 字节数留在 *written 里, 上下文非法(ISR/已关中断)返回 -RT_EBUSY, 参数或设备不在
 * 已注册表里返回 -RT_EINVAL。定义见 drv_uart.c(含 SDK 叶子无等待的本地依据)。 */
rt_err_t ns800_uart_write_budgeted(rt_device_t dev, const rt_uint8_t *data,
                                   rt_size_t size, rt_tick_t start, rt_tick_t total,
                                   rt_size_t *written);

#endif  /* __DRV_USART_H__ */

