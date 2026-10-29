#!/usr/bin/env python3
"""HOST_SYNTHETIC only. No serial library/device imported or constructed."""
import importlib.util, pathlib, tempfile, types, sys, json
root=pathlib.Path(__file__).resolve().parents[1]
p=root/'current/reports/reviews/phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py'
s=importlib.util.spec_from_file_location('orig_p0',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
rows=[]
class P(m.FakePort):
 def close(self):pass

def execute(name,script):
 with tempfile.TemporaryDirectory() as out:
  port=P(script);sys.modules['serial']=types.SimpleNamespace(Serial=lambda *a,**k:port)
  a=types.SimpleNamespace(execute_approved_p0=True,approval_source='HOST_SYNTHETIC',site_confirmed=True,image_continuity_confirmed=True,out=out,port='FAKE',payload_bin='',payload_sha256='',readback_bin='',expect_banner=m.BANNER.decode().strip())
  try:rc=m.run_execute(a);error=None
  except SystemExit as e:rc=1;error=str(e)
  rows.append(dict(case=name,rc=rc,writes=[b.decode().strip() for b in port.writes],error=error))
with tempfile.TemporaryDirectory() as out:
 p=P([b'msh >safety_state\r\n'])
 b=m.send_command(p,'safety_state',out,'echo.bin',m.Ledger(out+'/ledger.json'),1)
 rows.append(dict(case='ECHO_ONLY_ACCEPTED_AS_RESPONSE',accepted=True,payload=b.decode()))
execute('ALL_GARBAGE_RESPONSES_ACCEPTED',m._script())
a=m._script();a[1]=b'[W/kernel.sched] sensor stack is close\r\nmsh >';execute('FIRST_RESPONSE_DANGER_STILL_SENDS_ALL_SIX',a)
a=m._script();a[0]+=b'[W/kernel.sched] sensor stack is close\r\n';execute('BOOT_DANGER_IGNORED',a)
with tempfile.TemporaryDirectory() as out:
 class Broken(P):
  def read(self,n):
   if not hasattr(self,'once'):self.once=True;return b'partial actual bytes'
   raise OSError('HOST_SYNTHETIC disconnected')
 p=Broken([b'x']);r=m.drain_until(p,out,'partial.bin',1)
 rows.append(dict(case='READ_ERROR_DISCARDS_RECEIVED_BYTES',bytes_returned=len(r[1]),raw_file_exists=pathlib.Path(out,'partial.bin').exists()))
with tempfile.TemporaryDirectory() as out:
 class Short(P):
  def write(self,b):self.writes.append(bytes(b));return 0
 p=Short([b'garbage\r\nmsh >'])
 m.send_command(p,'safety_state',out,'short.bin',m.Ledger(out+'/ledger.json'),1)
 rows.append(dict(case='ZERO_BYTE_WRITE_RETURN_IGNORED',accepted=True))
print(json.dumps(rows,indent=2))
assert rows[0]['accepted'] and rows[1]['rc']==0 and len(rows[2]['writes'])==6 and rows[3]['rc']==0 and not rows[4]['raw_file_exists'] and rows[5]['accepted']
print('BASELINE_OBSERVATIONS=6/6 (defect reproduction; not product PASS)')
