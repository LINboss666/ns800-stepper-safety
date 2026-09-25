/*
 * blackbox.c - 故障黑匣子 (Phase 7-D, Fix C 修复: post 窗口 / 首故障 / 语义 / 重试)
 *
 * 修复记录(Phase 7 review):
 *   P0-1 旧实现把 pre 200 帧拷进 bb_post[100] 数组 → 确定性越界。
 *        现改为单一捕获区 bb_cap[BB_CAP_FRAMES](编译期断言) +
 *        独立 pre 环 bb_ring; pre 不足时只写实际帧数(开机前 2s 不造假历史)。
 *   数字输入持久化: estop/limit_min/limit_max/diag 存 record.flags 位域。
 *   NS_VALID_STEP 不再借用为 valid_safety —— 数字输入在 flags 位域,
 *   NS_VALID_STEP 表示 step_hz 字段有效。
 *
 * Fix C(本轮, 逐项对源码核实后修复):
 *   C1 post 窗口此前永远收不满: worker 一次循环里调了两次 bb_sample()
 *      (pre 环一次、post 一次), 而 bb_sample() 内部按"源帧序号未变化就返回空帧"
 *      工作, 所以第二次必然拿到空帧 → BB_POST_COLLECT 永不推进到 BB_WRITING,
 *      故障记录一次也不会落盘, blackbox_selftest 必然在 20s 后报 not drained。
 *      现在每循环只采一次, 由 pre 环 / post 区共用这一帧; 顺序为
 *      采样 → 喂 pre 环 → 收 post → 处理触发 → 落盘, 触发当帧只属于 pre 窗口,
 *      既不重复也不丢失。
 *   C2 首故障优先此前只在"已进入捕获/写盘"之后成立: blackbox_trigger() 无条件
 *      覆盖 bb_pending_code, 因此 A、B 两次触发都发生在 worker 消费之前时, 落盘
 *      的是 B 的故障码而 A 被静默丢弃。现在 pending 标志未消费就不覆盖并计数。
 *   C3 pre 区记录 event 字段此前恒为 NS_EVENT_TEST(=1, 与 FAULT_ESTOP 数值撞车),
 *      而 blackbox_selftest 又要求本会话全部记录 event==触发码 → 只要 pre_n>0
 *      该自检必挂。现在语义明确: pre 记录 event=0(该帧本身不是事件),
 *      post 记录 event=会话故障码, selftest 按 pre/post 分别断言。
 *   C4 取不到配置快照时此前读未初始化局部量当窗口, 还从 prio 18 worker 里调
 *      project_config_defaults() 改全局运行配置。现在用编译期安全窗口常量,
 *      worker 不再有权修改全局配置。
 *   C5 ns_log_submit 失败时的重试此前无上限也不睡眠(存储 online 但队列不消化
 *      时空转), 会把 prio 18 worker 变成忙等并饿死 tshell/ui。现在有上限,
 *      超限则丢弃本会话余下帧并置 DEGRADED。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdlib.h>                 /* Fix C: atoi (blackbox_dump 用到) */
#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "sensor_service.h"
#include "safety_state.h"
#include "project_config.h"
#include "ns_storage.h"
#include "blackbox.h"

/* 编译期断言(C89 兼容): 负数组长度技巧 */
typedef char bb_size_assert_pre_ge_post[(BB_PRE_FRAMES >= BB_POST_FRAMES) ? 1 : -1];
typedef char bb_size_assert_cap[(BB_CAP_FRAMES == BB_PRE_FRAMES + BB_POST_FRAMES) ? 1 : -1];

typedef struct
{
    rt_uint32_t timestamp;
    rt_uint16_t sg;
    rt_uint32_t current_raw;
    rt_int32_t  current_ma;
    rt_int16_t  ax, ay, az, vib;
    rt_uint32_t step_hz;
    rt_uint8_t  motor_state, dir;
    rt_uint8_t  safety_state;
    rt_uint8_t  estop, limit_min, limit_max, tmc_diag;
    rt_uint8_t  valid_imu, valid_current, valid_sg, valid_safety;
    rt_uint32_t fault_code;      /* 触发时锁存的故障码(仅 post 区帧有效; pre 区=0) */
    rt_uint32_t seq;
} bb_frame_t;

/* 静态内存(实测本结构 48B/帧, 非旧注释写的 36B):
 *   bb_ring 300×48 = 14,400B + bb_cap 450×48 = 21,600B = 36,000B ≈ 35.2KB BSS
 * 零 malloc。链接后 RW_DTCM 约 50KB/128KB, 黑匣子占其中大头 —— 若日后要扩
 * 窗口必须先看链接 map 文件里的 RW_DTCM 占用。 */
static bb_frame_t bb_ring[BB_PRE_FRAMES];
static bb_frame_t bb_cap[BB_CAP_FRAMES];

typedef enum { BB_IDLE = 0, BB_POST_COLLECT, BB_WRITING } bb_state_t;

static volatile bb_state_t bb_state = BB_IDLE;
static volatile rt_uint32_t bb_pending_code = 0;
static volatile rt_bool_t bb_pending_flag = RT_FALSE;
static rt_uint32_t bb_session_fault_code = 0;   /* P1-2: 会话锁存故障码 */
static rt_uint16_t bb_ring_idx = 0;
static rt_uint16_t bb_ring_count = 0;       /* 开机以来实际喂入的帧数 */
static rt_uint16_t bb_post_idx = 0;         /* 捕获区写游标(pre+post) */
static rt_uint32_t bb_last_pre_n = 0;       /* 触发时锁存的 pre 帧数 */
static rt_uint32_t bb_session = 0;
/* 跨上下文计数(worker/任意触发者 写, tshell 读) —— 与 bb_state 同样标 volatile */
static volatile rt_uint32_t bb_trig_dropped = 0;     /* 捕获/写盘中(已消费后)忽略的触发数 */
static volatile rt_uint32_t bb_trig_early_dropped = 0; /* Fix C: 消费之前就来的第二触发数 */
static volatile rt_uint32_t bb_write_dropped = 0;    /* Fix C: 落盘放弃的帧数 */
static volatile rt_err_t bb_last_err = RT_EOK;
static volatile rt_uint32_t bb_written_total = 0;
static rt_thread_t bb_tid = RT_NULL;
static subsys_health_t bb_health = SUBSYS_UNINIT;
static rt_uint32_t bb_last_seq_seen = 0;
/* 生效窗口(从 config 派生并夹取到缓冲容量), worker 每循环同步; 仅状态打印读 */
static volatile rt_uint32_t bb_pre_eff = 200, bb_post_eff = 100;

/* Fix C / C4: 取不到配置快照时的安全回退窗口(编译期常量, 与 config 默认一致)。
 * 绝不读未初始化局部量, 也绝不调 project_config_defaults() 改全局配置。 */
#define BB_FALLBACK_PRE_MS    2000u
#define BB_FALLBACK_POST_MS   1000u
/* Fix C / C5: 单帧 submit 的最大重试轮数(每轮含一次 flush + 10ms 睡眠) */
#define BB_SUBMIT_TRIES_MAX   20


/* ---------- 采样 ---------- */

static void bb_sample(bb_frame_t *f)
{
    sensor_frame_t s;

    if (sensor_service_get_latest(&s) != RT_EOK) return;
    if (s.seq == bb_last_seq_seen) return;          /* 无新帧不重复喂 */
    bb_last_seq_seen = s.seq;

    f->timestamp    = s.timestamp;
    f->sg           = s.sg_result;
    f->current_raw  = s.current_raw;
    f->current_ma   = (rt_int32_t)s.current_ma;     /* 帧内值, 不重复推 EMA */
    f->ax = s.ax; f->ay = s.ay; f->az = s.az; f->vib = s.vib_mg;
    f->step_hz      = s.step_hz;
    f->motor_state  = s.motor_state;
    f->dir          = s.dir;
    f->safety_state = (rt_uint8_t)safety_state_get();
    f->estop        = s.estop;  f->limit_min = s.limit_min;
    f->limit_max    = s.limit_max;  f->tmc_diag = s.tmc_diag;
    f->valid_imu    = s.valid_imu;
    f->valid_current= s.valid_current;
    f->valid_sg     = s.valid_sg;
    f->valid_safety = s.valid_safety;
    f->fault_code   = (bb_state == BB_POST_COLLECT) ? bb_session_fault_code : 0;
    f->seq          = s.seq;
}

/* ns_fault_sample 映射。
 * Fix C / C3 event 语义: 只有"因该故障而被记录"的帧才带 event = 故障码;
 *   pre 窗口的帧是该故障发生*之前*的历史上下文, 本身不是事件 → event = 0,
 *   靠 session_id 归组。旧实现把 pre 帧标成 NS_EVENT_TEST(=1), 既与
 *   FAULT_ESTOP(=1) 数值撞车, 又与 blackbox_selftest 的期望互相矛盾。
 * flags 位域: [7:0]=fault_code [11:8]=estop/lim_min/lim_max/diag
 *             [19:16]=motor_state [23:20]=safety_state
 * valid: SG/CURRENT/ACCEL/VIBRATION 按帧内 valid 位;
 *        STEP 表示 step_hz 字段有效(帧存在即有效), 不再借用为 safety。 */
static void bb_to_sample(const bb_frame_t *f, ns_fault_sample_t *s)
{
    rt_memset(s, 0, sizeof(*s));
    s->session_id = bb_session;
    s->time_ms    = f->timestamp;
    s->event      = f->fault_code;      /* 0 = pre 窗口上下文帧 */
    s->valid      = 0;
    s->sg         = f->sg;
    s->current_ma = f->current_ma;
    s->ax_mg      = f->ax; s->ay_mg = f->ay; s->az_mg = f->az;
    s->step_hz    = (rt_int32_t)f->step_hz;
    s->vibration_mg = f->vib;
    s->flags      = ((rt_uint32_t)(f->fault_code  & 0xFF) <<  0) |
                    ((rt_uint32_t)(f->estop       & 0x01) <<  8) |
                    ((rt_uint32_t)(f->limit_min   & 0x01) <<  9) |
                    ((rt_uint32_t)(f->limit_max   & 0x01) << 10) |
                    ((rt_uint32_t)(f->tmc_diag    & 0x01) << 11) |
                    ((rt_uint32_t)(f->motor_state & 0x0F) << 16) |
                    ((rt_uint32_t)(f->safety_state& 0x0F) << 20);
    if (f->valid_sg)      s->valid |= NS_VALID_SG;
    if (f->valid_current) s->valid |= NS_VALID_CURRENT;
    if (f->valid_imu)     s->valid |= NS_VALID_ACCEL | NS_VALID_VIBRATION;
    s->valid |= NS_VALID_STEP;
}

/* ---------- worker: 一次采样喂 pre 环 / post 区 + 触发 + 异步落盘 ----------
 * Fix C / C1: 每个循环只允许调用 bb_sample() 一次。它内部按"源帧序号未变化就
 * 返回空帧"去重, 同一循环调两次时第二次必然拿到空帧 —— 旧实现正是这么写的
 * (pre 环一次 + post 一次), 于是 post 窗口一个帧也收不到, 状态永久卡在
 * BB_POST_COLLECT, BB_WRITING 不可达, 故障记录从不落盘, blackbox_selftest
 * 必然在 20s 后报 not drained。
 * 现在: 采样 → 喂 pre 环 → 收 post → 处理触发 → 落盘。pre/post 由状态互斥,
 * 触发当帧只属于 pre 窗口(不重复、不丢失)。 */

/* Fix C / C4: 窗口同步。取不到快照就用编译期安全常量, 且绝不触碰全局配置。 */
static void bb_sync_window(void)
{
    project_config_t cs;

    if (project_config_get_snapshot(&cs) != RT_EOK)
    {
        /* 不读未初始化的 cs, 更不从 prio 18 worker 改全局 project config */
        cs.pre_fault_ms  = BB_FALLBACK_PRE_MS;
        cs.post_fault_ms = BB_FALLBACK_POST_MS;
    }

    bb_pre_eff  = cs.pre_fault_ms  / 10;
    bb_post_eff = cs.post_fault_ms / 10;
    if (bb_pre_eff  < 1) bb_pre_eff  = 1;
    if (bb_pre_eff  > BB_PRE_FRAMES)  bb_pre_eff  = BB_PRE_FRAMES;
    if (bb_post_eff < 1) bb_post_eff = 1;
    if (bb_post_eff > BB_POST_FRAMES) bb_post_eff = BB_POST_FRAMES;
}

/* 把已冻结的捕获区逐帧送进异步 logger。
 * Fix C / C5: 重试有上限且每轮睡眠; 超限则丢弃本会话余下帧并置 DEGRADED。
 * 旧实现 while(submit != EOK) { flush(50); } 在存储 online 但 accepting=0
 * (或队列长期不消化)时是纯空转, prio 18 忙等会饿死 tshell/ui。 */
static void bb_flush_capture(rt_uint32_t total)
{
    ns_fault_sample_t s;
    rt_uint32_t i;
    int tries, gave_up = 0;

    bb_last_err = RT_EOK;

    if (ns_storage_init() != RT_EOK)
    {
        rt_kprintf("[BB] storage unavailable, session %u dropped (%u frames)\n",
                   bb_session, total);
        bb_health = SUBSYS_DEGRADED;
        bb_write_dropped += total;
        return;
    }

    for (i = 0; i < total; ++i)
    {
        bb_to_sample(&bb_cap[i], &s);
        tries = 0;
        while ((bb_last_err = ns_log_submit(&s)) != RT_EOK)
        {
            if (++tries >= BB_SUBMIT_TRIES_MAX) { gave_up = 1; break; }
            ns_log_flush(50);           /* 排空队列再投 */
            rt_thread_mdelay(10);
        }
        if (gave_up)
        {
            bb_write_dropped += total - i;
            rt_kprintf("[BB] !! session %u submit FAILED at frame %u (err=%d,"
                       " %u tries), rest dropped=%u -> DEGRADED\n",
                       bb_session, i, (int)bb_last_err,
                       (unsigned)BB_SUBMIT_TRIES_MAX, bb_write_dropped);
            bb_health = SUBSYS_DEGRADED;
            return;
        }
        bb_written_total++;
        if ((i % 16) == 15) ns_log_flush(50);
    }

    ns_log_flush(500);
    rt_kprintf("[BB] session %u written (%u frames), last_err=%d\n",
               bb_session, total, (int)bb_last_err);
}

static void blackbox_entry(void *param)
{
    bb_frame_t f;
    rt_uint32_t i, pre_n;

    (void)param;

    while (1)
    {
        /* ---- 0. 生效窗口同步(config 快照 → 夹取到缓冲容量) ---- */
        bb_sync_window();

        /* ---- 1. 本循环唯一一次采样 ---- */
        rt_memset(&f, 0, sizeof(f));
        bb_sample(&f);

        /* ---- 2. 常态: 喂 pre 环 ---- */
        if (f.seq != 0 && bb_state == BB_IDLE)
        {
            bb_ring[bb_ring_idx] = f;
            bb_ring_idx = (rt_uint16_t)((bb_ring_idx + 1) % BB_PRE_FRAMES);
            if (bb_ring_count < BB_PRE_FRAMES) bb_ring_count++;
        }

        /* ---- 3. post 收集(接在捕获区 pre_n 之后; 与喂环互斥) ---- */
        if (f.seq != 0 && bb_state == BB_POST_COLLECT)
        {
            bb_cap[bb_post_idx++] = f;
            if (bb_post_idx >= bb_last_pre_n + bb_post_eff)
                bb_state = BB_WRITING;
        }

        /* ---- 4. 触发(Fix C / C2: 首故障优先, 含消费之前的窗口) ----
         * 放在采样与 post 收集之后: 触发当帧已进 pre 环, 属于故障前历史。 */
        if (bb_pending_flag)
        {
            if (bb_state == BB_IDLE)
            {
                bb_session++;
                pre_n = (bb_ring_count < bb_pre_eff) ? bb_ring_count : bb_pre_eff;
                /* 按时间顺序把 pre 环最后 pre_n 帧搬进捕获区前段 */
                for (i = 0; i < pre_n; ++i)
                    bb_cap[i] = bb_ring[(bb_ring_idx + BB_PRE_FRAMES - pre_n + i)
                                        % BB_PRE_FRAMES];
                bb_session_fault_code = bb_pending_code;  /* 锁存本会话故障码 */
                bb_last_pre_n = pre_n;
                bb_post_idx = pre_n;            /* post 帧接在 pre 段之后 */
                bb_state = BB_POST_COLLECT;
                rt_kprintf("[BB] trigger code=%u session=%u pre_n=%u post_eff=%u\n",
                           bb_session_fault_code, bb_session, pre_n, bb_post_eff);
            }
            else
            {
                bb_trig_dropped++;              /* busy: 只丢弃, 不改 session code */
                rt_kprintf("[BB] trigger DROPPED (busy, dropped=%u)\n",
                           bb_trig_dropped);
            }
            bb_pending_flag = RT_FALSE;         /* 无论接受与否都消费标志 */
        }

        /* ---- 5. 异步落盘(有界重试) ---- */
        if (bb_state == BB_WRITING)
        {
            bb_flush_capture((rt_uint32_t)bb_post_idx);
            bb_state = BB_IDLE;                 /* 回到常态采样 */
            bb_post_idx = 0;                    /* 下一会话从头使用捕获区 */
            bb_last_pre_n = 0;
        }

        rt_thread_mdelay(10);                   /* 100Hz */
    }
}

/* ---------- 正式 API (blackbox.h) ---------- */

/* 故障触发(O(1), Safety Thread / 状态机调用): 只置标志, 零等待零 Flash。
 * Fix C / C2 首故障优先: pending 标志还没被 worker 消费时, 后来的触发绝不
 * 覆盖首个故障码 —— 旧实现在这个窗口里会用 B 顶掉 A(两次触发都发生在消费前
 * 是真实场景: 多源同时判 severe / EXTI 抖动连发), 落盘的是 B 而 A 被静默丢弃。
 * "已进入捕获/写盘"之后的丢弃由 worker 侧计数(bb_trig_dropped), 这里计数
 * 消费之前的到达(bb_trig_early_dropped)。 */
void blackbox_trigger(rt_uint32_t fault_code)
{
    rt_base_t level = rt_hw_interrupt_disable();  /* P1-2: flag+code 对的最小临界 */

    if (!bb_pending_flag)
    {
        bb_pending_code = fault_code;
        bb_pending_flag = RT_TRUE;
    }
    else
    {
        bb_trig_early_dropped++;      /* 首故障已锁存, 本次不覆盖 */
    }

    rt_hw_interrupt_enable(level);
}

rt_err_t blackbox_init(void)
{
    if (bb_tid != RT_NULL) return RT_EOK;       /* 幂等 */

    rt_memset(bb_ring, 0, sizeof(bb_ring));
    rt_memset(bb_cap, 0, sizeof(bb_cap));
    bb_state = BB_IDLE;
    bb_ring_idx = 0; bb_ring_count = 0;
    bb_post_idx = 0; bb_last_pre_n = 0;
    bb_last_seq_seen = 0;

    bb_tid = rt_thread_create("bblog", blackbox_entry, RT_NULL,
                              1024, 18, 10);    /* prio 18: 低于所有实时服务 */
    if (bb_tid == RT_NULL) { bb_health = SUBSYS_FAILED; return -RT_ERROR; }
    rt_thread_startup(bb_tid);

    bb_health = SUBSYS_OK;
    rt_kprintf("[BB] blackbox ready (ring=%u cap=%u frames @100Hz, prio 18)\n",
               BB_PRE_FRAMES, BB_CAP_FRAMES);
    return RT_EOK;
}

subsys_health_t blackbox_get_health(void) { return bb_health; }

/* ---------- MSH ---------- */

static void blackbox_status(void)
{
    ns_log_stats_t st;

    rt_kprintf("[BB] state=%d session=%u written=%u last_err=%d health=%s\n",
               (int)bb_state, bb_session, bb_written_total,
               (int)bb_last_err, subsys_health_name(bb_health));
    rt_kprintf("[BB] trigger drops: early(pre-consumption)=%u busy(capturing)=%u\n",
               bb_trig_early_dropped, bb_trig_dropped);
    rt_kprintf("[BB] write drops=%u frames (submit retries exhausted)\n",
               bb_write_dropped);
    rt_kprintf("[BB] window: pre_eff=%u post_eff=%u frames (pre_n_latched=%u)\n",
               bb_pre_eff, bb_post_eff, bb_last_pre_n);
    if (ns_log_stats(&st) == RT_EOK)
        rt_kprintf("[BB] flash: used=%u valid=%u bad=%u acc=%u done=%u fail=%u drop=%u\n",
                   st.used_slots, st.valid_records, st.bad_records,
                   st.accepted, st.completed, st.failed, st.dropped);
}
MSH_CMD_EXPORT(blackbox_status, show blackbox capture and flash status);

static void blackbox_dump(int argc, char **argv)
{
    ns_fault_sample_t s;
    rt_uint32_t seq, slot, shown = 0, i;
    ns_log_stats_t st;
    int max = 10;

    if (ns_log_stats(&st) != RT_EOK) { rt_kprintf("[BB] stats failed\n"); return; }
    if (argc == 2) max = atoi(argv[1]);
    if (max > 50) max = 50;

    rt_kprintf("[BB] dump last %d of used=%u:\n", max, st.used_slots);
    for (i = 0; i < st.used_slots && shown < (rt_uint32_t)max; ++i)
    {
        slot = (st.used_slots - 1 - i) % NS_LOG_SLOTS;
        if (ns_log_read_slot(slot, &s, &seq) != RT_EOK) continue;
        rt_kprintf("[BB] slot=%u ses=%u t=%u ev=%u valid=%02X flags=%08X sg=%d ma=%d vib=%d hz=%d\n",
                   slot, s.session_id, s.time_ms, s.event, s.valid, s.flags,
                   (int)s.sg, (int)s.current_ma, (int)s.vibration_mg, (int)s.step_hz);
        shown++;
    }
    rt_kprintf("[BB] dump done (%u shown)\n", shown);
}
MSH_CMD_EXPORT(blackbox_dump, dump recent blackbox records from flash);

static void blackbox_clear(void)
{
    rt_kprintf("[BB] clearing event log (destructive)...\n");
    if (ns_log_clear() == RT_EOK) rt_kprintf("[BB] clear OK\n");
    else rt_kprintf("[BB] clear FAILED (rerun if interrupted)\n");
}
MSH_CMD_EXPORT(blackbox_clear, destructive clear of event log partition);

/* 软件自检: 真实触发 → 等待(session 递增 + 本会话真的写了帧 + 回 IDLE)
 * → 落盘回读核对。会真实写 Flash 事件分区, 需要 Flash 在位。
 * 软件级验证, 非整机硬件验证。
 *
 * Fix C 修正的三点:
 *   - 旧实现把 B 触发放在 A 之后 10ms, 那时 worker 多半已经消费掉 A 的 pending,
 *     所以它测的是"捕获中丢弃"(bb_trig_dropped), 而不是"C2 消费之前不覆盖"。
 *     现在 A、B 背靠背触发(测早到窗口), 另外补一个 C 测捕获/写盘忙窗口。
 *     注: bblog(18) 优先级高于 tshell(20), 两次调用之间 worker 仍可能抢先消费,
 *     所以"早到丢弃"与"忙时丢弃"具体计在哪一边是不确定的 —— 断言只要求
 *     两个窗口合计至少丢弃一次, 并且任何记录都不得带 B/C 的故障码。
 *   - 旧等待条件 bb_written_total > 0 会被*历史*会话满足; 现在要求本会话增量。
 *   - 旧断言"本会话全部记录 event == FAULT_SOFT"与 pre 窗口记录的语义矛盾
 *     (pre 帧 event 恒为 NS_EVENT_TEST), 只要 pre_n>0 必挂。现在按 pre(=0)/
 *     post(=触发码)分别断言, 并单独断言"绝不允许出现 B/C 的故障码"。 */
static void blackbox_selftest(void)
{
    ns_fault_sample_t s;
    ns_log_stats_t st0, st1;
    rt_uint32_t seq, before, after, i, sess0, written0, drop0, edrop0;
    rt_uint32_t checked = 0, pre_recs = 0, post_recs = 0, bad = 0, pollute = 0;
    int pass = 1, found = 0;
    unsigned long wait;

    if (blackbox_init() != RT_EOK) { rt_kprintf("[BB-ST] init failed\n"); return; }
    if (ns_storage_init() != RT_EOK)
    { rt_kprintf("[BB-ST] SKIP: storage not available\n"); return; }
    if (ns_log_stats(&st0) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    before  = st0.valid_records;
    sess0   = bb_session;
    written0 = bb_written_total;
    drop0    = bb_trig_dropped;
    edrop0   = bb_trig_early_dropped;

    rt_kprintf("[BB-ST] A=SOFT then B=LIM_MIN back-to-back (pre-consumption window),"
               " waiting for session %u...\n", sess0 + 1);
    blackbox_trigger(FAULT_SOFT);         /* A: 首故障, 必须胜出 */
    blackbox_trigger(FAULT_LIMIT_MIN);    /* B: 消费之前到达 -> 必须被丢弃 */

    /* C: 落在捕获/写盘的忙窗口(post 窗口要 ~1s, 50ms 时必然仍在采集) */
    rt_thread_mdelay(50);
    blackbox_trigger(FAULT_ESTOP);

    /* 等本会话真的写完: session 递增 + 回到 IDLE + 落盘帧数有增量, 最多 20s */
    for (wait = 0; wait < 2000u; ++wait)
    {
        if (bb_session >= sess0 + 1 && bb_state == BB_IDLE &&
            bb_written_total > written0)
            break;
        rt_thread_mdelay(10);
    }
    if (bb_session < sess0 + 1 || bb_state != BB_IDLE)
    { rt_kprintf("[BB-ST] FAIL: not drained (state=%d written=%u/%u)\n",
                 (int)bb_state, bb_written_total, written0); return; }
    if (bb_written_total <= written0)
    { rt_kprintf("[BB-ST] FAIL: session wrote 0 frames (post window stuck?)\n");
      return; }

    if (ns_log_stats(&st1) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    after = st1.valid_records;
    rt_kprintf("[BB-ST] valid records: %u -> %u (this session wrote %u)\n",
               before, after, bb_written_total - written0);
    if (after <= before) { rt_kprintf("[BB-ST] FAIL: no new records\n"); return; }

    /* 回读本会话全部记录: pre 帧 event==0, post 帧 event==FAULT_SOFT;
     * 任何其它取值(尤其是 B 的 FAULT_LIMIT_MIN)都说明首故障被污染。 */
    for (i = 0; i < st1.used_slots; ++i)
    {
        rt_uint32_t slot = (st1.used_slots - 1 - i) % NS_LOG_SLOTS;
        if (ns_log_read_slot(slot, &s, &seq) != RT_EOK) continue;
        if (s.session_id != bb_session) continue;
        found = 1; checked++;
        if (s.event == 0)            pre_recs++;
        else if (s.event == FAULT_SOFT) post_recs++;
        else { bad++;
               if (s.event == FAULT_LIMIT_MIN || s.event == FAULT_ESTOP) pollute++; }
        if (checked <= 2 || (s.event != 0 && s.event != FAULT_SOFT))
            rt_kprintf("[BB-ST] readback ses=%u ev=%u valid=%02X flags=%08X\n",
                       s.session_id, s.event, s.valid, s.flags);
    }

    if (!found)
    { rt_kprintf("[BB-ST] FAIL: session records not found\n"); pass = 0; }
    if (bad)
    { rt_kprintf("[BB-ST] FAIL: %u record(s) with unexpected event"
                 " (B pollution=%u)\n", bad, pollute); pass = 0; }
    if (!post_recs)
    { rt_kprintf("[BB-ST] FAIL: no post-fault record carries the trigger code\n");
      pass = 0; }
    /* 首故障优先必须真的计了数(两个窗口之一) */
    if (bb_trig_early_dropped <= edrop0 && bb_trig_dropped <= drop0)
    { rt_kprintf("[BB-ST] FAIL: second trigger was neither early-dropped"
                 " nor busy-dropped\n"); pass = 0; }
    rt_kprintf("[BB-ST] records=%u pre(event0)=%u post(event=SOFT)=%u"
               " early_drop=%u busy_drop=%u write_dropped=%u\n",
               checked, pre_recs, post_recs,
               bb_trig_early_dropped, bb_trig_dropped, bb_write_dropped);
    if (pre_recs == 0)
        rt_kprintf("[BB-ST] NOTE: no pre-window record (sensor had no fresh frame"
                   " before trigger) - pre semantics not covered this run\n");

    rt_kprintf("[BB-ST] %s\n", pass ? "PASS (software+flash path)" : "FAILED");
}
MSH_CMD_EXPORT(blackbox_selftest, capture-trigger-write-readback selftest);
