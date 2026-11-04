#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7F5A 推送后的远端逐 blob 回读(只 fetch + cat-file, 不写不推)。

判据不采信仓库里的文本自报: 每条登记值都从**远端 blob** 重算, 再与磁盘字节三方对照。
检查器先证明自己会命中(第一条登记值改成错误值必须被判不符), 否则"全部相符"不作数。

环境变量: RBI_TIP=期望 tip  RBI_ROOT=工作根  RBI_ALLOW_CHANGED=逗号分隔的允许改动集
          RBI_GITATTR_DIFF=本轮是否期望 .gitattributes 有差异(0/1)
"""
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
EXEC = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
IMG = "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin"
DEMO = "reports/reviews/phase8-m8-demo-host-offline-v03"

out, fails = [], []


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args()
    local = a.repo.resolve()
    root = Path(os.environ.get("RBI_ROOT", str(local.parent / "r7f5a_readback"))).resolve()
    expect_tip = os.environ.get("RBI_TIP", "")
    allow_changed = {p for p in os.environ.get("RBI_ALLOW_CHANGED", "").split(",") if p}
    want_gitattr_diff = os.environ.get("RBI_GITATTR_DIFF", "0") == "1"
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

    proc = subprocess.Popen(["git", "-c", "core.autocrlf=false", "cat-file", "--batch"],
                            cwd=str(bare), stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def blob(spec):
        proc.stdin.write((spec + "\n").encode("utf-8"))
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
        note("SUMS_LIST|NOT_CHECKED(清单取不到)")
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

    # 2) 任务书声明的两件落位件: 远端字节 == capture_scope 声明 == 磁盘
    scope = json.loads(at(RV + "/a_review_r7f5a/capture_scope.json").decode("utf-8"))
    for f in scope["source_binding"]["files"]:
        b = at(f["path"])
        disk = (local / f["path"]).read_bytes()
        ok = b is not None and sha(b) == f["sha256"] and len(b) == f["bytes"] and b == disk
        note("PLACE|%s|remote=%s declared=%s bytes=%d/%d %s"
             % (f["path"].split("/")[-1], str(sha(b))[:12], f["sha256"][:12],
                len(b or b""), f["bytes"], "PASS" if ok else "FAIL"))
        if not ok:
            fails.append("PLACE|" + f["path"])
    b = at(EXEC)
    ok = sha(b) == scope["source_binding"]["executor_sha256"]
    note("SCOPE_ID|executor 远端=%s 任务书=%s %s"
         % (str(sha(b))[:12], scope["source_binding"]["executor_sha256"][:12], "PASS" if ok else "FAIL"))
    if not ok:
        fails.append("SCOPE_EXECUTOR")
    b = at(IMG)
    ok = sha(b) == scope["source_binding"]["firmware_sha256"] and len(b) == 292708
    note("SCOPE_ID|image 远端=%s %d B %s" % (str(sha(b))[:12], len(b or b""), "PASS" if ok else "FAIL"))
    if not ok:
        fails.append("SCOPE_IMAGE")

    # 3) 嵌入原件 zip 的声明在两份归档件之间是否自洽(原件字节不在仓库, 只核声明一致性)
    ob = scope["original_zip"]
    ident = json.loads(at(RV + "/a_review_r7f5a/IDENTITY.json").decode("utf-8"))
    prior = json.loads(at(RV + "/handoff_R7F5_registered.json").decode("utf-8"))["reviewed_package"]
    ok = (ob["sha256"] == prior["sha256"] and ob["bytes"] == prior["bytes"] and
          ident["sha256"] != ob["sha256"])
    note("EMBED_CLAIM|任务书=%s %d B 上一轮登记=%s %d B 一致=%s %s"
         % (ob["sha256"][:12], ob["bytes"], prior["sha256"][:12], prior["bytes"],
            ob["sha256"] == prior["sha256"], "PASS" if ok else "FAIL"))
    if not ok:
        fails.append("EMBED_CLAIM")

    # 4) 回执 JSON 的关键声称用远端 blob 复算
    hj = json.loads(at(RV + "/handoff_R7F5A_registered.json").decode("utf-8"))
    note("JSON_CLAIM|判据 total=%d fails=%d 步骤摘要=%s 设备动作=%d"
         % (hj["checks_total"], hj["checks_fails"], hj["offline_checks"]["steps_digest"][:16],
            hj["device_actions_this_round"])
         )
    if not (hj["checks_fails"] == 0 and hj["device_actions_this_round"] == 0
            and hj["offline_checks"]["base_serial_steps"] == 16
            and hj["offline_checks"]["arm_start_in_plan"] is False):
        fails.append("JSON_CHECKS")
    led = hj["old_ledger_read_only"]
    note("JSON_CLAIM|旧账 spent非零=%d pending=%d 身份等于磁盘=%s 分支=%s"
         % (len(led["spent_nonzero"]), len(led["pending"]),
            led["identities_equal_current_disk_bytes"], led["branch_taken"]))
    if led["spent_nonzero"] or led["pending"] or not led["identities_equal_current_disk_bytes"]:
        fails.append("JSON_LEDGER")
    zm = hj["quota_cross_map"]
    note("JSON_CLAIM|被放宽的零额度=%d 重述 irq_polarity_prep=%s"
         % (zm["zero_actions_widened_by_supplement"], zm["re_expressed"]["irq_polarity_prep"]))
    if zm["zero_actions_widened_by_supplement"] != 0:
        fails.append("JSON_WIDEN")
    sm = hj["la_smoke_capture"]
    b = at(RV + "/la_local_r7f5a/la_smoke_capture_digital.csv")
    ok = sm["label"] == "CONTROL_PATH_SMOKE" and sha(b) == sm["csv_sha256"] and len(b) == sm["csv_bytes"]
    note("JSON_CLAIM|浮空采集 远端=%s %d B 回执=%s 标签=%s %s"
         % (str(sha(b))[:12], len(b or b""), sm["csv_sha256"][:12], sm["label"], "PASS" if ok else "FAIL"))
    if not ok:
        fails.append("JSON_SMOKE")
    op = hj["preflight_open_items"]
    note("JSON_CLAIM|OPEN 前置=%d 项 %s" % (len(op), op))
    if len(op) != 3:
        fails.append("JSON_OPEN")

    # 5) tip^ -> tip 差异必须恰好是本轮新增 + 显式声明的改动集
    rc, so, _ = git(["diff", "--name-status", parent, tip])
    added, changed = [], []
    for line in so.splitlines():
        parts = line.split("\t")
        (added if parts[0] == "A" else changed).append(parts[-1])
    note("DIFF|新增=%d 改动=%d" % (len(added), len(changed)))
    stray = [p for p in changed if p not in allow_changed]
    note("  允许改动集=%d 越界改动=%d %s" % (len(allow_changed), len(stray), stray[:6]))
    fails.extend(["UNEXPECTED_MODIFY|" + p for p in stray])
    stray_new = [p for p in added if RV + "/" not in p]
    note("  新增件越界=%d %s" % (len(stray_new), stray_new[:6]))
    fails.extend(["UNEXPECTED_ADD|" + p for p in stray_new])

    # 6) 原件保护: 上一轮 R7F5 归档件/现场字节/镜像/执行器/演示端目录在 tip 与 tip^ 必须逐字节相同
    pres = [RV + "/handoff_R7F5_registered.json", RV + "/handoff_R7F5_registered.md",
            RV + "/a_review_r7f5/IDENTITY.json", RV + "/a_review_r7f5/review_result.json",
            RV + "/offline_r7f5/summary.json", RV + "/offline_r7f5/host_shape_recompile.json",
            RV + "/field/plan_r7f5_b502_blind_irq_static.json",
            RV + "/field/rows_r7f5_b502_blind_irq_static.json",
            FIELD + "/01_sys_pre.rx.bin", FIELD + "/03_cfg_apply.rx.bin", FIELD + "/06_safety_post.rx.bin",
            RV + "/handoff_R7F4_field.json", RV + "/a_review_r7f4/IDENTITY.json",
            RV + "/tools/r7f5_registered_checks.py", RV + "/tools/r7f5_staged_preflight.py",
            RV + "/tools/r7f5_remote_readback.py",
            IMG, EXEC,
            "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1.axf",
            "reports/firmware/phase8-m8-r7f2b-safety-print-fix/build_manifest.json",
            DEMO + "/handoff_DEMO_V03_review.md", DEMO + "/SHA256SUMS.txt",
            "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/"
            "safety_thread.c",
            "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/"
            "project_board.h"]
    same = diff_ = absent = 0
    for path in pres:
        x, y = at(path), at(path, parent)
        if x is None or y is None:
            absent += 1
            fails.append("PRES_ABSENT|" + path)
            note("  %-42s NOT_CHECKED(tip=%s tip^=%s)" % (path.split("/")[-1], x is not None, y is not None))
        elif x == y:
            same += 1
        else:
            diff_ += 1
            fails.append("PRES_CHANGED|" + path)
            note("  %-42s **被改动**" % path.split("/")[-1])
    note("PRESERVE|比对=%d 相同=%d 被改=%d 取不到=%d" % (len(pres), same, diff_, absent))
    x, y = at(".gitattributes"), at(".gitattributes", parent)
    got_diff = x is not None and y is not None and x != y
    fired = got_diff == want_gitattr_diff
    note("SELFCHECK|.gitattributes 差异期望=%s 实测=%s: %s"
         % (want_gitattr_diff, got_diff, "FIRED" if fired else "**不符**"))
    if not fired:
        fails.append("SELFCHECK_NOT_FIRED")
    proc.stdin.close()
    fails[:] = [f for f in fails if f]
    (root / "remote_readback_r7f5a.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
    note("SUMMARY|fails=%d|%s|report=%s" % (len(fails), fails[:8], root / "remote_readback_r7f5a.txt"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
