/*
 * shim_leaf/rtthread.h —— CP5 §5: 真实 HAL 叶子链的 RT-Thread 兼容层(叠加在 shim_ps 上)。
 *
 * 为什么是"叠加"而不是另写一份: S1/I-O/SD/V1V2/S2 五条既有链共用 shim_ps/, 它们的
 * 时钟与设备语义已经审过, 不能因为本链需要脚本化时钟而去改它们的行为。本文件只做两件事:
 *   1) 按相对路径包含 shim_ps/rtthread.h(内容逐字不变, 只多一个 PS_LEAF_SCRIPTED_TIME 分支);
 *   2) 补本链才需要的东西: CMSIS 的 __get_PRIMASK(真实 UART 叶子的上下文门要用它)、
 *      信号量(TMC 自身的锁注入钩子用)。
 * 编译时 -I shim_leaf 必须排在 -I shim_ps 之前, 于是生产代码里的 <rtthread.h> 落到这里。
 */
#ifndef NS800_LEAF_RTTHREAD_H
#define NS800_LEAF_RTTHREAD_H

#include "../shim_ps/rtthread.h"
#include <stddef.h>

/* rt_container_of: 目标上由 RT-Thread 的 rtdef.h 提供; shim_ps 的最小 rtthread.h 没有它
 * (那五条链不需要)。本链的真实 EPWM 叶子用
 *   rt_container_of(device, struct ns800_epwm, epwm_device)
 * 从设备句柄反查驱动对象 —— 这正是"私有结构布局必须共用"的原因, 所以这里按 rtdef.h
 * 原式给出同一个定义。 */
#ifndef rt_container_of
#define rt_container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - (size_t)offsetof(type, member)))
#endif

/* ---------- CMSIS 原物: PRIMASK 读取 ----------
 * drv_uart_txleaf.inc 里的 ns800_intr_masked() 用 __get_PRIMASK() != 0 判"当前是否
 * 已屏蔽中断"(目标上是 CMSIS 内联读 PRIMASK)。主机上没有 PRIMASK, 但本链的窗口原语
 * 已经把"谁在临界区内"记成线程本地状态(与 S2 并发夹具同一模型), 所以这里把它接到
 * 那个真实状态上: 在窗口内 => 非 0, 窗口外 => 0。
 * ⚠ 这条判据的用意是"屏蔽期内不得按 tick 等待", 因此 C17 的 ISR/掩码拒绝用例观察的
 *   是**生产叶子自己的**三条上下文判据, 不是夹具替生产做的判断。 */
uint32_t ns800_leaf_primask(void);
static inline uint32_t __get_PRIMASK(void) { return ns800_leaf_primask(); }

/* ---------- 信号量(生产 tmc2209.c 自身的锁持有/交接测试钩子会用到) ---------- */
struct rt_semaphore { const char *name; };
typedef struct rt_semaphore *rt_sem_t;

rt_sem_t  rt_sem_create(const char *name, rt_uint32_t value, rt_uint8_t flag);
rt_err_t  rt_sem_take(rt_sem_t sem, rt_int32_t timeout);
rt_err_t  rt_sem_release(rt_sem_t sem);
rt_err_t  rt_sem_delete(rt_sem_t sem);

#endif /* NS800_LEAF_RTTHREAD_H */
