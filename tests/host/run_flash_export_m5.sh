#!/usr/bin/env bash
# run_flash_export_m5.sh - M5 flash_dump 导出边界链 (2026-10-29)
#
# 真编译 applications/ns_flash_shell.c; RT 控制台桩按目标 128 字节语义建模:
#   rt_kprintf 长行截断并把结尾 NUL 送进流; rt_kputs 完整输出。
#
# 运行:
#   bash tests/host/run_flash_export_m5.sh
#      normal   FE01..FE05 全部 PASS + 归档判读 ACCEPT/REJECT 负控 + 判读器自测
#      cap      生产 line[] 收缩到 64 的**仓外副本**构建, FE06 证明容量不足时
#               只报具名错误、不输出半条记录
#   REVERSE=1 bash tests/host/run_flash_export_m5.sh
#      额外把 rt_kputs(line) 退回单次受限长 rt_kprintf, 要求 FE02 具名 FAIL,
#      且归档判读对截断批次给出 CLEAR_ALLOWED=NO
#
# 退出码: 0=通过; 1=检出失败; 4=基础设施故障; 127=环境缺失

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_flash}"
FLASH_MAIN="${FLASH_MAIN:-$HERE/flash_main.c}"
CHECKER="${CHECKER:-$HERE/m5_log_archive_check.py}"
PY="${PY:-python}"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${FLASH_OUT_DIR-${FLASH_WORK-$(env_work_root)/m5-flash}/$RUN_ID}"
if ! OUT_DIR="$(require_outside_repo "$OUT_DIR" "FLASH_OUT_DIR")"; then
    echo "FATAL: FLASH_OUT_DIR 被工作目录守卫拒绝(未创建目录, 未重定向, 未编译)" >&2
    exit 4
fi
[ -f "$FLASH_MAIN" ] || { echo "FATAL: 夹具源不存在: $FLASH_MAIN" >&2; exit 127; }
[ -d "$SHIM_DIR" ]   || { echo "FATAL: 兼容层目录不存在: $SHIM_DIR" >&2; exit 127; }
[ -d "$SRC_APP" ]    || { echo "FATAL: 源码目录不存在: $SRC_APP" >&2; exit 127; }
[ -f "$CHECKER" ]    || { echo "FATAL: 归档判读器不存在: $CHECKER" >&2; exit 127; }
if [ -e "$OUT_DIR" ] && [ ! -f "$OUT_DIR/.ns800_host_work" ]; then
    echo "FATAL: 输出目录已存在但没有本工具的哨兵: $OUT_DIR" >&2
    exit 4
fi
mkdir -p "$OUT_DIR" || { echo "FATAL: 无法创建 OUT_DIR" >&2; exit 4; }
printf 'ns800-host-chain m5-flash runner work dir (run_id=%s)\n' "$RUN_ID" \
    > "$OUT_DIR/.ns800_host_work" || exit 4

GCC_RESOLVED="$(env_detect_gcc || true)"
[ -n "$GCC_RESOLVED" ] && [ -x "$GCC_RESOLVED" ] || {
    echo "FATAL: 未找到可用主机编译器(设 GCC=<路径>)" >&2; exit 127; }
GCC="$GCC_RESOLVED"
FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function"
echo "###### M5 flash_dump 导出边界链 (基线 $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo UNKNOWN)) ######"
echo "run_id=$RUN_ID"
echo "OUT_DIR=$OUT_DIR"
echo "compiler=$("$GCC" --version | head -1)"

overall=0
note() { printf '%s\n' "$*"; }
remember() { case "$1" in ''|*[!0-9]*) return 0 ;; esac
             if [ "$overall" -eq 0 ] && [ "$1" -ne 0 ]; then overall="$1"; fi; }

CASE_IDS="FE01-header_and_normal_row FE02-extreme_values_full_roundtrip FE03-invalid_args_zero_reads FE04-last_slot_boundary_accepted FE05-read_error_no_partial_row"

# ---- 台账核对: 每个必需 id 恰好一次且 PASS, SUMMARY 自洽, 无未登记 id ----
validate_ledger() {
    local f="$1" ids="$2" id cnt pass n=0 lines sum_cases sum_pass sum_fail sum_lines bad=0
    lines="$(grep -cE '^CASE\|' "$f" || true)"
    sum_lines="$(grep -cE '^SUMMARY\|' "$f" || true)"
    for id in $ids; do
        n=$((n+1))
        cnt="$(grep -c "^CASE|$id|" "$f" || true)"
        pass="$(grep -c "^CASE|$id|PASS|" "$f" || true)"
        if [ "$cnt" -ne 1 ] || [ "$pass" -ne 1 ]; then
            note "  台账不符: $id 出现 $cnt 次, PASS $pass 次"
            bad=1
        fi
    done
    if [ "$sum_lines" -ne 1 ]; then
        note "  SUMMARY 行数=$sum_lines (必须恰好 1)"; bad=1
    else
        sum_cases="$(sed -n 's/^SUMMARY|cases=\([0-9]*\).*/\1/p' "$f")"
        sum_pass="$(sed -n 's/.*pass=\([0-9]*\).*/\1/p' "$f")"
        sum_fail="$(sed -n 's/.*fail=\([0-9]*\).*/\1/p' "$f")"
        if [ "$sum_cases" != "$lines" ] || [ $((sum_pass + sum_fail)) -ne "$lines" ]; then
            note "  SUMMARY(cases=$sum_cases pass=$sum_pass fail=$sum_fail) 与 CASE 行数=$lines 不符"
            bad=1
        fi
    fi
    local unreg
    unreg="$(grep -E '^CASE\|' "$f" | sed 's/^CASE|\([^|]*\)|.*/\1/' | sort -u | while read -r id; do
                 printf '%s\n' $ids | grep -Fx -- "$id" >/dev/null || echo "$id"
             done | grep -c . || true)"
    if [ "$unreg" -ne 0 ]; then note "  存在未登记用例 id=$unreg"; bad=1; fi
    [ "$bad" -eq 0 ] && note "  台账自洽: $n 个必需 id 各 PASS 一次, 共 $lines 行"
    return "$bad"
}

build_variant() {   # build_variant <name> <inc1> [extra flags...]
    local name="$1" inc="$2"; shift 2
    "$GCC" $FLAGS "$@" -I "$inc" -I "$SHIM_DIR" -I "$SRC_APP" \
        "$FLASH_MAIN" -o "$OUT_DIR/$name.exe" > "$OUT_DIR/${name}_build.txt" 2>&1
    local rc=$?
    [ "$rc" -eq 0 ] && note "  $name 构建 OK"
    return "$rc"
}

# ============================ 1) 正常构建 ============================
note "=== normal: 真编译 ns_flash_shell.c ==="
if ! build_variant normal "$SRC_APP"; then
    note "FATAL: normal 构建失败, 见 $OUT_DIR/normal_build.txt"; exit 4
fi
"$OUT_DIR/normal.exe" > "$OUT_DIR/normal_out.txt" 2>&1
nrc=$?
note "  normal 真实进程 rc=$nrc 输出 $(wc -c < "$OUT_DIR/normal_out.txt") 字节"
if ! validate_ledger "$OUT_DIR/normal_out.txt" "$CASE_IDS"; then
    note "normal 台账不成立(基础设施故障)"; exit 4
fi
if [ "$nrc" -ne 0 ]; then
    note "normal 检出失败 (rc=$nrc)"; remember 1
else
    note "  normal: FE01..FE05 全部 PASS"
fi

# ============================ 2) 容量不足探针 ============================
note "=== cap: line[256]->line[64] 的仓外副本 (仅证明错误分支) ==="
CAP_SRC="$OUT_DIR/src_cap"
mkdir -p "$CAP_SRC"
cp "$SRC_APP/ns_flash_shell.c" "$CAP_SRC/ns_flash_shell.c" || exit 4
sed -i 's/char line\[256\];/char line[64];/' "$CAP_SRC/ns_flash_shell.c"
if [ "$(grep -c 'char line\[64\];' "$CAP_SRC/ns_flash_shell.c")" -ne 1 ]; then
    note "FATAL: cap 注入未命中(期望恰好 1 处)"; exit 4
fi
if ! build_variant cap "$CAP_SRC" -DFLASH_CAP_PROBE; then
    note "FATAL: cap 构建失败, 见 $OUT_DIR/cap_build.txt"; exit 4
fi
"$OUT_DIR/cap.exe" > "$OUT_DIR/cap_out.txt" 2>&1
crc=$?
note "  cap 真实进程 rc=$crc"
if ! validate_ledger "$OUT_DIR/cap_out.txt" "FE06-capacity_overflow_named_no_partial"; then
    note "cap 台账不成立(基础设施故障)"; exit 4
fi
if [ "$crc" -ne 0 ]; then
    note "cap 探针检出失败 (rc=$crc)"; remember 1
else
    note "  cap: 具名溢出错误、无半条数据"
fi

# ==================== 3) 归档判读: 接受与拒绝 ====================
note "=== archive: 判读器自测 + 真实导出字节接受/拒绝 ==="
if ! "$PY" "$CHECKER" --selftest > "$OUT_DIR/archive_selftest.txt" 2>&1; then
    note "FATAL: 归档判读器自测失败, 见 $OUT_DIR/archive_selftest.txt"; exit 4
fi
if ! grep -q '^SUMMARY|cases=19 pass=19 fail=0$' "$OUT_DIR/archive_selftest.txt"; then
    note "FATAL: 判读器自测台账不是 16/16"; exit 4
fi
note "  判读器自测 19/19 PASS"

"$OUT_DIR/normal.exe" emit > "$OUT_DIR/batch_0_4.raw" 2> "$OUT_DIR/emit_err.txt"
if [ $? -ne 0 ] || [ ! -s "$OUT_DIR/batch_0_4.raw" ]; then
    note "FATAL: 无法产出归档用导出字节"; exit 4
fi
if "$PY" "$CHECKER" --expected 4 --batch 0 4 "$OUT_DIR/batch_0_4.raw" \
       --csv "$OUT_DIR/roundtrip.csv" --json "$OUT_DIR/archive_meta.json" \
       > "$OUT_DIR/archive_accept.txt" 2>&1 &&
   grep -qx 'ARCHIVE=ACCEPT rows=4 batches=1' "$OUT_DIR/archive_accept.txt" &&
   grep -qx 'CLEAR_ALLOWED=YES' "$OUT_DIR/archive_accept.txt" &&
   cmp -s "$OUT_DIR/batch_0_4.raw" "$OUT_DIR/roundtrip.csv"; then
    note "  完整批次 ACCEPT, 重建 CSV 与导出字节逐字节一致"
else
    note "  完整批次未被接受或重建不一致 (rc=$?)"; remember 1
fi

# 缺槽负控: 删掉 slot 2 的数据行
grep -v '^2,' "$OUT_DIR/batch_0_4.raw" > "$OUT_DIR/batch_missing.raw"
if "$PY" "$CHECKER" --expected 4 --batch 0 4 "$OUT_DIR/batch_missing.raw" \
       > "$OUT_DIR/archive_reject.txt" 2>&1; then
    note "  缺槽负控未被拒绝"; remember 1
elif grep -q 'reason=MISSING_SLOT' "$OUT_DIR/archive_reject.txt" &&
     grep -qx 'CLEAR_ALLOWED=NO' "$OUT_DIR/archive_reject.txt"; then
    note "  缺槽批次 REJECT (MISSING_SLOT), CLEAR_ALLOWED=NO"
else
    note "  缺槽批次拒绝原因不符"; remember 1
fi

# ============================ 4) 反向 ============================
if [ "${REVERSE:-0}" = "1" ]; then
    note "=== reverse: rt_kputs(line) -> rt_kprintf(\"%s\", line) ==="
    REV_SRC="$OUT_DIR/src_rev"
    mkdir -p "$REV_SRC"
    cp "$SRC_APP/ns_flash_shell.c" "$REV_SRC/ns_flash_shell.c" || exit 4
    sed -i 's/rt_kputs(line);/rt_kprintf("%s", line);/' "$REV_SRC/ns_flash_shell.c"
    if [ "$(grep -c 'rt_kprintf("%s", line);' "$REV_SRC/ns_flash_shell.c")" -ne 1 ]; then
        note "FATAL: reverse 注入未命中(期望恰好 1 处)"; exit 4
    fi
    if ! build_variant rev "$REV_SRC"; then
        note "FATAL: reverse 构建失败"; exit 4
    fi
    "$OUT_DIR/rev.exe" > "$OUT_DIR/rev_out.txt" 2>&1
    rrc=$?
    note "  rev 真实进程 rc=$rrc"
    if [ "$rrc" -ne 1 ]; then
        note "  rev_long_console | NOT-DETECTED (合同 rc 应为 1, 实际 $rrc)"; remember 1
    elif ! grep -q '^CASE|FE02-extreme_values_full_roundtrip|FAIL|' "$OUT_DIR/rev_out.txt"; then
        note "  rev_long_console | NOT-DETECTED (FE02 没有判 FAIL)"; remember 1
    else
        rev_bad=0
        for id in FE01-header_and_normal_row FE03-invalid_args_zero_reads \
                  FE04-last_slot_boundary_accepted FE05-read_error_no_partial_row; do
            [ "$(grep -c "^CASE|$id|PASS|" "$OUT_DIR/rev_out.txt")" -eq 1 ] || rev_bad=1
        done
        if [ "$rev_bad" -ne 0 ] ||
           ! grep -q '^SUMMARY|cases=5 pass=4 fail=1$' "$OUT_DIR/rev_out.txt"; then
            note "  rev_long_console | INFRA-FAIL (仅 FE02 应失败, 台账不匹配)"; exit 4
        fi
        note "  rev_long_console | META-OK 命中 FE02-extreme_values_full_roundtrip"
    fi

    # 截断的导出一旦进入归档判读, 必须 CLEAR_ALLOWED=NO
    "$OUT_DIR/rev.exe" emit > "$OUT_DIR/batch_rev_truncated.raw" 2>/dev/null
    if "$PY" "$CHECKER" --expected 4 --batch 0 4 "$OUT_DIR/batch_rev_truncated.raw" \
           > "$OUT_DIR/archive_rev.txt" 2>&1; then
        note "  rev_archive_block | NOT-DETECTED (截断批次被接受)"; remember 1
    elif grep -qx 'CLEAR_ALLOWED=NO' "$OUT_DIR/archive_rev.txt"; then
        note "  rev_archive_block | META-OK $(grep '^ARCHIVE=REJECT' "$OUT_DIR/archive_rev.txt")"
    else
        note "  rev_archive_block | INFRA-FAIL (拒绝但未输出 CLEAR_ALLOWED=NO)"; exit 4
    fi
fi

note ""
if [ "$overall" -eq 0 ]; then
    note "M5 FLASH EXPORT RESULT: PASS (normal + cap + archive gate$([ "${REVERSE:-0}" = 1 ] && echo " + reverse"))"
else
    note "M5 FLASH EXPORT RESULT: FAIL (rc=$overall) 详见 $OUT_DIR"
fi
exit "$overall"
