/*
 * blackbox.c - 故障黑匣子 (Phase 7-D, 审查修复版)
 *
 * 修复记录(Phase 7 review):
 *   P0-1 旧实现把 pre 200 帧拷进 bb_post[100] 数组 → 确定性越界。
 *        现改为单一捕获区 bb_cap[BB_CAP_FRAMES](编译期断言) +
 *        独立 pre 环 bb_ring; pre 不足时只写实际帧数(开机前 2s 不造假历史)。
 *   触发策略: 捕获/写盘中新触发忽略并计数(首个故障优先)。
 *   selftest 竞态修复: 等待条件改为 session 递增且状态回到 IDLE。
 *   数字输入持久化: estop/limit_min/limit_max/diag 存 record.flags 位域。
 *   NS_VALID_STEP 不再借用为 valid_safety —— 数字输入在 flags 位域,
 *   NS_VALID_STEP 表示 step_hz 字段有效。
 */

#include <rtthread.h>
#include <rtdevice.h>
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
    rt_uint32_t fault_code;      /* 触发时锁存的故障码(post 区帧有效) */
    rt_uint32_t seq;
} bb_frame_t;

/* 静态内存: ring 300×36B + cap 450×36B ≈ 27KB BSS, 零 malloc */
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
static rt_uint32_t bb_trig_dropped = 0;     /* 捕获中忽略的新触发数 */
static rt_err_t bb_last_err = RT_EOK;
static rt_uint32_t bb_written_total = 0;
static rt_thread_t bb_tid = RT_NULL;
static subsys_health_t bb_health = SUBSYS_UNINIT;
static rt_uint32_t bb_last_seq_seen = 0;
/* 生效窗口(从 config 派生并夹取到缓冲容量), worker 每循环同步 */
static rt_uint32_t bb_pre_eff = 200, bb_post_eff = 100;

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
 * flags 位域: [7:0]=fault_code [11:8]=estop/lim_min/lim_max/diag
 *             [19:16]=motor_state [23:20]=safety_state
 * valid: SG/CURRENT/ACCEL/VIBRATION 按帧内 valid 位;
 *        STEP 表示 step_hz 字段有效(帧存在即有效), 不再借用为 safety。 */
static void bb_to_sample(const bb_frame_t *f, ns_fault_sample_t *s)
{
    rt_memset(s, 0, sizeof(*s));
    s->session_id = bb_session;
    s->time_ms    = f->timestamp;
    s->event      = f->fault_code ? f->fault_code : NS_EVENT_TEST;
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

/* ---------- worker: 喂环 + post 收集 + 异步落盘 ---------- */

static void blackbox_entry(void *param)
{
    bb_frame_t f;
    ns_fault_sample_t s;
    rt_err_t e;
    rt_uint32_t i, pre_n;

    (void)param;

    while (1)
    {
        /* ---- 0. 生效窗口同步(config 快照 → 夹取到容量) ---- */
        project_config_t cs;
        const project_config_t *c = &cs;

        if (project_config_get_snapshot(&cs) != RT_EOK)
            project_config_defaults();          /* 拿不到快照(不应发生)→默认窗口 */
        bb_pre_eff  = c->pre_fault_ms  / 10;
        bb_post_eff = c->post_fault_ms / 10;
        if (bb_pre_eff  < 1) bb_pre_eff  = 1;
        if (bb_pre_eff  > BB_PRE_FRAMES)  bb_pre_eff  = BB_PRE_FRAMES;
        if (bb_post_eff < 1) bb_post_eff = 1;
        if (bb_post_eff > BB_POST_FRAMES) bb_post_eff = BB_POST_FRAMES;

        /* ---- 1. 采样入 pre 环(常态) ---- */
        rt_memset(&f, 0, sizeof(f));
        bb_sample(&f);
        if (f.seq != 0 && bb_state == BB_IDLE)
        {
            bb_ring[bb_ring_idx] = f;
            bb_ring_idx = (rt_uint16_t)((bb_ring_idx + 1) % BB_PRE_FRAMES);
            if (bb_ring_count < BB_PRE_FRAMES) bb_ring_count++;
        }

        /* ---- 2. 触发(策略: 捕获/写盘中忽略新触发, 首个故障优先) ---- */
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
                rt_kprintf("[BB] trigger code=%u session=%u pre_n=%u\n",
                           bb_session_fault_code, bb_session, pre_n);
            }
            else
            {
                bb_trig_dropped++;              /* busy: 只丢弃, 不改 session code */
                rt_kprintf("[BB] trigger DROPPED (busy, dropped=%u)\n",
                           bb_trig_dropped);
            }
            bb_pending_flag = RT_FALSE;         /* 无论接受与否都消费标志 */
        }

        /* ---- 3. post 收集(接在捕获区 pre_n 之后) ---- */
        if (bb_state == BB_POST_COLLECT)
        {
            rt_memset(&f, 0, sizeof(f));
            bb_sample(&f);
            if (f.seq != 0)
            {
                bb_cap[bb_post_idx++] = f;
                if (bb_post_idx >= bb_last_pre_n + bb_post_eff)
                    bb_state = BB_WRITING;
            }
        }

        /* ---- 4. 异步落盘: 逐帧 submit, 队列满→flush 重试(节流) ---- */
        if (bb_state == BB_WRITING)
        {
            rt_uint32_t total = bb_post_idx;
            bb_last_err = RT_EOK;
            if (ns_storage_init() != RT_EOK)
            {
                rt_kprintf("[BB] storage unavailable, session %u dropped\n",
                           bb_session);
                bb_health = SUBSYS_DEGRADED;
            }
            else
            {
                for (i = 0; i < total; ++i)
                {
                    bb_to_sample(&bb_cap[i], &s);
                    while ((e = ns_log_submit(&s)) != RT_EOK)
                    {
                        bb_last_err = e;
                        ns_log_flush(50);       /* 排空队列再投, 节流 */
                    }
                    bb_written_total++;
                    if ((i % 16) == 15) ns_log_flush(50);
                }
                ns_log_flush(500);
                rt_kprintf("[BB] session %u written (%u frames), last_err=%d\n",
                           bb_session, total, bb_last_err);
            }
            bb_state = BB_IDLE;                 /* 回到常态采样 */
        }

        rt_thread_mdelay(10);                   /* 100Hz */
    }
}

/* ---------- 正式 API (blackbox.h) ---------- */

void blackbox_trigger(rt_uint32_t fault_code)
{
    rt_base_t level = rt_hw_interrupt_disable();  /* P1-2: flag+code 对的最小临界 */
    bb_pending_code = fault_code;
    bb_pending_flag = RT_TRUE;
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

    rt_kprintf("[BB] state=%d session=%u written=%u dropped_trig=%u last_err=%d health=%s\n",
               (int)bb_state, bb_session, bb_written_total, bb_trig_dropped,
               (int)bb_last_err, subsys_health_name(bb_health));
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

/* 软件自检: 真实触发 → 等待(session 递增且回 IDLE)→ 落盘回读核对。
 * 会真实写 Flash 事件分区; 需要 Flash 在位。软件级验证, 非整机硬件验证。 */
static void blackbox_selftest(void)
{
    ns_fault_sample_t s;
    ns_log_stats_t st0, st1;
    rt_uint32_t seq, before, after, i, sess0, wait;
    int pass = 1, found = 0;

    if (blackbox_init() != RT_EOK) { rt_kprintf("[BB-ST] init failed\n"); return; }
    if (ns_storage_init() != RT_EOK)
    { rt_kprintf("[BB-ST] SKIP: storage not available\n"); return; }
    if (ns_log_stats(&st0) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    before = st0.valid_records;
    sess0 = bb_session;

    rt_kprintf("[BB-ST] trigger A (fault=SOFT), waiting for session %u...\n",
               sess0 + 1);
    blackbox_trigger(FAULT_SOFT);
    /* P1-2: busy 期间触发 B —— 应被丢弃, 不污染 session fault code */
    rt_thread_mdelay(10);
    blackbox_trigger(FAULT_LIMIT_MIN);

    /* 竞态修复: 等 session 递增且状态回到 IDLE(写完), 最多 20s */
    for (wait = 0; wait < 2000; ++wait)
    {
        if (bb_session >= sess0 + 1 && bb_state == BB_IDLE &&
            bb_written_total > 0)
            break;
        rt_thread_mdelay(10);
    }
    if (bb_session < sess0 + 1 || bb_state != BB_IDLE)
    { rt_kprintf("[BB-ST] FAIL: not drained (state=%d)\n", (int)bb_state); return; }

    if (ns_log_stats(&st1) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    after = st1.valid_records;
    rt_kprintf("[BB-ST] valid records: %u -> %u\n", before, after);
    if (after <= before) { rt_kprintf("[BB-ST] FAIL: no new records\n"); return; }

    /* 回读本 session 全部记录: 核对 event 全 == 触发码 A(FAULT_SOFT=9),
     * 并验证 busy 期间的 B 触发(FAULT_LIMIT_MIN)只被丢弃不污染 */
    rt_uint32_t checked = 0, bad = 0;
    for (i = 0; i < st1.used_slots; ++i)
    {
        rt_uint32_t slot = (st1.used_slots - 1 - i) % NS_LOG_SLOTS;
        if (ns_log_read_slot(slot, &s, &seq) != RT_EOK) continue;
        if (s.session_id != bb_session) continue;
        found = 1; checked++;
        if (s.event != FAULT_SOFT) bad++;
        if (checked <= 2)
            rt_kprintf("[BB-ST] readback ses=%u ev=%u valid=%02X flags=%08X\n",
                       s.session_id, s.event, s.valid, s.flags);
    }
    if (!found) { rt_kprintf("[BB-ST] FAIL: session records not found\n"); pass = 0; }
    else if (bad) { rt_kprintf("[BB-ST] FAIL: %u records polluted\n", bad); pass = 0; }

    rt_kprintf("[BB-ST] %s\n", pass ? "PASS (software+flash path)" : "FAILED");
}
MSH_CMD_EXPORT(blackbox_selftest, capture-trigger-write-readback selftest);
