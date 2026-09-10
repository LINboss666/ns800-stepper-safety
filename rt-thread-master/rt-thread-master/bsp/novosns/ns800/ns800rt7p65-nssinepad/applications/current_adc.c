/*
 * current_adc.c - 母线电流采样 (ADCA CH15 / PH2 / J1-3)
 *
 * 前端(扩展板方案 §7.4): 30mΩ 分流 + NSCSA240A1(20V/V) + 1.65V 中点
 *   Vadc ≈ 1.65V + Ibus × 0.03Ω × 20  →  Ibus(mA) ≈ (Vadc_mV - 1650) / 0.6
 *
 * 状态: 代码就绪. 当前采样前端未接线, 读数为浮空噪声, 如实打印原始值.
 *       标定(offset/gain)待硬件到位后做, 理论换算仅供参考.
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"

#define CUR_ADC_DEV     CURRENT_ADC_DEVICE_NAME   /* "adc0" */
#define CUR_ADC_CH      CURRENT_ADC_CHANNEL       /* 15 */
#define CUR_SAMPLES     64
#define CUR_VREF_MV     3300
#define CUR_RAW_MAX     4095
#define CUR_ZERO_MV     1650.0f
#define CUR_V_PER_A     0.6f                      /* 30mΩ × 20V/V */

static rt_adc_device_t cur_adc = RT_NULL;
static rt_bool_t cur_enabled = RT_FALSE;

static int current_adc_init(void)
{
    cur_adc = (rt_adc_device_t)rt_device_find(CUR_ADC_DEV);
    if (cur_adc == RT_NULL)
    {
        rt_kprintf("[CUR] adc device %s not found\n", CUR_ADC_DEV);
        return RT_EOK;   /* 不阻塞系统, current_raw 会报错 */
    }

    if (rt_adc_enable(cur_adc, CUR_ADC_CH) != RT_EOK)
    {
        rt_kprintf("[CUR] adc ch%d enable failed\n", CUR_ADC_CH);
        return RT_EOK;
    }
    cur_enabled = RT_TRUE;
    rt_kprintf("[CUR] adc0 ch15 enabled (前端未接线, 数据无物理意义)\n");
    return RT_EOK;
}
INIT_APP_EXPORT(current_adc_init);

static void current_raw(void)
{
    rt_uint32_t raw, sum = 0, min = 0xFFFFFFFF, max = 0;
    int i, n = CUR_SAMPLES;

    if (cur_adc == RT_NULL || !cur_enabled)
    {
        rt_kprintf("[CUR] adc not ready\n");
        return;
    }

    for (i = 0; i < n; ++i)
    {
        raw = rt_adc_read(cur_adc, CUR_ADC_CH);
        sum += raw;
        if (raw < min) min = raw;
        if (raw > max) max = raw;
        rt_thread_mdelay(1);
    }
    raw = sum / n;

    {
        float mv = (float)raw * CUR_VREF_MV / CUR_RAW_MAX;
        float ma = (mv - CUR_ZERO_MV) / CUR_V_PER_A * 1000.0f;

        rt_kprintf("[CUR] raw mean=%u min=%u max=%u (n=%d)\n", raw, min, max, n);
        rt_kprintf("[CUR] theory: %d.%03d mV, %d mA\n",
                   (int)mv, (int)(mv * 1000) % 1000, (int)ma);
        }
}
MSH_CMD_EXPORT(current_raw, sample bus current ADC ch15 with min/max/mean);
