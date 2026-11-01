#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按轮重算 SHA256SUMS.txt(清单是**派生件**: 只登记本轮目录下每个常规文件的磁盘字节 sha256)。

规则(与既有轮一致, 逐条都能被第三方复算):
  * 排除清单自身(SHA256SUMS.txt)、remote_readback.md 与任何 __pycache__ —— 一条清单不能给自己的字节作证
  * 路径用仓库相对形式 + 正斜杠; 输出 LF、无 BOM; 排序稳定
  * 本目录下的 `.gitattributes` 一律 `-text`, 所以工作树字节 == Git blob 字节; 逐条与 blob 的复算由
    host/staged_preflight_batch.py(提交前)与推送后的远端逐 blob 回读完成
  * 不改任何被列文件的字节: 清单跟随数据, 不是数据跟随清单

用法: python make_round_sums.py <目录> [<目录> ...]
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EXCLUDE_NAMES = ("SHA256SUMS.txt", "remote_readback.md")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(target):
    files = sorted(p for p in target.rglob("*")
                   if p.is_file() and p.name not in EXCLUDE_NAMES and "__pycache__" not in p.parts)
    rel = target.relative_to(ROOT).as_posix()
    head = ["# sha256 * <仓库相对路径>(LF, 无 BOM; 本清单不哈希自身字节 —— 一条清单不能给自己的字节作证)",
            "# 生成方式: 对 %s/ 下每个常规文件(排除 SHA256SUMS.txt、remote_readback.md 与 __pycache__) "
            "直接读磁盘字节算 sha256" % rel,
            "# 条目数=%d; 与 Git blob 的逐条复算由 host/staged_preflight_batch.py(提交前, 单批流)与"
            "推送后的远端逐 blob 回读完成" % len(files),
            "# 重新生成: python reports/reviews/phase8-m8-idle-stack-r4/host/make_round_sums.py <本目录>"]
    body = ["%s  %s" % (sha(p), p.relative_to(ROOT).as_posix()) for p in files]
    out = target / "SHA256SUMS.txt"
    out.write_text("\n".join(head + body) + "\n", encoding="utf-8", newline="\n")
    print("SUMS|%s|entries=%d|sha=%s" % (rel, len(files), sha(out)[:16]))


if __name__ == "__main__":
    for a in sys.argv[1:]:
        build(Path(a).resolve())
