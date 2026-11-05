from pathlib import Path
import argparse
import json
import os
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--repo-root', required=True, type=Path)
p.add_argument('--output-dir', required=True, type=Path)
p.add_argument('--cc', default='gcc')
a = p.parse_args()
repo = a.repo_root.resolve()
out = a.output_dir.resolve()
if out == repo or repo in out.parents:
    raise SystemExit('Output must be outside the repository')
out.mkdir(parents=True, exist_ok=True)
here = Path(__file__).resolve().parent
app = repo / 'rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications'
argv = [a.cc, '-std=gnu11', '-Wall', '-Wextra', '-Wno-unused-function', '-Wno-unused-parameter']
if os.name != 'nt':
    argv += ['-I', str(here / 'compat')]
argv += ['-I', str(repo / 'tests/host/shim_rel'), '-I', str(repo / 'tests/host'),
         '-I', str(app), str(here / 'recovery_ramp_probe.c'), '-o', str(out / 'probe.exe')]
b = subprocess.run(argv, capture_output=True)
(out / 'build.stdout').write_bytes(b.stdout)
(out / 'build.stderr').write_bytes(b.stderr)
if b.returncode:
    print(b.stderr.decode('utf-8', errors='replace'))
    raise SystemExit(b.returncode)
r = subprocess.run([str(out / 'probe.exe')], capture_output=True)
(out / 'run.stdout').write_bytes(r.stdout)
(out / 'run.stderr').write_bytes(r.stderr)
summary = {'build_rc': b.returncode, 'warnings': b.stderr.count(b'warning:'),
           'run_rc': r.returncode, 'hardware_actions': 0,
           'compatibility': 'Native Windows headers' if os.name == 'nt' else 'Empty unused windows.h include'}
(out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(r.stdout.decode('utf-8', errors='strict'))
print(json.dumps(summary))
raise SystemExit(r.returncode)
