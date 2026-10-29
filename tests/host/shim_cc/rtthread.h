/* shim_cc/rtthread.h - M7 current_cal host chain: extend the S1 RT-Thread shim
 * with the two scheduler-critical primitives used by current_adc.c.  All real
 * product .c files are compiled against this shim; test state lives in cc_main.c. */
#ifndef SHIM_CC_RTTHREAD_H
#define SHIM_CC_RTTHREAD_H

#include "../shim_ps/rtthread.h"

void rt_enter_critical(void);
void rt_exit_critical(void);

#endif /* SHIM_CC_RTTHREAD_H */
