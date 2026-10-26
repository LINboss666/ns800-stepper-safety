/*
 * sp_measure_a0_main.c - 目标机测量 A0 的主机验证链（任务 phase8-target-measurement-a0）
 *
 * 被测对象是**真编译**的两份生产实现：
 *   APP/stop_priority.c   —— 三个入口的插桩形状、动作次数、返回优先序
 *   APP/sp_measure_a0.c   —— 预约、窗口内逐字段钉快照、有效性判定、发布、控制状态机与命令族
 * 本夹具只补三样东西：PRIMASK/线程模型、脚本化 PPB 寄存器模型、总线动作计数。
 * 记录器与判定逻辑**不在这里重写**（任务书 §8）。
 *
 * 模型与目标事实的同形关系：
 *   - 窗口 = 跨线程真独占：一把全局互斥量 + **线程本地**深度/掩码（S2 N7 教训：进程级全局
 *     掩码位会让别的线程看到别人的窗口，导致上下文门静默误拒）。
 *   - 可屏蔽中断只能在窗口结束后交付（排队 -> 最外层 enable 时同步交付，交付期间
 *     rt_interrupt_get_nest() 非 0）。
 *   - CYCCNT 是脚本化递增值；**绝不**用主机墙钟冒充周期计数。SysTick->CTRL 的
 *     "读即清 COUNTFLAG" 副作用如实建模。
 *   - GPIO/PWM 的返回码与**真实调用次数**独立计数：记录里的 pwm_called/low_called 只是声明，
 *     必须由这些计数交叉核对（记录标志不能替代真实调用证据）。
 *
 * r1（复审 A0-01..04）新增 TMA19..TMA24：真实命令名查表分发、严格参数矩阵、逐样本门控、
 *   能力检查生命周期、历史旗正向对照、修订后的分批顺序。
 * 两个可执行体由同一 runner 构建（同一份本文件）：
 *   a0_off : -DNS800_TARGET_MEASUREMENT_A0=0  => 只跑 TMA01
 *   a0_on  : -DNS800_TARGET_MEASUREMENT_A0=1 -DNS800_SPM_HOST_BACKEND => 跑 TMA02..TMA24
 * 生产侧的 rt_kprintf 展开成 printf，本链用 `-Wl,--wrap=printf` 数出**生产打印**（夹具自己的
 * 台账走 emit()/fprintf，不经这条通道），因此"窗口内不许打印"是可判定的而不是口头承诺。
 *
 * ⚠ TMA13 的"嵌套扰动外层判据"用注入实现：目标上只有不可屏蔽源(NMI/HardFault 类)能造成这种
 *   交错，而记录器的同步合同**不覆盖**它们（计划 §2.3/M-1 边界）。该用例断言的是标注行为：
 *   被扰动必须标 invalid，绝不静默报有效。
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdint.h>
#include <pthread.h>
#include <errno.h>
#include <time.h>

#include <rtthread.h>
#include <rtdevice.h>
#include "stop_priority.h"
#include "stop_result.h"
#include "safety_state.h"
#include "sp_measure_a0.h"
#include "sp_measure_a0_port.h"
/* r1 / 复审 A0-01：命令必须**按名字查表**分发，夹具不得直接调用 spm_cmd()。
 * 本 TU 拥有 FSymTab 等价表(实现体)，REC 那份只声明 extern，两边看的是同一张表。 */
#define SPM_FINSH_HOST_IMPL
#include "sp_measure_a0_finsh.h"

/* ================= A0 控制台长度模型（BOARD-A-01 的可判定化） =================
 * 同形关系与边界逐条写在 tests/host/sp_measure_a0_console.h 的文件头注释里。
 * 生产侧的 rt_kprintf / rt_vsnprintf / rt_kputs 三个名字被那一份头映射到下面这三个实现，
 * 于是"128 字节控制台缓冲剪断长行"第一次成为主机可判定事实；断言只看**逐字节捕获缓冲**，
 * 不看 stdout（stdout 只用来让人看见内容并维持 -Wl,--wrap=printf 的打印计数）。
 */
#define A0_CONSOLEBUF 128                 /* == rtconfig.h:107 RT_CONSOLEBUF_SIZE */
static char a0_log_buf[A0_CONSOLEBUF];    /* == kservice.c:391 static char rt_log_buf[] */
static char a0_cap_buf[32768];
static int  a0_cap_on;
static int  a0_cap_bytes;
static int  a0_cap_trunc;                 /* 被 127 上限剪断过的行数 */
static int  a0_cap_nul;                   /* 实际上线字节里的 NUL 个数 */

int a0_rt_vsnprintf(char *buf, rt_size_t size, const char *fmt, va_list ap)
{
    char big[4096];
    va_list ap2;
    int n, c;

    big[0] = 0;
    va_copy(ap2, ap);
    n = vsnprintf(big, sizeof(big), fmt, ap2);
    va_end(ap2);
    if (n < 0 || n >= (int) sizeof(big))
    {
        n = (int) strlen(big);            /* msvcrt 截断时返回 -1, 这里取真实应写长度 */
    }
    c = (size > 0u) ? ((n < (int) size - 1) ? n : (int) size - 1) : 0;
    memcpy(buf, big, (size_t) c);
    buf[c] = 0;
    return n;                             /* 与 rt_vsnprintf_tiny 一致: 应写总字节数, 不含 NUL */
}

static void a0_console_emit(const char *s, int n)
{
    int i;

    if (a0_cap_on)
    {
        for (i = 0; i < n; i++)
        {
            if (a0_cap_bytes < (int) sizeof(a0_cap_buf) - 1)
            {
                a0_cap_buf[a0_cap_bytes++] = s[i];
            }
            if (s[i] == 0)
            {
                a0_cap_nul++;
            }
        }
    }
    (void) printf("%.*s", n, s);          /* 经 --wrap=printf: 生产打印计数与窗口内禁止项台账 */
    fflush(stdout);
}

int a0_kprintf(const char *fmt, ...)
{
    va_list ap;
    int length;

    va_start(ap, fmt);
    length = a0_rt_vsnprintf(a0_log_buf, A0_CONSOLEBUF - 1, fmt, ap);
    va_end(ap);
    if (length > A0_CONSOLEBUF - 1)
    {
        a0_cap_trunc++;                   /* 与 kservice.c:406-409 同一道钳位 */
        length = A0_CONSOLEBUF - 1;
    }
    a0_console_emit(a0_log_buf, length);
    return length;
}

void a0_kputs(const char *s)
{
    if (s == RT_NULL)
    {
        return;                           /* kservice.c:372-375 */
    }
    a0_console_emit(s, (int) strlen(s));  /* kservice.c:376-380 _kputs(str, rt_strlen(str)) */
}

void a0_console_capture(int on) { a0_cap_on = on; }

void a0_console_reset(void)
{
    a0_cap_bytes = 0;
    a0_cap_trunc = 0;
    a0_cap_nul = 0;
    a0_cap_buf[0] = 0;
}

const char *a0_console_bytes(int *len, int *trunc, int *nul)
{
    int last = a0_cap_bytes;

    if (last >= (int) sizeof(a0_cap_buf))
    {
        last = (int) sizeof(a0_cap_buf) - 1;
    }
    a0_cap_buf[last] = 0;
    if (len)   *len = a0_cap_bytes;
    if (trunc) *trunc = a0_cap_trunc;
    if (nul)   *nul = a0_cap_nul;
    return a0_cap_buf;
}

int a0_console_has(const char *frag) { return strstr(a0_cap_buf, frag) != 0 ? 1 : 0; }

/* 捕获缓冲里的行工具：断言只看逐字节捕获，不看 stdout。 */
static const char *a0_cap_find_line(const char *pre, int *linelen)
{
    const char *p = a0_cap_buf;
    size_t n = strlen(pre);

    while (*p)
    {
        const char *e = strchr(p, '\n');
        int L = e ? (int) (e - p) : (int) strlen(p);

        if ((size_t) L >= n && memcmp(p, pre, n) == 0)
        {
            *linelen = L;
            return p;
        }
        if (!e) break;
        p = e + 1;
    }
    *linelen = 0;
    return RT_NULL;
}

static int a0_cap_line_ends(const char *line, int linelen, const char *suf)
{
    size_t n = strlen(suf);

    return (line && linelen >= (int) n && memcmp(line + linelen - n, suf, n) == 0) ? 1 : 0;
}

/* 一行的列数(逗号数 + 1)与其中 NUL 字节数：被剪断的行既缺列又带 NUL。 */
static int a0_cap_line_cols(const char *line, int linelen)
{
    int i, cols = 1;

    for (i = 0; i < linelen; i++)
    {
        if (line[i] == ',') cols++;
    }
    return cols;
}

static int a0_cap_line_nul(const char *line, int linelen)
{
    int i, n = 0;

    for (i = 0; i < linelen; i++)
    {
        if (line[i] == 0) n++;
    }
    return n;
}

/* frag 在该行内的字节偏移；没出现返回 -1。> 127 即"旧通道一定会剪掉的位置"。 */
static int a0_cap_line_pos(const char *line, int linelen, const char *frag)
{
    size_t n = strlen(frag);
    int i;

    if (!line) return -1;
    for (i = 0; i + (int) n <= linelen; i++)
    {
        if (memcmp(line + i, frag, n) == 0) return i;
    }
    return -1;
}

/* ------------------------------- 台账输出 */
static int g_fail_total, g_checks_total, g_case_fail, g_case_checks, g_cases_done;
static char g_case_id[16] = "-";

static void emit(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stdout, fmt, ap);
    va_end(ap);
}

static void case_begin(const char *id, const char *name)
{
    snprintf(g_case_id, sizeof g_case_id, "%s", id);
    g_case_fail = 0; g_case_checks = 0;
    emit("CASE %s %s\n", id, name);
}

static void case_end(void)
{
    emit("RESULT %s %s checks=%d fails=%d\n", g_case_id,
         (g_case_fail == 0) ? "PASS" : "FAIL", g_case_checks, g_case_fail);
    g_cases_done++;
}

static void ck(const char *what, long expect, long actual)
{
    g_checks_total++; g_case_checks++;
    if (expect != actual)
    {
        g_fail_total++; g_case_fail++;
        emit("  CHECK FAIL %s expect=%ld actual=%ld\n", what, expect, actual);
    }
    else emit("  CHECK PASS %s =%ld\n", what, actual);
}

/* -------------------------------------------- 生产打印通道(被 --wrap=printf 接住) */
static int  s_masked_at_print(void);          /* 线程本地屏蔽状态(定义在后段) */
static void c_mdelay_note(void);
static int  c_mdelay;

static int c_print;                       /* 生产侧 rt_kprintf 次数 */
static int c_win_viol;                    /* 窗口内禁止项命中次数 */
static char c_last_viol[64];

int __wrap_printf(const char *fmt, ...)
{
    va_list ap;
    int n;

    c_print++;
    if (s_masked_at_print())
    {
        c_win_viol++;
        snprintf(c_last_viol, sizeof c_last_viol, "kprintf-in-window");
    }
    va_start(ap, fmt);
    n = vprintf(fmt, ap);
    va_end(ap);
    return n;
}

/* shim 的 rt_thread_mdelay 内联体引用 ps_win_note(本链不会真的等待)。 */
void ps_win_note(const char *what)
{
    c_mdelay_note();
    if (s_masked_at_print())
    {
        c_win_viol++;
        snprintf(c_last_viol, sizeof c_last_viol, "%s-in-window", what);
    }
}

/* ------------------------------------------------------- PRIMASK/线程模型 */
static pthread_mutex_t s_atomic = PTHREAD_MUTEX_INITIALIZER;   /* 窗口 = 跨线程真独占 */
static int   s_win_live;
static __thread volatile int s_depth;
static __thread int          s_masked;
static __thread int          s_nest;
static struct rt_thread      s_self = { "a0", NULL, NULL };

/* 供上面的 wrap 使用: 状态与深度都是**线程本地**的(S2 N7 教训)。 */
static int s_masked_at_print(void) { return s_masked ? 1 : 0; }
static void c_mdelay_note(void)    { c_mdelay++; }

static void (*s_isr_fn[8])(void *);
static void  *s_isr_arg[8];
static int    s_isr_head, s_isr_tail, s_isr_queued, s_isr_delivered;

static void isr_dispatch_pending(void)
{
    while (s_isr_head != s_isr_tail)
    {
        void (*fn)(void *) = s_isr_fn[s_isr_head];
        void *arg = s_isr_arg[s_isr_head];

        s_isr_fn[s_isr_head] = NULL; s_isr_arg[s_isr_head] = NULL;
        s_isr_head = (s_isr_head + 1) % 8;
        s_nest++;
        if (fn) fn(arg);
        s_nest--;
        s_isr_delivered++;
    }
}

/* 发起一次"中断请求": 窗口开着 => 只能排队; 否则当场交付(nest 非 0 的上下文)。 */
static void isr_raise(void (*fn)(void *), void *arg)
{
    if (s_win_live)
    {
        int next = (s_isr_tail + 1) % 8;
        if (next == s_isr_head) { emit("ISR-QUEUE-OVERFLOW\n"); return; }
        s_isr_fn[s_isr_tail] = fn; s_isr_arg[s_isr_tail] = arg; s_isr_tail = next;
        s_isr_queued++;
        return;
    }
    s_nest++;
    fn(arg);
    s_nest--;
    s_isr_delivered++;
}

static int s_isr_count_delivered(void) { return s_isr_delivered; }

rt_base_t rt_hw_interrupt_disable(void)
{
    rt_base_t old = (rt_base_t)s_depth;

    if (s_depth == 0) { pthread_mutex_lock(&s_atomic); s_win_live = 1; }
    s_depth++;
    s_masked = 1;
    return old;
}

void rt_hw_interrupt_enable(rt_base_t level)
{
    s_depth = (int)level;
    if (s_depth == 0)
    {
        s_masked = 0;
        s_win_live = 0;
        pthread_mutex_unlock(&s_atomic);
        isr_dispatch_pending();       /* 目标: 退出临界区立即响应挂着的中断 */
    }
    else s_masked = 1;
}

rt_bool_t   rt_interrupt_get_nest(void) { return s_nest ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void)        { return (s_masked || s_nest) ? RT_NULL : &s_self; }

/* ------------------------------------------------------ 总线动作计数/脚本 */
static int c_pwm_dis, c_pwm_en, c_low, c_high, c_pin_write, c_shutdown;
static struct rt_device_pwm s_pwm_obj = { 1 };
static rt_int32_t pwm_script[4]; static int pwm_script_n, pwm_script_i;
static rt_int32_t low_script[4]; static int low_script_n, low_script_i;
static rt_uint32_t last_shutdown_code = 0xFFFFFFFFu;

rt_err_t rt_pwm_set(struct rt_device_pwm *p, int ch, rt_uint32_t per, rt_uint32_t pul)
{ (void)p; (void)ch; (void)per; (void)pul; return RT_EOK; }

rt_err_t rt_pwm_enable(struct rt_device_pwm *p, int ch)
{ (void)p; (void)ch; c_pwm_en++; return RT_EOK; }

rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    rt_int32_t rc = RT_EOK;

    (void)p; (void)ch;
    c_pwm_dis++;
    if (pwm_script_i < pwm_script_n) rc = pwm_script[pwm_script_i++];
    return (rt_err_t)rc;
}

void rt_pin_write(rt_base_t pin, rt_base_t value) { (void)pin; (void)value; c_pin_write++; }
rt_base_t rt_pin_read(rt_base_t pin) { (void)pin; return 0; }
rt_base_t rt_pin_get(const char *name) { (void)name; return 87; }
void rt_pin_mode(rt_base_t pin, rt_base_t mode) { (void)pin; (void)mode; }
rt_device_t rt_device_find(const char *name) { (void)name; return (rt_device_t)&s_pwm_obj; }

/* 许可(PC.23)的裸写端口: 生产在窗口内调这两条, 返回码可脚本化。 */
static rt_err_t permit_raw_write(rt_uint8_t level)
{
    rt_int32_t rc = RT_EOK;

    if (level == 0u)
    {
        c_low++;
        if (low_script_i < low_script_n) rc = low_script[low_script_i++];
    }
    else c_high++;
    return (rt_err_t)rc;
}

rt_base_t safety_drv_permit_pin(void) { return 87; }
rt_err_t safety_drv_permit_low_raw_held(void)  { return permit_raw_write(0u); }
rt_err_t safety_drv_permit_high_raw_held(void) { return permit_raw_write(1u); }
rt_uint32_t tmc2209_config_view_generation(void) { return 0u; }

/* spm trip 的真实副作用载体: 本链只数次数, 不接任何硬件。 */
void safety_force_shutdown(rt_uint32_t code) { c_shutdown++; last_shutdown_code = code; }

/* mutex/event/线程端口: 本链不测锁; 只在被误用于窗口内时计成违例。 */
rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{ (void)m; (void)name; (void)flags; return RT_EOK; }
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout)
{
    (void)m; (void)timeout;
    if (s_masked)
    {
        c_win_viol++;
        snprintf(c_last_viol, sizeof c_last_viol, "mutex-take-in-window");
    }
    return RT_EOK;
}
rt_err_t rt_mutex_release(struct rt_mutex *m) { (void)m; return RT_EOK; }
rt_err_t rt_mutex_detach(struct rt_mutex *m)  { (void)m; return RT_EOK; }
rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag)
{ (void)e; (void)name; (void)flag; return RT_EOK; }
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event) { (void)e; (void)event; return RT_EOK; }
rt_err_t rt_event_recv(struct rt_event *e, rt_uint32_t ev, rt_uint32_t fl,
                       rt_int32_t to, rt_uint32_t *recv)
{ (void)e; (void)ev; (void)fl; (void)to; if (recv) *recv = 0u; return RT_EOK; }
rt_err_t rt_event_detach(struct rt_event *e) { (void)e; return RT_EOK; }
rt_thread_t rt_thread_create(const char *n, void (*e)(void *), void *p, int s,
                             rt_uint8_t pr, rt_uint32_t tk)
{ (void)n; (void)e; (void)p; (void)s; (void)pr; (void)tk; return RT_NULL; }
rt_err_t rt_thread_startup(rt_thread_t t) { (void)t; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t)  { (void)t; return RT_EOK; }

int  port_pwm_disable_calls(void)  { return c_pwm_dis; }
int  port_pwm_enable_calls(void)   { return c_pwm_en; }
int  port_permit_low_calls(void)   { return c_low; }
int  port_permit_high_calls(void)  { return c_high; }
int  port_pin_write_calls(void)    { return c_pin_write; }
int  port_shutdown_calls(void)     { return c_shutdown; }
int  port_print_calls(void)        { return c_print; }
int  port_mutex_calls(void)        { return 0; }        /* 本链不调锁 */
int  port_mdelay_calls(void)       { return c_mdelay; }
int  port_window_violations(void)  { return c_win_viol; }
const char *port_window_last_violation(void) { return c_last_viol; }
void port_window_open(void)  { /* 深度模型自带, 保留接口 */ }
void port_window_close(void) { }

void port_pwm_script(rt_int32_t a, rt_int32_t b)
{ pwm_script_n = 2; pwm_script_i = 0; pwm_script[0] = a; pwm_script[1] = b; }
void port_permit_script(rt_int32_t a, rt_int32_t b)
{ low_script_n = 2; low_script_i = 0; low_script[0] = a; low_script[1] = b; }

void port_bus_reset(void)
{
    c_pwm_dis = c_pwm_en = c_low = c_high = c_pin_write = c_shutdown = 0;
    c_print = c_mdelay = c_win_viol = 0;
    pwm_script_n = low_script_n = pwm_script_i = low_script_i = 0;
    c_last_viol[0] = '\0';
    last_shutdown_code = 0xFFFFFFFFu;
    s_isr_head = s_isr_tail = s_isr_queued = s_isr_delivered = 0;
}

/* --------------------------------------------------------- PPB 寄存器模型 */
#define M_COUNTFLAG        (1UL << 16)
#define M_ST_ENABLE        (1UL << 0)
#define M_ST_CLKSRC        (1UL << 2)
#define M_CTRL_CYCCNTENA   (1UL << 0)
#define M_CTRL_NOCYCCNT    (1UL << 25)

static struct {
    int implemented;              /* 0 => DWT_CTRL.NOCYCCNT=1(芯片没实现周期计数器) */
    rt_uint32_t step;             /* 每次 now() 的脚本化增量; 0 => 不递增 */
    rt_uint32_t cyccnt;
    rt_uint32_t ctr;
    rt_uint32_t demcr;
    rt_uint32_t systick_ctrl;
    rt_uint32_t systick_load;
    int reload_inside;            /* 1 => 第二次 CTRL 读看到 COUNTFLAG(窗口内重装过) */
    int load_drift_after;         /* >=0 => 第 n 次 CTRL 读之后 LOAD 变化(配置漂移) */
    int force_wrap;               /* 1 => body_end 那次读回绕到 begin 之下 */
    int ctr_reads, ctrl_reads, enable_calls;
} P;

static port_hook_t now_hook, ctrl_hook;
static __thread int my_now_idx;
static __thread int my_ctrl_idx;
static __thread rt_uint32_t my_begin_seen;

void port_ppb_reset(rt_bool_t implemented, rt_uint32_t step_per_read, rt_bool_t systick_on)
{
    P.implemented = implemented ? 1 : 0;
    P.step = step_per_read;
    P.cyccnt = 1000u;
    P.ctr = P.implemented ? 0u : M_CTRL_NOCYCCNT;
    P.demcr = 0u;
    P.systick_ctrl = systick_on ? (M_ST_ENABLE | M_ST_CLKSRC) : 0u;
    P.systick_load = systick_on ? PORT_LOAD_DEFAULT : 0u;
    P.reload_inside = 0; P.load_drift_after = -1; P.force_wrap = 0;
    P.ctr_reads = P.ctrl_reads = P.enable_calls = 0;
    my_now_idx = 0; my_ctrl_idx = 0; my_begin_seen = 0;
    now_hook = NULL; ctrl_hook = NULL;
}

void port_ppb_reload_inside(rt_bool_t on) { P.reload_inside = on ? 1 : 0; }
void port_ppb_load_drift_after(int n)     { P.load_drift_after = n; }
void port_ppb_make_wrap(void)             { P.force_wrap = 1; }
void port_ppb_stopped(void)               { P.step = 0u; }
/* 复位**本线程**的读序: 被测调用之前调用一次, 让脚本条件按"同一笔调用内的第几次读"对齐
 * (pre=0, body_begin=1, body_end=2, post=3; CTRL 起点读=0, 终点读=1)。 */
void port_ppb_arm(void)                   { my_now_idx = 0; my_ctrl_idx = 0; my_begin_seen = 0; }
int  port_ppb_ctr_reads(void)             { return P.ctr_reads; }
int  port_ppb_ctrl_reads(void)            { return P.ctrl_reads; }
int  port_ppb_enable_calls(void)          { return P.enable_calls; }
void port_set_now_hook(port_hook_t h)     { now_hook = h; my_now_idx = 0; }
void port_set_ctrl_hook(port_hook_t h)    { ctrl_hook = h; my_ctrl_idx = 0; }

rt_uint32_t spm_port_ctr_now(void)
{
    int idx = my_now_idx++;
    rt_uint32_t v;

    P.ctr_reads++;
    if (now_hook) now_hook(idx);              /* 确定接缝: 由用例决定在哪个读取点动作 */
    if (P.force_wrap && idx == 2 && my_begin_seen != 0u)
    {
        P.cyccnt = my_begin_seen - 5u;         /* 32 位回绕: end < begin */
        P.force_wrap = 0;
    }
    v = P.cyccnt;
    if (idx == 1) my_begin_seen = v;
    if (!(P.ctr & M_CTRL_CYCCNTENA) || !P.implemented) return v;   /* 没启用 => 不递增 */
    P.cyccnt = v + P.step;
    return v;
}

rt_uint32_t spm_port_ctr_ctrl(void) { return P.ctr; }
rt_uint32_t spm_port_demcr(void)    { return P.demcr; }

void spm_port_ctr_enable(rt_uint8_t trcena, rt_uint8_t cyccntena)
{
    P.enable_calls++;
    if (trcena)    P.demcr |= (1UL << 24);
    if (cyccntena) P.ctr   |= M_CTRL_CYCCNTENA;
}

rt_uint32_t spm_port_systick_ctrl(void)
{
    int idx = my_ctrl_idx++;
    rt_uint32_t v = P.systick_ctrl;

    P.ctrl_reads++;
    if (ctrl_hook) ctrl_hook(idx);
    if (P.reload_inside && idx == 1) v |= M_COUNTFLAG;    /* 窗口内重装过一次 */
    P.systick_ctrl = v & ~M_COUNTFLAG;                    /* 读即清: 副作用如实建模 */
    if (P.load_drift_after >= 0 && idx == P.load_drift_after) P.systick_load += 1u;
    return v;
}

rt_uint32_t spm_port_systick_load(void) { return P.systick_load; }
rt_uint32_t spm_port_primask(void)      { return s_masked ? 1u : 0u; }

/* -------------------------------------------------------------- 公共动作 */
static void boot_once(void)
{
    static int done;
    if (done) return;
    done = 1;
    sp_bind_pwm(&s_pwm_obj, 0);
    sp_bind_permit_pin(87);
}

/* 整行驱动：经**名字查表**分发(msh_get_cmd 语义)，夹具不直接调用 spm_cmd()。
 * 返回 -998 = 命令名不在表里(等价 msh 的 -RT_ERROR，一个函数都没被调用)；
 * 其它 = 被分到的那个函数自己的返回码。 */
static int cmd_line(const char *name, const char *a1, const char *a2)
{
#if NS800_TARGET_MEASUREMENT_A0
    char line[96];
    int  ret = -999;

    if (a1 == RT_NULL)                                snprintf(line, sizeof line, "%s", name);
    else if (a2 == RT_NULL)                           snprintf(line, sizeof line, "%s %s", name, a1);
    else                                              snprintf(line, sizeof line, "%s %s %s", name, a1, a2);
    if (spm_msh_exec(line, &ret) != 0) return -998;
    return ret;
#else
    /* 开关为 0 的变体里命令入口根本不存在(可执行体符号表可核对), 用例不调到这里。 */
    (void)name; (void)a1; (void)a2;
    return -999;
#endif
}

/* 默认用**目标上的命令名 spm** 驱动，即 cmd("trip","EXTRA") => 整行 "spm trip EXTRA"。 */
static int cmd(const char *a1, const char *a2)
{
    return cmd_line("spm", a1, a2);
}

#if NS800_TARGET_MEASUREMENT_A0
static void meas_start(const char *entry)
{
    (void)cmd("stop", NULL);
    (void)cmd("clear_records", NULL);
    port_bus_reset();
    (void)cmd("start", entry);
    port_ppb_arm();                      /* 被测调用从读序 0 开始 */
}

static void meas_end(void) { (void)cmd("stop", NULL); }

/* init 现在只在**明确静止态**允许(复审 A0-02a 的生命周期一半): 先 stop, 再把测量 RAM 清干净,
 * 才允许重新做能力检查。夹具用这个助手到达该状态, 不改生产判据。 */
static void quiesce_for_init(void)
{
    (void)cmd("stop", NULL);
    (void)cmd("clear_records", NULL);
}

static const spm_rec_t *last_rec(void)
{
    rt_uint32_t n = spm_records_used();
    return (n == 0u) ? RT_NULL : spm_record_at(n - 1u);
}

/* 会合设施: 事件等待, 不用 usleep 猜窗口。 */
static pthread_mutex_t jm = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  jc = PTHREAD_COND_INITIALIZER;
static int jq;

static void jm_become(int to)
{
    pthread_mutex_lock(&jm);
    jq = to;
    pthread_cond_broadcast(&jc);
    pthread_mutex_unlock(&jm);
}

static int jm_wait_until(int want, int guard_ms)
{
    struct timespec dl;
    int hit;

    clock_gettime(CLOCK_REALTIME, &dl);
    dl.tv_sec += guard_ms / 1000;
    dl.tv_nsec += (long)(guard_ms % 1000) * 1000000L;
    if (dl.tv_nsec >= 1000000000L) { dl.tv_nsec -= 1000000000L; dl.tv_sec += 1; }
    pthread_mutex_lock(&jm);
    while (jq != want)
    {
        if (pthread_cond_timedwait(&jc, &jm, &dl) == ETIMEDOUT) break;
    }
    hit = (jq == want) ? 1 : 0;
    pthread_mutex_unlock(&jm);
    return hit;
}

static __thread int am_a;                        /* 只有被点名的线程在接缝里停/动作 */
static int g_inner_ran, g_b_done_epoch;
#endif /* NS800_TARGET_MEASUREMENT_A0 */

/* ================================================================== 用例 */

/* TMA01 编译开关 0：三个原入口合同不变, 且 counter/记录/测量控制访问增量全为 0。 */
static void tma01_switch_off_contract(void)
{
    sp_proof_t p;
    sp_protect_outcome_t oc;
    rt_uint32_t e0, e1, e2;
    rt_err_t r;

    case_begin("TMA01", "switch OFF: 三个入口原合同 + 零测量访问");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();

    e0 = sp_epoch();
    memset(&p, 0, sizeof p);
    r = sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    ck("trip_fast 返回 EOK", (long)RT_EOK, (long)r);
    e1 = sp_epoch();
    ck("trip_fast 换了一次 epoch", 1, (e1 == e0 + 1u) ? 1 : 0);
    ck("trip_fast 真实 disable 一次", 1, port_pwm_disable_calls());
    ck("trip_fast 真实许可 LOW 一次", 1, port_permit_low_calls());

    r = sp_trip_for_operation(e1 + 5u, FAULT_TMC_COMM, 0u, MOT_STOP_SRC_GUARD_STOP, &p, &oc);
    ck("trip_for_operation 陈旧身份返回 -RT_EINTR", (long)(-RT_EINTR), (long)r);
    ck("陈旧身份不产生第二次关闭", 1, port_pwm_disable_calls());
    ck("陈旧身份不产生第二次 LOW", 1, port_permit_low_calls());

    e2 = sp_epoch();
    r = sp_off_attempt_for(e2, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, &p);
    ck("off_attempt(release) 返回 EOK", (long)RT_EOK, (long)r);
    ck("off_attempt disable 累计两次", 2, port_pwm_disable_calls());
    ck("off_attempt LOW 累计两次", 2, port_permit_low_calls());

    /* 开关为 0 的可执行体里: 测量侧叶子一次都不该被调用(runner 另做 nm 符号核对)。 */
    ck("零 CYCCNT 读", 0, port_ppb_ctr_reads());
    ck("零 SysTick CTRL 读", 0, port_ppb_ctrl_reads());
    ck("零 DWT 使能写", 0, port_ppb_enable_calls());
    case_end();
}

#if NS800_TARGET_MEASUREMENT_A0

/* TMA02 计数器不可用(未实现 / 不递增)：不得产生有效时长；关闭仍真实执行。 */
static void tma02_counter_unavailable(void)
{
    sp_proof_t p;
    const spm_rec_t *r;
    int d0, l0;

    case_begin("TMA02", "计数器不可用 => 无有效时长, 关闭仍执行");
    boot_once();

    /* (a) 芯片没实现: DWT_CTRL.NOCYCCNT=1 */
    port_ppb_reset(RT_FALSE, 8u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    ck("NOCYCCNT => COUNTER_UNAVAILABLE", SPM_CNT_UNAVAILABLE, spm_counter_status());
    ck("NOCYCCNT 时不写使能位", 0, port_ppb_enable_calls());
    meas_start("trip_fast");
    d0 = port_pwm_disable_calls(); l0 = port_permit_low_calls();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("本次仍落了一条记录(状态/顺序证据不丢)", 1, r != NULL ? 1 : 0);
    if (r == NULL) { case_end(); return; }
    ck("记录里 counter_status=UNAVAILABLE", SPM_CNT_UNAVAILABLE, r->counter_status);
    ck("body_valid=0", 0, r->body_valid);
    ck("原因含 COUNTER_NOT_OK", SPM_IV_COUNTER_NOT_OK,
       r->invalid_reason & SPM_IV_COUNTER_NOT_OK);
    ck("四个时间戳保持原始 0", 1,
       (r->pre == 0u && r->body_begin == 0u && r->body_end == 0u && r->post == 0u) ? 1 : 0);
    ck("零值不被当成 0 耗时(必须带无效原因)", 1, r->invalid_reason != SPM_IV_NONE ? 1 : 0);
    ck("关闭动作真实发生: disable +1", 1, port_pwm_disable_calls() - d0);
    ck("关闭动作真实发生: LOW +1", 1, port_permit_low_calls() - l0);

    /* (b) 实现了但按下去不递增 */
    port_ppb_reset(RT_TRUE, 0u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    ck("不递增 => COUNTER_UNAVAILABLE", SPM_CNT_UNAVAILABLE, spm_counter_status());
    ck("递增检查用有限读取(不轮询到成功)", 1, port_ppb_ctr_reads() >= 2 ? 1 : 0);
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("不递增场景 body_valid=0", 0, r ? r->body_valid : -1);
    ck("不递增场景仍真实调用 disable", 1, r ? r->pwm_called_by_this_call : -1);
    case_end();
}

/* TMA03 未 init 的 UNCHECKED 与 init 后不可用的 UNAVAILABLE 分开登记。 */
static void tma03_unchecked_vs_unavailable(void)
{
    sp_proof_t p;
    const spm_rec_t *r;

    case_begin("TMA03", "UNCHECKED 与 UNAVAILABLE 是两种事实");
    boot_once();

    port_ppb_reset(RT_TRUE, 8u, RT_TRUE);
    (void)cmd("stop", NULL);
    (void)cmd("clear_records", NULL);
    ck("未做检查 => COUNTER_UNCHECKED", SPM_CNT_UNCHECKED, spm_counter_status());
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("UNCHECKED 也落了记录", 1, r != NULL ? 1 : 0);
    if (r)
    {
        ck("UNCHECKED 不产出有效时长", 0, r->body_valid);
        ck("UNCHECKED 的原因位", SPM_IV_COUNTER_NOT_OK, r->invalid_reason & SPM_IV_COUNTER_NOT_OK);
        ck("未检查前不访问 PPB", 0, port_ppb_ctr_reads());
        ck("未检查前不读 CTRL(无清旗副作用)", 0, port_ppb_ctrl_reads());
    }

    quiesce_for_init();
    (void)cmd("init", NULL);
    ck("init 后 => COUNTER_OK", SPM_CNT_OK, spm_counter_status());
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("OK 场景的 counter_status 与 UNCHECKED 不同", SPM_CNT_OK, r ? r->counter_status : -1);
    ck("两种状态名不同(可判定)", 1,
       strcmp(spm_counter_status_name(SPM_CNT_UNCHECKED),
              spm_counter_status_name(SPM_CNT_UNAVAILABLE)) != 0 ? 1 : 0);
    case_end();
}

/* TMA04 主体有效；CONFIG_READBACK 不升级成实测 µs；包围区间独立判无效。 */
static void tma04_valid_body_enclosing_independent(void)
{
    sp_proof_t p;
    const spm_rec_t *r;

    case_begin("TMA04", "主体有效 + 包围区间独立无效");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    ck("init => COUNTER_OK", SPM_CNT_OK, spm_counter_status());
    ck("init => clock_basis 只到 CONFIG_READBACK", SPM_CLK_CONFIG_READBACK, spm_clock_basis());
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    if (r == NULL) { ck("记录存在", 1, 0); case_end(); return; }
    ck("记录存在", 1, 1);
    ck("body_valid=1", 1, r->body_valid);
    ck("主体计数差 = 脚本步长", 7, (long)(r->body_end - r->body_begin));
    ck("guard 依据被留档(LOAD 已捕获)", SPM_GD_LOAD_CAPTURED,
       r->guard_metadata & SPM_GD_LOAD_CAPTURED);
    ck("guard 依据含 SysTick 已启用", SPM_GD_SYSTICK_ENABLED,
       r->guard_metadata & SPM_GD_SYSTICK_ENABLED);
    ck("guard 依据含处理器时钟", SPM_GD_CLKSOURCE_PROCESSOR,
       r->guard_metadata & SPM_GD_CLKSOURCE_PROCESSOR);
    ck("guard_load 记的是 LOAD 而不是 0", PORT_LOAD_DEFAULT, (long)r->guard_load_seen);
    ck("包围区间独立判无效", 0, r->enclosing_valid);
    ck("包围区间原因位", SPM_IV_ENCLOSING_UNPROVEN,
       r->invalid_reason & SPM_IV_ENCLOSING_UNPROVEN);
    ck("pre/post 原始值仍保留(未被主体有效性改写)", 1, r->post > r->pre ? 1 : 0);
    ck("clock_basis 未升级成 EXTERNAL_VERIFIED", 1,
       r->clock_basis != SPM_CLK_EXTERNAL_VERIFIED ? 1 : 0);
    ck("有效主体样本仍带未升级的时钟依据(body_valid 不等于已校准 µs)", 1,
       (r->body_valid == 1u && r->clock_basis == SPM_CLK_CONFIG_READBACK) ? 1 : 0);
    case_end();
}

/* TMA05 主体长于 SysTick 一圈 => COUNTFLAG 使其无效。 */
static void tma05_reload_in_body(void)
{
    sp_proof_t p;
    const spm_rec_t *r;

    case_begin("TMA05", "窗口内发生重装 => 主体样本无效(混叠不可排除)");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    port_ppb_reload_inside(RT_TRUE);
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("记录存在", 1, r != NULL ? 1 : 0);
    if (!r) { case_end(); return; }
    ck("body_valid=0", 0, r->body_valid);
    ck("原因位 RELOAD_IN_BODY", SPM_IV_RELOAD_IN_BODY, r->invalid_reason & SPM_IV_RELOAD_IN_BODY);
    ck("guard 记了终点旗", SPM_GD_FLAG_POST_SET, r->guard_metadata & SPM_GD_FLAG_POST_SET);
    ck("没有用 VAL 模差分凑时长(无效就是无效)", 1, r->body_valid == 0u ? 1 : 0);
    ck("原始四时间戳仍完整保留", 1,
       (r->pre && r->body_begin && r->body_end && r->post) ? 1 : 0);
    case_end();
}

/* TMA06 回绕 / 配置漂移 / 判据不成立：三种无效都保留记录与原因。 */
static void tma06_wrap_cfg_guard(void)
{
    sp_proof_t p;
    const spm_rec_t *r;

    case_begin("TMA06", "回绕/配置变化/guard 不成立 => 保留无效记录与原因");
    boot_once();

    /* (a) 32 位回绕 */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    port_ppb_make_wrap();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(a) 记录保留", 1, r != NULL ? 1 : 0);
    if (r)
    {
        ck("(a) body_valid=0", 0, r->body_valid);
        ck("(a) 原因含 COUNTER_WRAPPED", SPM_IV_COUNTER_WRAPPED,
           r->invalid_reason & SPM_IV_COUNTER_WRAPPED);
        ck("(a) complete=1(无效但确实是完整一条)", 1, r->complete);
    }

    /* (b) 两次读之间 LOAD 漂移 */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    port_ppb_load_drift_after(0);
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(b) 落了一条记录", 1, (long)spm_records_used());
    if (r)
    {
        ck("(b) body_valid=0", 0, r->body_valid);
        ck("(b) 原因含 CFG_CHANGED", SPM_IV_CFG_CHANGED, r->invalid_reason & SPM_IV_CFG_CHANGED);
    }

    /* (c) SysTick 判据不成立(没启用 => 没有"无重装"依据可用) */
    port_ppb_reset(RT_TRUE, 7u, RT_FALSE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    ck("(c) 计数器仍可 OK", SPM_CNT_OK, spm_counter_status());
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(c) 记录仍产出", 1, (long)spm_records_used());
    if (r)
    {
        ck("(c) body_valid=0", 0, r->body_valid);
        ck("(c) 原因含 GUARD_UNAVAILABLE", SPM_IV_GUARD_UNAVAILABLE,
           r->invalid_reason & SPM_IV_GUARD_UNAVAILABLE);
        ck("(c) 状态/顺序证据不丢(inhibit 仍记)", 1, r->inhibit ? 1 : 0);
    }
    case_end();
}

/* TMA07 trip_fast 两条真实动作独立；失败组合与未插桩合同一致。 */
static void tma07_two_independent_actions(void)
{
    sp_proof_t p;
    sp_snapshot_t s;
    const spm_rec_t *r;

    case_begin("TMA07", "PWM 失败/LOW 失败组合的合同与未插桩一致");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);

    /* (a) PWM 失败, LOW 成功 */
    meas_start("trip_fast");
    port_bus_reset();
    port_pwm_script(-RT_EIO, RT_EOK);
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    r = last_rec();
    sp_get(&s);
    ck("(a) disable 恰好一次", 1, port_pwm_disable_calls());
    ck("(a) LOW 恰好一次(与 PWM 失败无关)", 1, port_permit_low_calls());
    if (r)
    {
        ck("(a) 返回 PWM 原始错误优先", (long)(-RT_EIO), (long)r->call_outcome_or_branch);
        ck("(a) pwm raw 分开留档", (long)(-RT_EIO), (long)r->observed_pwm_raw_rc);
        ck("(a) permit raw 是 EOK(未被合并)", (long)RT_EOK, (long)r->observed_permit_write_rc);
    }
    ck("(a) owner 归未知持有", SP_HELD_UNKNOWN, (int)s.owner);
    ck("(a) inhibit=1", 1, s.inhibit);

    /* (b) PWM 成功, LOW 失败 */
    meas_start("trip_fast");
    port_bus_reset();
    port_permit_script(-RT_EIO, RT_EOK);
    sp_trip_fast(FAULT_TMC_COMM, 0u, MOT_STOP_SRC_ESTOP, &p);
    r = last_rec();
    sp_get(&s);
    ck("(b) 返回许可的原始错误", (long)(-RT_EIO), (long)(r ? r->call_outcome_or_branch : 999));
    ck("(b) pwm raw=EOK", (long)RT_EOK, (long)(r ? r->observed_pwm_raw_rc : 999));
    ck("(b) permit raw=-RT_EIO", (long)(-RT_EIO), (long)(r ? r->observed_permit_write_rc : 999));
    ck("(b) 请求电平记为 LOW", 0, (int)(r ? r->observed_permit_level : 99));
    ck("(b) pending 义务产生", 1, s.metadata_pending ? 1 : 0);
    ck("(b) 两条动作各一次", 1,
       (port_pwm_disable_calls() == 1 && port_permit_low_calls() == 1) ? 1 : 0);
    case_end();
}

/* TMA08 陈旧身份：真实写次数 0；本次 outcome 与历史 raw 分清。 */
static void tma08_stale_identity(void)
{
    sp_proof_t p;
    sp_protect_outcome_t oc;
    sp_snapshot_t s;
    const spm_rec_t *r;
    rt_uint32_t stale;
    rt_err_t rc;

    case_begin("TMA08", "trip_for_operation 陈旧身份: 一个寄存器写都不发");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_for_operation");

    port_bus_reset();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);        /* 留下一笔历史 raw */
    stale = sp_epoch() - 3u;                                        /* 故意用一个旧代 */
    port_bus_reset();
    rc = sp_trip_for_operation(stale, FAULT_TMC_COMM, 0u, MOT_STOP_SRC_GUARD_STOP, &p, &oc);
    r = last_rec();
    sp_get(&s);
    ck("陈旧身份返回 -RT_EINTR", (long)(-RT_EINTR), (long)rc);
    ck("陈旧身份 disable 次数 0", 0, port_pwm_disable_calls());
    ck("陈旧身份 LOW 次数 0", 0, port_permit_low_calls());
    ck("早退也结束本次记录", 1, r != NULL ? 1 : 0);
    if (r)
    {
        ck("branch=TRIP_OP_STALE", SPM_BR_TRIP_OP_STALE, r->branch_id);
        ck("本次未执行动作: pwm_called=0", 0, r->pwm_called_by_this_call);
        ck("本次未执行动作: low_called=0", 0, r->low_called_by_this_call);
        ck("outcome 记的是本笔返回值", (long)(-RT_EINTR), (long)r->call_outcome_or_branch);
        ck("observed_* 是 canonical 历史(等于当前快照, 且已标注 0/0)", 1,
           (r->observed_pwm_raw_rc == (rt_int32_t)s.pwm.raw_rc &&
            r->observed_pwm_epoch == s.pwm.epoch) ? 1 : 0);
        ck("input_expected_epoch 保留本次请求的原身份", stale, r->input_expected_epoch);
        ck("陈旧出口不换代", 1, r->epoch_before == r->epoch_after ? 1 : 0);
        ck("trips 未被晚到结果抬高", 1, r->trips_before == r->trips_after ? 1 : 0);
    }
    case_end();
}

/* TMA09 off_attempt 七个出口：动作次数与返回优先序不被记录改变。 */
static void tma09_off_attempt_matrix(void)
{
    sp_proof_t p;
    const spm_rec_t *r;
    rt_uint32_t e, used0;

    case_begin("TMA09", "off_attempt 七个出口: 次数与返回优先序不变");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("off_attempt");
    used0 = spm_records_used();

    /* (1) 非法 owner */
    port_bus_reset();
    e = sp_epoch();
    r = RT_NULL;
    ck("(1) 非法 owner 返回 -RT_EINVAL", (long)(-RT_EINVAL),
       (long)sp_off_attempt_for(e, (sp_owner_t)99, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, &p));
    r = last_rec();
    ck("(1) 无任何硬件写", 0, port_pwm_disable_calls() + port_permit_low_calls());
    ck("(1) branch", SPM_BR_OFF_BAD_OWNER, r ? r->branch_id : -1);
    ck("(1) 本次动作计数 0/0", 0, r ? r->pwm_called_by_this_call + r->low_called_by_this_call : -1);

    /* (2) 陈旧身份 */
    port_bus_reset();
    ck("(2) 陈旧身份 -RT_EINTR", (long)(-RT_EINTR),
       (long)sp_off_attempt_for(e - 3u, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, &p));
    r = last_rec();
    ck("(2) 无硬件写", 0, port_pwm_disable_calls() + port_permit_low_calls());
    ck("(2) branch", SPM_BR_OFF_STALE, r ? r->branch_id : -1);

    /* (3) release 全成功 */
    port_bus_reset();
    e = sp_epoch();
    ck("(3) release 成功返回 EOK", (long)RT_EOK,
       (long)sp_off_attempt_for(e, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, &p));
    r = last_rec();
    ck("(3) disable 恰好一次", 1, port_pwm_disable_calls());
    ck("(3) LOW 恰好一次", 1, port_permit_low_calls());
    ck("(3) branch", SPM_BR_OFF_RELEASE_OK, r ? r->branch_id : -1);
    ck("(3) 本次动作计数 1/1", 2, r ? r->pwm_called_by_this_call + r->low_called_by_this_call : -1);

    /* (4) release + PWM 失败 => PWM 原始错误优先, 且不做第二轮 disable */
    port_bus_reset();
    port_pwm_script(-RT_EIO, RT_EOK);
    e = sp_epoch();
    ck("(4) PWM 失败优先返回", (long)(-RT_EIO),
       (long)sp_off_attempt_for(e, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, &p));
    r = last_rec();
    ck("(4) disable 只有一次", 1, port_pwm_disable_calls());
    ck("(4) LOW 仍独立尝试一次", 1, port_permit_low_calls());
    ck("(4) branch", SPM_BR_OFF_RELEASE_PE, r ? r->branch_id : -1);

    /* (5) release + PWM OK + LOW 失败 => 返回 le */
    port_bus_reset();
    port_permit_script(-RT_EIO, RT_EOK);
    e = sp_epoch();
    ck("(5) LOW 失败返回 le", (long)(-RT_EIO),
       (long)sp_off_attempt_for(e, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, &p));
    r = last_rec();
    ck("(5) branch", SPM_BR_OFF_RELEASE_LE, r ? r->branch_id : -1);
    ck("(5) 两路 raw 分开", 1,
       r && r->observed_permit_write_rc == -RT_EIO && r->observed_pwm_raw_rc == RT_EOK ? 1 : 0);

    /* (6) 保留预约 + PWM 失败 */
    port_bus_reset();
    port_pwm_script(-RT_EIO, RT_EOK);
    e = sp_epoch();
    ck("(6) 保留预约失败返回 pe", (long)(-RT_EIO),
       (long)sp_off_attempt_for(e, SP_MOTOR, MOT_STOP_SRC_RAMP_ZERO, FAULT_SOFT, 0u, 0, &p));
    r = last_rec();
    ck("(6) branch", SPM_BR_OFF_RESERVE_PE, r ? r->branch_id : -1);
    ck("(6) 失败支同窗仍独立尝试 LOW", 1, port_permit_low_calls());

    /* (7) 保留预约成功 => 原本只调 PWM, 不强加一次 LOW */
    port_bus_reset();
    e = sp_epoch();
    ck("(7) 保留预约成功返回 EOK", (long)RT_EOK,
       (long)sp_off_attempt_for(e, SP_MOTOR, MOT_STOP_SRC_RAMP_ZERO, FAULT_SOFT, 0u, 0, &p));
    r = last_rec();
    ck("(7) disable 一次", 1, port_pwm_disable_calls());
    ck("(7) LOW 次数 0", 0, port_permit_low_calls());
    ck("(7) branch", SPM_BR_OFF_RESERVE_ZERO_OK, r ? r->branch_id : -1);
    ck("(7) 记录里 low_called=0", 0, r ? r->low_called_by_this_call : -1);

    ck("七个出口 => 七条记录(每 id 恰好一次)", 7, (long)(spm_records_used() - used0));
    case_end();
}

/* TMA10 out==NULL 仍能同窗固定证据，不为补记录额外调用关闭/许可。 */
static void tma10_proof_null(void)
{
    const spm_rec_t *r;
    rt_uint32_t e;

    case_begin("TMA10", "out==NULL 也固定本次证据, 不额外触发硬件动作");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    port_bus_reset();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, RT_NULL);
    r = last_rec();
    ck("记录存在", 1, r != NULL ? 1 : 0);
    if (r)
    {
        ck("proof_null=1 被如实记录", 1, r->proof_out_null);
        ck("身份仍在窗口内钉好(epoch 前进一次)", 1,
           r->epoch_after == r->epoch_before + 1u ? 1 : 0);
        ck("两路 raw 都记了(没为补证据再调一次)", 1,
           r->pwm_called_by_this_call == 1u && r->low_called_by_this_call == 1u ? 1 : 0);
    }
    ck("硬件动作各一次", 1,
       port_pwm_disable_calls() == 1 && port_permit_low_calls() == 1 ? 1 : 0);

    port_bus_reset();
    e = sp_epoch();
    sp_off_attempt_for(e - 2u, SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, 0u, 1, RT_NULL);
    r = last_rec();
    ck("off_attempt 陈旧 + out==NULL: 零硬件写", 0,
       port_pwm_disable_calls() + port_permit_low_calls());
    ck("out==NULL 也记 proof_null", 1, r ? r->proof_out_null : -1);
    case_end();
}

/* --------- 并发用例：真线程 + 事件会合(不靠 usleep 猜窗口) --------- */

/* TMA11: 新故障在窗口内挂起、在**解屏蔽之后**交付；本次记录仍保持解屏蔽之前的身份与 raw 事实。
 * 模型形状与目标一致: PRIMASK=1 时中断只能挂在 interrupt 线上, 退出临界区立即响应
 * (夹具在最外层 rt_hw_interrupt_enable 里同步交付)。 */
static int g_isr_delivered_seen;

static void isr_new_fault(void *arg)
{
    sp_proof_t dummy;
    sp_protect_outcome_t oc;

    (void)arg;
    /* 用未在选择集里的入口做一笔真保护: 换 epoch, 但不占本批次槽 */
    sp_trip_for_operation(sp_epoch(), FAULT_IMU_COMM, 0u, MOT_STOP_SRC_GUARD_STOP, &dummy, &oc);
}

static void hook_raise_inside_window(int idx)
{
    if (!am_a || idx != 1) return;              /* idx 1 = body_begin 那次读(仍在窗口内) */
    g_inner_ran++;
    isr_raise(isr_new_fault, NULL);             /* 窗口开着 => 只能排队 */
}

static void tma11_fault_in_publish_seam(void)
{
    sp_proof_t p;
    sp_snapshot_t s;
    const spm_rec_t *r;

    case_begin("TMA11", "解屏蔽后交付的新故障不改写本次记录的身份");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    port_bus_reset();
    g_inner_ran = 0;
    g_isr_delivered_seen = s_isr_count_delivered();
    am_a = 1;
    port_set_now_hook(hook_raise_inside_window);
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    port_set_now_hook(NULL);
    am_a = 0;
    sp_get(&s);
    r = last_rec();
    ck("窗口内确实挂起了一次新故障", 1, g_inner_ran);
    ck("新故障在解屏蔽之后被交付(一次)", 1,
       s_isr_count_delivered() == g_isr_delivered_seen + 1 ? 1 : 0);
    ck("本批次仍只有这一条(内层入口未被选 => 不占槽)", 1, (long)spm_records_used());
    if (r)
    {
        ck("记录的 epoch_after 仍是本次那一代(没被解屏蔽后的新故障改写)", 1,
           r->epoch_after + 1u == s.epoch ? 1 : 0);
        ck("记录的 pwm raw 是本次那笔", 1,
           r->observed_pwm_raw_rc == (rt_int32_t)p.pwm.raw_rc ? 1 : 0);
        ck("记录的 epoch 字段与本次动作同代", 1,
           r->observed_pwm_epoch == r->epoch_after ? 1 : 0);
        ck("post 在恢复中断之后仍对应本次槽", 1, r->post >= r->body_end ? 1 : 0);
        ck("complete 由本次发布置位一次", 1, r->complete);
    }
    case_end();
}

/* TMA12: A 预约后被抢占, B 完整跑完并发布, A 续写 —— 槽与代次不串。 */
static void hook_a_pauses_after_reserve(int idx)
{
    if (idx != 0 || !am_a) return;              /* 只有 A 在 pre 读处停 */
    jm_become(1);                               /* A 已预约(槽 0), 还没开窗口 */
    (void)jm_wait_until(2, 5000);               /* 等 B 完整跑完并发布 */
}

static sp_proof_t g_pa, g_pb;
static rt_uint32_t g_a_epoch_before;

static void *thr_a(void *arg)
{
    (void)arg;
    am_a = 1;
    g_a_epoch_before = sp_epoch();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &g_pa);
    am_a = 0;
    return NULL;
}

static void *thr_b(void *arg)
{
    (void)arg;
    am_a = 0;
    sp_trip_fast(FAULT_TMC_COMM, 0u, MOT_STOP_SRC_DIAG_FORCE, &g_pb);
    g_b_done_epoch = (int)sp_epoch();
    jm_become(2);                               /* 放行 A */
    return NULL;
}

static void tma12_two_threads_slot_generation(void)
{
    pthread_t ta, tb;
    const spm_rec_t *ra, *rb;
    rt_uint32_t e0;

    case_begin("TMA12", "预约后被抢占 + 另一记录先完成: 槽与代次不串");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    port_bus_reset();
    e0 = sp_epoch();
    jq = 0;
    port_set_now_hook(hook_a_pauses_after_reserve);
    pthread_create(&ta, NULL, thr_a, NULL);
    ck("A 已预约槽 0 并停在开窗口之前", 1, jm_wait_until(1, 5000));
    pthread_create(&tb, NULL, thr_b, NULL);
    ck("B 已完整跑完并发布", 1, jm_wait_until(2, 5000));
    pthread_join(tb, NULL);
    pthread_join(ta, NULL);
    port_set_now_hook(NULL);

    ck("A 预约时看到的代次 = 用例起始代次", e0, g_a_epoch_before);
    ck("两次调用 => 两条记录", 2, (long)spm_records_used());
    ra = spm_record_at(0u); rb = spm_record_at(1u);
    ck("两条记录都在", 1, (ra && rb) ? 1 : 0);
    if (ra && rb)
    {
        ck("A 先预约(槽 0) B 后预约(槽 1)", 1,
           ra->slot == 0u && rb->slot == 1u ? 1 : 0);
        ck("两条都 complete", 2, (int)ra->complete + (int)rb->complete);
        ck("代次按预约顺序严格递增", 1,
           rb->capture_generation == ra->capture_generation + 1u ? 1 : 0);
        ck("A 的 epoch_before 是 B 那一代(B 先换代)", 1,
           ra->epoch_before == rb->epoch_after ? 1 : 0);
        ck("B 的 epoch_before = 用例起始代次", e0, rb->epoch_before);
        ck("A 的 epoch_after = B 的 epoch_after + 1", 1,
           ra->epoch_after == rb->epoch_after + 1u ? 1 : 0);
        ck("两条各自绑自己那一代的 pwm 记录", 1,
           (ra->observed_pwm_epoch == ra->epoch_after &&
            rb->observed_pwm_epoch == rb->epoch_after) ? 1 : 0);
        ck("两条 raw 各自独立(未被覆盖)", 1,
           ra->observed_pwm_raw_rc == RT_EOK && rb->observed_pwm_raw_rc == RT_EOK ? 1 : 0);
    }
    ck("B 确实完整跑过", 1, g_b_done_epoch > 0 ? 1 : 0);
    ck("硬件动作共两次", 2, port_pwm_disable_calls());
    case_end();
}

/* TMA13: ISR 上下文可记录；判据被扰动时外层标无效(机制标注, 见文件头边界说明)。 */
static void isr_trip_inner(void *arg)
{
    sp_proof_t p;
    (void)arg;
    sp_trip_fast(FAULT_SELF_TEST, 0u, MOT_STOP_SRC_DIAG_FORCE, &p);
}

static void hook_nested_at_guard_read(int idx)
{
    /* 外层已取到判据之后的第一次窗口内读(终点读, idx==1)进来一笔内层:
     * 只有不可屏蔽源能在目标上造成这种交错(文件头边界说明)。 */
    if (!am_a || idx != 1) return;
    g_inner_ran++;
    sp_trip_fast(FAULT_SOFT, 0u, MOT_STOP_SRC_DIAG_FORCE, RT_NULL);
}

static void tma13_isr_and_nested_guard(void)
{
    const spm_rec_t *outer, *inner;
    rt_uint32_t used0;

    case_begin("TMA13", "ISR 上下文可记录; 判据被扰动时外层标无效");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);

    /* (a) ISR 上下文里的一次关断 */
    meas_start("trip_fast");
    port_bus_reset();
    isr_raise(isr_trip_inner, NULL);
    ck("(a) ISR 里落了记录", 1, (long)spm_records_used());
    outer = last_rec();
    if (outer) ck("(a) context_isr=1", 1, outer->context_isr);
    ck("(a) 窗口内零打印/零锁/零等待", 0, port_window_violations());
    ck("(a) 两条动作各一次", 1,
       port_pwm_disable_calls() == 1 && port_permit_low_calls() == 1 ? 1 : 0);

    /* (b) 嵌套扰动: 内层在窗口里清掉了外层的"无重装"依据 */
    meas_start("trip_fast");
    port_bus_reset();
    used0 = spm_records_used();
    g_inner_ran = 0;
    am_a = 1;
    port_set_ctrl_hook(hook_nested_at_guard_read);
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, RT_NULL);
    port_set_ctrl_hook(NULL);
    am_a = 0;
    ck("(b) 内层确实进来了", 1, g_inner_ran);
    ck("(b) 外层与内层各一条记录", 2, (long)(spm_records_used() - used0));
    outer = spm_record_at(used0); inner = spm_record_at(used0 + 1u);
    if (outer && inner)
    {
        ck("(b) 外层被标 NESTED_GUARD", SPM_IV_NESTED_GUARD,
           outer->invalid_reason & SPM_IV_NESTED_GUARD);
        ck("(b) 外层 body_valid=0(不静默报有效)", 0, outer->body_valid);
        ck("(b) 内层也标 NESTED_GUARD", SPM_IV_NESTED_GUARD,
           inner->invalid_reason & SPM_IV_NESTED_GUARD);
        ck("(b) 两条都仍是完整记录", 2, (int)outer->complete + (int)inner->complete);
    }
    /* (c) 扰动之后下一笔必须能重新取到判据(深度没漏) */
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, RT_NULL);
    ck("(c) 下一笔重新取到判据 => body_valid 回到 1", 1,
       last_rec() ? last_rec()->body_valid : 0);
    case_end();
}

/* TMA14 64 槽满后第 65/66 笔仍关闭；dropped 可见；既有槽不被覆盖。 */
static void tma14_batch_full(void)
{
    sp_proof_t p;
    rt_uint32_t i;
    rt_uint32_t sum_before = 0u, sum_after = 0u;

    case_begin("TMA14", "批次满: 后续笔仍关闭, dropped 可见, 不覆盖既有槽");
    boot_once();
    port_ppb_reset(RT_TRUE, 3u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    port_bus_reset();
    for (i = 0u; i < SPM_SLOTS; i++) sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    ck("装满 64 槽", SPM_SLOTS, spm_records_used());
    ck("满之前 dropped=0", 0, (long)spm_dropped());
    for (i = 0u; i < SPM_SLOTS; i++)
    {
        const spm_rec_t *r = spm_record_at(i);
        if (r) sum_before += r->body_end + r->capture_generation;
    }
    sp_trip_fast(FAULT_TMC_COMM, 0u, MOT_STOP_SRC_ESTOP, &p);            /* 第 65 笔 */
    ck("第 65 笔不增加槽数", SPM_SLOTS, spm_records_used());
    ck("第 65 笔计成 dropped", 1, (long)spm_dropped());
    ck("第 65 笔的关闭动作照做", 65, port_pwm_disable_calls());
    ck("第 65 笔的 LOW 照做", 65, port_permit_low_calls());
    sp_trip_fast(FAULT_SOFT, 0u, MOT_STOP_SRC_ESTOP, &p);                 /* 第 66 笔 */
    ck("第 66 笔也只计丢弃(不循环复用)", 2, (long)spm_dropped());
    ck("槽数仍是 64", SPM_SLOTS, spm_records_used());
    for (i = 0u; i < SPM_SLOTS; i++)
    {
        const spm_rec_t *r = spm_record_at(i);
        if (r) sum_after += r->body_end + r->capture_generation;
    }
    ck("既有 64 槽未被覆盖", (long)sum_before, (long)sum_after);
    case_end();
}

/* TMA15 在途期间控制不等待、不误清；已预约的调用仍能完成。 */
static void hook_hold_inflight(int idx)
{
    if (!am_a || idx != 0) return;
    jm_become(1);                              /* A 已预约, 停在开窗口之前 */
    (void)jm_wait_until(2, 5000);              /* 等主测试做完 BUSY 检查 */
}

static void *thr_hold(void *arg)
{
    sp_proof_t p;
    (void)arg;
    am_a = 1;
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    am_a = 0;
    jm_become(3);
    return NULL;
}

static void tma15_busy_control(void)
{
    pthread_t t;
    const spm_rec_t *r;

    case_begin("TMA15", "在途期间 stop/dump/clear/start 不等待、不误清");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    (void)cmd("stop", NULL);
    (void)cmd("clear_records", NULL);
    port_bus_reset();
    (void)cmd("start", "trip_fast");
    jq = 0;
    port_set_now_hook(hook_hold_inflight);
    pthread_create(&t, NULL, thr_hold, NULL);
    ck("A 进到在途状态", 1, jm_wait_until(1, 5000));
    ck("在途计数 = 1", 1, (long)spm_in_flight());
    ck("stop 允许(只停预约, 不等待)", 0, cmd("stop", NULL));
    ck("stop 后状态=STOPPING", SPM_STATE_STOPPING, spm_state());
    ck("dump 在途 => 拒绝", 1, spm_dump_records() < 0 ? 1 : 0);
    ck("clear_records 在途 => 拒绝", 1, spm_clear_records() < 0 ? 1 : 0);
    ck("start 在非 IDLE => 拒绝", 1, cmd("start", "trip_fast") != 0 ? 1 : 0);
    r = spm_record_at(0u);
    ck("未完成槽不得当完整样本(complete=0)", 0, r ? r->complete : -1);
    ck("未完成槽的记录计数仍在(不静默丢)", 1, spm_records_used() == 1u ? 1 : 0);
    jm_become(2);                              /* 放行 A */
    ck("A 自己发布完成", 1, jm_wait_until(3, 5000));
    port_set_now_hook(NULL);
    pthread_join(t, NULL);
    ck("in_flight 归零", 0, (long)spm_in_flight());
    r = spm_record_at(0u);
    ck("该槽发布成 complete=1", 1, r ? r->complete : -1);
    ck("关闭动作没被 BUSY 挡住", 1, port_pwm_disable_calls());
    ck("放行后 dump 可用", 0, spm_dump_records());
    case_end();
}

/* TMA16 停止且无在途才可 dump/清 RAM；清测量数据不改 epoch/inhibit/锁存。 */
static void tma16_dump_and_clear_bounds(void)
{
    sp_proof_t p;
    sp_snapshot_t a, b;

    case_begin("TMA16", "dump/clear 前置与\"只清测量数据\"的边界");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    ck("ACTIVE 时 dump 被拒", 1, spm_dump_records() < 0 ? 1 : 0);
    ck("ACTIVE 时 clear_records 被拒", 1, spm_clear_records() < 0 ? 1 : 0);
    meas_end();
    ck("stop 后 dump 可用", 0, spm_dump_records());
    sp_get(&a);
    ck("clear 前记录仍在", 1, spm_records_used() == 1u ? 1 : 0);
    ck("clear_records 成功", 0, spm_clear_records());
    sp_get(&b);
    ck("清 RAM 后 cursor=0", 0, (long)spm_records_used());
    ck("epoch 未被清测量动作改变", (long)a.epoch, (long)b.epoch);
    ck("inhibit 未被改变", (long)a.inhibit, (long)b.inhibit);
    ck("motor 锁存未被改变", (long)a.motor_stop_latched, (long)b.motor_stop_latched);
    ck("diag 锁存未被改变", (long)a.diag_stop_latched, (long)b.diag_stop_latched);
    ck("pending 未被改变", (long)a.metadata_pending, (long)b.metadata_pending);
    ck("pending_epoch 未被改变", (long)a.pending_epoch, (long)b.pending_epoch);
    ck("清完回到 IDLE", SPM_STATE_IDLE, spm_state());
    case_end();
}

/* TMA17 命令门：非法输入/非法上下文零副作用；trip 只走一次正式关闭。 */
static void tma17_cli_gates(void)
{
    int reads0, ctrl0, en0, shut0, high0, pen0, dis0, pr0;

    case_begin("TMA17", "命令门: 非法输入/上下文零副作用; trip 只一次正式关闭");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    reads0 = port_ppb_ctr_reads(); ctrl0 = port_ppb_ctrl_reads();
    en0 = port_ppb_enable_calls(); shut0 = port_shutdown_calls();
    high0 = port_permit_high_calls(); pen0 = port_pwm_enable_calls();
    dis0 = port_pwm_disable_calls();

    /* 未知子命令 / 非法 entry / 缺参数: 必须在任何计数读取、状态变化与安全动作之前拒绝。 */
    (void)cmd("nonsense", NULL);
    (void)cmd("start", "bogus_entry");
    (void)cmd("start", NULL);
    ck("非法输入不读计数器", 0, port_ppb_ctr_reads() - reads0);
    ck("非法输入不动 CTRL", 0, port_ppb_ctrl_reads() - ctrl0);
    ck("非法输入不写使能位", 0, port_ppb_enable_calls() - en0);
    ck("非法 entry 不改状态", SPM_STATE_IDLE, spm_state());
    ck("非法输入不触发关闭", 0, port_shutdown_calls() - shut0);

    /* 非法上下文(已在屏蔽窗口里) => 拒绝且不在其中打印 */
    quiesce_for_init();
    (void)cmd("init", NULL);
    reads0 = port_ppb_ctr_reads();
    {
        rt_base_t l = rt_hw_interrupt_disable();
        pr0 = port_print_calls();
        (void)cmd("cap", NULL);
        (void)cmd("dump", NULL);
        (void)cmd("clear_records", NULL);
        (void)cmd("trip", NULL);
        (void)cmd("init", NULL);
        ck("屏蔽上下文里拒绝命令且不打印", 0, port_print_calls() - pr0);
        ck("屏蔽上下文里不读计数器", 0, port_ppb_ctr_reads() - reads0);
        ck("屏蔽上下文里没触发正式关闭", 0, port_shutdown_calls() - shut0);
        rt_hw_interrupt_enable(l);
    }

    /* 合法 trip: 恰好一次 safety_force_shutdown(FAULT_SELF_TEST), 不开任何东西 */
    port_bus_reset();
    ck("trip 返回成功", 0, cmd("trip", NULL));
    ck("trip 只调一次 safety_force_shutdown", 1, port_shutdown_calls());
    ck("trip 用的是 FAULT_SELF_TEST", 1, last_shutdown_code == FAULT_SELF_TEST ? 1 : 0);
    ck("trip 不请求许可 HIGH", 0, port_permit_high_calls() - high0);
    ck("trip 不 enable PWM", 0, port_pwm_enable_calls() - pen0);
    ck("trip 不自己调 disable(交既有正式链)", 0, port_pwm_disable_calls() - dis0);
    ck("trip 不自动改测量状态", SPM_STATE_IDLE, spm_state());
    case_end();
}

/* TMA18 台账：窗口内禁止项 / 发布顺序 / 每 id 恰好一次 / 丢失与未完成不冒充有效样本。 */
static void tma18_ledger_and_publish_order(void)
{
    sp_proof_t p;
    rt_uint32_t i, n, valid = 0u, complete = 0u, enclosing_valid = 0u;
    rt_uint32_t prev_gen = 0u, order_ok = 1u;

    case_begin("TMA18", "台账: 窗口禁止项/发布顺序/每调用一条/无效样本不入统计");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    (void)cmd("init", NULL);
    meas_start("trip_fast");
    port_bus_reset();
    for (i = 0u; i < 5u; i++) sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    n = spm_records_used();
    ck("五次调用 => 五条记录", 5, (long)n);
    ck("窗口内禁止项命中 0", 0, port_window_violations());
    ck("窗口期间没有登记过任何违例(原因串保持空)", 1,
       port_window_last_violation()[0] == '\0' ? 1 : 0);
    ck("等待/锁调用为 0", 0, port_mdelay_calls());
    ck("窗口期间零生产打印", 0, port_print_calls());
    for (i = 0u; i < n; i++)
    {
        const spm_rec_t *r = spm_record_at(i);
        if (!r) { order_ok = 0u; break; }
        if (r->complete) complete++;
        if (r->body_valid) valid++;
        if (r->enclosing_valid) enclosing_valid++;
        if (i != 0u && r->capture_generation != prev_gen + 1u) order_ok = 0u;
        prev_gen = r->capture_generation;
    }
    ck("RESERVED->COMPLETE 恰好一次(五条都发布)", 5, (long)complete);
    ck("有效时长样本数 = 5(判据成立)", 5, (long)valid);
    ck("包围区间没有一条被当有效", 0, (long)enclosing_valid);
    ck("导出顺序与预约顺序一致(代次严格递增)", 1, order_ok ? 1 : 0);
    ck("每 id 恰好一条(不重复发布)", 1,
       (last_rec() && last_rec()->entry_id == SPM_ENTRY_TRIP_FAST) ? 1 : 0);
    meas_end();
    ck("停止后 dump 可打印(打印只发生在窗口外)", 0, spm_dump_records());
    ck("导出后生产打印>0 且违例仍为 0", 1,
       (port_print_calls() > 0 && port_window_violations() == 0) ? 1 : 0);
    (void)cmd("clear_records", NULL);
    ck("clear 后无残留槽可被当成样本", 0, (long)spm_records_used());
    case_end();
}

/* TMA19 目标命令名接缝：注册出来的是 spm，函数名 spm_cmd 不是命令；分发只经名字查表。 */
static void tma19_command_name_binding(void)
{
    int shut0, ctr0;

    case_begin("TMA19", "命令名接缝: FSymTab 名 spm -> spm_cmd; 函数名不是命令");
    boot_once();
    ck("本链只注册一条命令", 1, spm_fsym_n);
    ck("注册名逐字是 spm(不是 spm_cmd)", 0,
       spm_fsym_tab[0] != RT_NULL ? strcmp(spm_fsym_tab[0]->name, "spm") : -1);
    ck("描述串逐字在位", 0, spm_fsym_tab[0] != RT_NULL ?
       strcmp(spm_fsym_tab[0]->desc, "NS800 target measurement A0 control") : -1);
    ck("按完整名查表得到的函数就是 spm_cmd", 1,
       spm_msh_get_cmd("spm", 3) == (syscall_func)spm_cmd ? 1 : 0);
    ck("函数名 spm_cmd 不在表里(A0-01 的根因)", 1,
       spm_msh_get_cmd("spm_cmd", 7) == RT_NULL ? 1 : 0);
    ck("前缀 sp 不匹配完整名", 1, spm_msh_get_cmd("spm", 2) == RT_NULL ? 1 : 0);
    ck("前缀 spm_ 不匹配完整名", 1, spm_msh_get_cmd("spm_", 4) == RT_NULL ? 1 : 0);

    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();
    shut0 = port_shutdown_calls();
    ctr0  = port_ppb_ctr_reads();
    ck("整行 'spm_cmd trip' => 名字查不到, 未分发", -998, cmd_line("spm_cmd", "trip", RT_NULL));
    ck("未分发时没有正式关闭", 0, port_shutdown_calls() - shut0);
    ck("未分发时没有计数读", 0, port_ppb_ctr_reads() - ctr0);
    ck("整行 'spm trip' => 经表分发返回 0", 0, cmd_line("spm", "trip", RT_NULL));
    ck("分发到的那条命令真做了一次正式关闭", 1, port_shutdown_calls() - shut0);
    case_end();
}

/* TMA20 严格参数矩阵：每个子命令的缺参/多参、未知子命令、非法 entry 都在任何动作之前拒绝。 */
static void tma20_strict_arg_matrix(void)
{
    static const char *plain[] = { "cap", "init", "stop", "status", "dump",
                                   "clear_records", "trip" };
    int i, rej_rows = 0, print_rows = 0, silent_rows = 0;

    case_begin("TMA20", "严格参数矩阵: 缺参/多参/未知/非法 entry 先于任何动作拒绝");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();
    quiesce_for_init();
    ck("合法 init 先建立能力", 0, cmd("init", NULL));

    /* (a) 七个单参数子命令各加一个多余参数：rc=1、零动作、零计数读、状态不变、可打印 */
    for (i = 0; i < (int)(sizeof plain / sizeof plain[0]); i++)
    {
        rt_uint32_t st0 = spm_state();
        int c0 = port_ppb_ctr_reads(), k0 = port_ppb_ctrl_reads(), e0 = port_ppb_enable_calls();
        int s0 = port_shutdown_calls(), d0 = port_pwm_disable_calls(), l0 = port_permit_low_calls();
        int p0 = port_print_calls(), rc = cmd(plain[i], "EXTRA");

        if (rc == 1 && port_ppb_ctr_reads() == c0 && port_ppb_ctrl_reads() == k0 &&
            port_ppb_enable_calls() == e0 && port_shutdown_calls() == s0 &&
            port_pwm_disable_calls() == d0 && port_permit_low_calls() == l0 &&
            spm_state() == st0)                      rej_rows++;
        if (port_print_calls() > p0)                 print_rows++;
    }
    ck("7 个子命令的多余参数全部零动作拒绝", 7, rej_rows);
    ck("合法线程上下文里参数错误有打印(不是静默)", 7, print_rows);

    /* (b) 非法上下文里的参数错误与无参数：静默拒绝，同样零动作 */
    {
        rt_base_t lvl = rt_hw_interrupt_disable();
        rt_uint32_t st0 = spm_state();
        int s0 = port_shutdown_calls(), d0 = port_pwm_disable_calls(), l0 = port_permit_low_calls();
        int p0 = port_print_calls(), c0 = port_ppb_ctr_reads();

        if (cmd("trip", "EXTRA") != 0 && port_print_calls() == p0 &&
            port_shutdown_calls() == s0 && port_pwm_disable_calls() == d0 &&
            port_permit_low_calls() == l0 && port_ppb_ctr_reads() == c0 &&
            spm_state() == st0)                      silent_rows++;
        if (cmd(NULL, NULL) != 0 && port_print_calls() == p0) silent_rows++;
        rt_hw_interrupt_enable(lvl);
    }
    ck("屏蔽上下文里参数错误与无参数都静默零动作", 2, silent_rows);

    /* (c) 参数个数矩阵的其余形状 */
    quiesce_for_init();
    ck("无参数 => 打印用法并拒绝", 1, cmd(NULL, NULL));
    ck("未知子命令 => 拒绝", 1, cmd("nonsense", NULL));
    ck("start 缺 entry(argc=2) => 拒绝", 1, cmd("start", NULL));
    ck("start 多一个参数(argc=4) => 拒绝", 1, cmd("start", "trip_fast EXTRA"));
    ck("start 非法 entry(argc=3 但值不合法) => 拒绝", 1, cmd("start", "bogus_entry"));
    ck("非法参数之后状态仍是 IDLE", SPM_STATE_IDLE, (long)spm_state());
    ck("cap(argc=2) 合法 => 0", 0, cmd("cap", NULL));
    ck("start trip_fast(argc=3) 合法 => 0", 0, cmd("start", "trip_fast"));
    ck("再 start => 拒在 BUSY, 不是参数", 1, cmd("start", "trip_for_operation"));
    ck("stop 合法 => 0", 0, cmd("stop", NULL));
    ck("status 合法 => 0", 0, cmd("status", NULL));
    ck("dump 合法 => 0", 0, cmd("dump", NULL));
    ck("clear_records 合法 => 0", 0, cmd("clear_records", NULL));
    quiesce_for_init();
    case_end();
}

/* TMA21 逐样本门控失效：成功 init 之后关使能位/关 TRCENA/计数停转，样本无效但关闭次数不变。 */
static void tma21_per_sample_gate_failure(void)
{
    sp_proof_t p;
    const spm_rec_t *r;
    int d0, l0;

    case_begin("TMA21", "逐样本门控: init 后门被关/计数停转 => 该样本无效, 关闭动作不变");
    boot_once();

    /* (a) 成功 init 后关掉 DWT_CTRL.CYCCNTENA */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    ck("(a) init 成功", 0, cmd("init", NULL));
    meas_start("trip_fast");
    P.ctr &= ~M_CTRL_CYCCNTENA;
    port_ppb_arm();
    d0 = port_pwm_disable_calls(); l0 = port_permit_low_calls();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(a) body_valid=0", 0, r ? (int)r->body_valid : -1);
    ck("(a) 原因含 GATE_LOST", SPM_IV_GATE_LOST, r ? (int)(r->invalid_reason & SPM_IV_GATE_LOST) : -1);
    ck("(a) 门控字节缺 CYCCNTENA 两位", 1,
       r && (r->counter_gates & (SPM_GT_BEFORE_CYCCNTENA | SPM_GT_AFTER_CYCCNTENA)) == 0u ? 1 : 0);
    ck("(a) 缓存的能力仍是 OK(证明缓存不是充分条件)", SPM_CNT_OK, r ? (int)r->counter_status : -1);
    ck("(a) 原始值逐字保留(没被清成 0)", 1,
       r && r->body_begin == 1014u && r->body_end == 1014u ? 1 : 0);
    ck("(a) 关闭动作仍各恰好一次", 1,
       (port_pwm_disable_calls() - d0 == 1 && port_permit_low_calls() - l0 == 1) ? 1 : 0);

    /* (b) 成功 init 后关掉 DEMCR.TRCENA */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    ck("(b) init 成功", 0, cmd("init", NULL));
    meas_start("trip_fast");
    P.demcr &= ~0x01000000u;                          /* DEMCR.TRCENA = 0 */
    port_ppb_arm();
    d0 = port_pwm_disable_calls(); l0 = port_permit_low_calls();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(b) body_valid=0", 0, r ? (int)r->body_valid : -1);
    ck("(b) 门控字节四个位全缺", 1, r && (r->counter_gates & SPM_GT_ALL_MASK) == 0u ? 1 : 0);
    ck("(b) TRCENA 未证时不读 DWT_CTRL(不碰实现定义地址)", 1,
       r && (r->guard_metadata & SPM_GD_DWT_CYCCNTENA) == 0u ? 1 : 0);
    ck("(b) 关闭动作仍各恰好一次", 1,
       (port_pwm_disable_calls() - d0 == 1 && port_permit_low_calls() - l0 == 1) ? 1 : 0);

    /* (c) 两位使能都在，但计数停转 => 无有效进展（不是 0 耗时） */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    ck("(c) init 成功", 0, cmd("init", NULL));
    meas_start("trip_fast");
    port_ppb_stopped();
    port_ppb_arm();
    d0 = port_pwm_disable_calls(); l0 = port_permit_low_calls();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(c) body_valid=0", 0, r ? (int)r->body_valid : -1);
    ck("(c) 原因含 NO_PROGRESS", SPM_IV_NO_PROGRESS,
       r ? (int)(r->invalid_reason & SPM_IV_NO_PROGRESS) : -1);
    ck("(c) 门控字节仍全成立(区分于 a/b)", SPM_GT_ALL_MASK,
       r ? (int)(r->counter_gates & SPM_GT_ALL_MASK) : -1);
    ck("(c) 停转不被当成 0 耗时: invalid_reason 非空", 1,
       r && r->invalid_reason != SPM_IV_NONE ? 1 : 0);
    ck("(c) 关闭动作仍各恰好一次", 1,
       (port_pwm_disable_calls() - d0 == 1 && port_permit_low_calls() - l0 == 1) ? 1 : 0);

    /* (d) 对照: 什么都不动，样本仍有效（收紧判据没有把正常样本一起打掉） */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    ck("(d) init 成功", 0, cmd("init", NULL));
    meas_start("trip_fast");
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(d) 对照样本 body_valid=1", 1, r ? (int)r->body_valid : -1);
    ck("(d) 对照门控全成立", SPM_GT_ALL_MASK, r ? (int)(r->counter_gates & SPM_GT_ALL_MASK) : -1);
    case_end();
}

/* TMA22 能力检查生命周期：ACTIVE / 在途 / 有未处理旧批次时 init 被拒且零 PPB 访问。 */
static void tma22_init_capability_lifecycle(void)
{
    spm_ctx_t c;
    const spm_rec_t *r;
    rt_uint8_t cs0;
    int ctrl0, ctr0, en0;

    case_begin("TMA22", "能力检查生命周期: 非静止态 init 被拒, 不读清在途 guard, 不换缓存");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();
    quiesce_for_init();
    ck("静止态 init 允许", 0, cmd("init", NULL));
    cs0 = spm_counter_status();
    ck("静止态 init 后 counter=OK", SPM_CNT_OK, cs0);

    /* (a) ACTIVE 时 */
    ck("start 成功", 0, cmd("start", "trip_fast"));
    ctrl0 = port_ppb_ctrl_reads(); ctr0 = port_ppb_ctr_reads(); en0 = port_ppb_enable_calls();
    ck("ACTIVE 时 init 被拒", 1, cmd("init", NULL));
    ck("ACTIVE 时 init 没读 SysTick CTRL", 0, port_ppb_ctrl_reads() - ctrl0);
    ck("ACTIVE 时 init 没读 CYCCNT", 0, port_ppb_ctr_reads() - ctr0);
    ck("ACTIVE 时 init 没写使能位", 0, port_ppb_enable_calls() - en0);
    ck("ACTIVE 时能力缓存没被换掉", cs0, spm_counter_status());

    /* (b) 在途且已取 COUNTFLAG 判据时 */
    spm_open(&c, SPM_ENTRY_TRIP_FAST, 0u, 0u, 0u, RT_NULL);
    spm_win_note(&c, 1u);
    ck("在途记录确实存在", 1, spm_in_flight() == 1u ? 1 : 0);
    ctrl0 = port_ppb_ctrl_reads();
    ck("在途时 init 被拒", 1, cmd("init", NULL));
    ck("在途时没有读清那条记录的旗", 0, port_ppb_ctrl_reads() - ctrl0);
    spm_body_end(&c);
    spm_publish(&c, RT_EOK, SPM_BR_TRIP_FAST_DONE, 1u, 1u);
    r = last_rec();
    ck("在途记录照常发布(complete=1)", 1, r && r->complete ? 1 : 0);
    ck("该记录只用了一次能力检查(与预约时同源)", SPM_CNT_OK, r ? (int)r->counter_status : -1);
    ck("发布后在途归零", 0, (long)spm_in_flight());

    /* (c) 已停止但旧批次还没导出/清理时 */
    ck("stop", 0, cmd("stop", NULL));
    ck("有未处理旧批次时 init 被拒", 1, cmd("init", NULL));
    ck("dump 导出旧批次", 0, cmd("dump", NULL));
    ck("clear_records 只清测量 RAM", 0, cmd("clear_records", NULL));
    ck("清后记录数归零", 0, (long)spm_records_used());
    ck("清后回到静止态, 合法 init 重新可用", 0, cmd("init", NULL));
    ck("静止态的合法 init 确实做了能力检查(读了 CTRL)", 1,
       port_ppb_ctrl_reads() > ctrl0 ? 1 : 0);
    quiesce_for_init();
    case_end();
}

/* TMA23 历史旗只作观测：起点读已把它清掉且主体有进展 => 主体可有效；真负向仍拒绝。 */
static void tma23_historical_flag_positive(void)
{
    sp_proof_t p;
    const spm_rec_t *r;
    spm_ctx_t a, b;
    rt_uint32_t slot_a, slot_b, i, hits = 0u;

    case_begin("TMA23", "历史旗正向对照 + 真重装/回绕/配置/嵌套负向仍拒绝");
    boot_once();

    /* (a) 正向: 起点前有历史旗, 起点读把它清掉, 主体内无重装, 计数有进展 */
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    quiesce_for_init();
    ck("(a) init", 0, cmd("init", NULL));
    meas_start("trip_fast");
    P.systick_ctrl |= M_COUNTFLAG;
    port_ppb_arm();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(a) body_valid=1", 1, r ? (int)r->body_valid : -1);
    ck("(a) 历史旗仍作为观测位留档", SPM_GD_FLAG_PRE_SET,
       r ? (int)(r->guard_metadata & SPM_GD_FLAG_PRE_SET) : -1);
    ck("(a) 原因里除包围区间恒无效外没有别的", SPM_IV_ENCLOSING_UNPROVEN,
       r ? (int)r->invalid_reason : -1);
    ck("(a) 退役位 0x0004 不得再出现", 0, r ? (int)(r->invalid_reason & 0x0004u) : -1);

    /* (b) 负向: 历史旗 + 主体内真重装 => 仍然拒绝(正向修法没有把真检测器弄瞎) */
    meas_start("trip_fast");
    P.systick_ctrl |= M_COUNTFLAG;
    port_ppb_reload_inside(RT_TRUE);
    port_ppb_arm();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(b) body_valid=0", 0, r ? (int)r->body_valid : -1);
    ck("(b) 原因含 RELOAD_IN_BODY", SPM_IV_RELOAD_IN_BODY,
       r ? (int)(r->invalid_reason & SPM_IV_RELOAD_IN_BODY) : -1);
    ck("(b) 终点旗也留档", SPM_GD_FLAG_POST_SET,
       r ? (int)(r->guard_metadata & SPM_GD_FLAG_POST_SET) : -1);
    port_ppb_reload_inside(RT_FALSE);

    /* (c) 负向: 32 位回绕 */
    meas_start("trip_fast");
    P.systick_ctrl |= M_COUNTFLAG;
    port_ppb_make_wrap();
    port_ppb_arm();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(c) 回绕仍判无效", SPM_IV_COUNTER_WRAPPED,
       r ? (int)(r->invalid_reason & SPM_IV_COUNTER_WRAPPED) : -1);

    /* (d) 负向: 两次读之间 LOAD 漂移(配置变化) */
    meas_start("trip_fast");
    P.systick_ctrl |= M_COUNTFLAG;
    port_ppb_load_drift_after(0);
    port_ppb_arm();
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    meas_end();
    r = last_rec();
    ck("(d) 配置漂移仍判无效", SPM_IV_CFG_CHANGED,
       r ? (int)(r->invalid_reason & SPM_IV_CFG_CHANGED) : -1);
    port_ppb_load_drift_after(-1);

    /* (e) 负向: 嵌套扰动 —— 外层与内层都判无效 */
    meas_start("trip_fast");
    slot_a = spm_records_used();
    spm_open(&a, SPM_ENTRY_TRIP_FAST, 0u, 0u, 0u, RT_NULL);
    spm_win_note(&a, 1u);
    slot_b = spm_records_used();
    spm_open(&b, SPM_ENTRY_TRIP_FAST, 0u, 0u, 0u, RT_NULL);
    spm_win_note(&b, 1u);
    spm_body_end(&b);
    spm_publish(&b, RT_EOK, SPM_BR_TRIP_FAST_DONE, 1u, 1u);
    spm_body_end(&a);
    spm_publish(&a, RT_EOK, SPM_BR_TRIP_FAST_DONE, 1u, 1u);
    meas_end();
    r = spm_record_at(slot_a);
    ck("(e) 外层判无效", 0, r ? (int)r->body_valid : -1);
    ck("(e) 外层原因含 NESTED_GUARD", SPM_IV_NESTED_GUARD,
       r ? (int)(r->invalid_reason & SPM_IV_NESTED_GUARD) : -1);
    r = spm_record_at(slot_b);
    ck("(e) 内层也判无效", 0, r ? (int)r->body_valid : -1);

    for (i = 0u; i < spm_records_used(); i++)
    {
        const spm_rec_t *x = spm_record_at(i);
        if (x && (x->invalid_reason & 0x0004u)) hits++;
    }
    ck("本批记录里退役位出现次数 = 0", 0, (long)hits);
    quiesce_for_init();
    case_end();
}

/* ============ BOARD-A-01 导出通道用例（TMA25..TMA27） ============
 * 把"控制台 128 字节边界"变成主机可判定事实：修前必须失败，修后必须通过。
 * 判据一律建立在**逐字节捕获**的行长度/列数/尾字段上，因此一条行是否真的完整上线
 * 不再由"无限长 stdout"代替（任务书 §5.7）。
 */
/* TMA25：cap/init/status/usage/拒绝行等诊断长行同样必须完整（只修 CSV 不合格）。
 * 判据形状：该行存在 + 该行的**最后一个字段名**确实上线；另外如实数出"整行字节数 > 127"
 * 的行数(旧通道一定剪断的那些)，两条一起成立才说明长行是被修好的、而不是本来就短。 */
static void tma25_export_lines_complete(void)
{
    static const char *const PRE[] = {
        "[spm] counter=", "[spm] init: counter=", "[spm] status:", "[spm] stop:",
        "[spm] schema=", "[spm] clear_records:"
    };
    /* 每条格式串里最后出现的字段名(值除外, 值随 ABI 变, 字段名不随 ABI 变)。 */
    static const char *const LAST[] = {
        " dropped=", " clock_basis=", " gates_now=", "DWT/DEMCR ",
        " sizeof_ctx=", "Safety "
    };
    const char *line;
    int len = 0, trunc = 0, nul = 0, L, i, over127 = 0, last_ok = 0;

    case_begin("TMA25", "导出通道: cap/init/status/stop/usage/拒绝行的尾字段完整且无 NUL");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();
    quiesce_for_init();
    a0_console_reset();
    a0_console_capture(1);
    (void) cmd("cap", NULL);
    (void) cmd("init", NULL);
    (void) cmd("status", NULL);
    (void) cmd("stop", NULL);
    (void) cmd("start", NULL);              /* 参数个数不对 => 拒绝行 + usage 行 */
    (void) cmd("nope", NULL);               /* 未知子命令拒绝行 */
    (void) cmd("start", "bad_entry");       /* 未知 entry 拒绝行 */
    (void) cmd("clear_records", NULL);
    a0_console_capture(0);
    (void) a0_console_bytes(&len, &trunc, &nul);

    ck("上线字节里没有 127 截断行", 0, trunc);
    ck("上线字节里没有 NUL", 0, nul);
    ck("整段没有触发 EXPORT_ERROR", 0, a0_console_has("EXPORT_ERROR"));

    for (i = 0; i < (int) (sizeof PRE / sizeof PRE[0]); i++)
    {
        line = a0_cap_find_line(PRE[i], &L);
        ck("诊断行存在", 1, line ? 1 : 0);
        if (L > 127) over127++;
        if (line && a0_cap_line_pos(line, L, LAST[i]) >= 0) last_ok++;
        if (a0_cap_line_nul(line, L) != 0) ck("该行不带 NUL", 0, a0_cap_line_nul(line, L));
    }
    ck("六条被点名的诊断行的最后字段名全部上线", 6, last_ok);
    /* 本夹具的真实数据里恰好两条诊断行超过 127 字节(cap 第 3 行 142 B、init 行 129 B)：
     * 这两条就是"旧通道一定丢尾字段"的实例；条数变了说明判据基础变了, 要显式改。 */
    ck("其中超过 127 字节(旧通道必剪断)的行数 = 2", 2, over127);

    line = a0_cap_find_line("[spm] \xe6\x9c\xaa\xe7\x9f\xa5\xe5\xad\x90\xe5\x91\xbd\xe4\xbb\xa4", &L);
    ck("未知子命令拒绝行的中文尾完整上线", 1,
       a0_cap_line_ends(line, L, "\xe6\x9c\xaa\xe5\x81\x9a\xe5\x85\xb3\xe9\x97\xad)"));
    line = a0_cap_find_line("[spm] \xe5\x8f\x82\xe6\x95\xb0\xe4\xb8\xaa\xe6\x95\xb0\xe4\xb8\x8d\xe5\xaf\xb9", &L);
    ck("参数个数不对拒绝行的中文尾完整上线", 1,
       a0_cap_line_ends(line, L, "\xe6\x9c\xaa\xe5\x81\x9a\xe5\x85\xb3\xe9\x97\xad"));
    line = a0_cap_find_line("usage: spm", &L);
    ck("usage 行尾子命令 clear_records|trip 到位", 1,
       a0_cap_line_ends(line, L, "clear_records|trip"));

    quiesce_for_init();
    case_end();
}

/* TMA26：容量不足必须是具名短 ASCII 错误 + 负返回 + 绝不发半条内容。 */
static void tma26_export_overflow_named_error(void)
{
    char want[48];
    int len = 0, trunc = 0, nul = 0, rc;

    case_begin("TMA26", "导出通道: 整行超过 SPM_TEXT_CAP 时具名 EXPORT_ERROR 且不发半条");
    a0_console_reset();
    a0_console_capture(1);
    rc = spm_test_emit_long(SPM_TEXT_CAP + 8);
    a0_console_capture(0);
    (void) a0_console_bytes(&len, &trunc, &nul);

    ck("超限整行返回 -1", -1, rc);
    ck("发出具名 EXPORT_ERROR", 1, a0_console_has("[spm] EXPORT_ERROR: line_overflow need="));
    snprintf(want, sizeof want, "need=%d cap=%d", SPM_TEXT_CAP + 9, SPM_TEXT_CAP);
    ck("错误行如实报告应写字节数与容量", 1, a0_console_has(want));
    ck("未发出超长内容的任何片段", 0,
       a0_console_has("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
                      "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"));
    ck("错误行自身没有被 127 截断", 0, trunc);
    ck("错误行不带 NUL", 0, nul);
    case_end();
}

#if SPM_TEXT_CAP >= 512
/* TMA27（正常容量构建）：头行 490 B 与每条数据行必须逐字节完整，dump 返回 0。 */
static void tma27_export_records_complete(void)
{
    const char *line, *b;
    int len = 0, trunc = 0, nul = 0, rc, L, i;
    int rows = 0, rows_cols47 = 0, rows_nul0 = 0, rows_complete1 = 0, rows_within_cap = 0;
    rt_uint32_t got;
    sp_proof_t p;

    case_begin("TMA27", "导出通道: CSV 头行 490 B 与逐条数据行 47 列完整, dump 返回 0");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();
    quiesce_for_init();
    ck("init 前置", 0, cmd("init", NULL));
    ck("start 前置", 0, cmd("start", "trip_fast"));
    for (i = 0; i < 3; i++) sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    ck("stop 前置", 0, cmd("stop", NULL));
    got = spm_records_used();
    ck("前置恰好 3 条记录", 3, (long) got);

    a0_console_reset();
    a0_console_capture(1);
    rc = spm_dump_records();
    a0_console_capture(0);
    b = a0_console_bytes(&len, &trunc, &nul);
    (void) b;

    ck("dump 返回 0", 0, rc);
    ck("没有 127 截断行", 0, trunc);
    ck("上线字节不含 NUL", 0, nul);
    ck("没有 EXPORT_ERROR", 0, a0_console_has("EXPORT_ERROR"));

    line = a0_cap_find_line("schema,gen,slot,", &L);
    ck("头行存在", 1, line ? 1 : 0);
    /* 捕获到的"行"不含结尾 LF：行体 489 B + LF = 上线 490 B(与本轮测量界限一致)。 */
    ck("头行行体字节数 = 489", 489, L);
    ck("头行上线字节数(含 LF) = 490", 490, L + 1);
    ck("头行列数 = 47", 47, a0_cap_line_cols(line, L));
    ck("头行尾列 dropped_at_publish,complete 在剪断点之后", 1,
       (a0_cap_line_pos(line, L, "dropped_at_publish,complete") > 127) ? 1 : 0);
    ck("头行不含 NUL", 0, a0_cap_line_nul(line, L));

    for (line = a0_cap_buf; *line; )
    {
        const char *e = strchr(line, '\n');
        L = e ? (int) (e - line) : (int) strlen(line);

        if (L > 0 && line[0] == '2' && line[1] == ',')
        {
            rows++;
            if (a0_cap_line_cols(line, L) == 47)      rows_cols47++;
            if (a0_cap_line_nul(line, L) == 0)        rows_nul0++;
            if (a0_cap_line_ends(line, L, ",1"))      rows_complete1++;
            if (L < SPM_TEXT_CAP)                     rows_within_cap++;
        }
        if (!e) break;
        line = e + 1;
    }
    ck("数据行数 = 3", 3, rows);
    ck("每条数据行都是 47 列", 3, rows_cols47);
    ck("每条数据行都不带 NUL", 3, rows_nul0);
    ck("每条数据行末列 complete=1", 3, rows_complete1);
    ck("每条数据行都在容量界限内(没有溢出)", 3, rows_within_cap);

    quiesce_for_init();
    case_end();
}
#else
/* TMA27（精简容量构建，-DSPM_TEXT_CAP<512）：490 B 头行必然超限 =>
 * 具名 EXPORT_ERROR + dump 返回 -3 + 一条半头行/半记录都不许上线。 */
static void tma27_export_records_complete(void)
{
    const char *b;
    int len = 0, trunc = 0, nul = 0, rc;

    case_begin("TMA27", "导出通道(精简容量): 头行超限 => 具名错误 + dump=-3 + 无半条记录");
    a0_console_reset();
    a0_console_capture(1);
    rc = spm_dump_records();
    a0_console_capture(0);
    b = a0_console_bytes(&len, &trunc, &nul);
    (void) b;

    ck("容量不足时 dump 返回 -3", -3, rc);
    ck("发出具名 EXPORT_ERROR", 1, a0_console_has("[spm] EXPORT_ERROR: line_overflow"));
    ck("没有发出被剪断的半条头行", 0, a0_console_has("schema,gen,slot,entry,"));
    ck("没有发出任何 CSV 数据行", 0, a0_console_has(",trip_fast,"));
    ck("导出通道自身不产生 127 截断行", 0, trunc);
    ck("错误短行不带 NUL", 0, nul);
    case_end();
}
#endif

/* TMA24 修订后的分批顺序：用真实控制函数跑 1 条与 16 条两批，逐批数量一致且清 RAM 不清故障。 */
static void tma24_pack_batch_sequence(void)
{
    sp_proof_t p, pk;
    rt_uint32_t ep_start, ep_b1, code = 0u, events = 0u;
    rt_uint32_t i, b1, b2, complete = 0u, entry_ok = 0u, action_ok = 0u, valid = 0u;
    rt_bool_t pend_b1, pend_b2;

    case_begin("TMA24", "操作包分批顺序: 1 条与 16 条两批逐批一致, clear 只清测量 RAM");
    boot_once();
    port_ppb_reset(RT_TRUE, 7u, RT_TRUE);
    port_bus_reset();
    quiesce_for_init();
    ck("init", 0, cmd("init", NULL));
    ep_start = sp_epoch();

    /* ---- 批 1：start -> 1 次关闭 -> stop -> status -> dump -> 核对并保存 -> clear ---- */
    ck("批1 start", 0, cmd("start", "trip_fast"));
    sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    ck("批1 stop", 0, cmd("stop", NULL));
    ck("批1 status(确认无在途)", 0, cmd("status", NULL));
    ck("批1 无在途", 0, (long)spm_in_flight());
    ck("批1 dump 未被 BUSY 挡", 0, cmd("dump", NULL));
    b1 = spm_records_used();
    ck("批1 恰好 1 条", 1, (long)b1);
    ep_b1 = sp_epoch();
    pend_b1 = sp_pending_peek(&pk, &code, &events);
    ck("批1 clear_records", 0, cmd("clear_records", NULL));
    ck("clear 不改 Safety epoch", ep_b1, (long)sp_epoch());
    pend_b2 = sp_pending_peek(&pk, &code, &events);
    ck("clear 不改 pending 观测", 1, (pend_b1 == pend_b2) ? 1 : 0);
    ck("clear 后最后一笔 dump 只能是空批次", 0, (long)spm_records_used());

    /* ---- 批 2：start -> 16 次关闭 -> stop -> status -> dump -> 核对并保存 -> clear ---- */
    ck("批2 start(静止后才允许)", 0, cmd("start", "trip_fast"));
    for (i = 0u; i < 16u; i++) sp_trip_fast(FAULT_ESTOP, 0u, MOT_STOP_SRC_ESTOP, &p);
    ck("批2 stop", 0, cmd("stop", NULL));
    ck("批2 status", 0, cmd("status", NULL));
    ck("批2 dump 未被 BUSY 挡", 0, cmd("dump", NULL));
    b2 = spm_records_used();
    ck("批2 恰好 16 条(两批合计 17 次关闭)", 16, (long)b2);
    ck("两批合计 disable 动作 17 次", 17, port_pwm_disable_calls());
    ck("两批合计许可 LOW 17 次", 17, port_permit_low_calls());
    for (i = 0u; i < b2; i++)
    {
        const spm_rec_t *x = spm_record_at(i);

        if (!x) continue;
        if (x->complete)                            complete++;
        if (x->entry_id == SPM_ENTRY_TRIP_FAST)     entry_ok++;
        if (x->pwm_called_by_this_call && x->low_called_by_this_call) action_ok++;
        if (x->body_valid)                          valid++;
    }
    ck("批2 每条都发布", 16, (long)complete);
    ck("批2 每条 entry 都是 trip_fast", 16, (long)entry_ok);
    ck("批2 每条都留了本次真实动作位", 16, (long)action_ok);
    ck("批2 有效时长样本数 = 16(判据成立)", 16, (long)valid);
    ck("批2 无丢弃", 0, (long)spm_dropped());
    ck("批2 后 epoch 共前进 17", ep_start + 17u, (long)sp_epoch());
    quiesce_for_init();
    case_end();
}
#endif /* NS800_TARGET_MEASUREMENT_A0 */

/* ------------------------------------------------------------------ 驱动 */
int main(int argc, char **argv)
{
    const char *grp = (argc > 1) ? argv[1] : "all";

    setvbuf(stdout, NULL, _IONBF, 0);
    emit("A0CHAIN variant=%s group=%s\n",
         NS800_TARGET_MEASUREMENT_A0 ? "measure_on" : "switch_off", grp);
#if NS800_TARGET_MEASUREMENT_A0
    emit("A0LAYOUT host_sizeof_rec=%u host_sizeof_ctx=%u slots=%u host_bss_delta_max=%u\n",
         (unsigned)sizeof(spm_rec_t), (unsigned)sizeof(spm_ctx_t), SPM_SLOTS,
         (unsigned)(sizeof(spm_rec_t) * SPM_SLOTS));
#endif

    if (strcmp(grp, "off") == 0) tma01_switch_off_contract();

#if NS800_TARGET_MEASUREMENT_A0
    if (strcmp(grp, "counter") == 0) {
        /* TMA03 先跑: 它需要在**进程刚起步、还没做任何能力检查**的状态下取 UNCHECKED。 */
        tma03_unchecked_vs_unavailable();
        tma02_counter_unavailable();
        tma04_valid_body_enclosing_independent();
        tma05_reload_in_body();
        tma06_wrap_cfg_guard();
        tma21_per_sample_gate_failure();
        tma22_init_capability_lifecycle();
        tma23_historical_flag_positive();
    }
    if (strcmp(grp, "contract") == 0) {
        tma07_two_independent_actions();
        tma08_stale_identity();
        tma09_off_attempt_matrix();
        tma10_proof_null();
    }
    if (strcmp(grp, "conc") == 0) {
        tma11_fault_in_publish_seam();
        tma12_two_threads_slot_generation();
        tma13_isr_and_nested_guard();
    }
    if (strcmp(grp, "batch") == 0) {
        tma14_batch_full();
        tma15_busy_control();
        tma16_dump_and_clear_bounds();
    }
    if (strcmp(grp, "cli") == 0) {
        tma17_cli_gates();
        tma19_command_name_binding();
        tma20_strict_arg_matrix();
    }
    if (strcmp(grp, "ledger") == 0) tma18_ledger_and_publish_order();
    if (strcmp(grp, "pack") == 0) tma24_pack_batch_sequence();
    if (strcmp(grp, "export") == 0)
    {
        tma25_export_lines_complete();
        tma26_export_overflow_named_error();
        tma27_export_records_complete();
    }
#endif

    emit("SUMMARY cases=%d checks=%d fails=%d\n", g_cases_done, g_checks_total, g_fail_total);
    emit("EXIT %s\n", (g_fail_total == 0) ? "OK" : "FAIL");
    return (g_fail_total == 0) ? 0 : 1;
}
