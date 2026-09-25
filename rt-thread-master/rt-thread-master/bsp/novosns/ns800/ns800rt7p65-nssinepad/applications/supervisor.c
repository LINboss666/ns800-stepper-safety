/*
 * supervisor.c - 系统监督 + 显式业务层 bootstrap (Phase 7-B/C/D, R2 修复)
 *
 * P1-8: 业务层启动顺序显式化(supervisor_boot), 业务模块不再依赖 INIT_APP
 * 链接顺序。BSP/RT-Thread 驱动自身的 INIT 保持原机制。
 *
 * runtime_selftest: 软件级测试(不依赖真实运动), 覆盖:
 *   非法状态转换拒绝 / motor arm 双门禁 / P0-1 故障后业务转换拒绝 /
 *   故障停机链(事件→Safety Thread→FAULT_LATCHED) / fault_reset 实测源 /
 *   sensor valid 位处理; 恢复必须实际重跑 required selftest(P1-15)。
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
#include "diagnosis.h"
#include "blackbox.h"
#include "project_config.h"
#include "ns_storage.h"
#include "supervisor.h"

/* ---------- LED/蜂鸣器统一管理(单一写者) ----------
 * UI_OUTPUT_ENABLED=RT_FALSE: 扩展板 LED/蜂鸣器驱动极性未实物确认,
 * 安全默认全灭。极性确认后置 RT_TRUE, 模式逻辑即生效。硬件 pending. */
#ifndef UI_OUTPUT_ENABLED
#define UI_OUTPUT_ENABLED   RT_FALSE
#endif

static rt_uint8_t ns_flash_ok_cached = 0;   /* 自检时记录 */

/* ---------- system_status ---------- */

void system_status(void)
{
    motor_snapshot_t snap;
    project_config_t cfgs;
    const project_config_t *cfg = &cfgs;

    project_config_get_snapshot(&cfgs);

    rt_kprintf("=== SYSTEM STATUS ===\n");

    rt_kprintf("[Safety] state=%s fault=%u irq_gate=%s protect_ready=%s\n",
               safety_state_name(safety_state_get()), safety_fault_get(),
               safety_irq_attached() ? "OPEN" : "CLOSED",
               safety_protection_ready() ? "YES" : "NO");
    rt_kprintf("[Safety] gpio_ready=%s thread=%s\n",
               safety_gpio_ready() ? "YES" : "NO",
               subsys_health_name(safety_thread_get_health()));

    if (motor_get_snapshot(&snap) == RT_EOK)
        rt_kprintf("[Motor] state=%d cur=%u tgt=%u armed=%d drv_en=%d health=%s\n",
                   snap.state, snap.current_hz, snap.target_hz, snap.armed,
                   snap.drv_en_request, subsys_health_name(motor_get_health()));
    else
        rt_kprintf("[Motor] snapshot failed\n");

    rt_kprintf("[Sensor] thread=%s\n",
               subsys_health_name(sensor_service_get_health()));

    rt_kprintf("[TMC]    %s\n", subsys_health_name(tmc2209_get_health()));
    rt_kprintf("[IMU]    %s\n", subsys_health_name(adxl345_get_health()));
    rt_kprintf("[ADC]    %s cal=%s\n",
               subsys_health_name(current_adc_get_health()),
               current_adc_cal_source() == CURRENT_ADC_CAL_MEASURED ? "MEASURED"
               : current_adc_cal_source() == CURRENT_ADC_CAL_THEORETICAL ? "THEORETICAL"
               : "NONE");
    rt_kprintf("[Flash]  %s (spi1, BUG-013 static verified)\n",
               ns_flash_ok_cached ? "OK(selftest)" : "UNTESTED-this-boot");
    rt_kprintf("[Storage]%s (ns_params/log)\n",
               project_config_get_health() == SUBSYS_OK ? " OK"
               : " DEGRADED(safe defaults)");
    rt_kprintf("[Config] %s valid=%s cal_source=%d\n",
               subsys_health_name(project_config_get_health()),
               project_config_is_valid() ? "YES" : "NO",
               cfg->cur_cal_source);
    rt_kprintf("[Diag]   %s verdict=%d mode=%s\n",
               subsys_health_name(diagnosis_get_health()),
               diagnosis_get_verdict(), diagnosis_get_mode());
    rt_kprintf("[Blackbox] %s\n", subsys_health_name(blackbox_get_health()));
    rt_kprintf("[UI]     led/buzzer output=%s (polarity hardware-pending)\n",
               UI_OUTPUT_ENABLED ? "ENABLED" : "SAFE-OFF");
}

/* ---------- LED/蜂鸣器统一管理(单一写者) ----------
 * UI_OUTPUT_ENABLED=RT_FALSE: 扩展板 LED/蜂鸣器驱动极性未实物确认,
 * 安全默认全灭。极性确认后置 RT_TRUE, 模式逻辑即生效。硬件 pending。 */
#ifndef UI_OUTPUT_ENABLED
#define UI_OUTPUT_ENABLED   RT_FALSE
#endif

static rt_base_t ui_run = -1, ui_warn = -1, ui_fault = -1, ui_buzz = -1;
static rt_uint32_t ui_ticks = 0;
static rt_thread_t ui_tid = RT_NULL;

static void ui_write(rt_base_t pin, rt_uint8_t on)
{
    if (pin < 0) return;
    if (!UI_OUTPUT_ENABLED) { rt_pin_write(pin, PIN_LOW); return; }
    rt_pin_write(pin, on ? PIN_HIGH : PIN_LOW);
}

static void ui_thread_entry(void *param)
{
    (void)param;

    ui_run   = safety_pin(PIN_NAME_RUN_LED);
    ui_warn  = safety_pin(PIN_NAME_WARN_LED);
    ui_fault = safety_pin(PIN_NAME_FAULT_LED);
    ui_buzz  = safety_pin(PIN_NAME_BUZZER);

    while (1)
    {
        safety_state_t st = safety_state_get();
        rt_uint8_t run = 0, warn = 0, fault = 0, buzz = 0;
        rt_uint32_t phase = (ui_ticks / 5) % 2;      /* 500ms 半周期 */

        switch (st)
        {
        case SAFETY_INIT:
        case SAFETY_SELF_TEST:
        case SAFETY_BOOT:
            warn = phase; break;
        case SAFETY_READY:
        case SAFETY_MANUAL_CLEAR:
            run = 1; break;
        case SAFETY_RUN:
            run = phase; break;
        case SAFETY_LOAD_WARNING:
            warn = (ui_ticks / 2) % 2; break;
        case SAFETY_ABNORMAL:
            warn = 1; fault = phase; break;
        case SAFETY_FAULT_LATCHED:
        case SAFETY_ESTOP:
            fault = 1; buzz = 1; break;
        default: break;
        }

        ui_write(ui_run,   run);
        ui_write(ui_warn,  warn);
        ui_write(ui_fault, fault);
        ui_write(ui_buzz,  buzz);

        ui_ticks++;
        rt_thread_mdelay(100);
    }
}

static void ui_manager_init(void)
{
    if (ui_tid != RT_NULL) return;
    ui_tid = rt_thread_create("ui", ui_thread_entry, RT_NULL,
                              512, 20, 10);
    if (ui_tid != RT_NULL) rt_thread_startup(ui_tid);
}

/* ---------- MSH: system_status / selftests ---------- */

static void system_status_msh(void) { system_status(); }
MSH_CMD_EXPORT(system_status, print all subsystem healths and states);

static void runtime_selftest(void)
{
    int pass = 1;
    safety_state_t st0 = safety_state_get();
    rt_err_t e;
    sensor_frame_t f;
    motor_snapshot_t snap;

    rt_kprintf("[SELFTEST] start (state0=%s)\n", safety_state_name(st0));

    /* ① 非法状态转换拒绝 */
    e = safety_transition(SAFETY_BOOT);
    if (e == -RT_EPERM)
        rt_kprintf("[SELFTEST] 1.illegal-transition REFUSED ok\n");
    else { rt_kprintf("[SELFTEST] 1.illegal-transition FAIL (e=%d)\n", e); pass = 0; }

    /* ② motor arm 双门禁: 使能链宏 + protection ready(均默认 false) */
    e = motor_arm();
    if (e == -RT_EPERM)
        rt_kprintf("[SELFTEST] 2.motor-arm-gate REFUSED ok\n");
    else { rt_kprintf("[SELFTEST] 2.motor-arm-gate FAIL (e=%d)\n", e); pass = 0; }
    if (!safety_protection_ready())
        rt_kprintf("[SELFTEST] 2b.protection-gate CLOSED ok (default)\n");
    else { rt_kprintf("[SELFTEST] 2b.protection-gate unexpectedly OPEN\n"); pass = 0; }

    /* ③ 故障停机链 + 事件处理 + P0-1 串行化验证:
     * SOFT_FAULT 事件 → FAULT_LATCHED 后, 尝试业务恢复转换必须被拒 */
    safety_post_event(EVT_SOFT_FAULT);
    rt_thread_mdelay(1200);
    if (safety_state_get() == SAFETY_FAULT_LATCHED &&
        safety_fault_get() == FAULT_SOFT)
    {
        rt_kprintf("[SELFTEST] 3.fault-shutdown via event OK\n");
        e = safety_transition(SAFETY_READY);
        if (e == -RT_EPERM)
            rt_kprintf("[SELFTEST] 3b.fault-vs-business-transition REFUSED ok\n");
        else { rt_kprintf("[SELFTEST] 3b FAIL (e=%d)\n", e); pass = 0; }
    }
    else { rt_kprintf("[SELFTEST] 3.fault-shutdown FAIL (state=%s code=%u)\n",
                      safety_state_name(safety_state_get()), safety_fault_get());
           pass = 0; }

    /* ④ fault_reset: 实测源(浮空/未触发=safe) → MANUAL_CLEAR */
    safety_fault_reset_manual();
    if (safety_state_get() == SAFETY_MANUAL_CLEAR)
        rt_kprintf("[SELFTEST] 4.fault-reset OK\n");
    else { rt_kprintf("[SELFTEST] 4.fault-reset FAIL\n"); pass = 0; }

    /* ⑤ sensor valid 位 */
    sensor_service_get_latest(&f);
    if (adxl345_get_health() != SUBSYS_OK && f.valid_imu != 0)
    { rt_kprintf("[SELFTEST] 5.sensor-valid FAIL\n"); pass = 0; }
    else rt_kprintf("[SELFTEST] 5.sensor-valid ok (imu valid=%d adc valid=%d)\n",
                    f.valid_imu, f.valid_current);

    /* 恢复: P1-15 MANUAL_CLEAR → READY 必须实际重跑 required selftest */
    if (safety_state_get() == SAFETY_MANUAL_CLEAR)
    {
        if (safety_run_selftest() == RT_EOK)
            safety_transition(SAFETY_READY);
        else
            safety_force_shutdown(FAULT_SELF_TEST);
    }

    motor_get_snapshot(&snap);
    rt_kprintf("[SELFTEST] %s (final state=%s motor=%d)\n",
               pass ? "ALL PASS" : "FAILED",
               safety_state_name(safety_state_get()), snap.state);
}
MSH_CMD_EXPORT(runtime_selftest, software-level runtime self test (no motion));

static void system_selftest(void)
{
    rt_err_t e = safety_run_selftest();

    if (e == RT_EOK)
        safety_transition(SAFETY_READY);
    else
        safety_force_shutdown(FAULT_SELF_TEST);
    system_status();
}
MSH_CMD_EXPORT(system_selftest, rerun safety selftest and print full status);

/* ---------- P1-8: 显式业务层 bootstrap(幂等) ----------
 * 目标顺序(每步打印 stage, 真机可核对):
 *  1 safe GPIO  2 storage  3 project config
 *  4 hardware service init(TMC/IMU/ADC/PWM)
 *  5 safety state/event  6 safety thread  7 sensor service
 *  8 diagnosis  9 blackbox  10 UI  11 startup selftest / READY
 * BSP/RT-Thread 驱动自身的 INIT 保持原机制。全部幂等。 */
static rt_bool_t boot_done = RT_FALSE;

void supervisor_boot(void)
{
    if (boot_done) return;
    boot_done = RT_TRUE;

    rt_kprintf("[BOOT] stage 1: safe GPIO\n");
    safety_gpio_boot();

    rt_kprintf("[BOOT] stage 2: storage\n");
    ns_storage_init();          /* 幂等; 失败由 config/blackbox 降级处理 */

    rt_kprintf("[BOOT] stage 3: project config\n");
    project_config_boot();

    rt_kprintf("[BOOT] stage 4: hardware services\n");
    tmc2209_init();
    adxl345_init();             /* 失败不阻塞: selftest/健康位如实降级 */
    current_adc_boot();
    motor_init();

    rt_kprintf("[BOOT] stage 5-6: safety state + thread\n");
    safety_state_boot();
    safety_thread_init();

    rt_kprintf("[BOOT] stage 7-10: sensor/diagnosis/blackbox/ui\n");
    sensor_service_init();
    diagnosis_init();
    blackbox_init();
    ui_manager_init();

    rt_kprintf("[BOOT] stage 11: startup selftest done, state=%s\n",
               safety_state_name(safety_state_get()));
}

int supervisor_init(void) { supervisor_boot(); return RT_EOK; }
