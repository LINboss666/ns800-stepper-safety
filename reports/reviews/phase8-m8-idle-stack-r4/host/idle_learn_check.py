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
prev = subprocess.run(["git", "show", "48204fe:%s" % RTCONFIG.relative_to(ROOT).as_posix()],
                      cwd=ROOT, capture_output=True)
norm = lambda b: b.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()
wt = norm(RTCONFIG.read_bytes())
drift = [(a, b) for a, b in zip(norm(head.stdout), wt) if a != b]
diff = [(a, b) for a, b in zip(norm(prev.stdout), wt) if a != b]
add("A6_rtconfig_only_the_idle_line_changed",
    len(drift) == 0 and len(wt) == len(norm(head.stdout)) and len(diff) == 1 and
    b"IDLE_THREAD_STACK_SIZE 256" in prev.stdout and
    "#define IDLE_THREAD_STACK_SIZE 1024" in "\n".join(wt),
    "工作树与 HEAD 的 rtconfig 逐行相同(只差行尾); 相对构建时 HEAD(48204fe) 的树恰好差 1 行 = idle 256->1024; "
    "旧写法是拿未跟踪时 git show 返回空来凑出 1 行差, 那是巧合不是检查")
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
# 机械前置在本轮会把运动阶段整条挡掉, 那会**掩盖**人工门缺答案这个判据; 所以这一例把会挡起转的
# 前置设成 PROVIDED, 只留"人工门没有答案"这一个缺陷 —— 判据要单独可判, 不能靠别的门替它挡。
for k, v in nogate["mechanical_prerequisites"].items():
    if v.get("blocks_motion"):
        v["status"] = "PROVIDED"
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
    and swap.get("payload_axf") == PLAN["tools"]["download"]["image"]["path"] and
    "relative-protection-r3" not in dl_exec,
    "只看可执行字段(command/argv), 不看散文: 上一轮的教训是扫整份对象会被自己的说明文字打出假报警")
add("E2_identity_binding_uses_executor_schema",
    set(("image", "executor", "software_input_commit", "tested_image_build_commit"))
    <= set(PLAN["identity_binding"].keys()) and
    PLAN["identity_binding"]["image"]["sha256"] == bm_new["targets"][0]["images"]
    ["rt-thread-motor-m1-candidate.bin"]["sha256"])
two = [s for s in PLAN["stages"] if s.get("scenario")]
add("E3_two_independent_excitation_sessions",
    len(two) == 8 and {t["scenario"] for t in two} ==
    {"SG_ADC_controlled_load", "IMU_non_destructive_excitation"} and
    all("first_cause_contract" in t for t in two if t["id"] in ("S8", "S8b")) and
    all("first_cause_legs" in t for t in two if t["id"] in ("S8", "S9", "S8b", "S9b")),
    "两条链各 4 个阶段带 scenario; 两场运动会话有首因合同, 四个取证/运动阶段有可执行四腿")
add("E4_no_software_trip_substitute",
    all("safety_trip" in json.dumps(t["first_cause_contract"], ensure_ascii=False)
        for t in two if t["id"] in ("S8", "S8b")) and
    "safety_trip" not in json.dumps([s.get("cmd") for t in two for s in t["steps"]]),
    "两场运动会话的首因合同都写明禁止用 safety_trip 代替真实越界, 且没有任何步骤发 safety_trip")
add("E5_boot_confirm_asserts_both_stack_sizes",
    len(ps_step["expect_values"]) == 2 and
    sorted(sorted(v["must"].keys()) for v in ps_step["expect_values"])
    == [["diag_stack"], ["idle_stack"]])
add("E6_mechanical_gaps_marked_not_provided",
    json.dumps(PLAN).count("NOT_PROVIDED") >= 5 and len(PLAN["not_provided_summary"]) == 7 and
    any("装配" in x["item"] and x["status"].startswith("PROVIDED") and x.get("still_open")
        for x in PLAN["not_provided_summary"]) and
    any("SG/ADC" in x["item"] and x["status"] == "NOT_PROVIDED"
        for x in PLAN["not_provided_summary"]),
    PLAN["not_provided_summary"][0]["item"])
add("E7_validate_plan_clean", bad5 == 0 and len(lines5) > 200, "checks=%d fail=%d" % (len(lines5), bad5))
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
left_map = dict(re.findall(r'"(\w+)": (\d+)', mleft.group(1))) if mleft else {}
add("E9_stub_dispatch_end_to_end_ok",
    "DISPATCH_SUMMARY|status=OK" in htxt and "stages_not_run=0" in htxt and
    left_map.get("port_open") == str(int(PLAN["quota"]["port_open"]) - 8) and
    left_map.get("motor_run") == str(int(PLAN["quota"]["motor_run"]) - 2) and
    "PREREQUISITE_REFUSAL|scenario=SG_ADC_controlled_load|refused_before=S8|gates=load_means|"
    "skipped_stages=S8,S9,S10,S11|" in htxt and
    "PREREQUISITE_REFUSAL|scenario=IMU_non_destructive_excitation" not in htxt and
    "FLOW_VS_VERIFICATION|flow_complete=True|core_protection_verified=False" in htxt,
    "交付态(装配已确认 + 仍缺加载)整包干跑: 只有 SG_ADC 链在开端口前被拒 => 实际用 8 次开口 / "
    "2 次起转 / 3 组清理, 流程判定与通道判定分开报(SG/ADC 那场没跑, core 必为 False); "
    "原文存 field/dispatch_stub_healthy.txt")
add("E10_dry_run_touched_no_device", "actions_attempted=[]" in htxt,
    "干跑里 actions_attempted 为空: 没有一次真实工具调用、没有开口、没有起转(见同文件)")
add("E11_skipped_chain_has_no_session_at_all",
    all("stage=%s|" % sid not in htxt for sid in ("S8", "S9", "S10", "S11")) and
    all("stage=%s|" % sid in htxt for sid in
        ("S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8b", "S9b", "S10b", "S11b")),
    "被机械前置门拒掉的 SG 链在汇总里一条阶段行都没有(不是执行了但失败); "
    "学习/提交/重启恢复与 IMU 那四阶段照常出现在序列里")
add("E12_refusal_lines_name_their_own_chain",
    "PREREQUISITE_REFUSAL|scenario=SG_ADC_controlled_load|refused_before=S8|gates=load_means|"
    "skipped_stages=S8,S9,S10,S11|via_requires=-|" in htxt,
    "拒绝行只列自己作用域被跳过的阶段(SG 链四阶段), 且 gates 只具名真正缺的那一项(load_means): "
    "装配已确认后不再把 assembly_readiness 也算成缺项")
_fs = PLAN.get("field_sequence_before_start") or {}
_m_exec = [s.split("|")[1].split("=")[1] for s in htxt.splitlines() if s.startswith("DISPATCH|stage=")]
_m_sk = re.search(r"prerequisite_skips=\[(.*?)\]", htxt)
_m_skip = [x.strip().strip('"') for x in _m_sk.group(1).split(",")] if _m_sk else []
add("E13_recorded_sequence_matches_real_run",
    _fs.get("expected_executed_stages") == _m_exec and
    _fs.get("expected_skipped_stages") == _m_skip and
    left_map.get("port_open") == str(int(PLAN["quota"]["port_open"]) -
                                     _fs["expected_usage"]["port_open"]) and
    left_map.get("motor_run") == str(int(PLAN["quota"]["motor_run"]) -
                                     _fs["expected_usage"]["motor_run"]) and
    len(_m_exec) == 12,
    "启动前记录的实际计划序列与真实干跑逐条一致: 执行 %s / 跳过 %s / 用量 port_open=%s motor_run=%s"
    % (_m_exec, _m_skip, _fs.get("expected_usage", {}).get("port_open"),
       _fs.get("expected_usage", {}).get("motor_run")))
_gap = json.loads(json.dumps(PLAN))
for k, v in _gap["mechanical_prerequisites"].items():
    if k == "assembly_readiness":
        v["status"] = "NOT_CONFIRMED"
atxt = dispatch(_gap, "assembly_not_confirmed")
add("E14_assembly_rollback_scenario_still_blocks_every_spinup",
    bool(re.search(r"prerequisite_skips=\[\"S4\", \"S5\", \"S6\", \"S7\", \"S8\"", atxt)) and
    "|via_requires=S4|" in atxt and
    all("stage=%s|" % sid not in atxt for sid in
        ("S4", "S5", "S7", "S8", "S8b", "S9b", "S10", "S10b")) and
    "quota_used={\"diag_profile_zero\": 1, \"download\": 1, \"explicit_reset\": 1, "
    "\"irq_polarity_prep\": 2, \"port_open\": 1" in atxt.replace("'", '"') and
    "core_protection_verified=False" in atxt,
    "装配任何一项回退成未确认时, 起转仍被逐个挡下(S4/S8/S8b/S10/S10b + 沿 requires 的 S5/S6/S7), "
    "port_open 降到 1、零次起转 —— 门没被拆, 只是当前状态放行(原文存 "
    "field/dispatch_stub_assembly_not_confirmed.txt)")

# ---------------- F 组: ① 镜像身份统一(S0 / 下载 / 回读 同源) ----------------
R3_BIN_OLD = "00608ec93c6a3027d68b44b5317574209fccfb0ba82a9974793d13b54b79e304"
img, axf = PLAN["identity_binding"]["image"], PLAN["identity_binding"]["image"]["axf"]
rb, dl = PLAN["tools"]["readback"]["image"], PLAN["tools"]["download"]["image"]
add("F1_readback_bound_to_this_image",
    rb["sha256"] == img["sha256"] and rb["bytes"] == img["bytes"] and rb["path"] == img["path"],
    "回读目标 = 本轮 BIN %s…(%d B), 与 S0 的 identity_binding.image 逐字段相同" % (img["sha256"][:8], img["bytes"]))
add("F2_download_bound_to_this_axf",
    dl["sha256"] == axf["sha256"] and dl["bytes"] == axf["bytes"] and dl["path"] == axf["path"] and
    PLAN["tools"]["download"]["argv"][-1] == axf["path"],
    "下载载荷 = 本轮 AXF %s…, 且 argv 最后一个参数就是该路径" % axf["sha256"][:8])
EXEC_SCOPE = {"tools": {k: {kk: vv for kk, vv in (v or {}).items()
                            if kk in ("argv", "command", "image", "out_file", "start_address")}
                        for k, v in PLAN["tools"].items()},
              "stages": [s.get("steps") for s in PLAN["stages"]]}
add("F3_no_stale_binding_in_executable_fields",
    "00608ec9" not in json.dumps(EXEC_SCOPE, ensure_ascii=False) and
    "relative-protection-r3/rt-thread-motor-m1" not in json.dumps(EXEC_SCOPE, ensure_ascii=False),
    "只扫会被执行字段(tools 的 argv/command/image/out_file + 每步的 cmd/key): 旧 00608ec9 绑定已全部移除; "
    "散文里作为历史说明提到它是允许的(扫全文会被自己的文字打出假报警)")
add("F4_readback_not_board_image_before",
    rb["sha256"] != PLAN["identity_binding"]["board_image_before"]["sha256"],
    "回读目标不等于上一轮在板镜像(那只是 board_image_before 的登记事实)")
ic = M.identity_consistency(PLAN)
add("F5_executor_gate_agrees", ic == [], "validate_plan 的同一函数离线判定: %s" % (ic or "一致"))


def stale_copy():
    p = json.loads(json.dumps(PLAN))
    p["tools"]["readback"]["image"] = {"path": "reports/firmware/phase8-m8-relative-protection-r3/"
                                               "rt-thread-motor-m1-candidate.bin",
                                       "bytes": 290916, "sha256": R3_BIN_OLD}
    return p


add("F6_meta_negative_gate_fires", bool(M.identity_consistency(stale_copy())),
    "元负控: 把回读绑回旧 00608ec9… 时 identity_consistency 必须报出 %s" %
    M.identity_consistency(stale_copy())[:1])
mb = M.ManualActionBackend(led, SCRATCH / "f6", Clock(), 5)
nd = SCRATCH / "f6" / "action_readback"
nd.mkdir(parents=True, exist_ok=True)
(nd / "rc.txt").write_text("0", encoding="utf-8")
(nd / "readback.bin").write_bytes((FW / "rt-thread-motor-m1-candidate.bin").read_bytes())
act_stale = {"as": "readback", "action": "readback", "command": "x", "run_root": str(SCRATCH / "f6"),
             "out_file": "readback.bin", "image": stale_copy()["tools"]["readback"]["image"]}
try:
    mb.readback(act_stale, {})
    code6 = "NO_STOP"
except M.Stop as e:
    code6 = e.code
act_good = json.loads(json.dumps(act_stale))
act_good["image"] = PLAN["tools"]["readback"]["image"]
good = mb.readback(act_good, {}).get("identity_gate")
add("F7_real_readback_function_reproduces_then_accepts",
    code6 == "READBACK_HASH_MISMATCH" and good == "REAL_HASH_EQUAL",
    "复审的复现路径: 用**本轮新 BIN 的原字节**跑现有回读判据, 绑旧镜像时 %s, 绑新镜像时 %s" % (code6, good))

# ---------------- G 组: ② 两场顺序与分场基线 ----------------
ids_order = [s["id"] for s in PLAN["stages"]]
want_order = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7",
              "S8", "S9", "S10", "S11", "S8b", "S9b", "S10b", "S11b"]
add("G1_stage_order_two_full_chains", ids_order == want_order,
    "执行器按 stages 数组迭代, 所以数组顺序就是执行顺序: %s" % ",".join(ids_order))
byid = {s["id"]: s for s in PLAN["stages"]}
add("G2_scenario_scopes_declared",
    all(byid[x].get("scenario") == "SG_ADC_controlled_load" for x in ("S8", "S9", "S10", "S11")) and
    all(byid[x].get("scenario") == "IMU_non_destructive_excitation" for x in ("S8b", "S9b", "S10b", "S11b")),
    "两条链各自的 scenario 已标(分支与基线的作用域)")
rec_keys = [k for spec in byid["S8"]["record_from"] for k in spec["as"]]
rec_keys_b = [k for spec in byid["S8b"]["record_from"] for k in spec["as"]]
add("G3_incident_baseline_namespaced",
    rec_keys == ["bb_session_sg", "bb_written_sg", "flash_used_before_sg", "flash_capacity_sg"] and
    rec_keys_b == ["bb_session_imu", "bb_written_imu", "flash_used_before_imu", "flash_capacity_imu"],
    "事故基线按场存: A=%s B=%s" % (rec_keys, rec_keys_b))
add("G4_consumers_use_same_suffix",
    byid["S9"]["compare_recorded_gt"][0]["keys"] == ["bb_session_sg", "bb_written_sg"] and
    byid["S9"]["require_frames"][0]["session_key"] == "bb_session_sg" and
    byid["S9"]["steps"][-1]["new_log_window"]["before_key"] == "flash_used_before_sg" and
    byid["S9b"]["require_frames"][0]["session_key"] == "bb_session_imu",
    "取证会话的比较对象/新日志窗口基线都指向**本场**的键(不共用同一本基线)")
add("G5_profile_leg_step_present",
    any(s.get("as") == "prof_at_arm" for s in byid["S8"]["steps"]) and
    any(s.get("as") == "prof_at_arm" for s in byid["S8b"]["steps"]),
    "两场运动会话都在自己会话里读一次 diag_profile show(首因的 profile 腿)")
gtxt = subprocess.run([sys.executable, str(CHAIN), "--validate-plan", "--plan", str(FIELD / "plan_core_r4.json")],
                      capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
add("G6_validate_chain_lines_pass",
    "PLAN_SCENARIO_CHAIN|all|PASS" in gtxt and "PLAN_IMAGE_IDENTITY_CONSISTENCY|all|PASS" in gtxt and
    "PLAN_PREREQUISITE_GATE|all|PASS" in gtxt and "PLAN_FIRST_CAUSE_LEGS|all|PASS" in gtxt and
    "PLAN_VALIDATE_SUMMARY|checks=" in gtxt and "|fail=0|" in gtxt,
    "执行器自己的离线门: 身份/链序/前置门/首因腿全 PASS 且 fail=0")


def bad_order_plan():
    p = json.loads(json.dumps(PLAN))
    idx = [i for i, s in enumerate(p["stages"]) if s["id"] == "S9"]
    i9 = idx[0]
    s9 = p["stages"].pop(i9)
    p["stages"].insert(next(i for i, s in enumerate(p["stages"]) if s["id"] == "S8"), s9)
    return p


gbad = subprocess.run([sys.executable, str(CHAIN), "--validate-plan", "--plan", "-"],
                      input=json.dumps(bad_order_plan()), capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
add("G7_meta_negative_chain_order", "PLAN_SCENARIO_CHAIN|all|FAIL" in gbad.stdout or
    bool(M.validate_plan(bad_order_plan(), json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig")),
                         str(APPS))[1]),
    "元负控: 把 S9 搬到它的分支决定点 S8 之前, 链序门必须 FAIL")

# ---------------- H 组: ③ 额度按真实路径重算 ----------------
derived = M.derive_quota_needs(PLAN)
add("H1_quota_equals_derived", all(int(PLAN["quota"][k]) >= v for k, v in derived.items()),
    "推导=%s 申报=%s" % (json.dumps(derived, sort_keys=True),
                         json.dumps({k: PLAN["quota"][k] for k in derived}, sort_keys=True)))
add("H2_twelve_openings_five_cleanup",
    derived["port_open"] == 12 and derived["cleanup_motor_stop"] == 5 and
    derived["cleanup_motor_disarm"] == 5 and derived["cleanup_motor_status"] == 5 and
    derived["motor_run"] == 3 and PLAN["quota"]["port_open"] >= 12 and
    PLAN["quota"]["cleanup_motor_stop"] >= 5,
    "两场均触发的真实路径: 12 次开口 + 5 组必要清理(S4/S8/S8b 起转, S10/S10b 锁存态再 arm)")
add("H3_no_over_declared_reset", derived["explicit_reset"] == 1 and PLAN["quota"]["explicit_reset"] == 1,
    "显式复位真实只有 S3 一处(上一版申报 2 是多报, 本轮按路径改回 1)")
qbad = json.loads(json.dumps(PLAN))
qbad["quota"]["port_open"] = 10
qbad["quota"]["cleanup_motor_stop"] = 3
_lines, _bad, _n = M.validate_plan(qbad, json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig")),
                                   str(APPS))
add("H4_meta_negative_quota", any("PLAN_QUOTA_NEED|port_open|FAIL" in l for l in _lines) and
    any("PLAN_QUOTA_NEED|cleanup_motor_stop|FAIL" in l for l in _lines) and _bad > 0,
    "元负控: 退回申报 10 开口 / 3 清理组时, PLAN_QUOTA_NEED 必须逐项报 FAIL")

# ---------------- I 组: ④ 首因四腿与不降级 ----------------
def _watch_file(with_source):
    lines = ["[DIAG] REL TRIP r=REL_SG_SEVERE id=7 rev=1 post_rc=0",
             "[DIAG] REL TRIP val=61 thr=45 ms=120 cov=0"]
    if with_source:
        lines.append("[DIAG] REL TRIP sg=1008 cur=10041 imu=0 tick=4588411 n=3")
    p = SCRATCH / ("watch_%s.txt" % ("with_source" if with_source else "no_source"))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return str(p)


def _prof_file(pid=7, prev=1):
    p = SCRATCH / ("prof_%s_%s.txt" % (pid, prev))
    p.write_text("[DIAG] profile id=%d rev=%d abi=2 flg=0005 VALID\n" % (pid, prev),
                 encoding="utf-8", newline="\n")
    return str(p)


class _L(object):
    def ev(self, *a, **kw):
        pass


REC_OK = {"id": "7", "rev": "1", "bb_session_sg": "0", "flash_used_before_sg": "300"}
for sid, want in (("S8", ["trip_row", "source_sample", "profile_identity", "incident_session"]),
                  ("S8b", ["trip_row", "source_sample", "profile_identity", "incident_session"]),
                  ("S9", ["latch_row", "source_sample", "profile_identity", "incident_session"]),
                  ("S9b", ["latch_row", "source_sample", "profile_identity", "incident_session"])):
    spec = byid[sid].get("first_cause_legs") or {}
    add("I1_legs_%s" % sid, sorted(spec.get("covers", [])) == sorted(want) and
        spec.get("on_missing") == "EVIDENCE_INCOMPLETE" and len(spec.get("legs", [])) >= 3 and
        spec.get("applies_when_branch") == "trip",
        "%s 覆盖=%s on_missing=%s 腿数=%d" % (sid, spec.get("covers"), spec.get("on_missing"),
                                            len(spec.get("legs", []))))
try:
    M.response_branch({"status": "OBSERVATION_NO_TRIGGER",
                       "results": [{"kind": "watch", "trigger_seen": ["[DIAG] REL TRIP r="]}]},
                      {"decides_response_branch": True, "id": "S8"})
    guard = "NO_STOP"
except M.Stop as e:
    guard = e.code
add("I2_trip_cannot_be_rewritten", guard == "TRIP_REWRITTEN_AS_NO_TRIGGER",
    "已观察到的触发被记成到时未触发 => 具名 %s(不是 NO_TRIGGER 也不是通过)" % guard)

res_i3 = M.first_cause_verdict(
    byid["S8"], [{"as": "trip_window", "txt": _watch_file(False)},
                 {"as": "prof_at_arm", "txt": _prof_file()}], REC_OK, "trip", _L())
add("I3_missing_leg_named", any(x.startswith("source_sample") for x in res_i3) and
    len(res_i3) == 1, "缺源样本行 => %s(其余三条腿仍成立, 所以只报这一条)" % res_i3)
res_i4 = M.first_cause_verdict(
    byid["S8"], [{"as": "trip_window", "txt": _watch_file(True)},
                 {"as": "prof_at_arm", "txt": _prof_file()}], REC_OK, "trip", _L())
add("I4_all_legs_present_passes", res_i4 == [], "四腿齐(同次 TRIP/源样本/profile 同代/事故基线已记录) => 无缺腿")
res_i5 = M.first_cause_verdict(
    byid["S8"], [{"as": "trip_window", "txt": _watch_file(True)},
                 {"as": "prof_at_arm", "txt": _prof_file(pid=9)}], REC_OK, "trip", _L())
add("I5_wrong_generation_named", any("profile_identity" in x and "NOT_EQUAL_RECORDED" in x for x in res_i5),
    "profile 不是本轮提交的那一代 => 具名不匹配(不能拿旧基线冒充首因): %s" % res_i5[:2])

# ---------------- J 组: ⑤+R5② 机械前置是**真门**, 且覆盖每一个起转阶段 ----------
mp = PLAN["mechanical_prerequisites"]["load_means"]
am = PLAN["mechanical_prerequisites"]["assembly_readiness"]
# 扫描范围只限**会执行到现场的机械字段**: 勘误段(corrections_from_review_r5)按审核要求要原样引用
# 复审的旧措辞, 全文扫描会把自己的勘误当成过期事实(上一轮 K5 就是栽在这里)。
MECH_TEXT = json.dumps([PLAN["mechanical_prerequisites"], PLAN["load_step"],
                        PLAN["not_provided_summary"]], ensure_ascii=False)
SPIN = sorted({s["id"] for s in PLAN["stages"]
               for st in s.get("steps", []) if st.get("cmd") in ("motor_arm", "motor_start")})
add("J1_load_means_state", mp["status"] == "NOT_PROVIDED" and mp["blocks_motion"] is True and
    byid["S8"].get("prerequisite_gates") == ["load_means", "assembly_readiness"],
    "缺加载手段: 状态 NOT_PROVIDED + blocks_motion + 与装配就绪门一起挂在 S8 上")
add("J2_asset_facts_updated",
    "T8" in mp["asset"] and "8 mm" in mp["asset"] and
    "已由操作者" in mp["asset_state"] and "仍未提供" in mp["asset_state"] and
    all(x in am["asset_state"] for x in ("松手保持固定", "紧固", "螺母防转", "移动块导向")) and
    not any(x in MECH_TEXT for x in ("300 mm", "尚未连接", "完整导轨滑台", "导程待确认")),
    "机械事实跟着现场走: 装配四项按操作者确认写入 %s, 加载作用点仍是 %s(执行字段里已无过期三段; "
    "旧措辞只出现在勘误/引用段, 故扫描范围限定在机械前置+load_step+缺项汇总)"
    % (am["status"], "NOT_PROVIDED"))
add("J3_instrument_not_a_spinup_gate",
    PLAN["mechanical_prerequisites"]["instrument_probe"]["blocks_motion"] is False and
    "instrument_probe" not in byid["S8"].get("prerequisite_gates", []),
    "示波器继续暂缓: 不作为起转前置, 也不新增高精度仪器前置")
add("J4_gate_is_executed_not_prose",
    ("STAGE_SKIPPED_BY_PREREQUISITE" in CHAIN.read_text(encoding="utf-8") and
     "prerequisite_gates" in CHAIN.read_text(encoding="utf-8")),
    "门在执行器代码里(开端口之前跳过整条链), 不是只在计划的说明字段里")
add("J6_assembly_gate_still_wired_after_confirmation",
    am["status"] == "PROVIDED" and am["blocks_motion"] is True and
    SPIN == ["S10", "S10b", "S4", "S8", "S8b"] and
    all("assembly_readiness" in (byid[s].get("prerequisite_gates") or []) for s in SPIN) and
    bool(am.get("confirmed_items")) and len(am["confirmed_items"]) == 4 and
    any("顺时针=向右" in x for x in am["still_open"]),
    "操作者确认 4 项后状态改 PROVIDED, 但门仍挂在每一个起转阶段上(放行不等于拆门); "
    "方向核实与累计距离核算仍列在 still_open: %s" % am["confirmed_items"])
add("J7_refusal_propagates_along_requires",
    "via_requires" in CHAIN.read_text(encoding="utf-8") and
    "SKIPPED_BEFORE_SPIN_UP(PREREQUISITE)" in CHAIN.read_text(encoding="utf-8") and
    bool(re.search(r"prerequisite_skips=\[\"S4\", \"S5\", \"S6\", \"S7\", \"S8\"", atxt)) and
    "|via_requires=S4|" in atxt,
    "装配未确认场景(E14 的干跑)里, S4 被挡后依赖本次学习结果的 S5/S6/S7 沿计划申报的 requires "
    "一并具名跳过(不是中途失败); 交付态因 S4 已放行, 该传播不再触发")
add("J8_imu_mounting_fact_closed",
    PLAN["mechanical_prerequisites"]["imu_mounting"]["status"].startswith("MOUNTED_TAPE") and
    "绝缘胶带" in json.dumps(PLAN["mechanical_prerequisites"], ensure_ascii=False) and
    not any("ADXL345(现知在扩展板上)的实际固定方式" in json.dumps(x, ensure_ascii=False)
            for x in PLAN["not_provided_summary"]),
    "IMU 固定方式已由操作者确认(绝缘胶带), 不再是缺失项; 但刚度仍未测, 不做精确振动标定")
jbad = json.loads(json.dumps(PLAN))
for s in jbad["stages"]:
    if s["id"] == "S8":
        s["prerequisite_gates"] = []
_lines2, _bad2, _ = M.validate_plan(jbad, json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig")),
                                    str(APPS))
add("J5_meta_negative_ungated_prerequisite",
    any("PLAN_PREREQUISITE_GATE|all|FAIL" in l and "load_means" in l for l in _lines2) and
    any("PLAN_SPINUP_GATE_COVERAGE|all|FAIL" in l and "S8" in l for l in _lines2),
    "元负控: 把 S8 的门摘掉(只剩说明字段)时, PLAN_PREREQUISITE_GATE 与 PLAN_SPINUP_GATE_COVERAGE "
    "都必须 FAIL: %s" % [l[:60] for l in _lines2 if "|FAIL|" in l][:3])

# ---------------- K 组: 登记修正(构建输入提交 + rtconfig 双身份 + 356 B 措辞) -------
bi = PLAN["identity_binding"]
add("K1_build_input_commits_are_true",
    bi["software_input_commit"] == "58904e1045e275698a8c80f63943a1bd0afdb2eb" and
    bi["tested_image_build_commit"] == "fe3d9f5cc37470d732acbbb222012a15d71573c9" and
    bi["build_time_head_commit"] == "48204fe37de3da6f8caeeff467256562f11e58e2",
    "构建输入登记为 tip(58904e1)与 rtconfig 入库提交(fe3d9f5); 构建时 HEAD(48204fe)单独登记且不再充当输入")
for _c, _fld in ((bi["software_input_commit"], "software_input_commit"),
                 (bi["tested_image_build_commit"], "tested_image_build_commit")):
    r = subprocess.run(["git", "rev-parse", "%s^{commit}" % _c], cwd=str(ROOT),
                       capture_output=True, text=True)
    add("K2_%s_resolvable" % _fld, r.returncode == 0 and r.stdout.strip() == _c, "%s 可在仓库里 rev-parse" % _c[:7])
blob = subprocess.run(["git", "show", "48204fe:%s/rtconfig.h" % KERNEL_ROOT], cwd=str(ROOT),
                      capture_output=True)
add("K3_build_head_rtconfig_was_256",
    b"IDLE_THREAD_STACK_SIZE 256" in blob.stdout and b"IDLE_THREAD_STACK_SIZE 1024" not in blob.stdout,
    "实测: 48204fe 的树里 rtconfig.h 仍是 256 => 旧登记把该提交当构建输入是错的")
raw_rt = RTCONFIG.read_bytes()
lf_rt = raw_rt.replace(b"\r\n", b"\n")
tip_blob = subprocess.run(["git", "show", "58904e1:%s/rtconfig.h" % KERNEL_ROOT], cwd=str(ROOT),
                          capture_output=True).stdout
reg = bi["build_inputs"]["rtconfig_h"]
add("K4_rtconfig_dual_identity",
    sha(RTCONFIG) == reg["local_worktree_crlf"]["sha256"] and
    hashlib.sha256(lf_rt).hexdigest() == reg["git_blob_lf"]["sha256"] and
    hashlib.sha256(tip_blob).hexdigest() == reg["git_blob_lf"]["sha256"] and
    len(lf_rt) == reg["git_blob_lf"]["bytes"] and reg["differ_only_by_line_endings"] is True,
    "本机 CRLF %s…/%d B 与远端 LF %s…/%d B 只差行尾, 后者就是 tip 的 git blob 字节 => 不需因此重编"
    % (reg["local_worktree_crlf"]["sha256"][:8], len(raw_rt), reg["git_blob_lf"]["sha256"][:8], len(lf_rt)))
add("K5_356_not_called_margin",
    "已验证安全余量" in json.dumps(PLAN["stack_margin_wording"], ensure_ascii=False) and
    PLAN["stack_margin_wording"]["reachable_path_fixed_frame_lower_bound_bytes"] == 264 and
    PLAN["stack_margin_wording"]["armlink_static_max_depth_bytes"] == 668 and
    not re.search(r"356\s*B\s*(的)?(已验证)?余量", (RD / "run_record.md").read_text(encoding="utf-8")) and
    not re.search(r"留\s*356\s*B", (RD / "stack_report.txt").read_text(encoding="utf-8")),
    "264 与 668 并存且不称 1024-668=356 B 是已验证余量; 轮内两份文件的旧措辞已改")

# ---------------- L 组: C-01 交付包原件逐字节入库 ----------------
CD = ROOT / "reports/reviews/phase8-m8-parallel-protection-ready"
want4 = {f["name"]: (f["sha256"], f["bytes"]) for f in PLAN["c01_intake"]["files_byte_identical"]}
got4 = {p.name: (sha(p), len(p.read_bytes())) for p in CD.iterdir() if p.is_file()}
add("L1_four_originals_byte_identical",
    set(got4) == set(want4) and all(got4[n] == want4[n] for n in want4),
    "4 件与 zip 内清单逐字节一致(不覆盖、不重跑): %s" % sorted(got4))
res_local = json.loads((CD / "results_local.json").read_text(encoding="utf-8"))
add("L2_results_local_not_regenerated",
    res_local["summary"]["all_met"] is True and res_local["summary"]["checks_total"] == 32 and
    len((CD / "results_local.json").read_bytes().splitlines()) > 0,
    "原始结果件按交付态读回: %s(CR 字节 %d, 散列绑的是含 CR 的原字节)" %
    (res_local["summary"], (CD / "results_local.json").read_bytes().count(b"\r")))
ZIPC = pathlib.Path("C:/Users/LMX/Downloads/NS800_C01_Delivery_20261101.zip")
zip_ok = PLAN["c01_intake"]["zip_sha256"] == "3eb4c1d335b08fe39bf83e35aa34865d85bb778521fe0cd9e73e1d331a1bc893"
zip_recompute = sha(ZIPC) == PLAN["c01_intake"]["zip_sha256"] if ZIPC.exists() else "源包已不在本机(登记值仍为交付时复算值)"
add("L3_zip_sha_registered", zip_ok and (zip_recompute is True or isinstance(zip_recompute, str)),
    "交付包本身的 sha256 已登记; 本机复算=%s" % zip_recompute)

# ---------------- M 组: C-02 范围(定向更正, 不全局替换) ----------------
r3plan = json.loads((R3FIELD / "plan.json").read_text(encoding="utf-8"))
r3cont = json.loads((R3FIELD / "plan_continuation_s2.json").read_text(encoding="utf-8"))
r3rows = json.loads((R3FIELD / "rows.json").read_text(encoding="utf-8-sig"))
r4rows = json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig"))
def events_of(doc):
    return sorted({n["event"] for st in doc["stages"] for sp in st.get("require_frames") or []
                   for n in sp.get("must_include") or []})
add("M1_plans_take_event_five",
    events_of(r3plan) == ["5"] and events_of(r3cont) == ["5"] and events_of(PLAN) == ["5"],
    "R3 两份 + R4 计划的停后帧判据都取 event=5: %s/%s/%s" %
    (events_of(r3plan), events_of(r3cont), events_of(PLAN)))
frames5 = lambda rows: sorted({l.split(",")[4] for l in rows["commands"]["flash_dump 0 8"]["trip"]
                               if len(l.split(",")) == 14 and l.split(",")[0] != "slot"})
add("M2_fixtures_are_event_five_and_source_derived",
    frames5(r3rows) == ["5"] and frames5(r4rows) == ["5"] and
    r3rows["commands"]["flash_dump 0 8"]["_provenance"]["kind"] == "source_shape" and
    r4rows["commands"]["flash_dump 0 8"]["_provenance"]["kind"] == "source_shape",
    "夹具帧的 event 列=5 且两组都标 source_shape(全库没有一次板上采集给出 flash_dump CSV 行)")
add("M3_adc68_fixture_present",
    "diag_profile learn 8 80 120 0@adc68_over_sample" in r4rows["commands"] and
    "diag_profile learn 8 80 120 0@adc68_over_sample" in r3rows["commands"] and
    r3rows["commands"]["diag_profile learn 8 80 120 0@adc68_over_sample"]["_provenance"]["kind"]
    == "source_shape",
    "ADC68/SG8 过量采样健康夹具已在两份 rows.json, 且标明是源码派生不是实测")
arch = sorted((R3FIELD.parent / "offline").glob("psrun-*/neg-stagemutate/plan.json"))
dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True,
                       text=True).stdout
keep9 = [p for p in arch if '"event": "9"' in p.read_text(encoding="utf-8") or
         '"event":"9"' in p.read_text(encoding="utf-8")]
add("M4_archived_runs_untouched",
    len(arch) >= 5 and len(keep9) >= 1 and
    not any("psrun-" in l for l in dirty.splitlines()),
    "归档运行输出共 %d 份, 其中 %d 份仍带 event=9(其余是加 require_frames 之前的老快照); "
    "工作树里没有任何 psrun 文件被改动 => 本轮是定向更正而不是全局替换" % (len(arch), len(keep9)))
add("M5_m5_history_untouched",
    not any("phase8-motor-m5" in l for l in dirty.splitlines()),
    "工作树里没有 M5 软件故障原件的改动(event=9 的那批历史证据原样保留); 本轮改动=%d 项" %
    len([l for l in dirty.splitlines() if l.strip()]))
add("M6_event_five_basis_is_source",
    all("safety_state.h:46" in (byid[x].get("require_frames") or [{}])[0].get("event_basis", "")
        for x in ("S9", "S9b")),
    "判据出处逐字写在计划里(FAULT_MULTI_SOURCE=5u 的三条源码锚点)")

# ---------------- N 组: R5① 流程跑完 != 目标传感器通道验证成功 --------------------
SC_A, SC_B = "SG_ADC_controlled_load", "IMU_non_destructive_excitation"
RULES_SRC = M.rel_rule_names(str(APPS))
ROWS4 = json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig"))["commands"]


def provided_copy():
    p = json.loads(json.dumps(PLAN))
    for k, v in p["mechanical_prerequisites"].items():
        if v.get("blocks_motion"):
            v["status"] = "PROVIDED"
    p["stub_branches"] = {SC_A: "trip", SC_B: "trip"}
    return p


def retarget_watch(p, watch_key, latch_key):
    for stg in p["stages"]:
        for st in stg.get("steps", []):
            if stg["id"] == "S8b" and st.get("as") == "trip_window":
                st["key"] = watch_key
            if stg["id"] == "S9b" and st.get("as") == "latch":
                st["key"] = latch_key
    return p


def drop_target_channel(p, sid):
    for stg in p["stages"]:
        if stg["id"] == sid:
            stg.pop("target_channel", None)
    return p


def invent_rule(p, name):
    for stg in p["stages"]:
        if stg["id"] == "S8b":
            stg["target_channel"]["rule_names"] = [name]
    return p


tc8, tc8b = byid["S8"]["target_channel"], byid["S8b"]["target_channel"]
add("N1_target_channel_declared",
    tc8b["rule_names"] == ["REL_IMPACT"] and
    set(tc8["rule_names"]) == {"REL_SG_SEVERE", "REL_ADC_SEVERE", "REL_FUSED_SEVERE"} and
    tc8["rule_key"] == "trip_rule_sg" and tc8b["rule_key"] == "trip_rule_imu" and
    tc8["source_nonzero_keys"] == ["trip_sg_sg", "trip_cur_sg"] and
    tc8b["source_nonzero_keys"] == ["trip_imu_imu"],
    "每场申报自己的目标通道: IMU 只认 REL_IMPACT(imu 列必须非零), SG/ADC 认那三路(sg/cur 非零); "
    "规则名键与源样本键都指向**本场**记录下来的字段")
add("N2_rule_names_exist_in_product",
    all(r in RULES_SRC for r in tc8["rule_names"] + tc8b["rule_names"]) and
    tc8["known_rule_names"] == sorted(RULES_SRC) == tc8b["known_rule_names"],
    "申报的规则名逐个来自产品枚举(rel_reason_t 与 verdict_name() 两头一致的 %d 个名字), 计划造不出名字: %s"
    % (len(RULES_SRC), ",".join(sorted(RULES_SRC))))
_step_key = lambda sid, asname: next(s.get("key") for s in byid[sid]["steps"] if s.get("as") == asname)
add("N3_imu_chain_uses_its_own_fixture",
    _step_key("S8b", "trip_window") == "__watch__@imu" and
    _step_key("S9b", "latch") == "diag_status@latch@imu" and
    _step_key("S8", "trip_window") == "__watch__" and
    _step_key("S9", "latch") == "diag_status@latch" and
    any("r=REL_IMPACT" in l for l in ROWS4["__watch__@imu"]["trip"]) and
    any("imu=3789" in l for l in ROWS4["__watch__@imu"]["trip"]) and
    any("imu=0 " in l for l in ROWS4["__watch__"]["trip"]) and
    ROWS4["__watch__@imu"]["_provenance"]["kind"] == "source_shape" and
    ROWS4["diag_status@latch@imu"]["_provenance"]["kind"] == "source_shape",
    "IMU 那两场吃的是自己通道的夹具(REL_IMPACT + imu 非零), 且标 source_shape(形状取打印语句、"
    "数值合成, 不是板上采集)")
ttxt = dispatch(retarget_watch(provided_copy(), "__watch__", "diag_status@latch"), "target_sg_in_imu")
add("N4_other_source_not_counted_as_success",
    "PROTECTION_CLAIM|scenario=%s|claim=OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)" % SC_B in ttxt and
    "PROTECTION_CLAIM|scenario=%s|claim=VERIFIED" % SC_A in ttxt and
    "core_protection_verified=False" in ttxt and "flow_complete=True" in ttxt and
    "DISPATCH|stage=S9b|" in ttxt and "DISPATCH_SUMMARY|status=OK" in ttxt,
    "复审实测反例(IMU 场喂 REL_SG_SEVERE + imu=0): 具名登记为其他来源触发, 整链仍走完、取证照做, "
    "但 core_protection_verified=False(原文存 field/dispatch_stub_target_sg_in_imu.txt)")
_off = drop_target_channel(provided_copy(), "S8b")
ntxt = dispatch(_off, "target_gate_removed")
_lines3, _, _ = M.validate_plan(_off, json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig")),
                                str(APPS))
add("N5_meta_negative_gate_removed",
    "claim=OTHER_SOURCE_TRIGGERED" not in ntxt and "core_protection_verified=True" in ntxt and
    any("PLAN_TARGET_CHANNEL|all|FAIL" in l and "S8b" in l for l in _lines3),
    "元负控: 摘掉 S8b 的 target_channel 后同一份 SG 数据不再被具名拒绝(core 翻回 True), 而这份突变计划"
    "被离线 PLAN_TARGET_CHANNEL 拒绝 => N4 的判定确实来自这个门(原文存 "
    "field/dispatch_stub_target_gate_removed.txt)")
_p4 = invent_rule(provided_copy(), "REL_IMU_MADE_UP")
_lines4, _, _ = M.validate_plan(_p4, json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig")),
                                str(APPS))
add("N6_meta_negative_invented_rule",
    any("PLAN_TARGET_CHANNEL|all|FAIL" in l and "REL_IMU_MADE_UP" in l for l in _lines4),
    "元负控: 把 IMU 的规则名换成产品里不存在的 REL_IMU_MADE_UP, 离线门必须具名拒绝")
_cb = PLAN.get("claim_boundaries") or {}
add("N8_r3_history_recomputed_same",
    all(x["fail"] == M.validate_plan(json.loads((R3FIELD / pathlib.Path(x["file"]).name).read_text(
            encoding="utf-8")),
        json.loads((R3FIELD / "rows.json").read_text(encoding="utf-8")), str(APPS))[1]
        for x in (PLAN.get("r3_historical_plans_under_current_executor") or {}).get("items", [])) and
    len(PLAN["r3_historical_plans_under_current_executor"]["items"]) == 2,
    "R3 两份历史计划在当前执行器下的 fail 数是**当场复算**的(登记值=%s), 不靠记忆; 本轮不回写它们"
    % [x["fail"] for x in PLAN["r3_historical_plans_under_current_executor"]["items"]])
add("N7_plan_states_boundary",
    "不等于" in json.dumps(_cb, ensure_ascii=False) and
    "跳过" in json.dumps(_cb, ensure_ascii=False) and
    bool(_cb.get("core_protection_verified_true_means")) and bool(_cb.get("never_from_this_package")),
    "计划自己写明边界: 交付态跳过 SG/ADC 激励链, 整包完成不等于核心保护全部通过(%d 条 never_from)"
    % len(_cb.get("never_from_this_package") or []))

add("N9_summary_label_requires_whole_run_success",
    "core_verified = bool(ok) and bool(claims) and all(" in CHAIN.read_text(encoding="utf-8") and
    "FLOW_VS_VERIFICATION|flow_complete=%s|core_protection_verified=%s" in
    CHAIN.read_text(encoding="utf-8") and
    "flow_complete=True|core_protection_verified=False" in htxt,
    "汇总标签的机器写法核到执行器源码: core_verified 必须先有整包成功(ok), 再看各场声明; "
    "交付态实测 flow=True/core=False 也在同一次干跑里(行为反例见 host/verify_four_branch_matrix.py 的 V14)")

problems = [n for n, ok, _ in CHECKS if not ok]
n_metaneg = sum(1 for n, _, _ in CHECKS if n.startswith("D"))
extra_meta = [n for n, ok, _ in CHECKS if "meta_negative" in n]
lines = ["IDLE_LEARN_CHECK mode=offline_only (零设备: 不起 pyocd, 不开串口, 不动 Keil; dispatch 走 stub+sim)",
         "run_dir(LOCAL_ONLY, 仓外)=%s" % SCRATCH.as_posix()]
lines += [("OK " if ok else "PROBLEM ") + n + ("" if ok else " | " + dt) for n, ok, dt in CHECKS]
lines.append("CHECKS=%d problems=%d" % (len(CHECKS), len(problems)))
lines.append("META_NEGATIVES=%d fired=%d" % (n_metaneg, n_metaneg - len([p for p in problems if p.startswith("D")])))
lines.append("META_NEGATIVES_R5=%d(%s) fired=%d" %
             (len(extra_meta), ",".join(extra_meta), len(extra_meta) - len([p for p in problems
                                                                           if p in extra_meta])))
lines.append("IDLE_LEARN_CHECK %s" % ("PROBLEMS " + json.dumps(problems, ensure_ascii=False) if problems
                                      else "ALL_PASS_WITH_META_NEGATIVES"))
txt = "\n".join(lines) + "\n"
(RD / "host" / "idle_learn_console.txt").write_text(txt, encoding="utf-8", newline="\n")
(RD / "host" / "idle_learn_check.json").write_text(json.dumps(
    {"checks": [{"name": n, "ok": ok, "detail": dt} for n, ok, dt in CHECKS], "problems": problems},
    ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
print(txt.encode("utf-8", "replace").decode("utf-8"))
sys.exit(0 if not problems else 1)
