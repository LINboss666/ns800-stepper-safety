#!/usr/bin/env python3
"""m5_field_serial.py - M5 现场串口执行与判读 (2026-10-29)

两阶段、同一批准包内连续执行:
  archive  P0 身份/静止前置 + P1 旧日志完整导出、判读、有条件的一次清区
  fault    P2 运行中一次软件故障 + P3 新日志读回 + P4 一次人工恢复(不自动起转)

纪律:
  - 每条命令的原始 RX **先落盘再解析**; 记录单调时标的 TX/RX 时刻;
  - archive 阶段只有归档判读 ACCEPT 才允许 CLEAR_ALLOWED=YES;
    空间不足且未给 --allow-clear 时停止, 不清任何区;
  - fault 阶段拒绝在 archive_state.json 未 PASS 或空间不足(未清)时启动;
  - safety_trip 只发一次; 不先发普通 stop; 最迟 t=5s 发一次兜底 motor_disarm;
  - 新日志未通过判读时不 fault_reset / system_selftest; 不自动重试;
  - 所有文本按原始字节保存; 解析只做只读提取, 不回写目标。

--selftest 用假链路+假判读器覆盖关键顺序、3s/5s 截止、归档失败禁止 clear、
新日志失败禁止恢复; 不接触 COM5/DAP。

用法:
  python m5_field_serial.py archive --port COM5 --out <dir> --expect-banner <s> [--allow-clear]
  python m5_field_serial.py fault   --port COM5 --out <dir>
  python m5_field_serial.py --selftest
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CHECKER = os.path.join(REPO_ROOT, "tests", "host", "m5_log_archive_check.py")
NEED_SLOTS = 400          # pre200+post100 + 余量
NS_LOG_SLOTS = 4096
POLL_ROUNDS = 16
POLL_INTERVAL = 2.0
POLL_DEADLINE = 45.0


class Abort(Exception):
    pass


def _text(raw):
    return raw.replace(b"\x00", b"<NUL>").decode("utf-8", "replace").replace("\r\n", "\n")


def find1(pattern, text, what):
    m = re.search(pattern, text)
    if not m:
        raise Abort("missing %s in output" % what)
    return m.group(1)


class Recorder:
    def __init__(self, out_dir, phase):
        self.dir = os.path.join(out_dir, "raw", phase)
        os.makedirs(self.dir, exist_ok=True)
        self.events = []
        self.n = 0

    def save(self, cmd, raw, t_tx, t_rx):
        self.n += 1
        name = "%03d_%s.bin" % (self.n, re.sub(r"[^A-Za-z0-9_.-]+", "_", cmd)[:60])
        path = os.path.join(self.dir, name)
        with open(path, "wb") as fh:
            fh.write(raw)
        self.events.append({
            "seq": self.n, "cmd": cmd, "file": path,
            "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "t_tx": t_tx, "t_rx": t_rx,
        })
        return path

    def write_index(self):
        with open(os.path.join(self.dir, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(self.events, fh, indent=2)
            fh.write("\n")


class SerialLink:
    """pyserial 链路; 每条命令前丢弃未读字节, 以静默间隔结束读取。"""
    def __init__(self, port, baud=115200):
        import serial
        self.ser = serial.Serial(port, baud, timeout=0.05)
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def send(self, cmd, timeout, quiet=0.5, done_marker=None):
        self.ser.reset_input_buffer()
        t_tx = time.monotonic()
        self.ser.write((cmd + "\r\n").encode("ascii"))
        buf = bytearray()
        last = t_tx
        end = t_tx + timeout
        while time.monotonic() < end:
            chunk = self.ser.read(max(1, self.ser.in_waiting))
            if chunk:
                buf.extend(chunk)
                last = time.monotonic()
            elif (time.monotonic() - last >= quiet and buf and
                  (done_marker is None or done_marker in bytes(buf))):
                break
            else:
                time.sleep(0.02)
        return bytes(buf), t_tx, time.monotonic()

    def close(self):
        self.ser.close()


class Runner:
    def __init__(self, link, out_dir, checker_cmd, clock=time.monotonic,
                 sleep=time.sleep):
        self.link = link
        self.out = out_dir
        self.checker_cmd = checker_cmd
        self.clock = clock
        self.sleep = sleep

    # ---------- 基础 ----------
    def q(self, rec, cmd, timeout=4.0, quiet=0.5, done_marker=None):
        raw, t0, t1 = self.link.send(cmd, timeout, quiet, done_marker)
        rec.save(cmd, raw, t0, t1)
        text = _text(raw)
        if done_marker is not None and done_marker not in text:
            raise Abort("command did not report completion: %s" % cmd)
        return text

    def wait_until(self, t_target):
        while self.clock() < t_target:
            self.sleep(min(0.05, t_target - self.clock()))

    def run_checker(self, batches, expected, start, tag):
        if callable(self.checker_cmd):
            rc, out = self.checker_cmd(batches, expected, start, tag)
            with open(os.path.join(self.out, "%s_checker.txt" % tag), "w",
                      encoding="utf-8") as fh:
                fh.write(out)
            return rc, out
        args = list(self.checker_cmd) + [
            "--expected", str(expected), "--start", str(start), "--json",
            os.path.join(self.out, "%s_meta.json" % tag),
            "--csv", os.path.join(self.out, "%s.csv" % tag)]
        for first, count, path in batches:
            args += ["--batch", str(first), str(count), path]
        proc = subprocess.run(args, capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        with open(os.path.join(self.out, "%s_checker.txt" % tag), "w",
                  encoding="utf-8") as fh:
            fh.write(out)
        return proc.returncode, out

    # ---------- 解析 ----------
    @staticmethod
    def parse_logstat(text):
        m = re.search(r"slots=(\d+)/(\d+) valid=(\d+) bad=(\d+)", text)
        q = re.search(r"queued=(\d+) completed=(\d+) failed=(\d+) "
                      r"queue_dropped=(\d+) last_error=(-?\d+)", text)
        if not m or not q:
            raise Abort("flash_logstat parse failed")
        return {"used": int(m.group(1)), "slots": int(m.group(2)),
                "valid": int(m.group(3)), "bad": int(m.group(4)),
                "queued": int(q.group(1)), "completed": int(q.group(2)),
                "failed": int(q.group(3)), "dropped": int(q.group(4)),
                "last_error": int(q.group(5))}

    @staticmethod
    def parse_blackbox(text):
        m = re.search(r"\[BB\] state=(\d+) session=(\d+) written=(\d+) "
                      r"last_err=(-?\d+)", text)
        f = re.search(r"\[BB\] flash: used=(\d+) valid=(\d+) bad=(\d+) "
                      r"acc=(\d+) done=(\d+) fail=(\d+) drop=(\d+)", text)
        w = re.search(r"pre_eff=(\d+) post_eff=(\d+) frames", text)
        if not m or not f:
            raise Abort("blackbox_status parse failed")
        return {"state": int(m.group(1)), "session": int(m.group(2)),
                "written": int(m.group(3)), "last_err": int(m.group(4)),
                "used": int(f.group(1)), "valid": int(f.group(2)),
                "bad": int(f.group(3)), "acc": int(f.group(4)),
                "done": int(f.group(5)), "fail": int(f.group(6)),
                "drop": int(f.group(7)),
                "pre_eff": int(w.group(1)) if w else 0,
                "post_eff": int(w.group(2)) if w else 0}

    @staticmethod
    def parse_safety_state(text):
        m = re.search(r"\[SS\] state=([A-Z_]+) fault_code=(\d+)", text)
        if not m:
            raise Abort("safety_state parse failed")
        return {"state": m.group(1), "fault": int(m.group(2))}

    @staticmethod
    def parse_safety_status(text):
        req = re.search(r"request=(ENABLE\(HIGH\)|DISABLE\(LOW\)) pad=(-?\d+)", text)
        fb = re.search(r"FB=(-?\d+) -> verdict=(\w+)", text)
        if not req:
            raise Abort("safety_status request parse failed")
        return {"request": req.group(1), "pad": int(req.group(2)),
                "verdict": fb.group(2) if fb else "UNKNOWN"}

    @staticmethod
    def parse_motor(text):
        m = re.search(r"\[MOT\] state=(\d+)", text)
        hz = re.search(r"cur_hz=(\d+) tgt_hz=(\d+)", text)
        a = re.search(r"armed=(\d+)", text)
        g = re.search(r"arm_gate_mask=0x([0-9A-Fa-f]+)", text)
        if not m or not hz:
            raise Abort("motor_status parse failed")
        return {"state": int(m.group(1)), "cur": int(hz.group(1)),
                "tgt": int(hz.group(2)), "armed": int(a.group(1)) if a else -1,
                "gate": int(g.group(1), 16) if g else -1}

    # ---------- P0 ----------
    def preflight(self, rec, expect_banner=None, allow_apply=False):
        text = {}
        for cmd in ("system_status", "version", "safety_state", "safety_status",
                    "motor_status", "step_status", "tmc_config_show",
                    "diag_status", "config_show", "sensor_snapshot",
                    "blackbox_status", "flash_logstat"):
            text[cmd] = self.q(rec, cmd)
        banner_seen = text["version"] + text["system_status"]
        if expect_banner and expect_banner not in banner_seen:
            raise Abort("banner mismatch: %r not in version/system_status" % expect_banner)
        ss = self.parse_safety_state(text["safety_state"])
        st = self.parse_safety_status(text["safety_status"])
        mo = self.parse_motor(text["motor_status"])
        if ss["state"] != "READY" or ss["fault"] != 0:
            raise Abort("not READY/fault0: %s" % ss)
        if mo["armed"] != 0 or mo["tgt"] != 0 or mo["cur"] != 0:
            raise Abort("motor not at rest: %s" % mo)
        if st["request"] != "DISABLE(LOW)" or st["pad"] != 0 or st["verdict"] != "OK":
            raise Abort("drive request not LOW/confirmed: %s" % st)
        if "state=CONFIGURED" not in text["tmc_config_show"]:
            if not allow_apply:
                raise Abort("TMC not CONFIGURED; package did not allow one apply")
            text["tmc_config_apply"] = self.q(rec, "tmc_config_apply CONFIRM", 30.0, 1.0)
            if "state=CONFIGURED" not in text["tmc_config_show"] and \
               "CONFIGURED" not in text["tmc_config_apply"]:
                raise Abort("one apply did not reach CONFIGURED")
        if "MONITOR_ONLY" not in text["diag_status"]:
            raise Abort("diagnosis not MONITOR_ONLY")
        if "THEORETICAL" not in text["diag_status"]:
            raise Abort("ADC calibration source not disclosed as THEORETICAL")
        bb = self.parse_blackbox(text["blackbox_status"])
        f = self.parse_logstat(text["flash_logstat"])
        if bb["state"] != 0 or bb["acc"] != bb["done"]:
            raise Abort("blackbox/flash queue not idle: %s" % bb)
        if f["valid"] != f["used"] or f["bad"] != 0:
            raise Abort("log not all-valid: %s" % f)
        return text, bb, f

    # ---------- P1 ----------
    def _archive_manifest(self, used, valid, bad, batches, checker):
        files = []
        for first, count, path in batches:
            b = open(path, "rb").read()
            files.append({"first": first, "count": count,
                          "path": os.path.relpath(path, self.out),
                          "bytes": len(b),
                          "sha256": hashlib.sha256(b).hexdigest()})
        extra = []
        for name in ("old_archive.csv", "old_archive_checker.txt", "raw/archive/index.json"):
            p = os.path.join(self.out, name)
            if os.path.exists(p):
                b = open(p, "rb").read()
                extra.append({"path": name, "bytes": len(b),
                              "sha256": hashlib.sha256(b).hexdigest()})
        manifest = {"used": used, "valid": valid, "bad": bad,
                    "batches": files, "files": extra,
                    "checker": checker.strip()}
        p = os.path.join(self.out, "old_archive_manifest.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")
        return manifest

    def _second_copy(self, second, manifest):
        if not second:
            raise Abort("two-copy archive required before clear: --second-copy missing")
        src = os.path.abspath(self.out)
        dst = os.path.abspath(second)
        try:
            common = os.path.commonpath([src, dst])
        except ValueError:
            common = None              # 不同盘符: 必然分离
        if common in (src, dst):
            raise Abort("second copy must be a separate directory")
        if os.path.exists(dst) and (not os.path.isdir(dst) or os.listdir(dst)):
            raise Abort("second copy directory must be absent or empty: %s" % dst)
        os.makedirs(dst, exist_ok=True)
        rels = [f["path"] for f in manifest["batches"]]
        rels += [f["path"] for f in manifest["files"]]
        rels += ["old_archive_manifest.json"]
        for rel in rels:
            s = os.path.join(src, rel)
            d = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            sb = open(s, "rb").read()
            db = open(d, "rb").read()
            if hashlib.sha256(sb).hexdigest() != hashlib.sha256(db).hexdigest():
                raise Abort("second copy mismatch: %s" % rel)
        marker = {"copies": 2, "files": len(rels), "used": manifest["used"],
                  "checker": manifest["checker"]}
        for root in (src, dst):
            with open(os.path.join(root, "COPY_VERIFIED.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(marker, fh, indent=2)
                fh.write("\n")
        return dst

    def archive(self, expect_banner=None, allow_clear=False, allow_apply=False,
                second_copy=None):
        os.makedirs(self.out, exist_ok=True)
        rec = Recorder(self.out, "archive")
        text, bb0, f0 = self.preflight(rec, expect_banner, allow_apply)
        used = f0["used"]
        batches = []
        first = 0
        while first < used:
            count = min(100, used - first)
            raw = self.q(rec, "flash_dump %d %d" % (first, count), 8.0, 0.8)
            path = rec.events[-1]["file"]
            batches.append((first, count, path))
            first += count
        if used == 0:
            rc, out = 0, ("ARCHIVE=ACCEPT rows=0 batches=0 (log empty)\n"
                          "CLEAR_ALLOWED=YES\n")
            with open(os.path.join(self.out, "old_archive_checker.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write(out)
        else:
            rc, out = self.run_checker(batches, used, 0, "old_archive")
        if rc != 0 or "ARCHIVE=ACCEPT" not in out or "CLEAR_ALLOWED=YES" not in out:
            raise Abort("old archive REJECT (no clear): %s" % out.strip())
        # 导出后计数不得变化
        f1 = self.parse_logstat(self.q(rec, "flash_logstat"))
        bb1 = self.parse_blackbox(self.q(rec, "blackbox_status"))
        for k in ("used", "valid", "bad"):
            if f1[k] != f0[k]:
                raise Abort("flash %s changed during archive" % k)
        for k in ("acc", "done", "fail", "drop", "session"):
            if bb1[k] != bb0[k]:
                raise Abort("blackbox %s changed during archive" % k)
        free = NS_LOG_SLOTS - used
        rec.write_index()
        manifest = self._archive_manifest(used, f1["valid"], f1["bad"], batches, out)
        clear_done = False
        second_dst = None
        if free < NEED_SLOTS:
            if not allow_clear:
                raise Abort("log nearly full (free=%d) and --allow-clear not given" % free)
            second_dst = self._second_copy(second_copy, manifest)
            self.q(rec, "flash_logclear ERASE", 600.0, 3.0,
                   done_marker="[FLASH] clear event partition")
            f2 = self.parse_logstat(self.q(rec, "flash_logstat"))
            if f2["used"] != 0 or f2["valid"] != 0 or f2["bad"] != 0 or \
               f2["queued"] != 0 or f2["failed"] != 0 or f2["dropped"] != 0:
                raise Abort("clear did not reach idle 0/0/0: %s" % f2)
            clear_done = True
        rec.write_index()
        state = {"archive_accept": True, "used_before": used, "used_after": used,
                 "free": NS_LOG_SLOTS - used, "clear_done": clear_done,
                 "batches": len(batches), "csv": os.path.join(self.out, "old_archive.csv"),
                 "manifest": os.path.join(self.out, "old_archive_manifest.json"),
                 "second_copy": second_dst,
                 "checker": out.strip()}
        with open(os.path.join(self.out, "archive_state.json"), "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
        print("ARCHIVE=PASS used=%d clear=%s free_after=%d"
              % (used, "YES" if clear_done else "NO",
                 NS_LOG_SLOTS - (0 if clear_done else used)))
        return state

    # ---------- P2/P3/P4 ----------
    def fault(self, clear_target=True):
        st_path = os.path.join(self.out, "archive_state.json")
        if not os.path.exists(st_path):
            raise Abort("archive_state.json missing; run archive phase first")
        state = json.load(open(st_path, encoding="utf-8"))
        if not state.get("archive_accept"):
            raise Abort("archive not accepted")
        if not state.get("clear_done") and state.get("free", 0) < NEED_SLOTS:
            raise Abort("log space insufficient and clear not done")
        rec = Recorder(self.out, "fault")
        # Approved P2 starts from target 0; clear any stale command value first.
        if clear_target:
            self.q(rec, "motor_target 0")
        text, bb0, f0 = self.preflight(rec)
        mo = self.parse_motor(text["motor_status"])
        if mo["gate"] != 0:
            raise Abort("arm gate not clear before P2 (mask=0x%X); no motor command sent"
                        % (mo["gate"] & 0xFFFFFFFF))
        pre_used = f0["used"]
        # P2: arm must be confirmed before target/start (a refused arm must not set target)
        self.q(rec, "motor_dir 0")
        self.q(rec, "motor_arm", 10.0, 1.0)
        armed = self.parse_motor(self.q(rec, "motor_status"))
        if armed["armed"] != 1:
            raise Abort("arm refused before target/start: %s" % armed)
        self.q(rec, "motor_target 300")
        raw, t_start, _ = self.link.send("motor_start", 10.0, 1.0)
        rec.save("motor_start", raw, t_start, self.clock())
        self.wait_until(t_start + 1.0)
        m1 = self.q(rec, "motor_status")
        s1 = self.q(rec, "sensor_snapshot")
        mv = self.parse_motor(m1)
        if mv["state"] not in (1, 2) or mv["tgt"] != 300 or mv["cur"] == 0:
            raise Abort("t=1s not running at 300 Hz: %s" % mv)
        self.wait_until(t_start + 3.0)
        raw, t_trip, _ = self.link.send("safety_trip CONFIRM", 10.0, 1.0)
        rec.save("safety_trip CONFIRM", raw, t_trip, self.clock())
        trip_text = _text(raw)
        if "EVT_SOFT_FAULT posted rc=0" not in trip_text:
            raise Abort("safety_trip not accepted: %r" % trip_text)
        after_trip = {
            "motor_status": self.q(rec, "motor_status"),
            "safety_state": self.q(rec, "safety_state"),
            "safety_status": self.q(rec, "safety_status"),
        }
        # Safety 线程整理事件可能晚于命令返回; 给一个有界观察窗, 不延长运动
        for _ in range(3):
            ss_now = self.parse_safety_state(after_trip["safety_state"])
            if ss_now["state"] == "FAULT_LATCHED" and ss_now["fault"] == 9:
                break
            self.sleep(0.2)
            after_trip["safety_state"] = self.q(rec, "safety_state")
            after_trip["safety_status"] = self.q(rec, "safety_status")
        self.wait_until(t_start + 5.0)
        raw, t_disarm, _ = self.link.send("motor_disarm", 10.0, 1.0)
        rec.save("motor_disarm", raw, t_disarm, self.clock())
        # P3: 锁存拒绝
        if self.parse_safety_state(after_trip["safety_state"])["state"] != "FAULT_LATCHED" or \
           self.parse_safety_state(after_trip["safety_state"])["fault"] != 9:
            raise Abort("not FAULT_LATCHED code 9 after trip: %s"
                        % after_trip["safety_state"])
        st = self.parse_safety_status(self.q(rec, "safety_status"))
        if st["request"] != "DISABLE(LOW)" or st["pad"] != 0:
            raise Abort("output not LOW after trip: %s" % st)
        arm_text = self.q(rec, "motor_arm", 10.0, 1.0)
        step_text = self.q(rec, "step_status")
        if "REFUSED" not in arm_text or "enabled=0" not in step_text:
            raise Abort("fault-state arm was not refused: %r / %r"
                        % (arm_text, step_text))
        # 等待黑匣子写完, 有界轮询
        t_end = self.clock() + POLL_DEADLINE
        bb = None
        flash = None
        for _ in range(POLL_ROUNDS):
            if self.clock() >= t_end:
                break
            bb = self.parse_blackbox(self.q(rec, "blackbox_status"))
            flash = self.parse_logstat(self.q(rec, "flash_logstat"))
            if bb["state"] == 0 and bb["acc"] == bb["done"] and \
               flash["used"] > pre_used and flash["valid"] > pre_used:
                break
            self.sleep(POLL_INTERVAL)
        if bb is None or bb["state"] != 0 or bb["acc"] != bb["done"]:
            raise Abort("blackbox did not idle: %s" % bb)
        if bb["session"] != bb0["session"] + 1:
            raise Abort("blackbox session not +1: %d -> %d"
                        % (bb0["session"], bb["session"]))
        if bb["fail"] != bb0["fail"] or bb["drop"] != bb0["drop"] or bb["last_err"] != 0:
            raise Abort("blackbox failure counters moved: %s" % bb)
        delta = flash["used"] - pre_used
        if delta <= 0 or flash["valid"] - pre_used != delta or flash["bad"] != f0["bad"]:
            raise Abort("new slots inconsistent: pre=%d now=%s" % (pre_used, flash))
        if bb["written"] - bb0["written"] != delta:
            raise Abort("written delta %d != used delta %d"
                        % (bb["written"] - bb0["written"], delta))
        batches = []
        first = pre_used
        while first < flash["used"]:
            count = min(100, flash["used"] - first)
            self.q(rec, "flash_dump %d %d" % (first, count), 8.0, 0.8)
            batches.append((first, count, rec.events[-1]["file"]))
            first += count
        rc, out = self.run_checker(batches, delta, pre_used, "new_log")
        if rc != 0 or "ARCHIVE=ACCEPT" not in out:
            raise Abort("new log readback REJECT: %s" % out.strip())
        events = self.verify_new_log(os.path.join(self.out, "new_log.csv"), bb["session"])
        # P4: 仅在新日志读回通过后恢复
        st = self.parse_safety_status(self.q(rec, "safety_status"))
        if st["request"] != "DISABLE(LOW)" or st["pad"] != 0:
            raise Abort("output not disabled before recovery: %s" % st)
        reset_text = self.q(rec, "fault_reset", 30.0, 1.0)
        if "MANUAL_CLEAR" not in reset_text:
            raise Abort("fault_reset did not reach MANUAL_CLEAR: %r" % reset_text)
        if self.parse_motor(self.q(rec, "motor_status"))["armed"] != 0:
            raise Abort("armed after fault_reset")
        stest_text = self.q(rec, "system_selftest", 30.0, 1.0)
        ss = self.parse_safety_state(self.q(rec, "safety_state"))
        if "required" not in stest_text or ss["state"] != "READY" or ss["fault"] != 0:
            raise Abort("system_selftest did not restore READY: %s / %r" % (ss, stest_text))
        final = {c: self.q(rec, c) for c in
                 ("safety_state", "safety_status", "motor_status", "step_status")}
        if self.parse_motor(final["motor_status"])["armed"] != 0 or \
           self.parse_safety_status(final["safety_status"])["request"] != "DISABLE(LOW)":
            raise Abort("final state not disarmed/LOW")
        rec.write_index()
        result = {
            "t_start": t_start, "t_trip": t_trip, "t_disarm": t_disarm,
            "trip_minus_start_s": round(t_trip - t_start, 3),
            "disarm_minus_start_s": round(t_disarm - t_start, 3),
            "session_before": bb0["session"], "session_after": bb["session"],
            "used_before": pre_used, "frames": delta,
            "new_log_events": events,
            "cal_source": "THEORETICAL",
            "recovery": "MANUAL_CLEAR -> READY",
        }
        with open(os.path.join(self.out, "fault_result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
        print("FAULT_CLOSURE=PASS trip=%.3fs disarm=%.3fs frames=%d session=%d"
              % (t_trip - t_start, t_disarm - t_start, delta, bb["session"]))
        return result

    @staticmethod
    def verify_new_log(csv_path, session):
        with open(csv_path, encoding="ascii", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise Abort("new log CSV empty")
        ev = [int(r["event"]) for r in rows]
        first9 = ev.index(9) if 9 in ev else -1
        if first9 <= 0 or any(e not in (0, 9) for e in ev):
            raise Abort("new log event semantics: %s" % ev[:20])
        if any(e == 0 for e in ev[first9:]):
            raise Abort("event 0 after first event 9")
        if not any(int(r["step_hz"]) > 0 for r in rows[:first9]):
            raise Abort("no RUN-context pre-fault row (step_hz>0)")
        if any(int(r["session"]) != session for r in rows):
            raise Abort("session id mismatch in new log")
        times = [int(r["time_ms"]) for r in rows]
        if times != sorted(times):
            raise Abort("new log time_ms not monotonic")
        return {"rows": len(rows), "pre_event0": first9, "post_event9": len(rows) - first9}


# ============================== 离线自测 ==============================
class FakeClock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t
    def sleep(self, dt): self.t += max(0.0, dt)


class FakeDevice:
    """最小目标机模型: 只为验证脚本顺序/截止/门禁, 不代表真实固件行为。"""
    def __init__(self):
        self.used = 0
        self.session = 0
        self.written = 0
        self.acc = self.done = 0
        self.fail = self.drop = 0
        self.bb_state = 0
        self.frames = {}
        self.motor = {"state": 0, "cur": 0, "tgt": 0, "armed": 0}
        self.gate = 0
        self.safety = "READY"
        self.fault = 0
        self.pad = 0
        self.req = "DISABLE(LOW)"
        self.trip_count = 0
        self.cmds = []

    def text(self, cmd):
        if cmd == "system_status": return "FAKEBANNER build M5\r\n"
        if cmd == "version": return " / | \\     5.3.0 build FAKEBANNER Oct 29 2026 00:00:00\r\n"
        if cmd == "safety_state":
            return "[SS] state=%s fault_code=%d degraded=0x00\n" % (self.safety, self.fault)
        if cmd == "safety_status":
            return ("[SAFETY] ready=YES first_err=0\n"
                    "[SAFETY] request=%s pad=%d expected_FB=HIGH FB=1 -> verdict=OK\n"
                    % (self.req, self.pad))
        if cmd == "motor_status":
            return ("[MOT] state=%d name=OTHER\n"
                    "[MOT] cur_hz=%d tgt_hz=%d accel=2000 decel=2000 dir=0\n"
                    "[MOT] armed=%d drv_en_request=1 health=OK\n"
                    "[MOT] arm_gate_mask=0x%X\n"
                    % (self.motor["state"], self.motor["cur"], self.motor["tgt"],
                       self.motor["armed"], self.gate))
        if cmd == "step_status": return "[STEP] enabled=0 last_stop=not-attempted\n"
        if cmd == "tmc_config_show": return "[TMC] state=CONFIGURED snapshot_valid=1 last_err=0\n"
        if cmd == "diag_status":
            return ("[DIAG] mode=MONITOR_ONLY verdict=NORMAL\n"
                    "[DIAG] adc_cal_source=THEORETICAL (NOT a measured current)\n"
                    "[DIAG] threshold_validation=NOT_ESTABLISHED\n")
        if cmd == "config_show": return "[CFG] cal: offset=1650.0 mV gain=0.600 V/A source=THEORETICAL(1)\n"
        if cmd == "sensor_snapshot": return "[SENSOR] seq=1 tick=1 ax=1 ay=2 az=3\n"
        if cmd == "blackbox_status":
            return ("[BB] state=%d session=%d written=%d last_err=0 health=OK\n"
                    "[BB] trigger drops: early(pre-consumption)=0 busy(capturing)=0\n"
                    "[BB] write drops=%d frames (submit retries exhausted)\n"
                    "[BB] window: pre_eff=200 post_eff=100 frames (pre_n_latched=0)\n"
                    "[BB] flash: used=%d valid=%d bad=0 acc=%d done=%d fail=%d drop=%d\n"
                    % (self.bb_state, self.session, self.written, self.drop,
                       self.used, self.used, self.acc, self.done, self.fail, self.drop))
        if cmd == "flash_logstat":
            return ("slots=%d/4096 valid=%d bad=0\n"
                    "queued=%d completed=%d failed=%d queue_dropped=0 last_error=0\n"
                    % (self.used, self.used, self.acc, self.done, self.fail))
        if cmd.startswith("flash_dump "):
            _, a, b = cmd.split()
            a, b = int(a), int(b)
            out = ("slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,"
                   "az_mg,step_hz,vibration_mg,flags\n")
            for s in range(a, a + b):
                if s not in self.frames:
                    out += "slot %d empty\n" % s
                else:
                    f = self.frames[s]
                    out += ("%d,%d,%d,%d,%d,31,60,200,1,2,3,%d,900,1\n"
                            % (s, s + 1, f[0], s * 10, f[1], f[2]))
            return out
        if cmd == "motor_dir 0":
            return "OK\n"
        if cmd == "motor_arm":
            if self.safety == "FAULT_LATCHED":
                return "[MOT] arm REFUSED (gates failing)\n[MOT] arm_gate_mask=0x2\n"
            self.motor["armed"] = 1
            return "[MOT] armed\n"
        if cmd.startswith("motor_target "):
            self.motor["tgt"] = int(cmd.split()[1])
            return "[MOT] set target %s Hz: OK\n" % cmd.split()[1]
        if cmd == "motor_start":
            self.motor["state"] = 1
            self.motor["cur"] = 300
            return "[MOT] started\n"
        if cmd == "safety_trip CONFIRM":
            self.trip_count += 1
            self.safety, self.fault = "FAULT_LATCHED", 9
            self.motor = {"state": 4, "cur": 0, "tgt": 0, "armed": 0}
            self.req, self.pad = "DISABLE(LOW)", 0
            self.session += 1
            self.bb_state = 0
            for i in range(300):
                ev = 0 if i < 200 else 9
                st = 300 if ev == 0 else 0
                self.frames[self.used + i] = (self.session, ev, st)
            self.used += 300
            self.written += 300
            self.acc += 300
            self.done += 300
            return "[SS] safety_trip: EVT_SOFT_FAULT posted rc=0 (software event; protection ran in this call; persistence NOT implied)\n"
        if cmd == "motor_disarm":
            self.motor["armed"] = 0
            return "[MOT] disarmed\n"
        if cmd == "fault_reset":
            if self.safety != "FAULT_LATCHED":
                return "[SS] fault_reset REFUSED\n"
            self.safety, self.fault = "MANUAL_CLEAR", 0
            return "[SS] ==> MANUAL_CLEAR\n"
        if cmd == "system_selftest":
            self.safety = "READY"
            return "[SS] self-test done: required=PASS degraded=0x00\n[SS] ==> READY\n"
        if cmd == "flash_logclear ERASE":
            self.used = 0
            self.frames.clear()
            return "[FLASH] clear event partition: PASS (0)\n"
        return "OK\n"


class FakeLink:
    def __init__(self, dev, clock):
        self.dev, self.clock = dev, clock
        self.commands = []
    def send(self, cmd, timeout, quiet=0.5, done_marker=None):
        self.commands.append((self.clock(), cmd))
        self.clock.sleep(0.05)
        return self.dev.text(cmd).encode(), self.clock() - 0.05, self.clock()
    def close(self): pass


def _fake_checker_accept(batches, expected, start, tag):
    out_dir = os.path.dirname(os.path.dirname(os.path.dirname(batches[0][2])))
    csv_path = os.path.join(out_dir, "%s.csv" % tag)
    header = None
    rows = []
    for _, _, path in batches:
        for line in open(path, encoding="ascii", newline=""):
            line = line.rstrip("\r\n")
            if line.startswith("slot,seq,"):
                header = line
            elif line:
                rows.append(line)
    with open(csv_path, "w", encoding="ascii", newline="\n") as fh:
        fh.write((header or "slot") + "\n" + "".join(r + "\n" for r in rows))
    return 0, "ARCHIVE=ACCEPT rows=%d batches=%d\nCLEAR_ALLOWED=YES\n" % (expected, len(batches))


def _fake_checker_reject(batches, expected, start, tag):
    return 2, "ARCHIVE=REJECT reason=SYNTHETIC detail=forced\nCLEAR_ALLOWED=NO\n"


def _runner_for_selftest(tmpdir, checker):
    clock = FakeClock()
    dev = FakeDevice()
    link = FakeLink(dev, clock)
    runner = Runner(link, tmpdir, checker, clock=clock, sleep=clock.sleep)
    runner.link = link
    return runner, dev, link, clock


def selftest():
    import tempfile
    passed = failed = 0

    def case(cid, fn):
        nonlocal passed, failed
        try:
            fn()
            ok, detail = True, "ok"
        except Exception as exc:
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        print("CASE|%s|%s|%s" % (cid, "PASS" if ok else "FAIL", detail))

    tmp = tempfile.mkdtemp(prefix="m5_field_selftest_")
    # 1. 归档失败 -> 禁止 clear
    def t1():
        runner, dev, link, _ = _runner_for_selftest(tmp, _fake_checker_reject)
        dev.used = 1
        dev.frames[0] = (0, 0, 300)
        try:
            runner.archive(expect_banner="FAKEBANNER", allow_clear=True)
            raise AssertionError("archive accepted a rejected ledger")
        except Abort as exc:
            if "archive REJECT" not in str(exc):
                raise
        if any("flash_logclear" in c for _, c in link.commands):
            raise AssertionError("clear sent after archive reject")

    # 2. 归档通过 + clear 门 + 3s/5s 顺序
    def t2():
        runner, dev, link, clock = _runner_for_selftest(tmp, _fake_checker_accept)
        dev.used = 4096
        for i in range(4096):
            dev.frames[i] = (0, 0, 0)
        state = runner.archive(expect_banner="FAKEBANNER", allow_clear=True,
                               second_copy=tmp + "_second_fs02")
        if not state["clear_done"]:
            raise AssertionError("clear not executed with full log")
        if not os.path.exists(os.path.join(tmp + "_second_fs02", "COPY_VERIFIED.json")):
            raise AssertionError("second copy not verified")
        runner.fault()
        times = {c: t for t, c in link.commands}
        t_start = times["motor_start"]
        if abs(times["safety_trip CONFIRM"] - t_start - 3.0) > 0.2:
            raise AssertionError("trip not near 3s")
        if abs(times["motor_disarm"] - t_start - 5.0) > 0.2:
            raise AssertionError("disarm not near 5s")
        if dev.trip_count != 1:
            raise AssertionError("trip count %d" % dev.trip_count)
        dump_t = times.get("flash_dump 0 100")
        if dump_t is None or times["fault_reset"] < dump_t:
            raise AssertionError("recovery before new-log export")

    # 3. 空间不足且未批准 clear -> 不启动
    def t3():
        runner, dev, link, _ = _runner_for_selftest(tmp, _fake_checker_accept)
        dev.used = 4096
        try:
            runner.archive(expect_banner="FAKEBANNER", allow_clear=False)
            raise AssertionError("archive ran without clear approval on full log")
        except Abort as exc:
            if "--allow-clear" not in str(exc):
                raise

    # 4. 新日志无增长 -> 禁止恢复
    def t4():
        runner, dev, link, clock = _runner_for_selftest(tmp, _fake_checker_accept)
        state = runner.archive(expect_banner="FAKEBANNER", allow_clear=False)
        orig = dev.text
        def no_write(cmd):
            if cmd == "safety_trip CONFIRM":
                dev.safety, dev.fault = "FAULT_LATCHED", 9
                dev.motor = {"state": 4, "cur": 0, "tgt": 0, "armed": 0}
                dev.req, dev.pad = "DISABLE(LOW)", 0
                return orig(cmd)
            return orig(cmd)
        dev.text = no_write
        # 让 trip 后 used 不增长
        def trip_no_write(cmd):
            if cmd == "safety_trip CONFIRM":
                dev.safety, dev.fault = "FAULT_LATCHED", 9
                dev.motor = {"state": 4, "cur": 0, "tgt": 0, "armed": 0}
                dev.req, dev.pad = "DISABLE(LOW)", 0
                dev.session += 0
                return "[SS] safety_trip: EVT_SOFT_FAULT posted rc=0\n"
            return orig(cmd)
        dev.text = trip_no_write
        try:
            runner.fault()
            raise AssertionError("fault accepted without new log")
        except Abort as exc:
            if "blackbox" not in str(exc) and "new slots" not in str(exc):
                raise
        if any(c.startswith("fault_reset") for _, c in link.commands):
            raise AssertionError("recovery sent without new log")

    # 5. 批准清区但未给第二份归档 -> 禁止 clear
    def t5():
        runner, dev, link, _ = _runner_for_selftest(tmp, _fake_checker_accept)
        dev.used = 4096
        for i in range(4096):
            dev.frames[i] = (0, 0, 0)
        try:
            runner.archive(expect_banner="FAKEBANNER", allow_clear=True)
            raise AssertionError("clear ran without second archive copy")
        except Abort as exc:
            if "two-copy" not in str(exc):
                raise
        if any("flash_logclear" in c for _, c in link.commands):
            raise AssertionError("clear sent without second copy")

    # 6. arm gate 未清零 -> 在发送任何电机命令前停止
    def t6():
        runner, dev, link, _ = _runner_for_selftest(tmp, _fake_checker_accept)
        runner.archive(expect_banner="FAKEBANNER", allow_clear=False)
        dev.gate = 8
        before = len(link.commands)
        try:
            runner.fault()
            raise AssertionError("fault ran with arm gate 0x8")
        except Abort as exc:
            if "arm gate not clear" not in str(exc):
                raise
        sent = [c for _, c in link.commands[before:]]
        for bad in ("motor_dir", "motor_arm", "motor_start", "motor_target 300"):
            if any(c.startswith(bad) for c in sent):
                raise AssertionError("motor command sent before gate check: %s" % sent)

    # 7. stale target 300: fault must clear it before preflight/arm
    def t7():
        runner, dev, link, _ = _runner_for_selftest(tmp, _fake_checker_accept)
        runner.archive(expect_banner="FAKEBANNER", allow_clear=False)
        dev.motor["tgt"] = 300
        runner.fault()
        cmds = [c for _, c in link.commands]
        if "motor_target 0" not in cmds or "motor_dir 0" not in cmds or \
           cmds.index("motor_target 0") > cmds.index("motor_dir 0"):
            raise AssertionError("stale target not cleared before motion: %s" % cmds[-12:])

    # 8. caller already set target 0: fault(clear_target=False) must not repeat it
    def t8():
        runner, dev, link, _ = _runner_for_selftest(tmp, _fake_checker_accept)
        runner.archive(expect_banner="FAKEBANNER", allow_clear=False)
        dev.motor["tgt"] = 0
        before = [c for _, c in link.commands].count("motor_target 0")
        runner.fault(clear_target=False)
        after = [c for _, c in link.commands].count("motor_target 0")
        if after != before:
            raise AssertionError("clear_target=False still sent motor_target 0")

    case("FS01-archive_reject_blocks_clear", t1)
    case("FS02-order_3s_5s_and_clear", t2)
    case("FS03-full_log_without_clear_approval_stops", t3)
    case("FS04-no_new_log_blocks_recovery", t4)
    case("FS05-clear_requires_two_copies", t5)
    case("FS06-arm_gate_blocks_before_any_motor_command", t6)
    case("FS07-stale_target_cleared_first", t7)
    case("FS08-clear_target_false_no_repeat", t8)
    print("SUMMARY|cases=%d pass=%d fail=%d" % (passed + failed, passed, failed))
    return 0 if failed == 0 else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", nargs="?", choices=["archive", "fault"])
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "m5_field_run"))
    ap.add_argument("--checker", default=CHECKER)
    ap.add_argument("--expect-banner")
    ap.add_argument("--allow-clear", action="store_true")
    ap.add_argument("--second-copy", help="second archive directory, verified before clear")
    ap.add_argument("--allow-apply", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.phase:
        ap.error("phase or --selftest required")
    os.makedirs(args.out, exist_ok=True)
    link = SerialLink(args.port)
    try:
        runner = Runner(link, args.out, [sys.executable, args.checker])
        if args.phase == "archive":
            runner.archive(args.expect_banner, args.allow_clear, args.allow_apply,
                           args.second_copy)
        else:
            runner.fault()
    except Abort as exc:
        print("M5_FIELD=ABORT %s" % exc)
        return 2
    finally:
        link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
