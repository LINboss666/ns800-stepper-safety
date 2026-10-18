#!/usr/bin/env bash
# test_path_guard.sh - 工作目录守卫的沙盒回归（R4-A）
#
# 只在本脚本**新建的一次性沙盒**里跑: 绝不拿真实项目根、真实用户目录或现有工作数据试删除保护。
# 证明的是复审要求的那一件事: **危险目录在任何破坏或创建之前就被拒绝**, 标记文件不丢。
# 另外证明两类假阳性没有: 同前缀兄弟目录不被误拒; 含空格的有效仓外路径可正常使用。
#
# 用法: bash tests/host/test_path_guard.sh
# 退出码: 0=全部断言成立; 1=任一断言不成立(打印 FAIL 行)

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
fails=0
pass_n=0
ok()   { printf '  [PASS] %s\n' "$*"; pass_n=$((pass_n+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; fails=$((fails+1)); }

# 沙盒放在系统临时目录里, 不在被测仓库内
SB="$(mktemp -d 2>/dev/null)" || { echo "FATAL: 无法创建沙盒临时目录"; exit 2; }
case "$SB" in "$HERE"*) echo "FATAL: 沙盒竟然落在测试目录内"; exit 2 ;; esac
SB="$(cd "$SB" && pwd -P)"
REPOS="$SB/repo"                       # 沙盒里的"假仓库", 守卫应把它当仓库根
mkdir -p "$REPOS/scratch_out" "$SB/outside" "$SB/repo-sibling" "$SB/valid out dir"
git init -q "$REPOS" 2>/dev/null || { echo "FATAL: 沙盒 git init 失败"; exit 2; }
printf 'DO-NOT-DELETE\n' > "$REPOS/REPO_KEEP.txt"
printf 'DO-NOT-DELETE\n' > "$REPOS/scratch_out/CHILD_KEEP.txt"
printf 'DO-NOT-DELETE\n' > "$SB/ANCESTOR_KEEP.txt"
( cd "$REPOS" && git add REPO_KEEP.txt >/dev/null 2>&1 && \
  git -c user.name=guard -c user.email=guard@example.invalid commit -qm sandbox >/dev/null 2>&1 )
SANDBOX_HEAD="$(cd "$REPOS" && git rev-parse HEAD 2>/dev/null)"

KEEP_CONTENT="DO-NOT-DELETE"
kept() { [ -f "$1" ] && [ "$(cat "$1" 2>/dev/null)" = "$KEEP_CONTENT" ]; }

# run_negatives.sh 入口拒绝测试
neg_rejects() {                        # neg_rejects <标签> <危险路径> <必须存活的标记文件>
    local tag="$1" target="$2" marker="$3" rc
    local log="$SB/neg_$tag.log"
    ( cd "$HERE" && env REPO="$REPOS" BASE_HEAD="${SANDBOX_HEAD:-HEAD}" NEG_WORK="$target" \
        ONLY="N1_only_V1_injected_fail" NEG_META_SKIP=1 bash "$HERE/run_negatives.sh" ) \
        > "$log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then bad "$tag: 入口对危险目录返回 0(应拒绝)"; else ok "$tag: 入口拒绝, rc=$rc"; fi
    grep -aq 'GUARD-REJECT' "$log" && ok "$tag: 日志含 GUARD-REJECT 原因" \
        || bad "$tag: 日志没有 GUARD-REJECT(可能死于别的原因)"
    if kept "$marker"; then ok "$tag: 标记文件 $marker 未被删除/改写"; else bad "$tag: 标记文件丢失 => 先删后拒"; fi
}

# build_and_run.sh 入口拒绝测试
runner_rejects() {                     # runner_rejects <标签> <危险 OUT_DIR> <标记文件>
    local tag="$1" target="$2" marker="$3" rc
    local log="$SB/run_$tag.log"
    ( cd "$HERE" && env REPO_ROOT="$REPOS" OUT_DIR="$target" \
        bash "$HERE/build_and_run.sh" ) > "$log" 2>&1
    rc=$?
    [ "$rc" -ne 0 ] && ok "$tag: runner 拒绝危险 OUT_DIR, rc=$rc" || bad "$tag: runner 未拒绝(rc=0)"
    grep -aq 'GUARD-REJECT' "$log" && ok "$tag: 日志含 GUARD-REJECT" || bad "$tag: 日志无 GUARD-REJECT"
    if kept "$marker"; then ok "$tag: 标记文件保留"; else bad "$tag: 标记文件丢失"; fi
    [ -d "$target" ] && bad "$tag: 被拒后仍创建了目标目录" || ok "$tag: 被拒后没有创建目标目录"
}

echo "############ 路径守卫沙盒回归 (沙盒=$SB) ############"

echo "-- 1) 仓库根本身"
neg_rejects    repo_root "$REPOS" "$REPOS/REPO_KEEP.txt"
echo "-- 2) 仓内子目录(先删后拒的那个原始复现)"
neg_rejects    repo_sub "$REPOS/scratch_out" "$REPOS/scratch_out/CHILD_KEEP.txt"
runner_rejects repo_sub_runner "$REPOS/scratch_out/will_not_be_created" "$REPOS/scratch_out/CHILD_KEEP.txt"
echo "-- 3) 包含仓库的祖先目录"
neg_rejects    ancestor "$SB" "$SB/ANCESTOR_KEEP.txt"
kept "$REPOS/REPO_KEEP.txt" && ok "祖先场景: 仓库内文件仍完好" || bad "祖先场景: 仓库内容被删"
echo "-- 4) 含 .. 规范化后落回仓内"
neg_rejects    dotdot_in "$REPOS/scratch_out/../scratch_out" "$REPOS/scratch_out/CHILD_KEEP.txt"
neg_rejects    dotdot_in2 "$REPOS/../repo/scratch_out" "$REPOS/scratch_out/CHILD_KEEP.txt"
echo "-- 5) 文件系统根与空值"
neg_rejects    fs_root "/" "$SB/ANCESTOR_KEEP.txt"
( cd "$HERE" && env REPO="$REPOS" BASE_HEAD="${SANDBOX_HEAD:-HEAD}" NEG_WORK="" \
    ONLY="N1_only_V1_injected_fail" NEG_META_SKIP=1 bash "$HERE/run_negatives.sh" ) > "$SB/neg_empty.log" 2>&1
[ $? -ne 0 ] && ok "空值: 入口拒绝" || bad "空值: 入口竟然通过"
grep -aq 'GUARD-REJECT' "$SB/neg_empty.log" && ok "空值: 明确拒绝而非静默改用别的路径" \
    || bad "空值: 没有 GUARD-REJECT"

echo "-- 6) 同前缀兄弟目录不得被误拒(不是字符串前缀匹配)"
u="$( cd "$HERE" && REPO_ROOT="$REPOS" bash -c '. "$1/env.sh"; require_outside_repo "$2" T' _ "$HERE" "$SB/repo-sibling" 2>/dev/null )" \
    && ok "repo-sibling 被接受 => $u" || bad "repo-sibling 被误拒(字符串前缀 bug 复发)"
kept "$REPOS/REPO_KEEP.txt" && ok "兄弟目录场景未伤及沙盒仓库" || bad "兄弟目录场景伤及沙盒仓库"

echo "-- 7) 含空格的有效仓外路径可用"
u="$( cd "$HERE" && REPO_ROOT="$REPOS" bash -c '. "$1/env.sh"; require_outside_repo "$2" T' _ "$HERE" "$SB/valid out dir" 2>/dev/null )" \
    && ok "含空格路径被接受 => $u" || bad "含空格路径被误拒"
mkdir -p "$SB/valid out dir/sub" && printf 'x\n' > "$SB/valid out dir/sub/f.txt" \
    && [ -f "$SB/valid out dir/sub/f.txt" ] && ok "含空格路径可正常创建与写入" || bad "含空格路径不可用"

echo "-- 8) 规范化输出确实是绝对路径且不含尾斜杠"
u="$( cd "$HERE" && REPO_ROOT="$REPOS" bash -c '. "$1/env.sh"; require_outside_repo "$2" T' _ "$HERE" "$SB/outside/./x/../y/" )" \
    && case "$u" in /*) [ "${u%/}" = "$u" ] && ok "规范化结果=$u (绝对、无尾斜杠、.. 已消解)" || bad "规范化结果异常: $u" ;;
        *) bad "规范化结果不是绝对路径: $u" ;; esac \
    || bad "有效路径被拒"

echo "-- 9) 链接落点的真实能力边界(如实记录, 不夸大)"
ln -s "$REPOS" "$SB/link_into_repo" 2>/dev/null
if [ -L "$SB/link_into_repo" ]; then
    if ( cd "$HERE" && REPO_ROOT="$REPOS" bash -c '. "$1/env.sh"; require_outside_repo "$2" T' _ "$HERE" "$SB/link_into_repo" ) >/dev/null 2>&1; then
        bad "指向仓内的符号链接被接受(落点未解析)"
    else
        ok "指向仓内的符号链接被拒绝(落点已解析)"
    fi
else
    printf '  [NOTE] 本机 MSYS 把目录软链表现为普通目录(test -L 为假, realpath/pwd -P 都不解析),\n'
    printf '         所以"经由链接绕回仓内"这一条**不声称**被词法守卫覆盖。\n'
    printf '         实际保护改成不依赖链接可见性: 只删本工具写过哨兵文件的目录 =>\n'
    kept "$REPOS/REPO_KEEP.txt" && ok "  该场景沙盒仓库文件完好(未被 rm 波及)" || bad "  沙盒仓库文件被删"
fi

echo "-- 10) 无哨兵的已存在仓外目录: 必须拒绝且不删内容"
mkdir -p "$SB/foreign" && printf 'DO-NOT-DELETE\n' > "$SB/foreign/CHILD_KEEP.txt"
( cd "$HERE" && env REPO="$REPOS" BASE_HEAD="${SANDBOX_HEAD:-HEAD}" NEG_WORK="$SB/foreign" \
    ONLY="N1_only_V1_injected_fail" NEG_META_SKIP=1 bash "$HERE/run_negatives.sh" ) > "$SB/neg_foreign.log" 2>&1
rc=$?
[ "$rc" -ne 0 ] && ok "无哨兵的已存在目录被拒绝, rc=$rc" || bad "无哨兵目录未被拒绝(rc=0) => 会 rm 别人的东西"
grep -aq '哨兵' "$SB/neg_foreign.log" && ok "拒绝原因明确指向缺少哨兵" || bad "日志未提及哨兵(可能死于别处)"
kept "$SB/foreign/CHILD_KEEP.txt" && ok "无哨兵目录里的文件未被删除" || bad "无哨兵目录内容被 rm 掉"

echo ""
echo "############ 结果: PASS=$pass_n FAIL=$fails ############"
rm -rf "$SB"
[ "$fails" -eq 0 ] || { echo "路径守卫回归失败"; exit 1; }
echo "全部断言成立: 危险目录在任何文件操作之前被拒绝, 标记文件零丢失。"
