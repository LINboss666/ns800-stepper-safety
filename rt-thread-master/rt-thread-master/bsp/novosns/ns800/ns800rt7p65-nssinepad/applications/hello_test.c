/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Change Logs:
 * Date           Author           Notes
 * 2026-09-10     ZCode            test file added by agent
 */

#include <rtthread.h>

#define HELLO_THREAD_PRIORITY   25
#define HELLO_THREAD_TIMESLICE  5
#define HELLO_PERIOD_MS         5000

static rt_thread_t hello_tid = RT_NULL;

static void hello_thread_entry(void *parameter)
{
    rt_uint32_t count = 0;

    while (1)
    {
        rt_kprintf("[hello_test] running, count = %d\r\n", count++);
        rt_thread_mdelay(HELLO_PERIOD_MS);
    }
}

static rt_err_t hello_init(void)
{
    hello_tid = rt_thread_create("hello",
                                 hello_thread_entry, RT_NULL,
                                 1024,
                                 HELLO_THREAD_PRIORITY,
                                 HELLO_THREAD_TIMESLICE);
    if (hello_tid != RT_NULL)
    {
        rt_thread_startup(hello_tid);
        rt_kprintf("[hello_test] thread created\r\n");
    }
    return RT_EOK;
}
INIT_APP_EXPORT(hello_init);

static void cmd_hello(void)
{
    rt_kprintf("hello from ns800 test file!\r\n");
}
MSH_CMD_EXPORT(cmd_hello, print a hello message);
