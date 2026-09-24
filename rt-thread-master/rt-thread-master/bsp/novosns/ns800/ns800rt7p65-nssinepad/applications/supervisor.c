/*
 * supervisor.c - 系统监督初版 (Phase 7-B)
 *
 * runtime_selftest 是软件级测试: 不转动电机、不依赖物理 ESTOP。
 * 覆盖: ①非法状态转换拒绝 ②motor arm 门禁 ③故障停机链(SOFT_FAULT 事件
 * → Safety Thread → FAULT_LATCHED)④fault_reset 实测源+人工清除
 * ⑤sensor valid 位处理。测试后恢复 READY。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "safety_state.h"
#include "safety_thread.h"
#include "adxl345.h"
#include "current_adc.h"
#include "tmc2209.h"
#include "motor.h"
#include "sensor_service.h"
#include "supervisor.h"
#include "diagnosis.h"

/* ---------- system_status ---------- */

static rt_uint8_t ns_flash_ok_cached = 0;   /* 自检时记录 */

static void system_status(void)
{
    motor_snapshot_t snap;

    rt_kprintf("=== SYSTEM STATUS ===\n");

    /* Safety */
    rt_kprintf("[Safety] state=%s fault=%u irq_gate=%s\n",
               safety_state_name(safety_state_get()), safety_fault_get(),
               safety_irq_attached() ? "OPEN" : "CLOSED");
    rt_kprintf("[Safety] gpio_ready=%s thread=%s\n",
               safety_gpio_ready() ? "YES" : "NO",
               subsys_health_name(safety_thread_get_health()));

    /* Motor */
    if (motor_get_snapshot(&snap) == RT_EOK)
        rt_kprintf("[Motor] state=%d cur=%u tgt=%u armed=%d drv_en=%d health=%s\n",
                   snap.state, snap.current_hz, snap.target_hz, snap.armed,
                   snap.drv_en_request, subsys_health_name(motor_get_health()));
    else
        rt_kprintf("[Motor] snapshot failed\n");

    /* Sensor service */
    rt_kprintf("[Sensor] thread=%s\n",
               subsys_health_name(sensor_service_get_health()));

    /* Subsystems */
    rt_kprintf("[TMC]    %s\n", subsys_health_name(tmc2209_get_health()));
    rt_kprintf("[IMU]    %s\n", subsys_health_name(adxl345_get_health()));
    rt_kprintf("[ADC]    %s calibrated=%s\n",
               subsys_health_name(current_adc_get_health()),
               current_adc_is_calibrated() ? "YES" : "NO");
    rt_kprintf("[Flash]  %s (spi1, BUG-013 static verified)\n",
               ns_flash_ok_cached ? "OK(selftest)" : "UNTESTED-this-boot");
}
MSH_CMD_EXPORT(system_status, print all subsystem healths and states);

/* ---------- runtime_selftest (软件级, 不动电机) ---------- */

static void runtime_selftest(void)
{
    int pass = 1;
    safety_state_t st0 = safety_state_get();
    rt_err_t e;
    sensor_frame_t f;
    motor_snapshot_t snap;

    rt_kprintf("[SELFTEST] start (state0=%s)\n", safety_state_name(st0));

    /* ① 非法状态转换拒绝: READY/任意 → BOOT 不在白名单 */
    e = safety_transition(SAFETY_BOOT);
    if (e == -RT_EPERM)
        rt_kprintf("[SELFTEST] 1.illegal-transition REFUSED ok\n");
    else { rt_kprintf("[SELFTEST] 1.illegal-transition FAIL (e=%d)\n", e); pass = 0; }

    /* ② motor arm 门禁: 硬件使能链未验证 → 必须拒绝 */
    e = motor_arm();
    if (e == -RT_EPERM)
        rt_kprintf("[SELFTEST] 2.motor-arm-gate REFUSED ok\n");
    else { rt_kprintf("[SELFTEST] 2.motor-arm-gate FAIL (e=%d)\n", e); pass = 0; }

    /* ③ 故障停机链 + 事件处理: SOFT_FAULT 事件 → Safety Thread → FAULT_LATCHED */
    safety_post_event(EVT_SOFT_FAULT);
    rt_thread_mdelay(1200);      /* Safety Thread 500ms recv 超时窗 + 余量 */
    if (safety_state_get() == SAFETY_FAULT_LATCHED &&
        safety_fault_get() == FAULT_SOFT)
        rt_kprintf("[SELFTEST] 3.fault-shutdown via event OK\n");
    else { rt_kprintf("[SELFTEST] 3.fault-shutdown FAIL (state=%s code=%u)\n",
                      safety_state_name(safety_state_get()), safety_fault_get());
           pass = 0; }

    /* ④ fault_reset: 实测源(浮空/未触发=safe) → MANUAL_CLEAR */
    safety_fault_reset_manual();
    if (safety_state_get() == SAFETY_MANUAL_CLEAR)
        rt_kprintf("[SELFTEST] 4.fault-reset OK\n");
    else { rt_kprintf("[SELFTEST] 4.fault-reset FAIL\n"); pass = 0; }

    /* ⑤ sensor valid 位: 源不健康时 valid 位必须为 0 */
    sensor_service_get_latest(&f);
    if (adxl345_get_health() != SUBSYS_OK && f.valid_imu != 0)
    { rt_kprintf("[SELFTEST] 5.sensor-valid FAIL (imu unhealthy but valid=1)\n"); pass = 0; }
    else rt_kprintf("[SELFTEST] 5.sensor-valid ok (imu valid=%d adc valid=%d)\n",
                    f.valid_imu, f.valid_current);

    /* 恢复: MANUAL_CLEAR → READY(白名单允许; 不重跑自检, 自检已在启动时跑过) */
    if (safety_state_get() == SAFETY_MANUAL_CLEAR)
        safety_transition(SAFETY_READY);

    motor_get_snapshot(&snap);
    rt_kprintf("[SELFTEST] %s (final state=%s motor=%d)\n",
               pass ? "ALL PASS" : "FAILED",
               safety_state_name(safety_state_get()), snap.state);
}
MSH_CMD_EXPORT(runtime_selftest, software-level runtime self test (no motion));

/* ---------- 初始化 ---------- */

int supervisor_init(void)
{
    /* system_status / runtime_selftest 的 MSH_EXPORT 已静态注册;
     * 此处仅做启动序列: Motor/Sensor/Safety 三服务在 self-test 与 main 中
     * 按需初始化(motor_init 由 safety selftest 调用, sensor/safety thread 在此拉起)。 */
    safety_thread_init();
    sensor_service_init();
    diagnosis_init();
    return RT_EOK;
}
INIT_APP_EXPORT(supervisor_init);
