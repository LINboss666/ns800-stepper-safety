/*
 * shim_rel/rtthread.h - M8 相对保护链(diag_relative/diagnosis/sensor_service/
 * project_config 真编译)的 RT-Thread 兼容层
 *
 * 与 shim_sd/ 并列而**不复用**: 本链需要 shim_sd 没有的两样东西 ——
 *   1) **脚本化时间**: rt_tick_get / rt_thread_mdelay 由 rel_main.c 的虚拟时钟驱动。
 *      M8 的判据是"持续 500ms 且跨过 >=3 个独立 SG 样本", 用 GetTickCount() 真实
 *      等待既不可复现也无法在毫秒级铺开边界(SG 10Hz 与 ADC 100Hz 的并度必须精确
 *      构造)。shim_sd 的真实时钟在这里会让用例变成时序运气。
 *   2) **互斥量**: project_config.c 的 cfg_lock 与 cfg_take_checked() 是本次判据
 *      ("取不到锁不得发布/不得读 profile")的执行路径, 必须可注入失败。
 *
 * 错误码数值与 shim/、shim_sd/、shim_io/ 同一套(armclang 常量探针实测, 见
 * tests/host/README.md), 不另立口径。
 * 线程一律**不真起**: rt_thread_create 只登记, 因此生产循环由用例自己按预算驱动
 * (rel_main.c 用 rel_thread_stop_here() 从受控 mdelay 里 longjmp 回来)。
 */
#ifndef SHIM_REL_RTTHREAD_H
#define SHIM_REL_RTTHREAD_H

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
#define RT_EFULL           13
#define RT_EEMPTY          14

#define RT_WAITING_FOREVER ((rt_int32_t)-1)
#define RT_WAITING_NO      ((rt_int32_t)0)
#define RT_IPC_FLAG_FIFO   0x0000
#define RT_IPC_FLAG_PRIO   0x0001

struct rt_thread
{
    const char *name;
    void      (*entry)(void *);
    void       *param;
    int         started;
};
typedef struct rt_thread *rt_thread_t;

/* 假线程: 只登记不创建(rel_main.c 计数并可提供句柄) */
rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick);
rt_err_t    rt_thread_startup(rt_thread_t t);
rt_err_t    rt_thread_delete(rt_thread_t t);

/* 互斥量: 可实现/可取/可还, 取还次数与失败注入由 rel_main.c 记账 */
struct rt_mutex
{
    rt_uint8_t initialized;
    rt_uint8_t held;
    rt_uint8_t name[RT_NAME_MAX];
};
rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint8_t flag);
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t time);
rt_err_t rt_mutex_release(struct rt_mutex *m);

/* 临界区: 单线程链里只计数(用于证明"等待期间不持锁") */
void        rt_enter_critical(void);
void        rt_exit_critical(void);
rt_base_t   rt_hw_interrupt_disable(void);
void        rt_hw_interrupt_enable(rt_base_t level);

#define rt_kprintf   rel_kprintf
#define rt_memset    memset
#define rt_memmove   memmove
#define rt_memcmp    memcmp
#define rt_memcpy    memcpy
#define rt_strcmp    strcmp
#define rt_strncmp   strncmp
#define rt_strlen    strlen
#define rt_strstr    strstr
#define rt_snprintf  snprintf
#define rt_vsnprintf vsnprintf

/* ---- 脚本化时间(rel_main.c 实现) ---- */
rt_tick_t rel_tick_get(void);
void      rel_mdelay(rt_int32_t ms);
#define rt_tick_get()                rel_tick_get()
#define rt_thread_mdelay(ms)         rel_mdelay(ms)
#define rt_tick_from_millisecond(ms) ((rt_tick_t)(ms))

int  rel_kprintf(const char *fmt, ...);
void rel_capture_reset(void);
const char *rel_capture_text(void);

#define MSH_CMD_EXPORT(command, desc) \
    const void *rel_mshref_##command = (const void *)(command)
#define MSH_CMD_EXPORT_ALIAS(alias, cmd, desc) \
    const void *rel_mshref_##alias = (const void *)(alias)

#endif /* SHIM_REL_RTTHREAD_H */
