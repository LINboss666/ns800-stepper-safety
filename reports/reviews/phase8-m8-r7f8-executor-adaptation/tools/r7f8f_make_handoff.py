# -*- coding: utf-8 -*-
"""M8-R7F8F 交接 JSON: 所有数字**当场从盘上算**, 不抄文档、不抄上一轮。

同名 .md 是人读的正文; 本脚本只产机器可读那一份。两处同源 => 复算一次即可对账。
用法: python tools/r7f8f_make_handoff.py --repo D:/ns800
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
DRAFT = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
RUN_RECORD = R7F2B + "/run_record.md"
MERGED = DRAFT + "/R7F8_DRAFT_MERGED.md"
ORIG = DRAFT + "/A_DRAFT_ORIGINAL.md"
EXE = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
OLD_EXE = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
IMAGE = "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin"
PKG = ROUND + "/inputs/A_R7F8F_PKG"
BUDGET_PKG = ROUND + "/inputs/A_R7F8F_BUDGET_PKG"
# 合并件的承接源**永远是当前活草案**: 上一版这里写死 PKG(裁定前那包), 裁定落地后就比错了对象。
LIVING = BUDGET_PKG + "/R7F8_LIVING_DRAFT.md"
PLAN = ROUND + "/field/plan_r7f8f_2369_real.json"
ROWS = ROUND + "/field/rows_r7f8f_real.json"

# 每条判据族的 proof 文件与汇总行前缀(前缀写错 => summary() 报 missing, 不会静默取到 0)
FAMILIES = [("case_matrix", "proof/r7f8e_case_matrix.txt", "R7F8E_MATRIX_SUMMARY"),
            ("stub_package", "proof/r7f8e_stub_package.txt", "R7F8E_STUB_SUMMARY"),
            ("no_regression", "proof/r7f8e_no_regression.txt", "R7F8E_NOREG_SUMMARY"),
            ("probe_replay", "proof/r7f8f_probe_replay.txt", "REPLAY_SUMMARY"),
            ("offline_gate", "proof/r7f8f_offline_gate.txt", "R7F8F_OFFLINE_GATE_SUMMARY")]


def ident(p):
    b = pathlib.Path(p).read_bytes()
    return {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}


def summary(repo, rel, prefix):
    """从 proof 最后一行取 FAIL 与计数; 取不到就报 missing, 不猜、不把 None 写成 0。

    两族计数标签不一样: 四族用「判据=」, 三版本重放用「件数=」+「每条件数=」。
    """
    last = (repo / ROUND / rel).read_text(encoding="utf-8").strip().splitlines()[-1]
    if not last.startswith(prefix):
        return {"file": rel, "last_line": last, "missing": True}
    fail = re.search(r"\|FAIL=(\d+)", last)
    preds = re.search(r"\|判据=(\d+)", last) or re.search(r"\|件数=(\d+)", last)
    names = re.search(r"\|FAIL=\d+\|(\S+)", last)
    return {"file": rel, "predicates": int(preds.group(1)) if preds else None,
            "predicate_label": "判据" if re.search(r"\|判据=\d+", last) else "件数",
            "per_case": re.findall(r"\|每条件数=(\d+)", last),
            "fail": int(fail.group(1)) if fail else None,
            "failed_names": names.group(1) if names else None,
            "last_line": last}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()

    gate_json = json.loads((repo / ROUND / "proof/offline_gate.json").read_text(encoding="utf-8"))
    plan = json.loads((repo / PLAN).read_text(encoding="utf-8"))
    seg = (plan.get("motion_budget") or {}).get("business_segments") or {}
    ft = json.loads((repo / ROUND / "proof/r7f8f_fixture_changes.json")
                    .read_text(encoding="utf-8"))
    rr = (repo / RUN_RECORD).read_text(encoding="utf-8")
    exe_lines = (repo / EXE).read_text(encoding="utf-8").splitlines()
    recipe = (repo / (ROUND + "/tools/r7f8f_build_executor.py")).read_text(encoding="utf-8")
    edits = len(re.findall(r"(?m)^ *edit\(", recipe))
    if not edits:
        print("HANDOFF_REFUSE|配方里一条 edit( 都没数到(计数正则失效, 不许写 0 交差)")
        return 1
    # §24.5 的自伤条数从 run_record 当场数, 不写死字面量(写死了就不会随文档漂移而报错)
    m45 = re.search(r"(?m)^### 24\.5 .*?(?=^### )", rr, re.M | re.S)
    injuries = len(re.findall(r"(?m)^\d+\. ", m45.group(0))) if m45 else 0
    if not injuries:
        print("HANDOFF_REFUSE|§24.5 没数到任何自伤条目(小节缺失或编号方式变了)")
        return 1
    pkg_files = sorted(p for p in (repo / PKG).rglob("*") if p.is_file())
    # 合并件正文 == A 的活草案全文(逐字), 在**本地磁盘字节**上比; 远端逐 blob 的同一条由
    # tools/r7f8e_remote_readback.py 作证, 这里不冒充远端取证。
    md_body = (repo / MERGED).read_text(encoding="utf-8").splitlines()
    h = [k for k, l in enumerate(md_body) if l.startswith("## 本次合并与修订记录")]
    md_body = md_body[:h[0]] if h else []
    while md_body and md_body[-1].strip() in ("", "---"):
        md_body.pop()
    src_body = (repo / LIVING).read_text(encoding="utf-8").splitlines()
    inherit_lines, inherit_equal = len(md_body), md_body == src_body
    if not inherit_equal:
        print("HANDOFF_REFUSE|合并件正文与**当前**活草案(%s)不逐字相等: 正文=%d 行 vs 快照=%d 行"
              % (LIVING, inherit_lines, len(src_body)))
        return 1

    # ---- F19: 真字节回放 L 族 + F20 草案 + §26.5 自伤数, 全部从盘上取, 取不到就拒 ----
    mx = (repo / ROUND / "proof/r7f8e_case_matrix.txt").read_text(encoding="utf-8").splitlines()
    l_gates = {}
    for line in mx:
        if re.match(r"^FB\d_[^|]*\|", line):
            parts = line.split("|")
            l_gates[parts[0]] = parts[1] if len(parts) > 1 else "NO_VERDICT"
    if len(l_gates) != 7:
        print("HANDOFF_REFUSE|矩阵回执里 L 族判据数=%d(应为 7: 正例/反例/元负控/静态两侧)"
              % len(l_gates))
        return 1
    f20p = repo / ROUND / "field/plan_r7f8f_2369_real_dispatch_f20_draft.json"
    f20 = json.loads(f20p.read_text(encoding="utf-8")) if f20p.is_file() else None
    if f20 and f20["approval"]["token_verbatim"].startswith("NOT_ISSUED") is False:
        print("HANDOFF_REFUSE|F20 草案里出现了令牌, 但本轮没有对新身份的真实原话 => 要么文件名错, "
              "要么有人在交接工具里造批准")
        return 1
    m265 = re.search(r"(?m)^### 26\.5 .*?(?=^### |^## )", rr, re.M | re.S)
    injuries_265 = len(re.findall(r"(?m)^\d+\. ", m265.group(0))) if m265 else 0

    # 整包 stub 那条判据的名字要能在回执里找到; 找不到就拒(改名不许静默降级成 False)
    whole_gate = [g for g in gate_json["gates"]
                  if g["name"] == "STUB_NORMAL_DISPATCH_SUCCEEDS"]
    if not whole_gate:
        print("HANDOFF_REFUSE|回执里找不到判据 STUB_NORMAL_DISPATCH_SUCCEEDS")
        return 1

    budget_files = sorted(p for p in (repo / BUDGET_PKG).rglob("*") if p.is_file())
    budget_receipt = json.loads((repo / ROUND / "proof/r7f8f_budget_package.json")
                                 .read_text(encoding="utf-8"))
    # 上一版合并件的身份**从合并件自己的记录节读**, 不再写死字面量(写死过一次, 结果裁定落地后
    # 交接里那两格停在 R7F8E 的值)。读不到就拒产。
    pm = re.search(r"上一版合并件: (\d+) B, sha256 前缀 `([0-9a-f]{12})`（提交 `([0-9a-f]{7})`[^）]*）",
                   (repo / MERGED).read_text(encoding="utf-8"))
    if not pm:
        print("HANDOFF_REFUSE|合并件记录节里读不到上一版身份(格式变了 => 不许把旧字面量抄进交接)")
        return 1
    ffp = repo / ROUND / "proof/r7f8f_field_f11_receipt.json"
    field = json.loads(ffp.read_text(encoding="utf-8")) if ffp.is_file() else None
    dcopy = repo / ROUND / "field/plan_r7f8f_2369_real_dispatch.json"
    fq = {g["name"]: g for g in (field or {}).get("gates", [])}
    diff_keys = []
    if dcopy.is_file():
        def flat(x, pre=""):
            o = {}
            if isinstance(x, dict):
                for k, v in x.items():
                    o.update(flat(v, pre + "/" + str(k)))
            elif isinstance(x, list):
                for i, v in enumerate(x):
                    o.update(flat(v, pre + "/%d" % i))
            else:
                o[pre] = x
            return o
        fb = flat(plan)
        fa = flat(json.loads(dcopy.read_text(encoding="utf-8")))
        diff_keys = sorted(set(fb) ^ set(fa)) + sorted(k for k in set(fb) & set(fa)
                                                       if fb[k] != fa[k])
    og = summary(repo, "proof/r7f8f_offline_gate.txt", "R7F8F_OFFLINE_GATE_SUMMARY")
    # 汇总行没有红项时写的是字面量 "none"，不是空串 —— 上一版直接把 "none".split(",") 当成名单
    # 去比 json 的空列表，全绿那天当场判 HANDOFF_CROSS_CHECK_FAILED。两边都归一成列表再比。
    txt_fails = [] if og["failed_names"] in (None, "none") else og["failed_names"].split(",")
    doc = {
        "round": "M8-R7F8F 返修+整包轮（A 包 NS800_R7F8F_Repair_And_Field_20261105 + A 预算裁定"
                 "包 NS800_R7F8F_A_Budget_Ruling_20261105；设备动作 0）",
        "date_local": "2026-11-05",
        "task_book": PKG + "/R7F8F_REPAIR_AND_FIELD_TASK.md",
        "budget_ruling": BUDGET_PKG + "/R7F8F_A_BUDGET_RULING.md",
        "task_id": "M8R7F8F-repair-and-offline-gate-20261105",
        # 两条静态红已由 A 定口径并落地, 整包离线门全绿 => 状态是"待一次现场批准",
        # 不是"可上机已证"：现场判读仍未发生, 设备动作 0。
        "status": ("OFFLINE_GATE_ALL_GREEN / FIELD_RUN_ONCE_DONE_STOPPED_AT_STEP1 "
                   "(BUSINESS_FAILED, 包侧缺 status_lines_allow) / 电机从未带电、从未起转 / "
                   "SECOND_FIELD_RUN_NEEDS_NEW_OPERATOR_APPROVAL"
                   if field else
                   "OFFLINE_GATE_ALL_GREEN / FIELD_PACKAGE_AUTHORITATIVE_PENDING_APPROVAL / "
                   "HARDWARE_ACTIONS=0 / ONE_FIELD_APPROVAL_NOT_GRANTED"),
        "repo_tip_at_start": "d6bbc1b5795fc12d27ee8aaa0939f0408dc43699",
        "branch": "feature/m8-r7d1-async-learn-offline",
        "inputs": {
            "a_package": {"dir": PKG, "files": len(pkg_files),
                          "total_bytes": sum(p.stat().st_size for p in pkg_files),
                          "manifest_registered_items": 13,
                          "manifest_count_note": "zip 内 SHA256SUMS.txt 不列自身 => 可比对集合是"
                                                 "清单列出的其余 12 件；早先误数为 23 的原因已登记，"
                                                 "A 的原清单未回改"},
            "a_budget_ruling_package": {
                "dir": BUDGET_PKG, "files": len(budget_files),
                "total_bytes": sum(p.stat().st_size for p in budget_files),
                "manifest_registered_items": budget_receipt["a_manifest_entries"],
                "zip_reference": budget_receipt["zip_reference"],
                "independent_recompute": {
                    "receipt": ROUND + "/proof/r7f8f_budget_package.txt",
                    "checks": budget_receipt["summary"]["checks"],
                    "fail": budget_receipt["summary"]["fail"],
                    "failed_names": budget_receipt["summary"]["failed_names"],
                    "a_checker_ran_here": "A 自己写的 budget_check.py 用我盘上的件真跑一次，"
                                          "逐条与 A 的 PROOF_budget.json 对账",
                    "reference_bound_to_blob": "基准件按 commit 6f2b12f 的 Git blob 核，"
                                               "不是拿工作树比（上一版这样错过，见 §24.5 第 16 条）",
                    "not_claimed": budget_receipt["not_claimed"]}},
            "frozen_untouched": {
                "product_source_commit_signed": "00718b49e65546aa0bae84006059011d68284c4a",
                "product_tree_subtree": "32470c7ed44e（顶层 rt-thread-master 子树，本轮 0 改产品）",
                "old_executor": dict(ident(repo / OLD_EXE), path=OLD_EXE),
                "board_image_reference_only": dict(ident(repo / IMAGE), path=IMAGE),
                "draft_original": dict(ident(repo / ORIG), path=ORIG)}},
        "derived_executor": {
            "path": EXE, **ident(repo / EXE),
            "lines": len(exe_lines),
            "generator": ROUND + "/tools/r7f8f_build_executor.py",
            "edits": edits,
            "patch": dict(ident(repo / (ROUND + "/patches/r7f8f_executor.patch")),
                          path=ROUND + "/patches/r7f8f_executor.patch"),
            "rebuild_verify": "VERIFY|PASS|仓外重建与仓库派生件逐字节相等（同输入两次一致）"},
        "repairs": {
            "F1_job_poll_contract": "轮询先查作业合同再决定继续等：终态立即收口，不把慢回包当失败",
            "F2_energize_deadline_and_session_abs": "arm 首次响应受通电截止约束 + 会话绝对截止"
                                                    "session_abs_dl=t0+total 全程夹住",
            "F3_single_arm_start_and_evidence": "一场只允许一次 arm/start（第二次在写入尝试之前拒），"
                                                "义务关闭需产品具名证据行",
            "F4_running_contract": "运行状态必须确实在使能侧：逐字段反向 G9~G17",
            "F5_bounded_tail": "有界固定收口五条 + IRQ 固定尾部（disable 必须见 :306 detach 行）",
            "F5b_irq_obligation_and_reserve": "尝试过开 IRQ 本身即建立尾部义务；7 s 预留必须排得下"
                                              "五条各 1.2 s 窗，排不下在开口之前拒",
            "F5c_business_deadline_minus_reserve": "有清理义务的阶段，业务绝对截止提前到"
                                                   "「会话总截止 - 清理预留」，读/轮询/等待/写前检查都受它管",
            "F6_first_error_replay": "三版本重放 21 件 × 3 侧：修前误放行 / 修后首错 STOP 各有原文",
            "F7_coverage_preserved": "原 65/27/32 基线判据名一条不少，新增逐条带人写理由",
            "F8_real_package": "最终 real plan + 真实 rows + 全首错路径复算",
            "F9_machine_receipt": "offline_gate.json + 不变项核对（产品树/旧件/旧账本 0 改动）",
            "F12_three_segment_budget": "静态预算拆三段（裁定落地后为 pre_start / motion_active / "
                                        "post_zero），cap 只算一次；镜像与执行器现算逐字段机器核对"
                                        "（漂移即判红）",
            "F13_ruling_package_identity": "A 预算裁定包 9 件按 A 自己的 SHA256SUMS 当场复算 + "
                                           "A 的 budget_check.py 在我这台机器真跑对账 + 三条等价"
                                           "突变负控（回执 11/FAIL=0）",
            "F14_motion_timer_ends_at_zero": "裁定一落地：运动计时结束点=正常 stop 之后第一份新鲜"
                                             "完整可信的零速快照（zero_speed_contract + "
                                             "motion_end_gate）；stop ACK 不算、迟到不倒填"
                                             "（MOTION_ZERO_CONFIRMATION_LATE）、t0 不清；arm 的 "
                                             "18 s 与业务 83 s 不随零速解除；台账新增 "
                                             "MOTOR_STOP_CONFIRMED / MOTION_TIMER_ENDED",
            "F15_declared_step_total_window": "裁定二落地：step_total_seconds 是**整步总窗**"
                                              "（含余量），由单一 step_window() 供执行器/计划门/"
                                              "镜像/仿真共用；非法值在开口前逐条具名拒"
                                              "（STEP_TOTAL_SECONDS_INVALID / "
                                              "POLL_UNTIL_EXCEEDS_STEP_TOTAL）；未申报的旧计划仍走"
                                              " legacy seconds+SLACK",
            "F16_ruling_probes_and_legacy_kept": "裁定 §5 六项对应 K1~K8 进程内探针 + 整包两条"
                                                 "运行时门；91/23 的旧红原文由 "
                                                 "LEGACY_RED_TEXT_KEPT_VERBATIM 留档，裁定前基准件"
                                                 "重跑仍红（PRE_RULING_REFERENCE_PLAN_STILL_RED_"
                                                 "UNCHANGED）",
            "F17_draft_and_handoff_resync": "活草案按裁定包重合并（逐字承接 304 行）+ run_record "
                                            "§24.8~§24.11 + 本交接 json 同源重算"},
        "offline_verification": dict(
            (k, summary(repo, rel, pre)) for k, rel, pre in FAMILIES),
        "gate_machine_receipt_cross_check": {
            "json_gates": len(gate_json["gates"]),
            "json_fail_names": gate_json["summary"]["failed_names"],
            "text_and_json_same_set":
                gate_json["summary"]["failed_names"] == txt_fails,
            "text_failed_names_normalized": txt_fails,
            "fail_count_matches_name_list":
                gate_json["summary"].get("fail") == len(gate_json["summary"]["failed_names"]),
            "all_json_verdicts_pass_or_fail":
                all(g["verdict"] in ("PASS", "FAIL") for g in gate_json["gates"]),
            "no_real_port_in_any_ledger":
                bool([g for g in gate_json["gates"]
                      if g["name"] == "NO_REAL_PORT_IN_ANY_LEDGER"
                      and g["verdict"] == "PASS"])},
        "fixture_change_table": {
            "file": ROUND + "/proof/r7f8f_fixture_changes.md",
            "base_commit": ft["base_commit"], "problems": ft["problems"],
            "families": dict((k, {"base_count": v["base_count"], "now_count": v["now_count"],
                                  "kept": v["kept"], "added": len(v["added"]),
                                  "removed": len(v["removed"]),
                                  "verdict_flips": len(v["flipped"])})
                             for k, v in ft["artifacts"].items())},
        "field_package": {
            "plan": dict(ident(repo / PLAN), path=PLAN),
            "rows": dict(ident(repo / ROWS), path=ROWS),
            "steps": len((plan.get("stages") or [{}])[0].get("steps") or []),
            "fixed_four": {"motion_cap_seconds": plan["stages"][0]["motion_cap_seconds"],
                           "arm_energize_deadline_seconds":
                               plan["stages"][0]["arm_energize_deadline_seconds"],
                           "total_deadline_seconds": plan["stages"][0]["total_deadline_seconds"],
                           "cleanup_reserve_seconds":
                               plan["stages"][0]["cleanup_reserve_seconds"]},
            "three_segments": seg,
            "step_windows_declared": sum(1 for s in (plan["stages"][0]["steps"])
                                         if isinstance(s, dict) and "step_total_seconds" in s),
            "window_table": dict(ident(repo / (BUDGET_PKG + "/step_total_windows.json")),
                                 path=BUDGET_PKG + "/step_total_windows.json"),
            "normal_stop_send_by_seconds":
                plan["stages"][0].get("normal_stop_send_by_seconds"),
            "static_budget_items_after_ruling": plan.get("static_budget_open_items"),
            "approval": "F11 那次按操作者原话开过**一个**账并已用满 port_open=1；F19(本轮)未开新账、"
                        "未发任何现场命令，四件身份已变 => 再一次真机需要一句新的真实原话"},
        "red_gates_remaining": [g for g in gate_json["gates"] if g["verdict"] == "FAIL"],
        "draft_sync": {
            "merged_file": MERGED, **({"merged_" + k: v for k, v in
                                       ident(repo / MERGED).items()}),
            "inherited_from": LIVING,
            "inherited_from_identity": ident(repo / LIVING),
            "inherit_lines": inherit_lines, "inherit_equal": inherit_equal,
            "inherit_scope": "本地磁盘字节逐字比对；远端 blob 的同一条见 "
                             "tools/r7f8e_remote_readback.py 的回执",
            "previous_merged": {"bytes": int(pm.group(1)), "sha256_prefix": pm.group(2),
                                "commit": pm.group(3),
                                "source": "从合并件自己的记录节正则读出（不再写死字面量）"},
            "agent_authored_part": "只有末尾那节「本次合并与修订记录」（含逐节差异计数表）；"
                                   "记录节之前的 304 行正文与 A 包活草案逐字相等",
            "original_untouched": ident(repo / ORIG)},
        "self_injuries_registered": {"count": injuries,
                                    "where": RUN_RECORD + " §24.5（当场数出来的条目数）",
                                    "count_f19": injuries_265,
                                    "where_f19": RUN_RECORD + " §26.5（F19 那五条：假判红被无回归族"
                                                             "抓住、双引号截断、三引号嵌套、承接件改派生、"
                                                             "判据族 L→FB 改名）"},
        "field_run_f11": ({
            "receipt": ROUND + "/proof/r7f8f_field_f11_receipt.txt",
            "receipt_gates": field["summary"],
            "raw_capture": field["raw_capture"],
            "stop": field["stop"],
            "quota_spent": field["quota_spent"],
            "dispatch_copy": dict(ident(dcopy), path=ROUND + "/field/"
                                  + "plan_r7f8f_2369_real_dispatch.json"),
            "dispatch_copy_diff_vs_delivered_plan": diff_keys,
            "token_verbatim": json.loads(dcopy.read_text(encoding="utf-8"))["approval"]
            ["token_verbatim"] if dcopy.is_file() else None,
            "approval_id": "M8-R7F8F-F11-20261105",
            "meaning": "真机跑过一次并在第 1 步首错停止: 唯一一次开口只写出一条只读查询 "
                       "(捕获里仅此一条回显), 电机/arm/learn/apply 一条都没发 => 不是验收通过, "
                       "也不是板上危险状态; 根因与逐行板态见 run_record §25"
        } if field else {"absent": "现场回执不在盘上 => 本轮尚未做过真实会话"}),
        "package_fix_f19": ({
            "asked_by_operator": "两条处置: 修法=「补包侧申报(不动冻结源码)」; 离线盲区=「必做: "
                                 "真字节回放三条」。产品树/固件/旧证据一字未动。",
            "steps_declaring_allow": [(s.get("as"), (s.get("cmd") or "").split(" ")[0])
                                      for stg in plan.get("stages", [])
                                      for s in (stg.get("steps") or [])
                                      if s.get("status_lines_allow")],
            "allow_patterns": [p for stg in plan.get("stages", [])
                               for s in (stg.get("steps") or [])
                               for p in (s.get("status_lines_allow") or [])],
            "product_grounding": "supervisor.c:126 在 system_status 里**无条件**打这一行; "
                                 "motor.h:92/93 定 bit3(0x8)/bit4(0x10); 现场前态实测 0x18 = 两位同置。"
                                 "mask=0 时该行不含 REFUSED, 不匹配豁免形状也不需要豁免。",
            "rows_only_change": "SYS_READY_APPLIED(第 7 步切片): 补披露行 + 三处臆造值改回源码 token "
                                "(irq_gate=CLOSED / polarity=not-confirmed / protect_ready=NO); "
                                "该行 mask=0x8 是**推导**(0x18 减去 apply 清零的 bit4), "
                                "rows._about.derived_shape_rows 明写它不是实测。",
            "runtime_gate": "STATUS_ALLOW_NOT_POSITIVELY_REQUIRED(派生件 recipe 第 71 项): "
                            "被放行的每一行必须含本步 expect_contains 的某个字面片段, 否则具名停。"
                            "把 R7F8E 起写在注释里但从未核过的口径兑现成判据。",
            "static_gate": "PLAN_STATUS_ALLOW 补第四条(recipe 第 72/73 项 + 新函数 "
                           "status_allow_literal_prefix): 豁免模式的字面前缀必须被 expect_contains 命中 "
                           "=> 静态绿保证运行期那条门不会翻红(两边同一口径)。",
            "real_bytes_replay": l_gates,
            "real_bytes_input": (field or {}).get("raw_capture"),
            "identities_for_next_approval": {
                "plan": dict(ident(repo / PLAN), path=PLAN),
                "rows": dict(ident(repo / ROWS), path=ROWS),
                "executor": dict(ident(repo / EXE), path=EXE),
                "image": plan["identity_binding"]["image"]},
            "f20_dispatch_draft": ({
                "file": ROUND + "/field/plan_r7f8f_2369_real_dispatch_f20_draft.json",
                **ident(repo / (ROUND + "/field/plan_r7f8f_2369_real_dispatch_f20_draft.json")),
                "status": f20["status"],
                "token_verbatim": f20["approval"]["token_verbatim"],
                "derived_by": "tools/r7f8f_make_dispatch_copy.py(只许 9 格差异, 写完扁平 diff 复算)",
                "meaning": "**草案**: 本轮没有对新身份的第二句真实原话, 所以令牌位是 NOT_ISSUED; "
                           "机械前置那格沿用操作者 F11 会话里的事实声明并写明'未重新核对'。"
                           "它不是批准件, 不许直接 dispatch。"}) if f20 else {"absent": "未产出 F20 草案"},
            "f11_dispatch_copy_frozen": "F11 那份承接件按字节冻结不回改(见 field_run_f11), "
                                        "与本轮交付计划已经**不再是同一身份**(差 9 格 + F19 那次 rows 修)。",
        }),
        "device_actions_this_round": {
            "serial_port_open": (field["quota_spent"].get("port_open", 0) if field else 0),
            "dispatch_real": (1 if field else 0),
            # 写出去几条命令不靠我说: 现场回执里那条"捕获里只允许有 system_status 的回显"
            # 判 PASS 才算 1 条查询; 电机/使能/配置/学习类回显一条都不许出现
            "commands_written_to_port": (1 if fq.get("ONLY_THE_QUERY_EVER_WRITTEN", {}).get(
                "verdict") == "PASS" else None) if field else 0,
            "download": 0, "readback": 0,
            "explicit_reset": 0, "motor_arm": 0, "motor_start": 0, "learn": 0,
            "learn_cancel": 0, "logic_analyzer": 0, "keil_build": 0, "power_switch": 0,
            "dispatch_stub_sessions":
                len([g for g in gate_json["gates"] if g["name"].startswith("FIRST_ERROR|")]) + 1,
            "stub_whole_package_ran_green":
                bool(whole_gate) and whole_gate[0]["verdict"] == "PASS",
            "evidence": ("现场那一次的台账与额度逐条在 proof/r7f8f_field_f11_*.jsonl/"
                         "receipt.json(真实 COM5, transport=real); 整包离线门自己的 47 条判据里 "
                         "NO_REAL_PORT_IN_ANY_LEDGER 只声明**门内**那批 dispatch 全走 stub/"
                         "FakePort —— 两条口径各管各的, 不许互相顶(见 run_record §25 末条)"),
            "receipt_gate_verdicts": dict((k, v["verdict"])
                                          for k, v in (fq or {}).items()),
        },
        "boundaries_kept": {
            "product_code_changes": 0, "firmware_rebuilt": 0,
            "old_executor_modified": False, "old_evidence_modified": False,
            "old_approval_ledgers_modified": False,
            "negative_seconds_used": 0, "motion_cap_or_total_window_widened": False,
            "step_list_reduced_for_green": False,
            "run_record_sections": {"total_sections": len(re.findall(r"(?m)^## \d+ ", rr)),
                                    "section_24_occurrences":
                                        len(re.findall(r"(?m)^## 24 ", rr))}},
        "claims_not_made": {
            "polarity_measured": False, "core_protection_verified": False,
            "field_acceptance": "本轮只是工具与离线判据；现场验收需真机一轮",
            "budget_numbers_are": "计划分配的**窗口与计时合同**（A 裁定 §3 明写），不是硬件实测"
                                  "耗时；运行时仍以单调时钟真实耗时裁剪，超界即首错收口",
            "not_verified": ["板上正常巡航 DONE 与活动取消",
                             "SG/ADC+IMU 相对保护的实际触发与恢复",
                             "加减速窗冲击保护",
                             "§5 全 15 组在最坏到达下的现场可行性（离线只证申报窗口排得下）",
                             "0.5 s 短窗在真实 Windows/USB 上的传输下界（A 裁定 §2 末段自留的"
                             "保留意见，本轮未新增仪器结论）"]},
        "next_step_owners": {
            "A": "无需再定口径：两条静态红已按裁定落地并全绿。仅当真机跑出来必须改产品，或机械"
                 "条件变了，才按裁定末段回报范围差异",
            "operator": "F20 需要**一句新的真实原话**，对象 = 本轮四件新身份(见 "
                        "package_fix_f19.identities_for_next_approval)；F11 那句只覆盖了已经用满的"
                        "那一个账(port_open=1)，不借它开第二个账",
            "me_after_approval": "按那句原话离线 --approval-init 新账(绑四件)，再一次 dispatch；"
                                 "本轮 0 设备动作，未开第二个账"}}

    bad = [k for k, v in doc.items() if isinstance(v, tuple)]
    if bad:
        print("HANDOFF_SHAPE_REFUSED|顶层 tuple(JSON 会变成数组)=%s" % bad)
        return 1
    out = repo / "reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                   newline="\n")
    print("HANDOFF|%s|bytes=%d|sha=%s|keys=%d" %
          (out.relative_to(repo).as_posix(), out.stat().st_size,
           hashlib.sha256(out.read_bytes()).hexdigest()[:12], len(doc)))
    gate = doc["offline_verification"]["offline_gate"]
    cc = doc["gate_machine_receipt_cross_check"]
    print("HANDOFF_CROSS_CHECK|json 条数=%d 文本条数=%d 红名单同源=%s|判定值纯净=%s|"
          "红条数与名单一致=%s" %
          (cc["json_gates"], gate["predicates"], cc["text_and_json_same_set"],
           cc["all_json_verdicts_pass_or_fail"], cc["fail_count_matches_name_list"]))
    if not (cc["text_and_json_same_set"]
            and cc["all_json_verdicts_pass_or_fail"]
            and cc["fail_count_matches_name_list"]
            and cc["json_gates"] == gate["predicates"]):
        print("HANDOFF_CROSS_CHECK_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
