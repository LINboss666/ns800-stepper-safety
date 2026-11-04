# -*- coding: utf-8 -*-
"""提交前置校验(R7F7 离线轮): 暂存 blob == 清单登记值 == 磁盘字节, 泄漏扫描只判本轮暂存集。

两条清单都要核: 本轮 ROUND_SHA256SUMS.txt 与 r7f2b 的 SHA256SUMS.txt(228 条, 本轮只因
run_record §20 而重算 => 另加一条"条目名集合不得漂移"的判据, 防止我顺手扩大/缩小清单)。

判据先自证会命中才算数:
  * 每条清单的首个登记值故意改错, 比较器必须判不符
  * 路径片段与 48 位十六进制几类样例必须全部命中, 且 64 位 sha 不得被当成 48 位 UID
  * 私有 DAP UID **不硬编码在本脚本里**(否则扫描器自己就是泄漏源), 而是从仓外原件读出后构造判据;
    读不到就直接拒绝运行, 不静默降级
  * 范围门: 暂存集里出现本轮不该动的路径(产品源码、主机链、旧执行器、旧证据件)判红

用法: python tools/r7f7_staged_preflight.py --repo D:/ns800 [--uid-file <仓外私有 UID 文件>]
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import subprocess
import sys

R7F7 = "reports/reviews/phase8-m8-r7f7-newimage-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
MANIFESTS = [R7F7 + "/ROUND_SHA256SUMS.txt", R7F2B + "/SHA256SUMS.txt"]
ALLOWED = (R7F7 + "/", "reports/agent_handoffs/phase8-m8-r7f7-newimage-static",
           R7F2B + "/run_record.md", R7F2B + "/SHA256SUMS.txt", ".gitattributes")
UID_DEFAULT = "D:/ns800-m7-p0-20261030T182818/dap_uid_bare.txt"
# 模式串用 chr(92) 现场拼: 直接把 [\\/]Users[\\/] 写进源码, 本扫描会先抓到脚本自己。
SLQ = "[" + chr(92) + chr(92) + "/]"
RE48 = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])")
REUSER = re.compile((SLQ + "Users" + SLQ).encode(), re.I)
REAPP = re.compile((SLQ + "AppData" + SLQ).encode(), re.I)


def entries(repo, man):
    out = []
    for ln in (repo / man).read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            h, _s, rel = ln.partition("  ")
            out.append((h.strip(), rel.strip()))
    return out


class Batch:
    def __init__(self, cwd):
        self.p = subprocess.Popen(["git", "cat-file", "--batch"], cwd=str(cwd),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def get(self, spec):
        self.p.stdin.write((spec + "\n").encode("utf-8"))
        self.p.stdin.flush()
        head = self.p.stdout.readline().decode("utf-8", "replace").strip().split(" ")
        if head[-1] == "missing" or len(head) < 3 or head[1] != "blob":
            return None
        data = self.p.stdout.read(int(head[2]))
        self.p.stdout.read(1)
        return data

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--uid-file", default=UID_DEFAULT, help="仓外私有 DAP UID 文件(读明文构造判据用)")
    a = ap.parse_args()
    repo = a.repo.resolve()
    up = pathlib.Path(a.uid_file)
    if not up.exists():
        print("PREFLIGHT_REFUSED|读不到私有 UID 文件 %s => UID 泄漏判据不可用, 宣布干净不作数" % a.uid_file)
        return 2
    uid = up.read_bytes().strip()
    if len(uid) != 48:
        print("PREFLIGHT_REFUSED|私有 UID 长度=%d(期望 48), 不拿它当判据" % len(uid))
        return 2
    here = pathlib.Path(__file__).read_bytes()
    scanner_dirty = (uid in here) or bool(REUSER.search(here)) or bool(REAPP.search(here))
    print("SELFCHECK_SCANNER_CLEAN|扫描器自身含 UID 明文/用户目录/AppData 片段=%s" % scanner_dirty)
    if scanner_dirty:
        print("PREFLIGHT_REFUSED|扫描器自己就是泄漏源 => 先修脚本, 不带着它宣布干净")
        return 2
    LEAKS = [(RE48, "48位十六进制(疑似 DAP UID, 不分大小写)"), (REUSER, "用户主目录路径片段"),
             (REAPP, "AppData 路径片段"), (re.compile(re.escape(uid)), "私有 DAP UID 明文(从仓外原件构造)"),
             (re.compile(re.escape(uid.upper())), "私有 DAP UID 明文的大写形态(OS 观察到的复合序列号用大写)")]

    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                            cwd=str(repo), capture_output=True).stdout.decode("utf-8").split("\n")
    staged = [p.strip('"') for p in staged if p.strip('"')]
    print("STAGED|件=%d" % len(staged))
    oos = [p for p in staged if not any(p.startswith(x) for x in ALLOWED)]
    print("SCOPE|本轮允许路径外的暂存件=%d %s" % (len(oos), oos[:8]))

    b = Batch(repo)
    problems, leak_hits, schecks = [], [], []
    try:
        # 自检 1: 三类通用模式 + UID 明文都必须命中合成样本; 64 位 sha 不能被当成 48 位 UID
        # 合成样本用字节码拼装: 写成字面量会被本扫描抓到(扫描器自己就是泄漏源),
        # 那样就分不清"证据里有"还是"检查器里有"。
        B = bytes([92])
        probes = [(RE48, b"zz" + b"0123456789abcdef" * 3 + b"zz"),
                  (REUSER, b"C:" + B + b"Users" + B + b"someone"),
                  (REAPP, b"D:" + B + b"AppData" + B + b"Roaming"),
                  (re.compile(re.escape(uid)), b"prefix-" + uid + b"-suffix"),
                  (re.compile(re.escape(uid.upper())), b"prefix-" + uid.upper() + b"-suffix")]
        fired = [bool(p.search(s)) for p, s in probes]
        false_fire = RE48.search(b"0" * 64) is not None
        print("SELFCHECK_LEAK|五类样例命中=%s 64位sha不误判为48位=%s" % (fired, not false_fire))
        if not all(fired) or false_fire:
            print("PREFLIGHT_REFUSED|泄漏判据不可用(有不命中的, 或把 sha 当 UID) => 宣布干净不作数")
            return 2

        def compare(man, ent, corrupt_first=False):
            pr = []
            for i, (want, rel) in enumerate(ent):
                exp = ("0" * 64 if (corrupt_first and i == 0) else want)
                data = b.get(":" + rel)
                if data is None:
                    pr.append("missing_staged_blob %s" % rel)
                    continue
                got = hashlib.sha256(data).hexdigest()
                if got != exp:
                    pr.append("sha_mismatch %s want=%s got=%s" % (rel, exp[:12], got[:12]))
                if (repo / rel).read_bytes() != data:
                    pr.append("blob_not_equal_disk %s (行尾被改写? 检查 -text 规则)" % rel)
            return pr

        for man in MANIFESTS:
            ent = entries(repo, man)
            if b.get(":" + ent[0][1]) is None:
                print("PREFLIGHT_REFUSED|清单 %s 首件未暂存: %s" % (man, ent[0][1]))
                return 2
            sc = compare(man, ent, corrupt_first=True)
            schecks.append((man + " corrupt-first", bool(sc)))
            print("SELFCHECK_MANIFEST|%s|首条登记值改错后被判不符=%d %s" % (man, len(sc), sc[:1]))
            p2 = compare(man, ent)
            problems += p2
            print("MANIFEST|%s|条目=%d 不符=%d" % (man, len(ent), len(p2)))

        # r7f2b 清单: 本轮只该因为 run_record.md 改字节, 条目名集合不许漂移
        old_txt = subprocess.run(["git", "show", "HEAD:%s" % (R7F2B + "/SHA256SUMS.txt")],
                                 cwd=str(repo), capture_output=True).stdout.decode("utf-8")
        pairs = re.findall(r"^([0-9a-f]{64})  (.+)$", old_txt, re.M)
        old_names = set(rel for _h, rel in pairs)
        new_names = {r for _h, r in entries(repo, R7F2B + "/SHA256SUMS.txt")}
        print("SUMS_SET_UNCHANGED_R7F2B|旧=%d 新=%d 只增=%s 只删=%s"
              % (len(old_names), len(new_names), sorted(new_names - old_names)[:5],
                 sorted(old_names - new_names)[:5]))
        if new_names != old_names:
            problems.append("r7f2b 清单条目集合漂移")

        scan_set = [p for p in staged if not p.endswith("SHA256SUMS.txt")]
        for rel in scan_set:
            data = b.get(":" + rel) or b""
            for pat, why in LEAKS:
                if pat.search(data):
                    leak_hits.append("%s: %s" % (why, rel))
        print("LEAK_SCAN(本轮暂存集)|件=%d 命中=%d %s" % (len(scan_set), len(leak_hits), leak_hits[:8]))
    finally:
        b.close()

    print("SELFCHECK_COMPARATOR|%s (必须全 True)" % schecks)
    if not all(f for _n, f in schecks):
        print("PREFLIGHT_REFUSED|比较器对错误登记值不判红 => 恒真, 结果不作数")
        return 2
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain",
                            "rt-thread-master", "tests"], capture_output=True).stdout.decode("utf-8").strip()
    print("PRODUCT_CLEAN|产品与主机链待提交改动=%d 件" % len([l for l in dirty.splitlines() if l]))
    fails = len(problems) + len(leak_hits) + len(oos)
    print("PREFLIGHT|problems=%d %s" % (len(problems), problems[:8]))
    print("SUMMARY|fails=%d (登记/字节=%d 本轮泄漏=%d 越界暂存=%d)"
          % (fails, len(problems), len(leak_hits), len(oos)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
