/*
 * sp_measure_a0_console.h - A0 主机链专用的"控制台长度模型"（BOARD-A-01 的可判定化）
 *
 * 为什么需要它：shim_ps/rtthread.h 把 rt_kprintf 直接映射成宿主 printf(无限长)，
 * 所以 BOARD-A-01(目标 128 字节控制台缓冲剪断长行)在主机链里**结构性地看不见**。
 * 本头让 A0 链的三个编译单元(stop_priority.c / sp_measure_a0.c / 夹具)改用与目标
 * 同形的控制台叶子，于是"真实 console 边界"第一次能在主机上判定。
 *
 * 只由 tests/host/run_sp_measure_a0.sh 用 -include 注入，**不改公共 shim_ps**
 * （shim_ps 同时被 S1/S2 链使用，本文件的存在不影响它们）。
 *
 * 与当前内核源码的同形关系（逐条，边界见下）：
 *   A0_CONSOLEBUF=128            <- BSP rtconfig.h:107 #define RT_CONSOLEBUF_SIZE 128
 *   static a0_log_buf[]          <- src/kservice.c:391 static char rt_log_buf[RT_CONSOLEBUF_SIZE]
 *   a0_rt_vsnprintf(buf,size,..) <- src/kservice.c:405 rt_vsnprintf(rt_log_buf, sizeof(rt_log_buf)-1, ...)
 *                                   目标实际链接的是 src/klibc/rt_vsnprintf_tiny.c
 *                                   (Keil project.uvprojx 的文件列表里有 rt_vsnprintf_tiny.c；
 *                                   rtconfig.h 未定义 RT_KLIBC_USING_LIBC_VSNPRINTF，
 *                                   所以 kstdio.c:81 的 libc 版本被编译掉)。
 *                                   tiny 版每个转换都"先判 str<end 再无条件 ++str"
 *                                   (rt_vsnprintf_tiny.c:325-340, 447-470)，因此返回值
 *                                   = **应当写出的总字节数**(C99 语义, 不含 NUL)，写入
 *                                   至多 size-1 个字符 + NUL。本模型用 4096 字节大缓冲
 *                                   先取到这个"应写长度"，再按 size 截断，等价。
 *   length>127 => 127            <- src/kservice.c:406-409
 *   a0_console_emit(buf,length)  <- src/kservice.c:411 _kputs(rt_log_buf, length)
 *                                   **逐字节**送出 length 个字节，包括被剪断时位于
 *                                   第 127 字节的那个 NUL(板级原始接收字节已实证：
 *                                   146 个 NUL 中 144 个恰好在前一分隔符后第 126 字节)。
 *   a0_kputs(s) => strlen 长度   <- src/kservice.c:369-381 rt_kputs() 用 _kputs(str, rt_strlen(str))，
 *                                   不经过 128 字节格式化缓冲，也不送 NUL。
 *   不加打印锁                   <- rtconfig.h 未定义 RT_USING_THREADSAFE_PRINTF，
 *                                   kservice.c:327-332 把 CONSOLE_TAKE/CONSOLE_RELEASE
 *                                   展开成空 => 模型同样不承诺一行不被别的线程插入。
 *
 * 明确的边界（模型不覆盖，故不得据此宣称"目标导出已通过"）：
 *   1) 控制台设备层的 \n -> \r\n 扩展(板级原始字节里 776/781 个 LF 前都有 CR；
 *      唯一 5 个孤 LF 在 01_reset_pyocd.txt，那是本机工具自己的 stdout 文本，不是目标上线)。
 *      => 采集侧 a0_export_gate.py 同时接受 LF 与 CRLF。
 *   2) UART 的发送时序/波特率/缓冲区溢出，以及其它线程日志的真实插入时机。
 *   3) rt_console_output_get_enabled() 为假时 rt_kputs/rt_kprintf 直接返回(kservice.c:372-380,
 *      396-399) —— 本模型假定输出使能(与板级一致：横幅与 msh 提示符都能收到)。
 *   4) 目标上若没有注册 console 设备，_kputs 会退化到 rt_hw_console_output(str)
 *      (drv_common.c:175)，那条路径用 rt_strlen 且会补 \r —— 板级收到 NUL 说明
 *      走的是 rt_device_write(有 console 设备) 分支，本模型按这条同形。
 */
#ifndef SP_MEASURE_A0_CONSOLE_H
#define SP_MEASURE_A0_CONSOLE_H

#include <rtthread.h>      /* 先让 shim 完成 rt_kprintf 的默认映射, 下面再覆盖 */
#include <stdarg.h>

/* ---- 模型叶子（定义在 tests/host/sp_measure_a0_main.c） ---- */
int  a0_rt_vsnprintf(char *buf, rt_size_t size, const char *fmt, va_list ap);
int  a0_kprintf(const char *fmt, ...);
void a0_kputs(const char *s);

/* ---- 上线字节捕获：断言只看这里，不看 stdout ---- */
void        a0_console_capture(int on);   /* 1=开始记录逐字节, 0=停止 */
void        a0_console_reset(void);
const char *a0_console_bytes(int *len, int *trunc, int *nul);
int         a0_console_has(const char *frag);

/* 生产代码里的这三个名字在目标上是内核函数；主机链用同签名实现顶替，
 * 使 spm_emitf() 走真实叶子、也让"退回 rt_kprintf"的反向可被检出。 */
#define rt_vsnprintf a0_rt_vsnprintf
#define rt_kputs     a0_kputs
#undef  rt_kprintf
#define rt_kprintf   a0_kprintf

#endif /* SP_MEASURE_A0_CONSOLE_H */
