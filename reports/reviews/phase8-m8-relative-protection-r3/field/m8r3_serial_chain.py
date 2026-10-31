#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R3 现场候选包的会话执行器(单一写者 / 原始字节先落盘 / 起转即硬截止 / 动作后端可注入)。

本轮离线: 只有 transport="stub" 与 backend="sim" 被真正跑过(真端口/真工具分支从未执行,
hardware_actions=[])。real/manual 分支的存在是为了让 RunField 在拿到批准后**真的做事**。

R3 相对 R2 交付版的四处实质修正(全部有对应具名判据, 见 plan.json 的 corrections_from_review_r3):
  B1 潜在起转义务建立在 port.write **之前**: 写异常/短写/应答丢失都照样走一次必要撤能。
  B2 清理三条命令各有**独立**确认窗与**独立**预留额度: 一条命令的缺帧不再吃掉另一条的发送机会。
  B3 禁能判据改用同一份新 motor_status 的六个合同字段(产品只打 name=FAULT|OTHER, 从不打 IDLE;
     drv_en_request 只是历史确认值, 当前请求侧看 drv_request/pad), 并新增 expect_values(逐值)与
     expect_profile(AT_REST/RUNNING 一致性)。
  C  一次批准可走完整包(dispatch_plan: S0 真实文件/提交/needle 绑定 -> 下载 -> 定长回读 ->
     运动会话 -> 静止提交与保存 -> 重启交付门 -> 恢复逐项实际比较 -> 武装观察 -> 事故取证/人工恢复),
     整包共用**一本**额度账, 阶段失败才终止且把未执行阶段明列为 NOT_RUN。

沿用的既有纪律(不新造第三套验证门):
  A) P0(phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py): 开口时一次配齐、
     **开口之后不写任何串口属性**、提示符正则 msh > / msh />、回显剥离、字段必须"恰好一条"、
     原始字节先落盘(解码只用于判读)。
  B) M6(phase8-motor-m6-offline/m6_field_candidate.py): 首错保留、证据失败与主错分开记、
     清理同一写者、不可证实的禁能一律 UNKNOWN。
  C) R2 已修的协议/业务分层与 needles 证明链保留: 缺提示符/缺帧尾/非法字节/字段不唯一都是协议失败;
     出现 FAILED / REFUSED / VOIDED 一律业务失败(除非该步骤把某个具名拒绝登记为 allowed_outcome,
     或整个阶段申报了 expected_terminal —— 那也只是**到此为止**, 不是继续)。
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

PLAN_APP_REL = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                "ns800rt7p65-nssinepad/applications")
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


def uid_from_file(path):
    """DAP UID 只在真实分支从既存私有文件读取: 永不打印、永不入库, 只披露长度与 sha256 前 12 位。"""
    raw = Path(path).read_text(encoding="utf-8").strip()
    return raw


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
    try:
        sink = open(raw_path, "wb")
    except OSError as e:
        raise Stop("EVIDENCE_OPEN_FAILED", "%s:%s" % (raw_path, e.__class__.__name__))
    with sink as f:
        while True:
            chunk = port.read(512)
            if chunk:
                buf += chunk
                try:
                    f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                except OSError as e:
                    raise Stop("EVIDENCE_WRITE_FAILED",
                               "%s:%s bytes=%d" % (raw_path, e.__class__.__name__, len(buf)))
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
    # 业务层先判: 一条 FAILED/REFUSED/VOIDED 线是**业务结论**, 不能因为"这一步本来还想
    # 看见别的字串"就先把它的具名结果盖掉(内容缺失与业务失败是两个不同层的问题)。
    bad = business_failures(rows)
    allowed = st.get("allowed_outcomes", [])
    if bad:
        named = [b for b in bad for a in allowed if a in b]
        led.ev("BUSINESS_OUTCOME", **{"as": asname, "lines": bad, "allowed": bool(named)})
        if named:
            return {"verdict": "REFUSED", "code": "NAMED_REFUSAL", "lines": named}
        return {"verdict": "STOP", "code": "BUSINESS_FAILED", "lines": bad}
    miss = [n for n in st.get("expect_contains", []) if n not in text]
    if miss:
        led.ev("PROTOCOL_OR_CONTENT_FAIL", **{"as": asname, "missing": miss})
        return {"verdict": "STOP", "code": "EXPECTATION_NOT_MET", "missing": miss}
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
    # expect_values: 一行(必须唯一)里的**命名组等于给定值**。只核行数/形状不算通过。
    for spec in st.get("expect_values", []):
        pat = spec["row"]
        hits = [re.match(pat, r) for r in rows if r]
        hits = [h for h in hits if h]
        if len(hits) != 1:
            led.ev("VALUE_ASSERT_FAIL", **{"as": asname, "err": "ROW_NOT_UNIQUE",
                                           "count": len(hits), "row": pat})
            return {"verdict": "STOP", "code": "FIELD_NOT_EXACTLY_ONE", "row": pat,
                    "count": len(hits)}
        g = hits[0].groupdict()
        wrong = {k: (g.get(k), v) for k, v in spec.get("must", {}).items()
                 if str(g.get(k)) != str(v)}
        loose = {k: g.get(k) for k, v in spec.get("must_not", {}).items()
                 if str(g.get(k)) == str(v)}
        unknown = [k for k in spec.get("must", {}) if k not in g]
        if wrong or loose or unknown:
            led.ev("VALUE_ASSERT_FAIL", **{"as": asname, "wrong": wrong, "forbidden_val": loose,
                                           "absent_groups": unknown})
            return {"verdict": "STOP", "code": "FIELD_VALUE_MISMATCH", "wrong": wrong,
                    "forbidden_val": loose, "absent_groups": unknown}
    # expect_profile: 用与清理同一条合同判读一份完整 motor_status(静止/武装/运行的一致性)
    if st.get("expect_profile") == "AT_REST":
        summ, bad = disable_contract(rows)
        if bad:
            led.ev("PROFILE_FAIL", **{"as": asname, "profile": "AT_REST", "bad": bad})
            return {"verdict": "STOP", "code": "PROFILE_NOT_AT_REST", "bad": bad,
                    "fields": summ}
    elif st.get("expect_profile") == "RUNNING":
        summ, _ = disable_contract(rows)
        if summ is None:
            return {"verdict": "STOP", "code": "PROFILE_FIELDS_UNREADABLE"}
        inconsistent = []
        if summ["armed"] != 1:
            inconsistent.append("armed=%d(要求 1)" % summ["armed"])
        if summ["cur_hz"] <= 0 and summ["tgt_hz"] <= 0:
            inconsistent.append("cur_hz/tgt_hz 都是 0(没在动)")
        if summ["verdict"] not in ("OK", "CUT_BY_HARDWARE") or summ["fb_state"] != "resolved":
            inconsistent.append("反馈未解析或判决不可用")
        if inconsistent:
            led.ev("PROFILE_FAIL", **{"as": asname, "profile": "RUNNING",
                                       "bad": inconsistent})
            return {"verdict": "STOP", "code": "PROFILE_NOT_RUNNING", "bad": inconsistent,
                    "fields": summ}
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

    def __init__(self, ledger, outdir=None, clock=None, timeout_seconds=None, subs=None):
        # 三个后端的构造签名必须一致: 调度器只写一次 backend_cls(led, out, clock, 900, subs),
        # 不因后端不同而分叉(sim 只是不碰工具)。
        self.led = ledger
        self.out = Path(outdir) if outdir else None
        self.clock = clock
        self.timeout = timeout_seconds
        self.subs = dict(subs or {})
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

    def __init__(self, ledger, outdir, clock, wait_seconds, subs=None):
        self.led = ledger
        self.out = Path(outdir)
        self.clock = clock
        self.wait = wait_seconds
        self.subs = dict(subs or {})

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

    def __init__(self, ledger, outdir, clock, timeout_seconds, subs=None):
        self.led = ledger
        self.out = Path(outdir)
        self.clock = clock
        self.timeout = timeout_seconds
        # 计划里只允许三个机械占位; 展开表由调度器给(UID 只在 real 分支从私有文件读)。
        self.subs = dict(subs or {})

    def _resolve(self, name, argv):
        """@UID_FILE/@RUN_ROOT/@IMAGE_BYTES 展开; 展开后仍残留占位 => 具名拒绝,
        绝不带着待填参数去启动工具(那是"获批后再手改"的另一种写法)。"""
        out = list(argv)
        if any("@UID_FILE" in x for x in out):
            uid = self.subs.get("@UID_FILE")
            if not uid:
                raise Stop("UID_FILE_REQUIRED", name)
        for k, v in self.subs.items():
            out = [x.replace(k, str(v)) for x in out]
        left = [x for x in out if x.startswith("@") or PLACEHOLDER_RE.search(x)]
        if left:
            raise Stop("TOOL_ARGV_UNRESOLVED", "%s:%s" % (name, left))
        return out

    def _run(self, name, act, argv):
        argv = self._resolve(name, argv)
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

    @staticmethod
    def _tokens(act, r):
        """成功原文按工具的真实输出核对(单看 rc 不算通过)。expect_all / expect_counts 的取值
        与出处写在 plan.tools.* 里, 每条都指向既存真机日志的具体行。"""
        blob = (r["stderr"] + r["stdout"]).decode("utf-8", "replace")
        miss = [t for t in act.get("expect_all", []) if t not in blob]
        if miss:
            raise Stop("ACTION_SUCCESS_TEXT_MISSING", "%s: %s" % (act.get("as", "?"), miss))
        for pat, want in act.get("expect_counts", {}).items():
            got = len(re.findall(pat, blob))
            if got != int(want):
                raise Stop("ACTION_TEXT_COUNT_MISMATCH",
                           "%s %s got=%d want=%d" % (act.get("as", "?"), pat, got, int(want)))
        return blob

    def download(self, act, ctx):
        r = self._run("download", act, act["argv"])
        self._tokens(act, r)
        r["identity_gate"] = "REAL_TOOL_RC0_AND_SUCCESS_TEXT"
        return r

    def readback(self, act, ctx):
        r = self._run("readback", act, act["argv"])
        self._tokens(act, r)
        # 交付物 = savemem 明确写出的文件, 不是混有工具文字的 stdout
        data = Path(act["run_root"], act["out_file"]).read_bytes()
        want = act["image"]
        if len(data) != int(want["bytes"]):
            raise Stop("READBACK_BYTE_COUNT", "%d != %d" % (len(data), int(want["bytes"])))
        if sha_bytes(data) != want["sha256"]:
            raise Stop("READBACK_HASH_MISMATCH", sha_bytes(data)[:16])
        if data != Path(want["path"]).read_bytes():
            raise Stop("READBACK_NOT_BYTE_EQUAL_TO_IMAGE", act["out_file"])
        r["identity_gate"] = "REAL_BYTES_HASH_AND_FILE_EQUAL"
        r["out_file"] = {"name": act["out_file"], "bytes": len(data),
                         "sha256": sha_bytes(data)}
        return r

    def reset(self, act, ctx):
        r = self._run("reset", act, act["argv"])
        self._tokens(act, r)
        return r

    def reboot(self, act, ctx):
        r = self._run("reboot", act, act["argv"])
        self._tokens(act, r)
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
ROW_MOT_HZ = r"\[MOT\] cur_hz=(\d+) tgt_hz=(\d+) accel=(\d+) decel=(\d+) dir=(\d+)"
ROW_MOT_ARMED = r"\[MOT\] armed=(\d) drv_en_request=(\d) health=(\w+)"
ROW_MOT_DRVREQ = r"\[MOT\] drv_request=(\S+) pad=(-?\d+)"
ROW_MOT_FB = r"\[MOT\] expected_ENN=(\w+) ENN_fb=(\d) (\w+) verdict=(\w+)"
ROW_MOT_OFF = r"\[MOT\] diag_off_confirmed=(\w+)"
# 产品里 cmd_motor_status 的 name 只可能是 FAULT / OTHER(motor.c: snap.state==MOTOR_FAULT ?
# "FAULT" : "OTHER"), 所以**不能**要求 IDLE 才算停; 故障锁存后的禁能同样要能证得。
DISABLE_ALLOWED_STATES = ("FAULT", "OTHER")
DISABLE_PROVEN_VERDICTS = ("OK", "CUT_BY_HARDWARE")
# 六个合同字段必须来自**同一份**新的 motor_status 响应(不跨响应拼字段)。
DISABLE_FIELDS = (("state", ROW_MOT_STATE), ("hz", ROW_MOT_HZ), ("armed", ROW_MOT_ARMED),
                  ("drvreq", ROW_MOT_DRVREQ), ("fb", ROW_MOT_FB), ("off", ROW_MOT_OFF))
DISABLE_KEYS = tuple(k for k, _ in DISABLE_FIELDS)
SNAPSHOT_FAILED_ROW = "[MOT] snapshot failed"
MOTION_OBLIGATION_CMDS = ("motor_arm", "motor_start")
CLEANUP_CMDS = ("motor_stop", "motor_disarm", "motor_status")
CLEANUP_WINDOW_SECONDS = 1.2   # 每条清理命令自己的确认窗(三段互不抢占)


def _cleanup_read(port, led, clock, dl, cmd, out_dir, feed_hook=None):
    """发一条清理/只读命令并取回响应。返回 (rows, raw, sent_t, ended_t, errs):
    协议不完整/危险字节/落盘失败都**记进 errs 并继续**, 绝不抛出去打断下一条必要撤能。"""
    errs = []
    if feed_hook:
        feed_hook(cmd)
    sent_t = clock.monotonic()
    try:
        port.write((cmd + "\r\n").encode("ascii"))
    except (OSError, Stop) as e:
        return [], b"", sent_t, clock.monotonic(), ["%s:WRITE_FAILED:%s" %
                                                    (cmd, getattr(e, "code", e.__class__.__name__))]
        if quota_box is not None:
            nm = "cleanup_" + cmd
            if nm not in quota_box:
                if overruns is not None:
                    overruns.append("QUOTA_UNDECLINED_" + nm)
            else:
                quota_box[nm] -= 1
                if quota_box[nm] < 0 and overruns is not None:
                    overruns.append("%s_OVERRESERVED=%d" % (nm, -quota_box[nm]))
    sent_ok_t = clock.monotonic()
    raw_path = Path(out_dir) / ("cleanup_%s.bin" % cmd.replace(" ", "_"))
    buf = bytearray()
    try:
        f = open(raw_path, "wb")
    except OSError as e:
        return [], b"", sent_ok_t, clock.monotonic(), ["%s:EVIDENCE_OPEN_FAILED" % cmd]
    with f:
        while clock.monotonic() < dl:
            chunk = port.read(512)
            if chunk:
                buf += chunk
                try:
                    f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                except OSError as e:
                    errs.append("%s:EVIDENCE_WRITE_FAILED" % cmd)
                    break
                if PROMPT_RE.search(ANSI.sub(b"", bytes(buf))):
                    break
            clock.sleep(0.01)
    ended_t = clock.monotonic()
    try:
        check_danger(bytes(buf))
    except Stop as e:
        errs.append("%s:DANGER_BYTES_%s" % (cmd, e.code))
    if not bytes(buf).strip():
        errs.append("%s:NO_RESPONSE" % cmd)
        return [], bytes(buf), sent_ok_t, ended_t, errs
    try:
        rows = split_rows(bytes(buf), cmd)
    except Stop as e:
        errs.append("%s:%s" % (cmd, e.code))
        rows = []
    led.ev("CLEANUP_READ", cmd=cmd, bytes=len(buf), raw_sha256=sha_bytes(bytes(buf)),
           sent_t=round(sent_ok_t - led.t0, 3), ended_t=round(ended_t - led.t0, 3),
           window_s=round(dl - sent_ok_t, 3))
    return rows, bytes(buf), sent_ok_t, ended_t, errs


def disable_contract(rows):
    """同一份响应里的禁能合同字段(motor.c cmd_motor_status 的真实打印形状)。
    返回 (summary, bad): bad 非空即"这份响应没证得禁能"; 缺/重/冲突/未解析都具名在内。
    产品从不打印 name=IDLE, 故障锁存后 name=FAULT 同样可以证得禁能, 因此不按 IDLE 要求。"""
    facts, bad = {}, []
    for key, pat in DISABLE_FIELDS:
        hits = [h for h in (re.fullmatch(pat, r) for r in rows if r) if h]
        if not hits:
            bad.append("FIELD_MISSING_" + key)
            continue
        if len(hits) != 1:
            bad.append("FIELD_NOT_UNIQUE_%s=%d" % (key, len(hits)))
            continue
        facts[key] = hits[0]
    if SNAPSHOT_FAILED_ROW in " ".join(rows):
        return None, ["SNAPSHOT_FAILED(产品没交出快照, 旧字段不能当证据)"]
    if len(facts) != len(DISABLE_KEYS):
        return None, bad
    name = facts["state"].group(2)
    cur, tgt = int(facts["hz"].group(1)), int(facts["hz"].group(2))
    armed, hist_req, health = (int(facts["armed"].group(1)),
                               int(facts["armed"].group(2)), facts["armed"].group(3))
    drvreq, pad = facts["drvreq"].group(1), int(facts["drvreq"].group(2))
    exp_enn, fb, resolved, verdict = (facts["fb"].group(1), int(facts["fb"].group(2)),
                                      facts["fb"].group(3), facts["fb"].group(4))
    off = facts["off"].group(1)
    if name not in DISABLE_ALLOWED_STATES:
        bad.append("state_name=%s" % name)
    if armed != 0:
        bad.append("armed=%d" % armed)
    if cur != 0 or tgt != 0:
        bad.append("cur_hz=%d/tgt_hz=%d" % (cur, tgt))
    if drvreq != "LOW" or pad != 0:
        bad.append("drv_request=%s/pad=%d" % (drvreq, pad))
    if exp_enn != "HIGH" or fb != 1:
        bad.append("expected_ENN=%s/ENN_fb=%d" % (exp_enn, fb))
    if resolved != "resolved":
        bad.append("fb_UNRESOLVED")
    if verdict not in DISABLE_PROVEN_VERDICTS:
        bad.append("verdict=%s" % verdict)
    if off != "YES":
        bad.append("diag_off_confirmed=%s" % off)
    if health != "OK":
        bad.append("health=%s" % health)
    summary = {"state_num": int(facts["state"].group(1)), "state_name": name, "armed": armed,
               "drv_en_request_hist": hist_req, "health": health, "cur_hz": cur, "tgt_hz": tgt,
               "drv_request": drvreq, "pad": pad, "expected_ENN": exp_enn, "enn_fb": fb,
               "fb_state": resolved, "verdict": verdict, "diag_off_confirmed": off}
    return summary, bad


def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None,
            quota_box=None, overruns=None, used_box=None):
    """义务建立后的唯一清理路径(M6 语义 + R3-B 分段窗): motor_stop -> motor_disarm -> motor_status。
    返回 (disarm_state, errors); 本函数**绝不**抛: 主错由调用方保留, 清理失败单独记账。
    三条命令各有自己的确认窗(总保留量分三段), 任何一条没回都**不减少**后面一条的发送机会 ——
    这正是 R2 交付版的缺陷(STOP_RESPONSE_MISSING 里 disarm 从未发出)。
    禁能成立与否只由**最后这一份新的 motor_status 响应**里的合同字段决定, 不跨响应拼字段。"""
    errors = []
    state = "UNKNOWN"
    if overruns is None:
        overruns = []
    hard = budget.cleanup_deadline()
    out_dir = led.out
    status_rows = []
    for cmd in CLEANUP_CMDS:
        dl = min(hard, clock.monotonic() + CLEANUP_WINDOW_SECONDS)
        if clock.monotonic() >= hard:
            errors.append("CLEANUP_HARD_DEADLINE_BEFORE_" + cmd)
            continue                      # 不 break: 后面的必要撤能仍要在自己的窗里试
        # 撤能预留额度按计划**独立**登记(与业务 motor_run 分列): 一条命令的缺帧不能
        # 吃掉另一条的发送机会。预留未申报/耗尽时仍然发送(安全动作优先于额度), 但那
        # 是一条具名披露 => 本会话一律不算成功。
        if quota_box is not None:
            key = "cleanup_" + cmd
            if key not in quota_box:
                overruns.append(key + ":RESERVE_NOT_DECLARED")
            elif quota_box[key] <= 0:
                overruns.append(key + ":RESERVE_EXHAUSTED_SENT_ANYWAY")
            else:
                quota_box[key] -= 1
                if used_box is not None:
                    used_box[key] = used_box.get(key, 0) + 1
        rows, raw, sent_t, ended_t, errs = _cleanup_read(port, led, clock, dl, cmd,
                                                          out_dir, feed_hook)
        errors.extend(errs)
        if cmd == "motor_status":
            status_rows = rows
    summary, bad = disable_contract(status_rows)
    led.ev("DISARM_FIELDS", fields=summary, unproven=" ".join(bad))
    if bad:
        errors.append("DISARM_NOT_CONFIRMED " + " ".join(bad))
    else:
        state = "CONFIRMED_DISABLED"
    return state, errors
# --------------------------------------------------------------------------- 会话
def plan_motion_window_violations(steps):
    """R3-B §4 第 4 条: 运动窗口里不许有任何阻塞动作(人工 input、长外部动作)。
    人工确认必须排在 arm/start **之前**; 运行状态由同一轮 motor_status 证明。
    这是计划的静态判据, 在开口之前就能拒绝, 不需要等到预算被睡光。"""
    viol, seen_motion = [], False
    for idx, st in enumerate(steps, start=1):
        cmd = st.get("cmd", "")
        if "manual" in st and seen_motion:
            viol.append("manual:%s@%d" % (st["manual"], idx))
        if st.get("blocking") and seen_motion:
            viol.append("blocking:%s@%d" % (st.get("as") or cmd, idx))
        if cmd in MOTION_OBLIGATION_CMDS:
            seen_motion = True
    return viol


def profile_of(fixture):
    return (fixture or {}).get("profile", "healthy")


def exec_session(session_id, steps, outdir, transport, fixture, port_name,
                 total_deadline_seconds, clock, backend=None, motion_cap_seconds=None,
                 plan=None, quota_box=None, used_box=None):
    """一个会话 = 一个端口 = 一个写者。

    与 R1 交付版的三处实质差别:
      1) 任何一步判据 STOP/REFUSED/NO_TRIGGER 都**结束业务循环**, 然后统一清理;
         R1 只在 command 步这么办, watch 步 STOP 之后还继续 motor_arm/fault_reset。
      2) motion_cap_seconds 从 motor_start 的发送尝试起算, 之后所有业务动作受剩余预算
         约束; R1 的 seconds=5 只是那一条响应的 deadline。
      3) finally 不再"只关端口": 先 stop+disarm 并核字段, 未证禁能 => 终态 UNKNOWN。
    """
    viol = plan_motion_window_violations(steps)
    if viol:
        raise Stop("PLAN_MANUAL_AFTER_MOTION", ",".join(viol))   # 开口之前就拒绝
    out = Path(outdir) / session_id
    out.mkdir(parents=True, exist_ok=True)
    led = Ledger(out / "ledger.jsonl", clock, out_dir=out)
    if backend is not None:
        backend.led = led            # 动作事件与串口事件同一条台账: 调用序列不能散在两个文件里
    results = []
    errors = []
    primary = None
    disarm_state = "NOT_ARMED"
    obligation = False
    cleanup_overruns = []
    used = used_box if used_box is not None else {}
    quota = {}
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
        # 额度是**整包共用**的一本账(由调度器传进来), 不是每个会话各自复制一份。
        # 这两个名字在 try 之前已经绑定: 开口失败时 finally 与末尾汇总仍要能用。
        if quota_box is not None:
            quota = quota_box
        elif plan:
            quota = dict(plan["quota"])

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
        steps = [st for st in steps if not st.get("when")
                 or profile_of(fixture) in st["when"]]
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
                try:
                    txt = emit_text(raw, lines)
                except OSError as e:
                    raise Stop("EVIDENCE_WRITE_FAILED", "as=%s:%s" % (asname, e))
                results.append(dict({"as": asname, "kind": "watch", "seconds": want,
                                     "rx_bytes": total, "txt": txt}, **v))
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
            if cmd in MOTION_OBLIGATION_CMDS:
                # 义务与截止起点都在**写之前**: 写异常/短写/应答丢失都照样走撤能清理
                obligation = True
                if cmd == "motor_start":
                    budget.start_attempt()
                led.ev("OBLIGATION_SET", cmd=cmd, t_s=round(clock.monotonic() - led.t0, 3),
                       cap_seconds=budget.cap)
            try:
                port.write((cmd + "\r\n").encode("ascii"))
            except (OSError, Stop) as e:
                raise Stop("COMMAND_WRITE_FAILED",
                           "%s:%s" % (cmd, getattr(e, "code", e.__class__.__name__)))
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
            try:
                txt = emit_text(raw, lines)
            except OSError as e:
                raise Stop("EVIDENCE_WRITE_FAILED", "as=%s:%s" % (asname, e))
            results.append(dict({"as": asname, "kind": "command", "cmd": cmd,
                                 "rx_bytes": nbytes, "rx_sha256": sha_of(raw),
                                 "txt": txt}, **v))
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
        if obligation:                     # 不只是"起转成功之后": 潜在已武装同样清理
            hook = _stub_feed(port, fixture) if transport == "stub" else None
            disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture,
                                         hook, quota_box if plan else None,
                                         cleanup_overruns, used)
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
    # 成功口径(R3-B §4 最后一条): 清理 UNKNOWN、清理错误、证据落盘失败都**不是成功**,
    # 即使业务判据本身走到终态(OK / OBSERVATION_NO_TRIGGER)也一样。
    obligated = obligation
    success = (ret["status"] in ("OK", "OBSERVATION_NO_TRIGGER") and not errors and
               not cleanup_overruns and
               (disarm_state == "CONFIRMED_DISABLED" if obligated else True))
    terminal = ("KNOWN" if ((not obligated and disarm_state == "NOT_ARMED") or
                            disarm_state == "CONFIRMED_DISABLED") else "UNKNOWN")
    if not success and not ret["code"]:
        if cleanup_overruns:
            ret["code"] = "CLEANUP_RESERVE_EXHAUSTED"
        elif obligated and disarm_state != "CONFIRMED_DISABLED":
            ret["code"] = "CLEANUP_UNCONFIRMED"
        else:
            ret["code"] = "CLEANUP_ERRORS" if errors else "STATUS_NOT_SUCCESS"
    ret.update({"session": session_id, "results": results, "disarm": disarm_state,
                "obligation": obligated, "success": success,
                "cleanup_errors": errors, "cleanup_overruns": cleanup_overruns,
                "budget_seconds": elapsed,
                "quota_left": quota, "quota_used": used, "dir": str(out),
                "ledger": str(out / "ledger.jsonl"), "terminal": terminal})
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



# --------------------------------------------------------------------------- 整包调度(R3-C1)
def msh_command_names(app_dir):
    """从产品源码里刮出**真实存在的** msh 命令名(MSH_CMD_EXPORT / _ALIAS 的第二参)。
    计划里的每条命令都必须在这张表里: 这是"不发明命令"的机器判据, 不是注释。"""
    names = set()
    pat = re.compile(r"MSH_CMD_EXPORT(?:_ALIAS)?\(\s*([A-Za-z_][0-9A-Za-z_]*)\s*(?:,\s*([A-Za-z_][0-9A-Za-z_]*))?")
    for f in Path(app_dir).glob("*.c"):
        for mo in pat.finditer(f.read_text(encoding="utf-8", errors="replace")):
            # MSH_CMD_EXPORT(fn, 描述) => 命令名就是函数名; 只有 _ALIAS 的**第二个**参数才是命令名
            # (finsh.h:101-112)。把 EXPORT 的第二参当命令名会刮出 "print"/"show" 这类描述词。
            names.add(mo.group(2) if "_ALIAS" in mo.group(0) else mo.group(1))
    return names


def validate_plan(plan, rows_doc, app_dir):
    """整包计划的离线门: 逐项返回 "检查名|PASS/FAIL|细节" 行。
    这里只判定**结构可执行性**(命令真实存在、取行取得到、额度申报过、as 唯一、
    人工门都在运动之前), 不判定任何设备结论。"""
    out, bad = [], 0
    names = msh_command_names(app_dir)
    cmds = rows_doc["commands"]
    quota = plan["quota"]
    for stg in plan["stages"]:
        sid = stg["id"]
        seen_as = []
        for st in stg.get("steps", []):
            asname = st.get("as") or "?"
            if asname in seen_as:
                bad += 1
                out.append("PLAN_DUP_AS|%s|%s|FAIL|同一个阶段里 as 重复, 记录/比较会取错行" %
                           (sid, asname))
            seen_as.append(asname)
            if "manual" in st:
                continue
            if "action" in st:
                ok = st.get("quota_name", st["action"]) in quota
                out.append("PLAN_QUOTA|%s|%s|%s" % (sid, asname, "PASS" if ok else "FAIL"))
                if not ok:
                    bad += 1
                continue
            cmd = st.get("cmd", "")
            if not cmd:
                bad += 1
                out.append("PLAN_NO_CMD|%s|%s|FAIL|既不是 manual/action 也没有 cmd" % (sid, asname))
                continue
            head = cmd.split(" ")[0]
            known = head in names or cmd == "__watch__"
            out.append("PLAN_COMMAND_EXISTS|%s|%s|%s|%s" %
                       (sid, asname, "PASS" if known else "FAIL", head))
            if not known:
                bad += 1
            key = st.get("key") or cmd
            if key != "__watch__":
                declared = key in cmds
                prof = declared and (st.get("watch_profile") or "__any__")
                has_healthy = declared and "healthy" in cmds[key]
                ok = declared and (has_healthy or prof is not None)
                out.append("PLAN_ROWS_KEY|%s|%s|%s|%s" %
                           (sid, asname, "PASS" if ok else "FAIL", key))
                if not ok:
                    bad += 1
            q = st.get("quota")
            if q:
                ok = q["name"] in quota
                out.append("PLAN_QUOTA|%s|%s|%s" % (sid, asname, "PASS" if ok else "FAIL"))
                if not ok:
                    bad += 1
        v = plan_motion_window_violations(stg.get("steps", []))
        out.append("PLAN_MOTION_WINDOW|%s|%s|%s" %
                   (sid, "PASS" if not v else "FAIL", ",".join(v) or "人工门都在运动之前"))
        if v:
            bad += 1
        for spec in stg.get("record_from", []) + stg.get("compare_recorded", []) + \
                stg.get("compare_recorded_gt", []):
            have = bool(spec.get("regex")) and bool(spec.get("step"))
            out.append("PLAN_RECORD_SPEC|%s|%s|%s" %
                       (sid, spec.get("step", "?"), "PASS" if have else "FAIL"))
            if not have:
                bad += 1
    for name in ("download", "readback", "reset"):
        t = plan.get("tools", {}).get(name)
        ok = bool(t) and isinstance(t.get("argv"), list) and t["argv"] and \
            not plan_placeholder_scan({name: t})
        out.append("PLAN_TOOL_ARGV|%s|%s|%s" % (name, "PASS" if ok else "FAIL",
                                                " ".join(t.get("argv", []))[:70]))
        if not ok:
            bad += 1
    undeclared = sorted({q["name"] for sg in plan["stages"] for q in [st.get("quota")
                                                                     for st in sg.get("steps", [])
                                                                     if st.get("quota")]})
    missing = [k for k in undeclared if k not in quota]
    out.append("PLAN_QUOTA_COVERAGE|all|%s|used=%s missing=%s" %
               ("PASS" if not missing else "FAIL", ",".join(undeclared), ",".join(missing)))
    bad += len(missing)
    return out, bad, len(names)


def load_rows(path):
    """真实响应行表: 每命令行都注明**来源**(真机原件或本轮主机链真实产品输出), 见 rows.json 的 _source。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return doc


def rows_for(table, cmd, profile):
    per = table["commands"].get(cmd)
    if per is None:
        raise Stop("ROWS_NOT_DECLARED", cmd)
    if profile in per:
        return list(per[profile])
    return list(per["healthy"])


def stage_steps(stage, profile):
    """同一份计划在不同档案下只走该走的支线: 主线(healthy)一路到 tier/commit/save;
    真实触发与到时未触发只在演示阶段分岔, 不再让"未触发"把提交与保存跳掉。"""
    return [st for st in stage.get("steps", [])
            if not st.get("when") or profile in st["when"]]


def _spec_active(spec, profile):
    """记录/比较规格也可以只在某些档案生效: 未触发档案里"事故后那一读"本就不存在,
    若不过滤会把一条正常的 NO_TRIGGER 误判成 COMPARE_STEP_MISSING。"""
    return not spec.get("when") or profile in spec["when"]


def record_from_rows(stage, results, recorded, profile="healthy"):
    # 规格里的正则以 re.M 生效: 记录/比较的目标行可能在判读文本的第 N 行,
    # 不加 MULTILINE 的话 "^\[DIAG\] live sig=..." 这类锚点永远落不到那一行。
    """保存阶段把这次真正提交的 id/rev/signature 记进整包账; 重启后按同一组值实际比较。
    文档式 must_compare 列表不算实现 —— 这里没匹配到就具名停止。"""
    got = dict((r.get("as"), r.get("txt")) for r in results if r.get("txt"))
    for spec in stage.get("record_from", []):
        if not _spec_active(spec, profile):
            continue
        txt = got.get(spec["step"])
        if not txt:
            raise Stop("RECORD_STEP_MISSING", spec["step"])
        m = re.search(spec["regex"], Path(txt).read_text(encoding="utf-8", errors="replace"), re.M)
        if not m:
            raise Stop("RECORD_PATTERN_NOT_FOUND", "%s %s" % (spec["step"], spec["regex"]))
        for k in spec["as"]:
            recorded[k] = m.groupdict()[k]
    for spec in stage.get("compare_recorded_gt", []):
        if not _spec_active(spec, profile):
            continue
        """新日志按**本轮实际新增的 session** 取回(M5 口径): 只读旧 logstat 计数或
        做等值比较都不能证明"这一次真的多写了一笔", 所以这里要求严格增大。"""
        txt = got.get(spec["step"])
        if not txt:
            raise Stop("COMPARE_GT_STEP_MISSING", spec["step"])
        m = re.search(spec["regex"], Path(txt).read_text(encoding="utf-8", errors="replace"), re.M)
        if not m:
            raise Stop("COMPARE_GT_PATTERN_NOT_FOUND",
                       "%s %s" % (spec["step"], spec["regex"]))
        vals = m.groupdict()
        bad = []
        for k in spec["keys"]:
            before = recorded.get(k)
            if before is None:
                bad.append("%s:NOT_RECORDED" % k)
                continue
            try:
                if int(vals[k]) <= int(before):
                    bad.append("%s=%s<=before=%s" % (k, vals[k], before))
            except ValueError:
                bad.append("%s=NOT_INTEGER(%s)" % (k, vals[k]))
        if bad:
            raise Stop("SESSION_NOT_INCREASED", json.dumps(bad, ensure_ascii=False))
        for k in spec["keys"]:
            recorded[k] = vals[k]
    for spec in stage.get("compare_recorded", []):
        if not _spec_active(spec, profile):
            continue
        txt = got.get(spec["step"])
        if not txt:
            raise Stop("COMPARE_STEP_MISSING", spec["step"])
        m = re.search(spec["regex"], Path(txt).read_text(encoding="utf-8", errors="replace"), re.M)
        if not m:
            raise Stop("COMPARE_PATTERN_NOT_FOUND", "%s %s" % (spec["step"], spec["regex"]))
        vals = m.groupdict()
        diff = {k: (recorded.get(k), vals[k]) for k in spec["keys"] if recorded.get(k) != vals[k]}
        if diff:
            raise Stop("RESTORE_MISMATCH", json.dumps(diff, ensure_ascii=False))
    return recorded


def build_session_fixture(stage, table, profile):
    """按阶段步骤 + 行表 + 分支档案装配一个会话的响应表(不含任何人工门的答案)。"""
    responses = {}
    for st in stage.get("steps", []):
        key = st.get("key") or st.get("cmd")
        if not key or key == "__approved_reboot__":
            continue
        if key == "__watch__":
            pr = st.get("watch_profile", profile)
            responses[key] = rows_for(table, "__watch__", pr) if "__watch__" in table["commands"] \
                else list(st.get("watch_rows", []))
            continue
        responses[key] = rows_for(table, key, profile)
    # 清理三条命令**总是**要在同一张表里有一行: 义务建立后的 stop/disarm/status 是执行器
    # 自己发的, 不是阶段步骤 —— 少一份响应就会变成"清理查不到东西"的假 NO_RESPONSE。
    for cmd in CLEANUP_CMDS:
        if cmd not in responses:
            responses[cmd] = rows_for(table, cmd, profile)
    return {"boot_bytes": list(stage.get("boot_bytes", [])), "responses": responses,
            "manual": dict(stage.get("manual_answers", {})),
            "cleanup_rows_source": list(CLEANUP_CMDS)}


# 计划里任何"<待手填>"占位或已废弃的工具形态都是**交付缺陷**: 离线绑定必须拒绝。
PLACEHOLDER_KEYS = ("argv", "command", "out_file", "path", "expect_all")
PLACEHOLDER_RE = re.compile(r"<[A-Za-z0-9_.:-]+>")


def plan_placeholder_scan(plan):
    """只扫真正会被执行的字段(tools.* 与 stages[].steps[] 的 argv/command/out_file/path)。
    说明性文字里出现尖括号不算缺陷, 因此这里不做全文扫描。"""
    hits = []

    def check(val, where):
        if isinstance(val, str) and (PLACEHOLDER_RE.search(val) or "packmem" in val):
            hits.append(where)

    def walk(node, where):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in PLACEHOLDER_KEYS:
                    if isinstance(v, list):
                        for i, x in enumerate(v):
                            check(x, "%s/%s[%d]" % (where, k, i))
                    else:
                        check(v, "%s/%s" % (where, k))
                walk(v, "%s/%s" % (where, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (where, i))

    walk({"tools": plan.get("tools", {}),
          "stages": [s.get("steps", []) for s in plan.get("stages", [])]}, "")
    return hits


def plan_forbidden_command_hits(plan):
    """阶段自己声明的 forbidden_commands 必须在**开口之前**就能拒绝, 否则那是一句注释。"""
    hits = []
    for stg in plan.get("stages", []):
        banned = set(stg.get("forbidden_commands", []))
        for st in stg.get("steps", []):
            c = st.get("cmd", "")
            head = c.split(" ")[0] if c else ""
            if c in banned or head in banned:
                hits.append("%s:%s" % (stg["id"], c))
    return hits


def s0_binding(plan, repo_root):
    """S0 是**真实文件与来源绑定**, 不是一行 OFFLINE_BINDING 声明:
    镜像按路径重算 sha256/字节数并与 identity_binding 与 build_manifest 逐字段比;
    源码提交用 git cat-file 证明该 blob 存在; needles 全部回到 C 源码证明。"""
    root = Path(repo_root)
    ident = plan["identity_binding"]
    img = ident["image"]
    p = root / img["path"]
    facts = {"image_path": img["path"]}
    if not p.is_file():
        raise Stop("S0_IMAGE_ABSENT", img["path"])
    data = p.read_bytes()
    facts["image_bytes"] = len(data)
    facts["image_sha256"] = sha_bytes(data)
    if len(data) != int(img["bytes"]):
        raise Stop("S0_BYTE_COUNT_MISMATCH", "%d!=%d" % (len(data), int(img["bytes"])))
    if facts["image_sha256"] != img["sha256"]:
        raise Stop("S0_HASH_MISMATCH", facts["image_sha256"])
    man_path = root / img["manifest"]
    if not man_path.is_file():
        raise Stop("S0_MANIFEST_ABSENT", img["manifest"])
    man = json.loads(man_path.read_text(encoding="utf-8-sig"))
    hit = None
    for t in man.get("targets", []):
        for name, v in (t.get("images") or {}).items():
            if name.rsplit("/", 1)[-1] == Path(img["path"]).name:
                hit = (t.get("target"), name, v)
    if not hit:
        raise Stop("S0_IMAGE_NOT_IN_MANIFEST", img["path"])
    if hit[2]["sha256"] != facts["image_sha256"] or hit[2]["bytes"] != facts["image_bytes"]:
        raise Stop("S0_MANIFEST_FIELD_MISMATCH", hit[1])
    facts["manifest_target"] = hit[0]
    for field in ("software_input_commit", "tested_image_build_commit"):
        c = ident.get(field)
        r = subprocess.run(["git", "rev-parse", "%s^{commit}" % c], cwd=str(root),
                           capture_output=True, text=True)
        if r.returncode != 0 or r.stdout.strip() != c:
            raise Stop("S0_COMMIT_NOT_IN_REPO", "%s=%s" % (field, c))
        facts[field] = c
    if ident.get("reviewed_delivery_commit"):
        facts["reviewed_delivery_commit"] = ident["reviewed_delivery_commit"]
    hits = plan_placeholder_scan(plan)
    if hits:
        raise Stop("S0_PLAN_PLACEHOLDER", ",".join(hits[:8]))
    facts["plan_placeholders"] = 0
    fhits = plan_forbidden_command_hits(plan)
    if fhits:
        raise Stop("S0_PLAN_FORBIDDEN_COMMAND_PRESENT", ",".join(fhits[:8]))
    facts["plan_forbidden_hits"] = 0
    nroot = root / plan.get("needles_source_root", ".")
    facts["needles_source_root"] = str(plan.get("needles_source_root", "."))
    lines, bad = prove_needles(root / plan["needles_file"], nroot)
    facts["needles_total"] = len(lines)
    if bad:
        raise Stop("S0_NEEDLES_UNPROVEN", str(bad))
    return facts


def dispatch_plan(plan, transport, backend_name, outdir, clock, profile, port,
                  repo_root, approval_token=None, expected_token=None, uid_file=None,
                  plan_dir=None):
    """一次批准 -> 从 S0 一直走到**计划里的最后一个阶段**; 成功阶段只记检查点(不为下一阶段
    重新批准、不重新下载), **失败**才终止。
    整包共用同一本额度账(quota_box)与同一个动作后端; 每个串口会话仍各开一次端口。
    任何会话非成功(含清理 UNKNOWN / 清理错误 / 预留耗尽 / 证据失败)都阻断后续阶段并非零返回。"""
    quota_box = dict(plan["quota"])
    used_box = {}

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    led_path = out / "dispatch_ledger.jsonl"
    led = Ledger(led_path, clock, out_dir=out)
    img_bytes = int(plan["identity_binding"]["image"]["bytes"])
    subs = {"@RUN_ROOT": str(out), "@IMAGE_BYTES": "0x%X" % img_bytes}
    if uid_file:
        subs["@UID_FILE"] = uid_from_file(uid_file)
    backend_cls = {"sim": SimActionBackend, "manual": ManualActionBackend,
                   "real": RealActionBackend}[backend_name]
    backend = backend_cls(led, out, clock, 900, subs)
    if transport == "real" and (not approval_token or approval_token != expected_token):
        # 拒绝分支也要带回调度层的完整字段: main 会照样打印 stages/not_run/quota,
        # 少一个键就是 KeyError, 现场只会看到崩栈而不是"被拒绝"。
        return {"status": "REFUSED", "code": "REAL_TRANSPORT_REFUSED", "stages": [],
                "not_run": [s["id"] for s in plan["stages"]], "recorded": {},
                "quota_left": dict(plan["quota"]), "quota_used": {},
                "expected_stages": [], "profile": profile, "dir": str(out),
                "quota_audit": {"undeclared_used": [], "negative_left": [], "lines": []},
                "ledger": str(led_path)}
    table = load_rows(Path(plan_dir or out.parent) / plan.get("rows_file", "rows.json"))
    recorded = {}
    stages, done, notrun = plan["stages"], [], []
    for i, stg in enumerate(stages):
        sid = stg["id"]
        rest = [x["id"] for x in stages[i:]]
        if stg.get("profiles") and profile not in stg["profiles"]:
            # 档案不参与 = **不在期望序列里**, 因此既不算已执行也不算 NOT_RUN; 台账留具名跳过事件。
            led.ev("STAGE_SKIPPED_BY_PROFILE", stage=sid, profiles=stg["profiles"],
                   profile=profile)
            continue
        if transport == "stub" and stg["kind"] in ("download", "readback", "reset"):
            pass                                   # sim 后端记录调用序列, 不碰设备
        kind = stg["kind"]
        try:
            if kind == "offline":
                facts = s0_binding(plan, repo_root)
                led.ev("S0_FACTS", **facts)
                done.append({"id": sid, "status": "OK", "kind": kind, "facts": facts})
                continue
            if kind == "operator_gate":
                """现场重启在这块板上是**断电再上电**, 没有可逐字执行的子进程命令。
                本轮把它做成显式的操作者交付门: 真传输下必须有人输入确认词(read_manual
                在非 stub 传输下绝不读夹具), stub 下用计划里**申报过的**答案并如实记来源;
                额度在交付成立之前扣, 交付不成立就是 MANUAL_GATE_DENIED 并停止后续阶段。"""
                gate = stg["manual"]
                qname = stg.get("quota_name", "approved_reboot")
                if quota_box.get(qname, 0) < 1:
                    raise Stop("QUOTA_EXHAUSTED", "%s left=%s" % (qname, quota_box.get(qname)))
                fx = {"manual": dict(stg.get("manual_answers", {}))}
                val = read_manual(gate, fx, True, transport=transport)
                quota_box[qname] -= 1
                used_box[qname] = used_box.get(qname, 0) + 1
                led.ev("OPERATOR_GATE", stage=sid, gate=gate, value=val,
                       source="operator" if transport == "real" else "declared_stub_answer",
                       post_fact_required=stg.get("post_fact_required", []))
                done.append({"id": sid, "status": "OK", "kind": kind, "gate": gate,
                             "success": True, "post_facts": stg.get("post_fact_required", [])})
                continue
            if kind in ("action", "approved_reboot", "download", "readback", "reset"):
                # 阶段可以直接用动作名当 kind(S1/S2 就是这样), 也可以用旧的 kind=action +
                # actions[0]。两条写法都归一到同一个工具名, 再由 plan.tools[name] 取逐字 argv。
                name = {"download": "download", "readback": "readback", "reset": "reset",
                        "approved_reboot": "reboot"}.get(kind)
                if name is None:
                    acts = stg.get("actions") or []
                    if len(acts) != 1:
                        raise Stop("STAGE_ACTION_AMBIGUOUS", "%s:n=%d" % (sid, len(acts)))
                    name = acts[0]["action"]
                act = plan["tools"][name]
                if name not in quota_box:
                    raise Stop("QUOTA_UNDECLINED", name)
                if quota_box[name] < 1:
                    raise Stop("QUOTA_EXHAUSTED", name)
                quota_box[name] -= 1
                used_box[name] = used_box.get(name, 0) + 1
                payload = {"as": name}
                payload.update(act)
                payload["action"] = name
                payload["run_root"] = str(out)
                r = run_action(payload, backend, led, {"session": sid})
                if r["verdict"] != "OK":
                    raise Stop(r["code"], r.get("detail", ""))
                done.append({"id": sid, "status": "OK", "kind": kind, "action": name})
                if stg.get("operator_checkpoint"):
                    led.ev("CHECKPOINT", stage=sid, note=stg["operator_checkpoint"])
                continue
            if kind == "serial_session":
                steps = stage_steps(stg, profile)
                fx = build_session_fixture(stg, table, profile)
                fx["profile"] = profile
                res = exec_session("S_" + sid, steps, out, transport, fx, port,
                                   int(stg.get("total_deadline_seconds", 120)), clock,
                                   backend=backend,
                                   motion_cap_seconds=plan["quota"]["motion_cap_seconds_per_run"],
                                   plan=plan, quota_box=quota_box, used_box=used_box)
                # 计划可以**申报**某个具名拒绝就是这一阶段的目的(expected_terminal):
                # 该会话仍然在拒绝处结束(绝不继续 commit/save/start/arm/fault_reset), 危险面
                # 与不申报时完全一致, 只是不再把"观察到了预期拒绝"当成阶段失败。拒绝的原文与
                # code 照样进结果, 台账里仍是 status=REFUSED。
                if (not res["success"] and res["status"] in stg.get("expected_terminal", [])):
                    res = {**res, "success": True, "expected_refusal": res["status"],
                            "refused_lines": res.get("refused")}
                try:
                    record_from_rows(stg, res["results"], recorded, profile)
                except Stop as e:
                    # 会话本身已经带着主错(例如 QUOTA_EXHAUSTED)时**不覆盖**它: 记录/比较
                    # 的失败另记 record_code, 否则现场只会看到"比较缺步骤"而看不到真因。
                    res = {**res, "success": False,
                           "status": "STOP" if not res.get("code") else res["status"],
                           "code": res.get("code") or e.code,
                           "detail": res.get("detail") or e.detail,
                           "record_code": e.code, "record_detail": e.detail}
                done.append({"id": sid, "status": res["status"], "code": res["code"],
                             "recorded": dict(recorded),
                             "success": res["success"], "disarm": res["disarm"],
                             "terminal": res["terminal"],
                             "cleanup_errors": res["cleanup_errors"],
                             "obligation": res["obligation"],
                             "dir": res["dir"], "steps": len(res["results"])})
                if not res["success"]:
                    notrun = rest
                    break
                continue
            raise Stop("STAGE_KIND_UNKNOWN", "%s:%s" % (sid, kind))
        except Stop as e:
            done.append({"id": sid, "status": "STOP", "code": e.code, "detail": e.detail})
            notrun = rest
            break
    led.ev("DISPATCH_DONE", run=len(done), not_run=notrun, quota_left=quota_box,
           quota_used=used_box)
    # 额度审计: 用了的必须都申报过, 申报的余额不许为负 —— 两者都是"整包共用一本账"
    # 的可核查形式, 比在计划里写一段说明文字强。
    undeclared = sorted(k for k in used_box if k not in plan["quota"])
    negative = sorted("%s=%d" % (k, v) for k, v in quota_box.items() if v < 0)
    audit_lines = ["QUOTA_AUDIT|%s|declared=%d|used=%d|left=%d" %
                   (k, plan["quota"][k], used_box.get(k, 0), quota_box.get(k))
                   for k in sorted(plan["quota"])]
    expected = [x["id"] for x in stages if not x.get("profiles") or profile in x["profiles"]]
    ok = (not notrun) and all(d.get("success", True) for d in done) \
        and [d["id"] for d in done] == expected \
        and not undeclared and not negative
    led.close()
    return {"status": "OK" if ok else "STOP", "stages": done, "not_run": notrun,
            "quota_audit": {"undeclared_used": undeclared, "negative_left": negative,
                            "lines": audit_lines},
            "expected_stages": expected, "recorded": recorded,
            "quota_left": quota_box, "quota_used": used_box, "profile": profile,
            "ledger": str(led_path), "dir": str(out)}


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
    ap.add_argument("--validate-plan", action="store_true",
                    help="离线门: 命令真实存在/取行取得到/额度申报过/人工门在运动之前")
    ap.add_argument("--app-dir", default="")
    ap.add_argument("--dispatch", action="store_true",
                    help="整包调度: 一次批准从 S0 走到最后一个阶段(失败才终止)")
    ap.add_argument("--profile", default="healthy",
                    help="行表分支: healthy|trip|no_trigger|denied|silent")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--uid-file", default="")
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
    elif not a.dispatch and not a.validate_plan and not a.fixture:
        # 单会话入口仍需显式夹具; --dispatch 自己按 rows.json 逐阶段装配, 不接受外部夹具
        print("STUB_REFUSED:fixture_required", file=sys.stderr)
        return 4
    plan_doc = None
    if a.plan or a.dispatch or a.validate_plan:
        if not a.plan:
            print("DISPATCH_REFUSED:plan_required", file=sys.stderr)
            return 4
        plan_doc = json.loads(Path(a.plan).read_text(encoding="utf-8-sig"))
    if a.validate_plan:
        app = a.app_dir or str(Path(a.repo_root) / PLAN_APP_REL)
        rows_doc = load_rows(Path(a.plan).resolve().parent /
                              plan_doc.get("rows_file", "rows.json"))
        lines, bad, ncmd = validate_plan(plan_doc, rows_doc, app)
        for l in lines:
            print(l)
        print("PLAN_VALIDATE_SUMMARY|checks=%d|fail=%d|msh_commands=%d|plan=%s" %
              (len(lines), bad, ncmd, a.plan))
        return 1 if bad else 0
    if a.dispatch:
        clock = SimClock() if a.transport == "stub" else RealClock()
        try:
            res = dispatch_plan(plan_doc, a.transport, a.backend, a.out, clock, a.profile,
                                a.port, a.repo_root, a.approval_token, a.expected_token,
                                a.uid_file, str(Path(a.plan).resolve().parent))
        except Stop as e:
            print(json.dumps({"status": "STOP", "code": e.code, "detail": e.detail},
                             ensure_ascii=False))
            return 1
        for stg in res["stages"]:
            print("DISPATCH|stage=%s|status=%s|kind=%s|%s" %
                  (stg["id"], stg["status"], stg.get("kind", ""),
                   json.dumps({k: v for k, v in stg.items()
                               if k not in ("id", "status", "kind")}, ensure_ascii=False)))
        for line in res.get("quota_audit", {}).get("lines", []):
            print(line)
        for k in res.get("quota_audit", {}).get("undeclared_used", []):
            print("QUOTA_AUDIT_FAIL|undeclared_used=%s" % k)
        for k in res.get("quota_audit", {}).get("negative_left", []):
            print("QUOTA_AUDIT_FAIL|negative_left=%s" % k)
        for sid in res["not_run"]:
            print("DISPATCH|stage=%s|status=NOT_RUN" % sid)
        print("DISPATCH_SUMMARY|status=%s|stages_executed=%d|stages_not_run=%d|"
              "quota_left=%s|quota_used=%s|profile=%s|ledger=%s" %
              (res["status"], len(res["stages"]), len(res["not_run"]),
               json.dumps(res["quota_left"], sort_keys=True),
               json.dumps(res["quota_used"], sort_keys=True), res["profile"], res["ledger"]))
        return 0 if res["status"] == "OK" else 1
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
        backend = SimActionBackend(boot_led)        # 单会话入口只需记账, 不碰工具
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
    # 返回码只认 success(含"清理 UNKNOWN / 清理错误 / 证据失败"一律非成功), 不只看 status
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
