/*
 * blackbox.h - 故障黑匣子 (Phase 7-D, 审查修复版)
 *
 * 结构: 100Hz 连续采样入 RAM pre 环(默认 200 帧=2s, 上限 300);
 *       触发(blackbox_trigger, O(1) 不阻塞 Safety)后继续收 post 帧
 *       (默认 100 帧=1s, 上限 150), 捕获区 = pre+post 一块静态数组
 *       (BB_CAP_FRAMES, 编译期断言), 零 malloc。
 *
 * Safety Thread 纪律: trigger 仅置 volatile 标志, 零等待零 Flash 操作。
 * 触发策略: 捕获/写盘进行中的新触发被忽略并计数(首个故障优先),
 *           绝不产生陈旧第二 session。
 * pre 不足: 开机未满 2s 时只写实际已采帧数(pre_n), 不把全 0 当有效历史。
 * 数字输入: estop/limit/diag 持久化在 record.flags 位域(见 bb_to_sample)。
 */
#ifndef BLACKBOX_H
#define BLACKBOX_H

#include <rtthread.h>
#include "app_health.h"

#define BB_PRE_FRAMES       300u    /* 捕获缓冲容量上限(对应 config pre ≤3s) */
#define BB_POST_FRAMES      150u    /* 捕获缓冲容量上限(对应 config post ≤1.5s) */
#define BB_CAP_FRAMES       (BB_PRE_FRAMES + BB_POST_FRAMES)

/* 幂等初始化: 建立采样/落盘 worker 线程(prio 18)。 */
rt_err_t blackbox_init(void);

/* 故障触发(O(1), Safety Thread/Safety 状态机调用):
 * 冻结 pre 环, 收 post 帧, session++ 后由 worker 写 Flash。
 * 捕获/写盘进行中收到的新触发被忽略(计数), 首个故障优先。 */
void blackbox_trigger(rt_uint32_t fault_code);

subsys_health_t blackbox_get_health(void);

#endif /* BLACKBOX_H */
