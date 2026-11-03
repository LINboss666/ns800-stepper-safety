#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6A 的**本轮范围**远端逐 blob 回读: 只从远端取字节, 再把远端各件里申报的散列/字节数逐条重算回来。

为什么要另写一份: 通用回读
`reports/reviews/phase8-m8-relative-protection-r3/host/remote_readback.py` 只把文件名含
`manifest` / `plan.json` / `needles.json` 的 JSON 当"内部申报件"解析, 所以
`plan_core_r6a.json` 与交接 JSON 里申报的执行器/镜像/清单身份**没有**被它逐条核过
(它只核过这些文件自身的 blob 字节)。本脚本补的正是那一段。

纪律:
  * 输入只有 `git fetch` + `git cat-file`(远端 git 协议), 不拿本机工作树来"证明"远端;
  * 设备动作 0: 不开串口、不连 DAP、不烧录/回读/复位、不 learn/zero/起转/保存配置;
  * 冻结件只核对"未被改动", 不回改;
  * 取不到的历史对象如实写 NOT_CHECKED + 原因, 不猜。

用法: RBI_TIP=<完整 tip SHA> python remote_readback_r6a.py
      RBI_ROOT 缺省 D:/ns800_r6a_scratch/r6areadback(仓库外)
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
ROOT = os.environ.get("RBI_ROOT", "D:/ns800_r6a_scratch/r6areadback")
EXPECT_TIP = os.environ.get("RBI_TIP", "")
BASE_COMMIT = "44e493cea9bbbc35e18394ca23dd1a8502519448"
# 开工核对时从本地 git 对象库量到的基线 EXEC 身份(本轮起点, 不是本轮产物)
BASE_EXEC_SHA = "1b23719a3c9a26569ae15214e01712603f7c31aefe0b6d7c5421c0cafd117aee"
BASE_EXEC_BYTES = 184093

R6A = "reports/reviews/phase8-m8-r6a-offline-rework"
FW = "reports/firmware/phase8-m8-r6a-offline-rework"
PLAN = R6A + "/field/plan_core_r6a.json"
MANIFEST = FW + "/build_manifest.json"
HANDOFF_JSON = "reports/agent_handoffs/phase8-m8-r6a-offline-rework.json"
HANDOFF_MD = "reports/agent_handoffs/phase8-m8-r6a-offline-rework.md"
R5C_PLAN = "reports/reviews/phase8-m8-idle-stack-r4/field/plan_core_r5c.json"
R6_PLAN = "reports/reviews/phase8-m8-r6-offline-recovery/field/plan_core_r6.json"
EXEC = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
# §7 点名要继续保持不动的旧件
FROZEN = [
    (R5C_PLAN, "05bcf96aa7a6bf89291dab9b140253dde74982f7c3907a49a51bb2972d3a3746",
     "R5-C 已执行那 26 场的计划"),
    (R6_PLAN, None, "R6 离线派生件(本包的源; 由候选包 derived_from 现算比对)"),
    ("reports/firmware/phase8-m8-r6-offline-recovery/rt-thread-motor-m1-candidate.bin",
     "d70a9180b40c5c6a25f3692bd973ffb0309a1da5db9a2f6e0768300405a9b86b", "R6 候选镜像归档件"),
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
    """申报里可能是 D:/ns800/... 绝对路径 -> 取仓库内相对部分(按 <drive>:/<root>/ 剥一层)。"""
    p = (declared or "").replace("\\", "/")
    m = re.match(r"^[A-Za-z]:/[^/]+/(.*)$", p)
    return m.group(1) if m else p


class Remote:
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

    def text(self, rev, rel):
        b = self.blob(rev, rel)
        return None if b is None else b.decode("utf-8", "replace")

    def json(self, rev, rel):
        t = self.text(rev, rel)
        return None if t is None else json.loads(t.lstrip("﻿"))


def check_sums(r, tip, sums_path):
    text = r.text(tip, sums_path)
    if text is None:
        rec("sums/%s" % sums_path, False, "远端没有这份清单")
        return
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    bad = []
    for ln in lines:
        want, path = ln.split("  ", 1)
        got = sha(r.blob(tip, path.strip()))
        if got != want:
            bad.append(path.strip())
    rec("sums/%s" % sums_path, not bad,
        "entries=%d mismatch=%d%s" % (len(lines), len(bad), (" " + ",".join(bad)) if bad else ""))


def check_triple(r, tip, rel, want_sha, want_bytes, label, src):
    b = r.blob(tip, rel)
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
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", tip], cwd=ROOT,
                           capture_output=True, text=True).stdout.splitlines()
    print("remote_files=%d" % len(files))

    print("\n== 1. 本轮两份清单逐条对远端 blob ==")
    check_sums(r, tip, R6A + "/SHA256SUMS.txt")
    check_sums(r, tip, FW + "/SHA256SUMS.txt")

    print("\n== 2. build_manifest 内的镜像与编译输入(只核 blob 那一层) ==")
    man = r.json(tip, MANIFEST)
    if man is None:
        rec("manifest/%s" % MANIFEST, False, "远端没有")
    else:
        for s in man["source_inputs"]:
            check_triple(r, tip, rel_of(s["path"]), s["sha256"], s["bytes"],
                         "manifest.source_input %s" % os.path.basename(s["path"]),
                         "manifest.disk_side=%s…" % s["compiled_from_disk_bytes_sha256"][:12])
        for t in man["targets"]:
            for name, v in sorted(t["images"].items()):
                check_triple(r, tip, rel_of(v["path"]), v["sha256"], v["bytes"],
                             "manifest.image %s" % name, "manifest")
        check_triple(r, tip, rel_of(man["log_path"]), man["log_sha256"], man["log_bytes"],
                     "manifest.build_log", "manifest")

    print("\n== 3. 候选包内部申报的身份 ==")
    plan = r.json(tip, PLAN)
    ib = plan["identity_binding"]
    ex = ib["executor"]
    check_triple(r, tip, rel_of(ex["path"]), ex["sha256"], ex["bytes"],
                 "plan.executor", "plan")
    img = ib["image"]
    check_triple(r, tip, rel_of(img["path"]), img["sha256"], img["bytes"],
                 "plan.image(r4 在板件)", "plan")
    for role in ("axf", "map"):
        d = img[role]
        check_triple(r, tip, rel_of(d["path"]), d["sha256"], d["bytes"],
                     "plan.image.%s" % role, "plan")
    cand = plan["r6a_candidate_image"]
    for pkey, skey, bkey in (("bin_path", "bin_sha256", "bin_bytes"),
                             ("axf_path", "axf_sha256", "axf_bytes"),
                             ("map_path", "map_sha256", "map_bytes")):
        check_triple(r, tip, rel_of(cand[pkey]), cand[skey], cand[bkey],
                     "plan.candidate.%s" % pkey.split("_")[0], "plan")
    check_triple(r, tip, MANIFEST, cand["manifest_sha256"], None,
                 "plan.candidate.manifest", "plan")
    df = plan["derived_from"]
    db = r.blob(tip, rel_of(df["path"]))
    check_triple(r, tip, rel_of(df["path"]), df["sha256"], df["bytes"],
                 "plan.derived_from", "plan")
    r6c = plan["r6_candidate_image"]
    check_triple(r, tip, rel_of(r6c["bin_path"]), r6c["bin_sha256"], r6c["bin_bytes"],
                 "plan.r6_candidate_frozen", "plan")

    print("\n== 4. 交接件申报的产物身份 ==")
    hj, hb = r.json(tip, HANDOFF_JSON), r.blob(tip, HANDOFF_JSON)
    rec("handoff.json.parses", hj is not None and hb is not None,
        "bytes=%s sha=%s…" % (len(hb) if hb else "-", (sha(hb) or "-")[:12]))
    if hj:
        for key in ("candidate_image", "map"):
            d = hj.get(key) or {}
            if d.get("path"):
                check_triple(r, tip, rel_of(d["path"]), d["sha256"], d.get("bytes"),
                             "handoff.%s" % key, "handoff")
        pp = hj.get("plan_package", {}) or {}
        if pp.get("path"):
            check_triple(r, tip, rel_of(pp["path"]), pp["sha256"], pp.get("bytes"),
                         "handoff.plan_package", "handoff")
        er = pp.get("executor_registration", {}) or {}
        if er.get("sha256"):
            check_triple(r, tip, EXEC, er["sha256"], er.get("bytes"),
                         "handoff.plan_package.executor_registration", "handoff")
        bl = pp.get("baseline_executor_at_44e493c", {}) or {}
        rec("handoff.baseline_declared", bl.get("sha256") == BASE_EXEC_SHA
            and bl.get("bytes") == BASE_EXEC_BYTES,
            "交接自报基线 EXEC=%s…/%s B(第 5 节到远端历史对象核)" %
            ((bl.get("sha256") or "-")[:12], bl.get("bytes")))
        for i, s in enumerate(hj.get("build_inputs") or []):
            if s.get("path") and s.get("sha256"):
                check_triple(r, tip, rel_of(s["path"]), s["sha256"], s.get("bytes"),
                             "handoff.build_input[%d] %s" % (i, os.path.basename(s["path"])),
                             "handoff")
        kl = (hj.get("keil_build") or {}).get("log", {}) or {}
        if kl.get("path"):
            check_triple(r, tip, rel_of(kl["path"]), kl["sha256"], kl.get("bytes"),
                         "handoff.keil_build.log", "handoff")
        rec("handoff.no_self_hash", "sha256" not in hj and "bytes" not in hj,
            "交接 JSON 顶层不自证字节(清单不哈希自身的同一条纪律)")
    md = r.blob(tip, HANDOFF_MD)
    rec("handoff.md.present", md is not None and len(md) > 1000, "%s B" % (len(md) if md else "-"))

    print("\n== 5. §7 要求保持冻结的旧件 ==")
    for path, want, why in FROZEN:
        b = r.blob(tip, path)
        got = sha(b)
        expect = want or df["sha256"]
        rec("frozen %s" % os.path.basename(path), got == expect and b is not None,
           "remote=%s…/%s B expect=%s… (%s)" %
           ((got or "-")[:12], len(b) if b else "-", (expect or "-")[:12], why))
    # 基线 EXEC 字节: 需要历史提交对象, depth=1 取不到时按需追加一次定点 fetch
    fb = subprocess.run(["git"] + PROXY + ["fetch", "--depth=1", "--no-tags", REMOTE, BASE_COMMIT],
                        cwd=ROOT, capture_output=True, text=True)
    if fb.returncode:
        skip("frozen executor@base_commit",
             "定点 fetch %s 失败(%s) => 本轮不断言基线 EXEC 字节" %
             (BASE_COMMIT[:12], fb.stderr.strip()[-120:]))
    else:
        b = r.blob(BASE_COMMIT, EXEC)
        check_triple(r, BASE_COMMIT, EXEC, BASE_EXEC_SHA, BASE_EXEC_BYTES,
                     "frozen executor@base_commit", "开工核对时的本地对象库")

    print("\n== 6. 未改动的历史件仍在远端(存在性抽查) ==")
    for path in ("reports/reviews/phase8-m8-idle-stack-r4/field/plan_core_r4.json",
                 "reports/reviews/phase8-m8-relative-protection-r3/field/plan.json",
                 "reports/reviews/phase8-m8-r6-offline-recovery/remote_readback_at_a8d9033.txt",
                 "reports/agent_handoffs/phase8-m8-r6-offline-recovery.json"):
        b = r.blob(tip, path)
        rec("exists %s" % os.path.basename(path), b is not None, "%s B" % (len(b) if b else "-"))

    print("\nR6A_REMOTE_READBACK_SUMMARY|tip=%s|checks=%d|fails=%d|not_checked=%d" %
          (tip[:12], checks[0], len(fails), len(notchecked)))
    if fails:
        print("FAILED_CHECKS=%s" % ",".join(fails))
    if notchecked:
        print("NOT_CHECKED=%s" % ",".join(notchecked))
    print("device_actions=0|serial_ports_opened=0|dap_connections=0|reads_from=remote_git_protocol_only")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
