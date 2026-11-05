from pathlib import Path
import copy
import importlib.util
import json
import sys

import argparse
p = argparse.ArgumentParser()
p.add_argument('--repo', type=Path, required=True)
p.add_argument('--work', type=Path, required=True)
p.add_argument('--executor', type=Path)
a = p.parse_args()
BASE = a.work.resolve()
BASE.mkdir(parents=True, exist_ok=True)
REPO = a.repo.resolve()
ROUND = REPO / 'reports/reviews/phase8-m8-r7f8-executor-adaptation'
spec = importlib.util.spec_from_file_location('b_matrix', ROUND/'tools/r7f8e_case_matrix.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
exe = (a.executor.resolve() if a.executor else ROUND / 'tools/m8r3_serial_chain_r7f8.py')
work = BASE / 'a_probe_work'
work.mkdir(exist_ok=True)
out = []

def run(name, steps, fixture, extra=None, must_stop=False):
    c = dict(name=name, group='A independent', steps=steps, fixture=fixture,
             extra=extra or [], expect={})
    rc,res,led=m.run_case(REPO,work,exe,c,name)
    item={'name':name,'rc':rc,'status':res.get('status'),'code':res.get('code'),
          'expected': 'First contradictory response must STOP' if must_stop else 'Positive control / timing observation',
          'gap_reproduced': must_stop and res.get('status')=='OK',
          'polls':sum(x.get('kind')=='command_poll' for x in res.get('results',[])),
          'energize_actual':res.get('energize_seconds_actual'),
          'sent':[x.get('cmd') for x in res.get('results',[]) if x.get('cmd')]}
    item['events']=[x for x in led if x.get('kind') in ['ENERGIZE_OBLIGATION_SET','ENERGIZE_DEADLINE_CLOSED','SESSION_STOP','TX']]
    out.append(item)
    return item

def j(st='RUNNING',**kw):
    return m.job(17,st,why='RUNNING' if st=='RUNNING' else 'COMPLETE',
                 owner=kw.pop('owner',2 if st=='RUNNING' else 0),
                 cred=kw.pop('cred','1/0' if st=='RUNNING' else '1/1'),**kw)

steps=[m.learn('learn','x'),m.poll_done('status','x')]
run('POS_real_owner_2',steps,m.fx(responses={m.LEARN:m.started(17)},
    sequence={m.LS:[j(adc=1,sg=1),j('DONE')]}))
for name,bad in [('P1_RUNNING_owner_FREE',j(owner=0,adc=1,sg=1)),
                 ('P2_RUNNING_wrong_want',j(want=9,adc=1,sg=1)),
                 ('P3_RUNNING_wrong_direction',j(d=1,adc=1,sg=1))]:
    run(name,steps,m.fx(responses={m.LEARN:m.started(17)},sequence={m.LS:[bad,j('DONE')]}),must_stop=True)
run('P4_progress_decreases',steps,m.fx(responses={m.LEARN:m.started(17)},
    sequence={m.LS:[j(adc=6,sg=6),j(adc=2,sg=2),j('DONE')]}),must_stop=True)
run('P5_DONE_wrong_window_and_no_candidate',steps,m.fx(responses={m.LEARN:m.started(17),
    m.LS:j('DONE',lo=100,hi=200,d=1)}),must_stop=True)

arm=m.st_('arm','motor_arm',seconds=2,expect_contains=['armed=1'])
fixture=m.fx(responses={'motor_arm':m.ARM_OK,'motor_status':m.STATUS_DISABLED,
                       'motor_stop':m.STOP_OK,'motor_disarm':m.DISARM_OK})
fixture['block']={'motor_arm':3.0}
run('P6_arm_response_exceeds_2s', [arm], fixture,
    ['--arm-energize-deadline-seconds','2'])

run('P7_second_arm_loses_timer',
    [copy.deepcopy(arm),m.st_('disarm','motor_disarm',seconds=2,expect_contains=['DISARM']),
     m.st_('arm2','motor_arm',seconds=2,expect_contains=['armed=1']),
     m.st_('wait','__watch__',seconds=5),m.st_('after','motor_status',seconds=1,expect_contains=['state='])],
    m.fx(responses={'motor_arm':m.ARM_OK,'motor_disarm':m.DISARM_OK,
                   'motor_stop':m.STOP_OK,'__watch__':['[DIAG] quiet'],'motor_status':m.STATUS_DISABLED}),
    ['--arm-energize-deadline-seconds','2'])

run('P8_RUNNING_with_ENN_disabled',
    [m.st_('running','motor_status',seconds=1,expect_profile='RUNNING')],
    m.fx(responses={'motor_status':m.MOT_CRUISE}),must_stop=True)

(BASE/'A_counterexamples.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
for x in out:
    print(json.dumps({k:v for k,v in x.items() if k!='events'},ensure_ascii=False))
