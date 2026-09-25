/*
 * sensor_service.c - 统一传感器采集服务 (Phase 7-B, Fix E 采集可注入状态)
 *
 * 线程: 优先级 7, 100Hz。所有数据源走 Phase 7-A 正式 API。
 * 失败策略: 源读取失败 → 对应 valid 位清零, 保留上次值(绝不写 0 冒充有效)。
 * SG_RESULT(UART ≈1ms/次)按 1/10 分频采样, 其余源每帧采集。
 *
 * Fix E(测试隔离): s_collect() 以前隐式读写文件静态 s_prev / s_seq,
 *   而 Sensor Thread 一直在调用它 —— sensor_selftest 同时调 s_collect 就会
 *   改掉生产的基帧与帧序号(还会推进 s_seq, 影响 SG 分频相位), 与真实线程竞争。
 *   现在基帧与序号计数器都由调用方传入:
 *     生产线程 → s_collect(&f, &s_prev, &s_seq)
 *     selftest → s_collect(&f, &test_prev, &test_seq)   (全在栈上)
 *   因此 sensor_selftest 结构上不可能改动 s_prev / s_seq / s_frame,
 *   自检结尾也把"生产状态未变"作为显式断言打出来。
 *
 * 顺带修正该自检本身的一处假测试: 旧实现先 memset(&f, 0xAB) 再调 s_collect(&f),
 *   而 s_collect 第一句就是 *f = s_prev(全 0 的静态基帧), 0xAB 垃圾立刻被覆盖,
 *   "注入垃圾基帧"从未真正生效。现在把 0xAB 基帧作为 *输入* 传进去, 才真正验证
 *   "失败源的 valid 位必须被清零"。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "app_health.h"
#include "adxl345.h"
#include "current_adc.h"
#include "tmc2209.h"
#include "motor.h"
#include "sensor_service.h"
#include "safety_state.h"

#define SENSOR_THREAD_STACK   1024
#define SENSOR_THREAD_PRIO    7
#define SENSOR_PERIOD_MS      10          /* 100Hz */
#define SENSOR_SG_DIV         10          /* SG_RESULT 10Hz 分频 */

static sensor_frame_t s_frame;              /* 最新帧(临界区保护) */
static sensor_frame_t s_prev;               /* 生产基帧(零初始化=全 valid 0) */
static rt_uint32_t s_seq = 0;               /* 生产帧序号(兼 SG 分频相位) */
static rt_thread_t s_tid = RT_NULL;
static subsys_health_t s_health = SUBSYS_UNINIT;

/* 整数平方根(Newton), 用于振动幅值 */
static rt_uint32_t s_isqrt(rt_uint32_t n)
{
    rt_uint32_t x = n, y = 1;

    if (n == 0) return 0;
    while (x > y) { x = (x + y) / 2; y = n / x; }
    return x;
}

/* 单帧采集(Phase 7 review P0-2 + Fix E):
 * 基帧与帧序号由调用方提供/回写, 本函数不碰任何文件静态 ⇒ 可在自检里用
 * 独立的 test_prev/test_seq 复现同一套逻辑。每源独立 try:
 *   成功 → 更新值 + valid=1 + fresh=1
 *   失败 → valid=0 + fresh=0(值字段保留基帧值, 调用方看 valid)
 * SG 10Hz 分频: 非采样帧 fresh_sg=0, valid_sg 沿用(上次成功样本仍在窗内)
 *
 * base_prev 同时是输出: 采完这帧后它就成了下一帧的基(生产传 &s_prev)。 */
static void s_collect(sensor_frame_t *f, sensor_frame_t *base_prev,
                      rt_uint32_t *seq)
{
    rt_int16_t mx, my, mz;
    rt_uint32_t raw;
    float ma;
    motor_snapshot_t snap;
    rt_err_t e;

    *f = *base_prev;                    /* 基帧: 显式传入, 不是栈垃圾 */
    f->timestamp = rt_tick_get();

    /* IMU */
    if (adxl345_read_mg(&mx, &my, &mz) == RT_EOK)
    {
        f->ax = mx; f->ay = my; f->az = mz;
        f->vib_mg = (rt_int16_t)s_isqrt((rt_uint32_t)(
            (rt_int32_t)mx * mx + (rt_int32_t)my * my + (rt_int32_t)mz * mz));
        f->valid_imu = 1; f->fresh_imu = 1;
    }
    else { f->valid_imu = 0; f->fresh_imu = 0; }

    /* 电流(单次采样一次换算, 不重复推 EMA) */
    if (current_adc_read_measurement(&raw, RT_NULL, &ma) == RT_EOK)
    {
        f->current_raw = raw;
        f->current_filtered = current_adc_get_filtered_raw();
        f->current_ma = ma;
        f->valid_current = 1; f->fresh_current = 1;
        /* Fix E: 存"标定来源"而不是含糊的 calibrated 位 */
        f->current_cal_source = (rt_uint8_t)current_adc_cal_source();
    }
    else { f->valid_current = 0; f->fresh_current = 0; }

    /* TMC SG_RESULT: 10Hz 分频 */
    if ((*seq % SENSOR_SG_DIV) == 0)
    {
        rt_uint16_t sg = 0;
        e = tmc2209_read_sg_result(&sg);
        if (e == RT_EOK) { f->sg_result = sg; f->valid_sg = 1; f->fresh_sg = 1; }
        else { f->valid_sg = 0; f->fresh_sg = 0; }
    }
    else f->fresh_sg = 0;               /* 非采样帧: 值与 valid 沿用, fresh=0 */

    /* 运动快照 */
    if (motor_get_snapshot(&snap) == RT_EOK)
    {
        f->step_hz = snap.current_hz;
        f->motor_state = (rt_uint8_t)snap.state;
        f->dir = snap.dir;
    }

    /* P2-2: 安全输入直读 —— 四路全部成功解析/read 才置 valid_safety=1 */
    {
        rt_base_t pe = safety_pin(PIN_NAME_ESTOP);
        rt_base_t pn = safety_pin(PIN_NAME_LIMIT_MIN);
        rt_base_t px = safety_pin(PIN_NAME_LIMIT_MAX);
        rt_base_t pd = safety_pin(PIN_NAME_TMC_DIAG);

        if (pe >= 0) { f->estop = rt_pin_read(pe) ? 1 : 0; }
        if (pn >= 0) { f->limit_min = rt_pin_read(pn) ? 1 : 0; }
        if (px >= 0) { f->limit_max = rt_pin_read(px) ? 1 : 0; }
        if (pd >= 0) { f->tmc_diag = rt_pin_read(pd) ? 1 : 0; }
        f->valid_safety = (pe >= 0 && pn >= 0 && px >= 0 && pd >= 0) ? 1 : 0;
    }

    *base_prev = *f;                    /* 成为下一帧的基 */
    f->seq = ++(*seq);
}

static void sensor_thread_entry(void *param)
{
    (void)param;

    while (1)
    {
        sensor_frame_t f;

        s_collect(&f, &s_prev, &s_seq);  /* 生产状态: s_prev / s_seq */
        rt_enter_critical();
        s_frame = f;
        rt_exit_critical();
        rt_thread_mdelay(SENSOR_PERIOD_MS);
    }
}

/* ---------- 正式 API (sensor_service.h) ---------- */

rt_err_t sensor_service_init(void)
{
    if (s_tid != RT_NULL) return RT_EOK;        /* 幂等 */

    rt_memset(&s_frame, 0, sizeof(s_frame));    /* 初值 0 + valid 全 0 = 无数据 */
    rt_memset(&s_prev, 0, sizeof(s_prev));      /* 基帧同样显式清零 */
    s_seq = 0;

    s_tid = rt_thread_create("sensor", sensor_thread_entry, RT_NULL,
                             SENSOR_THREAD_STACK, SENSOR_THREAD_PRIO, 10);
    if (s_tid == RT_NULL) { s_health = SUBSYS_FAILED; return -RT_ERROR; }
    rt_thread_startup(s_tid);

    s_health = SUBSYS_OK;
    rt_kprintf("[SNS] sensor thread started (%dHz, prio %d)\n",
               1000 / SENSOR_PERIOD_MS, SENSOR_THREAD_PRIO);
    return RT_EOK;
}

rt_err_t sensor_service_get_latest(sensor_frame_t *out)
{
    if (out == RT_NULL) return -RT_EINVAL;
    rt_enter_critical();
    *out = s_frame;
    rt_exit_critical();
    return RT_EOK;
}

subsys_health_t sensor_service_get_health(void) { return s_health; }

/* ---------- MSH (sensor_status / sensor_snapshot) ----------
 * Fix D/E: 电流标定一律显示"来源"三态, 不用 current_adc_is_calibrated()
 * (它的含义是 source != NONE, 对 THEORETICAL 也返回 TRUE) 当"已标定"标签。 */

static const char *s_cal_name(rt_uint8_t src)
{
    switch (src)
    {
    case CURRENT_ADC_CAL_MEASURED:    return "MEASURED";
    case CURRENT_ADC_CAL_THEORETICAL: return "THEORETICAL(default)";
    default:                          return "NONE";
    }
}

static const char *s_live_cal_name(void)
{
    return s_cal_name((rt_uint8_t)current_adc_cal_source());
}

static void sensor_status(void)
{
    rt_kprintf("[SNS] thread=%s health=%s period=%dms seq=%u\n",
               (s_tid != RT_NULL) ? "running" : "off",
               subsys_health_name(sensor_service_get_health()),
               SENSOR_PERIOD_MS, s_seq);
    rt_kprintf("[SNS] adxl345=%s current_adc=%s tmc2209=%s motor=%s\n",
               subsys_health_name(adxl345_get_health()),
               subsys_health_name(current_adc_get_health()),
               subsys_health_name(tmc2209_get_health()),
               subsys_health_name(motor_get_health()));
    rt_kprintf("[SNS] current cal source=%s%s\n", s_live_cal_name(),
               current_adc_cal_source() == CURRENT_ADC_CAL_MEASURED ?
               "" : "  -> mA is NOT a measured current, do not use as evidence");
}
MSH_CMD_EXPORT(sensor_status, show sensor service and source healths);

static void sensor_snapshot(void)
{
    sensor_frame_t f;

    if (sensor_service_get_latest(&f) != RT_EOK)
    { rt_kprintf("[SNS] snapshot failed\n"); return; }

    rt_kprintf("[SNS] frame seq=%u tick=%u\n", f.seq, f.timestamp);
    rt_kprintf("[SNS] imu  [%s] ax=%d ay=%d az=%d vib=%d mg\n",
               f.valid_imu ? "VALID" : "STALE", f.ax, f.ay, f.az, f.vib_mg);
    rt_kprintf("[SNS] curr [%s] raw=%u ema=%u ma=%d cal_source=%s%s\n",
               f.valid_current ? "VALID" : "STALE",
               f.current_raw, f.current_filtered, (int)f.current_ma,
               s_cal_name(f.current_cal_source),
               f.current_cal_source == CURRENT_ADC_CAL_MEASURED ?
                   "" : "  (NOT measured)");
    rt_kprintf("[SNS] sg   [%s] SG_RESULT=%u\n",
               f.valid_sg ? "VALID" : "STALE", f.sg_result);
    rt_kprintf("[SNS] mot  state=%d step_hz=%u dir=%d\n",
               f.motor_state, f.step_hz, f.dir);
    rt_kprintf("[SNS] safe [%s] estop=%d lim_min=%d lim_max=%d diag=%d\n",
               f.valid_safety ? "READ" : "N/A",
               f.estop, f.limit_min, f.limit_max, f.tmc_diag);
}
MSH_CMD_EXPORT(sensor_snapshot, dump latest sensor frame with valid bits);

/* P0-2 + Fix E 软件自检: 验证 s_collect 的基帧/valid/fresh 语义 ——
 * 以 0xAB 模式注入"垃圾基帧"作为*输入*, 采集后失败源的 valid 位必须被清零
 * (垃圾值可以留在值字段, 但不得被标为有效), fresh 位符合采样节拍。
 *
 * Fix E 两点:
 *   - 旧实现把 0xAB 写进 f 后调 s_collect(&f), 而它第一句就 *f = s_prev 把
 *     0xAB 全量覆盖 ⇒ 注入从未生效, 该用例实际只测了"零基帧"路径。
 *   - 自检现在跑在自己的 test_prev/test_seq 上, 不再改动生产状态;
 *     结尾显式核对 s_prev / s_seq 未被本次自检触碰。
 *     (s_frame 只由生产线程写入, s_collect 本来就不碰它, 结构上已隔离。)
 * 纯软件验证(源的真实状态以当前硬件为准), 非整机硬件验证。 */
static void sensor_selftest(void)
{
    sensor_frame_t f;
    sensor_frame_t test_prev;           /* 自检专用基帧 */
    sensor_frame_t prod_prev_before;
    rt_uint32_t test_seq = 0;
    rt_uint32_t prod_seq_before, prod_seq_after;
    int pass = 1;
    rt_bool_t prev_changed;

    /* 生产状态指纹: 只要求"本次自检不参与推进" */
    prod_seq_before  = s_seq;
    prod_prev_before = s_prev;

    rt_kprintf("[SNS-ST] inject 0xAB-pattern base frame into a PRIVATE context\n");
    rt_memset(&test_prev, 0xAB, sizeof(test_prev));  /* 垃圾基帧(作为输入) */
    rt_memset(&f, 0, sizeof(f));
    s_collect(&f, &test_prev, &test_seq);            /* 不碰 s_prev / s_seq */

    /* 垃圾 valid 位(0xAB 非零)必须被真实采集结果覆盖。
     * D5: valid/fresh 表达的是"这一帧到底读到没有", 与标定来源无关 ——
     *   读不出(FAILED)      -> valid 必须 0
     *   读得出(OK/DEGRADED) -> valid=1 且 fresh=1
     * 电流通道的 DEGRADED 只表示标定来源是 THEORETICAL
     * (current_adc.c: health = (cal_source==MEASURED) ? OK : DEGRADED),
     * 采样本身成功; 旧断言拿 "health != OK" 推 "valid 必须 0", 在正确代码上
     * 必挂(真机复现)。IMU / TMC 的 DEGRADED 含义不同(adxl345.c 传输失败、
     * tmc2209.c 读失败), 所以这两个源继续用 != OK 当"读不出"的判据。 */
    if (adxl345_get_health() != SUBSYS_OK && f.valid_imu != 0)
    { rt_kprintf("[SNS-ST] imu valid FAIL (unhealthy but valid=%d)\n", f.valid_imu);
      pass = 0; }
    if (tmc2209_get_health() != SUBSYS_OK && f.valid_sg != 0)
    { rt_kprintf("[SNS-ST] sg valid FAIL\n"); pass = 0; }
    if (current_adc_get_health() == SUBSYS_FAILED && f.valid_current != 0)
    { rt_kprintf("[SNS-ST] adc valid FAIL (FAILED but valid=%d)\n", f.valid_current);
      pass = 0; }
    if (current_adc_get_health() != SUBSYS_FAILED && current_adc_get_health() != SUBSYS_UNINIT &&
        (f.valid_current != 1 || f.fresh_current != 1))
    { rt_kprintf("[SNS-ST] adc readable but valid/fresh FAIL (h=%s v=%d f=%d)\n",
                 subsys_health_name(current_adc_get_health()),
                 f.valid_current, f.fresh_current);
      pass = 0; }

    /* 标定来源是另一件事, 单独断言: DEGRADED 只允许来自"未实测", 绝不反过来
     * 用健康度冒充标定来源。 */
    if (current_adc_get_health() == SUBSYS_DEGRADED &&
        f.current_cal_source == (rt_uint8_t)CURRENT_ADC_CAL_MEASURED)
    { rt_kprintf("[SNS-ST] DEGRADED yet claims MEASURED calibration\n"); pass = 0; }

    /* fresh 位: IMU 每帧采样 → fresh 应为 1(若对应源健康) */
    if (adxl345_get_health() == SUBSYS_OK && f.fresh_imu != 1)
    { rt_kprintf("[SNS-ST] imu fresh FAIL\n"); pass = 0; }

    /* 帧内标定来源现在是三态值, 不再是含糊的布尔 */
    rt_kprintf("[SNS-ST] current_cal_source=%u (%s)\n",
               f.current_cal_source, s_cal_name(f.current_cal_source));
    if (f.current_cal_source > (rt_uint8_t)CURRENT_ADC_CAL_MEASURED)
    { rt_kprintf("[SNS-ST] cal source out of range\n"); pass = 0; }

    rt_kprintf("[SNS-ST] frame: imu[v=%d f=%d] cur[v=%d f=%d] sg[v=%d f=%d]\n",
               f.valid_imu, f.fresh_imu, f.valid_current, f.fresh_current,
               f.valid_sg, f.fresh_sg);
    rt_kprintf("[SNS-ST] private seq advanced to %u (live s_seq was %u)\n",
               test_seq, prod_seq_before);

    /* Fix E 隔离断言: 生产的 s_seq 与 s_prev 不得因本次自检而改变。
     * Sensor Thread 自己会推进它们, 所以:
     *   线程未创建 → 强断言(必须完全一致);
     *   线程在跑   → 只断言"不倒退", 并把线程自己推进的量如实打出来。 */
    prod_seq_after = s_seq;
    prev_changed = (rt_memcmp(&prod_prev_before, &s_prev, sizeof(s_prev)) != 0);

    if (s_tid == RT_NULL)
    {
        if (prod_seq_after != prod_seq_before || prev_changed)
        { rt_kprintf("[SNS-ST] ISOLATION FAIL: selftest mutated live state"
                     " (seq %u -> %u, prev_changed=%d)\n",
                     prod_seq_before, prod_seq_after, (int)prev_changed);
          pass = 0; }
        else
            rt_kprintf("[SNS-ST] isolation OK (sensor thread off: live state"
                       " untouched)\n");
    }
    else
    {
        rt_kprintf("[SNS-ST] isolation INFO: thread running, live seq %u -> %u"
                   " prev_changed=%d (advanced by the thread, not by selftest)\n",
                   prod_seq_before, prod_seq_after, (int)prev_changed);
        if (prod_seq_after < prod_seq_before)
        { rt_kprintf("[SNS-ST] isolation FAIL: live seq went backwards\n"); pass = 0; }
    }

    rt_kprintf("[SNS-ST] %s\n", pass ? "PASS" : "FAILED");
}
MSH_CMD_EXPORT(sensor_selftest, verify frame base management and valid/fresh bits);
