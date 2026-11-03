/*
 * ps_main.c - 关断结果可信链 (phase8-stop-result-s1, 2026-10-23)
 *
 * 真编译并执行**生产的**三个模块(任务书 §6 要求的"真实路径"):
 *     applications/step_pwm.c     STEP 诊断输出 + 唯一的 checked 关断路径
 *     applications/motor.c        斜坡/运行守卫/arm/disarm/急停 + 关断结果记账
 *     applications/safety_state.c 统一停机 + 故障锁存 + 人工恢复清除
 * 其余依赖(RT 原语、安全 GPIO、安全线程、TMC、传感器、存储、黑匣子)是本文件里的
 * **显式可注入假服务**。与另外三条链并列, 不复用它们的夹具。
 *
 * 桩的三条关键纪律(任务书 §6):
 *   1. PWM 每个 API 的返回码可独立注入, 并且**独立**保存"模拟输出是否仍在运行":
 *      rt_pwm_disable 失败时桩保留输出运行 —— 桩绝不替固件把输出清掉。
 *      motor.c 与 step_pwm.c 拿到同一个 epwm1/ch0 句柄, 所以这个 running 标志就是
 *      那条物理通道的单一真相, 能照出"两路都想关但有一路没关掉"。
 *   2. 许可(ENABLE)写结果、许可脚实测电平、ENN 反馈判定是**三个独立注入量**
 *      (ps_set_enable_write_rc / ps_sim_set_pad_level / ps_set_fb*),
 *      不用同一个布尔同时代表所有"成功"。
 *   3. 前置"已关断"一律由生产路径自己拿到(真调 force_stop/disarm 并让桩返回成功),
 *      不直接改写私有状态伪造成功过门。锁存的解除只走 fault_reset() 的人工恢复。
 *
 * 判定纪律: 只用生产 API 返回值/状态 + ps_sim_* 计数。两处例外是任务书明文要求的
 * "输出文本本身就是被测行为": PS05 的 step_status 不得写 "no(安全)"、
 * PS06 的 force_shutdown 不得写 "STEP stopped"。除此之外不做全局文本搜索。
 *
 * 本链**单线程、确定性**: rt_thread_create 只计次数、不起线程; 斜坡的一拍由本文件直接
 * 调用生产 motor_ramp_tick() 单步执行(等价性论证见 motor.c 的 S1 注释)。
 * 因此本链不模拟调度竞争、不测停机时延 —— 那是 S2, 本轮不声称已验证。
 *
 * 变体(runner 用 -D 传入, 不改生产配置):
 *   target    四道硬件宏全部 RT_FALSE = 目标发布配置
 *   host_ovr  -DPS_GATE_HOST_OVERRIDE + 三宏 RT_TRUE(仅主机, 用于走通 arm/start/斜坡)
 *
 * 退出码: 0=全部 PASS; 1=检出业务失败(有用例 FAIL); 2=夹具前置不自洽(不该发生)
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <pthread.h>
#include <unistd.h>
#include <errno.h>
#include <time.h>

#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "safety_state.h"
#include "safety_thread.h"
#include "stop_result.h"
#include "step_pwm.h"
#include "motor.h"
#include "tmc2209.h"
#include "adxl345.h"
#include "current_adc.h"
#include "ns_flash.h"
#include "blackbox.h"

/* ===================================================================== */
/* rt_kprintf 重定向: 照常打印 + 落入捕获缓冲(只给两条"输出即行为"的用例)  */
/* ===================================================================== */
#undef rt_kprintf
#define rt_kprintf ps_kprintf

#define PS_CAP_SIZE 8192
static char ps_cap[PS_CAP_SIZE];
static int  ps_cap_len;

static void ps_cap_clear(void) { ps_cap_len = 0; ps_cap[0] = '\0'; }
int s2_logs_in_isr;                     /* K5: ISR/中断上下文里的打印次数(必须恒 0) */

/* ---- 共享状态与前置声明(定义分散在本文件后面各小节) ---- */
static __thread int s2_tls_nest;        /* >0 = 正在 ISR 交付上下文 */
static int s_isr_nest;                  /* 显式注入"调用点本身在 ISR 里" */
#define S2_SEQ_CONTEND 20               /* 固定序列位: 有线程在门外等业务锁 */
void s2_seq_set(int bit);
static void s2_run_hook(const char *where);

/* ---- R7F1(2026-11-03 任务书 §4): 逐次 rt_kprintf 的控制台字节量具 ----------------
 * 复刻 kservice.c:410-416 那一刀(现场那三条超长打印正是被它切的):
 *   length = rt_vsnprintf(rt_log_buf, sizeof(rt_log_buf) - 1, fmt, args);
 *   if (length > RT_CONSOLEBUF_SIZE - 1) length = RT_CONSOLEBUF_SIZE - 1;
 *   _kputs(rt_log_buf, length);
 * rt_log_buf 是 RT_CONSOLEBUF_SIZE(=128, rtconfig.h:107) 的静态缓冲, 所以**内容**最多
 * 落 126 字节, 交给 _kputs 的第 127 个字节是缓冲区自己的 NUL 终止符, 末尾换行连同后半句
 * 一起丢 —— 2026-11-03 现场 S_S3/01_rst.boot.rx.bin 里三条打印的 NUL 前长度实测都是
 * 126 字节, 与这把尺逐字吻合。
 * 判据按任务书取 120 字节(留 7 字节余量给 CRLF 与设备侧换行处理)。
 * ⚠ 这是**夹具侧对内核输出路径的复刻**, 不是板上量到的字节; 板上原件在上一轮已归档。 */
#define BPT_CONSOLEBUF   128            /* rtconfig.h:107 的 RT_CONSOLEBUF_SIZE */
#define BPT_KERNEL_MAX   (BPT_CONSOLEBUF - 1)   /* 127: _kputs 拿到的字节数上限 */
#define BPT_CONTENT_MAX  (BPT_CONSOLEBUF - 2)   /* 126: 缓冲区装得下的内容字节 */
#define BPT_LIMIT        120            /* 任务书 §4 的门槛 */
#define BPT_MAX_LINES    128            /* 一整个 wire_and_boot + 一次停机的行数有余量 */
#define BPT_LINE_MAX     200

struct bpt_rec {
    int  would_be;        /* 这一次调用"本该有"的字节数(不含 NUL) */
    int  content;         /* 真能落进控制台缓冲的内容字节 = min(would_be, 126) */
    int  emitted;         /* 交给 _kputs 的字节数 = min(would_be, 127) */
    int  lost;            /* 被这一刀丢掉的字节数(末尾换行在里面) */
    int  nul_emitted;     /* 截断时缓冲区尾的 NUL 被当成数据一起发出 */
    int  tail_nl;         /* 落到控制台的最后一个字节是不是 '\n' */
    int  utf_split;       /* 截断处把一个多字节序列切成半截 */
    int  text_fit;        /* 夹具文本槽放得下(0 => 不许据 text 做判据) */
    char text[BPT_LINE_MAX];
};
static struct bpt_rec bpt_line[BPT_MAX_LINES];
static int bpt_n, bpt_dropped;          /* 窗口放不下 => 计数; 用例必须看到它恒 0 */

static void bpt_reset(void) { bpt_n = 0; bpt_dropped = 0; }

static void bpt_record(int would_be, const char *txt)
{
    struct bpt_rec *r;
    int i, content;

    if (bpt_n >= BPT_MAX_LINES) { bpt_dropped++; return; }
    r = &bpt_line[bpt_n++];
    content = (would_be > BPT_CONTENT_MAX) ? BPT_CONTENT_MAX : would_be;
    r->would_be    = would_be;
    r->content     = content;
    r->emitted     = (would_be > BPT_KERNEL_MAX) ? BPT_KERNEL_MAX : would_be;
    r->lost        = would_be - r->emitted;
    r->nul_emitted = (would_be > BPT_CONTENT_MAX) ? 1 : 0;
    r->tail_nl     = (content > 0 && txt[content - 1] == '\n') ? 1 : 0;
    r->utf_split   = 0;
    r->text_fit    = (would_be < BPT_LINE_MAX) ? 1 : 0;
    r->text[0]     = '\0';
    if (r->text_fit)
    {
        memcpy(r->text, txt, (size_t)would_be);
        r->text[would_be] = '\0';
    }
    if (content < would_be && content > 0)
    {
        /* 从截断点往回找这个序列的首字节, 看它能不能在落地的那 content 个字节里走完 */
        for (i = 1; i <= 4 && i <= content; ++i)
        {
            unsigned char c = (unsigned char)txt[content - i];

            if ((c & 0x80u) == 0u) break;                 /* ASCII 结尾: 边界干净 */
            if ((c & 0xC0u) != 0xC0u) continue;           /* 续字节: 继续往回 */
            {
                int need = ((c & 0xE0u) == 0xC0u) ? 2
                           : ((c & 0xF0u) == 0xE0u) ? 3 : 4;
                if (i < need) r->utf_split = 1;           /* 序列没走完整就被切 */
            }
            break;
        }
    }
}

/* 在**本窗口**里按整行内容找一条记录。每个用例都先 bpt_reset(), 窗口就是这一次触发
 * 打出来的那几行, 所以按内容找不会和别的行混。找不到返回 -1。 */
static int bpt_find(const char *needle)
{
    int i;

    for (i = 0; i < bpt_n; ++i)
        if (bpt_line[i].text_fit && strstr(bpt_line[i].text, needle) != RT_NULL)
            return i;
    return -1;
}
/* 窗口里的每一条(不只是被点名的那一条)都不许超门槛; 顺带交回最宽的那条的长度。
 * bpt_dropped!=0 表示窗口装不下 => 样本不全, 这条判据直接不成立(不许静默丢)。 */
static int bpt_all_within_limit(int *worst_out)
{
    int i, worst = 0, ok = 1;

    for (i = 0; i < bpt_n; ++i)
    {
        if (bpt_line[i].would_be > worst) worst = bpt_line[i].would_be;
        if (bpt_line[i].would_be > BPT_LIMIT) ok = 0;
    }
    if (worst_out) *worst_out = worst;
    return ok && bpt_dropped == 0 && bpt_n > 0;
}

/* 台账是**按行**解析的, 所以把被点名行的原文放进 detail 之前必须把其中的换行换成 '@'
 * (否则一条 CASE 会被劈成两行, 判据反而看不见真正的失败项)。4 个轮换缓冲: 同一次
 * fm 里最多用两三个, 不会自相覆盖。 */
static const char *bpt_flat(int idx)
{
    static char out[4][BPT_LINE_MAX];
    static int slot;
    char *p;
    int i;

    p = out[slot++ & 3];
    if (idx < 0 || idx >= bpt_n || !bpt_line[idx].text_fit) { p[0] = '\0'; return "-"; }
    for (i = 0; i < BPT_LINE_MAX - 1 && bpt_line[idx].text[i] != '\0'; ++i)
        p[i] = (bpt_line[idx].text[i] == '\n') ? '@' : bpt_line[idx].text[i];
    p[i] = '\0';
    return p;
}

static void ps_kprintf(const char *fmt, ...)
{
    /* 控制台在目标上是逐字符忙等(每字符 ≤BSP_NS800_UART_TX_TIMEOUT=6000ms), 属于
     * 短窗口内的禁止项; 这里只**观察**(掩码为 1 才计数), 不改任何行为。 */
    ps_win_note("rt_kprintf");
    if (s2_tls_nest || s_isr_nest) s2_logs_in_isr++;
    va_list ap;
    char tmp[1024];
    int would;
    int n;

    va_start(ap, fmt);
    would = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    fputs(tmp, stdout);
    n = (int)strlen(tmp);
    /* 夹具自己的 tmp 只有 1024 B: 真超到那儿就按哨兵值记(text_fit 会判 0, 用例不得
     * 拿被夹断的文本做判据), 同时这个长度本身已经远超门槛, 用例必然判红。 */
    if (would < 0 || would >= (int)sizeof(tmp)) would = (int)sizeof(tmp) - 1;
    bpt_record(would, tmp);
    if (ps_cap_len + n >= PS_CAP_SIZE)
    {
        int keep = PS_CAP_SIZE / 2;
        memmove(ps_cap, ps_cap + (ps_cap_len - keep), (size_t)keep);
        ps_cap_len = keep;
    }
    memcpy(ps_cap + ps_cap_len, tmp, (size_t)n);
    ps_cap_len += n;
    ps_cap[ps_cap_len] = '\0';
}

static int ps_cap_has(const char *needle) { return strstr(ps_cap, needle) != RT_NULL; }

/* ===================================================================== */
/* RT 原语(单线程夹具: 不起线程、不阻塞)                                  */
/* ===================================================================== */
static int c_mutex_take, c_mutex_release, c_event_send, c_thread_create, c_thread_startup;
static rt_uint32_t c_event_bits, c_event_last;
static struct rt_thread c_thread_store[4];

/* ---------- S2: 真 pthread 互斥量(生产代码等的就是这把锁, 不模拟成功) ----------
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

/* CP6 回归修正: "确实有线程在门外等"必须是一次**事件会合**, 不能是主线程按
 * usleep(200) 的粒度自旋来猜。本机实测(2026-10-24, 满载时): 那条 2500×usleep(200) 的
 * 猜测窗口会先走完, 于是同一条 C14b 偶发判 FAIL —— 观察时机缺陷, 不是产品行为变化。
 * 现在竞争点在 trylock 失败处 broadcast, 等待方在条件量上等; guard_ms 只作死锁守卫,
 * 到期原样返回"没等到", 不放宽任何判据。 */
static pthread_mutex_t s2_contend_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  s2_contend_c = PTHREAD_COND_INITIALIZER;

/* 等到 s2_contend_hits 真的越过 base(= 至少一次"别的线程持有时 trylock 失败"的门外等待)。
 * 返回 1=等到了; 0=墙钟守卫到期(调用方据此如实判失败)。 */
static int s2_contend_wait_above(int base, int guard_ms)
{
    struct timespec dl;
    int hit;

    clock_gettime(CLOCK_REALTIME, &dl);
    dl.tv_sec  += guard_ms / 1000;
    dl.tv_nsec += (long)(guard_ms % 1000) * 1000000L;
    if (dl.tv_nsec >= 1000000000L) { dl.tv_nsec -= 1000000000L; dl.tv_sec += 1; }

    pthread_mutex_lock(&s2_contend_m);
    while (s2_contend_hits == base)
    {
        if (pthread_cond_timedwait(&s2_contend_c, &s2_contend_m, &dl) == ETIMEDOUT) break;
    }
    hit = (s2_contend_hits > base) ? 1 : 0;
    pthread_mutex_unlock(&s2_contend_m);
    return hit;
}

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{
    (void)flags;
    if (m) { m->name = name; m->inited = RT_TRUE; s2_mtx_bind(m, name); }
    return RT_EOK;
}

/* mot_lock 在**本线程**的递归持有层数。POSIX 不暴露递归 mutex 的深度, 所以在夹具
 * 自己的取/放里按锁名记一层, 且只在成功时变动。C22f 用它回答"这次 SP_DIAG 预约到底
 * 发生在业务锁的持有期内还是持有期外" —— 修前的形状里, 诊断线程只是**碰巧**阻塞在
 * gate 内部的取锁上, 一旦没有人持锁, gate 一还锁预约就落在锁外; 光看"有没有别的线程
 * 抢进来"分不出这两种(2026-10-29 REV3 实测: 拆掉外层持锁后 C22b/C22c 仍全绿)。 */
#define S2_MOT_LOCK_NAME "mot"
static __thread int s2_mot_depth;

static void s2_mot_note(struct rt_mutex *m, rt_err_t rc, int is_release)
{
    if (rc != RT_EOK) return;
    if (m == RT_NULL || m->name == RT_NULL) return;
    if (strcmp(m->name, S2_MOT_LOCK_NAME) != 0) return;
    if (is_release) { if (s2_mot_depth > 0) s2_mot_depth--; }
    else            { s2_mot_depth++; }
}

rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t t)
{
    pthread_mutex_t *real;
    rt_err_t r;
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
        r = (pthread_mutex_trylock(real) == 0) ? RT_EOK : -RT_EBUSY;
        s2_mot_note(m, r, 0);
        return r;
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
        {
            /* 先放行"是否在门外等"的会合等待者, 再真的阻塞到拿到为止(不抱着
             * s2_contend_m 去等业务锁)。 */
            pthread_mutex_lock(&s2_contend_m);
            pthread_cond_broadcast(&s2_contend_c);
            pthread_mutex_unlock(&s2_contend_m);
        }
        e = pthread_mutex_lock(real);
    }
    r = (e == 0) ? RT_EOK : RT_ERROR;
    s2_mot_note(m, r, 0);
    return r;
}

rt_err_t rt_mutex_release(struct rt_mutex *m)
{
    pthread_mutex_t *real = s2_mtx_bind(m, m ? m->name : RT_NULL);
    rt_err_t r;

    c_mutex_release++;
    if (real == RT_NULL) return -RT_EINVAL;
    /* 递归 mutex 自己保证: 同线程多层只放到最后一层; 非持有者 unlock 返回 EPERM
     * => 与产品语义一致的"不偷放", 不再需要(也不能有)跨线程裸读的元数据。 */
    r = pthread_mutex_unlock(real) == 0 ? RT_EOK : -RT_EPERM;
    s2_mot_note(m, r, 1);
    return r;
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
void s2_lock_fail_clear(void)           { s2_lock_fail_name = RT_NULL; s2_lock_fail_hits = 0; }

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag)
{ (void)flag; if (e) { e->name = name; e->inited = RT_TRUE; } return RT_EOK; }
int s2_event_send_fail = 0;                  /* 注入: event 发送一律失败 */
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event)
{
    (void)e;
    c_event_send++; c_event_bits |= event; c_event_last = event;
    if (s2_event_send_fail) return -RT_ERROR;      /* ISR 里"发送失败"那条路径 */
    return RT_EOK;
}
rt_err_t rt_event_recv(struct rt_event *e, rt_uint32_t ev, rt_uint32_t fl,
                       rt_int32_t to, rt_uint32_t *out)
{ (void)e; (void)ev; (void)fl; (void)to; if (out) *out = 0; return -RT_ETIMEOUT; }
rt_err_t rt_event_detach(struct rt_event *e) { (void)e; return RT_EOK; }

rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick)
{
    (void)stack_size; (void)prio; (void)tick;
    if (c_thread_create >= (int)(sizeof(c_thread_store)/sizeof(c_thread_store[0])))
        return RT_NULL;                       /* 名额用完: 真失败也如实返回 */
    c_thread_store[c_thread_create].name  = name;
    c_thread_store[c_thread_create].entry = entry;
    c_thread_store[c_thread_create].param = param;
    c_thread_create++;                        /* 只计数: 本链不真的起线程 */
    return &c_thread_store[c_thread_create - 1];
}
rt_err_t rt_thread_startup(rt_thread_t t) { (void)t; c_thread_startup++; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t)  { (void)t; return RT_EOK; }

/* ===================================================================== */
/* 设备 / PWM / GPIO 仿真                                                  */
/* ===================================================================== */
#define PS_MAX_PINS 10

struct ps_pin
{
    const char *name;
    rt_int32_t  level;
    rt_int32_t  last_write;
    rt_base_t   last_mode;
    int         writes, reads, present;
};
static struct ps_pin pins[PS_MAX_PINS];
static int n_pins;

static struct rt_device_pwm g_pwm_dev;
static int       dev_present = 1;
static rt_err_t  rc_set = RT_EOK, rc_enable = RT_EOK, rc_disable = RT_EOK;
static int       calls_set, calls_enable, calls_disable;
static int       out_running;          /* 模拟输出是否仍在跑(桩的独立真相) */
static int       disable_seq_fail_left;/* R04: 前 N 次 disable 强制返回 disable_seq_rc */
static rt_err_t  disable_seq_rc = -RT_EIO;
static int       pwm_null_calls;         /* 生产代码拿 NULL 句柄调 PWM 的次数(必须恒 0) */

/* 许可写 / 许可脚实测 / ENN 反馈: 三个独立注入量 */
static rt_err_t  enable_write_rc = RT_EOK;
static rt_err_t  last_enable_ret = RT_EOK;   /* 桩留档: 上一次许可写的真实返回值 */
static rt_int32_t pad_level = PIN_LOW;
static int       pad_stuck;
static int       writes_low, writes_high;
static safety_fb_verdict_t fb_dis = SAFETY_FB_OK, fb_en = SAFETY_FB_OK;
static rt_err_t  fb_confirm_rc = RT_EOK;
static int       fb_confirm_calls, fb_now_calls;
static int       confirm_fail_after = -1;    /* 前 N 次成功, 之后失败(造"后置复核才变坏") */

static int       bb_triggers;
static int       protect_ready = 1, irq_attached = 1, polarity_ok = 1, gpio_ready = 1;
static rt_bool_t f_cfg_ok = RT_TRUE;
static tmc_drv_cfg_state_t f_cfg_state = TMC_DRV_CONFIGURED;
static subsys_health_t     f_tmc_health = SUBSYS_OK;
static rt_err_t  f_integrity = RT_EOK;
static int       cfg_txn_begins, cfg_txn_ends, fake_bus_reads;
static tmc_drv_config_t f_applied;

rt_device_t rt_device_find(const char *name)
{
    if (dev_present && rt_strcmp(name, EPWM_STEP_DEV_NAME) == 0)
        return (rt_device_t)&g_pwm_dev;
    return RT_NULL;
}

rt_err_t rt_pwm_set(struct rt_device_pwm *p, int ch, rt_uint32_t per, rt_uint32_t pul)
{ (void)ch; (void)per; (void)pul; calls_set++; if (p == RT_NULL) pwm_null_calls++; return rc_set; }

rt_err_t rt_pwm_enable(struct rt_device_pwm *p, int ch)
{
    (void)ch; calls_enable++; if (p == RT_NULL) pwm_null_calls++;
    if (rc_enable == RT_EOK) out_running = 1;     /* 只有 enable 成功才认为"输出在跑" */
    return rc_enable;
}

/* ⚠ 桩语义核心: disable 失败时**不动** out_running —— 桩不替固件把输出清掉。
 * "软件自称已停但模拟侧还在跑"会被 ps_sim_pwm_running() 直接照出来。
 * R04(2026-10-23 返修)追加"前 N 次失败、其后成功"的逐次序列(默认关闭), 用来
 * 照出"同一个停机动作里第二次关断覆盖第一次失败"的形状。 */
rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    rt_err_t e = rc_disable;

    (void)p; (void)ch; calls_disable++;
    /* 钩子点①: 真实危险写(时间基冻结)之前 —— 这个点**在屏蔽窗口之内**, 所以只允许挂
     * "请求一次中断"这种纯入队动作(C04), 不许挂业务/等待/日志(那会破坏模型本身)。 */
    s2_run_hook("rt_pwm_disable");
    if (p == RT_NULL) pwm_null_calls++;
    if (disable_seq_fail_left > 0)
    {
        disable_seq_fail_left--;
        e = disable_seq_rc;
    }
    if (e == RT_EOK) out_running = 0;
    return e;
}

static int pin_find(const char *name)
{
    int i;
    for (i = 0; i < n_pins; ++i)
        if (pins[i].present && rt_strcmp(pins[i].name, name) == 0) return i;
    return -1;
}

rt_base_t rt_pin_get(const char *name)
{
    int i = pin_find(name);
    return (i < 0) ? -RT_EINVAL : (rt_base_t)i;
}
void rt_pin_mode(rt_base_t pin, rt_base_t mode)
{ if (pin >= 0 && pin < (rt_base_t)n_pins) pins[pin].last_mode = mode; }
void rt_pin_write(rt_base_t pin, rt_base_t value)
{
    if (pin < 0 || pin >= (rt_base_t)n_pins) return;
    pins[pin].writes++;
    pins[pin].last_write = value;
    if (rt_strcmp(pins[pin].name, PIN_NAME_DRV_ENABLE) == 0 && !pad_stuck)
        pad_level = value;                      /* 焊盘跟着写走; stuck 时不跟 */
}
rt_base_t rt_pin_read(rt_base_t pin)
{
    if (pin < 0 || pin >= (rt_base_t)n_pins) return -RT_EINVAL;
    pins[pin].reads++;
    return pins[pin].level;
}

/* ---------- 测试侧控制与观测 ---------- */
void ps_sim_add_pin(const char *name, rt_int32_t lv)
{
    if (n_pins >= PS_MAX_PINS) return;
    pins[n_pins].name = name; pins[n_pins].level = lv;
    pins[n_pins].last_write = -1; pins[n_pins].last_mode = -999;
    pins[n_pins].writes = 0; pins[n_pins].reads = 0; pins[n_pins].present = 1;
    n_pins++;
}
void ps_sim_remove_pin(const char *name)
{ int i = pin_find(name); if (i >= 0) pins[i].present = 0; }
void ps_sim_set_level(const char *name, rt_int32_t lv)
{ int i = pin_find(name); if (i >= 0) pins[i].level = lv; }
rt_int32_t ps_sim_level(const char *name)
{ int i = pin_find(name); return (i < 0) ? -1 : pins[i].level; }

void ps_sim_dev_set_present(int on)  { dev_present = on; }
void ps_sim_pwm_set_rc(rt_err_t s, rt_err_t e, rt_err_t d)
{ rc_set = s; rc_enable = e; rc_disable = d; }
int  ps_sim_pwm_set_calls(void)      { return calls_set; }
int  ps_sim_pwm_enable_calls(void)   { return calls_enable; }
int  ps_sim_pwm_disable_calls(void)  { return calls_disable; }
int  ps_sim_pwm_running(void)        { return out_running; }
void ps_sim_pwm_force_running(int on){ out_running = on; }
/* R04 用: 接下来 n 次 rt_pwm_disable 返回 fail_rc, 之后的回到 ps_sim_pwm_set_rc 的值 */
void ps_sim_pwm_disable_seq(int n, rt_err_t fail_rc)
{ disable_seq_fail_left = n; disable_seq_rc = fail_rc; }
int ps_sim_pwm_disable_seq_left(void) { return disable_seq_fail_left; }
int ps_sim_pwm_null_calls(void)       { return pwm_null_calls; }

void ps_sim_set_pad_stuck(int on)     { pad_stuck = on; }
void ps_sim_set_pad_level(rt_int32_t lv) { pad_level = lv; }
int  ps_sim_pin_write_calls(const char *name)
{ int i = pin_find(name); return (i < 0) ? 0 : pins[i].writes; }
rt_int32_t ps_sim_last_write(const char *name)
{ int i = pin_find(name); return (i < 0) ? -1 : pins[i].last_write; }
int  ps_sim_pin_read_calls(const char *name)
{ int i = pin_find(name); return (i < 0) ? 0 : pins[i].reads; }
int  ps_sim_thread_create_calls(void)  { return c_thread_create; }
int  ps_sim_thread_startup_calls(void) { return c_thread_startup; }
int  ps_sim_mutex_takes(void)          { return c_mutex_take; }

void ps_set_enable_write_rc(rt_err_t rc) { enable_write_rc = rc; }
int  ps_enable_write_calls(int want_high) { return want_high ? writes_high : writes_low; }
void ps_set_fb(rt_uint8_t want_enabled, safety_fb_verdict_t v)
{ if (want_enabled) fb_en = v; else fb_dis = v; }
void ps_set_fb_confirm_rc(rt_err_t rc) { fb_confirm_rc = rc; }
void ps_set_confirm_fail_after(int n)  { confirm_fail_after = n; fb_confirm_calls = 0; }
int  ps_fb_confirm_calls(void)         { return fb_confirm_calls; }
int  ps_bb_triggers(void)              { return bb_triggers; }
int  ps_event_sends(void)              { return c_event_send; }
rt_uint32_t ps_event_bits(void)        { return c_event_bits; }
rt_uint32_t ps_event_last(void)        { return c_event_last; }
void ps_event_bits_clear(void)         { c_event_bits = 0; }
void ps_set_protect(int r, int i, int p, int g)
{ protect_ready = r; irq_attached = i; polarity_ok = p; gpio_ready = g; }
void ps_set_integrity(rt_err_t e) { f_integrity = e; }
void ps_set_cfg_ok(rt_bool_t ok, tmc_drv_cfg_state_t st) { f_cfg_ok = ok; f_cfg_state = st; }
void ps_set_tmc_health(subsys_health_t h) { f_tmc_health = h; }
int  ps_cfg_txn_begins(void) { return cfg_txn_begins; }
int  ps_cfg_txn_ends(void)   { return cfg_txn_ends; }
int  ps_fake_bus_reads(void) { return fake_bus_reads; }

void ps_sim_reset(void)
{
    n_pins = 0;
    dev_present = 1;
    rc_set = rc_enable = rc_disable = RT_EOK;
    calls_set = calls_enable = calls_disable = 0;
    out_running = 0;
    disable_seq_fail_left = 0; disable_seq_rc = -RT_EIO; pwm_null_calls = 0;
    enable_write_rc = RT_EOK; last_enable_ret = RT_EOK; pad_level = PIN_LOW; pad_stuck = 0;
    writes_low = writes_high = 0;
    fb_dis = fb_en = SAFETY_FB_OK; fb_confirm_rc = RT_EOK;
    fb_confirm_calls = fb_now_calls = 0; confirm_fail_after = -1;
    bb_triggers = 0;
    c_mutex_take = c_mutex_release = c_event_send = 0; c_event_bits = 0; c_event_last = 0;
    c_thread_create = c_thread_startup = 0;
    protect_ready = irq_attached = polarity_ok = gpio_ready = 1;
    f_cfg_ok = RT_TRUE; f_cfg_state = TMC_DRV_CONFIGURED; f_tmc_health = SUBSYS_OK;
    f_integrity = RT_EOK; cfg_txn_begins = cfg_txn_ends = fake_bus_reads = 0;
    ps_cap_clear();
}

/* ===================================================================== */
/* 假服务: safety_gpio.c / safety_thread.c / tmc2209.c / 传感器 / 存储 / 黑匣子 */
/* ===================================================================== */
rt_bool_t safety_gpio_ready(void) { return gpio_ready ? RT_TRUE : RT_FALSE; }
rt_err_t  safety_gpio_boot(void)  { return RT_EOK; }

rt_base_t safety_pin(const char *name)
{
    int i = pin_find(name);
    return (i < 0) ? (rt_base_t)-1 : (rt_base_t)i;
}

/* 许可脚"写 + 回读确认": 写结果与焊盘实测是两个独立量 */
rt_err_t safety_drv_enable_write(rt_uint8_t level)
{
    rt_base_t p = safety_pin(PIN_NAME_DRV_ENABLE);
    rt_err_t ret;

    if (level) writes_high++; else writes_low++;
    if (p < 0) return (last_enable_ret = -RT_EINVAL);
    rt_pin_write(p, level);
    if (enable_write_rc != RT_EOK) ret = enable_write_rc;
    else ret = (pad_level == (rt_int32_t)level) ? RT_EOK : -RT_EIO;
    last_enable_ret = ret;
    return ret;
}

/* 桩侧留档的**许可写**原始返回值(与快照里的 stop_rc 分列, 两者互不替代) */
rt_err_t ps_last_enable_write_rc(void) { return last_enable_ret; }

rt_bool_t safety_drv_enable_is_low(void)
{
    return (pad_level == PIN_LOW) ? RT_TRUE : RT_FALSE;
}

const char *safety_fb_verdict_name(safety_fb_verdict_t v)
{
    switch (v)
    {
    case SAFETY_FB_OK:                return "OK";
    case SAFETY_FB_TRANSITION:        return "TRANSITION";
    case SAFETY_FB_CUT_BY_HARDWARE:   return "CUT_BY_HARDWARE";
    case SAFETY_FB_UNEXPECTED_ENABLE: return "UNEXPECTED_ENABLE";
    case SAFETY_FB_PAD_MISMATCH:      return "PAD_MISMATCH";
    case SAFETY_FB_NO_SAMPLE:         return "NO_SAMPLE";
    default:                          return "?";
    }
}

safety_fb_verdict_t safety_fb_now(rt_uint8_t want_enabled)
{
    fb_now_calls++;
    return want_enabled ? fb_en : fb_dis;
}

rt_err_t safety_drv_feedback_confirm(rt_uint8_t want_enabled, safety_fb_verdict_t *out)
{
    safety_fb_verdict_t v = want_enabled ? fb_en : fb_dis;
    int seq = fb_confirm_calls++;

    /* 钩子点④: ENN 有界确认读 —— safety_run_selftest 的必需项真的经过这里(C15c 用)。
     * 选它而不是随便造一个"接缝": 自检在捕获 epoch0 之后、写 READY 之前必然做一次
     * 反馈确认, 这就是审查观察 old-selftest-ready-after-new-run 的真实交错位置。 */
    s2_run_hook("drv_feedback_confirm");

    if (confirm_fail_after >= 0 && seq >= confirm_fail_after)
    {
        if (out) *out = SAFETY_FB_NO_SAMPLE;
        return -RT_EIO;
    }
    if (out) *out = v;
    if (fb_confirm_rc != RT_EOK) return fb_confirm_rc;
    return (v == SAFETY_FB_OK) ? RT_EOK : -RT_EIO;
}

void safety_fb_snapshot(rt_int32_t *req, rt_int32_t *pad, rt_int32_t *fb, rt_bool_t *ok)
{
    if (req) *req = pad_level;
    if (pad) *pad = pad_level;
    if (fb)  *fb  = (fb_dis == SAFETY_FB_NO_SAMPLE) ? -1
               : (fb_dis == SAFETY_FB_OK ? PIN_HIGH : PIN_LOW);
    if (ok)  *ok  = (fb_dis != SAFETY_FB_NO_SAMPLE) ? RT_TRUE : RT_FALSE;
}

rt_bool_t safety_limit_inputs_enabled(void)
{
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
    return RT_TRUE;
#else
    return RT_FALSE;
#endif
}

/* 本链不编译 safety_gpio.c, 这个纯函数只为其头文件契约存在(不参与任何判定)。 */
safety_fb_verdict_t safety_fb_evaluate(rt_uint8_t want, rt_int32_t pad, rt_bool_t pad_ok,
                                       rt_int32_t fb, rt_bool_t fb_ok)
{
    (void)want; (void)pad; (void)pad_ok; (void)fb; (void)fb_ok;
    return SAFETY_FB_OK;
}

rt_err_t safety_thread_init(void)         { return RT_EOK; }
rt_err_t safety_irq_attach(void)          { return RT_EOK; }
rt_bool_t safety_irq_attached(void)       { return irq_attached ? RT_TRUE : RT_FALSE; }
rt_bool_t safety_polarity_validated(void) { return polarity_ok ? RT_TRUE : RT_FALSE; }
rt_bool_t safety_protection_ready(void)   { return protect_ready ? RT_TRUE : RT_FALSE; }
subsys_health_t safety_thread_get_health(void) { return SUBSYS_OK; }

rt_bool_t tmc2209_drive_config_ok_for_arm(void) { return f_cfg_ok; }
tmc_drv_cfg_state_t tmc2209_get_config_state(void) { return f_cfg_state; }
subsys_health_t     tmc2209_get_health(void)       { return f_tmc_health; }
rt_err_t  tmc2209_init(void) { return RT_EOK; }
/* R7F1(任务书 §2): 自检那一次读的失败分支现在要能被打到 —— 注入一个返回码,
 * 默认 RT_EOK => 所有旧用例的行为逐字不变(与 io 链 io_set_tmc_ident 同一做法)。 */
static rt_err_t f_tmc_read_rc = RT_EOK;
void s2_set_tmc_read_rc(rt_err_t rc) { f_tmc_read_rc = rc; }
rt_err_t  tmc2209_read_register(rt_uint8_t a, rt_uint8_t r, rt_uint32_t *v)
{
    (void)a; fake_bus_reads++;
    if (f_tmc_read_rc != RT_EOK) return f_tmc_read_rc;
    /* 916c34b 起生产启动自检要求 IOIN[31:24]==0x21; 假总线给出合法身份,
     * 其余寄存器仍是通用假值。身份不匹配由生产检查自身负责, 不在本夹具改写。 */
    if (v) *v = (r == 0x06u) ? 0x21000006u : (0x12340000u | r);
    return RT_EOK;
}
/* R7F1(任务书 §2): 生产自检那一次读现在会取"本次调用的归因"。本链是**假总线**,
 * 没有 flush/TX/RX 阶段可言, 所以不伪造阶段: 成功就报 ok, 失败只交出返回码,
 * reason 用夹具自己的哨兵值(不在产品 tmc_xfer_reason_t 取值域内), 由下面的名字表打成
 * "not-modelled(fake-bus)"。阶段级归因由真实传输层那条链证明
 * (tests/host/leaf_main.c 的 tmc_boot / read_obs 组)。 */
#define TMC_FAKE_REASON_NOT_MODELLED ((tmc_xfer_reason_t)0xFE)
const char *tmc2209_xfer_reason_name(tmc_xfer_reason_t r)
{
    if (r == TMC_XFER_OK) return "ok";
    if (r == TMC_FAKE_REASON_NOT_MODELLED) return "not-modelled(fake-bus)";
    return "?";
}
rt_err_t  tmc2209_read_register_stat(rt_uint8_t a, rt_uint8_t r, rt_uint32_t *v,
                                     tmc_xfer_stat_t *out)
{
    rt_err_t e = tmc2209_read_register(a, r, v);

    if (out)
    {
        rt_memset(out, 0, sizeof(*out));
        out->rc = e;
        out->reason = (e == RT_EOK) ? TMC_XFER_OK : TMC_FAKE_REASON_NOT_MODELLED;
    }
    return e;
}
rt_err_t  tmc2209_cfg_txn_begin(void) { cfg_txn_begins++; return RT_EOK; }
void      tmc2209_cfg_txn_end(void)   { cfg_txn_ends++; }
/* CP4 K3(§5.3): 生产侧现在有"带调用方原身份 + 本次结果摘要"的复核版本。
 * 桩保持同一事实源(f_integrity / fake_bus_reads), 并把摘要按"本次未保护"回填 ——
 * 保护与否由生产侧 sp 决定, 桩不冒充配置层做过保护。 */
rt_uint32_t f_cfg_view_gen = 1u;
rt_uint32_t tmc2209_config_view_generation(void) { return f_cfg_view_gen; }
void        ps_set_cfg_view_gen(rt_uint32_t g)   { f_cfg_view_gen = g; }

rt_err_t tmc2209_config_integrity_check_for(rt_uint32_t expected_epoch,
                                           tmc_op_result_t *out)
{
    fake_bus_reads++;
    /* 钩子点③: 前段完整性复核(UART 慢步骤, 必然在窗口外)。K2 的"预约前发生恢复"与
     * "旧自检比较完之后换代"都安排在这里。 */
    s2_run_hook("integrity_check");
    if (out != NULL)
    {
        memset(out, 0, sizeof(*out));
        out->prod      = RT_TRUE;
        out->op_epoch  = expected_epoch;
        out->rc        = f_integrity;
        out->protect   = SP_PROTECT_NONE;
        out->proof.valid = RT_FALSE;
    }
    return f_integrity;
}

rt_err_t  tmc2209_config_integrity_check(void)
{
    return tmc2209_config_integrity_check_for((rt_uint32_t)sp_epoch(), NULL);
}
rt_bool_t tmc2209_get_applied_config(rt_uint32_t *g, rt_uint32_t *c, tmc_drv_config_t *o)
{
    if (!f_cfg_ok) return RT_FALSE;
    if (g) *g = 0x00000C00u;
    if (c) *c = 0x10000053u;
    if (o) *o = f_applied;
    return RT_TRUE;
}
rt_uint32_t tmc2209_get_write_gate_mask(void)
{
    /* 生产 tmc_cfg_gate_prod() 不在本链(未编译 tmc2209.c): 这里给等价语义的假实现,
     * 用来证明宏门确实参与求值; 真实位的接入由 tmc2209.c 静态检查 + 旧链覆盖。 */
    rt_uint32_t bad = 0;
    if (!TMC_DRIVE_CONFIG_HW_VALIDATED)      bad |= TMC_WGATE_HW_NOT_VALIDATED;
    if (!safety_drv_enable_is_low())         bad |= TMC_WGATE_DRV_PAD_NOT_LOW;
    if (safety_fb_now(0u) != SAFETY_FB_OK)   bad |= TMC_WGATE_ENN_FB_NOT_DISABLED;
    if (safety_state_get() != SAFETY_READY)  bad |= TMC_WGATE_NOT_READY;
    return bad;
}
const char *tmc_drv_cfg_state_name(tmc_drv_cfg_state_t s) { (void)s; return "CFG"; }

rt_err_t adxl345_init(void) { return RT_EOK; }
subsys_health_t adxl345_get_health(void) { return SUBSYS_OK; }
rt_err_t current_adc_init(void) { return RT_EOK; }
subsys_health_t current_adc_get_health(void) { return SUBSYS_OK; }
rt_err_t ns_flash_init(void) { return RT_EOK; }
rt_err_t ns_flash_identify(rt_uint8_t id[3], rt_uint8_t sr[3])
{ id[0] = 0xEF; id[1] = 0x40; id[2] = 0x17; sr[0] = 0; return RT_EOK; }
void blackbox_trigger(rt_uint32_t code) { (void)code; bb_triggers++; }
subsys_health_t blackbox_get_health(void) { return SUBSYS_OK; }

/* ===================================================================== */
/* 真实的被测生产源码                                                      */
/* ===================================================================== */

/* ===================================================================== */
/* CP3 §9 第 1/2/5 项: stop_priority 与真实 drv_epwm.h 需要的可编程端口    */
/*   这一节全部是**夹具侧端口**, 不改生产代码; 每类观察量都是独立注入量:   */
/*     raw 写请求结果(enable_write_rc) / 实际焊盘电平(pad_level) /          */
/*     确认返回(safety_drv_permit_confirm_read 只看 pad_level) /            */
/*     ENN 反馈(fb_dis/fb_en/fb_confirm_rc) 四者互不代替。                  */
/*   中断掩码按"递归保存"的真实语义模拟: disable 返回**进入前**的值,        */
/*   enable 把掩码恢复成传入的那个值 —— 与 Cortex-M 的 PRIMASK 一致。       */
/*   窗口内的禁止项(取锁/睡眠/控制台/设备读写)由 ps_win_violations() 计数,  */
/*   任何非 0 都表示"生产代码在临界区里做了不许做的事"。                    */
/* ===================================================================== */
/* CP4 §9 关键修正: 掩码与嵌套深度必须是**线程本地**的 —— 多线程共用一个全局深度会让
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

/* ---------- M7R1-A(C22f): 预约时刻的"业务锁覆盖"探针 ----------
 * 生产里每一次预约都在**一个最外层窗口内**把 sp.owner 从 SP_NONE 改成自己的 owner
 * (sp_reserve -> sp_win -> sp_reserve_impl_held), 所以窗口的进出就是"预约当场"这个
 * 确定时刻, 不需要在产品里加任何会合点。最外层窗口进入时记下本线程 mot_lock 的持有
 * 层数与 owner; 最外层退出时若 owner 由 SP_NONE 变成别的值, 这一次就是预约, 把当时
 * 的层数登记下来。合同: 诊断借用路径的预约层数必须 >=1(在 mot_lock 持有期内)。 */
static __thread int s2_win_mot_before;
static __thread int s2_win_owner_before;
int s2_reserve_probe_seen;              /* 1 = 复位后确实发生过一次 owner 认领 */
int s2_reserve_probe_mot_depth;         /* 那次认领所在最外层窗口进入时的 mot_lock 层数 */
int s2_reserve_probe_owner;             /* 认领到的 owner */
static int  sp_owner_peek(void);        /* 定义在 stop_priority.c 纳入之后 */
static void s2_win_probe_exit(void);

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
        s2_win_mot_before   = s2_mot_depth;
        s2_win_owner_before = sp_owner_peek();
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
        s2_win_live = 0;
        pthread_mutex_unlock(&s2_atomic);
        s2_win_probe_exit();
        /* 窗口一结束就响应挂着的中断(与目标上"退出临界区后立即进 ISR"同序)。
         * ⚠ 必须在解锁**之后**交付: 被交付的 ISR 自己会再开一个短临界区。 */
        s2_dispatch_pending();
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
int  s2_win_live_now(void)  { return s2_win_live; }

/* ---------- 接缝钩子与线程序列点(CP4 §9: "用显式屏障安排接缝") ----------
 * s2_set_hook(fn, where): 下一次命中钩子点时, 在**同一个线程**里执行 fn 一次。
 * 钩子点只放在**窗口外**的生产慢步骤上(许可回读 / 前段完整性复核 / 真实危险写之前),
 * 因为目标上屏蔽窗口里不允许等待/日志/IPC; 不在已屏蔽窗口里强行执行 ISR。
 * s2_seq_set/wait: 线程之间的先后顺序用显式标志点安排; 墙钟预算**只防死锁**,
 * 不作为任何时延结论(任务书 §9 原文)。超时一律返回 -1, 调用方按前置不成立处理。
 */
static void (*s2_hook_once)(void);
static int s2_hook_fires;
static const char *s2_hook_last;

void s2_set_hook(void (*fn)(void), const char *where)
{
    s2_hook_once = fn;
    s2_hook_last = where;
}
int  s2_hook_fires_count(void) { return s2_hook_fires; }
const char *s2_hook_where(void) { return s2_hook_last ? s2_hook_last : "(none)"; }

static void s2_run_hook(const char *where)
{
    if (s2_hook_once)
    {
        void (*h)(void) = s2_hook_once;

        s2_hook_once = RT_NULL;
        s2_hook_fires++;
        s2_hook_last = where;
        h();
    }
}

#define S2_SEQ_BITS 24   /* 其余序列/竞争观测点见端口层与"序列点"小节 */
static pthread_mutex_t s2_seq_m = PTHREAD_MUTEX_INITIALIZER;
static unsigned int s2_seq_flags;

void s2_seq_reset(void)
{
    pthread_mutex_lock(&s2_seq_m);
    s2_seq_flags = 0u;
    pthread_mutex_unlock(&s2_seq_m);
}
static unsigned int s2_seq_get_bit(int bit)
{
    unsigned int v;

    pthread_mutex_lock(&s2_seq_m);
    v = (s2_seq_flags >> bit) & 1u;
    pthread_mutex_unlock(&s2_seq_m);
    return v;
}
void s2_seq_set(int bit)
{
    pthread_mutex_lock(&s2_seq_m);
    s2_seq_flags |= (1u << bit);
    pthread_mutex_unlock(&s2_seq_m);
}
/* 0=等到了; -1=墙钟超时(调用方必须按"前置不成立"记 FAIL, 不许当通过) */
int  s2_seq_wait(int bit, int ms)
{
    int us = 0;

    while (us < ms * 1000)
    {
        if (s2_seq_get_bit(bit)) return 0;
        usleep(1000);
        us += 1000;
    }
    return s2_seq_get_bit(bit) ? 0 : -1;
}

/* ---------- 真线程 spawn(夹具侧的"另一条业务线程") ---------- */
static pthread_t s2_thr[6];
static int s2_thr_n;

/* 线程入口桥接(任务书附录 A): 夹具的业务入口是 void (*)(void *), 而 pthread 要的是
 * void *(*)(void *) —— 原写法用强转把两者当同一个, POSIX 下是**类型不符**(审查环境
 * GCC 13.3 直接报 warning, 原 runner 按"构建不干净"判 INFRA-FAIL)。现在桥接函数调用
 * 原入口后返回 NULL, 不压警告、也不改任何业务判据。 */
struct s2_thread_call { void (*fn)(void *); void *arg; };
static struct s2_thread_call s2_calls[6];

static void *s2_thread_entry(void *p)
{
    struct s2_thread_call *c = (struct s2_thread_call *)p;

    c->fn(c->arg);
    return RT_NULL;
}

int s2_spawn(void (*fn)(void *), void *arg)
{
    if (s2_thr_n >= (int)(sizeof(s2_thr) / sizeof(s2_thr[0]))) return -1;
    s2_calls[s2_thr_n].fn  = fn;
    s2_calls[s2_thr_n].arg = arg;
    if (pthread_create(&s2_thr[s2_thr_n], RT_NULL,
                       s2_thread_entry, &s2_calls[s2_thr_n]) != 0)
        return -1;
    s2_thr_n++;
    return 0;
}
void s2_join_all(void)
{
    int i;

    for (i = 0; i < s2_thr_n; ++i) pthread_join(s2_thr[i], RT_NULL);
    s2_thr_n = 0;
}

int  ps_win_masked(void)      { return s_intr_masked; }
int  ps_win_depth(void)       { return s_intr_depth; }
int  ps_win_violations(void)  { return s_win_violations; }
static const char *s_win_last;
/* 只在"当前确实屏蔽着中断"时计数; 生产路径在门外调用同一个函数则不记。 */
void ps_win_note(const char *what)
{
    if (s_intr_masked) { s_win_violations++; s_win_last = what; }
}
const char *ps_win_last_item(void) { return s_win_last ? s_win_last : "(none)"; }
void ps_win_violations_clear(void) { s_win_violations = 0; s_win_last = RT_NULL; }

/* ---- CP4 K5(§7): 上下文门的两条输入在夹具里的等价物 ----
 * s_isr_nest 只由测试显式置位(模拟"首条故障来自 ISR"的情境); rt_thread_self()
 * 在屏蔽窗口内返回空 —— 与目标上"窗口里没有可等待上下文"同一事实。
 * ⚠ 这不是完整 ISR 队列/nest 模型(那属于 S2 并发链的 C03/C04)。 */
void ps_isr_set(int v) { s_isr_nest = v; }
rt_bool_t rt_interrupt_get_nest(void) { return (s2_tls_nest || s_isr_nest) ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void)
{
    /* 窗口内没有可等待的线程上下文; 交付 ISR 时同样按"中断上下文"处理。 */
    return (s_intr_masked || s2_tls_nest || s_isr_nest) ? RT_NULL : &c_thread_store[0];
}

/* ---- 许可脚(PC.23 的等价物): 裸写请求 vs 实际焊盘 vs 回读确认, 三者分列 ---- */
rt_base_t safety_drv_permit_pin(void) { return safety_pin(PIN_NAME_DRV_ENABLE); }

static rt_err_t permit_raw_write(rt_uint8_t level)
{
    rt_base_t p = safety_pin(PIN_NAME_DRV_ENABLE);

    if (p < 0) return -RT_ENOSYS;
    if (level) writes_high++; else writes_low++;
    rt_pin_write(p, level);                 /* 桩按 pin 表更新 pad_level */
    last_enable_ret = enable_write_rc;      /* 裸写请求的返回结果(独立注入) */
    return enable_write_rc;
}

rt_err_t safety_drv_permit_low_raw_held(void)  { return permit_raw_write(0u); }
rt_err_t safety_drv_permit_high_raw_held(void) { return permit_raw_write(1u); }

/* 确认 = 只看**实际焊盘电平**, 与"写请求是否被接受"互不代替 */
rt_err_t safety_drv_permit_confirm_read(rt_uint8_t level)
{
    /* 钩子点②: 带回读的许可确认(窗口外的慢步骤)。arm/start/人工恢复都走这里。 */
    s2_run_hook("permit_confirm_read");
    return (pad_level == (rt_int32_t)level) ? RT_EOK : -RT_EIO;
}

rt_err_t safety_drv_permit_confirm_low(void) { return safety_drv_permit_confirm_read(0u); }

/* ---- 真实 drv_epwm.h 的 prepare/commit 端口(门外纯计算 + 门内整数写) ---- */
#include "drv_epwm.h"          /* 真实公开头(struct ns800_epwm_prepared 的唯一定义处) */
static rt_err_t rc_prepare = RT_EOK;
void ps_sim_epwm_set_prepare_rc(rt_err_t r) { rc_prepare = r; }

int ns800_epwm_prepare(struct rt_device_pwm *device, rt_uint32_t channel,
                       rt_uint32_t period_ns, rt_uint32_t pulse_ns,
                       struct ns800_epwm_prepared *out)
{
    (void)device;
    if (out == RT_NULL) return -RT_EINVAL;
    if (rc_prepare != RT_EOK) return (int)rc_prepare;
    memset(out, 0, sizeof(*out));
    out->channel      = channel;
    out->count_mode   = 2u;              /* EPWM_COUNTER_MODE_UP_DOWN(桩侧固定值) */
    out->clock_div    = 1u;              /* EPWM_CLOCK_DIVIDER_2 */
    out->hs_clock_div = 0u;              /* EPWM_HSCLOCK_DIVIDER_1 */
    out->period_ns    = period_ns;
    out->pulse_ns     = pulse_ns;
    out->tb_period    = (rt_uint16_t)(period_ns / 10u);
    out->cmp_val      = (rt_uint16_t)(pulse_ns / 10u);
    out->valid        = 1u;
    return RT_EOK;
}

/* 门内提交: 只记一次"整数写"并复用 rt_pwm_set 的注入结果(不写回计数模式)。
 * ⚠ 这是**端口桩**, 不等于真实 HAL 叶子; C10 用另一条链真编译 drv_epwm.c。 */
int ns800_epwm_commit_prepared_held(struct rt_device_pwm *device,
                                    const struct ns800_epwm_prepared *prepared)
{
    if (prepared == RT_NULL || prepared->valid != 1u) return -RT_EINVAL;
    return (int)rt_pwm_set(device, (int)prepared->channel,
                           prepared->period_ns, prepared->pulse_ns);
}


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

/* M7R1-A(C22f) 探针的后半: 要直接读 canonical 的 sp.owner, 只能放在 stop_priority.c
 * 纳入之后(前面只有前向声明)。读的是普通字段, 不开窗口、不打印、不调度任何动作。 */
static int sp_owner_peek(void) { return (int)sp.owner; }

static void s2_win_probe_exit(void)
{
    int after = (int)sp.owner;

    if (s2_win_owner_before == (int)SP_NONE && after != (int)SP_NONE)
    {
        /* 这个最外层窗口把 owner 从"无人"认领成了某个 owner => 它就是那次预约的窗口。
         * 登记认领时刻本线程 mot_lock 的持有层数, 由 C22f 判"预约是否落在业务锁的
         * 持有期内"。层数 0 = 检查与预约之间锁被还掉过 = 修前的形状。 */
        s2_reserve_probe_seen = 1;
        s2_reserve_probe_mot_depth = s2_win_mot_before;
        s2_reserve_probe_owner = after;
    }
}

void s2_reserve_probe_reset(void)
{
    s2_reserve_probe_seen = 0;
    s2_reserve_probe_mot_depth = -1;
    s2_reserve_probe_owner = -1;
}

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
    /* CP6: 会合到"B 确实在门外被挡住"这一事件(墙钟 5s 只作死锁守卫)。 */
    b_waited = s2_contend_wait_above(cont0, 5000);
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

/* =====================================================================
 * R6(2026-11-03 任务书 §7.2): 一次**成功的 disarm** 必须把 Safety 从 RUN 收口到 READY。
 * 现场证据(reports/reviews/phase8-m8-idle-stack-r4/field/field-20261101T153923Z/
 * S_S3/01_sysboot.rx.bin): 同一份回包里同时有
 *   [Safety] state=RUN ... protect_ready=YES   /   [Motor] state=0 armed=0
 *   [Motor] arm gate fail mask=0x2 (arm REFUSED)
 * => 停机与禁能都做了, 状态机却留在 RUN, 于是 MOT_GATE_NOT_READY 永久拒绝 arm,
 *    现场只能靠人工断电恢复。源码侧可达缺口: mot_commit_gated()(motor.c:1803)
 *    只写 mot.*, 从不写 ss_state; 而 ramp 在 mot.state==IDLE 时提前 return
 *    (motor.c:786-792), 未 armed 分支也明确不改 Safety。
 * 下面全部走真实 motor_arm/motor_start/motor_stop/motor_disarm/motor_ramp_tick,
 * 不另写简化状态机。C23a..C23c 是"该收口"的正例(修前判 FAIL),
 * C23d..C23h 是"不许乱收口"的反例(修前修后都必须 PASS, 防止把门改成无条件 READY)。
 * ===================================================================== */
static int dr_run(void)
{
    sp_snapshot_t s;
    motor_snapshot_t m;

    if (motor_arm() != RT_EOK) return 0;
    if (motor_set_target_hz(900) != RT_EOK) return 0;
    if (motor_start() != RT_EOK) return 0;
    ramp_until_zero(6);
    m = snap_now();
    sp_get(&s);
    return (s.phase == SP_RUNNING && m.current_hz > 0 &&
            safety_state_get() == SAFETY_RUN) ? 1 : 0;
}

static void cases_disarm_ready(void)
{
    sp_snapshot_t s;
    motor_snapshot_t m;
    sp_proof_t pf;
    sp_token_t oldtok;
    rt_uint32_t old_ep;
    rt_uint32_t gate;
    rt_err_t rearm;
    rt_err_t e;
    rt_bool_t committed;

    if (!gates_open())
    {
        SKIPV("C23", "target 变体宏全 FALSE, 无法 arm/start, RUN->READY 收口不可达");
        return;
    }

    /* ---- C23a: RUN 中直接 disarm(现场清理就是这个形状: 没有先发 stop) ---- */
    wire_and_boot();
    if (!pre_ok("C23a run", dr_run())) return;
    e = motor_disarm();
    m = snap_now();
    sp_get(&s);
    CHECK("C23a-disarm-from-run-commits-ready",
          e == RT_EOK && m.state == MOTOR_IDLE && m.armed == 0 &&
          m.current_hz == 0 && m.target_hz == 0 &&
          safety_state_get() == SAFETY_READY && pad_level == PIN_LOW &&
          s.permit.confirmed == RT_TRUE && s.inhibit == RT_FALSE,
          fm("disarm rc=%d mot.state=%d armed=%d hz=%u/%u ss=%s pad=%d"
             " permit conf=%s lvl=%u we=%u ce=%u | pwm res=%s ep=%u | c ep=%u phase=%s"
             " inhibit=%s latched=%s pending_ep=%u meta_pend=%s",
             (int)e, (int)m.state, (int)m.armed,
             (unsigned)m.current_hz, (unsigned)m.target_hz,
             safety_state_name(safety_state_get()), (int)pad_level,
             s.permit.confirmed ? "YES" : "no", (unsigned)s.permit.request_level,
             (unsigned)s.permit.write_epoch, (unsigned)s.permit.confirm_epoch,
             mot_stop_result_name(s.pwm.result), (unsigned)s.pwm.epoch,
             (unsigned)s.epoch, sp_phase_name(s.phase),
             s.inhibit ? "YES" : "no", s.motor_stop_latched ? "YES" : "no",
             (unsigned)s.pending_epoch, s.metadata_pending ? "YES" : "no"));

    /* ---- C23a2: 收口之后必须能再次 arm(现场 0x2 永久拒绝就是这条过不去) ---- */
    gate = motor_get_gate_fail_mask();
    rearm = motor_arm();
    CHECK("C23a2-rearm-allowed-after-run-disarm",
          (gate & MOT_GATE_NOT_READY) == 0u && rearm == RT_EOK,
          fm("gate_mask=0x%X arm rc=%d ss=%s", (unsigned)gate, (int)rearm,
             safety_state_name(safety_state_get())));
    (void)motor_disarm();

    /* ---- C23b: stop 刚发出(斜坡未归零)就 disarm, 之后再跑若干拍 ---- */
    wire_and_boot();
    if (!pre_ok("C23b run", dr_run())) return;
    (void)motor_stop();
    m = snap_now();
    if (!pre_ok("C23b still ramping", m.current_hz > 0 && m.target_hz == 0)) return;
    e = motor_disarm();
    ramp_until_zero(40);
    m = snap_now();
    sp_get(&s);
    CHECK("C23b-disarm-before-zero-commits-ready",
          e == RT_EOK && m.state == MOTOR_IDLE && m.armed == 0 && m.current_hz == 0 &&
          safety_state_get() == SAFETY_READY && s.phase != SP_RUNNING,
          fm("disarm rc=%d mot.state=%d armed=%d hz=%u ss=%s phase=%s",
             (int)e, (int)m.state, (int)m.armed, (unsigned)m.current_hz,
             safety_state_name(safety_state_get()), sp_phase_name(s.phase)));

    /* ---- C23c: 正常归零后再 disarm(既有行为回归, 修前就应 PASS 且不许变差) ---- */
    wire_and_boot();
    if (!pre_ok("C23c run", dr_run())) return;
    (void)motor_stop();
    ramp_until_zero(80);
    if (!pre_ok("C23c zero-ready", safety_state_get() == SAFETY_READY)) return;
    e = motor_disarm();
    m = snap_now();
    gate = motor_get_gate_fail_mask();
    rearm = motor_arm();
    CHECK("C23c-normal-zero-then-disarm-keeps-ready",
          e == RT_EOK && m.state == MOTOR_IDLE && m.armed == 0 &&
          safety_state_get() == SAFETY_READY && rearm == RT_EOK,
          fm("disarm rc=%d mot.state=%d ss=%s rearm rc=%d", (int)e, (int)m.state,
             safety_state_name(safety_state_get()), (int)rearm));
    (void)motor_disarm();

    /* ---- C23d 反例: STEP 关断失败 => 落 FAULT, 不得清故障, 不得写 READY ---- */
    wire_and_boot();
    if (!pre_ok("C23d run", dr_run())) return;
    ps_sim_pwm_disable_seq(1, -RT_ERROR);
    e = motor_disarm();
    m = snap_now();
    sp_get(&s);
    CHECK("C23d-off-fail-no-ready-no-latch-clear",
          m.state == MOTOR_FAULT && m.stop_fail_latched == 1 &&
          safety_state_get() != SAFETY_READY && s.inhibit == RT_TRUE,
          fm("disarm rc=%d mot.state=%d latched=%u ss=%s inhibit=%s",
             (int)e, (int)m.state, (unsigned)m.stop_fail_latched,
             safety_state_name(safety_state_get()), s.inhibit ? "YES" : "no"));
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    trip_and_reset();

    /* ---- C23e 反例: 许可 LOW 未确认(焊盘卡 HIGH) => 不授予重新 arm 资格 ---- */
    wire_and_boot();
    if (!pre_ok("C23e run", dr_run())) return;
    ps_sim_set_pad_stuck(1);
    e = motor_disarm();
    ps_sim_set_pad_stuck(0);
    sp_get(&s);
    CHECK("C23e-permit-low-unconfirmed-no-ready",
          safety_state_get() != SAFETY_READY &&
          (s.permit.confirmed != RT_TRUE || s.permit.confirmed_level != 0u) &&
          (motor_get_gate_fail_mask() & MOT_GATE_NOT_READY) != 0u,
          fm("disarm rc=%d ss=%s permit conf=%s level=%u gate=0x%X", (int)e,
             safety_state_name(safety_state_get()), s.permit.confirmed ? "YES" : "no",
             (unsigned)s.permit.confirmed_level, (unsigned)motor_get_gate_fail_mask()));
    trip_and_reset();

    /* ---- C23f 反例: 旧一代 disarm 的落位晚到, 不得把新 owner 的 RUN 改成 READY ---- */
    wire_and_boot();
    if (!pre_ok("C23f arm", motor_arm() == RT_EOK)) return;
    oldtok = mot_res;
    old_ep = oldtok.epoch;
    rt_memset(&pf, 0, sizeof(pf));
    (void)sp_off_attempt_for(old_ep, SP_MOTOR, MOT_STOP_SRC_DISARM,
                             FAULT_SOFT, EVT_SOFT_FAULT, 1, &pf);
    (void)motor_disarm();
    if (!pre_ok("C23f new run", dr_run())) return;   /* 新一代已经在 RUN */
    committed = motor_commit_off(&pf, MOT_COMMIT_DISARM);
    m = snap_now();
    CHECK("C23f-stale-disarm-cannot-ready-new-run",
          committed == RT_FALSE && safety_state_get() == SAFETY_RUN &&
          m.state != MOTOR_IDLE && mot.armed == 1,
          fm("旧代 commit 返回=%s ss=%s mot.state=%d armed=%d",
             committed ? "TRUE" : "FALSE", safety_state_name(safety_state_get()),
             (int)m.state, (int)mot.armed));
    (void)motor_disarm();

    /* ---- C23g 反例: 已锁存故障时 disarm 不得把 Safety 拉回 READY ---- */
    wire_and_boot();
    if (!pre_ok("C23g run", dr_run())) return;
    rt_memset(&pf, 0, sizeof(pf));
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    (void)safety_reconcile_trip(FAULT_SOFT, EVT_SOFT_FAULT);
    e = motor_disarm();
    sp_get(&s);
    CHECK("C23g-latched-fault-disarm-no-ready",
          safety_state_get() != SAFETY_READY && s.inhibit == RT_TRUE &&
          (motor_get_gate_fail_mask() & MOT_GATE_NOT_READY) != 0u,
          fm("disarm rc=%d ss=%s inhibit=%s gate=0x%X", (int)e,
             safety_state_name(safety_state_get()), s.inhibit ? "YES" : "no",
             (unsigned)motor_get_gate_fail_mask()));
    trip_and_reset();

    /* ---- C23h 反例: 取不到 ss_lock 就整笔放弃(不写半笔), 恢复后可正常收口 ---- */
    wire_and_boot();
    if (!pre_ok("C23h run", dr_run())) return;
    s2_set_lock_fail("ss");
    e = motor_disarm();
    m = snap_now();
    CHECK("C23h-ss-lock-error-abandons-commit",
          safety_state_get() == SAFETY_RUN && m.state != MOTOR_IDLE &&
          m.armed == 0 && s2_lock_fail_hits > 0,
          fm("disarm rc=%d ss=%s mot.state=%d armed=%d 取锁失败次数=%d", (int)e,
             safety_state_name(safety_state_get()), (int)m.state, (int)m.armed,
             s2_lock_fail_hits));
    s2_lock_fail_clear();
    e = motor_disarm();
    m = snap_now();
    rearm = motor_arm();
    /* 重试那条 disarm 可能带回 -RT_EAGAIN(预约已在第一次尝试里撤销, 没有东西再可禁),
     * 这**不是**没收口: 判据只看"状态机与影子最终一致 + 恢复资格重新可用"。 */
    CHECK("C23h2-retry-after-lock-ok-commits-ready",
          m.state == MOTOR_IDLE && safety_state_get() == SAFETY_READY &&
          rearm == RT_EOK,
          fm("重试 disarm rc=%d mot.state=%d armed=%d ss=%s rearm rc=%d",
             (int)e, (int)m.state, (int)m.armed,
             safety_state_name(safety_state_get()), (int)rearm));
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
/* C22 = M7R1-A: 标定静止事务与诊断预约必须共用 mot_lock 的串行化关系
 * 两条确定次序都用真线程, 先后顺序靠夹具既有的"门外等待"会合点判定:
 * s2_contend_hits/S2_SEQ_CONTEND 只在**另一个线程确实持有**该业务锁时才置位,
 * 因此"对方已经卡在锁上"是一个事件, 不是 usleep 猜出来的(任务书 §9 同一纪律)。
 * 修后的合同两条:
 *   (1) 标定事务没结束期间, 不得出现新的 SP_DIAG 预约;
 *   (2) 事务结束之后同一条借用路径必须重新可用 —— 否则是永久封锁而不是串行化。
 * 但 (1) 单独还不足以锁住这条并发合同: 两条"别人持锁"的次序在修前的实现里也会通过
 * (诊断线程只是卡在 gate 内部的取锁上, 不是"检查+预约"整段被覆盖)。因此补 C22f,
 * 直接观察**预约那一刻**本线程是否仍在 mot_lock 持有期内 —— 这是"诊断先取得资格但
 * 还未预约时标定不得竞争进来"这第二种次序的可判定形式。 */
#define BIT_CAL_START     6
#define BIT_DIAG_DONE     7

static int c22_grant_rc = 99, c22_owner_after = 0xFF, c22_taken;


static void c22_diag_worker(void *a)
{
    sp_token_t tok;
    sp_snapshot_t s;

    (void)a;
    s2_seq_set(BIT_CAL_START);            /* 在可能阻塞之前先声明"我要请求了" */
    c22_grant_rc = (int)motor_pwm_grant_to_diag(&tok);
    sp_get(&s);
    c22_owner_after = (int)s.owner;
    c22_taken = (c22_grant_rc == RT_EOK) ? 1 : 0;
    if (c22_taken) (void)sp_release(&tok);   /* 真拿到就归还, 不给后面的用例留残态 */
    s2_seq_set(BIT_DIAG_DONE);
}

static void cases_cal_static(void)
{
    sp_snapshot_t s;
    sp_token_t tok;
    rt_err_t e;
    int hits0;

    /* 这一族全部以"诊断借用真可发生"为前提 => target 变体(四宏 RT_FALSE)整族按声明
     * SKIP, 不拿"门永远拒绝"冒充标定串行化已经被验证。 */
    if (!diag_gates_open())
    {
        SKIPV("C22", "target 变体无 STEP 输出许可, 预约路径不可达(整族)");
        return;
    }

    /* ---- C22a 控制: 静止且无事务时, 预约必须能正常取得并归还 ----
     * 没有这一条, 后面的"被拒绝/被串行"可能只是因为夹具根本拿不到预约。 */
    wire_and_boot();
    if (!pre_ok("C22a READY", safety_state_get() == SAFETY_READY)) return;
    e = motor_pwm_grant_to_diag(&tok);
    sp_get(&s);
    CHECK("C22a-idle-grant-works",
          e == RT_EOK && s.owner == SP_DIAG,
          fm("grant rc=%d owner=%u(期望 SP_DIAG=%u) - 无人竞争时借用路径必须可用",
             (int)e, (unsigned)s.owner, (unsigned)SP_DIAG));
    (void)sp_release(&tok);
    sp_get(&s);
    if (!pre_ok("C22a release balances", s.owner == SP_NONE)) return;

    /* ---- C22b 标定先持事务 -> 另一线程请求预约: 事务期间不得出现预约 ---- */
    wire_and_boot();
    s2_seq_reset();
    s2_contend_clear();
    hits0 = s2_contend_hits;
    if (!pre_ok("C22b cal begin",
                motor_calibration_static_begin() == RT_EOK)) return;
    c22_grant_rc = 99; c22_taken = 0; c22_owner_after = 0xFF;
    s2_spawn(c22_diag_worker, RT_NULL);
    if (!seq_or_fail("C22b worker started", BIT_CAL_START))
    {
        motor_calibration_static_end(); s2_join_all(); return;
    }
    if (!seq_or_fail("C22b worker parked on mot_lock", S2_SEQ_CONTEND))
    {
        motor_calibration_static_end(); s2_join_all(); return;
    }
    sp_get(&s);
    /* 此刻工作线程还停在锁外, 它自己的观察值尚未写下(初值 0xFF) —— 那是正常的,
     * 判据只看**全局**有没有产生新的 SP_DIAG 预约, 不看它还没机会写的那个字段。 */
    CHECK("C22b-cal-tx-blocks-diag-reservation",
          s2_contend_hits > hits0 && strcmp(s2_contend_name, "mot") == 0 &&
          s.owner == SP_NONE && !c22_taken,
          fm("事务持锁中: 竞争计数 %d->%d(锁=%s) 全局 owner=%u(SP_NONE=%u)"
             " 工作线程已完成=%d - 预约没有在事务期间产生",
             hits0, s2_contend_hits, s2_contend_name,
             (unsigned)s.owner, (unsigned)SP_NONE,
             (s2_seq_get_bit(BIT_DIAG_DONE) ? 1 : 0)));
    /* 放锁 => 同一线程的等待必须被放行, 并且这次合法地拿到预约(串行完成)。 */
    motor_calibration_static_end();
    if (!seq_or_fail("C22b2 worker finished", BIT_DIAG_DONE))
    {
        s2_join_all(); return;
    }
    s2_join_all();
    sp_get(&s);
    CHECK("C22b2-grant-legal-after-tx-ends",
          c22_grant_rc == RT_EOK && c22_taken == 1,
          fm("事务结束后诊断 rc=%d taken=%d - 是串行化, 不是永久封锁",
             c22_grant_rc, c22_taken));
    if (!pre_ok("C22b2 owner released", s.owner == SP_NONE)) return;

    /* ---- C22c 反向次序(不带标定参与): 任意线程持 mot_lock 时, 诊断借用同样必须
     * 被挡在锁外且不产生预约。修前 grant 的"资格判定 + 预约"跨了两次取放, 这一段
     * 窗口对任何持锁方都存在; 现在两段在同一个持锁段里, 所以挡住它的只有锁本身。
     * 用夹具的真递归互斥量按名字取"mot"(与 C02 同一手法), 不依赖标定标志位。 */
    wire_and_boot();
    s2_seq_reset();
    s2_contend_clear();
    hits0 = s2_contend_hits;
    {
        struct rt_mutex *m = s2_mtx_by_name("mot");

        if (!pre_ok("C22c mot mutex exists", m != RT_NULL)) return;
        if (!pre_ok("C22c main takes mot_lock",
                    rt_mutex_take(m, RT_WAITING_FOREVER) == RT_EOK)) return;
        c22_grant_rc = 99; c22_taken = 0; c22_owner_after = 0xFF;
        s2_spawn(c22_diag_worker, RT_NULL);
        if (!seq_or_fail("C22c worker started", BIT_CAL_START) ||
            !seq_or_fail("C22c worker parked on mot_lock", S2_SEQ_CONTEND))
        {
            (void)rt_mutex_release(m); s2_join_all(); return;
        }
        sp_get(&s);
        CHECK("C22c-lock-holder-blocks-diag-reservation",
              s2_contend_hits > hits0 && strcmp(s2_contend_name, "mot") == 0 &&
              s.owner == SP_NONE && !c22_taken,
              fm("外部持锁中: 竞争 %d->%d(锁=%s) owner=%u(SP_NONE=%u) done=%d"
                 " - 串行化来自锁, 不是来自标定标志",
                 hits0, s2_contend_hits, s2_contend_name, (unsigned)s.owner,
                 (unsigned)SP_NONE, (s2_seq_get_bit(BIT_DIAG_DONE) ? 1 : 0)));
        (void)rt_mutex_release(m);
        if (!seq_or_fail("C22c worker finished", BIT_DIAG_DONE))
        {
            s2_join_all(); return;
        }
        s2_join_all();
        sp_get(&s);
        CHECK("C22c2-grant-legal-after-holder-releases",
              c22_grant_rc == RT_EOK && c22_taken,
              fm("放锁后诊断 rc=%d taken=%d - 是串行化, 不是永久封锁",
                 c22_grant_rc, c22_taken));
        if (!pre_ok("C22c2 owner released", s.owner == SP_NONE)) return;
    }

    /* ---- C22d 原有语义未退化: 诊断已持预约时, 标定事务必须拒绝 ---- */
    wire_and_boot();
    if (!pre_ok("C22d grant", motor_pwm_grant_to_diag(&tok) == RT_EOK)) return;
    e = motor_calibration_static_begin();
    sp_get(&s);
    CHECK("C22d-diag-owner-blocks-cal-tx",
          e != RT_EOK && s.owner == SP_DIAG,
          fm("begin rc=%d owner=%u - 已持诊断预约时标定事务必须拒绝且不残留",
             (int)e, (unsigned)s.owner));
    (void)sp_release(&tok);

    /* ---- C22e 失败出口不泄锁: 被串行化过的诊断路径之后, 锁仍可正常取放 ---- */
    wire_and_boot();
    if (!pre_ok("C22e cal begin",
                motor_calibration_static_begin() == RT_EOK)) return;
    s2_seq_reset();
    s2_contend_clear();
    c22_grant_rc = 99; c22_taken = 0;
    s2_spawn(c22_diag_worker, RT_NULL);
    if (!seq_or_fail("C22e worker parked", S2_SEQ_CONTEND))
    {
        motor_calibration_static_end(); s2_join_all(); return;
    }
    motor_calibration_static_end();
    if (!seq_or_fail("C22e worker finished", BIT_DIAG_DONE))
    {
        s2_join_all(); return;
    }
    s2_join_all();
    {
        motor_snapshot_t ms;
        int r1, r2;

        memset(&ms, 0, sizeof(ms));
        r1 = (int)motor_get_snapshot(&ms);          /* 内部要取放 mot_lock */
        r2 = (int)motor_pwm_grant_to_diag(&tok);
        sp_get(&s);
        CHECK("C22e-contended-path-leaks-no-lock",
              r1 == RT_EOK && r2 == RT_EOK && s.owner == SP_DIAG && c22_taken,
              fm("排队之后 snapshot=%d grant=%d owner=%u - 没有把锁留在被拒的一刻",
                 r1, r2, (unsigned)s.owner));
        (void)sp_release(&tok);
    }

    /* ---- C22f "已取得资格但尚未预约"这段时间必须真的在锁里 ----
     * C22b/C22c 只证明"别人持锁时预约出不来", 而修前的形状同样满足它: 诊断线程会
     * 碰巧卡在 gate 内部 motor_get_snapshot() 的那次取锁上, 而不是"检查+预约"这一整段
     * 被同一把锁覆盖(2026-10-29 REV3 实测: 把外层持锁拆掉, C22b/C22c 仍然全绿)。
     * 这一条不靠抢时间: 生产里每次预约都在一个最外层屏蔽窗口内把 owner 从 SP_NONE
     * 认领成自己, 夹具在该窗口进入时记下本线程 mot_lock 的持有层数 => 预约时刻的覆盖
     * 是一个当场可读的事实。合同: 借用路径的这次认领层数必须 >=1。 */
    wire_and_boot();
    if (!pre_ok("C22f READY", safety_state_get() == SAFETY_READY)) return;
    sp_get(&s);
    if (!pre_ok("C22f no owner before", s.owner == SP_NONE)) return;
    s2_reserve_probe_reset();
    e = motor_pwm_grant_to_diag(&tok);
    sp_get(&s);
    CHECK("C22f-qualification-and-reservation-are-one-hold",
          e == RT_EOK && s2_reserve_probe_seen == 1 &&
          s2_reserve_probe_owner == (int)SP_DIAG &&
          s2_reserve_probe_mot_depth >= 1 && s.owner == SP_DIAG,
          fm("grant=%d 认领=%d owner=%d 预约时 mot_lock 层数=%d - 检查还锁之后才预约"
             "就是修前那个窗口(期望层数>=1, 即仍在 mot_lock 持有期内)",
             (int)e, s2_reserve_probe_seen, s2_reserve_probe_owner,
             s2_reserve_probe_mot_depth));
    if (s.owner != SP_NONE) (void)sp_release(&tok);
    sp_get(&s);
    if (!pre_ok("C22f release balances", s.owner == SP_NONE)) return;
    /* 对照: 预约时刻的观测本身不是恒真 —— 同一探针在没有发生认领时必须报"没认领",
     * 否则 C22f 的"看到一次层数>=1 的认领"不构成证据。 */
    {
        motor_snapshot_t fms;

        s2_reserve_probe_reset();
        memset(&fms, 0, sizeof(fms));
        if (!pre_ok("C22f2 snapshot ok", motor_get_snapshot(&fms) == RT_EOK)) return;
        CHECK("C22f2-probe-not-vacuous",
              s2_reserve_probe_seen == 0 && s2_reserve_probe_mot_depth == -1,
              fm("只读快照之后 认领=%d 层数=%d - 探针不会把无关窗口当预约",
                 s2_reserve_probe_seen, s2_reserve_probe_mot_depth));
    }
}

/* ===================================================================== */
/* main: 一个进程跑完全部适用 ID(与既有四链同一台账纪律)                    */
/* ===================================================================== */
struct s2_grp { const char *name; void (*fn)(void); };
/* ===================================================================== */
/* R7F1(任务书 §2/§4): 现场被截断的那几条启动打印拆短之后的逐条字节量证      */
/* ===================================================================== */
/* 每条判据都是**同一次实跑**里产品真正发出去的那一行(不是格式串长度):
 *   would_be <= 120  /  没有 lost 字节  /  没有把缓冲区的 NUL 当数据发出去
 *   末尾就是换行     /  截断处没有半截 UTF-8 序列
 * 三处的成功与故障分支都跑到: [MOT] init OK、[SS] STEP: / !! STEP、
 * [SS] ==> FAULT_LATCHED、以及 §2 新加的 [SS] [REQ FAIL] TMC2209 IOIN 两行。
 * ⚠ 本组不主张"全仓打印都达标": 现场本轮未执行的那条 126 B 的 [MOT]  注: 行仍按原样
 *   留着(在 127 线内), 由 BPT01 的 detail 如实量出来并登记, 不在本轮顺带重写。 */

/* 一条被点名行的完整判据(把实际长度交给 detail, 便于第三方按同一份文本复算)。 */
static int bpt_line_ok(int idx, int *len_out)
{
    struct bpt_rec *r;

    if (idx < 0 || idx >= bpt_n) return 0;
    r = &bpt_line[idx];
    if (len_out) *len_out = r->would_be;
    return r->text_fit && r->would_be <= BPT_LIMIT && r->lost == 0 &&
           r->nul_emitted == 0 && r->tail_nl == 1 && r->utf_split == 0 &&
           memchr(r->text, '\0', (size_t)r->content) == RT_NULL;
}

/* fm() 的参数求值顺序在 C 里是**未定义**的(gcc 本机实测从右往左), 所以"判据里才写进
 * len_out 的数字"绝不能直接出现在 detail 里 —— 会打成 -1 而判据本身仍然为真, 台账就
 * 变成一份看不出问题的假证据。下面这些 k 与 t 全部先用语句算完, 再把纯整数与纯字符串
 * 交给 fm。 */
static void bpt_copy(int idx, char *dst, size_t n)
{
    size_t i;

    if (idx < 0 || idx >= bpt_n || !bpt_line[idx].text_fit) { dst[0] = '-'; dst[1] = '\0'; return; }
    for (i = 0; i + 1 < n && bpt_line[idx].text[i] != '\0'; ++i)
        dst[i] = (bpt_line[idx].text[i] == '\n') ? '@' : bpt_line[idx].text[i];
    dst[i] = '\0';
}

static void cases_boot_print(void)
{
    int i1, i2, i3, i4, i5, i6, i7, j1;
    int l1, l2, l3, l4, l5, l6, l7, note_l, worst;
    int k1, k2, k3, k4, k5, k6, k7;
    char t1[BPT_LINE_MAX], t2[BPT_LINE_MAX];
    rt_err_t e;

    /* ---------- BPT01: [MOT] init OK 拆成的两条(motor.c:1062/1064) ----------
     * 这条要连着 wire_and_boot() 一起看: [MOT] init OK 只在**本进程第一次** motor_init()
     * 时打印, 所以窗口必须把装配包进来。 */
    l1 = l2 = note_l = worst = -1;
    bpt_reset();
    wire_and_boot();
    i1 = bpt_find("[MOT] init OK (enable-path-validated=");
    i2 = bpt_find("[MOT] init OK: DRV_ENABLE LOW verified, STEP=");
    note_l = bpt_find("[MOT]   注: STEP=api-accepted");
    k1 = bpt_line_ok(i1, &l1);
    k2 = bpt_line_ok(i2, &l2);
    (void)bpt_all_within_limit(&worst);
    CHECK("BPT01-mot-init-ok-splits-into-two-lines-each-within-console-limit",
          bpt_dropped == 0 && i1 >= 0 && i2 >= 0 && k1 == 1 && k2 == 1,
          fm("L1=%d B L2=%d B(门槛%d) / 窗口共 %d 行, 全窗口最宽=%d B, dropped=%d"
             " / 本轮未改的那条 [MOT]  注: 行实测=%d B(在 127 线内, 登记不改)"
             " / L2 原文=[%s]",
             l1, l2, BPT_LIMIT, bpt_n, worst, bpt_dropped,
             (note_l >= 0) ? bpt_line[note_l].would_be : -1,
             (i2 >= 0) ? bpt_flat(i2) : "-"));

    /* ---------- BPT02: [SS] STEP: 成功分支三条 + FAULT_LATCHED 四条 ----------
     * 窗口从装配**之后**才开始记: wire_and_boot() 自己会走一遍恢复/关断并打出
     * 另一族的行, 留在窗口里会让"另一分支不该出现"这条判据失去意义。 */
    l1 = l2 = l3 = l4 = l5 = l6 = l7 = worst = -1;
    wire_and_boot();
    bpt_reset();
    (void)safety_force_shutdown(FAULT_SELF_TEST);
    i1 = bpt_find("[SS] STEP: 两条输出路径都没有未确认的关断");
    i2 = bpt_find("[SS] STEP: motor=");
    i3 = bpt_find("[SS] STEP: diag=");
    i4 = bpt_find("[SS] ==> FAULT_LATCHED code=");
    i5 = bpt_find("[SS] ==> FAULT_LATCHED ENN feedback=");
    i6 = bpt_find("[SS] ==> FAULT_LATCHED motor_rc=");
    i7 = bpt_find("[SS] ==> 整理身份 epoch=");
    j1 = bpt_find("[SS] !! STEP");
    k1 = bpt_line_ok(i1, &l1); k2 = bpt_line_ok(i2, &l2); k3 = bpt_line_ok(i3, &l3);
    k4 = bpt_line_ok(i4, &l4); k5 = bpt_line_ok(i5, &l5); k6 = bpt_line_ok(i6, &l6);
    k7 = bpt_line_ok(i7, &l7);
    (void)bpt_all_within_limit(&worst);
    CHECK("BPT02-ss-step-success-branch-and-fault-latched-all-short-complete-lines",
          bpt_dropped == 0 && j1 < 0 && k1 && k2 && k3 && k4 && k5 && k6 && k7 &&
          safety_state_get() == SAFETY_FAULT_LATCHED &&
          safety_fault_get() == FAULT_SELF_TEST,
          fm("STEP 三条=%d/%d/%d B, FAULT_LATCHED 四条=%d/%d/%d/%d B(门槛%d)"
             " 锁存态=%s fault=%u / !! STEP 故障分支出现=%d(必须 -1)"
             " 窗口 %d 行 最宽=%d B dropped=%d",
             l1, l2, l3, l4, l5, l6, l7, BPT_LIMIT,
             safety_state_name(safety_state_get()), (unsigned)safety_fault_get(),
             j1, bpt_n, worst, bpt_dropped));

    /* ---------- BPT03: [SS] !! STEP 故障分支四条(同一 if/else 的另一侧) ----------
     * 注入"每一次 rt_pwm_disable 都失败" => 两条输出路径都有未确认的关断 =>
     * step_off_ok=FALSE 走故障分支。窗口同样只记这一次触发之后的行。 */
    l1 = l2 = l3 = l4 = l5 = l6 = worst = -1;
    wire_and_boot();
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, -RT_EIO);
    bpt_reset();
    (void)safety_force_shutdown(FAULT_SELF_TEST);
    ps_sim_pwm_set_rc(RT_EOK, RT_EOK, RT_EOK);
    i1 = bpt_find("[SS] !! STEP 关断**未确认** ->");
    i2 = bpt_find("[SS] !! STEP motor=");
    i3 = bpt_find("[SS] !! STEP diag=");
    i4 = bpt_find("后续 arm/start/诊断借用/故障清除全部拒绝");
    i5 = bpt_find("[SS] ==> FAULT_LATCHED code=");
    i6 = bpt_find("[SS] ==> 整理身份 epoch=");
    j1 = bpt_find("[SS] STEP: 两条输出路径都没有未确认的关断");
    k1 = bpt_line_ok(i1, &l1); k2 = bpt_line_ok(i2, &l2); k3 = bpt_line_ok(i3, &l3);
    k4 = bpt_line_ok(i4, &l4); k5 = bpt_line_ok(i5, &l5); k6 = bpt_line_ok(i6, &l6);
    bpt_copy(i2, t2, sizeof(t2));
    (void)bpt_all_within_limit(&worst);
    CHECK("BPT03-ss-step-unconfirmed-fault-branch-splits-into-four-complete-lines",
          bpt_dropped == 0 && j1 < 0 && k1 && k2 && k3 && k4 && k5 && k6,
          fm("故障分支四条=%d/%d/%d/%d B(门槛%d; motor= 那行同时带 src/rc/latched"
             " 四个字段, 离线最宽实参按 49+14+11+3 算) FAULT_LATCHED 首末行=%d/%d B"
             " 成功分支出现=%d(必须 -1) 窗口 %d 行 最宽=%d B dropped=%d 原文=[%s]",
             l1, l2, l3, l4, BPT_LIMIT, l5, l6, j1, bpt_n, worst, bpt_dropped, t2));

    /* ---------- BPT04: §2 新加的自检归因两行 + 自检仍必须判 FAIL ----------
     * 注入现场的 -RT_EBUSY(-23)。窗口从装配之后开始: 装配自己那次自检会打 [REQ OK],
     * 留在窗口里会让"失败时不许同时报成功"这条判据失去意义。
     * ⚠ 本链是**假总线**, 不建模 flush/TX/RX 阶段, 所以 reason 由夹具明标
     *   not-modelled(fake-bus); 阶段级归因在真实传输层那条链上量(BTB06..BTB10)。 */
    l1 = l2 = worst = -1;
    wire_and_boot();
    bpt_reset();
    s2_set_tmc_read_rc(-RT_EBUSY);
    e = safety_startup_selftest();
    s2_set_tmc_read_rc(RT_EOK);
    i1 = bpt_find("[SS] [REQ FAIL] TMC2209 IOIN rc=");
    i2 = bpt_find("[SS] [REQ FAIL] TMC2209 IOIN tx=");
    j1 = bpt_find("[SS] [REQ OK] TMC2209 IOIN=");
    k1 = bpt_line_ok(i1, &l1); k2 = bpt_line_ok(i2, &l2);
    bpt_copy(i1, t1, sizeof(t1));
    bpt_copy(i2, t2, sizeof(t2));
    (void)bpt_all_within_limit(&worst);
    CHECK("BPT04-selftest-ioin-failure-prints-per-call-reason-as-two-short-lines",
          bpt_dropped == 0 && i1 >= 0 && i2 >= 0 && j1 < 0 && k1 && k2 &&
          e != RT_EOK && safety_state_get() != SAFETY_READY,
          fm("归因两行=%d/%d B(门槛%d) 第一行原文=[%s] 第二行原文=[%s]"
             " 自检 rc=%d state=%s REQ-OK 行同时出现=%d(必须 -1) 窗口 %d 行 dropped=%d",
             l1, l2, BPT_LIMIT, t1, t2,
             (int)e, safety_state_name(safety_state_get()), j1, bpt_n, bpt_dropped));

/* ---------- BPT05: 量具自身的元负控(不先证明会命中, 就不许宣布干净) ----------
     * 三条**夹具自己发的**样本(用 %.*s 控制长度, 不是产品行), 分别命中四种坏签名:
     *   L0 130 个 ASCII + 换行 => would_be=131 > 127: 尾部被裁、发出台尾 NUL、换行丢失
     *   L1 124 个 ASCII + "中文" + 换行 => 正好在 126 内容字节处把一个中文字符切成半截
     *   L2 200 个 ASCII + 换行 => 连夹具文本槽都放不下 => text_fit=0, 不许据文本做判据 */
    {
        char pad[256];
        int  k;

        for (k = 0; k < 210; ++k) pad[k] = 'Z';
        pad[210] = '\0';
        bpt_reset();
        rt_kprintf("%.*s\n", 130, pad);
        rt_kprintf("%.*s中文\n", 124, pad);
        rt_kprintf("%.*s\n", 200, pad);
        CHECK("BPT05-console-cutter-model-fires-on-known-too-long-lines",
              bpt_n == 3 && bpt_dropped == 0 &&
              bpt_line[0].would_be == 131 && bpt_line[0].content == 126 &&
              bpt_line[0].emitted == 127 && bpt_line[0].lost == 4 &&
              bpt_line[0].nul_emitted == 1 && bpt_line[0].tail_nl == 0 &&
              bpt_line[0].utf_split == 0 && bpt_line[0].text_fit == 1 &&
              bpt_line[1].would_be == 131 && bpt_line[1].utf_split == 1 &&
              bpt_line[1].lost == 4 && bpt_line[1].tail_nl == 0 &&
              bpt_line[2].would_be == 201 && bpt_line[2].text_fit == 0 &&
              bpt_line[2].lost == 201 - 127,
              fm("L0 would=%d content=%d emitted=%d lost=%d nul=%d tail_nl=%d utf=%d"
                 " fit=%d / L1 would=%d utf=%d(必须 1) / L2 would=%d fit=%d(必须 0)"
                 " - 四种坏签名都会命中, 所以 BPT01..04 的 PASS 不是量具空转",
                 bpt_line[0].would_be, bpt_line[0].content, bpt_line[0].emitted,
                 bpt_line[0].lost, bpt_line[0].nul_emitted, bpt_line[0].tail_nl,
                 bpt_line[0].utf_split, bpt_line[0].text_fit,
                 bpt_line[1].would_be, bpt_line[1].utf_split,
                 bpt_line[2].would_be, bpt_line[2].text_fit));
    }
}

static const struct s2_grp grps[] = {
    { "lock", cases_lock },           { "isr", cases_isr },
    { "isr_window", cases_isr_window }, { "off_matrix", cases_off_matrix },
    { "off_arm", cases_off_arm },     { "unknown", cases_unknown },
    { "identity", cases_identity },
    { "diag", cases_diag },            { "recovery", cases_recovery },
    { "stale", cases_stale },          { "boundary", cases_boundary },
    { "healthy", cases_healthy },      { "target_reject", cases_target_reject },
    { "cal_static", cases_cal_static }, { "disarm_ready", cases_disarm_ready },
    { "boot_print", cases_boot_print },
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
