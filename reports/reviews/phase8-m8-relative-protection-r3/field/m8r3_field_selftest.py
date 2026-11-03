#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R3 现场候选包的离线自检(真实执行, 不是清单)。

四条纪律:
 1) 正控文本一律来自**真实来源**: 板上原始采集/本轮真实产品 C 输出, 或 rows.json 里逐条
    标了出处的行。不手写"成功文案"反证自己, 也不再使用产品从不打印的 name=IDLE/CRUISE。
 2) 每条判定都是 PASS/FAIL + 具名原因; 只有数字或状态位的地方不写 PASS 之外的物理结论。
 3) 定向检出(Negative-Injection)在**仓库外的一次性副本**上做: 每条突变都必须让对应的
    正控用例**转为 FAIL**, 否则判据不足。突变文本自带 NEGATIVE-INJECTION 标记。
 4) 负控断言的是**实际发送序列与时刻**(读台账的 TX/CLEANUP_READ), 不是汇总标签。

R2 的 25 个身份(FS01..FS25)全部保留、顺序不变; 其中依赖旧夹具形状(3 字段撤能块、
name=CRUISE、manual 排在 arm 之后、packmem 命令串)的几条, 按 R3-B 的真实合同改写到
**同一条性质**上 —— 审核要求"不要为夹具改固件输出", 所以这里改的是夹具。
"""
import builtins
import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHAIN = HERE / "m8r3_serial_chain.py"
ROWS = json.loads((HERE / "rows.json").read_text(encoding="utf-8-sig"))
PLAN = json.loads((HERE / "plan.json").read_text(encoding="utf-8-sig"))
REPO = HERE.parents[3]        # reports/reviews/<task>/field -> 仓库根
HOST_OUT = HERE.parent / "host" / "c_chain_output.txt"
C_LINES = HOST_OUT.read_text(encoding="utf-8").splitlines() if HOST_OUT.exists() else []

RESULT = []
WORK = Path(tempfile.mkdtemp(prefix="m8r3-field-selftest-"))

# 撤能/运行/锁存三块**只用交付表里的行**: 自检与现场共用同一份数据, 不留第二套判据
DISARM_ROWS = ROWS["commands"]["motor_status"]["healthy"]
RUNNING_ROWS = ROWS["commands"]["motor_status@running"]["healthy"]
LATCHED_ROWS = ROWS["commands"]["motor_status@latched"]["healthy"]
ARM_OK = ROWS["commands"]["motor_arm"]["healthy"]
DIR_OK = ROWS["commands"]["motor_dir 0"]["healthy"]
TARGET_OK = ROWS["commands"]["motor_target 100"]["healthy"]
STOP_OK = ROWS["commands"]["motor_stop"]["healthy"]
DISARM_OK = ROWS["commands"]["motor_disarm"]["healthy"]
SAFETY_OK = ROWS["commands"]["safety_status"]["healthy"]


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
        raise AssertionError("本轮 C 输出里没有这条行(或 host/c_chain_output.txt 缺失): " + prefix)
    return hits[0]


def ok_rows(rows):
    return ("\r\n".join(rows) + "\r\nmsh >").encode("utf-8")


def motion_fx(extra):
    """起转类场景的夹具: 业务响应 + **同一条表里的清理响应**(否则"业务有回、清理静默"是假象)。
    默认撤能块就是真机整块(DISARM_ROWS), 所以 CONFIRMED_DISABLED 是六个合同字段证出来的。"""
    fx = {"boot_bytes": [], "responses": {
        "motor_stop": STOP_OK, "motor_disarm": DISARM_OK, "motor_status": DISARM_ROWS}}
    for k, v in extra.items():
        if k == "__no_cleanup__":
            fx["responses"] = {}
        else:
            fx["responses"][k] = v
    return fx


def tx_seq(result):
    return [x["bytes"].strip() for x in _ledger_events(result["ledger"]) if x["kind"] == "TX"]


def stamp(m, steps):
    """R6A §4 之后, 会改变设备/持久状态的命令**必须**带额度申报才允许发出。
    工装的步骤表也守这条合同: 按执行器**同一张映射表**补上它本该有的申报。
    已经申报的一律不动(那正是某些用例要测的额度语义)。"""
    out = []
    for st in steps:
        st = dict(st)
        key = m.quota_key_for_command(st["cmd"]) if st.get("cmd") else None
        if key and not st.get("quota"):
            st["quota"] = {"name": key, "n": 1}
        out.append(st)
    return out


def fx_approved(m, steps, sid="FX", tag="fx"):
    """给真实传输的工装会话造一份**离线批准件**: 步骤落成真实字节, 由执行器同一套
    `verify_approved_binding` 逐字段核对。它只活在工作目录里、只配假端口, 不是现场批准,
    也不给任何硬件额度。"""
    ss = stamp(m, steps)
    pfile = Path(out_dir("approved-%s-%s" % (sid, tag))) / "plan.json"
    pfile.write_text(json.dumps({"quota": dict(BASE_QUOTA),
                                 "stages": [{"id": sid, "kind": "serial_session",
                                             "steps": ss}]}, ensure_ascii=False),
                     encoding="utf-8")
    return {"plan_path": str(pfile), "plan_sha256": m.sha_of(pfile), "stage": sid,
            "profile": "healthy", "approval_id": "OFFLINE-FXTURE-NO-DEVICE",
            "steps_sha256": m.canonical_steps_sha(ss)}


BASE_QUOTA = {"port_open": 4, "download": 1, "readback": 1, "explicit_reset": 1,
              "motor_run": 3, "motor_run_seconds_total": 15, "motion_cap_seconds_per_run": 5,
              "config_save": 1, "approved_reboot": 1, "tmc_config_apply": 2,
              "irq_polarity_prep": 4, "retry": 0, "physical_collision": 0,
              "diag_profile_zero": 1, "diag_profile_learn": 2, "diag_profile_commit": 1,
              "blackbox_clear": 0, "dmm_readings": 0,
              "cleanup_motor_stop": 2, "cleanup_motor_disarm": 2, "cleanup_motor_status": 2}


def run_with_write_hook(m, mode, steps, extra_steps=(), cap=5):
    """用**纯内存端口工厂**跑一条 real 传输会话: 与审核探针同一条路径, 断言实际写序/时刻。
    只有端口工厂与 input() 被替换, 执行器本体一行没改; 不导入 serial, 不碰设备。"""
    clock = m.SimClock()
    captured = []
    status_calls = [0]

    class ReactivePort(m.FakePort):
        def write(self, data):
            cmd = data.decode("ascii").strip()
            if cmd == "motor_start" and mode == "partial_start":
                self.feed(b"[MOT] partial" * 3)
                raise OSError("injected write failure AFTER bytes were accepted")
            if cmd == "motor_start" and mode == "slow_start":
                clock.sleep(2)
            n = m.FakePort.write(self, data)
            captured.append((round(clock.monotonic(), 3), cmd))
            if mode == "arm_ack_lost" and cmd == "motor_arm":
                return n                                  # 写到达, 应答丢失
            if mode == "silent_stop" and cmd == "motor_stop":
                return n                                  # stop 无应答
            if mode == "silent_status" and cmd == "motor_status":
                return n                                  # 清理查询无应答
            if cmd == "motor_status":
                status_calls[0] += 1
                rows = RUNNING_ROWS if (mode == "running" and status_calls[0] == 1) else DISARM_ROWS
                if mode == "dup_field":
                    rows = DISARM_ROWS + [DISARM_ROWS[2]]
                if mode == "req_high":
                    rows = [r.replace("drv_request=LOW pad=0", "drv_request=HIGH pad=1")
                            for r in DISARM_ROWS]
            else:
                rows = {"motor_arm": ARM_OK, "motor_dir 0": DIR_OK,
                        "motor_target 100": TARGET_OK, "motor_stop": STOP_OK,
                        "motor_disarm": DISARM_OK, "motor_start": []}.get(cmd, ["[ACK] complete"])
            self.feed(ok_rows(rows))
            return n

    saved_factory, saved_input = m.make_real_port, builtins.input
    m.make_real_port = lambda port_name, led: ReactivePort([], led)
    builtins.input = lambda prompt: (clock.sleep(12), "yes")[1]
    plan = {"quota": dict(BASE_QUOTA)}
    all_steps = stamp(m, list(steps) + list(extra_steps))
    # R6A §4: 真实传输只接"经批准的整包"。这里的批准件就是本用例自己那份步骤落成的**真实字节**
    # —— 执行器仍然逐字段核对(改一步骤就会被 STEPS_NOT_BOUND_TO_APPROVAL 拒), 只是这张批准
    # 只对本工装有效, 不指向任何设备动作。
    pfile = Path(out_dir("plan-FX-" + mode)) / "plan.json"
    pfile.write_text(json.dumps({"quota": dict(BASE_QUOTA),
                                 "stages": [{"id": "FX", "kind": "serial_session",
                                             "steps": all_steps}]}, ensure_ascii=False),
                     encoding="utf-8")
    approved = {"plan_path": str(pfile), "plan_sha256": m.sha_of(pfile), "stage": "FX",
                "profile": "healthy", "approval_id": "OFFLINE-FX-" + mode,
                "steps_sha256": m.canonical_steps_sha(all_steps)}
    try:
        r = m.exec_session("FX", stamp(m, all_steps), out_dir("hook-" + mode),
                           "real", {}, "MEMORY_PORT", 30, clock, motion_cap_seconds=cap,
                           plan=plan, approved=approved)
    except m.Stop as e:                             # 开口之前就被计划静态判据拒绝
        r = {"status": "STOP", "code": e.code, "detail": e.detail, "disarm": "NOT_ARMED",
             "terminal": "KNOWN", "success": False, "obligation": False, "cleanup_errors": [],
             "ledger": "", "budget_seconds": None}
    finally:
        m.make_real_port, builtins.input = saved_factory, saved_input
    r["writes"] = [c for _, c in captured]
    r["times"] = captured
    return r


def dispatch(m, profile, plan=None, rows=None, root=None, transport="stub", backend="sim",
             token=None, expected=None, approval_id=None, approval_root=None, plan_path=None):
    """整包调度的一次真实执行(只用假端口 + sim 后端)。rows 可以换成变异表, 用来证明
    "恢复比较/新增取证"这两条判据真的会拒绝, 而不是只在文档里写着会拒绝。"""
    d = root or out_dir("dispatch-" + profile)
    if rows is not None:
        (Path(d) / "rows.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    p = plan if plan is not None else PLAN
    if rows is not None:
        (Path(d) / "plan.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    plan_dir = str(HERE) if rows is None else str(Path(d))
    return m.dispatch_plan(p, transport, backend, Path(d) / "run", m.SimClock(), profile,
                           "NO_DEVICE", str(REPO), token, expected, "",
                           plan_dir=plan_dir, approval_id=approval_id,
                           approval_root=approval_root, plan_path=plan_path), d


def plan_bytes(plan, tag):
    """把计划落成**真实字节**。R6A §3 要求批准身份三件 SHA 齐全, 而 plan_sha256 只能从
    "实际要执行的那份文件"算 —— 所以离线开账也必须指向一个真实路径, 不能照抄内存对象。"""
    p = Path(out_dir("planbytes-" + tag)) / "plan.json"
    p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return str(p)


def bind_approval(m, plan, aid, root=None):
    """给一次 dispatch 绑定批准身份(离线开立持久账, 不碰设备)。
    返回 (approval_root, id, plan_path): 三者一起用才算绑对 —— 开账与执行必须指向同一份计划字节。
    真实传输现在**必须**有这本账才允许进入, 所以原先只填令牌的用例都要走这里。"""
    r = Path(root or out_dir("approvals-" + aid))
    pp = plan_bytes(plan, aid)
    m.approval_init(r, aid, m.current_identities(plan, pp, str(REPO)), plan["quota"],
                    note="selftest offline bind, no device action")
    return str(r), aid, pp


def full_ids(seed=1):
    """三件齐全且形状合法的假身份(64 位小写十六进制), 只给离线账用。"""
    return {"plan_sha256": "%064x" % seed, "executor_sha256": "%064x" % (seed + 1),
            "image_sha256": "%064x" % (seed + 2)}


def tx_stage(res, sid):
    """阶段结果里只有 dir: 发送序列要从该会话自己的台账读。"""
    return tx_seq({"ledger": str(Path(stage(res, sid)["dir"]) / "ledger.jsonl")})


def stage(res, sid):
    hits = [x for x in res["stages"] if x["id"] == sid]
    if not hits:
        raise AssertionError("阶段没出现在结果里: " + sid)
    return hits[0]


# --------------------------------------------------------------------------- FS01..FS25(R2 身份保留)
def s01_prompt_recognized(m):
    c = m.SimClock()
    led = m.Ledger(out_dir("s01") / "l.jsonl", c)
    p = m.FakePort([ok_rows(["[SAFETY] ready=YES first_err=0"])], led)
    lines, n = m.read_until_prompt(p, 1.0, led, out_dir("s01") / "a.bin", c)
    led.close()
    return (c.t < 0.05 and len(lines) == 1, "elapsed=%.3f rows=%d bytes=%d" % (c.t, len(lines), n))


def s02_missing_prompt_is_not_complete(m):
    c = m.SimClock()
    led = m.Ledger(out_dir("s02") / "l.jsonl", c)
    p = m.FakePort([b"[SAFETY] ready=YES first_err=0\r\n"], led)
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
    steps = [{"cmd": "diag_profile commit", "as": "commit",
              "allowed_outcomes": ["[DIAG] commit REFUSED"]},
             {"cmd": "config_save", "as": "save"}]
    fx = {"boot_bytes": [], "responses": {
        "diag_profile commit": [c_line("[DIAG] commit REFUSED")],
        "config_save": ["[CFG] save: OK"]}}
    r = m.exec_session("s06", stamp(m, steps), out_dir("s06"), "stub", fx, "NO_DEVICE", 30,
                       m.SimClock(),
                       plan={"quota": {"port_open": 1, "diag_profile_commit": 1}})
    sent = tx_seq(r)
    return (r["status"] == "REFUSED" and "config_save" not in sent,
            "status=%s commands=%s" % (r["status"], ",".join(sent)))


def s07_real_c_success_accepted(m):
    led = m.Ledger(out_dir("s07") / "l.jsonl", m.SimClock())
    line = c_line("[DIAG] mode=RELATIVE_ACTIVE")
    st = next(s for sg in PLAN["stages"] for s in sg.get("steps", []) if s.get("as") == "arm_rel")
    v = m.assert_step(st, [line], "arm_rel", led)
    led.close()
    return (v["verdict"] == "OK", "verdict=%s text=%s" % (v["verdict"], line))


def s08_watch_stop_halts_dangerous_steps(m):
    steps = [
        {"cmd": "__watch__", "as": "trip", "seconds": 5,
         "expect_contains": ["[DIAG] REL TRIP r="], "trigger_needles": ["[DIAG] REL TRIP r="]},
        {"cmd": "motor_arm", "as": "after_failed_watch"},
        {"cmd": "fault_reset", "as": "reset"},
    ]
    fx = motion_fx({"__watch__": ROWS["commands"]["__watch__"]["healthy"],
                    "motor_arm": ARM_OK, "fault_reset": ROWS["commands"]["fault_reset"]["healthy"]})
    r = m.exec_session("s08", stamp(m, steps), out_dir("s08"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1}})
    sent = tx_seq(r)
    return ("motor_arm" not in sent and "fault_reset" not in sent and
            r["status"] == "STOP" and r["code"] == "EXPECTATION_NOT_MET",
            "status=%s code=%s commands=%s" % (r["status"], r["code"], ",".join(sent)))


def s09_no_trigger_is_observation(m):
    """未触发是**观察终态**而不是失败: 运动之后的危险步骤一律不发, 清理照走。
    R3 改写: 观察窗不再要求 [MOT](板上巡航期不打印命令响应), 起转成功不再假造 CRUISE 回包。"""
    steps = [
        {"cmd": "motor_arm", "as": "arm", "expect_contains": ["[MOT] ARMED"]},
        {"cmd": "motor_target 100", "as": "target", "expect_contains": ["set target 100 Hz"]},
        {"cmd": "motor_start", "as": "start", "seconds": 5, "expect_absent": ["REFUSED"]},
        {"cmd": "__watch__", "as": "wait", "seconds": 5, "no_trigger_is_observation": True,
         "trigger_needles": ["[DIAG] REL TRIP r="]},
        {"cmd": "fault_reset", "as": "must_not_run"},
    ]
    fx = motion_fx({"motor_arm": ARM_OK, "motor_target 100": TARGET_OK, "motor_start": [],
                    "__watch__": ROWS["commands"]["__watch__"]["healthy"],
                    "fault_reset": ROWS["commands"]["fault_reset"]["healthy"]})
    r = m.exec_session("s09", stamp(m, steps), out_dir("s09"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    tx = tx_seq(r)
    return (r["status"] == "OBSERVATION_NO_TRIGGER" and r["success"] is True and
            r["disarm"] == "CONFIRMED_DISABLED" and "fault_reset" not in tx and
            "motor_stop" in tx and "motor_disarm" in tx,
            "status=%s success=%s disarm=%s tx=%s" % (
                r["status"], r["success"], r["disarm"], ",".join(tx)))


def s10_five_second_hard_cap(m):
    steps = [
        {"cmd": "motor_start", "as": "start", "seconds": 5, "expect_absent": ["REFUSED"]},
        {"cmd": "__watch__", "as": "wait", "seconds": 10, "expect_contains": ["[MOT]"]},
        {"cmd": "motor_stop", "as": "stop"},
    ]
    fx = motion_fx({"motor_start": [], "__watch__": RUNNING_ROWS})
    r = m.exec_session("s10", stamp(m, steps), out_dir("s10"), "stub", fx, "NO_DEVICE", 60, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    tx = [x for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    start = next(x["t_s"] for x in tx if x["bytes"] == "motor_start\r\n")
    stop = next((x["t_s"] for x in tx if x["bytes"] == "motor_stop\r\n"), None)
    span = None if stop is None else round(stop - start, 3)
    return (span is not None and span <= 5.0 and r["disarm"] in
            ("CONFIRMED_DISABLED", "UNKNOWN"),
            "start_to_stop=%s declared=5 disarm=%s" % (span, r["disarm"]))


def s11_error_after_start_still_disarms(m):
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 5, "expect_absent": ["REFUSED"]},
             {"cmd": "not_in_fixture", "as": "later_failure"}]
    fx = motion_fx({"motor_start": []})
    r = m.exec_session("s11", stamp(m, steps), out_dir("s11"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    tx = tx_seq(r)
    return (r["code"] == "STUB_MISS" and "motor_stop" in tx and "motor_disarm" in tx and
            r["disarm"] == "CONFIRMED_DISABLED" and r["terminal"] == "KNOWN",
            "code=%s tx=%s disarm=%s" % (r["code"], ",".join(tx), r["disarm"]))


def s12_unconfirmable_disable_is_unknown(m):
    """撤能字段读不到 => 终态 UNKNOWN, 且主错与清理错各自保留(M6 语义)。"""
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 5, "expect_absent": ["REFUSED"]},
             {"cmd": "missing_response", "as": "boom"}]
    fx = motion_fx({"motor_start": [], "__no_cleanup__": True})
    r = m.exec_session("s12", stamp(m, steps), out_dir("s12"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5, plan={"quota": {"port_open": 1, "motor_run": 2}})
    return (r["disarm"] == "UNKNOWN" and r["terminal"] == "UNKNOWN" and
            bool(r["cleanup_errors"]) and r["code"] == "STUB_MISS" and r["success"] is False,
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
            "operator_calls=%d answer=%s fixture_ignored=yes" % (calls["n"], got))


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
    """R3 改写: 人工门必须排在 arm/start **之前**(R3-B4), 所以这里用合法顺序,
    断言的仍是同一条性质: 操作者说不做, 一条运动命令都不许发。"""
    steps = [{"manual": "cruise_reached", "as": "gate"},
             {"cmd": "motor_arm", "as": "arm"},
             {"cmd": "motor_start", "as": "start"}]
    fx = {"boot_bytes": [], "manual": {"cruise_reached": "no"},
          "responses": {"motor_arm": ARM_OK, "motor_start": []}}
    r = m.exec_session("s15", stamp(m, steps), out_dir("s15"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1, "motor_run": 1}})
    tx = tx_seq(r)
    return ("motor_start" not in tx and "motor_arm" not in tx and
            r["code"] == "MANUAL_GATE_DENIED" and r["obligation"] is False,
            "code=%s tx=%s obligation=%s" % (r["code"], ",".join(tx), r["obligation"]))


def s16_actions_really_dispatched(m):
    steps = [{"action": "download", "as": "dl", "quota_name": "download",
              "image": PLAN["tools"]["download"]["image"],
              "command": PLAN["tools"]["download"]["command"],
              "argv": PLAN["tools"]["download"]["argv"]},
             {"action": "readback", "as": "rb", "quota_name": "readback",
              "image": PLAN["tools"]["readback"]["image"],
              "command": PLAN["tools"]["readback"]["command"],
              "argv": PLAN["tools"]["readback"]["argv"]},
             {"action": "reset", "as": "rst", "quota_name": "explicit_reset",
              "command": PLAN["tools"]["reset"]["command"],
              "argv": PLAN["tools"]["reset"]["argv"]}]
    led = m.Ledger(out_dir("s16") / "boot.jsonl", m.SimClock())
    be = m.SimActionBackend(led)
    r = m.exec_session("s16", stamp(m, steps), out_dir("s16"), "stub", {"boot_bytes": [], "responses": {}},
                       "NO_DEVICE", 30, m.SimClock(), backend=be,
                       plan={"quota": {"port_open": 1, "download": 1, "readback": 1,
                                       "explicit_reset": 1}})
    kinds = [x.get("action") for x in r["results"] if x["kind"] == "action"]
    gates = [x.get("identity_gate", "") for x in r["results"] if x["kind"] == "action"]
    return (kinds == ["download", "readback", "reset"] and be.calls == kinds and
            all("NOT_EXERCISED" in g for g in gates),
            "calls=%s status=%s code=%s" % (",".join(kinds), r["status"], r.get("code", "")))


def s17_manual_backend_requires_deliverables(m):
    """外部工具由人手工完成: 交付物不齐 => 具名停止, 绝不"当作完成"。"""
    led = m.Ledger(out_dir("s17") / "boot.jsonl", m.SimClock())
    be = m.ManualActionBackend(led, out_dir("s17"), m.SimClock(), wait_seconds=0)
    st = {"action": "readback", "as": "rb", "command": PLAN["tools"]["readback"]["command"],
          "image": PLAN["tools"]["readback"]["image"]}
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
    img = PLAN["tools"]["readback"]["image"]
    (ad / "readback.bin").write_bytes(b"\xAB" * int(img["bytes"]))
    st = {"action": "readback", "as": "rb", "command": PLAN["tools"]["readback"]["command"],
          "image": img}
    try:
        be.readback(st, {})
        return False, "散列不符却通过"
    except m.Stop as e:
        return e.code == "READBACK_HASH_MISMATCH", e.code


def s19_silent_port_no_retry(m):
    steps = [{"cmd": "safety_status", "as": "boot", "seconds": 1,
              "expect_contains": ["[SAFETY]"]}]
    fx = {"boot_bytes": [], "inject": {"port_silent": True}}
    r = m.exec_session("s19", stamp(m, steps), out_dir("s19"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1}})
    tx = [x for x in _ledger_events(r["ledger"]) if x["kind"] == "TX"]
    return (len(tx) == 1 and r["code"] == "MISSING_RESPONSE_PROMPT",
            "tx=%d code=%s" % (len(tx), r["code"]))


def s20_needles_proven_against_c(m):
    src_root = REPO / PLAN["needles_source_root"]
    declared = len(json.loads((HERE / "needles.json").read_text(encoding="utf-8-sig"))["needles"])
    lines, bad = m.prove_needles(HERE / "needles.json", src_root)
    return (bad == 0 and len(lines) == declared,
            "total=%d declared=%d unproven=%d" % (len(lines), declared, bad))


def s21_static_port_discipline(m):
    bad = m.static_port_discipline(str(CHAIN))
    return (not bad, "post_open_attribute_writes=%s" % (",".join(bad) if bad else "none"))


def s22_quota_exhausted_refuses_send(m):
    steps = [{"cmd": "motor_start", "as": "s1", "seconds": 1, "expect_absent": ["REFUSED"],
              "quota": {"name": "motor_run", "n": 1}},
             {"cmd": "motor_start", "as": "s2", "seconds": 1, "expect_absent": ["REFUSED"],
              "quota": {"name": "motor_run", "n": 1}}]
    fx = motion_fx({"motor_start": []})
    r = m.exec_session("s22", stamp(m, steps), out_dir("s22"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1, "motor_run": 1,
                                       "cleanup_motor_stop": 2, "cleanup_motor_disarm": 2,
                                       "cleanup_motor_status": 2}})
    tx = tx_seq(r)
    return (r["code"] == "QUOTA_EXHAUSTED" and sum(1 for t in tx if t == "motor_start") == 1,
            "code=%s starts=%d" % (r["code"], sum(1 for t in tx if t == "motor_start")))


def s23_negative_five_second_cap(m):
    """定向检出 1: 去掉起转后的硬截止 => s10 必须不再成立(5 秒额度只是"扣了"而不停)。"""
    mod = _mutant("RV10a", [("        hard = self.t0 + self.cap",
                            "        hard = self.clock.monotonic() + want_seconds + slack"
                            "   # NEGATIVE-INJECTION RV10a: 硬截止不再约束业务动作")])
    ok, msg = s10_five_second_hard_cap(mod)
    return (not ok, "去掉硬截止后 s10 仍然通过 => 判据不足: " + msg)


def s24_negative_cleanup_gate(m):
    """定向检出 2: finally 只关端口(不 stop/disarm) => s11 必须不再成立。"""
    mod = _mutant("RV10b", [('        if obligation:',
                             '        if obligation and False:   # NEGATIVE-INJECTION RV10b: 撤能清理被摘掉')])
    ok, msg = s11_error_after_start_still_disarms(mod)
    return (not ok, "去掉撤能清理后 s11 仍然通过 => 判据不足: " + msg)


def s25_negative_business_layer(m):
    """定向检出 3: 去掉业务失败层 => 真实 C 的 learn FAILED 又被当成步骤成功。"""
    # R5-C: 业务失败扫描的入参改名 rows->rest(整行锚定的状态披露行先被摘出去), 突变锚点跟着走
    mod = _mutant("RV10c", [("    bad = business_failures(rest)",
                             "    bad = []   # NEGATIVE-INJECTION RV10c")])
    ok, msg = s05_real_c_learn_failure_is_business_stop(mod)
    return (not ok, "去掉业务层后 s05 仍然通过 => 判据不足: " + msg)


# --------------------------------------------------------------------------- FS26..(R3 边界与整包)
def s26_write_failure_still_disarms(m):
    """R3-B1: 部分字节已被接收后 write 抛错 => 义务在写**之前**建立, 仍发一次必要撤能。"""
    r = run_with_write_hook(m, "partial_start",
                            [{"cmd": "motor_start", "as": "start", "seconds": 1,
                              "expect_absent": ["REFUSED"]}])
    return (r["obligation"] is True and r["writes"] == ["motor_stop", "motor_disarm", "motor_status"]
            and r["code"] == "COMMAND_WRITE_FAILED" and r["success"] is False,
            "obligation=%s writes=%s code=%s disarm=%s" % (
                r["obligation"], ",".join(r["writes"]), r["code"], r["disarm"]))


def s27_write_cost_inside_cap(m):
    """R3-B2: start 的写本身耗 2 s => 截止起点在尝试**之前**, 清理仍落在 cap 内。"""
    r = run_with_write_hook(m, "slow_start",
                            [{"cmd": "motor_start", "as": "start", "seconds": 1,
                              "expect_absent": ["REFUSED"]},
                             {"cmd": "__watch__", "as": "wait", "seconds": 5,
                              "no_trigger_is_observation": True,
                              "trigger_needles": ["[DIAG] REL TRIP r="]}])
    st = [t for t, c in r["times"] if c == "motor_start"]
    stop_t = [t for t, c in r["times"] if c == "motor_stop"]
    return (bool(st) and bool(stop_t) and stop_t[0] <= 5.0 and
            r["disarm"] == "CONFIRMED_DISABLED",
            "start_t=%s stop_t=%s cap=5 disarm=%s" % (st, stop_t, r["disarm"]))


def s28_stop_silent_does_not_eat_disarm(m):
    """R3-B3: stop 无应答 => disarm/status 仍各自在自己的窗里发出; 主错与清理错分开。"""
    r = run_with_write_hook(m, "silent_stop",
                            [{"cmd": "motor_start", "as": "start", "seconds": 1,
                              "expect_absent": ["REFUSED"]},
                             {"cmd": "__watch__", "as": "wait", "seconds": 5,
                              "no_trigger_is_observation": True,
                              "trigger_needles": ["[DIAG] REL TRIP r="]}])
    stop_t = [t for t, c in r["times"] if c == "motor_stop"]
    dis_t = [t for t, c in r["times"] if c == "motor_disarm"]
    return (r["writes"] == ["motor_start", "motor_stop", "motor_disarm", "motor_status"] and
            "motor_stop:NO_RESPONSE" in r["cleanup_errors"] and r["success"] is False and
            bool(stop_t) and bool(dis_t) and dis_t[0] > stop_t[0],
            "writes=%s errs=%s stop_t=%s disarm_t=%s success=%s" % (
                ",".join(r["writes"]), r["cleanup_errors"], stop_t, dis_t, r["success"]))


def s29_manual_after_motion_refused_before_open(m):
    """R3-B4: 人工门排在 start 之后 => 开口前就具名拒绝, **一条命令都不发**。"""
    r = run_with_write_hook(m, "manual", [{"cmd": "motor_start", "as": "start", "seconds": 1}],
                            extra_steps=[{"manual": "operator_ready", "as": "gate"}])
    return (r["writes"] == [] and r["code"] == "PLAN_MANUAL_AFTER_MOTION" and
            r["success"] is False, "writes=%s code=%s" % (r["writes"], r["code"]))


def s30_real_disable_rows_confirm(m):
    """R3-B5 正控: 真机整块(六个合同字段)必须证得 CONFIRMED_DISABLED, 不要求 IDLE。"""
    summ, bad = m.disable_contract(DISARM_ROWS)
    return (summ is not None and not bad and summ["state_name"] == "OTHER" and
            summ["armed"] == 0 and summ["drv_request"] == "LOW" and summ["verdict"] == "OK",
            "bad=%s fields=%s" % (bad, json.dumps(summ or {}, ensure_ascii=False,
                                                  sort_keys=True))[:170])


def s31_latched_fault_disable_still_provable(m):
    """故障锁存后的禁能同样要能证得: name=FAULT 在允许集合内(审核表第 5 行)。"""
    summ, bad = m.disable_contract(LATCHED_ROWS)
    return (summ is not None and not bad and summ["state_name"] == "FAULT" and
            summ["armed"] == 0,
            "bad=%s state=%s armed=%s" % (bad, summ and summ["state_name"],
                                          summ and summ["armed"]))


def s31b_no_capture_row_claims_idle_or_cruise(m):
    """夹具卫生: 交付表里不许再出现产品从不打印的 name=IDLE / name=CRUISE。"""
    rows_blob = json.dumps([r for cmd in ROWS["commands"].values()
                             for prof, v in cmd.items() if isinstance(v, list) for r in v],
                            ensure_ascii=False)
    return ("IDLE" not in rows_blob and "CRUISE" not in rows_blob and
            "IDLE" not in m.DISABLE_ALLOWED_STATES,
            "rows_with_idle_or_cruise=%s allowed_states=%s" % (
                "IDLE" in rows_blob or "CRUISE" in rows_blob, ",".join(m.DISABLE_ALLOWED_STATES)))


def s32_arm_ack_lost_cleans_up(m):
    """arm 的写已到达但应答丢失 => 潜在已武装同样进入必要清理(R2 只有 start 之后才清理)。"""
    r = run_with_write_hook(m, "arm_ack_lost",
                            [{"cmd": "motor_arm", "as": "arm", "seconds": 1,
                              "expect_absent": ["REFUSED"]}])
    return (r["obligation"] is True and "motor_disarm" in r["writes"] and
            r["code"] == "MISSING_RESPONSE_PROMPT" and r["success"] is False,
            "writes=%s obligation=%s code=%s" % (",".join(r["writes"]), r["obligation"], r["code"]))


def s33_cleanup_query_silent_is_unknown(m):
    """清理查询(status)无应答 => 不可证实的禁能一律 UNKNOWN, 且不是成功。"""
    r = run_with_write_hook(m, "silent_status",
                            [{"cmd": "motor_start", "as": "start", "seconds": 1,
                              "expect_absent": ["REFUSED"]},
                             {"cmd": "__watch__", "as": "wait", "seconds": 5,
                              "no_trigger_is_observation": True,
                              "trigger_needles": ["[DIAG] REL TRIP r="]}])
    return (r["writes"] == ["motor_start", "motor_stop", "motor_disarm", "motor_status"] and
            r["disarm"] == "UNKNOWN" and r["terminal"] == "UNKNOWN" and
            any("motor_status:NO_RESPONSE" in e for e in r["cleanup_errors"]) and
            r["success"] is False,
            "writes=%s disarm=%s errs=%s" % (",".join(r["writes"]), r["disarm"],
                                             r["cleanup_errors"]))


def s34_duplicate_field_is_unknown(m):
    """同一响应里 armed 行出现两次 => 不许择一采信, UNKNOWN + 具名字段。"""
    r = run_with_write_hook(m, "dup_field",
                            [{"cmd": "motor_start", "as": "start", "seconds": 1,
                              "expect_absent": ["REFUSED"]}])
    return (r["disarm"] == "UNKNOWN" and
            any("FIELD_NOT_UNIQUE_armed=2" in e for e in r["cleanup_errors"]) and
            r["success"] is False, "disarm=%s errs=%s" % (r["disarm"], r["cleanup_errors"]))


def s35_current_request_still_high_is_unknown(m):
    """drv_request 仍为 HIGH(当前请求侧) => 即使 armed=0 也不能确证禁能。"""
    r = run_with_write_hook(m, "req_high",
                            [{"cmd": "motor_start", "as": "start", "seconds": 1,
                              "expect_absent": ["REFUSED"]}])
    return (r["disarm"] == "UNKNOWN" and
            any("drv_request=HIGH/pad=1" in e for e in r["cleanup_errors"]) and
            r["success"] is False, "disarm=%s errs=%s" % (r["disarm"], r["cleanup_errors"]))


def s36_running_and_rest_profiles_both_directions(m):
    """expect_profile 两个方向都要有效: RUNNING 通过、同一份武装块在 AT_REST 下具名失败。"""
    r1 = run_with_write_hook(m, "running",
                             [{"cmd": "motor_start", "as": "start", "seconds": 1,
                               "expect_absent": ["REFUSED"]},
                              {"cmd": "motor_status", "as": "run", "expect_profile": "RUNNING"}])
    r2 = run_with_write_hook(m, "running",
                             [{"cmd": "motor_start", "as": "start", "seconds": 1,
                               "expect_absent": ["REFUSED"]},
                              {"cmd": "motor_status", "as": "run", "expect_profile": "AT_REST"}])
    return (r1["success"] is True and r2["code"] == "PROFILE_NOT_AT_REST" and
            r2["success"] is False, "ok=%s bad=%s" % (r1["status"], r2["code"]))


def s37_expect_values_rejects_wrong_value(m):
    """只核行数不算判据: armed=1 也满足旧 expect_fields 的形状, 现在必须被**值**判据拒绝。"""
    led = m.Ledger(out_dir("s37") / "l.jsonl", m.SimClock())
    row = r"\[MOT\] armed=(?P<armed>\d) drv_en_request=(?P<hist>\d) health=(?P<health>\w+)"
    v_bad = m.assert_step({"expect_values": [{"row": row, "must": {"armed": "0"}}]},
                          ["[MOT] armed=1 drv_en_request=1 health=OK"], "s37bad", led)
    v_ok = m.assert_step({"expect_values": [{"row": row, "must": {"armed": "1"}}]},
                         ["[MOT] armed=1 drv_en_request=1 health=OK"], "s37ok", led)
    led.close()
    return (v_bad["verdict"] == "STOP" and v_bad["code"] == "FIELD_VALUE_MISMATCH" and
            v_ok["verdict"] == "OK", "bad=%s good=%s" % (v_bad.get("code"), v_ok["verdict"]))


def s38_parse_text_actually_written(m):
    """判读文本必须真的在盘上(旧版只登记 .txt 文件名而不写文件, 记录/比较就没东西可读)。"""
    steps = [{"cmd": "safety_status", "as": "boot", "seconds": 2,
              "expect_contains": ["[SAFETY] ready=YES"]}]
    fx = {"boot_bytes": [], "responses": {"safety_status": SAFETY_OK}}
    r = m.exec_session("s38", stamp(m, steps), out_dir("s38"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1}})
    res = r["results"][0]
    txt = Path(res["txt"])
    raw = Path(str(txt)[: -len(".txt")])
    return (txt.is_file() and "[SAFETY] ready=YES" in txt.read_text(encoding="utf-8") and
            raw.is_file() and raw.stat().st_size > 0,
            "txt=%s bytes=%d raw_exists=%s" % (txt.name, txt.stat().st_size, raw.is_file()))


def s39_evidence_failure_is_named_and_halts(m):
    """证据落不开 => 具名 EVIDENCE_* 终止业务, **下一条命令不发**, 只保留必要清理。"""
    d = out_dir("s39")
    # 执行器把第一条响应的原始字节写成 <会话目录>/01_boot.rx.bin; 先把这个名字占成一个**目录**,
    # open(...,"wb") 就必然 OSError => 走具名的证据失败支(而不是靠改权限之类平台相关技巧)。
    (d / "s39").mkdir()
    (d / "s39" / "01_boot.rx.bin").mkdir()
    steps = [{"cmd": "safety_status", "as": "boot", "seconds": 2,
              "expect_contains": ["[SAFETY] ready=YES"]},
             {"cmd": "motor_start", "as": "must_not_run", "seconds": 2}]
    fx = motion_fx({"safety_status": SAFETY_OK, "motor_start": []})
    r = m.exec_session("s39", stamp(m, steps), d, "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       plan={"quota": {"port_open": 1, "motor_run": 1,
                                       "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                                       "cleanup_motor_status": 1}})
    tx = tx_seq(r)
    return (r["code"] in ("EVIDENCE_OPEN_FAILED", "EVIDENCE_WRITE_FAILED") and
            "motor_start" not in tx and r["success"] is False,
            "code=%s tx=%s" % (r["code"], ",".join(tx)))


def s40_cleanup_reserve_charged_and_disclosed(m):
    """清理三条各有**独立**预留并真实扣减; 预留耗尽仍发送但具名披露(= 该会话不是成功)。"""
    clock = m.SimClock()
    fx = motion_fx({})
    q = {"cleanup_motor_stop": 1, "cleanup_motor_disarm": 1, "cleanup_motor_status": 1}
    used, over = {}, []
    led = m.Ledger(out_dir("s40") / "l.jsonl", clock)
    port = m.FakePort([], led)
    hook = m._stub_feed(port, fx)
    st1, e1 = m.cleanup(port, led, clock, m.MotionBudget(clock, 5, 4), "stub", fx, hook,
                        q, over, used)
    mid, over1 = dict(q), list(over)
    st2, e2 = m.cleanup(port, led, clock, m.MotionBudget(clock, 5, 4), "stub", fx, hook,
                        q, over, used)
    led.close()
    # 会话级: 预留申报为 0 时仍然撤能(安全优先), 但必须具名 CLEANUP_RESERVE_EXHAUSTED 且不是成功
    fx2 = motion_fx({"motor_start": []})
    r = m.exec_session("s40b", stamp(m, [{"cmd": "motor_start", "as": "start", "seconds": 1,
                                "expect_absent": ["REFUSED"]}]), out_dir("s40b"), "stub", fx2,
                       "NO_DEVICE", 30, m.SimClock(), motion_cap_seconds=5,
                       plan={"quota": {"port_open": 1, "motor_run": 1, "retry": 0,
                                        "cleanup_motor_stop": 0, "cleanup_motor_disarm": 0,
                                        "cleanup_motor_status": 0}},
                       # 共享账本(调度器就是这么传的): 清理必须扣**同一本**预留
                       quota_box={"port_open": 1, "motor_run": 1, "retry": 0,
                                  "cleanup_motor_stop": 0, "cleanup_motor_disarm": 0,
                                  "cleanup_motor_status": 0})
    return (st1 == "CONFIRMED_DISABLED" and not over1 and mid == {"cleanup_motor_stop": 0,
                                                                 "cleanup_motor_disarm": 0,
                                                                 "cleanup_motor_status": 0} and
            st2 == "CONFIRMED_DISABLED" and len(over) == 3 and
            all(x.endswith("RESERVE_EXHAUSTED_SENT_ANYWAY") for x in over) and
            r["code"] == "CLEANUP_RESERVE_EXHAUSTED" and r["success"] is False and
            len(r["cleanup_overruns"]) == 3 and "motor_disarm" in tx_seq(r),
            "first=%s after_first=%s second=%s over=%s session=%s/%s tx=%s" % (
                st1, mid, st2, over, r["code"], r["success"], ",".join(tx_seq(r))))


def s41_dispatch_healthy_reaches_commit_and_save(m):
    res, _d = dispatch(m, "healthy")
    ids = [x["id"] for x in res["stages"]]
    s5 = stage(res, "S5")
    asaved = [x["as"] for x in json.loads((Path(s5["dir"]) / "results.json").read_text(
        encoding="utf-8"))] if (Path(s5["dir"]) / "results.json").exists() else None
    ok_save = s5["success"] and res["quota_used"].get("config_save") == 1
    flags = res["recorded"].get("flags") or ""
    ok_flags = (flags == "0005" and
                int(flags, 16) & m.RPF_BITS["COMMITTED"] and
                not int(flags, 16) & m.RPF_BITS["FIELD_VERIFIED"] and
                int(flags, 16) & m.RPF_BITS["FROM_LEARN"])
    return (res["status"] == "OK" and ids == res["expected_stages"] and not res["not_run"] and
            ok_save and ok_flags and
            res["recorded"].get("id") == "7" and
            # 签名不再钉夹具数字: 记录的是本次 show 行的实际值, 且产品自己判定 match=YES
            (res["recorded"].get("sig") or "").isdigit() and
            res["recorded"].get("match") == "YES" and
            all(v >= 0 for v in res["quota_left"].values()),
            "status=%s ids=%s recorded=%s quota_used=%s asaved=%s" % (
                res["status"], ",".join(ids), res["recorded"],
                json.dumps(res["quota_used"], sort_keys=True), asaved))


def s42_dispatch_no_trigger_and_trip_branches(m):
    h, _hd = dispatch(m, "no_trigger")
    t, _td = dispatch(m, "trip")
    nt8 = stage(h, "S8")
    tr8 = stage(t, "S8")
    tr10 = stage(t, "S10")
    tr11 = stage(t, "S11")
    return (h["status"] == "OK" and nt8["status"] == "OBSERVATION_NO_TRIGGER" and
            [x["id"] for x in h["stages"]] == h["expected_stages"] and
            "S9" not in h["expected_stages"] and "S10" not in h["expected_stages"] and
            t["status"] == "OK" and tr8["status"] == "OK" and
            tr10["status"] == "REFUSED" and tr10["success"] is True and
            tr11["success"] is True and int(t["recorded"]["bb_session"]) > 0 and
            int(t["recorded"]["bb_written"]) > 0,
            "nt=%s(%s) trip=%s s10=%s s11=%s bb=%s" % (
                h["status"], nt8["status"], t["status"], tr10["status"], tr11["status"],
                t["recorded"]))


def s43_dispatch_forensics_sessions_never_start(m):
    """取证与恢复会话不得自动起转(C3): 计划申报禁 motor_start, 台账里也必须一条都没有。"""
    t, _td = dispatch(m, "trip")
    tx9 = tx_stage(t, "S9")
    tx11 = tx_stage(t, "S11")
    return ("motor_start" not in tx9 and "motor_arm" not in tx9 and
            "motor_start" not in tx11 and stage(t, "S9")["success"] and
            stage(t, "S11")["success"],
            "s9_tx=%s s11_tx=%s" % (",".join(tx9), ",".join(tx11)))


def s44_dispatch_stops_and_lists_not_run(m):
    """S5 保存判据不成立 => S6 之后一律 NOT_RUN, 不许把没执行的阶段算进 stages_executed。"""
    p = copy.deepcopy(PLAN)
    for st in next(x for x in p["stages"] if x["id"] == "S5")["steps"]:
        if st["as"] == "save":
            st["expect_values"] = [{"row": r"^\[CFG\] save: (?P<r>\w+)$", "must": {"r": "NEVER"}}]
    res, _d = dispatch(m, "healthy", plan=p)
    return (res["status"] == "STOP" and [x["id"] for x in res["stages"]][-1] == "S5" and
            len(res["not_run"]) == len(p["stages"]) - 5 and
            res["stages"][-1]["success"] is False,
            "status=%s executed=%s not_run=%s" % (
                res["status"], ",".join(x["id"] for x in res["stages"]),
                ",".join(res["not_run"])))


def s45_restore_mismatch_is_named(m):
    """重启后 id/rev 漂移 => RESTORE_MISMATCH(而不是"看起来差不多"), 后续阶段停止。"""
    tbl = copy.deepcopy(ROWS)
    tbl["commands"]["diag_profile show@after_reboot"]["healthy"] = \
        [r.replace("id=7 rev=1", "id=7 rev=2")
         for r in tbl["commands"]["diag_profile show@after_reboot"]["healthy"]]
    res, _d = dispatch(m, "healthy", rows=tbl)
    s7 = stage(res, "S7")
    return (res["status"] == "STOP" and s7["code"] == "RESTORE_MISMATCH" and
            "rev" in json.dumps(s7, ensure_ascii=False),
            "status=%s code=%s detail=%s" % (res["status"], s7["code"],
                                             s7.get("detail", ""))[:160])


def s46_new_session_must_actually_increase(m):
    """事故取证: [BB] session 没增大就必须具名停止(不接受只看旧计数)。"""
    tbl = copy.deepcopy(ROWS)
    tbl["commands"]["blackbox_status@after"]["healthy"] = list(
        tbl["commands"]["blackbox_status"]["healthy"])
    res, _d = dispatch(m, "trip", rows=tbl)
    s9 = stage(res, "S9")
    return (res["status"] == "STOP" and s9["code"] == "SESSION_NOT_INCREASED" and
            "S10" in res["not_run"],
            "status=%s code=%s not_run=%s" % (res["status"], s9["code"],
                                              ",".join(res["not_run"])))


def s47_plan_placeholder_and_forbidden_rejected(m):
    """交付包里不许留待手改的占位; 阶段自己申报的禁止命令必须真能拒绝。"""
    p1 = copy.deepcopy(PLAN)
    p1["tools"]["download"]["argv"][1] = "--pack=<CMSIS-Pack>"
    p1["tools"]["readback"]["argv"] = ["python", "-m", "pyocd", "packmem", "-c", "1"]
    try:
        m.s0_binding(p1, REPO)
        a = False
        a_code = "NO_STOP"
    except m.Stop as e:
        a, a_code = e.code == "S0_PLAN_PLACEHOLDER", e.code
    p2 = copy.deepcopy(PLAN)
    next(x for x in p2["stages"] if x["id"] == "S3")["steps"].append(
        {"cmd": "config_load", "as": "banned"})
    try:
        m.s0_binding(p2, REPO)
        b = False
        b_code = "NO_STOP"
    except m.Stop as e:
        b, b_code = e.code == "S0_PLAN_FORBIDDEN_COMMAND_PRESENT", e.code
    return (a and b, "placeholder=%s(%s) forbidden=%s(%s)" % (a, a_code, b, b_code))


def s48_real_transport_without_token_sends_nothing(m):
    a, _da = dispatch(m, m.REAL_PROFILE, transport="real", backend="real")
    b, _db = dispatch(m, "healthy", transport="real", backend="real")
    return (a["status"] == "REFUSED" and a["stages"] == [] and
            len(a["not_run"]) == len(PLAN["stages"]) and
            a["code"] == "REAL_TRANSPORT_REFUSED" and
            b["status"] == "REFUSED" and b["code"] == "REAL_PROFILE_NOT_RESPONSE_DRIVEN" and
            b["actions_attempted"] == [],
            "token=%s(%d) profile=%s(%d)" % (
                a.get("code", ""), len(a["stages"]), b.get("code", ""), len(b["stages"])))


def s49_real_argv_tokens_refuse_to_run(m):
    """real 后端: 缺 UID 或残留占位都必须**在启动工具之前**具名停止。"""
    d = out_dir("s49")
    led = m.Ledger(d / "l.jsonl", m.SimClock())
    be = m.RealActionBackend(led, d, m.SimClock(), 5, {})
    codes = []
    for argv in [["python", "-m", "pyocd", "reset", "-u", "@UID_FILE"],
                 ["python", "-m", "pyocd", "reset", "-u", "@LEFTOVER"]]:
        try:
            be._run("probe", {"as": "p"}, argv)
            codes.append("SUBPROC_RAN")
        except m.Stop as e:
            codes.append(e.code)
        except Exception as e:                                       # noqa: BLE001
            codes.append("UNEXPECTED_" + type(e).__name__)
    led.close()
    return (codes == ["UID_FILE_REQUIRED", "TOOL_ARGV_UNRESOLVED"], ",".join(codes))


def s50_learn_timeout_keeps_named_stop(m):
    """learn 撞上产品自己的 12 s 超时(> 5 s 硬截止)时: 后续业务步一律不发, 清理仍走, 不是成功。
    这条把 residual_risk 变成可判定判据, 不是注释。"""
    fx = motion_fx({"motor_start": [],
                    "__watch__": ROWS["commands"]["__watch__"]["healthy"],
                    "diag_profile learn 8 80 120 0":
                        ROWS["commands"]["diag_profile learn 8 80 120 0"]["timeout_12s"]})
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 1, "expect_absent": ["REFUSED"]},
             {"cmd": "diag_profile learn 8 80 120 0", "as": "learn", "seconds": 3,
              "expect_contains": ["[DIAG] learn OK"]},
             {"cmd": "diag_profile tier 2", "as": "must_not_run"}]
    r = m.exec_session("s50", stamp(m, steps), out_dir("s50"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5,
                       plan={"quota": {"port_open": 1, "motor_run": 1, "retry": 0,
                                       "diag_profile_learn": 1,
                                       "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                                       "cleanup_motor_status": 1}})
    tx = tx_seq(r)
    return ("diag_profile tier 2" not in tx and "motor_stop" in tx and "motor_disarm" in tx and
            r["success"] is False and r["code"] == "BUSINESS_FAILED",
            "code=%s tx=%s disarm=%s" % (r["code"], ",".join(tx), r["disarm"]))


def s51_negative_cleanup_single_window(m):
    """定向检出 4: 清理退回**一个共享截止**(R2 交付版) => s28 必须不再成立。"""
    mod = _mutant("RV11", [("        dl = min(hard, clock.monotonic() + CLEANUP_WINDOW_SECONDS)",
                           "        dl = hard   # NEGATIVE-INJECTION RV11: 三条命令抢同一个截止")])
    ok, msg = s28_stop_silent_does_not_eat_disarm(mod)
    return (not ok, "共享截止下 s28 仍然通过 => 判据不足: " + msg)


def s52_negative_idle_only_disable(m):
    """定向检出 5: 禁能判据退回**只认 IDLE** => s30 必须不再成立(产品从不打印 IDLE)。"""
    mod = _mutant("RV12", [('    if name not in DISABLE_ALLOWED_STATES:\n'
                           '        bad.append("state_name=%s" % name)',
                           '    if name != "IDLE":   # NEGATIVE-INJECTION RV12: 又要求 IDLE\n'
                           '        bad.append("state_name=%s" % name)')])
    ok, msg = s30_real_disable_rows_confirm(mod)
    return (not ok, "IDLE-only 判据下 s30 仍然通过 => 判据不足: " + msg)


def s53_negative_obligation_after_write(m):
    """定向检出 6: 义务退回**写成功之后**才建立 => s26 必须不再成立(短写就跳过撤能)。"""
    mod = _mutant("RV13", [('                obligation = True\n                if cmd == "motor_start":',
                           '                # NEGATIVE-INJECTION RV13: 义务推到写成功之后\n'
                           '                if cmd == "motor_start":')])
    ok, msg = s26_write_failure_still_disarms(mod)
    return (not ok, "写后才建义务时 s26 仍然通过 => 判据不足: " + msg)


def s54_negative_profile_check_removed(m):
    """定向检出 7: 去掉 expect_profile 的一致性判据 => s36 的失败支必须变成通过(即判据消失)。"""
    mod = _mutant("RV14", [('    if st.get("expect_profile") == "AT_REST":',
                            '    if False:   # NEGATIVE-INJECTION RV14: 静止/运行一致性判据被摘掉')])
    ok, msg = s36_running_and_rest_profiles_both_directions(mod)
    return (not ok, "摘掉一致性判据后 s36 仍然通过 => 判据不足: " + msg)


# ==================================================== R3 收尾(§2/§3/§4)的确定反例与健康对照
# 下面每一条都跑**同一条交付执行器**, 只替换纯内存端口/动作后端/subprocess; 不导入 serial,
# 不碰设备。审核方给的 remaining_field_probes.py 断言的是修前行为, 这里断言修后行为。

def _halted_port_factory(m, state, clock, boot_after_reset=True):
    """回读之后核是 Halted 的内存端口: 复位之前**任何**写都得不到应答; 复位之后才把启动流交出来。"""
    class Port(m.FakePort):
        def write(self, data):
            n = m.FakePort.write(self, data)
            cmd = data.decode("ascii").strip()
            if not state["running"]:
                return n                        # 核心没跑: 一个字都不回(不是"观测缺失")
            per = ROWS["commands"].get(cmd)
            self.feed(ok_rows(per["healthy"] if per else []))
            return n

        def read(self, nn):
            if state["running"] and boot_after_reset and not state.get("booted"):
                state["booted"] = True
                self.feed(b"msh >")
            return m.FakePort.read(self, nn)
    return Port


def s56_halted_readback_reset_before_first_query(m):
    """审核 §2A: S2 的定长回读用 connect_mode=halt + resume_on_disconnect=false => 会话开头核是
    Halted。计划必须是 开口 -> 唯一一次已批准复位 -> 消费启动流 -> 首个合法查询。"""
    stg = copy.deepcopy(next(s for s in PLAN["stages"] if s["id"] == "S3"))
    rst, query = stg["steps"][0], stg["steps"][1]
    assert rst.get("action") == "reset" and query.get("cmd") == "system_status", \
        "计划 S3 的前两步不再满足 复位->查询 的顺序"

    def run(steps, boot=True):
        state = {"running": False, "resets": 0}
        clock = m.SimClock()

        class Backend:
            kind = "memory_reset"
            attempts = []

            def reset(self, act, ctx):
                state["running"] = True
                state["resets"] += 1
                return {"identity_gate": "MODEL_ONLY"}

        saved = m.make_real_port
        m.make_real_port = lambda port_name, led: _halted_port_factory(m, state, clock, boot)(
            [], led, clock=clock)
        try:
            r = m.exec_session("s56", stamp(m, steps), out_dir("s56"), "real",
                               {"post_reset_boot": stg["post_reset_boot"]}, "MEMORY", 40, clock,
                               backend=Backend(), plan={"quota": {"port_open": 1,
                                                                  "explicit_reset": 1}}, approved=fx_approved(m, stamp(m, steps), "s56"))
        finally:
            m.make_real_port = saved
        return r, state

    good, st_good = run([rst, query])
    reversed_steps, st_rev = run([query, rst])
    no_boot, st_nb = run([rst, query], boot=False)
    kinds = [x.get("kind") for x in good["results"]]
    return (good["status"] == "OK" and st_good["resets"] == 1 and "boot_stream" in kinds and
            reversed_steps["code"] == "MISSING_RESPONSE_PROMPT" and st_rev["resets"] == 0 and
            [x["as"] for x in reversed_steps["results"]] == [] and
            no_boot["status"] == "OK" and st_nb["resets"] == 1 and
            len([r for r in no_boot["results"] if r.get("cmd")=="system_status"]) == 1,
            "good=%s resets=%d kinds=%s | reversed=%s resets=%d results=%d | no_boot=%s" % (
                good["status"], st_good["resets"], ",".join(kinds), reversed_steps["code"],
                st_rev["resets"], len(reversed_steps["results"]), no_boot["code"]))


def _real_backend_with_fake_subprocess(m, tag, stdout_map, rc=0):
    """只替换 subprocess.run 与交付文件, 其余按**原 RealActionBackend** 执行(审核 §2A)。"""
    d = out_dir(tag)
    led = m.Ledger(d / "l.jsonl", m.SimClock())
    calls = []

    class Completed:
        def __init__(self, out, err):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(argv, **kw):
        calls.append({"argv": list(argv), "cwd": kw.get("cwd")})
        key = "readback" if "commander" in argv else ("download" if "flash" in argv else "reset")
        return Completed(stdout_map.get(key, b""), stdout_map.get(key + "_err", b""))

    return d, led, calls, fake_run


def s57_real_backend_zero_device_injection(m):
    """RealActionBackend 的下载原文门 / 定长比较 / 复位处理, 用**既存 P0 日志**做输入,
    不执行 pyocd, 不拿 SimActionBackend 的 rc0 冒充这条路已测。"""
    flash_err = (REPO / "reports/reviews/phase8-motor-m7-p0-board/S1_flash.stderr.txt").read_bytes()
    rb_out = (REPO / "reports/reviews/phase8-motor-m7-p0-board/S2_readback.stdout.txt").read_bytes()
    image_rel = PLAN["tools"]["readback"]["image"]["path"]
    image_bytes = (REPO / image_rel).read_bytes()
    d, led, calls, fake_run = _real_backend_with_fake_subprocess(
        m, "s57", {"download_err": flash_err, "readback": rb_out})
    (d / "readback.bin").write_bytes(image_bytes)
    subs = {"@UID_FILE": "U" * 48, "@RUN_ROOT": str(d), "@IMAGE_BYTES": "0x%X" % len(image_bytes)}
    saved = m.subprocess.run
    m.subprocess.run = fake_run
    codes, good = [], {}
    try:
        be = m.RealActionBackend(led, d, m.SimClock(), 30, subs, repo_root=str(REPO))
        act_rb = copy.deepcopy(PLAN["tools"]["readback"])
        act_rb["run_root"] = str(d)
        good["download"] = be.download(copy.deepcopy(PLAN["tools"]["download"]), {})
        good["readback"] = be.readback(act_rb, {})
        good["reset"] = be.reset(copy.deepcopy(PLAN["tools"]["reset"]), {})
        # 负控: 非零 rc / 成功原文缺一行 / 交付件短一字节 / 交付件改一字节
        bad = m.RealActionBackend(led, d, m.SimClock(), 30, subs, repo_root=str(REPO))
        m.subprocess.run = lambda argv, **kw: type("C", (), {"returncode": 1, "stdout": b"",
                                                             "stderr": b""})()
        try:
            bad.download(copy.deepcopy(PLAN["tools"]["download"]), {})
        except m.Stop as e:
            codes.append(e.code)
        m.subprocess.run = fake_run
        cut = copy.deepcopy(PLAN["tools"]["download"])
        cut["expect_all"] = list(cut["expect_all"]) + ["NEVER_PRINTED_MARKER"]
        try:
            bad.download(cut, {})
        except m.Stop as e:
            codes.append(e.code)
        short = copy.deepcopy(act_rb)
        (d / "readback.bin").write_bytes(image_bytes[:-1])
        try:
            bad.readback(short, {})
        except m.Stop as e:
            codes.append(e.code)
        # 末字节 XOR: 保证一定与镜像不同(直接补 \x00 会撞上"镜像末字节本来就是 0")
        flipped = image_bytes[:-1] + bytes([image_bytes[-1] ^ 0xFF])
        (d / "readback.bin").write_bytes(flipped)
        try:
            bad.readback(short, {})
        except m.Stop as e:
            codes.append(e.code)
        # 第三个门单独核: 尺寸与声明散列都**自洽**, 但交付件与镜像文件不是同一份字节
        eq = copy.deepcopy(act_rb)
        eq["image"] = dict(eq["image"], bytes=len(flipped),
                           sha256=m.sha_bytes(flipped))
        try:
            bad.readback(eq, {})
        except m.Stop as e:
            codes.append(e.code)
        (d / "readback.bin").write_bytes(image_bytes)
    finally:
        m.subprocess.run = saved
        led.close()
    dl_argv = calls[0]["argv"]
    axf = [a for a in dl_argv if a.endswith(".axf")]
    return (all(k in good for k in ("download", "readback", "reset")) and
            good["download"]["identity_gate"] == "REAL_TOOL_RC0_AND_SUCCESS_TEXT" and
            good["readback"]["identity_gate"] == "REAL_BYTES_HASH_AND_FILE_EQUAL" and
            good["readback"]["out_file"]["bytes"] == len(image_bytes) and
            calls[0]["cwd"] == str(REPO) and len(axf) == 1 and str(axf[0]).startswith(str(REPO)) and
            codes == ["ACTION_NONZERO_RC", "ACTION_SUCCESS_TEXT_MISSING", "READBACK_BYTE_COUNT",
                      "READBACK_HASH_MISMATCH", "READBACK_NOT_BYTE_EQUAL_TO_IMAGE"],
            "gates=%s cwd=%s axf=%s codes=%s" % (
                [good[k]["identity_gate"] for k in ("download", "readback")],
                calls[0]["cwd"], axf, ",".join(codes)))


def s58_uid_stays_out_of_public_evidence(m):
    """审核 §2C 最后一条: 展开后的 UID 不许进 ARGV.json, 也不许进台账; 公开件只登记
    脱敏副本 + **原 argv 的散列** + 长度/sha12 披露。"""
    flash_err = (REPO / "reports/reviews/phase8-motor-m7-p0-board/S1_flash.stderr.txt").read_bytes()
    d, led, calls, fake_run = _real_backend_with_fake_subprocess(m, "s58", {"download_err": flash_err})
    secret = "0684615234575061" + "0" * 32          # 48 字符形状的私有件(本轮虚构值, 不是板子的)
    saved = m.subprocess.run
    m.subprocess.run = fake_run
    try:
        be = m.RealActionBackend(led, d, m.SimClock(), 30, {"@UID_FILE": secret},
                                 repo_root=str(REPO))
        be.download(copy.deepcopy(PLAN["tools"]["download"]), {})
    finally:
        m.subprocess.run = saved
        led.close()
    argv_txt = (d / "action_download" / "ARGV.json").read_text(encoding="utf-8")
    led_txt = (d / "l.jsonl").read_text(encoding="utf-8")
    rec = json.loads(argv_txt)
    real = calls[0]["argv"]
    return (secret not in argv_txt and secret not in led_txt and secret in real and
            rec["uid_disclosure"]["length"] == 48 and
            rec["uid_disclosure"]["sha256_12"] == __import__("hashlib").sha256(
                secret.encode()).hexdigest()[:12] and
            len(rec["argv_sha256_of_private_original"]) == 64 and
            any("@UID_FILE<redacted" in a for a in rec["argv_masked"]) and
            all(secret not in a for a in rec["argv_masked"]),
            "leak_argv=%s leak_ledger=%s masked=%s disclosure=%s" % (
                secret in argv_txt, secret in led_txt,
                sum(1 for a in rec["argv_masked"] if "@UID_FILE<redacted" in a),
                rec["uid_disclosure"]))


def s59_stop_read_exception_still_disarms(m):
    """审核 §2B: stop 的**读异常**不能阻止唯一必要的 disarm; 主错与清理错分列; 外层 finally 仍关端口。"""
    state = {"last": "", "closed": [None]}

    class Port(m.FakePort):
        def write(self, data):
            state["last"] = data.decode("ascii").strip()
            n = m.FakePort.write(self, data)
            if state["last"] == "motor_status":
                self.feed(ok_rows(DISARM_ROWS))
            elif state["last"] in ("motor_stop", "motor_disarm"):
                self.feed(ok_rows(STOP_OK if state["last"] == "motor_stop" else DISARM_OK))
            return n

        def read(self, nn):
            if state["last"] == "motor_stop":
                raise OSError("injected stop-response read transport failure")
            return m.FakePort.read(self, nn)

        def close(self):
            state["closed"][0] = True
            m.FakePort.close(self)

    clock = m.SimClock()
    saved = m.make_real_port
    m.make_real_port = lambda port_name, led: Port([], led, clock=clock)
    try:
        r = m.exec_session("s59", stamp(m, [{"cmd": "motor_start", "as": "start", "seconds": 2,
                                    "expect_absent": ["REFUSED"]}]), out_dir("s59"), "real", {},
                           "MEMORY", 30, clock, motion_cap_seconds=5,
                           plan={"quota": dict(BASE_QUOTA)}, approved=fx_approved(m, stamp(m, [{"cmd": "motor_start", "as": "start", "seconds": 2,
                                    "expect_absent": ["REFUSED"]}]), "s59"))
    except Exception as e:                                     # noqa: BLE001
        r = {"escaped": type(e).__name__}
    finally:
        m.make_real_port = saved
    tx = tx_seq(r)
    return ("escaped" not in r and "motor_disarm" in tx and "motor_status" in tx and
            r["obligation"] is True and
            any(e["kind"] == "PORT_CLOSED" for e in _ledger_events(r["ledger"])) and
            any("READ_EXCEPTION" in x for x in r["cleanup_errors"]) and
            r["code"] and r["success"] is False,
            "tx=%s disarm=%s terminal=%s cleanup_errs=%s code=%s" % (
                ",".join(tx), r["disarm"], r["terminal"], r["cleanup_errors"], r["code"]))


def s60_short_write_is_not_success(m):
    """审核 §2B 第 2 条: 业务与清理的写都要核实**实际写入字节数**; 短写不是成功,
    而已建立的起转义务继续成立(照样走撤能)。"""
    n_written = []

    class Port(m.FakePort):
        def write(self, data):
            short = bytes(data[:-1])                 # 少写一个字节(到 \n 之前)
            n = m.FakePort.write(self, short)
            n_written.append((data.decode().strip(), n, len(data)))
            return n - 1 if n > 1 else n

    clock = m.SimClock()
    saved = m.make_real_port
    m.make_real_port = lambda port_name, led: Port([], led, clock=clock)
    try:
        r = m.exec_session("s60", stamp(m, [{"cmd": "motor_start", "as": "start", "seconds": 2}]),
                           out_dir("s60"), "real", {}, "MEMORY", 30, clock,
                           motion_cap_seconds=5, plan={"quota": dict(BASE_QUOTA)}, approved=fx_approved(m, stamp(m, [{"cmd": "motor_start", "as": "start", "seconds": 2}]), "s60"))
    finally:
        m.make_real_port = saved
    return (r["code"] == "COMMAND_SHORT_WRITE" and r["obligation"] is True and
            r["disarm"] in ("CONFIRMED_DISABLED", "UNKNOWN") and r["success"] is False,
            "code=%s obligation=%s disarm=%s writes=%s" % (
                r["code"], r["obligation"], r["disarm"], n_written[:2]))


def s61_open_quota_charged_before_attempt(m):
    """审核 §2B 第 4 条: 开口额度在**尝试开口之前**消费。零额度 => 一次尝试都没有;
    开口抛错 => 额度已扣且尝试留痕, 不许漏账。"""
    attempts = []
    clock = m.SimClock()

    def boom(port_name, led):
        attempts.append(port_name)
        raise OSError(2, "injected open failure")

    saved = m.make_real_port
    m.make_real_port = boom
    zero, threw = None, None
    try:
        zero = m.exec_session("s61z", stamp(m, [{"cmd": "system_status", "as": "q"}]), out_dir("s61z"),
                              "real", {}, "COM9", 20, clock,
                              plan={"quota": {"port_open": 0}}, approved=fx_approved(m, stamp(m, [{"cmd": "system_status", "as": "q"}]), "s61z"))
        zero_attempts = len(attempts)
        q = dict(BASE_QUOTA)
        q["port_open"] = 2
        threw = m.exec_session("s61t", stamp(m, [{"cmd": "system_status", "as": "q"}]), out_dir("s61t"),
                               "real", {}, "COM9", 20, m.SimClock(), plan={"quota": q}, approved=fx_approved(m, stamp(m, [{"cmd": "system_status", "as": "q"}]), "s61t"))
    finally:
        m.make_real_port = saved
    return (zero["code"] == "QUOTA_EXHAUSTED" and zero_attempts == 0 and
            zero["quota_used"].get("port_open", 0) == 0 and
            threw["code"] == "PORT_OPEN_FAILED" and len(attempts) == 1 and
            threw["quota_used"]["port_open"] == 1,
            "zero=%s used=%s attempts_after_zero=%d threw=%s attempts=%d used_after=%s" % (
                zero["code"], zero["quota_used"], zero_attempts, threw["code"], len(attempts),
                threw["quota_used"].get("port_open")))


def s62_expected_refusal_never_waives_cleanup_failure(m):
    """审核 §2B 第 3 条: "这阶段就是演示 arm 拒绝"只豁免**业务结果**; 清理 UNKNOWN/清理错/
    预留耗尽/证据失败一律仍然非成功。健康支仍然作为预期结果接受。"""
    broken = copy.deepcopy(ROWS)
    broken["commands"]["motor_status"]["healthy"] = []      # 清理那一份新响应缺字段
    only10 = copy.deepcopy(PLAN)
    only10["stages"] = [s for s in only10["stages"] if s["id"] == "S10"]
    ok_res, _od = dispatch(m, "trip", plan=only10)
    bad_res, _bd = dispatch(m, "trip", rows=broken, plan=copy.deepcopy(only10))
    s10_ok = stage(ok_res, "S10")
    s10_bad = next((s for s in bad_res["stages"] if s["id"] == "S10"), None)
    return (s10_ok.get("success") is True and s10_ok.get("expected_refusal") == "REFUSED" and
            s10_bad is not None and s10_bad["success"] is False and
            s10_bad["terminal"] == "UNKNOWN" and bool(s10_bad["cleanup_errors"]) and
            bad_res["status"] != "OK",
            "healthy_s10=%s/%s broken_s10=%s/%s errs=%d dispatch=%s" % (
                s10_ok.get("success"), s10_ok.get("expected_refusal"), s10_bad["success"],
                s10_bad["terminal"], len(s10_bad["cleanup_errors"]), bad_res["status"]))


def s63_mode_combinations_refused_before_any_action(m):
    """审核 §2C: 离线只允许 stub+sim; 真实允许 real+real / real+manual。混合模式在任何
    UID 读取/工具/端口动作之前具名拒绝, 且**副作用清单为空**。"""
    out = []
    for t, b in (("real", "sim"), ("stub", "real"), ("stub", "manual")):
        res, _d = dispatch(m, "healthy" if t == "stub" else m.REAL_PROFILE,
                           transport=t, backend=b, token="X", expected="X")
        out.append((t + "+" + b, res["status"], res.get("code"), res["stages"],
                    res["actions_attempted"]))
    allowed = [dispatch(m, "healthy", transport="stub", backend="sim")[0]["status"],
               dispatch(m, m.REAL_PROFILE, transport="real", backend="manual",
                        token="X", expected="X")[0]]
    bad = [x for x in out if not (x[1] == "REFUSED" and x[2] == "MODE_COMBINATION_REFUSED"
                                  and x[3] == [] and x[4] == [])]
    # R6 §7.4 新门: 真实传输还要求一本**绑定批准身份的持久额度账**。它同样在 UID/工具/端口之前,
    # 所以"允许的组合"这一刻仍是零副作用; 账本缺失本身由 s88 专门检。
    rman = allowed[1]
    return (not bad and allowed[0] in ("OK", "STOP") and
            rman.get("code") in ("MODE_COMBINATION_REFUSED", "TOOL_ARGV_UNRESOLVED",
                                 "UID_FILE_REQUIRED", None, "MANUAL_GATE_DENIED",
                                 "ACTION_DELIVERABLES_MISSING", "APPROVAL_ID_REQUIRED") and
            rman.get("stages") == [] and rman.get("actions_attempted") == [],
            "rejected=%s allowed_stub=%s real_manual=%s stages=%d attempted=%d" % (
                [x[0] for x in out], allowed[0], rman.get("code"),
                len(rman.get("stages", [])), len(rman.get("actions_attempted", []))))


def s64_signature_not_hardcoded_but_drift_still_refused(m):
    """审核 §2D 第 1 条: sig 是产品函数算的, 不是用户输入值。把**所有**出现一致改掉 => 仍通过;
    只改重启后那一处(真漂移) => 仍然 RESTORE_MISMATCH。"""
    consistent = json.loads(json.dumps(ROWS).replace("2138438894", "2138438899"))
    ok_res, _d = dispatch(m, "healthy", rows=consistent)
    drift = copy.deepcopy(ROWS)
    rb = drift["commands"]["diag_profile show@after_reboot"]["healthy"]
    rb[:] = [x.replace("2138438894", "2999999999") for x in rb]
    bad_res, _bd = dispatch(m, "healthy", rows=drift)
    s7 = next((s for s in bad_res["stages"] if s["id"] == "S7"), {"code": "S7_NOT_REACHED"})
    return (ok_res["status"] == "OK" and ok_res["recorded"].get("sig") == "2138438899" and
            s7.get("code") == "RESTORE_MISMATCH" and bad_res["status"] != "OK",
            "consistent=%s sig=%s drift_s7=%s" % (
                ok_res["status"], ok_res["recorded"].get("sig"), s7.get("code")))


def s65_flags_by_bit_reject_illegal_commit(m):
    """审核 §2D 第 2 条: flags=0010 连 COMMITTED 位都没有, 不再因为"两处都印 0010"被接受。"""
    bad_rows = json.loads(json.dumps(ROWS).replace("flags=0005", "flags=0010")
                          .replace("flg=0005", "flg=0010"))
    res, _d = dispatch(m, "healthy", rows=bad_rows)
    s5 = next((s for s in res["stages"] if s["id"] == "S5"), {"code": "S5_NOT_REACHED",
                                                               "detail": ""})
    led = _ledger_events(str(Path(s5.get("dir", WORK)) / "ledger.jsonl")) if s5.get("dir") else []
    bits = [e for e in led if e["kind"] == "FLAG_BITS"]
    good = copy.deepcopy(PLAN)
    step = next(x for x in next(s for s in good["stages"] if s["id"] == "S5")["steps"]
                if x.get("as") == "commit")
    step["expect_flag_bits"] = dict(step["expect_flag_bits"], must_clear=["COMMITTED"])
    inv, _id = dispatch(m, "healthy", plan=good)
    return (s5.get("code") == "PROFILE_FLAGS_NOT_QUALIFIED" and res["status"] != "OK" and
            any("缺位COMMITTED" in json.dumps(b, ensure_ascii=False) for b in bits) and
            inv["status"] != "OK",
            "s5=%s bits=%s inverted_expect=%s" % (
                s5.get("code"), [b.get("value") for b in bits], inv["status"]))


def s66_running_needs_numeric_cruise_and_bounded_polling(m):
    """审核 §2D 第 3 条: 起转后有限轮询证得**数值** state=2; 一直在 ACCEL => 到期撤能;
    矛盾字段(armed=1 而 state=0/cur=0/request=LOW) => 立刻拒绝, 不许当成"已运行"。"""
    accel_rows = ROWS["commands"]["motor_status@accel"]["healthy"]
    contradiction = [r.replace("armed=1", "armed=1").replace("state=2 name=OTHER",
                                                              "state=0 name=OTHER")
                     .replace("cur_hz=100 tgt_hz=100", "cur_hz=0 tgt_hz=100")
                     .replace("drv_request=HIGH pad=1", "drv_request=LOW pad=0")
                     for r in RUNNING_ROWS]

    def session(rows_seq, cap, poll=True):
        fx = motion_fx({"motor_start": [], "motor_status": DISARM_ROWS})
        fx["sequence"] = {"motor_status": rows_seq}     # 只有轮询读走序列, 清理读走撤能块
        steps = [{"cmd": "motor_start", "as": "start", "seconds": 2, "expect_absent": ["REFUSED"]}]
        st = {"cmd": "motor_status", "as": "running_proof", "expect_profile": "RUNNING", "seconds": 2}
        if poll:
            st["poll"] = {"until_seconds": 3, "interval_seconds": 0.5,
                          "retry_codes": ["PROFILE_NOT_RUNNING", "FIELD_VALUE_MISMATCH"]}
        steps.append(st)
        return m.exec_session("s66", stamp(m, steps), out_dir("s66"), "stub", fx, "NO_DEVICE", 60,
                              m.SimClock(), motion_cap_seconds=cap,
                              plan={"quota": dict(BASE_QUOTA)})

    up = session([accel_rows, RUNNING_ROWS], 15)
    stuck = session([accel_rows, accel_rows, accel_rows, accel_rows, accel_rows], 15)
    contra = session([contradiction], 15)
    single = session([accel_rows], 15, poll=False)
    up_attempts = max(x.get("attempt", 0) for x in up["results"] if x.get("as") == "running_proof")
    return (up["status"] == "OK" and up_attempts == 2 and
            stuck["code"] == "POLL_WINDOW_EXPIRED" and stuck["success"] is False and
            stuck["disarm"] == "CONFIRMED_DISABLED" and
            contra["code"] == "PROFILE_NOT_RUNNING" and
            len([x for x in contra["results"] if x.get("as") == "running_proof"]) == 1 and
            single["code"] == "PROFILE_NOT_RUNNING",
            "up=%s attempts=%d stuck=%s disarm=%s contra=%s single=%s" % (
                up["status"], up_attempts, stuck["code"], stuck["disarm"], contra["code"],
                single["code"]))


def s67_busy_msh_twelve_second_boundary(m):
    """审核 §3: 用真实 C 的 12000 ms 结果驱动已有 FakePort 的忙 msh 模型 —— 不是注入
    "立刻送达的一行 12s timeout 文本"。学习支(15 s 上限)能等到; 保护支(5 s 上限)等不到,
    忙期里 stop/disarm 只能排队, 物理停不下来就记 UNKNOWN。"""
    learn_rows = ROWS["commands"]["diag_profile learn 8 80 120 0"]["healthy"]

    def run(cap, window):
        fx = motion_fx({"motor_start": [], "diag_profile learn 8 80 120 0": learn_rows})
        fx["block"] = {"diag_profile learn 8 80 120 0": 12.0}
        steps = [{"cmd": "motor_start", "as": "start", "seconds": 2, "expect_absent": ["REFUSED"]},
                 {"cmd": "diag_profile learn 8 80 120 0", "as": "learn", "seconds": window,
                  "expect_contains": ["[DIAG] learn OK"]}]
        return m.exec_session("s67", stamp(m, steps), out_dir("s67"), "stub", fx, "NO_DEVICE", 60,
                              m.SimClock(), motion_cap_seconds=cap, plan={"quota": dict(BASE_QUOTA)})

    learns = run(15, 14)
    tight = run(5, 3)
    busy_ev = [e for e in _ledger_events(learns["ledger"]) if e["kind"] == "PORT_BUSY_UNTIL"]
    tx_busy = [e for e in _ledger_events(tight["ledger"]) if e["kind"] == "TX"]
    stop_t = next((e["t_s"] for e in tx_busy if e["bytes"] == "motor_stop\r\n"), None)
    until = busy_ev[0]["until"] if busy_ev else None
    return (learns["status"] == "OK" and until is not None and
            float(learns["budget_seconds"]) >= until and
            busy_ev[0]["block_seconds"] == 12.0 and
            tight["code"] == "MISSING_RESPONSE_PROMPT" and tight["disarm"] == "UNKNOWN" and
            tight["terminal"] == "UNKNOWN" and tight["success"] is False and
            stop_t is not None and stop_t < until,
            "learns=%s elapsed=%s busy_until=%s | tight=%s disarm=%s stop@%s" % (
                learns["status"], learns["budget_seconds"], until, tight["code"],
                tight["disarm"], stop_t))


def s68_response_driven_branch_and_undetermined_stop(m):
    """审核 §2C: 真实入口不能预选夹具档案。分支由本次响应决定; 还没决定就遇到依赖分支的阶段
    => 具名停止, 绝不默默跳过事故取证。"""
    b_none = m.response_branch({"status": "OBSERVATION_NO_TRIGGER", "results": []},
                               {"decides_response_branch": True})
    b_trip = m.response_branch({"status": "OK", "results": [
        {"kind": "watch", "trigger_seen": ["[DIAG] REL TRIP r="]}]}, {"decides_response_branch": True})
    b_ignore = m.response_branch({"status": "OBSERVATION_NO_TRIGGER", "results": []}, {})
    late = copy.deepcopy(PLAN)
    late["stages"] = [s for s in late["stages"] if s["id"] in ("S0", "S8", "S9")]
    order = late["stages"]
    order[1], order[2] = order[2], order[1]           # 把 trip-only 阶段搬到分支决定点之前
    calls = []

    def no_tools(self, name, act, argv):
        calls.append(name)
        raise AssertionError("tool attempted")

    saved_tool, saved_port = m.RealActionBackend._run, m.make_real_port
    m.RealActionBackend._run = no_tools
    m.make_real_port = lambda port_name, led: m.FakePort([], led, clock=m.SimClock())
    aroot, aid, appath = bind_approval(m, late, "S68-BOUND")
    try:
        res = m.dispatch_plan(late, "real", "real", out_dir("s68-real"), m.SimClock(),
                              m.REAL_PROFILE, "MEMORY", str(REPO), "APPR", "APPR", "",
                              plan_dir=str(HERE), approval_id=aid, approval_root=aroot,
                              plan_path=appath)
    finally:
        m.RealActionBackend._run, m.make_real_port = saved_tool, saved_port
    codes = [s.get("code") for s in res["stages"]]
    return (b_none == "no_trigger" and b_trip == "trip" and b_ignore is None and
            "RESPONSE_BRANCH_UNDETERMINED" in codes and res["status"] != "OK" and
            calls == [] and res["actions_attempted"] == [],
            "none=%s trip=%s ignore=%s codes=%s tool_calls=%d attempted=%s" % (
                b_none, b_trip, b_ignore, ",".join([c or "" for c in codes]), len(calls),
                res["actions_attempted"]))


def _flash_head_idx(m):
    return {n: i for i, n in enumerate(m.FLASH_DUMP_HEADER.split(","))}


def _flash_edit(m, head_idx, g, keep=None, set_col=None, append=()):
    """按**表头列名**改夹具帧。旧版这里用 `all(x.isdigit())` 筛行, 将含负数列
    (ax_mg=-23 等)的真实行整行丢掉, 于是 keep=… != "9" 这类突变其实是在"删掉全部帧",
    四种突变退化成同一种。C-02 要求四路对照各自成立, 所以数字判定必须允许负号。"""
    rows_ = [g[0]]
    for line in g[1:]:
        p = line.split(",")
        if len(p) != m.FLASH_DUMP_COLUMNS or not all(x.lstrip("-").isdigit() for x in p):
            continue
        if keep and not keep(p):
            continue
        for name, val in (set_col or {}).items():
            p[head_idx[name]] = str(val)
        rows_.append(",".join(p))
    return rows_ + list(append)


def _flash_mutator(m):
    def mutate(fn):
        rows = copy.deepcopy(ROWS)
        fn(rows["commands"]["flash_dump 0 8"])
        return rows
    return mutate


def s69_new_session_without_frames_is_not_forensics(m):
    """审核 §2D 第 4 条 + C-02: 停后帧的事件码是 FAULT_MULTI_SOURCE=5(A/safety_state.h:46)。
    五路变体都必须以**各自的原因**被拒, 只看"这阶段没成功"不够 —— 空帧、错事件码、错 session、
    缺停后帧、槽读错是五种不同缺陷, 混成一个就无法证明判据分得开它们。"""
    hi, mutate = _flash_head_idx(m), _flash_mutator(m)

    def go(**kw):
        return dispatch(m, "trip", rows=mutate(
            lambda g: g.__setitem__("trip", _flash_edit(m, hi, g["trip"], **kw))))[0]

    variants = {"no_frames": go(keep=lambda p: False),
                "wrong_session": go(set_col={"session": 7}),
                "only_event9": go(set_col={"event": 9}),
                "no_post_frame": go(set_col={"step_hz": 100}),
                "slot_error": dispatch(m, "trip", rows=mutate(
                    lambda g: g.__setitem__("trip", _flash_edit(m, hi, g["trip"])
                                            + ["slot 9 error -5"])))[0]}
    control, _d = dispatch(m, "trip")
    want_reason = {"no_frames": "CSV_FRAMES=0", "wrong_session": "SESSION_FRAMES=0",
                   "only_event9": "NO_FRAME_WITH", "no_post_frame": "NO_FRAME_WITH",
                   "slot_error": "SLOT_READ_ERRORS"}
    out, reason_ok = {}, {}
    for tag, res in list(variants.items()) + [("control", control)]:
        s9 = next((s for s in res["stages"] if s["id"] == "S9"), {"status": "ABSENT"})
        out[tag] = (s9.get("code"), s9.get("success"), s9.get("status"))
        if tag != "control":
            reason_ok[tag] = want_reason[tag] in str(s9.get("detail", ""))
    return (out["control"][2] == "OK" and out["control"][1] is True and
            all(out[t][0] == "FLASH_FRAMES_NOT_RECOVERED" and out[t][1] is not True
                for t in want_reason) and all(reason_ok.values()),
            " | ".join(["control=%s" % (out["control"],)] +
                        ["%s=%s reason=%s" % (t, out[t][:2], reason_ok[t]) for t in want_reason]))


def s70_negative_flag_gate_removed(m):
    """定向检出 8: 摘掉按位资格判定 => s65 的非法 flags 必须被接受(判据消失)。"""
    mod = _mutant("RV15", [('        if bad_bits:',
                            '        if False:   # NEGATIVE-INJECTION RV15: 按位资格判定被摘掉')])
    ok, msg = s65_flags_by_bit_reject_illegal_commit(mod)
    return (not ok, "摘掉按位判定后 s65 仍然拒绝非法 flags => 判据不足: " + str(msg)[:120])


def s71_negative_busy_model_removed(m):
    """定向检出 9: 忙期模型被摘(feed 无视 block) => s67 必须不再成立(12 秒边界消失了)。"""
    mod = _mutant("RV16", [("        if block_seconds and self._clock is not None:",
                            "        if False:   # NEGATIVE-INJECTION RV16: 忙 msh 模型被摘掉")] )
    ok, msg = s67_busy_msh_twelve_second_boundary(mod)
    return (not ok, "摘掉忙期模型后 s67 仍然通过 => 判据不足: " + str(msg)[:120])


def s72_negative_poll_removed(m):
    """定向检出 10: 轮询退回单发 => s66 的 ACCEL->CRUISE 正控必须失败(一条立即查询不算巡航)。"""
    mod = _mutant("RV17", [('            if st.get("poll"):',
                            '            if False:   # NEGATIVE-INJECTION RV17: 有限轮询被摘掉')] )
    ok, msg = s66_running_needs_numeric_cruise_and_bounded_polling(mod)
    return (not ok, "摘掉轮询后 s66 仍然通过 => 判据不足: " + str(msg)[:120])


def s73_negative_boot_drain_removed(m):
    """定向检出 11: 复位后的启动流消费被摘掉 => s56 必须不再成立(查询会咬进启动字节)。"""
    mod = _mutant("RV18", [("                if st[\"action\"] == \"reset\":",
                            "                if False:   # NEGATIVE-INJECTION RV18: 不消费启动流")] )
    ok, msg = s56_halted_readback_reset_before_first_query(mod)
    return (not ok, "摘掉启动流消费后 s56 仍然通过 => 判据不足: " + str(msg)[:120])


def _ledger_events(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def hashlib_sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _mutant(marker, pairs):
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
    key = "NEGATIVE-INJECTION " + marker
    d = WORK / ("mut-" + hashlib_sha(key))
    d.mkdir(parents=True, exist_ok=True)
    p = d / "m8r3_serial_chain_mutant.py"
    p.write_text(src, encoding="utf-8")
    return load(p, "mut_" + hashlib_sha(key))


def _pyocd_shlex_fake(m, image_bytes):
    """假 pyOCD: 像真 commander 那样对 `-c` **命令串**再做一次分词(反斜杠在这里是转义符),
    然后按分出来的路径落盘。现场实证就是这个形状: 解析后的名字被咬掉分隔符 => 落不下去 => rc=1。
    git 调用原样交回真实 subprocess(离线只读, 与 FS68 同一手法)。"""
    saved = m.subprocess.run
    calls = []

    class Completed:
        def __init__(self, rc, out, err):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def fake_run(argv, **kw):
        import shlex
        argv = [str(x) for x in argv]
        calls.append(list(argv))
        if argv and argv[0] in ("git", "git.exe"):
            return saved(argv, **kw)
        if "flash" in argv:                       # S1: 既存成功原文的三条 loader 行
            return Completed(0, b"", b"0002300 I Erasing... [loader]\r\n"
                                   b"0008619 I Programming... [loader]\r\n"
                                   b"0023790 I Erased 291840 bytes (285 sectors), programmed "
                                   b"291840 bytes (285 pages), identical 0 bytes (0 pages) [loader]\r\n")
        if "commander" in argv:                   # S2: 只有真写得下去的文件才算"取回来了"
            ctoks = [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == "-c"]
            target = None
            for t in ctoks:
                parts = shlex.split(t)
                if parts and parts[0] == "savemem" and len(parts) >= 4:
                    target = parts[-1]
            if target is None:
                return Completed(1, b"", b"no savemem target in -c\r\n")
            try:
                with open(target, "wb") as f:
                    f.write(image_bytes)
            except OSError as e:
                return Completed(1, b"", ("Error: [Errno %s] %s: %r [__main__]\r\n" % (
                    getattr(e, "errno", 2), e.strerror or "No such file or directory", target)
                ).encode("utf-8", "replace"))
            out = ("Core 0 (None):  Halted\r\nSaved %d bytes to %s\r\n"
                   "Core 0 (None):  Halted\r\n" % (len(image_bytes), target)).encode("utf-8")
            return Completed(0, out, b"")
        return Completed(0, b"", b"")
    return calls, fake_run


def s74_run_root_inside_commander_c_is_shlex_safe(m):
    """首次真跑(FIELD-1)打出来的形状缺陷: `@RUN_ROOT` 会被展开进 pyOCD 的 `-c` 命令串,
    而 commander 对整条命令串再分词一次 —— Windows 的反斜杠在那里是**转义符**,
    `D:\\ns800\\...` 被咬成一串控制字符, 于是回读 rc=1(现场 stderr 原文见 host/field_run_...)。
    这条用例走**真 dispatch 的 subs 构造**(不是测试自己拼 subs —— 那正是当初漏掉它的原因),
    只把工具换成会分词的假件。既存成功原文里 savemem 的目标本来就是正斜杠形式
    (P0 的 S2_readback.stdout.txt: `Saved 266244 bytes to D:/ns800-m7-p0-.../readback.bin`)。"""
    import re
    import shlex
    image_bytes = (REPO / PLAN["tools"]["readback"]["image"]["path"]).read_bytes()
    plan = copy.deepcopy(PLAN)
    plan["stages"] = [s for s in plan["stages"] if s["id"] in ("S0", "S1", "S2")]
    d = out_dir("s74-run")
    uidf = Path(d) / "uid.txt"
    uidf.write_text("U" * 48, encoding="utf-8")
    calls, fake_run = _pyocd_shlex_fake(m, image_bytes)
    aroot, aid, appath = bind_approval(m, plan, "S74-BOUND")
    saved = m.subprocess.run
    m.subprocess.run = fake_run
    try:
        res = m.dispatch_plan(plan, "real", "real", d, m.SimClock(), m.REAL_PROFILE, "NO_PORT",
                              str(REPO), "APPR", "APPR", str(uidf), plan_dir=str(HERE),
                              approval_id=aid, approval_root=aroot, plan_path=appath)
    finally:
        m.subprocess.run = saved
    st = {s["id"]: s for s in res["stages"]}
    rb = [c for c in calls if "commander" in c]
    ctoks = [c[i + 1] for c in rb for i, x in enumerate(c[:-1]) if x == "-c"]
    savemem = [t for t in ctoks if t.split(" ")[0] == "savemem"]
    tgt = shlex.split(savemem[0])[-1] if savemem else ""
    one_token = bool(rb) and any(x.startswith("savemem ") for x in rb[0])   # F2 那半不许退回去
    clean = bool(tgt) and not any(ch in tgt for ch in "\\\r\n\x0c\x1a") and re.match(
        r"^[A-Za-z]:/", tgt) and tgt.endswith("/readback.bin")
    ok = (one_token and clean and not any("\\" in t for t in ctoks) and
          st.get("S2", {}).get("status") == "OK" and Path(tgt).is_file() and
          Path(tgt).read_bytes() == image_bytes and res["quota_used"].get("download") == 1)
    return ok, "s2=%s one_token=%s ctoks=%d backslash_in_c=%d target=%s written=%s" % (
        st.get("S2", {}).get("status"), one_token, len(ctoks),
        sum("\\" in t for t in ctoks), tgt[-46:] or "(none)", Path(tgt).is_file() if tgt else False)


def s75_negative_backslash_run_root_removed(m):
    """定向检出 8: 把 `@RUN_ROOT`/`run_root` 退回反斜杠形式(即现场翻车的那份字节),
    s74 必须转为失败 —— 否则说明这条判据其实没在管事。"""
    mod = _mutant("RV19", [
        ('    subs = {"@RUN_ROOT": out.as_posix(), "@IMAGE_BYTES": "0x%X" % img_bytes}',
         '    subs = {"@RUN_ROOT": str(out), "@IMAGE_BYTES": "0x%X" % img_bytes}  '
         '# NEGATIVE-INJECTION RV19: 回读根退回反斜杠形式'),
        ('payload["run_root"] = out.as_posix()      # 与 @RUN_ROOT 同形(见上面注释)',
         'payload["run_root"] = str(out)      # NEGATIVE-INJECTION RV19b: 同形性一起退回反斜杠')])
    ok, msg = s74_run_root_inside_commander_c_is_shlex_safe(mod)
    return (not ok, "反斜杠形式回来时 s74 仍然通过 => 判据不足: " + msg)


LEARN_ADC68_KEY = "diag_profile learn 8 80 120 0@adc68_over_sample"


def _learn_step_in(doc):
    """从**已交付的计划字节**里取 learn 步骤(不另造一份判据): 自检与现场跑的是同一个 spec。"""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("cmd"), str) and "diag_profile learn" in node["cmd"]:
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(doc)
    return found


def _learn_row(adc_n, sg_n):
    return ("[DIAG] learn OK adc[2050..2090]n%d nz3 sg[68..75]n%d seq1001..%d"
            % (adc_n, sg_n, 1000 + adc_n))


def s76_learn_must_min_matrix(m):
    """C-01 待办第 2 条: must_min(各 >=8) 的正反例与元负控**并入本自检**, 不再只是仓外临时夹具。
    判据来自计划字节; 健康面必须走到 ADC 过量采样(68), 不能只测 8/8 边界。"""
    plans = [("plan.json", PLAN),
             ("plan_continuation_s2.json",
              json.loads((HERE / "plan_continuation_s2.json").read_text(encoding="utf-8-sig")))]
    adc68 = ROWS["commands"][LEARN_ADC68_KEY]["healthy"][0]
    led = m.Ledger(out_dir("s76") / "l.jsonl", m.SimClock())
    cases = [("adc68_sg8", [_learn_row(68, 8)], "OK"),
             ("adc68_sg8_from_rows_json", [adc68], "OK"),
             ("boundary_8_8", [_learn_row(8, 8)], "OK"),
             ("adc8_sg68", [_learn_row(8, 68)], "OK"),
             ("adc100_sg79", [_learn_row(100, 79)], "OK"),
             ("adc7_sg8", [_learn_row(7, 8)], "FIELD_VALUE_BELOW_MIN"),
             ("adc68_sg7", [_learn_row(68, 7)], "FIELD_VALUE_BELOW_MIN"),
             ("both_7", [_learn_row(7, 7)], "FIELD_VALUE_BELOW_MIN"),
             ("no_learn_row_at_all", ["[DIAG] zero OK n=32"], "EXPECTATION_NOT_MET"),
             ("malformed_row", ["[DIAG] learn OK adc[1..2]n8"], "FIELD_NOT_EXACTLY_ONE"),
             ("duplicate_success", [_learn_row(68, 8), _learn_row(68, 8)], "FIELD_NOT_EXACTLY_ONE"),
             ("failed_timeout", ["[DIAG] learn FAILED timeout 12000ms"], "BUSINESS_FAILED"),
             ("not_supported", ["msh: command not supported"], "BUSINESS_FAILED")]
    mutations = [("min9_rejects_adc8",
                  lambda s: s["expect_values"][0]["must_min"].__setitem__("adc_n", 9),
                  [_learn_row(8, 8)], "FIELD_VALUE_BELOW_MIN"),
                 ("gate_removed_admits_adc7",
                  lambda s: s["expect_values"][0].__setitem__("must_min", {}),
                  [_learn_row(7, 8)], "OK"),
                 ("absent_group_named_reject",
                  lambda s: s["expect_values"][0].__setitem__("must_min", {"adc_n_nope": 1}),
                  [_learn_row(68, 8)], "FIELD_VALUE_MISMATCH"),
                 ("exact8_rejects_adc68",
                  lambda s: s["expect_values"][0].__setitem__("must", {"adc_n": "8"}),
                  [_learn_row(68, 8)], "FIELD_VALUE_MISMATCH")]
    bad, n = [], 0
    for fname, doc in plans:
        steps = _learn_step_in(doc)
        if len(steps) != 1:
            bad.append("%s:learn 步骤数=%d(要求 1)" % (fname, len(steps)))
            continue
        spec = steps[0]["expect_values"][0]
        if spec.get("must_min") != {"adc_n": 8, "sg_n": 8} or "must" in spec:
            bad.append("%s:判据不是 must_min{8,8} 且无 must=%s/%s"
                       % (fname, spec.get("must"), spec.get("must_min")))
        for name, rows, want in cases:
            n += 1
            r = m.assert_step(steps[0], rows, "s76_learn", led, raw=None)
            got = "OK" if r.get("verdict") == "OK" else r.get("code")
            if got != want:
                bad.append("%s/%s want=%s got=%s" % (fname, name, want, got))
        for name, mutate, rows, want in mutations:
            n += 1
            step = copy.deepcopy(steps[0])
            mutate(step)
            r = m.assert_step(step, rows, "s76_mut", led, raw=None)
            got = "OK" if r.get("verdict") == "OK" else r.get("code")
            if got != want:
                bad.append("%s/%s want=%s got=%s" % (fname, name, want, got))
    led.close()
    return (not bad, "plans=%d cases=%d bad=%s" % (len(plans), n, bad[:4] or "0"))


def s77_forensic_event_is_multi_source_five(m):
    """C-02 的范围核对: 计划与夹具都取 event=5, 而**归档运行输出**里的 9 原样保留(不是全局替换)。"""
    hi = _flash_head_idx(m)
    bad = []
    docs = [("plan.json", PLAN),
            ("plan_continuation_s2.json",
             json.loads((HERE / "plan_continuation_s2.json").read_text(encoding="utf-8-sig")))]
    for fname, doc in docs:
        for stg in doc["stages"]:
            for spec in stg.get("require_frames") or []:
                for need in spec.get("must_include") or []:
                    if need.get("event") != "5" or need.get("step_hz") != "0":
                        bad.append("%s/%s=%s" % (fname, stg["id"], need))
    for line in ROWS["commands"]["flash_dump 0 8"]["trip"][1:]:
        p = line.split(",")
        if len(p) == m.FLASH_DUMP_COLUMNS and all(x.lstrip("-").isdigit() for x in p):
            if p[hi["event"]] != "5":
                bad.append("rows.json event=%s" % p[hi["event"]])
    prov = ROWS["commands"]["flash_dump 0 8"].get("_provenance") or {}
    if prov.get("kind") != "source_shape":
        bad.append("flash_dump 夹具来源等级=%s(没有板上采集, 必须 source_shape)" % prov.get("kind"))
    archived = list((HERE.parent / "offline").glob("psrun-*/neg-stagemutate/plan.json"))
    nine = ('"event": "9"', '"event":"9"')
    still_nine = [p for p in archived
                  if any(n in p.read_text(encoding="utf-8") for n in nine)]
    if archived and not still_nine:
        bad.append("归档运行输出被一起改掉了(%d 份) => 这不是定向更正" % len(archived))
    return (not bad, "plans=%d archived_with_9=%d/%d bad=%s" %
            (len(docs), len(still_nine), len(archived), bad[:4] or "0"))


def s78_negative_event_pin_back_to_nine(m):
    """C-02 修前形态复现: 同一批 event=5 的停后帧, 把判据退回 9 就必须具名拒绝(且原因是
    NO_FRAME_WITH) —— 这证明"钉 9"会把一次**成功**的保护判成取证未完成。"""
    mutated_plan = copy.deepcopy(PLAN)
    hits = 0
    for stg in mutated_plan["stages"]:
        for spec in stg.get("require_frames") or []:
            for need in spec.get("must_include") or []:
                if need.get("event") == "5":
                    need["event"] = "9"
                    hits += 1
    res = dispatch(m, "trip", plan=mutated_plan)[0]
    s9 = next((s for s in res["stages"] if s["id"] == "S9"), {"status": "ABSENT"})
    return (hits == 1 and s9.get("code") == "FLASH_FRAMES_NOT_RECOVERED" and
            s9.get("success") is not True and "NO_FRAME_WITH" in str(s9.get("detail", "")),
            "hits=%d s9=%s success=%s detail=%s" %
            (hits, s9.get("code"), s9.get("success"), str(s9.get("detail"))[:90]))


def s79_negative_event_leg_removed(m):
    """定向检出: 摘掉 event 这一腿(只留 step_hz=0) => "只有 event=9 的停后帧"必须被**放行**,
    否则 FS77/FS78 的拒绝并不是 event 判据造成的, 因果不成立。"""
    hi = _flash_head_idx(m)
    mutated_plan = copy.deepcopy(PLAN)
    for stg in mutated_plan["stages"]:
        for spec in stg.get("require_frames") or []:
            for need in spec.get("must_include") or []:
                need.pop("event", None)
    rows = copy.deepcopy(ROWS)
    grp = rows["commands"]["flash_dump 0 8"]
    grp["trip"] = _flash_edit(m, hi, grp["trip"], set_col={"event": 9})
    res = dispatch(m, "trip", plan=mutated_plan, rows=rows)[0]
    s9 = next((s for s in res["stages"] if s["id"] == "S9"), {"status": "ABSENT"})
    return (s9.get("status") == "OK" and s9.get("success") is True,
            "摘掉 event 腿后 event9 停后帧的 S9=%s success=%s(应为放行)" %
            (s9.get("status"), s9.get("success")))


# ============ R6(2026-11-03 复审 §5/§7.3): 字节完整性硬门 + 退役的"损坏后可继续"合同 ============
def s80_clean_response_still_passes(m):
    """正控: 干净响应在新的字节门下照常通过(新门不是"一律拒绝")。"""
    c = m.SimClock()
    led = m.Ledger(out_dir("s80") / "l.jsonl", c)
    body = ok_rows(["[TMC-CFG] state=CONFIGURED fail_reg=0x00 snapshot_valid=1"])
    p = m.FakePort([body], led)
    lines, n = m.read_until_prompt(p, 1.0, led, out_dir("s80") / "a.bin", c)
    v = m.assert_step({"expect_contains": ["[TMC-CFG] state=CONFIGURED"]}, lines, "s80", led)
    led.close()
    return (v["verdict"] == "OK" and len(lines) == 1 and n == len(body),
            "verdict=%s rows=%d bytes=%d" % (v["verdict"], len(lines), n))


def s81_bare_nul_hard_stop_bytes_kept(m):
    """复审探针 2 的形状: 提示符前多一条独立的 `00 CR LF`。
    旧版只记 NUL_IN_RESPONSE 事件, 整条响应仍判 OK; 现在必须具名硬停止, 且落盘原件一字不改。"""
    c = m.SimClock()
    d = out_dir("s81")                                   # out_dir 每次换新目录, 所以台账与原件必须同一个 d
    led = m.Ledger(d / "l.jsonl", c)
    body = ok_rows(["[TMC-CFG] state=CONFIGURED snapshot_valid=1"]).replace(
        b"msh >", b"\x00\r\nmsh >")
    p = m.FakePort([body], led)
    path = d / "a.bin"
    code = ""
    try:
        m.read_until_prompt(p, 1.0, led, path, c)
    except m.Stop as e:
        code = e.code
    kept = path.exists() and path.read_bytes() == body
    led.close()                                       # 先落台账再读事件(台账是缓冲写)
    evs = [x["kind"] for x in _ledger_events(d / "l.jsonl")]
    return (code == "NUL_BYTE_IN_RESPONSE" and kept and "NUL_IN_RESPONSE" in evs,
            "code=%s 原字节逐字节相同=%s 台账含 NUL 事件=%s" % (code or "未停", kept,
                                                            "NUL_IN_RESPONSE" in evs))


def s82_danger_text_beats_decode_failure(m):
    """复审探针 3 的形状: 同一批原始字节里既有 ASCII `HardFault` 又有非法 UTF-8。
    旧顺序在解码处先抛, 再被恢复合同转成"可继续"; 现在危险门只看原始字节且排在解码之前。"""
    c = m.SimClock()
    led = m.Ledger(out_dir("s82") / "l.jsonl", c)
    body = b"[11:23:45] HardFault\r\n\xe6\x96\xad\xff\xfe\r\nmsh >"
    p = m.FakePort([body], led)
    code = detail = ""
    try:
        m.read_until_prompt(p, 1.0, led, out_dir("s82") / "a.bin", c)
    except m.Stop as e:
        code, detail = e.code, e.detail
    led.close()
    return (code == "DANGER_TEXT_IN_STREAM", "code=%s detail=%s" % (code or "未停", detail))


def s83_declared_recovery_refused_and_next_step_not_sent(m):
    """申报 byte_corruption_recovery 的步骤一律具名拒绝, 且同会话后面的危险动作不再发出。"""
    fx = motion_fx({"__watch__": ROWS["commands"]["__watch__"]["healthy"],
                    "motor_start": [],
                    "tmc_config apply": ["[TMC-CFG] state=CONFIGURED"],
                    "diag_profile tier 2": ["[DIAG] tier=2"]})
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 1, "expect_absent": ["REFUSED"]},
             {"cmd": "tmc_config apply", "as": "cfgapply",
              "byte_corruption_recovery": {"reconfirmed_by": "cfgafter",
                                           "no_facts_from_corrupted_response": True}},
             {"cmd": "diag_profile tier 2", "as": "must_not_run"}]
    r = m.exec_session("s83", stamp(m, steps), out_dir("s83"), "stub", fx, "NO_DEVICE", 30, m.SimClock(),
                       motion_cap_seconds=5,
                       plan={"quota": {"port_open": 1, "motor_run": 1, "retry": 0,
                                       "diag_profile_learn": 1,
                                       "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                                       "cleanup_motor_status": 1}})
    tx = tx_seq(r)
    return (r["code"] == "RETIRED_RECOVERY_CONTRACT" and "diag_profile tier 2" not in tx and
            "motor_stop" in tx and "motor_disarm" in tx and r["success"] is False,
            "code=%s tx=%s disarm=%s" % (r["code"], ",".join(tx), r["disarm"]))


def s84_plan_gate_refuses_recovery_declaration(m):
    """离线门: 申报该合同的计划判红; 不申报的计划判 PASS 并明确写"未申报任何例外"。"""
    app = str(Path(HERE).parents[3] / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
              "ns800rt7p65-nssinepad/applications")
    base = json.loads(json.dumps(PLAN))
    _l, bad0, _x = m.validate_plan(base, ROWS, app)
    line0 = [s for s in _l if s.startswith("PLAN_BYTE_RECOVERY")]
    mut = json.loads(json.dumps(PLAN))
    stg = next(s for s in mut["stages"] if s.get("steps"))
    stg["steps"][0]["byte_corruption_recovery"] = {"reconfirmed_by": stg["steps"][-1].get("as"),
                                                   "no_facts_from_corrupted_response": True}
    _l2, bad1, _x2 = m.validate_plan(mut, ROWS, app)
    line1 = [s for s in _l2 if s.startswith("PLAN_BYTE_RECOVERY")]
    ok0 = bool(line0) and "|PASS|" in line0[0] and "未申报任何" in line0[0]
    ok1 = bool(line1) and "|FAIL|" in line1[0] and "已退役" in line1[0]
    return (ok0 and ok1 and bad1 > bad0,
            "未申报=%s 申报后=%s bad %d->%d" %
            ((line0[0].split("|")[2] if line0 else "-"),
             (line1[0].split("|")[2] if line1 else "-"), bad0, bad1))


def s85_quota_not_revived_by_new_dir_or_new_process(m):
    """复审 §7.4 的主控: R5-C 那 26 场每场各持一份**内存**账, 换 --out / 重开进程就等于
    重新发一份额度(apply 实际用到 14/2)。现在同一批准身份连跑两整包, 第二包必须在
    **动工具之前**被持久账拒绝, 且账上的消耗是 1 而不是 2、也不是被清零的 0。"""
    p = copy.deepcopy(PLAN)
    aroot, aid, appath = bind_approval(m, p, "S85-ONE-APPROVAL")
    r1, _d1 = dispatch(m, "healthy", plan=p, approval_id=aid, approval_root=aroot,
                       plan_path=appath)
    r2, _d2 = dispatch(m, "healthy", plan=p, approval_id=aid, approval_root=aroot,
                       plan_path=appath)
    doc = json.loads(Path(m.approval_path(aroot, aid)).read_text(encoding="utf-8"))
    s1_1 = [x for x in r1["stages"] if x["id"] == "S1"][0]
    s1_2 = [x for x in r2["stages"] if x["id"] == "S1"][0]
    return (r1["quota_used"].get("download") == 1 and s1_1["status"] == "OK" and
            s1_2.get("code") == "APPROVAL_QUOTA_EXHAUSTED" and s1_2["status"] == "STOP" and
            r2["actions_attempted"] == [] and r2["quota_used"] == {} and
            doc["spent"].get("download") == 1 and doc["pending"] == [] and
            str(Path(aroot)) not in str(_d1),
            "一轮S1=%s download=%s 二轮S1=%s/%s 二轮工具=%d 二轮内存账=%s 账本download=%s "
            "账本在输出目录之外=%s" % (
                s1_1["status"], r1["quota_used"].get("download"), s1_2["status"],
                s1_2.get("code"),
                len(r2["actions_attempted"]), r2["quota_used"], doc["spent"].get("download"),
                str(Path(aroot)) not in str(_d1)))


def s86_last_unit_reserved_then_crash_is_not_refunded(m):
    """"占用后崩溃"必须按**已消耗**计: 账本里没有 release 这条路, 未确认的预占只留在 pending。

    R6A §3 之后账本**只能**由 approval_init 建立(运行期读盘一律严格), 所以这里先离线开账。"""
    root = out_dir("s86")
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    caps, ids = {"motor_run": 1}, full_ids(11)
    m.approval_init(root, "S86", ids, caps, note="offline s86, no device action")
    path = m.approval_path(root, "S86")
    a = m.ApprovalLedger(path, "S86", ids, caps, led, m.SimClock())
    a.reserve("motor_run", 1)               # 占掉最后一格……然后进程在这里死掉(没有 commit)
    del a                                   # 等价于"崩了": 预占留在盘上的 pending 里
    b = m.ApprovalLedger(path, "S86", ids, caps, led, m.SimClock())
    snap = b.snapshot()
    code = ""
    try:
        b.reserve("motor_run", 1)
    except m.Stop as e:
        code = e.code
    led.close()
    kinds = [x["kind"] for x in _ledger_events(root / "l.jsonl")]
    return (snap["spent"] == {"motor_run": 1} and len(snap["pending"]) == 1 and
            code == "APPROVAL_QUOTA_EXHAUSTED" and
            kinds.count("APPROVAL_RESERVED") == 1 and kinds.count("APPROVAL_REFUSED") == 1,
            "spent=%s pending=%d 再占=%s 事件=%s" % (
                snap["spent"], len(snap["pending"]), code or "未停",
                "R%d/F%d" % (kinds.count("APPROVAL_RESERVED"), kinds.count("APPROVAL_REFUSED"))))


def s87_two_executors_on_one_approval_never_exceed_cap(m):
    """双执行者(两个对象/两个进程)共享同一批准身份: 交替预占的总量不得超过 cap,
    超出那一次必须具名拒绝; 账本被别的进程锁住时也不许盲写。"""
    root = out_dir("s87")
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    caps, ids = {"port_open": 3}, full_ids(21)
    m.approval_init(root, "S87", ids, caps, note="offline s87, no device action")
    path = m.approval_path(root, "S87")
    a = m.ApprovalLedger(path, "S87", ids, caps, led, m.SimClock())
    b = m.ApprovalLedger(path, "S87", ids, caps, led, m.SimClock())
    seq, refused = [], 0
    for who, t in (("A", a), ("B", b), ("A", a), ("B", b), ("A", a)):
        try:
            t.reserve("port_open", 1)
            seq.append(who + "=ok")
        except m.Stop as e:
            refused += 1
            seq.append("%s=%s" % (who, e.code))
    doc = json.loads(path.read_text(encoding="utf-8"))
    lock = path.with_name(path.name + ".lock")
    lock.write_text("other-pid", encoding="utf-8")
    locked = ""
    try:
        a.snapshot()
    except m.Stop as e:
        locked = e.code
    lock.unlink()
    led.close()
    return (doc["spent"] == {"port_open": 3} and refused == 2 and
            all("APPROVAL_QUOTA_EXHAUSTED" in x for x in seq[3:]) and
            locked == "APPROVAL_LEDGER_LOCKED",
            "spent=%s 序列=%s 被锁=%s" % (doc["spent"], ",".join(seq), locked or "未停"))


def s88_missing_or_corrupt_ledger_blocks_real_action(m):
    """台账缺失 / JSON 损坏 => 真实动作一律不进入, 且**绝不自建一份满额账**。
    这条用的是 transport=real(端口工厂换成内存假口, 不碰设备), 所以拒绝完全是账本引起的。"""
    root = out_dir("s88")
    p = copy.deepcopy(PLAN)
    p["stages"] = [s for s in p["stages"] if s["id"] in ("S0", "S3")]
    pp = plan_bytes(p, "S88")          # 真实整包必须有 --plan 字节可核对(R6A §4)
    opened = []
    saved = m.make_real_port
    m.make_real_port = lambda name, led: (opened.append(name),
                                          m.FakePort([], led, clock=m.SimClock()))[1]
    try:
        miss = m.dispatch_plan(p, "real", "real", out_dir("s88-missing"), m.SimClock(),
                               m.REAL_PROFILE, "NO_PORT", str(REPO), "APPR", "APPR", "",
                               plan_dir=str(HERE), approval_id="S88-A", approval_root=str(root),
                               plan_path=pp)
        bad_path = m.approval_path(root, "S88-B")
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text('{"version": 2, "approval_id": "S88-B"', encoding="utf-8")  # 半截 JSON
        corrupt = m.dispatch_plan(p, "real", "real", out_dir("s88-corrupt"), m.SimClock(),
                                  m.REAL_PROFILE, "NO_PORT", str(REPO), "APPR", "APPR", "",
                                  plan_dir=str(HERE), approval_id="S88-B", approval_root=str(root),
                                  plan_path=pp)
    finally:
        m.make_real_port = saved
    rebuilt = m.approval_path(root, "S88-A").exists()
    return (miss["status"] == "REFUSED" and miss["code"] == "APPROVAL_LEDGER_ABSENT" and
            corrupt["status"] == "REFUSED" and corrupt["code"] == "APPROVAL_LEDGER_CORRUPT" and
            miss["stages"] == [] and corrupt["stages"] == [] and opened == [] and
            miss["actions_attempted"] == [] and not rebuilt,
            "缺失=%s 损坏=%s 开口=%d 阶段=%d/%d 自建账=%s" % (
                miss.get("code"), corrupt.get("code"), len(opened),
                len(miss["stages"]), len(corrupt["stages"]), rebuilt))


def s89_changed_identity_cannot_reuse_the_same_approval(m):
    """批准身份 = 令牌 + 计划 SHA + 执行器 SHA + 镜像 SHA(+ cap)。任一变了都必须具名拒绝沿用,
    而且不许"顺手"按新身份开一本新预算 —— 那等于同一份授权被悄悄续签。"""
    root = out_dir("s89")
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    ids = {"plan_sha256": "1" * 64, "executor_sha256": "2" * 64, "image_sha256": "3" * 64}
    caps = {"port_open": 5}
    path = m.approval_path(root, "S89")
    m.approval_init(root, "S89", ids, caps, note="offline s89, no device action")
    m.ApprovalLedger(path, "S89", ids, caps, led, m.SimClock()).reserve("port_open", 1)
    out = {}
    for tag, kw, cp in (
            ("执行器换了", dict(ids, executor_sha256="9" * 64), caps),
            ("计划换了", dict(ids, plan_sha256="8" * 64), caps),
            ("镜像换了", dict(ids, image_sha256="7" * 64), caps),
            ("cap 放宽了", ids, dict(caps, port_open=9)),
            ("令牌换了", ids, caps)):
        obj = m.ApprovalLedger(path, "OTHER" if tag == "令牌换了" else "S89",
                               kw, cp, led, m.SimClock())
        try:
            obj.snapshot()
            out[tag] = "未拒"
        except m.Stop as e:
            out[tag] = e.code
    led.close()
    same = m.ApprovalLedger(path, "S89", ids, caps, led, m.SimClock()).snapshot()
    return (all(v == "APPROVAL_IDENTITY_MISMATCH" for v in out.values()) and
            same["spent"] == {"port_open": 1},
            "%s 原账消耗仍=%s" % (out, same["spent"]))


def s90_business_exhausted_but_disarm_still_runs(m):
    """撤能优先于额度: 三条清理预留**全部**在账上耗尽时, motor_stop/disarm/status 仍要发出,
    禁能照旧由真响应字段证明; 同时这一场一律不算成功, 且每条拒绝都在两本账上具名留痕。"""
    root = out_dir("s90")
    led0 = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    caps = {"port_open": 1, "motor_run": 1, "retry": 0,
            "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1, "cleanup_motor_status": 1}
    ids = full_ids(31)
    m.approval_init(root, "S90", ids, caps, note="offline s90, no device action")
    a = m.ApprovalLedger(m.approval_path(root, "S90"), "S90", ids, caps, led0, m.SimClock())
    for k in ("cleanup_motor_stop", "cleanup_motor_disarm", "cleanup_motor_status"):
        a.reserve(k, 1, kind="cleanup")          # 上一场已把撤能预留花光(等价于额度耗尽)
    fx = motion_fx({"__watch__": ROWS["commands"]["__watch__"]["healthy"], "motor_start": []})
    try:
        r = m.exec_session("s90", stamp(m, [{"cmd": "motor_start", "as": "start", "seconds": 1,
                                    "expect_absent": ["REFUSED"]}]), out_dir("s90-run"),
                           "stub", fx, "NO_DEVICE", 30, m.SimClock(), motion_cap_seconds=5,
                           plan={"quota": dict(caps)}, quota_box=dict(caps), approval=a)
    finally:
        led0.close()                              # 账本事件先落盘再读
    tx = tx_seq(r)
    evs = [x["kind"] for x in _ledger_events(root / "l.jsonl")]
    refused = [x for x in _ledger_events(root / "l.jsonl") if x["kind"] == "APPROVAL_REFUSED"]
    return (all(c in tx for c in ("motor_stop", "motor_disarm", "motor_status")) and
            r["disarm"] == "CONFIRMED_DISABLED" and r["success"] is False and
            len(r["cleanup_overruns"]) == 3 and
            all("APPROVAL_QUOTA_EXHAUSTED_SENT_ANYWAY" in x for x in r["cleanup_overruns"]) and
            len(refused) == 3 and "APPROVAL_COMMITTED" in evs,
            "tx=%s disarm=%s success=%s 披露=%s 账本拒绝=%d 确认消耗=%s" % (
                ",".join(tx), r["disarm"], r["success"],
                ";".join(r["cleanup_overruns"]), len(refused), "APPROVAL_COMMITTED" in evs))


def s91_negative_ledger_gate_removed(m):
    """定向检出: 把"绑定持久账"这一步摘掉(等价于退回 R5-C 的每轮内存账), s85 必须**转为失败** ——
    否则那条 APPROVAL_QUOTA_EXHAUSTED 其实不是账本给的, 这条控制就拦不住越权。"""
    saved = m.open_approval
    m.open_approval = lambda *a, **k: None
    try:
        ok, detail = s85_quota_not_revived_by_new_dir_or_new_process(m)
    finally:
        m.open_approval = saved
    return (not ok, "摘掉账本门后 s85 判=%s(应为失败) 细节=%s" % (ok, detail))


def s92_negative_cap_check_removed(m):
    """定向检出: 把 reserve 的 cap 判定摘掉(恒放行), s87 必须转为失败, 且账上真的超了 cap ——
    证明"两个执行者不超额度"是靠这本账, 不是测试自己数出来的。

    突变体绕开 _load/_store(严格校验也在里面), 否则失败原因会变成"账本损坏"而不是"没守 cap"。"""
    saved = m.ApprovalLedger.reserve

    def no_cap(self, name, n=1, kind="business"):
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        doc["spent"][name] = doc["spent"][name] + n
        doc["pending"].append({"id": "%s#mut%d" % (name, doc["spent"][name]),
                               "name": name, "n": n, "kind": kind})
        doc["writes"] = doc["writes"] + 1
        self.path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1),
                             encoding="utf-8")
        return {"name": name, "tag": "%s#mut" % name, "kind": kind}

    m.ApprovalLedger.reserve = no_cap
    try:
        ok, detail = s87_two_executors_on_one_approval_never_exceed_cap(m)
    finally:
        m.ApprovalLedger.reserve = saved
    return (not ok, "摘掉 cap 判定后 s87 判=%s(应为失败) 细节=%s" % (ok, detail))


# ----------------------------------------------------------------- R6A R1: 账本严格读取反例
def _tmp_ledger(m, tag, caps=None, ids=None, reserve=()):
    """**临时**账(只活在 WORK 里的一次性目录): 离线 init 开账 + 可选预占。
    绝不产生任何可用于现场的批准身份 —— 复审 §3 最后一句。"""
    root = out_dir("r1-" + tag)
    caps = dict(caps or {"apply": 1, "port_open": 1})
    ids = ids or full_ids(len(tag) + 1)
    m.approval_init(root, tag, ids, caps, note="offline R6A R1 probe, no device action")
    path = m.approval_path(root, tag)
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    obj = m.ApprovalLedger(path, tag, ids, caps, led, m.SimClock())
    for name in reserve:
        obj.reserve(name, 1)
    return root, path, ids, caps, led, obj


def _mutate_ledger(path, mutate):
    """按复审的口径改**账本字节**(保持合法 JSON): 改完立刻读回, 判据看的是拒绝而不是标签。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    mutate(doc)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1),
                          encoding="utf-8")
    return doc


def _codes_of(fn, *a, **k):
    try:
        fn(*a, **k)
        return "未拒", None
    except Exception as e:                                        # noqa: BLE001
        return getattr(e, "code", e.__class__.__name__), e


def s93_missing_identity_sha_is_refused(m):
    """反例①: 删掉 identities.executor_sha256(仍是合法 JSON)。旧版 snapshot 正常返回,
    因为比较写的是 `if want and have and want != have` —— 缺失那侧直接跳过。"""
    root, path, ids, caps, led, obj = _tmp_ledger(m, "R93")
    doc = _mutate_ledger(path, lambda d: d["identities"].pop("executor_sha256"))
    snap_code, _ = _codes_of(obj.snapshot)
    res_code, _ = _codes_of(obj.reserve, "apply", 1)
    after = json.loads(path.read_text(encoding="utf-8"))
    led.close()
    return (snap_code == "APPROVAL_LEDGER_CORRUPT" and res_code == "APPROVAL_LEDGER_CORRUPT" and
            "executor_sha256" not in after["identities"] and after["spent"] == doc["spent"] and
            after["pending"] == [],
            "snapshot=%s reserve=%s 缺字段仍在=%s spent 没被补=%s pending=%d" %
            (snap_code, res_code, "executor_sha256" not in after["identities"],
             after["spent"], len(after["pending"])))


def s94_missing_spent_field_is_not_full_quota(m):
    """反例②: 删掉整个 spent 字段。旧版 `doc.setdefault("spent", {})` 把"结构丢失"当成
    "从未消耗", 于是重复预占又成功。现在: 缺必需字段 = 账本损坏, 不补零、不重建。"""
    root, path, ids, caps, led, obj = _tmp_ledger(m, "R94", reserve=("apply",))
    before = json.loads(path.read_text(encoding="utf-8"))
    _mutate_ledger(path, lambda d: d.pop("spent"))
    code, _ = _codes_of(obj.reserve, "apply", 1)
    after = json.loads(path.read_text(encoding="utf-8"))
    snap_code, _ = _codes_of(obj.snapshot)
    led.close()
    return (code == "APPROVAL_LEDGER_CORRUPT" and snap_code == "APPROVAL_LEDGER_CORRUPT" and
            "spent" not in after and after["pending"] == before["pending"],
            "再预占=%s snapshot=%s spent 被补回来了=%s 预占留痕=%d" %
            (code, snap_code, "spent" in after, len(after["pending"])))


def s95_illegal_counts_are_rejected(m):
    """反例③ + 计数口径: spent.apply = -1 时旧版 `int()` 一转就当合法, 于是再预占变成 0 并
    发出 apply#0 的凭据。现在负数/小数/字符串数字/bool 全部具名拒绝; reserve 的 n 必须是正整数。"""
    root, path, ids, caps, led, obj = _tmp_ledger(m, "R95", reserve=("apply",))
    _mutate_ledger(path, lambda d: d["spent"].__setitem__("apply", -1))
    code, _ = _codes_of(obj.reserve, "apply", 1)
    doc = json.loads(path.read_text(encoding="utf-8"))
    bad_n = {}
    for tag, n in (("0", 0), ("-1", -1), ("True", True), ("'1'", "1"), ("1.5", 1.5),
                   ("None", None)):
        bad_n[tag] = _codes_of(obj.reserve, "port_open", n)[0]
    init_bad = {}
    for tag, c in (("bool cap", {"apply": True}), ("负 cap", {"apply": -2}),
                   ("字符串 cap", {"apply": "3"}), ("小数 cap", {"apply": 1.0})):
        r2 = out_dir("r95-init")
        init_bad[tag] = _codes_of(m.approval_init, r2, "R95-%s" % tag, ids, c)[0]
    led.close()
    return (code == "APPROVAL_LEDGER_CORRUPT" and doc["spent"]["apply"] == -1 and
            all(v == "APPROVAL_RESERVE_N_INVALID" for v in bad_n.values()) and
            all(v == "APPROVAL_CAPS_INVALID" for v in init_bad.values()),
            "spent=-1 再预占=%s 账本仍是 %s; n 非法=%s; init cap 非法=%s" %
            (code, doc["spent"]["apply"], bad_n, init_bad))


def s96_deleted_ledger_cannot_be_rebuilt(m):
    """反例④: 已绑定、已耗尽后**删掉账本文件**。旧版每次读盘都走自动建账分支, 于是删文件
    等于把一次用尽的批准复活成满额; open_approval 只在最开始查了一次 exists。"""
    root, path, ids, caps, led, obj = _tmp_ledger(m, "R96")
    res = obj.reserve("apply", 1)
    obj.commit(res)
    ex, _ = _codes_of(obj.reserve, "apply", 1)              # 正控: 满额账耗尽 => 具名拒绝
    path.unlink()
    code_snap, _ = _codes_of(obj.snapshot)
    code_res, _ = _codes_of(obj.reserve, "apply", 1)
    gate_code, _ = _codes_of(m.open_approval, "stub", str(root), "R96", ids, caps, led,
                             m.SimClock())
    rebuilt = path.exists()
    led.close()
    return (ex == "APPROVAL_QUOTA_EXHAUSTED" and code_snap == "APPROVAL_LEDGER_ABSENT" and
            code_res == "APPROVAL_LEDGER_ABSENT" and gate_code == "APPROVAL_LEDGER_ABSENT" and
            not rebuilt,
            "耗尽正控=%s 删后 snapshot=%s reserve=%s open_approval(stub)=%s 自建满额账=%s" %
            (ex, code_snap, code_res, gate_code, rebuilt))


def s97_incomplete_identity_or_cap_cannot_open_or_reuse(m):
    """开账侧与读账侧同一口径: 三件 SHA 缺一件/形状不对根本立不起来; 事后删一个 cap 键、
    带来一套多/少键的 caps、或删掉一个 spent 动作键, 都必须具名拒绝(旧版只比对**双方都有**的键,
    缺的一侧直接跳过)。"""
    root = out_dir("r97")
    ids = full_ids(97)
    miss = dict(ids)
    miss.pop("image_sha256")
    init_code, _ = _codes_of(m.approval_init, root, "R97-A", miss, {"apply": 1})
    bad_hex = dict(ids, plan_sha256="0" * 63 + "g")
    fmt_code, _ = _codes_of(m.approval_init, root, "R97-B", bad_hex, {"apply": 1})
    m.approval_init(root, "R97-C", ids, {"apply": 1, "port_open": 1},
                    note="offline r97")
    path = m.approval_path(root, "R97-C")
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    same_caps = m.ApprovalLedger(path, "R97-C", ids, {"apply": 1, "port_open": 1}, led,
                                 m.SimClock())
    less_caps = m.ApprovalLedger(path, "R97-C", ids, {"apply": 1}, led, m.SimClock())
    more_caps = m.ApprovalLedger(path, "R97-C", ids, {"apply": 1, "port_open": 1, "save": 9},
                                 led, m.SimClock())
    ok_code, _ = _codes_of(same_caps.snapshot)
    less_code, _ = _codes_of(less_caps.snapshot)
    more_code, _ = _codes_of(more_caps.snapshot)
    _mutate_ledger(path, lambda d: d["caps"].pop("port_open"))
    doc_missing_cap, _ = _codes_of(same_caps.snapshot)
    _mutate_ledger(path, lambda d: d["caps"].__setitem__("port_open", 1))
    _mutate_ledger(path, lambda d: d["spent"].pop("port_open"))
    doc_missing_spent, _ = _codes_of(same_caps.snapshot)
    _mutate_ledger(path, lambda d: d["spent"].__setitem__("port_open", 0))
    _mutate_ledger(path, lambda d: d["identities"].__setitem__("plan_sha256", "z" * 64))
    doc_bad_sha, _ = _codes_of(same_caps.snapshot)
    led.close()
    return (init_code == "APPROVAL_IDENTITIES_INCOMPLETE" and
            fmt_code == "APPROVAL_IDENTITIES_INCOMPLETE" and ok_code == "未拒" and
            less_code == "APPROVAL_IDENTITY_MISMATCH" and
            more_code == "APPROVAL_IDENTITY_MISMATCH" and
            doc_missing_cap == "APPROVAL_IDENTITY_MISMATCH" and
            doc_missing_spent == "APPROVAL_LEDGER_CORRUPT" and
            doc_bad_sha == "APPROVAL_LEDGER_CORRUPT",
            "缺SHA开账=%s 非16进制=%s 完整正控=%s caps少键=%s caps多键=%s 账本缺cap=%s "
            "账本缺spent键=%s 账本SHA畸形=%s" %
            (init_code, fmt_code, ok_code, less_code, more_code, doc_missing_cap,
             doc_missing_spent, doc_bad_sha))


def s98_two_inits_cannot_both_open_the_same_approval(m):
    """双 init 竞争: 旧版是 exists 检查 + os.replace, 两次 init 都会"成功", 第二次把已消费的账
    覆回满额(等于退款)。现在同一把锁 + O_CREAT|O_EXCL: 只有一份账、只有一个赢。"""
    import threading
    root = out_dir("r98")
    ids, caps = full_ids(98), {"apply": 1}
    res = []
    barrier = threading.Barrier(3)

    def who():
        barrier.wait()
        try:
            m.approval_init(root, "R98", ids, caps, note="offline r98")
            res.append("init")
        except m.Stop as e:
            res.append(e.code)

    ts = [threading.Thread(target=who) for _ in range(2)]
    for t in ts:
        t.start()
    barrier.wait()
    for t in ts:
        t.join(10)
    docs = sorted(p.name for p in root.glob("approval_R98*.json"))
    doc = json.loads(m.approval_path(root, "R98").read_text(encoding="utf-8"))
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    obj = m.ApprovalLedger(m.approval_path(root, "R98"), "R98", ids, caps, led, m.SimClock())
    obj.reserve("apply", 1)
    reinit = _codes_of(m.approval_init, root, "R98", ids, caps)[0]
    after = json.loads(m.approval_path(root, "R98").read_text(encoding="utf-8"))
    led.close()
    return (res.count("init") == 1 and res.count("APPROVAL_LEDGER_ALREADY_EXISTS") == 1 and
            len(docs) == 1 and doc["spent"] == {"apply": 0} and doc["pending"] == [] and
            reinit == "APPROVAL_LEDGER_ALREADY_EXISTS" and after["spent"] == {"apply": 1},
            "两次 init=%s 账本份数=%d 开账时 spent=%s 已消费后再 init=%s 消费没被覆掉=%s" %
            (",".join(res), len(docs), doc["spent"], reinit, after["spent"] == {"apply": 1}))


def s99_store_failure_is_not_reported_as_success(m):
    """存盘失败如实传播: 旧 commit 忽略 `_store` 的返回值, 于是"没落成"也能打出已确认消耗。
    现在 reserve 与 commit 都在失败时具名停止, 且 pending 留在盘上按已消耗计(不退款)。"""
    root, path, ids, caps, led, obj = _tmp_ledger(m, "R99")
    saved_store = m.ApprovalLedger._store
    try:
        m.ApprovalLedger._store = lambda self, doc: False
        res_code, _ = _codes_of(obj.reserve, "apply", 1)
        doc_after_fail = json.loads(path.read_text(encoding="utf-8"))
        m.ApprovalLedger._store = saved_store
        obj.reserve("apply", 1)                          # 正常落成一次, 留下 pending
        doc = json.loads(path.read_text(encoding="utf-8"))
        res = {"name": "apply", "tag": doc["pending"][0]["id"], "kind": "business"}
        m.ApprovalLedger._store = lambda self, d: False
        commit_code, _ = _codes_of(obj.commit, res, True)
        still_pending = json.loads(path.read_text(encoding="utf-8"))["pending"]
        m.ApprovalLedger._store = saved_store
        obj.commit(res)
    finally:
        m.ApprovalLedger._store = saved_store
    events = [x["kind"] for x in _ledger_events(root / "l.jsonl")]
    led.close()
    return (res_code == "APPROVAL_LEDGER_WRITE_FAILED" and
            commit_code == "APPROVAL_LEDGER_WRITE_FAILED" and
            doc_after_fail["spent"] == {"apply": 0, "port_open": 0} and
            doc_after_fail["pending"] == [] and len(still_pending) == 1 and
            "APPROVAL_COMMIT_FAILED" in events and events.count("APPROVAL_COMMITTED") == 1,
            "预占存盘失败=%s(账未改=%s) 确认存盘失败=%s(pending 仍=%d) 事件=%s" %
            (res_code, doc_after_fail["spent"], commit_code, len(still_pending),
             ",".join(sorted(set(x for x in events if x.startswith("APPROVAL"))))))


def s100_historical_ledger_version_is_not_migrated(m):
    """版本门: v1(上一轮)账本结构上看着完整, 但缺 R1 的严格性 => 具名拒绝, 不当成满额新账,
    也不悄悄迁移成"已消耗 0"。"""
    root, path, ids, caps, led, obj = _tmp_ledger(m, "R100", reserve=("apply",))
    _mutate_ledger(path, lambda d: d.__setitem__("version", 1))
    code, _ = _codes_of(obj.snapshot)
    after = json.loads(path.read_text(encoding="utf-8"))
    led.close()
    return (code == "APPROVAL_LEDGER_VERSION" and after["version"] == 1 and
            after["spent"] == {"apply": 1, "port_open": 0},
            "v1 账本=%s 未被改写(version=%s spent=%s)" %
            (code, after["version"], after["spent"]))


# ----------------------------------------------------------------- R6A R2: 步骤绑定批准 / 额度映射
R2_QUOTA = {"port_open": 1, "motor_run": 1, "tmc_config_apply": 0, "irq_polarity_prep": 1,
            "retry": 0, "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
            "cleanup_motor_status": 1}


def _r2_session(m, steps, tag, transport="stub", approved=None, quota=None):
    """跑一条工装会话: 额度表就是本次要测的那份, 端口只有内存假口, 不碰设备。"""
    fx = motion_fx({"__watch__": ROWS["commands"]["__watch__"]["healthy"], "motor_start": []})
    caps = dict(quota or R2_QUOTA)
    qbox = dict(caps)
    try:
        return m.exec_session(tag, steps, out_dir("r2-" + tag), transport, fx, "MEMORY", 30,
                              m.SimClock(), motion_cap_seconds=5,
                              plan={"quota": dict(caps)}, quota_box=qbox, approved=approved)
    except m.Stop as e:                       # 开口之前就被门拒: 与审核探针同形
        return {"status": "STOP", "code": e.code, "detail": e.detail, "results": [],
                "cleanup_errors": [], "ledger": "", "quota_used": {}, "disarm": "NOT_ARMED"}


def s101_real_session_without_approved_package_sends_nothing(m):
    """复审 §4 的反例入口: 真实传输 + 任意单会话步骤。旧版会**照发** tmc_config_apply
    (cap=0 也只扣 port_open); 现在开口与第一条 write 都不会发生。"""
    opened = []
    saved = m.make_real_port
    m.make_real_port = lambda name, led: (opened.append(name),
                                          m.FakePort([], led, clock=m.SimClock()))[1]
    try:
        r = _r2_session(m, [{"cmd": "tmc_config_apply CONFIRM", "as": "apply",
                             "expect_contains": ["[TMC-CFG] CONFIGURED"]}],
                        "s101", transport="real")
    finally:
        m.make_real_port = saved
    return (r["code"] == "REAL_SESSION_REQUIRES_APPROVED_PACKAGE" and opened == [] and
            r["quota_used"] == {} and not r.get("ledger"),
            "code=%s 开口=%d 额度=%s 会话台账=%s(拒绝发生在建会话之前)" %
            (r["code"], len(opened), r["quota_used"], r.get("ledger") or "-"))


def s102_cli_single_session_real_is_refused(m):
    """CLI 装配层同一条门: --transport real 只有 --dispatch 一条路, 单会话 --steps rc=4 且不产出。"""
    import io
    import contextlib
    d = out_dir("r2-cli")
    sf = Path(d) / "steps.json"
    sf.write_text(json.dumps([{"cmd": "tmc_config_apply CONFIRM", "as": "apply",
                               "quota": {"name": "tmc_config_apply", "n": 1}}]), encoding="utf-8")
    buf_out, buf_err = io.StringIO(), io.StringIO()
    argv = ["--transport", "real", "--backend", "real", "--session", "S", "--steps", str(sf),
            "--out", str(d / "run"), "--approval-token", "T", "--expected-token", "T",
            "--approval-id", "R2-CLI"]
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        rc = m.main(argv)
    err = buf_err.getvalue()
    ran = (d / "run").exists()
    # stub 侧是同一条 CLI 的另一半: 夹具入口必须**照常可跑**(它不碰设备), 不能被真实门一起关掉。
    fxf = Path(d) / "fx.json"
    fxf.write_text(json.dumps({"boot_bytes": [], "responses": {
        "tmc_config_apply CONFIRM": ["[TMC-CFG] CONFIGURED"]}}), encoding="utf-8")
    qf = Path(d) / "plan.json"
    qf.write_text(json.dumps({"quota": {"port_open": 1, "tmc_config_apply": 1,
                                        "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                                        "cleanup_motor_status": 1}}), encoding="utf-8")
    se = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(se):
        stub_rc = m.main(["--transport", "stub", "--backend", "sim", "--session", "S",
                          "--steps", str(sf), "--out", str(d / "stub"), "--fixture", str(fxf),
                          "--plan", str(qf)])
    stub_err = se.getvalue()          # **单独**一个缓冲区: 否则上面那条真实拒绝的文本会混进来
    stub_tx = [x["bytes"].strip() for x in
               _ledger_events(d / "stub" / "S" / "ledger.jsonl") if x["kind"] == "TX"]
    return (rc == 4 and "single_session_steps_not_approved_package" in err and not ran and
            "tmc_config_apply CONFIRM" in stub_tx and
            "single_session_steps_not_approved_package" not in stub_err,
            "real rc=%d msg=%s 有输出目录=%s | stub rc=%d 发过=%s(工装入口仍在)" %
            (rc, err.strip().splitlines()[-1][:60] if err.strip() else "-", ran, stub_rc,
             ",".join(stub_tx)))


def s103_steps_changed_but_plan_bytes_unchanged(m):
    """批准件不动、递进来的步骤多一条 apply => STEPS_NOT_BOUND_TO_APPROVAL, 在开口之前。"""
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 1, "expect_absent": ["REFUSED"]}]
    approved = fx_approved(m, steps, sid="S103", tag="bound")
    opened = []
    saved = m.make_real_port
    m.make_real_port = lambda name, led: (opened.append(name),
                                          m.FakePort([], led, clock=m.SimClock()))[1]
    try:
        good = m.exec_session("S103-ok", stamp(m, steps), out_dir("r2-s103-ok"), "real",
                              motion_fx({"motor_start": []}), "MEMORY", 30, m.SimClock(),
                              motion_cap_seconds=5,
                              plan={"quota": dict(R2_QUOTA)}, quota_box=dict(R2_QUOTA),
                              approved=approved)
        tampered = steps + [{"cmd": "tmc_config_apply CONFIRM", "as": "extra"}]
        n_before = len(opened)
        bad = _r2_session(m, stamp(m, tampered), "s103", transport="real", approved=approved)
    finally:
        m.make_real_port = saved
    ev_good = [e["kind"] for e in _ledger_events(good["ledger"])] if good.get("ledger") else []
    return ("APPROVED_PACKAGE_VERIFIED" in ev_good and
            bad["code"] == "STEPS_NOT_BOUND_TO_APPROVAL" and len(opened) == n_before and
            bad["quota_used"] == {},
            "正控事件=%s 篡改=%s 篡改后开口=%d 额度=%s" %
            ("VERIFIED" if "APPROVED_PACKAGE_VERIFIED" in ev_good else ",".join(ev_good[:3]),
             bad["code"], len(opened) - n_before, bad["quota_used"]))


def s104_plan_file_edited_after_approval(m):
    """绑定之后有人改了 --plan 文件 => PLAN_BYTES_CHANGED_AFTER_APPROVAL(不是"跟着新字节走")。"""
    steps = [{"cmd": "motor_status", "as": "q"}]
    approved = fx_approved(m, steps, sid="S104", tag="drift")
    p = Path(approved["plan_path"])
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["stages"][0]["steps"].append({"cmd": "config_save", "as": "extra",
                                       "quota": {"name": "config_save", "n": 1}})
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    r = _r2_session(m, steps, "s104", transport="real", approved=approved)
    return (r["code"] == "PLAN_BYTES_CHANGED_AFTER_APPROVAL" and
            m.sha_of(p) != approved["plan_sha256"],
            "code=%s 盘上=%s… 批准=%s…" % (r["code"], m.sha_of(p)[:12],
                                          approved["plan_sha256"][:12]))


def s105_quota_map_refuses_before_send(m):
    """缺声明 / 错键 / n 不符 三种都在那条 write **之前**具名拒绝, 且额度一分没动。"""
    cases = {}
    for tag, st in (("缺声明", {"cmd": "tmc_config_apply CONFIRM", "as": "a"}),
                    ("错键", {"cmd": "tmc_config_apply CONFIRM", "as": "a",
                              "quota": {"name": "irq_polarity_prep", "n": 1}}),
                    ("n不符", {"cmd": "tmc_config_apply CONFIRM", "as": "a",
                               "quota": {"name": "tmc_config_apply", "n": 2}}),
                    ("n=0", {"cmd": "config_save", "as": "s",
                             "quota": {"name": "config_save", "n": 0}}),
                    ("n负数-未映射", {"cmd": "motor_status", "as": "q",
                                      "quota": {"name": "port_open", "n": -1}})):
        r = _r2_session(m, [st], tag, quota={"port_open": 1, "tmc_config_apply": 5,
                                             "config_save": 5, "irq_polarity_prep": 5,
                                             "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                                             "cleanup_motor_status": 1})
        cases[tag] = (r["code"], ",".join(tx_seq(r)) if r.get("ledger") else "-",
                      r["quota_used"])
    return (cases["缺声明"][0] == "QUOTA_STEP_UNDECLARED" and
            cases["错键"][0] == "QUOTA_KEY_MISMATCH" and
            cases["n不符"][0] == "QUOTA_N_MISMATCH" and
            cases["n=0"][0] == "QUOTA_N_MISMATCH" and
            cases["n负数-未映射"][0] == "QUOTA_N_INVALID" and
            all("tmc_config_apply CONFIRM" not in c[1] and "config_save" not in c[1]
                for c in cases.values()) and
            all(c[2] in ({}, {"port_open": 1}) for c in cases.values()),
            "; ".join("%s=%s tx=[%s] 额度=%s" % (k, v[0], v[1], v[2]) for k, v in cases.items()))


def s106_readonly_polling_is_not_charged_as_apply(m):
    """只读查询与有限轮询**不**进映射表: 不许把 motor_status 的重试扣成 apply 额度。"""
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 1, "expect_absent": ["REFUSED"]},
             {"cmd": "motor_status", "as": "run", "poll": {"codes": ["PROFILE_NOT_RUNNING"],
                                                            "expect_values": {}}},
             {"cmd": "diag_status", "as": "d"}]
    r = _r2_session(m, stamp(m, steps), "s106",
                    quota={"port_open": 1, "motor_run": 1, "retry": 0, "tmc_config_apply": 0,
                           "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                           "cleanup_motor_status": 1})
    tx = tx_seq(r)
    return (r["code"] not in ("QUOTA_STEP_UNDECLARED", "QUOTA_KEY_MISMATCH", "QUOTA_UNDECLINED")
            and "motor_status" in tx and "diag_status" in tx and
            r["quota_used"].get("motor_run") == 1 and
            "tmc_config_apply" not in r["quota_used"] and "retry" not in r["quota_used"],
            "code=%s tx=%s 额度=%s" % (r.get("code"), ",".join(tx), r["quota_used"]))


def s107_negative_quota_map_removed(m):
    """元负控: 摘掉命令->额度键映射(恒返回 None), 复审 §4 的反例就必须**放行**那条 apply ——
    否则 s105 的拒绝其实来自别的判据, 这条控制拦不住回退。"""
    saved = m.quota_key_for_command
    m.quota_key_for_command = lambda cmd: None
    try:
        r = _r2_session(m, [{"cmd": "tmc_config_apply CONFIRM", "as": "a"}], "s107",
                       quota={"port_open": 1, "tmc_config_apply": 5, "cleanup_motor_stop": 1,
                              "cleanup_motor_disarm": 1, "cleanup_motor_status": 1})
    finally:
        m.quota_key_for_command = saved
    tx = tx_seq(r)
    return ("tmc_config_apply CONFIRM" in tx and r.get("code") != "QUOTA_STEP_UNDECLARED",
            "摘表后 tx=%s code=%s(应为已发出)" % (",".join(tx), r.get("code")))


def s108_declared_zero_cap_apply_cannot_be_bypassed(m):
    """复审 §4 的探针形状: cap(tmc_config_apply)=0 且步骤带正确申报 => 在 write 之前拒,
    端口一次没开(额度先于开口已经扣了 port_open 那一格, 这正是既有纪律)。"""
    r = _r2_session(m, [{"cmd": "tmc_config_apply CONFIRM", "as": "a",
                         "quota": {"name": "tmc_config_apply", "n": 1}}], "s108")
    tx = tx_seq(r)
    return (r["code"] == "QUOTA_EXHAUSTED" and "tmc_config_apply CONFIRM" not in tx and
            r["disarm"] in ("CONFIRMED_DISABLED", "NOT_ARMED"),
            "code=%s tx=%s disarm=%s 额度=%s" % (r["code"], ",".join(tx), r["disarm"],
                                                 r["quota_used"]))


# ------------------------------------------------- R6A R3: 清理/观察窗/排空残留的字节完整性
R3_QUOTA = {"port_open": 1, "motor_run": 1, "retry": 0,
            "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1, "cleanup_motor_status": 1}


def _r3_run(m, status_rows, tag="s109", watch_rows=None):
    """业务起转 -> 清理三发, 清理回包按传入的 rows 注入(其余沿用交付的撤能块)。"""
    extra = {"motor_start": [], "__watch__": ROWS["commands"]["__watch__"]["healthy"],
             "motor_status": status_rows}
    if watch_rows is not None:
        extra["__watch__"] = watch_rows
    fx = motion_fx(extra)
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 1, "expect_absent": ["REFUSED"]}]
    if watch_rows is not None:
        steps.append({"cmd": "__watch__", "as": "w", "seconds": 1})
    r = m.exec_session(tag, stamp(m, steps), out_dir("r3-" + tag), "stub", fx, "NO_DEVICE", 30,
                       m.SimClock(), motion_cap_seconds=5,
                       plan={"quota": dict(R3_QUOTA)}, quota_box=dict(R3_QUOTA))
    return r


def _surrogate_feed(m):
    """只让**注入器**能写非法 UTF-8 字节(生产判读路径一行没动): 代理码点按 surrogateescape 编码。"""
    def hook_of(port, fixture):
        def hook(cmd):
            resp = fixture.get("responses", {}).get(cmd)
            if resp is not None:
                port.feed(("\r\n".join(resp) + "\r\nmsh >").encode("utf-8", "surrogateescape"))
        return hook
    return hook_of


def s109_cleanup_status_nul_is_not_proven_disable(m):
    """复审 §5 的反例: 清理用的 motor_status 回包里多一条独立裸 NUL。旧版 split_rows 只严格解码
    UTF-8(裸 NUL 合法) => 仍判 CONFIRMED_DISABLED/success=True。现在: 原字节保留、记独立证据错误、
    必要撤能照发、禁能一律 UNKNOWN、整场不成功。"""
    r = _r3_run(m, DISARM_ROWS + ["\x00"])
    raw = (Path(r["dir"]) / "cleanup_motor_status.bin").read_bytes()
    tx = tx_seq(r)
    return (raw.count(b"\x00") == 1 and r["disarm"] == "UNKNOWN" and r["success"] is False and
            any("NUL_BYTE_IN_RESPONSE" in e for e in r["cleanup_errors"]) and
            all(c in tx for c in ("motor_stop", "motor_disarm", "motor_status")),
            "NUL数=%d disarm=%s success=%s 清理错=%s tx=%s" %
            (raw.count(b"\x00"), r["disarm"], r["success"],
             ";".join(r["cleanup_errors"]), ",".join(tx)))


def s110_cleanup_illegal_bytes_named_and_disable_unknown(m):
    """清理回包里的非法 UTF-8: 具名 ILLEGAL_BYTES_IN_RESPONSE(不是被 replace 掉后继续证明),
    原字节仍逐字节留在证据文件里。"""
    saved = m._stub_feed
    m._stub_feed = _surrogate_feed(m)
    try:
        r = _r3_run(m, DISARM_ROWS + ["\udcf8"])
    finally:
        m._stub_feed = saved
    raw = (Path(r["dir"]) / "cleanup_motor_status.bin").read_bytes()
    return (b"\xf8" in raw and r["disarm"] == "UNKNOWN" and r["success"] is False and
            any("ILLEGAL_BYTES" in e for e in r["cleanup_errors"]),
            "原字节含 0xf8=%s disarm=%s success=%s 清理错=%s" %
            (b"\xf8" in raw, r["disarm"], r["success"], ";".join(r["cleanup_errors"])))


def s111_cleanup_danger_beats_illegal_and_bytes_kept(m):
    """同一份清理回包里既有 HardFault 又有非法字节: 记录的是**危险事实**(§5 要的优先级),
    且非法字节不许把它降成"可继续的正常结果"。"""
    saved = m._stub_feed
    m._stub_feed = _surrogate_feed(m)
    try:
        r = _r3_run(m, DISARM_ROWS + ["\udcf8", "HardFault at 0x00bd80b0"])
    finally:
        m._stub_feed = saved
    raw = (Path(r["dir"]) / "cleanup_motor_status.bin").read_bytes()
    errs = [e for e in r["cleanup_errors"] if "motor_status" in e]
    return (b"\xf8" in raw and b"HardFault" in raw and
            errs and errs[0].endswith("DANGER_TEXT_IN_STREAM") and
            r["disarm"] == "UNKNOWN" and r["success"] is False,
            "errs=%s disarm=%s success=%s 原字节含危险=%s 含非法=%s" %
            (";".join(errs), r["disarm"], r["success"], b"HardFault" in raw, b"\xf8" in raw))


def s112_watch_window_corruption_not_swallowed(m):
    """观察窗(被动流)里的裸 NUL 不再被吞成正常 NO_TRIGGER: 会话以具名 STOP 结束。"""
    r = _r3_run(m, DISARM_ROWS, tag="s112",
                watch_rows=ROWS["commands"]["__watch__"]["healthy"] + ["\x00"])
    raw = Path(r["dir"]) / "02_w.rx.bin"
    return (r["status"] == "STOP" and r["code"] == "NUL_BYTE_IN_RESPONSE" and
            raw.read_bytes().count(b"\x00") == 1 and r["success"] is False,
            "code=%s 窗内NUL=%d success=%s" % (r.get("code"),
                                              raw.read_bytes().count(b"\x00") if raw.exists()
                                              else -1, r["success"]))


def s113_pre_drained_bytes_keep_danger_gate(m):
    """发送前排走的残留被另存成 .pre.rx.bin, 但**不能因此过了危险门**: 先存证, 再在下一条
    业务 write 之前停 —— 这一刻端口一次写都没有。"""
    steps = [{"cmd": "motor_status", "as": "q"}]
    writes = []
    saved = m.make_real_port

    class PreLoaded(m.FakePort):
        def write(self, data):
            writes.append(data.decode("ascii").strip())
            return m.FakePort.write(self, data)

    m.make_real_port = lambda name, led: PreLoaded([b"HardFault\r\n"], led, clock=m.SimClock())
    try:
        try:
            r = m.exec_session("s113", stamp(m, steps), out_dir("r3-s113"), "real", {},
                               "MEMORY", 30, m.SimClock(),
                               plan={"quota": dict(BASE_QUOTA)}, approved=fx_approved(m, steps,
                                                                                      sid="s113"))
        except m.Stop as e:
            r = {"status": "STOP", "code": e.code, "results": [], "cleanup_errors": [],
                "ledger": "", "dir": ""}
    finally:
        m.make_real_port = saved
    pres = list(Path(r["dir"]).glob("*.pre.rx.bin")) if r.get("dir") else []
    kept = bool(pres) and b"HardFault" in pres[0].read_bytes()
    return (r["code"] == "DANGER_TEXT_IN_STREAM" and writes == [] and kept,
            "code=%s 业务写=%s 残留存证=%s" % (r["code"], writes, kept))


def s114_negative_split_rows_entry_gate_removed(m):
    """元负控: 摘掉集中式的原始字节门(guard_stream_bytes + guard_partial_bytes, 普通响应/清理/
    观察窗/排空四条入口共用它), §5 的清理 NUL 反例就必须**重新变成** CONFIRMED_DISABLED 且整场
    成功 —— 否则那条 UNKNOWN 其实来自别的判据, 这次修的正控没有区分力。"""
    saved, saved2 = m.guard_stream_bytes, m.guard_partial_bytes
    m.guard_stream_bytes = lambda raw, *a, **k: raw
    m.guard_partial_bytes = lambda raw, *a, **k: raw
    try:
        r = _r3_run(m, DISARM_ROWS + ["\x00"], tag="s114")
    finally:
        m.guard_stream_bytes, m.guard_partial_bytes = saved, saved2
    return (r["disarm"] == "CONFIRMED_DISABLED" and r["success"] is True and
            not r["cleanup_errors"],
            "摘门后 disarm=%s success=%s 清理错=%s(应为正常, 说明判据在门上)" %
            (r["disarm"], r["success"], ";".join(r["cleanup_errors"]) or "-"))


# ------------------------------------------------- R6B B1: 账本类型损坏与必要撤能/关闭的隔离
B1_QUOTA = {"port_open": 1, "motor_run": 1, "cleanup_motor_stop": 1,
            "cleanup_motor_disarm": 1, "cleanup_motor_status": 1}


def _b1_session(m, tag, mutate=None, patch=None):
    """复刻复审 R6B §4 的探针形状: 完整临时账 + 内存端口, 起转发出**之后**再改盘上的账。
    返回 (结果或None, 逸出的异常repr或None, tx序列, 端口是否关闭, 台账事件)。"""
    root = out_dir("r6b-" + tag)
    clock = m.SimClock()
    ids = full_ids(900 + len(tag))
    path = m.approval_init(root, tag, ids, dict(B1_QUOTA),
                           note="R6B B1 临时离线探针, 不产生任何现场批准")
    led = m.Ledger(root / "approval-events.jsonl", clock, out_dir=root)
    approval = m.ApprovalLedger(path, tag, ids, dict(B1_QUOTA), led, clock)
    ports = []
    old_port = m.FakePort

    class Recorder(old_port):
        def __init__(self, *a, **k):
            super(Recorder, self).__init__(*a, **k)
            ports.append(self)

        def write(self, data):
            n = super(Recorder, self).write(data)
            if mutate and data.strip() == b"motor_start":
                doc = json.loads(Path(path).read_text(encoding="utf-8"))
                mutate(doc)
                Path(path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return n

    m.FakePort = Recorder
    saved = {}
    if patch:
        patch(m, saved)
    result, raised = None, None
    try:
        result = m.exec_session(tag, [{"cmd": "motor_start", "as": "start", "seconds": 1,
                                       "quota": {"name": "motor_run", "n": 1},
                                       "expect_absent": ["REFUSED"]}],
                                root, "stub", motion_fx({"motor_start": []}), "MEMORY_ONLY",
                                30, clock, motion_cap_seconds=5, plan={"quota": dict(B1_QUOTA)},
                                quota_box=dict(B1_QUOTA), approval=approval)
    except Exception as e:
        raised = "%s:%s" % (e.__class__.__name__, e)
    finally:
        for k, v in saved.items():
            setattr(v[0], k, v[1])
        m.FakePort = old_port
        led.close()
    evs = [json.loads(s) for s in (Path(root) / tag / "ledger.jsonl").read_text(
        encoding="utf-8").splitlines()]
    tx = [e["bytes"].strip() for e in evs if e["kind"] == "TX"]
    return (result, raised, tx, (ports[0]._closed if ports else None), evs, Path(path))


def _must_raise_TypeError(v):
    """元证据: 未受保护的成员查找对该值**确实**炸普通异常 —— 说明类型门不是空转的装饰。"""
    try:
        v in {"apply": 1}
        return None
    except TypeError as e:
        return "TypeError:%s" % e


def s115_ledger_type_corruption_is_named_not_python_error(m):
    """复审 §4 反例1: `pending[i].name` 是 list/int/{}/空串时, 旧版在**类型敏感操作**
    (成员查找)里直接抛 TypeError。现在一律 APPROVAL_LEDGER_CORRUPT 具名拒绝, 且盘上坏账
    一字不改(不修复、不清零、不迁移)。"""
    bad = ([], 5, {}, "")
    codes, details, hazards = [], [], []
    for i, v in enumerate(bad):
        root, path, ids, caps, led, obj = _tmp_ledger(m, "R115%d" % i,
                                                      caps=dict(B1_QUOTA), reserve=("motor_run",))
        _mutate_ledger(path, lambda d, v=v: d["pending"][-1].__setitem__("name", v)
                       if d["pending"] else None)
        code, err = _codes_of(obj.snapshot)
        codes.append(code)
        details.append(getattr(err, "detail", ""))
        hazards.append(_must_raise_TypeError(v))
        after = json.loads(Path(path).read_text(encoding="utf-8"))
        led.close()
        if code != "APPROVAL_LEDGER_CORRUPT" or "pending[0]_name_form" not in details[-1]:
            return False, "name=%r 得到 %s/%s" % (v, code, details[-1])
        if after["pending"][-1]["name"] != v:
            return False, "坏账被改写了: %r" % (after["pending"][-1],)
    return (codes == ["APPROVAL_LEDGER_CORRUPT"] * 4 and hazards[0] and hazards[2] and
            hazards[1] is None and hazards[3] is None,
            "codes=%s 元证据(未保护成员查找)=%s 详情=%s" %
            (",".join(codes), ";".join(str(h) for h in hazards),
             " | ".join(d[:44] for d in details)))


def s116_corrupt_ledger_still_runs_all_disarm_and_closes(m):
    """复审 §4 主反例(端到端): 起转发出后把盘上 pending.name 改成 []。修前: TypeError 逸出,
    只发 motor_start, 三条必要撤能一条不发, 端口不关。修后与"负数计数"那条对照同形:
    四条都发、端口关闭、具名拒绝留在账上、整场不是成功。"""
    r, raised, tx, closed, evs, path = _b1_session(
        m, "S116", mutate=lambda d: d["pending"][-1].__setitem__("name", []))
    frame = [e for e in evs if e["kind"] == "CLEANUP_FRAME_EXCEPTION"]
    ok_tx = ["motor_stop", "motor_disarm", "motor_status"]
    return (raised is None and r is not None and all(c in tx for c in ok_tx) and
            closed is True and not frame and r["success"] is False and
            any("APPROVAL_LEDGER_CORRUPT" in x for x in r["cleanup_overruns"]) and
            json.loads(path.read_text(encoding="utf-8"))["pending"][-1]["name"] == [],
            "raised=%s tx=%s closed=%s 框架异常=%d success=%s 披露=%s 坏账仍=%s" %
            (raised, ",".join(tx), closed, len(frame), r["success"] if r else None,
             ";".join(r["cleanup_overruns"]) if r else "-",
             json.loads(path.read_text(encoding="utf-8"))["pending"][-1]["name"]))


def s117_reserve_plain_exception_does_not_skip_disarm(m):
    """复审 §4.2: 预占抛**普通**异常(不是 Stop)时也不许离开清理框架 —— 三条必要撤能照发,
    端口照关, 但落成 APPROVAL_RESERVE_FRAME_ERROR_* 具名披露, 本会话不算成功。"""
    def apply_patch(mm, saved):
        orig = mm.ApprovalLedger.reserve

        def boom(self, name, n=1, kind="business"):
            if str(name).startswith("cleanup_"):
                raise RuntimeError("注入: 账本预占层的非 Stop 异常")
            return orig(self, name, n, kind=kind)
        saved["reserve"] = (mm.ApprovalLedger, orig)
        mm.ApprovalLedger.reserve = boom
    r, raised, tx, closed, evs, _p = _b1_session(m, "S117", patch=apply_patch)
    return (raised is None and all(c in tx for c in
                                   ("motor_stop", "motor_disarm", "motor_status")) and
            closed is True and r["success"] is False and
            all("APPROVAL_RESERVE_FRAME_ERROR_RuntimeError_SENT_ANYWAY" in x
                for x in r["cleanup_overruns"]),
            "raised=%s tx=%s closed=%s 披露=%s success=%s" %
            (raised, ",".join(tx), closed, ";".join(r["cleanup_overruns"]), r["success"]))


def s118_commit_plain_exception_still_closes_port(m):
    """复审 §4.3: 确认消耗(存盘)炸普通异常时, 端口与日志的关闭不能被跳过。
    关端口只是交还句柄, **不等于**证得禁能 —— 断言看的是 PORT_CLOSED 事件与 success=False。"""
    def apply_patch(mm, saved):
        orig = mm.ApprovalLedger.commit

        def boom(self, res, ok=True):
            raise RuntimeError("注入: 账本确认层的非 Stop 异常")
        saved["commit"] = (mm.ApprovalLedger, orig)
        mm.ApprovalLedger.commit = boom
    r, raised, tx, closed, evs, _p = _b1_session(m, "S118", patch=apply_patch)
    kinds = [e["kind"] for e in evs]
    return (raised is None and closed is True and "PORT_CLOSED" in kinds and
            "APPROVAL_COMMIT_FRAME_ERROR_RuntimeError" in r["cleanup_errors"] and
            r["success"] is False and r["disarm"] == "CONFIRMED_DISABLED",
            "raised=%s closed=%s PORT_CLOSED=%s 错误=%s disarm=%s success=%s" %
            (raised, closed, "PORT_CLOSED" in kinds, ";".join(r["cleanup_errors"][:3]),
             r["disarm"], r["success"]))


# ------------------------------------------------- R6B B2: 整包执行对象与批准字节同源
B2_ARGV = "MEMORY_ONLY_UNAPPROVED_ARGUMENTS"


def _b2_dispatch(m, tag, snapshot=None, patch_read=None):
    """复审 §5 的形状: 盘上是批准的那份计划, 内存对象被改成"只剩 S1 + download.argv 换掉"。
    动作后端换成只记录调用的内存替身(不起子进程、不碰设备)。返回 (结果, 收到的 argv 列表)。"""
    root = out_dir("r6b-" + tag)
    approved = json.loads(json.dumps(PLAN))
    path = Path(root) / "approved-plan.json"
    path.write_text(json.dumps(approved, ensure_ascii=False), encoding="utf-8")
    ids = m.current_identities(approved, str(path), str(REPO))
    m.approval_init(root, tag, ids, approved["quota"],
                    note="R6B B2 临时离线探针, 不产生任何现场批准")
    live = json.loads(json.dumps(approved))
    live["stages"] = [s for s in live["stages"] if s["id"] == "S1"]
    live["tools"]["download"]["argv"] = [B2_ARGV]
    calls = []

    class MemoryBackend(m.ActionBackend):
        kind = "memory-only"

        def __init__(self, *a, **k):
            self.attempts = []

        def download(self, act, ctx):
            calls.append(act["argv"])
            self.note_attempt("download", "IN_MEMORY")
            return {"rc": 0}

    old = m.RealActionBackend
    old_snap = getattr(m, "read_plan_snapshot", None)
    if patch_read:
        m.read_plan_snapshot = patch_read(path, approved, live, m)
    m.RealActionBackend = MemoryBackend
    try:
        res = m.dispatch_plan(live, "real", "real", Path(root) / "run", m.SimClock(),
                              m.REAL_PROFILE, "MEMORY_NO_PORT", str(REPO), "TEMP_TEST",
                              "TEMP_TEST", plan_dir=str(HERE), approval_id=tag,
                              approval_root=root, plan_path=str(path), plan_snapshot=snapshot)
    finally:
        m.RealActionBackend = old
        if old_snap is not None:
            m.read_plan_snapshot = old_snap
    return res, calls, path, approved


def s119_memory_plan_not_from_snapshot_reaches_no_backend(m):
    """复审 §5.1: 批准账绑的是文件那份字节, 调用方却给执行引擎塞了另一份内存对象
    (阶段被砍、download.argv 换掉)。修前: 未批准的 argv 到达 download 后端并扣 download=1,
    dispatch=OK。修后: PLAN_NOT_FROM_APPROVED_SNAPSHOT 具名拒绝, 后端零调用, 额度一格不扣。"""
    res, calls, path, approved = _b2_dispatch(m, "B119")
    spent = (res.get("approval") or {}).get("spent")
    return (res["status"] == "REFUSED" and res.get("code") == "PLAN_NOT_FROM_APPROVED_SNAPSHOT"
            and calls == [] and json.loads(path.read_text(encoding="utf-8")) == approved and
            (spent is None or spent.get("download", 0) == 0),
            "status=%s code=%s 后端调用=%s 盘上计划仍=批准那份=%s spent=%s" %
            (res["status"], res.get("code"), calls,
             json.loads(path.read_text(encoding="utf-8")) == approved, spent and spent.get("download")))


def s120_negative_snapshot_gate_removed(m):
    """元负控(§7.2.5): 把快照门摘回"两次独立读取"的旧形状(身份用文件的 SHA, 执行对象用内存
    那份) —— 未批准的 argv 必须**重新到达后端**。这条不亮就说明 s119 的判据是空转的。"""
    def patch_read(path, approved, live, mm):
        def fake(p):
            return {"path": str(p), "bytes": 0, "sha256": mm.sha_of(str(path)), "doc": live}
        return fake
    res, calls, _p, _a = _b2_dispatch(m, "B120", patch_read=patch_read)
    return (calls == [[B2_ARGV]] or calls == [B2_ARGV]) and res["status"] != "REFUSED", \
        "摘门后 后端调用=%s status=%s code=%s" % (calls, res["status"], res.get("code"))


def s121_cli_read_swap_cannot_bypass_identity(m):
    """复审 §5.2(CLI 可达): 账绑 P 的字节; 计划文件先被换成 Q, 执行器读完后文件再换回 P。
    修前: 现算的 SHA 是 P(与账相符)而跑的是 Q => rc=0 且未批准 argv 到达后端。
    修后: 身份哈希与执行对象出自**同一次读盘**的那份字节 => 账本身份不符, 在工具动作前拒绝。"""
    root = out_dir("r6b-B121")
    (Path(root) / "rows.json").write_bytes((HERE / "rows.json").read_bytes())
    approved = json.loads(json.dumps(PLAN))
    approved_bytes = json.dumps(approved, ensure_ascii=False).encode("utf-8")
    path = Path(root) / "plan.json"
    path.write_bytes(approved_bytes)
    ids = m.current_identities(approved, str(path), str(REPO))
    m.approval_init(root, "B121", ids, approved["quota"],
                    note="R6B B2 临时离线探针, 不产生任何现场批准")
    live = json.loads(json.dumps(approved))
    live["stages"] = [s for s in live["stages"] if s["id"] == "S1"]
    live["tools"]["download"]["argv"] = [B2_ARGV]
    path.write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")   # 现在是 Q
    calls, switched = [], []
    old_read, old_backend = m.Path.read_bytes, m.RealActionBackend

    def interleaved(p, *a, **k):
        raw = old_read(p, *a, **k)
        if str(p) == str(path) and not switched:
            path.write_bytes(approved_bytes)      # 读出 Q 之后、身份核对之前换回 P
            switched.append(True)
        return raw

    class MemoryBackend(m.ActionBackend):
        kind = "memory-only"

        def __init__(self, *a2, **k2):
            self.attempts = []

        def download(self, act, ctx):
            calls.append(act["argv"])
            self.note_attempt("download", "IN_MEMORY")
            return {"rc": 0}

    m.Path.read_bytes = interleaved
    m.RealActionBackend = MemoryBackend
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = m.main(["--dispatch", "--transport", "real", "--backend", "real",
                         "--profile", m.REAL_PROFILE, "--plan", str(path),
                         "--out", str(Path(root) / "run"), "--repo-root", str(REPO),
                         "--approval-token", "TEMP_TEST", "--expected-token", "TEMP_TEST",
                         "--approval-id", "B121", "--approval-root", str(root)])
    finally:
        m.Path.read_bytes = old_read
        m.RealActionBackend = old_backend
    txt = out.getvalue()
    refused = "code=APPROVAL_IDENTITY_MISMATCH" in txt or "APPROVAL_IDENTITY_MISMATCH" in txt
    return (bool(switched) and rc != 0 and calls == [] and refused and path.read_bytes() ==
            approved_bytes,
            "交错发生=%s rc=%s 后端调用=%s 具名拒绝=%s 盘上=批准那份=%s" %
            (switched, rc, calls, refused, path.read_bytes() == approved_bytes))


CASES = [
    ("FS01", "提示符识别", s01_prompt_recognized),
    ("FS02", "缺提示符不算完整", s02_missing_prompt_is_not_complete),
    ("FS03", "非法字节具名", s03_illegal_bytes_named),
    ("FS04", "重复字段拒绝", s04_duplicate_field_refused),
    ("FS05", "真C的learnFAILED是业务失败", s05_real_c_learn_failure_is_business_stop),
    ("FS06", "具名拒绝后commit/save不发", s06_named_refusal_halts_commit),
    ("FS07", "真C成功行被接受", s07_real_c_success_accepted),
    ("FS08", "watchSTOP后危险步骤不发", s08_watch_stop_halts_dangerous_steps),
    ("FS09", "未触发是观察终态", s09_no_trigger_is_observation),
    ("FS10", "5秒硬截止", s10_five_second_hard_cap),
    ("FS11", "起转后出错仍撤能", s11_error_after_start_still_disarms),
    ("FS12", "不可证实禁能UNKNOWN", s12_unconfirmable_disable_is_unknown),
    ("FS13", "真传输人工门绝不读夹具", s13_real_manual_never_uses_fixture),
    ("FS14", "无人应答时人工门停止", s14_real_manual_without_operator_stops),
    ("FS15", "人工否决后无运动命令", s15_denied_manual_runs_no_motion),
    ("FS16", "动作真被调度", s16_actions_really_dispatched),
    ("FS17", "manual后端要交付物", s17_manual_backend_requires_deliverables),
    ("FS18", "manual回读散列门", s18_manual_readback_hash_gate),
    ("FS19", "静默端口不重试", s19_silent_port_no_retry),
    ("FS20", "needles回C源码证明", s20_needles_proven_against_c),
    ("FS21", "开口后不写端口属性", s21_static_port_discipline),
    ("FS22", "额度耗尽拒发", s22_quota_exhausted_refuses_send),
    ("FS23", "反向-去硬截止", s23_negative_five_second_cap),
    ("FS24", "反向-去撤能清理", s24_negative_cleanup_gate),
    ("FS25", "反向-去业务失败层", s25_negative_business_layer),
    ("FS26", "R3B1写失败仍撤能", s26_write_failure_still_disarms),
    ("FS27", "R3B2写耗时计入截止", s27_write_cost_inside_cap),
    ("FS28", "R3B3stop静默不吃disarm机会", s28_stop_silent_does_not_eat_disarm),
    ("FS29", "R3B4运动窗内人工门开口前拒绝", s29_manual_after_motion_refused_before_open),
    ("FS30", "R3B5真机六字段证禁能OTHER", s30_real_disable_rows_confirm),
    ("FS31", "R3B5锁存FAULT仍可证禁能", s31_latched_fault_disable_still_provable),
    ("FS32", "夹具无IDLE或CRUISE行", s31b_no_capture_row_claims_idle_or_cruise),
    ("FS33", "arm应答丢失仍清理", s32_arm_ack_lost_cleans_up),
    ("FS34", "清理查询静默UNKNOWN", s33_cleanup_query_silent_is_unknown),
    ("FS35", "清理字段重复UNKNOWN", s34_duplicate_field_is_unknown),
    ("FS36", "当前请求仍HIGH则UNKNOWN", s35_current_request_still_high_is_unknown),
    ("FS37", "RUNNING与AT_REST两方向", s36_running_and_rest_profiles_both_directions),
    ("FS38", "逐值判据拒绝错值", s37_expect_values_rejects_wrong_value),
    ("FS39", "判读文本真的落盘", s38_parse_text_actually_written),
    ("FS40", "证据失败具名并终止业务", s39_evidence_failure_is_named_and_halts),
    ("FS41", "清理预留独立扣减与耗尽披露", s40_cleanup_reserve_charged_and_disclosed),
    ("FS42", "整包healthy走到commit与save", s41_dispatch_healthy_reaches_commit_and_save),
    ("FS43", "整包未触发与真实触发两支", s42_dispatch_no_trigger_and_trip_branches),
    ("FS44", "取证与恢复会话不起转", s43_dispatch_forensics_sessions_never_start),
    ("FS45", "阶段失败后续一律NOT_RUN", s44_dispatch_stops_and_lists_not_run),
    ("FS46", "重启恢复漂移具名停止", s45_restore_mismatch_is_named),
    ("FS47", "新增session不增大就停", s46_new_session_must_actually_increase),
    ("FS48", "占位与禁止命令被拒绝", s47_plan_placeholder_and_forbidden_rejected),
    ("FS49", "无批准令牌时real一条不发", s48_real_transport_without_token_sends_nothing),
    ("FS50", "argv占位未展开拒跑", s49_real_argv_tokens_refuse_to_run),
    ("FS51", "learn撞12s超时仍具名清理", s50_learn_timeout_keeps_named_stop),
    ("FS52", "反向-清理退回共享截止", s51_negative_cleanup_single_window),
    ("FS53", "反向-禁能退回只认IDLE", s52_negative_idle_only_disable),
    ("FS54", "反向-义务推到写成功之后", s53_negative_obligation_after_write),
    ("FS55", "反向-摘掉静止运行一致性判据", s54_negative_profile_check_removed),
    ("FS56", "回读后Halted先复位再查询", s56_halted_readback_reset_before_first_query),
    ("FS57", "Real后端零设备注入(P0原文)", s57_real_backend_zero_device_injection),
    ("FS58", "UID不入公开件双身份登记", s58_uid_stays_out_of_public_evidence),
    ("FS59", "stop读异常仍撤能并关端口", s59_stop_read_exception_still_disarms),
    ("FS60", "短写不算成功且义务仍成立", s60_short_write_is_not_success),
    ("FS61", "开口额度先于尝试且失败留痕", s61_open_quota_charged_before_attempt),
    ("FS62", "预期拒绝不豁免清理失败", s62_expected_refusal_never_waives_cleanup_failure),
    ("FS63", "模式混搭在动作前具名拒绝", s63_mode_combinations_refused_before_any_action),
    ("FS64", "签名不钉数字漂移仍拒", s64_signature_not_hardcoded_but_drift_still_refused),
    ("FS65", "非法flags按位被拒绝", s65_flags_by_bit_reject_illegal_commit),
    ("FS66", "轮询证得CRUISE与矛盾拒绝", s66_running_needs_numeric_cruise_and_bounded_polling),
    ("FS67", "忙msh的12秒真实边界", s67_busy_msh_twelve_second_boundary),
    ("FS68", "真实分支由响应决定", s68_response_driven_branch_and_undetermined_stop),
    ("FS69", "无实际帧即取证未完成", s69_new_session_without_frames_is_not_forensics),
    ("FS70", "反向-摘掉按位资格判定", s70_negative_flag_gate_removed),
    ("FS71", "反向-摘掉忙msh模型", s71_negative_busy_model_removed),
    ("FS72", "反向-轮询退回单发查询", s72_negative_poll_removed),
    ("FS73", "反向-复位后不消费启动流", s73_negative_boot_drain_removed),
    ("FS74", "回读落盘名不被去转义(shlex 安全)", s74_run_root_inside_commander_c_is_shlex_safe),
    ("FS75", "反向-回读根退回反斜杠形式", s75_negative_backslash_run_root_removed),
    ("FS76", "learn must_min 各>=8 正反例与元负控入链", s76_learn_must_min_matrix),
    ("FS77", "停后帧事件码取 5 且不全局替换 9", s77_forensic_event_is_multi_source_five),
    ("FS78", "判据退回 9 时 event5 帧被具名拒绝", s78_negative_event_pin_back_to_nine),
    ("FS79", "反向-摘掉 event 腿则 event9 放行", s79_negative_event_leg_removed),
    ("FS80", "干净响应在字节硬门下仍通过", s80_clean_response_still_passes),
    ("FS81", "裸NUL硬停止且原件不改", s81_bare_nul_hard_stop_bytes_kept),
    ("FS82", "危险文本优先于解码失败", s82_danger_text_beats_decode_failure),
    ("FS83", "恢复合同申报被拒且后续不发", s83_declared_recovery_refused_and_next_step_not_sent),
    ("FS84", "离线门拒绝恢复合同申报", s84_plan_gate_refuses_recovery_declaration),
    ("FS85", "换输出目录/重开进程不复活额度", s85_quota_not_revived_by_new_dir_or_new_process),
    ("FS86", "最后额度占用后崩溃不退款", s86_last_unit_reserved_then_crash_is_not_refunded),
    ("FS87", "双执行者共用一账不超cap且守锁", s87_two_executors_on_one_approval_never_exceed_cap),
    ("FS88", "台账缺失/损坏拒绝真实动作", s88_missing_or_corrupt_ledger_blocks_real_action),
    ("FS89", "身份任一变化不得沿用同一批准", s89_changed_identity_cannot_reuse_the_same_approval),
    ("FS90", "额度耗尽仍执行撤能并留痕", s90_business_exhausted_but_disarm_still_runs),
    ("FS91", "反向-摘掉账本绑定门", s91_negative_ledger_gate_removed),
    ("FS92", "反向-摘掉账本 cap 判定", s92_negative_cap_check_removed),
    ("FS93", "缺身份SHA即拒不真值跳过", s93_missing_identity_sha_is_refused),
    ("FS94", "缺spent字段不当成满额", s94_missing_spent_field_is_not_full_quota),
    ("FS95", "非法计数与非法n全部具名拒", s95_illegal_counts_are_rejected),
    ("FS96", "绑定后删账不重建满额", s96_deleted_ledger_cannot_be_rebuilt),
    ("FS97", "缺SHA/缺cap开不出也用不了", s97_incomplete_identity_or_cap_cannot_open_or_reuse),
    ("FS98", "双init竞争只有一份账不退款", s98_two_inits_cannot_both_open_the_same_approval),
    ("FS99", "存盘失败不谎称已落成", s99_store_failure_is_not_reported_as_success),
    ("FS100", "历史版本账具名拒绝不迁移", s100_historical_ledger_version_is_not_migrated),
    ("FS101", "真实单会话无批准件一条不发", s101_real_session_without_approved_package_sends_nothing),
    ("FS102", "CLI真实单会话入口被拒rc4", s102_cli_single_session_real_is_refused),
    ("FS103", "改步骤不改计划字节被拒绝", s103_steps_changed_but_plan_bytes_unchanged),
    ("FS104", "绑定后计划文件漂移拒发", s104_plan_file_edited_after_approval),
    ("FS105", "缺声明错键n不符写前拒", s105_quota_map_refuses_before_send),
    ("FS106", "只读轮询不计成apply额度", s106_readonly_polling_is_not_charged_as_apply),
    ("FS107", "反向-摘掉命令额度映射表", s107_negative_quota_map_removed),
    ("FS108", "cap为0的apply申报也拒发", s108_declared_zero_cap_apply_cannot_be_bypassed),
    ("FS109", "清理status裸NUL不证禁能", s109_cleanup_status_nul_is_not_proven_disable),
    ("FS110", "清理非法字节具名且UNKNOWN", s110_cleanup_illegal_bytes_named_and_disable_unknown),
    ("FS111", "清理危险优先且原字节保留", s111_cleanup_danger_beats_illegal_and_bytes_kept),
    ("FS112", "观察窗损坏不被吞成未触发", s112_watch_window_corruption_not_swallowed),
    ("FS113", "排空残留过危险门后才写", s113_pre_drained_bytes_keep_danger_gate),
    ("FS114", "反向-摘掉split_rows入口门", s114_negative_split_rows_entry_gate_removed),
    ("FS115", "账本类型损坏具名拒绝不逸出", s115_ledger_type_corruption_is_named_not_python_error),
    ("FS116", "坏账仍完成三条撤能并关端口", s116_corrupt_ledger_still_runs_all_disarm_and_closes),
    ("FS117", "预占普通异常不中断清理", s117_reserve_plain_exception_does_not_skip_disarm),
    ("FS118", "确认普通异常仍关端口与日志", s118_commit_plain_exception_still_closes_port),
    ("FS119", "内存计划非批准快照拒发", s119_memory_plan_not_from_snapshot_reaches_no_backend),
    ("FS120", "反向-摘掉快照门", s120_negative_snapshot_gate_removed),
    ("FS121", "CLI读取换文件被身份门拒", s121_cli_read_swap_cannot_bypass_identity),
]


def main():
    m = load(CHAIN, "m8r3_chain_under_test")
    ids = [c[0] for c in CASES]
    if len(set(ids)) != len(ids):
        raise AssertionError("场景 id 重复")
    if ids[:25] != ["FS%02d" % i for i in range(1, 26)]:
        raise AssertionError("R2 的 25 个身份必须原序保留")
    passed = 0
    for cid, title, fn in CASES:
        if case(cid, lambda fn=fn: fn(m)):
            passed += 1
    print("SUMMARY|cases=%d pass=%d fail=%d executed=%d preserved_r2_identities=25|"
          "negative_injections=%d|hardware_actions=0|transport=stub_only|port=IN_MEMORY" %
          (len(CASES), passed, len(CASES) - passed, len(RESULT),
           sum(1 for c in CASES if c[1].startswith("反向"))))
    print("SELFTEST_WORKDIR|" + str(WORK))
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
