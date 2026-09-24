/*
 * blackbox.h - 故障黑匣子 (Phase 7-D)
 *
 * 结构: 100Hz 连续采样入 RAM 环形缓冲(pre 区, 默认 200 帧=2s);
 *       触发(blackbox_trigger, O(1) 不阻塞 Safety)后继续收 post 区
 *       (默认 100 帧=1s), 然后由本模块 worker(优先级 18)异步逐帧
 *       ns_log_submit 写 Flash —— 队列深度 16, 满时 flush 重试,
 *       绝不一次暴力提交打爆队列。
 *
 * Safety Thread 纪律: trigger 仅置 volatile 标志, 零等待零 Flash 操作。
 * 数据纪律: 源帧 valid 位映射进 NS_VALID_*; 无效源不写 0 冒充
 *          (对应字段保留上次值, valid 位如实)。
 * 静态内存: 环形缓冲与 post 区均为编译期数组, 零运行期 malloc。
 */
#ifndef BLACKBOX_H
#define BLACKBOX_H

#include <rtthread.h>
#include "app_health.h"

/* 采样参数(帧周期 10ms @100Hz) */
#define BB_PRE_FRAMES      200u   /* 2.0s */
#define BB_POST_FRAMES     100u   /* 1.0s */

/* 幂等初始化: 建立采样/落盘 worker 线程(prio 18)。 */
rt_err_t blackbox_init(void);

/* 故障触发(O(1), Safety Thread/Safety 状态机调用):
 * 冻结 pre 环, 收 post 帧, session++ 后由 worker 写 Flash。 */
void blackbox_trigger(rt_uint32_t fault_code);

subsys_health_t blackbox_get_health(void);

#endif /* BLACKBOX_H */
