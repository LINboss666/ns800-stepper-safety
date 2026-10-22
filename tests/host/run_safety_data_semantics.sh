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
# 用法: bash tests/host/run_safety_data_semantics.sh
#       SD_OUT_DIR=<仓外目录> SRC_APP=<快照目录> GCC=<编译器> ONLY=off_target REVERSE=1 ...
# 退出码: 0=通过; 1=检出失败; 4=基础设施故障; 127=环境缺失

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

VARIANTS="off_target on_target"

overall=0
detected_fail=0
declare -a matrix=()
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
    local proc_rc reported_fail rc_line req missing dup badverdict unreg r id cnt passline

    "$exe" > "$out" 2>&1
    proc_rc=$?

    rc_line="$(grep -E '^SUMMARY\|' "$out" | tail -1)"
    reported_fail="$(printf '%s' "$rc_line" | sed -n 's/.*fail=\([0-9]*\).*/\1/p')"
    [ -n "$reported_fail" ] || reported_fail="MISSING"

    req="$(required_of "$name")"
    missing=0; dup=0; badverdict=0
    for r in $req; do
        cnt="$(grep -c "^CASE|$r|" "$out")"
        passline="$(grep -c "^CASE|$r|PASS|" "$out")"
        if [ "$cnt" -eq 0 ]; then missing=$((missing+1)); note "  缺席: $r"
        elif [ "$cnt" -gt 1 ]; then dup=$((dup+1)); note "  重复执行: $r ($cnt 次)"
        elif [ "$passline" -ne 1 ]; then badverdict=$((badverdict+1))
             note "  判 FAIL: $(grep "^CASE|$r|" "$out" | head -1)"
        fi
    done
    # 反向检查: 输出里出现的 id 必须都在台账内(防止偷偷加没登记的用例)
    unreg=0
    for id in $(grep -E '^CASE\|' "$out" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u); do
        case " $req " in *" $id "*) : ;; *) unreg=$((unreg+1)); note "  未登记用例: $id" ;; esac
    done

    if [ "$missing" -ne 0 ] || [ "$dup" -ne 0 ] || [ "$unreg" -ne 0 ]; then
        matrix+=("$name | INFRA-FAIL 缺席=$missing 重复=$dup 未登记=$unreg (proc_rc=$proc_rc)")
        note "  基础设施故障: 必需用例未全部/唯一执行, 或有未登记用例"
        remember 4
        return 0
    fi
    if [ "$badverdict" -ne 0 ] || [ "$reported_fail" != "0" ] || [ "$proc_rc" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 判FAIL=$badverdict 自报fail=$reported_fail proc_rc=$proc_rc")
        note "  检出失败: 有用例判 FAIL"
        detected_fail=1
        remember 1
        return 0
    fi
    matrix+=("$name | OK 必需用例=$(printf '%s\n' $req | grep -c .) 全部PASS 自报fail=0 proc_rc=0 (输出 $(file_bytes "$out") 字节)")
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
    local ids="$*" work exe brc rc hit=0 miss=0 id extra out

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
    if [ "$rc" -eq 0 ]; then
        note "  $name: 注入后链仍然通过 => 没检出"
        matrix+=("$name | NOT-DETECTED 注入后 proc_rc=0"); detected_fail=1; remember 1; return
    fi
    for id in $ids; do
        if grep -q "^CASE|$id|FAIL|" "$out"; then hit=$((hit+1)); note "  命中期望失败身份: $id"
        else miss=$((miss+1)); note "  未命中期望失败身份: $id"; fi
    done
    if [ "$miss" -ne 0 ]; then
        matrix+=("$name | NOT-DETECTED 命中=$hit 缺失=$miss proc_rc=$rc")
        detected_fail=1; remember 1; return
    fi
    matrix+=("$name | META-OK 命中 $hit 个业务失败身份 (variant=$variant proc_rc=$rc)")
}

REVERSE="${REVERSE:-0}"

if [ "$REVERSE" = "1" ]; then
    note ""
    note "=== 反向验证(2 项, 仓外一次性副本) ==="
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
    {
        echo "SD_REVERSE_RC=$overall"
        for m in "${matrix[@]}"; do echo "$m"; done
    } > "$OUT_DIR/sd_reverse_status.txt"
    if [ "$overall" -eq 0 ]; then
        note "SD REVERSE RESULT: PASS —— 两项注入均命中指定业务失败身份(不是编译失败/崩溃)"
    else
        note "SD REVERSE RESULT: FAIL (rc=$overall)"
    fi
    exit "$overall"
fi

for v in $VARIANTS; do
    if ! want_variant "$v"; then NOT_RAN+=("$v"); continue; fi
    note ""
    note "=== 变体 $v ==="
    build_variant "$v" "$(variant_flags "$v")"
    [ $? = "2" ] && continue
    run_variant "$v"
done

note ""
note "=== 语义链矩阵 ==="
for m in "${matrix[@]}"; do note "$m"; done
if [ ${#NOT_RAN[@]} -gt 0 ]; then note "未执行变体(ONLY 选择): ${NOT_RAN[*]}"; fi

{
    echo "SD_RUNNER_RC=$overall"
    echo "SD_DETECTED_FAIL=$detected_fail"
    for m in "${matrix[@]}"; do echo "$m"; done
} > "$OUT_DIR/sd_status.txt"

note ""
if [ "$overall" -eq 0 ]; then
    note "SD RUNNER RESULT: PASS (rc=0) —— 两个变体的必需用例全部真执行且判 PASS"
else
    note "SD RUNNER RESULT: FAIL (rc=$overall; 1=检出失败, 4=基础设施故障) 详见 $OUT_DIR"
fi
exit "$overall"
