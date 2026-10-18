#!/usr/bin/env bash
# make_source_manifest.sh - 生成/校验"验证源码哈希清单"
#
# 字节语义(重要): 清单里的 SHA-256 一律针对 **Git blob 的原始字节**(对象库内容), 不是工作树字节。
# 本机 core.autocrlf=true 会把 CRLF 文件在签出时改写, 工作树字节与 blob 字节的哈希不同 ——
# F2d-R3 的清单就是踩了这个差异(声称 11/11, 远端实校 10/11)。清单不能靠本机校验冒充远端一致。
#
# 用法:
#   bash tests/host/make_source_manifest.sh --out <清单文件> [路径前缀...]
#       从**索引**读取 blob 内容生成清单(默认前缀 tests/host)。先 git add, 再生成。
#   bash tests/host/make_source_manifest.sh --verify <rev> <清单文件>
#       对指定 rev(本地提交或 origin/<分支> 这类远端跟踪引用)逐项复算 blob 哈希并比对。
# 退出码: 0 成功/全部一致; 4 用法或读取失败; 1 存在失配项。

set -u -o pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null)}"
[ -n "$REPO" ] || { echo "FATAL: 无法定位仓库根"; exit 4; }

git_b() { git -C "$REPO" "$@"; }        # 取 blob 字节: git cat-file blob <对象>

cmd="${1:-}"; shift || true
case "$cmd" in
--out)
    out="${1:-}"; shift || true
    [ -n "$out" ] || { echo "FATAL: --out 需要目标文件"; exit 4; }
    prefixes=("$@"); [ ${#prefixes[@]} -gt 0 ] || prefixes=(tests/host)
    tmp="$(mktemp)"
    {
        echo "# 验证源码哈希清单 —— 字节语义 = Git blob 原始字节(对象库内容)"
        echo "# 生成时间(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)   生成方式: git cat-file blob ':<path>' | sha256sum"
        echo "# 前缀: ${prefixes[*]}   来源: 索引(已 git add, 尚未提交 => 提交后请用 --verify 对远端对象复核)"
        echo "# 每行: <sha256>  <路径>   (清单本身不包含自己)"
    } > "$tmp.head"
    n=0; wt_diff=0
    : > "$tmp.body"
    : > "$tmp.wt"
    for pre in "${prefixes[@]}"; do
        while IFS= read -r p; do
            [ -n "$p" ] || continue
            h="$(git_b cat-file blob ":$p" | sha256sum | cut -d' ' -f1)"
            printf '%s  %s\n' "$h" "$p" >> "$tmp.body"
            n=$((n+1))
            if [ -f "$REPO/$p" ]; then
                wh="$(sha256sum "$REPO/$p" | cut -d' ' -f1)"
                if [ "$wh" != "$h" ]; then
                    printf '%s  %s\n' "$wh" "$p" >> "$tmp.wt"
                    wt_diff=$((wt_diff+1))
                fi
            fi
        done < <(git_b ls-files -c -- "$pre")
    done
    {
        cat "$tmp.head"
        sort "$tmp.body"
        echo
        echo "# 条目数: $n"
        if [ "$wt_diff" -gt 0 ]; then
            echo "#"
            echo "# 另列标签【仅供本地对照, 不是清单的校验对象】: 下列文件的工作树字节与 blob 字节不同"
            echo "# (通常是 core.autocrlf 在签出时改写换行)。远端审查请以**上面的 blob 哈希**为准。"
            echo "# --- worktree-bytes (labeled separately, not the verification target) ---"
            sort "$tmp.wt"
        fi
    } > "$out"
    rm -f "$tmp.head" "$tmp.body" "$tmp.wt"
    echo "已生成 $out: 条目 $n, 工作树与 blob 字节不同的文件 $wt_diff 个"
    ;;
--verify)
    rev="${1:-}"; man="${2:-}"
    [ -n "$rev" ] && [ -n "$man" ] || { echo "FATAL: --verify <rev> <清单文件>"; exit 4; }
    git_b cat-file -e "$rev" 2>/dev/null || git_b rev-parse -q --verify "$rev" >/dev/null || {
        echo "FATAL: 找不到引用 $rev"; exit 4; }
    ok=0; bad=0; miss=0
    while IFS= read -r line; do
        case "$line" in
        ''|\#*) continue ;;
        esac
        h="${line%%  *}"; p="${line#*  }"
        # 只吃 blob 段: 遇到另列的工作树段就停
        case "$h" in ---*) break ;; esac
        [ ${#h} -eq 64 ] || continue
        if ! git_b cat-file blob "$rev:$p" > /dev/null 2>&1; then
            printf 'MISSING  %s\n' "$p"; miss=$((miss+1)); continue
        fi
        a="$(git_b cat-file blob "$rev:$p" | sha256sum | cut -d' ' -f1)"
        if [ "$a" = "$h" ]; then ok=$((ok+1)); else
            printf 'MISMATCH %s 清单=%s 实际=%s\n' "$p" "$h" "$a"; bad=$((bad+1))
        fi
    done < "$man"
    echo "VERIFY rev=$rev : OK=$ok MISMATCH=$bad MISSING=$miss"
    [ "$bad" -eq 0 ] && [ "$miss" -eq 0 ] || exit 1
    ;;
*)
    sed -n '2,12p' "$0"
    exit 4
    ;;
esac
