#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8 diag 栈 4096 的板级对照包执行器（一次批准、零电机）。

单一事实来源是本文件里的 PLAN；`--emit-plan` 用它生成 plan.json，`--mode sim` 只展开 argv 并做门禁自检
（**不碰设备**），`--mode real` 才真正执行且必须带 `--approve` 原文令牌。

与 R3 现场包的差别：本轮不跑电机、不写配置、不清黑匣子；多了下载之前两条只读 RAM 判定（旧异常现场）。
"""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path("D:/ns800")
ROUND = "phase8-m8-diag-stack-r1"
FIELD = ROOT / "reports/reviews" / ROUND / "field"
BIN = ROOT / "reports/firmware/phase8-m8-diag-stack-r1/rt-thread-motor-m1-candidate.bin"
AXF = ROOT / "reports/firmware/phase8-m8-diag-stack-r1/rt-thread-motor-m1.axf"
UID_DEFAULT = pathlib.Path("D:/ns800-m7-p0-20261030T182818/dap_uid_bare.txt")

PACK = "C:/Keil_v5/ARM/PACK/.Download/NOVOSENSE.NS800RT7XXX.0.5.3.pack"
DAP_BASE = ["-f", "1000000", "-Oauto_unlock=false",
            "-Ocache.enable_memory=false", "-Ocache.read_code_from_elf=false"]

# 下载前两条只读 RAM（旧现场；地址来自已入库的异常栈帧轮 + 本轮三处更正）
READ_A = {"name": "A_locals", "addr": 0x200127D8, "bytes": 40,
          "why": "rt_object_init 出错前的局部槽(sp+4=information, sp+8=node, sp+0x14=name, "
                 "sp+0x1b=type, sp+0x1c=object) 与保存的调用者返回地址",
          "sentinels": {"node@sp+8": "0x00bd80b0", "object@sp+0x1c": "0x20015b78",
                        "type 期望很小(对象类枚举)": "<=11"}}
READ_B = {"name": "B_tcb", "addr": 0x20015B28, "bytes": 48,
          "why": "推断的 TCB 起点: name[16]@0(RT_NAME_MAX=16, include/rtdef.h:284)、"
                 "type@16、flag@17、list@0x14、sp/entry/parameter/stack_addr/stack_size",
          "sentinels": {"name 应是可读线程名": "ascii", "stack_size 若为 bblog 则 1024": "0x400"}}

PLAN = {
    "package": "M8 diag 栈 4096 板级对照包 R1",
    "status": "BOUND_NOT_EXECUTED",
    "numbers_are_proposals_not_authorization": True,
    "why": "复审已接受 diag 栈 4096 为**修复候选**; 板上要确认: 能完成启动、查询可交互、"
           "diag 栈有余量、没有再次 HardFault。故障闭环仍未成立。",
    "identity_binding": {
        "board_image_before": {"path": "reports/firmware/phase8-m8-relative-protection-r3/rt-thread-motor-m1-candidate.bin",
                               "bytes": 290916,
                               "sha256": "00608ec93c6a3027d68b44b5317574209fccfb0ba82a9974793d13b54b79e304",
                               "proven_on_board": "已逐字节回读证得(往轮)"},
        "image_under_test": {"path": BIN.relative_to(ROOT).as_posix(), "bytes": BIN.stat().st_size,
                             "sha256": hashlib.sha256(BIN.read_bytes()).hexdigest()},
        "axf_under_test": {"path": AXF.relative_to(ROOT).as_posix(), "bytes": AXF.stat().st_size,
                           "sha256": hashlib.sha256(AXF.read_bytes()).hexdigest()},
        "software_input_commit": "bd517fe (fix) / 0daf94f (docs) —— 远端逐 blob 回读 5159 条 problems=0",
        "diag_stack_source": "applications/diagnosis.c:48 DIAG_THREAD_STACK 4096 (armlink Max Depth 1580 B 对照)",
    },
    "corrections_applied_from_review": [
        "M7 硬件浮点扩展帧 = 26 字 = 104 B(可能另有 4 B 对齐填充); S16-S31 属软件保存部分, 本次 "
        "HardFault 入口汇编没有保存它们; 保存的 xPSR=0x81000000 的 bit9(对齐标记)=0 => 无填充 => "
        "出错前 SP = 0x20012770+104 = 0x200127D8。我上一轮写的'104 或 168 都可能'作废。",
        "offsetof(rt_thread, thread_timer) = 0x50: 复审从新 AXF 的 DWARF 核得; 本机第二路独立证据 = "
        "新镜像反汇编里 _rt_event_recv/_rt_mq_recv 等多处 `LDR r0,[sp,#..] / ADDS r0,r0,#0x50` 紧跟 "
        "`BL rt_timer_control|rt_timer_start`。=> 不再需要靠现场猜。",
        "offsetof(rt_object, name) = 0 (不是 4): include/rtdef.h:284 在 RT_NAME_MAX=16 分支下 name 在最前, "
        "type@16/flag@17/list@0x14 与反汇编 `SUBS r0,r0,#0x14` 一致; 所以 B 项从 0x20015B28 直接解释线程名。",
    ],
    "pre_download_reads": [READ_A, READ_B],
    "quota": {"dap_attach": 3, "savemem_reads": 3, "explicit_halt": 0, "download": 1, "flash_readback_bytes": 1,
              "explicit_reset": 1, "port_open": 1, "motor_run": 0, "config_save": 0,
              "blackbox_clear": 0, "tmc_config_apply": 0, "safety_trip": 0, "retry": 0},
    "forbidden": ["motor_*", "pwm_test", "config_save", "flash_test", "flash_logclear", "safety_trip",
                  "fault_reset", "diag_mode", "diag_profile *commit*", "任何 Flash 写(下载除外)"],
    "tools": {
        "provenance": "逐字沿用已成功的形态: 下载/回读 = R3 field/plan.json 的 tools.download/readback "
                      "(P0 与 M8-S2 两次真跑过); 复位 = 同件 tools.reset(显式 --method sysresetreq + attach); "
                      "只读 RAM = 本轮 frame/frame2 三次实测的 commander 形态(无 --core, resume_on_disconnect=false)。",
        "read_ram": {"argv": ["python", "-m", "pyocd", "commander", "--no-config", "--connect", "attach",
                              "--no-wait", "-t", "cortex_m"] + DAP_BASE +
                               ["-O", "connect_mode=attach", "-O", "scan_all_aps=false",
                                "-O", "resume_on_disconnect=false", "-u", "@UID_FILE",
                                "-c", "status"] +
                               sum([["-c", "savemem 0x%08x %d %s" % (r["addr"], r["bytes"], "@ROOT/" + r["name"] + ".bin")]
                                    for r in (READ_A, READ_B)], []) +
                               ["-c", "status", "-c", "exit"],
                    "device_actions": {"attach": 1, "halt": 0, "reset": 0, "ram_write": 0},
                    "leave": "Halted(不断恢复运行)"},
        "download": {"argv": ["python", "-m", "pyocd", "flash", "--pack", PACK,
                              "-t", "ns800rt7p65d_dhqfby2", "-u", "@UID_FILE"] + DAP_BASE +
                               ["-e", "sector", str(AXF)],
                     "device_actions": {"attach": 1, "flash_write": 1}},
        "readback": {"argv": ["python", "-m", "pyocd", "commander", "--no-config", "-t", "cortex_m",
                              "-u", "@UID_FILE", "-f", "1000000", "-O", "auto_unlock=false",
                              "-O", "connect_mode=halt", "-O", "resume_on_disconnect=false",
                              "-O", "cache.enable_memory=false", "-O", "cache.read_code_from_elf=false",
                              "-O", "scan_all_aps=false", "-c", "status",
                              "-c", "savemem 0x08000000 @IMAGE_BYTES @ROOT/flash_readback.bin",
                              "-c", "status", "-c", "exit"],
                     "compare_against": BIN.relative_to(ROOT).as_posix(),
                     "device_actions": {"attach": 1, "halt_by_connect_mode": 1, "flash_read": 1}},
        "reset": {"argv": ["python", "-m", "pyocd", "reset", "--method", "sysresetreq", "--no-config",
                           "--no-wait", "--connect", "attach", "--core", "0", "-t", "cortex_m",
                           "-u", "@UID_FILE"],
                  "ordering": "必须在串口已 Open 之后发出 —— 否则复位期的 [BOOT] 行永远看不到(观测缺失≠没产出)",
                  "device_actions": {"explicit_reset": 1}},
    },
    "serial": {"port": "COM5", "baud": 115200, "settings": "8N1, 无流控",
               "discipline": "端口对象构造时一次给全参数, **打开之后不再赋任何设置属性**"
                             "(pySerial 在 Windows 上打开后赋值会重设串口并吞字节, R2 已实测并修过)"},
    "stages": [
        {"id": "S-1", "title": "下载前两条只读 RAM: A=异常现场局部槽, B=推断的 TCB", "tool": "read_ram",
         "device_actions": 1, "halt_after": False},
        {"id": "S0", "title": "离线门禁: 三件产物字节/sha 复算 + UID 文件在场 + argv 展开 + 禁止项静态拒绝",
         "tool": None, "device_actions": 0, "halt_after": False},
        {"id": "S1", "title": "唯一一次下载(-e sector, 新 AXF)", "tool": "download", "device_actions": 1,
         "operator_checkpoint": "回传 rc + 原始 stdout/stderr 全文, 由复审批准下一次(下载不可逆)"},
        {"id": "S2", "title": "唯一一次定长回读 290916 B 并与登记 BIN 逐字节比较", "tool": "readback",
         "device_actions": 1, "halt_after": False},
        {"id": "S3", "title": "串口 Open -> 复位 -> 完整启动采集(窗口 75 s, 覆盖 stage-5 数秒扫描与可能的 HardFault dump)",
         "tool": "reset", "device_actions": 1, "raw_first": True},
        {"id": "S4", "title": "交互查询: 先探回显, 再 system_status / ps / free / diag_status",
         "tool": None, "device_actions": 0, "note": "回显缺失即 NO_ECHO 停, 不猜"},
    ],
    "needles": {
        "boot_complete": "[BOOT] Phase-7 runtime bootstrap complete (state=",
        "stage5_storage_line": "[BOOT] 5 storage ......... ",
        "stage8_diag_line": "[BOOT] 8 diagnosis ....... ",
        "fail_hardfault_dump": "hard fault on thread: ",
        "fail_hardfault_regs": "psr: 0x",
        "fail_flash_scan_start": "[FLASH] scanning 1 MiB event log...",
        "sources": {"boot_complete": "applications/supervisor.c:499",
                    "stage5_storage_line": "applications/supervisor.c:463",
                    "stage8_diag_line": "applications/supervisor.c:482",
                    "fail_hardfault_dump": "libcpu/arm/cortex-m7/cpuport.c:440",
                    "fail_hardfault_regs": "libcpu/arm/cortex-m7/cpuport.c:420",
                    "fail_flash_scan_start": "applications/ns_storage.c:147"}},
    "pass_criteria": {
        "boot_completed": "采集里出现 boot_complete 行",
        "interactive": "msh 回显存在且四条查询各有非空响应",
        "diag_stack_headroom": "ps 表里 diag 行 stack=4096 且 max used < 4096",
        "heap_headroom": "free 的 total/used/max 全部可读且 used+max 未贴近 total",
        "no_hardfault": "两个 fail needle 都不出现",
        "readback_equal": "S2 的 290916 B 与登记 BIN 逐字节相同",
        "a_b_reads": "A 命中两个哨兵(node=0x00bd80b0 @+8, object=0x20015b78 @+0x1c) 即证明局部槽未被覆盖; "
                     "B 的 name 字段给出线程身份, 与 0x50 偏移一起判死'定时器链表'这条推断"},
    "stop_labels": ["PRE_READ_MISSING_FILE", "PLAN_GATE_REFUSED", "DOWNLOAD_FAILED", "READBACK_MISMATCH",
                    "PORT_BUSY", "RESET_FAILED", "BOOT_INCOMPLETE", "NO_ECHO", "HARDFAULT_NEEDLE_SEEN",
                    "MOTION_FORBIDDEN_REQUESTED"],
    "quota_is_executed_not_declared": True,
    "offline_only_statement": "本文件与 plan.json 由 --emit-plan/--mode sim 生成; 真实设备动作只在带 "
                              "--approve 原文令牌的 --mode real 下发生, 且必须由复审一次批准。",
}


def expand(argv, run_root, uid):
    out = []
    for a in argv:
        a = a.replace("@UID_FILE", uid or "@UID_FILE_MISSING")
        a = a.replace("@ROOT", str(run_root))
        a = re.sub(r"@IMAGE_BYTES", "%d" % BIN.stat().st_size, a)
        out.append(a)
    return out


def masked(argv, uid):
    if uid:
        return [("<UID_FILE@masked len=%d>" % len(uid)) if a == uid else a for a in argv]
    return argv


def gate_check():
    """静态门禁: **只看会被执行的东西**(argv + stage 绑定), 不看散文, 也不看 forbidden 清单本身。

    上一版把整个 PLAN 序列化成 blob 再去搜禁止词, 结果 forbidden 列表自己就是命中源 => 9 条假报警。
    """
    problems = []
    executable = []
    for tool in PLAN["tools"].values():
        if isinstance(tool, dict):          # provenance 是散文, 不是可执行体
            executable += tool.get("argv", [])
    for st in PLAN["stages"]:
        if st.get("tool"):
            executable.append(st["tool"])
        executable.append(st.get("title", ""))
    blob = " ".join(executable).lower()
    for bad in ("motor_start", "motor_stop", "motor_arm", "motor_disarm", "pwm_test", "config_save",
                "flash_test", "flash_logclear", "safety_trip", "fault_reset", "diag_mode",
                "blackbox_clear", "write32", "writemem", "loadmem", "erase"):
        if bad in blob:
            problems.append("forbidden_token_in_executables %s" % bad)
    n_dl = sum(1 for st in PLAN["stages"] if st.get("tool") == "download")
    n_rb = sum(1 for st in PLAN["stages"] if st.get("tool") == "readback")
    n_rs = sum(1 for st in PLAN["stages"] if st.get("tool") == "reset")
    if (n_dl, n_rb, n_rs) != (1, 1, 1):
        problems.append("action_cardinality download=%d readback=%d reset=%d" % (n_dl, n_rb, n_rs))
    for r in PLAN["pre_download_reads"]:
        lo, hi = r["addr"], r["addr"] + r["bytes"]
        if not (0x20012000 <= lo and hi <= 0x2001A000):
            problems.append("read_outside_known_ram %s" % r["name"])
    if PLAN["quota"]["motor_run"] != 0 or PLAN["quota"]["config_save"] != 0:
        problems.append("motion_or_config_quota_nonzero")
    return problems


def sim(run_root, emit):
    run_root.mkdir(parents=True, exist_ok=True)
    probs = gate_check()
    lines = ["MODE=sim (零设备动作: 没有 pyocd/Keil/串口调用)", "PLAN_GATE problems=%d %s" % (len(probs), probs)]
    for st in PLAN["stages"]:
        tool = st.get("tool")
        lines.append("stage %s tool=%s device_actions=%s" % (st["id"], tool, st["device_actions"]))
        if tool:
            argv = expand(PLAN["tools"][tool]["argv"], run_root, None)
            lines.append("   argv: " + " ".join(masked(argv, None)))
    lines.append("image_under_test bytes=%d sha256=%s" % (PLAN["identity_binding"]["image_under_test"]["bytes"],
                                                          PLAN["identity_binding"]["image_under_test"]["sha256"]))
    lines.append("readback_length=%d (=0x%X)" % (BIN.stat().st_size, BIN.stat().st_size))
    lines.append("needles: " + json.dumps({k: v for k, v in PLAN["needles"].items() if k != "sources"},
                                          ensure_ascii=False))
    lines.append("SIM_TOTAL problems=%d" % len(probs))
    txt = "\n".join(lines) + "\n"
    (run_root / "sim_dispatch.txt").write_text(txt, encoding="utf-8", newline="\n")
    if emit:
        emit.write_text(json.dumps(PLAN, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        print("EMIT_PLAN %s bytes=%d" % (emit, emit.stat().st_size))
    print(txt)
    return 0 if not probs else 1


def _backend(run_root, uid):
    sys.path.insert(0, str(ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field"))
    import m8r3_serial_chain as M  # 已证形态: Ledger + RealActionBackend(原始 stdout/stderr 先落盘)
    clock = M.RealClock()
    led = M.Ledger(run_root / "ledger.jsonl", clock)
    bk = M.RealActionBackend(led, run_root, clock, 300, subs={"@UID_FILE": uid}, repo_root=str(ROOT))
    return M, bk


def _serial_and_capture(run_root, uid, serial_factory=None, do_reset=True):
    """S3/S4: 先 Open, 再复位, 再采集; 每段原始字节先落盘再判定。serial_factory 只给 selftest 注入假端口。"""
    import subprocess
    out = {}
    if serial_factory is None:
        import serial
        serial_factory = lambda: serial.Serial(PORT["port"], PORT["baud"], timeout=0.2,
                                               rtscts=False, dsrdtr=False, xonxoff=False,
                                               bytesize=8, parity="N", stopbits=1)
    p = serial_factory()          # 参数一次给全, 打开后不再赋任何设置属性
    p.reset_input_buffer()
    if do_reset:
        r = subprocess.run(expand(PLAN["tools"]["reset"]["argv"], run_root, uid),
                           cwd=str(ROOT), capture_output=True, timeout=120)
        (run_root / "S3_reset.stdout.bin").write_bytes(r.stdout)
        (run_root / "S3_reset.stderr.bin").write_bytes(r.stderr)
        (run_root / "S3_reset.rc.txt").write_text(str(r.returncode), encoding="ascii", newline="\n")
    else:
        (run_root / "S3_reset.rc.txt").write_text("SKIPPED_INJECTION(零设备: 不碰 DAP)", encoding="utf-8",
                                                  newline="\n")
    deadline = time.time() + 75
    buf = bytearray()
    while time.time() < deadline:
        chunk = p.read(512)
        if chunk:
            buf += chunk
    (run_root / "S3_boot_capture.bin").write_bytes(bytes(buf))
    out["boot_bytes"] = len(buf)
    txt = buf.decode("utf-8", "replace")
    n = PLAN["needles"]
    out["hardfault_seen"] = n["fail_hardfault_dump"] in txt or n["fail_hardfault_regs"] in txt
    out["boot_complete_seen"] = n["boot_complete"] in txt
    p.write(b"\r")
    time.sleep(1.0)
    echo = p.read(4096)
    (run_root / "S4_echo.bin").write_bytes(echo)
    out["echo_bytes"] = len(echo)
    if echo:
        for cmd in ("system_status", "ps", "free", "diag_status"):
            p.write(("%s\r" % cmd).encode())
            time.sleep(3.0)
            resp = b""
            while True:
                c = p.read(512)
                if not c:
                    break
                resp += c
            (run_root / ("S4_%s.bin" % cmd)).write_bytes(resp)
            out.setdefault("query_bytes", {})[cmd] = len(resp)
    p.close()
    return out


PORT = {"port": "COM5", "baud": 115200}


def selftest(run_root):
    """零设备注入: 用假端口跑 S3/S4 的判定与停点逻辑(照 R3 CL-A 'Real 后端零设备注入' 的形状)。"""
    class FakePort(object):
        """假端口: 每次 write 先吐掉积压, 再按写入内容挂一段 canned 响应给下一次 read。"""

        def __init__(self, boot_text, echo=b"ns800 msh >"):
            self.chunks = [boot_text.encode()]
            self.echo = echo
            self.i = 0
            self.pending = b""
            self.closed = False
            self.written = []

        def reset_input_buffer(self):
            pass

        def read(self, n):
            if self.i < len(self.chunks):
                self.i += 1
                return self.chunks[self.i - 1]
            out, self.pending = self.pending, b""
            return out

        def write(self, b):
            self.written.append(b)
            if b.strip() == b"\r" or b == b"\r":
                self.pending = self.echo
            elif self.echo:
                self.pending = ("resp<%s>\r\n" % b.decode(errors="replace").strip()).encode()
            return len(b)

        def close(self):
            self.closed = True

    n = PLAN["needles"]
    healthy = "[BOOT] 5 storage ......... OK\r\n[BOOT] 8 diagnosis ....... OK\r\n" + n["boot_complete"] + "READY\r\n"
    crashed = "[BOOT] 4 hw-services ...\r\n" + n["boot_complete"] + n["fail_hardfault_dump"] + "diag\r\n"
    checks = []
    d = run_root / "st_healthy"
    d.mkdir(parents=True, exist_ok=True)
    fp = FakePort(healthy)
    got = _serial_and_capture(d, "@UID_FILE_REDACTED", serial_factory=lambda: fp, do_reset=False)
    checks.append(("ST_healthy_pass", got["boot_complete_seen"] and not got["hardfault_seen"] and got["echo_bytes"] > 0))
    checks.append(("ST_healthy_queries_saved", len(got.get("query_bytes", {})) == 4 and
                                      all(v > 0 for v in got.get("query_bytes", {}).values()) and fp.closed))
    d2 = run_root / "st_hardfault"
    d2.mkdir(parents=True, exist_ok=True)
    got2 = _serial_and_capture(d2, "@UID_FILE_REDACTED", serial_factory=lambda: FakePort(crashed, echo=b""), do_reset=False)
    checks.append(("ST_hardfault_needle_fires", got2["hardfault_seen"] is True))
    checks.append(("ST_no_echo_stops_before_queries", "query_bytes" not in got2 and got2["echo_bytes"] == 0))
    d3 = run_root / "st_silent"
    d3.mkdir(parents=True, exist_ok=True)
    got3 = _serial_and_capture(d3, "@UID_FILE_REDACTED", serial_factory=lambda: FakePort("", echo=b""), do_reset=False)
    checks.append(("ST_silent_not_pass", (not got3["boot_complete_seen"]) and got3["echo_bytes"] == 0))
    lines = ["SELFTEST mode=zero_device_injection (未接触真实端口/设备)"]
    for name, ok in checks:
        lines.append(("OK " if ok else "PROBLEM ") + name)
    bad = sum(1 for _, ok in checks if not ok)
    lines.append("SELFTEST_PROBLEMS=%d" % bad)
    (run_root / "selftest.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return 0 if bad == 0 else 1


def real(run_root, uid, approve):
    if not approve or "批准" not in approve:
        print("REFUSED: real 模式需要复审一次批准的原文令牌(--approve ...), 且必须含'批准'二字")
        return 2
    if not uid or not pathlib.Path(uid).exists():
        print("STOP PRE_READ_MISSING_FILE: UID 文件不在场")
        return 2
    probs = gate_check()
    if probs:
        print("STOP PLAN_GATE_REFUSED %s" % probs)
        return 2
    M, bk = _backend(run_root, uid)
    try:
        r = bk._run("read_ram", {"as": "read_ram"}, expand(PLAN["tools"]["read_ram"]["argv"], run_root, uid))
        print("S-1 rc=%s stdout=%dB" % (r["rc"], len(r["stdout"])))
        if r["rc"]:
            print("STOP PRE_READ_FAILED")
            return 3
        r = bk._run("download", {"as": "download"}, expand(PLAN["tools"]["download"]["argv"], run_root, uid))
        print("S1 rc=%s" % r["rc"])
        if r["rc"]:
            print("STOP DOWNLOAD_FAILED")
            return 3
        r = bk._run("readback", {"as": "readback"}, expand(PLAN["tools"]["readback"]["argv"], run_root, uid))
        rb = run_root / "flash_readback.bin"
        same = rb.exists() and rb.read_bytes() == BIN.read_bytes()
        print("S2 rc=%s readback=%sB byte_equal=%s" % (r["rc"], rb.stat().st_size if rb.exists() else -1, same))
        if not same:
            print("STOP READBACK_MISMATCH")
            return 3
        got = _serial_and_capture(run_root, uid)
        print("S3/S4 %s" % json.dumps(got, ensure_ascii=False))
        verdict = "PASS" if (got["boot_complete_seen"] and not got["hardfault_seen"]
                             and got["echo_bytes"] > 0) else "FAIL_OR_INCOMPLETE"
        print("VERDICT=%s (栈/堆余量要人工读 ps/free 两个文件, 不在这里自动判定)" % verdict)
        return 0 if verdict == "PASS" else 4
    except M.Stop as e:  # noqa: F821
        print("STOP %s %s" % (getattr(e, "code", "STOP"), e))
        return 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "real", "selftest"], default="sim")
    ap.add_argument("--emit-plan", type=pathlib.Path, default=None)
    ap.add_argument("--run-root", type=pathlib.Path, default=None)
    ap.add_argument("--uid-file", default=str(UID_DEFAULT))
    ap.add_argument("--approve", default=None)
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_root = a.run_root or (FIELD / ({"sim": "sim-%s", "selftest": "selftest-%s",
                                        "real": "field-%s"}[a.mode] % stamp))
    uid = a.uid_file if a.mode == "real" and pathlib.Path(a.uid_file).exists() else None
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "MODE.txt").write_text("%s %s\n" % (a.mode, stamp), encoding="utf-8", newline="\n")
    if a.mode == "sim":
        return sim(run_root, a.emit_plan)
    if a.mode == "selftest":
        return selftest(run_root)
    return real(run_root, uid, a.approve)


if __name__ == "__main__":
    sys.exit(main())
