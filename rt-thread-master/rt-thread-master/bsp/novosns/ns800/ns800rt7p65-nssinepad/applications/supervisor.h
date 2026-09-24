/*
 * supervisor.h - 系统监督初版 (Phase 7-B 最小实现)
 *
 * system_status: 打印各子系统健康 + 安全状态机 + 电机快照。
 * runtime_selftest: 软件级联调(不依赖真实运动), 验证:
 *   非法状态转换拒绝 / motor arm 门禁 / 故障停机链 / 事件处理 / sensor valid 处理。
 * 完整 UI 留 Phase 7-D。
 */
#ifndef SUPERVISOR_H
#define SUPERVISOR_H

#include <rtthread.h>

/* 幂等: 注册 MSH 命令(system_status / runtime_selftest)并拉起 UI 管理线程 */
int supervisor_init(void);

/* 全子系统状态打印(main 与 MSH system_status 共用) */
void system_status(void);

#endif /* SUPERVISOR_H */
