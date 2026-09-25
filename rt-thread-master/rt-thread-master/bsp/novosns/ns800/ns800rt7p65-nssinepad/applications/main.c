/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author           Notes
 * 2026-05-06     Jiawei.Deng      first version
 * 2026-09-13     LMX/Agent        项目启动入口(Phase 7-D): 初始化编排+状态打印
 * 2026-09-25     LMX/Agent        Fix A: 显式调用 supervisor_boot 并如实汇报结果
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

/* Earliest possible safety action (before any RT-Thread/device init in main):
 * external flash module CS (PF12/J2-12) must idle HIGH to avoid bus contention
 * (BUG-001). safety_gpio_boot() re-establishes it plus every other safe level.
 * (PA19/J2-13 freed by BUG-009 migration: now reserved, see project_board.h) */
static void flash_cs_idle_high(void)
{
    rt_pin_mode(PIN_NUM(GPIOF, GPIO_PIN_12), PIN_MODE_OUTPUT);
    rt_pin_write(PIN_NUM(GPIOF, GPIO_PIN_12), PIN_HIGH);
}

int main(void)
{
    rt_err_t boot;
    safety_state_t st;

    flash_cs_idle_high();

    rt_kprintf("\r\n========================================\r\n");
    rt_kprintf("  NS800 stepper safety system startup\r\n");
    rt_kprintf("  stages: 1 safe-hw / 2 safety-state / 3 safety-thread /\r\n");
    rt_kprintf("          4 hw-svc / 5 storage / 6 config / 7 motor+sensor /\r\n");
    rt_kprintf("          8 diag / 9 blackbox / 10 ui / 11 selftest->READY\r\n");
    rt_kprintf("========================================\r\n");

    /* Fix A: Phase 7 runtime 全部由 supervisor_boot 建立。它返回 required stage
     * 是否走通; 失败时内部已 fail closed(FAULT_LATCHED) 且不打印 READY。 */
    boot = supervisor_boot();

    system_status();

    st = safety_state_get();
    if (boot != RT_EOK)
        rt_kprintf("[MAIN] BOOT ABORTED (err=%d) - state=%s fault=%u."
                   " Motion is NOT permitted.\n",
                   (int)boot, safety_state_name(st), safety_fault_get());
    else if (st == SAFETY_READY)
        rt_kprintf("[MAIN] boot complete: READY (motion still gate-locked)\n");
    else
        rt_kprintf("[MAIN] boot complete but state=%s fault=%u"
                   " (degraded/latched - NOT ready)\n",
                   safety_state_name(st), safety_fault_get());

    rt_kprintf("[MAIN] motor arm gate fail mask=0x%X"
               " (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=%s)\n",
               motor_get_gate_fail_mask(),
               MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ? "YES" : "NO");

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
