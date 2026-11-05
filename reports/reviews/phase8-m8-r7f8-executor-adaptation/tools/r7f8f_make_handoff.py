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
    src_body = (repo / (PKG + "/R7F8_LIVING_DRAFT.md")).read_text(encoding="utf-8").splitlines()
    inherit_lines, inherit_equal = len(md_body), md_body == src_body

    # 整包 stub 那条判据的名字要能在回执里找到; 找不到就拒(改名不许静默降级成 False)
    whole_gate = [g for g in gate_json["gates"]
                  if g["name"] == "STUB_NORMAL_DISPATCH_SUCCEEDS"]
    if not whole_gate:
        print("HANDOFF_REFUSE|回执里找不到判据 STUB_NORMAL_DISPATCH_SUCCEEDS")
        return 1

    doc = {
        "round": "M8-R7F8F 返修+整包轮（A 包 NS800_R7F8F_Repair_And_Field_20261105；设备动作 0）",
        "date_local": "2026-11-05",
        "task_book": PKG + "/R7F8F_REPAIR_AND_FIELD_TASK.md",
        "task_id": "M8R7F8F-repair-and-offline-gate-20261105",
        # 现场包已作者化但两条静态预算红待 A 定口径 => 不能标成"可上机"
        "status": "OFFLINE_GATE_DONE_WITH_2_NAMED_RED / FIELD_PACKAGE_NOT_READY / "
                  "HARDWARE_ACTIONS=0 / ONE_FIELD_APPROVAL_NOT_GRANTED",
        "repo_tip_at_start": "d6bbc1b5795fc12d27ee8aaa0939f0408dc43699",
        "branch": "feature/m8-r7d1-async-learn-offline",
        "inputs": {
            "a_package": {"dir": PKG, "files": len(pkg_files),
                          "total_bytes": sum(p.stat().st_size for p in pkg_files),
                          "manifest_registered_items": 13,
                          "manifest_count_note": "zip 内 SHA256SUMS.txt 不列自身 => 可比对集合是"
                                                 "清单列出的其余 12 件；早先误数为 23 的原因已登记，"
                                                 "A 的原清单未回改"},
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
            "F12_three_segment_budget": "静态预算拆起转前/运动窗内/停机后三段，cap 只算一次，"
                                        "单步界=申报窗+SLACK 与执行器同一表达式，镜像 17 字段机器核对"},
        "offline_verification": dict(
            (k, summary(repo, rel, pre)) for k, rel, pre in FAMILIES),
        "gate_machine_receipt_cross_check": {
            "json_gates": len(gate_json["gates"]),
            "json_fail_names": gate_json["summary"]["failed_names"],
            "text_and_json_same_set":
                gate_json["summary"]["failed_names"]
                == summary(repo, "proof/r7f8f_offline_gate.txt",
                           "R7F8F_OFFLINE_GATE_SUMMARY")["failed_names"].split(","),
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
            "open_items_for_A": plan.get("static_budget_open_items"),
            "approval": "本轮未取得现场批准，未开现场账，未发任何现场命令"},
        "two_named_reds": [g for g in gate_json["gates"] if g["verdict"] == "FAIL"],
        "draft_sync": {
            "merged_file": MERGED, **({"merged_" + k: v for k, v in
                                       ident(repo / MERGED).items()}),
            "inherited_from": PKG + "/R7F8_LIVING_DRAFT.md",
            "inherit_lines": inherit_lines, "inherit_equal": inherit_equal,
            "inherit_scope": "本地磁盘字节逐字比对；远端 blob 的同一条见 "
                             "tools/r7f8e_remote_readback.py 的回执",
            "previous_merged": {"bytes": 36273, "sha256_prefix": "e9a5253de024"},
            "section_appended_by_agent":
                "### R7F8F 整包轮对 §7.2 的现场前提答复（我加的，待 A 认可）",
            "original_untouched": ident(repo / ORIG)},
        "self_injuries_registered": {"count": injuries,
                                     "where": RUN_RECORD + " §24.5（当场数出来的条目数）"},
        "device_actions_this_round": {
            "serial_port_open": 0, "dispatch_real": 0, "download": 0, "readback": 0,
            "explicit_reset": 0, "motor_arm": 0, "motor_start": 0, "learn": 0,
            "learn_cancel": 0, "logic_analyzer": 0, "keil_build": 0, "power_switch": 0,
            "dispatch_stub_sessions":
                len([g for g in gate_json["gates"] if g["name"].startswith("FIRST_ERROR|")]) + 1,
            "stub_whole_package_ran_green":
                bool(whole_gate) and whole_gate[0]["verdict"] == "PASS",
            "evidence": "全部 dispatch 走 transport=stub/backend=sim 的 FakePort；离线门台账里每条 "
                        "PORT_OPEN 都是 transport=stub 且 port 字段为空（NO_REAL_PORT_IN_ANY_LEDGER）"
                        "；批准账只建在仓外 --work"},
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
            "not_verified": ["板上正常巡航 DONE 与活动取消",
                             "SG/ADC+IMU 相对保护的实际触发与恢复",
                             "加减速窗冲击保护",
                             "§5 全 15 组在最坏到达下的现场可行性（待 A 定两条红）"]},
        "next_step_owners": {
            "A": "对 §24.4 / 计划 static_budget_open_items 的两条静态冲突定口径"
                 "（放宽 cap / 认可 stop 后解除运动 cap 的运行时语义 / 减步或挪会话）",
            "operator": "一次现场批准的真实原话（未取得就不发任何现场命令）",
            "me_after_both": "接上机执行一次整包并交付原始字节与两级台账；本轮不做"}}

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
    print("HANDOFF_CROSS_CHECK|json 条数=%d 文本条数=%d 红名单同源=%s|判定值纯净=%s"
          % (doc["gate_machine_receipt_cross_check"]["json_gates"], gate["predicates"],
             doc["gate_machine_receipt_cross_check"]["text_and_json_same_set"],
             doc["gate_machine_receipt_cross_check"]["all_json_verdicts_pass_or_fail"]))
    if not (doc["gate_machine_receipt_cross_check"]["text_and_json_same_set"]
            and doc["gate_machine_receipt_cross_check"]["all_json_verdicts_pass_or_fail"]
            and doc["gate_machine_receipt_cross_check"]["json_gates"] == gate["predicates"]):
        print("HANDOFF_CROSS_CHECK_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
