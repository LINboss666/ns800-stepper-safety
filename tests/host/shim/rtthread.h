/*
 * shim/rtthread.h - 主机(GCC/MinGW)测试夹具用的 RT-Thread API 兼容层
 *
 * 用途: 在不改固件源码的前提下, 让**真实的** applications/tmc2209.c 与
 *       motor.c 在 PC 上编译并执行单元测试(独立复审 R01/R02/R03/R04 要求)。
 * 语义对齐要点:
 *   - 互斥量: CRITICAL_SECTION = 同线程可重入 + 带持有计数, 与 RT-Thread rt_mutex
 *     一致; RT_WAITING_NO 映射到 TryEnterCriticalSection(被别的线程占着就失败)。
 *   - 错误码: 目标固件**启用**了 RT_USING_LIBC + RT_USING_ARMLIBC(定义在 Keil 工程
 *     Target 级 <Define>, 不是 rtconfig.h), 因此 rtdef.h 走 libc 分支:
 *     EPERM=8 / EINVAL=5 / ENOSYS=45 / EBUSY=23 / EIO=12 / ETIMEOUT=117 / ERROR=255。
 *     本文件下方的宏就是这套数值(由 armclang 常量探针实测, 见
 *     host_harness/armclang_errcode_evidence.txt)。上一版注释曾写"未开 LIBC、
 *     EBUSY=7"那一套非 libc 数值, 那是**错的**(F2d-R1 复审 §5 勘误)。
 *   - 这不是目标平台运行环境: 线程优先级、tick 调度、中断上下文都不模拟。
 *     因此本夹具证明的是**逻辑与状态机行为**, 不是实时性/并发时序(那些仍待上板)。
 */
#ifndef SHIM_RTTHREAD_H
#define SHIM_RTTHREAD_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <windows.h>

typedef int8_t   rt_int8_t;
typedef uint8_t  rt_uint8_t;
typedef int16_t  rt_int16_t;
typedef uint16_t rt_uint16_t;
typedef int32_t  rt_int32_t;
typedef uint32_t rt_uint32_t;
typedef int64_t  rt_int64_t;
typedef uint64_t rt_uint64_t;
typedef int      rt_bool_t;
typedef int      rt_err_t;
typedef long     rt_base_t;
typedef size_t   rt_size_t;
typedef uint32_t rt_tick_t;

#define RT_NULL            NULL
#define RT_TRUE            1
#define RT_FALSE           0
#define RT_NAME_MAX        8

/* 目标错误码 —— F2d-R1 R04 校准。
 * 上一轮这里按"rtconfig.h/.config 里搜不到 RT_USING_LIBC"选了 kerrno.h 的非 libc
 * 分支(EPERM=13/EBUSY=7/ENOSYS=6/…)，那是错的: RT_USING_LIBC 与 RT_USING_ARMLIBC
 * 定义在 Keil 工程 Target 级 <Define>(project.uvprojx 的 <Cads> 段), Applications
 * 组无相反覆盖; rttypes.h 的 <sys/errno.h> 实际解析到
 * components/libc/compilers/common/extension/sys/errno.h, 其 __ARMCC_VERSION 分支
 * ERROR_BASE_NO=7。
 * 下面的数值由**真实 armclang + 工程同一宏集与包含路径**编译探针读回, 与独立复审
 * 的静态推导逐项一致(见 host_harness/armclang_errcode_evidence.txt):
 *   RT_EOK=0 RT_ERROR=255 RT_ETIMEOUT=117 RT_ENOSYS=45 RT_EBUSY=23 RT_EIO=12
 *   RT_EINVAL=5 RT_EPERM=8 RT_EINTR=11 RT_ENOMEM=6
 * 注: 断言一律用符号(-RT_EBUSY 等), 数值只影响日志里打印出来的数字。 */
#define RT_EOK             0
#define RT_ERROR           255
#define RT_ETIMEOUT        117
#define RT_ENOSYS          45
#define RT_EBUSY           23
#define RT_EIO             12
#define RT_EINVAL          5
#define RT_EPERM           8
#define RT_EINTR           11
#define RT_ENOMEM          6

#define RT_WAITING_FOREVER ((rt_int32_t)-1)
#define RT_WAITING_NO      ((rt_int32_t)0)
#define RT_IPC_FLAG_FIFO   0x0000
#define RT_IPC_FLAG_PRIO   0x0001

/* ---------- mutex: 同线程可重入, 非阻塞取用 TryEnter ---------- */
struct rt_mutex
{
    CRITICAL_SECTION cs;
    rt_bool_t        inited;
};

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags);
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout);
rt_err_t rt_mutex_release(struct rt_mutex *m);
rt_err_t rt_mutex_detach(struct rt_mutex *m);

/* ---------- semaphore ---------- */
struct rt_sem_obj { HANDLE h; };
typedef struct rt_sem_obj *rt_sem_t;

rt_sem_t rt_sem_create(const char *name, rt_uint32_t value, rt_uint8_t flag);
rt_err_t rt_sem_take(rt_sem_t s, rt_int32_t timeout);
rt_err_t rt_sem_release(rt_sem_t s);
rt_err_t rt_sem_delete(rt_sem_t s);

/* ---------- thread(仅用于"另一线程持锁"这类互斥用例) ---------- */
struct rt_thread_obj
{
    HANDLE          h;
    const char     *name;                 /* 夹具用于按名字注入创建/启动失败 */
    void          (*entry)(void *);
    void           *param;
};
typedef struct rt_thread_obj *rt_thread_t;

rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick);
rt_err_t    rt_thread_startup(rt_thread_t t);
rt_err_t    rt_thread_delete(rt_thread_t t);

/* ---------- klibc 映射 ----------
 * rt_kprintf 走夹具函数: 一边照常打到 stdout, 一边在"捕获窗口"开启时留一份行,
 * 供 R02 机器可判定地核对固件自测的**终态行**(void 自测没有返回值, 上一轮因此
 * 漏掉了 FAILED 也报 ALL PASS 的缺口)。捕获只读, 不改变任何输出内容。 */
int shim_kprintf(const char *fmt, ...);
#define rt_kprintf   shim_kprintf
#define rt_memset    memset
#define rt_memmove   memmove
#define rt_strcmp    strcmp
#define rt_strlen    strlen
#define rt_snprintf  snprintf
#define rt_tick_get  shim_tick_get
#define rt_tick_from_millisecond(ms) ((rt_tick_t)(ms))

static inline void rt_thread_mdelay(rt_int32_t ms) { Sleep((DWORD)ms); }
static inline rt_tick_t shim_tick_get(void) { return (rt_tick_t)GetTickCount(); }

/* ---------- finsh 命令注册: 主机上不需要 ---------- */
#define MSH_CMD_EXPORT(command, desc)
#define MSH_CMD_EXPORT_ALIAS(alias, cmd, desc)

/* ---------- 主机测试夹具的可见计数器(在 main.c 里断言) ---------- */
extern int shim_dev_find_calls;
extern int shim_dev_open_calls;
extern int shim_dev_read_calls;
extern int shim_dev_write_calls;
extern int shim_pwm_enable_calls;
extern int shim_pwm_disable_calls;
extern int shim_pwm_set_calls;
extern int shim_pin_write_calls;
extern int shim_transition_calls;
extern int shim_transition_to_run;
extern int shim_post_event_calls;
extern int shim_drv_enable_write_calls;
extern int shim_last_drv_enable_level;

/* 可控的"芯片侧/安全侧"输入(默认: 安全侧一切禁能, 与真机上电一致) */
extern rt_bool_t shim_pad_low;            /* safety_drv_enable_is_low() */
extern rt_bool_t shim_state_ready;        /* safety_state_get()==READY */
extern rt_bool_t shim_fault_none;         /* safety_fault_get()==FAULT_NONE */
extern rt_bool_t shim_protection_ready;   /* safety_protection_ready() */
extern rt_bool_t shim_read_returns_data;  /* 串口读是否可能拿到应答(默认 FALSE=失联) */

/* ---------- 故障注入开关(只在主机夹具里生效, 不改固件源码/工程配置) ----------
 * R02 的 INCOMPLETE 方向与 R03 的"辅助线程建不起来/起不来"方向需要它们。
 * 全部默认关闭 = 正常回归。 */
extern const char *shim_mutex_init_fail_prefix;   /* 非 NULL: 该前缀的互斥量创建失败 */
extern const char *shim_thread_create_fail_name;  /* 非 NULL: 该名字的线程创建失败 */
extern const char *shim_thread_startup_fail_name; /* 非 NULL: 该名字的线程启动失败 */

#endif /* SHIM_RTTHREAD_H */
