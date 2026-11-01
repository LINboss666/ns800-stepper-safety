#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前置校验(diag 栈修复轮): 逐条比较 **暂存区 blob** 的 sha256/字节数 与清单登记值。

判据是 blob 而不是磁盘, 因为本机 core.autocrlf=true 会在 git add 时改写文本字节; 交付方最终
从远端读回的就是 blob。范围 = 本轮两份新清单 + R3 两份清单(作为回归门, 证明本轮没碰动已冻结件)。
"""
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path("D:/ns800")
MANIFESTS = [
    "reports/reviews/phase8-m8-diag-stack-r1/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-diag-stack-r1/SHA256SUMS.txt",
    "reports/reviews/phase8-m8-relative-protection-r3/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-relative-protection-r3/SHA256SUMS.txt",
]
BUILD_MANIFESTS = [
    "reports/firmware/phase8-m8-diag-stack-r1/build_manifest.json",
    "reports/firmware/phase8-m8-relative-protection-r3/build_manifest.json",
]


def blob(rel):
    p = subprocess.run(["git", "cat-file", "blob", ":" + rel], cwd=ROOT, capture_output=True)
    return None if p.returncode else p.stdout


def entries(sums_rel):
    out = []
    text = (ROOT / sums_rel).read_text(encoding="utf-8")
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        h, _sep, rel = ln.partition("  ")
        out.append((h.strip(), rel.strip()))
    return out


total = 0
for sums in MANIFESTS:
    probs = []
    es = entries(sums)
    for want_sha, rel in es:
        b = blob(rel)
        if b is None:
            probs.append("missing_blob %s" % rel)
            continue
        got = hashlib.sha256(b).hexdigest()
        if got != want_sha:
            probs.append("sha_mismatch %s want=%s got=%s" % (rel, want_sha[:16], got[:16]))
        elif len(b) != int(pathlib.Path(rel).stat().st_size) and pathlib.Path(rel).exists():
            probs.append("size_mismatch %s" % rel)
    print("MANIFEST %s entries=%d problems=%d %s" % (sums, len(es), len(probs), probs[:3]))
    total += len(probs)

# build_manifest 里登记的 images/maps/日志: 用两份清单当 "sha -> 仓内路径" 索引, 再从 blob 复算
index = {}
for sums in MANIFESTS:
    for want_sha, rel in entries(sums):
        index[want_sha] = rel
for bm in BUILD_MANIFESTS:
    d = json.loads((ROOT / bm).read_text(encoding="utf-8"))
    want = []
    for t in d["targets"]:
        for key in ("images", "maps"):
            for it in t.get(key, []):
                want.append((it["sha256"], it["name"], it["bytes"]))
        if "log_sha256" in t:
            want.append((t["log_sha256"], t.get("log_archive_path", "log"), t["log_bytes"]))
    probs, ok = [], 0
    for s, name, n in want:
        rel = index.get(s)
        if rel is None:
            probs.append("unlisted_in_manifests %s" % name)
            continue
        b = blob(rel)
        if b is None:
            probs.append("missing_blob %s" % rel)
        elif len(b) != n:
            probs.append("size_mismatch %s declared=%d blob=%d" % (rel, n, len(b)))
        else:
            ok += 1
    print("BUILD_MANIFEST %s declared=%d resolved=%d problems=%d %s" % (
        bm, len(want), ok, len(probs), probs[:3]))
    total += len(probs)

print("STAGED_PREFLIGHT_TOTAL problems=%d  (0 => 每个登记件的 Git blob 都与清单逐字节相符)" % total)
sys.exit(0 if total == 0 else 1)
