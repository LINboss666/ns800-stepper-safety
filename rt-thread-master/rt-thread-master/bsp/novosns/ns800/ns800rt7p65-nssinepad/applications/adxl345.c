/*
 * adxl345.c - ADXL345 三轴加速度计驱动 (SPI1 共享总线, 交接文档 §6.2)
 *
 * 硬件: CS=PA.20(J2-14), INT1=PA.21(J2-15, EXT5, 暂未用)
 * 时序: 4线 SPI Mode3(CPOL=1,CPHA=1), 1MHz bring-up(上限5MHz), 8bit
 * 协议: 地址字节 bit7=RW(1读), bit6=MB(多字节自增)
 *
 * 防复发(调试记录.md):
 *   BUG-001 CS 常高: safety_gpio 上电已把 PA.20 拉高, 这里发送前再次确保
 *   BUG-002 rt_spi_configure 返回 -RT_EBUSY 不算错误(总线被 Flash 占用)
 *
 * 状态: 代码就绪; DEVID=0xE5 待接线验证. 读不到 0xE5 时如实报错,
 *       按交接文档 §6.2 检查单排查, 禁止继续配置.
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"

#define ADXL_BUS_NAME   SENSOR_SPI_BUS_NAME     /* "spi1" */
#define ADXL_DEV_NAME   "adxl345"
#define ADXL_SPI_HZ     1000000u

/* 寄存器 */
#define ADXL_DEVID      0x00
#define ADXL_BW_RATE    0x2C
#define ADXL_POWER_CTL  0x2D
#define ADXL_DATA_FORMAT 0x31
#define ADXL_DATAX0     0x32

#define ADXL_DEVID_VAL  0xE5

static struct rt_spi_device adxl_spi;
static struct rt_spi_device *adxl_dev = RT_NULL;
static rt_bool_t adxl_attached = RT_FALSE;

/* 单寄存器读: 地址|0x80 */
static rt_err_t adxl_read_reg(rt_uint8_t reg, rt_uint8_t *val)
{
    rt_uint8_t tx = reg | 0x80;
    return rt_spi_send_then_recv(adxl_dev, &tx, 1, val, 1);
}

/* 单寄存器写 */
static rt_err_t adxl_write_reg(rt_uint8_t reg, rt_uint8_t val)
{
    rt_uint8_t tx[2];
    tx[0] = reg & 0x3F;
    tx[1] = val;
    return rt_spi_send(adxl_dev, tx, 2) == 2 ? RT_EOK : -RT_EIO;
}

/* 多字节读: 地址|0x80|0x40 */
static rt_err_t adxl_read_regs(rt_uint8_t reg, rt_uint8_t *buf, rt_size_t n)
{
    rt_uint8_t tx = reg | 0x80 | 0x40;
    return rt_spi_send_then_recv(adxl_dev, &tx, 1, buf, n);
}

static rt_err_t adxl_attach(void)
{
    struct rt_spi_configuration cfg;
    rt_err_t e;

    if (adxl_attached) return RT_EOK;

    if (rt_device_find(ADXL_BUS_NAME) == RT_NULL) return -RT_ERROR;
    if (rt_device_find(ADXL_DEV_NAME) != RT_NULL) return -RT_EBUSY;

    e = rt_spi_bus_attach_device_cspin(&adxl_spi, ADXL_DEV_NAME,
                                       ADXL_BUS_NAME,
                                       safety_pin(PIN_NAME_IMU_CS), RT_NULL);
    if (e != RT_EOK) return e;

    adxl_dev = (struct rt_spi_device *)rt_device_find(ADXL_DEV_NAME);
    if (adxl_dev == RT_NULL) return -RT_ERROR;

    rt_memset(&cfg, 0, sizeof(cfg));
    cfg.data_width = 8;
    cfg.mode = RT_SPI_MASTER | RT_SPI_MODE_3 | RT_SPI_MSB;
    cfg.max_hz = ADXL_SPI_HZ;
    e = rt_spi_configure(adxl_dev, &cfg);
    /* BUG-002: -RT_EBUSY = 配置待总线空闲后生效, 放行 */
    if (e != RT_EOK && e != -RT_EBUSY) return e;

    adxl_attached = RT_TRUE;
    return RT_EOK;
}

static void imu_id(void)
{
    rt_uint8_t id = 0;
    rt_err_t e = adxl_attach();

    if (e != RT_EOK) { rt_kprintf("[IMU] attach failed: %d\n", e); return; }

    /* 发送前再次确保 CS 常高(BUG-001 双保险) */
    rt_pin_write(safety_pin(PIN_NAME_IMU_CS), PIN_HIGH);

    e = adxl_read_reg(ADXL_DEVID, &id);
    if (e != RT_EOK) { rt_kprintf("[IMU] spi transfer failed: %d\n", e); return; }

    if (id == ADXL_DEVID_VAL)
        rt_kprintf("[IMU] DEVID=0x%02X - ADXL345 detected\n", id);
    else
        rt_kprintf("[IMU] DEVID=0x%02X (expect 0x%02X) - NOT DETECTED\n"
                   "[IMU] check: CS/SCK/MOSI/MISO wiring, Mode3, power, common GND\n",
                   id, ADXL_DEVID_VAL);
}
MSH_CMD_EXPORT(imu_id, read ADXL345 DEVID register);

static void imu_raw(void)
{
    rt_uint8_t id = 0, buf[6] = {0};
    rt_int16_t x, y, z;
    rt_err_t e = adxl_attach();

    if (e != RT_EOK) { rt_kprintf("[IMU] attach failed: %d\n", e); return; }
    rt_pin_write(safety_pin(PIN_NAME_IMU_CS), PIN_HIGH);

    e = adxl_read_reg(ADXL_DEVID, &id);
    if (e != RT_EOK || id != ADXL_DEVID_VAL)
    {
        rt_kprintf("[IMU] DEVID=0x%02X - 未检测到 ADXL345, 先跑 imu_id 排查\n", id);
        return;
    }

    /* 基础配置: 100Hz, ±2g (默认量程 3.9mg/LSB) */
    adxl_write_reg(ADXL_POWER_CTL, 0x00);          /* standby */
    adxl_write_reg(ADXL_BW_RATE, 0x0A);            /* 100Hz */
    adxl_write_reg(ADXL_DATA_FORMAT, 0x00);        /* ±2g, 非全分辨率 */
    adxl_write_reg(ADXL_POWER_CTL, 0x08);          /* measure */

    e = adxl_read_regs(ADXL_DATAX0, buf, 6);
    if (e != RT_EOK) { rt_kprintf("[IMU] read XYZ failed: %d\n", e); return; }

    x = (rt_int16_t)((buf[1] << 8) | buf[0]);
    y = (rt_int16_t)((buf[3] << 8) | buf[2]);
    z = (rt_int16_t)((buf[5] << 8) | buf[4]);

    rt_kprintf("[IMU] raw X=%d Y=%d Z=%d  (%d, %d, %d) mg @2g\n",
               x, y, z, x * 39 / 10, y * 39 / 10, z * 39 / 10);
    rt_kprintf("[IMU] 静置时某一轴应约 ±1000mg(1g 重力), 敲击应有明显变化\n");
}
MSH_CMD_EXPORT(imu_raw, read ADXL345 XYZ raw values);
