/*
 * supervisor.h - 系统监督 + 显式业务层 bootstrap (Phase 7-D, Fix A 加固)
 *
 * supervisor_boot(): 唯一的业务层启动入口, 由 main() 显式调用, 返回 rt_err_t。
 *   stage 1..3 / 7 为 required, 任一失败即 fail closed(锁存 FAULT_LATCHED)、
 *   放弃后续 stage 并返回错误; 成功走完才置 boot_done。幂等, 可重试。
 *   启动顺序: 1 safe GPIO / 2 safety state+event / 3 Safety Thread /
 *   4 自检所需硬件服务 / 5 storage(慢速 log 扫描) / 6 project config /
 *   7 motor+sensor / 8 diagnosis / 9 blackbox / 10 UI / 11 启动自检+READY 决策。
 *
 * system_status(): 全子系统状态打印(main 与 MSH system_status 共用)。
 */
#ifndef SUPERVISOR_H
#define SUPERVISOR_H

#include <rtthread.h>

/* Fix A: 显式业务层 bootstrap(幂等), 替代 INIT_APP 链接顺序依赖 */
rt_err_t supervisor_boot(void);

/* 全子系统状态打印(main 与 MSH system_status 共用) */
void system_status(void);

#endif /* SUPERVISOR_H */
