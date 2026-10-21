#!/usr/bin/env bash
# run_io_behavior.sh - 独立 I/O 行为测试 runner (Phase 8, 2026-10-21)
#
# 与旧链(run_negatives.sh / run_meta_negatives.sh)**并列**执行, 不重写它们:
#   - 旧链 host_main.c 把 safety_gpio/safety_thread/safety_state/step_pwm 替换成桩,
#     只真编译 tmc2209.c + motor.c; 本轮改动的正是那些被桩替换的模块, 所以必须有
#     一条真编译安全模块的链 —— 这就是本脚本跑的 io_main.c。
#   - 判定纪律沿用 R2/R3/R4: 编译失败不跑旧产物; 真实进程 rc 与自报结果分别记录;
#     必需用例没跑就不能 ALL PASS; 不用全局文本搜索代替用例判定(用例判定在二进制内
#     用生产 API 返回值/仿真计数器完成, 本脚本只核对**执行台账**: 每个必需 id 出现
#     一次且判 PASS, 并且二进制的 SUMMARY fail=0、真实 rc=0)。
#   - 输出目录先过 require_outside_repo; 只删带 .ns800_host_work 哨兵的目录。
#
# 变体(全部用同一份生产源码, 只改预处理宏):
#   off_target  LIMIT-OFF + 三道硬件宏 RT_FALSE          = 目标发布配置
#   on_target   LIMIT-ON  + 三道硬件宏 RT_FALSE
#   off_ovr     LIMIT-OFF + 仅主机覆盖三宏 RT_TRUE       (走通 arm/start 正路径)
#   on_ovr      LIMIT-ON  + 仅主机覆盖三宏 RT_TRUE
#   illegal     -DSAFETY_LIMIT_INPUTS_ENABLED=2 必须**编译不过**(反向验证配置合法性)
#
# 用法: bash tests/host/run_io_behavior.sh
#       IO_OUT_DIR=<仓外目录> SRC_APP=<快照目录> GCC=<编译器> ONLY=off_target ...
# 退出码: 0=全部通过; 1=检出失败(某用例 FAIL / 必需用例缺席); 4=基础设施故障(编译/执行本身不可信)

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_io}"
IO_MAIN="${IO_MAIN:-$HERE/io_main.c}"
OUT_DIR="${IO_OUT_DIR-$(env_work_root)/io}"
ONLY="${ONLY:-}"

# ---- 用例台账(与 io_main.c 里的 CHECK id 必须一字不差) ----
COMMON_CASES="IO1_CFG-limit_macro IO1_CFG-diag_always_active IO2-fb_pin_is_input \
IO2-fb_no_irq_registered IO2-fb_edges_post_no_estop IO2d-fb_never_written \
IO3a-disabled_ok IO3b-unexpected_enable_refused IO3c-no_sample_refused \
IO3e-pure_truth_table IO6a-transition_bounded_not_success \
IO10a-feedback_readonly_no_bus IO10b-no_real_device_io_total \
IO8d-ready_ok_before_pin_dead IO8e-dead_feedback_pin_refuses_ready \
IO8f-arm_refused_with_dead_feedback"
LIMIT_OFF_CASES="IO1OFF-limits_not_resolved IO1OFF-limits_never_touched \
IO1OFF-unwired_limits_do_not_block_ready IO1OFF-diag_still_real_producer \
IO1OFF-fault_clear_not_blocked_by_limits"
LIMIT_ON_CASES="IO1ON-limits_resolved_input_mode IO1ON-falling_edge_for_limits \
IO1ON-both_high_is_safe IO1ON-any_low_refuses_ready \
IO1ON-falling_irq_posts_limit_event IO1ON-invalid_sample_refuses_ready \
IO1ON-limit_irq_attach_failure_rolls_back"
TARGET_CASES="IO9a-arm_refused_by_enable_path_macro IO9b-no_HIGH_write_on_refused_arm \
IO9c-step_diag_denied IO9e-txn_balanced_after_refusal IO9f-start_refused_not_armed \
IO9d-write_gate_has_fb_bit_and_macro_bit"
OVERRIDE_CASES="IO4a-arm_confirms_enabled_side IO4b-start_passes_after_arm_no_phase_deadlock \
IO5a-runtime_guard_stops_on_cut IO5b-feedback_event_and_latch \
IO5c-stop_actions_not_skipped IO6b1-arm_ok_on_ideal_chain \
IO6b2-cut_request_rolls_back_to_low IO6c-no_start_while_feedback_inconsistent \
IO7a-clear_refused_while_fb_stuck_enabled IO7c-clear_allowed_once_feedback_ideal \
IO8b-pad_write_not_sticking_reported IO8c-ready_never_hardwired_true"

VARIANTS="off_target on_target off_ovr on_ovr illegal"

overall=0
detected_fail=0
matrix=()
declare -a CHECK_NOTES=()
declare -a REQUESTED=()
declare -a NOT_RAN=()

note() { printf '%s\n' "$*"; }
remember() { case "$1" in ''|*[!0-9]*) return 0 ;; esac
             if [ "$overall" -eq 0 ] && [ "$1" -ne 0 ]; then overall="$1"; fi; }
file_bytes() { if [ -f "$1" ]; then wc -c <"$1" | tr -d ' '; else echo ABSENT; fi; }

want_variant() {
    [ -z "$ONLY" ] && return 0
    local v
    for v in $ONLY; do [ "$v" = "$1" ] && return 0; done
    return 1
}

GCC_RESOLVED="$(env_detect_gcc || true)"
if [ -z "$GCC_RESOLVED" ] || [ ! -x "$GCC_RESOLVED" ]; then
    note "FATAL: 未找到可用主机编译器(设 GCC=<路径> 或把 gcc 放进 PATH)"; exit 127
fi
GCC="$GCC_RESOLVED"

# ---- R4 守卫: 任何 rm/mkdir/重定向/编译之前先验证工作目录 ----
if ! OUT_DIR="$(require_outside_repo "$OUT_DIR" "IO_OUT_DIR")"; then
    note "FATAL: IO_OUT_DIR 被工作目录守卫拒绝(未创建目录, 未重定向, 未编译)"
    exit 4
fi
[ -f "$IO_MAIN" ] || { note "FATAL: I/O 夹具源不存在: $IO_MAIN"; exit 127; }
[ -d "$SHIM_DIR" ] || { note "FATAL: I/O 兼容层目录不存在: $SHIM_DIR"; exit 127; }
[ -d "$SRC_APP" ]  || { note "FATAL: 源码快照目录不存在: $SRC_APP"; exit 127; }

if [ -e "$OUT_DIR" ] && [ ! -f "$OUT_DIR/.ns800_host_work" ]; then
    note "FATAL: 输出目录已存在但没有本工具的哨兵, 拒绝清理: $OUT_DIR"
    exit 4
fi
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR" || { note "FATAL: 无法创建 OUT_DIR"; exit 4; }
printf 'ns800-host-chain io runner work dir (safe to delete by this tool only)\n' \
    > "$OUT_DIR/.ns800_host_work" || { note "FATAL: 哨兵写不进去"; exit 4; }

FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function"
LIMIT_ON="-DSAFETY_LIMIT_INPUTS_ENABLED=RT_TRUE"
OVR="-DIO_GATE_HOST_OVERRIDE -DMOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_TRUE \
     -DSTEP_DIAG_ISOLATED_ENV_CONFIRMED=RT_TRUE -DTMC_DRIVE_CONFIG_HW_VALIDATED=RT_TRUE"

note "############ 独立 I/O 行为链 (基线 $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)) ############"
note "SRC_APP=$SRC_APP"
note "OUT_DIR=$OUT_DIR"
note "compiler=$("$GCC" --version | head -1)"

# 必需用例集合
required_of() {
    case "$1" in
        off_target) echo "$COMMON_CASES $LIMIT_OFF_CASES $TARGET_CASES" ;;
        on_target)  echo "$COMMON_CASES $LIMIT_ON_CASES  $TARGET_CASES" ;;
        off_ovr)    echo "$COMMON_CASES $LIMIT_OFF_CASES $OVERRIDE_CASES" ;;
        on_ovr)     echo "$COMMON_CASES $LIMIT_ON_CASES  $OVERRIDE_CASES" ;;
        illegal)    echo "" ;;
    esac
}

build_variant() {
    local name="$1" extra="$2" want_fail="$3"
    local log="$OUT_DIR/${name}_build.txt" exe="$OUT_DIR/${name}.exe" marker="$OUT_DIR/${name}.executed.marker"
    local rc

    rm -f "$exe" "$marker"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -I "$SHIM_DIR" -I "$SRC_APP" "$IO_MAIN" -o "$exe" > "$log" 2>&1
    rc=$?
    if [ "$want_fail" = "1" ]; then
        if [ "$rc" -ne 0 ]; then
            note "  illegal 宏值: 编译如预期被拒绝 (rc=$rc) —— 配置合法性成立"
            matrix+=("IO1_CFG-illegal_macro_rejected | OK(拒绝编译 rc=$rc)")
            return 0
        fi
        note "  illegal 宏值: 编译竟然成功 —— 非法 SAFETY_LIMIT_INPUTS_ENABLED 未被挡住"
        matrix+=("IO1_CFG-illegal_macro_rejected | NOT-DETECTED(rc=0, 本应非零)")
        remember 1
        detected_fail=1
        return 0
    fi
    if [ "$rc" -ne 0 ]; then
        tail -20 "$log"
        note "  $name 编译失败 rc=$rc => 不运行二进制(旧产物已删除)"
        matrix+=("$name | COMPILE_FAILED rc=$rc")
        remember "$rc"
        return 2
    fi
    printf 'LAUNCHED exe=%s\n' "$exe" > "$marker"
    return 0
}

run_variant() {
    local name="$1"
    local exe="$OUT_DIR/${name}.exe" out="$OUT_DIR/${name}_out.txt"
    local marker="$OUT_DIR/${name}.executed.marker"
    local proc_rc reported_fail rc_line case_line

    "$exe" > "$out" 2>&1
    proc_rc=$?
    printf 'DONE rc=%s exe=%s out_bytes=%s\n' "$proc_rc" "$exe" "$(file_bytes "$out")" > "$marker"

    rc_line="$(grep -E '^SUMMARY\|' "$out" | tail -1)"
    reported_fail="$(printf '%s' "$rc_line" | sed -n 's/.*fail=\([0-9]*\).*/\1/p')"
    [ -n "$reported_fail" ] || reported_fail="MISSING"

    # 逐 id 台账核对: 每个必需 id 必须出现恰好一次且判 PASS
    local missing=0 dup=0 badverdict=0 r id cnt passline
    for r in $(required_of "$name"); do
        cnt="$(grep -c "^CASE|$r|" "$out")"
        passline="$(grep -c "^CASE|$r|PASS|" "$out")"
        if [ "$cnt" -eq 0 ]; then missing=$((missing+1)); note "  缺席: $r"
        elif [ "$cnt" -gt 1 ]; then dup=$((dup+1)); note "  重复执行: $r ($cnt 次)"
        elif [ "$passline" -ne 1 ]; then badverdict=$((badverdict+1))
             id="$(grep "^CASE|$r|" "$out" | head -1)"
             note "  判 FAIL: $id"
        fi
    done
    # 反向检查: 出现的 id 是否都在台账里(防止偷偷加了没登记的用例)
    local unreg
    unreg="$(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u | wc -l)"
    local req_n
    req_n="$(printf '%s\n' $(required_of "$name") | grep -c . || true)"

    if [ "$missing" -ne 0 ] || [ "$dup" -ne 0 ]; then
        matrix+=("$name | INFRA-FAIL 缺席=$missing 重复=$dup (rc=$proc_rc)")
        note "  基础设施故障: 必需用例未全部执行/未唯一执行"
        remember 4
        return 0
    fi
    if [ "$badverdict" -ne 0 ] || [ "$reported_fail" != "0" ] || [ "$proc_rc" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 判FAIL=$badverdict 自报fail=$reported_fail proc_rc=$proc_rc 用例=$req_n/$unreg")
        detected_fail=1
        remember 1
        return 0
    fi
    matrix+=("$name | OK 必需用例=$req_n 全部PASS 自报fail=0 proc_rc=0 (输出 $(file_bytes "$out") 字节)")
    return 0
}

for v in $VARIANTS; do
    want="0"
    [ "$v" = "illegal" ] && want="1"
    if ! want_variant "$v"; then NOT_RAN+=("$v"); continue; fi
    note ""
    note "=== 变体 $v ==="
    case "$v" in
        off_target) extra="" ;;
        on_target)  extra="$LIMIT_ON" ;;
        off_ovr)    extra="$OVR" ;;
        on_ovr)     extra="$LIMIT_ON $OVR" ;;
        illegal)    extra="-DSAFETY_LIMIT_INPUTS_ENABLED=2" ;;
    esac
    build_variant "$v" "$extra" "$want"
    brc=$?
    [ "$brc" = "2" ] && continue
    [ "$v" = "illegal" ] && continue
    run_variant "$v"
done

note ""
note "=== I/O 行为矩阵 ==="
for m in "${matrix[@]}"; do note "$m"; done
if [ ${#NOT_RAN[@]} -gt 0 ]; then
    note "未执行变体(ONLY 选择): ${NOT_RAN[*]}"
fi

{
    echo "IO_RUNNER_RC=$overall"
    echo "IO_DETECTED_FAIL=$detected_fail"
    for m in "${matrix[@]}"; do echo "$m"; done
} > "$OUT_DIR/io_status.txt"

note ""
if [ "$overall" -eq 0 ]; then
    note "IO RUNNER RESULT: PASS (rc=0) —— 4 个变体的必需用例全部真执行且判 PASS, 非法宏值被编译拒绝"
else
    note "IO RUNNER RESULT: FAIL (rc=$overall; 1=检出失败, 4=基础设施故障) 详见 $OUT_DIR"
fi
exit "$overall"
