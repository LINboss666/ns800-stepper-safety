/*
 * project_config.c - 运行时配置持久化 (Phase 7-C)
 *
 * load 校验: magic + version + 每字段范围。任一失败 → 安全默认值 + DEGRADED
 * (config invalid), 绝不使用半解析数据。标定合法时自动下发 current_adc。
 * 默认阈值为工程占位(未做真实电机标定), 标定流程属 Phase 7-D/实验阶段。
 */

#include <rtthread.h>
#include "project_config.h"
#include "app_health.h"
#include "ns_storage.h"
#include "current_adc.h"

static project_config_t cfg;
static rt_bool_t cfg_valid = RT_FALSE;      /* 加载/设置是否通过校验 */
static subsys_health_t cfg_health = SUBSYS_UNINIT;

/* ---------- 默认值 ---------- */

void project_config_defaults(void)
{
    rt_memset(&cfg, 0, sizeof(cfg));
    cfg.magic  = PROJ_CFG_MAGIC;
    cfg.version = PROJ_CFG_VERSION;

    cfg.cur_offset_mv     = 1650.0f;      /* 理论默认, 标定后覆盖 */
    cfg.cur_gain_v_per_a  = 0.6f;

    cfg.band_hz[0] = 200;  cfg.band_hz[1] = 1000;

    cfg.sg_warn[0] =  30;  cfg.sg_warn[1] = 100;  cfg.sg_warn[2] = 200;
    cfg.sg_stall[0] = 15;  cfg.sg_stall[1] =  50; cfg.sg_stall[2] = 100;

    cfg.cur_warn_ma[0]  = 300.f; cfg.cur_warn_ma[1]  =  500.f; cfg.cur_warn_ma[2]  =  800.f;
    cfg.cur_stall_ma[0] = 800.f; cfg.cur_stall_ma[1] = 1200.f; cfg.cur_stall_ma[2] = 1500.f;

    cfg.vib_impact_mg = 2500;

    cfg.persist_warn       = 50;    /* 0.5s @100Hz */
    cfg.persist_stall      = 100;   /* 1.0s */
    cfg.impact_frames      = 2;
    cfg.hysteresis_frames  = 100;   /* 1.0s */

    cfg.pre_fault_ms  = 3000;       /* Phase 7-D 黑匣子窗口 */
    cfg.post_fault_ms = 500;
}

/* ---------- 范围校验 ---------- */

static rt_bool_t config_in_range(const project_config_t *c)
{
    int i;

    if (c->magic != PROJ_CFG_MAGIC || c->version != PROJ_CFG_VERSION)
        return RT_FALSE;
    if (c->cur_gain_v_per_a <= 0.0f || c->cur_gain_v_per_a > 100.0f)
        return RT_FALSE;
    if (c->cur_offset_mv < 0.0f || c->cur_offset_mv > 3300.0f)
        return RT_FALSE;
    if (c->band_hz[0] < 1 || c->band_hz[0] >= c->band_hz[1] ||
        c->band_hz[1] > 20000)
        return RT_FALSE;
    for (i = 0; i < 3; ++i)
    {
        if (c->sg_warn[i] <= c->sg_stall[i] || c->sg_stall[i] < 0 ||
            c->sg_warn[i] > 1023)
            return RT_FALSE;
        if (c->cur_warn_ma[i] <= 0.0f || c->cur_warn_ma[i] >= c->cur_stall_ma[i] ||
            c->cur_stall_ma[i] > 3000.0f)
            return RT_FALSE;
    }
    if (c->vib_impact_mg < 1200 || c->vib_impact_mg > 8000)   /* 必须明显高于 1g 基线 */
        return RT_FALSE;
    if (c->persist_warn < 1 || c->persist_warn > 1000 ||
        c->persist_stall < 1 || c->persist_stall > 2000 ||
        c->persist_stall < c->persist_warn ||
        c->impact_frames < 1 || c->impact_frames > 100 ||
        c->hysteresis_frames < 1 || c->hysteresis_frames > 2000)
        return RT_FALSE;
    if (c->pre_fault_ms > 10000 || c->post_fault_ms > 5000)
        return RT_FALSE;
    return RT_TRUE;
}

/* 应用标定到 current_adc(合法标定才下发) */
static void config_apply_calibration(void)
{
    if (cfg.cur_gain_v_per_a > 0.0f)
        current_adc_set_calibration(cfg.cur_offset_mv, cfg.cur_gain_v_per_a);
}

/* ---------- 正式 API (project_config.h) ---------- */

const project_config_t *project_config_get(void) { return &cfg; }
rt_bool_t project_config_is_valid(void) { return cfg_valid; }
subsys_health_t project_config_get_health(void)
{ return cfg_valid ? SUBSYS_OK : SUBSYS_DEGRADED; }

rt_err_t project_config_set(const project_config_t *c)
{
    if (c == RT_NULL) return -RT_EINVAL;
    if (!config_in_range(c)) return -RT_EINVAL;
    cfg = *c;
    cfg_valid = RT_TRUE;
    if (cfg_health == SUBSYS_UNINIT) cfg_health = SUBSYS_OK;
    else cfg_health = SUBSYS_OK;
    config_apply_calibration();
    return RT_EOK;
}

rt_err_t project_config_save(void)
{
    /* ns_params_save: 双副本+CRC, ≤224B; struct ~110B, 预留扩展 */
    return ns_params_save(&cfg, sizeof(cfg));
}

rt_err_t project_config_load(void)
{
    project_config_t tmp;
    rt_size_t len = 0;
    rt_err_t e;

    /* 先保底默认(校验失败也不裸奔) */
    project_config_defaults();
    cfg_valid = RT_FALSE;

    e = ns_params_load(&tmp, sizeof(tmp), &len);
    if (e != RT_EOK)
    {
        cfg_health = SUBSYS_DEGRADED;
        rt_kprintf("[CFG] load failed (%d) -> safe defaults\n", e);
        return e;
    }
    if (len != sizeof(tmp))
    {
        cfg_health = SUBSYS_DEGRADED;
        rt_kprintf("[CFG] load size mismatch (%d != %d) -> safe defaults\n",
                   (int)len, (int)sizeof(tmp));
        return -RT_EINVAL;
    }
    if (!config_in_range(&tmp))
    {
        cfg_health = SUBSYS_DEGRADED;
        rt_kprintf("[CFG] load validation FAILED (magic/version/range) -> safe defaults\n");
        return -RT_EINVAL;
    }

    cfg = tmp;
    cfg_valid = RT_TRUE;
    cfg_health = SUBSYS_OK;
    config_apply_calibration();
    rt_kprintf("[CFG] loaded OK (version 0x%08X)\n", cfg.version);
    return RT_EOK;
}

/* ---------- MSH: config_show / config_default / config_save / config_load ---------- */

static void config_show(void)
{
    int i;
    rt_kprintf("[CFG] valid=%s health=%s version=0x%08X\n",
               cfg_valid ? "YES" : "NO(safe defaults)",
               subsys_health_name(cfg_health), cfg.version);
    rt_kprintf("[CFG] cal: offset=%.1f mV gain=%.3f V/A\n",
               cfg.cur_offset_mv, cfg.cur_gain_v_per_a);
    rt_kprintf("[CFG] bands: %u / %u Hz\n", cfg.band_hz[0], cfg.band_hz[1]);
    for (i = 0; i < 3; ++i)
        rt_kprintf("[CFG] band%d: sg_warn=%d sg_stall=%d cur_warn=%d mA cur_stall=%d mA\n",
                   i, cfg.sg_warn[i], cfg.sg_stall[i],
                   (int)cfg.cur_warn_ma[i], (int)cfg.cur_stall_ma[i]);
    rt_kprintf("[CFG] vib_impact=%u mg\n", cfg.vib_impact_mg);
    rt_kprintf("[CFG] persist warn=%u stall=%u impact=%u hyst=%u frames\n",
               cfg.persist_warn, cfg.persist_stall, cfg.impact_frames,
               cfg.hysteresis_frames);
    rt_kprintf("[CFG] blackbox pre=%u ms post=%u ms\n",
               cfg.pre_fault_ms, cfg.post_fault_ms);
}
MSH_CMD_EXPORT(config_show, show runtime configuration);

static void config_default(void)
{
    project_config_defaults();
    cfg_valid = RT_TRUE;    /* 默认值本身必然合法 */
    cfg_health = SUBSYS_OK;
    config_apply_calibration();
    rt_kprintf("[CFG] defaults restored (RAM; use config_save to persist)\n");
}
MSH_CMD_EXPORT(config_default, restore safe default configuration in RAM);

static void config_save(void)
{
    rt_err_t e = project_config_save();
    rt_kprintf("[CFG] save: %s\n", e == RT_EOK ? "OK" : "FAILED");
}
MSH_CMD_EXPORT(config_save, persist current configuration to flash);

static void config_load(void)
{
    project_config_load();
}
MSH_CMD_EXPORT(config_load, load configuration from flash with validation);

/* ---------- 初始化 ---------- */

int project_config_init(void)
{
    project_config_defaults();
    cfg_health = SUBSYS_DEGRADED;   /* 加载成功前按降级处理 */
    project_config_load();          /* 失败→默认+DEGRADED, 成功→OK */
    return RT_EOK;
}
INIT_APP_EXPORT(project_config_init);
