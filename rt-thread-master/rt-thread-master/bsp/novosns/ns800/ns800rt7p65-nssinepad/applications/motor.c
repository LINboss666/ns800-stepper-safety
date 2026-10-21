/*
 * motor.c - 步进电机运动服务 (Phase 7-B, Fix A 门禁/回读/所有权加固)
 *
 * 结构: EPWM1_A 输出 STEP 脉冲, PA.2 输出 DIR, PC.23 输出使能"请求"。
 * 斜坡线程(优先级 8, 10ms 节拍)按线性斜率把 current_hz 推向 target_hz。
 *
 * 安全:
 *   - MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_FALSE 期间 motor_arm() 一律拒绝
 *     (事实更正 2026-10-18: 新扩展板 MCU_DRV_ENABLE 已引出, 当前阻塞项是 U5 与门
 *     损坏 + 急停非门损坏, TMC_ENN 实测约 0V → 焊盘回读 LOW 证明不了驱动已禁能)。
 *   - DRV_ENABLE 常态 LOW; 仅 arm 成功且运行时拉高。
 *   - F2b P0-A: STEP 诊断借用门在此前只查"焊盘 LOW + 电机静止", 上面那条事实使它
 *     不再充分 → 现在必须 MOTOR_HARDWARE_ENABLE_PATH_VALIDATED 或
 *     STEP_DIAG_ISOLATED_ENV_CONFIRMED 为 TRUE 才允许任何 STEP 输出。
 *   - F2b P1-D: motor_arm 的"查门→完整性复核→请求使能"整段持 TMC 配置事务锁,
 *     与 tmc2209_apply_config() 互斥; 紧急停机路径不取该锁。
 *   - F2b P1-E: armed 期间斜坡线程按固定周期上限复核 TMC 驱动配置, 失联/复位/
 *     配置丢失/事务持续占用 → 立即停 STEP + 请求 DRV_ENABLE LOW + FAULT + 事件。
 *     软件复核只是第二道响应, 不替代硬件独立切断, 也不能反过来当成切断手段。
 *   - P1-9: motor_start 成功前必须完成 READY→RUN 状态转换(经 safety_transition,
 *     禁止直写私有 state); 斜坡减速到 0 后 RUN→READY。Fault 仍只能走 Safety。
 *   - P1-10: arm 时 DRV_ENABLE 写失败 → 回滚 armed/输出(fail closed)。
 *   - P1-8: 斜坡每步 clamp 到 target(消除目标附近振荡); mot_ramp_step 为纯函数,
 *     motor_ramp_selftest 做确定性验证。
 *
 * Fix A 加固:
 *   - A3: motor_arm 五道门禁(使能链验收 / 状态 READY / 本子系统健康 /
 *     safety_protection_ready() / TMC 驱动参数已下发)全部无条件求值并保留失败位
 *     掩码, 供真机确认第 4、5 门确实参与判定。任何一门失败都拒绝。
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
#include "tmc2209.h"

#define MOTOR_PWM_DEV       EPWM_STEP_DEV_NAME   /* "epwm1" */
#define MOTOR_PWM_CH        0                    /* EPWMX_A = PA0 */
#define MOTOR_RAMP_PERIOD_MS 10
#define MOTOR_THREAD_STACK   768
#define MOTOR_THREAD_PRIO    8                    /* Safety(4) > Sensor(7) > Motor(8) */

#define MOTOR_DEFAULT_ACCEL 2000u    /* Hz/s */
#define MOTOR_DEFAULT_DECEL 2000u
#define MOTOR_MAX_HZ        20000u

/* F2b P1-E: 运行期驱动配置复核周期。只在 armed 时才产生 UART 流量。
 *
 * ⚠ F2c R05 —— 200ms 是**复核周期**, 不是停机时延, 两者不能混为一谈:
 *   1) 检测延迟最坏 = 周期 200ms + 本轮复核耗时。复核 = 2 次可读寄存器回读,
 *      每次上限 TMC_REPLY_TIMEOUT_MS=100ms(内部 2ms 轮询), 故一轮复核 ≤ ~205ms,
 *      合计最坏 ≈ **405ms** 才可能触发作出反应(不含调度抖动)。
 *   2) 复核跑在斜坡线程(prio 8, 栈 MOTOR_THREAD_STACK=768B)内, 期间 10ms 节拍被
 *      阻塞, STEP 仍以最后设置的频率继续输出(PWM 硬件自由运行) —— 复核失败之前
 *      脉冲不会停。栈余量与真实阻塞时长均未上板实测(NOT TESTED)。
 *   3) 判定为停机后的动作是**立即**的: rt_pwm_disable + DRV_ENABLE 写 LOW 并回读,
 *      不经斜坡; 但 rt_pwm_disable 的返回值当前未判定, ENN 端电平也未实测。
 *   4) 因此: 软件守卫是第二层, 最终切断必须由独立硬件(ENN/急停链)承担; 硬件链
 *      未验收期间 motor 根本不能 armed, 这条路径实际不可达。
 *   5) 是否把 UART 守卫从斜坡线程解耦(独立低优先级线程 + 事件传递):**待上板实测
 *      复核阻塞时长与栈占用之后再决定**, 不在没有测量的情况下先改结构。
 *   ❌ 任何材料都不得写"200ms 内必停"。 */
#define MOTOR_DRV_GUARD_PERIOD_MS 200u
#define MOTOR_DRV_GUARD_TICKS  (MOTOR_DRV_GUARD_PERIOD_MS / MOTOR_RAMP_PERIOD_MS)

/* 守卫周期必须大于一个斜坡节拍, 否则每次 10ms 节拍都会跑一段 UART 复核(把节拍
 * 拖成阻塞式)。周期被改成小于 20ms 时这里直接编译失败, 和 tmc2209.c 的寄存器
 * 断言同一套路 —— 写错不会报错的配置项就用编译期钉住。 */
typedef char mot_guard_ticks_sane[(MOTOR_DRV_GUARD_TICKS >= 2u) ? 1 : -1];

static struct rt_device_pwm *mot_pwm = RT_NULL;
static rt_bool_t mot_dev_ok = RT_FALSE;
static motor_snapshot_t mot = {
    MOTOR_IDLE, 0, 0, MOTOR_DEFAULT_ACCEL, MOTOR_DEFAULT_DECEL, 0, 0, 0,
    0, MOT_GUARD_NONE,
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

/* ---------- F2b P1-E: 运行期驱动配置守卫的纯判定 ----------
 * 规则(fail closed, 且未 armed 时绝不产生 UART 流量):
 *   - 未 armed                        → 不动作(也不复核,  disarm 后芯片状态无关)
 *   - armed 但第 5 门已不再成立        → 停机(配置状态/快照被失败或作废打掉)
 *   - armed 且本轮未到复核周期         → 不动作
 *   - 复核 RT_EOK                      → 不动作, busy 计数清零
 *   - 复核 -RT_EBUSY(事务被占)         → 连续超限才停机, 否则下一轮再试
 *     (占用者是操作员的 apply / 另一条 arm 事务, 短暂占用不该误停; 但"一直复核
 *      不成"就不能当作"配置仍有效" —— 超过 MOT_GUARD_BUSY_MAX 轮立即停)
 *   - 其它错误(读不到/现值不符/EPERM)  → 停机(芯片可能断电/复位/链路已坏)
 * 本函数无副作用、不碰硬件, 由 motor_drv_guard_selftest 做确定性真值表验证。 */
void motor_guard_decide(const motor_guard_in_t *in, motor_guard_out_t *out)
{
    out->stop = 0u;
    out->reason = MOT_GUARD_NONE;
    out->busy_streak = in->busy_streak;

    if (!in->armed) { out->busy_streak = 0u; return; }

    if (!in->cfg_state_ok)
    {
        out->stop = 1u;
        out->reason = MOT_GUARD_CFG_LOST;
        return;
    }
    if (!in->due) return;

    if (in->integrity == RT_EOK)
    {
        out->busy_streak = 0u;
        return;
    }
    if (in->integrity == -RT_EBUSY)
    {
        if (in->busy_streak + 1u >= MOT_GUARD_BUSY_MAX)
        {
            out->stop = 1u;
            out->reason = MOT_GUARD_TXN_BUSY;
            out->busy_streak = in->busy_streak + 1u;
        }
        else out->busy_streak = in->busy_streak + 1u;
        return;
    }
    out->stop = 1u;
    out->reason = MOT_GUARD_INTEGRITY;
}

/* 守卫判定为停机时的执行动作。调用约定: 不得持 mot_lock 调用(内部会再取一次),
 * 与 mot_fail_closed() 同一条 fail-closed 路径。 */
static void mot_guard_shutdown(rt_uint8_t reason)
{
    mot_lock_take();
    mot.armed = 0;
    mot.target_hz = 0;
    mot.current_hz = 0;
    mot_pwm_output_off();
    mot.state = MOTOR_FAULT;
    if (mot.guard_stops < 0xFFFFu) mot.guard_stops++;
    mot.guard_reason = reason;
    mot_lock_release();

    if (mot_drv_en_request(0) != RT_EOK)
        rt_kprintf("[MOT] !! GUARD STOP but DRV_ENABLE LOW UNVERIFIED !!\n");
    /* 反馈不一致类停机如实投 EVT_DRV_FEEDBACK(黑匣子记 FAULT_DRV_FEEDBACK),
     * 不假冒急停按钮; 其余配置守卫原因沿用 EVT_SOFT_FAULT。 */
    (void)safety_post_event(reason == MOT_GUARD_ENN_FEEDBACK ? EVT_DRV_FEEDBACK
                                                             : EVT_SOFT_FAULT);
    rt_kprintf("[MOT] DRV GUARD: 驱动配置/反馈异常(reason=%d) -> 已停 STEP、请求使能 LOW、"
               "置 FAULT 并上报。!! 这是软件层第二响应, 不替代硬件独立切断 !!\n",
               (int)reason);
}

/* 斜坡线程: 10ms 节拍, 线性逼近 target */
static void motor_ramp_entry(void *param)
{
    rt_uint32_t guard_ctr = 0;
    rt_uint8_t  guard_busy_streak = 0;

    (void)param;

    while (1)
    {
        rt_uint32_t tgt;
        rt_uint8_t was_moving;
        rt_uint8_t armed;
        rt_uint8_t guard_due = 0u;
        rt_uint8_t fb_stopped = 0u;

        /* ---------- F2b P1-E: 驱动配置运行期守卫(只在 armed 时复核) ----------
         * armed 与"本轮是否到复核周期"在锁内一次取定; 复核本身必须在锁外做
         * (UART 会阻塞, 持 mot_lock 会拖停斜坡)。 */
        mot_lock_take();
        if (mot.armed)
        {
            guard_ctr++;
            if (guard_ctr >= MOTOR_DRV_GUARD_TICKS) { guard_ctr = 0; guard_due = 1u; }
        }
        else guard_ctr = 0;
        armed = mot.armed;
        mot_lock_release();

        /* 追加(2026-10-21): armed 期每拍先做一次**无 UART、无延时**的 ENN 反馈复核
         * (单次 rt_pin_read)。已确认的许可请求是 HIGH, 所以期望反馈 = LOW;
         * 不一致(硬件急停把 ENN 拉高 / 级联逻辑故障 / 样本不可得)立即停机,
         * 并跳过本拍的 UART 复核 —— 停机优先, 且不在已决定停机的拍上再阻塞。
         * ⚠ 这缩小了软件层响应窗口, 但仍然是软件第二响应, 不构成时延上界证明。 */
        if (armed)
        {
            safety_fb_verdict_t fv = safety_fb_now(1u);

            if (fv != SAFETY_FB_OK)
            {
                rt_kprintf("[MOT] runtime guard: ENN feedback %s while request=ENABLE"
                           " (expected LOW) -> 停机\n", safety_fb_verdict_name(fv));
                mot_guard_shutdown(MOT_GUARD_ENN_FEEDBACK);
                fb_stopped = 1u;
            }
        }

        if (armed && !fb_stopped)
        {
            motor_guard_in_t  in;
            motor_guard_out_t out;

            in.armed = 1u;
            in.cfg_state_ok = tmc2209_drive_config_ok_for_arm() ? 1u : 0u;
            in.due          = guard_due;
            in.integrity    = guard_due ? tmc2209_config_integrity_check() : RT_EOK;
            in.busy_streak  = guard_busy_streak;
            motor_guard_decide(&in, &out);
            guard_busy_streak = out.busy_streak;
            if (out.stop) mot_guard_shutdown(out.reason);
        }
        else guard_busy_streak = 0u;

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

/* ---------- Fix A / A11 + F2: 五门门禁矩阵(纯只读 无副作用) ----------
 * 返回"失败门"位掩码; 0 = 五门全通。全部无条件求值, 这样真机可以看到
 * 第 4 门(safety_protection_ready)与第 5 门(驱动参数已下发)确实参与判定,
 * 而不是被前面的门短路掉。 */
/* ---------- 2026-10-21: ENN 状态反馈与许可请求的一致性 ----------
 * 请求态基准 = 最近一次**经回读确认**的许可电平(sg_drv_en_request), 不是 READY/IDLE
 * 状态推断: motor_stop 只停脉冲并保持 armed, 此时驱动请求仍然是"使能"。
 * 从未确认过请求一律按禁能侧判定(fail closed)。 */
static rt_uint8_t mot_fb_request_basis(void)
{
    rt_int32_t req = -1, pad = -1, fb = -1;
    rt_bool_t  fb_ok = RT_FALSE;

    safety_fb_snapshot(&req, &pad, &fb, &fb_ok);
    return (req == PIN_HIGH) ? 1u : 0u;
}

rt_uint32_t motor_get_gate_fail_mask(void)
{
    rt_uint32_t bad = 0;

    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)        bad |= MOT_GATE_ENABLE_PATH;
    if (safety_state_get() != SAFETY_READY)           bad |= MOT_GATE_NOT_READY;
    if (mot.health == SUBSYS_FAILED || !mot_dev_ok)   bad |= MOT_GATE_UNHEALTHY;
    if (!safety_protection_ready())                   bad |= MOT_GATE_NOT_PROTECTED;
    /* Phase 8-F2 第 5 门: TMC2209 驱动参数未下发(或下发失败/已丢失)时禁止使能。
     * 复位默认下 IHOLD_IRUN 没有可用的运转电流档位, 芯片实际电流状态未知;
     * 在"未知电流 + 安全链未验收"的板子上通电属危险动作。
     * 这里只用纯状态判定(本函数被 system_status 等只读路径反复调用, 不得产生
     * 总线流量); 总线级复核在 motor_arm() 决策点单独进行。 */
    if (!tmc2209_drive_config_ok_for_arm())     bad |= MOT_GATE_NO_DRV_CONFIG;
    /* 追加第 6 位(不复用旧位): 最终 ENN 反馈必须与当前许可请求一致。
     * 单次采样判定(2 次 rt_pin_read, 无延时、无 UART), 读不到/矛盾都算失败。 */
    if (safety_fb_now(mot_fb_request_basis()) != SAFETY_FB_OK)  bad |= MOT_GATE_FB_MISMATCH;

    return bad;
}

static void mot_print_gate_fail(rt_uint32_t bad)
{
    rt_kprintf("[MOT] arm gate mask=0x%X:", bad);
    if (bad & MOT_GATE_ENABLE_PATH)   rt_kprintf(" enable-path-not-validated");
    if (bad & MOT_GATE_NOT_READY)     rt_kprintf(" safety-not-READY");
    if (bad & MOT_GATE_UNHEALTHY)     rt_kprintf(" subsystem-unhealthy");
    if (bad & MOT_GATE_NOT_PROTECTED) rt_kprintf(" protection-not-ready");
    if (bad & MOT_GATE_NO_DRV_CONFIG) rt_kprintf(" drive-config-not-applied");
    if (bad & MOT_GATE_FB_MISMATCH)   rt_kprintf(" enn-feedback-mismatch");
    rt_kprintf("\n");
    rt_kprintf("[MOT] gate4 detail: irq_gate=%s polarity=%s gpio_ready=%s\n",
               safety_irq_attached() ? "OPEN" : "CLOSED",
               safety_polarity_validated() ? "CONFIRMED" : "not-confirmed",
               safety_gpio_ready() ? "YES" : "NO");
    rt_kprintf("[MOT] gate5 detail: tmc_drive_cfg=%s\n",
               tmc_drv_cfg_state_name(tmc2209_get_config_state()));
    rt_kprintf("[MOT] gate6 detail: request_basis=%s enn_feedback_verdict=%s (PC.6=TMC_ENN)\n",
               mot_fb_request_basis() ? "ENABLED" : "DISABLED",
               safety_fb_verdict_name(safety_fb_now(mot_fb_request_basis())));
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
    motor_snapshot_t snap;

    /* F2b P1-D: "查门 → 完整性复核 → 请求使能"整段必须与 apply 互斥, 否则复核刚
     * 通过、电流档就被并发的配置写改掉(审核报告点名的 TOCTOU)。非阻塞取锁:
     * 取不到就说明有人正在下发配置, 此刻无法保证配置有效 → 拒绝本次 arm。 */
    e = tmc2209_cfg_txn_begin();
    if (e != RT_EOK)
    {
        rt_kprintf("[MOT] arm REFUSED: 驱动配置事务正在进行(-RT_EBUSY)\n");
        return e;
    }

    /* Fix A / A3 + F2: 五门全部无条件求值后统一判定(见 motor_get_gate_fail_mask) */
    bad = motor_get_gate_fail_mask();
    if (bad)
    {
        rt_kprintf("[MOT] arm REFUSED (gates failing)\n");
        mot_print_gate_fail(bad);
        e = -RT_EPERM;
        goto out;
    }

    /* F2a: 门掩码只看软件状态, 不查总线。但"状态说 CONFIGURED"并不等于芯片里
     * 还是那套配置 —— TMC 断电/复位后寄存器会回到默认(电流档与 vsense 全丢),
     * 而 UART 照样能通。arm 是真正通电前的最后一个决策点, 在这里做一次实值
     * 复核; 复核失败会立即把状态打成 FAILED, 于是第 5 门重新闭合。 */
    if (tmc2209_config_integrity_check() != RT_EOK)
    {
        rt_kprintf("[MOT] arm REFUSED: 驱动配置完整性复核未通过"
                   " (芯片可能已断电/复位/重连)\n");
        mot_print_gate_fail(motor_get_gate_fail_mask());
        e = -RT_EPERM;
        goto out;
    }

    /* O1(真机发现): 五门之外还要求电机本体确实停在"可以安全使能"的位置。
     * MOTOR_FAULT 是紧急停机的锁存态, 未走 disarm/恢复就再次 arm 会让 armed
     * 标志叠加在故障态上。取一次一致快照后判定, 不改动上面五门。 */
    if (motor_get_snapshot(&snap) != RT_EOK)
    {
        rt_kprintf("[MOT] arm REFUSED: snapshot failed\n");
        e = -RT_ERROR;
        goto out;
    }
    if (snap.state != MOTOR_IDLE || snap.armed != 0 ||
        snap.current_hz != 0 || snap.target_hz != 0)
    {
        rt_kprintf("[MOT] arm REFUSED: not at rest (state=%d armed=%d hz=%u tgt=%u)"
                   " - must be IDLE/armed=0/hz=0, run motor_disarm first\n",
                   (int)snap.state, (int)snap.armed,
                   snap.current_hz, snap.target_hz);
        e = -RT_EPERM;
        goto out;
    }

    /* 诊断输出仍占用 EPWM ch0 时禁止 arm(所有权未交回) */
    if (step_pwm_output_active())
    {
        rt_kprintf("[MOT] arm REFUSED: pwm_test still owns EPWM1 ch0"
                   " (run 'pwm_test stop')\n");
        e = -RT_EBUSY;
        goto out;
    }

    /* F2b P1-D: 请求使能之前把五门再求值一次。上面的完整性复核要跑 UART, 期间
     * Safety 状态/保护链/本子系统健康都可能已经变了(配置侧被 txn 锁挡住, 安全侧不会)。 */
    bad = motor_get_gate_fail_mask();
    if (bad)
    {
        rt_kprintf("[MOT] arm REFUSED: 使能写之前重新求值门禁未通过\n");
        mot_print_gate_fail(bad);
        e = -RT_EPERM;
        goto out;
    }

    /* 追加(2026-10-21): 发出使能请求之前, 必须先确认当前确实处在"已禁能且反馈一致"一侧。
     * 反馈读不到/矛盾就不请求使能(不猜), 也不改动任何已经执行的关闭动作。 */
    {
        safety_fb_verdict_t fv = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe = safety_drv_feedback_confirm(0u, &fv);

        if (fe != RT_EOK)
        {
            rt_kprintf("[MOT] arm REFUSED: 请求使能前未确认 ENN 反馈在禁能侧"
                       " (err=%d verdict=%s)\n", (int)fe, safety_fb_verdict_name(fv));
            e = -RT_EPERM;
            goto out;
        }
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
        goto out;
    }

    /* 追加(2026-10-21): 焊盘已经到 HIGH, 还必须在**有界**窗口内确认最终 ENN 反馈到了
     * "未施加禁能"侧(LOW)。不一致 = 硬件没有接受这个请求(硬件急停链切断、级联逻辑
     * 故障、反馈线断开都可能, 软件无法区分) => 撤销请求、回滚、投递反馈不一致事件。
     * 绝不把"软件想开"当成"驱动已开"。 */
    {
        safety_fb_verdict_t fv = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe = safety_drv_feedback_confirm(1u, &fv);

        if (fe != RT_EOK)
        {
            rt_kprintf("[MOT] arm FAILED: ENN feedback not on enabled side"
                       " (err=%d verdict=%s) - rollback fail-closed\n",
                       (int)fe, safety_fb_verdict_name(fv));
            mot_lock_take();
            mot.armed = 0;
            mot.current_hz = 0;
            mot.target_hz = 0;
            mot_pwm_output_off();
            mot.state = MOTOR_FAULT;
            mot_lock_release();
            if (mot_drv_en_request(0) != RT_EOK)
                rt_kprintf("[MOT] !! rollback LOW ALSO UNVERIFIED - pad unknown !!\n");
            (void)safety_post_event(EVT_DRV_FEEDBACK);
            e = -RT_EPERM;
            goto out;
        }
    }

    mot_lock_take();
    mot.armed = 1;
    mot_lock_release();
    rt_kprintf("[MOT] ARMED (drv_en request=HIGH readback verified,"
               " ENN feedback=LOW confirmed)\n");
    e = RT_EOK;

out:
    tmc2209_cfg_txn_end();
    return e;
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
    /* 追加(2026-10-21): 如实报告最终 ENN 反馈。没确认到禁能侧时**绝不**打印成"整链已安全"。
     * 关闭动作(停 STEP + 请求 LOW)已经照做, 这一步只是不谎报。 */
    {
        safety_fb_verdict_t fv = safety_fb_now(0u);

        rt_kprintf("[MOT] DISARMED (ENN feedback=%s%s)\n", safety_fb_verdict_name(fv),
                   fv == SAFETY_FB_OK ? "" : " - 最终禁能**未确认**, 不得称整链已安全");
    }
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
    rt_uint32_t bad;
    rt_err_t te;

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

    /* F2b P1-E: arm 与 start 之间驱动配置可能已经失联/被复位打掉(第 5 门重新闭合),
     * 起转前重新求值五门。第 5 门用的是纯状态判定(不产生 UART 流量)。 */
    bad = motor_get_gate_fail_mask();
    if (bad)
    {
        rt_kprintf("[MOT] start REFUSED: 起转前门禁未通过\n");
        mot_print_gate_fail(bad);
        (void)safety_post_event(EVT_SOFT_FAULT);
        return -RT_EPERM;
    }
    if (tmc2209_get_health() == SUBSYS_FAILED)
    {
        rt_kprintf("[MOT] start REFUSED: TMC 链路 FAILED(无法确认驱动配置)\n");
        (void)safety_post_event(EVT_SOFT_FAULT);
        return -RT_EPERM;
    }

    /* F2c R03: 上面两道都还是**软件状态**判定。arm 之后 TMC 模块断电/复位再恢复
     * UART 时, 软件 CONFIGURED 可以仍然是 TRUE(只有实值回读才能照出来), 于是
     * motor_start 会带着芯片里的默认电流档进入 RUN 并输出 STEP, 一直等到下一次
     * armed 周期守卫才发现。起转前必须在配置事务内做一次芯片实值复核。
     * ⚠ 这缩小了窗口但消不掉它(微秒级复位竞争仍然在), 最终切断必须由独立的
     *   ENN/急停硬件承担 —— 见本文件头的 R05 记录。 */
    te = tmc2209_cfg_txn_begin();
    if (te != RT_EOK)
    {
        rt_kprintf("[MOT] start REFUSED: 配置事务不可用/被占用(%d),"
                   " 此刻无法复核芯片实值\n", (int)te);
        return te;
    }
    te = tmc2209_config_integrity_check();          /* 同线程重入, 不会自锁 */
    tmc2209_cfg_txn_end();
    if (te != RT_EOK)
    {
        rt_kprintf("[MOT] start REFUSED: 芯片配置实值复核未通过(%d)"
                   " - 可能已断电/复位/重连, 需重新 apply + arm\n", (int)te);
        mot_print_gate_fail(motor_get_gate_fail_mask());
        (void)safety_post_event(EVT_SOFT_FAULT);
        return -RT_EPERM;
    }

    /* 追加(2026-10-21): 起转前按**使能侧请求**重新确认最终 ENN 反馈。
     * 这是 arm 之后的第二阶段判定(不能沿用禁能侧期望, 否则正常的 ENN LOW 会让
     * start 永远失败): armed 且许可焊盘已确认 HIGH 时, 期望反馈 = LOW。
     * 不一致(硬件急停切断/级联故障/反馈断开) => 不起转 + 投递反馈不一致事件。 */
    {
        safety_fb_verdict_t fv = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe = safety_drv_feedback_confirm(1u, &fv);

        if (fe != RT_EOK)
        {
            rt_kprintf("[MOT] start REFUSED: ENN feedback 不在使能侧(expected LOW)"
                       " (err=%d verdict=%s) - 不起转\n", (int)fe,
                       safety_fb_verdict_name(fv));
            (void)safety_post_event(EVT_DRV_FEEDBACK);
            return -RT_EPERM;
        }
    }

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
    /* 追加(2026-10-21): 反馈状态如实报告。停 STEP 与撤销许可**总是**先做,
     * 反馈读不到只影响"最终禁能是否已确认"这句话, 不影响动作本身。 */
    {
        safety_fb_verdict_t fv = safety_fb_now(0u);

        rt_kprintf("[MOT] EMERGENCY STOP (drv_en LOW %s, ENN feedback %s%s)\n",
                   e == RT_EOK ? "verified" : "UNVERIFIED",
                   safety_fb_verdict_name(fv),
                   fv == SAFETY_FB_OK ? "" : " -> final disable NOT confirmed");
    }
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

/* ---------- Fix A / A9 + F2b P0-A: EPWM1 ch0 诊断借用门 ----------
 * Motor Service 是该通道唯一生产 owner。F2b 起第一道条件是 **STEP 输出许可**:
 * 审核报告 P0-A 指出的漏洞正是"只查 armed/静止/焊盘 LOW"。在现板 U5 与门损坏 +
 * 急停非门损坏的情况下, MCU 焊盘 LOW 只能证明 MCU 侧的请求电平, 证明不了 TMC_ENN
 * 已禁能(实测 TMC_ENN 约 0V = 驱动侧仍是"使能"语义), 所以电机一旦接上, pwm_test
 * 输出的 STEP 会真实驱动电机 —— 即使 motor_arm() 被五门挡着也一样。
 * 因此在硬件链验收完成之前, 除非操作员在隔离环境(TMC 模块拔出 / 功率级断开)下
 * 人工确认并置 STEP_DIAG_ISOLATED_ENV_CONFIRMED, 否则任何 STEP 输出一律拒绝。 */
rt_err_t motor_pwm_grant_to_diag(void)
{
    motor_snapshot_t snap;

    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED && !STEP_DIAG_ISOLATED_ENV_CONFIRMED)
    {
        rt_kprintf("[MOT] STEP diag REFUSED: 使能链未验收且未确认隔离环境"
                   " (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=FALSE,"
                   " STEP_DIAG_ISOLATED_ENV_CONFIRMED=FALSE)\n");
        rt_kprintf("[MOT]   现板与门/非门损坏 -> DRV_ENABLE 焊盘 LOW 证明不了 TMC_ENN"
                   " 已禁能, 输出 STEP 等同于可能真实驱动电机。\n");
        rt_kprintf("[MOT]   要量 STEP 频率: 先拔出 TMC 模块/断开功率级, 再由操作员"
                   "翻转 STEP_DIAG_ISOLATED_ENV_CONFIRMED 并留证。\n");
        return -RT_EPERM;
    }

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
    /* 追加(2026-10-21): 借用 STEP 输出之前, 还要看**最终** ENN 反馈是否真在禁能侧。
     * 宏翻转仍然只放开"许可", 这一条不因此放松: 反馈不一致就拒绝出脉冲。 */
    {
        safety_fb_verdict_t fv = safety_fb_now(0u);

        if (fv != SAFETY_FB_OK)
        {
            rt_kprintf("[MOT] grant REFUSED: ENN feedback not on disabled side (verdict=%s)\n",
                       safety_fb_verdict_name(fv));
            return -RT_EPERM;
        }
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

/* ---------- F2b P1-E: 运行期守卫判定的确定性真值表(纯软件, 不碰硬件) ---------- */
static void motor_drv_guard_selftest(void)
{
    struct
    {
        const char        *tag;
        motor_guard_in_t   in;
        rt_uint8_t         stop;
        rt_uint8_t         reason;
        rt_uint8_t         streak;
    } t[] = {
        /* tag, armed, due, cfg_ok, integrity, streak_in, 期望 stop/reason/streak */
        { "G1.not-armed(reset streak)", {0, 1, 1, RT_EOK, 2},   0, MOT_GUARD_NONE,      0 },
        { "G2.armed-not-due",           {1, 0, 1, RT_EOK, 0},   0, MOT_GUARD_NONE,      0 },
        { "G3.due-ok",                  {1, 1, 1, RT_EOK, 2},   0, MOT_GUARD_NONE,      0 },
        { "G4.cfg-lost",                {1, 0, 0, RT_EOK, 0},   1, MOT_GUARD_CFG_LOST,  0 },
        { "G5.integrity-io",            {1, 1, 1, -RT_EIO, 0},  1, MOT_GUARD_INTEGRITY, 0 },
        { "G6.integrity-eperm",         {1, 1, 1, -RT_EPERM, 0},1, MOT_GUARD_INTEGRITY, 0 },
        { "G7.busy-1st",                {1, 1, 1, -RT_EBUSY, 0},0, MOT_GUARD_NONE,      1 },
        { "G8.busy-2nd",                {1, 1, 1, -RT_EBUSY, 1},0, MOT_GUARD_NONE,      2 },
        { "G9.busy-limit",              {1, 1, 1, -RT_EBUSY, 2},1, MOT_GUARD_TXN_BUSY,  3 },
        { "G10.lost-beats-busy",        {1, 1, 0, -RT_EBUSY, 2},1, MOT_GUARD_CFG_LOST,  2 },
    };
    int i, pass = 1;
    rt_bool_t cfg_ok;

    for (i = 0; i < (int)(sizeof(t) / sizeof(t[0])); ++i)
    {
        motor_guard_in_t  in = t[i].in;
        motor_guard_out_t out;

        /* G10: cfg_state_ok=0 时, 无论复核返回什么(这里故意给 -RT_EBUSY 且已接近
         * busy 上限), 结论都必须是"配置已丢失"这条优先级最高的停机。 */
        motor_guard_decide(&in, &out);
        rt_kprintf("[MOT-GUARD] %-26s stop=%u reason=%u streak=%u (期望 %u/%u/%u)\n",
                   t[i].tag, (unsigned)out.stop, (unsigned)out.reason,
                   (unsigned)out.busy_streak, (unsigned)t[i].stop,
                   (unsigned)t[i].reason, (unsigned)t[i].streak);
        if (out.stop != t[i].stop || out.reason != t[i].reason ||
            out.busy_streak != t[i].streak) pass = 0;
    }

    /* F2c R04: 本自测**只**做纯判定。判定的两个输入接口在这里做"无副作用自洽"
     * 检查(两者都不碰总线): 第 5 门放行必须同时存在有效快照。
     * ⚠ 绝不调用 tmc2209_config_integrity_check() —— 它会真读 GCONF/CHOPCONF,
     *   并且失败会把生产配置状态打成 FAILED(复审 R04 指出的污染)。生产链路的实
     *   值巡检已拆到独立命令 motor_drv_guard_probe。 */
    cfg_ok = tmc2209_drive_config_ok_for_arm();
    rt_kprintf("[MOT-GUARD] 纯判定自测: drive_config_ok=%s, gate mask=0x%X,"
               " write-gate mask=0x%X (本命令不产生任何 UART 流量)\n",
               cfg_ok ? "YES" : "NO", (unsigned)motor_get_gate_fail_mask(),
               (unsigned)tmc2209_get_write_gate_mask());
    if (cfg_ok && !tmc2209_get_applied_config(RT_NULL, RT_NULL, RT_NULL))
    { rt_kprintf("[MOT-GUARD] arm 放行却无有效快照(判定不自洽)\n"); pass = 0; }

    rt_kprintf("[MOT-GUARD] %s (G1..G10 纯判定, 未碰硬件、未改生产状态)\n",
               pass ? "ALL PASS" : "FAILED");
}
MSH_CMD_EXPORT(motor_drv_guard_selftest, offline truth table for the runtime drive-config guard);

/* ---------- F2c R04: 生产链路实值巡检(与纯真值表自测分开) ----------
 * ⚠ 本命令会真实读 GCONF/CHOPCONF(产生 UART 流量); 若复核不过, 生产配置状态会被
 *   打成 FAILED, 第 5 门随之闭合 —— 这是**期望行为**(失联就该被照出来), 但它是
 *   有副作用的动作, 所以不放进"纯软件"自测里。 */
static void motor_drv_guard_probe(void)
{
    rt_err_t e;

    rt_kprintf("[MOT-PROBE] 生产驱动配置巡检: 会真实读芯片寄存器, 失败将使配置降级为 FAILED\n");
    rt_kprintf("[MOT-PROBE] state=%s ok_for_arm=%s health=%s gate mask=0x%X"
               " write-gate mask=0x%X\n",
               tmc_drv_cfg_state_name(tmc2209_get_config_state()),
               tmc2209_drive_config_ok_for_arm() ? "YES" : "NO",
               subsys_health_name(tmc2209_get_health()),
               (unsigned)motor_get_gate_fail_mask(),
               (unsigned)tmc2209_get_write_gate_mask());
    e = tmc2209_config_integrity_check();
    rt_kprintf("[MOT-PROBE] integrity=%d (0=OK, -EPERM=未CONFIGURED/快照作废,"
               " -EIO=实值不符或读不到, -EBUSY=事务占用, -ENOSYS=锁不可用)\n", (int)e);
    if (e != RT_EOK)
        rt_kprintf("[MOT-PROBE] 复核后 state=%s, gate mask=0x%X\n",
                   tmc_drv_cfg_state_name(tmc2209_get_config_state()),
                   (unsigned)motor_get_gate_fail_mask());
    rt_kprintf("[MOT-PROBE] 守卫计数: stops=%u last_reason=%u\n",
               (unsigned)mot.guard_stops, (unsigned)mot.guard_reason);
}
MSH_CMD_EXPORT(motor_drv_guard_probe, inspect live TMC drive config (reads the chip));

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
    rt_kprintf("[MOT] drv-guard: stops=%u last_reason=%u "
               "(0=none 1=cfg-lost 2=integrity 3=txn-busy-limit 4=enn-feedback)\n",
               (unsigned)snap.guard_stops, (unsigned)snap.guard_reason);
    bad = motor_get_gate_fail_mask();
    rt_kprintf("[MOT] arm gate fail mask=0x%X (%s)\n", bad,
               bad ? "arm will be REFUSED" : "all gate conditions pass");
    if (bad) mot_print_gate_fail(bad);
    /* 追加(2026-10-21): 请求 / 焊盘 / 最终反馈三者分列, 不混为一谈 */
    {
        rt_int32_t req = -1, pad = -1, fb = -1;
        rt_bool_t  fb_ok = RT_FALSE;
        rt_uint8_t want  = mot_fb_request_basis();

        safety_fb_snapshot(&req, &pad, &fb, &fb_ok);
        rt_kprintf("[MOT] drive request=%s pad=%d expected_ENN=%s ENN_feedback=%d(%s)"
                   " verdict=%s\n",
                   req < 0 ? "NEVER-VERIFIED" : (req == PIN_HIGH ? "HIGH" : "LOW"),
                   (int)pad, want ? "LOW" : "HIGH", (int)fb,
                   fb_ok ? "resolved" : "UNRESOLVED/no-sample",
                   safety_fb_verdict_name(safety_fb_now(want)));
        rt_kprintf("[MOT] limit inputs=%s (SAFETY_LIMIT_INPUTS_ENABLED;"
                   " OFF => DISABLED_BY_CONFIG, 不解析/不读/不注册)\n",
                   safety_limit_inputs_enabled() ? "ENABLED (HIGH=safe, LOW=trigger)"
                                                 : "DISABLED_BY_CONFIG");
    }
    rt_kprintf("[MOT] step-diag permission=%s (enable-path / isolated-env 两个宏"
               "至少一个为 TRUE 才允许输出 STEP)\n",
               (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ||
                STEP_DIAG_ISOLATED_ENV_CONFIRMED) ? "GRANTED" : "DENIED");
    rt_kprintf("[MOT] tmc runtime write gate mask=0x%X\n",
               (unsigned)tmc2209_get_write_gate_mask());
}
MSH_CMD_EXPORT_ALIAS(cmd_motor_status, motor_status, show motor state and arm gate mask);

static void cmd_motor_arm(void)      { (void)motor_arm(); }
MSH_CMD_EXPORT_ALIAS(cmd_motor_arm, motor_arm, request arm through the five gates);

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
