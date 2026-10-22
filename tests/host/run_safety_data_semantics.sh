#!/usr/bin/env bash
# run_safety_data_semantics.sh - 安全数据语义链 runner (phase8-safety-data-semantics, 2026-10-22)
#
# 与旧链(build_and_run.sh / run_negatives.sh / run_meta_negatives.sh)和 I/O 链
# (run_io_behavior.sh)**并列**, 不改它们: 本链真编译并执行
#   applications/sensor_service.c  (采样: 电平 / active / valid / 聚合标志 / 输出)
#   applications/blackbox.c        (拷贝 / flags 打包 / 语义版本解释 / dump)
# 其余依赖(GPIO、RT 原语、其他传感器与电机服务、存储 API)是 sd_main.c 里的显式可注入桩,
# 存储用内存环模拟 —— 不重建真实 Flash 驱动, 也不做全系统仿真。
#
# 变体(同一份生产源码, 只改预处理宏):
#   off_target  SAFETY_LIMIT_INPUTS_ENABLED=RT_FALSE  = 目标发布配置
#   on_target   SAFETY_LIMIT_INPUTS_ENABLED=RT_TRUE   (仅主机分支, 不进 Keil)
#
# 判定纪律沿用 R2/R3/R4: 先过 require_outside_repo 再动任何文件; 只删带本工具哨兵的目录;
# 编译失败不跑旧产物; 逐 id 台账必须"出现恰好一次且判 PASS"; 缺席/重复=基础设施故障(4),
# 判 FAIL 或自报 fail≠0 或进程 rc≠0 = 检出失败(1); 最外层真实退出码反映失败。
#
# 返修(复审件 NS800_Safety_Data_Semantics_Review_2399d28_20261023.md §4/§6)补的两条入口纪律:
#   SD-NEW-01  ONLY 在**任何清理/创建/重定向/编译之前**验证: 只接受已知完整变体名。
#              **未设置或空串 ONLY = 默认跑两个变体**(不是零执行); 只含空白的 ONLY、未知词、
#              合法词夹带未知词、重复词, 以及 ONLY 与 REVERSE=1 的组合, 一律 rc=4 拒绝且不动
#              任何文件。汇总只声明**实际执行集合**, 未执行的显式 NOT_RUN; 只有默认(两变体)选择
#              全部执行且通过, 才说"两个变体全部真执行"。
#   SD-NEW-02  反向"检测成功"必须同时满足: 副本构建成功 + 夹具**合同退出码 rc=1** + 完整且自洽的
#              适用台账(必需 id 唯一出现、无未登记 id、SUMMARY 恰好一条且 cases/pass/fail 与实际行
#              对齐) + 指定 FAIL 身份全部命中。非 0 非 1 的退出(信号/异常终止)或台账不完整
#              => INFRA-FAIL(4), 不得记 META-OK; rc=0 或缺少期望身份 => NOT-DETECTED(1)。
#              原 R1/R2 判据未放宽: R1 附带 SD05c 失败是当前已知结果, 如实登记为"额外失败"
#              但**不改变判定**(不因额外失败放宽, 也不要求 FAIL 数与命中数相等)。
#
# 用法: bash tests/host/run_safety_data_semantics.sh
#       SD_OUT_DIR=<仓外目录> SRC_APP=<快照目录> GCC=<编译器> ONLY=off_target REVERSE=1 ...
#       SD_VALIDATE_FILE=<既有输出文件> SD_VALIDATE_VARIANT=off_target   # 只复核台账, 不编译不运行
# 退出码: 0=通过; 1=检出失败; 4=基础设施/参数故障或被选择却未执行; 127=环境缺失

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_sd}"
SD_MAIN="${SD_MAIN:-$HERE/sd_main.c}"
OUT_DIR="${SD_OUT_DIR-$(env_work_root)/sd}"
ONLY="${ONLY:-}"
PY="${PY:-python}"

# ---- 用例台账(必须与 sd_main.c 里 check() 的 id 一字不差) ----
COMMON_CASES="SD03-parse-fail-clears-only-that-valid-bit
SD04-illegal-reading-never-becomes-valid-high
SD05-dirty-baseframe-does-not-pollute
SD05c-recovery-makes-source-valid-again
SD06-private-context-isolated-and-divider-kept
SD06b-valid-fresh-division-semantics-kept
SD07-same-source-frame-propagates
SD07b-no-recompute-at-write-time
SD08-flags-position-vector-against-independent-expectation
SD08b-legacy-valid-and-event-fields-unchanged
SD08c-pre-post-event-responsibility-kept
SD09-schema-dispatch-by-record-not-by-board
SD09b-dump-keeps-legacy-raw-uninterpreted
SD10a-snapshot-distinguishes-valid-invalid-disabled
SD10b-dump-shows-version-and-per-channel-readability
SD11a-no-gpio-write-in-sampling-or-conversion
SD11b-uart-only-the-divided-sg-read
SD11c-layout-and-storage-offsets-unchanged"
OFF_ONLY_CASES="SD01-off-masks-limits-never-touched
SD01b-off-output-disabled-not-safe
SD05b-stale-limit-values-not-valid"
ON_ONLY_CASES="SD02-on-all-four-active-limit-low-still-valid
SD02b-on-output-trigger-state-is-valid"

KNOWN_VARIANTS="off_target on_target"
VARIANTS="$KNOWN_VARIANTS"

overall=0
detected_fail=0
declare -a matrix=()
declare -a SELECTED=()
declare -a EXECUTED=()
declare -a SELECTED_NOT_RUN=()
SELECT_MODE="default"

note() { printf '%s\n' "$*"; }
remember() { case "$1" in ''|*[!0-9]*) return 0 ;; esac
             if [ "$overall" -eq 0 ] && [ "$1" -ne 0 ]; then overall="$1"; fi; }
file_bytes() { if [ -f "$1" ]; then wc -c <"$1" | tr -d ' '; else echo ABSENT; fi; }

# ---------- SD-NEW-01: ONLY 在任何文件操作之前验证 ----------
# 成功: 选择写入 SELECTED, SELECT_MODE=default|explicit, 返回 0
# 拒绝: 打印原因并返回 1(调用方立即 rc=4, 不创建/不清理/不重定向/不编译)
validate_only() {
    local tok s
    SELECTED=()
    if [ -z "$ONLY" ]; then
        for tok in $KNOWN_VARIANTS; do SELECTED+=("$tok"); done
        SELECT_MODE="default"
        return 0
    fi
    set -- $ONLY                                  # 词分割: 只含空白 => $# == 0(空串走上面的默认分支)
    if [ "$#" -eq 0 ]; then
        note "REJECT(ONLY): 显式设置了 ONLY 但没有任何有效选择词(只含空白) —— 不能当作已执行"
        return 1
    fi
    for tok in "$@"; do
        case " $KNOWN_VARIANTS " in
            *" $tok "*) : ;;
            *) note "REJECT(ONLY): 未知选择词 '$tok'(只接受完整名称: $KNOWN_VARIANTS)"; return 1 ;;
        esac
        for s in ${SELECTED[@]+"${SELECTED[@]}"}; do
            if [ "$s" = "$tok" ]; then
                note "REJECT(ONLY): 重复词 '$tok' —— 明确拒绝, 不做隐式归并"
                return 1
            fi
        done
        SELECTED+=("$tok")
    done
    SELECT_MODE="explicit"
    return 0
}

# ---------- 台账与 SUMMARY 合同校验(正常变体与反向共用同一实现) ----------
# 结果写入 LED_*; 返回 0 = 结构完整且计数自洽(逐 id 的 PASS/FAIL 由调用方处置), 4 = 基础设施故障
validate_ledger() {                               # validate_ledger <输出文件> <变体>
    local out="$1" name="$2" r id cnt sumline
    LED_MISSING=0; LED_DUP=0; LED_UNREG=0; LED_REQ_FAILS=0
    LED_CASES=0; LED_PASSN=0; LED_FAILN=0; LED_SUMMARIES=0
    LED_REPORTED_CASES="MISSING"; LED_REPORTED_PASS="MISSING"; LED_REPORTED_FAIL="MISSING"
    LED_REQ="$(required_of "$name")"

    if [ ! -s "$out" ]; then note "  台账: 输出文件缺失或为空: $out"; return 4; fi

    LED_CASES="$(grep -cE '^CASE\|' "$out")"
    LED_PASSN="$(grep -cE '^CASE\|[^|]*\|PASS\|' "$out")"
    LED_FAILN="$(grep -cE '^CASE\|[^|]*\|FAIL\|' "$out")"
    LED_SUMMARIES="$(grep -cE '^SUMMARY\|' "$out")"
    if [ "$LED_SUMMARIES" -ne 1 ]; then
        note "  台账: SUMMARY 行 $LED_SUMMARIES 条(必须恰好 1 条)"
        return 4
    fi
    sumline="$(grep -E '^SUMMARY\|' "$out" | head -1)"
    LED_REPORTED_CASES="$(printf '%s' "$sumline" | sed -n 's/.*cases=\([0-9]*\).*/\1/p')"
    LED_REPORTED_PASS="$(printf '%s' "$sumline" | sed -n 's/.*pass=\([0-9]*\).*/\1/p')"
    LED_REPORTED_FAIL="$(printf '%s' "$sumline" | sed -n 's/.*fail=\([0-9]*\).*/\1/p')"
    for r in "$LED_REPORTED_CASES" "$LED_REPORTED_PASS" "$LED_REPORTED_FAIL"; do
        [ "$r" = "MISSING" ] && { note "  台账: SUMMARY 字段不全: $sumline"; return 4; }
    done
    if [ "$LED_REPORTED_CASES" != "$LED_CASES" ] || [ "$LED_REPORTED_PASS" != "$LED_PASSN" ] \
       || [ "$LED_REPORTED_FAIL" != "$LED_FAILN" ] \
       || [ "$((LED_REPORTED_PASS + LED_REPORTED_FAIL))" -ne "$LED_REPORTED_CASES" ]; then
        note "  台账: SUMMARY 与实际行不一致 cases=$LED_REPORTED_CASES/$LED_CASES pass=$LED_REPORTED_PASS/$LED_PASSN fail=$LED_REPORTED_FAIL/$LED_FAILN"
        return 4
    fi

    for r in $LED_REQ; do
        cnt="$(grep -c "^CASE|$r|" "$out")"
        if [ "$cnt" -eq 0 ]; then LED_MISSING=$((LED_MISSING+1)); note "  缺席: $r"
        elif [ "$cnt" -gt 1 ]; then LED_DUP=$((LED_DUP+1)); note "  重复执行: $r ($cnt 次)"; fi
    done
    for id in $(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u); do
        case " $LED_REQ " in *" $id "*) : ;; *) LED_UNREG=$((LED_UNREG+1)); note "  未登记用例: $id" ;; esac
    done
    if [ "$LED_UNREG" -ne 0 ]; then
        note "  台账: 出现 $LED_UNREG 个未登记用例(偷偷加用例不算通过)"
        return 4
    fi
    if [ "$LED_MISSING" -ne 0 ] || [ "$LED_DUP" -ne 0 ]; then
        note "  台账: 缺席=$LED_MISSING 重复=$LED_DUP => 必需用例未全部/唯一执行"
        return 4
    fi
    for r in $LED_REQ; do
        [ "$(grep -c "^CASE|$r|PASS|" "$out")" = "1" ] || LED_REQ_FAILS=$((LED_REQ_FAILS+1))
    done
    return 0
}

required_of() {
    case "$1" in
        off_target) printf '%s\n' "$COMMON_CASES $OFF_ONLY_CASES" | tr '\n' ' ' ;;
        on_target)  printf '%s\n' "$COMMON_CASES $ON_ONLY_CASES"  | tr '\n' ' ' ;;
    esac
}

variant_flags() {
    case "$1" in
        off_target) echo "" ;;
        on_target)  echo "-DSAFETY_LIMIT_INPUTS_ENABLED=RT_TRUE" ;;
    esac
}

# ---------- 入口参数验证(在任何 rm/mkdir/重定向/编译之前) ----------
REVERSE="${REVERSE:-0}"

# 只复核既有产物的台账(供返修回归使用): 不编译、不运行、不创建或清理任何目录
if [ -n "${SD_VALIDATE_FILE:-}" ]; then
    V="${SD_VALIDATE_VARIANT:-off_target}"
    case " $KNOWN_VARIANTS " in *" $V "*) : ;; *) note "FATAL: SD_VALIDATE_VARIANT 未知: $V"; exit 4 ;; esac
    note "###### 台账复核模式(不编译/不运行/不动任何目录) ######"
    note "SD_VALIDATE_FILE=$SD_VALIDATE_FILE  变体=$V"
    if validate_ledger "$SD_VALIDATE_FILE" "$V"; then
        note "VALIDATE RESULT: 台账合同成立(结构完整且计数自洽)"
        exit 0
    fi
    note "VALIDATE RESULT: 拒绝 —— 台账合同不成立"
    exit 4
fi

# ONLY 与 REVERSE 的组合必须显式处理, 不得静默忽略参数
if [ "$REVERSE" = "1" ] && [ -n "$ONLY" ]; then
    note "REJECT: REVERSE=1 不接受 ONLY 选择(反向两项固定覆盖 off_target 的完整台账) —— 不静默忽略参数"
    exit 4
fi
if ! validate_only; then
    note "FATAL: ONLY 选择被拒绝; 未创建目录、未清理、未重定向、未编译"
    exit 4
fi

GCC_RESOLVED="$(env_detect_gcc || true)"
if [ -z "$GCC_RESOLVED" ] || [ ! -x "$GCC_RESOLVED" ]; then
    note "FATAL: 未找到可用主机编译器(设 GCC=<路径> 或把 gcc 放进 PATH)"; exit 127
fi
GCC="$GCC_RESOLVED"

# ---- R4 守卫: 任何 rm/mkdir/重定向/编译之前先验证工作目录 ----
if ! OUT_DIR="$(require_outside_repo "$OUT_DIR" "SD_OUT_DIR")"; then
    note "FATAL: SD_OUT_DIR 被工作目录守卫拒绝(未创建目录, 未重定向, 未编译)"
    exit 4
fi
[ -f "$SD_MAIN" ]  || { note "FATAL: 语义链夹具源不存在: $SD_MAIN"; exit 127; }
[ -d "$SHIM_DIR" ] || { note "FATAL: 语义链兼容层目录不存在: $SHIM_DIR"; exit 127; }
[ -d "$SRC_APP" ]  || { note "FATAL: 生产源码目录不存在: $SRC_APP"; exit 127; }

if [ -e "$OUT_DIR" ] && [ ! -f "$OUT_DIR/.ns800_host_work" ]; then
    note "FATAL: 输出目录已存在但没有本工具的哨兵, 拒绝清理: $OUT_DIR"
    exit 4
fi
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR" || { note "FATAL: 无法创建 OUT_DIR"; exit 4; }
printf 'ns800-host-chain safety-data-semantics runner work dir (safe to delete by this tool only)\n' \
    > "$OUT_DIR/.ns800_host_work" || { note "FATAL: 哨兵写不进去"; exit 4; }

FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function"

note "###### 安全数据语义链 (基线 $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)) ######"
note "SRC_APP=$SRC_APP"
note "SHIM_DIR=$SHIM_DIR"
note "OUT_DIR=$OUT_DIR"
note "compiler=$("$GCC" --version | head -1)"
note "ONLY=${ONLY:-<未设置>}  选择模式=$SELECT_MODE  选择集合=${SELECTED[*]}"

build_variant() {
    local name="$1" extra="$2"
    local log="$OUT_DIR/${name}_build.txt" exe="$OUT_DIR/${name}.exe"
    local rc

    rm -f "$exe"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -I "$SHIM_DIR" -I "$SRC_APP" "$SD_MAIN" -o "$exe" > "$log" 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        tail -25 "$log"
        note "  $name 编译失败 rc=$rc => 不运行二进制(旧产物已删除)"
        matrix+=("$name | COMPILE_FAILED rc=$rc")
        remember "$rc"
        return 2
    fi
    return 0
}

run_variant() {
    local name="$1"
    local exe="$OUT_DIR/${name}.exe" out="$OUT_DIR/${name}_out.txt"
    local proc_rc

    "$exe" > "$out" 2>&1
    proc_rc=$?
    EXECUTED+=("$name")

    if ! validate_ledger "$out" "$name"; then
        matrix+=("$name | INFRA-FAIL 台账合同不成立 (proc_rc=$proc_rc)")
        note "  基础设施故障: 台账缺失/重复/未登记, 或 SUMMARY 缺失/不唯一/计数不自洽"
        remember 4
        return 0
    fi
    if [ "$LED_REQ_FAILS" -ne 0 ] || [ "$LED_REPORTED_FAIL" != "0" ] || [ "$proc_rc" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 必需用例判FAIL=$LED_REQ_FAILS 自报fail=$LED_REPORTED_FAIL proc_rc=$proc_rc")
        note "  检出失败: 有用例判 FAIL"
        detected_fail=1
        remember 1
        return 0
    fi
    matrix+=("$name | OK 必需用例=$(printf '%s\n' $LED_REQ | grep -c .) 全部PASS 自报fail=0 proc_rc=0 (输出 $(file_bytes "$out") 字节)")
    return 0
}

# ================= 反向验证(两项, 只改仓外一次性源码副本) =================
# 判定纪律: 注入锚点必须**恰好命中一次**; 副本编译失败/进程崩溃=INFRA-FAIL, 不算检出;
# 只有"链跑起来了但指定业务失败身份没出现"才算 NOT-DETECTED。干净对照由上面的正常变体提供。
inject_python() {           # inject_python <file> <old> <new> <marker>
    "$PY" - "$1" "$2" "$3" "$4" <<'PYEOF'
import io, sys
path, old, new, marker = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
t = io.open(path, encoding='utf-8', newline='').read()
n = t.count(old)
if n != 1:
    sys.stderr.write('ANCHOR-COUNT=%d (必须恰好 1 次)\n' % n)
    sys.exit(3)
io.open(path, 'w', encoding='utf-8', newline='').write(t.replace(old, new, 1))
sys.stderr.write('INJECTED %s\n' % marker)
PYEOF
}

reverse_case() {            # reverse_case <name> <variant> <target-file> <old> <new> <ids...>
    local name="$1" variant="$2" target="$3" old_txt="$4" new_txt="$5"; shift 5
    local ids="$*" work exe brc rc hit=0 miss=0 id extra out r extras nextra

    work="$OUT_DIR/reverse/$name"
    rm -rf "$work"
    mkdir -p "$work" || { note "  $name: 无法建副本目录"; remember 4; return; }
    cp -r "$SRC_APP" "$work/app" || { note "  $name: 源码副本失败"; remember 4; return; }
    if ! inject_python "$work/app/$target" "$old_txt" "$new_txt" "$name"; then
        note "  $name: 注入未落地或锚点不唯一 => 判基础设施故障, 不算检出"
        matrix+=("$name | INFRA-FAIL 注入未落地/锚点不唯一"); remember 4; return
    fi
    if ! grep -q "NEGATIVE-INJECTION" "$work/app/$target"; then
        note "  $name: 副本里缺少注入标记"
        matrix+=("$name | INFRA-FAIL 无注入标记"); remember 4; return
    fi
    exe="$work/sd.exe"; out="$work/out.txt"
    extra="$(variant_flags "$variant")"
    # shellcheck disable=SC2086
    "$GCC" $FLAGS $extra -I "$SHIM_DIR" -I "$work/app" "$SD_MAIN" -o "$exe" \
        > "$work/build.txt" 2>&1
    brc=$?
    if [ "$brc" -ne 0 ]; then
        tail -15 "$work/build.txt"
        note "  $name: 被注入副本编译失败 rc=$brc => 不算检出"
        matrix+=("$name | INFRA-FAIL 副本编译失败 rc=$brc"); remember 4; return
    fi
    "$exe" > "$out" 2>&1
    rc=$?
    EXECUTED+=("reverse:$name")

    # SD-NEW-02: 夹具的正常失败合同是 rc=1; 其它非零退出(信号/异常终止)判基础设施故障
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
        note "  $name: 子进程 rc=$rc 不是合同的正常业务失败 1(异常终止/信号) => 基础设施故障, 不算检出"
        matrix+=("$name | INFRA-FAIL 非合同退出码 rc=$rc"); remember 4; return
    fi
    if [ "$rc" -eq 0 ]; then
        note "  $name: 注入后链仍然通过 => 没检出"
        matrix+=("$name | NOT-DETECTED 注入后 proc_rc=0"); detected_fail=1; remember 1; return
    fi
    if ! validate_ledger "$out" "$variant"; then
        note "  $name: 异常运行的台账不完整/不自洽 => 基础设施故障, 不算检出"
        matrix+=("$name | INFRA-FAIL 台账合同不成立 (rc=$rc)"); remember 4; return
    fi

    for id in $ids; do
        if grep -q "^CASE|$id|FAIL|" "$out"; then hit=$((hit+1)); note "  命中期望失败身份: $id"
        else miss=$((miss+1)); note "  未命中期望失败身份: $id"; fi
    done
    if [ "$miss" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 命中=$hit 缺失=$miss proc_rc=$rc")
        detected_fail=1; remember 1; return
    fi
    # 额外失败身份**如实列出但不改变判定**: 复审 §6.2.4 明确 R1 附带 SD05c 失败是当前已知
    # 结果, 不能误当基础设施故障; 检出的判据是"构建成功 + rc=1 + 完整自洽台账 + 期望身份成立"。
    extras=""; nextra=0
    for r in $LED_REQ; do
        if grep -q "^CASE|$r|FAIL|" "$out"; then
            case " $ids " in *" $r "*) : ;; *) extras="$extras $r"; nextra=$((nextra+1)) ;; esac
        fi
    done
    if [ "$nextra" -ne 0 ]; then
        note "  额外失败身份(登记但不改变判定):$extras"
    fi
    matrix+=("$name | META-OK 命中 $hit 个业务失败身份 (variant=$variant proc_rc=$rc 必需用例=$(printf '%s\n' $LED_REQ | grep -c .) 实际FAIL行=$LED_FAILN 额外失败=$nextra)")
}

if [ "$REVERSE" = "1" ]; then
    note ""
    note "=== 反向验证(2 项, 仓外一次性副本; 本轮未与 ONLY 组合) ==="
    # R1: 聚合有效性退回"必须四路均解析/有效" —— OFF 变体必须命中 SD01
    reverse_case R1_aggregate_requires_all_four off_target sensor_service.c \
'        f->valid_safety = (f->sai_active != 0 &&
                           (valid & f->sai_active) == f->sai_active) ? 1 : 0;' \
'        f->valid_safety = ((valid & SAI_MASK_ALL) == SAI_MASK_ALL) ? 1 : 0; /* NEGATIVE-INJECTION R1 */' \
        SD01-off-masks-limits-never-touched
    # R2: 黑匣子 valid 掩码打包改成恒等 active —— 必须命中 SD07 / SD07b
    reverse_case R2_valid_mask_equals_active off_target blackbox.c \
'                    BBFL_PACK(f->sai_valid,  BBFL_SHIFT_VALID)     |' \
'                    BBFL_PACK(f->sai_active, BBFL_SHIFT_VALID)     | /* NEGATIVE-INJECTION R2 */' \
        SD07-same-source-frame-propagates SD07b-no-recompute-at-write-time \
        SD10b-dump-shows-version-and-per-channel-readability

    note ""
    note "=== 反向验证汇总 ==="
    for m in "${matrix[@]}"; do note "$m"; done
    note "实际执行集合=${EXECUTED[*]:-<无>}"
    {
        echo "SD_REVERSE_RC=$overall"
        echo "SD_EXECUTED=${EXECUTED[*]:-<none>}"
        for m in "${matrix[@]}"; do echo "$m"; done
    } > "$OUT_DIR/sd_reverse_status.txt"
    if [ "$overall" -eq 0 ]; then
        note "SD REVERSE RESULT: PASS —— 两项注入均以合同退出码 rc=1 命中指定业务失败身份(构建成功且台账完整自洽)"
    else
        note "SD REVERSE RESULT: FAIL (rc=$overall; 1=未检出, 4=基础设施故障/非合同退出/台账不成立)"
    fi
    exit "$overall"
fi

for v in $VARIANTS; do
    case " ${SELECTED[*]} " in
        *" $v "*) : ;;
        *) continue ;;
    esac
    note ""
    note "=== 变体 $v ==="
    build_variant "$v" "$(variant_flags "$v")"
    [ $? = "2" ] && continue
    run_variant "$v"
done

note ""
note "=== 语义链矩阵 ==="
for m in "${matrix[@]}"; do note "$m"; done

# 实际选择 vs 实际执行: 被选择却没跑完的显式 NOT_RUN, 不为未执行侧背书
for v in $SELECTED; do
    case " ${EXECUTED[*]-} " in *" $v "*) : ;; *) SELECTED_NOT_RUN+=("$v") ;; esac
done
note ""
note "选择集合=${SELECTED[*]} (模式=$SELECT_MODE)"
note "实际执行集合=${EXECUTED[*]:-<无>}"
for v in ${SELECTED_NOT_RUN[@]+"${SELECTED_NOT_RUN[@]}"}; do note "NOT_RUN: $v (被选择但未完成执行)"; done
for v in $KNOWN_VARIANTS; do
    case " ${SELECTED[*]} " in
        *" $v "*) : ;;
        *) note "NOT_RUN: $v (未被 ONLY 选择)" ;;
    esac
done

{
    echo "SD_RUNNER_RC=$overall"
    echo "SD_DETECTED_FAIL=$detected_fail"
    echo "SD_SELECT_MODE=$SELECT_MODE"
    echo "SD_SELECTED=${SELECTED[*]}"
    echo "SD_EXECUTED=${EXECUTED[*]:-<none>}"
    echo "SD_SELECTED_NOT_RUN=${SELECTED_NOT_RUN[*]-}"
    for m in "${matrix[@]}"; do echo "$m"; done
} > "$OUT_DIR/sd_status.txt"

note ""
if [ "$overall" -eq 0 ] && [ "${#SELECTED_NOT_RUN[@]}" -eq 0 ] && [ "${#EXECUTED[@]}" -gt 0 ]; then
    if [ "$SELECT_MODE" = "default" ]; then
        note "SD RUNNER RESULT: PASS (rc=0) —— 两个变体的必需用例全部真执行且判 PASS"
    else
        note "SD RUNNER RESULT: PASS (rc=0) —— 仅对实际执行集合(${EXECUTED[*]})声明通过; 未选择侧见上方 NOT_RUN"
    fi
else
    note "SD RUNNER RESULT: FAIL (rc=$overall; 1=检出失败, 4=基础设施故障/台账不成立/被选择却未执行) 详见 $OUT_DIR"
fi
exit "$overall"
