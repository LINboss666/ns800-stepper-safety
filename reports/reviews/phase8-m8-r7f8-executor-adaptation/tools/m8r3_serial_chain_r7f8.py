#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R7F8F 派生执行器(返修候选, 逐字节派生自 4c962170b2357eef 的 R7F8E 件; 旧两份均未动) 现场候选包的会话执行器(单一写者 / 原始字节先落盘 / 起转即硬截止 / 动作后端可注入)。

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
import tempfile
import time
from pathlib import Path

PLAN_APP_REL = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                "ns800rt7p65-nssinepad/applications")
BAUD = 115200
SLACK_SECONDS = 3
WRITE_SECONDS = 2
# F5b(A 的 stub 复现第二项): 4 s 排不下**五条**各自的 1.2 s 确认窗(3 条电机 + 2 条 IRQ 尾部
# = 6.0 s), 于是全部无响应时第五条 safety_irq_status 必被 IRQ_TAIL_HARD_DEADLINE_BEFORE_*
# 跳过。本轮任务书 §5 明写"会话 90 s 含 7 s 收口预留", 这里落实成申报值 7 s;
# exec_session 开口前用 CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS 拒掉任何 < 6.0 s 的申报。
CLEANUP_RESERVE_SECONDS = 7          # 起转硬截止之后留给 stop+disarm+复核+IRQ 尾部的时间
BOOT_CAP = 262144
RESPONSE_CAP = 32768
PROMPT_RE = re.compile(rb"(?:^|\r?\n|\r)msh\s*(?:>|/>)\s*\Z")
ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")
DANGER = re.compile(rb"stack is close|stack overflow|SYSTEM_HALT|HardFault|BusFault|UsageFault", re.I)
# 业务失败的具名标记: 产品里这几条线一律由 rt_kprintf 打出(见 needles.json 的 source 字段),
# 出现即说明这一步没有成立, 与"响应是否完整"无关。
BUSINESS_FAILURE = (b"FAILED", b"REFUSED", b"VOIDED", b"not supported")
# 只有"查询类"命令的响应里, 才允许出现"此刻若做某动作会被拒"这种**状态披露**; 动作命令本身被拒
# (motor_arm/motor_start/learn/apply/…) 一律照旧具名停止, 不得用白名单盖掉。
STATUS_QUERY_CMDS = ("system_status", "motor_status", "safety_irq_status", "tmc_config_show",
                     "diag_status", "config_show", "diag_profile", "free", "ps", "safety_status")
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
    板子只是把 [DIAG]/[MOT] 行流出来。命令响应的提示符门绝不因此放宽。

    R6A §5: 危险门在**剥之前**的完整原始字节上做 —— 提示符/回显的剥离不能顺手把损坏的
    一段剪掉(清理路径正是这么漏掉裸 NUL 的)。"""
    guard_stream_bytes(raw, where="split_rows", evidence="in-memory")
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


# 复审 M8-R6A §5: 危险门只有 read_until_prompt 一处, 清理与观察窗各走各的解码路径,
# 于是"裸 NUL 一律硬停"在 cleanup_motor_status 上没生效(裸 NUL 是合法 UTF-8)。
# 现在三类原始字节缺陷集中在**一个函数**里, 普通响应/清理/观察窗/发送前排空全部复用它。
STREAM_BYTE_DEFECTS = ("DANGER_TEXT_IN_STREAM", "NUL_BYTE_IN_RESPONSE",
                       "ILLEGAL_BYTES_IN_RESPONSE")
# 状态行豁免的白名单原本只看命令首词, 而 `diag_profile` 下既有只读查询也有写动作;
# 把首词放进 STATUS_QUERY_CMDS 会让 learn/zero/commit 的 REFUSED 线也能被洗成"安全态披露"。
# 因此这里按**完整命令**放行两条真只读查询, 不放宽首词面。
READONLY_QUERY_CMDS = ("diag_profile learn_status", "diag_profile show")


def guard_partial_bytes(raw, led=None, where="", evidence=""):
    """流**还没读完**时只能做与分块边界无关的两项: 危险文本、裸 NUL。
    非法 UTF-8 绝不在这里判 —— 一条中文行被 512 B 的读块从中间剪开是正常现象,
    在半截缓冲上判解码会把好响应误杀(完整字节流的解码门在 guard_stream_bytes 里)。"""
    m = DANGER.search(ANSI.sub(b"", raw))
    if m:
        if led:
            led.ev("DANGER_BYTES", where=where, evidence=str(evidence),
                   hit=m.group(0).decode("ascii", "replace"))
        raise Stop("DANGER_TEXT_IN_STREAM", m.group(0).decode("ascii", "replace"))
    nul = raw.count(b"\x00")
    if nul:
        if led:
            led.ev("NUL_IN_RESPONSE", where=where, bytes=nul, first_offset=raw.index(b"\x00"),
                   tail=repr(raw[-24:]), evidence=str(evidence),
                   meaning="控制台输出通道的字节损坏, 机制未证")
        raise Stop("NUL_BYTE_IN_RESPONSE",
                   "bytes=%d first_offset=%d where=%s raw=%s" %
                   (nul, raw.index(b"\x00"), where, evidence))
    return raw


def guard_stream_bytes(raw, led=None, where="", evidence=""):
    """顺序固定: ① 危险文本 ② 裸 NUL ③ 非法 UTF-8 —— 都在**未解码的原始字节**上判,
    命中即具名 Stop。原字节由调用方先行落盘, 这里不删一个字节, 也不 replace 后继续。
    长度帽与这道门的先后见 read_until_prompt 的循环注释(门先于帽)。"""
    guard_partial_bytes(raw, led, where=where, evidence=evidence)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as e:
        if led:
            led.ev("ILLEGAL_BYTES", where=where, evidence=str(evidence),
                   around=repr(raw[max(0, e.start - 8):e.start + 8]))
        raise Stop("ILLEGAL_BYTES_IN_RESPONSE", repr(raw[max(0, e.start - 8):e.start + 8]))
    return raw



def _is_real_serial_port(port):
    """回显门只对**真串口**生效: 假端口/内存端口不是被测对象, 也不建模 finsh 的 echo。"""
    try:
        import serial
    except ImportError:
        return False
    return isinstance(port, serial.Serial)


def has_echo(raw, cmd):
    """本条命令是否真的被板子回显过(提示符可有可无, 与 split_rows 剥回显的口径一致)。"""
    return bool(re.search(rb"(?:msh\s*(?:>|/>)\s*)?" + re.escape(cmd.encode("ascii")) +
                          rb"\r(?:\n)?", raw))


def drain_pending(port, led, clock, limit=64):
    """发命令**之前**把缓冲里的残留读干净。

    为什么要这一步: 上一条响应之后板上往往还会再打一个 `msh >`, 它留在 OS 接收缓冲里; 下一条命令一发送,
    读循环先撞到的就是这个**陈旧提示符**, 于是把 5 个字节当成整条响应返回(现场实测 `04_cfgapply.rx.bin`
    就是 5 B 且连回显都没有)。残留不丢: 原样交给调用方并入本步的证据文件, 并在台账具名披露。
    """
    buf = bytearray()
    for _ in range(limit):
        chunk = port.read(512)
        if not chunk:
            break
        buf += chunk
        clock.sleep(0.002)
    if buf:
        led.ev("PRE_CMD_DRAIN", bytes=len(buf), tail=repr(bytes(buf)[-24:]))
    return bytes(buf)


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
                # R6(复审 §5.2) + R6A §5: **原始字节**里的危险标记/裸 NUL 先于长度帽与任何
                # 解码/协议判定 —— 旧写法先撞 20 kB 的帽, 会把"板上正在报 HardFault/栈溢出"
                # 这件事实降级成"响应太长"。与普通响应/清理/观察窗共用同一个门。
                guard_partial_bytes(bytes(buf), led, where="read_until_prompt",
                                    evidence=raw_path)
                if len(buf) > RESPONSE_CAP:
                    raise Stop("RESPONSE_BYTE_CAP")
                if PROMPT_RE.search(ANSI.sub(b"", bytes(buf))):
                    break
            if clock.monotonic() > deadline:
                break
            clock.sleep(0.005)
    raw = bytes(buf)
    nul = raw.count(b"\x00")
    if nul:
        # 裸 0x00 本身是合法 UTF-8, 所以 decode 门抓不到它 —— 而现场实测两种损坏都见过:
        # 一种是截断的多字节序列(被 ILLEGAL_BYTES 拒), 另一种就是这种裸 NUL(以前静默通过)。
        led.ev("NUL_IN_RESPONSE", bytes=nul, first_offset=raw.index(b"\x00"),
               tail=repr(raw[-24:]), meaning="控制台输出通道的字节损坏, 机制未证")
    led.ev("RX_DONE", bytes=len(raw), prompt_seen=bool(PROMPT_RE.search(ANSI.sub(b"", raw))),
           raw_sha256=sha_bytes(raw), tail=repr(raw[-16:]), nul_bytes=nul)
    if nul:
        # R6 §5.1: 裸 NUL = 硬失败(复审探针实测到旧版只记事件不拒绝, 让含 NUL 的"干净重确认"通过)。
        # 原字节已经落盘, 不改一字; 本步响应一律不作数, 也不允许"由后面某步重确认"来放行。
        raise Stop("NUL_BYTE_IN_RESPONSE", "bytes=%d first_offset=%d raw=%s" %
                   (nul, raw.index(b"\x00"), raw_path))
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


# 产品这两行的出处: motor.c:2479-2480(cmd_motor_status 打 "arm_gate_mask=0x%X %s")与
# supervisor.c:126-127(system_status 打 "arm gate fail mask=0x%X (%s)")。两处都走同一个
# motor_get_gate_fail_mask()(motor.c:894), 且 tail 由 mask 是否为 0 唯一决定, 所以契约只钉
# mask; motor.c:937 的 mot_print_gate_fail 还会再打一条**没有 tail** 的同名行, 下面的锚定正则
# 把它排除在外(否则那条行会因为"同一行不唯一"把判据打死)。
MOTOR_MASK_ROW = r"^\[MOT\] arm_gate_mask=(?P<mask>0x[0-9A-F]+) (?P<tail>REFUSED|ALL-PASS)$"
SYS_MASK_ROW = (r"^\[Motor\] arm gate fail mask=(?P<mask>0x[0-9A-F]+) \((?P<tail>arm REFUSED"
                r"|all gate conditions pass \(incl\. ENN feedback\))\)$")
ARM_GATE_MASK_ROWS = {"motor_status": MOTOR_MASK_ROW, "system_status": SYS_MASK_ROW}
# 两行的 tail 用词不同(motor.c:2480 打 REFUSED/ALL-PASS, supervisor.c:127 打 arm REFUSED/
# all gate conditions pass...), 契约门按 cmd 取自己的那一对, 不拿一个词去判另一条行。
ARM_GATE_MASK_TAILS = {"motor_status": ("REFUSED", "ALL-PASS"),
                       "system_status": ("arm REFUSED",
                                         "all gate conditions pass (incl. ENN feedback)")}
# 计划侧声明这份契约的版本标记: 没标记的历史冻结包只登记 NA(不回改其字节, 也不给它新红,
# 与 observation_bounds 同一处置); 带标记的计划**逐条硬判**, 一条没钉就 FAIL。
ARM_GATE_MASK_CONTRACT_KEY = "arm_gate_mask_contract"
ARM_GATE_MASK_CONTRACT_VERSION = "R7F8F_PER_STEP_V1"


def arm_gate_refused_allow(cmd, mask):
    # 按 cmd + 预期 mask 构造**整行锚定**的披露豁免: 只豁免"该 mask 那一条 REFUSED 行"。
    # 预期 0x0 的步骤一条都不该有 —— 产品在那一行只会打 ALL-PASS, 打出 REFUSED 就是别的事。
    if cmd == "motor_status":
        return r"^\[MOT\] arm_gate_mask=%s REFUSED$" % mask
    if cmd == "system_status":
        return r"^\[Motor\] arm gate fail mask=%s \(arm REFUSED\)$" % mask
    raise Stop("ARM_GATE_MASK_CMD_UNSUPPORTED", cmd)


def status_lines_allow_patterns(st):
    """只允许"整行锚定"的状态披露行绕过业务失败扫描(见 assert_step)。"""
    pats = []
    for p in st.get("status_lines_allow", []):
        try:
            pats.append(re.compile(p))
        except re.error:
            raise Stop("STATUS_ALLOW_PATTERN_INVALID", "%s:%s" % (st.get("as", "?"), p))
    return pats


def status_allow_literal_prefix(pat):
    # 取模式串在第一个**真正通配**的结构之前的字面片段(去掉首尾锚点)。
    # 给 PLAN_STATUS_ALLOW 用: 这段是"凡能被该模式 fullmatch 的行都必须以此开头"的确定前缀,
    # 因此 expect_contains 里只要有它的子串, 运行期的正面要求门就一定成立。
    # 反斜杠后面跟**字符类/断言**(d s w 与其大写、b B A Z)或反向引用才是通配; 跟在 \[ \] \(
    # \) \. \* 以及**转义空格**后面的, 就是那个字符本身(冻结的 R7F7 计划整条模式都用反斜杠转义
    # 空格, 把它当通配会把前缀截成 "[DIAG]" 而假判红)。
    _CLS = "dDsSwWbBAZnrtfv0123456789"
    s = pat[1:] if pat.startswith("^") else pat
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _CLS:
                break                               # 字符类/断言/反向引用: 不是字面
            out.append(nxt)                         # 转义的字面字符(含转义空格)
            i += 2
            continue
        if c in "$.*+?|{[(":
            break
        out.append(c)
        i += 1
    return "".join(out)


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
    # 状态披露行(**整行锚定**, 且必须同时被本步 expect 正面要求)不参与"业务失败"扫描: 一条查询的
    # 响应里可以合法地报告"此刻 arm 会被拒"这种安全态。命令自己被拒仍然走 BUSINESS_FAILED/REFUSED。
    pats = status_lines_allow_patterns(st)
    allow_hit = [r.strip() for r in rows
                 if any(p.fullmatch(r.strip()) for p in pats)] if pats else []
    # F11 现场补正(设备动作已发生那一次): 上面那句注释从 R7F8E 起就写着"必须同时被本步 expect
    # 正面要求", 但**代码从来没核过** —— 于是写一条 status_lines_allow 实际等于"这步里凡是长得
    # 像拒绝的行都免检"。计划侧那道门(validate-plan)要求锚定 + 硬字段, 运行期却没有对应强制,
    # 两边口径不一致。这里把注释兑现成判据: 被放行的每一行都必须含本步 expect_contains 的某个
    # 字面片段, 否则当场具名停止(fail-closed), 不做"看着像就放过"。
    if allow_hit:
        want = [e for e in st.get("expect_contains", []) if e]
        loose = [l for l in allow_hit if not any(w in l for w in want)]
        if loose:
            led.ev("STATUS_ALLOW_NOT_POSITIVELY_REQUIRED",
                   **{"as": asname, "lines": loose, "expect_contains": want})
            raise Stop("STATUS_ALLOW_NOT_POSITIVELY_REQUIRED",
                       "%s 用 status_lines_allow 放行了未被本步 expect_contains 正面要求的行: %s"
                       % (asname, ";".join(loose)[:200]))
    rest = [r for r in rows if r.strip() not in allow_hit]
    # 业务层先判: 一条 FAILED/REFUSED/VOIDED 线是**业务结论**, 不能因为"这一步本来还想
    # 看见别的字串"就先把它的具名结果盖掉(内容缺失与业务失败是两个不同层的问题)。
    bad = business_failures(rest)
    allowed = st.get("allowed_outcomes", [])
    if bad or allow_hit:
        named = [b for b in bad for a in allowed if a in b]
        led.ev("BUSINESS_OUTCOME", **{"as": asname, "lines": bad, "allowed": bool(named),
                                      "status_lines_allowed": allow_hit})
        if named:
            return {"verdict": "REFUSED", "code": "NAMED_REFUSAL", "lines": named}
        if bad:
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
        # must_one_of: 观测值必须落在**本步申报的集合**里(边界步用)。不在集合里就具名停止,
        # 并把观测值与允许的集合一起写进台账 —— "看着像一个合法值"不算判过。
        not_in = {}
        for k, allowed in (spec.get("must_one_of") or {}).items():
            if k not in g:
                unknown.append(k)
                continue
            if not isinstance(allowed, (list, tuple)) or not allowed:
                not_in[k] = (g[k], "SET_NOT_DECLARED")
            elif str(g[k]) not in [str(x) for x in allowed]:
                not_in[k] = (g[k], [str(x) for x in allowed])
        if not_in:
            led.ev("VALUE_ASSERT_FAIL", **{"as": asname, "not_in_set": not_in})
            return {"verdict": "STOP", "code": "FIELD_VALUE_NOT_IN_SET", "not_in_set": not_in}
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
        # F4(复审 P8): 修前把 verdict=CUT_BY_HARDWARE 也当"在跑"收, 于是
        # expected_ENN=HIGH/ENN_fb=1(硬件把驱动切了)照样 PASS。运行必须**确实在使能侧**:
        # 产品只在请求使能(want=1)时才打 expected_ENN=LOW(motor.c:2491 的 want ? LOW : HIGH),
        # 而 safety_gpio.c:296 规定 CUT_BY_HARDWARE 只在 want_enabled=1 且 fb=1 时出现 —— 那正是
        # "要动没让动", 不是正常巡航。逐项清单见 running_contract()。
        summ, bad_r = running_contract(st, rows)
        if bad_r:
            led.ev("PROFILE_FAIL", **{"as": asname, "profile": "RUNNING", "bad": bad_r})
            return {"verdict": "STOP", "code": "PROFILE_NOT_RUNNING", "bad": bad_r,
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
        try:
            got = input("OPERATOR CONFIRM [%s] (yes/no): " % name).strip()
        except EOFError:
            # 没有终端可用(非交互调用) => 具名"无人应答", 绝不退化成 UNEXPECTED_EXCEPTION,
            # 也不得当成"操作者说不": 答案根本没被问到人。
            raise Stop("MANUAL_INPUT_UNANSWERED", "%s:no_tty" % name)
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
STEP_TOTAL_KEY = "step_total_seconds"
MOTION_END_KEY = "motion_end_evidence"
# §4/裁定一: 正常 motor_stop 的**发送尝试**必须排在起转后 8 s 内 —— 这是计划侧的静态界,
# 现场判读单独核实际 TX 时刻。常量放在这里让检查器与交接引用同一个数。
NORMAL_STOP_SEND_SECONDS = 8


def step_total_problem(st):
    """裁定二第 1 条: 申报的单步总窗必须是**有限正数**(非 bool、不 int 截断)。

    返回具名问题串, None=合法或没申报。轮询步骤还要求 `poll.until_seconds <= 本步总窗`
    (裁定二第 2 条): 否则"总窗"含不住它自己申报的轮询时间, 就是漏算。
    """
    if STEP_TOTAL_KEY not in st:
        return None
    v = st[STEP_TOTAL_KEY]
    if isinstance(v, bool):
        return "BOOL_VALUE"
    if not isinstance(v, (int, float)):
        return "NOT_A_NUMBER(%s)" % type(v).__name__
    f = float(v)
    if f != f or abs(f) == float("inf"):
        return "NOT_FINITE(%r)" % v
    if f <= 0:
        return "NON_POSITIVE(%r)" % v
    p = st.get("poll") or {}
    if p and float(p.get("until_seconds", 0) or 0) > f:
        return "POLL_UNTIL_EXCEEDS_STEP_TOTAL(until=%r>total=%r)" % (
            p.get("until_seconds"), v)
    return None


def step_window(st, slack=SLACK_SECONDS):
    """这一步的等待上限(秒)与其来源 —— 执行器、计划校验、预算镜像与仿真**都调这里**。

    * 申报了 `step_total_seconds`: 该值**已经包含本步全部等待与余量** => 原样取用(保留 0.5
      这样的小数), **不再加 SLACK**(裁定二第 1 条);
    * 没申报: 回到 legacy = 申报秒数 + SLACK_SECONDS —— 冻结的 R7F7 计划与旧计划一字不变
      (裁定二第 3 条), 也不许把全局 SLACK 改成 0 来"统一"。
    返回 (窗口秒数, 来源标签)。
    """
    v = st.get(STEP_TOTAL_KEY)
    if v is not None:
        return float(v), STEP_TOTAL_KEY
    return int(st.get("seconds", 3)) + slack, "legacy_seconds_plus_slack"


class MotionBudget:
    """motor_start 的发送尝试一旦建立, 后续任何动作只能用剩余预算; 清理另给保留段。"""

    def __init__(self, clock, cap_seconds, reserve=CLEANUP_RESERVE_SECONDS):
        self.clock = clock
        self.cap = float(cap_seconds)
        self.reserve = float(reserve)   # F5b: 由会话/阶段**申报**, 不再只靠模块常量
        self.t0 = None
        self.actual = None
        # F5b: 会话绝对截止(台账 t0 + 总时限)。cleanup_deadline() 用它把收口再夹一次,
        # 落实"7 s 预留仍受会话总截止约束"——预留不是把会话无上限拉长的手段。
        self.session_abs_dl = None
        # F5c: 业务侧绝对截止 = session_abs_dl - reserve(只有本会话可能建立收口义务时才设)。
        self.business_dl = None
        # G1(A 裁定一): 可信零速快照的确认时刻(**相对 t0 的运动耗时**)与证据摘要。
        # None => 运动 cap 仍约束后面的业务与清理; 置位后只解开**运动**那一项,
        # arm 通电 18 s 与收口预留 7 s / 会话 90 s 封顶**一律不变**。
        self.motion_end = None
        self.motion_end_evidence = None

    @property
    def armed(self):
        return self.t0 is not None

    def start_attempt(self):
        if self.t0 is None:
            self.t0 = self.clock.monotonic()

    @property
    def cap_active(self):
        """运动 cap 是否还在约束本会话 —— 裁定一: 可信零速确认后**不再**约束停机后的业务。"""
        return self.t0 is not None and self.motion_end is None

    def confirm_zero_speed(self, elapsed_from_t0, evidence):
        """裁定一: 唯一正常业务结束点。保留 t0(不许靠清空 t0 放行第二次 start), 记下结束时刻。"""
        if self.t0 is None:
            raise Stop("MOTION_END_WITHOUT_START", "运动计时还没起算就收到零速确认申报")
        if self.motion_end is not None:
            raise Stop("MOTION_END_ALREADY_CONFIRMED",
                       json.dumps({"already_at_s": round(self.motion_end, 3),
                                   "attempt_at_s": round(elapsed_from_t0, 3)}))
        if elapsed_from_t0 > self.cap:
            # "12 秒已到不能用一份迟到零速回包倒填为按时完成"。
            raise Stop("MOTION_ZERO_CONFIRMATION_LATE", json.dumps(
                {"elapsed_from_start_s": round(elapsed_from_t0, 3),
                 "motion_cap_seconds": self.cap}, ensure_ascii=False))
        self.motion_end = float(elapsed_from_t0)
        self.motion_end_evidence = evidence
        return self.motion_end

    def past_business_hard_deadline(self):
        """轮询/等待用: 现在是否已越过"仍生效的最早硬截止"。

        等价于修前那句 `business_deadline(0) <= now`(cap 项 + 业务项), 但零速确认后 cap 项
        自动退出 —— 停机后的 commit/show/ps/free 不该再被旧的 t_start+12 夹住。
        """
        now = self.clock.monotonic()
        dl = []
        if self.cap_active:
            dl.append(self.t0 + self.cap)
        if self.business_dl is not None:
            dl.append(self.business_dl)
        return bool(dl) and now >= min(dl)

    def business_deadline_window(self, window_seconds):
        """和 business_deadline() 同一组夹紧项, 但入参是**已解析好的单步总窗**(不再加 slack)。

        裁定二第 3 条的落点: 检查器用总窗、执行器仍加旧 3 秒 是不允许的, 所以两边都走
        step_window() 取值, 再由这个方法夹截止。legacy 计划传 want+SLACK 时与修前逐值相同。
        """
        base = self.clock.monotonic() + float(window_seconds)
        if self.cap_active:
            base = min(base, self.t0 + self.cap)
        if self.business_dl is not None:
            base = min(base, self.business_dl)
        return base

    def business_deadline(self, want_seconds, slack=SLACK_SECONDS):
        """业务动作的截止 = min(本步想等的+余量, 起转硬截止, **收口前的业务截止**)。

        F5c: 第三条以前不存在。收口预留必须从业务时间里**先扣掉**, 否则前置步骤(等待/轮询/
        只读查询)把会话跑到总截止, 收口剩 0 秒, 五条撤能/关 IRQ 全被 *_HARD_DEADLINE_BEFORE_*
        跳过 —— 那时"具名判红"只是把失败写进账, 设备仍留在带电+IRQ OPEN。
        """
        base = self.clock.monotonic() + want_seconds + slack
        if self.t0 is not None:
            base = min(base, self.t0 + self.cap)
        if self.business_dl is not None:
            base = min(base, self.business_dl)
        return base

    def cleanup_deadline(self):
        """收口绝对截止 = 起转硬截止 + **申报的**收口预留, 再被会话总截止夹一次。

        F5b 两项都在这里落地: 预留取申报值(本轮 7 s, 足够五条各 1.2 s 窗), 会话总截止仍然
        封顶(不因为申报了预留就把 90 s 放宽)。被夹短到排不下时不静默少发——cleanup() 入口
        先具名披露 CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE, 每条自己的窗到点也照旧具名。
        """
        # 裁定一: 运动确认后不能再把收口夹在旧的 t_start+12(+7) 里 —— 改用"从现在起 7 秒预留",
        # 但下面的 session_abs_dl 封顶一字不动(预留不是把会话拉长的手段)。
        if self.motion_end is not None:
            base = self.clock.monotonic() + self.reserve
        else:
            base = (self.t0 + self.cap + self.reserve) if self.t0 is not None \
                else self.clock.monotonic() + self.reserve
        if self.session_abs_dl is not None:
            return min(base, self.session_abs_dl)
        return base

    def remaining(self):
        if self.t0 is None:
            return None
        return self.cap - (self.clock.monotonic() - self.t0)

    def close(self):
        if self.t0 is not None and self.actual is None:
            self.actual = self.clock.monotonic() - self.t0
        return self.actual


class EnergizeBudget(object):
    """E4: 从 motor_arm 的**写入尝试之前**起算的通电保持截止, 到成功收到 motor_disarm 为止。

    与 MotionBudget 分列且互不替换: 那一条从 motor_start 起算, 只管"转多久"; 这一条管"线圈带电最多
    待多久", 所以 arm 后不起转、只查询、只等待同样在约束内, 正常 stop 而仍 armed 也不清除义务。
    到期时执行器不假装能替产品断电: 它在下一条业务命令写之前具名停止, 撤能仍走既有固定清理三条;
    成功 disarm 才把计时停住。主机软件截止不是物理实时停机保证(人工禁能仍是独立手段)。
    """

    def __init__(self, clock, cap_seconds):
        self.clock = clock
        self.cap = float(cap_seconds) if cap_seconds else None
        self.t0 = None
        self.actual = None
        # F3: 本场第几次 arm 写入尝试, 以及"已发过 disarm、等一份新的禁能快照来关闭义务"。
        self.arm_attempts = 0
        self.await_disable_snapshot = False

    @property
    def armed(self):
        return self.cap is not None and self.t0 is not None and self.actual is None

    def start_attempt(self):
        self.arm_attempts += 1
        if self.cap is not None and self.t0 is None:
            self.t0 = self.clock.monotonic()

    def deadline(self):
        return self.t0 + self.cap

    def remaining(self):
        if not self.armed:
            return None
        return self.cap - (self.clock.monotonic() - self.t0)

    def expired(self):
        return self.armed and self.clock.monotonic() >= self.deadline()

    def close(self):
        if self.t0 is not None and self.actual is None:
            self.actual = self.clock.monotonic() - self.t0
        return self.actual


def business_segments(steps, cap=None, reserve=None, total=None, slack=SLACK_SECONDS):
    """F6: 一次会话的业务时间按**互不重叠**的三段拆开, 并给出与执行器同一条 min() 的窗口上界。

    分段点不靠人眼猜, 用执行器自己的计时点:
      pre       —— motor_start 的写入尝试之前(MotionBudget.t0 还没置位, 不受运动 cap 夹);
      in_motion —— 从 motor_start 尝试到第一次 motor_stop/motor_disarm;
      post_stop —— 停机/禁能之后直到业务循环结束。
    cap 为什么只加一次: MotionBudget.t0 一旦在 motor_start **写之前**置位就**不清**(正常 stop
    也不清, 见 EnergizeBudget 上方那段注释), 于是 business_deadline() 把 in_motion 与 post_stop
    的每一步都夹在 t0+cap 之内 —— 那两段吃的是**同一段**时间, 再各自加一遍 cap 就是重复计时。
    单步窗口用执行器同一表达式 `poll.until_seconds or seconds`; `*_bound` 每步再加一次 SLACK
    (执行器 step_dl = now + want + SLACK_SECONDS), 所以"包含余量的总窗"是逐步计的, 既没漏算
    余量, 也没把 38 步的容差折成整场一次。`sim` 用执行器那条 min() 逐步推进, 给出"会话夹/
    cap 夹第一次让某一步在写之前就没时间"的那一步 —— 这是最坏情况下的**业务尾巴能跑到哪**。
    """
    SPIN_UP = ("motor_start",)
    STOP_DOWN = ("motor_stop", "motor_disarm")

    def _c(s):
        return str(s.get("cmd", "")).split(" ")[0]

    def _w(s):
        p = s.get("poll") or {}
        return float(p.get("until_seconds") or s.get("seconds") or 0)

    have = [(i, s) for i, s in enumerate(steps) if isinstance(s, dict) and s.get("cmd")]
    i_spin = next((i for i, s in have if _c(s) in SPIN_UP), None)
    i_stop = next((i for i, s in have if i_spin is not None and i >= i_spin and
                   _c(s) in STOP_DOWN), None)
    seg = {"pre": [], "in_motion": [], "post_stop": []}
    for i, s in have:
        if i_spin is None or i < i_spin:
            seg["pre"].append(s)
        elif i_stop is None or i < i_stop:
            seg["in_motion"].append(s)
        else:
            seg["post_stop"].append(s)
    out = {"spin_up_index": i_spin, "stop_index": i_stop}
    for k in ("pre", "in_motion", "post_stop"):
        out[k + "_steps"] = len(seg[k])
        out[k + "_declared"] = round(sum(_w(s) for s in seg[k]), 3)
        out[k + "_bound"] = round(sum(_w(s) + float(slack) for s in seg[k]), 3)
    # 没有 motor_start 的会话(纯只读/只开 IRQ)不进运动 cap: window 段就是 0, 全部内容
    # 都在 pre 里按申报窗口计。cap 缺省同样如此。
    out["window_seconds"] = float(cap) if (i_spin is not None and cap is not None) else 0.0
    out["worst_declared"] = round(out["pre_declared"] + out["window_seconds"], 3)
    out["worst_bound"] = round(out["pre_bound"] + max(out["window_seconds"],
                                                     out["in_motion_bound"] +
                                                     out["post_stop_bound"]), 3)
    out["after_spin_declared"] = round(out["in_motion_declared"] + out["post_stop_declared"], 3)
    out["after_spin_bound"] = round(out["in_motion_bound"] + out["post_stop_bound"], 3)
    out["business_budget_seconds"] = (round(float(total) - float(reserve), 3)
                                      if total is not None and reserve is not None else None)
    # 逐步仿真: 与 exec_session 里 step_dl 那条 min() 同一顺序、同一常数。
    t = 0.0
    t_spin = None
    first_refused = None
    for i, s in have:
        dl = t + _w(s) + float(slack)
        if t_spin is not None and cap is not None:
            dl = min(dl, t_spin + float(cap))
        if out["business_budget_seconds"] is not None:
            dl = min(dl, out["business_budget_seconds"])
        if first_refused is None and dl <= t:
            first_refused = {"index": i, "as": s.get("as") or ("step%02d" % (i + 1)),
                             "cmd": str(s.get("cmd", ""))[:40], "t_s": round(t, 3),
                             "want_seconds": _w(s), "slack_seconds": float(slack),
                             "cap_or_budget_ceiling_s": round(dl, 3)}
        if i == i_spin:
            t_spin = t
        t = dl
    out["sim_end_seconds"] = round(t, 3)
    out["first_step_refused_before_write"] = first_refused
    return out




def business_segments_ruling(steps, cap=None, reserve=None, total=None,
                             slack=SLACK_SECONDS):
    """A 预算裁定(2026-11-05)之后的静态三段: 起转前 / 活动(起转→可信零速) / 零速后。

    与 business_segments() 的三点区别, 一一对应裁定的两句:
      * 运动计时的**结束点**是申报 `motion_end_evidence` 的那一步(一份可信零速快照), 不是第一条
        stop/disarm —— stop 的回话本身不结束运动计时(裁定一);
      * 每步窗口取 step_window() 的**解析值**: 申报了 step_total_seconds 就是含全部等待与余量的
        总窗, **不再叠 SLACK**(裁定二), 因此 declared==bound; 未申报的步仍按 seconds+SLACK;
      * 旧语义算出来的 91.0/23.0 原样留在 `legacy` 字段里并写明它对应"每步再加 SLACK / stop 不
        解除运动计时", 转绿不等于抹掉(A 的 §4 明令)。
    时间轴与 A 的 `budget_check.py` 同一定义: 步的 start_s 是前面所有步窗口之和, end_s 再加上
    本步窗口; stop/零速/禁能的界都按这个累计轴取。
    """
    rows = [s for s in steps if isinstance(s, dict) and s.get("cmd")]
    cmd0 = [str(s.get("cmd", "")).split(" ")[0] for s in rows]
    asname = [s.get("as") for s in rows]

    def idx_of(name):
        return asname.index(name) if name in asname else None

    spin = cmd0.index("motor_start") if "motor_start" in cmd0 else None
    zero = next((i for i, s in enumerate(rows) if s.get(MOTION_END_KEY)), None)
    stop = next((i for i in range(spin if spin is not None else 0, len(rows))
                 if cmd0[i] in ("motor_stop", "motor_disarm")), None)
    arm = cmd0.index("motor_arm") if "motor_arm" in cmd0 else None
    disabled = idx_of("mot_disabled")

    win = [step_window(s, slack)[0] for s in rows]
    starts, acc = [], 0.0
    for w in win:
        starts.append(acc)
        acc += w
    ends = [starts[i] + win[i] for i in range(len(rows))]

    def seg(lo, hi):
        """[lo, hi] 闭区间的 (步数, 申报总窗); 区间无效返回 (0, 0.0)。"""
        if lo is None or hi is None or hi < lo:
            return 0, 0.0
        return hi - lo + 1, round(sum(win[lo:hi + 1]), 3)

    pre_n, pre_w = seg(0, (spin - 1) if spin is not None else None)
    act_n, act_w = seg(spin, zero)
    post_n, post_w = seg((zero + 1) if zero is not None else None, len(rows) - 1)
    if spin is not None and zero is None:
        # 没有可信零速结束的申报 => 运动计时**不解除**, 起转之后所有内容都还在 t0+cap 里
        # (这正是裁定一之前那条红的成因; 这里如实把它算回 cap, 不留绿灯)。
        act_n, act_w = len(rows) - spin, round(sum(win[spin:]), 3)
        post_n, post_w = 0, 0.0
    business_total = round(sum(win), 3)
    business_budget = (round(float(total) - float(reserve), 3)
                       if total is not None and reserve is not None else None)
    # 保守式(裁定 §3 最后一行): 用**整段 12 s cap** 替掉实际活动窗, 再加上停机后内容与预留。
    conservative_total = (round(pre_w + float(cap) + post_w + float(reserve), 3)
                          if cap is not None and reserve is not None else None)
    conservative_arm = (round(starts[spin] - starts[arm] + float(cap)
                              + (ends[disabled] - ends[zero]), 3)
                        if (cap is not None and arm is not None and spin is not None
                            and zero is not None and disabled is not None) else None)
    # legacy: 修前(F6 那一版)的两个数, **只按旧的申报字段**算 —— 加进 step_total_seconds
    # 之后这些步的 seconds/until 原字段不动(裁定二明令), 所以旧口径仍可原样复算。
    def legacy_w(s):
        return float((s.get("poll") or {}).get("until_seconds") or s.get("seconds") or 0)

    legacy_pre_bound = (round(sum(legacy_w(s) + slack for s in rows[:spin]), 3)
                        if spin is not None else None)
    legacy_after_spin = (round(sum(legacy_w(s) for s in rows[spin:]), 3)
                         if spin is not None else None)
    return {
        "semantics": "A_BUDGET_RULING_20261105(motion ends at trusted zero snapshot; "
                     "per-step total window without extra SLACK)",
        "spin_up_index": spin, "stop_request_index": stop, "zero_confirm_index": zero,
        "arm_index": arm, "disabled_confirm_index": disabled,
        "pre_start_steps": pre_n, "motion_active_steps": act_n, "post_zero_steps": post_n,
        "pre_start_seconds": pre_w, "motion_active_seconds": act_w, "post_zero_seconds": post_w,
        "window_seconds": cap, "reserve_seconds": reserve, "session_total_seconds": total,
        "stop_send_after_start_s": (round(starts[stop] - starts[spin], 3)
                                    if stop is not None and spin is not None else None),
        "zero_confirm_after_start_s": (round(ends[zero] - starts[spin], 3)
                                       if zero is not None and spin is not None else None),
        "arm_to_disabled_s": (round(ends[disabled] - starts[arm], 3)
                              if disabled is not None and arm is not None else None),
        "business_total_seconds": business_total,
        "business_budget_seconds": business_budget,
        "worst_declared": (round(pre_w + float(cap), 3) if cap is not None else None),
        "conservative_total_with_cap_s": conservative_total,
        "conservative_arm_with_cap_s": conservative_arm,
        "steps_total": len(rows),
        "legacy_semantics_note": "对应旧口径: 每步再加 SLACK_SECONDS、且正常 stop 不解除运动计时",
        "legacy_pre_bound_seconds": legacy_pre_bound,
        "legacy_after_spin_declared_seconds": legacy_after_spin,
        "legacy_slack_seconds": slack,
    }


class ApprovalLedger:
    """绑定批准身份的**跨进程**持久预占账(复审 M8-R5C §7.4 要求的那件缺失控制)。

    为什么会存在: R5-C 那 26 场里每场 dispatch 各自持一本内存账, 换输出目录或重开进程就把
    一次批准的边界清零, 于是 tmc_config_apply 实际用到 14(批准 2)。"结束时再汇总"无法阻止
    下一次发送, 所以把扣减搬到一个由**批准身份**命名的文件上:
      * 身份 = 批准令牌 + 计划 sha256 + 执行器 sha256 + 镜像 sha256; 任一变化 => 拒绝沿用同一份账,
        也**不**自动生成新预算(REQUIRES_NEW_APPROVAL 由调用方另给)。
      * 发送前先落盘预占再动作: tmp + os.replace 原子换入, 同目录 O_CREAT|O_EXCL 互斥锁;
      * 崩溃后未确认的预占按**已消耗**处理, 不退款(没有 release 这条路);
      * 账本缺失 / JSON 损坏 / 身份不符 => 拒绝进入真实动作, 绝不自建一份满额账;
      * cleanup_* 走独立预留: 业务额度耗尽不得阻挡已经成立义务的撤能(单独记 kind 与前缀)。

    M8-R6A R1(复审 §3)把"创建"和"读取"彻底分开, 因为上一版只在 open_approval 里查了一次
    exists, 之后每次 reserve/commit/snapshot 还会走 `_load_or_create` 的自动建账分支:
      * **运行期只严格读取已存在的账**(每次加锁读盘都守), 创建只允许 approval_init;
      * 缺字段 / 空值 / 错类型一律具名拒绝 —— 不许用 `if want and have` 这种真值比较跳过缺失
        的 SHA, 不许 `setdefault("spent", {})` 把"结构丢失"当成"从未消耗";
      * int 只接受真 int(bool/负数/小数/字符串数字都拒), caps 必须与批准记录**精确相符**;
      * 账本内部一致性(spent<=caps、pending 与 spent 的对应)校验不过就拒绝, **不修好再执行**;
      * 存盘失败如实传播: commit 不在 `_store` 失败时声称已落成。
    """

    VERSION = 2                       # R1 严格格式; v1 账本由 APPROVAL_LEDGER_VERSION 具名拒绝
    LOCK_TRIES = 200

    def __init__(self, path, approval_id, identities, caps, led, clock):
        self.path = Path(path)
        self.approval_id = str(approval_id)
        self.identities = dict(identities or {})
        self.caps = dict(caps or {})
        self.led = led
        self.clock = clock
        self.state = None

    # ---------------- 内部: 严格读盘 / 写盘 / 互斥 ----------------
    def _load(self):
        """**只读已存在的账**。R6A §3 的反例: 上一版这里是 `_load_or_create`, 文件不见了就返回
        一份 `spent={}` 的满额结构 —— 于是"绑定后删账本"等于把一次用尽的批准复活。现在:
        文件缺失 / 读不动 / 不是 JSON 对象 / 缺必需字段 / 类型不对 / 身份或 cap 不符 / 内部不一致
        全部具名拒绝, 且不补零、不迁移、不修好再继续。"""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raise Stop("APPROVAL_LEDGER_ABSENT",
                       "%s 不存在 => 任何一次预占/确认/快照都不进入(运行期不建账; 创建只走 approval_init)"
                       % self.path)
        except OSError as e:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "%s:%s" % (self.path.name, e.__class__.__name__))
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "%s:%s" % (self.path.name, e.__class__.__name__))
        validate_ledger_doc(doc, self.approval_id, self.identities, self.caps)
        return doc

    def _store(self, doc):
        validate_ledger_doc(doc, self.approval_id, self.identities, self.caps)
        doc["writes"] = doc["writes"] + 1
        tmp = self.path.with_name(self.path.name + ".tmp%d" % os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(self.path))    # 原子换入: 崩溃只会留下旧账或新账, 不会半截
            return True
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    def _lock(self):
        return ledger_lock(self.path, self.clock, self.approval_id)

    @staticmethod
    def _unlock(lk):
        try:
            os.remove(lk)
        except OSError:
            pass

    # ---------------- 对外: 预占 / 确认 ----------------
    def reserve(self, name, n=1, kind="business"):
        """先落盘再动作。返回预占凭据; 额度不足或身份不符 => 具名停止, 调用方不得发送。"""
        if not isinstance(name, str) or not name:
            raise Stop("APPROVAL_RESERVE_NAME_INVALID", repr(name)[:40])
        if not is_count(n) or n < 1:
            # R6A §3: "int 能转就算合法"会让 0/-1/True/"1"/1.5 都通过旧检查, 于是额度可以被
            # 一个非正整数的 n 悄悄清零。n 只能是**正整数**, 并且要落得下。
            raise Stop("APPROVAL_RESERVE_N_INVALID", "n=%r(%s)" % (n, type(n).__name__))
        if not isinstance(kind, str) or not kind:
            raise Stop("APPROVAL_RESERVE_KIND_INVALID", repr(kind)[:40])
        lk = self._lock()
        try:
            doc = self._load()
            if name not in doc["caps"]:
                raise Stop("QUOTA_UNDECLINED", name)
            cap, used = doc["caps"][name], doc["spent"][name]
            if used + n > cap:
                self.led.ev("APPROVAL_REFUSED", approval_id=self.approval_id, name=name,
                            used=used, want=n, cap=cap, reserve_kind=kind, ledger=str(self.path))
                raise Stop("APPROVAL_QUOTA_EXHAUSTED",
                           "%s used=%d want=%d cap=%d kind=%s" % (name, used, n, cap, kind))
            tag = "%s#%d" % (name, used + n)
            doc["spent"][name] = used + n
            doc["pending"].append({"id": tag, "name": name, "n": n, "kind": kind})
            if not self._store(doc):
                raise Stop("APPROVAL_LEDGER_WRITE_FAILED",
                           "%s 预占没落成 => 那条命令不发(不退款纪律的另一半)" % name)
            self.state = doc
            self.led.ev("APPROVAL_RESERVED", approval_id=self.approval_id, name=name, n=n,
                        used=used + n, cap=cap, reserve_kind=kind, ledger=str(self.path))
            return {"name": name, "tag": tag, "kind": kind}
        finally:
            self._unlock(lk)

    def commit(self, res, ok=True):
        """只把预占从 pending 里摘掉并留痕; **不退回额度** —— 崩溃/未确认一律按已消耗。

        R6A §3 最后一条: 存盘失败必须如实传播。旧版忽略 `_store` 的返回值, 于是"确认没落成"
        和"确认落成"在事件里长得一样, 而 pending 里还留着那条 —— 账面上看是已消耗(方向安全),
        但回执会说谎。"""
        lk = self._lock()
        try:
            doc = self._load()
            before = len(doc["pending"])
            doc["pending"] = [p for p in doc["pending"] if p["id"] != res["tag"]]
            removed = before - len(doc["pending"])
            if not self._store(doc):
                self.led.ev("APPROVAL_COMMIT_FAILED", tag=res["tag"], reserve_kind=res["kind"],
                            code="APPROVAL_LEDGER_WRITE_FAILED", ledger=str(self.path))
                raise Stop("APPROVAL_LEDGER_WRITE_FAILED",
                           "%s 的确认没落成: 预占仍挂在 pending, 按已消耗计(不退款)" % res["tag"])
            self.state = doc
            self.led.ev("APPROVAL_COMMITTED", tag=res["tag"], reserve_kind=res["kind"],
                        outcome="consumed" if ok else "consumed-with-error",
                        pending_removed=removed,
                        missing_tag=(0 if removed else 1))
        finally:
            self._unlock(lk)

    def snapshot(self):
        lk = self._lock()
        try:
            self.state = self._load()
            return {"spent": dict(self.state["spent"]), "caps": dict(self.state["caps"]),
                    "pending": list(self.state["pending"]), "writes": self.state["writes"],
                    "approval_id": self.approval_id, "identities": dict(self.state["identities"])}
        finally:
            self._unlock(lk)


# --------------------------------------------------------------------------- R1 严格校验
APPROVAL_IDENTITY_KEYS = ("plan_sha256", "executor_sha256", "image_sha256")
LEDGER_REQUIRED_FIELDS = ("version", "approval_id", "identities", "caps", "spent", "pending",
                          "writes", "created_by_action")
LEDGER_PENDING_FIELDS = ("id", "name", "n", "kind")


def is_count(v):
    """计数只接受**真正的 int**: bool 是 int 的子类, 所以 `True` 必须单独拒;
    小数、字符串数字("1")、None 同样不算(reviewer §3: 不接受"int 能转就算合法")。"""
    return isinstance(v, int) and not isinstance(v, bool)


def is_hex_sha(v):
    return isinstance(v, str) and bool(re.fullmatch(r"[0-9a-f]{64}", v))


def validate_caps(caps):
    """批准记录里的 caps: 非空键名 + 非负 int, 且必须是 object。运行期与 init 共用同一口径。"""
    if not isinstance(caps, dict):
        raise Stop("APPROVAL_CAPS_INVALID", "caps=%s" % type(caps).__name__)
    bad = sorted("%s=%r(%s)" % (k, v, type(v).__name__) for k, v in caps.items()
                 if not isinstance(k, str) or not k or not is_count(v) or v < 0)
    if bad:
        raise Stop("APPROVAL_CAPS_INVALID", ";".join(bad))
    return caps


def validate_ledger_doc(doc, approval_id, identities, caps):
    """账本的严格读盘校验(复审 M8-R6A §3)。每一项都是**拒绝**, 不是修复:

      | 反例(§3 表)              | 旧版            | 本函数                |
      | 删 identities.<SHA>      | snapshot 正常   | APPROVAL_LEDGER_CORRUPT |
      | 删整个 spent(合法 JSON)  | 再预占成功      | APPROVAL_LEDGER_CORRUPT |
      | spent.apply = -1         | 再预占变 0      | APPROVAL_LEDGER_CORRUPT |
      | 绑定后账本文件消失        | 自建满额账      | APPROVAL_LEDGER_ABSENT  |
    """
    if not isinstance(doc, dict):
        raise Stop("APPROVAL_LEDGER_CORRUPT", "top_level=%s" % type(doc).__name__)
    missing = [k for k in LEDGER_REQUIRED_FIELDS if k not in doc]
    if missing:
        raise Stop("APPROVAL_LEDGER_CORRUPT", "missing_fields=%s" % ",".join(missing))
    if doc["version"] != ApprovalLedger.VERSION:
        # 历史不兼容账具名拒绝; 不自动迁移成"未消费"状态(那等于把花掉的批准重置)。
        raise Stop("APPROVAL_LEDGER_VERSION",
                   "version=%r 本执行器要求 %d => 不自动迁移, 请按新身份另批"
                   % (doc["version"], ApprovalLedger.VERSION))
    if str(doc["approval_id"]) != str(approval_id):
        raise Stop("APPROVAL_IDENTITY_MISMATCH",
                   "账本记的是另一次批准 %s, 本次=%s" % (doc["approval_id"], approval_id))
    ids = doc["identities"]
    if not isinstance(ids, dict):
        raise Stop("APPROVAL_LEDGER_CORRUPT", "identities=%s" % type(ids).__name__)
    malformed = [k for k in APPROVAL_IDENTITY_KEYS if not is_hex_sha(ids.get(k))]
    if malformed:
        raise Stop("APPROVAL_LEDGER_CORRUPT",
                   "identity_missing_or_malformed=%s(三件 SHA 必须齐全且是 64 位小写十六进制)"
                   % ",".join(malformed))
    unknown_ids = sorted(set(ids) - set(APPROVAL_IDENTITY_KEYS))
    if unknown_ids:
        raise Stop("APPROVAL_LEDGER_CORRUPT", "identity_unknown_keys=%s" % ",".join(unknown_ids))
    for k in APPROVAL_IDENTITY_KEYS:
        want = (identities or {}).get(k)
        if not is_hex_sha(want):
            # 旧版是 `if want and have and want != have`: 调用方没算出这一件时**直接跳过**,
            # 于是"删掉 executor_sha256 / 不给 plan_sha256"都能带着同一本账继续走。
            raise Stop("APPROVAL_IDENTITY_MISMATCH",
                       "%s 本次没给出可用身份(%r) => 不许沿用该批准" % (k, want))
        if want != ids[k]:
            raise Stop("APPROVAL_IDENTITY_MISMATCH",
                       "%s 变了(%s… vs %s…) => 不许沿用同一批准, 也不自动生成新预算"
                       % (k, ids[k][:10], want[:10]))
    have_caps = doc["caps"]
    if not isinstance(have_caps, dict):
        raise Stop("APPROVAL_LEDGER_CORRUPT", "caps=%s" % type(have_caps).__name__)
    for key in sorted(have_caps):
        if not isinstance(key, str) or not key or not is_count(have_caps[key]) or \
                have_caps[key] < 0:
            raise Stop("APPROVAL_LEDGER_CORRUPT",
                       "cap_not_count[caps.%s]=%r" % (key, have_caps[key]))
    if set(have_caps) != set(caps or {}):
        raise Stop("APPROVAL_IDENTITY_MISMATCH",
                   "caps 键集合变了: 账本=%s 本次=%s" %
                   (",".join(sorted(have_caps)), ",".join(sorted(caps or {}))))
    for key in sorted(have_caps):
        if have_caps[key] != caps[key]:
            raise Stop("APPROVAL_IDENTITY_MISMATCH",
                       "caps[%s] 账本=%d 本次=%s => 计划申报更宽不算既成事实"
                       % (key, have_caps[key], caps[key]))
    spent = doc["spent"]
    if not isinstance(spent, dict):
        raise Stop("APPROVAL_LEDGER_CORRUPT", "spent=%s" % type(spent).__name__)
    if set(spent) != set(have_caps):
        # "缺动作键悄悄当 0" 与 "整个 spent 字段丢失" 都落在这里: 结构丢了就是损坏, 不是没花过。
        raise Stop("APPROVAL_LEDGER_CORRUPT",
                   "spent_keys!=caps_keys missing=%s extra=%s" %
                   (",".join(sorted(set(have_caps) - set(spent))) or "-",
                    ",".join(sorted(set(spent) - set(have_caps))) or "-"))
    for key in sorted(spent):
        if not is_count(spent[key]) or spent[key] < 0:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "spent_not_count[spent.%s]=%r"
                       % (key, spent[key]))
        if spent[key] > have_caps[key]:
            raise Stop("APPROVAL_LEDGER_CORRUPT",
                       "spent_exceeds_cap %s=%d>%d => 账本自己不对劲, 拒绝而不是改写"
                       % (key, spent[key], have_caps[key]))
    pending = doc["pending"]
    if not isinstance(pending, list):
        raise Stop("APPROVAL_LEDGER_CORRUPT", "pending=%s" % type(pending).__name__)
    seen = []
    for i, p in enumerate(pending):
        if not isinstance(p, dict):
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]=%s" % (i, type(p).__name__))
        pmiss = [f for f in LEDGER_PENDING_FIELDS if f not in p]
        if pmiss:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_missing=%s"
                       % (i, ",".join(pmiss)))
        if not isinstance(p["name"], str) or not p["name"]:
            # R6B §4: 类型敏感操作(成员查找 / 下标)之前先验类型。上一版直接
            # `p["name"] not in have_caps`, 合法 JSON 里的 `name: []` 就抛
            # TypeError: unhashable type: 'list' —— 普通异常会跳出清理框架,
            # 必要撤能一条都不发, 端口也不关。结构损坏必须是**具名账本拒绝**。
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_name_form=%r" % (i, p["name"]))
        if p["name"] not in have_caps:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_name_unknown=%s" % (i, p["name"]))
        if not is_count(p["n"]) or p["n"] < 1:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_n_invalid=%r" % (i, p["n"]))
        if not isinstance(p["kind"], str) or not p["kind"]:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_kind_invalid=%r" % (i, p["kind"]))
        if not isinstance(p["id"], str) or not re.fullmatch(r"[\w.-]+#\d+", p["id"]):
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_id_form=%r" % (i, p["id"]))
        if p["id"] in seen:
            raise Stop("APPROVAL_LEDGER_CORRUPT", "pending[%d]_id_duplicated=%s" % (i, p["id"]))
        seen.append(p["id"])
        seq = int(p["id"].rsplit("#", 1)[1])
        if not 1 <= seq <= spent[p["name"]]:
            raise Stop("APPROVAL_LEDGER_CORRUPT",
                       "pending[%d]_id_beyond_spent %s vs %s=%d" % (i, p["id"], p["name"],
                                                                     spent[p["name"]]))
    if not is_count(doc["writes"]) or doc["writes"] < 0:
        raise Stop("APPROVAL_LEDGER_CORRUPT", "writes_invalid=%r" % (doc["writes"],))
    return doc


def ledger_lock(path, clock, approval_id=""):
    """同目录 O_CREAT|O_EXCL 互斥锁: 预占/确认/快照/init 都走这一把(创建与消费互斥是 R1 的
    "双 init 竞争"要的东西 —— 两处用不同锁文件就等于没有锁)。"""
    lk = Path(path).with_name(Path(path).name + ".lock")
    for _ in range(ApprovalLedger.LOCK_TRIES):
        try:
            fd = os.open(str(lk), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            clock.sleep(0.002)
            continue
        try:
            os.write(fd, ("%d %s" % (os.getpid(), approval_id)).encode("utf-8", "replace"))
        finally:
            os.close(fd)
        return lk
    raise Stop("APPROVAL_LEDGER_LOCKED", str(lk))


def ledger_unlock(lk):
    try:
        os.remove(lk)
    except OSError:
        pass



def default_approval_root():
    """默认账本目录在**仓库与 --out 之外**: 输出目录每轮都换, 用它当账本父目录就等于每轮换一本账。
    真实现场应当用 --approval-root 指一块可持久化的位置; %TEMP% 被清掉后真实 dispatch 会因
    APPROVAL_LEDGER_ABSENT 拒绝进入 —— 方向是"拒绝动作", 不是"复活额度"。"""
    return Path(tempfile.gettempdir()) / "m8r6_approvals"


def approval_path(root, approval_id):
    "账本由**批准身份**命名, 与输出目录无关(复审 §7.4: 批准身份独立于输出目录)。"
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(approval_id))[:80] or "unnamed"
    return Path(root) / ("approval_%s.json" % safe)


def read_plan_snapshot(path):
    """R6B §5: **一次**读盘, 同一份原始字节同时给出 plan 的 SHA、解析结果与执行对象。

    旧写法是 `json.loads(read_text())` 先拿对象, 之后 `current_identities()` 再**另读一次**
    文件算 SHA —— 两次读之间文件被换掉时, 核对的是后来的字节、跑的却是先前的对象
    (复审的两条负例: 内存 plan 被改 / CLI 读取与哈希之间的替换交错, 未批准的 download argv
    都能到达真实后端)。快照只有一份 => 后面的 stages/tools/argv/额度/时间窗只能从它推导。
    """
    p = Path(path)
    raw = p.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as e:
        raise Stop("PLAN_SNAPSHOT_UNDECODABLE", "%s|%r" % (str(p), e))
    except ValueError as e:
        raise Stop("PLAN_SNAPSHOT_UNPARSEABLE", "%s|%r" % (str(p), e))
    if not isinstance(doc, dict):
        raise Stop("PLAN_SNAPSHOT_UNPARSEABLE", "%s|top_level=%s" % (str(p), type(doc).__name__))
    return {"path": str(p), "bytes": len(raw), "sha256": sha_bytes(raw), "doc": doc}


def current_identities(plan, plan_path, repo_root, snapshot=None):
    """身份**当场从真实文件算**, 不照抄 JSON 里登记的哈希(那正是复审 §6 指出的"登记≠实际")。

    R6B §5: 给了快照时 plan_sha256 就用**快照那份字节**的散列, 不再第二次读盘 —— 并且先确认
    要执行的这个对象就是快照解析出来的那一份; 不是就具名拒绝, 不替别的对象作保。
    """
    ids = {"executor_sha256": sha_of(__file__)}
    if snapshot is not None:
        if snapshot.get("doc") is not plan and snapshot.get("doc") != plan:
            raise Stop("PLAN_SNAPSHOT_NOT_EXECUTED_OBJECT",
                       "传入的 plan 对象与快照(%s)解析出的不是同一份 => 不用快照哈希给它作证"
                       % snapshot.get("sha256", "-")[:12])
        ids["plan_sha256"] = snapshot["sha256"]
    else:
        pp = Path(plan_path) if plan_path else None
        if pp and pp.is_file():
            ids["plan_sha256"] = sha_of(pp)
    img = (plan.get("identity_binding") or {}).get("image") or {}
    if img.get("path"):
        p = Path(repo_root) / img["path"]
        if p.is_file():
            ids["image_sha256"] = sha_of(p)
    return ids


def token_bytes_from_file(path):
    # 在 Python 内按**严格 UTF-8** 读原话文件, 只裁掉尾随 CR/LF。F20 那次是内层 python -c 把原话
    # 经控制台(cp936)打印再经 shell 变量转运, 令牌字节与文件字节就不是同一份(9701aa45… vs
    # e3b3a647…), 而账本与批准句都当时已花出去 —— 从这里取字节, 中间层就不存在了。
    p = Path(path)
    if not p.is_file():
        raise Stop("TOKEN_VERBATIM_FILE_ABSENT", "%s => 拒绝(不猜原话内容)" % p)
    canon = p.read_bytes().rstrip(b"\r\n")
    try:
        canon.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Stop("TOKEN_VERBATIM_NOT_UTF8", "%s:%s" % (p.name, e))
    return canon


def verify_token_chain(root, approval_id, passed_bytes, verbatim_file=None):
    """开口之前核三份摘要: **原话文件字节 / 实际传入的令牌字节 / 账本绑定的令牌摘要**。

    任一对不上就具名拒绝, 一条命令都不发。返回三份摘要供落证(不返回"差不多")。
    """
    if not approval_id:
        raise Stop("APPROVAL_ID_REQUIRED", "令牌核对需要批准身份(账本按它命名)")
    if passed_bytes is None:
        raise Stop("TOKEN_NOT_PROVIDED",
                   "真实执行需要令牌字节: --approval-token 或 --approval-token-file 至少给一个")
    dig = {"passed_token_sha256": sha_bytes(bytes(passed_bytes)),
           "passed_token_bytes": len(passed_bytes)}
    if verbatim_file:
        v = token_bytes_from_file(verbatim_file)
        dig["verbatim_file_sha256"] = sha_bytes(v)
        dig["verbatim_file_bytes"] = len(v)
    path = approval_path(root, approval_id)
    if not path.is_file():
        raise Stop("APPROVAL_LEDGER_ABSENT", "%s 不存在 => 拒绝开口" % path)
    try:
        doc = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Stop("APPROVAL_LEDGER_CORRUPT", "令牌核对读账本失败:%s" % e.__class__.__name__)
    bound = (doc.get("approval_token") or {}).get("sha256")
    if not is_hex_sha(bound):
        raise Stop("APPROVAL_TOKEN_NOT_BOUND",
                   "账本 %s 没登记批准令牌摘要 => 拒绝开口(创建时给 --approval-token-file)"
                   % approval_id)
    dig["ledger_bound_sha256"] = bound
    bad = []
    if dig["passed_token_sha256"] != bound:
        bad.append("传入令牌=%s… != 账本绑定=%s…" % (dig["passed_token_sha256"][:12], bound[:12]))
    if "verbatim_file_sha256" in dig and dig["verbatim_file_sha256"] != bound:
        bad.append("原话文件=%s… != 账本绑定=%s…" % (dig["verbatim_file_sha256"][:12], bound[:12]))
    if bad:
        raise Stop("TOKEN_DIGEST_MISMATCH", "; ".join(bad))
    return dig


def open_approval(transport, root, approval_id, identities, caps, led, clock):
    """返回 ApprovalLedger(stub 且没给身份 => None, 与旧的单次内存账一致)。

    门在这里, 不在某一步的判据里:
      * 没有 approval_id => APPROVAL_ID_REQUIRED;
      * 账本不存在 => APPROVAL_LEDGER_ABSENT —— **运行期不建账**, 要先离线 --approval-init;
      * 账本损坏/版本不符/身份不符 => 由 ApprovalLedger 每次读盘时具名拒绝。

    R6A §3: 上一版这里只对 real 查了一次 exists, 之后 reserve/commit/snapshot 仍会走进
    自动建账分支, 所以"真实路径缺失从不自建满额账"并不成立; 现在 real 与 stub 同一条门,
    而**每一次**读盘都严格(见 ApprovalLedger._load)。缺目录也不再被顺手 mkdir。
    """
    if not approval_id:
        if transport == "real":
            raise Stop("APPROVAL_ID_REQUIRED",
                       "真实执行必须绑定一次批准身份: 额度账要挂在那次批准上, 不挂就不发命令")
        return None
    validate_caps(caps)
    path = approval_path(root, approval_id)
    if not path.exists():
        raise Stop("APPROVAL_LEDGER_ABSENT",
                   "%s 不存在 => 一条命令都不发(不自建满额账; 先离线 --approval-init 落一份)。"
                   "transport=%s" % (path, transport))
    return ApprovalLedger(path, approval_id, identities, caps, led, clock)


def approval_init(root, approval_id, identities, caps, note="", token_bytes=None,
                  token_source=""):
    """离线**显式**开立一本账: 唯一允许"从空开始"的入口, 由人主动调用, 执行器顺手建不出来。

    R6A §3 的三条都在这一函数里:
      * 身份三件必须齐全(64 位小写十六进制) —— 缺 SHA 的批准根本立不起来;
      * spent 在 init 时按 caps 的**每一个**动作键预置 0: 于是"缺动作键"在读侧就是结构损坏,
        而不是"悄悄当 0";
      * 创建用同一把互斥锁 + O_CREAT|O_EXCL: 没有"exists 检查后再 os.replace"的覆盖窗口,
        双 init 里只有一个成功, 另一个具名拒绝(覆盖成功的那一次等于退款)。
    """
    if not approval_id:
        raise Stop("APPROVAL_ID_REQUIRED", "--approval-init 需要批准身份(账本按它命名)")
    validate_caps(caps)
    missing = [k for k in APPROVAL_IDENTITY_KEYS if not is_hex_sha((identities or {}).get(k))]
    if missing:
        raise Stop("APPROVAL_IDENTITIES_INCOMPLETE",
                   "%s 不是可用的批准身份 => 开立即拒绝(三件 SHA 必须齐全; "
                   "plan_sha256 要 --plan 指向真实计划字节)" % ",".join(missing))
    path = approval_path(root, approval_id)
    doc = {"version": ApprovalLedger.VERSION, "approval_id": str(approval_id),
           "identities": {k: identities[k] for k in APPROVAL_IDENTITY_KEYS},
           "caps": dict(caps), "spent": {k: 0 for k in caps}, "pending": [],
           "writes": 0, "created_by_action": "approval_init", "note": note}
    # 批准令牌只登记**摘要与字节数**, 不落原文(原话是操作者的材料, 账本会进证据链)。
    # 四件实体身份(identities)证的是"批准绑在哪份字节上"; 这一格证的是"这次开口用的确实是
    # 那句原话" —— 两件事分开记, 缺这格就无法开口(见 verify_token_chain)。
    if token_bytes is not None:
        if not isinstance(token_bytes, (bytes, bytearray)):
            raise Stop("TOKEN_BYTES_NOT_BINARY",
                       "令牌必须按**字节**递进来(str 请在调用侧 encode('utf-8'))")
        doc["approval_token"] = {"sha256": sha_bytes(bytes(token_bytes)),
                                 "bytes": len(token_bytes),
                                 "source": str(token_source or "caller")}
    validate_ledger_doc(doc, approval_id, identities, caps)
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Stop("APPROVAL_LEDGER_ROOT_UNUSABLE",
                   "%s:%s" % (Path(root), e.__class__.__name__))
    lk = None
    try:
        lk = ledger_lock(path, RealClock(), approval_id)
        if path.exists():
            raise Stop("APPROVAL_LEDGER_ALREADY_EXISTS",
                       "%s 已存在: 换批准身份或按已消耗处理, 不覆盖既有账(覆盖等于退款)" % path)
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # 独占创建, 不 replace
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=1)
            f.flush()
            os.fsync(f.fileno())
        return path
    except FileExistsError:
        raise Stop("APPROVAL_LEDGER_ALREADY_EXISTS",
                   "%s 已被另一个 init 独占创建 => 本次不覆盖(同名批准只能有一份账)" % path)
    except OSError as e:
        raise Stop("APPROVAL_LEDGER_ROOT_UNUSABLE",
                   "%s:%s" % (path, e.__class__.__name__))
    finally:
        if lk:
            ledger_unlock(lk)



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
# F5(任务书 §2 明示增加的范围): 只在**尝试过** safety_irq_enable 的场里, 三条之后同一端口内
# 再发这两条; 各一次、各占自己的预留额度, 失败不阻断前面的撤能, 也不关闭第二次端口。
IRQ_TAIL_CMDS = ("safety_irq_disable", "safety_irq_status")
# 产品闭合态那一条来自 safety_thread.c:306 的 `IRQ gate CLOSED - 活动安全输入…无软件响应路径`
# (大写 CLOSED; 修前我这正则写的是小写 closed, 那样**永远匹配不上**, 尾部就成了恒红)。
IRQ_TAIL_ROW = r"^\[SAFETY\] IRQ gate CLOSED"
# F5e: 同一个"门已关"事实在产品里有**两条不同的打印**, 各自只跟在自己的命令后面:
#   safety_thread.c:306  st_irq_detach_all() => "[SAFETY] IRQ gate CLOSED - 活动安全输入(N 路)..."
#                        (safety_irq_disable 自己打的)
#   safety_thread.c:388  safety_irq_status()  => "[SAFETY] irq gate   = CLOSED (polled only)"
#                        (尾部第二条状态回读打的)
# 修前只在 status 的回包上认第一条, 于是整包程序(先 disable 再 status)永远判
# IRQ_TAIL_NOT_CLOSED。两行都收进集合, 但 disable 那一步单独要求 :306(不许拿 status 顶掉
# "disable 确实动手了"这条证据)。
IRQ_TAIL_CLOSED_ROWS = (IRQ_TAIL_ROW, r"^\[SAFETY\] irq gate\s+= CLOSED\b")
CLEANUP_WINDOW_SECONDS = 1.2   # 每条清理命令自己的确认窗(五条互不抢占)

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
    # R6A §5: 清理路径与普通响应共用同一个原始字节门(危险文本 -> 裸 NUL)。命中不抛出去 ——
    # 它记成这条清理自己的**证据错误**(一次就够, 不再重复判读), 必要撤能照旧向后走;
    # 但如果坏的是最后那份 motor_status, rows 就是空 => 禁能一律 UNKNOWN, 整场 success=False
    # (不许用另一场的干净回包补这一场)。
    defect = None
    try:
        guard_partial_bytes(bytes(buf), led, where="cleanup_read", evidence=raw_path)
    except Stop as e:
        defect = e.code
        errs.append("%s:%s" % (cmd, e.code))
    if not bytes(buf).strip():
        errs.append("%s:NO_RESPONSE" % cmd)
        return [], bytes(buf), sent_ok_t, ended_t, errs
    if defect:
        rows = []
    else:
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


RUNNING_HZ_ROW = (r"^\[MOT\] cur_hz=(?P<cur>[0-9]+) tgt_hz=(?P<tgt>[0-9]+) "
                  r"accel=(?P<accel>[0-9]+) decel=(?P<decel>[0-9]+) dir=(?P<dir>[0-9]+)$")
# 产品真实成功行(motor.c:1415)。修前的夹具写 "[MOT] DISARM (done)", 产品**从不**打这一行,
# 而 F3 要的正是"义务关闭须有真实成功 disarm 输出", 泛匹配 DISARM 字样不算。
DISARM_DONE_ROW = r"^\[MOT\] DISARM \(动作已做: STEP (?P<step>\S+) / 许可 LOW (?P<grant>[A-Za-z_]+)"
ARMED_OK_ROW = r"^\[MOT\] ARMED \(drv_en request=HIGH readback verified, ENN feedback=LOW"


def running_contract(st, rows):
    """F4: "在跑"= 巡航态 + 武装 + 非零且等于目标频率 + 使能侧反馈自洽。

    逐项都从**同一份响应**里取(不跨响应拼字段)。计划可用 `expect_running` 再钉死
    dir/cur/tgt/accel/decel 的实值(本轮任务书 §4: DIR=1、300 Hz、加减速 2000 Hz/s)。
    返回 (fields, bad): bad 非空即这份响应没证得运行。"""
    summ, bad = disable_contract(rows)
    if summ is None:
        return None, ["PROFILE_FIELDS_UNREADABLE"]
    bad = []
    hits = [m for m in (re.fullmatch(RUNNING_HZ_ROW, r) for r in rows if r) if m]
    if len(hits) != 1:
        return summ, ["RUNNING_HZ_ROW_MISSING_or_DUPLICATE=%d" % len(hits)]
    g = hits[0].groupdict()
    if summ["state_num"] != RUNNING_REQUIRED_STATE:
        bad.append("state=%d(要求 MOTOR_CRUISE=%d)" % (summ["state_num"], RUNNING_REQUIRED_STATE))
    if summ["armed"] != 1:
        bad.append("armed=%d(要求 1)" % summ["armed"])
    if int(g["cur"]) <= 0:
        bad.append("cur_hz=%s(没在动)" % g["cur"])
    if int(g["tgt"]) <= 0:
        bad.append("tgt_hz=%s(没在动)" % g["tgt"])
    if g["cur"] != g["tgt"]:
        bad.append("cur_hz=%s!=tgt_hz=%s(还在爬坡/减速, 不算巡航)" % (g["cur"], g["tgt"]))
    if summ["drv_request"] != "HIGH" or summ["pad"] != 1:
        bad.append("drv_request=%s/pad=%d(与武装运行不相容)" % (summ["drv_request"], summ["pad"]))
    if summ["expected_ENN"] != "LOW" or summ["enn_fb"] != 0:
        bad.append("expected_ENN=%s/ENN_fb=%d(要求 LOW/0: 硬件没让动就不是正常巡航)" %
                   (summ["expected_ENN"], summ["enn_fb"]))
    if summ["fb_state"] != "resolved" or summ["verdict"] != "OK":
        bad.append("fb=%s/verdict=%s(要求 resolved/OK; CUT_BY_HARDWARE 与 UNRESOLVED 都不放行)" %
                   (summ["fb_state"], summ["verdict"]))
    if summ["health"] != "OK":
        bad.append("health=%s" % summ["health"])
    want = st.get("expect_running") or {}
    for k in ("dir", "cur", "tgt", "accel", "decel"):
        if k in want and str(want[k]) != g[k]:
            bad.append("expect_running.%s=%s 而响应是 %s" % (k, want[k], g[k]))
    return summ, bad


def disarm_success_evidence(rows):
    """F3: 只认产品那条 `[MOT] DISARM (动作已做: ... / 许可 LOW verified / ...)`,
    且许可那一路必须 verified; 子串 "DISARM" 不算。"""
    hits = [m for m in (re.match(DISARM_DONE_ROW, r) for r in rows if r) if m]
    if len(hits) != 1:
        return False, "DISARM_DONE_ROW_MISSING_or_DUPLICATE=%d" % len(hits)
    if hits[0].group("grant") != "verified":
        return False, "许可 LOW %s(未 verified, 禁能动作没证得)" % hits[0].group("grant")
    return True, ""


ZERO_ARMED_TRUE_SHAPE = ("HIGH", 1, "LOW", 0)
ZERO_ARMED_FALSE_SHAPE = ("LOW", 0, "HIGH", 1)


def zero_speed_contract(rows):
    """裁定一: "新鲜、完整、可信的零速快照" => (summary, bad)。

    六项合同字段必须来自**同一份** motor_status 响应(复用 disable_contract 的唯一性/齐备要求,
    不跨响应拼字段); 然后:
      * `state=0`、`cur_hz=0`、`tgt_hz=0`、`health=OK`;
      * 反馈解算必须 resolved 且判决不是 CUT_BY_HARDWARE;
      * 使能侧两套自洽形状: 仍武装(armed=1) => 请求 HIGH/pad=1 且 ENN 预期 LOW/反馈 0;
        已禁能(armed=0) => 请求 LOW/pad=0 且 ENN 预期 HIGH/反馈 1。别的组合一律不算。
    注意这里**不**要求 diag_off_confirmed=YES —— 那是"禁能已证得"的字段, 不是零速字段,
    stop 之后仍武装的那份真实回包就是 NO。
    """
    summ, _bad = disable_contract(rows)
    if summ is None:
        return None, ["ZERO_SNAPSHOT_FIELDS_INCOMPLETE"]
    bad = []
    if summ["state_num"] != 0:
        bad.append("state=%d(要求 0)" % summ["state_num"])
    if summ["cur_hz"] != 0 or summ["tgt_hz"] != 0:
        bad.append("cur_hz=%d/tgt_hz=%d(要求 0/0)" % (summ["cur_hz"], summ["tgt_hz"]))
    if summ["health"] != "OK":
        bad.append("health=%s" % summ["health"])
    if summ["fb_state"] != "resolved":
        bad.append("fb=%s(未解算的不算可信快照)" % summ["fb_state"])
    if summ["verdict"] == "CUT_BY_HARDWARE":
        bad.append("verdict=CUT_BY_HARDWARE(硬件切断不作为正常完成证据)")
    shape = ZERO_ARMED_TRUE_SHAPE if summ["armed"] == 1 else \
        (ZERO_ARMED_FALSE_SHAPE if summ["armed"] == 0 else None)
    if shape is None:
        bad.append("armed=%d(只认 0/1)" % summ["armed"])
    else:
        got = (summ["drv_request"], summ["pad"], summ["expected_ENN"], summ["enn_fb"])
        if got != shape:
            bad.append("使能侧形状=%s 要求 %s(armed=%d 那一套自洽值)"
                       % (got, shape, summ["armed"]))
    return summ, bad


def motion_end_gate(budget, st, cmd, txt, start_attempts, stop_confirmed, asname, led, clock):
    """裁定一: 只在**实际执行路径**上按证据释放运动计时(不许只在静态表上切开)。

    全部前提缺一不可: 本会话已尝试过那唯一一次 start; 其后有一条**正常 motor_stop** 已发送
    并拿到成功响应(stop_confirmed 由调用点在判 OK 后置位); 这一步自己申报 motion_end_evidence;
    命令就是 motor_status; 同一份回包通过 zero_speed_contract。任何一项没满足 => 运动 cap 继续。
    """
    if not st.get(MOTION_END_KEY) or cmd != "motor_status":
        return None
    miss = []
    if start_attempts < 1:
        miss.append("本会话没有 motor_start 的写入尝试(不能关闭一笔不存在的运动)")
    if not stop_confirmed:
        miss.append("本会话没有已发送且判 OK 的正常 motor_stop(回话本身不算零速)")
    if not budget.armed:
        miss.append("运动计时还没起算")
    if miss:
        raise Stop("MOTION_END_PRECONDITION_MISSING", json.dumps(
            {"as": asname, "missing": miss}, ensure_ascii=False))
    summ, bad = zero_speed_contract(_rows_of(txt))
    if bad:
        # 残缺/矛盾/旧形状的快照 => 运动义务照旧, 并给出具名原因(不静默放行)。
        raise Stop("MOTION_END_SNAPSHOT_NOT_TRUSTWORTHY", json.dumps(
            {"as": asname, "bad": bad, "snapshot": summ}, ensure_ascii=False))
    elapsed = clock.monotonic() - budget.t0
    budget.confirm_zero_speed(elapsed, {"as": asname, "snapshot": summ})
    led.ev("MOTION_TIMER_ENDED", **{"as": asname, "elapsed_from_start_s": round(elapsed, 3),
                                    "motion_cap_seconds": budget.cap,
                                    "arm_deadline_kept": True})
    return round(elapsed, 3)


def _ev_or_defer(led, deferred, kind, **kw):
    """清理框架内的**记账写入**: 写不进去绝不许抛, 只把这份事件原样收进内存清单 `deferred`。

    R6C §4: 上一轮把"预占异常"隔离了, 但紧接着这条披露写入没隔离 —— 一次 `OSError`
    就让整个 cleanup 退出, 三条必要撤能一条没发(禁能 UNKNOWN)。这里的合同是:
      * 发送机会**不依赖**日志写得成 —— 记账失败只是把这条事件推到 deferred, 由调用方
        在尽力发完之后二次落证;
      * 不吞掉: 二次落证成/败都落成一条具名内存错误(errors 非空 => 本场一律不成功);
      * 不是"全局忽略 Ledger 错": 只有清理路径上的这几处走本函数, 其余 ledger 写入照旧。
    """
    try:
        led.ev(kind, **kw)
        return True
    except Exception as e:
        deferred.append({"kind": kind, "kw": kw,
                         "err": "%s:%s" % (e.__class__.__name__, repr(e))})
        return False


def _flush_deferred_ev(led, deferred, errors):
    """清理末尾的二次落证: 仍然失败也只多一条具名披露, 绝不反过来改变"撤能已发"这个事实。

    两条路径都留错误(non-empty errors => 本场不成功): 迟到写成功记
    `LEDGER_EVENT_WRITTEN_AFTER_RETRY`, 写不成记 `LEDGER_EVENT_WRITE_FAILED`。
    """
    for item in list(deferred):
        try:
            led.ev(item["kind"], retry_of_deferred=True, **item["kw"])
        except Exception as e:
            errors.append("LEDGER_EVENT_WRITE_FAILED:%s:%s" %
                          (item["kind"], "%s:%s" % (e.__class__.__name__, repr(e))))
            continue
        errors.append("LEDGER_EVENT_WRITTEN_AFTER_RETRY:%s" % item["kind"])
        deferred.remove(item)


def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None,
            quota_box=None, overruns=None, used_box=None, approval=None, reservations=None,
            energize=None, irq_attempted=False, motor_obligation=True):
    """义务建立后的唯一清理路径(M6 语义 + R3-B 分段窗): motor_stop -> motor_disarm -> motor_status。
    返回 (disarm_state, errors); 本函数**绝不**抛: 主错由调用方保留, 清理失败单独记账。
    三条命令各有自己的确认窗(总保留量分三段), 任何一条没回都**不减少**后面一条的发送机会 ——
    这正是 R2 交付版的缺陷(STOP_RESPONSE_MISSING 里 disarm 从未发出)。
    禁能成立与否只由**最后这一份新的 motor_status 响应**里的合同字段决定, 不跨响应拼字段。

    带 approval 时撤能预留也在**持久账**上预约(这样"本次批准一共撤了几次能"跨进程可见),
    但预约失败**不阻止发送**: 撤能优先于额度, 失败只落成具名披露 => 本场一律不算成功。"""
    errors = []
    state = "UNKNOWN"
    deferred_ev = []            # R6C §4: 记账写不进去时的事件内存副本(命令照发, 末尾尽力落证)
    if overruns is None:
        overruns = []
    hard = budget.cleanup_deadline()
    out_dir = led.out
    status_rows = []
    # F5b①: 电机那三条只由**电机义务**决定发不发。"先开 IRQ、随后失败"这一类场里电机从未
    # 武装, 那三条与本场义务无关(发它们既花额度又造出无关响应), 但 IRQ 尾部**必须**照发。
    sends = list(CLEANUP_CMDS) if motor_obligation else []
    chances = len(sends) + (len(IRQ_TAIL_CMDS) if irq_attempted else 0)
    # F5b②: 会话总截止把预留夹短到排不下本场需要的全部发送窗时, 在**第一条清理命令之前**
    # 就把这件事具名披露出来。修前只有第 N 条 *_HARD_DEADLINE_BEFORE_* 才露一手, 四条之内
    # 看不出"第五条本来就来不及"——那是事后归因, 不是事前可见。
    need_windows = chances * CLEANUP_WINDOW_SECONDS
    left = hard - clock.monotonic()
    if left < need_windows:
        errors.append("CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE " + json.dumps(
            {"left_seconds": round(max(0.0, left), 3),
             "required_seconds": round(need_windows, 3), "send_chances": chances,
             "reserve_seconds": budget.reserve,
             "session_abs_deadline_set": budget.session_abs_dl is not None},
            ensure_ascii=False))
    # 收口起点具名入台账: 之后每一条 TX 才**可判定**属于收口还是业务(判据不能靠命令名猜——
    # 业务里也允许发 motor_status, 例如 disarm 后的相邻禁能快照)。
    _ev_or_defer(led, deferred_ev, "CLEANUP_ENTERED", motor_obligation=bool(motor_obligation),
                 irq_attempted=bool(irq_attempted), send_chances=chances,
                 reserve_seconds=budget.reserve, hard_deadline=round(hard, 3),
                 left_seconds=round(max(0.0, left), 3))
    for cmd in sends:
        dl = min(hard, clock.monotonic() + CLEANUP_WINDOW_SECONDS)
        if clock.monotonic() >= hard:
            errors.append("CLEANUP_HARD_DEADLINE_BEFORE_" + cmd)
            continue                      # 不 break: 后面的必要撤能仍要在自己的窗里试
        # 撤能预留额度按计划**独立**登记(与业务 motor_run 分列): 一条命令的缺帧不能
        # 吃掉另一条的发送机会。预留未申报/耗尽时仍然发送(安全动作优先于额度), 但那
        # 是一条具名披露 => 本会话一律不算成功。
        if quota_box is not None:
            key = "cleanup_" + cmd
            denied = None
            if key not in quota_box:
                overruns.append(key + ":RESERVE_NOT_DECLARED")
            else:
                if approval is not None:
                    try:
                        res = approval.reserve(key, 1, kind="cleanup")
                        if reservations is not None:
                            reservations.append(res)
                    except Stop as e:
                        denied = e.code     # 账本说没了也照发: 见本函数头的"撤能优先于额度"
                    except Exception as e:
                        # R6B §4.2: 账本的**普通** Python 异常同样不许离开清理框架 —— 这一条
                        # 照发, 后面两条必要撤能也照发; 但这是一条具名披露 => 本会话一律不算成功。
                        # 不是"忽略异常报成功": 披露进 overruns, success 由 overruns 判掉。
                        denied = "APPROVAL_RESERVE_FRAME_ERROR_%s" % e.__class__.__name__
                if quota_box[key] <= 0:
                    overruns.append(key + ":RESERVE_EXHAUSTED_SENT_ANYWAY")
                else:
                    quota_box[key] -= 1
                    if used_box is not None:
                        used_box[key] = used_box.get(key, 0) + 1
                if denied:
                    overruns.append("%s:%s_SENT_ANYWAY" % (key, denied))
                    # R6C §4.1: 这条披露**写在发送之前**, 所以它绝不能成为发送的前提。
                    # 上一版这里直接 led.ev(...), 一次 OSError 就把后面三条必要撤能全吃掉。
                    if not _ev_or_defer(led, deferred_ev, "CLEANUP_RESERVE_REFUSED_SENT_ANYWAY",
                                        key=key, code=denied):
                        overruns.append("%s:BOOKKEEPING_DEFERRED" % key)
        try:
            rows, raw, sent_t, ended_t, errs = _cleanup_read(port, led, clock, dl, cmd,
                                                             out_dir, feed_hook)
        except Exception as e:
            errors.append("%s:CLEANUP_RAISED_%s" % (cmd, e.__class__.__name__))
            continue                # 本函数的合同是"绝不抛": 一条异常不吃掉下一条必要撤能
        errors.extend(errs)
        if cmd == "motor_status":
            status_rows = rows
    if not motor_obligation:
        # "不需要禁能"是**已知事实**(电机从未武装 => 终态 KNOWN), 不是"证实了禁能"。
        # 这两个字符串不能互换: 把 NOT_ARMED 写成 CONFIRMED_DISABLED 就是替设备编证据。
        state = "NOT_ARMED"
        _ev_or_defer(led, deferred_ev, "CLEANUP_MOTOR_NOT_OBLIGATED",
                     irq_attempted=bool(irq_attempted), sends=0)
    else:
        summary, bad = disable_contract(status_rows)
        _ev_or_defer(led, deferred_ev, "DISARM_FIELDS", fields=summary, unproven=" ".join(bad))
        if bad:
            errors.append("DISARM_NOT_CONFIRMED " + " ".join(bad))
        else:
            state = "CONFIRMED_DISABLED"
            if energize is not None and energize.await_disable_snapshot:
                led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                       cap_seconds=energize.cap, basis="cleanup_status")
                energize.await_disable_snapshot = False
    # F5: 固定 IRQ 尾部排在**禁能判据算完之后** —— 尾部任何失败只追加具名披露,
    # 绝不回头改上面已经成立的 state, 也不吃掉撤能(任务书 §2: 尾部失败不能阻断前面的撤能)。
    if irq_attempted:
        for cmd in IRQ_TAIL_CMDS:
            dl = min(hard, clock.monotonic() + CLEANUP_WINDOW_SECONDS)
            if clock.monotonic() >= hard:
                errors.append("IRQ_TAIL_HARD_DEADLINE_BEFORE_" + cmd)
                continue
            key = "cleanup_" + cmd
            if quota_box is not None:
                if key not in quota_box:
                    overruns.append(key + ":RESERVE_NOT_DECLARED")
                else:
                    if approval is not None:
                        try:
                            res = approval.reserve(key, 1, kind="cleanup")
                            if reservations is not None:
                                reservations.append(res)
                        except Stop as e:
                            overruns.append("%s:%s_SENT_ANYWAY" % (key, e.code))
                        except Exception as e:
                            overruns.append("%s:APPROVAL_RESERVE_FRAME_ERROR_%s_SENT_ANYWAY"
                                            % (key, e.__class__.__name__))
                    if quota_box[key] <= 0:
                        overruns.append(key + ":RESERVE_EXHAUSTED_SENT_ANYWAY")
                    else:
                        quota_box[key] -= 1
                        if used_box is not None:
                            used_box[key] = used_box.get(key, 0) + 1
            try:
                trows, _, _, _, terrs = _cleanup_read(port, led, clock, dl, cmd,
                                                      out_dir, feed_hook)
            except Exception as e:
                errors.append("IRQ_TAIL_RAISED_%s:%s" % (cmd, e.__class__.__name__))
                continue
            errors.extend("IRQ_TAIL_" + x for x in terrs)
            if cmd == "safety_irq_disable" and not any(
                    re.match(IRQ_TAIL_ROW, r) for r in trows if r):
                errors.append("IRQ_TAIL_DISABLE_NO_DETACH_LINE")
            if cmd == "safety_irq_status" and not any(
                    re.match(x, r) for r in trows if r for x in IRQ_TAIL_CLOSED_ROWS):
                errors.append("IRQ_TAIL_NOT_CLOSED")
                led.ev("IRQ_TAIL_STATE", closed=False, rows=len(trows))
            elif cmd == "safety_irq_status":
                led.ev("IRQ_TAIL_STATE", closed=True, rows=len(trows))
    # 三条必要撤能已经发完、禁能判据已经算完, 现在才回头尽力落证。失败也只是多一条披露:
    # **不**因为"日志写成功"而撤销已发送的事实, 也**不**因为写失败就把本场报成成功。
    _flush_deferred_ev(led, deferred_ev, errors)
    return state, errors
# --------------------------------------------------------------------------- 会话
# ---- R6A R2: 命令 -> 额度键的**集中映射**(复审 §4) ----------------------------------
# 旧版记账依赖步骤自己申报 `st["quota"]`: 不写就不扣, 于是同一执行引擎可以在
# `tmc_config_apply=0` 的批准下把 `tmc_config_apply CONFIRM` 发出去。映射表把这些命令和
# 额度键钉在一起, 缺申报/错键/n 不符都在**发送之前**具名拒绝。
# 只列**会改变设备或持久状态**的命令: 只读查询(motor_status/diag_status/… 与有限轮询)
# 不在这里 —— 把轮询误计成 apply 重试是复审 §4 明确禁止的另一件事。
QUOTA_COMMAND_KEYS = (
    ("tmc_config_apply", "tmc_config_apply"),
    ("config_save", "config_save"),
    ("motor_start", "motor_run"),
    ("safety_polarity_confirm", "irq_polarity_prep"),
    ("safety_irq_enable", "irq_polarity_prep"),
    ("diag_profile zero", "diag_profile_zero"),
    ("diag_profile learn", "diag_profile_learn"),
    ("diag_profile commit", "diag_profile_commit"),
    ("blackbox_clear", "blackbox_clear"),
    # E3: diag_profile learn_cancel <ID> 改作业状态(rel_job_cancel 置 CANCEL_PENDING), 不是只读查询。
    # 旧映射漏列属工具覆盖缺口, 不能据此免记账(任务书 §2 learn_cancel 裁定)。
    ("diag_profile learn_cancel", "diag_profile_learn_cancel"),
)


def quota_key_for_command(cmd):
    """命令串(含参数)映射到它必须花的额度键; 不在表里 => None(只读/无额度动作)。"""
    c = " ".join(str(cmd or "").split())
    for prefix, key in QUOTA_COMMAND_KEYS:
        if c == prefix or c.startswith(prefix + " "):
            return key
    return None


def canonical_steps_sha(steps):
    """步骤列表的**字节身份**: 排序 + 无空白, 这样"同一批步骤"无论来自哪次 json 解析都同值,
    而任何字段改动都会变值(exec_session 用它核对实际执行的步骤 == 批准的步骤)。"""
    return sha_bytes(json.dumps(list(steps), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")).encode("utf-8"))


def verify_approved_binding(approved, steps, transport):
    """R6A §4: 真实动作前核对"这一刻要发的步骤"确实来自那份被批准的字节。

    两道都查, 因为它们拦的是两件不同的事:
      1) **计划文件字节**仍等于批准时登记的 plan_sha256 —— 拦"绑定之后有人改了 --plan 文件";
      2) **本会话实际步骤**等于从该文件同一阶段重新推导出的步骤 —— 拦"计划没动, 但递进来的
         步骤被换过/加过"(复审 §4 的 steps-not-bound 反例)。
    只做第 1 道是不够的: 单会话入口可以拿一份合法批准 + 一份完全不同的 steps 混进去。
    """
    if not isinstance(approved, dict):
        raise Stop("REAL_SESSION_REQUIRES_APPROVED_PACKAGE",
                   "真实执行只允许经批准的整包 dispatch(复审 §4 的最小修法); "
                   "单会话任意 --steps 不接(stub 工装入口保留)")
    for k in ("plan_path", "plan_sha256", "stage", "profile", "steps_sha256"):
        if not approved.get(k):
            raise Stop("APPROVED_PACKAGE_INCOMPLETE", "missing=%s" % k)
    pp = Path(approved["plan_path"])
    try:
        raw = pp.read_bytes()
    except OSError as e:
        raise Stop("APPROVED_PLAN_UNREADABLE", "%s:%s" % (pp, e.__class__.__name__))
    if sha_bytes(raw) != approved["plan_sha256"]:
        raise Stop("PLAN_BYTES_CHANGED_AFTER_APPROVAL",
                   "%s 现在是 %s…, 批准登记的是 %s… => 一条命令都不发" %
                   (pp.name, sha_bytes(raw)[:12], approved["plan_sha256"][:12]))
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Stop("APPROVED_PLAN_UNREADABLE", "%s:%s" % (pp, e.__class__.__name__))
    stage = [s for s in doc.get("stages", []) if s.get("id") == approved["stage"]]
    if len(stage) != 1:
        raise Stop("APPROVED_STAGE_NOT_FOUND",
                   "stage=%s 在批准的计划字节里出现 %d 次" % (approved["stage"], len(stage)))
    expected = stage_steps(stage[0], approved["profile"])
    got = [st for st in steps if not st.get("when") or approved["profile"] in st["when"]]
    if canonical_steps_sha(expected) != canonical_steps_sha(got):
        raise Stop("STEPS_NOT_BOUND_TO_APPROVAL",
                   "as stage=%s 计划里是 %d 步/%s…, 递进来的是 %d 步/%s… => 真实动作不进入" %
                   (approved["stage"], len(expected), canonical_steps_sha(expected)[:12],
                    len(got), canonical_steps_sha(got)[:12]))
    return {"verified": True, "stage": approved["stage"], "transport": transport,
            "plan_sha256": approved["plan_sha256"], "steps": len(got),
            "steps_sha256": canonical_steps_sha(got)}


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

    窗口尺寸(R5 现场轮的修正): 旧实现有一条**未申报**的静默早退(`>=min(2.0,window)` 且静默 0.2 s 即
    break), 于是申报 20 s 的窗实测 2.0 s 就关了 —— 而 stage-5 的 1 MiB 扫描按配置值估就要 ≥8.5 s
    (`host/m8diag_source_audit.txt` §3), 启动流被从中间剪断, 后续查询拿到 0 字节。现在申报的预算**走满**,
    只有两种提前结束: ① 看到正信号 `boot_done_needle`(产品自己的"启动完成"打印)再加一小段尾巴;
    ② 显式申报了 `boot_quiet_seconds` 才允许按静默早退。结束的**哪一种、申报多少、实耗多少**都进台账。
    """
    window = float(st.get("boot_drain_seconds", 20))
    needle = st.get("boot_done_needle")
    needle_b = needle.encode("utf-8") if needle else None
    grace = float(st.get("boot_done_grace_seconds", 2.0))
    quiet = st.get("boot_quiet_seconds")
    quiet = float(quiet) if quiet is not None else None
    t0 = last = clock.monotonic()
    dl = t0 + window
    raw = out / ("%02d_%s.boot.rx.bin" % (idx, asname))
    buf = bytearray()
    needle_at = None
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
                if needle_b and needle_at is None and needle_b in bytes(buf):
                    needle_at = last
            now = clock.monotonic()
            if needle_at is not None and now - needle_at >= grace:
                break
            if quiet is not None and now - t0 >= min(2.0, window) and now - last >= quiet:
                break
            clock.sleep(min(0.01, max(0.0, dl - now)))
    # Bytes repr is presentation only; do not decode/repair boot text into business facts.
    txt = emit_text(raw, [repr(bytes(buf))])
    spent = clock.monotonic() - t0
    end_reason = ("BOOT_DONE_NEEDLE" if needle_at is not None else
                  "QUIET_BREAK" if (quiet is not None and clock.monotonic() < dl)
                  else "DRAIN_BUDGET_REACHED")
    results.append({"as": asname + "_boot", "kind": "boot_stream", "rx_bytes": len(buf),
                    "rx_sha256": sha_of(raw), "txt": txt, "drain_declared_seconds": window,
                    "drain_spent_seconds": round(spent, 3), "boot_done_needle": needle,
                    "boot_quiet_seconds": quiet,
                    "readiness": "NOT_ASSESSED_QUERY_REQUIRED", "end_reason": end_reason})
    led.ev("BOOT_STREAM_DRAINED", **{"as": asname, "bytes": len(buf),
           "raw": str(raw), "end_reason": end_reason, "prompt_required": False,
           "drain_declared_s": window, "drain_spent_s": round(spent, 3),
           "done_needle": needle, "quiet_break_s": quiet})


def command_attempt(port, cmd, st, asname, raw, step_dl, transport, fixture, silent, led, clock,
                    attempt=1):
    """一次"发送 -> 取回完整响应 -> 判据 -> 原字节/文本落盘"的最小单元。
    单发步骤与业务轮询走**同一条门**, 所以轮询不会绕过短写、提示符、具名字段这些判据。"""
    data = (cmd + "\r\n").encode("ascii")
    pre = drain_pending(port, led, clock) if transport != "stub" else b""
    if pre:
        # R6A §5 最后一条: **发送前排走的残留**也是同一块控制台流上读到的字节。它被另存成
        # `.pre.rx.bin` 不等于过了危险门 —— 先原样存证, 再在同一条业务 write 之前判危险。
        # (清理写入仍按已经成立的义务执行, 那条路径在 exec_session 的 finally 里, 不受这里影响。)
        pre_path = Path(str(raw).replace(".rx.bin", ".pre.rx.bin"))
        try:
            with open(pre_path, "wb") as f:
                f.write(pre)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise Stop("EVIDENCE_WRITE_FAILED", "pre:%s" % e.__class__.__name__)
        led.ev("PRE_CMD_DRAIN_SAVED", raw=str(pre_path), bytes=len(pre), sha256=sha_bytes(pre),
               danger_gate="applied_before_write")
        guard_stream_bytes(pre, led, where="pre_drain", evidence=pre_path)
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
            # F6d: 序列只在**这一步自己申报了轮询**时才生效。修前只要 fixture 里存在这个键的序列,
            # 任何同键的非轮询步骤(整包 §5 行 10 的"取消后仍巡航"查询就撞上)都会取到序列的**第一份**
            # (ACCEL), 于是那一步在自己的单次尝试里读到爬坡行 => PROFILE_NOT_RUNNING。序列是"这一
            # 步轮询期间的可见状态轨迹"的申报, 不是按命令键给全场发牌; 只影响离线注入形状,
            # 现场真串口不查这张表。
            seq = ((fixture.get("sequence") or {}).get(key)
                   if (st.get("poll") or st.get("poll_sequence")) else None)
            if seq:                     # 轮询用**申报过的**响应序列(如先 ACCEL 后 CRUISE), 越界取最后一份
                resp = seq[min(attempt, len(seq)) - 1]
            else:
                resp = fixture.get("responses", {}).get(key)
            if resp is None:
                raise Stop("STUB_MISS", cmd)
            # 同步命令会把单线程 msh 占住: 响应只在忙期结束后才可见(审核 §3 "忙 msh")。
            block = float((fixture.get("block") or {}).get(key, 0) or 0)
            port.feed(("\r\n".join(resp) + "\r\nmsh >").encode("utf-8"), block_seconds=block)
    rec = st.get("byte_corruption_recovery")
    if rec:
        # R6(复审 §5.3): 执行器已经取消"损坏后由后面某步重确认"这条路, 所以这里**不接受**该申报。
        # 计划层还有 PLAN_BYTE_RECOVERY 会把这种申报判红; 运行时再挡一次, 防止绕过校验直接喂步骤。
        raise Stop("RETIRED_RECOVERY_CONTRACT",
                   "as=%s 申报了 byte_corruption_recovery(%s), 但本版执行器一律'字节损坏即停'" %
                   (asname, rec.get("reconfirmed_by", "")))
    lines, nbytes = read_until_prompt(port, step_dl, led, raw, clock)
    if transport != "stub" and cmd != "__watch__" and _is_real_serial_port(port):
        full = Path(str(raw)).read_bytes()
        if not has_echo(full, cmd):
            # 只对**真串口**要求回显: finsh 的 echo 默认开, 命令到达就必然回显。
            # 反过来不行 —— `motor_start` 成功时产品一个字节都不打(计划里 expect_absent 写明),
            # 所以"响应为空"本身不是证据; 而现场实测那种 5 B 纯陈旧提示符, 缺的正是回显。
            # 桩件/内存端口不带回显, 因此不参与这条门(它们不是被测对象)。
            raise Stop("COMMAND_NOT_ECHOED", "as=%s bytes=%d body_empty=%s tail=%r" %
                       (asname, nbytes,
                        not PROMPT_RE.sub(b"", ANSI.sub(b"", full)).strip(), full[-24:]))
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


POLL_GATE_MOTOR = "motor_ramping"
POLL_GATE_ROW = "in_progress_row"
MOTOR_RAMPING_STATES = (MOTOR_STATE_ENUM["ACCEL"], MOTOR_STATE_ENUM["DECEL"])


def poll_gate_of(p):
    """没申报 in_progress => 沿用旧的 [MOT] state 门(R7F7 语义逐字节不变); 申报了 => 改用
    "响应里那一行的状态字段是否仍在申报的进行中集合里"。E1 要的就是这个分离: learn_status
    一行都不带 [MOT] state=, 旧门会把"还没到 DONE"直接判成不一致而当场停。"""
    spec = p.get("in_progress")
    if not spec:
        return POLL_GATE_MOTOR, None, None
    return POLL_GATE_ROW, spec["row"], tuple(spec["states"])


def poll_observe(gate, in_row, in_states, txt):
    """返回(观测到的进行中值, 该行命名组)。判不出/不在集合里 => 第一项 None => 立刻具名停止。
    行缺失、同形行出现多条、值在集合外(DONE/FAILED/CANCELLED/TIMEOUT/IDLE)都算"不在进行中" ——
    轮询绝不把一个业务终态等成窗口耗尽(那会把"失败了"报成"只是没等到")。"""
    if gate == POLL_GATE_MOTOR:
        s = ramping_state(txt)
        return (s if s in MOTOR_RAMPING_STATES else None), {}
    try:
        rows = Path(str(txt)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, {}
    hits = [h for h in (re.match(in_row, r) for r in rows if r) if h]
    if len(hits) != 1:
        return None, {}
    g = hits[0].groupdict()
    val = g.get("st")
    return (val if val in in_states else None), g


# -------------------------------------------------- E2: 命名作业绑定(不是通用模板系统)
JOB_ID_MIN = 1
JOB_ID_MAX = 999999999      # 产品: "learn_cancel JOB_ID must be 1..999999999"(diag_relative.c)
JOB_ROW_STARTED = (r"^\[DIAG\] learn STARTED id=(?P<job_id>[0-9]+) n=(?P<n>[0-9]+) "
                   r"win=(?P<lo>[0-9]+)\.\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")
JOB_REF_RE = re.compile(r"^@([a-z][a-z0-9_]{0,31})$")
CANCEL_PREFIX_TOKENS = ("diag_profile", "learn_cancel")
CANCEL_ROWS = (
    ("REQUESTED", r"^\[DIAG\] cancel id=(?P<id>[0-9]+) REQUESTED st=(?P<st>[A-Z_]+)"),
    ("ALREADY_TERMINAL", r"^\[DIAG\] cancel id=(?P<id>[0-9]+) NOT-CANCELLED: already "
                         r"(?P<st>[A-Z_]+) why=(?P<why>\S+)"),
)


def _rows_of(txt_path):
    try:
        return Path(str(txt_path)).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        raise Stop("JOB_BIND_TEXT_MISSING", str(txt_path))


def bind_job_id(st, txt, raw, bindings, led, asname, cmd=None):
    """E2: 把**这一步自己**的完整响应里唯一整行的 STARTED 绑成命名作业 ID。
    先过原字节门与步骤判据(调用点在 verdict OK 之后), 再发布绑定; 参数(n/lo/hi/dir)必须与
    计划里申报的一致 —— 否则"取消的是另一枚作业"这种错绑会在写之前被拒。"""
    spec = st.get("job_bind")
    if not spec:
        return
    name = spec["name"]
    row = spec.get("row", JOB_ROW_STARTED)
    hits = [m for m in (re.match(row, r) for r in _rows_of(txt)) if m]
    if len(hits) != 1:
        raise Stop("JOB_BIND_NOT_UNIQUE",
                   "as=%s name=%s STARTED 行命中=%d" % (asname, name, len(hits)))
    g = hits[0].groupdict()
    if "job_id" not in g:
        raise Stop("JOB_BIND_GROUP_MISSING", "as=%s name=%s 正则里没有组 job_id" % (asname, name))
    try:
        jid = int(g["job_id"])
    except ValueError:
        raise Stop("JOB_ID_UNPARSEABLE", "as=%s raw=%r" % (asname, g["job_id"]))
    if not JOB_ID_MIN <= jid <= JOB_ID_MAX:
        raise Stop("JOB_ID_OUT_OF_RANGE",
                   "as=%s id=%d 不在 %d..%d(0 或越界一律拒绝, 不默认 1)" %
                   (asname, jid, JOB_ID_MIN, JOB_ID_MAX))
    bad = {k: (g.get(k), v) for k, v in (spec.get("params") or {}).items()
           if str(g.get(k)) != str(v)}
    if bad:
        raise Stop("JOB_PARAMS_MISMATCH",
                   "as=%s name=%s 受理参数与计划申报不符 %s" % (asname, name, bad))
    if cmd:
        toks = str(cmd).split()
        sent = dict(zip(("n", "lo", "hi", "dir"), toks[2:6])) if len(toks) >= 6 else {}
        sc = {k: (sent.get(k), g.get(k)) for k in sent
              if k in ("n", "lo", "hi", "dir") and str(sent[k]) != str(g.get(k))}
        if sc:
            raise Stop("JOB_ACCEPTED_NOT_SENT",
                       "as=%s name=%s 实发 %s 与产品受理行不符 %s" % (asname, name, cmd, sc))
    if name in bindings and bindings[name]["job_id"] != jid:
        raise Stop("JOB_BIND_DUPLICATE",
                   "name=%s 已绑到 %s(来自 %s), 本步又要绑成 %d => 不覆盖" %
                   (name, bindings[name]["job_id"], bindings[name]["from_step"], jid))
    bindings[name] = {"job_id": jid, "from_step": asname, "row": hits[0].string.strip(),
                      "rx_sha256": sha_of(raw), "params": dict(g), "accepted": dict(g),
                      "counts": None}
    led.ev("JOB_ID_BOUND", name=name, job_id=jid, source_step=asname,
           accepted={k: g.get(k) for k in ("n", "lo", "hi", "dir")},
           rx_sha256=sha_of(raw), row=bindings[name]["row"])


def resolve_job_cmd(st, cmd, bindings, asname):
    """E2: 只接受固定参数位 `diag_profile learn_cancel @<绑定名>`; 其他任何形态都不生成。
    没有 eval/exec/format/shell 展开, 也不从日志、旧会话或 IDLE 状态里捞 ID。"""
    ref = st.get("job_cmd")
    toks = cmd.split()
    if ref is None:
        if any(JOB_REF_RE.match(t) for t in toks):
            raise Stop("JOB_REF_NOT_DECLARED",
                       "as=%s cmd=%s 里有 @引用但没有 job_cmd 声明, 不发送" % (asname, cmd))
        if cmd.split(" ")[0] == "diag_profile" and len(toks) > 1 and toks[1] == "learn_cancel":
            raise Stop("CANCEL_NEEDS_JOB_REF",
                       "as=%s cmd=%s 取消必须走 @绑定(不接受字面 ID, 避免把旧作业当本轮)" %
                       (asname, cmd))
        return cmd, None
    if tuple(toks[:2]) != CANCEL_PREFIX_TOKENS or len(toks) != 3 or not JOB_REF_RE.match(toks[2]):
        raise Stop("JOB_CMD_SHAPE",
                   "as=%s cmd=%s 不符合固定形式 `diag_profile learn_cancel @<绑定名>`" % (asname, cmd))
    name = JOB_REF_RE.match(toks[2]).group(1)
    if name != ref:
        raise Stop("JOB_CMD_NAME_MISMATCH",
                   "as=%s 位置引用 @%s 与步骤申报的 job_cmd=%s 不一致" % (asname, name, ref))
    if name not in bindings:
        raise Stop("JOB_UNBOUND",
                   "as=%s 绑定 %s 还没有来自 STARTED 的 ID => 一条命令都不发" % (asname, name))
    b = bindings[name]
    out = "%s %s %d" % (CANCEL_PREFIX_TOKENS[0], CANCEL_PREFIX_TOKENS[1], b["job_id"])
    return out, {"binding": name, "job_id": b["job_id"], "source_step": b["from_step"],
                "source_rx_sha256": b["rx_sha256"], "sent": out}


def check_job_expect(st, txt, bindings, asname):
    """E2: 同一作业的状态断言行(`id` 组)必须等于绑定的那枚 ID —— 禁止把旧 DONE/旧 FAILED 当本轮结果。"""
    for spec in st.get("expect_values", []):
        name = spec.get("job_binding")
        if not name:
            continue
        if name not in bindings:
            raise Stop("JOB_UNBOUND", "as=%s 状态断言引用未绑定的 %s" % (asname, name))
        hits = [m for m in (re.match(spec["row"], r) for r in _rows_of(txt)) if m]
        if len(hits) != 1:
            raise Stop("JOB_ASSERT_ROW_NOT_UNIQUE",
                       "as=%s 命中=%d row=%s" % (asname, len(hits), spec["row"]))
        g = hits[0].groupdict()
        if "id" not in g:
            raise Stop("JOB_ASSERT_GROUP_MISSING", "as=%s 行里没有组 id" % asname)
        if int(g["id"]) != bindings[name]["job_id"]:
            raise Stop("JOB_ID_MISMATCH",
                       "as=%s 行 id=%s 但本轮绑定=%s(来自 %s) => 不是同一枚作业" %
                       (asname, g["id"], bindings[name]["job_id"], bindings[name]["from_step"]))


def enforce_cancel_contract(st, txt, bindings, led, asname, verdict="OK"):
    """E1/E2: 取消只接受 `cancel id=<绑定ID> REQUESTED`。已终态(NOT-CANCELLED: already DONE)
    一律登记成"未捕获活动取消"并当场具名停止 —— allowed_outcomes 之类的事后豁免不能把它
    洗成取消成功。调用点在通用判据**之前**: 否则这条事实会被降级成 EXPECTATION_NOT_MET。
    verdict 非 OK 时不补 CANCEL_RESULT_UNPARSED(REFUSED/BUSINESS_FAILED 由业务层给准确身份)。"""
    if not st.get("job_cmd"):
        return
    name = st["job_cmd"]
    want = bindings[name]["job_id"]
    rows = _rows_of(txt)
    for row_kind, pat in CANCEL_ROWS:
        hits = [m for m in (re.match(pat, r) for r in rows) if m]
        if not hits:
            continue
        if len(hits) != 1:
            raise Stop("CANCEL_ROW_NOT_UNIQUE",
                       "as=%s %s 命中=%d" % (asname, row_kind, len(hits)))
        g = hits[0].groupdict()
        # 台账字段名不能叫 kind: Ledger.ev(kind, **kw) 的首个位置参数就叫 kind,
        # 传 kind=... 会当场 TypeError(本轮第一版正是这样炸在取消步上)。
        led.ev("CANCEL_RESULT", row=row_kind, id=int(g["id"]), st=g.get("st", ""),
               binding=name, expected_id=want)
        if row_kind == "ALREADY_TERMINAL":
            raise Stop("CANCEL_NOT_CAPTURED",
                       "as=%s id=%s 作业已是 %s(why=%s) => 未捕获活动取消, 不算取消成功, "
                       "也不自动再开一枚" % (asname, g["id"], g["st"], g.get("why", "")))
        if int(g["id"]) != want:
            raise Stop("CANCEL_ID_MISMATCH",
                       "as=%s 回包 id=%s 与本轮绑定 %s 不符" % (asname, g["id"], want))
        return
    if verdict != "OK":
        return
    raise Stop("CANCEL_RESULT_UNPARSED",
               "as=%s 取消响应里没有可判定的受理/未捕获行(REFUSED 线在业务层已先停)" % asname)


# ------------------------------------------------- F1(复审 P1~P5): 作业轮询先查合同, 再决定要不要等
# 状态名/归属/原因都来自产品源码, 不是本轮自造:
#   diag_relative.h:353-358  RUNNING / CANCEL_PENDING / DONE / FAILED / CANCELLED / TIMEOUT
#   diag_relative.h:365-367  REL_CAP_FREE=0 / BY_ZERO=1 / BY_JOB=2
#   diag_relative.c:1372 REL_JOB_WHY_RUN "RUNNING" / :1674 "CANCEL_REQUESTED"
#   diag_relative.c:1760  DONE 的 why 是 "COMPLETE"; :1690 终态把 why 落成传入值(CANCELLED 同路)
# cred 两位 = zero_cred/learn_cred(diag_relative.c:2042-2044 的 cred=%d/%d)。
JOB_CAP_ENUM = {"FREE": 0, "BY_ZERO": 1, "BY_JOB": 2}
# 产品固定行形(diag_relative.c:2042 主行 / :2050 进度行)。F1 要的是"固定格式解析",
# 所以这两条**写在执行器里**当基准, 计划里的 row 只允许与它们一致(由 r7f8f_plan_gates 核)。
JOB_ROW = (r"^\[DIAG\] job id=(?P<id>[0-9]+) st=(?P<st>[A-Z_]+) why=(?P<why>\S+) "
           r"owner=(?P<owner>[0-9]+) cred=(?P<cred>[0-9]/[0-9])$")
N_ROW = (r"^\[DIAG\] job n=(?P<adc>[0-9]+)/(?P<sg>[0-9]+) want=(?P<want>[0-9]+) "
         r"win=(?P<lo>[0-9]+)\.\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")
JOB_CONTRACT_TABLE = {
    "RUNNING": {"owner": "2", "cred": "1/0", "why": "RUNNING"},
    "CANCEL_PENDING": {"owner": "2", "cred": "1/0", "why": "CANCEL_REQUESTED"},
    "DONE": {"owner": "0", "cred": "1/1", "why": "COMPLETE"},
    "CANCELLED": {"owner": "0", "cred": "1/0", "why": "CANCELLED"},
}
JOB_TERMINAL_FAILURE = ("FAILED", "TIMEOUT")
JOB_PHASE_STATES = {
    "pre_cancel": ("RUNNING",),               # 取消之前出现 CANCEL_PENDING 就是矛盾
    "post_cancel": ("RUNNING", "CANCEL_PENDING", "CANCELLED"),
    "done": ("RUNNING", "CANCEL_PENDING", "DONE"),
}
JOB_DATA_ROW = (r"^\[DIAG\] job data adc\[(?P<amin>[0-9]+)\.\.(?P<amax>[0-9]+)\]n(?P<an>[0-9]+)"
                r" nz(?P<anz>[0-9]+) sg\[(?P<smin>[0-9]+)\.\.(?P<smax>[0-9]+)\]n(?P<sn>[0-9]+)"
                r" seq(?P<qf>[0-9]+)\.\.(?P<ql>[0-9]+)$")
JOB_THR_ROW = (r"^\[DIAG\] job thr tier=(?P<tier>[0-9]+) dev=(-?[0-9]+)/(-?[0-9]+)"
               r" drop=[0-9]+/[0-9]+ FIELD_UNVERIFIED$")
JOB_DONE_RESULT_ROW = (r"^\[DIAG\] job DONE result; cred zero=(?P<zero>[0-9]) learn=(?P<learn>[0-9])"
                       r" commit is separate$")


def _one_row(rows, pat, what):
    hits = [m for m in (re.match(pat, r) for r in rows if r) if m]
    if len(hits) != 1:
        return None, "%s_ROW_MISSING_or_DUPLICATE=%d" % (what, len(hits))
    return hits[0], ""


def job_contract(st, txt, bindings, led, asname, attempt=1):
    """F1: 每次查询都先把**这一行自己**的合同判完, 再决定"还在进行中 => 继续等"。

    修前的门只看 `st=` 在不在申报集合里, 于是 owner=FREE 的 RUNNING、want 与受理值不符、
    方向不符、计数倒退、DONE 没有候选输出(P1~P5)都被当成"还没到终态"等过去, 甚至被后面
    那条 DONE 洗绿。这里任何一项在**首次出现**就具名停止; 计数用无符号实值解析, 且
    **不**设 count<=N 的假上限(rel_cap_feed 不在单源达到 N 时停采, 较快源可以超过 N)。"""
    spec = st.get("job_contract")
    if not spec:
        # 合同**默认生效**: 只要这一步带着作业绑定(轮询的 job_binding / 状态断言的
        # expect_values[*].job_binding / 本步自己的 job_bind), 就先判完合同再决定等不等。
        # 计划里的 job_contract 只是额外申报 phase, 不是开关 —— 否则旧计划形状(复审的
        # P1~P5 都不申报)整条绕过, 这道门就只管新计划, 那正是任务书 §3.3 不许的做法。
        bn = (st.get("poll") or {}).get("job_binding")
        if not bn:
            for sp in st.get("expect_values") or []:
                if sp.get("job_binding"):
                    bn = sp["job_binding"]
                    break
        if not bn:
            bn = (st.get("job_bind") or {}).get("name")
        if not bn or bn not in bindings:
            return
        spec = {"binding": bn, "phase": "done"}
    name = spec["binding"]
    if name not in bindings:
        raise Stop("JOB_UNBOUND", "as=%s 作业合同引用未绑定的 %s" % (asname, name))
    b = bindings[name]
    rows = _rows_of(txt)
    main, why = _one_row(rows, JOB_ROW, "job id")
    if why:
        raise Stop("JOB_CONTRACT_ROW_MISSING", "as=%s %s(第 %d 次查询)" % (asname, why, attempt))
    g = main.groupdict()
    nrow, why = _one_row(rows, N_ROW, "job n")
    if why:
        raise Stop("JOB_CONTRACT_ROW_MISSING", "as=%s %s(第 %d 次查询)" % (asname, why, attempt))
    ng = nrow.groupdict()
    bad = []
    if int(g["id"]) != b["job_id"]:
        raise Stop("JOB_ID_MISMATCH", "as=%s 行 id=%s 但本轮绑定=%s(来自 %s)" %
                   (asname, g["id"], b["job_id"], b["from_step"]))
    stt = g["st"]
    if stt in JOB_TERMINAL_FAILURE:
        raise Stop("JOB_STATE_TERMINAL_FAILURE", json.dumps(
            {"as": asname, "binding": name, "job_id": b["job_id"], "st": stt,
             "why": g["why"], "attempt": attempt}, ensure_ascii=False))
    if stt not in JOB_CONTRACT_TABLE:
        raise Stop("JOB_STATE_UNKNOWN", "as=%s st=%s 不在产品状态集合 %s" %
                   (asname, stt, sorted(JOB_CONTRACT_TABLE) + list(JOB_TERMINAL_FAILURE)))
    want = JOB_CONTRACT_TABLE[stt]
    for k in ("owner", "cred", "why"):
        if g[k] != want[k]:
            bad.append("%s=%s(%s 要求 %s)" % (k, g[k], stt, want[k]))
    phase = spec.get("phase", "done")
    allowed = JOB_PHASE_STATES.get(phase)
    if allowed is None:
        raise Stop("JOB_PHASE_UNKNOWN", "as=%s phase=%s" % (asname, phase))
    if stt not in allowed:
        bad.append("phase=%s 不允许状态 %s(允许 %s)" %
                   (phase, stt, "/".join(allowed)))
    acc = b.get("accepted") or {}
    for k, col in (("n", "want"), ("lo", "lo"), ("hi", "hi"), ("dir", "dir")):
        if k in acc and str(acc[k]) != ng[col]:
            bad.append("%s=%s 与本作业**受理行**的 %s=%s 不符" % (col, ng[col], k, acc[k]))
    prev = b.get("counts")
    if prev is not None:
        for k, col in (("adc", "adc"), ("sg", "sg")):
            if int(ng[col]) < int(prev[col]):
                bad.append("%s 计数从 %s 倒退到 %s(同一作业不可倒退)" % (col, prev[col], ng[col]))
    b["counts"] = dict(ng)
    led.ev("JOB_CONTRACT", **{"as": asname, "binding": name, "attempt": attempt, "st": stt,
                              "owner": g["owner"], "cred": g["cred"], "why": g["why"],
                              "adc": ng["adc"], "sg": ng["sg"], "want": ng["want"],
                              "phase": phase, "bad": bad})
    if bad:
        raise Stop("JOB_CONTRACT_VIOLATED", json.dumps(
            {"as": asname, "binding": name, "job_id": b["job_id"], "st": stt,
             "attempt": attempt, "bad": bad}, ensure_ascii=False))
    if stt == "DONE":
        # 两行状态就宣告"可提交"是不够的: 产品只在 state==DONE 且 cand_valid 时才打
        # job thr/job DONE result(diag_relative.c:2068-2076), job data 要 has_data(:2056)。
        d, why = _one_row(rows, JOB_DATA_ROW, "job data")
        t, why2 = _one_row(rows, JOB_THR_ROW, "job thr")
        r, why3 = _one_row(rows, JOB_DONE_RESULT_ROW, "job DONE result")
        missing = [x for x in (why, why2, why3) if x]
        if missing:
            raise Stop("JOB_DONE_CANDIDATE_EVIDENCE_MISSING", json.dumps(
                {"as": asname, "binding": name, "attempt": attempt, "missing": missing,
                 "basis": "diag_relative.c:2056/2068/2074"}, ensure_ascii=False))
        dg = d.groupdict()
        cbad = []
        if int(dg["an"]) != int(ng["adc"]) or int(dg["sn"]) != int(ng["sg"]):
            cbad.append("候选统计 adc/sg=%s/%s 与进度行 n=%s/%s 不等" %
                        (dg["an"], dg["sn"], ng["adc"], ng["sg"]))
        w = int(ng["want"])
        if int(ng["adc"]) < w or int(ng["sg"]) < w:
            cbad.append("DONE 但某一路计数 %s/%s 低于受理的 want=%s" %
                        (ng["adc"], ng["sg"], ng["want"]))
        if int(dg["amin"]) > int(dg["amax"]) or int(dg["smin"]) > int(dg["smax"]):
            cbad.append("统计范围倒挂 adc[%s..%s] sg[%s..%s]" %
                        (dg["amin"], dg["amax"], dg["smin"], dg["smax"]))
        if int(dg["qf"]) > int(dg["ql"]):
            cbad.append("SG 新样本序号倒挂 seq%s..%s" % (dg["qf"], dg["ql"]))
        if int(dg["sn"]) <= 0:
            cbad.append("SG 序号区间存在但样本数=0(把同一枚旧样本当新样本)")
        rg = r.groupdict()
        if (rg["zero"], rg["learn"]) != (g["cred"].split("/")[0], g["cred"].split("/")[1]):
            cbad.append("job DONE result 的 cred=%s/%s 与主行 cred=%s 不符" %
                        (rg["zero"], rg["learn"], g["cred"]))
        if cbad:
            raise Stop("JOB_DONE_CANDIDATE_INCONSISTENT", json.dumps(
                {"as": asname, "binding": name, "attempt": attempt, "bad": cbad},
                ensure_ascii=False))
        led.ev("JOB_DONE_EVIDENCE", **{"as": asname, "binding": name, "adc_n": dg["an"],
                                       "sg_n": dg["sn"], "sg_seq": "%s..%s" % (dg["qf"], dg["ql"]),
                                       "tier": t.group("tier"), "cred": g["cred"]})


def poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture, silent,
                      led, clock, budget, results, bindings=None):
    """起转后的**有限轮询**(审核 §2D 第 3 条): 一条立即查询或 tgt>0 都不算稳定巡航。
    只在申报的 retry_codes 里重试(ACCEL 不是异常 => 同一预算内继续等), 窗口到期或不一致就
    具名停止并交由同一条清理路径撤能。"""
    p = st["poll"]
    window = float(p.get("until_seconds", 3))
    interval = float(p.get("interval_seconds", 0.5))
    codes = tuple(p.get("retry_codes", ["PROFILE_NOT_RUNNING"]))
    gate, in_row, in_states = poll_gate_of(p)
    max_attempts = p.get("max_attempts")
    bindings = bindings if bindings is not None else {}
    until = min(clock.monotonic() + window, step_dl)
    n = 0
    while True:
        n += 1
        raw = out / ("%02d_%s.poll%02d.rx.bin" % (idx, asname, n))
        v, nbytes, txt = command_attempt(port, cmd, st, "%s#%d" % (asname, n), raw, until,
                                         transport, fixture, silent, led, clock, attempt=n)
        results.append(dict({"as": asname, "kind": "command_poll", "cmd": cmd, "attempt": n,
                             "poll_gate": gate,
                             "rx_bytes": nbytes, "rx_sha256": sha_of(raw), "txt": txt}, **v))
        # F1: 合同排在"要不要继续等"之前。响应里只要有 [DIAG] job id= 行就判完; 一条业务拒绝行
        # (learn_status REFUSED: ...)根本没这行, 交给下面的 verdict 门给准确身份, 不在这伪装成缺行。
        if v["verdict"] == "OK" or any(r.startswith("[DIAG] job id=") for r in _rows_of(txt) if r):
            job_contract(st, txt, bindings, led, asname, n)
        if v["verdict"] == "OK":
            check_job_expect(st, txt, bindings, asname)
            led.ev("POLL_OK", **{"as": asname, "attempts": n, "poll_gate": gate,
                                 "elapsed_s": round(clock.monotonic() - led.t0, 3)})
            return v, txt
        state, groups = poll_observe(gate, in_row, in_states, txt)
        jbn = p.get("job_binding")
        if jbn and groups.get("id") is not None:
            if jbn not in bindings:
                raise Stop("JOB_UNBOUND", "as=%s poll 引用未绑定的 %s" % (asname, jbn))
            if int(groups["id"]) != bindings[jbn]["job_id"]:
                # 首份坏响应就停: 别人家的作业行不能等成"本轮还没到 DONE"。
                raise Stop("POLL_JOB_MISMATCH", json.dumps(
                    {"as": asname, "row_id": groups["id"],
                     "bound_id": bindings[jbn]["job_id"],
                     "bound_from": bindings[jbn]["from_step"], "attempt": n},
                    ensure_ascii=False))
        if not (v["verdict"] == "STOP" and v.get("code") in codes and state is not None):
            raise Stop(v["code"], json.dumps(dict(v, poll_gate=gate, observed=state),
                                              ensure_ascii=False))
        if max_attempts and n >= int(max_attempts):
            # E1: 上限同时受次数与时间窗约束; 次数到是另一种结束, 不借口"窗口还没到"继续发。
            raise Stop("POLL_ATTEMPTS_EXHAUSTED", json.dumps(
                {"as": asname, "attempts": n, "max_attempts": int(max_attempts),
                 "last": v.get("code"), "state": state, "poll_gate": gate},
                ensure_ascii=False))
        # G1/G2: 轮询的"最早硬截止"改走 past_business_hard_deadline() —— 与修前逐值等价
        # (cap 项 + 收口前业务项), 但零速确认后 cap 项退出; 且**每份尝试共用同一个 until**,
        # 不随次数刷新(裁定二第 1/5 条)。
        if clock.monotonic() + interval >= until or budget.past_business_hard_deadline():
            raise Stop("POLL_WINDOW_EXPIRED", json.dumps(
                {"as": asname, "attempts": n, "last": v.get("code"), "state": state,
                 "poll_gate": gate, "in_progress_states": list(in_states or []),
                 "bad": v.get("bad", []), "window_s": window}, ensure_ascii=False))
        led.ev("POLL_RETRY", **{"as": asname, "attempt": n, "code": v.get("code"),
                                "state": state, "poll_gate": gate, "waiting_s": interval,
                                "rx_sha256": sha_of(raw)})
        clock.sleep(interval)


def arm_attempt_gate(energize, led, clock, asname, cmd):
    """F2+F3: 在 arm 的**写入尝试之前**建立通电计时义务, 并在这一步就拒绝第二次 arm。

    修前的顺序是"先按申报秒数算 step_dl, 再在写之前 start_attempt", 于是 arm 那条命令自己的
    首次回读/排残留/等待都没被新成立的截止约束(复审 P6: cap 2 s 而回包阻塞 3 s 照样 OK)。
    现在义务先立, 截止后算, 且 disarm 之后再来一条 arm 也在写之前拒(复审 P7)。"""
    if cmd == "motor_arm":
        if energize.cap is None:
            raise Stop("ARM_ENERGIZE_DEADLINE_UNDECLARED",
                       "as=%s 阶段没申报 arm_energize_deadline_seconds => 不发 motor_arm" % asname)
        if energize.arm_attempts >= 1:
            raise Stop("ARM_SECOND_ATTEMPT_REFUSED", json.dumps(
                {"as": asname, "attempts_so_far": energize.arm_attempts,
                 "first_from_t0_s": round(clock.monotonic() - energize.t0, 3)
                 if energize.t0 is not None else None,
                 "closed": energize.actual is not None,
                 "note": "本场只允许一次 arm(任务书 §2 F3); 第二次在写入尝试之前拒"},
                ensure_ascii=False))
        energize.start_attempt()
        led.ev("ENERGIZE_OBLIGATION_SET", cap_seconds=energize.cap,
               t_s=round(clock.monotonic() - led.t0, 3), before="step_deadline")


def exec_session(session_id, steps, outdir, transport, fixture, port_name,
                 total_deadline_seconds, clock, backend=None, motion_cap_seconds=None,
                 plan=None, quota_box=None, used_box=None, context=None, approval=None,
                 approved=None, energize_cap_seconds=None, cleanup_reserve_seconds=None):
    """一个会话 = 一个端口 = 一个写者。

    与 R1 交付版的三处实质差别:
      1) 任何一步判据 STOP/REFUSED/NO_TRIGGER 都**结束业务循环**, 然后统一清理;
         R1 只在 command 步这么办, watch 步 STOP 之后还继续 motor_arm/fault_reset。
      2) motion_cap_seconds 从 motor_start 的发送尝试起算, 之后所有业务动作受剩余预算
         约束; R1 的 seconds=5 只是那一条响应的 deadline。
      3) finally 不再"只关端口": 先 stop+disarm 并核字段, 未证禁能 => 终态 UNKNOWN。

    M8-R6A R2(复审 §4)再加两条:
      4) `transport == "real"` 只接受**经批准的整包**(`approved` 由 dispatch 当场构造):
         任意单会话步骤一律在开口之前具名拒绝; 给了 `approved` 也要两步核对通过才发第一条命令。
      5) 落在 QUOTA_COMMAND_KEYS 上的命令必须有**对应键、n=1** 的额度申报:
         缺声明、错键、n 不符都在那条 write 之前拒绝(去掉字段逃不了账)。
    """
    viol = plan_motion_window_violations(steps)
    if viol:
        raise Stop("PLAN_MANUAL_AFTER_MOTION", ",".join(viol))   # 开口之前就拒绝
    if transport == "real" and approved is None:
        # 这一步必须在任何端口/工具/账本动作之前: 拒绝的副作用清单要真的是空。
        raise Stop("REAL_SESSION_REQUIRES_APPROVED_PACKAGE",
                   "会话 %s: 真实传输只允许经批准的整包 dispatch 进入(复审 §4); "
                   "任意 --steps 不接, 离线工装请用 transport=stub" % session_id)
    if transport == "real" and not plan:
        # 没有计划就没有额度来源: spend() 会整条跳过, 那正是"绕账"的另一半。
        raise Stop("REAL_SESSION_REQUIRES_PLAN", "真实会话必须带 plan(quota 的唯一定义处)")
    binding = verify_approved_binding(approved, steps, transport) if approved is not None else None
    out = Path(outdir) / session_id
    out.mkdir(parents=True, exist_ok=True)
    led = Ledger(out / "ledger.jsonl", clock, out_dir=out)
    if backend is not None:
        backend.led = led            # 动作事件与串口事件同一条台账: 调用序列不能散在两个文件里
    if binding:
        led.ev("APPROVED_PACKAGE_VERIFIED", **binding)
    results = []
    errors = []
    primary = None
    disarm_state = "NOT_ARMED"
    obligation = False
    cleanup_overruns = []
    reservations = []
    start_attempts = 0
    stop_confirmed = False         # G1: 业务步里"已发送且判 OK 的正常 motor_stop"(零速确认的前提之一)
    irq_attempted = False          # F5: 只记"是否**尝试过** safety_irq_enable", 决定固定尾部走不走
    used = used_box if used_box is not None else {}
    # 这本账在开口之前就要能用: "试了但没开成"也必须留痕, 所以 spend 定义在 try 之前。
    quota = quota_box if quota_box is not None else (dict(plan["quota"]) if plan else {})

    def spend(name, n):
        """额度是**执行时**扣的, 不是声明的: 超了就具名停止, 且不发送那条命令。

        给了 approval(批准身份绑定的持久账)时, 扣减先落到那本账上再发送 —— 换一个输出目录、
        重开一个进程都不会把一次批准的边界清零(R5-C 的 26 场就是这么把 apply 用到 14/2)。"""
        if not plan:
            return None
        if not isinstance(name, str) or not name:
            raise Stop("QUOTA_UNDECLINED", repr(name)[:40])
        if not is_count(n) or n < 1:
            # 复审 §4: 扣减数本身要合法。旧版把 n 原样交给 int()/内存账, 于是 n=0 是"免费一次",
            # n=-1 甚至把剩余额度抬高。持久账那边同样拒(APPROVAL_RESERVE_N_INVALID), 这里补的是
            # 没有批准账的那条路。
            raise Stop("QUOTA_N_INVALID", "%s n=%r(%s)" % (name, n, type(n).__name__))
        if name not in quota:
            raise Stop("QUOTA_UNDECLINED", name)      # 计划里没有的动作一律禁止
        if approval is not None:
            if quota[name] < n:
                raise Stop("QUOTA_EXHAUSTED", "in-process %s want=%d left=%d"
                           % (name, n, quota[name]))
            kind = "cleanup" if name.startswith("cleanup_") else "business"
            res = approval.reserve(name, n, kind=kind)
            reservations.append(res)
            quota[name] -= n
            used[name] = used.get(name, 0) + n
            led.ev("QUOTA_CONSUMED", name=name, n=n, left=quota[name], approval_kind=kind,
                   approval_used=approval.snapshot()["spent"].get(name))
            return res
        if quota[name] < n:
            raise Stop("QUOTA_EXHAUSTED", "%s want=%d left=%d" % (name, n, quota[name]))
        quota[name] -= n
        used[name] = used.get(name, 0) + n
        led.ev("QUOTA_CONSUMED", name=name, n=n, left=quota[name])
        return None

    # F2: 会话绝对截止 = 台账 t0 + 总时限。修前每步用"当前时刻 + 总时限", 相当于无上限续期。
    session_abs_dl = clock.monotonic() + float(total_deadline_seconds)
    bindings = {}
    energize = EnergizeBudget(clock, energize_cap_seconds)
    # F5b②: 收口预留取**申报值**(没申报就用本轮常量 7 s)。类型在这里就判, 和
    # PLAN_MANUAL_AFTER_MOTION 同一层: 畸形申报在台账/端口/账本之前拒, 一条命令都不发。
    reserve_declared = CLEANUP_RESERVE_SECONDS if cleanup_reserve_seconds is None \
        else cleanup_reserve_seconds
    if isinstance(reserve_declared, bool) or \
            not isinstance(reserve_declared, (int, float)):
        raise Stop("CLEANUP_RESERVE_NOT_A_NUMBER",
                   "cleanup_reserve_seconds=%r(%s)" % (reserve_declared,
                                                       type(reserve_declared).__name__))
    budget = MotionBudget(clock, motion_cap_seconds,
                          reserve=reserve_declared) if motion_cap_seconds else \
        MotionBudget(clock, total_deadline_seconds, reserve=reserve_declared)
    # F5b②: 收口仍然被会话**绝对**截止封顶(修前 cleanup 只认 t0+cap+reserve, 总时限管不到它)。
    budget.session_abs_dl = session_abs_dl
    # F5c: 本会话**可能**建立收口义务(步骤里出现 motor_arm/motor_start/safety_irq_enable 任一)时,
    # 业务绝对截止提前到"总截止 - 收口预留"。这条判据只看获批步骤里有没有这三条命令, 不看运行
    # 结果: 义务还没建立也得先把撤能窗口留在预算外, 否则"能不能留出 7 秒"取决于前面跑顺不顺。
    OBLIGATION_COMMANDS = ("motor_arm", "motor_start", "safety_irq_enable")
    may_obligate = any(str(s.get("cmd", "")).split(" ")[0] in OBLIGATION_COMMANDS
                       for s in steps if isinstance(s, dict))
    business_abs_dl = (session_abs_dl - float(reserve_declared)) if may_obligate else session_abs_dl
    budget.business_dl = business_abs_dl if may_obligate else None
    silent = bool(fixture.get("inject", {}).get("port_silent"))
    port = None
    ret = None
    try:
        # F5b②: 预留必须容得下**全部**必要发送各自的确认窗(3 条电机 + 2 条 IRQ 尾部 = 5 条
        # × 1.2 s = 6.0 s)。修前的 4 s 常量正是这样静默吃掉第五条 safety_irq_status 的;
        # 现在申报排不下就在第一条命令之前具名拒绝 —— 端口、额度、批准账三处仍然零副作用,
        # 但这次拒绝本身进台账(可审计的"没发生", 而不是事后靠 stdout 抓一行)。
        reserve_floor = (len(CLEANUP_CMDS) + len(IRQ_TAIL_CMDS)) * CLEANUP_WINDOW_SECONDS
        if float(reserve_declared) + 1e-9 < reserve_floor:
            raise Stop("CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS", json.dumps(
                {"reserve_seconds": float(reserve_declared),
                 "required_seconds": round(reserve_floor, 3),
                 "send_chances": len(CLEANUP_CMDS) + len(IRQ_TAIL_CMDS),
                 "window_seconds_each": CLEANUP_WINDOW_SECONDS,
                 "checked_before": "port_open_and_quota"}, ensure_ascii=False))
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
            if energize.expired():
                # 通电截止到了: 连这一步都不尝试(不是"发完再补"), 清理仍由 finally 走。
                raise Stop("ARM_ENERGIZE_DEADLINE_EXCEEDED", json.dumps(
                    {"as": asname, "cap_seconds": energize.cap,
                     "elapsed_s": round(clock.monotonic() - energize.t0, 3),
                     "checked_before_write": True}, ensure_ascii=False))
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
            cmd, job_prov = resolve_job_cmd(st, cmd, bindings, asname)
            if job_prov:
                # 派生命令只进台账与实际发送, 不回写获批的 plan(批准绑定的是计划字节+生成规则)。
                led.ev("JOB_CMD_RESOLVED", **job_prov)
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
            if cmd.split(" ")[0] == "safety_irq_enable" and not irq_attempted:
                # F5: 尾部由"是否尝试过开 IRQ"决定, 在写之前置位 => 连写失败也要收口。
                irq_attempted = True
                led.ev("IRQ_ENABLE_ATTEMPTED", **{"as": asname})
            # ---- R6A §4: 命令 -> 额度键的交叉核对, 缺声明/错键/n 不符都在这条 write 之前拒 ----
            need_key = quota_key_for_command(cmd)
            declared = st.get("quota")
            if declared is not None:
                if not isinstance(declared, dict) or "name" not in declared:
                    raise Stop("QUOTA_STEP_SHAPE", "as=%s quota=%r" % (asname, declared))
                qn, qnum = declared["name"], declared.get("n", 1)
                if need_key and qn != need_key:
                    raise Stop("QUOTA_KEY_MISMATCH",
                               "as=%s cmd=%s 属额度键 %s, 步骤申报的是 %s" %
                               (asname, cmd, need_key, qn))
                if need_key and (not is_count(qnum) or qnum != 1):
                    raise Stop("QUOTA_N_MISMATCH",
                               "as=%s cmd=%s 一条命令一次动作, 申报 n=%r" % (asname, cmd, qnum))
                spend(qn, qnum)
            elif need_key:
                raise Stop("QUOTA_STEP_UNDECLARED",
                           "as=%s cmd=%s 会改变设备/持久状态, 必须申报额度键 %s(n=1) 才允许发送"
                           % (asname, cmd, need_key))
            # ---- F2: 义务先立, 截止后算; 会话截止是**绝对**的 t0+总时限, 不逐步续期 ----
            arm_attempt_gate(energize, led, clock, asname, cmd)
            want = int(st.get("seconds", 3))
            # G2(裁定二): 单步窗口由 step_window() 给出 —— 申报了 step_total_seconds 就是那个
            # 值(含全部等待与余量, **不再加 SLACK**, 也不 int 截断); 没申报走 legacy。
            problem = step_total_problem(st)
            if problem:
                # 非法值在**开口与写入之前**具名拒绝(不等到超时才红)。
                raise Stop("STEP_TOTAL_SECONDS_INVALID", json.dumps(
                    {"as": asname, "cmd": cmd, "index": idx, "problem": problem,
                     "declared": st.get(STEP_TOTAL_KEY)}, ensure_ascii=False))
            window, window_src = step_window(st)
            # F5c: 这一步(以及它下面的读/轮询/等待)受**业务**截止约束, 不是会话总截止 ——
            # 差额就是留给五条收口的预留。may_obligate=False 的只读场两者相同。
            step_dl = min(clock.monotonic() + window, business_abs_dl)
            if cmd == "__watch__" or budget.armed:
                step_dl = min(step_dl, budget.business_deadline_window(window))
            st_window_gate = dict(window_seconds=round(window, 3), window_from=window_src)
            if energize.armed:
                step_dl = min(step_dl, energize.deadline())
                if energize.expired():
                    raise Stop("ARM_ENERGIZE_DEADLINE_EXCEEDED", json.dumps(
                        {"as": asname, "want_seconds": want, "cap_seconds": energize.cap,
                         "elapsed_s": round(clock.monotonic() - energize.t0, 3),
                         "checked_before_write": True}, ensure_ascii=False))
            if step_dl <= clock.monotonic():
                raise Stop("STEP_DEADLINE_BEFORE_WRITE", json.dumps(
                    {"as": asname, "want_seconds": want,
                     # G2: 首错里同时给"这一步实际用的总窗与它的来源", 便于核对检查器与执行器
                     # 用的是不是同一个值(裁定二明令不许两边不一致)。
                     "step_window_seconds": st_window_gate["window_seconds"],
                     "window_from": st_window_gate["window_from"],
                     "remaining_s": round(step_dl - clock.monotonic(), 3),
                     "session_remaining_s": round(session_abs_dl - clock.monotonic(), 3),
                     # F5c: 首错细节里同时给"会话还剩多少"和"收口前业务还剩多少", 让人一眼看出
                     # 这条是被预留挡住的, 不是总截止到了才顺手红。
                     "business_remaining_s": round(business_abs_dl - clock.monotonic(), 3),
                     "cleanup_reserve_seconds": float(reserve_declared),
                     "cleanup_reserved": bool(may_obligate),
                     "energize_remaining_s": energize.remaining()}, ensure_ascii=False))
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
                    if e.code in STREAM_BYTE_DEFECTS:
                        # R6A §5: 观察窗里的裸 NUL/非法字节/危险文本**不被吞成正常 NO_TRIGGER**。
                        # 原字节已经逐块落盘(下面 raise 之前不做任何改写), 由会话层转成具名 STOP。
                        raise
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
                    # F3: 本场只允许一次 start。计数在**写之前**加, 预算起点也在那一刻。
                    if start_attempts >= 1:
                        raise Stop("MOTOR_START_SECOND_REFUSED",
                                   "as=%s 本场已尝试过 %d 次 motor_start" % (asname, start_attempts))
                    budget.start_attempt()
                # F2/F3: arm 的计时义务与第二次拒绝都在 arm_attempt_gate() 里(算 step_dl 之前),
                # 这里只留"起转"那一支的运动预算起点。
                if cmd == "motor_start":
                    start_attempts += 1
                led.ev("OBLIGATION_SET", cmd=cmd, t_s=round(clock.monotonic() - led.t0, 3),
                       cap_seconds=budget.cap)
            if st.get("poll"):
                # 有限轮询取证(只在申报的重试码内等): 到期/不一致 => 撤能由 finally 统一走
                _polled, polled_txt = poll_command_step(
                    port, cmd, st, asname, idx, out, step_dl, transport, fixture,
                    silent, led, clock, budget, results, bindings)
                # G1: 轮询到那份**新**零速快照之后同样走释放门(证据不齐就抛具名 STOP)。
                motion_end_gate(budget, st, cmd, polled_txt, start_attempts, stop_confirmed,
                                asname, led, clock)
                continue
            v, nbytes, txt = command_attempt(port, cmd, st, asname, raw, step_dl, transport,
                                             fixture, silent, led, clock)
            results.append(dict({"as": asname, "kind": "command", "cmd": cmd,
                                 "rx_bytes": nbytes, "rx_sha256": sha_of(raw),
                                 "txt": txt, "selected_log_window": selected_window}, **v))
            if st.get("job_cmd"):
                # 取消合同排在**通用判据之前**: "已终态"必须登记成未捕获活动取消(E1),
                # 不能被 expect_contains 的 EXPECTATION_NOT_MET 掩盖掉身份; 而业务失败线
                # (REFUSED/FAILED)仍由下面的 verdict 门给出准确码。
                enforce_cancel_contract(st, txt, bindings, led, asname, v["verdict"])
            if v["verdict"] == "BYTE_CORRUPT":
                # R6 §5.3: 本版执行器**不产生**这个判决(字节损坏一律停)。真出现了就说明有人又把
                # "损坏后由后面某步重确认"接回运行时 —— 具名拒绝, 不让它作为可继续状态泄漏。
                raise Stop("RETIRED_RECOVERY_CONTRACT",
                           "as=%s 产生了 BYTE_CORRUPT 判决, 但恢复合同已退役" % asname)
            if v["verdict"] == "REFUSED":
                ret = {"status": "REFUSED", "code": v.get("code", ""),
                       "refused": v.get("lines")}
                break
            if v["verdict"] != "OK":
                raise Stop(v["code"], json.dumps(v, ensure_ascii=False))
            # 到这里这条命令是**判据通过**的: 才允许绑定 ID、核对同作业断言, 也才允许认定撤能成立。
            bind_job_id(st, txt, raw, bindings, led, asname, cmd)
            check_job_expect(st, txt, bindings, asname)
            if cmd == "motor_stop":
                # G1: stop 判 OK 只证"已请求且产品接单", 它**不**结束运动计时;
                # 但它是随后那份零速快照的前提之一(裁定一第 1 条第 1 小项)。
                stop_confirmed = True
                led.ev("MOTOR_STOP_CONFIRMED", **{"as": asname, "ends_motion_timer": False})
            motion_end_gate(budget, st, cmd, txt, start_attempts, stop_confirmed, asname,
                            led, clock)
            if st.get("job_contract") and not st.get("poll"):
                job_contract(st, txt, bindings, led, asname)
            if cmd == "motor_disarm" and energize.armed:
                # F3: 这条命令判 OK 之后还只算"动作已做且许可路 verified" => 把义务**转成待快照确认**,
                # 关闭要靠相邻那份**新的** motor_status 禁能合同(清理里那份同样可以)。
                okd, whyd = disarm_success_evidence(_rows_of(txt))
                if not okd:
                    raise Stop("DISARM_SUCCESS_ROW_UNPROVEN",
                               "as=%s %s => 不解除通电义务" % (asname, whyd))
                energize.await_disable_snapshot = True
                led.ev("DISARM_ACTION_DONE", **{"as": asname, "await": "motor_status"})
            if cmd == "motor_status" and energize.await_disable_snapshot:
                summ2, bad2 = disable_contract(_rows_of(txt))
                if bad2:
                    led.ev("DISARM_SNAPSHOT_NOT_PROVEN", **{"as": asname, "bad": bad2})
                else:
                    led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                           cap_seconds=energize.cap, basis="motor_status_step")
                    energize.await_disable_snapshot = False
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
        # F5b①(A 的 stub 复现第一项): 任务书 §2 要的是"**尝试过**开 IRQ 本身就建立尾部清理义务",
        # 与电机义务无关。真实序列正是先 safety_irq_enable 再 motor_arm, 中间任何一步失败(查询
        # 失败/判据红/截止)都落在 `obligation` 还是 False 的窗口里 => 修前一条 disable/status
        # 都不发, IRQ 门留在 OPEN。电机那三条仍按 obligation 走(见 cleanup 的 motor_obligation)。
        if obligation or irq_attempted:    # 潜在已武装**或**已尝试开 IRQ => 都要收口
            hook = _stub_feed(port, fixture) if transport == "stub" else None
            try:
                disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture,
                                             hook, quota if plan else None,
                                             cleanup_overruns, used,
                                             approval=approval, reservations=reservations,
                                             energize=energize, irq_attempted=irq_attempted,
                                             motor_obligation=obligation)
                errors.extend(cerr)          # 清理错独立留账: 主错在 ret, 不能被吞成空成功
            except Exception as e:
                # 清理框架自己炸了: 禁能一律 UNKNOWN(不猜"应该已经撤了"), 但端口仍要关
                disarm_state = "UNKNOWN"
                errors.append("CLEANUP_FRAME_RAISED_%s:%s" % (e.__class__.__name__, repr(e)))
                led.ev("CLEANUP_FRAME_EXCEPTION", err=repr(e))
        # 会话结束才把每条预占**确认消耗**(摘 pending + 留痕)。这里绝不做反方向的事:
        # 中途 STOP / 被拒绝 / 业务失败都照样确认, 额度一条都不退 —— 崩溃走不到这一行,
        # 未确认的预占就一直挂在 pending 上并按已消耗计入, 下一次动作看得见。
        try:
            if approval is not None:
                ok_now = (ret or {}).get("status") in ("OK", "OBSERVATION_NO_TRIGGER")
                for res in list(reservations):
                    try:
                        approval.commit(res, ok=ok_now)
                    except Stop as e:
                        errors.append("APPROVAL_COMMIT_REFUSED_%s" % e.code)
                        try:
                            led.ev("APPROVAL_COMMIT_FAILED", tag=res["tag"], code=e.code,
                                   detail=e.detail)
                        except Exception as e2:
                            # 留痕自己炸了也只多记一条错, 不吃掉后面的确认与关闭
                            errors.append("APPROVAL_COMMIT_EVIDENCE_%s" % e2.__class__.__name__)
                    except Exception as e:
                        # R6B §4.2: 上一版只捕 Stop, 于是坏账引发的 TypeError 直接越过下面的
                        # port.close()/led.close()(A 的 pending-name-list 探针 closed=False)。
                        errors.append("APPROVAL_COMMIT_FRAME_ERROR_%s" % e.__class__.__name__)
                del reservations[:]
        except Exception as e:
            errors.append("APPROVAL_FINALIZATION_FRAME_ERROR_%s:%s"
                          % (e.__class__.__name__, repr(e)))
        finally:
            # R6B §4.3: 账本收尾(确认消耗/存盘)的**任何**错都不许跳过端口与日志的关闭。
            # 关端口只是交还句柄, **不等于**证得禁能 —— 禁能只由上面清理拿到的新 status 决定。
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
                # F5b: 回执里带上**这一场实际生效**的收口预留与是否被会话截止夹过, 让 7 s
                # 不只是文档里的字: 现场判读能直接看到申报值与绝对截止的差。
                "cleanup_reserve_seconds": budget.reserve,
                "cleanup_hard_deadline": round(budget.cleanup_deadline(), 6),
                "session_abs_deadline": round(session_abs_dl, 6),
                "business_abs_deadline": round(business_abs_dl, 6),
                "cleanup_reserved": bool(may_obligate),
                "energize_cap_seconds": energize.cap,
                "energize_seconds_actual": energize.close(),
                "job_bindings": {k: v["job_id"] for k, v in bindings.items()},
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
            q = st.get("quota") or {}
            key = quota_key_for_command(st.get("cmd"))
            if key:
                # R6A §4: 命令在映射表上 => **无论有没有申报**都算一次消耗(没申报运行期直接拒),
                # 这样 PLAN_QUOTA_NEED 的"申报==推导"才不会被"删掉字段"糊过去。
                bump(key, int(q.get("n", 1)) if is_count(q.get("n", 1)) and q.get("n", 1) > 0
                     else 1)
            elif q:
                bump(q["name"], int(q["n"]))
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


R7F8E_JOB_ROW_GROUP = "st"
R7F8E_CANCEL_CMD = "diag_profile learn_cancel"


def job_state_names_from_source(app_dir):
    """合法作业状态名只认产品源码 rel_job_state_name 的 case 表(注释与文档不算依据)。"""
    p = Path(str(app_dir)) / "diag_relative.c"
    src = p.read_text(encoding="utf-8", errors="replace")
    if "rel_job_state_name" not in src:
        raise Stop("R7F8E_JOB_STATE_SOURCE_MISSING", str(p))
    body = src.split("rel_job_state_name", 1)[1]
    body = body[:body.index("\n}")] if "\n}" in body else body
    got = sorted(set(re.findall(r'return "([A-Z_]+)";', body)))
    if not got:
        raise Stop("R7F8E_JOB_STATE_SOURCE_EMPTY", str(p))
    return got


def validate_r7f8e(plan, rows_doc, app_dir):
    """E1~E4 的离线门: 全部是"结构上就发不出去/等不到/记不上账"的拒绝, 不判定任何设备结论。"""
    out, fails = [], []

    def gate(name, sid, ok, detail):
        out.append("%s|%s|%s|%s" % (name, sid, "PASS" if ok else "FAIL", detail))
        if not ok:
            fails.append(name)

    names = msh_command_names(app_dir)
    legal = job_state_names_from_source(app_dir)
    pairs = list(QUOTA_COMMAND_KEYS)
    keys = sorted({k for _p, k in pairs})
    cancel_pair = (R7F8E_CANCEL_CMD, "diag_profile_learn_cancel")
    gate("R7F8E_QUOTA_TABLE", "all",
         len(pairs) == 10 and len(keys) == 9 and cancel_pair in pairs,
         "pairs=%d keys=%d 取消键=%s" % (len(pairs), len(keys), cancel_pair in pairs))
    ghost = [p for p, _k in pairs if p.split(" ")[0] not in names]
    gate("R7F8E_QUOTA_PREFIX_REAL", "all", not ghost,
         "产品里不存在的前缀=%s(写错会静默永不命中, 于是动作被当成只读)" % ghost)
    gate("R7F8E_LEARN_STATUS_READONLY", "all",
         quota_key_for_command("diag_profile learn_status") is None
         and quota_key_for_command(R7F8E_CANCEL_CMD + " 17") == "diag_profile_learn_cancel",
         "learn_status 不得算 learn/cancel; learn 请求与取消分别计数")
    gate("R7F8E_JOB_STATE_NAMES", "all", "RUNNING" in legal and "DONE" in legal,
         "源码 rel_job_state_name 状态名=%s" % ",".join(legal))
    for stg in plan["stages"]:
        sid = stg["id"]
        steps = stg.get("steps", [])
        seen_as = [st.get("as") for st in steps]
        bound = {}
        for st in steps:
            for spec in st.get("expect_values", []):
                jb = spec.get("job_binding")
                if jb and jb not in bound:
                    gate("R7F8E_JOB_REF_SPEC", sid, False,
                         "%s 引用未绑定的 %s(绑定必须由更早步骤的 job_bind 产生)"
                         % (st.get("as"), jb))
            if not st.get("job_bind"):
                continue
            b = st["job_bind"]
            nm = b.get("name")
            bad = []
            if not nm or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(nm)):
                bad.append("绑定名不合法(%r)" % (nm,))
            if seen_as.count(st.get("as")) != 1:
                bad.append("as 在本阶段不唯一")
            try:
                rgx = re.compile(b.get("row", JOB_ROW_STARTED))
                if "job_id" not in (rgx.groupindex or {}):
                    bad.append("正则缺组 job_id")
            except re.error as e:
                bad.append("STARTED 正则不可编译:%s" % e)
            for k, v in (b.get("params") or {}).items():
                if not is_count(v) or v < 0:
                    bad.append("参数 %s=%r 非非负整数" % (k, v))
            if nm in bound:
                bad.append("同一阶段里绑定名重复")
            gate("R7F8E_JOB_BIND_SPEC", sid, not bad,
                 ("%s name=%s" % (st.get("as"), nm)) + ("" if not bad else "|" + ";".join(bad)))
            if not bad:
                bound[nm] = st.get("as")
        for st in steps:
            asname = st.get("as") or "?"
            cmd = st.get("cmd", "")
            toks = cmd.split()
            p = st.get("poll") or {}
            if p:
                bad = []
                if quota_key_for_command(cmd) is not None:
                    bad.append("被轮询的 cmd=%s 是修改状态的动作 => 轮询只能发预声明的只读查询" % cmd)
                if st.get("job_bind"):
                    bad.append("job_bind 与 poll 不能同步(STARTED 行只在这一步自己的响应里绑定)")
                if "max_attempts" in p and (not is_count(p["max_attempts"]) or p["max_attempts"] < 1):
                    bad.append("max_attempts=%r" % (p["max_attempts"],))
                for f in ("until_seconds", "interval_seconds"):
                    if f in p and (not isinstance(p[f], (int, float)) or p[f] <= 0):
                        bad.append("%s=%r" % (f, p[f]))
                if p.get("in_progress"):
                    spec = p["in_progress"]
                    try:
                        rgx = re.compile(spec["row"])
                        if R7F8E_JOB_ROW_GROUP not in (rgx.groupindex or {}):
                            bad.append("缺组 %s" % R7F8E_JOB_ROW_GROUP)
                    except (re.error, KeyError, TypeError) as e:
                        bad.append("行正则不可用:%s" % e.__class__.__name__)
                    states = list(spec.get("states") or [])
                    if not states:
                        bad.append("states 为空(空集合等于永不重试, 窗口形同不存在)")
                    unknown = [x for x in states if x not in legal]
                    if unknown:
                        bad.append("状态不在源码集合里:%s" % unknown)
                    if not p.get("retry_codes"):
                        bad.append("retry_codes 为空")
                    if not p.get("job_binding"):
                        bad.append("in_progress 轮询必须声明 job_binding: 不绑就是"
                                   "把别人家的终态等成本轮的结果")
                    for spec2 in st.get("expect_values", []):
                        if spec2.get("job_binding") and \
                                spec2["job_binding"] != p.get("job_binding"):
                            bad.append("一条轮询只允许引用同一枚作业: expect_values=%s "
                                       "poll=%s" % (spec2["job_binding"],
                                                    p.get("job_binding")))
                gate("R7F8E_POLL_SPEC", sid, not bad, "%s%s" % (asname, "" if not bad else "|" + ";".join(bad)))
                if p.get("job_binding") and p["job_binding"] not in bound:
                    gate("R7F8E_JOB_REF_SPEC", sid, False,
                         "%s poll 引用未绑定的 %s" % (asname, p["job_binding"]))
            if st.get("job_cmd"):
                bad = []
                if tuple(toks[:2]) != CANCEL_PREFIX_TOKENS or len(toks) != 3:
                    bad.append("cmd=%s 不是固定形式 diag_profile learn_cancel @<绑定名>" % cmd)
                else:
                    m = JOB_REF_RE.match(toks[2])
                    if not m:
                        bad.append("参数位不是 @引用: %s" % toks[2])
                    elif m.group(1) != st["job_cmd"]:
                        bad.append("位置引用 @%s 与 job_cmd=%s 不一致" % (m.group(1), st["job_cmd"]))
                    elif m.group(1) not in bound:
                        bad.append("绑定 %s 没有由更早步骤的 job_bind 产生" % m.group(1))
                    elif bound[m.group(1)] and seen_as.index(bound[m.group(1)]) >= seen_as.index(asname):
                        bad.append("来源步骤 %s 不在本步之前" % bound[m.group(1)])
                gate("R7F8E_JOB_REF_SPEC", sid, not bad, "%s%s" % (asname, "" if not bad else "|" + ";".join(bad)))
        arms = [i for i, st in enumerate(steps) if st.get("cmd") == "motor_arm"]
        disarms = [i for i, st in enumerate(steps) if st.get("cmd") == "motor_disarm"]
        cap = stg.get("arm_energize_deadline_seconds")
        if arms:
            bad = []
            if not is_count(cap) or cap <= 0:
                bad.append("未申报正整数 arm_energize_deadline_seconds(=%r)" % (cap,))
            else:
                total = int(stg.get("total_deadline_seconds", 0) or 0)
                if total and cap > total:
                    bad.append("截止 %s 超过本阶段总窗 %s(到不了)" % (cap, total))
            if disarms and max(disarms) < min(arms):
                bad.append("阶段内的 disarm 排在 arm 之前")
            gate("R7F8E_ARM_ENERGIZE", sid, not bad,
                 "arm@%s cap=%s disarm@%s%s" % (arms, cap, disarms,
                                                "" if not bad else "|" + ";".join(bad)))
        elif cap:
            gate("R7F8E_ARM_ENERGIZE", sid, False, "申报了 arm 通电截止却没有 motor_arm 步(死条款)")
    for f in sorted(set(fails)):
        out.append("R7F8E_GATE_SUMMARY|all|FAIL|%s" % f)
    if not fails:
        out.append("R7F8E_GATE_SUMMARY|all|PASS|fails=0")
    return out, len(fails)


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
            need_key = quota_key_for_command(cmd)
            if need_key:
                # R6A §4: 离线就把"这条命令必须花哪格额度"钉住。运行期同款判定在 exec_session
                # 的 write 之前, 两者用的是同一张表 —— 不是两份判据。
                dn = (q or {}).get("name")
                n_ = (q or {}).get("n", 1)
                ok = (dn == need_key and n_ == 1 and is_count(n_) and need_key in quota)
                out.append("PLAN_QUOTA_MAP|%s|%s|%s|cmd=%s need=%s declared=%s n=%r" %
                           (sid, asname, "PASS" if ok else "FAIL", cmd, need_key, dn or "-", n_))
                if not ok:
                    bad += 1
            elif q:
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
                                                " ".join((t or {}).get("argv", []))[:70]))
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
    # F5b②(离线整包侧, 与 exec_session 里那条同一判据): 预留(a)容得下全部必要发送窗,
    # (b)住在本阶段总窗之内(motion_cap + 预留 <= 总时限)——"仍受会话总截止约束"要在**申报**
    # 阶段成立, 不能等现场清理时才发现排不下。只读盘上申报值, 不发任何命令。
    floor_r = (len(CLEANUP_CMDS) + len(IRQ_TAIL_CMDS)) * CLEANUP_WINDOW_SECONDS

    def _num_or_none(v):
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    for sg in plan["stages"]:
        if sg.get("kind") != "serial_session":
            continue
        rs = _num_or_none(sg.get("cleanup_reserve_seconds", CLEANUP_RESERVE_SECONDS))
        td = _num_or_none(sg.get("total_deadline_seconds"))
        mc = _num_or_none(sg.get("motion_cap_seconds"))
        bad_r = []
        if rs is None:
            bad_r.append("cleanup_reserve_seconds 不是数字(=%r)" % (sg.get("cleanup_reserve_seconds"),))
        else:
            if rs + 1e-9 < floor_r:
                bad_r.append("预留=%s < 五条发送窗合计=%.1f" % (rs, floor_r))
            if td is not None and mc is not None and mc + rs > td:
                bad_r.append("motion_cap=%s + 预留=%s > 本阶段总窗=%s(预留排不进会话)"
                             % (mc, rs, td))
        # F5c: 只看 cap+reserve<=total 不够 —— 起转**之前**已经消耗的步骤时间同样吃会话预算。
        # F6(A 的补充审核项)修掉这里原来的**重复计时**: 上一版把**全部**步骤的申报窗口累加成
        # 一个"起转前耗时", 后面又补一整条 motion_cap —— 起转后那些步骤同一段时间被算了两次。
        # 现在按执行器自己的计时点分三段(见 business_segments 的 docstring), cap 只进一次;
        # 并且"申报窗口"和"每步再加 SLACK 的最坏上界"分开各判一条, 余量既不漏算也不折成一次。
        steps_r = sg.get("steps", [])
        may_ob = [i for i, s in enumerate(steps_r) if isinstance(s, dict)
                  and str(s.get("cmd", "")).split(" ")[0] in
                  ("motor_arm", "motor_start", "safety_irq_enable")]
        # G3(A 预算裁定): 静态三段改用**裁定语义**的函数(结束点=可信零速快照那一步,
        # 单步窗=step_window() 的解析值)。旧函数 business_segments() 保留不删 —— 它是
        # legacy 语义的参照实现, 交接里还要用它复算那 91/23 两个数。
        bs = business_segments_ruling(steps_r, cap=mc, reserve=rs, total=td)
        arm_dl = sg.get("arm_energize_deadline_seconds")
        bad_s = []
        if may_ob and td is not None and rs is not None:
            if bs["worst_declared"] + rs > td + 1e-9:
                bad_s.append("三段互不重叠合计(申报窗, cap 只算一次)=%s + 预留=%s > 总窗=%s"
                             % (bs["worst_declared"], rs, td))
            # 裁定一: 运动 cap 只约束**起转→可信零速确认**那一段; 零速之后的内容改由会话业务
            # 截止(总窗-预留)约束, 不再叠在 cap 上。零速申报缺失(zero_confirm_index=None)时
            # 运动计时不解除, 这条按旧口径判(活动+停机后都算进 cap) => 不给"忘了申报"留绿灯。
            if bs["spin_up_index"] is not None and mc is not None:
                act = bs["motion_active_seconds"]
                if bs["zero_confirm_index"] is None:
                    act = round(act + bs["post_zero_seconds"], 3)
                if act > mc + 1e-9:
                    bad_s.append("起转→可信零速确认的活动内容=%s > cap=%s"
                                 "(裁定一后运动 cap 只管这一段)" % (act, mc))
                if bs["post_zero_seconds"] > (bs["business_budget_seconds"] or 0.0) + 1e-9:
                    bad_s.append("零速确认后内容=%s > 业务预算=%s"
                                 % (bs["post_zero_seconds"], bs["business_budget_seconds"]))
            if bs["stop_send_after_start_s"] is not None and \
                    sg.get("normal_stop_send_by_seconds") is not None and \
                    bs["stop_send_after_start_s"] > \
                    float(sg["normal_stop_send_by_seconds"]) + 1e-9:
                # 这条只在阶段**申报了**那个数时才判(现场包申报 §4 的 8 s): 合成夹具的窗口是按
                # 它自己的场景放大的(62 s cap), 没申报这条业务规则, 不许拿现场的值去判它。
                bad_s.append("正常 stop 的发送尝试排在起转后 %s s > 申报的 %s s"
                             % (bs["stop_send_after_start_s"], sg["normal_stop_send_by_seconds"]))
            if bs["zero_confirm_after_start_s"] is not None and mc is not None and \
                    bs["zero_confirm_after_start_s"] > mc + 1e-9:
                bad_s.append("按申报窗累计, 可信零速确认要到起转后 %s s > cap=%s"
                             % (bs["zero_confirm_after_start_s"], mc))
            if bs["arm_to_disabled_s"] is not None and \
                    bs["arm_to_disabled_s"] > (arm_dl if arm_dl is not None else 1e9) + 1e-9:
                bad_s.append("按申报窗累计, arm→可信禁能确认 %s s > 通电截止=%s"
                             % (bs["arm_to_disabled_s"], arm_dl))
        bad_w = []
        if may_ob and bs["business_budget_seconds"] is not None:
            # 裁定二之后: 申报的**单步总窗已含余量**, 所以这里核的是"38 步总窗合计 + 预留
            # 排不排得进总窗"以及"保守式(整段 cap)排不排得进总窗", 不再按每步叠 SLACK 推最坏。
            if bs["business_total_seconds"] + float(rs) > float(td) + 1e-9:
                bad_w.append("38 步总窗合计=%s + 预留=%s > 总窗=%s"
                             % (bs["business_total_seconds"], rs, td))
            if bs["business_total_seconds"] > bs["business_budget_seconds"] + 1e-9:
                bad_w.append("38 步总窗合计=%s > 业务预算(总窗-预留)=%s"
                             % (bs["business_total_seconds"], bs["business_budget_seconds"]))
            if bs["conservative_total_with_cap_s"] is not None and \
                    bs["conservative_total_with_cap_s"] > float(td) + 1e-9:
                bad_w.append("保守式(起转前 + 整段 cap + 零速后 + 预留)=%s > 总窗=%s"
                             % (bs["conservative_total_with_cap_s"], td))
        # 计划自己申报的三段镜像必须与这里现算的**同一套数**一致(A 的要求: 检查器与执行器
        # 使用同一值)。没申报镜像的历史包不判红 —— 它们字节冻结, 只登记 NA。
        decl = (plan.get("motion_budget") or {}).get("business_segments")
        mirror = "NA(计划未申报 motion_budget.business_segments)"
        if decl:
            keys = ("spin_up_index", "stop_request_index", "zero_confirm_index",
                    "pre_start_steps", "motion_active_steps", "post_zero_steps",
                    "pre_start_seconds", "motion_active_seconds", "post_zero_seconds",
                    "stop_send_after_start_s", "zero_confirm_after_start_s",
                    "arm_to_disabled_s", "window_seconds", "reserve_seconds",
                    "session_total_seconds", "business_total_seconds",
                    "business_budget_seconds", "worst_declared",
                    "conservative_total_with_cap_s", "conservative_arm_with_cap_s",
                    "steps_total", "semantics")
            drift = []
            for k in keys:
                if k not in decl:
                    drift.append("%s=缺失" % k)
                elif isinstance(decl[k], (int, float)) and isinstance(bs[k], (int, float)):
                    if abs(float(decl[k]) - float(bs[k])) > 1e-6:
                        drift.append("%s=%s!=%s" % (k, decl[k], bs[k]))
                elif decl[k] != bs[k]:
                    drift.append("%s=%r!=%r" % (k, decl[k], bs[k]))
            mirror = ("一致(%d 项)" % len(keys) if not drift
                      else "漂移: " + ",".join(drift[:6]))
            if drift:
                bad_s.append("计划申报的三段镜像与执行器现算不符 => %s" % ",".join(drift[:3]))
        out.append("PLAN_CLEANUP_RESERVE|%s|%s|reserve=%s floor=%.1f total=%s cap=%s%s" %
                   (sg["id"], "PASS" if not bad_r else "FAIL", rs, floor_r, td, mc,
                    "" if not bad_r else "|" + ";".join(bad_r)))
        if bad_r:
            bad += 1
        out.append("PLAN_BUSINESS_RESERVE_SPLIT|%s|%s|may_obligate=%s spin@%s stop@%s zero@%s "
                   "pre_start=%s(%d步) motion_active=%s(%d步) post_zero=%s(%d步) "
                   "stop_after_start=%s zero_after_start=%s arm_to_disabled=%s "
                   "cap=%s reserve=%s total=%s business_total=%s worst=%s cons_total=%s "
                   "cons_arm=%s mirror=%s%s" %
                   (sg["id"], "PASS" if not bad_s else "FAIL", bool(may_ob),
                    bs["spin_up_index"], bs["stop_request_index"], bs["zero_confirm_index"],
                    bs["pre_start_seconds"], bs["pre_start_steps"],
                    bs["motion_active_seconds"], bs["motion_active_steps"],
                    bs["post_zero_seconds"], bs["post_zero_steps"],
                    bs["stop_send_after_start_s"], bs["zero_confirm_after_start_s"],
                    bs["arm_to_disabled_s"], mc, rs, td, bs["business_total_seconds"],
                    bs["worst_declared"], bs["conservative_total_with_cap_s"],
                    bs["conservative_arm_with_cap_s"], mirror,
                    "" if not bad_s else "|" + ";".join(bad_s)))
        # 旧口径的两个数继续留原文(A: 原来的 91/23 秒红原文继续保留, 注明对应旧语义),
        # 这一行**不参与判红**(bad 不加), 只把"如果还按每步叠 SLACK / stop 不解除运动计时"
        # 会算出的数摆在这里, 让人看见转绿是语义变了, 不是把数改了。
        out.append("PLAN_BUSINESS_RESERVE_SPLIT_LEGACY_SEMANTICS|%s|INFO|%s "
                   "legacy_pre_bound=%s legacy_after_spin=%s 本行不判红, 仅留档旧口径" %
                   (sg["id"], bs["legacy_semantics_note"], bs["legacy_pre_bound_seconds"],
                    bs["legacy_after_spin_declared_seconds"]))
        # 裁定二第 1/2/3 条的静态面: 每一步的总窗必须合法、轮询窗不得超出本步总窗、
        # 没申报的步继续走 legacy(冻结的 R7F7 计划因此一字不变)。非法值在这里就判红,
        # 不等运行时开口(运行时那侧是 STEP_TOTAL_SECONDS_INVALID)。
        bad_t = []
        n_decl = 0
        for s in steps_r:
            if not isinstance(s, dict) or not s.get("cmd"):
                continue
            prob = step_total_problem(s)
            if prob:
                bad_t.append("%s:%s" % (s.get("as"), prob))
            if STEP_TOTAL_KEY in s:
                n_decl += 1
        if may_ob and bs["zero_confirm_index"] is not None:
            z = bs["zero_confirm_index"]
            if str(steps_r[z].get("cmd", "")).split(" ")[0] != "motor_status":
                bad_t.append("零速确认申报落在 %s(命令 %s), 裁定一只认 motor_status"
                             % (steps_r[z].get("as"), steps_r[z].get("cmd")))
        out.append("PLAN_STEP_TOTAL_WINDOWS|%s|%s|declared=%d/%d legacy=%d 非法=%s "
                   "zero_evidence_as=%s%s" %
                   (sg["id"], "PASS" if not bad_t else "FAIL", n_decl, bs["steps_total"],
                    bs["steps_total"] - n_decl, bad_t[:4] or "无",
                    steps_r[bs["zero_confirm_index"]].get("as")
                    if bs["zero_confirm_index"] is not None else "NONE(运动计时不解除)",
                    "" if not bad_t else "|" + ";".join(bad_t[:3])))
        if bad_t:
            bad += 1
        if bad_s:
            bad += 1
        # 最坏情况那条单独成门: 它不改运行时(收口预留始终由 business_abs_dl 硬夹保护),
        # 只回答"计划里的业务尾巴在最坏到达下跑到第几步" —— 红了就是**取证完整性**风险,
        # 不是撤能风险, 两者必须分开写, 不许混成一条"看起来更严"的判据。
        out.append("PLAN_BUSINESS_WORST_CASE_FIT|%s|%s|business_total=%s budget=%s "
                   "cons_total=%s cons_arm=%s%s" %
                   (sg["id"], "PASS" if not bad_w else "FAIL", bs["business_total_seconds"],
                    bs["business_budget_seconds"], bs["conservative_total_with_cap_s"],
                    bs["conservative_arm_with_cap_s"],
                    "" if not bad_w else "|" + ";".join(bad_w)))
        if bad_w:
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
    # 状态披露白名单必须是"锚定 + 同时被正面要求", 否则它就是把一个 REFUSED 换成"以后什么都放行"的口子。
    sa_bad, sa_seen = [], []
    for stg in plan["stages"]:
        for st in stg.get("steps", []):
            for p in st.get("status_lines_allow", []):
                head = (st.get("cmd") or "").split(" ")[0]
                if not (p.startswith("^") and p.endswith("$")) or ".*" in p:
                    sa_bad.append("%s/%s:模式未整行锚定或含 .* 通配(%s)" % (stg["id"], st.get("as"), p))
                elif (head not in STATUS_QUERY_CMDS and
                      " ".join((st.get("cmd") or "").split()) not in READONLY_QUERY_CMDS and
                      st.get("expect_profile") != "RUNNING"):
                    sa_bad.append("%s/%s:豁免只给**状态查询步**(cmd=%s 不是查询, 也没断言 RUNNING)"
                                  % (stg["id"], st.get("as"), head or "无 cmd"))
                elif not st.get("expect_values"):
                    sa_bad.append("%s/%s:豁免步骤没有 expect_values 硬字段判据"
                                  % (stg["id"], st.get("as")))
                elif not any(w and w in status_allow_literal_prefix(p)
                             for w in (st.get("expect_contains") or [])):
                    sa_bad.append("%s/%s:豁免行的字面前缀 %r 没被本步 expect_contains 正面要求"
                                  " => 运行期会具名停 STATUS_ALLOW_NOT_POSITIVELY_REQUIRED"
                                  % (stg["id"], st.get("as"), status_allow_literal_prefix(p)))
                else:
                    sa_seen.append("%s:%s" % (stg["id"], st.get("as")))
    # 字节损坏口子的唯一形状: 坏响应不作数 + 同会话更靠后的干净步骤**正面**给出同一事实(离线核形状)。
    # R6(复审 §5.3): "字节损坏后由同会话更靠后的干净步骤重确认"这条合同**退役** ——
    # 它当时只核对了"存在更晚的步骤且它有 expect_values", 没有证明那些字段与受损动作逐项等价,
    # 于是"含 NUL 的响应"可以被后面一条 system_status 洗成 OK。本版执行器一律"字节损坏即停",
    # 因此计划里再申报这个合同本身就是缺陷: 申报即判红, 不再检查它的形状。
    br_bad = ["%s/%s:申报了 byte_corruption_recovery(reconfirmed_by=%s), 但该合同已退役 => "
              "字节损坏必须即停" % (stg["id"], x.get("as"),
                                    (x.get("byte_corruption_recovery") or {}).get("reconfirmed_by"))
              for stg in plan["stages"] for x in stg.get("steps", [])
              if x.get("byte_corruption_recovery")]
    out.append("PLAN_BYTE_RECOVERY|all|%s|%s" %
               ("PASS" if not br_bad else "FAIL",
                ";".join(br_bad) or "计划未申报任何'损坏后可继续'例外 => 裸 NUL / 非法 UTF-8 / "
                                     "危险文本一律即停(原字节仍先落盘)"))
    if br_bad:
        bad += 1
    out.append("PLAN_STATUS_ALLOW|all|%s|%s" %
               ("PASS" if not sa_bad else "FAIL",
                ";".join(sa_bad) or ("白名单都是锚定且被正面要求(要求串必须是该行的前缀)的状态行: %s" % ",".join(sa_seen)
                                     if sa_seen else "未使用(默认所有 REFUSED 线都算业务失败)")))
    if sa_bad:
        bad += 1
    # 契约门: 凡 cmd 在 ARM_GATE_MASK_ROWS 里的查询步, 都必须 (a)有且只有一条钉 arm_gate_mask
    # 的逐值判据(行正则与执行器常量逐字节相同), (b)mask 组被 must / must_one_of 钉死,
    # (c)status_lines_allow 恰好等于"预期非 0 的那些 mask"各一条整行锚定模式。
    # 于是"预期为 0 的步骤仍要求 0": 它没有豁免, 现场真打出 REFUSED 就按 BUSINESS_FAILED 停。
    declared_mask = plan.get(ARM_GATE_MASK_CONTRACT_KEY) == ARM_GATE_MASK_CONTRACT_VERSION
    if not declared_mask:
        out.append("R7F8F_ARM_GATE_MASK|all|NA|计划未申报 %s=%s(历史冻结包字节不回改, 只登记 NA)"
                   % (ARM_GATE_MASK_CONTRACT_KEY, ARM_GATE_MASK_CONTRACT_VERSION))
    else:
        mc_bad, mc_seen = [], []
        for stg in plan["stages"]:
            for st in stg.get("steps", []):
                head = (st.get("cmd") or "").split(" ")[0]
                row = ARM_GATE_MASK_ROWS.get(head)
                if row is None:
                    continue
                nm = "%s/%s" % (stg["id"], st.get("as"))
                refused_tail, pass_tail = ARM_GATE_MASK_TAILS[head]
                specs = [sp for sp in (st.get("expect_values") or []) if sp.get("row") == row]
                if len(specs) != 1:
                    mc_bad.append("%s:arm_gate_mask 逐值判据要有且只有一条(实际 %d 条)"
                                  % (nm, len(specs)))
                    continue
                sp = specs[0]
                decl = {}
                for grp in ("mask", "tail"):
                    one = (sp.get("must") or {}).get(grp)
                    setv = (sp.get("must_one_of") or {}).get(grp)
                    if one is not None and setv is not None:
                        mc_bad.append("%s:%s 同时给了 must 与 must_one_of" % (nm, grp))
                    elif one is not None:
                        decl[grp] = [str(one)]
                    elif isinstance(setv, (list, tuple)) and setv:
                        decl[grp] = [str(x) for x in setv]
                    else:
                        mc_bad.append("%s:%s 既没 must 也没 must_one_of(等于没在判)" % (nm, grp))
                masks = decl.get("mask") or []
                if not masks:
                    continue
                want = sorted(arm_gate_refused_allow(head, m) for m in masks if m != "0x0")
                got = sorted(st.get("status_lines_allow") or [])
                if want != got:
                    mc_bad.append("%s:按预期 mask%s 应有的豁免=%s, 实际申报=%s"
                                  % (nm, masks, want, got))
                twant = ([pass_tail] if masks == ["0x0"] else
                         ([pass_tail, refused_tail] if "0x0" in masks else [refused_tail]))
                if decl.get("tail") and sorted(decl["tail"]) != sorted(twant):
                    mc_bad.append("%s:tail 集合%s 与 mask 集合%s 不自洽(产品只在 mask=0 打 ALL-PASS)"
                                  % (nm, decl.get("tail"), masks))
                mc_seen.append("%s=%s" % (st.get("as"), "|".join(sorted(masks))))
        if not mc_seen:
            mc_bad.append("申报了契约却没有覆盖到任何 motor_status/system_status 查询步")
        out.append("R7F8F_ARM_GATE_MASK|all|%s|%s" %
                   ("PASS" if not mc_bad else "FAIL",
                    ";".join(mc_bad) or ("覆盖 %d 条查询步, 每步都钉了预期 mask(预期 0 的步无豁免): %s"
                                         % (len(mc_seen), ",".join(mc_seen)))))
        if mc_bad:
            bad += 1
    # R5 现场轮(field-20261101T142722Z)的观测窗教训: 计划申报的被动启动窗必须**真的走满**, 而且
    # "从复位/开口到第一条查询"的可观测总量要覆盖经验上成功过的启动窗(cont 轮 75 s 拿满整段启动)。
    # 未申报 observation_bounds 的历史包只登记 NA, 不回改它们的字节, 也不给红。
    ob = plan.get("observation_bounds") or {}
    bmin = ob.get("boot_window_seconds_min")
    bw_bad, bw_seen = [], []
    if bmin is None:
        out.append("PLAN_BOOT_WINDOW_COVER|all|NA|未申报 observation_bounds.boot_window_seconds_min"
                   "(R5-C 轮新增门; 历史包不回改字节, 因此不判红)")
    else:
        if not ob.get("boot_window_provenance"):
            bw_bad.append("下界没有出处(要回指经验成功过的窗口 + 源码层耗时估算)")
        for stg in plan["stages"]:
            if stg.get("kind") != "serial_session":
                continue
            steps = stg.get("steps", [])
            ri = next((i for i, s in enumerate(steps) if s.get("action") == "reset"), None)
            if ri is None and not stg.get("first_query_is_boot_probe"):
                continue
            first = next((s for s in steps[(ri + 1) if ri is not None else 0:] if s.get("cmd")), None)
            if first is None:
                bw_bad.append("%s:会话里没有第一条可判定查询" % stg["id"])
                continue
            rstep = steps[ri] if ri is not None else {}
            if rstep.get("boot_quiet_seconds") is not None:
                bw_bad.append("%s:申报了 boot_quiet_seconds => 静默会把窗重新剪短" % stg["id"])
            drain = float(rstep.get("boot_drain_seconds", 20)) if ri is not None else 0.0
            want = float(first.get("seconds", 3))
            hz = drain + want + SLACK_SECONDS
            bw_seen.append("%s=%.0f+%0.f+%ds=%.0fs" % (stg["id"], drain, want, SLACK_SECONDS, hz))
            if hz < float(bmin):
                bw_bad.append("%s:可观测量 %.0fs < 下界 %ss" % (stg["id"], hz, bmin))
        if not bw_seen and not bw_bad:
            bw_bad.append("申报了下界但没有任何会话落在门内(恒真门)")
        out.append("PLAN_BOOT_WINDOW_COVER|all|%s|%s" %
                   ("PASS" if not bw_bad else "FAIL",
                    ";".join(bw_bad) or "下界=%ss, 覆盖: %s" % (bmin, ",".join(bw_seen))))
        if bw_bad:
            bad += 1
    e_out, e_bad = validate_r7f8e(plan, rows_doc, app_dir)
    out.extend(e_out)
    bad += e_bad
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
    # F6c: F5b 的 IRQ 尾部两条和上面三条一样是**执行器自己发的**, 不是阶段步骤。修前这里只给
    # CLEANUP_CMDS 兜底, 于是整包 stub 里 safety_irq_disable/safety_irq_status 永远无行可喂 =>
    # 本轮最想要的那条"整包跑到 IRQ 尾部并且确认 CLOSED"在离线**判不了**(只能在矩阵里手搓夹具)。
    # 只在行表确实带了这个键时才注入: 历史包的行表没有这两个键, 它们的字节行为一丁点都不变。
    for cmd in IRQ_TAIL_CMDS:
        if cmd not in responses and cmd in (table.get("commands") or {}):
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


def s0_binding(plan, repo_root, plan_path=None, approval_id=None, snapshot=None):
    """S0 是**真实文件与来源绑定**, 不是一行 OFFLINE_BINDING 声明:
    镜像按路径重算 sha256/字节数并与 identity_binding 与 build_manifest 逐字段比;
    源码提交用 git cat-file 证明该 blob 存在; needles 全部回到 C 源码证明。

    R6 §7.4 补的一条: S0 还要当场算 **plan / 执行器 / 镜像** 三件的实际哈希并连同批准身份一起记,
    因为"JSON 里登记的哈希"和"这一刻真正被执行的字节"是两回事(R5-C 的 S0 就是照抄登记值,
    现场跑的却是改过两轮的采集器)。历史 S0 不回填。

    R6C §5: 这一条当时只做了一半 —— plan 的"实际哈希"仍是**另读一次磁盘**。于是"读出 P →
    核对 P → 执行 P"之后磁盘被换成 Q, S0 会把 Q 记成本次的 plan_sha256(实际动作仍来自已批准的
    P, 所以不是 B2 那个未批准 argv 绕过, 但登记身份是假的)。现在 plan_sha256 只认**被执行的那份
    快照**, 磁盘值另用 `plan_sha256_on_disk` 记录; 两者在执行前就不同 => 具名拒绝, 不留下"记 Q 执行 P"。"""
    root = Path(repo_root)
    ident = plan["identity_binding"]
    img = ident["image"]
    p = root / img["path"]
    facts = {"image_path": img["path"]}
    facts["approval_id"] = approval_id or "NONE_DECLARED"
    facts["plan_source"] = str(plan_path) if plan_path else "IN_MEMORY_PLAN_NO_PATH"
    disk_sha = sha_of(plan_path) if plan_path and Path(plan_path).is_file() else None
    facts["plan_sha256_on_disk"] = disk_sha
    if snapshot is not None:
        facts["plan_sha256"] = snapshot["sha256"]
        facts["plan_snapshot_bytes"] = snapshot.get("bytes")
        facts["plan_snapshot_path"] = str(snapshot.get("path"))
        if disk_sha is not None and disk_sha != snapshot["sha256"]:
            raise Stop("S0_PLAN_DISK_DRIFT",
                       "execution=%s disk=%s 磁盘在执行前已与被执行的字节不同" %
                       (snapshot["sha256"][:16], disk_sha[:16]))
    else:
        facts["plan_sha256"] = disk_sha
        facts["plan_snapshot_bytes"] = None
    facts["executor_path"] = str(Path(__file__).resolve())
    facts["executor_sha256"] = sha_of(__file__)
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
                  plan_dir=None, approval_id=None, approval_root=None, plan_path=None,
                  plan_snapshot=None):
    """一次批准 -> 从 S0 一直走到**计划里的最后一个阶段**; 成功阶段只记检查点(不为下一阶段
    重新批准、不重新下载), **失败**才终止。
    整包共用同一本额度账(quota_box)与同一个动作后端; 每个串口会话仍各开一次端口。
    任何会话非成功(含清理 UNKNOWN / 清理错误 / 预留耗尽 / 证据失败)都阻断后续阶段并非零返回。

    R6 §7.4: 给了 approval_id 时, 这本 quota_box 背后还挂着一份**以批准身份命名、放在
    输出目录之外**的持久预占账 —— 换 --out、重开进程都不清零(26 场把 apply 用到 14/2 就是
    没有这本账)。真实传输没有这本账一律不进入。"""
    code = mode_gate(transport, backend_name, profile)
    if code:
        # 模式账先于一切: 这一刻 UID 没读、工具没起、端口没开, 所以"副作用清单为空"是真空而不是漏记。
        return dispatch_refusal(plan, profile, outdir, code)
    if transport == "real" and (not approval_token or approval_token != expected_token):
        # 批准门也在读私有 UID 之前: 没批准就没有任何一次私有材料的消费。
        return dispatch_refusal(plan, profile, outdir, "REAL_TRANSPORT_REFUSED")
    if transport == "real" and not approval_id:
        return dispatch_refusal(plan, profile, outdir, "APPROVAL_ID_REQUIRED")
    if transport == "real" and not plan_path:
        # 复审 §4: 真实整包必须从**一份文件**执行 —— "计划/步骤与批准字节一致"这句话
        # 需要一个可重读的字节来源, 内存对象不算。没有 --plan 就没有可核对的东西, 一条不发。
        return dispatch_refusal(plan, profile, outdir, "REAL_DISPATCH_REQUIRES_PLAN_FILE")
    snap = plan_snapshot
    if transport == "real":
        # R6B §5: 真实整包的**执行对象**与**身份哈希**必须来自同一份原始字节。旧版这里
        # 只把 plan_path 交给 current_identities 另读一次, 于是内存对象可以和被哈希的文件不同。
        try:
            snap = snap or read_plan_snapshot(plan_path)
        except Stop as e:
            return dispatch_refusal(plan, profile, outdir, e.code)
        except OSError as e:
            return dispatch_refusal(plan, profile, outdir, "PLAN_SNAPSHOT_UNREADABLE")
        if snap["doc"] != plan:
            # §5.1 内存对象被改(只剩 S1 / download.argv 换掉) 与 §5.2 CLI 在"读取"与"哈希"
            # 之间被换成另一份文件, 都落在这里: 这一刻 UID 没读、工具没起、端口没开、额度没预占。
            return dispatch_refusal(plan, profile, outdir, "PLAN_NOT_FROM_APPROVED_SNAPSHOT")
        plan = snap["doc"]      # 之后 stages/tools/argv/quota/时间窗**只**从这份已核快照推导
    quota_box = dict(plan["quota"])
    used_box = {}

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    led_path = out / "dispatch_ledger.jsonl"
    led = Ledger(led_path, clock, out_dir=out)
    ledger_reservations = []
    try:
        approval = open_approval(transport, Path(approval_root or default_approval_root()),
                                 approval_id, current_identities(plan, plan_path, repo_root,
                                                                  snapshot=snap),
                                 plan["quota"], led, clock)
        if approval is not None:
            snap0 = approval.snapshot()      # 读盘即验身份: 损坏/版本/身份不符都在这一步炸
            led.ev("APPROVAL_LEDGER_BOUND", path=str(approval.path), approval_id=approval_id,
                   prior_spent=snap0["spent"], prior_pending=len(snap0["pending"]))
    except Stop as e:
        # 账本自己不对劲 => 一条命令都不发。这里绝不"顺手建一本新的"或"退回内存账":
        # 那等于让一次用尽的批准重新满血, 而本次复审要拦的正是这件事。
        led.ev("APPROVAL_GATE_REFUSED", code=e.code, detail=e.detail)
        led.close()
        return dispatch_refusal(plan, profile, outdir, e.code)

    def reserve_stage(name, n=1, kind="business"):
        """阶段级动作(download/readback/reset/reboot/人工重启门)在**动工具之前**先落持久预占。
        quota_box 只活到本次 dispatch 结束, 换 --out 或重开进程就清零 —— R5-C 把 apply 用到
        14/2 正是从这条路漏的。内存账照旧(顺序与语义都不动), 这本账负责跨进程可见。"""
        if approval is None:
            return None
        res = approval.reserve(name, n, kind=kind)
        ledger_reservations.append(res)
        return res

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
                facts = s0_binding(plan, repo_root, plan_path=plan_path, approval_id=approval_id,
                                   snapshot=snap)
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
                reserve_stage(qname, 1)
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
                reserve_stage(name, 1)     # 工具起来之前先落账: 崩溃也按已消耗计, 不退款
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
                approved_pkg = None
                if transport == "real" or approval is not None:
                    # R6A §4: 真实动作前, 会话自己会再核一次"这些步骤就是那份被批准的字节里的"。
                    # plan_sha256 取**账本登记值**(= 开账时那三件身份之一), 不是此刻盘上的值 ——
                    # 盘上变了就该拒, 而不是跟着变。
                    approved_pkg = {
                        "plan_path": str(Path(plan_path).resolve()) if plan_path else None,
                        "plan_sha256": ((approval.identities or {}).get("plan_sha256")
                                        if approval is not None else
                                        (sha_of(plan_path) if plan_path else None)),
                        "stage": sid, "profile": ap, "approval_id": approval_id,
                        "steps_sha256": canonical_steps_sha(steps)}
                res = exec_session("S_" + sid, steps, out, transport, fx, port,
                                   int(stg.get("total_deadline_seconds", 120)), clock,
                                   backend=backend,
                                   motion_cap_seconds=cap,
                                   energize_cap_seconds=stg.get("arm_energize_deadline_seconds"),
                                   cleanup_reserve_seconds=stg.get("cleanup_reserve_seconds"),
                                   plan=plan, quota_box=quota_box, used_box=used_box,
                                   context=recorded, approval=approval, approved=approved_pkg)
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
                             # F6b: 整包 dispatch 才是现场轮真正走的那条路。上一版这里只搬
                             # status/code/disarm/terminal, 把 F5b/F5c 新加的**预留与夹短回执字段**
                             # 留在了会话结果里 —— 现场 stdout 看不到"这 7 秒到底留没留住",
                             # 离线门也只能判成 code=None(工具的 parse 缺陷之外还有一层数据缺陷)。
                             "scenario": sc, "branch": ap,
                             "cleanup_reserve_seconds": res.get("cleanup_reserve_seconds"),
                             "cleanup_hard_deadline": res.get("cleanup_hard_deadline"),
                             "session_abs_deadline": res.get("session_abs_deadline"),
                             "business_abs_deadline": res.get("business_abs_deadline"),
                             "cleanup_reserved": res.get("cleanup_reserved"),
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
    # 阶段级预占在整包收尾时确认消耗(会话级由 exec_session 自己收尾)。确认只摘 pending,
    # **一条额度都不退**: 中途 STOP 的 download/readback 也算用过。
    approval_end = None
    commit_failures = []
    if approval is not None:
        for res in ledger_reservations:
            try:
                approval.commit(res, ok=not notrun)
            except Stop as e:
                commit_failures.append("%s:%s" % (res["tag"], e.code))
                led.ev("APPROVAL_COMMIT_FAILED", tag=res["tag"], code=e.code, detail=e.detail)
        del ledger_reservations[:]
        approval_end = approval.snapshot()
        led.ev("APPROVAL_LEDGER_END", path=str(approval.path), spent=approval_end["spent"],
               pending=len(approval_end["pending"]), caps=approval_end["caps"],
               commit_failures=commit_failures)
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
        and not undeclared and not negative and not commit_failures
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
    # 一次 STOP(例如取证会话失败)的整包不能顶着一个"保护已验证"的汇总标签: 每场各自的
    # target_claim 可以在失败之前就都拿到 VERIFIED, 但那不改变整包没走完这个事实。
    core_verified = bool(ok) and bool(claims) and all(
        v == "VERIFIED" for v in claims.values())
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
            "approval": (None if approval is None else
                         {"approval_id": approval_id, "ledger": str(approval.path),
                          "spent": approval_end["spent"], "pending": len(approval_end["pending"]),
                          "caps": approval_end["caps"],
                          "commit_failures": commit_failures}),
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
    ap.add_argument("--arm-energize-deadline-seconds", type=int, default=0,
                    help="E4: motor_arm 之后的通电保持截止(单会话工装入口; 整包按阶段申报)")
    ap.add_argument("--cleanup-reserve-seconds", type=float, default=0.0,
                    help="F5b: 收口预留(默认 %s s, 必须容得下五条各自的确认窗; 整包按阶段申报)"
                         % CLEANUP_RESERVE_SECONDS)
    ap.add_argument("--plan", help="计划 JSON: 提供后额度在发送前真实扣减(quota)")
    ap.add_argument("--approval-token")
    ap.add_argument("--expected-token")
    ap.add_argument("--approval-token-file",
                    help="批准令牌原话**文件**(推荐): 在 Python 内按严格 UTF-8 取字节, "
                         "不经控制台打印与 shell 变量转运; 与 --approval-token 同时给必须逐字节相等")
    ap.add_argument("--token-verbatim-file",
                    help="只参与对账的原话文件(不改令牌来源): 真实 dispatch 前核它的摘要是否"
                         "等于账本绑定值")
    ap.add_argument("--approval-id", default="",
                    help="批准身份(令牌/审批记录名): 跨进程持久预占账按它命名, 真实执行必填")
    ap.add_argument("--approval-root", default="",
                    help="账本目录(默认在 %s, 与 --out 无关); 现场请指一块可持久化的位置" %
                         default_approval_root())
    ap.add_argument("--approval-init", action="store_true",
                    help="离线开立一本新账(唯一的\"从空开始\"入口, 不碰设备; 已存在则拒绝覆盖)")
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
        # 真实执行还要一个**持久额度账的挂钩**: 只有令牌没有账, 换一次 --out 就换一份额度,
        # 那正是 R5-C 把 apply 用到 14/2 的路径。没有 approval_id / 没有 plan(额度来源) => 不开口。
        if not a.approval_id:
            print("REAL_TRANSPORT_REFUSED:approval_id_required", file=sys.stderr)
            return 4
        if not a.dispatch:
            # 复审 M8-R6A §4 的最小修法: 真实后端暂时只允许**经批准的整包 dispatch**。
            # 单会话 --steps 没有"这批步骤属于那份批准"的来源, 留在入口就等于留一条绕账的路。
            # 离线工装(stub)的单会话入口原样保留 —— 它是夹具测试的入口, 不碰设备。
            print("REAL_TRANSPORT_REFUSED:single_session_steps_not_approved_package"
                  "(真实执行只接 --dispatch 整包; 任意 --steps 请用 --transport stub)",
                  file=sys.stderr)
            return 4
        if not a.plan:
            print("REAL_TRANSPORT_REFUSED:plan_required_for_quota_ledger", file=sys.stderr)
            return 4
    elif not a.dispatch and not a.validate_plan and not a.approval_init and not a.fixture:
        # 单会话入口仍需显式夹具; --dispatch 自己按 rows.json 逐阶段装配, 不接受外部夹具
        print("STUB_REFUSED:fixture_required", file=sys.stderr)
        return 4
    plan_doc = None
    plan_snap = None
    if a.plan or a.dispatch or a.validate_plan or a.approval_init:
        if not a.plan:
            print("DISPATCH_REFUSED:plan_required", file=sys.stderr)
            return 4
        # R6B §5: 计划**只读一次盘**, 后面的身份哈希与整包执行对象都从这份快照推导
        # (旧版是 read_text 拿对象 + current_identities 再读一次算 SHA, 两次读之间换文件即可绕过)
        try:
            plan_snap = read_plan_snapshot(a.plan)
        except Stop as e:
            print("PLAN_SNAPSHOT_REFUSED:%s|%s" % (e.code, e.detail), file=sys.stderr)
            return 4
        except OSError as e:
            print("PLAN_SNAPSHOT_REFUSED:PLAN_SNAPSHOT_UNREADABLE|%r" % (e,), file=sys.stderr)
            return 4
        plan_doc = plan_snap["doc"]
    if a.approval_init:
        if not a.approval_id:
            print("APPROVAL_INIT_REFUSED:approval_id_required", file=sys.stderr)
            return 4
        try:
            # 开账时就把令牌摘要绑进账本: 之后每一次真实开口都要与它对账(不带令牌=不绑=开不了口)。
            tb, tsrc = None, ""
            if a.approval_token_file:
                tb = token_bytes_from_file(a.approval_token_file)
                tsrc = "file:%s" % a.approval_token_file
                if a.approval_token is not None and a.approval_token.encode("utf-8") != tb:
                    raise Stop("TOKEN_SOURCE_CONFLICT",
                               "--approval-token 与 --approval-token-file 不是同一份原话字节")
                text = tb.decode("utf-8")
            else:
                text = a.approval_token
                if text:
                    tb, tsrc = text.encode("utf-8"), "cli"
            p = approval_init(Path(a.approval_root or default_approval_root()), a.approval_id,
                              current_identities(plan_doc, a.plan, a.repo_root,
                                                 snapshot=plan_snap),
                              plan_doc["quota"], note="offline --approval-init, no device action",
                              token_bytes=tb, token_source=tsrc)
        except Stop as e:
            print("APPROVAL_INIT_REFUSED:%s|%s" % (e.code, e.detail), file=sys.stderr)
            return 4
        print("APPROVAL_INIT|path=%s|caps=%s|identities=%s" %
              (p, json.dumps(plan_doc["quota"], sort_keys=True),
               json.dumps(current_identities(plan_doc, a.plan, a.repo_root,
                                             snapshot=plan_snap), sort_keys=True)))
        print("APPROVAL_INIT_TOKEN|%s" % json.dumps(
            {"approval_id": a.approval_id,
             "token_sha256": (tb and sha_bytes(tb)) or "NOT_BOUND",
             "token_bytes": (tb and len(tb)) or 0, "source": tsrc or "-",
             "note": "没绑令牌摘要的账本**开不了真实口**(verify_token_chain 具名拒绝)"},
            ensure_ascii=False, sort_keys=True))
        return 0
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
        if a.transport == "real":
            # 开口之前的令牌门(F20 的自伤就在这里: 令牌与批注原话不是同一份字节, 而当时没有任何
            # 一道门会发现)。stub 工装入口不经过这里 —— 它本来就不需要批准。
            try:
                tb = (token_bytes_from_file(a.approval_token_file) if a.approval_token_file
                      else (a.approval_token.encode("utf-8") if a.approval_token else None))
                if a.approval_token_file and a.approval_token is not None and \
                        a.approval_token.encode("utf-8") != tb:
                    raise Stop("TOKEN_SOURCE_CONFLICT",
                               "--approval-token 与 --approval-token-file 不是同一份原话字节")
                a.approval_token = tb.decode("utf-8") if tb is not None else a.approval_token
                tc = verify_token_chain(Path(a.approval_root or default_approval_root()),
                                        a.approval_id or None, tb, a.token_verbatim_file)
                print("TOKEN_CHAIN_VERIFIED|%s" % json.dumps(tc, sort_keys=True))
            except Stop as e:
                print("TOKEN_CHAIN_REFUSED:%s|%s" % (e.code, e.detail), file=sys.stderr)
                return 4
        clock = SimClock() if a.transport == "stub" else RealClock()
        try:
            res = dispatch_plan(plan_doc, a.transport, a.backend, a.out, clock, a.profile,
                                a.port, a.repo_root, a.approval_token, a.expected_token,
                                a.uid_file, str(Path(a.plan).resolve().parent),
                                approval_id=a.approval_id or None,
                                approval_root=a.approval_root or None, plan_path=a.plan,
                                plan_snapshot=plan_snap)
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
        print("APPROVAL_LEDGER|%s" %
              (json.dumps(res["approval"], ensure_ascii=False, sort_keys=True) if res.get("approval")
               else "NONE(未绑定批准身份: 额度只活在这一次 dispatch 的内存里; 本轮状态=%s%s)" %
                    (res["status"],
                     " code=" + res["code"] if res.get("code") else "")))
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
    # 单会话入口(--steps)也要能挂上同一本持久账: 现场包以外的一次性补测最容易从这里绕开额度。
    session_approval = None
    if a.approval_id or a.transport == "real":
        try:
            session_approval = open_approval(
                a.transport, Path(a.approval_root or default_approval_root()), a.approval_id,
                current_identities(plan_doc or {}, a.plan, a.repo_root),
                (plan_doc or {}).get("quota") or {}, boot_led, clock)
        except Stop as e:
            print("APPROVAL_GATE_REFUSED:%s|%s" % (e.code, e.detail), file=sys.stderr)
            boot_led.close()
            return 4
    # boot_led 必须活到会话结束: 动作后端的 ACTION/ACTION_WAITING 事件正是写进它的。
    try:
        res = exec_session(a.session, steps, a.out, a.transport, fixture, a.port,
                           a.total_deadline_seconds, clock, backend=backend,
                           motion_cap_seconds=a.motion_cap_seconds or None,
                           energize_cap_seconds=a.arm_energize_deadline_seconds or None,
                           cleanup_reserve_seconds=a.cleanup_reserve_seconds or None,
                           plan=plan_doc, approval=session_approval)
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
