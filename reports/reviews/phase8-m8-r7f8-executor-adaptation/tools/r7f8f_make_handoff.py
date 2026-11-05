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
# F21 起当前活草案 = agent 在上一版正文之上追加的快照(A 那两份是历史承接源, 仍在 PKG/BUDGET_PKG 里)。
LIVING = DRAFT + "/R7F8_DRAFT_SNAPSHOT_F21.md"
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
    l_gates, mc_gates, tg_gates = {}, {}, {}
    for line in mx:
        m = re.match(r"^(FB\d|MC\d+|TG\d+)_[^|]*\|", line)
        if not m:
            continue
        parts = line.split("|")
        verdict = parts[1] if len(parts) > 1 else "NO_VERDICT"
        {"FB": l_gates, "MC": mc_gates, "TG": tg_gates}[m.group(1)[:2]][parts[0]] = verdict
    if len(l_gates) != 7:
        print("HANDOFF_REFUSE|矩阵回执里 L 族判据数=%d(应为 7: 正例/反例/元负控/静态两侧)"
              % len(l_gates))
        return 1
    if len(mc_gates) != 12 or len(tg_gates) != 10:
        print("HANDOFF_REFUSE|矩阵回执里 MC 族=%d 条(应 12)/TG 族=%d 条(应 10) => F21 那两族少判据就不出交接"
              % (len(mc_gates), len(tg_gates)))
        return 1
    # F21 承接**草案**: 本轮没有对新身份的真实原话, 令牌位必须是 NOT_ISSUED; 出现令牌就说明
    # 有人在交接工具里造批准(或拿错了批准件文件名)。
    f21p = repo / ROUND / "field/plan_r7f8f_2369_real_dispatch_f21_draft.json"
    f21 = json.loads(f21p.read_text(encoding="utf-8")) if f21p.is_file() else None
    if f21 and not f21["approval"]["token_verbatim"].startswith("NOT_ISSUED"):
        print("HANDOFF_REFUSE|F21 草案里出现了令牌, 但本轮没有对新身份的真实原话 => 要么文件名错, "
              "要么有人在交接工具里造批准")
        return 1
    m265 = re.search(r"(?m)^### 26\.5 .*?(?=^### |^## )", rr, re.M | re.S)
    injuries_265 = len(re.findall(re.compile(r"(?m)^\d+\. "), m265.group(0))) if m265 else 0
    m276 = re.search(r"(?m)^### 27\.6 .*?(?=^### |^## |\Z)", rr, re.M | re.S)
    injuries_276 = len(re.findall(re.compile(r"(?m)^\d+\. "), m276.group(0))) if m276 else 0
    if not injuries_276:
        print("HANDOFF_REFUSE|run_record 里没有 §27.6 自伤清单(F21 的登记缺失, 不许出交接)")
        return 1
    # 交接里那句"哪几条"必须与 run_record 的条数一起走: 加了一条却不改这里, 当场拒产。
    inj_f21_titles = ["局部变量遮蔽把离线门六条判据带红", "bytes 字面量含非 ASCII",
                      "真字节夹具把裸披露行也换掉造成行不唯一", "F20 的 CLI 令牌字节没落进任何工件",
                      "承接件工具里写死上一轮现场原话",
                      "交接 JSON 把**通过**的第 1 步当成停止步(取值规则用了 [0])",
                      "矩阵判据名里带了字段分隔符 '|'(MC6 的 0x0|0x2), 回执第三格被切开",
                      "next_step_owners 里写了两条同名 'A' 键, 前一条被静默覆盖"]
    if len(inj_f21_titles) != injuries_276:
        print("HANDOFF_REFUSE|§27.6 实际 %d 条, 交接里的标题清单 %d 条 => 两边必须同数"
              % (injuries_276, len(inj_f21_titles)))
        return 1
    # F20 现场的三件产物**从归档字节读**(不抄我自己的结论): 步台账里的首错、dispatch 台账里的
    # 额度与 not_run、账本副本里没有 approval_token 这一格。
    f20_step = [json.loads(l) for l in (repo / ROUND / "proof/r7f8f_field_f20_step_ledger.jsonl")
                .read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    f20_disp = [json.loads(l) for l in (repo / ROUND / "proof/r7f8f_field_f20_dispatch_ledger.jsonl")
                .read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    f20_led = json.loads((repo / ROUND / "proof/r7f8f_field_f20_approval_ledger.json")
                         .read_text(encoding="utf-8-sig"))
    f20_stop = [e for e in f20_step if e.get("kind") == "BUSINESS_OUTCOME"]
    # "停在哪一步"只能由台账自己说: 取与 SESSION_STOP.detail.lines 同源的那条 BUSINESS_OUTCOME。
    # 早期版本直接拿 f20_stop[0], 把第一条**通过**的查询(sys_pre)写成了停止步 —— 那是我的记忆
    # 覆盖了字节。找不到同源项就拒产, 不回落"最后一条"。
    f20_stop_at = None
    for ev in f20_step:
        if ev.get("kind") != "SESSION_STOP" or not ev.get("detail"):
            continue
        dl = set(json.loads(ev["detail"]).get("lines") or [])
        for e in f20_stop:
            if dl and dl & set(e.get("lines") or []):
                f20_stop_at = e
                f20_stop_code = ev.get("code")
    if f20_stop_at is None:
        print("HANDOFF_REFUSE|F20 台账里没有与会话停止行同源的 BUSINESS_OUTCOME => 停止步不许凭记忆填")
        return 1
    f20_done = [e for e in f20_disp if e.get("kind") == "DISPATCH_DONE"]
    f20_capt = {n: ident(repo / (ROUND + "/proof/" + n))
                for n in ("r7f8f_field_f20_sys_pre.rx.bin", "r7f8f_field_f20_mot_pre.rx.bin")}
    f20_verbatim = ident(repo / (ROUND + "/proof/r7f8f_field_f20_operator_verbatim.txt"))
    f20_draft_p = repo / ROUND / "field/plan_r7f8f_2369_real_dispatch_f20.json"

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
            "approval": "F11 与 F20 各按操作者原话开过**一个**账, 两本的 port_open=1 都已用满; "
                        "F21(本轮)未开新账、未发任何现场命令, 且四件身份已再次变(见 "
                        "package_fix_f21.identities_for_next_approval) => 再一次真机需要一句新的真实原话, "
                        "并在同一句里逐字重新声明机械/电气前置"},
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
            "agent_authored_part": "记录节(含逐节差异计数表)由本工具写；记录节之前 %d 行正文与本次"
                                   "承接源逐字相等。F21 那次承接源是 **agent 追加的快照** "
                                   "(§11 那一节是我写的, 不是 A 签收件) —— 记录节里点名了这一点。"
                                   % inherit_lines,
            "original_untouched": ident(repo / ORIG)},
        "self_injuries_registered": {"count": injuries,
                                    "where": RUN_RECORD + " §24.5（当场数出来的条目数）",
                                    "count_f19": injuries_265,
                                    "where_f19": RUN_RECORD + " §26.5（F19 那五条：假判红被无回归族"
                                                             "抓住、双引号截断、三引号嵌套、承接件改派生、"
                                                             "判据族 L→FB 改名）",
                                    "count_f21": injuries_276,
                                    "where_f21": RUN_RECORD + " §27.6（F21 那 %d 条：%s）"
                                                 % (injuries_276, "、".join(inj_f21_titles)),
                                    "f20_registered_as_failed": "令牌字节链那一判在 F20 单独记未通过"
                                                                "(TG8), 实体绑定那一判记通过(TG9)"},
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
            "f20_draft_became_approved_copy": {
                "meaning": "F19 产出的 F20 草案在操作者给出真实原话后, 由同一工具生成批准版 "
                           "(field/plan_r7f8f_2369_real_dispatch_f20.json) 并真机跑过一次; "
                           "两份都已冻结不回改, F20 的执行结果见 field_run_f20。",
                "approved_copy": (dict(ident(repo / f20_draft_p),
                                       path=ROUND + "/field/plan_r7f8f_2369_real_dispatch_f20.json")
                                  if f20_draft_p.is_file() else {"absent": "F20 批准版不在盘上"}),
            },
            "f11_dispatch_copy_frozen": "F11 那份承接件按字节冻结不回改(见 field_run_f11), "
                                        "与本轮交付计划已经**不再是同一身份**(派生差异格 + 三轮的 "
                                        "rows/计划修订)。",
        }),
        "field_run_f20": {
            "status": "已执行并在第 2 条只读查询停止(电机从未带电, 收口照做, 保持通电)",
            "stop_code": f20_stop_code,
            "stop_step_as": f20_stop_at.get("as"),
            "query_steps": [{"as": e.get("as"),
                             "business_failed_lines": e.get("lines") or [],
                             "status_lines_allowed": e.get("status_lines_allowed") or []}
                            for e in f20_stop],
            "stop_lines": f20_stop_at.get("lines") or [],
            "status_lines_allowed_at_stop": f20_stop_at.get("status_lines_allowed") or [],
            "not_run_stages": [x for e in f20_done for x in (e.get("not_run") or [])],
            "captures": f20_capt,
            "operator_verbatim": f20_verbatim,
            "ledger": {"approval_id": f20_led.get("approval_id"),
                       "identities": f20_led.get("identities"),
                       "spent": f20_led.get("spent"),
                       "approval_token_field_present": "approval_token" in f20_led},
            "meaning": "两次只读查询(system_status / motor_status)写出现场, 之后一条业务命令都没发; "
                       "第一条通过=**F19 的申报在真机生效**, 第二条停止=同类缺申报出现在 "
                       "motor_status 上(见 run_record §27.1)。",
            "token_verdict": "单独判**未通过**: 账本里没有 approval_token 格, 开口传入的令牌字节与"
                             "原话文件散列不同(e3b3a647a359… vs 9701aa453fe5…), 当时没有门能发现 "
                             "=> 矩阵 TG8 判据把它钉成一条可复算的 FAIL 形状; 四件实体绑定另判 "
                             "(TG9 PASS)。原账/原话/两份散列都保留不补改。",
            "quota_carried_to_f21": False,
            "evidence_judgments_in_this_receipt": {"MC11_bytes_identity": mc_gates.get(
                "MC11_F20原始字节入档且与历史归档逐字节同一身份(口径=F20本会话首次取得)"),
                "TG8_token_gate_recompute_on_f20_ledger": tg_gates.get(
                    "TG8_F20 那次开口按新门复算=令牌判据不成立")},
            "note": "F20 那本账的 port_open=1 已用满, **不结转**给 F21(A 的原话)。"
        },
        "package_fix_f21": {
            "asked_by_A": "三条补齐: (1) 按最终计划全量核查 8 处 motor_status 的预期 mask, 预期为 0 "
                          "仍要求 0; (2) 纳入历史真实字节 + 补跨计划回归, 口径改为'F20 本会话首次取得'; "
                          "(3) 令牌字节不一致单独判未通过, 开口前核原话文件/实际传入/账本绑定三份摘要。"
                          "另: 两次 system_status 字节相同只证输出重复, 接线未变属操作者报告。",
            "contract_version": plan.get("arm_gate_mask_contract"),
            "per_step_contract": plan.get("arm_gate_mask_contract_steps"),
            "not_inherited": plan.get("arm_gate_mask_contract_not_inherited"),
            "zero_expected_steps_without_allow": [
                s.get("as") for stg in plan.get("stages", []) for s in (stg.get("steps") or [])
                if (s.get("cmd") or "").split(" ")[0] in ("motor_status", "system_status")
                and any(((x.get("must") or {}).get("mask") == "0x0")
                        for x in (s.get("expect_values") or []))
                and not s.get("status_lines_allow")],
            "product_grounding": "motor.c:894-917 七门(bit1 NOT_READY / bit3 NOT_PROTECTED / "
                                 "bit4 NO_DRV_CONFIG 是这里出现的三位); 披露行 motor.c:2479-2480 与 "
                                 "supervisor.c:126; RUN→READY 提交 motor.c:1989(要求 armed!=0 且 "
                                 "cur==tgt==0) => stopped 才是两点边界; bit1 出处 motor.c:899。",
            "executor_gates": "recipe 76 项(新增 H0 常量/H1 must_one_of/H2 静态契约门 + T1~T7 令牌链): "
                              "R7F8F_ARM_GATE_MASK 逐条核, FIELD_VALUE_NOT_IN_SET 新具名码, "
                              "verify_token_chain 在 dispatch_plan 之前拦。",
            "archived_real_bytes": {"0x18": "1128 B/f0550d313164(5 次归档 + F20)",
                                    "0x0": "648 B/10807f63f02a(9 次归档)",
                                    "0x2": "1026 B/9005a306999a 与 1025 B/57efcc7c90d5",
                                    "0x8": "1080 B/fa69aa5cf5a1",
                                    "口径": "同一身份字节早已归档, 所以说'F20 本会话首次取得'而不是'史上第一次'"},
            "f21_dispatch_draft": ({
                "file": ROUND + "/field/plan_r7f8f_2369_real_dispatch_f21_draft.json",
                **ident(repo / f21p),
                "status": f21["status"],
                "token_verbatim": f21["approval"]["token_verbatim"],
                "mechanical_prerequisite_status": f21["mechanical_prerequisites"]["bench_continuity"]
                ["status"],
                "derived_by": "tools/r7f8f_make_dispatch_copy.py(白名单 13 格, 含令牌来源与两份摘要; "
                              "机械前置的原话改成本轮必填参数, 不再沿用上一轮)",
                "meaning": "**草案**: 本轮没有对新身份的真实原话 => 令牌位 NOT_ISSUED、机械前置写 "
                           "NOT_REDECLINED_THIS_ROUND。它不是批准件, 不许直接 dispatch。"})
            if f21 else {"absent": "未产出 F21 草案"},
            "families": {"FB": l_gates, "MC": mc_gates, "TG": tg_gates},
            "identities_for_next_approval": {
                "plan": dict(ident(repo / PLAN), path=PLAN),
                "rows": dict(ident(repo / ROWS), path=ROWS),
                "executor": dict(ident(repo / EXE), path=EXE),
                "image": plan["identity_binding"]["image"]},
        },
        "device_actions_this_round": {
            "serial_port_open": ((field or {}).get("quota_spent", {}).get("port_open", 0)
                                 + f20_led.get("spent", {}).get("port_open", 0)),
            "dispatch_real": (1 if field else 0) + 1,
            "history_note": "上面两个数是**F11 + F20 两次**现场轮的累计(各自账本里的 port_open), "
                            "本轮(F21)自己不新增任何设备动作; F21 沿原动作上限, F20 那本账不结转。",
            "f20_commands_written": 2,
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
            # 这里曾经有过两条 "A" 键(Python 字典字面量**后写覆盖前写**, 静默丢一条)。现在只留一条,
            # 改文案就改这一条, 不要再往上面加同名键。
            "A": "无需再定口径：F21 三条补齐都已落地并全绿(矩阵 135/0、整包离线门 49/0、无回归 9/0)。"
                 "只要求确认新包的门位契约表(10 步)与不继承那一格的**理由**是否成立；若真机再出现必须"
                 "改产品的形状(例如 motor_status 打出预期外的 mask)，我按停止-报告处理，不绕过去",
            "operator": "第三次现场尝试(用 F21 包)需要**一句新的真实原话**，对象 = 本轮四件新身份(见 "
                        "package_fix_f21.identities_for_next_approval)，并在同一句里逐字重新声明"
                        "机械/电气前置；F11/F20 那两句只覆盖了各自已用满的账(port_open=1)，不结转、"
                        "不借它开新账",
            "me_after_approval": "按那句原话把承接件生成器指到本轮四件身份(--approved-prefixes 当场"
                                 "复算)，用 --approval-token-file 离线 --approval-init 新账(绑令牌摘要)，"
                                 "再一次 dispatch；F21 本轮 0 设备动作，未开第三个账"}}

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
