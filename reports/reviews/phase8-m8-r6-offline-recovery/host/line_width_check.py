#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6 §7.3 的输出界限依据: 逐条 rt_kprintf 计算"最坏情况字节数", 与控制台 127 B 裁剪线对账。

口径(全部可被第三方复算, 不依赖本机运行结果):
  * rt_kprintf 的缓冲是 RT_CONSOLEBUF_SIZE(=128), kservice.c:411 把 length 静默裁到 127,
    所以**超过 127 B 的那次调用**尾部整段丢失, 且丢的是控制台侧而不是产品侧的字段。
  * 一条格式的最坏字节 = 字面 UTF-8 字节 + 每个转换符按其**参数类型的最宽展开**替换:
      %s -> 该处所有可能实参字符串里最长者的 UTF-8 字节   %u/%x -> 10 (0xFFFFFFFF)
      %08X -> 8                                          %d -> 11 (-2147483648)
      %%  -> 1                                           \n -> 1
  * 门槛取 120 B(留 7 B 余量, 因 CRLF 与设备侧换行处理会再吃字节)。

用法: python line_width_check.py            # 复算 + 断言(退出码 0=全部达标)
      python line_width_check.py --selfcheck # 元负控: 已知超长的原件必须被判红
"""
import io
import os
import re
import sys

APP = os.path.join("rt-thread-master", "rt-thread-master", "bsp", "novosns", "ns800",
                   "ns800rt7p65-nssinepad", "applications")
LIMIT = 120

# 本轮直接相关、已改成短行的站点(文件, 关键字, 每个 %s 的最宽实参)
FIXED = [
    ("tmc2209.c", "CONFIGURED (各次写入均经 IFCNT 确认)", []),
    ("tmc2209.c", "GCONF/CHOPCONF 读回一致", []),
    ("tmc2209.c", "电流值仍为理论计算", []),
    ("tmc2209.c", "预算: 已用 %u", []),
    ("tmc2209.c", "flush=%uB TX=%uB", []),
    ("tmc2209.c", "irun=%u ihold=%u", []),
    ("tmc2209.c", "GCONF %08X->%08X CHOPCONF", []),
    ("tmc2209.c", "op_epoch=%u\\n", []),
    ("tmc2209.c", "晚到的 apply 成功被拒绝提交", []),
    ("tmc2209.c", "剩余预算=%u tick", []),
    ("tmc2209.c", "旧成功不刷新健康", []),
    ("safety_thread.c", "[SAFETY] LIMIT inputs config", ["DISABLED_BY_CONFIG"]),
    ("safety_thread.c", "[SAFETY] LIMIT OFF", []),
    # R6A §6 补: 上一版这张表只有 tmc2209/safety_thread, 所以 R6 新增在 motor.c 的那条
    # 错误路径打印根本没被这条 PASS 约束过。现在把它自己拆出来的三条 + R6 的另一条新站点列进来。
    ("motor.c", "关闭落位**未提交**", []),
    ("motor.c", "关闭已落位, 但**未授予恢复资格**: 本代禁能侧证据不足", []),
    ("motor.c", "[MOT]  回读许可=%s 已确认电平=%u@%u", []),
    ("motor.c", "[MOT]  抑制=%s 停机锁存=%s", []),
    ("motor.c", "=> Safety 保持原状态", []),
]
# 修前的原件(从现场字节直接量得), 必须被同一把尺判红 —— 元负控的靶子
KNOWN_TOO_LONG = [
    ("修前 CONFIGURED 一行",
     "[TMC-CFG] CONFIGURED (各次写入均经 IFCNT 确认; GCONF/CHOPCONF 读回一致)。"
     "电流值仍为理论计算, 未实测。 [本次预算: 已用 0000000000 tick / 总 0000000000 tick, "
     "flush=0000000000B, TX=0000000000B; irun=0000000000 ihold=0000000000 dly=0000000000 "
     "tpd=0000000000 vsense=0000000000 GCONF 00000000->00000000 CHOPCONF 00000000->00000000 "
     "op_epoch=0000000000]", 132),
    ("修前 LIMIT 一行",
     "[SAFETY] LIMIT inputs config = DISABLED_BY_CONFIG (SAFETY_LIMIT_INPUTS_ENABLED;"
     " OFF => PF.14/PF.15 不解析/不配置/不读/不注册, 也不参与就绪判定)", 250),
    # R6A §6 的靶子: 这一条是 **R6 自己新增**的错误路径打印, 不在上面两条的历史豁免里。
    ("R6 新增的 withheld 一行(修前)",
     "[MOT] 关闭已落位, 但**未授予恢复资格**: 本代禁能侧回读/抑制/锁存证据不足"
     " (epoch=%u permit=%s level=%u@%u inhibit=%s latched=%s)"
     " - Safety 保持原状态, 需 fault_reset 人工复核\\n", 237),
]

# R6A §6: 只靠手维护的 FIXED 表会漏(上一轮的 PASS 恰恰没约束新增站点)。
# 这一档从 **git diff 的新增行**里取出本轮真正改动过的 rt_kprintf 站点逐条量, 表漏了也判红。
# 默认基准 = R6 任务书的基线提交(本轮所有产品改动的起点), 可用 --since 覆盖。
DEFAULT_SINCE = "535459f84724cdb755af00ee4dea4b3f42c93d45"
KPFMT = re.compile(r"rt_kprintf\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*)+)")
LITSTR = re.compile(r"\"((?:[^\"\\]|\\.)*)\"")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def added_ranges(base, rel_files):
    """`git diff -U0 base -- <文件>` 的新增行区间(新文件行号)。"""
    import subprocess
    out = subprocess.run(["git", "diff", "-U0", base, "--"] + rel_files,
                         cwd=os.getcwd(), capture_output=True)
    if out.returncode != 0:
        raise SystemExit("GIT_DIFF_FAILED:%s" % out.stderr.decode("utf-8", "replace")[:120])
    ranges, cur = [], None
    for raw in out.stdout.decode("utf-8", "replace").splitlines():
        if raw.startswith("+++ b/"):
            cur = raw[6:]
            continue
        m = HUNK.match(raw)
        if m and cur in rel_files:
            start, count = int(m.group(1)), int(m.group(2) or 1)
            ranges.append((cur, start, start + max(count, 1)))
    return ranges


def changed_sites(base):
    """本轮 diff 里**新增/改动过**的 rt_kprintf 站点: (文件, 行号, 最坏字节, 格式串)。"""
    rel = ["%s/%s" % (APP.replace(os.sep, "/"), fn) for fn in sorted(os.listdir(APP))
           if fn.endswith(".c")]
    hits = []
    for fn in sorted(os.listdir(APP)):
        if not fn.endswith(".c"):
            continue
        relpath = "%s/%s" % (APP.replace(os.sep, "/"), fn)
        rows = [r for r in added_ranges(base, rel) if r[0] == relpath]
        if not rows:
            continue
        text = io.open(os.path.join(APP, fn), encoding="utf-8").read()
        for m in KPFMT.finditer(text):
            line = text[:m.start()].count("\n") + 1
            if not any(a <= line < b for _, a, b in rows):
                continue
            fmt = "".join(LITSTR.findall(m.group(1)))
            n, _s = worst(fmt)
            hits.append((fn, line, n, fmt))
    return hits



def worst(fmt, str_tokens=()):
    """把格式串按最坏展开成实际字节数(返回 (字节数, 展开后的样例串))。"""
    out = []
    i = 0
    k = 0
    ti = list(str_tokens)
    while i < len(fmt):
        c = fmt[i]
        if c != "%":
            out.append(c)
            i += 1
            continue
        m = re.match(r"%(-?0?)(\d*)(?:[hl]*)([duxXsc%])", fmt[i:])
        if not m:
            out.append(c)
            i += 1
            continue
        flag, width, conv = m.group(1), m.group(2), m.group(3)
        i += m.end()
        if conv == "%":
            out.append("%")
        elif conv == "s":
            out.append(ti[k] if k < len(ti) else "0" * 18)
            k += 1
        elif conv in ("x", "X"):
            n = int(width) if width else 8
            out.append(("0" * n if flag == "0" else "F" * min(n, 8))[:max(n, 8)])
        else:  # d / u
            out.append("-2147483648" if conv == "d" else "4294967295")
    s = "".join(out).replace("\n", "")
    return len(s.encode("utf-8")) + s.count("\\n"), s


def scan_sources():
    """全仓 applications 下每条 rt_kprintf 的**字面下界**(转换符按 1 B 计) —— 找确定超长者。"""
    rows = []
    pat = re.compile(r"rt_kprintf\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*)+)")
    for fn in sorted(os.listdir(APP)):
        if not fn.endswith(".c"):
            continue
        text = io.open(os.path.join(APP, fn), encoding="utf-8", errors="replace").read()
        for m in pat.finditer(text):
            lits = re.findall(r"\"((?:[^\"\\]|\\.)*)\"", m.group(1))
            fmt = "".join(lits)
            lower = len(re.sub(r"\\.", ".", fmt).replace("\n", "")
                        .encode("utf-8"))
            # 每个转换符至少 1 B, 再加字面
            lower += len(re.findall(r"%(?:-?0?\d*)(?:[hl]*)(?:[duxXsc])", fmt))
            line = text[:m.start()].count("\n") + 1
            if lower > LIMIT:
                rows.append((fn, line, lower, fmt[:64]))
    return rows


def main():
    selfcheck = "--selfcheck" in sys.argv[1:]
    base = DEFAULT_SINCE
    if "--since" in sys.argv[1:]:
        base = sys.argv[1:][sys.argv[1:].index("--since") + 1]
    bad = []
    print("LINE_WIDTH mode=离线界限依据 limit=%dB (控制台裁剪线 127B) base=%s" % (LIMIT, base[:9]))
    for label, fmt, measured in KNOWN_TOO_LONG:
        n, _s = worst(fmt)
        hit = n > LIMIT
        print("%s|known-too-long|%s|worst=%dB|复审/现场量得=%dB|%s" %
              ("SELFCHECK" if selfcheck else "META", label, n, measured,
               "fired" if hit else "NOT-DETECTED"))
        if selfcheck and not hit:
            bad.append(label)
    for fn, key, toks in FIXED:
        text = io.open(os.path.join(APP, fn), encoding="utf-8").read()
        pat = re.compile(r"rt_kprintf\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*)+)")
        got = None
        for m in pat.finditer(text):
            lits = "".join(re.findall(r"\"((?:[^\"\\]|\\.)*)\"", m.group(1)))
            if key in lits:
                got = (text[:m.start()].count("\n") + 1, lits)
                break
        if got is None:
            bad.append("%s:%s 站点找不到" % (fn, key))
            print("MISSING|%s|%s" % (fn, key))
            continue
        n, sample = worst(got[1], toks)
        ok = n <= LIMIT
        print("%s|%s:%d|%d B|%s" % ("LINE" if not selfcheck else "SELFCHECK-LINE",
                                    fn, got[0], n, sample[:46]))
        if not ok:
            bad.append("%s:%d=%dB" % (fn, got[0], n))
    # ---- R6A §6: 本轮新增/改动过的站点逐个量(不依赖手维护的 FIXED 表) ----
    sites = changed_sites(base)
    print("CHANGED_SITES|base=%s|本轮 diff 命中的 rt_kprintf 站点=%d" % (base[:9], len(sites)))
    for fn, line, n, fmt in sites:
        ok = n <= LIMIT
        print("CHANGE|%s:%d|%d B|%s" % (fn, line, n, fmt[:44].replace("\n", " ")))
        if not ok:
            bad.append("%s:%d=%dB(本轮新增/改动站点)" % (fn, line, n))
    if not sites:
        # 空扫描不算通过: 上一轮的 PASS 正是因为这条约束不存在才漏了 motor.c 的新站点。
        print("CHANGE_SCAN_META|FAIL|命中 0 个站点 => 基准/路径不对, 这条判据没有在管任何事")
        bad.append("changed-sites 扫描空转(0 命中)")
    else:
        print("CHANGE_SCAN_META|fired|命中 %d 个站点并逐条判宽" % len(sites))
    if not selfcheck:
        over = scan_sources()
        print("REPO_SCAN|applications 下字面下界 >%dB 的 rt_kprintf 站点=%d" % (LIMIT, len(over)))
        for fn, line, lower, head in over:
            print("  OVER|%s:%d|%dB|%s" % (fn, line, lower, head.replace("\n", " ")))
        print("SCOPE_NOTE|本轮只修现场实际路径上被字节证实超长的两条(tmc2209 CONFIGURED 132B /"
              " safety_thread LIMIT 250B); 其余站点登记不改, 不声称全仓已达标")
    print("LINE_WIDTH_RESULT %s (bad=%d)" % ("PASS" if not bad else "PROBLEMS " + repr(bad), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
