#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""现场脚本补正 r2 的集中复核(零设备、零重编、零五千条清单)。

三组判据:
  A 组 = 固定接受值与 argv 形态(UID 内容 / posix+单引号 / 长度与散列先于设备动作)
  B 组 = 串口段零设备注入(假端口 + 假时钟): 停点、句柄关闭、部分字节保、查询三段齐全
  C 组 = 元负控: 把守门函数换成"永远放行", 对应的 A/B 判据**必须**变红; 不变红说明判据恒真

同一份输入在修前脚本上会给出"PASS/继续发 5 次查询/UID 传路径/反斜杠路径", 这里逐项要求修后拒绝。
"""
import json
import pathlib
import re
import shutil
import sys
import tempfile

HOST = pathlib.Path("D:/ns800/reports/reviews/phase8-m8-diag-stack-r1/host")
sys.path.insert(0, str(HOST.parent / "field"))
import run_diag_stack_compare as F  # noqa: E402

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="fieldcheck_", dir="D:/ns800_m8r3_scratch"))
FAKE_UID = "0700000105d3ff393837554757028533a5a5a5a597969908"   # 形状同真实 UID(48 hex); 注入用, 非现场值
PROMPT = F.PROMPT


class FakeClock(object):
    """假时钟: 每次 monotonic() 前进 0.5 s => 75 s 窗口 150 次读, 瞬时完成(不缩短真机窗口)。"""

    def __init__(self, step=0.5):
        self.t = 0.0
        self.step = step

    def monotonic(self):
        self.t += self.step
        return self.t

    def sleep(self, s):
        pass


class FakePort(object):
    def __init__(self, boot_chunks=(), response=None, fail_after=None, short_write=False,
                 prompt_on_enter=True):
        self.queue = list(boot_chunks)
        self.response = response or {}
        self.fail_after = fail_after
        self.reads = 0
        self.short_write = short_write
        self.prompt_on_enter = prompt_on_enter
        self.written = []
        self.closed = False

    def reset_input_buffer(self):
        pass

    def read(self, n):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise OSError(22, "injected read failure")
        if self.queue:
            return self.queue.pop(0)
        return b""

    def write(self, b):
        self.written.append(b)
        if self.short_write:
            return len(b) - 1
        key = b.decode("utf-8", "replace").strip()
        if key == "" :
            if self.prompt_on_enter:
                self.queue.append(PROMPT.encode())
        else:
            self.queue.append(self.response.get(key, PROMPT.encode()))
        return len(b) - 1 if self.short_write else len(b)

    def close(self):
        self.closed = True


def healthy_response(cmd):
    return ("%s\r\nsome body line\r\n%s" % (cmd, PROMPT)).encode()


def run_serial(case, boot_chunks, response=None, fail_after=None, short_write=False,
               prompt_on_enter=True, reset_rc=0):
    """走真实 serial_stage(假端口/假时钟/假 runner), 返回 (out, port)。"""
    d = SCRATCH / case
    d.mkdir(parents=True, exist_ok=True)
    port = FakePort(boot_chunks, response, fail_after, short_write, prompt_on_enter)
    runner = lambda name, argv: {"rc": reset_rc, "stdout": b"", "stderr": b""}  # noqa: E731
    out = {"mode": "inject"}
    got = F.serial_stage(d, FAKE_UID, ["pyocd", "reset"], lambda: port, FakeClock(), runner, out)
    return got, port, d


CHECKS = []


def add(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


BOOT_OK = ("[BOOT] 5 storage ......... OK\r\n[BOOT] 8 diagnosis ....... OK\r\n" +
           F.PLAN["needles"]["boot_complete"] + "READY\r\n").encode()
BOOT_HF = (BOOT_OK + F.PLAN["needles"]["fail_hardfault_dump"].encode() + b"diag\r\n")
BOOT_INCOMPLETE = b"[BOOT] 4 hw-services ...\r\n"

# ---------------- A 组: 固定接受值与 argv 形态 ----------------
add("A1_accepted_literals_agree_with_manifest", not F.verify_accepted_literals(),
    str(F.verify_accepted_literals()))
add("A2_good_expanded_argv_accepted",
    not F.argv_gate("readback", F.expand(F.PLAN["tools"]["readback"]["argv"], SCRATCH, FAKE_UID),
                     FAKE_UID, SCRATCH))
bad = F.expand(F.PLAN["tools"]["readback"]["argv"], SCRATCH, FAKE_UID)
bad[bad.index(FAKE_UID)] = str(F.UID_DEFAULT)            # 修前形态: -u 传的是**文件路径**
p = F.argv_gate("readback", bad, FAKE_UID, SCRATCH)
add("A3_uid_path_form_rejected", any("uid_arg_not_content(path)" in x for x in p), str(p))
backslash = "savemem 0x08000000 290916 %s" % str(SCRATCH / "flash_readback.bin")
add("A4_savemem_backslash_rejected", any("savemem_target_not_quoted" in x or "savemem_backslash" in x
                                         for x in F.argv_gate("readback", [backslash], FAKE_UID, SCRATCH)))
wrong_len = "savemem 0x08000000 40 '%s/flash_readback.bin'" % SCRATCH.as_posix()
add("A5_savemem_count_not_accepted_rejected",
    any("savemem_count_not_accepted" in x for x in F.argv_gate("readback", [wrong_len], FAKE_UID, SCRATCH)))
outside = "savemem 0x08000000 290916 'D:/elsewhere/x.bin'"
add("A6_savemem_outside_run_root_rejected",
    any("savemem_outside_run_root" in x for x in F.argv_gate("readback", [outside], FAKE_UID, SCRATCH)))
add("A7_unresolved_placeholder_rejected",
    any("unresolved_placeholder" in x for x in
        F.argv_gate("read_ram", ["-c", "savemem 0x200127d8 40 '@ROOT/A_locals.bin'"], None, SCRATCH)))
# 盘上件被换掉 => 必须在任何设备动作之前拒绝(用真实文件长度的篡改副本)
tamper_root = SCRATCH / "tamper"
(tamper_root / "reports/firmware/phase8-m8-diag-stack-r1").mkdir(parents=True, exist_ok=True)
tp = tamper_root / "reports/firmware/phase8-m8-diag-stack-r1/rt-thread-motor-m1-candidate.bin"
tp.write_bytes(b"\0" * 10)
_saved_root = F.ROOT
F.ROOT = tamper_root
try:
    probs = F.verify_products()
    add("A8_tampered_product_rejected", any("missing_file" in x or "length" in x or "sha" in x for x in probs),
        str(probs[:2]))
    goodcopy = pathlib.Path(_saved_root / F.ACCEPTED["image_under_test"]["rel"]).read_bytes()
    tp.write_bytes(goodcopy)
    for k in ("axf_under_test", "board_image_before"):
        src = pathlib.Path(_saved_root / F.ACCEPTED[k]["rel"])
        dst = tamper_root / F.ACCEPTED[k]["rel"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    add("A9_intact_copy_passes_verify_products", not F.verify_products(), str(F.verify_products()[:2]))
finally:
    F.ROOT = _saved_root
add("A10_dap_sessions_is_4_tool_connections",
    F.PLAN["quota"]["dap_sessions"] == 4 and sum(
        1 for t in F.PLAN["tools"].values() if isinstance(t, dict) and t.get("argv")) == 4)
add("A11_no_operator_checkpoint_left",
    not [st for st in F.PLAN["stages"] if "operator_checkpoint" in st])
add("A12_plan_has_no_self_computed_hash_binding",
    "hashlib.sha256(BIN" not in pathlib.Path(F.__file__).read_text(encoding="utf-8"))

# ---------------- B 组: 串口段零设备注入 ----------------
got, port, d = run_serial("b_healthy", [BOOT_OK],
                          response={c: healthy_response(c) for c in F.QUERIES})
add("B1_healthy_verdict_is_pending_not_pass",
    got.get("verdict") == "CAPTURE_COMPLETE_REVIEW_PENDING" and "PASS" != got.get("verdict"))
add("B2_healthy_four_queries_with_body",
    len(got.get("queries", {})) == 4 and all(v["body_nonspace_bytes"] > 0 for v in got["queries"].values()))
add("B3_healthy_port_closed", port.closed is True)
add("B4_healthy_raw_files_saved",
    all((d / f).exists() for f in ["S3_boot_capture.rx.bin", "S4_prompt_probe.rx.bin"] +
        ["S4_%s.rx.bin" % c for c in F.QUERIES]))

got, port, d = run_serial("b_hardfault", [BOOT_HF])
add("B5_hardfault_needle_stops", got.get("stop_label") == "HARDFAULT_NEEDLE_SEEN")
add("B6_hardfault_sends_no_queries", not port.written and port.closed)

got, port, d = run_serial("b_reset_fail", [BOOT_OK], reset_rc=1)
add("B7_reset_rc1_stops", got.get("stop_label") == "RESET_FAILED")
add("B8_reset_rc1_sends_nothing_and_closes", port.written == [] and port.closed)

got, port, d = run_serial("b_boot_incomplete", [BOOT_INCOMPLETE])
add("B9_boot_incomplete_stops", got.get("stop_label") == "BOOT_INCOMPLETE" and not port.written)

got, port, d = run_serial("b_read_exception", [BOOT_OK[:40]], fail_after=3)
saved = (d / "S3_boot_capture.rx.bin").read_bytes() if (d / "S3_boot_capture.rx.bin").exists() else b""
add("B10_read_exception_named_stop", got.get("stop_label") == "SERIAL_READ_EXCEPTION", str(got.get("verdict")))
add("B11_partial_boot_bytes_kept", saved == BOOT_OK[:40] and port.closed, "%d B 已落盘" % len(saved))

got, port, d = run_serial("b_no_prompt", [BOOT_OK], prompt_on_enter=False)
add("B12_no_prompt_stops", got.get("stop_label") == "NO_PROMPT" and port.closed)

resp = {c: ("%s\r\n%s" % (c, PROMPT)).encode() for c in F.QUERIES}
got, port, d = run_serial("b_echo_only", [BOOT_OK], response=resp)
add("B13_echo_only_not_success", got.get("stop_label") == "QUERY_INCOMPLETE",
    "detail=%s" % got.get("stop_detail"))

resp = {c: ("%s\r\nbody without terminator\r\n" % c).encode() for c in F.QUERIES}
got, port, d = run_serial("b_query_never_ends", [BOOT_OK], response=resp)
add("B14_unterminated_query_times_out", got.get("stop_label") == "QUERY_TIMEOUT")

got, port, d = run_serial("b_short_write", [BOOT_OK], short_write=True)
add("B15_short_write_stops", got.get("stop_label") == "QUERY_SHORT_WRITE")

# 修前脚本把"四条查询全部零字节"也算 PASS: 这里要求零字节响应一定过不了
resp = {c: b"" for c in F.QUERIES}
got, port, d = run_serial("b_zero_byte", [BOOT_OK], response=resp)
add("B16_zero_byte_response_not_success",
    got.get("stop_label") in ("NO_PROMPT", "QUERY_TIMEOUT", "QUERY_INCOMPLETE") and
    got.get("verdict") != "CAPTURE_COMPLETE_REVIEW_PENDING", str(got.get("stop_label")))

# ---------------- C 组: 元负控(把守门换成永远放行, 判据必须变红) ----------------
orig_gate, orig_products, orig_read = F.argv_gate, F.verify_products, F.read_until_prompt
flipped = 0
F.argv_gate = lambda name, argv, uid, run_root: []
try:
    bad2 = F.expand(F.PLAN["tools"]["readback"]["argv"], SCRATCH, None)
    bad2[8] = str(F.UID_DEFAULT)
    flipped += 0 if F.argv_gate("readback", bad2, FAKE_UID, SCRATCH) else 1
finally:
    F.argv_gate = orig_gate
add("C1_metaneg_argv_gate_load_bearing", flipped == 1, "换成永远放行后 UID 路径形态不再被拒")

flipped = 0
F.verify_products = lambda: []
try:
    flipped = 1 if not F.verify_products() else 0
finally:
    F.verify_products = orig_products
add("C2_metaneg_verify_products_load_bearing", flipped == 1)

flipped = 0
flipped = 0


def _always_ok(port, run_root, fname, term, sec, clock):
    """突变: 去掉单调截止与终止符判据(照旧把看到的字节落盘, 但永远说"齐了")。"""
    data = PROMPT.encode() if fname == "S4_prompt_probe.rx.bin" else b"".join(port.queue)
    port.queue = []
    (run_root / fname).write_bytes(data)
    return data, True


F.read_until_prompt = _always_ok
try:
    d3 = SCRATCH / "c3_unterminated"
    d3.mkdir(parents=True, exist_ok=True)
    port3 = FakePort([BOOT_OK], response={c: ("%s\r\nbody without terminator\r\n" % c).encode()
                                          for c in F.QUERIES})
    out3 = F.serial_stage(d3, FAKE_UID, ["pyocd", "reset"], lambda: port3, FakeClock(),
                          lambda name, argv: {"rc": 0}, {"mode": "inject"})
    flipped = 1 if out3.get("verdict") == "CAPTURE_COMPLETE_REVIEW_PENDING" else 0
finally:
    F.read_until_prompt = orig_read
add("C3_metaneg_query_deadline_load_bearing", flipped == 1,
    "去掉截止/终止符判据后, 永不收尾的响应会被当成查询成功")

saved_quota = F.PLAN["quota"]["dap_sessions"]
F.PLAN["quota"]["dap_sessions"] = 3                       # 突变: 回到上一版误记的会话数
try:
    gp = F.gate_check(None, SCRATCH)
finally:
    F.PLAN["quota"]["dap_sessions"] = saved_quota
add("C4_metaneg_dap_session_count_load_bearing",
    any("dap_session_count_mismatch" in x for x in gp), str(gp[:3]))

problems = [n for n, ok, _ in CHECKS if not ok]
lines = ["FIELD_CHECK mode=zero_device_injection (未起 pyocd, 未开真实端口)",
         "run_dir(LOCAL_ONLY, 仓外)=%s" % SCRATCH.as_posix(),
         "clock=FakeClock(step=0.5s) 说明: 真机 BOOT_WINDOW_S=%d s 未缩短, 只是自检不等真实时间" % F.BOOT_WINDOW_S]
for name, ok, detail in CHECKS:
    lines.append(("OK " if ok else "PROBLEM ") + name + ((" | " + detail) if detail and not ok else ""))
lines.append("CHECKS=%d problems=%d" % (len(CHECKS), len(problems)))
neg_total = sum(1 for n, _ok, _ in CHECKS if n.startswith("C"))
lines.append("NEG_CONTROLS_FIRED=%d/%d" % (sum(1 for n, ok, _ in CHECKS if n.startswith("C") and ok), neg_total))
lines.append("FIELD_CHECK %s" % ("PROBLEMS " + json.dumps(problems, ensure_ascii=False) if problems
                                 else "ALL_PASS_WITH_META_NEGATIVES"))
txt = "\n".join(lines) + "\n"
(HOST / "field_check_console.txt").write_text(txt, encoding="utf-8", newline="\n")
(HOST / "field_check.json").write_text(json.dumps(
    {"checks": [{"name": n, "ok": ok, "detail": dt} for n, ok, dt in CHECKS],
     "problems": problems, "scratch_local_only": SCRATCH.as_posix(),
     "boot_window_s_declared_real": F.BOOT_WINDOW_S}, ensure_ascii=False, indent=1) + "\n",
    encoding="utf-8", newline="\n")
print(txt)
shutil.rmtree(SCRATCH, ignore_errors=True)
sys.exit(0 if not problems else 1)
