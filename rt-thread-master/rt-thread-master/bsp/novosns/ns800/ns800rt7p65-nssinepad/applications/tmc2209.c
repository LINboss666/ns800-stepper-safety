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
#include "app_health.h"
#include "tmc2209.h"
#include "motor.h"          /* F2b P0-B: 运行态写入门禁要读电机静止/armed 状态 */
#include "safety_gpio.h"    /* F2b P0-B: DRV_ENABLE 焊盘是否实测 LOW */
#include "safety_state.h"   /* F2b P0-B: Safety 状态机与锁存故障码 */

#define TMC_UART_NAME    TMC_UART_DEVICE_NAME    /* "uart2" */
#define TMC_BAUD         115200
#define TMC_SYNC_BYTE    0x05
#define TMC_MASTER_ADDR  0xFF
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
static struct rt_mutex tmc_xfer_lock;      /* P1-11: 完整事务串行化 */
static rt_bool_t tmc_lock_ok = RT_FALSE;

/* P1-11: 周期 SG 读取默认关闭 hex_dump; 调试时置 1 重编 */
#ifndef TMC_TRACE_VERBOSE
#define TMC_TRACE_VERBOSE  0
#endif

static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);
static rt_err_t tmc_read_reg_locked(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);
static rt_err_t tmc_write_reg_locked(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t value);
/* F2b: 配置实例的同步对象初始化(定义在文件后段, init 里要先调)。F2c R02: 返回状态。 */
static rt_err_t tmc_cfg_prod_sync(void);

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
    if (!tmc_lock_ok)
    {
        if (rt_mutex_init(&tmc_xfer_lock, "tmc", RT_IPC_FLAG_PRIO) != RT_EOK)
        {
            tmc_opened = RT_FALSE;
            return -RT_ERROR;
        }
        tmc_lock_ok = RT_TRUE;
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
 * 发送后短暂延时排空自身回声, 保证后续 read 的字节流干净。
 * P1-11: 完整事务(flush+TX+排空)持锁串行化。 */
static rt_err_t tmc_write_reg_locked(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t value)
{
    rt_uint8_t req[8];

    req[0] = TMC_SYNC_BYTE;
    req[1] = addr;
    req[2] = (rt_uint8_t)(reg | TMC_WRITE_BIT);
    req[3] = (rt_uint8_t)(value >> 24);
    req[4] = (rt_uint8_t)(value >> 16);
    req[5] = (rt_uint8_t)(value >> 8);
    req[6] = (rt_uint8_t)value;
    req[7] = tmc_crc8(req, 7);

    tmc_rx_flush();
#if TMC_TRACE_VERBOSE
    hex_dump("TX(write)", req, 8);
#endif
    if (rt_device_write(tmc_serial, 0, req, 8) != 8) return -RT_EIO;

    /* 8 字节 @115200 ≈ 0.7ms; 延时后一次排空回声 */
    rt_thread_mdelay(3);
    tmc_rx_flush();
    return RT_EOK;
}

static rt_err_t tmc_write_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t value)
{
    rt_err_t e;
    if (tmc_uart_open() != RT_EOK) return -RT_ERROR;
    if (tmc_lock_ok) rt_mutex_take(&tmc_xfer_lock, RT_WAITING_FOREVER);
    e = tmc_write_reg_locked(addr, reg, value);
    if (tmc_lock_ok) rt_mutex_release(&tmc_xfer_lock);
    return e;
}

/* 读寄存器: 发 4 字节请求后, RX 上先出现自身回声(4 字节, byte1=slave 地址),
 * 随后才是应答(8 字节, byte1=0xFF)。在字节流中滑动搜索 [05 FF reg&0x7F]
 * 应答头, 收满 8 字节后验 CRC —— 不假设应答从 buffer 第一个字节开始。 */
static rt_err_t tmc_read_reg_locked(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value)
{
    rt_uint8_t req[4];
    rt_uint8_t buf[TMC_RX_BUF];
    rt_size_t n = 0;
    rt_tick_t deadline;

    req[0] = TMC_SYNC_BYTE;
    req[1] = addr;
    req[2] = (rt_uint8_t)(reg & 0x7F);
    req[3] = tmc_crc8(req, 3);

    tmc_rx_flush();
#if TMC_TRACE_VERBOSE
    hex_dump("TX(read)", req, 4);
#endif
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
#if TMC_TRACE_VERBOSE
            hex_dump("RX", buf, n);
#endif
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

/* P1-11: 读事务(flush+TX+RX+parse)持锁串行化, 防 MSH 与线程交叉访问 */
static rt_err_t tmc_read_reg(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value)
{
    rt_err_t e;
    if (tmc_uart_open() != RT_EOK) return -RT_ERROR;
    if (tmc_lock_ok) rt_mutex_take(&tmc_xfer_lock, RT_WAITING_FOREVER);
    e = tmc_read_reg_locked(addr, reg, value);
    if (tmc_lock_ok) rt_mutex_release(&tmc_xfer_lock);
    return e;
}

/* ---------- 正式 API (tmc2209.h, Phase 7-A) ----------
 * 只封装已验证的底层 tmc_read_reg/tmc_write_reg, 不改协议实现。
 * health 语义: OK=链路通且最近一次传输成功; DEGRADED=链路通但最近失败;
 * FAILED=uart2 打不开; UNINIT=未初始化。 */

static subsys_health_t tmc_health = SUBSYS_UNINIT;

static void tmc_health_after_xfer(rt_err_t e)
{
    if (tmc_health == SUBSYS_UNINIT) return;      /* 未 init 时不越级判定 */
    if (tmc_health == SUBSYS_FAILED) return;
    tmc_health = (e == RT_EOK) ? SUBSYS_OK : SUBSYS_DEGRADED;
}

rt_err_t tmc2209_init(void)
{
    rt_err_t e = tmc_uart_open();
    rt_err_t e2;

    /* F2b/F2c: 配置执行器的两把锁随子系统一起就绪(本函数在 bootstrap 单线程阶段
     * 调用, 之后才有 motor/diagnosis 线程)。
     * F2c R02: 锁创建失败 = 执行器无法串行化 = 子系统判 FAILED 并把错误上抛,
     * 不再出现"链路 OK 但关键区没有互斥"的中间态。 */
    e2 = tmc_cfg_prod_sync();

    if (e != RT_EOK)      tmc_health = SUBSYS_FAILED;
    else if (e2 != RT_EOK) tmc_health = SUBSYS_FAILED;
    else                  tmc_health = SUBSYS_OK;

    return (e != RT_EOK) ? e : e2;
}

rt_err_t tmc2209_read_register(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value)
{
    rt_err_t e = tmc_read_reg(addr, reg, value);

    tmc_health_after_xfer(e);
    return e;
}

rt_err_t tmc2209_write_register_confirmed(rt_uint8_t addr, rt_uint8_t reg,
                                          rt_uint32_t value)
{
    rt_uint32_t before = 0, after = 0;
    rt_err_t e;

    /* P1-3: 整个 READ IFCNT → WRITE → READ IFCNT → CHECK 必须在同一把
     * tmc_xfer_lock 内完成, 否则其它 writer 插入会使 IFCNT 误判。
     * 事务边界: tmc_uart_open 之后取锁, 全部 locked helper 操作完释放。
     * locked helper 不再自行取锁(避免嵌套)。 */
    if (tmc_uart_open() != RT_EOK) return -RT_ERROR;
    if (tmc_lock_ok) rt_mutex_take(&tmc_xfer_lock, RT_WAITING_FOREVER);

    e = tmc_read_reg_locked(addr, TMC_REG_IFCNT, &before);
    if (e != RT_EOK) goto out;

    e = tmc_write_reg_locked(addr, reg, value);
    if (e != RT_EOK) { tmc_health_after_xfer(e); goto out; }

    e = tmc_read_reg_locked(addr, TMC_REG_IFCNT, &after);
    if (e != RT_EOK) goto out;

    /* IFCNT 只对有效 WRITE 自增: +1 = 芯片接受了完整写帧 */
    if ((after & 0xFF) != ((before + 1) & 0xFF))
    {
        rt_kprintf("[TMC] IFCNT mismatch (%u -> %u)\n",
                   before & 0xFF, after & 0xFF);
        tmc_health_after_xfer(-RT_EIO);      /* P1-11: 健康联动 DEGRADED */
        e = -RT_EIO;
        goto out;
    }

    tmc_health_after_xfer(RT_EOK);
    e = RT_EOK;

out:
    if (tmc_lock_ok) rt_mutex_release(&tmc_xfer_lock);
    return e;
}

rt_err_t tmc2209_read_sg_result(rt_uint16_t *sg_result)
{
    rt_uint32_t v = 0;
    rt_err_t e = tmc2209_read_register(TMC_ADDR_DEFAULT, TMC_REG_SG_RESULT, &v);

    if (e == RT_EOK) *sg_result = (rt_uint16_t)(v & 0x03FF);  /* SG_RESULT 10bit */
    return e;
}

rt_err_t tmc2209_read_status(rt_uint8_t *gstat)
{
    rt_uint32_t v = 0;
    rt_err_t e = tmc2209_read_register(TMC_ADDR_DEFAULT, TMC_REG_GSTAT, &v);

    if (e == RT_EOK) *gstat = (rt_uint8_t)(v & 0xFF);
    return e;
}

subsys_health_t tmc2209_get_health(void) { return tmc_health; }

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

/* 只读扫描: 地址 0..3 逐个 READ IOIN(0x06), 不执行任何 WRITE、不改配置。
 * 复用 tmc_read_reg(含回声过滤/应答头搜索/CRC 校验), 不另起第二套协议实现。
 * IOIN[31:24]=VERSION: TMC2209=0x21, TMC2208=0x20。 */
static void tmc_scan(void)
{
    rt_uint8_t addr;
    int found = 0;

    if (tmc_uart_open() != RT_EOK) return;

    rt_kprintf("[TMC] scan: READ IOIN(0x06) on addr 0..3, read-only\n");
    for (addr = 0; addr <= 3; ++addr)
    {
        rt_uint32_t ioin = 0;
        rt_err_t e = tmc_read_reg(addr, TMC_REG_IOIN, &ioin);

        if (e == RT_EOK)
        {
            rt_uint8_t ver = (rt_uint8_t)(ioin >> 24);
            found++;
            if (ver == 0x21)
                rt_kprintf("ADDR %d: IOIN=0x%08X VERSION=0x%02X -> TMC2209 FOUND\n",
                           addr, ioin, ver);
            else
                rt_kprintf("ADDR %d: IOIN=0x%08X VERSION=0x%02X -> VERSION mismatch"
                           " (0x21=TMC2209 expected)\n", addr, ioin, ver);
        }
        else
        {
            rt_kprintf("ADDR %d: no valid reply\n", addr);
        }
    }

    if (found == 0)
    {
        rt_kprintf("[TMC] UART MCU TX/RX echo path OK\n");
        rt_kprintf("[TMC] No TMC2209 response on addresses 0..3\n");
        rt_kprintf("[TMC] Check VIO / VM / common GND / PDN_UART physical connection"
                   " / MS1-MS2 hardware\n");
    }
}
MSH_CMD_EXPORT(tmc_scan, read-only scan addr 0..3 IOIN for TMC2209);

/* 寄存器快照: 全部 READ, 零 WRITE, 记录上电基线供日后电机配置/FAE 比对 */
static void tmc_regs(void)
{
    static const struct { rt_uint8_t reg; const char *name; } regs[] = {
        { TMC_REG_GCONF,    "GCONF   " },
        { TMC_REG_GSTAT,    "GSTAT   " },
        { TMC_REG_IFCNT,    "IFCNT   " },
        { TMC_REG_IOIN,     "IOIN    " },
        { 0x22,             "VACTUAL " },
        { 0x6C,             "CHOPCONF" },
        { TMC_REG_SG_RESULT,"SGRESULT" },
    };
    int i;

    if (tmc_uart_open() != RT_EOK) return;

    rt_kprintf("[TMC] register snapshot (read-only, addr %d):\n", TMC_ADDR_DEFAULT);
    for (i = 0; i < (int)(sizeof(regs) / sizeof(regs[0])); ++i)
    {
        rt_uint32_t v = 0;
        rt_err_t e = tmc_read_reg(TMC_ADDR_DEFAULT, regs[i].reg, &v);
        if (e == RT_EOK)
            rt_kprintf("[TMC] %s (0x%02X) = 0x%08X\n", regs[i].name, regs[i].reg, v);
        else
            rt_kprintf("[TMC] %s (0x%02X) = READ FAILED\n", regs[i].name, regs[i].reg);
    }
}
MSH_CMD_EXPORT(tmc_regs, dump TMC2209 key registers read-only);

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

/* ================= Phase 8-F2/F2a/F2b: 驱动参数配置(软件准备, 默认不启用) ====
 *
 * 编译期硬门禁 TMC_DRIVE_CONFIG_HW_VALIDATED 定义在 tmc2209.h(便于其它模块如实
 * 报告门的状态)。当前扩展板 U5 与门损坏 / 急停非门损坏 / TMC_ENN 实测约 0V /
 * H2 限位未接, 安全链未验收 → 必须保持 RT_FALSE; 翻转需单独提交 + 硬件验证记录。
 * F2b 起它还只是第一层: apply 在此之后还要过运行态门禁(见 tmc_cfg_gate_prod)。
 * apply 只写配置寄存器: 不碰 ENN / MCU_DRV_ENABLE, 不写 VACTUAL, 不产生 STEP。 */

/* ---- F2a: 寄存器地址/位定义编译期钉住 --------------------------------------
 * 写错地址是"沉默失败": 芯片对任意地址的 WRITE 都应答并让 IFCNT 自增, 到达
 * 确认发现不了地址错误(F2 就把 TPOWERDOWN 误写成 0x3C)。故在此用编译期断言
 * 把官方确认值固定下来 —— 条件为假时数组长度 -1, 直接编译失败。 */
#define TMC_CFG_STATIC_ASSERT(cond, tag) \
    typedef char tmc_cfg_static_assert_##tag[((cond) ? 1 : -1)]

TMC_CFG_STATIC_ASSERT(TMC_REG_GCONF      == 0x00, gconf_addr);
TMC_CFG_STATIC_ASSERT(TMC_REG_CHOPCONF   == 0x6C, chopconf_addr);
TMC_CFG_STATIC_ASSERT(TMC_REG_IHOLD_IRUN == 0x10, ihold_irun_addr);
TMC_CFG_STATIC_ASSERT(TMC_REG_TPOWERDOWN == 0x11, tpowerdown_addr);
TMC_CFG_STATIC_ASSERT(TMC_GCONF_PDN_DISABLE  == (1u << 6),    gconf_pdn_disable_bit);
TMC_CFG_STATIC_ASSERT(TMC_GCONF_I_SCALE_ANALOG == (1u << 0),  gconf_iscale_bit);
TMC_CFG_STATIC_ASSERT(TMC_GCONF_MSTEP_REG_SELECT == (1u << 7), gconf_mstepsel_bit);
TMC_CFG_STATIC_ASSERT(TMC_CHOPCONF_VSENSE == 0x00020000u, chopconf_vsense_bit17);
TMC_CFG_STATIC_ASSERT(TMC_CHOPCONF_TOFF_MASK == 0x0Fu,     chopconf_toff_bits);
TMC_CFG_STATIC_ASSERT(TMC_IHOLD_MASK == 0x1Fu,             ihold_field_mask);
TMC_CFG_STATIC_ASSERT(TMC_IRUN_SHIFT == 8 && TMC_IRUN_MASK == 0x1F00u, irun_field);
TMC_CFG_STATIC_ASSERT(TMC_IHOLDDELAY_SHIFT == 16 &&
                      TMC_IHOLDDELAY_MASK == 0x000F0000u,  iholddelay_field);

/* ---- 待下发计划参数 ---------------------------------------------------------
 * 位置初始化: { irun, ihold, ihold_delay, tpowerdown, vsense, mstep_sel, mres }
 * ⚠ 全部为"待验证配置", 不是已实测事实。 */
static const tmc_drv_config_t tmc_cfg_planned = { 20, 8, 4, 8, 1, 0, 0 };

/* 换算输入: RSENSE 取模块商品页暂定 0.11R, VREF 取操作员万用表实测 1.27V。
 * 二者都不是本固件可验证的量, 只用于给理论电流。 */
#define TMC_CFG_RSENSE_MOHM   110u
#define TMC_CFG_VREF_MV       1270u

/* 目标值(由上面计划参数推导, 单元测试逐项核对, 防手改漂移) */
#define TMC_CFG_TGT_GCONF     0x00000141u   /* 基线 0x101 | pdn_disable(bit6) */
#define TMC_CFG_TGT_CHOPCONF  0x15030053u   /* 基线 | vsense(bit17) */
#define TMC_CFG_TGT_IHOLD_IRUN 0x00041408u  /* ihold=8 irun=20 dly=4 */
#define TMC_CFG_TGT_TPOWERDOWN 0x00000008u

/* ---- F2b: 配置执行器的状态放进实例 (P1-C / P1-D) --------------------------
 * 审核意见(P1-C): 自测只保存 state 与 bus, 退出时强制 done_valid=FALSE 并清零
 * fail 元数据, 没有保存/恢复 done_gconf/done_chop/done; 而且把全局 tmc_cfg_bus
 * 临时换成假总线, 其它线程在此期间可能踩到假总线。根因是"执行器状态是全局的"。
 * 现在整套状态 + 接缝 + 同步都在 tmc_cfg_ctx_t 里:
 *   - 生产: 文件静态实例 tmc_cfg_prod, 只有公共 API 经它访问硬件
 *   - 自测: 栈上局部实例 + 静态假总线 vtable + 实例私有数据, 生产实例零接触
 * 锁纪律 (P1-D):
 *   txn  : 事务锁, 覆盖"整段序列"(apply / 完整性复核 / motor_arm 的决策段)。
 *          只在 UI/Motor 线程取; Safety 停机路径绝不取 -> 停机永不被配置事务阻塞。
 *          非阻塞 try-take, 取不到就返回 -RT_EBUSY 让调用方 fail closed。
 *          顺序: 先 txn 后 lock; 反向禁止。
 *   lock : 叶锁, 只保护状态字段与快照的读改写。持有期间不做 UART、不调用任何
 *          其它子系统 API -> 不可能与其它锁构成环。
 * 两把锁未 init 时(lock_ok/txn_ok=FALSE)自动跳过, 与本工程既有 idiom 一致。 */

typedef struct
{
    rt_err_t (*open) (void *priv);
    rt_err_t (*read) (void *priv, rt_uint8_t reg, rt_uint32_t *v);
    rt_err_t (*write)(void *priv, rt_uint8_t reg, rt_uint32_t v); /* 内含 IFCNT 到达确认 */
    rt_bool_t (*link_healthy)(void *priv);  /* FALSE = 链路不可信, 不猜 */
} tmc_cfg_bus_t;

/* 运行态写入门禁提供者: 返回 TMC_WGATE_* 位掩码(0=全通)。生产实例查真实子系统,
 * 自测实例返回受控值 —— 这样"门禁拒绝写入"本身也是可离线断言的对象。 */
typedef rt_uint32_t (*tmc_cfg_gate_fn)(void *priv);

typedef struct
{
    /* 生命周期状态 */
    tmc_drv_cfg_state_t state;
    rt_uint8_t  fail_reg;                    /* 失败发生在哪个寄存器(0=无) */
    rt_err_t    last_err;
    rt_bool_t   fail_from_integrity;
    /* 成功下发后的快照: 断电/复位后芯片寄存器回默认值, 而 MCU 侧不会自动知道,
     * 只能靠"已下发值 vs 现值"比对照出来。done_valid=FALSE = 这份快照不可信。 */
    rt_uint32_t      done_gconf;
    rt_uint32_t      done_chop;
    tmc_drv_config_t done;
    rt_bool_t        done_valid;
    /* 接缝: bus/gate 是无状态 vtable, 实例数据一律经 priv */
    const tmc_cfg_bus_t *bus;
    tmc_cfg_gate_fn      gate;
    void                *priv;
    /* 同步 */
    struct rt_mutex lock;
    struct rt_mutex txn;
    rt_bool_t       lock_ok;
    rt_bool_t       txn_ok;
} tmc_cfg_ctx_t;

static void tmc_cfg_lock(tmc_cfg_ctx_t *ctx)
{
    if (ctx->lock_ok) rt_mutex_take(&ctx->lock, RT_WAITING_FOREVER);
}

static void tmc_cfg_unlock(tmc_cfg_ctx_t *ctx)
{
    if (ctx->lock_ok) rt_mutex_release(&ctx->lock);
}

/* F2c R02: 事务锁不可用 = 无法串行化 = **拒绝操作**, 不再退化成"无锁也放行"。
 * 内核未就绪与互斥量创建失败都走这条路: apply/integrity 得到 -RT_ENOSYS, 第 5 门
 * 保持闭合。宁可下发不了配置, 也不能带着未知并发状态写芯片。 */
static rt_bool_t tmc_cfg_txn_take(tmc_cfg_ctx_t *ctx)
{
    if (!ctx->txn_ok) return RT_FALSE;
    return (rt_mutex_take(&ctx->txn, RT_WAITING_NO) == RT_EOK) ? RT_TRUE : RT_FALSE;
}

static void tmc_cfg_txn_give(tmc_cfg_ctx_t *ctx)
{
    if (ctx->txn_ok) (void)rt_mutex_release(&ctx->txn);
}

/* 返回 RT_EOK 当且仅当**两把锁都**创建成功; 失败时实例保持"未同步"(不可操作)。 */
static rt_err_t tmc_cfg_ctx_init(tmc_cfg_ctx_t *ctx, const tmc_cfg_bus_t *bus,
                                 tmc_cfg_gate_fn gate, void *priv,
                                 const char *nm_lock, const char *nm_txn)
{
    rt_memset(ctx, 0, sizeof(*ctx));
    ctx->state     = TMC_DRV_UNCONFIGURED;
    ctx->bus       = bus;
    ctx->gate      = gate;
    ctx->priv      = priv;
    ctx->lock_ok   = RT_FALSE;
    ctx->txn_ok    = RT_FALSE;
    if (rt_mutex_init(&ctx->lock, nm_lock, RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ENOSYS;
    ctx->lock_ok = RT_TRUE;
    if (rt_mutex_init(&ctx->txn, nm_txn, RT_IPC_FLAG_PRIO) != RT_EOK)
    {
        (void)rt_mutex_detach(&ctx->lock);
        ctx->lock_ok = RT_FALSE;
        return -RT_ENOSYS;
    }
    ctx->txn_ok = RT_TRUE;
    return RT_EOK;
}

static void tmc_cfg_ctx_detach(tmc_cfg_ctx_t *ctx)
{
    if (ctx->txn_ok)  (void)rt_mutex_detach(&ctx->txn);
    if (ctx->lock_ok) (void)rt_mutex_detach(&ctx->lock);
    ctx->txn_ok  = RT_FALSE;
    ctx->lock_ok = RT_FALSE;
}

/* ---- 生产接缝: 真实 UART2(协议层的 write 已内含 IFCNT 到达确认) ---- */
static rt_err_t tmc_cfg_hw_open(void *priv)
{
    (void)priv;
    return tmc2209_init();
}

static rt_err_t tmc_cfg_hw_read(void *priv, rt_uint8_t reg, rt_uint32_t *v)
{
    (void)priv;
    return tmc2209_read_register(TMC_ADDR_DEFAULT, reg, v);
}

static rt_err_t tmc_cfg_hw_write(void *priv, rt_uint8_t reg, rt_uint32_t v)
{
    (void)priv;
    return tmc2209_write_register_confirmed(TMC_ADDR_DEFAULT, reg, v);
}

/* 只有 SUBSYS_OK 算健康: DEGRADED(最近一次传输失败)/FAILED(uart2 打不开)/
 * UNINIT(未初始化)一律不可信 -> 配置写入与完整性复核都 fail closed。 */
static rt_bool_t tmc_cfg_hw_link_healthy(void *priv)
{
    (void)priv;
    return (tmc2209_get_health() == SUBSYS_OK) ? RT_TRUE : RT_FALSE;
}

static const tmc_cfg_bus_t tmc_cfg_bus_real =
{
    tmc_cfg_hw_open, tmc_cfg_hw_read, tmc_cfg_hw_write, tmc_cfg_hw_link_healthy
};

/* ---- 生产实例(声明在门禁函数之前, 门禁要读它的锁状态) ---- */
static tmc_cfg_ctx_t tmc_cfg_prod;
static rt_bool_t     tmc_cfg_prod_ready = RT_FALSE;

/* ---------- 生产运行态门禁 (P0-B / F2c R02) ----------
 * 每次 apply 之前重新求值, 不用"宏以前置硬件验收"替代"每次操作的条件检查"。
 * 本函数全部是纯只读(不产生任何 UART 流量), 可以在 system_status 里直接打印。
 * ⚠ 调用点已持有 txn, 因此这里取 mot_lock 的顺序是 txn -> mot_lock。 */
static rt_uint32_t tmc_cfg_gate_prod(void *priv)
{
    rt_uint32_t bad = 0;
    motor_snapshot_t snap;

    (void)priv;

    /* R02: 两把锁没建起来 = 无法保证写入序列与 arm/复核互斥 → 直接挡住 */
    if (!tmc_cfg_prod_ready || !tmc_cfg_prod.lock_ok || !tmc_cfg_prod.txn_ok)
                                             bad |= TMC_WGATE_NOT_SYNCED;

    if (!TMC_DRIVE_CONFIG_HW_VALIDATED)      bad |= TMC_WGATE_HW_NOT_VALIDATED;

    /* 改电流档/斩波器参数时电机必须彻底停着且未使能 */
    if (motor_get_snapshot(&snap) != RT_EOK) bad |= TMC_WGATE_MOTOR_NOT_QUIET;
    else if (snap.armed || snap.state != MOTOR_IDLE ||
             snap.current_hz != 0u || snap.target_hz != 0u)
                                             bad |= TMC_WGATE_MOTOR_NOT_QUIET;

    /* 驱动必须处于禁能请求(焊盘实测 LOW)。注意: 现板与门/非门损坏, 这条只能证明
     * MCU 侧的请求电平, 证明不了 TMC_ENN 已禁能 —— 所以它必要但不充分, 编译期
     * 那道门不会因为本位通过就允许翻转。 */
    if (!safety_drv_enable_is_low())         bad |= TMC_WGATE_DRV_PAD_NOT_LOW;
    if (safety_state_get() != SAFETY_READY)  bad |= TMC_WGATE_NOT_READY;
    if (safety_fault_get() != FAULT_NONE)    bad |= TMC_WGATE_FAULT_LATCHED;
    if (!tmc_cfg_hw_link_healthy(RT_NULL))   bad |= TMC_WGATE_LINK_UNHEALTHY;

    return bad;
}

static void tmc_cfg_print_gate(rt_uint32_t bad)
{
    rt_kprintf("[TMC-CFG] write gate mask=0x%X:", (unsigned)bad);
    if (bad & TMC_WGATE_NOT_SYNCED)       rt_kprintf(" executor-not-synced(NO-LOCK=refuse)");
    if (bad & TMC_WGATE_HW_NOT_VALIDATED) rt_kprintf(" hw-chain-not-validated(compile)");
    if (bad & TMC_WGATE_MOTOR_NOT_QUIET)  rt_kprintf(" motor-not-quiet");
    if (bad & TMC_WGATE_DRV_PAD_NOT_LOW)  rt_kprintf(" drv-enable-pad-not-low");
    if (bad & TMC_WGATE_NOT_READY)        rt_kprintf(" safety-not-READY");
    if (bad & TMC_WGATE_FAULT_LATCHED)    rt_kprintf(" fault-not-cleared");
    if (bad & TMC_WGATE_LINK_UNHEALTHY)   rt_kprintf(" uart-link-unhealthy");
    rt_kprintf("\n");
}

/* 幂等。**只有两把锁都创建成功才置 prod_ready**(R02); 失败时保持未同步, 于是
 * 门禁带 TMC_WGATE_NOT_SYNCED、apply/integrity 得到 -RT_ENOSYS、第 5 门闭合。
 * 由 tmc2209_init()(bootstrap 单线程阶段)调用; 在此之前被调用也只是"未同步"。 */
static rt_err_t tmc_cfg_prod_sync(void)
{
    rt_err_t e;

    if (tmc_cfg_prod_ready) return RT_EOK;
    e = tmc_cfg_ctx_init(&tmc_cfg_prod, &tmc_cfg_bus_real, tmc_cfg_gate_prod,
                         RT_NULL, "tmcCfgL", "tmcCfgT");
    if (e != RT_EOK)
    {
        rt_kprintf("[TMC-CFG] !! CRITICAL: 执行器互斥量创建失败(%d) -> "
                   "配置写入与复核一律拒绝(-RT_ENOSYS), 第 5 门保持闭合\n", (int)e);
        tmc_cfg_prod_ready = RT_FALSE;
        return e;
    }
    tmc_cfg_prod_ready = RT_TRUE;
    return RT_EOK;
}

/* ---- 纯函数: 状态名 / 理论电流 / 打包 / 方向守卫 ---- */
const char *tmc_drv_cfg_state_name(tmc_drv_cfg_state_t s)
{
    switch (s)
    {
    case TMC_DRV_UNCONFIGURED: return "UNCONFIGURED";
    case TMC_DRV_CONFIGURING:  return "CONFIGURING";
    case TMC_DRV_CONFIGURED:   return "CONFIGURED";
    case TMC_DRV_FAILED:       return "FAILED";
    default:                   return "UNKNOWN";
    }
}
/* F2b: 生产状态的读取一律经 ctx 叶锁取一致值(此前是无锁裸读)。 */
tmc_drv_cfg_state_t tmc2209_get_config_state(void)
{
    tmc_drv_cfg_state_t s;

    tmc_cfg_lock(&tmc_cfg_prod);
    s = tmc_cfg_prod.state;
    tmc_cfg_unlock(&tmc_cfg_prod);
    return s;
}

void tmc2209_get_planned_config(tmc_drv_config_t *out)
{
    if (out != RT_NULL) *out = tmc_cfg_planned;
}

/* P1-D: 快照有效性由 done_valid 单点决定。配置开始/失败/失联/复位都会把它清掉,
 * 所以这里不会把"上一轮的旧值"当成已下发的有效配置返回。 */
rt_bool_t tmc2209_get_applied_config(rt_uint32_t *gconf, rt_uint32_t *chop,
                                     tmc_drv_config_t *out)
{
    rt_bool_t valid;

    tmc_cfg_lock(&tmc_cfg_prod);
    valid = tmc_cfg_prod.done_valid;
    if (valid)
    {
        if (gconf) *gconf = tmc_cfg_prod.done_gconf;
        if (chop)  *chop  = tmc_cfg_prod.done_chop;
        if (out)   *out   = tmc_cfg_prod.done;
    }
    tmc_cfg_unlock(&tmc_cfg_prod);
    return valid;
}

/* CS*1000。手册: "IRUN, IHOLD: 1/32 to 32/32 of full-scale current" */
rt_uint32_t tmc_cs_milli(rt_uint8_t field)
{
    if (field > 31u) field = 31u;
    return ((rt_uint32_t)field + 1u) * 1000u / 32u;
}

/* I_peak[mA] = V_FS/(RSENSE+Rds) × CS × min(VREF,2.5V)/2.5V。
 * 口径由手册自身算例校准: "set vsense for max. 180mV at sense resistor
 * (0R15: 1.1A peak)" -> 0.18V/(0.15+0.02) = 1.06A ≈ 1.1A peak。 */
rt_uint32_t tmc_current_peak_ma(rt_uint32_t vfs_mv, rt_uint32_t rsense_mohm,
                                rt_uint32_t vref_mv, rt_uint8_t irun)
{
    rt_uint64_t num;
    rt_uint32_t rtot = rsense_mohm + TMC_SENSE_RDS_MOHM;

    if (rtot == 0u) return 0u;
    if (vref_mv > TMC_VREF_FULL_MV) vref_mv = TMC_VREF_FULL_MV;   /* >=2.5V=100% */

    num = (rt_uint64_t)vfs_mv * 1000ull
          * (rt_uint64_t)tmc_cs_milli(irun)
          * (rt_uint64_t)vref_mv;
    num /= (rt_uint64_t)rtot * 1000ull * TMC_VREF_FULL_MV;
    return (rt_uint32_t)num;
}

rt_uint32_t tmc_current_rms_ma(rt_uint32_t vfs_mv, rt_uint32_t rsense_mohm,
                               rt_uint32_t vref_mv, rt_uint8_t irun)
{
    rt_uint64_t p = (rt_uint64_t)tmc_current_peak_ma(vfs_mv, rsense_mohm,
                                                     vref_mv, irun);
    return (rt_uint32_t)((p * 10000ull + 7071ull) / 14142ull);
}

rt_uint32_t tmc_ihold_irun_pack(const tmc_drv_config_t *c)
{
    rt_uint32_t ih = (c->ihold       > 31u) ? 31u : c->ihold;
    rt_uint32_t ir = (c->irun         > 31u) ? 31u : c->irun;
    rt_uint32_t dl = (c->ihold_delay  > 15u) ? 15u : c->ihold_delay;

    return (ih & TMC_IHOLD_MASK)
           | ((ir << TMC_IRUN_SHIFT) & TMC_IRUN_MASK)
           | ((dl << TMC_IHOLDDELAY_SHIFT) & TMC_IHOLDDELAY_MASK);
}

/* vsense 由 1 改回 0 会把满量程从 180mV 抬到 325mV(约 1.8 倍)。手册要求换
 * vsense 时同步下调 IRUN 以防过流 —— 而 IHOLD_IRUN 只写不可读, 芯片里现在是
 * 哪一档无从回读核实, 因此该方向一律拒绝: 要改回去只能断电复位后从已知起点
 * 重新下发整套配置。 */
rt_bool_t tmc_change_is_current_safe(rt_uint8_t vsense_old, rt_uint8_t vsense_new)
{
    if (vsense_old == 1u && vsense_new == 0u) return RT_FALSE;
    return RT_TRUE;
}

/* ---- RMW 计算(纯函数, 便于离线断言"不粗暴覆盖无关字段") ---- */
rt_uint32_t tmc_gconf_next(rt_uint32_t cur, const tmc_drv_config_t *c)
{
    rt_uint32_t v = cur & ~(TMC_GCONF_I_SCALE_ANALOG | TMC_GCONF_PDN_DISABLE |
                            TMC_GCONF_MSTEP_REG_SELECT);
    v |= TMC_GCONF_I_SCALE_ANALOG | TMC_GCONF_PDN_DISABLE;
    if (c->mstep_reg_select) v |= TMC_GCONF_MSTEP_REG_SELECT;
    return v;
}

rt_uint32_t tmc_chopconf_next(rt_uint32_t cur, const tmc_drv_config_t *c)
{
    rt_uint32_t v = cur & ~TMC_CHOPCONF_VSENSE;
    if (c->vsense) v |= TMC_CHOPCONF_VSENSE;
    if (c->mstep_reg_select)
        v = (v & ~((rt_uint32_t)TMC_CHOPCONF_MRES_MASK << TMC_CHOPCONF_MRES_SHIFT))
            | (((rt_uint32_t)c->mres & TMC_CHOPCONF_MRES_MASK)
               << TMC_CHOPCONF_MRES_SHIFT);
    return v;
}

/* ---- 失败提交与单项写入(F2b: 全部经 ctx, 并在锁内一次性提交) ---- */

/* P1-D: 任何失败/失联/芯片复位都必须让 done_valid 立即失效 —— 否则上一轮快照会
 * 继续被 tmc2209_get_applied_config() 当作"已下发的有效配置"返回, 第 5 门也不会
 * 重新闭合。打印放在叶锁之外, 保持锁内不做什么耗时/可阻塞的事。 */
static rt_err_t tmc_cfg_fail_ctx(tmc_cfg_ctx_t *ctx, rt_uint8_t reg, rt_err_t e,
                                 const char *stage, rt_bool_t from_integrity)
{
    rt_err_t ret = (e == RT_EOK) ? -RT_ERROR : e;

    tmc_cfg_lock(ctx);
    ctx->fail_reg            = reg;
    ctx->last_err            = ret;
    ctx->fail_from_integrity = from_integrity;
    ctx->state               = TMC_DRV_FAILED;
    ctx->done_valid          = RT_FALSE;
    tmc_cfg_unlock(ctx);

    rt_kprintf("[TMC-CFG] FAILED at %s (reg=0x%02X err=%d) - 序列已中止, 芯片可能"
               "处于部分配置状态; 已下发快照作废, 禁止使能, 需重新 apply 或断电复位\n",
               stage, reg, (int)ret);
    return ret;
}

/* 写 + IFCNT 到达确认 +(可读寄存器)读回比对。只写寄存器不发非法读。
 * -RT_EIO 有三条来源: 写未被芯片接受 / 读回传输失败 / 读回值与写入值不符。
 * 第三条(F2b 新增用例覆盖)意味着"IFCNT+1 但内容不对", 同样必须中止。 */
static rt_err_t tmc_cfg_write_one_ctx(tmc_cfg_ctx_t *ctx, rt_uint8_t reg,
                                      rt_uint32_t val, rt_bool_t readable)
{
    rt_err_t    e = ctx->bus->write(ctx->priv, reg, val);
    rt_uint32_t rb = 0;

    if (e != RT_EOK) return e;                    /* IFCNT 未 +1 = 芯片没接受 */
    if (!readable)   return RT_EOK;               /* 只写: 不读回 */

    e = ctx->bus->read(ctx->priv, reg, &rb);
    if (e != RT_EOK) return e;
    if (rb != val)
    {
        rt_kprintf("[TMC-CFG] readback MISMATCH reg=0x%02X wrote=%08X read=%08X\n",
                   reg, (unsigned)val, (unsigned)rb);
        return -RT_EIO;
    }
    return RT_EOK;
}

/* 配置序列本体。
 * P0-B: 运行态门禁在任何总线访问(连 open 都在其后)之前求值, 不通过就一位不写、
 *       状态与快照保持原样。
 * P1-D: 整段被 ctx 的 txn 锁串行化; 进入 CONFIGURING 即作废旧快照, 成功后才提交
 *       新快照。自测把 ctx 换成局部实例(假总线), 不需要临时篡改生产全局。 */
static rt_err_t tmc_cfg_apply_ctx(tmc_cfg_ctx_t *ctx, const tmc_drv_config_t *c)
{
    rt_uint32_t gconf = 0, chop = 0, new_g = 0, new_c = 0, gates;
    rt_uint8_t  old_vs;
    rt_err_t    e;

    /* F2c R02: 先把"锁不可用"与"锁被占用"分开 —— 前者是能力缺失(ENOSYS),
     * 后者是并发时序(EBUSY)。两种都在产生任何总线动作之前拒绝。 */
    if (!ctx->txn_ok)
    {
        rt_kprintf("[TMC-CFG] REFUSED: 执行器互斥量不可用(-RT_ENOSYS),"
                   " 无法串行化 -> 不发出任何总线写\n");
        return -RT_ENOSYS;
    }
    if (!tmc_cfg_txn_take(ctx))
    {
        rt_kprintf("[TMC-CFG] REFUSED: 配置/arm 事务正在进行(-RT_EBUSY),"
                   " 本次不发出任何总线写\n");
        return -RT_EBUSY;
    }

    gates = (ctx->gate != RT_NULL) ? ctx->gate(ctx->priv) : 0u;
    if (gates != 0u)
    {
        rt_kprintf("[TMC-CFG] REFUSED: 写入门禁未通过 - 未发出任何总线写,"
                   " 状态保持 %s\n", tmc_drv_cfg_state_name(ctx->state));
        tmc_cfg_print_gate(gates);
        tmc_cfg_txn_give(ctx);
        return -RT_EPERM;
    }

    tmc_cfg_lock(ctx);
    if (ctx->state == TMC_DRV_CONFIGURING)
    {
        tmc_cfg_unlock(ctx);
        tmc_cfg_txn_give(ctx);
        rt_kprintf("[TMC-CFG] REFUSED: 上一次配置尚未结束(CONFIGURING)\n");
        return -RT_EBUSY;
    }
    ctx->state      = TMC_DRV_CONFIGURING;
    ctx->done_valid = RT_FALSE;           /* 重新下发期间旧快照不可信 */
    tmc_cfg_unlock(ctx);

    rt_kprintf("[TMC-CFG] apply start: irun=%u ihold=%u dly=%u tpd=%u vsense=%u\n",
               c->irun, c->ihold, c->ihold_delay, c->tpowerdown, c->vsense);

    if (ctx->bus->open(ctx->priv) != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, 0, -RT_ERROR, "uart-open", RT_FALSE);
        goto out;
    }

    /* ---- 1) GCONF: 只置 i_scale_analog + pdn_disable, 其余保留(含 OTP 派生位) */
    e = ctx->bus->read(ctx->priv, TMC_REG_GCONF, &gconf);
    if (e != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_GCONF, e, "GCONF-read", RT_FALSE);
        goto out;
    }

    /* internal_Rsense=1 时 VREF 引脚被内部拉到 GND, 外部 0.11R 模型与 VREF
     * 分档一起失效 -> 不是"写不进去"而是"算了也不对", 必须 fail closed。 */
    if (gconf & TMC_GCONF_INTERNAL_RSENSE)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_GCONF, -RT_EPERM,
                             "GCONF.internal_Rsense=1", RT_FALSE);
        goto out;
    }

    new_g = tmc_gconf_next(gconf, c);
    rt_kprintf("[TMC-CFG] GCONF %08X -> %08X\n",
               (unsigned)gconf, (unsigned)new_g);
    e = tmc_cfg_write_one_ctx(ctx, TMC_REG_GCONF, new_g, RT_TRUE);
    if (e != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_GCONF, e, "GCONF-write", RT_FALSE);
        goto out;
    }

    /* ---- 2) CHOPCONF: 只动 vsense(及可选 MRES), TOFF/HSTRT/HEND/TBL 全保留 */
    e = ctx->bus->read(ctx->priv, TMC_REG_CHOPCONF, &chop);
    if (e != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, e, "CHOPCONF-read", RT_FALSE);
        goto out;
    }

    old_vs = (chop & TMC_CHOPCONF_VSENSE) ? 1u : 0u;
    if (!tmc_change_is_current_safe(old_vs, c->vsense))
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, -RT_EPERM,
                             "CHOPCONF.overcurrent-dir", RT_FALSE);
        goto out;
    }

    /* 不变式: TOFF 必须原样保留且非 0(TOFF=0 会关断所有桥) */
    new_c = tmc_chopconf_next(chop, c);
    if ((new_c & TMC_CHOPCONF_TOFF_MASK) != (chop & TMC_CHOPCONF_TOFF_MASK))
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, -RT_EINVAL,
                             "CHOPCONF.TOFF-changed", RT_FALSE);
        goto out;
    }
    if ((new_c & TMC_CHOPCONF_TOFF_MASK) == 0u)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, -RT_EINVAL,
                             "CHOPCONF.TOFF-zero", RT_FALSE);
        goto out;
    }

    rt_kprintf("[TMC-CFG] CHOPCONF %08X -> %08X (vsense %u -> %u)\n",
               (unsigned)chop, (unsigned)new_c, old_vs, c->vsense);
    e = tmc_cfg_write_one_ctx(ctx, TMC_REG_CHOPCONF, new_c, RT_TRUE);
    if (e != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, e, "CHOPCONF-write", RT_FALSE);
        goto out;
    }

    /* ---- 3) IHOLD_IRUN: 只写寄存器, 到达凭证只有 IFCNT+1 ---- */
    rt_kprintf("[TMC-CFG] IHOLD_IRUN <- %08X (只写, 不读回)\n",
               (unsigned)tmc_ihold_irun_pack(c));
    e = tmc_cfg_write_one_ctx(ctx, TMC_REG_IHOLD_IRUN, tmc_ihold_irun_pack(c), RT_FALSE);
    if (e != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_IHOLD_IRUN, e, "IHOLD_IRUN-write", RT_FALSE);
        goto out;
    }

    /* ---- 4) TPOWERDOWN: 同样按只写处理(不做非法读回) ---- */
    rt_kprintf("[TMC-CFG] TPOWERDOWN(0x%02X) <- %u\n",
               TMC_REG_TPOWERDOWN, c->tpowerdown);
    e = tmc_cfg_write_one_ctx(ctx, TMC_REG_TPOWERDOWN, c->tpowerdown, RT_FALSE);
    if (e != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_TPOWERDOWN, e, "TPOWERDOWN-write", RT_FALSE);
        goto out;
    }

    /* 全部到位: 一次性提交快照与 CONFIGURED */
    tmc_cfg_lock(ctx);
    ctx->done_gconf = new_g;
    ctx->done_chop  = new_c;
    ctx->done       = *c;
    ctx->done_valid = RT_TRUE;
    ctx->state      = TMC_DRV_CONFIGURED;
    ctx->fail_reg   = 0;
    ctx->last_err   = RT_EOK;
    ctx->fail_from_integrity = RT_FALSE;
    tmc_cfg_unlock(ctx);
    rt_kprintf("[TMC-CFG] CONFIGURED (各次写入均经 IFCNT 确认; GCONF/CHOPCONF"
               " 读回一致)。电流值仍为理论计算, 未实测。\n");
    e = RT_EOK;

out:
    tmc_cfg_txn_give(ctx);
    return e;
}

rt_err_t tmc2209_apply_config(void)
{
    tmc_cfg_prod_sync();
    return tmc_cfg_apply_ctx(&tmc_cfg_prod, &tmc_cfg_planned);
}

/* 运行态门禁的只读观察口(system_status / tmc_config_show 用, 不产生总线流量)。 */
rt_uint32_t tmc2209_get_write_gate_mask(void)
{
    return tmc_cfg_gate_prod(RT_NULL);
}

/* P1-C 修复点: 必须同时看 state 和 done_valid。只看 state 会让"状态仍是
 * CONFIGURED 而快照已作废"的组合从第 5 门假通过(旧版自测退出时正是这个组合)。
 * F2c R02: 叶锁不可用时连状态都无法原子读取 -> 不猜, 直接返回 FALSE(门闭合)。 */
static rt_bool_t tmc_cfg_ok_for_arm_ctx(tmc_cfg_ctx_t *ctx)
{
    rt_bool_t ok;

    if (!ctx->lock_ok) return RT_FALSE;
    tmc_cfg_lock(ctx);
    ok = (ctx->state == TMC_DRV_CONFIGURED && ctx->done_valid) ? RT_TRUE : RT_FALSE;
    tmc_cfg_unlock(ctx);
    return ok;
}

rt_bool_t tmc2209_drive_config_ok_for_arm(void)
{
    return tmc_cfg_ok_for_arm_ctx(&tmc_cfg_prod);
}

/* ---------- F2b P1-D: 配置事务的外部串行化口(motor_arm 决策段用) ----------
 * 非阻塞: 取不到就返回 -RT_EBUSY, 调用方必须拒绝使能(fail closed)。
 * 同线程可重入(RT-Thread 互斥量带持有计数), 所以 arm 段内的完整性复核不会自锁。
 * F2c R02: 互斥量不可用返回 -RT_ENOSYS(能力缺失), 不再是"无锁也放行"的 RT_EOK。
 * ⚠ 停机路径(motor_emergency_stop / safety_force_shutdown)不得调用本函数 ——
 *   紧急停机永远不能被配置事务挡住。 */
rt_err_t tmc2209_cfg_txn_begin(void)
{
    if (!tmc_cfg_prod.txn_ok)
    {
        rt_kprintf("[MOT] cfg txn BEGIN refused: 执行器互斥量不可用(-RT_ENOSYS)\n");
        return -RT_ENOSYS;
    }
    return (rt_mutex_take(&tmc_cfg_prod.txn, RT_WAITING_NO) == RT_EOK)
           ? RT_EOK : -RT_EBUSY;
}

void tmc2209_cfg_txn_end(void)
{
    if (tmc_cfg_prod.txn_ok) (void)rt_mutex_release(&tmc_cfg_prod.txn);
}

/* 见头文件说明: 芯片断电/复位后寄存器回默认, 必须在这里被照出来。
 * F2b: 快照/状态先在叶锁内取一致副本, UART 读在锁外做(只由 txn 串行化),
 * 因此本函数不会持叶锁跨越总线调用。 */
static rt_err_t tmc_cfg_integrity_ctx(tmc_cfg_ctx_t *ctx)
{
    rt_uint32_t tgt_g = 0, tgt_c = 0, v = 0;
    tmc_drv_cfg_state_t st;
    rt_bool_t valid;
    rt_err_t  e;

    /* F2c R02: 没有事务锁就无从保证"复核期间没有并发写" -> 拒绝(不猜) */
    if (!ctx->txn_ok) return -RT_ENOSYS;
    if (!tmc_cfg_txn_take(ctx)) return -RT_EBUSY;   /* 正在 apply/arm: 此刻无法复核 */

    tmc_cfg_lock(ctx);
    st    = ctx->state;
    valid = ctx->done_valid;
    tgt_g = ctx->done_gconf;
    tgt_c = ctx->done_chop;
    tmc_cfg_unlock(ctx);

    if (st != TMC_DRV_CONFIGURED || !valid)
    {
        e = -RT_EPERM;                    /* 尚未 CONFIGURED / 快照已失效: 不猜 */
        goto out;
    }

    /* 链路本身已不可信时不猜: 读不到 != 配置还在 */
    if (!ctx->bus->link_healthy(ctx->priv))
    {
        e = tmc_cfg_fail_ctx(ctx, 0, -RT_EIO, "integrity:link-not-healthy", RT_TRUE);
        goto out;
    }

    v = 0;
    if (ctx->bus->read(ctx->priv, TMC_REG_GCONF, &v) != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_GCONF, -RT_EIO,
                             "integrity:GCONF-read", RT_TRUE);
        goto out;
    }
    if (v != tgt_g)
    {
        rt_kprintf("[TMC-CFG] !! GCONF 已偏离下发值 (%08X != %08X) - 芯片可能断电/复位\n",
                   (unsigned)v, (unsigned)tgt_g);
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_GCONF, -RT_EIO,
                             "integrity:GCONF-mismatch", RT_TRUE);
        goto out;
    }

    v = 0;
    if (ctx->bus->read(ctx->priv, TMC_REG_CHOPCONF, &v) != RT_EOK)
    {
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, -RT_EIO,
                             "integrity:CHOPCONF-read", RT_TRUE);
        goto out;
    }
    if (v != tgt_c)
    {
        rt_kprintf("[TMC-CFG] !! CHOPCONF 已偏离下发值 (%08X != %08X) - vsense 档可能已丢\n",
                   (unsigned)v, (unsigned)tgt_c);
        e = tmc_cfg_fail_ctx(ctx, TMC_REG_CHOPCONF, -RT_EIO,
                             "integrity:CHOPCONF-mismatch", RT_TRUE);
        goto out;
    }
    e = RT_EOK;

out:
    tmc_cfg_txn_give(ctx);
    return e;
}

rt_err_t tmc2209_config_integrity_check(void)
{
    tmc_cfg_prod_sync();
    return tmc_cfg_integrity_ctx(&tmc_cfg_prod);
}

/* ---------- MSH: 配置查看 / 受控下发 / 离线自检 ---------- */

static void tmc_config_show(void)
{
    tmc_drv_config_t c = tmc_cfg_planned;
    rt_uint32_t vfs = c.vsense ? TMC_SENSE_VFS_180_MV : TMC_SENSE_VFS_325_MV;
    rt_uint32_t run  = tmc_current_rms_ma(vfs, TMC_CFG_RSENSE_MOHM, TMC_CFG_VREF_MV, c.irun);
    rt_uint32_t hold = tmc_current_rms_ma(vfs, TMC_CFG_RSENSE_MOHM, TMC_CFG_VREF_MV, c.ihold);
    rt_uint32_t v = 0, gates, snap_g = 0, snap_c = 0;
    tmc_drv_cfg_state_t st;
    rt_uint8_t  fail_reg;
    rt_err_t    last_err;
    rt_bool_t   from_integrity, snap_valid;

    tmc_cfg_lock(&tmc_cfg_prod);
    st             = tmc_cfg_prod.state;
    fail_reg       = tmc_cfg_prod.fail_reg;
    last_err       = tmc_cfg_prod.last_err;
    from_integrity = tmc_cfg_prod.fail_from_integrity;
    snap_valid     = tmc_cfg_prod.done_valid;
    snap_g         = tmc_cfg_prod.done_gconf;
    snap_c         = tmc_cfg_prod.done_chop;
    tmc_cfg_unlock(&tmc_cfg_prod);

    rt_kprintf("[TMC-CFG] state=%s fail_reg=0x%02X last_err=%d from_integrity=%d"
               " snapshot_valid=%d\n", tmc_drv_cfg_state_name(st),
               fail_reg, (int)last_err, (int)from_integrity, (int)snap_valid);
    gates = tmc2209_get_write_gate_mask();
    rt_kprintf("[TMC-CFG] hw_write_gate(compile)=%s runtime_gate_mask=0x%X\n",
               TMC_DRIVE_CONFIG_HW_VALIDATED ? "OPEN" : "CLOSED", (unsigned)gates);
    if (gates) tmc_cfg_print_gate(gates);
    else rt_kprintf("[TMC-CFG] !! 运行态门禁全通, 但编译期门未翻转前仍然一位不写\n");
    rt_kprintf("[TMC-CFG] planned irun=%u ihold=%u dly=%u tpd=%u vsense=%u mstep_sel=%u mres=%u\n",
               c.irun, c.ihold, c.ihold_delay, c.tpowerdown,
               c.vsense, c.mstep_reg_select, c.mres);
    rt_kprintf("[TMC-CFG] targets GCONF=%08X CHOPCONF=%08X IHOLD_IRUN=%08X TPOWERDOWN(0x%02X)=%02X\n",
               (unsigned)TMC_CFG_TGT_GCONF, (unsigned)TMC_CFG_TGT_CHOPCONF,
               (unsigned)TMC_CFG_TGT_IHOLD_IRUN, TMC_REG_TPOWERDOWN,
               (unsigned)TMC_CFG_TGT_TPOWERDOWN);
    rt_kprintf("[TMC-CFG] 理论: V_FS=%umV Rsense=%umO(+%umO Rds) VREF=%umV\n",
               (unsigned)vfs, (unsigned)TMC_CFG_RSENSE_MOHM,
               (unsigned)TMC_SENSE_RDS_MOHM, (unsigned)TMC_CFG_VREF_MV);
    rt_kprintf("[TMC-CFG] 理论电流 run=%u mA RMS / hold=%u mA RMS"
               " (电机额定 1500 mA RMS)\n", (unsigned)run, (unsigned)hold);
    rt_kprintf("[TMC-CFG] !! 理论值, 未实测: VREF 为万用表读数(未在本固件核实),"
               " RSENSE 为商品页暂定值。不得当作实测电流使用。\n");

    if (snap_valid)
        rt_kprintf("[TMC-CFG] applied snapshot: GCONF=%08X CHOPCONF=%08X\n",
                   (unsigned)snap_g, (unsigned)snap_c);
    else
        rt_kprintf("[TMC-CFG] applied snapshot: 无效(从未成功下发, 或已因失败/失联作废)\n");

    if (tmc2209_read_register(TMC_ADDR_DEFAULT, TMC_REG_GCONF, &v) == RT_EOK)
        rt_kprintf("[TMC-CFG] live GCONF=%08X i_scale_analog=%u internal_Rsense=%u"
                   " en_spreadCycle=%u pdn_disable=%u mstep_reg_select=%u\n",
                   (unsigned)v, (unsigned)(v & 1u),
                   (unsigned)((v & TMC_GCONF_INTERNAL_RSENSE) ? 1u : 0u),
                   (unsigned)((v & TMC_GCONF_EN_SPREADCYCLE) ? 1u : 0u),
                   (unsigned)((v & TMC_GCONF_PDN_DISABLE) ? 1u : 0u),
                   (unsigned)((v & TMC_GCONF_MSTEP_REG_SELECT) ? 1u : 0u));
    else rt_kprintf("[TMC-CFG] live GCONF: 读不到(链路/供电未就绪)\n");

    v = 0;
    if (tmc2209_read_register(TMC_ADDR_DEFAULT, TMC_REG_CHOPCONF, &v) == RT_EOK)
        rt_kprintf("[TMC-CFG] live CHOPCONF=%08X TOFF=%u vsense(bit17)=%u MRES=%u\n",
                   (unsigned)v, (unsigned)(v & TMC_CHOPCONF_TOFF_MASK),
                   (unsigned)((v & TMC_CHOPCONF_VSENSE) ? 1u : 0u),
                   (unsigned)((v >> TMC_CHOPCONF_MRES_SHIFT) & TMC_CHOPCONF_MRES_MASK));
    else rt_kprintf("[TMC-CFG] live CHOPCONF: 读不到(链路/供电未就绪)\n");
}
MSH_CMD_EXPORT(tmc_config_show, show planned TMC drive config and theoretical current);

static void tmc_config_apply(int argc, char **argv)
{
    if (argc != 2 || rt_strcmp(argv[1], "CONFIRM") != 0)
    {
        rt_kprintf("usage: tmc_config_apply CONFIRM\n");
        rt_kprintf("当前状态: %s, 编译期门禁=%s, 运行态门禁 mask=0x%X。\n",
                   tmc_drv_cfg_state_name(tmc2209_get_config_state()),
                   TMC_DRIVE_CONFIG_HW_VALIDATED ? "OPEN" : "CLOSED",
                   (unsigned)tmc2209_get_write_gate_mask());
        rt_kprintf("写入会改变驱动电流档位, 因此必须显式 CONFIRM, 且编译期门与"
                   "全部运行态条件同时满足(电机静止未 armed、DRV_ENABLE 实测 LOW、"
                   "Safety=READY 无锁存故障、UART 健康)。\n");
        return;
    }
    (void)tmc2209_apply_config();
}
MSH_CMD_EXPORT(tmc_config_apply, write TMC drive config after hardware validation);

/* 纯计算自检: 只验证算术/打包/守卫, 不碰总线。数值锚点取自任务给定工况
 * (vsense=1, RSENSE=0.11R, VREF=1.27V): 理论 run≈326 mA, hold≈140 mA RMS。
 * 锚点是"待验证配置的理论值", 不是实测电流。 */
static void tmc_calc_selftest(void)
{
    tmc_drv_config_t c = { 20, 8, 4, 8, 1, 0, 0 };
    int pass = 1;
    rt_uint32_t v;

    v = tmc_current_rms_ma(TMC_SENSE_VFS_180_MV, 110u, 1270u, 20u);
    rt_kprintf("[TMC-ST] run  = %u mA RMS (expect 326 +/-3)\n", (unsigned)v);
    if (v < 323u || v > 329u) pass = 0;

    v = tmc_current_rms_ma(TMC_SENSE_VFS_180_MV, 110u, 1270u, 8u);
    rt_kprintf("[TMC-ST] hold = %u mA RMS (expect 140 +/-3)\n", (unsigned)v);
    if (v < 137u || v > 143u) pass = 0;

    v = tmc_current_peak_ma(TMC_SENSE_VFS_180_MV, 110u, 1270u, 20u);
    rt_kprintf("[TMC-ST] peak = %u mA (expect 461 +/-4)\n", (unsigned)v);
    if (v < 457u || v > 465u) pass = 0;

    if (tmc_cs_milli(31u) != 1000u || tmc_cs_milli(0u) != 31u)
    { rt_kprintf("[TMC-ST] CS bound FAIL (%u/%u)\n",
                 (unsigned)tmc_cs_milli(31u), (unsigned)tmc_cs_milli(0u)); pass = 0; }

    if (tmc_current_rms_ma(180u, 110u, 3300u, 20u) !=
        tmc_current_rms_ma(180u, 110u, 2500u, 20u))
    { rt_kprintf("[TMC-ST] VREF clamp FAIL\n"); pass = 0; }

    v = tmc_ihold_irun_pack(&c);
    rt_kprintf("[TMC-ST] IHOLD_IRUN packed=0x%08X (ih=%u ir=%u dl=%u)\n",
               (unsigned)v, (unsigned)(v & TMC_IHOLD_MASK),
               (unsigned)((v & TMC_IRUN_MASK) >> TMC_IRUN_SHIFT),
               (unsigned)((v & TMC_IHOLDDELAY_MASK) >> TMC_IHOLDDELAY_SHIFT));
    if ((v & TMC_IHOLD_MASK) != 8u ||
        ((v & TMC_IRUN_MASK) >> TMC_IRUN_SHIFT) != 20u ||
        ((v & TMC_IHOLDDELAY_MASK) >> TMC_IHOLDDELAY_SHIFT) != 4u)
    { rt_kprintf("[TMC-ST] pack/unpack roundtrip FAIL\n"); pass = 0; }

    if (!tmc_change_is_current_safe(0u, 1u))
    { rt_kprintf("[TMC-ST] safe direction 0->1 wrongly refused\n"); pass = 0; }
    if (tmc_change_is_current_safe(1u, 0u))
    { rt_kprintf("[TMC-ST] overcurrent direction 1->0 NOT refused\n"); pass = 0; }
    if (!tmc_change_is_current_safe(1u, 1u) || !tmc_change_is_current_safe(0u, 0u))
    { rt_kprintf("[TMC-ST] no-change direction wrongly refused\n"); pass = 0; }

    rt_kprintf("[TMC-ST] %s (纯算术, 未与芯片通信)\n", pass ? "ALL PASS" : "FAILED");
}
MSH_CMD_EXPORT(tmc_calc_selftest, offline theoretical current and packing selftest);

/* ---------- F2b/F2c: 假总线离线自测(局部实例, 不碰生产 ctx, 不碰 UART) ----------
 * 相对 F2a 的加强(逐条对应审核报告 P1-C / P1-D / P2-F):
 *   1) 状态全部放进栈上局部实例 tmc_cfg_ctx_t + 静态假总线 vtable + 实例私有数据,
 *      生产实例零接触, 并用 T18 逐项比对证明(旧版会强制 done_valid=FALSE、
 *      清零 fail 元数据、把全局总线换成假的)。
 *   2) 假总线建模 IFCNT: 只有"被接受"的写才自增, 被拒的写不改寄存器内容;
 *      另有 open 失败、读污染、链路健康、运行门禁四类注入。
 *   3) 断言基于有序事件序列(预读→写→读回的先后位置), 而不是"曾经读过"。
 *   4) 新增失败路径 T11..T17: 回读不符 / internal_Rsense=1 / 运行门禁每一位 /
 *      跨线程占用事务 / open 失败 / CONFIGURING 窗口 / 快照替换。
 * F2c 再加强(对应复审 R01 / R02 / R06 / R03):
 *   R01 读污染改为"**该寄存器的第 N 次读**"注入: apply 是先读基线再写再读回,
 *       旧的一次性注入会被基线预读消费掉, 于是 T11 期待的回读不符永远不出现
 *       (复审报告指出的确定性测试缺陷)。现在 T11 用 nth=2 命中"写后读回",
 *       并新增 T11b/T11c/T12b 覆盖"基线预读本身被污染"这条独立路径。
 *   R02 局部实例锁不可用时: 所有依赖事务锁的用例判 SKIP, 且整体不得打印 PASS;
 *       另加 T21 断言"锁不可用 → apply/integrity/arm 判定全部拒绝"。
 *   R06 T14 的辅助线程必须确认退出后才销毁 IPC, 销毁前不删 semaphore。
 *   R03 T20 走"下发→模拟芯片复位→复核→拒绝"整条序列, 正是 motor_start 起转前
 *       新增复核所依赖的状态机行为。
 * ⚠ 本自测只在假总线上运行, 不代表任何真机行为; 上板执行为 HARDWARE-PENDING。 */

#define TMC_FX_LOG_MAX  40

typedef struct
{
    rt_uint8_t  reg;
    rt_uint32_t val;
    rt_uint8_t  is_write;
} tmc_fx_ev_t;

typedef struct
{
    tmc_fx_ev_t log[TMC_FX_LOG_MAX];
    int      n;               /* 已记录事件数 */
    int      writes;          /* 发起过的 write 次数(含未被接受的) */
    int      opens;           /* 发起过的 open 次数 */
    int      ifcnt;           /* 模型 IFCNT: 仅"被接受的写"自增 */
    int      fail_write_nth;  /* 第 N 次 write 不被接受(1 基, <=0=不失败) */
    rt_uint32_t gconf, chop;  /* 芯片里当前的可读寄存器值 */
    int      rd_gconf, rd_chop;       /* 每个可读寄存器被读过几次(F2c R01) */
    rt_uint8_t  corrupt_reg;  /* 被注入污染的寄存器(0xFF=不注入) */
    int         corrupt_nth;  /* 该寄存器的第 N 次读(1 基)返回污染值; 0=不注入 */
    rt_uint32_t corrupt_xor;  /* 污染方式: 返回值 XOR 该掩码 */
    rt_bool_t   open_fail;
    rt_bool_t   link_ok;
    rt_uint32_t gate_mask;    /* 注入给运行态门禁提供者的位掩码 */
} tmc_fx_t;

static void tmc_fx_log(tmc_fx_t *fx, rt_uint8_t reg, rt_uint32_t val, rt_uint8_t w)
{
    if (fx->n < TMC_FX_LOG_MAX)
    {
        fx->log[fx->n].reg = reg;
        fx->log[fx->n].val = val;
        fx->log[fx->n].is_write = w;
        fx->n++;
    }
}

static rt_err_t tmc_fx_open(void *priv)
{
    tmc_fx_t *fx = (tmc_fx_t *)priv;

    fx->opens++;
    return fx->open_fail ? -RT_ERROR : RT_EOK;
}

static rt_err_t tmc_fx_read(void *priv, rt_uint8_t reg, rt_uint32_t *v)
{
    tmc_fx_t *fx = (tmc_fx_t *)priv;
    rt_uint32_t out;
    int nth;

    if (reg == TMC_REG_GCONF)      { nth = ++fx->rd_gconf; out = fx->gconf; }
    else if (reg == TMC_REG_CHOPCONF) { nth = ++fx->rd_chop;  out = fx->chop; }
    else { tmc_fx_log(fx, reg, 0, 0); return -RT_EIO; }  /* 只写寄存器: 读它没有意义 */

    /* F2c R01: 注入按"该寄存器的第 N 次读"命中, 不再是一次性消费。
     * apply 的读序是 基线读(1) -> 写 -> 读回(2), 所以:
     *   nth=2 才是"写进去了但现值不对"(回读不符), nth=1 是"基线预读被污染"。
     * 旧实现把注入挂在第一次读上, T11 期待的失败永远不会发生(复审 R01)。 */
    if (fx->corrupt_nth > 0 && fx->corrupt_reg == reg && nth == fx->corrupt_nth)
        out ^= fx->corrupt_xor;

    tmc_fx_log(fx, reg, out, 0);
    *v = out;
    return RT_EOK;
}

/* IFCNT 语义: 被接受的写 -> ifcnt++ 且寄存器更新; 未被接受的写 -> 两者都不发生,
 * 调用方(write_one)拿到错误并中止。这就是真机"IFCNT 没 +1"的等价模型。 */
static rt_err_t tmc_fx_write(void *priv, rt_uint8_t reg, rt_uint32_t val)
{
    tmc_fx_t *fx = (tmc_fx_t *)priv;

    fx->writes++;
    if (fx->fail_write_nth > 0 && fx->writes == fx->fail_write_nth)
        return -RT_EIO;

    fx->ifcnt++;
    tmc_fx_log(fx, reg, val, 1);
    if (reg == TMC_REG_GCONF)         fx->gconf = val;
    else if (reg == TMC_REG_CHOPCONF) fx->chop  = val;
    return RT_EOK;
}

static rt_bool_t tmc_fx_link(void *priv)
{
    return ((tmc_fx_t *)priv)->link_ok ? RT_TRUE : RT_FALSE;
}

static rt_uint32_t tmc_fx_gate(void *priv)
{
    return ((tmc_fx_t *)priv)->gate_mask;
}

static const tmc_cfg_bus_t tmc_cfg_bus_fake =
{
    tmc_fx_open, tmc_fx_read, tmc_fx_write, tmc_fx_link
};

static void tmc_fx_reset(tmc_fx_t *fx)
{
    rt_memset(fx, 0, sizeof(*fx));
    fx->corrupt_reg = 0xFFu;
    fx->gconf    = 0x00000101u;         /* 手册复位默认(bit0 + bit8 = 1) */
    fx->chop     = 0x15010053u;         /* 本轮真机读回的上电基线 */
    fx->link_ok  = RT_TRUE;
    fx->gate_mask = 0u;
}

static int tmc_fx_find_write(tmc_fx_t *fx, rt_uint8_t reg, rt_uint32_t *val)
{
    int i;
    for (i = 0; i < fx->n; ++i)
        if (fx->log[i].is_write && fx->log[i].reg == reg)
        { if (val) *val = fx->log[i].val; return 1; }
    return 0;
}

static int tmc_fx_any_read_of(tmc_fx_t *fx, rt_uint8_t reg)
{
    int i;
    for (i = 0; i < fx->n; ++i)
        if (!fx->log[i].is_write && fx->log[i].reg == reg) return 1;
    return 0;
}

static int tmc_fx_write_count_of(tmc_fx_t *fx, rt_uint8_t reg)
{
    int i, n = 0;
    for (i = 0; i < fx->n; ++i)
        if (fx->log[i].is_write && fx->log[i].reg == reg) n++;
    return n;
}

/* 一次干净下发的完整轨迹是固定的 8 步。只断言"某寄存器被读过"证明不了写后
 * 读回, 位置断言才行(P2-F 第 1 条)。 */
static int tmc_fx_seq_ok(tmc_fx_t *fx, const tmc_fx_ev_t *exp, int n_exp)
{
    int i;

    if (fx->n != n_exp) return 0;
    for (i = 0; i < n_exp; ++i)
        if (fx->log[i].is_write != exp[i].is_write ||
            fx->log[i].reg      != exp[i].reg)      return 0;
    return 1;
}

static void tmc_fx_seq_dump(tmc_fx_t *fx)
{
    int i;

    rt_kprintf("[TMC-CFG-ST] 事件序列(%d):", fx->n);
    for (i = 0; i < fx->n && i < TMC_FX_LOG_MAX; ++i)
        rt_kprintf(" %c%02X", fx->log[i].is_write ? 'w' : 'r', fx->log[i].reg);
    rt_kprintf(" (writes=%d accepted=%d opens=%d)\n", fx->writes, fx->ifcnt, fx->opens);
}

static rt_bool_t tmc_fx_apply(tmc_cfg_ctx_t *t, const tmc_drv_config_t *c)
{
    return (tmc_cfg_apply_ctx(t, c) == RT_EOK) ? RT_TRUE : RT_FALSE;
}

/* ---- 生产实例可见状态取一份副本, 用于证明自测没有改动它 ---- */
typedef struct
{
    tmc_drv_cfg_state_t state;
    rt_uint8_t  fail_reg;
    rt_err_t    last_err;
    rt_bool_t   fail_from_integrity;
    rt_bool_t   done_valid;
    rt_uint32_t done_gconf, done_chop;
    tmc_drv_config_t done;
    const void *bus;
    const void *gate;
    void       *priv;
} tmc_prod_probe_t;

static void tmc_prod_probe(tmc_prod_probe_t *p)
{
    tmc_cfg_lock(&tmc_cfg_prod);
    p->state  = tmc_cfg_prod.state;
    p->fail_reg = tmc_cfg_prod.fail_reg;
    p->last_err = tmc_cfg_prod.last_err;
    p->fail_from_integrity = tmc_cfg_prod.fail_from_integrity;
    p->done_valid = tmc_cfg_prod.done_valid;
    p->done_gconf = tmc_cfg_prod.done_gconf;
    p->done_chop  = tmc_cfg_prod.done_chop;
    p->done       = tmc_cfg_prod.done;
    p->bus        = (const void *)tmc_cfg_prod.bus;
    p->gate       = (const void *)tmc_cfg_prod.gate;
    p->priv       = tmc_cfg_prod.priv;
    tmc_cfg_unlock(&tmc_cfg_prod);
}

static int tmc_prod_probe_same(const tmc_prod_probe_t *a, const tmc_prod_probe_t *b)
{
    return (a->state  == b->state  && a->fail_reg == b->fail_reg &&
            a->last_err == b->last_err &&
            a->fail_from_integrity == b->fail_from_integrity &&
            a->done_valid == b->done_valid &&
            a->done_gconf == b->done_gconf && a->done_chop == b->done_chop &&
            a->done.irun == b->done.irun && a->done.ihold == b->done.ihold &&
            a->done.ihold_delay == b->done.ihold_delay &&
            a->done.tpowerdown == b->done.tpowerdown &&
            a->done.vsense == b->done.vsense &&
            a->done.mstep_reg_select == b->done.mstep_reg_select &&
            a->done.mres == b->done.mres &&
            a->bus == b->bus && a->gate == b->gate && a->priv == b->priv) ? 1 : 0;
}

/* ---- 跨线程占用 txn: 只有真的第二个线程持锁, 才能测出 -RT_EBUSY fail closed
 * (同线程重入是 RT-Thread 互斥量的正常语义, motor_arm 段内复核正依赖它) ----
 * F2c R06: 辅助线程的 IPC 生命周期必须与它的退出确认绑定。旧写法最多等 200ms
 * 就无条件删 semaphore, 也没检查 rt_thread_startup 的返回值 —— 那种类竞态一旦
 * 发生, 测出来的东西混进了测试基础设施自身的不稳定, 而不是业务行为。 */
static rt_sem_t tmc_fx_s_held, tmc_fx_s_free;
static volatile rt_bool_t tmc_fx_hold_have, tmc_fx_hold_done;

static void tmc_fx_hold_entry(void *param)
{
    tmc_cfg_ctx_t *t = (tmc_cfg_ctx_t *)param;

    tmc_fx_hold_have = tmc_cfg_txn_take(t);
    rt_sem_release(tmc_fx_s_held);                  /* "锁在我手上"(或拿锁失败) */
    rt_sem_take(tmc_fx_s_free, RT_WAITING_FOREVER);
    if (tmc_fx_hold_have) tmc_cfg_txn_give(t);
    tmc_fx_hold_done = RT_TRUE;                     /* 必须是最后一件事 */
}

/* 返回 1 = worker 已确认退出、IPC 已销毁; 0 = 未确认退出(**保留** IPC, 宁可泄漏
 * 两个对象也不能删除 worker 仍在使用的 semaphore), 调用方必须据此判 FAIL。 */
static int tmc_fx_hold_release_wait(void)
{
    int i;

    if (tmc_fx_s_free == RT_NULL) return 1;         /* 没建过, 无需清理 */
    rt_sem_release(tmc_fx_s_free);
    for (i = 0; i < 400 && !tmc_fx_hold_done; ++i)
        rt_thread_mdelay(5);                        /* 最多 2s 等它退出 */
    if (!tmc_fx_hold_done) return 0;                /* 未确认 -> 不碰 IPC */
    if (tmc_fx_s_held != RT_NULL) rt_sem_delete(tmc_fx_s_held);
    if (tmc_fx_s_free != RT_NULL) rt_sem_delete(tmc_fx_s_free);
    tmc_fx_s_held = RT_NULL;
    tmc_fx_s_free = RT_NULL;
    return 1;
}

/* 返回 1 = 另一线程确实已持有 txn(可以断言 busy);
 *     0 = 线程/信号量建不起来或没拿到锁(SKIP, 不据此判业务 FAIL);
 * *infra_ok 出参: 0 = 清理未确认(IPC 被保留), 业务 FAIL 必须置起。 */
static int tmc_fx_busy_setup(tmc_cfg_ctx_t *t, int *infra_ok)
{
    rt_thread_t th;
    int i;

    *infra_ok = 1;
    tmc_fx_hold_have = RT_FALSE;
    tmc_fx_hold_done = RT_FALSE;
    tmc_fx_s_held = rt_sem_create("fxcHeld", 0, RT_IPC_FLAG_FIFO);
    tmc_fx_s_free = rt_sem_create("fxcFree", 0, RT_IPC_FLAG_FIFO);
    th = rt_thread_create("fxcHold", tmc_fx_hold_entry, t, 768, 22, 10);
    if (tmc_fx_s_held == RT_NULL || tmc_fx_s_free == RT_NULL || th == RT_NULL)
    {
        if (th != RT_NULL) rt_thread_delete(th);
        if (tmc_fx_s_held != RT_NULL) rt_sem_delete(tmc_fx_s_held);
        if (tmc_fx_s_free != RT_NULL) rt_sem_delete(tmc_fx_s_free);
        tmc_fx_s_held = RT_NULL;
        tmc_fx_s_free = RT_NULL;
        return 0;
    }
    if (rt_thread_startup(th) != RT_EOK)
    {
        /* 线程从未运行 -> 它没碰过任何 IPC, 直接销毁对象是安全的 */
        rt_thread_delete(th);
        rt_sem_delete(tmc_fx_s_held);
        rt_sem_delete(tmc_fx_s_free);
        tmc_fx_s_held = RT_NULL;
        tmc_fx_s_free = RT_NULL;
        return 0;
    }
    for (i = 0; i < 400 && !tmc_fx_hold_have && !tmc_fx_hold_done; ++i)
        rt_thread_mdelay(5);                        /* 最多 2s 等它拿锁 */
    if (!tmc_fx_hold_have)
    {
        if (!tmc_fx_hold_release_wait()) *infra_ok = 0;
        return 0;
    }
    return 1;
}

static void tmc_cfg_selftest(void)
{
    static const tmc_fx_ev_t exp_seq[8] = {
        { TMC_REG_GCONF,      0u, 0u }, { TMC_REG_GCONF,      0u, 1u },
        { TMC_REG_GCONF,      0u, 0u }, { TMC_REG_CHOPCONF,   0u, 0u },
        { TMC_REG_CHOPCONF,   0u, 1u }, { TMC_REG_CHOPCONF,   0u, 0u },
        { TMC_REG_IHOLD_IRUN, 0u, 1u }, { TMC_REG_TPOWERDOWN, 0u, 1u }
    };
    static const rt_uint32_t gate_bits[7] = {
        TMC_WGATE_NOT_SYNCED,       TMC_WGATE_HW_NOT_VALIDATED,
        TMC_WGATE_MOTOR_NOT_QUIET,  TMC_WGATE_DRV_PAD_NOT_LOW,
        TMC_WGATE_NOT_READY,        TMC_WGATE_FAULT_LATCHED,
        TMC_WGATE_LINK_UNHEALTHY
    };
    tmc_prod_probe_t probe_before, probe_after;
    tmc_drv_config_t c2 = { 10, 4, 4, 8, 1, 1, 0 };
    tmc_cfg_ctx_t t;
    tmc_fx_t fx;
    rt_uint32_t v = 0;
    rt_err_t e;
    int pass = 1, i, busy, infra_ok = 1;

    tmc_prod_probe(&probe_before);
    rt_kprintf("[TMC-CFG-ST] 假总线离线自测(局部实例), 不使用 uart2\n");

    tmc_fx_reset(&fx);
    /* F2c R02: 局部实例的两把锁建不起来就没有任何串行化前提 -> 明确 SKIP 并直接
     * 返回, **不得打印 PASS/ALL PASS**(旧写法即便锁失败也会继续跑完并报总结果)。 */
    if (tmc_cfg_ctx_init(&t, &tmc_cfg_bus_fake, tmc_fx_gate, &fx,
                         "tfxCfgL", "tfxCfgT") != RT_EOK)
    {
        rt_kprintf("[TMC-CFG-ST] INCOMPLETE/SKIP: 局部实例互斥量创建失败,"
                   " 不执行任何用例, 不判定 PASS\n");
        return;
    }

    /* --- T1: 正常全流程 -> CONFIGURED + 四个目标值 + 完整有序事件序列 --- */
    if (!tmc_fx_apply(&t, &tmc_cfg_planned))
    { rt_kprintf("[TMC-CFG-ST] T1 apply 未成功\n"); pass = 0; }
    if (t.state != TMC_DRV_CONFIGURED)
    { rt_kprintf("[TMC-CFG-ST] T1 state=%s 应为 CONFIGURED\n",
                 tmc_drv_cfg_state_name(t.state)); pass = 0; }
    if (!tmc_fx_find_write(&fx, TMC_REG_GCONF, &v) || v != TMC_CFG_TGT_GCONF)
    { rt_kprintf("[TMC-CFG-ST] T1 GCONF 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_GCONF); pass = 0; }
    if (!tmc_fx_find_write(&fx, TMC_REG_CHOPCONF, &v) || v != TMC_CFG_TGT_CHOPCONF)
    { rt_kprintf("[TMC-CFG-ST] T1 CHOPCONF 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_CHOPCONF); pass = 0; }
    if (!tmc_fx_find_write(&fx, TMC_REG_IHOLD_IRUN, &v) || v != TMC_CFG_TGT_IHOLD_IRUN)
    { rt_kprintf("[TMC-CFG-ST] T1 IHOLD_IRUN 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_IHOLD_IRUN); pass = 0; }
    if (!tmc_fx_find_write(&fx, TMC_REG_TPOWERDOWN, &v) || v != TMC_CFG_TGT_TPOWERDOWN)
    { rt_kprintf("[TMC-CFG-ST] T1 TPOWERDOWN 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_TPOWERDOWN); pass = 0; }
    if (!tmc_fx_seq_ok(&fx, exp_seq, 8))
    { rt_kprintf("[TMC-CFG-ST] T1 事件序列不符(应 r00 w00 r00 r6c w6c r6c w10 w11)\n");
      tmc_fx_seq_dump(&fx); pass = 0; }
    if (fx.writes != 4 || fx.ifcnt != 4 || fx.opens != 1)
    { rt_kprintf("[TMC-CFG-ST] T1 IFCNT/open 计数错 writes=%d accepted=%d opens=%d\n",
                 fx.writes, fx.ifcnt, fx.opens); pass = 0; }
    if (!tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T1 CONFIGURED 却不容许 arm\n"); pass = 0; }

    /* --- T2: CHOPCONF 位域: vsense=bit17, 除 bit17 外不得有任何一位变化 --- */
    {
        rt_uint32_t base = 0x15010053u, nxt = tmc_chopconf_next(base, &tmc_cfg_planned);

        rt_kprintf("[TMC-CFG-ST] T2 CHOPCONF %08X -> %08X\n",
                   (unsigned)base, (unsigned)nxt);
        if (!(nxt & (1u << 17))) { rt_kprintf("[TMC-CFG-ST] T2 bit17 未置起\n"); pass = 0; }
        if (nxt & (1u << 18))    { rt_kprintf("[TMC-CFG-ST] T2 bit18 被意外修改\n"); pass = 0; }
        if ((nxt & 0xFu) != (base & 0xFu) || (nxt & 0xFu) == 0u)
        { rt_kprintf("[TMC-CFG-ST] T2 TOFF 未保持非零\n"); pass = 0; }
        if ((nxt & 0x7F0u) != (base & 0x7F0u))
        { rt_kprintf("[TMC-CFG-ST] T2 HSTRT/HEND 低位被改\n"); pass = 0; }
        if ((nxt & 0x00018000u) != (base & 0x00018000u))
        { rt_kprintf("[TMC-CFG-ST] T2 TBL 被改\n"); pass = 0; }
        if (((nxt ^ base) & ~TMC_CHOPCONF_VSENSE) != 0u)
        { rt_kprintf("[TMC-CFG-ST] T2 越界改动非 vsense 位 (xor=%08X)\n",
                     (unsigned)(nxt ^ base)); pass = 0; }
    }

    /* --- T3: GCONF 只允许 i_scale_analog/pdn_disable/mstep_reg_select 三位变化 --- */
    {
        rt_uint32_t base = 0x00000101u, nxt = tmc_gconf_next(base, &tmc_cfg_planned);

        rt_kprintf("[TMC-CFG-ST] T3 GCONF %08X -> %08X\n",
                   (unsigned)base, (unsigned)nxt);
        if (nxt != 0x00000141u) { rt_kprintf("[TMC-CFG-ST] T3 结果非 0x141\n"); pass = 0; }
        if (!(nxt & (1u << 6))) { rt_kprintf("[TMC-CFG-ST] T3 pdn_disable 未置起\n"); pass = 0; }
        if (!(nxt & 1u))        { rt_kprintf("[TMC-CFG-ST] T3 i_scale_analog 被清\n"); pass = 0; }
        if (!(nxt & (1u << 8))) { rt_kprintf("[TMC-CFG-ST] T3 multistep_filt 被清\n"); pass = 0; }
        if (nxt & (1u << 1))    { rt_kprintf("[TMC-CFG-ST] T3 意外置起 internal_Rsense\n"); pass = 0; }
        if (((nxt ^ base) & ~(1u | (1u << 6) | (1u << 7))) != 0u)
        { rt_kprintf("[TMC-CFG-ST] T3 越界改动其它 GCONF 位 (xor=%08X)\n",
                     (unsigned)(nxt ^ base)); pass = 0; }
    }

    /* --- T4: 只写寄存器不得被读; 读回必须紧跟在对应写之后(位置由序列保证) --- */
    if (tmc_fx_any_read_of(&fx, TMC_REG_IHOLD_IRUN) ||
        tmc_fx_any_read_of(&fx, TMC_REG_TPOWERDOWN))
    { rt_kprintf("[TMC-CFG-ST] T4 对只写寄存器发起了读\n"); pass = 0; }
    if (fx.log[2].is_write != 0u || fx.log[2].reg != TMC_REG_GCONF ||
        fx.log[5].is_write != 0u || fx.log[5].reg != TMC_REG_CHOPCONF)
    { rt_kprintf("[TMC-CFG-ST] T4 读回不在写之后的位置\n"); pass = 0; }

    /* --- T5: 配置一致 + 链路健康 -> integrity 通过(健康由实例注入, 不读全局) --- */
    if (tmc_cfg_integrity_ctx(&t) != RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T5 integrity 误判失败\n"); pass = 0; }

    /* --- T6: 芯片断电/复位(寄存器回默认) -> 照出并降级 + 作废快照 --- */
    fx.gconf = 0x00000101u;
    fx.chop  = 0x15010053u;
    if (tmc_cfg_integrity_ctx(&t) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T6 芯片复位后 integrity 仍说 OK\n"); pass = 0; }
    if (t.state != TMC_DRV_FAILED)
    { rt_kprintf("[TMC-CFG-ST] T6 状态未变 FAILED(%s)\n",
                 tmc_drv_cfg_state_name(t.state)); pass = 0; }
    if (t.done_valid != RT_FALSE)
    { rt_kprintf("[TMC-CFG-ST] T6 复位后旧快照仍标为有效\n"); pass = 0; }
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T6 配置已丢却仍容许 arm\n"); pass = 0; }
    rt_kprintf("[TMC-CFG-ST] T6 失联/复位 -> FAILED + 快照作废, arm 拒绝 (fail-closed OK)\n");

    /* --- T6b: 链路不健康(读不到 != 配置还在) -> 同样降级 --- */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    if (!tmc_fx_apply(&t, &tmc_cfg_planned))
    { rt_kprintf("[TMC-CFG-ST] T6b 前置 apply 未成功\n"); pass = 0; }
    fx.link_ok = RT_FALSE;
    if (tmc_cfg_integrity_ctx(&t) != -RT_EIO)
    { rt_kprintf("[TMC-CFG-ST] T6b 链路不健康时 integrity 未判失败\n"); pass = 0; }
    if (t.state != TMC_DRV_FAILED || t.done_valid != RT_FALSE)
    { rt_kprintf("[TMC-CFG-ST] T6b 失联后状态/快照未失效(%s valid=%d)\n",
                 tmc_drv_cfg_state_name(t.state), (int)t.done_valid); pass = 0; }

    /* --- T7: 第 2 次写未被接受 -> 立即停止后续写, IFCNT 不涨, 内容不变 --- */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.fail_write_nth = 2;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T7 写失败却返回成功\n"); pass = 0; }
    if (t.state != TMC_DRV_FAILED)
    { rt_kprintf("[TMC-CFG-ST] T7 状态=%s 应为 FAILED\n",
                 tmc_drv_cfg_state_name(t.state)); pass = 0; }
    if (tmc_fx_write_count_of(&fx, TMC_REG_IHOLD_IRUN) ||
        tmc_fx_write_count_of(&fx, TMC_REG_TPOWERDOWN))
    { rt_kprintf("[TMC-CFG-ST] T7 失败后仍继续写后续寄存器\n"); pass = 0; }
    if (fx.writes != 2 || fx.ifcnt != 1 || fx.chop != 0x15010053u)
    { rt_kprintf("[TMC-CFG-ST] T7 IFCNT 模型错 writes=%d accepted=%d chop=%08X\n",
                 fx.writes, fx.ifcnt, (unsigned)fx.chop); pass = 0; }

    /* --- T8: 第 1 次写就失败 -> FAILED、不写后续、快照作废 --- */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.fail_write_nth = 1;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T8 首次写失败却返回成功\n"); pass = 0; }
    if (tmc_fx_write_count_of(&fx, TMC_REG_CHOPCONF) ||
        tmc_fx_write_count_of(&fx, TMC_REG_TPOWERDOWN))
    { rt_kprintf("[TMC-CFG-ST] T8 首写失败后仍继续\n"); pass = 0; }
    if (t.done_valid != RT_FALSE)
    { rt_kprintf("[TMC-CFG-ST] T8 失败后快照仍有效\n"); pass = 0; }

    /* --- T9: 非 CONFIGURED / 快照失效 四种组合 arm 判定必假、integrity 必拒 --- */
    tmc_fx_reset(&fx);
    t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T9 UNCONFIGURED 却容许 arm\n"); pass = 0; }
    t.state = TMC_DRV_FAILED;
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T9 FAILED 却容许 arm\n"); pass = 0; }
    t.state = TMC_DRV_CONFIGURING;
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T9 CONFIGURING 却容许 arm\n"); pass = 0; }
    if (tmc_cfg_integrity_ctx(&t) != -RT_EPERM)
    { rt_kprintf("[TMC-CFG-ST] T9 非 CONFIGURED 时 integrity 应拒绝(-RT_EPERM)\n"); pass = 0; }
    /* P1-C 的关键回归: state=CONFIGURED 但快照已废 -> 第 5 门必须闭合。
     * 旧版只有"state"一个条件, 这个组合会从门里假通过。 */
    t.state = TMC_DRV_CONFIGURED; t.done_valid = RT_FALSE;
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T9 CONFIGURED+快照失效 却容许 arm\n"); pass = 0; }
    if (tmc_cfg_integrity_ctx(&t) != -RT_EPERM)
    { rt_kprintf("[TMC-CFG-ST] T9 快照失效时 integrity 应拒绝\n"); pass = 0; }

    /* --- T10: 过流方向 1->0 与 TOFF=0 基线都必须中止且不写电流档 --- */
    tmc_fx_reset(&fx); fx.chop = 0x15030053u;
    t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    {
        tmc_drv_config_t down = tmc_cfg_planned;

        down.vsense = 0;
        if (tmc_cfg_apply_ctx(&t, &down) == RT_EOK)
        { rt_kprintf("[TMC-CFG-ST] T10 1->0 方向未被拒\n"); pass = 0; }
        if (tmc_fx_write_count_of(&fx, TMC_REG_IHOLD_IRUN))
        { rt_kprintf("[TMC-CFG-ST] T10 拒绝后仍写了电流档\n"); pass = 0; }
    }
    tmc_fx_reset(&fx); fx.chop = 0x15010050u;       /* TOFF=0, 桥全关 */
    t.state = TMC_DRV_UNCONFIGURED;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T10 TOFF=0 基线竟通过\n"); pass = 0; }
    if (tmc_fx_write_count_of(&fx, TMC_REG_CHOPCONF))
    { rt_kprintf("[TMC-CFG-ST] T10 TOFF 非法时仍写了 CHOPCONF\n"); pass = 0; }

    /* --- T11(F2c 修正 R01): 写被接受(IFCNT+1)但**写后读回**不符 -> FAILED ---
     * 注入必须命中该寄存器的第 2 次读(第 1 次是 apply 的基线预读)。旧的一次性
     * 注入挂在第 1 次读上, 于是"回读不符"这条路径从未真的被测到。 */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.corrupt_reg = TMC_REG_GCONF; fx.corrupt_nth = 2; fx.corrupt_xor = 0x1u;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T11 回读污染竟通过\n"); pass = 0; }
    if (t.state != TMC_DRV_FAILED || t.done_valid != RT_FALSE)
    { rt_kprintf("[TMC-CFG-ST] T11 状态/快照未失效(%s valid=%d)\n",
                 tmc_drv_cfg_state_name(t.state), (int)t.done_valid); pass = 0; }
    if (tmc_fx_write_count_of(&fx, TMC_REG_CHOPCONF) ||
        tmc_fx_write_count_of(&fx, TMC_REG_IHOLD_IRUN))
    { rt_kprintf("[TMC-CFG-ST] T11 回读不符后仍写了后续寄存器\n"); pass = 0; }
    if (fx.ifcnt != 1 || fx.rd_gconf != 2)
    { rt_kprintf("[TMC-CFG-ST] T11 注入未命中回读 (ifcnt=%d rd_gconf=%d 应 1/2)\n",
                 fx.ifcnt, fx.rd_gconf); pass = 0; }
    if (t.fail_reg != TMC_REG_GCONF)
    { rt_kprintf("[TMC-CFG-ST] T11 fail_reg=0x%02X 应为 GCONF\n", t.fail_reg); pass = 0; }

    /* --- T11b(F2c 新增): **基线预读**被污染(GCONF 第 1 次读 bit0 被清) ---
     * 这条路径读回是发现不了的(读回比的是"我们写下去的值")。可断言的真实保护是:
     * 安全相关的位(i_scale_analog / pdn_disable)由 tmc_gconf_next 自己强制,
     * 污染位不会泄漏进写入值 —— 写入值仍必须等于 0x141。 */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.corrupt_reg = TMC_REG_GCONF; fx.corrupt_nth = 1; fx.corrupt_xor = 0x1u;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) != RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T11b 基线 bit0 污染竟导致下发失败\n"); pass = 0; }
    if (!tmc_fx_find_write(&fx, TMC_REG_GCONF, &v) || v != TMC_CFG_TGT_GCONF)
    { rt_kprintf("[TMC-CFG-ST] T11b 写入值带上污染位: %08X (应 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_GCONF); pass = 0; }
    if (fx.rd_gconf != 2 || fx.ifcnt != 4)
    { rt_kprintf("[TMC-CFG-ST] T11b 读序/IFCNT 异常 rd=%d accepted=%d\n",
                 fx.rd_gconf, fx.ifcnt); pass = 0; }

    /* --- T11c(F2c 新增): 基线预读被污染成 TOFF=0 -> 必须拒绝写 CHOPCONF ---
     * TOFF=0 会关断所有桥。我们**无法**分辨"芯片真是 0"还是"读被污染", 两种都
     * 按不可信拒绝(fail closed), 且绝不下发电流档。 */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.corrupt_reg = TMC_REG_CHOPCONF; fx.corrupt_nth = 1; fx.corrupt_xor = 0x3u;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T11c 基线 TOFF 被污染成 0 竟通过\n"); pass = 0; }
    if (tmc_fx_write_count_of(&fx, TMC_REG_CHOPCONF) ||
        tmc_fx_write_count_of(&fx, TMC_REG_IHOLD_IRUN))
    { rt_kprintf("[TMC-CFG-ST] T11c CHOPCONF 非法基线仍写了\n"); pass = 0; }
    if (t.fail_reg != TMC_REG_CHOPCONF)
    { rt_kprintf("[TMC-CFG-ST] T11c fail_reg=0x%02X 应为 CHOPCONF\n", t.fail_reg); pass = 0; }

    /* --- T12(新): internal_Rsense=1 -> VREF 模型失效, 必须在任何写之前拒绝 --- */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.gconf |= TMC_GCONF_INTERNAL_RSENSE;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T12 internal_Rsense=1 竟通过\n"); pass = 0; }
    if (fx.writes != 0 || fx.opens != 1)
    { rt_kprintf("[TMC-CFG-ST] T12 判定前就产生了总线动作 writes=%d opens=%d\n",
                 fx.writes, fx.opens); pass = 0; }

    /* --- T12b(F2c 新增): 基线读被污染成 internal_Rsense=1 -> 同样拒绝且零写 ---
     * 分辨不了"真是 OTP 位"还是"读被污染", 两种都拒绝(宁可不下发)。 */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.corrupt_reg = TMC_REG_GCONF; fx.corrupt_nth = 1;
    fx.corrupt_xor = TMC_GCONF_INTERNAL_RSENSE;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T12b 基线污染成 internal_Rsense=1 竟通过\n"); pass = 0; }
    if (fx.writes != 0)
    { rt_kprintf("[TMC-CFG-ST] T12b 拒绝前仍写了 %d 次\n", fx.writes); pass = 0; }

    /* --- T13(F2c 扩到 7 位): 运行态门禁每一位都必须挡住(零 open、零写、状态与快照不变) --- */
    for (i = 0; i < 7; ++i)
    {
        tmc_fx_reset(&fx);
        t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
        fx.gate_mask = gate_bits[i];
        e = tmc_cfg_apply_ctx(&t, &tmc_cfg_planned);
        if (e != -RT_EPERM)
        { rt_kprintf("[TMC-CFG-ST] T13 bit%d 门禁返回 %d 应为 -RT_EPERM\n",
                     i, (int)e); pass = 0; }
        if (fx.opens != 0 || fx.writes != 0)
        { rt_kprintf("[TMC-CFG-ST] T13 bit%d 被挡但仍 opens=%d writes=%d\n",
                     i, fx.opens, fx.writes); pass = 0; }
        if (t.state != TMC_DRV_UNCONFIGURED || t.done_valid != RT_FALSE)
        { rt_kprintf("[TMC-CFG-ST] T13 bit%d 拒绝后状态/快照被改动\n", i); pass = 0; }
        fx.gate_mask = 0u;
    }
    /* 全通时必须真的写得下去(证明上面的拒绝不是"永远拒绝") */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    if (!tmc_fx_apply(&t, &tmc_cfg_planned))
    { rt_kprintf("[TMC-CFG-ST] T13 门禁全通却未能下发\n"); pass = 0; }

    /* --- T14(新): 事务被另一线程占用 -> apply/integrity 都 -RT_EBUSY 且零总线动作 ---
     * F2c R06: 辅助线程/信号量建不起来只是 SKIP(不判业务失败), 但**清理未确认**
     * (worker 没确认退出, IPC 被有意保留)必须判 FAIL —— 不能把测试基础设施自身
     * 的竞态当成业务行为, 也不能在 worker 还在用的时候删掉 semaphore。 */
    busy = tmc_fx_busy_setup(&t, &infra_ok);
    if (busy)
    {
        tmc_fx_reset(&fx);
        if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) != -RT_EBUSY)
        { rt_kprintf("[TMC-CFG-ST] T14 事务占用时 apply 未返回 -RT_EBUSY\n"); pass = 0; }
        if (tmc_cfg_integrity_ctx(&t) != -RT_EBUSY)
        { rt_kprintf("[TMC-CFG-ST] T14 事务占用时 integrity 未返回 -RT_EBUSY\n"); pass = 0; }
        if (fx.opens != 0 || fx.writes != 0)
        { rt_kprintf("[TMC-CFG-ST] T14 busy 期间仍有总线动作 opens=%d writes=%d\n",
                     fx.opens, fx.writes); pass = 0; }
        if (!tmc_fx_hold_release_wait())
        { rt_kprintf("[TMC-CFG-ST] T14 FAIL: worker 未确认退出, IPC 已保留(不删)\n");
          pass = 0; }
        tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
        if (!tmc_fx_apply(&t, &tmc_cfg_planned))
        { rt_kprintf("[TMC-CFG-ST] T14 锁释放后 apply 仍失败\n"); pass = 0; }
        rt_kprintf("[TMC-CFG-ST] T14 跨线程占用 -> -RT_EBUSY fail closed OK\n");
    }
    else rt_kprintf("[TMC-CFG-ST] T14 SKIP: 线程/信号量不可用, 未测跨线程占用\n");
    if (!infra_ok) pass = 0;

    /* 同线程重入(motor_arm 段内复核依赖): 持有 txn 时 integrity 不得报 busy */
    if (tmc_cfg_txn_take(&t))
    {
        if (tmc_cfg_integrity_ctx(&t) == -RT_EBUSY)
        { rt_kprintf("[TMC-CFG-ST] T14b 同线程重入被误判 busy\n"); pass = 0; }
        tmc_cfg_txn_give(&t);
    }

    /* --- T15(新): open 失败 -> FAILED、零 write、快照作废 --- */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    fx.open_fail = RT_TRUE;
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T15 open 失败却返回成功\n"); pass = 0; }
    if (fx.writes != 0 || fx.opens != 1 || t.done_valid != RT_FALSE)
    { rt_kprintf("[TMC-CFG-ST] T15 open 失败路径异常 writes=%d opens=%d valid=%d\n",
                 fx.writes, fx.opens, (int)t.done_valid); pass = 0; }

    /* --- T16(新): CONFIGURING 窗口内旧快照不可用, 且二次 apply 被拒 --- */
    tmc_fx_reset(&fx);
    tmc_cfg_lock(&t);
    t.state = TMC_DRV_CONFIGURING; t.done_valid = RT_FALSE;
    tmc_cfg_unlock(&t);
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T16 CONFIGURING 窗口容许 arm\n"); pass = 0; }
    if (tmc_cfg_apply_ctx(&t, &tmc_cfg_planned) != -RT_EBUSY)
    { rt_kprintf("[TMC-CFG-ST] T16 CONFIGURING 时二次 apply 未被拒\n"); pass = 0; }
    t.state = TMC_DRV_UNCONFIGURED;

    /* --- T17(新): 二次 apply 成功必须替换快照, 且只在成功末尾恢复 valid --- */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    tmc_cfg_lock(&t); t.done_gconf = 0xDEADBEEFu; t.done_chop = 0xCAFEBABEu; tmc_cfg_unlock(&t);
    if (!tmc_fx_apply(&t, &c2))
    { rt_kprintf("[TMC-CFG-ST] T17 二次 apply 未成功\n"); pass = 0; }
    if (t.done_gconf == 0xDEADBEEFu || t.done_chop == 0xCAFEBABEu)
    { rt_kprintf("[TMC-CFG-ST] T17 快照未被新值替换(g=%08X c=%08X)\n",
                 (unsigned)t.done_gconf, (unsigned)t.done_chop); pass = 0; }
    if (t.done_valid != RT_TRUE || t.state != TMC_DRV_CONFIGURED ||
        t.done.irun != c2.irun || t.done.vsense != c2.vsense ||
        t.done.mstep_reg_select != c2.mstep_reg_select)
    { rt_kprintf("[TMC-CFG-ST] T17 快照内容/状态不符\n"); pass = 0; }
    if (tmc_cfg_integrity_ctx(&t) != RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T17 新快照 integrity 未通过\n"); pass = 0; }

    /* --- T20(F2c R03): arm 后芯片断电/复位 -> 复核必须持续拒绝, 重新下发才恢复 ---
     * motor_start 起转前新加的正是这步复核(F2c R03)。这里在实例上把整条序列跑完:
     * 下发成功 -> 复核通过 -> 模拟模块断电重上电(寄存器回默认) -> 复核失败且第 5
     * 门闭合 -> 第二次复核仍失败(不能"第二次就好了") -> 重新下发才能恢复。 */
    tmc_fx_reset(&fx); t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    if (!tmc_fx_apply(&t, &tmc_cfg_planned))
    { rt_kprintf("[TMC-CFG-ST] T20 前置下发未成功\n"); pass = 0; }
    if (tmc_cfg_integrity_ctx(&t) != RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T20 复位前复核竟失败\n"); pass = 0; }
    fx.gconf = 0x00000101u; fx.chop = 0x15010053u;        /* 模块断电后重上电 */
    if (tmc_cfg_integrity_ctx(&t) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T20 芯片复位未被照出\n"); pass = 0; }
    if (tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T20 复位后第 5 门竟仍放行\n"); pass = 0; }
    if (tmc_cfg_integrity_ctx(&t) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T20 第二次复核竟通过(降级不持久)\n"); pass = 0; }
    t.state = TMC_DRV_UNCONFIGURED; t.done_valid = RT_FALSE;
    if (!tmc_fx_apply(&t, &tmc_cfg_planned))
    { rt_kprintf("[TMC-CFG-ST] T20 重新下发未能恢复\n"); pass = 0; }
    if (!tmc_cfg_ok_for_arm_ctx(&t))
    { rt_kprintf("[TMC-CFG-ST] T20 重新下发后仍不容许 arm\n"); pass = 0; }
    rt_kprintf("[TMC-CFG-ST] T20 复位->持续拒绝->重新下发恢复 OK\n");

    /* --- T21(F2c R02): 执行器锁不可用 -> 写/复核/arm 判定全部拒绝 ---
     * 这是"rt_mutex_init 失败"或"内核未就绪"的形状。旧实现在这种情况下反而当
     * 成"没有并发, 放行", 正好违反 fail-closed。 */
    {
        tmc_cfg_ctx_t u;
        tmc_fx_t ux;

        tmc_fx_reset(&ux);
        if (tmc_cfg_ctx_init(&u, &tmc_cfg_bus_fake, tmc_fx_gate, &ux,
                             "tfxCgL2", "tfxCgT2") == RT_EOK)
        {
            tmc_cfg_ctx_detach(&u);           /* 只留下"锁不可用"这个状态 */
            u.state = TMC_DRV_CONFIGURED;     /* 假装以前成功下发过 */
            u.done_valid = RT_TRUE;
            if (tmc_cfg_apply_ctx(&u, &tmc_cfg_planned) != -RT_ENOSYS)
            { rt_kprintf("[TMC-CFG-ST] T21 无锁实例竟允许 apply\n"); pass = 0; }
            if (tmc_cfg_integrity_ctx(&u) != -RT_ENOSYS)
            { rt_kprintf("[TMC-CFG-ST] T21 无锁实例竟允许复核\n"); pass = 0; }
            if (tmc_cfg_ok_for_arm_ctx(&u))
            { rt_kprintf("[TMC-CFG-ST] T21 无锁实例竟容许 arm\n"); pass = 0; }
            if (ux.opens != 0 || ux.writes != 0)
            { rt_kprintf("[TMC-CFG-ST] T21 无锁实例仍产生了总线动作(%d/%d)\n",
                         ux.opens, ux.writes); pass = 0; }
            rt_kprintf("[TMC-CFG-ST] T21 锁不可用 -> ENOSYS 拒绝, 第 5 门闭合 OK\n");
        }
        else rt_kprintf("[TMC-CFG-ST] T21 SKIP: 第二个局部实例建不起来\n");
    }

    /* --- T18(新, P1-C 证明): 整轮自测必须没碰过生产实例 --- */
    tmc_prod_probe(&probe_after);
    if (!tmc_prod_probe_same(&probe_before, &probe_after))
    {
        rt_kprintf("[TMC-CFG-ST] T18 FAIL: 生产 ctx 被自测改动 (state %s->%s,"
                   " snapshot_valid %d->%d, gconf %08X->%08X)\n",
                   tmc_drv_cfg_state_name(probe_before.state),
                   tmc_drv_cfg_state_name(probe_after.state),
                   (int)probe_before.done_valid, (int)probe_after.done_valid,
                   (unsigned)probe_before.done_gconf, (unsigned)probe_after.done_gconf);
        pass = 0;
    }
    else rt_kprintf("[TMC-CFG-ST] T18 生产 ctx 未被改动(state=%s, snapshot_valid=%d)\n",
                    tmc_drv_cfg_state_name(probe_after.state),
                    (int)probe_after.done_valid);

    /* --- T19(新): 生产实例的 arm 判定必须与快照有效性自洽 --- */
    if (tmc2209_drive_config_ok_for_arm() &&
        !tmc2209_get_applied_config(RT_NULL, RT_NULL, RT_NULL))
    { rt_kprintf("[TMC-CFG-ST] T19 arm 放行却没有有效快照(判定不自洽)\n"); pass = 0; }
    if (tmc2209_get_write_gate_mask() == 0u)
        rt_kprintf("[TMC-CFG-ST] T19 注意: 运行态门禁全通(编译期门仍挡住任何写入)\n");

    tmc_cfg_ctx_detach(&t);

    rt_kprintf("[TMC-CFG-ST] %s (T1..T21, 假总线局部实例, 未访问 uart2;"
               " T14 允许 SKIP, 其余必须成立)\n", pass ? "ALL PASS" : "FAILED");
}
MSH_CMD_EXPORT(tmc_cfg_selftest, offline fake-bus drive config state machine selftest);
