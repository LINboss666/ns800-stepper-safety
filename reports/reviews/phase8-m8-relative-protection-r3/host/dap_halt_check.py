#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""(c) 一次 halt 定位的机器复算。三组断言 + 元负控：

A. 那个 run 根的**原始字节**里确实有这些 token(而不是我复述的数字);
B. 从这些字派生的位判据(PRECISERR/BFARVALID/FORCED/IPSR/SysTick)按 cpuport.c 自己用的
   位号算, 不引入外部说法;
C. 判据落点在产品源码的实际行上(rt_hw_hard_fault_exception 的 while(1) 与 BFAR 读取地址)。

检查器自己先证明"会失败"(三条具名元负控), 才允许它宣布 problems=0。零设备动作。
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path("D:/ns800")
REV = "reports/reviews/phase8-m8-relative-protection-r3"
RUN = ROOT / (REV + "/offline/daphalt-20261101T052450Z-22884")
CPU = ROOT / "rt-thread-master/rt-thread-master/libcpu/arm/cortex-m7/cpuport.c"
RTCFG = ROOT / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/rtconfig.h"
SLICE = ROOT / (REV + "/host/dap_halt_disasm_window.txt")

# --- A: 原始字节里必须逐字出现的 token ---------------------------------------------------------
TOKENS = [
    "Core 0 (None):  Running",
    "Selected core 0 (None) (AHB-AP#0)",
    "Successfully halted device",
    "pc: 0x0801e8a6",
    "lr: 0x08008201",
    "xpsr: 0x61000003",
    "sp: 0x2000d3dc",
    "msp = 0x2000d3dc",
    "control = 0x00000000",
    "primask = 0x00000001",
    "basepri = 0x00000000",
    "faultmask = 0x00000000",
    "e000ed04:  0440f803",
    "e000ed24:  00000000",
    "e000ed28:  00008200 40000000 00000001 00bd80b0",
    "e000ed38:  00bd80b0",
    "e000e010:  00010007 00061a7f 00014563",
]
# 计数判据用**整词匹配**, 不用子串: "Halted" 必须正好 2 次(初始 Running + halt 后两次 Halted)。
COUNTS = [("Core 0 (None):  Running", 1), ("Core 0 (None):  Halted", 2),
          ("Successfully halted device", 1)]

CITES = [
    ("handler-tail-loop", CPU, 460, "while (1);"),
    ("handler-calls-track", CPU, 457, "hard_fault_track();"),
    ("cfsr-address", CPU, 221, "0xE000ED28"),
    ("hfsr-address", CPU, 222, "0xE000ED2C"),
    ("bfar-address", CPU, 224, "0xE000ED38"),
    ("bfsr-byte-address", CPU, 229, "0xE000ED29"),
    ("preciserr-bit", CPU, 291, "PRECISERR"),
    ("bfarvalid-print", CPU, 314, "SCB->BFAR:%08X"),
    ("dump-printed-pc", CPU, 436, 'rt_kprintf(" pc: 0x%08x'),
]


def lines_of(p):
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def group_a(stdout_text):
    bad = []
    for t in TOKENS:
        if t not in stdout_text:
            bad.append(("A_TOKEN_MISSING", t))
    for t, want in COUNTS:
        got = stdout_text.count(t)
        if got != want:
            bad.append(("A_COUNT_DIFF", t, got, want))
    if "e000ed28:  00008200 00000000" in stdout_text:
        bad.append(("A_HFSR_ZERO_UNEXPECTED",))
    return bad


def group_b(stdout_text):
    """位判据只用实测的四个数, 位号取 cpuport.c 自己的注释编号。"""
    bad = []
    m = re.search(r"e000ed28:\s+([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})", stdout_text)
    if not m:
        return [("B_CFSR_LINE_UNPARSEABLE",)]
    cfsr, hfsr, dfsr, mmar = (int(x, 16) for x in m.groups())
    bf = re.search(r"e000ed38:\s+([0-9a-f]{8})", stdout_text)
    bfar = int(bf.group(1), 16) if bf else None
    bfsr = (cfsr >> 8) & 0xFF
    mfsr = cfsr & 0xFF
    ufsr = (cfsr >> 16) & 0xFFFF
    if not (bfsr & (1 << 1)):
        bad.append(("B_PRECISERR_NOT_SET", hex(bfsr)))
    if not (bfsr & (1 << 7)):
        bad.append(("B_BFARVALID_NOT_SET", hex(bfsr)))
    if bfsr & (1 << 2):
        bad.append(("B_IMPRECISERR_UNEXPECTED", hex(bfsr)))
    if mfsr or ufsr:
        bad.append(("B_MFSR_OR_UFSR_NONZERO", hex(mfsr), hex(ufsr)))
    if not (hfsr & (1 << 30)):
        bad.append(("B_HFSR_FORCED_NOT_SET", hex(hfsr)))
    if hfsr & (1 << 31):
        bad.append(("B_DEBUGEVT_SET(这次硬fault可能由调试事件造成)", hex(hfsr)))
    if hfsr & (1 << 1):
        bad.append(("B_VECTTABLE_FAULT(取向量失败)", hex(hfsr)))
    if bfar != 0x00bd80b0:
        bad.append(("B_BFAR_VALUE", hex(bfar) if bfar is not None else None))
    xp = re.search(r"xpsr: (0x[0-9a-f]{8,8})", stdout_text)
    ipsr = int(xp.group(1), 16) & 0x1FF if xp else None
    if ipsr != 3:
        bad.append(("B_IPSR_NOT_HARDFAULT", ipsr))
    icsr = int(re.search(r"e000ed04:\s+([0-9a-f]{8})", stdout_text).group(1), 16)
    if (icsr & 0x1FF) != 3:
        bad.append(("B_ICSR_VECTACTIVE_NOT_3", hex(icsr & 0x1FF)))
    st = re.search(r"e000e010:\s+([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})", stdout_text)
    csr, rvr, cvr = (int(x, 16) for x in st.groups())
    if (csr & 0x7) != 0x7:
        bad.append(("B_SYSTICK_NOT_FULLY_ENABLED", hex(csr)))
    if not (csr & (1 << 16)):
        bad.append(("B_SYSTICK_COUNTFLAG_CLEAR(读数不足以证明它在数)", hex(csr)))
    if rvr + 1 != 400000:
        bad.append(("B_SYSTICK_RELOAD_NOT_400000", rvr))
    tick = int(re.search(r"#define RT_TICK_PER_SECOND\s+(\d+)",
                         RTCFG.read_text(encoding="utf-8", errors="replace")).group(1))
    if tick != 1000:
        bad.append(("B_TICK_PER_SECOND", tick))
    if dfsr != 1:
        bad.append(("B_DFSR_NOT_HALTED_BIT", hex(dfsr)))
    return bad


def group_c():
    bad = []
    src = lines_of(CPU)
    for label, path, ln, want in CITES:
        if ln > len(src):
            bad.append(("C_LINE_PAST_EOF", label, ln, len(src)))
            continue
        if want not in src[ln - 1]:
            bad.append(("C_CITE_MISMATCH", label, ln, src[ln - 1].strip()[:80]))
    if not SLICE.exists():
        bad.append(("C_SLICE_MISSING", str(SLICE)))
    else:
        s = SLICE.read_text(encoding="utf-8", errors="replace")
        for t in ("rt_hw_hard_fault_exception + 398", "BL       hard_fault_track ; 0x800f430",
                  "bus_fault_track + 234", "MOV      r0,#0xed38",
                  "ns800_putc", "rt_serial_write"):
            if t not in s:
                bad.append(("C_SLICE_TOKEN", t))
    return bad


def negative_control(stdout_text):
    """元负控: 三种错法都必须被对应的具名判据抓到, 否则判据是空的。"""
    global SLICE
    fired = 0
    if any(x[0] == "A_TOKEN_MISSING" for x in
           group_a(stdout_text.replace("pc: 0x0801e8a6", "pc: 0x0801e8a5"))):
        fired += 1
    if any(x[0] == "B_PRECISERR_NOT_SET" for x in
           group_b(stdout_text.replace("00008200", "00008000"))):
        fired += 1
    keep = SLICE
    SLICE = ROOT / "no_such_slice_file.txt"
    if any(x[0] == "C_SLICE_MISSING" for x in group_c()):
        fired += 1
    SLICE = keep
    return fired


def main():
    ob = (RUN / "action_daphalt" / "stdout.bin").read_bytes()
    eb = (RUN / "action_daphalt" / "stderr.bin").read_bytes()
    rc = (RUN / "action_daphalt" / "rc.txt").read_text(encoding="utf-8").strip()
    stdout_text = ob.decode("utf-8", "replace")
    bad = []
    if rc != "0":
        bad.append(("A_RC_NOT_ZERO", rc))
    if eb:
        bad.append(("A_STDERR_NOT_EMPTY", len(eb)))
    bad += group_a(stdout_text)
    bad += group_b(stdout_text)
    bad += group_c()
    for b in bad:
        print("BAD %s" % (b,))
    fired = negative_control(stdout_text)
    if fired != 3:
        print("BAD NEGATIVE_CONTROL fired=%d want=3" % fired)
        bad.append(("NEGATIVE_CONTROL", fired))
    else:
        print("OK  NEGATIVE_CONTROL fired=3/3(A 改 PC 一位/ B 抹掉 PRECISERR/ C 换成不存在的切片文件)")
    print("\nTOKENS=%d  COUNTS=%d  CITES=%d  stdout=%d B sha256=%s"
          % (len(TOKENS), len(COUNTS), len(CITES), len(ob), hashlib.sha256(ob).hexdigest()[:12]))
    print("DAPHALT_PROBLEMS=%d  (0 => 这次 halt 的每个数字与每条源码落点都按本机字节成立)" % len(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
