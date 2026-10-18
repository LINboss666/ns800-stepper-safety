#!/usr/bin/env bash
# run_negatives.sh - 负例与干净回归全链（逐例预期矩阵 + 出口对账 + 选择集合对账）
#
# 历史缺陷（都被独立复现过，本版的规则由它们逐条反推出来）:
#   R1 旧版 `if [ outer -ne 0 ]` 就算"故障已检出"：内层 rc 仅展示、V2 rc 从不参与判定
#      => V2 检测器失效能被 V1 失败掩盖(复现 A)，全部没执行也能报"全部检出"(复现 B)。
#   R2 本轮 F2d-R1-NEW-01：保存了 runner 的**真实**退出码却不用它判定，矩阵里的 outer
#      全部取自状态文件的自报值 => 把 runner 末尾 `exit "$overall"` 改成 `exit 0`，
#      自报仍是 RUNNER_RC=1，整条链却以 rc=0 宣布全通过。
#   R3 本轮 F2d-R1-NEW-02：`ONLY` 注释写"前缀"，代码只认完整名 => `ONLY=N10b` 实际 0 例
#      执行，仍打印"所有负例按预期被检出 (rc=0)"。
#
# 因此本版规则:
#   A. 逐例登记**预期矩阵**：V1/V2 的 state + build rc + run rc、runner **期望 rc**、
#      runner **自报 rc 必须等于真实进程 rc**、指定失败身份 ERE。逐项比对预期与实际。
#   B. 三个 rc 分开记、分名字：**真实进程退出码**(runner_proc_rc)、**自报退出码**
#      (runner_reported_rc)、**预期退出码**(expected)。三者不一致一律不合格。
#   C. 分级只有三类：`OK` / `NOT-DETECTED`(跑了但没抓到) / `INFRA-FAIL`(没执行、编译意外、
#      回执缺字段或自报≠真实)；总链退出码 0/1/4。
#   D. `ONLY` 语义 = 完整名或**无歧义前缀**；未知、零匹配、歧义一律报错非零；
#      收尾核对"请求集合 == 实际执行集合"且非空。
#   E. 跳过的变体只接受 NOT_RUN；篡改/快照步骤校验退出码与锚点命中次数(恰为 1)。
#   F. 构建产物、隔离快照、被篡改副本一律写在**仓库外**工作区。
#
# 用法:
#   bash tests/host/run_negatives.sh
#   ONLY="N10b N12" bash tests/host/run_negatives.sh
#   BASE_HEAD=<完整sha> RUNNER_SCRIPT=<runner 副本> NEG_WORK=<目录> bash tests/host/run_negatives.sh
# 退出码: 0=全部符合预期; 1=漏检/身份缺失; 4=基础设施故障或回执不一致

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO="${REPO:-$(env_repo_root)}"
BASE_HEAD="${BASE_HEAD:-$(env_default_head)}"
WORK="${NEG_WORK:-$(env_work_root)/negatives}"
RUNNER_SCRIPT="${RUNNER_SCRIPT:-$HERE/build_and_run.sh}"
APPREL="$(env_app_rel)"
APPREL_ABS="$REPO/$APPREL"
PY="${PY:-python}"
ONLY="${ONLY:-}"
HOST_SRC_OVERRIDE="${HOST_SRC_OVERRIDE:-}"      # 元负例: 被致盲的夹具副本
GCC_OVERRIDE="${GCC_OVERRIDE:-}"                # 元负例: 编译器桩
NEG_META_SKIP="${NEG_META_SKIP:-0}"
overall=0
declare -a TABLE BAD ALL_CASES
TABLE=(); BAD=(); ALL_CASES=()
declare -A RAN_CASE

# ------------------------------------------------------------------ 用例登记表
declare -A EXP_V1S EXP_V1B EXP_V1R EXP_V2S EXP_V2B EXP_V2R EXP_OUTER EXP_RUNV1 EXP_RUNV2
declare -A EXP_V1OUT EXP_V2OUT MK1 MK2
FIELDS="V1_STATE V1_BUILD_RC V1_RUN_RC V2_STATE V2_BUILD_RC V2_RUN_RC V1_EXECUTED V2_EXECUTED V1_EXE V2_EXE V1_OUT_BYTES V2_OUT_BYTES RUNNER_RC"

reg() {                            # reg <case> key=value ...
    local name="$1"; shift
    local kv k v
    for kv in "$@"; do
        k="${kv%%=*}"; v="${kv#*=}"
        case "$k" in
            v1_state) EXP_V1S[$name]="$v" ;;  v1_build) EXP_V1B[$name]="$v" ;;
            v1_run)   EXP_V1R[$name]="$v" ;;  v2_state) EXP_V2S[$name]="$v" ;;
            v2_build) EXP_V2B[$name]="$v" ;;  v2_run)   EXP_V2R[$name]="$v" ;;
            outer)    EXP_OUTER[$name]="$v" ;; run_v1)  EXP_RUNV1[$name]="$v" ;;
            run_v2)   EXP_RUNV2[$name]="$v" ;;
            v1_out)   EXP_V1OUT[$name]="$v" ;; v2_out)  EXP_V2OUT[$name]="$v" ;;
            *) echo "FATAL: 未知登记键 $k (用例 $name)"; exit 4 ;;
        esac
    done
    EXP_RUNV1[$name]="${EXP_RUNV1[$name]:-1}"
    EXP_RUNV2[$name]="${EXP_RUNV2[$name]:-1}"
    EXP_V1OUT[$name]="${EXP_V1OUT[$name]:-$([ "${EXP_V1S[$name]}" = RAN ] && echo NONZERO || echo ABSENT)}"
    EXP_V2OUT[$name]="${EXP_V2OUT[$name]:-$([ "${EXP_V2S[$name]}" = RAN ] && echo NONZERO || echo ABSENT)}"
    ALL_CASES+=("$name")
}
mk() {                             # mk <case> <v1|v2> <ERE>
    local name="$1" v="$2" pat="$3"
    if [ "$v" = v1 ]; then
        if [ -z "${MK1[$name]:-}" ]; then MK1[$name]="$pat"
        else MK1[$name]="${MK1[$name]}"$'\n'"$pat"; fi
    else
        if [ -z "${MK2[$name]:-}" ]; then MK2[$name]="$pat"
        else MK2[$name]="${MK2[$name]}"$'\n'"$pat"; fi
    fi
}

# ===== R01: 外层必须传播失败, 且要指名是哪个变体失败 =====
reg N1_only_V1_injected_fail  v1_state=RAN v1_build=0 v1_run=1 v2_state=RAN v2_build=0 v2_run=0 outer=1
mk N1_only_V1_injected_fail v1 '\[FAIL\] 注入: 本变体故意判失败\(HOST_INJECT_VARIANT_FAIL=1\)'
mk N1_only_V1_injected_fail v1 '结论: NOT PASSED -> 判据失败 1 项'
reg N2_only_V2_injected_fail  v1_state=RAN v1_build=0 v1_run=0 v2_state=RAN v2_build=0 v2_run=1 outer=1
mk N2_only_V2_injected_fail v2 '\[FAIL\] 注入: 本变体故意判失败\(HOST_INJECT_VARIANT_FAIL=1\)'
mk N2_only_V2_injected_fail v2 '结论: NOT PASSED -> 判据失败 1 项'
reg N3_V1_stale_exe_not_reused v1_state=COMPILE_FAILED v1_build=1 v1_run=127 \
                               v2_state=RAN v2_build=0 v2_run=0 outer=1 v1_out=SENTINEL

# ===== R02: 固件自测终态必须进总判定(并指名终态身份) =====
reg N4_fw_calc_selftest_FAILED v1_state=RAN v1_build=0 v1_run=2 v2_state=RAN v2_build=0 v2_run=2 outer=2
mk N4_fw_calc_selftest_FAILED v1 '\[BADRESULT\] 自测终态 A1 tmc_calc_selftest = FAILED'
mk N4_fw_calc_selftest_FAILED v2 '\[BADRESULT\] 自测终态 A1 tmc_calc_selftest = FAILED'
reg N5_fw_cfg_selftest_INCOMPLETE v1_state=RAN v1_build=0 v1_run=2 \
                                  v2_state=RAN v2_build=0 v2_run=0 outer=2
mk N5_fw_cfg_selftest_INCOMPLETE v1 '\[BADRESULT\] 自测终态 A2 tmc_cfg_selftest = INCOMPLETE/SKIP'
mk N5_fw_cfg_selftest_INCOMPLETE v1 '\[MISSING\] 必需用例未执行: T14 跨线程占用'

# ===== R03: 关键并发用例没执行就必须非零(指名缺席用例身份) =====
reg N6_helper_thread_create_fail v1_state=RAN v1_build=0 v1_run=3 v2_state=RAN v2_build=0 v2_run=3 outer=3
mk N6_helper_thread_create_fail v1 '\[MISSING\] T14 走了 SKIP 分支'
mk N6_helper_thread_create_fail v1 '\[MISSING\] 必需用例未执行: FD4 probe 遇 txn 占用'
mk N6_helper_thread_create_fail v2 '\[MISSING\] T14 走了 SKIP 分支'
mk N6_helper_thread_create_fail v2 '\[MISSING\] 必需用例未执行: C7 motor_arm 遇 txn 占用'
reg N7_helper_thread_startup_fail v1_state=RAN v1_build=0 v1_run=3 v2_state=RAN v2_build=0 v2_run=3 outer=3
mk N7_helper_thread_startup_fail v1 '\[MISSING\] 必需用例未执行: FD4 probe 遇 txn 占用'
mk N7_helper_thread_startup_fail v2 '\[MISSING\] 必需用例未执行: C7 motor_arm 遇 txn 占用'

# ===== 保留的历史负例(各自指名命中的判据) =====
reg N8_gstat_expect_table_tamper v1_state=RAN v1_build=0 v1_run=1 v2_state=RAN v2_build=0 v2_run=0 outer=1
mk N8_gstat_expect_table_tamper v1 '\[FAIL\] FD6 GSTAT 解码'
reg N9_probe_writeback_V1  run_v2=0 v1_state=RAN v1_build=0 v1_run=1 v2_state=NOT_RUN v2_build=NOT_RUN v2_run=NOT_RUN outer=1
mk N9_probe_writeback_V1 v1 '\[FAIL\] FD2 probe\(死链路\)'
mk N9_probe_writeback_V1 v1 '\[FAIL\] FD3 probe\(活链路\)'
reg N10_probe_writeback_V2 run_v1=0 v1_state=NOT_RUN v1_build=NOT_RUN v1_run=NOT_RUN \
                           v2_state=RAN v2_build=0 v2_run=1 outer=1
mk N10_probe_writeback_V2 v2 '\[FAIL\] FD7b 已提交快照后再 probe: 写帧增量 1\(应 0\)'
reg N10b_writeback_both_variants v1_state=RAN v1_build=0 v1_run=1 \
                                 v2_state=RAN v2_build=0 v2_run=1 outer=1
mk N10b_writeback_both_variants v1 '\[FAIL\] FD3 probe\(活链路\)'
mk N10b_writeback_both_variants v2 '\[FAIL\] FD7b 已提交快照后再 probe: 写帧增量 1\(应 0\)'

# ===== 干净回归(两变体都必须真跑且全绿) =====
reg N11_clean_snapshot_regression v1_state=RAN v1_build=0 v1_run=0 v2_state=RAN v2_build=0 v2_run=0 outer=0
mk N11_clean_snapshot_regression v1 'CHECK_PASS=25 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0'
mk N11_clean_snapshot_regression v2 'CHECK_PASS=22 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0'
reg N12_workspace_clean_regression v1_state=RAN v1_build=0 v1_run=0 v2_state=RAN v2_build=0 v2_run=0 outer=0
mk N12_workspace_clean_regression v1 'CHECK_PASS=25 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0'
mk N12_workspace_clean_regression v2 'CHECK_PASS=22 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0'

# ------------------------------------------------------------------ 选择入口(R-2/D)
declare -a REQUESTED
resolve_selection() {
    local tok hit n
    if [ -z "$ONLY" ]; then
        REQUESTED=("${ALL_CASES[@]}")
    else
        REQUESTED=()
        for tok in $ONLY; do
            hit=()
            for n in "${ALL_CASES[@]}"; do [ "$n" = "$tok" ] && hit=("$n"); done
            if [ ${#hit[@]} -eq 0 ]; then
                for n in "${ALL_CASES[@]}"; do
                    case "$n" in "$tok"*) hit+=("$n");; esac
                done
            fi
            if [ ${#hit[@]} -eq 0 ]; then
                echo "FATAL: ONLY 里的选择 \"$tok\" 不匹配任何用例(已登记: ${ALL_CASES[*]})" >&2; exit 4
            fi
            if [ ${#hit[@]} -gt 1 ]; then
                echo "FATAL: ONLY 里的选择 \"$tok\" 歧义, 命中 ${#hit[@]} 个: ${hit[*]}" >&2; exit 4
            fi
            REQUESTED+=("${hit[0]}")
        done
    fi
    if [ ${#REQUESTED[@]} -eq 0 ]; then
        echo "FATAL: 解析后的执行集合为空(拒绝把 0 例当成通过)" >&2; exit 4
    fi
}
selected() {                       # selected <case>
    local n
    for n in "${REQUESTED[@]}"; do [ "$n" = "$1" ] && return 0; done
    return 1
}

die_infra() { printf 'FATAL(基础设施): %s\n' "$*" >&2; exit 4; }

snapshot_apps() {                  # $1 = 目标目录(仓库外), 内含 applications/
    local dst="$1"
    rm -rf "$dst" || return 1; mkdir -p "$dst"
    ( cd "$REPO" && git archive "$BASE_HEAD" "$APPREL" ) | tar -x -C "$dst" || return 1
    [ -f "$dst/$APPREL/tmc2209.c" ] || return 1
    return 0
}

tamper() {                         # $1=文件 $2=模式 -> 统一走 inject.py(锚点必须恰中 1 次)
    local file="$1" mode="$2" rc
    "$PY" "$HERE/inject.py" "$file" "$mode"
    rc=$?
    [ "$rc" -eq 0 ] || die_infra "注入步骤失败(mode=$mode, rc=$rc): 宁可终止, 不许带病继续"
    grep -aq 'NEGATIVE-INJECTION' "$file" || die_infra "注入未落地(找不到 NEGATIVE-INJECTION 标记): $file"
    return 0
}

status_field() {                   # $1=status 文件 $2=字段名 -> 出现次数必须恰为 1
    local cnt val
    [ -f "$1" ] || { echo "@@NOFILE@@"; return 1; }
    cnt=$(grep -a -c -- "^$2=" "$1")
    if [ "$cnt" != "1" ]; then echo "@@BAD($cnt)@@"; return 1; fi
    val=$(sed -n "s/^$2=//p" "$1")
    printf '%s\n' "$val"
}

mark_hits() {                      # $1=文件 $2=ERE -> 命中次数
    [ -f "$1" ] || { echo 0; return; }
    grep -a -E -c -- "$2" "$1" | tail -1
}

# ------------------------------------------------------------------ 用例执行+判定
run_case() {                       # run_case <name> [env 赋值...]
    local name="$1"; shift
    local out="$WORK/$name" log="$WORK/$name.log" status
    local run_v1 run_v2 bad_identity=0 infra=0 missid="" detail="" cls
    local stale_sentinel="STALE-EXE-WOULD-HAVE-RUN"

    selected "$name" || { echo "SKIP(不在请求集合) $name"; return 0; }

    rm -rf "$out"; mkdir -p "$out" || die_infra "无法创建输出目录 $out"
    run_v1="${EXP_RUNV1[$name]}"; run_v2="${EXP_RUNV2[$name]}"

    if [ "$name" = "N3_V1_stale_exe_not_reused" ]; then
        # 先决步骤: N3 需要一个"先前成功构建"的 host_v1.exe 当陈旧产物样本。
        # 这里显式用一次 V1-only 正常构建来准备它(仓库外工作区), 准备失败就终止。
        if [ ! -f "$WORK/_pristine_v1/host_v1.exe" ]; then
            mkdir -p "$WORK/_pristine_v1"
            ( cd "$HERE" && env SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" RUN_V2=0 \
                OUT_DIR="$WORK/_pristine_v1" bash "$RUNNER_SCRIPT" ) \
                > "$WORK/_pristine_v1.log" 2>&1 || true
        fi
        [ -f "$WORK/_pristine_v1/host_v1.exe" ] || die_infra "N3 无法准备陈旧 exe 样本"
        cp "$WORK/_pristine_v1/host_v1.exe" "$out/host_v1.exe" || die_infra "N3 预置旧 exe 失败"
        printf '%s\n' "$stale_sentinel" > "$out/v1_out.txt"
        printf 'STALE-PRESEEDED\n'      > "$out/v1_executed.marker"
    fi

    local -a RUNENV=()
    RUNENV+=("SHIM_DIR=$HERE/shim")
    [ -z "$GCC_OVERRIDE" ] || RUNENV+=("GCC=$GCC_OVERRIDE")
    [ -z "$HOST_SRC_OVERRIDE" ] || RUNENV+=("HOST_SRC=$HOST_SRC_OVERRIDE")

    ( cd "$HERE" && env "$@" OUT_DIR="$out" RUN_V1="$run_v1" RUN_V2="$run_v2" \
        "${RUNENV[@]}" bash "$RUNNER_SCRIPT" ) > "$log" 2>&1
    local proc_rc=$?                       # runner 进程的**真实**退出码
    status="$out/runner_status.txt"
    RAN_CASE[$name]=1

    if [ ! -f "$status" ]; then
        TABLE+=("INFRA-FAIL | $name | 缺 runner_status.txt"); BAD+=("$name:STATUS")
        echo "--- $name: INFRA-FAIL(缺状态回执)"; overall=4; return 0
    fi
    local status_bad=0 f got
    for f in $FIELDS; do
        got=$(status_field "$status" "$f") || {
            TABLE+=("INFRA-FAIL | $name | 状态字段 $f 缺失或重复($got)")
            BAD+=("$name:$f"); status_bad=1; }
    done
    if [ "$status_bad" -ne 0 ]; then
        echo "--- $name: INFRA-FAIL(状态回执坏)"; overall=4; return 0
    fi

    local a_v1s a_v1b a_v1r a_v2s a_v2b a_v2r a_rep a_v1x a_v2x a_v1e a_v2e a_v1o a_v2o
    a_v1s=$(status_field "$status" V1_STATE);  a_v1b=$(status_field "$status" V1_BUILD_RC)
    a_v1r=$(status_field "$status" V1_RUN_RC); a_v2s=$(status_field "$status" V2_STATE)
    a_v2b=$(status_field "$status" V2_BUILD_RC); a_v2r=$(status_field "$status" V2_RUN_RC)
    a_rep=$(status_field "$status" RUNNER_RC)
    a_v1x=$(status_field "$status" V1_EXECUTED); a_v2x=$(status_field "$status" V2_EXECUTED)
    a_v1e=$(status_field "$status" V1_EXE); a_v2e=$(status_field "$status" V2_EXE)
    a_v1o=$(status_field "$status" V1_OUT_BYTES); a_v2o=$(status_field "$status" V2_OUT_BYTES)

    cmp_one() {                           # cmp_one <标签> <期望> <实际>
        local label="$1" exp="$2" act="$3" sig=0
        [ "$exp" = "$act" ] && return 0
        detail="$detail  $label:期望=$exp 实际=$act"
        printf '%s' "$act" | grep -Eq '^[0-9]+$' && [ "$act" -ge 128 ] && sig=1
        case "$label" in
            runner.reported_rc)          # 自报值 != 真实进程码 => 回执失真(基础设施级)
                infra=1 ;;
            runner.proc_rc)              # 真实码 != 预期 => 失败被吞掉就是漏检
                if [ "$act" = 0 ] && [ "$exp" != 0 ]; then bad_identity=1; else infra=1; fi ;;
            *.executed)
                if [ "$exp" = 1 ] && [ "$act" != 1 ]; then infra=1
                elif [ "$exp" = 0 ] && [ "$act" = 1 ]; then infra=1
                else bad_identity=1; fi ;;
            *.out_bytes)
                if [ "$exp" = ABSENT ] && [ "$act" != ABSENT ]; then infra=1
                elif [ "$exp" != ABSENT ] && { [ "$act" = ABSENT ] || [ "$act" = 0 ]; }; then infra=1
                else bad_identity=1; fi ;;
            *.state|*.build|*.run|N3.stale_exe_deleted)
                if [ "$sig" = 1 ]; then infra=1; return 0; fi
                case "$act" in
                    127|COMPILE_FAILED|NOT_RUN|ABSENT)
                        case "$exp" in
                            NOT_RUN|COMPILE_FAILED|ABSENT) bad_identity=1 ;;
                            *) infra=1 ;;
                        esac ;;
                    *) bad_identity=1 ;;
                esac ;;
            *) bad_identity=1 ;;
        esac
    }

    # ---- R2-NEW-01: 三个 rc 分别核对(真实进程码优先, 自报值必须与之一致) ----
    cmp_one "runner.proc_rc"     "${EXP_OUTER[$name]}" "$proc_rc"
    cmp_one "runner.reported_rc" "$proc_rc"            "$a_rep"
    cmp_one "V1.state" "${EXP_V1S[$name]}" "$a_v1s"
    cmp_one "V1.build" "${EXP_V1B[$name]}" "$a_v1b"
    cmp_one "V1.run"   "${EXP_V1R[$name]}" "$a_v1r"
    cmp_one "V2.state" "${EXP_V2S[$name]}" "$a_v2s"
    cmp_one "V2.build" "${EXP_V2B[$name]}" "$a_v2b"
    cmp_one "V2.run"   "${EXP_V2R[$name]}" "$a_v2r"

    check_out() {                          # check_out <V1|V2>
        local V="$1" vl exp x b
        vl="$(printf '%s' "$V" | tr 'A-Z' 'a-z')"
        if [ "$V" = V1 ]; then exp="${EXP_V1OUT[$name]}"; x="$a_v1x"; b="$a_v1o"
        else exp="${EXP_V2OUT[$name]}"; x="$a_v2x"; b="$a_v2o"; fi
        case "$exp" in
            NONZERO)
                cmp_one "$V.executed" 1 "$x"
                case "$b" in ''|ABSENT|*[!0-9]*) cmp_one "$V.out_bytes" ">0" "ABSENT" ;;
                              0)                cmp_one "$V.out_bytes" ">0" "0" ;; esac ;;
            ABSENT)
                cmp_one "$V.executed" 0 "$x"; cmp_one "$V.out_bytes" ABSENT "$b" ;;
            SENTINEL)
                cmp_one "$V.executed" 0 "$x"
                if ! grep -aq "$stale_sentinel" "$out/${vl}_out.txt" 2>/dev/null; then
                    infra=1; detail="$detail  $V:哨兵输出缺失/被覆盖(旧 exe 可能真被运行)"
                fi
                if [ "$(mark_hits "$out/${vl}_out.txt" 'HOST TEST SUMMARY')" != "0" ]; then
                    infra=1; detail="$detail  $V:陈旧 exe 被运行过(输出里出现测试汇总)"
                fi ;;
        esac
    }
    check_out V1
    check_out V2

    # ---- 指定失败身份必须出现在**对应变体**的输出里 ----
    local m o n2
    if [ -n "${MK1[$name]:-}" ]; then
        o="$out/v1_out.txt"
        while IFS= read -r m; do [ -z "$m" ] && continue
            n2=$(mark_hits "$o" "$m")
            if [ "${n2:-0}" -lt 1 ]; then bad_identity=1; missid="$missid V1[$m]"; fi
        done <<EOF2
${MK1[$name]}
EOF2
    fi
    if [ -n "${MK2[$name]:-}" ]; then
        o="$out/v2_out.txt"
        while IFS= read -r m; do [ -z "$m" ] && continue
            n2=$(mark_hits "$o" "$m")
            if [ "${n2:-0}" -lt 1 ]; then bad_identity=1; missid="$missid V2[$m]"; fi
        done <<EOF2
${MK2[$name]}
EOF2
    fi

    # 期望"通过"的变体必须干净: 无任何 [FAIL]/[BADRESULT]/[MISSING]
    if [ "${EXP_V1R[$name]}" = 0 ]; then
        n2=$(mark_hits "$out/v1_out.txt" '\[(FAIL|BADRESULT|MISSING)\]')
        [ "${n2:-0}" -eq 0 ] || { bad_identity=1; missid="$missid V1有$n2条坏行"; }
    fi
    if [ "${EXP_V2R[$name]}" = 0 ]; then
        n2=$(mark_hits "$out/v2_out.txt" '\[(FAIL|BADRESULT|MISSING)\]')
        [ "${n2:-0}" -eq 0 ] || { bad_identity=1; missid="$missid V2有$n2条坏行"; }
    fi

    # N3: 陈旧产物必须被删除(输出侧已按 SENTINEL 校验)
    if [ "$name" = "N3_V1_stale_exe_not_reused" ]; then
        cmp_one "N3.stale_exe_deleted" ABSENT "$a_v1e"
    fi

    if [ "$infra" -ne 0 ]; then
        cls=INFRA-FAIL; [ "$overall" -lt 4 ] && overall=4
    elif [ "$bad_identity" -ne 0 ]; then
        cls=NOT-DETECTED; [ "$overall" -lt 1 ] && overall=1
    else
        cls=OK
    fi
    [ -n "$missid" ] && detail="$detail 缺席身份:$missid"
    [ "$cls" = OK ] || BAD+=("$name $cls")

    printf '%s | %s | rc 真实=%s 自报=%s 期望=%s | V1=%s/%s/%s V2=%s/%s/%s (期望 V1=%s/%s/%s V2=%s/%s/%s)%s\n' \
        "$cls" "$name" "$proc_rc" "$a_rep" "${EXP_OUTER[$name]}" \
        "$a_v1s" "$a_v1b" "$a_v1r" "$a_v2s" "$a_v2b" "$a_v2r" \
        "${EXP_V1S[$name]}" "${EXP_V1B[$name]}" "${EXP_V1R[$name]}" \
        "${EXP_V2S[$name]}" "${EXP_V2B[$name]}" "${EXP_V2R[$name]}" "$detail" \
        | tee -a "$WORK/verdict_matrix.txt"
    # 机器回执: 真实退出码 / 自报退出码 / 预期退出码 三个字段分开存(不都叫 outer)
    printf '%s\tclass=%s\trunner_proc_rc=%s\trunner_reported_rc=%s\texpected_rc=%s\t%s\n' \
        "$name" "$cls" "$proc_rc" "$a_rep" "${EXP_OUTER[$name]}" \
        "V1=$a_v1s/$a_v1b/$a_v1r V2=$a_v2s/$a_v2b/$a_v2r" >> "$WORK/case_rc_ledger.txt"
    TABLE+=("$(printf '%s | %s%s' "$cls" "$name" "${detail:+ | $detail}")")
    echo "--- $name: $cls$detail"
}

# ------------------------------------------------------------------ 主流程
resolve_selection
rm -rf "$WORK"; mkdir -p "$WORK" || die_infra "无法准备工作目录 $WORK(必须在仓库外)"
case "$WORK" in "$REPO"*) die_infra "工作目录不能落在仓库内: $WORK" ;; esac
: > "$WORK/verdict_matrix.txt"
: > "$WORK/case_rc_ledger.txt"
[ -f "$RUNNER_SCRIPT" ] || die_infra "runner 脚本不存在: $RUNNER_SCRIPT"

PRISTINE="$WORK/pristine"
snapshot_apps "$PRISTINE" || die_infra "基线快照导出失败 ($BASE_HEAD)"
SNAP_APPS="$PRISTINE/$APPREL"
HOST_MAIN="$HERE/host_main.c"
[ -z "$HOST_SRC_OVERRIDE" ] || HOST_MAIN="$HOST_SRC_OVERRIDE"
[ -f "$HOST_MAIN" ] || die_infra "夹具源文件不存在: $HOST_MAIN"

echo "############ 负例与回归全链 (基线 $BASE_HEAD, 逐例预期矩阵 + 出口对账) ############"
echo "请求集合(${#REQUESTED[@]}): ${REQUESTED[*]}"

run_case N1_only_V1_injected_fail \
    SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" V1_EXTRA="-DHOST_INJECT_VARIANT_FAIL=1"
run_case N2_only_V2_injected_fail \
    SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" V2_EXTRA="-DHOST_INJECT_VARIANT_FAIL=1"
run_case N3_V1_stale_exe_not_reused \
    SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" V1_EXTRA="-DHOST_INJECT_COMPILE_ERROR"

SNAP_CALC="$WORK/snap_calc"
if selected N4_fw_calc_selftest_FAILED; then
    snapshot_apps "$SNAP_CALC" || die_infra "snap_calc 导出失败"
    tamper "$SNAP_CALC/$APPREL/tmc2209.c" calc_anchor
fi
run_case N4_fw_calc_selftest_FAILED SRC_APP="$SNAP_CALC/$APPREL" HOST_SRC="$HOST_MAIN"

run_case N5_fw_cfg_selftest_INCOMPLETE \
    SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" V1_EXTRA="-DHOST_INJECT_TEST_CTX_LOCK_FAIL"

run_case N6_helper_thread_create_fail \
    SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" \
    V1_EXTRA="-DHOST_INJECT_NO_HELPER_THREAD" V2_EXTRA="-DHOST_INJECT_NO_HELPER_THREAD"
run_case N7_helper_thread_startup_fail \
    SRC_APP="$SNAP_APPS" HOST_SRC="$HOST_MAIN" \
    V1_EXTRA="-DHOST_INJECT_NO_THREAD_STARTUP" V2_EXTRA="-DHOST_INJECT_NO_THREAD_STARTUP"

CP_HARNESS="$WORK/harness_gstat"
if selected N8_gstat_expect_table_tamper; then
    mkdir -p "$CP_HARNESS"; cp "$HERE/host_main.c" "$CP_HARNESS/host_main.c"
    tamper "$CP_HARNESS/host_main.c" gstat_expect
fi
run_case N8_gstat_expect_table_tamper \
    SRC_APP="$SNAP_APPS" HOST_SRC="$CP_HARNESS/host_main.c"

SNAP_WB="$WORK/snap_writeback"
if selected N9_probe_writeback_V1 || selected N10_probe_writeback_V2 \
   || selected N10b_writeback_both_variants; then
    snapshot_apps "$SNAP_WB" || die_infra "snap_writeback 导出失败"
    tamper "$SNAP_WB/$APPREL/tmc2209.c" probe_writeback
fi
run_case N9_probe_writeback_V1  SRC_APP="$SNAP_WB/$APPREL" HOST_SRC="$HOST_MAIN"
run_case N10_probe_writeback_V2 SRC_APP="$SNAP_WB/$APPREL" HOST_SRC="$HOST_MAIN"
run_case N10b_writeback_both_variants SRC_APP="$SNAP_WB/$APPREL" HOST_SRC="$HOST_MAIN"

SNAP_CLEAN="$WORK/snap_clean"
if selected N11_clean_snapshot_regression; then
    snapshot_apps "$SNAP_CLEAN" || die_infra "snap_clean 导出失败"
fi
run_case N11_clean_snapshot_regression SRC_APP="$SNAP_CLEAN/$APPREL" HOST_SRC="$HOST_MAIN"
run_case N12_workspace_clean_regression SRC_APP="$APPREL_ABS" HOST_SRC="$HOST_MAIN"

# ---- 选择集合对账(R-2/D): 请求集合必须与实际执行集合一致且非空 ----
NOT_RAN=()
for f in "${REQUESTED[@]}"; do [ -z "${RAN_CASE[$f]:-}" ] && NOT_RAN+=("$f"); done
if [ ${#NOT_RAN[@]} -ne 0 ]; then
    echo "FATAL(选择集合): 请求但未执行的用例: ${NOT_RAN[*]}" >&2
    TABLE+=("INFRA-FAIL | 选择集合 | 请求但未执行: ${NOT_RAN[*]}")
    overall=4
fi

echo ""
echo "############ 判定汇总 ############"
printf '%s\n' "${TABLE[@]}"
echo ""
echo "执行集合(${#RAN_CASE[@]}) 与请求集合(${#REQUESTED[@]}) 一致: $([ ${#NOT_RAN[@]} -eq 0 ] && echo 是 || echo 否)"
echo "类别计数: OK=$(printf '%s\n' "${TABLE[@]}" | grep -a -c '^OK') NOT-DETECTED=$(printf '%s\n' "${TABLE[@]}" | grep -a -c '^NOT-DETECTED') INFRA-FAIL=$(printf '%s\n' "${TABLE[@]}" | grep -a -c '^INFRA-FAIL')"
if [ "$overall" -eq 0 ]; then
    echo "RUNNER(run_negatives): 所有负例按预期被检出且身份齐备; 干净回归通过 (rc=0)"
elif [ "$overall" -eq 4 ]; then
    echo "RUNNER(run_negatives): 基础设施故障/回执不一致 —— 有负例没真正执行或自报与真实 rc 不符 (rc=4)"
else
    echo "RUNNER(run_negatives): 有负例漏检或身份缺失 (rc=1)"
fi

if [ -z "$ONLY" ] && [ "$NEG_META_SKIP" != "1" ] && [ -f "$HERE/run_meta_negatives.sh" ]; then
    echo ""
    bash "$HERE/run_meta_negatives.sh"
    meta_rc=$?
    echo "RUNNER(meta) rc=$meta_rc"
    if [ "$meta_rc" -ne 0 ] && [ "$overall" -eq 0 ]; then overall=$meta_rc; fi
fi

exit "$overall"
