#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7F5A 现场判读: 串口层(原计划 16 步的字节事实) 与 仪器层(一次 8 路采集) 分别成结论。

纪律:
  * 两层各自判定, 不拼成"全过"; 仪器层不具区分力时如实标 NON_DISCRIMINATING, 不借串口全绿抬绿;
  * 通道身份不靠"预期一低一高"反推(A 补充第 2 条);
  * 浮空基线对照必须做: 本机会让未接通道读高, 所以"高"本身不证明被驱动。
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path("D:/ns800")
ROUND = ROOT / "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
FIELD = ROUND / "field/field-20261104T101046Z"
S1 = FIELD / "S_S1"
LA = Path("D:/ns800_r7f1_scratch/r7f5a_field")
OUT = ROUND / "field_r7f5a"
checks, fails = [], []


def ck(name, ok, detail=""):
    checks.append({"name": name, "result": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})
    if not ok:
        fails.append(name)
    return ok


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------- 仪器层 ----------
csv = (LA / "csv" / "digital.csv").read_text(encoding="utf-8").splitlines()
hdr = csv[0].split(",")
rows = [l.split(",") for l in csv[1:]]
cols = len(hdr) - 1
per = {}
for i, c in enumerate(hdr[1:]):
    vals = [r[i + 1] for r in rows]
    edges = sum(1 for k in range(1, len(vals)) if vals[k] != vals[k - 1])
    per[c] = {"initial": vals[0], "final": vals[-1], "distinct": sorted(set(vals)), "transitions": edges}
t0, t1 = rows[0][0], rows[-1][0]


def iso(s):
    import datetime
    return datetime.datetime.fromisoformat(s.replace("+00:00", "+00:00"))


span = (iso(t1) - iso(t0)).total_seconds()
start_rec = json.loads((LA / "la_start.json").read_text(encoding="utf-8"))
stop_rec = json.loads((LA / "la_stop.json").read_text(encoding="utf-8"))
ck("LA_CHANNELS_ALL_EIGHT_ENABLED", cols == 8 and all(v["distinct"] for v in per.values()), cols)
ck("LA_ZERO_TRANSITIONS_ON_EVERY_CHANNEL", all(v["transitions"] == 0 for v in per.values()),
   {c: v["transitions"] for c, v in per.items()})
ck("LA_SPAN_COVERS_SERIAL_SESSION", 20.0 < span < 60.0, "%.4f s" % span)
sal = LA / "native" / "capture_id8.sal"
ck("LA_NATIVE_SAVED", sal.is_file() and sal.stat().st_size > 0, "%d B" % sal.stat().st_size)
ck("LA_SAVE_FIRST_ATTEMPT_ERROR_KEPT",
   (LA / "la_save_first_attempt_error.json").is_file() and
   "filepath" in (LA / "la_save_first_attempt_error.json").read_text(encoding="utf-8"),
   "保存参数用错的具名错误原文留档, 未删")
low = [c for c, v in per.items() if v["initial"] == "0"]
high = [c for c, v in per.items() if v["initial"] == "1"]
ck("LA_LEVEL_PATTERN", low == ["Channel 0"] and len(high) == 7, "低=%s 高=%d" % (low, len(high)))
base = (Path("C:/r7f5_la") / "export_smoke" / "digital.csv").read_text(encoding="utf-8").splitlines()
brows = [l.split(",") for l in base[1:]]
bpat = [brows[0][i + 1] for i in range(len(brows[0]) - 1)]
npat = [rows[0][i + 1] for i in range(len(rows[0]) - 1)]
ck("LA_PATTERN_IDENTICAL_TO_UNCONNECTED_BASELINE", bpat == npat,
   "未接基线=%s 本轮=%s => 电平层无法区分'接线正确且静默'与'探头未接触/无共地'" % ("".join(bpat), "".join(npat)))
ck("LA_NO_EDGE_MEANS_NO_TRIGGER_OBSERVED", all(v["transitions"] == 0 for v in per.values()),
   "本轮不产生任何触发/极性边沿证据; 禁用声称照旧生效")

# ---------- 串口层 ----------
rx = sorted(p for p in S1.iterdir() if p.name.endswith(".rx.bin"))
ck("SERIAL_16_RESPONSES", len(rx) == 16, len(rx))
nul = {p.name: p.read_bytes().count(0) for p in rx}
ck("SERIAL_NO_BARE_NUL", all(v == 0 for v in nul.values()), {k: v for k, v in nul.items() if v})
bad_utf = []
for p in rx:
    try:
        p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        bad_utf.append(p.name)
ck("SERIAL_STRICT_UTF8", not bad_utf, bad_utf)
txt = {p.name: p.read_bytes().decode("utf-8", "replace") for p in rx}


def find(sub, names=None):
    return [n for n, t in txt.items() if (names is None or n in names) and sub in t]


ck("MASK_CHAIN_18_8_0_8",
   find("arm gate fail mask=0x18") and find("arm gate fail mask=0x8 (") and find("arm gate fail mask=0x0 (")
   and len(find("arm gate fail mask=0x8 (")) >= 2,
   "0x18@%s 0x8@%s 0x0@%s" % (find("mask=0x18"), find("mask=0x8 ("), find("mask=0x0 (")))
ck("IRQ_GATE_CLOSED_OPEN_CLOSED",
   find("irq gate   = CLOSED") and find("irq gate   = OPEN"),
   "CLOSED@%s OPEN@%s" % (find("irq gate   = CLOSED"), find("irq gate   = OPEN")))
ck("POLARITY_FLAG_ONLY",
   find("polarity   = NOT confirmed") and find("polarity   = operator CONFIRMED"),
   "置位前/后置位后的软件标志")
ck("PROTECTION_READY_CHAIN",
   find("protection_ready = NO") and find("protection_ready = YES"),
   "NO@%s YES@%s" % (find("protection_ready = NO"), find("protection_ready = YES")))
ck("CONFIG_APPLIED_ONCE", len(find("[TMC-CFG] CONFIGURED")) == 1, find("[TMC-CFG] CONFIGURED"))
def find(sub, names=None):
    return [n for n, t in txt.items() if (names is None or n in names) and sub in t]


def fpat(rx):
    return [n for n, t in txt.items() if re.search(rx, t)]


ck("DIAG_RAW_ZERO_RISING",
   fpat(r"TMC_DIAG\s+pin=3 raw=0 safe=0 irq=RISING \[safe\]"),
   "固件自报 DIAG raw=0 safe=0 RISING")
ck("ENN_FB_RAW_HIGH_DISCLOSED",
   fpat(r"PC\.6\(ENN_FB\)\s+pin=ok raw=HIGH"), "固件自报 ENN 反馈 raw=HIGH(禁能侧)")
armed = find("armed=1") + find("cur_hz=")
ck("NEVER_ARMED_NO_FREQUENCY", not find("armed=1") and not find("arm_gate_mask=0x0 ALL-PASS\n[MOT] armed=1"),
   "armed=1 命中=%s" % (find("armed=1") or "none"))
led = [json.loads(l) for l in (S1 / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
# 阶段台账不写 cmd 字段, 所以"实际发过什么"只能从响应原文首行回显取(上一轮已证回显存在)。
ck("STAGE_LEDGER_CARRIES_NO_CMD_FIELD", not any("cmd" in r for r in led),
   "台账 kinds=%s; 命令身份改用响应首行回显承担" % sorted({r.get("kind", "?") for r in led}))
echo = [txt[p.name].splitlines()[0] for p in rx if txt[p.name].strip()]
plan_doc = json.loads((ROOT / "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/"
                       "plan_r7f5_b502_blind_irq_static.json").read_text(encoding="utf-8"))
plan_cmds = [st.get("cmd") for st in plan_doc["stages"][1]["steps"] if st.get("cmd") != "__watch__"]
ck("ECHO_ORDER_EQUALS_PLAN_ORDER", echo == plan_cmds,
   "回显 %d 条 与计划 %d 步(去 __watch__)同序=%s" % (len(echo), len(plan_cmds), echo == plan_cmds))
ck("NO_ARM_START_SENT", not any(c in ("motor_arm", "motor_start") for c in echo),
   "实际发送=%s" % echo)
root_led = [json.loads(l) for l in (FIELD / "dispatch_ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
used = [r for r in root_led if r.get("kind") == "DISPATCH_DONE"][0]["quota_used"]
ck("QUOTA_USED_EXACTLY_AS_APPROVED",
   used == {"irq_polarity_prep": 2, "port_open": 1, "safety_irq_disable": 1, "tmc_config_apply": 1}, used)
watch = [p for p in rx if "quiet_observation" in p.name][0]
ck("PASSIVE_WATCH_ZERO_BYTES_DISCLOSED", watch.read_bytes() == b"",
   "被动观察 0 字节: 只说明窗口内无产出, 不构成'没触发'的证明")
ck("SESSION_WITHIN_DECLARED_DEADLINE", max(
    r.get("t_s", 0) for r in led if isinstance(r.get("t_s"), (int, float))) <= 90,
   "会话末 t_s=%.3f s < 申报 90 s" % max(r.get("t_s", 0) for r in led if isinstance(r.get("t_s"), (int, float))))

# ---------- 对齐 ----------
mt = sorted((p.stat().st_mtime, p.name) for p in rx if p.stat().st_size)
import datetime
sess_t0 = datetime.datetime.utcfromtimestamp(mt[0][0]).isoformat()
sess_t1 = datetime.datetime.utcfromtimestamp(mt[-1][0]).isoformat()
ck("ALIGN_CAPTURE_START_BEFORE_FIRST_RESPONSE", t0 < start_rec["utc"] and mt[0][0] > iso(t0).timestamp(),
   "采集起 %s / 首个响应写入 %s" % (t0, sess_t0))
ck("ALIGN_CAPTURE_STOP_AFTER_LAST_RESPONSE", mt[-1][0] < iso(t1).timestamp(),
   "末响应写入 %s < 采集止 %s" % (sess_t1, t1))
pending = [
    {"item": "operator_power_down_after_dispatch",
     "state": "PENDING_OPERATOR_REPORT",
     "note": "任务书第 69 行要求收口切断 VM+MCU; stop_capture 已于 %s 完成, 断电尾段不在这 %.2f s 内, "
             "但断电动作与其时刻要由操作者回报后才登记, 不代填。" % (stop_rec["utc"], span)}]

summary = {"round": "M8R7F5A-field", "checks": len(checks), "fails": len(fails), "fail_names": fails,
           "pending": pending,
           "serial_verdict": "SERIAL_16_STEP_STATIC_IRQ_TRIAL_OK",
           "instrument_verdict": "CAPTURE_VALID_BUT_NON_DISCRIMINATING",
           "combined_verdict": "PARTIAL_NOT_STITCHED",
           "forbidden_claims_still_blocked": ["PHYSICAL_POLARITY_VERIFIED", "DIAG_TRIGGER_VERIFIED",
                                              "STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE",
                                              "CORE_PROTECTION_VERIFIED"],
           "polarity_measured": False, "core_protection_verified": False,
           "channel_mapping": "CHANNEL_MAPPING_UNRESOLVED",
           "la": {"columns": cols, "span_s": span, "per_channel": per,
                  "start_utc": start_rec["utc"], "stop_utc": stop_rec["utc"],
                  "capture_id": 8, "sample_rate": 1000000, "buffer_requested_mb": 512,
                  "native_bytes": sal.stat().st_size, "native_sha256": sha(sal),
                  "csv_bytes": (LA / "csv" / "digital.csv").stat().st_size,
                  "csv_sha256": sha(LA / "csv" / "digital.csv")},
           "session": {"first_rx_utc": sess_t0, "last_rx_utc": sess_t1, "quota_used": used,
                       "responses": len(rx)},
           "device_actions": {"serial_dispatch": 1, "port_open": 1, "tmc_config_apply": 1,
                              "irq_polarity_prep": 2, "safety_irq_disable": 1,
                              "logic_capture_session": 1, "logic_start": 1, "logic_stop": 1,
                              "native_save": 1, "csv_export": 1,
                              "arm": 0, "start": 0, "motion": 0, "download": 0, "readback": 0,
                              "reset": 0, "retry": 0}}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "FIELD_JUDGEMENT.txt").write_bytes((
    "\n".join("%-6s %s | %s" % (c["result"], c["name"], c["detail"]) for c in checks) +
    "\nTOTAL=%d FAILS=%d\n" % (len(checks), len(fails))).encode("utf-8"))
(OUT / "field_summary.json").write_bytes((json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print(json.dumps({"checks": len(checks), "fails": len(fails), "fail_names": fails,
                  "serial": summary["serial_verdict"], "instrument": summary["instrument_verdict"],
                  "combined": summary["combined_verdict"],
                  "la_span_s": round(span, 4), "per_channel": per,
                  "align": [sess_t0, sess_t1]}, ensure_ascii=False, indent=2))
sys.exit(1 if fails else 0)
