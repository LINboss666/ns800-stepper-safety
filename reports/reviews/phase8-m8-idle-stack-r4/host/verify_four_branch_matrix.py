#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R4 补正后的定向场景验证(零设备: transport=stub, 假端口, sim 后端, 不下载不回读不复位)。

覆盖复审要求:
  V1 两场的四种触发组合(都触发 / A触发B未 / A未B触发 / 都未)各自跑一遍真实调度
  V2 每场顺序必须是 运动会话 -> 取证 -> 锁存态拒绝 -> 人工恢复, 全部走完才进下一场
  V3 两场均触发时实际用量: 12 次开口 + 5 组必要清理(与 PLAN_QUOTA_NEED 的推导同源)
  V4 事故基线按场隔离: bb_session_sg 与 bb_session_imu 各自记录/各自比较
  V5  交付态(assembly_readiness=NOT_CONFIRMED 且 load_means=NOT_PROVIDED): 每个起转阶段都在开端口
      之前被拒(S4/S8/S8b/S10/S10b), 依赖本次学习的 S5/S6/S7 沿 requires 一并跳过 => 零次起转
  V5b 只缺加载(装配就绪已确认): SG 链跳过, S4 学习与 S8b 激励照常起转(8 开口 / 2 motor_run / 3 组清理)
  V6 缺腿 => EVIDENCE_INCOMPLETE, 不得被写成 NO_TRIGGER; 且失败仍先完成必要撤能
  V7 已观察到的触发若被记成到时未触发 => TRIP_REWRITTEN_AS_NO_TRIGGER(具名拒绝)
  V8 混合结果不得互相推翻: 一条链 trip、另一条 no_trigger 不产生 RESPONSE_BRANCH_CONFLICT
  V9 两场各吃自己通道的夹具 => claims 双 VERIFIED 且 core_protection_verified=True
  V10 复审实测反例: IMU 场喂 SG 行(REL_SG_SEVERE, imu=0) => 具名 OTHER_SOURCE_TRIGGERED,
      但整链仍走完、撤能与取证照做、flow_complete 仍为 True(流程跑完 != 通道验证成功)
  V11 元负控: 规则名对(REL_IMPACT)但 imu=0 => TARGET_RULE_BUT_SOURCE_FIELD_ZERO
  V12 元负控: 摘掉 target_channel 声明后同一份数据不再被拒(证明拒绝来自这个门), 且该突变计划
      被离线 PLAN_TARGET_CHANNEL 拒绝
  V13 离线门: 发明产品枚举外的规则名 / 摘掉某个起转阶段的门 => 都必须具名 FAIL
  V14 复审补测的汇总标签缺陷: 各场声明全 VERIFIED 但取证会话失败 => 整包 STOP 时
      core_protection_verified 必须是 False(判据含 ok 一项, 摘掉它本例翻红)

用法: python verify_four_branch_matrix.py [--out 输出目录] ; 结果写 host/four_branch_matrix.txt
"""
import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent
ROOT = HERE.parents[3]
FIELD = ROUND / "field"
PLAN_PATH = FIELD / "plan_core_r4.json"
CHAIN = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
KERNEL_ROOT = "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
APPS = ROOT / KERNEL_ROOT / "applications"
SC_A, SC_B = "SG_ADC_controlled_load", "IMU_non_destructive_excitation"
OUT = HERE / "four_branch_matrix.txt"

RESULTS = []


def load(name="chain_under_verify"):
    spec = importlib.util.spec_from_file_location(name, str(CHAIN))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def case(cid, fn):
    try:
        detail = fn()
        ok, msg = (True, detail) if not isinstance(detail, tuple) else detail
    except Exception as e:                                      # noqa: BLE001
        ok, msg = False, "%s: %r" % (type(e).__name__, e)
    RESULTS.append((cid, bool(ok), str(msg)))
    print("CASE|%s|%s|%s" % (cid, "PASS" if ok else "FAIL", str(msg)[:300]))
    return bool(ok)


def run(m, plan, profile, tag, outdir, branches):
    p = copy.deepcopy(plan)
    p["stub_branches"] = dict(branches)
    res = m.dispatch_plan(p, "stub", "sim", outdir, m.SimClock(), profile, "NO_DEVICE",
                          str(ROOT), None, None, "", plan_dir=str(FIELD))
    res["_tag"] = tag
    return res


def ids(res, prefix=None):
    got = [s["id"] for s in res["stages"]]
    return [x for x in got if prefix is None or x.startswith(prefix)]


def ledger(path_dir, session):
    p = Path(path_dir) / session / "ledger.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def tx_cmds(evs):
    """台账里的发送事件是 kind=TX, 命令文本在 bytes 里(不是 cmd 字段); 清理是 kind=CLEANUP_READ + cmd。"""
    out = []
    for e in evs:
        if e.get("kind") == "TX":
            b = e.get("bytes")
            out.append((b if isinstance(b, str) else (b or "").decode("utf-8", "replace")).strip())
    return out


def cleanup_cmds(evs):
    return [e.get("cmd") for e in evs if e.get("kind") == "CLEANUP_READ"]


def chain_seq(res, chain):
    a = {SC_A: ("S8", "S9", "S10", "S11"), SC_B: ("S8b", "S9b", "S10b", "S11b")}[chain]
    return [x for x in ids(res) if x in a]


def main():
    argv = sys.argv[1:]
    # 台账门是 append-only(Ledger 对已存在的 ledger.jsonl 直接 LEDGER_PREEXISTING), 所以每次
    # 都用一个全新的工作目录, 不复用上一轮的 run 目录。
    work = (Path(argv[argv.index("--out") + 1]) if "--out" in argv
            else Path(tempfile.mkdtemp(prefix="r4-matrix-")))
    work.mkdir(parents=True, exist_ok=True)
    m = load()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))

    def provided_state(keep=None):
        """把**会挡起转的**机械前置全部改成 PROVIDED(健康正控面: 操作者逐条确认之后的路径)。
        keep 里的键保持原状态, 用来单独跑"只缺加载"这一面。"""
        p = copy.deepcopy(plan)
        for k, v in p["mechanical_prerequisites"].items():
            if v.get("blocks_motion") and k not in (keep or ()):
                v["status"] = "PROVIDED"
                for stg in p["stages"]:
                    for kk, vv in (stg.get("prerequisites") or {}).items():
                        if kk == k and isinstance(vv, dict) and vv.get("blocks_motion"):
                            vv["status"] = "PROVIDED"
        return p

    provided = provided_state()

    # ---------------- V1/V2/V3/V4: 四种组合(机械前置到位的健康面) --------------
    combos = [("both_trip", {SC_A: "trip", SC_B: "trip"}),
              ("a_trip_b_none", {SC_A: "trip", SC_B: "no_trigger"}),
              ("a_none_b_trip", {SC_A: "no_trigger", SC_B: "trip"}),
              ("both_none", {SC_A: "no_trigger", SC_B: "no_trigger"})]
    res = {}
    for tag, branches in combos:
        res[tag] = run(m, provided, "healthy", tag, work / tag, branches)

    def v1():
        bad = [(t, r["status"], r.get("code")) for t, r in res.items() if r["status"] != "OK"]
        return (not bad, "四种组合都跑完且 status=OK: bad=%s" % (bad or "0"))

    def v2():
        want = {("both_trip", SC_A): ["S8", "S9", "S10", "S11"],
                ("both_trip", SC_B): ["S8b", "S9b", "S10b", "S11b"],
                ("a_trip_b_none", SC_A): ["S8", "S9", "S10", "S11"],
                ("a_trip_b_none", SC_B): ["S8b"],
                ("a_none_b_trip", SC_A): ["S8"],
                ("a_none_b_trip", SC_B): ["S8b", "S9b", "S10b", "S11b"],
                ("both_none", SC_A): ["S8"],
                ("both_none", SC_B): ["S8b"]}
        bad = []
        for t, r in res.items():
            for sc in (SC_A, SC_B):
                got = chain_seq(r, sc)
                if got != want[(t, sc)]:
                    bad.append("%s/%s got=%s want=%s" % (t, sc[:6], got, want[(t, sc)]))
            seq = ids(r)
            pos = {s: i for i, s in enumerate(seq)}
            for a, b in (("S8", "S9"), ("S9", "S10"), ("S10", "S11"),
                         ("S8b", "S9b"), ("S9b", "S10b"), ("S10b", "S11b")):
                if a in pos and b in pos and not pos[a] < pos[b]:
                    bad.append("%s 顺序 %s>%s" % (t, a, b))
            if "S11" in pos and "S8b" in pos and not pos["S11"] < pos["S8b"]:
                bad.append("%s A 场恢复晚于 B 场开始" % t)
        return (not bad, "每场 运动->取证->拒绝->恢复, 且 A 场整体先于 B 场; 偏差=%s" % (bad or "0"))

    def v3():
        used = res["both_trip"]["quota_used"]
        need = m.derive_quota_needs(provided)
        left = res["both_trip"]["quota_left"]
        ok = (int(used.get("port_open", 0)) == 12 == int(need["port_open"]) and
              int(used.get("cleanup_motor_stop", 0)) == 5 == int(need["cleanup_motor_stop"]) and
              int(used.get("cleanup_motor_disarm", 0)) == 5 and
              int(used.get("cleanup_motor_status", 0)) == 5 and
              int(used.get("motor_run", 0)) == 3 and
              int(provided["quota"]["port_open"]) >= 12 and
              int(provided["quota"]["cleanup_motor_stop"]) >= 5 and
              all(v >= 0 for v in left.values()))
        audit = [l for l in res["both_trip"]["quota_audit"]["lines"]
                 if l.split("|")[1] in ("port_open", "cleanup_motor_stop")]
        return (ok, "both_trip 用量 port_open=%s cleanup=%s motor_run=%s 申报=%s/%s 余额负=%s audit=%s" %
                (used.get("port_open"), used.get("cleanup_motor_stop"), used.get("motor_run"),
                 provided["quota"]["port_open"], provided["quota"]["cleanup_motor_stop"],
                 res["both_trip"]["quota_audit"]["negative_left"], audit))

    def v4():
        rec = res["both_trip"]["recorded"]
        need = ["bb_session_sg", "bb_session_imu", "bb_written_sg", "bb_written_imu",
                "flash_used_before_sg", "flash_used_before_imu",
                "trip_rule_sg", "trip_sg_sg", "trip_rule_imu", "trip_sg_imu"]
        iso = all(k in rec for k in need) and "bb_session" not in rec and "flash_used_before" not in rec
        mixed = res["a_trip_b_none"]["recorded"]
        no_leak = ("trip_rule_sg" in mixed and "trip_rule_imu" not in mixed and
                   "trip_sg_imu" not in mixed)
        mixed2 = res["a_none_b_trip"]["recorded"]
        no_leak2 = ("trip_rule_imu" in mixed2 and "trip_rule_sg" not in mixed2)
        return (iso and no_leak and no_leak2,
                "按场隔离=%s A触发时只留 A 的事故身份=%s B触发时只留 B 的=%s 键=%s" %
                (iso, no_leak, no_leak2, sorted([k for k in rec if k.startswith(("bb_", "flash_",
                                                                                "trip_", "latch_", "pid", "prev"))])))

    for cid, fn in (("V1", v1), ("V2", v2), ("V3", v3), ("V4", v4)):
        case(cid, fn)

    # ---------------- V5: 交付态(装配就绪未确认 + 缺加载)在起转前拒绝全部起转 -------
    gap = run(m, plan, "healthy", "gap", work / "gap", {SC_A: "trip", SC_B: "trip"})

    def v5():
        skipped = gap["prerequisite_skips"]
        used = gap["quota_used"]
        refusals = {r["scenario"]: r for r in gap["prerequisite_refusals"]}
        spun = []
        for sid in ("S4", "S8", "S8b", "S10", "S10b"):
            spun += [t for t in tx_cmds(ledger(work / "gap", "S_%s" % sid))
                     if t.startswith("motor_arm") or t.startswith("motor_start")]
        want_skip = ["S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S8b", "S9b", "S10b", "S11b"]
        ok5 = (skipped == want_skip and
               set(refusals) >= {SC_A, SC_B, "S4"} and
               refusals["S4"]["gates"] == ["assembly_readiness"] and
               refusals[SC_B]["gates"] == ["assembly_readiness"] and
               refusals[SC_A]["gates"] == ["load_means", "assembly_readiness"] and
               refusals["S5"]["via_requires"] == ["S4"] and
               int(used.get("port_open", 99)) == 1 and int(used.get("motor_run", 0)) == 0 and
               "motor_run" not in used and not spun and
               all(v == "SKIPPED_BEFORE_SPIN_UP(PREREQUISITE)"
                   for v in gap["protection_claims"].values()) and
               gap["core_protection_verified"] is False and gap["flow_complete"] is True)
        return (ok5,
                "交付态 跳过=%s 实际用量 port_open=%s motor_run=%s 发过的起转命令=%s "
                "S5 传播来源=%s 分类=%s flow/core=%s/%s" %
                (skipped, used.get("port_open"), used.get("motor_run", 0), spun,
                 refusals["S5"]["via_requires"],
                 json.dumps(gap["protection_claims"], ensure_ascii=False),
                 gap["flow_complete"], gap["core_protection_verified"]))

    case("V5", v5)

    # -------- V5b: 只缺加载(装配就绪已确认) => SG 链跳, S4 学习与 S8b 仍起转 ----------
    asm_only = provided_state(keep=("load_means",))
    asm_gap = run(m, asm_only, "healthy", "asmgap", work / "asmgap", {SC_A: "trip", SC_B: "trip"})

    def v5b():
        tx_b = tx_cmds(ledger(work / "asmgap", "S_S8b"))
        tx_4 = tx_cmds(ledger(work / "asmgap", "S_S4"))
        used = asm_gap["quota_used"]
        ok = (asm_gap["prerequisite_skips"] == ["S8", "S9", "S10", "S11"] and
              asm_gap["prerequisite_refusals"][0]["gates"] == ["load_means"] and
              any(t.startswith("motor_start") for t in tx_b) and
              any(t.startswith("motor_start") for t in tx_4) and
              int(used.get("port_open", 99)) == 8 and int(used.get("motor_run", 99)) == 2 and
              int(used.get("cleanup_motor_stop", 99)) == 3 and
              asm_gap["protection_claims"][SC_B] == "VERIFIED" and
              asm_gap["protection_claims"][SC_A] == "SKIPPED_BEFORE_SPIN_UP(PREREQUISITE)" and
              asm_gap["core_protection_verified"] is False)
        return (ok, "装配确认/仍缺加载: 跳过=%s port_open=%s motor_run=%s cleanup=%s "
                    "S4 与 S8b 都起了转=%s/%s 分类=%s core=%s" %
                (asm_gap["prerequisite_skips"], used.get("port_open"), used.get("motor_run"),
                 used.get("cleanup_motor_stop"),
                 any(t.startswith("motor_start") for t in tx_4),
                 any(t.startswith("motor_start") for t in tx_b),
                 json.dumps(asm_gap["protection_claims"], ensure_ascii=False),
                 asm_gap["core_protection_verified"]))

    case("V5b", v5b)

    # ---------------- V6: 缺腿 => EVIDENCE_INCOMPLETE, 且先完成必要撤能 ----------
    def leg_variant(drop):
        p = copy.deepcopy(provided)
        p["stub_branches"] = {SC_A: "trip", SC_B: "no_trigger"}
        for stg in p["stages"]:
            if stg["id"] == "S8" and drop == "leg":
                stg["first_cause_legs"]["legs"] = [
                    l for l in stg["first_cause_legs"]["legs"] if l["leg"] != "source_sample"]
                stg["first_cause_legs"]["covers"] = ["trip_row", "profile_identity",
                                                     "incident_session"]
            if stg["id"] == "S8" and drop == "step":
                stg["steps"] = [s for s in stg["steps"] if s.get("as") != "prof_at_arm"]
        return p

    def v6():
        # (a) 申报的腿被摘掉一条 => 该腿不再参与判定, 但**必须仍然完整覆盖四路**才算合法;
        #     所以这里的检查看的是"腿缺失时真会具名拒绝": 用 rows 缺源样本行的方式注入。
        rows = json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig"))
        bad_rows = copy.deepcopy(rows)
        grp = bad_rows["commands"]["__watch__"]["trip"]
        bad_rows["commands"]["__watch__"]["trip"] = [l for l in grp if " sg=" not in l]
        d = work / "v6"
        d.mkdir(parents=True, exist_ok=True)
        (d / "rows.json").write_text(json.dumps(bad_rows, ensure_ascii=False), encoding="utf-8")
        p = copy.deepcopy(provided)
        p["stub_branches"] = {SC_A: "trip", SC_B: "no_trigger"}
        (d / "plan.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        res = m.dispatch_plan(p, "stub", "sim", d / "run", m.SimClock(), "healthy", "NO_DEVICE",
                              str(ROOT), None, None, "", plan_dir=str(d))
        s8 = next(s for s in res["stages"] if s["id"] == "S8")
        evs = ledger(d / "run", "S_S8")
        cleanup = cleanup_cmds(evs)
        sent = tx_cmds(evs)
        ok6 = (s8.get("code") == "EVIDENCE_INCOMPLETE" and
               s8.get("first_cause") == "EVIDENCE_INCOMPLETE" and
               s8.get("status") != "OBSERVATION_NO_TRIGGER" and s8.get("success") is not True and
               s8.get("terminal") == "KNOWN" and s8.get("disarm") == "CONFIRMED_DISABLED" and
               len(cleanup) >= 2 and res["status"] != "OK")
        return (ok6,
                "S8=%s/%s status=%s disarm=%s terminal=%s 清理=%s 总调度=%s 原因=%s" %
                (s8.get("code"), s8.get("first_cause"), s8.get("status"), s8.get("disarm"),
                 s8.get("terminal"), cleanup, res["status"], str(s8.get("detail"))[:90]))

    case("V6", v6)

    # ---------------- V7: 触发不得被降级成 NO_TRIGGER ---------------------------
    def v7():
        st = {"decides_response_branch": True, "id": "S8"}
        got = None
        try:
            m.response_branch({"status": "OBSERVATION_NO_TRIGGER",
                               "results": [{"kind": "watch", "trigger_seen": ["[DIAG] REL TRIP r="]}]}, st)
        except m.Stop as e:
            got = e.code
        honest_none = m.response_branch({"status": "OBSERVATION_NO_TRIGGER", "results": []}, st)
        honest_trip = m.response_branch({"status": "OK",
                                         "results": [{"kind": "watch", "trigger_seen": ["x"]}]}, st)
        return (got == "TRIP_REWRITTEN_AS_NO_TRIGGER" and honest_none == "no_trigger" and
                honest_trip == "trip",
                "降级注入=%s 正常未触发=%s 正常触发=%s" % (got, honest_none, honest_trip))

    case("V7", v7)

    # ---------------- V8: 混合结果不互相推翻 ------------------------------------
    def v8():
        codes = [s.get("code") for r in res.values() for s in r["stages"]]
        per_stage = {t: [(s["id"], s.get("branch")) for s in r["stages"]
                         if s["id"] in ("S8", "S9", "S10", "S11", "S8b", "S9b", "S10b", "S11b")]
                     for t, r in res.items()}
        conflict = any(c == "RESPONSE_BRANCH_CONFLICT" for c in codes)
        return (not conflict and all(r["status"] == "OK" for r in res.values()) and
                all(s.get("first_cause") in (None, "EVIDENCE_INCOMPLETE")
                    for r in res.values() for s in r["stages"]),
                "四种组合无分支冲突=%s 全部 status=OK=%s 各场分支(运动/取证/拒绝/恢复)=%s" %
                (not conflict, all(r["status"] == "OK" for r in res.values()),
                 json.dumps(per_stage, ensure_ascii=False)))

    case("V8", v8)

    # -------- V9..V12: "流程跑完"与"目标通道验证成功"分开判(R5 第 1 项) --------------
    def watch_swap(p, sg_for_imu=True, zero=False):
        """把 IMU 那场的观察窗/锁存夹具换成 SG 那一组(复审实测的就是这份数据), 或换成
        REL_IMPACT 但 imu=0 的元负控夹具。"""
        for stg in p["stages"]:
            for st in stg.get("steps", []):
                if st.get("as") == "trip_window" and stg["id"] == "S8b":
                    st["key"] = "__watch__" if sg_for_imu else (
                        "__watch__@imu_zero" if zero else "__watch__@imu")
                if st.get("as") == "latch" and stg["id"] == "S9b":
                    st["key"] = "diag_status@latch" if sg_for_imu else "diag_status@latch@imu"
        return p

    def v9():
        claims = res["both_trip"]["protection_claims"]
        s8b = next(s for s in res["both_trip"]["stages"] if s["id"] == "S8b")
        s8 = next(s for s in res["both_trip"]["stages"] if s["id"] == "S8")
        ok = (claims == {SC_A: "VERIFIED", SC_B: "VERIFIED"} and
              res["both_trip"]["core_protection_verified"] is True and
              s8b.get("target_claim") == "VERIFIED" and "REL_IMPACT" in str(s8b.get("target_why")) and
              s8.get("target_claim") == "VERIFIED")
        return (ok, "两场各吃自己通道的夹具: claims=%s core=%s S8b=%s(%s) S8=%s(%s)" %
                (json.dumps(claims, ensure_ascii=False), res["both_trip"]["core_protection_verified"],
                 s8b.get("target_claim"), s8b.get("target_why"),
                 s8.get("target_claim"), s8.get("target_why")))

    case("V9", v9)

    def v10():
        p = watch_swap(provided_state(), sg_for_imu=True)
        p["stub_branches"] = {SC_A: "trip", SC_B: "trip"}
        d = work / "v10"
        r = run(m, p, "healthy", "v10", d, {SC_A: "trip", SC_B: "trip"})
        s8b = next(s for s in r["stages"] if s["id"] == "S8b")
        chain_b = chain_seq(r, SC_B)
        evs = ledger(d, "S_S8b")
        cleanup = cleanup_cmds(evs)
        ok = (s8b.get("target_claim") == "OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)" and
              "REL_SG_SEVERE" in str(s8b.get("target_why")) and
              r["protection_claims"][SC_B] == "OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)" and
              r["core_protection_verified"] is False and r["flow_complete"] is True and
              r["status"] == "OK" and chain_b == ["S8b", "S9b", "S10b", "S11b"] and
              len(cleanup) >= 2 and
              s8b.get("first_cause") != "EVIDENCE_INCOMPLETE")
        return (ok, "IMU 场喂 SG 行(REL_SG_SEVERE, imu=0): 分类=%s 原因=%s 整链仍走完=%s "
                    "撤能清理=%s flow/core=%s/%s 调度=%s" %
                (s8b.get("target_claim"), str(s8b.get("target_why"))[:70], chain_b,
                 cleanup, r["flow_complete"], r["core_protection_verified"], r["status"]))

    case("V10", v10)

    def v11():
        p = watch_swap(provided_state(), sg_for_imu=False, zero=True)
        d = work / "v11"
        r = run(m, p, "healthy", "v11", d, {SC_A: "trip", SC_B: "trip"})
        s8b = next(s for s in r["stages"] if s["id"] == "S8b")
        ok = (s8b.get("target_claim") == "TARGET_RULE_BUT_SOURCE_FIELD_ZERO(trip_imu_imu)" and
              r["core_protection_verified"] is False)
        return (ok, "元负控(规则名对但 imu=0): 分类=%s 原因=%s core=%s => "
                    "source_nonzero_keys 这道腿确实在判" %
                (s8b.get("target_claim"), str(s8b.get("target_why"))[:80],
                 r["core_protection_verified"]))

    case("V11", v11)

    def v12():
        # 元负控: 把 S8b 的 target_channel 声明摘掉 => 同一份 SG 数据不再产生"其他来源触发"的
        # 具名拒绝(IMU 场不申报就没有分类), core_protection_verified 重新变 True。若这一步仍然
        # 是 False, 说明 V10 的拒绝不是这个门给的(判据不足)。
        # 这个突变在离线是**非法计划**: validate_plan 的 PLAN_TARGET_CHANNEL 必须拒绝它, 一并核。
        p = watch_swap(provided_state(), sg_for_imu=True)
        for stg in p["stages"]:
            if stg["id"] == "S8b":
                stg.pop("target_channel")
        d = work / "v12"
        r = run(m, p, "healthy", "v12", d, {SC_A: "trip", SC_B: "trip"})
        s8b = next(s for s in r["stages"] if s["id"] == "S8b")
        rows_doc = json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig"))
        vlines = [l for l in m.validate_plan(p, rows_doc, str(APPS))[0]
                  if "PLAN_TARGET_CHANNEL" in l]
        rejected = any("|FAIL|" in l for l in vlines)
        ok = (s8b.get("target_claim") is None and r["core_protection_verified"] is True and
              r["protection_claims"] == {SC_A: "VERIFIED"} and rejected)
        return (ok, "摘掉 target_channel: S8b 分类=%s(不再具名拒绝) core=%s claims=%s "
                    "离线门是否拒绝该计划=%s %s" %
                (s8b.get("target_claim"), r["core_protection_verified"],
                 json.dumps(r["protection_claims"], ensure_ascii=False), rejected,
                 vlines[0][:120] if vlines else "没有 PLAN_TARGET_CHANNEL 行"))

    case("V12", v12)

    def v13():
        # 造一个产品里不存在的规则名 => 离线必须拒绝(计划不能自己发明通道名)
        p = provided_state()
        for stg in p["stages"]:
            if stg["id"] == "S8b":
                stg["target_channel"]["rule_names"] = ["REL_IMU_BANG"]
        rows_doc = json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig"))
        vlines = [l for l in m.validate_plan(p, rows_doc, str(APPS))[0]
                  if "PLAN_TARGET_CHANNEL" in l]
        rejected = any("|FAIL|" in l and "REL_IMU_BANG" in l for l in vlines)
        # 再把某个起转阶段的门摘掉 => PLAN_SPINUP_GATE_COVERAGE 必须拒绝
        p2 = provided_state()
        for stg in p2["stages"]:
            if stg["id"] == "S4":
                stg["prerequisite_gates"] = []
        v2lines = [l for l in m.validate_plan(p2, rows_doc, str(APPS))[0]
                   if "PLAN_SPINUP_GATE_COVERAGE" in l]
        rejected2 = any("|FAIL|" in l and "S4" in l for l in v2lines)
        good = [l for l in m.validate_plan(provided_state(), rows_doc, str(APPS))[0]
                if l.startswith(("PLAN_SPINUP_GATE_COVERAGE", "PLAN_TARGET_CHANNEL"))]
        ok = rejected and rejected2 and all("|PASS|" in l for l in good)
        return (ok, "发明的规则名被拒=%s 摘掉 S4 起转门被拒=%s 交付计划两项都 PASS=%s(%s)" %
                (rejected, rejected2, all("|PASS|" in l for l in good),
                 " || ".join(x[:95] for x in good)))

    case("V13", v13)

    # -------- V14: 取证失败让整包 STOP 时, 汇总标签不得再说"通道已验证" ----------------
    def v14():
        # 复审补测的形状: 两场观察窗都拿到自己通道的触发(claims 全 VERIFIED), 但 S9b 的锁存
        # 回读与本场 TRIP 规则不同代 => 取证会话失败、整包 STOP。此前 core_protection_verified
        # 只看 claims, 于是 STOP 的包仍被贴上"已验证"。摘掉 ok 这一项, 本例必须翻红。
        p = provided_state()
        for stg in p["stages"]:
            for st in stg.get("steps", []):
                if stg["id"] == "S9b" and st.get("as") == "latch":
                    st["key"] = "diag_status@latch"          # SG 的 REL_SG_SEVERE, 与本场 IMPACT 不同规则
        d = work / "v14"
        r = run(m, p, "healthy", "v14", d, {SC_A: "trip", SC_B: "trip"})
        s9b = next(s for s in r["stages"] if s["id"] == "S9b")
        s8b = next(s for s in r["stages"] if s["id"] == "S8b")
        claims_all_verified = bool(r["protection_claims"]) and all(
            v == "VERIFIED" for v in r["protection_claims"].values())
        ok = (r["status"] == "STOP" and r["flow_complete"] is False and
              s9b.get("first_cause") == "EVIDENCE_INCOMPLETE" and
              claims_all_verified and r["core_protection_verified"] is False and
              s8b.get("disarm") == "CONFIRMED_DISABLED" and
              s9b.get("disarm") == "NOT_ARMED")
        return (ok, "S9b 取证失败: 整包=%s flow=%s S9b=%s 而各场声明=%s(全 VERIFIED=%s) "
                    "=> core=%s(必须 False); 撤能在运动会话里已确认=%s, 取证会话自己不带电机义务=%s" %
                (r["status"], r["flow_complete"], s9b.get("first_cause"),
                 json.dumps(r["protection_claims"], ensure_ascii=False), claims_all_verified,
                 r["core_protection_verified"], s8b.get("disarm"), s9b.get("disarm")))

    case("V14", v14)

    passed = sum(1 for r in RESULTS if r[1])
    lines = ["R4_MATRIX|%s" % ("ALL_PASS" if passed == len(RESULTS) else "HAS_FAIL")]
    lines += ["|".join([c, "PASS" if ok else "FAIL", msg]) for c, ok, msg in RESULTS]
    lines.append("SUMMARY|cases=%d|pass=%d|fail=%d|transport=stub_only|port=NO_DEVICE|"
                 "device_actions=0|builds=0" % (len(RESULTS), passed, len(RESULTS) - passed))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(lines[-1])
    print("WRITTEN|" + OUT.as_posix())
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
