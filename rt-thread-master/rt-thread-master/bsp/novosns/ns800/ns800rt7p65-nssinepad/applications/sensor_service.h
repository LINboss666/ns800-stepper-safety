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

typedef struct
{
    rt_uint32_t timestamp;      /* rt_tick_get() */
    /* IMU (adxl345_read_raw) */
    rt_int16_t  ax, ay, az;             /* 原始码 */
    rt_int16_t  vib_mg;                 /* 振动幅值: isqrt(mx²+my²+mz²) mg */
    rt_uint8_t  valid_imu;
    /* 电流 (current_adc) */
    rt_uint32_t current_raw;    /* 最新单次 raw */
    rt_uint32_t current_filtered;       /* EMA */
    float       current_ma;     /* 未标定时为理论换算值(DEGRADED) */
    rt_uint8_t  valid_current;
    rt_uint8_t  current_calibrated;
    /* TMC (tmc2209_read_sg_result, 10Hz 分频) */
    rt_uint16_t sg_result;
    rt_uint8_t  valid_sg;
    /* 运动 (motor_get_snapshot) */
    rt_uint32_t step_hz;
    rt_uint8_t  motor_state;    /* motor_state_t */
    rt_uint8_t  dir;
    /* 安全输入电平 (safety_gpio 直读, 无上拉前浮空值仅供参考) */
    rt_uint8_t  estop;
    rt_uint8_t  limit_min;
    rt_uint8_t  limit_max;
    rt_uint8_t  tmc_diag;
    rt_uint8_t  valid_safety;
    rt_uint32_t seq;            /* 帧序号 */
} sensor_frame_t;

/* 创建 Sensor Thread(幂等)。 */
rt_err_t sensor_service_init(void);

/* 拷贝最新一帧(内部临界区)。 */
rt_err_t sensor_service_get_latest(sensor_frame_t *out);

/* 采集线程健康: OK=线程在跑; FAILED=创建失败; UNINIT=未创建。 */
subsys_health_t sensor_service_get_health(void);

#endif /* SENSOR_SERVICE_H */
