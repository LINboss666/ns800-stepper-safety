# -*- coding: utf-8 -*-
"""R7F8E 轮的提交前置检查(只读 git 暂存区, 不写任何被检文件)。

六件事, 每件都带"必须会命中"的负控, 否则拒绝宣布干净:
  0) 清单覆盖面: 本轮目录里每个磁盘文件都要么在清单里, 要么是两件声明排除项之一
     (清单自身、推送后才存在的回读回执), 且该回执**不得被暂存**(提交了它, 清单就漏掉一件已入库的字节);
  1) 暂存 blob 的 sha256 与磁盘字节、与清单登记值三方对账(先把首条改坏, 确认比较器会报警);
  2) 暂存集里不得出现身份性形态: 私有 DAP UID 明文(两种大小写)/用户主目录片段/漫游数据目录片段;
     模式一律运行时拼装 —— 写成连续字面量会被本扫描抓到(检查器自己就是泄漏源);
  3) 越界暂存: 本轮只允许新轮次目录、其两份交接件、run_record 与其清单、草案目录的合并版与清单、
     以及 .gitattributes 里为本轮目录新增的 -text 规则(必须同一次提交生效, 否则 autocrlf 改掉登记字节);
  4) 产品源码树与主机链不得有任何待提交改动;
  5) 派生件必须"新身份入库": 暂存的派生件摘要 != 旧执行器摘要, 且 != A 包登记的旧值;
  6) 旧执行器/旧现场原件/旧清单在暂存区里必须**一条都不出现**(它们本轮 0 改动)。

用法: python tools/r7f8e_staged_preflight.py --repo D:/ns800 [--uid-file <仓外私有 UID 文件>]
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
DRAFT = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
OLD_EXE = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
OLD_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
NEW_EXE = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
MANIFESTS = [ROUND + "/ROUND_SHA256SUMS.txt", DRAFT + "/ROUND_SHA256SUMS.txt",
             R7F2B + "/SHA256SUMS.txt"]
# 白名单一律写完整字面量, 且**每行一条、行尾带逗号**: 下面的 PREFLIGHT_ALLOWED_PARSED_EXACTLY
# 会从本文件把这些串解析出来跟 git status 的实际待提交集对账,
# 拼接形态(ROUND + "/")与最后一行(收尾括号跟在串后)都会让解析出残缺的键。
ALLOWED = (
    "reports/reviews/phase8-m8-r7f8-executor-adaptation/",
    "reports/agent_handoffs/phase8-m8-r7f8-executor-adaptation.md",
    "reports/agent_handoffs/phase8-m8-r7f8-executor-adaptation.json",
    "reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md",
    "reports/reviews/phase8-m8-r7f2b-safety-print-fix/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-r7f8-closed-loop-draft/R7F8_DRAFT_MERGED.md",
    "reports/reviews/phase8-m8-r7f8-closed-loop-draft/ROUND_SHA256SUMS.txt",
    ".gitattributes",
)
UID_DEFAULT = "D:/ns800-m7-p0-20261030T182818/dap_uid_bare.txt"
SLQ = "[" + chr(92) + chr(92) + "/]"
RE48 = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])")
REUSER = re.compile((SLQ + "Users" + SLQ).encode(), re.I)
REAPP = re.compile((SLQ + "App" + "Data" + SLQ).encode(), re.I)


class Batch(object):
    """一次 git cat-file 批量取 blob, 避免每文件起一个进程。"""

    def __init__(self, repo):
        self.repo = str(repo)
        self.cache = {}

    def get(self, spec):
        if spec not in self.cache:
            p = subprocess.run(["git", "-C", self.repo, "cat-file", "-p", spec],
                               capture_output=True)
            self.cache[spec] = p.stdout if p.returncode == 0 else None
        return self.cache[spec]


def entries(repo, man):
    out = []
    for ln in pathlib.Path(str(repo / man)).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([0-9a-f]{64})  (.+)$", ln)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--uid-file", default=UID_DEFAULT)
    a = ap.parse_args()
    repo = a.repo.resolve()
    up = pathlib.Path(a.uid_file)
    if not up.exists():
        print("PREFLIGHT_REFUSED|读不到私有 UID 文件 => UID 泄漏判据不可用, 宣布干净不作数")
        return 2
    uid = up.read_bytes().strip()
    if len(uid) != 48:
        print("PREFLIGHT_REFUSED|私有 UID 长度=%d(期望 48)" % len(uid))
        return 2
    here = pathlib.Path(__file__).read_bytes()
    if uid in here or uid.upper() in here or REUSER.search(here) or REAPP.search(here):
        print("PREFLIGHT_REFUSED|扫描器自己就是泄漏源 => 先修脚本")
        return 2
    LEAKS = [(RE48, "48位十六进制(疑似 DAP UID, 不分大小写)"), (REUSER, "用户主目录路径片段"),
             (REAPP, "漫游数据目录片段"), (re.compile(re.escape(uid)), "私有 UID 明文"),
             (re.compile(re.escape(uid.upper())), "私有 UID 大写形态")]
    B = bytes([92])
    probes = [(RE48, b"zz" + b"0123456789abcdef" * 3 + b"zz"),
              (REUSER, b"C:" + B + b"Users" + B + b"someone"),
              (REAPP, b"D:" + B + b"App" + b"Data" + B + b"x"),
              (re.compile(re.escape(uid)), b"p-" + uid + b"-s"),
              (re.compile(re.escape(uid.upper())), b"p-" + uid.upper() + b"-s")]
    fired = [bool(p.search(s)) for p, s in probes]
    print("SELFCHECK_LEAK|五类样例命中=%s 64位sha不误判为48位=%s"
          % (fired, RE48.search(b"0" * 64) is None))
    if not all(fired) or RE48.search(b"0" * 64) is not None:
        print("PREFLIGHT_REFUSED|泄漏判据不可用 => 宣布干净不作数")
        return 2

    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only",
                             "--diff-filter=ACMR"], capture_output=True).stdout \
        .decode("utf-8").split("\n")
    staged = [p.strip('"') for p in staged if p.strip('"')]
    print("STAGED|件=%d" % len(staged))
    oos = [p for p in staged if not any(p.startswith(x) for x in ALLOWED)]
    print("SCOPE|白名单外的暂存件=%d %s" % (len(oos), oos[:8]))
    parsed = re.findall(r'^\s+"((?:reports|\.)[^"]+)",\s*$', here.decode("utf-8"), re.M)
    same = sorted(parsed) == sorted(list(ALLOWED))
    lit_sync = (ALLOWED[0] == ROUND + "/" and ALLOWED[3] == R7F2B + "/run_record.md"
                and ALLOWED[4] == R7F2B + "/SHA256SUMS.txt"
                and ALLOWED[5] == DRAFT + "/R7F8_DRAFT_MERGED.md"
                and ALLOWED[6] == DRAFT + "/ROUND_SHA256SUMS.txt")
    print("PREFLIGHT_ALLOWED_PARSED_EXACTLY|解析出=%d 白名单=%d 相等=%s 字面量与常量同步=%s"
          % (len(parsed), len(ALLOWED), same, lit_sync))
    if not same or not lit_sync:
        oos = oos + ["ALLOWED_LIST_NOT_PARSEABLE_EXACTLY"]

    b = Batch(repo)
    problems, leak_hits = [], []
    for man in MANIFESTS:
        ent = entries(repo, man)
        for i, (want, rel) in enumerate(ent):
            data = b.get(":" + rel)
            if data is None:
                problems.append("missing_staged_blob %s" % rel)
                continue
            if hashlib.sha256(data).hexdigest() != want:
                problems.append("sha_mismatch %s" % rel)
            if (repo / rel).read_bytes() != data:
                problems.append("blob_not_equal_disk %s" % rel)
        corrupt = [(("0" * 64) if i == 0 else w, r) for i, (w, r) in enumerate(ent)]
        fired_man = sum(1 for w, r in corrupt
                        if b.get(":" + r) is None
                        or hashlib.sha256(b.get(":" + r)).hexdigest() != w)
        print("SELFCHECK_MANIFEST|%s|首条登记值改坏后被判断条数=%d(必须 >=1)|条目=%d" %
              (man, fired_man, len(ent)))
        if fired_man < 1:
            problems.append("manifest_comparator_dead " + man)
    for rel in [p for p in staged if not p.endswith("SHA256SUMS.txt")]:
        data = b.get(":" + rel) or b""
        for pat, why in LEAKS:
            if pat.search(data):
                leak_hits.append("%s: %s" % (why, rel))
    print("LEAK_SCAN|件=%d 命中=%d %s" % (len(staged), len(leak_hits), leak_hits[:6]))

    rc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain",
                         "rt-thread-master", "tests"], capture_output=True).stdout \
        .decode("utf-8", "replace").strip()
    print("PRODUCT_CLEAN|产品与主机链待提交改动=%d 件" % len([x for x in rc.splitlines() if x]))
    if rc:
        problems.append("PRODUCT_DIRTY")

    # 清单覆盖面: 本轮目录里每个磁盘文件要么在清单里, 要么是**声明排除的那一件**
    # (推送后才存在的回读回执)。回执本身不得入库 —— 否则清单就漏掉了一件已提交的字节。
    man_rel = set(r for _, r in entries(repo, ROUND + "/ROUND_SHA256SUMS.txt"))
    # 推送之后才存在的远端回读回执一律排除(每轮写独立文件名, 所以按后缀族匹配而不是点名单一条)
    EXCLUDED_PREFIX = ROUND + "/proof/"
    def is_receipt(x):
        return x.startswith(EXCLUDED_PREFIX) and "remote_readback" in x and x.endswith(".txt")
    SELF = ROUND + "/ROUND_SHA256SUMS.txt"
    disk_rel = set(p.relative_to(repo).as_posix() for p in (repo / ROUND).rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts)
    uncovered = sorted(x for x in disk_rel if x not in man_rel and x != SELF and not is_receipt(x))
    staged_excluded = [x for x in staged if is_receipt(x)]
    print("MANIFEST_COVERAGE|磁盘=%d 清单=%d 未覆盖=%d %s|回执已暂存=%s"
          % (len(disk_rel), len(man_rel), len(uncovered), uncovered[:5], bool(staged_excluded)))
    if uncovered or staged_excluded:
        problems.append("manifest_coverage")
    # 覆盖面判据自己的负控: 从清单里抽掉一条真实登记的件, **用同一段判定代码**再跑一次,
    # 它必须立刻出现在"未覆盖"里。(写成 `drop not in man_rel - {drop}` 是恒真, 不算负控。)
    if man_rel:
        drop = sorted(x for x in man_rel if x != SELF and not is_receipt(x))[0]
        reduced = man_rel - {drop}
        probe_uncovered = sorted(x for x in disk_rel
                                 if x not in reduced and x != SELF and not is_receipt(x))
        fired = drop in probe_uncovered
        print("SELFCHECK_COVERAGE|抽掉 %s 后重跑判定 => 未覆盖=%d 条, 抽掉的那条在其中=%s"
              % (drop[:46], len(probe_uncovered), fired))
        if not fired:
            problems.append("coverage_comparator_dead")
    else:
        problems.append("coverage_comparator_dead")

    # 派生件必须是"新身份": 等于旧摘要就是冒用, 等于 A 包登记值也是。
    new_blob = b.get(":" + NEW_EXE)
    if new_blob is None:
        problems.append("derived_executor_not_staged " + NEW_EXE)
    else:
        s = hashlib.sha256(new_blob).hexdigest()
        print("NEW_EXECUTOR_IDENTITY|bytes=%d|sha=%s|与旧件不同=%s"
              % (len(new_blob), s[:16], s != OLD_SHA))
        if s == OLD_SHA:
            problems.append("derived_executor_reuses_old_sha")
    touched_old = [p for p in staged if p == OLD_EXE
                   or p.startswith("reports/reviews/phase8-m8-r7f7")
                   or p.startswith("reports/reviews/phase8-m8-idle-stack-r4")]
    print("UNCHANGED_SCOPE|旧执行器/旧现场原件出现在暂存集=%d %s"
          % (len(touched_old), touched_old[:5]))
    if touched_old:
        problems.append("frozen_inputs_staged")
    # run_record 的 §23 必须**只出现一次**, 且旧节字节可由清单三方对账兜住。
    rr = (repo / (R7F2B + "/run_record.md")).read_text(encoding="utf-8")
    n23 = len(re.findall(r"(?m)^## 23 ", rr))
    total = len(re.findall(r"(?m)^## \d+ ", rr))
    print("RUN_RECORD_SECTIONS|总节=%d|§23 出现=%d" % (total, n23))
    if n23 != 1 or total < 23:
        problems.append("run_record_section_23_shape %d/%d" % (n23, total))

    fails = problems + leak_hits + oos
    print("SUMMARY|fails=%d %s" % (len(fails), fails[:8]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
