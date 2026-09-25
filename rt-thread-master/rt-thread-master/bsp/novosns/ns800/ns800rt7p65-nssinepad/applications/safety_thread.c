/*
 * safety_thread.c - 安全线程与事件链 (审查修复 P0-3: 四路 EXTI 生产者)
 *
 * 事件流(设计, 交接文档 §14.3):
 *   GPIO ISR / 业务代码 --safety_post_event()--> rt_event
 *     --> Safety Thread(prio 4) rt_event_recv --> 分类 --> safety_force_shutdown()
 *
 * ISR 回调只做 safety_post_event —— 零 SPI/UART/Flash/delay/打印。
 *
 * P0-3: EVT_LIMIT_MIN/MAX 原本无生产者。现在 safety_irq_attach() 一次性
 * 注册四路输入(ESTOP=EXTI6/LIMIT_MIN=EXTI14/LIMIT_MAX=EXTI15/DIAG=EXTI3,
 * 与冻结表一致无冲突), 门默认关闭(防悬空 interrupt storm)。
 * 旧 DIAG 轮询已移除(EXTI 为唯一生产者, 避免双触发)。
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
#define SAFETY_RECV_TIMEOUT   500     /* ms; 超时仅用于线程节拍 */

#define SAFETY_EVT_MASK_ALL   (EVT_ESTOP | EVT_LIMIT_MIN | EVT_LIMIT_MAX | \
                               EVT_TMC_DIAG | EVT_MULTI_FAULT | EVT_SOFT_FAULT)

static struct rt_thread safety_thread;
static rt_uint8_t safety_stack[SAFETY_THREAD_STACK];
static rt_bool_t st_thread_up = RT_FALSE;
static subsys_health_t st_health = SUBSYS_UNINIT;
static rt_bool_t st_irq_attached = RT_FALSE;

/* ---------- ISR 回调: 只置事件(§14.3 纪律) ---------- */
static void estop_isr(void *args)     { rt_uint32_t e = EVT_ESTOP;     (void)args; safety_post_event(e); }
static void lim_min_isr(void *args)   { rt_uint32_t e = EVT_LIMIT_MIN; (void)args; safety_post_event(e); }
static void lim_max_isr(void *args)   { rt_uint32_t e = EVT_LIMIT_MAX; (void)args; safety_post_event(e); }
static void tmc_diag_isr(void *args)  { rt_uint32_t e = EVT_TMC_DIAG;  (void)args; safety_post_event(e); }

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
        motor_emergency_stop();
        safety_force_shutdown(FAULT_LIMIT_MIN);
        return;
    }
    if (ev & EVT_LIMIT_MAX)
    {
        rt_kprintf("[SAFETY] EVT_LIMIT_MAX\n");
        motor_emergency_stop();
        safety_force_shutdown(FAULT_LIMIT_MAX);
        return;
    }
    if (ev & EVT_TMC_DIAG)
    {
        rt_kprintf("[SAFETY] EVT_TMC_DIAG\n");
        motor_emergency_stop();
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

/* 四路 EXTI 注册(一次全开; 极性=上升沿, NC 断开/报警线拉高=触发) */
rt_err_t safety_irq_attach(void)
{
    struct { const char *name; void (*isr)(void *); } inputs[] = {
        { PIN_NAME_ESTOP,     estop_isr },
        { PIN_NAME_LIMIT_MIN, lim_min_isr },
        { PIN_NAME_LIMIT_MAX, lim_max_isr },
        { PIN_NAME_TMC_DIAG,  tmc_diag_isr },
    };
    int i;

    if (st_irq_attached) return RT_EOK;

    /* 前置: Safety Thread 必须在跑(否则事件无人消费) */
    if (!st_thread_up) return -RT_ERROR;

    for (i = 0; i < (int)(sizeof(inputs) / sizeof(inputs[0])); ++i)
    {
        rt_base_t pin = safety_pin(inputs[i].name);
        rt_err_t e;

        if (pin < 0)
        {
            rt_kprintf("[SAFETY] irq attach: %s pin unresolved\n", inputs[i].name);
            return -RT_ERROR;
        }
        e = rt_pin_attach_irq(pin, PIN_IRQ_MODE_RISING, inputs[i].isr, RT_NULL);
        if (e != RT_EOK) { rt_kprintf("[SAFETY] attach %s failed %d\n", inputs[i].name, e); return e; }
        e = rt_pin_irq_enable(pin, PIN_IRQ_ENABLE);
        if (e != RT_EOK) { rt_kprintf("[SAFETY] enable %s failed %d\n", inputs[i].name, e); return e; }
    }

    st_irq_attached = RT_TRUE;
    rt_kprintf("[SAFETY] 4x EXTI attached (ESTOP/LIM_MIN/LIM_MAX/DIAG, rising). Gate OPEN.\n");
    rt_kprintf("[SAFETY] ensure NC->GND wiring before arming!\n");
    return RT_EOK;
}

rt_bool_t safety_irq_attached(void) { return st_irq_attached; }
subsys_health_t safety_thread_get_health(void) { return st_health; }
