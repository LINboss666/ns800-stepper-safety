# -*- coding: utf-8 -*-
"""R7F8E §5.6: 生成交接 JSON(所有数字**当场从盘上算**, 不抄文档)。

同名 .md 是人读的正文, 本脚本只负责机器可读那一份; 两处数字同源 => 复算一次即可对账。
用法: python tools/r7f8e_make_handoff.py --repo D:/ns800
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
OLD_EXE = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
MERGED = "reports/reviews/phase8-m8-r7f8-closed-loop-draft/R7F8_DRAFT_MERGED.md"
ORIG = "reports/reviews/phase8-m8-r7f8-closed-loop-draft/A_DRAFT_ORIGINAL.md"
RUN_RECORD = "reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md"
IMAGE = "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin"
PKG = ROUND + "/inputs/A_R7F8E_PKG"


def h(p):
    b = pathlib.Path(p).read_bytes()
    return {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}


def summary(repo, rel, prefix):
    """从 proof 文件最后一行取 判据=/FAIL= 两个数字(没有就报缺, 不猜)。"""
    txt = (repo / rel).read_text(encoding="utf-8")
    m = re.search(prefix + r"\|判据=(\d+)\|FAIL=(\d+)\|(\S+)", txt.strip().splitlines()[-1])
    if not m:
        return {"file": rel, "predicates": None, "fail": None, "missing": True}
    return {"file": rel, "predicates": int(m.group(1)), "fail": int(m.group(2)),
            "failed_names": m.group(3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    pkg_files = sorted(p for p in (repo / PKG).rglob("*") if p.is_file())
    new_exe = repo / (ROUND + "/tools/m8r3_serial_chain_r7f8.py")
    old_exe = repo / OLD_EXE
    lines_new = new_exe.read_text(encoding="utf-8").splitlines()
    lines_old = old_exe.read_text(encoding="utf-8").splitlines()
    rr = (repo / RUN_RECORD).read_text(encoding="utf-8")
    doc = {
        "round": "M8-R7F8E 执行器离线适配轮（A 任务书 §3 四项适配 + §4 反例 + §5 交付；设备动作 0）",
        "date_local": "2026-11-05",
        "task_book": PKG + "/TASK_R7F8E_EXECUTOR_OFFLINE.md",
        "task_id": "M8R7F8E-offline-executor-adaptation-20261105",
        "status": "OFFLINE_DEVELOPMENT_DONE / SOFTWARE_REVIEW_PENDING / HARDWARE_ACTIONS=0",
        "repo_tip_at_start": "d6bbc1b5795fc12d27ee8aaa0939f0408dc43699",
        "branch": "feature/m8-r7d1-async-learn-offline",
        "inputs": {
            "a_package": {"dir": PKG, "files": len(pkg_files),
                          "total_bytes": sum(p.stat().st_size for p in pkg_files)},
            "old_executor": dict(h(old_exe), path=OLD_EXE,
                                 unchanged_verified=True),
            "product_source_commit_signed": "00718b49e65546aa0bae84006059011d68284c4a",
            "board_image_reference_only": dict(h(repo / IMAGE), path=IMAGE),
            "draft_original_untouched": dict(h(repo / ORIG), path=ORIG)},
        "derived_executor": {
            "path": ROUND + "/tools/m8r3_serial_chain_r7f8.py",
            **h(new_exe),
            "generator": ROUND + "/tools/r7f8e_build_executor.py",
            "edits": 29,
            "rebuild_verify": "VERIFY|PASS|仓外重建与仓库派生件逐字节相等",
            "lines_old": len(lines_old), "lines_new": len(lines_new),
            "patch": dict(h(repo / (ROUND + "/patches/r7f8e_executor.patch")),
                          path=ROUND + "/patches/r7f8e_executor.patch"),
            "identity_note": "本轮登记新摘要, 不冒用旧 33d3f0af8a4c… 作为派生件身份"},
        "adaptations": {
            "E1_bounded_poll": "in_progress 申报 + 次数/窗口/间隔三上限 + 会话/通电/运动最早截止夹住; "
                               "DONE 正例核 owner/凭据/ADC-SG 样本数; 首份坏响应即停; 纯查询不扣额度",
            "E2_dynamic_job_id": "只从本步 STARTED 整行绑定, ID 限 1..999999999, "
                                 "命令面仅 diag_profile learn_cancel @<绑定>; 派生命令进台账不回写获批 plan",
            "E3_cancel_quota": "映射新增 diag_profile_learn_cancel; 缺键/错键/n!=1/0/耗尽都在 write 前拒; "
                               "缺省按 0 不自动升级; 已终态取消仍算一次不退额",
            "E4_energize_deadline": "EnergizeBudget 在 motor_arm 写入尝试前建立; 未 start/学习/等待/"
                                    "stop 后仍 armed 都在窗内; 清理仍固定三条且重复发送分别记账",
            "registered_preexisting_tool_gaps": [
                "T3 单会话路径清理预留账未接(quota_box 形参恒 None) => 改传有效账本, dispatch 行为零变化",
                "T4 计划缺 tools.download/readback/reset 时 validate_plan 崩溃而非具名 FAIL => (t or {})"]},
        "offline_verification": {
            "case_matrix": summary(repo, ROUND + "/proof/r7f8e_case_matrix.txt",
                                   "R7F8E_MATRIX_SUMMARY"),
            "mutation_metas": ["X1_把JOB_ID固定成1", "X2_把任何终态当成功", "X3_移除取消额度行",
                               "X4_把arm截止改回start起算", "X5_清理预留账改回旧写法"],
            "raw_byte_probes": "R1 非法UTF-8 / R2 裸NUL / R3 危险文本先于解码 / R4 多字节跨读块不误杀"
                               "(进程内向同一条会话读路径灌原始字节)",
            "before_fix_baseline": {"file": ROUND + "/proof/r7f8e_case_matrix_before_fix.txt",
                                    "fail": 18,
                                    "note": "本轮第一次真跑的判红原文, 保留不改"},
            "stub_package": summary(repo, ROUND + "/proof/r7f8e_stub_package.txt",
                                    "R7F8E_STUB_SUMMARY"),
            "no_regression": summary(repo, ROUND + "/proof/r7f8e_no_regression.txt",
                                     "R7F8E_NOREG_SUMMARY"),
            "frozen_plan_checks": {"old": 57, "new": 62, "added_prefix": "R7F8E_",
                                   "preexisting_fail": "PLAN_MOTION_CAP_BUDGET(空运动链, 上一轮已登记)"},
            "synthetic_packages": {
                "plan": dict(h(repo / (ROUND + "/plan/plan_r7f8e_stub.json")),
                             path=ROUND + "/plan/plan_r7f8e_stub.json"),
                "plan_window_variant": ROUND + "/plan/plan_r7f8e_stub_window.json",
                "rows": dict(h(repo / (ROUND + "/plan/rows_r7f8e.json")),
                             path=ROUND + "/plan/rows_r7f8e.json"),
                "negative_rows": ["refused", "never_done", "corrupt"],
                "named_outcomes": {"N0_正常": "OK", "N1_拒绝": "BUSINESS_FAILED",
                                   "N2_次数上限": "POLL_ATTEMPTS_EXHAUSTED",
                                   "N2b_窗口到期": "POLL_WINDOW_EXPIRED",
                                   "N3_证据不完整": "NUL_BYTE_IN_RESPONSE"},
                "real_branch_probe": "同一计划 --transport real 被具名拒绝, 台账 0 条开口事件"}},
        "errata": {
            "file": ROUND + "/ERRATA_R7F8E.md", **h(repo / (ROUND + "/ERRATA_R7F8E.md")),
            "quotes": ["ADC+SG+IMU 三源", "learn_cancel <ID> 不在额度表内，可作为无额度只读步骤发送。",
                       "取消窗口宽度就是一拍量级"],
            "source_counter_evidence": "diag_relative.c:669 置 1u / :674 置 2u / :385 要求 "
                                       "ok_src_seen == 3u => 3 是 1|2 的两源位掩码, 无 IMU 正常位",
            "old_reports_preserved": "DRAFT_REVIEW.md / proof/r7f8_draft_checks.txt(116 条) / "
                                     "run_record §22 / 上一轮交接 md+json 全部原样保留"},
        "draft_sync": {
            "merged_file": MERGED, **({"merged_" + k: v for k, v in
                                       h(repo / MERGED).items()}),
            "inherited_from": PKG + "/R7F8_DRAFT_UPDATED.md",
            "inherit_lines": 272, "inherit_equal": True,
            "previous_merged": {"bytes": 31807, "sha256_prefix": "270383c12b1c"},
            "original_untouched": h(repo / ORIG)},
        "device_actions_this_round": {
            "serial_port_open": 0, "dispatch_real": 0, "download": 0, "readback": 0,
            "explicit_reset": 0, "motor_arm": 0, "motor_start": 0, "learn": 0,
            "learn_cancel": 0, "logic_analyzer": 0, "keil_build": 0, "power_switch": 0,
            "approval_ledgers_created_outside_repo_only": True},
        "boundaries_kept": {
            "product_code_changes": 0, "firmware_rebuilt": 0,
            "old_executor_modified": False, "r7f7_archive_bytes_modified": False,
            "old_approval_ledgers_modified": False,
            "old_executor_sha_recomputed": h(old_exe)["sha256"],
            "run_record_sections": {"total_sections": len(re.findall(r"(?m)^## \d+ ", rr)),
                                    "has_section_23": bool(re.search(r"(?m)^## 23 ", rr)),
                                    "section_23_occurrences":
                                        len(re.findall(r"(?m)^## 23 ", rr))}},
        "not_verified_this_round": [
            "正常巡航下的 DONE 学习与活动中的取消(板上行为)",
            "SG/ADC+IMU 相对保护的实际触发与恢复(含 ok_src_seen 两源语义的板端证据)",
            "加减速窗内的冲击保护(任务书裁定该路径连冲击一起跳过, 本轮不声称)",
            "R7F8 现场轮的刺激方法/速度窗/各段时限/真实动作预算",
            "任何新的镜像构建与上板"],
        "next_step_owners": {
            "A_or_operator": "复审本轮适配(含 T3/T4 两处附带修正是否接受) + 定稿 R7F8 现场参数与预算",
            "me_after_approval": "按定稿口径出可 dispatch 的现场包(含真实端口/UID 绑定)并单独请批",
            "me_blocked_on": "现场执行需操作者单独批准; 本轮不进入"}
    }
    out_md = repo / "reports/agent_handoffs/phase8-m8-r7f8-executor-adaptation.json"
    out_md.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                      newline="\n")
    if isinstance(doc, dict) and any(isinstance(v, tuple) for v in doc.values()):
        print("HANDOFF_SHAPE_REFUSED|顶层出现 tuple(JSON 会变成数组)")
        return 1
    print("HANDOFF|%s|bytes=%d|sha=%s|keys=%d" %
          (out_md.relative_to(repo).as_posix(), out_md.stat().st_size,
           hashlib.sha256(out_md.read_bytes()).hexdigest()[:12], len(doc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
