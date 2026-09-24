/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author           Notes
 * 2026-05-06     Jiawei.Deng      first version
 * 2026-09-13     LMX/Agent        项目启动入口(Phase 7-D): 初始化编排+状态打印
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <board.h>
#include "drv_gpio.h"
#include "supervisor.h"
#include "blackbox.h"
#include "safety_state.h"
#include "motor.h"

/* defined the LED1 pin: GPIO_68 = PC4 (板载, 不参与扩展板状态灯管理) */
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
    motor_snapshot_t snap;

    flash_cs_idle_high();

    rt_kprintf("\r\n========================================\r\n");
    rt_kprintf("  NS800 stepper safety system startup\r\n");
    rt_kprintf("  stages: safe-hw / config / services /\r\n");
    rt_kprintf("          threads / selftest / READY\r\n");
    rt_kprintf("========================================\r\n");

    system_status();

    /* 启动自检结果(在 safety_state INIT_APP 中已执行) */
    if (safety_state_get() == SAFETY_READY)
        rt_kprintf("[MAIN] boot complete: READY\n");
    else
        rt_kprintf("[MAIN] boot complete: state=%s fault=%u (degraded/latched)\n",
                   safety_state_name(safety_state_get()), safety_fault_get());
    if (motor_get_snapshot(&snap) == RT_EOK)
        rt_kprintf("[MAIN] motor gate: enable-path-validated=%s\n",
                   MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ? "YES" : "NO (arm refused)");

    /* 业务全部在线程中; main 仅板载 LED1 低速心跳(与扩展板状态灯无关) */
    rt_pin_mode(LED1_PIN, PIN_MODE_OUTPUT);
    while (1)
    {
        rt_pin_write(LED1_PIN, PIN_HIGH);
        rt_thread_mdelay(1000);
        rt_pin_write(LED1_PIN, PIN_LOW);
        rt_thread_mdelay(4000);
    }
}
