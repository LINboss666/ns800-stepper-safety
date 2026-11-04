"""Compile the ARD4 continuation assertion against actual product C through the existing host TU.

Exit 0: the assertion passes. Exit 1: the business assertion fails.
Exit 4: setup/compiler/runtime failure. This never opens a serial or hardware device.
"""
from pathlib import Path
import argparse, json, os, shutil, subprocess, sys

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo-root',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--cc',default='gcc')
    args=ap.parse_args()
    repo=Path(args.repo_root).resolve(strict=True)
    out=Path(args.out).resolve()
    if out==repo or repo in out.parents:
        raise ValueError('--out must be outside the repository')
    out.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parent/'commit_rollback_probe.c'
    harness=repo/'tests/host/rel_main.c'
    app=repo/'rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications'
    cc=shutil.which(args.cc) or args.cc
    argv=[cc,'-std=gnu11','-Wall','-Wextra','-Wno-unused-function','-Wno-unused-parameter']
    compatibility_note='Native Windows build; no compatibility adapter.'
    if os.name!='nt':
        inc=out/'compat_include';inc.mkdir(exist_ok=True)
        (inc/'windows.h').write_text('/* Review-only: this host chain includes windows.h but uses no Win32 APIs. */\n')
        argv+=['-I',str(inc)]
        compatibility_note='Linux portability: empty windows.h for an unused include only. No RTOS, mutex, clock or product behavior replaced.'
    exe=out/('commit_rollback_probe.exe' if os.name=='nt' else 'commit_rollback_probe')
    argv+=['-I',str(repo/'tests/host/shim_rel'),'-I',str(app),
           '-DREVIEW_HOST_MAIN="'+harness.as_posix()+'"',str(source),'-o',str(exe)]
    build=subprocess.run(argv,capture_output=True)
    (out/'build.stdout.txt').write_bytes(build.stdout)
    (out/'build.stderr.txt').write_bytes(build.stderr)
    result={'compatibility':compatibility_note,'build_rc':build.returncode,'argv':argv,'hardware_actions':0}
    if build.returncode:
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(build.stderr.decode(errors='replace'));return 4
    run=subprocess.run([str(exe)],capture_output=True)
    (out/'probe.stdout.txt').write_bytes(run.stdout)
    (out/'probe.stderr.txt').write_bytes(run.stderr)
    result['run_rc']=run.returncode
    result['assertion_lines']=[s for s in run.stdout.decode(errors='replace').splitlines()
                               if s.startswith(('OBS|','REVIEW|','REVIEW_SUMMARY|','PRE|'))]
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('\n'.join(result['assertion_lines']))
    return run.returncode if run.returncode in (0,1,4) else 4

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print('INFRA|'+repr(exc),file=sys.stderr);sys.exit(4)
