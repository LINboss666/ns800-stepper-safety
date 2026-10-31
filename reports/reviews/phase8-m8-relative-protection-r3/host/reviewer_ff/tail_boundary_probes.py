#!/usr/bin/env python3
"""Independent offline field-boundary checks. No real serial/tool calls.
Run: python tail_boundary_probes.py --root <repository> --inputs <this directory>
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

F = Path('reports/reviews/phase8-m8-relative-protection-r3/field')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--inputs', type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('reviewed_chain', args.root / F / 'm8r3_serial_chain.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    plan = json.loads((args.root / F / 'plan.json').read_text(encoding='utf-8-sig'))
    tmp = Path(tempfile.mkdtemp(prefix='m8-tail-boundaries-'))
    results = []

    class Log:
        def ev(self, *a, **kw):
            pass

    def case(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, type(e).__name__ + ':' + str(e)
        results.append({'id': name, 'pass': bool(ok), 'detail': detail})
        print(json.dumps(results[-1], ensure_ascii=True))

    boot1 = (args.inputs / 'prior_raw/p0_boot.bin').read_bytes()
    archive = (args.inputs / 'prior_raw/cp3_wire_archive.md').read_text(encoding='utf-8')
    section = archive.split('## `002_boot.rx.bin`', 1)[1]
    boot2 = base64.b64decode(re.search(r'```\s+(.*?)```', section, re.S).group(1))
    assert len(boot1) == 3381 and len(boot2) == 3757
    assert hashlib.sha256(boot2).hexdigest() == '97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749'

    def boot(raw, name):
        out = tmp / name
        out.mkdir()
        clk, led, got = m.SimClock(), Log(), []
        port = m.FakePort([raw], led, clock=clk)
        m.drain_post_reset_boot(port, {'boot_drain_seconds': 20}, 'reset', out, 1,
                               led, got, clk, {}, 'real')
        saved = next(out.glob('*.rx.bin')).read_bytes()
        return saved == raw and got[0].get('readiness') == 'NOT_ASSESSED_QUERY_REQUIRED', {
            'bytes': len(saved), 'sha256': hashlib.sha256(saved).hexdigest(),
            'elapsed': clk.monotonic(), 'writes': len(port.written)}

    case('TB01_REAL_BOOT_3381_NO_SPONTANEOUS_PROMPT', lambda: boot(boot1, 'boot1'))
    case('TB02_REAL_BOOT_3757_NO_SPONTANEOUS_PROMPT', lambda: boot(boot2, 'boot2'))
    case('TB03_EMPTY_BOOT_REQUIRES_QUERY_NOT_READY', lambda: boot(b'', 'empty'))

    def danger():
        try:
            boot(b'warning: sensor stack is close to end of stack address.\r\n', 'danger')
        except m.Stop as e:
            return e.code == 'DANGER_TEXT_IN_STREAM', e.code
        return False, 'danger was accepted'

    case('TB04_BOOT_DANGER_STILL_STOPS', danger)

    def argv_check():
        captures = []
        def fake_run(argv, **kw):
            captures.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        be = m.RealActionBackend(Log(), tmp / 'argv', m.SimClock(), 10,
                                 {'@UID_FILE': 'REVIEW_DUMMY_UID', '@RUN_ROOT': str(tmp),
                                  '@IMAGE_BYTES': '290916'},
                                 repo_root=args.root)
        with patch.object(m.subprocess, 'run', fake_run):
            be._run('readback', {}, plan['tools']['readback']['argv'])
        argv, kw = captures[0]
        commands = [argv[i + 1] for i, x in enumerate(argv[:-1]) if x == '-c']
        good = len(captures) == 1 and any(x.startswith('savemem 0x08000000 ') for x in commands)
        good = good and all(x.split()[0] in ('status', 'savemem', 'exit') for x in commands)
        return good, {'commands': commands, 'cwd_correct': kw['cwd'] == str(args.root.resolve()),
                      'real_tool_calls': 0}

    case('TB05_COMMANDER_C_ARGUMENT_IS_NOT_A_PATH', argv_check)

    stage = {'require_frames': [{'step': 'frames', 'session_key': 'bb_session',
                                'min_frames': 8, 'must_include': [{'event': 9, 'step_hz': 0}]}]}
    selection = {'before': 300, 'after': 600, 'first': 592, 'count': 8}
    serial = [0]

    def csv_verdict(slots, signed=False, corrupt=None, read_error=False):
        serial[0] += 1
        header = m.FLASH_DUMP_HEADER.split(',')
        lines = [m.FLASH_DUMP_HEADER]
        for slot in slots:
            vals = ['0'] * 14
            for key, value in [('slot', slot), ('session', 8), ('event', 9), ('step_hz', 0)]:
                # Product calls the slot column 'idx'. Use its exact header position below.
                at = 0 if key == 'slot' else header.index(key)
                vals[at] = str(value)
            if signed:
                vals[8] = '-23'
            if corrupt:
                vals[corrupt[0]] = corrupt[1]
            lines.append(','.join(vals))
        if read_error:
            lines.append('slot 599 error -12')
        p = tmp / ('frames%d.txt' % serial[0])
        p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return m.flash_frames_verdict(stage, [{'as': 'frames', 'txt': str(p),
                                              'selected_log_window': selection}],
                                      {'bb_session': 8}, Log())

    case('TB06_SIGNED_ACCELERATION_IS_VALID_CSV',
         lambda: (not (r := csv_verdict(range(592, 600), signed=True)), r))
    case('TB07_UNSIGNED_HEALTHY_CONTROL',
         lambda: (not (r := csv_verdict(range(592, 600))), r))
    case('TB08_OLD_SLOTS_SAME_SESSION_REJECTED',
         lambda: (bool(r := csv_verdict(range(8))), r))
    case('TB09_DUPLICATED_SLOT_REJECTED',
         lambda: (bool(r := csv_verdict([592] * 8)), r))
    case('TB10_MISSING_TAIL_SLOT_REJECTED',
         lambda: (bool(r := csv_verdict(range(592, 599))), r))
    case('TB11_UNSIGNED_SLOT_NEGATIVE_REJECTED',
         lambda: (bool(r := csv_verdict(range(592, 600), corrupt=(0, '-1'))), r))
    case('TB12_DEVICE_READ_ERROR_STILL_REJECTED',
         lambda: (bool(r := csv_verdict(range(592, 600), read_error=True)), r))

    def window(before, after, want=None, stop=None):
        p = tmp / ('stats%d_%d.txt' % (before, after))
        p.write_text('slots=%d/4096 valid=%d bad=0\n' % (after, after), encoding='utf-8')
        try:
            cmd, selected = m.select_new_log_window(
                {'before_key': 'before', 'stats_step': 'newlog', 'max_frames': 8},
                [{'as': 'newlog', 'txt': str(p)}], {'before': before})
            return stop is None and cmd == want, {'cmd': cmd, 'selected': selected}
        except m.Stop as e:
            return e.code == stop, e.code

    case('TB13_APPEND_RANGE_300_TO_600', lambda: window(300, 600, 'flash_dump 592 8'))
    case('TB14_EMPTY_STORE_0_TO_300_TAIL', lambda: window(0, 300, 'flash_dump 292 8'))
    case('TB15_NO_APPEND_REJECTED', lambda: window(300, 300, stop='NEW_LOG_NO_APPENDED_RANGE'))
    case('TB16_OVER_CAPACITY_REJECTED', lambda: window(300, 4097, stop='NEW_LOG_NO_APPENDED_RANGE'))

    # A valid command response still needs its own prompt. Boot relaxation is not a protocol bypass.
    def command_prompt():
        clk, port = m.SimClock(), m.FakePort([b'[SS] state=READY fault_code=0\r\n'], Log())
        lines, count = m.read_until_prompt(port, 1.0, Log(), tmp / 'missing_prompt.bin', clk)
        return not lines and count > 0, {'lines': lines, 'bytes': count}

    case('TB17_COMMAND_RESPONSE_PROMPT_STILL_MANDATORY', command_prompt)
    def capacity(used, accepted):
        st = {'min_free_log_slots': 300}
        verdict = m.assert_step(st, ['slots=%d/4096 valid=%d bad=0' % (used, used)],
                                'log_before', Log())
        ok = verdict['verdict'] == 'OK' if accepted else verdict.get('code') == 'EVENT_LOG_CAPACITY_LOW'
        return ok, verdict

    case('TB18_LOG_CAPACITY_REJECT_BEFORE_ARM', lambda: capacity(3900, False))
    case('TB19_LOG_CAPACITY_HEALTHY_CONTROL', lambda: capacity(300, True))
    def ps_profile():
        text = (args.root / F / 'M8R3Field.ps1').read_text(encoding='utf-8-sig')
        match = re.search(r"\[ValidateSet\(([^\n]+)\)\]\s*\[string\]\$Profile", text)
        values = re.findall(r"'([^']+)'", match.group(1)) if match else []
        return 'response_driven' in values, {'accepted_profiles': values,
                                            'powershell_executed': False}
    case('TB20_PS_PARAMETER_ACCEPTS_REQUIRED_REAL_PROFILE', ps_profile)
    def boot_query(state, fault, accept):
        st = next(x for stage in plan['stages'] if stage['id']=='S3'
                  for x in stage['steps'] if x.get('cmd')=='system_status')
        rows = ['=== SYSTEM STATUS ===', '[Boot]   bootstrap-complete=YES abort-stage=0',
                '[Safety] state=%s fault=%d irq_gate=CLOSED polarity=PENDING protect_ready=NO' % (state, fault)]
        verdict = m.assert_step(st, rows, 'sysboot', Log())
        return (verdict['verdict']=='OK') == accept, verdict
    case('TB21_BOOT_QUERY_FAULT_REFUSED', lambda: boot_query('FAULT_LATCHED', 9, False))
    case('TB22_BOOT_QUERY_READY_ZERO_FAULT_CONTROL', lambda: boot_query('READY', 0, True))
    n = sum(x['pass'] for x in results)
    print('BOUNDARY_SUMMARY|pass=%d cases=%d hardware_actions=0 real_tool_calls=0' % (n, len(results)))
    return 0 if n == len(results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
