#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前置校验(批量流版): 逐条比较**暂存区 blob** 的 sha256 与清单登记值。

与上一轮那版 staged_preflight 的差别: 用**一个** `git cat-file --batch` 流读完所有 blob, 不再为每条登记件
起一个进程(往轮 5137 条时那个实现要跑十几分钟, 也是本轮"不重跑历史清单"的真实原因)。
另加 `--selfcheck`: 故意把一条登记值改错, 比较器必须报出来 —— 检查器要先证明自己会命中, 才允许宣布干净。
"""
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path("D:/ns800")
MANIFESTS = [
    "reports/reviews/phase8-m8-r6b-executor-binding/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-r6a-offline-rework/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-r6a-offline-rework/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-r6-offline-recovery/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-r6-offline-recovery/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-idle-stack-r4/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-idle-stack-r4/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-diag-stack-r1/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-diag-stack-r1/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-relative-protection-r3/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-relative-protection-r3/SHA256SUMS.txt",
]
BUILD_MANIFESTS = [
    "reports/firmware/phase8-m8-r6-offline-recovery/build_manifest.json",
    "reports/firmware/phase8-m8-idle-stack-r4/build_manifest.json",
    "reports/firmware/phase8-m8-diag-stack-r1/build_manifest.json",
    "reports/firmware/phase8-m8-relative-protection-r3/build_manifest.json",
]


def entries(sums_rel):
    out = []
    for ln in (ROOT / sums_rel).read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            h, _sep, rel = ln.partition("  ")
            out.append((h.strip(), rel.strip()))
    return out


class Batch(object):
    """单个 git cat-file --batch 进程, 按需取 :<path> 的 blob 字节。"""

    def __init__(self, cwd):
        self.p = subprocess.Popen(["git", "cat-file", "--batch"], cwd=str(cwd), stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)

    def get(self, spec):
        self.p.stdin.write((spec + "\n").encode("utf-8"))
        self.p.stdin.flush()
        head = self.p.stdout.readline().decode("utf-8", "replace").strip()
        parts = head.split(" ")
        # `git cat-file --batch` 每次输出 "<sha> <type> <size>" 或 "<spec> missing"
        if parts[-1] == "missing" or len(parts) < 3 or parts[1] != "blob":
            return None
        size = int(parts[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        return data

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def compare(b, pairs, corrupt_first=False):
    probs = []
    for i, (want, rel) in enumerate(pairs):
        exp = ("0" * 64 if (corrupt_first and i == 0) else want)
        data = b.get(":" + rel)
        if data is None:
            probs.append("missing_blob %s" % rel)
            continue
        got = hashlib.sha256(data).hexdigest()
        if got != exp:
            probs.append("sha_mismatch %s want=%s got=%s" % (rel, exp[:16], got[:16]))
    return probs


def main():
    b = Batch(ROOT)
    total = 0
    try:
        sc = compare(b, entries(MANIFESTS[0]), corrupt_first=True)
        print("SELFCHECK(corrupted first entry) problems=%d %s  (必须>=1)" % (len(sc), sc[:1]))
        if not sc:
            print("STAGED_PREFLIGHT_REFUSED: 比较器对错误的登记值不报 => 判据恒真, 结果不作数")
            return 2
        index = {}
        for sums in MANIFESTS:
            es = entries(sums)
            probs = compare(b, es)
            print("MANIFEST %s entries=%d problems=%d %s" % (sums, len(es), len(probs), probs[:3]))
            total += len(probs)
            for want, rel in es:
                index[want] = rel
        for bm in BUILD_MANIFESTS:
            d = json.loads((ROOT / bm).read_text(encoding="utf-8"))
            want = []

            def collect(item, key):
                if isinstance(item, dict) and "sha256" in item:
                    want.append((item["sha256"], item.get("name", key), item.get("bytes")))
                elif isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, dict) and "sha256" in v:
                            want.append((v["sha256"], k, v.get("bytes")))

            for t in d["targets"]:
                for key in ("images", "maps", "image", "map"):
                    v = t.get(key)
                    if isinstance(v, list):
                        for it in v:
                            collect(it, key)
                    elif isinstance(v, dict):
                        collect(v, key)
                if "log_sha256" in t:
                    want.append((t["log_sha256"], t.get("log_archive_path", "log"), t["log_bytes"]))
            probs, ok = [], 0
            for s, name, n in want:
                rel = index.get(s)
                if rel is None:
                    probs.append("unlisted_in_manifests %s" % name)
                    continue
                data = b.get(":" + rel)
                if data is None:
                    probs.append("missing_blob %s" % rel)
                elif n is not None and len(data) != n:
                    probs.append("size_mismatch %s declared=%d blob=%d" % (rel, n, len(data)))
                else:
                    ok += 1
            print("BUILD_MANIFEST %s declared=%d resolved=%d problems=%d %s"
                  % (bm, len(want), ok, len(probs), probs[:3]))
            total += len(probs)
    finally:
        b.close()
    print("STAGED_PREFLIGHT_TOTAL problems=%d  (0 => 每个登记件的暂存 blob 都与清单逐字节相符)" % total)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
