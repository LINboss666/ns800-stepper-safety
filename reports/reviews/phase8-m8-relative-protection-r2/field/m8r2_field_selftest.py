#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R2 现场候选包的离线自检(真实执行, 不是清单)。

三条纪律:
 1) 正控文本一律来自**本轮真实产品 C 输出**(`host/c_chain_output.txt`, 由 M8 主机链在
    定稿字节上跑出来的行), 不手写"成功文案"反证自己。
 2) 每条判定都是 PASS/FAIL + 具名原因; 只有数字或状态位的地方不写 PASS 之外的物理结论。
 3) 三项定向检出(Negative-Injection)在**仓库外的一次性副本**上做: 去掉 5 秒硬截止、
    去掉清理撤能、去掉业务失败层 —— 对应场景必须转为 FAIL, 否则判据不足。
"""
import builtins
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHAIN = HERE / "m8r2_serial_chain.py"
PLAN = json.loads((HERE / "plan.json").read_text(encoding="utf-8-sig"))
C_LINES = (HERE.parent / "host" / "c_chain_output.txt").read_text(encoding="utf-8").splitlines()

RESULT = []
WORK = Path(tempfile.mkdtemp(prefix="m8r2-field-selftest-"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def case(cid, fn):
    """每条 case 单独 try: 意外异常 = FAIL(带异常名), 不当成通过, 也不中断整轮。"""
    try:
        detail = fn()
        ok, msg = (True, detail) if not isinstance(detail, tuple) else detail
    except Exception as e:                                       # noqa: BLE001
        ok, msg = False, "%s:%s" % (type(e).__name__, e)
    RESULT.append((cid, "PASS" if ok else "FAIL", msg))
    print("CASE|%s|%s|%s" % (cid, "PASS" if ok else "FAIL", msg))
    return ok


_DIRN = {}


def out_dir(name):
    """每次调用换一个全新目录: 台账是独占创建的, 同一目录跑第二次会撞 LEDGER_PREEXISTING
    —— 那正是自检里"变异体读到基线台账"的假通过来源。"""
    _DIRN[name] = _DIRN.get(name, 0) + 1
    d = WORK / ("%s-%d" % (name, _DIRN[name]))
    d.mkdir(parents=True, exist_ok=False)
    return d


def c_line(prefix):
    """取本轮真实 C 输出里以该前缀开头的一行(没有就报错, 绝不现编文本)。"""
    hits = [l for l in C_LINES if l.startswith(prefix)]
    if not hits:
        raise AssertionError("本轮 C 输出里没有这条行: " + prefix)
    return hits[0]


def m(cmd="x"):
    return "\r\n" + cmd + "\r\n"


def ok_rows(mod, rows):
    return ("\r\n".join(rows) + "\r\nmsh >").encode("utf-8")


DISARM_ROWS = ["[MOT] state=0 name=IDLE",
               "[MOT] armed=0 drv_en_request=0 health=OK",
               "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK"]


def motion_fx(extra):
    """起转类场景的夹具: 业务响应 + **同一条表里的清理响应**(否则"业务有回、清理静默"是假象)。"""
    fx = {"boot_bytes": [], "responses": {
        "motor_stop": ["[MOT] stop result=OK src=stop rc=0 latched=0"],
        "motor_disarm": ["[MOT] armed=0 drv_en_request=0 health=OK"],
        "motor_status": DISARM_ROWS}}
    for k, v in extra.items():
        if k == "__no_cleanup__":
            fx["responses"] = {}
        else:
            fx["responses"][k] = v
    return fx


# --------------------------------------------------------------------------- 场景
def s01_prompt_recognized(m):
    c = m.SimClock()
    led = m.Ledger(out_dir("s01") / "l.jsonl", c)
    p = m.FakePort([ok_rows(m, ["[SAFETY] ready=YES fault=0"])], led)
    lines, n = m.read_until_prompt(p, 1.0, led, out_dir("s01") / "a.bin", c)
    led.close()
    return (c.t < 0.05 and len(lines) == 1, "elapsed=%.3f rows=%d bytes=%d" % (c.t, len(lines), n))


def s02_missing_prompt_is_not_complete(m):
    c = m.SimClock()
    led = m.Ledger(out_dir("s02") / "l.jsonl", c)
    p = m.FakePort([b"[SAFETY] ready=YES fault=0\r\n"], led)
    lines, n = m.read_until_prompt(p, 1.0, led, out_dir("s02") / "a.bin", c)
    v = m.assert_step({"expect_contains": ["[SAFETY] ready=YES"]}, lines, "s02", led)
    led.close()
    return (v["verdict"] == "STOP" and len(lines) == 0,
            "verdict=%s rows=%d bytes=%d" % (v["verdict"], len(lines), n))


def s03_illegal_bytes_named(m):
    led = m.Ledger(out_dir("s03") / "l.jsonl", m.SimClock())
    try:
        m.split_rows(b"[\x80\x81] broken\r\nmsh >")
        return False, "非法字节没有具名失败"
    except m.Stop as e:
        return (e.code == "ILLEGAL_BYTES_IN_RESPONSE", e.code)
    finally:
        led.close()


def s04_duplicate_field_refused(m):
    led = m.Ledger(out_dir("s04") / "l.jsonl", m.SimClock())
    rows = ["[MOT] armed=0 drv_en_request=0 health=OK",
            "[MOT] armed=1 drv_en_request=1 health=OK"]
    v = m.assert_step({"expect_fields": [[m.ROW_MOT_ARMED, "armed"]]}, rows, "s04", led)
    led.close()
    return (v["verdict"] == "STOP" and v["code"] == "FIELD_NOT_EXACTLY_ONE",
            "verdict=%s code=%s" % (v["verdict"], v.get("code")))


def s05_real_c_learn_failure_is_business_stop(m):
    led = m.Ledger(out_dir("s05") / "l.jsonl", m.SimClock())
    line = c_line("[DIAG] learn FAILED:")
    st = next(s for sg in PLAN["stages"] for s in sg.get("steps", []) if s.get("as") == "learn")
    v = m.assert_step(st, [line], "learn", led)
    led.close()
    return (v["verdict"] == "STOP" and v["code"] == "BUSINESS_FAILED",
            "verdict=%s code=%s text=%s" % (v["verdict"], v.get("code"), line))


def s06_named_refusal_halts_commit(m):
    """拒绝被正确观察 => 但后续 commit/save 一律不发(R1 把它们当成功继续)。"""
    steps = [
        {"cmd": "diag_profile commit", "as": "commit",
         "allowed_outcomes": ["[DIAG] commit REFUSED"]},
        {"cmd": "config_save", "as": "save"},
    ]
    fx = {"boot_bytes": [], "responses": {
        "diag_profile commit": [c_line("[DIAG] commit REFUSED")],
        "config_save": ["[CFG] saved"]}}
    r = m.exec_session("s06", steps, out_dir("s06"), "stub", fx, "NO_DEVICE", 30,
                       m.SimClock(), plan={"quota": {"port_open": 1}})
    sent = [x["bytes"].strip() for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return (r["status"] == "REFUSED" and "config_save" not in sent,
            "status=%s commands=%s" % (r["status"], ",".join(sent)))


def s07_real_c_success_accepted(m):
    led = m.Ledger(out_dir("s07") / "l.jsonl", m.SimClock())
    line = c_line("[DIAG] mode=RELATIVE_ACTIVE")
    st = next(s for sg in PLAN["stages"] for s in sg.get("steps", []) if s.get("as") == "arm_rel")
    v = m.assert_step(st, [line], "arm_rel", led)
    led.close()
    return (v["verdict"] == "OK", "verdict=%s code=%s text=%s" % (v["verdict"], v.get("code"), line))


def s08_watch_stop_halts_dangerous_steps(m):
    steps = [
        {"cmd": "__watch__", "as": "trip", "seconds": 5,
         "expect_contains": ["[DIAG] REL TRIP r="], "trigger_needles": ["[DIAG] REL TRIP r="]},
        {"cmd": "motor_arm", "as": "after_failed_watch"},
        {"cmd": "fault_reset", "as": "reset"},
    ]
    fx = motion_fx({"__watch__": ["[MOT] state=0 name=IDLE"],
                    "motor_arm": ["[MOT] armed=1"], "fault_reset": ["[SAFETY] ready=YES"]})
    r = m.exec_session("s08", steps, out_dir("s08"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1}})
    sent = [x["bytes"].strip() for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return ("motor_arm" not in sent and "fault_reset" not in sent and
            r["status"] == "STOP" and r["code"] == "EXPECTATION_NOT_MET",
            "status=%s code=%s commands=%s" % (r["status"], r["code"], ",".join(sent)))


def s09_no_trigger_is_observation(m):
    steps = [
        {"cmd": "motor_arm", "as": "arm"},
        {"cmd": "motor_target 100", "as": "target"},
        {"cmd": "motor_start", "as": "start", "seconds": 5},
        {"cmd": "__watch__", "as": "wait", "seconds": 5, "no_trigger_is_observation": True,
         "trigger_needles": ["[DIAG] REL TRIP r="], "expect_contains": ["[MOT]"]},
        {"cmd": "fault_reset", "as": "must_not_run"},
    ]
    fx = motion_fx({"motor_arm": ["[MOT] armed=1"], "motor_target 100": ["[MOT] tgt=100"],
                    "motor_start": ["[MOT] state=2 name=CRUISE"],
                    "__watch__": ["[MOT] state=2 name=CRUISE"],
                    "fault_reset": ["[SAFETY] ready=YES"]})
    r = m.exec_session("s09", steps, out_dir("s09"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    tx = [x["bytes"].strip() for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    sent = tx
    return (r["status"] == "OBSERVATION_NO_TRIGGER" and r["disarm"] == "CONFIRMED_DISABLED" and
            "fault_reset" not in tx and "motor_stop" in tx and "motor_disarm" in tx,
            "status=%s disarm=%s cleanup=%s tx=%s" % (
                r["status"], r["disarm"], ",".join(sent[-3:]), ",".join(tx)))


def s10_five_second_hard_cap(m):
    steps = [
        {"cmd": "motor_start", "as": "start", "seconds": 5},
        {"cmd": "__watch__", "as": "wait", "seconds": 10, "expect_contains": ["[MOT]"]},
        {"cmd": "motor_stop", "as": "stop"},
    ]
    fx = motion_fx({"motor_start": ["[MOT] state=2 name=CRUISE"],
                    "__watch__": ["[MOT] state=2 name=CRUISE"]})
    r = m.exec_session("s10", steps, out_dir("s10"), "stub", fx, "NO_DEVICE", 60, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    tx = [x for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    start = next(x["t_s"] for x in tx if x["bytes"] == "motor_start\r\n")
    stop = next((x["t_s"] for x in tx if x["bytes"] == "motor_stop\r\n"), None)
    span = None if stop is None else round(stop - start, 3)
    return (span is not None and span <= 5.0 and r["disarm"] in
            ("CONFIRMED_DISABLED", "UNKNOWN"),
            "start_to_stop=%s declared=5 disarm=%s" % (span, r["disarm"]))


def s11_error_after_start_still_disarms(m):
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 5},
             {"cmd": "not_in_fixture", "as": "later_failure"}]
    fx = motion_fx({"motor_start": ["[MOT] state=2 name=CRUISE"]})
    r = m.exec_session("s11", steps, out_dir("s11"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    tx = [x["bytes"].strip() for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return (r["code"] == "STUB_MISS" and "motor_stop" in tx and "motor_disarm" in tx and
            r["disarm"] == "CONFIRMED_DISABLED" and r["terminal"] == "KNOWN",
            "code=%s tx=%s disarm=%s" % (r["code"], ",".join(tx), r["disarm"]))


def s12_unconfirmable_disable_is_unknown(m):
    """撤能字段读不到 => 终态 UNKNOWN, 且主错与清理错各自保留(M6 语义)。"""
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 5},
             {"cmd": "missing_response", "as": "boom"}]
    fx = motion_fx({"motor_start": ["[MOT] state=2 name=CRUISE"],
                    "__no_cleanup__": True})
    r = m.exec_session("s12", steps, out_dir("s12"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    return (r["disarm"] == "UNKNOWN" and r["terminal"] == "UNKNOWN" and
            bool(r["cleanup_errors"]) and r["code"] == "STUB_MISS",
            "disarm=%s terminal=%s cleanup=%s primary=%s" % (
                r["disarm"], r["terminal"], (r["cleanup_errors"] or [""])[0][:48], r["code"]))


def s13_real_manual_never_uses_fixture(m):
    fx = {"manual": {"cruise_reached": "yes"}}
    calls = {"n": 0}

    def fake_input(_):
        calls["n"] += 1
        return "yes"
    old = builtins.input
    builtins.input = fake_input
    try:
        got = m.read_manual("cruise_reached", fx, True, transport="real")
    finally:
        builtins.input = old
    return (calls["n"] == 1 and got == "yes",
            "operator_calls=%d answer=%s fixture_ignored=%s" % (calls["n"], got, "yes"))


def s14_real_manual_without_operator_stops(m):
    def boom(_):
        raise AssertionError("operator not present in this offline probe")
    old = builtins.input
    builtins.input = boom
    try:
        m.read_manual("cruise_reached", {"manual": {"cruise_reached": "yes"}},
                      True, transport="real")
        return False, "合成 yes 被用掉了"
    except (m.Stop, AssertionError) as e:
        code = getattr(e, "code", "OPERATOR_ASSERTION")
        return code != "MANUAL_GATE_DENIED", "code=%s" % code
    finally:
        builtins.input = old


def s15_denied_manual_runs_no_motion(m):
    steps = [{"cmd": "motor_arm", "as": "arm"},
             {"manual": "cruise_reached", "as": "gate"},
             {"cmd": "motor_start", "as": "start"}]
    fx = {"boot_bytes": [], "manual": {"cruise_reached": "no"},
          "responses": {"motor_arm": ["[MOT] armed=1"], "motor_start": ["[MOT] state=2"]}}
    r = m.exec_session("s15", steps, out_dir("s15"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1, "motor_run": 1}})
    tx = [x["bytes"].strip() for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return ("motor_start" not in tx and r["code"] == "MANUAL_GATE_DENIED",
            "code=%s tx=%s" % (r["code"], ",".join(tx)))


def s16_actions_really_dispatched(m):
    steps = [{"action": "download", "as": "dl", "quota_name": "download",
              "image": PLAN["identity_binding"]["image"], "command": "pyocd flash ..."},
             {"action": "readback", "as": "rb", "quota_name": "flash_readback",
              "image": PLAN["identity_binding"]["image"], "command": "pyocd packmem -c 4 ..."},
             {"action": "reset", "as": "rst", "quota_name": "explicit_reset",
              "command": "pyocd reset"}]
    fx = {"boot_bytes": [], "responses": {}}
    led = m.Ledger(out_dir("s16") / "boot.jsonl", m.SimClock())
    be = m.SimActionBackend(led)
    r = m.exec_session("s16", steps, out_dir("s16"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       backend=be, plan={"quota": {"port_open": 1, "download": 1,
                                                   "flash_readback": 1, "explicit_reset": 1}})
    kinds = [x.get("action") for x in r["results"] if x["kind"] == "action"]
    gates = [x.get("identity_gate", "") for x in r["results"] if x["kind"] == "action"]
    return (kinds == ["download", "readback", "reset"] and be.calls == kinds and
            all("NOT_EXERCISED" in g for g in gates),
            "calls=%s status=%s code=%s detail=%s" % (",".join(kinds), r["status"],
                                                       r.get("code", ""), r.get("detail", ""))[:90])


def s17_manual_backend_requires_deliverables(m):
    """外部工具由人手工完成: 交付物不齐 => 具名停止, 绝不"当作完成"。"""
    led = m.Ledger(out_dir("s17") / "boot.jsonl", m.SimClock())
    be = m.ManualActionBackend(led, out_dir("s17"), m.SimClock(), wait_seconds=0)
    st = {"action": "readback", "as": "rb",
          "command": "pyocd packmem -t cortex_m -O no_pack -c 289028 -o readback.bin 0 289028",
          "image": PLAN["identity_binding"]["image"]}
    try:
        be.readback(st, {})
        return False, "缺交付物却返回了成功"
    except m.Stop as e:
        return e.code == "ACTION_DELIVERABLES_MISSING", e.code


def s18_manual_readback_hash_gate(m):
    """交付物齐但字节不是那份镜像 => 真实身份门必须拒(模拟字节进不了这里)。"""
    d = out_dir("s18")
    led = m.Ledger(d / "boot.jsonl", m.SimClock())
    be = m.ManualActionBackend(led, d, m.SimClock(), wait_seconds=0)
    ad = d / "action_readback"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "rc.txt").write_text("0\n", encoding="utf-8")
    (ad / "readback.bin").write_bytes(b"\xAB" * int(PLAN["identity_binding"]["image"]["bytes"]))
    st = {"action": "readback", "as": "rb", "command": "pyocd packmem ...",
          "image": PLAN["identity_binding"]["image"]}
    try:
        be.readback(st, {})
        return False, "散列不符却通过"
    except m.Stop as e:
        return e.code == "READBACK_HASH_MISMATCH", e.code


def s19_silent_port_no_retry(m):
    steps = [{"cmd": "safety_status", "as": "boot", "seconds": 1,
              "expect_contains": ["[SAFETY]"]}]
    fx = {"boot_bytes": [], "inject": {"port_silent": True}}
    r = m.exec_session("s19", steps, out_dir("s19"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1}})
    tx = [x for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return (len(tx) == 1 and r["code"] == "MISSING_RESPONSE_PROMPT",
            "tx=%d code=%s" % (len(tx), r["code"]))


def s20_needles_proven_against_c(m):
    src_root = HERE.parents[3] / "rt-thread-master" / "rt-thread-master" / \
        "bsp" / "novosns" / "ns800" / "ns800rt7p65-nssinepad"
    lines, bad = m.prove_needles(HERE / "needles.json", src_root)
    return (bad == 0 and len(lines) >= 12, "total=%d unproven=%d" % (len(lines), bad))


def s21_static_port_discipline(m):
    bad = m.static_port_discipline(str(CHAIN))
    return (not bad, "post_open_attribute_writes=%s" % (",".join(bad) if bad else "none"))


def s22_quota_exhausted_refuses_send(m):
    steps = [{"cmd": "motor_start", "as": "s1", "seconds": 1,
              "expect_contains": ["[MOT]"], "quota": {"name": "motor_run", "n": 1}},
             {"cmd": "motor_start", "as": "s2", "seconds": 1,
              "expect_contains": ["[MOT]"], "quota": {"name": "motor_run", "n": 1}}]
    fx = {"boot_bytes": [], "responses": {"motor_start": ["[MOT] state=2 name=CRUISE"]}}
    r = m.exec_session("s22", steps, out_dir("s22"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1, "motor_run": 1}})
    tx = [x["bytes"].strip() for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return (r["code"] == "QUOTA_EXHAUSTED" and
            sum(1 for t in tx if t == "motor_start") == 1,
            "code=%s starts=%d" % (r["code"], sum(1 for t in tx if t == "motor_start")))


def s23_negative_five_second_cap(m):
    """定向检出 1: 去掉起转后的硬截止 => s10 必须不再成立(5 秒额度只是"扣了"而不停)。"""
    mod = _mutant(m, "NEGATIVE-INJECTION RV10a: 起转后不再受硬截止约束",
                  [("        hard = self.t0 + self.cap",
                    "        hard = self.clock.monotonic() + want_seconds + slack"
                    "   # NEGATIVE-INJECTION RV10a: 硬截止不再约束业务动作")])
    ok, msg = s10_five_second_hard_cap(mod)
    return (not ok, "去掉硬截止后 s10 仍然通过 => 判据不足: " + msg)


def s24_negative_cleanup_gate(m):
    """定向检出 2: finally 只关端口(不 stop/disarm) => s11 必须不再成立。"""
    mod = _mutant(m, "NEGATIVE-INJECTION RV10b: 清理只关端口",
                  [('            disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture, hook)',
                    '            # NEGATIVE-INJECTION RV10b: finally 只关端口, 不 stop/disarm\n'
                    '            disarm_state, cerr = "SKIPPED_BY_MUTANT", []')])
    ok, msg = s11_error_after_start_still_disarms(mod)
    return (not ok, "去掉撤能清理后 s11 仍然通过 => 判据不足: " + msg)


def s25_negative_business_layer(m):
    """定向检出 3: 去掉业务失败层 => 真实 C 的 learn FAILED 又被当成步骤成功。"""
    src = CHAIN.read_text(encoding="utf-8")
    mod = _mutant(m, "NEGATIVE-INJECTION RV10c: 业务失败层被移除",
                  [("    bad = business_failures(rows)", "    bad = []   # NEGATIVE-INJECTION RV10c")])
    ok, msg = s05_real_c_learn_failure_is_business_stop(mod)
    return (not ok, "去掉业务层后 s05 仍然通过 => 判据不足: " + msg)


def _ledger_events(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _mutant(base_mod, marker, pairs):
    """把执行器拷到仓库外、按字面替换(必须**恰好命中一次**)后重新导入。"""
    src = CHAIN.read_text(encoding="utf-8")
    for old, new in pairs:
        n = src.count(old)
        if n != 1:
            raise AssertionError("突变锚点命中 %d 次(要求恰好 1): %s" % (n, old[:40]))
        src = src.replace(old, new)
    for _o, _n in pairs:
        if "NEGATIVE-INJECTION" not in _n:
            raise AssertionError("突变替换文本必须自带 NEGATIVE-INJECTION 标记")
    d = WORK / ("mut-" + hashlib_sha(marker))
    d.mkdir(parents=True, exist_ok=True)
    p = d / "m8r2_serial_chain_mutant.py"
    p.write_text(src, encoding="utf-8")
    return load(p, "mut_" + hashlib_sha(marker))


def hashlib_sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def main():
    m = load(CHAIN, "m8r2_chain_under_test")
    cases = [("FS%02d" % (i + 1), fn) for i, fn in enumerate([
        s01_prompt_recognized, s02_missing_prompt_is_not_complete, s03_illegal_bytes_named,
        s04_duplicate_field_refused, s05_real_c_learn_failure_is_business_stop,
        s06_named_refusal_halts_commit, s07_real_c_success_accepted,
        s08_watch_stop_halts_dangerous_steps, s09_no_trigger_is_observation,
        s10_five_second_hard_cap, s11_error_after_start_still_disarms,
        s12_unconfirmable_disable_is_unknown, s13_real_manual_never_uses_fixture,
        s14_real_manual_without_operator_stops, s15_denied_manual_runs_no_motion,
        s16_actions_really_dispatched, s17_manual_backend_requires_deliverables,
        s18_manual_readback_hash_gate, s19_silent_port_no_retry, s20_needles_proven_against_c,
        s21_static_port_discipline, s22_quota_exhausted_refuses_send,
        s23_negative_five_second_cap, s24_negative_cleanup_gate, s25_negative_business_layer,
    ])]
    ids = [c[0] for c in cases]
    if len(set(ids)) != len(ids):
        raise AssertionError("场景 id 重复")
    passed = 0
    for cid, fn in cases:
        if case(cid, lambda fn=fn: fn(m)):
            passed += 1
    print("SUMMARY|cases=%d pass=%d fail=%d executed=%d" %
          (len(cases), passed, len(cases) - passed, len(RESULT)))
    print("SELFTEST_WORKDIR|" + str(WORK))
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
