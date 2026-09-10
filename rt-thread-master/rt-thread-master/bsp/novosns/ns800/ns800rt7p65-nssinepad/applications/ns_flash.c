#include "ns_flash.h"
#include "ns_flash_config.h"
#include <rtdevice.h>
#include <string.h>
#include <finsh.h>

static struct rt_spi_device spi;
static struct rt_mutex lock;
static int resources_ready, attached, ready;

static int flash_resources(void)
{
    rt_err_t e = rt_mutex_init(&lock, "flock", RT_IPC_FLAG_PRIO);
    if (e == RT_EOK) resources_ready = 1;
    return e;
}
INIT_APP_EXPORT(flash_resources);

static rt_err_t send_cmd(uint8_t cmd)
{
    return rt_spi_send(&spi, &cmd, 1) == 1 ? RT_EOK : -RT_EIO;
}
static rt_err_t status(uint8_t cmd, uint8_t *value)
{
    return rt_spi_send_then_recv(&spi, &cmd, 1, value, 1);
}
static rt_err_t wait_ready(int timeout_ms)
{
    rt_tick_t start = rt_tick_get();
    rt_tick_t timeout = rt_tick_from_millisecond(timeout_ms);
    uint8_t sr;
    rt_err_t e;
    if (!timeout) timeout = 1;
    for (;;)
    {
        e = status(0x05, &sr);
        if (e != RT_EOK) return e;
        if (!(sr & 1)) return RT_EOK;
        if ((rt_tick_t)(rt_tick_get() - start) >= timeout) return -RT_ETIMEOUT;
        rt_thread_mdelay(1); /* SPI bus is released between polls. */
    }
}
static rt_err_t range_ok(uint32_t a, const void *p, rt_size_t n)
{
    if ((n && !p) || a > NSF_CAPACITY || n > NSF_CAPACITY - a)
        return -RT_EINVAL;
    return RT_EOK;
}
static void address_cmd(uint8_t c[4], uint8_t op, uint32_t a)
{
    c[0] = op; c[1] = (uint8_t)(a >> 16);
    c[2] = (uint8_t)(a >> 8); c[3] = (uint8_t)a;
}
static rt_err_t read_raw(uint32_t a, void *p, rt_size_t n)
{
    uint8_t c[4];
    address_cmd(c, 0x03, a);
    return rt_spi_send_then_recv(&spi, c, 4, p, n);
}
static rt_err_t writable(void)
{
    uint8_t s1, s2, s3;
    rt_err_t e = wait_ready(NSF_ERASE_MS);
    if (e != RT_EOK) return e;
    if ((e = status(0x05, &s1)) != RT_EOK ||
        (e = status(0x35, &s2)) != RT_EOK ||
        (e = status(0x15, &s3)) != RT_EOK) return e;
    /* Conservative policy: report protection/suspend, never silently unlock. */
    /* A chip without SR3 (e.g. W25Q64FV, same JEDEC as JV) floats 0x15 reads
     * to 0xFF; only treat SR3 bits as protection when the register is real. */
    if ((s1 & 0x1c) || (s2 & 0xc0) || (s3 != 0xff && (s3 & 0x04))) return -RT_EBUSY;
    return RT_EOK;
}
static rt_err_t write_enable(void)
{
    uint8_t sr;
    rt_err_t e = send_cmd(0x06);
    if (e != RT_EOK) return e;
    e = status(0x05, &sr);
    if (e != RT_EOK) return e;
    return (sr & 2) ? RT_EOK : -RT_EIO;
}
static rt_err_t enter(void)
{
    rt_err_t e;
    if (!resources_ready || rt_interrupt_get_nest()) return -RT_ERROR;
    e = rt_mutex_take(&lock, RT_WAITING_FOREVER);
    if (e != RT_EOK) return e;
    if (!ready) { rt_mutex_release(&lock); return -RT_ERROR; }
    return RT_EOK;
}
rt_err_t ns_flash_init(void)
{
    struct rt_spi_configuration cfg;
    uint8_t id[3], cmd = 0x9f;
    rt_err_t e;
	      if (get_pin_info(NSF_CS_PIN) == RT_NULL ||
        get_pin_info(NSF_ONBOARD_CS_PIN) == RT_NULL ||
        get_pin_info(NSF_IMU_CS_PIN) == RT_NULL)
    {
        rt_kprintf(
            "[FLASH] invalid CS mapping: flash=%d onboard=%d imu=%d\n",
            (int)NSF_CS_PIN,
            (int)NSF_ONBOARD_CS_PIN,
            (int)NSF_IMU_CS_PIN);

        return -RT_EINVAL;
    }
    if (!resources_ready || rt_interrupt_get_nest()) return -RT_ERROR;
    e = rt_mutex_take(&lock, RT_WAITING_FOREVER);
    if (e != RT_EOK) return e;
    if (ready) { e = RT_EOK; goto out; }
    if (!attached)
    {
        if (!rt_device_find(NSF_BUS_NAME)) { e = -RT_ERROR; goto out; }
        /* Never reuse an unknown registered device/CS. */
        if (rt_device_find(NSF_DEVICE_NAME)) { e = -RT_EBUSY; goto out; }
        rt_pin_write(NSF_ONBOARD_CS_PIN, PIN_HIGH);
        rt_pin_mode(NSF_ONBOARD_CS_PIN, PIN_MODE_OUTPUT);
        rt_pin_write(NSF_ONBOARD_CS_PIN, PIN_HIGH);
        rt_pin_write(NSF_IMU_CS_PIN, PIN_HIGH);
        rt_pin_mode(NSF_IMU_CS_PIN, PIN_MODE_OUTPUT);
        rt_pin_write(NSF_IMU_CS_PIN, PIN_HIGH);
        rt_pin_write(NSF_CS_PIN, PIN_HIGH);
        rt_pin_mode(NSF_CS_PIN, PIN_MODE_OUTPUT);
        rt_pin_write(NSF_CS_PIN, PIN_HIGH);
        e = rt_spi_bus_attach_device_cspin(&spi, NSF_DEVICE_NAME,
                                          NSF_BUS_NAME, NSF_CS_PIN, RT_NULL);
        if (e != RT_EOK) goto out;
        attached = 1;
    }
    memset(&cfg, 0, sizeof(cfg));
    cfg.mode = RT_SPI_MASTER | RT_SPI_MODE_0 | RT_SPI_MSB;
    cfg.data_width = 8; cfg.max_hz = NSF_SPI_HZ;
    e = rt_spi_configure(&spi, &cfg);
    /* -RT_EBUSY on this core = bus owned by another device;
     * the stored config takes effect when this device takes the bus. */
    if (e != RT_EOK && e != -RT_EBUSY) goto out;
    e = send_cmd(0xab); /* Release deep power-down, no array change. */
    if (e != RT_EOK) goto out;
    rt_thread_mdelay(5);
    e = wait_ready(NSF_ERASE_MS);
    if (e != RT_EOK) goto out;
    e = rt_spi_send_then_recv(&spi, &cmd, 1, id, 3);
    if (e != RT_EOK) goto out;
    rt_kprintf("[FLASH] CS=%d JEDEC=%02X %02X %02X\n", (int)NSF_CS_PIN,
               id[0], id[1], id[2]);
    if (id[0] != 0xef || id[1] != 0x40 || id[2] != 0x17)
    { e = -RT_EIO; goto out; }
    ready = 1;
out:
    rt_mutex_release(&lock);
    return e;
}
rt_err_t ns_flash_identify(uint8_t id[3], uint8_t sr[3])
{
    uint8_t cmd = 0x9f;
    rt_err_t e;
    if (!id || !sr) return -RT_EINVAL;
    if ((e = enter()) != RT_EOK) return e;
    e = wait_ready(NSF_ERASE_MS);
    if (e == RT_EOK) e = rt_spi_send_then_recv(&spi, &cmd, 1, id, 3);
    if (e == RT_EOK) e = status(0x05, sr);
    if (e == RT_EOK) e = status(0x35, sr + 1);
    if (e == RT_EOK) e = status(0x15, sr + 2);
    rt_mutex_release(&lock);
    return e;
}
rt_err_t ns_flash_read(uint32_t a, void *p, rt_size_t n)
{
    uint8_t *dst = p;
    rt_size_t k;
    rt_err_t e = range_ok(a, p, n);
    if (e != RT_EOK || !n) return e;
    if ((e = enter()) != RT_EOK) return e;
    e = wait_ready(NSF_ERASE_MS);
    while (e == RT_EOK && n)
    {
        k = n > NSF_PAGE_SIZE ? NSF_PAGE_SIZE : n;
        e = read_raw(a, dst, k);
        a += k; dst += k; n -= k;
    }
    rt_mutex_release(&lock);
    return e;
}
rt_err_t ns_flash_program(uint32_t a, const void *p, rt_size_t n)
{
    uint8_t old[NSF_PAGE_SIZE], c[4];
    const uint8_t *src = p;
    rt_size_t i, k, left = n;
    uint32_t scan = a;
    rt_err_t e = range_ok(a, p, n);
    if (e != RT_EOK || !n) return e;
    if ((e = enter()) != RT_EOK) return e;
    e = writable();
    /* Check the WHOLE request before changing any byte. */
    while (e == RT_EOK && left)
    {
        k = left > sizeof(old) ? sizeof(old) : left;
        e = read_raw(scan, old, k);
        if (e != RT_EOK) break;
        for (i = 0; i < k; ++i)
            if ((old[i] & src[i]) != src[i]) { e = -RT_EINVAL; break; }
        scan += k; src += k; left -= k;
    }
    src = p;
    while (e == RT_EOK && n)
    {
        k = NSF_PAGE_SIZE - (a % NSF_PAGE_SIZE);
        if (k > n) k = n;
        address_cmd(c, 0x02, a);
        e = write_enable();
        if (e == RT_EOK) e = rt_spi_send_then_send(&spi, c, 4, src, k);
        if (e == RT_EOK) e = wait_ready(NSF_PROGRAM_MS);
        if (e == RT_EOK) e = read_raw(a, old, k);
        if (e == RT_EOK && memcmp(old, src, k)) e = -RT_EIO;
        a += k; src += k; n -= k;
    }
    /* Disarm WEL even if a transport failure occurred after WREN. */
    (void)send_cmd(0x04);
    rt_mutex_release(&lock);
    return e;
}
/* Explicitly clear SR1 block-protect bits and SR3 WPS. The driver itself
 * never unlocks silently (see writable()); this is a user-commanded step.
 * SR1=0xFC (BP0-2) and SR3=0xFF (WPS) were observed on the new module. */
rt_err_t ns_flash_clearwp(void)
{
    uint8_t s1, s2, s3;
    rt_err_t e = enter();
    if (e != RT_EOK) return e;
    /* WEL self-clears after every WRSR, so each write needs its own WREN,
     * and the next WRSR must wait for WIP to clear. */
    if ((e = write_enable()) == RT_EOK)
    {
        uint8_t c = 0x01, v = 0x00;      /* WRSR1 = 0x00: clear BP0/BP1/BP2/TB/SEC/SRP1 */
        e = rt_spi_send_then_send(&spi, &c, 1, &v, 1);
        if (e == RT_EOK) e = wait_ready(NSF_PROGRAM_MS);
    }
    e = status(0x15, &s3);
    if (e == RT_EOK && s3 != 0xff)   /* SR3 present: clear WPS/ADP/ADS */
    {
        e = write_enable();
        if (e == RT_EOK)
        {
            uint8_t c = 0x11, v = 0x00;
            e = rt_spi_send_then_send(&spi, &c, 1, &v, 1);
            if (e == RT_EOK) e = wait_ready(NSF_PROGRAM_MS);
        }
    }
    if (e == RT_EOK)
    {
        uint8_t c = 0x04;                /* WRDI: drop WEL left by WRSR */
        e = (rt_spi_send(&spi, &c, 1) == 1) ? RT_EOK : -RT_EIO;
    }
    if (e == RT_EOK)
    {
        e = status(0x05, &s1);
        if (e == RT_EOK) e = status(0x35, &s2);
        if (e == RT_EOK) e = status(0x15, &s3);
        rt_kprintf("[FLASH] after clearwp: SR1=%02X SR2=%02X SR3=%02X\n", s1, s2, s3);
        if (e == RT_EOK && (s1 & 0x1c || (s3 != 0xff && (s3 & 0x04)))) e = -RT_EIO;
    }
    rt_mutex_release(&lock);
    return e;
}

rt_err_t ns_flash_erase(uint32_t a, rt_size_t n)
{
    uint8_t c[4], check[NSF_PAGE_SIZE];
    uint32_t off;
    rt_size_t i;
    rt_err_t e;
    if (a > NSF_CAPACITY || n > NSF_CAPACITY - a ||
        a % NSF_SECTOR_SIZE || n % NSF_SECTOR_SIZE) return -RT_EINVAL;
    if (!n) return RT_EOK;
    if ((e = enter()) != RT_EOK) return e;
    e = writable();
    while (e == RT_EOK && n)
    {
        address_cmd(c, 0x20, a);
        e = write_enable();
        if (e == RT_EOK && rt_spi_send(&spi, c, 4) != 4) e = -RT_EIO;
        if (e == RT_EOK) e = wait_ready(NSF_ERASE_MS);
        for (off = 0; e == RT_EOK && off < NSF_SECTOR_SIZE; off += sizeof(check))
        {
            e = read_raw(a + off, check, sizeof(check));
            if (e != RT_EOK) break;
            for (i = 0; i < sizeof(check); ++i)
                if (check[i] != 0xff) { e = -RT_EIO; break; }
        }
        a += NSF_SECTOR_SIZE; n -= NSF_SECTOR_SIZE;
    }
    (void)send_cmd(0x04);
    rt_mutex_release(&lock);
    return e;
}
/* ������flash_diag */
static void flash_diag(void)
{
    rt_err_t e;
    rt_err_t id_ret;
    rt_err_t sr_ret;
    uint8_t cmd;
    uint8_t id[3];
    uint8_t sr1;
    int i;

    rt_kprintf("\n========== FLASH DIAG ==========\n");

    /* ��ӡ��������ʱʵ��ʹ�õĴ����� */
    rt_kprintf("ERROR=%d EIO=%d TIMEOUT=%d BUSY=%d EINVAL=%d\n",
               (int)-RT_ERROR,
               (int)-RT_EIO,
               (int)-RT_ETIMEOUT,
               (int)-RT_EBUSY,
               (int)-RT_EINVAL);

    rt_kprintf("bus=%s device=%s CS_PIN=%d\n",
               NSF_BUS_NAME,
               NSF_DEVICE_NAME,
               (int)NSF_CS_PIN);

    rt_kprintf("resources=%d attached=%d ready=%d\n",
               resources_ready, attached, ready);

    rt_kprintf("bus_found=%d\n",
               rt_device_find(NSF_BUS_NAME) != RT_NULL);

    e = ns_flash_init();

    rt_kprintf("ns_flash_init: %d\n", (int)e);

    if (e == -RT_ETIMEOUT)
        rt_kprintf("error_name: RT_ETIMEOUT\n");
    else if (e == -RT_EIO)
        rt_kprintf("error_name: RT_EIO\n");
    else if (e == -RT_EBUSY)
        rt_kprintf("error_name: RT_EBUSY\n");
    else if (e == -RT_ERROR)
        rt_kprintf("error_name: RT_ERROR\n");

    /*
     * δ�����Դ��ʼ�����豸����ʱ������������ SPI��
     */
    if (!resources_ready || !attached)
    {
        rt_kprintf("Stopped before SPI device became available.\n");
        return;
    }

    e = rt_mutex_take(&lock, RT_WAITING_FOREVER);
    if (e != RT_EOK)
    {
        rt_kprintf("mutex failed: %d\n", (int)e);
        return;
    }

    /*
     * ֱ�Ӷ�ԭʼ����ֵ������ ready=0 �����˳�������
     * ����ֻ�� ID ��״̬��������������̡���������
     */
    for (i = 0; i < 3; ++i)
    {
        id[0] = 0;
        id[1] = 0;
        id[2] = 0;
        sr1 = 0;

        cmd = 0x9F;
        id_ret = rt_spi_send_then_recv(
            &spi, &cmd, 1, id, sizeof(id));

        sr_ret = status(0x05, &sr1);

        rt_kprintf(
            "READ%d: id_ret=%d ID=%02X %02X %02X "
            "sr_ret=%d SR1=%02X\n",
            i + 1,
            (int)id_ret,
            id[0], id[1], id[2],
            (int)sr_ret,
            sr1);

        rt_thread_mdelay(10);
    }

    rt_mutex_release(&lock);

    rt_kprintf("Note: ID/SR bytes are valid only when ret=0.\n");
    rt_kprintf("================================\n");
}

MSH_CMD_EXPORT(flash_diag, diagnose flash initialization and raw SPI replies);
static void flash_regcheck(void)
{
    struct rt_spi_configuration saved_cfg;
    struct rt_spi_configuration cfg;

    const rt_uint32_t speeds[2] = {1000000, 100000};
    const rt_uint8_t commands[4] = {0x9F, 0x05, 0x35, 0x15};

    rt_uint8_t tx[9];
    rt_uint8_t rx[9];
    rt_size_t transferred;
    rt_err_t e;
    int speed_index;
    int command_index;
    int i;

    e = ns_flash_init();
    if (e != RT_EOK)
    {
        rt_kprintf("init failed: %d\n", (int)e);
        return;
    }

    e = rt_mutex_take(&lock, RT_WAITING_FOREVER);
    if (e != RT_EOK)
    {
        rt_kprintf("lock failed: %d\n", (int)e);
        return;
    }

    saved_cfg = spi.config;
    cfg = saved_cfg;

    rt_kprintf("\n=== FLASH REGISTER CHECK ===\n");

    for (speed_index = 0; speed_index < 2; ++speed_index)
    {
        cfg.max_hz = speeds[speed_index];

        e = rt_spi_configure(&spi, &cfg);
        if (e != RT_EOK)
        {
            rt_kprintf("configure failed: %d\n", (int)e);
            break;
        }

        rt_kprintf("Requested SPI clock: %u Hz\n",
                   (unsigned)cfg.max_hz);

        for (command_index = 0;
             command_index < 4;
             ++command_index)
        {
            memset(tx, 0xFF, sizeof(tx));
            memset(rx, 0, sizeof(rx));

            tx[0] = commands[command_index];

            /*
             * һ�δ�����ɣ����� + 8�������ֽڡ�
             * rx[0] �Ƿ��������ڼ��յ�����Ч�ֽڣ�������
             */
            transferred = rt_spi_transfer(
                &spi, tx, rx, sizeof(tx));

            if (transferred != sizeof(tx))
            {
                rt_kprintf("CMD=%02X transfer failed, count=%u\n",
                           tx[0], (unsigned)transferred);
                continue;
            }

            if (tx[0] == 0x9F)
            {
                rt_kprintf("ID : %02X %02X %02X\n",
                           rx[1], rx[2], rx[3]);
            }
            else
            {
                rt_kprintf("SR%d:", command_index);

                for (i = 1; i < 9; ++i)
                    rt_kprintf(" %02X", rx[i]);

                rt_kprintf("\n");
            }

            rt_thread_mdelay(10);
        }
    }

    e = rt_spi_configure(&spi, &saved_cfg);
    rt_kprintf("restore config: %d\n", (int)e);

    rt_mutex_release(&lock);
}

MSH_CMD_EXPORT(flash_regcheck, check flash registers with continuous SPI);
static void flash_probe(void)
{
    static const rt_uint8_t cmds[3] = {0x04, 0x06, 0x04};
    rt_uint8_t sr[3] = {0};
    rt_uint8_t tx[21];
    rt_uint8_t rx[21];
    rt_err_t e;
    rt_err_t cleanup;
    rt_size_t n;
    int i;
    int wel_ok = 1;

    e = ns_flash_init();
    if (e != RT_EOK)
    {
        rt_kprintf("[PROBE] init failed: %d\n", e);
        return;
    }

    e = enter();
    if (e != RT_EOK)
    {
        rt_kprintf("[PROBE] lock failed: %d\n", e);
        return;
    }

    e = wait_ready(1500);
    if (e != RT_EOK)
    {
        rt_kprintf("[PROBE] wait ready failed: %d\n", e);
        rt_mutex_release(&lock);
        return;
    }

    rt_kprintf("\n=== FLASH PROBE ===\n");

    /* WRDI -> WREN -> WRDI; read SR1 after each command. */
    for (i = 0; i < 3; i++)
    {
        e = send_cmd(cmds[i]);
        if (e == RT_EOK)
            e = status(0x05, &sr[i]);

        if (e != RT_EOK)
        {
            rt_kprintf("CMD=%02X error=%d\n", cmds[i], e);
            wel_ok = 0;
            break;
        }

        rt_kprintf("CMD=%02X SR1=%02X WEL=%d\n",
                   cmds[i], sr[i], (sr[i] >> 1) & 1);
    }

    /* Always attempt to leave writes disabled, including on error. */
    cleanup = send_cmd(0x04);
    if (cleanup != RT_EOK)
    {
        rt_kprintf("WRDI cleanup failed: %d\n", cleanup);
        rt_mutex_release(&lock);
        return;
    }

    if (wel_ok)
    {
        rt_kprintf("WEL sequence: %s\n",
                   (!(sr[0] & 2) &&
                     (sr[1] & 2) &&
                    !(sr[2] & 2)) ? "PASS" : "FAIL");
    }

    /* SFDP: command + 24-bit address + 8 dummy clocks + 16 bytes. */
    memset(tx, 0xFF, sizeof(tx));
    memset(rx, 0, sizeof(rx));
    tx[0] = 0x5A;
    tx[1] = 0x00;
    tx[2] = 0x00;
    tx[3] = 0x00;

    n = rt_spi_transfer(&spi, tx, rx, sizeof(tx));

    if (n != sizeof(tx))
    {
        rt_kprintf("SFDP transfer failed: %u bytes\n",
                   (unsigned int)n);
    }
    else
    {
        rt_kprintf("SFDP:");
        for (i = 5; i < 21; i++)
            rt_kprintf(" %02X", rx[i]);
        rt_kprintf("\n");

        rt_kprintf("SFDP signature: %s\n",
                   (rx[5] == 0x53 && rx[6] == 0x46 &&
                    rx[7] == 0x44 && rx[8] == 0x50)
                   ? "PASS" : "FAIL");
    }

    rt_mutex_release(&lock);
    rt_kprintf("===================\n");
}

MSH_CMD_EXPORT(flash_probe, check Flash WEL and SFDP);