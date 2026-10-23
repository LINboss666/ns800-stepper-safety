/*
 * safety_state.c - 安全状态机 (Phase 7-B 正式实现)
 *
 * 转换白名单(见 safety_state.h):
 *   BOOT→INIT→SELF_TEST→(READY | FAULT_LATCHED)
 *   READY↔RUN; RUN→LOAD_WARNING→(RUN | ABNORMAL); ABNORMAL→FAULT_LATCHED
 *   RUN/LOAD_WARNING/ABNORMAL/READY → FAULT_LATCHED / ESTOP(急停类事件)
 *   FAULT_LATCHED/ESTOP → MANUAL_CLEAR(仅 fault_reset: 实测源安全+人工命令)
 *   MANUAL_CLEAR → READY(内部重跑自检)
 *
 * ss_state 为私有; 业务代码一律走 safety_transition()。
 *
 * Fix A 启动编排: safety_state_boot() 只建事件系统/互斥并推进 BOOT→INIT→
 * SELF_TEST; 启动自检与最终 READY 决策由 supervisor_boot stage 11 调
 * safety_startup_selftest() 完成 —— Safety 线程必须先于慢速 Flash 扫描存在。
 *
 * Fix A 唯一停机路径: 硬件事件(限位/DIAG, 按本配置的活动集合)与软件故障(MULTI/SOFT)都只调
 * safety_force_shutdown(); 停 STEP 与 DRV_ENABLE "写+回读"由 motor.c 的
 * motor_emergency_stop() 负责, 本模块只做独立焊盘复核(不重复写) + 单次 ENN 反馈采样报告。
 * ⚠ 反馈读不到**不会**跳过关闭动作, 只会让"整链已安全/最终禁能已确认"这类话说不出口,
 *   并且不允许清除故障(见 fault_reset)。
 * ⚠ 2026-10-21: PC.6 是 TMC_ENN 反馈输入, 不是急停源; 板上急停不经 MCU, 软件没有独立
 *   急停输入。EVT_ESTOP/FAULT_ESTOP/SAFETY_ESTOP 作为历史编号保留(黑匣子兼容)。
 *
 * 关断结果可信(2026-10-23, phase8-stop-result-s1 / S1):
 *   - safety_force_shutdown() 现在**收集两条输出路径**的关断结果(Motor 生产路径 +
 *     诊断借用路径), 谁失败都不跳过另一条关闭动作, 也**不再**打印"STEP stopped";
 *     故障码/格式不变(软件关断失败沿用投递方给的 code, 通常是 FAULT_SOFT), 详细 rc
 *     保存在快照 stop_rc 与本日志里。黑匣子仍只触发一次, 不重复投事件 => 没有
 *     "消费事件→停机失败→再投同事件"的自激。
 *   - fault_reset() 增加一道**只收紧**的复核: 两条输出路径本次都必须拿到"驱动 API
 *     接受关断"的有效结果(诊断侧从未占用该通道算有效, 不虚构成功), 加上原有的
 *     活动输入实测 + 许可焊盘 LOW + ENN 前后两次有界确认 + disarm 成功, 全部成立才
 *     进 MANUAL_CLEAR。S1 的关断失败锁存**只在这里**解除(motor_clear_stop_latch /
 *     step_pwm_clear_stop_latch), 没有 MSH 旁路; 一次后续成功的普通 stop 不清锁存、
 *     不解 Safety 故障、不自动使能。
 *   - 本轮不处理锁等待/UART 阻塞/在途 arm-enable 竞争(S2), 不宣称停机时延上界。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "safety_state.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "adxl345.h"
#include "current_adc.h"
#include "tmc2209.h"
#include "ns_flash.h"
#include "motor.h"
#include "step_pwm.h"
#include "blackbox.h"

/* ---------- 状态机私有状态 ---------- */
static volatile safety_state_t ss_state = SAFETY_BOOT;
static volatile rt_uint32_t    ss_fault_code = FAULT_NONE;
static struct rt_event         ss_event;
static rt_bool_t               ss_inited = RT_FALSE;
static rt_uint8_t              ss_degraded_mask = 0;   /* 自检降级位图 */

#define SS_DEG_IMU    (1u << 0)
#define SS_DEG_ADC    (1u << 1)
#define SS_DEG_FLASH  (1u << 2)

const char *safety_state_name(safety_state_t s)
{
    switch (s)
    {
    case SAFETY_BOOT:         return "BOOT";
    case SAFETY_INIT:         return "INIT";
    case SAFETY_SELF_TEST:    return "SELF_TEST";
    case SAFETY_READY:        return "READY";
    case SAFETY_RUN:          return "RUN";
    case SAFETY_LOAD_WARNING: return "LOAD_WARNING";
    case SAFETY_ABNORMAL:     return "ABNORMAL";
    case SAFETY_FAULT_LATCHED:return "FAULT_LATCHED";
    case SAFETY_ESTOP:        return "ESTOP";
    case SAFETY_MANUAL_CLEAR: return "MANUAL_CLEAR";
    default:                  return "?";
    }
}

/* P0-1: 状态修改串行化 —— transition/force_shutdown/fault_reset 全部
 * 经 ss_lock; FAULT_LATCHED/ESTOP 建立后普通业务 transition 无法覆盖
 * (白名单 + 锁内重读双重防护)。ISR 不持锁(仍只 post event)。 */
static struct rt_mutex         ss_lock;
static rt_bool_t               ss_lock_ok = RT_FALSE;

/* 转换白名单 */
static rt_bool_t transition_allowed(safety_state_t from, safety_state_t to)
{
    switch (from)
    {
    case SAFETY_BOOT:      return (to == SAFETY_INIT);
    case SAFETY_INIT:      return (to == SAFETY_SELF_TEST);
    case SAFETY_SELF_TEST: return (to == SAFETY_READY || to == SAFETY_FAULT_LATCHED);
    case SAFETY_READY:     return (to == SAFETY_RUN || to == SAFETY_SELF_TEST ||
                                   to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_RUN:       return (to == SAFETY_LOAD_WARNING || to == SAFETY_READY ||
                                   to == SAFETY_ABNORMAL ||
                                   to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_LOAD_WARNING:
                           return (to == SAFETY_RUN || to == SAFETY_ABNORMAL ||
                                   to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_ABNORMAL:  return (to == SAFETY_FAULT_LATCHED || to == SAFETY_ESTOP);
    case SAFETY_FAULT_LATCHED:
                           return (to == SAFETY_MANUAL_CLEAR || to == SAFETY_ESTOP);
    case SAFETY_ESTOP:     return (to == SAFETY_MANUAL_CLEAR);
    case SAFETY_MANUAL_CLEAR:
                           return (to == SAFETY_READY);
    default:               return RT_FALSE;
    }
}

rt_err_t safety_transition(safety_state_t next)
{
    safety_state_t from;

    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    from = ss_state;                       /* P0-1: 锁内重读, 消除检查/写竞争 */

    if (from == next)
    {
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return RT_EOK;
    }

    if (!transition_allowed(from, next))
    {
        rt_kprintf("[SS] transition REFUSED: %s -> %s (not allowed)\n",
                   safety_state_name(from), safety_state_name(next));
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return -RT_EPERM;
    }

    ss_state = next;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] %s -> %s\n", safety_state_name(from), safety_state_name(next));
    return RT_EOK;
}

/* 统一安全停机(唯一旁路): 顺序不可变 */
void safety_force_shutdown(rt_uint32_t code)
{
    rt_err_t estop_e;
    rt_err_t diag_e;
    rt_bool_t pad_low;
    safety_fb_verdict_t fb_v;
    mot_stop_result_t m_res;
    mot_stop_result_t d_res;
    rt_uint8_t        m_src;
    rt_err_t          m_rc;
    rt_err_t          d_rc;
    rt_bool_t         m_lat;
    rt_bool_t         d_lat;
    rt_bool_t         step_off_ok;

    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    ss_fault_code = code;

    /* ① 停 STEP + DRV_ENABLE 请求 LOW(写+回读, 由 Motor Service 统一持有;
     *    幂等)。S1: 返回值 = **PWM 关断失败优先**, 否则使能脚写入回读结果;
     *    两个原始 rc 各自留档, 不得把非零一律解释成"只是 GPIO 失败"。
     *    诊断侧走同一条 checked 关断路径, 失败则**不**释放诊断所有权。
     * ⚠ 反馈读不到/任一路失败**绝不跳过**另一条关闭动作(合同 4.3)。 */
    estop_e = motor_emergency_stop();
    diag_e  = step_pwm_force_stop();

    /* ② 独立复核焊盘电平(不复写): 不知道=不安全 */
    pad_low = safety_drv_enable_is_low();
    if (!pad_low)
        rt_kprintf("[SS] !! CRITICAL: DRV_ENABLE pad NOT confirmed LOW (estop_rc=%d)"
                   " - hardware ENN chain is the only guard !!\n", (int)estop_e);

    /* ②b 追加(2026-10-21): 单次采样看最终 ENN 反馈是否真到了禁能侧(高)。
     *    这一条只影响"能不能声称整链已安全", 不影响①②的动作与④的锁存。 */
    fb_v = safety_fb_now(0u);
    if (fb_v != SAFETY_FB_OK)
        rt_kprintf("[SS] !! ENN feedback NOT on disabled side (verdict=%s)"
                   " - 不打印'最终禁能已确认'; 关闭动作已照做, 故障仍锁存\n",
                   safety_fb_verdict_name(fb_v));

    /* ②c 追加(2026-10-23 S1): STEP 关断结果分列核对。
     *    两条输出路径(Motor 生产路径 / 诊断借用路径)各自如实报告; 只要有一条没有
     *    有效关断结果, 就**绝不**打印"STEP stopped"这类话。故障码与格式不变(沿用
     *    投递方给的 code), 详细 rc 由快照 stop_rc 与本日志保存。 */
    motor_get_stop_state(&m_res, &m_src, &m_rc, &m_lat);
    step_pwm_get_stop_state(&d_res, &d_rc, &d_lat);
    step_off_ok = (mot_stop_result_is_off(m_res) && step_pwm_off_confirmed())
                      ? RT_TRUE : RT_FALSE;
    if (step_off_ok)
        rt_kprintf("[SS] STEP: 两条输出路径都没有未确认的关断"
                   " (motor=%s src=%s / diag=%s) - 仍不等于示波器证明无脉冲\n",
                   mot_stop_result_name(m_res), mot_stop_src_name(m_src),
                   d_res == MOT_STOP_NOT_ATTEMPTED ? "诊断从未占用该通道"
                                                   : "驱动已接受关断");
    else
        rt_kprintf("[SS] !! STEP 关断**未确认**: motor=%s(src=%s rc=%d latched=%s)"
                   " diag=%s(rc=%d latched=%s) -> 不谎称输出已停,"
                   " 后续 arm/start/诊断借用/故障清除全部拒绝, 直到人工恢复复核\n",
                   mot_stop_result_name(m_res), mot_stop_src_name(m_src), (int)m_rc,
                   m_lat ? "YES" : "no", mot_stop_result_name(d_res), (int)d_rc,
                   d_lat ? "YES" : "no");

    /* ③ 黑匣子触发: O(1) 置标志, Safety 线程绝不等待 Flash。
     *    无论上面的结果如何都只此一次(不重复触发, 也不因失败而跳过)。 */
    blackbox_trigger(code);

    /* ④ 锁存(旁路转换: 安全动作不受白名单限制)。
     * 即使 ② 复核失败也必须锁存 —— 回读失败绝不等于可以继续运动。 */
    ss_state = SAFETY_FAULT_LATCHED;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] ==> FAULT_LATCHED code=%u (DRV_ENABLE LOW %s, ENN feedback %s,"
               " STEP %s, motor_rc=%d diag_rc=%d)\n", code,
               pad_low ? "verified" : "UNVERIFIED!!", safety_fb_verdict_name(fb_v),
               step_off_ok ? "off-confirmed(api)" : "NOT-confirmed(unknown)",
               (int)estop_e, (int)diag_e);
}

void safety_enter_fault(rt_uint32_t code) { safety_force_shutdown(code); }

rt_err_t safety_post_event(rt_uint32_t event)
{
    if (!ss_inited) return -RT_ERROR;
    return rt_event_send(&ss_event, event);
}

safety_state_t safety_state_get(void)
{
    safety_state_t s;
    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    s = ss_state;
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    return s;
}

struct rt_event *safety_event_handle(void) { return &ss_event; }
rt_bool_t safety_events_ready(void) { return ss_inited; }
rt_uint32_t    safety_fault_get(void) { return ss_fault_code; }

/* ---------- 启动/重跑自检 ---------- */

/* required 全过 = RT_EOK; degraded 结果写入 ss_degraded_mask */
rt_err_t safety_run_selftest(void)
{
    rt_err_t required_fail = RT_EOK;
    rt_uint32_t v = 0;

    ss_degraded_mask = 0;
    rt_kprintf("[SS] self-test start\n");

    /* ---- required ① GPIO 安全态 ---- */
    if (!safety_gpio_ready())
    {
        rt_kprintf("[SS] [REQ FAIL] GPIO safety init\n");
        required_fail = -RT_ERROR;
    }
    else rt_kprintf("[SS] [REQ OK] GPIO\n");

    /* ---- required ② PWM 设备 ---- */
    if (motor_init() != RT_EOK)
    {
        rt_kprintf("[SS] [REQ FAIL] PWM/EPWM1 device\n");
        required_fail = -RT_ERROR;
    }
    else rt_kprintf("[SS] [REQ OK] PWM device\n");

    /* ---- required ③ DRV_ENABLE 必须"写 LOW + 回读确认"(Fix A) ----
     * 只读不够: 写不进/被顶高的焊盘读起来可能是 LOW 但不可控。回读只证明
     * MCU 焊盘电平, 不替代 ENN 整链硬件验收。 */
    {
        rt_err_t de = safety_drv_enable_write(0);
        if (de != RT_EOK)
        {
            rt_kprintf("[SS] [REQ FAIL] DRV_ENABLE LOW not verified (err=%d)\n",
                       (int)de);
            required_fail = -RT_ERROR;
        }
        else rt_kprintf("[SS] [REQ OK] DRV_ENABLE=LOW (write+readback verified)\n");
    }

    /* ---- required ③b ENN 反馈必须在禁能侧(2026-10-21 输入职责更正) ----
     * PC.6 读的是板上 TMC_ENN(H6-5)。自检时驱动请求应当是禁能, 所以期望反馈=HIGH。
     * 反馈读不到/矛盾 → required FAIL(不猜、不用"焊盘 LOW"代替), 但不跳过上面的关闭动作。 */
    {
        safety_fb_verdict_t fv = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe = safety_drv_feedback_confirm(0u, &fv);

        if (fe != RT_EOK)
        {
            rt_kprintf("[SS] [REQ FAIL] ENN feedback not confirmed on disabled side"
                       " (err=%d verdict=%s)\n", (int)fe, safety_fb_verdict_name(fv));
            required_fail = -RT_ERROR;
        }
        else rt_kprintf("[SS] [REQ OK] ENN feedback = disabled side (PC.6=H6-5/TMC_ENN)\n");
    }

    /* ---- required ④ TMC2209 链路(初始化 + IOIN 读) ---- */
    if (tmc2209_init() != RT_EOK ||
        tmc2209_read_register(TMC_ADDR_DEFAULT, TMC_REG_IOIN, &v) != RT_EOK)
    {
        rt_kprintf("[SS] [REQ FAIL] TMC2209 link\n");
        required_fail = -RT_ERROR;
    }
    else rt_kprintf("[SS] [REQ OK] TMC2209 (IOIN=0x%08X)\n", v);

    /* ---- degraded: IMU ---- */
    if (adxl345_init() != RT_EOK)
    {
        ss_degraded_mask |= SS_DEG_IMU;
        rt_kprintf("[SS] [DEGRADED] IMU missing/fault\n");
    }
    else rt_kprintf("[SS] [OK] IMU\n");

    /* ---- degraded: ADC ---- */
    if (current_adc_init() != RT_EOK)
    {
        ss_degraded_mask |= SS_DEG_ADC;
        rt_kprintf("[SS] [DEGRADED] ADC missing/fault\n");
    }
    else rt_kprintf("[SS] [OK] ADC\n");

    /* ---- degraded: Flash/存储(JEDEC 只读探测) ---- */
    {
        rt_uint8_t id[3] = {0}, sr[3] = {0};
        if (ns_flash_init() != RT_EOK || ns_flash_identify(id, sr) != RT_EOK ||
            id[0] != 0xEF)
        {
            ss_degraded_mask |= SS_DEG_FLASH;
            rt_kprintf("[SS] [DEGRADED] Flash/storage\n");
        }
        else rt_kprintf("[SS] [OK] Flash (JEDEC %02X %02X %02X)\n",
                        id[0], id[1], id[2]);
    }

    rt_kprintf("[SS] self-test done: required=%s degraded=0x%02X\n",
               required_fail == RT_EOK ? "PASS" : "FAIL", ss_degraded_mask);
    return required_fail;
}

/* ---------- MSH 命令 ---------- */

/* 显式故障清除: 仅 FAULT_LATCHED/ESTOP 且实测故障源安全 → MANUAL_CLEAR。
 * 之后必须 MANUAL_CLEAR→READY(重跑自检)→ 重新 arm。
 *
 * 2026-10-21 适配:
 *   - 实测源 = **本配置的活动保护输入**(限位宏关闭时只剩 DIAG; 限位启用时是反相后的
 *     LIMIT_*_OK, HIGH=安全)。
 *   - PC.6 不作为急停源出现在这里 —— 它是 TMC_ENN 反馈; 清除前必须**另外**确认
 *     最终反馈确实在禁能侧(HIGH)。确认不到就绝不打印"最终禁能已确认", 也不清除。
 *   - IO70-NEW-01: 清除路径最后一次改变许可的动作是 disarm, 因此禁能侧反馈确认
 *     在 disarm **之前和之后各做一次**, 以后一次为准(坏样本必须参与清除决策)。 */
static void fault_reset(void)
{
    struct { const char *name; const char *pin; rt_int32_t safe; } src[] = {
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
        { "LIMIT_MIN_OK", PIN_NAME_LIMIT_MIN, PIN_HIGH },
        { "LIMIT_MAX_OK", PIN_NAME_LIMIT_MAX, PIN_HIGH },
#endif
        { "TMC_DIAG",     PIN_NAME_TMC_DIAG,  PIN_LOW  },
    };
    int i;
    rt_err_t e;
    rt_bool_t active = RT_FALSE;
    safety_state_t st;
    mot_stop_result_t m_res;
    mot_stop_result_t d_res;
    rt_uint8_t        m_src;
    rt_err_t          m_rc;
    rt_err_t          d_rc;
    rt_bool_t         m_lat;
    rt_bool_t         d_lat;
    rt_bool_t         diag_off_valid;

    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    st = ss_state;
    if (st != SAFETY_FAULT_LATCHED && st != SAFETY_ESTOP)
    {
        rt_kprintf("[SS] fault_reset: not latched (state=%s)\n",
                   safety_state_name(st));
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }

    rt_kprintf("[SS] fault_reset: active protection inputs = %d"
               " (SAFETY_LIMIT_INPUTS_ENABLED=%s)\n", (int)(sizeof(src)/sizeof(src[0])),
               safety_limit_inputs_enabled() ? "RT_TRUE" : "RT_FALSE");

    /* BUG-011-2: 实测故障源, 不看状态位猜 */
    for (i = 0; i < (int)(sizeof(src) / sizeof(src[0])); ++i)
    {
        rt_base_t pin = safety_pin(src[i].pin);
        int level;

        if (pin < 0)
        {
            rt_kprintf("[SS] %s pin unresolved - treat as ACTIVE\n", src[i].name);
            active = RT_TRUE;
            continue;
        }
        level = rt_pin_read(pin);
        rt_kprintf("[SS] %s level=%d (safe=%d) %s\n",
                   src[i].name, level, (int)src[i].safe,
                   level == (int)src[i].safe ? "safe" : "!!ACTIVE!!");
        if (level != (int)src[i].safe) active = RT_TRUE;
    }
    /* Fix A: 安全关键输出无法确认 LOW 时一律拒绝清除 —— 清除之后就是重新
     * 自检→READY→arm 的路径, 使能脚不可控就不允许走通。 */
    if (!safety_drv_enable_is_low())
    {
        rt_kprintf("[SS] fault_reset REFUSED: DRV_ENABLE pad not confirmed LOW\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    /* 追加: 最终 ENN 反馈必须确认在禁能侧, 否则"整链已安全"这句话不成立。 */
    {
        safety_fb_verdict_t fv = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe = safety_drv_feedback_confirm(0u, &fv);

        if (fe != RT_EOK)
        {
            rt_kprintf("[SS] fault_reset REFUSED: ENN feedback not confirmed on the"
                       " disabled side (verdict=%s err=%d) - 不打印'最终禁能已确认',"
                       " 保持锁存\n", safety_fb_verdict_name(fv), (int)fe);
            if (ss_lock_ok) rt_mutex_release(&ss_lock);
            return;
        }
        rt_kprintf("[SS] ENN feedback confirmed DISABLED side (verdict=OK)\n");
    }
    if (active)
    {
        rt_kprintf("[SS] fault_reset REFUSED: source still active\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }

    /* O1: 接受故障清除的同时, 必须把 Motor Service 一起带回 IDLE。
     * 否则会出现 Safety=READY / Motor=MOTOR_FAULT 的不一致状态(真机
     * runtime_selftest 复现过)。走正式 disarm: 清 armed/target_hz/current_hz、
     * 停 STEP、写 DRV_ENABLE LOW 并回读。回读不确认就不进 MANUAL_CLEAR,
     * 也就同时关掉了 "重跑自检 -> READY -> 重新 arm" 这条路。
     * 锁序 ss_lock -> mot_lock 与 safety_force_shutdown() 现有路径一致。 */
    e = motor_disarm();
    if (e != RT_EOK)
    {
        rt_kprintf("[SS] fault_reset REFUSED: motor recovery disarm failed (%d)"
                   " - PWM 关断或许可撤销至少一步未确认(失败优先返回 PWM 错误),"
                   " 保持锁存\n", (int)e);
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    /* IO70-NEW-01 (2026-10-21 复审): disarm 是本路径上**最后一次可能改变许可**的动作,
     * 它自己又会写 LOW 并读一次反馈。上面第 355 行的预检查用的更早的样本, 不能代表
     * disarm 之后的真实状态 —— 所以这里必须在 disarm 之后再取一次有界确认, 并让它
     * 参与清除决策。反馈不匹配/样本不可得/仍在过渡 => 保持锁存与非零故障码。
     * ⚠ 停 STEP 与撤销许可已经由 motor_disarm() 照做, 这里**不**回退那些动作;
     *   确认只做 `rt_pin_read` + 有界采样, 不递归调用停机或状态机。
     * 锁序仍是 ss_lock -> (disarm 内部短暂取放 mot_lock), 与 safety_force_shutdown 一致;
     * 有界窗口 = DRV_FB_TRIES 次 × DRV_FB_SETTLE_MS, 不是时延上界声明。 */
    {
        safety_fb_verdict_t fv2 = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe2 = safety_drv_feedback_confirm(0u, &fv2);

        if (fe2 != RT_EOK)
        {
            rt_kprintf("[SS] fault_reset REFUSED: ENN feedback not re-confirmed AFTER"
                       " disarm (verdict=%s err=%d) - 保持锁存, 不清故障码,"
                       " 不打印'最终禁能已确认'\n",
                       safety_fb_verdict_name(fv2), (int)fe2);
            if (ss_lock_ok) rt_mutex_release(&ss_lock);
            return;
        }
        rt_kprintf("[SS] motor recovered: state=IDLE, DRV_ENABLE LOW verified,"
                   " ENN feedback on disabled side confirmed (post-disarm re-check)\n");
    }
    /* 追加(2026-10-23 S1, 合同 4.4): **两条输出路径**都必须有本次有效的关断结果才允许
     * 清除 —— disarm 里那次 mot_pwm_output_off() 就是 Motor 侧的最新一次尝试, 诊断侧
     * 用 step_pwm_off_confirmed()(从未占用该通道的情况算有效, 不虚构成功)。
     * PWM 仍失败/未知 => 保持锁存与非零故障码, 不打印"最终禁能已确认"。
     * ⚠ 这一步只**读**状态, 不再调用停机/清故障/关断动作; 解除 S1 锁存是下面唯一一处
     *   motor_clear_stop_latch()/step_pwm_clear_stop_latch() 调用, 都在全部复核通过之后。 */
    motor_get_stop_state(&m_res, &m_src, &m_rc, &m_lat);
    step_pwm_get_stop_state(&d_res, &d_rc, &d_lat);
    /* 诊断侧"有效"的两种情形: ①最近一次尝试被驱动接受; ②这条路径从未被用过
     * (没占用过通道 => 没有需要确认的关断, 不虚构一次成功)。
     * ⚠ 这里**故意不**用 step_pwm_off_confirmed()(它把失败锁存也算无效, 是给
     *   arm/start/诊断借用用的收紧门): 用在那儿会把人工恢复这条路堵死。 */
    diag_off_valid = (mot_stop_result_is_off(d_res) ||
                      (d_res == MOT_STOP_NOT_ATTEMPTED && !step_pwm_output_active()))
                         ? RT_TRUE : RT_FALSE;
    if (!mot_stop_result_is_off(m_res) || !diag_off_valid)
    {
        rt_kprintf("[SS] fault_reset REFUSED: STEP 关断结果无效"
                   " (motor=%s src=%s rc=%d latched=%s / diag=%s rc=%d latched=%s)"
                   " - 保持锁存, 不清故障码, 不进 MANUAL_CLEAR\n",
                   mot_stop_result_name(m_res), mot_stop_src_name(m_src), (int)m_rc,
                   m_lat ? "YES" : "no", mot_stop_result_name(d_res), (int)d_rc,
                   d_lat ? "YES" : "no");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    rt_kprintf("[SS] STEP 关断结果有效(Motor 侧=驱动接受关断; 诊断侧=%s) -"
               " 仍非波形证明, motor src=%s, diag latched=%s\n",
               mot_stop_result_is_off(d_res) ? "最近一次尝试被接受"
                                             : "从未占用该通道",
               mot_stop_src_name(m_src), d_lat ? "YES" : "no");

    ss_fault_code = FAULT_NONE;
    ss_state = SAFETY_MANUAL_CLEAR;
    /* 人工恢复复核已全部通过 —— 这里才是 S1 关断失败锁存的唯一解除点。
     * 之后仍需 MANUAL_CLEAR→READY(重跑自检)→ 重新 arm, 不会自动输出。 */
    if (m_lat) motor_clear_stop_latch();
    if (d_lat) step_pwm_clear_stop_latch();
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] ==> MANUAL_CLEAR (重跑自检进 READY 后仍需重新 arm)\n");
}
MSH_CMD_EXPORT(fault_reset, measure fault sources then clear latched fault);

void safety_fault_reset_manual(void) { fault_reset(); }

static void safety_state(void)
{
    rt_kprintf("[SS] state=%s fault_code=%u degraded=0x%02X\n",
               safety_state_name(ss_state), ss_fault_code, ss_degraded_mask);
}
MSH_CMD_EXPORT(safety_state, dump safety state machine state);

/* 软件级联调: 强制停机 → 锁死 → 实测源 → 清除(历史命令, 保留) */
static void ss_test(void)
{
    safety_force_shutdown(FAULT_SELF_TEST);
    safety_state();
    fault_reset();
    safety_state();
}
MSH_CMD_EXPORT(ss_test, exercise shutdown-latch-measure-clear chain);

/* ---------- Fix A: 启动分两段 ----------
 * stage 2: safety_state_boot()      事件系统 + 互斥 + BOOT→INIT→SELF_TEST
 *          (必须早于任何慢速 Flash/存储动作, 让 Safety 事件消费者尽早就位)
 * stage 11: safety_startup_selftest() required 自检 + 最终 READY 决策 */

rt_err_t safety_state_boot(void)
{
    if (ss_inited) return RT_EOK;           /* P1-8: 幂等 */

    if (rt_mutex_init(&ss_lock, "ss", RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ERROR;
    ss_lock_ok = RT_TRUE;

    if (rt_event_init(&ss_event, "ss_evt", RT_IPC_FLAG_PRIO) != RT_EOK)
    {
        rt_mutex_detach(&ss_lock);
        ss_lock_ok = RT_FALSE;
        return -RT_ERROR;
    }
    ss_inited = RT_TRUE;

    rt_kprintf("[SS] state machine inited (event+lock), BOOT->INIT->SELF_TEST\n");

    /* 转换失败(理论上不可能: 静态初值就是 BOOT)不撤销事件系统 ——
     * 状态机停在非 SELF_TEST 状态本身就是 fail closed, 但 Safety 线程
     * 仍需一个可用的事件消费者。 */
    if (safety_transition(SAFETY_INIT) != RT_EOK) return -RT_ERROR;
    if (safety_transition(SAFETY_SELF_TEST) != RT_EOK) return -RT_ERROR;
    return RT_EOK;
}

/* bootstrap stage 11 / system_selftest 共用: required 自检 + READY 决策 */
rt_err_t safety_startup_selftest(void)
{
    rt_err_t required = safety_run_selftest();

    if (required == RT_EOK)
    {
        if (safety_transition(SAFETY_READY) != RT_EOK)
        {
            rt_kprintf("[SS] !! SELF_TEST->READY refused (state=%s) - fail closed\n",
                       safety_state_name(safety_state_get()));
            return -RT_ERROR;
        }
    }
    else
    {
        safety_force_shutdown(FAULT_SELF_TEST);
    }

    safety_state();
    return required;
}
