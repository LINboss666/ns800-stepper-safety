/*
 * project_config.h - 运行时配置持久化 (Phase 7-C, Fix B 加固)
 *
 * 存储: ns_params_save/load(双副本 4KiB + CRC + commit, ≤224B, 应用自管 schema)。
 * 结构: magic + version + 校准(含来源) + 速度分带 + SG/电流/振动阈值 +
 *       persistence + 滞回 + 黑匣子预/后故障时间。
 *
 * 纪律: load 时 magic/version/范围(含标定来源)多重校验, 任一失败 → 安全默认值 +
 *       config invalid(DEGRADED), 绝不带病使用未知参数。
 *       Fix B: 标定来源(cur_cal_source)真正生效 —— defaults 恒为 THEORETICAL,
 *       保存为 MEASURED 的标定重启后仍是 MEASURED(下发走
 *       current_adc_set_calibration_ex, 不再被无条件降级成 THEORETICAL)。
 *       全部 cfg 读写在内部互斥内完成; Flash IO 一律在锁外。
 */
#ifndef PROJECT_CONFIG_H
#define PROJECT_CONFIG_H

#include <rtthread.h>
#include "app_health.h"

#define PROJ_CFG_MAGIC     0x4E533343u      /* "NS3C" */
#define PROJ_CFG_VERSION   0x00010002u

typedef struct
{
    rt_uint32_t magic;
    rt_uint32_t version;
    /* 电流标定(P1-4 字段 + Fix B 生效): 来源随配置持久化、参与校验、load 还原。
     * 合法取值仅 THEORETICAL / MEASURED(NONE 视为非法 → 回退安全默认)。 */
    float cur_offset_mv;                 /* 零电流电压 mV */
    float cur_gain_v_per_a;              /* V→A 系数 */
    rt_uint8_t cur_cal_source;           /* current_adc_cal_source_t */
    /* 速度分带边界(Hz) */
    rt_uint32_t band_hz[2];
    /* SG 阈值(每速度带; 低于 warn=负载抬升, 低于 stall=堵转特征) */
    rt_int32_t  sg_warn[3];
    rt_int32_t  sg_stall[3];
    /* 电流阈值(mA, 每速度带) */
    float cur_warn_ma[3];
    float cur_stall_ma[3];
    /* 振动(mg, 含重力基线) */
    rt_uint32_t vib_impact_mg;
    /* persistence(帧, 100Hz) */
    rt_uint32_t persist_warn;
    rt_uint32_t persist_stall;
    rt_uint32_t impact_frames;
    rt_uint32_t hysteresis_frames;
    /* 黑匣子预/后故障时间(Phase 7-D 使用, 本阶段仅存储) */
    rt_uint32_t pre_fault_ms;
    rt_uint32_t post_fault_ms;
} project_config_t;

/* P1-8: bootstrap 显式调用(幂等, 含 ns_storage_init 前置)。 */
rt_err_t project_config_boot(void);

/* P1-7: 原子快照(Diagnosis/Blackbox 用; 不暴露可被并发修改的指针) */
rt_err_t project_config_get_snapshot(project_config_t *out);

/* 设置并校验(范围非法拒绝, 不改现有)。 */
rt_err_t project_config_set(const project_config_t *cfg);

/* 保存当前配置到 Flash。 */
rt_err_t project_config_save(void);

/* 从 Flash 加载(含校验; 失败→安全默认+DEGRADED)。 */
rt_err_t project_config_load(void);

/* 恢复默认值(RAM, 不自动保存)。 */
void project_config_defaults(void);

/* 配置有效性: DEGRADED = 曾加载失败, 当前为安全默认 */
rt_bool_t project_config_is_valid(void);
subsys_health_t project_config_get_health(void);

#endif /* PROJECT_CONFIG_H */
