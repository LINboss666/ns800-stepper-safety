/*
 * stop_result.h - STEP 关断动作结果的语义定义 (Phase 8 安全停机可靠性 S1, 2026-10-23)
 *
 * 只定义"数据含义", 不含判定逻辑; 不改任何既有枚举编号、结构字段次序或黑匣子格式:
 *   - 关断结果、"许可(PC.23/IO87)写确认"、"ENN(PC.6/IO70)反馈"是**三件不同的事**,
 *     谁都不能替代谁: 焊盘 LOW 或 ENN HIGH 证明不了 STEP 已被关断, 反之亦然;
 *   - MOT_STOP_API_ACCEPTED 仅表示 `rt_pwm_disable()` 返回 RT_EOK, 即**驱动 API 接受了
 *     关断请求**; 它**不是**示波器/逻辑分析仪证明的"无脉冲"(那仍是 NOT TESTED);
 *   - "从未持有诊断所有权""软件频率字段被写成 0""未取得设备句柄"都**不能**冒充
 *     "输出已关断";
 *   - `current_hz`/`target_hz` 为 0 只是**软件命令值**, 实际是否关断只看本枚举。
 *
 * 本轮(S1)不处理锁等待 / UART 阻塞 / 在途 arm-取消竞争, 也不声称任何停机时延上界;
 * RISK-03/03a 与 RISK-04-b 继续开放。
 */
#ifndef STOP_RESULT_H
#define STOP_RESULT_H

#include <rtthread.h>

/* 最近一次 STEP 关断**动作**的结果。"输出状态未知"= 失败或未取得设备: 既不宣称
 * 已关断, 也不宣称仍在运行, 只说明软件无法确认。 */
typedef enum
{
    MOT_STOP_NOT_ATTEMPTED = 0,     /* 从未尝试关断(也不代表输出在跑) */
    MOT_STOP_DEV_UNAVAILABLE,       /* 未取得 PWM 设备句柄: 未尝试, 输出状态未知 */
    MOT_STOP_OUTPUT_REQUESTED,      /* 已请求非零输出: "关断"这个词此刻不适用 */
    MOT_STOP_CMD_ZERO_UNCONFIRMED,  /* 只把软件命令值写成 0Hz, 未确认输出已关断 */
    MOT_STOP_API_ACCEPTED,          /* rt_pwm_disable()==RT_EOK: API 接受关断(非波形证明) */
    MOT_STOP_API_FAILED             /* rt_pwm_disable()!=RT_EOK: 输出状态未知/可能仍在跑 */
} mot_stop_result_t;

/* 最近一次关断动作的调用来源(只用于如实报告"是谁关的", 不参与判定) */
#define MOT_STOP_SRC_NONE          0u
#define MOT_STOP_SRC_INIT          1u   /* motor_init() 的上电默认关断 */
#define MOT_STOP_SRC_RAMP_ZERO     2u   /* 斜坡线程减速到 0 后的关断 */
#define MOT_STOP_SRC_APPLY_FAIL    3u   /* 设置频率失败后的关断 */
#define MOT_STOP_SRC_ARM_ROLLBACK  4u   /* arm 失败回滚 */
#define MOT_STOP_SRC_GUARD_STOP    5u   /* 运行期配置守卫停机 */
#define MOT_STOP_SRC_FAIL_CLOSED   6u   /* mot_fail_closed() */
#define MOT_STOP_SRC_DISARM        7u   /* motor_disarm() */
#define MOT_STOP_SRC_ESTOP         8u   /* motor_emergency_stop() */
#define MOT_STOP_SRC_DIAG_TEST     9u   /* pwm_test stop */
#define MOT_STOP_SRC_DIAG_FORCE   10u   /* step_pwm_force_stop() 统一停机 */
#define MOT_STOP_SRC_STOP_CMD     11u   /* motor_stop()/target=0: 只是软件命令 */

/* 名称只用于日志与快照展示, 不做判定输入。 */
const char *mot_stop_result_name(mot_stop_result_t r);
const char *mot_stop_src_name(rt_uint8_t src);

/* 是否为"驱动 API 接受关断"。TRUE 也只代表驱动**接受了请求**, 不是无脉冲证明。 */
rt_bool_t mot_stop_result_is_off(mot_stop_result_t r);

/* 是否属于"输出状态未知"(关断失败或未取得设备) —— 只有这两种触发失败锁存。 */
rt_bool_t mot_stop_result_is_uncertain(mot_stop_result_t r);

#endif /* STOP_RESULT_H */
