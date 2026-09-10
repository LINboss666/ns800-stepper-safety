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
#define PIN_NAME_DRV_ENABLE     "PF.21"  /* J4-20  GPIO31 software drive permit, default LOW */
/* STEP = PA0 / EPWM1_A (J4-18), PA1 reserved EPWM1_B - never reuse PA1 */

/* ===== Safety inputs ===== */
#define PIN_NAME_LIMIT_MIN      "PF.14"  /* J4-25  GPIO25 min limit IRQ (EXTI14) */
#define PIN_NAME_LIMIT_MAX      "PF.15"  /* J4-25  GPIO25 max limit IRQ (EXTI15) */
#define PIN_NAME_ESTOP          "PC.6"   /* J4-40  GPIO70 e-stop sense IRQ (EXTI6) */

/* ===== SPI bus devices (shared spi1) ===== */
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
#define SENSOR_SPI_BUS_NAME     "spi1"
#define CURRENT_ADC_DEVICE_NAME "adc0"       /* ADCA, PH2 = channel 15 */
#define CURRENT_ADC_CHANNEL     15
#define EPWM_STEP_DEV_NAME      "epwm1"      /* verify exact name against local drv_epwm.c before use */

/* J4 connector reference (from handoff section 4, frozen):
 * RUN_LED=J4-38, WARN_LED=J4-37, FAULT_LED=J4-36 (do NOT use old J4-36/35/34 order)
 * LIMIT_MIN=J4-19(PF.14), MCU_DRV_ENABLE=J4-20(PF.21), LIMIT_MAX=J4-25(PF.15) */

#endif /* PROJECT_BOARD_H */
