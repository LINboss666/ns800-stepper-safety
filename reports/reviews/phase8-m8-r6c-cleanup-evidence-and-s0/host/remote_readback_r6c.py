#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6C 的**本轮范围**远端逐 blob 回读: 只从远端取字节, 把远端各件里申报的身份逐条重算回来。

与通用回读(`phase8-m8-relative-protection-r3/host/remote_readback.py`)的分工: 通用件按全仓清单
逐条核 blob, 但它的 JSON 名单不含 `plan_core_r6c.json` 与交接 JSON 内部申报的执行器/镜像/派生源/
探针件身份 —— 本脚本补那一段。

纪律: 输入只有 `git fetch` + `git cat-file`; 设备动作 0(不开串口/不连 DAP/不烧录/不回读/不复位/
不 learn/zero/起转/保存配置); 冻结件只核对"未被改动"; 取不到的对象如实 NOT_CHECKED, 不猜。

用法: RBI_TIP=<完整 tip SHA> python remote_readback_r6c.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-sg-adc-relative-protection"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
ROOT = os.environ.get("RBI_ROOT", "D:/ns800_r6c_scratch/readback")
EXPECT_TIP = os.environ.get("RBI_TIP", "")

R6C = "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0"
R6B = "reports/reviews/phase8-m8-r6b-executor-binding"
R6A = "reports/reviews/phase8-m8-r6a-offline-rework"
R4 = "reports/reviews/phase8-m8-idle-stack-r4"
R3 = "reports/reviews/phase8-m8-relative-protection-r3"
R3F = R3 + "/field"
PLAN = R6C + "/field/plan_core_r6c.json"
HANDOFF_JSON = "reports/agent_handoffs/phase8-m8-r6c-cleanup-evidence-and-s0.json"
HANDOFF_MD = "reports/agent_handoffs/phase8-m8-r6c-cleanup-evidence-and-s0.md"
EXEC = R3F + "/m8r3_serial_chain.py"
SELFTEST = R3F + "/m8r3_field_selftest.py"
MOTOR_C = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/"
           "applications/motor.c")
SUPERVISOR_C = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/"
                "applications/supervisor.c")
BASE_COMMIT = "64353285e9b34df1c54ccea4011ac555c482e626"        # 任务书指定的基线头
BASE_EXEC_SHA = "84df1306f48e947349b9669577a6faf03273f22fe47f2f87407bf54f90a66d1b"
BASE_EXEC_BYTES = 215704
SOFTWARE_COMMIT = "c0f361db5cc02fd087f6f3b404254151411d4a4a"
# §6.1 与复审 §3 点名要保持原字节的旧件
FROZEN = [
    (R4 + "/field/plan_core_r5c.json",
     "05bcf96aa7a6bf89291dab9b140253dde74982f7c3907a49a51bb2972d3a3746", "R5-C 已执行那 26 场的计划"),
    ("reports/reviews/phase8-m8-r6-offline-recovery/field/plan_core_r6.json",
     "6e0b97f251a61493a10d5d4fe3b3238b84e71214e21c77a40b332c0136785750", "R6 离线派生件"),
    (R6A + "/field/plan_core_r6a.json",
     "556a044e195705d1c27949c1393f60c23ac1e1af01cfa555581350ee69d732c2", "R6A 交付件"),
    (R6B + "/field/plan_core_r6b.json",
     "08fd5d522d85f05a1eb8d0c51f339ccd05c18761b85990d6454022d6686069bb", "R6B 交付件(本包的源)"),
    ("reports/firmware/phase8-m8-r6a-offline-rework/rt-thread-motor-m1-candidate.bin",
     "5ae4110d14fdbb40f49cc6f0deca474cf6d0b33b3ee9305e83681dbdd4a53825", "R6A 候选镜像(仍未上板)"),
    ("reports/firmware/phase8-m8-r6-offline-recovery/rt-thread-motor-m1-candidate.bin",
     "d70a9180b40c5c6a25f3692bd973ffb0309a1da5db9a2f6e0768300405a9b86b", "R6 候选镜像"),
    ("reports/firmware/phase8-m8-idle-stack-r4/rt-thread-motor-m1-candidate.bin",
     "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439", "已逐字节证在板的那件"),
]

fails = []
notchecked = []
checks = [0]


def rec(name, ok, detail):
    checks[0] += 1
    if not ok:
        fails.append(name)
    print("%-4s|%s|%s" % ("OK" if ok else "FAIL", name, detail))


def skip(name, detail):
    notchecked.append(name)
    print("SKIP |%s|%s" % (name, detail))


def sha(b):
    return hashlib.sha256(b).hexdigest() if b is not None else None


def rel_of(declared):
    """申报里可能是 D:/ns800/... 绝对路径 -> 剥掉 <drive>:/<仓库根>/ 一层。"""
    p = (declared or "").replace("\\", "/")
    m = re.match(r"^[A-Za-z]:/[^/]+/(.*)$", p)
    return m.group(1) if m else p


class Remote(object):
    def __init__(self, git_dir):
        self.git_dir = git_dir
        self.cache = {}

    def blob(self, rev, rel):
        key = (rev, rel)
        if key not in self.cache:
            p = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (rev, rel)],
                               cwd=self.git_dir, capture_output=True)
            self.cache[key] = None if p.returncode else p.stdout
        return self.cache[key]

    def json(self, rev, rel):
        b = self.blob(rev, rel)
        return None if b is None else json.loads(b.decode("utf-8", "replace").lstrip("﻿"))


def check_sums(r, tip, sums_path):
    b = r.blob(tip, sums_path)
    if b is None:
        rec("sums/%s" % sums_path, False, "远端没有这份清单")
        return
    lines = [l for l in b.decode("utf-8").splitlines() if l.strip() and not l.startswith("#")]
    bad = []
    for ln in lines:
        want, path = ln.split("  ", 1)
        got = sha(r.blob(tip, path.strip()))
        if got != want:
            bad.append(path.strip())
    rec("sums/%s" % sums_path, not bad,
        "entries=%d mismatch=%d%s" % (len(lines), len(bad), (" " + ",".join(bad)) if bad else ""))


def check_triple(r, rev, rel, want_sha, want_bytes, label, src):
    rel = rel_of(rel)
    b = r.blob(rev, rel)
    got = sha(b)
    ok = b is not None and got == want_sha and (want_bytes is None or len(b) == want_bytes)
    rec(label, ok, "%s remote=%s…/%s B vs 申报(%s) %s…/%s B" %
        (rel, (got or "-")[:12], len(b) if b is not None else "-", src,
         (want_sha or "-")[:12], want_bytes if want_bytes is not None else "-"))


def main():
    if os.path.isdir(ROOT):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            for f in filenames:
                try:
                    os.chmod(os.path.join(dirpath, f), 0o666)
                except OSError:
                    pass
        shutil.rmtree(ROOT)
    os.makedirs(ROOT, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare"], cwd=ROOT, capture_output=True)
    f = subprocess.run(["git"] + PROXY + ["fetch", "--depth=1", "--no-tags", REMOTE, BRANCH],
                       cwd=ROOT, capture_output=True, text=True)
    if f.returncode:
        print("FETCH_FAILED", f.stderr[-400:])
        return 4
    tip = subprocess.run(["git", "rev-parse", "FETCH_HEAD"], cwd=ROOT,
                         capture_output=True, text=True).stdout.strip()
    print("remote_tip=%s" % tip)
    if EXPECT_TIP and tip != EXPECT_TIP:
        print("TIP_MISMATCH expect=%s" % EXPECT_TIP)
        return 4
    r = Remote(ROOT)
    nfiles = len(subprocess.run(["git", "ls-tree", "-r", "--name-only", tip], cwd=ROOT,
                                capture_output=True, text=True).stdout.splitlines())
    print("remote_files=%d" % nfiles)

    print("\n== 1. 本轮与受影响轮的清单逐条对远端 blob ==")
    check_sums(r, tip, R6C + "/SHA256SUMS.txt")
    check_sums(r, tip, R3 + "/SHA256SUMS.txt")
    check_sums(r, tip, R4 + "/SHA256SUMS.txt")
    check_sums(r, tip, R6B + "/SHA256SUMS.txt")

    print("\n== 2. 候选模板内部申报的身份 ==")
    plan = r.json(tip, PLAN)
    ex = plan["identity_binding"]["executor"]
    check_triple(r, tip, ex["path"], ex["sha256"], ex["bytes"], "plan.executor", "plan")
    img = plan["identity_binding"]["image"]
    check_triple(r, tip, img["path"], img["sha256"], img["bytes"], "plan.image(在板件)", "plan")
    for role in ("axf", "map"):
        d = img[role]
        check_triple(r, tip, d["path"], d["sha256"], d["bytes"], "plan.image.%s" % role, "plan")
    df = plan["derived_from"]
    check_triple(r, tip, df["path"], df["sha256"], df["bytes"], "plan.derived_from(R6B 冻结源)", "plan")
    cand = plan.get("r6a_candidate_image") or {}
    if cand.get("bin_sha256"):
        check_triple(r, tip, cand["bin_path"], cand["bin_sha256"], cand.get("bin_bytes"),
                     "plan.r6a_candidate_frozen", "plan")
    note = plan.get("r6c_image_note") or {}
    rec("plan.rebuilt_this_round_false", "rebuilt_this_round" in note and
        note["rebuilt_this_round"] is False, "本轮不重编: %r" % note.get("rebuilt_this_round"))
    ap = plan.get("approval") or {}
    rec("plan.no_hardware_authorization",
        ap.get("hardware_actions_authorized_by_this_package") == 0 and
        ap.get("status") == "REQUIRES_NEW_APPROVAL" and
        plan.get("package_status") == "R6C_DERIVED_NOT_EXECUTED",
        "package_status=%s approval=%s authorized=%s" %
        (plan.get("package_status"), ap.get("status"),
         ap.get("hardware_actions_authorized_by_this_package")))

    print("\n== 3. 交接件申报的产物身份(逐个带 path+sha256 的条目都重算) ==")
    hj, hb = r.json(tip, HANDOFF_JSON), r.blob(tip, HANDOFF_JSON)
    rec("handoff.json.parses", hj is not None and hb is not None,
        "bytes=%s sha=%s…" % (len(hb) if hb else "-", (sha(hb) or "-")[:12]))
    art = (hj or {}).get("artifacts_and_identities", {})
    for k in sorted(art):
        d = art[k]
        if isinstance(d, dict) and d.get("path") and d.get("sha256"):
            check_triple(r, tip, d["path"], d["sha256"], d.get("bytes"), "handoff.%s" % k, "handoff")
    rec("handoff.no_self_hash", "sha256" not in hj and "bytes" not in hj,
        "交接 JSON 顶层不自证字节(清单不哈希自身的同一条纪律)")
    da = (hj or {}).get("device_actions", {})
    rec("handoff.device_actions_zero", da.get("total") == 0 and da.get("keil_builds", 0) == 0 and
        hj.get("safe_for_enable") is False and hj.get("new_ledger_created_for_field") is False,
        "device_actions.total=%s keil_builds=%s safe_for_enable=%s 新现场账=%s" %
        (da.get("total"), da.get("keil_builds"), hj.get("safe_for_enable"),
         hj.get("new_ledger_created_for_field")))
    md = r.blob(tip, HANDOFF_MD)
    rec("handoff.md.present", md is not None and len(md) > 1000, "%s B" % (len(md) if md else "-"))

    print("\n== 4. §6.1/复审 §3 要求保持原字节的旧件 ==")
    for path, want, why in FROZEN:
        b = r.blob(tip, path)
        rec("frozen %s" % os.path.basename(path), (sha(b) == want) and b is not None,
            "remote=%s…/%s B expect=%s… (%s)" % ((sha(b) or "-")[:12], len(b) if b else "-",
                                                want[:12], why))

    print("\n== 5. 基线头与软件输入提交的身份(定点 fetch 后现算) ==")
    fb = subprocess.run(["git"] + PROXY + ["fetch", "--depth=1", "--no-tags", REMOTE, BASE_COMMIT],
                        cwd=ROOT, capture_output=True, text=True)
    if fb.returncode:
        skip("baseline.executor@6435328", "定点 fetch 失败(%s) => 不断言基线 EXEC 字节"
             % fb.stderr.strip()[-120:])
    else:
        check_triple(r, BASE_COMMIT, EXEC, BASE_EXEC_SHA, BASE_EXEC_BYTES,
                     "baseline.executor@6435328", "R6B 交付时登记的那份(修前对照的输入)")
        for rel, label in ((MOTOR_C, "motor.c"), (SUPERVISOR_C, "supervisor.c")):
            same = r.blob(tip, rel) == r.blob(BASE_COMMIT, rel)
            rec("产品C未改 %s" % label, same,
                "tip 与基线 %s 的 blob 逐字节相同=%s(任务书 §6.1: 产品 C 不动)" %
                (BASE_COMMIT[:12], same))
    sb = subprocess.run(["git"] + PROXY + ["fetch", "--depth=1", "--no-tags", REMOTE, SOFTWARE_COMMIT],
                        cwd=ROOT, capture_output=True, text=True)
    if sb.returncode:
        skip("software_commit.c0f361d", "定点 fetch 失败 => 不断言软件输入提交里的 EXEC")
    else:
        check_triple(r, SOFTWARE_COMMIT, EXEC, ex["sha256"], ex["bytes"],
                     "software_commit 里的 EXEC == 候选模板登记", "plan")
        check_triple(r, SOFTWARE_COMMIT, SELFTEST,
                     (hj["artifacts_and_identities"]["selftest"]["sha256"]),
                     hj["artifacts_and_identities"]["selftest"]["bytes"],
                     "software_commit 里的自检夹具 == 交接申报", "handoff")
    same = r.blob(tip, EXEC) == r.blob(SOFTWARE_COMMIT, EXEC)
    rec("证据提交未偷改执行器", same,
        "tip 上的 EXEC 字节与软件输入提交 %s 里的那份逐字节相同=%s" % (SOFTWARE_COMMIT[:12], same))

    print("\nR6C_REMOTE_READBACK_SUMMARY|tip=%s|checks=%d|fails=%d|not_checked=%d" %
          (tip[:12], checks[0], len(fails), len(notchecked)))
    if fails:
        print("FAILED_CHECKS=%s" % ",".join(fails))
    if notchecked:
        print("NOT_CHECKED=%s" % ",".join(notchecked))
    print("device_actions=0|serial_ports_opened=0|dap_connections=0|keil_builds=0|"
          "reads_from=remote_git_protocol_only")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
