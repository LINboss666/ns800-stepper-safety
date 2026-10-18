/*
 * step_pwm.c - TMC_STEP 脉冲输出控制 (EPWM1_A / PA0 / J4-18)
 *
 * 设备: "epwm1"(drv_epwm.c EPWM_DRV_INIT 命名), channel 0 = EPWMX_A
 * API:  rt_pwm_set(dev, ch, period_ns, pulse_ns) + rt_pwm_enable/disable
 *
 * 安全设计(交接文档 §17.1 + Fix A + F2b P0-A):
 *   - 上电绝不自动使能输出, 必须 pwm_test 显式命令
 *   - EPWM1 ch0 的生产 owner 是 Motor Service; pwm_test 只是诊断借用, 必须先
 *     过 motor_pwm_grant_to_diag() 借用门, 否则拒绝。该门 F2b 起有两层:
 *     ① STEP 输出许可 —— MOTOR_HARDWARE_ENABLE_PATH_VALIDATED 或
 *        STEP_DIAG_ISOLATED_ENV_CONFIRMED 至少一个为 TRUE。现板两个都是 FALSE:
 *        与门/非门损坏使"DRV_ENABLE 焊盘 LOW"无法证明 TMC_ENN 已禁能, 所以只要
 *        接着电机, STEP 输出就可能真实驱动它 —— 光靠"电机未 armed"挡不住。
 *     ② 所有权 —— 未 armed + current_hz==0 + target_hz==0 + state==IDLE +
 *        DRV_ENABLE 焊盘实测 LOW(不允许与 Motor 斜坡线程并发写同一通道)。
 *   - pwm_test stop 永远允许(朝安全方向)
 *   - 频率正确性尚未示波器验收(BUG-008); 要量波形需先在 TMC 模块拔出的隔离环境
 *     下人工确认 STEP_DIAG_ISOLATED_ENV_CONFIRMED
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdlib.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "motor.h"
#include "step_pwm.h"

#define STEP_PWM_DEV    EPWM_STEP_DEV_NAME    /* "epwm1" */
#define STEP_PWM_CH     0                     /* EPWMX_A = PA0 */

static struct rt_device_pwm *step_pwm = RT_NULL;
static rt_bool_t step_enabled = RT_FALSE;

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

    if (step_find() != RT_EOK) return;

    /* stop 永远允许: 朝安全方向的动作不受所有权门限制 */
    if (rt_strcmp(argv[1], "stop") == 0)
    {
        rt_pwm_disable(step_pwm, STEP_PWM_CH);
        step_enabled = RT_FALSE;
        rt_kprintf("[STEP] output disabled (diagnostic ownership released)\n");
        return;
    }

    /* F2b P0-A: STEP 输出许可 + EPWM1 ch0 借用门(两层, 见文件头)。
     * 拒绝原因由 grant 函数自己打印(它会说明是许可还是所有权挡住了)。 */
    if (motor_pwm_grant_to_diag() != RT_EOK)
    {
        rt_kprintf("[STEP] REFUSED: STEP 输出未获许可(使能链未验收/未确认隔离环境)"
                   " 或 EPWM1 ch0 仍由 Motor 占用。改用 motor_* 正式 API。\n");
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

/* Fix A: 诊断输出是否正占用 EPWM1 ch0(Motor Service 的互斥门) */
rt_bool_t step_pwm_output_active(void)
{
    return step_enabled ? RT_TRUE : RT_FALSE;
}

void step_pwm_force_stop(void)
{
    if (step_pwm != RT_NULL)
    {
        rt_pwm_disable(step_pwm, STEP_PWM_CH);
    }
    step_enabled = RT_FALSE;   /* 设备未找到也清状态; 幂等可重入 */
}

static void step_status(void)
{
    rt_kprintf("[STEP] dev=%s ch=%d enabled=%s\n", STEP_PWM_DEV, STEP_PWM_CH,
               step_enabled ? "YES(输出中!)" : "no(安全)");
}
MSH_CMD_EXPORT(step_status, show STEP output state);
