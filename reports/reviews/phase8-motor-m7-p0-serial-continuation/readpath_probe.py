#!/usr/bin/env python3
"""Single-variable probe 3: the accepted collector's channel config and read loop.

Compared with cr_variant_probe.py (which answered byte-clean), exactly two things change:
  (1) Serial() is opened with the collector kwargs - bytesize/parity/stopbits explicit,
      timeout=0.1, write_timeout=2.0, xonxoff/rtscts/dsrdtr=False;
  (2) the read loop is the collector's capture() shape: port.timeout re-set per iteration to
      min(0.1, time left), read(min(512, cap - len(buf))), and each chunk is written, flushed
      and fsync'ed to an exclusive file before anything is decided.

Everything else is kept identical to the clean run: single CR terminator, the same 45 s
zero-send passive window, quiet-period completion, one command, one port open.
No reset, no DAP, no flash, no other command, no config write.
"""
import hashlib
import json
import os
import sys
import time

OUT = sys.argv[1]
PASSIVE_SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
RESPONSE_CAP = 32768
WRITE_SECONDS = 2.0
RESPONSE_SECONDS = 12.0
os.makedirs(OUT, exist_ok=True)

import serial

events = []


def log(kind, **kw):
    row = dict(kind=kind, t=round(time.monotonic(), 3), **kw)
    events.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


log('OPEN_ATTEMPT', port='COM5', kwargs='collector shape: timeout=0.1 write_timeout=2.0 '
                                        'xonxoff/rtscts/dsrdtr=False')
ser = serial.Serial('COM5', 115200, bytesize=8, parity='N', stopbits=1,
                    timeout=.1, write_timeout=WRITE_SECONDS,
                    xonxoff=False, rtscts=False, dsrdtr=False)
log('OPENED')
time.sleep(0.2)

# zero-send passive window, unchanged from the clean run
end = time.monotonic() + PASSIVE_SECONDS
seen = bytearray()
while time.monotonic() < end:
    chunk = ser.read(max(1, ser.in_waiting))
    if chunk:
        seen.extend(chunk)
    else:
        time.sleep(0.02)
if seen:
    with open(os.path.join(OUT, 'passive_before_send.bin'), 'xb') as f:
        f.write(bytes(seen))
    log('PASSIVE_BYTES_SEEN', bytes=len(seen), sha256=sha(bytes(seen)))
    ser.close()
    log('ABORT_BEFORE_SEND', reason='PASSIVE_OUTPUT_PRESENT')
    sys.exit(3)
log('PASSIVE_SILENCE_OK', seconds=PASSIVE_SECONDS)

# one command, single CR, exactly as the clean run sent it
ser.reset_input_buffer()
t_tx = time.monotonic()
ser.write_timeout = WRITE_SECONDS
written = ser.write(b'safety_state\r')
log('TX', command='safety_state', bytes_written=written, terminator='single CR')

# the collector capture() shape: per-iteration timeout, bounded 512 B reads,
# write+flush+fsync of every chunk to an exclusive file BEFORE any decision
buf = bytearray()
deadline = t_tx + RESPONSE_SECONDS
with open(os.path.join(OUT, '01_safety_state.rx.bin'), 'xb') as raw:
    while time.monotonic() < deadline:
        left = RESPONSE_CAP - len(buf)
        if left <= 0:
            log('RX_BYTE_CAP')
            break
        ser.timeout = min(.1, max(0., deadline - time.monotonic()))
        chunk = ser.read(min(512, left))
        if chunk:
            raw.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
            buf.extend(chunk)
            log('CHUNK', size=len(chunk), total=len(buf), sha_so_far=sha(bytes(buf)))
            if time.monotonic() > deadline:
                log('RX_LATE_SUCCESS')
                break
            if b'msh >' in buf[-8:] or b'msh/>' in buf[-8:]:
                log('PROMPT_TAIL_SEEN')          # keep going: the quiet rule decides the end
        else:
            time.sleep(0.01)

# quiet-period completion, same rule as the clean run: 0.5 s without bytes, capped at 6 s
time.sleep(0.6)
extra = bytearray()
drain_end = time.monotonic() + 0.6
while time.monotonic() < drain_end:
    chunk = ser.read(max(1, ser.in_waiting))
    if chunk:
        extra.extend(chunk)
        with open(os.path.join(OUT, '01_safety_state.rx.bin'), 'ab') as f:
            f.write(chunk)
    else:
        time.sleep(0.02)
if extra:
    buf.extend(extra)
    log('TRAILING_AFTER_FSYNC_LOOP', bytes=len(extra), sha256=sha(bytes(extra)))

ser.close()
log('CLOSED')

data = bytes(buf)
path = os.path.join(OUT, '01_safety_state.rx.bin')
on_disk = open(path, 'rb').read()
verdict = {
    'bytes_on_disk': len(on_disk),
    'sha256_on_disk': sha(on_disk),
    'in_memory_equals_disk': on_disk == data,
    'non_ascii_bytes': sum(1 for c in on_disk if c >= 0x80),
    'exact_clean_line_present': b'[SS] state=READY fault_code=0 degraded=0x' in on_disk,
    'repr': repr(on_disk),
}
with open(os.path.join(OUT, 'verdict.json'), 'x', encoding='utf-8', newline='\n') as f:
    json.dump({'verdict': verdict, 'events': events}, f, indent=2, ensure_ascii=False)
    f.write('\n')
log('VERDICT', **{k: v for k, v in verdict.items() if k != 'repr'})
print('DONE', flush=True)
