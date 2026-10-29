#!/usr/bin/env python3
"""m6_field_candidate.py - M6 offline candidate for the future fault session.

Scope: the fault phase only (arm -> start -> trip -> bounded disarm -> optional
verified new-log recovery). Archive/clear remain in the M5 script and are not
re-implemented here. This file is OFFLINE_VERIFIED / NOT_BOARD_EXECUTED.

Design rules:
  - one writer, monotonic deadlines, no reconnect/retry;
  - responsibility flag is set BEFORE arm/start; lost responses do not mean
    the command did not execute;
  - finally always attempts at most one motor_disarm if output may be on;
    primary error is preserved, cleanup result is separate;
  - trip acceptance uses canonical FAULT_LATCHED code 9, never a console line;
  - accepted==completed is queue drain; queue_dropped is a retry counter and
    does not replace slot coverage/session/event/readback checks;
  - recovery only after a complete 300-slot readback; no automatic reset.

--selftest constructs no pyserial/SerialPort object.
"""
import argparse
import re
import sys
import time

EXPECTED_FRAMES = 300


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


class Session:
    def __init__(self, link, clock=time.monotonic, sleep=time.sleep,
                 verify_log=None):
        self.link = link
        self.clock = clock
        self.sleep = sleep
        self.verify_log = verify_log
        self.raw = []
        self.output_may_be_on = False
        self.disarm_confirmed = False
        self.disarm_attempts = 0
        self.disarm_time = None
        self.primary_error = None
        self.cleanup_unknown = False

    def q(self, cmd, deadline, quiet=0.15):
        remain = max(0.0, deadline - self.clock())
        if remain <= 0:
            raise Timeout("deadline before %s" % cmd)
        raw, t0, t1 = self.link.send(cmd, remain, quiet)
        self.raw.append((cmd, raw, t0, t1))
        return _text(raw)

    def wait_until(self, t):
        while self.clock() < t:
            self.sleep(min(0.05, t - self.clock()))

    def disarm_once(self, deadline):
        if self.disarm_attempts:
            return
        self.disarm_attempts += 1
        self.disarm_time = self.clock()
        try:
            txt = self.q("motor_disarm", deadline, 0.2)
            # Only an explicit LOW/pad/ENN confirmation counts as confirmed.
            if re.search(r"request=DISABLE\(LOW\).*pad=0", txt) and \
               re.search(r"ENN.*raw=1|ENN_fb=1|FB=1", txt):
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
        fl = self.q("flash_logstat", deadline)
        bb = self.q("blackbox_status", deadline)
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
        used = parse_u(fl, r"slots=(\d+)/", "log used")
        valid = parse_u(fl, r"valid=(\d+)", "log valid")
        accepted = parse_u(fl, r"queued=(\d+)", "accepted")
        completed = parse_u(fl, r"completed=(\d+)", "completed")
        if used != valid or accepted != completed:
            raise Abort("log/queue not drained: %s/%s acc=%s done=%s"
                        % (used, valid, accepted, completed))
        if "state=0" not in bb or "fail=0" not in bb:
            raise Abort("blackbox not idle/clean")
        return used

    def run_fault(self, deadline_s=20.0, allow_recovery=False):
        t0 = self.clock()
        deadline = t0 + deadline_s
        recovered = False
        try:
            self._static_preflight(deadline)
            self.q("motor_dir 0", deadline)
            # Arm may make the output eligible: responsibility starts here.
            self.output_may_be_on = True
            arm = self.q("motor_arm", deadline)
            armed = "ARMED" in arm
            if not armed:
                mo = self.q("motor_status", deadline)
                armed = "armed=1" in mo
            if not armed:
                raise Abort("arm refused; no start sent")
            self.q("motor_target 300", deadline)
            t_start = self.clock()
            self.q("motor_start", deadline)
            self.wait_until(min(t_start + 3.0, deadline))
            self.q("safety_trip CONFIRM", deadline)
            latched = False
            while self.clock() < min(t_start + 4.8, deadline):
                ss = self.q("safety_state", deadline)
                if "state=FAULT_LATCHED" in ss and "fault_code=9" in ss:
                    latched = True
                    break
                self.sleep(0.1)
            # Fallback cleanup is independent of the trip response text.
            self.disarm_once(min(t_start + 5.0, deadline))
            if not latched:
                raise Abort("no FAULT_LATCHED code 9")
            used = None
            if self.verify_log is not None:
                used = self.verify_log(self, deadline)
            else:
                raise Abort("new-log verifier not configured")
            if used != EXPECTED_FRAMES:
                raise Abort("new log frames %s != %d" % (used, EXPECTED_FRAMES))
            if not self.disarm_confirmed:
                raise Abort("output disabled not confirmed")
            if allow_recovery:
                self.q("fault_reset", deadline)
                self.q("system_selftest", min(self.clock() + 10.0, deadline))
                recovered = True
            return {"latched": True, "disarm": self.disarm_confirmed,
                    "frames": used, "recovered": recovered}
        except Abort as exc:
            self.primary_error = str(exc)
            raise
        finally:
            if self.output_may_be_on and not self.disarm_confirmed:
                self.disarm_once(min(t0 + 5.0, deadline))


def log_is_complete(stats, rows, expected=EXPECTED_FRAMES):
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
    if any(int(r["session"]) != 1 for r in rows):
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
            return (("[MOT] cur_hz=0 tgt_hz=0\n[MOT] armed=%d\n"
                     "[MOT] arm_gate_mask=0x0\n" % self.armed).encode(),
                    self.t, self.t)
        if cmd == "flash_logstat":
            return b"slots=0/4096 valid=0 bad=0\nqueued=0 completed=0 failed=0 queue_dropped=0 last_error=0\n", self.t, self.t
        if cmd == "blackbox_status":
            return b"[BB] state=0 session=0 written=0 last_err=0 fail=0\n[BB] write drops=0 frames\n", self.t, self.t
        if cmd == "motor_arm":
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
            return b"[SS] s[SS] ==> FAULT_LATCHED code=9\n", self.t, self.t
        if cmd == "motor_disarm":
            self.armed = 0
            return b"[SAFETY] request=DISABLE(LOW) pad=0 ENN raw=1\n", self.t, self.t
        return b"OK\n", self.t, self.t


def _verify_ok(session, deadline):
    return 300


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
        s = Session(dev, clock=clock, sleep=clock.sleep, verify_log=_verify_ok)
        s.run_fault()
        if sum(1 for _, c in dev.sent if c == "motor_disarm") != 1:
            raise AssertionError("disarm not exactly once: %s" % dev.sent)

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

    def c3():
        dev = FDevice(); clock = FClock()
        dev.arm_reply_lost = True
        s = Session(dev, clock=clock, sleep=clock.sleep, verify_log=_verify_ok)
        try:
            s.run_fault()
        except Abort:
            pass
        # arm executed despite lost reply -> cleanup must still be attempted
        if sum(1 for _, c in dev.sent if c == "motor_disarm") != 1:
            raise AssertionError("cleanup missing after lost arm reply")

    def c4():
        # Static preflight refusal must not send start.
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
        ok, why = log_is_complete(base, rows)
        if not ok:
            raise AssertionError("expected complete: %s" % why)
        bad = dict(base); bad["completed"] = 299
        if log_is_complete(bad, rows)[0]:
            raise AssertionError("completed=299 accepted")
        if log_is_complete(base, rows[:-1])[0]:
            raise AssertionError("missing row accepted")

    def c6():
        # --selftest reaches here without importing pyserial.
        if "serial" in sys.modules:
            raise AssertionError("pyserial imported during selftest")

    case("M6D01-interleaved_trip_still_disarms", c1)
    case("M6D02-read_timeout_cleanup_within_5s", c2)
    case("M6D03-lost_arm_reply_still_cleans_up", c3)
    case("M6D04-static_refusal_no_start", c4)
    case("M6D05-log_300_accept_299_missing_reject", c5)
    case("M6D06-selftest_no_serial_object", c6)
    print("SUMMARY|cases=%d pass=%d fail=%d" % (passed + failed, passed, failed))
    return 0 if failed == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.port:
        ap.error("--port or --selftest required")
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
        s = Session(RealLink())
        s.run_fault(allow_recovery=False)
    finally:
        ser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
