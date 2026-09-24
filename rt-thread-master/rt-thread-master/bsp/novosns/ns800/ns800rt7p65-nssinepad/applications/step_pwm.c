/*
 * step_pwm.c - TMC_STEP 脉冲输出控制 (EPWM1_A / PA0 / J4-18)
 *
 * 设备: "epwm1"(drv_epwm.c EPWM_DRV_INIT 命名), channel 0 = EPWMX_A
 * API:  rt_pwm_set(dev, ch, period_ns, pulse_ns) + rt_pwm_enable/disable
 *
 * 安全设计(交接文档 §17.1):
 *   - 上电绝不自动使能输出, 必须 pwm_test 显式命令
 *   - pwm_test 只在 drv_enable 禁止状态下允许运行(电机无供电)
 *   - 频率正确性尚未示波器验收(BUG-008), 命令输出会提示未验收
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdlib.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "motor.h"

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

    /* Phase 7-B: 正式运动服务接管后, 诊断命令仅在 IDLE 态允许 */
    {
        motor_snapshot_t snap;
        if (motor_get_snapshot(&snap) == RT_EOK && snap.state != MOTOR_IDLE)
        {
            rt_kprintf("[STEP] REFUSED: motor active (state=%d), use motor API\n",
                       snap.state);
            return;
        }
    }

    if (rt_strcmp(argv[1], "stop") == 0)
    {
        rt_pwm_disable(step_pwm, STEP_PWM_CH);
        step_enabled = RT_FALSE;
        rt_kprintf("[STEP] output disabled\n");
        return;
    }

    hz = atoi(argv[1]);
    if (hz == 0 || hz > 200000)
    {
        rt_kprintf("[STEP] hz out of range (1~200000)\n");
        return;
    }

    /* 安全检查(BUG-011-3): "不知道是否安全=按不安全处理"。
     * safety_pin 解析失败或读到的不是 LOW, 一律拒绝输出 */
    {
        rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);
        rt_bool_t drv_safe = (en >= 0) && (rt_pin_read(en) == PIN_LOW);

        if (!drv_safe)
        {
            rt_kprintf("[STEP] REFUSED: MCU_DRV_ENABLE unknown or not LOW (pin=%d)\n",
                       (int)en);
            return;
        }
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
}
MSH_CMD_EXPORT(pwm_test, control STEP pulse output: pwm_test <hz>|stop);

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
