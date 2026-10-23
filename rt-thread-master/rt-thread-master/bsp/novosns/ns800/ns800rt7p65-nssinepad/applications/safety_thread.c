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
 * P0-3: 输入生产者登记(与冻结表一致, EXTI 无冲突)。
 *
 * 2026-10-21 输入职责更正(用户确认 PC.6 实际接扩展板 H6-5/TMC_ENN + RevA 图纸核对):
 *   - **PC.6 不再是急停输入**，也不注册任何 ISR；它是驱动使能状态**反馈**(被动读)，
 *     判定在 safety_gpio.c 的反馈接口里做。板上急停是纯硬件链(常闭→U13→U5→Q1)，
 *     图纸上没有送进 MCU 的网络，因此**软件不存在独立急停输入**。
 *   - 两路行程限位由 SAFETY_LIMIT_INPUTS_ENABLED 决定：RT_FALSE(目标默认)时**完全不进入**
 *     解析/配置/读取/IRQ 注册，也不参与就绪与故障清除；RT_TRUE 时按反相后的 OK 信号
 *     **HIGH=安全、LOW=触发、下降沿 IRQ**接入。
 *   - DIAG 保持原语义与极性依据，不受限位宏影响。
 *   - EVT_ESTOP / EVT_LIMIT_* 事件位与 FAULT_* 编号为黑匣子兼容**原样保留**；限位宏关闭时
 *     它们没有 GPIO 生产者，软件停机 API 与锁存路径仍然保留(见 safety_state.c)。
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
                               EVT_TMC_DIAG | EVT_MULTI_FAULT | EVT_SOFT_FAULT | \
                               EVT_DRV_FEEDBACK)

static struct rt_thread safety_thread;
static rt_uint8_t safety_stack[SAFETY_THREAD_STACK];
static rt_bool_t st_thread_up = RT_FALSE;
static subsys_health_t st_health = SUBSYS_UNINIT;
static rt_bool_t st_irq_attached = RT_FALSE;
/* P0-2: 输入极性/接线验证标志 —— 默认 FALSE(HARDWARE-PENDING)。
 * 真机完成极性验证后由 safety_polarity_confirm CONFIRM 置位(内存态,
 * 掉电不复位也不自动恢复)。软件不做任何极性推断, 用户"确认接了哪根线"
 * 也不等于本标志被置位。 */
static rt_bool_t st_polarity_validated = RT_FALSE;

/* ---------- ISR 回调: 只置事件(§14.3 纪律) ---------- */
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
static void lim_min_isr(void *args)  { (void)args; safety_post_event(EVT_LIMIT_MIN); }
static void lim_max_isr(void *args)  { (void)args; safety_post_event(EVT_LIMIT_MAX); }
#endif
static void tmc_diag_isr(void *args) { (void)args; safety_post_event(EVT_TMC_DIAG); }

/* 活动安全输入登记表(注册顺序固定; bit i 用于 attach 事务掩码)。
 * safe_level/irq_mode 来自 RevA 图纸: 限位经 U12/U11 反相 → HIGH=未触发, 断开=下降沿触发;
 * DIAG 沿用原有"上升沿=报警"注册(其物理极性仍是 HARDWARE-PENDING, 未在本轮改动)。 */
struct safety_input
{
    const char         *name;
    void              (*isr)(void *args);
    rt_uint8_t          irq_mode;
    rt_int32_t          safe_level;
    const char         *label;
};

static const struct safety_input st_inputs[] =
{
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
    { PIN_NAME_LIMIT_MIN, lim_min_isr,  PIN_IRQ_MODE_FALLING, PIN_HIGH, "LIMIT_MIN_OK" }, /* bit 0 */
    { PIN_NAME_LIMIT_MAX, lim_max_isr,  PIN_IRQ_MODE_FALLING, PIN_HIGH, "LIMIT_MAX_OK" }, /* bit 1 */
#endif
    { PIN_NAME_TMC_DIAG,  tmc_diag_isr, PIN_IRQ_MODE_RISING,  PIN_LOW,  "TMC_DIAG"      },
};

#define ST_INPUT_N ((int)(sizeof(st_inputs) / sizeof(st_inputs[0])))

/* ---------- 事件处理(线程上下文) ----------
 * 2026-10-23 S2(任务书 §8.1): 到这里的事件**都已经被投递方保护过** ——
 * safety_post_event() 对故障类事件先同步做一次短保护, safety_notify_trip_done() 只
 * 交接"已经保护过"的事实。因此本函数一律走 safety_reconcile_trip()(慢整理: 状态锁存
 * + 回读复核 + 黑匣子一次), **不再**调用 safety_force_shutdown() 重做一遍关断;
 * 否则同一波停机里会出现第二次 rt_pwm_disable, 让第一次的失败被第二次的成功掩盖。
 * 动作之前的 rt_kprintf 全部移到动作之后: 控制台是逐字符忙等(每字符上限
 * BSP_NS800_UART_TX_TIMEOUT=6000ms), 它不能再排在降险动作前面。 */
static void safety_handle_events(rt_uint32_t ev)
{
    if (ev & EVT_ESTOP)
    {
        /* 历史事件位(黑匣子/状态机兼容保留): 2026-10-21 起**没有 GPIO 生产者** ——
         * PC.6 是 TMC_ENN 反馈, 板上急停不经 MCU。软件停机 API 与锁存路径仍然保留。 */
        safety_reconcile_trip(FAULT_ESTOP, EVT_ESTOP);
        safety_transition(SAFETY_ESTOP);   /* FAULT_LATCHED -> ESTOP(白名单允许) */
        rt_kprintf("[SAFETY] EVT_ESTOP -> 已整理(短保护由投递方执行)\n");
        return;
    }
    if (ev & EVT_LIMIT_MIN)
    {
        safety_reconcile_trip(FAULT_LIMIT_MIN, EVT_LIMIT_MIN);
        rt_kprintf("[SAFETY] EVT_LIMIT_MIN -> 已整理\n");
        return;
    }
    if (ev & EVT_LIMIT_MAX)
    {
        safety_reconcile_trip(FAULT_LIMIT_MAX, EVT_LIMIT_MAX);
        rt_kprintf("[SAFETY] EVT_LIMIT_MAX -> 已整理\n");
        return;
    }
    if (ev & EVT_TMC_DIAG)
    {
        safety_reconcile_trip(FAULT_TMC_DIAG, EVT_TMC_DIAG);
        rt_kprintf("[SAFETY] EVT_TMC_DIAG -> 已整理\n");
        return;
    }
    if (ev & EVT_MULTI_FAULT)
    {
        safety_reconcile_trip(FAULT_MULTI_SOURCE, EVT_MULTI_FAULT);
        rt_kprintf("[SAFETY] EVT_MULTI_FAULT -> 已整理\n");
        return;
    }
    if (ev & EVT_SOFT_FAULT)
    {
        safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
        rt_kprintf("[SAFETY] EVT_SOFT_FAULT -> 已整理\n");
        return;
    }
    if (ev & EVT_DRV_FEEDBACK)
    {
        /* 追加(2026-10-21): 驱动请求与最终 TMC_ENN 反馈不一致(说停没停 / 要动没让动 /
         * 样本不可得)。如实报成"反馈不一致", 不假冒已读到急停按钮状态。 */
        safety_reconcile_trip(FAULT_DRV_FEEDBACK, EVT_DRV_FEEDBACK);
        rt_kprintf("[SAFETY] EVT_DRV_FEEDBACK -> 已整理"
                   " (drive request vs TMC_ENN feedback mismatch)\n");
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
        {
            safety_handle_events(ev);
            continue;
        }
        /* 追加(2026-10-23 S2, 任务书 §8.1): 接收超时也要检查"已保护但未整理"的事实。
         * 投递方可能在 rt_event_send 之前就失败(事件系统竞态/投递返回错误), 那时
         * 物理降险已经做过, 只有 pending 位记录着它 —— 不捡起来就会长期停在
         * "状态机还是旧值 + canonical 已抑制"的不一致上。 */
        if (safety_trip_pending())
        {
            rt_kprintf("[SAFETY] 超时拾取 pending 事实(投递未成功/无事件): 只做整理,"
                       " 不重复关断\n");
            safety_reconcile_trip(FAULT_SOFT, 0u);
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

/* 活动安全输入的 EXTI 注册(一次全开; 每路用自己的极性/边沿, 见 st_inputs)。
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
            rt_kprintf("[SAFETY] irq attach: %s pin unresolved\n", st_inputs[i].label);
            e = -RT_EINVAL;
            break;
        }
        e = rt_pin_attach_irq(pin, (rt_uint32_t)st_inputs[i].irq_mode,
                              st_inputs[i].isr, RT_NULL);
        if (e != RT_EOK)
        {
            rt_kprintf("[SAFETY] attach %s failed (%d)\n", st_inputs[i].label, e);
            break;
        }
        attached |= 1u << i;

        e = rt_pin_irq_enable(pin, PIN_IRQ_ENABLE);
        if (e != RT_EOK)
        {
            rt_kprintf("[SAFETY] enable %s failed (%d)\n", st_inputs[i].label, e);
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
    rt_kprintf("[SAFETY] IRQ gate OPEN: %d active protection input(s) registered"
               " (limits: falling edge / HIGH=safe; diag: rising)\n", ST_INPUT_N);
    rt_kprintf("[SAFETY] PC.6 = TMC_ENN feedback input, NOT an IRQ source;"
               " board e-stop cuts the chain in hardware, never via MCU\n");
    return RT_EOK;
}

/* 全拆(供 safety_irq_disable) */
static void st_irq_detach_all(void)
{
    st_irq_rollback((1u << ST_INPUT_N) - 1u, (1u << ST_INPUT_N) - 1u, "detach");
    rt_kprintf("[SAFETY] IRQ gate CLOSED - 活动安全输入(%d 路)无软件响应路径\n", ST_INPUT_N);
}

rt_bool_t safety_irq_attached(void) { return st_irq_attached; }

rt_bool_t safety_protection_ready(void)
{
    int i;

    if (!st_irq_attached || !st_polarity_validated) return RT_FALSE;
    if (!safety_gpio_ready()) return RT_FALSE;

    /* 活动输入逐路按各自的 safe_level 判定(限位宏关闭时循环里只有 DIAG)。 */
    for (i = 0; i < ST_INPUT_N; ++i)
    {
        rt_base_t pin = safety_pin(st_inputs[i].name);
        if (pin < 0 || rt_pin_read(pin) != st_inputs[i].safe_level) return RT_FALSE;
    }

    /* ENN 反馈条件按**当前已确认的许可请求侧**判定(IO70-NEW-02, 2026-10-21 复审):
     *   请求为 LOW / 从未确认 => 期望禁能侧一致(许可 LOW + 反馈 HIGH);
     *   请求为 HIGH        => 期望使能侧一致(许可 HIGH + 反馈 LOW)。
     * 原实现是"请求 HIGH 就整个跳过反馈条件", 那会让硬件切断(反馈仍 HIGH)或样本丢失
     * 时本接口谎报就绪。换成按请求侧取反期望之后, arm 之后正常的 ENN LOW 仍然就绪,
     * 不再制造 arm->start 阶段死锁, 同时 CUT_BY_HARDWARE / PAD_MISMATCH / NO_SAMPLE
     * 一律不放行。motor.c 在 arm/start/运行守卫上的同源判定继续独立生效(不依赖此处)。 */
    {
        rt_int32_t req = -1, pad = -1, fb = -1;
        rt_bool_t  fb_ok = RT_FALSE;
        rt_uint8_t want;

        safety_fb_snapshot(&req, &pad, &fb, &fb_ok);
        want = (req == PIN_HIGH) ? 1u : 0u;
        if (safety_fb_now(want) != SAFETY_FB_OK) return RT_FALSE;
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
            rt_kprintf("[SAFETY] %-12s pin=UNRESOLVED raw=- safe=%d (counts as NOT ready)\n",
                       st_inputs[i].label, (int)st_inputs[i].safe_level);
        else
            rt_kprintf("[SAFETY] %-12s pin=%d raw=%d safe=%d irq=%s %s\n",
                       st_inputs[i].label, (int)pin, (int)rt_pin_read(pin),
                       (int)st_inputs[i].safe_level,
                       st_inputs[i].irq_mode == PIN_IRQ_MODE_FALLING ? "FALLING" : "RISING",
                       rt_pin_read(pin) == st_inputs[i].safe_level ? "[safe]" : "[!!ACTIVE!!]");
    }

    rt_kprintf("[SAFETY] LIMIT inputs config = %s (SAFETY_LIMIT_INPUTS_ENABLED;"
               " OFF => PF.14/PF.15 不解析/不配置/不读/不注册, 也不参与就绪判定)\n",
               safety_limit_inputs_enabled() ? "ENABLED" : "DISABLED_BY_CONFIG");
    {
        rt_base_t fb = safety_pin(PIN_NAME_ENN_FEEDBACK);

        rt_kprintf("[SAFETY] %-12s pin=%s raw=%s = TMC_ENN feedback (HIGH=disabled side)."
                   " NOT an e-stop input, no IRQ registered on it\n",
                   "PC.6(ENN_FB)", fb < 0 ? "UNRESOLVED" : "ok",
                   fb < 0 ? "-" : ((rt_pin_read(fb) == PIN_HIGH) ? "HIGH" : "LOW"));
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
        rt_kprintf("[SAFETY] the ACTIVE inputs by hand (limits: HIGH=safe via U12/U11;\n");
        rt_kprintf("[SAFETY] diag: as documented) run:\n");
        rt_kprintf("[SAFETY]   safety_polarity_confirm CONFIRM\n");
        rt_kprintf("[SAFETY] Dangling inputs with EXTI enabled cause interrupt storm.\n");
        rt_kprintf("[SAFETY] PC.6(TMC_ENN feedback) is not part of this gate - it has no IRQ.\n");
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
MSH_CMD_EXPORT(safety_irq_disable, close safety EXTI gate and detach active inputs);

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

    rt_kprintf("[SAFETY] current raw levels of the ACTIVE protection inputs"
               " (operator must have MEASURED these):\n");
    st_print_inputs();
    rt_kprintf("[SAFETY] NOTE: this is an OPERATOR DECLARATION. Software does NOT\n");
    rt_kprintf("[SAFETY]       verify wiring. Status stays HARDWARE-PENDING for the\n");
    rt_kprintf("[SAFETY]       hardware chain; only the polarity flag changes here.\n");
    rt_kprintf("[SAFETY]       User-confirmed wire names / gate replacement are NOT a\n");
    rt_kprintf("[SAFETY]       measurement and never set this flag by themselves.\n");

    st_polarity_validated = RT_TRUE;
    rt_kprintf("[SAFETY] input polarity MARKED VALIDATED (operator confirm)\n");
    rt_kprintf("[SAFETY] protection_ready = %s\n",
               safety_protection_ready() ? "YES" : "NO (IRQ gate still CLOSED)");
}
MSH_CMD_EXPORT(safety_polarity_confirm, declare input polarity measured - needs CONFIRM token);
