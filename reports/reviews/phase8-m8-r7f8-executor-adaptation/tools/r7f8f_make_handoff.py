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


PLAN_DIR0 = ROUND + "/field/plan_r7f8f_2369_real_dir0.json"
ROWS_DIR0 = ROUND + "/field/rows_r7f8f_real_dir0.json"
RATIONALE_DIR0 = ROUND + "/field/DIR0_variant_rationale.md"
DIR0_RECEIPTS = [("case_matrix_dir0", "proof/r7f8e_case_matrix_dir0.txt", "R7F8E_MATRIX_SUMMARY"),
                 ("offline_gate_dir0", "proof/r7f8f_offline_gate_dir0.txt",
                  "R7F8F_OFFLINE_GATE_SUMMARY")]


def _refuse(msg):
    print("HANDOFF_REFUSE|" + msg)
    sys.exit(1)


def _dir0_block(repo):
    """DIR0 变体(操作者 2026-11-06 现场指令)的身份 + 它在**原有六族门**上的结果。
    件从盘上读、血统与交付件摘要对账、回执必须自带 FAIL=0; 任一不符就拒产交接 ——
    这三道跟本轮"派生件要逐字段重核继承事实"是同一条纪律。"""
    out = {}
    for name, rel in (("plan", PLAN_DIR0), ("rows", ROWS_DIR0), ("rationale", RATIONALE_DIR0)):
        p = repo / rel
        if not p.is_file():
            _refuse("DIR0 变体缺件: %s" % rel)
        out[name] = dict(ident(p), path=rel)
    pv = json.loads((repo / PLAN_DIR0).read_text(encoding="utf-8"))
    var = pv.get("package_variant") or {}
    if var.get("dir") != 0:
        _refuse("DIR0 计划里的 package_variant.dir=%r(应为 0)" % var.get("dir"))
    if (var.get("derived_from_plan_sha256") != ident(repo / PLAN)["sha256"]
            or var.get("derived_from_rows_sha256") != ident(repo / ROWS)["sha256"]):
        _refuse("变体自报的血统摘要与盘上 DIR1 交付件不一致 => 它不是从被批准的那份字节派生的")
    fam = {}
    for name, rel, pre in DIR0_RECEIPTS:
        rel = ROUND + "/" + rel
        p = repo / rel
        if not p.is_file():
            _refuse("DIR0 回执缺失: %s" % rel)
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith(pre)]
        if len(lines) != 1:
            _refuse("%s 里汇总行 %d 条(应为 1)" % (rel, len(lines)))
        if "|FAIL=0|" not in lines[0]:
            _refuse("DIR0 变体在 %s 上不绿: %s" % (rel, lines[0][:120]))
        fam[name] = lines[0]

    def cnt(rel, pre, tag="判据"):
        """从回执汇总行取条数; 取不到就拒产(写死字面量会随判据增减而失真, 本轮已错过一次)。"""
        last = [l for l in (repo / (ROUND + "/" + rel)).read_text(encoding="utf-8")
                .splitlines() if l.startswith(pre)]
        if len(last) != 1:
            _refuse("%s 汇总行 %d 条(应为 1), 条数无法当场复算" % (rel, len(last)))
        m = re.search(r"\|%s=(\d+)" % tag, last[0])
        if not m:
            _refuse("%s 汇总行里没有 |%s=N 这一格" % (rel, tag))
        return int(m.group(1))

    vp = re.search(r"PLAN_VALIDATE_SUMMARY\|checks=(\d+)\|fail=(\d+)",
                   (repo / (ROUND + "/proof/r7f8f_offline_gate_dir0.txt"))
                   .read_text(encoding="utf-8"))
    if not vp:
        _refuse("DIR0 离线门回执里找不到 PLAN_VALIDATE_SUMMARY 的 checks/fail 格")
    out.update({
        "package_variant": var,
        "families_on_variant": fam,
        "family_counts_on_variant": {
            "case_matrix": cnt("proof/r7f8e_case_matrix_dir0.txt", "R7F8E_MATRIX_SUMMARY"),
            "offline_gate": cnt("proof/r7f8f_offline_gate_dir0.txt", "R7F8F_OFFLINE_GATE_SUMMARY"),
            "validate_plan_inside_offline_gate": {"checks": int(vp.group(1)),
                                                  "fail": int(vp.group(2))}},
        "families_not_re_read": {
            "stub_package": cnt("proof/r7f8e_stub_package.txt", "R7F8E_STUB_SUMMARY"),
            "no_regression": cnt("proof/r7f8e_no_regression.txt", "R7F8E_NOREG_SUMMARY"),
            "budget_package": json.loads((repo / (ROUND + "/proof/r7f8f_budget_package.json"))
                                         .read_text(encoding="utf-8"))["summary"]["checks"],
            "probe_replay": cnt("proof/r7f8f_probe_replay.txt", "REPLAY_SUMMARY", "件数")},
        "executor_unchanged": dict(ident(repo / EXE), path=EXE),
        "image_unchanged": pv["identity_binding"]["image"],
        "dir_consuming_families": "只有反例矩阵与整包离线门真读被测包(条数见 "
                                  "family_counts_on_variant, 其中离线门内含 validate-plan); "
                                  "stub / 无回归 / A 包复算 / 三版本重放读的是合成夹具、冻结的 "
                                  "R7F7 计划、A 的裁定包与 6f2b12f 的 blob, 与 DIR 无关 "
                                  "=> 换变体不改变它们的判定对象(条数见 families_not_re_read)",
        "history_judgments_kept_frozen": "TG9(F20 四件实体绑定)与 MC10(六份冻结旧计划的披露豁免)"
                                         "继续读原始冻结字节, 不跟着 --package-tag 走",
        "evidence_scope": "本轮结论按 dir=0 单方向申报。依据: diag_relative.c:2023 学习只置一个方向位"
                          "(cand.dir_mask = 1u<<dir), diag_relative.c:334 判定只对方向位在 mask 内的"
                          "样本生效, 产品注释 :2005 自己写明'只采一个方向却打 dir_mask=3 是第二条假"
                          "证据链'。=> 不是双向已验。",
        "safety_axis_check": "arm 七门(motor.c:894-917)里没有方向项, DIR 只在四道门通过后才写"
                             "(motor.c:1470/1496) => 操作者'DIR 非安全相关轴'在**机械安全层成立**, "
                             "在**证据适用范围**上需要按上面那句收窄",
        "device_actions": 0,
        "next_approval_targets": "四件里 plan/rows = DIR0 变体两摘要; executor 与镜像不变。"
                                 "DIR1 交付件与其回执按字节冻结不回改, 变体用新文件名并存。"})
    return out


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
    # F22 这一段的自伤单独数(§29.1): 不与 F21 那 8 条混计, 也不许写死。
    m291 = re.search(r"(?m)^### 29\.1 .*?(?=^### |^## |\Z)", rr, re.M | re.S)
    injuries_291 = len(re.findall(re.compile(r"(?m)^\d+\. "), m291.group(0))) if m291 else 0
    if not injuries_291:
        print("HANDOFF_REFUSE|run_record 里没有 §29.1 自伤清单(F22 的登记缺失, 不许出交接)")
        return 1
    inj_f22_titles = ["把 DIR 这个非安全轴的标注差异升级成现场执行的卡点, 被操作者纠正",
                      "F21 宣布'文件令牌路线可用'时只在 CLI 拒绝形状上证过, 真实分支那条通路没测过",
                      "§29 初稿把 TG10 的输入写成'文件令牌 + 台账缺席'(实为 --approval-token), 按印象写",
                      "交接工具把 quota_used(只列非零)与账本 spent(满键)直接判等 => 当场假红"]
    if len(inj_f22_titles) != injuries_291:
        print("HANDOFF_REFUSE|§29.1 实际 %d 条, 交接里的 F22 标题清单 %d 条 => 两边必须同数"
              % (injuries_291, len(inj_f22_titles)))
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

    # ---- F22 第三次真机整包: 停止步/额度/令牌/捕获一律从归档字节 + 台账读, 对不上就拒产 ----
    # F22 那次的首错形状与前两轮**不同**(不是业务失败, 是内容预期不匹配), 所以"停在哪一步"
    # 只能由台账自己那行 PROTOCOL_OR_CONTENT_FAIL 说; 拿前两轮的同源算法套过来会取不到。
    f22_step = [json.loads(l) for l in (repo / ROUND / "proof/r7f8f_field_f22_ledger.jsonl")
                .read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    f22_disp = [json.loads(l) for l in (repo / ROUND / "proof/r7f8f_field_f22_dispatch_ledger.jsonl")
                .read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    f22_led = json.loads((repo / ROUND / "proof/r7f8f_field_f22_approval_ledger.json")
                         .read_text(encoding="utf-8-sig"))
    f22_poc = [e for e in f22_step if e.get("kind") == "PROTOCOL_OR_CONTENT_FAIL"]
    f22_sess = [e for e in f22_step if e.get("kind") == "SESSION_STOP"]
    if len(f22_poc) != 1 or len(f22_sess) != 1:
        print("HANDOFF_REFUSE|F22 台账里首错行=%d 会话停止行=%d(各应为 1) => 停止步不许凭记忆填"
              % (len(f22_poc), len(f22_sess)))
        return 1
    f22_stop_as, f22_missing = f22_poc[0].get("as"), f22_poc[0].get("missing")
    f22_sess_detail = json.loads(f22_sess[0].get("detail") or "{}")
    if f22_sess_detail.get("missing") != f22_missing:
        print("HANDOFF_REFUSE|F22 首错行的 missing=%r 与会话停止行的 %r 不同源" %
              (f22_missing, f22_sess_detail.get("missing")))
        return 1
    f22_out = (repo / ROUND / "proof/r7f8f_field_f22_dispatch_out.txt").read_text(
        encoding="utf-8", errors="replace").splitlines()
    f22_rec_ln = [l for l in f22_out if l.startswith("DISPATCH|stage=S1|status=STOP")]
    f22_sum_ln = [l for l in f22_out if l.startswith("DISPATCH_SUMMARY|")]
    if len(f22_rec_ln) != 1 or len(f22_sum_ln) != 1:
        print("HANDOFF_REFUSE|F22 dispatch_out 里整包结果行=%d 汇总行=%d(各应为 1)"
              % (len(f22_rec_ln), len(f22_sum_ln)))
        return 1
    f22_rec = json.loads(f22_rec_ln[0][f22_rec_ln[0].index("{"):])

    def f22_field(tag):
        for g in f22_sum_ln[0].split("|"):
            if g.startswith(tag + "="):
                v = g[len(tag) + 1:]
                return json.loads(v) if v.startswith(("{", "[")) else v
        return None

    f22_quota_used = f22_field("quota_used")
    f22_attempted = f22_field("actions_attempted")
    if f22_quota_used is None or f22_attempted is None:
        print("HANDOFF_REFUSE|F22 汇总行里找不到 quota_used / actions_attempted(格式变了不许抄旧字段)")
        return 1
    # 汇总行的 quota_used 只列**非零**项, 账本的 spent 是满键: 归一化后必须逐项相等,
    # 直接比会因键数不同而假红(本轮第一版就这样错过一次)。
    f22_spent_nonzero = {k: v for k, v in (f22_led.get("spent") or {}).items() if v}
    if f22_quota_used != f22_spent_nonzero:
        print("HANDOFF_REFUSE|F22 汇总 quota_used=%r 与账本 spent 的非零项 %r 不等"
              % (f22_quota_used, f22_spent_nonzero))
        return 1
    if f22_attempted:
        print("HANDOFF_REFUSE|F22 汇总里 actions_attempted 非空(%r) => 电机未带电那句话就不许写"
              % f22_attempted)
        return 1
    if f22_rec.get("code") != f22_sess[0].get("code"):
        print("HANDOFF_REFUSE|F22 结果行的 code=%r 与会话停止行的 %r 不同源"
              % (f22_rec.get("code"), f22_sess[0].get("code")))
        return 1
    # 逐字节捕获: 台账按时间顺序的 RX_DONE 与归档件名里的 01..NN 必须一一对上同一身份
    # (顺序 + 字节 + 散列三样同时成立), 不靠我记得"哪一步是哪一步"。
    f22_rxs = [e for e in f22_step if e.get("kind") == "RX_DONE"]
    f22_capt_names = sorted(p.name for p in (repo / ROUND / "proof").glob("r7f8f_field_f22_0*.rx.bin"))
    if len(f22_capt_names) != len(f22_rxs):
        print("HANDOFF_REFUSE|F22 台账 RX_DONE=%d 条, 归档捕获=%d 件 => 同源核对不成立就不出交接"
              % (len(f22_rxs), len(f22_capt_names)))
        return 1
    f22_capt, f22_step_order = {}, []
    for i, (name, ev) in enumerate(zip(f22_capt_names, f22_rxs)):
        got = ident(repo / (ROUND + "/proof/" + name))
        if (got["bytes"], got["sha256"]) != (ev.get("bytes"), ev.get("raw_sha256")):
            print("HANDOFF_REFUSE|F22 归档 %s 与台账第 %d 条 RX_DONE 不是同一身份: 盘上 %d B/%s… "
                  "vs 台账 %s B/%s…" % (name, i + 1, got["bytes"], got["sha256"][:12],
                                        ev.get("bytes"), str(ev.get("raw_sha256"))[:12]))
            return 1
        f22_capt[name] = got
        mm = re.match(r"r7f8f_field_f22_\d+_(\w+)\.rx\.bin$", name)
        if not mm:
            print("HANDOFF_REFUSE|F22 归档件名 %s 提不出步标(命名规则变了)" % name)
            return 1
        f22_step_order.append(mm.group(1))
    if f22_step_order[-1] != f22_stop_as:
        print("HANDOFF_REFUSE|F22 最后一个捕获是 %s, 台账首错步是 %s => 停止步与字节不同源"
              % (f22_step_order[-1], f22_stop_as))
        return 1
    if f22_rec.get("steps") != len(f22_rxs):
        print("HANDOFF_REFUSE|F22 结果行 steps=%r 与捕获件数 %d 不等" % (f22_rec.get("steps"),
                                                                        len(f22_rxs)))
        return 1
    f22_biz = [e for e in f22_step if e.get("kind") == "BUSINESS_OUTCOME"]
    if any(e.get("lines") for e in f22_biz):
        print("HANDOFF_REFUSE|F22 出现带业务失败行的查询步 => 本轮不是'前三步全过'的形状, 结论要重写")
        return 1
    f22_done = [e for e in f22_disp if e.get("kind") == "DISPATCH_DONE"]
    if len(f22_done) != 1:
        print("HANDOFF_REFUSE|F22 dispatch 台账里 DISPATCH_DONE=%d 条(应为 1)" % len(f22_done))
        return 1
    # 令牌三道摘要(F21 的门)在 F22 真分支上重新复算一遍: 原话文件裁尾 / 账本绑定 / 承接件申报
    f22_verbatim_p = repo / ROUND / "proof/r7f8f_field_f22_operator_verbatim.txt"
    f22_raw = f22_verbatim_p.read_bytes()
    f22_canon = f22_raw.rstrip(b"\r\n")
    f22_text = f22_canon.decode("utf-8")          # 严格 UTF-8: 解不开直接抛, 不猜内容
    f22_tok = f22_led.get("approval_token") or {}
    f22_copy_p = repo / ROUND / "field/plan_r7f8f_2369_real_dispatch_f22.json"
    f22_copy = json.loads(f22_copy_p.read_text(encoding="utf-8"))
    f22_ap = f22_copy.get("approval") or {}
    if not f22_tok:
        print("HANDOFF_REFUSE|F22 账本里没有 approval_token 格 => F21 的绑定门必须留下证据")
        return 1
    if not (f22_tok.get("sha256") == hashlib.sha256(f22_canon).hexdigest()
            == f22_ap.get("token_sha256")):
        print("HANDOFF_REFUSE|F22 令牌摘要不同源: 账本=%s… 原话裁尾=%s… 承接件=%s…"
              % (str(f22_tok.get("sha256"))[:12], hashlib.sha256(f22_canon).hexdigest()[:12],
                 str(f22_ap.get("token_sha256"))[:12]))
        return 1
    if f22_ap.get("token_file_raw_sha256") != hashlib.sha256(f22_raw).hexdigest():
        print("HANDOFF_REFUSE|F22 承接件申报的原话**原始字节**摘要与盘上不等")
        return 1
    if f22_ap.get("token_verbatim") != f22_text:
        print("HANDOFF_REFUSE|F22 承接件里的令牌文本与原话文件裁尾不等(逐字要求)")
        return 1
    f22_mech = (f22_copy.get("mechanical_prerequisites") or {}).get("bench_continuity") or {}
    if f22_text != f22_mech.get("declared_verbatim"):
        print("HANDOFF_REFUSE|F22 机械前置的逐字声明与令牌不是同一句 => 前置不许借批准句")
        return 1
    # 批准件的四件实体绑定: 账本里的三个摘要都要能在盘上/计划里复算出来
    f22_ids = f22_led.get("identities") or {}
    if f22_ids.get("executor_sha256") != hashlib.sha256((repo / EXE).read_bytes()).hexdigest():
        print("HANDOFF_REFUSE|F22 账本绑的执行器摘要与盘上派生件不等 => 跑的不是交付那件")
        return 1
    if f22_ids.get("plan_sha256") != hashlib.sha256(f22_copy_p.read_bytes()).hexdigest():
        print("HANDOFF_REFUSE|F22 账本绑的 plan 摘要与在盘承接件不等")
        return 1
    # 镜像: 承接件申报的 path+sha 与账本绑的值与盘上字节三方对账(本轮 0 下载/0 回读, 所以
    # 这条只证"批准绑在哪枚镜像身份上", 不证板上的字节 = 那枚 —— 后者由 R7F7-F1 的回读承担)。
    f22_img_decl = (f22_copy.get("identity_binding") or {}).get("image") or {}
    f22_img_p = repo / f22_img_decl.get("path", "")
    if not (f22_ids.get("image_sha256") == f22_img_decl.get("sha256")
            and f22_img_p.is_file()
            and hashlib.sha256(f22_img_p.read_bytes()).hexdigest() == f22_ids["image_sha256"]):
        print("HANDOFF_REFUSE|F22 镜像摘要三方不同源: 账本=%s… 承接件申报=%s… 盘上(%s)=%s…"
              % (str(f22_ids.get("image_sha256"))[:12], str(f22_img_decl.get("sha256"))[:12],
                 f22_img_decl.get("path") or "-",
                 hashlib.sha256(f22_img_p.read_bytes()).hexdigest()[:12]
                 if f22_img_p.is_file() else "件不在盘"))
        return 1
    # 承接件自报"从已交付原件 N/前缀… 派生": 这串数字必须能在盘上的交付件上复算, 否则派生链断。
    m_src = re.search(r"从已交付原件 (\d+)/([0-9a-f]{12})", f22_ap.get("dispatch_copy_note") or "")
    disk_plan = ident(repo / PLAN)
    if not m_src or int(m_src.group(1)) != disk_plan["bytes"] or \
            not disk_plan["sha256"].startswith(m_src.group(2)):
        print("HANDOFF_REFUSE|F22 承接件自报来源 %s 与盘上交付件 %d/%s… 不符 => 批准件不是交付件派生的"
              % (m_src.group(0) if m_src else "读不到", disk_plan["bytes"],
                 disk_plan["sha256"][:12]))
        return 1
    # 交接里**点名引用**的判据必须能在回执里取到 PASS: 名字写错(取到 None)或已翻红都当场拒产。
    # 上一版差点引用了两条根本不存在的判据(MC12/TG10-F22), 那正是"自总结"的形态。
    all_gates = dict(mc_gates); all_gates.update(tg_gates)
    for nm in ("TG10_CLI 真实分支被令牌门拦在 dispatch 之前(不导 pyserial、零台账零发送零开口)",
               "MC11_F20原始字节入档且与历史归档逐字节同一身份(口径=F20本会话首次取得)"):
        if all_gates.get(nm) != "PASS":
            print("HANDOFF_REFUSE|交接点名的判据 %s 在矩阵回执里取到的判定=%r(必须 PASS)"
                  % (nm, all_gates.get(nm)))
            return 1
    m29 = re.search(r"(?m)^## 29 .*?(?=^## \d+ |\Z)", rr, re.M | re.S)
    if not m29:
        print("HANDOFF_REFUSE|run_record 里没有 §29(F22 的现场登记缺失, 不许出交接)")
        return 1
    m301 = re.search(r"(?m)^### 30\.1 .*?(?=^### |^## |\Z)", rr, re.M | re.S)
    gaps_301 = len(re.findall(re.compile(r"(?m)^\d+\. "), m301.group(0))) if m301 else 0
    if not gaps_301:
        print("HANDOFF_REFUSE|run_record 里没有 §30.1 缺口清单(F22 撞出的工具洞没登记)")
        return 1
    gap_titles_f22 = ["真实传输预检查只认 --approval-token(文件令牌走不到真实分支)",
                      "包侧 expect_contains/行表 healthy 串没有'必须能在源码或实拍字节里找到出处'"
                      "的静态门(同形状三轮复发)",
                      "arm 门位 mask 与 TMC 写门 mask 在计划里被当成同一个数, 没有判据把两者分开"]
    if len(gap_titles_f22) != gaps_301:
        print("HANDOFF_REFUSE|§30.1 实际 %d 条, 交接里的缺口标题清单 %d 条 => 两边必须同数"
              % (gaps_301, len(gap_titles_f22)))
        return 1

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
                 "包 NS800_R7F8F_A_Budget_Ruling_20261105；离线部分设备动作 0，真机部分已跑三次："
                 "F11/F20/F22 各一次开口，每次一本新账）",

        "date_local": "2026-11-05 起（离线与 F11/F20）；F22 现场轮与本轮登记 = 2026-11-06",
        "task_book": PKG + "/R7F8F_REPAIR_AND_FIELD_TASK.md",
        "budget_ruling": BUDGET_PKG + "/R7F8F_A_BUDGET_RULING.md",
        "task_id": "M8R7F8F-repair-and-offline-gate-20261105",
        # 两条静态红已由 A 定口径并落地, 整包离线门全绿 => 状态是"待一次现场批准",
        # 不是"可上机已证"：现场判读仍未发生, 设备动作 0。
        "status": ("OFFLINE_GATE_ALL_GREEN / 真机已跑三次(F11 停在第 1 步, F20 停在第 2 步, "
                   "F22 停在第 6 步 = 三轮最远) / 三轮首错全在包侧申报, 产品未给预期外的行 / "
                   "电机从未带电、从未起转 / 本会话第三次批准已用满 / "
                   "下一步=F23 包侧修正(离线)+新的真实原话，NOT_FIELD_READY"
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
            "approval": "F11 / F20 / F22 各按操作者原话开过**一个**账, 三本的 port_open=1 都已用满"
                        "(F22 是本会话刚跑的那次); F21/DIR0 两轮未开新账、未发任何现场命令。"
                        "**本会话第三次现场尝试的批准已消耗完毕**, 再一次真机需要先做 F23 包侧修正"
                        "(见 tool_gaps_f22_and_f23_plan)并拿到针对新身份的一句真实原话, "
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
                                    "count_f22": injuries_291,
                                    "where_f22": RUN_RECORD + " §29.1（F22 那 %d 条：%s）"
                                                 % (injuries_291, "、".join(inj_f22_titles)),
                                    "gaps_f22": {"count": gaps_301,
                                                 "where": RUN_RECORD + " §30.1",
                                                 "titles": gap_titles_f22},
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
        "field_run_f22": {
            "status": "已执行并在第 6 步只读查询停止(三轮里最远的一次; 电机从未带电, 保持通电)",
            "stop_code": f22_sess[0].get("code"),
            "stop_step_as": f22_stop_as,
            "stop_missing": f22_missing,
            "steps_executed": f22_rec.get("steps"),
            "step_order_from_bytes": f22_step_order,
            "passed_steps": f22_step_order[:-1],
            "business_outcome_steps": [{"as": e.get("as"), "business_failed_lines": e.get("lines") or [],
                                        "status_lines_allowed": e.get("status_lines_allowed") or []}
                                       for e in f22_biz],
            "disarm": f22_rec.get("disarm"), "obligation": f22_rec.get("obligation"),
            "cleanup_errors": f22_rec.get("cleanup_errors"),
            "not_run_stages": [x for e in f22_done for x in (e.get("not_run") or [])],
            "quota_used": f22_quota_used, "actions_attempted": f22_attempted,
            "captures": f22_capt,
            "operator_verbatim": ident(repo / f22_verbatim_p),
            "token_chain": {
                "verbatim_file_raw": {"bytes": len(f22_raw),
                                      "sha256": hashlib.sha256(f22_raw).hexdigest()},
                "canonical_after_crlf_strip": {"bytes": len(f22_canon),
                                               "sha256": hashlib.sha256(f22_canon).hexdigest()},
                "ledger_bound": f22_tok,
                "dispatch_copy_declared": {"token_sha256": f22_ap.get("token_sha256"),
                                           "token_file_raw_sha256": f22_ap.get("token_file_raw_sha256"),
                                           "token_source": f22_ap.get("token_source")},
                "three_digests_equal": True,
                "meaning": "F21 建的三道摘要门第一次在真实分支上跑通: 传入令牌 == 原话文件裁尾 == "
                           "账本绑定 == 承接件申报, 四个口都由 Python 内从同一文件取字节 "
                           "(runner 已归档), 不经控制台、不经 shell 变量。"},
            "dispatch_copy": {"file": ROUND + "/field/plan_r7f8f_2369_real_dispatch_f22.json",
                              **ident(f22_copy_p),
                              "mechanical_prerequisite_status": f22_mech.get("status"),
                              "provenance_gate": "承接件自报的来源原件(N/摘要前缀)已当场与盘上交付件 "
                                                 "复算相符; 账本里 plan/executor/image 三个摘要也各自"
                                                 "在盘上/计划里复算相符(取不到就拒产本交接)。"},
            "ledger": {"approval_id": f22_led.get("approval_id"),
                       "identities": f22_ids, "spent": f22_led.get("spent"),
                       "approval_token_field_present": "approval_token" in f22_led},
            "meaning": "前两轮的首错(F11 缺 status_lines_allow / F20 缺 mask 申报)这次都被 F21 的"
                       "门位契约放过去了 => 前 5 步只读查询全过; 停止原因换成**包侧内容预期写错**"
                       "(expect_contains 用了 [TMC] 而产品打 [TMC-CFG])。tmc_config_apply 一条没发, "
                       "DIR1/arm/起转/学习/commit/stop/disarm 全 0, actions_attempted 为空。",
            "root_causes_package_side": [
                "cfg_pre/apply 的 expect_contains=[TMC] —— 该命令族全部打 [TMC-CFG]"
                "(tmc2209.c:3154/3158 show、:2844 CONFIGURED、:2645/2791 拒绝路径); "
                "[TMC] 这个前缀只在 system_status 里由 supervisor.c:128 打。",
                "行表 tmc_config_show/tmc_config_apply 的 healthy 串是我手写的 "
                "('[TMC] config show' / '[TMC] apply CONFIRM'), 源码里不存在 => 假端口不可能暴露, "
                "与 F11/F20 同形状(第三次)。",
                "sys_after_apply 期望 runtime write gate mask=0x8: 把 arm 门位 mask"
                "(motor.c:894-917 七门, supervisor.c:126 打)与 TMC 写门 mask"
                "(tmc2209_get_write_gate_mask() -> tmc_cfg_gate_prod(), supervisor.c:128 打)当成同一"
                "个数。本次实拍写门 0x0, apply 成功也不会把它变成 0x8。"],
            "not_product_defect": "三轮首错全部落在包侧申报/判据上, 产品一次都没给出预期外的行; "
                                  "按停止-报告口径未改产品码, 也未重构建。",
            "quota_carried_to_f23": False,
            "note": "F22 那本账 port_open=1 已用满, 不结转; F11/F20/F22 三本原样不动。"
        },
        "tool_gaps_f22_and_f23_plan": {
            "registered_in": RUN_RECORD + " §30.1",
            "count": gaps_301,
            "titles": gap_titles_f22,
            "workaround_used": "本轮第一次用纯文件令牌跑真实分支被 "
                               "REAL_TRANSPORT_REFUSED:approval_token_missing_or_mismatch 拒在开口前"
                               "(rc=4, 账本 spent 合计 0, 未建执行根 => 确实 0 字节发出, 已复算)。"
                               "随后在 Python 内从文件取严格 UTF-8 规范字节并同时喂四个令牌口, "
                               "让 TOKEN_SOURCE_CONFLICT / verify_token_chain 自己钉住三道摘要。"
                               "**这是绕行, 不是修复**: 修 :5530 需要改执行器字节, 改了就不是"
                               "操作者批准的 4363af36159b… 那件。",
            "f23_scope_not_executed": "F23 = 包侧修正(三处期望 + 出处门 + 两 mask 分离判据 + "
                                      "TG 族补'文件令牌 + 真实预检查')。本轮**未动**交付件字节, "
                                      "六族判据、清单与在库证据全部停在 F21/DIR0 身份。",
            "evidence_judgments_in_this_receipt": {
                "TG10_真实分支拦在_dispatch_之前": tg_gates.get(
                    "TG10_CLI 真实分支被令牌门拦在 dispatch 之前(不导 pyserial、零台账零发送零开口)"),
                "TG10_的覆盖范围(重要)": "TG10 喂的是 --approval-token 且**账本缺席**, 拒绝码是 "
                                        "APPROVAL_LEDGER_ABSENT; F22 撞的是 "
                                        "REAL_TRANSPORT_REFUSED:approval_token_missing_or_mismatch "
                                        "即'文件令牌 + 真实预检查'这条通路。六族里**没有**判据覆盖它 "
                                        "=> 该洞属 F23 要补的 TG 判据, 现在不冒充成已覆盖。",
                "MC11_历史字节入档口径": mc_gates.get(
                    "MC11_F20原始字节入档且与历史归档逐字节同一身份(口径=F20本会话首次取得)"),
                "F22_字节同一身份核对": "本交接 field_run_f22.captures 那段代码承担"
                                       "(归档件 vs 台账 RX_DONE 的顺序+字节+散列三重对账, "
                                       "不符即拒产本交接); 矩阵里给 F22 的具名判据要到 F23 才有。"},
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
        "dir0_variant": _dir0_block(repo),
        "device_actions_this_round": {
            "serial_port_open": ((field or {}).get("quota_spent", {}).get("port_open", 0)
                                 + f20_led.get("spent", {}).get("port_open", 0)
                                 + f22_led.get("spent", {}).get("port_open", 0)),
            "dispatch_real": (1 if field else 0) + 2,
            "new_in_f22_registration": {"port_open": f22_led.get("spent", {}).get("port_open", 0),
                                        "dispatch": 1, "download": 0, "readback": 0,
                                        "explicit_reset": 0, "tmc_config_apply": 0,
                                        "note": "F22 那一次 dispatch 自己消耗的就是这一个开口额度; "
                                                "上面两个累计键含 F11/F20, 别把累计当本轮新增。"},

            "history_note": "上面两个数是 **F11 + F20 + F22 三次**现场轮的累计(各自账本里的 "
                            "port_open 与 dispatch), 三本账都在盘上、原样不回改; 本会话里 F21/DIR0 "
                            "两轮自己不新增设备动作。F20 那本账不结转给 F21, F22 那本不结转给 F23。",
            "f20_commands_written": 2,
            "f22_queries_written": len(f22_rxs),
            "f22_query_steps": f22_step_order,
            "f22_business_actions_attempted": len(f22_attempted),
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
                         "receipt.json(真实 COM5, transport=real); 整包离线门自己的 %d 条判据里 "
                         "NO_REAL_PORT_IN_ANY_LEDGER 只声明**门内**那批 dispatch 全走 stub/"
                         "FakePort —— 两条口径各管各的, 不许互相顶(见 run_record §25 末条)"
                         % og["predicates"]),

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
                                        len(re.findall(r"(?m)^## 24 ", rr)),
                                    "section_29_occurrences":
                                        len(re.findall(r"(?m)^## 29 ", rr)),
                                    "section_30_occurrences":
                                        len(re.findall(r"(?m)^## 30 ", rr))}},
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
            "A": "两轮全绿的离线判据没被第三次真机推翻, 但三轮首错全在包侧申报上。请定两条口径: "
                 "(1) tmc_config_apply 成功之后 system_status 那行 `runtime write gate mask` **应当**"
                 "是什么(源码只给形状: tmc2209_get_write_gate_mask() -> tmc_cfg_gate_prod(); "
                 "F22 实拍 apply 之前 = 0x0, 成功后从未捕获过, 我不替产品猜); "
                 "(2) F23 新增的'包侧期望串必须有出处'静态门, 是否要求 expect_contains 与行表 "
                 "healthy 串逐条命中**产品源码字面量或已归档实拍字节**(我倾向后者, 但这会让我手写"
                 "的期望从此不可用)。另: 若你认为 [TMC]/[TMC-CFG] 前缀属产品该改的形状, 那要走"
                 "停止-报告改产品, 我不在包侧绕。",
            "operator": "**本会话第三次整包批准已用完**(F22 账 M8-R7F8F-F22-20261106, port_open 1/1)。"
                        "板现在 tmc_config state=UNCONFIGURED、drv 未使能、保持通电, 未做任何复位或"
                        "下载。下一步 F23 是**离线包侧修正(0 设备动作)**; 修完的四件身份变了, 所以"
                        "第四次真机还需要**一句新的真实原话**——对象 = F23 的 plan/rows 新摘要 + "
                        "executor + 镜像, 并在同一句里逐字重新声明机械/电气前置(电机在丝杆上、"
                        "滑块位置、供电/接线是否有变)。我不借 F22 那句开新账。",
            "me_next_offline": "F23(离线, 未执行): cfg_pre 期望改 `[TMC-CFG] state=` 并把 F22 实拍 "
                               "1011 B/303eade7bb77 入行表; apply 期望改 `[TMC-CFG] CONFIGURED`"
                               "(tmc2209.c:2844, 标 source_shape/DERIVED, 因为从未实拍); "
                               "sys_after_apply 的 write gate 期望按 A 定的口径改并保留 arm 门位那行"
                               "的锚定; 加'两 mask 不同数'分离判据 + 期望串出处静态门 + TG 族补"
                               "'文件令牌 + 真实预检查'那条通路(修执行器 :5530 会改执行器身份, "
                               "需 A 签第二次软件章或按停止-报告处理)。",
            "me_after_approval": "按那句新原话把承接件生成器指到 F23 的四件身份(--approved-prefixes "
                                 "当场复算), 用文件令牌离线 --approval-init 新账(绑令牌摘要), "
                                 "再一次 dispatch; 本轮(F22 登记)自己不新增设备动作"}}

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
