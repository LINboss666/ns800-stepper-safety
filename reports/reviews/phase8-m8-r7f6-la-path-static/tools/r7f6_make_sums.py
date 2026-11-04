# -*- coding: utf-8 -*-
"""本轮清单生成(派生件): 只登记磁盘字节 sha256, 不改任何被列文件的字节。

两个目录两条规则, 因为本轮目录里**本来就有 A 包自带的 SHA256SUMS.txt**(它是原件, 必须被登记):
  * reports/reviews/phase8-m8-r7f6-la-path-static/  -> 写 ROUND_SHA256SUMS.txt, 只排除清单自身
  * reports/reviews/phase8-m8-r7f2b-safety-print-fix/ -> 原地重算 SHA256SUMS.txt(沿用既有排除规则:
    清单自身、remote_readback.md、__pycache__), 因为本轮给它追加了 run_record §19

用法: python tools/r7f6_make_sums.py --repo D:/ns800
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

R7F6 = "reports/reviews/phase8-m8-r7f6-la-path-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(root, target, out_name, exclude, note, exclude_names=()):
    self_rel = (target / out_name).relative_to(root).as_posix()
    files = sorted(p for p in target.rglob("*")
                   if p.is_file() and p.name not in exclude_names
                   and p.relative_to(root).as_posix() not in (exclude | {self_rel})
                   and "__pycache__" not in p.parts)
    head = ["# sha256 * <仓库相对路径>(LF, 无 BOM; 本清单不哈希自身字节 —— 一条清单不能给自己的字节作证)",
            "# 生成方式: " + note,
            "# 条目数=%d; 与 Git blob 的逐条复算由 tools/r7f6_staged_preflight.py(提交前)与推送后的远端逐 blob 回读完成"
            % len(files),
            "# 重新生成: python %s/tools/r7f6_make_sums.py --repo <仓库根>" % R7F6]
    body = ["%s  %s" % (sha(p), p.relative_to(root).as_posix()) for p in files]
    out = target / out_name
    out.write_text("\n".join(head + body) + "\n", encoding="utf-8", newline="\n")
    print("SUMS|%s|%s|entries=%d|sha=%s" % (target.relative_to(root).as_posix(), out_name,
                                            len(files), sha(out)[:16]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    root = a.repo.resolve()
    write(root, root / R7F6, "ROUND_SHA256SUMS.txt", set(),
          "对 %s/ 下每个常规文件(**含 A 包自带的 SHA256SUMS.txt 原件**, 只排除本清单自身与 __pycache__) "
          "直接读磁盘字节算 sha256; 本目录没有嵌套清单, 所以不按文件名排除" % R7F6)
    write(root, root / R7F2B, "SHA256SUMS.txt", set(),
          "沿用该目录既有规则: 按**文件名**排除 SHA256SUMS.txt(含 a_review_r7f*/ 四份 A 包子清单)、"
          "remote_readback.md 与 __pycache__; 本轮只因 run_record.md 追加 §19 而重算, 条目集合不变",
          exclude_names=("SHA256SUMS.txt", "remote_readback.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
