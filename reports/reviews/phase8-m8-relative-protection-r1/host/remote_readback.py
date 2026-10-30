#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远端逐 blob 回读: 在新建的独立仓里用 git 协议从 GitHub 拉一次 shallow 包,
   然后只用**从远端取回的字节**复算 sha256 并与远端清单逐条比对。
   不读本地工作树, 不读本地对象库(新仓里本来就没有对象)。"""
import hashlib
import io
import os
import subprocess
import sys

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-sg-adc-relative-protection"
EXPECT_TIP = "e320232cf7233d7e26df3fee8049ad9ce9202253"
ROOT = "D:/ns800_m8r1_scratch/remotecheck"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
RVI = "reports/reviews/phase8-m8-relative-protection-r1/"
FW = "reports/firmware/phase8-m8-relative-protection-r1/"


def main():
    os.makedirs(ROOT, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare"], cwd=ROOT, check=True)
    r = subprocess.run(["git"] + PROXY + ["fetch", "--depth=1", "--no-tags", REMOTE, BRANCH],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print("FETCH_FAILED:", r.stderr[-400:])
        return 4
    tip = subprocess.run(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    print("remote_tip=%s expect_equal=%s" % (tip, tip == EXPECT_TIP))
    if tip != EXPECT_TIP:
        return 4

    # 从远端对象库里读回两份清单与 manifest
    def blob_text(path):
        p = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (tip, path)],
                           cwd=ROOT, capture_output=True)
        if p.returncode != 0:
            raise SystemExit("MISSING_ON_REMOTE %s" % path)
        return p.stdout

    sums = {}
    for line in blob_text(RVI + "SHA256SUMS.txt").decode("utf-8").splitlines():
        h, _, p = line.partition("  ")
        sums["RVI/" + p.strip()] = (h.strip(), "review")
    for line in blob_text(FW + "SHA256SUMS.txt").decode("utf-8").splitlines():
        h, _, p = line.partition("  ")
        sums["FW/" + p.strip()] = (h.strip(), "firmware")

    # 远端全量文件清单
    tree = subprocess.run(["git", "ls-tree", "-r", "--long", tip],
                          cwd=ROOT, capture_output=True, text=True).stdout.splitlines()
    entries = []
    for ln in tree:
        meta, name = ln.split("\t", 1)
        _mode, _typ, oid, size = meta.split()
        entries.append((name, oid, int(size) if size != "-" else 0))
    print("remote_files=%d" % len(entries))

    checked = 0
    bad = []
    big_skipped = []
    for name, oid, size in entries:
        if name.endswith("SHA256SUMS.txt"):
            continue
        raw = subprocess.run(["git", "cat-file", "blob", oid],
                                        cwd=ROOT, capture_output=True).stdout
        sha = hashlib.sha256(raw).hexdigest()
        key = ("RVI/" + name[len(RVI):]) if name.startswith(RVI) else (
              "FW/" + name[len(FW):]) if name.startswith(FW) else None
        if key and key in sums:
            checked += 1
            if sums[key][0] != sha:
                bad.append((name, sums[key][0][:16], sha[:16]))
        elif key:
            big_skipped.append(name)
        if size >= 1024 * 1024:
            pass  # 远端 blob 已整份下载并复算, 不做"因大而跳过"
    print("listed_and_hash_matched=%d listed_but_mismatched=%d" % (checked, len(bad)))
    for b in bad[:10]:
        print("   MISMATCH", b)
    for b in big_skipped[:10]:
        print("   NOT_IN_MANIFEST", b)

    # 关键身份断言(全部来自远端字节)
    import json
    man = json.loads(blob_text(FW + "build_manifest.json").decode("utf-8"))
    ok_man = True
    for t in man["targets"]:
        for name, v in t["images"].items():
            raw = blob_text(FW + name)
            got = hashlib.sha256(raw).hexdigest()
            if got != v["sha256"] or len(raw) != v["bytes"]:
                ok_man = False
                print("   IMAGE_MISMATCH", name, len(raw), v["bytes"])
        for name, v in t.get("map", {}).items():
            raw = blob_text(FW + name)
            if hashlib.sha256(raw).hexdigest() != v["sha256"] or len(raw) != v["bytes"]:
                ok_man = False
                print("   MAP_MISMATCH", name)
    print("manifest_vs_remote_bytes=%s" % ("ALL_EQUAL" if ok_man else "MISMATCH"))

    # M8 补归档四件的 blob 与 M8 已发布清单比对(清单是 M8 提交里的原文)
    m8 = subprocess.run(["git", "ls-tree", "-r", "--long", tip],
                        cwd=ROOT, capture_output=True, text=True).stdout
    m8sums = {}
    for line in blob_text("reports/reviews/phase8-m8-sg-adc-relative-protection/SHA256SUMS.txt").decode("utf-8").splitlines():
        h, _, p = line.partition("  ")
        m8sums[p.strip()] = h.strip()
    m8_ok = 0
    m8_bad = []
    for p, h in m8sums.items():
        full = "reports/reviews/phase8-m8-sg-adc-relative-protection/" + p
        try:
            raw = blob_text(full)
        except SystemExit:
            m8_bad.append(("missing", p))
            continue
        if hashlib.sha256(raw).hexdigest() != h:
            m8_bad.append(("hash", p))
        else:
            m8_ok += 1
    print("M8_manifest_entries_verified_from_remote=%d problems=%d" % (m8_ok, len(m8_bad)))
    for x in m8_bad[:10]:
        print("   ", x)
    total = len(entries)
    print("REMOTE_READBACK_SUMMARY|files=%d|listed_checked=%d|mismatch=%d|manifest_bytes_ok=%s"
          % (total, checked, len(bad), ok_man))
    return 0 if (not bad and ok_man and not m8_bad and tip == EXPECT_TIP) else 1


if __name__ == "__main__":
    sys.exit(main())
