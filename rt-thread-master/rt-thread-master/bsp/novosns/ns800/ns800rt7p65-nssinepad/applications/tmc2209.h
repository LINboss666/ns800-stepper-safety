/*
 * tmc2209.h - TMC2209 UART 协议层正式 API (Phase 7-A)
 *
 * 硬件映射(冻结, 勿动): uart2 / PB6=TX / PB7=RX / 115200 8N1 /
 * TX 经 1k 与 RX 汇合到单线 PDN_UART / slave address 0。
 *
 * 底层协议算法(CRC8-ATM LSB-first、帧格式、回声过滤、应答头搜索)已经真机验证
 * (READ FOUND + WRITE IFCNT+1, 4 帧应答 CRC 独立核算一致), 本 API 只做封装,
 * 禁止改动协议实现细节。
 *
 * 约定(见 app_health.h):
 *   - 读函数非 RT_EOK 时输出参数无效
 *   - WRITE 在 TMC2209 上无应答; write_register_confirmed() 用 IFCNT 自增验证
 *     写入被芯片接受(禁止用 OTP 做测试)
 *   - health: OK=链路通且最近一次传输成功; DEGRADED=链路通但最近一次失败;
 *     FAILED=uart2 打不开; UNINIT=尚未初始化
 */
#ifndef TMC2209_H
#define TMC2209_H

#include <rtthread.h>
#include "app_health.h"

#define TMC_ADDR_DEFAULT 0    /* 单芯片网络, 默认 slave 地址 0 */

/* 寄存器地址(常用) */
#define TMC_REG_GCONF     0x00
#define TMC_REG_GSTAT     0x01
#define TMC_REG_IFCNT     0x02
#define TMC_REG_IOIN      0x06
#define TMC_REG_IHOLD_IRUN 0x10
#define TMC_REG_SG_RESULT 0x41

/* 幂等初始化: 打开 uart2(115200 8N1)。RT_EOK = 链路层就绪。
 * 注意: 链路就绪 ≠ 芯片在位, 在位验证用 tmc2209_write_register_confirmed()
 * 或 MSH 命令 tmc_uart_probe / tmc_scan。 */
rt_err_t tmc2209_init(void);

/* 读寄存器。addr 一般为 TMC_ADDR_DEFAULT。RT_EOK 时 *value 有效。 */
rt_err_t tmc2209_read_register(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);

/* 写寄存器 + 到达确认: 发送 WRITE 帧后读 IFCNT 前后各一次,
 * 验证 (after == before+1) mod 256 —— TMC2209 对 WRITE 无应答,
 * IFCNT 自增是写入被接受的唯一凭证。确认失败返回错误。 */
rt_err_t tmc2209_write_register_confirmed(rt_uint8_t addr, rt_uint8_t reg,
                                          rt_uint32_t value);

/* 读 StallGuard 结果(0x41)。电机静止时通常接近 0; 驱动使能转动后有意义。 */
rt_err_t tmc2209_read_sg_result(rt_uint16_t *sg_result);

/* 读全局状态 GSTAT(0x01): bit0 reset / bit1 driver_error / bit2 sg2。 */
rt_err_t tmc2209_read_status(rt_uint8_t *gstat);

/* 子系统健康(UNINIT/OK/DEGRADED/FAILED) */
subsys_health_t tmc2209_get_health(void);

#endif /* TMC2209_H */
