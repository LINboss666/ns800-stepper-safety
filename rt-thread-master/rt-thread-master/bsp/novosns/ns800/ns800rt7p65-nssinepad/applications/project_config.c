/*
 * project_config.c - 运行时配置持久化 (Phase 7-C, Fix B 加固)
 *
 * load 校验: magic + version + 每字段范围(含标定来源)。任一失败 → 安全默认值
 * + DEGRADED(config invalid), 绝不使用半解析数据。
 *
 * Fix B:
 *   - B1 标定来源真正持久化: defaults 显式置 THEORETICAL(而不是 memset 出来的
 *     NONE); 来源纳入范围校验; 下发一律走 current_adc_set_calibration_ex() 并
 *     带上来源, 因此保存为 MEASURED 的标定重启后仍是 MEASURED(旧实现固定走
 *     set_calibration() → 每次 load 都被降级成 THEORETICAL)。
 *   - B1 并发: 所有读写 cfg/cfg_valid/cfg_health 的路径统一在 cfg_lock 内完成;
 *     Flash IO 一律在锁外(save 先在锁内取一致副本, load 先读进局部变量再进锁)。
 *     目的: Diagnosis(prio 9)与 Blackbox(prio 18)每 10ms 取快照时不会读到
 *     半套配置, 也不会把半套配置写进 Flash。
 *
 * 默认阈值为工程占位(未做真实电机标定), 标定流程属实验阶段(HARDWARE-PENDING)。
 */

#include <rtthread.h>
#include "project_config.h"
#include "app_health.h"
#include "ns_storage.h"
#include "current_adc.h"

static project_config_t cfg;
static rt_bool_t cfg_valid = RT_FALSE;      /* 加载/设置是否通过校验 */
static subsys_health_t cfg_health = SUBSYS_UNINIT;
static struct rt_mutex cfg_lock;            /* P1-7 + Fix B: 保护全部 cfg 访问 */
static rt_bool_t cfg_lock_ok = RT_FALSE;

static void cfg_take(void)    { if (cfg_lock_ok) rt_mutex_take(&cfg_lock, RT_WAITING_FOREVER); }
static void cfg_release(void) { if (cfg_lock_ok) rt_mutex_release(&cfg_lock); }

/* ---------- 默认值 ---------- */

/* 无锁内部实现: 只允许已持有 cfg_lock 的调用者使用 */
static void cfg_fill_defaults_nolock(void)
{
    rt_memset(&cfg, 0, sizeof(cfg));
    cfg.magic  = PROJ_CFG_MAGIC;
    cfg.version = PROJ_CFG_VERSION;

    cfg.cur_offset_mv     = 1650.0f;      /* 理论默认, 标定后覆盖 */
    cfg.cur_gain_v_per_a  = 0.6f;
    /* Fix B: 默认值必须自称 THEORETICAL, 不能是 memset 出来的 NONE(0)。
     * NONE 的语义是"没有可用标定", 而这里确实有一组理论换算系数。 */
    cfg.cur_cal_source    = (rt_uint8_t)CURRENT_ADC_CAL_THEORETICAL;

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

    cfg.pre_fault_ms  = 2000;       /* 黑匣子窗口(默认 2s/1s) */
    cfg.post_fault_ms = 1000;
}

void project_config_defaults(void)
{
    cfg_take();
    cfg_fill_defaults_nolock();
    cfg_valid  = RT_FALSE;          /* 未经加载/显式设定 = 按降级处理 */
    cfg_health = SUBSYS_DEGRADED;
    cfg_release();
}

/* ---------- 范围校验 ---------- */

static rt_bool_t config_in_range(const project_config_t *c)
{
    int i;

    if (c->magic != PROJ_CFG_MAGIC || c->version != PROJ_CFG_VERSION)
        return RT_FALSE;
    /* Fix B: 标定来源纳入校验。NONE(0) 视为非法 → 回退安全默认(THEORETICAL),
     * 避免出现"配置说自己是 THEORETICAL 之外的第三种状态却无人解释"的历史数据。 */
    if (c->cur_cal_source < (rt_uint8_t)CURRENT_ADC_CAL_THEORETICAL ||
        c->cur_cal_source > (rt_uint8_t)CURRENT_ADC_CAL_MEASURED)
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
    if (c->pre_fault_ms < 100 || c->pre_fault_ms > 3000 ||
        c->post_fault_ms < 100 || c->post_fault_ms > 1500)
        return RT_FALSE;   /* 与 blackbox 静态缓冲容量一致(300/150 帧) */
    return RT_TRUE;
}

/* 把一份*显式副本*的标定下发到 current_adc(带来源, 不读全局 cfg → 调用方
 * 可在锁外安全使用)。只有合法标定才下发。 */
static void cfg_apply_from(const project_config_t *src)
{
    if (src->cur_gain_v_per_a <= 0.0f) return;
    if (src->cur_cal_source < (rt_uint8_t)CURRENT_ADC_CAL_THEORETICAL ||
        src->cur_cal_source > (rt_uint8_t)CURRENT_ADC_CAL_MEASURED)
        return;
    /* Fix B: 必须用 _ex 带上来源, 否则 MEASURED 会被无条件降级成 THEORETICAL */
    current_adc_set_calibration_ex(src->cur_offset_mv, src->cur_gain_v_per_a,
                                   (current_adc_cal_source_t)src->cur_cal_source);
}

/* ---------- 正式 API (project_config.h) ---------- */

rt_err_t project_config_get_snapshot(project_config_t *out)
{
    if (out == RT_NULL) return -RT_EINVAL;
    cfg_take();
    *out = cfg;                        /* P1-7: 原子快照 */
    cfg_release();
    return RT_EOK;
}

rt_bool_t project_config_is_valid(void)
{
    rt_bool_t v;
    cfg_take();
    v = cfg_valid;
    cfg_release();
    return v;
}

subsys_health_t project_config_get_health(void)
{
    subsys_health_t h;
    cfg_take();
    h = cfg_valid ? SUBSYS_OK : SUBSYS_DEGRADED;
    cfg_release();
    return h;
}

rt_err_t project_config_set(const project_config_t *c)
{
    project_config_t applied;

    if (c == RT_NULL) return -RT_EINVAL;
    if (!config_in_range(c)) return -RT_EINVAL;   /* 非法整套: 不改现有配置 */

    cfg_take();
    cfg = *c;                          /* P1-7: 整套原子发布 */
    cfg_valid  = RT_TRUE;
    cfg_health = SUBSYS_OK;
    applied = cfg;                     /* 锁内取副本, 锁外下发 */
    cfg_release();

    cfg_apply_from(&applied);
    return RT_EOK;
}

rt_err_t project_config_save(void)
{
    project_config_t snap;

    /* Fix B: 先在锁内取一致副本, 再在锁外做 Flash IO —— 既不会把半套配置
     * 写进 Flash, 也不会让 Flash 擦写(毫秒级)阻塞 cfg_lock 的持有者。 */
    cfg_take();
    snap = cfg;
    cfg_release();

    /* ns_params_save: 双副本+CRC, ≤224B; struct ~104B, 预留扩展 */
    return ns_params_save(&snap, sizeof(snap));
}

rt_err_t project_config_load(void)
{
    project_config_t tmp;
    project_config_t applied;
    rt_size_t len = 0;
    rt_bool_t ok;
    rt_err_t e;

    /* Flash IO 完全在锁外: 先读进局部变量 */
    e = ns_params_load(&tmp, sizeof(tmp), &len);
    ok = (e == RT_EOK) && (len == sizeof(tmp)) && config_in_range(&tmp);

    cfg_take();
    if (ok)
    {
        cfg = tmp;
        cfg_valid  = RT_TRUE;
        cfg_health = SUBSYS_OK;
    }
    else
    {
        /* 失败: 回到安全默认(来源=THEORETICAL), 不残留 RAM 里的 MEASURED */
        cfg_fill_defaults_nolock();
        cfg_valid  = RT_FALSE;
        cfg_health = SUBSYS_DEGRADED;
    }
    applied = cfg;                     /* 锁内取副本, 锁外下发 */
    cfg_release();

    cfg_apply_from(&applied);

    if (!ok)
    {
        if (e != RT_EOK)
            rt_kprintf("[CFG] load failed (%d) -> safe defaults (THEORETICAL)\n", e);
        else if (len != sizeof(tmp))
            rt_kprintf("[CFG] load size mismatch (%d != %d) -> safe defaults\n",
                       (int)len, (int)sizeof(tmp));
        else
            rt_kprintf("[CFG] load validation FAILED (magic/version/range/cal-source)"
                       " -> safe defaults\n");
        return (e != RT_EOK) ? e : -RT_EINVAL;
    }

    rt_kprintf("[CFG] loaded OK (version 0x%08X cal_source=%u)\n",
               applied.version, applied.cur_cal_source);
    return RT_EOK;
}

/* ---------- MSH: config_show / config_default / config_save / config_load ---------- */

static void config_show(void)
{
    project_config_t s;
    int i;

    if (project_config_get_snapshot(&s) != RT_EOK)
    { rt_kprintf("[CFG] snapshot failed\n"); return; }

    rt_kprintf("[CFG] valid=%s version=0x%08X\n",
               project_config_is_valid() ? "YES" : "NO(safe defaults)", s.version);
    rt_kprintf("[CFG] cal: offset=%.1f mV gain=%.3f V/A source=%s(%u)\n",
               s.cur_offset_mv, s.cur_gain_v_per_a,
               s.cur_cal_source == CURRENT_ADC_CAL_MEASURED ? "MEASURED" :
               s.cur_cal_source == CURRENT_ADC_CAL_THEORETICAL ? "THEORETICAL" : "NONE",
               (unsigned)s.cur_cal_source);
    rt_kprintf("[CFG] bands: %u / %u Hz\n", s.band_hz[0], s.band_hz[1]);
    for (i = 0; i < 3; ++i)
        rt_kprintf("[CFG] band%d: sg_warn=%d sg_stall=%d cur_warn=%d mA cur_stall=%d mA\n",
                   i, s.sg_warn[i], s.sg_stall[i],
                   (int)s.cur_warn_ma[i], (int)s.cur_stall_ma[i]);
    rt_kprintf("[CFG] vib_impact=%u mg\n", s.vib_impact_mg);
    rt_kprintf("[CFG] persist warn=%u stall=%u impact=%u hyst=%u frames\n",
               s.persist_warn, s.persist_stall, s.impact_frames, s.hysteresis_frames);
    rt_kprintf("[CFG] blackbox pre=%u ms post=%u ms\n", s.pre_fault_ms, s.post_fault_ms);
    rt_kprintf("[CFG] adc runtime cal_source=%d\n", (int)current_adc_cal_source());
}
MSH_CMD_EXPORT(config_show, show runtime configuration);

static void config_default(void)
{
    project_config_t applied;

    project_config_defaults();

    cfg_take();
    cfg_valid  = RT_TRUE;    /* 操作员显式选定默认值作为工作集 */
    cfg_health = SUBSYS_OK;
    applied = cfg;
    cfg_release();

    cfg_apply_from(&applied);
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

rt_err_t project_config_boot(void)
{
    if (cfg_lock_ok) return RT_EOK;    /* P1-8: 幂等 */

    if (rt_mutex_init(&cfg_lock, "cfg", RT_IPC_FLAG_PRIO) != RT_EOK)
        return -RT_ERROR;
    cfg_lock_ok = RT_TRUE;

    project_config_defaults();         /* 安全默认(含 THEORETICAL 来源) */

    /* P1-5: 显式保证 ns_storage_init 先于 config load / blackbox logging,
     * 不依赖偶然的 INIT_APP 链接顺序; 失败 → 安全默认 + DEGRADED */
    if (ns_storage_init() != RT_EOK)
    {
        project_config_t applied;
        rt_kprintf("[CFG] storage init FAILED -> safe defaults, DEGRADED\n");
        cfg_take();
        applied = cfg;
        cfg_release();
        cfg_apply_from(&applied);      /* 显式回 THEORETICAL */
        return RT_EOK;
    }

    project_config_load();          /* 失败→默认+DEGRADED, 成功→OK */
    return RT_EOK;
}
