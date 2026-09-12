/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author           Notes
 * 2026-05-06     Jiawei.Deng      first version
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <board.h>
#include "drv_gpio.h"

/* defined the LED1 pin: GPIO_68 = PC4 */
#define LED1_PIN    PIN_NUM(GPIO_68)

/* External flash module CS (PF12/J2-12) must idle HIGH from boot.
 * (PA19/J2-13 freed by BUG-009 migration: now reserved, see project_board.h) */
static void flash_cs_idle_high(void)
{
    rt_pin_mode(PIN_NUM(GPIOF, GPIO_PIN_12), PIN_MODE_OUTPUT);
    rt_pin_write(PIN_NUM(GPIOF, GPIO_PIN_12), PIN_HIGH);
}

int main(void)
{
    rt_pin_mode(LED1_PIN, PIN_MODE_OUTPUT);

    flash_cs_idle_high();

    while (1)
    {
/*        rt_kprintf("\r\n led1_thread_entry running! \r\n"); */
        rt_pin_write(LED1_PIN, PIN_HIGH);
        rt_thread_mdelay(1000);
        rt_pin_write(LED1_PIN, PIN_LOW);
        rt_thread_mdelay(1000);
    }
}

