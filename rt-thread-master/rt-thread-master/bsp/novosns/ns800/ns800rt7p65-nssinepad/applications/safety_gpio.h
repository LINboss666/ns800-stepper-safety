/*
 * safety_gpio.h - 安全 GPIO 子系统对外接口
 */
#ifndef SAFETY_GPIO_H
#define SAFETY_GPIO_H

#include <rtthread.h>

/* 安全 GPIO 是否全部解析并初始化成功 */
rt_bool_t safety_gpio_ready(void);

/* P1-8: bootstrap 显式调用(幂等); 业务模块不再依赖 INIT_APP 链接顺序。
 * Fix A: 返回真实结果 —— 任一引脚解析/初始化失败即 -RT_EINVAL, 调用方
 * (supervisor_boot stage 1)必须据此 fail closed, 不再吞掉错误。 */
rt_err_t safety_gpio_boot(void);

/* 按引脚名字(project_board.h 中的 PIN_NAME_xxx)取已解析的 pin 号, 失败返回 <0 */
rt_base_t safety_pin(const char *name);

/* ---------- MCU_DRV_ENABLE(PC.23 / J4-21)安全关键输出 ----------
 * rt_pin_write() 无返回值, 写不进/被顶高都无从得知(BUG-009 同类风险),
 * 因此该脚的所有写操作必须经下面的"写 + 回读"确认。回读走 GPIO_readPin
 * (DAT = 焊盘实际电平), 只能证明 MCU 焊盘电平, 不替代 ENN 整链硬件验收。 */

/* 写使能请求并回读确认(readback == level)。含短暂 settle + 重试 1 次。
 * RT_EOK = 焊盘实测等于 level; -RT_EIO = 回读不符; -RT_EINVAL = 引脚未解析。 */
rt_err_t safety_drv_enable_write(rt_uint8_t level);

/* 只读实测: 焊盘当前是否确认为 LOW(禁止驱动)。引脚未解析一律返回 FALSE。 */
rt_bool_t safety_drv_enable_is_low(void);

/* ---------- ENN 状态反馈(PC.6 = 板上 TMC_ENN 网络, 被动输入) ----------
 * 依据: 用户 2026-10-21 确认该脚接到扩展板 H6-5/TMC_ENN; TMC2209 Datasheet
 * Rev.1.09 印刷第 9 页 §2.2 —— ENN 低有效使能输入, 拉高则功率级关断、输出浮空。
 * 因此反馈 HIGH = 在禁能侧, 反馈 LOW = 在未施加禁能侧(**不等于一定有转矩**)。
 * 这颗脚不是急停输入, 软件不得把它当"独立急停感测", 也不得据此区分切断原因。
 * ⚠ 读到的数字电平只说明焊盘上看到什么, 不证明反馈线没断/不与模块脚断开 ——
 *   线路完整性仍属硬件验收(RISK-04)。 */
typedef enum
{
    SAFETY_FB_OK = 0,             /* 焊盘=请求 且 反馈=请求对应的期望侧 */
    SAFETY_FB_TRANSITION,         /* 焊盘已到请求侧, 反馈仍在另一侧(可能是传播中, 不算确认) */
    SAFETY_FB_CUT_BY_HARDWARE,    /* 请求使能但反馈在禁能侧(硬件链切断: 急停/逻辑器件) */
    SAFETY_FB_UNEXPECTED_ENABLE,  /* 请求禁能但反馈在使能侧(说停没停, 最危险) */
    SAFETY_FB_PAD_MISMATCH,       /* 焊盘实测与请求不符(写不进/被顶高) */
    SAFETY_FB_NO_SAMPLE           /* 反馈脚或许可脚未解析 / 读不出有效样本 */
} safety_fb_verdict_t;

/* 纯函数(无副作用, 可被生产链与主机用例共用): 按请求态判定一组观测样本。 */
safety_fb_verdict_t safety_fb_evaluate(rt_uint8_t want_enabled,
                                       rt_int32_t pad, rt_bool_t pad_ok,
                                       rt_int32_t fb,  rt_bool_t fb_ok);

const char *safety_fb_verdict_name(safety_fb_verdict_t v);

/* 单次采样判定(不延时、不写、不产生 UART 流量): 供只读状态路径与写入门禁用。 */
safety_fb_verdict_t safety_fb_now(rt_uint8_t want_enabled);

/* 决策点用的**有界**确认: 在有限的 settle/重试窗口内采到一致样本才算 OK。
 * 窗口是测试可见的配置常数, **不是**停机时延或物理时序上界(RISK-03 仍开放)。
 * RT_EOK=与请求一致; -RT_EINVAL=引脚未解析; -RT_EIO=组合矛盾;
 * -RT_ETIMEOUT=有界窗口内仍停在过渡态。调用方失败时必须按 fail-closed 处理。 */
rt_err_t safety_drv_feedback_confirm(rt_uint8_t want_enabled, safety_fb_verdict_t *out);

/* 只读快照: 许可请求(最近一次经回读确认的电平)/焊盘实测/反馈实测与解析状态。
 * 指针可为 RT_NULL。不产生任何写动作或总线流量。 */
void safety_fb_snapshot(rt_int32_t *out_request, rt_int32_t *out_pad,
                        rt_int32_t *out_fb, rt_bool_t *out_fb_resolved);

/* ---------- S2(2026-10-23, 停机优先): 裸动作与确认分离 ----------
 * 短保护窗口内**只能**调用下面的 raw 入口：单次寄存器写、无日志、无延时、无循环。
 * rt_pin_write() 返回 void ⇒ 返回 RT_EOK 只表示"写请求已发给 GPIO 寄存器"，
 * **不**表示焊盘实测到位；焊盘确认仍然是 safety_drv_enable_write()/回读那一条合同。 */

/* 已解析的许可**数值** pin（PC.23 -> 数值），<0 表示未解析。绑定后由 stop_priority 缓存，
 * 窗口内不再做字符串查找。 */
rt_base_t safety_drv_permit_pin(void);

/* 许可 LOW / HIGH 的裸写。HIGH 只允许由 stop_priority 的 token 门调用（生产旁路见下）。 */
rt_err_t safety_drv_permit_low_raw_held(void);
rt_err_t safety_drv_permit_high_raw_held(void);

/* 线程上下文用的许可 LOW **确认**（写 + settle + 回读，沿用 2 次×1ms 上限；
 * 数值是测试可见常数，不是停机时延上界）。返回 RT_EOK 才允许记 confirmed。 */
rt_err_t safety_drv_permit_confirm_low(void);

/* 只读确认(不写脚): 用于"已在 token 门内写过 HIGH"之后的 settle+回读复核。
 * 不匹配时的**重试**必须重新调用 sp_commit_permit_high() —— 每一次重写都是一次开启动作。
 * 上限沿用 DRV_EN_TRIES/SETTLE_MS(测试可见常数, 不是停机时延上界)。 */
rt_err_t safety_drv_permit_confirm_read(rt_uint8_t level);

/* 本配置里两路可选限位是否处于启用状态(= SAFETY_LIMIT_INPUTS_ENABLED)。 */
rt_bool_t safety_limit_inputs_enabled(void);

#endif /* SAFETY_GPIO_H */
