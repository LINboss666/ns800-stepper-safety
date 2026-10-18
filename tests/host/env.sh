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
