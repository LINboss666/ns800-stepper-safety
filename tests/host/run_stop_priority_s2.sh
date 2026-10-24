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
HAL_DRV="${HAL_DRV:-$REPO_ROOT/rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/HAL_Drivers/drivers}"
GROUP_TIMEOUT="${S2_GROUP_TIMEOUT:-60}"

# ---------------------------------------------------------------- 组与逐变体登记表
# 变量名不能叫 GROUPS: bash 里 $GROUPS 是保留数组(当前用户的组 ID 列表), 给它赋标量
# 会让 `for g in $GROUPS` 遍历出 GID(本机实测=197121), 组名全部丢失。
S2_GROUPS="lock isr isr_window off_matrix off_arm unknown identity diag recovery stale boundary healthy target_reject"

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

G_recovery_target="C12-new-fault-during-reset-keeps-latch C12b-low-only-fail-advances C13-latched-cleared-by-real-recheck C14-two-reset-threads-new-fault-wins"
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

# 变体期望的用例总数(全开时): 少一组/少一个 id 都不算通过。
S2_EXPECT_target="${S2_EXPECT_target:-22}"
S2_EXPECT_host_ovr="${S2_EXPECT_host_ovr:-40}"

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
    local variant="$1" log exe extra vrc group out marker proc_rc vres
    local tot_cases=0 tot_pass=0 tot_fail=0 tot_prebad=0 tot_skip=0 grp_n=0 bad_grps=""
    local decided=0 n_sel
    n_sel="$(printf '%s\n' $SELECTED | grep -c .)"
    log="$OUT_DIR/${variant}_build.txt"; exe="$OUT_DIR/${variant}.exe"
    extra="$(variant_flags "$variant")"
    case "$extra" in *__BAD_VARIANT__*) note "FATAL: 未知变体 $variant"; return 4;; esac

    rm -f "$exe" "$log"
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

    for group in $SELECTED; do
        out="$OUT_DIR/${variant}_${group}_out.txt"
        marker="$OUT_DIR/${variant}_${group}.executed.marker"
        rm -f "$out" "$marker"
        printf 'LAUNCHED exe=%s group=%s timeout=%ss\n' "$exe" "$group" "$GROUP_TIMEOUT" > "$marker"
        timeout "$GROUP_TIMEOUT" "$exe" "$group" > "$out" 2>&1
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
#   rev_recovery_ticket_check 家族②"去掉恢复/确认那一侧的 epoch 检查": 关掉
#                             sp_note_permit_confirmed_for 的身份门(票据/请求电平/写代三者
#                             必须仍与本次一致) => 过期许可确认照样写进别人那一代。
#   两次试投记录(不作数字用, 只说明为什么选这两处门): 先试的 sp_recovery_probe_off 票据门
#   在 recovery 组**没有被任何用例消费**(注入落地、台账自洽、proc rc=0 => NOT-DETECTED),
#   那一条事实这一批用例不观察; 反向要挑"产品真在被测的那个门"。
#   判据与 S1/SD 反向同一套: 注入必须真落地(否则 INFRA-FAIL) + 副本构建成功 + 夹具业务
#   rc=1 + 逐组台账自洽 + 预登记失败身份全部命中; 崩溃/超时/台账不完整一律 INFRA-FAIL,
#   "编译失败"与"没跑起来"都不算检出。
#   ⚠ §10.3 家族①的另一半("去掉真实危险写")与家族②的另一半("去掉真实 TX/flush 预算")
#     不在本轮: 它们要求把 drv_epwm.c / drv_uart.c 的真实叶子编进主机链(见 §11 未执行)。
# --------------------------------------------------------------------------
reverse_s2() {
    local name="$1" variant="$2" groups="$3" ids="$4"
    local work exe brc proc_rc rc id hit=0 miss=0 grp out marker vres extra miss_ids=""
    work="$OUT_DIR/reverse/$name"
    rm -rf "$work"; mkdir -p "$work/app" || { note "  $name: 无法建副本目录"; remember 4; return; }
    cp "$SRC_APP/stop_priority.c" "$work/app/stop_priority.c" || { note "  $name: 副本失败"; remember 4; return; }
    cp "$SRC_APP"/*.h "$work/app/" 2>/dev/null

    cat > "$work/inject.py" <<'PYEOF'
import io, sys
path, mode = sys.argv[1], sys.argv[2]
PAIRS = {
    # 两道门在文件里各有同形的孪生(sp_trip_for_operation / sp_recovery_note_failure),
    # 所以锚点带上下一行, 保证命中恰好一次 —— 命不中就判 INFRA-FAIL, 不猜。
    'rev_stale_identity_gate': [(
        '    if (sp.epoch != expected_epoch)\n'
        '    {\n'
        '        sp.stale_dropped++;\n'
        '        sp_copy_proof_held(out);',
        '    /* NEGATIVE-INJECTION(rev_stale_identity_gate): 关断不再核对调用方原身份 */\n'
        '    if (RT_FALSE)\n'
        '    {\n'
        '        sp.stale_dropped++;\n'
        '        sp_copy_proof_held(out);')],
    # sp_note_permit_confirmed_for 与 sp_note_enn_confirmed_for 的门**逐字同形**, 所以锚点
    # 必须从函数名开始, 否则命中 2 次(实测过), 那就是"注入没落到指定的那一道门"。
    'rev_recovery_ticket_check': [(
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
        '    /* NEGATIVE-INJECTION(rev_recovery_ticket_check): 异代的许可确认照样写进那一代 */\n'
        '    if (RT_FALSE)')],
}
subs = PAIRS[mode]
s = io.open(path, encoding='utf-8').read()
out = s
for old, new in subs:
    n = out.count(old)
    if n != 1:
        sys.stderr.write('INJECT-FATAL: anchor hits %d (want 1): %s\n' % (n, old[:60]))
        sys.exit(1)
    out = out.replace(old, new, 1)
io.open(path, 'w', encoding='utf-8', newline='').write(out)
print('injected mode=%s replacements=1' % mode)
PYEOF

    if ! "$PY" "$work/inject.py" "$work/app/stop_priority.c" "$name" > "$work/inject.txt" 2>&1; then
        cat "$work/inject.txt"
        note "  $name: 注入未落地 => 判基础设施故障, 不当作检出"
        matrix+=("$name | INFRA-FAIL 注入未落地"); remember 4; return
    fi
    grep -q "NEGATIVE-INJECTION" "$work/app/stop_priority.c" || {
        note "  $name: 副本里没有注入标记"; matrix+=("$name | INFRA-FAIL 无注入标记")
        remember 4; return; }

    exe="$work/conc.exe"
    extra="$(variant_flags "$variant")"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -I "$SHIM_DIR" -I "$work/app" -I "$SRC_APP" -I "$HAL_DRV" \
        -I "$COMPAT_DIR" "$S2_MAIN" -o "$exe" > "$work/build.txt" 2>&1
    brc=$?
    if [ "$brc" -ne 0 ]; then
        tail -15 "$work/build.txt"
        note "  $name: 注入副本编译失败 rc=$brc => INFRA-FAIL(编译失败不算检出)"
        matrix+=("$name | INFRA-FAIL 构建失败 rc=$brc"); remember 4; return
    fi

    for grp in $groups; do
        out="$work/${variant}_${grp}_out.txt"; marker="$work/${variant}_${grp}.marker"
        printf 'LAUNCHED\n' > "$marker"
        timeout "$GROUP_TIMEOUT" "$exe" "$grp" > "$out" 2>&1
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
    note "=== S2 反向(2 项家族, 仓外副本; 正常对照已先通过) ==="
    reverse_s2 rev_stale_identity_gate host_ovr "identity stale" \
        "C09b-late-actions-rejected-no-write C15b-old-identity-rejected-no-write"
    reverse_s2 rev_recovery_ticket_check host_ovr "identity" \
        "C08b-stale-confirm-rejected"
    note ""
    note "=== S2 反向汇总 ==="
    for m in "${matrix[@]}"; do note "$m"; done
    {
        echo "S2_REVERSE_RC=$overall"
        echo "run_id=$RUN_ID"
        for m in "${matrix[@]}"; do echo "$m"; done
    } > "$OUT_DIR/s2_reverse_status.txt"
    if [ "$overall" -eq 0 ]; then
        note "S2 REVERSE RESULT: PASS —— 两项注入均命中预登记业务失败身份(rc=1, 台账自洽)"
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
        note "S2 RUNNER RESULT: PASS (rc=0) —— target 与 host_ovr 全部 13 组的必需用例真执行且判 PASS"
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
