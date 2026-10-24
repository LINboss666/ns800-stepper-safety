"""CP4 §9: 给 sp_conc_main.c 加"接缝钩子 + 线程序列点"。"""
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
    assert t.count(old) == cnt, (old[:70].replace("\n", "\\n"), t.count(old))
    t = t.replace(old, new)


rep("""int  s2_win_live_now(void)  { return s2_win_live; }""",
    """int  s2_win_live_now(void)  { return s2_win_live; }

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

#define S2_SEQ_BITS 24
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

int s2_spawn(void (*fn)(void *), void *arg)
{
    if (s2_thr_n >= (int)(sizeof(s2_thr) / sizeof(s2_thr[0]))) return -1;
    if (pthread_create(&s2_thr[s2_thr_n], RT_NULL, (void *(*)(void *))fn, arg) != 0)
        return -1;
    s2_thr_n++;
    return 0;
}
void s2_join_all(void)
{
    int i;

    for (i = 0; i < s2_thr_n; ++i) pthread_join(s2_thr[i], RT_NULL);
    s2_thr_n = 0;
}""")

rep("""rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    rt_err_t e = rc_disable;

    (void)p; (void)ch; calls_disable++;""",
    """rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    rt_err_t e = rc_disable;

    (void)p; (void)ch; calls_disable++;
    /* 钩子点①: 真实危险写(时间基冻结)之前 —— 这个点**在屏蔽窗口之内**, 所以只允许挂
     * "请求一次中断"这种纯入队动作(C04), 不许挂业务/等待/日志(那会破坏模型本身)。 */
    s2_run_hook("rt_pwm_disable");""")

rep("""rt_err_t safety_drv_permit_confirm_read(rt_uint8_t level)
{
    return (pad_level == (rt_int32_t)level) ? RT_EOK : -RT_EIO;
}""",
    """rt_err_t safety_drv_permit_confirm_read(rt_uint8_t level)
{
    /* 钩子点②: 带回读的许可确认(窗口外的慢步骤)。arm/start/人工恢复都走这里。 */
    s2_run_hook("permit_confirm_read");
    return (pad_level == (rt_int32_t)level) ? RT_EOK : -RT_EIO;
}""")

rep("""rt_err_t tmc2209_config_integrity_check_for(rt_uint32_t expected_epoch,
                                           tmc_op_result_t *out)
{
    fake_bus_reads++;""",
    """rt_err_t tmc2209_config_integrity_check_for(rt_uint32_t expected_epoch,
                                           tmc_op_result_t *out)
{
    fake_bus_reads++;
    /* 钩子点③: 前段完整性复核(UART 慢步骤, 必然在窗口外)。K2 的"预约前发生恢复"与
     * "旧自检比较完之后换代"都安排在这里。 */
    s2_run_hook("integrity_check");""")

rep("""rt_err_t safety_drv_feedback_confirm(rt_uint8_t want_enabled, safety_fb_verdict_t *out)
{
    safety_fb_verdict_t v = want_enabled ? fb_en : fb_dis;
    int seq = fb_confirm_calls++;""",
    """rt_err_t safety_drv_feedback_confirm(rt_uint8_t want_enabled, safety_fb_verdict_t *out)
{
    safety_fb_verdict_t v = want_enabled ? fb_en : fb_dis;
    int seq = fb_confirm_calls++;

    /* 钩子点④: ENN 有界确认读 —— safety_run_selftest 的必需项真的经过这里(C15c 用)。
     * 选它而不是随便造一个"接缝": 自检在捕获 epoch0 之后、写 READY 之前必然做一次
     * 反馈确认, 这就是审查观察 old-selftest-ready-after-new-run 的真实交错位置。 */
    s2_run_hook("drv_feedback_confirm");""")

io.open(OUT, "w", encoding="utf-8", newline="\r\n").write(t)
print("hooks+seq written")
