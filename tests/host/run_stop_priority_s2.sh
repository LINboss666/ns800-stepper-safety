#!/usr/bin/env bash
# run_stop_priority_s2.sh - 停机优先(S2)并发/身份链 runner  (phase8-stop-priority-s2-cp4, 2026-10-24)
#
# 与既有四条链**并列**执行, 不改它们(旧 runner 已审, 冻结):
#   build_and_run.sh(V1/V2) / run_io_behavior.sh / run_stop_result_s1.sh
#   / run_safety_data_semantics.sh / run_negatives.sh(+元负例)
#
# 夹具: tests/host/sp_conc_main.c + tests/host/shim_ps/(与 S1 链同一份端口层来源,
#       只把"单线程 IPC + 掩码位"换成**真 pthread** 模型)。被测生产源是 applications 里
#       真实编译的 stop_priority.c / motor.c / step_pwm.c / safety_state.c。
#
# 为什么必须分组、每组一个进程(任务书 §9):
#   夹具用真实阻塞互斥量、真实线程与"被屏蔽窗口内排队、窗口结束后交付"的 ISR 模型;
#   一个进程里跑全部组会让前一组遗留的协调层状态成为下一组的隐式前置。每组独立进程,
#   台账按组自洽, runner 再按变体汇总。
#
# 变体(同一份生产源码, 只改预处理宏; 目标四宏本身不改):
#   target    不加覆盖 => 四道硬件宏全部 RT_FALSE = 目标发布配置。
#             需要"成功 arm/诊断持有/正常开启周期"的前提在这里**不可达**, 夹具按声明
#             登记 SKIP(见下方 S_ 表), 目标变体的拒绝类用例(C21*)在这里**真执行**。
#   host_ovr  -DPS_GATE_HOST_OVERRIDE + 三道宏 RT_TRUE(**仅主机**) => 前置可达的
#             身份/并发用例全部真执行。
#   ⚠ 不能用 target 的"前置拒绝"冒充并发通过, 也不能用 host_ovr 冒充目标配置。
#
# 判定纪律(沿用 R2/R3/R4/SD-NEW-01/02 与 S1 的收口结果, 不新建通用平台):
#   - 任何 rm / mkdir / 重定向 / 编译**之前**先过 require_outside_repo; 只动带本工具哨兵
#     .ns800_host_work 的目录; 每种运行用独立 run_id 目录, 保留旧结果。
#   - ONLY=<组名,逗号分隔> 先验证再执行: 未登记的组名直接 INFRA-FAIL(4), 不静默忽略;
#     并如实披露"选择集合"与"实际执行集合"(两者不一致就是基础设施故障)。
#   - 逐组逐 id 台账: 必需 id **出现恰好一次且判 PASS**; 缺席/重复/未登记/SUMMARY 不
#     自洽 => 基础设施故障(4); 判 FAIL / 自报 fail!=0 / pre_bad!=0 / 真实 rc!=0,1 => 见下。
#   - 进程退出码解释: 0=全 PASS, 1=存在判 FAIL 的用例; **其它任何退出(含墙钟 timeout
#     124、段错误 139、信号)一律 INFRA-FAIL(4)** —— 崩溃或超时不等于检出, 也不等于通过。
#     墙钟 timeout 在这里只作**死锁守卫**(夹具的真实互斥量一旦形成非法等待就会挂住)。
#   - SKIP 行只允许出现在"本变体声明过"的 id 上; 出现未声明的 SKIP => 台账不自洽(4)。
#   - 用例总数按变体核对(S2_EXPECT_*): 少跑一组/少一个 id 都不允许以"看起来全绿"通过。
#
# 用法: bash tests/host/run_stop_priority_s2.sh
#       S2_OUT_DIR=<仓外目录> SRC_APP=<快照目录> GCC=<编译器>
#       ONLY=lock,recovery bash tests/host/run_stop_priority_s2.sh
#       S2_GROUP_TIMEOUT=90 ...   # 单组墙钟秒数(默认 60)
# 退出码: 0=通过; 1=检出失败(有用例判 FAIL); 4=基础设施故障; 127=环境缺失

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_ps}"
S2_MAIN="${S2_MAIN:-$HERE/sp_conc_main.c}"
LEAF_MAIN="${LEAF_MAIN:-$HERE/leaf_main.c}"
LEAF_SHIM="${LEAF_SHIM:-$HERE/shim_leaf}"
HAL_DRV="${HAL_DRV:-$REPO_ROOT/rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/HAL_Drivers/drivers}"
GROUP_TIMEOUT="${S2_GROUP_TIMEOUT:-60}"

# ---------------------------------------------------------------- 组与逐变体登记表
# 变量名不能叫 GROUPS: bash 里 $GROUPS 是保留数组(当前用户的组 ID 列表), 给它赋标量
# 会让 `for g in $GROUPS` 遍历出 GID(本机实测=197121), 组名全部丢失。
# CP5 §5: 追加**真实 HAL 叶子链**的四个组(同一 runner、同一台账纪律, 但跑**第二个二进制**):
#   epwm_leaf / tmc_lock / uart_leaf / budget_leaf  =>  C10 / C16 / C17 / C18
# 其余十三组仍跑端口层那个二进制(它们测应用本体, 三段叶子是端口桩)。
# URO(2026-10-27 §6.1): read_obs 也跑**同一个叶子二进制**, 但它是只读通信观测, 不取
#   写入门禁 => 在 target 与 host_ovr **两个变体都真执行**(不许整族 SKIP, 也不许为了
#   跑本组在 target 里偷偷开宏)。旧四组的 target 整族 SKIP 保持原样、身份不变。
S2_GROUPS="lock isr isr_window off_matrix off_arm unknown identity diag recovery stale boundary healthy target_reject epwm_leaf tmc_lock uart_leaf budget_leaf read_obs"
LEAF_GROUPS=" epwm_leaf tmc_lock uart_leaf budget_leaf read_obs "

# 每组每变体**必需**(必须真执行且判 PASS)的用例 id; 名称与夹具里的 CASE id 逐字一致。
G_lock_target="C01-fast-before-lock-then-wait C01b-commits-after-release-no-second-close C01c-ss-lock-error-no-ack C02-mot-lock-held-then-commits C02b-mot-lock-error-no-place"
G_lock_host_ovr="$G_lock_target"

G_isr_target="C03a-isr-early-protected-quiet C03b-isr-send-fail-counted C03c-thread-context-observable"
G_isr_host_ovr="$G_isr_target"

G_isr_window_target="C04a-queued-only-inside-window C04b-delivered-after-window-and-acted"
G_isr_window_host_ovr="$G_isr_window_target"

G_off_matrix_target="C05a-disable-fail-raw-separated C05b-no-device-low-still-tried"
G_off_matrix_host_ovr="$G_off_matrix_target"

# C05c/C05d 的前置是"一次成功的 arm"(目标宏 FALSE 下不可达) => 只在 host_ovr 真执行。
G_off_arm_target=""
G_off_arm_host_ovr="C05c-low-write-error-not-eok C05d-disarm-low-confirm-commits-same-gen"

G_unknown_target="C06-explicit-stop-retries-and-latch-persists"
G_unknown_host_ovr="$G_unknown_target"

# C07*/C08*/C09* 需要真实预约/诊断持有/新运行 => 只在 host_ovr 真执行。
G_identity_target=""
G_identity_host_ovr="C07a-no-high-while-inhibited C07b-arm-refuses-post-recovery-reservation C08a-old-rollback-cannot-cut-new-diag C08b-stale-confirm-rejected C09a-close-proof-permit-confirm-same-gen C09b-late-actions-rejected-no-write C09c-re-reserve-new-identity"

G_diag_target=""
G_diag_host_ovr="C11a-prepare-fail-releases-no-enable C11b-motor-blocked-while-diag-owns C11c-diag-stop-releases C11d-diag-fail-keeps-owner-single-close"

G_recovery_target="C12-new-fault-during-reset-keeps-latch C12b-low-only-fail-advances C13-latched-cleared-by-real-recheck C14a-stale-ticket-refused-at-final-commit C14b-new-fault-after-commit-kept"
G_recovery_host_ovr="$G_recovery_target"

G_stale_target="C15a-empty-pending-writes-nothing"
G_stale_host_ovr="C15a-empty-pending-writes-nothing C15b-old-identity-rejected-no-write C15c-stale-selftest-cannot-write-ready"

G_boundary_target="C19a-boundary-non-eok-keeps-inhibit C19b-explicit-stop-still-closes-at-boundary"
G_boundary_host_ovr="$G_boundary_target"

# C20* = 健康周期(control-healthy-cycle 对照组)需要真实开启许可 => 只在 host_ovr 真执行。
G_healthy_target=""
G_healthy_host_ovr="C20a-healthy-run C20b-ramp-zero-ready-keeps-armed C20c-restart-after-ready C20d-disarm-then-rearm C20e-owner-views-consistent"

# C21* = "目标宏 FALSE 的拒绝**真在 target 执行**"; host_ovr 变体按声明 SKIP。
G_target_reject_target="C21a-target-false-refuses-open C21b-close-always-available"
G_target_reject_host_ovr=""

# --------------------------------------------------------------------------
# CP5 §5 真实 HAL 叶子链(第二个二进制: tests/host/leaf_main.c)
#   被测生产实现: stop_priority.c + tmc2209.c + drv_uart_txleaf.inc + drv_epwm_leaf.inc
#   target 变体:  四宏 RT_FALSE => 编译期写入门禁直接挡住, 总线序列/预约前提不可达,
#                 按声明整族 SKIP(不冒充真实执行); 两个变体都**必须干净编译**。
#   host_ovr:     C10/C16/C17/C18 全部真执行。
# --------------------------------------------------------------------------
G_epwm_leaf_target=""
G_epwm_leaf_host_ovr="C10a-prepare-writes-zero-registers C10b-period-compare-mode-match C10c-valid-token-commits-registers C10d-frozen-tbctl-not-restored-by-config-commit C10e-stale-token-commit-writes-zero-registers C10e2-protection-itself-did-one-real-off-and-low C10f-legacy-set-keeps-frozen-timebase C10g-20khz-roundtrip-50us C10h-low-frequency-dividers-selected C10i-invalid-or-inexpressible-zero-writes"
G_tmc_lock_target=""
G_tmc_lock_host_ovr="C16a-lock-held-refuses-within-budget C16a2-non-taker-cannot-release C16a3-owner-release-balances C16b-busy-call-does-not-clobber-active-op C16b2-real-failure-still-protects-exactly-once C16c-rx-flood-byte-cap-stops-before-tx C16c2-rx-flood-time-cap-stops-before-tx C16d-cfg-leaf-lock-held-bounded-fail-protects C16d2-holder-still-owns-cfg-lock-after-refusal C16d3-holder-release-restores-lock C16e-xfer-lock-held-bounded-fail-no-frame C16e2-xfer-lock-released-then-read-works C16e3-xfer-wait-is-min-of-cap-and-remaining C16f-wait-is-min-of-cap-and-remaining C16f2-exhausted-budget-does-not-attempt-lock"
G_uart_leaf_target=""
G_uart_leaf_host_ovr="C17a-no-space-expires-with-zero-written C17b-partial-send-keeps-raw-written C17c-expired-after-last-byte-not-a-success C17d-normal-frame-eok C17e-context-gates-reject-quietly C17f-late-complete-frame-not-a-success C17g-crc-error-rejected-and-protects-once"
G_budget_leaf_target=""
G_budget_leaf_host_ovr="C18a-single-budget-covers-subreads-and-ifcnt C18a2-summary-published-after-real-success C18b-budget-exhausts-without-reopening C18c-wraparound-with-room-succeeds C18c2-wraparound-expiry-is-unsigned-delta C18d-late-real-success-cannot-refresh-summary"

# --------------------------------------------------------------------------
# URO(2026-10-27 任务书 §6.1/§6.2): read_obs = 读事务观测 + RX 期限修正的 24 条具名用例
#   (URO01..URO22, 其中 URO06/URO22 各带一条同族子身份 URO06b/URO22b)。
#   **两个变体都真执行**: 只读观测不需要把四个安全宏翻成 1; 因此 target 的这一组
#   没有 SKIP 名额(S_read_obs_target 为空), 出现任何 SKIP 都会被判台账不自洽。
#   旧四个叶子组的 target 整族 SKIP 保持原样, 本组不适用那套写法。
# --------------------------------------------------------------------------
G_read_obs_target="URO01-healthy-read-publishes-real-value URO02-healthy-prestage-reply-inside-window URO03-reply-at-110-accepted-by-fixed-deadline URO04-stage-deadline-boundary-pair URO05-total-budget-exhausted-before-rx-entry URO06-lock-refusal-sends-nothing-and-releases-nothing URO06b-zero-remaining-budget-does-not-even-attempt-lock URO07-flush-limits-keep-flush-fact-tx-not-reached URO08-tx-no-space-zero-written-rx-not-reached URO09-partial-tx-keeps-real-written-count URO10-expire-after-last-byte-keeps-written-four URO11-rx-zero-bytes-not-claimed-as-echo URO12-exact-four-byte-echo-only URO13-wrong-header-or-wrong-reg-kept-as-raw-bytes URO14-header-matched-short-frame URO15-complete-frame-crc-error-keeps-eio URO16-complete-legal-frame-rejected-only-by-total-deadline URO17-long-stream-slide-counts-prefix-tail-truncation URO18-tick-wraparound-uses-unsigned-deltas URO19-probe-per-record-facts-and-early-exit URO20-probe-stays-read-only-and-prints-after-txn-release URO21-output-bounded-and-illegal-context-silent URO22-observation-on-off-equivalent-on-same-input URO22b-observation-never-invents-a-success"
G_read_obs_host_ovr="$G_read_obs_target"

# 每个 组×变体 **允许**的 SKIP id(与夹具内 SKIPV 的字面 id 一致)。
S_off_arm_target="C05c C05d"
S_off_arm_host_ovr=""
S_identity_target="C07 C08 C09"
S_identity_host_ovr=""
S_diag_target="C11"
S_diag_host_ovr=""
S_stale_target="C15b C15c"
S_stale_host_ovr=""
S_healthy_target="C20 C20d"
S_healthy_host_ovr=""
S_target_reject_target=""
S_target_reject_host_ovr="C21"
S_epwm_leaf_target="C10"
S_epwm_leaf_host_ovr=""
S_tmc_lock_target="C16"
S_tmc_lock_host_ovr=""
S_uart_leaf_target="C17"
S_uart_leaf_host_ovr=""
S_budget_leaf_target="C18"
S_budget_leaf_host_ovr=""
S_read_obs_target=""
S_read_obs_host_ovr=""

# 变体期望的用例总数(全开时): 少一组/少一个 id 都不算通过。
# CP5 §4: recovery 组的 C14 拆成 C14a/C14b 两个**确定接缝** => 22->23 / 40->41。
# CP5 §5: target 的叶子四组贡献 0 个 CASE(整族 SKIP) => 23 不变;
# host_ovr 追加真实执行的 27 条(7+7+7+6) => 41 -> 68。
# CP6 §2/§3: C10 追加 3 条数值反推用例(7->10)、C16 追加 8 条真实叶锁有界等待
# (7->15) => 41 + (10+15+7+6)=38 => 68 -> 79。
# URO §6.1: read_obs 在两个变体都真执行 24 条 => target 23+24=47, host_ovr 79+24=103。
# 旧的 23/79 合同没被改动(各组身份与数量逐字保留), 新用例**另计**。
S2_EXPECT_target="${S2_EXPECT_target:-47}"
S2_EXPECT_host_ovr="${S2_EXPECT_host_ovr:-103}"

VARIANTS="target host_ovr"

overall=0
detected_fail=0
declare -a matrix=()

note() { printf '%s\n' "$*"; }
remember() { case "$1" in ''|*[!0-9]*) return 0 ;; esac
             if [ "$overall" -eq 0 ] && [ "$1" -ne 0 ]; then overall="$1"; fi; }
file_bytes() { if [ -f "$1" ]; then wc -c <"$1" | tr -d ' '; else echo ABSENT; fi; }

g_required()   { local v="G_${1}_$2";  printf '%s\n' "${!v}"; }
g_allowed_skip(){ local v="S_${1}_$2"; printf '%s\n' "${!v}"; }

variant_flags() {
    case "$1" in
        target)   echo "" ;;
        host_ovr) echo "-DPS_GATE_HOST_OVERRIDE \
-DMOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_TRUE \
-DSTEP_DIAG_ISOLATED_ENV_CONFIRMED=RT_TRUE \
-DTMC_DRIVE_CONFIG_HW_VALIDATED=RT_TRUE" ;;
        *)        echo "__BAD_VARIANT__" ;;
    esac
}

# ---- 环境解析(必须先于任何文件动作) ----
GCC_RESOLVED="$(env_detect_gcc || true)"
if [ -z "$GCC_RESOLVED" ]; then
    note "FATAL: 未找到可用主机编译器(设 GCC=<路径> 或把 gcc 放进 PATH)"; exit 127
fi
GCC="$GCC_RESOLVED"
# 注入脚本用的 Python(与 S1/SD 链同一约定: PY 可显式覆盖; 默认 python)
PY="${PY:-python}"
command -v "$PY" >/dev/null 2>&1 || { note "FATAL: 找不到 Python($PY)"; exit 127; }

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${S2_OUT_DIR-${S2_WORK-$(env_work_root)/s2}/$RUN_ID}"
if ! OUT_DIR="$(require_outside_repo "$OUT_DIR" "S2_OUT_DIR")"; then
    note "FATAL: S2_OUT_DIR 被工作目录守卫拒绝(未创建目录, 未重定向, 未编译)"
    exit 4
fi
[ -f "$S2_MAIN" ]  || { note "FATAL: 夹具源不存在: $S2_MAIN"; exit 127; }
[ -f "$LEAF_MAIN" ] || { note "FATAL: 真实叶子夹具源不存在: $LEAF_MAIN"; exit 127; }
[ -d "$LEAF_SHIM" ] || { note "FATAL: 真实叶子 shim 目录不存在: $LEAF_SHIM"; exit 127; }
[ -f "$HAL_DRV/drv_uart_txleaf.inc" ] || { note "FATAL: 真实 UART 叶子不存在"; exit 127; }
[ -f "$HAL_DRV/drv_epwm_leaf.inc" ]   || { note "FATAL: 真实 EPWM 叶子不存在"; exit 127; }
[ -d "$SHIM_DIR" ] || { note "FATAL: 兼容层目录不存在: $SHIM_DIR"; exit 127; }
[ -d "$SRC_APP" ]  || { note "FATAL: 源码目录不存在: $SRC_APP"; exit 127; }
[ -f "$HAL_DRV/drv_epwm.h" ] || { note "FATAL: 真实 drv_epwm.h 不存在"; exit 127; }

if [ -e "$OUT_DIR" ] && [ ! -f "$OUT_DIR/.ns800_host_work" ]; then
    note "FATAL: 输出目录已存在但没有本工具的哨兵, 拒绝清理: $OUT_DIR"
    exit 4
fi
mkdir -p "$OUT_DIR" || { note "FATAL: 无法创建 OUT_DIR"; exit 4; }
printf 'ns800-host-chain stop-priority-s2 runner work dir (run_id=%s)\n' "$RUN_ID" \
    > "$OUT_DIR/.ns800_host_work" || { note "FATAL: 哨兵写不进去"; exit 4; }

# ---- ONLY 选择: 先验证再执行(R3-2 纪律) ----
SELECTED="$S2_GROUPS"
if [ -n "${ONLY:-}" ]; then
    bad=""
    for want in ${ONLY//,/ }; do
        hit=0
        for gname in $S2_GROUPS; do [ "$gname" = "$want" ] && hit=1; done
        [ "$hit" -eq 1 ] || bad="$bad $want"
    done
    if [ -n "$bad" ]; then
        note "FATAL: ONLY 含未登记的组(未执行任何编译):$bad"
        note "       可选组: $S2_GROUPS"
        exit 4
    fi
    SELECTED="${ONLY//,/ }"
fi

FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function -mthreads"
# 与 S1 链同一套最小兼容层(真实 drv_epwm.h 只用到 <rtdef.h> 的定型类型)。
COMPAT_DIR="$OUT_DIR/compat"
mkdir -p "$COMPAT_DIR" || { note "FATAL: 无法创建兼容头目录"; exit 4; }
printf '#ifndef NS800_HOST_RTDEF_COMPAT_H\n#define NS800_HOST_RTDEF_COMPAT_H\n#include <rtthread.h>\n#endif\n' \
    > "$COMPAT_DIR/rtdef.h" || { note "FATAL: 兼容头写不进去"; exit 4; }
printf '#ifndef NS800_HOST_RTHW_COMPAT_H\n#define NS800_HOST_RTHW_COMPAT_H\n#include <rtthread.h>\nrt_base_t rt_hw_interrupt_disable(void);\nvoid rt_hw_interrupt_enable(rt_base_t level);\n#endif\n' \
    > "$COMPAT_DIR/rthw.h" || { note "FATAL: rthw 兼容头写不进去"; exit 4; }
INCS=(-I "$SHIM_DIR" -I "$SRC_APP" -I "$HAL_DRV" -I "$COMPAT_DIR")
# 叶子链: -I shim_leaf 必须**先于** shim_ps, 于是 <rtthread.h>/<rtdevice.h>/<drv_common.h>
# 落到本链叠加层(内部再按相对路径 include shim_ps 的原文件)。
LEAF_INCS=(-I "$LEAF_SHIM" "${INCS[@]}")

note "###### 停机优先 S2 并发/身份链 (基线 $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)) ######"
note "run_id=$RUN_ID"
note "SRC_APP=$SRC_APP"
note "夹具=$S2_MAIN (真 pthread 互斥量/线程/窗口内排队 ISR)"
note "OUT_DIR=$OUT_DIR"
note "compiler=$("$GCC" --version | head -1)"
note "单组墙钟=${GROUP_TIMEOUT}s(死锁守卫)  选择集合=$(printf '%s' "$SELECTED" | tr ' ' ',')"

# --------------------------------------------------------------------------
# 台账核对: 逐组一份(每组一个进程 => 恰好一条 SUMMARY)
#   返回 0=自洽且必需 id 全 PASS; 2=不自洽(基础设施); 3=自洽但有判 FAIL 的必需用例
#   全局: g_miss g_dup g_bad g_unreg g_lines g_sum_ok g_sum_cases g_sum_pass
#         g_sum_fail g_sum_prebad g_sum_skips g_req_n
# --------------------------------------------------------------------------
validate_group_ledger() {
    local out="$1" group="$2" variant="$3" r cnt passline req_n=0 reg all
    g_miss=0; g_dup=0; g_bad=0; g_unreg=0; g_lines=0; g_sum_ok=0
    g_sum_cases=""; g_sum_pass=""; g_sum_fail=""; g_sum_prebad=""; g_sum_skips=""
    g_sum_lines=0; g_req_n=0

    req_n=0
    for r in $(g_required "$group" "$variant"); do
        g_req_n=$((g_req_n+1))
        cnt="$(grep -c "^CASE|$r|" "$out")"
        passline="$(grep -c "^CASE|$r|PASS|" "$out")"
        if   [ "$cnt" -eq 0 ]; then g_miss=$((g_miss+1)); note "    缺席: $r"
        elif [ "$cnt" -gt 1 ]; then g_dup=$((g_dup+1));   note "    重复执行: $r ($cnt 次)"
        elif [ "$passline" -ne 1 ]; then g_bad=$((g_bad+1))
             note "    判 FAIL: $(grep "^CASE|$r|" "$out" | head -1)"
        fi
    done

    g_lines="$(grep -cE '^CASE\|' "$out")"
    g_sum_lines="$(grep -cE '^SUMMARY\|' "$out")"
    if [ "$g_sum_lines" -eq 1 ]; then
        # 数字一律**只**从那一行 SUMMARY 里取: 早期写法对整份输出跑 sed, 生产日志里
        # 任何一行含 "pass="/"fail=" 都会让变量变成两行, 随即把 $(( )) 打成语法错误,
        # 并把整个 run_variant 中止(而 runner 仍打印 PASS —— 假结果)。
        local sline
        sline="$(grep -m1 '^SUMMARY|' "$out")"
        # SUMMARY 的字段用 '|' 分隔(SUMMARY|cases=5|pass=5|fail=0|pre_bad=0|skips=0):
        # 先按分隔符拆行再取键, 不要拿"空格"当分隔符去匹配。
        sfield() { printf '%s\n' "$sline" | tr '|' '\n' | grep -m1 "^$1=" | cut -d= -f2; }
        g_sum_cases="$(sfield cases)"
        g_sum_pass="$(sfield pass)"
        g_sum_fail="$(sfield fail)"
        g_sum_prebad="$(sfield pre_bad)"
        g_sum_skips="$(sfield skips)"
        local f
        for f in g_sum_cases g_sum_pass g_sum_fail g_sum_prebad g_sum_skips; do
            case "${!f}" in
                ''|*[!0-9]*) note "    SUMMARY 字段 $f='${!f}' 不是单一整数 —— 台账不可信"
                             eval "$f=0"; g_sum_ok=0; bad_fields=1 ;;
            esac
        done
        reg="$(printf '%s\n' $(g_required "$group" "$variant") | grep -c .)"
        all="$(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u | wc -l)"
        g_unreg="$(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u | while
                       read -r id; do
                           printf '%s\n' $(g_required "$group" "$variant") | grep -qx -- "$id" || echo "$id"
                       done | grep -c . )"
        # SKIP 只允许落在本变体声明过的 id 上
        local sk_bad=0 sk
        for sk in $(grep -E '^SKIP\|' "$out" | sed 's/^SKIP|\([^|]*\)|.*/\1/'); do
            printf '%s\n' $(g_allowed_skip "$group" "$variant") | grep -qx -- "$sk" || {
                sk_bad=$((sk_bad+1)); note "    未声明的 SKIP: $sk"; }
        done
        [ "$sk_bad" -ne 0 ] && g_unreg=$((g_unreg+sk_bad))
        if [ "$g_sum_cases" = "$g_lines" ] && \
           [ "$((g_sum_pass + g_sum_fail))" = "$g_lines" ]; then
            g_sum_ok=1
        fi
        note "    台账: 必需=$g_req_n 唯一出现=$all 实际行数=$g_lines SUMMARY(cases=$g_sum_cases pass=$g_sum_pass fail=$g_sum_fail pre_bad=$g_sum_prebad skips=$g_sum_skips) 自洽=$g_sum_ok"
    else
        note "    SUMMARY 行数=$g_sum_lines (必须恰好 1) —— 台账不可信"
    fi

    if [ "$g_sum_lines" -ne 1 ] || [ "$g_sum_ok" -ne 1 ] || [ "$g_unreg" -ne 0 ] || \
       [ "$g_miss" -ne 0 ] || [ "$g_dup" -ne 0 ]; then
        return 2
    fi
    [ "$g_bad" -ne 0 ] && return 3
    return 0
}

# --------------------------------------------------------------------------
# 每个变体: 编译一次 -> 逐组真执行 -> 逐组台账 -> 变体汇总
# --------------------------------------------------------------------------
run_variant() {
    local variant="$1" log exe leaf_exe leaf_log extra vrc lrc group gexe out marker proc_rc vres
    local tot_cases=0 tot_pass=0 tot_fail=0 tot_prebad=0 tot_skip=0 grp_n=0 bad_grps=""
    local decided=0 n_sel
    n_sel="$(printf '%s\n' $SELECTED | grep -c .)"
    log="$OUT_DIR/${variant}_build.txt"; exe="$OUT_DIR/${variant}.exe"
    leaf_exe="$OUT_DIR/${variant}_leaf.exe"; leaf_log="$OUT_DIR/${variant}_leaf_build.txt"
    extra="$(variant_flags "$variant")"
    case "$extra" in *__BAD_VARIANT__*) note "FATAL: 未知变体 $variant"; return 4;; esac

    rm -f "$exe" "$log" "$leaf_exe" "$leaf_log"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra "${INCS[@]}" "$S2_MAIN" -o "$exe" > "$log" 2>&1
    vrc=$?
    if [ "$vrc" -ne 0 ]; then
        tail -20 "$log"
        note "  $variant 编译失败 rc=$vrc => 不运行二进制(旧产物已删除)"
        matrix+=("$variant | COMPILE_FAILED rc=$vrc")
        remember 4
        return 1
    fi
    if [ -s "$log" ]; then
        note "  ⚠ $variant 构建有输出(视为警告/提示, 见 ${variant}_build.txt):"
        sed 's/^/      /' "$log"
        matrix+=("$variant | INFRA-FAIL 构建不干净(要求 0 warning)")
        remember 4
        return 1
    fi

    # ---- CP5 §5: 真实叶子链的第二个二进制(同样要求 0 warning; 失败即 INFRA-FAIL) ----
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -DPS_LEAF_SCRIPTED_TIME "${LEAF_INCS[@]}" "$LEAF_MAIN" \
        -o "$leaf_exe" > "$leaf_log" 2>&1
    lrc=$?
    if [ "$lrc" -ne 0 ]; then
        tail -20 "$leaf_log"
        note "  $variant 叶子链编译失败 rc=$lrc => 不运行叶子组(旧产物已删除)"
        matrix+=("$variant/leaf | COMPILE_FAILED rc=$lrc")
        remember 4
        leaf_exe=""
    elif [ -s "$leaf_log" ]; then
        note "  ⚠ $variant 叶子链构建有输出(要求 0 warning):"
        sed 's/^/      /' "$leaf_log"
        matrix+=("$variant/leaf | INFRA-FAIL 构建不干净(要求 0 warning)")
        remember 4
        leaf_exe=""
    fi

    for group in $SELECTED; do
        case "$LEAF_GROUPS" in
            *" $group "*) gexe="$leaf_exe" ;;
            *)            gexe="$exe" ;;
        esac
        if [ -z "$gexe" ]; then
            note "  $variant/$group 所需二进制未建成 => INFRA-FAIL(不运行, 也不算通过)"
            matrix+=("$variant/$group | INFRA-FAIL 所需二进制缺失(该二进制编译失败)")
            remember 4; decided=$((decided+1)); bad_grps="$bad_grps $group"; continue
        fi
        out="$OUT_DIR/${variant}_${group}_out.txt"
        marker="$OUT_DIR/${variant}_${group}.executed.marker"
        rm -f "$out" "$marker"
        printf 'LAUNCHED exe=%s group=%s timeout=%ss\n' "$gexe" "$group" "$GROUP_TIMEOUT" > "$marker"
        timeout "$GROUP_TIMEOUT" "$gexe" "$group" > "$out" 2>&1
        proc_rc=$?
        printf 'DONE rc=%s bytes=%s\n' "$proc_rc" "$(file_bytes "$out")" >> "$marker"
        grp_n=$((grp_n+1))
        case "$proc_rc" in
          0|1) : ;;
          124) note "  $variant/$group 墙钟超时(${GROUP_TIMEOUT}s) => 死锁守卫触发, INFRA-FAIL"
               matrix+=("$variant/$group | INFRA-FAIL 墙钟超时 rc=124(死锁守卫, 不算检出也不算通过)")
               remember 4; decided=$((decided+1)); bad_grps="$bad_grps $group"; continue ;;
          *)   note "  $variant/$group 异常退出 rc=$proc_rc => INFRA-FAIL(崩溃不算检出)"
               matrix+=("$variant/$group | INFRA-FAIL 异常退出 rc=$proc_rc")
               remember 4; decided=$((decided+1)); bad_grps="$bad_grps $group"; continue ;;
        esac

        validate_group_ledger "$out" "$group" "$variant"
        vres=$?
        decided=$((decided+1))
        tot_cases=$((tot_cases + ${g_sum_cases:-0}))
        tot_pass=$((tot_pass + ${g_sum_pass:-0}))
        tot_fail=$((tot_fail + ${g_sum_fail:-0}))
        tot_prebad=$((tot_prebad + ${g_sum_prebad:-0}))
        tot_skip=$((tot_skip + ${g_sum_skips:-0}))
        if [ "$vres" -eq 2 ]; then
            note "  $variant/$group 台账不自洽 => INFRA-FAIL"
            matrix+=("$variant/$group | INFRA-FAIL 缺席=$g_miss 重复=$g_dup 未登记=$g_unreg SUMMARY自洽=$g_sum_ok rc=$proc_rc")
            remember 4; bad_grps="$bad_grps $group"; continue
        fi
        if [ "${g_sum_prebad:-0}" -ne 0 ]; then
            note "  $variant/$group 有 pre_bad=$g_sum_prebad(前置不成立的观测行不算证明) => INFRA-FAIL"
            matrix+=("$variant/$group | INFRA-FAIL pre_bad=$g_sum_prebad 自报 fail=$g_sum_fail rc=$proc_rc")
            remember 4; bad_grps="$bad_grps $group"; continue
        fi
        if [ "$vres" -eq 3 ] || [ "$proc_rc" -ne 0 ] || [ "${g_sum_fail:-1}" != "0" ] || \
           [ "${g_sum_pass:-0}" != "$g_lines" ]; then
            note "  $variant/$group 有判 FAIL 的用例 => 检出失败"
            matrix+=("$variant/$group | NOT-PASS 判FAIL=$g_bad 自报fail=$g_sum_fail proc_rc=$proc_rc 必需=$g_req_n/$g_lines")
            detected_fail=1; remember 1; bad_grps="$bad_grps $group"; continue
        fi
        matrix+=("$variant/$group | OK 必需=$g_req_n 全部PASS 行数=$g_lines skips=$g_sum_skips pre_bad=0 proc_rc=0")
    done

    local expect
    expect_var=$(printf '%s' "S2_EXPECT_$variant")
    if [ "$SELECTED" = "$S2_GROUPS" ]; then
        expect="${!expect_var:-0}"
    else
        # ONLY 部分选择: 期望值改为"所选组的必需 id 数之和", 全量运行仍按登记总数核对。
        expect=0
        for group in $SELECTED; do
            expect=$((expect + $(printf '%s\n' $(g_required "$group" "$variant") | grep -c .)))
        done
        note "  (ONLY 部分运行: 期望用例改取所选组的必需 id 合计)"
    fi
    if [ "$decided" -ne "$n_sel" ]; then
        note "  ⚠ $variant 只有 $decided/$n_sel 个组产生了判定(执行被中断) => INFRA-FAIL"
        matrix+=("$variant | INFRA-FAIL 判定组数=$decided != 选择组数=$n_sel(不得称已验证)")
        remember 4
        return 1
    fi
    note "  $variant 汇总: 组=$grp_n 用例=$tot_cases PASS=$tot_pass FAIL=$tot_fail SKIP行=$tot_skip pre_bad=$tot_prebad 期望用例=$expect"
    if [ "$tot_cases" != "$expect" ]; then
        note "  ⚠ $variant 用例总数与登记期望不一致(少跑/多跑都不允许以'看起来全绿'通过)"
        matrix+=("$variant | INFRA-FAIL 用例总数=$tot_cases != 登记期望=$expect")
        remember 4
        return 1
    fi
    if [ -n "$bad_grps" ]; then
        matrix+=("$variant | 有失败组:$bad_grps")
        return 1
    fi
    matrix+=("$variant | OK 全部 $grp_n 组自洽且必需用例真执行判 PASS (用例=$tot_cases)")
    return 0
}

REVERSE="${REVERSE:-0}"

# --------------------------------------------------------------------------
# 反向(§10.3 两项 S2 家族, 只改**仓库外**的生产源码副本)
#   rev_stale_identity_gate   家族①"去掉旧 token 门": 关掉 sp_off_attempt_for 的
#                             expected_epoch 早退 => 迟到的旧关断能改写新那一代并真的动硬件。
#   rev_permit_confirm_identity_gate 家族②(CP5 §6 命名更正): 关掉的是
#                             sp_note_permit_confirmed_for 的确认身份门(请求电平/写代必须
#                             仍与本次一致) => 过期许可确认照样写进别人那一代; 命中 C08b。
#   两次试投记录(不作数字用, 只说明为什么选这两处门): 先试的 sp_recovery_probe_off 票据门
#   在 recovery 组**没有被任何用例消费**(注入落地、台账自洽、proc rc=0 => NOT-DETECTED),
#   那一条事实这一批用例不观察; 反向要挑"产品真在被测的那个门"。
#   判据与 S1/SD 反向同一套: 注入必须真落地(否则 INFRA-FAIL) + 副本构建成功 + 夹具业务
#   rc=1 + 逐组台账自洽 + 预登记失败身份全部命中; 崩溃/超时/台账不完整一律 INFRA-FAIL,
#   "编译失败"与"没跑起来"都不算检出。
#   CP5 §6 起, "去掉配置真实写的 token 门""去掉真实 TX/flush 预算""失败提前交回 owner"
#   三项已落在**真实叶子链**(drv_uart_txleaf.inc / drv_epwm_leaf.inc / tmc2209.c)与真实
#   门上, 各带预登记失败身份(C10e / C17c+C16c / C11d)。异常、缺用例、前置失败、编译失败
#   都不算检出; 反向只说"这道门被拆掉后这条业务断言当场失败", 不外推为整体安全证明。
# --------------------------------------------------------------------------
reverse_s2() {
    local name="$1" variant="$2" groups="$3" ids="$4"
    local work exe brc proc_rc rc id hit=0 miss=0 grp out marker vres extra miss_ids=""
    work="$OUT_DIR/reverse/$name"
    rm -rf "$work"; mkdir -p "$work/app" || { note "  $name: 无法建副本目录"; remember 4; return; }
    cp "$SRC_APP/stop_priority.c" "$work/app/stop_priority.c" || { note "  $name: 副本失败"; remember 4; return; }
    cp "$SRC_APP/tmc2209.c" "$work/app/tmc2209.c" || { note "  $name: tmc2209.c 副本失败"; remember 4; return; }
    cp "$SRC_APP"/*.h "$work/app/" 2>/dev/null
    # CP5 §5/§6: 反向也要能落在**真实 HAL 叶子**上, 所以把两份 .inc 也复制出来。
    mkdir -p "$work/hal" || { note "  $name: 无法建 hal 副本目录"; remember 4; return; }
    for f in drv_uart_txleaf.inc drv_epwm_leaf.inc drv_epwm_leaf.h; do
        cp "$HAL_DRV/$f" "$work/hal/$f" || { note "  $name: $f 副本失败"; remember 4; return; }
    done
    cp "$HAL_DRV"/drv_uart.h "$HAL_DRV"/drv_epwm.h "$work/hal/" 2>/dev/null
    cat > "$work/inject.py" <<'PYEOF'
import io, os, sys
work, mode = sys.argv[1], sys.argv[2]
# CP5 §6: 注入可以落在**多个**生产文件上(协调层 / TMC 配置层 / 真实 UART 叶子), 每条替换
# 都带文件键; 命不中恰好一次就整体失败(INFRA-FAIL), 不猜、不静默少注入。
FILES = {'sp': os.path.join(work, 'app', 'stop_priority.c'),
         'mc': os.path.join(work, 'app', 'tmc2209.c'),
         'tx': os.path.join(work, 'hal', 'drv_uart_txleaf.inc')}
PAIRS = {
    # 家族① "去掉旧 token 门": 关断不再核对调用方原身份。两道门在文件里各有同形的孪生
    # (sp_trip_for_operation / sp_recovery_note_failure), 所以锚点带上下一行。
    'rev_stale_identity_gate': [('sp',
        '    if (sp.epoch != expected_epoch)\n'
        '    {\n'
        '        sp.stale_dropped++;\n'
        '        sp_copy_proof_held(out);',
        '    /* NEGATIVE-INJECTION(rev_stale_identity_gate): 关断不再核对调用方原身份 */\n'
        '    if (RT_FALSE)\n'
        '    {\n'
        '        sp.stale_dropped++;\n'
        '        sp_copy_proof_held(out);')],
    # 家族②(CP5 §6 命名更正)改的是 **sp_note_permit_confirmed_for 的许可确认身份门**(命中
    # C08b)。它与 sp_note_enn_confirmed_for 的门逐字同形, 所以锚点从函数名开始。
    # ⚠ 本条**没有**移除恢复最终票据判据, 因此不得称"恢复最终提交 epoch 反向已覆盖";
    #   恢复最终窗口的真实接缝由 C14a/C14b(确定接缝)+ 被测提交路径自己覆盖。
    'rev_permit_confirm_identity_gate': [('sp',
        'rt_err_t sp_note_permit_confirmed_for(rt_uint32_t expected_epoch,\n'
        '                                      rt_uint8_t requested_level, rt_err_t rc)\n'
        '{\n'
        '    rt_base_t l = sp_win();\n'
        '    rt_err_t  e;\n'
        '\n'
        '    if (sp.epoch != expected_epoch || sp.permit.request_level != requested_level ||\n'
        '        sp.permit.write_epoch != expected_epoch)',
        'rt_err_t sp_note_permit_confirmed_for(rt_uint32_t expected_epoch,\n'
        '                                      rt_uint8_t requested_level, rt_err_t rc)\n'
        '{\n'
        '    rt_base_t l = sp_win();\n'
        '    rt_err_t  e;\n'
        '\n'
        '    /* NEGATIVE-INJECTION(rev_permit_confirm_identity_gate): 异代的许可确认照样写进那一代 */\n'
        '    if (RT_FALSE)')],
    # 家族③(CP5 §6 新增) 去掉**配置真实写的 token 门**: 窗口原语(sp_win/sp_end)与提交
    # 顺序都不动, 只取消资格检查 => 旧 token 也能进窗口并真的写寄存器 => C10e 具名检出。
    'rev_config_write_token_gate': [('sp',
        'rt_err_t sp_enter_token(const sp_token_t *t, rt_base_t *saved)\n'
        '{\n'
        '    rt_base_t l = sp_win();\n'
        '\n'
        '    if (!sp_token_ok_held(t))\n'
        '    {\n'
        '        sp.stale_dropped++;\n'
        '        sp_end(l);\n'
        '        return -RT_EPERM;\n'
        '    }',
        'rt_err_t sp_enter_token(const sp_token_t *t, rt_base_t *saved)\n'
        '{\n'
        '    rt_base_t l = sp_win();\n'
        '\n'
        '    /* NEGATIVE-INJECTION(rev_config_write_token_gate): 保留窗口原语, 只取消资格检查 */\n'
        '    if (RT_FALSE)\n'
        '    {\n'
        '        sp.stale_dropped++;\n'
        '        sp_end(l);\n'
        '        return -RT_EPERM;\n'
        '    }')],
    # 家族④(CP5 §6 新增) 去掉**真实 TX/flush 预算**两处判据:
    #   a) 真实 UART 叶子"末字节之后、返回成功之前再查同一条 start/total"(drv_uart_txleaf.inc)
    #      => 过期帧被发布成功 => C17c 具名检出; 输入有限(空间一直有), 错误版本正常返回。
    #   b) tmc2209.c 的 flush **字节上限**(排不干净就不发这一帧)=> 洪泛后照样发 TX
    #      => C16c 具名检出; 320 字节是可排空的有限输入, 不靠墙钟 timeout。
    'rev_tx_flush_budget': [
        ('tx',
         '    if ((rt_tick_t)(rt_tick_get() - start) >= total) return -RT_ETIMEOUT;\n'
         '    return RT_EOK;',
         '    /* NEGATIVE-INJECTION(rev_tx_flush_budget): 去掉末字节之后的原预算复核 */\n'
         '    return RT_EOK;'),
        ('mc',
         '        if (got_total > TMC_BUD_FLUSH_MAX_BYTES)\n',
         '        /* NEGATIVE-INJECTION(rev_tx_flush_budget): 去掉 flush 的字节上限 */\n'
         '        if (got_total > (rt_uint32_t)0x7FFFFFFFu)\n')],
    # 家族⑤(CP5 §6 新增) 关断失败分支**提前把 owner 交回**: 失败时 owner 应是
    # SP_HELD_UNKNOWN(输出状态未确认, 谁都不许接管), 这里改成 SP_NONE
    # => 扩充后的 C11d(canonical owner / 输出未交回)具名检出。
    'rev_fail_returns_owner_early': [('sp',
        '            sp_retain_held(code, events);\n'
        '            sp.owner = SP_HELD_UNKNOWN;\n'
        '            sp.trips++;',
        # C11d 走的是 release_on_ok=TRUE 那条路(诊断 pwm_test stop / disarm):
        # 生产在真正的 rt_pwm_disable 之前就把 owner 清成 SP_NONE(="提前交回"),
        # 关断失败时必须把通道收回 SP_HELD_UNKNOWN。注入去掉这一句收回 => owner 停在
        # SP_NONE, 扩充后的 C11d(canonical owner / 锁存 / 输出未交回)当场判 FAIL。
        '            sp_retain_held(code, events);\n'
        '            /* NEGATIVE-INJECTION(rev_fail_returns_owner_early): 失败后不再收回"未知持有" */\n'
        '            sp.trips++;')],
    # ------------------------------------------------------------------
    # URO(任务书 §6.3 的两项**局部**反向, 都落在真实生产本体 tmc2209.c 上, 只改仓外副本):
    #   rev_rx_deadline_old_origin   把修正后的 RX 期限表达式**退回旧起点算式**(漏掉已经
    #     花掉的 used)。健康正控(URO01/URO02/URO22)注入后仍必须通过, URO03(应答在第 110
    #     tick 才可见)必须具名检出; URO04/URO16 是同一条缺陷的期限边界例, 一并预登记。
    #   rev_obs_drop_raw_rx_capture  只去掉**原始字节取样/样本计数更新**, 保留业务解析与
    #     真实接收总量(rx_total 照常累加) => 必须由 URO12(精确 4 字节回显)与 URO17(跨缓冲
    #     滑动的前缀/尾部/截断标志)的原始字节断言具名检出, 不得靠业务失败码或崩溃算检出。
    # ------------------------------------------------------------------
    'rev_rx_deadline_old_origin': [('mc',
        '        left = b->total - used;\n'
        '        allowance = (stage_cap < left) ? stage_cap : left;\n'
        '        eff_end = b->start + used + allowance;\n',
        '        left = b->total - used;\n'
        '        allowance = (stage_cap < left) ? stage_cap : left;\n'
        '        /* NEGATIVE-INJECTION(rev_rx_deadline_old_origin): 期限重新锚在旧起点, '
        '漏掉已花掉的 used = a691eb5 的原算式 */\n'
        '        eff_end = b->start + allowance;\n')],
    'rev_obs_drop_raw_rx_capture': [('mc',
        '    obs->rx_total += (rt_uint32_t)got;\n'
        '    for (i = 0; i < got; ++i)\n',
        '    obs->rx_total += (rt_uint32_t)got;\n'
        '    /* NEGATIVE-INJECTION(rev_obs_drop_raw_rx_capture): 保留总量与业务解析, '
        '只去掉样本取样/样本计数更新 */\n'
        '    for (i = 0; i < (rt_size_t)0; ++i)\n')],
}

subs = PAIRS[mode]
for key, old, new in subs:
    path = FILES[key]
    txt = io.open(path, encoding='utf-8').read()
    n = txt.count(old)
    if n != 1:
        sys.stderr.write('INJECT-FATAL: %s anchor hits %d (want 1): %s\n' % (key, n, old[:70]))
        sys.exit(1)
    io.open(path, 'w', encoding='utf-8', newline='').write(txt.replace(old, new, 1))
    print('injected mode=%s file=%s replacements=1' % (mode, key))
print('injected mode=%s total=%d' % (mode, len(subs)))
PYEOF

    if ! "$PY" "$work/inject.py" "$work" "$name" > "$work/inject.txt" 2>&1; then
        cat "$work/inject.txt"
        note "  $name: 注入未落地 => 判基础设施故障, 不当作检出"
        matrix+=("$name | INFRA-FAIL 注入未落地"); remember 4; return
    fi
    # 注入标记必须**真的出现在被改的那个副本文件**里(不是只要某个文件有标记就算)。
    if ! grep -lq "NEGATIVE-INJECTION" "$work/app/stop_priority.c" "$work/app/tmc2209.c" "$work/hal/drv_uart_txleaf.inc" 2>/dev/null; then
        note "  $name: 副本里没有注入标记"; matrix+=("$name | INFRA-FAIL 无注入标记")
        remember 4; return
    fi
    # CP6 §4: note 是 shell 函数, 不能经 xargs 调用(云端实测五次 "xargs: note: No such
    # file or directory")。先在 shell 内取数, 再直接调用 note。
    inj_n="$(grep -c "^injected " "$work/inject.txt")"
    note "  $name: 注入替换 $inj_n 处(逐条已核对恰好命中一次)"

    exe="$work/conc.exe"; lexec="$work/leaf.exe"
    extra="$(variant_flags "$variant")"
    # 注入后的副本优先: -I "$work/app" 在 $SRC_APP 之前, -I "$work/hal" 在 $HAL_DRV 之前。
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -I "$SHIM_DIR" -I "$work/app" -I "$SRC_APP" -I "$HAL_DRV" \
        -I "$COMPAT_DIR" "$S2_MAIN" -o "$exe" > "$work/build.txt" 2>&1
    brc=$?
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -DPS_LEAF_SCRIPTED_TIME -I "$LEAF_SHIM" -I "$work/app" \
        -I "$work/hal" -I "$SHIM_DIR" -I "$SRC_APP" -I "$HAL_DRV" -I "$COMPAT_DIR" \
        "$LEAF_MAIN" -o "$lexec" >> "$work/build.txt" 2>&1
    lbrc=$?
    if [ "$brc" -ne 0 ] || [ "$lbrc" -ne 0 ]; then
        tail -20 "$work/build.txt"
        note "  $name: 注入副本编译失败 conc=$brc leaf=$lbrc => INFRA-FAIL(编译失败不算检出)"
        matrix+=("$name | INFRA-FAIL 构建失败 conc=$brc leaf=$lbrc"); remember 4; return
    fi

    for grp in $groups; do
        case "$LEAF_GROUPS" in *" $grp "*) gexe="$lexec";; *) gexe="$exe";; esac
        out="$work/${variant}_${grp}_out.txt"; marker="$work/${variant}_${grp}.marker"
        printf 'LAUNCHED exe=%s\n' "$gexe" > "$marker"
        timeout "$GROUP_TIMEOUT" "$gexe" "$grp" > "$out" 2>&1
        proc_rc=$?
        printf 'DONE rc=%s\n' "$proc_rc" > "$marker"
        cat "$out" >> "$work/all_out.txt"
        case "$proc_rc" in
          1) : ;;
          0) note "  $name/$grp: 注入后仍然全通过 => 没检出"
             matrix+=("$name/$grp | NOT-DETECTED 注入后链仍全部通过(proc rc=0)")
             detected_fail=1; remember 1; continue ;;
          124) matrix+=("$name/$grp | INFRA-FAIL 墙钟超时 rc=124(不算检出)")
             remember 4; continue ;;
          *) matrix+=("$name/$grp | INFRA-FAIL 异常退出 rc=$proc_rc(不算检出)")
             remember 4; continue ;;
        esac
        validate_group_ledger "$out" "$grp" "$variant" >/dev/null 2>&1
        vres=$?
        if [ "$vres" -eq 2 ] || [ "${g_sum_prebad:-0}" -ne 0 ]; then
            matrix+=("$name/$grp | INFRA-FAIL 台账不自洽/前置不成立(自报 fail=$g_sum_fail pre_bad=$g_sum_prebad)")
            remember 4; continue
        fi
    done

    # 失败身份在**所有已跑组的合并输出**里各查一次(逐组各查一遍会把同一个 id 数两次)。
    for id in $ids; do
        if [ "$(grep -c "^CASE|$id|FAIL|" "$work/all_out.txt")" -ge 1 ]; then
            hit=$((hit+1))
        else
            miss=$((miss+1)); miss_ids="$miss_ids $id"
        fi
    done

    if [ "$hit" -eq 0 ]; then
        matrix+=("$name | NOT-DETECTED 指定失败身份一个都没命中(命中=$hit 缺失=$miss)")
        detected_fail=1; remember 1; return
    fi
    if [ "$miss" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 命中=$hit 缺失=$miss$miss_ids")
        detected_fail=1; remember 1; return
    fi
    matrix+=("$name | META-OK 命中 $hit 个业务失败身份 (variant=$variant groups=$groups 台账自洽 proc_rc=1)")
}

if [ "$REVERSE" = "1" ]; then
    note ""
    note "=== 对照组: 反向之前必须先跑完整正常链 ==="
    for v in $VARIANTS; do run_variant "$v"; done
    if [ "$overall" -ne 0 ]; then
        note "正常对照未通过(rc=$overall) => 不执行反向(反向结论只在干净对照之上才有意义)"
        exit "$overall"
    fi
    matrix=()
    note ""
    note "=== S2 反向(7 项家族, 仓外副本; 正常对照已先通过) ==="
    reverse_s2 rev_stale_identity_gate host_ovr "identity stale" \
        "C09b-late-actions-rejected-no-write C15b-old-identity-rejected-no-write"
    reverse_s2 rev_permit_confirm_identity_gate host_ovr "identity" \
        "C08b-stale-confirm-rejected"
    # CP5 §6 新增三项(都落在**真实叶子/真实门**上):
    reverse_s2 rev_config_write_token_gate host_ovr "epwm_leaf" \
        "C10e-stale-token-commit-writes-zero-registers"
    reverse_s2 rev_tx_flush_budget host_ovr "uart_leaf tmc_lock" \
        "C17c-expired-after-last-byte-not-a-success C16c-rx-flood-byte-cap-stops-before-tx"
    reverse_s2 rev_fail_returns_owner_early host_ovr "diag" \
        "C11d-diag-fail-keeps-owner-single-close"
    # URO §6.3: 两项局部反向都跑 read_obs 组(同一个叶子二进制、同一套台账纪律)。
    #   健康正控在注入后**仍然必须通过**(否则"检出"来自本来就坏的链), 所以 groups 只给
    #   read_obs, 预登记失败身份只列 deadline 缺陷的三条边界/反例与两条原始字节断言。
    reverse_s2 rev_rx_deadline_old_origin host_ovr "read_obs" \
        "URO03-reply-at-110-accepted-by-fixed-deadline URO04-stage-deadline-boundary-pair URO16-complete-legal-frame-rejected-only-by-total-deadline"
    reverse_s2 rev_obs_drop_raw_rx_capture host_ovr "read_obs" \
        "URO12-exact-four-byte-echo-only URO17-long-stream-slide-counts-prefix-tail-truncation"
    note ""
    note "=== S2 反向汇总 ==="
    for m in "${matrix[@]}"; do note "$m"; done
    {
        echo "S2_REVERSE_RC=$overall"
        echo "run_id=$RUN_ID"
        for m in "${matrix[@]}"; do echo "$m"; done
    } > "$OUT_DIR/s2_reverse_status.txt"
    if [ "$overall" -eq 0 ]; then
        note "S2 REVERSE RESULT: PASS —— 七项注入均命中预登记业务失败身份(rc=1, 台账自洽)"
    else
        note "S2 REVERSE RESULT: FAIL (rc=$overall)"
    fi
    exit "$overall"
fi

for v in $VARIANTS; do
    note ""
    note "=== 变体 $v ==="
    run_variant "$v"
    vrun_rc=$?
    case "$vrun_rc" in
      0|1) : ;;
      *)   note "  ⚠ $v 的运行函数本身以 rc=$vrun_rc 退出(不是用例判定) => INFRA-FAIL"
           matrix+=("$v | INFRA-FAIL runner 函数异常退出 rc=$vrun_rc")
           remember 4 ;;
    esac
done

note ""
note "=== S2 并发/身份矩阵 ==="
for m in "${matrix[@]}"; do note "$m"; done
note ""
note "选择集合=$(printf '%s' "$SELECTED" | tr ' ' ',')"
executed_note=""
for v in $VARIANTS; do
    n=0
    for f in "$OUT_DIR/${v}_"*_out.txt; do [ -f "$f" ] && n=$((n+1)); done
    executed_note="$executed_note $v=$n组"
done
note "实际执行集合:$executed_note (每组一个独立进程)"

if [ "$overall" -eq 0 ]; then
    note ""
    if [ "$SELECTED" = "$S2_GROUPS" ]; then
        note "S2 RUNNER RESULT: PASS (rc=0) —— target 与 host_ovr 全部 $(printf '%s\n' $S2_GROUPS | grep -c .) 组的必需用例真执行且判 PASS"
    else
        note "S2 RUNNER RESULT: PASS(rc=0) —— **仅所选组**($SELECTED)的必需用例真执行且判 PASS; 未选择的组本轮未执行"
    fi
elif [ "$overall" = "1" ] && [ "$detected_fail" -eq 1 ]; then
    note ""
    note "S2 RUNNER RESULT: FAIL (rc=1) —— 存在判 FAIL 的必需用例"
else
    note ""
    note "S2 RUNNER RESULT: INFRA-FAIL (rc=$overall) —— 构建/台账/前置/超时未成立, 不得称已验证"
fi
exit "$overall"
