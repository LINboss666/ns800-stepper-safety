#!/usr/bin/env bash
# run_sp_measure_a0.sh - 目标机测量 A0 记录器链（任务 phase8-target-measurement-a0）
#
# 被测实现（真编译，不做副本）:
#   APP/stop_priority.c   —— 三个入口的 measured-enter/exit 形状、动作次数与返回优先序
#   APP/sp_measure_a0.c   —— 预约/钉快照/有效性判定/发布/控制状态机/命令族
# 夹具: tests/host/sp_measure_a0_main.c + sp_measure_a0_port.h(+ tests/host/shim_ps 兼容层)
#
# 两个可执行体（同一份夹具源码, 只差编译期开关）:
#   a0_off : NS800_TARGET_MEASUREMENT_A0 未定义(默认 0) => 只跑 TMA01, 并核"测量符号不存在"
#   a0_on  : =1 + NS800_SPM_HOST_BACKEND              => 跑 TMA02..TMA24
#
# r1（复审 phase8-target-measurement-a0-r1）新增:
#   TMA19 真实命令名查表分发(名字 spm -> spm_cmd, 函数名不是命令)
#   TMA20 严格参数矩阵(缺参/多参/未知/非法 entry 先于任何动作; 合法上下文可打印, 非法上下文静默)
#   TMA21 逐样本门控失效(init 成功后关 CYCCNTENA / 关 TRCENA / 计数停转 => 该样本无效, 关闭次数不变)
#   TMA22 能力检查生命周期(非静止态 init 被拒且零 PPB 访问)
#   TMA23 历史 COUNTFLAG 正向对照 + 真重装/回绕/配置/嵌套负向仍拒绝
#   TMA24 修订后的分批顺序(1 条与 16 条两批逐批一致, clear 只清测量 RAM)
#   REVERSE=1 时正向与反向**共用** ledger_check() 逐组判身份/数量/成对/SUMMARY/EXIT/退出码,
#   并额外跑一条"判定器自身"的负控: 在反向构建的夹具副本里省略非锚身份 TMA18 的 RESULT 行,
#   必须被判定成 INFRA-FAIL(复审 A0-04)。
#
# 生产打印经 -Wl,--wrap=printf 计成"窗口内禁止项"台账(夹具自身输出走 fprintf, 不经该通道)。
#
# 用法:
#   A0_OUT_DIR=<仓外目录> bash tests/host/run_sp_measure_a0.sh
#   ONLY=counter,batch bash tests/host/run_sp_measure_a0.sh     # 选择先验证再执行
#   REVERSE=1 bash tests/host/run_sp_measure_a0.sh              # 两个具名反向家族 + 台账负控
#   NM=<nm 路径> GCC=<gcc 路径> A0_GROUP_TIMEOUT=90 ...
# 退出码: 0=通过; 1=检出失败(有用例判 FAIL); 4=基础设施故障; 127=环境缺失
set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_ps}"
A0_MAIN="${A0_MAIN:-$HERE/sp_measure_a0_main.c}"
A0_PORT="${A0_PORT:-$HERE/sp_measure_a0_port.h}"
HAL_DRV="${HAL_DRV:-$REPO_ROOT/rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/HAL_Drivers/drivers}"
GROUP_TIMEOUT="${A0_GROUP_TIMEOUT:-90}"
OUT_DIR="${A0_OUT_DIR:-$(env_work_root)/a0}"

note() { printf '%s\n' "$*"; }
remember() { [ "$1" -ge "$WORST" ] && WORST="$1"; }
WORST=0

# ------------------------------------------------------------------ 组与逐组必需用例
# 组名 -> 该组**必须真执行且判 PASS** 的用例 id（与夹具 CASE/RESULT 行逐字一致）。
A0_GROUPS="off counter contract conc batch cli ledger pack"
G_off_ids="TMA01"
G_counter_ids="TMA03 TMA02 TMA04 TMA05 TMA06 TMA21 TMA22 TMA23"
G_contract_ids="TMA07 TMA08 TMA09 TMA10"
G_conc_ids="TMA11 TMA12 TMA13"
G_batch_ids="TMA14 TMA15 TMA16"
G_cli_ids="TMA17 TMA19 TMA20"
G_ledger_ids="TMA18"
G_pack_ids="TMA24"
# 每个可执行体跑哪些组
OFF_GROUPS="off"
ON_GROUPS="counter contract conc batch cli ledger pack"
ALL_IDS="$G_off_ids $G_counter_ids $G_contract_ids $G_conc_ids $G_batch_ids $G_cli_ids $G_ledger_ids $G_pack_ids"

# 反向家族在每个组里**期望**判 FAIL 的身份（其余组必须全 PASS）。
rev_expect() {                          # $1=家族名 $2=组名
    # 注意: case 的模式里 `|` 是"或"，所以整个 "家族|组" 键必须加引号才是字面量。
    case "$1|$2" in
      "rev_spm_unavailable_is_valid|counter") echo "TMA02 TMA03 TMA21" ;;
      "rev_spm_snapshot_after_unmask|conc")   echo "TMA11" ;;
      *)                                      echo "" ;;
    esac
}

# ---------------------------------------------------------------------- 前置检查
[ -f "$SRC_APP/stop_priority.c" ] || { note "FATAL: 生产源码不存在: $SRC_APP"; exit 127; }
[ -f "$SRC_APP/sp_measure_a0.c" ]  || { note "FATAL: 记录器实现不存在: $SRC_APP/sp_measure_a0.c"; exit 127; }
[ -f "$SRC_APP/sp_measure_a0.h" ]  || { note "FATAL: 记录器接口不存在"; exit 127; }
[ -f "$A0_MAIN" ] || { note "FATAL: 夹具不存在: $A0_MAIN"; exit 127; }
[ -f "$A0_PORT" ] || { note "FATAL: 端口头不存在: $A0_PORT"; exit 127; }
[ -d "$SHIM_DIR" ] || { note "FATAL: 兼容层目录不存在: $SHIM_DIR"; exit 127; }

if ! OUT_DIR="$(require_outside_repo "$OUT_DIR" "A0_OUT_DIR")"; then
    note "FATAL: 输出目录未通过仓外守卫, 未创建任何目录、未删除任何文件"
    exit 4
fi
if [ -e "$OUT_DIR" ] && [ ! -f "$OUT_DIR/.ns800_host_work" ]; then
    note "FATAL: 输出目录已存在但没有本工具的哨兵, 拒绝清理: $OUT_DIR"
    exit 4
fi
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$OUT_DIR" || { note "FATAL: 无法创建 OUT_DIR"; exit 4; }
printf 'ns800-host-chain sp-measure-a0 runner work dir (run_id=%s)\n' "$RUN_ID" \
    > "$OUT_DIR/.ns800_host_work" || { note "FATAL: 哨兵写不进去"; exit 4; }

if ! GCC="$(env_detect_gcc)"; then
    note "FATAL: 找不到 gcc（可用 GCC=<路径> 指定）"
    exit 127
fi
NM="${NM:-}"
if [ -z "$NM" ]; then
    for c in nm "/c/Program Files (x86)/Dev-Cpp/MinGW64/bin/nm.exe"; do
        if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then NM="$c"; break; fi
    done
fi

# ---------------------------------------------------------------- ONLY 选择(先验证再执行)
SELECTED="$A0_GROUPS"
if [ -n "${ONLY:-}" ]; then
    bad=""
    for want in ${ONLY//,/ }; do
        hit=0
        for gname in $A0_GROUPS; do [ "$gname" = "$want" ] && hit=1; done
        [ "$hit" -eq 1 ] || bad="$bad $want"
    done
    if [ -n "$bad" ]; then
        note "FATAL: ONLY 含未登记的组(未执行任何编译):$bad"
        note "       可选组: $A0_GROUPS"
        exit 4
    fi
    SELECTED="${ONLY//,/ }"
fi

FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function -mthreads"
INCS=(-I "$HERE" -I "$SHIM_DIR" -I "$SRC_APP" -I "$HAL_DRV")
COMPAT="$OUT_DIR/compat"
mkdir -p "$COMPAT" || { note "FATAL: 无法创建兼容头目录"; exit 4; }
printf '#ifndef NS800_HOST_RTDEF_COMPAT_H\n#define NS800_HOST_RTDEF_COMPAT_H\n#include <rtthread.h>\n#endif\n' \
    > "$COMPAT/rtdef.h" || { note "FATAL: 兼容头写不进去"; exit 4; }
printf '#ifndef NS800_HOST_RTHW_COMPAT_H\n#define NS800_HOST_RTHW_COMPAT_H\n#include <rtthread.h>\nrt_base_t rt_hw_interrupt_disable(void);\nvoid rt_hw_interrupt_enable(rt_base_t level);\n#endif\n' \
    > "$COMPAT/rthw.h" || { note "FATAL: rthw 兼容头写不进去"; exit 4; }
INCS+=(-I "$COMPAT")

EXE_OFF="$OUT_DIR/a0_off.exe"
EXE_ON="$OUT_DIR/a0_on.exe"
[ -n "${EXE_EXT:-}" ] && { EXE_OFF="$EXE_OFF"; EXE_ON="$EXE_ON"; }

note "###### 目标机测量 A0 记录器链 (基线 $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)) ######"
note "run_id=$RUN_ID"
note "SRC_APP=$SRC_APP"
note "OUT_DIR=$OUT_DIR"
note "GCC=$GCC"
note "NM=${NM:-NM_MISSING}"
note "选择组=$SELECTED"

build_variant() {                    # $1=off|on  $2..=额外 -D
    local name="$1"; shift
    local log="$OUT_DIR/build_$name.txt"
    local extra=()
    [ "$name" = "on" ] && extra=(-DNS800_TARGET_MEASUREMENT_A0=1 -DNS800_SPM_HOST_BACKEND)
    [ "$name" = "off" ] && extra=(-DNS800_SPM_HOST_BACKEND)
    while [ $# -gt 0 ]; do extra+=("$1"); shift; done
    local exe="$OUT_DIR/a0_$name.exe"
    "$GCC" $FLAGS "${extra[@]}" "${INCS[@]}" -Wl,--wrap=printf \
        "$A0_MAIN" "$SRC_APP/sp_measure_a0.c" "$SRC_APP/stop_priority.c" \
        -o "$exe" > "$log" 2>&1
    local rc=$?
    local bytes
    bytes="$(wc -c < "$log" 2>/dev/null || echo NA)"
    note "BUILD $name rc=$rc build_log_bytes=$bytes exe=$exe"
    if [ "$rc" -ne 0 ] || [ "$bytes" != "0" ]; then
        note "  构建输出(前 40 行):"
        head -40 "$log" | sed 's/^/    /'
    fi
    return $rc
}

# ------------------------------------------------- 逐组台账判定（正向与反向**共用**同一套）
# 参数: $1=日志 $2=该组声明身份 $3=该进程退出码
# 只判**台账合同**(身份/数量/成对/SUMMARY/EXIT/退出码)，不判"谁该 FAIL"：
#   返回 0 = 台账一致且无 FAIL; 1 = 台账一致但有 FAIL(LV_FAILIDS 给出身份); 4 = INFRA-FAIL(LV_MSG 给原因)
# 复审 A0-04 的合同: 缺席 / 重复 / 未登记身份 / CASE 与 RESULT 不成对 / SUMMARY 不自洽 /
# EXIT 与 fails 不符 / 退出码与失败情况不符 —— 全部算 INFRA-FAIL，不得当成"其它保持 PASS"。
ledger_check() {
    local log="$1" expect="$2" prc="$3"
    local id n e cnt miss=() dup=() nocase=() unknown=()
    local res_count exp_count fail_count sum_cases sum_checks sum_fails exit_ok

    LV_INFRA=0; LV_MSG=""; LV_FAILIDS=""

    for id in $expect; do
        n="$(grep -c "^RESULT $id " "$log" 2>/dev/null || true)"
        [ "$n" -eq 0 ] && miss+=("$id")
        [ "$n" -gt 1 ] && dup+=("$id")
        cnt="$(grep -c "^CASE $id " "$log" 2>/dev/null || true)"
        [ "$cnt" -eq 0 ] && nocase+=("$id")
    done
    exp_count="$(printf '%s\n' $expect | grep -c .)"
    res_count="$(grep -c '^RESULT ' "$log" 2>/dev/null || true)"
    while read -r id; do
        [ -z "$id" ] && continue
        local found=0
        for e in $ALL_IDS; do [ "$e" = "$id" ] && found=1; done
        [ "$found" = "1" ] || unknown+=("$id")
    done < <(grep '^RESULT ' "$log" | awk '{print $2}' | sort -u)
    while read -r id; do
        [ -z "$id" ] && continue
        LV_FAILIDS="$LV_FAILIDS $id"
    done < <(grep '^RESULT ' "$log" | grep ' FAIL ' | awk '{print $2}' | sort -u)
    fail_count="$(grep '^RESULT ' "$log" | grep -c ' FAIL ' || true)"

    sum_cases="$(sed -n 's/^SUMMARY cases=\([0-9]*\) checks=.*.*/\1/p' "$log" | tail -1)"
    sum_checks="$(sed -n 's/^SUMMARY cases=[0-9]* checks=\([0-9]*\) fails=.*/\1/p' "$log" | tail -1)"
    sum_fails="$(sed -n 's/^SUMMARY cases=[0-9]* checks=[0-9]* fails=\([0-9]*\).*/\1/p' "$log" | tail -1)"
    exit_ok="$(grep -q '^EXIT OK$' "$log" && echo 1 || echo 0)"
    : "${sum_cases:=NA}" "${sum_checks:=NA}" "${sum_fails:=NA}"

    LV_SUM="cases=$sum_cases checks=$sum_checks fails=$sum_fails rc=$prc"

    if [ "${#miss[@]}" -ne 0 ]; then
        LV_INFRA=1; LV_MSG="声明的 RESULT 身份缺席: ${miss[*]}"
    elif [ "${#dup[@]}" -ne 0 ]; then
        LV_INFRA=1; LV_MSG="RESULT 身份重复: ${dup[*]}"
    elif [ "${#nocase[@]}" -ne 0 ]; then
        LV_INFRA=1; LV_MSG="有 RESULT 但没有配对 CASE 的身份: ${nocase[*]}"
    elif [ "${#unknown[@]}" -ne 0 ]; then
        LV_INFRA=1; LV_MSG="出现未登记的 RESULT 身份: ${unknown[*]}"
    elif [ "$res_count" != "$exp_count" ]; then
        LV_INFRA=1; LV_MSG="执行集合与声明不一致(声明=$exp_count 实际 RESULT=$res_count)"
    elif [ "$sum_cases" != "$exp_count" ] || \
         { [ "$fail_count" -gt 0 ] && [ "$sum_fails" = "0" ]; } || \
         { [ "$fail_count" -eq 0 ] && [ "$sum_fails" != "0" ]; } || \
         [ "$sum_checks" = "NA" ] || [ "$sum_checks" -le 0 ]; then
        # 注意 SUMMARY 的 fails= 数的是**断言条数**而不是用例数，所以这里只要求
        # "有没有 FAIL 用例"与"fails 是不是 0"这两个符号一致，不要求相等。
        LV_INFRA=1; LV_MSG="SUMMARY 不自洽(cases=$sum_cases/$exp_count RESULTFAIL=$fail_count fails=$sum_fails checks=$sum_checks)"
    elif [ "$fail_count" -gt 0 ] && [ "$exit_ok" != "0" ]; then
        LV_INFRA=1; LV_MSG="有 FAIL 但 EXIT 行仍是 OK"
    elif [ "$fail_count" -eq 0 ] && [ "$exit_ok" != "1" ]; then
        LV_INFRA=1; LV_MSG="全 PASS 但 EXIT 不是 OK"
    elif [ "$fail_count" -gt 0 ] && [ "$prc" -ne 1 ]; then
        LV_INFRA=1; LV_MSG="有 $fail_count 条 FAIL 但业务进程退出码=$prc (合同要求恰好 1)"
    elif [ "$fail_count" -eq 0 ] && [ "$prc" -ne 0 ]; then
        LV_INFRA=1; LV_MSG="全 PASS 但业务进程退出码=$prc (合同要求 0)"
    fi

    if [ "$LV_INFRA" = "1" ]; then return 4; fi
    if [ "$fail_count" -gt 0 ]; then return 1; fi
    return 0
}

# 解析夹具台账: 每组期望 id 必须各出现一次且判 PASS; 缺席/重复/不自洽 = 基础设施故障
run_group() {                        # $1=exe 标签(off|on) $2=组名
    local tag="$1" grp="$2"
    local exe="$OUT_DIR/a0_$tag.exe"
    local ids_var="G_${grp}_ids"
    local expect="${!ids_var}"
    local log="$OUT_DIR/run_${tag}_${grp}.txt"

    timeout "$GROUP_TIMEOUT" "$exe" "$grp" > "$log" 2>&1
    local rc=$?
    if [ "$rc" -eq 124 ]; then
        note "  $tag/$grp: INFRA-FAIL 组超时(${GROUP_TIMEOUT}s), rc=124"
        remember 4; return
    fi
    ledger_check "$log" "$expect" "$rc"; local lr=$?
    if [ "$lr" = "0" ]; then
        note "  $tag/$grp: OK ($(printf '%s\n' $expect | grep -c .) 例, $LV_SUM)"
        return
    fi
    if [ "$lr" = "4" ]; then
        note "  $tag/$grp: INFRA-FAIL $LV_MSG ($LV_SUM)"
        remember 4; return
    fi
    note "  $tag/$grp: FAIL 声明内失败身份=$LV_FAILIDS ($LV_SUM)"
    grep "^  CHECK FAIL" "$log" | head -12 | sed 's/^/    /'
    remember 1
}

symbol_check() {
    if [ -z "$NM" ]; then
        note "  符号核对: NM_MISSING => 记 BLOCKED, 不声称已核对"
        remember 4; return 1
    fi
    # 只核对**生产记录器**的符号(夹具自己的端口实现叫 spm_port_*, 两个变体都在, 不算测量代码)。
    local FORBID='spm_(open|win_note|body_end|publish|cmd|init_counter|stop_accepting|dump_records|clear_records|trip_once|state|records_used|in_flight|dropped|counter_status|counter_status_name|clock_basis|clock_basis_name|entry_name|record_at|generation_next|guard_load_value|quiescent|validate|start|ctr_read|ctr_pair|ctr_pair_after|eval|context_ok)\b'
    local off_rec on_rec off_arr on_arr off_entries
    off_rec="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " [TtDdBbVv] $FORBID" || true)"
    on_rec="$("$NM" --defined-only "$OUT_DIR/a0_on.exe"  2>/dev/null | grep -cE " [TtDdBbVv] $FORBID" || true)"
    off_arr="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " [TtDdBbVv] spm_rec$| b spm_rec$| d spm_rec$" || true)"
    on_arr="$("$NM" --defined-only "$OUT_DIR/a0_on.exe"  2>/dev/null | grep -cE " [TtDdBbVv] spm_rec$" || true)"
    off_entries="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " T sp_(trip_fast|trip_for_operation|off_attempt_for)" || true)"
    # 命令名注册接缝(复审 A0-01)：别名条目 __fsym_spm 只能存在于测量变体，默认变体必须没有。
    off_fsym="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " [TtDdBbVvrR] __fsym_spm$" || true)"
    on_fsym="$("$NM" --defined-only "$OUT_DIR/a0_on.exe"  2>/dev/null | grep -cE " [TtDdBbVvrR] __fsym_spm$" || true)"
    on_fsym_cmd="$("$NM" --defined-only "$OUT_DIR/a0_on.exe" 2>/dev/null | grep -cE " [TtDdBbVvrR] __fsym_spm_cmd$" || true)"
    note "  符号核对: a0_off 生产记录器符号=$off_rec 记录数组=$off_arr 命令注册条目=$off_fsym (都要求 0) / 三个被测入口存在=$off_entries (要求 3)"
    note "  符号核对: a0_on  生产记录器符号=$on_rec (要求 >=18) 记录数组=$on_arr (要求 1) __fsym_spm=$on_fsym (要求 1) __fsym_spm_cmd=$on_fsym_cmd (要求 0)"
    if [ "$off_rec" != "0" ] || [ "$off_arr" != "0" ] || [ "$off_fsym" != "0" ] \
       || [ "$off_entries" != "3" ] \
       || [ "$on_rec" -lt 18 ] || [ "$on_arr" != "1" ] || [ "$on_fsym" != "1" ] \
       || [ "$on_fsym_cmd" != "0" ]; then
        note "  a0_off 里残留的生产符号:"
        "$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -E " $FORBID" | head -8 | sed 's/^/    /'
        note "  a0_on 里的 __fsym_* 条目:"
        "$NM" --defined-only "$OUT_DIR/a0_on.exe" 2>/dev/null | grep "__fsym" | head -8 | sed 's/^/    /'
        remember 1; return 1
    fi
    return 0
}

# ------------------------------------------------------------------------ 正向
BUILD_OK=1
build_variant off || { note "正向: a0_off 构建失败 => INFRA-FAIL"; remember 4; BUILD_OK=0; }
build_variant on  || { note "正向: a0_on 构建失败 => INFRA-FAIL"; remember 4; BUILD_OK=0; }

if [ "$BUILD_OK" = "1" ]; then
    note "--- 符号存在性(编译期绕开的可判定证据) ---"
    symbol_check || note "  符号核对未通过(计入总结果)"
    note "--- 逐组执行 ---"
    for g in $SELECTED; do
        case " $OFF_GROUPS " in *" $g "*) run_group off "$g" ;; esac
        case " $ON_GROUPS " in *" $g "*)
            # a0_on 里也允许显式选 off 组(它自己不跑测量用例), 但 off 组只在 a0_off 上跑
            run_group on "$g" ;; esac
    done
fi

# ------------------------------------------------------------------------ 反向
if [ "${REVERSE:-0}" = "1" ]; then
    if [ "$BUILD_OK" != "1" ]; then
        note "反向: 正向构建未通过, 跳过(不冒充反向结果)"
        remember 4
    else
    note "--- 具名反向家族(在仓外源码副本上变异) ---"
    # 每个家族: 注入点必须逐条命中恰好一次; 期望**具名**用例判 FAIL, 其余保持 PASS。
    run_reverse() {                   # $1=家族名（每组期望失败身份由 rev_expect 给）
        local fam="$1"
        shift
        local work="$OUT_DIR/rev_$fam"
        mkdir -p "$work/app" || { note "  $fam: INFRA-FAIL 无法建工作目录"; remember 4; return; }
        cp "$SRC_APP/sp_measure_a0.c" "$SRC_APP/sp_measure_a0.h" "$SRC_APP/stop_priority.c" \
           "$work/app/" 2>/dev/null || { note "  $fam: INFRA-FAIL 副本失败"; remember 4; return; }
        python - "$work/app" "$fam" <<'PY'
import sys, os
d, fam = sys.argv[1], sys.argv[2]
CM = "/* NEGATIVE-INJECTION %s */" % fam
def path(f):
    p = os.path.join(d, f)
    if not os.path.exists(p):
        raise SystemExit("NOFILE %s" % f)
    return p
spec = {
  # 把"逐样本计数器不可用/门控不成立/没有往前走"错误地接受为有效时长
  # => 应由 TMA02/TMA03/TMA21 具名检出。r1 起这三条判据集中在 spm_eval() 的一处，
  #    所以注入锚点就是那一整块（复审 A0-04 要求注入到"真实经过的语义位置"）。
  "rev_spm_unavailable_is_valid": [
    ("sp_measure_a0.c",
     "    if (c->counter_status != SPM_CNT_OK)                  why |= SPM_IV_COUNTER_NOT_OK;\n"
     "    if ((c->gates & SPM_GT_ALL_MASK) != SPM_GT_ALL_MASK)   why |= SPM_IV_GATE_LOST;\n"
     "    if (c->counter_status == SPM_CNT_OK && c->body_end == c->body_begin)\n"
     "        why |= SPM_IV_NO_PROGRESS;",
     "    %s" % CM),
  ],
  # 把本次身份/raw 快照挪到解屏蔽之后(新故障之后) => 应由 TMA11 具名检出。
  "rev_spm_snapshot_after_unmask": [
    ("stop_priority.c",
     "    spm_pin_state(c);                       /* 仍在本次屏蔽窗口内固定本次证据 */\n    SP_BARRIER();",
     "    %s\n    SP_BARRIER();" % CM),
    ("stop_priority.c",
     "    rt_hw_interrupt_enable(l);              /* 原 sp_end 第三条 */\n    spm_publish(c, api_rc, branch_id, pwm_called, low_called);",
     "    rt_hw_interrupt_enable(l);              /* 原 sp_end 第三条 */\n    %s\n    spm_pin_state(c);\n    spm_publish(c, api_rc, branch_id, pwm_called, low_called);" % CM),
  ],
}.get(fam, [])
if not spec:
    raise SystemExit("NOSPEC %s" % fam)
for f, old, new in spec:
    p = path(f)
    s = open(p, encoding="utf-8").read()
    if s.count(old) != 1:
        raise SystemExit("INJECT-MISS %s in %s (hits=%d)" % (fam, f, s.count(old)))
    open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))
print("INJECTED %s rules=%d" % (fam, len(spec)))
PY
        local irc=$?
        if [ "$irc" -ne 0 ]; then
            note "  $fam: INFRA-FAIL 注入未精确命中(irc=$irc), 未运行业务进程"
            remember 4; return
        fi
        local rlog="$work/build.txt"
        "$GCC" $FLAGS -DNS800_TARGET_MEASUREMENT_A0=1 -DNS800_SPM_HOST_BACKEND \
            -I "$HERE" -I "$SHIM_DIR" -I "$work/app" -I "$SRC_APP" -I "$HAL_DRV" -I "$COMPAT" \
            -Wl,--wrap=printf "$A0_MAIN" "$work/app/sp_measure_a0.c" "$work/app/stop_priority.c" \
            -o "$work/a0_rev.exe" > "$rlog" 2>&1
        if [ $? -ne 0 ]; then
            note "  $fam: INFRA-FAIL 变异副本编译不过"
            head -20 "$rlog" | sed 's/^/    /'
            remember 4; return
        fi
        local out="$work/run.txt" g glog grp_expect decl lr bad=0 hit=0
        : > "$out"
        for g in $ON_GROUPS; do
            glog="$work/run_$g.txt"
            timeout "$GROUP_TIMEOUT" "$work/a0_rev.exe" "$g" > "$glog" 2>&1
            local grc=$?
            cat "$glog" >> "$out"
            if [ "$grc" -eq 124 ]; then
                note "  $fam: INFRA-FAIL 组 $g 超时(rc=124)"; remember 4; bad=1; break
            fi
            # 与正向**同一套**逐组台账合同(身份/数量/成对/SUMMARY/EXIT/退出码)
            decl="G_${g}_ids"
            ledger_check "$glog" "${!decl}" "$grc"; lr=$?
            if [ "$lr" = "4" ]; then
                note "  $fam: INFRA-FAIL 组 $g 台账不自洽: $LV_MSG ($LV_SUM)"
                remember 4; bad=1; break
            fi
            grp_expect="$(rev_expect "$fam" "$g")"
            hit=$((hit + $(printf '%s\n' $grp_expect | grep -c .)))
            local norm_a norm_e
            norm_a="$(printf '%s\n' $LV_FAILIDS | grep . | sort | tr '\n' ' ')"
            norm_e="$(printf '%s\n' $grp_expect | grep . | sort | tr '\n' ' ')"
            if [ "$lr" = "0" ] && [ -n "$grp_expect" ]; then
                note "  $fam: NOT-DETECTED 组 $g 期望具名失败 [$grp_expect] 一个都没出现"
                remember 1; bad=1; break
            fi
            if [ "$norm_a" != "$norm_e" ]; then
                note "  $fam: PARTIAL/UNEXPECTED 组 $g 实际失败=[$norm_a] 期望=[$norm_e] ($LV_SUM)"
                grep "^  CHECK FAIL" "$glog" | head -8 | sed 's/^/    /'
                remember 1; bad=1; break
            fi
        done
        [ "$bad" = "1" ] && return
        note "  $fam: META-OK 具名检出 $hit 例，其余每组台账逐条一致且保持 PASS（含退出码合同）"
    }
    run_reverse rev_spm_unavailable_is_valid
    run_reverse rev_spm_snapshot_after_unmask

    # ---------------- 判定器自身的负控（复审 A0-04）----------------
    # 只在**反向构建的夹具副本**里省略非锚身份 TMA18 的 RESULT 行(保留 CASE/SUMMARY/退出码)，
    # 生产源码一个字节都不改。修后的共用台账判定必须报 INFRA-FAIL；若仍报 META-OK/全绿,
    # 说明判定器有能力把缺席说成"其它保持 PASS" —— 那是判定器缺陷, 记 INFRA-FAIL。
    note "--- 台账判定器负控: 故意缺席非锚身份 TMA18 的 RESULT ---"
    NEG_DIR="$OUT_DIR/negctrl_ledger"
    mkdir -p "$NEG_DIR" || { note "  negctrl: INFRA-FAIL 无法建目录"; remember 4; }
    if [ -d "$NEG_DIR" ]; then
        python - "$A0_MAIN" "$NEG_DIR/fixture_omit_tma18_result.c" <<'PY'
import sys, io
src = io.open(sys.argv[1], encoding='utf-8').read()
old = ('static void case_end(void)\n'
       '{\n'
       '    emit("RESULT %s %s checks=%d fails=%d' + chr(92) + 'n", g_case_id,\n')
new = ('static void case_end(void)\n'
       '{\n'
       '    if (strcmp(g_case_id, "TMA18") == 0)\n'
       '    { g_cases_done++; return; }   /* NEGCTRL: 省略非锚身份的 RESULT 行 */\n'
       '    emit("RESULT %s %s checks=%d fails=%d' + chr(92) + 'n", g_case_id,\n')
if src.count(old) != 1:
    raise SystemExit("NEGCTRL-INJECT-MISS hits=%d" % src.count(old))
io.open(sys.argv[2], 'w', encoding='utf-8', newline='\n').write(src.replace(old, new))
print("NEGCTRL-INJECTED fixture_omit_tma18_result.c")
PY
        if [ $? -ne 0 ]; then
            note "  negctrl: INFRA-FAIL 夹具副本变异未精确命中"
            remember 4
        else
            "$GCC" $FLAGS -DNS800_TARGET_MEASUREMENT_A0=1 -DNS800_SPM_HOST_BACKEND \
                "${INCS[@]}" -Wl,--wrap=printf \
                "$NEG_DIR/fixture_omit_tma18_result.c" "$SRC_APP/sp_measure_a0.c" \
                "$SRC_APP/stop_priority.c" -o "$NEG_DIR/a0_neg.exe" \
                > "$NEG_DIR/build.txt" 2>&1
            if [ $? -ne 0 ]; then
                note "  negctrl: INFRA-FAIL 变异夹具编译不过"
                head -20 "$NEG_DIR/build.txt" | sed 's/^/    /'
                remember 4
            else
                neg_ok=0
                for g in $ON_GROUPS; do
                    nlog="$NEG_DIR/run_$g.txt"
                    timeout "$GROUP_TIMEOUT" "$NEG_DIR/a0_neg.exe" "$g" > "$nlog" 2>&1
                    nrc=$?
                    decl="G_${g}_ids"
                    ledger_check "$nlog" "${!decl}" "$nrc"; nlr=$?
                    if [ "$g" = "ledger" ]; then
                        if [ "$nlr" = "4" ]; then
                            note "  negctrl/ledger: 按合同判为 INFRA-FAIL —— $LV_MSG  [期望结果]"
                            neg_ok=1
                        else
                            note "  negctrl/ledger: 判定器仍把缺席当成绿(lr=$nlr, $LV_SUM) => 判定器缺陷"
                            remember 4
                        fi
                    elif [ "$nlr" != "0" ] && [ "$g" != "counter" ]; then
                        note "  negctrl/$g: INFRA-FAIL $LV_MSG ($LV_SUM)"
                        remember 4
                    fi
                done
                [ "$neg_ok" = "1" ] || { note "  negctrl: NOT-EFFECTIVE 未观察到预期的 INFRA-FAIL"; remember 4; }
            fi
        fi
    fi
    fi
else
    note "--- 反向: 本轮未要求(REVERSE=1 才跑) ---"
fi

note "--- 总结果 ---"
case "$WORST" in
    0) note "A0CHAIN-META: OK (正向逐组一致 + 反向按声明执行)"; exit 0 ;;
    1) note "A0CHAIN-META: FAIL (有用例判 FAIL)"; exit 1 ;;
    4) note "A0CHAIN-META: INFRA-FAIL (构建/注入/台账不自洽, 不算检出也不算通过)"; exit 4 ;;
    *) note "A0CHAIN-META: 未知状态 rc=$WORST"; exit 4 ;;
esac
