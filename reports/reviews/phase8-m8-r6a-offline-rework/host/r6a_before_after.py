#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6A 四个反例的**修前/修后**同输入对照(复审 M8-R6A §7 要求交付"各反例的修前/修后")。

做法: 把基线头 44e493c 里的执行器 blob 取到**仓库外**的一次性副本, 与盘上修后的执行器
用**同一个探针函数**跑**同一份输入**, 两边各自打印实际结果。探针不读串口、不连 DAP、
不下载、不复位: 端口全是内存假口, 账本全是临时目录里的一次性文件。

退出码: 0 = 六个反例在修后全部被拒且修后行为符合合同; 1 = 有任何一条不符合。
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]      # .../host/<本文件> -> reports/reviews/仓库根
BASE = "44e493cea9bbbc35e18394ca23dd1a8502519448"
REL_EXEC = ("reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py")
REL_ROWS = "reports/reviews/phase8-m8-r6a-offline-rework/field/rows.json"
REL_MOTOR = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
             "ns800rt7p65-nssinepad/applications/motor.c")
EXEC_LIVE = REPO / REL_EXEC
SCRATCH = Path(tempfile.mkdtemp(prefix="m8r6a-before-after-"))
DISARM_KEY = "motor_status"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def baseline_executor():
    r = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (BASE, REL_EXEC)],
                       cwd=str(REPO), capture_output=True)
    if r.returncode != 0:
        raise SystemExit("BASELINE_BLOB_FAILED rc=%d %s" % (r.returncode, r.stderr[:120]))
    p = SCRATCH / ("m8r3_serial_chain_at_%s.py" % BASE[:7])
    p.write_bytes(r.stdout)
    return p, len(r.stdout), hashlib.sha256(r.stdout).hexdigest()


def full_ids(m, seed=7):
    return {"plan_sha256": "%064x" % seed, "executor_sha256": "%064x" % (seed + 1),
            "image_sha256": "%064x" % (seed + 2)}


_SEQ = [0]


def uniq(prefix):
    _SEQ[0] += 1
    return SCRATCH / ("%s-%02d" % (prefix, _SEQ[0]))


# ---------------------------------------------------------------- R1 四个账本反例
def probe_ledger(m, mutate, tag):
    """开一本临时账 -> 预占并确认耗掉唯一一次 -> 按 §3 的表改账本 -> 再预占。返回可判读的一行。"""
    root = uniq("led-%s" % tag)
    root.mkdir(parents=True, exist_ok=True)
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    caps = {"apply": 1}
    ids = full_ids(m)
    try:
        m.approval_init(root, tag, ids, caps, note="probe, no device")
    except m.Stop as e:
        led.close()
        return {"init": "REFUSED_" + e.code}
    path = m.approval_path(root, tag)
    obj = m.ApprovalLedger(path, tag, ids, caps, led, m.SimClock())
    out = {"init": "OK"}
    try:
        res = obj.reserve("apply", 1)
        obj.commit(res)
        out["first"] = "reserved+committed spent=%s" % json.loads(
            path.read_text(encoding="utf-8"))["spent"]
    except m.Stop as e:
        out["first"] = "REFUSED_" + e.code
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1),
                    encoding="utf-8")
    try:
        snap = obj.snapshot()
        out["snapshot"] = "OK spent=%s" % snap["spent"]
    except Exception as e:                                       # noqa: BLE001
        out["snapshot"] = "REFUSED_" + getattr(e, "code", e.__class__.__name__)
    try:
        res = obj.reserve("apply", 1)
        out["again"] = "ALLOWED tag=%s spent=%s" % (
            res["tag"], json.loads(path.read_text(encoding="utf-8"))["spent"])
    except Exception as e:                                       # noqa: BLE001
        out["again"] = "REFUSED_" + getattr(e, "code", e.__class__.__name__)
    out["file_present"] = path.exists()
    led.close()
    return out


def mut_drop_identity(doc):
    doc.get("identities", {}).pop("executor_sha256", None)


def mut_drop_spent(doc):
    doc.pop("spent", None)


def mut_negative_spent(doc):
    doc["spent"]["apply"] = -1


def mut_delete_file_factory(m, tag):
    """绑定并耗尽之后**删掉账本文件**: 反例走的是 snapshot/reserve 里的读盘路径。"""
    return None


def probe_delete_after_binding(m, tag="R1D"):
    root = uniq("ledel-%s" % tag)
    root.mkdir(parents=True, exist_ok=True)
    led = m.Ledger(root / "l.jsonl", m.SimClock(), out_dir=root)
    caps, ids = {"apply": 1}, full_ids(m)
    m.approval_init(root, tag, ids, caps, note="probe, no device")
    path = m.approval_path(root, tag)
    obj = m.ApprovalLedger(path, tag, ids, caps, led, m.SimClock())
    obj.commit(obj.reserve("apply", 1))
    try:
        obj.reserve("apply", 1)
        exhausted = "ALLOWED(不该)"
    except m.Stop as e:
        exhausted = e.code
    path.unlink()
    try:
        r = obj.reserve("apply", 1)
        after = "ALLOWED tag=%s 新账 spent=%s" % (
            r["tag"], json.loads(path.read_text(encoding="utf-8"))["spent"])
    except Exception as e:                                       # noqa: BLE001
        after = ("REFUSED_%s 文件重建=%s" % (getattr(e, "code", e.__class__.__name__),
                                            path.exists()))
    led.close()
    return {"exhausted": exhausted, "after_delete": after}


# ---------------------------------------------------------------- R2 单会话绕账
def probe_single_session(m):
    """复审 §4 的探针形状: cap(apply)=0, 步骤**不声明** quota, 真实传输 + 内存端口。"""
    opened = []
    saved = m.make_real_port
    m.make_real_port = lambda name, led: (opened.append(name),
                                          m.FakePort([], led, clock=m.SimClock()))[1]
    try:
        r = m.exec_session("R2PROBE", [{"cmd": "tmc_config_apply CONFIRM", "as": "apply",
                                        "expect_contains": ["[TMC-CFG] CONFIGURED"]}],
                           str(SCRATCH / ("run-r2-%s" % id(m))), "real",
                           {"boot_bytes": [], "responses": {}}, "MEMORY_PORT", 30,
                           m.SimClock(), plan={"quota": {"port_open": 1, "tmc_config_apply": 0}},
                           quota_box={"port_open": 1, "tmc_config_apply": 0})
        res = {"status": r["status"], "code": r.get("code", ""), "success": r["success"],
               "quota_used": r["quota_used"], "ports_opened": len(opened),
               "disarm": r["disarm"]}
        led_path = Path(r["ledger"])
        res["tx"] = [x["bytes"].strip() for x in
                     (json.loads(l) for l in led_path.read_text(encoding="utf-8").splitlines()
                      if l.strip()) if x["kind"] == "TX"]
    except m.Stop as e:
        res = {"raised": e.code, "ports_opened": len(opened), "tx": []}
    finally:
        m.make_real_port = saved
    led2 = Path(str(SCRATCH / ("r2cli-%s" % id(m))))
    return res


def probe_cli_single_session_real(m):
    d = Path(tempfile.mkdtemp(prefix="m8r6a-cli-", dir=str(SCRATCH)))
    sf = d / "steps.json"
    sf.write_text(json.dumps([{"cmd": "tmc_config_apply CONFIRM", "as": "apply",
                               "quota": {"name": "tmc_config_apply", "n": 1}}]), encoding="utf-8")
    import io
    import contextlib
    err = io.StringIO()
    out = io.StringIO()
    argv = ["--transport", "real", "--backend", "real", "--session", "P",
            "--steps", str(sf), "--out", str(d / "run"), "--plan",
            str(REPO / "reports/reviews/phase8-m8-r6a-offline-rework/field/plan_core_r6a.json"),
            "--repo-root", str(REPO), "--approval-token", "T", "--expected-token", "T",
            "--approval-id", "CLI-PROBE", "--approval-root", str(d / "approvals")]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = m.main(argv)
        except SystemExit as e:                     # argparse 的用法错误也算一种拒绝
            rc = e.code
    text = err.getvalue().strip().splitlines()
    return {"rc": rc, "stderr_last": text[-1][:96] if text else "-",
            "run_dir_created": (d / "run").exists()}


# ---------------------------------------------------------------- R3 清理裸 NUL
def probe_cleanup_nul(m):
    rows = json.loads((REPO / REL_ROWS).read_text(encoding="utf-8-sig"))
    dis = rows["commands"][DISARM_KEY]["healthy"]
    fx = {"boot_bytes": [], "responses": {
        "motor_stop": rows["commands"]["motor_stop"]["healthy"],
        "motor_disarm": rows["commands"]["motor_disarm"]["healthy"],
        DISARM_KEY: list(dis) + ["\x00"],
        "__watch__": rows["commands"]["__watch__"]["healthy"]}}
    caps = {"port_open": 1, "motor_run": 1, "retry": 0, "cleanup_motor_stop": 1,
            "cleanup_motor_disarm": 1, "cleanup_motor_status": 1}
    steps = [{"cmd": "motor_start", "as": "start", "seconds": 1, "expect_absent": ["REFUSED"],
              "quota": {"name": "motor_run", "n": 1}}]
    r = m.exec_session("R3PROBE", steps, str(SCRATCH / ("run-r3-%s" % id(m))), "stub", fx,
                       "NO_DEVICE", 30, m.SimClock(), motion_cap_seconds=5,
                       plan={"quota": dict(caps)}, quota_box=dict(caps))
    raw = Path(r["dir"]) / "cleanup_motor_status.bin"
    return {"status": r["status"], "success": r["success"], "disarm": r["disarm"],
            "cleanup_errors": r["cleanup_errors"], "nul_in_evidence": raw.read_bytes().count(b"\x00")}


def main():
    bp, bbytes, bsha = baseline_executor()
    old = load(bp, "chain_before")
    new = load(EXEC_LIVE, "chain_after")
    print("R6A_BEFORE_AFTER 基线头=%s 执行器 blob=%d B sha=%s…" % (BASE, bbytes, bsha[:16]))
    print("修后执行器 = %d B sha=%s… (%s)" % (EXEC_LIVE.stat().st_size, sha(EXEC_LIVE)[:16],
                                             EXEC_LIVE.relative_to(REPO).as_posix()))
    fails = []
    for label, fn, want in (
            ("R1①删 identities.executor_sha256", lambda m: probe_ledger(m, mut_drop_identity,
                                                                        "R1A"), "REFUSED"),
            ("R1②删整个 spent 字段", lambda m: probe_ledger(m, mut_drop_spent, "R1B"), "REFUSED"),
            ("R1③spent.apply=-1", lambda m: probe_ledger(m, mut_negative_spent, "R1C"), "REFUSED"),
            ("R1④绑定并耗尽后删账本文件", probe_delete_after_binding, "REFUSED")):
        b = fn(old)
        a = fn(new)
        key = "after_delete" if label.startswith("R1④") else "again"
        got_b = b.get(key) if isinstance(b, dict) else b
        got_a = a.get(key) if isinstance(a, dict) else a
        ok = isinstance(got_a, str) and want in got_a
        print("\n[%s]" % label)
        print("  修前: %s" % json.dumps(b, ensure_ascii=False, sort_keys=True))
        print("  修后: %s" % json.dumps(a, ensure_ascii=False, sort_keys=True))
        print("  判定: 修后 %s %s" % (key, "符合(具名拒绝)" if ok else "不符合(仍被放行!)"))
        if not ok:
            fails.append(label)
    print("\n[R2 真实单会话步骤未绑定批准(cap apply=0, 步骤不声明 quota)]")
    b = probe_single_session(old)
    a = probe_single_session(new)
    print("  修前: %s" % json.dumps(b, ensure_ascii=False, sort_keys=True))
    print("  修后: %s" % json.dumps(a, ensure_ascii=False, sort_keys=True))
    ok = (a.get("raised") == "REAL_SESSION_REQUIRES_APPROVED_PACKAGE" or
          a.get("code") == "REAL_SESSION_REQUIRES_APPROVED_PACKAGE") and a["ports_opened"] == 0 \
        and not a["tx"]
    print("  判定: 修后 %s" % ("符合(开口 0 次、一条未发)" if ok else "不符合!"))
    if not ok:
        fails.append("R2 单会话")
    print("\n[R2b CLI 单会话 --steps + --transport real 的真实退出码]")
    b = probe_cli_single_session_real(old)
    a = probe_cli_single_session_real(new)
    print("  修前: %s" % json.dumps(b, ensure_ascii=False, sort_keys=True))
    print("  修后: %s" % json.dumps(a, ensure_ascii=False, sort_keys=True))
    ok = a["rc"] == 4 and "single_session_steps_not_approved_package" in a["stderr_last"] \
        and not a["run_dir_created"]
    print("  判定: 修后 rc=%s %s" % (a["rc"], "符合(装配层就拒, 不建输出目录)" if ok else "不符合!"))
    if not ok:
        fails.append("R2b CLI")
    print("\n[R3 清理用的 motor_status 回包里多一条独立裸 NUL]")
    b = probe_cleanup_nul(old)
    a = probe_cleanup_nul(new)
    print("  修前: %s" % json.dumps(b, ensure_ascii=False, sort_keys=True))
    print("  修后: %s" % json.dumps(a, ensure_ascii=False, sort_keys=True))
    ok = (a["disarm"] == "UNKNOWN" and a["success"] is False and
          any("NUL_BYTE" in e for e in a["cleanup_errors"]) and a["nul_in_evidence"] == 1)
    print("  判定: 修后 %s" % ("符合(禁能 UNKNOWN、原字节保留、独立证据错误)" if ok else "不符合!"))
    if not ok:
        fails.append("R3 cleanup NUL")
    print("\n[R4 motor.c 本轮新增错误打印的最坏字节(同一把尺 host/line_width_check.py)]")
    sys.path.insert(0, str(REPO / "reports/reviews/phase8-m8-r6-offline-recovery/host"))
    import line_width_check as L

    def withheld_calls(text):
        """`if (withheld)` 分支里**每一条** rt_kprintf 的完整格式串(拼接后的字面)。"""
        i = text.index("if (withheld)")
        region = text[i:i + 2000]
        stop = region.find("\n    if (!ok)")
        region = region[:stop] if stop > 0 else region
        return ["".join(L.LITSTR.findall(mm.group(1))) for mm in L.KPFMT.finditer(region)]

    before_txt = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (BASE, REL_MOTOR)],
                                cwd=str(REPO), capture_output=True).stdout.decode("utf-8")
    after_txt = (REPO / REL_MOTOR).read_text(encoding="utf-8")
    for tag, text in (("修前(基线 44e493c 的 motor.c)", before_txt),
                      ("修后(盘上 motor.c)", after_txt)):
        fmts = withheld_calls(text)
        widths = [L.worst(f)[0] for f in fmts]
        print("  %s: 该分支 rt_kprintf 调用数=%d, 最长一条最坏展开 %d B (门槛 %d B, "
              "控制台裁剪线 127 B) => %s" %
              (tag, len(fmts), max(widths), L.LIMIT,
               "超限" if max(widths) > L.LIMIT else "达标"))
    print("\nRESULT %s (fails=%d)" % ("BEFORE_AFTER_OK" if not fails else "PROBLEMS " + repr(fails),
                                      len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
