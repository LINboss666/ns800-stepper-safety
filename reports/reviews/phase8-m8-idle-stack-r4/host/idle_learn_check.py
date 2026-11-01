#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""idle 栈轮 (phase8-m8-idle-stack-r4) 的集中复核 —— 零设备、零重编、零五千条清单。

A 组 = idle 栈缺陷的证据链(两份 map + 两份 build_manifest + 调用图 + 上一轮板上水位)
B 组 = learn 判据矩阵(**真执行器 + 真计划步骤**: >=8 接受, 三类仍拒)
C 组 = 启动确认的栈断言(构造期望过, 旧镜像实录必须被拒)
D 组 = 元负控(把判据换成永远放行, B/C 的期望必须翻红; 人工门去掉答案后 dispatch 必须停且零运动)
E 组 = 现场包结构(载荷已换新 AXF、两路激励各自首因合同、机械缺口 NOT_PROVIDED、validate fail=0)
"""
import hashlib
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path("D:/ns800")
RD = ROOT / "reports/reviews/phase8-m8-idle-stack-r4"
FIELD = RD / "field"
FW = ROOT / "reports/firmware/phase8-m8-idle-stack-r4"
OLD_FW = ROOT / "reports/firmware/phase8-m8-diag-stack-r1"
R3FIELD = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field"
CHAIN = R3FIELD / "m8r3_serial_chain.py"
KERNEL_ROOT = "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
RTCONFIG = ROOT / KERNEL_ROOT / "rtconfig.h"
APPS = ROOT / KERNEL_ROOT / "applications"
DIAG_C = APPS / "diagnosis.c"
SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="idlecheck_", dir="D:/ns800_m8r3_scratch"))
PLAN = json.loads((FIELD / "plan_core_r4.json").read_text(encoding="utf-8"))

sys.path.insert(0, str(R3FIELD))
import m8r3_serial_chain as M  # noqa: E402


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Clock(object):
    def monotonic(self):
        return 0.0

    def sleep(self, s):
        pass


led = M.Ledger(SCRATCH / "ledger.jsonl", Clock(), out_dir=SCRATCH)
learn_step = next(s for st in PLAN["stages"] for s in st.get("steps", []) if s.get("as") == "learn")


def learn_line(adc_n, sg_n, nz=8, seq_f=100, seq_l=140):
    return ("[DIAG] learn OK adc[80..120]n%d nz%d sg[80..120]n%d seq%d..%d"
            % (adc_n, nz, sg_n, seq_f, seq_l))


def judge(step, rows):
    return M.assert_step(step, rows, "learn" if step.get("as") == "learn" else "ps", led)


CHECKS = []


def add(name, ok, detail=""):
    CHECKS.append((name, bool(ok), str(detail)))


# ---------------- A 组: idle 栈证据链 ----------------
def map_idle_size(map_path):
    txt = pathlib.Path(map_path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"idle_thread_stack\s+0x[0-9a-fA-F]+\s+Data\s+(\d+)", txt)
    return int(m.group(1)) if m else None


old_size = map_idle_size(OLD_FW / "rt-thread-motor-m1.map")
new_size = map_idle_size(FW / "rt-thread-motor-m1.map")
add("A1_old_map_idle_stack_is_256", old_size == 256, old_size)
add("A2_new_map_idle_stack_is_1024", new_size == 1024, new_size)
mfs = PLAN["identity_binding"]
bm_new = json.loads((FW / "build_manifest.json").read_text(encoding="utf-8"))
bm_old = json.loads((OLD_FW / "build_manifest.json").read_text(encoding="utf-8"))
zi = lambda d: int(re.search(r"ZI-data=(\d+)", d["targets"][0]["program_size"]).group(1))
add("A3_ZI_grew_exactly_768", zi(bm_new) - zi(bm_old) == 1024 - 256,
    "%d-%d" % (zi(bm_new), zi(bm_old)))
sm = bm_new["targets"][0]["stack_metrics"]
add("A4_callgraph_max_depth_668_exceeds_old_256",
    sm["idle_thread_entry_max_depth"] == 668 and sm["reviewer_reported_fixed_frames_sum"] == 264,
    "armlink Max Depth=%s vs 任务书固定帧合计=%s" % (sm["idle_thread_entry_max_depth"],
                                                     sm["reviewer_reported_fixed_frames_sum"]))
add("A5_board_measured_idle_watermark_recorded_separately",
    sm["board_measured_idle_watermark_bytes_prev_round"] == 163,
    "静态界 668 与实测峰值 163 并列登记, 不互相替换")
head = subprocess.run(["git", "show", "HEAD:%s" % RTCONFIG.relative_to(ROOT).as_posix()],
                      cwd=ROOT, capture_output=True)
diff = [(a, b) for a, b in zip(head.stdout.decode("utf-8").splitlines(),
                               RTCONFIG.read_text(encoding="utf-8").splitlines()) if a != b]
add("A6_rtconfig_only_the_idle_line_changed",
    len(diff) == 1 and "IDLE_THREAD_STACK_SIZE" in diff[0][0] and "1024" in diff[0][1], diff)
add("A7_diag_4096_source_untouched",
    sha(DIAG_C) == "e1696ec92721c7df9da074c465ac3b705ad451029aa3608b1695c93c7466e557",
    sha(DIAG_C)[:16])
add("A8_accepted_image_not_overwritten",
    sha(OLD_FW / "rt-thread-motor-m1-candidate.bin").startswith("f5c2fd89") and
    sha(FW / "rt-thread-motor-m1-candidate.bin").startswith("dab356c6"))
reg = {}
for ln in (OLD_FW / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
    if ln.strip() and not ln.startswith("#"):
        h, _s, rel = ln.partition("  ")
        reg[rel.strip()] = h.strip()
add("A9_registered_hashes_agree_with_disk",
    all(reg.get(p.relative_to(ROOT).as_posix()) == sha(p)
        for p in (OLD_FW / "rt-thread-motor-m1-candidate.bin", OLD_FW / "rt-thread-motor-m1.axf",
                  OLD_FW / "rt-thread-motor-m1.map")))
txt = (FW / "rt-thread-motor-m1.map").read_text(encoding="utf-8", errors="replace")
m_head = re.search(r"Execution Region RW_RT_HEAD \(Exec base: 0x20012000,[^)]*Size: 0x([0-9a-f]+), "
                   r"Max: 0x([0-9a-f]+)", txt)
m_dtcm = re.search(r"Execution Region RW_DTCM \(Exec base: 0x20000400, Load base: [^,]+, Size: 0x([0-9a-f]+), "
                   r"Max: 0x([0-9a-f]+)", txt)
add("A10_heap_and_dram_bounds_intact",
    bool(m_head) and m_head.group(1) == m_head.group(2) == "00008000" and bool(m_dtcm)
    and int(m_dtcm.group(1), 16) < int(m_dtcm.group(2), 16),
    "heap=%s DTCM used/max=%s/%s" % (m_head.groups() if m_head else None,
                                     m_dtcm.group(1) if m_dtcm else None,
                                     m_dtcm.group(2) if m_dtcm else None))
log = pathlib.Path(bm_new["targets"][0]["log_archive_path"]).read_text(encoding="utf-8", errors="replace")
add("A11_build_zero_error_zero_warning",
    "0 Error(s), 0 Warning(s)" in log and bm_new["uv4_return_code"] == 0)

# ---------------- B 组: learn 判据矩阵 ----------------
good = {"verdict": "OK"}
for adc_n, sg_n, want_ok, label in ((8, 8, True, "8/8"), (9, 8, True, "9/8"), (8, 12, True, "8/12"),
                                    (32, 32, True, "32/32 上限不设"),
                                    (7, 9, False, "7/9"), (8, 7, False, "8/7"), (0, 8, False, "0/8")):
    r = judge(learn_step, [learn_line(adc_n, sg_n)])
    okp = r.get("verdict") == "OK"
    add("B_learn_accepts_%s" % label if want_ok else "B_learn_rejects_%s" % label, okp == want_ok,
        "%s -> %s" % (label, json.dumps({k: r.get(k) for k in ("verdict", "code")}, ensure_ascii=False)))
    if not want_ok:
        add("B_reject_reason_is_below_min_%s" % label, r.get("code") == "FIELD_VALUE_BELOW_MIN", r.get("code"))
dup = [learn_line(8, 8), learn_line(8, 8)]
r = judge(learn_step, dup)
add("B_rejects_duplicate_success_line", r.get("verdict") == "STOP" and
    r.get("code") == "FIELD_NOT_EXACTLY_ONE", r.get("code"))
r = judge(learn_step, ["msh >"])
add("B_rejects_no_response_prompt_only",
    r.get("verdict") == "STOP" and r.get("code") == "EXPECTATION_NOT_MET",
    "只有提示符、没有成功行 => 具名停止(期待行缺失), 不是非空即过")
r = judge(learn_step, [])
add("B_rejects_empty_response", r.get("verdict") == "STOP" and
    r.get("code") == "EXPECTATION_NOT_MET", r.get("code"))
r = judge(learn_step, ["[DIAG] learn FAILED: no fresh sample"])
add("B_business_failure_layer_still_stops", r.get("verdict") == "STOP" and
    r.get("code") != "FIELD_VALUE_BELOW_MIN", json.dumps({k: r.get(k) for k in ("verdict", "code")}))

# ---------------- C 组: 启动确认的栈断言 ----------------
ps_step = next(s for st in PLAN["stages"] for s in st.get("steps", []) if s.get("as") == "ps_after_boot")
rows_doc = json.loads((FIELD / "rows.json").read_text(encoding="utf-8"))
healthy_ps = rows_doc["commands"]["ps"]["healthy"]
real_old_ps = rows_doc["commands"]["ps"]["before_fix_real"]
add("C1_ps_fixture_lines_contain_both_threads",
    any(l.startswith("diag ") for l in healthy_ps) and any(l.startswith("tidle0") for l in healthy_ps))
r = judge(ps_step, healthy_ps)
add("C2_new_image_expectation_passes", r.get("verdict") == "OK", r.get("code"))
r = judge(ps_step, real_old_ps)
add("C3_old_image_real_capture_rejected",
    r.get("verdict") == "STOP" and r.get("code") == "FIELD_VALUE_MISMATCH",
    "旧镜像 idle=0x100 的板上实录必须被新包拒 => 断言真的在看栈字段")
regressed = [l.replace("0x00001000", "0x00000400") if l.startswith("diag ") else l for l in healthy_ps]
r = judge(ps_step, regressed)
add("C4_diag_stack_regression_to_1024_rejected", r.get("verdict") == "STOP", r.get("code"))

# ---------------- D 组: 元负控 ----------------
src = CHAIN.read_text(encoding="utf-8")


def mutant(old, new, tag):
    assert src.count(old) == 1, "突变锚点命中 %d 次: %s" % (src.count(old), old[:40])
    p = SCRATCH / ("m8r3_%s.py" % tag)
    p.write_text(src.replace(old, new), encoding="utf-8", newline="\n")
    return load_module(p, "m8r3_" + tag)


m1 = mutant("if int(str(g[k])) < int(v):", "if False:", "no_min")
r1 = judge_step_m1 = m1.assert_step(dict(learn_step, ), [learn_line(7, 9)], "learn", led)
add("D1_metaneg_removing_the_floor_lets_7_9_through", r1.get("verdict") == "OK",
    "去掉下限判据后 7/9 被接受 => B 的拒绝靠真判据")
m2 = mutant("        if len(hits) != 1:\n            led.ev(\"VALUE_ASSERT_FAIL\"",
            "        if len(hits) < 1:\n            led.ev(\"VALUE_ASSERT_FAIL\"", "no_unique")
r2 = m2.assert_step(dict(learn_step), dup, "learn", led)
add("D2_metaneg_removing_row_uniqueness_lets_duplicates_through", r2.get("verdict") == "OK",
    "去掉唯一性后重复成功行被接受 => B 的重复行拒绝靠真判据")
equal_only = json.loads(json.dumps(learn_step))
equal_only["expect_values"][0] = {"row": equal_only["expect_values"][0]["row"],
                                  "must": {"adc_n": "8", "sg_n": "8"}}
r3 = judge(equal_only, [learn_line(9, 8)])
add("D3_metaneg_exact_8_would_still_reject_9", r3.get("verdict") == "STOP" and
    r3.get("code") == "FIELD_VALUE_MISMATCH",
    "把判据换回等值 must{8,8} 后 9/8 又被拒 => 证明'恰好 8'确实被删掉了, 不是读错文件")
m4 = mutant("    sources = list(Path(app_dir).glob(\"*.c\")) + _kernel_builtin_cmd_sources(app_dir)",
            "    sources = list(Path(app_dir).glob(\"*.c\"))", "no_builtin")
lines4, bad4, _ = m4.validate_plan(PLAN, rows_doc, str(APPS))
add("D4_metaneg_dropping_kernel_builtin_scan_breaks_ps",
    any("|FAIL|ps" in l and "PLAN_COMMAND_EXISTS" in l for l in lines4),
    "去掉内置命令源后 ps 又变成'发明命令' => ps/free 的通过靠那条真实存在的导出")
lines5, bad5, _ = M.validate_plan(PLAN, rows_doc, str(APPS))
fake = json.loads(json.dumps(PLAN))
fake["stages"][4]["steps"][1]["cmd"] = "ps_not_a_command"
fake["stages"][4]["steps"][1]["key"] = "ps"
lines6, bad6, _ = M.validate_plan(fake, rows_doc, str(APPS))
add("D5_invented_command_still_rejected",
    any("|FAIL|ps_not_a_command" in l for l in lines6) and bad5 == 0,
    "validate_plan(未突变) fail=%d; 发明命令 fail=%d" % (bad5, bad6))
nogate = json.loads(json.dumps(PLAN))
for st in nogate["stages"]:
    if st.get("id") in ("S8", "S8b"):
        st["manual_answers"] = {}
gd = SCRATCH / "nogate_field"          # rows_file 是相对计划目录的, 所以夹具要一起放
gd.mkdir(parents=True, exist_ok=True)
for f in ("rows.json", "needles.json"):
    shutil.copyfile(str(FIELD / f), str(gd / f))
gp = gd / "plan_nogate_answers.json"
gp.write_text(json.dumps(nogate, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
out = SCRATCH / "dispatch_nogate"
p = subprocess.run([sys.executable, str(CHAIN), "--dispatch", "--transport", "stub", "--backend", "sim",
                    "--profile", "healthy", "--plan", str(gp), "--repo-root", str(ROOT), "--out", str(out)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
tail = (p.stdout or "") + (p.stderr or "")
mm = re.search(r"DISPATCH_SUMMARY\|status=(\w+)\|", tail)
used = re.search(r"quota_used=\{([^}]*)\}", tail)
motor = re.search(r'"motor_run": (\d+)', used.group(1)) if used else None
add("D6_manual_gate_without_answer_stops_before_motion",
    bool(mm) and mm.group(1) == "STOP" and "MANUAL_GATE_MISSING_STUB_ANSWER" in tail and
    bool(motor) and int(motor.group(1)) <= 1,
    "status=%s motor_run_used=%s" % (mm and mm.group(1), motor and motor.group(1)))

# ---------------- E 组: 现场包结构 ----------------
swap = PLAN["download_payload_swap"]
dl_exec = json.dumps([PLAN["tools"]["download"].get("command"), PLAN["tools"]["download"].get("argv")])
add("E1_download_payload_is_the_new_axf",
    swap["swaps"] >= 1 and (FW / "rt-thread-motor-m1.axf").relative_to(ROOT).as_posix() in dl_exec
    and swap["from"] not in dl_exec,
    "只看可执行字段(command/argv), 不看散文: 上一轮的教训是扫整份对象会被自己的说明文字打出假报警")
add("E2_identity_binding_uses_executor_schema",
    set(("image", "executor", "software_input_commit", "tested_image_build_commit"))
    <= set(PLAN["identity_binding"].keys()) and
    PLAN["identity_binding"]["image"]["sha256"] == bm_new["targets"][0]["images"]
    ["rt-thread-motor-m1-candidate.bin"]["sha256"])
two = [s for s in PLAN["stages"] if s.get("scenario")]
add("E3_two_independent_excitation_sessions",
    len(two) == 2 and {t["scenario"] for t in two} == {"SG_ADC_controlled_load", "IMU_non_destructive_excitation"}
    and all("first_cause_contract" in t for t in two))
add("E4_no_software_trip_substitute",
    all("safety_trip" in json.dumps(t["first_cause_contract"], ensure_ascii=False) for t in two) and
    "safety_trip" not in json.dumps([s.get("cmd") for t in two for s in t["steps"]]))
add("E5_boot_confirm_asserts_both_stack_sizes",
    len(ps_step["expect_values"]) == 2 and
    sorted(sorted(v["must"].keys()) for v in ps_step["expect_values"])
    == [["diag_stack"], ["idle_stack"]])
add("E6_mechanical_gaps_marked_not_provided",
    json.dumps(PLAN).count("NOT_PROVIDED") >= 5 and len(PLAN["not_provided_summary"]) == 5,
    PLAN["not_provided_summary"][0]["item"])
add("E7_validate_plan_clean", bad5 == 0 and len(lines5) == 207, "checks=%d fail=%d" % (len(lines5), bad5))
add("E8_quota_declares_no_retry_and_no_blackbox_clear",
    PLAN["quota"]["retry"] == 0 and PLAN["quota"].get("blackbox_clear", 0) == 0 and
    PLAN["quota"]["download"] == 1 and PLAN["quota"]["readback"] == 1)
def dispatch(plan_obj, tag):
    """把包放进仓外临时目录跑 stub+sim 干跑(rows_file 是相对计划目录解析的)。"""
    d = SCRATCH / (tag + "_field")
    d.mkdir(parents=True, exist_ok=True)
    for f in ("rows.json", "needles.json"):
        shutil.copyfile(str(FIELD / f), str(d / f))
    pp = d / "plan.json"
    pp.write_text(json.dumps(plan_obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, str(CHAIN), "--dispatch", "--transport", "stub", "--backend", "sim",
                        "--profile", "healthy", "--plan", str(pp), "--repo-root", str(ROOT),
                        "--out", str(SCRATCH / ("dispatch_" + tag))],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    txt = (r.stdout or "") + (r.stderr or "")
    (FIELD / ("dispatch_stub_%s.txt" % tag)).write_text(txt, encoding="utf-8", newline="\n")
    return txt


htxt = dispatch(json.loads(json.dumps(PLAN)), "healthy")
mleft = re.search(r"quota_left=\{([^}]*)\}", htxt)
add("E9_stub_dispatch_end_to_end_ok",
    "DISPATCH_SUMMARY|status=OK" in htxt and "stages_not_run=0" in htxt and
    bool(mleft) and '"motor_run": 0' in mleft.group(1) and '"download": 0' in mleft.group(1),
    "整包在 stub+sim 下走完全部有步骤的阶段且配额逐条扣完; 原文存 field/dispatch_stub_healthy.txt")
add("E10_dry_run_touched_no_device", "actions_attempted=[]" in htxt,
    "干跑里 actions_attempted 为空: 没有一次真实工具调用、没有开口、没有起转(见同文件)")

problems = [n for n, ok, _ in CHECKS if not ok]
n_metaneg = sum(1 for n, _, _ in CHECKS if n.startswith("D"))
lines = ["IDLE_LEARN_CHECK mode=offline_only (零设备: 不起 pyocd, 不开串口, 不动 Keil; dispatch 走 stub+sim)",
         "run_dir(LOCAL_ONLY, 仓外)=%s" % SCRATCH.as_posix()]
lines += [("OK " if ok else "PROBLEM ") + n + ("" if ok else " | " + dt) for n, ok, dt in CHECKS]
lines.append("CHECKS=%d problems=%d" % (len(CHECKS), len(problems)))
lines.append("META_NEGATIVES=%d fired=%d" % (n_metaneg, n_metaneg - len([p for p in problems if p.startswith("D")])))
lines.append("IDLE_LEARN_CHECK %s" % ("PROBLEMS " + json.dumps(problems, ensure_ascii=False) if problems
                                      else "ALL_PASS_WITH_META_NEGATIVES"))
txt = "\n".join(lines) + "\n"
(RD / "host" / "idle_learn_console.txt").write_text(txt, encoding="utf-8", newline="\n")
(RD / "host" / "idle_learn_check.json").write_text(json.dumps(
    {"checks": [{"name": n, "ok": ok, "detail": dt} for n, ok, dt in CHECKS], "problems": problems},
    ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
print(txt.encode("utf-8", "replace").decode("utf-8"))
sys.exit(0 if not problems else 1)
