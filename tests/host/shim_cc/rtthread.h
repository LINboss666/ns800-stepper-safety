/* shim_cc/rtthread.h - M7 current_cal host chain: extend the S1 RT-Thread shim
 * with the two scheduler-critical primitives used by current_adc.c.  All real
 * product .c files are compiled against this shim; test state lives in cc_main.c. */
#ifndef SHIM_CC_RTTHREAD_H
#define SHIM_CC_RTTHREAD_H

#include "../shim_ps/rtthread.h"

void rt_enter_critical(void);
void rt_exit_critical(void);

/* M8: project_config.c 的 v2->v3 逐字段迁移用 rt_memcpy; 之前本链没编译过这条路径。
 * 与 shim_sd 同一口径(memcpy 别名), 不引入新语义。 */
#define rt_memcpy memcpy

/* diag_relative.c 作为独立 TU 参与链接时需要 cc_main.c 里的捕获式打印符号声明。 */
int cc_kprintf(const char *fmt, ...);

#endif /* SHIM_CC_RTTHREAD_H */
