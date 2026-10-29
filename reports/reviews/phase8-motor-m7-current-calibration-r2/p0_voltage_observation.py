#!/usr/bin/env python3
"""M7 P0: bounded read-only voltage observation; offline by default.

No flashing is performed by this script. The operator first completes the one
approved download/readback. This script binds those exact bytes before opening
COM5, then performs one explicit reset with the already known DAP UID.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BIN_SHA = 'e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da'
AXF_SHA = '2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584'
CALC_SHA = '5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad'
BIN_SIZE, AXF_SIZE = 266244, 1688696
BANNER = b'5.3.0 build Oct 29 2026 23:11:52'
BOOT_DONE = b'[MAIN] boot complete: READY (motion still gate-locked)'
WHITELIST = ('safety_state', 'safety_status', 'motor_status', 'step_status',
             'current_cal show', 'current_cal sample 128')
SEQUENCE = WHITELIST + ('current_cal show',)
BODY_TAG = dict(zip(WHITELIST, (b'[SS] state=', b'[SAFETY] ready=', b'[MOT] state=',
                             b'[STEP] dev=', b'[CAL] adc health=', b'[CAL] sample n_req=')))
PROMPT = re.compile(rb'(?:^|[\r\n])msh\s*(?:>|/>)\s*\Z')
ANSI = re.compile(rb'\x1b\[[0-?]*[ -/]*[@-~]')
DANGER = re.compile(rb'stack is close|stack overflow|SYSTEM_HALT|HardFault|BusFault|UsageFault', re.I)
API_ACCEPTED = 'api-accepted(驱动接受关断, 非波形证明)'.encode('utf-8')
RESPONSE_CAP, BOOT_CAP = 32768, 262144
WRITE_SECONDS, RESPONSE_SECONDS, BOOT_SECONDS, HUMAN_SECONDS = 2., 12., 30., 900.


class Stop(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    # No success is reported when evidence cannot be saved.
    with open(path, 'x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n'); f.flush(); os.fsync(f.fileno())


class Session:
    def __init__(self, port, out, clock=time):
        self.port, self.out, self.clock = port, Path(out), clock
        self.serial_number = 0
        self.tx = []
        self.ledger = open(self.out / 'ledger.jsonl', 'x', encoding='utf-8', newline='\n')

    def event(self, kind, **kw):
        row = dict(kind=kind, monotonic=self.clock.monotonic(), **kw)
        self.ledger.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.ledger.flush(); os.fsync(self.ledger.fileno())

    def check_danger(self, data):
        if DANGER.search(ANSI.sub(b'', data)):
            raise Stop('STACK_DANGER_OBSERVED (raw bytes already saved)')

    def capture(self, name, seconds, cap, complete):
        """Persist each chunk before checking danger/completion, including errors."""
        deadline = self.clock.monotonic() + seconds
        buf = bytearray()
        with open(self.out / name, 'xb') as raw:
            while self.clock.monotonic() < deadline:
                left = cap - len(buf)
                if left <= 0:
                    raise Stop('RX_BYTE_CAP:' + name)
                self.port.timeout = min(.1, max(0., deadline - self.clock.monotonic()))
                chunk = self.port.read(min(512, left))
                if chunk:
                    raw.write(chunk); raw.flush(); os.fsync(raw.fileno())
                    buf.extend(chunk)
                    self.check_danger(bytes(buf))
                    if self.clock.monotonic() > deadline:
                        raise Stop('RX_LATE_SUCCESS:' + name)
                    if complete(bytes(buf)):
                        self.event('RX_COMPLETE', file=name, bytes=len(buf), sha256=sha(buf))
                        return bytes(buf)
                else:
                    self.clock.sleep(min(.01, max(0., deadline - self.clock.monotonic())))
        raise Stop('REPLY_TIMEOUT:' + name)

    def query(self, command, validator):
        if command not in WHITELIST:
            raise Stop('COMMAND_NOT_WHITELISTED')
        if len(self.tx) >= len(SEQUENCE) or command != SEQUENCE[len(self.tx)]:
            raise Stop('COMMAND_ORDER_OR_QUOTA')
        # Preserve already buffered asynchronous output separately. No quiet window
        # is required; only this finite snapshot is drained before the new request.
        self.pending('before_%02d.bin' % (len(self.tx) + 1))
        self.serial_number += 1
        name = '%02d_%s.rx.bin' % (self.serial_number, command.replace(' ', '_'))
        wire = (command + '\r').encode('ascii')   # One terminator, no second blank command.
        self.event('TX_ATTEMPT', command=command, file=name)
        self.tx.append(command)                  # Failed attempts consume the quota.
        self.port.write_timeout = WRITE_SECONDS
        before = self.clock.monotonic()
        count = self.port.write(wire)           # Exactly one bounded write; never flush()/retry.
        if count != len(wire) or self.clock.monotonic() - before > WRITE_SECONDS:
            raise Stop('SHORT_OR_LATE_WRITE:' + command)
        payload = self.capture(name, RESPONSE_SECONDS, RESPONSE_CAP,
                               lambda b: BODY_TAG[command] in b and
                               bool(PROMPT.search(ANSI.sub(b'', b))))
        value = validator(payload)
        self.event('COMMAND_ACCEPTED', command=command)
        return value

    def pending(self, name):
        count = self.port.in_waiting
        if not count: return
        if count > BOOT_CAP: raise Stop('PENDING_BYTE_CAP')
        self.capture(name, 2., BOOT_CAP, lambda b: len(b) >= count)

    def wait_dmm(self, phase, seconds=HUMAN_SECONDS):
        target = self.out / ('dmm_' + phase + '.json')
        if target.exists(): raise Stop('DMM_PREEXISTING:' + phase)
        self.event('WAIT_DMM', phase=phase, required_file=target.name)
        print('WAIT_DMM_' + phase.upper() + ': H10-1 and H10-3 DC voltage; keep H10-2 GND', flush=True)
        deadline = self.clock.monotonic() + seconds
        buf = bytearray()
        with open(self.out / ('passive_dmm_' + phase + '.bin'), 'xb') as raw:
            while self.clock.monotonic() < deadline:
                if target.exists():
                    data = target.read_bytes()
                    try: v = json.loads(data)
                    except (ValueError, UnicodeError) as e: raise Stop('DMM_JSON_INVALID') from e
                    if not isinstance(v, dict) or v.get('meter') != 'UT39A+' or v.get('range_V') != 4 or v.get('mode') != 'DCV':
                        raise Stop('DMM_METHOD_MISMATCH')
                    for key in ('Vadc', 'V3v3'):
                        r = v.get(key)
                        if not isinstance(r, dict) or not isinstance(r.get('observed_at'), str) or not r['observed_at']:
                            raise Stop('DMM_READING_MISSING')
                        n = r.get('V')
                        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not 0 <= n < 4:
                            raise Stop('DMM_VALUE_OUT_OF_RANGE')
                    self.event('DMM_RECEIVED', phase=phase, file=target.name, sha256=sha(data))
                    return v
                self.port.timeout = min(.1, max(0., deadline - self.clock.monotonic()))
                chunk = self.port.read(min(512, BOOT_CAP - len(buf)))
                if chunk:
                    raw.write(chunk); raw.flush(); os.fsync(raw.fileno()); buf.extend(chunk)
                    self.check_danger(bytes(buf))
                    if len(buf) >= BOOT_CAP: raise Stop('DMM_PASSIVE_BYTE_CAP')
                else:
                    self.clock.sleep(min(.05, max(0., deadline - self.clock.monotonic())))
        raise Stop('DMM_WAIT_EXPIRED:' + phase)


def lines(data, command):
    # Normalization is only for interpretation. Saved wire bytes stay untouched.
    data = ANSI.sub(b'', data)
    if not PROMPT.search(data): raise Stop('MISSING_RESPONSE_PROMPT')
    data = PROMPT.sub(b'', data)
    echo = re.compile(rb'(?:msh\s*(?:>|/>)\s*)?' + re.escape(command.encode()) + rb'\r(?:\n)?')
    data = echo.sub(b'', data, count=1)
    rows = data.replace(b'\r\n', b'\n').split(b'\n')
    if command == 'safety_status':
        expanded = []
        for row in rows:
            # kservice's known 126-byte truncation drops this explanatory line's
            # newline, immediately followed by an intact authoritative record.
            # Only this exact noncanonical prefix/length permits a boundary. Never
            # repair a clipped gate field, sample, or any other NUL-bearing row.
            a, sep, b = row.partition(b'\x00')
            if sep and len(a) == 126 and a.startswith(b'[SAFETY] ENN_FEEDBACK(') and b.startswith(b'[SAFETY] request='):
                expanded.extend((a + sep, b))
            else: expanded.append(row)
        rows = expanded
    return rows


def one(rows, pattern):
    hits = [re.fullmatch(pattern, row) for row in rows]
    hits = [v for v in hits if v]
    if len(hits) != 1: raise Stop('MISSING_DUPLICATE_OR_MALFORMED_FIELD:' + pattern.decode('utf-8', 'backslashreplace'))
    return hits[0]


def state(data):
    r = lines(data, 'safety_state')
    one(r, rb'\[SS\] state=READY fault_code=0 degraded=0x[0-9A-Fa-f]+')
    if sum(x.startswith(b'[SS] state=') for x in r) != 1: raise Stop('SAFETY_STATE_CONFLICT')


def safety(data):
    r = lines(data, 'safety_status')
    one(r, rb'\[SAFETY\] ready=YES first_err=0')
    one(r, rb'\[SAFETY\] MCU_DRV_ENABLE = 0 \([^\r\n\x00]+\)')
    one(r, rb'\[SAFETY\] DRV_ENABLE last write\+readback verified = 0 \(-1 = never verified\)')
    one(r, rb'\[SAFETY\] request=DISABLE\(LOW\) pad=0 expected_FB=HIGH FB=1 -> verdict=OK')
    # The old long ENN explanation can be truncated by the 128 B console.
    # It is never used as a gate: the short complete request/FB/verdict line above is mandatory.
    prefixes = (b'[SAFETY] ready=', b'[SAFETY] MCU_DRV_ENABLE =',
                b'[SAFETY] DRV_ENABLE last', b'[SAFETY] request=')
    if any(sum(x.startswith(p) for x in r) != 1 for p in prefixes): raise Stop('SAFETY_FIELD_CONFLICT')


def motor(data):
    r = lines(data, 'motor_status')
    patterns = (
        rb'\[MOT\] state=0 name=OTHER',
        rb'\[MOT\] cur_hz=0 tgt_hz=0 accel=[0-9]+ decel=[0-9]+ dir=[01]',
        rb'\[MOT\] armed=0 drv_en_request=-?[0-9]+ health=[A-Z_]+',
        rb'\[MOT\] stop result=' + re.escape(API_ACCEPTED) + rb' src=[a-z_-]+ rc=0 latched=0',
        rb'\[MOT\] diag_off_confirmed=YES',
        rb'\[MOT\] drv_request=LOW pad=0',
        rb'\[MOT\] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK',
    )
    for p in patterns: one(r, p)
    for prefix in (b'[MOT] state=', b'[MOT] cur_hz=', b'[MOT] armed=', b'[MOT] stop result=',
                   b'[MOT] diag_off_confirmed=', b'[MOT] drv_request=', b'[MOT] expected_ENN='):
        if sum(x.startswith(prefix) for x in r) != 1: raise Stop('MOTOR_FIELD_CONFLICT')


def step(data):
    r = lines(data, 'step_status')
    one(r, rb'\[STEP\] dev=epwm1 ch=0 enabled=0')
    one(r, rb'\[STEP\] last_stop=(?:not-attempted|' + re.escape(API_ACCEPTED) + rb') stop_rc=0 latched=0')
    if sum(x.startswith(b'[STEP] dev=') for x in r) != 1 or sum(x.startswith(b'[STEP] last_stop=') for x in r) != 1:
        raise Stop('STEP_FIELD_CONFLICT')


def show(data):
    r = lines(data, 'current_cal show')
    if b'\x00' in data: raise Stop('CAL_SHOW_NUL')
    a = one(r, rb'\[CAL\] adc health=[A-Z_]+ src=(NONE|THEORETICAL|MEASURED) ver=([0-9]+)')
    b = one(r, rb'\[CAL\] adc offset_uV=(-?[0-9]+) gain_uV_per_A=([0-9]+) measured=(YES|NO)')
    one(r, rb'\[CAL\] cfg valid=(YES|NO) src=[0-9]+ offset_uV=-?[0-9]+ gain_uV_per_A=[0-9]+')
    one(r, rb'\[CAL\] sample seq=[0-9]+ tick=[0-9]+ raw=[0-9]+ valid=1 fresh=1')
    one(r, rb'\[CAL\] sample ma_uA=-?[0-9]+ src=(NONE|THEORETICAL|MEASURED)')
    one(r, rb'\[CAL\] note: show/sample do not change cal/motion/flash')
    if len([x for x in r if x.startswith(b'[CAL]')]) != 6: raise Stop('CAL_SHOW_RECORD_COUNT')
    if int(a[2]) > 0xffffffff: raise Stop('CAL_VERSION_RANGE')
    return (a[1].decode(), int(a[2]), int(b[1]), int(b[2]), b[3].decode())


def sample_validator(calc, out):
    def parse(data):
        # This is the exact reviewed complete six-line parser, not another format.
        try: v = calc._parse_capture(str(Path(out) / '06_current_cal_sample_128.rx.bin'))
        except calc.Reject as e: raise Stop('SAMPLE_NOT_ACCEPTED:' + str(e)) from e
        if v['n_requested'] != 128 or v['n_valid'] != 128: raise Stop('SAMPLE_COUNT')
        n, sm, sq = 128, v['raw_sum'], v['raw_sum_sq']
        lo, hi = v['raw_min'], v['raw_max']
        if not 0 < lo <= hi < 4095 or not n * lo <= sm <= n * hi or not sm * sm <= n * sq:
            raise Stop('SAMPLE_MOMENT_CONTRADICTION')
        return v
    return parse


def observe(s, calc):
    # Every validator completes before the next command; no static gate is inferred from a prompt.
    for cmd, validator in zip(WHITELIST[:4], (state, safety, motor, step)):
        s.query(cmd, validator)
    before = s.wait_dmm('before')
    c0 = s.query('current_cal show', show)
    sampled = s.query('current_cal sample 128', sample_validator(calc, s.out))
    c1 = s.query('current_cal show', show)
    if c0 != c1 or sampled['cal_source'] != c0[0] or sampled['cal_version'] != c0[1]:
        raise Stop('CAL_CHANGED_ACROSS_P0')
    after = s.wait_dmm('after')
    s.pending('after_dmm.bin')
    mean_mv = sampled['raw_sum'] * 3300 / (128 * 4095)
    readings = []
    for phase, d in (('before', before), ('after', after)):
        for key in ('Vadc', 'V3v3'):
            measured = d[key]['V'] * 1000
            error = .005 * abs(measured) + 5
            readings.append(dict(phase=phase, node=key, measured_mV=measured,
                                 observed_at=d[key]['observed_at'], dmm_limit_mV=error,
                                 dmm_interval_mV=[measured-error, measured+error],
                                 nominal_adc_minus_dmm_mV=mean_mv-measured if key == 'Vadc' else None))
    result = dict(status='VOLTAGE_OBSERVATION_ONLY', commands=s.tx, sample=sampled,
                  nominal_mean_mV=mean_mv, nominal_min_mV=sampled['raw_min'] * 3300 / 4095,
                  nominal_max_mV=sampled['raw_max'] * 3300 / 4095,
                  readings=readings, calibrated=False, physical_accuracy_accepted=False)
    save_json(s.out / 'result.json', result)
    s.event('COMPLETE', status=result['status'], tx=len(s.tx))
    return result


def load_calc(path):
    p = Path(path)
    if sha(p.read_bytes()) != CALC_SHA: raise Stop('CALCULATOR_IDENTITY_MISMATCH')
    spec = importlib.util.spec_from_file_location('p0_calcalc', p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def bind(args):
    if not (args.approval_source and args.site_confirmed and args.execute_approved_p0):
        raise Stop('APPROVAL_AND_SITE_REQUIRED')
    for path, size, digest in ((args.payload_bin, BIN_SIZE, BIN_SHA),
                               (args.payload_axf, AXF_SIZE, AXF_SHA),
                               (args.readback_bin, BIN_SIZE, BIN_SHA)):
        b = Path(path).read_bytes()
        if len(b) != size or sha(b) != digest: raise Stop('PAYLOAD_OR_READBACK_MISMATCH')
    if Path(args.payload_bin).read_bytes() != Path(args.readback_bin).read_bytes():
        raise Stop('READBACK_BYTES_DIFFER')
    calc = load_calc(args.calculator)
    uid = Path(args.uid_file).read_text(encoding='ascii').strip()
    if len(uid) != 48 or sha(uid.encode())[:12] != 'd2f530dbd062': raise Stop('KNOWN_DAP_UID_MISMATCH')
    if Path(args.out).exists(): raise Stop('OUTPUT_DIRECTORY_MUST_BE_NEW')
    return calc, uid


def reset_once(s, uid):
    argv = [sys.executable, '-m', 'pyocd', 'reset', '--method', 'sysresetreq',
            '--no-config', '--no-wait', '--connect', 'attach', '--core', '0', '-t', 'cortex_m',
            '-u', uid, '-f', '1000000', '-Oauto_unlock=false',
            '-Ocache.enable_memory=false', '-Ocache.read_code_from_elf=false', '-Oscan_all_aps=false']
    # UID stays in the private process arguments, never in a published ledger.
    s.event('RESET_ATTEMPT', known_uid_digest_prefix='d2f530dbd062')
    with open(s.out / 'reset.stdout.bin', 'xb') as so, open(s.out / 'reset.stderr.bin', 'xb') as se:
        p = subprocess.Popen(argv, stdout=so, stderr=se)
        try:
            boot = s.capture('raw_boot.bin', BOOT_SECONDS, BOOT_CAP,
                             lambda b: BANNER in b and BOOT_DONE + b'\n' in b.replace(b'\r\n', b'\n'))
            # No quiet/prompt requirement at startup. Identity plus the complete main-ready line suffice.
            p.wait(timeout=2)
            if p.returncode != 0: raise Stop('RESET_TOOL_FAILED')
            s.event('BOOT_IDENTITY_AND_MAIN_READY', bytes=len(boot), sha256=sha(boot))
        finally:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=2)
                except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=2)


def execute(args):
    calc, uid = bind(args)                  # Before importing/constructing any serial object.
    Path(args.out).mkdir()
    import serial                         # Offline/selftest paths never import pyserial.
    port = None
    s = Session(None, args.out)
    primary = None
    cleanup = []
    try:
        s.event('BIND', payload_sha256=BIN_SHA, readback_sha256=BIN_SHA,
                axf_sha256=AXF_SHA, calculator_sha256=CALC_SHA)
        s.event('PORT_OPEN_ATTEMPT', port='COM5')
        # timeout/write_timeout are bounded; no flush(), probing, scanning, retries or extra commands.
        port = serial.Serial('COM5', 115200, bytesize=8, parity='N', stopbits=1,
                             timeout=.1, write_timeout=WRITE_SECONDS,
                             xonxoff=False, rtscts=False, dsrdtr=False)
        s.port = port
        s.event('PORT_OPEN', port='COM5')
        reset_once(s, uid)
        observe(s, calc)
    except BaseException as e:
        primary = e
    finally:
        if port is not None:
            try:
                port.close()
                s.event('PORT_CLOSED')
            except Exception as e: cleanup.append('PORT_CLOSE_OR_EVIDENCE:' + repr(e))
        try: s.ledger.close()
        except Exception as e: cleanup.append('LEDGER_CLOSE:' + repr(e))
    if cleanup: print('CLEANUP_ERRORS:' + repr(cleanup), file=sys.stderr)
    if primary is not None: raise primary
    if cleanup: raise Stop('CLEANUP_FAILED')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--execute-approved-p0', action='store_true')
    ap.add_argument('--approval-source')
    ap.add_argument('--site-confirmed', action='store_true')
    for key in ('out', 'payload-bin', 'payload-axf', 'readback-bin', 'uid-file'):
        ap.add_argument('--' + key)
    ap.add_argument('--calculator', default=str(Path(__file__).resolve().parents[1] /
                    'phase8-motor-m7-current-calibration' / 'm7_calcalc.py'))
    args = ap.parse_args(argv)
    if args.selftest:
        import verify_p0_candidate
        return verify_p0_candidate.run(sys.modules[__name__], args.calculator)
    if not args.execute_approved_p0:
        ap.print_help(); return 2
    try:
        return execute(args)
    except Exception as e:
        print('P0_FAILED:' + type(e).__name__ + ':' + str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
