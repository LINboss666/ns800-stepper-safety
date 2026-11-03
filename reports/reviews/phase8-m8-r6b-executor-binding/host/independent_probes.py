#!/usr/bin/env python3
"""Independent R6A probes. Temporary files and memory ports only; no device calls."""
import copy
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
OUT = Path(tempfile.mkdtemp(prefix="a-r6a-probes-"))
FIELD = REPO / "reports/reviews/phase8-m8-relative-protection-r3/field"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


t = load(FIELD / "m8r3_field_selftest.py", "probe_test_helpers")
m = load(FIELD / "m8r3_serial_chain.py", "probe_executor")


def ledger_corruption_after_start(mutation, label):
    root = OUT / label
    root.mkdir()
    clock = m.SimClock()
    caps = {"port_open": 1, "motor_run": 1, "cleanup_motor_stop": 1,
            "cleanup_motor_disarm": 1, "cleanup_motor_status": 1}
    ids = t.full_ids(123)
    path = m.approval_init(root, label, ids, caps, note="TEMPORARY OFFLINE PROBE ONLY")
    led = m.Ledger(root / "approval-events.jsonl", clock, out_dir=root)
    approval = m.ApprovalLedger(path, label, ids, caps, led, clock)
    ports = []
    old_port = m.FakePort

    class CorruptingMemoryPort(old_port):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            ports.append(self)

        def write(self, data):
            n = super().write(data)
            if data.strip() == b"motor_start" and mutation:
                doc = json.loads(path.read_text())
                mutation(doc)
                path.write_text(json.dumps(doc), encoding="utf-8")
            return n

    m.FakePort = CorruptingMemoryPort
    result = None
    raised = None
    try:
        result = m.exec_session(
            label, [{"cmd": "motor_start", "as": "start", "seconds": 1,
                     "quota": {"name": "motor_run", "n": 1},
                     "expect_absent": ["REFUSED"]}],
            root, "stub", t.motion_fx({"motor_start": []}), "MEMORY_ONLY", 30, clock,
            motion_cap_seconds=5, plan={"quota": caps}, quota_box=dict(caps),
            approval=approval)
    except Exception as e:
        raised = type(e).__name__ + ":" + str(e)
    finally:
        m.FakePort = old_port
        led.close()
    events = [json.loads(s) for s in (root / label / "ledger.jsonl").read_text().splitlines()]
    return {"probe": label, "raised": raised,
            "tx": [e["bytes"].strip() for e in events if e["kind"] == "TX"],
            "closed": ports[0]._closed,
            "cleanup_events": [e for e in events if e["kind"] == "CLEANUP_FRAME_EXCEPTION"],
            "result": ({k: result[k] for k in ("status", "code", "disarm", "success",
                                                "cleanup_errors", "cleanup_overruns")}
                       if result else None)}


def mutated_dispatch_input():
    root = OUT / "dispatch-input"
    root.mkdir()
    approved_plan = copy.deepcopy(t.PLAN)
    path = root / "approved-plan.json"
    path.write_text(json.dumps(approved_plan), encoding="utf-8")
    ids = m.current_identities(approved_plan, path, REPO)
    m.approval_init(root, "A_TEMP_DISPATCH", ids, approved_plan["quota"],
                    note="TEMPORARY OFFLINE PROBE ONLY")
    live = copy.deepcopy(approved_plan)
    live["stages"] = [s for s in live["stages"] if s["id"] == "S1"]
    original_argv = live["tools"]["download"]["argv"]
    live["tools"]["download"]["argv"] = ["MEMORY_ONLY_UNAPPROVED_ARGUMENTS"]
    calls = []
    old_backend = m.RealActionBackend

    class MemoryBackend(m.ActionBackend):
        kind = "memory-only"

        def __init__(self, *args, **kwargs):
            self.attempts = []

        def download(self, act, ctx):
            calls.append({"name": "download", "argv": act["argv"]})
            self.note_attempt("download", "IN_MEMORY")
            return {"rc": 0}

    m.RealActionBackend = MemoryBackend
    try:
        result = m.dispatch_plan(
            live, "real", "real", root / "run", m.SimClock(), m.REAL_PROFILE,
            "MEMORY_NO_PORT", str(REPO), "TEMP_TEST", "TEMP_TEST",
            plan_dir=str(FIELD), approval_id="A_TEMP_DISPATCH", approval_root=root,
            plan_path=path)
    finally:
        m.RealActionBackend = old_backend
    return {"probe": "in_memory_plan_differs_from_approved_file",
            "approved_file_unchanged": json.loads(path.read_text()) == approved_plan,
            "approved_download_argv": original_argv, "memory_backend_calls": calls,
            "status": result["status"], "code": result.get("code"),
            "stages": [{k: s.get(k) for k in ("id", "status", "code")} for s in result["stages"]],
            "spent": result.get("approval", {}).get("spent")}


def fragmented_clean_response():
    root = OUT / "utf8-fragments"
    root.mkdir()
    text = "[INFO] " + "安" * 180 + "\r\n"
    raw = text.encode("utf-8") + b"msh >"
    clock = m.SimClock()
    led = m.Ledger(root / "events.jsonl", clock, out_dir=root)
    port = m.FakePort([raw], led, clock=clock)
    rows, count = m.read_until_prompt(port, 2, led, root / "rx.bin", clock)
    led.close()
    return {"probe": "clean_utf8_crosses_512B", "raw_bytes": len(raw), "bytes": count,
            "passed": bool(rows) and rows[0] == text.strip()
                      and (root / "rx.bin").read_bytes() == raw}


def cleanup_fragment_matrix():
    prefix = ("[INFO] " + "安" * 180 + "\r\n").encode("utf-8")
    status = ("\r\n".join(t.DISARM_ROWS) + "\r\n").encode("utf-8")
    matrix = []
    for label, extra in [("clean", b""), ("nul", b"\x00\r\n"),
                         ("illegal_utf8", b"\xff\r\n"),
                         ("danger_and_illegal", b"HardFault\xff\r\n")]:
        root = OUT / ("cleanup-fragment-" + label)
        root.mkdir()
        raw = prefix + status + extra + b"msh >"
        clock = m.SimClock()
        led = m.Ledger(root / "events.jsonl", clock, out_dir=root)
        port = m.FakePort([raw], led, clock=clock)
        rows, got, _, _, errs = m._cleanup_read(port, led, clock, 2, "motor_status", root)
        _, bad = m.disable_contract(rows)
        led.close()
        matrix.append({"input": label, "bytes": len(raw), "errors": errs,
                       "disable_proven": not bad,
                       "raw_preserved": (root / "cleanup_motor_status.bin").read_bytes() == raw})
    return {"probe": "cleanup_utf8_crosses_512B", "matrix": matrix}


def cli_read_hash_interleaving():
    root = OUT / "cli-read-hash-interleaving"
    root.mkdir()
    (root / "rows.json").write_bytes((FIELD / "rows.json").read_bytes())
    approved_plan = copy.deepcopy(t.PLAN)
    approved_bytes = json.dumps(approved_plan).encode("utf-8")
    path = root / "plan.json"
    path.write_bytes(approved_bytes)
    ids = m.current_identities(approved_plan, path, REPO)
    m.approval_init(root, "A_TEMP_CLI", ids, approved_plan["quota"],
                    note="TEMPORARY OFFLINE PROBE ONLY")
    live = copy.deepcopy(approved_plan)
    live["stages"] = [s for s in live["stages"] if s["id"] == "S1"]
    live["tools"]["download"]["argv"] = ["MEMORY_ONLY_UNAPPROVED_ARGUMENTS"]
    path.write_text(json.dumps(live), encoding="utf-8")
    old_read = Path.read_text
    old_backend = m.RealActionBackend
    switched = []
    calls = []

    def interleaved_read(p, *args, **kwargs):
        text = old_read(p, *args, **kwargs)
        if p == path and not switched:
            # External-file interleaving: main has read the old bytes;
            # the file is replaced before current_identities reads bytes again.
            path.write_bytes(approved_bytes)
            switched.append(True)
        return text

    class MemoryBackend(m.ActionBackend):
        kind = "memory-only"

        def __init__(self, *args, **kwargs):
            self.attempts = []

        def download(self, act, ctx):
            calls.append(act["argv"])
            self.note_attempt("download", "IN_MEMORY")
            return {"rc": 0}

    stdout = io.StringIO()
    Path.read_text = interleaved_read
    m.RealActionBackend = MemoryBackend
    try:
        with contextlib.redirect_stdout(stdout):
            rc = m.main(["--dispatch", "--transport", "real", "--backend", "real",
                         "--profile", m.REAL_PROFILE, "--plan", str(path),
                         "--out", str(root / "run"), "--repo-root", str(REPO),
                         "--approval-token", "TEMP_TEST", "--expected-token", "TEMP_TEST",
                         "--approval-id", "A_TEMP_CLI", "--approval-root", str(root)])
    finally:
        Path.read_text = old_read
        m.RealActionBackend = old_backend
    (root / "stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
    return {"probe": "cli_plan_read_then_hash_interleaving", "rc": rc,
            "file_restored_before_hash": bool(switched),
            "file_matches_approval": path.read_bytes() == approved_bytes,
            "memory_backend_calls": calls, "stdout": str(root / "stdout.txt")}


results = [ledger_corruption_after_start(None, "clean-control"),
           ledger_corruption_after_start(lambda d: d["spent"].__setitem__("motor_run", -1),
                                         "negative-spent-control"),
           ledger_corruption_after_start(lambda d: d["pending"][-1].__setitem__("name", []),
                                         "pending-name-list"),
           mutated_dispatch_input(), cli_read_hash_interleaving(), fragmented_clean_response(),
           cleanup_fragment_matrix()]
print(json.dumps({"repo": str(REPO), "output": str(OUT), "device_actions": 0,
                  "serial_ports": 0, "results": results}, ensure_ascii=False, indent=2))
