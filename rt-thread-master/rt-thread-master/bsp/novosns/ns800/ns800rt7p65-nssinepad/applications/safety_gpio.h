/*
 * safety_gpio.h - 安全 GPIO 子系统对外接口
 */
#ifndef SAFETY_GPIO_H
#define SAFETY_GPIO_H

#include <rtthread.h>

/* 安全 GPIO 是否全部解析并初始化成功 */
rt_bool_t safety_gpio_ready(void);

/* P1-8: bootstrap 显式调用(幂等); 业务模块不再依赖 INIT_APP 链接顺序。
 * Fix A: 返回真实结果 —— 任一引脚解析/初始化失败即 -RT_EINVAL, 调用方
 * (supervisor_boot stage 1)必须据此 fail closed, 不再吞掉错误。 */
rt_err_t safety_gpio_boot(void);

/* 按引脚名字(project_board.h 中的 PIN_NAME_xxx)取已解析的 pin 号, 失败返回 <0 */
rt_base_t safety_pin(const char *name);

/* ---------- MCU_DRV_ENABLE(PC.23 / J4-21)安全关键输出 ----------
 * rt_pin_write() 无返回值, 写不进/被顶高都无从得知(BUG-009 同类风险),
 * 因此该脚的所有写操作必须经下面的"写 + 回读"确认。回读走 GPIO_readPin
 * (DAT = 焊盘实际电平), 只能证明 MCU 焊盘电平, 不替代 ENN 整链硬件验收。 */

/* 写使能请求并回读确认(readback == level)。含短暂 settle + 重试 1 次。
 * RT_EOK = 焊盘实测等于 level; -RT_EIO = 回读不符; -RT_EINVAL = 引脚未解析。 */
rt_err_t safety_drv_enable_write(rt_uint8_t level);

/* 只读实测: 焊盘当前是否确认为 LOW(禁止驱动)。引脚未解析一律返回 FALSE。 */
rt_bool_t safety_drv_enable_is_low(void);

#endif /* SAFETY_GPIO_H */
