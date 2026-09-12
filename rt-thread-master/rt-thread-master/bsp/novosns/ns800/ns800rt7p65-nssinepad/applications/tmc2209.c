/*
 * tmc2209.c - TMC2209 UART 协议层 (UART2: PB6 TX / PB7 RX, 单线汇合到 PDN_UART)
 *
 * v3 (2026-09-12) 依据 TMC2209 Datasheet Rev 1.09 与官方 TMC-API 逐字节核对重写:
 *   ① 帧格式(权威定义, 全部按此实现):
 *        read  req: [0x05][slave][reg     ][crc]              4 字节
 *        write req: [0x05][slave][reg|0x80][D3 D2 D1 D0][crc] 8 字节, 无应答
 *        reply     : [0x05][0xFF ][reg     ][D3 D2 D1 D0][crc] 8 字节
 *      v2 错误: 把应答首字节当 0xFF 判别 —— 实际 byte0=0x05(sync), byte1=0xFF(master)。
 *   ② CRC8-ATM: poly 0x07 / init 0, 每字节按 LSB→MSB 逐位喂入 (datasheet
 *      swuart_calcCRC 的等价实现)。向量: [05 00 02]→0x8F, [05 00 00]→0x48。
 *   ③ IFCNT 只在收到有效 WRITE 后自增, READ 不影响 → probe 用"写 GCONF 原值"
 *      前后 IFCNT+1 验证芯片存活, 不改任何配置。
 *   ④ 单线回声: TX 经 1k 汇合到 PDN, MCU RX 会收到自己发的帧。
 *      read 路径在字节流里搜索 [05 FF reg] 应答头, 天然跳过自身回声
 *      (回声 byte1=slave 地址, 永不等于 0xFF); write 后主动排空回声。
 *   ⑤ 应答校验: r[0]==0x05, r[1]==0xFF, r[2]==(reg&0x7F), CRC 有效, 四关全过。
 *
 * 硬件映射不变: uart2 / PB6=TX / PB7=RX / 115200 8N1 / slave addr 0。
 * 状态: CRC 自测向量真机 PASS; tmc_uart_probe 前不得据无应答判断芯片损坏。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include "project_board.h"

#define TMC_UART_NAME    TMC_UART_DEVICE_NAME    /* "uart2" */
#define TMC_BAUD         115200
#define TMC_SYNC_BYTE    0x05
#define TMC_MASTER_ADDR  0xFF
#define TMC_ADDR_DEFAULT 0x00
#define TMC_WRITE_BIT    0x80

/* 常用寄存器 */
#define TMC_REG_GCONF    0x00
#define TMC_REG_GSTAT    0x01
#define TMC_REG_IFCNT    0x02
#define TMC_REG_IOIN     0x06
#define TMC_REG_SG_RESULT 0x41

#define TMC_REPLY_LEN        8
#define TMC_RX_BUF           32
#define TMC_REPLY_TIMEOUT_MS 100

static rt_device_t tmc_serial = RT_NULL;
static rt_bool_t tmc_opened = RT_FALSE;

static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);

/* ---------- CRC8-ATM (datasheet swuart_calcCRC 等价实现, 见文件头 ②) ---------- */
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

static void hex_dump(const char *tag, const rt_uint8_t *p, rt_size_t n)
{
    rt_size_t i;
    rt_kprintf("[TMC] %s (%d):", tag, (int)n);
    for (i = 0; i < n; ++i) rt_kprintf(" %02X", p[i]);
    rt_kprintf("\n");
}

/* ---------- 协议层 ---------- */

/* 写寄存器: 8 字节帧, TMC2209 对 WRITE 无应答(IFCNT 才是写入凭证)。
 * 发送后短暂延时排空自身回声, 保证后续 read 的字节流干净。 */
static rt_err_t tmc_write_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t value)
{
    rt_uint8_t req[8];
    rt_err_t e = tmc_uart_open();
    if (e != RT_EOK) return e;

    req[0] = TMC_SYNC_BYTE;
    req[1] = addr;
    req[2] = (rt_uint8_t)(reg | TMC_WRITE_BIT);
    req[3] = (rt_uint8_t)(value >> 24);
    req[4] = (rt_uint8_t)(value >> 16);
    req[5] = (rt_uint8_t)(value >> 8);
    req[6] = (rt_uint8_t)value;
    req[7] = tmc_crc8(req, 7);

    tmc_rx_flush();
    hex_dump("TX(write)", req, 8);
    if (rt_device_write(tmc_serial, 0, req, 8) != 8) return -RT_EIO;

    /* 8 字节 @115200 ≈ 0.7ms; 延时后一次排空回声 */
    rt_thread_mdelay(3);
    tmc_rx_flush();
    return RT_EOK;
}

/* 读寄存器: 发 4 字节请求后, RX 上先出现自身回声(4 字节, byte1=slave 地址),
 * 随后才是应答(8 字节, byte1=0xFF)。在字节流中滑动搜索 [05 FF reg&0x7F]
 * 应答头, 收满 8 字节后验 CRC —— 不假设应答从 buffer 第一个字节开始。 */
static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value)
{
    rt_uint8_t req[4];
    rt_uint8_t buf[TMC_RX_BUF];
    rt_size_t n = 0;
    rt_tick_t deadline;
    rt_err_t e = tmc_uart_open();
    if (e != RT_EOK) return e;

    req[0] = TMC_SYNC_BYTE;
    req[1] = addr;
    req[2] = (rt_uint8_t)(reg & 0x7F);
    req[3] = tmc_crc8(req, 3);

    tmc_rx_flush();
    hex_dump("TX(read)", req, 4);
    if (rt_device_write(tmc_serial, 0, req, 4) != 4) return -RT_EIO;

    deadline = rt_tick_get() + rt_tick_from_millisecond(TMC_REPLY_TIMEOUT_MS);
    for (;;)
    {
        rt_size_t got = 0;
        rt_size_t i;
        rt_bool_t hdr_found = RT_FALSE;
        rt_size_t hdr_pos = 0;

        if (n < sizeof(buf))
        {
            got = rt_device_read(tmc_serial, 0, buf + n, sizeof(buf) - n);
            n += got;
        }

        for (i = 0; i + 3 <= n; ++i)
        {
            if (buf[i] == TMC_SYNC_BYTE &&
                buf[i + 1] == TMC_MASTER_ADDR &&
                buf[i + 2] == (rt_uint8_t)(reg & 0x7F))
            {
                hdr_found = RT_TRUE;
                hdr_pos = i;
                break;
            }
        }

        if (hdr_found && n >= hdr_pos + TMC_REPLY_LEN)
        {
            hex_dump("RX", buf, n);
            if (tmc_crc8(buf + hdr_pos, 7) != buf[hdr_pos + 7])
            {
                rt_kprintf("[TMC] reply CRC error\n");
                return -RT_EIO;
            }
            *value = ((rt_uint32_t)buf[hdr_pos + 3] << 24) |
                     ((rt_uint32_t)buf[hdr_pos + 4] << 16) |
                     ((rt_uint32_t)buf[hdr_pos + 5] << 8) |
                      (rt_uint32_t)buf[hdr_pos + 6];
            return RT_EOK;
        }

        if ((rt_tick_t)(rt_tick_get() - deadline) < (rt_tick_t)0x80000000)
        {
            /* 超时分类: 无字节 / 只有回声 / 有应答头但不完整 */
            if (n == 0)
                rt_kprintf("[TMC] RX: no bytes (timeout)\n");
            else if (hdr_found)
            {
                hex_dump("RX(reply header but incomplete)", buf, n);
            }
            else
            {
                hex_dump("RX(echo only, no 05 FF header)", buf, n);
            }
            return -RT_ETIMEOUT;
        }

        if (n >= sizeof(buf))
        {
            /* 窗口滑过后半, 继续搜索, 防长垃圾流撑爆缓冲 */
            rt_memmove(buf, buf + sizeof(buf) / 2, sizeof(buf) / 2);
            n = sizeof(buf) / 2;
        }
        if (got == 0) rt_thread_mdelay(2);
    }
}

/* ---------- MSH 命令 ---------- */

/* 协议向量: 独立来源(datasheet 官方示例/TMCStepper), 非本实现离线自算 */
static void tmc_crc_test(void)
{
    struct { rt_uint8_t f[7]; rt_size_t n; rt_uint8_t crc; const char *tag; } v[] = {
        { {0x05,0x00,0x02},                3, 0x8F, "READ IFCNT slave0 " },
        { {0x05,0x00,0x00},                3, 0x48, "READ GCONF slave0 " },
    };
    int i, pass = 1;

    for (i = 0; i < (int)(sizeof(v) / sizeof(v[0])); ++i)
    {
        rt_uint8_t got = tmc_crc8(v[i].f, v[i].n);
        rt_kprintf("[TMC] %s: crc=%02X expect=%02X %s\n",
                   v[i].tag, got, v[i].crc, got == v[i].crc ? "OK" : "FAIL");
        if (got != v[i].crc) pass = 0;
    }
    rt_kprintf("[TMC] crc self-test: %s\n", pass ? "PASS" : "FAIL");
}
MSH_CMD_EXPORT(tmc_crc_test, TMC2209 CRC8 self test (official vectors 8F/48));

/* probe: READ IFCNT -> WRITE GCONF(原值, 零改动) -> READ IFCNT, 验证 +1。
 * IFCNT 只对有效 WRITE 自增, 所以 +1 证明芯片真实收到并接受了完整写帧。 */
static void tmc_uart_probe(void)
{
    rt_uint32_t ifcnt1 = 0, ifcnt2 = 0, gconf = 0;
    rt_err_t e;

    if (tmc_uart_open() != RT_EOK) return;

    rt_kprintf("[TMC] probe: IFCNT -> write GCONF(same value) -> IFCNT\n");

    e = tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IFCNT, &ifcnt1);
    if (e != RT_EOK) { rt_kprintf("[TMC] IFCNT#1 read failed\n"); return; }
    rt_kprintf("[TMC] IFCNT before = %u\n", ifcnt1 & 0xFF);

    e = tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_GCONF, &gconf);
    if (e != RT_EOK) { rt_kprintf("[TMC] GCONF read failed\n"); return; }
    rt_kprintf("[TMC] GCONF = 0x%08X (writing same value back)\n", gconf);

    e = tmc_write_reg(TMC_ADDR_DEFAULT, TMC_REG_GCONF, gconf);
    if (e != RT_EOK) { rt_kprintf("[TMC] GCONF write failed\n"); return; }

    e = tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IFCNT, &ifcnt2);
    if (e != RT_EOK) { rt_kprintf("[TMC] IFCNT#2 read failed\n"); return; }
    rt_kprintf("[TMC] IFCNT after  = %u\n", ifcnt2 & 0xFF);

    if ((ifcnt2 & 0xFF) == ((ifcnt1 + 1) & 0xFF))
        rt_kprintf("[TMC] IFCNT +1 -> TMC2209 ALIVE (write accepted)\n");
    else
        rt_kprintf("[TMC] IFCNT did not increment as expected\n");
}
MSH_CMD_EXPORT(tmc_uart_probe, probe TMC2209: IFCNT-write-IFCNT handshake);

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
        rt_kprintf("[TMC] GSTAT read failed\n");
        return;
    }
    if (tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_IOIN, &ioin) == RT_EOK)
        rt_kprintf("[TMC] IOIN version=0x%02X (0x21=2209 0x20=2208)\n",
                   (rt_uint8_t)(ioin >> 24));
}
MSH_CMD_EXPORT(tmc_status, read TMC2209 GSTAT/IOIN);
