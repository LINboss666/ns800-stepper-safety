#ifndef NS_FLASH_CONFIG_H
#define NS_FLASH_CONFIG_H
#include "drv_gpio.h"
/* Rev.A extension board. Verify the actual PCB FLASH_CS net before writing. */
#define NSF_BUS_NAME       "spi1"
#define NSF_DEVICE_NAME    "sflash"
/* Two flash devices share SPI1: external module at J2-12/GPIO22 (PF.12) and
 * onboard U4 net at J2-13/PA19. Both CS must idle HIGH (see main.c) or MISO
 * contention corrupts reads. Storage/blackbox targets the external module. */
#define NSF_CS_PIN         PIN_NUM(GPIOF, GPIO_PIN_12) /* J2-12 external module */
#define NSF_ONBOARD_CS_PIN PIN_NUM(GPIOA, GPIO_PIN_19) /* J2-13 onboard U4 */
#define NSF_IMU_CS_PIN     PIN_NUM(GPIOA, GPIO_PIN_20) /* J2-14 */
#define NSF_SPI_HZ         1000000u
#define NSF_CAPACITY       0x800000u
#define NSF_SECTOR_SIZE    4096u
#define NSF_PAGE_SIZE      256u
#define NSF_PROGRAM_MS     50
#define NSF_ERASE_MS       1500
/* Boot resources only; no flash erase/write occurs automatically. */
#define NS_LOG_QUEUE_DEPTH 16
#define NS_LOG_STACK_SIZE  4096
#define NS_LOG_PRIORITY    (RT_THREAD_PRIORITY_MAX - 3)
#endif
