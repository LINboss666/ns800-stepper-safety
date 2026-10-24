"""CP4 §9: 追加 sp_conc_main.c 的用例与台账(修正版)。"""
import io
import os

# 路径按脚本自身位置推导: 本目录 = <仓库根>/tests/host/sp_conc_gen, 不写死任何机器路径。
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
import io

OUT = os.path.join(_ROOT, "tests", "host", "sp_conc_main.c")

CRLF = chr(13) + chr(10)
LF = chr(10)
t = io.open(OUT, encoding="utf-8", newline="").read().replace("\r\n", "\n")


# 本文件可能被 git core.autocrlf 以 CRLF 检出, 而嵌入锚点是与**已归一化为 LF** 的目标文本比对:
# 先把成对字符串自己也归一化, 生成结果就与检出行尾无关(本机实测: 未归一时 CRLF 检出会 ANCHOR MISSING)。
def rep(old, new, cnt=1):
    global t
    old = old.replace(CRLF, LF)
    new = new.replace(CRLF, LF)
    assert t.count(old) == cnt, (old[:60].replace("\n", "\\n"), t.count(old))
    t = t.replace(old, new)


# ---- 修正①: ISR 交付必须在窗口解锁之后(否则同一线程对全局独占锁自我死锁) ----
rep("""    s_intr_depth = (int)level;
    if (s_intr_depth == 0)
    {
        s_intr_masked = 0;
        s2_dispatch_pending();               /* 窗口一结束就响应挂着的中断 */
        s2_win_live = 0;
        pthread_mutex_unlock(&s2_atomic);
    }""",
    """    s_intr_depth = (int)level;
    if (s_intr_depth == 0)
    {
        s_intr_masked = 0;
        s2_win_live = 0;
        pthread_mutex_unlock(&s2_atomic);
        /* 窗口一结束就响应挂着的中断(与目标上"退出临界区后立即进 ISR"同序)。
         * ⚠ 必须在解锁**之后**交付: 被交付的 ISR 自己会再开一个短临界区。 */
        s2_dispatch_pending();
    }""")

rep("""#define S2_SEQ_BITS 24""", """#define S2_SEQ_BITS 24   /* 其余序列/竞争观测点见端口层与"序列点"小节 */""")

body = r'''

/* ===================================================================== */
/*  CP4 §9: S2 并发夹具 —— C01..C21 的真实线程 / 真互斥量 / 排队 ISR 用例    */
/* ===================================================================== */
/* 与被审方自述的边界(如实登记, 不越界声称):
 *   - 短临界区在主机上是**一把全局互斥量**, 复现"单核 + PRIMASK 屏蔽期内不可抢占";
 *     不模拟真实中断响应时延, 不模拟 NMI/HardFault, 也不模拟别的总线主设备。
 *   - 生产自己的斜坡/Safety 线程不由 rt_thread_startup 真起(与既有四链一致),
 *     由用例显式驱动 motor_ramp_tick / safety_reconcile_trip;
 *     "另一条业务线程"是夹具用 pthread **真起、真持有 ss_lock/mot_lock** 的线程 ——
 *     C01/C02/C14 的阻塞与 C04 的排队都是真阻塞/真排队, 不是单线程重入。
 *   - 墙钟超时(5s)只防死锁: 命中即记 PRE-FAIL/FAIL, 不作为时延结论, 也不算通过。
 *   - 真实寄存器/驱动叶子(drv_epwm.c 的 TBCTL 接缝、drv_uart.c 的
 *     ns800_uart_write_budgeted 等待循环、TMC cfg/xfer 叶锁与 RX 洪泛)不在本链,
 *     对应 C10/C16/C17/C18 —— 见 run_stop_priority_s2.sh 的 LEAF 段与交接登记。
 */
#include "stop_priority.c"
#include "step_pwm.c"
#include "motor.c"
#include "safety_state.c"

/* ---------------- 台账 ---------------- */
static int g_run, g_pass, g_fail, g_pre, g_skip;

static void report(const char *id, int ok, const char *detail)
{
    g_run++;
    if (ok) { g_pass++; printf("CASE|%s|PASS|%s\n", id, detail); }
    else    { g_fail++; printf("CASE|%s|FAIL|%s\n", id, detail); }
}
#define CHECK(id, cond, detail) report((id), (cond) ? 1 : 0, (detail))
/* 需要"成功输出前提"的 ID 在 target 变体里**不登记为用例**, 只声明跳过原因。 */
#define SKIPV(id, why) do { g_skip++; printf("SKIP|%s|%s\n", (id), (why)); } while (0)

static char g_buf[420];
/* 台账打印的格式/实参由编译器核对(2026-10-24 C05d 少一个实参直接段错误)。
     * 只在主机夹具上加, 产品代码不动。 */
__attribute__((format(printf, 1, 2)))
static const char *fm(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_buf, sizeof(g_buf), fmt, ap);
    va_end(ap);
    return g_buf;
}
static int pre_ok(const char *what, int cond)
{
    if (!cond) { g_pre++; printf("PRE-FAIL|%s\n", what); }
    return cond;
}
static int seq_or_fail(const char *what, int bit)
{
    int r = s2_seq_wait(bit, 5000);
    if (r != 0) { g_pre++; printf("SEQ-TIMEOUT|%s\n", what); }
    return r == 0;
}

/* ---------------- 公共前置 ---------------- */
#define BIT_HELD     0
#define BIT_SEEN     1
#define BIT_RELEASE  2
#define BIT_ERR      3

static int gates_open(void)
{
    return (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED &&
            TMC_DRIVE_CONFIG_HW_VALIDATED) ? 1 : 0;
}
static int diag_gates_open(void)
{
    return (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ||
            STEP_DIAG_ISOLATED_ENV_CONFIRMED) ? 1 : 0;
}

static void wire_and_boot(void)
{
    ps_sim_reset();
    ps_sim_add_pin(PIN_NAME_DRV_ENABLE, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIR,    PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIAG,   PIN_LOW);
    ps_sim_add_pin(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);
    ps_sim_dev_set_present(1);
    ps_sim_set_pad_stuck(0);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    ps_set_enable_write_rc(RT_EOK);
    ps_set_integrity(RT_EOK);
    ps_set_cfg_ok(RT_TRUE, TMC_DRV_CONFIGURED);
    ps_set_tmc_health(SUBSYS_OK);
    ps_set_protect(1, 1, 1, 1);
    ps_set_confirm_fail_after(-1);
    s2_seq_reset();
    s2_isr_stats_clear();
    s2_lock_fail_clear();
    s2_contend_clear();
    s2_event_send_fail = 0;
    s2_logs_in_isr = 0;
    s2_set_hook(RT_NULL, "reset");
    ps_isr_set(0);
    ps_win_violations_clear();
    (void)safety_state_boot();               /* 幂等 */
    (void)motor_init();                      /* 幂等: 已建线程直接 EOK */
    /* 生产静态量(sp 的 inhibit/锁存、SS 状态)跨用例存续: 先带回 READY 再跑本用例。 */
    if (safety_state_get() != SAFETY_READY)
    {
        sp_proof_t pf2;
        rt_memset(&pf2, 0, sizeof(pf2));
        if (!sp_epoch_exhausted())
        {
            (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf2);
            (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
            fault_reset();
        }
        (void)safety_startup_selftest();
    }
    (void)sp_bind_pwm((struct rt_device_pwm *)rt_device_find(EPWM_STEP_DEV_NAME), 0);
}

/* 一次干净的"故障 -> 整理 -> 人工恢复 -> 自检回 READY"(全走生产入口) */
static void trip_and_reset(void)
{
    sp_proof_t pf;
    rt_memset(&pf, 0, sizeof(pf));
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    fault_reset();
    (void)safety_startup_selftest();
}

static motor_snapshot_t snap_now(void)
{
    motor_snapshot_t s;
    rt_memset(&s, 0, sizeof(s));
    (void)motor_get_snapshot(&s);
    return s;
}

static void msh_pwm(const char *arg)
{
    char *av[2];
    av[0] = (char *)"pwm_test";
    av[1] = (char *)arg;
    pwm_test(2, av);
}

/* ===================================================================== */
/* C01 / C02: 另一线程**真持有** ss_lock / mot_lock                        */
/* ===================================================================== */
static const char *g_hold_name;
static int  g_hold_saw_disable, g_hold_saw_inhibit, g_hold_saw_pending;
static int  g_hold_saw_ss_state, g_hold_saw_mot_state;
static int  g_hold_contend, g_hold_done;

static void holder_thread(void *a)
{
    struct rt_mutex *m;

    (void)a;
    m = s2_mtx_by_name(g_hold_name);
    if (m == RT_NULL) { s2_seq_set(BIT_ERR); return; }
    (void)rt_mutex_take(m, RT_WAITING_FOREVER);          /* 真持有 */
    s2_seq_set(BIT_HELD);
    if (seq_or_fail("holder sees contender", S2_SEQ_CONTEND))
    {
        sp_snapshot_t s;

        sp_get(&s);
        g_hold_saw_disable   = ps_sim_pwm_disable_calls();
        g_hold_saw_inhibit   = s.inhibit ? 1 : 0;
        g_hold_saw_pending   = s.metadata_pending ? 1 : 0;
        g_hold_saw_ss_state  = (int)ss_state;
        g_hold_saw_mot_state = (int)mot.state;
        g_hold_contend       = s2_contend_hits;
    }
    s2_seq_set(BIT_SEEN);
    (void)rt_mutex_release(m);
    g_hold_done = 1;
}

static void run_contended(const char *name, int via_emergency)
{
    g_hold_name = name;
    g_hold_saw_disable = -1; g_hold_saw_inhibit = -1; g_hold_saw_pending = -1;
    g_hold_saw_ss_state = -1; g_hold_saw_mot_state = -1;
    g_hold_contend = 0; g_hold_done = 0;
    s2_seq_reset();
    s2_contend_clear();
    s2_spawn(holder_thread, RT_NULL);
    if (!seq_or_fail("holder holds", BIT_HELD)) { s2_join_all(); return; }
    if (via_emergency) (void)motor_emergency_stop();
    else               safety_force_shutdown(FAULT_SOFT);
    if (!seq_or_fail("holder observed", BIT_SEEN)) { s2_join_all(); return; }
    s2_join_all();
}

static void cases_lock(void)
{
    sp_snapshot_t s;
    int d0, bb0, tk0, hits;

    /* ---- C01: 另一线程真持 ss_lock; 快保护先完成, 慢整理真等锁 ---- */
    wire_and_boot();
    if (!pre_ok("C01 READY", safety_state_get() == SAFETY_READY)) return;
    d0 = ps_sim_pwm_disable_calls();
    bb0 = ps_bb_triggers();
    tk0 = ps_sim_mutex_takes();
    run_contended("ss", 0);
    sp_get(&s);
    CHECK("C01-fast-before-lock-then-wait",
          g_hold_saw_disable == d0 + 1 && g_hold_saw_inhibit == 1 &&
          g_hold_saw_pending == 1 && g_hold_contend >= 1 &&
          g_hold_saw_ss_state != (int)SAFETY_FAULT_LATCHED && g_hold_done == 1,
          fm("观察时: disable 已到 %d(=%d+1) inhibit=%d pending=%d 等锁命中=%d(%s)"
             " ss_state=%s(尚未落位)", g_hold_saw_disable, d0, g_hold_saw_inhibit,
             g_hold_saw_pending, g_hold_contend, s2_contend_name,
             safety_state_name((safety_state_t)g_hold_saw_ss_state)));
    CHECK("C01b-commits-after-release-no-second-close",
          s.metadata_pending == RT_FALSE && ps_bb_triggers() == bb0 + 1 &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          ps_sim_pwm_disable_calls() == d0 + 1 && ps_sim_mutex_takes() > tk0,
          fm("放锁后整理完成: pending=%s blackbox %d->%d disable %d->%d"
             " mutex_take 增量=%d", s.metadata_pending ? "YES" : "no", bb0,
             ps_bb_triggers(), d0, ps_sim_pwm_disable_calls(),
             ps_sim_mutex_takes() - tk0));

    /* ---- C01c: ss_lock 取用报错 => 不 ack / 不落位 / 不触发黑匣子(K1) ---- */
    wire_and_boot();
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    bb0 = ps_bb_triggers();
    s2_set_lock_fail("ss");
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    hits = s2_lock_fail_count();                 /* clear 会连计数一起清零, 先取走 */
    s2_lock_fail_clear();
    sp_get(&s);
    CHECK("C01c-ss-lock-error-no-ack",
          s.metadata_pending == RT_TRUE && s.inhibit == RT_TRUE &&
          ps_bb_triggers() == bb0 && hits >= 1,
          fm("报错 %d 次: pending=%s inhibit=%s blackbox %d->%d",
             hits, s.metadata_pending ? "YES" : "no",
             s.inhibit ? "YES" : "no", bb0, ps_bb_triggers()));

    /* ---- C02: 另一线程真持 mot_lock(显式急停的慢整理等锁) ---- */
    wire_and_boot();
    if (!pre_ok("C02 READY", safety_state_get() == SAFETY_READY)) return;
    d0 = ps_sim_pwm_disable_calls();
    bb0 = ps_bb_triggers();
    run_contended("mot", 1);
    /* 整理义务的唯一消费者是 Safety 侧的统一慢整理(§3.2): motor_emergency_stop 只做
     * 自己那一路的影子落位, **不** ack pending(否则同一条事实会被整理两次、黑匣子
     * 触发两次)。所以放锁之后再走一次消费者入口, 并断言 ack/黑匣子属于那一方。 */
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    sp_get(&s);
    CHECK("C02-mot-lock-held-then-commits",
          g_hold_saw_disable == d0 + 1 && g_hold_contend >= 1 &&
          g_hold_saw_mot_state != (int)MOTOR_FAULT && g_hold_done == 1 &&
          mot.state == MOTOR_FAULT &&
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          s.metadata_pending == RT_FALSE && ps_bb_triggers() == bb0 + 1,
          fm("观察: disable %d(=%d+1) 取锁记录=%d(%s) mot.state=%d(锁未放时未落位)"
             " 放锁后 state=%d; 消费者整理后: pending=%s blackbox %d->%d",
             g_hold_saw_disable, d0, g_hold_contend, s2_contend_name,
             g_hold_saw_mot_state, (int)mot.state,
             s.metadata_pending ? "YES" : "no", bb0, ps_bb_triggers()));

    /* ---- C02b: mot_lock 报错 => 不落位 / 不 ack / 不黑匣子 ---- */
    wire_and_boot();
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    bb0 = ps_bb_triggers();
    s2_set_lock_fail("mot");
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    hits = s2_lock_fail_count();                 /* 同上: 先取走再清零 */
    s2_lock_fail_clear();
    sp_get(&s);
    CHECK("C02b-mot-lock-error-no-place",
          s.metadata_pending == RT_TRUE && mot.state != MOTOR_FAULT &&
          ps_bb_triggers() == bb0 && hits >= 1,
          fm("报错 %d 次: pending=%s mot.state=%d blackbox %d->%d",
             hits, s.metadata_pending ? "YES" : "no",
             (int)mot.state, bb0, ps_bb_triggers()));
}

/* ===================================================================== */
/* C03: 首请求来自 ISR 上下文 / 事件未建 / 发送失败 —— 只降险, 不打日志      */
/* ===================================================================== */
static void cases_isr(void)
{
    rt_uint32_t ns = 0, sf = 0, ls = 0;
    sp_snapshot_t s;
    rt_err_t rc;
    int d0, low0;

    /* ---- C03a: 事件系统尚未建立(线程未起) + ISR 上下文的首条故障 ---- */
    ps_sim_reset();
    ps_sim_add_pin(PIN_NAME_DRV_ENABLE, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIR, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_TMC_DIAG, PIN_LOW);
    ps_sim_add_pin(PIN_NAME_ENN_FEEDBACK, PIN_HIGH);
    ps_sim_dev_set_present(0);                 /* 设备还没绑: 模拟"最早的一条请求" */
    s2_seq_reset();
    s2_isr_stats_clear();
    s2_lock_fail_clear();
    ps_win_violations_clear();
    s2_logs_in_isr = 0;
    low0 = ps_enable_write_calls(0);
    ps_isr_set(1);                             /* 整个调用都在中断上下文里 */
    rc = safety_post_event(EVT_ESTOP);
    ps_isr_set(0);
    sp_get(&s);
    safety_isr_event_stats(&ns, &sf, &ls);
    CHECK("C03a-isr-early-protected-quiet",
          rc == -RT_ERROR && ns >= 1 && s.inhibit == RT_TRUE &&
          s.metadata_pending == RT_TRUE &&
          ps_enable_write_calls(0) == low0 + 1 &&
          s.pwm.result == MOT_STOP_DEV_UNAVAILABLE && s.pwm.raw_rc == -RT_ENOSYS &&
          s2_logs_in_isr == 0 && ps_win_violations() == 0 && ls >= 1,
          fm("rc=%d no_system=%u log_suppressed=%u ISR 内打印=%d 窗口禁止项=%d"
             " LOW %d->%d pwm=%s(%d) inhibit=%s pending=%s", (int)rc, ns, ls,
             s2_logs_in_isr, ps_win_violations(), low0, ps_enable_write_calls(0),
             mot_stop_result_name(s.pwm.result), (int)s.pwm.raw_rc,
             s.inhibit ? "YES" : "no", s.metadata_pending ? "YES" : "no"));

    /* ---- C03b: 事件已建但发送失败, 仍在 ISR 上下文 => 计数不打印 ---- */
    wire_and_boot();
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    s2_event_send_fail = 1;
    ps_isr_set(1);
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_GUARD_STOP, RT_NULL);
    (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
    ps_isr_set(0);
    s2_event_send_fail = 0;
    safety_isr_event_stats(&ns, &sf, &ls);
    CHECK("C03b-isr-send-fail-counted",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          ps_enable_write_calls(0) == low0 + 1 && sf >= 1 &&
          s2_logs_in_isr == 0 && ps_win_violations() == 0 &&
          safety_trip_pending() == RT_TRUE,
          fm("disable %d->%d LOW %d->%d send_fail=%u ISR 打印=%d 禁止项=%d"
             " pending=%s", d0, ps_sim_pwm_disable_calls(), low0,
             ps_enable_write_calls(0), sf, s2_logs_in_isr, ps_win_violations(),
             safety_trip_pending() ? "YES" : "no"));

    /* ---- C03c: 线程上下文里同一失败必须仍可查(不是静默吞 rc) ---- */
    s2_event_send_fail = 1;
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
    s2_event_send_fail = 0;
    safety_isr_event_stats(&ns, &sf, &ls);
    CHECK("C03c-thread-context-observable",
          sf >= 1 && safety_trip_pending() == RT_TRUE,
          fm("send_fail 累计=%u pending=%s (原始 rc 由返回值交回)", sf,
             safety_trip_pending() ? "YES" : "no"));
}

/* ===================================================================== */
/* C04: 真实保护窗口内请求 ISR => 只能排队, 窗口结束后交付且真的动作        */
/* ===================================================================== */
static int c04_q_delta, c04_d_delta, c04_seq_at_probe;

static void isr_soft_request(void *a)
{
    (void)a;
    c04_seq_at_probe = s2_isr_seq_n();
    (void)safety_post_event(EVT_SOFT_FAULT);      /* ISR 里的第一条故障请求 */
}

static void raise_inside_window(void)
{
    int q0, dl0;
    q0  = s2_isr_queued_n();
    dl0 = s2_isr_delivered_n();
    s2_isr_raise(isr_soft_request, RT_NULL);
    c04_q_delta = s2_isr_queued_n() - q0;
    c04_d_delta = s2_isr_delivered_n() - dl0;
}

static void cases_isr_window(void)
{
    sp_snapshot_t s;
    rt_uint32_t e0;
    int d0, dl0, live_at_raise;

    wire_and_boot();
    trip_and_reset();
    e0 = sp_epoch();
    d0 = ps_sim_pwm_disable_calls();
    dl0 = s2_isr_delivered_n();
    c04_q_delta = -9; c04_d_delta = -9; live_at_raise = -9;
    s2_set_hook(raise_inside_window, "rt_pwm_disable");
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    live_at_raise = s2_win_live_now();
    sp_get(&s);
    CHECK("C04a-queued-only-inside-window",
          c04_q_delta == 1 && c04_d_delta == 0,
          fm("窗口内: 入队 %+d 交付 %+d(必须 0, 只能排队) queued=%d delivered=%d",
             c04_q_delta, c04_d_delta, s2_isr_queued_n(), s2_isr_delivered_n()));
    CHECK("C04b-delivered-after-window-and-acted",
          s2_isr_delivered_n() == dl0 + 1 &&
          ps_sim_pwm_disable_calls() == d0 + 2 &&
          s.epoch > e0 + 1u && s.inhibit == RT_TRUE &&
          s.metadata_pending == RT_TRUE && live_at_raise == 0 &&
          s2_win_live_now() == 0 && ps_win_violations() == 0,
          fm("交付后真的动作: disable %d->%d epoch %u->%u inhibit=%s pending=%s"
             " 交付序号=%d 窗口内禁止项=%d", d0, ps_sim_pwm_disable_calls(),
             (unsigned)e0, (unsigned)s.epoch, s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", s2_isr_seq_n(), ps_win_violations()));
}

/* ===================================================================== */
/* C05 / C06: 关断矩阵与 UNKNOWN 之后的新显式停止                          */
/* ===================================================================== */
static void cases_off_matrix(void)
{
    sp_snapshot_t s;
    sp_proof_t pf;
    rt_err_t e;
    int d0, low0;

    /* C05a: disable 失败 + LOW 成功 => 两个 raw 分列, PWM 错误优先, 不第二次关断 */
    wire_and_boot();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    rt_memset(&pf, 0, sizeof(pf));
    e = sp_off_attempt_for(sp_epoch(), SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT,
                           EVT_SOFT_FAULT, 1, &pf);
    sp_get(&s);
    CHECK("C05a-disable-fail-raw-separated",
          e == -RT_EIO && ps_sim_pwm_disable_calls() == d0 + 1 &&
          ps_enable_write_calls(0) == low0 + 1 &&
          s.pwm.result == MOT_STOP_API_FAILED && s.permit.write_rc == RT_EOK &&
          pf.epoch == s.pwm.epoch && pf.epoch == s.permit.write_epoch &&
          s.inhibit == RT_TRUE && s.metadata_pending == RT_TRUE,
          fm("rc=%d disable %d->%d LOW %d->%d pwm=%s permit_rc=%d 同代 %u/%u/%u",
             (int)e, d0, ps_sim_pwm_disable_calls(), low0,
             ps_enable_write_calls(0), mot_stop_result_name(s.pwm.result),
             (int)s.permit.write_rc, (unsigned)pf.epoch, (unsigned)s.pwm.epoch,
             (unsigned)s.permit.write_epoch));

    /* C05b: 未取得设备句柄 => DEV_UNAVAILABLE/未尝试, 不碰 NULL 句柄, LOW 仍独立尝试 */
    wire_and_boot();
    /* DEV_UNAVAILABLE 的真相是"协调层没绑到句柄", 不是端口里的 dev_present:
     * 用生产自己的绑定入口临时解绑(跑完立刻绑回), 不给产品开后门。 */
    (void)sp_bind_pwm(RT_NULL, 0);
    d0 = ps_sim_pwm_disable_calls();
    low0 = ps_enable_write_calls(0);
    rt_memset(&pf, 0, sizeof(pf));
    e = sp_off_attempt_for(sp_epoch(), SP_MOTOR, MOT_STOP_SRC_INIT, FAULT_SOFT,
                           EVT_SOFT_FAULT, 0, &pf);
    sp_get(&s);
    CHECK("C05b-no-device-low-still-tried",
          e == -RT_ENOSYS && ps_sim_pwm_disable_calls() == d0 &&
          ps_sim_pwm_null_calls() == 0 && ps_enable_write_calls(0) == low0 + 1 &&
          s.pwm.result == MOT_STOP_DEV_UNAVAILABLE &&
          s.motor_stop_latched == RT_TRUE && s.motor_output_active == RT_TRUE,
          fm("rc=%d disable %d->%d NULL 句柄=%d LOW %d->%d result=%s latch=%s",
             (int)e, d0, ps_sim_pwm_disable_calls(), ps_sim_pwm_null_calls(), low0,
             ps_enable_write_calls(0), mot_stop_result_name(s.pwm.result),
             s.motor_stop_latched ? "YES" : "no"));
    (void)sp_bind_pwm((struct rt_device_pwm *)rt_device_find(EPWM_STEP_DEV_NAME), 0);

}

/* C05c/C05d 需要"成功 arm"的前提 => 单独一组, 只在 host_ovr 变体真执行 */
static void cases_off_arm(void)
{
    sp_snapshot_t s;
    rt_err_t e;

    if (!gates_open())
    {
        SKIPV("C05c", "target 变体宏全 FALSE, arm 不可达(在 host_ovr 真执行)");
        SKIPV("C05d", "target 变体宏全 FALSE, arm 不可达(在 host_ovr 真执行)");
    }
    else
    {
        /* C05c: LOW 裸写失败而焊盘碰巧 LOW => 对外绝不回 EOK(K4 §6.2) */
        wire_and_boot();          /* 上一支留下抑制/锁存, 必须先带回 READY */
        if (pre_ok("C05c arm", motor_arm() == RT_EOK))
        {
            (void)motor_set_target_hz(500);
            /* 注入: 裸写请求返回 -RT_EIO, 但焊盘本来就是 LOW —— "碰巧为 LOW"不能
             * 被消费成成功(K4 §6.2)。 */
            ps_set_enable_write_rc(-RT_EIO);
            ps_sim_set_pad_level(PIN_LOW);
            e = motor_disarm();
            sp_get(&s);
            ps_set_enable_write_rc(RT_EOK);
            CHECK("C05c-low-write-error-not-eok",
                  e != RT_EOK && s.permit.write_rc == -RT_EIO &&
                  s.metadata_pending == RT_TRUE && s.inhibit == RT_TRUE &&
                  s.permit.write_epoch == s.pwm.epoch &&
                  s.pwm.epoch == s.epoch,
                  fm("disarm rc=%d permit.write_rc=%d pending=%s inhibit=%s"
                     " 同代 pwm=%u permit=%u 当前=%u", (int)e,
                     (int)s.permit.write_rc, s.metadata_pending ? "YES" : "no",
                     s.inhibit ? "YES" : "no", (unsigned)s.pwm.epoch,
                     (unsigned)s.permit.write_epoch, (unsigned)s.epoch));
        }
        /* C05d: 正常 disarm 的 LOW 确认真实提交得进去(K4 §6.1) */
        wire_and_boot();
        if (pre_ok("C05d arm", motor_arm() == RT_EOK))
        {
            (void)motor_set_target_hz(500);
            e = motor_disarm();
            sp_get(&s);
            CHECK("C05d-disarm-low-confirm-commits-same-gen",
                  e == RT_EOK && s.permit.written == RT_TRUE &&
                  s.permit.request_level == 0u && s.permit.confirmed == RT_TRUE &&
                  s.permit.confirm_rc == RT_EOK &&
                  s.permit.confirm_epoch == s.permit.write_epoch &&
                  s.permit.write_epoch == s.epoch &&
                  s.metadata_pending == RT_FALSE && s.inhibit == RT_FALSE,
                  fm("disarm rc=%d confirmed=%s confirm=%u write=%u 当前=%u"
                     " pending=%s inhibit=%s", (int)e,
                     s.permit.confirmed ? "YES" : "no",
                     (unsigned)s.permit.confirm_epoch,
                     (unsigned)s.permit.write_epoch, (unsigned)s.epoch,
                     s.metadata_pending ? "YES" : "no",
                     s.inhibit ? "YES" : "no"));
        }
    }
}

static void cases_unknown(void)
{
    sp_snapshot_t s;
    motor_snapshot_t m;
    int d0;

    wire_and_boot();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    (void)motor_emergency_stop();
    sp_get(&s);
    if (!pre_ok("C06 unknown", s.owner == SP_HELD_UNKNOWN &&
                s.motor_stop_latched == RT_TRUE)) return;
    d0 = ps_sim_pwm_disable_calls();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)motor_emergency_stop();                 /* 新的显式停止: 必须真的再调一次 */
    sp_get(&s);
    m = snap_now();
    CHECK("C06-explicit-stop-retries-and-latch-persists",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          s.pwm.result == MOT_STOP_API_ACCEPTED &&
          s.motor_stop_latched == RT_TRUE && m.state == MOTOR_FAULT &&
          s.inhibit == RT_TRUE,
          fm("disable %d->%d 当前 result=%s 历史锁存=%s state=%d inhibit=%s",
             d0, ps_sim_pwm_disable_calls(), mot_stop_result_name(s.pwm.result),
             s.motor_stop_latched ? "YES" : "no", (int)m.state,
             s.inhibit ? "YES" : "no"));
}

/* ===================================================================== */
/* C07 / C08 / C09: 开启方向的身份与迟到动作                               */
/* ===================================================================== */
static void c07_recover_inside_command(void)
{
    sp_proof_t pf;
    rt_memset(&pf, 0, sizeof(pf));
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    fault_reset();
    (void)safety_startup_selftest();
}

static void c08_diag_grab(void)
{
    trip_and_reset();
    msh_pwm("1000");                         /* 恢复之后诊断真实起转 */
}

static void cases_identity(void)
{
    sp_snapshot_t s;
    sp_token_t oldtok, nt;
    sp_proof_t pf;
    rt_err_t e;
    int d0, high0;
    rt_uint32_t fresh, old_e;

    if (!gates_open())
    {
        SKIPV("C07", "target 变体宏全 FALSE, arm/HIGH 路径不可达");
        SKIPV("C08", "target 变体宏全 FALSE, arm/诊断不可达");
        SKIPV("C09", "target 变体宏全 FALSE, arm/disarm 不可达");
        return;
    }
    if (!pre_ok("C07 gates+diag gates", diag_gates_open())) return;

    /* ---- C07a: 抑制期间一次 HIGH 都不发; ---- */
    wire_and_boot();
    d0 = ps_sim_pwm_disable_calls();
    if (!pre_ok("C07 arm", motor_arm() == RT_EOK)) return;
    high0 = ps_enable_write_calls(1);          /* 基线 = 这次**成功** arm 之后的 HIGH 计数 */
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    e = motor_arm();
    sp_get(&s);
    CHECK("C07a-no-high-while-inhibited",
          e != RT_EOK && ps_enable_write_calls(1) == high0 &&
          s.inhibit == RT_TRUE && ps_sim_pwm_disable_calls() == d0 + 1,
          fm("arm rc=%d HIGH 写 %d->%d(不变) inhibit=%s disable=%d(只有那一次保护)",
             (int)e, high0, ps_enable_write_calls(1), s.inhibit ? "YES" : "no",
             ps_sim_pwm_disable_calls()));

    /* ---- C07b: arm 预约之前发生恢复 => 旧命令不得新使能 ---- */
    trip_and_reset();
    high0 = ps_enable_write_calls(1);
    d0 = ps_sim_pwm_disable_calls();
    s2_set_hook(c07_recover_inside_command, "integrity_check");
    e = motor_arm();
    sp_get(&s);
    CHECK("C07b-arm-refuses-post-recovery-reservation",
          e != RT_EOK && mot.armed == 0 &&
          ps_enable_write_calls(1) == high0 && pad_level == PIN_LOW &&
          s.owner != SP_MOTOR && s.inhibit == RT_FALSE,
          fm("arm rc=%d armed=%d HIGH %d->%d pad=%d owner=%s inhibit=%s epoch=%u"
             " 钩子=%s", (int)e, (int)mot.armed, high0, ps_enable_write_calls(1),
             (int)pad_level, sp_owner_name(s.owner), s.inhibit ? "YES" : "no",
             (unsigned)s.epoch, s2_hook_where()));

    /* ---- C08a: arm 的慢确认里发生"恢复 + 新诊断输出" => 旧回滚不停新诊断 ---- */
    wire_and_boot();
    s2_set_hook(c08_diag_grab, "permit_confirm_read");
    e = motor_arm();
    sp_get(&s);
    CHECK("C08a-old-rollback-cannot-cut-new-diag",
          e != RT_EOK && s.owner == SP_DIAG && s.phase == SP_RUNNING &&
          s.diag_output_active == RT_TRUE && ps_sim_pwm_running() == 1 &&
          s.inhibit == RT_FALSE,
          fm("arm rc=%d owner=%s phase=%s diag_active=%s 输出在跑=%d inhibit=%s"
             " epoch=%u", (int)e, sp_owner_name(s.owner), sp_phase_name(s.phase),
             s.diag_output_active ? "YES" : "no", ps_sim_pwm_running(),
             s.inhibit ? "YES" : "no", (unsigned)s.epoch));

    /* ---- C08b: 代次失配的确认不污染新记录 ---- */
    fresh = sp_epoch();
    e = sp_note_permit_confirmed_for(fresh + 777u, 0u, RT_EOK);
    sp_get(&s);
    CHECK("C08b-stale-confirm-rejected",
          e == -RT_EINTR && s.epoch == fresh,
          fm("rc=%d epoch 仍=%u stale_dropped=%u", (int)e, (unsigned)s.epoch,
             (unsigned)s.stale_dropped));

    /* ---- C09: 同 owner 正常释放后再预约; 旧 commit/release/确认/预约全拒 ---- */
    trip_and_reset();
    if (!pre_ok("C09 arm", motor_arm() == RT_EOK)) return;
    oldtok = mot_res;
    old_e  = oldtok.epoch;
    d0 = ps_sim_pwm_disable_calls();
    e = motor_disarm();
    sp_get(&s);
    fresh = s.epoch;
    CHECK("C09a-close-proof-permit-confirm-same-gen",
          e == RT_EOK && s.pwm.epoch == s.permit.write_epoch &&
          s.permit.write_epoch == fresh && s.permit.confirm_epoch == fresh &&
          s.permit.confirmed == RT_TRUE && s.metadata_pending == RT_FALSE &&
          s.inhibit == RT_FALSE,
          fm("disarm rc=%d pwm=%u permit=%u confirm=%u 当前=%u", (int)e,
             (unsigned)s.pwm.epoch, (unsigned)s.permit.write_epoch,
             (unsigned)s.permit.confirm_epoch, (unsigned)fresh));

    high0 = ps_sim_pwm_disable_calls();
    rt_memset(&pf, 0, sizeof(pf));
    {
        rt_err_t e1 = sp_off_attempt_for(old_e, SP_MOTOR, MOT_STOP_SRC_ARM_ROLLBACK,
                                         FAULT_SOFT, EVT_SOFT_FAULT, 1, &pf);
        rt_err_t e2 = sp_release(&oldtok);
        rt_err_t e3 = sp_note_permit_confirmed_for(old_e, 0u, RT_EOK);
        rt_err_t e4 = sp_reserve_for(old_e, SP_MOTOR, &nt);
        sp_get(&s);
        CHECK("C09b-late-actions-rejected-no-write",
              e1 == -RT_EINTR && e2 == -RT_EPERM && e3 == -RT_EINTR &&
              e4 == -RT_EINTR && ps_sim_pwm_disable_calls() == high0 &&
              s.epoch == fresh,
              fm("late off=%d release=%d confirm=%d reserve_for=%d disable %d->%d"
                 " epoch=%u", (int)e1, (int)e2, (int)e3, (int)e4, high0,
                 ps_sim_pwm_disable_calls(), (unsigned)s.epoch));
    }
    (void)sp_reserve(SP_MOTOR, &nt);
    CHECK("C09c-re-reserve-new-identity",
          nt.epoch != old_e && nt.epoch > fresh,
          fm("新 epoch=%u(旧=%u 释放后=%u)", (unsigned)nt.epoch,
             (unsigned)old_e, (unsigned)fresh));
    (void)sp_release(&nt);
    (void)d0;
}

/* ===================================================================== */
/* C11: 诊断 grant/prepare/commit/enable 失败出口与竞争                    */
/* ===================================================================== */
static void cases_diag(void)
{
    sp_snapshot_t s;
    int d0, en0;

    if (!diag_gates_open()) { SKIPV("C11", "target 变体无 STEP 输出许可"); return; }
    wire_and_boot();
    if (!pre_ok("C11 no motor arm needed", 1)) return;

    /* (a) prepare 失败: 预约归还, 不 enable, 不半截持有 */
    en0 = ps_sim_pwm_enable_calls();
    ps_sim_pwm_set_rc(-RT_EIO, RT_EOK, RT_EOK);
    msh_pwm("1000");
    sp_get(&s);
    CHECK("C11a-prepare-fail-releases-no-enable",
          s.owner == SP_NONE && ps_sim_pwm_enable_calls() == en0 &&
          step_pwm_output_active() == RT_FALSE && s.diag_output_active == RT_FALSE,
          fm("owner=%s enable %d->%d output_active=%s", sp_owner_name(s.owner),
             en0, ps_sim_pwm_enable_calls(),
             step_pwm_output_active() ? "YES" : "no"));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);

    /* (b) 诊断持有期间 Motor 被挡 */
    msh_pwm("1000");
    if (!pre_ok("C11b diag running", ps_sim_pwm_running() &&
                step_pwm_output_active())) return;
    {
        rt_err_t me = gates_open() ? motor_arm() : -RT_EPERM;
        CHECK("C11b-motor-blocked-while-diag-owns",
              me != RT_EOK && step_pwm_output_active() == RT_TRUE,
              fm("arm rc=%d diag output_active=%s", (int)me,
                 step_pwm_output_active() ? "YES" : "no"));
    }
    /* (c) 正常交回 */
    d0 = ps_sim_pwm_disable_calls();
    msh_pwm("stop");
    sp_get(&s);
    CHECK("C11c-diag-stop-releases",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          step_pwm_output_active() == RT_FALSE && s.owner == SP_NONE &&
          step_pwm_off_confirmed() == RT_TRUE &&
          s.diag_pwm.result == MOT_STOP_API_ACCEPTED,
          fm("disable %d->%d owner=%s off_confirmed=%s diag_pwm=%s",
             d0, ps_sim_pwm_disable_calls(), sp_owner_name(s.owner),
             step_pwm_off_confirmed() ? "YES" : "no",
             mot_stop_result_name(s.diag_pwm.result)));

    /* (d) 关断失败: 所有权不释放 + 恰好一次 disable + Motor 与新借用都被挡 */
    msh_pwm("1000");
    d0 = ps_sim_pwm_disable_calls();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    msh_pwm("stop");
    sp_get(&s);
    /* CP5 §6: 除了"一次关断 + 锁存/抑制/pending", 还必须对 **canonical 的 owner 与
     * 输出未交回** 下断言 —— 否则"失败分支提前把 owner 交回 SP_NONE"这一类缺陷
     * 在这里不可见(此前 S1 的"失败提前交回 owner"只有 REV-POINTED, 没有真注入)。 */
    CHECK("C11d-diag-fail-keeps-owner-single-close",
          ps_sim_pwm_disable_calls() == d0 + 1 &&
          step_pwm_output_active() == RT_TRUE && s.diag_stop_latched == RT_TRUE &&
          s.inhibit == RT_TRUE && s.metadata_pending == RT_TRUE &&
          s.owner == SP_HELD_UNKNOWN && sp_diag_output_active() == RT_TRUE &&
          sp_diag_off_confirmed() == RT_FALSE,
          fm("disable %d->%d active=%s latch=%s inhibit=%s pending=%s canonical_owner=%s "
             "diag_output_active=%s diag_off_confirmed=%s",
             d0, ps_sim_pwm_disable_calls(), step_pwm_output_active() ? "YES" : "no",
             s.diag_stop_latched ? "YES" : "no", s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", sp_owner_name(s.owner),
             sp_diag_output_active() ? "YES" : "no",
             sp_diag_off_confirmed() ? "YES" : "no"));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
}

/* ===================================================================== */
/* C12 / C13 / C14: 人工恢复的换代、票据与两线程整体提交                     */
/* ===================================================================== */
static void c12_new_fault_during_reset(void)
{
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
}

/* ---- CP5 §4: C14 用的确定接缝(条件量 + 已在的钩子点), 不再用睡眠猜交错 ----
 * 停车点在 fault_reset 里"带票据复核已完成、最终提交之前"的那次
 * safety_drv_permit_confirm_low(), 它正是夹具已有的 permit_confirm_read 钩子点,
 * 所以不需要为测试在产品里新插桩。 */
static pthread_mutex_t c14_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  c14_c = PTHREAD_COND_INITIALIZER;
static int  c14_stage;        /* 0 初始 / 1 A 停在最终提交前 / 2 放行 / 3 线程已退出 */
static int  c14_parked;       /* 接缝确实被走到(不是空跑) */

static void c14_park_here(void)
{
    pthread_mutex_lock(&c14_m);
    c14_parked++;
    c14_stage = 1;
    pthread_cond_broadcast(&c14_c);
    while (c14_stage < 2) pthread_cond_wait(&c14_c, &c14_m);
    pthread_mutex_unlock(&c14_m);
}

static void c14_wait_stage(int want)
{
    pthread_mutex_lock(&c14_m);
    while (c14_stage < want) pthread_cond_wait(&c14_c, &c14_m);
    pthread_mutex_unlock(&c14_m);
}

static void c14_release_a(void)
{
    pthread_mutex_lock(&c14_m);
    c14_stage = 2;
    pthread_cond_broadcast(&c14_c);
    pthread_mutex_unlock(&c14_m);
}

static void c14_reset_thread(void *a)
{
    int *rc = (int *)a;

    fault_reset();
    *rc = 1;
    pthread_mutex_lock(&c14_m);
    if (c14_stage < 3) c14_stage = 3;
    pthread_cond_broadcast(&c14_c);
    pthread_mutex_unlock(&c14_m);
}

static void latch_then_prepare(int low_fail)
{
    wire_and_boot();
    if (low_fail) ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL);
    if (low_fail) ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    (void)pre_ok("prepared latch", safety_state_get() == SAFETY_FAULT_LATCHED);
}

static void cases_recovery(void)
{
    sp_snapshot_t s;
    rt_uint32_t e0;
    rt_uint32_t new_ep = 0u;
    safety_state_t mid_ss = SAFETY_BOOT;
    int d0, r0 = 0, r1 = 0;
    int cont0 = 0, b_waited = 0, ok_b = 0, mid_inh = 0, new_pend = 0;

    /* C12: 恢复复核之后落下新故障 => 换代 + 保持锁存, 旧票据不清新故障 */
    latch_then_prepare(0);
    e0 = sp_epoch();
    s2_set_hook(c12_new_fault_during_reset, "permit_confirm_read");
    fault_reset();
    sp_get(&s);
    CHECK("C12-new-fault-during-reset-keeps-latch",
          s.epoch > e0 && s.inhibit == RT_TRUE &&
          ss_state != SAFETY_MANUAL_CLEAR,
          fm("epoch %u->%u inhibit=%s pending=%s ss=%s first_code=%u",
             (unsigned)e0, (unsigned)s.epoch, s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", safety_state_name(ss_state),
             (unsigned)s.first_fault_code));

    /* C12b: 恢复复核中 LOW 裸写单独失败 => 换代并保持抑制(票据失效) */
    latch_then_prepare(0);
    e0 = sp_epoch();
    ps_set_enable_write_rc(-RT_EIO);
    ps_sim_set_pad_level(PIN_LOW);   /* 只让裸写失败; 电平/回读仍是 LOW 的事实 */
    fault_reset();
    ps_set_enable_write_rc(RT_EOK);
    sp_get(&s);
    CHECK("C12b-low-only-fail-advances",
          s.epoch != e0 && s.inhibit == RT_TRUE &&
          ss_state != SAFETY_MANUAL_CLEAR && s.permit.write_rc == -RT_EIO,
          fm("epoch %u->%u inhibit=%s pending=%s ss=%s permit.write_rc=%d",
             (unsigned)e0, (unsigned)s.epoch, s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", safety_state_name(ss_state),
             (int)s.permit.write_rc));

    /* C13: 历史锁存早于票据 => 真实复核可以清; 不永久卡死, 不自动开启 */
    latch_then_prepare(1);
    d0 = ps_sim_pwm_disable_calls();
    sp_get(&s);
    if (pre_ok("C13 latched first", s.motor_stop_latched == RT_TRUE))
    {
        fault_reset();
        sp_get(&s);
        CHECK("C13-latched-cleared-by-real-recheck",
              s.motor_stop_latched == RT_FALSE && s.inhibit == RT_FALSE &&
              ss_state == SAFETY_MANUAL_CLEAR &&
              ps_sim_pwm_disable_calls() == d0 + 1 &&
              mot.state == MOTOR_IDLE && mot.armed == 0 && pad_level == PIN_LOW,
              fm("latch=%s inhibit=%s ss=%s disable %d->%d state=%d armed=%d pad=%d",
                 s.motor_stop_latched ? "YES" : "no", s.inhibit ? "YES" : "no",
                 safety_state_name(ss_state), d0, ps_sim_pwm_disable_calls(),
                 (int)mot.state, (int)mot.armed, (int)pad_level));
    }

    /* ---- C14a: A 停在"复核已完成、最终提交前", 新故障先落地, 再放行 A ----
     * 原来的 usleep(3000) 只能"看起来"让两条线程进入复核, 并不能证明新请求落在
     * 最终提交之前还是之后(任务书 §4)。现在用条件量把先后钉死。 */
    latch_then_prepare(0);
    pthread_mutex_lock(&c14_m);
    c14_stage = 0;
    pthread_mutex_unlock(&c14_m);
    r0 = r1 = 0;
    s2_set_hook(c14_park_here, "permit_confirm_read");
    s2_spawn(c14_reset_thread, &r0);
    c14_wait_stage(1);                        /* A 真的停在最终提交之前 */
    /* ⚠ 这里**不能**调 safety_reconcile_trip(): A 停在接缝里仍持 ss_lock, 慢整理会
     * 排在同一把锁上 => 主线程等 A、A 等主线程放行(本机实测被 60s 墙钟守卫抓到)。
     * "新故障先落地"只需要短保护(不取任何锁), 它的可观察结果此刻已经成立;
     * 慢整理放到 A 被拒并 join 之后做, 那时没人持锁。 */
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_GUARD_STOP, RT_NULL);
    sp_get(&s);
    new_ep = s.epoch;
    new_pend = (s.metadata_pending == RT_TRUE) ? 1 : 0;   /* 放行前就已成立 */
    c14_release_a();                          /* 才放行 A 的最终窗口 */
    s2_join_all();
    /* 这里**不**再 ack: 新故障留下的 pending 必须还是断言要看的那一条事实。 */
    sp_get(&s);
    CHECK("C14a-stale-ticket-refused-at-final-commit",
          r0 == 1 && c14_parked == 1 && new_pend == 1 && s.epoch == new_ep &&
          s.inhibit == RT_TRUE && ss_state != SAFETY_MANUAL_CLEAR &&
          s.metadata_pending == RT_TRUE,
          fm("A 完成=%d 停在接缝=%d epoch=%u(新故障=%u) inhibit=%s ss=%s pending=%s"
             " - 旧票据不得在新故障那一代上清锁存", r0, c14_parked,
             (unsigned)s.epoch, (unsigned)new_ep, s.inhibit ? "YES" : "no",
             safety_state_name(ss_state), s.metadata_pending ? "YES" : "no"));

    /* ---- C14b: A 的最终窗口先成功, B 真的在 ss_lock 门外等, 新故障随后保持 ---- */
    latch_then_prepare(0);
    pthread_mutex_lock(&c14_m);
    c14_stage = 0;
    pthread_mutex_unlock(&c14_m);
    r0 = r1 = 0;
    c14_parked = 0;
    s2_set_hook(c14_park_here, "permit_confirm_read");
    s2_spawn(c14_reset_thread, &r0);
    c14_wait_stage(1);
    cont0 = s2_contend_hits;
    s2_spawn(c14_reset_thread, &r1);          /* B: 同一把 ss_lock 的真实竞争者 */
    {
        int spins = 0;

        while (s2_contend_hits == cont0 && spins++ < 2500) usleep(200);
        b_waited = (s2_contend_hits > cont0) ? 1 : 0;
    }
    c14_release_a();                            /* 恢复最终窗口先成功 */
    c14_wait_stage(3);
    s2_join_all();
    sp_get(&s);
    mid_ss = ss_state;
    mid_inh = (s.inhibit == RT_TRUE) ? 1 : 0;
    ok_b = (r0 == 1 && r1 == 1 && c14_parked == 1 && b_waited == 1 &&
            mid_ss == SAFETY_MANUAL_CLEAR && mid_inh == 0);
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_GUARD_STOP, RT_NULL);
    sp_get(&s);                               /* 不 ack: 要看的就是这条保持住的义务 */
    CHECK("C14b-new-fault-after-commit-kept",
          ok_b && s.inhibit == RT_TRUE &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&   /* canonical 视图: 抑制即锁存 */
          s.metadata_pending == RT_TRUE,
          fm("A=%d B=%d B 真在门外等=%d 接缝=%d 提交后 ss=%s inhibit=%d ->"
             " 随后新故障: ss=%s inhibit=%s pending=%s epoch=%u", r0, r1, b_waited,
             c14_parked, safety_state_name(mid_ss), mid_inh,
             safety_state_name(ss_state), s.inhibit ? "YES" : "no",
             s.metadata_pending ? "YES" : "no", (unsigned)s.epoch));
}

/* ===================================================================== */
/* C15: pending 空分支 / 迟到身份 / 旧自检                                  */
/* ===================================================================== */
/* C15c 的对手线程: 自检走到自己的慢步骤(ENN 有界确认读)时被唤醒, 在**另一个线程**上
 * 完成"停机 + 人工恢复 + 新一轮 arm/start"。自检与它之间只有真实pthread 锁与协调层
 * 状态相交, 不是同线程重入(§9 明令禁止的形状)。 */
static volatile int c15_go, c15_done;
static int          c15_mid_ss;

static void c15_rival(void *arg)
{
    (void)arg;
    while (!c15_go) usleep(200);
    trip_and_reset();                      /* 停机 + 恢复 + 自检回 READY */
    (void)motor_arm();
    (void)motor_set_target_hz(500);        /* start 前先给目标, 否则"target_hz=0"被拒 */
    (void)motor_start();                   /* 新运行: SS=RUN, owner=MOTOR, 又换一代 */
    c15_mid_ss = (int)ss_state;
    c15_done = 1;
}

static void c15_new_cycle(void)
{
    c15_go = 1;
    while (!c15_done) usleep(200);         /* 只等对线程做完, 不在这里改任何状态 */
}

static void cases_stale(void)
{
    sp_snapshot_t s;
    safety_state_t ss_before;
    int rc0, st0, bb0;

    wire_and_boot();

    /* (a) 空 pending 的重复通知什么都不写(K1 删除的旁路) */
    trip_and_reset();                          /* 现在 SS=READY, 无 pending */
    ss_before = ss_state;
    rc0 = (int)ss_fault_code;
    st0 = (int)mot.state;
    bb0 = ps_bb_triggers();
    safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    CHECK("C15a-empty-pending-writes-nothing",
          ss_state == ss_before && (int)ss_fault_code == rc0 &&
          (int)mot.state == st0 && ps_bb_triggers() == bb0,
          fm("ss=%s code=%d state=%d blackbox %d->%d (全部不变)",
             safety_state_name(ss_state), (int)ss_fault_code, (int)mot.state,
             bb0, ps_bb_triggers()));

    if (!gates_open())
    {
        SKIPV("C15b", "target 变体宏全 FALSE, 旧身份/新 arm 不可达");
        SKIPV("C15c", "target 变体宏全 FALSE, 诊断新预约不可达");
        return;
    }
    /* (b) 旧身份的一次关断被拒, 一个寄存器写都不发 */
    wire_and_boot();
    if (pre_ok("C15b arm", motor_arm() == RT_EOK))
    {
        sp_token_t stale = mot_res;
        (void)motor_set_target_hz(1000);
        trip_and_reset();
        if (pre_ok("C15b new arm", motor_arm() == RT_EOK))
        {
            int d0 = ps_sim_pwm_disable_calls();
            rt_err_t e = sp_off_attempt_for(stale.epoch, SP_MOTOR,
                                            MOT_STOP_SRC_FAIL_CLOSED, FAULT_SOFT,
                                            EVT_SOFT_FAULT, 0, RT_NULL);
            sp_get(&s);
            CHECK("C15b-old-identity-rejected-no-write",
                  e == -RT_EINTR && ps_sim_pwm_disable_calls() == d0 &&
                  s.inhibit == RT_FALSE && ss_state == SAFETY_READY,
                  fm("rc=%d disable %d->%d inhibit=%s ss=%s", (int)e, d0,
                     ps_sim_pwm_disable_calls(), s.inhibit ? "YES" : "no",
                     safety_state_name(ss_state)));
        }
    }
    /* (c) 旧自检在换代后不得写 READY */
    wire_and_boot();
    if (pre_ok("C15c to SELF_TEST", safety_transition(SAFETY_SELF_TEST) == RT_EOK))
    {
        rt_err_t e;
        int fb0 = ps_fb_confirm_calls();

        c15_go = 0; c15_done = 0; c15_mid_ss = -1;
        s2_spawn(c15_rival, RT_NULL);
        s2_set_hook(c15_new_cycle, "drv_feedback_confirm");
        e = safety_startup_selftest();
        s2_join_all();
        sp_get(&s);
        /* 前置: 换代必须由对线程在自检的慢步骤里真实完成, 且它做完时 SS 已经不是
         * READY —— 否则"旧自检验不回 READY"这句话没有可观测的落点。 */
        if (!pre_ok("C15c seam+rival", ps_fb_confirm_calls() > fb0 &&
                    s2_hook_fires_count() >= 1 && c15_done == 1 &&
                    c15_mid_ss >= 0 && c15_mid_ss != (int)SAFETY_READY)) return;
        CHECK("C15c-stale-selftest-cannot-write-ready",
              e != RT_EOK && (int)ss_state == c15_mid_ss,
              fm("旧自检 rc=%d ss=%s(新运行后应为 %s) owner=%s epoch=%u"
                 " 接缝=%s 反馈读 %d->%d", (int)e, safety_state_name(ss_state),
                 safety_state_name((safety_state_t)c15_mid_ss),
                 sp_owner_name(s.owner), (unsigned)s.epoch,
                 s2_hook_where(), fb0, ps_fb_confirm_calls()));
    }
}

/* ===================================================================== */
/* C19: 计数边界                                                          */
/* ===================================================================== */
static void cases_boundary(void)
{
    sp_token_t tok;
    sp_proof_t pf;
    sp_snapshot_t s;
    rt_err_t e, off, rel;
    rt_uint32_t ticket;
    rt_bool_t rec;
    int d0, adv0;

    wire_and_boot();
    sp.epoch = SP_EPOCH_MAX - 2u;              /* 边界种子: 唯一用途是接近 UINT32_MAX */
    e = sp_reserve(SP_MOTOR, &tok);
    sp_get(&s);
    adv0 = (int)s.advance_failed;
    d0 = ps_sim_pwm_disable_calls();
    rt_memset(&pf, 0, sizeof(pf));
    off = sp_off_attempt_for(tok.epoch, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT,
                             EVT_SOFT_FAULT, 1, &pf);
    sp_get(&s);
    rel = sp_release(&tok);
    rec = sp_recovery_start(&ticket);
    sp_get(&s);
    CHECK("C19a-boundary-non-eok-keeps-inhibit",
          (e == RT_EOK || e == -RT_EPERM) && off != RT_EOK && rel != RT_EOK &&
          rec == RT_FALSE && s.inhibit == RT_TRUE && s.exhausted == RT_TRUE &&
          s.advance_failed > (rt_uint32_t)adv0 &&
          pf.epoch == s.pwm.epoch && pf.epoch == s.permit.write_epoch &&
          ps_sim_pwm_disable_calls() == d0 + 1,
          fm("reserve=%d off=%d release=%d recovery=%d epoch=%u exhausted=%s"
             " advance_failed %d->%u proof/pwm/permit=%u/%u/%u disable %d->%d",
             (int)e, (int)off, (int)rel, (int)rec, (unsigned)s.epoch,
             s.exhausted ? "YES" : "no", adv0, (unsigned)s.advance_failed,
             (unsigned)pf.epoch, (unsigned)s.pwm.epoch,
             (unsigned)s.permit.write_epoch, d0, ps_sim_pwm_disable_calls()));

    d0 = ps_sim_pwm_disable_calls();
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    sp_get(&s);
    CHECK("C19b-explicit-stop-still-closes-at-boundary",
          ps_sim_pwm_disable_calls() == d0 + 1 && s.trips > 0 &&
          s.exhausted == RT_TRUE && s.inhibit == RT_TRUE &&
          safety_state_get() == SAFETY_FAULT_LATCHED,
          fm("disable %d->%d trips=%u exhausted=%s inhibit=%s ss=%s",
             d0, ps_sim_pwm_disable_calls(), (unsigned)s.trips,
             s.exhausted ? "YES" : "no", s.inhibit ? "YES" : "no",
             safety_state_name(safety_state_get())));
}

/* ===================================================================== */
/* C20: 正常全周期 + 分 owner 视图一致                                     */
/* ===================================================================== */
static void ramp_until_zero(int n)
{
    int i;
    for (i = 0; i < n; ++i) motor_ramp_tick();
}

static void cases_healthy(void)
{
    sp_snapshot_t s;
    motor_snapshot_t m;
    mot_stop_result_t dres;
    rt_err_t drc;
    rt_bool_t dlat;

    if (!gates_open()) { SKIPV("C20", "target 变体宏全 FALSE, 正常开启周期不可达");
                         SKIPV("C20d", "target 变体宏全 FALSE"); return; }
    wire_and_boot();
    if (!pre_ok("C20 arm", motor_arm() == RT_EOK)) return;
    if (!pre_ok("C20 target", motor_set_target_hz(1000) == RT_EOK)) return;
    if (!pre_ok("C20 start", motor_start() == RT_EOK)) return;
    ramp_until_zero(6);
    m = snap_now();
    sp_get(&s);
    if (!pre_ok("C20 running", s.phase == SP_RUNNING && m.current_hz > 0))
    {
        CHECK("C20a-healthy-run", 0, fm("phase=%s hz=%u", sp_phase_name(s.phase),
                                        (unsigned)m.current_hz));
        return;
    }
    CHECK("C20a-healthy-run",
          s.phase == SP_RUNNING && m.armed == 1 && s.owner == SP_MOTOR &&
          s.permit.request_level == 1u && s.permit.confirmed == RT_TRUE &&
          s.permit.enn_ok == RT_TRUE && pad_level == PIN_HIGH &&
          s.metadata_pending == RT_FALSE,
          fm("phase=%s armed=%d owner=%s permit req=%u confirmed=%s enn=%s pad=%d",
             sp_phase_name(s.phase), (int)m.armed, sp_owner_name(s.owner),
             (unsigned)s.permit.request_level, s.permit.confirmed ? "YES" : "no",
             s.permit.enn_ok ? "YES" : "no", (int)pad_level));

    /* 受控减速到 0: 归零那一拍就把 IDLE + RUN->READY 一起提交(K2 §4.3) */
    (void)motor_stop();
    ramp_until_zero(80);
    m = snap_now();
    sp_get(&s);
    CHECK("C20b-ramp-zero-ready-keeps-armed",
          m.current_hz == 0 && m.state == MOTOR_IDLE &&
          safety_state_get() == SAFETY_READY && m.armed == 1 &&
          s.phase == SP_ARMED && s.inhibit == RT_FALSE,
          fm("hz=%u state=%d ss=%s armed=%d phase=%s inhibit=%s",
             (unsigned)m.current_hz, (int)m.state,
             safety_state_name(safety_state_get()), (int)m.armed,
             sp_phase_name(s.phase), s.inhibit ? "YES" : "no"));

    if (pre_ok("C20 re-target", motor_set_target_hz(900) == RT_EOK) &&
        pre_ok("C20 restart", motor_start() == RT_EOK))
    {
        motor_ramp_tick();
        CHECK("C20c-restart-after-ready",
              sp_get_phase() == SP_RUNNING && mot.state == MOTOR_ACCEL,
              fm("phase=%s state=%d", sp_phase_name(sp_get_phase()),
                 (int)mot.state));
        (void)motor_stop();
        ramp_until_zero(80);
    }
    if (pre_ok("C20 disarm", motor_disarm() == RT_EOK))
        CHECK("C20d-disarm-then-rearm", motor_arm() == RT_EOK && mot.armed == 1,
              fm("rearm rc ok armed=%d", (int)mot.armed));

    /* 诊断一轮之后再让 Motor 正常运行: 各 owner 视图一致(K6.1) */
    if (!diag_gates_open()) { SKIPV("C20e", "本变体无 STEP 输出许可"); return; }
    (void)motor_disarm();
    trip_and_reset();
    msh_pwm("1000");
    msh_pwm("stop");
    if (!pre_ok("C20 arm after diag", motor_arm() == RT_EOK)) return;
    (void)motor_set_target_hz(800);
    if (!pre_ok("C20 start after diag", motor_start() == RT_EOK)) return;
    motor_ramp_tick();
    step_pwm_get_stop_state(&dres, &drc, &dlat);
    sp_get(&s);
    CHECK("C20e-owner-views-consistent",
          s.phase == SP_RUNNING && s.owner == SP_MOTOR &&
          s.diag_output_active == RT_FALSE && dres == MOT_STOP_API_ACCEPTED &&
          dlat == RT_FALSE && step_pwm_off_confirmed() == RT_TRUE &&
          sp_diag_off_confirmed() == RT_TRUE &&
          s.diag_pwm.result == MOT_STOP_API_ACCEPTED,
          fm("motor phase=%s owner=%s | diag active=%s result=%s latch=%s"
             " off_confirmed=%s diag_pwm=%s", sp_phase_name(s.phase),
             sp_owner_name(s.owner), s.diag_output_active ? "YES" : "no",
             mot_stop_result_name(dres), dlat ? "YES" : "no",
             step_pwm_off_confirmed() ? "YES" : "no",
             mot_stop_result_name(s.diag_pwm.result)));
    (void)motor_stop();
    ramp_until_zero(80);
    (void)motor_disarm();
}

/* 目标宏全 FALSE: 开启方向必须被拒, 关闭方向始终可用 */
static void cases_target_reject(void)
{
    sp_snapshot_t s;
    rt_err_t e;
    int d0;

    if (gates_open() || diag_gates_open())
    {
        SKIPV("C21", "host_ovr 变体不跑目标拒绝");
        return;
    }
    wire_and_boot();
    d0 = ps_sim_pwm_disable_calls();
    e = motor_arm();
    sp_get(&s);
    CHECK("C21a-target-false-refuses-open",
          e != RT_EOK && mot.armed == 0 && ps_enable_write_calls(1) == 0 &&
          ps_sim_pwm_enable_calls() == 0 && s.owner == SP_NONE,
          fm("arm rc=%d armed=%d HIGH 写=%d enable 调用=%d owner=%s",
             (int)e, (int)mot.armed, ps_enable_write_calls(1),
             ps_sim_pwm_enable_calls(), sp_owner_name(s.owner)));
    (void)motor_disarm();
    sp_get(&s);
    CHECK("C21b-close-always-available",
          ps_sim_pwm_disable_calls() >= d0 + 1 &&
          ps_sim_pwm_null_calls() == 0 && s.pwm.attempted == RT_TRUE,
          fm("disable %d->%d NULL 句柄=%d pwm.attempted=%s", d0,
             ps_sim_pwm_disable_calls(), ps_sim_pwm_null_calls(),
             s.pwm.attempted ? "YES" : "no"));
}

/* ===================================================================== */
/* main: 一个进程跑完全部适用 ID(与既有四链同一台账纪律)                    */
/* ===================================================================== */
struct s2_grp { const char *name; void (*fn)(void); };
static const struct s2_grp grps[] = {
    { "lock", cases_lock },           { "isr", cases_isr },
    { "isr_window", cases_isr_window }, { "off_matrix", cases_off_matrix },
    { "off_arm", cases_off_arm },     { "unknown", cases_unknown },
    { "identity", cases_identity },
    { "diag", cases_diag },            { "recovery", cases_recovery },
    { "stale", cases_stale },          { "boundary", cases_boundary },
    { "healthy", cases_healthy },      { "target_reject", cases_target_reject },
};

/* 每组**一个独立进程**: 生产静态量(sp 的 exhausted/inhibit、SS 状态、历史锁存)在同一
 * 进程内存续, C19 的边界种子会把之后的用例全堵住 —— 分组跑既是隔离也是台账要求。 */
int main(int argc, char **argv)
{
    size_t i, hit = (size_t)-1;

    if (argc != 2) { printf("USAGE|sp_conc <group>\n"); return 2; }
    for (i = 0; i < sizeof(grps)/sizeof(grps[0]); ++i)
        if (strcmp(grps[i].name, argv[1]) == 0) hit = i;
    if (hit == (size_t)-1) { printf("UNKNOWN_GROUP|%s\n", argv[1]); return 2; }
    printf("VARIANT|group=%s|gates=%d|diag_gates=%d\n", argv[1],
           gates_open(), diag_gates_open());
    grps[hit].fn();
    printf("SUMMARY|cases=%d|pass=%d|fail=%d|pre_bad=%d|skips=%d\n",
           g_run, g_pass, g_fail, g_pre, g_skip);
    fflush(stdout);
    return g_fail ? 1 : 0;
}
'''

t = t.rstrip("\n") + "\n" + body
io.open(OUT, "w", encoding="utf-8", newline="\r\n").write(t)
print("sp_conc_main.c complete, lines=", t.count("\n") + 1)
