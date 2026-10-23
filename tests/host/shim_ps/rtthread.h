/*
 * shim_ps/rtthread.h - 关断结果链(phase8-stop-result-s1)的 RT-Thread API 兼容层
 *
 * 服务对象: tests/host/ps_main.c。它**真编译** applications 里的
 *   step_pwm.c + motor.c + safety_state.c
 * 因此这里只提供那三个文件真正用到的原语声明。**实现全部在 ps_main.c**:
 *   - 本链是**单线程、确定性**夹具: rt_thread_create/startup 只记次数、不起线程
 *     (S1 要证明的是"关断结果有没有被传播", 不是并发时序);
 *     斜坡那一拍由 ps_main.c 直接调用生产 motor_ramp_tick() 单步执行
 *     (任务书 §4.5 允许的小型静态单步抽取, 等价性论证见 motor.c)。
 *   - 锁 = 计数空操作(单线程下没有竞争); 事件 = 记录 bit 与次数的内存环。
 *   - ⚠ 因此本链**不**模拟调度、优先级继承、中断上下文, 也不测停机时延;
 *     锁等待/UART 阻塞/在途 arm-enable 竞争仍是 S2 的范围(RISK-03/03a 开放)。
 *
 * 错误码数值与 shim/ 一致(由 armclang 常量探针实测, 见 tests/host/README.md);
 * 断言一律用符号, 不写死数字。
 *
 * ⚠ rt_kprintf 在本链被 ps_main.c 重定向为 ps_kprintf(tee: 既打印也进捕获缓冲)。
 *   捕获缓冲**只**用于"输出文本本身就是被测行为"的两条用例(step_status 不得把
 *   未持有输出写成 "no(安全)"、force_shutdown 不得写 "STEP stopped");
 *   其余判定一律来自生产 API 返回值/状态与 ps_sim_* 计数, 不做全局文本搜索。
 */
#ifndef SHIM_PS_RTTHREAD_H
#define SHIM_PS_RTTHREAD_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>

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

#define RT_WAITING_FOREVER ((rt_int32_t)-1)
#define RT_WAITING_NO      ((rt_int32_t)0)
#define RT_IPC_FLAG_FIFO   0x0000
#define RT_IPC_FLAG_PRIO   0x0001

#define RT_EVENT_FLAG_OR          0x0000
#define RT_EVENT_FLAG_AND         0x0100
#define RT_EVENT_FLAG_CLEAR       0x0200

struct rt_mutex { const char *name; rt_bool_t inited; };

rt_err_t rt_mutex_init(struct rt_mutex *m, const char *name, rt_uint16_t flags);
rt_err_t rt_mutex_take(struct rt_mutex *m, rt_int32_t timeout);
rt_err_t rt_mutex_release(struct rt_mutex *m);
rt_err_t rt_mutex_detach(struct rt_mutex *m);

struct rt_event { const char *name; rt_uint8_t flag; rt_bool_t inited; };

rt_err_t rt_event_init(struct rt_event *e, const char *name, rt_uint8_t flag);
rt_err_t rt_event_send(struct rt_event *e, rt_uint32_t event);
rt_err_t rt_event_recv(struct rt_event *e, rt_uint32_t event, rt_uint32_t flag,
                       rt_int32_t timeout, rt_uint32_t *recv);
rt_err_t rt_event_detach(struct rt_event *e);

struct rt_thread { const char *name; void (*entry)(void *); void *param; };
typedef struct rt_thread *rt_thread_t;

rt_thread_t rt_thread_create(const char *name, void (*entry)(void *), void *param,
                             int stack_size, rt_uint8_t prio, rt_uint32_t tick);
rt_err_t    rt_thread_startup(rt_thread_t t);
rt_err_t    rt_thread_delete(rt_thread_t t);

static inline void rt_thread_mdelay(rt_int32_t ms) { (void)ms; }  /* 单线程夹具: 不等 */
static inline rt_tick_t rt_tick_get(void) { return 0u; }          /* 不参与任何判定 */

#define rt_memset    memset
#define rt_memmove   memmove
#define rt_strcmp    strcmp
#define rt_strlen    strlen
#define rt_snprintf  snprintf

/* 生产代码里的 rt_kprintf 由 ps_main.c 在本宏之后 #undef 并换成 ps_kprintf(tee)。
 * 默认值只是"别人在包含前不小心用了它"的兜底。 */
#ifndef rt_kprintf
#define rt_kprintf   printf
#endif

/* MSH 在主机上不需要 shell, 但本链**要**按真实入口调用人工命令(pwm_test / step_status /
 * motor_status), 所以导出一个指向命令函数的外部指针 —— 走与 shell 完全相同的入口,
 * 不给生产代码开后门。 */
#define MSH_CMD_EXPORT(command, desc) \
    const void *ps_mshref_##command = (const void *)(command)
#define MSH_CMD_EXPORT_ALIAS(alias, cmd, desc) \
    const void *ps_mshref_##alias = (const void *)(alias)

#endif /* SHIM_PS_RTTHREAD_H */
