/*
 * blackbox.c - 故障黑匣子 (Phase 7-D)
 *
 * 线程(单一, prio 18): 10ms 轮询最新 sensor_frame 喂 pre 环;
 * 触发后切 post 收集; 收满后逐帧 ns_log_submit(队列满→flush 重试),
 * 最后 ns_log_flush 保证落盘。Safety Thread 只置标志, 永不等待 Flash。
 *
 * 触发源: safety_force_shutdown(所有故障路径统一入口) + blackbox_selftest。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "sensor_service.h"
#include "safety_state.h"
#include "ns_storage.h"
#include "blackbox.h"

typedef struct
{
    rt_uint32_t timestamp;
    rt_uint16_t sg;
    rt_uint32_t current_raw;
    rt_int32_t  current_ma;      /* ×1000 保留定点? 直接存 float 的 int 截断值 */
    rt_int16_t  ax, ay, az, vib;
    rt_uint32_t step_hz;
    rt_uint8_t  motor_state, dir;
    rt_uint8_t  safety_state;
    rt_uint8_t  estop, limit_min, limit_max, tmc_diag;
    rt_uint8_t  valid_imu, valid_current, valid_sg, valid_safety;
    rt_uint32_t fault_code;      /* 触发时锁存的故障码(post 区帧有效) */
    rt_uint32_t seq;
} bb_frame_t;

/* 静态内存: (200+100) × 36B ≈ 10.8KB BSS, 零 malloc */
static bb_frame_t bb_pre[BB_PRE_FRAMES];
static bb_frame_t bb_post[BB_POST_FRAMES];

typedef enum { BB_IDLE = 0, BB_POST_COLLECT, BB_WRITING } bb_state_t;

static volatile bb_state_t bb_state = BB_IDLE;
static volatile rt_uint32_t bb_trig_code = 0;
static volatile rt_bool_t bb_trig_flag = RT_FALSE;
static rt_uint16_t bb_pre_idx = 0;
static rt_uint16_t bb_post_idx = 0;
static rt_uint32_t bb_session = 0;
static rt_err_t bb_last_err = RT_EOK;
static rt_uint32_t bb_written_total = 0;
static rt_thread_t bb_tid = RT_NULL;
static subsys_health_t bb_health = SUBSYS_UNINIT;
static rt_uint32_t bb_last_seq_seen = 0;

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
    f->fault_code   = (bb_state == BB_POST_COLLECT) ? bb_trig_code : 0;
    f->seq          = s.seq;
}

/* ns_fault_sample 映射: valid 位 → NS_VALID_*; 无效字段保留旧值并如实标 absent */
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
    s->flags      = ((rt_uint32_t)f->safety_state << 24) |
                    ((rt_uint32_t)f->motor_state << 16) | (rt_uint32_t)f->fault_code;
    if (f->valid_sg)      s->valid |= NS_VALID_SG;
    if (f->valid_current) s->valid |= NS_VALID_CURRENT;
    if (f->valid_imu)     s->valid |= NS_VALID_ACCEL | NS_VALID_VIBRATION;
    if (f->valid_safety)  s->valid |= NS_VALID_STEP;   /* 数字输入齐全性借用该位 */
}

/* ---------- worker: 喂环 + post 收集 + 异步落盘 ---------- */

static void blackbox_entry(void *param)
{
    bb_frame_t f;
    ns_fault_sample_t s;
    rt_err_t e;
    rt_uint32_t pre_saved_idx, i;

    (void)param;

    while (1)
    {
        /* ---- 1. 采样入 pre 环(常态) ---- */
        rt_memset(&f, 0, sizeof(f));
        bb_sample(&f);
        if (f.seq != 0 && bb_state == BB_IDLE)
        {
            bb_pre[bb_pre_idx] = f;
            bb_pre_idx = (rt_uint16_t)((bb_pre_idx + 1) % BB_PRE_FRAMES);
        }

        /* ---- 2. 触发检测(O(1) 标志由 Safety 置位) ---- */
        if (bb_trig_flag && bb_state == BB_IDLE)
        {
            bb_session++;
            pre_saved_idx = bb_pre_idx;         /* 最老帧位置 */
            /* 冻结: 把 pre 环按时间顺序搬到 post 区前段复用 */
            for (i = 0; i < BB_PRE_FRAMES; ++i)
                bb_post[i] = bb_pre[(pre_saved_idx + i) % BB_PRE_FRAMES];
            bb_post_idx = BB_PRE_FRAMES;        /* post 从环尾继续追加 */
            bb_state = BB_POST_COLLECT;
            bb_trig_flag = RT_FALSE;
            rt_kprintf("[BB] trigger code=%u session=%u (pre frozen)\n",
                       bb_trig_code, bb_session);
        }

        /* ---- 3. post 收集 ---- */
        if (bb_state == BB_POST_COLLECT)
        {
            bb_sample(&f);
            if (f.seq != 0 && bb_post_idx < BB_PRE_FRAMES + BB_POST_FRAMES)
                bb_post[bb_post_idx++] = f;
            if (bb_post_idx >= BB_PRE_FRAMES + BB_POST_FRAMES)
                bb_state = BB_WRITING;
        }

        /* ---- 4. 异步落盘: 逐帧 submit, 队列满→flush 重试(不暴力打爆) ---- */
        if (bb_state == BB_WRITING)
        {
            bb_last_err = RT_EOK;
            for (i = 0; i < BB_PRE_FRAMES + BB_POST_FRAMES; ++i)
            {
                bb_to_sample(&bb_post[i], &s);
                while ((e = ns_log_submit(&s)) != RT_EOK)
                {
                    bb_last_err = e;
                    ns_log_flush(50);           /* 排空队列再投, 节流 */
                }
                bb_written_total++;
                if ((i % 16) == 15) ns_log_flush(50);   /* 定期推进持久化 */
            }
            ns_log_flush(500);
            rt_kprintf("[BB] session %u written (%u frames), last_err=%d\n",
                       bb_session, BB_PRE_FRAMES + BB_POST_FRAMES, bb_last_err);
            bb_state = BB_IDLE;                 /* 回到常态采样 */
        }

        rt_thread_mdelay(10);                   /* 100Hz */
    }
}

/* ---------- 正式 API (blackbox.h) ---------- */

void blackbox_trigger(rt_uint32_t fault_code)
{
    bb_trig_code = fault_code;
    bb_trig_flag = RT_TRUE;                     /* O(1), 无阻塞 */
}

rt_err_t blackbox_init(void)
{
    if (bb_tid != RT_NULL) return RT_EOK;       /* 幂等 */

    rt_memset(bb_pre, 0, sizeof(bb_pre));
    rt_memset(bb_post, 0, sizeof(bb_post));
    bb_state = BB_IDLE;
    bb_pre_idx = 0; bb_post_idx = 0;
    bb_last_seq_seen = 0;

    bb_tid = rt_thread_create("bblog", blackbox_entry, RT_NULL,
                              1024, 18, 10);    /* prio 18: 低于所有实时服务 */
    if (bb_tid == RT_NULL) { bb_health = SUBSYS_FAILED; return -RT_ERROR; }
    rt_thread_startup(bb_tid);

    bb_health = SUBSYS_OK;
    rt_kprintf("[BB] blackbox ready (pre=%u post=%u frames @100Hz, prio 18)\n",
               BB_PRE_FRAMES, BB_POST_FRAMES);
    return RT_EOK;
}

subsys_health_t blackbox_get_health(void) { return bb_health; }

/* ---------- MSH: blackbox_status / dump / clear / selftest ---------- */

static void blackbox_status(void)
{
    ns_log_stats_t st;

    rt_kprintf("[BB] state=%d session=%u written=%u last_err=%d health=%s\n",
               (int)bb_state, bb_session, bb_written_total, (int)bb_last_err,
               subsys_health_name(bb_health));
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
    /* 从写游标向前回溯最近 max 条 */
    for (i = 0; i < st.used_slots && shown < (rt_uint32_t)max; ++i)
    {
        slot = (st.used_slots - 1 - i) % NS_LOG_SLOTS;
        if (ns_log_read_slot(slot, &s, &seq) != RT_EOK) continue;
        rt_kprintf("[BB] slot=%u ses=%u t=%u ev=%u valid=%02X sg=%d ma=%d vib=%d hz=%d\n",
                   slot, s.session_id, s.time_ms, s.event, s.valid,
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

/* 软件自检: 合成帧注入 + 触发 + 落盘 + 回读核对。
 * 会真实写 Flash 事件分区; 需要 Flash 在位。软件级验证, 非整机硬件验证。 */
static void blackbox_selftest(void)
{
    sensor_frame_t f;
    ns_fault_sample_t s;
    ns_log_stats_t st0, st1;
    rt_uint32_t seq, before, after, i, wait;
    int pass = 1;
    rt_err_t e;

    if (blackbox_init() != RT_EOK) { rt_kprintf("[BB-ST] init failed\n"); return; }
    if (ns_storage_init() != RT_EOK)
    { rt_kprintf("[BB-ST] SKIP: storage not available\n"); return; }
    if (ns_log_stats(&st0) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    before = st0.valid_records;

    /* 注入 300 帧合成序列(pre 200 + post 100 会由真实线程采,
     * 但为确定性, 这里直接喂线程消费的 sensor_service 无法注入 ——
     * 因此自检策略: 用真实采样帧 + 真实触发, 校验落盘流程与回读) */
    rt_kprintf("[BB-ST] trigger real capture (fault=SOFT, ~3s)...\n");
    blackbox_trigger(FAULT_SOFT);
    /* 等待写完: 轮询状态回 IDLE, 最多 15s */
    for (wait = 0; wait < 1500 && bb_state != BB_IDLE; ++wait)
        rt_thread_mdelay(10);
    if (bb_state != BB_IDLE) { rt_kprintf("[BB-ST] FAIL: not drained\n"); return; }

    if (ns_log_stats(&st1) != RT_EOK) { rt_kprintf("[BB-ST] stats failed\n"); return; }
    after = st1.valid_records;
    rt_kprintf("[BB-ST] valid records: %u -> %u\n", before, after);
    if (after <= before) { rt_kprintf("[BB-ST] FAIL: no new records\n"); return; }

    /* 回读最新一条, 核对 event 字段 == 触发码(FAULT_SOFT=9) */
    for (i = 0; i < st1.used_slots; ++i)
    {
        rt_uint32_t slot = (st1.used_slots - 1 - i) % NS_LOG_SLOTS;
        if (ns_log_read_slot(slot, &s, &seq) != RT_EOK) continue;
        if (s.session_id == bb_session)
        {
            rt_kprintf("[BB-ST] readback ses=%u ev=%u valid=%02X\n",
                       s.session_id, s.event, s.valid);
            if (s.event != FAULT_SOFT)
            { rt_kprintf("[BB-ST] FAIL: event mismatch\n"); pass = 0; }
            break;
        }
    }
    (void)e;
    rt_kprintf("[BB-ST] %s\n", pass ? "PASS (software+flash path)" : "FAILED");
}
MSH_CMD_EXPORT(blackbox_selftest, capture-trigger-write-readback selftest);
