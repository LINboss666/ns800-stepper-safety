#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7F5A 登记轮判据: A 的两路 LA 采集任务书 + 旧 R7F5 串口计划, 本机离线复算(设备动作 0)。

纪律:
  * 判据只用**未改动执行器**与磁盘真实字节, 不采信任何自报哈希;
  * 检查器要先证明自己会命中(三类必 FIRED 自检 + 一条不得误报的对照);
  * A 明确要求的三项前置(实物输入耐压 / 采集容量覆盖时长 / 探头->GUI 通道映射)没有资料就是
    OPEN, 不写 PASS, 也不替操作者填现场事实。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("D:/ns800")
ROUND = ROOT / "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
PKG = Path("D:/ns800_r7f1_scratch/r7f5a_pkg")
LA = Path("C:/r7f5_la")
# 交付件所在目录不写进源码(避免把本机用户目录绝对路径带进公开仓库): 用环境变量或运行时展开。
ZIP = Path(os.environ.get("R7F5A_ZIP") or
           (Path.home() / "Downloads" / "NS800_R7F5A_LA_DIAG_ENN_Task_20261104.zip"))
AR = ROUND / "a_review_r7f5a"
LAAR = ROUND / "la_local_r7f5a"
OUT = ROUND / "offline_r7f5a"
EXEC_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
EXEC = ROOT / EXEC_REL
PLAN_REL = "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/plan_r7f5_b502_blind_irq_static.json"
ROWS_REL = "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/rows_r7f5_b502_blind_irq_static.json"
IMG_REL = "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin"
LEDGER = Path("C:/m8r_approvals/approval_M8R7F5-b502-blind-irq-static-20261104.json")
AID_OLD = "M8R7F5-b502-blind-irq-static-20261104"
AID_NEW = "M8R7F5A-b502-la-two-channel-static-20261104"
STEPS_SHA_EXPECT = "4a80dcb6dd43028c6f526298463cba3d71bc312a379db5b152a4db52359b7eb0"

sys.path.insert(0, str(EXEC.parent))
import m8r3_serial_chain as m  # noqa: E402

checks = []
fails = []


def ck(name, ok, detail=""):
    checks.append({"name": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:500]})
    if not ok:
        fails.append(name)
    return ok


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def dump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_bytes((json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


# ---------- 0. 归档 A 的包(原件不动) ----------
AR.mkdir(parents=True, exist_ok=True)
zip_bytes = ZIP.read_bytes()
ident = {
    "package": ZIP.name,
    "bytes": len(zip_bytes),
    "sha256": hashlib.sha256(zip_bytes).hexdigest(),
    "received_via": "operator relay of reviewer A attachment",
    "entries_in_zip": None,
    "prepared_by_A_hardware_actions": 0,
    "round": "M8-R7F5A-LA-TWO-CHANNEL-STATIC",
    "archived_here": sorted(p.name for p in AR.iterdir()),
}
for f in ["TASK_R7F5A_LA_TWO_CHANNEL.md", "capture_scope.json", "offline_binding_check.json",
          "SHA256SUMS.txt"]:
    shutil.copyfile(PKG / f, AR / f)
scope = jload(PKG / "capture_scope.json")
ident["entries_in_zip"] = len(zipfile_entries := __import__("zipfile").ZipFile(ZIP).namelist())
(AR / "IDENTITY.json").write_bytes((json.dumps(ident, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

# ---------- 1. 外层与内层身份 ----------
ck("ZIP_BYTES_66179", len(zip_bytes) == 66179, len(zip_bytes))
ck("ZIP_SHA_STABLE", ident["sha256"] == sha(ZIP), ident["sha256"][:16])
inner = [l for l in (PKG / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
ok_in = 0
for l in inner:
    h, _, rel = l.partition("  ")
    ok_in += (sha(PKG / rel.strip()) == h.strip())
ck("INNER_SUMS_ALL_OK", ok_in == len(inner) == 4, "ok=%d/%d" % (ok_in, len(inner)))
ck("INNER_SUMS_EXCLUDE_SELF", not any("SHA256SUMS" in l.partition("  ")[2] for l in inner), "清单不哈希自身")
emb = PKG / "original_R7F5.zip"
prior = jload(ROUND / "handoff_R7F5_registered.json")["reviewed_package"]
ck("EMBEDDED_R7F5_ZIP_MATCHES_CLAIM",
   sha(emb) == scope["original_zip"]["sha256"] and emb.stat().st_size == scope["original_zip"]["bytes"],
   scope["original_zip"]["sha256"][:16])
ck("EMBEDDED_R7F5_ZIP_MATCHES_ARCHIVED_RECORD",
   sha(emb) == prior["sha256"] and emb.stat().st_size == prior["bytes"],
   "旧登记 %d B %s" % (prior["bytes"], prior["sha256"][:16]))

# ---------- 2. 落位件与执行输入身份(不重新落位, 上一轮已放) ----------
placed = []
for f in scope["source_binding"]["files"]:
    p = ROOT / f["path"]
    got = {"path": f["path"], "bytes": p.stat().st_size, "sha256": sha(p),
           "declared_bytes": f["bytes"], "declared_sha256": f["sha256"],
           "match": p.stat().st_size == f["bytes"] and sha(p) == f["sha256"]}
    placed.append(got)
ck("PLACED_PLAN_ROWS_MATCH", all(x["match"] for x in placed),
   "; ".join("%s=%s" % (os.path.basename(x["path"]), x["sha256"][:12]) for x in placed))
sb = scope["source_binding"]
ck("EXECUTOR_UNCHANGED", sha(EXEC) == sb["executor_sha256"], sha(EXEC)[:16])
ck("IMAGE_UNCHANGED", sha(ROOT / IMG_REL) == sb["firmware_sha256"], sha(ROOT / IMG_REL)[:16])
ck("NO_NEW_FIRMWARE_OR_EXECUTOR",
   scope.get("base_serial_plan_bytes_unchanged") is True and
   jload(PKG / "offline_binding_check.json")["new_executable_code"] is False and
   jload(PKG / "offline_binding_check.json")["new_serial_plan"] is False, "A 自报无新码/新计划, 由上面两项独立承担")

# ---------- 3. 基线与改动范围 ----------
base = sb["reviewed_delivery_commit"]
rc = subprocess.run(["git", "rev-parse", base], cwd=str(ROOT), capture_output=True)
ck("BASELINE_COMMIT_EXISTS", rc.returncode == 0 and rc.stdout.decode().strip() == base.strip(),
   rc.stdout.decode().strip()[:12])
rc = subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=str(ROOT), capture_output=True)
ck("HEAD_DESCENDS_BASELINE", rc.returncode == 0, "0=是")
rc = subprocess.run(["git", "diff", "--name-only", base + "..HEAD"], cwd=str(ROOT), capture_output=True)
paths = [p for p in rc.stdout.decode("utf-8", "replace").splitlines() if p.strip()]
off = [p for p in paths if not p.startswith("reports/")]
ck("DIFF_FROM_BASELINE_REPORTS_ONLY", not off, "越界=%s 总数=%d" % (off[:3], len(paths)))

# ---------- 4. 旧账只读核查(A 要求的分支门) ----------
led = jload(LEDGER)
ids_now = {"plan_sha256": sha(ROOT / PLAN_REL),
           "executor_sha256": sha(EXEC),
           "image_sha256": sha(ROOT / IMG_REL)}
gate_err = ""
try:
    m.validate_ledger_doc(led, AID_OLD, ids_now, jload(ROOT / PLAN_REL)["quota"])
except m.Stop as e:
    gate_err = "%s|%s" % (e.code, e.detail)
ck("OLD_LEDGER_VALID_BOUND_TO_CURRENT_BYTES", gate_err == "", gate_err or "validate_ledger_doc 未拒绝")
spent = {k: v for k, v in led["spent"].items() if v}
ck("OLD_LEDGER_UNSPENT", not spent, "非零=%s" % (spent or "none"))
ck("OLD_LEDGER_NO_PENDING", led.get("pending") == [], led.get("pending"))
ck("OLD_LEDGER_APPROVAL_ID", led.get("approval_id") == AID_OLD, led.get("approval_id"))
ck("OLD_LEDGER_IDENTITIES_EQUAL_CURRENT", led["identities"] == ids_now,
   json.dumps({k: v[:12] for k, v in led["identities"].items()}))
# 负控: 故意换掉一个身份, 同一个校验器必须拒绝
bad = dict(ids_now)
bad["plan_sha256"] = "0" * 64
refused = ""
try:
    m.validate_ledger_doc(led, AID_OLD, bad, jload(ROOT / PLAN_REL)["quota"])
except m.Stop as e:
    refused = e.code
ck("SELFCHECK_LEDGER_GATE_FIRED", refused != "", "改身份后应拒绝: %s" % (refused or "未拒绝=判据恒真"))

# ---------- 5. 串口计划本身(16 步 / 无 arm-start / 步骤摘要) ----------
snap = m.read_plan_snapshot(str(ROOT / PLAN_REL))
plan = snap["doc"]
q = plan["quota"]
q_keys = list(q.keys())
ck("PLAN_SNAPSHOT_SHA_IS_CLAIM", snap["sha256"] == sb["files"][0]["sha256"], snap["sha256"][:16])
serial_stages = [s for s in plan["stages"] if s.get("kind") == "serial_session"]
n_steps = sum(len(m.stage_steps(s, "response_driven")) for s in serial_stages)
ck("BASE_SERIAL_STEPS_16", n_steps == 16 == jload(PKG / "offline_binding_check.json")["base_serial_steps"], n_steps)
cmds = [st.get("cmd") for s in serial_stages for st in m.stage_steps(s, "response_driven")]
ck("NO_ARM_START_IN_PLAN", not any(c in ("motor_arm", "motor_start") for c in cmds),
   " cmds=%d" % len(cmds))
forb = set(sum([s.get("forbidden_commands", []) for s in serial_stages], []))
ck("FORBIDDEN_LIST_COVERS_ARM_START", {"motor_arm", "motor_start"} <= forb, len(forb))
digest = m.canonical_steps_sha(sum([m.stage_steps(s, "response_driven") for s in serial_stages], []))
ck("STEPS_DIGEST_EQUALS_PRIOR_ROUND", digest == STEPS_SHA_EXPECT, digest[:16])
step_q_names = sorted({st["quota"]["name"] for s in serial_stages
                       for st in m.stage_steps(s, "response_driven") if st.get("quota")})
ck("EVERY_STEP_QUOTA_NAME_IS_DECLARED", set(step_q_names) <= set(q_keys),
   "步骤用到=%s 声明=%d 项" % (step_q_names, len(q_keys)))
summed = {}
for s in serial_stages:
    for st in m.stage_steps(s, "response_driven"):
        qq = st.get("quota")
        if qq:
            summed[qq["name"]] = summed.get(qq["name"], 0) + qq["n"]
ck("DECLARED_CAPS_COVER_SUMMED_STEP_QUOTAS",
   all(q[k] >= v for k, v in summed.items()), json.dumps([summed, {k: q[k] for k in summed}]))
ck("LEDGER_CAPS_AND_SPENT_KEYS_EQUAL_PLAN_QUOTA",
   set(led["caps"]) == set(q_keys) and set(led["spent"]) == set(q_keys),
   "caps=%d spent=%d quota=%d" % (len(led["caps"]), len(led["spent"]), len(q_keys)))
zero_keys = ("download", "readback", "explicit_reset", "motor_run", "motor_run_seconds_total",
             "diag_profile_learn", "diag_profile_commit", "diag_profile_zero", "config_save",
             "blackbox_clear", "retry", "approved_reboot", "dmm_readings", "physical_collision")
ck("ZERO_CAPS_COVER_FORBIDDEN_ACTIONS", all(q.get(k) == 0 for k in zero_keys),
   [k for k in zero_keys if q.get(k) != 0])

# ---------- 6. validate_plan 走生产入口 ----------
rc = subprocess.run([sys.executable, str(EXEC), "--validate-plan", "--plan", str(ROOT / PLAN_REL),
                     "--repo-root", str(ROOT)], cwd=str(ROOT), capture_output=True)
lines = [l for l in rc.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
summ = [l for l in lines if l.startswith("PLAN_VALIDATE_SUMMARY")]
ck("VALIDATE_PLAN_57_FAIL1", bool(summ) and "checks=57" in summ[0] and "fail=1" in summ[0], summ[0][:160] if summ else "无汇总")
only_fail = [l for l in lines if "|FAIL|" in l]
ck("THE_ONLY_FAIL_IS_MOTION_CAP_BUDGET", len(only_fail) == 1 and "PLAN_MOTION_CAP_BUDGET" in only_fail[0],
   only_fail[0][:120] if only_fail else "无 FAIL")
(OUT).mkdir(parents=True, exist_ok=True)
(OUT / "validate_plan_local.txt").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

# ---------- 7. 额度对照表(串口侧 vs 本补充范围) ----------
caps = scope["caps"]
q = plan["quota"]
mapping = {
    "port_open": (caps["port_open"], q["port_open"]),
    "tmc_config_apply": (caps["tmc_config_apply"], q["tmc_config_apply"]),
    "safety_irq_disable": (caps["safety_irq_disable"], q["safety_irq_disable"]),
    "polarity_confirm_plus_irq_enable": (
        caps["safety_polarity_confirm_trial_only"] + caps["safety_irq_enable"], q["irq_polarity_prep"]),
    "download": (caps["download"], q["download"]),
    "readback": (caps["readback"], q["readback"]),
    "reset": (caps["reset"], q["explicit_reset"]),
    "motor_arm_start_motion": (caps["motor_arm"] + caps["motor_start"] + caps["motion"],
                               q["motor_run"]),
    "learning": (caps["learning"], q["diag_profile_learn"]),
    "config_save": (caps.get("config_save", 0), q["config_save"]),
    "blackbox_clear_or_fault_reset": (0, q["blackbox_clear"] + q.get("retry", 0)),
}
ck("CAPS_MAP_TO_PLAN_QUOTA", all(a == b for a, b in mapping.values()),
   json.dumps({k: list(v) for k, v in mapping.items() if v[0] != v[1]}) or "逐项相等")
ck("INSTRUMENT_BUDGET_IS_EXTERNAL_NOT_ENFORCED",
   scope["instrument_budget_is_external_not_enforced_by_serial_executor"] is True and
   caps["logic_capture_session"] == 1 and caps["logic_start"] == 1,
   "串口执行器不扣仪器额度, 不谎称原子联动")
absent = [k for k in q_keys if k not in caps]
widened = [k for k in q_keys if q[k] == 0 and caps.get(k, 0) > 0]
ck("CAPS_DO_NOT_WIDEN_ANY_ZERO_ACTION", not widened,
   "计划为 0 但补充范围给了正数的动作=%s" % (widened or "none"))
ck("ABSENT_NONZERO_QUOTA_KEYS_REEXPRESSED_EXACTLY",
   [k for k in absent if q[k] != 0] == ["irq_polarity_prep"] and
   caps["safety_polarity_confirm_trial_only"] + caps["safety_irq_enable"] == q["irq_polarity_prep"],
   "缺席且非 0 的只有 irq_polarity_prep=%d, 由 polarity_confirm1+irq_enable1 重述" % q["irq_polarity_prep"])
ck("CAPS_DECLARES_SERIAL_ENFORCED_KEYS",
   all(k in caps for k in ("port_open", "tmc_config_apply", "safety_irq_disable")),
   "执行器真实扣减的三项都在补充范围里重述")
ck("ZERO_FORBIDDEN_CLAIM_LIST_KEPT",
   set(jload(ROOT / PLAN_REL)["trial_exception"]["forbidden_claims"]) ==
   {"PHYSICAL_POLARITY_VERIFIED", "DIAG_TRIGGER_VERIFIED", "STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE",
    "CORE_PROTECTION_VERIFIED"}, "四项禁用声称原样保留")
mc = scope["measurement_claims_before_execution"]
ck("CLAIMS_BEFORE_EXEC_ALL_FALSE", all(v is False for v in mc.values()), json.dumps(mc))

# ---------- 8. LA 侧本机实测事实 ----------
LAAR.mkdir(parents=True, exist_ok=True)
tools_list = jload(LA / "tools.out")["result"]["tools"]
names = sorted(t["name"] for t in tools_list)
shutil.copyfile(LA / "tools.out", LAAR / "la_mcp_tools_list.json")
shutil.copyfile(LA / "init.out", LAAR / "la_mcp_initialize_response.json")
shutil.copyfile(LA / "hdr.txt", LAAR / "la_mcp_initialize_headers.txt")
shutil.copyfile(LA / "cap_schema.txt", LAAR / "la_start_capture_schema.txt")
shutil.copyfile(LA / "export_smoke" / "digital.csv", LAAR / "la_smoke_capture_digital.csv")
shutil.copyfile(LA / "logic_win.png", LAAR / "logic_1_2_17_connected.png")
shutil.copyfile(LA / "mcp_start_capture.json", LAAR / "la_smoke_start_capture_response.json")
shutil.copyfile(LA / "mcp_export_raw_data_csv.json", LAAR / "la_smoke_export_response.json")
gd_raw = jload(LA / "mcp_get_devices.json")
devs = gd_raw["result"]["structuredContent"]["devices"]
serial = devs[0]["deviceId"]
san = {"devices": [{"deviceId_masked": "SHA12:" + hashlib.sha256(serial.encode()).hexdigest()[:12],
                    "deviceId_len": len(serial), "deviceType": devs[0]["deviceType"],
                    "isSimulation": devs[0]["isSimulation"]}],
       "masking_note": "探头机序列号不入公开仓库, 只留 sha256 前 12 位与长度; 原文仍在 C:/r7f5_la"}
dump(LAAR / "la_mcp_get_devices_sanitized.json", san)
ck("LA_MCP_TOOLS_15", len(tools_list) == 15, len(tools_list))
for need in ("start_capture", "stop_capture", "wait_capture", "export_raw_data_csv", "save_capture"):
    ck("LA_TOOL_PRESENT_" + need, need in names, "")
shutil.copyfile(LA / "la_reject_threshold.json", LAAR / "la_reject_threshold.json")
shutil.copyfile(LA / "la_reject_samplerate.json", LAAR / "la_reject_samplerate.json")
rej = {}
for f in ("la_reject_threshold.json", "la_reject_samplerate.json"):
    r = jload(LAAR / f)["result"]
    rej[f] = {"isError": r.get("isError") is True,
              "captureId_returned": "captureId" in (r.get("structuredContent") or {}),
              "text": r["content"][0]["text"]}
ck("LA_REJECTIONS_CREATED_NO_CAPTURE_SESSION",
   all(v["isError"] and not v["captureId_returned"] for v in rej.values()),
   "两条具名拒绝都在参数校验期返回, 未建采集会话")
err_txt = rej["la_reject_samplerate.json"]["text"] + "\n" + rej["la_reject_threshold.json"]["text"]
ck("LA_THRESHOLD_NOT_CONFIGURABLE", "does not support a configurable threshold voltage" in err_txt,
   "具名拒绝原文留档")
rates = re.findall(r'\{"digital":(\d+)', err_txt)
ck("LA_1MS_PER_SECOND_IS_SUPPORTED", "1000000" in rates,
   "档位=%s" % (",".join(rates[:3]) + "…"))
ck("LA_MIN_RATE_25K_MAX_24M", rates and min(map(int, rates)) == 25000 and max(map(int, rates)) == 24000000,
   "%s..%s" % (min(map(int, rates)), max(map(int, rates))))
smoke = (LAAR / "la_smoke_capture_digital.csv").read_text(encoding="utf-8").splitlines()
cols = smoke[0].split(",")
rows = [l.split(",") for l in smoke[1:]]
vals = list(zip(*[r[1:] for r in rows])) if rows else []
ck("LA_SMOKE_EXPORT_GIVES_ALL_8_COLUMNS",
   len(cols) == 9 and len(rows[0]) == 9, "请求 1 列, 实得 %d 列 => 导出侧筛选不生效" % (len(cols) - 1))
ck("LA_SMOKE_LEVELS_FLAT_FLOATING",
   all(len(set(v)) == 1 for v in vals), "每路全程不变; 探头未接 => 浮空, 不作测量证据")
ck("LA_SMOKE_D0_LOW_OTHERS_HIGH", [r[1] for r in rows] == ["0"] * len(rows) and
   all(set(r[i]) == {"1"} for r in rows for i in range(2, 9)), "D0=0, D1-D7=1")
ck("LA_SMOKE_HAS_TWO_STATE_ROWS", len(rows) == 2, "首行=初始态, 末行=停止时刻")
(LAAR / "README_LA_LOCAL_FACTS.txt").write_bytes((
    "本机(非 A)在 R7F5A 任务书下达前后对逻辑分析仪做的实测, 全部只到'仪器与软件通道'层面。\n"
    "\n"
    "来源与身份\n"
    "  - 被测软件: C:\\Program Files\\Logic\\Logic.exe = Logic 2 2.4.48 (Electron, 主窗口 PID 变化过);\n"
    "    更早一次截图 logic_1_2_17_connected.png 来自 C:\\Program Files\\Saleae Inc\\Logic.exe 1.2.17,\n"
    "    标题 [Connected] 只证明当时软件认到设备, 不证明任何实物输入耐压。\n"
    "  - 控制通道: MCP http://127.0.0.1:10530/ (la_mcp_initialize_*.json/txt 为握手原文)。\n"
    "    Automation Server 10430 当时无监听(连接被拒), 本轮未依赖它。\n"
    "  - 设备: 8 路数字通道; 序列号不入公开仓库, 见 la_mcp_get_devices_sanitized.json 的掩码说明。\n"
    "\n"
    "实测到的能力/限制(全部有对应原文件)\n"
    "  1) 数字采样率只接受 25 kS/s..24 MS/s 共 13 档 => la_reject_samplerate.json 具名拒绝原文;\n"
    "     任务书希望的 1 MS/s 在允许档位内。\n"
    "  2) 不支持可调输入阈值电压 => la_reject_threshold.json 具名拒绝原文。因此不能靠设阈值来适配电平。\n"
    "  3) 上面两条拒绝都发生在参数校验期, isError=true 且不返回 captureId => 没有建立采集会话。\n"
    "  4) 导出侧通道筛选不生效: 请求 digitalChannels:[0], 实得 8 列 => la_smoke_capture_digital.csv。\n"
    "     所以 CSV 的列号与信号的对应必须现场核, 不能假设导出按请求裁剪。\n"
    "\n"
    "CONTROL_PATH_SMOKE 声明(不是测量证据)\n"
    "  la_smoke_capture_digital.csv 是探头未接任何东西时的 1 秒定长采集: D0 全程 0, D1-D7 全程 1,\n"
    "  两行状态无任何变化。这是浮空输入, 按项目纪律禁止当作电平事实或'采集成功'的证据。\n"
    "  该次采集发生在 R7F5A 任务书下达之前, 属操作者要求的'试试能否控制仪器', 不占本轮\n"
    "  logic_capture_session=1 的额度; 任务书第 58 行'不要先做试采消耗一次机会'的要求与本事实\n"
    "  存在时间差, 如实登记, 不解释成已满足。\n"
    "\n"
    "仍未核清(见 offline_r7f5a/preflight_open_items.json)\n"
    "  实物输入耐压资料 / ≥100 M samples 的实际缓冲容量 / 探头印字 CH1-CH2 与 GUI 通道号的对应。\n").encode("utf-8"))

# ---------- 9. A 要求的三项前置: 没资料就登记 OPEN ----------
open_items = [
    {"item": "hardware_input_rating",
     "required_by": "capture_scope.preflight_required[3] + offline_binding_check.hardware_input_rating",
     "state": "OPEN",
     "reason": "本机没有该分析仪的实物输入耐压资料; 不得由 Logic 版本或 USB VID/PID 推定。"
               "需要型号规格页(通道输入电压范围/是否 5V 容许/最大绝对电压)。",
     "blocks": "上电、Start、开 COM 之前"},
    {"item": "acquisition_capacity_100Ms",
     "required_by": "capture_scope.acquisition_target.capacity_samples_at_preferred_rate_min",
     "state": "OPEN",
     "reason": "1 MS/s 在设备允许档位内(已证), 但 ≥100 M samples 的缓冲容量要从实际 GUI 读; "
               "本轮尚未在 GUI 里核过时长上限。",
     "blocks": "Start 之前"},
    {"item": "probe_label_to_gui_channel_mapping",
     "required_by": "capture_scope.channel_map.gui_channel_indices",
     "state": "OPEN",
     "reason": "操作者印字 CH1/CH2 与软件通道号(可能 0 基)的对应必须现场读, 不能凭第一路猜。"
               "另: 导出侧通道筛选不生效(已实测), 所以 CSV 列号与通道的对应也要现场核。",
     "blocks": "判读之前"},
]
dump(OUT / "preflight_open_items.json", {"items": open_items, "count": len(open_items)})
ck("PREFLIGHT_GAPS_REGISTERED_AS_OPEN", len(open_items) == 3 and
   all(i["state"] == "OPEN" for i in open_items), "三项未闭合, 不写 PASS")

# ---------- 10. 前次工具判断的更正(照 A 的补正登记) ----------
corrections = [
    {"claim": "我在 2026-11-04 说过 'Logic 1.2.17 完全没有远程/脚本 API'",
     "status": "OVERREACH_WITHDRAWN",
     "what_was_actually_measured": "当时本机: 127.0.0.1:10429/10430 无监听; 安装目录 C:\\Program Files\\Saleae Inc "
                                   "只有 Qt 运行件与 Analyzers, 没有 Logic 2 的 resources/bin; "
                                   "本机 pyusb 1.3.1 的 libusb1/libusb0 后端均 NOT_LOADED。",
     "corrected_by": "TASK_R7F5A_LA_TWO_CHANNEL.md 第 61 行(Legacy Logic 1.x API 确实存在)",
     "consequence": "'端口没监听'只能说明该通道当时未就绪, 不能推出'没有 API'。本轮不需要为它开新服务。"},
    {"claim": "我在本轮之前做过一次 1 秒采集(captureId=3, 25 kS/s, 探头未接)",
     "status": "DISCLOSED_PRE_EXISTING",
     "scope_note": "该采集发生在本任务书之前, 属操作者要求的'试试能否控制仪器', 标 CONTROL_PATH_SMOKE; "
                   "不占本轮 logic_capture_session=1 的额度, 也不作为任何电平证据。",
     "conflict_with_task_book": "任务书第 58 行要求不要先做试采消耗机会; 事实是试采已在本包下达前发生, "
                                "本轮的正式采集仍是第一次带接线的采集。"},
]
dump(OUT / "corrections_and_disclosures.json", {"items": corrections})
ck("CORRECTIONS_REGISTERED", len(corrections) == 2, "2 条")

# ---------- 11. 三类必 FIRED 自检 + 一条不得误报 ----------
LEAKS = [(re.compile(p), lab) for p, lab in [
    (rb"(?<![0-9a-fA-F])[0-9a-fA-F]{48}(?![0-9a-fA-F])", "48位十六进制(疑似 DAP UID)"),
    (rb"[Cc]:[\\/][Uu]sers[\\/][^\s\"']+", "用户目录绝对路径(含本机用户名)"),
    (rb"CONFIRM\s+[0-9A-Za-z]{16,}", "疑似批准令牌原文")]]
samples = [b"x " + b"0123456789abcdef" * 3 + b" y",
           b"C:" + b"\\Users\\someone\\file", b"CONFIRM " + b"Z" * 20]
hit = sum(any(p.search(s) for p, _ in LEAKS) for s in samples)
ck("SELFCHECK_LEAK_PATTERNS_FIRED", hit == 3, "命中 %d/3" % hit)
ck("SELFCHECK_SHA_NOT_FLAGGED_AS_UID",
   not LEAKS[0][0].search(b"ee3b26513fc7a572daff0c5c26124a9a0f7fbfa20632bd7088fb84d9d0ca4402"),
   "64 位 sha 不得被当成 48 位 UID")
ck("SELFCHECK_PLACED_SHA_CORRUPTION_FIRED",
   sha(ROOT / PLAN_REL) != "0" * 64 and sha(ROOT / PLAN_REL)[:8] == "ee3b2651",
   "对照: 真实散列与声明前缀一致")

summary = {"round": "M8-R7F5A-registration", "checks": len(checks), "fails": len(fails),
           "fail_names": fails, "device_actions_this_round": 0,
           "steps_digest": digest, "old_ledger_state": "UNSPENT_NO_PENDING",
           "new_approval_id_proposed": AID_NEW,
           "preflight_open_items": [i["item"] for i in open_items],
           "la_facts": {"tools": len(tools_list), "allowed_rates": rates,
                        "threshold_configurable": False, "export_columns": len(cols) - 1,
                        "smoke_levels": "D0=0 D1-D7=1 flat (probes unconnected)"}}
dump(OUT / "summary.json", summary)
dump(ROUND / "offline_r7f5a" / "checks_full.json", {"checks": checks})
print(json.dumps(summary, ensure_ascii=False, indent=2))
print("FAILS=%d" % len(fails))
sys.exit(1 if fails else 0)
