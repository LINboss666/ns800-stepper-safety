#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R1 现场候选包的串口会话执行器（单一写者 / 原始字节先落盘 / 总截止时间）。

复用 P0 已证的串口纪律（`phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py`）：
  1) 端口在构造时一次配齐，**打开之后不写任何串口属性**（Windows pySerial 的同值赋值也会触发
     SetCommState，P0 轮实测这会损坏字节）；
  2) 每个响应的**原始字节先落盘**（`NN_<as>.rx.bin`），解析只读落盘内容；
  3) **单一写者**：一个会话一个进程持有端口，不重开、不 flush、不重试；
  4) **总截止时间** + 逐步截止时间，全部整数秒；超时=具名停机，不是重试。

`--transport stub` 用注入假端口 + 虚拟时钟跑**同一份代码路径**（离线正例与关键失败路径用它）。
`--transport real` 才 import pyserial，且必须给对令牌；本轮从未使用（hardware_actions=[]）。
"""
import argparse
import ast
import hashlib
import json
import sys
import time
from pathlib import Path

BAUD = 115200
SLACK_SECONDS = 3
PROMPT = b"msh "


class Stop(Exception):
    def __init__(self, code, detail=""):
        Exception.__init__(self, code + (":" + detail if detail else ""))
        self.code = code
        self.detail = detail


class SimClock:
    """虚拟时钟：sleep 只推进时间，不真等（stub 会话因此可在毫秒内跑完 10 s 观察窗）。"""

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
    def __init__(self, path, clock):
        self.f = open(path, "a", encoding="utf-8", newline="\n")
        self.clock = clock
        self.t0 = clock.monotonic()

    def ev(self, kind, **kw):
        rec = {"t_s": round(self.clock.monotonic() - self.t0, 3), "kind": kind}
        rec.update(kw)
        self.f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self.f.flush()

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass


class FakePort:
    """脚本化假端口：只有 read/write/feed/close，任何属性写入都当场报错。"""

    def __init__(self, script, ledger):
        object.__setattr__(self, "_chunks", list(script))
        object.__setattr__(self, "_buf", b"")
        object.__setattr__(self, "_closed", False)
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(self, "written", [])

    def feed(self, data):
        self._chunks.append(data)

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
        # 下划线开头是本对象的内部状态; 其余任何"打开之后写属性"的形态都当场报错
        # (真机上 pySerial 的 baudrate/timeout 等同名属性写入会触发 SetCommState)。
        if str(name).startswith("_"):
            object.__setattr__(self, name, value)
            return
        raise AssertionError("post-open port property write attempted: " + str(name))


def make_real_port(port_name, ledger):
    import serial                          # 只在现场真实分支导入
    ledger.ev("PORT_OPEN_ATTEMPT", port=port_name, baud=BAUD)
    return serial.Serial(port_name, BAUD, bytesize=8, parity="N", stopbits=1,
                         timeout=0, write_timeout=2,
                         xonxoff=False, rtscts=False, dsrdtr=False)


def sha_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def emit_text(raw_path, lines):
    """解析用的文本与原始字节同目录另存, 供上层做"落盘之后再判读"的复核。"""
    txt = Path(str(raw_path) + ".txt")
    txt.write_text("\n".join(lines), encoding="utf-8")
    return str(txt)


def read_until_prompt(port, deadline, led, raw_path, clock):
    buf = bytearray()
    with open(raw_path, "wb") as f:
        while True:
            chunk = port.read(512)
            if chunk:
                buf += chunk
                f.write(chunk)
                f.flush()
            if buf.endswith(b"\r\n" + PROMPT) or buf.endswith(b"\n" + PROMPT):
                break
            if clock.monotonic() > deadline:
                if not buf:
                    raise Stop("NO_BYTES_BEFORE_DEADLINE", "deadline=%s" % int(deadline))
                break
            clock.sleep(0.005)
    return bytes(buf).decode("utf-8", "replace").splitlines(), len(buf)


def watch_window(port, raw_path, window_end, led, clock, seconds):
    total = 0
    lines = []
    with open(raw_path, "wb") as f:
        while clock.monotonic() < window_end:
            chunk = port.read(512)
            if chunk:
                f.write(chunk)
                f.flush()
                total += len(chunk)
                lines += chunk.decode("utf-8", "replace").splitlines()
            else:
                clock.sleep(0.02)
    led.ev("WATCH_DONE", seconds=seconds, rx_bytes=total)
    return lines, total


def assert_step(st, lines, asname, led):
    text = "\n".join(lines)
    miss = [n for n in st.get("expect_contains", []) if n not in text]
    if miss:
        led.ev("ASSERT_FAIL", **{"as": asname, "missing": miss})
        return {"verdict": "STOP", "code": "EXPECTATION_NOT_MET", "missing": miss}
    anyn = st.get("expect_any", [])
    if anyn and not any(n in text for n in anyn):
        led.ev("ASSERT_FAIL", **{"as": asname, "any_of": anyn})
        return {"verdict": "STOP", "code": "EXPECTATION_NOT_MET_ANY", "any_of": anyn}
    banned = [n for n in st.get("expect_absent", []) if n in text]
    if banned:
        led.ev("ASSERT_FAIL", **{"as": asname, "present": banned})
        return {"verdict": "STOP", "code": "FORBIDDEN_PRESENT", "present": banned}
    return {"verdict": "OK", "asserted": bool(anyn or st.get("expect_contains"))}


def read_manual(name, fixture, allow_prompt):
    got = fixture.get("manual", {}).get(name)
    if got is None:
        if not allow_prompt:
            raise Stop("MANUAL_INPUT_REQUIRED", name)
        got = input("OPERATOR CONFIRM [%s] (yes/no): " % name).strip()
    if str(got).strip().lower() not in ("yes", "y", "true", "1", "confirmed"):
        raise Stop("MANUAL_GATE_DENIED", "%s=%s" % (name, got))
    return str(got)


def exec_session(session_id, steps, outdir, transport, fixture, port_name,
                 total_deadline_seconds, clock):
    out = Path(outdir) / session_id
    out.mkdir(parents=True, exist_ok=True)
    led = Ledger(out / "ledger.jsonl", clock)
    results = []
    silent = bool(fixture.get("inject", {}).get("port_silent"))
    if transport == "stub":
        # 启动横幅**不带提示符尾缀**: 它是第一条命令等待期间的字节, 不能提前把读窗口"合拢",
        # 否则每条命令的响应会整体错后一步(自测里会表现为假通过)。
        script = [l.encode("utf-8") + b"\r\n" for l in fixture.get("boot_bytes", [])]
        if silent:
            script = [None]
        port = FakePort(script, led)
    else:
        port = make_real_port(port_name, led)
    hard_deadline = clock.monotonic() + total_deadline_seconds
    try:
        led.ev("PORT_OPEN", transport=transport, port=port_name if transport == "real" else None)
        for idx, st in enumerate(steps, start=1):
            if clock.monotonic() > hard_deadline:
                raise Stop("TOTAL_DEADLINE_EXCEEDED", "%ds" % total_deadline_seconds)
            asname = st.get("as") or ("step%02d" % idx)
            raw = out / ("%02d_%s.rx.bin" % (idx, asname))
            if "manual" in st:
                val = read_manual(st["manual"], fixture, transport == "real")
                led.ev("MANUAL", **{"as": asname, "name": st["manual"], "value": val})
                results.append({"as": asname, "kind": "manual", "verdict": "OK", "value": val})
                continue
            cmd = st["cmd"]
            if cmd == "__watch__":
                want = int(st.get("seconds", 10))
                if transport == "stub" and not silent:
                    resp = fixture.get("responses", {}).get(st.get("key") or "__watch__")
                    if resp is None:
                        raise Stop("STUB_MISS", "__watch__")
                    port.feed(("\r\n".join(resp) + "\r\n").encode("utf-8"))
                lines, nbytes = watch_window(port, raw, clock.monotonic() + want, led, clock, want)
                v = assert_step(st, lines, asname, led)
                results.append(dict({"as": asname, "kind": "watch", "seconds": want,
                                     "rx_bytes": nbytes, "txt": emit_text(raw, lines)}, **v))
                continue
            if cmd == "__approved_reboot__":
                led.ev("REBOOT_REQUEST", **{"as": asname})
                if transport == "real":
                    read_manual("approved_reboot_performed", fixture, True)
                results.append({"as": asname, "kind": "reboot", "verdict": "OK"})
                continue
            port.write((cmd + "\r\n").encode("utf-8"))
            if transport == "stub":
                if silent:
                    pass                      # 注入: 板子不回话 -> 只能靠截止时间具名停机, 不许重试
                else:
                    resp = fixture.get("responses", {}).get(st.get("key") or cmd)
                    if resp is None:
                        raise Stop("STUB_MISS", cmd)
                    port.feed(("\r\n".join(resp) + "\r\n" + PROMPT.decode()).encode("utf-8"))
            step_deadline = min(hard_deadline,
                                clock.monotonic() + int(st.get("seconds", 3)) + SLACK_SECONDS)
            lines, nbytes = read_until_prompt(port, step_deadline, led, raw, clock)
            v = assert_step(st, lines, asname, led)
            results.append(dict({"as": asname, "kind": "command", "cmd": cmd,
                                 "rx_bytes": nbytes, "rx_sha256": sha_of(raw),
                                 "txt": emit_text(raw, lines)}, **v))
            if v["verdict"] == "STOP":
                led.ev("SESSION_STOP", **{"as": asname, "code": v["code"]})
                return {"session": session_id, "status": "STOP", "results": results}
        led.ev("SESSION_OK", session=session_id, steps=len(results))
        return {"session": session_id, "status": "OK", "results": results}
    finally:
        try:
            port.close()
            led.ev("PORT_CLOSED")
        except Exception as e:
            led.ev("CLEANUP_ERROR", err=repr(e))
        led.close()


def static_port_discipline(src_path):
    """F-05 静态判据：本文件里不存在"给端口对象写属性"的语句形态。"""
    tree = ast.parse(Path(src_path).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            base = node.value
            name = getattr(base, "id", getattr(base, "attr", ""))
            if name == "port":
                bad.append("port.%s" % node.attr)
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transport", choices=["stub", "real"], default="stub")
    ap.add_argument("--steps", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fixture")
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--total-deadline-seconds", type=int, default=120)
    ap.add_argument("--approval-token")
    ap.add_argument("--expected-token")
    ap.add_argument("--check-static-port-discipline", action="store_true")
    a = ap.parse_args(argv)
    if a.check_static_port_discipline:
        bad = static_port_discipline(__file__)
        print("F05_STATIC_PORT_DISCIPLINE|%s|post_open_attribute_writes=%s" %
              ("FAIL" if bad else "PASS", ",".join(bad) if bad else "none"))
        return 1 if bad else 0
    if a.transport == "real":
        if not a.approval_token or a.approval_token != a.expected_token:
            print("REAL_TRANSPORT_REFUSED:approval_token_missing_or_mismatch", file=sys.stderr)
            return 4
    elif not a.fixture:
        print("STUB_REFUSED:fixture_required", file=sys.stderr)
        return 4
    steps = json.loads(Path(a.steps).read_text(encoding="utf-8"))
    fixture = json.loads(Path(a.fixture).read_text(encoding="utf-8")) if a.fixture else {}
    fixture.setdefault("responses", {})
    clock = SimClock() if a.transport == "stub" else RealClock()
    try:
        res = exec_session(a.session, steps, a.out, a.transport, fixture, a.port,
                           a.total_deadline_seconds, clock)
    except Stop as e:
        print(json.dumps({"session": a.session, "status": "STOP", "code": e.code,
                          "detail": e.detail, "results": []}, ensure_ascii=False))
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
