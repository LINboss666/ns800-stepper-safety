# -*- coding: utf-8 -*-
"""提交前置校验: 本轮清单逐条对账 登记值 == 暂存 blob == 磁盘字节, 外加泄漏扫描。

纪律: 检查器先证明自己会命中(第一条登记值故意改错必须被报), 才允许它宣布干净。
用法: python tools/round_preflight_demo_v03.py --repo <仓库根> --dir <本轮目录>
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
    a = ap.parse_args()
    repo = a.repo.resolve()
    sums = (a.dir / "SHA256SUMS.txt").resolve()
    rel_sums = sums.relative_to(repo).as_posix()
    entries = []
    for ln in sums.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            h, _sep, rel = ln.partition("  ")
            entries.append((h.strip(), rel.strip()))
    print("MANIFEST|%s|条目=%d" % (rel_sums, len(entries)))

    b = Batch(repo)
    problems, leak_hits = [], []
    probe = b"ab" * 24  # 48 位十六进制样例
    # 样例本身拼出来写, 否则这份扫描器会被自己的样例判成泄漏件
    user_path_probe = b"C:" + b"\\Users\\someone\\x"
    token_probe = b"CONFIRM " + b"Z" * 20
    fired = all(re.search(p, s) for p, s in
                [(LEAKS[0][0], probe), (LEAKS[1][0], user_path_probe), (LEAKS[2][0], token_probe)])
    # 反向对照: 64 位 sha256 不得被当成 48 位 UID, 否则每条证据散列都会变成假报警
    false_fire = re.search(LEAKS[0][0], b"0" * 64) is not None
    print("SELFCHECK_LEAK|三类样例必须全部命中: %s | 64位sha必须不被误判: %s"
          % ("FIRED" if fired else "**NOT-FIRED(扫描器失效)**",
             "OK" if not false_fire else "**假报警(边界未写对)**"))
    if not fired or false_fire:
        print("PREFLIGHT_REFUSED: 泄漏扫描器判据不可用 => 宣布干净不作数")
        return 2
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
                disk = (repo / rel).read_bytes()
                if disk != data:
                    probs.append("blob_not_equal_disk %s (行尾被改写? 检查 -text 规则)" % rel)
            return probs

        sc = compare(entries[:1], corrupt_first=True)
        print("SELFCHECK(corrupted first entry) problems=%d %s (必须>=1)" % (len(sc), sc[:1]))
        if not sc:
            print("PREFLIGHT_REFUSED: 比较器对错误登记值不报 => 判据恒真, 结果不作数")
            return 2
        problems = compare(entries)
        for _want, rel in entries:
            data = b.get(":" + rel) or b""
            for pat, why in LEAKS:
                if re.search(pat, data):
                    leak_hits.append("%s: %s" % (why, rel))
    finally:
        b.close()
    print("LEAK_SCAN|命中=%d %s" % (len(leak_hits), leak_hits[:6]))
    print("PREFLIGHT|problems=%d %s" % (len(problems), problems[:6]))
    print("SUMMARY|fails=%d" % (len(problems) + len(leak_hits)))
    return 1 if (problems or leak_hits) else 0


if __name__ == "__main__":
    sys.exit(main())
