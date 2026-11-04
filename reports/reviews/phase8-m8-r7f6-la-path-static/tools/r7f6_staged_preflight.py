# -*- coding: utf-8 -*-
"""提交前置校验(R7F6): 逐条比较**暂存 blob** 与清单登记值与磁盘字节, 泄漏扫描只判本轮暂存集。

两条清单都要核: 本轮目录 ROUND_SHA256SUMS.txt 与 r7f2b 的 SHA256SUMS.txt(228 条, 本轮只因
run_record §19 而重算)。扫描范围限定在本轮暂存集: 历史轮的冻结证据里已有本机绝对路径之类的行,
那些件本轮不改, 也不许被本轮的门 retroactively 判红; 但扫描器也不许闭嘴 —— 所以两件事分开做。

判据先自证会命中才算数:
  * 每条清单的首个登记值故意改错, 比较器必须判不符
  * 五类泄漏样例必须全部命中(48 位十六进制 / C:\\Users 绝对路径 / CONFIRM 令牌 /
    仪器 deviceId / 仪器序列号), 且 64 位 sha 不得被当成 48 位 UID
  * 两个仪器标识**不硬编码在本脚本里**(否则扫描器自己就是泄漏源), 而是从仓外原件读出后构造判据;
    读不到就直接拒绝运行, 不静默降级
  * 范围门: 暂存集里出现本轮不该动的路径(产品源码、主机链、旧执行器、旧证据件)判红

用法: python tools/r7f6_staged_preflight.py --repo D:/ns800 [--priv 仓外原件根] [--scan-all]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import zipfile

R7F6 = "reports/reviews/phase8-m8-r7f6-la-path-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
MANIFESTS = [R7F6 + "/ROUND_SHA256SUMS.txt", R7F2B + "/SHA256SUMS.txt"]
# 本轮允许改动的路径前缀(其余一律判红)
ALLOWED = (R7F6 + "/", "reports/agent_handoffs/phase8-m8-r7f6-la-path-static.",
           R7F2B + "/run_record.md", R7F2B + "/SHA256SUMS.txt", ".gitattributes")
PRIV_DEFAULT = "D:/ns800_r7f1_scratch/r7f6_field"


def load_secrets(priv_dir):
    """从仓外未脱敏原件读出仪器明文标识, 返回 [(值, 说明)]。读不到 => 直接抛错, 不降级。"""
    root = pathlib.Path(priv_dir)
    dev = json.loads((root / "la_devices.json").read_text(encoding="utf-8"))
    did = str(dev["response"]["result"]["structuredContent"]["devices"][0]["deviceId"])
    sal = json.loads(zipfile.ZipFile(str(root / "native" / "capture_id1.sal"))
                     .read("meta.json"))
    ser = str(sal["data"]["legacyDevice"]["deviceId"])
    return [(did, "Saleae deviceId 明文(仪器标识)"), (ser, "Saleae 仪器序列号明文(20 位十进制)")]


def build_leaks(secrets):
    out = [(re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])"), "48位十六进制(疑似 DAP UID)"),
           (re.compile(rb"[Cc]:[\\/][Uu]sers[\\/][^\s\"']+", re.I), "用户目录绝对路径(含本机用户名)"),
           (re.compile(rb"CONFIRM\s+[0-9A-Za-z]{16,}"), "疑似批准令牌原文")]
    for val, why in secrets:
        out.append((re.compile(re.escape(val.encode())), why))
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


def entries(repo, man):
    out = []
    for ln in (repo / man).read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            h, _s, rel = ln.partition("  ")
            out.append((h.strip(), rel.strip()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--priv", default=PRIV_DEFAULT, help="仓外未脱敏原件根(读仪器明文标识用)")
    ap.add_argument("--scan-all", action="store_true", help="连清单里所有登记件一起扫(默认只扫本轮暂存集)")
    a = ap.parse_args()
    repo = a.repo.resolve()
    try:
        secrets = load_secrets(a.priv)
    except Exception as e:
        print("PREFLIGHT_REFUSED|读不到仓外原件 %s => 仪器标识判据不可用, 宣布干净不作数: %r" % (a.priv, e))
        return 2
    LEAKS = build_leaks(secrets)

    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                            cwd=str(repo), capture_output=True).stdout.decode("utf-8").split("\n")
    staged = [p.strip('"') for p in staged if p.strip('"')]
    print("STAGED|件=%d" % len(staged))
    out_of_scope = [p for p in staged if not any(p.startswith(x) for x in ALLOWED)]
    print("SCOPE|本轮允许路径外的暂存件=%d %s" % (len(out_of_scope), out_of_scope[:8]))

    b = Batch(repo)
    problems, leak_hits, historical, selfchecks = [], [], [], []
    try:
        # 自检 1: 五类样例必须命中; 64 位 sha 不得被当成 48 位 UID
        samples = [re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])"),
                   re.compile(rb"[Cc]:[\\/][Uu]sers[\\/][^\s\"']+", re.I),
                   re.compile(rb"CONFIRM\s+[0-9A-Za-z]{16,}")]
        probe = [b"ab" * 24, b"C:" + b"\\Users\\someone\\x", b"CONFIRM " + b"Z" * 20]
        fired = [bool(p.search(s)) for p, s in zip(samples, probe)]
        for (pat, _why), (val, _w2) in zip(LEAKS[3:], secrets):
            fired.append(bool(pat.search(b"prefix-" + val.encode() + b"-suffix")))
        false_fire = samples[0].search(b"0" * 64) is not None
        print("SELFCHECK_LEAK|五类样例命中=%s 64位sha不误判=%s" % (fired, not false_fire))
        if not all(fired) or false_fire:
            print("PREFLIGHT_REFUSED: 泄漏扫描判据不可用 => 宣布干净不作数")
            return 2
        # 自检 2: 本脚本自身不得含任何被扫的明文(扫描器不能自己就是泄漏源)
        here = pathlib.Path(__file__).read_bytes()
        self_leak = [w for val, w in secrets if val.encode() in here]
        print("SELFCHECK_SCANNER_CLEAN|扫描器自身命中=%d %s" % (len(self_leak), self_leak))
        if self_leak:
            print("PREFLIGHT_REFUSED: 扫描器自带明文标识 => 先修脚本")
            return 2

        def compare(man, ent, corrupt_first=False):
            probs = []
            for i, (want, rel) in enumerate(ent):
                exp = ("0" * 64 if (corrupt_first and i == 0) else want)
                data = b.get(":" + rel)
                if data is None:
                    probs.append("missing_staged_blob %s" % rel)
                    continue
                got = hashlib.sha256(data).hexdigest()
                if got != exp:
                    probs.append("sha_mismatch %s want=%s got=%s" % (rel, exp[:12], got[:12]))
                if (repo / rel).read_bytes() != data:
                    probs.append("blob_not_equal_disk %s (行尾被改写? 检查 -text 规则)" % rel)
            return probs

        for man in MANIFESTS:
            ent = entries(repo, man)
            if b.get(":" + ent[0][1]) is None:
                print("PREFLIGHT_REFUSED|清单 %s 首件未暂存: %s" % (man, ent[0][1]))
                return 2
            sc = compare(man, ent, corrupt_first=True)
            selfchecks.append(("%s corrupt-first" % man, bool(sc)))
            print("SELFCHECK_MANIFEST|%s|首条登记值改错后被判不符=%d %s" % (man, len(sc), sc[:1]))
            if not sc:
                print("PREFLIGHT_REFUSED: 比较器对错误登记值不判红 => 恒真, 结果不作数")
                return 2
            p2 = compare(man, ent)
            problems += p2
            print("MANIFEST|%s|条目=%d 不符=%d" % (man, len(ent), len(p2)))

        scan_set = sorted({r for m in MANIFESTS for _h, r in entries(repo, m)}) if a.scan_all else \
                   [p for p in staged if not p.endswith("SHA256SUMS.txt")]
        for rel in scan_set:
            data = b.get(":" + rel) or b""
            for pat, why in LEAKS:
                if pat.search(data):
                    leak_hits.append("%s: %s" % (why, rel))
        print("LEAK_SCAN(%s)|件=%d 命中=%d %s"
              % ("全部登记件" if a.scan_all else "本轮暂存集", len(scan_set), len(leak_hits), leak_hits[:8]))
        for man in MANIFESTS:
            for _h, rel in entries(repo, man):
                if rel in scan_set:
                    continue
                data = b.get(":" + rel) or b""
                for pat, why in LEAKS:
                    if pat.search(data):
                        historical.append("%s: %s" % (why, rel))
        print("HISTORICAL_HITS(冻结历史件, 未改动, 只登记)|命中=%d %s"
              % (len(historical), historical[:8]))
    finally:
        b.close()

    print("SELFCHECK_COMPARATOR|%s (必须全 True)" % selfchecks)
    if not all(f for _n, f in selfchecks):
        print("PREFLIGHT_REFUSED: 比较器对错误登记值不判红 => 恒真, 结果不作数")
        return 2
    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain",
                            "rt-thread-master", "tests"], capture_output=True).stdout.decode("utf-8").strip()
    print("PRODUCT_CLEAN|产品与主机链待提交改动=%d 件" % len([l for l in dirty.splitlines() if l]))
    fails = len(problems) + len(leak_hits) + len(out_of_scope)
    print("PREFLIGHT|problems=%d %s" % (len(problems), problems[:8]))
    print("SUMMARY|fails=%d (登记/字节=%d 本轮泄漏=%d 越界暂存=%d)"
          % (fails, len(problems), len(leak_hits), len(out_of_scope)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
