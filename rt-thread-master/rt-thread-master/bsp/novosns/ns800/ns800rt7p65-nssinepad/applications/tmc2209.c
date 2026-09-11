/*
 * tmc2209.c - TMC2209 UART 协议层 (UART2: PB6 TX / PB7 RX, 单线汇合在扩展板)
 *
 * ⚠ BUG-010 重写版 (2026-09-12): 旧版三处协议错误已修正——
 *   ① 读写位反了: 官方规范 bit7=1 写 / bit7=0 读 (旧版恰好相反)
 *   ② CRC8 算法: TMC-API 官方实现——每字节按 LSB-first 逐位喂入,
 *      MSB 方向移位, 多项式 0x07, 初值 0x00 (旧版用了普通左移算法)
 *   ③ 读应答帧: [0xFF(master), addr, reg, d31..0, crc] 共 8 字节,
 *      首字节固定 0xFF (旧版误以为 echo slave 地址)
 *   ④ 写操作无应答: 以 IFCNT 自增确认写入成功 (旧版等 4 字节应答必然超时)
 *   测试向量: [05 00 02]→0x8F 来自 TMCStepper 真机验证库, 非自证
 *
 * 帧格式(TMC2209 数据手册):
 *   写: [0x05][addr][reg|0x80][d31..24][d23..16][d15..8][d7..0][crc]  无应答
 *   读: [0x05][addr][reg&0x7F][crc]  应答 [0xFF][addr][reg][d31..0][crc]
 *
 * 软件不处理单线方向切换(TX/RX 由扩展板 1k 电阻网络汇合到 PDN_UART)。
 * 状态: 协议层完成, CRC 自测真机 PASS; 真实通信待扩展板接线。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"

#define TMC_UART_NAME    TMC_UART_DEVICE_NAME    /* "uart2" */
#define TMC_BAUD         115200
#define TMC_SYNC_BYTE    0x05
#define TMC_REPLY_MASTER 0xFF                    /* 应答帧首字节固定 0xFF */
#define TMC_ADDR_DEFAULT 0x00

/* 常用寄存器 */
#define TMC_REG_GCONF    0x00
#define TMC_REG_GSTAT    0x01
#define TMC_REG_IFCNT    0x02
#define TMC_REG_IOIN     0x06
#define TMC_REG_SG_RESULT 0x41

static rt_device_t tmc_serial = RT_NULL;
static rt_bool_t tmc_opened = RT_FALSE;

static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);

/* ---------- CRC8 (TMC-API 官方算法, 见文件头 BUG-010 ②) ---------- */
static rt_uint8_t tmc_crc8(const rt_uint8_t *data, rt_size_t len)
{
    rt_uint8_t crc = 0;
    rt_size_t i;
    int b;

    for (i = 0; i < len; ++i)
    {
        rt_uint8_t curr = data[i];
        for (b = 0; b < 8; ++b)
        {
            if ((crc >> 7) ^ (curr & 0x01))
                crc = (rt_uint8_t)((crc << 1) ^ 0x07);
            else
                crc = (rt_uint8_t)(crc << 1);
            curr >>= 1;
        }
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

/* 读 IFCNT: 用于写确认与探活。返回 RT_EOK 时 *cnt 为计数值 */
static rt_err_t tmc_read_ifcnt(rt_uint32_t *cnt)
{
    return tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IFCNT, cnt);
}

/* ---------- 协议层 ---------- */
/* 写寄存器: 无应答。confirm=1 时用 IFCNT+1 验证芯片真的收到了 */
static rt_err_t tmc_write_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t value,
                              rt_bool_t confirm)
{
    rt_uint8_t f[8];
    rt_uint32_t before = 0, after = 0;
    rt_err_t e = tmc_uart_open();
    if (e != RT_EOK) return e;

    if (confirm)
    {
        if (tmc_read_ifcnt(&before) != RT_EOK) return -RT_EIO;
    }

    f[0] = TMC_SYNC_BYTE; f[1] = addr; f[2] = (rt_uint8_t)(reg | 0x80); /* BUG-010①: 写=bit7=1 */
    f[3] = (rt_uint8_t)(value >> 24); f[4] = (rt_uint8_t)(value >> 16);
    f[5] = (rt_uint8_t)(value >> 8);  f[6] = (rt_uint8_t)value;
    f[7] = tmc_crc8(f, 7);

    tmc_rx_flush();
    hex_dump("TX(write)", f, 8);
    if (rt_device_write(tmc_serial, 0, f, 8) != 8) return -RT_EIO;

    if (!confirm) return RT_EOK;

    rt_thread_mdelay(10);
    if (tmc_read_ifcnt(&after) != RT_EOK) return -RT_EIO;
    /* IFCNT 每收到一帧完整电报自增(含我们发的读命令), +1..+2 视为到达 */
    if (after == before || (rt_uint8_t)(after - before) > 4)
    {
        rt_kprintf("[TMC] write confirm failed (IFCNT %u -> %u)\n", before, after);
        return -RT_EIO;
    }
    rt_kprintf("[TMC] write confirmed (IFCNT %u -> %u)\n", before, after);
    return RT_EOK;
}

static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value)
{
    rt_uint8_t f[4], r[8];
    rt_size_t n;
    rt_err_t e = tmc_uart_open();
    if (e != RT_EOK) return e;

    f[0] = TMC_SYNC_BYTE; f[1] = addr; f[2] = (rt_uint8_t)(reg & 0x7F); /* BUG-010①: 读=bit7=0 */
    f[3] = tmc_crc8(f, 3);

    tmc_rx_flush();
    hex_dump("TX(read)", f, 4);
    if (rt_device_write(tmc_serial, 0, f, 4) != 4) return -RT_EIO;

    n = tmc_rx_wait(r, 8, 100);
    if (n != 8)
    {
        hex_dump("RX(short)", r, n);
        return -RT_ETIMEOUT;
    }
    hex_dump("RX", r, 8);
    /* BUG-010③: 应答帧 [0xFF][addr][reg][d31..0][crc], 首字节固定 0xFF */
    if (r[0] != TMC_REPLY_MASTER || r[1] != addr || r[2] != reg)
    {
        rt_kprintf("[TMC] reply head bad (FF/addr/reg)\n");
        return -RT_EIO;
    }
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
    /* 向量来源: ①[05 00 02]→0x8F 来自 TMCStepper 真机验证库(独立第三方),
     * ②③ 由独立 python 脚本按 TMC-API 官方算法离线计算, 与固件实现不同源 */
    struct { rt_uint8_t f[7]; rt_size_t n; rt_uint8_t crc; const char *tag; } v[] = {
        { {0x05,0x00,0x02},                3, 0x8F, "read IFCNT req" },
        { {0x05,0x00,0x80,0,0,0,0},        7, 0x49, "write GCONF=0 req" },
        { {0xFF,0x00,0x02,0,0,0,3},        7, 0xC2, "reply IFCNT=3" },
    };
    int i, pass = 1;

    for (i = 0; i < 3; ++i)
    {
        rt_uint8_t got = tmc_crc8(v[i].f, v[i].n);
        rt_kprintf("[TMC] %s: crc=%02X expect=%02X %s\n",
                   v[i].tag, got, v[i].crc, got == v[i].crc ? "OK" : "FAIL");
        if (got != v[i].crc) pass = 0;
    }
    rt_kprintf("[TMC] crc self-test: %s\n", pass ? "PASS" : "FAIL");
}
MSH_CMD_EXPORT(tmc_crc_test, TMC2209 CRC8 self test (vectors from TMCStepper));

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
                   e == -RT_ETIMEOUT ? "TIMEOUT (no reply: check single-wire net/addr/baud)"
                                     : "transport error");
        return;
    }
    e = tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IFCNT, &v2);
    if (e != RT_EOK) { rt_kprintf("[TMC] telegram#2 error\n"); return; }

    rt_kprintf("[TMC] IFCNT: %u -> %u (%s)\n", v1, v2,
               v2 != v1 ? "increment OK, TMC2209 ALIVE" : "unexpected, check reply");
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
        rt_kprintf("[TMC] GSTAT read failed (no reply?)\n");
        return;
    }
    if (tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IOIN, &ioin) == RT_EOK)
        rt_kprintf("[TMC] IOIN version=0x%02X (0x21=2209 0x20=2208)\n",
                   (rt_uint8_t)(ioin >> 24));
}
MSH_CMD_EXPORT(tmc_status, read TMC2209 GSTAT/IOIN);
