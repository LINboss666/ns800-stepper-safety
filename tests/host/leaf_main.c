/* leaf_main.c —— CP5 §5: **真实 HAL 叶子 + 真实 TMC 生产本体**的主机夹具(独立一条链)。
 *
 * 与既有五条链的分工(任务书 §5: "禁止再只测试端口伪实现"):
 *   V1V2 / I-O / SD / S1 / S2 并发链 —— 应用本体齐全, 但 EPWM/UART/TMC 三段是**端口桩**;
 *   本链                               —— 被测三段全部是生产实现本体, 端口只留给应用层状态:
 *     applications/stop_priority.c        (协调层: token 门 / 短保护 / 预约)
 *     applications/tmc2209.c              (配置事务、cfg/xfer 两把真锁、IFCNT、预算、摘要发布)
 *     libraries/HAL_Drivers/drivers/drv_uart_txleaf.inc   (TX 等待环 —— 唯一实现)
 *     libraries/HAL_Drivers/drivers/drv_epwm_leaf.inc     (prepare/configure_regs/commit)
 *
 * 本链因此能观察那五条链观察不到的东西:
 *   - **寄存器写次数**: 计数点在 shim_leaf/drv_common.h 的 WRITE/MODIFY/SET/CLEAR 宏里,
 *     表达式的值与副作用与 SDK 原文逐字一致, 只多一个自增(不是"桩被调了几次");
 *   - 真 TDRE 位轮询 + 每字节移位时间 + 脚本化 tick => 无空间 / 部分发送 / 末字节后过期;
 *   - 真 pthread 线程持 ctx->txn 与 cfg 锁 => 预算内拒绝、未取得不归还、BUSY 不改活跃 op;
 *   - 真 IFCNT 自增与读回比对 => 同一外层预算跨子读与 IFCNT、过期不发布成功。
 *
 * ⚠ 边界(如实登记, 不外推): 本链用**寄存器模型 + 受控时间**, 不等于目标机最坏时延、真实
 *   波形或物理安全证明; BUG-008、RISK-03/03a、物理 RISK-04-b 仍按原边界开放。没有连接
 *   设备, 没有执行真实 HAL, 没有任何 UART/STEP/电机/Flash/GPIO 动作。
 * ⚠ 短临界区在主机上 = 一把全局互斥量 + 线程本地"掩码位"(与 S2 并发链同一模型), 它复现
 *   "单核 + PRIMASK 期内不可抢占", 不复现中断响应时延, 也不模拟 NMI/HardFault。
 * ⚠ C10 的"prepare→门→提交"组合是夹具按 motor.c:mot_pwm_commit_gated 的同一顺序写的四行
 *   (被测门 sp_enter_token 与被测叶子 ns800_epwm_* 都是生产实现); 组合本身不是 motor.c,
 *   motor.c 那条组合链由 S2 并发链覆盖(那边是端口叶子)。口径在交接里同文登记。
 *
 * 组: epwm_leaf(C10*) / tmc_lock(C16*) / uart_leaf(C17*) / budget_leaf(C18*)
 * 用法: leaf.exe <group>   退出码 0=全 PASS, 1=有 FAIL, 2=前置不成立
 */
#include <rtthread.h>
#include <rtdevice.h>
#include <rthw.h>
#include <pthread.h>
#include <unistd.h>
#include <stdarg.h>

#include "app_health.h"
#include "project_board.h"
#include "stop_result.h"
#include "safety_gpio.h"
#include "safety_state.h"
#include "motor.h"
#include "tmc2209.h"
#include "stop_priority.h"
#include "drv_epwm.h"
#include "drv_uart.h"
#include "drv_epwm_leaf.h"

/* ===================================================================== */
/* 0. shim_leaf/drv_common.h 声明的观察量 + 前置声明                        */
/* ===================================================================== */
volatile uint32_t ns800_leaf_reg_writes;    /* 任何一次寄存器写(含同值写) */
volatile uint32_t ns800_leaf_tbctl_writes;  /* 其中 TBCTL 写 */
volatile uint32_t ns800_leaf_pclk1_calls;   /* prepare 取时钟的次数 */
static volatile uint32_t leaf_clock_in_window;   /* 必须恒 0: 换算不许在屏蔽窗口内 */

static UART_TypeDef  leaf_uart_regs;
static EPWM_TypeDef  leaf_epwm_regs;

static struct ns800_uart_config leaf_uart_cfg;
static struct ns800_uart        uart_obj[1];    /* 名字必须是 uart_obj: 真实叶子按它查表 */

static const struct ns800_epwm_config leaf_epwm_cfg = {
    "epwm1", &leaf_epwm_regs,
    EPWM_COUNTER_MODE_UP_DOWN,
    EPWM_CLOCK_DIVIDER_2, EPWM_HSCLOCK_DIVIDER_1
};
static struct ns800_epwm leaf_epwm_obj[1];

static rt_tick_t leaf_tick;
static void      leaf_advance(rt_tick_t ms);
static int       leaf_pad_high_now(void);
void             leaf_rx_release_due(void);
static void      leaf_pk_check(void);
static void      leaf_pk_reset(void);
static void      leaf_asm_feed(rt_uint8_t b);

/* 目标上 RCC_getPclk1Frequency() 返回值(单位 Hz)的依据见 shim_leaf/drv_common.h 末段:
 * rcc.c:163 原文 "Returns the PCLK1 (APB1) clock frequency in Hz"; 本板 board/SConscript:15
 * 的 SYSCLK_USE_PLL/SYSCLK_SOURCE_USE_HXTL + board.h:118-121(PLLM_40/PLLN_DIV1/PLLP_DIV2)
 * 与 board.h:171-175(HCLK=CPUCLK/2, APB1DIV=0) => 400000000>>1>>0 = 200000000。 */
#define LEAF_PCLK1_HZ 200000000u
static rt_uint32_t leaf_clock_force = 0xFFFFFFFFu;  /* 0xFFFFFFFF = 不注入 */
uint32_t RCC_getPclk1Frequency(void)
{
    ns800_leaf_pclk1_calls++;
    if (ns800_leaf_primask() != 0u) leaf_clock_in_window++;
    /* CP6 §2(C10i): 允许把时钟换成无效值、或高到任何分频都表达不了目标周期的值,
     * 用来评审 prepare 自己的范围检查。0xFFFFFFFF = 不注入(用原文链的 200MHz)。 */
    if (leaf_clock_force != 0xFFFFFFFFu) return leaf_clock_force;
    return LEAF_PCLK1_HZ;
}

/* ===================================================================== */
/* 1. UART TX 行模型(TDRE 位真读写, 等待环真的按它转)                       */
/* ===================================================================== */
#define LEAF_TDRE UART_STAT_TDRE_M
static int         leaf_tx_written;            /* 真正落进 DATA 的字节数 */
static int         leaf_tx_allowed = 1 << 20;  /* 注入: 允许写得出去的字节数 */
static rt_uint32_t leaf_drain_ms = 1;          /* 每字节移位时间 */
static rt_tick_t   leaf_tx_busy_until;         /* 移位器变空的时刻 */
/* 注入: "整帧被抢占到越过期限之后才发完"这一条生产注释描述的情形 —— 第 N 个字节落地
 * 的那一刻, 时钟一次性走 J ms(等价于那段时间被别的上下文占用)。真实叶子的
 * "末字节之后、返回成功之前再查同一条 start/total"(drv_uart_txleaf.inc:69) 只有在这种
 * 交错下才会被走到; 单纯靠每字节移位时间只会让循环在**下一个字节的入口**就过期, 那是
 * C17b 的部分发送, 不是同一条判据。 */
static int         leaf_tick_jump_after;       /* 0 = 关闭 */
static rt_uint32_t leaf_tick_jump_ms;
static int         leaf_tx_busy;             /* 1 = 正在移位(与 0 时刻可区分, 回绕不失效) */
static rt_uint8_t  leaf_tx_bytes_log[64];
static int         leaf_tx_log_n;

static void leaf_tx_reset(void)
{
    leaf_tx_written = 0; leaf_tx_allowed = 1 << 20; leaf_tx_busy_until = 0; leaf_tx_busy = 0;
    leaf_drain_ms = 1; leaf_tx_log_n = 0;
    leaf_tick_jump_after = 0; leaf_tick_jump_ms = 0;
    leaf_uart_regs.STAT.WORDVAL |= LEAF_TDRE;      /* 上电: 发送数据寄存器空 */
}

/* shim_leaf/drv_common.h 里 UART_writeChar 的 tap: 一次 DATA 写 = 一个字节离开本 MCU。
 * 寄存器写本身仍按 SDK 原文执行(TYPE8(&DATA.WORDVAL)=data), 这里只是把它接进行模型。 */
void ns800_leaf_uart_tx_byte(rt_uint8_t b)
{
    if (leaf_tx_log_n < (int)sizeof(leaf_tx_bytes_log))
        leaf_tx_bytes_log[leaf_tx_log_n] = b;
    leaf_tx_log_n++;
    leaf_tx_written++;
    leaf_tx_busy_until = leaf_tick + leaf_drain_ms;
    leaf_tx_busy = 1;
    /* 数据装入移位器 => TDRE 立刻落下(硬件语义), 由 leaf_advance 在移完后重新置起。
     * 写满 leaf_tx_allowed 之后不再置起, 于是生产等待环真的"等不到空位"。 */
    leaf_uart_regs.STAT.WORDVAL &= ~LEAF_TDRE;
    if (leaf_tick_jump_after != 0 && leaf_tx_written == leaf_tick_jump_after)
        leaf_advance((rt_tick_t)leaf_tick_jump_ms);
    leaf_asm_feed(b);
    leaf_pk_check();                                  /* C16b/C18d 的确定接缝 */
}

static void leaf_advance(rt_tick_t ms)
{
    int i;

    for (i = 0; i < (int)ms; ++i)
    {
        leaf_tick++;
        if (leaf_tx_busy && (rt_uint32_t)(leaf_tick - leaf_tx_busy_until) < 0x80000000u)
        {
            leaf_tx_busy = 0;
            if (leaf_tx_written < leaf_tx_allowed) leaf_uart_regs.STAT.WORDVAL |= LEAF_TDRE;
        }
        leaf_rx_release_due();                        /* 到点的应答才进 RX 队列 */
    }
}

/* ===================================================================== */
/* 2. TMC2209 假芯片 + RX 队列(受控 UART 数据)                              */
/* ===================================================================== */
/* 与 V1V2 链同一套行语义, 但触发点从"rt_device_write 被调用"移到"字节真的写进 UART
 * DATA 寄存器" —— 因为本链用的是真实等待环, 生产路径不再经过 rt_device_write。 */
static unsigned   leaf_chip[0x80];
static int        leaf_chip_enabled = 1;
static int        leaf_reply_crc_flip;          /* 注入: 应答 CRC 故意错 */
static rt_tick_t  leaf_reply_ready_at;          /* 注入: 应答何时才可见(0 = 立即) */
static rt_uint8_t leaf_rep[8];
static int        leaf_rep_pending;
static rt_uint8_t leaf_rx[1024];
static int        leaf_rxn;
static int        leaf_read_reqs, leaf_write_frames, leaf_dev_write_calls;
static int        leaf_rx_pushed_bytes;

static rt_uint8_t leaf_crc8(const rt_uint8_t *d, int n)
{
    rt_uint8_t crc = 0;
    int i, b;

    for (i = 0; i < n; ++i)
    {
        rt_uint8_t curr = d[i];

        for (b = 0; b < 8; ++b)
        {
            if ((crc >> 7) ^ (curr & 0x01)) crc = (rt_uint8_t)((crc << 1) ^ 0x07);
            else                            crc = (rt_uint8_t)(crc << 1);
            curr >>= 1;
        }
    }
    return crc;
}
static void leaf_rx_push(const rt_uint8_t *p, int n)
{
    int i;

    for (i = 0; i < n && leaf_rxn < (int)sizeof(leaf_rx); ++i) leaf_rx[leaf_rxn++] = p[i];
    leaf_rx_pushed_bytes += n;
}
void leaf_rx_release_due(void)
{
    if (!leaf_rep_pending) return;
    if (leaf_reply_ready_at != 0u && leaf_tick < leaf_reply_ready_at) return;
    leaf_rep_pending = 0;
    leaf_rx_push(leaf_rep, 8);
}

/* ---- 帧装配: 从 DATA 字节流还原 4 字节读请求 / 8 字节写帧 ---- */
static rt_uint8_t leaf_asm[9];
static int        leaf_asmn;
static int        leaf_asm_need;

static void leaf_asm_feed(rt_uint8_t b)
{
    if (leaf_asmn == 0) leaf_asm_need = 0;
    if (leaf_asmn < (int)sizeof(leaf_asm)) leaf_asm[leaf_asmn++] = b;
    if (leaf_asmn == 3)
        leaf_asm_need = ((leaf_asm[2] & 0x80u) != 0u) ? 8 : 4;
    if (leaf_asm_need == 0 || leaf_asmn < leaf_asm_need) return;

    {
        rt_uint8_t reg = (rt_uint8_t)(leaf_asm[2] & 0x7Fu);
        int is_write = ((leaf_asm[2] & 0x80u) != 0u);

        leaf_asmn = 0; leaf_asm_need = 0;
        if (is_write)
        {
            unsigned v;

            if (leaf_asm[7] != leaf_crc8(leaf_asm, 7)) return;   /* 自身 CRC 错: 芯片不接受 */
            leaf_write_frames++;
            if (!leaf_chip_enabled) return;
            v = ((unsigned)leaf_asm[3] << 24) | ((unsigned)leaf_asm[4] << 16) |
                ((unsigned)leaf_asm[5] << 8) | (unsigned)leaf_asm[6];
            leaf_chip[reg] = v;
            leaf_chip[0x02] = (leaf_chip[0x02] + 1u) & 0xFFu;    /* IFCNT 只对有效 WRITE 自增 */
            return;
        }
        leaf_read_reqs++;
        if (leaf_asm[3] != leaf_crc8(leaf_asm, 3)) return;
        if (!leaf_chip_enabled) return;                            /* 失联: 不回应答 */
        {
            unsigned val = leaf_chip[reg];
            rt_uint8_t rep[8];
            unsigned k;

            rep[0] = 0x05u; rep[1] = 0xFFu; rep[2] = reg;
            rep[3] = (rt_uint8_t)(val >> 24); rep[4] = (rt_uint8_t)(val >> 16);
            rep[5] = (rt_uint8_t)(val >> 8);  rep[6] = (rt_uint8_t)val;
            rep[7] = leaf_crc8(rep, 7);
            if (leaf_reply_crc_flip) rep[7] = (rt_uint8_t)(rep[7] ^ 0xA5u);
            leaf_rep_pending = 1;
            for (k = 0; k < 8u; ++k) leaf_rep[k] = rep[k];
            leaf_rx_release_due();
        }
    }
}
static void leaf_chip_boot(void)
{
    memset(leaf_chip, 0, sizeof(leaf_chip));
    leaf_chip[0x00] = 0x00000101u;      /* GCONF   复位默认(bit0/bit8), bit1 内部 Rsense=0 */
    leaf_chip[0x01] = 0x00000001u;      /* GSTAT   reset=1 */
    leaf_chip[0x02] = 0x00000000u;      /* IFCNT   还没收到任何写 */
    leaf_chip[0x06] = 0x21000040u;      /* IOIN    VERSION */
    leaf_chip[0x6C] = 0x15010053u;      /* CHOPCONF 上电基线(TOFF!=0, vsense=0) */
    leaf_rxn = 0; leaf_asmn = 0; leaf_asm_need = 0;
    leaf_rep_pending = 0; leaf_reply_ready_at = 0; leaf_reply_crc_flip = 0;
    leaf_read_reqs = 0; leaf_write_frames = 0; leaf_dev_write_calls = 0;
    leaf_rx_pushed_bytes = 0;
    leaf_chip_enabled = 1;
}

/* ===================================================================== */
/* 3. 设备层(真实签名的句柄, 让叶子的设备查表真的命中)                       */
/* ===================================================================== */
static int leaf_dev_present = 1;
static int leaf_dev_open_calls, leaf_dev_ctrl_calls, leaf_dev_read_calls;
static int leaf_rx_drip;                  /* 注入: 线路持续吐字节(每次读推进 1ms) */

rt_device_t rt_device_find(const char *name)
{
    if (!leaf_dev_present) return RT_NULL;
    if (rt_strcmp(name, TMC_UART_DEVICE_NAME) == 0) return &uart_obj[0].serial.parent;
    if (rt_strcmp(name, "epwm1") == 0)
        return (rt_device_t)&leaf_epwm_obj[0].epwm_device;
    return RT_NULL;
}
rt_err_t rt_device_control(rt_device_t dev, int cmd, void *arg)
{ (void)dev; (void)cmd; (void)arg; leaf_dev_ctrl_calls++; return RT_EOK; }
rt_err_t rt_device_open(rt_device_t dev, rt_uint16_t oflag)
{ (void)oflag; leaf_dev_open_calls++; return (dev == RT_NULL) ? -RT_ERROR : RT_EOK; }
rt_err_t rt_device_close(rt_device_t dev) { (void)dev; return RT_EOK; }
rt_size_t rt_device_read(rt_device_t dev, rt_off_t pos, void *buffer, rt_size_t size)
{
    rt_uint8_t *out = (rt_uint8_t *)buffer;
    rt_size_t n = 0;

    (void)dev; (void)pos;
    leaf_dev_read_calls++;
    if (leaf_rx_drip && size > 0)
    {
        leaf_advance(1);                   /* 对端一直在吐: 读一次就过去 1ms */
        out[0] = 0x55u;
        return 1;
    }
    while (n < size && leaf_rxn > 0)
    {
        out[n++] = leaf_rx[0];
        memmove(leaf_rx, leaf_rx + 1, (size_t)--leaf_rxn);
    }
    return n;
}
/* 生产 TMC 整帧写走真实叶子, **不**经过 rt_device_write。这里保留实现只为把它变成一个
 * 可判定的负判据: 一旦有代码绕过预算直接写设备(或反向删掉预算后回落到旧写法), 本链计数
 * 就会非 0。 */
rt_size_t rt_device_write(rt_device_t dev, rt_off_t pos, const void *buffer, rt_size_t size)
{
    (void)dev; (void)pos; (void)buffer;
    leaf_dev_write_calls++;
    return size;
}

/* ===================================================================== */
/* 4. 上下文 / 窗口 / 打印 / 线程 / 锁                                     */
/* ===================================================================== */
static __thread volatile int leaf_masked;
static __thread int          leaf_depth;
static pthread_mutex_t       leaf_atomic = PTHREAD_MUTEX_INITIALIZER;
static int                   leaf_win_viol;
static int                   leaf_nest;                 /* ISR 上下文注入量 */
static int                   leaf_log_total;
static int                   leaf_log_while_high;      /* 许可仍 HIGH 时的打印次数 */
static int                   leaf_log_in_isr;

void ps_win_note(const char *what) { if (leaf_masked) { leaf_win_viol++; (void)what; } }
int  leaf_win_masked(void) { return leaf_masked; }
int  leaf_win_violations(void) { return leaf_win_viol; }

rt_base_t rt_hw_interrupt_disable(void)
{
    rt_base_t old = (rt_base_t)leaf_masked;

    if (!leaf_depth) pthread_mutex_lock(&leaf_atomic);
    leaf_depth++;
    leaf_masked = 1;
    return old;
}
void rt_hw_interrupt_enable(rt_base_t level)
{
    leaf_masked = (int)level;
    leaf_depth--;
    if (!leaf_depth) pthread_mutex_unlock(&leaf_atomic);
}
uint32_t ns800_leaf_primask(void) { return leaf_masked ? 1u : 0u; }
rt_bool_t rt_interrupt_get_nest(void) { return leaf_nest ? RT_TRUE : RT_FALSE; }
rt_thread_t rt_thread_self(void) { return leaf_masked ? RT_NULL : (rt_thread_t)(void *)0x1; }
rt_tick_t rt_tick_get(void) { return leaf_tick; }
rt_tick_t rt_tick_from_millisecond(rt_int32_t ms) { return (rt_tick_t)ms; }
void rt_thread_mdelay(rt_int32_t ms) { ps_win_note("rt_thread_mdelay"); leaf_advance((rt_tick_t)ms); }

/* 打印: 照常输出, 并记录"这一次打印时许可是否仍 HIGH / 是否在 ISR 或屏蔽窗口里"。
 * C16/C17 的"先保护后日志"判据用的就是 leaf_log_while_high(与审查探针附录 C 同一判据)。 */
static int leaf_kprintf_wrap(const char *fmt, ...)
{
    va_list ap;

    leaf_log_total++;
    if (leaf_pad_high_now()) leaf_log_while_high++;
    if (leaf_nest || leaf_masked) leaf_log_in_isr++;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    return 0;
}
#undef rt_kprintf
#define rt_kprintf leaf_kprintf_wrap

/* ---- 真 pthread 递归互斥量(struct rt_mutex 按地址登记, 与 S2 并发链同一做法) ---- */
#define LEAF_MTX_MAX 32
static pthread_mutex_t  leaf_mtx_obj[LEAF_MTX_MAX];
static struct rt_mutex *leaf_mtx_key[LEAF_MTX_MAX];
static int              leaf_mtx_n;
static pthread_mutex_t  leaf_mtx_tab = PTHREAD_MUTEX_INITIALIZER;

static pthread_mutex_t *leaf_mtx_find(struct rt_mutex *m, int create)
{
    int i;
    pthread_mutex_t *out = RT_NULL;

    pthread_mutex_lock(&leaf_mtx_tab);
    for (i = 0; i < leaf_mtx_n; ++i)
        if (leaf_mtx_key[i] == m) { out = &leaf_mtx_obj[i]; break; }
    if (out == RT_NULL && create != 0 && leaf_mtx_n < LEAF_MTX_MAX)
    {
        pthread_mutexattr_t at;

        pthread_mutexattr_init(&at);
        pthread_mutexattr_settype(&at, PTHREAD_MUTEX_RECURSIVE);
        pthread_mutex_init(&leaf_mtx_obj[leaf_mtx_n], &at);
        pthread_mutexattr_destroy(&at);
        leaf_mtx_key[leaf_mtx_n] = m;
        out = &leaf_mtx_obj[leaf_mtx_n];
        leaf_mtx_n++;
    }
    pthread_mutex_unlock(&leaf_mtx_tab);
    return out;
}
rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags)
{
    (void)flags;
    if (m == RT_NULL) return -RT_EINVAL;
    if (leaf_mtx_find(m, 1) == RT_NULL) return -RT_EIO;
    m->name = name; m->inited = RT_TRUE;
    return RT_EOK;
}
/* CP6 §3.2: 三种等待形态必须可区分。上一轮把**任何正数 timeout**都变成无限阻塞
 * (pthread_mutex_lock), 于是生产侧 tmc_cfg_lock_take / tmc_xfer_take 的 10/20ms 上限
 * 在主机上根本没被验证。现在:
 *   RT_WAITING_NO       -> 一次 trylock;
 *   RT_WAITING_FOREVER  -> 真阻塞(死锁由 runner 的墙钟守卫兜住);
 *   有限 timeout        -> 每秒毫秒级 trylock 轮询: 每轮真实睡 ~1ms **并把脚本时钟推进 1
 *                         tick**(两者一一对应, 不存在"脚本时钟被别人续期"), 走完即失败。
 * 请求值/结果/实际取得与归还次数全部记账, 用例据此判"是否真的在有界期限内失败"。 */
static int leaf_mtx_takes, leaf_mtx_taken, leaf_mtx_timeouts, leaf_mtx_releases;
static int leaf_mtx_last_wait;
static rt_int32_t leaf_mtx_last_request;

rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout)
{
    pthread_mutex_t *p;
    int e;
    rt_int32_t waited = 0;

    if (m == RT_NULL || !m->inited) return -RT_EINVAL;
    ps_win_note("rt_mutex_take");                  /* 窗口内取锁 = 违规, 计数 */
    p = leaf_mtx_find(m, 1);
    if (p == RT_NULL) return -RT_EIO;

    leaf_mtx_takes++;
    leaf_mtx_last_request = timeout;
    leaf_mtx_last_wait = (int)timeout;

    if (timeout == RT_WAITING_NO)
    {
        e = pthread_mutex_trylock(p);
        if (e) return -RT_EBUSY;
        leaf_mtx_taken++;
        return RT_EOK;
    }
    if (timeout < 0)                                /* RT_WAITING_FOREVER: 真阻塞 */
    {
        e = pthread_mutex_lock(p);
        if (e) return -RT_EBUSY;
        leaf_mtx_taken++;
        return RT_EOK;
    }
    for (;;)
    {
        if (pthread_mutex_trylock(p) == 0)
        {
            leaf_mtx_taken++;
            return RT_EOK;
        }
        if (waited >= (int)timeout)
        {
            leaf_mtx_timeouts++;                    /* 有界等待**到期**, 不是无限阻塞 */
            return -RT_ETIMEOUT;
        }
        usleep(1000);                               /* 墙钟 1ms */
        leaf_advance(1);                            /* 脚本时钟同一毫秒 */
        waited++;
    }
}
rt_err_t rt_mutex_release(struct rt_mutex *m)
{
    pthread_mutex_t *p;

    if (m == RT_NULL) return -RT_EINVAL;
    p = leaf_mtx_find(m, 0);
    if (p == RT_NULL) return -RT_EINVAL;           /* 没登记过的锁 = 没取得, 不许归还 */
    if (pthread_mutex_unlock(p)) return -RT_EINVAL;
    leaf_mtx_releases++;
    return RT_EOK;
}
rt_err_t rt_mutex_detach(struct rt_mutex *m) { (void)m; return RT_EOK; }

/* ---- 信号量: 只用"先 release 后 take"的计数语义(tmc2209.c 的锁交接测试钩子) ---- */
struct leaf_sem { pthread_mutex_t m; unsigned v; };
static struct leaf_sem leaf_sems[4];
static int             leaf_sem_m_n;
static pthread_mutex_t leaf_sem_tab = PTHREAD_MUTEX_INITIALIZER;

rt_sem_t rt_sem_create(const char *name, rt_uint32_t value, rt_uint8_t flag)
{
    struct leaf_sem *s;

    (void)name; (void)flag;
    pthread_mutex_lock(&leaf_sem_tab);
    if (leaf_sem_m_n >= (int)(sizeof(leaf_sems) / sizeof(leaf_sems[0])))
    { pthread_mutex_unlock(&leaf_sem_tab); return RT_NULL; }
    s = &leaf_sems[leaf_sem_m_n++];
    pthread_mutex_init(&s->m, NULL);
    s->v = value;
    pthread_mutex_unlock(&leaf_sem_tab);
    return (rt_sem_t)s;
}
rt_err_t rt_sem_release(rt_sem_t sem)
{
    struct leaf_sem *s = (struct leaf_sem *)sem;

    pthread_mutex_lock(&s->m);
    s->v++;
    pthread_mutex_unlock(&s->m);
    return RT_EOK;
}
/* take 在计数为 0 时**轮询等待**(墙钟只防死锁): 本链的两个线程接缝另有显式会合,
 * 这里不让生产代码的测试钩子挂在半路上把整个夹具变成第二套调度模型。 */
rt_err_t rt_sem_take(rt_sem_t sem, rt_int32_t timeout)
{
    struct leaf_sem *s = (struct leaf_sem *)sem;
    int waited = 0;

    (void)timeout;
    for (;;)
    {
        unsigned v;

        pthread_mutex_lock(&s->m);
        v = s->v;
        if (v > 0u) s->v = v - 1u;
        pthread_mutex_unlock(&s->m);
        if (v > 0u) return RT_EOK;
        usleep(1000);
        waited++;
        if (waited > 5000) return -RT_ETIMEOUT;     /* 5s 墙钟守卫, 命中即前置不成立 */
    }
}
rt_err_t rt_sem_delete(rt_sem_t sem) { (void)sem; return RT_EOK; }

/* ---- 真线程 ---- */
struct leaf_thr_call { void (*fn)(void *); void *arg; };
static struct leaf_thr_call leaf_thr_slot[4];
static pthread_t            leaf_thr[4];
static int                  leaf_thr_n;

static void *leaf_thr_entry(void *p)
{
    struct leaf_thr_call *c = (struct leaf_thr_call *)p;

    c->fn(c->arg);
    return RT_NULL;
}
static int leaf_spawn(void (*fn)(void *), void *arg)
{
    if (leaf_thr_n >= (int)(sizeof(leaf_thr_slot) / sizeof(leaf_thr_slot[0]))) return -1;
    leaf_thr_slot[leaf_thr_n].fn  = fn;
    leaf_thr_slot[leaf_thr_n].arg = arg;
    if (pthread_create(&leaf_thr[leaf_thr_n], RT_NULL, leaf_thr_entry,
                       &leaf_thr_slot[leaf_thr_n]) != 0) return -1;
    leaf_thr_n++;
    return 0;
}
static void leaf_join_all(void)
{
    int i;

    for (i = 0; i < leaf_thr_n; ++i) pthread_join(leaf_thr[i], RT_NULL);
    leaf_thr_n = 0;
}
rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack, rt_uint8_t prio, rt_uint32_t tick)
{
    (void)name; (void)stack; (void)prio; (void)tick;
    if (leaf_spawn(entry, param) != 0) return RT_NULL;
    return (rt_thread_t)(void *)0x2;                 /* 非空 = 真起了(见 join_all) */
}
rt_err_t rt_thread_startup(rt_thread_t t) { (void)t; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t)  { (void)t; return RT_EOK; }

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag)
{ (void)flag; if (e) { e->name = name; e->inited = RT_TRUE; } return RT_EOK; }
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t ev) { (void)e; (void)ev; return RT_EOK; }
rt_err_t rt_event_recv(struct rt_event *e, rt_uint32_t ev, rt_uint32_t fl,
                       rt_int32_t to, rt_uint32_t *out)
{ (void)e; (void)ev; (void)fl; (void)to; if (out) *out = 0; return -RT_ETIMEOUT; }
rt_err_t rt_event_detach(struct rt_event *e) { (void)e; return RT_EOK; }

/* ===================================================================== */
/* 5. 应用层端口(本链不编 motor.c/safety_state.c: 那些状态是**输入**)        */
/* ===================================================================== */
static int          leaf_pad_low = 1;
static int          leaf_cuts, leaf_lows, leaf_highs;
static rt_err_t     leaf_low_rc = RT_EOK;
static rt_err_t     leaf_cut_rc = RT_EOK;
static safety_fb_verdict_t leaf_fb = SAFETY_FB_OK;
static safety_state_t      leaf_ss = SAFETY_READY;
static rt_bool_t           leaf_motor_idle = RT_TRUE;
static int                 leaf_trip_active;
static rt_uint32_t         leaf_fault = FAULT_NONE;

static int leaf_pad_high_now(void) { return leaf_pad_low ? 0 : 1; }

rt_base_t safety_drv_permit_pin(void) { return 23; }
rt_err_t safety_drv_permit_low_raw_held(void)
{
    ps_win_note("permit_low");
    leaf_lows++;
    if (leaf_low_rc == RT_EOK) leaf_pad_low = 1;
    return leaf_low_rc;
}
rt_err_t safety_drv_permit_high_raw_held(void)
{
    ps_win_note("permit_high");
    leaf_highs++;
    leaf_pad_low = 0;
    return RT_EOK;
}
rt_bool_t safety_drv_enable_is_low(void) { return leaf_pad_low ? RT_TRUE : RT_FALSE; }
safety_fb_verdict_t safety_fb_now(rt_uint8_t want) { (void)want; return leaf_fb; }
safety_state_t safety_state_get(void) { return leaf_ss; }
rt_uint32_t  safety_fault_get(void) { return leaf_trip_active ? leaf_fault : FAULT_NONE; }
rt_bool_t safety_trip_pending(void)   { return RT_FALSE; }
rt_err_t  safety_notify_trip_done(rt_uint32_t ev, rt_uint32_t ep)
{ (void)ev; (void)ep; return RT_EOK; }

/* motor.c 不在本链: 配置写入门禁要读的电机快照由端口给(如实反映"静止、未 armed")。 */
rt_err_t motor_get_snapshot(motor_snapshot_t *snap)
{
    if (snap == RT_NULL) return -RT_EINVAL;
    memset(snap, 0, sizeof(*snap));
    snap->state = leaf_motor_idle ? MOTOR_IDLE : MOTOR_CRUISE;
    snap->armed = leaf_motor_idle ? RT_FALSE : RT_TRUE;
    return RT_EOK;
}

/* PWM: 目标上 rt_pwm_disable -> ns800_epwm_drv_disable()(drv_epwm.c:638-642)会把时间基
 * 写成 STOP_FREEZE。本链不编那条 drv 入口, 但**用同一个 SDK 内联**把同一个位写进同一个
 * 模型, 于是"冻结之后旧配置提交不得把时间基拉回运行"这条判据是真可比对的。 */
rt_err_t rt_pwm_disable(struct rt_device_pwm *p, int ch)
{
    ps_win_note("rt_pwm_disable");
    (void)ch;
    if (p == RT_NULL) return -RT_EINVAL;
    leaf_cuts++;
    if (leaf_cut_rc != RT_EOK) return leaf_cut_rc;
    EPWM_setTimeBaseCounterMode(&leaf_epwm_regs, EPWM_COUNTER_MODE_STOP_FREEZE);
    if (leaf_low_rc == RT_EOK) leaf_pad_low = 1;
    return RT_EOK;
}
rt_err_t rt_pwm_enable(struct rt_device_pwm *p, int ch)
{
    (void)ch;
    if (p == RT_NULL) return -RT_EINVAL;
    EPWM_setTimeBaseCounterMode(&leaf_epwm_regs, leaf_epwm_cfg.count_mode);
    return RT_EOK;
}
/* 通用 set 路径: 目标上是 drv_epwm.c 的 ops(它自己调 prepare + configure_regs)。
 * 本链按同一条顺序给它接上**真实叶子**, 以便观察"旧 set 是否恢复冻结的时间基"。 */
rt_err_t rt_pwm_set(struct rt_device_pwm *p, int ch, rt_uint32_t per, rt_uint32_t pul)
{
    struct ns800_epwm_prepared pr;

    if (p == RT_NULL) return -RT_EINVAL;
    if (ns800_epwm_prepare(p, (rt_uint32_t)ch, per, pul, &pr) != RT_EOK) return -RT_EINVAL;
    return (rt_err_t)ns800_epwm_commit_prepared_held(p, &pr);
}
void rt_pin_mode(rt_base_t pin, rt_base_t mode) { (void)pin; (void)mode; }
void rt_pin_write(rt_base_t pin, rt_base_t value) { (void)pin; (void)value; }
rt_base_t rt_pin_read(rt_base_t pin) { (void)pin; return leaf_pad_low ? PIN_LOW : PIN_HIGH; }
rt_base_t rt_pin_get(const char *name) { (void)name; return 23; }

/* ===================================================================== */
/* 6. 真实生产叶子与本体(与目标 include 的是同一批文件)                      */
/* ===================================================================== */
#include "drv_uart_txleaf.inc"
#include "drv_epwm_leaf.inc"

#include "stop_priority.c"
#include "tmc2209.c"

/* ===================================================================== */
/* 7. 用例隔离与前置                                                      */
/* ===================================================================== */
static void leaf_state_clear(void);

static int leaf_pre(int ok, const char *what);

/* stop_priority.c / tmc2209.c 的文件静态在本 TU 内可见(与 S2 并发链同一做法)。 */
void sp_leaf_reset(void)
{
    memset(&sp, 0, sizeof(sp));
    sp_win_nest = 0;
}
static void leaf_hw_defaults(void)
{
    EPWM_setTimeBaseCounterMode(&leaf_epwm_regs, leaf_epwm_cfg.count_mode);
    ns800_leaf_reg_writes = 0; ns800_leaf_tbctl_writes = 0;
}
static void leaf_state_clear(void)
{
    leaf_join_all();
    sp_leaf_reset();
    leaf_tick = 100u;
    ns800_leaf_reg_writes = 0; ns800_leaf_tbctl_writes = 0; ns800_leaf_pclk1_calls = 0;
    leaf_clock_in_window = 0;
    leaf_masked = 0; leaf_depth = 0; leaf_win_viol = 0; leaf_nest = 0;
    leaf_log_total = 0; leaf_log_while_high = 0; leaf_log_in_isr = 0;
    leaf_cuts = 0; leaf_lows = 0; leaf_highs = 0;
    leaf_low_rc = RT_EOK; leaf_cut_rc = RT_EOK;
    leaf_fb = SAFETY_FB_OK; leaf_ss = SAFETY_READY;
    leaf_trip_active = 0; leaf_fault = FAULT_NONE; leaf_motor_idle = RT_TRUE;
    leaf_pad_low = 1;
    leaf_dev_present = 1;
    leaf_dev_open_calls = 0; leaf_dev_ctrl_calls = 0; leaf_dev_read_calls = 0;
    leaf_mtx_takes = 0; leaf_mtx_taken = 0; leaf_mtx_timeouts = 0; leaf_mtx_releases = 0;
    leaf_mtx_last_wait = 0; leaf_mtx_last_request = 0;
    leaf_clock_force = 0xFFFFFFFFu;
    leaf_rx_drip = 0;
    memset(&leaf_epwm_regs, 0, sizeof(leaf_epwm_regs));
    memset(&leaf_uart_regs, 0, sizeof(leaf_uart_regs));
    leaf_tx_reset();
    leaf_chip_boot();
    leaf_pk_reset();
    /* 用例隔离: 把**生产执行器实例**的"上一次结论"带回未配置态(等价上电)。这里只动
     * 状态字段, 不重建锁、不改算法 —— 与 S2 并发链直接清 sp 的做法同一口径。 */
    tmc_opened = RT_FALSE;
    tmc_serial = RT_NULL;
    tmc_health = SUBSYS_UNINIT;
    tmc_cfg_prod.state = TMC_DRV_UNCONFIGURED;
    tmc_cfg_prod.done_valid = RT_FALSE;
    tmc_cfg_prod.view_force_invalid = 1u;
    tmc_cfg_prod.view.state = (rt_uint8_t)TMC_DRV_UNCONFIGURED;
    tmc_cfg_prod.view.done_valid = 0u;
    tmc_cfg_prod.view.invalid = 1u;
    tmc_cfg_prod.op_epoch = 0u;
    tmc_cfg_prod.op_protect = (rt_uint8_t)SP_PROTECT_NONE;
    leaf_hw_defaults();
    if (tmc2209_init() != RT_EOK) leaf_pre(0, "tmc2209_init 在用例前置里失败");
}

/* 打开串口 + 建立执行器实例(全走生产入口), 让写入门禁的运行时判据可达 */
static int leaf_bus_ready(void)
{
    if (tmc2209_init() != RT_EOK) return 0;
    return tmc2209_get_health() == SUBSYS_OK ? 1 : 0;
}

/* 一次**真实成功的**配置下发(整帧写 + IFCNT 确认 + 读回比对), 给需要"已配置"前提的用例 */
static int leaf_apply_configured(void)
{
    if (!leaf_bus_ready()) return 0;
    if (tmc2209_apply_config() != RT_EOK) return 0;
    return tmc2209_drive_config_ok_for_arm() == RT_TRUE ? 1 : 0;
}

/* ===================================================================== */
/* 8. 显式会合(C16b/C18d 的两线程接缝; 墙钟只防死锁, 不猜交错)               */
/* ===================================================================== */
static pthread_mutex_t leaf_pk_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  leaf_pk_c = PTHREAD_COND_INITIALIZER;  /* CP6 §3: 不是 mutex 初始化器 */
static int             leaf_pk_arrived;
static int             leaf_pk_go;
static int             leaf_pk_arm;

static void leaf_pk_check(void)
{
    if (!leaf_pk_arm) return;
    pthread_mutex_lock(&leaf_pk_m);
    leaf_pk_arrived = 1;
    pthread_cond_broadcast(&leaf_pk_c);
    while (!leaf_pk_go) pthread_cond_wait(&leaf_pk_c, &leaf_pk_m);
    pthread_mutex_unlock(&leaf_pk_m);
}
static void leaf_pk_reset(void)
{
    pthread_mutex_lock(&leaf_pk_m);
    leaf_pk_arrived = 0; leaf_pk_go = 0; leaf_pk_arm = 0;
    pthread_mutex_unlock(&leaf_pk_m);
}
static int leaf_pk_wait_arrived(int ms)
{
    int i;

    pthread_mutex_lock(&leaf_pk_m);
    for (i = 0; i < ms && !leaf_pk_arrived; ++i)
    {
        pthread_mutex_unlock(&leaf_pk_m);
        usleep(1000);
        pthread_mutex_lock(&leaf_pk_m);
    }
    {
        int r = leaf_pk_arrived ? 0 : -1;

        pthread_mutex_unlock(&leaf_pk_m);
        return r;
    }
}
static void leaf_pk_release(void)
{
    pthread_mutex_lock(&leaf_pk_m);
    leaf_pk_go = 1;
    pthread_cond_broadcast(&leaf_pk_c);
    pthread_mutex_unlock(&leaf_pk_m);
}

/* ===================================================================== */
/* 9. 台账                                                               */
/* ===================================================================== */
static int g_run, g_pass, g_fail, g_pre, g_skip;
static char g_buf[460];

static void report(const char *id, int ok, const char *detail)
{
    g_run++;
    if (ok) { g_pass++; printf("CASE|%s|PASS|%s\n", id, detail); }
    else    { g_fail++; printf("CASE|%s|FAIL|%s\n", id, detail); }
}
#define CHECK(id, cond, detail) report((id), (cond) ? 1 : 0, (detail))
#define SKIPV(id, why) do { g_skip++; printf("SKIP|%s|%s\n", (id), (why)); } while (0)
__attribute__((format(printf, 1, 2)))
static const char *fm(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(g_buf, sizeof(g_buf), fmt, ap);
    va_end(ap);
    return g_buf;
}
static int leaf_pre(int ok, const char *what)
{
    if (!ok) { g_pre++; printf("PRE-FAIL|%s\n", what); }
    return ok;
}
static void summary(void)
{
    printf("SUMMARY|cases=%d|pass=%d|fail=%d|pre_bad=%d|skips=%d\n",
           g_run, g_pass, g_fail, g_pre, g_skip);
    fflush(stdout);
}
static int seq_or_timeout(const char *what, int rc)
{
    if (rc != 0) { g_pre++; printf("SEQ-TIMEOUT|%s\n", what); return 0; }
    return 1;
}

/* CP6 §2: 数值判据改成**行为反推**(请求单位 + 寄存器里的分频/计数模式反算回 ns 周期),
 * 不再"用同一套公式重算一遍再比对"。分频因子表按 SDK 枚举原文(EPWMH:61-70 / :80-89)
 * 在夹具侧独立写出, 这就是独立 oracle: 它验的是"ns 请求 <-> 计数关系"成立。 */
static const rt_uint32_t leaf_clkdiv_f[8] = { 1U, 2U, 4U, 8U, 16U, 32U, 64U, 128U };
static const rt_uint32_t leaf_hsdiv_f[8]  = { 1U, 2U, 4U, 6U, 8U, 10U, 12U, 14U };

/* 由 prepared 里的枚举与整数**反推**周期(ns)与高电平占空比(%)。 */
static void leaf_roundtrip(const struct ns800_epwm_prepared *p, rt_uint64_t *period_ns,
                           rt_uint64_t *counts_per_period, rt_uint64_t *duty_pct)
{
    rt_uint64_t div = (rt_uint64_t)leaf_clkdiv_f[p->clock_div & 7u] *
                      leaf_hsdiv_f[p->hs_clock_div & 7u];
    rt_uint64_t tb = (rt_uint64_t)p->tb_period, cmp = (rt_uint64_t)p->cmp_val;
    rt_uint64_t counts, high;

    if ((rt_uint32_t)p->count_mode == (rt_uint32_t)EPWM_COUNTER_MODE_UP_DOWN)
    {
        counts = 2U * tb;                       /* 0 -> TBPRD -> 0 */
        high   = 2U * ((tb > cmp) ? (tb - cmp) : 0U);
    }
    else
    {
        counts = tb + 1U;                       /* 一个周期 = TBPRD+1 个计数 */
        high   = ((rt_uint32_t)p->count_mode == (rt_uint32_t)EPWM_COUNTER_MODE_UP)
                 ? cmp : (counts - cmp);
    }
    if (counts_per_period != RT_NULL) *counts_per_period = counts;
    *period_ns = (counts * div * 1000000000ULL + (LEAF_PCLK1_HZ / 2U)) / LEAF_PCLK1_HZ;
    *duty_pct  = (counts == 0U) ? 0U : (high * 100U) / counts;
}

/* 声明的计数舍入界限: UP_DOWN 一个周期随 TBPRD 步进 2 个计数, 其余 1 个计数。
 * 界限 = (步进计数数) * 一个计数的时间(ns), 一个计数 = div/clk 秒。 */
static rt_uint64_t leaf_round_bound_ns(const struct ns800_epwm_prepared *p)
{
    rt_uint64_t div = (rt_uint64_t)leaf_clkdiv_f[p->clock_div & 7u] *
                      leaf_hsdiv_f[p->hs_clock_div & 7u];
    rt_uint64_t step = ((rt_uint32_t)p->count_mode ==
                        (rt_uint32_t)EPWM_COUNTER_MODE_UP_DOWN) ? 2U : 1U;

    return (step * div * 1000000000ULL) / LEAF_PCLK1_HZ;
}

static rt_uint64_t leaf_abs_diff(rt_uint64_t a, rt_uint64_t b)
{
    return (a > b) ? (a - b) : (b - a);
}

/* 一次 prepare + 反推核对: 返回 1 表示 EOK/valid 且"反推周期在舍入界限内、占空比相符"。 */
static int leaf_check_roundtrip(rt_uint32_t period_ns, rt_uint32_t pulse_ns,
                                unsigned duty_pct_want, struct ns800_epwm_prepared *pr,
                                rt_uint64_t *back_out, rt_uint64_t *bound_out,
                                rt_uint64_t *counts_out)
{
    rt_uint64_t back, duty, counts, bound;
    rt_err_t e;

    memset(pr, 0, sizeof(*pr));
    e = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                     period_ns, pulse_ns, pr);
    if (e != RT_EOK || pr->valid != 1u) return 0;
    leaf_roundtrip(pr, &back, &counts, &duty);
    bound = leaf_round_bound_ns(pr);
    *back_out = back; *bound_out = bound; *counts_out = counts;
    return leaf_abs_diff(back, (rt_uint64_t)period_ns) <= bound && duty == duty_pct_want;
}

static void c10_group(void)
{
    struct ns800_epwm_prepared pr;
    sp_token_t tok;
    sp_proof_t pf;
    rt_base_t lvl;
    rt_err_t e, gate;
    uint32_t w0;
    rt_uint64_t back = 0, bound = 0, counts = 0;
    int ok_b = 0, ok_g = 0;

    /* ---- C10a prepare 不写任何寄存器, 且换算只在门外(取时钟时未屏蔽中断) ---- */
    leaf_state_clear();
    w0 = ns800_leaf_reg_writes;
    e = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                     1000000u, 500000u, &pr);
    CHECK("C10a-prepare-writes-zero-registers",
          e == RT_EOK && pr.valid == 1u && ns800_leaf_reg_writes == w0 &&
          leaf_clock_in_window == 0u && ns800_leaf_pclk1_calls >= 1u,
          fm("rc=%d valid=%u 寄存器写 %u->%u pclk_calls=%u 窗口内取时钟=%u",
             (int)e, (unsigned)pr.valid, (unsigned)w0,
             (unsigned)ns800_leaf_reg_writes, (unsigned)ns800_leaf_pclk1_calls,
             (unsigned)leaf_clock_in_window));

    /* ---- C10b 1kHz/50%: 行为判据(反推 ns 周期 + 原分频沿用), 不是"重算同一公式" ----
     * 先算语句再拼台账: CHECK 的三个实参求值顺序未规定, 若 fm() 先于核对函数求值,
     * 台账就会印出上一个用例的旧数字(上一版实测正是这样)。 */
    ok_b = leaf_check_roundtrip(1000000u, 500000u, 50u, &pr, &back, &bound, &counts);
    CHECK("C10b-period-compare-mode-match",
          ok_b == 1 && (unsigned)pr.tb_period == 50000u &&
          (unsigned)pr.cmp_val == 25000u && counts == 100000u &&
          (unsigned)pr.clock_div == (unsigned)EPWM_CLOCK_DIVIDER_2 &&
          (unsigned)pr.hs_clock_div == (unsigned)EPWM_HSCLOCK_DIVIDER_1,
          fm("tb=%u cmp=%u counts=%u 反推周期=%uns(请求 1000000, 舍入界限 +%u) duty=50%% "
             "分频沿用原配 cd=%u hs=%u",
             (unsigned)pr.tb_period, (unsigned)pr.cmp_val, (unsigned)counts,
             (unsigned)back, (unsigned)bound,
             (unsigned)pr.clock_div, (unsigned)pr.hs_clock_div));

    /* ---- C10c 有效身份提交: 寄存器真的收到 prepare 算好的整数(写序仍是生产实现) ---- */
    if (leaf_pre(sp_reserve(SP_DIAG, &tok) == RT_EOK, "C10c reserve"))
    {
        w0 = ns800_leaf_reg_writes;
        gate = sp_enter_token(&tok, &lvl);
        if (gate == RT_EOK)
        {
            e = (rt_err_t)ns800_epwm_commit_prepared_held(&leaf_epwm_obj[0].epwm_device, &pr);
            sp_leave_token(lvl);
        }
        else e = gate;
        CHECK("C10c-valid-token-commits-registers",
              gate == RT_EOK && e == RT_EOK && ns800_leaf_reg_writes > w0 &&
              (leaf_epwm_regs.TBPRD.WORDVAL & EPWM_TBPRD_TBPRD_M) == (uint32_t)pr.tb_period &&
              (leaf_epwm_regs.CMPA.WORDVAL >> EPWM_CMPA_CMPA_S) == (uint32_t)pr.cmp_val,
              fm("gate=%d commit=%d 寄存器写 %u->%u TBPRD=%08X CMPA=%08X",
                 (int)gate, (int)e, (unsigned)w0, (unsigned)ns800_leaf_reg_writes,
                 leaf_epwm_regs.TBPRD.WORDVAL, leaf_epwm_regs.CMPA.WORDVAL));
        (void)sp_release(&tok);
    }

    /* ---- C10d 冻结的时间基不被配置提交恢复(有效身份, 只改整数不改计数模式) ---- */
    leaf_state_clear();
    EPWM_setTimeBaseCounterMode(&leaf_epwm_regs, EPWM_COUNTER_MODE_STOP_FREEZE);
    memset(&pr, 0, sizeof(pr));
    if (leaf_pre((rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                              1000000u, 500000u, &pr) == RT_EOK &&
                 pr.valid == 1u, "C10d prepare") &&
        leaf_pre(sp_reserve(SP_DIAG, &tok) == RT_EOK, "C10d reserve") &&
        leaf_pre((leaf_epwm_regs.TBCTL.WORDVAL & EPWM_TBCTL_CTRMODE_M) ==
                 (uint32_t)EPWM_COUNTER_MODE_STOP_FREEZE, "C10d frozen first"))
    {
        uint32_t mode_before = leaf_epwm_regs.TBCTL.WORDVAL & EPWM_TBCTL_CTRMODE_M;
        uint32_t mode_after;

        w0 = ns800_leaf_reg_writes;
        gate = sp_enter_token(&tok, &lvl);
        if (gate == RT_EOK)
        {
            e = (rt_err_t)ns800_epwm_commit_prepared_held(&leaf_epwm_obj[0].epwm_device, &pr);
            sp_leave_token(lvl);
        }
        else e = gate;
        mode_after = leaf_epwm_regs.TBCTL.WORDVAL & EPWM_TBCTL_CTRMODE_M;
        CHECK("C10d-frozen-tbctl-not-restored-by-config-commit",
              gate == RT_EOK && e == RT_EOK && ns800_leaf_reg_writes > w0 &&
              mode_after == mode_before,
              fm("CTRMODE 提交前=%u 后=%u(必须同为 3=STOP_FREEZE) 寄存器写=%u tbctl 写=%u",
                 (unsigned)mode_before, (unsigned)mode_after,
                 (unsigned)ns800_leaf_reg_writes, (unsigned)ns800_leaf_tbctl_writes));
        (void)sp_release(&tok);
    }

    /* ---- C10e §5 核心: prepare 已算完 -> 保护发生 -> 旧 token 提交零寄存器写 ---- */
    leaf_state_clear();
    memset(&pr, 0, sizeof(pr));
    if (leaf_pre((rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                              1000000u, 500000u, &pr) == RT_EOK &&
                 pr.valid == 1u, "C10e prepare") &&
        leaf_pre(sp_reserve(SP_DIAG, &tok) == RT_EOK, "C10e reserve"))
    {
        memset(&pf, 0, sizeof(pf));
        (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
        if (leaf_pre(sp_epoch() != tok.epoch, "C10e epoch advanced by protection"))
        {
            w0 = ns800_leaf_reg_writes;
            gate = sp_enter_token(&tok, &lvl);
            if (gate == RT_EOK)
            {
                (void)ns800_epwm_commit_prepared_held(&leaf_epwm_obj[0].epwm_device, &pr);
                sp_leave_token(lvl);
            }
            CHECK("C10e-stale-token-commit-writes-zero-registers",
                  gate != RT_EOK && ns800_leaf_reg_writes == w0 && leaf_highs == 0,
                  fm("enter_token rc=%d(应非 EOK) 寄存器写 %u->%u(必须不变) HIGH=%d",
                     (int)gate, (unsigned)w0, (unsigned)ns800_leaf_reg_writes, leaf_highs));
            CHECK("C10e2-protection-itself-did-one-real-off-and-low",
                  leaf_cuts == 1 && leaf_lows == 1 && sp_inhibited(),
                  fm("cuts=%d lows=%d inhibit=%d", leaf_cuts, leaf_lows,
                     (int)sp_inhibited()));
        }
    }

    /* ---- C10f 旧 set 路径(ops 顺序: prepare + configure_regs)同样不恢复冻结计数模式 ---- */
    leaf_state_clear();
    memset(&pf, 0, sizeof(pf));
    (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);
    {
        uint32_t mode_before = leaf_epwm_regs.TBCTL.WORDVAL & EPWM_TBCTL_CTRMODE_M;
        uint32_t w_before    = ns800_leaf_reg_writes;

        (void)rt_pwm_set(&leaf_epwm_obj[0].epwm_device, 0, 1000000u, 500000u);
        CHECK("C10f-legacy-set-keeps-frozen-timebase",
              mode_before == (uint32_t)EPWM_COUNTER_MODE_STOP_FREEZE &&
              (leaf_epwm_regs.TBCTL.WORDVAL & EPWM_TBCTL_CTRMODE_M) == mode_before &&
              ns800_leaf_reg_writes > w_before,
              fm("CTRMODE=%u(应 3) 寄存器写 %u->%u",
                 (unsigned)(leaf_epwm_regs.TBCTL.WORDVAL & EPWM_TBCTL_CTRMODE_M),
                 (unsigned)w_before, (unsigned)ns800_leaf_reg_writes));
    }

    /* ---- C10g 20kHz/50%: 原分频下 TBPRD=2500, 反推 50us ---- */
    leaf_state_clear();
    ok_g = leaf_check_roundtrip(50000u, 25000u, 50u, &pr, &back, &bound, &counts);
    CHECK("C10g-20khz-roundtrip-50us",
          ok_g == 1 && (unsigned)pr.tb_period == 2500u && (unsigned)pr.cmp_val == 1250u &&
          counts == 5000u,
          fm("tb=%u(应 2500) cmp=%u(应 1250) counts=%u 反推=%uns(请求 50000, 界限 +%u)",
             (unsigned)pr.tb_period, (unsigned)pr.cmp_val, (unsigned)counts,
             (unsigned)back, (unsigned)bound));

    /* ---- C10h 低频(20Hz 斜坡首拍 / 1Hz): 必须**选到合法分频**而不是范围失败 ---- */
    leaf_state_clear();
    {
        int ok20, ok1;
        rt_uint32_t tb20, cd20, hd20, tb1, cd1, hd1;
        rt_uint64_t back1 = 0, bound1 = 0, counts1 = 0;

        ok20 = leaf_check_roundtrip(50000000u, 25000000u, 50u, &pr,
                                    &back, &bound, &counts);
        tb20 = (rt_uint32_t)pr.tb_period; cd20 = (rt_uint32_t)pr.clock_div;
        hd20 = (rt_uint32_t)pr.hs_clock_div;
        ok1 = leaf_check_roundtrip(1000000000u, 500000000u, 50u, &pr,
                                   &back1, &bound1, &counts1);
        tb1 = (rt_uint32_t)pr.tb_period; cd1 = (rt_uint32_t)pr.clock_div;
        hd1 = (rt_uint32_t)pr.hs_clock_div;
        /* 恒真判据(uint16 <= 65535)已去掉: 可表达性由"反推周期在舍入界限内 + "
         * "分频确实偏离原配"承担。 */
        CHECK("C10h-low-frequency-dividers-selected",
              ok20 == 1 && counts >= 4u && tb20 >= 2u &&
              (cd20 != (unsigned)EPWM_CLOCK_DIVIDER_2 ||
               hd20 != (unsigned)EPWM_HSCLOCK_DIVIDER_1) &&
              ok1 == 1 && counts1 >= 4u && tb1 >= 2u,
              fm("20Hz: tb=%u cd=%u hs=%u 反推=%uns(界限 +%u) | "
                 "1Hz: tb=%u cd=%u hs=%u 反推=%uns(界限 +%u) 误差=%uns",
                 tb20, cd20, hd20, (unsigned)back, (unsigned)bound,
                 tb1, cd1, hd1,
                 (unsigned)back1, (unsigned)bound1,
                 (unsigned)leaf_abs_diff(back1, 1000000000ULL)));
    }

    /* ---- C10i 非法/不可表达输入: prepare 失败、valid=0、一个寄存器都不写 ---- */
    leaf_state_clear();
    {
        rt_err_t e_zero, e_pulse, e_mode, e_range, e_clock0, e_hard;
        uint32_t w_all;

        memset(&pr, 0xAA, sizeof(pr));
        w_all = ns800_leaf_reg_writes;
        e_zero  = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u, 0u, 0u, &pr);
        e_pulse = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                               1000000u, 1000001u, &pr);
        e_range = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                               2000000000u, 1000000000u, &pr);
        /* 非法计数模式: 另建一份配置为 STOP_FREEZE 的驱动对象(目标上属于配置错误) */
        {
            static struct ns800_epwm bad_obj[1];
            static struct ns800_epwm_config bad_cfg;

            bad_cfg = leaf_epwm_cfg;
            bad_cfg.count_mode = EPWM_COUNTER_MODE_STOP_FREEZE;
            bad_obj[0] = leaf_epwm_obj[0];
            bad_obj[0].epwm_config = &bad_cfg;
            e_mode = (rt_err_t)ns800_epwm_prepare(&bad_obj[0].epwm_device, 0u,
                                                  1000000u, 500000u, &pr);
        }
        /* 时钟无效(0)与"任何分频都表达不了"的高时钟: 由脚本化时钟注入 */
        leaf_clock_force = 0u;
        e_clock0 = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                                1000000u, 500000u, &pr);
        leaf_clock_force = 4000000000u;      /* 4GHz: 最大分频下也只能表达约 55us */
        e_hard = (rt_err_t)ns800_epwm_prepare(&leaf_epwm_obj[0].epwm_device, 0u,
                                              100000000u, 50000000u, &pr);
        leaf_clock_force = 0xFFFFFFFFu;      /* 关掉注入, 回到默认 200MHz */

        CHECK("C10i-invalid-or-inexpressible-zero-writes",
              e_zero != RT_EOK && e_pulse != RT_EOK && e_range != RT_EOK &&
              e_mode != RT_EOK && e_clock0 != RT_EOK && e_hard != RT_EOK &&
              pr.valid == 0u && ns800_leaf_reg_writes == w_all,
              fm("zero=%d pulse>period=%d range=%d mode=%d clk0=%d inexpressible=%d "
                 "valid=%u 寄存器写 %u->%u",
                 (int)e_zero, (int)e_pulse, (int)e_range, (int)e_mode, (int)e_clock0,
                 (int)e_hard, (unsigned)pr.valid, (unsigned)w_all,
                 (unsigned)ns800_leaf_reg_writes));
    }
}

/* ===================================================================== */
/* 11. C16 —— 真 cfg/xfer 锁被另一条线程持有                                */
/* ===================================================================== */
static void c16_apply_thread(void *arg)
{
    (void)arg;
    (void)tmc2209_apply_config();
}

/* 竞争调用必须由**另一条线程**发出: 本链的 rt_mutex 是递归的, 同一线程再 trylock 一定成功,
 * 那样"锁是否仍在持有者手上"就不可判定(本机实测: 第三方 trylock 返回 EOK 导致假失败)。 */
static volatile int         leaf_c16_busy_rc;
static tmc_op_result_t      leaf_c16_busy_out;
static volatile int         leaf_c16_trylock_rc;

static void c16_contender(void *arg)
{
    (void)arg;
    leaf_c16_busy_out.rc = 999;
    leaf_c16_busy_rc = tmc2209_config_integrity_check_for(0u, &leaf_c16_busy_out);
}
static void c16_probe(void *arg)
{
    (void)arg;
    leaf_c16_trylock_rc = rt_mutex_take(&tmc_cfg_prod.txn, RT_WAITING_NO);
    if (leaf_c16_trylock_rc == RT_EOK) (void)rt_mutex_release(&tmc_cfg_prod.txn);
}

/* ---- CP6 §3: 真实 cfg / xfer **叶锁**被另一条线程持有(有界失败, 不是 txn 竞争) ----
 *
 * 上一轮 C16a/b 争的是最外层 ctx->txn(而且是 RT_WAITING_NO), 加上夹具把任何正数 timeout
 * 都写成无限阻塞 => 生产侧 tmc_cfg_lock_take(<=10ms) / tmc_xfer_take(<=20ms) 的**有界**
 * 失败从未被观察。现在(全部走**生产**取锁/归还函数, 没有桩直接回 EBUSY):
 *   C16d: 另一条线程用 tmc_cfg_lock_take() 真持有 cfg 叶锁, 主线程从**生产复核入口**
 *         tmc2209_config_integrity_check_for() 进去 => 受理之后在 10ms 上限内到期, 并按
 *         本次身份保护一次(PWM 关断与许可 LOW 两条独立动作 + inhibit + 带回 proof/outcome),
 *         而且许可在此之前**可观察地为 HIGH**(所以"日志时已 LOW"不是初始 LOW 下的空判据);
 *         失败出口 fail_ctx 再取同一把锁也取不到 => 仍只有第一次那一个短保护、摘要作废。
 *         竞争失败者不得把持有者的锁放掉(第三线程 trylock 仍须失败, 归还后恢复可取)。
 *   C16e: 另一条线程用 tmc_xfer_take() 真持有 UART 事务叶锁, 主线程从**生产公开读**
 *         tmc2209_read_register() 进去 => 在 20ms 上限内到期, 一个字节都不发; 归还后同一
 *         入口真读成功。C16e3 再对同一把锁用"外层预算只剩 3ms"跑一次生产读事务本体
 *         tmc_read_reg(与 integrity 的 ctx->bus->read 同一入口)=> 等待值取 min(20, 3)。
 *   C16f: 同一把 cfg 锁, 把**外层预算**压到只剩 3ms(< 10ms 上限)=> 传给锁的等待值必须
 *         是 min(cap, remaining)=3; 预算为 0 时连一次取锁调用都不许发生。
 * ⚠ 时间是脚本化的: 这里证明的是同一预算与软件分支, 不是目标机最坏时延。墙钟只当死锁守卫。
 */
static pthread_mutex_t  leaf_hk_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t   leaf_hk_c = PTHREAD_COND_INITIALIZER;
static int              leaf_hk_have;      /* 持有者已拿到锁 */
static int              leaf_hk_release;   /* 主线程放行归还 */
static int              leaf_hk_err;       /* 持有者自己的取锁结果 */
static volatile int     leaf_hk_probe_done;/* 探针线程自己跑完的旗标(不 join) */
static rt_bool_t        leaf_hk_taken;

static void leaf_hk_reset(void)
{
    pthread_mutex_lock(&leaf_hk_m);
    leaf_hk_have = 0; leaf_hk_release = 0; leaf_hk_err = 0; leaf_hk_taken = RT_FALSE;
    pthread_mutex_unlock(&leaf_hk_m);
}
static void leaf_hk_signal_have(void)
{
    pthread_mutex_lock(&leaf_hk_m);
    leaf_hk_have = 1;
    pthread_cond_broadcast(&leaf_hk_c);
    pthread_mutex_unlock(&leaf_hk_m);
}
/* 会合用条件量, 墙钟上限只防死锁(不靠它推进脚本时钟) */
static int leaf_hk_wait_have(int ms)
{
    int i;

    pthread_mutex_lock(&leaf_hk_m);
    for (i = 0; i < ms && !leaf_hk_have; ++i)
    {
        pthread_mutex_unlock(&leaf_hk_m);
        usleep(1000);
        pthread_mutex_lock(&leaf_hk_m);
    }
    {
        int r = leaf_hk_have ? 0 : -1;

        pthread_mutex_unlock(&leaf_hk_m);
        return r;
    }
}
static void leaf_hk_ask_release(void)
{
    pthread_mutex_lock(&leaf_hk_m);
    leaf_hk_release = 1;
    pthread_cond_broadcast(&leaf_hk_c);
    pthread_mutex_unlock(&leaf_hk_m);
}
static void leaf_hk_wait_release(void)
{
    pthread_mutex_lock(&leaf_hk_m);
    while (!leaf_hk_release) pthread_cond_wait(&leaf_hk_c, &leaf_hk_m);
    pthread_mutex_unlock(&leaf_hk_m);
}

/* 持有者: 用**生产**取锁函数拿 cfg 叶锁, 等到放行后用生产归还函数放掉。 */
static void leaf_hold_cfg_lock(void *arg)
{
    const tmc_budget_t *b = (const tmc_budget_t *)arg;

    leaf_hk_err = (int)tmc_cfg_lock_take(&tmc_cfg_prod, b, &leaf_hk_taken);
    leaf_hk_signal_have();
    leaf_hk_wait_release();
    if (leaf_hk_taken) tmc_cfg_lock_give(&tmc_cfg_prod, leaf_hk_taken);
}
/* 持有者: 用**生产** tmc_xfer_take() 拿 UART 事务叶锁。 */
static void leaf_hold_xfer_lock(void *arg)
{
    (void)arg;
    leaf_hk_err = (int)tmc_xfer_take(RT_NULL, &leaf_hk_taken);
    leaf_hk_signal_have();
    leaf_hk_wait_release();
    if (leaf_hk_taken) tmc_xfer_give(leaf_hk_taken);
}
/* 第三方线程: 只 trylock, 用来证明"锁仍在持有者手上"以及"归还后恢复可取"。 */
static void leaf_probe_cfg_lock(void *arg)
{
    (void)arg;
    leaf_c16_trylock_rc = rt_mutex_take(&tmc_cfg_prod.lock, RT_WAITING_NO);
    if (leaf_c16_trylock_rc == RT_EOK) (void)rt_mutex_release(&tmc_cfg_prod.lock);
    leaf_hk_probe_done = 1;
}
/* 起探针线程并等它自己跑完 —— **不能** join: 此刻持有者线程还挂在"等放行"上,
 * join_all 会把它一起等死(本轮实测过一次死锁)。墙钟上限只防探针卡住。 */
static int leaf_probe_wait_done(int ms)
{
    int i;

    leaf_hk_probe_done = 0;
    if (leaf_spawn(leaf_probe_cfg_lock, RT_NULL) != 0) return -1;
    for (i = 0; i < ms && !leaf_hk_probe_done; ++i) usleep(1000);
    return leaf_hk_probe_done ? 0 : -1;
}

static void c16_leaflock_cases(void)
{
    tmc_op_result_t res;
    sp_proof_t      pp;
    rt_uint32_t ep0 = 0u, pcode = 0u, pevents = 0u;
    rt_bool_t     pend = RT_FALSE;
    rt_uint32_t v = 0;
    rt_err_t e;
    int takes0, timeouts0;

    /* ================= C16d: cfg 叶锁被另一条线程持有 ================= */
    leaf_state_clear();
    leaf_hk_reset();
    if (!leaf_pre(leaf_bus_ready(), "C16d bus ready")) return;
    if (!leaf_pre(leaf_apply_configured(), "C16d 先做一次成功 apply")) return;
    {
        sp_token_t t;

        if (!leaf_pre(sp_reserve(SP_MOTOR, &t) == RT_EOK && sp_release(&t) == RT_EOK &&
                      sp_epoch() > 0u, "C16d 真实地前进过一代")) return;
    }
    /* 前置: 许可**可观察地**为 HIGH(初始态是 LOW, 不这样置就无法区分"空判据") */
    if (!leaf_pre(safety_drv_permit_high_raw_held() == RT_EOK &&
                  leaf_pad_high_now() == 1, "C16d 前置: 观测到的许可为 HIGH")) return;
    ep0 = sp_epoch();
    if (!leaf_pre(leaf_spawn(leaf_hold_cfg_lock, RT_NULL) == 0, "C16d holder thread")) return;
    if (!seq_or_timeout("C16d holder 已取到 cfg 锁", leaf_hk_wait_have(5000)))
    { leaf_hk_ask_release(); leaf_join_all(); return; }
    if (!leaf_pre(leaf_hk_err == 0 && leaf_hk_taken == RT_TRUE, "C16d holder 确实持有 cfg 锁"))
    { leaf_hk_ask_release(); leaf_join_all(); return; }

    takes0 = leaf_mtx_takes; timeouts0 = leaf_mtx_timeouts;
    memset(&res, 0, sizeof(res));
    res.rc = 999;
    e = tmc2209_config_integrity_check_for(ep0, &res);   /* 生产复核入口 */
    pend = sp_pending_peek(&pp, &pcode, &pevents);        /* 只读不清: 整理义务应当已建立 */
    CHECK("C16d-cfg-leaf-lock-held-bounded-fail-protects",
          e != RT_EOK && res.rc == e && ep0 > 0u && res.op_epoch == ep0 &&
          leaf_mtx_takes > takes0 && leaf_mtx_timeouts > timeouts0 &&
          leaf_mtx_last_request > 0 && leaf_mtx_last_request <= (rt_int32_t)TMC_BUD_CFG_LOCK_MS &&
          leaf_cuts == 1 && leaf_lows == 1 && sp_inhibited() &&
          pend == RT_TRUE && pcode == FAULT_TMC_COMM &&
          res.protect == SP_PROTECT_BY_THIS_CALL && res.proof.valid == RT_TRUE &&
          leaf_log_while_high == 0 && leaf_pad_high_now() == 0 &&
          tmc2209_drive_config_ok_for_arm() == RT_FALSE,
          fm("integrity rc=%d 身份=%u(本次 out.op_epoch=%u) 等待请求=%dms(上限 %d) "
             "到期次数+%d cuts=%d lows=%d inhibit=%d pending=%d(code=%08X) "
             "protect=%u proof.valid=%u "
             "HIGH 期间错误打印=%d 结束时许可=%s 摘要可用=%d",
             (int)e, (unsigned)ep0, (unsigned)res.op_epoch,
             (int)leaf_mtx_last_request, (int)TMC_BUD_CFG_LOCK_MS,
             leaf_mtx_timeouts - timeouts0, leaf_cuts, leaf_lows, (int)sp_inhibited(),
             (int)pend, (unsigned)pcode,
             (unsigned)res.protect, (unsigned)res.proof.valid, leaf_log_while_high,
             leaf_pad_low ? "LOW" : "HIGH", (int)tmc2209_drive_config_ok_for_arm()));

    /* 竞争失败者不得把持有者的锁放掉: 第三线程 trylock 仍须失败 */
    if (seq_or_timeout("C16d 探针线程(持有者未放行, 不能 join)", leaf_probe_wait_done(5000)))
    {
        CHECK("C16d2-holder-still-owns-cfg-lock-after-refusal",
              leaf_c16_trylock_rc == -RT_EBUSY,
              fm("失败者返回后第三线程 trylock cfg=%d(必须 EBUSY: 锁仍在持有者手上)",
                 (int)leaf_c16_trylock_rc));
    }
    leaf_hk_ask_release();
    leaf_join_all();
    if (seq_or_timeout("C16d 探针线程 2", leaf_probe_wait_done(5000)))
    {
        CHECK("C16d3-holder-release-restores-lock",
              leaf_c16_trylock_rc == RT_EOK && leaf_cuts == 1 && leaf_lows == 1 &&
              leaf_mtx_taken == leaf_mtx_releases && leaf_mtx_taken >= 2,
              fm("持有者归还后 trylock cfg=%d(应 EOK), 全程关断=%d 次、LOW=%d 次"
                 "(失败出口再取锁也没造成第二次关闭); 真实取得=%d 次 / 真实归还=%d 次",
                 (int)leaf_c16_trylock_rc, leaf_cuts, leaf_lows,
                 leaf_mtx_taken, leaf_mtx_releases));
    }
    leaf_join_all();

    /* ================= C16e: xfer 叶锁被另一条线程持有 ================= */
    leaf_state_clear();
    leaf_hk_reset();
    if (!leaf_pre(leaf_bus_ready(), "C16e bus ready")) return;
    {
        rt_uint32_t v0 = 0;

        /* 前置: 先有一次**无竞争**的真读成功 => 叶锁由生产 tmc_uart_open() 建立,
         * 否则持有者会拿到 -RT_ENOSYS(能力缺失), 那就不是竞争。 */
        e = tmc2209_read_register(0, TMC_REG_GCONF, &v0);
        if (!leaf_pre(e == RT_EOK && tmc_lock_ok == RT_TRUE, "C16e 前置: 无竞争读成功且锁已建立"))
            return;
    }
    if (!leaf_pre(leaf_spawn(leaf_hold_xfer_lock, RT_NULL) == 0, "C16e holder")) return;
    if (!seq_or_timeout("C16e holder 已取到 xfer 锁", leaf_hk_wait_have(5000)))
    { leaf_hk_ask_release(); leaf_join_all(); return; }
    if (!leaf_pre(leaf_hk_err == 0 && leaf_hk_taken == RT_TRUE, "C16e holder 确实持有 xfer 锁"))
    { leaf_hk_ask_release(); leaf_join_all(); return; }
    {
        int t0 = leaf_mtx_takes, f0 = leaf_write_frames, rr0 = leaf_read_reqs;

        e = tmc2209_read_register(0, TMC_REG_GCONF, &v);        /* 生产公开读 */
        CHECK("C16e-xfer-lock-held-bounded-fail-no-frame",
              e == -RT_EBUSY && leaf_mtx_takes > t0 && leaf_mtx_timeouts > 0 &&
              leaf_mtx_last_request > 0 &&
              leaf_mtx_last_request <= (rt_int32_t)TMC_BUD_XFER_LOCK_MS &&
              leaf_write_frames == f0 && leaf_read_reqs == rr0,
              fm("read rc=%d 等待请求=%dms(上限 %d) 到期=%d 写帧=%d 读请求=%d value=%08X",
                 (int)e, (int)leaf_mtx_last_request, (int)TMC_BUD_XFER_LOCK_MS,
                 leaf_mtx_timeouts, leaf_write_frames, leaf_read_reqs, (unsigned)v));
    }
    leaf_hk_ask_release();
    leaf_join_all();
    {
        int rr1 = leaf_read_reqs;
        rt_uint32_t v2 = 0;

        e = tmc2209_read_register(0, TMC_REG_GCONF, &v2);
        CHECK("C16e2-xfer-lock-released-then-read-works",
              e == RT_EOK && leaf_read_reqs == rr1 + 1 && v2 == 0x00000101u,
              fm("归还后再读 rc=%d 读请求=%d(前=%d) value=%08X",
                 (int)e, leaf_read_reqs, rr1, (unsigned)v2));
    }

    /* C16e3: 同一把 xfer 叶锁, 但**外层预算**只剩 3ms(< 20ms 上限)=> 传给锁的等待值
     * 必须是 min(20, 3); 到期后一个字节都不许发, 出参 value 也不许被改写。
     * 走的是生产读事务本体 tmc_read_reg(与 integrity 的 ctx->bus->read 同一个入口)。 */
    leaf_hk_reset();
    if (!leaf_pre(leaf_spawn(leaf_hold_xfer_lock, RT_NULL) == 0, "C16e3 holder")) return;
    if (!seq_or_timeout("C16e3 holder 已取到 xfer 锁", leaf_hk_wait_have(5000)))
    { leaf_hk_ask_release(); leaf_join_all(); return; }
    if (!leaf_pre(leaf_hk_err == 0 && leaf_hk_taken == RT_TRUE, "C16e3 holder 确实持有 xfer 锁"))
    { leaf_hk_ask_release(); leaf_join_all(); return; }
    {
        tmc_budget_t    tight_x;
        tmc_xfer_stat_t xs;
        rt_uint32_t     v3 = 0xA5A5A5A5u;
        int             f0 = leaf_write_frames, rr0 = leaf_read_reqs, t0 = leaf_mtx_takes;

        (void)tmc_budget_open(&tight_x, 4u);
        leaf_advance(1u);                       /* 用掉 1ms => 剩 3ms < 20ms 上限 */
        memset(&xs, 0, sizeof(xs));
        e = tmc_read_reg(0, TMC_REG_GCONF, &v3, &tight_x, &xs);
        CHECK("C16e3-xfer-wait-is-min-of-cap-and-remaining",
              e == -RT_EBUSY && xs.reason == TMC_XFER_LOCK_BUSY &&
              leaf_mtx_takes > t0 && leaf_mtx_last_request == 3 && v3 == 0xA5A5A5A5u &&
              leaf_write_frames == f0 && leaf_read_reqs == rr0,
              fm("读事务 rc=%d reason=%d 传给锁的等待值=%dms(必须 =min(上限 20, 剩余 3)) "
                 "value=%08X(必须没被改写) 写帧=%d 读请求=%d",
                 (int)e, (int)xs.reason, (int)leaf_mtx_last_request, (unsigned)v3,
                 leaf_write_frames, leaf_read_reqs));
    }
    leaf_hk_ask_release();
    leaf_join_all();

    /* ================= C16f: min(cap, 剩余预算) 与预算为 0 ================= */
    leaf_state_clear();
    leaf_hk_reset();
    if (!leaf_pre(leaf_bus_ready(), "C16f bus ready")) return;
    {
        tmc_budget_t tight, dead;
        rt_bool_t tk = RT_FALSE;
        rt_err_t le2, le0;
        int takes_before;

        (void)tmc_budget_open(&tight, 4u);            /* 只剩 4ms */
        leaf_advance(1u);                             /* 再用掉 1ms => 剩 3ms(<10ms 上限) */
        if (!leaf_pre(leaf_spawn(leaf_hold_cfg_lock, &tight) == 0, "C16f holder")) return;
        if (!seq_or_timeout("C16f holder 已取到 cfg 锁", leaf_hk_wait_have(5000)))
        { leaf_hk_ask_release(); leaf_join_all(); return; }
        takes_before = leaf_mtx_takes;
        le2 = tmc_cfg_lock_take(&tmc_cfg_prod, &tight, &tk);        /* 主线程同一生产函数 */
        CHECK("C16f-wait-is-min-of-cap-and-remaining",
              le2 == -RT_EBUSY && tk == RT_FALSE && leaf_mtx_takes > takes_before &&
              leaf_mtx_last_request == 3,
              fm("叶锁 rc=%d(应 -RT_EBUSY) taken=%d 传给锁的等待值=%dms"
                 "(必须 =min(上限 10, 剩余 3))",
                 (int)le2, (int)tk, (int)leaf_mtx_last_request));
        leaf_hk_ask_release();
        leaf_join_all();

        (void)tmc_budget_open(&dead, 4u);
        leaf_advance(9u);                             /* 已过期限 => remaining = 0 */
        tk = RT_FALSE;
        takes_before = leaf_mtx_takes;
        le0 = tmc_cfg_lock_take(&tmc_cfg_prod, &dead, &tk);
        CHECK("C16f2-exhausted-budget-does-not-attempt-lock",
              le0 == -RT_ETIMEOUT && tk == RT_FALSE && leaf_mtx_takes == takes_before,
              fm("预算已尽 rc=%d(应 -RT_ETIMEOUT) taken=%d 取锁调用增量=%d(必须 0)",
                 (int)le0, (int)tk, leaf_mtx_takes - takes_before));
    }
}

static void c16_group(void)
{
    rt_uint32_t active_before, active_after, stale_epoch;
    rt_uint8_t  protect_before;
    tmc_op_result_t res;
    rt_err_t e, held;

    /* ---- C16a 另一条线程真持有事务锁 => 预算内拒绝, 不产生任何保护动作/总线流量 ---- */
    leaf_state_clear();
    if (!leaf_pre(leaf_bus_ready(), "C16a bus ready")) return;
    held = tmc2209_cfg_txn_begin();
    if (!leaf_pre(held == RT_EOK, "C16a txn held by main")) return;
    if (!leaf_pre(leaf_spawn(c16_contender, RT_NULL) == 0, "C16a contender thread"))
    { tmc2209_cfg_txn_end(); return; }
    leaf_join_all();
    e = (rt_err_t)leaf_c16_busy_rc;
    CHECK("C16a-lock-held-refuses-within-budget",
          e == -RT_EBUSY && leaf_cuts == 0 && leaf_lows == 0 && leaf_log_while_high == 0 &&
          leaf_write_frames == 0 && leaf_read_reqs == 0 &&
          leaf_c16_busy_out.protect == SP_PROTECT_NONE &&
          leaf_c16_busy_out.proof.valid == RT_FALSE &&
          leaf_c16_busy_out.rc == e && leaf_c16_busy_out.op_epoch == 0u,
          fm("rc=%d(应 -RT_EBUSY) cuts=%d lows=%d HIGH 期间打印=%d 总线帧 w=%d r=%d "
             "out{op_epoch=%u rc=%d protect=%u proof.valid=%u}",
             (int)e, leaf_cuts, leaf_lows, leaf_log_while_high, leaf_write_frames,
             leaf_read_reqs, (unsigned)leaf_c16_busy_out.op_epoch,
             (int)leaf_c16_busy_out.rc, (unsigned)leaf_c16_busy_out.protect,
             (unsigned)leaf_c16_busy_out.proof.valid));
    /* 未取得锁的那一次不许把持有者的锁放掉: 第三方线程 trylock 仍须失败 */
    if (leaf_pre(leaf_spawn(c16_probe, RT_NULL) == 0, "C16a probe thread"))
    {
        leaf_join_all();
        CHECK("C16a2-non-taker-cannot-release",
              leaf_c16_trylock_rc == -RT_EBUSY,
              fm("竞争调用返回后, 第三方 trylock txn=%d(必须 EBUSY: 锁仍在持有者手上)",
                 (int)leaf_c16_trylock_rc));
    }
    tmc2209_cfg_txn_end();
    if (leaf_pre(leaf_spawn(c16_probe, RT_NULL) == 0, "C16a probe thread 2"))
    {
        leaf_join_all();
        CHECK("C16a3-owner-release-balances",
              leaf_c16_trylock_rc == RT_EOK,
              fm("持有者归还后第三方 trylock=%d(应 EOK: 恰好一次归还)",
                 (int)leaf_c16_trylock_rc));
    }

    /* ---- C16b(§2 反例并入)真线程正在 apply, 竞争调用返回 BUSY 却不许改活跃 op ----
     * 先让协调层**真实地**前进一代(生产预约 + 生产释放), 于是"活跃 op 的身份"与
     * "竞争调用手里那个旧身份"是两个不同的数 —— 否则 0->0 的相等是空洞判据。 */
    leaf_state_clear();
    if (!leaf_pre(leaf_bus_ready(), "C16b bus ready")) return;
    {
        sp_token_t t;

        if (!leaf_pre(sp_reserve(SP_MOTOR, &t) == RT_EOK && sp_release(&t) == RT_EOK &&
                      sp_epoch() > 0u, "C16b 真实地前进过一代")) return;
        stale_epoch = t.epoch;                  /* 释放前那一代 = 竞争调用手里的旧身份 */
    }
    leaf_pk_reset();
    leaf_pk_arm = 1;
    if (!leaf_pre(leaf_spawn(c16_apply_thread, RT_NULL) == 0, "C16b apply thread")) return;
    if (!seq_or_timeout("C16b apply 进入本次操作", leaf_pk_wait_arrived(5000)))
    { leaf_pk_release(); leaf_join_all(); return; }
    active_before = tmc_cfg_prod.op_epoch;
    protect_before = (rt_uint8_t)tmc_cfg_prod.op_protect;
    memset(&res, 0, sizeof(res));
    res.rc = 999;
    e = tmc2209_config_integrity_check_for(stale_epoch, &res);   /* 旧身份的竞争调用 */
    active_after = tmc_cfg_prod.op_epoch;
    CHECK("C16b-busy-call-does-not-clobber-active-op",
          e == -RT_EBUSY && active_before != stale_epoch && active_after == active_before &&
          tmc_cfg_prod.op_protect == protect_before &&
          res.op_epoch == stale_epoch && res.protect == SP_PROTECT_NONE &&
          res.proof.valid == RT_FALSE && res.rc != 999,
          fm("busy rc=%d 活跃身份=%u(旧身份=%u) -> 现在=%u(必须不变) 本次 out: op_epoch=%u "
             "protect=%u proof.valid=%u rc=%d",
             (int)e, (unsigned)active_before, (unsigned)stale_epoch,
             (unsigned)active_after,
             (unsigned)res.op_epoch, (unsigned)res.protect,
             (unsigned)res.proof.valid, (int)res.rc));

    /* 放行 A 并把总线变哑 => A 的真实错误必须由**这一次**产生一次保护 */
    leaf_chip_enabled = 0;
    leaf_pk_release();
    leaf_join_all();
    CHECK("C16b2-real-failure-still-protects-exactly-once",
          leaf_cuts == 1 && leaf_lows == 1 && sp_inhibited() &&
          tmc_cfg_prod.op_protect == (rt_uint8_t)SP_PROTECT_BY_THIS_CALL &&
          leaf_log_while_high == 0,
          fm("cuts=%d lows=%d inhibit=%d outcome=%u HIGH 期间打印=%d",
             leaf_cuts, leaf_lows, (int)sp_inhibited(),
             (unsigned)tmc_cfg_prod.op_protect, leaf_log_while_high));
    leaf_pk_arm = 0;
    leaf_chip_enabled = 1;

    /* ---- C16c RX 连续洪泛 => 字节/时间双上限, 排不干净就不发这一帧 ---- */
    leaf_state_clear();
    if (!leaf_pre(leaf_bus_ready(), "C16c bus ready")) return;
    {
        rt_uint8_t junk[16];
        int i;

        for (i = 0; i < 16; ++i) junk[i] = (rt_uint8_t)(0x11 * (i + 1));
        for (i = 0; i < 20; ++i) leaf_rx_push(junk, 16);      /* 320 字节 > 256 上限 */
        leaf_write_frames = 0; leaf_read_reqs = 0;
        {
            rt_uint32_t v = 0xA5A5A5A5u;

            e = tmc2209_read_register(0, TMC_REG_GCONF, &v);
            CHECK("C16c-rx-flood-byte-cap-stops-before-tx",
                  e == -RT_EIO && v == 0xA5A5A5A5u && leaf_write_frames == 0,
                  fm("洪泛 rc=%d value=%08X(必须没被改写) 发出 WRITE 帧=%d 读请求=%d "
                     "灌入=%d",
                     (int)e, (unsigned)v, leaf_write_frames, leaf_read_reqs,
                     leaf_rx_pushed_bytes));
        }
        /* 时间上限: 线路持续吐字节(总量不超过 256) => 2ms 的 flush 阶段上限先到,
         * 报 RX_FLOOD(-RT_ETIMEOUT), 并且这一帧根本不发 TX。 */
        leaf_state_clear();
        if (!leaf_pre(leaf_bus_ready(), "C16c2 bus ready")) return;
        leaf_rx_drip = 1;
        {
            rt_uint32_t v = 0xA5A5A5A5u;
            rt_tick_t t0 = leaf_tick;

            e = tmc2209_read_register(0, TMC_REG_GCONF, &v);
            CHECK("C16c2-rx-flood-time-cap-stops-before-tx",
                  e == -RT_ETIMEOUT && leaf_write_frames == 0 && v == 0xA5A5A5A5u &&
                  (rt_uint32_t)(leaf_tick - t0) >= 2u,
                  fm("时间上限 rc=%d elapsed=%ums(阶段上限 2ms) 写帧=%d value=%08X",
                     (int)e, (unsigned)(leaf_tick - t0), leaf_write_frames, (unsigned)v));
        }
        leaf_rx_drip = 0;
    }

    /* CP6 §3: 叶锁(而非最外层 txn)的有界失败与保护 */
    c16_leaflock_cases();
}

/* ===================================================================== */
/* 12. C17 —— 真 UART 等待环                                               */
/* ===================================================================== */
static void c17_group(void)
{
    rt_uint8_t frame[8] = { 0x05u, 0x00u, 0x80u, 0x00u, 0x00u, 0x01u, 0x41u, 0x00u };
    rt_size_t  written;
    rt_err_t   e;
    tmc_op_result_t res;

    frame[7] = leaf_crc8(frame, 7);

    /* ---- C17a 无空间: 一个字节都发不出去, 预算耗尽 => ETIMEOUT 且 written 保持 0 ---- */
    leaf_state_clear();
    if (!leaf_pre(tmc2209_init() == RT_EOK, "C17a uart open")) return;
    leaf_tx_allowed = 0;
    leaf_uart_regs.STAT.WORDVAL &= ~LEAF_TDRE;
    written = 123u;
    e = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, frame, 8, leaf_tick, 5, &written);
    CHECK("C17a-no-space-expires-with-zero-written",
          e == -RT_ETIMEOUT && written == 0 && leaf_tx_written == 0,
          fm("rc=%d written=%u(应 0) 落 DATA 字节=%d tick 推进=%u",
             (int)e, (unsigned)written, leaf_tx_written, (unsigned)(leaf_tick - 100u)));

    /* ---- C17b 部分发送: 原始 written 必须就是发出去的真实字节数 ---- */
    leaf_state_clear();
    if (!leaf_pre(tmc2209_init() == RT_EOK, "C17b uart open")) return;
    leaf_tx_allowed = 3;
    written = 9u;
    e = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, frame, 8, leaf_tick, 20, &written);
    CHECK("C17b-partial-send-keeps-raw-written",
          e == -RT_ETIMEOUT && written == 3 && leaf_tx_written == 3 &&
          memcmp(leaf_tx_bytes_log, frame, 3) == 0,
          fm("rc=%d written=%u 落字节=%d 前 3 字节一致=%d",
             (int)e, (unsigned)written, leaf_tx_written,
             (int)(memcmp(leaf_tx_bytes_log, frame, 3) == 0)));

    /* ---- C17c 末字节之后过期: 全部发出但同一条 start/total 已过 => 不发布成功 ---- */
    leaf_state_clear();
    if (!leaf_pre(tmc2209_init() == RT_EOK, "C17c uart open")) return;
    leaf_tick_jump_after = 8;               /* 第 8 个字节落地的那一刻才被抢占过期限 */
    leaf_tick_jump_ms = 20u;
    written = 0u;
    e = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, frame, 8, leaf_tick, 20, &written);
    CHECK("C17c-expired-after-last-byte-not-a-success",
          e == -RT_ETIMEOUT && written == 8 && leaf_tx_written == 8,
          fm("rc=%d written=%u 落字节=%d(过期仍如实保留已发出的事实)",
             (int)e, (unsigned)written, leaf_tx_written));

    /* ---- C17d 正常整帧对照: RT_EOK 且 written == size, 字节序一致 ---- */
    leaf_state_clear();
    if (!leaf_pre(tmc2209_init() == RT_EOK, "C17d uart open")) return;
    written = 0u;
    e = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, frame, 8, leaf_tick, 64, &written);
    CHECK("C17d-normal-frame-eok",
          e == RT_EOK && written == 8 && leaf_tx_written == 8 &&
          memcmp(leaf_tx_bytes_log, frame, 8) == 0 && leaf_dev_write_calls == 0,
          fm("rc=%d written=%u 字节一致=%d 绕过叶子的 rt_device_write 次数=%d",
             (int)e, (unsigned)written, (int)(memcmp(leaf_tx_bytes_log, frame, 8) == 0),
             leaf_dev_write_calls));

    /* ---- C17e 三条上下文判据一律拒绝, 且拒绝路径不写寄存器、不打印、不延时 ---- */
    leaf_state_clear();
    if (!leaf_pre(tmc2209_init() == RT_EOK, "C17e uart open")) return;
    {
        rt_err_t r_isr, r_masked, r_null;
        uint32_t w0 = ns800_leaf_reg_writes;
        rt_tick_t t0;
        int l0 = leaf_log_total;

        leaf_nest = 1;
        written = 7u;
        r_isr = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, frame, 8,
                                          leaf_tick, 10, &written);
        leaf_nest = 0;

        written = 7u;
        t0 = leaf_tick;
        {
            rt_base_t lvl = rt_hw_interrupt_disable();

            r_masked = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, frame, 8,
                                                 leaf_tick, 10, &written);
            rt_hw_interrupt_enable(lvl);
        }
        written = 7u;
        r_null = ns800_uart_write_budgeted(RT_NULL, frame, 8, leaf_tick, 10, &written);

        CHECK("C17e-context-gates-reject-quietly",
              r_isr == -RT_EBUSY && r_masked == -RT_EBUSY && r_null == -RT_EINVAL &&
              written == 0 && ns800_leaf_reg_writes == w0 && leaf_tx_written == 0 &&
              leaf_log_total == l0 && leaf_tick == t0,
              fm("isr=%d masked=%d null_dev=%d written=%u 寄存器写增量=%u 落字节=%d "
                 "打印增量=%d tick 推进=%u",
                 (int)r_isr, (int)r_masked, (int)r_null, (unsigned)written,
                 (unsigned)(ns800_leaf_reg_writes - w0), leaf_tx_written,
                 leaf_log_total - l0, (unsigned)(leaf_tick - t0)));
    }

    /* ---- C17f 应答完整但阶段期限后才到达 => 操作失败、一次保护、日志排在保护之后 ----
     * 前提用**真实 apply**建立(一次成功的整帧写 + IFCNT 确认), 然后把下一帧应答推迟到
     * 复核的阶段上限之后 => 这一次复核是真的在总线上等过期。 */
    leaf_state_clear();
    memset(&res, 0, sizeof(res));
    if (!leaf_pre(leaf_apply_configured(), "C17f apply configured first")) return;
    leaf_reply_ready_at = leaf_tick + 400u;         /* 远超复核/读回的阶段上限 */
    {
        int cuts0 = leaf_cuts;

        e = tmc2209_config_integrity_check_for(sp_epoch(), &res);
        CHECK("C17f-late-complete-frame-not-a-success",
              e != RT_EOK && res.rc == e && res.protect == SP_PROTECT_BY_THIS_CALL &&
              res.proof.valid == RT_TRUE && leaf_cuts == cuts0 + 1 && leaf_lows == 1 &&
              sp_inhibited() && leaf_log_while_high == 0,
              fm("integrity rc=%d res.rc=%d protect=%u proof.valid=%u cuts=%d lows=%d "
                 "inhibit=%d HIGH 期间打印=%d",
                 (int)e, (int)res.rc, (unsigned)res.protect, (unsigned)res.proof.valid,
                 leaf_cuts, leaf_lows, (int)sp_inhibited(), leaf_log_while_high));
        leaf_reply_ready_at = 0u;
    }

    /* ---- C17g CRC 错的应答 => 结构化失败 + 恰好一次保护, 不许把垃圾当帧 ---- */
    leaf_state_clear();
    memset(&res, 0, sizeof(res));
    if (!leaf_pre(leaf_apply_configured(), "C17g apply configured first")) return;
    leaf_reply_crc_flip = 1;
    {
        rt_uint32_t v = 0;

        (void)tmc2209_read_register(0, TMC_REG_GCONF, &v);
        e = tmc2209_config_integrity_check_for(sp_epoch(), &res);
        CHECK("C17g-crc-error-rejected-and-protects-once",
              e != RT_EOK && v == 0u && leaf_cuts == 1 && leaf_lows == 1 &&
              leaf_log_while_high == 0 && res.rc == e &&
              res.protect == SP_PROTECT_BY_THIS_CALL && res.proof.valid == RT_TRUE,
              fm("integrity rc=%d 裸读 value=%u(必须没拿到) cuts=%d lows=%d HIGH 期间打印=%d "
                 "res.rc=%d res.protect=%u res.proof.valid=%u",
                 (int)e, (unsigned)v, leaf_cuts, leaf_lows, leaf_log_while_high,
                 (int)res.rc, (unsigned)res.protect, (unsigned)res.proof.valid));
        leaf_reply_crc_flip = 0;
    }
}

/* ===================================================================== */
/* 13. C18 —— 同一外层预算跨子读与 IFCNT / tick 回绕 / 旧 op 不刷新          */
/* ===================================================================== */
static void c18_group(void)
{
    rt_err_t  e;
    rt_size_t written;

    /* ---- C18a 一次成功的 apply: 整笔只用同一条 1500ms 预算, 4 写 + 8 读全在预算内 ---- */
    leaf_state_clear();
    if (!leaf_pre(leaf_bus_ready(), "C18a bus ready")) return;
    {
        rt_tick_t t0 = leaf_tick;

        e = tmc2209_apply_config();
        CHECK("C18a-single-budget-covers-subreads-and-ifcnt",
              e == RT_EOK && leaf_write_frames == 4 && leaf_read_reqs == 12 &&
              leaf_chip[0x02] == 4u &&
              leaf_chip[0x00] == 0x00000141u && leaf_chip[0x6C] == 0x15030053u &&
              (leaf_tick - t0) < 1500u && leaf_dev_write_calls == 0,
              fm("apply rc=%d 写帧=%d 读请求=%d IFCNT=%u GCONF=%08X CHOPCONF=%08X "
                 "elapsed=%ums 绕过预算的设备写=%d",
                 (int)e, leaf_write_frames, leaf_read_reqs, leaf_chip[0x02],
                 leaf_chip[0x00], leaf_chip[0x6C], (unsigned)(leaf_tick - t0),
                 leaf_dev_write_calls));
        CHECK("C18a2-summary-published-after-real-success",
              tmc2209_drive_config_ok_for_arm() == RT_TRUE &&
              tmc2209_get_config_state() == TMC_DRV_CONFIGURED &&
              leaf_log_while_high == 0,
              fm("ok_for_arm=%d state=%d HIGH 期间打印=%d",
                 (int)tmc2209_drive_config_ok_for_arm(),
                 (int)tmc2209_get_config_state(), leaf_log_while_high));
    }

    /* ---- C18b 预算耗尽: 每字节 70ms => 同一 start 的 1500ms 在序列中途到期;
     *      到期后既不许重开预算, 也不许发布过期成功 ---- */
    leaf_state_clear();
    if (!leaf_pre(leaf_bus_ready(), "C18b bus ready")) return;
    leaf_drain_ms = 70;
    {
        rt_tick_t t0 = leaf_tick;

        e = tmc2209_apply_config();
        CHECK("C18b-budget-exhausts-without-reopening",
              e != RT_EOK && (leaf_tick - t0) < 1500u + 8u * 70u &&
              tmc2209_drive_config_ok_for_arm() == RT_FALSE &&
              tmc2209_get_config_state() != TMC_DRV_CONFIGURED &&
              leaf_cuts == 1 && leaf_lows == 1 && sp_inhibited() &&
              leaf_log_while_high == 0,
              fm("apply rc=%d elapsed=%ums(预算 1500) 写帧=%d 读请求=%d ok_for_arm=%d "
                 "state=%d cuts=%d lows=%d HIGH 期间打印=%d",
                 (int)e, (unsigned)(leaf_tick - t0), leaf_write_frames, leaf_read_reqs,
                 (int)tmc2209_drive_config_ok_for_arm(), (int)tmc2209_get_config_state(),
                 leaf_cuts, leaf_lows, leaf_log_while_high));
        leaf_drain_ms = 1;
    }

    /* ---- C18c tick 接近回绕: 判据必须是无符号差值(既不能永不超时, 也不能误判过期) ---- */
    leaf_state_clear();
    if (!leaf_pre(tmc2209_init() == RT_EOK, "C18c uart open")) return;
    {
        rt_uint8_t f[8] = { 0x05u, 0x00u, 0x80u, 0x00u, 0x00u, 0x00u, 0x01u, 0x00u };
        rt_tick_t start;

        f[7] = leaf_crc8(f, 8 - 1);
        leaf_tick = 0xFFFFFFFFu - 1u;
        start = leaf_tick;
        written = 0u;
        e = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, f, 8, start, 64u, &written);
        CHECK("C18c-wraparound-with-room-succeeds",
              e == RT_EOK && written == 8 &&
              (rt_uint32_t)(leaf_tick - start) < 64u,
              fm("跨回绕充足预算 rc=%d written=%u start=%08X end=%08X",
                 (int)e, (unsigned)written, (unsigned)start, (unsigned)leaf_tick));

        /* 回绕后已到期的那一类: 预算极小, 必须过期而不是"看起来还剩很久" */
        leaf_state_clear();
        if (!leaf_pre(tmc2209_init() == RT_EOK, "C18c2 uart open")) return;
        leaf_tx_allowed = 0;
        leaf_uart_regs.STAT.WORDVAL &= ~LEAF_TDRE;
        leaf_tick = 0xFFFFFFFFu - 1u;
        start = leaf_tick;
        written = 0u;
        e = ns800_uart_write_budgeted(&uart_obj[0].serial.parent, f, 8, start, 4u, &written);
        CHECK("C18c2-wraparound-expiry-is-unsigned-delta",
              e == -RT_ETIMEOUT && written == 0 &&
              (rt_tick_t)(leaf_tick - start) >= 4u && (rt_tick_t)(leaf_tick - start) < 100u,
              fm("rc=%d written=%u 差值=%u(必须按回绕后的无符号差判)",
                 (int)e, (unsigned)written, (unsigned)(leaf_tick - start)));
    }

    /* ---- C18d 旧 op 晚到的成功不许刷新摘要/健康/返回结果 ---- */
    leaf_state_clear();
    if (!leaf_pre(leaf_bus_ready(), "C18d bus ready")) return;
    {
        rt_uint32_t e0 = sp_epoch();
        sp_proof_t pf;

        leaf_pk_arm = 1;
        if (!leaf_pre(leaf_spawn(c16_apply_thread, RT_NULL) == 0, "C18d apply thread")) return;
        if (!seq_or_timeout("C18d apply 进入本次操作", leaf_pk_wait_arrived(5000)))
        { leaf_pk_release(); leaf_join_all(); return; }
        memset(&pf, 0, sizeof(pf));
        (void)sp_trip_fast(FAULT_SOFT, EVT_SOFT_FAULT, MOT_STOP_SRC_ESTOP, &pf);  /* 换 epoch */
        leaf_pk_release();
        leaf_join_all();
        CHECK("C18d-late-real-success-cannot-refresh-summary",
              sp_epoch() != e0 &&
              tmc2209_get_config_state() != TMC_DRV_CONFIGURED &&
              tmc2209_drive_config_ok_for_arm() == RT_FALSE &&
              leaf_write_frames >= 1,
              fm("身份 %u->%u state=%s ok_for_arm=%d 芯片实收写帧=%d "
                 "(寄存器确实被写过, 但那次结论已过期)",
                 (unsigned)e0, (unsigned)sp_epoch(),
                 tmc_drv_cfg_state_name(tmc2209_get_config_state()),
                 (int)tmc2209_drive_config_ok_for_arm(), leaf_write_frames));
        leaf_pk_arm = 0;
    }
}

/* ===================================================================== */
/* 14. 组入口                                                             */
/* ===================================================================== */
int main(int argc, char **argv)
{
    const char *group = (argc > 1) ? argv[1] : "epwm_leaf";
    int gated = (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED && TMC_DRIVE_CONFIG_HW_VALIDATED) ? 1 : 0;

    /* 台账逐行立刻落盘: 某组一旦被 runner 的墙钟守卫杀掉, 已经跑过的那几行仍然是可读的
     * 证据(重定向到文件时默认是满缓冲, 否则整份日志会随进程一起丢)。 */
    setvbuf(stdout, RT_NULL, _IONBF, 0);

    leaf_uart_cfg.name = TMC_UART_DEVICE_NAME;
    leaf_uart_cfg.Instance = &leaf_uart_regs;
    uart_obj[0].handle.Instance = &leaf_uart_regs;
    uart_obj[0].config = &leaf_uart_cfg;
    uart_obj[0].tx_block_timeout = 6000u;
    leaf_epwm_obj[0].epwm_config = &leaf_epwm_cfg;
    leaf_epwm_obj[0].pin_config = RT_NULL;
    (void)sp_bind_pwm(&leaf_epwm_obj[0].epwm_device, 0);
    (void)sp_bind_permit_pin(safety_drv_permit_pin());

    printf("LEAF|variant=%s|group=%s|gates=%d\n",
#ifdef PS_GATE_HOST_OVERRIDE
           "host_ovr",
#else
           "target",
#endif
           group, gated);

    if (!gated)
    {
        /* 目标发布配置(四宏 RT_FALSE)下, 本组的预约/写入门禁前置不可达 => 只登记**本组**
         * 家族的 SKIP(每组一个进程, 台账里不许出现别的组的行), 由 runner 的 S_ 表判定;
         * 不允许拿"拒绝路径"冒充并发通过, 也不允许拿 target 的 0 用例冒充真实执行。 */
        static const struct { const char *g, *id; } fam[] = {
            { "epwm_leaf", "C10" }, { "tmc_lock", "C16" },
            { "uart_leaf", "C17" }, { "budget_leaf", "C18" }
        };
        unsigned k;

        for (k = 0; k < sizeof(fam) / sizeof(fam[0]); ++k)
            if (rt_strcmp(group, fam[k].g) == 0)
                SKIPV(fam[k].id,
                      "target 变体: 编译期写入门禁挡住, 本组的预约/总线序列前置不可达");
        summary();
        return g_pre ? 2 : 0;
    }

    if (rt_strcmp(group, "epwm_leaf") == 0)      c10_group();
    else if (rt_strcmp(group, "tmc_lock") == 0)  c16_group();
    else if (rt_strcmp(group, "uart_leaf") == 0) c17_group();
    else if (rt_strcmp(group, "budget_leaf") == 0) c18_group();
    else { printf("FATAL|未知组 %s\n", group); return 2; }

    summary();
    if (g_pre) return 2;
    return g_fail ? 1 : 0;
}
