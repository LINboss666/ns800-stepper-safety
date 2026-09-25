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
                /* Fix E 后的兜底: trigger 侧已按 bb_state != BB_IDLE 丢弃,
                 * 正常不会再有"忙时才发现 pending"的情况。保留这道同语义
                 * 判定, 因为 bb_state 是跨线程 volatile, 不依赖"绝不可能"。 */
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
 *
 * 首故障优先 —— 一个关中断临界内同时判 pending 标志与 worker 状态(Fix E):
 *   - bb_pending_flag 已置: A 的码已锁存但还没被消费 → 绝不覆盖(early_drop)。
 *   - bb_state != BB_IDLE: 正在采集 post 或正在写盘 → 丢弃且**不留 pending**。
 *     旧实现在这里漏判: pending 已被 worker 消费成 FALSE 时, B 会被写进
 *     bb_pending_*; 等 A 落盘完成、worker 把状态清回 BB_IDLE 后的那一步,
 *     B 就被接受成第二个 session —— 违反"捕获/写盘期间后续触发必须丢弃"。
 *   - 两者都不成立才接受为首故障。
 * 计数分工: 消费前到达计 bb_trig_early_dropped; 因捕获/写盘在忙而丢弃计
 * bb_trig_dropped(worker 侧另有一道同语义的兜底判定)。 */
void blackbox_trigger(rt_uint32_t fault_code)
{
    rt_base_t level = rt_hw_interrupt_disable();  /* flag + state + code 最小临界 */

    if (bb_pending_flag)
    {
        bb_trig_early_dropped++;      /* 首故障已锁存待消费, 本次不覆盖 */
    }
    else if (bb_state != BB_IDLE)
    {
        bb_trig_dropped++;            /* 捕获/写盘忙: 丢弃, 不产生第二 session */
    }
    else
    {
        bb_pending_code = fault_code;
        bb_pending_flag = RT_TRUE;
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

/* 软件自检: 真实触发 → 等待(本会话真的写完 + 回 IDLE)→ 落盘回读核对。
 * 会真实写 Flash 事件分区, 需要 Flash 在位。软件级验证, 非整机硬件验证。
 *
 * 覆盖的四个语义(Fix C 引入, Fix E 补齐 WRITING 窗口):
 *   1) A/B 背靠背触发 → "pending 未消费"窗口不覆盖首故障码(early_drop)。
 *   2) worker 进入忙状态后再触发 C → 必须 busy_drop, 且**不得留下 pending**。
 *      关键断言是第 3 条: A 落盘完成、状态清回 IDLE 之后, 只允许出现
 *      sess0+1 这一个 session —— 旧实现漏判 bb_state 时, C 会在下一拍被
 *      接受成第二个 session, 只测 POST_COLLECT 不测这条路径就发现不了。
 *   3) 排空后 bb_session 必须恰为 sess0+1(第二 session 即回归)。
 *   4) 记录语义: pre 帧 event==0, post 帧 event==FAULT_SOFT,
 *      任何 B/C/D 的故障码出现在记录里都算污染。
 *
 * 说明两处诚实的不确定性:
 *   - bblog(18) 优先级高于 tshell(20), A/B 两次调用之间 worker 仍可能抢先消费,
 *     所以第 1 条只能断言"早到/忙时两个计数器合计至少加一", 不断言具体哪边。
 *   - BB_WRITING 的时长取决于 Flash 与 logger 队列, 轮询观测到就顺带覆盖
 *     (POST_COLLECT 与 WRITING 走 trigger 里同一个 bb_state != BB_IDLE 分支,
 *      所以第 2 条在 POST_COLLECT 上是确定性覆盖); 观测不到则如实报 NOTE。
 *   - 旧实现把 C 触发固定放在 A 之后 50ms, 既没断言"必须 busy_drop", 也没检查
 *     会不会长出第二个 session, 等于没测这条规则。 */
static void blackbox_selftest(void)
{
    ns_fault_sample_t s;
    ns_log_stats_t st0, st1;
    rt_uint32_t seq, before, after, i, sess0, written0, drop0, edrop0;
    rt_uint32_t drop_before, drop_after, seen_state;
    rt_uint32_t checked = 0, pre_recs = 0, post_recs = 0, bad = 0, pollute = 0;
    int pass = 1, found = 0, writing_covered = 0;
    unsigned long wait;

    if (blackbox_init() != RT_EOK) { rt_kprintf("[BB-ST] init failed\n"); return; }
    if (ns_storage_init() != RT_EOK)
    { rt_kprintf("[BB-ST] SKIP: storage not available\n"); return; }
    if (ns_log_stats(&st0) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    before   = st0.valid_records;
    sess0    = bb_session;
    written0 = bb_written_total;
    drop0    = bb_trig_dropped;
    edrop0   = bb_trig_early_dropped;

    rt_kprintf("[BB-ST] A=SOFT B=LIM_MIN back-to-back; then busy-window C and D\n");
    blackbox_trigger(FAULT_SOFT);         /* A: 首故障, 必须胜出 */
    blackbox_trigger(FAULT_LIMIT_MIN);    /* B: 未消费窗口到达 -> 不得覆盖 */

    /* ---- 覆盖 2: 等 worker 进入忙状态, 再触发 C ---- */
    for (wait = 0; wait < 500u; ++wait)
    {
        if (bb_state != BB_IDLE) break;
        rt_thread_mdelay(1);
    }
    seen_state = (rt_uint32_t)bb_state;
    if (seen_state == BB_IDLE)
    { rt_kprintf("[BB-ST] FAIL: never observed busy state (session=%u written=%u)\n",
                 bb_session, bb_written_total); pass = 0; }
    else
    {
        drop_before = bb_trig_dropped;
        blackbox_trigger(FAULT_ESTOP);            /* C: 忙窗口触发(多为 POST_COLLECT) */
        rt_thread_mdelay(2);
        drop_after = bb_trig_dropped;
        rt_kprintf("[BB-ST] C in state=%u: busy_drop %u -> %u\n",
                   (int)seen_state, drop_before, drop_after);
        if (drop_after <= drop_before)
        { rt_kprintf("[BB-ST] FAIL: busy trigger C was NOT dropped"
                     " (would create a second session)\n"); pass = 0; }
    }

    /* ---- 覆盖 2b: 尽量抓一次 BB_WRITING(同一分支, 观测不到只报 NOTE) ---- */
    for (wait = 0; wait < 3000u; ++wait)
    {
        if (bb_state == BB_WRITING)
        {
            drop_before = bb_trig_dropped;
            blackbox_trigger(FAULT_TMC_COMM);     /* D: 正在写 Flash 时到达 */
            rt_thread_mdelay(2);
            writing_covered = (bb_trig_dropped > drop_before) ? 1 : -1;
            if (writing_covered < 0)
            { rt_kprintf("[BB-ST] FAIL: trigger D during BB_WRITING not dropped\n");
              pass = 0; writing_covered = 0; }
            else rt_kprintf("[BB-ST] D in BB_WRITING: dropped OK\n");
            break;
        }
        if (bb_state == BB_IDLE && bb_written_total > written0) break;  /* 已排空 */
        rt_thread_mdelay(1);
    }
    if (!writing_covered)
        rt_kprintf("[BB-ST] NOTE: BB_WRITING not observed this run (flash too fast?)"
                   " - same guard branch as POST_COLLECT\n");

    /* ---- 覆盖 3: 排空 + 余量, 然后要求 session 计数恰好 +1 ---- */
    for (wait = 0; wait < 2500u; ++wait)
    {
        if (bb_session >= sess0 + 1 && bb_state == BB_IDLE &&
            bb_written_total > written0)
            break;
        rt_thread_mdelay(10);
    }
    rt_thread_mdelay(200);     /* 若 C/D 被误留成 pending, 这里会长出第二个 session */

    if (bb_state != BB_IDLE)
    { rt_kprintf("[BB-ST] FAIL: not drained (state=%d written=%u/%u)\n",
                 (int)bb_state, bb_written_total, written0); return; }
    if (bb_written_total <= written0)
    { rt_kprintf("[BB-ST] FAIL: session wrote 0 frames (post window stuck?)\n");
      return; }
    if (bb_session != sess0 + 1)
    { rt_kprintf("[BB-ST] FAIL: session went %u -> %u, expected exactly +1"
                 " (busy-window trigger leaked a second capture)\n",
                 sess0, bb_session); pass = 0; }
    else
        rt_kprintf("[BB-ST] session exactly +1 (%u -> %u): first-fault-wins held\n",
                   sess0, bb_session);

    if (ns_log_stats(&st1) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    after = st1.valid_records;
    rt_kprintf("[BB-ST] valid records: %u -> %u (this session wrote %u)\n",
               before, after, bb_written_total - written0);
    if (after <= before) { rt_kprintf("[BB-ST] FAIL: no new records\n"); return; }

    /* ---- 覆盖 4: 回读按 pre/post 分类, 外来故障码算污染 ----
     * 明确按 sess0+1(A 那一轮)过滤, 而不是当前的 bb_session: 万一上面那条
     * "恰好 +1"断言失败真的多长出一个 session, 这里仍会核对 A 的记录。 */
    for (i = 0; i < st1.used_slots; ++i)
    {
        rt_uint32_t slot = (st1.used_slots - 1 - i) % NS_LOG_SLOTS;
        if (ns_log_read_slot(slot, &s, &seq) != RT_EOK) continue;
        if (s.session_id != sess0 + 1) continue;
        found = 1; checked++;
        if (s.event == 0)                 pre_recs++;
        else if (s.event == FAULT_SOFT)   post_recs++;
        else
        {
            bad++;
            if (s.event == FAULT_LIMIT_MIN || s.event == FAULT_ESTOP ||
                s.event == FAULT_TMC_COMM)
                pollute++;
        }
        if (checked <= 2 || (s.event != 0 && s.event != FAULT_SOFT))
            rt_kprintf("[BB-ST] readback ses=%u ev=%u valid=%02X flags=%08X\n",
                       s.session_id, s.event, s.valid, s.flags);
    }

    if (!found)
    { rt_kprintf("[BB-ST] FAIL: session records not found\n"); pass = 0; }
    if (bad)
    { rt_kprintf("[BB-ST] FAIL: %u record(s) with unexpected event"
                 " (B/C/D pollution=%u)\n", bad, pollute); pass = 0; }
    if (!post_recs)
    { rt_kprintf("[BB-ST] FAIL: no post-fault record carries the trigger code\n");
      pass = 0; }
    /* 覆盖 1: A/B 背靠背必须至少丢掉一次(早到或忙, 见上说不确定性) */
    if (bb_trig_early_dropped <= edrop0 && bb_trig_dropped <= drop0)
    { rt_kprintf("[BB-ST] FAIL: trigger B was neither early- nor busy-dropped\n");
      pass = 0; }
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
