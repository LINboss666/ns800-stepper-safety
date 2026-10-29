#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M7-R1 local adaptation of the review's probes/reproduce.py (offline, no device).

Adaptations are declared explicitly, as the review section 7 requires when a seam
anchor moves.  Nothing here edits the repository: every mutated file is written into
the output directory outside the repo.
"""
import json
import os
import pathlib
import subprocess
import sys

repo = pathlib.Path(sys.argv[1]).resolve()
out = pathlib.Path(sys.argv[2]).resolve()
if repo in out.parents or out == repo:
    raise SystemExit("choose a fresh output directory OUTSIDE the repository")
out.mkdir(parents=True, exist_ok=True)

probes = pathlib.Path(sys.argv[3]).resolve()
app = repo / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications"
host = repo / "tests/host"
hal = repo / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/HAL_Drivers/drivers"
GCC = os.environ.get("GCC", "gcc")

adapt = {
    "source_of_probes": str(probes),
    "adaptation_1_seam_anchor": (
        "reproduce.py anchors the diag seam on '    if (motor_diag_gate_check() != "
        "RT_EOK) return -RT_EPERM;\\n', which the R1a fix removed. The hook is now "
        "inserted before 'sp_reserve(SP_DIAG, out);' inside the mot_lock hold - the "
        "same semantic point (final qualification passed, reservation not yet taken)"),
    "adaptation_2_compiler_driver": (
        "reproduce.py passes -pthread and -fsanitize=undefined,float-cast-overflow. "
        "This machine has only MinGW-w64 GCC 4.9.2 (tdm64): -pthread is not accepted "
        "by the driver and libubsan/float-cast-overflow are unavailable, so the UBSan "
        "arm of the overflow probe is NOT reproduced here and the sanitizer evidence "
        "stays attributed to the cloud run"),
    "adaptation_3_threads_flag": "pthread link uses -mthreads on this driver",
    "adaptation_4_calcalc_key": (
        "probe_calcalc.py reads the holdout row key 'combined_error_bound_mA'. R3b removed "
        "that RSS'd quantity and renamed the disclosed interval bound to "
        "'worst_case_bound_mA', so a copy of the probe with only that key name updated is "
        "used. Input documents are produced by the probe unchanged."),
    "adaptation_5_line_endings": (
        "the review's byte anchors use lone LF because the cloud reads Git blobs; this "
        "Windows working tree is CRLF (core.autocrlf=true). The seam copies are built with "
        "endings normalised back to LF, so each seam file equals the reviewed blob form "
        "plus the injected host-only hook."),
}
(out / "adaptations.json").write_text(json.dumps(adapt, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
print(json.dumps(adapt, indent=2, ensure_ascii=False))


def run(name, args, timeout=60):
    r = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
    (out / (name + ".stdout.txt")).write_bytes(r.stdout)
    (out / (name + ".stderr.txt")).write_bytes(r.stderr)
    print("== %s rc=%d" % (name, r.returncode))
    sys.stdout.flush()
    return r.returncode


# ---------------------------------------------------------------- probe_calcalc.py
# adaptation_4: the review's probe reads holdout row key 'combined_error_bound_mA'.
# R3b replaced the RSS'd "combined bound" with an explicit interval bound and renamed
# that row key to 'worst_case_bound_mA', so the probe is copied next to this runner and
# only that key name is updated.  Every input document stays byte-identical.
pc = out / "probe_calcalc_adapted.py"
src = (probes / "probe_calcalc.py").read_text(encoding="utf-8")
assert src.count("combined_error_bound_mA") == 1, "calcalc key anchor moved again"
pc.write_text(src.replace("combined_error_bound_mA", "worst_case_bound_mA"), encoding="utf-8")
rc = run("calcalc", [sys.executable, pc, repo, out / "calcalc_inputs"])
print("calcalc rc=%d (0 = the experiment ran to completion, not a project PASS)" % rc)

# ---------------------------------------------------------------- probe_cc (no UBSan)
cc = [GCC, "-std=gnu11", "-Wno-unused-function", "-DPS_LEAF_SCRIPTED_TIME",
      "-Drt_kprintf=cc_kprintf", "-I", host, "-I", host / "shim_cc", "-I", app,
      probes / "probe_cc.c", "-o", out / "probe_cc.exe"]
if run("cc_build", cc) == 0:
    run("cfg_mutex", [out / "probe_cc.exe"])
    run("show_overflow", [out / "probe_cc.exe", "overflow"])
else:
    print("INFRASTRUCTURE: probe_cc did not build on this toolchain (see cc_build.stderr.txt)")

# ---------------------------------------------------------------- probe_diag_threads
# adaptation_5: the review's anchors are written with lone LF because the cloud reads
# the sources as Git blobs.  This Windows working tree is CRLF (core.autocrlf=true), so
# the seam copies below are built from the CRLF text with the endings normalised back to
# LF - i.e. the seam copy is byte-for-byte the reviewed blob form plus the injected hook.
seam = out / "seam"
seam.mkdir(exist_ok=True)


def lf_bytes(p):
    return p.read_bytes().replace(b"\r\n", b"\n")


b = lf_bytes(app / "motor.c")
old = b"    e = sp_reserve(SP_DIAG, out);\n    motor_lock_give_checked();\n"
if b.count(old) != 1:
    print("SEAM ANCHOR NOT MATCHED (count=%d): refusing to report a pass" % b.count(old))
    sys.exit(4)
(seam / "motor.c").write_bytes(
    b.replace(old, b"    m7_after_diag_gate_hook(); /* host-only rendezvous, mot_lock held */\n" + old))
b = lf_bytes(host / "sp_conc_main.c")
old2 = b"        s2_contend_hits++;\n"
if b.count(old2) != 1:
    print("CONTEND ANCHOR NOT MATCHED (count=%d)" % b.count(old2))
    sys.exit(4)
(seam / "sp_conc_main.c").write_bytes(
    b.replace(old2, old2 + b"        m7_port_contend_hook(); /* host-only signal */\n"))
(seam / "rtdef.h").write_text("#include <rtthread.h>\n", encoding="utf-8")
(seam / "rthw.h").write_text("#include <rtthread.h>\nrt_base_t rt_hw_interrupt_disable(void);\n"
                             "void rt_hw_interrupt_enable(rt_base_t);\n", encoding="utf-8")

diag = [GCC, "-std=gnu11", "-mthreads", "-Wno-unused-function",
        "-DPS_GATE_HOST_OVERRIDE", "-DMOTOR_HARDWARE_ENABLE_PATH_VALIDATED=RT_TRUE",
        "-DSTEP_DIAG_ISOLATED_ENV_CONFIRMED=RT_TRUE", "-DTMC_DRIVE_CONFIG_HW_VALIDATED=RT_TRUE",
        "-I", seam, "-I", host, "-I", host / "shim_ps", "-I", app, "-I", hal,
        probes / "probe_diag_threads.c", "-o", out / "probe_diag_threads.exe"]
if run("diag_build", diag) == 0:
    run("diag_threads", [out / "probe_diag_threads.exe"], timeout=20)
else:
    print("INFRASTRUCTURE: thread probe did not build on this toolchain")
print("These are defect-observation probes, not a project PASS and not a hardware approval.")
