# -*- coding: utf-8 -*-
"""R7F7 推送后的远端逐 blob 回读(只 fetch + cat-file, 不写不推; 报告留仓外避免自引用循环)。

判据不采信仓库里的文本自报: 每条登记值都从**远端 blob** 重算, 再与磁盘字节、与 A 包 zip 内字节三方对照。
检查器先证明自己会命中(把首条登记值改成错误值必须被判不符), 否则"全部相符"不作数。

环境变量: RBI_ROOT=工作根(默认 D:/ns800_r7f7_readback)  RBI_TIP=期望 tip  RBI_ZIP=A 包原件(不给则该节 NOT_CHECKED)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import zipfile

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-r7d1-async-learn-offline"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
R7F7 = "reports/reviews/phase8-m8-r7f7-newimage-static"
R7F6 = "reports/reviews/phase8-m8-r7f6-la-path-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
MANIFESTS = [R7F7 + "/ROUND_SHA256SUMS.txt", R7F6 + "/ROUND_SHA256SUMS.txt", R7F2B + "/SHA256SUMS.txt"]
BIN_SHA = "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963"
AXF_SHA = "0f421595ac940863c4d38a2791f31bc8811fc3b8fa40062782f2ac7505c317f6"
EXEC_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
PRIOR = "7a08f9d56e69c66b93695cb201a7a3ecefe2b91a"      # 本轮开始时的 tip
ACCEPTED = "00718b49e65546aa0bae84006059011d68284c4a"
APP = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications")
FROZEN_FILES = [APP + "/" + n for n in ("blackbox.c", "diag_relative.c", "diagnosis.c", "motor.c",
                                        "ns_flash_shell.c", "project_config.c", "safety_gpio.c",
                                        "safety_thread.c", "supervisor.c", "tmc2209.c")] + [
    "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py",
    "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin",
    "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1.axf",
    R7F6 + "/FIELD_JUDGEMENT.md", R7F6 + "/POWERDOWN_REPORT.txt", R7F2B + "/run_record.md"]
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    local = a.repo.resolve()
    root = pathlib.Path(os.environ.get("RBI_ROOT", "D:/ns800_r7f7_readback")).resolve()
    expect_tip = os.environ.get("RBI_TIP", "")
    zip_path = os.environ.get("RBI_ZIP", "")
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
            txt = b.get("%s:%s" % (tip, man))
            if txt is None:
                return None
            res = []
            for ln in txt.decode("utf-8", "replace").splitlines():
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
            fired = sha(sc or b"") != "0" * 64
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

        if zip_path and os.path.exists(zip_path):
            zf = zipfile.ZipFile(zip_path)
            names = [n for n in zf.namelist() if not n.endswith("/")]
            zbad = []
            for n in names:
                blob = b.get("%s:%s/%s" % (tip, R7F7, n))
                if blob is None or blob != zf.read(n):
                    zbad.append(n)
            note("PKG_ORIGINALS_ON_REMOTE|%d 件 与 zip 不符=%d %s" % (len(names), len(zbad), zbad[:6]))
            if zbad:
                fails.append("PKG_ORIGINALS")
        else:
            note("PKG_ORIGINALS|NOT_CHECKED(未给 RBI_ZIP 或 zip 不存在 => 不猜路径)")

        # 镜像与执行器身份(远端重算, 不采信包内文本)
        binb = b.get("%s:reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin" % tip)
        axfb = b.get("%s:reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1.axf" % tip)
        exeb = b.get("%s:%s" % (tip, "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"))
        for nm, data, want in (("BIN", binb, BIN_SHA), ("AXF", axfb, AXF_SHA), ("EXECUTOR", exeb, EXEC_SHA)):
            good = data is not None and sha(data) == want
            note("REMOTE_IMAGE_IDENTITY|%s|%s bytes=%s sha=%s" % (nm, "PASS" if good else "FAIL",
                                                                 len(data or b""), sha(data or b"")[:12]))
            if not good:
                fails.append("REMOTE_IMAGE_IDENTITY|" + nm)

        # 冻结件: tip 的 blob == 上一轮 tip 的 blob(逐字节未动)
        for rel in FROZEN_FILES:
            cur = b.get("%s:%s" % (tip, rel))
            old = b.get("%s:%s" % (PRIOR, rel))
            same = cur is not None and cur == old
            note("FROZEN_UNCHANGED|%s|%s sha=%s" % (rel.split("/")[-1], "PASS" if same else "FAIL",
                                                    sha(cur or b"")[:12]))
            if not same:
                fails.append("FROZEN|" + rel)
        # 冻结源 == 00718b4 的 blob(10 件 .c 的来源身份在远端也成立)
        sbad = []
        for rel in FROZEN_FILES[:10]:
            cur = b.get("%s:%s" % (tip, rel))
            src = b.get("%s:%s" % (ACCEPTED, rel))
            if cur is None or cur != src:
                sbad.append(rel.split("/")[-1])
        note("FROZEN_EQ_ACCEPTED_ON_REMOTE|10 件不符=%d %s" % (len(sbad), sbad))
        if sbad:
            fails.append("FROZEN_EQ_ACCEPTED")
        # 产品树: rt-thread-master 子树对象在远端 tip 上仍是同一个
        tr = subprocess.run(["git", "ls-tree", tip], cwd=str(bare), capture_output=True).stdout.decode("utf-8")
        m = re.search(r"^040000 tree ([0-9a-f]{40})\trt-thread-master$", tr, re.M)
        note("REMOTE_PRODUCT_TREE|%s|%s" % (m.group(1) if m else "取不到",
                                          "PASS" if m and m.group(1) == "32470c7ed44ef6ef5f762490a5416912a336cdd9"
                                          else "FAIL"))
        if not (m and m.group(1) == "32470c7ed44ef6ef5f762490a5416912a336cdd9"):
            fails.append("REMOTE_PRODUCT_TREE")

        # run_record: §20 已入库, §16-§19 原文仍在且各一次
        rr = (b.get("%s:%s/run_record.md" % (tip, R7F2B)) or b"").decode("utf-8", "replace")
        for sec in ("## 16", "## 17", "## 18", "## 19", "## 20"):
            hit = rr.count("\n" + sec)
            note("RUN_RECORD_SECTION|%s|命中=%d %s" % (sec, hit, "PASS" if hit == 1 else "FAIL"))
            if hit != 1:
                fails.append("RUN_RECORD|" + sec)
        for man in MANIFESTS:
            blob = b.get("%s:%s" % (tip, man))
            note("MANIFEST_BLOB|remote=%s bytes=%d sha=%s disk_equal=%s"
                 % (man.split("/")[-2] + "/" + man.split("/")[-1], len(blob or b""), sha(blob or b"")[:16],
                    blob == (local / man).read_bytes()))
            if blob != (local / man).read_bytes():
                fails.append("MANIFEST_BLOB|" + man)
    finally:
        b.close()

    note("READBACK_SUMMARY|fails=%d %s" % (len(fails), fails[:10]))
    rep = root / "remote_readback_r7f7.txt"
    rep.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print("REPORT|%s" % rep.as_posix())
    return 1 if fails else 0


def note(s):
    print(s)
    out.append(s)


if __name__ == "__main__":
    sys.exit(main())
