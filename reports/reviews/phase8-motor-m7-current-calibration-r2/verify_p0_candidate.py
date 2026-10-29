#!/usr/bin/env python3
"""Offline only: deterministic transport/clock; no serial/DAP construction."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types

def run(m, calc_path):
    calc = m.load_calc(calc_path)
    results = []
    def ck(name, fn):
        try:
            detail = fn()
            results.append((name, True, str(detail)))
        except Exception as e:
            results.append((name, False, type(e).__name__ + ':' + str(e)))

    dmm = dict(meter='UT39A+', range_V=4, mode='DCV',
               Vadc=dict(V=1.65, observed_at='UNKNOWN_TIME'),
               V3v3=dict(V=3.30, observed_at='UNKNOWN_TIME'))
    tail = b'\r\nmsh >'
    raw = {
        'safety_state': b'[SS] state=READY fault_code=0 degraded=0x00\r\n',
        'safety_status': (b'[SAFETY] ready=YES first_err=0\r\n'
            b'[SAFETY] MCU_DRV_ENABLE = 0 (disabled)\r\n'
            b'[SAFETY] DRV_ENABLE last write+readback verified = 0 (-1 = never verified)\r\n'
            b'[SAFETY] request=DISABLE(LOW) pad=0 expected_FB=HIGH FB=1 -> verdict=OK\r\n'),
        'motor_status': (b'[MOT] state=0 name=OTHER\r\n'
            b'[MOT] cur_hz=0 tgt_hz=0 accel=100 decel=100 dir=0\r\n'
            b'[MOT] armed=0 drv_en_request=0 health=OK\r\n'
            b'[MOT] stop result=' + m.API_ACCEPTED + b' src=init rc=0 latched=0\r\n'
            b'[MOT] diag_off_confirmed=YES\r\n'
            b'[MOT] drv_request=LOW pad=0\r\n'
            b'[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK\r\n'),
        'step_status': (b'[STEP] dev=epwm1 ch=0 enabled=0\r\n'
            b'[STEP] last_stop=not-attempted stop_rc=0 latched=0\r\n'),
        'current_cal show': (b'[CAL] adc health=OK src=THEORETICAL ver=2\r\n'
            b'[CAL] adc offset_uV=1650000 gain_uV_per_A=600000 measured=NO\r\n'
            b'[CAL] cfg valid=NO src=1 offset_uV=1650000 gain_uV_per_A=600000\r\n'
            b'[CAL] sample seq=10 tick=100 raw=2048 valid=1 fresh=1\r\n'
            b'[CAL] sample ma_uA=0 src=THEORETICAL\r\n'
            b'[CAL] note: show/sample do not change cal/motion/flash\r\n'),
        'current_cal sample 128': (b'[CAL] sample n_req=128 n_seen=128 n_valid=128 dup=0 invalid=0 skip=0\r\n'
            b'[CAL] sample seq=10..137 tick=1000..2280 ver=2..2 reset=0\r\n'
            b'[CAL] raw sum=262144 min=2048 max=2048 mean_x1000=2048000\r\n'
            b'[CAL] raw sum_sq=536870912\r\n'
            b'[CAL] sample cal src=THEORETICAL changed=0 fit=READY reason=OK\r\n'
            b'[CAL] note: sample is observation only; no cal/flash/motion change\r\n'),
    }

    class Clock:
        def __init__(self): self.t = 0.; self.hook = None
        def monotonic(self): return self.t
        def sleep(self, n):
            self.t += max(n, .001)
            if self.hook: self.hook()

    class Port:
        def __init__(self, clock, mutate=None):
            self.c, self.mutate, self.buf, self.writes = clock, mutate, b'', []
            self.timeout = .1; self.write_timeout = None
            self.short = False; self.late_read = False; self.late_write = False
            self.fail_read = False; self.split = 512
        @property
        def in_waiting(self): return len(self.buf)
        def read(self, n):
            self.c.sleep(.01)
            if self.fail_read and not self.buf: raise OSError('HOST_SYNTHETIC disconnect')
            v, self.buf = self.buf[:min(n,self.split)], self.buf[min(n,self.split):]
            if v and self.late_read: self.c.sleep(100)
            return v
        def write(self, wire):
            self.writes.append(wire)
            cmd = wire.decode().strip()
            reply = raw[cmd] + tail
            if self.mutate: reply = self.mutate(cmd, len(self.writes), reply)
            self.buf += b'msh >' + wire + reply
            if self.late_write: self.c.sleep(3)
            return 0 if self.short else len(wire)

    def pipeline(change=None, human=True, callback=None, expect_error=None):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); c = Clock(); p = Port(c, change); s = m.Session(p,out,c)
            if human:
                def hook():
                    for phase in ('before','after'):
                        target = out/('dmm_'+phase+'.json')
                        if ('"phase": "'+phase+'"') in (out/'ledger.jsonl').read_text() and not target.exists():
                            target.write_text(json.dumps(dmm))
                c.hook = hook
            else:
                original = s.wait_dmm
                s.wait_dmm = lambda phase: original(phase, .1)
            if callback: callback(s,p,c,out)
            try:
                value = m.observe(s,calc); error=None
            except Exception as e:
                error=e; value=None
            s.ledger.close()
            cmds=[x.decode().strip() for x in p.writes]
            if expect_error:
                assert error is not None, ('unexpected success',cmds)
                if isinstance(expect_error, type): assert isinstance(error, expect_error), repr(error)
                else: assert expect_error in str(error), str(error)
            else: assert error is None, repr(error)
            return cmds,value,dict((f.name,f.read_bytes()) for f in out.iterdir() if f.is_file())

    def healthy():
        cmds,r,files=pipeline()
        assert tuple(cmds)==m.SEQUENCE and cmds.count('current_cal show')==2
        assert r['status']=='VOLTAGE_OBSERVATION_ONLY' and len(r['readings'])==4
        assert files['06_current_cal_sample_128.rx.bin'].endswith(tail)
        return '7 TX, two show, one sample, 4 DMM values'
    ck('P0T01-whitelist-six-sequence-seven', lambda: (None if len(m.WHITELIST)==6 and len(m.SEQUENCE)==7 else 1/0))
    ck('P0T02-no-privileged-command', lambda: (None if set(m.SEQUENCE)==set(raw) else 1/0))
    ck('P0T03-offline-no-pyserial-import', lambda: (None if 'serial' not in sys.modules else 1/0))
    def bind_bad():
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.bin';p.write_bytes(b'bad')
            a=types.SimpleNamespace(approval_source='HOST_SYNTHETIC',site_confirmed=True,
                execute_approved_p0=True,payload_bin=str(p),payload_axf='',readback_bin='',uid_file='',calculator=calc_path,out=str(Path(tmp)/'new'))
            try:m.execute(a)
            except m.Stop as e: assert str(e)=='PAYLOAD_OR_READBACK_MISMATCH'
            else:raise AssertionError('accepted wrong bytes')
            assert not Path(a.out).exists() and 'serial' not in sys.modules
    ck('P0T04-bind-before-transport',bind_bad)
    def boot_test(data, complete=True):
        with tempfile.TemporaryDirectory() as tmp:
            c=Clock();p=Port(c);p.buf=data;s=m.Session(p,tmp,c)
            try:r=s.capture('boot.bin',.2,m.BOOT_CAP,lambda b:m.BANNER in b and m.BOOT_DONE+b'\n' in b.replace(b'\r\n',b'\n'))
            except m.Stop:
                assert not complete
            else: assert complete and r==data
            assert (Path(tmp)/'boot.bin').read_bytes()==data and not p.writes
            s.ledger.close()
    ck('P0T05-boot-bytes-before-decision',lambda:boot_test(m.BANNER+b'\r\n'+m.BOOT_DONE+b'\r\n'))
    def banned():
        with tempfile.TemporaryDirectory() as tmp:
            c=Clock();p=Port(c);s=m.Session(p,tmp,c)
            try:s.query('config_save',lambda b:b)
            except m.Stop:pass
            else:raise AssertionError('not refused')
            assert not p.writes;s.ledger.close()
    ck('P0T06-whitelist-reject-before-write',banned)
    def bounded():
        with tempfile.TemporaryDirectory() as tmp:
            c=Clock();p=Port(c);p.buf=b'x'*10000;s=m.Session(p,tmp,c)
            try:s.capture('cap.bin',1,1024,lambda b:False)
            except m.Stop as e:assert str(e).startswith('RX_BYTE_CAP')
            else:raise AssertionError('unbounded')
            assert (Path(tmp)/'cap.bin').stat().st_size==1024;s.ledger.close()
    ck('P0T07-read-cap',bounded)
    def echo_only():
        cmds,_,files=pipeline(lambda cmd,i,r:b'' if i==1 else r,expect_error='REPLY_TIMEOUT')
        assert cmds==['safety_state'] and files['01_safety_state.rx.bin']==b'msh >safety_state\r'
    ck('P0T08-echo-is-not-response-end-no-retry',echo_only)
    def danger_first():
        cmds,_,files=pipeline(lambda cmd,i,r:b'sensor stack is close\r\n'+r if i==1 else r,
                               expect_error='STACK_DANGER_OBSERVED')
        assert cmds==['safety_state'] and b'stack is close' in files['01_safety_state.rx.bin']
    ck('P0T09-immediate-danger-save-stop',danger_first)
    ck('P0T10-complete-production-shaped-sequence',healthy)
    def ledger():
        _,_,files=pipeline();rows=[json.loads(x) for x in files['ledger.jsonl'].splitlines()]
        assert sum(x['kind']=='TX_ATTEMPT' for x in rows)==7
        assert sum(x['kind']=='COMMAND_ACCEPTED' for x in rows)==7
        assert [x['phase'] for x in rows if x['kind']=='DMM_RECEIVED']==['before','after']
    ck('P0T11-ledger-attempt-vs-accepted',ledger)
    ck('P0T12-boot-danger-no-TX',lambda:boot_test(m.BANNER+b'\r\nsensor stack is close\r\n'+m.BOOT_DONE+b'\r\n',False))
    def reject_change(name,cmd,old,new,n,reason):
        def exercise():
            cmds,_,_=pipeline(lambda c,i,r:r.replace(old,new) if c==cmd else r,expect_error=reason)
            assert len(cmds)==n,cmds
        ck(name,exercise)
    reject_change('P0T13-garbage-not-complete','safety_state',raw['safety_state'],b'garbage\r\n',1,'REPLY_TIMEOUT')
    reject_change('P0T14-armed-state-blocks-sample','motor_status',b'armed=0',b'armed=1',3,'MISSING_DUPLICATE')
    reject_change('P0T15-latched-stop-blocks-sample','motor_status',b'rc=0 latched=0',b'rc=0 latched=1',3,'MISSING_DUPLICATE')
    reject_change('P0T16-short-FB-proof-required','safety_status',b'FB=1 -> verdict=OK',b'FB=0 -> verdict=OK',2,'MISSING_DUPLICATE')
    reject_change('P0T17-conflicting-state','safety_state',raw['safety_state'],raw['safety_state']+b'[SS] state=FAULT_LATCHED fault_code=8 degraded=0x00\r\n',1,'SAFETY_STATE_CONFLICT')
    def partial():
        with tempfile.TemporaryDirectory() as tmp:
            c=Clock();p=Port(c);p.buf=b'actual partial bytes';p.fail_read=True;s=m.Session(p,tmp,c)
            try:s.capture('partial.bin',.2,1024,lambda b:False)
            except OSError:pass
            else:raise AssertionError('did not fail')
            assert (Path(tmp)/'partial.bin').read_bytes()==b'actual partial bytes';s.ledger.close()
    ck('P0T18-error-preserves-partial-RX',partial)
    def short():
        cmds,_,files=pipeline(callback=lambda s,p,c,o:setattr(p,'short',True),expect_error='SHORT_OR_LATE_WRITE')
        assert len(cmds)==1 and '01_safety_state.rx.bin' not in files
    ck('P0T19-short-write-no-read-no-next',short)
    def late_read():
        cmds,_,files=pipeline(callback=lambda s,p,c,o:setattr(p,'late_read',True),expect_error='RX_LATE_SUCCESS')
        assert len(cmds)==1 and b'[SS]' in files['01_safety_state.rx.bin']
    ck('P0T20-deadline-rechecked-after-read',late_read)
    def late_write():
        cmds,_,_=pipeline(callback=lambda s,p,c,o:setattr(p,'late_write',True),expect_error='SHORT_OR_LATE_WRITE');assert len(cmds)==1
    ck('P0T21-deadline-rechecked-after-write',late_write)
    def evidence():
        def setup(s,p,c,out): (out/'01_safety_state.rx.bin').mkdir()
        cmds,_,_=pipeline(callback=setup,expect_error=OSError);assert cmds==['safety_state']
    ck('P0T22-evidence-failure-no-next',evidence)
    reject_change('P0T23-sample-NUL','current_cal sample 128',b'fit=READY',b'fit=\x00NO',6,'SAMPLE_NOT_ACCEPTED')
    reject_change('P0T24-sample-not-ready','current_cal sample 128',b'fit=READY reason=OK',b'fit=NO reason=CAL_CHANGED',6,'SAMPLE_NOT_ACCEPTED')
    def changed():
        cmds,_,_=pipeline(lambda c,i,r:r.replace(b'ver=2',b'ver=3') if i==7 else r,expect_error='CAL_CHANGED_ACROSS_P0');assert len(cmds)==7
    ck('P0T25-second-show-actually-compared',changed)
    def no_dmm():
        cmds,_,_=pipeline(human=False,expect_error='DMM_WAIT_EXPIRED');assert len(cmds)==4
    ck('P0T26-waits-before-sampling',no_dmm)
    def preexisting():
        def setup(s,p,c,out): (out/'dmm_before.json').write_text(json.dumps(dmm))
        cmds,_,_=pipeline(callback=setup,expect_error='DMM_PREEXISTING');assert len(cmds)==4
    ck('P0T27-preexisting-DMM-not-reused',preexisting)
    ck('P0T28-wrong-banner-no-TX',lambda:boot_test(b'wrong banner\n'+m.BOOT_DONE+b'\n',False))
    ck('P0T29-incomplete-main-line-no-TX',lambda:boot_test(m.BANNER+b'\n[MAIN] boot complete: READ',False))
    def pending():
        def setup(s,p,c,out): p.buf=b'benign async output\r\nmsh >'
        cmds,_,files=pipeline(callback=setup)
        assert len(cmds)==7 and files['before_01.bin']==b'benign async output\r\nmsh >'
    ck('P0T30-pending-async-not-used-as-response',pending)
    def long_info():
        info=b'[SAFETY] ENN_FEEDBACK(PC.6) '+b'x'*98+b'\x00\r\n'
        cmds,_,files=pipeline(lambda c,i,r:info+r if c=='safety_status' else r)
        assert len(cmds)==7 and b'\x00' in files['02_safety_status.rx.bin']
    ck('P0T31-known-noncanonical-NUL-not-gate-proof',long_info)
    def split_echo():
        def setup(s,p,c,o):p.split=5
        cmds,_,_=pipeline(callback=setup);assert len(cmds)==7
    ck('P0T32-split-echo-and-response',split_echo)
    def reset_test(bad=False):
        with tempfile.TemporaryDirectory() as tmp:
            c=Clock();p=Port(c);s=m.Session(p,tmp,c);calls=[]
            s.event('PORT_OPEN',port='FAKE')
            class Process:
                def __init__(self,argv,**kw):
                    assert 'PORT_OPEN' in (Path(tmp)/'ledger.jsonl').read_text()
                    calls.append(argv);self.returncode=None
                    assert argv[argv.index('--method')+1]=='sysresetreq'
                    assert argv[argv.index('--connect')+1]=='attach'
                    assert '--no-config' in argv and '--no-wait' in argv and '-u' in argv
                    p.buf=(b'WRONG' if bad else m.BANNER)+b'\r\n'+m.BOOT_DONE+b'\r\n'
                def wait(self,timeout):self.returncode=0;return 0
                def poll(self):return self.returncode
                def terminate(self):self.returncode=-15
            old=m.subprocess.Popen;m.subprocess.Popen=Process
            try:
                try:m.reset_once(s,'HOST_SYNTHETIC_UID')
                except m.Stop:assert bad
                else:assert not bad
            finally:m.subprocess.Popen=old;s.ledger.close()
            assert len(calls)==1 and not p.writes
            assert (Path(tmp)/'raw_boot.bin').read_bytes().startswith(b'WRONG' if bad else m.BANNER)
    ck('P0T33-port-before-one-known-reset',reset_test)
    ck('P0T34-bad-reset-banner-no-retry',lambda:reset_test(True))
    def invalid_dmm():
        def setup(s,p,c,out):
            def hook():
                target=out/'dmm_before.json'
                if 'WAIT_DMM' in (out/'ledger.jsonl').read_text() and not target.exists():
                    target.write_text(json.dumps(dict(dmm,meter='WRONG')))
            c.hook=hook
        cmds,_,_=pipeline(callback=setup,expect_error='DMM_METHOD_MISMATCH');assert len(cmds)==4
    ck('P0T35-invalid-meter-no-sample',invalid_dmm)
    def merged_info():
        head=b'[SAFETY] ENN_FEEDBACK(PC.6) '
        info=head+b'x'*(126-len(head))+b'\x00'
        old=b'[SAFETY] request='
        cmds,_,files=pipeline(lambda c,i,r:r.replace(old,info+old) if c=='safety_status' else r)
        assert len(cmds)==7 and info+old in files['02_safety_status.rx.bin']
    ck('P0T36-exact-noncanonical-truncation-boundary',merged_info)
    reject_change('P0T37-NUL-in-authoritative-line-rejected','safety_status',b'request=DISABLE',b'request=DISA\x00BLE',2,'MISSING_DUPLICATE')
    failed=[x for x in results if not x[1]]
    for name,ok,detail in results: print(('PASS ' if ok else 'FAIL ')+name+' '+detail)
    print('SUMMARY cases=%d pass=%d fail=%d'%(len(results),len(results)-len(failed),len(failed)))
    print('HOST_SYNTHETIC_ONLY / DEVICE_ACTIONS=0 / NO_REAL_TRANSPORT')
    return bool(failed)

if __name__=='__main__':
    p=Path(sys.argv[1]).resolve();spec=importlib.util.spec_from_file_location('p0_candidate',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    raise SystemExit(run(m,sys.argv[2]))
