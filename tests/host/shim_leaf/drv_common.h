/*
 * shim_leaf/drv_common.h —— CP5 §5: 主机侧**最小 SDK/寄存器 shim**。
 *
 * 服务对象: 真实生产叶子
 *   libraries/HAL_Drivers/drivers/drv_uart_txleaf.inc  (ns800_uart_write_budgeted)
 *   libraries/HAL_Drivers/drivers/drv_epwm_leaf.inc    (prepare/configure_regs/commit)
 * 这两份 .inc 在目标上由 drv_uart.c / drv_epwm.c 在原位置 include; 主机夹具 TU 在提供
 * 本文件 + 真实签名的对象表之后 include **同一个文件**。等待循环与寄存器写序因此只有
 * 一份实现, C10/C17 观察到的就是生产语义。
 *
 * 每条声明都注明**本地 SDK 原文出处**(不许猜):
 *   DEVH = packages/novosns-series-latest/NS800RT7XXX/Device/Inc/NS800RT7P65x.h
 *   ROOTH= packages/novosns-series-latest/NS800RT7XXX/Device/Inc/NS800RTxxxx.h
 *   EPWMH= packages/novosns-series-latest/NS800RT7XXX/StdDriver/Inc/epwm.h
 *   UARTH= packages/novosns-series-latest/NS800RT7XXX/StdDriver/Inc/uart.h
 *   RCCC = packages/novosns-series-latest/NS800RT7XXX/StdDriver/Src/rcc.c
 *   TIH  = packages/novosns-series-latest/NS800RT7XXX/StdDriver/Inc/ti/NS800RT7xxx_TI_epwm.h
 * StdDriver 头本身**不能**在 PC 上编: ROOTH:64-74 有 ARM 内联汇编 nop_delay_asm、
 * DEVH:314 include core_cm7.h(CMSIS 核头, 需要 __ASM/__IOM/__DSB/NVIC)。因此这里按原文
 * 复刻被叶子用到的寄存器布局、位定义与内联函数体, 并用 _Static_assert 把布局钉住。
 *
 * ⚠ 这条链用**寄存器模型 + 受控时间**判据, 不等于目标机最坏时延、真实波形或物理安全
 *   证明(BUG-008 / RISK-03/03a / 物理 RISK-04-b 仍按原边界开放)。
 */
#ifndef NS800_LEAF_DRV_COMMON_H
#define NS800_LEAF_DRV_COMMON_H

#include <rtthread.h>
#include <rtdevice.h>
#include <assert.h>     /* _Static_assert(c11) */

/* ---------- CMSIS 限定符在主机上的等价物(不影响可观察语义) ---------- */
#ifndef __IOM
#define __IOM volatile
#endif
#ifndef __IM
#define __IM  volatile
#endif
#ifndef __OM
#define __OM  volatile
#endif

/* ---------- drv_uart.h 需要的非寄存器类型(目标上来自 SDK/CMSIS) ---------- */
typedef int GPIO_PinNum;          /* 目标: GPIO_PIN_x 枚举 */
typedef int GPIO_AltFunc;
typedef int GPIO_Direction;
typedef int IRQn_Type;
typedef struct { volatile uint32_t w[16]; } GPIO_TypeDef;

/* ---------- 寄存器访问宏(ROOTH:23-35 原文; 唯一增补 = 写次数计数) ----------
 * SDK 原文:
 *   SET_BIT(REG,BIT)                   ((REG) |= (BIT))
 *   CLEAR_BIT(REG,BIT)                 ((REG) &= ~((uint32_t)BIT))
 *   READ_BIT(REG,BIT)                  ((REG) & (BIT))
 *   WRITE_REG(REG,VAL)                 ((REG) = (VAL))
 *   READ_REG(REG)                      ((REG))
 *   MODIFY_REG(REG,C,S)                WRITE_REG((REG), (((READ_REG(REG)) & (~((uint32_t)C))) | (S)))
 *   TYPE8(addr) / TYPE32(addr)         (*((volatile uint8_t/32_t *)(addr)))
 * 主机侧把每一次"落到寄存器对象上的写"计一次 ns800_leaf_reg_writes —— 表达式的值与
 * 副作用与原文逐字一致, 只是多一个自增。C10 的"寄存器写计数必须 0"因此是**真计数**,
 * 不是"桩调用次数"。 */
extern volatile uint32_t ns800_leaf_reg_writes;     /* 定义在夹具: 全部寄存器写次数 */
extern volatile uint32_t ns800_leaf_tbctl_writes;   /* 其中 TBCTL 写次数 */

#define NS800_LEAF_WR(ptr, val)  (ns800_leaf_reg_writes++, *(ptr) = (uint32_t)(val))

#define LEAF_SET_BIT(REG, BIT)        (NS800_LEAF_WR(&(REG), ((REG) | (BIT))))
#define LEAF_CLEAR_BIT(REG, BIT)      (NS800_LEAF_WR(&(REG), ((REG) & ~((uint32_t)(BIT)))))
#define LEAF_READ_BIT(REG, BIT)       ((REG) & (BIT))
#define LEAF_MODIFY_REG(REG, C, S)    (NS800_LEAF_WR(&(REG), \
                                    (((REG) & ~((uint32_t)(C))) | ((uint32_t)(S)))))
#define LEAF_WRITE_REG(REG, VAL)      NS800_LEAF_WR(&(REG), (VAL))
#define LEAF_READ_REG(REG)            (REG)
#define LEAF_TYPE8(addr)              (*((volatile uint8_t *)(addr)))
#define LEAF_TYPE32(addr)             (*((volatile uint32_t *)(addr)))

/* SET_BIT/CLEAR_BIT/MODIFY_REG/WRITE_REG/READ_BIT/READ_REG/TYPE8/TYPE32 在叶子源码里
 * 就叫这些名字, 因此宏名必须与 SDK 一致(SDK 原文同样是全局命名)。 */
#ifndef SET_BIT
#define SET_BIT(REG, BIT)      LEAF_SET_BIT((REG), (BIT))
#endif
#ifndef CLEAR_BIT
#define CLEAR_BIT(REG, BIT)    LEAF_CLEAR_BIT((REG), (BIT))
#endif
#ifndef READ_BIT
#define READ_BIT(REG, BIT)     LEAF_READ_BIT((REG), (BIT))
#endif
#ifndef WRITE_REG
#define WRITE_REG(REG, VAL)    LEAF_WRITE_REG((REG), (VAL))
#endif
#ifndef READ_REG
#define READ_REG(REG)          LEAF_READ_REG(REG)
#endif
#ifndef MODIFY_REG
#define MODIFY_REG(REG, C, S)  LEAF_MODIFY_REG((REG), (C), (S))
#endif
#ifndef TYPE8
#define TYPE8(addr)            LEAF_TYPE8(addr)
#endif
#ifndef TYPE32
#define TYPE32(addr)           LEAF_TYPE32(addr)
#endif

/* ===================================================================== */
/* UART 寄存器模型                                                        */
/* ===================================================================== */
/* DEVH:31469-31668 (UART_TypeDef, Size=48):
 *   RESERVED[2] / GLB@0x08 / RESERVED1@0x0C / BAUD@0x10 / STAT@0x14 /
 *   CTRL@0x18 / DATA@0x1C / MATCH@0x20 / MODIR@0x24 / FIFO@0x28 / WATER@0x2C
 * 只有 STAT 与 DATA 参与被测叶子, 其余按同偏移占位, 保证布局与原文一致。 */
typedef struct {
    uint32_t          RESERVED[2];      /* 0x00 */
    union { uint32_t WORDVAL; } GLB;    /* 0x08 */
    uint32_t          RESERVED1;        /* 0x0C */
    union { uint32_t WORDVAL; } BAUD;   /* 0x10 */
    union {
        __IOM uint32_t WORDVAL;         /* 0x14 UART state register */
        struct {
                uint32_t          : 21;
            __IM uint32_t RDRF    : 1;  /* [21] DEVH:31524 */
            __IM uint32_t TC      : 1;  /* [22] DEVH:31525 */
            __IM uint32_t TDRE    : 1;  /* [23] DEVH:31526 */
                uint32_t          : 8;
        } BIT;
    } STAT;
    union { uint32_t WORDVAL; } CTRL;   /* 0x18 */
    union { uint32_t WORDVAL; } DATA;   /* 0x1C UART data register */
    union { uint32_t WORDVAL; } MATCH;  /* 0x20 */
    union { uint32_t WORDVAL; } MODIR;  /* 0x24 */
    union { uint32_t WORDVAL; } FIFO;   /* 0x28 */
    union { uint32_t WORDVAL; } WATER;  /* 0x2C */
} UART_TypeDef;

_Static_assert(offsetof(UART_TypeDef, STAT.WORDVAL) == 0x14u, "UART STAT offset");
_Static_assert(offsetof(UART_TypeDef, DATA.WORDVAL) == 0x1Cu, "UART DATA offset");

/* DEVH:57584-57585 原文 */
#define UART_STAT_TDRE_S   (23UL)
#define UART_STAT_TDRE_M   (0x800000UL)

/* uart.h:696-699 / :757-760 原文函数体(无等待、无循环 —— 这正是生产叶子敢在预算内
 * 反复调它的前提; 主机侧复刻同一对叶子, 等待循环仍然只在唯一实现里)。
 * ⚠ ns800_leaf_uart_tx_byte 是本链唯一的增补: 一次 DATA 写 = 一个字节离开本 MCU, 夹具
 *   把它接进行模型(帧装配 + 移位时间)。寄存器写本身仍按原文执行, 判断"有没有空"的
 *   UART_isSpaceAvailable 一字未改, 所以生产等待环看到的是**模型给的 TDRE**。 */
void ns800_leaf_uart_tx_byte(rt_uint8_t data);
/* 2026-10-28 TMC-RC: 生产等待环由 rt_thread_mdelay(1) 改为按 tick 预算的短自旋
 * (帧内字节间隔必须连续)。主机模型的时间只在 mdelay/read 里推进, 因此这里的轮询
 * 必须代表"硬件在这段时间里移位": 无空位时推进 1ms 模型时间。 */
void ns800_leaf_uart_poll(void);

static inline uint8_t UART_isSpaceAvailable(UART_TypeDef *uart)
{
    uint8_t r = (uint8_t)((READ_BIT(uart->STAT.WORDVAL, UART_STAT_TDRE_M)) >> UART_STAT_TDRE_S);
    if (!r) ns800_leaf_uart_poll();
    return r;
}
static inline void UART_writeChar(UART_TypeDef *uart, uint8_t data)
{
    ns800_leaf_uart_tx_byte(data);
    TYPE8(&uart->DATA.WORDVAL) = data;
}

/* ===================================================================== */
/* EPWM 寄存器模型 + StdDriver 内联                                       */
/* ===================================================================== */
/* 布局按 DEVH:12133-15140 的偏移复刻(StdDriver 的内联函数做**字节偏移**的指针运算,
 * 例如 EPWM_setCounterCompareValue 用 &CMPA.WORDVAL + compModule, 因此空洞必须一致,
 * 否则主机上写的就不是那个寄存器了)。每个寄存器只用 .WORDVAL 访问, 位段以掩码/移位
 * 常量表达(与 DEVH 原文数值一致)。 */
typedef struct {
    union { uint32_t WORDVAL; } TBCTL;              /* 0x000 */
    union { uint32_t WORDVAL; } TBCTL2;             /* 0x004 */
    uint32_t RESERVED0[2];                          /* 0x008 */
    union { uint32_t WORDVAL; } TBCTR;              /* 0x010 */
    uint32_t RESERVED1[3];                          /* 0x014 */
    union { uint32_t WORDVAL; } CMPCTL;             /* 0x020 */
    union { uint32_t WORDVAL; } CMPCTL2;            /* 0x024 */
    uint32_t RESERVED2[2];                          /* 0x028 */
    union { uint32_t WORDVAL; } DBCTL;              /* 0x030 */
    uint32_t RESERVED3[7];                          /* 0x034 */
    union { uint32_t WORDVAL; } PCCTL;              /* 0x050 */
    uint32_t RESERVED4[43];                         /* 0x054 */
    union { uint32_t WORDVAL; } AQCTLA;             /* 0x100 */
    union { uint32_t WORDVAL; } AQCTLA2;            /* 0x104 */
    union { uint32_t WORDVAL; } AQCTLB;             /* 0x108 */
    union { uint32_t WORDVAL; } AQCTLB2;            /* 0x10C */
    uint32_t RESERVED5[12];                         /* 0x110 */
    union { uint32_t WORDVAL; } DBREDHR;            /* 0x140 */
    union { uint32_t WORDVAL; } DBRED;              /* 0x144 */
    union { uint32_t WORDVAL; } DBFEDHR;            /* 0x148 */
    union { uint32_t WORDVAL; } DBFED;              /* 0x14C */
    uint32_t RESERVED6[12];                         /* 0x150 */
    union { uint32_t WORDVAL; } TBPHS;              /* 0x180 */
    uint32_t RESERVED7[2];                          /* 0x184 */
    union { uint32_t WORDVAL; } TBPRD;              /* 0x18C */
    uint32_t RESERVED15[6];                         /* 0x190 */
    union { uint32_t WORDVAL; } CMPA;               /* 0x1A8 */
    uint32_t RESERVED16;                            /* 0x1AC */
    union { uint32_t WORDVAL; } CMPB;               /* 0x1B0 */
} EPWM_TypeDef;

#define LEAF_ASSERT_OFF(f, off) _Static_assert(offsetof(EPWM_TypeDef, f) == (off), #f)
LEAF_ASSERT_OFF(TBCTL.WORDVAL,   0x000u);
LEAF_ASSERT_OFF(TBCTL2.WORDVAL,  0x004u);
LEAF_ASSERT_OFF(TBCTR.WORDVAL,   0x010u);
LEAF_ASSERT_OFF(CMPCTL.WORDVAL,  0x020u);
LEAF_ASSERT_OFF(CMPCTL2.WORDVAL, 0x024u);
LEAF_ASSERT_OFF(DBCTL.WORDVAL,   0x030u);
LEAF_ASSERT_OFF(PCCTL.WORDVAL,   0x050u);
LEAF_ASSERT_OFF(AQCTLA.WORDVAL,  0x100u);
LEAF_ASSERT_OFF(AQCTLA2.WORDVAL, 0x104u);
LEAF_ASSERT_OFF(AQCTLB.WORDVAL,  0x108u);
LEAF_ASSERT_OFF(AQCTLB2.WORDVAL, 0x10Cu);
LEAF_ASSERT_OFF(DBRED.WORDVAL,   0x144u);
LEAF_ASSERT_OFF(DBFED.WORDVAL,   0x14Cu);
LEAF_ASSERT_OFF(TBPHS.WORDVAL,   0x180u);
LEAF_ASSERT_OFF(TBPRD.WORDVAL,   0x18Cu);
LEAF_ASSERT_OFF(CMPA.WORDVAL,    0x1A8u);
LEAF_ASSERT_OFF(CMPB.WORDVAL,    0x1B0u);
#undef LEAF_ASSERT_OFF

/* DEVH:43152-43163 / 43215 / 43292-43299 / 43644-43679 原文数值 */
#define EPWM_TBCTL_CTRMODE_S     (0UL)
#define EPWM_TBCTL_CTRMODE_M     (0x3UL)
#define EPWM_TBCTL_PHSEN_S       (2UL)
#define EPWM_TBCTL_PHSEN_M       (0x4UL)
#define EPWM_TBCTL_PRDLD_S       (3UL)
#define EPWM_TBCTL_PRDLD_M       (0x8UL)
#define EPWM_TBCTL_HSPCLKDIV_S   (7UL)
#define EPWM_TBCTL_HSPCLKDIV_M   (0x380UL)
#define EPWM_TBCTL_CLKDIV_S      (10UL)
#define EPWM_TBCTL_CLKDIV_M      (0x1c00UL)
#define EPWM_TBCTR_TBCTR_M       (0xffffUL)
#define EPWM_TBCTR_TBCTR_S       (0UL)
#define EPWM_DBCTL_POLSEL_S      (2UL)
#define EPWM_DBCTL_POLSEL_M      (0xcUL)
#define EPWM_DBCTL_LOADREDMODE_S (6UL)
#define EPWM_DBCTL_LOADREDMODE_M (0xc0UL)
#define EPWM_DBCTL_LOADFEDMODE_S (8UL)
#define EPWM_DBCTL_LOADFEDMODE_M (0x300UL)
#define EPWM_DBRED_DBRED_M       (0x3fffUL)
#define EPWM_DBRED_DBRED_S       (0UL)
#define EPWM_DBFED_DBFED_M       (0x3fffUL)
#define EPWM_DBFED_DBFED_S       (0UL)
#define EPWM_TBPHS_TBPHS_S       (16UL)
#define EPWM_TBPHS_TBPHS_M       (0xffff0000UL)
#define EPWM_TBPRD_TBPRD_M       (0xffffUL)
#define EPWM_TBPRD_TBPRD_S       (0UL)
#define EPWM_CMPA_CMPA_S         (16UL)
#define EPWM_CMPA_CMPA_M         (0xffff0000UL)
#define EPWM_CMPB_CMPB_S         (16UL)
#define EPWM_CMPB_CMPB_M         (0xffff0000UL)
#define EPWM_CMPC_CMPC_S         (0UL)
#define EPWM_CMPC_CMPC_M         (0xffffUL)
/* TIH:160-167 原文 */
#define EPWM_DBCTL_OUT_MODE_S    (0UL)
#define EPWM_DBCTL_SHDWDBREDMODE (0x00000400UL)
#define EPWM_DBCTL_SHDWDBFEDMODE (0x00000800UL)

/* 枚举数值逐条按 EPWMH 原文(顺序枚举 = 前一个 +1):
 *  EPWMH:61-70   CLOCK_DIVIDER_1=0 .. _128=7
 *  EPWMH:80-89   HSCLOCK_DIVIDER_1=0, _2, **_4=2**, _6, _8, _10, _12, _14=7
 *  EPWMH:165-170 COUNTER_MODE_UP=0, DOWN=1, UP_DOWN=2, STOP_FREEZE=3
 *  EPWMH:244-249 COMPARE_A=0, B=2, C=5, D=7
 *  EPWMH:259-268 COMP_LOAD_ON_CNTR_ZERO=0
 *  EPWMH:343-346 AQ_OUTPUT_A=0, B=2 ; EPWMH:356-361 NO_CHANGE=0/LOW=1/HIGH=2/TOGGLE=3
 *  EPWMH:371-386 事件 = 直接当作位移量: ZERO=0, PERIOD=2, UP_CMPA=4, DOWN_CMPA=6,
 *                 UP_CMPB=8, DOWN_CMPB=10
 *  EPWMH:438-441 DB_RED=1, DB_FED=0 ; EPWMH:451-454 POLARITY_ACTIVE_HIGH=0/LOW=1
 *  EPWMH:479-499 RED/FED_LOAD_ON_CNTR_ZERO=0 */
typedef enum { EPWM_CLOCK_DIVIDER_1 = 0U, EPWM_CLOCK_DIVIDER_2, EPWM_CLOCK_DIVIDER_4,
               EPWM_CLOCK_DIVIDER_8, EPWM_CLOCK_DIVIDER_16, EPWM_CLOCK_DIVIDER_32,
               EPWM_CLOCK_DIVIDER_64, EPWM_CLOCK_DIVIDER_128 } EPWM_ClockDivider;
typedef enum { EPWM_HSCLOCK_DIVIDER_1 = 0U, EPWM_HSCLOCK_DIVIDER_2,
               EPWM_HSCLOCK_DIVIDER_4, EPWM_HSCLOCK_DIVIDER_6, EPWM_HSCLOCK_DIVIDER_8,
               EPWM_HSCLOCK_DIVIDER_10, EPWM_HSCLOCK_DIVIDER_12,
               EPWM_HSCLOCK_DIVIDER_14 } EPWM_HSClockDivider;
typedef enum { EPWM_COUNTER_MODE_UP = 0U, EPWM_COUNTER_MODE_DOWN,
               EPWM_COUNTER_MODE_UP_DOWN, EPWM_COUNTER_MODE_STOP_FREEZE }
               EPWM_TimeBaseCountMode;
typedef enum { EPWM_COUNTER_COMPARE_A = 0U, EPWM_COUNTER_COMPARE_B = 2U,
               EPWM_COUNTER_COMPARE_C = 5U, EPWM_COUNTER_COMPARE_D = 7U }
               EPWM_CounterCompareModule;
typedef enum { EPWM_COMP_LOAD_ON_CNTR_ZERO = 0U, EPWM_COMP_LOAD_ON_CNTR_PERIOD = 1U }
               EPWM_CounterCompareLoadMode;
typedef enum { EPWM_AQ_OUTPUT_A = 0U, EPWM_AQ_OUTPUT_B = 2U }
               EPWM_ActionQualifierOutputModule;
typedef enum { EPWM_AQ_OUTPUT_NO_CHANGE = 0U, EPWM_AQ_OUTPUT_LOW = 1U,
               EPWM_AQ_OUTPUT_HIGH = 2U, EPWM_AQ_OUTPUT_TOGGLE = 3U }
               EPWM_ActionQualifierOutput;
typedef enum { EPWM_AQ_OUTPUT_ON_TIMEBASE_ZERO = 0U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_PERIOD = 2U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPA = 4U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPA = 6U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPB = 8U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPB = 10U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPC = 12U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPC = 14U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_UP_CMPD = 16U,
               EPWM_AQ_OUTPUT_ON_TIMEBASE_DOWN_CMPD = 18U,
               EPWM_AQ_OUTPUT_ON_T1_COUNT_UP = 1U,
               EPWM_AQ_OUTPUT_ON_T1_COUNT_DOWN = 3U,
               EPWM_AQ_OUTPUT_ON_T2_COUNT_UP = 5U,
               EPWM_AQ_OUTPUT_ON_T2_COUNT_DOWN = 7U } EPWM_ActionQualifierOutputEvent;
typedef enum { EPWM_DB_FED = 0U, EPWM_DB_RED = 1U } EPWM_DeadBandDelayMode;
typedef enum { EPWM_DB_POLARITY_ACTIVE_HIGH = 0U,
               EPWM_DB_POLARITY_ACTIVE_LOW = 1U } EPWM_DeadBandPolarity;
typedef enum { EPWM_RED_LOAD_ON_CNTR_ZERO = 0U, EPWM_RED_LOAD_ON_CNTR_PERIOD = 1U }
               EPWM_RisingEdgeDelayLoadMode;
typedef enum { EPWM_FED_LOAD_ON_CNTR_ZERO = 0U, EPWM_FED_LOAD_ON_CNTR_PERIOD = 1U }
               EPWM_FallingEdgeDelayLoadMode;

/* ---- EPWMH 内联函数体(逐字复刻; 只有 WRITE/MODIFY 宏多了一个写计数) ---- */

/* EPWMH:1569-1573 —— RMW **保留 CTRMODE**(清除掩码只含 CLKDIV/HSPCLKDIV) */
static inline void EPWM_setClockPrescaler(EPWM_TypeDef *epwm,
                                          EPWM_ClockDivider prescaler,
                                          EPWM_HSClockDivider highSpeedPrescaler)
{
    ns800_leaf_tbctl_writes++;
    MODIFY_REG(epwm->TBCTL.WORDVAL, EPWM_TBCTL_CLKDIV_M | EPWM_TBCTL_HSPCLKDIV_M,
               ((uint32_t)prescaler << EPWM_TBCTL_CLKDIV_S) |
               ((uint32_t)highSpeedPrescaler << EPWM_TBCTL_HSPCLKDIV_S));
}

/* EPWMH:2492-2495 */
static inline void EPWM_disablePhaseShiftLoad(EPWM_TypeDef *epwm)
{
    ns800_leaf_tbctl_writes++;
    CLEAR_BIT(epwm->TBCTL.WORDVAL, EPWM_TBCTL_PHSEN_M);
}

/* EPWMH:2521-2524 (停机冻结时间基用的就是这一条: CTRMODE=STOP_FREEZE) */
static inline void EPWM_setTimeBaseCounterMode(EPWM_TypeDef *epwm,
                                               EPWM_TimeBaseCountMode counterMode)
{
    ns800_leaf_tbctl_writes++;
    MODIFY_REG(epwm->TBCTL.WORDVAL, EPWM_TBCTL_CTRMODE_M, (uint32_t)counterMode);
}

/* EPWMH:2836-2839 */
static inline void EPWM_setPhaseShift(EPWM_TypeDef *epwm, uint16_t phaseCount)
{
    MODIFY_REG(epwm->TBPHS.WORDVAL, (uint32_t)EPWM_TBPHS_TBPHS_M,
               (uint32_t)phaseCount << EPWM_TBPHS_TBPHS_S);
}

/* EPWMH:2865-2868 */
static inline void EPWM_setTimeBasePeriod(EPWM_TypeDef *epwm, uint16_t periodCount)
{
    WRITE_REG(epwm->TBPRD.WORDVAL, periodCount);
}

/* EPWMH:1497-1500 */
static inline void EPWM_setTimeBaseCounter(EPWM_TypeDef *epwm, uint16_t count)
{
    WRITE_REG(epwm->TBCTR.WORDVAL, count);
}

/* EPWMH:2960-2987
 * ⚠ 唯一与原文不同的地方: SDK 把寄存器地址存进 `uint32_t registerOffset`(目标 32 位
 * 成立)。主机是 64 位, 截断会得到野指针, 因此这里用 uintptr_t 存同一个表达式 ——
 * **偏移算术逐字不变**(`+ (compModule & 0x4)`)。 */
static inline void EPWM_setCounterCompareShadowLoadMode(EPWM_TypeDef *epwm,
                                                        EPWM_CounterCompareModule compModule,
                                                        EPWM_CounterCompareLoadMode loadMode)
{
    uint32_t syncModeOffset;
    uint32_t loadModeOffset;
    uint32_t shadowModeOffset;
    uintptr_t registerOffset;

    if ((compModule == EPWM_COUNTER_COMPARE_A) || (compModule == EPWM_COUNTER_COMPARE_C))
    {
        syncModeOffset = 10U;
        loadModeOffset = 0U;
        shadowModeOffset = 4U;
    }
    else
    {
        syncModeOffset = 12U;
        loadModeOffset = 2U;
        shadowModeOffset = 6U;
    }

    registerOffset = ((uintptr_t)&epwm->CMPCTL.WORDVAL + ((uint32_t)compModule & 0x4));

    MODIFY_REG(LEAF_TYPE32(registerOffset),
               ((0x3U << syncModeOffset) | (0x3U << loadModeOffset) | (0x1U << shadowModeOffset)),
               ((((uint32_t)loadMode >> 0x2U) << syncModeOffset) |
                (((uint32_t)loadMode & 0x3U) << loadModeOffset)));
}

/* EPWMH:3057-3068 (同上: 主机指针 64 位, registerOffset 用 uintptr_t, 偏移算术不变) */
static inline void EPWM_setCounterCompareValue(EPWM_TypeDef *epwm,
                                               EPWM_CounterCompareModule compModule,
                                               uint16_t compCount)
{
    uintptr_t registerOffset = (uintptr_t)&epwm->CMPA.WORDVAL + ((uint32_t)compModule << 2U);

    if ((compModule == EPWM_COUNTER_COMPARE_A) || (compModule == EPWM_COUNTER_COMPARE_B))
    {
        MODIFY_REG(LEAF_TYPE32(registerOffset), EPWM_CMPA_CMPA_M,
                   (uint32_t)(compCount) << EPWM_CMPA_CMPA_S);
    }
    else
    {
        MODIFY_REG(LEAF_TYPE32(registerOffset), EPWM_CMPC_CMPC_M, (uint32_t)compCount);
    }
}

/* EPWMH:3303-3321 */
static inline void EPWM_setActionQualifierAction(EPWM_TypeDef *epwm,
                                                 EPWM_ActionQualifierOutputModule epwmOutput,
                                                 EPWM_ActionQualifierOutput output,
                                                 EPWM_ActionQualifierOutputEvent event)
{
    if (((uint32_t)event & 0x1U) == 0x1U)
    {
        uint32_t shift_amount = ((uint32_t)event - 1U) % 32U;

        MODIFY_REG(LEAF_TYPE32(&(epwm->AQCTLA2.WORDVAL) + epwmOutput),
                   3U << shift_amount,
                   (uint32_t)output << shift_amount);
    }
    else
    {
        MODIFY_REG(LEAF_TYPE32(&(epwm->AQCTLA.WORDVAL) + epwmOutput),
                   3U << ((uint32_t)event),
                   (uint32_t)output << ((uint32_t)event));
    }
}

/* EPWMH:3590-3602 */
static inline void EPWM_setDeadBandDelayMode(EPWM_TypeDef *epwm,
                                             EPWM_DeadBandDelayMode delayMode,
                                             rt_bool_t enableDelayMode)
{
    uint16_t mask;

    mask = (uint16_t)1U << ((uint16_t)delayMode + EPWM_DBCTL_OUT_MODE_S);
    if (enableDelayMode) SET_BIT(epwm->DBCTL.WORDVAL, mask);
    else                 CLEAR_BIT(epwm->DBCTL.WORDVAL, mask);
}

/* EPWMH:3630-3635 */
static inline void EPWM_setDeadBandDelayPolarity(EPWM_TypeDef *epwm,
                                                 EPWM_DeadBandDelayMode delayMode,
                                                 EPWM_DeadBandPolarity polarity)
{
    uint8_t shift;

    shift = (uint16_t)((delayMode ^ 0x1U) + 2);
    MODIFY_REG(epwm->DBCTL.WORDVAL, 1U << shift, (uint32_t)polarity << shift);
}

/* EPWMH:3783-3787 / :3811-3814 / :3896-3899 */
static inline void EPWM_setRisingEdgeDelayCountShadowLoadMode(EPWM_TypeDef *epwm,
                                                              EPWM_RisingEdgeDelayLoadMode loadMode)
{
    MODIFY_REG(epwm->DBCTL.WORDVAL, EPWM_DBCTL_LOADREDMODE_M,
               (uint32_t)EPWM_DBCTL_SHDWDBREDMODE |
               (uint32_t)loadMode << EPWM_DBCTL_LOADREDMODE_S);
}
static inline void EPWM_disableRisingEdgeDelayCountShadowLoadMode(EPWM_TypeDef *epwm)
{
    CLEAR_BIT(epwm->DBCTL.WORDVAL, EPWM_DBCTL_SHDWDBREDMODE);
}
static inline void EPWM_setRisingEdgeDelayCount(EPWM_TypeDef *epwm, uint16_t redCount)
{
    WRITE_REG(epwm->DBRED.WORDVAL, redCount);
}

/* EPWMH:3840-3844 / :3868-3871 / :3924-3927 */
static inline void EPWM_setFallingEdgeDelayCountShadowLoadMode(EPWM_TypeDef *epwm,
                                                               EPWM_FallingEdgeDelayLoadMode loadMode)
{
    MODIFY_REG(epwm->DBCTL.WORDVAL, EPWM_DBCTL_LOADFEDMODE_M,
               (uint32_t)EPWM_DBCTL_SHDWDBFEDMODE |
               (uint32_t)loadMode << EPWM_DBCTL_LOADFEDMODE_S);
}
static inline void EPWM_disableFallingEdgeDelayCountShadowLoadMode(EPWM_TypeDef *epwm)
{
    CLEAR_BIT(epwm->DBCTL.WORDVAL, EPWM_DBCTL_SHDWDBFEDMODE);
}
static inline void EPWM_setFallingEdgeDelayCount(EPWM_TypeDef *epwm, uint16_t fedCount)
{
    WRITE_REG(epwm->DBFED.WORDVAL, fedCount);
}

/* ===================================================================== */
/* RCC —— 单位必须来自本地 SDK 原文, 不能猜                                */
/* ===================================================================== */
/* rcc.c:159-168 原文:
 *   @brief  Get the PCLK1 (APB1) Clock Frequency.
 *   @return Returns the PCLK1 (APB1) clock frequency in Hz.
 *   uint32_t RCC_getPclk1Frequency (void) { return (RCC_getHclkFrequency() >> RCC_getApb1ClkDiv()); }
 * 上游 rcc.c:141/152 同样写 "in Hz"; rcc.c:108-136 的 SYSCLK 来自 HXTL_FREQ_VALUE
 * (ROOTH:53 = 20000000UL) 经 PLL。本板构建定义 SYSCLK_USE_PLL / SYSCLK_SOURCE_USE_HXTL /
 * PLLCLK_SOURCE_USE_HXTL (board/SConscript:15), board.h:118-121 的 PLL_CONFIG =
 * PLLM_40 / PLLN_DIV1 / PLLP_DIV2 且 board.h:171-175 配 HCLK=CPUCLK/2、APB1DIV=0
 * => RCC_getPclk1Frequency() = 400000000>>1>>0 = 200000000 (Hz)。
 * 主机侧由夹具提供实现(值 = 上面这条链的**原文换算结果**), 并允许注入以检查
 * "换算只发生在门外": ns800_leaf_pclk1_calls 计 prepare 实际取用了多少次。 */
uint32_t RCC_getPclk1Frequency(void);

extern volatile uint32_t ns800_leaf_pclk1_calls;   /* 夹具定义: prepare 取时钟的次数 */

#endif /* NS800_LEAF_DRV_COMMON_H */
