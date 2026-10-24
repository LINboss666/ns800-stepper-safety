/*
 * shim_io/rtthread.h - 独立 I/O 行为测试用的 RT-Thread 原语兼容层
 *
 * 与 shim/ 的区别: shim/ 服务旧链(host_main.c 把 safety_gpio/safety_thread/
 * safety_state/step_pwm 全部替换成桩, 只编译 tmc2209.c + motor.c)。本目录服务
 * **新链 io_main.c**: 它编译**真实的** safety_gpio.c / safety_thread.c /
 * safety_state.c / motor.c, 因此这里必须提供固件真正用到的原语:
 *   rt_mutex_*   : CRITICAL_SECTION(同线程可重入 + 持有计数, 与 RT-Thread 一致)
 *   rt_event_*   : CS + CONDITION_VARIABLE 的真实位事件(跨线程 post/recv)
 *   rt_thread_*  : CreateThread(静态 init 与动态 create 都支持)
 *   rt_thread_mdelay / rt_tick_*
 * 错误码数值与 shim/ 一致(由 armclang 常量探针实测, 见 tests/host/README.md),
 * 断言一律用符号。
 *
 * ⚠ 这里**故意不提供** rt_kprintf 的"输出捕获"接口: 用例判定只能来自 API 状态与
 *   计数器(shim_io/rtdevice.h 的 io_sim_*), 不得用全局文本搜索代替用例判定。
 * ⚠ 这不是目标平台: 中断上下文、优先级继承、tick 调度不模拟; 证明的是**逻辑与
 *   接入行为**, 不是物理时序或硬件安全链(那些仍是 NOT TESTED / RISK-04)。
 */
#ifndef SHIM_IO_RTTHREAD_H
#define SHIM_IO_RTTHREAD_H

/* 条件变量需要 Vista+ API 集(否则 windows.h 不声明 SleepConditionVariableCS 等) */
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

/* 目标错误码(与 shim/ 同一套, armclang 实测): EPERM=8 EINVAL=5 ENOSYS=45
 * EBUSY=23 EIO=12 ETIMEOUT=117 ERROR=255 */
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

#define RT_EVENT_FLAG_OR          0x0000
#define RT_EVENT_FLAG_AND         0x0100
#define RT_EVENT_FLAG_CLEAR       0x0200

/* ---------- mutex: 同线程可重入(CRITICAL_SECTION 天然满足) ---------- */
struct rt_mutex
{
    CRITICAL_SECTION cs;
    rt_bool_t        inited;
};

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags);
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout);
rt_err_t rt_mutex_release(struct rt_mutex *m);
rt_err_t rt_mutex_detach(struct rt_mutex *m);

/* ---------- event: 真实跨线程位事件 ---------- */
struct rt_event
{
    CRITICAL_SECTION     cs;
    CONDITION_VARIABLE   cv;
    rt_uint32_t          flags;
    rt_bool_t            inited;
};

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag);
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event);
rt_err_t rt_event_recv(struct rt_event *e, rt_uint32_t event, rt_uint32_t flag,
                       rt_int32_t timeout, rt_uint32_t *recv);
rt_err_t rt_event_detach(struct rt_event *e);

/* ---------- thread ---------- */
struct rt_thread
{
    HANDLE    h;
    const char *name;
    void    (*entry)(void *);
    void     *param;
    rt_bool_t started;
};
typedef struct rt_thread *rt_thread_t;

/* CP4 K5(§7): 生产代码的可等待/可打印上下文门需要的两条真实 RT-Thread API。
 * I/O 链同样是单线程模型: 中断嵌套由 io_isr_set() 显式置位, rt_thread_self() 在
 * 已屏蔽窗口内返回空(与目标上 PRIMASK 期内不许 mdelay/打印同一事实)。 */
rt_bool_t   rt_interrupt_get_nest(void);
rt_thread_t rt_thread_self(void);
void        io_isr_set(int v);

rt_err_t      rt_thread_init(struct rt_thread *t, const char *name,
                             void (*entry)(void *), void *param,
                             rt_uint8_t *stack, rt_uint32_t stack_size,
                             rt_uint8_t prio, rt_uint32_t tick);
rt_thread_t   rt_thread_create(const char *name, void (*entry)(void *), void *param,
                               int stack_size, rt_uint8_t prio, rt_uint32_t tick);
rt_err_t      rt_thread_startup(rt_thread_t t);
rt_err_t      rt_thread_delete(rt_thread_t t);

/* ---------- klibc 映射 ---------- */
#define rt_kprintf   printf
#define rt_memset    memset
#define rt_memmove   memmove
#define rt_strcmp    strcmp
#define rt_strlen    strlen
#define rt_snprintf  snprintf
#define rt_tick_get  io_tick_get
#define rt_tick_from_millisecond(ms) ((rt_tick_t)(ms))

static inline void rt_thread_mdelay(rt_int32_t ms) { Sleep((DWORD)(ms > 0 ? ms : 1)); }
static inline rt_tick_t io_tick_get(void) { return (rt_tick_t)GetTickCount(); }

/* MSH 命令注册在主机上不需要空实现, 但本夹具**要**能按真实入口调用这些人工命令
 * (safety_polarity_confirm / safety_irq_enable ...), 因此导出一个指向命令函数的
 * 外部指针。测试通过它走与 shell 完全相同的入口(含参数校验), 不给生产代码开后门。 */
#define MSH_CMD_EXPORT(command, desc) \
    const void *io_mshref_##command = (const void *)(command)
#define MSH_CMD_EXPORT_ALIAS(alias, cmd, desc) \
    const void *io_mshref_##alias = (const void *)(alias)

/* 线程安全: 事件/仿真核共用的进程级锁(由 io_main.c 定义) */
extern CRITICAL_SECTION io_lock;

#endif /* SHIM_IO_RTTHREAD_H */
