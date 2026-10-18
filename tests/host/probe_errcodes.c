/* probe_errcodes.c - 只定义常量, 供 ARMCLANG 编译到汇编后读回数值。
 * 由 probe_errcodes.py 生成; 不参与固件构建。
 */
#include <rtthread.h>

volatile int obs_RT_EOK = RT_EOK;
volatile int obs_RT_ERROR = RT_ERROR;
volatile int obs_RT_ETIMEOUT = RT_ETIMEOUT;
volatile int obs_RT_ENOSYS = RT_ENOSYS;
volatile int obs_RT_EBUSY = RT_EBUSY;
volatile int obs_RT_EIO = RT_EIO;
volatile int obs_RT_EINVAL = RT_EINVAL;
volatile int obs_RT_EPERM = RT_EPERM;
volatile int obs_RT_EINTR = RT_EINTR;
volatile int obs_RT_ENOMEM = RT_ENOMEM;
volatile int obs_RT_WAITING_FOREVER = RT_WAITING_FOREVER;
volatile int obs_RT_WAITING_NO = RT_WAITING_NO;
#ifdef RT_USING_LIBC
volatile int obs_branch_RT_USING_LIBC = 1;
#else
volatile int obs_branch_RT_USING_LIBC = 0;
#endif
#ifdef RT_USING_ARMLIBC
volatile int obs_branch_RT_USING_ARMLIBC = 1;
#else
volatile int obs_branch_RT_USING_ARMLIBC = 0;
#endif
#ifdef RT_USING_NANO
volatile int obs_branch_RT_USING_NANO = 1;
#else
volatile int obs_branch_RT_USING_NANO = 0;
#endif
