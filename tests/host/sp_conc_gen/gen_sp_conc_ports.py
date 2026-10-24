"""CP4 §9: 生成 tests/host/sp_conc_main.c

做法(任务书 §9 "复用既有 runner 守卫/台账, 只加 S2 业务入口与必要 shim"):
  1) 取 ps_main.c 的**端口层**(到 #include "stop_priority.c" 之前)作为基础;
  2) 把"单线程 IPC + 掩码位"整块换成**真 pthread** 模型: 真实阻塞的 mutex、
     全局独占的短临界区(等价于 PRIMASK 屏蔽期的不可抢占)、被屏蔽期间排队并在
     窗口结束后由同一线程交付的 ISR、可注入的取锁错误与 event 发送失败;
  3) 追加 C01..C20 的并发用例与逐 ID 台账。
生成物是**自包含的夹具源**, 随本轮一起提交(可审), 不依赖本脚本再次运行。
"""
import io
import os

# 路径按脚本自身位置推导: 本目录 = <仓库根>/tests/host/sp_conc_gen, 不写死任何机器路径。
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
import io
import re
import sys

PS = os.path.join(_ROOT, "tests", "host", "ps_main.c")
OUT = os.path.join(_ROOT, "tests", "host", "sp_conc_main.c")
SPLIT = '#include "stop_priority.c"'

prefix = io.open(PS, encoding="utf-8", newline="").read().replace("\r\n", "\n")
idx = prefix.index(SPLIT)
ports = prefix[:idx]

# ---------------- 端口替换: 真线程 IPC / 窗口 / ISR 队列 ----------------
CRLF = chr(13) + chr(10)
LF = chr(10)

REPL = []

REPL.append((
    """rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{ (void)flags; if (m) { m->name = name; m->inited = RT_TRUE; } return RT_EOK; }
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t t)
{ (void)m; (void)t; c_mutex_take++; ps_win_note("rt_mutex_take"); return RT_EOK; }
rt_err_t rt_mutex_release(struct rt_mutex *m)
{ (void)m; c_mutex_release++; return RT_EOK; }
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }""",
    """/* ---------- S2: 真 pthread 互斥量(生产代码等的就是这把锁, 不模拟成功) ----------
 * rt_mutex_init 把 (name -> 对象指针) 登记下来, 于是"另一线程真实持 ss_lock/mot_lock"
 * 用的是**同一个** struct rt_mutex 实例, 而不是同名副本。
 * ⚠ 在已屏蔽窗口内 take 仍然计数为禁止项(ps_win_note), 但这里**真的**去阻塞 ——
 *   那样"窗口内不许取锁"一旦破功就会死锁, 由 runner 的墙钟 timeout 抓到。 */
#define S2_MAX_MTX 8
/* RT-Thread 的 rt_mutex 是**按持有线程递归计数**的(同线程重入合法) —— 主机模型必须
 * 一致, 否则"持 ss_lock 时又触发一次要 ss_lock 的慢整理"会自我死锁(2026-10-24 在
 * C12/C14 上实测到)。CP5 §4: 递归与"非持有者不得偷放"现在**全部**由 PTHREAD_MUTEX_
 * RECURSIVE 本体承担 —— 原来另存一份 owner/owned/count 元数据并在不持任何保护的情况下
 * 读/写另一线程写的字段, 既是数据竞争, 也可能与真实持有关系脱节。元数据整块删除。 */
typedef struct {
    struct rt_mutex *key;
    const char      *name;
    pthread_mutex_t  real;
} s2_slot;
static s2_slot s2_mtx[S2_MAX_MTX];
static int s2_mtx_n;
static s2_slot *s2_mtx_slot_of(struct rt_mutex *m);
static pthread_mutex_t s2_mtx_tab = PTHREAD_MUTEX_INITIALIZER;
static const char *s2_lock_fail_name;          /* 注入: 该名字的锁一律返回 -RT_EIO */
static int s2_lock_fail_hits;

static pthread_mutex_t *s2_mtx_bind(struct rt_mutex *m, const char *name)
{
    int i;
    pthread_mutex_t *out = RT_NULL;

    pthread_mutex_lock(&s2_mtx_tab);
    for (i = 0; i < s2_mtx_n; ++i)
        if (s2_mtx[i].key == m) { out = &s2_mtx[i].real; break; }
    if (out == RT_NULL && s2_mtx_n < S2_MAX_MTX && m != RT_NULL)
    {
        pthread_mutexattr_t attr;

        s2_mtx[s2_mtx_n].key  = m;
        s2_mtx[s2_mtx_n].name = name;
        pthread_mutexattr_init(&attr);
        pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
        pthread_mutex_init(&s2_mtx[s2_mtx_n].real, &attr);
        pthread_mutexattr_destroy(&attr);
        out = &s2_mtx[s2_mtx_n].real;
        s2_mtx_n++;
    }
    pthread_mutex_unlock(&s2_mtx_tab);
    return out;
}

struct rt_mutex *s2_mtx_by_name(const char *name);   /* 夹具侧: 按名字找到生产那把锁 */

/* 竞争观测: "有线程在门外等某把业务锁"的次数与名字 —— 用例据此证明慢整理确实
 * 排在锁之后, 而不是看起来像。S2_SEQ_CONTEND 是序列位里的固定一位。 */
#define S2_SEQ_CONTEND 20
void s2_seq_set(int bit);                      /* 定义在后面"序列点"小节 */
int  s2_contend_hits;
const char *s2_contend_name = "(none)";
void s2_contend_clear(void) { s2_contend_hits = 0; s2_contend_name = "(none)"; }
int  s2_seq_contend_seen(void) { return s2_contend_hits; }

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{
    (void)flags;
    if (m) { m->name = name; m->inited = RT_TRUE; s2_mtx_bind(m, name); }
    return RT_EOK;
}

rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t t)
{
    pthread_mutex_t *real;
    int e;

    c_mutex_take++;
    ps_win_note("rt_mutex_take");
    if (s2_lock_fail_name && m != RT_NULL && m->name != RT_NULL &&
        strcmp(m->name, s2_lock_fail_name) == 0)
    {
        s2_lock_fail_hits++;
        return -RT_EIO;
    }
    real = s2_mtx_bind(m, m ? m->name : RT_NULL);
    if (real == RT_NULL) return -RT_EINVAL;
    if (t == RT_WAITING_NO)
    {
        /* 递归 mutex: 同线程重入 trylock 直接成功 => 不会被误记成"门外竞争"。 */
        return (pthread_mutex_trylock(real) == 0) ? RT_EOK : -RT_EBUSY;
    }
    /* 竞争观测点: trylock 拿不到 = **确实有别的线程持有**这把业务锁, 此时才记一次
     * "门外等待"并置序列位, 然后真阻塞。之前把 trylock 探测去掉(一律记)会让
     * holder 把自己的取锁当成竞争者, 观察时机被提前(2026-10-24 实测)。 */
    e = pthread_mutex_trylock(real);
    if (e != 0)
    {
        /* 竞争观测点: trylock 失败 = **确实有别的线程持有**这把业务锁(同线程重入在上面
         * 已经成功了), 这时才记一次"门外等待"并置序列位, 然后真阻塞到拿到为止。 */
        s2_contend_hits++;
        s2_contend_name = (m->name ? m->name : "?");
        s2_seq_set(S2_SEQ_CONTEND);
        e = pthread_mutex_lock(real);
    }
    return (e == 0) ? RT_EOK : RT_ERROR;
}

rt_err_t rt_mutex_release(struct rt_mutex *m)
{
    pthread_mutex_t *real = s2_mtx_bind(m, m ? m->name : RT_NULL);

    c_mutex_release++;
    if (real == RT_NULL) return -RT_EINVAL;
    /* 递归 mutex 自己保证: 同线程多层只放到最后一层; 非持有者 unlock 返回 EPERM
     * => 与产品语义一致的"不偷放", 不再需要(也不能有)跨线程裸读的元数据。 */
    return pthread_mutex_unlock(real) == 0 ? RT_EOK : -RT_EPERM;
}

static s2_slot *s2_mtx_slot_of(struct rt_mutex *m)
{
    int i;
    s2_slot *out = RT_NULL;

    pthread_mutex_lock(&s2_mtx_tab);
    for (i = 0; i < s2_mtx_n; ++i)
        if (s2_mtx[i].key == m) { out = &s2_mtx[i]; break; }
    pthread_mutex_unlock(&s2_mtx_tab);
    return out;
}
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }

struct rt_mutex *s2_mtx_by_name(const char *name)
{
    int i;
    struct rt_mutex *out = RT_NULL;

    pthread_mutex_lock(&s2_mtx_tab);
    for (i = 0; i < s2_mtx_n; ++i)
        if (s2_mtx[i].name && strcmp(s2_mtx[i].name, name) == 0) { out = s2_mtx[i].key; break; }
    pthread_mutex_unlock(&s2_mtx_tab);
    return out;
}

void s2_set_lock_fail(const char *name) { s2_lock_fail_name = name; }
int  s2_lock_fail_count(void)           { return s2_lock_fail_hits; }
void s2_lock_fail_clear(void)           { s2_lock_fail_name = RT_NULL; s2_lock_fail_hits = 0; }"""))

REPL.append((
    """rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event)
{ (void)e; c_event_send++; c_event_bits |= event; c_event_last = event; return RT_EOK; }""",
    """int s2_event_send_fail = 0;                  /* 注入: event 发送一律失败 */
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event)
{
    (void)e;
    c_event_send++; c_event_bits |= event; c_event_last = event;
    if (s2_event_send_fail) return -RT_ERROR;      /* ISR 里"发送失败"那条路径 */
    return RT_EOK;
}"""))

# 掩码模型 -> 真窗口(全局独占) + ISR 队列
REPL.append((
    """static volatile int s_intr_masked;      /* 1 = 已屏蔽(PRIMASK 语义) */
static int          s_intr_depth;
static int          s_win_violations;   /* 窗口内禁止项命中次数 */

rt_base_t rt_hw_interrupt_disable(void)
{
    rt_base_t old = (rt_base_t)s_intr_masked;
    s_intr_masked = 1;
    s_intr_depth++;
    return old;
}

void rt_hw_interrupt_enable(rt_base_t level)
{
    s_intr_masked = (int)level;
    if (s_intr_depth > 0) s_intr_depth--;
}""",
    """/* CP4 §9 关键修正: 掩码与嵌套深度必须是**线程本地**的 —— 多线程共用一个全局深度会让
 * "谁持有全局独占锁"记账错乱, 表现为解锁不属于自己的互斥量并死锁(2026-10-24 实测)。
 * s2_win_live 仍是全局事实(只有持锁线程能改它, 且都在锁内读写)。 */
static __thread volatile int s_intr_masked;   /* 1 = 本线程当前处于屏蔽窗口 */
static __thread int          s_intr_depth;
static int          s_win_violations;   /* 窗口内禁止项命中次数 */

/* ---------- S2 的核心: 窗口 = 跨线程真独占 ----------
 * 目标上 rt_hw_interrupt_disable() 屏蔽 PRIMASK 之后, 单核既不会被别的线程抢占,
 * 也不会响应可屏蔽中断 => 窗口是原子的。主机上用一把**全局互斥量**复现同一件事:
 *   谁开了窗口, 别的线程(含夹具里的"另一条业务线程"和 ISR 交付线程)一律进不来;
 *   嵌套沿用"保存/恢复进入前值"的 PRIMASK 语义(线程本地深度)。
 * ⚠ 副作用: 生产代码若在窗口里取业务锁, 本模型会真的死锁 —— 这正是"窗口内不许
 *   取锁"这条纪律的执行方式, 由 runner 的墙钟 timeout 抓到(不是静默通过)。 */
static pthread_mutex_t s2_atomic = PTHREAD_MUTEX_INITIALIZER;
static int s2_win_live;                        /* 有线程正在窗口内(受 s2_atomic 保护) */

/* ---------- S2: 被屏蔽期间排队、窗口结束后交付的 ISR 模型 ----------
 * 目标事实: PRIMASK=1 时可屏蔽中断只能挂在中断线上, 退出临界区后立即响应。
 * 这里把 s2_isr_raise() 做成同一形状: 窗口开着 => 只入队(记一次"已请求未交付");
 * 最外层 rt_hw_interrupt_enable() => 由**同一个线程**同步交付, 交付期间
 * rt_interrupt_get_nest() 报告非 0(等价 ISR 上下文)。 */
#define S2_ISR_QUEUE 8
static void (*s2_isr_fn[S2_ISR_QUEUE])(void *);
static void *s2_isr_arg[S2_ISR_QUEUE];
static int s2_isr_head, s2_isr_tail, s2_isr_queued, s2_isr_delivered, s2_isr_seq;

static void s2_dispatch_pending(void)
{
    while (s2_isr_head != s2_isr_tail)
    {
        void (*fn)(void *) = s2_isr_fn[s2_isr_head];
        void *arg          = s2_isr_arg[s2_isr_head];

        s2_isr_fn[s2_isr_head] = RT_NULL;
        s2_isr_arg[s2_isr_head] = RT_NULL;
        s2_isr_head = (s2_isr_head + 1) % S2_ISR_QUEUE;
        s2_tls_nest++;
        s2_isr_seq++;
        fn(arg);
        s2_tls_nest--;
        s2_isr_delivered++;
    }
}

rt_base_t rt_hw_interrupt_disable(void)
{
    rt_base_t old = (rt_base_t)s_intr_depth;

    if (s_intr_depth == 0)
    {
        pthread_mutex_lock(&s2_atomic);
        s2_win_live = 1;
    }
    s_intr_depth++;
    s_intr_masked = 1;
    return old;
}

void rt_hw_interrupt_enable(rt_base_t level)
{
    s_intr_depth = (int)level;
    if (s_intr_depth == 0)
    {
        s_intr_masked = 0;
        s2_dispatch_pending();               /* 窗口一结束就响应挂着的中断 */
        s2_win_live = 0;
        pthread_mutex_unlock(&s2_atomic);
    }
    else s_intr_masked = 1;
}

/* 夹具侧发起一次"中断请求"。窗口开着 => 只能排队; 否则当场交付。 */
void s2_isr_raise(void (*fn)(void *), void *arg)
{
    if (s2_win_live)
    {
        int next = (s2_isr_tail + 1) % S2_ISR_QUEUE;
        if (next == s2_isr_head) return;               /* 队列满: 如实丢弃(用例断言不会走到) */
        s2_isr_fn[s2_isr_tail]  = fn;
        s2_isr_arg[s2_isr_tail] = arg;
        s2_isr_tail = next;
        s2_isr_queued++;
        return;
    }
    s2_tls_nest++;
    s2_isr_seq++;
    fn(arg);
    s2_tls_nest--;
    s2_isr_delivered++;
}
int  s2_isr_queued_n(void)    { return s2_isr_queued; }
int  s2_isr_delivered_n(void) { return s2_isr_delivered; }
int  s2_isr_seq_n(void)       { return s2_isr_seq; }
void s2_isr_stats_clear(void)
{
    s2_isr_head = s2_isr_tail = s2_isr_queued = s2_isr_delivered = s2_isr_seq = 0;
    memset(s2_isr_fn, 0, sizeof(s2_isr_fn));
    memset(s2_isr_arg, 0, sizeof(s2_isr_arg));
}
int  s2_win_live_now(void)  { return s2_win_live; }"""))

# 上下文门: nest 由 ISR 交付/显式注入决定; rt_thread_self 在窗口内或 ISR 内为空
REPL.append((
    """static int s_isr_nest;
void ps_isr_set(int v) { s_isr_nest = v; }
rt_bool_t rt_interrupt_get_nest(void) { return s_isr_nest ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void)
{
    return (s_intr_masked || s_isr_nest) ? RT_NULL : &c_thread_store[0];
}""",
    """void ps_isr_set(int v) { s_isr_nest = v; }
rt_bool_t rt_interrupt_get_nest(void) { return (s2_tls_nest || s_isr_nest) ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void)
{
    /* 窗口内没有可等待的线程上下文; 交付 ISR 时同样按"中断上下文"处理。 */
    return (s_intr_masked || s2_tls_nest || s_isr_nest) ? RT_NULL : &c_thread_store[0];
}"""))

# 控制台计数: ISR/窗口内打印必须为 0(两个独立计数器)
REPL.append((
    """    ps_win_note("rt_kprintf");
    va_list ap;""",
    """    ps_win_note("rt_kprintf");
    if (s2_tls_nest || s_isr_nest) s2_logs_in_isr++;
    va_list ap;"""))
REPL.append((
    """static void ps_cap_clear(void) { ps_cap_len = 0; ps_cap[0] = '\\0'; }""",
    """static void ps_cap_clear(void) { ps_cap_len = 0; ps_cap[0] = '\\0'; }
int s2_logs_in_isr;                     /* K5: ISR/中断上下文里的打印次数(必须恒 0) */

/* ---- 共享状态与前置声明(定义分散在本文件后面各小节) ---- */
static __thread int s2_tls_nest;        /* >0 = 正在 ISR 交付上下文 */
static int s_isr_nest;                  /* 显式注入"调用点本身在 ISR 里" */
#define S2_SEQ_CONTEND 20               /* 固定序列位: 有线程在门外等业务锁 */
void s2_seq_set(int bit);
static void s2_run_hook(const char *where);"""))

# 本文件可能被 git core.autocrlf 以 CRLF 检出, 而嵌入锚点是与**已归一化为 LF** 的目标文本比对:
# 先把成对字符串自己也归一化, 生成结果就与检出行尾无关(本机实测: 未归一时 CRLF 检出会 ANCHOR MISSING)。
for raw_old, raw_new in REPL:
    old = raw_old.replace(CRLF, LF)
    new = raw_new.replace(CRLF, LF)
    if ports.count(old) != 1:
        # 锚点必须恰好命中一次: 静默少替换会生成一个内容缺失的夹具
        # (本机实测: 缺这道守卫时端口层少了 7835 字节, 却仍然'构建通过')。
        sys.exit(
            'ANCHOR MISSING in ports: '
            + old[:60].replace(CR, '?').replace(LF, '|')
            + '  count=%d' % ports.count(old))
    ports = ports.replace(old, new)

# 端口层需要 pthread + 额外的 include 头
ports = ports.replace('#include <stdarg.h>',
                      '#include <stdarg.h>\n#include <pthread.h>\n#include <unistd.h>')

io.open(OUT, "w", encoding="utf-8", newline="\r\n").write(ports)
print("ports written, bytes=", len(ports))
