# -*- coding: utf-8 -*-
"""提交前置校验(本轮专用): 逐条比较**暂存 blob** 与清单登记值与磁盘字节, 泄漏扫描只判本轮暂存集。

为什么扫描要限定范围: 历史轮的证据件里已有 `C:\\Users\\<用户名>\\Downloads\\...` 这样的行(见
historical_hits 输出), 那些件是冻结件, 本轮不改也不该被本轮的门 retroactively 判红;
但也不许因此把扫描器闭嘴 —— 所以两件事分开做: 本轮暂存集判红, 历史命中如实登记。

检查器先证明自己会命中(第一条登记值故意改错必须被报; 三类泄漏样例必须全部命中; 64 位 sha 不得被
当成 48 位 UID), 否则宣布干净不作数。

用法: python tools/r7d2_staged_preflight.py --repo <仓库根> --dir <本轮目录> [--scan-all]
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

LEAKS = [(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])", "48位十六进制(疑似 DAP UID)"),
         (rb"[Cc]:[\\/][Uu]sers[\\/][^\s\"']+", "用户目录绝对路径(含本机用户名)"),
         (rb"CONFIRM\s+[0-9A-Za-z]{16,}", "疑似批准令牌原文")]


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
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--scan-all", action="store_true", help="连清单里所有登记件一起扫(默认只扫本轮暂存集)")
    a = ap.parse_args()
    repo = a.repo.resolve()
    sums = (repo / a.dir).resolve() / "SHA256SUMS.txt"
    entries = []
    for ln in sums.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            h, _sep, rel = ln.partition("  ")
            entries.append((h.strip(), rel.strip()))
    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                            cwd=str(repo), capture_output=True, text=True).stdout.split("\n")
    staged = [p.strip('"') for p in staged if p.strip('"')]
    print("MANIFEST|%s|条目=%d 本轮暂存=%d" % (sums.relative_to(repo).as_posix(), len(entries), len(staged)))

    b = Batch(repo)
    problems, leak_hits, historical = [], [], []
    try:
        def compare(pairs, corrupt_first=False):
            probs = []
            for i, (want, rel) in enumerate(pairs):
                exp = ("0" * 64 if (corrupt_first and i == 0) else want)
                data = b.get(":" + rel)
                if data is None:
                    probs.append("missing_blob %s" % rel)
                    continue
                got = hashlib.sha256(data).hexdigest()
                if got != exp:
                    probs.append("sha_mismatch %s want=%s got=%s" % (rel, exp[:12], got[:12]))
                if (repo / rel).read_bytes() != data:
                    probs.append("blob_not_equal_disk %s (行尾被改写? 检查 -text 规则)" % rel)
            return probs

        samples = [(LEAKS[0][0], b"ab" * 24),
                   (LEAKS[1][0], b"C:" + b"\\Users\\someone\\x"),
                   (LEAKS[2][0], b"CONFIRM " + b"Z" * 20)]
        fired = all(re.search(p, s) for p, s in samples)
        false_fire = re.search(LEAKS[0][0], b"0" * 64) is not None
        print("SELFCHECK_LEAK|三类样例全命中=%s 64位sha不误判=%s" % (fired, not false_fire))
        if not fired or false_fire:
            print("PREFLIGHT_REFUSED: 泄漏扫描判据不可用 => 宣布干净不作数")
            return 2
        sc = compare(entries[:1], corrupt_first=True)
        print("SELFCHECK(corrupted first entry) problems=%d %s (必须>=1)" % (len(sc), sc[:1]))
        if not sc:
            print("PREFLIGHT_REFUSED: 比较器对错误登记值不报 => 判据恒真, 结果不作数")
            return 2
        problems = compare(entries)
        scan_set = [rel for _w, rel in entries] if a.scan_all else \
                   [p for p in staged if p not in ("SHA256SUMS.txt",)]
        for rel in scan_set:
            data = b.get(":" + rel) or b""
            for pat, why in LEAKS:
                if re.search(pat, data):
                    leak_hits.append("%s: %s" % (why, rel))
        for want, rel in entries:
            if rel in staged:
                continue
            data = b.get(":" + rel) or b""
            for pat, why in LEAKS:
                if re.search(pat, data):
                    historical.append("%s: %s" % (why, rel))
    finally:
        b.close()
    print("LEAK_SCAN(本轮暂存集)|件=%d 命中=%d %s" % (len(scan_set), len(leak_hits), leak_hits[:6]))
    print("HISTORICAL_HITS(冻结历史件, 未改动, 只登记)|命中=%d %s"
          % (len(historical), historical[:6]))
    print("PREFLIGHT|problems=%d %s" % (len(problems), problems[:6]))
    print("SUMMARY|fails=%d" % (len(problems) + len(leak_hits)))
    return 1 if (problems or leak_hits) else 0


if __name__ == "__main__":
    sys.exit(main())
