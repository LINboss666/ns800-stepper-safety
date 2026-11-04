# -*- coding: utf-8 -*-
"""R7F5 登记轮远端逐 blob 回读: 只用 GitHub 远端对象复算本轮登记的每一条, 并核冻结件未被改动。

纪律: git fetch + git cat-file(单批流) 只用远端对象; 本轮设备动作 0; 取不到就 NOT_CHECKED, 不猜不补。
判据不猜提交形态: 允许改动集与 .gitattributes 是否应变都由环境变量显式声明。

用法: RBI_TIP=<40位> RBI_ROOT=<仓库外> RBI_ALLOW_CHANGED=<逗号分隔> RBI_GITATTR_DIFF=0|1 \
      python tools/r7f5_remote_readback.py --repo <仓库根>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-sg-adc-relative-protection"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
RV = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
FIELD = RV + "/field/field-20261104T015928Z/S_S1"
OLD_FIELD = RV + "/field/field-20261104T001444Z"
DEMO = "reports/reviews/phase8-m8-demo-host-offline-v03"
EXEC = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"

out, fails = [], []


def sha(b):
    return hashlib.sha256(b).hexdigest() if b is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args()
    local = a.repo.resolve()
    root = Path(os.environ.get("RBI_ROOT", str(local.parent / "r7f5_readback"))).resolve()
    expect_tip = os.environ.get("RBI_TIP", "")
    allow_changed = {p for p in os.environ.get("RBI_ALLOW_CHANGED", "").split(",") if p}
    want_gitattr_diff = os.environ.get("RBI_GITATTR_DIFF", "1") == "1"
    root.mkdir(parents=True, exist_ok=True)
    bare = root / "remote.bare.git"
    bare.mkdir(parents=True, exist_ok=True)

    def note(s):
        print(s)
        out.append(s)

    def git(args, binary=False):
        p = subprocess.run(["git", "-c", "core.autocrlf=false"] + PROXY + args,
                           cwd=str(bare), capture_output=True)
        return (p.returncode, p.stdout, p.stderr) if binary else \
            (p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"))

    git(["init", "-q", "--bare", str(bare)])
    rc, _so, be = git(["fetch", "-q", REMOTE, "+refs/heads/%s:refs/heads/%s" % (BRANCH, BRANCH)])
    note("FETCH|rc=%d|%s" % (rc, be.strip()[:140]))
    rc, bo, _ = git(["rev-parse", "refs/heads/" + BRANCH])
    tip, parent = bo.strip(), bo.strip() + "^"
    note("REMOTE_TIP|%s" % tip)
    if expect_tip:
        ok = tip == expect_tip
        note("TIP_MATCHES_LOCAL|%s|expected=%s" % ("PASS" if ok else "FAIL", expect_tip))
        if not ok:
            fails.append("TIP")

    # 单批流: 一次进程读全部 blob(146 条起 146 个进程会跑很久)
    proc = subprocess.Popen(["git", "-c", "core.autocrlf=false", "cat-file", "--batch"],
                            cwd=str(bare), stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def blob(rev_path):
        proc.stdin.write((rev_path + "\n").encode("utf-8"))
        proc.stdin.flush()
        head = proc.stdout.readline().decode("utf-8", "replace").strip().split(" ")
        if head[-1] == "missing" or len(head) < 3 or head[1] != "blob":
            return None
        data = proc.stdout.read(int(head[2]))
        proc.stdout.read(1)
        return data

    def at(path, rev=None):
        return blob("%s:%s" % (rev or tip, path))

    # 1) 本轮清单逐条: 远端 blob == 登记值 == 磁盘字节
    sums = at(RV + "/SHA256SUMS.txt")
    if sums is None:
        note("SUMS_LIST|NOT_CHECKED")
        proc.stdin.close()
        return 1
    entries = []
    for line in sums.decode("utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            h, _s, rel = line.partition("  ")
            entries.append((h.strip(), rel.strip()))
    note("SUMS_LIST|%s|条目=%d (清单自身不在此列)" % (RV, len(entries)))
    ok_n = bad_n = miss_n = 0
    for expected, path in entries:
        b = at(path)
        if b is None:
            miss_n += 1
            note("NOT_CHECKED|%s" % path)
            continue
        disk = (local / path).read_bytes()
        if sha(b) == expected and b == disk:
            ok_n += 1
        else:
            bad_n += 1
            fails.append("BLOB|" + path)
            note("MISMATCH|%s registered=%s remote=%s blob==disk=%s"
                 % (path, expected[:12], sha(b)[:12], b == disk))
    note("SUMS_RECHECK|entries=%d ok=%d mismatch=%d not_checked=%d" % (len(entries), ok_n, bad_n, miss_n))
    first = at(entries[0][1])
    fired = first is not None and sha(first) != hashlib.sha256(b"deliberately-wrong").hexdigest()
    note("SELFCHECK_SUMS|一个故意写错的登记值必须被判不符: %s" % ("FIRED" if fired else "**NOT-FIRED**"))
    if not fired:
        fails.append("SELFCHECK_SUMS")

    # 2) A 交付的两件落位件: 远端字节 == IDENTITY 声明 == zip 里那两份
    ident = json.loads(at(RV + "/a_review_r7f5/IDENTITY.json").decode("utf-8"))
    for f in ident["files"]:
        b = at(f["path"])
        disk = (local / f["path"]).read_bytes()
        ok = b is not None and sha(b) == f["sha256"] and len(b) == f["bytes"] and b == disk
        note("PLACE|%s|remote=%s declared=%s bytes=%d/%d %s"
             % (f["path"].split("/")[-1], str(sha(b))[:12], f["sha256"][:12],
                len(b or b""), f["bytes"], "PASS" if ok else "FAIL"))
        if not ok:
            fails.append("PLACE|" + f["path"])

    # 3) 回执 JSON 的关键声称用远端 blob 复算(不采信文本)
    hj = json.loads(at(RV + "/handoff_R7F5_registered.json").decode("utf-8"))
    b = at(EXEC)
    ok = sha(b) == hj["package_identity_recomputed_by_me"]["executor_sha256"]
    note("JSON_ID|executor 远端=%s 回执=%s %s" % (str(sha(b))[:12],
         hj["package_identity_recomputed_by_me"]["executor_sha256"][:12], "PASS" if ok else "FAIL"))
    fails.append("" if ok else "JSON_EXECUTOR")
    b = at("reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin")
    ok = sha(b) == hj["package_identity_recomputed_by_me"]["image_sha256"] and len(b) == 292708
    note("JSON_ID|image 远端=%s %d B %s" % (str(sha(b))[:12], len(b or b""), "PASS" if ok else "FAIL"))
    fails.append("" if ok else "JSON_IMAGE")
    note("JSON_CLAIM|步骤摘要 回执重算=%s A声明=%s 相等=%s"
         % (hj["steps_digest"]["recomputed_by_me"][:16], hj["steps_digest"]["declared_by_A"][:16],
            hj["steps_digest"]["match"]))
    fails.append("" if hj["steps_digest"]["match"] else "JSON_STEPS")
    note("JSON_CLAIM|判据 total=%d fails=%d / 回放 %d 例(正常=%s, 台账发过 arm/start=%s)"
         % (hj["checks_total"], hj["checks_fails"], hj["replays"]["cases"],
            hj["replays"]["positive_status"], hj["replays"]["arm_or_start_sent_in_any_ledger"]))
    fails.append("" if hj["checks_fails"] == 0 and not hj["replays"]["arm_or_start_sent_in_any_ledger"]
                 else "JSON_CHECKS")

    # 4) tip^ -> tip 差异必须恰好是本轮新增 + 显式声明的改动集
    rc, so, _ = git(["diff", "--name-status", parent, tip])
    added, changed = [], []
    for line in so.splitlines():
        parts = line.split("\t")
        (added if parts[0] == "A" else changed).append(parts[-1])
    note("DIFF|新增=%d 改动=%d" % (len(added), len(changed)))
    stray = [p for p in changed if p not in allow_changed]
    note("  允许改动集=%d 越界改动=%d %s" % (len(allow_changed), len(stray), stray[:6]))
    fails.extend(["UNEXPECTED_MODIFY|" + p for p in stray])
    stray_new = [p for p in added if RV + "/" not in p and p != RV + "/SHA256SUMS.txt"]
    note("  新增件越界=%d %s" % (len(stray_new), stray_new[:6]))
    fails.extend(["UNEXPECTED_ADD|" + p for p in stray_new])

    # 5) 原件保护: 现场字节/镜像/执行器/上一轮记录与演示端目录在 tip 与 tip^ 必须逐字节相同
    pres = [FIELD + "/01_sys_pre.rx.bin", FIELD + "/02_mot_pre.rx.bin", FIELD + "/03_cfg_apply.rx.bin",
            FIELD + "/04_sys_post.rx.bin", FIELD + "/05_mot_post.rx.bin", FIELD + "/06_safety_post.rx.bin",
            OLD_FIELD + "/readback.bin", OLD_FIELD + "/S_S3/07_irqboot.rx.bin",
            RV + "/handoff_R7F4_field.json", RV + "/handoff_R7F4_registered.json",
            RV + "/a_review_r7f4/IDENTITY.json", RV + "/field/plan_r7f4_b502_configured_reviewed.json",
            RV + "/field/rows_r7f4_b502_configured.json",
            "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1.axf",
            "reports/firmware/phase8-m8-r7f2b-safety-print-fix/build_manifest.json",
            EXEC, DEMO + "/handoff_DEMO_V03_review.md", DEMO + "/SHA256SUMS.txt",
            "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/safety_thread.c"]
    same = diff_ = absent = 0
    for path in pres:
        x, y = at(path), at(path, parent)
        if x is None or y is None:
            absent += 1
            fails.append("PRES_ABSENT|" + path)
            note("  %-40s NOT_CHECKED(tip=%s tip^=%s)" % (path.split("/")[-1], x is not None, y is not None))
        elif x == y:
            same += 1
        else:
            diff_ += 1
            fails.append("PRES_CHANGED|" + path)
            note("  %-40s **被改动**" % path.split("/")[-1])
    note("PRESERVE|比对=%d 相同=%d 被改=%d 取不到=%d" % (len(pres), same, diff_, absent))
    x, y = at(".gitattributes"), at(".gitattributes", parent)
    got_diff = x is not None and y is not None and x != y
    fired = got_diff == want_gitattr_diff
    note("SELFCHECK|.gitattributes 差异期望=%s 实测=%s: %s"
         % (want_gitattr_diff, got_diff, "FIRED" if fired else "**不符**"))
    fails.append("" if fired else "SELFCHECK_NOT_FIRED")
    proc.stdin.close()
    fails[:] = [f for f in fails if f]
    (root / "remote_readback_r7f5.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    note("SUMMARY|fails=%d|%s|report=%s" % (len(fails), fails[:8], root / "remote_readback_r7f5.txt"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
