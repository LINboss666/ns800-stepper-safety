#!/usr/bin/env python3
"""Replay the candidate P0 validators over the repo's own archived M5 wire bytes.

Read-only: no serial/DAP object, no device. Inputs are the byte-exact final_pass
responses recorded by the M5 field round (CRLF, NUL truncation and double prompt
included). This is a re-check of the collector's field grammar against real wire
bytes, not a new board measurement.
"""
import hashlib
import importlib.util
import json
import pathlib
import sys

CAND = pathlib.Path(sys.argv[1]).resolve()
ARCH = pathlib.Path(sys.argv[2]).resolve()
CLOUD = pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None
EXPECT = 'a2c75ea83cd3842246397a6eff33eea52745c69f8926fb6800a2561fe36cb95a'

got = hashlib.sha256(CAND.read_bytes()).hexdigest()
if got != EXPECT:
    print(json.dumps([{'case': 'CANDIDATE_IDENTITY', 'ok': False, 'sha256': got, 'expect': EXPECT}]))
    sys.exit(2)

spec = importlib.util.spec_from_file_location('cand_p0', CAND)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

CASES = (('016_safety_state.bin', 'safety_state', m.state),
         ('017_safety_status.bin', 'safety_status', m.safety),
         ('018_motor_status.bin', 'motor_status', m.motor),
         ('019_step_status.bin', 'step_status', m.step))

rows = []
for name, cmd, validator in CASES:
    raw = (ARCH / name).read_bytes()
    row = dict(case='VERBATIM_' + name, command=cmd, bytes=len(raw),
               cr_bytes=raw.count(b'\r'), nul_bytes=raw.count(b'\x00'),
               sha256=hashlib.sha256(raw).hexdigest())
    row['body_tag_present'] = m.BODY_TAG[cmd] in raw
    row['prompt_at_frame_end'] = bool(m.PROMPT.search(m.ANSI.sub(b'', raw)))
    try:
        validator(raw)
        row['verdict'] = 'ACCEPT'
    except Exception as e:
        row['verdict'] = 'STOP:' + type(e).__name__ + ':' + str(e)
    rows.append(row)
    if CLOUD is not None:
        alt = (CLOUD / name).read_bytes()
        row2 = dict(case='CLOUD_REPLAY_INPUT_' + name, bytes=len(alt),
                    cr_bytes=alt.count(b'\r'), identical_to_repo_archive=(alt == raw))
        try:
            validator(alt)
            row2['verdict'] = 'ACCEPT'
        except Exception as e:
            row2['verdict'] = 'STOP:' + type(e).__name__ + ':' + str(e)
        rows.append(row2)

acc = [r for r in rows if r['case'].startswith('VERBATIM_') and r['verdict'] == 'ACCEPT']
print(json.dumps(rows, indent=2, ensure_ascii=False))
print('VERBATIM_M5_REPLAY=%d/%d accept' % (len(acc), len(CASES)))
print('candidate sha256 verified / DEVICE_ACTIONS=0 / inputs are historical archives, not new measurements')
sys.exit(0 if len(acc) == len(CASES) else 1)
