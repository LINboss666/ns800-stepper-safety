#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R4 补正后的定向场景验证(零设备: transport=stub, 假端口, sim 后端, 不下载不回读不复位)。

覆盖复审要求:
  V1 两场的四种触发组合(都触发 / A触发B未 / A未B触发 / 都未)各自跑一遍真实调度
  V2 每场顺序必须是 运动会话 -> 取证 -> 锁存态拒绝 -> 人工恢复, 全部走完才进下一场
  V3 两场均触发时实际用量: 12 次开口 + 5 组必要清理(与 PLAN_QUOTA_NEED 的推导同源)
  V4 事故基线按场隔离: bb_session_sg 与 bb_session_imu 各自记录/各自比较
  V5 机械缺项(load_means=NOT_PROVIDED, 即本包交付态)在**开端口之前**拒绝整条 SG 链
  V6 缺腿 => EVIDENCE_INCOMPLETE, 不得被写成 NO_TRIGGER; 且失败仍先完成必要撤能
  V7 已观察到的触发若被记成到时未触发 => TRIP_REWRITTEN_AS_NO_TRIGGER(具名拒绝)
  V8 混合结果不得互相推翻: 一条链 trip、另一条 no_trigger 不产生 RESPONSE_BRANCH_CONFLICT

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
    provided = copy.deepcopy(plan)
    provided["mechanical_prerequisites"]["load_means"]["status"] = "PROVIDED"
    for stg in provided["stages"]:
        for k, v in (stg.get("prerequisites") or {}).items():
            if isinstance(v, dict) and v.get("status") == "NOT_PROVIDED" and v.get("blocks_motion"):
                v["status"] = "PROVIDED"

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

    # ---------------- V5: 机械缺项在起转前拒绝(本包交付态) ----------------------
    gap = run(m, plan, "healthy", "gap", work / "gap", {SC_A: "trip", SC_B: "trip"})

    def v5():
        s8_ids = ids(gap)
        skipped = gap["prerequisite_skips"]
        used = gap["quota_used"]
        refusals = gap["prerequisite_refusals"]
        s8_dir_exists = (work / "gap" / "S_S8").exists()
        tx_b = tx_cmds(ledger(work / "gap", "S_S8b"))
        ok5 = (skipped == ["S8", "S9", "S10", "S11"] and "S8" not in s8_ids and
               refusals and refusals[0]["gates"] == ["load_means"] and
               not s8_dir_exists and int(used.get("port_open", 99)) == 8 and
               any(t.startswith("motor_start") for t in tx_b))
        return (ok5,
                "跳过=%s port_open 实际用量=%s(申报 %s) S8 目录未建=%s 汇总拒绝=%s B 场仍起转=%s" %
                (skipped, used.get("port_open"), plan["quota"]["port_open"], not s8_dir_exists,
                 json.dumps(refusals, ensure_ascii=False),
                 any(t.startswith("motor_start") for t in tx_b)))

    case("V5", v5)

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
