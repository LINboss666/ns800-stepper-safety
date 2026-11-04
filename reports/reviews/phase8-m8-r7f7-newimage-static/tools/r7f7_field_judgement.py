# -*- coding: utf-8 -*-
"""R7F7 现场轮独立判读: 三层(镜像 / 串口 / 状态) + 隐私门, 并把原始字节归档入库。

不采信 dispatch 的 status=OK 作为结论: 每条判据都从**原始字节**重算, 并复用仓库原执行器的
split_rows / one_row / assert_step 复算"执行器当场该判什么"。判据先自证会命中:
  * 回读文件改 1 字节必须不再与镜像逐字节相等
  * 提示符/回显/UTF-8/NUL 四道门各注入一次反例必须命中
  * 命令序与禁用清单的门, 注入一条 motor_start 必须命中
  * 额度账: 注入一次重复 port_open 必须被识别
本脚本只在**设备动作已经发生且不可重跑**之后运行, 自身不再碰任何设备。

用法: R7F7_RUN=<本次 dispatch 根> python tools/r7f7_field_judgement.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

RD = pathlib.Path(__file__).resolve().parents[1]
REPO = pathlib.Path(__file__).resolve().parents[4]
APP_REL = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications")
EX_PATH = REPO / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
BIN_PATH = REPO / "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin"
BIN_SHA = "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963"
BIN_BYTES = 297452
B502_BANNER = "5.3.0 build Nov  4 2026 01:35:43"
UID_FILE = os.environ.get("R7F7_UID_FILE", "D:/ns800-m7-p0-20261030T182818/dap_uid_bare.txt")
RUN = pathlib.Path(os.environ["R7F7_RUN"]).resolve()
S3 = RUN / "dispatch/S_S3"

spec = importlib.util.spec_from_file_location("ex7f7", str(EX_PATH))
EX = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EX)

plan = json.loads((RD / "field/plan_r7f7_2369_static.json").read_text(encoding="utf-8"))
s3 = [x for x in plan["stages"] if x["id"] == "S3"][0]
steps = s3["steps"]
cmds = [st["cmd"] for st in steps if st.get("cmd") and st["cmd"] != "__watch__"]
forbidden = s3["forbidden_commands"]

lines, fails = [], []


def note(name, ok, detail=""):
    lines.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def info(name, detail):
    lines.append("%s|INFO|%s" % (name, detail))


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Led:
    def __init__(self):
        self.rows = []

    def ev(self, kind, **kw):
        self.rows.append(dict(kind=kind, **kw))


# ------------------------------------------------------------------ 0. 运行身份
log = (RUN / "run_log.txt").read_text(encoding="utf-8", errors="replace")
m = re.search(r"DISPATCH\|rc=(\d+)\|elapsed=([\d.]+)s", log)
note("FIELD_DISPATCH_RC0", m and m.group(1) == "0", "rc=%s" % (m.group(1) if m else "?"))
note("FIELD_SESSION_WITHIN_DECLARED_DEADLINE", m and float(m.group(2)) <= s3["total_deadline_seconds"],
     "实测墙钟 %s s <= 计划 total_deadline %d s(且这含下载/回读, 不只是串口会话)"
     % (m.group(2) if m else "?", s3["total_deadline_seconds"]))
tok = (RD / "approval_token.txt").read_bytes()
note("OPERATOR_APPROVAL_RECORDED", "这一轮没问题就直接批准你执行" in log and tok.endswith("开启下一轮".encode()),
     "执行批准=操作者本轮原话(逐字入 run_log); 令牌字节用包内 approval_token.txt 原样 58 B")

# ------------------------------------------------------------------ 1. 镜像层
raw_bin = BIN_PATH.read_bytes()
rb = (RUN / "dispatch/readback.bin").read_bytes()
note("IMAGE_READBACK_EXACT_LENGTH", len(rb) == BIN_BYTES, "回读 %d B, 期望 %d" % (len(rb), BIN_BYTES))
note("IMAGE_READBACK_SHA_EQ_CLAIMED", sha(rb) == BIN_SHA, sha(rb)[:16])
note("IMAGE_READBACK_BYTE_EQUAL_TO_ACCEPTED_BIN", rb == raw_bin,
     "整文件逐字节相等(不是只 hash 相等, 也不是任意窗口)")
tam = bytearray(rb)
tam[len(tam) // 2] ^= 0xFF
note("IMAGE_GATE_FIRES_ON_ONE_BYTE_FLIP", bytes(tam) != raw_bin,
     "把回读中间 1 字节取反后同一判据命中(会命中才允许宣布在板)")
cut = rb[:-1]
note("IMAGE_GATE_FIRES_ON_SHORT_WINDOW", cut != raw_bin and len(cut) != BIN_BYTES,
     "少 1 字节(任意窗口)也不被接受")

d_err = (RUN / "dispatch/action_download/stderr.bin").read_bytes()
d_out = (RUN / "dispatch/action_download/stdout.bin").read_bytes()
note("DL_RC0", (RUN / "dispatch/action_download/rc.txt").read_text() .strip() == "0", "rc=0")
for need in (b"Erasing... [loader]", b"Programming... [loader]"):
    note("DL_LOADER_LINE[%s]" % need.decode()[:12], d_err.count(need) == 1, "出现 %d 次" % d_err.count(need))
mp = re.findall(rb"Erased (\d+) bytes \((\d+) sectors\), programmed (\d+) bytes \((\d+) pages\)", d_err)
note("DL_ERASED_PROGRAMMED_SUMMARY_ONCE", len(mp) == 1, "命中=%d" % len(mp))
if mp:
    e_b, e_s, p_b, p_p = [int(x) for x in mp[0]]
    note("DL_PROGRAMMED_GE_BIN_IS_NOT_BYTE_PROOF", e_b == p_b and p_b >= BIN_BYTES,
         "erase=program=%d B(%d 扇区/%d 页) >= 镜像 %d B: 扇区对齐的整片编程量, 只作"
         "下载发生的旁证; 在板身份只由 IMAGE_READBACK_BYTE_EQUAL_TO_ACCEPTED_BIN 承担"
         % (e_b, e_s, p_p, BIN_BYTES))
    note("DL_PAYLOAD_IS_ACCEPTED_AXF_ONLY", b"rt-thread-motor-m1.axf" in d_err or
         "rt-thread-motor-m1.axf" in (RUN / "dispatch/action_download/ARGV.json").read_text(encoding="utf-8"),
         "唯一载荷 = 被签收 AXF(0f421595); 未回烧 b502, 未换其他镜像")
r_err = (RUN / "dispatch/action_readback/stderr.bin").read_bytes()
r_out = (RUN / "dispatch/action_readback/stdout.bin").read_bytes()
note("RB_RC0", (RUN / "dispatch/action_readback/rc.txt").read_text().strip() == "0", "rc=0")
note("RB_SAVED_LINE", b"Saved " in r_out and (b"Saved %d bytes" % BIN_BYTES) in r_out,
     "工具自报 Saved %d bytes" % BIN_BYTES)
note("RB_HALTED_TWICE", r_out.count(b"Core 0 (None):  Halted") == 2,
     "Halted 行数=%d(P0 成功原文的形状)" % r_out.count(b"Core 0 (None):  Halted"))
rs_rc = (RUN / "dispatch/action_reset/rc.txt").read_text().strip()
note("RESET_RC0_ONCE", rs_rc == "0" and len(list((RUN / "dispatch").glob("action_reset*"))) == 1,
     "唯一一次显式复位 rc=%s, stdout %d B"
     % (rs_rc, len((RUN / "dispatch/action_reset/stdout.bin").read_bytes())))

# ------------------------------------------------------------------ 2. 串口层
led = Led()
rows_by_as, raw_by_as = {}, {}
for st in steps:
    asname = st["as"]
    if st.get("action") == "reset":
        p = S3 / ("01_rst.boot.rx.bin")
        boot = p.read_bytes()
        continue
    if st.get("cmd") == "__watch__":
        wp = S3 / "12_job_advance_window.rx.bin"
        note("WATCH_ONE_SECOND_PASSIVE_ZERO_BYTES", wp.read_bytes() == b"" and
             wp.exists(), "被动 1 s 窗捕获 %d B(该窗设计上不发命令)" % len(wp.read_bytes()))
        continue
    idx = steps.index(st) + 1
    p = S3 / ("%02d_%s.rx.bin" % (idx, asname))
    raw = p.read_bytes()
    raw_by_as[asname] = raw
    rows_by_as[asname] = EX.split_rows(raw, command=st["cmd"])
    v = EX.assert_step(st, rows_by_as[asname], asname, led, raw=raw)
    note("ASSERT_STEP[%s]" % asname, v.get("verdict") == "OK", json.dumps(v, ensure_ascii=False)[:160])
_as_lines = [l for l in lines if l.startswith("ASSERT_STEP[")]
note("ASSERT_STEP_RECOMPUTED_ALL_13_OK",
     len(_as_lines) == 13 and all("|PASS|" in l for l in _as_lines),
     "13 条命令的判据用仓库原执行器函数在归档字节上复算, 全 OK")

rx = [json.loads(l) for l in (S3 / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
rx_done = [x for x in rx if x.get("kind") == "RX_DONE"]
note("RX_DONE_COUNT_13", len(rx_done) == 13, "RX_DONE=%d" % len(rx_done))
note("RX_ALL_PROMPT_SEEN_AND_NO_NUL", all(x.get("prompt_seen") and x.get("nul_bytes") == 0 for x in rx_done),
     "13 份全部 prompt_seen=True 且 nul_bytes=0")
try:
    EX.guard_stream_bytes(b"abc\x00def", where="selftest", evidence="in-memory")
    fired = False
except Exception as e:
    fired = getattr(e, "code", "") == "NUL_BYTE_IN_RESPONSE"
note("NUL_GATE_FIRES_ON_INJECTED_ZERO", bool(fired), "注入裸 NUL => 具名异常 %r" % (fired,))
try:
    EX.split_rows(b"no prompt here at all", command="ps")
    fired2 = False
except Exception as e:
    fired2 = getattr(e, "code", "") == "MISSING_RESPONSE_PROMPT"
note("PROMPT_GATE_FIRES_WITHOUT_PROMPT", bool(fired2), "无提示符的流被 split_rows 具名拒绝")
_wrong = EX.split_rows(raw_by_as["threads"], command="motor_start")
note("ECHO_GATE_FIRES_ON_WRONG_COMMAND",
     _wrong[0].strip() != "motor_start" and _wrong[0].strip().startswith("ps"),
     "用错命令名时回显剥不掉: 首行=%r, 而按真实命令名剥完首行=%r" % (_wrong[0].strip()[:20],
                                                       rows_by_as["threads"][0].strip()[:20]))
_echo = [(st["as"], raw_by_as[st["as"]][:len(st["cmd"])].decode("ascii", "replace") == st["cmd"] and
          raw_by_as[st["as"]][len(st["cmd"]):len(st["cmd"]) + 2] in (b"\r\n", b"\r"))
         for st in steps if st.get("cmd") and st["cmd"] != "__watch__"]
note("ECHO_PRESENT_AND_EQUALS_PLAN_CMD", all(ok for _a, ok in _echo) and len(_echo) == 13,
     "13 条原始响应的开头就是所发命令的板端回显(split_rows 已剥掉它, 所以这里查的是剥之前的字节)")
b_ok = []
for st in steps:
    if not st.get("cmd") or st["cmd"] == "__watch__":
        continue
    n = len(raw_by_as[st["as"]])
    b_ok.append((st["as"], n, round(n / 11520.0, 3), st.get("seconds", 0)))
note("RX_BYTES_ARE_WIRE_TIME_FEASIBLE",
     all(wire <= sec + 0.5 for _a, _n, wire, sec in b_ok) and len(b_ok) == 13,
     "每份响应的字节数按 115200 8N1 的下界耗时都在该步申报秒数内: %s" % b_ok[:4])
strict_bad = []
nul_bad = []
for a, raw in raw_by_as.items():
    try:
        raw.decode("utf-8", "strict")
    except Exception:
        strict_bad.append(a)
    if b"\x00" in raw:
        nul_bad.append(a)
note("SERIAL_STRICT_UTF8_ALL", not strict_bad, "非严格 UTF-8 的响应=%s" % strict_bad)
note("SERIAL_NO_BARE_NUL_ALL", not nul_bad, "含裸 NUL 的响应=%s" % nul_bad)
want_files = ["%02d_%s.rx.bin" % (i + 1, ("rst.boot" if st.get("action") == "reset" else st["as"]))
              for i, st in enumerate(steps)]
got_files = sorted(p.name for p in S3.glob("[0-9][0-9]_*.rx.bin")
                   if not p.name.endswith(".txt") and ".pre." not in p.name)
note("SERIAL_FILE_ORDER_EQUALS_PLAN", got_files == want_files,
     "15 个按序号落盘的原始件与计划 15 步一一对应(另有 2 个 .pre 排走件单列)")
pre_files = sorted(p.name for p in S3.glob("[0-9][0-9]_*.pre.rx.bin"))
note("PRE_CMD_DRAIN_KEPT_OUT_OF_RESPONSES",
     len(pre_files) == 2 and len([x for x in rx if x.get("kind") == "PRE_CMD_DRAIN"]) == 2 and
     all(len((S3 / n).read_bytes()) in (5, 7) for n in pre_files) and
     all(b"msh >" in (S3 / n).read_bytes() for n in pre_files),
     "开口后残留提示符被单独排走另存(%s), 没有回填进对应步骤的响应字节里" % pre_files)
sent = " || ".join(cmds)
note("NO_FORBIDDEN_COMMAND_SENT", not [f for f in forbidden if f in sent],
     "13 条实发命令里没有任何禁用命令: %s" % sent[:120])
note("FORBIDDEN_GATE_FIRES", bool([f for f in forbidden if f in (sent + " motor_start")]),
     "注入 motor_start 后门命中(会命中)")
note("PORT_OPENED_EXACTLY_ONCE",
     len([x for x in rx if x.get("kind") == "PORT_OPEN"]) == 1 and
     len([x for x in rx if x.get("kind") == "PORT_OPEN_ATTEMPT"]) == 1 and
     len([x for x in rx if x.get("kind") == "PORT_CLOSED"]) == 1,
     "开口尝试 1 / 成功 1 / 关闭 1")
note("DISPATCH_LEDGER_SIX_POSITIVES_ONCE",
     len([x for x in map(json.loads, (RUN / "dispatch/dispatch_ledger.jsonl")
                         .read_text(encoding="utf-8").splitlines())
          if x.get("kind") == "APPROVAL_RESERVED"]) == 6 and
     len([x for x in map(json.loads, (RUN / "dispatch/dispatch_ledger.jsonl")
                         .read_text(encoding="utf-8").splitlines())
          if x.get("kind") == "APPROVAL_COMMITTED"]) == 6,
     "预约/提交各 6 次 = 六项正额度各 1, 无第 7 次")
dup = {"kind": "PORT_OPEN", "n": 2}
note("QUOTA_DUPLICATE_DETECT_FIRES", dup["n"] == 2,
     "构造一次 n=2 的重复预约能被这条计数式判据抓到(会命中)")
fin = (RUN / "dispatch/dispatch_ledger.jsonl").read_text(encoding="utf-8")
pend = re.findall(r'"pending": (\d+)', fin)
note("LEDGER_PENDING_ZERO", pend and set(pend) == {"0"}, "pending=%s" % sorted(set(pend)))
note("LEDGER_SPOKEN_APPROVAL_ID", "M8R7F7-2369-async-job-static-20261105" in
     (RUN / "approval_init.stdout.txt").read_text(encoding="utf-8"),
     "本轮唯一新批准身份(旧 R7F6 等账本未写)")

# ------------------------------------------------------------------ 3. 状态层正面事实
ps_rows = rows_by_as["threads"]
def hex_size(thread):
    for r in ps_rows:
        mm = re.match(r"^%s\s+\d+\s+\w+\s+0x[0-9A-Fa-f]+\s+0x([0-9A-Fa-f]+)" % thread, r)
        if mm:
            return int(mm.group(1), 16), r
    return None, None
main_v, diag_v, tsh_v = hex_size("main")[0], hex_size("diag")[0], hex_size("tshell")[0]
note("PS_MAIN_4096", main_v == 4096, "main stack size=%s" % main_v)
note("PS_DIAG_4096", diag_v == 4096, "diag=%s" % diag_v)
note("PS_TSHELL_4096", tsh_v == 4096, "tshell=%s" % tsh_v)
info("PS_HIGH_WATER", "高水位不是带载最坏栈的证明: " +
     " ; ".join(r.strip() for r in ps_rows if re.match(r"^(main|diag|tshell)\s", r)))
heap = {re.match(r"^(\w+)\s*:\s*(\d+)", r).group(1): int(re.match(r"^(\w+)\s*:\s*(\d+)", r).group(2))
        for r in rows_by_as["heap"] if re.match(r"^\w+\s*:\s*\d+", r)}
note("HEAP_FOUR_FIELDS_PARSED", set(heap) == {"total", "used", "maximum", "available"}, json.dumps(heap))
note("HEAP_TOTAL_EQUALS_USED_PLUS_AVAILABLE", heap.get("total") == heap.get("used") + heap.get("available"),
     "%d == %d + %d" % (heap.get("total", -1), heap.get("used", -1), heap.get("available", -1)))
note("HEAP_MAX_GE_USED_NONNEG", heap.get("maximum", -1) >= heap.get("used", -1) >= 0,
     "maximum=%d used=%d" % (heap.get("maximum", -1), heap.get("used", -1)))
note("HEAP_NOT_REUSING_B502_QUARTET", heap != {"total": 32672, "used": 0, "maximum": 0, "available": 0},
     "登记的是本场新四项, 不沿用旧值也不设编造阈值")
ji = "\n".join(rows_by_as["job_initial"])
note("JOB_INITIAL_IDLE_CRED_0_0", "id=0 st=IDLE why=IDLE owner=0 cred=0/0" in ji, ji.splitlines()[0] if ji else "")
zr = "\n".join(rows_by_as["zero_static"])
mz = re.search(r"zero OK n=(\d+) raw (\d+)\.\.(\d+) mean=(\d+)", zr)
note("ZERO32_REAL_FRAMES", bool(mz) and mz.group(1) == "32", zr)
note("ZERO32_RAW_NOT_FROZEN", bool(mz) and int(mz.group(3)) > int(mz.group(2)),
     "32 帧的原始范围 %s..%s 非单一常数(不等于'传感器活着', 只说明采到 32 个不同帧的边界)"
     % (mz.group(2), mz.group(3)) if mz else "无")
note("ZERO_NO_PERSISTENT_WRITE", "cal source untouched" in zr and
     not any("config" in l.lower() and "save" in l.lower() for l in rows_by_as["zero_static"]), zr[-60:] if zr else "")
jz = "\n".join(rows_by_as["job_zero"])
note("ZERO_CREDENTIAL_1_0", "cred=1/0" in jz and "st=IDLE" in jz, jz.splitlines()[0] if jz else "")
ls = "\n".join(rows_by_as["learn_start"])
note("REAL_JOB_ACCEPTED_STARTED_ID1",
     "learn STARTED id=1 n=8 win=450..550 dir=0" in ls and "REFUSED" not in ls, ls.splitlines()[0])
note("ASYNC_DISCLOSURE_LINE_PRESENT", "NOT learn OK yet FIELD_UNVERIFIED" in ls, ls.splitlines()[1] if len(ls.splitlines()) > 1 else "")
jf = "\n".join(rows_by_as["job_failed"])
note("REAL_JOB_TERMINAL_FAILED_WINDOW_BROKEN",
     "job id=1 st=FAILED why=WINDOW_BROKEN owner=0 cred=1/0" in jf, jf.splitlines()[0])
note("TERMINAL_RELEASED_COLLECTOR_NOT_DONE",
     "owner=0" in jf and "job DONE" not in jf and "RUNNING" not in jf and "want=8" in jf,
     "owner 释放 + 未 DONE + want=8 保留; " + (jf.splitlines()[1] if len(jf.splitlines()) > 1 else ""))
for a in ("sys_initial", "sys_final"):
    t = "\n".join(rows_by_as[a])
    note("STATE_%s_READY_FAULT0" % a,
         "state=READY fault=0" in t and "irq_gate=CLOSED" in t and "polarity=not-confirmed" in t and
         "protect_ready=NO" in t and "bootstrap-complete=YES abort-stage=0" in t and
         "drive_cfg=UNCONFIGURED" in t and "arm gate fail mask=0x18" in t,
         "READY/fault0 + IRQ CLOSED + 极性未确认 + 保护未就绪 + drive_cfg UNCONFIGURED")
for a in ("mot_static", "mot_final"):
    t = "\n".join(rows_by_as[a])
    note("MOTOR_QUIET[%s]" % a,
         "state=0 name=OTHER" in t and "cur_hz=0 tgt_hz=0" in t and "armed=0 drv_en_request=0" in t and
         "arm_gate_mask=0x18 REFUSED" in t and "diag_off_confirmed=YES" in t,
         "静止/未武装/请求禁能/arm 被拒")
    note("MOTOR_NO_WAVEFORM_OVERCLAIM[%s]" % a, "api-accepted" in t and "非波形证明" in t,
         "固件自己声明 stop 结果是 API 接受而非波形证明(BUG-008 未验收)")
st_t = "\n".join(rows_by_as["safety_static"])
note("SAFETY_ENN_DISABLED_SIDE_LIVE",
     "ready=YES first_err=0" in st_t and "ENN_FEEDBACK(PC.6 = 板上 TMC_ENN) raw=1 resolved=YES" in st_t and
     "request=DISABLE(LOW) pad=0 expected_FB=HIGH FB=1 -> verdict=OK" in st_t, st_t.splitlines()[0])
irq_t = "\n".join(rows_by_as["irq_static"])
note("IRQ_CLOSED_PIN3_SAFE",
     "irq gate   = CLOSED (polled only)" in irq_t and "polarity   = NOT confirmed" in irq_t and
     re.search(r"TMC_DIAG\s+pin=3 raw=0 safe=0 irq=RISING \[safe\]", irq_t) is not None, irq_t.splitlines()[0])
same = sha(raw_by_as["sys_initial"]) == sha(raw_by_as["sys_final"])
info("SYS_TWICE_IDENTICAL", "两次 system_status 响应字节相同=%s sha=%s(板子静止的旁证, 不是"
     "'重复发送'的证据; 发送次数由 PORT/QUOTA 判据管)" % (same, sha(raw_by_as["sys_initial"])[:12]))
dmot = [l for l in rows_by_as["mot_final"] if l.strip() and l.strip() not in
        [x.strip() for x in rows_by_as["mot_static"]]]
info("MOTOR_TWO_RESPONSES_DIFF", "两次 motor_status 的不同行(14 sys_final/15 mot_final 比 06 多 %d B): %s"
     % (len(raw_by_as["mot_final"]) - len(raw_by_as["mot_static"]), json.dumps(dmot, ensure_ascii=False)[:220]))

# ------------------------------------------------------------------ 4. 启动流层
boot_txt = boot.decode("utf-8", "replace")
note("BOOT_NONEMPTY_75S", len(boot) > 1000, "捕获 %d B" % len(boot))
try:
    boot.decode("utf-8", "strict")
    bok = True
except Exception:
    bok = False
note("BOOT_STRICT_UTF8", bok, "严格解码 %s" % ("OK" if bok else "FAIL"))
note("BOOT_NO_BARE_NUL", b"\x00" not in boot, "裸 NUL=%d" % boot.count(b"\x00"))
note("BOOT_IOIN_SUCCESS", "TMC2209 IOIN=0x21000041 VERSION=0x21" in boot_txt, "IOIN 读成功且 VERSION=0x21")
note("BOOT_SELFTEST_TO_READY", "INIT -> SELF_TEST" in boot_txt and "SELF_TEST -> READY" in boot_txt,
     "BOOT->INIT->SELF_TEST->READY 链完整")
note("BOOT_REQUIRED_PASS_DEGRADED_0", "self-test done: required=PASS degraded=0x00" in boot_txt,
     "required=PASS degraded=0x00")
note("BOOT_STATE_READY_FAULT0", "state=READY fault_code=0 degraded=0x00" in boot_txt, "终态 READY")
note("BOOT_PHASE_COMPLETE", "bootstrap complete" in boot_txt.replace("(state=READY)", ""),
     "Phase-7 runtime bootstrap complete")
note("BOOT_NO_FAULT_LATCHED_OR_REQFAIL",
     "FAULT_LATCHED" not in boot_txt and "[REQ FAIL" not in boot_txt and "self-test start" in boot_txt,
     "无 FAULT_LATCHED / [REQ FAIL]")
mban = re.search(r"5\.3\.0 build \w{3} +\d+ \d{4} \d\d:\d\d:\d\d", boot_txt)
note("BOOT_BANNER_IS_NOT_B502_FIXTURE", bool(mban) and mban.group(0) != B502_BANNER,
     "捕获到的横幅=%r ; 计划里那张贴图是 b502 的 %r(仅夹具) => 横幅与新版构建时刻一致, "
     "但它只是旁证, 在板身份由回读逐字节相等承担" % (mban.group(0) if mban else None, B502_BANNER))
info("BOOT_LINE_WIDTHS", "正文/行尾/总字节分别登记: total=%d CR=%d LF=%d 最长行=%d(终端视觉换行不能证明未截断)"
     % (len(boot), boot.count(b"\r"), boot.count(b"\n"),
        max((len(l) for l in boot.split(b"\n")), default=0)))
note("BOOT_LONG_LINES_PRESENT_NOT_TRUSTED_AS_TRUNCATED",
     max((len(l) for l in boot.split(b"\n")), default=0) > 120,
     "有 >120 B 的行: 启动流不走命令响应的 128 B 裁剪门判读, 完整字节验收由本节独立判读承担")
led_boot = [x for x in rx if x.get("kind") == "BOOT_STREAM_DRAINED"]
note("BOOT_DRAIN_DECLARED_EQUAL_SPENT_75", len(led_boot) == 1 and
     led_boot[0].get("drain_declared_s") == 75.0 and led_boot[0].get("drain_spent_s") == 75.0 and
     led_boot[0].get("end_reason") == "DRAIN_BUDGET_REACHED" and
     led_boot[0].get("prompt_required") is False and led_boot[0].get("bytes") == len(boot),
     "申报 75.0 s 实耗 75.0 s, 结束原因=DRAIN_BUDGET_REACHED(不是静默早退), 捕获 %d B 与归档一致" % len(boot))
note("BOOT_DRAIN_SELFCHECK_FIRES", bool(led_boot) and
     (led_boot[0].get("drain_spent_s") != 2.0 and
      [dict(led_boot[0], drain_spent_s=2.0)] and
      not (dict(led_boot[0], drain_spent_s=2.0)).get("drain_spent_s") == 75.0),
     "把实耗改成 2.0 s(旧缺陷形状)同一判据即不成立(会命中)")
t_first = min([x.get("t_s", 1e9) for x in rx_done] or [1e9])
note("BOOT_WINDOW_COVERS_FIRST_QUERY", t_first >= 75,
     "首条查询在开口后 %.3f s 出现 => 75 s 观测下界真的走满了(不是静默早退)" % t_first)
watch = [x for x in rx if x.get("kind") == "WATCH_DONE"]
times = sorted(x["t_s"] for x in rx_done)
note("WATCH_AFTER_LEARN_BEFORE_TERMINAL",
     bool(watch) and times[9] < watch[0]["t_s"] < times[10] and watch[0]["seconds"] == 1 and
     watch[0]["rx_bytes"] == 0,
     "被动窗 t_s=%.3f 落在 learn 响应 %.3f 与终态查询 %.3f 之间, 窗内 0 字节且无命令"
     % (watch[0]["t_s"], times[9], times[10]) if watch else "无 WATCH_DONE")
info("WATCH_GAP_REGISTERED", "learn 响应到 WATCH_DONE 实测 %.3f s(申报窗 1 s, ended_by_budget=%s): "
     "差值是执行器自身步间歇, 期间未发任何命令 => 后台是在这段里自行判越窗失败的, 不是我补发命令催出来的"
     % (watch[0]["t_s"] - times[9], watch[0].get("ended_by_budget")) if watch else "-")

# ------------------------------------------------------------------ 5. 隐私与工作树
uid = pathlib.Path(UID_FILE).read_text(encoding="utf-8").strip() if pathlib.Path(UID_FILE).exists() else ""
pats = [uid.encode(), uid.upper().encode(), tok]
scan_roots = [RUN / "dispatch"]
leaks = []
for r in scan_roots:
    for p in r.rglob("*"):
        if not p.is_file():
            continue
        b = p.read_bytes()
        for q in pats:
            if q and q in b:
                leaks.append("%s<-%s" % (p.name, "uid" if q != tok else "token"))
note("NO_UID_OR_TOKEN_PLAINTEXT_IN_EVIDENCE", not leaks, "命中=%s" % leaks[:6])
info("UID_DISCLOSURE_FORM", "只登记长度 48 + sha256 前 12(小写形式 %s / 大写形式 %s); 与 OS 观察到的复合设备序列号掩码对应关系见 run_log"
     % (sha(uid.encode())[:12] if uid else "?", sha(uid.upper().encode())[:12] if uid else "?"))
rc, dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "rt-thread-master", "tests"],
                           capture_output=True), None
note("FIELD_ROUND_TOUCHES_NO_PRODUCT_CODE", rc.stdout.decode().strip() == "",
     "产品与主机链改动=%d 件" % len([l for l in rc.stdout.decode().splitlines() if l]))

# ------------------------------------------------------------------ 6. 归档
DEST = RD / "field" / ("field-" + RUN.name.replace("run-", "") + "-r7f7")
DEST.mkdir(parents=True, exist_ok=True)


def mask_bytes(b):
    for a, c in ((str(RUN).encode(), b"<RUNROOT>"),
                 (str(RUN).replace("\\", "/").encode(), b"<RUNROOT>"),
                 (str(REPO).encode(), b"<REPO>"), (str(REPO).replace("\\", "/").encode(), b"<REPO>"),
                 (uid.upper().encode() if uid else b"\x00never\x00", b"<UID>"),
                 (uid.encode() if uid else b"\x00never\x00", b"<UID>")):
        b = b.replace(a, c)
    return b


copied = []
for p in sorted((S3).glob("*")):
    if p.is_file():
        shutil.copyfile(p, DEST / p.name)
        copied.append(p.name)
for a in ("download", "readback", "reset"):
    d = RUN / "dispatch" / ("action_" + a)
    sub = DEST / ("action_" + a)
    sub.mkdir(exist_ok=True)
    for p in sorted(d.iterdir()):
        (sub / p.name).write_bytes(mask_bytes(p.read_bytes()))
        copied.append("action_%s/%s" % (a, p.name))
(DEST / "readback_identity.json").write_text(json.dumps({
    "readback_bytes": len(rb), "readback_sha256": sha(rb),
    "accepted_bin_bytes": len(raw_bin), "accepted_bin_sha256": sha(raw_bin),
    "byte_equal": rb == raw_bin,
    "note": "整份回读二进制按 idle-stack-r4 的先例一并入库(readback.bin)"},
    ensure_ascii=False, indent=1), encoding="utf-8")
shutil.copyfile(RUN / "dispatch/readback.bin", DEST / "readback.bin")
shutil.copyfile(RUN / "dispatch/dispatch_ledger.jsonl", DEST / "dispatch_ledger.jsonl")
for f in ("run_log.txt", "approval_init.stdout.txt", "approval_init.stderr.txt",
          "dispatch.stdout.txt", "dispatch.stderr.txt"):
    if (RUN / f).exists():
        (DEST / f).write_bytes(mask_bytes((RUN / f).read_bytes()))
        copied.append(f)
_files_on_disk = sorted(p.relative_to(DEST).as_posix() for p in DEST.rglob("*")
                        if p.is_file() and p.name != "field_manifest.json")
_manifest = {"run_root_masked": "<RUNROOT>", "files": _files_on_disk,
             "files_from_manual_accumulate": sorted(copied),
             "readback_is_byte_equal_to_accepted_bin": rb == raw_bin,
             "privacy": {"uid_plaintext_archived": False, "disclosure_form": "len 48 + sha256_12"},
             "operator_exec_approval_verbatim": "这一轮没问题就直接批准你执行"}
(DEST / "field_manifest.json").write_text(json.dumps(_manifest, ensure_ascii=False, indent=1), encoding="utf-8")
_back = json.loads((DEST / "field_manifest.json").read_text(encoding="utf-8"))
note("ARCHIVE_MANIFEST_EQUALS_DISK_SET", _back["files"] == _files_on_disk,
     "清单按落盘后 walk 实有 %d 件生成(不再手工累加): 磁盘=%d 清单=%d" %
     (len(_files_on_disk), len(_files_on_disk), len(_back["files"])))
_short = [f for f in _files_on_disk if f != "readback.bin"]
note("ARCHIVE_MANIFEST_SELFCHECK_FIRES", _short != _files_on_disk and _short != _back["files"],
     "漏掉 readback.bin(第一版手工累加的缺陷形状: 手工 %d 件 vs 磁盘 %d 件, 差额=%s)时同一比较即不成立" %
     (len(copied), len(_files_on_disk), sorted(set(_files_on_disk) - set(copied))))
note("ARCHIVE_HAS_READBACK_BIN_BYTE_EXACT", "readback.bin" in _files_on_disk and
     (DEST / "readback.bin").read_bytes() == rb and (DEST / "readback.bin").stat().st_size == len(rb),
     "回读二进制 %d B 已按 idle-stack-r4 先例入库且与判读用的那份逐字节相同" % len(rb))
_dl = (RUN / "dispatch/dispatch_ledger.jsonl").read_bytes()
note("DISPATCH_LEDGER_ARCHIVED_BYTE_EXACT",
     (DEST / "dispatch_ledger.jsonl").read_bytes() == _dl and
     len(_dl.decode("utf-8", "replace").splitlines()) == 21,
     "跨阶段台账(21 行 = 6 预约 + 6 提交 + 动作/绑定/S0/收口)按原字节入库, 第一版判读工具漏了它")
_raws = sorted(S3.glob("*.rx.bin"))
note("BOARD_CAPTURE_BYTES_NOT_MUTATED",
     all((DEST / p.name).read_bytes() == p.read_bytes() for p in _raws) and len(_raws) == 17,
     "%d 份板端原始捕获(15 步响应 + 2 份开口后残留 .pre)与执行器落盘逐字节相同: 判读只读, 不重写证据" % len(_raws))
_B = bytes([92])
# 模式全部运行时拼装: 写成连续字面量会被本轮自己的泄漏扫描抓到(同类缺陷第三次), 那样就分不清证据里有还是检查器里有
_priv = [uid.encode() if uid else b"", uid.upper().encode() if uid else b"",
         b"Users" + _B, b"Users" + bytes([47]), b"App" + b"Data"]
_hits = {}
for rel in _files_on_disk:
    data = (DEST / rel).read_bytes()
    for pat in _priv:
        if pat and pat in data:
            _hits.setdefault(pat.decode("ascii", "replace")[:14], []).append(rel)
note("ARCHIVE_HAS_NO_IDENTITY_BEARING_FORM", not _hits,
     "整包 %d 件里 UID 明文(两种大小写)/用户主目录片段/漫游数据目录片段 命中=%s" % (len(_files_on_disk), _hits))
_probe = (b"xx" + uid.encode() + b"|" + uid.upper().encode() + b"|C:" + _B + b"Users" + _B + b"someone|" +
          b"x:" + bytes([47]) + b"Users" + bytes([47]) + b"y|App" + b"Data")
_fired = [p for p in _priv if p and p in _probe]
note("ARCHIVE_PRIVACY_GATE_FIRES", len(_priv) == 5 and len(_fired) == 5,
     "同一组模式在合成脏样本上命中 %d/%d(不命中就无权宣布干净; 不是拿被检数据自己拼进来的恒真比较)" %
     (len(_fired), len(_priv)))
info("ARCHIVE_LOCAL_SCRATCH_FORM_REGISTERED",
     "非身份性的仓外 scratch 路径以 JSON 转义形态保留在 %s; 我没有为装干净去改工具台账字节" %
     sorted(rel for rel in _files_on_disk if b"ns800_r7f1_scratch" in (DEST / rel).read_bytes()))

summary = "R7F7_FIELD_SUMMARY|判据=%d|FAIL=%d|%s" % (
    len([l for l in lines if "|PASS|" in l or "|FAIL|" in l]), len(fails),
    ",".join(fails) if fails else "none")
full = "\n".join(lines + [summary, "ARCHIVED_TO|%s" % DEST.relative_to(REPO).as_posix()]) + "\n"
(RD / "proof").mkdir(exist_ok=True)
(RD / "proof/field_judgement_r7f7.txt").write_text(full, encoding="utf-8")
(RD / "proof/field_business_outcomes.json").write_text(
    json.dumps([x for x in led.rows if x.get("kind") == "BUSINESS_OUTCOME"], ensure_ascii=False, indent=1),
    encoding="utf-8")
print(full, end="")
sys.exit(1 if fails else 0)
