/* shim_flash/drv_gpio.h - 只满足 ns_flash_config.h 的编译期引脚标记。
 * 数值不参与本链任何判定; flash_info 的打印不在本链执行。 */
#ifndef SHIM_FLASH_DRV_GPIO_H
#define SHIM_FLASH_DRV_GPIO_H

#define GPIOF ((void *)0)
#define GPIOA ((void *)0)
#define GPIO_PIN_12 12
#define GPIO_PIN_20 20
#define PIN_NUM(port, pin) ((int)(pin))

#endif
