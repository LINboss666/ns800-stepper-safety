#!/usr/bin/env python3
"""R6B remaining-path probes: temporary files, memory ports/backends, no devices."""
import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
FIELD = REPO / "reports/reviews/phase8-m8-relative-protection-r3/field"
OUT = Path(tempfile.mkdtemp(prefix="a-r6b-remaining-"))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


t = load(FIELD / "m8r3_field_selftest.py", "r6b_helper")
m = load(FIELD / "m8r3_serial_chain.py", "r6b_remaining_exec")


def cleanup_log_failure(tag, inject=False, corrupt=False, zero_cleanup=False):
    saved_ev = m.Ledger.ev
    saved_quota = dict(t.B1_QUOTA)
    hits = []

    def ev(self, kind, **kw):
        if inject and kind == "CLEANUP_RESERVE_REFUSED_SENT_ANYWAY" and not hits:
            hits.append(kind)
            raise OSError("INJECTED: one cleanup bookkeeping write failure")
        return saved_ev(self, kind, **kw)

    m.Ledger.ev = ev
    if zero_cleanup:
        t.B1_QUOTA.update({k: 0 for k in t.B1_QUOTA if k.startswith("cleanup_")})
    try:
        mutation = (lambda d: d["pending"][-1].__setitem__("name", [])) if corrupt else None
        result, raised, tx, closed, events, path = t._b1_session(m, tag, mutate=mutation)
    finally:
        m.Ledger.ev = saved_ev
        t.B1_QUOTA.clear()
        t.B1_QUOTA.update(saved_quota)
    return {"probe": tag, "injected_events": hits, "raised": raised, "tx": tx,
            "closed": closed, "frame_errors": [e for e in events if e["kind"] ==
                                                   "CLEANUP_FRAME_EXCEPTION"],
            "result": {k: result.get(k) for k in ("status", "code", "success", "disarm",
                                                  "cleanup_errors", "cleanup_overruns")},
            "raw_evidence_directory": str(path.parent)}


def s0_snapshot_record():
    root = OUT / "s0"
    root.mkdir()
    (root / "rows.json").write_bytes((FIELD / "rows.json").read_bytes())
    plan = copy.deepcopy(t.PLAN)
    plan["stages"] = [s for s in plan["stages"] if s["id"] in ("S0", "S1")]
    p_bytes = json.dumps(plan, ensure_ascii=False).encode("utf-8")
    q = copy.deepcopy(plan)
    q["task_id"] = "DISK_CHANGED_AFTER_SNAPSHOT"
    q_bytes = json.dumps(q, ensure_ascii=False).encode("utf-8")
    path = root / "plan.json"
    path.write_bytes(p_bytes)
    identities = m.current_identities(plan, path, REPO)
    m.approval_init(root, "A_TEMP_S0", identities, plan["quota"],
                    note="TEMPORARY OFFLINE PROBE ONLY")
    old_read, old_backend = Path.read_bytes, m.RealActionBackend
    switched, calls = [], []

    def read_bytes(p):
        raw = old_read(p)
        if p == path and not switched:
            path.write_bytes(q_bytes)  # after taking P bytes; retain P for parsing/hash
            switched.append(True)
        return raw

    class MemoryBackend(m.ActionBackend):
        kind = "memory-only"

        def __init__(self, *args, **kwargs):
            self.attempts = []

        def download(self, act, ctx):
            calls.append(act["argv"])
            self.note_attempt("download", "IN_MEMORY")
            return {"rc": 0}

    Path.read_bytes = read_bytes
    m.RealActionBackend = MemoryBackend
    stdout = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout):
            rc = m.main(["--dispatch", "--transport", "real", "--backend", "real",
                         "--profile", m.REAL_PROFILE, "--plan", str(path),
                         "--out", str(root / "run"), "--repo-root", str(REPO),
                         "--approval-token", "TEMP", "--expected-token", "TEMP",
                         "--approval-id", "A_TEMP_S0", "--approval-root", str(root)])
    finally:
        Path.read_bytes, m.RealActionBackend = old_read, old_backend
    (root / "stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
    # 本机适配(仅环境, 不改判据): Windows 下无 encoding 的 read_text 走 cp936 会炸在中文行,
    # 这里按 UTF-8 读同一份台账字节; A 在 Linux 上默认就是 UTF-8, 断言与记录字段未动。
    events = [json.loads(line) for line in
              (root / "run/dispatch_ledger.jsonl").read_bytes().decode("utf-8").splitlines()]
    facts = [e for e in events if e["kind"] == "S0_FACTS"]
    recorded = facts[0]["plan_sha256"] if facts else None
    return {"probe": "s0_record_uses_disk_instead_of_execution_snapshot", "rc": rc,
            "switched": bool(switched), "approved_execution_sha": m.sha_bytes(p_bytes),
            "changed_disk_sha": m.sha_bytes(q_bytes), "s0_recorded_plan_sha": recorded,
            "s0_records_execution_snapshot": recorded == m.sha_bytes(p_bytes),
            "memory_backend_calls": len(calls), "stdout": str(root / "stdout.txt")}


result = {"device_actions": 0, "serial_ports": 0, "output": str(OUT), "results": [
    cleanup_log_failure("corrupt-control", corrupt=True),
    cleanup_log_failure("corrupt-log-failure", inject=True, corrupt=True),
    cleanup_log_failure("exhausted-control", zero_cleanup=True),
    cleanup_log_failure("exhausted-log-failure", inject=True, zero_cleanup=True),
    s0_snapshot_record()]}
print(json.dumps(result, ensure_ascii=False, indent=2))
