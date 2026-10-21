/*
 * supervisor.c - 系统监督 + 显式业务层 bootstrap (Phase 7-B/C/D, Fix A 加固)
 *
 * Fix A: supervisor_boot() 返回 rt_err_t 并由 main() 显式调用(业务层不再依赖
 * INIT_APP 链接顺序; BSP/RT-Thread 驱动自身的 INIT 保持原机制)。
 *
 * 启动顺序原则(Fix A / A2):
 *   1 safe GPIO   2 safety state+event 核心   3 Safety Thread
 *   4 自检所需硬件服务   5 storage(1 MiB log 扫描, 可能数秒)   6 project config
 *   7 motor + sensor service   8 diagnosis   9 blackbox   10 UI
 *   11 启动自检 + 最终 READY 决策
 * —— 慢速 Flash/存储动作必须排在 Safety 事件消费者之后。stage 1/2/3/7 属
 * required, 任一失败立即 fail closed(强制锁存 FAULT_LATCHED)并放弃后续 stage,
 * 绝不允许打印 READY。boot_done 只在走到最后才置位。
 *
 * runtime_selftest: 软件级测试(不依赖真实运动), 覆盖:
 *   bootstrap 是否真正执行 / 非法状态转换拒绝 / motor arm 门禁矩阵(不打开
 *   任何生产门) / P0-1 故障后业务转换拒绝 / 故障停机链(事件→Safety Thread→
 *   FAULT_LATCHED) / fault_reset 实测源 / sensor valid 位处理;
 *   恢复必须实际重跑 required selftest(P1-15); 收尾再次确认真实 motor_arm
 *   仍然被拒(没有伪造硬件门禁)。
 *   2g(2026-10-21 追加): 分列报告许可请求 / 焊盘实测 / 最终 ENN 反馈, 并校验
 *   掩码 bit5 与判定一致; 限位配置如实标 ENABLED / DISABLED_BY_CONFIG。
 *   ⚠ PC.6 现在是 TMC_ENN 状态反馈输入, 软件不再有独立急停输入; 板上急停是纯硬件链。
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
 * 安全默认全灭。极性确认后置 RT_TRUE, 模式逻辑即生效。硬件 pending。 */
#ifndef UI_OUTPUT_ENABLED
#define UI_OUTPUT_ENABLED   RT_FALSE
#endif

/* stage 5 存储 bring-up 结果(含 Flash 探测 + 1 MiB log 扫描)。
 * 只反映"本次启动真的走通没有", 不做任何推断。 */
static rt_uint8_t ns_flash_ok_cached = 0;
/* Fix A: bootstrap 是否已完整跑到 stage 11 */
static rt_bool_t boot_done = RT_FALSE;
/* Fix A: bootstrap 中止于哪个 required stage(0 = 未中止) */
static int boot_abort_stage = 0;

/* ---------- system_status ---------- */

/* D2: 诊断模式必须走显式枚举→字符串映射。旧实现把 diag_mode_t 枚举值直接
 * 传给 %s, MONITOR_ONLY(0) 被当成 NULL 指针, 真机打印 "mode=(null)"。
 * 未知取值如实报 UNKNOWN, 不猜。 */
static const char *diag_mode_name(diag_mode_t m)
{
    switch (m)
    {
    case DIAG_MODE_MONITOR_ONLY:        return "MONITOR_ONLY";
    case DIAG_MODE_ACTIVE_PROTECTION:   return "ACTIVE_PROTECTION";
    default:                            return "UNKNOWN";
    }
}

void system_status(void)
{
    motor_snapshot_t snap;
    project_config_t cfgs;
    const project_config_t *cfg = &cfgs;
    rt_uint32_t gates;

    project_config_get_snapshot(&cfgs);
    gates = motor_get_gate_fail_mask();

    rt_kprintf("=== SYSTEM STATUS ===\n");

    rt_kprintf("[Boot]   bootstrap-complete=%s abort-stage=%d\n",
               boot_done ? "YES" : "NO", boot_abort_stage);

    rt_kprintf("[Safety] state=%s fault=%u irq_gate=%s polarity=%s protect_ready=%s\n",
               safety_state_name(safety_state_get()), safety_fault_get(),
               safety_irq_attached() ? "OPEN" : "CLOSED",
               safety_polarity_validated() ? "CONFIRMED" : "not-confirmed",
               safety_protection_ready() ? "YES" : "NO");
    rt_kprintf("[Safety] gpio_ready=%s thread=%s drv_en_pad_low=%s\n",
               safety_gpio_ready() ? "YES" : "NO",
               subsys_health_name(safety_thread_get_health()),
               safety_drv_enable_is_low() ? "YES" : "NO/unknown");

    /* 追加(2026-10-21): 许可请求 / MCU 焊盘实测 / 最终 ENN 反馈三者分列。
     * PC.6 = 板上 TMC_ENN(H6-5)状态反馈, **不是**急停按钮输入; 板上急停是纯硬件链。
     * 全部是只读采样: 不写脚、不产生 UART 流量。 */
    {
        rt_int32_t req = -1, pad = -1, fb = -1;
        rt_bool_t  fb_ok = RT_FALSE;
        rt_uint8_t want;

        safety_fb_snapshot(&req, &pad, &fb, &fb_ok);
        want = (req == PIN_HIGH) ? 1u : 0u;
        rt_kprintf("[Safety] request=%s pad=%d expected_ENN=%s ENN_fb=%d(%s) verdict=%s\n",
                   req < 0 ? "NEVER-VERIFIED" : (req == PIN_HIGH ? "HIGH" : "LOW"),
                   (int)pad, want ? "LOW" : "HIGH", (int)fb,
                   fb_ok ? "resolved" : "NO-SAMPLE",
                   safety_fb_verdict_name(safety_fb_now(want)));
        rt_kprintf("[Safety] limit_inputs=%s (HIGH=safe/LOW=trigger when ENABLED);"
                   " e-stop=hardware-only, no MCU e-stop input\n",
                   safety_limit_inputs_enabled() ? "ENABLED" : "DISABLED_BY_CONFIG");
    }

    if (motor_get_snapshot(&snap) == RT_EOK)
        rt_kprintf("[Motor] state=%d cur=%u tgt=%u armed=%d drv_en_verified=%d health=%s\n",
                   snap.state, snap.current_hz, snap.target_hz, snap.armed,
                   snap.drv_en_request, subsys_health_name(motor_get_health()));
    else
        rt_kprintf("[Motor] snapshot failed\n");
    rt_kprintf("[Motor] arm gate fail mask=0x%X (%s)\n", gates,
               gates ? "arm REFUSED" : "all gate conditions pass (incl. ENN feedback)");
    rt_kprintf("[TMC]    runtime write gate mask=0x%X (0=运行态条件全通,"
               " 编译期门未翻转前仍然一位不写)\n",
               (unsigned)tmc2209_get_write_gate_mask());

    rt_kprintf("[Sensor] thread=%s\n",
               subsys_health_name(sensor_service_get_health()));

    rt_kprintf("[TMC]    %s drive_cfg=%s\n", subsys_health_name(tmc2209_get_health()),
               tmc_drv_cfg_state_name(tmc2209_get_config_state()));
    rt_kprintf("[IMU]    %s\n", subsys_health_name(adxl345_get_health()));
    rt_kprintf("[ADC]    %s cal=%s\n",
               subsys_health_name(current_adc_get_health()),
               current_adc_cal_source() == CURRENT_ADC_CAL_MEASURED ? "MEASURED"
               : current_adc_cal_source() == CURRENT_ADC_CAL_THEORETICAL ? "THEORETICAL"
               : "NONE");
    rt_kprintf("[Flash]  storage bring-up=%s (spi1)\n",
               ns_flash_ok_cached ? "OK" : "NOT-OK this boot");
    rt_kprintf("[Storage]%s (ns_params/log)\n",
               project_config_get_health() == SUBSYS_OK ? " OK"
               : " DEGRADED(safe defaults)");
    rt_kprintf("[Config] %s valid=%s cal_source=%d\n",
               subsys_health_name(project_config_get_health()),
               project_config_is_valid() ? "YES" : "NO",
               cfg->cur_cal_source);
    rt_kprintf("[Diag]   %s verdict=%d mode=%s\n",
               subsys_health_name(diagnosis_get_health()),
               diagnosis_get_verdict(), diag_mode_name(diagnosis_get_mode()));
    rt_kprintf("[Blackbox] %s\n", subsys_health_name(blackbox_get_health()));
    rt_kprintf("[UI]     led/buzzer output=%s (polarity hardware-pending)\n",
               UI_OUTPUT_ENABLED ? "ENABLED" : "SAFE-OFF");
}

/* ---------- UI 管理线程(单一写者) ---------- */

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

/* 直接导出公共函数 system_status(无参数) —— 不再需要 wrapper */
MSH_CMD_EXPORT(system_status, print all subsystem healths and states);

static void runtime_selftest(void)
{
    int pass = 1;
    safety_state_t st0 = safety_state_get();
    rt_err_t e;
    rt_uint32_t gates;
    sensor_frame_t f;
    motor_snapshot_t snap;

    rt_kprintf("[SELFTEST] start (state0=%s)\n", safety_state_name(st0));

    /* ⓪ Fix A: bootstrap 必须真的执行过 —— 否则整个 Phase 7 runtime 是死代码 */
    if (boot_done)
        rt_kprintf("[SELFTEST] 0.bootstrap-complete OK\n");
    else
    { rt_kprintf("[SELFTEST] 0.bootstrap-complete FAIL (abort-stage=%d)\n",
                 boot_abort_stage); pass = 0; }

    /* ① 非法状态转换拒绝 */
    e = safety_transition(SAFETY_BOOT);
    if (e == -RT_EPERM)
        rt_kprintf("[SELFTEST] 1.illegal-transition REFUSED ok\n");
    else { rt_kprintf("[SELFTEST] 1.illegal-transition FAIL (e=%d)\n", e); pass = 0; }

    /* ② Fix A / A11 + F2: motor arm 五门禁矩阵。禁止为了测试临时打开任何生产门
     *    (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED / STEP_DIAG_ISOLATED_ENV_CONFIRMED /
     *    TMC_DRIVE_CONFIG_HW_VALIDATED 与 st_polarity_validated 全部保持 FALSE)。
     *    五门无条件求值, 所以掩码必须显示第 4、5 门确实参与判定。 */
    gates = motor_get_gate_fail_mask();
    rt_kprintf("[SELFTEST] 2.arm gate fail mask=0x%X"
               " (bit0 enable-path bit1 READY bit2 health bit3 protection"
               " bit4 drive-config bit5 enn-feedback)\n", gates);
    if (!(gates & MOT_GATE_ENABLE_PATH))
    { rt_kprintf("[SELFTEST] 2a.enable-path gate unexpectedly OPEN\n"); pass = 0; }
    else
        rt_kprintf("[SELFTEST] 2a.enable-path gate CLOSED ok (default)\n");
    if (!(gates & MOT_GATE_NOT_PROTECTED))
    { rt_kprintf("[SELFTEST] 2b.gate4 NOT evaluated - motor_arm missing"
                 " safety_protection_ready!\n"); pass = 0; }
    else
        rt_kprintf("[SELFTEST] 2b.gate4 (safety_protection_ready) evaluated OK\n");

    e = motor_arm();
    if (e == -RT_EPERM)
        rt_kprintf("[SELFTEST] 2c.motor-arm-gate REFUSED ok\n");
    else { rt_kprintf("[SELFTEST] 2c.motor-arm-gate FAIL (e=%d)\n", e); pass = 0; }

    if (!safety_protection_ready())
        rt_kprintf("[SELFTEST] 2d.protection-gate CLOSED ok (default)\n");
    else { rt_kprintf("[SELFTEST] 2d.protection-gate unexpectedly OPEN\n"); pass = 0; }

    /* ②e F2b P0-A: STEP 诊断借用门。使能链未验收且未确认隔离环境时, 任何能输出
     *    STEP 的诊断路径都必须被拒 —— 现板与门/非门损坏, "焊盘 LOW"证明不了
     *    TMC_ENN 已禁能, 所以这一步是在证明"接上电机也发不出脉冲"。 */
    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED && !STEP_DIAG_ISOLATED_ENV_CONFIRMED)
    {
        e = motor_pwm_grant_to_diag();          /* 纯只读判定, 不碰 PWM/GPIO */
        if (e != RT_EOK)
            rt_kprintf("[SELFTEST] 2e.step-diag-permission REFUSED ok (e=%d)\n", (int)e);
        else { rt_kprintf("[SELFTEST] 2e FAIL: STEP 输出被许可(危险)\n"); pass = 0; }
    }
    else rt_kprintf("[SELFTEST] 2e.step-diag-permission SKIP (某许可宏已翻转)\n");

    /* ②f F2b P0-B: 配置写入的运行态门禁必须至少挡住编译期那一位(纯只读, 无 UART) */
    {
        rt_uint32_t wg = tmc2209_get_write_gate_mask();

        rt_kprintf("[SELFTEST] 2f.tmc-write-gate mask=0x%X\n", wg);
        if (!TMC_DRIVE_CONFIG_HW_VALIDATED && !(wg & TMC_WGATE_HW_NOT_VALIDATED))
        { rt_kprintf("[SELFTEST] 2f FAIL: 编译期门 FALSE 却没反映到运行态掩码\n"); pass = 0; }
        else if (wg)
            rt_kprintf("[SELFTEST] 2f.write-gate CLOSED ok (apply 不会发出任何写)\n");
        else rt_kprintf("[SELFTEST] 2f 注意: 运行态门禁全通(仍受编译期门约束)\n");
    }

    /* ②g F2b/2026-10-21: ENN 状态反馈(PC.6 = 板上 TMC_ENN / H6-5)与限位配置如实报告。
     *    纯只读(2 次 rt_pin_read), 不写脚、不产生 UART 流量、不要求特定电平 ——
     *    反馈本身正常与否属于硬件事实(换件后待验收), 本步只证明:
     *      1) 三件事被分开报告: 许可请求 / MCU 焊盘实测 / 最终 ENN 反馈;
     *      2) 追加的第 6 位掩码确实与判定结果一致(不一致 = 掩码没反映真实状态)。
     *    ⚠ 通过本步**不等于**反馈线完整性或整链已验收(RISK-04 仍开放)。 */
    {
        rt_int32_t req = -1, pad = -1, fb = -1;
        rt_bool_t  fb_ok = RT_FALSE;
        rt_uint8_t want;
        safety_fb_verdict_t fv;
        rt_uint32_t g2;

        safety_fb_snapshot(&req, &pad, &fb, &fb_ok);
        want = (req == PIN_HIGH) ? 1u : 0u;
        fv   = safety_fb_now(want);
        g2   = motor_get_gate_fail_mask();

        rt_kprintf("[SELFTEST] 2g.enn-feedback request=%s pad=%d expected_ENN=%s"
                   " fb=%d(%s) verdict=%s\n",
                   req < 0 ? "NEVER-VERIFIED" : (req == PIN_HIGH ? "HIGH" : "LOW"),
                   (int)pad, want ? "LOW" : "HIGH", (int)fb,
                   fb_ok ? "resolved" : "NO-SAMPLE", safety_fb_verdict_name(fv));
        rt_kprintf("[SELFTEST] 2g.limit_inputs=%s (HIGH=safe/LOW=trigger when ENABLED);"
                   " PC.6 is TMC_ENN feedback, NOT an e-stop input;"
                   " board e-stop = hardware chain only\n",
                   safety_limit_inputs_enabled() ? "ENABLED" : "DISABLED_BY_CONFIG");
        if ((fv != SAFETY_FB_OK) != ((g2 & MOT_GATE_FB_MISMATCH) != 0u))
        {
            rt_kprintf("[SELFTEST] 2g FAIL: 反馈判定(%s)与掩码 bit5(0x%X)不一致\n",
                       safety_fb_verdict_name(fv), (unsigned)g2);
            pass = 0;
        }
        else rt_kprintf("[SELFTEST] 2g.feedback-gate consistent ok (mask=0x%X)\n",
                        (unsigned)g2);
    }

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
    else { rt_kprintf("[SELFTEST] 4.fault-reset FAIL (state=%s)\n",
                      safety_state_name(safety_state_get())); pass = 0; }

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

    /* ⑥ 收尾: 没有伪造任何硬件门, 真实 motor_arm 必须仍然拒绝 */
    if (motor_arm() == -RT_EPERM)
        rt_kprintf("[SELFTEST] 6.arm-still-refused OK (no gate was faked)\n");
    else
    { rt_kprintf("[SELFTEST] 6.arm-still-refused FAIL - motor ARMED. disarm now\n");
      (void)motor_disarm(); pass = 0; }

    motor_get_snapshot(&snap);
    rt_kprintf("[SELFTEST] %s (final state=%s motor=%d)\n",
               pass ? "ALL PASS" : "FAILED",
               safety_state_name(safety_state_get()), snap.state);
}
MSH_CMD_EXPORT(runtime_selftest, software-level runtime self test (no motion));

static void system_selftest(void)
{
    (void)safety_startup_selftest();
    system_status();
}
MSH_CMD_EXPORT(system_selftest, rerun safety selftest and print full status);

/* ---------- Fix A: 显式业务层 bootstrap ----------
 * required stage 失败 → boot_abort(): 强制 FAULT_LATCHED + 放弃后续 stage +
 * 返回错误, 且 boot_done 保持 FALSE。全部 stage 幂等, 允许修复后重试。 */

static const char *boot_res(rt_err_t e) { return e == RT_EOK ? "OK" : "FAIL"; }

static rt_err_t boot_abort(rt_err_t e, int stage)
{
    boot_abort_stage = stage;
    boot_done = RT_FALSE;
    rt_kprintf("[BOOT] ABORTED at required stage %d (err=%d)"
               " - fail closed. runtime NOT started. NOT ready.\n",
               stage, (int)e);
    /* 失败项可能正是状态机核心本身: safety_force_shutdown 对未初始化的
     * 锁/事件是安全的(内部有 *_ok 守卫), 且必须尽力拉低使能脚。 */
    safety_force_shutdown(FAULT_BOOT);
    return (e == RT_EOK) ? -RT_ERROR : e;
}

rt_err_t supervisor_boot(void)
{
    rt_err_t e;

    if (boot_done) return RT_EOK;

    rt_kprintf("\r\n[BOOT] Phase-7 runtime bootstrap start\r\n");

    /* ---- 1 safe GPIO (REQUIRED): 上电安全态 + DRV_ENABLE 写+回读 LOW ---- */
    e = safety_gpio_boot();
    rt_kprintf("[BOOT] 1 safe-gpio ....... %s\r\n", boot_res(e));
    if (e != RT_EOK) return boot_abort(e, 1);

    /* ---- 2 safety state + event core (REQUIRED) ---- */
    e = safety_state_boot();
    rt_kprintf("[BOOT] 2 safety-state .... %s\r\n", boot_res(e));
    if (e != RT_EOK) return boot_abort(e, 2);

    /* ---- 3 Safety Thread (REQUIRED): 事件消费者必须先于慢速存储存在 ---- */
    e = safety_thread_init();
    rt_kprintf("[BOOT] 3 safety-thread ... %s\r\n", boot_res(e));
    if (e != RT_EOK) return boot_abort(e, 3);

    /* ---- 4 自检所需硬件服务(信息性: stage 11 才是权威判定) ---- */
    {
        rt_err_t et = tmc2209_init();
        rt_err_t ei = adxl345_init();
        rt_err_t ea = current_adc_boot();
        rt_kprintf("[BOOT] 4 hw-services ... tmc=%s imu=%s adc=%s"
                   " (re-checked at stage 11)\r\n",
                   boot_res(et), boot_res(ei), boot_res(ea));
    }

    /* ---- 5 storage(刻意排在 Safety Thread 之后: 1 MiB log 扫描要数秒) ---- */
    e = ns_storage_init();
    ns_flash_ok_cached = (e == RT_EOK) ? 1 : 0;
    rt_kprintf("[BOOT] 5 storage ......... %s%s\r\n", boot_res(e),
               e == RT_EOK ? "" : " (config/blackbox will degrade)");

    /* ---- 6 project config(失败=安全默认+DEGRADED, 不中止) ---- */
    e = project_config_boot();
    rt_kprintf("[BOOT] 6 project-config .. %s valid=%s\r\n", boot_res(e),
               project_config_is_valid() ? "YES" : "NO(safe defaults)");

    /* ---- 7 motor + sensor service (REQUIRED) ---- */
    e = motor_init();
    rt_kprintf("[BOOT] 7 motor-service ... %s health=%s\r\n", boot_res(e),
               subsys_health_name(motor_get_health()));
    if (e != RT_EOK) return boot_abort(e, 7);
    e = sensor_service_init();
    rt_kprintf("[BOOT] 7 sensor-service .. %s\r\n", boot_res(e));
    if (e != RT_EOK) return boot_abort(e, 7);

    /* ---- 8 diagnosis(只出判据; ACTIVE 模式另有标定门禁) ---- */
    e = diagnosis_init();
    rt_kprintf("[BOOT] 8 diagnosis ....... %s\r\n", boot_res(e));

    /* ---- 9 blackbox(worker prio 18; trigger 侧 O(1) 不等 Flash) ---- */
    e = blackbox_init();
    rt_kprintf("[BOOT] 9 blackbox ........ %s\r\n", boot_res(e));

    /* ---- 10 UI 管理线程(单一写者; 极性未确认前安全全灭) ---- */
    ui_manager_init();
    rt_kprintf("[BOOT] 10 ui-manager ..... started (output=%s)\r\n",
               UI_OUTPUT_ENABLED ? "ENABLED" : "SAFE-OFF");

    /* ---- 11 启动自检 + 最终 READY 决策(唯一进 READY 的入口) ---- */
    e = safety_startup_selftest();
    rt_kprintf("[BOOT] 11 startup-selftest %s -> state=%s\r\n", boot_res(e),
               safety_state_name(safety_state_get()));

    boot_done = RT_TRUE;          /* 只有真正走完才算 bootstrap 完成 */
    rt_kprintf("[BOOT] Phase-7 runtime bootstrap complete (state=%s)\r\n",
               safety_state_name(safety_state_get()));
    return RT_EOK;
}
