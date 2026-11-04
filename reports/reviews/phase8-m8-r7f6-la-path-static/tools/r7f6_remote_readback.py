# -*- coding: utf-8 -*-
"""R7F6 推送后的远端逐 blob 回读(只 fetch + cat-file, 不写不推; 报告留仓外避免自引用循环)。

判据不采信仓库里的文本自报: 每条登记值都从**远端 blob** 重算, 再与磁盘字节、与 A 包 zip 内字节三方对照。
检查器先证明自己会命中(把首条登记值改成错误值必须被判不符), 否则"全部相符"不作数。

环境变量: RBI_ROOT=工作根(默认 D:/ns800_r7f6_readback)  RBI_TIP=期望 tip
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import subprocess
import sys
import zipfile

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-r7d1-async-learn-offline"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
R7F6 = "reports/reviews/phase8-m8-r7f6-la-path-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
MANIFESTS = [R7F6 + "/ROUND_SHA256SUMS.txt", R7F2B + "/SHA256SUMS.txt"]
ZIP = os.environ.get("RBI_ZIP", "")
PRIOR = "4e533c3f54bfa54d395f0ea8f9cfa0abc6820022"
FROZEN = ["reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py",
          "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin",
          R7F2B + "/handoff_R7F5A_field.json",
          R7F2B + "/field_r7f5a/la_digital_raw.csv",
          R7F2B + "/field_r7f5a/la_capture_id8.sal"]

out, fails = [], []


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Batch:
    def __init__(self, cwd):
        self.p = subprocess.Popen(["git", "-c", "core.autocrlf=false", "cat-file", "--batch"],
                                  cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE)

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


def note(s):
    print(s)
    out.append(s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    local = a.repo.resolve()
    root = pathlib.Path(os.environ.get("RBI_ROOT", "D:/ns800_r7f6_readback")).resolve()
    expect_tip = os.environ.get("RBI_TIP", "")
    root.mkdir(parents=True, exist_ok=True)
    bare = root / "remote.bare.git"
    bare.mkdir(parents=True, exist_ok=True)

    def git(args):
        p = subprocess.run(["git", "-c", "core.autocrlf=false"] + PROXY + args,
                           cwd=str(bare), capture_output=True)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    git(["init", "-q", "--bare", str(bare)])
    rc, _o, be = git(["fetch", "-q", REMOTE, "+refs/heads/%s:refs/heads/%s" % (BRANCH, BRANCH)])
    note("FETCH|rc=%d|%s" % (rc, be.strip()[:160]))
    if rc:
        return 2
    tip = git(["rev-parse", "refs/heads/%s" % BRANCH])[1].strip()
    note("TIP|%s|期望=%s|%s" % (tip, expect_tip, "PASS" if tip == expect_tip else "FAIL"))
    if expect_tip and tip != expect_tip:
        fails.append("TIP")

    b = Batch(bare)
    try:
        def entries(man):
            res = []
            txt = b.get("%s:%s" % (tip, man))
            if txt is None:
                return None
            for ln in txt.decode("utf-8").splitlines():
                if ln.strip() and not ln.lstrip().startswith("#"):
                    h, _s, rel = ln.partition("  ")
                    res.append((h.strip(), rel.strip()))
            return res

        total = ok = miss = bad = 0
        for man in MANIFESTS:
            ent = entries(man)
            if ent is None:
                note("SUMS_LIST|NOT_CHECKED %s 远端取不到" % man)
                fails.append("SUMS_LIST|" + man)
                continue
            note("SUMS_LIST|%s|条目=%d" % (man, len(ent)))
            sc = b.get("%s:%s" % (tip, ent[0][1]))
            fired = sha(sc) != "0" * 64
            note("SELFCHECK|%s|首条登记值改成 0*64 必须被判不符: %s" % (man, "FIRED" if fired else "**NOT-FIRED**"))
            if not fired:
                fails.append("SELFCHECK|" + man)
            for want, rel in ent:
                total += 1
                blob = b.get("%s:%s" % (tip, rel))
                if blob is None:
                    miss += 1
                    note("NOT_CHECKED|%s" % rel)
                    continue
                disk = (local / rel).read_bytes()
                if sha(blob) == want and blob == disk:
                    ok += 1
                else:
                    bad += 1
                    fails.append("BLOB|" + rel)
                    note("MISMATCH|%s registered=%s remote=%s blob==disk=%s"
                         % (rel, want[:12], sha(blob)[:12], blob == disk))
        note("SUMS_RECHECK|entries=%d ok=%d mismatch=%d not_checked=%d" % (total, ok, bad, miss))

        # A 包 30 件: 远端 blob == zip 内字节 == 磁盘(zip 路径由环境变量给, 不把本机绝对路径写进入库件)
        if not ZIP or not os.path.exists(ZIP):
            note("PKG_ORIGINALS|NOT_CHECKED(未给 RBI_ZIP 或 zip 不存在 => 不猜路径)")
            fails.append("PKG_ZIP")
        else:
            zf = zipfile.ZipFile(ZIP)
            names = [n for n in zf.namelist() if not n.endswith("/")]
            zbad = []
            for n in names:
                blob = b.get("%s:%s/%s" % (tip, R7F6, n))
                if blob is None or blob != zf.read(n):
                    zbad.append(n)
            note("PKG_ORIGINALS_ON_REMOTE|%d 件 与 zip 不符=%d %s" % (len(names), len(zbad), zbad[:6]))
            if zbad:
                fails.append("PKG_ORIGINALS")

        # 冻结件: tip 的 blob == 上一轮 tip 的 blob(逐字节未动)
        for rel in FROZEN:
            cur = b.get("%s:%s" % (tip, rel))
            old = b.get("%s:%s" % (PRIOR, rel))
            same = cur is not None and cur == old
            note("FROZEN_UNCHANGED|%s|%s sha=%s" % (rel.split("/")[-1], "PASS" if same else "FAIL",
                                                    sha(cur or b"")[:12]))
            if not same:
                fails.append("FROZEN|" + rel)

        # run_record: §19 已入库, §16/§17/§18 原文仍在
        rr = b.get("%s:%s/run_record.md" % (tip, R7F2B)).decode("utf-8")
        for sec in ("## 16", "## 17", "## 18", "## 19"):
            hit = rr.count("\n" + sec)
            note("RUN_RECORD_SECTION|%s|命中=%d %s" % (sec, hit, "PASS" if hit == 1 else "FAIL"))
            if hit != 1:
                fails.append("RUN_RECORD|" + sec)
        # 清单自身也在远端, 报出远端散列供对账(不自我登记)
        for man in MANIFESTS:
            blob = b.get("%s:%s" % (tip, man))
            note("MANIFEST_BLOB|remote=%s bytes=%d sha=%s disk_equal=%s"
                 % (man.split("/")[-1], len(blob or b""), sha(blob or b"")[:16],
                    blob == (local / man).read_bytes()))
            if blob != (local / man).read_bytes():
                fails.append("MANIFEST_BLOB|" + man)
    finally:
        b.close()

    note("READBACK_SUMMARY|fails=%d %s" % (len(fails), fails[:10]))
    rep = root / "remote_readback_r7f6.txt"
    rep.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print("REPORT|%s" % rep.as_posix())
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
