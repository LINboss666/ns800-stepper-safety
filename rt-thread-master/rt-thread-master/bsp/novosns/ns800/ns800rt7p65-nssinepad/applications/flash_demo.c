#include <rtthread.h>
#include <rtdevice.h>
#include <finsh.h>

#include "drv_gpio.h"

/* SPI bus/device name */
#define FLASH_SPI_BUS_NAME       "spi1"
#define FLASH_SPI_DEVICE_NAME    "spi10"

/*
 * GPIO19 = PA19
 * NSS is used as GPIO controlled CS
 */
#define FLASH_CS_PIN    PIN_NUM(GPIOA, GPIO_PIN_19)

/* JEDEC Read ID command */
#define FLASH_CMD_JEDEC_ID       0x9F

static struct rt_spi_device flash_spi_device;
static struct rt_spi_device *flash_dev = RT_NULL;


/*
 * Initialize SPI FLASH device
 */
static int flash_spi_init(void)
{
    struct rt_spi_configuration cfg;
    rt_err_t ret;

    /*
     * First confirm SPI1 bus exists
     */
    if (rt_device_find(FLASH_SPI_BUS_NAME) == RT_NULL)
    {
        rt_kprintf("[FLASH] cannot find %s\n",
                   FLASH_SPI_BUS_NAME);

        rt_kprintf("[FLASH] check menuconfig: Enable SPI1\n");

        return -RT_ERROR;
    }

    /*
     * Attach FLASH to SPI1
     *
     * GPIO19 is used as CS
     */
    if (rt_device_find(FLASH_SPI_DEVICE_NAME) == RT_NULL)
    {
        ret = rt_spi_bus_attach_device_cspin(
            &flash_spi_device,
            FLASH_SPI_DEVICE_NAME,
            FLASH_SPI_BUS_NAME,
            FLASH_CS_PIN,
            RT_NULL);

        if (ret != RT_EOK)
        {
            rt_kprintf("[FLASH] attach device failed: %d\n",
                       ret);

            return ret;
        }
    }

    flash_dev =
        (struct rt_spi_device *)
        rt_device_find(FLASH_SPI_DEVICE_NAME);

    if (flash_dev == RT_NULL)
    {
        rt_kprintf("[FLASH] cannot find %s\n",
                   FLASH_SPI_DEVICE_NAME);

        return -RT_ERROR;
    }

    /*
     * SPI NOR Flash:
     *
     * Master
     * Mode 0
     * MSB first
     * 8 bit
     *
     * First test uses only 1 MHz.
     * Slow and stable for Dupont wires.
     */
    cfg.data_width = 8;

    cfg.mode =
        RT_SPI_MASTER |
        RT_SPI_MODE_0 |
        RT_SPI_MSB;

    cfg.max_hz = 1000000;

    ret = rt_spi_configure(
        flash_dev,
        &cfg);

    /* New rt_spi_bus_configure returns -RT_EBUSY when another device owns the
     * bus; the config then takes effect on the next transfer. Not fatal. */
    if (ret != RT_EOK && ret != -RT_EBUSY)
    {
        rt_kprintf("[FLASH] configure failed: %d\n",
                   ret);

        return ret;
    }

    return RT_EOK;
}


/*
 * MSH command:
 *
 * flash_id
 */
static void flash_id(void)
{
    rt_uint8_t cmd;
    rt_uint8_t id[3];
    rt_err_t ret;

    if (flash_spi_init() != RT_EOK)
    {
        return;
    }

    cmd = FLASH_CMD_JEDEC_ID;

    id[0] = 0;
    id[1] = 0;
    id[2] = 0;

    /*
     * Send:
     *
     * 9F
     *
     * Receive:
     *
     * Manufacturer ID
     * Memory Type
     * Capacity
     */
    ret = rt_spi_send_then_recv(
        flash_dev,
        &cmd,
        1,
        id,
        3);

    if (ret != RT_EOK)
    {
        rt_kprintf("[FLASH] SPI transfer failed: %d\n",
                   ret);

        return;
    }

    rt_kprintf("\n");
    rt_kprintf("SPI FLASH JEDEC ID\n");
    rt_kprintf("------------------\n");

    rt_kprintf(
        "Manufacturer : 0x%02X\n",
        id[0]);

    rt_kprintf(
        "Memory Type  : 0x%02X\n",
        id[1]);

    rt_kprintf(
        "Capacity     : 0x%02X\n",
        id[2]);

    rt_kprintf(
        "JEDEC ID     : %02X %02X %02X\n",
        id[0],
        id[1],
        id[2]);

    rt_kprintf("------------------\n");

    if ((id[0] == 0x00) &&
        (id[1] == 0x00) &&
        (id[2] == 0x00))
    {
        rt_kprintf("FLASH TEST FAILED: all 00\n");
    }
    else if ((id[0] == 0xFF) &&
             (id[1] == 0xFF) &&
             (id[2] == 0xFF))
    {
        rt_kprintf("FLASH TEST FAILED: all FF\n");
    }
    else
    {
        rt_kprintf("FLASH SPI COMMUNICATION OK\n");
    }
}

MSH_CMD_EXPORT(flash_id, read SPI FLASH JEDEC ID);