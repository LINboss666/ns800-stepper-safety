#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8 diag 栈修复轮(R1)的机器可复算检查 —— 零设备、零构建。

判据分七组(A-G)+ 三个元负控。全部 OK 且负控 3/3 fired, 本轮才有资格说"零问题"。
外部依赖: 仓库外的两份全文反汇编(dis_r3.txt / dis_new.txt)与 armlink 调用图 htm,
都按 LOCAL_ONLY 登记, 其关键行已逐字抄进 stack_report.txt / disasm_diff.txt。
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path("D:/ns800")
REV = ROOT / "reports/reviews/phase8-m8-diag-stack-r1"
FIRM = ROOT / "reports/firmware/phase8-m8-diag-stack-r1"
R3F = ROOT / "reports/firmware/phase8-m8-relative-protection-r3"
PROJ = ROOT / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
DIAGC = PROJ / "applications/diagnosis.c"
SCR = pathlib.Path(os.environ.get("DS_SCR", "D:/ns800_m8r3_scratch"))
CG = SCR / "build/motor_m1/rt-thread-motor-m1.htm"
if not CG.exists():
    CG = PROJ / "build/motor_m1/rt-thread-motor-m1.htm"
LOG = REV / "build/rt-thread-motor-m1-candidate.log"
MANI = FIRM / "build_manifest.json"
DIFF = REV / "disasm_diff.txt"
OLD_REF = os.environ.get("DS_BASE", "e489438a60aac096b9d2d1ab9af68b6a00b8795f")

STACKS = {"diag": 4096, "sensor": 4096, "motor": 2048, "bblog": 1024, "ui": 512, "tshell": 4096}
HEAP_TOTAL = 0x8000
res = []


def add(name, ok, detail=""):
    res.append((name, bool(ok), detail))


def txt(p):
    return pathlib.Path(p).read_bytes().decode("utf-8", "replace")


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


# ---------------- A: 构建日志
def group_a():
    t = txt(LOG)
    add("A_log_0error_0warning", '0 Error(s), 0 Warning(s)' in t, "日志尾部逐字")
    add("A_log_program_size", "Code=204762 RO-data=85578 RW-data=2188 ZI-data=84824" in t, "四项尺寸")
    add("A_log_elapsed", "Build Time Elapsed:  00:00:12" in t, "构建耗时行")
    add("A_log_diagnosis_compiled_once", t.count("compiling diagnosis.c") == 1,
        "compiling diagnosis.c 出现 %d 次(必须恰好 1)" % t.count("compiling diagnosis.c"))
    add("A_log_target_is_motor", "rt-thread-motor-m1.axf" in t, "链接产物是 motor 目标")
    add("A_log_armclang_v624", "ARMCLANG" in t.upper(), "编译器为 ARMCLANG")
    add("A_no_download_markers", not re.search(r"(?i)(eras|program flash|flashpy|download|CMSIS_AGDI.*load)", t),
        "日志里没有任何下载/擦除字样(本轮 build only)")


# ---------------- B: 归档件身份
def group_b():
    m = json.loads(txt(MANI))
    tgt = m["targets"][0]
    want = {i["name"]: (i["bytes"], i["sha256"]) for i in tgt["images"] + tgt["maps"]}
    for name, (b, s) in want.items():
        p = FIRM / name
        add("B_identity_%s" % name.replace(".", "_"), p.exists() and p.stat().st_size == b and sha(p) == s,
            "%s bytes=%d sha=%s" % (name, p.stat().st_size if p.exists() else -1,
                                    sha(p)[:16] if p.exists() else "缺失"))
    add("B_manifest_program_size_equals_r3", tgt["program_size"] ==
        [x for x in json.loads(txt(R3F / "build_manifest.json"))["targets"]
         if x["target"] == "rt-thread-motor-m1-candidate"][0]["program_size"],
        "与 R3 同目标 Program Size 逐字相同(运行时堆参数不占静态段)")
    add("B_log_sha_in_manifest", tgt["log_sha256"] == sha(LOG) and tgt["log_bytes"] == LOG.stat().st_size,
        "构建日志 bytes=%d sha=%s" % (LOG.stat().st_size, tgt["log_sha256"][:16]))
    add("B_diagnosis_c_sha_bound", m["diagnosis_c_bytes_at_build"]["sha256"] == sha(DIAGC) and
        m["diagnosis_c_bytes_at_build"]["size"] == DIAGC.stat().st_size,
        "登记的 diagnosis.c 构建时字节 == 现在工作树字节(sha %s)" % sha(DIAGC)[:16])


# ---------------- C: 源码改动形状
def group_c():
    lines = txt(DIAGC).splitlines()
    add("C_define_at_48", lines[47].strip().startswith("#define DIAG_THREAD_STACK   4096"),
        "diagnosis.c:48 -> %s" % lines[47].strip()[:78])
    site = [i for i, l in enumerate(lines) if "DIAG_THREAD_STACK, 9, 10" in l]
    add("C_create_site_uses_macro", len(site) == 1, "rt_thread_create 实参行 = :%d" % (site[0] + 1 if site else -1))
    add("C_no_bare_1024_in_create", not any(re.search(r"rt_thread_create.*\b1024\b", l) for l in lines),
        "创建点不再有裸 1024")
    old = subprocess.run(["git", "-C", str(ROOT), "cat-file", "blob",
                          "%s:%s" % (OLD_REF, DIAGC.relative_to(ROOT).as_posix())],
                         capture_output=True)
    if old.returncode:
        add("C_base_blob_readable", False, "git cat-file %s 失败: %s" % (OLD_REF[:8], old.stderr[:80]))
    else:
        ob = old.stdout.decode("utf-8", "replace")
        add("C_base_blob_had_1024", "1024, 9, 10" in ob, "%s 的旧 blob 确实是 1024 栈" % OLD_REF[:8])
        add("C_base_blob_had_no_macro", "DIAG_THREAD_STACK" not in ob, "旧 blob 里没有这个宏 => 本轮才是引入点")
    add("C_sibling_convention_matched",
        "MOTOR_THREAD_STACK   2048" in txt(PROJ / "applications/motor.c") and
        "SENSOR_THREAD_STACK   4096" in txt(PROJ / "applications/sensor_service.c"),
        "命名沿用同目录既有约定(motor/sensor 都有具名栈宏)")


# ---------------- D: 反汇编差异分类(每一行差异都要归到允许的四类里)
ALLOW = [
    (r"^(\d+,)?\d+(,\d+)?[acd]\d*$", "diff 块头"),
    (r"^---$", "旧文件标记"),
    (r"^\+\+\+$", "新文件标记"),
    (r"^<\s+File Name:", "fromelf 打的输入路径"),
    (r"^>\s+File Name:", "fromelf 打的输入路径"),
    (r"^<\s+0x0800ca26:    f44f6380    O\.\.c    MOV      r3,#0x400$", "旧栈大小立即数 1024"),
    (r"^>\s+0x0800ca26:    f44f5380    O\.\.S    MOV      r3,#0x1000$", "新栈大小立即数 4096"),
    (r"^[<>]\s+0x080216(dc|e8):    f6452(08c|098|0[89][0-9a-f])", "__DATE__/__TIME__ 串地址互换"),
    (r"^[<>]\s+\.L\.str\.(11|12)$", "构建时间串标签"),
    (r"^[<>]\s+0x08045a(8|9)[0-9a-f]:", "构建时间串字节"),
    (r"^[<>] 0x08045a(8c|95|98)\s", "串地址索引行"),
]


def classify(line):
    for pat, why in ALLOW:
        if re.match(pat, line):
            return why
    return None


def group_d():
    d = txt(DIFF).splitlines()
    body = [l for l in d if l[:1] in "<>" and not l.startswith("---") and not l.startswith("+++")]
    unclassified = [l for l in body if classify(l) is None]
    add("D_diff_lines_present", len(d) > 10, "disasm_diff.txt %d 行" % len(d))
    add("D_all_diff_lines_classified", not unclassified,
        "无法归类的差异行 %d 条: %s" % (len(unclassified), unclassified[:3]))
    add("D_stack_immediate_pair", any("r3,#0x400" in l for l in body) and any("r3,#0x1000" in l for l in body),
        "成对出现: 0x400(旧) 与 0x1000(新)")
    add("D_no_other_code_changed", not [l for l in body
                                         if re.match(r"^[<>]\s+0x0(8[0-3]|0)[0-9a-f]{6}:.*\b(BL|LDR|STR|MOVS?|ADDS?|SUBS?)\b", l)
                                         and "0x0800ca26" not in l and "0x080216" not in l],
        "除 0x0800ca26 与 __DATE__/__TIME__ 取址行外, 没有任何指令行变化")


# ---------------- E: armlink 调用图(仓库外原件 + 登记件互证)
def group_e():
    if not CG.exists():
        add("E_callgraph_present", False, "调用图 htm 不可读: %s" % CG)
        return
    t = re.sub(r"<[^>]+>", "", txt(CG)).replace("&rArr;", "=>").replace("&nbsp;", " ")
    lines = [l.strip() for l in t.splitlines()]
    own, md = {}, {}
    for fn in ("diag_thread_entry", "rel_prod_evaluate", "rel_engine_step"):
        for i, l in enumerate(lines):
            if l.startswith(fn + " (Thumb"):
                own[fn] = int(re.search(r"Stack size (\d+) bytes", l).group(1))
                j = next((j for j in range(i + 1, min(i + 10, len(lines)))
                          if "[Stack]Max Depth" in lines[j]), None)
                md[fn] = int(re.search(r"Max Depth = (\d+)", lines[j]).group(1)) if j else None
                break
    add("E_own_frames", own == {"diag_thread_entry": 632, "rel_prod_evaluate": 248, "rel_engine_step": 184},
        "armlink 本帧: %s" % own)
    add("E_max_depths", md == {"diag_thread_entry": 1580, "rel_prod_evaluate": 948, "rel_engine_step": 352},
        "armlink 累计: %s" % md)
    add("E_old_stack_insufficient_both_ways", sum(own.values()) > 1024 and md["diag_thread_entry"] > 1024,
        "三帧下界 %d > 1024 且 armlink 累计 %d > 1024" % (sum(own.values()), md["diag_thread_entry"]))
    add("E_new_stack_above_known_depth", 4096 > md["diag_thread_entry"],
        "4096 - %d = %d B 余量(仅对静态已知链而言)" % (md["diag_thread_entry"], 4096 - md["diag_thread_entry"]))
    img = [l for l in lines if l.startswith("Maximum Stack Usage")]
    add("E_image_level_keeps_unknown_flag", bool(img) and "Unknown" in img[0],
        "镜像级: %s —— 有 Unknown, 所以静态图不给闭合上界" % (img[0][:96] if img else "无"))


# ---------------- F: 与 R3 已接受 AXF 的独立互证(同一测量两条来源)
def group_f():
    dis = SCR / "dis_r3.txt"
    if not dis.exists():
        add("F_r3_disasm_present", False, "R3 全文反汇编不可读(LOCAL_ONLY): %s" % dis)
        return
    lines = txt(dis).splitlines()
    got = {}
    for fn in ("diag_thread_entry", "rel_prod_evaluate", "rel_engine_step"):
        for i, l in enumerate(lines):
            if l.strip() == fn:
                for j in range(i + 1, min(i + 6, len(lines))):
                    m = re.search(r"SUB\s+sp,sp,#0x([0-9a-f]+)", lines[j])
                    if m:
                        got[fn] = 8 + int(m.group(1), 16)   # PUSH {r7,lr} = 8 B
                        break
                break
    add("F_r3_frames_match", got == {"diag_thread_entry": 632, "rel_prod_evaluate": 248, "rel_engine_step": 184},
        "R3 已接受 AXF 反汇编算出的本帧: %s" % got)
    add("F_r3_axf_untouched", sha(R3F / "rt-thread-motor-m1.axf").startswith("0bba8ca7"),
        "R3 的 AXF 未被本轮覆盖(sha 前缀 %s)" % sha(R3F / "rt-thread-motor-m1.axf")[:8])
    add("F_r3_bin_still_onboard_identity", sha(R3F / "rt-thread-motor-m1-candidate.bin").startswith("00608ec9"),
        "R3 候选 BIN 仍是板上那枚(sha 前缀 %s)" % sha(R3F / "rt-thread-motor-m1-candidate.bin")[:8])


# ---------------- G: 堆核算与"零设备动作"
def group_g():
    m = json.loads(txt(MANI))
    add("G_heap_total_from_scatter", HEAP_TOTAL == 0x8000 and
        "RW_RT_HEAD 0x20012000 EMPTY 0x8000" in txt(PROJ / "board/linker_scripts/link.sct"),
        "堆 = 32768 B(link.sct 预留块 + board.h HEAP_BEGIN/HEAP_END ±0x8000)")
    add("G_delta_is_3072", STACKS["diag"] - 1024 == 3072, "diag 1024 -> 4096 = +3072 B 堆需求")
    finsh = int(re.search(r"#define FINSH_THREAD_STACK_SIZE (\d+)",
                          txt(PROJ / "rtconfig.h")).group(1))
    add("G_stacks_sum", STACKS["tshell"] == finsh and sum(STACKS.values()) == 15872,
        "六枚线程栈合计 %d B(旧 %d B, delta %+d), tshell 取 rtconfig 实值 %d; "
        "未含 TCB 与其它 rt_malloc => 只是枚举, 不是够不够的结论" % (
            sum(STACKS.values()), sum(STACKS.values()) - 3072, 3072, finsh))
    add("G_failclosed_path_present", "-RT_ERROR" in txt(DIAGC) and "SUBSYS_FAILED" in txt(DIAGC),
        "diagnosis_init 在建线程失败时 fail-closed(堆真不够会显式暴露而非静默)")
    add("G_no_device_action_roots", not (REV / "offline").exists() and not (REV / "field").exists(),
        "本轮没有 offline/field 执行根 => 零设备动作")
    add("G_not_proven_listed", len(m["not_proven"]) == 4, "登记件里明写未证的 4 项: %s" % m["not_proven"])


def negative_control():
    """元负控: 判据必须会被真实破坏触发, 否则没有资格宣布零问题。"""
    fired = 0
    # 1) 往差异里塞一条没登记过的指令行 -> D_all_diff_lines_classified 必须失败
    d = txt(DIFF).splitlines()
    bad = d + [">         0x08012345:    f000f000    ....    BL       some_unexpected_call ; 0x8099999"]
    body = [l for l in bad if l[:1] in "<>" and not l.startswith("---") and not l.startswith("+++")]
    if any(classify(l) is None for l in body):
        fired += 1
    # 2) 篡改登记散列 -> B 组身份必须失败
    m = json.loads(txt(MANI))
    tgt = m["targets"][0]
    want = {i["name"]: (i["bytes"], i["sha256"]) for i in tgt["images"] + tgt["maps"]}
    if any(not (p.exists() and p.stat().st_size == b and sha(p) == s)
           for p, (b, s) in [((FIRM / n), (b, s + "0")) for n, (b, s) in want.items()]):
        fired += 1
    # 3) 调用图不可读 -> E 组必须报缺失而不是静默通过
    keep = CG
    try:
        globals()["CG"] = pathlib.Path("D:/no_such_callgraph.htm")
        before = len(res)
        group_e()
        if any(n == "E_callgraph_present" and not ok for n, ok, _ in res[before:]):
            fired += 1
    finally:
        del res[len(res) - 1:]
        globals()["CG"] = keep
    return fired


def main():
    group_a(); group_b(); group_c(); group_d(); group_e(); group_f(); group_g()
    lines, problems = [], 0
    for n, ok, d in res:
        problems += 0 if ok else 1
        lines.append(("OK " if ok else "PROBLEM ") + n + (" | " + d if d else ""))
    fired = negative_control()
    lines.append("CHECKS=%d" % len(res))
    lines.append("DIAGSTACK_PROBLEMS=%d" % problems)
    lines.append("NEGATIVE_CONTROL fired=%d/3" % fired)
    print("\n".join(lines) + "\n")
    return 0 if problems == 0 and fired == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
