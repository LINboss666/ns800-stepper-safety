/*
 * sensor_service.h - 统一传感器采集服务 (Phase 7-B)
 *
 * Sensor Thread: 优先级 7, 初始 100Hz(10ms 周期)。
 * 数据源全部走 Phase 7-A 正式 API(adxl345/current_adc/tmc2209 + motor 快照)。
 * 任一源读取失败: 对应 valid 位清零并保留上次值 —— 禁止写 0 冒充有效测量。
 * TMC SG_RESULT 为 UART 读(约 1ms), 按 1/10 分频(10Hz)采样避免占用线程。
 */
#ifndef SENSOR_SERVICE_H
#define SENSOR_SERVICE_H

#include <rtthread.h>
#include "app_health.h"
#include "safety_io_semantics.h"

typedef struct
{
    rt_uint32_t timestamp;      /* rt_tick_get() */
    /* IMU (adxl345_read_raw) */
    rt_int16_t  ax, ay, az;             /* 原始码 */
    rt_int16_t  vib_mg;                 /* 振动幅值: isqrt(mx²+my²+mz²) mg */
    rt_uint8_t  valid_imu;      /* 1=当前值有效(本次采样成功或保留上次成功值) */
    rt_uint8_t  fresh_imu;      /* 1=本帧实际重新采样(区分保留值) */
    /* M8-R1/R6: IMU 的独立样本身份(与下面 sg_seq/cur_seq 同一口径, 仅本次真的
     * adxl345_read_mg 成功才 +1)与所在帧 tick。碰撞判据必须靠它区分"又采到一次
     * 超阈值"与"上一次撞击的值还留在滑窗里"。仍只占 RAM, 不进黑匣子 Flash 记录。 */
    rt_uint32_t imu_seq;
    rt_uint32_t imu_tick;
    /* 电流 (current_adc) */
    rt_uint32_t current_raw;    /* 最新单次 raw */
    rt_uint32_t current_filtered;       /* EMA */
    float       current_ma;     /* 未标定时为理论换算值(DEGRADED) */
    rt_uint8_t  valid_current;  /* 同上语义 */
    rt_uint8_t  fresh_current;
    /* Fix E: 标定来源三态而不是含糊布尔。取值为 current_adc_cal_source_t:
     *   NONE=无标定 / THEORETICAL=理论默认系数 / MEASURED=实测量值。
     *   旧字段 current_calibrated 的含义是 "source != NONE", 对 THEORETICAL 也为
     *   1, 会被读成"已实测" —— 已废弃。判据请查 current_adc_cal_source()。 */
    rt_uint8_t  current_cal_source;
    /* TMC (tmc2209_read_sg_result, 10Hz 分频) */
    rt_uint16_t sg_result;
    rt_uint8_t  valid_sg;       /* 1=上次 SG 采样有效(10Hz 分频期间保留) */
    rt_uint8_t  fresh_sg;       /* 1=本帧实际重采样 SG */
    /* 运动 (motor_get_snapshot) */
    rt_uint32_t step_hz;
    rt_uint8_t  motor_state;    /* motor_state_t */
    rt_uint8_t  dir;
    /* 安全输入电平 (safety_gpio 直读, 无上拉前浮空值仅供参考)
     * 2026-10-22 数据语义修正: 这颗脚是 **PC.6 = H6-5/TMC_ENN 被动状态反馈**,
     * 不是急停输入。MCU 没有独立的急停 GPIO, 因此:
     *   enn_feedback=HIGH → 功率级处于禁能侧; =LOW → 未施加禁能(**不证明有转矩**);
     *   任何电平都不能用来判断"是哪种原因切断"。旧字段名 estop 已废止。 */
    rt_uint8_t  enn_feedback;
    rt_uint8_t  limit_min;
    rt_uint8_t  limit_max;
    rt_uint8_t  tmc_diag;
    rt_uint8_t  sai_active;     /* 活动输入掩码(SAI_* 位序), 只由配置决定 */
    rt_uint8_t  sai_valid;      /* 本帧真正读到并符合 PIN_LOW/PIN_HIGH 的掩码 */
    /* 聚合标志: active!=0 且 (valid & active)==active。它**不是**保护就绪、
     * 安全电平或线路完整性证明; 限位/ENN 读到"触发态"仍算有效样本。 */
    rt_uint8_t  valid_safety;
    rt_uint32_t seq;            /* 帧序号 */
    /* ---- M8(2026-10-30 相对保护): 源样本身份, 只追加在末尾 ----
     * 之前只有 valid_sg/fresh_sg: 一个 SG 读数一旦被保留, 消费方无法区分
     * "这是第几次独立采样"与"这是上一帧的旧值被重放"。诊断因此不能证明
     * 持续时间是跨**独立样本**建立的。
     *   sg_seq / cur_seq: 该源**成功读取**的累计次数。只在真实成功(SG=UART 读成功,
     *     ADC=current_adc 读成功)时 +1; 分频帧与失败帧都原样继承, 绝不前进。
     *     计数存在帧链里(由调用方持有的基帧携带), 不是文件静态 ⇒ 自检用私有
     *     基帧时结构上不可能推进生产计数(与 Fix E 同一纪律)。
     *   sg_tick / cur_tick: 那次成功读取所在帧的 rt_tick_get(), 用于跨重启
     *     无关的年龄/时间跨度计算(帧 tick 与"诊断当前 tick"必须分开)。
     * 这些字段只在 RAM 帧内; 黑匣子落盘仍是 bb_to_sample() 的固定
     * ns_fault_sample_t 布局, Flash schema 未变。 */
    rt_uint32_t sg_seq;
    rt_uint32_t sg_tick;
    rt_uint32_t cur_seq;
    rt_uint32_t cur_tick;
} sensor_frame_t;

/* 创建 Sensor Thread(幂等)。 */
rt_err_t sensor_service_init(void);

/* 拷贝最新一帧(内部临界区)。 */
rt_err_t sensor_service_get_latest(sensor_frame_t *out);

/* 采集线程健康: OK=线程在跑; FAILED=创建失败; UNINIT=未创建。 */
subsys_health_t sensor_service_get_health(void);

#endif /* SENSOR_SERVICE_H */
