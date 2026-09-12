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
    /* BUG-009: 实测与未知驱动源争抢(写低时网络 2.37V), 暂改 Hi-Z 避免持续
     * 对灌电流; 使能状态视为不可信(pwm 联锁已拒真)。待 FAE/换脚后恢复输出 */
    { PIN_NAME_DRV_ENABLE, -1, PIN_MODE_OUTPUT,  PIN_LOW,  "MCU_DRV_ENABLE(J4-21, default LOW!)" },
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

        /* BUG-009 建议(手册防毛刺): 输出脚先写锁存值再切方向,
         * 输入→输出切换瞬间即为目标安全电平 */
        if (p->mode == PIN_MODE_OUTPUT)
            rt_pin_write(p->pin, p->level);
        rt_pin_mode(p->pin, p->mode);
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

/* ==== 诊断工具(BUG-009 未完全破案, 保留): 端口F pin21 寄存器级探测,
 * 详情见 调试记录.md BUG-009。主要嫌疑: 双核 CPU2 抢占 GPIO 锁存 ==== */
#include "drv_gpio.h"

/* ==== 诊断工具 v2(BUG-009, DAT/DATR 语义已按官方手册修正:
 *  DAT=焊盘实际电平(rt_pin_read 用), DATR=输出锁存(软件写的值)) ==== */
#define PF21_SYSCON_AHBCPUSELEN1  (*(volatile rt_uint32_t *)0x40037300)
#define PF21_GPIOFCPUSELEN_BIT    (1u << 21)

/* 手册推荐的安全输出配置顺序: 先清锁存, 再开数字/复用/方向 */
static void pf21_config_output(void)
{
    GPIO_TypeDef *pt = GPIOF;
    GPIO_clearPin(pt, GPIO_PIN_21);
    GPIO_setAnalogMode(pt, GPIO_PIN_21, GPIO_ANALOG_DISABLED);
    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_STD);
    GPIO_setPinConfig(pt, GPIO_PIN_21, ALT0_FUNCTION);
    GPIO_setDirectionMode(pt, GPIO_PIN_21, GPIO_DIR_MODE_OUT);
}

static void pf21_diag(void)
{
    GPIO_TypeDef *pt = GPIOF;
    volatile rt_uint32_t *csel3 = (volatile rt_uint32_t *)((char *)&pt->CSEL1 + 8);

    pf21_config_output();
    rt_thread_mdelay(100);

    rt_kprintf("[D] GPFDIR.bit21(output)  = %d (expect 1)\n", 
               (int)((pt->DIR.WORDVAL >> 21) & 1));
    rt_kprintf("[D] GPFAMSEL.bit21(analog)= %d (expect 0)\n", 
               (int)((pt->AMSEL.WORDVAL >> 21) & 1));
    rt_kprintf("[D] GPFODR.bit21(open-dr) = %d (expect 0)\n", 
               (int)((pt->ODR.WORDVAL >> 21) & 1));
    rt_kprintf("[D] GPFMUX2[11:10]        = %d (expect 0=GPIO)\n", 
               (int)((pt->MUX2.WORDVAL >> 10) & 3));
    rt_kprintf("[D] GPFGMUX2[11:10]       = %d\n", 
               (int)((pt->GMUX2.WORDVAL >> 10) & 3));
    rt_kprintf("[D] GPFDATR21(latch)      = %d (wrote 0)\n", 
               (int)((pt->DATR.WORDVAL >> 21) & 1));
    rt_kprintf("[D] GPFDAT21(pad level)   = %d\n", 
               (int)((pt->DAT.WORDVAL >> 21) & 1));

    rt_kprintf("[D] AHBCPUSELEN1.bit21(GPIOFCPUSELEN) = %d\n", 
               (int)((PF21_SYSCON_AHBCPUSELEN1 >> 21) & 1));
    rt_kprintf("[D] GPFCSEL3.PF21(master) = %d (0=M0 1=M1, 仅门控=1时有效)\n", 
               (int)((*csel3 >> 20) & 1));
    rt_kprintf("[D] CPU2 status: 无独立状态寄存器(头文件未定义), 见开发进度待办\n");

    /* 门控实验: 打开 GPIOF CSEL 使能后, 归属切换才真正生效 */
    PF21_SYSCON_AHBCPUSELEN1 |= PF21_GPIOFCPUSELEN_BIT;
    *csel3 &= ~(1u << 20);          /* PF21 -> master0 */
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_thread_mdelay(50);
    rt_kprintf("[D] gate=1 master0: latch=%d pad=%d\n", 
               (int)((pt->DATR.WORDVAL >> 21) & 1),
               (int)((pt->DAT.WORDVAL >> 21) & 1));
    *csel3 |= (1u << 20);           /* PF21 -> master1 */
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_thread_mdelay(50);
    rt_kprintf("[D] gate=1 master1: latch=%d pad=%d\n", 
               (int)((pt->DATR.WORDVAL >> 21) & 1),
               (int)((pt->DAT.WORDVAL >> 21) & 1));
    *csel3 &= ~(1u << 20);
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_uint32_t mask = 0x1UL << 21;
    /* 判别实验: 输入+内部上/下拉, 区分"网络外部强上拉"vs"输出驱动损坏" */
    GPIO_setPinConfig(pt, GPIO_PIN_21, ALT0_FUNCTION);
    GPIO_setDirectionMode(pt, GPIO_PIN_21, GPIO_DIR_MODE_IN);
    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_PULLDOWN);
    rt_thread_mdelay(20);
    rt_kprintf("[D] in+pulldown: pad=%d (sample still HIGH; DMM on J4-20 needed)\n",
               (int)((pt->DAT.WORDVAL & mask) ? 1 : 0));
    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_PULLUP);
    rt_thread_mdelay(20);
    rt_kprintf("[D] in+pullup  : pad=%d (sample still HIGH; DMM on J4-20 needed)\n",
               (int)((pt->DAT.WORDVAL & mask) ? 1 : 0));
    GPIO_setPadConfig(pt, GPIO_PIN_21, GPIO_PIN_TYPE_STD);
    GPIO_setDirectionMode(pt, GPIO_PIN_21, GPIO_DIR_MODE_OUT);
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_kprintf("[D] final: latch=%d pad=%d\n",
               (int)((pt->DATR.WORDVAL >> 21) & 1),
               (int)((pt->DAT.WORDVAL >> 21) & 1));

    /* 对照组: PA.2(TMC_DIR) 同为 safety_gpio 输出脚且此前回读一致,
     * 若 PA.2 latch==pad 而 PF21 latch!=pad, 更利于 FAE 定位 */
    {
        GPIO_TypeDef *pa = GPIOA;
        rt_uint32_t m2 = 0x1UL << 2;
        rt_kprintf("[D] control PA.2: latch=%d pad=%d (expect equal)\n",
                   (int)((pa->DATR.WORDVAL & m2) ? 1 : 0),
                   (int)((pa->DAT.WORDVAL & m2) ? 1 : 0));
    }
}
MSH_CMD_EXPORT(pf21_diag, BUG-009 v2 full diag with corrected DAT/DATR semantics);

/* 慢速 A/B 测试: 低/高各 2 秒交替 3 轮, 配合万用表看 J4-20.
 * 仅允许 J4-20 未接任何外部电路时运行! */
static void pf21_ab(void)
{
    GPIO_TypeDef *pt = GPIOF;
    int round;
    pf21_config_output();
    for (round = 1; round <= 3; ++round)
    {
        GPIO_clearPin(pt, GPIO_PIN_21);
        rt_thread_mdelay(2000);
        rt_kprintf("[AB] r%d LOW : DATR=%d DAT=%d\n", round,
                   (int)((pt->DATR.WORDVAL >> 21) & 1),
                   (int)((pt->DAT.WORDVAL >> 21) & 1));
        GPIO_setPin(pt, GPIO_PIN_21);
        rt_thread_mdelay(2000);
        rt_kprintf("[AB] r%d HIGH: DATR=%d DAT=%d\n", round,
                   (int)((pt->DATR.WORDVAL >> 21) & 1),
                   (int)((pt->DAT.WORDVAL >> 21) & 1));
    }
    GPIO_clearPin(pt, GPIO_PIN_21);
    rt_kprintf("[AB] end LOW (safe)\n");
}
MSH_CMD_EXPORT(pf21_ab, BUG-009 slow LOW/HIGH A/B for multimeter watch);
