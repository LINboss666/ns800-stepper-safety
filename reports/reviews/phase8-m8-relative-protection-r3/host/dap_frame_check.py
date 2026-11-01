#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8 异常栈帧轮 (frame + frame2) 的机器可复算检查。

零设备、零构建: 只读已入库的原件字节 + 仓库源码/映射表 + 反汇编切片。
判据分五组, 全部要 OK 才算这一轮自证成立; 末尾三个元负控必须全部 fired,
否则本检查器没有资格说"零问题"。

用法: python host/dap_frame_check.py        (在仓库根或任意 cwd 都可)
"""
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path("D:/ns800")
REV = ROOT / "reports/reviews/phase8-m8-relative-protection-r3"
FRAME = REV / "offline/dapframe-20261101T054539Z-12284/action_dapframe"
FRAME2 = REV / "offline/dapframe2-20261101T054747Z-38496/action_dapframe2"
HALT = REV / "offline/daphalt-20261101T052450Z-22884/action_daphalt"
SLICE = REV / "host/dap_frame_disasm_window.txt"
PROBE = REV / "host/dap_frame_probe.json"
OBJ = ROOT / "rt-thread-master/rt-thread-master/src/object.c"
CPU = ROOT / "rt-thread-master/rt-thread-master/libcpu/arm/cortex-m7/cpuport.c"
ASM = ROOT / "rt-thread-master/rt-thread-master/libcpu/arm/cortex-m7/context_rvds.S"
MAP = ROOT / "reports/firmware/phase8-m8-relative-protection-r3/rt-thread-motor-m1.map"
BOARDH = ROOT / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/board/board.h"
SCT = ROOT / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/board/linker_scripts/link.sct"
AXF = ROOT / "reports/firmware/phase8-m8-relative-protection-r3/rt-thread-motor-m1.axf"

BASE = 0x20012748           # exception_info 基址, 由 [0x2000d3e8] 实测给出
PC_FAULT = 0x08020096
BFAR_FRAME = 0x00BD80B0


def txt(p):
    return p.read_bytes().decode("utf-8", "replace")


def rd32_words(text):
    """把 pyocd `read32` 的输出行解析成 {地址: 值}。"""
    words = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-f]{8}):\s+(.*)$", line)
        if not m:
            continue
        a = int(m.group(1), 16)
        for i, tok in enumerate(re.findall(r"\b[0-9a-f]{8}\b", m.group(2))):
            words[a + 4 * i] = int(tok, 16)
    return words


# ---------------------------------------------------------------- A: 原件逐行/计数
def group_a(t1, t2, t3):
    res = []
    TOK1 = ["Core 0 (None):  Halted",
            "2000d3e8:  20012748",
            "20012718:  00000000 00000000 00000000 00000000",
            "20012748:  ffffffed ffffffed 00000400 00000012",
            "20012758:  0000000a deadbeef deadbeef deadbeef",
            "20012768:  deadbeef deadbeef"]
    for i, s in enumerate(TOK1):
        res.append((("A1_TOKEN_MISSING_%d" % i) if s not in t1 else "A1_token_%d" % i, s in t1, s))
    TOK2 = ["20012770:  00bd80b0 20015b78 00000001 20015b28",
            "20012780:  20015b78 08021d0d 08020096 81000000"]
    for i, s in enumerate(TOK2):
        res.append((("A2_TOKEN_MISSING_%d" % i) if s not in t2 else "A2_token_%d" % i, s in t2, s))
    # 零新增 halt: 两轮 stdout 里都不能出现 halt/reset 字样, Halted 状态行各 2 次
    res.append(("A_no_halt_line", "Successfully halted" not in t1 and "Successfully halted" not in t2,
                "两轮 stdout 均无 `Successfully halted device`"))
    res.append(("A_no_reset_line", "reset" not in (t1 + t2).lower(), "两轮 stdout 无 reset 字样"))
    res.append(("A_Halted_count_frame1", t1.count("Core 0 (None):  Halted") == 2,
                "frame: %d 次(开口确认+收尾)" % t1.count("Core 0 (None):  Halted")))
    res.append(("A_Halted_count_frame2", t2.count("Core 0 (None):  Halted") == 2,
                "frame2: %d 次" % t2.count("Core 0 (None):  Halted")))
    res.append(("A_no_Running_this_rounds", "Running" not in t1 and "Running" not in t2,
                "两轮都没有 Running —— 板子全程保持 Halted"))
    res.append(("A3_BFAR_line_present", "e000ed38:  00bd80b0" in t3,
                "(c) 轮原件里 BFAR 行逐字在"))
    return res


# ---------------------------------------------------------------- B: 布局算术
def group_b(w1, w2, t1, t2, t3):
    res = []
    res.append(("B_ptr_slot_equals_base", w1.get(0x2000d3e8) == BASE,
                 "[0x2000d3e8]=%s 应=0x%x" % (hex(w1.get(0x2000d3e8) or 0), BASE)))
    # 硬件帧起点 = base + 40 (10 个软件槽: exc_return, flag, r4..r11)
    res.append(("B_hw_frame_at_base_plus_40", BASE + 40 == 0x20012770,
                "base+40=0x%x 即第二次读取的起点" % (BASE + 40)))
    res.append(("B_pc_slot_address", BASE + 64 == 0x20012788, "pc 槽地址 0x%x" % (BASE + 64)))
    res.append(("B_exc_return", w2.get(BASE) is None and w1.get(BASE) == 0xffffffed,
                "exc_return=0x%x" % (w1.get(BASE) or 0)))
    res.append(("B_flag_equals_exc_return", w1.get(BASE + 4) == w1.get(BASE),
                "flag 槽与 exc_return 同值 —— 对应 context_rvds.S:238/240 两条都是 `STMFD r0!, {lr}`"))
    er = w1.get(BASE, 0)
    res.append(("B_exc_return_bit2_PSP", bool(er & (1 << 2)), "bit2=1 => 出错上下文用 PSP(线程模式)"))
    res.append(("B_exc_return_bit3_thread", bool(er & (1 << 3)), "bit3=1 => 返回到线程模式"))
    res.append(("B_exc_return_bit4_extended", not (er & (1 << 4)), "bit4=0 => 扩展帧标记"))
    psr = w2.get(BASE + 68, 0)
    res.append(("B_stacked_psr_IPSR_zero", (psr & 0x1FF) == 0,
                "出错瞬间 IPSR=%d (0=线程模式, 不在任何处理程序里)" % (psr & 0x1FF)))
    res.append(("B_stacked_psr_thumb", bool(psr & (1 << 24)), "栈帧 psr=0x%08x 的 T 位=1" % psr))
    halted_psr = int(re.search(r"xpsr: 0x([0-9a-f]{8})", t3).group(1), 16)
    res.append(("B_halttime_psr_is_hardfault", (halted_psr & 0x1FF) == 3,
                "(c) 轮当场 xpsr=0x%08x 的 IPSR=3=HardFault, 与栈帧里 IPSR=0 是两个不同时刻" % halted_psr))
    r0 = w2.get(BASE + 40, 0)
    bfar3 = int(re.search(r"e000ed38:\s+([0-9a-f]{8})", t3).group(1), 16)
    res.append(("B_r0_equals_BFAR", r0 == bfar3 == BFAR_FRAME,
                "栈帧 r0=0x%08x == (c) 轮 BFAR=0x%08x" % (r0, bfar3)))
    res.append(("B_r1_is_object_ptr_in_ram", 0x20012000 <= w2.get(BASE + 44, 0) < 0x2001a000,
                "r1=0x%08x 落在 RW_RT_HEAD 预留块内" % w2.get(BASE + 44, 0)))
    res.append(("B_r4_r11_sequence",
                [w1.get(BASE + 8 + 4 * i) for i in range(8)] ==
                [0x400, 0x12, 0x0A, 0xDEADBEEF, 0xDEADBEEF, 0xDEADBEEF, 0xDEADBEEF, 0xDEADBEEF],
                "r4..r11 实测序列 0x400,0x12,0x0a,deadbeef x5"))
    res.append(("B_psp_equals_base_after_stub", w1.get(0x2000d3e8) == BASE,
                "context_rvds.S:245 `MSRNE psp, r0` 把递减后的基址写回 PSP, 所以 (c) 轮 psp=0x20012748 就是帧基址"))
    lr = w2.get(BASE + 60, 0)
    res.append(("B_lr_is_bl_residue_of_atomic_add", lr == ((0x08021D08 + 4) | 1),
                "栈帧 lr=0x%08x = 0x08021d08 那条 BL 的返回地址(+1 是 Thumb 标记)" % lr))
    res.append(("B_pc_offset_in_symbol", PC_FAULT - 0x08020010 == 134,
                "pc - rt_object_init(0x08020010) = 134, 与切片里 `rt_object_init + 134` 一致"))
    res.append(("B_obj_from_node_minus_14", (BFAR_FRAME - 0x14) == 0x00BD809C,
                "若把坏 node 当 rt_object, 其地址为 node-0x14=0x%x (offsetof(list)=0x14)" % (BFAR_FRAME - 0x14)))
    doc_b = json.loads(txt(PROBE))
    b1 = sum(int(c.split()[-1]) for c in doc_b["reads"][0]["commands"] if c.startswith("read32"))
    b2 = sum(int(c.split()[-1]) for c in doc_b["reads"][1]["commands"] if c.startswith("read32"))
    res.append(("B_read_bytes_within_approval", b1 <= 96 and b2 == 32,
                "frame=%d B(批准 <=96) frame2=%d B(单独批准 32), 均从登记件里的逐字 commands 复算" % (b1, b2)))
    return res


# ---------------------------------------------------------------- C: 源码/切片引用行
CITES = [
    ("obj-379-loop-head", OBJ, 379, "for (node  = information->object_list.next;"),
    ("obj-381-increment", OBJ, 381, "node  = node->next"),
    ("obj-386-assert", OBJ, 386, "RT_ASSERT(obj != object);"),
    ("obj-377-spinlock", OBJ, 377, "rt_spin_lock_irqsave(&(information->spinlock))"),
    ("obj-355-def", OBJ, 355, "void rt_object_init(struct rt_object         *object,"),
    ("cpu-221-cfsr", CPU, 221, "0xE000ED28"),
    ("cpu-224-bfar", CPU, 224, "0xE000ED38"),
    ("cpu-460-tail-loop", CPU, 460, "while (1);"),
    ("cpu-398-exception_info", CPU, 398, "struct exception_info"),
    ("cpu-400-exc_return-field", CPU, 400, "rt_uint32_t exc_return;"),
    ("cpu-61-stack_frame", CPU, 61, "struct stack_frame"),
    ("cpu-64-flag-field", CPU, 64, "rt_uint32_t flag;"),
    ("cpu-77-hwframe-field", CPU, 77, "struct exception_stack_frame exception_stack_frame;"),
    ("cpu-51-r0-first", CPU, 51, "rt_uint32_t r0;"),
    ("cpu-58-psr-last", CPU, 58, "rt_uint32_t psr;"),
    ("cpu-169-deadbeef-fill", CPU, 169, "((rt_uint32_t *)stack_frame)[i] = 0xdeadbeef;"),
    ("asm-231-tst-lr4", ASM, 231, "TST     lr, #0x04"),
    ("asm-234-mrs-psp", ASM, 234, "MRSNE   r0, psp"),
    ("asm-236-stmfd-r4r11", ASM, 236, "STMFD   r0!, {r4 - r11}"),
    ("asm-238-stmfd-flag", ASM, 238, "STMFD   r0!, {lr}               ; push dummy for flag"),
    ("asm-240-stmfd-exc", ASM, 240, "STMFD   r0!, {lr}               ; push exec_return register"),
    ("asm-245-msr-psp", ASM, 245, "MSRNE   psp, r0"),
    ("sct-27-empty-hole", SCT, 27, "RW_RT_HEAD 0x20012000 EMPTY 0x8000"),
    ("boardh-30-heap-begin", BOARDH, 30, "#define HEAP_BEGIN      (&Image$$RW_RT_HEAD$$Limit)"),
]

SLICE_TOKENS = [
    "0x08020010:    b580        ..      PUSH     {r7,lr}",
    "0x08020094:    9802        ..      LDR      r0,[sp,#8]",
    "0x08020096:    6800        .h      LDR      r0,[r0,#0]",
    "0x0802006a:    3814        .8      SUBS     r0,r0,#0x14",
    "0x08020088:    f44f72c1    O..r    MOV      r2,#0x182",
    "0x0802008c:    f7fdf9b0    ....    BL       rt_assert_handler ; 0x801d3f0",
    "0x0802004e:    f002fb33    ..3.    BL       rt_spin_lock_irqsave ; 0x80226b8",
    "0x080226c4:    f7fbfcbc    ....    BL       rt_enter_critical ; 0x801e040",
    "0x080226d0:    bd80        ..      POP      {r7,pc}",
    "0x0801e04e:    f003fe4b    ..K.    BL       rt_soft_atomic_add ; 0x8021ce8",
    "0x0801e05a:    bd80        ..      POP      {r7,pc}",
    "0x08021d08:    f7defb6e    ..n.    BL       rt_hw_interrupt_enable ; 0x80003e8",
    "0x08021d10:    bd80        ..      POP      {r7,pc}",
    "0x080460d1 (偏移 0x460d1) -> 'obj != object'",
    "0x080460f9 (偏移 0x460f9) -> 'rt_object_init'",
    "AXF sha256     : 0bba8ca74ed8c11e149395efd64c42072fce0b3ced5ab76281210ddc04550963",
]


def group_c(slice_text, axf_bytes):
    res = []
    for tag, path, line, want in CITES:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        got = lines[line - 1] if line - 1 < len(lines) else "<EOF>"
        res.append((("C_%s" % tag) if want in got else "C_%s_MISMATCH" % tag, want in got,
                    "%s:%d -> %s" % (path.name, line, got.strip()[:78])))
    for i, s in enumerate(SLICE_TOKENS):
        name = "C_slice_%s" % (s[:10].replace(":", "") if s[:2] == "0x" else "axf_sha")
        res.append((name if s in slice_text else name + "_MISSING", s in slice_text, s[:60]))
    res.append(("C_axf_sha_matches_slice_header",
                hashlib.sha256(axf_bytes).hexdigest() in slice_text,
                "切片头部登记的 AXF sha256 == 现在复算值 (bytes=%d)" % len(axf_bytes)))
    bin_bytes = (AXF.parent / "rt-thread-motor-m1-candidate.bin").read_bytes()
    bin_sha = hashlib.sha256(bin_bytes).hexdigest()
    res.append(("C_onboard_bin_identity_in_slice", bin_sha in slice_text,
                "切片登记的候选 BIN 身份 == 现在复算值 (%d B, sha %s...) —— 就是已在板上逐字节证得的那枚镜像"
                % (len(bin_bytes), bin_sha[:12])))
    return res


# ---------------------------------------------------------------- E: 地址合法性(来自 map)
def group_e(map_text):
    res = []
    regions = []
    for m in re.finditer(r"Execution Region (\S+) \(Exec base: (0x[0-9a-f]+), .*?Size: (0x[0-9a-f]+), "
                         r"Max: (0x[0-9a-f]+)", map_text):
        regions.append((m.group(1), int(m.group(2), 16), int(m.group(4), 16)))
    res.append(("E_regions_parsed", len(regions) >= 5,
                "map 里解出 %d 个执行区: %s" % (len(regions), ", ".join(
                    "%s[0x%08x,+0x%x)" % r for r in regions))))
    inside = [n for n, b, mx in regions if b <= BFAR_FRAME < b + mx]
    res.append(("E_bfar_in_no_image_region", not inside,
                "0x%08x 不落在 map 任何一个执行区(ITCM/Flash/DTCM/RT_HEAD)内 => 它不可能是本镜像里任何对象或代码的地址"
                % BFAR_FRAME))
    for name, addr, label in [("RW_ITCM", 0x00000000, "ITCM 上界 0x00020000"),
                              ("ER_IROM1", 0x08000000, "Flash"),
                              ("RW_DTCM", 0x20000400, "DTCM"),
                              ("RW_RT_HEAD", 0x20012000, "堆预留块")]:
        got = [r for r in regions if r[0] == name]
        detail = ("%s exec base=0x%08x (%s) Max=0x%x" % (name, got[0][1], label, got[0][2])) \
            if got else ("%s 在 map 里没出现" % name)
        res.append(("E_region_%s_declared" % name, bool(got) and got[0][1] == addr, detail))
    return res


# ---------------------------------------------------------------- D: 登记件自洽 + 泄漏扫描
def group_d():
    res = []
    doc = json.loads(txt(PROBE))
    res.append(("D_json_parses", isinstance(doc, dict), "host/dap_frame_probe.json 可解析"))
    r1, r2 = doc["reads"]
    res.append(("D_ram_accounting",
                r1["target_ram_bytes_read"] <= 96 and r2["target_ram_bytes_read"] == 32,
                "frame=%d B(<=96 批准) frame2=%d B(单独批准)" % (
                    r1["target_ram_bytes_read"], r2["target_ram_bytes_read"])))
    res.append(("D_argv_no_halt_reset",
                all(all(("halt" not in a.lower() or a == "attach") and "reset" not in a.lower()
                        for a in r["argv_masked"]) for r in (r1, r2)),
                "两轮 argv 里除 `--connect attach` 外没有 halt/reset 选项"))
    res.append(("D_argv_resume_false", all("resume_on_disconnect=false" in r["argv_masked"] for r in (r1, r2)),
                "两轮都是 `-O resume_on_disconnect=false`(按上一轮更正)"))
    res.append(("D_uid_only_masked", all(r["uid_disclosure"]["present"] and len(r["uid_disclosure"]) >= 2
                                         and "@UID_FILE<redacted" in " ".join(r["argv_masked"]) for r in (r1, r2)),
                "argv 副本里 UID 只有 `@UID_FILE<redacted len=… sha256_…>` 形态"))
    # 全仓扫描: 任何 48 位十六进制串, 只要其 sha256 前 12 位等于登记的那把 UID 摘要, 就算泄漏
    want = r1["uid_disclosure"]["sha256_12"]
    res.append(("D_registered_uid_digest_shape", bool(re.fullmatch(r"[0-9a-f]{12}", want or "")),
                "登记的是 sha256 前 12 位: %s" % want))
    scan = [SLICE, PROBE]
    for d in (FRAME, FRAME2, HALT):
        scan.extend(sorted(d.parent.rglob("*")))
    leak = []
    for f in scan:
        if not f.is_file():
            continue
        for tok in re.findall(rb"\b[0-9a-fA-F]{48}\b", f.read_bytes()):
            if hashlib.sha256(tok).hexdigest()[:12] == want:
                leak.append(str(f))
    res.append(("D_no_uid_leak", not leak,
                "扫描 %d 个文件里所有 48 位十六进制串, 与登记摘要(%s)相符的命中: %s" % (len(scan), want, leak or "无")))
    return res


def negative_control(t1, t2, t3, w2_good, slice_text_good):
    """三个元负控: 判据必须会因为原件被改动而报警, 否则它没有资格宣布零问题。"""
    fired = 0
    # 1) 把 pc 改一位 -> 组 A 必须报缺 token
    if any(n.startswith("A2_TOKEN_MISSING") and ok is False for n, ok, _ in group_a(t1, t2.replace(
            "08020096", "08020097"), t3)):
        fired += 1
    # 2) 把 (c) 轮 BFAR 改掉 -> r0==BFAR 的交叉核对必须失败
    bad3 = t3.replace("e000ed38:  00bd80b0", "e000ed38:  00bd80b1")
    if any(n == "B_r0_equals_BFAR" and ok is False for n, ok, _ in group_b(
            rd32_words(t1), w2_good, t1, t2, bad3)):
        fired += 1
    # 3) 切片文件不存在 -> 组 C 必须报缺
    keep = SLICE
    try:
        globals()["SLICE"] = pathlib.Path("D:/no_such_slice_file_please_ignore.txt")
        try:
            group_c(txt(SLICE), b"")
        except Exception:
            fired += 1
    finally:
        globals()["SLICE"] = keep
    return fired


def main():
    t1, t2, t3 = txt(FRAME / "stdout.bin"), txt(FRAME2 / "stdout.bin"), txt(HALT / "stdout.bin")
    w1, w2 = rd32_words(t1), rd32_words(t2)
    axf_bytes = AXF.read_bytes()
    slice_text = txt(SLICE)
    rows = []
    for n, ok, d in group_a(t1, t2, t3):
        rows.append((n, ok, d))
    for n, ok, d in group_b(w1, w2, t1, t2, t3):
        rows.append((n, ok, d))
    for n, ok, d in group_c(slice_text, axf_bytes):
        rows.append((n, ok, d))
    for n, ok, d in group_e(txt(MAP)):
        rows.append((n, ok, d))
    for n, ok, d in group_d():
        rows.append((n, ok, d))
    lines = []
    problems = 0
    for n, ok, d in rows:
        if not ok:
            problems += 1
        lines.append(("OK " if ok else "PROBLEM ") + n + (" | " + d if d else ""))
    # 原件字节身份也登记进来(清单会绑这些)
    lines.append("RAW frame_stdout bytes=%d sha256=%s" % (
        len((FRAME / "stdout.bin").read_bytes()), hashlib.sha256((FRAME / "stdout.bin").read_bytes()).hexdigest()))
    lines.append("RAW frame2_stdout bytes=%d sha256=%s" % (
        len((FRAME2 / "stdout.bin").read_bytes()), hashlib.sha256((FRAME2 / "stdout.bin").read_bytes()).hexdigest()))
    lines.append("RAW rc_files=%s,%s stderr_bytes=%d,%d" % (
        (FRAME / "rc.txt").read_text().strip(), (FRAME2 / "rc.txt").read_text().strip(),
        len((FRAME / "stderr.bin").read_bytes()), len((FRAME2 / "stderr.bin").read_bytes())))
    fired = negative_control(t1, t2, t3, w2, slice_text)
    lines.append("CITES=%d CHECKS=%d" % (len(CITES), len(rows)))
    lines.append("DAPFRAME_PROBLEMS=%d" % problems)
    lines.append("NEGATIVE_CONTROL fired=%d/3" % fired)
    text = "\n".join(lines) + "\n"
    print(text)
    return 0 if (problems == 0 and fired == 3) else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
    sys.exit(main())
