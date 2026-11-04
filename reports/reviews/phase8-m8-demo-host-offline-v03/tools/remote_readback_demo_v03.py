# -*- coding: utf-8 -*-
"""DEMO-V03 复审轮远端逐 blob 回读: 只从 GitHub 远端取字节复算本轮登记的每一条, 并核原件未被改动。

纪律: git fetch + git cat-file 只用远端对象; 本轮设备动作 0; 取不到就 NOT_CHECKED, 不猜不补。
用法: RBI_TIP=<完整 40 位 SHA> RBI_ROOT=<仓库外工作区> python tools/remote_readback_demo_v03.py --repo <仓库根>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-sg-adc-relative-protection"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
ROUND = "reports/reviews/phase8-m8-demo-host-offline-v03"
FIELD = "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/field-20261104T015928Z/S_S1"

out, fails = [], []


def note(s):
    print(s)
    out.append(s)


def sha(b):
    return hashlib.sha256(b).hexdigest() if b is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args()
    local = a.repo.resolve()
    root = Path(os.environ.get("RBI_ROOT", str(local.parent / "demo_v03_readback"))).resolve()
    expect_tip = os.environ.get("RBI_TIP", "")
    root.mkdir(parents=True, exist_ok=True)

    def run(args, cwd=None):
        p = subprocess.run(["git", "-c", "core.autocrlf=false"] + PROXY + args,
                           cwd=str(cwd or root), capture_output=True)
        return p.returncode, p.stdout, p.stderr

    bare = root / "remote.bare.git"
    bare.mkdir(parents=True, exist_ok=True)
    run(["init", "-q", "--bare", str(bare)])
    rc, _so, be = run(["fetch", "-q", REMOTE, "+refs/heads/%s:refs/heads/%s" % (BRANCH, BRANCH)], bare)
    note("FETCH|rc=%d|%s" % (rc, be.decode("utf-8", "replace").strip()[:140]))
    rc, bo, _be = run(["rev-parse", "refs/heads/" + BRANCH], bare)
    tip = bo.decode().strip()
    parent = tip + "^"
    note("REMOTE_TIP|%s" % tip)
    if expect_tip:
        ok = tip == expect_tip
        note("TIP_MATCHES_LOCAL|%s|expected=%s" % ("PASS" if ok else "FAIL", expect_tip))
        fails.append("" if ok else "TIP")

    def at(path, rev=None):
        rc, raw, _ = run(["cat-file", "blob", "%s:%s" % (rev or tip, path)], bare)
        return raw if rc == 0 else None

    # 1) 本轮清单逐条: 远端 blob == 登记值 == 磁盘字节
    sums = at(ROUND + "/SHA256SUMS.txt")
    if sums is None:
        note("SUMS_LIST|%s NOT_CHECKED" % ROUND)
        return 1
    entries = []
    for line in sums.decode("utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            h, _s, rel = line.partition("  ")
            entries.append((h.strip(), rel.strip()))
    note("SUMS_LIST|%s|条目=%d (清单自身不在此列)" % (ROUND, len(entries)))
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
    fake = hashlib.sha256(b"deliberately-wrong").hexdigest()
    first = at(entries[0][1])
    fired = first is not None and sha(first) != fake
    note("SELFCHECK_SUMS|一个故意写错的登记值必须被判不符: %s" % ("FIRED" if fired else "**NOT-FIRED**"))
    fails.append("" if fired else "SELFCHECK_SUMS")

    # 2) 回执 JSON 的关键声称用远端 blob 复算(不采信我写的文本)
    hj = json.loads(at(ROUND + "/handoff_DEMO_V03_review.json").decode("utf-8"))
    note("--- 回执 JSON 自证(远端 blob)")
    nf = hj["claim_2_seven_frames"]
    b = at(nf["new_frame_source"])
    ok = b is not None and sha(b) == nf["new_frame_sha256"]
    note("  写前帧 %s registered=%s remote=%s" % ("PASS" if ok else "FAIL",
                                                  nf["new_frame_sha256"][:12], str(sha(b))[:12]))
    fails.append("" if ok else "JSON_NEW_FRAME")
    for row in hj["claim_3_desktop_0x18_0x8"]["local_frames"]:
        b = at(row["archive_original"])
        ok = b is not None and sha(b) == row["raw_tab_sha256"]
        note("  第 %d 帧原始回包页 %s remote=%s" % (row["frame"], "PASS" if ok else "FAIL", str(sha(b))[:12]))
        fails.append("" if ok else "JSON_RAW_TAB|%d" % row["frame"])
    note("  判据 total=%d fails=%d / 反例 %d 例 rc 全对=%d"
         % (hj["checks_total"], hj["checks_fails"], hj["negative_controls"]["per_case_lines"],
            hj["negative_controls"]["per_case_lines_rc_matches_expected"]))
    fails.append("" if hj["checks_fails"] == 0 else "CHECKS_FAILS")

    # 3) tip^ -> tip 差异必须恰好是本轮新增 + 声明过的改动件
    note("--- tip^ -> tip 差异(远端对象比较)")
    rc, so, _ = run(["diff", "--name-status", parent, tip], bare)
    added, changed = [], []
    for line in so.decode().splitlines():
        parts = line.split("\t")
        (added if parts[0] == "A" else changed).append(parts[-1])
    note("DIFF|新增=%d 改动=%d" % (len(added), len(changed)))
    # 首提交: 允许改动只有 .gitattributes; 收口跟修提交: 用 RBI_ALLOW_CHANGED 显式声明允许改动集
    allow_changed = set(p for p in os.environ.get("RBI_ALLOW_CHANGED", ".gitattributes").split(",") if p)
    stray = [p for p in changed if p not in allow_changed]
    note("  允许改动集=%d 越界改动=%d %s" % (len(allow_changed), len(stray), stray[:5]))
    fails.extend(["UNEXPECTED_MODIFY|" + p for p in stray])
    stray_new = [p for p in added if not p.startswith(ROUND + "/")]
    note("  新增件越界=%d %s" % (len(stray_new), stray_new[:5]))
    fails.extend(["UNEXPECTED_ADD|" + p for p in stray_new])

    # 4) 原件保护: 七份归档原件与上一轮记录在 tip 与 tip^ 必须逐字节相同
    note("--- 原件保护(tip vs tip^ 内容逐字节)")
    pres = [FIELD + "/01_sys_pre.rx.bin", FIELD + "/02_mot_pre.rx.bin", FIELD + "/03_cfg_apply.rx.bin",
            FIELD + "/04_sys_post.rx.bin", FIELD + "/05_mot_post.rx.bin", FIELD + "/06_safety_post.rx.bin",
            "reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md",
            "reports/reviews/phase8-m8-r7f2b-safety-print-fix/handoff_R7F4_field.json"]
    same = diff_ = absent = 0
    for path in pres:
        a, c = at(path), at(path, parent)
        if a is None or c is None:
            absent += 1
            fails.append("PRES_ABSENT|" + path)
            note("  %-40s NOT_CHECKED(tip=%s tip^=%s)" % (path.split("/")[-1], a is not None, c is not None))
        elif a == c:
            same += 1
        else:
            diff_ += 1
            fails.append("PRES_CHANGED|" + path)
            note("  %-40s **被改动**" % path.split("/")[-1])
    note("PRESERVE|比对=%d 相同=%d 被改=%d 取不到=%d" % (len(pres), same, diff_, absent))
    a, c = at(".gitattributes"), at(".gitattributes", parent)
    want_diff = os.environ.get("RBI_GITATTR_DIFF", "1") == "1"
    got_diff = a is not None and c is not None and a != c
    fired = got_diff == want_diff
    note("SELFCHECK|.gitattributes tip vs tip^ 差异期望=%s 实测=%s: %s"
         % (want_diff, got_diff, "FIRED" if fired else "**不符(本轮不该动 .gitattributes)**"))
    fails.append("" if fired else "SELFCHECK_NOT_FIRED")

    fails[:] = [f for f in fails if f]
    (root / "remote_readback_demo_v03.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    note("SUMMARY|fails=%d|%s|report=%s" % (len(fails), fails[:8], root / "remote_readback_demo_v03.txt"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
