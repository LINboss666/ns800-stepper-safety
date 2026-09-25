/*
 * motor.c - 步进电机运动服务 (Phase 7-B, 审查修复 P0-4/P1-8/9/10)
 *
 * 结构: EPWM1_A 输出 STEP 脉冲, PA.2 输出 DIR, PC.23 输出使能"请求"。
 * 斜坡线程(优先级 8, 10ms 节拍)按线性斜率把 current_hz 推向 target_hz。
 *
 * 安全:
 *   - MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_FALSE 期间 motor_arm() 一律拒绝
 *     (ENN 硬件链未验收: J4-21 终验未做 + 扩展板使能链 PCB 漏画)。
 *   - DRV_ENABLE 请求脚常态 LOW; 仅 arm 成功且运行时拉高。
 *   - P0-4: PWM apply 失败 → fail-closed(PWM off + armed=0 + target/current=0
 *     + DRV_ENABLE LOW)并 post EVT_SOFT_FAULT 交 Safety 锁死。
 *     释放 mot_lock 之后才发事件, 避免重入死锁。
 *   - P1-9: motor_start 成功前必须完成 READY→RUN 状态转换(经 safety_transition,
 *     禁止直写私有 state); 斜坡减速到 0 后 RUN→READY。Fault 仍只能走 Safety。
 *   - P1-10: arm 时 DRV_ENABLE 写失败 → 回滚 armed/输出(fail closed)。
 *   - P1-8: 斜坡每步 clamp 到 target(消除目标附近振荡); mot_ramp_step 为纯函数,
 *     motor_ramp_selftest 做确定性验证。
 *
 * 与 pwm_test(诊断)的关系: motor 非 IDLE 时 pwm_test 拒绝执行, 防止双写 EPWM。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "motor.h"
#include "safety_state.h"

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

static rt_err_t mot_drv_en_request(rt_uint8_t level)
{
    rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);

    if (en < 0) return -RT_ERROR;
    rt_pin_write(en, level ? PIN_HIGH : PIN_LOW);
    mot.drv_en_request = level;
    return RT_EOK;
}

static void mot_lock_take(void)
{
    if (mot_lock_ok) rt_mutex_take(&mot_lock, RT_WAITING_FOREVER);
}

static void mot_lock_release(void)
{
    if (mot_lock_ok) rt_mutex_release(&mot_lock);
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
    mot_drv_en_request(0);
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
            mot.state = (tgt == 0) ? MOTOR_IDLE : MOTOR_CRUISE;
            mot_lock_release();

            /* P1-9: 减速到 0(受控停止完成) → RUN 回 READY(经 transition API) */
            if (tgt == 0 && was_moving && mot.armed)
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
    mot_drv_en_request(0);                      /* 请求=LOW(默认禁止) */

    mot_tid = rt_thread_create("motor", motor_ramp_entry, RT_NULL,
                               MOTOR_THREAD_STACK, MOTOR_THREAD_PRIO, 10);
    if (mot_tid == RT_NULL) { mot.health = SUBSYS_FAILED; return -RT_ERROR; }
    rt_thread_startup(mot_tid);

    mot.health = SUBSYS_OK;
    rt_kprintf("[MOT] init OK (enable-path-validated=%s, arm gate ACTIVE)\n",
               MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ? "YES" : "NO");
    return RT_EOK;
}

rt_err_t motor_arm(void)
{
    rt_err_t e;

    /* 门禁 1: 硬件使能链未验收 —— 刻意的失效安全门 */
    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)
    {
        rt_kprintf("[MOT] arm REFUSED: hardware enable path NOT validated\n");
        return -RT_EPERM;
    }
    /* 门禁 2: 安全状态机必须在 READY */
    if (safety_state_get() != SAFETY_READY)
    {
        rt_kprintf("[MOT] arm REFUSED: safety state=%s (need READY)\n",
                   safety_state_name(safety_state_get()));
        return -RT_EPERM;
    }
    /* 门禁 3: 子系统健康 */
    if (mot.health == SUBSYS_FAILED || !mot_dev_ok)
    {
        rt_kprintf("[MOT] arm REFUSED: motor subsystem health=%s\n",
                   subsys_health_name(mot.health));
        return -RT_EPERM;
    }

    /* P1-10: 先写使能请求, 成功才置 armed; 失败回滚 fail-closed */
    e = mot_drv_en_request(1);
    if (e != RT_EOK)
    {
        rt_kprintf("[MOT] arm FAILED: DRV_ENABLE write error (%d), rollback\n", e);
        mot_lock_take();
        mot.armed = 0;
        mot.current_hz = 0;
        mot.target_hz = 0;
        mot_pwm_output_off();
        mot_lock_release();
        mot_drv_en_request(0);
        return e;
    }

    mot_lock_take();
    mot.armed = 1;
    mot_lock_release();
    rt_kprintf("[MOT] ARMED (drv_en request=HIGH)\n");
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

    e = mot_drv_en_request(0);                  /* P1-10: 检查并报告 */
    if (e != RT_EOK)
        rt_kprintf("[MOT] disarm WARNING: DRV_ENABLE write failed (%d)\n", e);
    rt_kprintf("[MOT] DISARMED\n");
    return e;
}

rt_err_t motor_set_direction(rt_uint8_t dir)
{
    rt_base_t d = safety_pin(PIN_NAME_TMC_DIR);

    if (d < 0) return -RT_ERROR;
    rt_pin_write(d, dir ? PIN_HIGH : PIN_LOW);
    mot_lock_take();
    mot.dir = dir ? 1 : 0;
    mot_lock_release();
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

    e = mot_drv_en_request(0);                  /* P1-10: 检查并报告 */
    if (e != RT_EOK)
        rt_kprintf("[MOT] estop WARNING: DRV_ENABLE write failed (%d)\n", e);
    rt_kprintf("[MOT] EMERGENCY STOP\n");
    return RT_EOK;
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
