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

    tmc_health = (e == RT_EOK) ? SUBSYS_OK : SUBSYS_FAILED;
    return e;
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

/* ================= Phase 8-F2/F2a: 驱动参数配置(软件准备, 默认不启用) =========
 *
 * 编译期硬门禁: 当前扩展板 U5 与门异常 / 急停非门异常 / TMC_ENN 实测约 0V /
 * H2 限位未接, 安全链未验收 → 必须保持 RT_FALSE; 翻转需单独提交 + 硬件验证记录。
 * 门禁为 FALSE 时 apply 不向总线发出任何写, 也不改变状态。
 * apply 只写配置寄存器: 不碰 ENN / MCU_DRV_ENABLE, 不写 VACTUAL, 不产生 STEP。 */
#ifndef TMC_DRIVE_CONFIG_HW_VALIDATED
#define TMC_DRIVE_CONFIG_HW_VALIDATED   RT_FALSE
#endif

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

/* ---- 配置生命周期状态 ---- */
static tmc_drv_cfg_state_t tmc_cfg_state = TMC_DRV_UNCONFIGURED;
static rt_uint8_t tmc_cfg_fail_reg  = 0;         /* 失败发生在哪个寄存器(0=无) */
static rt_err_t   tmc_cfg_last_err  = RT_EOK;
static rt_bool_t  tmc_cfg_fail_from_integrity = RT_FALSE;

/* 成功下发后的快照: 断电/复位后芯片寄存器回默认值, 而 MCU 侧不会自动知道,
 * 只能靠"已下发值 vs 现值"比对照出来。 */
static rt_uint32_t      tmc_cfg_done_gconf = 0;
static rt_uint32_t      tmc_cfg_done_chop = 0;
static tmc_drv_config_t tmc_cfg_done;
static rt_bool_t        tmc_cfg_done_valid = RT_FALSE;

/* ---- 总线接缝: 配置序列只经它访问硬件, 便于用假总线做离线测试 ------------ */
typedef struct
{
    rt_err_t (*open) (void);
    rt_err_t (*read) (rt_uint8_t reg, rt_uint32_t *v);
    rt_err_t (*write)(rt_uint8_t reg, rt_uint32_t v);   /* 内含 IFCNT 到达确认 */
} tmc_cfg_bus_t;

static rt_err_t tmc_cfg_hw_open(void)  { return tmc2209_init(); }
static rt_err_t tmc_cfg_hw_read(rt_uint8_t reg, rt_uint32_t *v)
{
    return tmc2209_read_register(TMC_ADDR_DEFAULT, reg, v);
}
static rt_err_t tmc_cfg_hw_write(rt_uint8_t reg, rt_uint32_t v)
{
    return tmc2209_write_register_confirmed(TMC_ADDR_DEFAULT, reg, v);
}
static const tmc_cfg_bus_t tmc_cfg_bus_real =
{
    tmc_cfg_hw_open, tmc_cfg_hw_read, tmc_cfg_hw_write
};
static const tmc_cfg_bus_t *tmc_cfg_bus = &tmc_cfg_bus_real;

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
tmc_drv_cfg_state_t tmc2209_get_config_state(void) { return tmc_cfg_state; }

void tmc2209_get_planned_config(tmc_drv_config_t *out)
{
    if (out != RT_NULL) *out = tmc_cfg_planned;
}

rt_bool_t tmc2209_get_applied_config(rt_uint32_t *gconf, rt_uint32_t *chop,
                                      tmc_drv_config_t *out)
{
    if (!tmc_cfg_done_valid) return RT_FALSE;
    if (gconf) *gconf = tmc_cfg_done_gconf;
    if (chop)  *chop  = tmc_cfg_done_chop;
    if (out)   *out   = tmc_cfg_done;
    return RT_TRUE;
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

/* ---- 失败与写入 ---- */
static rt_err_t tmc_cfg_fail(rt_uint8_t reg, rt_err_t e, const char *stage)
{
    tmc_cfg_fail_reg = reg;
    tmc_cfg_last_err = e;
    tmc_cfg_state = TMC_DRV_FAILED;
    rt_kprintf("[TMC-CFG] FAILED at %s (reg=0x%02X err=%d) - 序列已中止, 芯片可能"
               "处于部分配置状态; 禁止使能, 需重新 apply 或断电复位\n",
               stage, reg, (int)e);
    return (e == RT_EOK) ? -RT_ERROR : e;
}

/* 写 + IFCNT 到达确认 +(可读寄存器)读回比对。只写寄存器不发非法读。 */
static rt_err_t tmc_cfg_write_one(rt_uint8_t reg, rt_uint32_t val, rt_bool_t readable)
{
    rt_err_t    e = tmc_cfg_bus->write(reg, val);
    rt_uint32_t rb = 0;

    if (e != RT_EOK) return e;                    /* IFCNT 未 +1 = 芯片没接受 */
    if (!readable)   return RT_EOK;               /* 只写: 不读回 */

    e = tmc_cfg_bus->read(reg, &rb);
    if (e != RT_EOK) return e;
    if (rb != val)
    {
        rt_kprintf("[TMC-CFG] readback MISMATCH reg=0x%02X wrote=%08X read=%08X\n",
                   reg, (unsigned)val, (unsigned)rb);
        return -RT_EIO;
    }
    return RT_EOK;
}

/* 配置序列本体。allow_hw 由调用方决定: 生产路径传编译期门禁, 离线自测传 TRUE
 * 但同时必须把 tmc_cfg_bus 换成假总线(见 tmc_cfg_selftest), 因此自测不会碰 UART。 */
static rt_err_t tmc_cfg_run(const tmc_drv_config_t *c, const tmc_cfg_bus_t *bus,
                            rt_bool_t allow_hw)
{
    rt_uint32_t gconf = 0, chop = 0, new_g, new_c;
    rt_uint8_t  old_vs;
    rt_err_t    e;

    if (!allow_hw)
    {
        rt_kprintf("[TMC-CFG] REFUSED: 硬件安全链未验收"
                   " (TMC_DRIVE_CONFIG_HW_VALIDATED=FALSE) - 未发出任何总线写,"
                   " 状态保持 %s\n", tmc_drv_cfg_state_name(tmc_cfg_state));
        return -RT_EPERM;
    }
    if (tmc_cfg_state == TMC_DRV_CONFIGURING) return -RT_EBUSY;

    tmc_cfg_bus = bus;
    if (tmc_cfg_bus->open() != RT_EOK)
        return tmc_cfg_fail(0, -RT_ERROR, "uart-open");

    tmc_cfg_state = TMC_DRV_CONFIGURING;
    rt_kprintf("[TMC-CFG] apply start: irun=%u ihold=%u dly=%u tpd=%u vsense=%u\n",
               c->irun, c->ihold, c->ihold_delay, c->tpowerdown, c->vsense);

    /* ---- 1) GCONF: 只置 i_scale_analog + pdn_disable, 其余保留(含 OTP 派生位) */
    e = tmc_cfg_bus->read(TMC_REG_GCONF, &gconf);
    if (e != RT_EOK) return tmc_cfg_fail(TMC_REG_GCONF, e, "GCONF-read");

    /* internal_Rsense=1 时 VREF 引脚被内部拉到 GND, 外部 0.11R 模型与 VREF
     * 分档一起失效 -> 不是"写不进去"而是"算了也不对", 必须 fail closed。 */
    if (gconf & TMC_GCONF_INTERNAL_RSENSE)
        return tmc_cfg_fail(TMC_REG_GCONF, -RT_EPERM, "GCONF.internal_Rsense=1");

    new_g = tmc_gconf_next(gconf, c);
    rt_kprintf("[TMC-CFG] GCONF %08X -> %08X\n",
               (unsigned)gconf, (unsigned)new_g);
    e = tmc_cfg_write_one(TMC_REG_GCONF, new_g, RT_TRUE);
    if (e != RT_EOK) return tmc_cfg_fail(TMC_REG_GCONF, e, "GCONF-write");

    /* ---- 2) CHOPCONF: 只动 vsense(及可选 MRES), TOFF/HSTRT/HEND/TBL 全保留 */
    e = tmc_cfg_bus->read(TMC_REG_CHOPCONF, &chop);
    if (e != RT_EOK) return tmc_cfg_fail(TMC_REG_CHOPCONF, e, "CHOPCONF-read");

    old_vs = (chop & TMC_CHOPCONF_VSENSE) ? 1u : 0u;
    if (!tmc_change_is_current_safe(old_vs, c->vsense))
        return tmc_cfg_fail(TMC_REG_CHOPCONF, -RT_EPERM, "CHOPCONF.overcurrent-dir");

    /* 不变式: TOFF 必须原样保留且非 0(TOFF=0 会关断所有桥) */
    new_c = tmc_chopconf_next(chop, c);
    if ((new_c & TMC_CHOPCONF_TOFF_MASK) != (chop & TMC_CHOPCONF_TOFF_MASK))
        return tmc_cfg_fail(TMC_REG_CHOPCONF, -RT_EINVAL, "CHOPCONF.TOFF-changed");
    if ((new_c & TMC_CHOPCONF_TOFF_MASK) == 0u)
        return tmc_cfg_fail(TMC_REG_CHOPCONF, -RT_EINVAL, "CHOPCONF.TOFF-zero");

    rt_kprintf("[TMC-CFG] CHOPCONF %08X -> %08X (vsense %u -> %u)\n",
               (unsigned)chop, (unsigned)new_c, old_vs, c->vsense);
    e = tmc_cfg_write_one(TMC_REG_CHOPCONF, new_c, RT_TRUE);
    if (e != RT_EOK) return tmc_cfg_fail(TMC_REG_CHOPCONF, e, "CHOPCONF-write");

    /* ---- 3) IHOLD_IRUN: 只写寄存器, 到达凭证只有 IFCNT+1 ---- */
    rt_kprintf("[TMC-CFG] IHOLD_IRUN <- %08X (只写, 不读回)\n",
               (unsigned)tmc_ihold_irun_pack(c));
    e = tmc_cfg_write_one(TMC_REG_IHOLD_IRUN, tmc_ihold_irun_pack(c), RT_FALSE);
    if (e != RT_EOK) return tmc_cfg_fail(TMC_REG_IHOLD_IRUN, e, "IHOLD_IRUN-write");

    /* ---- 4) TPOWERDOWN: 同样按只写处理(不做非法读回) ---- */
    rt_kprintf("[TMC-CFG] TPOWERDOWN(0x%02X) <- %u\n",
               TMC_REG_TPOWERDOWN, c->tpowerdown);
    e = tmc_cfg_write_one(TMC_REG_TPOWERDOWN, c->tpowerdown, RT_FALSE);
    if (e != RT_EOK) return tmc_cfg_fail(TMC_REG_TPOWERDOWN, e, "TPOWERDOWN-write");

    /* 全部到位: 存快照供 integrity 复核 */
    tmc_cfg_done_gconf = new_g;
    tmc_cfg_done_chop  = new_c;
    tmc_cfg_done       = *c;
    tmc_cfg_done_valid = RT_TRUE;
    tmc_cfg_state      = TMC_DRV_CONFIGURED;
    tmc_cfg_fail_reg   = 0;
    tmc_cfg_last_err   = RT_EOK;
    tmc_cfg_fail_from_integrity = RT_FALSE;
    rt_kprintf("[TMC-CFG] CONFIGURED (各次写入均经 IFCNT 确认; GCONF/CHOPCONF"
               " 读回一致)。电流值仍为理论计算, 未实测。\n");
    return RT_EOK;
}

rt_err_t tmc2209_apply_config(void)
{
    return tmc_cfg_run(&tmc_cfg_planned, &tmc_cfg_bus_real,
                       TMC_DRIVE_CONFIG_HW_VALIDATED);
}

rt_bool_t tmc2209_drive_config_ok_for_arm(void)
{
    return (tmc_cfg_state == TMC_DRV_CONFIGURED) ? RT_TRUE : RT_FALSE;
}

/* 见头文件说明: 芯片断电/复位后寄存器回默认, 必须在这里被照出来。 */
rt_err_t tmc2209_config_integrity_check(void)
{
    rt_uint32_t v = 0;
    subsys_health_t h = tmc2209_get_health();

    if (tmc_cfg_state != TMC_DRV_CONFIGURED || !tmc_cfg_done_valid)
        return -RT_EPERM;

    /* 链路本身已不可信时不猜: 直接降级(读不到 != 配置还在) */
    if (h == SUBSYS_FAILED || h == SUBSYS_DEGRADED)
    {
        tmc_cfg_fail_from_integrity = RT_TRUE;
        return tmc_cfg_fail(0, -RT_EIO, "integrity:link-not-healthy");
    }

    v = 0;
    if (tmc_cfg_bus->read(TMC_REG_GCONF, &v) != RT_EOK)
    {
        tmc_cfg_fail_from_integrity = RT_TRUE;
        return tmc_cfg_fail(TMC_REG_GCONF, -RT_EIO, "integrity:GCONF-read");
    }
    if (v != tmc_cfg_done_gconf)
    {
        rt_kprintf("[TMC-CFG] !! GCONF 已偏离下发值 (%08X != %08X) - 芯片可能断电/复位\n",
                   (unsigned)v, (unsigned)tmc_cfg_done_gconf);
        tmc_cfg_fail_from_integrity = RT_TRUE;
        return tmc_cfg_fail(TMC_REG_GCONF, -RT_EIO, "integrity:GCONF-mismatch");
    }

    v = 0;
    if (tmc_cfg_bus->read(TMC_REG_CHOPCONF, &v) != RT_EOK)
    {
        tmc_cfg_fail_from_integrity = RT_TRUE;
        return tmc_cfg_fail(TMC_REG_CHOPCONF, -RT_EIO, "integrity:CHOPCONF-read");
    }
    if (v != tmc_cfg_done_chop)
    {
        rt_kprintf("[TMC-CFG] !! CHOPCONF 已偏离下发值 (%08X != %08X) - vsense 档可能已丢\n",
                   (unsigned)v, (unsigned)tmc_cfg_done_chop);
        tmc_cfg_fail_from_integrity = RT_TRUE;
        return tmc_cfg_fail(TMC_REG_CHOPCONF, -RT_EIO, "integrity:CHOPCONF-mismatch");
    }
    return RT_EOK;
}

/* ---------- MSH: 配置查看 / 受控下发 / 离线自检 ---------- */

static void tmc_config_show(void)
{
    tmc_drv_config_t c = tmc_cfg_planned;
    rt_uint32_t vfs = c.vsense ? TMC_SENSE_VFS_180_MV : TMC_SENSE_VFS_325_MV;
    rt_uint32_t run  = tmc_current_rms_ma(vfs, TMC_CFG_RSENSE_MOHM, TMC_CFG_VREF_MV, c.irun);
    rt_uint32_t hold = tmc_current_rms_ma(vfs, TMC_CFG_RSENSE_MOHM, TMC_CFG_VREF_MV, c.ihold);
    rt_uint32_t v = 0;

    rt_kprintf("[TMC-CFG] state=%s fail_reg=0x%02X last_err=%d from_integrity=%d\n",
               tmc_drv_cfg_state_name(tmc_cfg_state),
               tmc_cfg_fail_reg, (int)tmc_cfg_last_err,
               (int)tmc_cfg_fail_from_integrity);
    rt_kprintf("[TMC-CFG] hw_write_gate=%s (安全链验收后才可翻转)\n",
               TMC_DRIVE_CONFIG_HW_VALIDATED ? "OPEN" : "CLOSED");
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

    if (tmc_cfg_state == TMC_DRV_CONFIGURED)
        rt_kprintf("[TMC-CFG] applied snapshot: GCONF=%08X CHOPCONF=%08X\n",
                   (unsigned)tmc_cfg_done_gconf, (unsigned)tmc_cfg_done_chop);

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
        rt_kprintf("当前状态: %s, 硬件门禁=%s。写入会改变驱动电流档位, 因此必须"
                   "显式 CONFIRM 且门禁已开放。\n",
                   tmc_drv_cfg_state_name(tmc_cfg_state),
                   TMC_DRIVE_CONFIG_HW_VALIDATED ? "OPEN" : "CLOSED");
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

/* ---------- F2a: 假总线离线自测(完全不碰 UART) ----------
 * 覆盖: 目标值/位域/RMW 不越界、只写寄存器不非法读回、IFCNT 失败即停止后续写、
 * 中途失败置 FAILED、配置丢失被 integrity 照出、门禁与 arm 判定的真值表。
 * 结束后必须还原 tmc_cfg_bus 与 tmc_cfg_state, 否则会污染生产状态。 */
#define TMC_FX_LOG_MAX  24

static struct
{
    rt_uint8_t  reg;
    rt_uint32_t val;
    rt_uint8_t  is_write;
} tmc_fx_log[TMC_FX_LOG_MAX];
static int tmc_fx_n;
static int tmc_fx_fail_write_nth;        /* 第 N 次 write 返回失败(1 基, <=0=不失败) */
static int tmc_fx_writes;                 /* 已发生的 write 次数 */
static rt_uint32_t tmc_fx_gconf, tmc_fx_chop;

static rt_err_t tmc_fx_open(void)  { return RT_EOK; }

static rt_err_t tmc_fx_read(rt_uint8_t reg, rt_uint32_t *v)
{
    if (tmc_fx_n < TMC_FX_LOG_MAX)
    {
        tmc_fx_log[tmc_fx_n].reg = reg;
        tmc_fx_log[tmc_fx_n].val = 0;
        tmc_fx_log[tmc_fx_n].is_write = 0;
        tmc_fx_n++;
    }
    if (reg == TMC_REG_GCONF)      *v = tmc_fx_gconf;
    else if (reg == TMC_REG_CHOPCONF) *v = tmc_fx_chop;
    else return -RT_EIO;            /* 其它寄存器(含只写的 0x10/0x11)本测试不建模 */
    return RT_EOK;
}

static rt_err_t tmc_fx_write(rt_uint8_t reg, rt_uint32_t val)
{
    tmc_fx_writes++;
    if (tmc_fx_fail_write_nth > 0 && tmc_fx_writes == tmc_fx_fail_write_nth)
        return -RT_EIO;              /* 模拟 IFCNT 未 +1 = 写入未被接受 */
    if (tmc_fx_n < TMC_FX_LOG_MAX)
    {
        tmc_fx_log[tmc_fx_n].reg = reg;
        tmc_fx_log[tmc_fx_n].val = val;
        tmc_fx_log[tmc_fx_n].is_write = 1;
        tmc_fx_n++;
    }
    if (reg == TMC_REG_GCONF)      tmc_fx_gconf = val;
    else if (reg == TMC_REG_CHOPCONF) tmc_fx_chop = val;
    return RT_EOK;
}

static const tmc_cfg_bus_t tmc_cfg_bus_fake =
{
    tmc_fx_open, tmc_fx_read, tmc_fx_write
};

static int tmc_fx_find_write(rt_uint8_t reg, rt_uint32_t *val)
{
    int i;
    for (i = 0; i < tmc_fx_n; ++i)
        if (tmc_fx_log[i].is_write && tmc_fx_log[i].reg == reg)
        { if (val) *val = tmc_fx_log[i].val; return 1; }
    return 0;
}
static int tmc_fx_any_read_of(rt_uint8_t reg)
{
    int i;
    for (i = 0; i < tmc_fx_n; ++i)
        if (!tmc_fx_log[i].is_write && tmc_fx_log[i].reg == reg) return 1;
    return 0;
}
static int tmc_fx_write_count_of(rt_uint8_t reg)
{
    int i, n = 0;
    for (i = 0; i < tmc_fx_n; ++i)
        if (tmc_fx_log[i].is_write && tmc_fx_log[i].reg == reg) n++;
    return n;
}
static void tmc_fx_reset(void)
{
    rt_memset(tmc_fx_log, 0, sizeof(tmc_fx_log));
    tmc_fx_n = 0;
    tmc_fx_writes = 0;
    tmc_fx_fail_write_nth = 0;
    tmc_fx_gconf = 0x00000101u;     /* 手册复位默认 */
    tmc_fx_chop  = 0x15010053u;     /* 本轮真机读回的上电基线 */
}

static rt_bool_t tmc_fx_run_expect_ok(rt_bool_t set_health_ok_unused)
{
    (void)set_health_ok_unused;
    return (tmc_cfg_run(&tmc_cfg_planned, &tmc_cfg_bus_fake, RT_TRUE) == RT_EOK)
           ? RT_TRUE : RT_FALSE;
}

static void tmc_cfg_selftest(void)
{
    tmc_drv_cfg_state_t saved_state = tmc_cfg_state;
    const tmc_cfg_bus_t *saved_bus  = tmc_cfg_bus;
    rt_uint32_t v = 0;
    int pass = 1;

    rt_kprintf("[TMC-CFG-ST] 假总线离线自测, 不使用 uart2\n");

    /* --- T1: 正常全流程 -> CONFIGURED, 四个目标值逐项核对 --- */
    tmc_fx_reset();
    if (!tmc_fx_run_expect_ok(RT_TRUE))
    { rt_kprintf("[TMC-CFG-ST] T1 apply 未成功\n"); pass = 0; }
    if (tmc_cfg_state != TMC_DRV_CONFIGURED)
    { rt_kprintf("[TMC-CFG-ST] T1 state=%s 应为 CONFIGURED\n",
                 tmc_drv_cfg_state_name(tmc_cfg_state)); pass = 0; }

    if (!tmc_fx_find_write(TMC_REG_GCONF, &v) || v != TMC_CFG_TGT_GCONF)
    { rt_kprintf("[TMC-CFG-ST] T1 GCONF 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_GCONF); pass = 0; }
    if (!tmc_fx_find_write(TMC_REG_CHOPCONF, &v) || v != TMC_CFG_TGT_CHOPCONF)
    { rt_kprintf("[TMC-CFG-ST] T1 CHOPCONF 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_CHOPCONF); pass = 0; }
    if (!tmc_fx_find_write(TMC_REG_IHOLD_IRUN, &v) || v != TMC_CFG_TGT_IHOLD_IRUN)
    { rt_kprintf("[TMC-CFG-ST] T1 IHOLD_IRUN 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_IHOLD_IRUN); pass = 0; }
    if (!tmc_fx_find_write(TMC_REG_TPOWERDOWN, &v) || v != TMC_CFG_TGT_TPOWERDOWN)
    { rt_kprintf("[TMC-CFG-ST] T1 TPOWERDOWN 目标错: %08X (期望 %08X)\n",
                 (unsigned)v, (unsigned)TMC_CFG_TGT_TPOWERDOWN); pass = 0; }

    /* --- T2: 位域细节: vsense=bit17 置起, bit18 与 TOFF/HSTRT/HEND/TBL 不受扰动 --- */
    {
        rt_uint32_t base = 0x15010053u, nxt = tmc_chopconf_next(base, &tmc_cfg_planned);
        rt_kprintf("[TMC-CFG-ST] T2 CHOPCONF %08X -> %08X\n",
                   (unsigned)base, (unsigned)nxt);
        if (!(nxt & (1u << 17)))
        { rt_kprintf("[TMC-CFG-ST] T2 bit17 未置起\n"); pass = 0; }
        if (nxt & (1u << 18))
        { rt_kprintf("[TMC-CFG-ST] T2 bit18 被意外修改\n"); pass = 0; }
        if ((nxt & 0xFu) != (base & 0xFu) || (nxt & 0xFu) == 0u)
        { rt_kprintf("[TMC-CFG-ST] T2 TOFF 未保持非零\n"); pass = 0; }
        if ((nxt & 0x7F0u) != (base & 0x7F0u))
        { rt_kprintf("[TMC-CFG-ST] T2 HSTRT/HEND 低位被改\n"); pass = 0; }
        if ((nxt & 0x00018000u) != (base & 0x00018000u))
        { rt_kprintf("[TMC-CFG-ST] T2 TBL 被改\n"); pass = 0; }
        /* 最强 RMW 断言: 除 vsense(bit17) 外不得有任何一位变化 */
        if (((nxt ^ base) & ~TMC_CHOPCONF_VSENSE) != 0u)
        { rt_kprintf("[TMC-CFG-ST] T2 越界改动非 vsense 位 (xor=%08X)\n",
                     (unsigned)(nxt ^ base)); pass = 0; }
    }

    /* --- T3: GCONF 只加 bit6, 保留 bit0/bit8, 不动 bit1(OTP 位) --- */
    {
        rt_uint32_t base = 0x00000101u, nxt = tmc_gconf_next(base, &tmc_cfg_planned);
        rt_kprintf("[TMC-CFG-ST] T3 GCONF %08X -> %08X\n",
                   (unsigned)base, (unsigned)nxt);
        if (nxt != 0x00000141u)
        { rt_kprintf("[TMC-CFG-ST] T3 结果非 0x141\n"); pass = 0; }
        if (!(nxt & (1u << 6)))
        { rt_kprintf("[TMC-CFG-ST] T3 pdn_disable 未置起\n"); pass = 0; }
        if (!(nxt & 1u))
        { rt_kprintf("[TMC-CFG-ST] T3 i_scale_analog 被清\n"); pass = 0; }
        if (!(nxt & (1u << 8)))
        { rt_kprintf("[TMC-CFG-ST] T3 multistep_filt 被清\n"); pass = 0; }
        if (nxt & (1u << 1))
        { rt_kprintf("[TMC-CFG-ST] T3 意外置起 internal_Rsense\n"); pass = 0; }
        /* 只允许 i_scale_analog / pdn_disable / mstep_reg_select 三位变化 */
        if (((nxt ^ base) & ~(1u | (1u << 6) | (1u << 7))) != 0u)
        { rt_kprintf("[TMC-CFG-ST] T3 越界改动其它 GCONF 位 (xor=%08X)\n",
                     (unsigned)(nxt ^ base)); pass = 0; }
    }

    /* --- T4: 只写寄存器不得被读回(0x10/0x11 无读操作) --- */
    if (tmc_fx_any_read_of(TMC_REG_IHOLD_IRUN) ||
        tmc_fx_any_read_of(TMC_REG_TPOWERDOWN))
    { rt_kprintf("[TMC-CFG-ST] T4 对只写寄存器发起了读\n"); pass = 0; }
    /* 可读寄存器必须既有写也有读回 */
    if (tmc_fx_write_count_of(TMC_REG_GCONF) < 1 ||
        !tmc_fx_any_read_of(TMC_REG_GCONF))
    { rt_kprintf("[TMC-CFG-ST] T4 GCONF 缺少预读/读回\n"); pass = 0; }
    if (tmc_fx_write_count_of(TMC_REG_CHOPCONF) < 1 ||
        !tmc_fx_any_read_of(TMC_REG_CHOPCONF))
    { rt_kprintf("[TMC-CFG-ST] T4 CHOPCONF 缺少预读/读回\n"); pass = 0; }

    /* --- T5: integrity 在配置一致时通过 --- */
    if (tmc2209_config_integrity_check() != RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T5 integrity 误判失败\n"); pass = 0; }
    if (!tmc2209_drive_config_ok_for_arm())
    { rt_kprintf("[TMC-CFG-ST] T5 CONFIGURED 却不容许 arm\n"); pass = 0; }

    /* --- T6: 模拟芯片断电/复位(寄存器回默认) -> integrity 必须照出来并降级 --- */
    tmc_fx_gconf = 0x00000101u;
    tmc_fx_chop  = 0x15010053u;
    if (tmc2209_config_integrity_check() == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T6 芯片复位后 integrity 仍说 OK\n"); pass = 0; }
    if (tmc_cfg_state != TMC_DRV_FAILED)
    { rt_kprintf("[TMC-CFG-ST] T6 状态未变 FAILED(%s)\n",
                 tmc_drv_cfg_state_name(tmc_cfg_state)); pass = 0; }
    if (tmc2209_drive_config_ok_for_arm())
    { rt_kprintf("[TMC-CFG-ST] T6 配置已丢却仍容许 arm\n"); pass = 0; }
    rt_kprintf("[TMC-CFG-ST] T6 失联/复位 -> FAILED, arm 拒绝 (fail-closed OK)\n");

    /* --- T7: IFCNT 在第 2 次写(CHOPCONF)失败 -> 必须立即停止后续写 --- */
    tmc_fx_reset();
    tmc_cfg_state = TMC_DRV_UNCONFIGURED;
    tmc_fx_fail_write_nth = 2;
    if (tmc_cfg_run(&tmc_cfg_planned, &tmc_cfg_bus_fake, RT_TRUE) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T7 写失败却返回成功\n"); pass = 0; }
    if (tmc_cfg_state != TMC_DRV_FAILED)
    { rt_kprintf("[TMC-CFG-ST] T7 状态=%s 应为 FAILED\n",
                 tmc_drv_cfg_state_name(tmc_cfg_state)); pass = 0; }
    if (tmc_fx_write_count_of(TMC_REG_IHOLD_IRUN) || tmc_fx_write_count_of(TMC_REG_TPOWERDOWN))
    { rt_kprintf("[TMC-CFG-ST] T7 失败后仍继续写后续寄存器\n"); pass = 0; }
    rt_kprintf("[TMC-CFG-ST] T7 第 2 次写失败 -> 已停止, 后续 0 次写\n");

    /* --- T8: 第 1 次写就失败(链路死) -> FAILED 且不写任何后续 --- */
    tmc_fx_reset();
    tmc_cfg_state = TMC_DRV_UNCONFIGURED;
    tmc_fx_fail_write_nth = 1;
    if (tmc_cfg_run(&tmc_cfg_planned, &tmc_cfg_bus_fake, RT_TRUE) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T8 首次写失败却返回成功\n"); pass = 0; }
    if (tmc_fx_write_count_of(TMC_REG_CHOPCONF) || tmc_fx_write_count_of(TMC_REG_TPOWERDOWN))
    { rt_kprintf("[TMC-CFG-ST] T8 首写失败后仍继续\n"); pass = 0; }

    /* --- T9: 未配置 / 失败态下 arm 判定必须为假 --- */
    tmc_cfg_state = TMC_DRV_UNCONFIGURED;
    if (tmc2209_drive_config_ok_for_arm())
    { rt_kprintf("[TMC-CFG-ST] T9 UNCONFIGURED 却容许 arm\n"); pass = 0; }
    tmc_cfg_state = TMC_DRV_FAILED;
    if (tmc2209_drive_config_ok_for_arm())
    { rt_kprintf("[TMC-CFG-ST] T9 FAILED 却容许 arm\n"); pass = 0; }
    tmc_cfg_state = TMC_DRV_CONFIGURING;
    if (tmc2209_drive_config_ok_for_arm())
    { rt_kprintf("[TMC-CFG-ST] T9 CONFIGURING 却容许 arm\n"); pass = 0; }
    if (tmc2209_config_integrity_check() != -RT_EPERM)
    { rt_kprintf("[TMC-CFG-ST] T9 非 CONFIGURED 时 integrity 应拒绝\n"); pass = 0; }

    /* --- T10: 过流方向 1->0 与 TOFF=0 的芯片都必须中止 --- */
    tmc_fx_reset();
    tmc_fx_chop = 0x15030053u;                 /* 已是 vsense=1 */
    tmc_cfg_state = TMC_DRV_UNCONFIGURED;
    {
        tmc_drv_config_t down = tmc_cfg_planned;
        down.vsense = 0;
        if (tmc_cfg_run(&down, &tmc_cfg_bus_fake, RT_TRUE) == RT_EOK)
        { rt_kprintf("[TMC-CFG-ST] T10 1->0 方向未被拒\n"); pass = 0; }
        if (tmc_fx_write_count_of(TMC_REG_IHOLD_IRUN))
        { rt_kprintf("[TMC-CFG-ST] T10 拒绝后仍写了电流档\n"); pass = 0; }
    }
    tmc_fx_reset();
    tmc_fx_chop = 0x15010050u;                 /* TOFF=0, 桥全关 */
    tmc_cfg_state = TMC_DRV_UNCONFIGURED;
    if (tmc_cfg_run(&tmc_cfg_planned, &tmc_cfg_bus_fake, RT_TRUE) == RT_EOK)
    { rt_kprintf("[TMC-CFG-ST] T10 TOFF=0 基线竟通过\n"); pass = 0; }
    if (tmc_fx_write_count_of(TMC_REG_CHOPCONF))
    { rt_kprintf("[TMC-CFG-ST] T10 TOFF 非法时仍写了 CHOPCONF\n"); pass = 0; }

    /* --- 还原生产上下文(关键: 自测不得污染状态) --- */
    tmc_cfg_bus   = saved_bus;
    tmc_cfg_state = saved_state;
    tmc_cfg_done_valid = RT_FALSE;
    tmc_cfg_fail_from_integrity = RT_FALSE;
    tmc_cfg_fail_reg = 0;
    tmc_cfg_last_err = RT_EOK;
    if (tmc_cfg_bus != &tmc_cfg_bus_real)
    { rt_kprintf("[TMC-CFG-ST] RESTORE FAIL: 总线未还原\n"); pass = 0; }
    rt_kprintf("[TMC-CFG-ST] 已还原 state=%s, 总线=real\n",
               tmc_drv_cfg_state_name(tmc_cfg_state));

    rt_kprintf("[TMC-CFG-ST] %s (T1..T10, 假总线, 未访问 uart2)\n",
               pass ? "ALL PASS" : "FAILED");
}
MSH_CMD_EXPORT(tmc_cfg_selftest, offline fake-bus drive config state machine selftest);
