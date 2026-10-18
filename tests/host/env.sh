#!/usr/bin/env bash
# env.sh - tests/host 验证链的公共环境解析（被三个 sh 以 source 方式引用）
#
# 解析顺序全部可被显式环境变量覆盖，不写死任何盘符：
#   REPO_ROOT  <- git rev-parse（或环境变量）
#   APP_REL    <- 生产 applications 目录（仓库根相对路径）
#   SRC_APP    <- $REPO_ROOT/$APP_REL
#   GCC        <- 显式指定优先；否则 PATH 里的 gcc/cc；再退到常见 MinGW 安装位置
#   WORK_ROOT  <- 仓库**外**的临时工作区（构建产物、隔离快照一律不落仓库内）

env_die() { printf 'FATAL: %s\n' "$*" >&2; exit 4; }

env_repo_root() {
    if [ -n "${REPO_ROOT:-}" ]; then printf '%s\n' "$REPO_ROOT"; return 0; fi
    local h r
    h="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    r="$(git -C "$h" rev-parse --show-toplevel 2>/dev/null)"
    [ -n "$r" ] || env_die "无法定位仓库根（可用 REPO_ROOT=<路径> 指定）"
    printf '%s\n' "$r"
}

env_app_rel() { printf '%s\n' "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications"; }

env_detect_gcc() {
    if [ -n "${GCC:-}" ]; then printf '%s\n' "$GCC"; return 0; fi
    local c
    for c in gcc cc; do
        if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return 0; fi
    done
    for c in "/c/Program Files (x86)/Dev-Cpp/MinGW64/bin/gcc.exe" \
             "/c/MinGW/bin/gcc.exe" \
             "/mingw64/bin/gcc.exe" \
             "/usr/bin/gcc.exe" \
             "/usr/bin/gcc"; do
        [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
    done
    return 1
}

env_work_root() {
    if [ -n "${WORK_ROOT:-}" ]; then printf '%s\n' "$WORK_ROOT"; return 0; fi
    local base="${TMPDIR:-/tmp}" win=""
    # 注意: 不能写成 `cd && pwd -W || cd && pwd` —— 那个结合顺序会让两条 pwd 都执行,
    # 把带换行的结果当成路径用, 目录被建到奇怪的地方, 快照导出随之失败。
    win="$(cd "$base" 2>/dev/null && pwd -W 2>/dev/null)" || win=""
    [ -n "$win" ] || win="$base"
    printf '%s\n' "$win/ns800-host-chain"
}

env_default_head() {
    local r; r="$(env_repo_root)"
    git -C "$r" rev-parse HEAD 2>/dev/null || env_die "无法取得 HEAD（可用 BASE_HEAD=<sha> 指定）"
}

# ---------------------------------------------------------------- 工作目录守卫(R4-A)
# 规则: **任何 rm / mkdir / 输出重定向 / 编译之前**先做规范化与验证; 只按目录成分比较,
# 不做字符串前缀匹配(同前缀兄弟目录不得误拒); Windows 路径大小写不敏感。
# 拒绝: 空值、文件系统根、仓库根本身、仓库内任何子目录、以及"包含仓库的祖先目录"
# (清理这种目录会把整个仓库一起包住删掉)。
# 符号链接: 目标已存在时用 realpath --canonicalize-existing 解析实际落点(可识别"仓外链到仓内");
# 目标尚不存在时只能做词法规范化(--canonicalize-missing), 此时**不保证**识别经由新建软链的落点,
# 这是本工具的已知限制, 不做超出能力的承诺。

fs_abs() {                           # 规范化为绝对路径(msys 风格, 无结尾斜杠)
    local p="$1" out=""
    [ -n "${p// /}" ] || return 1
    if command -v cygpath >/dev/null 2>&1; then
        p="$(cygpath -u -- "$p" 2>/dev/null)" || p="$1"
    fi
    case "$p" in /*) : ;; *) p="$PWD/$p" ;; esac
    if command -v realpath >/dev/null 2>&1; then
        if [ -e "$p" ]; then
            out="$(realpath --canonicalize-existing -- "$p" 2>/dev/null)" || out=""
        fi
        [ -n "$out" ] || out="$(realpath --canonicalize-missing -- "$p" 2>/dev/null)" || out=""
    fi
    [ -n "$out" ] || out="$p"
    out="${out%/}"
    [ -n "$out" ] || out="/"
    printf '%s\n' "$out"
    return 0
}

path_within() {                      # path_within <子> <父>: 子==父 或 子严格在父目录之下
    local c p
    c="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    p="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
    [ "$c" = "$p" ] && return 0
    case "$c" in "$p"/*) return 0 ;; esac
    return 1
}

# require_outside_repo <候选路径> <用途标签>
#   成功: 打印**规范化后的绝对路径**(调用方必须改用这个值), 返回 0
#   失败: 在 stderr 打印 GUARD-REJECT + 原因, 返回 1(调用方立即非零退出, 且不得动任何文件)
require_outside_repo() {
    local raw="$1" tag="$2" abs repo
    if [ -z "${raw// /}" ]; then
        printf 'GUARD-REJECT(%s): 路径为空 —— 拒绝而不是静默改用别的路径\n' "$tag" >&2
        return 1
    fi
    if ! abs="$(fs_abs "$raw")"; then
        printf 'GUARD-REJECT(%s): 路径无法规范化为绝对路径\n' "$tag" >&2
        return 1
    fi
    # 仓库根取调用脚本已经确定的值(REPO_ROOT / REPO), 让沙盒回归可以把"仓库"指向别处
    if ! repo="$(fs_abs "${REPO_ROOT:-${REPO:-$(env_repo_root)}}")"; then
        printf 'GUARD-REJECT(%s): 无法定位仓库根\n' "$tag" >&2
        return 1
    fi
    if [ "$abs" = "/" ]; then
        printf 'GUARD-REJECT(%s): 目标规范化后是文件系统根, 清理它会毁掉整块盘\n' "$tag" >&2
        return 1
    fi
    if path_within "$abs" "$repo"; then
        printf 'GUARD-REJECT(%s): 目标位于仓库内, 拒绝在任何文件操作之前\n' "$tag" >&2
        printf '           目标=%s 仓库根=%s\n' "$abs" "$repo" >&2
        return 1
    fi
    if path_within "$repo" "$abs"; then
        printf 'GUARD-REJECT(%s): 目标是包含仓库的祖先目录, 清理它会连带仓库一起删\n' "$tag" >&2
        printf '           目标=%s 仓库根=%s\n' "$abs" "$repo" >&2
        return 1
    fi
    printf '%s\n' "$abs"
}
