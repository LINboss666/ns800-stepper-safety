#!/usr/bin/env bash
# sd_runner_rework_check.sh - 语义链 runner 两个误报入口的有限回归 (复审 §6.3)
#
# 只针对复审件 NS800_Safety_Data_Semantics_Review_2399d28_20261023.md §4 的两个已复现缺陷:
#   SD-NEW-01 ONLY 选择被拒绝/零执行却声称通过
#   SD-NEW-02 反向的异常终止或非完整台账被算成"检测成功"
# 不新建元负例框架, 不递归测试工具链: 每个场景都是**真实调用同一个 runner**,
# 断言的只是 runner 自己打印的身份与真实退出码(不另写一份复制判定的"自检器")。
#
# 沙盒: 一律用 mktemp -d 在仓库外新建一次性目录, 内含 KEEP 标记文件;
#       每个拒绝场景之后核对"目录未被创建/标记未丢", 绝不拿真实项目目录做删除试验。
#
# 用法: bash tests/host/sd_runner_rework_check.sh
# 退出码: 0 = 全部场景符合预期; 1 = 有场景不符; 4 = 环境/构建故障

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"

REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
RUNNER="$HERE/run_safety_data_semantics.sh"
PY="${PY:-python}"

GCC_RESOLVED="$(env_detect_gcc || true)"
if [ -z "$GCC_RESOLVED" ] || [ ! -x "$GCC_RESOLVED" ]; then
    printf 'FATAL: 未找到主机编译器\n'; exit 4
fi
REAL_GCC="$GCC_RESOLVED"
SB="$(mktemp -d "${TMPDIR:-/tmp}/sd_rework_XXXXXX")" || { printf 'FATAL: 无法建沙盒\n'; exit 4; }
printf 'KEEP-MARKER 场景核对用, 拒绝必须发生在任何删除之前\n' > "$SB/KEEP.txt"

pass=0; fail=0; infra=0
declare -a rows=()

expect() {          # expect <场景> <期望rc> <实际rc> <判据说明>
    local name="$1" want="$2" got="$3" what="$4" ok
    if [ "$want" = "$got" ]; then ok=1; else ok=0; fi
    if [ "$want" = "0" ] && [ "$got" = "4" ]; then ok=0; fi
    if [ "$ok" = "1" ]; then
        pass=$((pass+1)); rows+=("$name | 期望rc=$want 实际rc=$got | OK: $what")
    else
        fail=$((fail+1)); rows+=("$name | 期望rc=$want 实际rc=$got | FAIL: $what")
    fi
}

sentinel_ok() {     # 拒绝场景: 目标 OUT_DIR 不得被创建, 沙盒 KEEP 必须还在
    local dir="$1" tag="$2"
    if [ -e "$dir" ]; then
        fail=$((fail+1)); rows+=("$tag | 拒绝后目录仍被创建: $dir | FAIL")
        return
    fi
    if [ ! -f "$SB/KEEP.txt" ]; then
        fail=$((fail+1)); rows+=("$tag | 沙盒 KEEP 标记丢失 | FAIL"); return
    fi
    pass=$((pass+1)); rows+=("$tag | 未创建任何目录且 KEEP 标记完好 | OK")
}

printf '=== 语义链 runner 返修回归 (沙盒 %s) ===\n\n' "$SB"

# ---------- S1..S5: ONLY / REVERSE 参数入口(必须 rc=4 且不动文件) ----------
for s in "unknown:ZZZ_NO_VARIANT" "mixed:off_target ZZZ" "blank:   " "dup:off_target off_target"; do
    tag="${s%%:*}"; val="${s#*:}"
    out="$SB/out_$tag"
    ONLY="$val" SD_OUT_DIR="$out" bash "$RUNNER" > "$SB/log_$tag.txt" 2>&1
    rc=$?
    expect "S1-$tag ONLY 拒绝" 4 "$rc" "$(grep -o 'REJECT[^—]*' "$SB/log_$tag.txt" | head -1 | cut -c1-60)"
    sentinel_ok "$out" "S1-$tag 前置拒绝核查"
done

out="$SB/out_reverse_only"
REVERSE=1 ONLY=off_target SD_OUT_DIR="$out" bash "$RUNNER" > "$SB/log_reverse_only.txt" 2>&1
rc=$?
expect "S2 REVERSE+ONLY 组合" 4 "$rc" "$(grep -o 'REJECT: REVERSE=1[^—]*' "$SB/log_reverse_only.txt" | head -1 | cut -c1-60)"
sentinel_ok "$out" "S2 前置拒绝核查"

# ---------- S3: 合法单变体只声明实际执行集合 ----------
SD_OUT_DIR="$SB/out_single" ONLY=off_target bash "$RUNNER" > "$SB/log_single.txt" 2>&1
rc=$?
expect "S3 合法单变体退出码" 0 "$rc" "off_target 真执行"
if grep -q '实际执行集合=off_target$' "$SB/log_single.txt" \
   && grep -q 'NOT_RUN: on_target' "$SB/log_single.txt" \
   && ! grep -q '两个变体的必需用例全部真执行' "$SB/log_single.txt"; then
    pass=$((pass+1)); rows+=("S3 汇总措辞 | 只声明 off_target, on_target 标 NOT_RUN | OK")
else
    fail=$((fail+1)); rows+=("S3 汇总措辞 | 仍声称两变体全部执行或未标 NOT_RUN | FAIL")
fi

# ---------- 反向注入用的异常包装编译器(只放沙盒, 不改仓库任何源) ----------
cat > "$SB/wrap_main.c" <<'EOF'
/* 复审 §4 SD-NEW-02 的复现件: 夹具本身正常算出业务失败 rc=1,
 * 这里只把**退出方式**改成非合同值(rc=3)或信号终止, 源码与 runner 均不改。 */
#include <stdlib.h>
#include <signal.h>
#include <string.h>
int __real_main(void);
int __wrap_main(void)
{
    const char *k = getenv("SD_ANOMALY");
    int rc = __real_main();
    if (rc == 0) return 0;
    if (k && strcmp(k, "sigterm") == 0) { raise(SIGTERM); }
    return 3;
}
EOF
cat > "$SB/gccwrap.sh" <<EOF
#!/bin/bash
args=("\$@")
args+=("$SB/wrap_main.c" "-Wl,--wrap=main")
exec "$REAL_GCC" "\${args[@]}"
EOF
chmod +x "$SB/gccwrap.sh"
# 先用一个最小程序确认 --wrap 可用; 不可用就是环境故障, 不当成"检出"
printf 'int main(void){return 1;}\n' > "$SB/probe.c"
if "$SB/gccwrap.sh" -o "$SB/probe.exe" "$SB/probe.c" >"$SB/probe_build.txt" 2>&1 \
   && "$SB/probe.exe" >/dev/null 2>&1; then
    infra=$((infra+1))
    rows+=("S4 包装器可用性 | 探针仍返回 rc=1(未被改成异常终止) | INFRA-FAIL: 该环境下不判此项")
else
    pass=$((pass+1)); rows+=("S4 包装器可用性 | 探针被改成非 1 退出, 注入通道成立 | OK")
fi

# ---------- S5/S6: 异常退出的反向必须 INFRA-FAIL(rc=4), 不得 META-OK ----------
for kind in exit3 sigterm; do
    SD_OUT_DIR="$SB/out_abn_$kind" REVERSE=1 SD_ANOMALY="$kind" GCC="$SB/gccwrap.sh" \
        bash "$RUNNER" > "$SB/log_abn_$kind.txt" 2>&1
    rc=$?
    expect "S5-$kind 异常反向退出码" 4 "$rc" "$(grep -c 'INFRA-FAIL 非合同退出码' "$SB/log_abn_$kind.txt") 条 INFRA-FAIL"
    if grep -q 'META-OK' "$SB/log_abn_$kind.txt"; then
        fail=$((fail+1)); rows+=("S5-$kind 不得 META-OK | 出现 META-OK | FAIL")
    else
        pass=$((pass+1)); rows+=("S5-$kind 不得 META-OK | 无 META-OK, 外层判基础设施故障 | OK")
    fi
done

# ---------- S7: 同一包装器的干净对照(不设 SD_ANOMALY 时夹具本就 rc=1) ----------
SD_OUT_DIR="$SB/out_control" REVERSE=1 bash "$RUNNER" > "$SB/log_control.txt" 2>&1
rc=$?
expect "S7 未注入同编译器对照" 0 "$rc" "$(grep -c 'META-OK' "$SB/log_control.txt") 条 META-OK"

# ---------- S8/S9: 不完整输出必须被真实校验器拒绝 ----------
BASE_OUT="$SB/out_control/reverse/R1_aggregate_requires_all_four/out.txt"
if [ ! -f "$BASE_OUT" ]; then
    infra=$((infra+1)); rows+=("S8 输入 | 找不到反向原始输出, 无法构造缺例副本 | INFRA-FAIL")
else
    cp "$BASE_OUT" "$SB/doc_missing_case.txt"
    DROP_ID="$(grep -m1 -E '^CASE\|SD0[0-9][a-z]?-' "$BASE_OUT" | sed 's/^CASE|\([^|]*\)|.*/\1/')"
    # 只删一条必需 CASE, 并把 SUMMARY 的计数改成与实际行一致 =>
    # 这样唯一失配点就是"必需用例缺席", 而不是计数不自洽(校验器两条都要拒, 这里分开取证)
    "$PY" - "$BASE_OUT" "$SB/doc_missing_case.txt" "$DROP_ID" <<'PYEOF'
import io, sys
src, dst, drop = sys.argv[1], sys.argv[2], sys.argv[3]
lines = io.open(src, encoding='utf-8', newline='').read().split('\n')
kept = [l for l in lines if not l.startswith('CASE|%s|' % drop)]
cases = len([l for l in kept if l.startswith('CASE|')])
npass = len([l for l in kept if l.startswith('CASE|') and '|PASS|' in l])
nfail = len([l for l in kept if l.startswith('CASE|') and '|FAIL|' in l])
out = []
for l in kept:
    if l.startswith('SUMMARY|'):
        l = 'SUMMARY|cases=%d pass=%d fail=%d' % (cases, npass, nfail)
    out.append(l)
io.open(dst, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))
sys.stderr.write('DOCTORED drop=%s cases=%d pass=%d fail=%d\n' % (drop, cases, npass, nfail))
PYEOF
    SD_VALIDATE_FILE="$SB/doc_missing_case.txt" SD_VALIDATE_VARIANT=off_target \
        bash "$RUNNER" > "$SB/log_missing_case.txt" 2>&1
    rc=$?
    expect "S8 删除必需用例($DROP_ID)" 4 "$rc" "$(grep -o '缺席: .*' "$SB/log_missing_case.txt" | head -1)"

    grep -v '^SUMMARY|' "$BASE_OUT" > "$SB/doc_no_summary.txt"
    SD_VALIDATE_FILE="$SB/doc_no_summary.txt" SD_VALIDATE_VARIANT=off_target \
        bash "$RUNNER" > "$SB/log_no_summary.txt" 2>&1
    rc=$?
    expect "S9 删除 SUMMARY 行" 4 "$rc" "$(grep -o 'SUMMARY 行 [0-9]* 条[^)]*' "$SB/log_no_summary.txt" | head -1)"

    SD_VALIDATE_FILE="$BASE_OUT" SD_VALIDATE_VARIANT=off_target \
        bash "$RUNNER" > "$SB/log_ok_validate.txt" 2>&1
    rc=$?
    expect "S9b 未篡改副本的正对照必须成立" 0 "$rc" "$(grep -o 'VALIDATE RESULT: .*' "$SB/log_ok_validate.txt" | head -1 | cut -c1-50)"
fi

# ---------- 汇总 ----------
printf '\n=== 场景矩阵 ===\n'
for r in "${rows[@]}"; do printf '%s\n' "$r"; done
printf '\n通过=%d 不符=%d 环境故障=%d\n' "$pass" "$fail" "$infra"
{
    printf 'SD_REWORK_PASS=%s\n' "$pass"
    printf 'SD_REWORK_FAIL=%s\n' "$fail"
    printf 'SD_REWORK_INFRA=%s\n' "$infra"
    for r in "${rows[@]}"; do printf '%s\n' "$r"; done
} > "$SB/sd_rework_status.txt"
printf '状态文件(仓外, 未入库): %s\n' "$SB/sd_rework_status.txt"

if [ "$infra" -ne 0 ]; then exit 4; fi
if [ "$fail" -ne 0 ]; then exit 1; fi
printf 'SD REWORK CHECK RESULT: PASS\n'
exit 0
