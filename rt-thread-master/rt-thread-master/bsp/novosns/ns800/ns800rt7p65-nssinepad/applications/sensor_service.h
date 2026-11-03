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
    /* ---- M8-R2 R2-B3: 运动事实自己的身份与年龄(只追加在末尾, RAM 帧内) ----
     * motor_get_snapshot() 失败时 motor_state/step_hz/dir 是**继承**来的旧值,
     * 而旧代码把"外壳帧是新的"当成"运动事实是新的" ⇒ 缓存的 IDLE 变成无限的
     * 免保护证据。现在:
     *   motion_ok  : 1 = 本帧确实取到了一份运动快照; 0 = 只有继承值, 事实未知。
     *   motion_seq : 成功取到运动的累计次数(失败帧原样继承, 绝不前进)。
     *   motion_tick: 那次成功所在帧的 tick, 用于年龄判定。
     * 与 sg_seq/cur_seq 完全同一口径: 保留值不是新样本。 */
    rt_uint8_t  motion_ok;
    rt_uint32_t motion_seq;
    rt_uint32_t motion_tick;
} sensor_frame_t;

/* 创建 Sensor Thread(幂等)。 */
rt_err_t sensor_service_init(void);

/* ---------- R7F1(2026-11-03 任务书 §3): 启动期 SG 门 ----------
 * 事实(supervisor.c / sensor_service.c / tmc2209.c 三处对齐读过):
 *   stage 7 就 rt_thread_startup 起 "sensor"(prio 7), 而 required 自检在 stage 11;
 *   s_seq 从 0 起, 所以**首帧**就命中 SG 分频(seq 对 SENSOR_SG_DIV 取模为 0);
 *   后台 SG 读走 tmc2209_read_sg_result(), 与自检那一次 IOIN 读共用同一把
 *   tmc_xfer_lock, 而自检那次取锁只等 TMC_BUD_XFER_LOCK_MS(=20ms)就拿不到 =>
 *   回 -RT_EBUSY(-23), required 失败并锁存 FAULT_SELF_TEST —— 2026-11-03 现场 S3
 *   留下的正是这个形状。
 * 本门只压"到 stage 11 返回为止"这一段:
 *   - 只让 SG 这一路的 UART 读不发; IMU / ADC / 限位与 ENN 反馈 / 运动快照每帧照采
 *     —— 自检的 IMU/ADC 分支与保护输入都不经过这一路, 不被本门影响;
 *   - 门由 supervisor 在 stage 11 **返回之后**打开, 自检 PASS 还是 FAIL 都打开,
 *     所以 SG 不是被永久禁用;
 *   - 门关闭期间那一帧走的是既有"非分频帧"语义: fresh_sg=0, sg_result/valid_sg 继承
 *     基帧, sg_seq/sg_tick 不推进(M8 的身份口径: 只有真读成功才推进)。
 * ⚠ 本门不覆盖 msh system_selftest(supervisor.c:403)重跑自检那一类**运行中**的再入:
 *   那时门早已打开, 后台 SG 仍可能与那次读竞争。那是既有行为, 本轮不改, 由 §2 的逐调用
 *   归因在现场区分(取锁失败 tx_written=0 / 链路无应答 tx_written=4)。 */
void      sensor_service_sg_startup_release(void);   /* 打开门: 允许 SG 分频读 */
rt_bool_t sensor_service_sg_startup_gated(void);     /* 只读观察: 门当前是否还关着 */

/* 拷贝最新一帧(内部临界区)。 */
rt_err_t sensor_service_get_latest(sensor_frame_t *out);

/* 采集线程健康: OK=线程在跑; FAILED=创建失败; UNINIT=未创建。 */
subsys_health_t sensor_service_get_health(void);

#endif /* SENSOR_SERVICE_H */
