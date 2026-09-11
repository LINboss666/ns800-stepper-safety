/*
 * safety_state.h - 安全状态机与黑匣子帧定义 (Phase 7 骨架)
 */
#ifndef SAFETY_STATE_H
#define SAFETY_STATE_H

#include <rtthread.h>

/* 状态机: 方案 docx §12 + 交接文档 §18 */
typedef enum
{
    SAFETY_BOOT = 0,
    SAFETY_INIT,
    SAFETY_SELF_TEST,
    SAFETY_READY,          /* 条件满足, 电机仍禁止 */
    SAFETY_RUN,            /* 仅显式运动命令后进入 */
    SAFETY_LOAD_WARNING,   /* 单源弱异常, 可继续观察 */
    SAFETY_ABNORMAL,       /* 多源一致异常, 准备停机 */
    SAFETY_FAULT_LATCHED,  /* 锁死, 禁止自动重启 */
    SAFETY_ESTOP,          /* 急停 */
    SAFETY_MANUAL_CLEAR,   /* 人工确认后清除, 需重新 arm */
} safety_state_t;

/* 故障码 */
#define FAULT_NONE          0u
#define FAULT_ESTOP         1u
#define FAULT_LIMIT_MIN     2u
#define FAULT_LIMIT_MAX     3u
#define FAULT_TMC_DIAG      4u      /* StallGuard2/DIAG 硬件堵转信号 */
#define FAULT_MULTI_SOURCE  5u      /* 多源一致异常(SG+电流+振动) */
#define FAULT_TMC_COMM      6u      /* TMC2209 通信丢失 */
#define FAULT_IMU_COMM      7u
#define FAULT_SELF_TEST     8u

/* 事件位(rt_event) */
#define EVT_ESTOP        (1u << 0)
#define EVT_LIMIT_MIN    (1u << 1)
#define EVT_LIMIT_MAX    (1u << 2)
#define EVT_TMC_DIAG     (1u << 3)
#define EVT_MULTI_FAULT  (1u << 4)
#define EVT_FAULT_CLEAR  (1u << 5)

/* 黑匣子采样帧(方案 docx §13; RAM 环形缓冲用, Flash 持久化走 ns_storage) */
struct sample_frame
{
    rt_uint32_t tick;         /* rt_tick_get() */
    rt_int16_t  ax, ay, az;   /* ADXL345 原始值 */
    rt_uint16_t current_raw;  /* ADC 原始值 */
    rt_int32_t  current_ma;   /* 换算值(标定后) */
    rt_uint16_t sg_result;    /* TMC2209 SG_RESULT */
    rt_uint32_t step_hz;      /* 当前 STEP 频率 */
    rt_uint8_t  dir;
    rt_uint8_t  limit_min : 1;
    rt_uint8_t  limit_max : 1;
    rt_uint8_t  estop     : 1;
    rt_uint8_t  tmc_diag  : 1;
    rt_uint8_t  reserved  : 4;
    rt_uint16_t state;        /* safety_state_t */
    rt_uint16_t fault_code;
};

const char    *safety_state_name(safety_state_t s);
rt_err_t       safety_post_event(rt_uint32_t event);
void           safety_force_shutdown(rt_uint32_t code);  /* 统一安全停机入口(BUG-011-1) */
void           safety_enter_fault(rt_uint32_t code);     /* 兼容别名, 内部走 force_shutdown */
safety_state_t safety_state_get(void);
rt_uint32_t    safety_fault_get(void);

#endif /* SAFETY_STATE_H */
