/*
 * sp_measure_a0_port.h - A0 主机链的寄存器/总线模型接口（仅主机测试使用）
 *
 * 分工（任务书 §8）：**目标 counter 后端与主机脚本化寄存器后端分离，记录/判定/发布逻辑
 * 共用同一份生产实现**。因此这里只声明"叶子寄存器模型 + 总线动作计数"这套端口，
 * 记录器与有效性判定一律走 APP/sp_measure_a0.c 的真码，夹具不再自写一份记录器。
 *
 * 模型刻意**不使用主机墙钟**冒充周期计数器：CYCCNT 是脚本化递增值，reload/回绕/配置漂移
 * 都由测试显式安排；读 SysTick->CTRL 的"读即清 COUNTFLAG"副作用如实建模。
 */
#ifndef SP_MEASURE_A0_PORT_H
#define SP_MEASURE_A0_PORT_H

#include <rtthread.h>

/* ---- 生产侧要求的主机后端（APP/sp_measure_a0.c 在 NS800_SPM_HOST_BACKEND 下引用） ---- */
rt_uint32_t spm_port_ctr_now(void);
rt_uint32_t spm_port_ctr_ctrl(void);
rt_uint32_t spm_port_demcr(void);
void        spm_port_ctr_enable(rt_uint8_t trcena, rt_uint8_t cyccntena);
rt_uint32_t spm_port_systick_ctrl(void);
rt_uint32_t spm_port_systick_load(void);
rt_uint32_t spm_port_primask(void);

/* ---- 寄存器模型的脚本控制 ---- */
#define PORT_LOAD_DEFAULT   200000u          /* 200 MHz / 1000 tick => LOAD = 200000-1 量级 */

void port_ppb_reset(rt_bool_t implemented, rt_uint32_t step_per_read, rt_bool_t systick_on);
void port_ppb_reload_inside(rt_bool_t on);   /* 窗口内发生一次重装 => 终点读见 COUNTFLAG */
void port_ppb_load_drift_after(int n_ctrl_reads);  /* 第 n 次 CTRL 读之后 LOAD 变化 */
void port_ppb_make_wrap(void);               /* body_end < body_begin（32 位回绕） */
void port_ppb_stopped(void);                 /* 计数器不递增（step=0） */
void port_ppb_arm(void);                     /* 复位本线程的读序(被测调用前调用一次) */
int  port_ppb_ctr_reads(void);
int  port_ppb_ctrl_reads(void);
int  port_ppb_enable_calls(void);

/* ---- 在窗口/发布序列里的确定接缝 ----
 * now 回调参数 idx = 本线程内第几次计数读（0=pre, 1=body_begin, 2=body_end, 3=post）。
 * ctrl 回调参数 idx = 本线程内第几次 CTRL 读（0=起点读, 1=终点读）。 */
typedef void (*port_hook_t)(int idx);
void port_set_now_hook(port_hook_t h);
void port_set_ctrl_hook(port_hook_t h);

/* ---- 总线动作计数（独立核对"记录标志"不能替代的真实调用） ---- */
int  port_pwm_disable_calls(void);
int  port_pwm_enable_calls(void);
int  port_permit_low_calls(void);
int  port_permit_high_calls(void);
int  port_pin_write_calls(void);
int  port_shutdown_calls(void);
int  port_print_calls(void);
int  port_mutex_calls(void);
int  port_mdelay_calls(void);
void port_bus_reset(void);
void port_pwm_script(rt_int32_t first_rc, rt_int32_t second_rc);   /* 接下来两次 disable 的 rc */
void port_permit_script(rt_int32_t first_rc, rt_int32_t second_rc);

/* ---- 窗口内禁止项台账（TMA18）：夹具把窗口内观察到的每一次违例登记成一行 ---- */
void port_window_open(void);
void port_window_close(void);
int  port_window_violations(void);
const char *port_window_last_violation(void);

/* ---- 导出通道（TMA25..TMA27，BOARD-A-01） ----
 * spm_test_emit_long() 定义在生产文件里、仅在 NS800_SPM_HOST_BACKEND 下编译，
 * 目标上不存在这个符号；它唯一的作用是造出一条必然超过 SPM_TEXT_CAP 的整行，
 * 用来判定"具名 EXPORT_ERROR + 负返回 + 不发半条内容"。
 * 控制台模型本体（a0_kprintf/a0_kputs/a0_rt_vsnprintf + 逐字节捕获）见
 * tests/host/sp_measure_a0_console.h 的文件头注释。 */
int spm_test_emit_long(int pad);

#endif /* SP_MEASURE_A0_PORT_H */
