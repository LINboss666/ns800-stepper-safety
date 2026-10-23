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
 *   - fault_reset() 增加一道**只收紧**的复核: Motor 侧必须拿到本次"驱动 API 接受关断"
 *     的有效结果(由它自己调的 motor_disarm() 产生), 诊断侧必须"最近一次尝试被接受"或
 *     "从未占用该通道"(不虚构一次成功) —— 为此 2026-10-23 返修 R03 让 fault_reset 在
 *     判定前先调一次 step_pwm_force_stop() 刷新诊断侧, 与 Motor 侧的刷新对称(纯朝安全
 *     方向: 只请求关断, 不开许可、不投事件)。其余原条件不变: 活动输入实测 + 许可焊盘
 *     LOW + ENN 前后两次有界确认 + disarm 成功, 全部成立才进 MANUAL_CLEAR。
 *     S1 的关断失败锁存**只在这里**解除(motor_clear_stop_latch /
 *     step_pwm_clear_stop_latch), 没有 MSH 旁路; 一次后续成功的普通 stop 不清锁存、
 *     不解 Safety 故障、不自动使能。
 *   - 返修 R03(=S1-NEW-03): 普通外部入口(motor_disarm / pwm_test stop)遇到关断失败时
 *     投**一次** EVT_SOFT_FAULT 交给既有链(Safety 线程消费 → 本文件的
 *     safety_force_shutdown → FAULT_LATCHED + 黑匣子), 否则"跑 fault_reset"这句话在
 *     Safety 仍为 READY 时兑现不了(fault_reset 只接受已锁存的故障)。统一停机与
 *     step_pwm_force_stop() 的失败分支不投事件 => 不构成自激。
 *   - 本轮不处理锁等待/UART 阻塞/在途 arm-enable 竞争(S2), 不宣称停机时延上界。
 *
 * 停机优先(S2, 2026-10-23, phase8-stop-priority-s2-integrated)顺序重排:
 *   - safety_force_shutdown() 现在**先**在 stop_priority 的一个不依赖任何锁的短临界区
 *     里完成两条独立降险动作(STEP 冻结 + 许可 LOW 裸写)并就地记两个原始结果, **再**
 *     做线程慢整理(焊盘回读、ENN 采样、分列核对、黑匣子一次、状态锁存)。控制台打印
 *     不再排在降险之前(逐字符忙等, 每字符上限 6000ms)。
 *   - 慢整理**不再**调用 motor_emergency_stop()/step_pwm_force_stop(): 同一波停机里的
 *     第二次 disable 会让第一次的失败被第二次的成功掩盖。改为
 *     motor_reconcile_after_trip(原始 pe) + step_pwm_reconcile_after_trip()。
 *   - safety_post_event() 对故障类事件先同步做一次短保护再投递; 已经保护过的路径改用
 *     safety_notify_trip_done()(只交接事实, 绝不再关一次), 消费者走
 *     safety_reconcile_trip() —— "消费事件→停机再失败→再投同事件"的自激仍不可能。
 *   - safety_state_get()/safety_fault 视图先读 canonical 抑制位(不先 FOREVER 等 ss_lock),
 *     非故障转换在抑制期间一律拒绝; 启动自检在开始捕获 epoch、进 READY 前比较该 epoch。
 *   - fault_reset() 改为"慢复核 + 一次最终提交": 票据=当时的 epoch, 用
 *     sp_recovery_probe_off() 取代会换 epoch 的普通 motor_disarm(), 诊断侧不再另关一次,
 *     mot_lock 在窗口外取得且取不到就不提交, 最终在**一个**短临界区里核对票据并清
 *     canonical 锁存/取消 owner/清故障码/写 MANUAL_CLEAR/换 epoch/最后清抑制。
 *     S1 的两处锁存清除口(motor_clear_stop_latch / step_pwm_clear_stop_latch)已删除,
 *     代之以同窗口内的 motor_recovery_fields_held() / step_pwm_recovery_fields_held()。
 *   - 仍未处理: 停机时延上界(RISK-03/03a)与整链物理验收(RISK-04-b)继续开放;
 *     本机制只协调本核普通可屏蔽上下文, 不涵盖 NMI/HardFault、另一核或其它总线主设备,
 *     也不涵盖未走本协议的维护旁路写(通用 pin 命令、pin_status、selftest 初始化写)。
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
#include "stop_priority.h"

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

    /* S2(2026-10-23, 任务书 §8.2): 抑制期间的**非故障**转换一律拒绝。
     * 白名单一字未改, 只在这一道门上收紧: 协调层已经保护过之后, 迟到的旧 ramp
     * RUN→READY、旧自检成功、旧配置成功都不能把状态机拉回"可 arm"的 READY。
     * FAULT_LATCHED/ESTOP 两个方向不挡(停机与急停永远允许继续锁存)。 */
    if (next != SAFETY_FAULT_LATCHED && next != SAFETY_ESTOP && sp_inhibited())
    {
        rt_kprintf("[SS] transition REFUSED: %s -> %s"
                   " (协调层已抑制, 需 fault_reset 人工恢复复核)\n",
                   safety_state_name(from), safety_state_name(next));
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return -RT_EPERM;
    }

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

/* ---------- 2026-10-23 S2: 事件分类与"先保护"的三条入口(任务书 §8.1) ----------
 * 优先级保留: 多位事件按现有优先级取**主故障码**(急停类不被较弱事件覆盖), 原因位
 * 合并进协调层的 reason_events(不在这里做二次仲裁)。 */
static rt_uint32_t ss_code_for_event(rt_uint32_t event)
{
    if (event & EVT_ESTOP)        return FAULT_ESTOP;
    if (event & EVT_LIMIT_MIN)    return FAULT_LIMIT_MIN;
    if (event & EVT_LIMIT_MAX)    return FAULT_LIMIT_MAX;
    if (event & EVT_TMC_DIAG)     return FAULT_TMC_DIAG;
    if (event & EVT_MULTI_FAULT)  return FAULT_MULTI_SOURCE;
    if (event & EVT_DRV_FEEDBACK) return FAULT_DRV_FEEDBACK;
    return FAULT_SOFT;                       /* EVT_SOFT_FAULT 及其它软件故障 */
}

static rt_uint32_t ss_events_for_code(rt_uint32_t code)
{
    switch (code)
    {
    case FAULT_ESTOP:         return EVT_ESTOP;
    case FAULT_LIMIT_MIN:     return EVT_LIMIT_MIN;
    case FAULT_LIMIT_MAX:     return EVT_LIMIT_MAX;
    case FAULT_TMC_DIAG:      return EVT_TMC_DIAG;
    case FAULT_MULTI_SOURCE:  return EVT_MULTI_FAULT;
    case FAULT_DRV_FEEDBACK:  return EVT_DRV_FEEDBACK;
    case FAULT_SOFT:
    case FAULT_SELF_TEST:
    case FAULT_BOOT:          return EVT_SOFT_FAULT;
    default:                  return 0u;     /* FAULT_NONE: 不带事件位 */
    }
}

/* 慢整理(不产生任何关断动作): 独立复核焊盘、单次 ENN 反馈采样、两条输出路径的
 * 结果分列核对、黑匣子触发、状态锁存。
 * `pe/le` = 那一次短保护返回的**原始** PWM 错误与许可裸写错误(由调用方带入,
 * 本函数绝不重新猜测); 各子系统自己的镜像整理已由 motor_reconcile_after_trip() /
 * step_pwm_reconcile_after_trip() 做过。 */
static void ss_slow_reconcile(rt_uint32_t code, rt_err_t pe, rt_err_t le)
{
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

    /* ② 独立复核焊盘电平(不复写): 不知道=不安全 */
    pad_low = safety_drv_enable_is_low();
    if (!pad_low)
        rt_kprintf("[SS] !! CRITICAL: DRV_ENABLE pad NOT confirmed LOW (estop_rc=%d"
                   " low_rc=%d) - hardware ENN chain is the only guard !!\n",
                   (int)pe, (int)le);

    /* ②b 单次采样看最终 ENN 反馈是否真到了禁能侧(高)。
     *    这一条只影响"能不能声称整链已安全", 不影响②③④的动作与锁存。 */
    fb_v = safety_fb_now(0u);
    if (fb_v != SAFETY_FB_OK)
        rt_kprintf("[SS] !! ENN feedback NOT on disabled side (verdict=%s)"
                   " - 不打印'最终禁能已确认'; 关闭动作已照做, 故障仍锁存\n",
                   safety_fb_verdict_name(fb_v));

    /* ②c STEP 关断结果分列核对(两条输出路径各自的语义视图, 全部读 canonical)。
     *    只要有一条没有有效关断结果, 就**绝不**打印"STEP stopped"这类话。
     *    故障码与格式不变(沿用投递方给的 code), 详细 rc 由快照与本日志保存。 */
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
               (int)pe, (int)le);
}

/* 统一安全停机(唯一旁路)。
 * 2026-10-23 S2 顺序: ①**先**做短保护(取 ss_lock 与任何打印之前, 两条独立降险动作
 * 在同一窗口内完成并记原始结果) → ②Motor/诊断各自的慢整理 → ③状态机锁存与日志。
 * 慢阶段**不再**调用 motor_emergency_stop()/step_pwm_force_stop(): 那会构成同一波
 * 停机里的第二次 disable, 并用第二次的成功掩盖第一次的失败。 */
void safety_force_shutdown(rt_uint32_t code)
{
    sp_proof_t proof;
    rt_err_t   pe;

    proof.valid = RT_FALSE;
    pe = sp_trip_fast(code, ss_events_for_code(code), MOT_STOP_SRC_ESTOP, &proof);
    /* Motor 侧整理: 镜像 + 软件状态 + 带回读的许可确认(有 settle, 只能在门外) */
    (void)motor_reconcile_after_trip(pe);
    step_pwm_reconcile_after_trip();
    ss_slow_reconcile(code, pe,
                      proof.valid ? proof.permit.write_rc : RT_EOK);
}

/* Safety 线程消费"已经保护过"的通知: 只整理当前仍有效的 pending 事实。
 * pending 由 sp 的 metadata_pending 提供, 取走即清 —— 于是"一次有效故障整理只触发
 * 一次黑匣子"(§8.1), 重复通知/迟到的第二条同事件不会再次触发, 也不会再关一次。 */
void safety_reconcile_trip(rt_uint32_t code, rt_uint32_t event)
{
    sp_proof_t proof;
    rt_uint32_t pcode = code;
    rt_bool_t   had;

    proof.valid = RT_FALSE;
    had = sp_take_pending(&pcode, RT_NULL, &proof);
    if (!had)
    {
        rt_kprintf("[SS] reconcile: 没有待整理的事实(重复通知或已整理过)"
                   " code=%u -> 不再触发黑匣子、不再关断\n", code);
        if (sp_inhibited() && ss_lock_ok)
        {
            /* 仍然要保证状态机与"已抑制"这一事实一致(只改状态, 不碰硬件)。 */
            rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
            if (ss_state != SAFETY_FAULT_LATCHED && ss_state != SAFETY_ESTOP)
            {
                ss_fault_code = (rt_uint32_t)pcode;
                ss_state      = SAFETY_FAULT_LATCHED;
            }
            rt_mutex_release(&ss_lock);
        }
        return;
    }
    if (event == 0u) event = ss_events_for_code(pcode);
    (void)motor_reconcile_after_trip(proof.valid ? proof.pwm.raw_rc : RT_EOK);
    step_pwm_reconcile_after_trip();
    ss_slow_reconcile(pcode,
                      proof.valid ? proof.pwm.raw_rc : RT_EOK,
                      proof.valid ? proof.permit.write_rc : RT_EOK);
    (void)event;
}

void safety_enter_fault(rt_uint32_t code) { safety_force_shutdown(code); }

/* 故障类事件: 先在本上下文做一次短保护(不等任何锁), 再把事件交给 Safety 线程。
 * 投递失败只影响"谁去做慢整理", 不影响已经做完的物理降险(§8.1)。 */
rt_err_t safety_post_event(rt_uint32_t event)
{
    if (event & SAFETY_EVT_FAULT_MASK)
    {
        (void)sp_trip_fast(ss_code_for_event(event), event,
                           MOT_STOP_SRC_ESTOP, RT_NULL);
    }
    if (!ss_inited)
    {
        rt_kprintf("[SS] !! 事件系统未就绪: 已执行短保护, 但慢整理无人消费"
                   " (pending=%s) - 状态命令会照实显示, 不打印'全部整理完成'\n",
                   safety_trip_pending() ? "YES" : "no");
        return -RT_ERROR;
    }
    return rt_event_send(&ss_event, event);
}

/* 只通知, 不再关一次共享模块(见 safety_state.h)。非故障类事件一律拒绝: 那个方向
 * 没有"已经保护过"的事实可交接。 */
rt_err_t safety_notify_trip_done(rt_uint32_t event, rt_uint32_t epoch)
{
    rt_err_t e;

    if ((event & SAFETY_EVT_FAULT_MASK) == 0u) return -RT_EINVAL;
    if (!ss_inited) return -RT_ERROR;
    e = rt_event_send(&ss_event, event);
    if (e != RT_EOK)
        rt_kprintf("[SS] notify trip done 投递失败(%d): 保护已做(epoch=%u),"
                   " pending=%s - 慢整理待 Safety 接收超时路径自行拾取\n",
                   (int)e, epoch, safety_trip_pending() ? "YES" : "no");
    return e;
}

rt_bool_t safety_trip_pending(void)
{
    sp_snapshot_t snap;

    sp_get(&snap);
    return snap.metadata_pending ? RT_TRUE : RT_FALSE;
}

safety_state_t safety_state_get(void)
{
    safety_state_t s;

    /* S2(2026-10-23): 已经形成的抑制必须**立刻**可观测 —— 不先等 ss_lock。
     * 否则协调层已停机而慢整理还排在锁后面(人工恢复正持锁做慢复核)时, 所有调用方
     * (包括 motor_get_gate_fail_mask 的第 2 门)会看到陈旧的 READY/RUN, 或跟着一起
     * 卡在那把锁上。查询本身不触发任何保护动作。 */
    if (sp_inhibited()) return SAFETY_FAULT_LATCHED;

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
    rt_bool_t         tripped = RT_FALSE;
    rt_uint32_t       ticket = 0u;
    sp_proof_t        proof;

    /* ---------- 2026-10-23 S2 人工恢复(任务书 §7): 慢复核 + 一次最终提交 ----------
     * 恢复自己仍然经 ss_lock 串行化; 快保护从不等 ss_lock, 所以"恢复正持锁做慢复核"
     * 不再挡住第一次关断。所有可能等待的锁都在**关中断之前**取, 最终只开**一次**
     * 短临界区。锁序沿用 ss_lock -> mot_lock, 不引入从 mot 等 ss 的反序。 */
    if (ss_lock_ok) rt_mutex_take(&ss_lock, RT_WAITING_FOREVER);
    st = ss_state;
    proof.valid = RT_FALSE;

    /* 第 1 步: 逻辑故障态 + 恢复票据(=当时的 epoch)。
     * "协调层已抑制但慢整理还没跑完"时状态机可能仍是旧的 RUN/READY, 因此抑制位
     * 本身就是待人工恢复的权威判据(§7 第 1 步); 反过来"状态已锁存但协调层没有抑制
     * 记录"(启动早期/历史路径)时, 先按**新的显式保护请求**做一次短保护, 让票据与
     * 后面的复核落在同一代上 —— 只收紧, 不放松任何门。
     * exhausted(epoch 已饱和)一律拒绝: 禁止新的预约与人工恢复, 如实报需重新启动,
     * 本任务不执行真实复位。 */
    if (sp_epoch_exhausted())
    {
        rt_kprintf("[SS] fault_reset REFUSED: epoch 已饱和(exhausted) - 人工恢复与新的"
                   "预约都被禁止, 需**重新启动**; 每次显式保护仍照做关闭, 本轮不做真实复位\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    if (!sp_inhibited() && (st == SAFETY_FAULT_LATCHED || st == SAFETY_ESTOP))
        (void)sp_trip_fast(ss_fault_code ? ss_fault_code : FAULT_SOFT, 0u,
                           MOT_STOP_SRC_DISARM, &proof);
    tripped = sp_recovery_start(&ticket);
    if (!tripped && st != SAFETY_FAULT_LATCHED && st != SAFETY_ESTOP)
    {
        rt_kprintf("[SS] fault_reset: not latched (state=%s, 无抑制记录)\n",
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

    /* 第 3 步(§7): **带票据的专用复核**取代普通 motor_disarm() —— 后者会换 epoch,
     * 那等于把自己的票据作废。sp_recovery_probe_off() 先在同一个短临界区里确认
     * ticket 仍有效且抑制仍在, 然后才做一次**真实**关断 + 许可 LOW 裸写并记账:
     *   成功: 不换 epoch(票据继续有效), 不清历史锁存(那是最终提交的事);
     *   失败: 构成新失败 -> 换 epoch + 保持抑制, 本次恢复自然失效(旧票据不再能用)。
     * O1 要求的一致性(Safety 清除时 Motor 必须一起回 IDLE)由第 6 步窗口内的
     * motor_recovery_fields_held() 兑现, 而不是靠一次会换 epoch 的普通 disarm。 */
    e = sp_recovery_probe_off(ticket, &proof);
    if (e != RT_EOK)
    {
        rt_kprintf("[SS] fault_reset REFUSED: 安全关闭复核失败 (%d)"
                   " - PWM 关断或许可撤销至少一步未确认(失败优先返回 PWM 错误),"
                   " 已换 epoch 并保持抑制, 本次票据自然失效, 保持锁存\n", (int)e);
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }
    /* 复核发出的 LOW 裸写之后补一次带 settle 的回读确认(线程上下文, 窗口外)。
     * 确认只写进"本次这一代"的影子: 票据已失效时不反向破坏新的预约。 */
    {
        rt_err_t ge = safety_drv_permit_confirm_low();

        (void)sp_note_low_confirmed(ge);
        if (ge != RT_EOK)
        {
            rt_kprintf("[SS] fault_reset REFUSED: 复核后的 DRV_ENABLE LOW 回读未确认"
                       " (%d) - 保持锁存, 不进 MANUAL_CLEAR\n", (int)ge);
            if (ss_lock_ok) rt_mutex_release(&ss_lock);
            return;
        }
    }
    /* IO70-NEW-01 (2026-10-21 复审): 安全关闭复核是本路径上**最后一次可能改变许可**
     * 的动作, 上面第 2 步的预检查用的是更早的样本, 不能代表复核之后的真实状态 ——
     * 所以这里必须在复核之后再取一次有界确认, 并让它参与清除决策。
     * ⚠ 停 STEP 与撤销许可已由那次复光照做, 这里**不**回退那些动作; 确认只做
     *   `rt_pin_read` + 有界采样, 不递归调用停机或状态机。
     * 有界窗口 = DRV_FB_TRIES 次 × DRV_FB_SETTLE_MS, 不是时延上界声明(RISK-03)。 */
    {
        safety_fb_verdict_t fv2 = SAFETY_FB_NO_SAMPLE;
        rt_err_t fe2 = safety_drv_feedback_confirm(0u, &fv2);

        if (fe2 != RT_EOK)
        {
            rt_kprintf("[SS] fault_reset REFUSED: ENN feedback not re-confirmed AFTER"
                       " 安全关闭复核 (verdict=%s err=%d) - 保持锁存, 不清故障码,"
                       " 不打印'最终禁能已确认'\n",
                       safety_fb_verdict_name(fv2), (int)fe2);
            if (ss_lock_ok) rt_mutex_release(&ss_lock);
            return;
        }
        rt_kprintf("[SS] motor recovered: DRV_ENABLE LOW verified,"
                   " ENN feedback on disabled side confirmed (post-recheck)\n");
    }
    /* 第 4 步(§7): 诊断侧的**本次结果刷新** = 复用上面那一次真实关断的结果,
     * 不再另关一次共享模块(那正是任务书 §4.2 禁止的"同一波停机里的第二次 disable")。
     * 共享实际动作如实说明: Motor 侧与诊断侧看的是同一个 epwm1 时间基上的同一次
     * rt_pwm_disable; "诊断从未占用"仍然保持 NOT_ATTEMPTED, 不伪造一次 API 调用。
     * ⚠ 这一步只读状态、只抄镜像, 不产生硬件动作、不投事件。 */
    step_pwm_reconcile_after_trip();
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

    /* 第 5 步(§7): mot_lock 必须在窗口**外**取(锁序 ss_lock -> mot_lock 不变);
     * 取不到锁一律**不提交** —— 互斥量没建起来也按取不到处理(fail closed)。 */
    if (motor_lock_for_recovery() != RT_EOK)
    {
        rt_kprintf("[SS] fault_reset REFUSED: 取不到 mot_lock, 不做任何提交"
                   " - 保持锁存\n");
        if (ss_lock_ok) rt_mutex_release(&ss_lock);
        return;
    }

    /* 第 6 步(§7): **只开一次**短临界区, 在里面完成整体提交并重新核对票据:
     *   比较 epoch==ticket / inhibit / 非 exhausted / 本次两路结果属于这一代
     *   → sp_finish_recovery_held(清 canonical 锁存、取消 owner 与运行影子、
     *     清故障原因、换 epoch、**最后**清抑制)
     *   → 只有它成功才写 Motor 影子、诊断影子、ss_fault_code、ss_state。
     * 期间任何新故障都已经换过 epoch, 所以**新故障一定赢**(恢复被拒, 抑制与锁存
     * 保持); 而历史锁存(代次早于本票据)由这次复核的真实关断结果覆盖清除, 不会
     * 永久卡死(S2-C12/S2-C13/S2-C14)。
     * ⚠ 单核 + PRIMASK 屏蔽下, 窗口内的中间状态对外不可观测; 本线程仍持 ss_lock 与
     *   mot_lock, 因此这里没有"检查与写之间的可抢占空隙"。窗口内只有字段赋值。 */
    {
        rt_base_t lvl = sp_win_begin();
        rt_bool_t ok  = sp_recovery_valid_held(ticket, &proof);

        if (ok) ok = sp_finish_recovery_held(ticket);
        if (ok)
        {
            motor_recovery_fields_held();
            step_pwm_recovery_fields_held();
            ss_fault_code = FAULT_NONE;
            ss_state      = SAFETY_MANUAL_CLEAR;
        }
        sp_win_end(lvl);
        motor_unlock_for_recovery();

        if (!ok)
        {
            rt_kprintf("[SS] fault_reset REFUSED: 最终提交前已出现更新的一代请求"
                       " (inhibit 与锁存保持, 未写 MANUAL_CLEAR)\n");
            if (ss_lock_ok) rt_mutex_release(&ss_lock);
            return;
        }
    }
    /* 第 7 步: 先放锁再打印(控制台是逐字符忙等, 不能占着锁做)。
     * 进入 MANUAL_CLEAR 不自动自检、不自动 arm、不出 STEP; 后续 required 自检通过
     * 才 READY, 之后仍需操作员重新 arm。 */
    if (ss_lock_ok) rt_mutex_release(&ss_lock);
    rt_kprintf("[SS] ==> MANUAL_CLEAR (重跑自检进 READY 后仍需重新 arm)"
               " [本次覆盖的历史关断锁存: motor=%s diag=%s, 票据 epoch=%u]\n",
               m_lat ? "已清" : "无", d_lat ? "已清" : "无", ticket);
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

/* bootstrap stage 11 / system_selftest 共用: required 自检 + READY 决策。
 * 2026-10-23 S2(任务书 §8.2): 进入时捕获协调层 epoch, 最终 READY 提交前比较该 epoch。
 * 自检期间任何保护/预约/正式释放/人工恢复都会推进 epoch, 于是"迟到的旧自检成功"
 * 不能跨代把状态机拉进 READY —— 尤其不能把一次人工恢复后的 MANUAL_CLEAR 直接当成
 * "自检成功"。这里只做身份接入, selftest 的产品含义与步骤一字未改, 本轮也不上板跑。 */
rt_err_t safety_startup_selftest(void)
{
    rt_uint32_t epoch0 = sp_epoch();
    rt_err_t required = safety_run_selftest();

    if (required == RT_EOK)
    {
        rt_uint32_t epoch1 = sp_epoch();

        if (epoch1 != epoch0 || sp_inhibited())
        {
            rt_kprintf("[SS] !! SELF_TEST->READY refused: 自检期间协调层已换代"
                       " (epoch %u -> %u, inhibit=%s, pending=%s) - 需重跑自检,"
                       " 不把旧一次成功当结论\n",
                       epoch0, epoch1, sp_inhibited() ? "YES" : "no",
                       safety_trip_pending() ? "YES" : "no");
            return -RT_EPERM;
        }
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
