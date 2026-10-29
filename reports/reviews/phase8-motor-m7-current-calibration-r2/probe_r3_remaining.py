#!/usr/bin/env python3
"""All records are invented offline review probes, not field measurements."""
import copy
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2]).resolve()
out.mkdir(parents=True, exist_ok=True)
tool = pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else root/'reports/reviews/phase8-motor-m7-current-calibration/m7_calcalc.py'
spec = importlib.util.spec_from_file_location('r1_calcalc', tool)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def field_doc():
    d = m.synthetic_doc()
    d['provenance'] = 'FIELD_MEASURED'  # Deliberately synthetic FIELD declaration for negative testing.
    d['method'].update(fuse_rating='HOST_REVIEW_ONLY', series_voltage_drop_mV=1,
        surcharge_handling='HOST_REVIEW_ONLY', environment='HOST_REVIEW_ONLY')
    return d

def capture(p):
    return (
        f"[CAL] sample n_req={int(p['n_requested'])} n_seen={int(p['n_valid'])} n_valid={int(p['n_valid'])} dup=0 invalid=0 skip=0\n"
        f"[CAL] sample seq={int(p['seq_first'])}..{int(p['seq_last'])} tick={int(p['tick_first'])}..{int(p['tick_last'])} ver=1..1 reset=0\n"
        f"[CAL] raw sum={int(p['raw_sum'])} min={int(p['raw_min'])} max={int(p['raw_max'])} mean_x1000={int(p['raw_sum']*1000/p['n_valid'])}\n"
        f"[CAL] raw sum_sq={int(p['raw_sum_sq'])}\n"
        "[CAL] sample cal src=THEORETICAL changed=0 fit=READY reason=OK\n"
        "[CAL] note: sample is observation only; no cal/flash/motion change\n")

def field_case(name, transform=lambda text, p: text, reused=False):
    d = field_doc()
    if reused:
        d['points'][2] = copy.deepcopy(d['points'][0])
        d['points'][2].update(id='H1', role='holdout', used_for_fit=False)
    for p in d['points']:
        path = out/(name+'_'+p['id']+'.rx.txt')
        b = transform(capture(p), p).encode('utf-8')
        path.write_bytes(b)
        p.update(raw_source_file=str(path), raw_source_sha256=hashlib.sha256(b).hexdigest())
    inp = out/(name+'.json'); inp.write_text(json.dumps(d, indent=2)+'\n')
    r = subprocess.run([sys.executable, str(tool), '--input', str(inp)], capture_output=True, text=True)
    (out/(name+'.stdout.txt')).write_text(r.stdout)
    (out/(name+'.stderr.txt')).write_text(r.stderr)
    try: v=json.loads(r.stdout)
    except ValueError: v={}
    print(json.dumps(dict(case=name, rc=r.returncode, status=v.get('status'), usable=v.get('usable'),
        command=v.get('candidate_command_DRAFT_NOT_APPROVED')), sort_keys=True))

field_case('CONTROL_FULL_REAL_FORMAT')
field_case('HEADER_ONLY', lambda text,p: text.splitlines(keepends=True)[0])
field_case('MISSING_DECISION_TAIL', lambda text,p: ''.join(text.splitlines(keepends=True)[:4]))
field_case('NUL_IN_NEGATIVE_DECISION', lambda text,p: text.replace('fit=READY reason=OK','fit=\x00NO reason=CAL_CHANGED'))
field_case('CHANGED_VERSION', lambda text,p: text.replace('ver=1..1 reset=0','ver=1..2 reset=0'))
field_case('REUSED_FIT_AS_HOLDOUT_RENAMED', reused=True)

# A two-point fit with actual UT39A+ 400mA error-limit formula, all code moments attainable.
d = m.synthetic_doc()
x0,x1,xh = 2000.0,2011.0,2010.75
slope = 7.14
yh = 100.0; y0 = yh-slope*(xh-x0); y1 = y0+slope*(x1-x0)
d['points'] = [m._pt('F1','fit',x0,y0,.008*abs(y0)+.3,half=0),
               m._pt('F2','fit',x1,y1,.008*abs(y1)+.3,half=0),
               m._pt('H1','holdout',xh,yh,.008*abs(yh)+.3,half=0)]
h = d['points'][2]
codes = [2010]*32+[2011]*96
h.update(raw_sum=sum(codes), raw_sum_sq=sum(v*v for v in codes), raw_min=min(codes),raw_max=max(codes))
v = m.compute(copy.deepcopy(d))
bh = v['holdout_points'][0]
e0,e1,eh = [p['reference_error_mA'] for p in d['points']]
corner_pred = m.predict_from([x0+.5,x1-.5],[y0-e0,y1+e1],xh+.5)
corner_difference = abs(corner_pred-(yh-eh))
row = dict(case='NONLINEAR_INTERVAL_CORNER',status=v['status'], usable=v['usable'],
           reported_bound=bh['worst_case_bound_mA'], target=bh['target_mA'],
           exact_corner_difference=corner_difference,
           nominal_prediction=bh['predicted_mA'],corner_prediction=corner_pred,
           corner_fit_x=[x0+.5,x1-.5],corner_fit_y=[y0-e0,y1+e1],
           corner_hold_x=xh+.5,corner_hold_reference=yh-eh,
           reference_limits=[e0,e1,eh])
(out/'NONLINEAR_INTERVAL_CORNER.input.json').write_text(json.dumps(d,indent=2)+'\n')
(out/'NONLINEAR_INTERVAL_CORNER.result.json').write_text(json.dumps(row,indent=2)+'\n')
print(json.dumps(row,sort_keys=True))
print('All source transcripts and numeric references in this script are HOST_REVIEW_SYNTHETIC, not target evidence.')
