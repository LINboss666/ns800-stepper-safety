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
#include "step_pwm.h"

#define STEP_PWM_DEV    EPWM_STEP_DEV_NAME    /* "epwm1" */
#define STEP_PWM_CH     0                     /* EPWMX_A = PA0 */

static struct rt_device_pwm *step_pwm = RT_NULL;
static rt_bool_t step_enabled = RT_FALSE;
/* 2026-10-23 S1: 诊断侧关断结果单独记账, 不与许可/反馈混为一谈。
 * step_stop_latched 在任一关断失败或未取得设备时**置位并保持**: 一次后续成功的关断
 * 只更新"当前关断结果", 不解除锁存(合同 4.4 —— 一次重试成功不得绕过人工恢复门)。
 * 解除只由 step_pwm_clear_stop_latch() 执行, 其唯一合法调用点是 fault_reset() 的
 * 人工恢复复核。它也不清 Safety 故障锁存(那条路同样只走 fault_reset)。 */
static mot_stop_result_t step_stop_result = MOT_STOP_NOT_ATTEMPTED;
static rt_err_t          step_stop_rc      = RT_EOK;
static rt_bool_t         step_stop_latched = RT_FALSE;

/* 唯一的 checked 关断路径: pwm_test stop 与 step_pwm_force_stop 都走这里。
 * 三种情形必须分开(合同 4.3 "对未持有/设备未取得的情况明确区分"):
 *   1) 本模块没有持有输出(从未 enable, 或上一次关断已被接受) => 无事可做: 返回 EOK,
 *      **不**记成一次硬件关断成功, 也**不**新置失败锁存; 已有的失败锁存原样保留。
 *   2) 持有输出但拿不到设备句柄 => "未尝试, 输出状态未知" + 锁存(真异常)。
 *   3) 持有输出且 rt_pwm_disable 失败 => "输出状态未知" + 锁存 + 不释放所有权。
 * 失败时**不**释放所有权(step_enabled 保持 TRUE), 也不假装输出已关闭。 */
static rt_err_t step_pwm_disable_checked(rt_uint8_t src)
{
    rt_err_t e;

    if (!step_enabled)
    {
        /* 本模块没有持有输出: 没有需要关断的诊断输出。这**不是**一次硬件关断证明,
         * 也不新置失败锁存; 已有的锁存原样保留(只能由人工恢复复核清除)。 */
        if (step_stop_latched && step_stop_rc != RT_EOK)
        {
            rt_kprintf("[STEP] 未持有输出, 但最近一次关断仍失败(rc=%d src=%s):"
                       " 不谎报成功, 锁存保持待人工恢复\n",
                       (int)step_stop_rc, mot_stop_src_name(src));
            return step_stop_rc;
        }
        rt_kprintf("[STEP] 未持有输出(%s), 本次无需关断动作 - 不构成一次硬件关断证明;"
                   " 失败锁存=%s\n",
                   step_stop_result == MOT_STOP_NOT_ATTEMPTED ? "从未占用该通道"
                                                              : "上次关断已被接受",
                   step_stop_latched ? "仍置位, 待 fault_reset 人工恢复" : "未置位");
        return RT_EOK;
    }

    if (step_pwm == RT_NULL)
    {
        step_stop_result  = MOT_STOP_DEV_UNAVAILABLE;
        step_stop_rc      = -RT_ENOSYS;
        step_stop_latched = RT_TRUE;
        rt_kprintf("[STEP] !! 持有输出却未取得 %s 设备句柄: 关断**未尝试**(src=%s),"
                   " 状态=未知 (不称'已停止', 也不宣布所有权已交回)\n",
                   STEP_PWM_DEV, mot_stop_src_name(src));
        return -RT_ENOSYS;
    }

    e = rt_pwm_disable(step_pwm, STEP_PWM_CH);
    step_stop_rc = e;
    if (e == RT_EOK)
    {
        step_stop_result = MOT_STOP_API_ACCEPTED;
        step_enabled     = RT_FALSE;    /* 只有 API 接受关断后才交回所有权 */
        /* 注意: 这里**不**清 step_stop_latched —— 一次后续成功只更新"当前关断结果",
         * 解除锁存只能由 fault_reset() 的人工恢复复核做(合同 4.4)。 */
        rt_kprintf("[STEP] disable accepted by driver (src=%s) - 只称驱动接受了关断"
                   "请求, 不等于示波器证明无脉冲\n", mot_stop_src_name(src));
    }
    else
    {
        step_stop_result  = MOT_STOP_API_FAILED;
        step_stop_latched = RT_TRUE;
        rt_kprintf("[STEP] !! rt_pwm_disable FAILED (rc=%d src=%s): 输出状态未知,"
                   " 诊断所有权**不释放**, motor 接管与新诊断输出都会被拒,"
                   " 直到 fault_reset 人工恢复复核通过\n",
                   (int)e, mot_stop_src_name(src));
    }
    return e;
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
    rt_err_t e;

    if (argc != 2)
    {
        rt_kprintf("usage: pwm_test <hz>   e.g. pwm_test 1000\n");
        rt_kprintf("       pwm_test stop   (stop output)\n");
        rt_kprintf("NOTE: STEP 频率未示波器验收(BUG-008), 禁止接电机依赖此频率\n");
        return;
    }

    /* 2026-10-23 S1: stop 必须先于"设备是否找得到"被处理 —— 找不到句柄也要如实
     * 报"未尝试成功/状态未知", 而不是静默 return 让操作员以为已经停了。
     * 走与统一停机**同一条** checked 路径, 不另写一份关断代码。 */
    if (rt_strcmp(argv[1], "stop") == 0)
    {
        rt_err_t se = step_pwm_disable_checked(MOT_STOP_SRC_DIAG_TEST);

        if (se != RT_EOK)
        {
            rt_kprintf("[STEP] stop NOT confirmed (rc=%d): 不宣称输出已关闭,"
                       " 诊断所有权未交回 - 交给既有软件故障链\n", (int)se);
            /* 2026-10-23 返修 R03(=S1-NEW-03): 操作员在 msh 里敲 `pwm_test stop`
             * 失败时也要把故障交给**既有**链(投一次 EVT_SOFT_FAULT -> Safety 线程消费
             * -> safety_force_shutdown 统一停机 + FAULT_LATCHED + 黑匣子), 否则底层
             * 锁存住却只在"已锁存的故障"上才有恢复入口, fault_reset 会直接回
             * "not latched(state=READY)", 人工恢复不可达。
             * ⚠ 只有这个**外部命令入口**投事件: 统一停机用的 step_pwm_force_stop()
             *   绝不投, 否则会形成"消费事件 -> 停机再次失败 -> 再投同事件"的自激。 */
            (void)safety_post_event(EVT_SOFT_FAULT);
        }
        return;
    }

    if (step_find() != RT_EOK) return;

    /* F2b P0-A: STEP 输出许可 + EPWM1 ch0 借用门(两层, 见文件头)。
     * 拒绝原因由 grant 函数自己打印(它会说明是许可还是所有权挡住了)。 */
    if (motor_pwm_grant_to_diag() != RT_EOK)
    {
        rt_kprintf("[STEP] REFUSED: STEP 输出未获许可(使能链未验收/未确认隔离环境)"
                   " 或 EPWM1 ch0 仍由 Motor 占用。改用 motor_* 正式 API。\n");
        return;
    }

    /* 追加(2026-10-23 S1, 合同 4.3): 诊断侧关断曾失败且未经人工恢复复核 => 不允许
     * 再执行一次 enable 去覆盖那个未处理的失败(stop 方向永远仍然允许)。 */
    if (step_stop_latched)
    {
        rt_kprintf("[STEP] REFUSED: 本模块仍带着一次**未确认的关断**"
                   " (last=%s rc=%d) - 不用新的 enable 覆盖它, 需 fault_reset 复核\n",
                   mot_stop_result_name(step_stop_result), (int)step_stop_rc);
        return;
    }

    hz = atoi(argv[1]);
    if (hz == 0 || hz > 200000)
    {
        rt_kprintf("[STEP] hz out of range (1~200000)\n");
        return;
    }

    period_ns = 1000000000u / hz;
    /* 占空比固定 50%, STEP 脉冲 TMC2209 只看边沿 */
    e = rt_pwm_set(step_pwm, STEP_PWM_CH, period_ns, period_ns / 2);
    if (e != RT_EOK)
    {
        rt_kprintf("[STEP] pwm_set failed: %d\n", e);
        return;
    }
    e = rt_pwm_enable(step_pwm, STEP_PWM_CH);
    if (e != RT_EOK)
    {
        rt_kprintf("[STEP] pwm_enable failed: %d\n", e);
        return;
    }
    step_enabled = RT_TRUE;
    rt_kprintf("[STEP] output %u Hz (period %u ns) - UNVERIFIED, 需示波器确认\n",
               hz, period_ns);
    rt_kprintf("[STEP] motor_start/motor_arm are blocked while this is on;"
               " run 'pwm_test stop' to release\n");
}
MSH_CMD_EXPORT(pwm_test, control STEP pulse output: pwm_test hz or stop);

/* Fix A + S1: 诊断输出是否仍占用(或**未确认可交回**)EPWM1 ch0。
 * 保守判据: 仍启用 或 关断失败/未取得设备 ⇒ TRUE, Motor Service 不得接管。 */
rt_bool_t step_pwm_output_active(void)
{
    if (step_enabled) return RT_TRUE;
    if (step_stop_latched) return RT_TRUE;
    return RT_FALSE;
}

void step_pwm_get_stop_state(mot_stop_result_t *out_result, rt_err_t *out_rc,
                             rt_bool_t *out_latched)
{
    if (out_result)  *out_result  = step_stop_result;
    if (out_rc)      *out_rc      = step_stop_rc;
    if (out_latched) *out_latched = step_stop_latched;
}

/* 诊断通道是否已经有**有效**的关断结果:
 *   - 从未启用、也从未尝试关断 => TRUE(该通道没被诊断占用过, 不是"虚构的一次成功关断");
 *   - 尝试过关断 => 只有最近一次 API 接受、当前未持有输出、且没有未复核的失败锁存
 *     才算 TRUE。失败锁存期间即使后来又成功一次, 也仍然是 FALSE(合同 4.4)。 */
rt_bool_t step_pwm_off_confirmed(void)
{
    if (step_stop_latched) return RT_FALSE;
    if (step_stop_result == MOT_STOP_NOT_ATTEMPTED)
        return step_enabled ? RT_FALSE : RT_TRUE;
    return (step_stop_result == MOT_STOP_API_ACCEPTED && !step_enabled)
               ? RT_TRUE : RT_FALSE;
}

/* ⚠ 调用方审计(合同 4.4): 唯一合法调用点 = safety_state.c 的 fault_reset(),
 * 且只在全部人工恢复复核成立之后。不导出 MSH 命令, 没有第二条恢复路径。 */
void step_pwm_clear_stop_latch(void)
{
    step_stop_latched = RT_FALSE;
}

rt_err_t step_pwm_force_stop(void)
{
    return step_pwm_disable_checked(MOT_STOP_SRC_DIAG_FORCE);
}

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
