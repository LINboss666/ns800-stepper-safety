#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8 diag 栈 4096 的板级对照包执行器（整包一次批准、零电机）。

单一事实来源是本文件里的 PLAN。`--mode sim` 只展开 argv 并做门禁自检(**不碰设备**);
`--mode selftest` 用零设备假端口 + 假时钟跑判定与停点(不等真实 75 s);
`--mode real` 才执行设备动作, 且必须带操作者/复审的原文授权令牌。

本轮集中补正(复审 2026-11-01 任务书, 六项):
1. UID: 传 `-u <文件内容>` 而不是文件路径(原写法 pyocd 按 UID 匹配不到探针)。UID 原值永不入库、永不打印,
   公开件只有 `len + sha256_12` 脱敏形式(沿用 m8r3_serial_chain.uid_from_file/uid_disclosure 口径)。
2. 路径: `@ROOT` 一律展开成 `run_root.as_posix()`, savemem 目标文件名加**单引号**。
   反斜杠形态在本机 pyocd 0.45.1 的分词器里实测会被拆坏:
   `split_command_line("savemem 0x08000000 290916 D:\\ns800\\a.bin")` ->
   `['savemem','0x08000000','290916','D:','s800\\x07.bin']`(而单引号形态被正确剥成单个路径)。
3. 接受值先固定: BIN/AXF 的字节数与 sha256 是**源码里的常量**, 并与已登记的
   reports/firmware/phase8-m8-diag-stack-r1/SHA256SUMS.txt 逐字对照; 任何设备动作之前先复核盘上文件。
   原来在 PLAN 里现算 sha 再拿现算值当接受值 —— 盘上文件坏了也能"自证相符", 该写法作废。
4. 串口段统一 try/finally 关句柄; 每读到一个分片立刻 append+flush 落盘(异常时已收到的启动字节不丢);
   复位非零、HardFault、启动未完成、响应缺失或缺提示符 —— 一律停止业务, 不猜。
5. 每条查询用单调时钟截止, 核对写全量与"回显+正文+结尾提示符"三段齐全; 只有回显/非空垃圾不算成功。
6. 结果标签: 栈/堆余量尚未离线判读时只能是 CAPTURE_COMPLETE_REVIEW_PENDING, 脚本不再自宣 PASS。
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
BIN_REL = "reports/firmware/phase8-m8-diag-stack-r1/rt-thread-motor-m1-candidate.bin"
AXF_REL = "reports/firmware/phase8-m8-diag-stack-r1/rt-thread-motor-m1.axf"
BOARD_BEFORE_REL = ("reports/firmware/phase8-m8-relative-protection-r3/"
                    "rt-thread-motor-m1-candidate.bin")
FW_SUMS = ROOT / "reports/firmware" / ROUND / "SHA256SUMS.txt"
UID_DEFAULT = pathlib.Path("D:/ns800-m7-p0-20261030T182818/dap_uid_bare.txt")

PACK = "C:/Keil_v5/ARM/PACK/.Download/NOVOSENSE.NS800RT7XXX.0.5.3.pack"
DAP_BASE = ["-f", "1000000", "-Oauto_unlock=false",
            "-Ocache.enable_memory=false", "-Ocache.read_code_from_elf=false"]

# 固定接受值(补正 3): 来自已登记清单, 先写死再复核, 不在跑的时候现算。
ACCEPTED = {
    "image_under_test": {"rel": BIN_REL, "bytes": 290916,
                         "sha256": "f5c2fd89bbf28255a5b26c9cca495bc0fb1e39d401d0bb7bea19d051221708ce"},
    "axf_under_test": {"rel": AXF_REL, "bytes": 1771288,
                       "sha256": "fb26d5a0e866b2c8852c989c98f158d99d0511ffdc6ce4e9b8c8add3372ed6ea"},
    "board_image_before": {"rel": BOARD_BEFORE_REL, "bytes": 290916,
                           "sha256": "00608ec93c6a3027d68b44b5317574209fccfb0ba82a9974793d13b54b79e304",
                           "proven_on_board": "已逐字节回读证得(往轮 f4eca5d)"},
}

# 下载前两条只读 RAM（旧现场；地址来自已入库的异常栈帧轮 + 三处更正）
READ_A = {"name": "A_locals", "addr": 0x200127D8, "bytes": 40,
          "why": "rt_object_init 出错前的局部槽(sp+4=information, sp+8=node, sp+0x14=name, "
                 "sp+0x1b=type, sp+0x1c=object) 与保存的调用者返回地址",
          "sentinels": {"node@+0x08": "0x00bd80b0", "object@+0x1c": "0x20015b78"}}
READ_B = {"name": "B_tcb", "addr": 0x20015B28, "bytes": 48,
          "why": "推断的 TCB 起点: name[16]@0(RT_NAME_MAX=16, include/rtdef.h:284)、type@16、flag@17、"
                 "list@0x14; 其后 sp/entry/parameter/stack_addr/stack_size 的偏移本轮不在此脚本判定",
          "sentinels": {"name 应是可读线程名": "ascii"}}

PROMPT = "msh >"          # components/finsh/shell.c:93 _MSH_PROMPT "msh " + :122 追加 '>'
BOOT_WINDOW_S = 75        # 真机窗口保留(勿缩短: 上一轮正是窗口不足导致漏判)
QUERY_WINDOW_S = 12
QUERIES = ("system_status", "ps", "free", "diag_status")


def _savemem(addr, count, rel_name):
    """savemem 命令串: 目标路径由 @ROOT 展开成 posix 形式, 文件名用单引号包住(补正 2)。"""
    return "savemem 0x%08x %d '@ROOT/%s'" % (addr, count, rel_name)


PLAN = {
    "package": "M8 diag 栈 4096 板级对照包 R1(脚本补正 r2)",
    "status": "BOUND_NOT_EXECUTED",
    "numbers_are_proposals_not_authorization": True,
    "approval": "整包一次批准(操作者/复审原文令牌); S1 之后不再向复审申请下一步 —— 原 S1 "
                "operator_checkpoint 与任务书冲突, 已删除。",
    "why": "复审已接受 diag 栈 4096 为**修复候选**; 板上要确认: 能完成启动、查询可交互、"
           "diag 栈有余量、没有再次 HardFault。故障闭环仍未成立。",
    "identity_binding": {
        "accepted_constants_source": "本文件 ACCEPTED 常量 + 与 FW_SUMS 登记值逐字对照(verify_accepted_literals)",
        "image_under_test": ACCEPTED["image_under_test"],
        "axf_under_test": ACCEPTED["axf_under_test"],
        "board_image_before": ACCEPTED["board_image_before"],
        "software_input_commit": "bd517fe (fix) / 0daf94f (docs); 8bf93fb 为上一版板级包绑定",
        "diag_stack_source": "applications/diagnosis.c:48 DIAG_THREAD_STACK 4096 (armlink Max Depth 1580 B 对照)",
    },
    "corrections_applied_from_review": [
        "M7 硬件浮点扩展帧 = 26 字 = 104 B(可能另有 4 B 对齐填充); S16-S31 属软件保存部分, 本次 "
        "HardFault 入口汇编没有保存它们; 保存的 xPSR=0x81000000 的 bit9(对齐标记)=0 => 无填充 => "
        "出错前 SP = 0x20012770+104 = 0x200127D8。",
        "offsetof(rt_thread, thread_timer) = 0x50: 复审从新 AXF 的 DWARF 核得; 本机第二路证据 = 新镜像反汇编 "
        "多处 `ADDS r0,r0,#0x50` 紧跟 `BL rt_timer_control|rt_timer_start`。",
        "offsetof(rt_object, name) = 0 (不是 4): include/rtdef.h:284 在 RT_NAME_MAX=16 分支下 name 在最前, "
        "type@16/flag@17/list@0x14 与反汇编 `SUBS r0,r0,#0x14` 一致; B 项从 0x20015B28 直接解释线程名。",
        "脚本补正 r2 六项(UID 内容/posix+单引号/固定接受值/try-finally 逐块落盘/查询完整性判据/"
        "CAPTURE_COMPLETE_REVIEW_PENDING)与 DAP 会话数改 4 —— 见本文件 docstring。",
    ],
    "pre_download_reads": [READ_A, READ_B],
    "quota": {"dap_sessions": 4, "dap_sessions_note": "RAM读取 / 下载 / 回读 / 复位 = 4 次工具连接(上一版误记 3)",
              "savemem_reads": 3, "explicit_halt": 0, "download": 1, "flash_readback_bytes": 1,
              "explicit_reset": 1, "port_open": 1, "motor_run": 0, "config_save": 0,
              "blackbox_clear": 0, "tmc_config_apply": 0, "safety_trip": 0, "retry": 0},
    "forbidden": ["motor_*", "pwm_test", "config_save", "flash_test", "flash_logclear", "safety_trip",
                  "fault_reset", "diag_mode", "diag_profile *commit*", "任何 Flash 写(下载除外)"],
    "tools": {
        "provenance": "形态沿用已实测成功的件: 下载/回读 = R3 field/plan.json 的 tools.download/readback "
                      "(P0 与 M8-S2 真跑过, 其中 M8-S2 已证 290916 B 逐字节相符); 复位 = 同件 tools.reset; "
                      "只读 RAM = 本轮 frame/frame2 三次实测的 commander 形态(无 --core, resume_on_disconnect=false)。",
        "read_ram": {"argv": ["python", "-m", "pyocd", "commander", "--no-config", "--connect", "attach",
                              "--no-wait", "-t", "cortex_m"] + DAP_BASE +
                             ["-O", "connect_mode=attach", "-O", "scan_all_aps=false",
                              "-O", "resume_on_disconnect=false", "-u", "@UID_FILE",
                              "-c", "status"] +
                             sum([["-c", _savemem(r["addr"], r["bytes"], r["name"] + ".bin")]
                                  for r in (READ_A, READ_B)], []) +
                             ["-c", "status", "-c", "exit"],
                     "device_actions": {"attach": 1, "halt": 0, "reset": 0, "ram_write": 0},
                     "leave": "Halted(不断恢复运行)"},
        "download": {"argv": ["python", "-m", "pyocd", "flash", "--pack", PACK,
                              "-t", "ns800rt7p65d_dhqfby2", "-u", "@UID_FILE"] + DAP_BASE +
                             ["-e", "sector", "@AXF"],
                     "device_actions": {"attach": 1, "flash_write": 1}},
        "readback": {"argv": ["python", "-m", "pyocd", "commander", "--no-config", "-t", "cortex_m",
                              "-u", "@UID_FILE", "-f", "1000000", "-O", "auto_unlock=false",
                              "-O", "connect_mode=halt", "-O", "resume_on_disconnect=false",
                              "-O", "cache.enable_memory=false", "-O", "cache.read_code_from_elf=false",
                              "-O", "scan_all_aps=false", "-c", "status",
                              "-c", _savemem(0x08000000, ACCEPTED["image_under_test"]["bytes"],
                                             "flash_readback.bin"),
                              "-c", "status", "-c", "exit"],
                     "compare_against": ACCEPTED["image_under_test"]["rel"],
                     "device_actions": {"attach": 1, "halt_by_connect_mode": 1, "flash_read": 1}},
        "reset": {"argv": ["python", "-m", "pyocd", "reset", "--method", "sysresetreq", "--no-config",
                           "--no-wait", "--connect", "attach", "--core", "0", "-t", "cortex_m",
                           "-u", "@UID_FILE"],
                  "ordering": "必须在串口已 Open 之后发出 —— 否则复位期的 [BOOT] 行永远看不到(观测缺失≠没产出)",
                  "device_actions": {"explicit_reset": 1}},
    },
    "serial": {"port": "COM5", "baud": 115200, "settings": "8N1, 无流控",
               "prompt_marker": PROMPT, "boot_window_s": BOOT_WINDOW_S, "query_window_s": QUERY_WINDOW_S,
               "discipline": "端口对象构造时一次给全参数, 打开之后不再赋任何设置属性(pySerial 在 Windows "
                             "上打开后赋值会重设串口并吞字节, R2 已实测并修过); 分片即时落盘 + try/finally 关闭"},
    "stages": [
        {"id": "S-1", "title": "下载前两条只读 RAM: A=异常现场局部槽, B=推断的 TCB", "tool": "read_ram",
         "device_actions": 1, "gates": "A=40 B / B=48 B 在场且长度精确; status 读出 Halted"},
        {"id": "S0", "title": "离线门禁: 固定接受值复核 + UID 内容读取 + argv 展开形态 + 禁止项静态拒绝",
         "tool": None, "device_actions": 0},
        {"id": "S1", "title": "唯一一次下载(-e sector, 新 AXF)", "tool": "download", "device_actions": 1},
        {"id": "S2", "title": "唯一一次定长回读 290916 B 并与固定接受散列逐字节比较", "tool": "readback",
         "device_actions": 1, "halt_after": False},
        {"id": "S3", "title": "串口 Open -> 复位 -> 完整启动采集(75 s, 覆盖 stage-5 数秒扫描与可能的 HardFault dump)",
         "tool": "reset", "device_actions": 1, "raw_first": True},
        {"id": "S4", "title": "交互查询: 先探提示符, 再 system_status / ps / free / diag_status",
         "tool": None, "device_actions": 0, "note": "提示符缺失=NO_PROMPT 停; 每条查询要回显+正文+结尾提示符三段齐全"},
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
                    "fail_flash_scan_start": "applications/ns_storage.c:147",
                    "prompt": "components/finsh/shell.c:93(_MSH_PROMPT) + :122('>')"}},
    "stop_labels": ["PRODUCT_IDENTITY_MISMATCH", "UID_NOT_CONTENT", "SAVEMEM_PATH_FORM", "PLAN_GATE_REFUSED",
                    "PRE_READ_MISSING_FILE", "PRE_READ_SHORT_FILE", "CORE_NOT_HALTED", "DOWNLOAD_FAILED",
                    "READBACK_MISSING", "READBACK_SHORT", "READBACK_MISMATCH", "PORT_BUSY",
                    "PORT_NOT_CLOSED", "RESET_FAILED", "HARDFAULT_NEEDLE_SEEN", "BOOT_INCOMPLETE",
                    "NO_PROMPT", "QUERY_SHORT_WRITE", "QUERY_TIMEOUT", "QUERY_INCOMPLETE",
                    "SERIAL_READ_EXCEPTION", "PRIOR_READBACK_NOT_PROVEN", "CONT_TOOL_CARDINALITY",
                    "MOTION_FORBIDDEN_REQUESTED"],
    "verdict_labels": {
        "CAPTURE_COMPLETE_REVIEW_PENDING": "启动完成+无 HardFault+四条查询三段齐全; "
                                          "diag 栈余量与堆余量**尚未离线判读**, 所以不写 PASS",
        "FAIL_OR_INCOMPLETE": "上面任一条件不成立(具体停点见 stop_label)",
    },
    "quota_is_executed_not_declared": True,
    "offline_only_statement": "真实设备动作只在带原文令牌的 --mode real 下发生; sim/selftest 两条路径"
                              "不起 pyocd、不开真端口(selftest 用假端口与假时钟)。",
}

SAVEMEM_RE = re.compile(r"^savemem\s+(0x[0-9a-fA-F]+)\s+(\d+)\s+'([^']*)'$")


def sha256_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def read_entries(sums_path):
    out = {}
    for ln in pathlib.Path(sums_path).read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            h, _sep, rel = ln.partition("  ")
            out[rel.strip()] = h.strip()
    return out


def verify_accepted_literals():
    """补正 3 的第一半: ACCEPTED 里的散列必须与**已登记清单**逐字相同, 否则常量是我编的。"""
    reg = read_entries(FW_SUMS)
    problems = []
    for key in ("image_under_test", "axf_under_test"):
        want = ACCEPTED[key]
        got = reg.get(want["rel"])
        if got is None:
            problems.append("not_in_registered_manifest %s" % want["rel"])
        elif got != want["sha256"]:
            problems.append("literal_disagrees_with_manifest %s" % want["rel"])
    return problems


def verify_products():
    """补正 3 的第二半: 设备动作之前用固定接受值复核盘上文件(不是拿盘上文件现算接受值)。"""
    problems = []
    for key in ("image_under_test", "axf_under_test"):
        want = ACCEPTED[key]
        p = ROOT / want["rel"]
        if not p.exists():
            problems.append("missing_file %s" % want["rel"])
            continue
        n = p.stat().st_size
        if n != want["bytes"]:
            problems.append("length %s declared=%d on_disk=%d" % (want["rel"], want["bytes"], n))
        s = sha256_file(p)
        if s != want["sha256"]:
            problems.append("sha %s accepted=%s on_disk=%s" % (want["rel"], want["sha256"][:16], s[:16]))
    return problems


def uid_from_file(path):
    """补正 1: 取**文件内容**并 strip; 原值只在内存里, 永不入库、永不打印。"""
    return pathlib.Path(path).read_text(encoding="utf-8").strip()


def uid_disclosure(secret):
    s = str(secret or "")
    if not s:
        return {"present": False}
    return {"present": True, "length": len(s),
            "sha256_12": hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]}


def expand(argv, run_root, uid):
    """@UID_FILE -> UID 内容; @ROOT -> posix 形式; @AXF -> posix 绝对路径; 无 UID 时保留可判别的缺位标记。"""
    token = uid if uid else "@UID_FILE_MISSING"
    out = []
    for a in argv:
        a = a.replace("@UID_FILE", token)
        a = a.replace("@AXF", (ROOT / ACCEPTED["axf_under_test"]["rel"]).as_posix())
        a = a.replace("@ROOT", run_root.as_posix())
        out.append(a)
    return out


def argv_gate(name, argv, uid, run_root):
    """补正 1/2 的可执行体检查: 只检查真会发出去的东西。"""
    problems = []
    for i, a in enumerate(argv):
        if "@UID_FILE" in a or "@ROOT" in a or "@AXF" in a or "@IMAGE_BYTES" in a:
            problems.append("%s:unresolved_placeholder %s" % (name, a))
        if a.startswith("D:\\") or a.startswith("D:\\\\") or "\\ns800" in a:
            problems.append("%s:backslash_path %s" % (name, a))
        if i and argv[i - 1] == "-u":
            if not uid:
                problems.append("%s:UID_MISSING" % name)
            elif a != uid:
                looks = "path" if ("/" in a or "\\" in a or a.endswith(".txt")) else "differs"
                problems.append("%s:uid_arg_not_content(%s)" % (name, looks))
        m = SAVEMEM_RE.match(a)
        if m:
            _addr, count, path = m.group(1), int(m.group(2)), m.group(3)
            if "\\" in path:
                problems.append("%s:savemem_backslash %s" % (name, path))
            elif not path.startswith(run_root.as_posix()):
                problems.append("%s:savemem_outside_run_root %s" % (name, path))
            elif name == "readback" and count != ACCEPTED["image_under_test"]["bytes"]:
                problems.append("%s:savemem_count_not_accepted %d" % (name, count))
        elif re.match(r"^savemem\b", a):
            problems.append("%s:savemem_target_not_quoted %s" % (name, a))
    return problems


def gate_check(uid, run_root):
    """静态门禁: 只看会被执行的东西(argv + stage 绑定), 不看散文, 也不看 forbidden 清单自身。"""
    problems = list(verify_accepted_literals())
    executable = []
    for name, tool in PLAN["tools"].items():
        if isinstance(tool, dict):                    # provenance 是散文, 不是可执行体
            executable += tool.get("argv", [])
            if uid:                                   # 只有真跑(有 UID 内容)才核展开后的形态
                problems += argv_gate(name, expand(tool.get("argv", []), run_root, uid), uid, run_root)
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
    if PLAN["quota"]["dap_sessions"] != 4:
        problems.append("dap_session_count_mismatch %s" % PLAN["quota"]["dap_sessions"])
    for r in PLAN["pre_download_reads"]:
        lo, hi = r["addr"], r["addr"] + r["bytes"]
        if not (0x20012000 <= lo and hi <= 0x2001A000):
            problems.append("read_outside_known_ram %s" % r["name"])
    if PLAN["quota"]["motor_run"] != 0 or PLAN["quota"]["config_save"] != 0:
        problems.append("motion_or_config_quota_nonzero")
    return problems


def mask(argv, uid):
    if not uid:
        return list(argv)
    return [("@UID_FILE<redacted len=%d sha256_%s>" % (len(uid), uid_disclosure(uid)["sha256_12"]))
            if a == uid else a for a in argv]


class RealClock(object):
    def monotonic(self):
        return time.monotonic()

    def sleep(self, s):
        time.sleep(s)


def run_tool(name, argv, run_root):
    """默认后端: 真起子进程。stdout/stderr/rc 先落盘再判读。"""
    d = run_root / ("action_" + name)
    d.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(argv, cwd=str(ROOT), capture_output=True, timeout=600)
    (d / "stdout.bin").write_bytes(p.stdout)
    (d / "stderr.bin").write_bytes(p.stderr)
    (d / "rc.txt").write_text("%d\n" % p.returncode, encoding="ascii", newline="\n")
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "dir": d}


def last_core_state(stdout_text):
    """pyocd `status` 的实测措辞: 'Core 0 (None):  Halted' / 'Running'(见往轮 action_*/stdout.bin)。"""
    hits = re.findall(r"Core\s*\d+\s*\([^)]*\)\s*:\s*(\w+)", stdout_text)
    return hits[-1] if hits else None


def result(out, run_root):
    (run_root / "RESULT.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8", newline="\n")
    return out


def stop(out, run_root, label, detail=None):
    out["stop_label"] = label
    out["verdict"] = "FAIL_OR_INCOMPLETE"
    if detail is not None:
        out["stop_detail"] = detail
    return result(out, run_root)


def capture_window(port, run_root, fname, seconds, clock, needles):
    """补正 4: 每个分片立刻 append+flush 落盘; 由调用方 try/finally 负责关闭。"""
    path = run_root / fname
    with path.open("wb") as f:
        end = clock.monotonic() + seconds
        total = 0
        while clock.monotonic() < end:
            chunk = port.read(512)
            if chunk:
                f.write(chunk)
                f.flush()
                total += len(chunk)
                txt = chunk.decode("utf-8", "replace")
                if needles["fail_hardfault_dump"] in txt or needles["fail_hardfault_regs"] in txt:
                    return total, True
    return total, False


def read_until_prompt(port, run_root, fname, terminator, seconds, clock):
    """补正 5: 单调截止; 命中 terminator 即收尾; 分片同样即时落盘。"""
    path = run_root / fname
    buf = bytearray()
    with path.open("wb") as f:
        end = clock.monotonic() + seconds
        while clock.monotonic() < end:
            chunk = port.read(512)
            if chunk:
                f.write(chunk)
                f.flush()
                buf += chunk
                if buf.rstrip().endswith(terminator):
                    return bytes(buf), True
    return bytes(buf), False


def serial_stage(run_root, uid, argv_reset, open_port, clock, runner, out):
    """S3+S4: Open -> 复位 -> 启动采集 -> 提示符 -> 四条查询。任何异常都保证关句柄且字节已落盘。

    RESULT.json 在 finally 里**再写一次**: 判据(含 port_closed)必须在句柄真正关掉之后留档,
    否则成功路径的 RESULT.json 里 port_closed 永远是空 —— 这是 D 组端到端干跑抓出来的第 7 项。
    """
    port = open_port()
    try:
        try:
            got = _serial_body(port, run_root, argv_reset, clock, runner, out, PLAN["needles"])
        except Exception as e:                            # 补正 4: 读取异常也要具名停止, 且字节已在盘上
            out["serial_exception"] = "%s: %s" % (type(e).__name__, e)
            got = stop(out, run_root, "SERIAL_READ_EXCEPTION", out["serial_exception"])
        return got
    finally:
        try:
            port.close()
            out["port_closed"] = True
        except Exception as e:                            # 关闭也失败 => 具名, 不静默
            out["port_closed"] = False
            out["port_close_error"] = "%s: %s" % (type(e).__name__, e)
        result(out, run_root)


def _serial_body(port, run_root, argv_reset, clock, runner, out, n):
    port.reset_input_buffer()
    rc = runner("reset", argv_reset)
    (run_root / "S3_reset.rc.txt").write_text("%d\n" % rc["rc"], encoding="ascii", newline="\n")
    if rc["rc"] != 0:                                 # 补正 4: 复位非零即停, 一条查询都不发
        return stop(out, run_root, "RESET_FAILED", "rc=%d" % rc["rc"])
    nb, hardfault = capture_window(port, run_root, "S3_boot_capture.rx.bin", BOOT_WINDOW_S, clock, n)
    out["boot_bytes"] = nb
    boot = (run_root / "S3_boot_capture.rx.bin").read_bytes().decode("utf-8", "replace")
    if hardfault:
        return stop(out, run_root, "HARDFAULT_NEEDLE_SEEN", "boot capture")
    if n["boot_complete"] not in boot:
        return stop(out, run_root, "BOOT_INCOMPLETE", "boot_bytes=%d" % nb)
    # 提示符探针: 只有 msh 提示符算交互; 回显与非空垃圾都不算(补正 5)
    w = port.write(b"\r")
    if w is not None and w != 1:
        return stop(out, run_root, "QUERY_SHORT_WRITE", "prompt probe wrote=%s" % w)
    pre, ok = read_until_prompt(port, run_root, "S4_prompt_probe.rx.bin", PROMPT.encode(), QUERY_WINDOW_S, clock)
    out["prompt_probe_bytes"] = len(pre)
    if not ok:
        return stop(out, run_root, "NO_PROMPT", "probe_bytes=%d" % len(pre))
    queries = {}
    for cmd in QUERIES:
        payload = ("%s\r" % cmd).encode()
        w = port.write(payload)
        if w is not None and w != len(payload):
            return stop(out, run_root, "QUERY_SHORT_WRITE", "%s wrote=%s" % (cmd, w))
        resp, ok = read_until_prompt(port, run_root, "S4_%s.rx.bin" % cmd, PROMPT.encode(), QUERY_WINDOW_S, clock)
        text = resp.decode("utf-8", "replace")
        echoed = cmd in text
        body = text.replace(cmd, "", 1) if echoed else text
        body_bytes = len(re.sub(r"\s", "", body.replace(PROMPT, "")).encode())
        if not ok:
            return stop(out, run_root, "QUERY_TIMEOUT", "%s bytes=%d" % (cmd, len(resp)))
        if not echoed:
            return stop(out, run_root, "QUERY_INCOMPLETE", "%s no echo bytes=%d" % (cmd, len(resp)))
        if body_bytes == 0:
            return stop(out, run_root, "QUERY_INCOMPLETE", "%s echo/prompt only" % cmd)
        queries[cmd] = {"bytes": len(resp), "body_nonspace_bytes": body_bytes}
    out["queries"] = queries
    out["stop_label"] = None
    out["verdict"] = "CAPTURE_COMPLETE_REVIEW_PENDING"
    return result(out, run_root)


def real(run_root, uid_file, approve, runner=None, open_port=None, clock=None):
    """真跑。授权来源 = 操作者/复审本轮真实回复的**原文**, 逐字存进 run 根; 令牌为空即拒绝。"""
    out = {"mode": "real", "run_root": run_root.as_posix()}
    if not approve or not approve.strip():
        print("REFUSED: real 模式需要操作者/复审的原文授权令牌(--approve '原文'), 空即拒绝")
        return 2
    (run_root / "APPROVAL_TOKEN.verbatim.txt").write_text(approve + "\n", encoding="utf-8", newline="\n")
    probs = verify_accepted_literals()
    if probs:
        return report(stop(out, run_root, "PRODUCT_IDENTITY_MISMATCH", probs))
    probs = verify_products()
    if probs:
        return report(stop(out, run_root, "PRODUCT_IDENTITY_MISMATCH", probs))
    if not uid_file or not pathlib.Path(uid_file).exists():
        return report(stop(out, run_root, "PRE_READ_MISSING_FILE", "UID 文件不在场"))
    uid = uid_from_file(uid_file)
    out["uid_disclosure"] = uid_disclosure(uid)
    clock = clock or RealClock()
    runner = runner or (lambda name, argv: run_tool(name, argv, run_root))
    probs = gate_check(uid, run_root)
    if probs:
        return report(stop(out, run_root, "PLAN_GATE_REFUSED", probs))
    (run_root / "PRODUCT_IDENTITY.txt").write_text(
        "\n".join("%s bytes=%d sha256=%s on_disk_recheck=OK" % (k, v["bytes"], v["sha256"])
                  for k, v in ACCEPTED.items()) + "\n", encoding="utf-8", newline="\n")

    argv = {name: expand(t["argv"], run_root, uid) for name, t in PLAN["tools"].items() if isinstance(t, dict)}
    for name, a in argv.items():
        g = argv_gate(name, a, uid, run_root)
        if g:
            return report(stop(out, run_root, "PLAN_GATE_REFUSED", g))
        (run_root / ("ARGV.masked.%s.txt" % name)).write_text(
            " ".join(mask(a, uid)) + "\n", encoding="utf-8", newline="\n")

    r = runner("read_ram", argv["read_ram"])
    out["S-1_rc"] = r["rc"]
    state = last_core_state(r["stdout"].decode("utf-8", "replace"))
    for rd in PLAN["pre_download_reads"]:
        p = run_root / (rd["name"] + ".bin")
        got = p.stat().st_size if p.exists() else -1
        if got != rd["bytes"]:
            return report(stop(out, run_root, "PRE_READ_SHORT_FILE", "%s want=%d got=%d" %
                               (rd["name"], rd["bytes"], got)))
    out["core_state_before_download"] = state
    if state != "Halted":
        return report(stop(out, run_root, "CORE_NOT_HALTED", "state=%s" % state))
    out["A_sentinels"] = sentinels(run_root / "A_locals.bin")

    r = runner("download", argv["download"])
    out["S1_rc"] = r["rc"]
    if r["rc"]:
        return report(stop(out, run_root, "DOWNLOAD_FAILED", "rc=%d" % r["rc"]))
    r = runner("readback", argv["readback"])
    out["S2_rc"] = r["rc"]
    rb = run_root / "flash_readback.bin"
    if not rb.exists():
        return report(stop(out, run_root, "READBACK_MISSING", str(rb)))
    if rb.stat().st_size != ACCEPTED["image_under_test"]["bytes"]:
        return report(stop(out, run_root, "READBACK_SHORT", "%d" % rb.stat().st_size))
    if sha256_file(rb) != ACCEPTED["image_under_test"]["sha256"]:
        return report(stop(out, run_root, "READBACK_MISMATCH", "见 flash_readback.bin"))
    out["readback_byte_equal"] = True

    got = serial_stage(run_root, uid, argv["reset"],
                       open_port or make_real_port, clock, runner, out)
    print(json.dumps({k: got.get(k) for k in ("stop_label", "verdict", "boot_bytes", "queries",
                                              "readback_byte_equal", "core_state_before_download")},
                     ensure_ascii=False))
    return 0 if got.get("verdict") == "CAPTURE_COMPLETE_REVIEW_PENDING" else 4


def sentinels(path):
    data = pathlib.Path(path).read_bytes()
    words = [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data) - 3, 4)]
    return {"node@+0x08": "0x%08x" % words[2] if len(words) > 2 else None,
            "object@+0x1c": "0x%08x" % words[7] if len(words) > 7 else None}


def make_real_port():
    """补正 4 的真实分支: 参数一次给全, 打开后不赋任何设置属性(P0/R2 已证)。"""
    import serial
    s = PLAN["serial"]
    return serial.Serial(s["port"], s["baud"], timeout=0.2, rtscts=False, dsrdtr=False, xonxoff=False,
                         bytesize=8, parity="N", stopbits=1)


def report(out):
    print(json.dumps(out, ensure_ascii=False))
    return 3 if out.get("stop_label") else 0


def sim(run_root, emit):
    run_root.mkdir(parents=True, exist_ok=True)
    probs = gate_check(None, run_root)
    lines = ["MODE=sim (零设备动作: 没有 pyocd/Keil/串口调用)", "PLAN_GATE problems=%d %s" % (len(probs), probs)]
    lines.append("ACCEPTED_LITERALS vs %s: %s" % (FW_SUMS.relative_to(ROOT).as_posix(),
                                                  verify_accepted_literals() or "逐字相符"))
    for st in PLAN["stages"]:
        tool = st.get("tool")
        lines.append("stage %s tool=%s device_actions=%s" % (st["id"], tool, st["device_actions"]))
        if tool:
            argv = expand(PLAN["tools"][tool]["argv"], run_root, None)
            lines.append("   argv: " + " ".join(mask(argv, None)))
    lines.append("product_files(bytes/sha256) 复核=推迟到 real(S0) : 见 ACCEPTED 常量")
    lines.append("prompt_marker=%r boot_window=%ds query_window=%ds queries=%s" %
                 (PROMPT, BOOT_WINDOW_S, QUERY_WINDOW_S, list(QUERIES)))
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


def cont_only_reset(name):
    """续测入口的硬门: 除 reset 外任何工具名一律拒绝(整包 real() 不在这里可达)。"""
    if name != "reset":
        raise AssertionError("cont 只允许 reset, 实际请求 %s" % name)


CONT = {
    "quota": {"port_open": 1, "explicit_reset": 1, "boot_window_s": BOOT_WINDOW_S, "queries": list(QUERIES),
              "read_ram": 0, "download": 0, "flash_readback": 0, "flash_write": 0, "ram_write": 0,
              "motor_run": 0, "config_save": 0, "blackbox_clear": 0, "rebuild": 0, "retry": 0},
    "image_proven_on_board": {
        "run_root": "reports/reviews/phase8-m8-diag-stack-r1/field/field-20261101T084256Z",
        "readback_file": "flash_readback.bin", "bytes": 290916,
        "sha256": "f5c2fd89bbf28255a5b26c9cca495bc0fb1e39d401d0bb7bea19d051221708ce",
        "proven": "S2 唯一一次回读 rc=0 且与该散列逐字节相同(run_record §12); 复审亦独立复算确认",
        "note": "续测**不再**下载与回读: 那两项配额已用尽, 镜像身份已证"},
    "port_open_ledger_before": [
        {"when_utc": "2026-11-01T08:42Z 前", "what": "COM5 空闲探测(打开后立即关闭)",
         "counted_as": "port_open 1 —— 复审指出: 探测本身就是开口, 必须入台账"},
        {"when_utc": "2026-11-01T08:43Z", "what": "崩溃那次 real() 里 make_real_port() 已开口(未关闭即抛异常)",
         "counted_as": "port_open 2 —— 绑定包内的那一次, 句柄随进程退出释放"},
    ],
    "next_open_is": "port_open 3 (正式会话; 不再做任何额外开口探测)",
}


def prior_image_proven(prior_dir):
    """续测入口的镜像前提: 既往那一次**唯一**回读的原字节必须仍在, 且等于固定接受散列。"""
    rb = pathlib.Path(prior_dir) / CONT["image_proven_on_board"]["readback_file"]
    return rb.exists() and sha256_file(rb) == ACCEPTED["image_under_test"]["sha256"]


def cont(run_root, uid_file, approve, runner=None, open_port=None, clock=None, prior_run=None):
    """S3/S4 续测入口: 复用 serial_stage(), **不调用整包 real()**, 工具调用只允许 reset。"""
    out = {"mode": "cont", "run_root": run_root.as_posix(), "quota": CONT["quota"]}
    if not approve or not approve.strip():
        print("REFUSED: 续测同样需要原文授权令牌(--approve '原文'), 空即拒绝")
        return 2
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "APPROVAL_TOKEN.verbatim.txt").write_text(approve + "\n", encoding="utf-8", newline="\n")
    probs = verify_accepted_literals() + verify_products()
    if probs:
        return report(stop(out, run_root, "PRODUCT_IDENTITY_MISMATCH", probs))
    prior = pathlib.Path(prior_run) if prior_run else (ROOT / CONT["image_proven_on_board"]["run_root"])
    if not prior_image_proven(prior):
        return report(stop(out, run_root, "PRIOR_READBACK_NOT_PROVEN", str(prior)))
    out["image_proven_on_board"] = CONT["image_proven_on_board"]
    if not uid_file or not pathlib.Path(uid_file).exists():
        return report(stop(out, run_root, "PRE_READ_MISSING_FILE", "UID 文件不在场"))
    uid = uid_from_file(uid_file)
    out["uid_disclosure"] = uid_disclosure(uid)
    argv_reset = expand(PLAN["tools"]["reset"]["argv"], run_root, uid)
    g = argv_gate("reset", argv_reset, uid, run_root)
    if g:
        return report(stop(out, run_root, "PLAN_GATE_REFUSED", g))
    (run_root / "ARGV.masked.reset.txt").write_text(" ".join(mask(argv_reset, uid)) + "\n",
                                                    encoding="utf-8", newline="\n")
    clock = clock or RealClock()
    called = []

    def only_reset(name, argv):
        cont_only_reset(name)
        called.append(name)
        return (runner or (lambda n, a: run_tool(n, a, run_root)))(name, argv)

    got = serial_stage(run_root, uid, argv_reset, open_port or make_real_port, clock, only_reset, out)
    got["tools_called"] = called
    got["port_open_ledger"] = {"before": len(CONT["port_open_ledger_before"]), "this_run": 1,
                               "next_open_is": CONT["next_open_is"]}
    if called != ["reset"]:
        return report(stop(got, run_root, "CONT_TOOL_CARDINALITY", str(called)))
    result(got, run_root)   # 末次重写: tools_called 与开口台账也必须进得了归档
    print(json.dumps({k: got.get(k) for k in ("stop_label", "verdict", "boot_bytes", "queries",
                                              "port_closed", "tools_called")}, ensure_ascii=False))
    return 0 if got.get("verdict") == "CAPTURE_COMPLETE_REVIEW_PENDING" else 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sim", "real", "cont"], default="sim")
    ap.add_argument("--emit-plan", type=pathlib.Path, default=None)
    ap.add_argument("--run-root", type=pathlib.Path, default=None)
    ap.add_argument("--uid-file", default=str(UID_DEFAULT))
    ap.add_argument("--approve", default=None)
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_root = a.run_root or (FIELD / ({"sim": "sim-%s", "real": "field-%s",
                                        "cont": "cont-%s"}[a.mode] % stamp))
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "MODE.txt").write_text("%s %s\n" % (a.mode, stamp), encoding="utf-8", newline="\n")
    if a.mode == "sim":
        return sim(run_root, a.emit_plan)
    if a.mode == "cont":
        return cont(run_root, a.uid_file, a.approve)
    return real(run_root, a.uid_file, a.approve)


if __name__ == "__main__":
    sys.exit(main())
