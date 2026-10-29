#!/usr/bin/env python3
"""m6_field_candidate.py - M6 offline candidate for the future fault session.

Scope: the fault phase only (arm -> start -> trip -> bounded disarm -> full
new-log readback -> optional verified recovery). Archive/clear remain in the
M5 script; this file neither clears nor archives. This file is
OFFLINE_VERIFIED / NOT_BOARD_EXECUTED.

Design rules:
  - one writer, monotonic deadlines, no reconnect/retry;
  - responsibility flag is set BEFORE arm/start; lost responses do not mean
    the command did not execute;
  - finally always attempts at most one motor_disarm if output may be on;
    primary error is preserved, cleanup result is separate;
  - trip acceptance uses canonical FAULT_LATCHED code 9, never a console line;
  - accepted==completed is queue drain; queue_dropped is a retry counter and
    does not replace slot coverage/session/event/readback checks;
  - recovery only after a complete slot readback and a confirmed disarm; no
    automatic reset (--allow-recovery is explicit).

--selftest constructs no pyserial/SerialPort object.
"""
import argparse
import json
import os
import re
import sys
import time

EXPECTED_FRAMES = 300
COLUMNS = ("slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,"
           "az_mg,step_hz,vibration_mg,flags").split(",")


class Abort(Exception):
    pass


class Timeout(Abort):
    pass


def _text(raw):
    return raw.replace(b"\x00", b"<NUL>").decode("utf-8", "replace")


def parse_u(text, pattern, what):
    m = re.search(pattern, text)
    if not m:
        raise Abort("missing %s" % what)
    return int(m.group(1))


def parse_logstat(text):
    m = re.search(r"slots=(\d+)/(\d+) valid=(\d+) bad=(\d+)", text)
    q = re.search(r"queued=(\d+) completed=(\d+) failed=(\d+) "
                  r"queue_dropped=(\d+) last_error=(-?\d+)", text)
    if not m or not q:
        raise Abort("flash_logstat parse failed")
    return {"used": int(m.group(1)), "slots": int(m.group(2)),
            "valid": int(m.group(3)), "bad": int(m.group(4)),
            "accepted": int(q.group(1)), "completed": int(q.group(2)),
            "failed": int(q.group(3)), "dropped": int(q.group(4)),
            "last_error": int(q.group(5))}


def parse_blackbox(text):
    m = re.search(r"\[BB\] state=(\d+) session=(\d+) written=(\d+) "
                  r"last_err=(-?\d+)", text)
    w = re.search(r"\[BB\] write drops=(\d+)", text)
    f = re.search(r"\[BB\] flash: used=(\d+) valid=(\d+) bad=(\d+) "
                  r"acc=(\d+) done=(\d+) fail=(\d+) drop=(\d+)", text)
    if not m or not w or not f:
        raise Abort("blackbox_status parse failed")
    return {"state": int(m.group(1)), "session": int(m.group(2)),
            "written": int(m.group(3)), "last_err": int(m.group(4)),
            "write_drops": int(w.group(1)),
            "used": int(f.group(1)), "valid": int(f.group(2)),
            "bad": int(f.group(3)), "acc": int(f.group(4)),
            "done": int(f.group(5)), "fail": int(f.group(6)),
            "drop": int(f.group(7))}


class Session:
    def __init__(self, link, clock=time.monotonic, sleep=time.sleep,
                 verify_log=None, out_dir=None):
        self.link = link
        self.clock = clock
        self.sleep = sleep
        self.verify_log = verify_log or default_verify_log
        self.out_dir = out_dir
        self.raw = []
        self.seq = 0
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            self.raw_index = open(os.path.join(out_dir, "raw_index.jsonl"),
                                  "a", encoding="utf-8")
        else:
            self.raw_index = None
        self.output_may_be_on = False
        self.arm_time = None
        self.pre_used = 0
        self.pre_valid = 0
        self.bb0 = None
        self.disarm_confirmed = False
        self.disarm_attempts = 0
        self.disarm_time = None
        self.cleanup_unknown = False
        self.cleanup_late = False
        self.primary_error = None

    def q(self, cmd, deadline, quiet=0.15):
        remain = max(0.0, deadline - self.clock())
        if remain <= 0:
            raise Timeout("deadline before %s" % cmd)
        raw, t0, t1 = self.link.send(cmd, remain, quiet)
        self.raw.append((cmd, raw, t0, t1))
        self._save_raw(cmd, raw, t0, t1)
        return _text(raw)

    def _save_raw(self, cmd, raw, t0, t1):
        if not self.out_dir:
            return
        self.seq += 1
        name = "%03d_%s.bin" % (self.seq, re.sub(r"[^A-Za-z0-9_.-]+", "_", cmd))
        with open(os.path.join(self.out_dir, name), "wb") as fh:
            fh.write(raw)
        self.raw_index.write(json.dumps(
            {"seq": self.seq, "cmd": cmd, "file": name, "bytes": len(raw),
             "t_tx": t0, "t_rx": t1}) + "\n")
        self.raw_index.flush()

    def wait_until(self, t):
        while self.clock() < t:
            self.sleep(min(0.05, t - self.clock()))

    def disarm_once(self, deadline):
        if self.disarm_attempts:
            return
        self.disarm_attempts += 1
        self.disarm_time = self.clock()
        try:
            self.q("motor_disarm", deadline, 0.2)
            # Confirmation is a contract field, not a guessed sentence:
            # the permit request must read LOW and pad must read 0.
            st = self.q("safety_status", deadline)
            if re.search(r"request=DISABLE\(LOW\)", st) and \
               re.search(r"pad=0\b", st):
                self.disarm_confirmed = True
                self.output_may_be_on = False
            else:
                self.cleanup_unknown = True
        except Abort:
            self.cleanup_unknown = True

    def _static_preflight(self, deadline):
        ss = self.q("safety_state", deadline)
        st = self.q("safety_status", deadline)
        mo = self.q("motor_status", deadline)
        fl = parse_logstat(self.q("flash_logstat", deadline))
        bb = parse_blackbox(self.q("blackbox_status", deadline))
        if "state=READY" not in ss or "fault_code=0" not in ss:
            raise Abort("not READY/fault0")
        if "request=DISABLE(LOW)" not in st or "pad=0" not in st:
            raise Abort("drive request not LOW")
        if "armed=0" not in mo:
            raise Abort("motor already armed")
        m = re.search(r"cur_hz=(\d+) tgt_hz=(\d+)", mo)
        if not m or int(m.group(1)) != 0 or int(m.group(2)) != 0:
            raise Abort("motor not at target 0")
        g = re.search(r"arm_gate_mask=0x([0-9A-Fa-f]+)", mo)
        if not g or int(g.group(1), 16) != 0:
            raise Abort("arm gate not clear")
        if fl["used"] != fl["valid"] or fl["bad"] != 0:
            raise Abort("log slots inconsistent: used=%d valid=%d bad=%d"
                        % (fl["used"], fl["valid"], fl["bad"]))
        if fl["slots"] - fl["used"] < EXPECTED_FRAMES:
            raise Abort("insufficient log space: %d free, need %d"
                        % (fl["slots"] - fl["used"], EXPECTED_FRAMES))
        if fl["accepted"] != fl["completed"] or fl["failed"] != 0:
            raise Abort("queue not drained: acc=%d done=%d failed=%d"
                        % (fl["accepted"], fl["completed"], fl["failed"]))
        if bb["state"] != 0 or bb["last_err"] != 0 or bb["fail"] != 0:
            raise Abort("blackbox not idle/clean: %s" % bb)
        if bb["acc"] != bb["done"]:
            raise Abort("blackbox queue not drained: %s" % bb)
        self.pre_used = fl["used"]
        self.pre_valid = fl["valid"]
        self.bb0 = bb
        return fl["used"]

    def run_fault(self, deadline_s=20.0, allow_recovery=False):
        t0 = self.clock()
        deadline = t0 + deadline_s
        recovered = False
        try:
            self._static_preflight(deadline)
            self.q("motor_dir 0", deadline)
            # Arm may make the output eligible: responsibility starts here.
            self.arm_time = self.clock()
            self.output_may_be_on = True
            arm = self.q("motor_arm", min(deadline, self.clock() + 10.0))
            armed = "ARMED" in arm or "armed" in arm
            if not armed:
                mo = self.q("motor_status", deadline)
                armed = "armed=1" in mo
            if not armed:
                raise Abort("arm refused; no start sent")
            self.q("motor_target 300", min(deadline, self.clock() + 2.0))
            t_start = self.clock()
            try:
                # A lost start reply must not consume the 5 s cleanup window.
                self.q("motor_start", min(deadline, t_start + 1.5))
            except Abort:
                pass  # command may have executed; trip still decides safety
            self.wait_until(min(t_start + 3.0, deadline))
            try:
                # Bound the trip read so the fallback disarm keeps its budget.
                self.q("safety_trip CONFIRM", min(deadline, t_start + 4.0))
            except Abort:
                pass
            latched = False
            while self.clock() < min(t_start + 4.8, deadline):
                ss = self.q("safety_state", min(deadline, t_start + 4.8))
                if "state=FAULT_LATCHED" in ss and "fault_code=9" in ss:
                    latched = True
                    break
                self.sleep(0.1)
            # Fallback cleanup is independent of the trip response text.
            self.disarm_once(min(t_start + 5.0, deadline))
            if not latched:
                raise Abort("no FAULT_LATCHED code 9")
            used = self.verify_log(self, min(deadline, self.clock() + 60.0))
            if used != EXPECTED_FRAMES:
                raise Abort("new log frames %s != %d" % (used, EXPECTED_FRAMES))
            if not self.disarm_confirmed:
                raise Abort("output disabled not confirmed")
            if allow_recovery:
                st = self.q("safety_status", min(deadline, self.clock() + 2.0))
                if "request=DISABLE(LOW)" not in st or "pad=0" not in st:
                    raise Abort("recovery refused: output not LOW")
                reset = self.q("fault_reset", min(deadline, self.clock() + 30.0))
                if "MANUAL_CLEAR" not in reset:
                    raise Abort("fault_reset did not reach MANUAL_CLEAR")
                self.q("system_selftest", min(deadline, self.clock() + 30.0))
                ss = self.q("safety_state", min(deadline, self.clock() + 2.0))
                if "state=READY" not in ss or "fault_code=0" not in ss:
                    raise Abort("selftest did not restore READY")
                recovered = True
            return {"latched": True, "disarm": self.disarm_confirmed,
                    "frames": used, "recovered": recovered,
                    "cleanup_unknown": self.cleanup_unknown,
                    "cleanup_late": self.cleanup_late}
        except Abort as exc:
            self.primary_error = str(exc)
            raise
        finally:
            if self.output_may_be_on and not self.disarm_confirmed:
                base = self.arm_time if self.arm_time is not None else t0
                clean_dl = min(base + 5.0, deadline)
                if clean_dl <= self.clock():
                    # The planned window was consumed by a hung read; still
                    # make one short bounded attempt and record it as late.
                    clean_dl = self.clock() + 0.5
                    self.cleanup_late = True
                self.disarm_once(clean_dl)


def default_verify_log(sess, deadline):
    """Full readback of the new slots; rejects any missing row or bad semantics."""
    fl = parse_logstat(sess.q("flash_logstat", min(deadline, sess.clock() + 3.0)))
    bb = parse_blackbox(sess.q("blackbox_status", min(deadline, sess.clock() + 3.0)))
    delta = fl["used"] - sess.pre_used
    if delta != EXPECTED_FRAMES:
        raise Abort("new slots %d != %d" % (delta, EXPECTED_FRAMES))
    rows = []
    slots = []
    first = sess.pre_used
    while first < fl["used"]:
        count = min(100, fl["used"] - first)
        txt = sess.q("flash_dump %d %d" % (first, count),
                     min(deadline, sess.clock() + 8.0), 0.8)
        for line in txt.splitlines():
            parts = line.strip().split(",")
            if len(parts) == len(COLUMNS) and parts[0].isdigit():
                rows.append(dict(zip(COLUMNS, parts)))
                slots.append(int(parts[0]))
        first += count
    if sorted(slots) != list(range(sess.pre_used, fl["used"])):
        raise Abort("slot coverage incomplete: %d rows for %d slots"
                    % (len(slots), delta))
    stats = {"used": delta, "valid": fl["valid"] - sess.pre_valid,
             "bad": fl["bad"], "accepted": fl["accepted"],
             "completed": fl["completed"], "failed": fl["failed"],
             "dropped": fl["dropped"],
             "bb_write_drops": bb["write_drops"] - sess.bb0["write_drops"]}
    ok, why = log_is_complete(stats, rows, expected=EXPECTED_FRAMES,
                              expected_session=bb["session"])
    if not ok:
        raise Abort("new log rejected: %s" % why)
    return delta


def log_is_complete(stats, rows, expected=EXPECTED_FRAMES, expected_session=None):
    """stats: dict(used,valid,bad,accepted,completed,failed,dropped,bb_write_drops).
    rows: iterable of dicts with session,event,step_hz,time_ms."""
    if stats["used"] != expected or stats["valid"] != expected or stats["bad"] != 0:
        return False, "used/valid/bad"
    if stats["accepted"] != stats["completed"] or stats["failed"] != 0:
        return False, "queue not drained"
    if stats["bb_write_drops"] != 0:
        return False, "blackbox abandoned frames"
    rows = list(rows)
    if len(rows) != expected:
        return False, "row count %d" % len(rows)
    sessions = {int(r["session"]) for r in rows}
    if len(sessions) != 1 or (expected_session is not None and
                              sessions != {expected_session}):
        return False, "session"
    ev = [int(r["event"]) for r in rows]
    if ev.count(0) + ev.count(9) != expected or not ev or ev[0] != 0 or ev[-1] != 9:
        return False, "event sequence"
    first9 = ev.index(9) if 9 in ev else -1
    if first9 <= 0 or not any(int(r["step_hz"]) > 0 for r in rows[:first9]):
        return False, "pre RUN context"
    if any(int(r["step_hz"]) != 0 for r in rows[first9:]):
        return False, "post step_hz"
    times = [int(r["time_ms"]) for r in rows]
    if times != sorted(times):
        return False, "time order"
    return True, "ok"


# ============================== offline selftest ==============================
class FClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, dt): self.t += max(0.0, dt)


class FDevice:
    def __init__(self):
        self.t = 0.0
        self.armed = 0
        self.latched = False
        self.sent = []
        self.hang = set()
        self.arm_reply_lost = False
        self.trip_latched = True
        self.started = False
        self.hang_after_start = set()
        self.used = 0
        self.slots_total = 4096
        self.session = 0
        self.written = 0
        self.acc = self.done = 0
        self.fail = 0
        self.drop = 0
        self.write_drops = 0
        self.dropped = 51
        self.frames = {}
        self.missing_slot = None

    def send(self, cmd, timeout, quiet):
        self.t += 0.01
        self.sent.append((self.t, cmd))
        if cmd in self.hang or (self.started and cmd in self.hang_after_start):
            self.t += timeout
            return b"", self.t - timeout, self.t
        if cmd == "safety_state":
            if self.latched:
                return b"[SS] state=FAULT_LATCHED fault_code=9\n", self.t, self.t
            return b"[SS] state=READY fault_code=0\n", self.t, self.t
        if cmd == "safety_status":
            return (b"[SAFETY] request=DISABLE(LOW) pad=0 expected_FB=HIGH "
                    b"FB=1 -> verdict=OK\n"), self.t, self.t
        if cmd == "motor_status":
            return (("[MOT] state=%d\n[MOT] cur_hz=0 tgt_hz=0\n"
                     "[MOT] armed=%d\n[MOT] arm_gate_mask=0x0\n"
                     % (4 if self.latched else 0, self.armed)).encode(),
                    self.t, self.t)
        if cmd == "flash_logstat":
            return (("slots=%d/%d valid=%d bad=0\n"
                     "queued=%d completed=%d failed=%d queue_dropped=%d "
                     "last_error=0\n"
                     % (self.used, self.slots_total, self.used, self.acc,
                        self.done, self.fail, self.dropped)).encode(),
                    self.t, self.t)
        if cmd == "blackbox_status":
            return (("[BB] state=0 session=%d written=%d last_err=0 fail=%d\n"
                     "[BB] write drops=%d frames\n"
                     "[BB] flash: used=%d valid=%d bad=0 acc=%d done=%d "
                     "fail=%d drop=%d\n"
                     % (self.session, self.written, self.fail,
                        self.write_drops, self.used, self.used, self.acc,
                        self.done, self.fail, self.drop)).encode(),
                    self.t, self.t)
        if cmd == "motor_arm":
            if self.latched:
                return b"[MOT] arm REFUSED (gates failing)\n", self.t, self.t
            self.armed = 1
            if self.arm_reply_lost:
                return b"", self.t, self.t
            return b"[MOT] ARMED\n", self.t, self.t
        if cmd == "motor_start":
            self.started = True
            return b"[MOT] started\n", self.t, self.t
        if cmd == "safety_trip CONFIRM":
            if self.trip_latched:
                self.latched = True
                self.armed = 0
                self.session += 1
                for i in range(EXPECTED_FRAMES):
                    ev = 0 if i < 200 else 9
                    st = 300 if ev == 0 else 0
                    self.frames[self.used + i] = (self.session, ev, st)
                self.used += EXPECTED_FRAMES
                self.written += EXPECTED_FRAMES
                self.acc += EXPECTED_FRAMES
                self.done += EXPECTED_FRAMES
            return b"[SS] s[SS] ==> FAULT_LATCHED code=9\n", self.t, self.t
        if cmd == "motor_disarm":
            if not self.latched:
                return b"[MOT] disarmed\n", self.t, self.t
            self.armed = 0
            return b"[MOT] DISARM (STEP api-accepted)\n", self.t, self.t
        if cmd.startswith("flash_dump "):
            _, a, b = cmd.split()
            a, b = int(a), int(b)
            out = ",".join(COLUMNS) + "\n"
            for s in range(a, a + b):
                if s == self.missing_slot or s not in self.frames:
                    out += "slot %d empty\n" % s
                else:
                    f = self.frames[s]
                    out += ("%d,%d,%d,%d,%d,31,60,200,1,2,3,%d,900,1\n"
                            % (s, s + 1, f[0], s * 10, f[1], f[2]))
            return out.encode(), self.t, self.t
        if cmd == "fault_reset":
            if not self.latched:
                return b"[SS] fault_reset REFUSED\n", self.t, self.t
            self.latched = False
            return b"[SS] ==> MANUAL_CLEAR\n", self.t, self.t
        if cmd == "system_selftest":
            return b"[SS] self-test done: required=PASS\n[SS] ==> READY\n", self.t, self.t
        return b"OK\n", self.t, self.t


def _verify_ok(session, deadline):
    return EXPECTED_FRAMES


def selftest():
    passed = failed = 0

    def case(cid, fn):
        nonlocal passed, failed
        try:
            fn(); ok, detail = True, "ok"
        except Exception as exc:
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        print("CASE|%s|%s|%s" % (cid, "PASS" if ok else "FAIL", detail))

    def c1():
        dev = FDevice(); clock = FClock()
        s = Session(dev, clock=clock, sleep=clock.sleep)
        s.run_fault()
        if sum(1 for _, c in dev.sent if c == "motor_disarm") != 1:
            raise AssertionError("disarm not exactly once: %s" % dev.sent)
        if any(c == "fault_reset" for _, c in dev.sent):
            raise AssertionError("automatic recovery attempted")

    def c2():
        dev = FDevice(); clock = FClock()
        s = Session(dev, clock=clock, sleep=clock.sleep, verify_log=_verify_ok)
        dev.hang_after_start.add("safety_state")
        try:
            s.run_fault(deadline_s=6.0)
        except Abort:
            pass
        if s.disarm_time is None or s.disarm_time > 5.01:
            raise AssertionError("no disarm within 5s: %s" % s.disarm_time)
        if s.primary_error is None:
            raise AssertionError("primary error lost")
        if not s.disarm_confirmed or s.cleanup_unknown:
            raise AssertionError("disarm confirmation state wrong")

    def c2b():
        dev = FDevice(); clock = FClock()
        s = Session(dev, clock=clock, sleep=clock.sleep, verify_log=_verify_ok)
        # Both the disarm echo and the contract-field confirmation are lost,
        # so the only honest outcome is UNKNOWN (never "already disabled").
        dev.hang_after_start.update({"motor_disarm", "safety_status"})
        try:
            s.run_fault()
        except Abort:
            pass
        if not s.cleanup_unknown or s.disarm_confirmed:
            raise AssertionError("lost disarm reply not UNKNOWN")
        if any(c == "fault_reset" for _, c in dev.sent):
            raise AssertionError("recovery after unknown disarm")

    def c3():
        dev = FDevice(); clock = FClock()
        dev.arm_reply_lost = True
        s = Session(dev, clock=clock, sleep=clock.sleep)
        try:
            s.run_fault()
        except Abort:
            pass
        if sum(1 for _, c in dev.sent if c == "motor_disarm") != 1:
            raise AssertionError("cleanup missing after lost arm reply")

    def c4():
        dev = FDevice(); clock = FClock()
        s = Session(dev, clock=clock, sleep=clock.sleep, verify_log=_verify_ok)
        dev.armed = 1
        try:
            s.run_fault()
        except Abort:
            pass
        if any(c == "motor_start" for _, c in dev.sent):
            raise AssertionError("start sent after static refusal")

    def c5():
        base = {"used": 300, "valid": 300, "bad": 0, "accepted": 300,
                "completed": 300, "failed": 0, "dropped": 51,
                "bb_write_drops": 0}
        rows = []
        for i in range(300):
            rows.append({"session": 1, "event": 0 if i < 200 else 9,
                         "step_hz": 300 if i < 200 else 0,
                         "time_ms": 1000 + i})
        ok, why = log_is_complete(base, rows, expected_session=1)
        if not ok:
            raise AssertionError("expected complete: %s" % why)
        bad = dict(base); bad["completed"] = 299
        if log_is_complete(bad, rows)[0]:
            raise AssertionError("completed=299 accepted")
        if log_is_complete(base, rows[:-1])[0]:
            raise AssertionError("missing row accepted")
        wrong = [dict(r) for r in rows]
        wrong[-1]["session"] = 2
        if log_is_complete(base, wrong, expected_session=1)[0]:
            raise AssertionError("wrong session accepted")

    def c6():
        if "serial" in sys.modules:
            raise AssertionError("pyserial imported during selftest")

    def c7():
        dev = FDevice(); clock = FClock()
        s = Session(dev, clock=clock, sleep=clock.sleep)
        res = s.run_fault(allow_recovery=True)
        if not res["recovered"] or not any(c == "fault_reset" for _, c in dev.sent):
            raise AssertionError("verified recovery not executed")
        # One missing new row: reject and forbid recovery.
        dev2 = FDevice(); clock2 = FClock()
        dev2.missing_slot = 150
        s2 = Session(dev2, clock=clock2, sleep=clock2.sleep)
        try:
            s2.run_fault(allow_recovery=True)
        except Abort:
            pass
        if any(c == "fault_reset" for _, c in dev2.sent):
            raise AssertionError("recovery after incomplete readback")
        if s2.primary_error is None or "slot coverage" not in s2.primary_error:
            raise AssertionError("wrong primary error: %s" % s2.primary_error)

    def c8():
        dev = FDevice(); clock = FClock()
        dev.used = 4096
        s = Session(dev, clock=clock, sleep=clock.sleep, verify_log=_verify_ok)
        try:
            s.run_fault()
        except Abort:
            pass
        if any(c in ("motor_arm", "motor_start") for _, c in dev.sent):
            raise AssertionError("motor command sent without free log space")

    def c9():
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="m6_raw_")
        try:
            dev = FDevice(); clock = FClock()
            s = Session(dev, clock=clock, sleep=clock.sleep, out_dir=d)
            s.run_fault()
            with open(os.path.join(d, "raw_index.jsonl"), encoding="utf-8") as fh:
                recs = [json.loads(line) for line in fh]
            if len(recs) != s.seq or s.seq < 10:
                raise AssertionError("raw index count %d != seq %d"
                                     % (len(recs), s.seq))
            for rec in recs:
                p = os.path.join(d, rec["file"])
                if os.path.getsize(p) != rec["bytes"]:
                    raise AssertionError("raw size mismatch: %s" % rec["file"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def c10():
        try:
            main([])
        except SystemExit as exc:
            if exc.code != 2:
                raise AssertionError("bad argument exit code %s" % exc.code)
        else:
            raise AssertionError("missing-argument invocation did not exit")
        if "serial" in sys.modules:
            raise AssertionError("pyserial imported on argument error")

    case("M6D01-interleaved_trip_still_disarms", c1)
    case("M6D02-read_timeout_cleanup_within_5s", c2)
    case("M6D02b-lost_disarm_reply_marks_unknown", c2b)
    case("M6D03-lost_arm_reply_still_cleans_up", c3)
    case("M6D04-static_refusal_no_start", c4)
    case("M6D05-log_300_accept_299_missing_reject", c5)
    case("M6D06-selftest_no_serial_object", c6)
    case("M6D07-incomplete_readback_forbids_recovery", c7)
    case("M6D08-no_space_blocks_motor_commands", c8)
    case("M6D09-raw_txrx_preserved", c9)
    case("M6D10-argument_error_no_serial", c10)
    print("SUMMARY|cases=%d pass=%d fail=%d" % (passed + failed, passed, failed))
    return 0 if failed == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--out-dir")
    ap.add_argument("--allow-recovery", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.port:
        ap.error("--port or --selftest required")
    if not args.out_dir:
        args.out_dir = "m6_field_%s" % time.strftime("%Y%m%d_%H%M%S")
    import serial  # lazy: never imported by selftest
    ser = serial.Serial(args.port, 115200, timeout=0.05)
    try:
        class RealLink:
            def send(self, cmd, timeout, quiet):
                ser.reset_input_buffer()
                t0 = time.monotonic()
                ser.write((cmd + "\r\n").encode("ascii"))
                buf = b""
                last = t0
                end = t0 + timeout
                while time.monotonic() < end:
                    chunk = ser.read(max(1, ser.in_waiting))
                    if chunk:
                        buf += chunk
                        last = time.monotonic()
                    elif buf and time.monotonic() - last >= quiet:
                        break
                return buf, t0, time.monotonic()
        s = Session(RealLink(), out_dir=args.out_dir)
        try:
            res = s.run_fault(allow_recovery=args.allow_recovery)
            print("FAULT_RESULT=PASS %s" % json.dumps(res, sort_keys=True))
            return 0
        except Abort as exc:
            print("FAULT_RESULT=FAIL primary=%s cleanup_confirmed=%s "
                  "cleanup_unknown=%s cleanup_late=%s"
                  % (exc, s.disarm_confirmed, s.cleanup_unknown,
                     s.cleanup_late))
            return 1
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
