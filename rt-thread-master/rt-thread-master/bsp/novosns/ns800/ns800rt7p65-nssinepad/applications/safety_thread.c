/*
 * safety_thread.c - 安全线程与事件链 (Phase 7-B/C, Fix A IRQ 事务化)
 *
 * 事件流(设计, 交接文档 §14.3):
 *   GPIO ISR / 业务代码 --safety_post_event()--> rt_event
 *     --> Safety Thread(prio 4) rt_event_recv --> 分类 --> safety_force_shutdown()
 *
 * ISR 回调只做 safety_post_event —— 零 SPI/UART/Flash/delay/打印。
 *
 * Fix A 唯一停机路径: 六类事件(ESTOP/LIMIT_MIN/LIMIT_MAX/DIAG/MULTI/SOFT)
 * 全部只调 safety_force_shutdown(); 停 STEP 与 DRV_ENABLE 写+回读由
 * safety_force_shutdown → motor_emergency_stop 统一负责, 此处不再重复调用。
 *
 * Fix A IRQ 门:
 *   - safety_irq_attach() 事务化: 任一步失败按逆序 disable+detach 已注册的
 *     输入, 门保持 CLOSED, 可安全重复调用。
 *   - 门只能由人工 MSH 命令 safety_irq_enable 打开, 且必须先显式完成
 *     safety_polarity_confirm CONFIRM(操作员实测极性的声明, 软件不自动判定)。
 *   - 默认不注册任何 EXTI: 悬空输入注册中断会造成 interrupt storm。
 *
 * P0-3: 四路输入(ESTOP=EXTI6 / LIMIT_MIN=EXTI14 / LIMIT_MAX=EXTI15 /
 * DIAG=EXTI3, 与冻结表一致无冲突)是 EVT_ESTOP / EVT_LIMIT_MIN /
 * EVT_LIMIT_MAX / EVT_TMC_DIAG 的唯一生产者(旧 DIAG 轮询已移除, 避免双触发)。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "app_health.h"
#include "safety_state.h"
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
/* P0-2: 输入极性/接线验证标志 —— 默认 FALSE(HARDWARE-PENDING)。
 * 真机完成 NC 极性验证后由 safety_polarity_confirm CONFIRM 置位(内存态,
 * 掉电不复位也不自动恢复)。软件不做任何极性推断。 */
static rt_bool_t st_polarity_validated = RT_FALSE;

/* ---------- ISR 回调: 只置事件(§14.3 纪律) ---------- */
static void estop_isr(void *args)    { (void)args; safety_post_event(EVT_ESTOP); }
static void lim_min_isr(void *args)  { (void)args; safety_post_event(EVT_LIMIT_MIN); }
static void lim_max_isr(void *args)  { (void)args; safety_post_event(EVT_LIMIT_MAX); }
static void tmc_diag_isr(void *args) { (void)args; safety_post_event(EVT_TMC_DIAG); }

/* 四路安全输入(注册顺序固定; bit i 用于 attach 事务掩码) */
struct safety_input
{
    const char         *name;
    void (*isr)(void *args);
};

static const struct safety_input st_inputs[] =
{
    { PIN_NAME_ESTOP,     estop_isr    },   /* bit 0 */
    { PIN_NAME_LIMIT_MIN, lim_min_isr  },   /* bit 1 */
    { PIN_NAME_LIMIT_MAX, lim_max_isr  },   /* bit 2 */
    { PIN_NAME_TMC_DIAG,  tmc_diag_isr },   /* bit 3 */
};

#define ST_INPUT_N ((int)(sizeof(st_inputs) / sizeof(st_inputs[0])))

/* ---------- 事件处理(线程上下文) ---------- */
static void safety_handle_events(rt_uint32_t ev)
{
    if (ev & EVT_ESTOP)
    {
        rt_kprintf("[SAFETY] EVT_ESTOP\n");
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

/* 逆序拆掉已注册的 IRQ: 只 disable 真正 enable 过的, 只 detach attach 过的。
 * best-effort, 不回报错误(回滚路径本身不能再失败)。 */
static void st_irq_rollback(rt_uint32_t attached, rt_uint32_t enabled,
                            const char *tag)
{
    int i;

    for (i = ST_INPUT_N - 1; i >= 0; --i)
    {
        rt_base_t pin;

        if (!(attached & (1u << i))) continue;
        pin = safety_pin(st_inputs[i].name);
        if (pin < 0) continue;

        if (enabled & (1u << i))
        {
            rt_err_t de = rt_pin_irq_enable(pin, PIN_IRQ_DISABLE);
            if (de != RT_EOK)
                rt_kprintf("[SAFETY] %s: disable %s FAILED (%d)\n",
                           tag, st_inputs[i].name, de);
        }
        {
            rt_err_t te = rt_pin_detach_irq(pin);
            rt_kprintf("[SAFETY] %s: %s detached (%d)\n",
                       tag, st_inputs[i].name, te);
        }
    }
    st_irq_attached = RT_FALSE;
}

/* 四路 EXTI 注册(一次全开; 极性=上升沿, NC 断开/报警线拉高=触发)。
 * Fix A: 事务化 —— 任一步失败逆序 disable+detach, 门保持 CLOSED。可重复调用。 */
rt_err_t safety_irq_attach(void)
{
    rt_uint32_t attached = 0, enabled = 0;
    rt_err_t e = RT_EOK;
    int i;

    if (st_irq_attached) return RT_EOK;         /* 幂等: 门已开 */

    /* 前置: Safety Thread 必须在跑(否则事件无人消费) */
    if (!st_thread_up)
    {
        rt_kprintf("[SAFETY] irq attach REFUSED: safety thread not running\n");
        return -RT_ERROR;
    }

    for (i = 0; i < ST_INPUT_N; ++i)
    {
        rt_base_t pin = safety_pin(st_inputs[i].name);

        if (pin < 0)
        {
            rt_kprintf("[SAFETY] irq attach: %s pin unresolved\n", st_inputs[i].name);
            e = -RT_EINVAL;
            break;
        }
        e = rt_pin_attach_irq(pin, PIN_IRQ_MODE_RISING, st_inputs[i].isr, RT_NULL);
        if (e != RT_EOK)
        {
            rt_kprintf("[SAFETY] attach %s failed (%d)\n", st_inputs[i].name, e);
            break;
        }
        attached |= 1u << i;

        e = rt_pin_irq_enable(pin, PIN_IRQ_ENABLE);
        if (e != RT_EOK)
        {
            rt_kprintf("[SAFETY] enable %s failed (%d)\n", st_inputs[i].name, e);
            break;
        }
        enabled |= 1u << i;
    }

    if (e != RT_EOK)
    {
        rt_kprintf("[SAFETY] irq attach FAILED (attached=0x%X enabled=0x%X)"
                   " - rolling back\n", attached, enabled);
        st_irq_rollback(attached, enabled, "rollback");
        rt_kprintf("[SAFETY] IRQ gate stays CLOSED\n");
        return e;
    }

    st_irq_attached = RT_TRUE;
    rt_kprintf("[SAFETY] 4x EXTI attached (ESTOP/LIM_MIN/LIM_MAX/DIAG, rising)."
               " Gate OPEN.\n");
    rt_kprintf("[SAFETY] ensure NC->GND wiring before arming!\n");
    return RT_EOK;
}

/* 全拆(供 safety_irq_disable) */
static void st_irq_detach_all(void)
{
    st_irq_rollback((1u << ST_INPUT_N) - 1u, (1u << ST_INPUT_N) - 1u, "detach");
    rt_kprintf("[SAFETY] IRQ gate CLOSED - 四路安全输入无软件响应路径\n");
}

rt_bool_t safety_irq_attached(void) { return st_irq_attached; }

rt_bool_t safety_protection_ready(void)
{
    int i;

    if (!st_irq_attached || !st_polarity_validated) return RT_FALSE;
    if (!safety_gpio_ready()) return RT_FALSE;

    /* 四路可解析且当前全部处于安全电平(0, NC+上拉方案) */
    for (i = 0; i < ST_INPUT_N; ++i)
    {
        rt_base_t pin = safety_pin(st_inputs[i].name);
        if (pin < 0 || rt_pin_read(pin) != PIN_LOW) return RT_FALSE;
    }
    return RT_TRUE;
}

rt_bool_t safety_polarity_validated(void) { return st_polarity_validated; }

subsys_health_t safety_thread_get_health(void) { return st_health; }

/* ---------- Fix A: 人工 IRQ 门 MSH 表面 ----------
 * 门默认关闭; 只有操作员显式声明极性已实测后才允许打开。 */

static void st_print_inputs(void)
{
    int i;

    for (i = 0; i < ST_INPUT_N; ++i)
    {
        rt_base_t pin = safety_pin(st_inputs[i].name);

        if (pin < 0)
            rt_kprintf("[SAFETY] %-11s pin=UNRESOLVED raw=-\n", st_inputs[i].name);
        else
            rt_kprintf("[SAFETY] %-11s pin=%d raw=%d\n",
                       st_inputs[i].name, (int)pin, (int)rt_pin_read(pin));
    }
}

static void safety_irq_status(void)
{
    rt_kprintf("[SAFETY] irq gate   = %s\n",
               st_irq_attached ? "OPEN (EXTI registered)" : "CLOSED (polled only)");
    rt_kprintf("[SAFETY] polarity   = %s (HARDWARE-PENDING unless CONFIRMed)\n",
               st_polarity_validated ? "operator CONFIRMED" : "NOT confirmed");
    rt_kprintf("[SAFETY] gpio_ready = %s\n", safety_gpio_ready() ? "YES" : "NO");
    rt_kprintf("[SAFETY] thread     = %s health=%s\n",
               st_thread_up ? "running" : "off", subsys_health_name(st_health));
    rt_kprintf("[SAFETY] protection_ready = %s\n",
               safety_protection_ready() ? "YES" : "NO");
    st_print_inputs();
}
MSH_CMD_EXPORT(safety_irq_status, show safety EXTI gate and raw input levels);

static void safety_irq_enable(void)
{
    if (!st_thread_up)
    {
        rt_kprintf("[SAFETY] irq enable REFUSED: safety thread not running\n");
        return;
    }
    if (!st_polarity_validated)
    {
        rt_kprintf("[SAFETY] irq enable REFUSED: input polarity NOT confirmed\n");
        rt_kprintf("[SAFETY] hardware wiring is HARDWARE-PENDING. After measuring\n");
        rt_kprintf("[SAFETY] NC+pull-up levels by hand run:\n");
        rt_kprintf("[SAFETY]   safety_polarity_confirm CONFIRM\n");
        rt_kprintf("[SAFETY] Dangling inputs with EXTI enabled cause interrupt storm.\n");
        return;
    }
    (void)safety_irq_attach();
}
MSH_CMD_EXPORT(safety_irq_enable, open safety EXTI gate (needs polarity confirm));

static void safety_irq_disable(void)
{
    if (!st_irq_attached)
    {
        rt_kprintf("[SAFETY] irq gate already CLOSED\n");
        return;
    }
    st_irq_detach_all();
}
MSH_CMD_EXPORT(safety_irq_disable, close safety EXTI gate and detach all four);

/* 真机极性验证完成后的人工声明(硬件 pending, 软件不做任何推断)。
 * Fix A: 必须带字面量参数 CONFIRM —— 无参数/错参数一律拒绝, 防误触。 */
static void safety_polarity_confirm(int argc, char **argv)
{
    if (argc != 2 || rt_strcmp(argv[1], "CONFIRM") != 0)
    {
        rt_kprintf("[SAFETY] usage: safety_polarity_confirm CONFIRM\n");
        rt_kprintf("[SAFETY] refused: literal CONFIRM required (no accidental set)\n");
        return;
    }

    rt_kprintf("[SAFETY] current raw input levels (operator must have MEASURED these):\n");
    st_print_inputs();
    rt_kprintf("[SAFETY] NOTE: this is an OPERATOR DECLARATION. Software does NOT\n");
    rt_kprintf("[SAFETY]       verify wiring. Status stays HARDWARE-PENDING for the\n");
    rt_kprintf("[SAFETY]       hardware chain; only the polarity flag changes here.\n");

    st_polarity_validated = RT_TRUE;
    rt_kprintf("[SAFETY] input polarity MARKED VALIDATED (operator confirm)\n");
    rt_kprintf("[SAFETY] protection_ready = %s\n",
               safety_protection_ready() ? "YES" : "NO (IRQ gate still CLOSED)");
}
MSH_CMD_EXPORT(safety_polarity_confirm, declare input polarity measured - needs CONFIRM token);
