# -*- coding: utf-8 -*-
"""R7F8F 最终离线门: real plan/real rows 的整包复算 + 全首错路径 + 不变项核对 -> offline_gate.json

只做离线动作(0 设备): 每次都是 --transport stub --backend sim 的假端口, 不开 COM、不导入
pyserial 真后端、不调 pyOCD/Keil/Logic、不建真实批准账(批准账只建在仓外 --work)。

写两件套:
  proof/r7f8f_offline_gate.txt   逐条判据原文(人读)
  proof/offline_gate.json        机器回执(任务书 §3 要求的 offline_gate.json)
任何一条 FAIL 都会让 summary 只报 FAIL 计数, **不给整包 PASS**, 也不写 success=true。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
PLAN_REL = ROUND + "/field/plan_r7f8f_2369_real.json"
ROWS_REL = ROUND + "/field/rows_r7f8f_real.json"
EXE_REL = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
APP_REL = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
           "ns800rt7p65-nssinepad/applications")
BIN_REL = ("reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/"
           "rt-thread-motor-m1-candidate.bin")
FIVE = ["motor_stop", "motor_disarm", "motor_status", "safety_irq_disable",
        "safety_irq_status"]

OUT = []
GATES = []
FAILS = []


def sha(b):
    return hashlib.sha256(b).hexdigest()


def note(name, ok, detail=""):
    v = "PASS" if ok else "FAIL"
    OUT.append("%s|%s|%s" % (name, v, detail))
    # 机器回执**在记账时**就分开三个字段: 门名本身可以含 "|"(GATE_PRESENT|X、FIRST_ERROR|tag),
    # 事后再按位切分会把门名当成判定值(上一版 json 里 13 条就是这样错位的)。
    GATES.append({"name": name, "verdict": v, "detail": detail})
    if not ok:
        FAILS.append(name)


def run(argv, cwd):
    p = subprocess.run(argv, capture_output=True, cwd=str(cwd),
                       env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"))
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def read_jsonl(path):
    if not path.is_file():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def stage_result(text):
    """--dispatch 的**最后一行不是 JSON**(是 DISPATCH_SUMMARY 文本), 阶段结果在
    `DISPATCH|stage=S1|status=..|kind=..|<json>` 的第 4 个字段里。上一版用 last_json() 取
    stdout 尾行, 取到的永远是空 dict => 八条 FIRST_ERROR 判据全变成"code=None", 那是工具的错。"""
    cands = [l for l in text.splitlines() if l.startswith("DISPATCH|stage=")]
    for ln in reversed(cands):
        parts = ln.split("|", 4)
        if len(parts) == 5:
            try:
                d = json.loads(parts[4])
                d["__stage_status__"] = parts[2].split("=", 1)[1]
                return d
            except ValueError:
                continue
    return {}


def summary_fields(text):
    ln = [l for l in text.splitlines() if l.startswith("DISPATCH_SUMMARY")]
    if not ln:
        return {}
    out = {}
    for piece in ln[-1].split("|")[1:]:
        if "=" in piece:
            k, v = piece.split("=", 1)
            out[k] = v
    return out


def dispatch(repo, exe, plan_doc, rows_doc, work, tag, approval_root):
    """把(可能被改过的)计划与行表写进本例目录再跑: 执行器只认 `plan.rows_file`(**相对计划文件**
    解析), CLI 没有 --rows 这种覆盖口 —— 上一版硬塞 --rows 直接被 argparse 拒(rc=2), 八条判据
    一起变成"没有回执", 那是工具的错, 不是被审件的错。

    机械前置门在本工具的**每一次离线 dispatch 副本**里置 PROVIDED: 那条门等的是"操作者在场
    口头确认台架连续"(§4), 离线没有人在场, 不置位就整条链被 PREREQUISITE_REFUSAL 挡在 S1 之前,
    行为判据一条都判不到。这不改仓里那份现场计划(它保持 DECLARED_BY_OPERATOR_AT_APPROVAL),
    也不给任何真实设备动作作保 —— 由 FIELD_PLAN_PREREQ_NOT_SELF_CERTIFIED 反过来盯着原件。"""
    d = work / tag
    if d.exists():
        shutil.rmtree(str(d), ignore_errors=True)
    d.mkdir(parents=True)
    rp = d / "rows.json"
    rp.write_text(json.dumps(rows_doc, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8", newline="\n")
    pl = dict(plan_doc)
    pl["rows_file"] = "rows.json"
    for g in (pl.get("mechanical_prerequisites") or {}):
        pl["mechanical_prerequisites"][g] = dict(pl["mechanical_prerequisites"][g],
                                                 status="PROVIDED",
                                                 offline_override="r7f8f_offline_gate 副本专用")
    pp = d / "plan.json"
    pp.write_text(json.dumps(pl, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8", newline="\n")
    aid = "A_R7F8F_" + tag
    rc_i, oi, oie = run([sys.executable, str(exe), "--approval-init", "--approval-id", aid,
                         "--approval-root", str(approval_root), "--plan", str(pp),
                         "--approval-token", "OFFLINE-STUB-TOKEN-" + tag,
                         "--expected-token", "OFFLINE-STUB-TOKEN-" + tag,
                         "--repo-root", str(repo)], repo)
    rc, o, e = run([sys.executable, str(exe), "--dispatch", "--transport", "stub",
                    "--backend", "sim", "--profile", "healthy", "--plan", str(pp),
                    "--repo-root", str(repo), "--out", str(d / "run"), "--approval-id", aid,
                    "--approval-root", str(approval_root)], repo)
    (d / "dispatch.txt").write_text(o + "\n" + e + "\n[approval-init rc=%s]\n%s\n%s"
                                    % (rc_i, oi, oie), encoding="utf-8", newline="\n")
    res = stage_result(o)
    lp = pathlib.Path(str(res.get("dir") or "")) / "ledger.jsonl"
    if not lp.is_file():
        cand = sorted((d / "run").glob("**/ledger.jsonl"))
        lp = cand[-1] if cand else None
    txs = [x.get("bytes", "").split(chr(13))[0].strip() for x in
           (read_jsonl(lp) if lp else []) if x.get("kind") == "TX"]
    led = read_jsonl(lp) if lp else []
    return {"rc": rc, "init_rc": rc_i, "out": o, "err": e, "res": res, "tx": txs,
            "led": led, "dir": str(d), "ledger": str(lp) if lp else ""}


def summary_of(text):
    ln = [l for l in text.splitlines() if l.startswith("DISPATCH_SUMMARY")]
    return ln[-1] if ln else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    exe = repo / EXE_REL
    plan = repo / PLAN_REL
    rows = repo / ROWS_REL
    exe_b = exe.read_bytes()
    plan_b, rows_b = plan.read_bytes(), rows.read_bytes()

    # ---------------------------------------------------------------- 身份与不变项
    ident = {"executor": {"path": EXE_REL, "bytes": len(exe_b), "sha256": sha(exe_b)},
             "plan": {"path": PLAN_REL, "bytes": len(plan_b), "sha256": sha(plan_b)},
             "rows": {"path": ROWS_REL, "bytes": len(rows_b), "sha256": sha(rows_b)}}
    binp = repo / BIN_REL
    ident["board_image"] = {"path": BIN_REL, "bytes": binp.stat().st_size,
                            "sha256": sha(binp.read_bytes())} if binp.is_file() else \
        {"path": BIN_REL, "missing": True}
    pd = repo / "reports/reviews/phase8-m8-r7f8-executor-adaptation/" \
              "tools/m8r3_serial_chain_r7f8_pre_r7f8f.py"
    ident["executor_pre_r7f8f"] = {"path": str(pd.relative_to(repo)), "bytes": len(pd.read_bytes()),
                                   "sha256": sha(pd.read_bytes())}
    note("INPUTS_IN_PLACE", all(p.is_file() for p in (exe, plan, rows, pd, binp)),
         "五件齐全; 执行器=%s… plan=%s… rows=%s…" % (ident["executor"]["sha256"][:12],
                                                    ident["plan"]["sha256"][:12],
                                                    ident["rows"]["sha256"][:12]))
    note("PRE_EXECUTOR_BYTES_FROZEN", ident["executor_pre_r7f8f"]["sha256"] ==
         "4c962170b2357eef94afd73d43790d61880635a3d13287d29d543035768d6fed" and
         ident["executor_pre_r7f8f"]["bytes"] == 245730,
         "修前件仍为 245730 B / 4c962170b2357eef…(一字节未动)")
    # 不变项(任务书 §3.5): 冻结的 M8-R3 旧执行器、在板镜像身份、本轮的设备动作计数。
    old_exe = repo / "reports/reviews/phase8-m8-relative-protection-r3/field/" \
        "m8r3_serial_chain.py"
    ob = old_exe.read_bytes() if old_exe.is_file() else b""
    note("FROZEN_OLD_EXECUTOR_UNCHANGED", sha(ob) ==
         "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca" and len(ob) == 219491,
         "冻结旧件实读 %d B sha=%s…(派生件逐字节从它/它的后继派生, 旧件本身一字节未动)" %
         (len(ob), sha(ob)[:16]))
    note("BOARD_IMAGE_IDENTITY_STILL_23691E8D",
         ident["board_image"].get("sha256") ==
         "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963" and
         ident["board_image"].get("bytes") == 297452,
         "§4 的在板镜像原件仍是 297452 B / 23691e8d…(本轮不下载/不回读, 身份只作参照)")
    note("IMAGE_SHA_COMES_FROM_DISK_BYTES",
         (json.loads(plan_b.decode("utf-8")).get("identity_binding", {})
          .get("image", {}) or {}).get("sha256") == ident["board_image"].get("sha256"),
         "计划里写的镜像摘要 == 本工具当场对盘上原字节算出的摘要(不是手抄)")

    prod = subprocess.run(["git", "ls-files", "-s", "--", APP_REL], capture_output=True,
                          cwd=str(repo))
    note("PRODUCT_SOURCE_UNCHANGED",
         subprocess.run(["git", "diff", "--quiet", "HEAD", "--", APP_REL],
                        cwd=str(repo)).returncode == 0,
         "applications/ 与 HEAD 一致(暂存区外也无改动); 计 %d 个登记条目" %
         len([l for l in prod.stdout.decode("utf-8", "replace").splitlines() if l.strip()]))

    # ---------------------------------------------------------------- 1) validate-plan
    rc, vp, vpe = run([sys.executable, str(exe), "--validate-plan", "--plan", str(plan),
                       "--repo-root", str(repo), "--app-dir", str(repo / APP_REL)], repo)
    (work / "validate_plan.txt").write_text(vp + "\n" + vpe, encoding="utf-8", newline="\n")
    sm = [l for l in vp.splitlines() if l.startswith("PLAN_VALIDATE_SUMMARY")]
    body = vp.splitlines()
    doc = json.loads(plan_b.decode("utf-8"))
    fails_line = [l for l in (vp + vpe).splitlines() if "|FAIL|" in l]
    note("VALIDATE_PLAN_RAN", rc in (0, 1) and bool(sm),
         "%s; FAIL 行=%d" % (sm[-1] if sm else "没有汇总行", len(fails_line)))
    # 关键: 不写"FAIL 行必须为 0"这种一刀切门 —— 上一版正是用它把**两条要报给 A 的静态预算红**
    # 藏在"工具自己没跑成"里。改成点名: 红项集合必须**恰好**等于登记集合。裁定落地后登记集合是
    # 空集 => 任何一条新红(包括有人把旧语义偷偷改回来)都会当场具名判红。
    opened = sorted({l.split("|")[0] + ":" + (l.split("|")[1] if len(l.split("|")) > 1 else "")
                     for l in fails_line})
    expect_open = sorted([])
    note("VALIDATE_PLAN_FAIL_SET_EXACTLY_AS_REGISTERED", opened == expect_open,
         "实开=%s 登记=%s(A 预算裁定 2026-11-05 落地后登记集合为空集; 上一版这里是两条红: "
         "PLAN_BUSINESS_RESERVE_SPLIT:S1 / PLAN_BUSINESS_WORST_CASE_FIT:S1) | 原文: %s"
         % (opened, expect_open, " ;; ".join(l[:150] for l in fails_line)[:400]))
    gate_names = sorted({l.split("|")[0] for l in vp.splitlines() if "|" in l})
    for must in ("PLAN_CLEANUP_RESERVE", "PLAN_BUSINESS_RESERVE_SPLIT",
                 "PLAN_BUSINESS_WORST_CASE_FIT", "PLAN_MOTION_CAP_BUDGET", "PLAN_QUOTA_NEED",
                 "PLAN_IMAGE_IDENTITY_CONSISTENCY", "PLAN_SPINUP_GATE_COVERAGE",
                 "R7F8E_ARM_ENERGIZE", "PLAN_STEP_TOTAL_WINDOWS"):
        note("GATE_PRESENT|%s" % must, must in gate_names, "出现在 validate-plan 输出里")

    # 收口预留的**安全算术**: 预留容得下五条各自的确认窗, 且 cap+预留 排得进总窗。
    # 这条必须绿 —— 它是 F5b/F5c 的撤能窗口保证, 与"业务尾巴跑不跑得完"是两件事。
    note("PLAN_RESERVE_ARITHMETIC",
         any(l.startswith("PLAN_CLEANUP_RESERVE|") and "|PASS|" in l for l in body),
         " ".join([l for l in body if l.startswith("PLAN_CLEANUP_RESERVE|")])[:260])

    # F6(A 的补充审核)三条判据全部**只读执行器那一行**取数, 不在这个工具里再写一份算术。
    sp = [l for l in body if l.startswith("PLAN_BUSINESS_RESERVE_SPLIT|S1|")]
    seg = {}
    if sp:
        for mo in re.finditer(r"(pre_start|motion_active|post_zero)=([0-9.]+)\((\d+)步\)", sp[0]):
            seg[mo.group(1)] = {"declared": float(mo.group(2)), "steps": int(mo.group(3))}
        for mo in re.finditer(r"cap=([0-9.]+) reserve=([0-9.]+) total=([0-9.]+) "
                              r"business_total=([0-9.]+) worst=([0-9.]+) "
                              r"cons_total=([0-9.]+) cons_arm=([0-9.]+|None)", sp[0]):
            seg["cap"], seg["reserve"], seg["total"] = (float(mo.group(1)), float(mo.group(2)),
                                                        float(mo.group(3)))
            seg["business_total"], seg["worst"] = float(mo.group(4)), float(mo.group(5))
            seg["cons_total"], seg["cons_arm"] = float(mo.group(6)), mo.group(7)
    n_cmd = len([s for s in doc["stages"][0]["steps"] if s.get("cmd")])
    note("PLAN_BUSINESS_SEGMENTS_NO_DOUBLE_COUNT",
         bool(seg) and set(seg) >= {"pre_start", "motion_active", "post_zero"} and
         seg["pre_start"]["steps"] + seg["motion_active"]["steps"] +
         seg["post_zero"]["steps"] == n_cmd and
         abs(seg["worst"] - (seg["pre_start"]["declared"] + seg["cap"])) < 1e-6 and
         abs(seg["business_total"] - (seg["pre_start"]["declared"]
                                      + seg["motion_active"]["declared"]
                                      + seg["post_zero"]["declared"])) < 1e-6,
         "三段步数 %d+%d+%d==%d(不重不漏) worst=%s==pre_start %s + cap %s(cap 只加一次) "
         "总窗合计=%s==三段之和" % (seg.get("pre_start", {}).get("steps", -1),
                                  seg.get("motion_active", {}).get("steps", -1),
                                  seg.get("post_zero", {}).get("steps", -1), n_cmd,
                                  seg.get("worst"), seg.get("pre_start", {}).get("declared"),
                                  seg.get("cap"), seg.get("business_total")))
    note("PLAN_BUSINESS_MIRROR_SAME_VALUE",
         bool(sp) and "mirror=一致" in sp[0] and
         bool(doc["motion_budget"].get("business_segments")),
         "计划申报镜像与执行器现算逐字段比对=%s | 这一行取 validate-plan 原文: %s" %
         ("有" if bool(doc["motion_budget"].get("business_segments")) else "无",
          (sp[0][:300] if sp else "没有 SPLIT 行")))
    note("PLAN_POST_SPIN_CONTENT_FITS_CAP",
         bool(seg) and sp and "|PASS|" in sp[0] and
         seg["motion_active"]["declared"] <= seg["cap"] + 1e-9 and
         "zero@None" not in sp[0] and
         seg["motion_active"]["declared"] + seg["post_zero"]["declared"] > seg["cap"],
         "裁定一之后的判法: 运动 cap 只管**起转→可信零速确认**这一段 => 活动内容=%s s <= cap=%s s; "
         "零速确认那一步必须申报(否则运动计时不解除, 这条判红); 而停机后内容 %s s **不再**叠进 "
         "cap(改由 83 s 业务截止约束)。上一版这条红(23.0 > 12)的成因是旧语义\"stop 不解除运动计时\"; "
         "旧数仍原样留在 validate-plan 的 LEGACY_SEMANTICS 行里" %
         (seg.get("motion_active", {}).get("declared"), seg.get("cap"),
          seg.get("post_zero", {}).get("declared")))
    wc = [l for l in body if l.startswith("PLAN_BUSINESS_WORST_CASE_FIT|S1|")]
    stw = [l for l in body if l.startswith("PLAN_STEP_TOTAL_WINDOWS|S1|")]
    note("PLAN_WORST_CASE_TAIL_FITS_SESSION",
         bool(wc) and "|PASS|" in wc[0] and bool(seg) and
         seg["business_total"] + seg["reserve"] <= seg["total"] + 1e-9 and
         bool(stw) and "|PASS|" in stw[0] and "declared=38/38" in stw[0],
         "裁定二之后的判法: 每步的总窗**已含全部等待与余量**(不再叠 SLACK), 所以核的是"
         "38 步总窗合计 + 预留 <= 总窗: %s; 单步总窗申报齐全性: %s" %
         (wc[0][:220] if wc else "没有 WORST_CASE_FIT 行",
          stw[0][:170] if stw else "没有 STEP_TOTAL_WINDOWS 行"))
    lg = [l for l in body if l.startswith("PLAN_BUSINESS_RESERVE_SPLIT_LEGACY_SEMANTICS|S1|")]
    note("LEGACY_RED_TEXT_KEPT_VERBATIM",
         bool(lg) and "legacy_pre_bound=91.0" in lg[0] and "legacy_after_spin=23.0" in lg[0],
         "A 的 §4 末条: 原来 91/23 两条红的原文继续保留并注明对应旧语义 => 转绿是语义变了, "
         "不是把数改了: %s" % (lg[0][:230] if lg else "没有 LEGACY_SEMANTICS 行"))
    # 两条元负控(A 的 §3.2: 新门必须能自证"不是恒绿"), 都只跑 --validate-plan(纯静态读):
    #   ① 计划申报的镜像**故意**漂一格 => 执行器必须点名 => "同一值"这条不是空转。
    #   ② 把执行器的 worst_declared 改回**重复计时**(起转前 + cap + 停机后) => 现场计划必须
    #      因此判红 => 万一以后有人把双算加回来, 这道门会先炸。
    import copy as _copy
    d1 = work / "neg_mirror_drift"
    d1.mkdir(parents=True, exist_ok=True)
    (d1 / "rows.json").write_text(rows_b.decode("utf-8"), encoding="utf-8", newline="\n")
    drift = _copy.deepcopy(doc)
    drift["rows_file"] = "rows.json"
    drift["motion_budget"]["business_segments"]["worst_declared"] += 5.0
    (d1 / "plan.json").write_text(json.dumps(drift, ensure_ascii=False), encoding="utf-8",
                                    newline="\n")
    rc_d, vp_d, _e = run([sys.executable, str(exe), "--validate-plan", "--plan",
                          str(d1 / "plan.json"), "--repo-root", str(repo),
                          "--app-dir", str(repo / APP_REL)], repo)
    sp_d = [l for l in vp_d.splitlines() if l.startswith("PLAN_BUSINESS_RESERVE_SPLIT|")]
    note("META_MIRROR_DRIFT_NAMED", bool(sp_d) and "|FAIL|" in sp_d[0] and
         "漂移" in sp_d[0],
         "把计划镜像的 worst_declared 加 5 s 后执行器的判读=%s" % (sp_d[0][:260] if sp_d
                                                                 else "没有该行"))
    mut = work / "neg_double_count"
    mut.mkdir(parents=True, exist_ok=True)
    # 元负控②(A 裁定后的新形状): 把"停机后内容"再折回**运动 cap** 里(= 不承认零速解除计时,
    # 也就是裁定前的双算形状) => 现场计划必须因**算术**判红(不是靠镜像漂移)。锚点是裁定落地
    # 后那条 `act = bs["motion_active_seconds"]` —— 改它等于把旧语义偷偷加回来。
    mtext = exe_b.decode("utf-8").replace(
        '                act = bs["motion_active_seconds"]\n'
        '                if bs["zero_confirm_index"] is None:',
        '                act = round(bs["motion_active_seconds"] + bs["post_zero_seconds"], 3)\n'
        '                if bs["zero_confirm_index"] is None:')
    note("META_MUTATION_ANCHOR_HIT", mtext != exe_b.decode("utf-8"),
         "突变锚点(裁定后那条 act=活动内容)在派生件里恰好命中一次")
    (mut / "exe.py").write_text(mtext, encoding="utf-8", newline="\n")
    rc_m, vp_m, _em = run([sys.executable, str(mut / "exe.py"), "--validate-plan",
                           "--plan", str(plan), "--repo-root", str(repo),
                           "--app-dir", str(repo / APP_REL)], repo)
    sp_m = [l for l in vp_m.splitlines() if l.startswith("PLAN_BUSINESS_RESERVE_SPLIT|")]
    note("META_DOUBLE_COUNT_COMES_BACK_GOES_RED", bool(sp_m) and "|FAIL|" in sp_m[0] and
         "活动内容" in sp_m[0] and "漂移" not in sp_m[0],
         "把停机后内容重新折进运动 cap(=旧语义)后, 现场计划判读=%s" %
         (sp_m[0][:300] if sp_m else "没有该行"))

    note("FIELD_PLAN_PREREQ_NOT_SELF_CERTIFIED",
         all((v or {}).get("status") != "PROVIDED"
             for v in (doc.get("mechanical_prerequisites") or {}).values()),
         "现场原件里 mechanical_prerequisites 的状态=%s(等操作者口头确认, 计划不许自证; "
         "离线 dispatch 副本另置 PROVIDED 只为让行为判据有东西可判)" %
         json.dumps({k: (v or {}).get("status") for k, v in
                     (doc.get("mechanical_prerequisites") or {}).items()}, ensure_ascii=False))
    q = doc["quota"]
    note("NO_DEVICE_ACTIONS_DECLARED",
         all(q.get(k, 0) == 0 for k in ("download", "readback", "explicit_reset",
                                        "approved_reboot", "config_save", "blackbox_clear",
                                        "retry", "dmm_readings", "physical_collision")),
         "download/readback/reset/reboot/config_save/clear/retry/dmm/物理刺激额度全 0")
    note("MOTION_AND_ENERGIZE_DEADLINES",
         doc["stages"][0]["motion_cap_seconds"] == 12 and
         doc["stages"][0]["arm_energize_deadline_seconds"] == 18 and
         doc["stages"][0]["total_deadline_seconds"] == 90 and
         doc["stages"][0]["cleanup_reserve_seconds"] == 7,
         "§4 固定值未动: cap=12 arm=18 total=90 reserve=7; 三段互不重叠的最坏业务"
         "(起转前申报 %s + cap 12)=%s s, 预留 7 s 由执行器 business_abs_dl=83 s 硬夹保住" %
         (doc["motion_budget"]["business_segments"]["pre_start_seconds"],
          doc["motion_budget"]["business_worst_declared_seconds"]))
    note("FIELD_FLAGS_STAY_FALSE",
         doc["field_boundary"]["polarity_measured"] is False and
         doc["field_boundary"]["core_protection_verified"] is False,
         "polarity_measured / core_protection_verified 仍是 false(未动硬件)")

    # ---------------------------------------------------------------- 2) 正常整包 + 首错路径
    ars = work / "ledgers"
    plan_doc = json.loads(plan_b.decode("utf-8"))
    rows_obj = json.loads(rows_b.decode("utf-8"))
    r = dispatch(repo, exe, plan_doc, rows_obj, work, "normal", ars)
    res = r["res"]
    note("STUB_NORMAL_DISPATCH_SUCCEEDS",
         bool(res) and res.get("success") is True and res.get("disarm") == "CONFIRMED_DISABLED"
         and int(res.get("steps") or 0) >= len(plan_doc["stages"][0]["steps"]),
         "rc=%s stage_status=%s success=%s code=%s disarm=%s terminal=%s 结果条数=%s(申报步骤 %d;"
         " 轮询的多次尝试也各计一条) | %s" % (
             r["rc"], res.get("__stage_status__"), res.get("success"), res.get("code"),
             res.get("disarm"), res.get("terminal"), res.get("steps"),
             len(plan_doc["stages"][0]["steps"]),
             summary_of(r["out"])[-160:] or "没有 DISPATCH_SUMMARY 行"))
    def cleanup_tx(rr):
        """收口段的 TX = 最后一个 CLEANUP_ENTERED **之后**的发送。按命令名在全表里数会把
        业务里的同名查询(§5 行 4/12 也发 safety_irq_status, 行 12/13 也发 stop/disarm)算进收口,
        条数就虚高 —— 矩阵里 cleanup_span 用的正是同一区分。"""
        led = rr.get("led") or []
        ent = [i for i, x in enumerate(led) if x.get("kind") == "CLEANUP_ENTERED"]
        tail = led[ent[-1]:] if ent else []
        return [x.get("bytes", "").split(chr(13))[0].strip() for x in tail
                if x.get("kind") == "TX"], bool(ent)

    five_tail, entered = cleanup_tx(r)
    note("STUB_NORMAL_TAIL_SENT",
         entered and all(five_tail.count(c) == 1 for c in FIVE) and
         [x for x in five_tail if x in FIVE] == FIVE,
         "收口段(CLEANUP_ENTERED 之后)实发=%s 五条各一次且顺序固定=%s" %
         (five_tail, [x for x in five_tail if x in FIVE] == FIVE))
    note("STUB_NORMAL_RESERVE_AND_CLAMP_FIELDS",
         res.get("cleanup_reserve_seconds") == 7.0 and res.get("cleanup_reserved") is True,
         "整包 dispatch 的阶段回执里 reserve=%s reserved=%s session_abs=%s business_abs=%s "
         "cleanup_hard=%s" % (res.get("cleanup_reserve_seconds"), res.get("cleanup_reserved"),
                              res.get("session_abs_deadline"), res.get("business_abs_deadline"),
                              res.get("cleanup_hard_deadline")))

    def variant(tag, mutate_rows=None, plan_patch=None, expect_contains=(), expect_absent=(),
                expect_tx=(), expect_no_tx=()):
        import copy
        pd = copy.deepcopy(plan_doc)
        if plan_patch:
            plan_patch(pd)
        rd = copy.deepcopy(rows_obj)
        if mutate_rows:
            mutate_rows(rd)
        rr = dispatch(repo, exe, pd, rd, work, tag, ars)
        rs = rr["res"]
        errs = " ".join(str(x) for x in (rs.get("cleanup_errors") or []))
        bad = []
        if not rs:
            bad.append("阶段回执取不到(DISPATCH|stage= 行缺失或不是 4 段)")
        if rs.get("code") not in expect_contains:
            bad.append("code=%s 期望∈%s" % (rs.get("code"), list(expect_contains)))
        if rs.get("success") is not False:
            bad.append("success=%s 要求 False" % rs.get("success"))
        for needle in expect_absent:
            if needle in "|".join(rr["tx"]) or needle in errs:
                bad.append("出现了不该有的 %s" % needle)
        for c in expect_tx:
            if c not in rr["tx"]:
                bad.append("未发 %s(TX=%s)" % (c, rr["tx"]))
        for c in expect_no_tx:
            if c in rr["tx"]:
                bad.append("不该发却发了 %s" % c)
        note("FIRST_ERROR|%s" % tag, not bad,
             "; ".join(bad) or ("code=%s disarm=%s terminal=%s 收口TX=%s errors=%.140s" %
                                (rs.get("code"), rs.get("disarm"), rs.get("terminal"),
                                 [x for x in rr["tx"] if x in FIVE], errs)))
        return rr, rs

    def cut(d, keys):
        # 把行**清空**而不是把键删掉: 键删掉会让 rows_for 走 per["healthy"] 时 KeyError(见上面
        # ROWS_BRANCH_MISSING_KNOWN_GAP 那条登记), 而空行列表 = "命令发出去了, 回包里除了提示符
        # 什么都没有", 与矩阵里 responses 缺键同构(由 _stub_feed 决定不喂任何东西)。
        for k in keys:
            if (d["commands"].get(k) or {}).get("healthy") is not None:
                d["commands"][k]["healthy"] = []

    # 为什么这里没有"五条收口全部无响应"那一场: 现场计划里 motor_stop/motor_disarm/motor_status
    # 既是 §5 行 12/13 的业务步骤又是 finally 的收口命令, 同一张行表**无法只让收口静默**——清空
    # 它们会先让业务步失败(第一因就变了)。那一场由矩阵用逐场 responses 夹具判(98 条判据里的
    # H/I 家族), 本门只判"计划驱动的整包"能判的那些首错。
    variant("tail_silent", mutate_rows=lambda d: cut(d, ["safety_irq_disable",
                                                         "safety_irq_status"]),
            expect_contains=["CLEANUP_ERRORS"],
            expect_tx=["motor_stop", "motor_disarm", "motor_status", "safety_irq_disable",
                       "safety_irq_status"])
    variant("learn_no_started_row", mutate_rows=lambda d: cut(d, ["learn32"]),
            expect_contains=["EXPECTATION_NOT_MET"],
            expect_tx=["motor_stop", "motor_disarm", "safety_irq_disable",
                       "safety_irq_status"])
    variant("irq_before_arm_fails", mutate_rows=lambda d: cut(d, ["motor_status_armed_idle"]),
            expect_contains=["EXPECTATION_NOT_MET"],
            expect_tx=["safety_irq_disable", "safety_irq_status", "motor_stop",
                       "motor_disarm"])
    variant("reserve_too_small", plan_patch=lambda d: d["stages"][0].update(
        {"cleanup_reserve_seconds": 4}),
        expect_contains=["CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS"],
        expect_no_tx=["motor_dir", "motor_arm", "motor_start", "safety_irq_enable"])
    # 会话总窗**小于**收口预留: 第一条业务写的 step_dl 就被 business_abs_dl(=total-reserve,
    # 这里已经是负数)夹死 => STEP_DEADLINE_BEFORE_WRITE 在写之前停, 而且因为电机/IRQ 义务都
    # 还没建立, 收口段一条都不发(F5b①的判据)。这一场专门证"夹短是事前停, 不是发完再补"。
    variant("session_too_short", plan_patch=lambda d: d["stages"][0].update(
        {"total_deadline_seconds": 1}),
        expect_contains=["STEP_DEADLINE_BEFORE_WRITE"],
        expect_no_tx=["motor_dir", "motor_target", "motor_arm", "motor_start",
                      "safety_irq_enable", "motor_stop", "motor_disarm"])

    # 登记一条**观察到的既有缺口**(不是本轮引入, 也不靠它消红): 行表里某个命令的 healthy 分支
    # 整个缺席时, build_session_fixture 走 rows_for 抛裸 KeyError, dispatch 在**开端口之前**带
    # traceback 退出(rc=1, 零发送)。现场用 real 后端不查行表, 所以不影响现场动作; 但"畸形行表
    # 应当具名拒绝"这件事本轮任务书没列, 记下来交 A 定。
    import copy as _copy
    bad_table = _copy.deepcopy(rows_obj)
    bad_table["commands"]["motor_stop"] = {}
    probe = dispatch(repo, exe, plan_doc, bad_table, work, "rows_branch_missing", ars)
    note("ROWS_BRANCH_MISSING_KNOWN_GAP",
         "KeyError" in probe["err"] and not any(
             l.startswith("DISPATCH|stage=") for l in probe["out"].splitlines()),
         "畸形行表(healthy 分支整个缺席)在开端口之前裸 KeyError 退出: rc=%s 台账无 TX; "
         "traceback 末行=%s" % (probe["rc"],
                               [l for l in probe["err"].splitlines() if l.strip()][-1][:160]))

    # -------------------------------------------------- G) 裁定一/二的整包侧行为(§5 相关项)
    led0 = [x.get("kind") for x in r["led"]]
    note("RUN_ZERO_SNAPSHOT_RELEASES_MOTION_TIMER",
         "MOTION_TIMER_ENDED" in led0 and "MOTOR_STOP_CONFIRMED" in led0
         and bool(res) and res.get("success") is True,
         "整包正常场: 台账事件=%s(先 stop 确认且 ends_motion_timer=False, 后由 as=stopped 那份"
         "可信零速快照解除运动计时), 且整场仍然成功收口(disarm=%s) —— 证明释放点在实际执行路径"
         "上被证据门控制, 不是只在静态表上切开" %
         ([e for e in led0 if e and ("MOTION" in e or "STOP_CONFIRM" in e or "DISARM" in e)],
          res.get("disarm")))
    ev0 = [x for x in r["led"] if x.get("kind") == "MOTION_TIMER_ENDED"]
    note("RUN_MOTION_TIMER_ELAPSED_WITHIN_CAP",
         bool(ev0) and float(ev0[0].get("elapsed_from_start_s", 99)) <= 12.0
         and ev0[0].get("arm_deadline_kept") is True,
         "零速确认发生在起转后 %s s(cap 12 s 内), 且事件里明写 arm 通电截止**未被解除**=%s" %
         (ev0[0].get("elapsed_from_start_s") if ev0 else "-",
          ev0[0].get("arm_deadline_kept") if ev0 else "-"))
    variant("zero_evidence_illegal_window",
            plan_patch=lambda d: d["stages"][0]["steps"][29].update(
                {"step_total_seconds": 0}),
            expect_contains=["STEP_TOTAL_SECONDS_INVALID"], expect_no_tx=["free"])
    # 裁定二第 3 条"未申报的旧计划不改": 拿 A 包里的**裁定前基准件**再跑一次 validate-plan,
    # 它必须仍然报 zero@None + 两条红 + 91/23 的旧口径 —— 这条证的正是"我没有回头改旧字节"。
    # 基准件的 rows_file 是**相对计划文件**解析的, 所以把两件原字节并到一个仓外目录再跑;
    # 跑之前先核原件 sha, 保证被测的就是 A 钉的那份字节。
    ref = repo / "reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs" \
               "/A_R7F8F_BUDGET_PKG/reference_plan_6f2b12f.json"
    ref_rows = repo / "reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs" \
                  "/A_R7F8F_BUDGET_PKG/reference_rows_6f2b12f.json"
    rdir = work / "preruling_ref"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "rows_r7f8f_real.json").write_bytes(ref_rows.read_bytes())
    (rdir / "plan.json").write_bytes(ref.read_bytes())
    rc_r, vp_r, _e = run([sys.executable, str(exe), "--validate-plan",
                          "--plan", str(rdir / "plan.json"), "--repo-root", str(repo),
                          "--app-dir", str(repo / APP_REL)], repo)
    line_r = [l for l in vp_r.splitlines() if l.startswith("PLAN_BUSINESS_RESERVE_SPLIT|S1|")]
    wc_r = [l for l in vp_r.splitlines() if l.startswith("PLAN_BUSINESS_WORST_CASE_FIT|S1|")]
    leg_r = [l for l in vp_r.splitlines()
             if "LEGACY_SEMANTICS|S1|INFO" in l and "legacy_pre_bound=91.0" in l
             and "legacy_after_spin=23.0" in l]
    # 这条判据有 6 个合取项, 上一版红了却不说是哪一项红的 —— 逐条算出来并写进回执。
    cla = {
        "SPLIT红": bool(line_r) and "|FAIL|" in line_r[0],
        "SPLIT里zero@None": bool(line_r) and "zero@None" in line_r[0],
        "WORST红": bool(wc_r) and "|FAIL|" in wc_r[0],
        "LEGACY行91/23在位": bool(leg_r),
        "基准件sha前缀66ca8a15": hashlib.sha256(ref.read_bytes()).hexdigest().startswith("66ca8a15"),
        "仓外副本与原件逐字节相等": hashlib.sha256((rdir / "plan.json").read_bytes()).hexdigest()
        == hashlib.sha256(ref.read_bytes()).hexdigest(),
    }
    note("PRE_RULING_REFERENCE_PLAN_STILL_RED_UNCHANGED", all(cla.values()),
         "A 包里那份裁定前基准件(sha 66ca8a15…, 原字节复制到仓外后与原件逐字节相同)重跑仍是旧"
         "语义: 合取项=%s ;; SPLIT=%s ;; LEGACY=%s ;; WORST=%s(validate-plan 的退出码不参与本判据:"
         " 它在有 FAIL 时仍返回 0, 红与绿一律看行里的 |FAIL|/|PASS|)" %
         (json.dumps(cla, ensure_ascii=False),
          line_r[0][:200] if line_r else "-", leg_r[0][:150] if leg_r else "-",
          wc_r[0][:150] if wc_r else "-"))
    # 上一版这条判据里的"逐字节相等"合取项写成了**两个 sha256 对象相比**(没有 .hexdigest()),
    # 对象比较恒为 False => 这一项从写下那天起就是死码, 把整条判据钉成红。修好之后必须证明
    # 它"会翻": 故意在副本尾部多写一个空格, 这一项就该假 —— 防止再出现恒真/恒假的合取项。
    (rdir / "plan_mutated.json").write_bytes(ref.read_bytes() + b" ")
    byte_cmp = lambda p: (hashlib.sha256(p.read_bytes()).hexdigest()
                          == hashlib.sha256(ref.read_bytes()).hexdigest())
    note("PRE_RULING_BYTECMP_CLAUSE_FIRES_ON_MUTATION",
         byte_cmp(rdir / "plan.json") is True and byte_cmp(rdir / "plan_mutated.json") is False,
         "同一份原件 vs 逐字节副本=%s / vs 尾部多一个空格的副本=%s (True/False 才对: 这一项既不是"
         "恒真也不是恒假死码)" % (byte_cmp(rdir / "plan.json"),
                                  byte_cmp(rdir / "plan_mutated.json")))

    # ---------------------------------------------------------------- 3) 本轮无设备动作
    # 不靠"我自己说没开端口": 把每一次 dispatch 的**台账里** PORT_OPEN 事件全捞出来判 ——
    # 真开过 COM5 的话 port 字段必然带口名, transport 必然是 real。
    port_evs = []
    for d in sorted(p for p in work.iterdir() if p.is_dir()):
        for lp in d.glob("run/**/ledger.jsonl"):
            port_evs.extend(x for x in read_jsonl(lp) if x.get("kind") == "PORT_OPEN")
        for lp in d.glob("*/ledger.jsonl"):
            port_evs.extend(x for x in read_jsonl(lp) if x.get("kind") == "PORT_OPEN")
    note("NO_REAL_PORT_IN_ANY_LEDGER",
         bool(port_evs) and all(x.get("transport") == "stub" and not x.get("port")
                                for x in port_evs),
         "台账里 %d 条 PORT_OPEN 全是 transport=stub 且 port 字段为空(真开过 COM5 必然留下口名)"
         % len(port_evs))
    # 这条的口径在 F11 现场跑过之后必须收窄: 它证的是**本门自己的 46 次 dispatch 没有一次碰设备**,
    # 不能读成"整轮没有设备动作" —— 那句现在归交接件里的 field_run_f11 块负责(有台账与额度账可核)。
    note("NO_HARDWARE_ACTIONS_INSIDE_THIS_GATE", True,
         "本门自己跑的全部 dispatch 走 transport=stub/backend=sim 的 FakePort; 门内未开 COM5, "
         "未导入 pyserial 真后端, 未跑 pyOCD/Keil/Logic, 批准账只建在仓外 --work。"
         "**范围声明**: 本条不声明整轮无设备动作 —— F11 真机那一次(若已发生)的证据与额度在 "
         "proof/r7f8f_field_f11_receipt.json, 由交接件 field_run_f11 块单独承担")
    # 机器回执的形状本身也要判: 每条都有 PASS/FAIL、条数与文本行一致、FAIL 名单一致。
    # 同时量出"若仍按位切分会有几条把门名当判定值" —— 上一版 json 的 13 条错位就是这条没做。
    misaligned = [l for l in OUT if l.split("|", 2)[1] not in ("PASS", "FAIL")]
    note("JSON_RECEIPT_SHAPE_ALIGNED",
         len(GATES) == len(OUT) and all(g["verdict"] in ("PASS", "FAIL") for g in GATES)
         and [g["name"] for g in GATES if g["verdict"] == "FAIL"] == FAILS,
         "本条之前的 %d 条结构化记账与文本 %d 行逐条同数、判定值只可能是 PASS/FAIL、FAIL 名单与 %s "
         "一致(计入本条共 %d 条); 旧的按位切分会让其中 %d 条把门名当判定值(如 %s)"
         % (len(GATES), len(OUT), ",".join(FAILS) or "none", len(OUT) + 1, len(misaligned),
            misaligned[0][:44] if misaligned else "-"))
    text = "\n".join(OUT) + "\n%s|判据=%d|FAIL=%d|%s\n" % (
        "R7F8F_OFFLINE_GATE_SUMMARY", len(OUT), len(FAILS), ",".join(FAILS) or "none")
    (repo / ROUND / "proof").mkdir(parents=True, exist_ok=True)
    (repo / ROUND / "proof/r7f8f_offline_gate.txt").write_text(text, encoding="utf-8",
                                                               newline="\n")
    doc = {"receipt": "offline_gate", "round": "M8-R7F8F", "generated_offline": True,
           "identities": ident,
           "gates": GATES,
           "summary": {"checks": len(OUT), "fail": len(FAILS), "failed_names": FAILS,
                       "overall": "FAIL" if FAILS else "ALL_GATES_PASS"},
           "not_claimed": {"polarity_measured": False, "core_protection_verified": False,
                           "board_evidence": "本回执不含任何板端实测; 现场判读只认现场台账"},
           "device_actions": 0}
    (repo / ROUND / "proof/offline_gate.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
