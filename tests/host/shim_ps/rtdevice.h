/*
 * shim_ps/rtdevice.h - 关断结果链的设备/PWM/GPIO 仿真层声明
 *
 * 固件(step_pwm.c / motor.c / safety_state.c)真正用到的设备 API 只有
 * rt_device_find / rt_pwm_* / 一组 rt_pin_*。这里给**可仿真但绝不碰硬件**的实现,
 * 实现体在 ps_main.c:
 *   - PWM: 可注入每个 API 的返回码, 并**独立**保存"模拟输出是否仍在运行":
 *       rt_pwm_enable 成功 => running=YES; rt_pwm_disable **成功** => running=NO;
 *       rt_pwm_disable 失败 => running **保持不变**(桩不替固件把输出清掉)。
 *     motor.c 与 step_pwm.c 拿到的是**同一个** epwm1 句柄与同一个 ch0, 所以这个
 *     running 标志就是那条物理通道的单一真相, 能照出"两路都想关但有一路没关掉"。
 *   - 设备句柄可摘除(ps_sim_dev_set_present(0)) => rt_device_find 返回 RT_NULL,
 *     用于"未取得设备句柄"这一类结果(与"API 失败"是不同含义)。
 *   - GPIO: 名称→仿真引脚表 + 可注入电平; rt_pin_read 可以返回 0/1 之外的值,
 *     表示"取不到有效样本"。许可脚(PC.23)的"写不进"用 ps_sim_set_pad_stuck() 注入。
 *
 * ⚠ 用例判定必须来自生产 API 返回值/状态 + ps_sim_* 计数(见文件头说明),
 *   只有两条"输出文本本身即被测行为"的用例允许查捕获缓冲。
 */
#ifndef SHIM_PS_RTDEVICE_H
#define SHIM_PS_RTDEVICE_H

#include <rtthread.h>

struct rt_device { int fake; };
typedef struct rt_device *rt_device_t;

struct rt_device_pwm { int fake; };

#define PIN_HIGH 1
#define PIN_LOW  0

#define PIN_MODE_INPUT          0
#define PIN_MODE_OUTPUT         1
#define PIN_MODE_INPUT_PULLUP   2
#define PIN_MODE_INPUT_PULLDOWN 3
#define PIN_MODE_OUTPUT_OD      4

/* ---------- 生产代码用到的 API ---------- */
rt_device_t rt_device_find(const char *name);

rt_err_t    rt_pwm_set(struct rt_device_pwm *pwm, int channel,
                       rt_uint32_t period, rt_uint32_t pulse);
rt_err_t    rt_pwm_enable(struct rt_device_pwm *pwm, int channel);
rt_err_t    rt_pwm_disable(struct rt_device_pwm *pwm, int channel);

rt_base_t   rt_pin_get(const char *name);
void        rt_pin_mode(rt_base_t pin, rt_base_t mode);
void        rt_pin_write(rt_base_t pin, rt_base_t value);
rt_base_t   rt_pin_read(rt_base_t pin);

/* ---------- 测试侧控制与观测(实现体在 ps_main.c) ---------- */
void        ps_sim_reset(void);                 /* 清引脚表/计数/注入(不动生产状态) */

/* 设备句柄 */
void        ps_sim_dev_set_present(int on);     /* 0 => rt_device_find 返回 RT_NULL */

/* PWM: 每个 API 的返回码可独立注入; 调用次数分别可观测 */
void        ps_sim_pwm_set_rc(rt_err_t set_rc, rt_err_t enable_rc, rt_err_t disable_rc);
int         ps_sim_pwm_set_calls(void);
int         ps_sim_pwm_enable_calls(void);
int         ps_sim_pwm_disable_calls(void);
int         ps_sim_pwm_running(void);           /* 模拟输出是否仍在跑(桩的真相) */
void        ps_sim_pwm_force_running(int on);   /* 前置: 造出"输出确实在跑"的状态 */

/* GPIO: 引脚表与许可脚行为 */
void        ps_sim_add_pin(const char *name, rt_int32_t init_level);
void        ps_sim_remove_pin(const char *name);
void        ps_sim_set_level(const char *name, rt_int32_t lv);
rt_int32_t  ps_sim_level(const char *name);
void        ps_sim_set_pad_stuck(int on);       /* 许可脚写不进(回读不符) */
int         ps_sim_pin_write_calls(const char *name);
rt_int32_t  ps_sim_last_write(const char *name);
int         ps_sim_pin_read_calls(const char *name);

/* 线程创建/启动次数(证明 init 失败时没有建正常运行线程) */
int         ps_sim_thread_create_calls(void);
int         ps_sim_thread_startup_calls(void);

#endif /* SHIM_PS_RTDEVICE_H */
