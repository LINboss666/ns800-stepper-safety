/*
 * safety_thread.c - 安全线程与事件链 (Phase 7-B)
 *
 * 事件流(设计, 交接文档 §14.3):
 *   GPIO ISR / 业务代码 --safety_post_event()--> rt_event
 *     --> Safety Thread(prio 4) rt_event_recv --> 分类 --> safety_force_shutdown()
 *
 * ISR 回调(estop_isr)只做 safety_post_event —— 零 SPI/UART/Flash/delay/打印。
 * DIAG(PA.3) 为电平输入, 由本线程在 recv 超时唤醒时轮询(门控开启后),
 * 高电平 → 自投 EVT_TMC_DIAG。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "app_health.h"
#include "safety_state.h"
#include "motor.h"
#include "safety_thread.h"

#define SAFETY_THREAD_STACK   1024
#define SAFETY_THREAD_PRIO    4
#define SAFETY_RECV_TIMEOUT   500     /* ms; 超时唤醒用于 DIAG 轮询 */

#define SAFETY_EVT_MASK_ALL   (EVT_ESTOP | EVT_LIMIT_MIN | EVT_LIMIT_MAX | \
                               EVT_TMC_DIAG | EVT_MULTI_FAULT | EVT_SOFT_FAULT)

static struct rt_thread safety_thread;
static rt_uint8_t safety_stack[SAFETY_THREAD_STACK];
static rt_bool_t st_thread_up = RT_FALSE;
static subsys_health_t st_health = SUBSYS_UNINIT;
static rt_bool_t st_irq_attached = RT_FALSE;

/* ---------- ISR 回调: 只置事件(§14.3 纪律) ---------- */
static void estop_isr(void *args)
{
    rt_uint32_t evt = EVT_ESTOP;
    (void)args;
    safety_post_event(evt);      /* rt_event_send ISR 安全 */
}

/* ---------- 事件处理(线程上下文) ---------- */
static void safety_handle_events(rt_uint32_t ev)
{
    if (ev & EVT_ESTOP)
    {
        rt_kprintf("[SAFETY] EVT_ESTOP\n");
        motor_emergency_stop();
        safety_force_shutdown(FAULT_ESTOP);
        safety_transition(SAFETY_ESTOP);   /* FAULT_LATCHED -> ESTOP(白名单允许) */
        return;
    }
    if (ev & EVT_LIMIT_MIN)
    {
        rt_kprintf("[SAFETY] EVT_LIMIT_MIN\n");
        safety_force_shutdown(FAULT_LIMIT_MIN);
        return;
    }
    if (ev & EVT_LIMIT_MAX)
    {
        rt_kprintf("[SAFETY] EVT_LIMIT_MAX\n");
        safety_force_shutdown(FAULT_LIMIT_MAX);
        return;
    }
    if (ev & EVT_TMC_DIAG)
    {
        rt_kprintf("[SAFETY] EVT_TMC_DIAG\n");
        safety_force_shutdown(FAULT_TMC_DIAG);
        return;
    }
    if (ev & EVT_MULTI_FAULT)
    {
        rt_kprintf("[SAFETY] EVT_MULTI_FAULT\n");
        safety_force_shutdown(FAULT_MULTI_SOURCE);
        return;
    }
    if (ev & EVT_SOFT_FAULT)
    {
        rt_kprintf("[SAFETY] EVT_SOFT_FAULT\n");
        safety_force_shutdown(FAULT_SOFT);
        return;
    }
}

static void safety_thread_entry(void *param)
{
    rt_uint32_t ev = 0;
    rt_err_t e;

    (void)param;

    while (1)
    {
        if (!safety_events_ready())      /* 事件系统未就绪, 等待状态机 init */
        {
            rt_thread_mdelay(10);
            continue;
        }
        ev = 0;
        e = rt_event_recv(safety_event_handle(), SAFETY_EVT_MASK_ALL,
                          RT_EVENT_FLAG_OR | RT_EVENT_FLAG_CLEAR,
                          rt_tick_from_millisecond(SAFETY_RECV_TIMEOUT), &ev);
        if (e == RT_EOK && ev)
            safety_handle_events(ev);

        /* DIAG 轮询(门控开启后): PA.3 高 = TMC 报警 → 自投事件 */
        if (st_irq_attached)
        {
            rt_base_t diag = safety_pin(PIN_NAME_TMC_DIAG);
            if (diag >= 0 && rt_pin_read(diag) == PIN_HIGH)
                safety_post_event(EVT_TMC_DIAG);
        }
    }
}

/* ---------- 正式 API (safety_thread.h) ---------- */

rt_err_t safety_thread_init(void)
{
    rt_err_t e;

    if (st_thread_up) return RT_EOK;            /* 幂等 */

    e = rt_thread_init(&safety_thread, "safety", safety_thread_entry, RT_NULL,
                       safety_stack, sizeof(safety_stack),
                       SAFETY_THREAD_PRIO, 10);
    if (e != RT_EOK) { st_health = SUBSYS_FAILED; return e; }
    rt_thread_startup(&safety_thread);
    st_thread_up = RT_TRUE;

    st_health = SUBSYS_OK;
    rt_kprintf("[SAFETY] thread started (prio %d, IRQ gate CLOSED)\n",
               SAFETY_THREAD_PRIO);
    return RT_EOK;
}

rt_err_t safety_irq_attach(void)
{
    rt_base_t pin;
    rt_err_t e;

    if (st_irq_attached) return RT_EOK;

    /* 前置: Safety Thread 必须在跑(否则事件无人消费) */
    if (!st_thread_up) return -RT_ERROR;

    pin = safety_pin(PIN_NAME_ESTOP);
    if (pin < 0) return -RT_ERROR;

    /* NC+GND 方案: 常态闭合=LOW, 拍下/断线=HIGH → 上升沿触发 */
    e = rt_pin_attach_irq(pin, PIN_IRQ_MODE_RISING, estop_isr, RT_NULL);
    if (e != RT_EOK) return e;
    e = rt_pin_irq_enable(pin, PIN_IRQ_ENABLE);
    if (e != RT_EOK) return e;

    st_irq_attached = RT_TRUE;
    rt_kprintf("[SAFETY] ESTOP EXTI attached (rising). Gate OPEN.\n");
    rt_kprintf("[SAFETY] ensure ESTOP wired NC->GND before arming!\n");
    return RT_EOK;
}

rt_bool_t safety_irq_attached(void) { return st_irq_attached; }
subsys_health_t safety_thread_get_health(void) { return st_health; }
