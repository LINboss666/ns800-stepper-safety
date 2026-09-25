/*
 * motor.c - 步进电机运动服务 (Phase 7-B, Fix A 门禁/回读/所有权加固)
 *
 * 结构: EPWM1_A 输出 STEP 脉冲, PA.2 输出 DIR, PC.23 输出使能"请求"。
 * 斜坡线程(优先级 8, 10ms 节拍)按线性斜率把 current_hz 推向 target_hz。
 *
 * 安全:
 *   - MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_FALSE 期间 motor_arm() 一律拒绝
 *     (ENN 硬件链未验收: J4-21 终验未做 + 扩展板使能链 PCB 漏画)。
 *   - DRV_ENABLE 常态 LOW; 仅 arm 成功且运行时拉高。
 *   - P1-9: motor_start 成功前必须完成 READY→RUN 状态转换(经 safety_transition,
 *     禁止直写私有 state); 斜坡减速到 0 后 RUN→READY。Fault 仍只能走 Safety。
 *   - P1-10: arm 时 DRV_ENABLE 写失败 → 回滚 armed/输出(fail closed)。
 *   - P1-8: 斜坡每步 clamp 到 target(消除目标附近振荡); mot_ramp_step 为纯函数,
 *     motor_ramp_selftest 做确定性验证。
 *
 * Fix A 加固:
 *   - A3: motor_arm 四道门禁(使能链验收 / 状态 READY / 本子系统健康 /
 *     safety_protection_ready())全部无条件求值并保留失败位掩码, 供真机确认
 *     第 4 门确实参与判定。任何一门失败都拒绝。
 *   - A7: PC.23 是安全关键输出, rt_pin_write 无返回值 → 一律经
 *     safety_drv_enable_write() 做"写 + settle + 回读 (+重试 1 次)"确认。
 *     回读只证明 MCU 焊盘电平, 不替代 ENN 整链硬件验收。
 *     motor_emergency_stop() 现在向上传播确认失败(safety_force_shutdown 会
 *     记录 CRITICAL 并仍然锁存 FAULT_LATCHED)。
 *     motor_init() 若连初始 LOW 都无法确认 → health=FAILED 且不创建斜坡线程。
 *   - A8: motor_set_direction() 只允许完全静止(current_hz==0 且 IDLE/FAULT);
 *     开环步进运行中翻 DIR 等同注入丢步/堵转。
 *   - A9: EPWM1 ch0 生产 owner = 本服务。诊断 pwm_test 必须过
 *     motor_pwm_grant_to_diag(); 反向由 step_pwm_output_active() 挡住 start。
 *   - A10: 增加 motor_status/arm/disarm/dir/target/start/stop MSH 表面,
 *     全部只调正式 API, 不直接写 GPIO/PWM。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdlib.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "motor.h"
#include "safety_state.h"
#include "safety_thread.h"
#include "step_pwm.h"

#define MOTOR_PWM_DEV       EPWM_STEP_DEV_NAME   /* "epwm1" */
#define MOTOR_PWM_CH        0                    /* EPWMX_A = PA0 */
#define MOTOR_RAMP_PERIOD_MS 10
#define MOTOR_THREAD_STACK   768
#define MOTOR_THREAD_PRIO    8                    /* Safety(4) > Sensor(7) > Motor(8) */

#define MOTOR_DEFAULT_ACCEL 2000u    /* Hz/s */
#define MOTOR_DEFAULT_DECEL 2000u
#define MOTOR_MAX_HZ        20000u

static struct rt_device_pwm *mot_pwm = RT_NULL;
static rt_bool_t mot_dev_ok = RT_FALSE;
static motor_snapshot_t mot = {
    MOTOR_IDLE, 0, 0, MOTOR_DEFAULT_ACCEL, MOTOR_DEFAULT_DECEL, 0, 0, 0,
    SUBSYS_UNINIT
};
static struct rt_mutex mot_lock;
static rt_bool_t mot_lock_ok = RT_FALSE;
static rt_thread_t mot_tid = RT_NULL;

static void mot_lock_take(void)
{
    if (mot_lock_ok) rt_mutex_take(&mot_lock, RT_WAITING_FOREVER);
}

static void mot_lock_release(void)
{
    if (mot_lock_ok) rt_mutex_release(&mot_lock);
}

/* ---------- Fix A / A7: DRV_ENABLE 写 + 回读确认 ----------
 * 唯一允许写 PC.23 的地方(经由 safety_gpio 的确认实现)。成功才更新影子字段,
 * 因此 drv_en_request 语义 = "最近一次经回读确认的焊盘电平"。
 * 调用约定: 不得持 mot_lock 调用(内部会更新快照字段并可能 mdelay)。 */
static rt_err_t mot_drv_en_request(rt_uint8_t level)
{
    rt_err_t e = safety_drv_enable_write(level);

    if (e == RT_EOK)
        mot.drv_en_request = level;
    else
        rt_kprintf("[MOT] !! DRV_ENABLE %s NOT verified (err=%d) - pad state unknown\n",
                   level ? "HIGH" : "LOW", (int)e);
    return e;
}

/* 立即关 STEP 输出 */
static void mot_pwm_output_off(void)
{
    if (mot_dev_ok)
        rt_pwm_disable(mot_pwm, MOTOR_PWM_CH);
}

/* 设置指定频率的 50% 占空比脉冲; hz==0 时关闭输出 */
static rt_err_t mot_pwm_apply(rt_uint32_t hz)
{
    rt_err_t e;

    if (!mot_dev_ok) return -RT_ERROR;
    if (hz == 0)
    {
        rt_pwm_disable(mot_pwm, MOTOR_PWM_CH);
        return RT_EOK;
    }
    e = rt_pwm_set(mot_pwm, MOTOR_PWM_CH, 1000000000u / hz, 500000000u / hz);
    if (e != RT_EOK) return e;
    return rt_pwm_enable(mot_pwm, MOTOR_PWM_CH);
}

/* ---------- P1-8: 斜坡纯函数(每步 clamp 到 target) ---------- */
static rt_uint32_t mot_ramp_step(rt_uint32_t cur, rt_uint32_t tgt,
                                 rt_uint32_t accel, rt_uint32_t decel)
{
    rt_int32_t n;

    if (cur == tgt) return cur;
    if (cur < tgt)
    {
        n = (rt_int32_t)cur + (rt_int32_t)(accel * MOTOR_RAMP_PERIOD_MS / 1000);
        if (n > (rt_int32_t)tgt) n = (rt_int32_t)tgt;   /* clamp: 不越过目标 */
        return (rt_uint32_t)n;
    }
    n = (rt_int32_t)cur - (rt_int32_t)(decel * MOTOR_RAMP_PERIOD_MS / 1000);
    if (n < (rt_int32_t)tgt) n = (rt_int32_t)tgt;       /* clamp: 不越过目标 */
    return (rt_uint32_t)n;
}

/* ---------- P0-4: fail-closed(PWM apply 失败路径) ----------
 * 调用约定: 必须在 mot_lock 释放后调用(内部不再拿锁),
 * 事件 → Safety Thread → force_shutdown → motor_emergency_stop(幂等)。 */
static void mot_fail_closed(void)
{
    mot_pwm_output_off();
    if (mot_drv_en_request(0) != RT_EOK)
        rt_kprintf("[MOT] !! FAIL-CLOSED but DRV_ENABLE LOW UNVERIFIED !!\n");
    safety_post_event(EVT_SOFT_FAULT);
    rt_kprintf("[MOT] FAIL-CLOSED (pwm error), EVT_SOFT_FAULT posted\n");
}

/* 斜坡线程: 10ms 节拍, 线性逼近 target */
static void motor_ramp_entry(void *param)
{
    (void)param;

    while (1)
    {
        rt_uint32_t tgt;
        rt_uint8_t was_moving;
        rt_uint8_t armed;

        mot_lock_take();
        tgt = mot.target_hz;

        if (mot.state == MOTOR_IDLE || mot.state == MOTOR_FAULT)
        {
            mot_lock_release();
            rt_thread_mdelay(MOTOR_RAMP_PERIOD_MS);
            continue;
        }

        if (!mot.armed) tgt = 0;

        if (mot.current_hz == tgt)
        {
            was_moving = (mot.state == MOTOR_CRUISE ||
                          mot.state == MOTOR_ACCEL || mot.state == MOTOR_DECEL);
            armed = mot.armed;                      /* Fix A: 锁内取快照 */
            mot.state = (tgt == 0) ? MOTOR_IDLE : MOTOR_CRUISE;
            mot_lock_release();

            /* P1-9: 减速到 0(受控停止完成) → RUN 回 READY(经 transition API) */
            if (tgt == 0 && was_moving && armed)
            {
                if (safety_transition(SAFETY_READY) != RT_EOK)
                    rt_kprintf("[MOT] RUN->READY transition refused\n");
            }
            rt_thread_mdelay(MOTOR_RAMP_PERIOD_MS);
            continue;
        }

        mot.current_hz = mot_ramp_step(mot.current_hz, tgt,
                                       mot.accel_hz_s, mot.decel_hz_s);

        if (mot.current_hz == 0)
        {
            mot_pwm_output_off();
            mot.state = (tgt == 0) ? MOTOR_IDLE : MOTOR_ACCEL;
        }
        else
        {
            if (mot_pwm_apply(mot.current_hz) != RT_EOK)
            {
                /* P0-4: fail-closed(锁内只改状态, 锁外发事件) */
                mot_pwm_output_off();
                mot.armed = 0;
                mot.current_hz = 0;
                mot.target_hz = 0;
                mot.state = MOTOR_FAULT;
                mot_lock_release();
                mot_fail_closed();
                continue;
            }
            mot.state = (mot.current_hz < tgt) ? MOTOR_ACCEL
                      : (mot.current_hz > tgt) ? MOTOR_DECEL : MOTOR_CRUISE;
        }
        mot_lock_release();
        rt_thread_mdelay(MOTOR_RAMP_PERIOD_MS);
    }
}

/* ---------- Fix A / A11: 门禁矩阵(纯只读 无副作用) ----------
 * 返回"失败门"位掩码; 0 = 四门全通。全部无条件求值, 这样真机可以看到
 * 第 4 门(safety_protection_ready)确实参与判定, 而不是被前面的门短路掉。 */
rt_uint32_t motor_get_gate_fail_mask(void)
{
    rt_uint32_t bad = 0;

    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)        bad |= MOT_GATE_ENABLE_PATH;
    if (safety_state_get() != SAFETY_READY)           bad |= MOT_GATE_NOT_READY;
    if (mot.health == SUBSYS_FAILED || !mot_dev_ok)   bad |= MOT_GATE_UNHEALTHY;
    if (!safety_protection_ready())                   bad |= MOT_GATE_NOT_PROTECTED;

    return bad;
}

static void mot_print_gate_fail(rt_uint32_t bad)
{
    rt_kprintf("[MOT] arm gate mask=0x%X:", bad);
    if (bad & MOT_GATE_ENABLE_PATH)   rt_kprintf(" enable-path-not-validated");
    if (bad & MOT_GATE_NOT_READY)     rt_kprintf(" safety-not-READY");
    if (bad & MOT_GATE_UNHEALTHY)     rt_kprintf(" subsystem-unhealthy");
    if (bad & MOT_GATE_NOT_PROTECTED) rt_kprintf(" protection-not-ready");
    rt_kprintf("\n");
    rt_kprintf("[MOT] gate4 detail: irq_gate=%s polarity=%s gpio_ready=%s\n",
               safety_irq_attached() ? "OPEN" : "CLOSED",
               safety_polarity_validated() ? "CONFIRMED" : "not-confirmed",
               safety_gpio_ready() ? "YES" : "NO");
}

/* ---------- 正式 API (motor.h) ---------- */

rt_err_t motor_init(void)
{
    rt_err_t e;

    if (mot_tid != RT_NULL) return RT_EOK;      /* 幂等 */

    if (!mot_lock_ok)
    {
        if (rt_mutex_init(&mot_lock, "mot", RT_IPC_FLAG_PRIO) != RT_EOK)
            return -RT_ERROR;
        mot_lock_ok = RT_TRUE;
    }

    mot_pwm = (struct rt_device_pwm *)rt_device_find(MOTOR_PWM_DEV);
    if (mot_pwm == RT_NULL)
    {
        rt_kprintf("[MOT] %s not found (check BSP_USING_EPWM1)\n", MOTOR_PWM_DEV);
        mot.health = SUBSYS_FAILED;
        return -RT_ERROR;
    }
    mot_dev_ok = RT_TRUE;

    mot_pwm_output_off();
    mot_lock_take();
    mot.state = MOTOR_IDLE;
    mot.current_hz = 0;
    mot.target_hz = 0;
    mot.armed = 0;
    mot_lock_release();

    /* Fix A / A7: 初始"禁能"必须是经回读确认的 LOW, 否则不创建斜坡线程 */
    e = mot_drv_en_request(0);
    if (e != RT_EOK)
    {
        rt_kprintf("[MOT] init FAILED: DRV_ENABLE LOW unverified - no ramp thread\n");
        mot_dev_ok = RT_FALSE;
        mot.health = SUBSYS_FAILED;
        return e;
    }

    mot_tid = rt_thread_create("motor", motor_ramp_entry, RT_NULL,
                               MOTOR_THREAD_STACK, MOTOR_THREAD_PRIO, 10);
    if (mot_tid == RT_NULL) { mot.health = SUBSYS_FAILED; return -RT_ERROR; }
    rt_thread_startup(mot_tid);

    mot.health = SUBSYS_OK;
    rt_kprintf("[MOT] init OK (enable-path-validated=%s, arm gate ACTIVE,"
               " DRV_ENABLE LOW verified)\n",
               MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ? "YES" : "NO");
    return RT_EOK;
}

rt_err_t motor_arm(void)
{
    rt_uint32_t bad;
    rt_err_t e;

    /* Fix A / A3: 四门全部求值后统一判定(见 motor_get_gate_fail_mask) */
    bad = motor_get_gate_fail_mask();
    if (bad)
    {
        rt_kprintf("[MOT] arm REFUSED (gates failing)\n");
        mot_print_gate_fail(bad);
        return -RT_EPERM;
    }

    /* 诊断输出仍占用 EPWM ch0 时禁止 arm(所有权未交回) */
    if (step_pwm_output_active())
    {
        rt_kprintf("[MOT] arm REFUSED: pwm_test still owns EPWM1 ch0"
                   " (run 'pwm_test stop')\n");
        return -RT_EBUSY;
    }

    /* P1-10: 先写使能请求(含回读确认), 成功才置 armed; 失败回滚 fail-closed */
    e = mot_drv_en_request(1);
    if (e != RT_EOK)
    {
        rt_kprintf("[MOT] arm FAILED: DRV_ENABLE HIGH not verified (%d),"
                   " rollback fail-closed\n", (int)e);
        mot_lock_take();
        mot.armed = 0;
        mot.current_hz = 0;
        mot.target_hz = 0;
        mot_pwm_output_off();
        mot.state = MOTOR_FAULT;
        mot_lock_release();
        if (mot_drv_en_request(0) != RT_EOK)
            rt_kprintf("[MOT] !! rollback LOW ALSO UNVERIFIED - pad unknown !!\n");
        safety_post_event(EVT_SOFT_FAULT);
        return e;
    }

    mot_lock_take();
    mot.armed = 1;
    mot_lock_release();
    rt_kprintf("[MOT] ARMED (drv_en request=HIGH readback verified)\n");
    return RT_EOK;
}

rt_err_t motor_disarm(void)
{
    rt_err_t e;

    mot_lock_take();
    mot.armed = 0;
    mot.target_hz = 0;
    mot.current_hz = 0;
    mot_pwm_output_off();
    mot.state = MOTOR_IDLE;
    mot_lock_release();

    e = mot_drv_en_request(0);                  /* P1-10 + A7: 回读确认 */
    if (e != RT_EOK)
        rt_kprintf("[MOT] !! disarm: DRV_ENABLE LOW UNVERIFIED (%d) !!\n", (int)e);
    rt_kprintf("[MOT] DISARMED\n");
    return e;
}

/* Fix A / A8: 只允许完全静止时改方向 */
rt_err_t motor_set_direction(rt_uint8_t dir)
{
    rt_base_t d;
    rt_bool_t at_rest;

    mot_lock_take();
    at_rest = (mot.current_hz == 0) &&
              (mot.state == MOTOR_IDLE || mot.state == MOTOR_FAULT);
    if (!at_rest)
    {
        rt_kprintf("[MOT] dir REFUSED: not at rest (state=%d hz=%u) -"
                   " DIR change mid-run injects a stall\n",
                   mot.state, mot.current_hz);
        mot_lock_release();
        return -RT_EBUSY;
    }
    mot_lock_release();

    d = safety_pin(PIN_NAME_TMC_DIR);
    if (d < 0) return -RT_ERROR;
    rt_pin_write(d, dir ? PIN_HIGH : PIN_LOW);
    mot_lock_take();
    mot.dir = dir ? 1 : 0;
    mot_lock_release();
    rt_kprintf("[MOT] dir=%u (at rest)\n", dir ? 1u : 0u);
    return RT_EOK;
}

rt_err_t motor_set_target_hz(rt_uint32_t hz)
{
    if (hz > MOTOR_MAX_HZ) return -RT_EINVAL;
    mot_lock_take();
    mot.target_hz = hz;
    mot_lock_release();
    return RT_EOK;
}

rt_err_t motor_start(void)
{
    rt_uint8_t armed, has_target;

    if (step_pwm_output_active())
    {
        rt_kprintf("[MOT] start REFUSED: pwm_test owns EPWM1 ch0"
                   " (run 'pwm_test stop')\n");
        return -RT_EBUSY;
    }

    mot_lock_take();
    armed = mot.armed;
    has_target = (mot.target_hz > 0);
    mot_lock_release();

    if (!armed)      { rt_kprintf("[MOT] start REFUSED: not armed\n");  return -RT_EPERM; }
    if (!has_target) { rt_kprintf("[MOT] start REFUSED: target_hz=0\n"); return -RT_EPERM; }

    /* P1-9: 运动前建立合法 READY→RUN(经 transition API, 拒绝则不起转) */
    if (safety_state_get() == SAFETY_READY)
    {
        rt_err_t e = safety_transition(SAFETY_RUN);
        if (e != RT_EOK)
        {
            rt_kprintf("[MOT] start REFUSED: READY->RUN transition refused\n");
            return e;
        }
    }
    else if (safety_state_get() != SAFETY_RUN)
    {
        rt_kprintf("[MOT] start REFUSED: safety state=%s\n",
                   safety_state_name(safety_state_get()));
        return -RT_EPERM;
    }

    /* 运动前再次确认安全保护链仍就绪(arm 与 start 之间门可能已关) */
    if (!safety_protection_ready())
    {
        rt_kprintf("[MOT] start REFUSED: protection chain no longer ready\n");
        safety_post_event(EVT_SOFT_FAULT);
        return -RT_EPERM;
    }

    mot_lock_take();
    if (mot.state == MOTOR_IDLE) mot.state = MOTOR_ACCEL;
    mot_lock_release();
    return RT_EOK;
}

rt_err_t motor_stop(void)
{
    mot_lock_take();
    mot.target_hz = 0;      /* 斜坡自动减速到 0 → IDLE + RUN→READY(保持 armed) */
    mot_lock_release();
    return RT_EOK;
}

rt_err_t motor_emergency_stop(void)
{
    rt_err_t e;

    mot_lock_take();
    mot.armed = 0;
    mot.target_hz = 0;
    mot.current_hz = 0;
    mot_pwm_output_off();
    mot.state = MOTOR_FAULT;
    mot_lock_release();

    /* P1-10 + A7: 回读确认并把结果传播给调用方(safety_force_shutdown 会记录
     * CRITICAL 但依然锁存 FAULT_LATCHED)。绝不吞掉失败。 */
    e = mot_drv_en_request(0);
    if (e != RT_EOK)
        rt_kprintf("[MOT] !! EMERGENCY STOP but DRV_ENABLE LOW UNVERIFIED (%d) !!\n",
                   (int)e);
    rt_kprintf("[MOT] EMERGENCY STOP (drv_en LOW %s)\n",
               e == RT_EOK ? "verified" : "UNVERIFIED");
    return e;
}

rt_err_t motor_get_snapshot(motor_snapshot_t *snap)
{
    if (snap == RT_NULL) return -RT_EINVAL;
    mot_lock_take();
    *snap = mot;
    mot_lock_release();
    return RT_EOK;
}

subsys_health_t motor_get_health(void) { return mot.health; }

/* ---------- Fix A / A9: EPWM1 ch0 诊断借用门 ----------
 * Motor Service 是该通道唯一生产 owner; pwm_test 只有在本服务完全静止且
 * 使能脚实测 LOW 时才可借用。全部条件在锁内取一致快照后判定。 */
rt_err_t motor_pwm_grant_to_diag(void)
{
    motor_snapshot_t snap;

    if (motor_get_snapshot(&snap) != RT_EOK)
    {
        rt_kprintf("[MOT] grant REFUSED: snapshot failed\n");
        return -RT_ERROR;
    }
    if (snap.armed)
    {
        rt_kprintf("[MOT] grant REFUSED: motor is ARMED\n");
        return -RT_EPERM;
    }
    if (snap.state != MOTOR_IDLE)
    {
        rt_kprintf("[MOT] grant REFUSED: motor state=%d (need IDLE)\n", snap.state);
        return -RT_EBUSY;
    }
    if (snap.current_hz != 0)
    {
        rt_kprintf("[MOT] grant REFUSED: current_hz=%u still nonzero\n",
                   snap.current_hz);
        return -RT_EBUSY;
    }
    if (snap.target_hz != 0)
    {
        rt_kprintf("[MOT] grant REFUSED: target_hz=%u pending\n", snap.target_hz);
        return -RT_EBUSY;
    }
    if (!safety_drv_enable_is_low())
    {
        rt_kprintf("[MOT] grant REFUSED: DRV_ENABLE pad not confirmed LOW\n");
        return -RT_EPERM;
    }
    return RT_EOK;
}

/* ---------- P1-8: 斜坡确定性自测(纯软件) ---------- */
static void motor_ramp_selftest(void)
{
    struct { rt_uint32_t cur, tgt; rt_uint32_t expect; } up[] = {
        { 0,    1000, 20   },      /* accel 2000Hz/s × 10ms = 20Hz/步 */
        { 1990, 2000, 2000 },      /* clamp: 不越过目标(原 2010 振荡 bug) */
        { 2000, 2000, 2000 },
    };
    struct { rt_uint32_t cur, tgt; rt_uint32_t expect; } dn[] = {
        { 1000, 0,    980  },      /* decel */
        { 15,   0,    0    },      /* clamp: 不越过 0(负值) */
    };
    int i, pass = 1;

    for (i = 0; i < (int)(sizeof(up) / sizeof(up[0])); ++i)
    {
        rt_uint32_t n = mot_ramp_step(up[i].cur, up[i].tgt,
                                      MOTOR_DEFAULT_ACCEL, MOTOR_DEFAULT_DECEL);
        rt_kprintf("[MOT-ST] up %u->%u: %u %s\n", up[i].cur, up[i].tgt, n,
                   n == up[i].expect ? "OK" : "FAIL");
        if (n != up[i].expect) pass = 0;
    }
    for (i = 0; i < (int)(sizeof(dn) / sizeof(dn[0])); ++i)
    {
        rt_uint32_t n = mot_ramp_step(dn[i].cur, dn[i].tgt,
                                      MOTOR_DEFAULT_ACCEL, MOTOR_DEFAULT_DECEL);
        rt_kprintf("[MOT-ST] dn %u->%u: %u %s\n", dn[i].cur, dn[i].tgt, n,
                   n == dn[i].expect ? "OK" : "FAIL");
        if (n != dn[i].expect) pass = 0;
    }
    rt_kprintf("[MOT-ST] %s\n", pass ? "PASS" : "FAILED");
}
MSH_CMD_EXPORT(motor_ramp_selftest, deterministic ramp step selftest);

/* ---------- Fix A / A10: Motor MSH 表面(全部只走正式 API) ---------- */

static void cmd_motor_status(void)
{
    motor_snapshot_t snap;
    rt_uint32_t bad;

    if (motor_get_snapshot(&snap) != RT_EOK)
    { rt_kprintf("[MOT] snapshot failed\n"); return; }

    rt_kprintf("[MOT] state=%d cur=%u tgt=%u accel=%u decel=%u dir=%u\n",
               snap.state, snap.current_hz, snap.target_hz,
               snap.accel_hz_s, snap.decel_hz_s, snap.dir);
    rt_kprintf("[MOT] armed=%d drv_en_verified_pad=%d health=%s\n",
               snap.armed, snap.drv_en_request, subsys_health_name(snap.health));
    bad = motor_get_gate_fail_mask();
    rt_kprintf("[MOT] arm gate fail mask=0x%X (%s)\n", bad,
               bad ? "arm will be REFUSED" : "all four gates pass");
    if (bad) mot_print_gate_fail(bad);
}
MSH_CMD_EXPORT_ALIAS(cmd_motor_status, motor_status, show motor state and arm gate mask);

static void cmd_motor_arm(void)      { (void)motor_arm(); }
MSH_CMD_EXPORT_ALIAS(cmd_motor_arm, motor_arm, request arm through the four gates);

static void cmd_motor_disarm(void)   { (void)motor_disarm(); }
MSH_CMD_EXPORT_ALIAS(cmd_motor_disarm, motor_disarm, disarm and verify DRV_ENABLE LOW);

static void cmd_motor_dir(int argc, char **argv)
{
    if (argc != 2 || (argv[1][0] != '0' && argv[1][0] != '1'))
    { rt_kprintf("usage: motor_dir 0 or 1\n"); return; }
    (void)motor_set_direction((rt_uint8_t)(argv[1][0] - '0'));
}
MSH_CMD_EXPORT_ALIAS(cmd_motor_dir, motor_dir, set DIR only when fully at rest);

static void cmd_motor_target(int argc, char **argv)
{
    rt_err_t e;
    long v;

    if (argc != 2) { rt_kprintf("usage: motor_target <hz>\n"); return; }
    v = strtol(argv[1], RT_NULL, 10);
    if (v < 0) { rt_kprintf("[MOT] bad hz (negative or not a number)\n"); return; }
    e = motor_set_target_hz((rt_uint32_t)v);
    rt_kprintf("[MOT] set target %d Hz: %s\n", (int)v,
               e == RT_EOK ? "OK" : "REJECTED(over MOTOR_MAX_HZ)");
}
MSH_CMD_EXPORT_ALIAS(cmd_motor_target, motor_target, set ramp target frequency in Hz);

static void cmd_motor_start(void)    { (void)motor_start(); }
MSH_CMD_EXPORT_ALIAS(cmd_motor_start, motor_start, start motion through the formal API);

static void cmd_motor_stop(void)     { (void)motor_stop(); }
MSH_CMD_EXPORT_ALIAS(cmd_motor_stop, motor_stop, controlled ramp down to zero);
