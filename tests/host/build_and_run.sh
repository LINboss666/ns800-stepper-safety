#!/usr/bin/env bash
# build_and_run.sh - 主机测试 runner（在 PC 上编译并执行**真实**固件源文件）
#
# 出口规则（F2d-R1 R01 + F2d-R2 执行证据 + 本轮不变）:
#   1) 每个编译步骤与每个测试进程的真实 rc 一律原样保存并打印;
#   2) 编译失败 => 先删旧二进制, 该变体**不得运行**(防止拿旧 exe 蒙混);
#   3) 两个变体都跑完再统一汇总, 任一必需步骤失败 => 脚本最终非零;
#   4) 内层 rc 与外层 rc 分别打印;
#   5) 被跳过的变体写 NOT_RUN, 绝不留 0(调用方据此区分"跑过且通过"与"根本没跑");
#   6) 每次真正启动测试进程前后落 v?_executed.marker, 并在回执里给 EXECUTED/EXE/OUT_BYTES。
#
# 用法(全部可覆盖, 不写死盘符):
#   bash tests/host/build_and_run.sh
#   SRC_APP=<快照目录> OUT_DIR=<输出目录> bash tests/host/build_and_run.sh
#   V1_EXTRA="-DHOST_INJECT_VARIANT_FAIL=1" bash tests/host/build_and_run.sh
#   RUN_V2=0 / GCC=<编译器或桩脚本> bash tests/host/build_and_run.sh
# 退出码: 0=全部通过; 非 0=第一个失败步骤的 rc(详见 RUNNER SUMMARY 与 runner_status.txt)

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
OUT_DIR="${OUT_DIR:-$HERE/_out}"
HOST_SRC="${HOST_SRC:-$HERE/host_main.c}"
SHIM_DIR="${SHIM_DIR:-$HERE/shim}"          # 元负例把 runner 副本放到别处时仍可显式指定
V1_EXTRA="${V1_EXTRA:-}"
V2_EXTRA="${V2_EXTRA:-}"
RUN_V1="${RUN_V1:-1}"
RUN_V2="${RUN_V2:-1}"
FLAGS="-std=gnu11 -Wall -Wextra -Wno-unused-function"
# -Wno-unused-function: 夹具把 MSH_CMD_EXPORT 定义成空宏, 固件里的静态命令函数在主机侧
# 没有调用点 -> 这类告警与固件正确性无关。目标平台权威构建仍是 Keil Rebuild All(要求 0/0),
# 本脚本只负责行为执行。

overall=0
note() { printf '%s\n' "$*"; }
remember() { case "$1" in ''|*[!0-9]*) return 0;; esac
             if [ "$overall" -eq 0 ] && [ "$1" -ne 0 ]; then overall="$1"; fi; }
file_bytes() { if [ -f "$1" ]; then wc -c <"$1" | tr -d ' '; else echo ABSENT; fi; }

GCC_RESOLVED="$(env_detect_gcc || true)"
if [ -z "$GCC_RESOLVED" ] || [ ! -x "$GCC_RESOLVED" ]; then
    note "FATAL: 未找到可用主机编译器(设 GCC=<路径> 或把 gcc 放进 PATH)"; exit 127
fi
GCC="$GCC_RESOLVED"
[ -f "$HOST_SRC" ] || { note "FATAL: 夹具源不存在: $HOST_SRC"; exit 127; }
[ -d "$SRC_APP" ]  || { note "FATAL: 源码快照目录不存在: $SRC_APP"; exit 127; }
mkdir -p "$OUT_DIR"
note "runner   : $HOST_SRC"
note "SRC_APP  : $SRC_APP"
note "OUT_DIR  : $OUT_DIR"
note "compiler : $("$GCC" --version | head -1)"

run_variant() {
    local tag="$1" tag_lc extra exe marker brc rrc run_flag
    tag_lc="$(printf '%s' "$tag" | tr 'A-Z' 'a-z')"
    extra="V${tag#V}_EXTRA"          ; extra="${!extra}"
    eval "run_flag=\${RUN_$tag}"
    exe="$OUT_DIR/host_${tag_lc}.exe"; marker="$OUT_DIR/${tag_lc}_executed.marker"
    rm -f "$marker"

    if [ "$run_flag" != "1" ]; then
        note ""; note "=== $tag 跳过 (RUN_$tag=$run_flag) => 记 NOT_RUN, 不算通过 ==="
        eval "rc_${tag_lc}_build=NOT_RUN"; eval "rc_${tag_lc}_run=NOT_RUN"
        eval "state_${tag_lc}=NOT_RUN"
        return 0
    fi

    note ""
    if [ "$tag" = "V1" ]; then note "=== V1 (发布配置: 三个安全宏 = FALSE) ==="
    else note "=== V2 (HOST_GATE_OVERRIDE + 三宏按 TRUE; 仅主机覆盖路径, 不进 Keil/正式固件) ==="; fi
    rm -f "$exe"
    if [ "$tag" = "V1" ]; then
        # shellcheck disable=SC2086
        "$GCC" $FLAGS $extra -I "$SHIM_DIR" -I "$SRC_APP" "$HOST_SRC" -o "$exe" \
            > "$OUT_DIR/${tag_lc}_build.txt" 2>&1
    else
        # shellcheck disable=SC2086
        "$GCC" $FLAGS -DHOST_GATE_OVERRIDE \
              -DMOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_TRUE \
              -DSTEP_DIAG_ISOLATED_ENV_CONFIRMED=RT_TRUE \
              -DTMC_DRIVE_CONFIG_HW_VALIDATED=RT_TRUE \
              $extra -I "$SHIM_DIR" -I "$SRC_APP" "$HOST_SRC" -o "$exe" \
              > "$OUT_DIR/${tag_lc}_build.txt" 2>&1
    fi
    brc=$?
    eval "rc_${tag_lc}_build=$brc"
    note "$tag build rc = $brc  (log: ${tag_lc}_build.txt)"
    if [ "$brc" -ne 0 ]; then
        cat "$OUT_DIR/${tag_lc}_build.txt"
        note "$tag 编译失败 => 不运行二进制(旧产物已删除)"
        eval "rc_${tag_lc}_run=127"; eval "state_${tag_lc}=COMPILE_FAILED"
        remember "$brc"
        return 0
    fi
    printf 'LAUNCHED exe=%s\n' "$exe" > "$marker"
    "$exe" > "$OUT_DIR/${tag_lc}_out.txt" 2>&1
    rrc=$?
    printf 'DONE rc=%s exe=%s out_bytes=%s\n' "$rrc" "$exe" \
        "$(file_bytes "$OUT_DIR/${tag_lc}_out.txt")" > "$marker"
    eval "rc_${tag_lc}_run=$rrc"; eval "state_${tag_lc}=RAN"
    note "$tag run   rc = $rrc  (执行证据: ${tag_lc}_executed.marker)"
    tail -6 "$OUT_DIR/${tag_lc}_out.txt"
    remember "$rrc"
}

run_variant V1
run_variant V2

note ""
note "=== RUNNER SUMMARY ==="
note "  V1 build=$rc_v1_build run=$rc_v1_run state=$state_v1   " \
     "V2 build=$rc_v2_build run=$rc_v2_run state=$state_v2"
if [ "$overall" -eq 0 ]; then
    note "  RUNNER RESULT: PASS (rc=0)"
else
    note "  RUNNER RESULT: FAIL (rc=$overall; 任一必需步骤非零即失败)"
fi

{
  echo "V1_BUILD_RC=$rc_v1_build"
  echo "V1_RUN_RC=$rc_v1_run"
  echo "V1_STATE=$state_v1"
  echo "V1_EXECUTED=$([ -f "$OUT_DIR/v1_executed.marker" ] && echo 1 || echo 0)"
  echo "V1_EXE=$([ -f "$OUT_DIR/host_v1.exe" ] && echo PRESENT || echo ABSENT)"
  echo "V1_OUT_BYTES=$(file_bytes "$OUT_DIR/v1_out.txt")"
  echo "V2_BUILD_RC=$rc_v2_build"
  echo "V2_RUN_RC=$rc_v2_run"
  echo "V2_STATE=$state_v2"
  echo "V2_EXECUTED=$([ -f "$OUT_DIR/v2_executed.marker" ] && echo 1 || echo 0)"
  echo "V2_EXE=$([ -f "$OUT_DIR/host_v2.exe" ] && echo PRESENT || echo ABSENT)"
  echo "V2_OUT_BYTES=$(file_bytes "$OUT_DIR/v2_out.txt")"
  echo "RUNNER_RC=$overall"
  echo "REPO_ROOT=$REPO_ROOT"
  echo "SRC_APP=$SRC_APP"
  echo "HOST_SRC=$HOST_SRC"
  echo "SHIM_DIR=$SHIM_DIR"
  echo "V1_EXTRA=$V1_EXTRA"
  echo "V2_EXTRA=$V2_EXTRA"
  echo "GCC=$GCC"
} > "$OUT_DIR/runner_status.txt"

exit "$overall"
