#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""stack_ram_diag.py — 现有 R2 镜像的单次栈 RAM 诊断(有界、只读、结束保持 Halted)。

纪律:
- 地址全部从**已核验身份**的本地 AXF 现场解析(DWARF + symtab), 不硬编码; AXF 身份不符直接拒绝。
- RAM 读取仅限 0x20000000 <= addr 且 addr+len <= 0x20020000(该 A0 map 的 RW_VT_DTCM + RW_DTCM 合并上界),
  且只读下列绑定对象: s_tid(4) / sensor TCB(sizeof rt_thread) / _cpu(sizeof rt_cpu) /
  sensor 栈(必须恰好 4096) / 同会话复读 / CCR(4) / DTCMCR(4)。RAM+系统状态总预算 16384 B。
- 不烧录、不读目标 Flash、不复位、不开串口、不发 msh、不写业务内存、不注入代码、不维护缓存。
- --dryrun 用内存夹具, 完全不创建 pyOCD 会话/探针。
"""
import argparse
import hashlib
import io
import json
import os
import re
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
BSP = os.path.join(REPO, "rt-thread-master", "rt-thread-master", "bsp", "novosns", "ns800",
                   "ns800rt7p65-nssinepad")
AXF_DEFAULT = os.path.join(BSP, "build", "a0", "rt-thread-a0.axf")
EXPECTED_AXF = (1693804, "15be5f3efc45bf296bc0ef24e3947081372616d819e4bb3b189e9614cf9a5b9a")
RAM_LO, RAM_HI = 0x20000000, 0x20020000
STACK_SIZE_REQUIRED = 4096
OBJ_MAX = 1024           # TCB / Cpu 对象各自上限
TOTAL_BUDGET = 16384
CORE_CM7_H = os.path.join(BSP, "packages", "CMSIS-Core-latest", "Include", "core_cm7.h")
SYSREG_LEN = 4
REG_NAMES = ["pc", "sp", "lr", "msp", "psp", "control", "xpsr", "primask", "basepri", "faultmask",
             "fpscr", "mvac", "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r12"]


class Refuse(Exception):
    """按包纪律的拒绝: 先记录原值, 再停, 不追加动作。"""


class Gate(object):
    def __init__(self, log, sysreg):
        self.used = 0
        self.log = log
        self.reads = []
        self.sysreg = sysreg

    def check(self, addr, n, what):
        errs = []
        if what in self.sysreg:
            want, wn = self.sysreg[what][0], self.sysreg[what][1]
            if addr != want or n != wn:
                errs.append("SYSREG_NOT_BOUND %s want=0x%08x+%d got=0x%08x+%d" % (what, want, wn, addr, n))
        else:
            if not (RAM_LO <= addr and addr + n <= RAM_HI):
                errs.append("OUT_OF_RANGE 0x%08x+%d window=[0x%08x,0x%08x)" % (addr, n, RAM_LO, RAM_HI))
            if what in ("tcb", "tcb_reread", "cpu") and n > OBJ_MAX:
                errs.append("OBJECT_TOO_LARGE %s=%d>%d" % (what, n, OBJ_MAX))
            if what == "stack" and n != STACK_SIZE_REQUIRED:
                errs.append("STACK_LEN_MUST_BE_4096 len=%d" % n)
        if n > TOTAL_BUDGET - self.used:
            errs.append("BUDGET_EXCEEDED need=%d remaining=%d" % (n, TOTAL_BUDGET - self.used))
        if errs:
            self.log("GATE-REFUSE(before-read) %s: %s" % (what, "; ".join(errs)))
            raise Refuse("GATE_REFUSED(%s): %s" % (what, "; ".join(errs)))
        return True

    def read(self, mem, addr, n, what, sink=None):
        self.check(addr, n, what)
        data = bytes(mem.read(addr, n))
        assert len(data) == n, "短读 %s: %d != %d" % (what, len(data), n)
        self.used += n
        h = hashlib.sha256(data).hexdigest()
        self.reads.append({"what": what, "addr": "0x%08x" % addr, "len": n, "sha256": h, "cum_bytes": self.used})
        self.log("READ %-12s addr=0x%08x len=%-5d cum=%-6d sha=%s" % (what, addr, n, self.used, h[:16]))
        if sink is not None:
            sink[what] = data
        return data


def bind(axf, cmsis_h=CORE_CM7_H):
    b = open(axf, "rb").read()
    got = hashlib.sha256(b).hexdigest()
    if (len(b), got) != EXPECTED_AXF:
        raise Refuse("AXF_IDENTITY_MISMATCH bytes=%d/%d sha=%s/%s" % (
            len(b), EXPECTED_AXF[0], got[:16], EXPECTED_AXF[1][:16]))
    from elftools.elf.elffile import ELFFile
    f = ELFFile(io.BytesIO(b))
    di = f.get_dwarf_info()
    out = {"axf": {"bytes": len(b), "sha256": got, "path_kind": "本地 build/a0 现存件"}}
    stid, cpu, entry = [], [], None
    types = {}
    for cu in di.iter_CUs():
        top = cu.get_top_DIE()
        a = top.attributes.get("DW_AT_name")
        base = (a.value.decode("utf-8", "replace") if isinstance(a.value, bytes) else str(a.value)
                ).split("\\")[-1].split("/")[-1]
        for d in cu.iter_DIEs():
            if d.tag == "DW_TAG_variable" and "DW_AT_name" in d.attributes and "DW_AT_location" in d.attributes:
                v = d.attributes["DW_AT_name"].value
                nm = v.decode() if isinstance(v, bytes) else str(v)
                la = d.attributes["DW_AT_location"]
                if la.form == "DW_FORM_exprloc" and len(la.value) == 5 and la.value[0] == 0x03:
                    addr = int.from_bytes(bytes(la.value[1:]), "little")
                    if nm == "s_tid":
                        stid.append((base, addr))
                    elif nm == "_cpu":
                        cpu.append((base, addr))
            if d.tag == "DW_TAG_subprogram" and "DW_AT_name" in d.attributes and "DW_AT_low_pc" in d.attributes:
                v = d.attributes["DW_AT_name"].value
                nm = v.decode() if isinstance(v, bytes) else str(v)
                if nm == "sensor_thread_entry" and base == "sensor_service.c":
                    entry = d.attributes["DW_AT_low_pc"].value
            if d.tag in ("DW_TAG_structure_type", "DW_TAG_union_type") and "DW_AT_name" in d.attributes \
                    and d.attributes.get("DW_AT_byte_size"):
                v = d.attributes["DW_AT_name"].value
                nm = v.decode() if isinstance(v, bytes) else str(v)
                if nm in ("rt_thread", "rt_cpu", "rt_object"):
                    mem = {}
                    for c in d.iter_children():
                        if c.tag != "DW_TAG_member" or "DW_AT_name" not in c.attributes:
                            continue
                        cn = c.attributes["DW_AT_name"].value
                        cn = cn.decode() if isinstance(cn, bytes) else str(cn)
                        mem[cn] = member_off(c.attributes.get("DW_AT_data_member_location"))
                        if cn == "name":
                            tt = c.attributes.get("DW_AT_type")
                            if tt is not None:
                                try:
                                    mem[cn + "_array_len"] = array_len(c.get_DIE_from_attribute("DW_AT_type"))
                                except Exception:
                                    pass
                    t = types.setdefault(nm, {"sizes": set(), "members": {}, "defs": 0, "cus": set()})
                    t["sizes"].add(d.attributes["DW_AT_byte_size"].value)
                    t["defs"] += 1
                    t["cus"].add(base)
                    for k, x in mem.items():
                        t["members"].setdefault(k, set()).add(x)
    if len(stid) != 1:
        raise Refuse("S_TID_BINDING_AMBIGUOUS %r(同名静态变量必须唯一绑定编译单元)" % (stid,))
    if len(cpu) != 1:
        raise Refuse("_CPU_BINDING_AMBIGUOUS %r" % (cpu,))
    if entry is None:
        raise Refuse("NO_SENSOR_THREAD_ENTRY_ADDRESS")
    # symtab 里同一函数的取值带 Thumb 位; DWARF low_pc 不带。二者互相印证 TCB 里应存的 entry 值。
    stab = {}
    for sec in f.iter_sections():
        if sec.header.sh_type == "SHT_SYMTAB":
            for s in sec.iter_symbols():
                if s["st_info"]["type"] == "STT_FUNC" and s.name in ("sensor_thread_entry",
                                                                     "rt_scheduler_stack_check"):
                    stab[s.name] = {"value": "0x%08x" % s["st_value"], "size": s["st_size"],
                                    "bind": s["st_info"]["bind"]}
    if "sensor_thread_entry" not in stab:
        raise Refuse("NO_SYMTAB_SENSOR_THREAD_ENTRY")
    if int(stab["sensor_thread_entry"]["value"], 16) != entry | 1:
        raise Refuse("ENTRY_DWARF_VS_SYMTAB_DISAGREE dwarf=0x%08x symtab=%s" % (entry,
                                                                               stab["sensor_thread_entry"]["value"]))
    for nm in ("rt_thread", "rt_cpu", "rt_object"):
        t = types.get(nm)
        offs = {k: v for k, v in t["members"].items() if not k.endswith("_array_len")}
        if not t or len(t["sizes"]) != 1 or any(len(set(v)) != 1 for v in offs.values()):
            raise Refuse("TYPE_LAYOUT_INCONSISTENT %s sizes=%r" % (nm, t and t["sizes"]))

    def off(nm, field):
        return next(iter(types[nm]["members"][field]))

    # 系统寄存器: SCS/SCB 基址取仓库内 CMSIS 头; CCR/DTCMCR 偏移取该 AXF 里"含 CCR+DTCMCR 的匿名结构"
    # 的 DWARF 布局, 并与该头自带 Offset 注释交叉核对。不硬编码任何寄存器地址。
    sysreg, sysev = {}, {}
    try:
        hdr = io.open(cmsis_h, "r", encoding="utf-8", errors="replace").read()
    except OSError as e:
        raise Refuse("CMSIS_HEADER_UNREADABLE %s" % e.__class__.__name__)
    m1 = re.search(r"#define\s+SCS_BASE\s+\((0x[0-9A-Fa-f]+)UL\)", hdr)
    m2 = re.search(r"#define\s+SCB_BASE\s+\(SCS_BASE\s*\+\s*(0x[0-9A-Fa-f]+)UL\)", hdr)
    if not m1 or not m2:
        raise Refuse("CMSIS_SCS_SCB_BASE_NOT_FOUND scs=%r scb=%r" % (bool(m1), bool(m2)))
    scs_base, scb_base = int(m1.group(1), 16), int(m1.group(1), 16) + int(m2.group(1), 16)
    scb_defs = []
    for cu in di.iter_CUs():
        for d in cu.iter_DIEs():
            if d.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                continue
            ms = {}
            for c in d.iter_children():
                if c.tag != "DW_TAG_member" or "DW_AT_name" not in c.attributes:
                    continue
                cn = c.attributes["DW_AT_name"].value
                cn = cn.decode() if isinstance(cn, bytes) else str(cn)
                ms[cn] = member_off(c.attributes.get("DW_AT_data_member_location"))
            if "CCR" in ms and "DTCMCR" in ms and "SCR" in ms:
                sz = d.attributes.get("DW_AT_byte_size")
                scb_defs.append({"size": sz.value if sz else None, "ccr": ms["CCR"],
                                 "dtcmcr": ms["DTCMCR"]})
    if not scb_defs:
        raise Refuse("SYSREG_SCB_STRUCT_NOT_IN_DWARF")
    if len({x["size"] for x in scb_defs}) != 1 or len({x["ccr"] for x in scb_defs}) != 1 \
            or len({x["dtcmcr"] for x in scb_defs}) != 1 or None in (scb_defs[0]["ccr"],
                                                                    scb_defs[0]["dtcmcr"]):
        raise Refuse("SYSREG_SCB_LAYOUT_INCONSISTENT %r" % (scb_defs,))
    dwarf = scb_defs[0]
    hdr_off = {}
    for nm_m in ("CCR", "DTCMCR"):
        mm = re.search(r"__IOM uint32_t\s+%s;\s*/\*!< Offset:\s*(0x[0-9A-Fa-f]+)" % nm_m, hdr)
        if mm:
            hdr_off[nm_m] = int(mm.group(1), 16)
    if hdr_off.get("CCR") is None:
        raise Refuse("CMSIS_CCR_OFFSET_NOT_ANNOTATED")
    if hdr_off["CCR"] != dwarf["ccr"]:
        raise Refuse("SYSREG_CCR_DWARF_VS_CMSIS dwarf=%d header=%d" % (dwarf["ccr"], hdr_off["CCR"]))
    sysreg["ccr"] = [scb_base + dwarf["ccr"], SYSREG_LEN]
    sysev["ccr"] = {"cmsis_header": "core_cm7.h SCS_BASE=0x%08x SCB_BASE=SCS_BASE+0x%x"
                                    % (scs_base, scb_base - scs_base),
                    "dwarf_anon_scb_defs": len(scb_defs), "dwarf_scb_size": dwarf["size"],
                    "offset_dwarf": dwarf["ccr"], "offset_cmsis_annotation": hdr_off["CCR"],
                    "addr": "0x%08x" % sysreg["ccr"][0]}
    # DTCMCR 仅在该 AXF 布局唯一且 CMSIS 头同时给出(成员定义+Offset 注释)时读; 否则 NOT_READ, 不猜地址。
    if hdr_off.get("DTCMCR") is not None and hdr_off["DTCMCR"] == dwarf["dtcmcr"]:
        sysreg["dtcmcr"] = [scb_base + dwarf["dtcmcr"], SYSREG_LEN]
        sysev["dtcmcr"] = {"dwarf_anon_scb_defs": len(scb_defs), "offset_dwarf": dwarf["dtcmcr"],
                           "offset_cmsis_annotation": hdr_off["DTCMCR"],
                           "target_visible_basis": "DTCM 地址空间属调试 AP 可见的 data TCM 窗口",
                           "addr": "0x%08x" % sysreg["dtcmcr"][0]}
    else:
        sysev["dtcmcr"] = {"status": "NOT_BOUND",
                           "header_annotation": hdr_off.get("DTCMCR"),
                           "dwarf_offset": dwarf["dtcmcr"]}

    out.update({
        "s_tid_addr": "0x%08x" % stid[0][1], "s_tid_addr_int": stid[0][1], "s_tid_cu": stid[0][0],
        "cpu_addr": "0x%08x" % cpu[0][1], "cpu_addr_int": cpu[0][1], "cpu_cu": cpu[0][0],
        "sensor_thread_entry": "0x%08x" % entry, "sensor_thread_entry_int": entry,
        "sensor_thread_entry_expected_in_tcb": "0x%08x" % (entry | 1),
        "entry_symtab": stab.get("sensor_thread_entry"),
        "stack_check_symtab": stab.get("rt_scheduler_stack_check"),
        "rt_thread_size": next(iter(types["rt_thread"]["sizes"])),
        "rt_thread_offsets": {k: off("rt_thread", k) for k in
                              ("parent", "sp", "entry", "parameter", "stack_addr", "stack_size", "error")},
        "rt_object_name_off": off("rt_object", "name"),
        "rt_object_name_len": next(iter(types["rt_object"]["members"]["name_array_len"])),
        "rt_object_type_off": off("rt_object", "type"),
        "rt_cpu_size": next(iter(types["rt_cpu"]["sizes"])),
        "rt_cpu_current_thread_off": off("rt_cpu", "current_thread"),
        "layout_evidence": {k: {"definitions": v["defs"], "byte_size": next(iter(v["sizes"])),
                                "member_offset_variants": {kk: len(set(vv)) for kk, vv in v["members"].items()}}
                            for k, v in types.items()},
        "ram_window": ["0x%08x" % RAM_LO, "0x%08x" % RAM_HI],
        "sysreg": sysreg,
        "sysreg_evidence": sysev,
        "budget": TOTAL_BUDGET,
    })
    return out


def d32(data, o):
    return struct.unpack_from("<I", data, o)[0]


def member_off(loc):
    """DW_AT_data_member_location -> int 偏移(实测该 AXF 用 DW_FORM_data1; 兼容 exprloc/DW_OP_plus_uconst)。"""
    if loc is None:
        return None
    v = loc.value
    if isinstance(v, int):
        return v
    if isinstance(v, (bytes, bytearray)):
        if len(v) == 0:
            return 0
        if v[0] == 0x23:                     # DW_OP_plus_uconst 后跟 ULEB128
            n, shift, i = 0, 0, 1
            while i < len(v):
                b = v[i]
                n |= (b & 0x7F) << shift
                i += 1
                if not b & 0x80:
                    break
                shift += 7
            return n
        if len(v) == 1:
            return v[0]
    raise Refuse("UNDECODABLE_MEMBER_LOCATION form=%s value=%r" % (getattr(loc, "form", "?"), v))


def array_len(die):
    """DW_TAG_array_type 的元素个数(取 subrange 的 DW_AT_count / DW_AT_upper_bound)。"""
    for c in die.iter_children():
        if c.tag != "DW_TAG_subrange_type":
            continue
        if "DW_AT_count" in c.attributes:
            return c.attributes["DW_AT_count"].value
        if "DW_AT_upper_bound" in c.attributes:
            return c.attributes["DW_AT_upper_bound"].value + 1
    return None


def run(mem, bd, log, artifacts, R):
    g = Gate(log, bd["sysreg"])
    R["gate_reads"] = g.reads            # 同一个 list 对象: 拒绝时也能看到已发生的读
    R["ram_bytes_read_so_far"] = 0

    def step(k, v):
        R.setdefault("steps", []).append({k: v})
        log("STEP %s: %s" % (k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v))

    p = g.read(mem, bd["s_tid_addr_int"], 4, "s_tid", artifacts)
    tcb_addr = d32(p, 0)
    step("s_tid_value", "0x%08x" % tcb_addr)
    R["s_tid_raw"] = p.hex()
    if tcb_addr == 0:
        raise Refuse("S_TID_NULL —— 线程尚未创建, 记录原值后停止, 不扫描内存")
    if tcb_addr & 3:
        raise Refuse("TCB_MISALIGNED 0x%08x" % tcb_addr)
    g.check(tcb_addr, bd["rt_thread_size"], "tcb")          # 越界指针必须在读 TCB 之前被拒
    o = bd["rt_thread_offsets"]
    tcb = g.read(mem, tcb_addr, bd["rt_thread_size"], "tcb", artifacts)
    name = tcb[bd["rt_object_name_off"]:bd["rt_object_name_off"] + 16].split(b"\x00")[0].decode("ascii", "replace")
    sp, entry = d32(tcb, o["sp"]), d32(tcb, o["entry"])
    base, size = d32(tcb, o["stack_addr"]), d32(tcb, o["stack_size"])
    ptype = tcb[bd["rt_object_type_off"]]
    R["tcb_raw"] = {"name": name, "type_byte": ptype, "sp": "0x%08x" % sp, "entry": "0x%08x" % entry,
                    "stack_addr": "0x%08x" % base, "stack_size": size,
                    "error_field": d32(tcb, o["error"]), "sha256": hashlib.sha256(tcb).hexdigest()}
    step("tcb_fields", R["tcb_raw"])
    bad = []
    if name != "sensor":
        bad.append("NAME_NOT_SENSOR(%r)" % name)
    if (entry & ~1) != (bd["sensor_thread_entry_int"] & ~1):
        bad.append("ENTRY_MISMATCH got=0x%08x want_base=0x%08x" % (entry, bd["sensor_thread_entry_int"]))
    if entry != bd["sensor_thread_entry_int"] | 1:
        bad.append("ENTRY_NOT_THUMB_MARKED got=0x%08x want=0x%08x(Cortex-M 的 TCB entry 必带 Thumb 位)" % (
            entry, bd["sensor_thread_entry_int"] | 1))
    if size != STACK_SIZE_REQUIRED:
        bad.append("STACK_SIZE_NOT_4096(%d)" % size)
    if bad:
        raise Refuse("TCB_IDENTITY_CONTRADICTION: " + "; ".join(bad) + " —— 原值已记录, 停, 不读栈, 不扫描")
    g.check(base, size, "stack")
    if not (base <= sp <= base + size):
        raise Refuse("SP_OUTSIDE_STACK sp=0x%08x base=0x%08x size=%d" % (sp, base, size))

    cpu = g.read(mem, bd["cpu_addr_int"], bd["rt_cpu_size"], "cpu", artifacts)
    cur = d32(cpu, bd["rt_cpu_current_thread_off"])
    step("cpu_current_thread", {"value": "0x%08x" % cur, "equals_tcb": cur == tcb_addr})

    stack = g.read(mem, base, size, "stack", artifacts)
    lead = 0
    while lead < len(stack) and stack[lead] == 0x23:
        lead += 1
    step("stack_fill", {"stack0": "0x%02x" % stack[0], "stack0_is_hash": stack[0] == 0x23,
                        "leading_hash_run_from_low": lead,
                        "first_non_hash_offset": lead if lead < len(stack) else None,
                        "high_water_estimate_bytes": len(stack) - lead,
                        "saved_free_sp_minus_base": sp - base,
                        "saved_used_high_minus_sp": (base + size) - sp})
    fill_used = len(stack) - lead
    R["computation"] = {"stack_low": "0x%08x" % base, "stack_high": "0x%08x" % (base + size),
                        "saved_sp": "0x%08x" % sp, "saved_free": sp - base, "saved_used": (base + size) - sp,
                        "leading_hash_run": lead, "hash_tail_after_run": stack[lead:lead + 1].hex(),
                        "sp_at_base_plus": sp - base,
                        "in_warning_band": sp <= base + 32, "in_overflow_band": (sp <= base or sp > base + size),
                        "fill_vs_sp_consistent": fill_used >= (base + size) - sp,
                        "sp_window_hex": stack[max(0, sp - base - 32):min(len(stack), sp - base + 32)].hex(),
                        "first64": stack[:64].hex(), "last64": stack[-64:].hex()}
    R["artifacts_expected"] = {"stack_sha256": hashlib.sha256(stack).hexdigest(), "stack_len": len(stack)}

    p2 = g.read(mem, bd["s_tid_addr_int"], 4, "s_tid_reread", artifacts)
    tcb2 = g.read(mem, tcb_addr, bd["rt_thread_size"], "tcb_reread", artifacts)
    same = (p2 == p) and (tcb2 == tcb)
    step("snapshot_recheck", {"s_tid_equal": p2 == p, "tcb_equal": tcb2 == tcb, "consistent": same})
    if not same:
        R["snapshot_inconsistent"] = True
        log("快照复读不一致 —— 只登记, 不追加第三次")
    for nm in sorted(bd["sysreg"]):
        try:
            v = g.read(mem, bd["sysreg"][nm][0], bd["sysreg"][nm][1], nm, artifacts)
            step(nm, {"addr": "0x%08x" % bd["sysreg"][nm][0], "value": "0x%08x" % d32(v, 0),
                      "decode": {"dcache_en": bool(d32(v, 0) & (1 << 16)) if nm == "ccr" else None,
                                 "icache_en": bool(d32(v, 0) & (1 << 9)) if nm == "ccr" else None}})
        except Refuse as e:
            R.setdefault("sysreg_refused", {})[nm] = str(e)
            log("%s NOT_READ: %s" % (nm, e))
        except Exception as e:
            R.setdefault("sysreg_unavailable", {})[nm] = type(e).__name__
            log("%s NOT_READ(读取不可用): %s" % (nm, type(e).__name__))
    R["total_ram_bytes_read"] = g.used
    R["gate_reads"] = g.reads
    if g.used > TOTAL_BUDGET:
        raise Refuse("BUDGET 超限 %d" % g.used)
    return R


class FakeMem(object):
    def __init__(self, regions):
        self.regions = regions
        self.touched = []

    def read(self, addr, n):
        self.touched.append((addr, n))
        for a, d in self.regions:
            if a <= addr and addr + n <= a + len(d):
                return d[addr - a: addr - a + n]
        raise AssertionError("夹具没有 0x%08x+%d 的内容(门本该在读前拦住)" % (addr, n))


def build_fixture(mode, bd):
    tcb_addr, base = 0x20008000, 0x20010000
    o = bd["rt_thread_offsets"]
    size = bd["rt_thread_size"]

    def mk_tcb(name=b"sensor", entry=None, sp=base + 16, base_=base, stack_size=4096):
        t = bytearray(size)
        t[bd["rt_object_name_off"]:bd["rt_object_name_off"] + 16] = name.ljust(16, b"\x00")
        struct.pack_into("<I", t, o["sp"], sp)
        struct.pack_into("<I", t, o["entry"], entry if entry is not None
                         else (bd["sensor_thread_entry_int"] | 1))
        struct.pack_into("<I", t, o["stack_addr"], base_)
        struct.pack_into("<I", t, o["stack_size"], stack_size)
        t[bd["rt_object_type_off"]] = 3
        return bytes(t)

    def mk_stack(hash_len):
        return bytes(b"#" * hash_len + b"\xAA" * (4096 - hash_len))

    regs = {"pc": "0x0801ad81", "sp": "0x%08x" % (base + 16), "lr": "0x0801accd", "msp": "0x20000500",
            "psp": "0x%08x" % (base + 16), "control": "0x0c", "xpsr": "0x61000000"}
    stid = struct.pack("<I", tcb_addr)
    common = [(bd["s_tid_addr_int"], stid),
              (bd["cpu_addr_int"], stid + b"\x00" * (bd["rt_cpu_size"] - 4)),
              (bd["sysreg"]["ccr"][0], b"\x00\x05\x00\x01"),
              (bd["sysreg"]["dtcmcr"][0], b"\x01\x00\x00\x00")]
    if mode == "ok":            # 低端 sp + 填充痕迹一致 => 真实深栈(告警带内)
        m = FakeMem(common + [(tcb_addr, mk_tcb(sp=base + 16)), (base, mk_stack(16))])
    elif mode == "okshallow":   # 高位 sp + 填充一致 => 未捕获告警态
        m = FakeMem(common + [(tcb_addr, mk_tcb(sp=base + 1024)), (base, mk_stack(1024))])
    elif mode == "contradict":  # sp 贴近低端但填充显示未深用 => 快照自相矛盾
        m = FakeMem(common + [(tcb_addr, mk_tcb(sp=base + 16)), (base, mk_stack(1024))])
    elif mode == "oor":
        m = FakeMem([(bd["s_tid_addr_int"], struct.pack("<I", RAM_HI + 0x1000))])   # 指针越界
    elif mode == "oor2":
        m = FakeMem(common + [(tcb_addr, mk_tcb(base_=0x2001F800)),
                              (0x2001F800, mk_stack(1024))])  # base+4096 越界
    elif mode == "badname":
        m = FakeMem(common + [(tcb_addr, mk_tcb(name=b"motor")), (base, mk_stack(1024))])
    elif mode == "badentry":
        m = FakeMem(common + [(tcb_addr, mk_tcb(entry=0x0800ABCD)), (base, mk_stack(1024))])
    elif mode == "biggerstack":
        m = FakeMem(common + [(tcb_addr, mk_tcb(stack_size=8192)), (base, mk_stack(1024))])
    else:
        raise Refuse("unknown mode " + mode)
    return m, regs


class LiveMem(object):
    def __init__(self, core):
        self.core = core
        self.attempts = 0

    def read(self, addr, n):
        self.attempts += 1
        return bytes(self.core.read_memory_block8(addr, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--axf", default=AXF_DEFAULT)
    ap.add_argument("--cmsis", default=CORE_CM7_H, help="core_cm7.h 路径(SCS/SCB 基址来源)")
    ap.add_argument("--out", required=True, help="产物目录(必须全新)")
    ap.add_argument("--dryrun", choices=["ok", "okshallow", "contradict", "oor", "oor2", "badname",
                                         "badentry", "biggerstack"])
    ap.add_argument("--live", action="store_true", help="真实单会话(需本包一次批准)")
    ap.add_argument("--uid-file", help="私有 DAP UID 文件(仅 --live)")
    a = ap.parse_args()
    if bool(a.dryrun) == bool(a.live):
        raise SystemExit("必须且只能指定 --dryrun 或 --live 之一")
    if os.path.exists(a.out):
        raise SystemExit("输出目录已存在, 拒绝覆盖: " + a.out)
    os.makedirs(a.out)
    L = []

    def log(s):
        L.append(s)
        print(s)

    artifacts, R = {}, {"mode": ("live" if a.live else "dryrun:" + a.dryrun)}
    try:
        bd = bind(a.axf, a.cmsis)
        R["binding"] = bd
        log("BINDING " + json.dumps({k: v for k, v in bd.items() if k != "layout_evidence"}, ensure_ascii=False))
        if a.dryrun:
            mem, regs = build_fixture(a.dryrun, bd)
            R["registers"] = regs
            R["registers_note"] = "dry-run 夹具寄存器, 非设备实测"
            try:
                run(mem, bd, log, artifacts, R)
                R["outcome"] = "PASS_ALL_GATES"
            except Refuse as e:
                R["outcome"] = "REFUSED"
                R["refusal"] = str(e)
                log("REFUSED(before doing more): %s" % e)
            R["fixture_touched"] = [("0x%08x" % x, y) for x, y in mem.touched]
            R["fixture_touched_bytes"] = sum(y for _, y in mem.touched)
            R["serial_objects_created"] = False
            R["pyocd_session_created"] = False
        else:
            from pyocd.core.helpers import ConnectHelper
            if not a.uid_file:
                raise Refuse("LIVE_NEEDS_UID_FILE —— 不枚举探针")
            uid = io.open(a.uid_file, encoding="utf-8").read().strip()
            if not uid:
                raise Refuse("UID_FILE_EMPTY")
            R["uid_source"] = "私有 UID 文件(未写入产物)"
            opts = {"no_config": True, "connect_mode": "attach", "auto_unlock": False,
                    "resume_on_disconnect": False, "scan_all_aps": False,
                    "cache.enable_memory": False, "cache.read_code_from_elf": False}
            sess = ConnectHelper.session_with_chosen_probe(unique_id=uid, options=opts)
            if sess is None:
                raise Refuse("NO_SESSION_FOR_BOUND_UID —— 不枚举其它探针")
            core = sess.target.primary_core
            try:
                R["initial_state"] = {"halted": bool(core.is_halted()), "running": bool(core.is_running())}
                log("INITIAL %s" % R["initial_state"])
                if not R["initial_state"]["halted"]:
                    core.halt()
                    R["halt"] = {"performed": True, "after_halted": bool(core.is_halted())}
                    log("HALT once -> %s" % R["halt"])
                else:
                    R["halt"] = {"performed": False, "note": "连接时已 Halted, 不重复执行 halt"}
                regs = {}
                for n in REG_NAMES:
                    try:
                        regs[n] = "0x%08x" % core.read_core_register(n)
                    except Exception as e:
                        regs[n] = "unavailable(%s)" % type(e).__name__
                R["registers"] = regs
                mem = LiveMem(core)
                run(mem, bd, log, artifacts, R)
                R["mem_read_attempts"] = mem.attempts
                R["final_state"] = {"halted": bool(core.is_halted()), "running": bool(core.is_running())}
                R["outcome"] = "PASS_ALL_GATES" if R["final_state"]["halted"] else "NOT_HALTED_AT_END"
            except Refuse as e:
                R["outcome"] = "REFUSED"
                R["refusal"] = str(e)
                log("REFUSED: %s" % e)
            finally:
                sess.disconnect()
                sess.close()
    except Refuse as e:
        R["outcome"] = "REFUSED_EARLY"
        R["refusal"] = str(e)
        log("REFUSED_EARLY: %s" % e)
    except Exception as e:
        R["outcome"] = "ERROR"
        R["error"] = "%s: %s" % (type(e).__name__, e)
        log("ERROR %s: %s" % (type(e).__name__, e))
    for k, v in artifacts.items():
        io.open(os.path.join(a.out, k + ".bin"), "wb").write(v)
    io.open(os.path.join(a.out, "result.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(R, ensure_ascii=False, indent=1, default=str) + "\n")
    io.open(os.path.join(a.out, "session.log"), "w", encoding="utf-8", newline="\n").write(
        "\n".join(x.rstrip() for x in L) + "\n")
    print("SUMMARY outcome=%s ram_bytes=%s reads=%s artifacts=%s" % (
        R.get("outcome"), R.get("total_ram_bytes_read", R.get("fixture_touched_bytes")),
        len(R.get("gate_reads", [])), sorted(artifacts.keys())))
    return 0 if R.get("outcome") in ("PASS_ALL_GATES",) else (0 if R.get("expected_refusal") else 3)


if __name__ == "__main__":
    sys.exit(main())
