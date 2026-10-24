/*
 * step_pwm.c - TMC_STEP 脉冲输出控制 (EPWM1_A / PA0 / J4-18)
 *
 * 设备: "epwm1"(drv_epwm.c EPWM_DRV_INIT 命名), channel 0 = EPWMX_A
 * API:  rt_pwm_set(dev, ch, period_ns, pulse_ns) + rt_pwm_enable/disable
 *
 * 安全设计(交接文档 §17.1 + Fix A + F2b P0-A):
 *   - 上电绝不自动使能输出, 必须 pwm_test 显式命令
 *   > **2026-10-23 措辞更正(S1)**: 下面两处"与门/非门损坏""焊盘 LOW 证明不了已禁能"
 *   > 是**维修前的历史解释**。当前记录是: 门电路更换后**静态电气与现有 SW1 急停功能已由
 *   > 用户自行检查确认为 PASS (USER_CONFIRMED)**(草案 A29), 且 IO87↔IO70 跟随与 SW1 同窗口
 *   > 阻断已有数字记录(`4edcf5e`/`f5f7cbf`)。STEP 诊断许可宏仍为 FALSE 的原因是**授权范围**
 *   > (未烧录新版、未做瞬态/波形/时延验收), 不是"已证实门芯片坏"。借用门的逻辑本身不变。
 *   - EPWM1 ch0 的生产 owner 是 Motor Service; pwm_test 只是诊断借用, 必须先
 *     过 motor_pwm_grant_to_diag() 借用门, 否则拒绝。该门 F2b 起有两层:
 *     ① STEP 输出许可 —— MOTOR_HARDWARE_ENABLE_PATH_VALIDATED 或
 *        STEP_DIAG_ISOLATED_ENV_CONFIRMED 至少一个为 TRUE。目标两者都是 FALSE:
 *        在没有示波器波形与隔离环境人工确认之前, STEP 输出一旦真实接到驱动器就可能
 *        驱动电机 —— 光靠"电机未 armed"挡不住(与门/非门的历史说法见上面更正)。
 *     ② 所有权 —— 未 armed + current_hz==0 + target_hz==0 + state==IDLE +
 *        DRV_ENABLE 焊盘实测 LOW(不允许与 Motor 斜坡线程并发写同一通道)。
 *   - pwm_test stop 永远允许(朝安全方向)
 *   - S1(2026-10-23, 关断结果可信): 诊断侧只有**一条** checked 关断路径
 *     step_pwm_disable_checked(), pwm_test stop 与 step_pwm_force_stop() 共用;
 *     rt_pwm_disable 失败或未取得设备句柄 => 结果记为"输出状态未知"并**锁存**:
 *     ① 不释放诊断所有权(step_enabled 保持), step_pwm_output_active() 保守返回 TRUE
 *        挡住 Motor 接管; ② 不允许再 enable 覆盖未处理的失败; ③ step_status 不再把
 *        "未持有输出"写成"no(安全)"; ④ 一次后续成功只更新当前结果, 锁存只能由
 *        fault_reset() 的人工恢复复核解除(step_pwm_clear_stop_latch)。
 *     返回 RT_EOK 只称"驱动 API 接受了关断请求", 不是示波器证明无脉冲。
 *   > **2026-10-23 S2 更新(停机优先协调层, phase8-stop-priority-s2)**: 上面这段的
 *   > **语义合同全部保留**, 但实现位置变了 —— 关断结果、所有权与失败锁存的 canonical
 *   > 记账搬到 stop_priority, 因为 Motor 与诊断共用同一个 epwm1 时间基, 两处各记一份
 *   > 会互相看不见(诊断"从未占用"与 Motor"关断失败"在同一次停机里必须同代)。替换:
 *   >   · step_pwm_force_stop() **删除** —— 它做的正是统一停机里的"第二次 disable";
 *   >     统一停机与人工恢复复核的诊断侧改用 step_pwm_reconcile_after_trip()
 *   >     (只把 canonical 结果抄进本模块镜像, 不碰硬件、不投事件);
 *   >   · step_pwm_clear_stop_latch() **删除** —— 无身份的公共清除口等于旁路;
 *   >     锁存改由 fault_reset 的**最终提交窗口**内 step_pwm_recovery_fields_held()
 *   >     与 sp_finish_recovery_held() 一起解除(同一代票据成立才提交);
 *   >   · 新的开启流程: 先校验 hz → 与协调层绑定的句柄/通道做一致性核对 →
 *   >     motor_pwm_grant_to_diag(&token)(借用门 + **真正的 SP_DIAG 预约**) →
 *   >     门外 ns800_epwm_prepare() → **同一个 token 窗口内**提交寄存器并启动;
 *   >     失败出口归还仍有效且未运行的本次预约(旧 token 撤销不了新 owner);
 *   >   · pwm_test stop 失败仍只通知一次(不自激), 且失败分支不再补第二次关闭。
 *   - 频率正确性尚未示波器验收(BUG-008); 要量波形需先在 TMC 模块拔出的隔离环境
 *     下人工确认 STEP_DIAG_ISOLATED_ENV_CONFIRMED
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdlib.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "safety_state.h"      /* 返修 R03: pwm_test stop 失败时投递既有软件故障事件 */
#include "motor.h"
#include "stop_priority.h"
#include "drv_epwm.h"          /* ns800_epwm_prepare / commit_prepared_held(门外准备 + 门内提交) */
#include "step_pwm.h"

#define STEP_PWM_DEV    EPWM_STEP_DEV_NAME    /* "epwm1" */
#define STEP_PWM_CH     0                     /* EPWMX_A = PA0 */

static struct rt_device_pwm *step_pwm = RT_NULL;
static rt_bool_t step_enabled = RT_FALSE;
/* 2026-10-23 S2: 诊断侧的关断结果、所有权与失败锁存的**canonical** 记账搬进了
 * stop_priority（Motor 与诊断共用同一个 epwm1 时间基，两路各自记一份会互相看不见）。
 * 本文件的 step_stop_result/rc/latched 退化为**镜像**，只由 sp 的视图刷新，不再由
 * 本地直接下结论；对外 API(step_pwm_output_active / off_confirmed / get_stop_state)
 * 的语义与 S1 合同保持一致：
 *   ① 失败/未知不释放所有权，一次后续成功不清历史锁存(合同 4.4)；
 *   ② "诊断从未占用该通道"仍是 NOT_ATTEMPTED，不伪造一次 API 调用；
 *   ③ 历史锁存只能由 fault_reset 的最终提交解除(经 sp)，没有 MSH 旁路。 */
static mot_stop_result_t step_stop_result = MOT_STOP_NOT_ATTEMPTED;
static rt_err_t          step_stop_rc      = RT_EOK;
static rt_bool_t         step_stop_latched = RT_FALSE;

/* 把 canonical 的诊断视图抄进本模块镜像。调用方需保证不与其他镜像写并发
 * (诊断路径只有 msh 线程, 且 sp 内部用自己的短窗口取快照)。 */
static void step_stop_mirror(void)
{
    mot_stop_result_t res;
    rt_err_t          rc;
    rt_bool_t         lat;

    sp_view_diag(&res, &rc, &lat);
    step_stop_result = res;
    step_stop_rc     = rc;
    step_stop_latched = lat;
}

/* 诊断侧**当前**是否真的持有/欠着这个通道：正在由诊断 owner 持有，或带着一次
 * 未复核的关断失败。两种都不是时，pwm_test stop 不该动共享模块（否则会把一次
 * 新的 Motor 预约凭空打断，违反任务书 §4.3）。 */
static rt_bool_t step_pwm_diag_holds(void)
{
    return (sp_owner_is(SP_DIAG) || sp_diag_stop_latched()) ? RT_TRUE : RT_FALSE;
}

/* 唯一的 checked 关断路径: pwm_test stop 与统一停机的诊断整理都走这里。
 * 2026-10-23 S2 的变化与不变之处:
 *   - 实际 rt_pwm_disable 与结果登记在**同一个短临界区**里由 sp 完成, 且失败时
 *     同窗追加抑制/换 epoch/许可 LOW, **不做第二次 disable**(S1-R1 R04 合同不变);
 *   - 三种情形仍然分开(合同 4.3):
 *     1) 诊断既没持有也没有未复核的失败 => 无事可做: 返回 EOK, 不记成一次硬件
 *        关断成功, 不新置失败锁存, 也不碰 Motor 的预约;
 *     2) 持有输出但驱动侧未取得设备句柄 => sp 记 DEV_UNAVAILABLE/未尝试 + 锁存,
 *        绝不对 NULL 句柄调用 API;
 *     3) 持有输出且 rt_pwm_disable 失败 => "输出状态未知" + 锁存 + 不释放所有权。
 *   - 成功时在同一短提交里写许可 LOW 并撤销诊断预约(换 epoch) —— 与 disarm 同形。
 * ⚠ 本函数**从不投递事件**(S1 返修 R03 的自激禁止仍成立), 也从不"另关一次":
 *   统一停机与人工恢复复核的诊断侧整理只走 step_pwm_shadow_from_proof_held()。 */
static rt_err_t step_pwm_disable_checked(rt_uint32_t op_epoch, rt_uint8_t src,
                                         sp_proof_t *pf)
{
    rt_err_t e;

    if (pf != RT_NULL) rt_memset(pf, 0, sizeof(*pf));
    if (!step_pwm_diag_holds())
    {
        step_stop_mirror();
        if (step_stop_latched && step_stop_rc != RT_EOK)
        {
            rt_kprintf("[STEP] 未持有输出, 但最近一次关断仍失败(rc=%d src=%s):"
                       " 不谎报成功, 锁存保持待人工恢复\n",
                       (int)step_stop_rc, mot_stop_src_name(src));
            return step_stop_rc;
        }
        rt_kprintf("[STEP] 未持有输出(%s), 本次无需关断动作 - 不构成一次硬件关断证明;"
                   " 失败锁存=%s; 不去动 Motor 的预约\n",
                   step_stop_result == MOT_STOP_NOT_ATTEMPTED ? "诊断从未占用该通道"
                                                              : "上次关断已被接受",
                   step_stop_latched ? "仍置位, 待 fault_reset 人工恢复" : "未置位");
        return RT_EOK;
    }

    /* CP4 K3(§5.1): 带**本次 stop 命令入口**采到的代次身份。身份已被更新的一代取代时
     * sp 在一个寄存器写都不发的情况下返回 -RT_EINTR —— 迟到的旧 stop 不再停掉新 owner
     * 的输出, 也不清它的 owner/影子/pending。 */
    e = sp_off_attempt_for(op_epoch, SP_DIAG, src, FAULT_SOFT, EVT_SOFT_FAULT, 1, pf);
    step_stop_mirror();
    if (e == RT_EOK)
    {
        step_enabled = RT_FALSE;          /* 只有 canonical 关断被接受才交回所有权 */
        rt_kprintf("[STEP] disable accepted by driver (src=%s) - 只称驱动接受了关断"
                   "请求, 不等于示波器证明无脉冲\n", mot_stop_src_name(src));
    }
    else
    {
        rt_kprintf("[STEP] !! 关断未确认 (rc=%d src=%s): 输出状态未知,"
                   " 诊断所有权**不释放**, 已在同一短临界区内追加抑制并请求许可 LOW,"
                   " motor 接管与新诊断输出都会被拒, 直到 fault_reset 人工恢复复核通过\n",
                   (int)e, mot_stop_src_name(src));
    }
    return e;
}

/* R1: 慢整理/恢复提交窗口内的诊断镜像写(调用方已持短临界区, 本函数不开窗口、
 * 不取锁、不打印、不碰硬件)。 */
void step_pwm_shadow_from_proof_held(const sp_proof_t *pf)
{
    if (pf == RT_NULL || !pf->valid) return;
    sp_view_diag_held(&step_stop_result, &step_stop_rc, &step_stop_latched);
    if (!sp_diag_output_active_held()) step_enabled = RT_FALSE;
}

/* 人工恢复最终提交窗内的诊断字段复位(调用方已持短临界区, 不取锁、不打印、不碰硬件)。
 * 本次复核的那一次真实关断结果由 sp 在同一窗口内一并规范化。 */
void step_pwm_recovery_fields_held(void)
{
    step_enabled        = RT_FALSE;
    step_stop_result    = MOT_STOP_NOT_ATTEMPTED;
    step_stop_rc        = RT_EOK;
    step_stop_latched   = RT_FALSE;
}

static rt_err_t step_find(void)
{
    if (step_pwm != RT_NULL) return RT_EOK;
    step_pwm = (struct rt_device_pwm *)rt_device_find(STEP_PWM_DEV);
    if (step_pwm == RT_NULL)
    {
        rt_kprintf("[STEP] pwm device %s not found (check BSP_USING_EPWM1)\n",
                   STEP_PWM_DEV);
        return -RT_ERROR;
    }
    return RT_EOK;
}

static void pwm_test(int argc, char **argv)
{
    rt_uint32_t hz;
    rt_uint32_t period_ns;
    struct ns800_epwm_prepared prepared;
    sp_token_t  tok;
    rt_base_t   lvl;
    rt_err_t    e;

    if (argc != 2)
    {
        rt_kprintf("usage: pwm_test <hz>   e.g. pwm_test 1000\n");
        rt_kprintf("       pwm_test stop   (stop output)\n");
        rt_kprintf("NOTE: STEP 频率未示波器验收(BUG-008), 禁止接电机依赖此频率\n");
        return;
    }

    /* 2026-10-23 S1: stop 必须先于"设备是否找得到"被处理 —— 找不到句柄也要如实
     * 报"未尝试成功/状态未知", 而不是静默 return 让操作员以为已经停了。
     * 走与统一停机**同一条** checked 关断路径, 不另写一份关断代码。 */
    if (rt_strcmp(argv[1], "stop") == 0)
    {
        sp_proof_t spf;
        rt_uint32_t op_epoch = sp_epoch();     /* 本命令的原身份(第一次动作之前) */
        rt_err_t    se = step_pwm_disable_checked(op_epoch, MOT_STOP_SRC_DIAG_TEST, &spf);

        if (se != RT_EOK)
        {
            rt_kprintf("[STEP] stop NOT confirmed (rc=%d): 不宣称输出已关闭,"
                       " 诊断所有权未交回 - 交给既有软件故障链\n", (int)se);
            /* 2026-10-23 返修 R03(=S1-NEW-03): 操作员在 msh 里敲 `pwm_test stop`
             * 失败时也要把故障交给**既有**链, 否则底层锁存住却只在"已锁存的故障"上
             * 才有恢复入口, fault_reset 会直接回 "not latched(state=READY)"。
             * CP4 K3(§5.1/§5.2)改了交接方式: 这一次关断失败**已经在同一个短临界区**
             * 内建立了抑制/换 epoch/留 pending, 所以这里只**通知**(带那次动作的代次),
             * 不再 safety_post_event() —— 后者等于同一波里再来一次真实关断。
             * ⚠ 身份失配(-RT_EINTR, 迟到的旧 stop)同样只通知/只如实报告, 不动新 owner。 */
            if (spf.valid) (void)safety_notify_trip_done(EVT_SOFT_FAULT, spf.epoch);
            else           (void)safety_notify_trip_done(EVT_SOFT_FAULT, op_epoch);
        }
        return;
    }

    /* 任务书 §9: **先校验 hz, 后预约** —— 参数错误不该留下一次半途预约。 */
    hz = atoi(argv[1]);
    if (hz == 0 || hz > 200000)
    {
        rt_kprintf("[STEP] hz out of range (1~200000)\n");
        return;
    }

    if (step_find() != RT_EOK) return;

    /* 诊断借用必须与 Motor 用**同一个** epwm1 实例与同一通道(任务书 §4.2):
     * 时间基是整个模块共享的, 各自假定另一路设备会让"冻结"冻错对象。 */
    {
        struct rt_device_pwm *bound = RT_NULL;
        int                   bch   = -1;

        sp_get_bound(&bound, &bch, RT_NULL);
        if (bound != step_pwm || bch != STEP_PWM_CH)
        {
            rt_kprintf("[STEP] REFUSED: 诊断句柄与协调层绑定的实例不一致"
                       " (diag=%p ch=%d / bound=%p ch=%d) - 时间基是共享的, 不能各关各的\n",
                       (void *)step_pwm, STEP_PWM_CH, (void *)bound, bch);
            return;
        }
    }

    /* F2b P0-A: STEP 输出许可 + EPWM1 ch0 借用门(两层, 见文件头)。
     * 2026-10-23 S2: 这道门现在**返回真正的预约身份**(sp_reserve(SP_DIAG)),
     * 只返回 EOK 的旧只读检查不能当预约(任务书 §9)。
     * 拒绝原因由 grant 函数自己打印(它会说明是许可、反馈还是协调状态挡住了)。 */
    if (motor_pwm_grant_to_diag(&tok) != RT_EOK)
    {
        rt_kprintf("[STEP] REFUSED: STEP 输出未获许可(使能链未验收/未确认隔离环境)"
                   " 或 EPWM1 ch0 仍由 Motor 占用。改用 motor_* 正式 API。\n");
        return;
    }

    /* 换算在**门外**做(浮点/除法不进短临界区), 门内只有固定整数寄存器写。 */
    period_ns = 1000000000u / hz;
    if (ns800_epwm_prepare(step_pwm, (rt_uint32_t)STEP_PWM_CH,
                           period_ns, period_ns / 2, &prepared) != RT_EOK)
    {
        rt_kprintf("[STEP] prepare failed (hz=%u) - 未预约生效的输出未改变\n", hz);
        (void)sp_release(&tok);
        return;
    }

    /* 资格判定 + 寄存器提交 + 启动在**同一个窗口**内(任务书 §4.4): 禁止"门内检查、
     * 门外写", 否则停机可以插在两者之间而旧流程照样把时间基拉起来。 */
    if (sp_enter_token(&tok, &lvl) != RT_EOK)
    {
        rt_kprintf("[STEP] REFUSED: 预约在提交前已失效(新故障或新 owner)\n");
        return;
    }
    e = ns800_epwm_commit_prepared_held(step_pwm, &prepared);
    if (e == RT_EOK) e = sp_pwm_start_held(&tok);
    sp_leave_token(lvl);
    if (e != RT_EOK)
    {
        /* 提交/启动失败: 该窗口内没有任何输出被启动。身份仍有效就把预约交回去,
         * 失配(新故障已撤销)就不做第二次关断, 也不去动新 owner。 */
        if (sp_release(&tok) == RT_EOK)
            rt_kprintf("[STEP] 预约已归还 (未产生输出)\n");
        rt_kprintf("[STEP] commit/enable failed: %d - 不记录'输出已启动'\n", (int)e);
        return;
    }

    step_enabled      = RT_TRUE;
    step_stop_result  = MOT_STOP_OUTPUT_REQUESTED;
    step_stop_rc      = RT_EOK;
    rt_kprintf("[STEP] output %u Hz (period %u ns) - UNVERIFIED, 需示波器确认\n",
               hz, period_ns);
    rt_kprintf("[STEP] motor_start/motor_arm are blocked while this is on;"
               " run 'pwm_test stop' to release\n");
}
MSH_CMD_EXPORT(pwm_test, control STEP pulse output: pwm_test hz or stop);

/* Fix A + S1: 诊断输出是否仍占用(或**未确认可交回**)EPWM1 ch0。
 * 保守判据(任务书 §9): RESERVED / RUNNING / HELD_UNKNOWN / 历史失败锁存都阻止接管;
 * 判定全部取自 canonical(sp), 本地镜像不参与门。 */
rt_bool_t step_pwm_output_active(void)
{
    return sp_diag_output_active();
}

void step_pwm_get_stop_state(mot_stop_result_t *out_result, rt_err_t *out_rc,
                             rt_bool_t *out_latched)
{
    rt_bool_t lat;
    mot_stop_result_t res;
    rt_err_t  rc;

    sp_view_diag(&res, &rc, &lat);
    if (out_result)  *out_result  = res;
    if (out_rc)      *out_rc      = rc;
    if (out_latched) *out_latched = lat;
}

/* 诊断通道是否已经有**有效**的关断结果(从未占用该通道算有效, 不虚构一次成功;
 * 历史失败锁存期间即使后来又成功一次也仍然无效)。判定全在 canonical 里。 */
rt_bool_t step_pwm_off_confirmed(void)
{
    return sp_diag_off_confirmed();
}

/* 统一停机/人工恢复复核的诊断侧整理与恢复窗口内的字段复位见上面
 * step_pwm_reconcile_after_trip() / step_pwm_recovery_fields_held(); 原
 * step_pwm_force_stop() 已删除 —— 它做的正是那"第二次 disable"。 */

static void step_status(void)
{
    const char *state;

    if (step_enabled)                       state = "YES(输出中!)";
    else if (step_stop_latched)
        /* 关断失败过且未经人工恢复: 即便最近一次成功也不能写成"no(安全)" */
                                            state = "UNKNOWN(曾有失败, 待 fault_reset)";
    else if (step_stop_result == MOT_STOP_API_ACCEPTED)
                                            state = "no(驱动 API 接受关断; 未做波形验证)";
    else if (step_stop_result == MOT_STOP_API_FAILED)
                                            state = "UNKNOWN(关断失败, 所有权未释放!)";
    else if (step_stop_result == MOT_STOP_DEV_UNAVAILABLE)
                                            state = "UNKNOWN(未取得设备句柄, 未确认关断)";
    else                                    state = "no(诊断从未占用该通道)";

    rt_kprintf("[STEP] dev=%s ch=%d enabled=%s last_stop=%s stop_rc=%d\n",
               STEP_PWM_DEV, STEP_PWM_CH, state,
               mot_stop_result_name(step_stop_result), (int)step_stop_rc);
    rt_kprintf("[STEP] 说明: '驱动 API 接受关断' != 示波器证明无脉冲(BUG-008 未验收);"
               " 未持有诊断所有权也不代表整个通道物理无 STEP\n");
}
MSH_CMD_EXPORT(step_status, show STEP output state);
