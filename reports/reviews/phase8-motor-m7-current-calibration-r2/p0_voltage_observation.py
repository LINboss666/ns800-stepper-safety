#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NS800 M7-R1 P0 voltage-observation collector (read-only whitelist).

Offline by default. The only real transport constructor lives behind
--execute-approved-p0; the selftest never opens a port and asserts that.

Disciplines kept from the previously executed field rounds:
  * raw bytes are written to disk BEFORE anything is interpreted;
  * every read and write is bounded, and the first failure stops the run
    (no retry, no auto-recovery, no extra command);
  * the command set is a fixed literal whitelist - anything else is refused
    before it can reach the wire;
  * danger markers (stack/overflow) stop the run after the bytes are saved,
    never by suppressing the alarm or disabling the producer.
"""
import argparse
import hashlib
import json
import os
import re
import sys

BAUD = 115200
# Fixed, verbatim whitelist. No pin_status, no run_fault, no probe, no clear,
# no apply, no save, no selftest, no blank line.
WHITELIST = (
    "safety_state",
    "safety_status",
    "motor_status",
    "step_status",
    "current_cal show",
    "current_cal sample 128",
)
MAX_BANNER_BYTES = 262144
PROMPT_RE = re.compile(rb"msh[^\r\n]*>")   # the msh prompt ends one frame
MAX_RESPONSE_BYTES = 32768
READ_CHUNK = 512
POLL_TICK = 0.05
BANNER_DEADLINE_S = 25.0
RESPONSE_DEADLINE_S = 12.0
DANGER_PATTERNS = (
    rb"stack is close", rb"stack overflow", rb"SYSTEM_HALT", rb"HardFault",
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class Ledger:
    """Append-only run record; written after each step so a stop keeps evidence."""

    def __init__(self, path):
        self.path = path
        self.rows = []

    def add(self, kind, **kw):
        row = {"kind": kind}
        row.update(kw)
        self.rows.append(row)
        with open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.rows, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return row

    def count_tx(self):
        return sum(1 for r in self.rows if r["kind"] == "TX_ATTEMPT")


def save_raw(out_dir, name, payload):
    """Write bytes first; the caller may only look at the file afterwards."""
    path = os.path.join(out_dir, name)
    with open(path, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    return path, len(payload), sha256_file(path)


def drain_until(port, out_dir, name, deadline_s, stop_re=None, ledger=None,
                label=""):
    """Bounded passive read. Returns (path, bytes, matched) or ('', n, False) on error."""
    buf = bytearray()
    import time
    end = time.monotonic() + deadline_s
    err = None
    while time.monotonic() < end and len(buf) < MAX_BANNER_BYTES:
        try:
            n = port.in_waiting if hasattr(port, "in_waiting") else port.inWaiting()
        except Exception as e:                                  # transport died
            err = repr(e)
            break
        if n:
            try:
                chunk = port.read(min(int(n), READ_CHUNK))
            except Exception as e:
                err = repr(e)
                break
            if chunk:
                buf += chunk
                if stop_re is not None and stop_re.search(bytes(buf)):
                    break
            continue
        time.sleep(POLL_TICK)
    if err is not None:
        return None, bytes(buf), False, err
    if not buf:
        return None, b"", False, "NO_BYTES_BEFORE_DEADLINE"
    path, size, digest = save_raw(out_dir, name, bytes(buf))
    matched = bool(stop_re.search(bytes(buf))) if stop_re is not None else True
    if ledger is not None:
        ledger.add("RX", label=label, file=os.path.basename(path), bytes=size,
                   sha256=digest, matched=matched, error=None)
    return path, bytes(buf), matched, None


def danger_hit(payload):
    for pat in DANGER_PATTERNS:
        if re.search(pat, payload, re.IGNORECASE):
            return pat.decode("ascii", "replace")
    return None


def send_command(port, cmd, out_dir, name, ledger, step):
    """One write, one bounded read, bytes first, no retry."""
    if cmd not in WHITELIST:
        ledger.add("REFUSED_NOT_WHITELISTED", command=cmd, step=step)
        raise SystemExit("REFUSED: command not on the P0 read-only whitelist: %r" % cmd)
    ledger.add("TX_ATTEMPT", command=cmd, step=step, tx_bytes=len(cmd) + 2)
    try:
        port.write((cmd + "\r\n").encode("ascii"))
        port.flush()
    except Exception as e:
        ledger.add("TX_FAILED", command=cmd, step=step, error=repr(e))
        raise SystemExit("STOP: TX failed at step %d (%s); no retry" % (step, e))
    path, payload, matched, err = drain_until(
        port, out_dir, name, RESPONSE_DEADLINE_S,
        stop_re=PROMPT_RE, ledger=ledger, label=cmd)
    if err is not None:
        ledger.add("RX_FAILED", command=cmd, step=step, error=err,
                   bytes_before_error=len(payload))
        raise SystemExit("STOP: RX failed at step %d (%s); bytes kept if any"
                         % (step, err))
    if not matched:
        ledger.add("FRAME_END_NOT_SEEN", command=cmd, step=step,
                   bytes=len(payload))
        raise SystemExit("STOP: no prompt frame end for %r; not re-sending" % cmd)
    return payload


def run_execute(args):
    if not (args.execute_approved_p0 and args.approval_source and
            args.site_confirmed and args.image_continuity_confirmed):
        raise SystemExit("REFUSED: execution needs the explicit approval flags "
                         "(--execute-approved-p0 --approval-source --site-confirmed "
                         "--image_continuity_confirmed_equivalent)")
    import serial                                                # only here
    out = args.out
    os.makedirs(out, exist_ok=True)
    led = Ledger(os.path.join(out, "p0_ledger.json"))
    led.add("BIND", payload_bin=args.payload_bin, payload_sha256=expected_sha(args),
            readback_file=args.readback_bin, port=args.port, baud=BAUD,
            whitelist=list(WHITELIST), expected_banner=args.expect_banner)
    port = serial.Serial(args.port, BAUD, timeout=0.2)           # 1 open attempt
    led.add("PORT_OPEN", port=args.port, baud=BAUD)
    try:
        _, banner, got, err = drain_until(port, out, "raw_boot.bin",
                                         BANNER_DEADLINE_S,
                                         stop_re=re.compile(re.escape(
                                             args.expect_banner.encode("ascii"))),
                                         ledger=led, label="boot_banner")
        if not got:
            led.add("STOP", reason="BOOT_BANNER_NOT_OBSERVED", error=err)
            raise SystemExit("STOP: expected boot banner not in the captured bytes; "
                             "identity stays unproven, no commands sent")
        payloads = {}
        for step, cmd in enumerate(WHITELIST, start=1):
            payloads[cmd] = send_command(port, cmd, out, "raw_cmd%02d.bin" % step,
                                         led, step)
        for cmd, payload in payloads.items():
            hit = danger_hit(payload)
            if hit:
                led.add("STACK_DANGER_OBSERVED", pattern=hit, command=cmd)
                raise SystemExit("STOP: danger marker %r present after %s "
                                 "(bytes already saved)" % (hit, cmd))
        led.add("RUN_RESULT", status="COMPLETE", tx=led.count_tx(),
                commands=len(WHITELIST))
    finally:
        try:
            port.close()
            led.add("PORT_CLOSED")
        except Exception as e:
            led.add("PORT_CLOSE_FAILED", error=repr(e))
    return 0


def expected_sha(args):
    if not args.payload_sha256:
        return None
    return args.payload_sha256


# ------------------------------------------------------------------ offline selftest
class FakePort:
    """Selftest transport only. Answers from a canned byte script, counts writes."""

    def __init__(self, script):
        self.script = list(script)
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.script[0]) if self.script else 0

    def read(self, n):
        if not self.script:
            return b""
        head = self.script[0][:n]
        self.script[0] = self.script[0][n:]
        if not self.script[0]:
            self.script.pop(0)
        return head

    def write(self, b):
        self.writes.append(bytes(b))
        return len(b)

    def flush(self):
        pass


BANNER = b"\r\n / | \\     5.3.0 build Oct 29 2026 23:11:52\r\n"
PROMPT = b"msh >"
OK_TAIL = b"\r\n" + PROMPT


def _script():
    out = [BANNER + PROMPT]
    for cmd in WHITELIST:
        body = b"[P0] " + cmd.encode("ascii") + b" ok\r\n" + OK_TAIL
        if cmd == "current_cal sample 128":
            body = (b"[CAL] sample n_req=128 n_seen=128 n_valid=128 dup=0 invalid=0"
                    b" skip=0\r\n[CAL] sample seq=10..137 tick=1000..3560 ver=2..2"
                    b" reset=0\r\n" + OK_TAIL)
        out.append(body)
    return out


def run_selftest():
    checks = []

    def ck(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        return bool(ok)

    # 1. whitelist is exactly the six literal commands, nothing else can pass
    ck("P0T01-whitelist-is-exactly-six", WHITELIST == (
        "safety_state", "safety_status", "motor_status", "step_status",
        "current_cal show", "current_cal sample 128"), "|".join(WHITELIST))
    joined = " ".join(WHITELIST).lower()
    for banned in ("pin_status", "run_fault", "probe", "config_save", "apply",
                   "selftest", "fault_reset", "motor_start", "motor_arm"):
        if banned in joined:
            ck("P0T02-no-privileged-command-in-whitelist", False, banned)
            break
    else:
        ck("P0T02-no-privileged-command-in-whitelist", True, "0 hits")

    # 2. module import and the offline path must not create a device object
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    head = src.split("def run_execute", 1)[0]
    ck("P0T03-no-transport-import-at-module-scope",
       "import serial" not in head and "pyocd" not in head,
       "import serial appears only after run_execute starts")
    ck("P0T04-real-port-only-behind-approval-flag",
       "serial.Serial" in src.split("def run_execute", 1)[1], "constructor in execute path")

    # 3. raw bytes reach disk before any interpretation, and once per step
    out = os.path.join(os.environ.get("TEMP", "/tmp"), "p0_selftest_run")
    if os.path.isdir(out):
        for f in os.listdir(out):
            os.remove(os.path.join(out, f))
    os.makedirs(out, exist_ok=True)
    led = Ledger(os.path.join(out, "ledger.json"))
    fake = FakePort(_script())
    path, payload, matched, err = drain_until(fake, out, "raw_boot.bin", 2.0,
                                             stop_re=re.compile(re.escape(BANNER)),
                                             ledger=led, label="banner")
    ck("P0T05-banner-saved-before-decision",
       matched and err is None and path and os.path.isfile(path)
       and sha256_file(path) == hashlib.sha256(BANNER + PROMPT).hexdigest(),
       os.path.basename(path or ""))

    # 4. a non-whitelisted command is refused before it can be written
    fake2 = FakePort([])
    try:
        send_command(fake2, "config_save", out, "raw_x.bin", led, 99)
        ck("P0T06-non-whitelist-refused-before-wire", False, "it returned")
    except SystemExit as e:
        ck("P0T06-non-whitelist-refused-before-wire",
           "not on the P0 read-only whitelist" in str(e) and fake2.writes == [],
           "%d bytes written" % len(b"".join(fake2.writes)))

    # 5. bounded read: a producer that never stops cannot hang the run
    endless = FakePort([b"x" * 4096] * 200)
    p2, pay2, m2, e2 = drain_until(endless, out, "raw_endless.bin", 3.0,
                                   stop_re=re.compile(rb"never-seen"),
                                   ledger=led, label="endless")
    ck("P0T07-read-is-bounded", len(pay2) <= MAX_BANNER_BYTES and m2 is False
   and os.path.isfile(p2),
       "captured=%d cap=%d" % (len(pay2), MAX_BANNER_BYTES))

    # 6. one failure stops: no prompt frame end => SystemExit, no re-send
    fake3 = FakePort([b"[P0] stuck, no prompt here\r\n"])
    try:
        send_command(fake3, "safety_state", out, "raw_noprompt.bin", led, 1)
        ck("P0T08-first-failure-stops", False, "returned normally")
    except SystemExit as e:
        ck("P0T08-first-failure-stops", "no prompt frame end" in str(e)
           and len(fake3.writes) == 1, "writes=%d" % len(fake3.writes))

    # 7. danger marker is detected only after the bytes are on disk
    body = b"[MOT] sensor stack is close 12%\r\n" + OK_TAIL
    fake4 = FakePort([body])
    payload4 = send_command(fake4, "safety_state", out, "raw_danger.bin", led, 1)
    dpath = os.path.join(out, "raw_danger.bin")
    ck("P0T09-danger-stops-after-saving",
       os.path.isfile(dpath) and danger_hit(payload4) == "stack is close"
       and open(dpath, "rb").read() == body, os.path.basename(dpath))

    # 8. the six-command sequence executes in the fixed order, one write each
    fake5 = FakePort(_script()[1:])
    seq = []
    for step, cmd in enumerate(WHITELIST, start=1):
        send_command(fake5, cmd, out, "raw_seq%02d.bin" % step, led, step)
        seq.append(cmd)
    ck("P0T10-fixed-order-one-write-each",
       fake5.writes == [(c + "\r\n").encode("ascii") for c in WHITELIST]
       and seq == list(WHITELIST), "%d writes" % len(fake5.writes))

    # 11. ledger keeps TX attempts distinct from successes
    rows = json.load(open(os.path.join(out, "ledger.json"), encoding="utf-8"))
    tx = [r for r in rows if r["kind"] == "TX_ATTEMPT"]
    ck("P0T11-ledger-separates-tx-from-result",
       all("command" in r and "step" in r for r in tx) and len(tx) >= 7,
       "TX_ATTEMPT rows=%d" % len(tx))

    fails = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("SUMMARY cases=%d pass=%d fail=%d" % (len(checks), len(checks) - len(fails),
                                                len(fails)))
    print("M7-R2 P0 COLLECTOR SELFTEST:", "PASS" if not fails else "FAIL")
    return 0 if not fails else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--execute-approved-p0", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "p0_run"))
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--expect-banner", default="5.3.0 build Oct 29 2026 23:11:52")
    ap.add_argument("--payload-bin", default="")
    ap.add_argument("--payload-sha256", default="")
    ap.add_argument("--readback-bin", default="")
    ap.add_argument("--approval-source", default="")
    ap.add_argument("--site-confirmed", action="store_true")
    ap.add_argument("--image-continuity-confirmed", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if args.execute_approved_p0:
        return run_execute(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
