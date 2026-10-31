#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R2 现场候选包的会话执行器(单一写者 / 原始字节先落盘 / 起转即硬截止 / 动作后端可注入)。

本轮离线: 只有 transport="stub" 与 backend="sim" 被真正跑过; real/manual 分支从未执行
(hardware_actions=[])。它们的存在是为了让 RunField 在拿到批准后**真的做事**, 而不是像
R1 那样只扣额度然后写一个离线 PASS。

沿用的两条既有纪律(不新造第三套验证门):
  A) P0(``phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py``):
     开口时一次配齐、**开口之后不写任何串口属性**、提示符正则 ``msh >`` / ``msh />``、
     回显剥离、字段必须"恰好一条"、原始字节先落盘(解码只用于判读)。
  B) M6(``phase8-motor-m6-offline/m6_field_candidate.py``): 首错保留、证据失败与主错
     分开记、清理同一写者、不可证实的禁能一律 UNKNOWN。

R2-C 的具体修正对应关系:
  C1  RunField 真调度: download / readback / reset / reboot / port / start / stop 全部
      经 ActionBackend 接口, sim 记录调用序列, manual 要操作者交付物, real 跑逐字命令。
      模拟字节永远进不了真实身份门(见 RealIdentityGate)。
  C2  seconds=5 是 motor_start 尝试那一刻建立的**单调硬截止**, 之后的写/读/watch/人工/
      learn 只能用剩余预算; 到点必做 stop+disarm 并核字段, 证不到禁能 => UNKNOWN。
      watch 的 STOP 会终止后续危险步骤; finally 不再只关端口。
      人工门在非 stub 传输下绝不读夹具(行为判据, 不是注释)。
  C3  "响应完整"(协议) 与 "业务成功" 分成两层: 缺提示符/缺帧尾/非法字节/字段不唯一
      都是协议失败; 出现 FAILED / REFUSED / VOIDED 一律业务失败(除非该步骤把某个具名
      拒绝登记为 allowed_outcome, 而那也只是停止, 不是继续)。
      needles 由 prove_needles() 逐条回到真实 C 源码字面量证明。
"""
import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BAUD = 115200
SLACK_SECONDS = 3
WRITE_SECONDS = 2
CLEANUP_RESERVE_SECONDS = 4          # 起转硬截止之后留给 stop+disarm+复核的时间
BOOT_CAP = 262144
RESPONSE_CAP = 32768
PROMPT_RE = re.compile(rb"(?:^|\r?\n|\r)msh\s*(?:>|/>)\s*\Z")
ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
DANGER = re.compile(rb"stack is close|stack overflow|SYSTEM_HALT|HardFault|BusFault|UsageFault", re.I)
# 业务失败的具名标记: 产品里这几条线一律由 rt_kprintf 打出(见 needles.json 的 source 字段),
# 出现即说明这一步没有成立, 与"响应是否完整"无关。
BUSINESS_FAILURE = (b"FAILED", b"REFUSED", b"VOIDED", b"not supported")
YES_TOKENS = ("yes", "y", "true", "1", "confirmed")


class Stop(Exception):
    def __init__(self, code, detail=""):
        Exception.__init__(self, code + (":" + detail if detail else ""))
        self.code = code
        self.detail = detail


class SimClock:
    """虚拟时钟: sleep 只推进时间。stub 会话因此在毫秒内跑完整个 5 s 预算与清理。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))


class RealClock:
    monotonic = staticmethod(time.monotonic)

    @staticmethod
    def sleep(s):
        time.sleep(s)


class Ledger:
    def __init__(self, path, clock, out_dir=None):
        # 台账一律**独占创建**('x'): 同名目录里已有台账说明这一轮撞上了上一轮的产物,
        # 那种读数会把两次运行混成一串(自检里出现过变异体读到基线台账的假通过)。
        if Path(path).exists():
            raise Stop("LEDGER_PREEXISTING", str(path))
        self.f = open(path, "x", encoding="utf-8", newline="\n")
        self.clock = clock
        self.t0 = clock.monotonic()
        self.out = Path(out_dir) if out_dir else Path(path).parent

    def ev(self, kind, **kw):
        rec = {"t_s": round(self.clock.monotonic() - self.t0, 3), "kind": kind}
        rec.update(kw)
        self.f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self.f.flush()
        return rec

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class FakePort:
    """脚本化假端口: 只有 read/write/feed/close/in_waiting, 任何"开口后写属性"都当场报错。"""

    def __init__(self, script, ledger):
        object.__setattr__(self, "_chunks", list(script))
        object.__setattr__(self, "_buf", b"")
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(self, "written", [])

    def feed(self, data):
        self._chunks.append(data)

    @property
    def in_waiting(self):
        return len(self._buf) + sum(0 if c is None else len(c) for c in self._chunks)

    def write(self, data):
        if self._closed:
            raise Stop("PORT_CLOSED_BEFORE_WRITE")
        self.written.append(bytes(data))
        self._ledger.ev("TX", bytes=bytes(data).decode("utf-8", "replace"))
        return len(data)

    def read(self, n):
        if self._closed:
            return b""
        while not self._buf and self._chunks:
            nxt = self._chunks.pop(0)
            if nxt is None:
                break
            self._buf += nxt if isinstance(nxt, bytes) else nxt.encode("utf-8")
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self):
        object.__setattr__(self, "_closed", True)

    def __setattr__(self, name, value):
        if str(name).startswith("_"):
            object.__setattr__(self, name, value)
            return
        raise AssertionError("post-open port property write attempted: " + str(name))


def make_real_port(port_name, ledger):
    """只在现场真实分支导入 pyserial; 全部参数一次配齐, 开口之后不再写任何属性(P0 已证)。"""
    import serial
    ledger.ev("PORT_OPEN_ATTEMPT", port=port_name, baud=BAUD)
    return serial.Serial(port_name, BAUD, bytesize=8, parity="N", stopbits=1,
                         timeout=0, write_timeout=WRITE_SECONDS,
                         xonxoff=False, rtscts=False, dsrdtr=False)


def sha_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- 协议层
def decode_for_read(raw):
    """解码只用于判读; 落盘字节不动。非法 UTF-8 字节序列是**协议失败**, 不是被 replace 掉。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Stop("ILLEGAL_BYTES_IN_RESPONSE", repr(raw[max(0, e.start - 8):e.start + 8]))
    return text


def split_rows(raw, command=None, require_prompt=True):
    """去 ANSI、剥提示符、剥回显, 返回行列表; 缺提示符 => 具名协议失败。

    require_prompt=False 只给**被动观察窗**用: 那段时间里没有命令、也就没有提示符,
    板子只是把 [DIAG]/[MOT] 行流出来。命令响应的提示符门绝不因此放宽。"""
    data = ANSI.sub(b"", raw)
    if require_prompt and not PROMPT_RE.search(data):
        raise Stop("MISSING_RESPONSE_PROMPT", "tail=%r" % data[-16:])
    data = PROMPT_RE.sub(b"", data)
    if command:
        echo = re.compile(rb"(?:msh\s*(?:>|/>)\s*)?" + re.escape(command.encode("ascii")) +
                          rb"\r(?:\n)?")
        data = echo.sub(b"", data, count=1)
    text = decode_for_read(data)
    return [r for r in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def check_danger(raw):
    m = DANGER.search(ANSI.sub(b"", raw))
    if m:
        raise Stop("DANGER_TEXT_IN_STREAM", m.group(0).decode("ascii", "replace"))


def read_until_prompt(port, deadline, led, raw_path, clock):
    """读到提示符为止; 原始字节先落盘再判读。

    返回值仍与 R1 交付版同形(行列表, 字节数), 这样审核探针可以原样调用。差别是:
    没有提示符时**不再**返回那批行, 而是让它们进入 split_rows 后具名失败 —— 由调用方
    (exec_session)转成 STOP。这里为了兼容"直接调用"的探针, 采取更严的做法: 到期仍没有
    提示符就返回空行列表(协议不成立 => 判据层必然不通过), 并把原因记进台账。
    """
    buf = bytearray()
    with open(raw_path, "wb") as f:
        while True:
            chunk = port.read(512)
            if chunk:
                buf += chunk
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                if len(buf) > RESPONSE_CAP:
                    raise Stop("RESPONSE_BYTE_CAP")
                if PROMPT_RE.search(ANSI.sub(b"", bytes(buf))):
                    break
            if clock.monotonic() > deadline:
                break
            clock.sleep(0.005)
    raw = bytes(buf)
    led.ev("RX_DONE", bytes=len(raw), prompt_seen=bool(PROMPT_RE.search(ANSI.sub(b"", raw))),
           raw_sha256=sha_bytes(raw), tail=repr(raw[-16:]))
    if not PROMPT_RE.search(ANSI.sub(b"", raw)):
        return [], len(raw)                     # 协议不成立: 交给判据层, 绝不当作完整响应
    return split_rows(raw), len(raw)


# --------------------------------------------------------------------------- 判据层
def one_row(rows, pattern, what):
    """字段必须恰好一条: 缺失/多条/格式非法一律失败(M6/P0 的 one())。"""
    hits = [re.fullmatch(pattern, row) for row in rows]
    hits = [h for h in hits if h]
    if len(hits) != 1:
        raise Stop("FIELD_NOT_EXACTLY_ONE", "%s hits=%d pat=%s" % (what, len(hits), pattern))
    return hits[0]


def business_failures(rows):
    out = []
    for row in rows:
        rb = row.encode("utf-8")
        for needle in BUSINESS_FAILURE:
            if needle in rb:
                out.append(row.strip())
                break
    return out


def assert_step(st, rows, asname, led, raw=None):
    """把"响应完整"和"业务成功"分开判, 并把结论交给调度层。

    verdict: OK / STOP / REFUSED / NO_TRIGGER
    - 任何业务失败线 => STOP(即使这一步的 expect 里也提到了它): R1 的 expect_any 把
      learn FAILED 当成步骤成功, 就是这一条被抹平的路径。
    - allowed_outcomes 里的具名拒绝 => REFUSED(停止继续危险步骤, 但不算基础设施故障)。
    """
    text = "\n".join(rows)
    if raw is not None:
        check_danger(raw)
    miss = [n for n in st.get("expect_contains", []) if n not in text]
    if miss:
        led.ev("PROTOCOL_OR_CONTENT_FAIL", **{"as": asname, "missing": miss})
        return {"verdict": "STOP", "code": "EXPECTATION_NOT_MET", "missing": miss}
    bad = business_failures(rows)
    allowed = st.get("allowed_outcomes", [])
    if bad:
        named = [b for b in bad for a in allowed if a in b]
        led.ev("BUSINESS_OUTCOME", **{"as": asname, "lines": bad, "allowed": bool(named)})
        if named:
            return {"verdict": "REFUSED", "code": "NAMED_REFUSAL", "lines": named}
        return {"verdict": "STOP", "code": "BUSINESS_FAILED", "lines": bad}
    for pat, what in st.get("expect_fields", []):
        try:
            one_row(rows, pat, what)
        except Stop as e:
            led.ev("FIELD_ASSERT_FAIL", **{"as": asname, "err": e.code, "detail": e.detail})
            return {"verdict": "STOP", "code": e.code, "detail": e.detail}
    banned = [n for n in st.get("expect_absent", []) if n in text]
    if banned:
        led.ev("FORBIDDEN_PRESENT", **{"as": asname, "present": banned})
        return {"verdict": "STOP", "code": "FORBIDDEN_PRESENT", "present": banned}
    if st.get("no_trigger_is_observation") and not any(
            n in text for n in st.get("trigger_needles", [])):
        return {"verdict": "NO_TRIGGER", "asserted": True}
    return {"verdict": "OK", "asserted": bool(rows)}


# --------------------------------------------------------------------------- 人工门
OPERATOR_CALLS = {"n": 0}


def read_manual(name, fixture, allow_prompt, transport="real"):
    """人工门。R2 修正: 夹具里的 yes **只在 stub 传输**下可用; 真实现场必须新输入,
    且调用次数被记账 —— 审核探针把 builtins.input 换成会报错的桩, 于是这条路径在
    real 下必然走到操作者, 不再被合成 yes 顶替。"""
    if transport == "stub":
        got = fixture.get("manual", {}).get(name)
        if got is None:
            raise Stop("MANUAL_GATE_MISSING_STUB_ANSWER", name)
        led_note = "stub_fixture"
    else:
        got, led_note = None, "operator"
        if not allow_prompt:
            raise Stop("MANUAL_INPUT_REQUIRED", name)
        OPERATOR_CALLS["n"] += 1
        got = input("OPERATOR CONFIRM [%s] (yes/no): " % name).strip()
    if str(got).strip().lower() not in YES_TOKENS:
        raise Stop("MANUAL_GATE_DENIED", "%s=%s" % (name, got))
    return str(got)


# --------------------------------------------------------------------------- 动作后端
class ActionBackend:
    """download / readback / reset / reboot 的统一接口。sim 记录, manual 要交付物,
    real 跑逐字命令。三者走同一条判定链, 因此"stub 通过"绝不会被当成"真实身份门通过"。"""

    kind = "abstract"

    def download(self, act, ctx):
        raise NotImplementedError

    def readback(self, act, ctx):
        raise NotImplementedError

    def reset(self, act, ctx):
        raise NotImplementedError

    def reboot(self, act, ctx):
        raise NotImplementedError


class SimActionBackend(ActionBackend):
    """离线: 只记录调用序列与声明值; 身份门一律显式标记 NOT_EXERCISED。"""

    kind = "sim"

    def __init__(self, ledger):
        self.led = ledger
        self.calls = []

    def _rec(self, name, act, ctx, result):
        self.calls.append(name)
        self.led.ev("ACTION", backend=self.kind, name=name, plan=act.get("as"),
                    **{k: v for k, v in result.items() if k != "identity"})
        result["identity_gate"] = "NOT_EXERCISED(simulated bytes are never real proof)"
        return result

    def download(self, act, ctx):
        return self._rec("download", act, ctx,
                         {"declared_sha256": act["image"]["sha256"], "rc": 0,
                          "log": "sim: " + act["image"]["path"]})

    def readback(self, act, ctx):
        return self._rec("readback", act, ctx,
                         {"declared_bytes": act["image"]["bytes"], "rx_bytes": 0, "rc": 0})

    def reset(self, act, ctx):
        return self._rec("reset", act, ctx, {"rc": 0})

    def reboot(self, act, ctx):
        return self._rec("reboot", act, ctx, {"rc": 0, "operator_fact": None})


class ManualActionBackend(ActionBackend):
    """外部工具由操作者手工完成时: 打印逐字命令, 等操作者把**交付物**放进运行目录,
    再按导入门判定(退出码文件 + 日志 + 回读件字节数/散列)。缺任一件 => 具名 STOP。"""

    kind = "manual"

    def __init__(self, ledger, outdir, clock, wait_seconds):
        self.led = ledger
        self.out = Path(outdir)
        self.clock = clock
        self.wait = wait_seconds

    def _gate(self, name, act, want):
        """want: {相对名: (是否必需, 判据)}; 全部交付物到齐才返回, 超时即具名停止。"""
        need_dir = self.out / ("action_" + name)
        need_dir.mkdir(parents=True, exist_ok=True)
        (need_dir / "COMMAND.txt").write_text(act["command"] + "\n", encoding="utf-8")
        self.led.ev("ACTION_WAITING", backend=self.kind, name=name,
                    command=act["command"], need=list(want))
        print("ACTION %s: 请执行下列逐字命令并把交付物放入 %s\n  %s" %
              (name, need_dir, act["command"]), flush=True)
        deadline = self.clock.monotonic() + self.wait
        while True:
            missing = [k for k, (req, _) in want.items() if not (need_dir / k).exists()]
            if not missing:
                break
            if self.clock.monotonic() > deadline:
                raise Stop("ACTION_DELIVERABLES_MISSING", "%s: %s" % (name, ",".join(missing)))
            self.clock.sleep(0.2)
        res = {"rc": None, "files": {}}
        for k, (_, chk) in want.items():
            p = need_dir / k
            data = p.read_bytes()
            res["files"][k] = {"bytes": len(data), "sha256": sha_bytes(data)}
            if chk:
                chk(data, res, k)
        rc_txt = (need_dir / "rc.txt").read_bytes().strip()
        try:
            res["rc"] = int(rc_txt)
        except ValueError:
            raise Stop("ACTION_RC_NOT_INTEGER", "%s rc=%r" % (name, rc_txt[:16]))
        if res["rc"] != 0:
            raise Stop("ACTION_NONZERO_RC", "%s rc=%d" % (name, res["rc"]))
        self.led.ev("ACTION", backend=self.kind, name=name, rc=res["rc"],
                    files=res["files"], identity_gate=res.get("identity_gate", ""))
        return res

    def download(self, act, ctx):
        return self._gate("download", act, {
            "rc.txt": (True, None), "flash.log": (True, None)})

    def readback(self, act, ctx):
        want_bytes = int(act["image"]["bytes"])
        want_sha = act["image"]["sha256"]

        def chk(data, res, key):
            res["rx_bytes"] = len(data)
            if len(data) != want_bytes:
                raise Stop("READBACK_BYTE_COUNT", "%d != %d" % (len(data), want_bytes))
            if sha_bytes(data) != want_sha:
                raise Stop("READBACK_HASH_MISMATCH", sha_bytes(data)[:16])
            res["identity_gate"] = "REAL_HASH_EQUAL"
        r = self._gate("readback", act, {"rc.txt": (True, None),
                                         "readback.bin": (True, chk)})
        return r

    def reset(self, act, ctx):
        return self._gate("reset", act, {"rc.txt": (True, None), "reset.log": (True, None)})

    def reboot(self, act, ctx):
        return self._gate("reboot", act, {"rc.txt": (True, None),
                                          "reboot_operator_fact.txt": (True, None)})


class RealActionBackend(ActionBackend):
    """真跑外部工具(subprocess, 固定 argv 列表, 有界超时), 原始 stdout/stderr 落盘。
    本轮从未执行(hardware_actions=[]); 它的存在使 RunField 的 real 路径不是空壳。"""

    kind = "real"

    def __init__(self, ledger, outdir, clock, timeout_seconds):
        self.led = ledger
        self.out = Path(outdir)
        self.clock = clock
        self.timeout = timeout_seconds

    def _run(self, name, act, argv):
        d = self.out / ("action_" + name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "ARGV.json").write_text(json.dumps(argv, ensure_ascii=False), encoding="utf-8")
        self.led.ev("ACTION_ATTEMPT", backend=self.kind, name=name, argv=argv)
        t0 = self.clock.monotonic()
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=self.timeout)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            raise Stop("ACTION_TIMEOUT", name)
        except OSError as e:
            raise Stop("ACTION_SPAWN_FAILED", "%s: %s" % (name, e))
        (d / "stdout.bin").write_bytes(out)
        (d / "stderr.bin").write_bytes(err)
        (d / "rc.txt").write_text("%d\n" % rc, encoding="utf-8")
        el = self.clock.monotonic() - t0
        if rc != 0:
            raise Stop("ACTION_NONZERO_RC", "%s rc=%d elapsed=%.1f" % (name, rc, el))
        self.led.ev("ACTION", backend=self.kind, name=name, rc=rc, elapsed_s=round(el, 3),
                    stdout_sha256=sha_bytes(out), stderr_sha256=sha_bytes(err))
        return {"rc": rc, "stdout": out, "stderr": err, "elapsed_s": el, "dir": str(d)}

    def download(self, act, ctx):
        r = self._run("download", act, act["argv"])
        r["identity_gate"] = "REAL_TOOL_RC0"
        return r

    def readback(self, act, ctx):
        r = self._run("readback", act, act["argv"])
        data = Path(r["dir"], "stdout.bin").read_bytes()
        if len(data) != int(act["image"]["bytes"]):
            raise Stop("READBACK_BYTE_COUNT", "%d != %d" % (len(data), act["image"]["bytes"]))
        if sha_bytes(data) != act["image"]["sha256"]:
            raise Stop("READBACK_HASH_MISMATCH", sha_bytes(data)[:16])
        r["identity_gate"] = "REAL_HASH_EQUAL"
        return r

    def reset(self, act, ctx):
        return self._run("reset", act, act["argv"])

    def reboot(self, act, ctx):
        r = self._run("reboot", act, act["argv"])
        read_manual("approved_reboot_performed", {}, True, transport="real")
        return r


# --------------------------------------------------------------------------- 起转硬截止
class MotionBudget:
    """motor_start 的发送尝试一旦建立, 后续任何动作只能用剩余预算; 清理另给保留段。"""

    def __init__(self, clock, cap_seconds, reserve=CLEANUP_RESERVE_SECONDS):
        self.clock = clock
        self.cap = float(cap_seconds)
        self.reserve = float(reserve)
        self.t0 = None
        self.actual = None

    @property
    def armed(self):
        return self.t0 is not None

    def start_attempt(self):
        if self.t0 is None:
            self.t0 = self.clock.monotonic()

    def business_deadline(self, want_seconds, slack=SLACK_SECONDS):
        """业务动作的截止 = min(本步想等的, 硬截止) ; 硬截止已过 => 0(不许再做业务动作)。"""
        if self.t0 is None:
            return self.clock.monotonic() + want_seconds + slack
        hard = self.t0 + self.cap
        return min(self.clock.monotonic() + want_seconds + slack, hard)

    def cleanup_deadline(self):
        return (self.t0 + self.cap + self.reserve) if self.t0 is not None \
            else self.clock.monotonic() + self.reserve

    def remaining(self):
        if self.t0 is None:
            return None
        return self.cap - (self.clock.monotonic() - self.t0)

    def close(self):
        if self.t0 is not None and self.actual is None:
            self.actual = self.clock.monotonic() - self.t0
        return self.actual


# --------------------------------------------------------------------------- 清理
# 产品事实: 禁能是否成立由 motor_status 同一份响应里的三条**各自唯一**的行共同说明
#   [MOT] state=%d name=%s
#   [MOT] armed=%d drv_en_request=%d health=%s
#   [MOT] expected_ENN=%s ENN_fb=%d %s verdict=%s   (safety_gpio.c sg_fb_names)
# 任何一条缺失、重复、非法或判决不在"证得禁能"集合内都不算证得 —— 与 M6 的
# "不可证实的禁能一律 UNKNOWN"同一口径。
ROW_MOT_STATE = r"\[MOT\] state=(\d+) name=(\w+)"
ROW_MOT_ARMED = r"\[MOT\] armed=(\d) drv_en_request=(\d) health=(\w+)"
ROW_MOT_FB = r"\[MOT\] expected_ENN=(\w+) ENN_fb=(\d) (\w+) verdict=(\w+)"
IDLE_STATE_NAME = "IDLE"
DISABLE_PROVEN_VERDICTS = ("OK", "CUT_BY_HARDWARE")


def _cleanup_read(port, led, clock, dl, cmd, out_dir, feed_hook=None):
    """发一条关断/只读命令并取回**一条完整响应**; 不做重试(单一写者)。"""
    if feed_hook:
        feed_hook(cmd)
    port.write((cmd + "\r\n").encode("ascii"))
    raw_path = Path(out_dir) / ("cleanup_%s.bin" % cmd.replace(" ", "_"))
    buf = bytearray()
    with open(raw_path, "wb") as f:
        while clock.monotonic() < dl:
            chunk = port.read(512)
            if chunk:
                buf += chunk
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                if PROMPT_RE.search(ANSI.sub(b"", bytes(buf))):
                    break
            clock.sleep(0.01)
    return split_rows(bytes(buf), cmd), bytes(buf)


def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None):
    """起转后的唯一清理路径(M6 语义): stop -> disarm -> motor_status(一次读回全部字段)。
    返回 (disarm_state, errors), 本函数**绝不**抛: 主错由调用方保留, 这里的失败单独记账,
    并且"清理失败"与"业务失败"是分开的两条事实, 谁都不覆盖谁。"""
    errors = []
    state = "UNKNOWN"
    facts = {}
    dl = budget.cleanup_deadline()
    out_dir = led.out
    for cmd in ("motor_stop", "motor_disarm", "motor_status"):
        if clock.monotonic() >= dl:
            errors.append("CLEANUP_BUDGET_EXHAUSTED_BEFORE_" + cmd)
            break
        try:
            rows, raw = _cleanup_read(port, led, clock, dl, cmd, out_dir, feed_hook)
            check_danger(raw)
        except (Stop, OSError) as e:
            errors.append("%s:%s" % (cmd, getattr(e, "code", repr(e))))
            continue
        for key, pat in (("state", ROW_MOT_STATE), ("armed", ROW_MOT_ARMED),
                         ("fb", ROW_MOT_FB)):
            hits = [re.fullmatch(pat, r) for r in rows]
            hits = [h for h in hits if h]
            if not hits:
                continue
            if len(hits) != 1:
                errors.append("%s:ROW_NOT_UNIQUE:%s=%d" % (cmd, key, len(hits)))
                facts[key] = None
                continue
            facts[key] = hits[0]
        led.ev("CLEANUP_READ", cmd=cmd, bytes=len(raw), raw_sha256=sha_bytes(raw))
    a, s, f = facts.get("armed"), facts.get("state"), facts.get("fb")
    if a is not None and s is not None and f is not None:
        armed, req = int(a.group(1)), int(a.group(2))
        name = s.group(2)
        verdict = f.group(4)
        led.ev("DISARM_FIELDS", armed=armed, drv_en_request=req, state_name=name,
               fb=verdict, expected_ENN=f.group(1),
               enn_fb=int(f.group(2)), fb_state=f.group(3))
        if armed == 0 and req == 0 and name == IDLE_STATE_NAME and \
                verdict in DISABLE_PROVEN_VERDICTS:
            state = "CONFIRMED_DISABLED"
        else:
            errors.append("DISARM_NOT_CONFIRMED armed=%d req=%d state=%s verdict=%s" %
                          (armed, req, name, verdict))
    else:
        errors.append("DISARM_FIELDS_INCOMPLETE " + ",".join(
            k for k in ("armed", "state", "fb") if facts.get(k) is None))
    return state, errors


# --------------------------------------------------------------------------- 会话
def exec_session(session_id, steps, outdir, transport, fixture, port_name,
                 total_deadline_seconds, clock, backend=None, motion_cap_seconds=None,
                 plan=None):
    """一个会话 = 一个端口 = 一个写者。

    与 R1 交付版的三处实质差别:
      1) 任何一步判据 STOP/REFUSED/NO_TRIGGER 都**结束业务循环**, 然后统一清理;
         R1 只在 command 步这么办, watch 步 STOP 之后还继续 motor_arm/fault_reset。
      2) motion_cap_seconds 从 motor_start 的发送尝试起算, 之后所有业务动作受剩余预算
         约束; R1 的 seconds=5 只是那一条响应的 deadline。
      3) finally 不再"只关端口": 先 stop+disarm 并核字段, 未证禁能 => 终态 UNKNOWN。
    """
    out = Path(outdir) / session_id
    out.mkdir(parents=True, exist_ok=True)
    led = Ledger(out / "ledger.jsonl", clock, out_dir=out)
    if backend is not None:
        backend.led = led            # 动作事件与串口事件同一条台账: 调用序列不能散在两个文件里
    results = []
    errors = []
    primary = None
    disarm_state = "NOT_ARMED"
    budget = MotionBudget(clock, motion_cap_seconds) if motion_cap_seconds else \
        MotionBudget(clock, total_deadline_seconds)
    silent = bool(fixture.get("inject", {}).get("port_silent"))
    port = None
    ret = None
    try:
        if transport == "stub":
            script = [l.encode("utf-8") + b"\r\n" for l in fixture.get("boot_bytes", [])]
            if silent:
                script = [None]
            port = FakePort(script, led)
        else:
            port = make_real_port(port_name, led)
        led.ev("PORT_OPEN", transport=transport,
               port=port_name if transport == "real" else None)
        quota = dict(plan["quota"]) if plan else {}
        used = {}

        def spend(name, n):
            """额度是**执行时**扣的, 不是声明的: 超了就具名停止, 且不发送那条命令。"""
            if not plan:
                return
            if name not in quota:
                raise Stop("QUOTA_UNDECLINED", name)      # 计划里没有的动作一律禁止
            if quota[name] < n:
                raise Stop("QUOTA_EXHAUSTED", "%s want=%d left=%d" % (name, n, quota[name]))
            quota[name] -= n
            used[name] = used.get(name, 0) + n
            led.ev("QUOTA_CONSUMED", name=name, n=n, left=quota[name])

        spend("port_open", 1)
        for idx, st in enumerate(steps, start=1):
            if clock.monotonic() - led.t0 > total_deadline_seconds:
                raise Stop("TOTAL_DEADLINE_EXCEEDED", "%ds" % total_deadline_seconds)
            asname = st.get("as") or ("step%02d" % idx)
            raw = out / ("%02d_%s.rx.bin" % (idx, asname))
            if "manual" in st:
                val = read_manual(st["manual"], fixture, True, transport=transport)
                led.ev("MANUAL", **{"as": asname, "name": st["manual"], "value": val,
                                    "source": "stub_fixture" if transport == "stub" else "operator"})
                results.append({"as": asname, "kind": "manual", "verdict": "OK", "value": val})
                continue
            if "action" in st:
                spend(st.get("quota_name", st["action"]),
                      int(st.get("quota_n", 1)))
                r = run_action(st, backend, led, ctx={"session": session_id})
                results.append({**r, "kind": "action"})
                if r["verdict"] != "OK":
                    primary = Stop(r["code"], r.get("detail", ""))
                    raise primary
                continue
            cmd = st["cmd"]
            if cmd == "__approved_reboot__":
                spend("approved_reboot", 1)
                r = run_action({"action": "reboot", "as": asname,
                                "command": st.get("command", ""),
                                "argv": st.get("argv", []),
                                "image": st.get("image", {})}, backend, led,
                               {"session": session_id})
                results.append({**r, "as": asname, "kind": "reboot"})
                if r["verdict"] != "OK":
                    raise Stop(r["code"], r.get("detail", ""))
                continue
            if st.get("quota"):
                spend(st["quota"]["name"], int(st["quota"]["n"]))
            # ---- 业务动作一律受"起转之后的剩余预算"约束 ----
            want = int(st.get("seconds", 3))
            step_dl = budget.business_deadline(want) if (cmd == "__watch__" or budget.armed) \
                else min(clock.monotonic() + want + SLACK_SECONDS,
                         clock.monotonic() + total_deadline_seconds)
            if budget.armed and step_dl <= clock.monotonic():
                raise Stop("MOTION_BUDGET_EXHAUSTED", "as=%s want=%ds" % (asname, want))
            if cmd == "__watch__":
                if transport == "stub" and not silent:
                    resp = fixture.get("responses", {}).get(st.get("key") or "__watch__")
                    if resp is None:
                        raise Stop("STUB_MISS", "__watch__")
                    port.feed(("\r\n".join(resp) + "\r\n" + "msh >").encode("utf-8"))
                total = 0
                got = bytearray()
                with open(raw, "wb") as f:
                    # 观察窗一直跑到**剩余预算的截止点**: 提前因为"看见一个提示符"就收,
                    # 会把 5 s 上限变成 0 s, 于是硬截止形同不存在(RV10a 的检出点)。
                    while clock.monotonic() < step_dl:
                        chunk = port.read(512)
                        if chunk:
                            f.write(chunk)
                            f.flush()
                            os.fsync(f.fileno())
                            total += len(chunk)
                            got += chunk
                        else:
                            # 只睡到截止点(不 overshoot): 否则 5 s 硬截止会被量成 5.02 s。
                            clock.sleep(min(0.02, max(0.0, step_dl - clock.monotonic())))
                led.ev("WATCH_DONE", seconds=want, rx_bytes=total,
                       ended_by_budget=budget.armed and clock.monotonic() >= step_dl)
                try:
                    lines = split_rows(bytes(got), require_prompt=False) if got else []
                except Stop as e:
                    lines = []
                    led.ev("PROTOCOL_FAIL", **{"as": asname, "err": e.code})
                v = assert_step(st, lines, asname, led, raw=bytes(got))
                results.append(dict({"as": asname, "kind": "watch", "seconds": want,
                                     "rx_bytes": total, "txt": str(raw) + ".txt"}, **v))
                if v["verdict"] in ("STOP", "REFUSED", "NO_TRIGGER"):
                    led.ev("SESSION_HALT_BY_WATCH", **{"as": asname, "verdict": v["verdict"],
                                                       "code": v.get("code", "")})
                    if v["verdict"] == "STOP":
                        raise Stop(v["code"], json.dumps(v, ensure_ascii=False))
                    # NO_TRIGGER 与具名拒绝都是"业务到此为止"的正常停止: 结束业务循环,
                    # 但**仍然**走同一条清理路径 —— 撤能事实由 finally 之后的汇总给出。
                    ret = {"status": ("OBSERVATION_NO_TRIGGER" if v["verdict"] == "NO_TRIGGER"
                                      else "REFUSED"),
                           "code": v.get("code", ""), "refused": v.get("lines")}
                    break
                continue
            port.write((cmd + "\r\n").encode("ascii"))
            if cmd == "motor_start":
                budget.start_attempt()          # 尝试即建立硬截止: 之后一切受剩余预算约束
            if transport == "stub":
                if silent:
                    pass                        # 注入: 板子不回话 => 只能靠截止时间具名停止
                else:
                    resp = fixture.get("responses", {}).get(st.get("key") or cmd)
                    if resp is None:
                        raise Stop("STUB_MISS", cmd)
                    port.feed(("\r\n".join(resp) + "\r\nmsh >").encode("utf-8"))
            lines, nbytes = read_until_prompt(port, step_dl, led, raw, clock)
            if not lines:
                raise Stop("MISSING_RESPONSE_PROMPT", "as=%s bytes=%d" % (asname, nbytes))
            v = assert_step(st, lines, asname, led, raw=Path(raw).read_bytes())
            results.append(dict({"as": asname, "kind": "command", "cmd": cmd,
                                 "rx_bytes": nbytes, "rx_sha256": sha_of(raw),
                                 "txt": str(raw) + ".txt"}, **v))
            if v["verdict"] == "REFUSED":
                ret = {"status": "REFUSED", "code": v.get("code", ""),
                       "refused": v.get("lines")}
                break
            if v["verdict"] != "OK":
                raise Stop(v["code"], json.dumps(v, ensure_ascii=False))
        if ret is None:
            led.ev("SESSION_OK", session=session_id, steps=len(results))
            ret = {"status": "OK", "code": ""}
    except Stop as e:
        primary = e
        ret = {"status": "STOP", "code": e.code, "detail": e.detail}
        led.ev("SESSION_STOP", code=e.code, detail=e.detail)
    except Exception as e:                                  # 意外异常同样必须先清理再上报
        primary = Stop("UNEXPECTED_EXCEPTION", repr(e))
        ret = {"status": "STOP", "code": primary.code, "detail": primary.detail}
        led.ev("SESSION_STOP", code=primary.code, detail=primary.detail)
    finally:
        if budget.armed:
            hook = _stub_feed(port, fixture) if transport == "stub" else None
            disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture, hook)
            errors.extend(cerr)
        try:
            port.close()
            led.ev("PORT_CLOSED")
        except Exception as e:
            errors.append("PORT_CLOSE:" + repr(e))
        led.close()
    if ret is None:
        ret = {"status": "STOP", "code": "NO_PRIMARY", "detail": "循环结束但没有落定状态"}
    elapsed = budget.close()
    ret.update({"session": session_id, "results": results, "disarm": disarm_state,
                "cleanup_errors": errors, "budget_seconds": elapsed,
                "quota_left": quota, "quota_used": used, "dir": str(out),
                "ledger": str(out / "ledger.jsonl"),
                "terminal": ("KNOWN" if disarm_state in ("CONFIRMED_DISABLED", "NOT_ARMED")
                             else "UNKNOWN")})
    return ret


def _stub_feed(port, fixture):
    """清理阶段也要有可注入的响应(与业务同一张表, 因此不会出现"业务有回复、清理静默"的假象)。"""
    def hook(cmd):
        resp = fixture.get("responses", {}).get(cmd)
        if resp is not None:
            port.feed(("\r\n".join(resp) + "\r\nmsh >").encode("utf-8"))
    return hook


def run_action(st, backend, led, ctx):
    if backend is None:
        return {"as": st.get("as", st["action"]), "kind": "action", "verdict": "STOP",
                "code": "NO_ACTION_BACKEND"}
    name = st["action"]
    fn = getattr(backend, name, None)
    if fn is None:
        return {"as": st.get("as", name), "kind": "action", "verdict": "STOP",
                "code": "UNKNOWN_ACTION_" + name}
    try:
        r = fn(st, ctx)
    except Stop as e:
        return {"as": st.get("as", name), "kind": "action", "verdict": "STOP",
                "code": e.code, "detail": e.detail}
    return {"as": st.get("as", name), "kind": "action", "action": name, "verdict": "OK",
            "backend": backend.kind, "identity_gate": r.get("identity_gate", ""),
            "result": {k: v for k, v in r.items() if k not in ("stdout", "stderr")}}


# --------------------------------------------------------------------------- 静态判据
def static_port_discipline(src_path):
    """F-05: 本文件里不存在"给端口对象写属性"的语句形态(P0 已证该调用路径必须避开)。"""
    tree = ast.parse(Path(src_path).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            base = node.value
            name = getattr(base, "id", getattr(base, "attr", ""))
            if name in ("port", "serial_port"):
                bad.append("%s.%s" % (name, node.attr))
    return bad


def prove_needles(needles_path, source_root):
    """每条 needle 必须能在真实 C 源码里找到对应的字面量(needle 是字面量去掉 % 之后的前缀)。"""
    doc = json.loads(Path(needles_path).read_text(encoding="utf-8-sig"))
    src = {}
    out = []
    bad = 0
    for item in doc["needles"]:
        rel = item["source"]
        if rel not in src:
            p = Path(source_root) / rel
            src[rel] = p.read_text(encoding="utf-8") if p.exists() else None
        text = src[rel] or ""
        lit = item["literal"]
        hit = lit in text
        # 可选的具名替换: 产品里同一行由 tag 参数化(rt_kprintf("[DIAG] %s id=%u...", tag)),
        # 现场要看的文本必须能由"字面量 + 显式给出的 tag 值"机械拼出, 不接受手抄文案。
        rend = lit
        for sub in item.get("sub", []):
            rend = rend.replace("%s", sub, 1)
        pre = item["needle"] in rend
        ok = hit and pre
        if not ok:
            bad += 1
        out.append("NEEDLE|%s|%s|literal_in_%s=%s|needle_is_prefix=%s" %
                   (item["id"], "OK" if ok else "FAIL", rel, hit, pre))
    return out, bad


def emit_text(raw_path, lines):
    txt = Path(str(raw_path) + ".txt")
    txt.write_text("\n".join(lines), encoding="utf-8")
    return str(txt)


def main(argv=None):
    ap = argparse.ArgumentParser(description="M8-R2 serial/action executor")
    ap.add_argument("--transport", choices=["stub", "real"], default="stub")
    ap.add_argument("--backend", choices=["sim", "manual", "real"], default="sim")
    ap.add_argument("--steps")
    ap.add_argument("--session", default="sess")
    ap.add_argument("--out")
    ap.add_argument("--fixture")
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--total-deadline-seconds", type=int, default=120)
    ap.add_argument("--motion-cap-seconds", type=int, default=0)
    ap.add_argument("--plan", help="计划 JSON: 提供后额度在发送前真实扣减(quota)")
    ap.add_argument("--approval-token")
    ap.add_argument("--expected-token")
    ap.add_argument("--check-static-port-discipline", action="store_true")
    ap.add_argument("--prove-needles")
    ap.add_argument("--source-root")
    a = ap.parse_args(argv)
    if a.check_static_port_discipline:
        bad = static_port_discipline(__file__)
        print("F05_STATIC_PORT_DISCIPLINE|%s|post_open_attribute_writes=%s" %
              ("FAIL" if bad else "PASS", ",".join(bad) if bad else "none"))
        return 1 if bad else 0
    if a.prove_needles:
        lines, bad = prove_needles(a.prove_needles, a.source_root)
        for l in lines:
            print(l)
        print("NEEDLES|total=%d|unproven=%d" % (len(lines), bad))
        return 1 if bad else 0
    if a.transport == "real":
        if not a.approval_token or a.approval_token != a.expected_token:
            print("REAL_TRANSPORT_REFUSED:approval_token_missing_or_mismatch", file=sys.stderr)
            return 4
    elif not a.fixture:
        print("STUB_REFUSED:fixture_required", file=sys.stderr)
        return 4
    plan_doc = None
    if a.plan:
        plan_doc = json.loads(Path(a.plan).read_text(encoding="utf-8-sig"))
    steps = json.loads(Path(a.steps).read_text(encoding="utf-8-sig"))
    if not isinstance(steps, list):
        print("STEPS_REFUSED:steps_must_be_a_json_array", file=sys.stderr)
        return 4
    fixture = json.loads(Path(a.fixture).read_text(encoding="utf-8-sig")) if a.fixture else {}
    fixture.setdefault("responses", {})
    clock = SimClock() if a.transport == "stub" else RealClock()
    led_path = Path(a.out) / a.session / "bootstrap.jsonl"
    led_path.parent.mkdir(parents=True, exist_ok=True)
    boot_led = Ledger(led_path, clock)
    if a.backend == "sim":
        backend = SimActionBackend(boot_led)
    elif a.backend == "manual":
        backend = ManualActionBackend(boot_led, led_path.parent, clock, 900)
    else:
        backend = RealActionBackend(boot_led, led_path.parent, clock, 120)
    # boot_led 必须活到会话结束: 动作后端的 ACTION/ACTION_WAITING 事件正是写进它的。
    try:
        res = exec_session(a.session, steps, a.out, a.transport, fixture, a.port,
                           a.total_deadline_seconds, clock, backend=backend,
                           motion_cap_seconds=a.motion_cap_seconds or None,
                           plan=plan_doc)
    except Stop as e:
        print(json.dumps({"session": a.session, "status": "STOP", "code": e.code,
                          "detail": e.detail, "results": []}, ensure_ascii=False))
        return 1
    finally:
        boot_led.close()
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["status"] in ("OK", "OBSERVATION_NO_TRIGGER") else 1


if __name__ == "__main__":
    sys.exit(main())
