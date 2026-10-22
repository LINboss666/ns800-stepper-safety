/*
 * shim_sd/rtthread.h - 安全数据语义链(phase8-safety-data-semantics)的 RT-Thread 兼容层
 *
 * 本目录只服务 sd_main.c: 它**真编译** applications/sensor_service.c 与
 * applications/blackbox.c, 其余依赖(其他传感器/电机/状态/存储/GPIO)都是显式可注入桩,
 * 定义在 sd_main.c 里。与 shim/ 和 shim_io/ 并列, 互不复用:
 *   - 错误码数值与另两套一致(armclang 常量探针实测, 见 tests/host/README.md)。
 *   - 线程**不真起**: rt_thread_create 返回非空假句柄, 因此 worker 不会并发运行,
 *     用例直接调 s_collect()/bb_sample()/bb_to_sample() 这些真实代码路径。
 *   - rt_kprintf = printf, 并由 sd_main.c 做**进程内捕获**, 只用于 SD10 这类
 *     "输出本身就是被测行为"的判定; 其余判定一律来自掩码/返回值/计数器。
 */
#ifndef SHIM_SD_RTTHREAD_H
#define SHIM_SD_RTTHREAD_H

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#ifndef WINVER
#define WINVER 0x0600
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stddef.h>
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

/* 与 shim/、shim_io/ 同一套目标错误码 */
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

struct rt_thread
{
    HANDLE      h;
    const char *name;
    void      (*entry)(void *);
    void       *param;
};
typedef struct rt_thread *rt_thread_t;

/* 假线程: 只登记不创建, 保证测试单线程且确定(sd_main.c 统计 create 次数) */
rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick);
rt_err_t    rt_thread_startup(rt_thread_t t);
rt_err_t    rt_thread_delete(rt_thread_t t);

/* 临界区/中断屏蔽: 本链单线程, 用进程级锁计数即可 */
void        rt_enter_critical(void);
void        rt_exit_critical(void);
rt_base_t   rt_hw_interrupt_disable(void);
void        rt_hw_interrupt_enable(rt_base_t level);

#define rt_kprintf   sd_kprintf
#define rt_memset    memset
#define rt_memmove   memmove
#define rt_memcmp    memcmp
#define rt_strcmp    strcmp
#define rt_strlen    strlen
#define rt_snprintf  snprintf
#define rt_tick_get  sd_tick_get
#define rt_tick_from_millisecond(ms) ((rt_tick_t)(ms))

static inline void rt_thread_mdelay(rt_int32_t ms) { Sleep((DWORD)(ms > 0 ? ms : 1)); }
static inline rt_tick_t sd_tick_get(void) { return (rt_tick_t)GetTickCount(); }

/* 捕获式 kprintf(sd_main.c 实现): 同时写真实 stdout 与用例缓冲 */
int sd_kprintf(const char *fmt, ...);
void sd_capture_reset(void);
const char *sd_capture_text(void);

#define MSH_CMD_EXPORT(command, desc) \
    const void *sd_mshref_##command = (const void *)(command)
#define MSH_CMD_EXPORT_ALIAS(alias, cmd, desc) \
    const void *sd_mshref_##alias = (const void *)(alias)

#endif /* SHIM_SD_RTTHREAD_H */
