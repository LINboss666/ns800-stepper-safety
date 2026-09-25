/*
 * diagnosis.h - 多源异常诊断引擎 (Phase 7-C)
 *
 * 输入: 统一 diag_input_t(从 sensor_frame_t + motor 快照提取)。
 * 特征: SG 滤波/Δ、电流滤波/Δ、振动 RMS/峰值(滑动窗)、速度分带、加减速相位。
 * 输出: NORMAL / LOAD_WARNING / IMPACT / OVERLOAD /
 *       STALL_SUSPECT / STALL_CONFIRMED / SENSOR_FAULT
 *
 * 反误报纪律:
 *   - 单个 sample 永不直接判 severe(persistence 计数)
 *   - 同一源帧序号只处理一次(P1-5/Fix B: 去重在 diag_step() 引擎内, 不在线程里,
 *     因此 diag_selftest 直接调引擎也能覆盖这一层)
 *   - SG 阈值按速度分带(低速 SG 不可靠 → 阈值收敛)
 *   - sensor missing ≠ 0: 无效数据冻结特征, 只累计 sensor_bad
 *   - 加减速相位抑制 SG 判据(SG 在斜坡期天然漂移)
 *   - STALL_CONFIRMED 需多源一致(SG 低于带阈值 + 电流升高 + 持续)
 *   - 恢复带 hysteresis(需连续干净帧)
 *
 * 模式: MONITOR_ONLY(默认, 只判不动作) /
 *       ACTIVE_PROTECTION(severe 边沿 → safety_post_event(EVT_MULTI_FAULT);
 *       未完成真实电机标定前禁止开启, 开启命令有标定门禁)
 *
 * 硬件保护(ESTOP/LIMIT/DIAG)不经过本引擎 —— 仍由 Safety 事件链直通。
 * 诊断结果只通过事件交 Safety, 引擎不直接操作 Flash。
 */
#ifndef DIAGNOSIS_H
#define DIAGNOSIS_H

#include <rtthread.h>
#include "app_health.h"

typedef enum
{
    DIAG_NORMAL = 0,
    DIAG_LOAD_WARNING,
    DIAG_IMPACT,
    DIAG_OVERLOAD,
    DIAG_STALL_SUSPECT,
    DIAG_STALL_CONFIRMED,
    DIAG_SENSOR_FAULT,
} diag_verdict_t;

typedef enum
{
    DIAG_MODE_MONITOR_ONLY = 0,
    DIAG_MODE_ACTIVE_PROTECTION,
} diag_mode_t;

/* 引擎单步输入(由 sensor_frame 提取; selftest 可注入合成值) */
typedef struct
{
    rt_uint32_t seq;            /* 源帧序号(P1-5 dedup 用) */
    rt_uint32_t timestamp;
    rt_uint8_t  sg_valid;       rt_uint16_t sg;          /* 0~1023 */
    rt_uint8_t  cur_valid;      float current_ma;        /* mA */
    rt_uint8_t  imu_valid;      rt_int16_t  vib_mg;      /* 含重力基线 ~1000 */
    rt_uint8_t  motor_state;    /* motor_state_t */
    rt_uint32_t step_hz;
} diag_input_t;

/* 创建 Diagnosis Thread(幂等, prio 9, 100Hz 消费最新帧)。 */
rt_err_t diagnosis_init(void);

/* 引擎单步(线程内部使用; diag_selftest 直接注入合成输入)。 */
diag_verdict_t diag_step(const diag_input_t *in);

/* 清零引擎状态(自检/模式切换前调用)。 */
void diag_reset(void);

/* 模式切换。切 ACTIVE 的门禁: 电流已标定(未标定=理论值, 不得驱动保护)。 */
rt_err_t diagnosis_set_mode(diag_mode_t mode);
diag_mode_t diagnosis_get_mode(void);

diag_verdict_t diagnosis_get_verdict(void);

/* 特征值读取(selftest 用): 任一指针可 NULL */
void diag_get_features(float *sg_filt, float *sg_delta,
                       float *cur_filt, float *cur_delta);
subsys_health_t diagnosis_get_health(void);

/* 帧内振动特征(供 selftest/记录): mg 值的整数幅值 */
rt_uint32_t diag_vib_magnitude(rt_int16_t mg_x, rt_int16_t mg_y, rt_int16_t mg_z);

#endif /* DIAGNOSIS_H */
