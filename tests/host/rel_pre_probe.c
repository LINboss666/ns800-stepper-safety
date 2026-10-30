/*
 * rel_pre_probe.c - M8 开工前的"修前基线观察"复现探针(离线, 无设备)
 *
 * 目的(任务书 §8 的修 Bug 顺序: 已知正常版本/路径 → 同输入复现 → 相关 diff → 最小改动
 * → 直接复验 → 针对性回归): 在**精确 f1aa2e8 的 applications 副本**上, 用与 M8 主机链
 * 同一套输入形状, 逐条复现 CP0 功能表登记的三项基线观察, 把"修前是什么样"固定成
 * 可重放的机器判据, 而不是事后口头描述。
 *
 *   M8-PRE-1  基线没有"源年龄"这一判据: 一个 sg_valid=1 但已经陈旧 5s 的样本, 在
 *             旧引擎里永远得到"有效样本"的待遇, 判据只看 valid 位, 不看样本是新是旧。
 *   M8-PRE-2  基线没有单源严重出口: SG 崩到 stall 阈值以下 + 电流不升, 3000 帧内
 *             verdict 永远到不了 STALL_CONFIRMED/OVERLOAD(两者都要求双计数同时成立),
 *             而 ACTIVE 模式的开启门禁又要求 MEASURED 标定 —— 未实测标定时没有任何
 *             模式能对这个场景采取行动。
 *   M8-PRE-3  基线的 severe 边沿是**先打印后保护**(diag_report_severe 里 kprintf 在
 *             safety_post_event 之前)。打印走控制台, 保护是同步短停机。
 *
 * 本夹具真编译并执行基线 applications/diagnosis.c(其余依赖是显式可注入桩)。
 * argv[1] 只影响 M8-PRE-3 的期望方向: "baseline"=期望复现先打印; "fixed"=期望已改成
 * 先保护。PRE-1/PRE-2 是对旧链**既有行为**的刻画, M8 按任务书要求不改它, 因此两个
 * 树下都应当成立(它们不是"缺陷", 是 M8 另起一条并行模式所要补的**能力缺口**)。
 *
 * 台账: CASE|<id>|PASS|FAIL|detail + SUMMARY; rc 0=通过 1=有 FAIL 4=基础设施故障。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <stdio.h>
#include <string.h>
#include <setjmp.h>

#include "app_health.h"
#include "project_board.h"
#include "safety_gpio.h"
#include "safety_state.h"
#include "sensor_service.h"
#include "motor.h"
#include "current_adc.h"
#include "project_config.h"
#include "diagnosis.h"

/* 被测 TU: 基线(或当前树)的真实 diagnosis.c */
#include "diagnosis.c"

/* ======================= 捕获 + 保护次序记账 ======================= */

#define CAP_BUF 65536
static char   g_cap[CAP_BUF];
static size_t g_cap_len;
static int    g_post_calls;
static size_t g_post_cap_len;
static int    g_post_severe_lines_at_call;
static rt_err_t g_post_rc = RT_EOK;

int rel_kprintf(const char *fmt, ...)
{
    va_list ap;
    char tmp[512];
    int n;
    size_t l;

    va_start(ap, fmt);
    n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (n < 0) return 0;
    fputs(tmp, stdout);
    l = strlen(tmp);
    if (g_cap_len + l + 1u < CAP_BUF) { memcpy(g_cap + g_cap_len, tmp, l); g_cap_len += l; g_cap[g_cap_len] = 0; }
    return n;
}
void rel_capture_reset(void) { g_cap[0] = 0; g_cap_len = 0; }
const char *rel_capture_text(void) { return g_cap; }

static int cap_count(const char *needle)
{
    int c = 0;
    const char *p = g_cap;
    while ((p = strstr(p, needle)) != RT_NULL) { c++; p += strlen(needle); }
    return c;
}

rt_err_t safety_post_event(rt_uint32_t event)
{
    (void)event;
    g_post_calls++;
    g_post_cap_len = g_cap_len;
    g_post_severe_lines_at_call = cap_count("[DIAG] ACTIVE: severe");
    return g_post_rc;
}

/* ======================= 虚拟时钟(与主链同一口径) ======================= */

static rt_tick_t g_now = 1000u;
rt_tick_t rel_tick_get(void) { return g_now; }
void rel_mdelay(rt_int32_t ms) { g_now += (rt_tick_t)(ms > 0 ? ms : 1); }

/* ======================= 依赖桩 ======================= */

static project_config_t g_cfg;
static current_adc_cal_source_t g_cal = CURRENT_ADC_CAL_THEORETICAL;

rt_err_t project_config_get_snapshot(project_config_t *out)
{
    if (!out) return -RT_EINVAL;
    *out = g_cfg;
    return RT_EOK;
}
rt_err_t sensor_service_get_latest(sensor_frame_t *out)
{
    if (!out) return -RT_EINVAL;
    memset(out, 0, sizeof(*out));
    out->seq = 1;
    return RT_EOK;
}
current_adc_cal_source_t current_adc_cal_source(void) { return g_cal; }
rt_bool_t current_adc_is_calibrated(void) { return g_cal == CURRENT_ADC_CAL_MEASURED; }
subsys_health_t current_adc_get_health(void) { return SUBSYS_DEGRADED; }
subsys_health_t adxl345_get_health(void) { return SUBSYS_OK; }
subsys_health_t tmc2209_get_health(void) { return SUBSYS_OK; }
subsys_health_t motor_get_health(void) { return SUBSYS_OK; }
subsys_health_t sensor_service_get_health(void) { return SUBSYS_OK; }
rt_err_t motor_get_snapshot(motor_snapshot_t *s) { if (!s) return -RT_EINVAL; memset(s, 0, sizeof(*s)); return RT_EOK; }
safety_state_t safety_state_get(void) { return SAFETY_READY; }
rt_base_t safety_pin(const char *n) { (void)n; return 40; }
rt_base_t rt_pin_get(const char *n) { (void)n; return 40; }
rt_base_t rt_pin_read(rt_base_t p) { (void)p; return PIN_HIGH; }
void rt_pin_write(rt_base_t p, rt_base_t v) { (void)p; (void)v; }
void rt_pin_mode(rt_base_t p, rt_base_t m) { (void)p; (void)m; }
rt_err_t rt_pin_attach_irq(rt_base_t p, rt_uint32_t m, void (*cb)(void *), void *a)
{ (void)p; (void)m; (void)cb; (void)a; return RT_EOK; }
rt_err_t rt_pin_irq_enable(rt_base_t p, rt_uint32_t e) { (void)p; (void)e; return RT_EOK; }
rt_thread_t rt_thread_create(const char *n, void (*e)(void *), void *p, int s, rt_uint8_t pr, rt_uint32_t t)
{ (void)n; (void)e; (void)p; (void)s; (void)pr; (void)t; return RT_NULL; }
rt_err_t rt_thread_startup(rt_thread_t t) { (void)t; return RT_EOK; }
rt_err_t rt_thread_delete(rt_thread_t t) { (void)t; return RT_EOK; }
void rt_enter_critical(void) {}
void rt_exit_critical(void) {}

/* 工程占位阈值(与 project_config 默认值一致): 探针必须用**真配置**跑判据 */
static void cfg_fill(void)
{
    memset(&g_cfg, 0, sizeof(g_cfg));
    g_cfg.magic = PROJ_CFG_MAGIC;
    g_cfg.version = PROJ_CFG_VERSION;
    g_cfg.cur_offset_mv = 1650.0f;
    g_cfg.cur_gain_v_per_a = 0.6f;
    g_cfg.cur_cal_source = (rt_uint8_t)CURRENT_ADC_CAL_THEORETICAL;
    g_cfg.band_hz[0] = 200;  g_cfg.band_hz[1] = 1000;
    g_cfg.sg_warn[0] = 30;   g_cfg.sg_warn[1] = 100;  g_cfg.sg_warn[2] = 200;
    g_cfg.sg_stall[0] = 15;  g_cfg.sg_stall[1] = 50;  g_cfg.sg_stall[2] = 100;
    g_cfg.cur_warn_ma[0] = 300.f;  g_cfg.cur_warn_ma[1] = 500.f;  g_cfg.cur_warn_ma[2] = 800.f;
    g_cfg.cur_stall_ma[0] = 800.f; g_cfg.cur_stall_ma[1] = 1200.f; g_cfg.cur_stall_ma[2] = 1500.f;
    g_cfg.vib_impact_mg = 2500;
    g_cfg.persist_warn = 50; g_cfg.persist_stall = 100;
    g_cfg.impact_frames = 2; g_cfg.hysteresis_frames = 100;
    g_cfg.pre_fault_ms = 2000; g_cfg.post_fault_ms = 1000;
}

/* ======================= 台账 ======================= */

static int g_pass, g_fail, g_checks, g_bad;
#define CHECK(cond, ...) do { g_checks++; if (!(cond)) { g_bad++; printf("    ! "); printf(__VA_ARGS__); printf("\n"); } } while (0)
static void case_begin(void) { g_checks = 0; g_bad = 0; rel_capture_reset(); }
static void case_end(const char *id)
{
    if (g_bad == 0) { g_pass++; printf("CASE|%s|PASS|checks=%d\n", id, g_checks); }
    else            { g_fail++; printf("CASE|%s|FAIL|checks=%d bad=%d\n", id, g_checks, g_bad); }
}

static void feed(diag_engine_t *e, rt_uint16_t sg, float ma, rt_int16_t vib,
                 rt_uint8_t st, rt_uint32_t hz, rt_uint8_t sg_valid, rt_uint32_t *seq)
{
    diag_input_t in;
    memset(&in, 0, sizeof(in));
    in.seq = ++(*seq);
    in.timestamp = g_now;
    in.sg_valid = sg_valid; in.sg = sg;
    in.cur_valid = 1; in.current_ma = ma;
    in.imu_valid = 1; in.vib_mg = vib;
    in.motor_state = st; in.step_hz = hz;
    diag_engine_step(e, &in);
}

int main(int argc, char **argv)
{
    const char *tree = (argc > 1) ? argv[1] : "baseline";
    diag_engine_t t;
    diag_verdict_t v;
    rt_uint32_t seq;
    int i;

    if (strcmp(tree, "baseline") != 0) {
        printf("INFRA|TREE-LABEL|%s\n", tree);
        printf("SUMMARY|cases=0 pass=0 fail=0 pre_bad=1 executed=0\n");
        return 4;
    }
    cfg_fill();
    diag_sync_config();      /* 阈值必须真同步: 否则 dt 全 0, sensor_bad_cnt>=persist_warn
                              * 恒成立, 探针会把"配置没加载"误当成基线的年龄判据 */

    printf("PROBE|tree=%s|diag_input_t_size=%u|sensor_frame_size=%u\n",
           tree, (unsigned)sizeof(diag_input_t), (unsigned)sizeof(sensor_frame_t));

    /* ---- M8-PRE-1: 有效但陈旧的样本没有任何年龄判据 ---- */
    case_begin();
    memset(&t, 0, sizeof(t));
    diag_engine_reset(&t);
    seq = 0;
    for (i = 0; i < 500; ++i)
    {
        g_now += 10u;                        /* 5 秒的墙上时间过去 */
        feed(&t, 30, 150.0f, 1000, (rt_uint8_t)MOTOR_CRUISE, 500, 1, &seq);
        CHECK(t.st.verdict != DIAG_SENSOR_FAULT,
              "第 %d 帧就报了 SENSOR_FAULT(基线不该有年龄判据)", i);
    }
    CHECK(t.st.sensor_bad_cnt == 0, "陈旧样本被计成了 bad=%u", t.st.sensor_bad_cnt);
    CHECK(t.st.verdict == DIAG_STALL_SUSPECT,
          "5s 陈旧 + SG 崩的结论=%s(基线给不出'源已失效', 只能给 SUSPECT)", verdict_name(t.st.verdict));
    case_end("M8-PRE-1-baseline-has-no-source-age");

    /* ---- M8-PRE-2: 单源强异常没有严重出口 ---- */
    case_begin();
    memset(&t, 0, sizeof(t));
    diag_engine_reset(&t);
    seq = 0;
    for (i = 0; i < 3000; ++i)
        feed(&t, 5, 150.0f, 1000, (rt_uint8_t)MOTOR_CRUISE, 500, 1, &seq);
    v = t.st.verdict;
    CHECK(v != DIAG_STALL_CONFIRMED && v != DIAG_OVERLOAD,
          "基线竟然从单源 SG 崩给出严重结论(%s)", verdict_name(v));
    CHECK(g_post_calls == 0, "未双源一致却 post 了 %d 次", g_post_calls);
    /* 同一场景下把标定"变成 MEASURED"也不会改变结论: 门禁开了, 判据仍要求电流升 */
    memset(&t, 0, sizeof(t));
    diag_engine_reset(&t);
    d_mode = DIAG_MODE_ACTIVE_PROTECTION;
    g_cal = CURRENT_ADC_CAL_MEASURED;
    seq = 0;
    for (i = 0; i < 3000; ++i)
        feed(&t, 5, 150.0f, 1000, (rt_uint8_t)MOTOR_CRUISE, 500, 1, &seq);
    CHECK(g_post_calls == 0, "MEASURED + ACTIVE 下 SG 崩仍 post=0 才对(实测=%d)", g_post_calls);
    CHECK(t.st.verdict != DIAG_STALL_CONFIRMED, "单源被冒称双源确认");
    case_end("M8-PRE-2-baseline-has-no-single-source-severe");

    /* ---- M8-PRE-3: severe 边沿的打印/保护次序(基线=先打印) ----
     * 期望方向固定为"基线"。若在 M8 之后的树上编译本探针, 这一例必然 FAIL ——
     * 那就是修后对照本身; 修后的正向判据在主链 REL27-active-severe-protect-before-print
     * 里(同一枚 needle, 同一套记账), 不在此重复。 */
    case_begin();
    memset(&t, 0, sizeof(t));
    t.may_report = RT_TRUE;
    diag_engine_reset(&t);
    d_mode = DIAG_MODE_ACTIVE_PROTECTION;
    g_post_calls = 0;
    g_post_severe_lines_at_call = -1;
    seq = 0;
    for (i = 0; i < 300; ++i)
        feed(&t, 5, 900.0f, 1000, (rt_uint8_t)MOTOR_CRUISE, 500, 1, &seq);
    CHECK(g_post_calls == 1, "post=%d(期望 1)", g_post_calls);
    CHECK(t.st.verdict == DIAG_STALL_CONFIRMED, "verdict=%s(期望 STALL_CONFIRMED)",
          verdict_name(t.st.verdict));
    CHECK(cap_count("[DIAG] ACTIVE: severe") == 1, "severe 摘要行数=%d(期望 1)",
          cap_count("[DIAG] ACTIVE: severe"));
    CHECK(g_post_severe_lines_at_call == 1,
          "基线复现失败: post 时 severe 行=%d(期望 1, 即**打印在保护之前**)",
          g_post_severe_lines_at_call);
    CHECK(g_post_cap_len > 0 &&
          strstr(g_cap + g_post_cap_len, "[DIAG] ACTIVE: severe") == RT_NULL,
          "基线复现失败: severe 行其实出现在 post 之后");
    case_end("M8-PRE-3-baseline-print-before-protect");

    printf("SUMMARY|cases=%d pass=%d fail=%d pre_bad=0 executed=%d\n",
           g_pass + g_fail, g_pass, g_fail, g_pass + g_fail);
    return g_fail ? 1 : 0;
}
