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

    def __init__(self, script, ledger, clock=None):
        object.__setattr__(self, "_chunks", list(script))
        object.__setattr__(self, "_buf", b"")
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(self, "_clock", clock)
        object.__setattr__(self, "_busy_until", None)
        object.__setattr__(self, "_held", [])
        object.__setattr__(self, "written", [])

    def feed(self, data, block_seconds=0.0):
        """block_seconds>0 建模**单线程 msh 被同步命令占住**: 这期间一个字节都不产出,
        响应排在忙期结束之后才可见(审核 §3: stop/disarm 在忙期只能排队, 不能立刻返回成功)。"""
        if block_seconds and self._clock is not None:
            until = self._clock.monotonic() + float(block_seconds)
            self._held.append((until, data))
            if self._busy_until is None or until > self._busy_until:
                object.__setattr__(self, "_busy_until", until)
            self._ledger.ev("PORT_BUSY_UNTIL", until=round(until, 3),
                            block_seconds=float(block_seconds))
            return
        self._chunks.append(data)

    def _release_held(self):
        now = self._clock.monotonic() if self._clock is not None else None
        while self._held and (now is None or self._held[0][0] <= now):
            self._chunks.append(self._held.pop(0)[1])
        object.__setattr__(self, "_busy_until", self._held[0][0] if self._held else None)

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
        now = self._clock.monotonic() if self._clock is not None else None
        if now is not None and self._busy_until is not None and now < self._busy_until:
            return b""                    # 忙期: 板子还没把话讲完(不是"观测缺失")
        self._release_held()
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


def uid_disclosure(secret):
    """UID 的**公开**身份形式: 长度 + sha256 前 12 位(与 M7/P0 记录同一口径)。
    私有原始件仍在操作者手里, 仓库里只允许出现这个形式。"""
    s = str(secret or "")
    if not s:
        return {"present": False}
    return {"present": True, "length": len(s), "sha256_12": sha_bytes(s.encode("utf-8"))[:12]}


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
        # must_min: **下限**判据(复审 2026-11-01: learn 接受 ADC/SG 独立样本数各 >=8, 不再有"恰好 8")。
        # 只做数值下限比较, 不做上限; 非整数/缺组一律具名拒绝 —— 放宽接受面不等于放弃判据。
        below = {}
        for k, v in spec.get("must_min", {}).items():
            if k not in g:
                unknown.append(k)
                continue
            try:
                if int(str(g[k])) < int(v):
                    below[k] = (g[k], v)
            except ValueError:
                below[k] = (g[k], "NOT_INTEGER")
        if below:
            led.ev("VALUE_ASSERT_FAIL", **{"as": asname, "below_min": below})
            return {"verdict": "STOP", "code": "FIELD_VALUE_BELOW_MIN", "below_min": below}
        if wrong or loose or unknown:
            led.ev("VALUE_ASSERT_FAIL", **{"as": asname, "wrong": wrong, "forbidden_val": loose,
                                           "absent_groups": unknown})
            return {"verdict": "STOP", "code": "FIELD_VALUE_MISMATCH", "wrong": wrong,
                    "forbidden_val": loose, "absent_groups": unknown}
    if st.get("min_free_log_slots") is not None:
        hit = one_row(rows, FLASH_LOGSTAT_ROW, "event-log-capacity")
        free = int(hit["capacity"]) - int(hit["used"])
        if free < int(st["min_free_log_slots"]):
            return {"verdict": "STOP", "code": "EVENT_LOG_CAPACITY_LOW", "free_slots": free}
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
        # "能解析出一份完整 summary" **不等于**"在跑"(审核 §2B 第 5 条): 同一份
        # state=0 cur=0 tgt=100 armed=1 request=LOW pad=0 ENN=HIGH 之前会被收为 OK。
        # 运行只能由数值状态 + 实际频率证明: state=MOTOR_CRUISE(2) 且 cur_hz>0。
        inconsistent = []
        if summ["state_num"] != RUNNING_REQUIRED_STATE:
            inconsistent.append("state=%d(要求 MOTOR_CRUISE=%d)" % (summ["state_num"],
                                                                    RUNNING_REQUIRED_STATE))
        if summ["armed"] != 1:
            inconsistent.append("armed=%d(要求 1)" % summ["armed"])
        if summ["cur_hz"] <= 0:
            inconsistent.append("cur_hz=%d(没在动)" % summ["cur_hz"])
        if summ["tgt_hz"] <= 0:
            inconsistent.append("tgt_hz=%d(没在动)" % summ["tgt_hz"])
        if summ["drv_request"] != "HIGH" or summ["pad"] != 1:
            inconsistent.append("drv_request=%s/pad=%d(与武装运行不相容)" %
                                (summ["drv_request"], summ["pad"]))
        if summ["verdict"] not in ("OK", "CUT_BY_HARDWARE") or summ["fb_state"] != "resolved":
            inconsistent.append("反馈未解析或判决不可用")
        if inconsistent:
            led.ev("PROFILE_FAIL", **{"as": asname, "profile": "RUNNING",
                                       "bad": inconsistent})
            return {"verdict": "STOP", "code": "PROFILE_NOT_RUNNING", "bad": inconsistent,
                    "fields": summ}
    # expect_flag_bits: 提交资格按**位**判, 不再把"字段字符串前后相同"当合法(审核 §2D 第 2 条)。
    # 0x0010 连 COMMITTED(bit0) 都没置, 却曾因为"两处都印 0010"被判成一致。
    bits_specs = st.get("expect_flag_bits") or []
    if isinstance(bits_specs, dict):
        bits_specs = [bits_specs]
    for spec in bits_specs:
        hits = [h for h in (re.match(spec["row"], r) for r in rows if r) if h]
        if len(hits) != 1:
            led.ev("FLAG_ASSERT_FAIL", **{"as": asname, "err": "ROW_NOT_UNIQUE",
                                          "count": len(hits), "row": spec["row"]})
            return {"verdict": "STOP", "code": "FLAG_ROW_NOT_EXACTLY_ONE", "row": spec["row"],
                    "count": len(hits)}
        try:
            value = int(hits[0].group(spec["group"]), 16)
        except (ValueError, IndexError):
            return {"verdict": "STOP", "code": "FLAG_VALUE_UNREADABLE",
                    "detail": hits[0].groupdict()}
        bad_bits = []
        for name in spec.get("must_set", []):
            if not value & RPF_BITS[name]:
                bad_bits.append("缺位%s" % name)
        for name in spec.get("must_clear", []):
            if value & RPF_BITS[name]:
                bad_bits.append("不应置%s" % name)
        unknown = value & ~((1 << len(RPF_BITS)) - 1)
        if unknown:
            bad_bits.append("未知位=0x%X" % unknown)
        led.ev("FLAG_BITS", **{"as": asname, "value": "0x%04X" % value, "bad": bad_bits})
        if bad_bits:
            return {"verdict": "STOP", "code": "PROFILE_FLAGS_NOT_QUALIFIED",
                    "bad": bad_bits, "flags": "0x%04X" % value, "basis": list(RPF_BITS)}
    if st.get("no_trigger_is_observation"):
        seen = [n for n in st.get("trigger_needles", []) if n in text]
        if not seen:
            return {"verdict": "NO_TRIGGER", "asserted": True, "trigger_seen": []}
        # 触发**是**这次观察要拿到的东西: 命中原文要进结果, 真实调度据此决定事故取证支线。
        return {"verdict": "OK", "asserted": True, "trigger_seen": seen}
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

    # 真实/人工后端**实际尝试过**的设备动作(R3 复审 §2C: 真实摘要不许再固定写 hardware_actions=[])。
    # sim 后端永远留空 —— 它没有设备副作用, 把它算进"已尝试"就是冒充。
    def note_attempt(self, name, how):
        if self.kind == "sim":
            return
        self.attempts.append("%s:%s" % (name, how))

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
        self.attempts = []          # sim 恒空: 没有设备副作用就不许进"已尝试"清单

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
        self.attempts = []

    def _gate(self, name, act, want):
        """want: {相对名: (是否必需, 判据)}; 全部交付物到齐才返回, 超时即具名停止。"""
        need_dir = self.out / ("action_" + name)
        need_dir.mkdir(parents=True, exist_ok=True)
        (need_dir / "COMMAND.txt").write_text(act["command"] + "\n", encoding="utf-8")
        # 命令已经交到操作者手上 = 一次真实的设备动作尝试(哪怕后续交付物不齐), 必须进总账。
        self.note_attempt(name, "operator_hand")
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

    def __init__(self, ledger, outdir, clock, timeout_seconds, subs=None, repo_root=None):
        # 计划里只允许三个机械占位; 展开表由调度器给(UID 只在 real 分支从私有文件读)。
        self.led = ledger
        self.out = Path(outdir)
        self.clock = clock
        self.timeout = timeout_seconds
        self.subs = dict(subs or {})
        # 工具 argv 里的相对路径**以 repo_root 为基准**解析(R3 复审 §2A): 不能依赖操作者
        # 从哪个当前目录启动 PS —— 换个目录就找不到 .axf 是执行器的事, 不是"现场环境问题"。
        self.repo_root = Path(repo_root).resolve() if repo_root else Path.cwd()
        self.secret = str(self.subs.get("@UID_FILE") or "")
        self.attempts = []

    def _p(self, x):
        """相对路径 -> repo_root 下的绝对路径; 绝对路径与取值参数原样返回。"""
        if not isinstance(x, str) or x.startswith("-") or not any(s in x for s in ("/", "\\")):
            return x
        if os.path.isabs(x) or re.match(r"^[A-Za-z]:", x):
            return x
        return str(self.repo_root / x)

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

    def _mask(self, argv):
        """脱敏副本: 把展开后的 UID 换回占位形式, 公开件里不出现私有值本身。"""
        if not self.secret:
            return list(argv)
        tag = "@UID_FILE<redacted len=%d sha256_%s>" % (
            len(self.secret), sha_bytes(self.secret.encode("utf-8"))[:12])
        return [x.replace(self.secret, tag) for x in argv]

    def _run(self, name, act, argv):
        resolved = self._resolve(name, argv)
        # A commander -c argument is an opaque command, not a filesystem path.
        argv = [x if i > 0 and resolved[i - 1] == "-c" else self._p(x)
                for i, x in enumerate(resolved)]
        d = self.out / ("action_" + name)
        d.mkdir(parents=True, exist_ok=True)
        masked = self._mask(argv)
        real_sha = sha_bytes(json.dumps(argv, ensure_ascii=False).encode("utf-8"))
        # 公开归档只有脱敏 argv + **原 argv 的散列**: 事后拿同一私有 UID 文件可重算散列来证明
        # 当时执行的正是这条命令, 而仓库里永远不含 UID 值(R3 复审 §2C 最后一条)。
        (d / "ARGV.json").write_text(json.dumps(
            {"argv_masked": masked, "argv_sha256_of_private_original": real_sha,
             "cwd": str(self.repo_root), "uid_disclosure": uid_disclosure(self.secret)},
            ensure_ascii=False, indent=1), encoding="utf-8")
        self.note_attempt(name, "real_tool")
        self.led.ev("ACTION_ATTEMPT", backend=self.kind, name=name, argv=masked,
                    argv_sha256=real_sha)
        t0 = self.clock.monotonic()
        try:
            p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=self.timeout, cwd=str(self.repo_root))
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
        if data != Path(self._p(want["path"])).read_bytes():
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

# 运动状态枚举与提交位都来自产品源码, 不是本轮自造的名字:
#   motor.h:110-116  MOTOR_IDLE=0, ACCEL, CRUISE, DECEL, FAULT  (隐式递增)
#   diag_relative.h:39-41  RPF_COMMITTED(1u<<0) / RPF_FIELD_VERIFIED(1u<<1) / RPF_FROM_LEARN(1u<<2)
# `--validate-plan` 会回到这两处源码逐项核对(PLAN_STATE_AND_FLAG_CONSTANTS), 常量漂移就 FAIL。
MOTOR_STATE_ENUM = {"IDLE": 0, "ACCEL": 1, "CRUISE": 2, "DECEL": 3, "FAULT": 4}
MOTOR_STATE_SOURCE = ("motor.h", "MOTOR_IDLE = 0")
RPF_BITS = {"COMMITTED": 1 << 0, "FIELD_VERIFIED": 1 << 1, "FROM_LEARN": 1 << 2}
RPF_BITS_SOURCE = ("diag_relative.h", "#define RPF_COMMITTED")
# RUNNING 判据要的**数值**状态: 产品的 name 只打 FAULT|OTHER, 所以"在巡航"只能由 state=2 证明。
RUNNING_REQUIRED_STATE = MOTOR_STATE_ENUM["CRUISE"]

# flash_dump 的表头与逐帧 CSV 来自 ns_flash_shell.c:153/159-163(M5 现场原字节归档同一形状);
# CRC/commit 由 ns_storage.c 的读取路径内部校验, 校验不过的槽打成 "slot N error -x" ——
# 所以"这一批里没有 empty/error/overflow 行"就是产品自己给出的 CRC/commit 通过形式。
FLASH_DUMP_HEADER = ("slot,seq,session,time_ms,event,valid,sg,current_ma,"
                     "ax_mg,ay_mg,az_mg,step_hz,vibration_mg,flags")
FLASH_DUMP_COLUMNS = 14
FLASH_DUMP_BAD = ("empty", "error", "ERROR")
FLASH_EVENT_PRE, FLASH_EVENT_POST_STOP = 0, 9


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
    # (这里曾挂着一段"清理额度扣减", 挂在 return 之后 => 永不执行, 且引用了本函数没有的参数。
    #  清理额度的唯一记账点在 cleanup() 里, 不在读函数里。)
    sent_ok_t = clock.monotonic()
    raw_path = Path(out_dir) / ("cleanup_%s.bin" % cmd.replace(" ", "_"))
    buf = bytearray()
    try:
        f = open(raw_path, "wb")
    except OSError as e:
        return [], b"", sent_ok_t, clock.monotonic(), ["%s:EVIDENCE_OPEN_FAILED" % cmd]
    with f:
        while clock.monotonic() < dl:
            try:
                chunk = port.read(512)
            except Exception as e:
                # 端口读抛出同样是"这条没回": 记错并结束本命令的窗, 让后面的必要撤能还有机会
                errs.append("%s:READ_EXCEPTION_%s" % (cmd, e.__class__.__name__))
                break
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
        try:
            rows, raw, sent_t, ended_t, errs = _cleanup_read(port, led, clock, dl, cmd,
                                                             out_dir, feed_hook)
        except Exception as e:
            errors.append("%s:CLEANUP_RAISED_%s" % (cmd, e.__class__.__name__))
            continue                # 本函数的合同是"绝不抛": 一条异常不吃掉下一条必要撤能
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


def drain_post_reset_boot(port, st, asname, out, idx, led, results, clock, fixture, transport):
    """Archive passive boot bytes; application readiness is proved by the next query.
    A spontaneous boot prompt is optional. Command-response prompts remain mandatory.
    """
    window = float(st.get("boot_drain_seconds", 20))
    t0 = last = clock.monotonic()
    dl = t0 + window
    raw = out / ("%02d_%s.boot.rx.bin" % (idx, asname))
    buf = bytearray()
    if transport == "stub":
        for line in (fixture or {}).get("post_reset_boot", []):
            port.feed((line + "\r\n").encode("utf-8"))
    with open(raw, "wb") as f:
        while clock.monotonic() < dl:
            chunk = port.read(512)
            if chunk:
                buf += chunk
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                last = clock.monotonic()
                if len(buf) > BOOT_CAP:
                    raise Stop("BOOT_BYTE_CAP")
                check_danger(bytes(buf))
            now = clock.monotonic()
            if now - t0 >= min(2.0, window) and now - last >= 0.2:
                break
            clock.sleep(min(0.01, max(0.0, dl - now)))
    # Bytes repr is presentation only; do not decode/repair boot text into business facts.
    txt = emit_text(raw, [repr(bytes(buf))])
    end_reason = "DRAIN_BUDGET_REACHED" if clock.monotonic() >= dl else "BOUNDED_DRAIN"
    results.append({"as": asname + "_boot", "kind": "boot_stream", "rx_bytes": len(buf),
                    "rx_sha256": sha_of(raw), "txt": txt,
                    "readiness": "NOT_ASSESSED_QUERY_REQUIRED", "end_reason": end_reason})
    led.ev("BOOT_STREAM_DRAINED", **{"as": asname, "bytes": len(buf),
           "raw": str(raw), "end_reason": end_reason, "prompt_required": False})


def command_attempt(port, cmd, st, asname, raw, step_dl, transport, fixture, silent, led, clock,
                    attempt=1):
    """一次"发送 -> 取回完整响应 -> 判据 -> 原字节/文本落盘"的最小单元。
    单发步骤与业务轮询走**同一条门**, 所以轮询不会绕过短写、提示符、具名字段这些判据。"""
    data = (cmd + "\r\n").encode("ascii")
    try:
        written = port.write(data)
    except (OSError, Stop) as e:
        raise Stop("COMMAND_WRITE_FAILED",
                   "%s:%s" % (cmd, getattr(e, "code", e.__class__.__name__)))
    if written is not None and written != len(data):
        # 短写: 命令可能只进去一半, 而义务已经建立 => 具名停止并照常走撤能清理
        raise Stop("COMMAND_SHORT_WRITE", "%s wrote=%s want=%d" % (cmd, written, len(data)))
    if transport == "stub":
        if silent:
            pass                        # 注入: 板子不回话 => 只能靠截止时间具名停止
        else:
            key = st.get("key") or cmd
            seq = (fixture.get("sequence") or {}).get(key)
            if seq:                     # 轮询用**申报过的**响应序列(如先 ACCEL 后 CRUISE), 越界取最后一份
                resp = seq[min(attempt, len(seq)) - 1]
            else:
                resp = fixture.get("responses", {}).get(key)
            if resp is None:
                raise Stop("STUB_MISS", cmd)
            # 同步命令会把单线程 msh 占住: 响应只在忙期结束后才可见(审核 §3 "忙 msh")。
            block = float((fixture.get("block") or {}).get(key, 0) or 0)
            port.feed(("\r\n".join(resp) + "\r\nmsh >").encode("utf-8"), block_seconds=block)
    lines, nbytes = read_until_prompt(port, step_dl, led, raw, clock)
    if not lines:
        raise Stop("MISSING_RESPONSE_PROMPT", "as=%s bytes=%d" % (asname, nbytes))
    v = assert_step(st, lines, asname, led, raw=Path(raw).read_bytes())
    try:
        txt = emit_text(raw, lines)
    except OSError as e:
        raise Stop("EVIDENCE_WRITE_FAILED", "as=%s:%s" % (asname, e))
    return v, nbytes, txt


def ramping_state(txt_path):
    """这次尝试的响应里 [MOT] state= 的**数值**(唯一一条才认)。轮询只允许在 ACCEL/DECEL
    这类"还在爬坡"的状态上重试 —— 矛盾字段(armed=1 但 state=0)必须立刻具名拒绝, 不能被
    重试到窗口耗尽而看起来像"只是没等到"。"""
    try:
        rows = Path(str(txt_path)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    hits = [h for h in (re.match(ROW_MOT_STATE, r) for r in rows if r) if h]
    return int(hits[0].group(1)) if len(hits) == 1 else None


def poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture, silent,
                      led, clock, budget, results):
    """起转后的**有限轮询**(审核 §2D 第 3 条): 一条立即查询或 tgt>0 都不算稳定巡航。
    只在申报的 retry_codes 里重试(ACCEL 不是异常 => 同一预算内继续等), 窗口到期或不一致就
    具名停止并交由同一条清理路径撤能。"""
    p = st["poll"]
    window = float(p.get("until_seconds", 3))
    interval = float(p.get("interval_seconds", 0.5))
    codes = tuple(p.get("retry_codes", ["PROFILE_NOT_RUNNING"]))
    ramping = {MOTOR_STATE_ENUM["ACCEL"], MOTOR_STATE_ENUM["DECEL"]}
    until = min(clock.monotonic() + window, step_dl)
    n = 0
    while True:
        n += 1
        raw = out / ("%02d_%s.poll%02d.rx.bin" % (idx, asname, n))
        v, nbytes, txt = command_attempt(port, cmd, st, "%s#%d" % (asname, n), raw, until,
                                         transport, fixture, silent, led, clock, attempt=n)
        results.append(dict({"as": asname, "kind": "command_poll", "cmd": cmd, "attempt": n,
                             "rx_bytes": nbytes, "rx_sha256": sha_of(raw), "txt": txt}, **v))
        if v["verdict"] == "OK":
            led.ev("POLL_OK", **{"as": asname, "attempts": n,
                                 "elapsed_s": round(clock.monotonic() - led.t0, 3)})
            return v
        state = ramping_state(txt)
        if not (v["verdict"] == "STOP" and v.get("code") in codes and state in ramping):
            raise Stop(v["code"], json.dumps(v, ensure_ascii=False))
        if clock.monotonic() + interval >= until or (budget.armed and
                                                    budget.business_deadline(0) <= clock.monotonic()):
            raise Stop("POLL_WINDOW_EXPIRED", json.dumps(
                {"as": asname, "attempts": n, "last": v.get("code"), "state": state,
                 "bad": v.get("bad", []), "window_s": window}, ensure_ascii=False))
        led.ev("POLL_RETRY", **{"as": asname, "attempt": n, "code": v.get("code"),
                                "state": state, "waiting_s": interval})
        clock.sleep(interval)


def exec_session(session_id, steps, outdir, transport, fixture, port_name,
                 total_deadline_seconds, clock, backend=None, motion_cap_seconds=None,
                 plan=None, quota_box=None, used_box=None, context=None):
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
    # 这本账在开口之前就要能用: "试了但没开成"也必须留痕, 所以 spend 定义在 try 之前。
    quota = quota_box if quota_box is not None else (dict(plan["quota"]) if plan else {})

    def spend(name, n):
        "额度是**执行时**扣的, 不是声明的: 超了就具名停止, 且不发送那条命令。"
        if not plan:
            return
        if name not in quota:
            raise Stop("QUOTA_UNDECLINED", name)      # 计划里没有的动作一律禁止
        if quota[name] < n:
            raise Stop("QUOTA_EXHAUSTED", "%s want=%d left=%d" % (name, n, quota[name]))
        quota[name] -= n
        used[name] = used.get(name, 0) + n
        led.ev("QUOTA_CONSUMED", name=name, n=n, left=quota[name])

    budget = MotionBudget(clock, motion_cap_seconds) if motion_cap_seconds else \
        MotionBudget(clock, total_deadline_seconds)
    silent = bool(fixture.get("inject", {}).get("port_silent"))
    port = None
    ret = None
    try:
        spend("port_open", 1)               # 先记账再尝试开口(尝试数 == 扣减数)
        if transport == "stub":
            script = [l.encode("utf-8") + b"\r\n" for l in fixture.get("boot_bytes", [])]
            if silent:
                script = [None]
            port = FakePort(script, led, clock=clock)
        else:
            try:
                port = make_real_port(port_name, led)
            except (OSError, Stop) as e:
                # 额度已经消费、尝试已经发生: 具名上报而不是让它在通用异常里变成 UNEXPECTED
                raise Stop("PORT_OPEN_FAILED",
                           "%s:%s" % (port_name, getattr(e, "code", e.__class__.__name__)))
        led.ev("PORT_OPEN", transport=transport,
               port=port_name if transport == "real" else None)
        # (整包共用一本账: quota/used/spend 都在 try 之前绑定, 见上面那里)
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
                if st["action"] == "reset":
                    # 复位把核从 Halted 放出来: 启动流必须在第一条查询之前消费掉。
                    drain_post_reset_boot(port, st, asname, out, idx, led, results, clock,
                                          fixture, transport)
                continue
            cmd = st["cmd"]
            selected_window = None
            if st.get("new_log_window"):
                cmd, selected_window = select_new_log_window(st["new_log_window"], results, context)
                led.ev("NEW_LOG_WINDOW", **selected_window)
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
            if st.get("poll"):
                # 有限轮询取证(只在申报的重试码内等): 到期/不一致 => 撤能由 finally 统一走
                poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture,
                                  silent, led, clock, budget, results)
                continue
            v, nbytes, txt = command_attempt(port, cmd, st, asname, raw, step_dl, transport,
                                             fixture, silent, led, clock)
            results.append(dict({"as": asname, "kind": "command", "cmd": cmd,
                                 "rx_bytes": nbytes, "rx_sha256": sha_of(raw),
                                 "txt": txt, "selected_log_window": selected_window}, **v))
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
            try:
                disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture,
                                             hook, quota_box if plan else None,
                                             cleanup_overruns, used)
                errors.extend(cerr)          # 清理错独立留账: 主错在 ret, 不能被吞成空成功
            except Exception as e:
                # 清理框架自己炸了: 禁能一律 UNKNOWN(不猜"应该已经撤了"), 但端口仍要关
                disarm_state = "UNKNOWN"
                errors.append("CLEANUP_FRAME_RAISED_%s:%s" % (e.__class__.__name__, repr(e)))
                led.ev("CLEANUP_FRAME_EXCEPTION", err=repr(e))
        try:
            if port is not None:
                port.close()
                led.ev("PORT_CLOSED")
            else:
                led.ev("PORT_NEVER_OPENED")
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
def _kernel_builtin_cmd_sources(app_dir):
    """从 applications 往上找含 components/finsh/msh.c 的内核根(最多 8 层)。
    msh.c 是内置命令的导出处(msh.c:58 help / :74 ps / :93 free / :148 bind), 不在 applications 里;
    只扫 applications 会把**确实存在**的 ps/free 判成"发明命令"(上一轮板上实测 ps/free 都有应答)。"""
    p = Path(app_dir).resolve()
    for _ in range(8):
        cand = p / "components" / "finsh" / "msh.c"
        if cand.exists():
            return [cand]
        if p.parent == p:
            break
        p = p.parent
    return []


def msh_command_names(app_dir):
    """从产品源码里刮出**真实存在的** msh 命令名(MSH_CMD_EXPORT / _ALIAS 的第二参)。
    计划里的每条命令都必须在这张表里: 这是"不发明命令"的机器判据, 不是注释。"""
    names = set()
    pat = re.compile(r"MSH_CMD_EXPORT(?:_ALIAS)?\(\s*([A-Za-z_][0-9A-Za-z_]*)\s*(?:,\s*([A-Za-z_][0-9A-Za-z_]*))?")
    sources = list(Path(app_dir).glob("*.c")) + _kernel_builtin_cmd_sources(app_dir)
    for f in sources:
        for mo in pat.finditer(f.read_text(encoding="utf-8", errors="replace")):
            # MSH_CMD_EXPORT(fn, 描述) => 命令名就是函数名; 只有 _ALIAS 的**第二个**参数才是命令名
            # (finsh.h:101-112)。把 EXPORT 的第二参当命令名会刮出 "print"/"show" 这类描述词。
            names.add(mo.group(2) if "_ALIAS" in mo.group(0) else mo.group(1))
    return names


def prove_state_and_flag_constants(app_dir):
    """把执行器里的两张常量表**回到产品源码**逐项核对(与 prove_needles 同一纪律):
      motor.h 的 motor_state_t 枚举(隐式递增) 与 diag_relative.h 的 RPF_* 位。
    返回 (ok, detail)。名字或数值漂了就必须在这里 FAIL, 而不是等现场判错。"""
    bad = []
    mh = Path(app_dir) / "motor.h"
    if not mh.exists():
        return False, "motor.h_missing"
    body = re.search(r"typedef enum\s*\{(.*?)\}\s*motor_state_t;", mh.read_text(encoding="utf-8",
                                                                                   errors="replace"),
                     re.S)
    if not body:
        return False, "motor_state_t_enum_not_found"
    found, value = {}, -1
    for mo in re.finditer(r"\bMOTOR_([A-Z_]+)\s*(?:=\s*(\d+))?\s*(?:,|\n)", body.group(1)):
        v = int(mo.group(2)) if mo.group(2) else value + 1
        value = v
        found[mo.group(1)] = v
    for k, want in MOTOR_STATE_ENUM.items():
        if found.get(k) != want:
            bad.append("MOTOR_%s=源码%s/执行器%s" % (k, found.get(k), want))
    dh = Path(app_dir) / "diag_relative.h"
    if not dh.exists():
        return False, "diag_relative.h_missing" + (";" + ",".join(bad) if bad else "")
    bits = {mo.group(1): 1 << int(mo.group(2)) for mo in re.finditer(
        r"#define\s+RPF_([A-Z_]+)\s+\(1u\s*<<\s*(\d)\)", dh.read_text(encoding="utf-8",
                                                                       errors="replace"))}
    for k, want in RPF_BITS.items():
        if bits.get(k) != want:
            bad.append("RPF_%s=源码%s/执行器%s" % (k, bits.get(k), want))
    hdr = re.search(r'rt_kputs\("(slot,seq,[^"]*)"|rt_snprintf\([^;]*?"(slot,seq,[^"]*)"',
                    (Path(app_dir) / "ns_flash_shell.c").read_text(encoding="utf-8", errors="replace")
                    if (Path(app_dir) / "ns_flash_shell.c").exists() else "")
    src_header = (hdr.group(1) or hdr.group(2)).replace("\\n", "").strip() if hdr else None
    if src_header and src_header.strip() != FLASH_DUMP_HEADER:
        bad.append("FLASH_DUMP_HEADER 漂移")
    return (not bad), (";" .join(bad) if bad else
                       "motor=%s rpf=%s columns=%d" % (found, bits, FLASH_DUMP_COLUMNS))


def target_channel_claim(stage, recorded):
    """把"这一场流程跑完"和"目标传感器通道验证成功"分开判。

    复审实测: 旧版把 IMU 场喂了 SG 的触发数据(`r=REL_SG_SEVERE`、`imu=0`)仍然判通过 —— 规则名
    与源样本通道都没人对。这里只认计划里申报的**枚举名**(来自 rel_reason_t 与 verdict_name()),
    并要求该场的源样本字段确实非零; 不匹配就具名成"其他来源触发/目标通道未证", 照常撤能与取证,
    但绝不计入保护已验证。"""
    spec = stage.get("target_channel") or {}
    if not spec:
        return None, ""
    rule = recorded.get(spec.get("rule_key", ""))
    if rule is None:
        return ("TARGET_UNPROVEN(NO_TRIP_RULE_CAPTURED)",
                "本场没有抓到 TRIP 规则名(缺 %s)" % spec.get("rule_key"))
    known = set(spec.get("known_rule_names") or [])
    if known and rule not in known:
        return ("TARGET_UNPROVEN(RULE_NOT_IN_PRODUCT_ENUM)", "规则名 %s 不在产品枚举里" % rule)
    targets = set(spec.get("rule_names") or [])
    if rule not in targets:
        return ("OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)",
                "触发规则=%s, 本场目标通道只认 %s" % (rule, sorted(targets)))
    zero = [k for k in (spec.get("source_nonzero_keys") or [])
            if str(recorded.get(k)) in ("0", "None", "")]
    if zero:
        return ("TARGET_RULE_BUT_SOURCE_FIELD_ZERO(%s)" % ",".join(zero),
                "规则对了但源样本通道仍是 0, 不能称该通道已验证")
    return ("VERIFIED", "规则=%s 且源样本通道非零" % rule)


def rel_rule_names(app_dir):
    """产品里真实存在的相对判定原因名: 从 rel_reason_t 枚举 + verdict_name() 的返回串两头取,
    两头不一致就报错。计划只能引用这份名单, 不许自己造规则名(复审抓到的正是 IMU 场喂了
    REL_SG_SEVERE 还算通过)。"""
    hdr = Path(app_dir) / "diag_relative.h"
    src = Path(app_dir) / "diag_relative.c"
    txt_h = hdr.read_text(encoding="utf-8", errors="replace")
    txt_c = src.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"typedef\s+enum\s*\{(.*?)\}\s*rel_reason_t\s*;", txt_h, re.S)
    enum_names = re.findall(r"^\s*(REL_[A-Z_]+)", m.group(1), re.M) if m else []
    printed = set(re.findall(r"case\s+(REL_[A-Z_]+):\s*return\s+\"\1\";", txt_c))
    names = {n for n in enum_names if n in printed}
    if not m or names != set(enum_names) & printed or not names:
        raise Stop("REL_RULE_NAMES_SOURCE_MISMATCH",
                   "enum=%s printed=%s" % (sorted(enum_names), sorted(printed)))
    return names


def derive_quota_needs(plan):
    """③ 额度从**真实执行路径**推导, 不是拍一个数: 逐阶段按执行器实际会 spend 的名字累加。
      - port_open: 每个 serial_session 阶段恰好扣 1(exec_session 开口前先 spend)
      - 步骤级: st["quota"](cmd 步)与 st["quota_name"]/st["action"](action 步)
      - 阶段级: kind 本身就是工具名的阶段(download/readback/reset)与 operator_gate 的 quota_name
      - cleanup_*: 每个**可能建立撤能义务**的阶段(含 motor_arm/motor_start cmd)各触发一次三条清理
    推导值是**下限**: 申报少于推导 => 现场会在某一步 QUOTA_EXHAUSTED/RESERVE_NOT_DECLARED 停,
    而那一次批准已经花掉了, 所以必须在离线就拒绝。"""
    need = {}
    stages = plan["stages"]

    def bump(name, n=1):
        need[name] = need.get(name, 0) + n

    for stg in stages:
        kind = stg.get("kind")
        if kind == "serial_session":
            bump("port_open")
        elif kind in ("download", "readback", "reset"):
            bump(kind)
        elif kind == "operator_gate":
            bump(stg.get("quota_name", "approved_reboot"))
        sets_obligation = False
        for st in stg.get("steps", []):
            if st.get("quota"):
                bump(st["quota"]["name"], int(st["quota"]["n"]))
            if st.get("action"):
                bump(st.get("quota_name", st["action"]))
            if st.get("cmd") in MOTION_OBLIGATION_CMDS:
                sets_obligation = True
        if sets_obligation:
            for cmd in CLEANUP_CMDS:
                bump("cleanup_" + cmd)
    return need


def identity_consistency(plan):
    """① S0 / 下载 / 回读**必须指向同一个镜像身份**。这一项是纯离线结构判据:
    之前就是 readback 仍绑上一轮的 BIN, 用新 BIN 原字节跑真实回读函数立刻 READBACK_HASH_MISMATCH。"""
    ident = plan.get("identity_binding", {})
    tools = plan.get("tools", {})
    img = ident.get("image", {})
    axf = img.get("axf", {})
    dl = (tools.get("download") or {}).get("image") or {}
    rb = (tools.get("readback") or {}).get("image") or {}
    argv = (tools.get("download") or {}).get("argv") or []
    bad = []
    if not img.get("sha256"):
        bad.append("S0_IMAGE_MISSING")
    for k, want, got in (
            ("READBACK_TARGET_IMAGE", (img.get("path"), int(img.get("bytes", 0)), img.get("sha256")),
             (rb.get("path"), int(rb.get("bytes", 0)) if rb.get("bytes") else None, rb.get("sha256"))),
            ("DOWNLOAD_PAYLOAD_AXF", (axf.get("path"), int(axf.get("bytes", 0)), axf.get("sha256")),
             (dl.get("path"), int(dl.get("bytes", 0)) if dl.get("bytes") else None, dl.get("sha256")))):
        if want != got:
            bad.append("%s|want=%s|got=%s" % (k, want, got))
    if argv and axf.get("path") and argv[-1] != axf["path"]:
        bad.append("DOWNLOAD_ARGV_NOT_AXF_PATH|%s" % argv[-1])
    before = (ident.get("board_image_before") or {}).get("sha256")
    if before and rb.get("sha256") == before:
        bad.append("READBACK_STILL_BOUND_TO_BOARD_IMAGE_BEFORE|%s" % before[:12])
    return bad


def first_cause_leg_coverage(plan, stage):
    spec = stage.get("first_cause_legs") or {}
    required = {"trip_row", "source_sample", "profile_identity", "incident_session"}
    got = set(spec.get("covers", []))
    return sorted(required - got), spec


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
        if stg.get("reset_after_port_open"):
            # 定长回读用 connect_mode=halt + resume_on_disconnect=false, 结束时核是 **Halted**
            # (审核 §2A): 核心还没跑, 第一条串口查询必然没有应答。所以这一步的结构判据是
            # "复位在最前 + 启动流已申报", 不是"等它自己冒提示符"。
            steps = stg.get("steps", [])
            first_cmd = next((i for i, s in enumerate(steps) if s.get("cmd")), None)
            first_reset = next((i for i, s in enumerate(steps)
                                if s.get("action") == "reset"), None)
            ok = (first_reset == 0 and (first_cmd is None or first_reset < first_cmd)
                  and bool(stg.get("post_reset_boot")))
            out.append("PLAN_RESET_BEFORE_FIRST_QUERY|%s|%s|reset@%s first_cmd@%s boot=%d" %
                       (sid, "PASS" if ok else "FAIL", first_reset, first_cmd,
                        len(stg.get("post_reset_boot") or [])))
            if not ok:
                bad += 1
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
    # 状态/位/表头三张常量表回到源码核对(不靠注释声明"我是照抄产品的")
    ok_c, detail_c = prove_state_and_flag_constants(app_dir)
    out.append("PLAN_STATE_AND_FLAG_CONSTANTS|all|%s|%s" % ("PASS" if ok_c else "FAIL", detail_c))
    if not ok_c:
        bad += 1
    # 起转上限按阶段申报, 总额受 quota.motor_run_seconds_total 全局审计(审核 §3 新离线合同)
    caps = [(sg["id"], int(sg["motion_cap_seconds"])) for sg in plan["stages"]
            if sg.get("motion_cap_seconds")]
    starts = sum(1 for sg in plan["stages"] for st in sg.get("steps", [])
                 if st.get("cmd") == "motor_start")
    total = int(quota.get("motor_run_seconds_total", 0))
    ok_t = caps and sum(c for _, c in caps) <= total and starts <= int(quota.get("motor_run", 0)) \
        and all(c <= total for _, c in caps)
    out.append("PLAN_MOTION_CAP_BUDGET|all|%s|caps=%s sum=%d total=%d starts=%d" %
               ("PASS" if ok_t else "FAIL", caps, sum(c for _, c in caps), total, starts))
    if not ok_t:
        bad += 1
    # ① 镜像身份: S0/下载/回读三处必须同源(离线就拒绝, 不等现场 READBACK_HASH_MISMATCH)
    ib = identity_consistency(plan)
    out.append("PLAN_IMAGE_IDENTITY_CONSISTENCY|all|%s|%s" %
               ("PASS" if not ib else "FAIL", ";".join(ib) or
                "S0=download.axf=readback.bin 同源且回读不认旧镜像"))
    if ib:
        bad += 1
    # ③ 额度按真实执行路径推导: 申报 < 推导 就是"这一轮批准会在中途被额度停掉"
    needs = derive_quota_needs(plan)
    for name in sorted(set(needs) | set(quota)):
        want, have = int(needs.get(name, 0)), int(quota.get(name, 0))
        if want == 0 and have == 0:
            continue
        ok_q = have >= want
        out.append("PLAN_QUOTA_NEED|%s|%s|need=%d declared=%d" % (name, "PASS" if ok_q else "FAIL",
                                                                  want, have))
        if not ok_q:
            bad += 1
    # ② 场景链: 带 profiles 标签的阶段必须排在自己那条链的分支决定点之后
    decided = {}
    chain_bad = []
    for stg in plan["stages"]:
        sc = stg.get("scenario")
        if not sc:
            continue
        if stg.get("profiles") and not decided.get(sc):
            chain_bad.append("%s:no_branch_decider_before_it(scenario=%s)" % (stg["id"], sc))
        if stg.get("decides_response_branch"):
            decided[sc] = stg["id"]
    out.append("PLAN_SCENARIO_CHAIN|all|%s|%s" %
               ("PASS" if not chain_bad else "FAIL",
                ";".join(chain_bad) or "链内分支决定点都在依赖阶段之前: %s" %
                json.dumps(decided, ensure_ascii=False)))
    if chain_bad:
        bad += 1
    # 离线申报的按链分支: 场景名必须真实存在, 档案取值必须只有三种(拼错会静默不生效)
    sc_all = {sg.get("scenario") for sg in plan["stages"] if sg.get("scenario")}
    sb_bad = []
    for sc, prof in (plan.get("stub_branches") or {}).items():
        if sc not in sc_all:
            sb_bad.append("%s:未知场景" % sc)
        if prof not in ("healthy", "trip", "no_trigger"):
            sb_bad.append("%s:档案=%s" % (sc, prof))
    out.append("PLAN_STUB_BRANCHES|all|%s|%s" %
               ("PASS" if not sb_bad else "FAIL",
                ";".join(sb_bad) or "申报=%s 场景=%s" %
                (json.dumps(plan.get("stub_branches") or {}, ensure_ascii=False),
                 ",".join(sorted(sc_all)))))
    if sb_bad:
        bad += 1
    # ⑤ 机械前置: 申报 blocks_motion 的前置**必须**挂在起转阶段的 prerequisite_gates 上,
    #    否则它只是说明字段, 执行器不会在起转前拒绝。
    pre_bad = []
    for stg in plan["stages"]:
        has_motion = any(st.get("cmd") in MOTION_OBLIGATION_CMDS for st in stg.get("steps", []))
        for key, val in (stg.get("prerequisites") or {}).items():
            if isinstance(val, dict) and val.get("blocks_motion") is True and has_motion:
                pkg = (plan.get("mechanical_prerequisites") or {}).get(key)
                if key not in (stg.get("prerequisite_gates") or []):
                    pre_bad.append("%s:%s=blocks_motion 但未挂 prerequisite_gates" % (stg["id"], key))
                elif not pkg:
                    pre_bad.append("%s:%s 没有包级 mechanical_prerequisites 条目" % (stg["id"], key))
    out.append("PLAN_PREREQUISITE_GATE|all|%s|%s" %
               ("PASS" if not pre_bad else "FAIL",
                ";".join(pre_bad) or "起转前真拒绝已接线: %s" %
                json.dumps({k: v.get("status") for k, v in
                            (plan.get("mechanical_prerequisites") or {}).items()},
                           ensure_ascii=False)))
    if pre_bad:
        bad += 1
    # ⑤' 每一个**起转/带电**阶段都必须挂机械前置门(复审: 缺项只挡 S8 是不够的, S4 学习与 S8b 也起转)
    spin_bad = []
    for stg in plan["stages"]:
        spins = any(st.get("cmd") in MOTION_OBLIGATION_CMDS for st in stg.get("steps", []))
        if spins and not (stg.get("prerequisite_gates") or []):
            spin_bad.append("%s(有 motor_arm/motor_start 但 prerequisite_gates 为空)" % stg["id"])
    out.append("PLAN_SPINUP_GATE_COVERAGE|all|%s|%s" %
               ("PASS" if not spin_bad else "FAIL",
                ";".join(spin_bad) or "所有起转/带电阶段都在开端口前检查机械前置"))
    if spin_bad:
        bad += 1
    # ④' 目标通道声明: 规则名必须是产品枚举里的名字, 且每个分支决定点都必须声明目标通道
    try:
        known = rel_rule_names(app_dir)
    except Stop as e:
        known = set()
        out.append("PLAN_TARGET_CHANNEL|all|FAIL|%s" % e.code)
        bad += 1
    tc_bad = []
    for stg in plan["stages"]:
        spec = stg.get("target_channel")
        if stg.get("decides_response_branch") and not spec:
            tc_bad.append("%s:分支决定点没声明 target_channel" % stg["id"])
            continue
        if not spec:
            continue
        unknown = [x for x in spec.get("rule_names") or [] if x not in known]
        if unknown:
            tc_bad.append("%s:规则名不在产品枚举%s" % (stg["id"], unknown))
        if not spec.get("rule_names"):
            tc_bad.append("%s:rule_names 为空" % stg["id"])
        if not spec.get("rule_key"):
            tc_bad.append("%s:rule_key 未指向记录下来的 TRIP 规则键" % stg["id"])
    out.append("PLAN_TARGET_CHANNEL|all|%s|%s" %
               ("PASS" if not tc_bad else "FAIL",
                ";".join(tc_bad) or "产品枚举=%s" % ",".join(sorted(known))))
    if tc_bad:
        bad += 1
    # ④ 首因腿: 每条会决定 trip 分支的链都必须覆盖四路取证, 且缺证的具名码是 EVIDENCE_INCOMPLETE
    fc_bad = []
    for stg in plan["stages"]:
        if stg.get("decides_response_branch"):
            miss, spec = first_cause_leg_coverage(plan, stg)
            if miss:
                fc_bad.append("%s:缺腿%s" % (stg["id"], miss))
            elif spec.get("on_missing") != "EVIDENCE_INCOMPLETE":
                fc_bad.append("%s:on_missing 不是 EVIDENCE_INCOMPLETE" % stg["id"])
            elif not spec.get("legs"):
                fc_bad.append("%s:legs 为空" % stg["id"])
    out.append("PLAN_FIRST_CAUSE_LEGS|all|%s|%s" %
               ("PASS" if not fc_bad else "FAIL",
                ";".join(fc_bad) or "trip 链都有同次 REL TRIP/源样本/profile/新 session 四腿"))
    if fc_bad:
        bad += 1
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


FLASH_LOGSTAT_ROW = r"^slots=(?P<used>[0-9]+)/(?P<capacity>[0-9]+) valid=(?P<valid>[0-9]+) bad=(?P<bad>[0-9]+)$"


def select_new_log_window(spec, results, context):
    """Select only appended committed slots; event storage is stop-when-full, not a ring."""
    if context is None or spec["before_key"] not in context:
        raise Stop("NEW_LOG_BASELINE_MISSING")
    before = int(context[spec["before_key"]])
    rows = [r for r in results if r.get("as") == spec["stats_step"] and r.get("txt")]
    if len(rows) != 1:
        raise Stop("NEW_LOG_STATS_MISSING")
    text = Path(rows[0]["txt"]).read_text(encoding="utf-8")
    hits = list(re.finditer(FLASH_LOGSTAT_ROW, text, re.M))
    if len(hits) != 1:
        raise Stop("NEW_LOG_STATS_NOT_UNIQUE")
    after, capacity = int(hits[0]['used']), int(hits[0]['capacity'])
    if not 0 <= before < after <= capacity <= 4096:
        raise Stop("NEW_LOG_NO_APPENDED_RANGE", "before=%d after=%d capacity=%d" % (before, after, capacity))
    count = min(after - before, int(spec.get("max_frames", 8)))
    if not 1 <= count <= 100:
        raise Stop("NEW_LOG_COUNT_OUT_OF_RANGE")
    first = after - count
    return "flash_dump %d %d" % (first, count), {"before": before, "after": after,
                                                "first": first, "count": count}


def flash_frames_verdict(stage, results, recorded, led):
    """事故取证要**读回到实际日志帧**(审核 §2D 第 4 条): 计数增大只是"本轮确实多写了一笔"的
    身份前置, 不是日志内容。这里复用 M5/M6 既有的 flash_dump 原字节归档与同一张 14 列表头
    (ns_flash_shell.c:153/159-163), 不新造日志判定工具, 也不重跑 300 帧软件触发。

    逐帧行必须: 列数=14、session 列 == 本轮记录的新 session、批内没有 empty/error/overflow
    (产品读槽时内部校 CRC/commit, 不过就打成 "slot N error -x" => 有这类行就是取证未完成)。"""
    bad = []
    got = dict((r.get("as"), r.get("txt")) for r in results if r.get("txt"))
    for spec in stage.get("require_frames", []):
        txt = got.get(spec["step"])
        if not txt:
            bad.append("%s:STEP_TEXT_MISSING" % spec["step"])
            continue
        rows = Path(txt).read_text(encoding="utf-8", errors="replace").splitlines()
        frames, read_errors = [], []
        for line in rows:
            s = line.strip()
            if not s or s == FLASH_DUMP_HEADER:
                continue
            if any(b in s for b in FLASH_DUMP_BAD):
                read_errors.append(s[:80])
                continue
            parts = s.split(",")
            if len(parts) == FLASH_DUMP_COLUMNS and all(
                    re.fullmatch(r"[0-9]+" if i < 6 or i == 13 else r"-?[0-9]+", p)
                    for i, p in enumerate(parts)):
                frames.append(parts)
        selection = next((r.get("selected_log_window") for r in results
                          if r.get("as") == spec["step"]), None)
        want = recorded.get(spec["session_key"])
        keyed = [f for f in frames if want is not None and f[2] == str(want)]
        miss = []
        if not frames:
            miss.append("CSV_FRAMES=0")
        if not selection:
            miss.append("NEW_LOG_WINDOW_NOT_RECORDED")
        else:
            slots = [int(f[0]) for f in frames]
            expected = set(range(selection["first"], selection["first"] + selection["count"]))
            if len(slots) != len(expected) or set(slots) != expected or min(expected) < selection["before"]:
                miss.append("NEW_LOG_SLOT_COVERAGE_MISMATCH")
        if want is None:
            miss.append("SESSION_NOT_RECORDED(%s)" % spec["session_key"])
        elif len(keyed) < int(spec.get("min_frames", 1)):
            miss.append("SESSION_FRAMES=%d<%d(session=%s)" %
                        (len(keyed), int(spec.get("min_frames", 1)), want))
        if read_errors:
            miss.append("SLOT_READ_ERRORS=%d:%s" % (len(read_errors), read_errors[0]))
        for need in spec.get("must_include", []):
            hit = [f for f in keyed
                   if all(f[FLASH_DUMP_HEADER.split(",").index(k)] == str(v)
                          for k, v in need.items())]
            if not hit:
                miss.append("NO_FRAME_WITH(%s)" % json.dumps(need, sort_keys=True))
        led.ev("FLASH_FRAMES", **{"as": spec["step"], "csv_frames": len(frames),
                                  "session_frames": len(keyed), "session": want,
                                  "read_errors": len(read_errors), "bad": miss})
        if miss:
            bad.append("%s:%s" % (spec["step"], ",".join(miss)))
    return bad


def build_session_fixture(stage, table, profile):
    """按阶段步骤 + 行表 + 分支档案装配一个会话的响应表(不含任何人工门的答案)。"""
    responses = {}
    sequence = {}
    block = {}
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
        if st.get("poll_sequence"):
            # 轮询的响应序列也要**申报**: 第 1 次读到 ACCEL、之后读到 CRUISE 是行表里的两组,
            # 不是执行器临时编出来的。
            sequence[key] = [rows_for(table, k, profile) for k in st["poll_sequence"]]
            responses[key] = sequence[key][-1]
        bs = (table["commands"].get(key) or {}).get("_block_seconds")
        if bs:
            block[key] = float(bs)
    # 清理三条命令**总是**要在同一张表里有一行: 义务建立后的 stop/disarm/status 是执行器
    # 自己发的, 不是阶段步骤 —— 少一份响应就会变成"清理查不到东西"的假 NO_RESPONSE。
    for cmd in CLEANUP_CMDS:
        if cmd not in responses:
            responses[cmd] = rows_for(table, cmd, profile)
    return {"boot_bytes": list(stage.get("boot_bytes", [])), "responses": responses,
            "sequence": sequence, "block": block,
            "post_reset_boot": list(stage.get("post_reset_boot", [])),
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


# ---------------------------------------------------- 模式分流与响应分支(R3 复审 §2C)
# 离线工装与真实执行是两条互不相交的路: 混合模式既没有离线工装的确定性, 也没有真实执行的
# 身份交付门 —— `transport=real + backend=sim` 会把"模拟下载"记成真实执行, 反过来
# `transport=stub + backend=real` 会在假端口上跑真工具。两者都必须在**任何 UID 读取/工具调用/
# 开口动作之前**具名拒绝, 而不是走到某一步才偶然发现。
ALLOWED_MODES = {("stub", "sim"), ("real", "real"), ("real", "manual")}
STUB_PROFILES = ("healthy", "trip", "no_trigger", "denied", "silent")
REAL_PROFILE = "response_driven"


def mode_gate(transport, backend_name, profile=None):
    """合法组合返回 "", 否则返回具名拒绝码。真实入口只接受一次 dispatch + 一本总账 +
    **由本次响应决定分支**的 profile; 预选夹具档案(healthy/trip/no_trigger)与 -Profile all
    的多遍整包只属于离线工装。profile=None 只核模式组合(单会话入口没有档案概念)。"""
    if (transport, backend_name) not in ALLOWED_MODES:
        return "MODE_COMBINATION_REFUSED"
    if profile is None:
        return ""
    if transport == "real":
        return "" if profile == REAL_PROFILE else "REAL_PROFILE_NOT_RESPONSE_DRIVEN"
    return "" if profile in STUB_PROFILES else "STUB_PROFILE_UNKNOWN"


def response_branch(res, stage):
    """本次响应决定的分支: 观察窗到时无触发 => no_trigger; 窗内出现申报过的触发 needle
    (会话按业务正常结束, 而不是 OBSERVATION_NO_TRIGGER) => trip。两者都没证据 => None,
    此时依赖分支的阶段一律不许默默跳过, 而是 RESPONSE_BRANCH_UNDETERMINED 停止。

    只有**申报了自己是分支决定点**的阶段才参与判定(decides_response_branch): 学习会话里的
    到时未触发是常态, 把它当"本次没有事故"来决定事故支线就是拿错的观察点下结论。"""
    if not stage.get("decides_response_branch"):
        return None
    seen = any(r.get("kind") == "watch" and r.get("trigger_seen") for r in res.get("results", []))
    if res.get("status") == "OBSERVATION_NO_TRIGGER" and seen:
        # 同一份观察里已经见过触发针, 却把本次记成"到时未触发" —— 这是把已观察到的触发
        # 降级, 不是判读分歧。具名停止, 不返回分支。
        raise Stop("TRIP_REWRITTEN_AS_NO_TRIGGER",
                   "%s 窗内已见申报的触发针, 但会话终态是 OBSERVATION_NO_TRIGGER" % stage.get("id"))
    if res.get("status") == "OBSERVATION_NO_TRIGGER":
        return "no_trigger"
    if seen:
        return "trip"
    return None


def first_cause_verdict(stage, results, recorded, branch, led):
    """④ 首因合同(可执行, 不是说明字段): 同一次的 REL TRIP/LATCH 行、profile 身份、源样本值
    与本轮新日志 session 必须**互相咬合**。缺任何一条腿 => 具名 EVIDENCE_INCOMPLETE。

    只在分支=trip 时评估: 没有触发就没有首因可主张; 而已经观察到的触发也**不许**被改写成
    NO_TRIGGER(那条降级由 response_branch() 的 TRIP_REWRITTEN_AS_NO_TRIGGER 守)。
    legs 按声明顺序求值: 后面的 equal_pairs 只能用前面腿已经抓到的字段。"""
    spec = stage.get("first_cause_legs") or {}
    if not spec:
        return []
    if branch != spec.get("applies_when_branch", "trip"):
        return []
    got = dict((r.get("as"), r.get("txt")) for r in results if r.get("txt"))
    caps, bad = {}, []
    for leg in spec.get("legs", []):
        name = leg.get("leg", "?")
        if leg.get("recorded_keys"):
            miss = [k for k in leg["recorded_keys"] if recorded.get(k) is None]
            if miss:
                bad.append("%s:RECORDED_MISSING(%s)" % (name, ",".join(miss)))
            continue
        if leg.get("equal_pairs"):
            broken = ["%s=%s!=%s=%s" % (a, caps.get(a), b, caps.get(b))
                      for a, b in leg["equal_pairs"]
                      if caps.get(a) is None or caps.get(b) is None or caps[a] != caps[b]]
            if broken:
                bad.append("%s:%s" % (name, ";".join(broken)))
            continue
        txt = got.get(leg["step"])
        if not txt:
            bad.append("%s:STEP_TEXT_MISSING(%s)" % (name, leg["step"]))
            continue
        m = re.search(leg["regex"], Path(txt).read_text(encoding="utf-8", errors="replace"), re.M)
        if not m:
            bad.append("%s:PATTERN_NOT_FOUND" % name)
            continue
        d = m.groupdict()
        for k in leg.get("capture", []):
            if d.get(k) is None:
                bad.append("%s:NO_CAPTURE_GROUP(%s)" % (name, k))
            else:
                caps[k] = d[k]
        for fld, key in (leg.get("must_equal_recorded") or {}).items():
            if caps.get(fld) is None or str(recorded.get(key)) != caps[fld]:
                bad.append("%s:NOT_EQUAL_RECORDED(%s!=recorded.%s=%s)" %
                           (name, caps.get(fld), key, recorded.get(key)))
        # 抓到的值按链存进整包账, 让后面那场自己的取证会话能咬回**同一次**事故
        for k in leg.get("record_as", []):
            src = k.rsplit("_", 1)[0]
            if caps.get(src) is None:
                bad.append("%s:RECORD_AS_MISSING(%s)" % (name, k))
            else:
                recorded[k] = caps[src]
    led.ev("FIRST_CAUSE_LEGS", **{"stage": stage.get("id"), "branch": branch,
                                  "captured": caps, "incomplete": bad})
    return bad


def dispatch_refusal(plan, profile, outdir, code):
    """拒绝分支也要带回调度层的完整字段: 调用方会照样打印 stages/not_run/quota,
    少一个键就是 KeyError, 现场只会看到崩栈而不是"被拒绝"。"""
    return {"status": "REFUSED", "code": code, "stages": [],
            "not_run": [s["id"] for s in plan["stages"]], "recorded": {},
            "quota_left": dict(plan["quota"]), "quota_used": {},
            "expected_stages": [], "profile": profile, "dir": str(outdir),
            "branch": None, "scenario_branches": {},
            "protection_claims": {}, "core_protection_verified": False, "flow_complete": False,
            "prerequisite_skips": [], "prerequisite_refusals": [],
            "actions_attempted": [],
            "quota_audit": {"undeclared_used": [], "negative_left": [], "lines": []},
            "ledger": str(Path(outdir) / "dispatch_ledger.jsonl")}


def dispatch_plan(plan, transport, backend_name, outdir, clock, profile, port,
                  repo_root, approval_token=None, expected_token=None, uid_file=None,
                  plan_dir=None):
    """一次批准 -> 从 S0 一直走到**计划里的最后一个阶段**; 成功阶段只记检查点(不为下一阶段
    重新批准、不重新下载), **失败**才终止。
    整包共用同一本额度账(quota_box)与同一个动作后端; 每个串口会话仍各开一次端口。
    任何会话非成功(含清理 UNKNOWN / 清理错误 / 预留耗尽 / 证据失败)都阻断后续阶段并非零返回。"""
    code = mode_gate(transport, backend_name, profile)
    if code:
        # 模式账先于一切: 这一刻 UID 没读、工具没起、端口没开, 所以"副作用清单为空"是真空而不是漏记。
        return dispatch_refusal(plan, profile, outdir, code)
    if transport == "real" and (not approval_token or approval_token != expected_token):
        # 批准门也在读私有 UID 之前: 没批准就没有任何一次私有材料的消费。
        return dispatch_refusal(plan, profile, outdir, "REAL_TRANSPORT_REFUSED")
    quota_box = dict(plan["quota"])
    used_box = {}

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    led_path = out / "dispatch_ledger.jsonl"
    led = Ledger(led_path, clock, out_dir=out)
    img_bytes = int(plan["identity_binding"]["image"]["bytes"])
    # `@RUN_ROOT` 会被展开进 pyOCD 的 `-c` **命令字符串**里, 而 commander 对该串做 shlex 解析:
    # Windows 的反斜杠在这里是转义符, 会把 D:\ns800\... 咬成 D:ns800...(首次真跑的回读就是这样 rc=1)。
    # 所以这两个根都必须是正斜杠形式; Path 在 Windows 上照样能拼, 不改变指向的文件。
    subs = {"@RUN_ROOT": out.as_posix(), "@IMAGE_BYTES": "0x%X" % img_bytes}
    if uid_file:
        subs["@UID_FILE"] = uid_from_file(uid_file)
    backend_cls = {"sim": SimActionBackend, "manual": ManualActionBackend,
                   "real": RealActionBackend}[backend_name]
    backend = (backend_cls(led, out, clock, 900, subs, repo_root=repo_root)
               if backend_cls is RealActionBackend else backend_cls(led, out, clock, 900, subs))
    table = load_rows(Path(plan_dir or out.parent) / plan.get("rows_file", "rows.json"))
    recorded = {}
    stages, done, notrun = plan["stages"], [], []
    # 分支: 离线由档案**声明**; 真实由本次响应**决定**(response_branch), 决定前依赖分支的阶段不许走。
    # 分支的作用域是**场景链**(stages[].scenario): 两路激励各自决定自己的分支, 一路触发、
    # 另一路未触发不是矛盾(混合结果照常跑完), 只有**同一条链**给出互相冲突的观察才是矛盾。
    branch = profile if transport == "stub" else None
    active_profile = profile
    # 离线的分支同样是**按链**给的: plan.stub_branches 可以声明 {场景: 档案}, 让两条链在这一轮
    # 里各自走 trip / no_trigger。没声明的链退回 --profile。这样"两场均触发/一触发一未触发"
    # 四种组合都能在零设备的工装上真实跑一遍, 而不是只在文档里声称覆盖。
    stub_branches = dict(plan.get("stub_branches") or {}) if transport == "stub" else {}
    scenario_branch, scenario_profile = {}, {}
    blocked_scenarios, skipped_by_prerequisite = {}, []
    skipped_by_scope = {}
    expected_ids = []
    for i, stg in enumerate(stages):
        sid = stg["id"]
        # 没标 scenario 的阶段各自成域: 否则 S4 的前置拒绝会把所有未分场的阶段一起"拉黑"
        sc = stg.get("scenario") or sid
        rest = [x["id"] for x in stages[i:]]
        # ⑤ 机械前置: 缺加载手段时在**起转之前**具名拒绝整条链 —— 不开端口、不扣额度、
        # 不发任何一条命令(而不是把约束写在 prerequisites 说明字段里照常往下走)。
        missing_pre = [g for g in stg.get("prerequisite_gates", [])
                       if (plan.get("mechanical_prerequisites", {}).get(g) or {}).get("status")
                       != "PROVIDED"]
        if missing_pre and sc not in blocked_scenarios:
            blocked_scenarios[sc] = {"stage": sid, "gates": missing_pre, "via_requires": []}
        # 前置拒绝**沿 requires 传播**: S4(带起转的学习)被装配就绪门挡下之后, 依赖它本次学习
        # 结果的 S5 tier/commit/save 不能继续跑 —— 那等于拿一份本轮没有学出来的 profile 去提交,
        # 现场只会中途失败并把唯一一次批准烧掉。传播只走计划里已申报的 requires, 不猜依赖。
        if not missing_pre and sc not in blocked_scenarios:
            up_refused = sorted({mo.group(1) for mo in
                                 (re.match(r"^(S\d+[a-z]?)", str(rq)) for rq in stg.get("requires", []))
                                 if mo and mo.group(1) in skipped_by_prerequisite})
            if up_refused:
                blocked_scenarios[sc] = {"stage": sid, "via_requires": up_refused,
                                         "gates": ["requires:%s 已被机械前置跳过" % u for u in up_refused]}
        if sc in blocked_scenarios:
            b = blocked_scenarios[sc]
            led.ev("STAGE_SKIPPED_BY_PREREQUISITE", stage=sid, scenario=sc,
                   gates=b["gates"], refused_before=b["stage"],
                   via_requires=b.get("via_requires") or [],
                   reason=("起转前拒绝: 机械前置未提供(不开口/不扣额度/不发命令)" if not b.get("via_requires")
                           else "依赖的阶段已被机械前置跳过, 本级继续跑就是在没有本次前置结果的前提下动作"))
            skipped_by_scope.setdefault(sc, []).append(sid)
            skipped_by_prerequisite.append(sid)
            continue
        br = (stub_branches.get(sc, profile) if transport == "stub"
              else scenario_branch.get(sc))
        ap = (stub_branches.get(sc, profile) if transport == "stub"
              else scenario_profile.get(sc, profile))
        tags = stg.get("profiles")
        if tags:
            if transport == "real" and br is None:
                done.append({"id": sid, "status": "STOP", "code": "RESPONSE_BRANCH_UNDETERMINED",
                             "detail": "%s(scenario=%s) 依赖 %s, 但本次响应还没给出分支"
                                       % (sid, sc, tags)})
                notrun = rest
                break
            if br not in tags:
                # 档案/分支不参与 = **不在期望序列里**, 因此既不算已执行也不算 NOT_RUN; 台账留具名跳过事件。
                led.ev("STAGE_SKIPPED_BY_PROFILE", stage=sid, profiles=tags, branch=br, scenario=sc)
                continue
        expected_ids.append(sid)
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
                payload["run_root"] = out.as_posix()      # 与 @RUN_ROOT 同形(见上面注释)
                r = run_action(payload, backend, led, {"session": sid})
                if r["verdict"] != "OK":
                    raise Stop(r["code"], r.get("detail", ""))
                done.append({"id": sid, "status": "OK", "kind": kind, "action": name})
                if stg.get("operator_checkpoint"):
                    led.ev("CHECKPOINT", stage=sid, note=stg["operator_checkpoint"])
                continue
            if kind == "serial_session":
                steps = stage_steps(stg, ap)
                fx = build_session_fixture(stg, table, ap)
                fx["profile"] = ap
                # 起转上限**按阶段**传入(R3 复审 §3): 学习 15 s、保护观察 5 s 是两种不同的
                # 时间合同, 不能都套同一个 5 s; 总额仍由 plan.quota.motor_run_seconds_total 全局审计。
                cap = int(stg.get("motion_cap_seconds",
                                  plan["quota"]["motion_cap_seconds_per_run"]))
                res = exec_session("S_" + sid, steps, out, transport, fx, port,
                                   int(stg.get("total_deadline_seconds", 120)), clock,
                                   backend=backend,
                                   motion_cap_seconds=cap,
                                   plan=plan, quota_box=quota_box, used_box=used_box, context=recorded)
                if transport == "real":
                    # 分支由**本次响应**决定, 作用域是本阶段所在的场景链: 未触发就把该链的事故
                    # 取证支线留在 NOT_RUN 之外, 真实触发就必须走该链的 S9/S10/S11 —— 不许用
                    # "先前选了一个夹具档案"把它跳过。另一条链的分支与本次结果互不推翻。
                    seen = response_branch(res, stg)
                    if seen:
                        prior = scenario_branch.get(sc)
                        if prior and prior != seen:
                            raise Stop("RESPONSE_BRANCH_CONFLICT",
                                       "%s(scenario=%s) 本次=%s 与该链已有=%s 相冲"
                                       % (sid, sc, seen, prior))
                        scenario_branch[sc] = seen
                        scenario_profile[sc] = seen
                        branch = seen
                        active_profile = seen
                        led.ev("RESPONSE_BRANCH", stage=sid, scenario=sc, branch=seen)
                # 计划可以**申报**某个具名拒绝就是这一阶段的目的(expected_terminal):
                # 该会话仍然在拒绝处结束(绝不继续 commit/save/start/arm/fault_reset), 危险面
                # 与不申报时完全一致, 只是不再把"观察到了预期拒绝"当成阶段失败。拒绝的原文与
                # code 照样进结果, 台账里仍是 status=REFUSED。
                # 计划可以申报"这一阶段的目的就是看见这条具名拒绝"。豁免的**只有业务结果**这一项:
                # 清理 UNKNOWN / 清理错 / 预留耗尽 / 证据失败一律仍然非成功。
                expected_refusal_ok = (
                    res["status"] == "REFUSED"
                    and res["status"] in stg.get("expected_terminal", [])
                    and res["code"] == "NAMED_REFUSAL"
                    and res["terminal"] == "KNOWN"
                    and not res["cleanup_errors"]
                    and not res.get("cleanup_overruns")
                    and (not res["obligation"] or res["disarm"] == "CONFIRMED_DISABLED")
                    and bool(res.get("refused"))
                    and all(t in " ".join(res.get("refused") or [])
                            for t in stg.get("expected_refusal_needles", [])))
                if (not res["success"] and expected_refusal_ok):
                    res = {**res, "success": True, "expected_refusal": res["status"],
                            "refused_lines": res.get("refused")}
                # 分支决定之后重新取本链的档案: 记录/取证规格里的 when 只按**自己这条链**的
                # 分支过滤, 不能被另一条链的分支串过去。
                ap = (stub_branches.get(sc, profile) if transport == "stub"
                      else scenario_profile.get(sc, profile))
                try:
                    record_from_rows(stg, res["results"], recorded, ap)
                except Stop as e:
                    # 会话本身已经带着主错(例如 QUOTA_EXHAUSTED)时**不覆盖**它: 记录/比较
                    # 的失败另记 record_code, 否则现场只会看到"比较缺步骤"而看不到真因。
                    res = {**res, "success": False,
                           "status": "STOP" if not res.get("code") else res["status"],
                           "code": res.get("code") or e.code,
                           "detail": res.get("detail") or e.detail,
                           "record_code": e.code, "record_detail": e.detail}
                if stg.get("require_frames"):
                    # 计数增大只证明"本轮多写了一笔"; 帧本身读不回来 = 取证未完成, 阶段不许成功。
                    fb = flash_frames_verdict(stg, res["results"], recorded, led)
                    if fb:
                        res = {**res, "success": False, "forensics": "INCOMPLETE",
                               "code": res.get("code") or "FLASH_FRAMES_NOT_RECOVERED",
                               "detail": res.get("detail") or json.dumps(fb, ensure_ascii=False)}
                        led.ev("FORENSICS_INCOMPLETE", stage=sid, bad=fb)
                fb = first_cause_verdict(stg, res["results"], recorded, ap, led)
                if fb:
                    # ④ 缺腿 = 取证未完成: 事件确实观察到了, 但"谁引起这次停机"还没咬合。
                    res = {**res, "success": False, "first_cause": "EVIDENCE_INCOMPLETE",
                           "code": res.get("code") or "EVIDENCE_INCOMPLETE",
                           "detail": res.get("detail") or json.dumps(fb, ensure_ascii=False)}
                if stg.get("target_channel"):
                    if fb:
                        tc, tw = "TARGET_UNPROVEN(EVIDENCE_INCOMPLETE)", "四腿缺一, 先不主张通道"
                    elif ap != "trip":
                        tc, tw = ("NO_TRIGGER", "本场观察窗到时未触发(不是目标通道已验证)")
                    else:
                        tc, tw = target_channel_claim(stg, recorded)
                    res = {**res, "target_claim": tc, "target_why": tw}
                    led.ev("TARGET_CHANNEL", stage=sid, scenario=sc, claim=tc, why=tw)
                    if tc.startswith("OTHER_SOURCE") or tc.startswith("TARGET_RULE_BUT"):
                        # 别的源触发了: 撤能与取证照做, 但本场不得计成"目标通道已验证"。
                        # 不把它写成失败(设备侧确实保护了), 也不写成通过 —— 只把声明降级。
                        led.ev("PROTECTION_CLAIM_DOWNGRADED", stage=sid, scenario=sc, claim=tc)
                done.append({"id": sid, "status": res["status"], "code": res["code"],
                             "detail": res.get("detail"),
                             "recorded": dict(recorded),
                             "success": res["success"], "disarm": res["disarm"],
                             "terminal": res["terminal"],
                             "scenario": sc, "branch": ap,
                             "expected_refusal": res.get("expected_refusal"),
                             "forensics": res.get("forensics"),
                             "first_cause": res.get("first_cause"),
                             "target_claim": res.get("target_claim"),
                             "target_why": res.get("target_why"),
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
           quota_used=used_box, prerequisite_skips=skipped_by_prerequisite,
           scenario_branches=scenario_branch)
    # 额度审计: 用了的必须都申报过, 申报的余额不许为负 —— 两者都是"整包共用一本账"
    # 的可核查形式, 比在计划里写一段说明文字强。
    undeclared = sorted(k for k in used_box if k not in plan["quota"])
    negative = sorted("%s=%d" % (k, v) for k, v in quota_box.items() if v < 0)
    audit_lines = ["QUOTA_AUDIT|%s|declared=%d|used=%d|left=%d" %
                   (k, plan["quota"][k], used_box.get(k, 0), quota_box.get(k))
                   for k in sorted(plan["quota"])]
    expected = expected_ids
    ok = (not notrun) and all(d.get("success", True) for d in done) \
        and [d["id"] for d in done] == expected \
        and not undeclared and not negative
    # "流程跑完"与"目标传感器通道验证成功"是两件事: 后者只在各场抓到**自己那一路**的规则
    # 且源样本非零时才成立。二者分开报, 免得 status=OK 被读成核心保护全部通过。
    claims = {}
    for d in done:
        if d.get("target_claim"):
            claims[d.get("scenario") or d["id"]] = d["target_claim"]
    skipped_sc = sorted({s.get("scenario") for s in plan["stages"]
                         if s["id"] in skipped_by_prerequisite and s.get("scenario")})
    for sc in skipped_sc:
        claims[sc] = "SKIPPED_BEFORE_SPIN_UP(PREREQUISITE)"
    core_verified = bool(claims) and all(v == "VERIFIED" for v in claims.values())
    led.ev("PROTECTION_CLAIMS", claims=claims, core_protection_verified=core_verified,
           flow_complete=bool(ok))
    led.close()
    return {"status": "OK" if ok else "STOP", "stages": done, "not_run": notrun,
            "quota_audit": {"undeclared_used": undeclared, "negative_left": negative,
                            "lines": audit_lines},
            "expected_stages": expected, "recorded": recorded,
            "quota_left": quota_box, "quota_used": used_box, "profile": profile,
            "branch": branch, "scenario_branches": dict(scenario_branch),
            "protection_claims": claims, "core_protection_verified": core_verified,
            "flow_complete": bool(ok),
            "prerequisite_skips": skipped_by_prerequisite,
            "prerequisite_refusals": [{"scenario": k, "refused_before": v["stage"],
                                       "gates": v["gates"],
                                       "skipped_stages": skipped_by_scope.get(k, []),
                                       "via_requires": v.get("via_requires") or []}
                                      for k, v in sorted(blocked_scenarios.items())],
            "actions_attempted": list(backend.attempts),
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
    mcode = mode_gate(a.transport, a.backend, a.profile if a.dispatch else None)
    if mcode:
        # 分流门在批准令牌/UID/工具/端口之前: 混合模式或真实预选档案 => 一条命令都不发。
        print("%s:transport=%s backend=%s profile=%s" %
              (mcode, a.transport, a.backend, a.profile), file=sys.stderr)
        return 4
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
        for r in res.get("prerequisite_refusals", []):
            print("PREREQUISITE_REFUSAL|scenario=%s|refused_before=%s|gates=%s|skipped_stages=%s|"
                  "via_requires=%s|ports_opened_by_chain=0" %
                  (r["scenario"], r["refused_before"], ",".join(r["gates"]),
                   ",".join(r.get("skipped_stages") or []),
                   ",".join(r.get("via_requires") or []) or "-"))
        for sc, claim in sorted((res.get("protection_claims") or {}).items()):
            print("PROTECTION_CLAIM|scenario=%s|claim=%s" % (sc, claim))
        print("FLOW_VS_VERIFICATION|flow_complete=%s|core_protection_verified=%s|"
              "含义: flow_complete 只说明申报的阶段都跑完且额度/取证/撤能账目一致; "
              "core_protection_verified 才说明每一场都是**目标传感器通道**自己触发的" %
              (res.get("flow_complete"), res.get("core_protection_verified")))
        print("DISPATCH_SUMMARY|status=%s|code=%s|stages_executed=%d|stages_not_run=%d|"
              "quota_left=%s|quota_used=%s|profile=%s|branch=%s|scenario_branches=%s|"
              "prerequisite_skips=%s|actions_attempted=%s|ledger=%s" %
              (res["status"], res.get("code", ""), len(res["stages"]), len(res["not_run"]),
               json.dumps(res["quota_left"], sort_keys=True),
               json.dumps(res["quota_used"], sort_keys=True), res["profile"],
               res.get("branch"), json.dumps(res.get("scenario_branches", {}), sort_keys=True),
               json.dumps(res.get("prerequisite_skips", [])),
               json.dumps(res.get("actions_attempted", [])), res["ledger"]))
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
