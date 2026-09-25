/*
 * diagnosis.h - 多源异常诊断引擎 (Phase 7-C, Fix E 引擎上下文化)
 *
 * 输入: 统一 diag_input_t(从 sensor_frame_t + motor 快照提取)。
 * 特征: SG 滤波/Δ、电流滤波/Δ、振动 RMS/峰值(滑动窗)、速度分带、加减速相位。
 * 输出: NORMAL / LOAD_WARNING / IMPACT / OVERLOAD /
 *       STALL_SUSPECT / STALL_CONFIRMED / SENSOR_FAULT
 *
 * 状态归属(Fix E): 引擎状态不再是一堆文件静态, 而是可实例化的上下文 ——
 *   生产上下文由 Diagnosis Thread 独占推进; diag_selftest 用自己栈上的独立
 *   上下文(may_report=FALSE)。因此合成输入不会被真实帧流污染, 也不会因为
 *   自检喂出 STALL_CONFIRMED 就真的 post EVT_MULTI_FAULT 把系统打掉。
 *
 * 反误报纪律:
 *   - 单个 sample 永不直接判 severe(persistence 计数)
 *   - 同一源帧序号只处理一次(P1-5/Fix B: 去重在引擎步骤内, 不在线程里,
 *     因此自检直接调引擎也能覆盖这一层)
 *   - SG 阈值按速度分带(低速 SG 不可靠 → 阈值收敛)
 *   - sensor missing ≠ 0: 无效数据冻结特征, 只累计 sensor_bad
 *   - 加减速相位抑制 SG 判据(SG 在斜坡期天然漂移)
 *   - STALL_CONFIRMED 需多源一致(SG 低于带阈值 + 电流升高 + 持续)
 *   - 恢复带 hysteresis(需连续干净帧)
 *
 * 模式: MONITOR_ONLY(默认, 只判不动作) /
 *       ACTIVE_PROTECTION(severe 边沿 → safety_post_event(EVT_MULTI_FAULT);
 *       标定来源必须实测(MEASURED)才允许开启, 开启命令有标定门禁)
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

/* 引擎单步输入(由 sensor_frame 提取; 合成注入也必须填 seq) */
typedef struct
{
    rt_uint32_t seq;            /* 源帧序号(引擎内 dedup 用; 0=不提供序号, 不去重) */
    rt_uint32_t timestamp;
    rt_uint8_t  sg_valid;       rt_uint16_t sg;          /* 0~1023 */
    rt_uint8_t  cur_valid;      float current_ma;        /* mA */
    rt_uint8_t  imu_valid;      rt_int16_t  vib_mg;      /* 含重力基线 ~1000 */
    rt_uint8_t  motor_state;    /* motor_state_t */
    rt_uint32_t step_hz;
} diag_input_t;

/* 创建 Diagnosis Thread(幂等, prio 9, 100Hz 消费最新帧)。 */
rt_err_t diagnosis_init(void);

/* ⚠ Fix E: 下面三个函数都作用于"生产上下文"(Diagnosis Thread 正在推进的那份
 * 引擎状态)。它们会因 ACTIVE 模式下的 severe 判定而 post 真实 Safety 事件。
 * 因此 diag_selftest 不再使用它们 —— 自检改用 .c 内的独立上下文
 * (may_report=FALSE), 以免合成输入被真实帧流污染、或误发停机事件。
 * 新代码若要加测试, 请同样走独立上下文, 不要调这三个包装。 */
diag_verdict_t diag_step(const diag_input_t *in);

/* 清零生产引擎状态(模式切换前调用; 自检请用 ctx 版)。 */
void diag_reset(void);

/* 模式切换。切 ACTIVE 的门禁: 电流标定来源必须是 MEASURED
 * (理论默认值 THEORETICAL 不得驱动保护动作)。 */
rt_err_t diagnosis_set_mode(diag_mode_t mode);
diag_mode_t diagnosis_get_mode(void);

diag_verdict_t diagnosis_get_verdict(void);

/* 生产上下文的特征值读取(诊断/记录用): 任一指针可 NULL */
void diag_get_features(float *sg_filt, float *sg_delta,
                       float *cur_filt, float *cur_delta);
subsys_health_t diagnosis_get_health(void);

/* 振动幅值(纯函数, 无状态): mg 三轴合成幅值 */
rt_uint32_t diag_vib_magnitude(rt_int16_t mg_x, rt_int16_t mg_y, rt_int16_t mg_z);

#endif /* DIAGNOSIS_H */
