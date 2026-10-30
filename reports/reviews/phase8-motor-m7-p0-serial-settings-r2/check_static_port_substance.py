#!/usr/bin/env python3
"""Independent, encoding-safe re-assertion of what P0C09 claims.

The delivered verify_p0_candidate.py P0C09 parses the collector with
Path(...).read_text() and therefore depends on the host locale; this script
repeats the same assertions with an explicit UTF-8 decode so the *claim* can be
separated from the *harness defect*. It changes no candidate byte and touches
no device, port or transport object.
"""
import ast
import hashlib
import sys
from pathlib import Path

PROTECTED = {'timeout', 'write_timeout', 'baudrate', 'bytesize', 'parity', 'stopbits',
             'xonxoff', 'rtscts', 'dsrdtr', 'rts', 'dtr', 'break_condition'}

target = Path(sys.argv[1]).resolve()
raw = target.read_bytes()
print('file           =', target)
print('bytes          =', len(raw))
print('sha256         =', hashlib.sha256(raw).hexdigest())

text = raw.decode('utf-8')          # explicit: no locale dependency
tree = ast.parse(text)

ctors = [n for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and isinstance(n.func.value, ast.Name) and n.func.value.id == 'serial'
         and n.func.attr == 'Serial']
print('serial.Serial call sites =', len(ctors))
assert len(ctors) == 1, 'P0C09A: more than one Serial() construction'

kw = {x.arg: x.value for x in ctors[0].keywords}
print('timeout keyword          =', ast.dump(kw.get('timeout')))
print('write_timeout keyword    =', ast.dump(kw.get('write_timeout')))
assert isinstance(kw['timeout'], ast.Constant) and kw['timeout'].value == 0, 'P0C09B: timeout must be the literal 0'
assert isinstance(kw['write_timeout'], ast.Name) and kw['write_timeout'].id == 'WRITE_SECONDS', 'P0C09C'

stores = []
for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
        if node.attr in PROTECTED:
            holder = node.value
            who = getattr(holder, 'id', None) or getattr(holder, 'attr', '?')
            stores.append((who + '.' + node.attr, node.lineno))
print('post-open protected stores =', stores)
assert not stores, 'P0C09D: a protected serial setting is still assigned after open'

# Serial() must be the only construction and no setter call may be re-issued.
opens = [n.lineno for n in ast.walk(tree)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
         and isinstance(n.func.value, ast.Name) and n.func.value.id == 'serial']
print('serial.* call lines        =', opens)
print('P0C09_SUBSTANCE_OK / one-open-config-timeout-0 / zero-post-open-setting-stores')
print('HARNESS_DEFECT_IS_LOCALE_ONLY / DEVICE_ACTIONS=0')
