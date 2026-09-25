/*
 * blackbox.h - 故障黑匣子 (Phase 7-D, Fix C 修复)
 *
 * 结构: 100Hz 连续采样入 RAM pre 环(默认 200 帧=2s, 上限 300);
 *       触发(blackbox_trigger, O(1) 不阻塞 Safety)后继续收 post 帧
 *       (默认 100 帧=1s, 上限 150), 捕获区 = pre+post 一块静态数组
 *       (BB_CAP_FRAMES, 编译期断言), 零 malloc。
 *       实测占用: bb_frame_t = 48B/帧 → 300×48 + 450×48 = 36,000B ≈ 35.2KB BSS
 *       (旧注释的 10.8KB / 27KB 都不对; 扩窗口前先看 map 里的 RW_DTCM)。
 *
 * Safety Thread 纪律: trigger 只置标志(关中断最小临界), 零等待零 Flash 操作。
 *
 * 触发策略(Fix C / C2: 首故障优先, 两个窗口都覆盖):
 *   1) pending 标志尚未被 worker 消费 → 后来的触发不覆盖首个故障码,
 *      计 early_drop;
 *   2) 已在捕获/写盘中 → 丢弃并计 busy_drop, 绝不产生第二个 session。
 *
 * 记录语义(Fix C / C3, 与 blackbox_selftest 一致):
 *   pre  窗口记录: event = 0        (故障发生前的历史上下文, 本身不是事件)
 *   post 窗口记录: event = 会话故障码
 *   两者靠 record.session_id 归组; estop/limit/diag 数字输入存 record.flags 位域。
 *
 * pre 不足: 开机未满一个 pre 窗口时只写实际已采帧数(pre_n), 不把全 0 当有效历史。
 * 落盘: 逐帧 ns_log_submit, 队列满时有界重试 + 睡眠(Fix C / C5); 超限则丢弃本
 *   会话余下帧并置 SUBSYS_DEGRADED —— 绝不空转, 也绝不阻塞安全路径。
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
 * 首个故障优先: pending 未消费时的后续触发不覆盖首故障码(early_drop);
 * 捕获/写盘进行中的后续触发被丢弃(busy_drop)。 */
void blackbox_trigger(rt_uint32_t fault_code);

subsys_health_t blackbox_get_health(void);

#endif /* BLACKBOX_H */
