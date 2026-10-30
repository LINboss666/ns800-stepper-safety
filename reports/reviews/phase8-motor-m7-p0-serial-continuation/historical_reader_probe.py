#!/usr/bin/env python3
"""One-shot discriminator using the M5-era read primitive (verbatim semantics).

Device actions: exactly one COM5 open and one command send ("safety_state"), plus a
zero-send passive listen on the same handle. No reset, no DAP, no flash, no other command.

Why this shape: the archived byte-clean safety_state reply on this board was captured by
m5_field_serial.py using pyserial with timeout=0.05, read(max(1, in_waiting)), a 0.5 s
quiet-period completion, CRLF terminator and reset_input_buffer() before the write. The
accepted P0 collector differs from that in all four read/write dimensions, so this harness
reproduces the historical reader unchanged to separate "reader behaviour" from "board side".
"""
import hashlib
import json
import os
import sys
import time

OUT = sys.argv[1]
PASSIVE_SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
os.makedirs(OUT, exist_ok=True)

import serial  # the same library the historical clean capture used

events = []


def log(kind, **kw):
    row = dict(kind=kind, t=time.monotonic(), **kw)
    events.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def save(name, data):
    path = os.path.join(OUT, name)
    with open(path, 'xb') as f:      # exclusive create: never overwrite evidence
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return {'file': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


log('OPEN_ATTEMPT', port='COM5', baud=115200, timeout=0.05)
ser = serial.Serial('COM5', 115200, timeout=0.05)   # exactly one open, no DTR/RTS args: same
log('OPENED')                                       # defaults as the historical primitive used them
time.sleep(0.2)

# 1) zero-send passive listen: does the console damage its own unsolicited output?
end = time.monotonic() + PASSIVE_SECONDS
buf = bytearray()
while time.monotonic() < end:
    chunk = ser.read(max(1, ser.in_waiting))
    if chunk:
        buf.extend(chunk)
    else:
        time.sleep(0.02)
if buf:
    # startup or periodic output appeared; record it and stop rather than send into it
    ev = save('passive_before_send.bin', bytes(buf))
    ev.update(log('PASSIVE_BYTES_SEEN', **ev))
    ser.close()
    log('ABORT_BEFORE_SEND', reason='PASSIVE_OUTPUT_PRESENT')
    sys.exit(3)
log('PASSIVE_SILENCE_OK', seconds=PASSIVE_SECONDS)

# 2) one command, historical reader semantics
ser.reset_input_buffer()
t_tx = time.monotonic()
written = ser.write(b'safety_state\r\n')            # CRLF and quiet-period end, as M5 did
log('TX', command='safety_state', bytes_written=written, terminator='CRLF')
resp = bytearray()
last = t_tx
deadline = t_tx + 6.0
while time.monotonic() < deadline:
    chunk = ser.read(max(1, ser.in_waiting))
    if chunk:
        resp.extend(chunk)
        last = time.monotonic()
    elif time.monotonic() - last >= 0.5 and resp:
        break
    else:
        time.sleep(0.02)
ev = save('01_safety_state.rx.bin', bytes(resp))
ev.update({'response_bytes': len(resp), 'quiet_period_end': True,
           'elapsed': round(time.monotonic() - t_tx, 3)})
log('RX', **ev)

ser.close()
log('CLOSED')

text = bytes(resp)
verdict = {
    'exact_clean_line_present': b'[SS] state=READY fault_code=0 degraded=0x' in text,
    'non_ascii_bytes': sum(1 for c in text if c >= 0x80),
    'repr': repr(text),
}
with open(os.path.join(OUT, 'verdict.json'), 'x', encoding='utf-8', newline='\n') as f:
    json.dump({'verdict': verdict, 'events': events}, f, indent=2, ensure_ascii=False)
    f.write('\n')
log('VERDICT', **{k: v for k, v in verdict.items() if k != 'repr'})
print('DONE', flush=True)
