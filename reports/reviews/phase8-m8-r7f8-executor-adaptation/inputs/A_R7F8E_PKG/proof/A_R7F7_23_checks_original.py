from pathlib import Path
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = Path('/workspace/scratch/f24247cdf3d6/r7f7-task/repo')
PKG = Path('/workspace/scratch/f24247cdf3d6/r7f7-task/package')
REL = 'reports/reviews/phase8-m8-r7f7-newimage-static'
R = ROOT / 'remote' / REL
F = R / 'field/field-20261104T201458Z-r7f7'
COMMIT = '4984ee966e3f87ab9573c8f716e971696d068023'
SHA = '23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963'
checks = []

def check(name, condition, detail):
    checks.append({'name': name, 'pass': bool(condition), 'detail': detail})

def git_bytes(path):
    return subprocess.check_output(['git', 'show', COMMIT + ':' + path], cwd=REPO)

def digest(b):
    return hashlib.sha256(b).hexdigest()

def sums(path):
    return [re.fullmatch(r'([0-9a-f]{64})\s+\*?(.+)', s).groups()
            for s in path.read_text().splitlines() if s and not s.startswith('#')]

round_entries = sums(R / 'ROUND_SHA256SUMS.txt')
round_bad = [p for sha, p in round_entries
             if not (ROOT / 'remote' / p).is_file()
             or digest((ROOT / 'remote' / p).read_bytes()) != sha]
check('REMOTE_ROUND_SUMS', len(round_entries) == 100 and not round_bad,
      {'entries': len(round_entries), 'bad': round_bad})
pkg_files = [p for p in PKG.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
pkg_bad = [p.relative_to(PKG).as_posix() for p in pkg_files
           if not (R / p.relative_to(PKG)).is_file()
           or p.read_bytes() != (R / p.relative_to(PKG)).read_bytes()]
check('A_PACKAGE_ORIGINALS_UNCHANGED', len(pkg_files) == 29 and not pkg_bad,
      {'files': len(pkg_files), 'bad': pkg_bad})
ids = json.loads((R / 'IDENTITY.json').read_text())
accepted = git_bytes(ids['image']['path'])
rb = (F / 'readback.bin').read_bytes()
check('EXACT_ACCEPTED_IMAGE_ON_BOARD', len(rb) == 297452 and digest(rb) == SHA and rb == accepted,
      {'bytes': len(rb), 'sha256': digest(rb)})
axf_path = str(Path(ids['image']['path']).with_name('rt-thread-motor-m1.axf'))
axf = git_bytes(axf_path)
phoff = struct.unpack_from('<I', axf, 28)[0]
phentsize, phnum = struct.unpack_from('<HH', axf, 42)
loads = []
for i in range(phnum):
    typ, off, va, pa, size, mem, flags, align = struct.unpack_from('<IIIIIIII', axf, phoff + i * phentsize)
    if typ == 1 and size:
        loads.append((pa, axf[off:off + size]))
check('ACCEPTED_AXF_LOAD_EQ_BIN', digest(axf) == ids['image']['axf_sha256']
      and len(loads) == 1 and loads[0][0] == 0x08000000 and loads[0][1] == rb,
      {'load_segments': len(loads), 'axf_sha256': digest(axf)})
trees = [subprocess.check_output(['git', 'rev-parse', c + ':rt-thread-master'], cwd=REPO, text=True).strip()
         for c in ['00718b4', COMMIT]]
check('PRODUCT_TREE_UNCHANGED', trees[0] == trees[1], {'tree': trees[1]})
manifest = json.loads((F / 'field_manifest.json').read_text())
actual = sorted(p.relative_to(F).as_posix() for p in F.rglob('*')
                if p.is_file() and p.name != 'field_manifest.json')
check('FIELD_INVENTORY_EXACT', manifest['files'] == actual and len(actual) == 53,
      {'listed_files_excluding_manifest': len(actual)})

plan_bytes = (R / 'field/plan_r7f7_2369_static.json').read_bytes()
plan = json.loads(plan_bytes)
s3 = next(s for s in plan['stages'] if s['id'] == 'S3')
steps = s3['steps']
ledger = [json.loads(s) for s in (F / 'ledger.jsonl').read_text().splitlines()]
dispatch = [json.loads(s) for s in (F / 'dispatch_ledger.jsonl').read_text().splitlines()]
end = next(v for v in dispatch if v['kind'] == 'APPROVAL_LEDGER_END')
bound = next(v for v in ledger if v['kind'] == 'APPROVED_PACKAGE_VERIFIED')
ex_path = R / 'signed_inputs/m8r3_serial_chain.py'
check('PLAN_AND_EXECUTOR_IDENTITY', digest(plan_bytes) == ids['plan']['sha256']
      and digest(ex_path.read_bytes()) == ids['executor']['sha256']
      and bound['plan_sha256'] == digest(plan_bytes), 'Original signed inputs, no executor edits')
spec = importlib.util.spec_from_file_location('r7f7_A_readonly_executor', ex_path)
ex = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ex
spec.loader.exec_module(ex)
check('CANONICAL_STEP_IDENTITY', bound['steps_sha256'] == ex.canonical_steps_sha(ex.stage_steps(s3, 'response_driven')),
      {'steps': len(steps)})

class Log:
    def ev(self, *args, **kw):
        pass

raws = {}
verdicts = []
for i, st in enumerate(steps, 1):
    alias = 'rst.boot' if st.get('action') == 'reset' else st['as']
    raw = (F / ('%02d_%s.rx.bin' % (i, alias))).read_bytes()
    raws[st['as']] = raw
    if st.get('cmd') and st['cmd'] != '__watch__':
        rows = ex.split_rows(raw, command=st['cmd'])
        v = ex.assert_step(st, rows, st['as'], Log(), raw=raw)
        verdicts.append((st['as'], v['verdict']))
check('ORIGINAL_EXECUTOR_REPLAY', len(verdicts) == 13 and all(v == 'OK' for _, v in verdicts), verdicts)
check('RAW_UTF8_NO_NUL', all(b'\0' not in b and b.decode('utf-8').encode('utf-8') == b for b in raws.values()),
      {'files': len(raws)})
commands = [st['cmd'] for st in steps if st.get('cmd') and st['cmd'] != '__watch__']
echoed = [raws[st['as']].splitlines()[0].decode('ascii')
          for st in steps if st.get('cmd') and st['cmd'] != '__watch__']
check('RAW_COMMAND_ORDER', echoed == commands and not any(c.startswith('motor_') and c != 'motor_status' for c in echoed),
      {'commands': echoed})
rx = [v for v in ledger if v['kind'] == 'RX_DONE']
command_raws = [raws[st['as']] for st in steps if st.get('cmd') and st['cmd'] != '__watch__']
check('RX_HASHES_AND_LENGTHS', len(rx) == 13 and all(len(b) == v['bytes'] and digest(b) == v['raw_sha256']
      for b, v in zip(command_raws, rx)), {'responses': len(rx)})
boot = raws['rst'].decode('utf-8')
check('BOOT_READY_SELFTEST_IOIN', all(s in boot for s in ['SELF_TEST -> READY', 'required=PASS degraded=0x00',
      'state=READY fault_code=0', '[REQ OK] TMC2209 IOIN=0x21000041 VERSION=0x21'])
      and not any(s in boot for s in ['FAULT_LATCHED', '[REQ FAIL]']), {'bytes': len(raws['rst'])})
drain = next(v for v in ledger if v['kind'] == 'BOOT_STREAM_DRAINED')
check('PASSIVE_BOOT_WINDOW', drain['drain_declared_s'] == drain['drain_spent_s'] == 75.0
      and drain['end_reason'] == 'DRAIN_BUDGET_REACHED', {'seconds': drain['drain_spent_s']})
check('JOB_ZERO_START_TERMINAL', all(needle in raws[alias] for alias, needle in [
      ('job_initial', b'id=0 st=IDLE why=IDLE owner=0 cred=0/0'),
      ('zero_static', b'zero OK n=32 raw 2055..2065'),
      ('job_zero', b'id=0 st=IDLE why=IDLE owner=0 cred=1/0'),
      ('learn_start', b'learn STARTED id=1 n=8 win=450..550 dir=0'),
      ('job_failed', b'id=1 st=FAILED why=WINDOW_BROKEN owner=0 cred=1/0'),
      ('job_failed', b'job n=0/0 want=8 win=450..550 dir=0')]), 'Actual accepted job; expected static-window failure; owner FREE')
watch = next(v for v in ledger if v['kind'] == 'WATCH_DONE')
check('PASSIVE_NO_COMMAND_INTERVAL', watch['rx_bytes'] == 0 and raws['job_advance_window'] == b''
      and echoed[echoed.index('diag_profile learn 8 450 550 0') + 1] == 'diag_profile learn_status',
      {'learn_response_t': rx[9]['t_s'], 'watch_done_t': watch['t_s'], 'terminal_observed_t': rx[10]['t_s'],
       'note': 'Observation bounds, not measured internal termination latency'})
check('FINAL_STATIC_CONTRACT', all(s in raws['mot_final'] for s in [b'armed=0 drv_en_request=0 health=OK',
      b'cur_hz=0 tgt_hz=0', b'drv_request=LOW pad=0', b'expected_ENN=HIGH ENN_fb=1 resolved verdict=OK']),
      'Software/status evidence only; no physical STEP waveform acquired')
check('SYSTEM_STATE_UNCHANGED', raws['sys_initial'] == raws['sys_final'], {'bytes': len(raws['sys_final'])})
check('EXACT_APPROVAL_SPEND', end['caps'] == end['spent'] == plan['quota'] and end['pending'] == 0
      and not end['commit_failures'], {'positive': {k:v for k,v in end['spent'].items() if v}})
check('SIX_RESERVATIONS_AND_COMMITS', sum(v['kind'] == 'APPROVAL_RESERVED' for v in dispatch) == 6
      and sum(v['kind'] == 'APPROVAL_COMMITTED' for v in dispatch) == 6,
      'No seventh reservation in recorded dispatch ledger')
check('PORT_ONCE_AND_CLOSED', all(sum(v['kind'] == k for v in ledger) == 1
      for k in ['PORT_OPEN_ATTEMPT','PORT_OPEN','PORT_CLOSED']), {'closed_t': ledger[-1]['t_s']})
check('CLAIMS_NOT_ESCALATED', dispatch[-1]['kind'] == 'PROTECTION_CLAIMS'
      and dispatch[-1]['core_protection_verified'] is False, 'No successful learning or physical protection claim')
proof = (R / 'proof/field_judgement_r7f7.txt').read_text()
check('B_RECORDED_JUDGEMENT', len(re.findall(r'^[^\n|]+\|PASS\|', proof, re.M)) == 99
      and not re.search(r'^[^\n|]+\|FAIL\|', proof, re.M),
      'Recorded 99 PASS; this reviewer separately replayed the core raw evidence, not all B negative controls')
result = {'reviewed_commit': COMMIT, 'checks': checks,
          'total': len(checks), 'failures': sum(not v['pass'] for v in checks)}
(ROOT / 'A_R7F7_readonly_review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(bool(result['failures']))
