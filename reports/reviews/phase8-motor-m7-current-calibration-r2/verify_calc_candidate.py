#!/usr/bin/env python3
"""Focused offline acceptance for a candidate, in addition to its original 54 IDs."""
import copy
import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import contextlib

tool=pathlib.Path(sys.argv[1]).resolve()
out=pathlib.Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
s=importlib.util.spec_from_file_location('candidate_calc',tool);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
results=[]

def response(p):
    return (
        f"[CAL] sample n_req={int(p['n_requested'])} n_seen={int(p['n_valid'])} n_valid={int(p['n_valid'])} dup=0 invalid=0 skip=0\n"
        f"[CAL] sample seq={int(p['seq_first'])}..{int(p['seq_last'])} tick={int(p['tick_first'])}..{int(p['tick_last'])} ver=3..3 reset=0\n"
        f"[CAL] raw sum={int(p['raw_sum'])} min={int(p['raw_min'])} max={int(p['raw_max'])} mean_x1000={int(p['raw_sum']*1000//p['n_valid'])}\n"
        f"[CAL] raw sum_sq={int(p['raw_sum_sq'])}\n"
        "[CAL] sample cal src=THEORETICAL changed=0 fit=READY reason=OK\n"
        "[CAL] note: sample is observation only; no cal/flash/motion change\n")

def exercise(name,change=None,expect_ok=False,encoding='LF',reuse=False,mutate_doc=None):
    run=out/name;run.mkdir(exist_ok=True)
    d=m.synthetic_doc();d['provenance']='FIELD_MEASURED'
    d['method'].update(fuse_rating='HOST_REVIEW_ONLY',series_voltage_drop_mV=1,
        surcharge_handling='HOST_REVIEW_ONLY',environment='HOST_REVIEW_ONLY')
    if reuse:
        d['points'][2]=copy.deepcopy(d['points'][0]);d['points'][2].update(id='H1',role='holdout',used_for_fit=False)
    for i,p in enumerate(d['points']):
        text=response(p)
        if i==0 and change:text=change(text)
        if encoding=='CRLF':text=text.replace('\n','\r\n')
        if encoding=='ECHO' and i==0:text='msh >current_cal sample 128\r'+text
        path=run/(p['id']+'.rx.bin');b=text.encode();path.write_bytes(b)
        p.update(raw_source_file=str(path),raw_source_sha256=hashlib.sha256(b).hexdigest())
    if mutate_doc:mutate_doc(d)
    inp=run/'input.json';inp.write_text(json.dumps(d,indent=2)+'\n')
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf):rc=m.main(['--input',str(inp)])
    text=buf.getvalue();(run/'stdout.txt').write_text(text)
    ok=(rc==0 and 'current_cal apply' in text and 'DRAFT_NOT_APPROVED' in text) if expect_ok else (rc==1 and 'REJECT ' in text and 'current_cal apply' not in text)
    row=dict(case=name,pass_=ok,rc=rc);results.append(row);print(json.dumps(row))

exercise('FULL_LF',expect_ok=True)
exercise('FULL_CRLF',expect_ok=True,encoding='CRLF')
exercise('FULL_KNOWN_MSH_ECHO',expect_ok=True,encoding='ECHO')
exercise('HEADER_ONLY',lambda t:t.splitlines(keepends=True)[0])
exercise('NO_DECISION_TAIL',lambda t:''.join(t.splitlines(keepends=True)[:4]))
exercise('NUL_IN_NEGATIVE_DECISION',lambda t:t.replace('fit=READY reason=OK','fit=\x00NO reason=CAL_CHANGED'))
exercise('VERSION_CHANGED',lambda t:t.replace('ver=3..3','ver=3..4'))
exercise('CHANGE_FLAG',lambda t:t.replace('changed=0','changed=1'))
exercise('NO_POSITIVE_FIT',lambda t:t.replace('fit=READY','fit=UNKNOWN'))
exercise('NO_REASON_FIELD',lambda t:t.replace(' reason=OK',''))
exercise('DUPLICATED_SAMPLE',lambda t:t+t)
exercise('REORDERED_LINES',lambda t:'\n'.join(t.splitlines()[1:2]+t.splitlines()[:1]+t.splitlines()[2:])+'\n')
exercise('TICK_DIFFERENT_FROM_DECLARATION',lambda t:t.replace('tick=1000..3560','tick=1001..3560'))
exercise('NREQ_DIFFERENT',lambda t:t.replace('n_req=128','n_req=127'))
exercise('NUMERIC_TOKEN_SUFFIX',lambda t:t.replace('n_valid=128','n_valid=128junk'))
exercise('RENAMED_COPY_OF_FIT',reuse=True)
exercise('BARE_CR_INSIDE_LINE',lambda t:t.replace('raw sum=','raw\r sum='))
exercise('FORGED_SHA',mutate_doc=lambda d:d['points'][0].update(raw_source_sha256='0'*64))

# Same exact numerical counterexample as the independent baseline review.
d=m.synthetic_doc();x0,x1,xh=2000.,2011.,2010.75;a=7.14;yh=100.;y0=yh-a*(xh-x0);y1=y0+a*(x1-x0)
d['points']=[m._pt('F1','fit',x0,y0,.008*abs(y0)+.3,half=0),m._pt('F2','fit',x1,y1,.008*abs(y1)+.3,half=0),m._pt('H1','holdout',xh,yh,1.1,half=0)]
codes=[2010]*32+[2011]*96;d['points'][2].update(raw_sum=sum(codes),raw_sum_sq=sum(x*x for x in codes),raw_min=2010,raw_max=2011)
v=m.compute(copy.deepcopy(d));h=v['holdout_points'][0]
corner=m.predict_from([2000.5,2010.5],[y0-d['points'][0]['reference_error_mA'],y1+d['points'][1]['reference_error_mA']],2011.25)
need=abs(corner-98.9)
ok=v['status']=='REJECT' and not v['usable'] and v['candidate_command_DRAFT_NOT_APPROVED'] is None and h['worst_case_bound_mA']+1e-9>=need
results.append(dict(case='EXACT_NONLINEAR_CORNER_REJECT',pass_=ok,bound=h['worst_case_bound_mA'],corner=need,target=h['target_mA']))

bad=m.synthetic_doc();bad['points'][0].update(raw_min=1980,raw_max=2020)
try:m.compute(bad);ok=False
except m.Reject as e:ok=e.code=='IMPOSSIBLE_SECOND_MOMENT'
results.append(dict(case='IMPOSSIBLE_OBSERVED_EXTREMA',pass_=ok))

extra=m.synthetic_doc();extra['points'].insert(2,m._pt('F3','fit',2075,110,1.18))
try:m.compute(extra);ok=False
except m.Reject as e:ok=e.code=='UNSUPPORTED_BOUND_MODEL'
results.append(dict(case='MULTIPOINT_BOUND_NOT_PRETENDED',pass_=ok))

(out/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
failed=[r['case'] for r in results if not r['pass_']]
print('SUMMARY',len(results),'pass',len(results)-len(failed),'fail',len(failed),failed)
raise SystemExit(bool(failed))
