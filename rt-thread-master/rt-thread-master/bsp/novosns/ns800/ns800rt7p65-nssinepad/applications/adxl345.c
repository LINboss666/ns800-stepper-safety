/*
 * adxl345.c - ADXL345 三轴加速度计驱动 (硬件 SPI3 独占总线, 交接文档 §6.2)
 *
 * 硬件: CS=PA.20(J2-14, 软件 CS), INT1=PA.21(J2-15, EXTI5, 线已接/中断未验证)
 * 总线: spi3 = SCK=GPIO_52(J3-34), MOSI(SIMO)=GPIO_50(J3-36),
 *       MISO(SOMI)=GPIO_51(J3-35), 全部 ALT6 (2026-09-12 修正 BSP SPI3 引脚表,
 *       原 PC0/1/2@ALT7 与官方 mux 表不符, 详见 调试记录.md BUG-012/BUG-013)
 * 时序: 4线 SPI Mode3(CPOL=1,CPHA=1), 1MHz bring-up(上限5MHz), 8bit
 * 协议: 地址字节 bit7=RW(1读), bit6=MB(多字节自增)
 *
 * 防复发(调试记录.md):
 *   BUG-001 CS 常高: safety_gpio 上电已把 PA.20 拉高, 这里发送前再次确保
 *   BUG-002 rt_spi_configure 返回 -RT_EBUSY 不算错误(总线被其它设备占用)
 *
 * 真机验证(勿降级): DEVID=0xE5 双读一致; 静置三轴合成≈1g; SPI1 Flash 回归 PASS。
 *
 * 正式 API 见 adxl345.h; 本文件内 MSH 命令(imu_probe/imu_id/imu_raw)为薄封装。
 * 采样时序注意: 写 POWER_CTL 使能测量后首帧转换未就绪, 读数会为全 0, 已加延时。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"
#include "safety_gpio.h"
#include "app_health.h"
#include "adxl345.h"

#define ADXL_BUS_NAME   SENSOR_SPI_BUS_NAME     /* "spi3" */
#define ADXL_DEV_NAME   "adxl345"
#define ADXL_SPI_HZ     1000000u

/* 寄存器 */
#define ADXL_DEVID       0x00
#define ADXL_BW_RATE     0x2C
#define ADXL_POWER_CTL   0x2D
#define ADXL_DATA_FORMAT 0x31
#define ADXL_DATAX0      0x32
#define ADXL_INT_SOURCE  0x30

#define ADXL_DEVID_VAL   0xE5

#define ADXL_READ_SINGLE 0x80
#define ADXL_READ_MULTI  0xC0

static struct rt_spi_device adxl_spi;
static struct rt_spi_device *adxl_dev = RT_NULL;
static rt_bool_t adxl_attached = RT_FALSE;
static subsys_health_t adxl_health = SUBSYS_UNINIT;

/* ---------- 底层寄存器访问 (SPI 传输) ---------- */

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

/* ---------- 设备 attach/configure (幂等, 只做一次) ---------- */

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

/* ---------- 正式 API (adxl345.h) ---------- */

rt_err_t adxl345_init(void)
{
    rt_uint8_t id = 0;
    rt_err_t e;

    if (adxl_health == SUBSYS_OK) return RT_EOK;    /* 幂等 */

    e = adxl_attach();
    if (e != RT_EOK) { adxl_health = SUBSYS_FAILED; return e; }

    /* BUG-001 双保险: 访问前确保 CS 未选中态(高) */
    rt_pin_write(safety_pin(PIN_NAME_IMU_CS), PIN_HIGH);

    /* 第一关: DEVID. 不匹配立即 FAILED, 停止配置(交接文档 §6.2) */
    e = adxl_read_reg(ADXL_DEVID, &id);
    if (e != RT_EOK) { adxl_health = SUBSYS_FAILED; return e; }
    if (id != ADXL_DEVID_VAL)
    {
        adxl_health = SUBSYS_FAILED;
        return -RT_EIO;     /* 常见原因: 接线/Mode3/供电/共地, 见 imu_id 输出 */
    }

    /* 基础配置: 100Hz, ±2g (默认量程 3.9mg/LSB)。每一步检查返回值。 */
    if ((e = adxl_write_reg(ADXL_POWER_CTL, 0x00)) != RT_EOK) goto wr_fail;
    if ((e = adxl_write_reg(ADXL_BW_RATE, 0x0A)) != RT_EOK) goto wr_fail;
    if ((e = adxl_write_reg(ADXL_DATA_FORMAT, 0x00)) != RT_EOK) goto wr_fail;
    if ((e = adxl_write_reg(ADXL_POWER_CTL, 0x08)) != RT_EOK) goto wr_fail;
    /* 首帧转换未就绪时读数为全 0, 留出转换时间 */
    rt_thread_mdelay(10);

    adxl_health = SUBSYS_OK;
    return RT_EOK;

wr_fail:
    adxl_health = SUBSYS_FAILED;
    return e;
}

rt_err_t adxl345_read_raw(rt_int16_t *x, rt_int16_t *y, rt_int16_t *z)
{
    rt_uint8_t buf[6] = {0};
    rt_err_t e;

    if (adxl_health != SUBSYS_OK) return -RT_ERROR;   /* 禁止用 0 冒充有效测量 */
    rt_pin_write(safety_pin(PIN_NAME_IMU_CS), PIN_HIGH);

    e = adxl_read_regs(ADXL_DATAX0, buf, 6);
    if (e != RT_EOK)
    {
        adxl_health = SUBSYS_DEGRADED;   /* 传输失败可重试, 不直接判死 */
        return e;
    }

    *x = (rt_int16_t)((buf[1] << 8) | buf[0]);
    *y = (rt_int16_t)((buf[3] << 8) | buf[2]);
    *z = (rt_int16_t)((buf[5] << 8) | buf[4]);

    adxl_health = SUBSYS_OK;             /* 成功读回, 恢复 OK */
    return RT_EOK;
}

rt_err_t adxl345_read_mg(rt_int16_t *mg_x, rt_int16_t *mg_y, rt_int16_t *mg_z)
{
    rt_int16_t x, y, z;
    rt_err_t e = adxl345_read_raw(&x, &y, &z);

    if (e != RT_EOK) return e;

    /* ±2g: 3.9 mg/LSB (×39/10, 整数运算) */
    *mg_x = (rt_int16_t)(x * 39 / 10);
    *mg_y = (rt_int16_t)(y * 39 / 10);
    *mg_z = (rt_int16_t)(z * 39 / 10);
    return RT_EOK;
}

rt_err_t adxl345_read_int_source(rt_uint8_t *int_source)
{
    rt_err_t e;

    if (adxl_health != SUBSYS_OK) return -RT_ERROR;
    rt_pin_write(safety_pin(PIN_NAME_IMU_CS), PIN_HIGH);

    e = adxl_read_reg(ADXL_INT_SOURCE, int_source);
    if (e != RT_EOK) adxl_health = SUBSYS_DEGRADED;
    return e;
}

rt_err_t adxl345_fifo_configure(void) { return -RT_ENOSYS; }   /* Phase 7-C/D */
rt_err_t adxl345_fifo_read(void)      { return -RT_ENOSYS; }   /* Phase 7-C/D */
rt_err_t adxl345_int1_attach(void (*cb)(void *args), void *args)
{
    (void)cb; (void)args;
    return -RT_ENOSYS;                                    /* Phase 7-B (EXTI5) */
}

subsys_health_t adxl345_get_health(void) { return adxl_health; }

/* ---------- MSH 命令 (薄封装, 全部保留) ---------- */

static void imu_id(void)
{
    rt_uint8_t id = 0;
    rt_err_t e;

    if (adxl_attach() != RT_EOK) { rt_kprintf("[IMU] attach failed\n"); return; }
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

/* 只读探测: 验证 spi3 总线/设备注册/DEVID, 不写任何 ADXL345 寄存器 */
static void imu_probe(void)
{
    rt_err_t e = adxl_attach();
    rt_uint8_t id1 = 0, id2 = 0;

    if (e != RT_EOK) { rt_kprintf("[IMU] attach failed: %d\n", e); return; }
    rt_pin_write(safety_pin(PIN_NAME_IMU_CS), PIN_HIGH);

    rt_kprintf("[IMU] bus=%s dev=%s cs=%s mode=3 hz=%u\n",
               ADXL_BUS_NAME, ADXL_DEV_NAME, PIN_NAME_IMU_CS, ADXL_SPI_HZ);

    e = adxl_read_reg(ADXL_DEVID, &id1);
    if (e != RT_EOK) { rt_kprintf("[IMU] read#1 failed: %d\n", e); return; }
    e = adxl_read_reg(ADXL_DEVID, &id2);
    if (e != RT_EOK) { rt_kprintf("[IMU] read#2 failed: %d\n", e); return; }

    rt_kprintf("[IMU] DEVID read#1=0x%02X read#2=0x%02X (expect 0x%02X twice)\n",
               id1, id2, ADXL_DEVID_VAL);
    if (id1 == ADXL_DEVID_VAL && id2 == ADXL_DEVID_VAL)
        rt_kprintf("[IMU] SPI3/ADXL345 PROBE OK\n");
    else
        rt_kprintf("[IMU] SPI3 bus alive but no valid ADXL345 reply\n");
}
MSH_CMD_EXPORT(imu_probe, read-only probe of spi3 bus and ADXL345 DEVID);

static void imu_raw(void)
{
    rt_int16_t x, y, z;
    rt_err_t e = adxl345_init();     /* 幂等: OK 则直通, FAILED 则重试 */

    if (e != RT_EOK)
    {
        rt_kprintf("[IMU] init failed (%d), health=%s\n",
                   e, subsys_health_name(adxl345_get_health()));
        return;
    }

    e = adxl345_read_mg(&x, &y, &z);
    if (e != RT_EOK)
    {
        rt_kprintf("[IMU] read failed (%d), health=%s\n",
                   e, subsys_health_name(adxl345_get_health()));
        return;
    }

    rt_kprintf("[IMU] mg X=%d Y=%d Z=%d health=%s\n",
               x, y, z, subsys_health_name(adxl345_get_health()));
    rt_kprintf("[IMU] 静置时某一轴应约 ±1000mg(1g 重力), 敲击应有明显变化\n");
}
MSH_CMD_EXPORT(imu_raw, read ADXL345 XYZ acceleration in mg);
