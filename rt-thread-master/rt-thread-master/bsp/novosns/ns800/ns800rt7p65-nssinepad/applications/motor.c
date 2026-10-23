/*
 * motor.c - 步进电机运动服务 (Phase 7-B, Fix A 门禁/回读/所有权加固)
 *
 * 结构: EPWM1_A 输出 STEP 脉冲, PA.2 输出 DIR, PC.23 输出使能"请求"。
 * 斜坡线程(优先级 8, 10ms 节拍)按线性斜率把 current_hz 推向 target_hz。
 *
 * 安全:
 *   - MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_FALSE 期间 motor_arm() 一律拒绝
 *     (事实更正 2026-10-18: 新扩展板 MCU_DRV_ENABLE 已引出, 当时记录的阻塞项是 U5 与门
 *     损坏 + 急停非门损坏, TMC_ENN 实测约 0V → 焊盘回读 LOW 证明不了驱动已禁能)。
 *     > **2026-10-23 措辞更正(S1 任务书 §5)**: 上面那句"与门/非门损坏"是**维修前的历史
 *     > 解释**, 不是当前已证实的故障: 静态电气与现有 SW1 急停功能已由用户自行检查确认
 *     > 为 PASS (USER_CONFIRMED)(草案 A29)。四个硬件宏仍为 RT_FALSE 的原因是**授权范围**
 *     > (未烧录新版固件、未做瞬态/波形/时延验收), 不是"已证实门芯片坏"。逻辑本身不变。
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
 *   - S1(2026-10-23, 关断结果可信): 本文件里所有 STEP 关断都走**唯一**的
 *     mot_pwm_output_off(src), 它保留 rt_pwm_disable 的真实返回值并记账
 *     (stop_result / stop_src / stop_rc / stop_fail_latched, 见 stop_result.h)。
 *     失败或未取得设备 => 锁存, 触发第 7 门 MOT_GATE_STOP_UNCONFIRMED, 只收紧;
 *     解除只发生在 safety_state.c 的 fault_reset() 人工恢复复核里。
 *     motor_disarm()/motor_emergency_stop() 的返回值 = **PWM 关断失败优先**,
 *     许可写确认结果其次, 两个原始 rc 各自留档; 任一步失败都不豁免另一步动作。
 *     `rt_pwm_disable()==RT_EOK` 一律只称"驱动 API 接受了关断请求"。
 *     本轮**不**改锁/UART/线程结构, 不宣称停机时延上界(S2 待办)。
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
 *
 * S2(2026-10-23, phase8-stop-priority-s2-integrated)停机优先接入(合同不变, 只改顺序与身份):
 *   - 第一次降险不再等 mot_lock/ss_lock/控制台: 停机类入口先调 stop_priority 的短保护
 *     (同一个不依赖锁的窗口内做"STEP 冻结 + 许可 LOW 裸写"并记两个原始结果), 之后才是
 *     本文件的慢整理(motor_reconcile_after_trip / 镜像 / 带回读的确认 / 日志)。
 *   - 开启方向的"资格判定 + 真实写"必须同窗: 许可 HIGH 与 PWM 寄存器提交+启动都在
 *     sp_enter_token()..sp_leave_token() 之内(sp_commit_permit_high / sp_pwm_start_held),
 *     换算在门外(ns800_epwm_prepare), settle/回读/ENN 确认也在门外; 每一次 HIGH 重试
 *     都重新过一次门; armed=1 与 RUN/READY 的最终提交同样带本预约身份。
 *   - 每一次 arm 在最后一次慢校验前 sp_reserve(SP_MOTOR) 取得身份; 失败出口只归还
 *     **仍然有效且未运行**的本次预约(sp_release 自己比 epoch+owner, 旧身份撤销不了新 owner)。
 *   - 守卫/回滚/disarm 一律"恰好一次 disable": 失败时同窗追加抑制/换 epoch/许可 LOW,
 *     不再补第二次关断, 也只通知一次(safety_notify_trip_done)。
 *   - 判定改读 canonical 无等待快照(gate 7、DIR 门、get_stop_state、get_snapshot),
 *     mot.* 里的关断字段降级为镜像, 只供日志与旧 API 的兼容视图。
 *   - 模型边界照旧: 只协调本核普通可屏蔽上下文, 不涵盖 NMI/HardFault、另一核或其它总线
 *     主设备, 也不涵盖通用 pin 命令一类未走本协议的维护旁路写。
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
#include "stop_priority.h"
#include "drv_epwm.h"       /* ns800_epwm_prepared / prepare / commit_prepared_held */

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
 *   1) 按下述口径估算的"检测窗口" = 周期 200ms + 本轮复核耗时。复核 = 2 次可读寄存器
 *      回读, 每次上限 TMC_REPLY_TIMEOUT_MS=100ms(内部 2ms 轮询), 故一轮复核 ≤ ~205ms,
 *      合计 ≈ **405ms** 才可能触发作出反应(不含调度抖动)。
 *      > **2026-10-23 S1 更正**: 这个 405ms **不是最坏停机时延上界** —— 它没有计入
 *        ss_lock / mot_lock / TMC 配置事务 / UART xfer 的等待(RT_WAITING_FOREVER 路径
 *        本轮一律未改, 见任务书 §3 与 §4.5), 因此不能称"可靠上界", 只是软件复核周期本身
 *        的量级估计(未上板实测)。停机时延上界仍属 RISK-03/03a, 待 S2 与上板验证。
 *   2) 复核跑在斜坡线程(prio 8, 栈 MOTOR_THREAD_STACK=768B)内, 期间 10ms 节拍被
 *      阻塞, STEP 仍以最后设置的频率继续输出(PWM 硬件自由运行) —— 复核失败之前
 *      脉冲不会停。栈余量与真实阻塞时长均未上板实测(NOT TESTED)。
 *   3) 判定为停机后的动作是**立即**的: rt_pwm_disable + DRV_ENABLE 写 LOW 并回读,
 *      不经斜坡。2026-10-23 S1 起 rt_pwm_disable 的返回值**已被判定并向上传播**
 *      (失败 => 锁存 + 拒绝 arm/start/诊断借用); 但"API 接受关断"仍不等于示波器证明
 *      无脉冲, ENN 端电平也未做波形实测。
 *   4) 因此: 软件守卫是第二层, 最终切断必须由独立硬件(ENN/急停链)承担; 硬件链
 *      未验收期间 motor 根本不能 armed, 这条路径实际不可达。
 *   5) 是否把 UART 守卫从斜坡线程解耦(独立低优先级线程 + 事件传递):**待上板实测
 *      复核阻塞时长与栈占用之后再决定**, 不在没有测量的情况下先改结构。
 *   ❌ 任何材料都不得写"200ms 内必停", 也不得写"400/405ms 是停机上界"。 */
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
    SUBSYS_UNINIT,
    /* 2026-10-23 S1 追加(与 motor_snapshot_t 末尾新字段一一对应):
     * stop_result / stop_src / stop_rc / stop_fail_latched */
    MOT_STOP_NOT_ATTEMPTED, MOT_STOP_SRC_NONE, 0, 0
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

/* ---------- 2026-10-23 S1: 关断结果的语义与记账 ----------
 * 这三个函数是 stop_result.h 承诺的**唯一**语义解释处; 判定只读状态, 不碰硬件。 */
const char *mot_stop_result_name(mot_stop_result_t r)
{
    switch (r)
    {
    case MOT_STOP_NOT_ATTEMPTED:      return "not-attempted";
    case MOT_STOP_DEV_UNAVAILABLE:    return "dev-unavailable(output-UNKNOWN)";
    case MOT_STOP_OUTPUT_REQUESTED:   return "output-requested(running)";
    case MOT_STOP_CMD_ZERO_UNCONFIRMED: return "cmd-0hz-unconfirmed";
    case MOT_STOP_API_ACCEPTED:       return "api-accepted(驱动接受关断, 非波形证明)";
    case MOT_STOP_API_FAILED:         return "api-failed(output-UNKNOWN)";
    default:                          return "?";
    }
}

const char *mot_stop_src_name(rt_uint8_t src)
{
    switch (src)
    {
    case MOT_STOP_SRC_NONE:          return "none";
    case MOT_STOP_SRC_INIT:          return "init";
    case MOT_STOP_SRC_RAMP_ZERO:     return "ramp-zero";
    case MOT_STOP_SRC_APPLY_FAIL:    return "apply-fail";
    case MOT_STOP_SRC_ARM_ROLLBACK:  return "arm-rollback";
    case MOT_STOP_SRC_GUARD_STOP:    return "guard-stop";
    case MOT_STOP_SRC_FAIL_CLOSED:   return "fail-closed";
    case MOT_STOP_SRC_DISARM:        return "disarm";
    case MOT_STOP_SRC_ESTOP:         return "emergency-stop";
    case MOT_STOP_SRC_DIAG_TEST:     return "pwm_test-stop";
    case MOT_STOP_SRC_DIAG_FORCE:    return "force-stop";
    case MOT_STOP_SRC_STOP_CMD:      return "motor_stop-cmd";
    default:                         return "?";
    }
}

rt_bool_t mot_stop_result_is_off(mot_stop_result_t r)
{
    return (r == MOT_STOP_API_ACCEPTED) ? RT_TRUE : RT_FALSE;
}

rt_bool_t mot_stop_result_is_uncertain(mot_stop_result_t r)
{
    return (r == MOT_STOP_API_FAILED || r == MOT_STOP_DEV_UNAVAILABLE)
               ? RT_TRUE : RT_FALSE;
}

/* 记账(无硬件动作)。调用约定与 mot_drv_en_request() 同: 不自行取 mot_lock,
 * 由调用方在锁内(或初始化单线程期)调用; 一次动作只记一次, 不自动重试。
 * 失败/未知一律**置位** stop_fail_latched; 成功**不清**该位 —— 它只能由
 * fault_reset 的**最终提交窗口**清除(合同 4.4)。2026-10-23 S2 起原
 * motor_clear_stop_latch() 已删除 —— 无身份的公共清除口等于旁路; 现在只有
 * motor_recovery_fields_held() 与 sp_finish_recovery_held() 在同一个短临界区里
 * (票据仍成立才提交)一起解除, 因此不存在"清锁存但不核对新一代故障"的第二条路。 */
static void mot_stop_record(mot_stop_result_t result, rt_uint8_t src, rt_err_t rc)
{
    mot.stop_result = result;
    mot.stop_src    = src;
    mot.stop_rc     = rc;
    if (mot_stop_result_is_uncertain(result))
        mot.stop_fail_latched = 1;
}

/* 立即关 STEP 输出 —— 本文件唯一的 STEP 关断入口, 保留真实错误码(合同 4.2)。
 * 返回 RT_EOK 只说明**驱动 API 接受了关断请求**, 不说明示波器上看不到脉冲。 */
/* 把 canonical 的 Motor 视图抄进本地快照。调用方需持 mot_lock(或在建线程之前)。 */
static void mot_stop_mirror(void)
{
    mot_stop_result_t res;
    rt_uint8_t        src;
    rt_err_t          rc;
    rt_bool_t         lat;

    sp_mirror_motor(&res, &src, &rc, &lat);
    mot.stop_result = (rt_uint8_t)res;
    if (res != MOT_STOP_NOT_ATTEMPTED) mot.stop_src = src;
    mot.stop_rc           = (rt_int16_t)rc;
    mot.stop_fail_latched = lat ? 1u : 0u;
}

/* Motor 的当前预约身份(由 motor_arm 建立, 由成功 disarm / 故障释放)。
 * 跨拍复用同一身份 => 每拍配置提交都要与"最近一次故障请求"比 epoch; 失配即被拒。 */
static sp_token_t     mot_res;
static rt_bool_t      mot_res_ok = RT_FALSE;

static void mot_res_clear(void)
{
    mot_res_ok = RT_FALSE;
}

/* 配置的整数寄存器提交与(需要时的)启动必须在同一个 token 门内, 中间不得再开第二个窗口:
 * 换算已由 ns800_epwm_prepare() 在门外做完, 门内只有固定数目的寄存器写。
 * 时间基处于冻结时写比较值不产生边沿, 而 drv_epwm 已不再由配置路径回写计数模式,
 * 所以"开始输出"只能是显式的 sp_pwm_start_held()。 */
static rt_err_t mot_pwm_commit_gated(const struct ns800_epwm_prepared *prepared)
{
    rt_base_t lvl;
    rt_err_t  e, e2;

    if (!mot_res_ok)
    {
        rt_kprintf("[MOT] PWM 提交被拒: 没有有效预约(需先 motor_arm)\n");
        return -RT_EPERM;
    }
    e = sp_enter_token(&mot_res, &lvl);
    if (e != RT_EOK)
    {
        mot_res_clear();
        return e;
    }
    e2 = ns800_epwm_commit_prepared_held(mot_pwm, prepared);
    if (e2 != RT_EOK) { sp_leave_token(lvl); return e2; }
    e2 = sp_pwm_start_held(&mot_res);
    sp_leave_token(lvl);
    if (e2 != RT_EOK) mot_res_clear();
    return e2;
}

/* S2(2026-10-23): 实际关断与记账交给 stop_priority —— 关断动作、"是否形成抑制/换 epoch"、
 * 许可 LOW 都在**同一个短临界区**里完成, 因此第一次降险不再等 mot_lock/ss_lock/控制台。
 * 本函数仍保留 Motor 侧快照镜像, 让既有 API(motor_get_stop_state / 快照 / 第 7 门)的含义不变:
 *   result/src/rc = 最近一次真实 rt_pwm_disable 调用; latched = 未复核的关断失败。
 * ⚠ 返回非 EOK 时抑制与许可 LOW 已在同一窗口做过, 调用方**不得**再补第二次关断
 *   (S1-R1 R04 的"一个动作内 disable 恰好一次、首错不被后一次成功覆盖"继续成立)。
 * 调用约定不变: 多数调用点持 mot_lock(镜像写需要它); motor_init 在斜坡线程建立之前调用。 */
static rt_err_t mot_pwm_output_off(rt_uint8_t src)
{
    rt_err_t e = sp_off_attempt(SP_MOTOR, src, FAULT_SOFT, EVT_SOFT_FAULT, 0, RT_NULL);

    mot_stop_mirror();
    if (e != RT_EOK)
        rt_kprintf("[MOT] !! STEP 关断未确认 (rc=%d, src=%s): 输出状态未知; 同一短临界区内"
                   "已追加抑制并请求许可 LOW - 频率字段清零不能替代它\n",
                   (int)e, mot_stop_src_name(src));
    return e;
}

/* 停机动作里 PWM 与许可(ENABLE)是**两个独立结果**: 两者都失败时对外返回 PWM 错误,
 * 两个原始 rc 各自留档(快照 stop_rc + 日志), 调用方不得把非零一律解释成"GPIO 失败"。 */
static rt_err_t mot_stop_merge(rt_err_t pwm_e, rt_err_t gpio_e)
{
    if (pwm_e != RT_EOK) return pwm_e;
    return gpio_e;
}

/* 记录"软件已把目标命令值写成 0Hz"(合同 4.2 最后一条): 这不是一次关断动作, 更不
 * 是"输出已关断"的证明。调用约定: 持 mot_lock。已有的失败/未知记录**不被命令覆盖**
 * (未知比"已请求停"更严重, 必须一直照出来直到人工恢复复核)。 */
static void mot_record_zero_cmd(rt_uint8_t src)
{
    if (mot.stop_result == MOT_STOP_API_FAILED ||
        mot.stop_result == MOT_STOP_DEV_UNAVAILABLE)
        return;
    mot.stop_result = MOT_STOP_CMD_ZERO_UNCONFIRMED;
    mot.stop_src    = src;
}

/* 设置指定频率的 50% 占空比脉冲; hz==0 时走同一条 checked 关断路径 */
/* 许可 HIGH 的门内提交: 资格判定与 GPIO SET 写在同一个短临界区(见 stop_priority.h)。 */
static rt_err_t mot_permit_high_gated(void)
{
    sp_proof_t pf;
    rt_err_t   e = sp_commit_permit_high(&mot_res, &pf);

    if (e != RT_EOK) mot_res_clear();
    return e;
}

static rt_err_t mot_pwm_apply(rt_uint32_t hz)
{
    rt_err_t e;

    if (!mot_dev_ok)
    {
        mot_stop_record(MOT_STOP_DEV_UNAVAILABLE, MOT_STOP_SRC_NONE, -RT_ENOSYS);
        return -RT_ENOSYS;
    }
    if (hz == 0)
        return mot_pwm_output_off(MOT_STOP_SRC_RAMP_ZERO);
    {
        struct ns800_epwm_prepared prepared;

        e = ns800_epwm_prepare(mot_pwm, (rt_uint32_t)MOTOR_PWM_CH,
                               1000000000u / hz, 500000000u / hz, &prepared);
        if (e != RT_EOK) return e;
        e = mot_pwm_commit_gated(&prepared);
        mot_stop_mirror();
        if (e == RT_EOK) mot.stop_result = MOT_STOP_OUTPUT_REQUESTED;
        return e;
    }
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
 * 事件 → Safety Thread → force_shutdown → motor_emergency_stop(幂等)。
 * 2026-10-23 返修 R04: 关断**由调用点做一次**并把真实结果作为入参带进来, 本函数
 * 不再第二次调用 rt_pwm_disable —— 之前"回退里连关两次"会让第二次的成功覆盖第一次
 * 的失败(stop_rc/src 都被改写), 与 §4.1/§4.2"每个返回值都被消费、一个动作不互相
 * 覆盖"相矛盾。撤销许可与投递事件仍然照做, 失败也不跳过。 */
static void mot_fail_closed(rt_err_t pwm_e)
{
    rt_err_t ge = mot_drv_en_request(0);

    if (ge != RT_EOK)
        rt_kprintf("[MOT] !! FAIL-CLOSED but DRV_ENABLE LOW UNVERIFIED (%d) !!\n",
                   (int)ge);
    if (pwm_e != RT_EOK)
        rt_kprintf("[MOT] !! FAIL-CLOSED but STEP 关断未确认 (rc=%d): 输出状态未知,"
                   " 已锁存待人工恢复, 不因后续成功而抹掉首错 !!\n", (int)pwm_e);
    /* S2: 本次动作已经在 sp 的窗口里形成抑制并完成两个降险动作 => 只通知, 不再关闭 */
    (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
    rt_kprintf("[MOT] FAIL-CLOSED (pwm error), EVT_SOFT_FAULT notified (已保护, 待整理)"
               " [stop=%s enable-low=%s]\n",
               mot_stop_result_name(mot.stop_result),
               ge == RT_EOK ? "verified" : "UNVERIFIED");
}

/* STEP 关断未确认时的后续 fail-closed 动作(调用约定: mot_lock 已释放)。
 * 与 mot_fail_closed() 的区别只有一个: 关断**已经**由调用点做过并记了真实 rc,
 * 这里不再第二次调用 rt_pwm_disable(S1 不引入自动重试), 只做撤销许可 + 投一次事件。
 * 事件由 Safety 线程消费 -> safety_force_shutdown -> motor_emergency_stop(幂等)。 */
static void mot_stop_unconfirmed_close(rt_err_t pwm_rc, rt_uint8_t src)
{
    /* S2(2026-10-23): 调用点那一次 mot_pwm_output_off() 失败时, sp 已经在**同一个**短
     * 临界区里追加了抑制/换 epoch/许可 LOW 裸写 —— 这里不再第二次写 LOW(那只是重复
     * 同一个动作并拖进 2×1ms 回读延时), 只做**确认**(纯 rt_pin_read + 有界采样)。
     * S1 的"关断失败不跳过撤销许可"仍然成立: LOW 请求已经发出并被记账。 */
    rt_err_t ge = safety_drv_permit_confirm_low();

    (void)sp_note_low_confirmed(ge);
    rt_kprintf("[MOT] !! STEP 关断未确认 (rc=%d src=%s): 许可 LOW 裸写已在门内发出(%s)"
               " -> 置 FAULT 并上报一次; 不宣称'输出已停止'\n",
               (int)pwm_rc, mot_stop_src_name(src),
               ge == RT_EOK ? "LOW verified" : "LOW UNVERIFIED");
    /* 短保护已做过 => 只通知, 消费者不再关第二次(任务书 §4.3/§8.1)。 */
    (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
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

/* arm 失败回滚的唯一执行体(任务书 §4.3/§9)。
 * 一次真实关断 + 许可 LOW 裸写 + 撤销预约(换 epoch)全在同一个短临界区内由 sp 完成;
 * 该次关断失败时同窗追加抑制/换 epoch/LOW, **绝不做第二次 disable**, 也不投新事件
 * (只通知已经保护过的事实, 消费者不再关第二次 => 不构成自激)。
 * 返回对外错误码: PWM 关断失败优先, 否则本次调用点已有的第一个错误/许可确认结果。 */
static rt_err_t mot_arm_rollback(rt_uint32_t code, rt_uint32_t ev, rt_err_t first_err)
{
    rt_err_t pe = sp_off_attempt(SP_MOTOR, MOT_STOP_SRC_ARM_ROLLBACK, code, ev, 1,
                                 RT_NULL);
    rt_err_t ge;

    mot_res_clear();
    mot_lock_take();
    mot_stop_mirror();                           /* 镜像写需要持 mot_lock */
    mot.armed     = 0;
    mot.current_hz = 0;
    mot.target_hz  = 0;
    mot.state      = MOTOR_FAULT;
    mot_lock_release();

    ge = safety_drv_permit_confirm_low();          /* 只确认, 不再第二次写 LOW */
    (void)sp_note_low_confirmed(ge);
    if (ge != RT_EOK)
        rt_kprintf("[MOT] !! rollback: DRV_ENABLE LOW UNVERIFIED (%d)"
                   " - 裸写已在同一短临界区内发出 !!\n", (int)ge);
    if (pe != RT_EOK)
        rt_kprintf("[MOT] !! rollback: STEP 关断也未确认 (rc=%d) -> 输出状态未知,"
                   " 已在同窗追加抑制并锁存待人工恢复\n", (int)pe);
    (void)safety_notify_trip_done(ev, sp_epoch());
    return mot_stop_merge(pe, (first_err != RT_EOK) ? first_err : ge);
}

/* 守卫判定为停机时的执行动作。调用约定: 不得持 mot_lock 调用(内部会再取一次),
 * 与 mot_fail_closed() 同一条 fail-closed 路径。
 * S1: STEP 关断结果与许可撤销结果分别记账; 关断失败**不**跳过撤销许可, 也不因为
 * current_hz 清零而宣称"输出已停"。 */
static void mot_guard_shutdown(rt_uint8_t reason)
{
    rt_err_t pe;
    rt_uint32_t ev = (reason == MOT_GUARD_ENN_FEEDBACK) ? EVT_DRV_FEEDBACK : EVT_SOFT_FAULT;
    rt_uint32_t code = (reason == MOT_GUARD_ENN_FEEDBACK) ? FAULT_DRV_FEEDBACK : FAULT_SOFT;

    /* S2(2026-10-23): 第一次降险**不等**任何锁与控制台 —— 短保护先做(冻结 + 许可 LOW +
     * 抑制 + 换 epoch + 记账), 然后才是本线程的状态整理、日志与一次通知。 */
    (void)sp_trip_fast(code, ev, MOT_STOP_SRC_GUARD_STOP, RT_NULL);
    mot_stop_mirror();
    pe = (rt_err_t)mot.stop_rc;

    mot_lock_take();
    mot.armed = 0;
    mot.target_hz = 0;
    mot.current_hz = 0;
    /* S2(2026-10-23): 这里**不再**调用 mot_pwm_output_off() —— 上面那次 sp_trip_fast
     * 已经在同一个不依赖锁的窗口里做过真实冻结并记了原始 rc(pe 就是它的结果)。
     * 守卫路径第二次 disable 会把第一次的失败被"后一次成功"掩盖(S1-R1 R04 禁止的形状),
     * 也会在已经抑制之后再推进一代 epoch。 */
    mot.state = MOTOR_FAULT;
    if (mot.guard_stops < 0xFFFFu) mot.guard_stops++;
    mot.guard_reason = reason;
    mot_lock_release();

    if (safety_drv_permit_confirm_low() != RT_EOK)
        rt_kprintf("[MOT] !! GUARD STOP but DRV_ENABLE LOW UNVERIFIED (裸写已发出) !!\n");
    if (pe != RT_EOK)
        rt_kprintf("[MOT] !! GUARD STOP but STEP 关断未确认 (rc=%d) -> 输出状态未知,"
                   " current_hz=0 只是软件命令值 !!\n", (int)pe);
    /* 原因分列保留(反馈不一致如实记 EVT_DRV_FEEDBACK, 不假冒急停按钮), 但只**通知**:
     * 短保护已经在本函数开头做过, 消费者不得再关一次共享模块。 */
    (void)safety_notify_trip_done(ev, sp_epoch());
    rt_kprintf("[MOT] DRV GUARD: 驱动配置/反馈异常(reason=%d) -> 已请求停 STEP(src=guard)"
               "、请求使能 LOW、置 FAULT 并上报。!! 这是软件层第二响应, 不替代硬件独立切断"
               " !! [stop=%s]\n", (int)reason, mot_stop_result_name(mot.stop_result));
}

/* ---------- 2026-10-23 S1: 斜坡节拍体抽成单步函数(生产行为等价) ----------
 * 为什么抽: 任务书 §4.5 允许"为测试作小型静态单步函数抽取, 须保持生产调用顺序与
 * 线程周期, 说明前后等价"。抽成一次一拍之后, 主机链可以**确定性地执行一拍**,
 * 直接证明"斜坡减到 0 / apply 失败回退 / 运行守卫停机"三条路径真的走了生产关断
 * 路径并消费了错误, 而不是只测一个纯 helper。
 * 等价性论证(逐条, 供复审核对):
 *   1) 原循环体每条分支的结尾都是 `rt_thread_mdelay(MOTOR_RAMP_PERIOD_MS); continue;`,
 *      或不落到分支尾的同一句; 现在把这句 mdelay **唯一地**放到外层 entry 里,
 *      分支内改成 return —— 控制流一一对应, 没有新增/删除/换序任何动作。
 *   2) 原先在栈上的 guard_ctr / guard_busy_streak 是"线程生命周期内持久"的状态;
 *      改成文件内 static 后仍然只有斜坡路径读写(单一 owner), 取值序列不变。
 *      本服务的线程只由 motor_init() 创建一次, 不存在两份并行实例。
 *   3) 顺序不变: 守卫计数 → ENN 每拍反馈复核 → 配置守卫判定 → 取 target →
 *      状态判定 → 斜坡步进 → 关断/apply → 解锁; mot_lock 的取放点一字未动。
 *   4) 生产入口 motor_ramp_entry() 仍是 `while(1){ tick(); mdelay(10); }`,
 *      目标上的周期与行为不变(本轮不重构线程, S2 才动)。 */
static rt_uint32_t mot_ramp_guard_ctr;
static rt_uint8_t  mot_ramp_guard_busy;

static void motor_ramp_tick(void)
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
        mot_ramp_guard_ctr++;
        if (mot_ramp_guard_ctr >= MOTOR_DRV_GUARD_TICKS)
        { mot_ramp_guard_ctr = 0; guard_due = 1u; }
    }
    else mot_ramp_guard_ctr = 0;
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
        in.busy_streak  = mot_ramp_guard_busy;
        motor_guard_decide(&in, &out);
        mot_ramp_guard_busy = out.busy_streak;
        if (out.stop) mot_guard_shutdown(out.reason);
    }
    else mot_ramp_guard_busy = 0u;

    mot_lock_take();
    tgt = mot.target_hz;

    if (mot.state == MOTOR_IDLE || mot.state == MOTOR_FAULT)
    {
        mot_lock_release();
        return;
    }

    if (!mot.armed) tgt = 0;

    if (mot.current_hz == tgt)
    {
        was_moving = (mot.state == MOTOR_CRUISE ||
                      mot.state == MOTOR_ACCEL || mot.state == MOTOR_DECEL);
        armed = mot.armed;                      /* Fix A: 锁内取快照 */
        mot.state = (tgt == 0) ? MOTOR_IDLE : MOTOR_CRUISE;
        mot_lock_release();

        /* P1-9: 减速到 0(受控停止完成) → RUN 回 READY(经 transition API)。
         * S2(任务书 §8.2): 这条"回到 READY"是一次真实的资格恢复, 必须带**本预约**
         * 身份 —— 已被抑制/换代时旧 ramp 的那一拍不能把状态机拉回可 arm 的 READY。
         * safety_transition() 自己也会在抑制时拒绝, 这里是第二道(在调用方就丢掉,
         * 不产生"看起来成功过"的日志)。 */
        if (tgt == 0 && was_moving && armed)
        {
            if (!mot_res_ok || sp_token_current(SP_MOTOR, RT_NULL) != RT_EOK)
            {
                rt_kprintf("[MOT] RUN->READY 放弃: 本预约已失效(新故障/已恢复换代),"
                           " 旧斜坡的这一拍不把状态机拉回 READY\n");
            }
            else if (safety_transition(SAFETY_READY) != RT_EOK)
                rt_kprintf("[MOT] RUN->READY transition refused\n");
        }
        return;
    }

    mot.current_hz = mot_ramp_step(mot.current_hz, tgt,
                                   mot.accel_hz_s, mot.decel_hz_s);

    if (mot.current_hz == 0)
    {
        rt_err_t pe = mot_pwm_output_off(MOT_STOP_SRC_RAMP_ZERO);

        if (pe != RT_EOK)
        {
            /* 合同 4.2: 斜坡减到 0 但关断未确认 => **不算**正常 IDLE 完成,
             * 不做 RUN→READY(下一拍 state=FAULT 直接跳过该分支), 走故障处置。
             * 频率字段清零只是软件命令值, 输出状态未知并已锁存。 */
            mot.armed = 0;
            mot.target_hz = 0;
            mot.current_hz = 0;
            mot.state = MOTOR_FAULT;
            mot_lock_release();
            mot_stop_unconfirmed_close(pe, MOT_STOP_SRC_RAMP_ZERO);
            return;
        }
        mot.state = (tgt == 0) ? MOTOR_IDLE : MOTOR_ACCEL;
    }
    else
    {
        if (mot_pwm_apply(mot.current_hz) != RT_EOK)
        {
            /* P0-4: fail-closed(锁内只改状态, 锁外发事件) */
            rt_err_t ae = mot_pwm_output_off(MOT_STOP_SRC_APPLY_FAIL);

            mot.armed = 0;
            mot.current_hz = 0;
            mot.target_hz = 0;
            mot.state = MOTOR_FAULT;
            mot_lock_release();
            mot_fail_closed(ae);                  /* R04: 携带这一次的真实关断结果 */
            return;
        }
        mot.state = (mot.current_hz < tgt) ? MOTOR_ACCEL
                  : (mot.current_hz > tgt) ? MOTOR_DECEL : MOTOR_CRUISE;
    }
    mot_lock_release();
}

/* 斜坡线程: 10ms 节拍, 线性逼近 target(节拍体见 motor_ramp_tick) */
static void motor_ramp_entry(void *param)
{
    (void)param;

    while (1)
    {
        motor_ramp_tick();
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
    /* 追加第 7 位(2026-10-23 S1, 只在既有位之后追加): STEP 关断结果不可信 ——
     * 曾有一次 rt_pwm_disable 失败或未取得设备句柄, 且未经 fault_reset 人工恢复复核。
     * 这一位**只收紧**: 前六道门一字未改, 也不会因为 current_hz==0 而清零。
     * S2: 直接读 canonical(无等待快照), 不用可能陈旧的镜像 bool 代替门禁。 */
    if (sp_motor_stop_latched())                 bad |= MOT_GATE_STOP_UNCONFIRMED;

    return bad;
}

/* ---------- 2026-10-23 S1: 关断结果观测 / S2: 改读 canonical ----------
 * 语义与 S1 完全一致(result/src/rc = 最近一次**真实** rt_pwm_disable 调用,
 * latched = 未经人工恢复复核的关断失败), 但真相住在 stop_priority: 本地字段只是镜像,
 * 直接读镜像会在"刚保护过还没整理完"的窗口里给出陈旧值(任务书 §9 最后一条)。 */
void motor_get_stop_state(mot_stop_result_t *out_result, rt_uint8_t *out_src,
                          rt_err_t *out_rc, rt_bool_t *out_latched)
{
    sp_view_motor(out_result, out_src, out_rc, out_latched);
}

/* S2: 原 motor_clear_stop_latch() 已删除 —— 关断失败锁存的解除只剩下
 * fault_reset() 的**最终提交窗口**(motor_recovery_fields_held() +
 * sp_finish_recovery_held()), 那里同时保证本次复核的票据仍然有效。
 * 保留一个无身份的公共清除口就等于保留一条绕过人工恢复门的旁路。 */

static void mot_print_gate_fail(rt_uint32_t bad)
{
    rt_kprintf("[MOT] arm gate mask=0x%X:", bad);
    if (bad & MOT_GATE_ENABLE_PATH)   rt_kprintf(" enable-path-not-validated");
    if (bad & MOT_GATE_NOT_READY)     rt_kprintf(" safety-not-READY");
    if (bad & MOT_GATE_UNHEALTHY)     rt_kprintf(" subsystem-unhealthy");
    if (bad & MOT_GATE_NOT_PROTECTED) rt_kprintf(" protection-not-ready");
    if (bad & MOT_GATE_NO_DRV_CONFIG) rt_kprintf(" drive-config-not-applied");
    if (bad & MOT_GATE_FB_MISMATCH)   rt_kprintf(" enn-feedback-mismatch");
    if (bad & MOT_GATE_STOP_UNCONFIRMED) rt_kprintf(" step-stop-unconfirmed");
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
    rt_kprintf("[MOT] gate7 detail: motor_stop=%s src=%s rc=%d latched=%s |"
               " diag_off_confirmed=%s (current_hz=0 是命令值, 不构成关断证明)\n",
               mot_stop_result_name(mot.stop_result), mot_stop_src_name(mot.stop_src),
               (int)mot.stop_rc, mot.stop_fail_latched ? "YES" : "no",
               step_pwm_off_confirmed() ? "YES" : "NO");
}

/* ---------- 正式 API (motor.h) ---------- */

rt_err_t motor_init(void)
{
    rt_err_t e;
    rt_err_t pe;

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
        /* S1 + 返修 R02(=S1-NEW-02): 设备句柄都没取得 => STEP 关断"未尝试,
         * 结果未知", 如实锁存。但这个错误出口**也必须尝试撤销许可** ——
         * 不能因为"没拿到 PWM 设备"就跳过 PC.23 的降级动作(这里不调用任何
         * PWM API, 所以不会碰 NULL 句柄); 设备不可用与 LOW 的原始结果分别留档,
         * 并且明确作废 mot_dev_ok, 不留"旧 dev_ok=TRUE 配 NULL 句柄"的陈旧组合。 */
        /* 无设备: 协调层记 DEV_UNAVAILABLE/未尝试(绝不调用 NULL 句柄), 并仍独立尝试许可 LOW。 */
        (void)sp_bind_permit_pin(safety_drv_permit_pin());
        pe = sp_off_attempt(SP_MOTOR, MOT_STOP_SRC_INIT, FAULT_SOFT, EVT_SOFT_FAULT,
                            0, RT_NULL);
        mot_pwm    = RT_NULL;
        mot_dev_ok = RT_FALSE;
        mot_lock_take();
        mot_stop_mirror();
        mot.state = MOTOR_IDLE;
        mot.current_hz = 0;
        mot.target_hz = 0;
        mot.armed = 0;
        mot_lock_release();
        e = safety_drv_permit_confirm_low();
        (void)sp_note_low_confirmed(e);
        rt_kprintf("[MOT] init FAILED: %s 未取得 -> 仍已请求许可 LOW (%s, rc=%d);"
                   " STEP 侧=dev-unavailable(rc=%d), 不创建斜坡线程,"
                   " health=FAILED [设备不可用与 LOW 的原始结果分别留档]\n",
                   MOTOR_PWM_DEV, e == RT_EOK ? "已回读确认" : "未确认", (int)e,
                   (int)pe);
        mot.health = SUBSYS_FAILED;
        return -RT_ERROR;
    }
    mot_dev_ok = RT_TRUE;
    /* S2: 把同一个 epwm1 句柄与已解析的许可数值 pin 交给协调层 —— 短保护窗口内
     * 不做 rt_device_find / rt_pin_get 的字符串查找。绑定不重置 epoch/锁存/抑制。 */
    (void)sp_bind_pwm(mot_pwm, MOTOR_PWM_CH);
    (void)sp_bind_permit_pin(safety_drv_permit_pin());

    pe = mot_pwm_output_off(MOT_STOP_SRC_INIT);
    mot_lock_take();
    mot.state = MOTOR_IDLE;
    mot.current_hz = 0;
    mot.target_hz = 0;
    mot.armed = 0;
    mot_lock_release();

    /* Fix A / A7: 初始"禁能"必须是经回读确认的 LOW, 否则不创建斜坡线程。
     * S1(合同 4.2): 上一步关断失败**不豁免**这一步 —— 撤销许可总要先试;
     * 反过来这一步成功也不能把关断失败说成"初始化已安全"。 */
    e = mot_drv_en_request(0);
    if (pe != RT_EOK)
    {
        rt_kprintf("[MOT] init FAILED: STEP 关断未被驱动接受 (rc=%d) -> 不创建斜坡线程,"
                   " health=FAILED, 不称'已停止' [许可 LOW %s, 两者原始 rc 均留档]\n",
                   (int)pe, e == RT_EOK ? "已回读确认" : "未确认");
        mot.health = SUBSYS_FAILED;
        return mot_stop_merge(pe, e);
    }
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
               " DRV_ENABLE LOW verified, STEP=%s)\n",
               MOTOR_HARDWARE_ENABLE_PATH_VALIDATED ? "YES" : "NO",
               mot_stop_result_name(mot.stop_result));
    rt_kprintf("[MOT]   注: STEP=api-accepted 只说明驱动**接受了关断请求**,"
               " 不是示波器证明无脉冲(BUG-008 仍未验收)\n");
    return RT_EOK;
}

rt_err_t motor_arm(void)
{
    rt_uint32_t bad;
    rt_err_t e;
    rt_bool_t reserved_here = RT_FALSE;
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

    /* S2(2026-10-23): 最后一次慢校验之前先取本次预约身份。此后 HIGH 写、armed 提交、
     * 每拍 PWM 提交都要与它比 epoch+owner —— 期间任何新故障都会换 epoch 并撤销资格。 */
    if (sp_reserve(SP_MOTOR, &mot_res) != RT_EOK)
    {
        rt_kprintf("[MOT] arm REFUSED: 取不到 STEP 通道预约(已抑制/饱和/他人持有/未复核失败)\n");
        e = -RT_EPERM;
        goto out;
    }
    mot_res_ok = RT_TRUE;
    reserved_here = RT_TRUE;

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

    /* P1-10 + S2: 许可 HIGH 是**唯一**能让驱动器得电的软件动作, 它的"资格判定 + 寄存器写"
     * 必须在同一个短临界区内完成(sp_commit_permit_high); settle/回读在门外做, 而**每一次
     * 重写都是新的开启动作**, 因此重试也必须重新过门(不再用无身份的裸写入口)。 */
    {
        rt_err_t ce;

        e = mot_permit_high_gated();
        if (e == RT_EOK)
        {
            ce = safety_drv_permit_confirm_read(1u);
            (void)sp_note_permit_confirmed(&mot_res, ce);
            if (ce != RT_EOK)
            {
                e = mot_permit_high_gated();               /* 重试: 再过一次门 */
                if (e == RT_EOK)
                {
                    ce = safety_drv_permit_confirm_read(1u);
                    (void)sp_note_permit_confirmed(&mot_res, ce);
                }
                if (ce != RT_EOK) e = (ce != RT_EOK) ? ce : e;
            }
        }
        mot.drv_en_request = (e == RT_EOK) ? PIN_HIGH : mot.drv_en_request;
    }
    if (e != RT_EOK)
    {
        rt_kprintf("[MOT] arm FAILED: DRV_ENABLE HIGH not verified (%d),"
                   " rollback fail-closed\n", (int)e);
        e = mot_arm_rollback(FAULT_SOFT, EVT_SOFT_FAULT, e);
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
            e = mot_arm_rollback(FAULT_DRV_FEEDBACK, EVT_DRV_FEEDBACK, -RT_EPERM);
            goto out;
        }
    }

    /* armed 的最终提交也必须在同一身份的门内(检查与写之间不留可抢占的空隙)。
     * 锁序保持 mot_lock -> 短窗口, 不引入反向等待。 */
    mot_lock_take();
    {
        rt_base_t lvl;

        if (sp_enter_token(&mot_res, &lvl) != RT_EOK)
        {
            mot_lock_release();
            mot_res_clear();
            rt_kprintf("[MOT] arm REFUSED: 提交 armed 前预约已失效(新故障/新预约)\n");
            return -RT_EPERM;
        }
        mot.armed = 1;
        sp_leave_token(lvl);
    }
    mot_lock_release();
    rt_kprintf("[MOT] ARMED (drv_en request=HIGH readback verified,"
               " ENN feedback=LOW confirmed)\n");
    e = RT_EOK;

out:
    /* 失败出口(任务书 §9"失败出口释放仍有效的预约, 旧 token 不释放新 owner"):
     * 只归还**本次调用**取得的预约。sp_release 自己比较 epoch+owner 并要求
     * phase==RESERVED, 所以已经起转(仍输出中)或已被新故障换掉身份时它一定失败,
     * 且绝不改动新 owner —— 不存在"旧 arm 的尾巴撤销新预约"这条路。 */
    if (e != RT_EOK)
    {
        if (reserved_here && mot_res_ok && sp_get_phase() == SP_RESERVED)
            (void)sp_release(&mot_res);
        if (reserved_here) mot_res_clear();
    }
    tmc2209_cfg_txn_end();
    return e;
}

rt_err_t motor_disarm(void)
{
    rt_err_t pe;
    rt_err_t ge;

    mot_lock_take();
    mot.armed = 0;
    mot.target_hz = 0;
    mot.current_hz = 0;
    mot_lock_release();

    /* S2: 关断、失败时的追加抑制/许可 LOW、成功时的"LOW + 撤销预约 + 换 epoch"
     * 全在同一个短临界区内完成(本函数不再自己持 mot_lock)。 */
    pe = sp_off_attempt(SP_MOTOR, MOT_STOP_SRC_DISARM, FAULT_SOFT, EVT_SOFT_FAULT,
                        1, RT_NULL);
    mot_res_clear();            /* 成功已撤销预约(换 epoch); 失败被抑制 —— 两种都不再有有效身份 */
    mot_lock_take();
    mot_stop_mirror();                            /* 镜像写与状态写同锁(§9 影子写点收口) */
    /* 合同 4.2/4.4: 关断未被驱动接受时**不**回到"正常可启动 IDLE" —— 进 MOTOR_FAULT,
     * 于是 motor_arm 的静止检查与第 7 位一起把后续 arm/start 挡住(只收紧, 不放松)。 */
    mot.state = (pe == RT_EOK) ? MOTOR_IDLE : MOTOR_FAULT;
    mot_lock_release();

    ge = safety_drv_permit_confirm_low();        /* 写+settle+回读(禁能方向, 允许无身份) */
    (void)sp_note_low_confirmed(ge);
    if (ge != RT_EOK)
        rt_kprintf("[MOT] !! disarm: DRV_ENABLE LOW UNVERIFIED (%d) - 裸写已在门内发出 !!\n",
                   (int)ge);
    if (pe != RT_EOK)
        rt_kprintf("[MOT] !! disarm: STEP 关断未确认 (rc=%d) -> 输出状态未知,"
                   " state=FAULT 并锁存, 不回 IDLE !!\n", (int)pe);
    /* 追加(2026-10-23 返修 R03 = S1-NEW-03): 普通 disarm 遇到关断失败时, 把故障交给
     * **既有**链: 投一次 EVT_SOFT_FAULT -> Safety 线程消费 -> safety_force_shutdown()
     * 统一停机 + FAULT_LATCHED + 黑匣子, 于是"按提示执行 fault_reset"这条路从此可达。
     * - 这里只投事件: 不改 Safety 状态、不拿 ss_lock(事件投递与 ss_lock 无关),
     *   不新增 MSH 恢复旁路;
     * - 统一停机/紧急停机的失败分支**不**投事件 => 不会形成"消费事件->停机失败->
     *   再投同事件"的自激; fault_reset 内部再调本函数时若又投一次, 结果只是再次
     *   锁存, 方向保守。 */
    if (pe != RT_EOK) (void)safety_notify_trip_done(EVT_SOFT_FAULT, sp_epoch());
    /* 追加(2026-10-21): 如实报告最终 ENN 反馈。没确认到禁能侧时**绝不**打印成"整链已安全"。
     * 关闭动作(停 STEP + 请求 LOW)已经照做, 这一步只是不谎报。 */
    {
        safety_fb_verdict_t fv = safety_fb_now(0u);

        rt_kprintf("[MOT] DISARM (动作已做: STEP %s / 许可 LOW %s / ENN feedback=%s%s)\n",
                   mot_stop_result_name(mot.stop_result),
                   ge == RT_EOK ? "verified" : "UNVERIFIED",
                   safety_fb_verdict_name(fv),
                   fv == SAFETY_FB_OK ? "" : " - 最终禁能**未确认**, 不得称整链已安全");
    }
    /* 两个原始 rc 各自留档(日志 + 快照 stop_rc), 对外失败优先返回 PWM 关断错误。 */
    return mot_stop_merge(pe, ge);
}

/* Fix A / A8: 只允许完全静止时改方向。
 * 2026-10-23 返修 R01(=S1-NEW-01): "静止"必须包含 **STEP 关断已被确认**这一层 ——
 * 关断失败/未取得设备(以及诊断侧 owner 未交回)期间, current_hz==0 与 state=FAULT 都
 * 只是软件侧状态, 输出可能仍在走, 此时翻 DIR 等于在"可能运行中"翻方向。只收紧这一种
 * 情形: 原来的"非静止拒 -RT_EBUSY"与正常静止时设方向的能力都不动。 */
rt_err_t motor_set_direction(rt_uint8_t dir)
{
    rt_base_t d;
    rt_bool_t at_rest;
    rt_bool_t stop_unconfirmed;

    mot_lock_take();
    at_rest = (mot.current_hz == 0) &&
              (mot.state == MOTOR_IDLE || mot.state == MOTOR_FAULT);
    /* S2: 判据改读 canonical(无等待快照) —— 镜像可能还是陈旧的, 而"不能拿一个
     * 陈旧 bool 代替门禁"是任务书 §4.1 明确要求的。 */
    stop_unconfirmed = (sp_motor_stop_latched() || sp_diag_stop_latched() ||
                        !step_pwm_off_confirmed()) ? RT_TRUE : RT_FALSE;
    if (!at_rest)
    {
        rt_kprintf("[MOT] dir REFUSED: not at rest (state=%d hz=%u) -"
                   " DIR change mid-run injects a stall\n",
                   mot.state, mot.current_hz);
        mot_lock_release();
        return -RT_EBUSY;
    }
    if (stop_unconfirmed)
    {
        rt_kprintf("[MOT] dir REFUSED: STEP 关断未确认 (stop=%s rc=%d"
                   " latched=%u diag_off_confirmed=%s) - hz=0/FAULT 只是软件状态,"
                   " 不能当物理静止\n", mot_stop_result_name(mot.stop_result),
                   (int)mot.stop_rc, (unsigned)(sp_motor_stop_latched() ? 1u : 0u),
                   step_pwm_off_confirmed() ? "YES" : "NO");
        mot_lock_release();
        return -RT_EPERM;
    }
    mot_lock_release();

    d = safety_pin(PIN_NAME_TMC_DIR);
    if (d < 0) return -RT_ERROR;
    /* S2(任务书 §9): DIR 本身不启动输出, 但"检查后被新启动越过"同样是竞争 ——
     * 静止/关断可信这两道复核与实际 DIR 写在**同一个**短窗口里重核一次抑制位
     * (新故障一旦形成, DIR 写就不再发生)。窗口内只有一次寄存器写。
     * mot_lock 已在上面释放, 这里不再取放它(锁与窗口是两个层次的东西)。 */
    {
        rt_base_t lvl = sp_win_begin();
        rt_bool_t inhibited = sp_inhibited_held();

        if (!inhibited) rt_pin_write(d, dir ? PIN_HIGH : PIN_LOW);
        sp_win_end(lvl);
        if (inhibited)
        {
            rt_kprintf("[MOT] dir REFUSED: 写入窗口内已出现停机抑制\n");
            return -RT_EPERM;
        }
    }
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
    if (hz == 0) mot_record_zero_cmd(MOT_STOP_SRC_STOP_CMD);
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
    {
        rt_base_t lvl;

        if (mot.state == MOTOR_IDLE)
        {
            if (sp_enter_token(&mot_res, &lvl) != RT_EOK)
            {
                mot_lock_release();
                mot_res_clear();
                rt_kprintf("[MOT] start REFUSED: 起转提交前预约已失效(新故障/新预约)\n");
                return -RT_EPERM;
            }
            mot.state = MOTOR_ACCEL;
            (void)sp_phase_set(&mot_res, SP_RUNNING);
            sp_leave_token(lvl);
        }
        mot_lock_release();
    }
    return RT_EOK;
}

rt_err_t motor_stop(void)
{
    mot_stop_result_t res;

    mot_lock_take();
    mot.target_hz = 0;      /* 斜坡自动减速到 0 → IDLE + RUN→READY(保持 armed) */
    /* S1: 此刻只是**软件命令**要停, STEP 是否真的关断要等斜坡线程的那一次
     * checked 关断结果 —— 这里绝不打印"输出已停止"。 */
    mot_record_zero_cmd(MOT_STOP_SRC_STOP_CMD);
    res = mot.stop_result;
    mot_lock_release();
    rt_kprintf("[MOT] stop requested: target_hz=0 (软件命令值), STEP 实际关断结果见"
               " stop_result(当前=%s)\n", mot_stop_result_name(res));
    return RT_EOK;
}

/* 短保护之后的 Motor 侧**慢整理**(任务书 §4.2"先动作, 再慢整理"的后半段):
 * 只做快照镜像、软件状态位、带回读的许可确认与日志 —— 不再产生任何关断动作。
 * `pe` 必须是那次短保护返回的**原始** PWM 错误(不得由本函数重新猜测)。
 * 对外合同不变: 失败优先返回 PWM 关断错误, 许可确认结果独立留档。 */
rt_err_t motor_reconcile_after_trip(rt_err_t pe)
{
    rt_err_t ge;

    mot_res_clear();
    mot_lock_take();
    mot_stop_mirror();                            /* 镜像写与状态写同锁 */
    mot.armed = 0;
    mot.target_hz = 0;
    mot.current_hz = 0;
    mot.state = MOTOR_FAULT;
    mot_lock_release();

    /* P1-10 + A7: 回读确认并把结果传播给调用方(safety_force_shutdown 会记录
     * CRITICAL 但依然锁存 FAULT_LATCHED)。绝不吞掉失败。
     * S1: 关断失败**不**豁免撤销许可, 撤销成功也**不**掩盖关断失败。
     * S2: LOW 裸写已由短保护在同一窗口发出, 这里只做带 settle 的**回读确认**。 */
    ge = safety_drv_permit_confirm_low();
    (void)sp_note_low_confirmed(ge);
    if (ge != RT_EOK)
        rt_kprintf("[MOT] !! EMERGENCY STOP but DRV_ENABLE LOW UNVERIFIED (%d)"
                   " - 裸写已在同一短临界区内发出 !!\n", (int)ge);
    if (pe != RT_EOK)
        rt_kprintf("[MOT] !! EMERGENCY STOP but STEP 关断未确认 (rc=%d):"
                   " 不能称'输出已停止', 失败已锁存待人工恢复 !!\n", (int)pe);
    /* 追加(2026-10-21): 反馈状态如实报告。停 STEP 与撤销许可**总是**先做,
     * 反馈读不到只影响"最终禁能是否已确认"这句话, 不影响动作本身。 */
    {
        safety_fb_verdict_t fv = safety_fb_now(0u);

        rt_kprintf("[MOT] EMERGENCY STOP (drv_en LOW %s, ENN feedback %s%s, STEP %s)\n",
                   ge == RT_EOK ? "verified" : "UNVERIFIED",
                   safety_fb_verdict_name(fv),
                   fv == SAFETY_FB_OK ? "" : " -> final disable NOT confirmed",
                   mot_stop_result_name(mot.stop_result));
    }
    /* 返回值: PWM 关断失败优先, 否则许可写确认结果; 两个原始 rc 均另存。 */
    return mot_stop_merge(pe, ge);
}

rt_err_t motor_emergency_stop(void)
{
    /* S2(2026-10-23): 本函数不再自己等 mot_lock 才去关断 —— 短保护(冻结 + 许可 LOW +
     * 抑制 + 换 epoch + 两个原始结果)在一个不依赖任何锁的窗口里先做完。 */
    return motor_reconcile_after_trip(
        sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, RT_NULL));
}

/* ---------- 人工恢复用的锁接入与窗口内字段复位(任务书 §7) ----------
 * fault_reset 的最终提交必须在**一个**短临界区里同时落 Motor 影子、Safety 状态与
 * canonical, 所以这里给三个专用入口:
 *   motor_lock_for_recovery()   窗口**外**取 mot_lock(锁序保持 ss_lock -> mot_lock,
 *                               不引入 mot 等 ss 的反序); 锁不可用一律返回错误 =>
 *                               调用方拒绝提交(fail closed, 与 R02 同一纪律)。
 *   motor_recovery_fields_held()调用方已在窗口内: 只有固定字段赋值, 不取锁/不打印/
 *                               不碰硬件 —— 硬件状态已由那次带票据的复核确认。
 *   motor_unlock_for_recovery() 窗口外释放。
 * ⚠ 这三个入口只允许 safety_state.c 的 fault_reset() 使用, 不导出 MSH 命令。 */
rt_err_t motor_lock_for_recovery(void)
{
    if (!mot_lock_ok) return -RT_EIO;          /* 互斥量没建起来 = 不敢提交 */
    rt_mutex_take(&mot_lock, RT_WAITING_FOREVER);
    return RT_EOK;
}

void motor_recovery_fields_held(void)
{
    mot.armed             = 0;
    mot.target_hz         = 0;
    mot.current_hz        = 0;
    mot.state             = MOTOR_IDLE;
    mot.stop_result       = MOT_STOP_NOT_ATTEMPTED;
    mot.stop_rc           = RT_EOK;
    mot.stop_fail_latched = 0;                 /* 历史锁存: 本次复核已在同窗口覆盖 */
    mot.drv_en_request    = PIN_LOW;
}

void motor_unlock_for_recovery(void)
{
    if (mot_lock_ok) rt_mutex_release(&mot_lock);
}

rt_err_t motor_get_snapshot(motor_snapshot_t *snap)
{
    sp_snapshot_t c;

    if (snap == RT_NULL) return -RT_EINVAL;
    mot_lock_take();
    *snap = mot;
    mot_lock_release();

    /* S2(任务书 §9 最后一条): canonical 覆盖陈旧镜像。已经形成的抑制/关断失败必须
     * 立刻在快照里可见 —— 否则"刚保护过、慢整理还排在锁后面"的时候, 调用方会拿
     * 旧的 armed/hz/stop 影子当成"没有发生过保护"。这里只做无等待快照, 不因查询
     * 而执行任何保护或配置动作。 */
    sp_get(&c);
    snap->stop_result       = (rt_uint8_t)c.pwm.result;
    snap->stop_src          = c.pwm.src;
    snap->stop_rc           = (rt_int16_t)c.pwm.raw_rc;
    snap->stop_fail_latched = c.motor_stop_latched ? 1u : 0u;
    if (c.inhibit)
    {
        snap->armed = 0;
        snap->state = MOTOR_FAULT;
    }
    return RT_EOK;
}

subsys_health_t motor_get_health(void) { return mot.health; }

/* ---------- Fix A / A9 + F2b P0-A: EPWM1 ch0 诊断借用门 ----------
 * Motor Service 是该通道唯一生产 owner。F2b 起第一道条件是 **STEP 输出许可**:
 * 审核报告 P0-A 指出的漏洞正是"只查 armed/静止/焊盘 LOW"。在(维修前记录的)U5 与门 +
 * 急停非门损坏情形下, MCU 焊盘 LOW 只能证明 MCU 侧的请求电平, 证明不了 TMC_ENN
 * 已禁能(当时的实测记录是 TMC_ENN 约 0V = 驱动侧仍是"使能"语义), 所以电机一旦接上,
 * pwm_test 输出的 STEP 会真实驱动电机 —— 即使 motor_arm() 被门挡着也一样。
 * > 2026-10-23 S1 更正: "门芯片损坏"不是当前已证实故障(静态电气与现有 SW1 功能已由
 * > 用户确认为 PASS (USER_CONFIRMED)); 本门继续拒绝的原因是**使能链验收仍未完成**
 * > (未烧录新版、未做波形/瞬态验收, 属授权范围), 结论与逻辑都不变。
 * 因此在硬件链验收完成之前, 除非操作员在隔离环境(TMC 模块拔出 / 功率级断开)下
 * 人工确认并置 STEP_DIAG_ISOLATED_ENV_CONFIRMED, 否则任何 STEP 输出一律拒绝。 */
/* 借用门的**只读**部分(任务书 §9): 全部条件都是无副作用判定, supervisor 的
 * runtime_selftest 用它做"能不能借"的体检而不产生预约 —— 只返回 EOK 的判定
 * 本身不能当预约。 */
rt_err_t motor_diag_gate_check(void)
{
    motor_snapshot_t snap;

    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED && !STEP_DIAG_ISOLATED_ENV_CONFIRMED)
    {
        rt_kprintf("[MOT] STEP diag REFUSED: 使能链未验收且未确认隔离环境"
                   " (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=FALSE,"
                   " STEP_DIAG_ISOLATED_ENV_CONFIRMED=FALSE)\n");
        rt_kprintf("[MOT]   使能链未验收期间, DRV_ENABLE 焊盘 LOW 不能证明 TMC_ENN 已禁能"
                   " (历史测量记录过 TMC_ENN 约 0V; 门芯片损坏是**维修前**的解释,"
                   " 当前静态功能已由用户确认 PASS, 见本文件头 2026-10-23 更正),"
                   " 输出 STEP 等同于可能真实驱动电机。\n");
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
    /* 追加(2026-10-23 S1, 合同 4.4): 关断结果不可信期间禁止新的诊断借用 ——
     * 一次没被确认的关断失败之上再 enable, 等于用新动作覆盖未处理的失败。
     * 两个来源分别检查(Motor 侧锁存 / 诊断侧未确认), 只收紧, 不改上面既有条件。
     * S2: Motor 侧改读 canonical, 不用可能陈旧的镜像 bool 代替门禁。 */
    if (sp_motor_stop_latched())
    {
        rt_kprintf("[MOT] grant REFUSED: Motor 侧 STEP 关断失败/未知仍锁存"
                   " (stop=%s rc=%d) - 需 fault_reset 人工恢复\n",
                   mot_stop_result_name(mot.stop_result), (int)mot.stop_rc);
        return -RT_EPERM;
    }
    if (!step_pwm_off_confirmed())
    {
        rt_kprintf("[MOT] grant REFUSED: 诊断侧关断未确认(失败或未取得设备)"
                   " - 不释放借用, 也不允许新的 enable\n");
        return -RT_EPERM;
    }
    return RT_EOK;
}

/* 诊断借用的**预约**: 只读门全通之后再去 stop_priority 取一个真正的 SP_DIAG 身份。
 * 2026-10-23 S2(任务书 §9): 旧版只返回 EOK 的判定不能当预约 —— 没有身份的借用
 * 挡不住"停机之后再 enable"这一类交错。sp_reserve 自己在同一个短临界区里核对
 * inhibit/exhausted/owner/锁存, 因此这里不重复判一遍, 也不在门外补第二次检查。 */
rt_err_t motor_pwm_grant_to_diag(sp_token_t *out)
{
    if (out == RT_NULL) return -RT_EINVAL;
    if (motor_diag_gate_check() != RT_EOK) return -RT_EPERM;

    if (sp_reserve(SP_DIAG, out) != RT_EOK)
    {
        rt_kprintf("[MOT] grant REFUSED: 取不到诊断预约(已抑制/饱和/他人持有"
                   "/未复核的关断失败)\n");
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
    /* 追加(2026-10-23 S1): 关断结果单独一列。state=IDLE / current_hz=0 **不能**
     * 推出"STEP 已关断" —— 只有 result=api-accepted 才叫驱动接受了关断请求。 */
    rt_kprintf("[MOT] STEP stop: result=%s src=%s rc=%d fail_latched=%u"
               " | diag_off_confirmed=%s\n",
               mot_stop_result_name((mot_stop_result_t)snap.stop_result),
               mot_stop_src_name(snap.stop_src), (int)snap.stop_rc,
               (unsigned)snap.stop_fail_latched,
               step_pwm_off_confirmed() ? "YES" : "NO");
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
