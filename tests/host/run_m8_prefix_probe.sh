#!/usr/bin/env bash
# run_m8_prefix_probe.sh - 在**精确 f1aa2e8 的 applications 副本**上复现 M8 的三项修前观察
#
# 为什么要有这一步(任务书 §8): 修 Bug 的顺序是"已知正常版本/路径 → 同输入复现 →
# 相关 diff → 最小改动 → 直接复验 → 针对性回归"。M8 是新增能力, 它的三项前提
# (没有源年龄判据 / 没有单源严重出口 / severe 边沿先打印后保护) 必须在开工前用
# 同一套输入形状**复现过**, 否则后面的"修好了"没有对照物。
#
# 基线取源: **只用 git blob**(不碰工作树, 不受 core.autocrlf 影响):
#   git archive <BASE_REF> <applications 相对路径> | tar -x
# 副本一律落在仓库**外**(require_outside_repo), 且只删除带本工具哨兵的目录。
#
# 用法: bash tests/host/run_m8_prefix_probe.sh
#       M8_BASE_REF=f1aa2e8...  REL_OUT_DIR=<仓外目录>  GCC=<cc>  PY=<python>
# 退出码: 0=三项观察全部按预期复现 1=未复现(判据不成立) 4=基础设施故障 127=环境缺失

set -u -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
BASE_REF="${M8_BASE_REF:-f1aa2e832d536593641cea44ea890fb8ce9558b9}"
APP_REL="$(env_app_rel)"
SHIM_DIR="${SHIM_DIR:-$HERE/shim_rel}"
PROBE="${PROBE:-$HERE/rel_pre_probe.c}"
OUT_BASE="${REL_OUT_DIR-$(env_work_root)/m8-prefix}"
SENTINEL="NS800-HOSTCHAIN-WORK"
CASES="M8-PRE-1-baseline-has-no-source-age
M8-PRE-2-baseline-has-no-single-source-severe
M8-PRE-3-baseline-print-before-protect"
CASES="$(printf '%s' "$CASES" | tr '\n' ' ')"

note() { printf '%s\n' "$*"; }

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
WORK_RAW="$OUT_BASE/$RUN_ID"
OUT_DIR="$(require_outside_repo "$WORK_RAW" "REL_OUT_DIR")" || {
    note "FATAL: 工作目录被拒绝, 未创建任何目录"; exit 4; }
[ -e "$OUT_DIR" ] || mkdir -p "$OUT_DIR" || exit 4
: > "$OUT_DIR/.$SENTINEL" || exit 4

GCC="$(env_detect_gcc || true)"
[ -n "$GCC" ] || { note "FATAL: 无主机编译器"; exit 127; }
[ -f "$PROBE" ] || { note "FATAL: $PROBE 缺失"; exit 127; }

# ---------- 基线身份: 先证明 ref 存在且是提交 ----------
if ! git -C "$REPO_ROOT" cat-file -e "${BASE_REF}^{commit}" 2>/dev/null; then
    note "FATAL: 基线 ref 不存在: $BASE_REF"; exit 4
fi
BASE_SHA="$(git -C "$REPO_ROOT" rev-parse "${BASE_REF}^{commit}")"
note "base_commit=$BASE_SHA"
note "app_rel=$APP_REL"

BASE_COPY="$OUT_DIR/baseline_app"
mkdir -p "$BASE_COPY" || exit 4
# tar 会带上完整相对路径, 解到副本根下再指到 applications
if ! git -C "$REPO_ROOT" archive --format=tar "$BASE_SHA" "$APP_REL" | tar -x -C "$BASE_COPY"; then
    note "FATAL: git archive/tar 解包失败"; exit 4
fi
BASE_APP="$BASE_COPY/$APP_REL"
[ -f "$BASE_APP/diagnosis.c" ] || { note "FATAL: 副本里没有 diagnosis.c: $BASE_APP"; exit 4; }
[ -f "$BASE_APP/diagnosis.h" ] || { note "FATAL: 副本里没有 diagnosis.h"; exit 4; }
if [ -f "$BASE_APP/diag_relative.c" ]; then
    note "FATAL: 基线副本里竟然有 diag_relative.c —— 这不是 f1aa2e8 之前的树"
    exit 4
fi
note "baseline_diag_blob=$(git -C "$REPO_ROOT" rev-parse "$BASE_SHA:$APP_REL/diagnosis.c")"
note "baseline_frame_hdr_blob=$(git -C "$REPO_ROOT" rev-parse "$BASE_SHA:$APP_REL/sensor_service.h")"
# 记录副本关键文件的字节数(证据可复核)
wc -c < "$BASE_APP/diagnosis.c" > "$OUT_DIR/baseline_diagnosis_bytes.txt"
note "baseline_diagnosis_bytes=$(cat "$OUT_DIR/baseline_diagnosis_bytes.txt")"

# ---------- 编译 + 运行 ----------
FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter"
EXE="$OUT_DIR/pre_probe.exe"; OUT="$OUT_DIR/out.txt"
"$GCC" $FLAGS -I "$SHIM_DIR" -I "$BASE_APP" "$PROBE" -o "$EXE" > "$OUT_DIR/build.txt" 2>&1
brc=$?
if [ "$brc" -ne 0 ]; then
    tail -40 "$OUT_DIR/build.txt"
    note "FATAL: 基线探针编译失败 rc=$brc"; exit 4
fi
note "build_rc=$brc warnings=$(grep -c 'warning:' "$OUT_DIR/build.txt" || true)"
"$EXE" baseline > "$OUT" 2>&1
prc=$?
note "proc_rc=$prc"
cat "$OUT"

# ---------- 台账判定 ----------
n=$(grep -cE '^CASE\|' "$OUT")
p=$(grep -cE '^CASE\|[^|]*\|PASS\|' "$OUT")
f=$(grep -cE '^CASE\|[^|]*\|FAIL\|' "$OUT")
s=$(grep -cE '^SUMMARY\|' "$OUT")
rc=0
if [ "$s" -ne 1 ]; then note "RESULT: INFRA-FAIL SUMMARY=$s(必须恰好 1)"; exit 4; fi
if [ "$n" -ne 3 ]; then note "RESULT: INFRA-FAIL 台账用例数=$n(必须 3)"; exit 4; fi
miss=0
for id in $CASES; do
    if ! grep -q "^CASE|$id|PASS|" "$OUT"; then note "  未通过/缺席: $id"; miss=$((miss+1)); fi
done
if [ "$miss" -ne 0 ]; then
    note "M8 修前观察复现: FAIL(未复现=$miss, 自报 fail=$f proc_rc=$prc)"
    exit 1
fi
if [ "$prc" -ne 0 ] || [ "$f" -ne 0 ]; then
    note "M8 修前观察复现: FAIL(全部命中期望但进程 rc=$prc 自报 fail=$f)"
    exit 1
fi
note "M8 修前观察复现: PASS(三项都在精确基线 $BASE_SHA 上按预期复现; pass=$p/$n)"
exit "$rc"
