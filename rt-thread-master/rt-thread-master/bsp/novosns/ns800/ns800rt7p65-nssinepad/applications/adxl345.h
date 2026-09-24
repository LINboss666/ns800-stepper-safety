/*
 * adxl345.h - ADXL345 三轴加速度计正式 API (Phase 7-A)
 *
 * 总线: 硬件 spi3 (SCK=GPIO_52/J3-34, SIMO=GPIO_50/J3-36, SOMI=GPIO_51/J3-35,
 *       BSP drv_spi.c SPI3 引脚表已按官方 mux 表修正, 见 BUG-012/BUG-013)
 * CS  : PA.20 (J2-14, 软件 CS, safety_gpio 上电常高)
 * INT1: PA.21 (J2-15, EXTI5, 线已接, 中断功能未验证)
 *
 * 真机验证事实（勿降级为 pending）:
 *   DEVID=0xE5 双读一致；静置三轴合成 ≈1g；SPI1 Flash 回归不受影响。
 *
 * 使用约定（见 app_health.h）:
 *   - adxl345_init() 幂等：OK 状态下重复调用直接成功；FAILED 后重调会重试
 *   - 读函数非 RT_EOK 时输出参数无效，调用方必须检查返回值
 *   - 线程可直接调用本 API；MSH 命令(imu_probe/imu_id/imu_raw)只是薄封装
 */
#ifndef ADXL345_H
#define ADXL345_H

#include <rtthread.h>
#include "app_health.h"

/* 完整初始化: attach 设备 → 校验 DEVID → 配置 BW_RATE/DATA_FORMAT/POWER_CTL。
 * 每一步都检查返回值; DEVID 不匹配则 FAILED 并停止配置(交接文档 §6.2)。
 * 返回 RT_EOK = 初始化完成(或已处于 OK); 其它值 = FAILED, 不得使用测量值。 */
rt_err_t adxl345_init(void);

/* 读三轴原始码(有符号 16bit, ±2g 时 3.9mg/LSB)。
 * RT_EOK 时 x/y/z 三个输出全部有效; 否则无效。 */
rt_err_t adxl345_read_raw(rt_int16_t *x, rt_int16_t *y, rt_int16_t *z);

/* 读三轴毫 g 值(按当前量程换算)。
 * RT_EOK 时输出有效; 否则无效。 */
rt_err_t adxl345_read_mg(rt_int16_t *mg_x, rt_int16_t *mg_y, rt_int16_t *mg_z);

/* 读取 INT_SOURCE 寄存器(0x30): 反映本次采样期间触发的事件位。
 * FIFO/INT1 中断路径的预留接口 —— 本阶段只提供读值, 不实现中断 worker。 */
rt_err_t adxl345_read_int_source(rt_uint8_t *int_source);

/* 预留接口(Phase 7-C/D 实现, 当前返回 -RT_ENOSYS):
 *   adxl345_fifo_configure()  / adxl345_fifo_read()
 *   adxl345_int1_attach(cb)   —— EXTI5 → 事件链 */
rt_err_t adxl345_fifo_configure(void);
rt_err_t adxl345_fifo_read(void);
rt_err_t adxl345_int1_attach(void (*cb)(void *args), void *args);

/* 子系统健康(UNINIT/OK/DEGRADED/FAILED) */
subsys_health_t adxl345_get_health(void);

#endif /* ADXL345_H */
