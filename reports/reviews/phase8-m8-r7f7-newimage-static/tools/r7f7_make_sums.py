# -*- coding: utf-8 -*-
"""R7F7 清单生成(派生件): 只登记磁盘字节 sha256, 不改任何被列文件的字节。

两个目录两条规则(与 R7F6 同一形状, 原因也不同就写清):
  * reports/reviews/phase8-m8-r7f7-newimage-static/ -> 写 ROUND_SHA256SUMS.txt。
    本目录里 A 包自带的 SHA256SUMS.txt 是**原件**, 必须被登记而不是被按名字排除,
    所以只按"完整相对路径"排除本清单自身(清单不给自己的字节作证)。
  * reports/reviews/phase8-m8-r7f2b-safety-print-fix/ -> 原地重算 SHA256SUMS.txt,
    沿用该目录既有规则(按文件名排除清单自身与 remote_readback.md), 本轮只因 run_record.md 追加 §20。

用法: python tools/r7f7_make_sums.py --repo D:/ns800
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

R7F7 = "reports/reviews/phase8-m8-r7f7-newimage-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(root, target, out_name, note, exclude_names=()):
    self_rel = (target / out_name).relative_to(root).as_posix()
    files = sorted(p for p in target.rglob("*")
                   if p.is_file() and p.name not in exclude_names
                   and p.relative_to(root).as_posix() != self_rel
                   and "__pycache__" not in p.parts)
    head = ["# sha256 * <仓库相对路径>(LF, 无 BOM; 本清单不哈希自身字节 —— 一条清单不能给自己的字节作证)",
            "# 生成方式: " + note,
            "# 条目数=%d; 与 Git blob 的逐条复算由 tools/r7f7_staged_preflight.py(提交前)与推送后的远端逐 blob 回读完成"
            % len(files),
            "# 重新生成: python %s/tools/r7f7_make_sums.py --repo <仓库根>" % R7F7]
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
    write(root, root / R7F7, "ROUND_SHA256SUMS.txt",
          "对 %s/ 下每个常规文件(**含 A 包自带的 SHA256SUMS.txt 原件**, 只按完整相对路径排除本清单自身"
          "与 __pycache__)直接读磁盘字节算 sha256" % R7F7)
    write(root, root / R7F2B, "SHA256SUMS.txt",
          "沿用该目录既有规则: 按**文件名**排除 SHA256SUMS.txt(含 a_review_r7f*/ 四份 A 包子清单)与 "
          "remote_readback.md; 本轮只因 run_record.md 追加 §20/§21 而重算, 条目集合不变",
          exclude_names=("SHA256SUMS.txt", "remote_readback.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
