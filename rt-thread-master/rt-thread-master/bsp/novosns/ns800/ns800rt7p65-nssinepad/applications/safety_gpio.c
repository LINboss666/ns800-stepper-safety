/*
 * safety_gpio.c - 安全 GPIO 初始化与状态查询
 *
 * 职责（交接文档 §12/§13）:
 *   1. 用 rt_pin_get() 解析冻结引脚表（project_board.h），全部检查返回值
 *   2. 上电即建立安全默认态: MCU_DRV_ENABLE=LOW(最重要), DIR=LOW,
 *      IMU CS 常高, LED/蜂鸣器安全电平, 安全输入配置为普通输入
 *   3. 提供 pin_status / safety_status 命令查看实时电平
 *
 * 注意: 初始化失败只打印告警, 绝不因"默认 GPIO 状态"误使能驱动。
 * IRQ(EstI/DIAG/限位) 暂用普通输入轮询查看, 中断在接线验证后启用(EXTI 无冲突)。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"

struct safety_pin
{
    const char *name;    /* rt_pin_get 字符串 */
    rt_base_t   pin;     /* 解析结果, <0 = 失败 */
    rt_uint8_t  mode;    /* PIN_MODE_OUTPUT / PIN_MODE_INPUT */
    rt_uint8_t  level;   /* 输出脚的默认电平 */
    const char *desc;
};

static struct safety_pin safety_pins[] =
{
    { PIN_NAME_DRV_ENABLE, -1, PIN_MODE_OUTPUT, PIN_LOW,  "MCU_DRV_ENABLE(默认禁止!)" },
    { PIN_NAME_TMC_DIR,    -1, PIN_MODE_OUTPUT, PIN_LOW,  "TMC_DIR" },
    { PIN_NAME_TMC_DIAG,   -1, PIN_MODE_INPUT,  0,        "TMC_DIAG" },
    { PIN_NAME_LIMIT_MIN,  -1, PIN_MODE_INPUT,  0,        "LIMIT_MIN" },
    { PIN_NAME_LIMIT_MAX,  -1, PIN_MODE_INPUT,  0,        "LIMIT_MAX" },
    { PIN_NAME_ESTOP,      -1, PIN_MODE_INPUT,  0,        "ESTOP_SENSE" },
    { PIN_NAME_IMU_CS,     -1, PIN_MODE_OUTPUT, PIN_HIGH, "IMU_CS(常高防总线竞争)" },
    { PIN_NAME_FLASH_CS,   -1, PIN_MODE_OUTPUT, PIN_HIGH, "FLASH_CS(常高)" },
    { PIN_NAME_BUZZER,     -1, PIN_MODE_OUTPUT, PIN_LOW,  "BUZZER(静默)" },
    { PIN_NAME_RUN_LED,    -1, PIN_MODE_OUTPUT, PIN_LOW,  "RUN_LED(安全电平)" },
    { PIN_NAME_WARN_LED,   -1, PIN_MODE_OUTPUT, PIN_LOW,  "WARN_LED(安全电平)" },
    { PIN_NAME_FAULT_LED,  -1, PIN_MODE_OUTPUT, PIN_LOW,  "FAULT_LED(安全电平)" },
};

#define SAFETY_PIN_COUNT (sizeof(safety_pins) / sizeof(safety_pins[0]))

static rt_bool_t sg_ready = RT_FALSE;
static rt_err_t  sg_first_err = RT_EOK;

static struct safety_pin *find_pin(const char *name)
{
    int i;
    for (i = 0; i < (int)SAFETY_PIN_COUNT; ++i)
    {
        if (rt_strcmp(safety_pins[i].name, name) == 0) return &safety_pins[i];
    }
    return RT_NULL;
}

static rt_err_t safety_gpio_setup(void)
{
    int i;
    rt_err_t e = RT_EOK;

    for (i = 0; i < (int)SAFETY_PIN_COUNT; ++i)
    {
        struct safety_pin *p = &safety_pins[i];

        p->pin = rt_pin_get(p->name);
        if (p->pin < 0)
        {
            rt_kprintf("[SAFETY] pin resolve FAILED: %s (%s)\n", p->name, p->desc);
            if (e == RT_EOK) e = -RT_EINVAL;
            continue;
        }

        rt_pin_mode(p->pin, p->mode);
        if (p->mode == PIN_MODE_OUTPUT)
            rt_pin_write(p->pin, p->level);
    }

    sg_ready    = (e == RT_EOK) ? RT_TRUE : RT_FALSE;
    sg_first_err = e;
    return e;
}

static int safety_gpio_init(void)
{
    rt_err_t e = safety_gpio_setup();

    if (e != RT_EOK)
        rt_kprintf("[SAFETY] init FAILED (%d) - 驱动保持禁止, 禁止进入 READY\n", e);
    else
        rt_kprintf("[SAFETY] init OK - MCU_DRV_ENABLE=LOW, 输出安全态已建立\n");

    return RT_EOK;  /* 初始化失败不阻塞系统, 安全态已兜底 */
}
INIT_APP_EXPORT(safety_gpio_init);

static void pin_status(void)
{
    int i;

    rt_kprintf("%-10s %-6s %-7s %s\n", "PIN", "MODE", "LEVEL", "DESC");
    for (i = 0; i < (int)SAFETY_PIN_COUNT; ++i)
    {
        struct safety_pin *p = &safety_pins[i];
        if (p->pin < 0)
        {
            rt_kprintf("%-10s %-6s %-7s %s\n", p->name, "N/A", "-", p->desc);
            continue;
        }
        if (p->mode == PIN_MODE_OUTPUT)
        {
            /* 诊断: 重新写默认电平并回读, 检测写不进/被覆写的情况 */
            rt_pin_write(p->pin, p->level);
            rt_kprintf("%-10s %-6d w%d r=%-7d %s\n", p->name, (int)p->pin,
                       p->level, rt_pin_read(p->pin), p->desc);
        }
        else
        {
            rt_kprintf("%-10s %-6d in=%-7d %s\n", p->name, (int)p->pin,
                       rt_pin_read(p->pin), p->desc);
        }
    }
}
MSH_CMD_EXPORT(pin_status, dump all safety pin levels);

static void safety_status(void)
{
    struct safety_pin *p;

    rt_kprintf("[SAFETY] ready=%s first_err=%d\n",
               sg_ready ? "YES" : "NO", sg_first_err);

    p = find_pin(PIN_NAME_DRV_ENABLE);
    if (p != RT_NULL && p->pin >= 0)
        rt_kprintf("[SAFETY] MCU_DRV_ENABLE = %d (%s)\n", rt_pin_read(p->pin),
                   rt_pin_read(p->pin) == PIN_LOW ? "禁止(安全)" : "!!已使能!!");

    p = find_pin(PIN_NAME_ESTOP);
    if (p != RT_NULL && p->pin >= 0)
        rt_kprintf("[SAFETY] ESTOP_SENSE 原始电平 = %d (有效电平待接线确认)\n",
                   rt_pin_read(p->pin));
}
MSH_CMD_EXPORT(safety_status, show safety subsystem state);
rt_bool_t safety_gpio_ready(void) { return sg_ready; }

rt_base_t safety_pin(const char *name)
{
    struct safety_pin *p = find_pin(name);
    return (p != RT_NULL) ? p->pin : -RT_ERROR;
}

/* ==== 临时诊断(BUG-009): 端口F pin21 寄存器级探测, 破案后删除 ==== */
#include "drv_gpio.h"

static void pf21_probe(void)
{
    GPIO_TypeDef *pt = GPIOF;
    volatile rt_uint32_t *mux2 = (volatile rt_uint32_t *)(&pt->MUX1) + 1;
    volatile rt_uint32_t *gmux2 = (volatile rt_uint32_t *)(&pt->GMUX1) + 1;
    rt_uint32_t mask = 0x1UL << 21;

    rt_kprintf("[P] before: DIR21=%d DAT=%08X DATR21=%d MUX2=%08X GMUX2=%08X\n",
               (int)((pt->DIR.WORDVAL >> 21) & 1), pt->DAT.WORDVAL,
               (int)((pt->DATR.WORDVAL >> 21) & 1), *mux2, *gmux2);

    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_STD);
    GPIO_setDirectionMode(pt, GPIO_PIN_21, GPIO_DIR_MODE_OUT);
    GPIO_setPinConfig(pt, GPIO_PIN_21, ALT0_FUNCTION);

    /* 实验1: 直接写 DAT 寄存器(绕过 SET/CLR) */
    WRITE_REG(pt->DAT.WORDVAL, pt->DAT.WORDVAL & ~(1u << 21));
    rt_kprintf("[P] direct DAT clear: DAT21=%d DATR21=%d' + NL + '",
               (int)((pt->DAT.WORDVAL >> 21) & 1),
               (int)((pt->DATR.WORDVAL >> 21) & 1));

    /* 实验2: 开漏 + 写低 */
    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_OD);
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_kprintf("[P] OD + clr: DAT21=%d' + NL + '",
               (int)((pt->DAT.WORDVAL >> 21) & 1));
    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_STD);

    /* 实验3: 写低后延时再读 5 次, 看是否被弹回 */
    GPIO_clearPin(pt, GPIO_PIN_21);
    {
        int i;
        for (i = 0; i < 5; ++i)
        {
            rt_thread_mdelay(20);
            rt_kprintf("[P] t+%dms: DAT21=%d' + NL + '", (i + 1) * 20,
                       (int)((pt->DAT.WORDVAL >> 21) & 1));
        }
    }

    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_kprintf("[P] master0: w0 r=%d (DATR=%d)' + NL + '",
               (int)((pt->DAT.WORDVAL >> 21) & 1),
               (int)((pt->DATR.WORDVAL >> 21) & 1));

    csel1[2] = csel3 | (1u << 20);             /* master 1 */
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_kprintf("[P] master1: w0 r=%d (DATR=%d)' + NL + '",
               (int)((pt->DAT.WORDVAL >> 21) & 1),
               (int)((pt->DATR.WORDVAL >> 21) & 1));

    csel1[2] = csel3;                          /* 恢复原值 */
    GPIO_clearPin(pt, GPIO_PIN_21);

    rt_kprintf("[P] after OUT+CLR: DIR21=%d DAT21=%d DATR21=%d MUX2=%08X GMUX2=%08X\n",
               (int)((pt->DIR.WORDVAL >> 21) & 1), (int)((pt->DAT.WORDVAL >> 21) & 1),
               (int)((pt->DATR.WORDVAL >> 21) & 1), *mux2, *gmux2);

    GPIO_setPin(pt, GPIO_PIN_21);
    rt_kprintf("[P] after SET: DAT21=%d DATR21=%d\n",
               (int)((pt->DAT.WORDVAL >> 21) & 1),
               (int)((pt->DATR.WORDVAL >> 21) & 1));
    GPIO_clearPin(pt, GPIO_PIN_21);   /* 恢复安全低 */
    rt_kprintf("[P] end CLR: DAT21=%d DATR21=%d\n",
               (int)((pt->DAT.WORDVAL >> 21) & 1),
               (int)((pt->DATR.WORDVAL >> 21) & 1));
}
MSH_CMD_EXPORT(pf21_probe, BUG-009 register level probe for PF.21);
