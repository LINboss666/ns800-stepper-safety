/*
 * safety_gpio.h - 安全 GPIO 子系统对外接口
 */
#ifndef SAFETY_GPIO_H
#define SAFETY_GPIO_H

#include <rtthread.h>

/* 安全 GPIO 是否全部解析并初始化成功 */
rt_bool_t safety_gpio_ready(void);

/* P1-8: bootstrap 显式调用(幂等); 业务模块不再依赖 INIT_APP 链接顺序 */
rt_err_t safety_gpio_boot(void);

/* 按引脚名字(project_board.h 中的 PIN_NAME_xxx)取已解析的 pin 号, 失败返回 <0 */
rt_base_t safety_pin(const char *name);

#endif /* SAFETY_GPIO_H */
