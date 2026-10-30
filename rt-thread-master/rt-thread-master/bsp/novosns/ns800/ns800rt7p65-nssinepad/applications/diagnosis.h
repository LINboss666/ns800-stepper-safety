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
    /* M8(2026-10-30): 并行新增, 不改上面两个值的含义, 也不放宽 ACTIVE 的
     * MEASURED 门禁。RELATIVE 用 raw 码带与 SG 相对变化判异常, 因此它不要求
     * 电流标定来源是 MEASURED; 但它也不得对外宣称是"已验证的安培限流"。 */
    DIAG_MODE_RELATIVE_PROTECTION,
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
    /* ---- M8 追加(只在末尾追加, 上面字段次序与含义不变) ----
     * 源样本身份与年龄判定所需; 相对引擎**必须**能区分"新帧携带旧样本"与
     * "新样本", 否则"持续 500ms 且 ≥3 个独立 SG"是假的。
     *   sg_seq/cur_seq: 来自 sensor_frame 的同名计数(仅真实成功读取才 +1)。
     *   sg_tick/cur_tick: 那次成功读取所在帧的 tick。
     *   cur_raw: 与 cur_valid 同一次采样的 12bit ADC 原始码(相对判据用它, 不用 mA)。
     *   dir: motor_snapshot.dir(0/1), 工况门用。
     *   M8-R1/R6 追加 imu_seq/imu_tick: "IMU 真的采到了"的身份与时刻 —— 碰撞判据只认
     *   新样本, 16 帧滑窗里留着的旧 peak 不构成"又撞了一次"。 */
    rt_uint32_t sg_seq;
    rt_uint32_t sg_tick;
    rt_uint32_t cur_seq;
    rt_uint32_t cur_tick;
    rt_uint32_t cur_raw;
    rt_uint32_t imu_seq;
    rt_uint32_t imu_tick;
    rt_uint8_t  dir;
    /* M8-R1/R2.2: "运动事实是否真的取到了"。motor_state_t 的 0 就是 MOTOR_IDLE,
     * 所以零初始化的输入看起来完全"静止" —— 取帧失败又取不到 motor 快照时, 必须
     * 用这个位说明**不知道**在不在动, 引擎才敢把覆盖丢失算成降险义务。 */
    rt_uint8_t  motion_known;
    /* 调用方按**当前**驱动配置+工况+采样定义算出的身份签名(与 profile.signature
     * 比较即知是否还在被批准的那个工况里)。0 = 未提供。 */
    rt_uint32_t live_signature;
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

/* M8: "CONFIRM_EXPERIMENTAL" 的一次性凭据入口 —— 只允许由 diag_mode CLI 在操作员
 * 显式写出该词时置位; diagnosis_set_mode() 用掉即清。不给任何隐式放行路径。 */
void diagnosis_take_relative_confirm(void);

diag_verdict_t diagnosis_get_verdict(void);

/* 生产上下文的特征值读取(诊断/记录用): 任一指针可 NULL */
void diag_get_features(float *sg_filt, float *sg_delta,
                       float *cur_filt, float *cur_delta);
subsys_health_t diagnosis_get_health(void);

/* 振动幅值(纯函数, 无状态): mg 三轴合成幅值 */
rt_uint32_t diag_vib_magnitude(rt_int16_t mg_x, rt_int16_t mg_y, rt_int16_t mg_z);

#endif /* DIAGNOSIS_H */
