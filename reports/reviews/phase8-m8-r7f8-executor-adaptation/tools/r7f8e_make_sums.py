# -*- coding: utf-8 -*-
"""R7F8E 轮的清单生成: 只登记磁盘字节 sha256, 不改任何被列文件。

三条清单:
  * 本轮目录 -> ROUND_SHA256SUMS.txt(按**完整相对路径**排除清单自身; 清单不哈希自己的字节);
  * phase8-m8-r7f8-closed-loop-draft -> ROUND_SHA256SUMS.txt(本轮把 A 的修订草案合进了合并版, 必须重算);
  * phase8-m8-r7f2b-safety-print-fix -> SHA256SUMS.txt(本轮往它的 run_record.md 追加了 §23)。

上一版生成器 `phase8-m8-r7f8-closed-loop-draft/tools/r7f8_make_sums.py` 的说明行冻结在 §22,
再跑会把本节改回旧口径 => 指针改指本轮生成器。条目集合不变, 只是被列文件的字节变了。
用法: python tools/r7f8e_make_sums.py --repo D:/ns800
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
DRAFT = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
HEAD = ("# sha256 * <仓库相对路径>(LF, 无 BOM; 本清单不哈希自身字节 —— 一条清单不能给自己的字节作证)")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(root, target, out_name, notes, exclude_names=()):
    self_rel = (target / out_name).relative_to(root).as_posix()
    files = sorted(p for p in target.rglob("*") if p.is_file()
                   and p.name not in exclude_names
                   and p.relative_to(root).as_posix() != self_rel
                   and "__pycache__" not in p.parts)
    body = ["%s  %s" % (sha(p), p.relative_to(root).as_posix()) for p in files]
    with open(str(target / out_name), "wb") as f:
        f.write(("\n".join(notes(len(files)) + body) + "\n").encode("utf-8"))
    print("SUMS|%s|entries=%d|sha=%s" % (self_rel, len(files), sha(target / out_name)[:16]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    root = a.repo.resolve()
    write(root, root / ROUND, "ROUND_SHA256SUMS.txt", lambda n: [
        HEAD,
        "# 生成方式: 对 %s/ 下每个常规文件直接读磁盘字节算 sha256"
        "(派生件/生成器/补丁/四份 proof(含第一次回读判红原文)/合成计划与行表/A 包原件 24 件全在内); "
        "按**文件名**排除 r7f8e_remote_readback.txt —— 它只能在推送之后才存在, "
        "清单不能依赖自己交付之后的产物" % ROUND,
        "# 条目数=%d; 与 Git blob 的逐条复算由 %s/tools/r7f8e_staged_preflight.py(提交前)"
        "与 tools/r7f8e_remote_readback.py(推送后)完成" % (n, ROUND),
        "# 重新生成: python %s/tools/r7f8e_make_sums.py --repo <仓库根>" % ROUND],
        exclude_names=("r7f8e_remote_readback.txt",))
    write(root, root / DRAFT, "ROUND_SHA256SUMS.txt", lambda n: [
        HEAD,
        "# 生成方式: 对 %s/ 下每个常规文件直接读磁盘字节算 sha256(草案原件/合并版/判据原文/工具全在内)" % DRAFT,
        "# 本轮变化: R7F8_DRAFT_MERGED.md 按 A 包 R7F8_DRAFT_UPDATED.md 逐字承接后重算"
        "(A_DRAFT_ORIGINAL.md 与旧冻结件未改); 其余条目同一份规则",
        "# 条目数=%d; 与 Git blob 的逐条复算由 tools/r7f8_staged_preflight.py(提交前)与推送后的"
        "远端逐 blob 回读完成" % n,
        "# 重新生成: python %s/tools/r7f8e_make_sums.py --repo <仓库根>"
        "(上一版生成器 r7f8_make_sums.py 的说明行冻结在本轮之前的合并件, 再跑会丢掉本轮的承接说明)" % ROUND])
    write(root, root / R7F2B, "SHA256SUMS.txt", lambda n: [
        HEAD,
        "# 生成方式: 沿用该目录既有规则: 按**文件名**排除 SHA256SUMS.txt(含 a_review_r7f*/ 四份 A 包子清单)"
        "与 remote_readback.md; §20/§21 由 R7F7 轮追加, §22 由 R7F8 草案核对轮追加, "
        "§23 由 R7F8E 执行器离线适配轮追加, 条目集合不变",
        "# 条目数=%d; 与 Git blob 的逐条复算由 tools/r7f8_staged_preflight.py(提交前)与推送后的"
        "远端逐 blob 回读完成" % n,
        "# 重新生成: python %s/tools/r7f8e_make_sums.py --repo <仓库根>"
        "(上一版生成器 r7f8_make_sums.py 的说明行冻结在 §22, 再跑会把本节改回旧口径)" % ROUND],
        exclude_names=("SHA256SUMS.txt", "remote_readback.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
