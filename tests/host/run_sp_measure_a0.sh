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
#   a0_on  : =1 + NS800_SPM_HOST_BACKEND              => 跑 TMA02..TMA18
#
# 生产打印经 -Wl,--wrap=printf 计成"窗口内禁止项"台账(夹具自身输出走 fprintf, 不经该通道)。
#
# 用法:
#   A0_OUT_DIR=<仓外目录> bash tests/host/run_sp_measure_a0.sh
#   ONLY=counter,batch bash tests/host/run_sp_measure_a0.sh     # 选择先验证再执行
#   REVERSE=1 bash tests/host/run_sp_measure_a0.sh              # 两个具名反向家族
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
A0_GROUPS="off counter contract conc batch cli ledger"
G_off_ids="TMA01"
G_counter_ids="TMA03 TMA02 TMA04 TMA05 TMA06"
G_contract_ids="TMA07 TMA08 TMA09 TMA10"
G_conc_ids="TMA11 TMA12 TMA13"
G_batch_ids="TMA14 TMA15 TMA16"
G_cli_ids="TMA17"
G_ledger_ids="TMA18"
# 每个可执行体跑哪些组
OFF_GROUPS="off"
ON_GROUPS="counter contract conc batch cli ledger"
ALL_IDS="$G_off_ids $G_counter_ids $G_contract_ids $G_conc_ids $G_batch_ids $G_cli_ids $G_ledger_ids"

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

# 解析夹具台账: 每组期望 id 必须各出现一次且 RESULT PASS; 缺席/重复/不自洽 = 基础设施故障
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

    local miss=0 dup=0 failed=0 checks=0 fails=0 summary_ok=0 absent=()
    for id in $expect; do
        local n
        n="$(grep -c "^RESULT $id " "$log" || true)"
        if [ "$n" -eq 0 ]; then absent+=("$id"); miss=$((miss+1)); continue; fi
        if [ "$n" -gt 1 ]; then dup=$((dup+1)); fi
        if ! grep -q "^RESULT $id PASS " "$log"; then failed=$((failed+1)); fi
    done
    local res_count
    res_count="$(grep -c "^RESULT " "$log" || true)"
    local exp_count
    exp_count="$(printf '%s\n' $expect | grep -c .)"
    summary_ok="$(grep -q "^EXIT OK$" "$log" && echo 1 || echo 0)"
    checks="$(sed -n 's/^SUMMARY cases=[0-9]* checks=\([0-9]*\) fails=.*/\1/p' "$log" | tail -1)"
    fails="$(sed -n 's/^SUMMARY cases=[0-9]* checks=[0-9]* fails=\([0-9]*\).*/\1/p' "$log" | tail -1)"
    [ -n "$checks" ] || checks=0
    [ -n "$fails" ] || fails=0

    if [ "$miss" -ne 0 ] || [ "$dup" -ne 0 ] || [ "$res_count" != "$exp_count" ]; then
        note "  $tag/$grp: INFRA-FAIL 执行集合与声明不一致 (声明=$exp_count 实际 RESULT=$res_count 缺席=${#absent[@]} 重复=$dup)"
        [ "${#absent[@]}" -gt 0 ] && note "    缺席: ${absent[*]}"
        remember 4; return
    fi
    if [ "$failed" -gt 0 ] || [ "$fails" -gt 0 ]; then
        if [ "$rc" -ne 1 ]; then
            note "  $tag/$grp: INFRA-FAIL 有 FAIL 用例但业务 rc=$rc (应为 1)"
            remember 4; return
        fi
        note "  $tag/$grp: FAIL 用例=$failed checks=$checks fails=$fails rc=$rc"
        grep "^  CHECK FAIL" "$log" | head -12 | sed 's/^/    /'
        remember 1; return
    fi
    if [ "$rc" -ne 0 ] || [ "$summary_ok" != "1" ]; then
        note "  $tag/$grp: INFRA-FAIL 全部 PASS 但 rc=$rc / EXIT 非 OK"
        remember 4; return
    fi
    note "  $tag/$grp: OK ($exp_count 例, checks=$checks, rc=0)"
}

symbol_check() {
    if [ -z "$NM" ]; then
        note "  符号核对: NM_MISSING => 记 BLOCKED, 不声称已核对"
        remember 4; return 1
    fi
    # 只核对**生产记录器**的符号(夹具自己的端口实现叫 spm_port_*, 两个变体都在, 不算测量代码)。
    local FORBID='spm_(open|win_note|body_end|publish|cmd|init_counter|stop_accepting|dump_records|clear_records|trip_once|state|records_used|in_flight|dropped|counter_status|counter_status_name|clock_basis|clock_basis_name|entry_name|record_at|generation_next|guard_load_value)\b'
    local off_rec on_rec off_arr on_arr off_entries
    off_rec="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " [TtDdBbVv] $FORBID" || true)"
    on_rec="$("$NM" --defined-only "$OUT_DIR/a0_on.exe"  2>/dev/null | grep -cE " [TtDdBbVv] $FORBID" || true)"
    off_arr="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " [TtDdBbVv] spm_rec$| b spm_rec$| d spm_rec$" || true)"
    on_arr="$("$NM" --defined-only "$OUT_DIR/a0_on.exe"  2>/dev/null | grep -cE " [TtDdBbVv] spm_rec$" || true)"
    off_entries="$("$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -cE " T sp_(trip_fast|trip_for_operation|off_attempt_for)" || true)"
    note "  符号核对: a0_off 生产记录器符号=$off_rec 记录数组=$off_arr (都要求 0) / 三个被测入口存在=$off_entries (要求 3)"
    note "  符号核对: a0_on  生产记录器符号=$on_rec (要求 >=18) 记录数组=$on_arr (要求 1)"
    if [ "$off_rec" != "0" ] || [ "$off_arr" != "0" ] || [ "$off_entries" != "3" ] \
       || [ "$on_rec" -lt 18 ] || [ "$on_arr" != "1" ]; then
        note "  a0_off 里残留的生产符号:"
        "$NM" --defined-only "$OUT_DIR/a0_off.exe" 2>/dev/null | grep -E " $FORBID" | head -8 | sed 's/^/    /'
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
    run_reverse() {                   # $1=家族名 $2=期望失败id(空格) $3..=python 注入语句
        local fam="$1"
        shift
        local expect_ids="$1"
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
  # 把"不可用/未检查的计数器"错误地接受为有效时长 => 应由 TMA02/TMA03 具名检出。
  "rev_spm_unavailable_is_valid": [
    ("sp_measure_a0.c",
     "        c->invalid_reason |= SPM_IV_COUNTER_NOT_OK;\n        if (!spm_guard_ready)",
     "        %s\n        if (!spm_guard_ready)" % CM),
    ("sp_measure_a0.c",
     "    if (c->counter_status != SPM_CNT_OK) why |= SPM_IV_COUNTER_NOT_OK;",
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
        local out="$work/run.txt" allpass=1
        : > "$out"
        for g in $ON_GROUPS; do
            timeout "$GROUP_TIMEOUT" "$work/a0_rev.exe" "$g" >> "$out" 2>&1
            local grc=$?
            [ "$grc" -le 1 ] || { note "  $fam: INFRA-FAIL 组 $g rc=$grc"; remember 4; allpass=0; break; }
        done
        [ "$allpass" = "1" ] || return
        local hit=0 miss_names=()
        for id in $expect_ids; do
            if grep -q "^RESULT $id FAIL " "$out"; then hit=$((hit+1)); else miss_names+=("$id"); fi
        done
        local others=0 rid
        while read -r rid; do
            [ -z "$rid" ] && continue
            case " $expect_ids " in *" $rid "*) : ;; *) others=$((others+1)) ;; esac
        done < <(grep '^RESULT ' "$out" | grep ' FAIL ' | awk '{print $2}')
        if [ "$hit" -eq 0 ]; then
            note "  $fam: NOT-DETECTED 期望的失败身份一个都没出现(纠正注入点后再运行)"
            remember 1; return
        fi
        if [ "${#miss_names[@]}" -gt 0 ] || [ "$others" != "0" ]; then
            note "  $fam: PARTIAL/UNEXPECTED 命中=$hit 未命中=${miss_names[*]:-none} 其他失败=$others"
            remember 1; return
        fi
        note "  $fam: META-OK 具名检出 $hit 例 ($(printf '%s ' $expect_ids)) 其余保持 PASS"
    }
    run_reverse rev_spm_unavailable_is_valid "TMA02 TMA03"
    run_reverse rev_spm_snapshot_after_unmask "TMA11"
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
