#!/usr/bin/env bash
# run_meta_negatives.sh - 验收链自身失效模式的常驻检查（元负例），并**按原因**验收
#
# 为什么必须按原因核对（F2d-R1-NEW-03）: 只判"子链 rc 非零"会把错误原因当成正确结论 ——
# 例如给 M1 的致盲夹具副本换上一个必然编译失败的编译器桩, 子链以 rc=4(INFRA-FAIL) 失败,
# 但那**不是**"FD7b 漏检被抓到", 旧助手却照样报 META-OK。本版逐条核对:
#   子链 rc、命中用例身份、失败类别、拒绝原因文本、以及"请求集合==执行集合且非空"。
# 缺矩阵 / 空矩阵 / 意外错误 / 原因不符一律不得记 META-OK。
#
# 用例集:
#   M1  复现 A(F2d-R1 §4): 致盲 V2 的 FD7b 判据 => 期望 rc=1 + N10b NOT-DETECTED +
#       原因含 `V2.run:期望=1 实际=0` 与 FD7b 身份缺席
#   M1c 未致盲对照 => rc=0 且 N10b 为 OK
#   M2  复现 B: 编译器桩让 N1..N10b 全部编译失败(没有任何测试进程执行) => rc=4 且 11 例
#       全部 INFRA-FAIL, 原因含 runner.proc_rc 失配与 COMPILE_FAILED
#   M2c 真实编译器对照 => rc=0 且 11 例全部 OK 且集合完整
#   M3  R2-NEW-01: 把 runner 副本最后的 `exit "$overall"` 改成 `exit 0`(状态文件照旧自报)
#       => 必须拒绝, 原因含 runner.proc_rc 与 runner.reported_rc 两处失配; 对照必须 rc=0
#   M4  R2-NEW-02: 歧义前缀(N1)/未知选择(ZZZ)必须非零并给出原因; 无歧义前缀(N10b)必须
#       真跑 1 例且"请求集合==执行集合"
#   M5  助手自检: M1 场景被制造成"错误原因"(编译桩) => 助手不得记 META-OK
#
# 用法: bash tests/host/run_meta_negatives.sh
#       ONLY_META=M1 M1_GCC=<桩> META_DEEP=1 bash tests/host/run_meta_negatives.sh
# 退出码: 0=全部按预期; 1=有元负例被放过/原因不符/对照组失败/集合不符

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO="${REPO:-$(env_repo_root)}"
BASE_HEAD="${BASE_HEAD:-$(env_default_head)}"
PY="${PY:-python}"
WORK="${META_WORK-$(env_work_root)/meta}"   # 显式空值 => 拒绝而非静默替换
ONLY_META="${ONLY_META:-}"
META_DEEP="${META_DEEP:-0}"
M1_GCC="${M1_GCC:-}"
overall=0
declare -a TABLE META_RAN
TABLE=(); META_RAN=()
CHECK_NOTES=()
CHECK_FAIL_N=0
CUR_M="" CUR_LOG="" CUR_RC=0 CUR_GROUP=""

NEGS11="N1_only_V1_injected_fail N2_only_V2_injected_fail N3_V1_stale_exe_not_reused N4_fw_calc_selftest_FAILED N5_fw_cfg_selftest_INCOMPLETE N6_helper_thread_create_fail N7_helper_thread_startup_fail N8_gstat_expect_table_tamper N9_probe_writeback_V1 N10_probe_writeback_V2 N10b_writeback_both_variants"
ALL_META=(M1 M1c M2 M2c M3 M4)
[ "$META_DEEP" = "1" ] || ALL_META+=(M5)
REQUESTED_META=()

die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
ok_note()  { printf '      [.] %s\n' "$*"; }
bad_note() { printf '      [X] %s\n' "$*"; CHECK_FAIL_N=$((CHECK_FAIL_N+1)); CHECK_NOTES+=("$*"); }

# ---------------- 选择入口: 完整名或无歧义前缀(与 run_negatives.sh 同一语义) ----------------
resolve_meta_selection() {
    local tok hit n
    if [ -z "$ONLY_META" ]; then REQUESTED_META=("${ALL_META[@]}"); return 0; fi
    REQUESTED_META=()
    for tok in $ONLY_META; do
        hit=()
        for n in "${ALL_META[@]}"; do [ "$n" = "$tok" ] && hit=("$n"); done
        if [ ${#hit[@]} -eq 0 ]; then
            for n in "${ALL_META[@]}"; do case "$n" in "$tok"*) hit+=("$n");; esac; done
        fi
        [ ${#hit[@]} -eq 0 ] && { printf 'FATAL: ONLY_META "%s" 不匹配任何元负例(可用: %s)\n' "$tok" "${ALL_META[*]}" >&2; exit 1; }
        [ ${#hit[@]} -gt 1 ] && { printf 'FATAL: ONLY_META "%s" 歧义, 命中 %d 个: %s\n' "$tok" "${#hit[@]}" "${hit[*]}" >&2; exit 1; }
        REQUESTED_META+=("${hit[0]}")
    done
    [ ${#REQUESTED_META[@]} -eq 0 ] && { echo "FATAL: 元负例请求集合为空" >&2; exit 1; }
    return 0
}
want_meta() { local m; for m in "${REQUESTED_META[@]}"; do [ "$m" = "$1" ] && return 0; done; return 1; }

# ---------------- 注入助手(统一走 inject.py) ----------------
inject_copy() {                        # inject_copy <源> <目标> <模式>
    local src="$1" dst="$2" mode="$3"
    rm -f "$dst"; cp "$src" "$dst" || return 1
    "$PY" "$HERE/inject.py" "$dst" "$mode" || return 1
    grep -aq 'NEGATIVE-INJECTION' "$dst" || { printf 'FATAL: 注入未落地: %s\n' "$dst" >&2; return 1; }
    return 0
}

# ---------------- 子链执行与断言 ----------------
sub_chain() {                          # sub_chain <子目录名> [env 赋值...]
    local name="$1"; shift
    local w="$WORK/$name"
    if [ -e "$w" ] && [ ! -f "$w/.ns800_host_work" ]; then
        die "子链目录已存在且无哨兵, 拒绝 rm -rf: $w"
    fi
    rm -rf "$w"; mkdir -p "$w" || die "无法创建 $w"
    printf 'sentinel
' > "$w/.ns800_host_work"
    ( cd "$HERE" && env "$@" NEG_WORK="$w" NEG_META_SKIP=1 BASE_HEAD="$BASE_HEAD" \
        bash "$HERE/run_negatives.sh" ) > "$WORK/$name.runner.log" 2>&1
    CUR_RC=$?
    CUR_M="$w/verdict_matrix.txt"; CUR_LOG="$WORK/$name.runner.log"
    printf '    子链 %s: rc=%s 矩阵行数=%s\n' "$name" "$CUR_RC" \
        "$([ -f "$CUR_M" ] && grep -a -c '' "$CUR_M" || echo 无文件)"
}

row_of()   { [ -f "$CUR_M" ] && grep -a -E "^(OK|NOT-DETECTED|INFRA-FAIL) \| $1 \|" "$CUR_M" | head -1; }
class_of() { row_of "$1" | awk -F'|' '{gsub(/ /,"",$1); print $1}'; }
matrix_sane() {
    [ -f "$CUR_M" ] || { bad_note "缺 verdict_matrix.txt(基础设施故障, 不得算通过)"; return 1; }
    [ "$(grep -a -c '' "$CUR_M")" -gt 0 ] || { bad_note "verdict_matrix.txt 为空(0 例执行)"; return 1; }
    grep -aqE '^(OK|NOT-DETECTED|INFRA-FAIL) \|' "$CUR_M" || { bad_note "矩阵里没有可解析的判定行"; return 1; }
    return 0
}
need_rc_eq()      { [ "$CUR_RC" = "$1" ] && ok_note "子链 rc=$CUR_RC (=$1)" || bad_note "子链 rc=$CUR_RC 期望=$1"; }
need_rc_nonzero() { [ "$CUR_RC" -ne 0 ] && ok_note "子链 rc=$CUR_RC 非零" || bad_note "子链 rc=0, 坏结果被放过"; }
need_class() {
    local c; c="$(class_of "$1")"
    [ "$c" = "$2" ] && ok_note "$1 类别=$c" || bad_note "$1 类别='$c' 期望='$2'"
}
need_row_has() {
    local r; r="$(row_of "$1")"
    printf '%s' "$r" | grep -aqE -- "$2" && ok_note "$1 原因含 /$2/" \
        || bad_note "$1 原因缺 /$2/ (行: ${r:-<无该行>})"
}
need_all_class() {                     # need_all_class <用例列表> <期望类别>
    local list="$1" want="$2" n c
    for n in $list; do c="$(class_of "$n")"; [ "$c" = "$want" ] || bad_note "$n 类别='$c' 期望='$want'"; done; }
need_set_complete() {                  # 判定行集合 == 请求集合
    local -a req; IFS=' ' read -r -a req <<< "$1"
    local have n miss=0 extra=0
    have="$(grep -a -oE '^(OK|NOT-DETECTED|INFRA-FAIL) \| [A-Za-z0-9_]+' "$CUR_M" | awk -F'|' '{gsub(/ /,"",$2); print $2}')"
    for n in "${req[@]}"; do printf '%s\n' "$have" | grep -qxF -- "$n" || { bad_note "集合缺 $n"; miss=1; }; done
    for n in $have; do printf '%s\n' "${req[*]}" | grep -qw -- "$n" || { bad_note "集合多出 $n"; extra=1; }; done
    [ $((miss + extra)) -eq 0 ] && ok_note "执行集合与请求集合一致(${#req[@]} 例)"
}

verdict() {                            # verdict <显示名> <通过标签> <失败标签>
    local name="$1" good="$2" bad="$3"
    META_RAN+=("$CUR_GROUP")           # 集合对账用的是组键(M1/M1c/...), 不是显示名
    if [ "$CHECK_FAIL_N" -eq 0 ]; then
        TABLE+=("$good | $name")
        printf '  => %s | %s\n' "$good" "$name"
    else
        TABLE+=("$bad | $name | 不符 ${CHECK_FAIL_N} 项: ${CHECK_NOTES[*]}")
        printf '  => %s | %s (不符 %d 项)\n' "$bad" "$name" "$CHECK_FAIL_N"
    fi
    [ "$CHECK_FAIL_N" -eq 0 ] || overall=1
    CHECK_FAIL_N=0; CHECK_NOTES=()
}

# ---------------- 准备工作区(必须仓库外) ----------------
# R4-A: 守卫在 rm/mkdir 之前(与 negatives/normal 共用同一规则), 同样只删有哨兵的目录
if ! WORK="$(require_outside_repo "$WORK" "META_WORK")"; then
    die "元负例工作区被守卫拒绝(未执行任何 rm/mkdir, 现场保持原样)"
fi
WORK_SENTINEL="$WORK/.ns800_host_work"
if [ -e "$WORK" ] && [ ! -f "$WORK_SENTINEL" ]; then
    die "目标已存在但没有本工具的哨兵文件, 拒绝 rm -rf: $WORK"
fi
rm -rf "$WORK"; mkdir -p "$WORK" || die "无法建立 $WORK"
printf 'sentinel: 本目录由 tests/host/run_meta_negatives.sh 创建, 允许其清理
' > "$WORK_SENTINEL"
BLIND="$WORK/blinded"; RUNDIR="$WORK/runner_tampered"; mkdir -p "$BLIND" "$RUNDIR"
STUB="$WORK/gcc_stub_65.sh"
cat > "$STUB" <<'STUBEOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then echo "gcc_stub_65 (meta-negative compiler)"; exit 0; fi
echo "stub: forced compile failure (rc=65)" >&2
exit 65
STUBEOF
chmod +x "$STUB" 2>/dev/null
[ -x "$STUB" ] || die "编译器桩不可执行: $STUB"

resolve_meta_selection
echo "############ 元负例(按原因验收; 基线 $BASE_HEAD; 请求集合 ${REQUESTED_META[*]}) ############"

# ================= M1 / 复现 A =================
if want_meta M1; then
    CUR_GROUP="M1"
    inject_copy "$HERE/host_main.c" "$BLIND/host_main.c" fd7b_blinding \
        || die "M1 致盲注入失败"
    if [ -n "$M1_GCC" ]; then
        sub_chain M1 HOST_SRC_OVERRIDE="$BLIND/host_main.c" GCC_OVERRIDE="$M1_GCC" \
                  ONLY="N10b_writeback_both_variants"
    else
        sub_chain M1 HOST_SRC_OVERRIDE="$BLIND/host_main.c" ONLY="N10b_writeback_both_variants"
    fi
    matrix_sane
    need_rc_eq 1
    need_class N10b_writeback_both_variants NOT-DETECTED
    need_row_has N10b_writeback_both_variants 'V2\.run:期望=1 实际=0'
    need_row_has N10b_writeback_both_variants 'FD7b'
    need_set_complete "N10b_writeback_both_variants"
    verdict M1_blinded_fd7b META-OK META-LEAK
fi

# ================= M1 对照 =================
if want_meta M1c; then
    CUR_GROUP="M1c"
    sub_chain M1_ctl ONLY="N10b_writeback_both_variants"
    matrix_sane
    need_rc_eq 0
    need_class N10b_writeback_both_variants OK
    need_set_complete "N10b_writeback_both_variants"
    verdict M1_control_unblinded CONTROL-OK CONTROL-FAIL
fi

# ================= M2 / 复现 B =================
if want_meta M2; then
    CUR_GROUP="M2"
    sub_chain M2 GCC_OVERRIDE="$STUB" ONLY="$NEGS11"
    matrix_sane
    need_rc_eq 4
    need_all_class "$NEGS11" INFRA-FAIL
    need_row_has N1_only_V1_injected_fail 'runner\.proc_rc:期望=1 实际=65'
    need_row_has N1_only_V1_injected_fail 'COMPILE_FAILED'
    need_set_complete "$NEGS11"
    verdict M2_stubbed_compile META-OK META-LEAK
fi

# ================= M2 对照 =================
if want_meta M2c; then
    CUR_GROUP="M2c"
    sub_chain M2_ctl ONLY="$NEGS11"
    matrix_sane
    need_rc_eq 0
    need_all_class "$NEGS11" OK
    need_set_complete "$NEGS11"
    verdict M2_control_real_compiler CONTROL-OK CONTROL-FAIL
fi

# ================= M3 / 出口码丢失 =================
if want_meta M3; then
    CUR_GROUP="M3"
    inject_copy "$HERE/build_and_run.sh" "$RUNDIR/build_and_run.sh" runner_exit_zero \
        || die "M3 runner 副本注入失败"
    cp "$HERE/env.sh" "$RUNDIR/env.sh" || die "M3 无法复制 env.sh"
    sub_chain M3 RUNNER_SCRIPT="$RUNDIR/build_and_run.sh" ONLY="N1_only_V1_injected_fail"
    matrix_sane
    need_rc_nonzero
    need_class N1_only_V1_injected_fail INFRA-FAIL
    need_row_has N1_only_V1_injected_fail 'runner\.proc_rc:期望=1 实际=0'
    need_row_has N1_only_V1_injected_fail 'runner\.reported_rc:期望=0 实际=1'
    verdict M3_runner_exit_zero_lost META-OK META-LEAK

    CUR_GROUP="M3c"
    sub_chain M3_ctl ONLY="N1_only_V1_injected_fail"
    matrix_sane
    need_rc_eq 0
    need_class N1_only_V1_injected_fail OK
    need_row_has N1_only_V1_injected_fail 'rc 真实=1 自报=1 期望=1'
    verdict M3_control_unmodified CONTROL-OK CONTROL-FAIL
fi

# ================= M4 / 选择入口语义 =================
if want_meta M4; then
    CUR_GROUP="M4"
    sub_chain M4_ambiguous ONLY="N1"
    need_rc_eq 4
    grep -aq '歧义' "$CUR_LOG" && ok_note "歧义前缀被拒绝并列出候选" || bad_note "歧义前缀未被拒绝"
    if [ ! -s "$CUR_M" ]; then ok_note "歧义拒绝发生在执行之前(0 例被误跑)"; else bad_note "歧义拒绝前已经跑了用例"; fi

    sub_chain M4_unknown ONLY="ZZZ_no_such_case"
    need_rc_eq 4
    grep -aq '不匹配任何用例' "$CUR_LOG" && ok_note "未知选择被拒绝并列出登记表" || bad_note "未知选择未被拒绝"

    sub_chain M4_prefix ONLY="N10b"
    need_rc_eq 0
    grep -aq '执行集合(1) 与请求集合(1) 一致: 是' "$CUR_LOG" \
        && ok_note "无歧义前缀真跑 1 例且集合一致" || bad_note "无歧义前缀的执行集合未被确认"
    need_class N10b_writeback_both_variants OK
    verdict M4_selection_semantics META-OK META-LEAK
fi

# ================= M5 / 助手自检: 错误归因不得放行 =================
if want_meta M5; then
    CUR_GROUP="M5"
    ( cd "$HERE" && env ONLY_META="M1" M1_GCC="$STUB" META_DEEP=1 BASE_HEAD="$BASE_HEAD" \
        META_WORK="$WORK/M5_nested" bash "$HERE/run_meta_negatives.sh" ) > "$WORK/M5.log" 2>&1
    nested_rc=$?
    printf '    嵌套(M1 + 编译桩) rc=%s\n' "$nested_rc"
    [ "$nested_rc" -ne 0 ] && ok_note "嵌套助手未放行(rc=$nested_rc)" \
        || bad_note "嵌套助手把错误原因放过了(rc=0)"
    grep -aq 'META-OK | M1_blinded_fd7b' "$WORK/M5.log" \
        && bad_note "原因不符时仍记了 META-OK" || ok_note "未把错误原因记成 META-OK"
    grep -aq 'META-LEAK | M1_blinded_fd7b' "$WORK/M5.log" \
        && ok_note "明确报出 META-LEAK(原因不符)" || bad_note "没有说明拒绝原因"
    grep -aqE '子链 M1: rc=4' "$WORK/M5.log" \
        && ok_note "嵌套子链确实以 rc=4(INFRA) 失败 => 与正确原因 rc=1/NOT-DETECTED 不同" \
        || bad_note "嵌套场景没有制造出错误原因"
    verdict M5_wrong_reason_guard META-OK META-LEAK
fi

# ---------------- 元负例集合对账 ----------------
META_NOT_RAN=()
for m in "${REQUESTED_META[@]}"; do
    hitm=0
    for r in "${META_RAN[@]}"; do [ "$r" = "$m" ] && hitm=1; done
    [ "$hitm" -eq 0 ] && META_NOT_RAN+=("$m")
done
if [ ${#META_NOT_RAN[@]} -ne 0 ]; then
    echo "FATAL(元负例集合): 请求但未执行的组: ${META_NOT_RAN[*]}" >&2
    TABLE+=("META-LEAK | 集合对账 | 请求但未执行: ${META_NOT_RAN[*]}"); overall=1
fi

echo ""
echo "############ 元负例汇总表 ############"
printf '%s\n' "${TABLE[@]}"
echo ""
echo "已执行元负例组: ${META_RAN[*]:-（无）} / 请求组: ${REQUESTED_META[*]}"
if [ "$overall" -ne 0 ]; then
    echo "RUNNER(meta): 有元负例被放过、原因不符、对照组失败或集合不符 (rc=$overall)"
else
    echo "RUNNER(meta): 全部元负例按**正确原因**被拒绝, 对照组按预期通过, 集合一致 (rc=0)"
fi
exit "$overall"
