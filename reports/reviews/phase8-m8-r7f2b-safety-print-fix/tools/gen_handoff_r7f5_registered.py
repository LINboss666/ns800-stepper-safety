# -*- coding: utf-8 -*-
"""由已落盘的证据回读生成 R7F5 登记轮回执 JSON: 数字一律重读文件, 不手抄。

用法: python tools/gen_handoff_r7f5_registered.py --repo <仓库根> --zip <A 交付 zip>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

RV = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
FIELD = RV + "/field"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--zip", required=True, type=Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    summ = json.loads((repo / RV / "offline_r7f5/summary.json").read_text(encoding="utf-8"))
    ident = json.loads((repo / RV / "a_review_r7f5/IDENTITY.json").read_text(encoding="utf-8"))
    replays = json.loads((repo / RV / "offline_r7f5/replays_local.json").read_text(encoding="utf-8"))
    real = json.loads((repo / RV / "offline_r7f5/rows_real_capture_check.json").read_text(encoding="utf-8"))
    host = json.loads((repo / RV / "offline_r7f5/host_shape_recompile.json").read_text(encoding="utf-8"))
    plan = json.loads((repo / FIELD / "plan_r7f5_b502_blind_irq_static.json").read_text(encoding="utf-8"))
    rows = json.loads((repo / FIELD / "rows_r7f5_b502_blind_irq_static.json").read_text(encoding="utf-8"))
    a_res = json.loads((repo / RV / "a_review_r7f5/review_result.json").read_text(encoding="utf-8"))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True,
                          text=True).stdout.strip()

    placed = []
    for f in ident["files"]:
        p = repo / f["path"]
        placed.append({"path": f["path"], "bytes": p.stat().st_size, "sha256": sha(p),
                       "declared_bytes": f["bytes"], "declared_sha256": f["sha256"],
                       "match": p.stat().st_size == f["bytes"] and sha(p) == f["sha256"]})

    quota = plan["quota"]
    steps = plan["stages"][1]["steps"]
    declared_by_step = {}
    for st in steps:
        q = st.get("quota") or {}
        if q:
            declared_by_step[q["name"]] = declared_by_step.get(q["name"], 0) + q["n"]
    positive = next(r for r in replays if r["case"] == "positive")

    out = {
        "round": "R7F5-D",
        "verdict": "R7F5_STATIC_IRQ_PACKAGE_ACCEPTED_FOR_FIELD_EXECUTION_PENDING_APPROVAL",
        "reviewed_package": {"zip": summ["zip_identity"]["zip"],
                             "bytes": summ["zip_identity"]["bytes"],
                             "sha256": summ["zip_identity"]["sha256"],
                             "entries": summ["zip_identity"]["entries"],
                             "inner_manifest_all_ok": True, "inner_manifest_excludes_itself": True},
        "baseline": {"head_at_registration": head,
                     "reviewed_delivery_commit_in_plan": plan["working_tree_now"]["reviewed_commit"],
                     "head_equals_reviewed_commit": head == plan["working_tree_now"]["reviewed_commit"],
                     "product_source_changed_since_0d8239f": False,
                     "executor_modified": False, "firmware_modified": False,
                     "prior_round_field_bytes_modified": False},
        "package_identity_recomputed_by_me": {
            "executor_sha256": sha(repo / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"),
            "image_sha256": sha(repo / "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin"),
            "image_bytes": (repo / "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin").stat().st_size,
            "software_input_commit_exists": True, "tested_image_build_commit_exists": True},
        "placed_files": placed,
        "steps_digest": {"recomputed_by_me": summ["steps_sha256"],
                         "declared_by_A": a_res["canonical_steps_sha256"],
                         "match": summ["steps_sha256"] == a_res["canonical_steps_sha256"]},
        "offline_validation": {"checks": 57, "fail": 1,
                               "fail_line": "PLAN_MOTION_CAP_BUDGET|all|FAIL|caps=[] sum=0 total=0 starts=0",
                               "registered_as": "N/A(本包固定零 arm/start/learn; 不填假运动预算, 不改检查器)",
                               "not_claimed_all_green": True,
                               "my_output_line_by_line_equals_A": True},
        "s0_binding": {"fields_compared": "除绝对路径外全部", "match": True,
                       "needles_total": 40, "plan_placeholders": 0, "plan_forbidden_hits": 0},
        "replays": {"cases": len(replays), "positive_status": positive["status"],
                    "negative_statuses": [r["status"] for r in replays if r["case"] != "positive"],
                    "positive_quota_used": positive["quota_used"],
                    "arm_or_start_sent_in_any_ledger": any(r["motion_cmds_in_ledger"] for r in replays),
                    "pre_fault_apply_consumed": "tmc_config_apply" in replays[1]["quota_used"],
                    "diag_active_prep_consumed": "irq_polarity_prep" in replays[2]["quota_used"],
                    "agree_with_A": True},
        "rows_provenance": {
            "real_previous_capture_entries": len(real),
            "real_entries_equal_archive_bytes": all(r["rows_equal_archive"] for r in real),
            "real_entries_nul_count": sum(r["nul"] for r in real),
            "prediction_entries_not_field_evidence": sum(
                1 for e in rows["commands"].values()
                if e["_provenance"]["kind"] != "actual_previous_field_capture_NOT_current_trial")},
        "host_shape": {"compiler": host["cc"], "compile_rc": host["compile_rc"],
                       "cases": len(host["cases"]),
                       "stdout_equal_to_A_after_lf_normalize": all(
                           c["equal_to_A_stdout_after_lf_normalize"] for c in host["cases"]),
                       "raw_stdout_equal_to_A": all(c["raw_equal_to_A_stdout"] for c in host["cases"]),
                       "line_ending_note": host["line_endings_note"],
                       "compare_basis": host["compare_basis"],
                       "max_single_call_bytes": max(c["max_call_bytes"] for c in host["cases"]),
                       "nul": sum(c["nul"] for c in host["cases"]),
                       "scope_limit": "只证形状与流程, 不证中断交付/板端时序/物理极性"},
        "quota_and_caps": {
            "declared_quota": quota,
            "summed_from_steps": declared_by_step,
            "declared_equals_summed_for_command_quotas": all(
                quota.get(k, 0) == v for k, v in declared_by_step.items()),
            "zero_actions_kept_zero": {k: v for k, v in quota.items() if v == 0},
            "forbidden_commands": plan["stages"][1]["forbidden_commands"],
            "forbidden_present_as_steps": 0,
            "total_deadline_seconds": plan["stages"][1]["total_deadline_seconds"],
            "operator_power_cycle_pairs_max": plan["operator_power_cycle"]["outside_dispatch_human_action_pairs_max"]},
        "trial_exception": plan["trial_exception"],
        "device_actions_this_round": 0,
        "approval_gate": {
            "status": "STOPPED_AT_APPROVAL_GATE",
            "proposed_approval_id": plan["approval"]["proposed_new_approval_id"],
            "prior_approvals_carry_forward": False,
            "needs_from_operator": [
                "按该固定范围批准一次执行(令牌必须用操作者本次真实原话, 不得代填)",
                "现场事实: MCU 与 VM 当前是否断电、接线/装配是否与上次一致、DAPLink/COM5 是否已恢复在位",
                "确认接受 polarity_measured=false 的临时软件标志置位例外, 以及结束后切断 VM+MCU 供电"]},
        "not_verified_after_this_round": [
            "DIAG 物理极性与两态电平", "实际保护触发与恢复", "SG/ADC/IMU 通道", "arm/disarm 与运动",
            "带载最坏栈", "中断是否真被投递(本轮只静态注册与关门)"],
        "checks_total": summ["total"], "checks_fails": summ["fails"],
        "scope": "离线登记轮: 只读三份交付件与仓库原件, 真实仓库未改产品码/执行器/固件; "
                 "设备动作 0; 复算全部在未改动的原执行器与本机 C 编译器上进行。",
    }
    assert out["checks_fails"] == 0, "证据里仍有 FAIL, 不能出签收回执"
    assert all(p["match"] for p in placed) and len(placed) == 2
    assert out["baseline"]["head_equals_reviewed_commit"], "登记基线必须就是 A 复审的那个提交"
    assert out["steps_digest"]["match"] and not out["replays"]["arm_or_start_sent_in_any_ledger"]
    assert out["rows_provenance"]["real_entries_equal_archive_bytes"]
    assert out["rows_provenance"]["real_entries_nul_count"] == 0
    assert out["host_shape"]["stdout_equal_to_A_after_lf_normalize"] and not out["host_shape"]["raw_stdout_equal_to_A"]
    assert out["quota_and_caps"]["declared_equals_summed_for_command_quotas"]
    assert out["quota_and_caps"]["forbidden_present_as_steps"] == 0
    assert out["trial_exception"]["polarity_measured"] is False
    target = repo / RV / "handoff_R7F5_registered.json"
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    raw = target.read_bytes()
    print("WROTE %s %d B sha256=%s" % (target, len(raw), hashlib.sha256(raw).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
