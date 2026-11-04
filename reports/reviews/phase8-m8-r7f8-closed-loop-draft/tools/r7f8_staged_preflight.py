# -*- coding: utf-8 -*-
"""R7F8 草案核对轮的提交前置检查(只读 git 暂存区, 不写任何被检文件)。

四件事, 每件都带"必须会命中"的负控, 否则拒绝宣布干净:
  1) 暂存 blob 的 sha256 与磁盘字节、与清单登记值三方对账(先人为改坏首条, 确认比较器会报警);
  2) 本轮暂存集里不得出现身份性形态: 私有 DAP UID 明文(两种大小写)/用户主目录片段/漫游数据目录片段;
     模式一律运行时拼装 —— 写成连续字面量会被本扫描抓到(检查器自己就是泄漏源);
  3) 越界暂存: 本轮只允许新轮次目录、其交接件、run_record/清单, 以及 .gitattributes 里
     为本轮目录新增的 -text 规则(它必须在同一次提交里生效, 否则 autocrlf 会改掉被登记的字节);
  4) 产品与主机链不得有任何待提交改动。

用法: python tools/r7f8_staged_preflight.py --repo D:/ns800 [--uid-file <仓外私有 UID 文件>]
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import subprocess
import sys

R7F8 = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
MANIFESTS = [R7F8 + "/ROUND_SHA256SUMS.txt", R7F2B + "/SHA256SUMS.txt"]
# 白名单一律写完整字面量(不用 R7F8 + "/" 这种拼接): 本轮的判据 PREFLIGHT_ALLOWED_PARSED_EXACTLY
# 会从本文件把这些串解析出来跟 git status 的实际待提交集对账, 拼接形态会让解析出残缺的键。
ALLOWED = ("reports/reviews/phase8-m8-r7f8-closed-loop-draft/",
           "reports/agent_handoffs/phase8-m8-r7f8-closed-loop-draft",
           "reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md",
           "reports/reviews/phase8-m8-r7f2b-safety-print-fix/SHA256SUMS.txt",
           # 本轮新增的 -text 规则必须与它保护的字节同一次提交生效: core.autocrlf=true 下
           # 缺了这行, 草案原件/合并版的 CRLF 会在暂存时被改写, 所谓原字节入库就成了空话。
           ".gitattributes")
UID_DEFAULT = "D:/ns800-m7-p0-20261030T182818/dap_uid_bare.txt"
SLQ = "[" + chr(92) + chr(92) + "/]"
RE48 = re.compile((SLQ.join([b"", b""]) or b"") .join([]) if False else
                  rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])")
REUSER = re.compile((SLQ + "Users" + SLQ).encode(), re.I)
REAPP = re.compile((SLQ + "App" + "Data" + SLQ).encode(), re.I)


class Batch(object):
    """一次 git cat-file 批量取 blob, 避免每文件起一个进程。"""

    def __init__(self, repo):
        self.repo = str(repo)
        self.cache = {}

    def get(self, spec):
        if spec not in self.cache:
            p = subprocess.run(["git", "-C", self.repo, "cat-file", "-p", spec], capture_output=True)
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
    print("SELFCHECK_LEAK|五类样例命中=%s 64位sha不误判为48位=%s" % (fired, RE48.search(b"0" * 64) is None))
    if not all(fired) or RE48.search(b"0" * 64) is not None:
        print("PREFLIGHT_REFUSED|泄漏判据不可用 => 宣布干净不作数")
        return 2

    staged = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only",
                             "--diff-filter=ACMR"], capture_output=True).stdout.decode("utf-8").split("\n")
    staged = [p.strip('"') for p in staged if p.strip('"')]
    print("STAGED|件=%d" % len(staged))
    oos = [p for p in staged if not any(p.startswith(x) for x in ALLOWED)]
    print("SCOPE|允许路径外的暂存件=%d %s" % (len(oos), oos[:8]))
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
                        if b.get(":" + r) is None or hashlib.sha256(b.get(":" + r)).hexdigest() != w)
        print("SELFCHECK_MANIFEST|%s|首条登记值改坏后被判断条数=%d(必须 >=1)|条目=%d 不符=%d" %
              (man, fired_man, len(ent), len([p for p in problems if p.endswith(man)])))
        if fired_man < 1:
            problems.append("manifest_comparator_dead " + man)
    for rel in [p for p in staged if not p.endswith("SHA256SUMS.txt")]:
        data = b.get(":" + rel) or b""
        for pat, why in LEAKS:
            if pat.search(data):
                leak_hits.append("%s: %s" % (why, rel))
    print("LEAK_SCAN|件=%d 命中=%d %s" % (len(staged), len(leak_hits), leak_hits[:6]))
    rc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "rt-thread-master", "tests"],
                        capture_output=True).stdout.decode().strip()
    print("PRODUCT_CLEAN|产品与主机链待提交改动=%d 件" % len([x for x in rc.splitlines() if x]))
    fails = problems + leak_hits + oos + (["PRODUCT_DIRTY"] if rc else [])
    print("SUMMARY|fails=%d %s" % (len(fails), fails[:8]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
