/*
 * shim_flash/rtthread.h - flash_dump 导出边界链的 RT-Thread 兼容层
 *
 * 服务对象: tests/host/flash_main.c。它**真编译**
 *   applications/ns_flash_shell.c
 * 因此这里只给该文件真正用到的类型/宏/原语声明。实现全部在 flash_main.c:
 *   - rt_kprintf 按目标 RT_CONSOLEBUF_SIZE=128 建模单次截断(仅本链);
 *   - rt_kputs 完整输出(与 BSP 的真实实现一致: _kputs(str, strlen), 不逐次限长);
 *   - ns_flash_* / ns_storage_* / ns_log_* 全部是显式可注入桩。
 *
 * 本链证明的是**导出格式与边界**, 不证明 Flash 驱动、页布局、CRC 或真实落盘。
 */
#ifndef SHIM_FLASH_RTTHREAD_H
#define SHIM_FLASH_RTTHREAD_H

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
/* kerrno.h 的非 libc 分支取值; 本链只比较 e == -RT_EEMPTY, 不跨工具链传播。 */
#define RT_EEMPTY          4

#define RT_TICK_PER_SECOND 1000u

#define rt_memset    memset
#define rt_memmove   memmove
#define rt_strcmp    strcmp
#define rt_strlen    strlen
#define rt_snprintf  snprintf

void rt_kprintf(const char *fmt, ...);
void rt_kputs(const char *str);
void *rt_malloc(rt_size_t size);
void rt_free(void *ptr);
rt_tick_t rt_tick_get(void);

/* MSH 在主机上不需要 shell; 导出指针只为与真实入口同名, 本链直接调用 flash_dump。 */
#define MSH_CMD_EXPORT(command, desc) \
    const void *fl_mshref_##command = (const void *)(command)
#define MSH_CMD_EXPORT_ALIAS(alias, cmd, desc) \
    const void *fl_mshref_##alias = (const void *)(alias)

#endif
