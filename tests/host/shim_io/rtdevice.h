/*
 * shim_io/rtdevice.h - I/O 行为测试用的设备/PWM/GPIO 仿真层
 *
 * 固件里 safety_gpio/safety_thread/motor 真正用到的设备 API 只有 rt_pwm_*、
 * rt_device_find 和一组 rt_pin_*。这里全部给**可仿真但绝不碰硬件**的实现:
 *   - GPIO: 名称→仿真引脚表; 未注册的名称 rt_pin_get 返回负值 = "引脚解析失败";
 *           rt_pin_read 可以返回 0/1 之外的值 = "取不到有效样本"(固件必须拒绝)。
 *   - 使能链: rt_pin_read(PC.6) 的电平由 io_sim_set_chain() 选的模型推导,
 *           等价于 RevA 图纸 NAND + Q1 反相 + R10 上拉的真值表(理想/急停切断/
 *           卡使能/滞后/断线五种), 不是固件自己的逻辑。
 *   - PWM/设备: 只记调用次数, 不产生任何输出; 真实串口/下载器 API 在本夹具里
 *           **不存在**, 因此 io_dev_open/read/write_calls 必须恒为 0 (IO10)。
 *
 * ⚠ 用例判定必须来自下面的 io_sim_* 计数与生产 API 返回值/状态, 不允许文本搜索。
 */
#ifndef SHIM_IO_RTDEVICE_H
#define SHIM_IO_RTDEVICE_H

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

#define PIN_IRQ_MODE_RISING          0
#define PIN_IRQ_MODE_FALLING         1
#define PIN_IRQ_MODE_RISING_FALLING  2
#define PIN_IRQ_MODE_HIGH_LEVEL      4
#define PIN_IRQ_MODE_LOW_LEVEL       5

#define PIN_IRQ_DISABLE 0
#define PIN_IRQ_ENABLE  1

#define RT_DEVICE_OFLAG_RDWR   0x03
#define RT_DEVICE_FLAG_INT_RX  0x100
#define RT_DEVICE_CTRL_CONFIG  4

struct serial_configure
{
    rt_uint32_t baud_rate, data_bits, parity, stop_bits, buf_size;
};
#define RT_SERIAL_CONFIG_DEFAULT { 115200, 8, 0, 1, 64 }

/* ---------- 固件用到的 API ---------- */
rt_device_t rt_device_find(const char *name);
rt_err_t    rt_device_open(rt_device_t dev, rt_uint16_t oflag);
rt_err_t    rt_device_close(rt_device_t dev);
rt_size_t   rt_device_read(rt_device_t dev, rt_size_t pos, void *buffer, rt_size_t size);
rt_size_t   rt_device_write(rt_device_t dev, rt_size_t pos, const void *buffer, rt_size_t size);
rt_err_t    rt_device_control(rt_device_t dev, int cmd, void *arg);

rt_err_t    rt_pwm_set(struct rt_device_pwm *pwm, int channel,
                       rt_uint32_t period, rt_uint32_t pulse);
rt_err_t    rt_pwm_enable(struct rt_device_pwm *pwm, int channel);
rt_err_t    rt_pwm_disable(struct rt_device_pwm *pwm, int channel);

rt_base_t   rt_pin_get(const char *name);
void        rt_pin_mode(rt_base_t pin, rt_base_t mode);
void        rt_pin_write(rt_base_t pin, rt_base_t value);
rt_base_t   rt_pin_read(rt_base_t pin);
rt_err_t    rt_pin_attach_irq(rt_base_t pin, rt_uint32_t mode,
                              void (*hdr)(void *args), void *args);
rt_err_t    rt_pin_irq_enable(rt_base_t pin, rt_uint32_t enable);
rt_err_t    rt_pin_detach_irq(rt_base_t pin);

/* ---------- 测试侧控制与观测(定义在 io_main.c) ---------- */
/* 使能链仿真模型 */
enum io_chain_mode {
    IO_CHAIN_IDEAL = 0,     /* fb = (pad HIGH ? LOW : HIGH) —— 图纸真值表 */
    IO_CHAIN_ESTOP_CUT,     /* 硬件链切断: fb 恒 HIGH(即使许可 HIGH) */
    IO_CHAIN_STUCK_ENABLED, /* 说停没停: fb 恒 LOW(即使许可 LOW) */
    IO_CHAIN_LAG,           /* 传播滞后: 需要 n 次读才跟随 */
    IO_CHAIN_NO_WIRE,       /* 断线/无有效样本: rt_pin_read 返回非 0/1 */
    IO_CHAIN_FLAPPING       /* 样本在两侧来回跳: 用于验证"过渡不记成功"(纯仿真工件) */
};

void        io_sim_reset(void);                       /* 清空引脚表与所有计数 */
int         io_sim_add_pin(const char *name, rt_int32_t init_level);
void        io_sim_remove_pin(const char *name);      /* 模拟"引脚解析失败" */
rt_int32_t  io_sim_level(const char *name);           /* 当前电平(可能 -1=不存在) */
void        io_sim_set_level(const char *name, rt_int32_t lv);  /* 外部驱动输入脚 */
void        io_sim_wire(void);                        /* 注册冻结表用到的全部引脚 */

int         io_sim_mode_calls(const char *name);
int         io_sim_read_calls(const char *name);
int         io_sim_write_calls(const char *name);
rt_int32_t  io_sim_last_write(const char *name);
rt_base_t   io_sim_last_mode(const char *name);       /* -999 = 没调过 mode */
int         io_sim_attach_calls(const char *name);
int         io_sim_attach_edge(const char *name);     /* -1 = 没有 ISR 挂上 */
int         io_sim_irq_enable_calls(const char *name);
int         io_sim_detach_calls(const char *name);
int         io_sim_fire(const char *name, rt_int32_t new_level); /* 变沿匹配则调 ISR */

void        io_sim_set_chain(int mode);
int         io_sim_chain_mode(void);
void        io_sim_set_lag(int n);                    /* LAG 模型: 还需几次读才跟随 */
void        io_sim_set_pad_stuck(int on);             /* 焊盘写不进(回读不符) */
void        io_sim_fail_attach(const char *name, int times);  /* 注册 IRQ 失败 N 次 */
int         io_sim_fire(const char *name, rt_int32_t new_level);

extern int  io_dev_find_calls;
extern int  io_dev_open_calls;
extern int  io_dev_read_calls;
extern int  io_dev_write_calls;
extern int  io_pwm_set_calls;
extern int  io_pwm_enable_calls;
extern int  io_pwm_disable_calls;
extern int  io_event_post_counts[32];                 /* safety_post_event 每位计数 */
extern int  io_fake_bus_reads;                        /* 假芯片被读的次数(允许 >0) */
extern int  io_cfg_txn_begins;
extern int  io_cfg_txn_ends;

rt_err_t io_event_wait_bits(rt_uint32_t bits, rt_uint32_t *out, rt_int32_t timeout_ms);
void     io_event_clear_counts(void);

#endif /* SHIM_IO_RTDEVICE_H */
