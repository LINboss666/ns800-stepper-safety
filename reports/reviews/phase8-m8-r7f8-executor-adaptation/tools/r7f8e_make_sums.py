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
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
DRAFT = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
HEAD = ("# sha256 * <仓库相对路径>(LF, 无 BOM; 本清单不哈希自身字节 —— 一条清单不能给自己的字节作证)")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tracked(root, target):
    """Git 索引里的相对路径集合(含已暂存未提交的件)。"""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "--",
                          target.relative_to(root).as_posix()],
                         capture_output=True).stdout.decode("utf-8", "replace")
    return set(x for x in out.splitlines() if x)


def is_readback_receipt(root, target, p, tracked_rel):
    """推送后才存在的回读回执不列入清单; 但**只认本轮 proof/ 下、且还没进 Git 的那些 .txt**。

    两条收窄都有实例依据:
      * 上一版按文件名族(名字含 remote_readback 一律排除)把 `tools/r7f8*_remote_readback.py`
        这些**被交付的代码**也排除了, 覆盖面判定当场报未覆盖;
      * 再收窄到 proof/ 后仍会把 `proof/r7f8e_remote_readback_first_attempt.txt`
        (e97aaa4 已提交的判红原文)挤出清单 —— 已入库的证据字节必须继续有清单条目。
    所以口径是"尚未进 Git 的回执"= 本轮推送之后才会存在的那一份; 一旦某轮回执被提交,
    下一次的清单就把它列回去。与 r7f8e_staged_preflight.py 的 is_receipt 同一条规则。
    """
    rel = p.relative_to(root).as_posix()
    return (rel.startswith((target.relative_to(root).as_posix()) + "/proof/")
            and "remote_readback" in p.name and p.suffix == ".txt"
            and rel not in tracked_rel)


def write(root, target, out_name, notes, exclude_names=(), drop_receipts=False):
    self_rel = (target / out_name).relative_to(root).as_posix()
    tk = tracked(root, target) if drop_receipts else set()
    disk = [p for p in target.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    dropped = sorted(p.relative_to(root).as_posix() for p in disk
                     if drop_receipts and is_readback_receipt(root, target, p, tk))
    files = sorted(p for p in disk if p.name not in exclude_names
                   # 回执**按"本轮尚未入库的 proof 回执"**排除: 每轮写独立文件名(操作者要求
                   # 不覆盖上一轮的首次失败原文), 只点名一条会让新一轮的回执被当成
                   # 清单里的已交付字节。
                   and not (drop_receipts and is_readback_receipt(root, target, p, tk))
                   and p.relative_to(root).as_posix() != self_rel)
    if dropped:
        print("SUMS_EXCLUDED_RECEIPTS|%s" % ",".join(dropped))
    body = ["%s  %s" % (sha(p), p.relative_to(root).as_posix()) for p in files]
    with open(str(target / out_name), "wb") as f:
        f.write(("\n".join(notes(len(files)) + body) + "\n").encode("utf-8"))
    print("SUMS|%s|entries=%d|sha=%s" % (self_rel, len(files), sha(target / out_name)[:16]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    root = a.repo.resolve()

    def nf(rel_dir, skip_pending_receipts=False):
        """说明行里的**件数**一律当场数盘上常规文件, 不写字面量 —— 上一版写了"A 包原件 24 件",
        裁定包入库后那句就过期了(过期数字混在清单头里, 审核者会以为清单没重算)。
        proof 那一格还要跳过**推送后才存在的回读回执**(与清单排除同一条规则): 否则清单头依赖
        一个只有交付之后才会出现的文件, 重跑一次就把已交付的字节改掉(本轮踩过, §24.5 第 21 条)。"""
        d = root / rel_dir
        if not d.is_dir():
            return 0
        tk = tracked(root, root / ROUND)
        ps = [p for p in d.rglob("*") if p.is_file()]
        if skip_pending_receipts:
            ps = [p for p in ps
                  if not (p.relative_to(root).as_posix().startswith(ROUND + "/proof/")
                          and "remote_readback" in p.name and p.suffix == ".txt"
                          and p.relative_to(root).as_posix() not in tk)]
        return len(ps)

    inputs_n, proof_n = nf(ROUND + "/inputs"), nf(ROUND + "/proof", True)
    living = root / (ROUND + "/inputs/A_R7F8F_BUDGET_PKG/R7F8_LIVING_DRAFT.md")
    living_id = "%d B / %s…" % (living.stat().st_size, sha(living)[:12]) if living.is_file() \
        else "NA(不在盘上)"
    write(root, root / ROUND, "ROUND_SHA256SUMS.txt", lambda n: [
        HEAD,
        "# 生成方式: 对 %s/ 下每个常规文件直接读磁盘字节算 sha256"
        "(派生件/生成器/补丁/proof %d 份(含第一次回读判红原文)/合成计划与行表/inputs 里的 A 包原件 %d 件"
        "全在内); "
        "只排除 %s/proof/ 下**尚未进 Git** 且文件名含 remote_readback 的 .txt 回执 —— 它只能在推送"
        "之后才存在, 清单不能依赖自己交付之后的产物; 已提交的回执(如 first_attempt 判红原文)与"
        "工具件 tools/r7f8e_remote_readback.py 都是被交付的字节, 照常入列" % (ROUND, proof_n,
                                                                              inputs_n, ROUND),
        "# 条目数=%d; 与 Git blob 的逐条复算由 %s/tools/r7f8e_staged_preflight.py(提交前)"
        "与 tools/r7f8e_remote_readback.py(推送后)完成" % (n, ROUND),
        "# 重新生成: python %s/tools/r7f8e_make_sums.py --repo <仓库根>" % ROUND],
        exclude_names=("r7f8e_remote_readback.txt",), drop_receipts=True)
    write(root, root / DRAFT, "ROUND_SHA256SUMS.txt", lambda n: [
        HEAD,
        "# 生成方式: 对 %s/ 下每个常规文件直接读磁盘字节算 sha256(草案原件/合并版/判据原文/工具全在内)" % DRAFT,
        "# 本轮变化: R7F8_DRAFT_MERGED.md 按 A **预算裁定包**的活草案(%s)逐字承接 304 行后重算;"
        " A_DRAFT_ORIGINAL.md 与旧冻结件未改, 上一版合并件身份只写进记录节不回改; 其余条目同一份规则"
        % living_id,
        "# 条目数=%d; 与 Git blob 的逐条复算由 tools/r7f8_staged_preflight.py(提交前)与推送后的"
        "远端逐 blob 回读完成" % n,
        "# 重新生成: python %s/tools/r7f8e_make_sums.py --repo <仓库根>"
        "(上一版生成器 r7f8_make_sums.py 的说明行冻结在本轮之前的合并件, 再跑会丢掉本轮的承接说明)" % ROUND])
    write(root, root / R7F2B, "SHA256SUMS.txt", lambda n: [
        HEAD,
        "# 生成方式: 沿用该目录既有规则: 按**文件名**排除 SHA256SUMS.txt(含 a_review_r7f*/ 四份 A 包子清单)"
        "与 remote_readback.md; §20/§21 由 R7F7 轮追加, §22 由 R7F8 草案核对轮追加, "
        "§23 由 R7F8E 执行器离线适配轮追加, §24 由 R7F8F 返修+整包轮追加(其中 §24.8~§24.11 是"
        " A 预算裁定的落地记录), 条目集合不变",
        "# 条目数=%d; 与 Git blob 的逐条复算由 tools/r7f8_staged_preflight.py(提交前)与推送后的"
        "远端逐 blob 回读完成" % n,
        "# 重新生成: python %s/tools/r7f8e_make_sums.py --repo <仓库根>"
        "(上一版生成器 r7f8_make_sums.py 的说明行冻结在 §22, 再跑会把本节改回旧口径)" % ROUND],
        exclude_names=("SHA256SUMS.txt", "remote_readback.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
