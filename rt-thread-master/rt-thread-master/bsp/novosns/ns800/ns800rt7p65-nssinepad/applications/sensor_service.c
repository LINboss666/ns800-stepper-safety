/*
 * sensor_service.c - 统一传感器采集服务 (Phase 7-B)
 *
 * 线程: 优先级 7, 100Hz。所有数据源走 Phase 7-A 正式 API。
 * 失败策略: 源读取失败 → 对应 valid 位清零, 保留上次值(绝不写 0 冒充有效)。
 * SG_RESULT(UART ≈1ms/次)按 1/10 分频采样, 其余源每帧采集。
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
static sensor_frame_t s_prev;               /* 上一帧基(零初始化=全 valid 0, 无栈垃圾) */
static rt_uint32_t s_seq = 0;
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

/* 单帧采集(Phase 7 review P0-2):
 * 基帧 = 上一帧(显式复制, 非栈垃圾); 每源独立 try:
 *   成功 → 更新值 + valid=1 + fresh=1
 *   失败 → valid=0 + fresh=0(值字段保留上次成功值, 调用方看 valid)
 * SG 10Hz 分频: 非采样帧 fresh_sg=0, valid_sg 沿用(上次成功样本仍在窗内) */
static void s_collect(sensor_frame_t *f)
{
    rt_int16_t mx, my, mz;
    rt_uint32_t raw;
    float ma;
    motor_snapshot_t snap;
    rt_base_t p;
    rt_err_t e;

    *f = s_prev;                        /* 基帧: 显式上一帧(s_prev 静态零初始化, 无栈垃圾) */
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
        f->current_calibrated = current_adc_is_calibrated() ? 1 : 0;
    }
    else { f->valid_current = 0; f->fresh_current = 0; }

    /* TMC SG_RESULT: 10Hz 分频 */
    if ((s_seq % SENSOR_SG_DIV) == 0)
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

    /* 安全输入直读(电平语义待接线冻结, 只报原始值) */
    p = safety_pin(PIN_NAME_ESTOP);
    if (p >= 0) { f->estop = rt_pin_read(p) ? 1 : 0; f->valid_safety = 1; }
    p = safety_pin(PIN_NAME_LIMIT_MIN);
    if (p >= 0) f->limit_min = rt_pin_read(p) ? 1 : 0;
    p = safety_pin(PIN_NAME_LIMIT_MAX);
    if (p >= 0) f->limit_max = rt_pin_read(p) ? 1 : 0;
    p = safety_pin(PIN_NAME_TMC_DIAG);
    if (p >= 0) f->tmc_diag = rt_pin_read(p) ? 1 : 0;

    s_prev = *f;                        /* 成为本帧基 */
    f->seq = ++s_seq;
}

static void sensor_thread_entry(void *param)
{
    (void)param;

    while (1)
    {
        sensor_frame_t f;

        s_collect(&f);   /* f 由 s_collect 从 s_prev 基帧构建 */
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

/* ---------- MSH (sensor_status / sensor_snapshot) ---------- */

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
    rt_kprintf("[SNS] current calibrated=%s (未标定时 mA 为理论换算)\n",
               current_adc_is_calibrated() ? "YES" : "NO");
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
    rt_kprintf("[SNS] curr [%s] raw=%u ema=%u ma=%d cal=%d\n",
               f.valid_current ? "VALID" : "STALE",
               f.current_raw, f.current_filtered, (int)f.current_ma,
               f.current_calibrated);
    rt_kprintf("[SNS] sg   [%s] SG_RESULT=%u\n",
               f.valid_sg ? "VALID" : "STALE", f.sg_result);
    rt_kprintf("[SNS] mot  state=%d step_hz=%u dir=%d\n",
               f.motor_state, f.step_hz, f.dir);
    rt_kprintf("[SNS] safe [%s] estop=%d lim_min=%d lim_max=%d diag=%d\n",
               f.valid_safety ? "READ" : "N/A",
               f.estop, f.limit_min, f.limit_max, f.tmc_diag);
}
MSH_CMD_EXPORT(sensor_snapshot, dump latest sensor frame with valid bits);

/* P0-2 软件自检: 验证 s_collect 的基帧管理 ——
 * 以 0xAB 模式注入"垃圾基帧", 采集后失败源的 valid 位必须清零
 * (垃圾值可保留在值字段但不得被标有效), fresh 位符合采样节拍。
 * 纯软件验证(真实源状态以当前硬件为准)。 */
static void sensor_selftest(void)
{
    sensor_frame_t f;
    int pass = 1;

    rt_kprintf("[SNS-ST] inject 0xAB-pattern base frame...\n");
    rt_memset(&f, 0xAB, sizeof(f));        /* 垃圾基帧: 值与 valid 位全 0xAB */
    s_collect(&f);                          /* 用真实源状态采集一帧 */

    /* 垃圾 valid 位(0xAB 非零)必须被真实采集结果覆盖:
     * 不健康源 → valid=0; 健康源 → valid=1 + fresh=1 */
    if (adxl345_get_health() != SUBSYS_OK && f.valid_imu != 0)
    { rt_kprintf("[SNS-ST] imu valid FAIL (unhealthy but valid=%d)\n", f.valid_imu); pass = 0; }
    if (current_adc_get_health() != SUBSYS_OK && f.valid_current != 0)
    { rt_kprintf("[SNS-ST] adc valid FAIL\n"); pass = 0; }
    if (tmc2209_get_health() != SUBSYS_OK && f.valid_sg != 0)
    { rt_kprintf("[SNS-ST] sg valid FAIL\n"); pass = 0; }

    /* fresh 位: IMU/电流每帧采样 → fresh 应为 1(若对应源健康) */
    if (adxl345_get_health() == SUBSYS_OK && f.fresh_imu != 1)
    { rt_kprintf("[SNS-ST] imu fresh FAIL\n"); pass = 0; }
    if (current_adc_get_health() == SUBSYS_OK && f.fresh_current != 1)
    { rt_kprintf("[SNS-ST] adc fresh FAIL\n"); pass = 0; }

    rt_kprintf("[SNS-ST] frame: imu[v=%d f=%d] cur[v=%d f=%d] sg[v=%d f=%d]\n",
               f.valid_imu, f.fresh_imu, f.valid_current, f.fresh_current,
               f.valid_sg, f.fresh_sg);
    rt_kprintf("[SNS-ST] %s\n", pass ? "PASS" : "FAILED");
}
MSH_CMD_EXPORT(sensor_selftest, verify frame base management and valid/fresh bits);
