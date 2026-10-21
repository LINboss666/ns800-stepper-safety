/*
 * Copyright (c) 2006-2026, RT-Thread Development Team
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * NS800 stepper safety system - frozen board signal map
 * Source: Agent handoff doc section 4/24/32 (frozen 2026-09-10).
 * All pins MUST be resolved via rt_pin_get() with return value checked.
 * Do NOT use GPIO12/13 (UART1 console), do NOT use PA1 (reserved EPWM1_B).
 */

#ifndef PROJECT_BOARD_H
#define PROJECT_BOARD_H

/* ===== Motor control ===== */
#define PIN_NAME_TMC_DIR        "PA.2"   /* J4-16  GPIO2  direction output */
#define PIN_NAME_TMC_DIAG       "PA.3"   /* J4-15  GPIO3  stall/diag IRQ (EXTI3) */
/* BUG-009: DRV_ENABLE 从 PF.21(J4-20) 迁移到 PC.23(J4-21)——PF21 焊盘被芯片内
 * 另一驱动源顶高(推挽低只能拉到2.37V), 输出不可用, 详见 调试记录.md BUG-009 */
#define PIN_NAME_DRV_ENABLE     "PC.23"  /* J4-21  GPIO87 software drive permit, default LOW */
/* STEP = PA0 / EPWM1_A (J4-18), PA1 reserved EPWM1_B - never reuse PA1 */

/* ===== Safety inputs =====
 * 限位两路只在 SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE 时进入解析/配置/读取/IRQ;
 * 电平依据 SCH_Schematic1_2026-10-19.pdf(RevA/V1.0): 常闭触点→上拉→RC→U12/U11 反相,
 * 所以反相后的 LIMIT_*_OK 是 **HIGH=安全、LOW=触发**, IRQ 用下降沿。 */
#define PIN_NAME_LIMIT_MIN      "PF.14"  /* J4-19  GPIO24 min limit OK (EXTI14) */
#define PIN_NAME_LIMIT_MAX      "PF.15"  /* J4-25  GPIO25 max limit OK (EXTI15) */

/* PC.6(J4-40/GPIO70/EXTI6) 的实际接线由用户 2026-10-21 确认: 接到扩展板 **H6-5 = TMC_ENN**,
 * 是**驱动使能状态反馈输入**(被动读), 不是急停按钮/急停感测输入。
 * 板上急停是纯硬件链(常闭触点→U13 反相→U5 与门→Q1), 没有送进 MCU(图纸全网标核对),
 * 因此软件不存在独立的急停输入 GPIO, 也不得再按"LOW 安全/上升沿"解释这颗脚。
 * ENN 为低有效使能输入(TMC2209 Datasheet Rev.1.09 印刷第 9 页 §2.2):
 * 反馈 HIGH = 功率级已关断/输出浮空(禁能侧); 反馈 LOW = 未施加禁能(不保证有转矩)。 */
#define PIN_NAME_ENN_FEEDBACK   "PC.6"   /* J4-40  GPIO70 TMC_ENN state feedback (input only) */

/* ⚠ 遗留别名(本轮不授权改 sensor_service.c/blackbox): 传感器帧的 estop 位与黑匣子
 * record.flags bit8 历史名字叫 estop, 读的正是这颗脚。这里把它指向同一颗 ENN 反馈脚,
 * 使未修改的模块继续可编译; 该位的**真实含义已变为 ENN 反馈电平**, 需要单独一轮改名。
 * 安全逻辑(safety_gpio/safety_thread/safety_state/motor)一律不再使用本别名。 */
#define PIN_NAME_ESTOP          PIN_NAME_ENN_FEEDBACK

/* ===== 可选行程限位(功能启用开关, 与"硬件验证宏"职责不同, 不可互相替代) =====
 * RT_FALSE(目标默认): 两路限位不进解析/配置/读取/IRQ 注册, 也不参与就绪与故障清除判定;
 *                    实现原样保留, 不删除, 不靠 GND 跳线或伪造 LOW 来"通过"。
 * RT_TRUE:           两路限位真实接入 GPIO 初始化/健康度、输入读取、下降沿 IRQ、
 *                    保护就绪、必要自检与故障清除。HIGH=安全, LOW=触发。
 * 非法取值一律编译失败(RT_TRUE/RT_FALSE 只接受这两个)。
 * ⚠ 主机侧 LIMIT-ON 分支通过 ≠ 限位已上板验收。 */
#ifndef SAFETY_LIMIT_INPUTS_ENABLED
#define SAFETY_LIMIT_INPUTS_ENABLED   RT_FALSE
#endif

/* 取值合法性在编译期钉住: 写错(2、TRUE、拼错的宏名按 0 处理之外的任何值)不允许静默生效。
 * 依赖包含顺序: 全仓所有消费者都先 #include <rtthread.h> 再包含本文件(已逐文件核对)。 */
#if !(SAFETY_LIMIT_INPUTS_ENABLED == RT_FALSE || SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE)
#error "SAFETY_LIMIT_INPUTS_ENABLED must be RT_FALSE or RT_TRUE"
#endif

/* ===== SPI bus devices ===== */
/* Flash 独占硬件 SPI1 (PA16/17/18, J2-9/10/11)。
 * ADXL345 使用硬件 SPI3: 2026-09-12 修正 BSP drv_spi.c SPI3 引脚表(原 PC0/1/2@ALT7
 * 与官方 mux 表不符, GPIO_64/65/66 无 SPI3 复用, 详见 调试记录.md)。
 * SPI3 引脚组: SCK=GPIO_52(J3-34) MOSI(SIMO)=GPIO_50(J3-36) MISO(SOMI)=GPIO_51(J3-35) @ALT6 */
#define PIN_NAME_FLASH_CS       "PF.12"  /* J2-12  GPIO22 expansion flash CS */
#define PIN_NAME_IMU_CS         "PA.20"  /* J2-14  GPIO20 ADXL345 CS */
#define PIN_NAME_IMU_INT1       "PA.21"  /* J2-15  GPIO21 ADXL345 INT1 IRQ (EXTI5) */

/* ===== UI ===== */
#define PIN_NAME_BUZZER         "PC.19"  /* J4-26  GPIO83 */
#define PIN_NAME_RUN_LED        "PC.8"   /* J4-38  GPIO72 */
#define PIN_NAME_WARN_LED       "PC.9"   /* J4-37  GPIO73 */
#define PIN_NAME_FAULT_LED      "PC.10"  /* J4-36  GPIO74 */

/* ===== Devices ===== */
#define TMC_UART_DEVICE_NAME    "uart2"      /* PB6 TX / PB7 RX */
#define SENSOR_SPI_BUS_NAME     "spi3"   /* IMU 专用, Flash 仍为 spi1 */
#define CURRENT_ADC_DEVICE_NAME "adc0"       /* ADCA, PH2 = channel 15 */
#define CURRENT_ADC_CHANNEL     15
#define EPWM_STEP_DEV_NAME      "epwm1"      /* verify exact name against local drv_epwm.c before use */

/* J4 connector reference (from handoff section 4, frozen):
 * RUN_LED=J4-38, WARN_LED=J4-37, FAULT_LED=J4-36 (do NOT use old J4-36/35/34 order)
 * LIMIT_MIN=J4-19(PF.14), MCU_DRV_ENABLE=J4-21(PC.23, PF.21/J4-20 弃用见 BUG-009),
 * LIMIT_MAX=J4-25(PF.15), ENN_FEEDBACK=J4-40(PC.6, 旧名 ESTOP_SENSE 已废止)
 * 扩展板侧对应(2026-10-21 按 RevA 图纸 + 用户确认): MCU_DRV_ENABLE→H7-10→U5(与门)输入A,
 * H6-5=TMC_ENN(反馈来源), 限位 OK 信号在 H2-1/H2-2(现板未插, 限位宏默认关闭)。 */

#endif /* PROJECT_BOARD_H */
