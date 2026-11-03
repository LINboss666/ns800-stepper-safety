#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7 的**本轮范围**远端逐 blob 回读: 只从远端 Git 协议取字节, 把远端各件里申报的身份逐条重算回来。

与通用回读(`phase8-m8-relative-protection-r3/host/remote_readback.py`)的分工: 通用件按全仓清单逐条核 blob,
但它的名单不含 `plan_core_r7.json` 与交接 JSON 内部申报的执行器/镜像/派生源/干跑夹具/在板件身份 —— 本脚本补那一段。

纪律: 输入只有 `git fetch` + `git cat-file`; 设备动作 0(不开串口/不连 DAP/不烧录/不回读/不复位/不起转/
不 apply/不保存配置); 冻结件只核对"未被改动"; 取不到的对象如实 NOT_CHECKED, 不猜。

用法: RBI_TIP=<完整 tip SHA> python remote_readback_r7.py
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
ROOT = os.environ.get("RBI_ROOT", "D:/ns800_r7_scratch/readback")
EXPECT_TIP = os.environ.get("RBI_TIP", "")

R7 = "reports/reviews/phase8-m8-r7-field-package-5ae"
R6C = "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0"
R6B = "reports/reviews/phase8-m8-r6b-executor-binding"
R6A = "reports/reviews/phase8-m8-r6a-offline-rework"
R6 = "reports/reviews/phase8-m8-r6-offline-recovery"
R4 = "reports/reviews/phase8-m8-idle-stack-r4"
DS = "reports/reviews/phase8-m8-diag-stack-r1"
R3 = "reports/reviews/phase8-m8-relative-protection-r3"
R3F = R3 + "/field"
FW6A = "reports/firmware/phase8-m8-r6a-offline-rework"
PLAN = R7 + "/field/plan_core_r7.json"
HANDOFF_JSON = "reports/agent_handoffs/phase8-m8-r7-field-package-5ae.json"
HANDOFF_MD = "reports/agent_handoffs/phase8-m8-r7-field-package-5ae.md"
EXEC = R3F + "/m8r3_serial_chain.py"
SELFTEST = R3F + "/m8r3_field_selftest.py"
BASE_COMMIT = "f15ba464628d282f5d7fca91de79b6f702c54dfa"   # 本轮开工基线 = A 签收的 R6C 收口头

# §5 A 点名要按原字节保住的旧件(本轮一个新包都没碰它们)
FROZEN = [
    (R4 + "/field/plan_core_r5c.json",
     "05bcf96aa7a6bf89291dab9b140253dde74982f7c3907a49a51bb2972d3a3746", "R5-C 已执行那 26 场的计划"),
    (R6 + "/field/plan_core_r6.json",
     "6e0b97f251a61493a10d5d4fe3b3238b84e71214e21c77a40b332c0136785750", "R6 离线派生件"),
    (R6A + "/field/plan_core_r6a.json",
     "556a044e195705d1c27949c1393f60c23ac1e1af01cfa555581350ee69d732c2", "R6A 交付件"),
    (R6B + "/field/plan_core_r6b.json",
     "08fd5d522d85f05a1eb8d0c51f339ccd05c18761b85990d6454022d6686069bb", "R6B 交付件"),
    (R6C + "/field/plan_core_r6c.json",
     "408c18ecaf50f62a0620c904011f0c60bea3681087875a2ada64efbae7b6de73", "R6C 交付件(本包的派生源)"),
    (FW6A + "/rt-thread-motor-m1-candidate.bin",
     "5ae4110d14fdbb40f49cc6f0deca474cf6d0b33b3ee9305e83681dbdd4a53825", "R6A 候选镜像(本包要上板的那件)"),
    (R6 + "/rt-thread-motor-m1-candidate.bin",
     "d70a9180b40c5c6a25f3692bd973ffb0309a1da5db9a2f6e0768300405a9b86b", "R6 候选镜像"),
    (R4 + "/rt-thread-motor-m1-candidate.bin",
     "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439", "最后逐字节证在板的那件"),
    (R4 + "/field/field-20261101T142722Z/readback.bin",
     "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439", "那次在板证明的原始回读件"),
    (DS + "/field/field-20261101T084256Z/flash_readback.bin",
     "f5c2fd89bbf28255a5b26c9cca495bc0fb1e39d401d0bb7bea19d051221708ce", "更早一轮的在板回读件"),
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
            for fn in filenames:
                try:
                    os.chmod(os.path.join(dirpath, fn), 0o666)
                except OSError:
                    pass
        shutil.rmtree(ROOT, onerror=None)
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
    check_sums(r, tip, R7 + "/SHA256SUMS.txt")
    check_sums(r, tip, R6C + "/SHA256SUMS.txt")
    check_sums(r, tip, R3 + "/SHA256SUMS.txt")
    check_sums(r, tip, R4 + "/SHA256SUMS.txt")
    check_sums(r, tip, R6B + "/SHA256SUMS.txt")
    check_sums(r, tip, R6A + "/SHA256SUMS.txt")
    check_sums(r, tip, FW6A + "/SHA256SUMS.txt")

    print("\n== 2. 候选包内部申报的身份 ==")
    plan = r.json(tip, PLAN)
    if plan is None:
        rec("plan_core_r7.json 可解析", False, "远端取不到")
    else:
        rec("plan.task_id/status 写的是本轮", plan.get("task_id") == "phase8-m8-r7-field-package-5ae" and
            plan.get("status") == plan.get("package_status") == "R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED",
            "task_id=%s status=%s" % (plan.get("task_id"), plan.get("status")))
        rec("plan.no_hardware_authorization",
            (plan.get("approval") or {}).get("hardware_actions_authorized_by_this_package") == 0 and
            plan["approval"].get("status") == "REQUIRES_NEW_APPROVAL" and
            plan.get("numbers_are_proposals_not_authorization") is True,
            "authorized=%s approval=%s proposals=%s" %
            (plan["approval"].get("hardware_actions_authorized_by_this_package"),
             plan["approval"].get("status"), plan.get("numbers_are_proposals_not_authorization")))
        ex = plan["identity_binding"]["executor"]
        rec("plan.executor.path 是仓内相对路径",
            not re.match(r"^[A-Za-z]:", ex["path"]) and
            rel_of(ex["path"]) == EXEC and bool(ex.get("path_r6c_template_verbatim")),
            "path=%s(绝对原串保留=%s)" % (ex["path"], bool(ex.get("path_r6c_template_verbatim"))))
        check_triple(r, tip, ex["path"], ex["sha256"], ex["bytes"], "plan.executor", "plan")
        img = plan["identity_binding"]["image"]
        check_triple(r, tip, img["path"], img["sha256"], img["bytes"], "plan.image(本包要上板的 5ae)", "plan")
        for role in ("axf", "map"):
            d = img[role]
            check_triple(r, tip, d["path"], d["sha256"], d["bytes"], "plan.image.%s" % role, "plan")
        dl, rb = plan["tools"]["download"], plan["tools"]["readback"]
        check_triple(r, tip, dl["image"]["path"], dl["image"]["sha256"], dl["image"]["bytes"],
                     "plan.tools.download.image", "plan")
        check_triple(r, tip, rb["image"]["path"], rb["image"]["sha256"], rb["image"]["bytes"],
                     "plan.tools.readback.image", "plan")
        rec("四处镜像身份同源且 download.argv 末项==axf",
            dl["image"]["sha256"] == img["axf"]["sha256"] and
            rb["image"]["sha256"] == img["sha256"] and dl["argv"][-1] == img["axf"]["path"],
            "dl=axf, rb=bin, argv 末项同 axf 路径")
        rec("回读定长用 @IMAGE_BYTES 占位且登记等于 image.bytes 的展开值",
            any("@IMAGE_BYTES" in a for a in rb["argv"]) and
            "identity_binding.image.bytes" in plan["tools"]["substitutions"]["@IMAGE_BYTES"] and
            ("0x%X" % img["bytes"]) in json.dumps(plan["identity_binding"]["image_rebind_note"]),
            "argv 里是占位符(不是硬写长度); 执行器按 0x%%X %% image.bytes 展开 => 本轮应为 0x%X" % img["bytes"])
        df = plan["derived_from"]
        check_triple(r, tip, df["path"], df["sha256"], df["bytes"], "plan.derived_from(R6C 冻结源)", "plan")
        bib = plan["identity_binding"]["board_image_before"]
        check_triple(r, tip, bib["path"], bib["sha256"], bib["bytes"],
                     "plan.board_image_before(更正后)", "plan")
        rec("board_image_before == 最新一次在板回读件的远端字节",
            sha(r.blob(tip, R4 + "/field/field-20261101T142722Z/readback.bin")) == bib["sha256"],
            "remote readback.bin=%s… == 登记 %s…" % (bib["sha256"][:12], bib["sha256"][:12]))
        sup = bib.get("supersedes_in_r6c_template") or {}
        rec("被更正的旧口径也对得上它那轮回读",
            sha(r.blob(tip, DS + "/field/field-20261101T084256Z/flash_readback.bin")) ==
            sup.get("sha256"), "旧 %s…" % str(sup.get("sha256"))[:12])
        rec("下载对象 != 下载前在板件", bib["sha256"] != img["sha256"],
            "board=%s… target=%s…" % (bib["sha256"][:8], img["sha256"][:8]))
        man = r.json(tip, img["manifest"])
        tgt = [t for t in man.get("targets", []) if t.get("target") == img.get("manifest_target")]
        rec("build_manifest 的 target 行与三件申报相符",
            bool(tgt) and all(tgt[0]["images"].get(fn, {}).get("sha256") == want for fn, want in
                              [("rt-thread-motor-m1-candidate.bin", img["sha256"]),
                               ("rt-thread-motor-m1.axf", img["axf"]["sha256"]),
                               ("rt-thread-motor-m1.map", img["map"]["sha256"])]),
            "target=%s manifest 三件散列相符" % img.get("manifest_target"))
        rec("本包无运动步骤与运动额度",
            not [s for st in plan["stages"] for s in (st.get("steps") or [])
                 if str(s.get("cmd", "")).split(" ")[0] in ("motor_arm", "motor_start")] and
            plan["quota"]["motor_run"] == 0 and plan["quota"]["motor_run_seconds_total"] == 0 and
            isinstance(plan["motion_budget"], dict),
            "无起转步骤, motor_run=0, motion_budget 是对象")

    print("\n== 3. 交接件申报的产物身份 ==")
    hj, hb = r.json(tip, HANDOFF_JSON), r.blob(tip, HANDOFF_JSON)
    rec("handoff.json.parses", hj is not None and hb is not None,
        "bytes=%s sha=%s…" % (len(hb) if hb else "-", (sha(hb) or "-")[:12]))
    art = (hj or {}).get("artifacts_and_identities", {})
    bad_art = []
    for k, d in art.items():
        b = r.blob(tip, rel_of(d["path"]))
        if sha(b) != d["sha256"] or (b is not None and len(b) != d["bytes"]):
            bad_art.append(k)
    rec("handoff.artifacts_and_identities 全部对远端 blob", not bad_art,
        "条目=%d mismatch=%d%s" % (len(art), len(bad_art), (" " + ",".join(sorted(bad_art))) if bad_art else ""))
    rec("handoff.设备动作计数全 0 且无批准",
        all(v == 0 for v in (hj or {}).get("device_actions", {}).values()) and
        (hj or {}).get("approval", {}).get("hardware_actions_authorized_by_this_package") == 0 and
        (hj or {}).get("safe_for_enable") is False,
        "device_actions 全 0, authorized=0, safe_for_enable=false")
    rec("handoff.no_self_hash", "sha256" not in hj and "bytes" not in hj,
        "交接 JSON 顶层不自证字节(清单不哈希自身的同一条纪律)")
    rec("handoff.readback 字段在交付头是缺口(不预写)",
        (hj or {}).get("remote_readback") is None or
        bool((hj or {}).get("remote_readback") or {}).get("summary_lines_grepped_not_hand_copied"),
        "remote_readback=%s" % ("None(交付时如实缺口)" if hj and hj.get("remote_readback") is None
                                else "已由收口一步补入"))
    hmd = r.blob(tip, HANDOFF_MD)
    rec("handoff.md.present", hmd is not None and len(hmd) > 1000, "%s B" % (len(hmd) if hmd else "-"))
    for label, rel in [("handoff.md", HANDOFF_MD), ("handoff.json", HANDOFF_JSON)]:
        b = r.blob(tip, rel)
        txt = (b or b"").decode("utf-8", "replace")
        rec("%s 写明 0 设备动作与不含批准" % label,
            "hardware_actions_authorized_by_this_package = 0" in txt or
            "hardware_actions_authorized_by_this_package\": 0" in txt,
            "停点口径在文里")

    print("\n== 4. 要求保持原字节的旧件 ==")
    for path, want, why in FROZEN:
        b = r.blob(tip, path)
        rec("frozen %s" % os.path.basename(path), (sha(b) == want) and b is not None,
            "remote=%s…/%s B expect=%s… (%s)" % ((sha(b) or "-")[:12], len(b) if b else "-",
                                                want[:12], why))

    print("\n== 5. 基线头 f15ba46 上的复用件身份(定点 fetch 后现算) ==")
    fb = subprocess.run(["git"] + PROXY + ["fetch", "--depth=1", "--no-tags", REMOTE, BASE_COMMIT],
                        cwd=ROOT, capture_output=True, text=True)
    if fb.returncode:
        skip("baseline@f15ba46 复用件", "定点 fetch 失败(%s) => 不断言基线侧字节" % fb.stderr.strip()[-120:])
    else:
        for rel, lbl in [(EXEC, "baseline.executor"), (SELFTEST, "baseline.selftest"),
                         (R6C + "/field/plan_core_r6c.json", "baseline.R6C 派生源")]:
            rec(lbl + " 在两个头逐字节相同", r.blob(tip, rel) == r.blob(BASE_COMMIT, rel),
                "%s 本轮未改" % rel)

    print("\n== 6. 本轮不重编/不动产品: 归档件与源码在两个头相同 ==")
    for rel in [FW6A + "/rt-thread-motor-m1-candidate.bin", FW6A + "/build_manifest.json"]:
        rec("未新增构建件 %s" % os.path.basename(rel),
            r.blob(tip, rel) == r.blob(BASE_COMMIT, rel), "tip 与基线同字节")

    print("\nR7_REMOTE_READBACK_SUMMARY|tip=%s|checks=%d|fails=%d|not_checked=%d" %
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
