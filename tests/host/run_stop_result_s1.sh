#!/usr/bin/env bash
# run_stop_result_s1.sh - 关断结果可信链 runner (phase8-stop-result-s1, 2026-10-23)
#
# 与既有三条链**并列**执行, 不改它们(旧 runner 已审, 冻结):
#   build_and_run.sh(V1/V2) / run_io_behavior.sh / run_negatives.sh(+元负例)
#   / run_safety_data_semantics.sh
# 本链真编译并执行 applications 里的
#   step_pwm.c + motor.c + safety_state.c
# 夹具是 tests/host/ps_main.c + tests/host/shim_ps/(RT/GPIO/PWM/其他服务全为显式桩)。
#
# 变体(同一份生产源码, 只改预处理宏; 目标四宏本身不改):
#   target    不加覆盖 => 四道硬件宏全部 RT_FALSE = 目标发布配置
#   host_ovr  -DPS_GATE_HOST_OVERRIDE + 三道宏 RT_TRUE(**仅主机**)
#             用于走通"诊断持有输出 / arm / 斜坡 / 守卫"这些目标宏下不可达的前置;
#             PS05a..g / PS07* / PS09c 因此只在 host_ovr 登记(见下方台账注释)。
#
# 2026-10-23 返修(R01..R04 = S1-NEW-01..04)追加的 ID:
#   PS12a/PS12b(两变体)      关断失败期间拒写 DIR / 正常静止仍可设置(只收紧)
#   PS12c(仅 host_ovr)       真实 arm+start+斜坡让模拟输出在跑、关断与许可都失败后 DIR 仍被拒
#   PS13a..PS13d(两变体)     init 拿不到设备句柄的出口: 仍请求许可 LOW、两个原始 rc 分列、
#                            不建线程、绝不用 NULL 句柄调 PWM、设备回来后重新真实关断
#   PS14a..PS14f(两变体)     普通 disarm 失败投**一次** EVT_SOFT_FAULT 交给既有链 -> 统一停机
#                            (消费者是**桩**: safety_thread.c 未在本链编译, 不冒称新线程端到端)
#                            -> 锁存仍在 -> 坏输入仍拒 -> 人工 reset 可达 MANUAL_CLEAR -> 停机零投递
#   PS14g/h/i(仅 host_ovr)   诊断侧同一形状: pwm_test stop 失败投一次、force_stop 在统一停机
#                            内部绝不投事件、人工 reset 能恢复
#   PS15a/b/c(仅 host_ovr)   apply 失败回退只做一次 checked 关断并携带其真实结果;
#                            之后一次成功只更新"当前结果", 不抹掉首错证据(锁存/门禁/事件计数)
#
# 同一轮把 PS07c 的"至少调用两次 disable"改成"恰好一次": 复审指出第二次是多余关断, 且会
# 用后一次的成功覆盖前一次的失败(stop_rc/来源都被改写), "两次"从来不是产品要求(任务书
# §4.1/§4.2 与返修 §6.2 R04)。业务判据(失败被消费、来源正确、LOW 动作、锁存/FAULT、
# 事件次数)全部保留并加强; 既有 138 例与 V1/V2 的断言未放宽。
#
# 判定纪律(沿用 R2/R3/R4 与 SD-NEW-01/02 的收口结果, 不新建通用平台):
#   - 任何 rm / mkdir / 重定向 / 编译**之前**先过 require_outside_repo; 只删带本工具
#     哨兵 .ns800_host_work 的目录; 每种运行用独立 run_id 目录, 保留旧结果。
#   - 编译失败不跑旧产物; 逐 id 台账: 必需 id **出现恰好一次且判 PASS**;
#     缺席/重复 => 基础设施故障(4); 判 FAIL / 自报 fail!=0 / 真实 rc!=0 => 检出失败(1)。
#   - 台账自洽: SUMMARY 恰好一条, cases/pass/fail 与实际 CASE 行数一致,
#     且出现的 id 全部在登记集合内(没有偷偷加没登记的用例) —— 正常与反向共用同一个
#     validate_ledger(), 不写两份判定。
#   - 反向"检出"必须同时满足: 副本构建成功 + 夹具**合同退出码 rc=1** + 完整且自洽的
#     适用台账 + 指定 FAIL 身份全部命中。非 0/1 的退出(信号/异常终止)或台账不完整
#     => INFRA-FAIL(4), 不得记 META-OK; rc=0 或缺期望身份 => NOT-DETECTED(1)。
#
# 用法: bash tests/host/run_stop_result_s1.sh
#       PS_OUT_DIR=<仓外目录> SRC_APP=<快照目录> GCC=<编译器> ...
#       REVERSE=1 bash tests/host/run_stop_result_s1.sh   # 两项仓外局部反向
# 退出码: 0=通过; 1=检出失败; 4=基础设施故障; 127=环境缺失

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_ps}"
PS_MAIN="${PS_MAIN:-$HERE/ps_main.c}"
PY="${PY:-python}"
# CP3 §9 第 1 项: 编译**真实的公开 drv_epwm.h**(不拷进输出目录, 不动 SDK),
# 只补一个最小的 <rtdef.h> 兼容头(它在该头里只用于拿 rt_uint32_t 等定型类型)。
HAL_DRV="${HAL_DRV:-$REPO_ROOT/rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/HAL_Drivers/drivers}"

# ---- 用例台账(必须与 ps_main.c 里 CHECK 的 id 一字不差) ----
COMMON_CASES="PS01a-init_disable_fail_rc PS01b-init_disable_fail_still_low \
PS01c-init_no_ramp_thread_not_healthy PS01d-init_records_unknown_and_latches \
PS01e-init_success_control_creates_thread PS01f-init_success_updates_but_keeps_latch \
PS02a-estop_pwmfail_returns_pwm_rc PS02b-estop_pwmfail_low_action_counted \
PS02c-estop_pwmfail_output_still_running PS02d-estop_pwmfail_records_unknown \
PS03a-both_fail_keeps_both_rc PS03b-gpio_only_fail_keeps_gpio_rc \
PS03c-all_ok_returns_eok_only_api_accepted \
PS04a-disarm_fail_rc_is_pwm PS04b-disarm_fail_not_clean_idle \
PS04c-arm_refused_with_bit7 PS04d-no_high_write_on_refused_takeover \
PS05h-not-held-close_is_not_a_failure PS05i-never-held_status_no_false_claim \
PS06a-motor_close_and_low_always_attempted PS06b-latches_with_one_blackbox_trigger \
PS06c-no_false_step_stopped PS06d-no_event_self_excitation \
PS08a-clear_refused_when_pwm_uncertain PS08b-clear_still_runs_input_and_enn_checks \
PS08c-successful_stop_no_auto_recover \
PS09a-manual_clear_after_off_confirmed PS09b-no_auto_output_or_enable_after_clear \
PS10a-nosample_still_attempts_both_closes \
PS10b-enn_unknown_and_pwm_fail_recorded_separately \
PS10c-post_disarm_recheck_still_bites \
PS12a-dir_refused_while_stop_uncertain PS12b-dir_still_allowed_when_all_closes_ok \
PS13a-missing_dev_still_requests_low \
PS13b-missing_dev_keeps_both_rc_no_thread_no_null_pwm \
PS13c-missing_dev_and_low_both_fail_kept \
PS13d-device_back_re-attempts_real_close_no_stale_flag \
PS14a-disarm_pwmfail_posts_software_fault_once \
PS14b-consumer_latches_so_manual_recovery_is_reachable \
PS14c-plain_success_after_handoff_does_not_self_recover \
PS14d-active_input_still_refuses_recovery \
PS14e-manual_recovery_clears_after_plain_failure \
PS14f-unified_shutdown_never_posts_events \
PS16a-window_forbidden_calls_zero"
TARGET_ONLY_CASES="PS11a-arm_refused_by_enable_path_macro PS11b-diag_step_denied \
PS11b2-gate_check_is_read_only \
PS11c-tmc_write_gate_has_macro_bit PS11d-status_queries_have_no_side_effects \
PS11e-snapshot_and_readonly_agree"
# 下面这些 id 的**前置**(诊断持有输出 / arm 成功 / 斜坡真的跑)在目标四宏 FALSE 下
# 不可达(诊断 enable 与 arm 都会被宏拒绝), 因此只登记在 host_ovr; 这不是放宽判据:
# 目标变体用 PS11*(宏门确实拒绝)与 PS05h/PS05i(未持有语义)覆盖同一批代码路径。
OVR_ONLY_CASES="PS05a-force_stop_fail_keeps_owner PS05b-repeat_stop_still_not_released \
PS05c-new_diag_enable_refused PS05d-motor_takeover_refused \
PS05e-step_status_not_reported_safe PS05f-grant_refused_by_diag_latch \
PS05g-owner_released_success_latch_kept \
PS07a-ramp_zero_fail_not_normal_idle PS07b-ramp_zero_fail_consumes_error \
PS07c-apply_fail_failclosed_consumes PS07d-guard_stop_uses_checked_off \
PS07d2-guard_stop_latches_on_fail PS07e-arm_rollback_pwm_rc_first \
PS09c-arm_works_again_after_recovery \
PS12c-dir_refused_while_real_output_running \
PS14g-diag_stop_fail_posts_software_fault_once \
PS14h-force_stop_inside_shutdown_stays_silent \
PS14i-diag_failure_manual_recovers \
PS15a-apply_fail_single_checked_disable_keeps_first_error \
PS15b-later_success_does_not_erase_first_error \
PS15c-apply_fail_control_all_ok_single_close"

VARIANTS="target host_ovr"

overall=0
detected_fail=0
declare -a matrix=()

note() { printf '%s\n' "$*"; }
remember() { case "$1" in ''|*[!0-9]*) return 0 ;; esac
             if [ "$overall" -eq 0 ] && [ "$1" -ne 0 ]; then overall="$1"; fi; }
file_bytes() { if [ -f "$1" ]; then wc -c <"$1" | tr -d ' '; else echo ABSENT; fi; }

required_of() {
    case "$1" in
        target)   echo "$COMMON_CASES $TARGET_ONLY_CASES" ;;
        host_ovr) echo "$COMMON_CASES $OVR_ONLY_CASES"    ;;
    esac
}

variant_flags() {
    case "$1" in
        target)   echo "" ;;
        host_ovr) echo "-DPS_GATE_HOST_OVERRIDE \
-DMOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_TRUE \
-DSTEP_DIAG_ISOLATED_ENV_CONFIRMED=RT_TRUE \
-DTMC_DRIVE_CONFIG_HW_VALIDATED=RT_TRUE" ;;
    esac
}

GCC_RESOLVED="$(env_detect_gcc || true)"
if [ -z "$GCC_RESOLVED" ] || [ ! -x "$GCC_RESOLVED" ]; then
    note "FATAL: 未找到可用主机编译器(设 GCC=<路径> 或把 gcc 放进 PATH)"; exit 127
fi
GCC="$GCC_RESOLVED"

# ---- 先定工作目录, 再动任何文件(R4-A 纪律) ----
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${PS_OUT_DIR-${PS_WORK-$(env_work_root)/s1}/$RUN_ID}"
if ! OUT_DIR="$(require_outside_repo "$OUT_DIR" "PS_OUT_DIR")"; then
    note "FATAL: PS_OUT_DIR 被工作目录守卫拒绝(未创建目录, 未重定向, 未编译)"
    exit 4
fi
[ -f "$PS_MAIN" ]  || { note "FATAL: 夹具源不存在: $PS_MAIN"; exit 127; }
[ -d "$SHIM_DIR" ] || { note "FATAL: 兼容层目录不存在: $SHIM_DIR"; exit 127; }
[ -d "$SRC_APP" ]  || { note "FATAL: 源码目录不存在: $SRC_APP"; exit 127; }

if [ -e "$OUT_DIR" ] && [ ! -f "$OUT_DIR/.ns800_host_work" ]; then
    note "FATAL: 输出目录已存在但没有本工具的哨兵, 拒绝清理: $OUT_DIR"
    exit 4
fi
mkdir -p "$OUT_DIR" || { note "FATAL: 无法创建 OUT_DIR"; exit 4; }
printf 'ns800-host-chain stop-result-s1 runner work dir (run_id=%s)\n' "$RUN_ID" \
    > "$OUT_DIR/.ns800_host_work" || { note "FATAL: 哨兵写不进去"; exit 4; }

FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function"

# CP3 §9 第 1 项的最小兼容层: 真实 drv_epwm.h 里只用到 <rtdef.h> 的定型类型,
# 由本目录生成的 rtdef.h 转发到夹具的 rtthread.h(不复制 SDK 文件, 不改 SDK)。
COMPAT_DIR="$OUT_DIR/compat"
mkdir -p "$COMPAT_DIR" || { note "FATAL: 无法创建兼容头目录"; exit 4; }
printf '#ifndef NS800_HOST_RTDEF_COMPAT_H\n#define NS800_HOST_RTDEF_COMPAT_H\n#include <rtthread.h>\n#endif\n' \
    > "$COMPAT_DIR/rtdef.h" || { note "FATAL: 兼容头写不进去"; exit 4; }
printf '#ifndef NS800_HOST_RTHW_COMPAT_H\n#define NS800_HOST_RTHW_COMPAT_H\n#include <rtthread.h>\nrt_base_t rt_hw_interrupt_disable(void);\nvoid rt_hw_interrupt_enable(rt_base_t level);\n#endif\n' \
    > "$COMPAT_DIR/rthw.h" || { note "FATAL: rthw 兼容头写不进去"; exit 4; }
[ -f "$HAL_DRV/drv_epwm.h" ] || { note "FATAL: 真实 drv_epwm.h 不存在: $HAL_DRV/drv_epwm.h"; exit 127; }
note "drv_epwm.h=$HAL_DRV/drv_epwm.h (真实公开头) compat=$COMPAT_DIR/{rtdef.h,rthw.h}"
INCS=(-I "$SHIM_DIR" -I "$SRC_APP" -I "$HAL_DRV" -I "$COMPAT_DIR")

note "###### 关断结果可信链 S1 (基线 $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)) ######"
note "run_id=$RUN_ID"
note "SRC_APP=$SRC_APP"
note "OUT_DIR=$OUT_DIR"
note "compiler=$("$GCC" --version | head -1)"

# --------------------------------------------------------------------------
# 台账核对(正常与反向共用同一份判据)
#   返回: 0=台账自洽且必需 id 全部 PASS
#         2=台账不自洽(缺席/重复/未登记/SUMMARY 不符) —— 一律基础设施故障
#         3=台账自洽但存在判 FAIL 的必需用例
#   同时把统计写进全局: g_miss g_dup g_unreg g_bad g_lines g_sum_ok
# --------------------------------------------------------------------------
validate_ledger() {
    local out="$1" name="$2" r cnt passline
    g_miss=0; g_dup=0; g_bad=0; g_unreg=0; g_lines=0; g_sum_ok=0
    g_req_n=0; g_sum_cases=""; g_sum_pass=""; g_sum_fail=""; g_sum_lines=0

    for r in $(required_of "$name"); do
        g_req_n=$((g_req_n+1))
        cnt="$(grep -c "^CASE|$r|" "$out")"
        passline="$(grep -c "^CASE|$r|PASS|" "$out")"
        if [ "$cnt" -eq 0 ]; then g_miss=$((g_miss+1)); note "  缺席: $r"
        elif [ "$cnt" -gt 1 ]; then g_dup=$((g_dup+1)); note "  重复执行: $r ($cnt 次)"
        elif [ "$passline" -ne 1 ]; then g_bad=$((g_bad+1))
             note "  判 FAIL: $(grep "^CASE|$r|" "$out" | head -1)"
        fi
    done

    g_lines="$(grep -cE '^CASE\|' "$out")"
    g_sum_lines="$(grep -cE '^SUMMARY\|' "$out")"
    if [ "$g_sum_lines" -eq 1 ]; then
        g_sum_cases="$(sed -n 's/^SUMMARY|cases=\([0-9]*\).*/\1/p' "$out")"
        g_sum_pass="$(sed -n 's/.*pass=\([0-9]*\).*/\1/p' "$out")"
        g_sum_fail="$(sed -n 's/.*fail=\([0-9]*\).*/\1/p' "$out")"
        local reg all
        reg="$(printf '%s\n' $(required_of "$name") | grep -c . )"
        all="$(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u | wc -l)"
        # 出现的 id 必须是登记集合的子集(不允许偷偷加没登记的用例)
        g_unreg="$(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u | while
                       read -r id; do
                           printf '%s\n' $(required_of "$name") | grep -qx -- "$id" || echo "$id"
                       done | grep -c . )"
        if [ "$g_sum_cases" = "$g_lines" ] && \
           [ "$((g_sum_pass + g_sum_fail))" = "$g_lines" ]; then
            g_sum_ok=1
        fi
        note "  台账: 必需=$reg 唯一出现=$all 实际行数=$g_lines SUMMARY(cases=$g_sum_cases pass=$g_sum_pass fail=$g_sum_fail) 自洽=$g_sum_ok"
    else
        note "  SUMMARY 行数=$g_sum_lines (必须恰好 1) —— 台账不可信"
    fi

    if [ "$g_sum_lines" -ne 1 ] || [ "$g_sum_ok" -ne 1 ] || [ "$g_unreg" -ne 0 ] || \
       [ "$g_miss" -ne 0 ] || [ "$g_dup" -ne 0 ]; then
        return 2
    fi
    [ "$g_bad" -ne 0 ] && return 3
    return 0
}

# --------------------------------------------------------------------------
# 正向: 每个变体 编译 -> 真执行 -> 台账核对
# --------------------------------------------------------------------------
build_run_variant() {
    local name="$1" log exe out marker proc_rc vrc
    log="$OUT_DIR/${name}_build.txt"; exe="$OUT_DIR/${name}.exe"
    out="$OUT_DIR/${name}_out.txt";   marker="$OUT_DIR/${name}.executed.marker"
    local extra; extra="$(variant_flags "$name")"

    rm -f "$exe" "$marker" "$out"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra "${INCS[@]}" "$PS_MAIN" -o "$exe" > "$log" 2>&1
    vrc=$?
    if [ "$vrc" -ne 0 ]; then
        tail -20 "$log"
        note "  $name 编译失败 rc=$vrc => 不运行二进制(旧产物已删除)"
        matrix+=("$name | COMPILE_FAILED rc=$vrc")
        remember 4
        return 1
    fi
    printf 'LAUNCHED exe=%s\n' "$exe" > "$marker"
    "$exe" > "$out" 2>&1
    proc_rc=$?
    printf 'DONE rc=%s exe=%s out_bytes=%s\n' "$proc_rc" "$exe" "$(file_bytes "$out")" \
        > "$marker"
    note "  $name 真实进程 rc=$proc_rc 输出 $(file_bytes "$out") 字节"

    validate_ledger "$out" "$name"
    vrc=$?
    if [ "$vrc" -eq 2 ]; then
        matrix+=("$name | INFRA-FAIL 台账不自洽 缺席=$g_miss 重复=$g_dup 未登记=$g_unreg SUMMARY自洽=$g_sum_ok (rc=$proc_rc)")
        remember 4; return 1
    fi
    if [ "$vrc" -eq 3 ] || [ "$proc_rc" -ne 0 ] || [ "$g_sum_fail" != "0" ] || \
       [ "$g_sum_pass" != "$g_lines" ]; then
        matrix+=("$name | NOT-DETECTED 判FAIL=$g_bad 自报fail=$g_sum_fail proc_rc=$proc_rc 必需=$g_req_n/$g_lines")
        detected_fail=1; remember 1; return 1
    fi
    matrix+=("$name | OK 必需用例=$g_req_n 全部PASS 实际行数=$g_lines 自报 fail=0 proc_rc=0")
    return 0
}

# --------------------------------------------------------------------------
# 反向(两项, 只改**仓库外**的生产源码副本)
# --------------------------------------------------------------------------
reverse_case() {
    local name="$1" variant="$2" target="$3" ids="$4"
    local work exe brc proc_rc rc id hit=0 miss=0 vrc
    local old_file new_file
    work="$OUT_DIR/reverse/$name"

    rm -rf "$work"; mkdir -p "$work/app" || { note "  $name: 无法建副本目录"; remember 4; return; }
    cp "$SRC_APP/$target" "$work/app/$target" || { note "  $name: 单文件副本失败"; remember 4; return; }

    # 注入脚本(只写一次, 落不到就判基础设施故障, 绝不当作"检出")
    cat > "$work/inject.py" <<'PYEOF'
import io, sys
path, mode = sys.argv[1], sys.argv[2]
PAIRS = {
    # 反向①(2026-10-24 CP4 §10.2 重指向): "关断失败被当成驱动已接受"这件事在修后由
    # 协调层决定(sp_note_pwm_held), 不再在 motor.c/step_pwm.c 里各自记镜像。
    'rev_motor_off_ignores_failure': [(
        '        sp.pwm.result = (pe == RT_EOK) ? MOT_STOP_API_ACCEPTED : MOT_STOP_API_FAILED;',
        '        sp.pwm.result = MOT_STOP_API_ACCEPTED;'
        '   /* NEGATIVE-INJECTION(rev_motor_off_ignores_failure): 失败一律记成已接受 */')],
}
subs = PAIRS[mode]
s = io.open(path, encoding='utf-8').read()
out = s
for old, new in subs:
    n = out.count(old)
    if n != 1:
        sys.stderr.write('INJECT-FATAL: 锚点命中 %d 次(应恰 1 次): %s\n' % (n, old[:60]))
        sys.exit(1)
    out = out.replace(old, new, 1)
io.open(path, 'w', encoding='utf-8', newline='').write(out)
print('injected mode=%s replacements=1' % mode)
PYEOF

    if ! "$PY" "$work/inject.py" "$work/app/$target" "$name" > "$work/inject.txt" 2>&1; then
        cat "$work/inject.txt"
        note "  $name: 注入未落地 => 判基础设施故障, 不当作检出"
        matrix+=("$name | INFRA-FAIL 注入未落地"); remember 4; return
    fi
    grep -q "NEGATIVE-INJECTION" "$work/app/$target" || {
        note "  $name: 副本里没有注入标记"; matrix+=("$name | INFRA-FAIL 无注入标记")
        remember 4; return; }

    exe="$work/ps.exe"
    # 其余生产文件仍从原 SRC_APP 取(-I 指向副本目录优先, 只有被注入的那一个文件命中副本)
    cp "$SRC_APP"/*.h "$work/app/" 2>/dev/null
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $(variant_flags "$variant") -I "$SHIM_DIR" -I "$work/app" -I "$SRC_APP" \
        -I "$HAL_DRV" -I "$COMPAT_DIR" "$PS_MAIN" -o "$exe" > "$work/build.txt" 2>&1
    brc=$?
    if [ "$brc" -ne 0 ]; then
        tail -12 "$work/build.txt"
        note "  $name: 被注入副本编译失败 rc=$brc => 不算检出"
        matrix+=("$name | INFRA-FAIL 副本编译失败 rc=$brc"); remember 4; return
    fi
    "$exe" > "$work/out.txt" 2>&1
    proc_rc=$?
    if [ "$proc_rc" -eq 0 ]; then
        note "  $name: 注入后链仍然通过 => 没检出"
        matrix+=("$name | NOT-DETECTED rc=0 (proc) 业务失败身份完全没出现")
        detected_fail=1; remember 1; return
    fi
    if [ "$proc_rc" -ne 1 ]; then
        note "  $name: 夹具退出码 rc=$proc_rc(既不是合同要求的 1, 也不是 0)"
        note "         异常退出/信号/前置不自洽**不算**检出"
        matrix+=("$name | INFRA-FAIL 异常退出 rc=$proc_rc (合同要求 rc=1)")
        remember 4; return
    fi
    # 台账必须完整且自洽(缺例/未登记/SUMMARY 对不上都不算检出)
    validate_ledger "$work/out.txt" "$variant"
    vrc=$?
    if [ "$vrc" -eq 2 ]; then
        matrix+=("$name | INFRA-FAIL 台账不自洽 缺席=$g_miss 重复=$g_dup 未登记=$g_unreg 自洽=$g_sum_ok")
        note "  $name: 台账不完整 => 不算检出"; remember 4; return
    fi
    for id in $ids; do
        if grep -q "^CASE|$id|FAIL|" "$work/out.txt"; then
            hit=$((hit+1)); note "  命中期望失败身份: $id"
        else
            miss=$((miss+1)); note "  未命中期望失败身份: $id"
        fi
    done
    if [ "$miss" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 命中=$hit 缺失=$miss proc_rc=$proc_rc")
        detected_fail=1; remember 1; return
    fi
    matrix+=("$name | META-OK 命中 $hit/$((hit+miss)) 个业务失败身份 (variant=$variant proc_rc=$proc_rc 额外FAIL=$g_bad)")
}

REVERSE="${REVERSE:-0}"
if [ "$REVERSE" = "1" ]; then
    note ""
    note "=== 反向验证(2 项, 仓外副本; 正常对照必须先通过) ==="
    # 2026-10-24 CP4 §10.2 重指向: 注入点改到协调层之后, 失败身份按**实测命中**登记。
    # (原登记 PS01a/PS02a 只断言对外 rc, 不被"结果记成已接受"影响; 把它们继续挂在期望列表
    #  里只会得到"没命中", 那是对注入的误判, 不是产品问题。S1 的断言本身没有放宽。)
    reverse_case rev_motor_off_ignores_failure target stop_priority.c \
        "PS01d-init_records_unknown_and_latches PS02d-estop_pwmfail_records_unknown PS04a-disarm_fail_rc_is_pwm PS10b-enn_unknown_and_pwm_fail_recorded_separately PS13d-device_back_re-attempts_real_close_no_stale_flag"
    # §10.2 第②项"失败提前交回 owner"的重指向结论(不是删反向):
    # CP4 之后这条事实由协调层的身份/归还门承担, 本链不再消费它 —— 三次试投都是
    # "注入落地 + 台账自洽 + proc rc=0"(即本链抓不到):
    #   (a) 失败分支不退回 SP_HELD_UNKNOWN;
    #   (b) 许可裸写失败后不恢复 diag_output_active;
    #   (c) 撤销预约不再只看"本来由谁持有"。
    # 因此把它重指向真正消费该事实的 S2 并发链反向(同一判据、同一 runner 纪律):
    #   bash tests/host/run_stop_priority_s2.sh REVERSE=1
    #     rev_stale_identity_gate   -> C09b/C15b META-OK
    #     rev_recovery_ticket_check -> C08b      META-OK
    # 本轮两条链的反向都真实执行过, 上面的命中数字来自实际输出, 不是推断。
    matrix+=("rev_diag_fail_releases_owner | REV-POINTED 本链不消费该事实(3 次试投均 NOT-DETECTED), 由 S2 并发链反向代跑")
    note ""
    note "=== S1 反向汇总 ==="
    for m in "${matrix[@]}"; do note "$m"; done
    {
        echo "S1_REVERSE_RC=$overall"
        echo "run_id=$RUN_ID"
        for m in "${matrix[@]}"; do echo "$m"; done
    } > "$OUT_DIR/s1_reverse_status.txt"
    if [ "$overall" -eq 0 ]; then
        note "S1 REVERSE RESULT: PASS —— 本链注入命中预登记业务失败身份(rc=1, 台账自洽); 第②项按 §10.2 重指向 S2 并发链代跑(见上方 REV-POINTED 行)"
    else
        note "S1 REVERSE RESULT: FAIL (rc=$overall)"
    fi
    exit "$overall"
fi

for v in $VARIANTS; do
    note ""
    note "=== 变体 $v ==="
    build_run_variant "$v"
done

note ""
note "=== S1 关断结果矩阵 ==="
for m in "${matrix[@]}"; do note "$m"; done

{
    echo "S1_RUNNER_RC=$overall"
    echo "S1_DETECTED_FAIL=$detected_fail"
    echo "run_id=$RUN_ID"
    echo "baseline=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)"
    for m in "${matrix[@]}"; do echo "$m"; done
} > "$OUT_DIR/s1_status.txt"

note ""
if [ "$overall" -eq 0 ]; then
    note "S1 RUNNER RESULT: PASS (rc=0) —— target 与 host_ovr 两个变体的必需用例全部真执行且判 PASS"
else
    note "S1 RUNNER RESULT: FAIL (rc=$overall; 1=检出失败, 4=基础设施故障) 详见 $OUT_DIR"
fi
exit "$overall"
