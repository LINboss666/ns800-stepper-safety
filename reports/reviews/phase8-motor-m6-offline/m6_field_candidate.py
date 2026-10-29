#!/usr/bin/env python3
"""m6_field_candidate.py - M6 field candidate, offline-fixed after review.

Scope: the fault phase only (arm -> start -> trip -> bounded disarm -> full
new-log readback -> optional verified recovery). Archive/clear remain in the
M5 script; this file neither clears nor archives. This file is
FIELD_CANDIDATE_OFFLINE_FIXED / NOT_BOARD_EXECUTED.

Review fixes:
  D-1: Session, the fake device and the transport share one monotonic clock;
       the real transport sets write/read timeouts from the remaining budget
       and recomputes before each wait. Late cleanup is recorded as late.
  D-2: transport/SerialException/OSError and evidence-save failures are
       preserved as the primary error; cleanup errors are recorded separately;
       an unconfirmable disable is UNKNOWN, never success.
  D-3: arm success is decided only by one complete motor_status snapshot
       (state=IDLE, armed=1, cur=0, tgt=0), not by a response substring.

--selftest constructs no pyserial/SerialPort object.
"""
import argparse
import json
import os
import re
import sys
import time
import traceback

EXPECTED_FRAMES = 300
COLUMNS = ("slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,"
           "az_mg,step_hz,vibration_mg,flags").split(",")


class Abort(Exception):
    pass


class Timeout(Abort):
    pass


class TransportError(Abort):
    """A serial write/read failure; the original OSError stays chained."""


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


def parse_motor_status(text):
    """Exactly one state/armed/hz group. Missing or duplicated fields are a
    refusal, never a guess. This replaces substring success checks."""
    state = re.findall(r"(?m)^\[MOT\] state=(\d+)\b", text)
    armed = re.findall(r"(?m)^\[MOT\] armed=(\d+)\b", text)
    hz = re.findall(r"(?m)^\[MOT\] cur_hz=(\d+) tgt_hz=(\d+)\b", text)
    if len(state) != 1 or len(armed) != 1 or len(hz) != 1:
        raise Abort("motor_status fields missing/duplicated: state=%d "
                    "armed=%d hz=%d" % (len(state), len(armed), len(hz)))
    return {"state": int(state[0]), "armed": int(armed[0]),
            "cur": int(hz[0][0]), "tgt": int(hz[0][1])}


def load_archive_gate(path):
    """The M5 archive phase must have accepted the old log, and a clear (if
    any) must have a verified second copy, before a new fault may start."""
    try:
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError) as exc:
        raise Abort("archive state unreadable: %s" % exc)
    if not st.get("archive_accept"):
        raise Abort("archive not accepted")
    if st.get("clear_done"):
        second = st.get("second_copy")
        if not second:
            raise Abort("second copy missing after clear")
        if not os.path.isfile(os.path.join(second, "COPY_VERIFIED.json")):
            raise Abort("second copy verification missing")
    elif int(st.get("free", 0)) < EXPECTED_FRAMES:
        raise Abort("archive reports insufficient free slots")
    return st


class SerialLink:
    """The real transport path. Tests inject a fake serial object behind this
    same send(), so the timeout/blocking logic is exercised, not bypassed."""

    def __init__(self, ser, clock=time.monotonic):
        self.ser = ser
        self.clock = clock
        self.written = []

    def send(self, cmd, timeout, quiet=0.15):
        if timeout <= 0:
            raise Timeout("deadline before %s" % cmd)
        try:
            self.ser.reset_input_buffer()
            t0 = self.clock()
            end = t0 + timeout
            self.written.append((t0, cmd))
            remain = end - self.clock()
            if remain <= 0:
                raise Timeout("deadline before %s" % cmd)
            try:
                self.ser.write_timeout = remain
            except (AttributeError, ValueError):
                pass
            self.ser.write((cmd + "\r\n").encode("ascii"))
            buf = b""
            last = self.clock()
            while True:
                now = self.clock()
                if now >= end:
                    break
                remain = end - now
                try:
                    self.ser.timeout = min(0.05, remain)
                except (AttributeError, ValueError):
                    pass
                chunk = self.ser.read(max(1, getattr(self.ser,
                                                    "in_waiting", 0)))
                now = self.clock()
                if chunk:
                    buf += chunk
                    last = now
                else:
                    # A missing first byte must not consume the whole deadline:
                    # give it a short bounded grace, then leave time for cleanup.
                    limit = quiet if buf else max(quiet, 0.5)
                    if now - last >= limit:
                        break
                    if now >= end:
                        break
            return buf, t0, self.clock()
        except Timeout:
            raise
        except OSError as exc:
            # SerialException/SerialTimeoutException derive from OSError.
            raise TransportError("write/read failed: %s" % exc) from exc


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
        self.raw_index = None
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            self.raw_index = open(os.path.join(out_dir, "raw_index.jsonl"),
                                  "a", encoding="utf-8")
        self.output_may_be_on = False
        self.arm_time = None
        self.start_time = None
        self.pre_used = 0
        self.pre_valid = 0
        self.bb0 = None
        self.disarm_confirmed = False
        self.disarm_attempts = 0
        self.disarm_time = None
        self.cleanup_unknown = False
        self.cleanup_late = False
        self.cleanup_error = None
        self.cleanup_traceback = None
        self.primary_error = None
        self.primary_exc = None
        self.primary_traceback = None
        self.trip_response_error = None
        self.trip_response_exc = None
        self.trip_response_traceback = None
        self.evidence_error = None

    def q(self, cmd, deadline, quiet=0.15):
        remain = deadline - self.clock()
        if remain <= 0:
            raise Timeout("deadline before %s" % cmd)
        raw, t0, t1 = self.link.send(cmd, remain, quiet)
        self.raw.append((cmd, raw, t0, t1))
        self._save_raw(cmd, raw, t0, t1)
        return _text(raw)

    def _save_raw(self, cmd, raw, t0, t1):
        if not self.out_dir:
            return
        try:
            self.seq += 1
            name = "%03d_%s.bin" % (self.seq,
                                    re.sub(r"[^A-Za-z0-9_.-]+", "_", cmd))
            with open(os.path.join(self.out_dir, name), "wb") as fh:
                fh.write(raw)
            if self.raw_index is not None:
                self.raw_index.write(json.dumps(
                    {"seq": self.seq, "cmd": cmd, "file": name,
                     "bytes": len(raw), "t_tx": t0, "t_rx": t1}) + "\n")
                self.raw_index.flush()
        except (OSError, ValueError) as exc:
            # Evidence failure is recorded, never allowed to hide the primary
            # command/safety result.
            self.evidence_error = "%s: %s" % (type(exc).__name__, exc)

    def write_failure(self):
        if not self.out_dir:
            return
        try:
            doc = {"primary_error": self.primary_error,
                   "primary_type": (type(self.primary_exc).__name__
                                    if self.primary_exc is not None else None),
                   "primary_traceback": self.primary_traceback,
                   "cleanup_error": self.cleanup_error,
                   "cleanup_traceback": self.cleanup_traceback,
                   "cleanup_unknown": self.cleanup_unknown,
                   "cleanup_late": self.cleanup_late,
                   "disarm_confirmed": self.disarm_confirmed,
                   "evidence_error": self.evidence_error,
                   "sent": [{"t": t, "cmd": c}
                            for t, c in getattr(self.link, "written", [])]}
            with open(os.path.join(self.out_dir, "failure.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
        except (OSError, ValueError) as exc:
            self.evidence_error = "%s: %s" % (type(exc).__name__, exc)

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
            # Confirmation is a contract field, not a guessed sentence.
            st = self.q("safety_status", deadline)
            if re.search(r"request=DISABLE\(LOW\)", st) and \
               re.search(r"pad=0\b", st):
                self.disarm_confirmed = True
                self.output_may_be_on = False
            else:
                self.cleanup_unknown = True
                if self.cleanup_error is None:
                    self.cleanup_error = "disarm status not confirmed"
        except Exception as exc:
            # Cleanup must never replace the primary failure. A transport
            # exception or query failure leaves the output state UNKNOWN.
            self.cleanup_unknown = True
            self.cleanup_error = "%s: %s" % (type(exc).__name__, exc)
            self.cleanup_traceback = traceback.format_exc()

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
        m = parse_motor_status(mo)
        if m["state"] != 0 or m["armed"] != 0 or m["cur"] != 0 or m["tgt"] != 0:
            raise Abort("motor not at rest/disarmed: %s" % m)
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

    def _confirm_arm(self, deadline):
        mo = self.q("motor_status", min(deadline, self.clock() + 2.0))
        m = parse_motor_status(mo)
        if m["state"] != 0 or m["armed"] != 1 or m["cur"] != 0 or m["tgt"] != 0:
            raise Abort("arm not confirmed by motor_status: %s" % m)

    def _confirm_running(self, deadline):
        mo = self.q("motor_status", min(deadline, self.clock() + 2.0))
        m = parse_motor_status(mo)
        if m["state"] not in (1, 2) or m["tgt"] != 300 or m["cur"] == 0:
            raise Abort("start not confirmed running: %s" % m)

    def run_fault(self, deadline_s=20.0, allow_recovery=False,
                  archive_state=None):
        t0 = self.clock()
        deadline = t0 + deadline_s
        recovered = False
        try:
            if archive_state:
                load_archive_gate(archive_state)
            self._static_preflight(deadline)
            self.q("motor_dir 0", deadline)
            # Responsibility starts BEFORE arm: a lost reply does not mean the
            # enable request was not executed.
            self.arm_time = self.clock()
            self.output_may_be_on = True
            arm_text = self.q("motor_arm", min(deadline,
                                               self.clock() + 10.0))
            low = arm_text.lower()
            if "refused" in low or "not armed" in low:
                raise Abort("arm refused by response: %r" % arm_text)
            self._confirm_arm(deadline)
            self.q("motor_target 300", min(deadline, self.clock() + 2.0))
            t_start = self.clock()
            self.start_time = t_start
            self.q("motor_start", min(deadline, t_start + 1.5))
            self.wait_until(min(t_start + 1.0, deadline))
            self._confirm_running(deadline)
            self.wait_until(min(t_start + 3.0, deadline))
            try:
                # Bound the trip read so fallback disarm keeps its budget.
                self.q("safety_trip CONFIRM", min(deadline, t_start + 4.0))
            except Abort as exc:
                self.trip_response_error = "%s: %s" % (type(exc).__name__, exc)
                self.trip_response_exc = exc
                self.trip_response_traceback = traceback.format_exc()
            latched = False
            while self.clock() < min(t_start + 4.8, deadline):
                try:
                    ss = self.q("safety_state", min(deadline,
                                                    t_start + 4.8))
                except Abort as exc:
                    self.trip_response_error = ("%s: %s"
                                                % (type(exc).__name__, exc))
                    self.trip_response_exc = exc
                    self.trip_response_traceback = traceback.format_exc()
                    break
                if "state=FAULT_LATCHED" in ss and "fault_code=9" in ss:
                    latched = True
                    break
                self.sleep(0.1)
            # Fallback cleanup is independent of the trip response text.
            self.disarm_once(min(t_start + 5.0, deadline))
            if not latched:
                extra = ("; trip transport: %s" % self.trip_response_error
                         if self.trip_response_error else "")
                if self.trip_response_exc is not None:
                    raise Abort("no FAULT_LATCHED code 9" + extra)                         from self.trip_response_exc
                raise Abort("no FAULT_LATCHED code 9" + extra)
            used = self.verify_log(self, min(deadline, self.clock() + 60.0))
            if used != EXPECTED_FRAMES:
                raise Abort("new log frames %s != %d"
                            % (used, EXPECTED_FRAMES))
            if not self.disarm_confirmed:
                raise Abort("output disabled not confirmed")
            if allow_recovery:
                st = self.q("safety_status", min(deadline, self.clock() + 2.0))
                if "request=DISABLE(LOW)" not in st or "pad=0" not in st:
                    raise Abort("recovery refused: output not LOW")
                reset = self.q("fault_reset", min(deadline,
                                                  self.clock() + 30.0))
                if "MANUAL_CLEAR" not in reset:
                    raise Abort("fault_reset did not reach MANUAL_CLEAR")
                self.q("system_selftest", min(deadline,
                                              self.clock() + 30.0))
                ss = self.q("safety_state", min(deadline,
                                                self.clock() + 2.0))
                if "state=READY" not in ss or "fault_code=0" not in ss:
                    raise Abort("selftest did not restore READY")
                recovered = True
            if self.evidence_error is not None and self.primary_error is None:
                raise Abort("evidence error: %s" % self.evidence_error)
            return {"latched": True, "disarm": self.disarm_confirmed,
                    "frames": used, "recovered": recovered,
                    "cleanup_unknown": self.cleanup_unknown,
                    "cleanup_late": self.cleanup_late}
        except Exception as exc:
            if self.primary_error is None:
                self.primary_error = "%s: %s" % (type(exc).__name__, exc)
                self.primary_exc = exc
                self.primary_traceback = traceback.format_exc()
            raise
        finally:
            if self.output_may_be_on and not self.disarm_confirmed:
                base = (self.start_time if self.start_time is not None
                        else (self.arm_time if self.arm_time is not None
                              else t0))
                clean_dl = min(base + 5.0, deadline)
                if clean_dl <= self.clock():
                    # The planned window was consumed by a hung read/write;
                    # still make one short bounded attempt and record it late.
                    clean_dl = self.clock() + 0.5
                    self.cleanup_late = True
                self.disarm_once(clean_dl)


def default_verify_log(sess, deadline):
    """Full readback of the new slots; rejects any missing row or bad semantics."""
    fl = parse_logstat(sess.q("flash_logstat", min(deadline,
                                                   sess.clock() + 3.0)))
    bb = parse_blackbox(sess.q("blackbox_status", min(deadline,
                                                     sess.clock() + 3.0)))
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
    if stats["used"] != expected or stats["valid"] != expected or \
       stats["bad"] != 0:
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
    if ev.count(0) + ev.count(9) != expected or not ev or ev[0] != 0 or \
       ev[-1] != 9:
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
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += max(0.0, dt)

    def sleep(self, dt):
        self.advance(dt)


class FDevice:
    """Command semantics only; all time comes from the shared FClock."""

    def __init__(self, clock):
        self.clock = clock
        self.armed = 0
        self.latched = False
        self.started = False
        self.motion_started = False
        self.started_ok = True
        self.arm_sent = False
        self.session_id = 0
        self.used = 0
        self.slots_total = 4096
        self.written = 0
        self.acc = self.done = 0
        self.fail = 0
        self.drop = 0
        self.write_drops = 0
        self.dropped = 51
        self.frames = {}
        self.missing_slot = None
        self.arm_mode = "normal"          # normal/refused_text/not_armed/armed_zero
        self.motor_status_override = None
        self.write_block = {}             # cmd -> seconds before OSError
        self.response_lost = set()
        self.response_lost_after_arm = set()
        self.read_block_cmd_after_start = {}  # cmd -> seconds to block reads

    def on_command(self, cmd):
        if cmd == "motor_arm":
            self.arm_sent = True
            if self.latched or self.arm_mode in ("refused_text", "not_armed",
                                                 "armed_zero"):
                self.armed = 0
            else:
                self.armed = 1
        elif cmd == "motor_start":
            self.motion_started = True
            self.started = bool(self.started_ok)
        elif cmd == "safety_trip CONFIRM":
            if not self.latched:
                self.latched = True
                self.armed = 0
                self.started = False
                self.session_id += 1
                for i in range(EXPECTED_FRAMES):
                    ev = 0 if i < 200 else 9
                    st = 300 if ev == 0 else 0
                    self.frames[self.used + i] = (self.session_id, ev, st)
                self.used += EXPECTED_FRAMES
                self.written += EXPECTED_FRAMES
                self.acc += EXPECTED_FRAMES
                self.done += EXPECTED_FRAMES
        elif cmd == "motor_disarm":
            self.armed = 0
        elif cmd == "system_selftest":
            pass

    def _motor_text(self):
        if self.motor_status_override is not None:
            return self.motor_status_override
        if self.latched:
            state, cur, tgt = 4, 0, 0
        elif self.started:
            state, cur, tgt = 1, 300, 300
        else:
            state, cur, tgt = 0, 0, 0
        return ("[MOT] state=%d name=OTHER\n"
                "[MOT] cur_hz=%d tgt_hz=%d accel=2000 decel=2000 dir=0\n"
                "[MOT] armed=%d drv_en_request=%d health=OK\n"
                "[MOT] arm_gate_mask=0x0\n"
                % (state, cur, tgt, self.armed, 1 if self.started else 0))

    def response(self, cmd):
        if cmd == "safety_state":
            if self.latched:
                return "[SS] state=FAULT_LATCHED fault_code=9\n"
            return "[SS] state=READY fault_code=0\n"
        if cmd == "safety_status":
            return ("[SAFETY] request=DISABLE(LOW) pad=0 expected_FB=HIGH "
                    "FB=1 -> verdict=OK\n")
        if cmd == "motor_status":
            return self._motor_text()
        if cmd == "flash_logstat":
            return ("slots=%d/%d valid=%d bad=0\n"
                    "queued=%d completed=%d failed=%d queue_dropped=%d "
                    "last_error=0\n"
                    % (self.used, self.slots_total, self.used, self.acc,
                       self.done, self.fail, self.dropped))
        if cmd == "blackbox_status":
            return ("[BB] state=0 session=%d written=%d last_err=0 fail=%d\n"
                    "[BB] write drops=%d frames\n"
                    "[BB] flash: used=%d valid=%d bad=0 acc=%d done=%d "
                    "fail=%d drop=%d\n"
                    % (self.session_id, self.written, self.fail,
                       self.write_drops, self.used, self.used, self.acc,
                       self.done, self.fail, self.drop))
        if cmd == "motor_arm":
            if self.latched:
                return "[MOT] arm REFUSED (gates failing)\n"
            if self.arm_mode == "refused_text":
                return "arm refused: not armed\n"
            if self.arm_mode == "not_armed":
                return "[MOT] not armed\n"
            return "[MOT] ARMED\n"
        if cmd == "motor_start":
            return "[MOT] started\n" if self.started else "[MOT] not started\n"
        if cmd == "safety_trip CONFIRM":
            return "[SS] s[SS] ==> FAULT_LATCHED code=9\n"
        if cmd == "motor_disarm":
            return "[MOT] DISARM (STEP api-accepted)\n"
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
            return out
        if cmd == "fault_reset":
            if not self.latched:
                return "[SS] fault_reset REFUSED\n"
            self.latched = False
            return "[SS] ==> MANUAL_CLEAR\n"
        if cmd == "system_selftest":
            return "[SS] self-test done: required=PASS\n[SS] ==> READY\n"
        return "OK\n"


class FakeSerial:
    """A pySerial-shaped object; all blocking consumes the shared clock."""

    def __init__(self, dev, clock):
        self.dev = dev
        self.clock = clock
        self.written = []
        self.applied = []
        self.reads = []
        self.pending = b""
        self.in_waiting = 0
        self.timeout = 0.05
        self.write_timeout = 0.05
        self.block_left = 0.0

    def reset_input_buffer(self):
        self.pending = b""
        self.in_waiting = 0
        self.block_left = 0.0

    def write(self, data):
        cmd = data.decode("ascii").strip()
        self.written.append((self.clock(), cmd))
        if cmd in self.dev.write_block:
            dt = self.dev.write_block[cmd]
            cap = self.write_timeout if self.write_timeout is not None else dt
            self.clock.advance(min(dt, cap))
            raise OSError("WRITE_TIMEOUT:%s" % cmd)
        self.dev.on_command(cmd)
        self.applied.append(cmd)
        if self.dev.motion_started and cmd in self.dev.read_block_cmd_after_start:
            self.block_left = self.dev.read_block_cmd_after_start[cmd]
        lost = cmd in self.dev.response_lost
        if self.dev.arm_sent and cmd in self.dev.response_lost_after_arm:
            lost = True
        if lost:
            self.pending = b""
        else:
            self.pending = self.dev.response(cmd).encode("utf-8")
        self.in_waiting = len(self.pending)
        return len(data)

    def read(self, n):
        self.reads.append((self.clock(), n))
        if self.block_left > 0:
            dt = min(self.block_left,
                     self.timeout if self.timeout and self.timeout > 0
                     else 0.05)
            self.clock.advance(dt)
            self.block_left -= dt
            return b""
        if self.pending:
            out = self.pending[:n]
            self.pending = self.pending[n:]
            self.in_waiting = len(self.pending)
            return out
        self.clock.advance(min(self.timeout if self.timeout and
                               self.timeout > 0 else 0.05, 0.05))
        return b""


def _verify_ok(session, deadline):
    return EXPECTED_FRAMES


def selftest():
    passed = failed = 0

    def case(cid, fn):
        nonlocal passed, failed
        try:
            fn()
            ok, detail = True, "ok"
        except Exception as exc:
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        print("CASE|%s|%s|%s" % (cid, "PASS" if ok else "FAIL", detail))

    def build(out_dir=None, verify_log=None):
        clock = FClock()
        dev = FDevice(clock)
        ser = FakeSerial(dev, clock)
        link = SerialLink(ser, clock=clock)
        s = Session(link, clock=clock, sleep=clock.sleep,
                    verify_log=verify_log, out_dir=out_dir)
        return dev, ser, link, s, clock

    def sent(ser, cmd):
        return sum(1 for _, c in ser.written if c == cmd)

    def c1():
        dev, ser, link, s, clk = build()
        s.run_fault()
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("disarm not exactly once: %s" % ser.written)
        if sent(ser, "fault_reset"):
            raise AssertionError("automatic recovery attempted")

    def c2():
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.read_block_cmd_after_start["safety_state"] = 4.9
        try:
            s.run_fault(deadline_s=8.0)
        except Abort:
            pass
        if s.start_time is None or s.disarm_time is None:
            raise AssertionError("no start/disarm time")
        if s.disarm_time - s.start_time > 5.01:
            raise AssertionError("disarm late by %.2f"
                                 % (s.disarm_time - s.start_time))
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("actual disarm not sent")
        if s.primary_error is None:
            raise AssertionError("primary error lost")

    def c2b():
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.response_lost_after_arm.update({"motor_disarm",
                                              "safety_status"})
        try:
            s.run_fault()
        except Abort:
            pass
        if not s.cleanup_unknown or s.disarm_confirmed:
            raise AssertionError("lost disarm reply not UNKNOWN")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("cleanup command not actually sent")
        if sent(ser, "fault_reset"):
            raise AssertionError("recovery after unknown disarm")

    def c3():
        dev, ser, link, s, clk = build()
        dev.response_lost.add("motor_arm")
        s.run_fault()
        if sent(ser, "motor_start") != 1:
            raise AssertionError("start not sent after confirmed status")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("cleanup missing after lost arm reply")

    def c4():
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.armed = 1
        try:
            s.run_fault()
        except Abort:
            pass
        if sent(ser, "motor_arm") or sent(ser, "motor_start"):
            raise AssertionError("motor command after static refusal")

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
        dev, ser, link, s, clk = build()
        res = s.run_fault(allow_recovery=True)
        if not res["recovered"] or sent(ser, "fault_reset") != 1:
            raise AssertionError("verified recovery not executed")
        dev2, ser2, link2, s2, clk2 = build()
        dev2.missing_slot = 150
        try:
            s2.run_fault(allow_recovery=True)
        except Abort:
            pass
        if sent(ser2, "fault_reset"):
            raise AssertionError("recovery after incomplete readback")
        if s2.primary_error is None or "slot coverage" not in s2.primary_error:
            raise AssertionError("wrong primary error: %s" % s2.primary_error)

    def c8():
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.used = 4096
        try:
            s.run_fault()
        except Abort:
            pass
        if sent(ser, "motor_arm") or sent(ser, "motor_start"):
            raise AssertionError("motor command without free log space")

    def c9():
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="m6_raw_")
        try:
            dev, ser, link, s, clk = build(out_dir=d)
            s.run_fault()
            with open(os.path.join(d, "raw_index.jsonl"),
                      encoding="utf-8") as fh:
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

    def c11():
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="m6_gate_")
        try:
            good = os.path.join(d, "good.json")
            with open(good, "w", encoding="utf-8") as fh:
                json.dump({"archive_accept": True, "clear_done": False,
                           "free": 4096}, fh)
            load_archive_gate(good)
            bads = []
            p = os.path.join(d, "reject.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"archive_accept": False, "clear_done": False,
                           "free": 4096}, fh)
            bads.append(p)
            p = os.path.join(d, "nocopy.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"archive_accept": True, "clear_done": True,
                           "second_copy": None}, fh)
            bads.append(p)
            p = os.path.join(d, "unverified.json")
            empty = os.path.join(d, "second")
            os.makedirs(empty)
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"archive_accept": True, "clear_done": True,
                           "second_copy": empty}, fh)
            bads.append(p)
            for bad in bads:
                try:
                    load_archive_gate(bad)
                except Abort:
                    continue
                raise AssertionError("bad archive gate accepted: %s" % bad)
            dev, ser, link, s, clk = build(verify_log=_verify_ok)
            try:
                s.run_fault(archive_state=bads[0])
            except Abort:
                pass
            if sent(ser, "motor_arm") or sent(ser, "motor_start"):
                raise AssertionError("motor command after archive rejection")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def c12():
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="m6_gate_main_")
        try:
            p = os.path.join(d, "reject.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"archive_accept": False, "clear_done": False,
                           "free": 4096}, fh)
            rc = main(["--port", "FAKE_PORT", "--archive-state", p])
            if rc != 1:
                raise AssertionError("archive rejection rc=%s" % rc)
            if "serial" in sys.modules:
                raise AssertionError("pyserial imported before archive gate")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def c13():
        # D-1: a 10.1 s arm write block must advance the SAME clock used by
        # the deadline and cleanup. Old fixture reported 0.0.
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.write_block["motor_arm"] = 10.1
        try:
            s.run_fault(deadline_s=20.0)
        except Exception:
            pass
        if s.disarm_time is None:
            raise AssertionError("cleanup not attempted")
        if s.disarm_time < 10.0 or clk() < 10.0:
            raise AssertionError("blocked write not charged to session clock: "
                                 "disarm=%.2f clock=%.2f"
                                 % (s.disarm_time, clk()))
        if not s.cleanup_late:
            raise AssertionError("late cleanup not recorded")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("late cleanup command not sent")
        if s.primary_error is None or "WRITE_TIMEOUT" not in s.primary_error:
            raise AssertionError("primary write error lost: %s"
                                 % s.primary_error)

    def c14():
        # D-1: a start write timeout preserves the primary and still leaves
        # time for one actual disarm within start+5.
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.write_block["motor_start"] = 1.0
        try:
            s.run_fault()
        except Exception:
            pass
        if s.start_time is None or s.disarm_time is None:
            raise AssertionError("missing start/disarm time")
        if s.disarm_time - s.start_time > 5.01:
            raise AssertionError("disarm late")
        if not s.disarm_confirmed or s.cleanup_unknown:
            raise AssertionError("cleanup state wrong")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("actual disarm not sent")
        if s.primary_error is None or "WRITE_TIMEOUT" not in s.primary_error:
            raise AssertionError("primary write error lost")

    def c15():
        # D-1: start read blocking consumes the same clock; the command is
        # not considered running and cleanup is attempted within 5 s.
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.started_ok = False
        dev.read_block_cmd_after_start["motor_start"] = 1.5
        try:
            s.run_fault()
        except Abort:
            pass
        if s.start_time is None or s.disarm_time is None:
            raise AssertionError("missing start/disarm time")
        if s.disarm_time - s.start_time > 5.01:
            raise AssertionError("disarm late")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("actual disarm not sent")
        if s.primary_error is None or "start not confirmed" not in s.primary_error:
            raise AssertionError("primary start error lost: %s"
                                 % s.primary_error)

    def c16():
        # D-2 review repro: primary start write failure + cleanup write
        # failure must keep both errors and mark UNKNOWN.
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.write_block["motor_start"] = 0.5
        dev.write_block["motor_disarm"] = 0.5
        try:
            s.run_fault()
        except Exception:
            pass
        if s.primary_error is None or "PRIMARY" in s.primary_error and \
           "WRITE_TIMEOUT:motor_start" not in s.primary_error:
            raise AssertionError("primary lost: %s" % s.primary_error)
        if "WRITE_TIMEOUT:motor_start" not in (s.primary_error or ""):
            raise AssertionError("primary is not the start write error: %s"
                                 % s.primary_error)
        if "WRITE_TIMEOUT:motor_disarm" not in (s.cleanup_error or ""):
            raise AssertionError("cleanup error not recorded: %s"
                                 % s.cleanup_error)
        if not s.cleanup_unknown or s.disarm_confirmed:
            raise AssertionError("cleanup failure not UNKNOWN")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("actual cleanup command not sent")

    def c17():
        # D-2: command reached the device but the reply is lost; the real
        # status query proves the disarm, and the command record proves send.
        dev, ser, link, s, clk = build()
        dev.response_lost.add("motor_disarm")
        s.run_fault()
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("disarm command not sent")
        if "motor_disarm" not in ser.applied:
            raise AssertionError("disarm did not reach the device")
        if not s.disarm_confirmed or s.cleanup_unknown:
            raise AssertionError("disarm not confirmed despite LOW/pad0")

    def c18():
        # D-2: evidence write failure must not replace the primary error.
        import tempfile
        fd, path = tempfile.mkstemp(prefix="m6_evidence_")
        os.close(fd)
        try:
            dev, ser, link, s, clk = build(verify_log=_verify_ok)
            s.out_dir = path  # not a directory: every raw save fails
            dev.write_block["motor_start"] = 0.5
            try:
                s.run_fault()
            except Exception:
                pass
            if s.evidence_error is None:
                raise AssertionError("evidence failure not recorded")
            if s.primary_error is None or "WRITE_TIMEOUT" not in s.primary_error:
                raise AssertionError("primary lost after evidence failure")
        finally:
            os.unlink(path)

    def c19():
        # D-3: explicit refusal text must not start, even if a status query
        # might otherwise be ambiguous.
        for mode in ("refused_text", "not_armed"):
            dev, ser, link, s, clk = build(verify_log=_verify_ok)
            dev.arm_mode = mode
            try:
                s.run_fault()
            except Abort:
                pass
            if sent(ser, "motor_start"):
                raise AssertionError("start after refusal mode %s" % mode)
            if sent(ser, "motor_disarm") != 1:
                raise AssertionError("cleanup not sent after refusal %s" % mode)

    def c20():
        # D-3: armed=0, missing field, duplicated field, conflicting state
        # all refuse target/start.
        overrides = {
            "armed_zero": None,
            "missing": ("[MOT] state=0 name=OTHER\n"
                        "[MOT] cur_hz=0 tgt_hz=0\n"),
            "duplicate": ("[MOT] state=0 name=OTHER\n"
                          "[MOT] cur_hz=0 tgt_hz=0\n"
                          "[MOT] armed=1\n[MOT] armed=0\n"),
            "conflict": ("[MOT] state=4 name=FAULT\n"
                         "[MOT] cur_hz=0 tgt_hz=0\n[MOT] armed=1\n"),
        }
        for mode, text in overrides.items():
            dev, ser, link, s, clk = build(verify_log=_verify_ok)
            dev.arm_mode = "armed_zero" if mode == "armed_zero" else "normal"
            if text is not None:
                # preflight must see a healthy status; override only after arm
                dev.motor_status_override = None
                dev._arm_status_override = text
                orig_on = dev.on_command

                def on_command(cmd, _orig=orig_on, _dev=dev, _text=text):
                    _orig(cmd)
                    if cmd == "motor_arm":
                        _dev.motor_status_override = _text
                dev.on_command = on_command
            try:
                s.run_fault()
            except Abort:
                pass
            if sent(ser, "motor_start"):
                raise AssertionError("start after arm status %s" % mode)
            if sent(ser, "motor_disarm") != 1:
                raise AssertionError("cleanup not sent after arm status %s"
                                     % mode)

    def c21():
        # D-3 healthy control + lost arm reply confirmed by status.
        dev, ser, link, s, clk = build()
        dev.response_lost.add("motor_arm")
        s.run_fault()
        if sent(ser, "motor_start") != 1:
            raise AssertionError("start not sent after status confirmed arm")
        if "motor_arm" not in ser.applied:
            raise AssertionError("arm write did not reach the device")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("disarm not sent")

    def c22():
        # D-3: lost arm reply and unqueryable status -> no start.
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.response_lost.add("motor_arm")
        dev.response_lost_after_arm.add("motor_status")
        try:
            s.run_fault()
        except Abort:
            pass
        if sent(ser, "motor_start"):
            raise AssertionError("start sent without arm confirmation")
        if sent(ser, "motor_disarm") != 1:
            raise AssertionError("cleanup not sent")

    def c23():
        # D-2: a single preflight transport error must not add arm/start.
        dev, ser, link, s, clk = build(verify_log=_verify_ok)
        dev.write_block["safety_state"] = 0.5
        try:
            s.run_fault()
        except Exception:
            pass
        if sent(ser, "motor_arm") or sent(ser, "motor_start") or \
           sent(ser, "motor_dir 0"):
            raise AssertionError("motor command after preflight error")
        if s.primary_error is None or "WRITE_TIMEOUT" not in s.primary_error:
            raise AssertionError("primary lost")

    def c24():
        # D-2: evidence failure alone must fail the run, not claim success.
        import tempfile
        fd, path = tempfile.mkstemp(prefix="m6_evidence_only_")
        os.close(fd)
        try:
            dev, ser, link, s, clk = build()
            s.out_dir = path
            try:
                s.run_fault()
            except Abort as exc:
                if "evidence error" not in str(exc):
                    raise AssertionError("wrong evidence failure: %s" % exc)
            else:
                raise AssertionError("evidence failure returned success")
            if sent(ser, "motor_disarm") != 1 or not s.disarm_confirmed:
                raise AssertionError("evidence failure disturbed cleanup")
            if s.primary_error is None or "evidence error" not in s.primary_error:
                raise AssertionError("evidence error not primary")
        finally:
            os.unlink(path)

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
    case("M6D11-archive_gate_blocks_bad_state", c11)
    case("M6D12-bad_archive_before_serial", c12)
    case("M6D13-shared_clock_charges_arm_block", c13)
    case("M6D14-write_timeout_preserves_primary", c14)
    case("M6D15-start_read_block_within_cleanup", c15)
    case("M6D16-primary_and_cleanup_errors_separate", c16)
    case("M6D17-write_reached_reply_lost", c17)
    case("M6D18-evidence_failure_keeps_primary", c18)
    case("M6D19-arm_refusal_text_no_start", c19)
    case("M6D20-arm_status_missing_duplicate_conflict", c20)
    case("M6D21-arm_reply_lost_status_confirmed", c21)
    case("M6D22-arm_reply_lost_status_unavailable", c22)
    case("M6D23-static_preflight_error_no_motor", c23)
    case("M6D24-evidence_failure_alone_fails", c24)
    print("SUMMARY|cases=%d pass=%d fail=%d" % (passed + failed, passed, failed))
    return 0 if failed == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--out-dir")
    ap.add_argument("--archive-state",
                    help="M5 archive_state.json; required for a real run")
    ap.add_argument("--allow-recovery", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.port:
        ap.error("--port or --selftest required")
    if not args.archive_state:
        ap.error("--archive-state is required for a real run")
    try:
        load_archive_gate(args.archive_state)
    except Abort as exc:
        print("PRECHECK=FAIL archive=%s" % exc)
        return 1
    if not args.out_dir:
        args.out_dir = "m6_field_%s" % time.strftime("%Y%m%d_%H%M%S")
    import serial  # lazy: never imported by selftest or a failed pre-check
    ser = serial.Serial(args.port, 115200, timeout=0.05, write_timeout=0.05)
    s = None
    try:
        link = SerialLink(ser)
        s = Session(link, out_dir=args.out_dir)
        res = s.run_fault(allow_recovery=args.allow_recovery,
                          archive_state=args.archive_state)
        print("FAULT_RESULT=PASS %s" % json.dumps(res, sort_keys=True))
        return 0
    except Exception as exc:
        if s is not None:
            s.write_failure()
        print("FAULT_RESULT=FAIL primary=%s cleanup_error=%s "
              "cleanup_unknown=%s cleanup_late=%s evidence_error=%s"
              % (exc,
                 s.cleanup_error if s is not None else None,
                 s.cleanup_unknown if s is not None else None,
                 s.cleanup_late if s is not None else None,
                 s.evidence_error if s is not None else None))
        tb = (s.primary_traceback if s is not None and
              s.primary_traceback else traceback.format_exc())
        print("PRIMARY_TRACEBACK_BEGIN")
        print(tb)
        print("PRIMARY_TRACEBACK_END")
        return 1
    finally:
        try:
            ser.close()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
