/*
 * safety_gpio.c - 安全 GPIO 初始化与状态查询
 *
 * 职责（交接文档 §12/§13）:
 *   1. 用 rt_pin_get() 解析冻结引脚表（project_board.h），全部检查返回值
 *   2. 上电即建立安全默认态: MCU_DRV_ENABLE=LOW(最重要), DIR=LOW,
 *      IMU CS 常高, LED/蜂鸣器安全电平, 安全输入配置为普通输入
 *   3. 提供 pin_status / safety_status 命令查看实时电平
 *   4. Fix A: MCU_DRV_ENABLE 是唯一由本模块持有的"安全关键输出", 对外只暴露
 *      写后回读确认的 safety_drv_enable_write(); rt_pin_write 无返回值,
 *      裸写无法证明焊盘真的到了目标电平(参 BUG-009 PF21 被顶高)。
 *
 * 注意: 初始化失败现在向上传播(Fix A), 由 supervisor_boot stage 1 fail closed;
 *       绝不允许"默认 GPIO 状态"误使能驱动。
 * 中断(DIAG + 可选限位)注册在 safety_thread.c safety_irq_attach() (显式门控)。
 *
 * 2026-10-21 输入职责更正(用户确认接线 + RevA 图纸): PC.6/J4-40 读的是板上 TMC_ENN 网络
 * (扩展板 H6-5), 属于**驱动使能状态反馈**, 不是急停感测输入; 本模块只把它当被动输入读,
 * 并新增"按请求态判定反馈"的纯函数 + 有界确认入口(见 safety_gpio.h)。硬件急停没有送进 MCU,
 * 软件不存在独立急停输入。限位两路由 SAFETY_LIMIT_INPUTS_ENABLED 决定是否存在于本表内。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"

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
    { PIN_NAME_DRV_ENABLE, -1, PIN_MODE_OUTPUT,  PIN_LOW,  "MCU_DRV_ENABLE(J4-21, default LOW!)" },
    { PIN_NAME_TMC_DIR,    -1, PIN_MODE_OUTPUT, PIN_LOW,  "TMC_DIR" },
    { PIN_NAME_TMC_DIAG,   -1, PIN_MODE_INPUT,  0,        "TMC_DIAG" },
#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE
    /* 限位宏 ENABLED: 两路 OK 信号进入解析/配置/读取(极性: HIGH=安全, LOW=触发) */
    { PIN_NAME_LIMIT_MIN,  -1, PIN_MODE_INPUT,  0,        "LIMIT_MIN_OK(HIGH=safe)" },
    { PIN_NAME_LIMIT_MAX,  -1, PIN_MODE_INPUT,  0,        "LIMIT_MAX_OK(HIGH=safe)" },
#endif
    { PIN_NAME_ENN_FEEDBACK, -1, PIN_MODE_INPUT, 0,       "ENN_FEEDBACK(J4-40=H6-5/TMC_ENN, 只读)" },
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
/* 最近一次 DRV_ENABLE 回读实测电平; -1 = 从未成功确认(含引脚未解析) */
static volatile rt_int32_t sg_drv_en_last_read = -1;
/* 最近一次**经回读确认成功**的许可请求电平(不是裸写请求); -1 = 从未确认 */
static volatile rt_int32_t sg_drv_en_request = -1;

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

    /* ENN 反馈脚只能是被动输入: 表里被改成 OUTPUT 就 fail closed, 不静默变成可驱动。 */
    for (i = 0; i < (int)SAFETY_PIN_COUNT; ++i)
    {
        if (rt_strcmp(safety_pins[i].name, PIN_NAME_ENN_FEEDBACK) == 0 &&
            safety_pins[i].mode != PIN_MODE_INPUT)
        {
            rt_kprintf("[SAFETY] !! ENN_FEEDBACK(%s) must stay PIN_MODE_INPUT - FAIL CLOSED\n",
                       PIN_NAME_ENN_FEEDBACK);
            sg_ready     = RT_FALSE;
            sg_first_err = -RT_EPERM;
            return -RT_EPERM;
        }
    }

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

rt_err_t safety_gpio_boot(void)
{
    rt_err_t e = safety_gpio_setup();

    if (e != RT_EOK)
    {
        rt_kprintf("[SAFETY] gpio init FAILED (%d) - 驱动保持禁止, 禁止进入 READY\n", e);
        return e;
    }

    /* 安全关键输出必须"写 + 回读"确认, 裸写不算建立安全态(Fix A) */
    e = safety_drv_enable_write(0);
    if (e != RT_EOK)
    {
        rt_kprintf("[SAFETY] MCU_DRV_ENABLE LOW NOT verified (%d) - FAIL CLOSED\n", e);
        sg_ready   = RT_FALSE;
        sg_first_err = e;
        return e;
    }

    rt_kprintf("[SAFETY] init OK - MCU_DRV_ENABLE=LOW(readback verified), "
               "输出安全态已建立\n");
    return RT_EOK;
}

/* ---------- MCU_DRV_ENABLE: 写 + 回读确认(Fix A) ----------
 * level: 1=请求使能(HIGH) 0=禁止(LOW)。readback 只证明 MCU 焊盘实际电平,
 * 不替代 ENN 整链硬件验收(HARDWARE-PENDING)。调用方禁止用 rt_pin_write 裸写。 */
#define DRV_EN_SETTLE_MS 1u
#define DRV_EN_TRIES     2u

rt_err_t safety_drv_enable_write(rt_uint8_t level)
{
    rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);
    rt_int32_t want = level ? PIN_HIGH : PIN_LOW;
    rt_int32_t got = -1;
    rt_uint32_t i;

    if (en < 0)
    {
        rt_kprintf("[SAFETY] !! DRV_ENABLE pin (%s) unresolved - CANNOT verify\n",
                   PIN_NAME_DRV_ENABLE);
        sg_drv_en_last_read = -1;
        return -RT_EINVAL;
    }

    for (i = 0; i < DRV_EN_TRIES; ++i)
    {
        rt_pin_write(en, (rt_uint8_t)want);
        rt_thread_mdelay(DRV_EN_SETTLE_MS);
        got = (rt_int32_t)rt_pin_read(en);
        sg_drv_en_last_read = got;
        if (got == want)
        {
            sg_drv_en_request = want;      /* 只有回读一致才算"请求已建立" */
            return RT_EOK;
        }
        rt_kprintf("[SAFETY] DRV_ENABLE readback mismatch: want=%d got=%d (try %u)\n",
                   (int)want, (int)got, i + 1);
    }

    rt_kprintf("[SAFETY] !! CRITICAL: DRV_ENABLE READBACK FAILURE (want=%d got=%d)"
               " pad %s !!\n", (int)want, (int)got,
                   level ? "NOT driven HIGH" : "NOT confirmed LOW");
    return -RT_EIO;
}

rt_bool_t safety_drv_enable_is_low(void)
{
    rt_base_t en = safety_pin(PIN_NAME_DRV_ENABLE);

    if (en < 0) return RT_FALSE;             /* 不知道=不安全 */
    return (rt_pin_read(en) == PIN_LOW) ? RT_TRUE : RT_FALSE;
}

/* ---------- ENN 状态反馈判定(2026-10-21: PC.6 = 板上 TMC_ENN, 不是急停输入) ----------
 * 期望关系(RevA 图纸 U5/U13/Q1 + R10 上拉 + 手册 §2.2 推导, 未实测):
 *   请求禁能(want=0): 焊盘 LOW 且 反馈 HIGH(=1) → 一致
 *   请求使能(want=1): 焊盘 HIGH 且 反馈 LOW(=0) → 一致
 * 反馈 LOW 只说明"没有施加禁能", 不能证明存在转矩; 反馈读不到/矛盾一律按不成立处理。 */
#define DRV_FB_TRIES      3u        /* 有界窗口: 最多 3 次采样(测试可见, 非物理时延上界) */
#define DRV_FB_SETTLE_MS  1u

static const char *const sg_fb_names[] = {
    "OK", "TRANSITION", "CUT_BY_HARDWARE", "UNEXPECTED_ENABLE",
    "PAD_MISMATCH", "NO_SAMPLE"
};

const char *safety_fb_verdict_name(safety_fb_verdict_t v)
{
    if ((int)v < 0 || (int)v >= (int)(sizeof(sg_fb_names) / sizeof(sg_fb_names[0])))
        return "UNKNOWN";
    return sg_fb_names[(int)v];
}

safety_fb_verdict_t safety_fb_evaluate(rt_uint8_t want_enabled,
                                       rt_int32_t pad, rt_bool_t pad_ok,
                                       rt_int32_t fb,  rt_bool_t fb_ok)
{
    rt_int32_t want_pad = want_enabled ? PIN_HIGH : PIN_LOW;
    rt_int32_t want_fb  = want_enabled ? PIN_LOW  : PIN_HIGH;

    if (!pad_ok || !fb_ok) return SAFETY_FB_NO_SAMPLE;   /* 不知道=不成立 */
    if (pad != want_pad)   return SAFETY_FB_PAD_MISMATCH;
    if (fb  == want_fb)    return SAFETY_FB_OK;
    /* 焊盘已到请求侧但反馈在另一侧: 区分"说停没停"与"要动没让动" */
    return want_enabled ? SAFETY_FB_CUT_BY_HARDWARE : SAFETY_FB_UNEXPECTED_ENABLE;
}

/* 单次采样读反馈脚。返回 FALSE = 引脚未解析或电平不是合法 0/1(不猜)。 */
static rt_bool_t sg_fb_read(rt_int32_t *out)
{
    rt_base_t   fb  = safety_pin(PIN_NAME_ENN_FEEDBACK);
    rt_int32_t  val;

    if (fb < 0) return RT_FALSE;
    val = (rt_int32_t)rt_pin_read(fb);
    if (val != PIN_HIGH && val != PIN_LOW) return RT_FALSE;
    if (out != RT_NULL) *out = val;
    return RT_TRUE;
}

static rt_bool_t sg_pad_read(rt_int32_t *out)
{
    rt_base_t  en = safety_pin(PIN_NAME_DRV_ENABLE);
    rt_int32_t val;

    if (en < 0) return RT_FALSE;
    val = (rt_int32_t)rt_pin_read(en);
    if (val != PIN_HIGH && val != PIN_LOW) return RT_FALSE;
    if (out != RT_NULL) *out = val;
    return RT_TRUE;
}

safety_fb_verdict_t safety_fb_now(rt_uint8_t want_enabled)
{
    rt_int32_t pad = -1, fb = -1;
    rt_bool_t  pad_ok = sg_pad_read(&pad);
    rt_bool_t  fb_ok  = sg_fb_read(&fb);

    return safety_fb_evaluate(want_enabled, pad, pad_ok, fb, fb_ok);
}

rt_err_t safety_drv_feedback_confirm(rt_uint8_t want_enabled, safety_fb_verdict_t *out)
{
    safety_fb_verdict_t first, v = SAFETY_FB_NO_SAMPLE;
    rt_bool_t stable = RT_TRUE;
    rt_uint32_t i;

    first = safety_fb_now(want_enabled);
    v = first;
    for (i = 1; i < DRV_FB_TRIES; ++i)
    {
        if (v == SAFETY_FB_OK) break;
        rt_thread_mdelay(DRV_FB_SETTLE_MS);
        v = safety_fb_now(want_enabled);
        if (v != first) stable = RT_FALSE;
    }

    if (out != RT_NULL) *out = v;
    if (v == SAFETY_FB_OK) return RT_EOK;
    /* 样本在窗口内变化过 => 这是"过渡未确认", 绝不记成功, 也不谎称稳定矛盾 */
    if (!stable)
    {
        if (out != RT_NULL) *out = SAFETY_FB_TRANSITION;
        rt_kprintf("[SAFETY] ENN feedback still TRANSITIONING after %u bounded samples\n",
                   DRV_FB_TRIES);
        return -RT_ETIMEOUT;
    }
    if (v == SAFETY_FB_NO_SAMPLE)
    {
        rt_kprintf("[SAFETY] !! ENN feedback sample INVALID (pin unresolved/bad level)"
                   " - 不判为就绪/确认\n");
        return -RT_EINVAL;
    }
    rt_kprintf("[SAFETY] !! ENN feedback %s for request=%d (bounded tries=%u, stable)\n",
               safety_fb_verdict_name(v), (int)want_enabled, DRV_FB_TRIES);
    return -RT_EIO;
}

void safety_fb_snapshot(rt_int32_t *out_request, rt_int32_t *out_pad,
                        rt_int32_t *out_fb, rt_bool_t *out_fb_resolved)
{
    rt_int32_t fb = -1, pad = -1;
    rt_bool_t  fb_ok  = sg_fb_read(&fb);
    rt_bool_t  pad_ok = sg_pad_read(&pad);

    if (out_request     != RT_NULL) *out_request     = sg_drv_en_request;
    if (out_pad         != RT_NULL) *out_pad         = pad_ok ? pad : -1;
    if (out_fb          != RT_NULL) *out_fb          = fb_ok ? fb  : -1;
    if (out_fb_resolved != RT_NULL) *out_fb_resolved = fb_ok ? RT_TRUE : RT_FALSE;
}

rt_bool_t safety_limit_inputs_enabled(void)
{
    /* 写成表达式而不是第二个 #if, 是为了让反向验证注入能用唯一锚点命中引脚表那道 #if */
    return (SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE) ? RT_TRUE : RT_FALSE;
}

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
            /* 诊断: 重新写默认电平并回读, 检测写不进/被覆写的情况。
             * DRV_ENABLE 走统一的安全关键路径 helper(Fix A), 不裸写。 */
            if (rt_strcmp(p->name, PIN_NAME_DRV_ENABLE) == 0)
            {
                rt_err_t ev = safety_drv_enable_write(0);
                rt_kprintf("%-10s %-6d w%d r=%-7d %s%s\n", p->name, (int)p->pin,
                           p->level, rt_pin_read(p->pin), p->desc,
                           ev == RT_EOK ? "" : "  !! READBACK FAIL !!");
                continue;
            }
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
    else
        rt_kprintf("[SAFETY] !! MCU_DRV_ENABLE pin unresolved - 无软件保障 !!\n");

    rt_kprintf("[SAFETY] DRV_ENABLE last write+readback verified = %d (-1 = never verified)\n",
               (int)sg_drv_en_last_read);

    /* ENN 反馈(2026-10-21 职责更正): PC.6 读的是板上 TMC_ENN, 不是急停按钮 */
    {
        rt_int32_t req = -1, pad = -1, fb = -1;
        rt_bool_t  fb_ok = RT_FALSE;

        safety_fb_snapshot(&req, &pad, &fb, &fb_ok);
        rt_kprintf("[SAFETY] ENN_FEEDBACK(%s = 板上 TMC_ENN) raw=%d resolved=%s "
                   "[只读输入, 非急停感测; HIGH=禁能侧, LOW=未施加禁能(不保证转矩)]\n",
                   PIN_NAME_ENN_FEEDBACK, (int)fb, fb_ok ? "YES" : "NO");
        if (req < 0)
        {
            rt_kprintf("[SAFETY] drive request = NEVER-VERIFIED -> feedback verdict = NO_SAMPLE\n");
        }
        else
        {
            rt_uint8_t want = (req == PIN_HIGH) ? 1u : 0u;
            rt_kprintf("[SAFETY] request=%s pad=%d expected_FB=%s FB=%d -> verdict=%s\n",
                       want ? "ENABLE(HIGH)" : "DISABLE(LOW)", (int)pad,
                       want ? "LOW" : "HIGH", (int)fb,
                       safety_fb_verdict_name(safety_fb_now(want)));
        }
    }

    rt_kprintf("[SAFETY] LIMIT inputs = %s\n",
               safety_limit_inputs_enabled()
                   ? "ENABLED: HIGH=safe / LOW=trIGGER (falling IRQ), 仍需实物接线与极性验收"
                   : "DISABLED_BY_CONFIG (限位实现保留, 不解析/不配置/不读/不注册 IRQ)");
    if (safety_limit_inputs_enabled())
    {
        p = find_pin(PIN_NAME_LIMIT_MIN);
        if (p != RT_NULL && p->pin >= 0)
            rt_kprintf("[SAFETY] LIMIT_MIN_OK raw=%d (safe=1)\n", (int)rt_pin_read(p->pin));
        p = find_pin(PIN_NAME_LIMIT_MAX);
        if (p != RT_NULL && p->pin >= 0)
            rt_kprintf("[SAFETY] LIMIT_MAX_OK raw=%d (safe=1)\n", (int)rt_pin_read(p->pin));
    }
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
#ifdef NS800_ENABLE_LEGACY_PF21_DIAG
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
#endif /* NS800_ENABLE_LEGACY_PF21_DIAG */
