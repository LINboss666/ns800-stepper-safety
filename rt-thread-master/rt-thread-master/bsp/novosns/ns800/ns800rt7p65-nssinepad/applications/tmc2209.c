/*
 * tmc2209.c - TMC2209 UART 协议层 (UART2: PB6 TX / PB7 RX, 单线汇合在扩展板)
 *
 * 帧格式(TMC2209 数据手册):
 *   写: [0x05 sync][addr][reg][data31..24][23..16][15..8][7..0][crc]  应答4字节
 *   读: [0x05 sync][addr][reg|0x80][crc]                              应答8字节
 *   CRC8: 多项式 0x07, 初值 0x00, 覆盖除 CRC 外全部字节
 *
 * 软件不处理单线方向切换(TX/RX 由扩展板 1k 电阻网络汇合到 PDN_UART)。
 *
 * 状态: 协议层+CRC自测完成(tmc_crc_test 纯软件已可验证);
 *       真实寄存器通信待扩展板到位(交接文档 §2.2: 之前杜邦线扫描失败,
 *       Agent 不得据此判断 TMC2209 损坏)。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"

#define TMC_UART_NAME    TMC_UART_DEVICE_NAME    /* "uart2" */
#define TMC_BAUD         115200
#define TMC_SYNC_BYTE    0x05
#define TMC_ADDR_DEFAULT 0x00

/* 常用寄存器 */
#define TMC_REG_GCONF    0x00
#define TMC_REG_GSTAT    0x01
#define TMC_REG_IFCNT    0x02
#define TMC_REG_IOIN     0x06
#define TMC_REG_SG_RESULT 0x41

static rt_device_t tmc_serial = RT_NULL;
static rt_bool_t tmc_opened = RT_FALSE;

/* ---------- CRC8 (poly 0x07) ---------- */
static rt_uint8_t tmc_crc8(const rt_uint8_t *data, rt_size_t len)
{
    rt_uint8_t crc = 0;
    rt_size_t i;
    int b;

    for (i = 0; i < len; ++i)
    {
        crc ^= data[i];
        for (b = 0; b < 8; ++b)
            crc = (crc & 0x80) ? (rt_uint8_t)((crc << 1) ^ 0x07)
                               : (rt_uint8_t)(crc << 1);
    }
    return crc;
}

/* ---------- 串口底层 ---------- */
static rt_err_t tmc_uart_open(void)
{
    struct serial_configure cfg = RT_SERIAL_CONFIG_DEFAULT;

    if (tmc_opened) return RT_EOK;

    tmc_serial = rt_device_find(TMC_UART_NAME);
    if (tmc_serial == RT_NULL)
    {
        rt_kprintf("[TMC] %s not found (check BSP_USING_UART2)\n", TMC_UART_NAME);
        return -RT_ERROR;
    }

    cfg.baud_rate = TMC_BAUD;
    rt_device_control(tmc_serial, RT_DEVICE_CTRL_CONFIG, &cfg);

    if (rt_device_open(tmc_serial, RT_DEVICE_OFLAG_RDWR | RT_DEVICE_FLAG_INT_RX)
        != RT_EOK)
    {
        rt_kprintf("[TMC] uart open failed\n");
        return -RT_ERROR;
    }
    tmc_opened = RT_TRUE;
    return RT_EOK;
}

static void tmc_rx_flush(void)
{
    rt_uint8_t dump[32];
    while (rt_device_read(tmc_serial, 0, dump, sizeof(dump)) > 0) ;
}

/* 带超时读取期望长度, 返回实际读到的字节数 */
static rt_size_t tmc_rx_wait(rt_uint8_t *buf, rt_size_t want, rt_uint32_t timeout_ms)
{
    rt_size_t got = 0;
    rt_tick_t deadline = rt_tick_get() + rt_tick_from_millisecond(timeout_ms);

    while (got < want)
    {
        got += rt_device_read(tmc_serial, 0, buf + got, want - got);
        if ((rt_tick_t)(rt_tick_get() - deadline) < (rt_tick_t)0x80000000)
            break;                       /* 超时 */
        if (got < want) rt_thread_mdelay(2);
    }
    return got;
}

static void hex_dump(const char *tag, const rt_uint8_t *p, rt_size_t n)
{
    rt_size_t i;
    rt_kprintf("[TMC] %s:", tag);
    for (i = 0; i < n; ++i) rt_kprintf(" %02X", p[i]);
    rt_kprintf("\n");
}

/* ---------- 协议层 ---------- */
static rt_err_t tmc_write_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t value)
{
    rt_uint8_t f[8], r[4];
    rt_size_t n;
    rt_err_t e = tmc_uart_open();
    if (e != RT_EOK) return e;

    f[0] = TMC_SYNC_BYTE; f[1] = addr; f[2] = reg;
    f[3] = (rt_uint8_t)(value >> 24); f[4] = (rt_uint8_t)(value >> 16);
    f[5] = (rt_uint8_t)(value >> 8);  f[6] = (rt_uint8_t)value;
    f[7] = tmc_crc8(f, 7);

    tmc_rx_flush();
    if (rt_device_write(tmc_serial, 0, f, 8) != 8) return -RT_EIO;

    n = tmc_rx_wait(r, 4, 100);        /* 写应答: [sync][addr][reg][crc] */
    if (n != 4) return -RT_ETIMEOUT;
    if (r[0] != TMC_SYNC_BYTE || r[1] != addr || r[2] != reg) return -RT_EIO;
    if (r[3] != tmc_crc8(r, 3)) return -RT_EIO;   /* 应答 CRC 错 */
    return RT_EOK;
}

static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value)
{
    rt_uint8_t f[4], r[8];
    rt_size_t n;
    rt_err_t e = tmc_uart_open();
    if (e != RT_EOK) return e;

    f[0] = TMC_SYNC_BYTE; f[1] = addr; f[2] = (rt_uint8_t)(reg | 0x80);
    f[3] = tmc_crc8(f, 3);

    tmc_rx_flush();
    hex_dump("TX", f, 4);
    if (rt_device_write(tmc_serial, 0, f, 4) != 4) return -RT_EIO;

    n = tmc_rx_wait(r, 8, 100);        /* 读应答: [sync][addr][reg][d31..0][crc] */
    if (n != 8)
    {
        hex_dump("RX(short)", r, n);
        return -RT_ETIMEOUT;
    }
    hex_dump("RX", r, 8);
    if (r[0] != TMC_SYNC_BYTE || r[1] != addr || r[2] != reg) return -RT_EIO;
    if (r[7] != tmc_crc8(r, 7))
    {
        rt_kprintf("[TMC] reply CRC bad (got %02X)\n", r[7]);
        return -RT_EIO;
    }
    *value = ((rt_uint32_t)r[3] << 24) | ((rt_uint32_t)r[4] << 16) |
             ((rt_uint32_t)r[5] << 8) | r[6];
    return RT_EOK;
}

/* ---------- MSH 命令 ---------- */
static void tmc_crc_test(void)
{
    /* 向量用独立脚本(python)离线计算后硬编码, 防实现自证 */
    struct { rt_uint8_t f[7]; rt_size_t n; rt_uint8_t crc; } v[] = {
        { {0x05,0x00,0x81},                     3, 0x4E },  /* 读IFCNT请求 */
        { {0x05,0x00,0x22,0,0,0,0},             7, 0x0A },  /* 写VACTUAL=0 */
        { {0x05,0x00,0x02,0,0,0,0},             7, 0x6E },  /* 应答帧示例 */
    };
    int i, pass = 1;

    for (i = 0; i < 3; ++i)
    {
        rt_uint8_t got = tmc_crc8(v[i].f, v[i].n);
        rt_kprintf("[TMC] vector%d crc=%02X expect=%02X %s\n",
                   i, got, v[i].crc, got == v[i].crc ? "OK" : "FAIL");
        if (got != v[i].crc) pass = 0;
    }
    rt_kprintf("[TMC] crc self-test: %s\n", pass ? "PASS" : "FAIL");
}
MSH_CMD_EXPORT(tmc_crc_test, TMC2209 CRC8 self test (software only));

static void tmc_uart_probe(void)
{
    rt_uint32_t v1 = 0, v2 = 0;
    rt_err_t e;

    if (tmc_uart_open() != RT_EOK) return;

    rt_kprintf("[TMC] probe: read IFCNT twice (addr=%02X, %d baud)\n",
               TMC_ADDR_DEFAULT, TMC_BAUD);
    e = tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IFCNT, &v1);
    if (e != RT_EOK)
    {
        rt_kprintf("[TMC] telegram#1: %s\n",
                   e == -RT_ETIMEOUT ? "TIMEOUT (无应答: 检查单线网络/地址/波特率)"
                                     : "transport error");
        return;
    }
    e = tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IFCNT, &v2);
    if (e != RT_EOK) { rt_kprintf("[TMC] telegram#2 error\n"); return; }

    rt_kprintf("[TMC] IFCNT: %u -> %u (%s)\n", v1, v2,
               v2 == (rt_uint8_t)(v1 + 1) ? "increment OK, TMC2209 ALIVE"
                                          : "unexpected, check reply");
}
MSH_CMD_EXPORT(tmc_uart_probe, probe TMC2209 via UART2 IFCNT counter);

static void tmc_status(void)
{
    rt_uint32_t gstat = 0, ioin = 0;

    if (tmc_uart_open() != RT_EOK) return;

    if (tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_GSTAT, &gstat) == RT_EOK)
    {
        rt_kprintf("[TMC] GSTAT=%02X (reset=%d drv_err=%d sg2=%d)\n",
                   (rt_uint8_t)gstat, gstat & 1, (gstat >> 1) & 1, (gstat >> 2) & 1);
    }
    else
    {
        rt_kprintf("[TMC] GSTAT read failed (无应答?)\n");
        return;
    }
    if (tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IOIN, &ioin) == RT_EOK)
        rt_kprintf("[TMC] IOIN version=0x%02X (0x21=2209 0x20=2208)\n",
                   (rt_uint8_t)(ioin >> 24));
}
MSH_CMD_EXPORT(tmc_status, read TMC2209 GSTAT/IOIN);
