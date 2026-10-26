#!/usr/bin/env python3
"""Offline probes; argv[1] is a gate file. No devices, no firmware build."""
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location('gate_under_review', sys.argv[1])
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

def progress(lines):
    for i, line in enumerate(lines):
        if line[:1].isdigit() and len(line.split(',')) == 47:
            fields = line.split(',')
            fields[g.COLUMNS.index('body_end')] = str(int(fields[g.COLUMNS.index('body_begin')]) + 100)
            lines[i] = ','.join(fields)
    return lines

def change(lines, name, value):
    lines = progress(lines)
    for i, line in enumerate(lines):
        if line[:1].isdigit() and len(line.split(',')) == 47:
            fields = line.split(',')
            fields[g.COLUMNS.index(name)] = str(value)
            lines[i] = ','.join(fields)
    return lines

raw = g.fixture(invalid='0x0100', corrupt=progress)
extra = raw.split(b'\r\n')[2] + b'\r\n'
cases = [
    ('CONTROL_HEALTHY', raw, 3, 'ACCEPT'),
    ('CONTROL_LF', raw.replace(b'\r\n', b'\n'), 3, 'ACCEPT'),
    ('EXTRA_ROW_NUL_PREFIX', raw + b'\0' + extra, 3, 'REJECT'),
    ('NUL_OTHER', raw + b'\0unclassified\r\n', 3, 'REJECT'),
    ('EXTRA_CR_TAIL', raw.replace(b'\r\nmsh >', b'\r\r\nmsh >'), 3, 'REJECT'),
]
# Each source field below is uint8_t in spm_rec_t. %u is a print promotion.
u8_fields = ('ctx_isr', 'saved_primask', 'in_owner', 'owner_after', 'phase_after',
             'inhibit', 'pending', 'latch_m', 'latch_d', 'pwm_attempted', 'pwm_result',
             'permit_level', 'branch')
for field in u8_fields:
    for value in (0, 255, 256):
        cases.append((f'U8_{field}_{value}',
                      g.fixture(rows=1, corrupt=lambda lines, f=field, v=value: change(lines, f, v)),
                      1, 'ACCEPT' if value < 256 else 'REJECT'))
bad = 0
for name, data, n, want in cases:
    r = g.evaluate(data, n, 'trip_fast', name)
    allow = 1 if want == 'ACCEPT' else 0
    ok = (r['verdict'] == want
          and r['action_plan']['clear_records_send_count'] == allow
          and r['action_plan']['next_batch_trigger_send_count'] == allow)
    bad += not ok
    print(json.dumps({'id': name, 'expected': want, 'actual': r['verdict'],
                      'ok': ok, 'plan': r['action_plan'], 'failures': r['failures']}, ensure_ascii=False))
print(f'REMAINING-PROBES cases={len(cases)} mismatch={bad}')
sys.exit(1 if bad else 0)
