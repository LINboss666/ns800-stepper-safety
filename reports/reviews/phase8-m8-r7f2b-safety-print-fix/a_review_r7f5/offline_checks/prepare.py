from pathlib import Path
import contextlib, copy, hashlib, importlib.util, io, json, re, shutil, subprocess, time

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'repo'
OUT = ROOT / 'checks'
PKG = ROOT / 'package'
FIELD_REL = 'reports/reviews/phase8-m8-r7f2b-safety-print-fix/field'
EXE_REL = 'reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py'
PLAN_REL = FIELD_REL + '/plan_r7f5_b502_blind_irq_static.json'
ROWS_REL = FIELD_REL + '/rows_r7f5_b502_blind_irq_static.json'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO)

def get_tracked(path):
    data = git('show', 'HEAD:' + path)
    p = REPO / path
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return data

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def digest(b):
    return hashlib.sha256(b).hexdigest()

base = json.loads(get_tracked(FIELD_REL + '/plan_r7f4_b502_configured_reviewed.json'))
oldrows = json.loads(get_tracked(FIELD_REL + '/rows_r7f4_b502_configured.json'))
exe = REPO / EXE_REL
get_tracked(EXE_REL)
spec = importlib.util.spec_from_file_location('original_m8_executor', exe)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert digest(exe.read_bytes()) == '33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca'
for path in git('ls-tree', '-r', '--name-only', 'HEAD', 'reports/firmware/phase8-m8-r7f2b-safety-print-fix', 'reports/firmware/phase8-m8-r7f1a-minimal-c-patch').decode().splitlines():
    get_tracked(path)
get_tracked(base['identity_binding']['already_proven_board_image']['proven_on_board'])
get_tracked(base['needles_file'])
app = REPO / m.PLAN_APP_REL
src = (app / 'safety_thread.c').read_text(encoding='utf-8')
assert 'static rt_bool_t st_polarity_validated = RT_FALSE;' in src
assert 'st_polarity_validated = RT_TRUE;' in src
assert 'rt_pin_read(pin) != st_inputs[i].safe_level' in src
assert 'PIN_IRQ_MODE_RISING,  PIN_LOW,  "TMC_DIAG"' in src
assert not git('diff', '--name-only', '0d8239f', 'HEAD', '--', 'rt-thread-master').strip()

capture4 = FIELD_REL + '/field-20261104T015928Z/S_S1/'
capture3 = FIELD_REL + '/field-20261104T001444Z/S_S3/'
def actual(path):
    raw = get_tracked(path)
    raw.decode('utf-8')
    assert b'\0' not in raw
    return m.split_rows(raw)

sys_pre = actual(capture4 + '01_sys_pre.rx.bin')
mot_pre = actual(capture4 + '02_mot_pre.rx.bin')
apply_rows = actual(capture4 + '03_cfg_apply.rx.bin')
sys_cfg = actual(capture4 + '04_sys_post.rx.bin')
mot_cfg = actual(capture4 + '05_mot_post.rx.bin')
safety = actual(capture4 + '06_safety_post.rx.bin')
irq_pre = actual(capture3 + '07_irqboot.rx.bin')

rows = {'_about': {
    'kind': 'PREVIOUS_FIELD_REPLAY_AND_SOURCE_SHAPE_NOT_NEW_FIELD_EVIDENCE',
    'reviewed_delivery_commit': git('rev-parse', 'HEAD').decode().strip(),
    'scope': 'One static IRQ trial with motor permit LOW throughout. No arm/start/physical input injection.',
    'polarity_measured': False,
    'polarity_confirmation_semantics': 'Trial-only software flag override explicitly disclosed; source output is preserved verbatim and is NOT evidence of physical polarity measurement.'
}, 'commands': {}}

def add_rows(key, lines, kind, source):
    rows['commands'][key] = {'healthy': lines, '_provenance': {'kind': kind, 'source': source}}

for key, lines, source in [
    ('system_status@pre', sys_pre, capture4 + '01_sys_pre.rx.bin'),
    ('motor_status@pre', mot_pre, capture4 + '02_mot_pre.rx.bin'),
    ('tmc_config_apply CONFIRM', apply_rows, capture4 + '03_cfg_apply.rx.bin'),
    ('system_status@cfg', sys_cfg, capture4 + '04_sys_post.rx.bin'),
    ('motor_status@cfg', mot_cfg, capture4 + '05_mot_post.rx.bin'),
    ('safety_status', safety, capture4 + '06_safety_post.rx.bin'),
    ('safety_irq_status@pre', irq_pre, capture3 + '07_irqboot.rx.bin')]:
    add_rows(key, lines, 'actual_previous_field_capture_NOT_current_trial', source)

input_rows = [r for r in irq_pre[next(i for i, r in enumerate(irq_pre) if r.startswith('[SAFETY] TMC_DIAG')):] if r.startswith('[SAFETY]')]
assert input_rows[0] == '[SAFETY] TMC_DIAG     pin=3 raw=0 safe=0 irq=RISING [safe]'
pol_rows = ['[SAFETY] current raw levels of the ACTIVE protection inputs (operator must have MEASURED these):'] + input_rows + [
    '[SAFETY] NOTE: this is an OPERATOR DECLARATION. Software does NOT',
    '[SAFETY]       verify wiring. Status stays HARDWARE-PENDING for the',
    '[SAFETY]       hardware chain; only the polarity flag changes here.',
    '[SAFETY]       User-confirmed wire names / gate replacement are NOT a',
    '[SAFETY]       measurement and never set this flag by themselves.',
    '[SAFETY] input polarity MARKED VALIDATED (operator confirm)',
    '[SAFETY] protection_ready = NO (IRQ gate still CLOSED)']
enable_rows = [
    '[SAFETY] IRQ gate OPEN: 1 active protection input(s) registered (limits: falling edge / HIGH=safe; diag: rising)',
    '[SAFETY] PC.6 = TMC_ENN feedback input, NOT an IRQ source; board e-stop cuts the chain in hardware, never via MCU']
disable_rows = [
    '[SAFETY] detach: PA.3 detached (0)',
    '[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径']
irq_open = [r.replace('CLOSED (polled only)', 'OPEN (EXTI registered)').replace('NOT confirmed', 'operator CONFIRMED').replace('protection_ready = NO', 'protection_ready = YES') for r in irq_pre]
irq_closed = [r.replace('NOT confirmed', 'operator CONFIRMED') for r in irq_pre]
sys_open = [r.replace('irq_gate=CLOSED polarity=not-confirmed protect_ready=NO', 'irq_gate=OPEN polarity=CONFIRMED protect_ready=YES').replace('mask=0x8 (arm REFUSED)', 'mask=0x0 (all gate conditions pass (incl. ENN feedback))') for r in sys_cfg]
# A zero gate mask omits mot_print_gate_fail() entirely, including the six gate details.
mot_open = [r.replace('arm_gate_mask=0x8 REFUSED', 'arm_gate_mask=0x0 ALL-PASS') for r in mot_cfg if not re.match(r'^\[MOT\] (?:gate[1-6] |gate_reason=|arm_gate_mask=0x8$)', r)]
sys_closed = [r.replace('polarity=not-confirmed', 'polarity=CONFIRMED') for r in sys_cfg]
for key, lines in [
    ('safety_polarity_confirm CONFIRM', pol_rows),
    ('safety_irq_enable', enable_rows),
    ('safety_irq_disable', disable_rows),
    ('safety_irq_status@open', irq_open),
    ('safety_irq_status@closed', irq_closed),
    ('system_status@open', sys_open),
    ('motor_status@open', mot_open),
    ('system_status@closed', sys_closed)]:
    add_rows(key, lines, 'source_shape_NOT_field_evidence', ['applications/safety_thread.c', 'applications/supervisor.c', 'applications/motor.c'])
add_rows('__watch__', [], 'source_shape_silent_window_NOT_field_evidence', 'No injection or expected trip; observe bounded quiet interval only.')
for key in m.CLEANUP_CMDS:
    rows['commands'][key] = copy.deepcopy(oldrows['commands'][key])
    rows['commands'][key]['_unused_in_this_plan'] = 'Original stub builder requires cleanup fixtures even without arm/start. Cleanup quotas remain zero and no obligation is established.'

p = copy.deepcopy(base)
p['package'] = 'M8-R7F5-b502-BLIND-IRQ-STATIC-TRIAL'
p['task_id'] = 'M8R7F5-b502-blind-irq-static-20261104'
p['status'] = 'A_REVIEWED_STATIC_TRIAL_ONLY_USER_REQUESTED_ONCE_NO_ANALYZER'
p['numbers_are_proposals_not_authorization'] = False
p['scope_this_package'] = 'Cold power-up precondition, one COM5 dispatch; one config apply, one disclosed trial-only polarity software flag set without physical measurement, one IRQ enable, static quiet observation and readiness checks, one IRQ disable. Motor output disabled throughout. No arm/start/STEP/learning/triggering/retry/reset/flash/readback.'
p['approval'] = {
    'status': 'SCOPE_LIMITED_TO_USER_ONE_BLIND_TRIAL_REQUEST',
    'operator_request_verbatim': '我们盲测一次 如果失败再考虑逻辑分析仪测试',
    'proposed_new_approval_id': p['task_id'],
    'prior_approvals': 'Exhausted approvals do not carry forward.',
    'additional_hardware_authorization': 'No energization, arm, start, physical signal injection or measurement claim is authorized.'}
p['trial_exception'] = {
    'polarity_measured': False,
    'trial_only_software_flag_override': True,
    'basis': 'User requests one blind trial after explicitly reporting DIAG unmeasured; limit exception to static IRQ registration with motor disabled.',
    'software_output_is_not_physical_evidence': True,
    'core_protection_verified': False,
    'end_condition': 'Operator powers down both VM and MCU after port closure, whether trial succeeds or fails; flag is volatile and no clear-flag command is invented.',
    'forbidden_claims': ['PHYSICAL_POLARITY_VERIFIED', 'DIAG_TRIGGER_VERIFIED', 'STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE', 'CORE_PROTECTION_VERIFIED']}
p['operator_power_cycle'] = {
    'outside_dispatch_human_action_pairs_max': 1,
    'start_state_reported': 'MCU and VM off; DIAG unmeasured.',
    'before_dispatch': 'Operator supplies VM before MCU startup self-test reaches IOIN; power MCU once, allow bootstrap completion. Do not reset/retry if startup fails.',
    'after_dispatch': 'After normal port closure or first failure, operator turns VM and MCU off once and records observed power state. DAP may supply MCU: removing VM alone does not clear MCU RAM flag.',
    'instrument_actions': 0,
    'not_executor_proven': True}
p['quota'] = {k: 0 for k in base['quota']}
p['quota'].update({'port_open': 1, 'tmc_config_apply': 1, 'irq_polarity_prep': 2, 'safety_irq_disable': 1})
p['hardware_actions_proposed'] = {'port_open_COM5': 1, 'tmc_config_apply_CONFIRM': 1, 'trial_only_safety_polarity_confirm_CONFIRM': 1, 'safety_irq_enable': 1, 'safety_irq_disable': 1, 'arm_start_or_other_actions': 0}
p['hardware_actions_this_round'] = []
p['rows_file'] = Path(ROWS_REL).name
p['motion_budget'] = {'starts': 0, 'seconds_total': 0, 'registered_checker_gap': 'Existing PLAN_MOTION_CAP_BUDGET nonempty cap test is N/A for this fixed zero-arm/start/learning static trial. Do not add a fake movement budget or change executor.'}
p['observation_bounds'] = {'applies_to': 'NO_RESET_OR_BOOT_TIMING_TRIAL', 'note': 'Human performs one initial power-up; dispatch reads current state, not a captured boot window. No 75 s boot-window claim.'}
p['working_tree_now'] = {'reviewed_commit': git('rev-parse', 'HEAD').decode().strip()}
p['identity_binding']['reviewed_delivery_commit'] = p['working_tree_now']['reviewed_commit']
p['identity_binding']['already_proven_board_image']['current_power_state_source'] = 'Operator reported MCU and VM off. Prior b502 Flash byte proof remains historical; all volatile runtime states must be read fresh.'
p['derived_from'] = {'plan': FIELD_REL + '/plan_r7f4_b502_configured_reviewed.json', 'decision': 'One blind static IRQ trial only; no hardware measurement claim or motor energization.'}
p.pop('review_fixes', None)

steps0 = base['stages'][1]['steps']
def find(cmd):
    return next(copy.deepcopy(s) for s in steps0 if s['cmd'] == cmd)

steps = [copy.deepcopy(steps0[0]), copy.deepcopy(steps0[1])]
steps.append(find('tmc_config_apply CONFIRM'))
for original, key, alias in [(steps0[3], 'system_status@cfg', 'sys_cfg'), (steps0[4], 'motor_status@cfg', 'mot_cfg')]:
    st = copy.deepcopy(original)
    st.update(key=key, **{'as': alias})
    steps.append(st)

diag_row = r'^\[SAFETY\] TMC_DIAG\s+pin=(?P<pin>\d+) raw=(?P<raw>\d+) safe=(?P<safe>\d+) irq=(?P<irq>\w+) \[safe\]$'
def irq_step(alias, key, gate, polarity, ready):
    return {'cmd': 'safety_irq_status', 'as': alias, 'key': key, 'seconds': 5,
            'expect_fields': [[r'^\[SAFETY\] irq gate   = ' + re.escape(gate) + '$', 'irq-gate'], [r'^\[SAFETY\] polarity   = ' + re.escape(polarity) + r' \(HARDWARE-PENDING unless CONFIRMed\)$', 'software-flag-only'], [r'^\[SAFETY\] protection_ready = ' + ready + '$', 'software-readiness']],
            'expect_contains': ['[SAFETY] gpio_ready = YES', '[SAFETY] thread     = running health=OK', '[SAFETY] LIMIT inputs config = DISABLED_BY_CONFIG (SAFETY_LIMIT_INPUTS_ENABLED)', '[SAFETY] PC.6(ENN_FB) pin=ok raw=HIGH = TMC_ENN feedback (HIGH=disabled side). NOT an e-stop input, no IRQ registered on it'],
            'expect_values': [{'row': diag_row, 'must': {'pin': '3', 'raw': '0', 'safe': '0', 'irq': 'RISING'}}]}

steps.append(irq_step('irq_pre', 'safety_irq_status@pre', 'CLOSED (polled only)', 'NOT confirmed', 'NO'))
steps.append({'cmd': 'safety_polarity_confirm CONFIRM', 'as': 'pol_trial_flag', 'seconds': 5, 'quota': {'name': 'irq_polarity_prep', 'n': 1},
              'expect_fields': [[re.escape(r), 'source-print-%d' % i] for i, r in enumerate(pol_rows)],
              'note': 'TRIAL OVERRIDE ONLY, polarity_measured=false. Preserve actual source bytes, do not report this command as physical measurement.'})
steps.append({'cmd': 'safety_irq_enable', 'as': 'irq_enable', 'seconds': 5, 'quota': {'name': 'irq_polarity_prep', 'n': 1},
              'expect_fields': [[re.escape(r), 'source-print-%d' % i] for i, r in enumerate(enable_rows)]})
steps.append(irq_step('irq_open', 'safety_irq_status@open', 'OPEN (EXTI registered)', 'operator CONFIRMED', 'YES'))
steps.append({'cmd': '__watch__', 'as': 'quiet_observation', 'key': '__watch__', 'seconds': 3, 'note': 'Bounded passive observation; original slack may extend request. No expected trigger or physical manipulation.'})
st = copy.deepcopy(steps0[3])
st.update({'as': 'sys_open', 'key': 'system_status@open', 'expect_contains': ['=== SYSTEM STATUS ===', '[Boot]   bootstrap-complete=YES', '[Motor] arm gate fail mask=0x0 (all gate conditions pass (incl. ENN feedback))'], 'status_lines_allow': []})
st['expect_values'].append({'row': r'^\[Safety\] state=(?P<state>\w+) fault=(?P<fault>\d+) irq_gate=(?P<irq>\w+) polarity=(?P<pol>\w+) protect_ready=(?P<ready>\w+)$', 'must': {'state': 'READY', 'fault': '0', 'irq': 'OPEN', 'pol': 'CONFIRMED', 'ready': 'YES'}})
steps.append(st)
st = copy.deepcopy(steps0[4])
st.update({'as': 'mot_open', 'key': 'motor_status@open', 'expect_contains': ['[MOT] state=', '[MOT] arm_gate_mask=0x0 ALL-PASS'], 'status_lines_allow': []})
steps.append(st)
steps.append({'cmd': 'safety_irq_disable', 'as': 'irq_disable', 'seconds': 5, 'quota': {'name': 'safety_irq_disable', 'n': 1}, 'expect_fields': [[re.escape(r), 'source-print-%d' % i] for i, r in enumerate(disable_rows)]})
steps.append(irq_step('irq_closed', 'safety_irq_status@closed', 'CLOSED (polled only)', 'operator CONFIRMED', 'NO'))
st = copy.deepcopy(steps0[3])
st.update({'as': 'sys_closed', 'key': 'system_status@closed'})
st['expect_values'].append({'row': r'^\[Safety\] state=(?P<state>\w+) fault=(?P<fault>\d+) irq_gate=(?P<irq>\w+) polarity=(?P<pol>\w+) protect_ready=(?P<ready>\w+)$', 'must': {'state': 'READY', 'fault': '0', 'irq': 'CLOSED', 'pol': 'CONFIRMED', 'ready': 'NO'}})
steps.append(st)
steps.append(copy.deepcopy(steps0[-1]))

for st in steps:
    if st['cmd'] == 'system_status' and st['as'] not in ('sys_open', 'sys_closed'):
        st['expect_values'].append({'row': r'^\[Safety\] state=(?P<state>\w+) fault=(?P<fault>\d+) irq_gate=(?P<irq>\w+) polarity=(?P<pol>[\w-]+) protect_ready=(?P<ready>\w+)$', 'must': {'state': 'READY', 'fault': '0', 'irq': 'CLOSED', 'pol': 'not-confirmed', 'ready': 'NO'}})

stage = copy.deepcopy(base['stages'][1])
stage.update({'title': 'One static blind IRQ trial, no energization', 'steps': steps, 'total_deadline_seconds': 90,
              'forbidden_commands': ['motor_arm', 'motor_start', 'motor_disarm', 'motor_stop', 'motor_dir', 'motor_target', 'diag_profile learn', 'diag_profile commit', 'diag_profile zero', 'config_save', 'blackbox_clear', 'fault_reset']})
p['stages'] = [copy.deepcopy(base['stages'][0]), stage]
dump(REPO / ROWS_REL, rows)
p['plan_identity'] = {'path': PLAN_REL, 'rows_file': {'path': ROWS_REL, 'bytes': (REPO / ROWS_REL).stat().st_size, 'sha256': digest((REPO / ROWS_REL).read_bytes())}}
dump(REPO / PLAN_REL, p)
report = {'reviewed_commit': p['working_tree_now']['reviewed_commit'], 'product_source_unchanged_since_0d8239f': True, 'hardware_actions_performed_by_A': 0, 'polarity_measured': False, 'canonical_steps_sha256': m.canonical_steps_sha(m.stage_steps(stage, 'response_driven'))}
report['S0'] = m.s0_binding(p, str(REPO), plan_path=str(REPO / PLAN_REL), snapshot=m.read_plan_snapshot(REPO / PLAN_REL))
lines, bad, _ = m.validate_plan(p, rows, str(app))
(OUT / 'validate_plan.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
fail_lines = [r for r in lines if '|FAIL|' in r]
assert bad == 1 and len(fail_lines) == 1 and fail_lines[0].startswith('PLAN_MOTION_CAP_BUDGET|'), fail_lines
report['validation'] = {'checks': len(lines), 'fail': bad, 'known_NA': fail_lines, 'not_claimed_all_green': True}
report['new_source_shape_line_lengths_including_LF'] = {k: [len((r + '\n').encode()) for r in rows['commands'][k]['healthy']] for k in ['safety_polarity_confirm CONFIRM', 'safety_irq_enable', 'safety_irq_disable']}
assert max(n for lengths in report['new_source_shape_line_lengths_including_LF'].values() for n in lengths) <= 127

cases = [('positive', None, None),
         ('pre_fault', 'system_status@pre', lambda rs: [r.replace('state=READY fault=0', 'state=FAULT_LATCHED fault=8') for r in rs]),
         ('diag_already_active', 'safety_irq_status@pre', lambda rs: [r.replace('raw=0 safe=0 irq=RISING [safe]', 'raw=1 safe=0 irq=RISING [!!ACTIVE!!]') for r in rs]),
         ('enable_refused', 'safety_irq_enable', lambda rs: ['[SAFETY] irq enable REFUSED: input polarity NOT confirmed']),
         ('enable_nul', 'safety_irq_enable', lambda rs: rs + ['\0']),
         ('open_not_ready', 'safety_irq_status@open', lambda rs: [r.replace('protection_ready = YES', 'protection_ready = NO') for r in rs]),
         ('open_output_high', 'system_status@open', lambda rs: [r.replace('request=LOW pad=0', 'request=HIGH pad=1') for r in rs]),
         ('disable_bad_rc', 'safety_irq_disable', lambda rs: [r.replace('detached (0)', 'detached (-1)') for r in rs])]
results = []
batch = OUT / ('replay-' + str(time.time_ns()))
for name, key, mutate in cases:
    d = batch / name
    d.mkdir(parents=True, exist_ok=True)
    cr, cp = copy.deepcopy(rows), copy.deepcopy(p)
    if key:
        cr['commands'][key]['healthy'] = mutate(cr['commands'][key]['healthy'])
    dump(d / 'rows.json', cr)
    cp['rows_file'] = 'rows.json'
    cp['plan_identity'] = {'path': 'offline-test-only', 'rows_file': {'path': 'rows.json', 'bytes': (d / 'rows.json').stat().st_size, 'sha256': digest((d / 'rows.json').read_bytes())}}
    dump(d / 'plan.json', cp)
    with contextlib.redirect_stdout(io.StringIO()) as cap:
        res = m.dispatch_plan(cp, 'stub', 'sim', str(d / 'run'), m.SimClock(), 'healthy', 'COM5', str(REPO), plan_dir=str(d), plan_path=str(d / 'plan.json'), plan_snapshot=m.read_plan_snapshot(d / 'plan.json'))
    (d / 'dispatch.txt').write_text(cap.getvalue() + json.dumps(res, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if name == 'positive':
        assert res['status'] == 'OK', res
    else:
        assert res['status'] in ('STOP', 'REFUSED'), (name, res)
    used = res.get('quota_used', {})
    assert used.get('port_open') == 1
    assert used.get('irq_polarity_prep', 0) <= 2 and used.get('tmc_config_apply', 0) <= 1 and used.get('safety_irq_disable', 0) <= 1
    assert not any(v for k, v in used.items() if k not in ('port_open', 'tmc_config_apply', 'irq_polarity_prep', 'safety_irq_disable'))
    if name == 'pre_fault':
        assert used.get('tmc_config_apply', 0) == 0
    if name == 'diag_already_active':
        assert used.get('irq_polarity_prep', 0) == 0
    sent = [json.loads(l) for l in (d / 'run/S_S1/ledger.jsonl').read_text().splitlines()]
    assert not any('motor_arm' == v.get('cmd') or 'motor_start' == v.get('cmd') for v in sent)
    results.append({'case': name, 'status': res['status'], 'stages': [{'id': s.get('id'), 'status': s.get('status'), 'code': s.get('code')} for s in res['stages']], 'quota_used': used})
report['full_original_executor_replays'] = results
dump(OUT / 'review_result.json', report)
for rel in [PLAN_REL, ROWS_REL]:
    target = PKG / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / rel, target)
for f in ['review_result.json', 'validate_plan.txt']:
    (PKG / 'a_review').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OUT / f, PKG / 'a_review' / f)
print(json.dumps({'checks': report['validation'], 'cases': results, 'steps': len(steps), 'steps_sha': report['canonical_steps_sha256'], 'line_lengths': report['new_source_shape_line_lengths_including_LF']}, ensure_ascii=False, indent=2))
