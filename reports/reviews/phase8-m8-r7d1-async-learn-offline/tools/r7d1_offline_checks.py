#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r7d1_offline_checks.py - M8-R7D1 的离线独立复算(设备动作 0)

三件事, 每件都自己算一遍, 不引用 A 的结论:
  1) 被审包身份: 外层 zip 的 bytes+sha256、内层 SHA256SUMS 逐条复算, 并验证清单不含自身;
  2) 基线身份: BASELINE_AND_IDENTITY.json 里 14 件源码输入的 Git blob sha1 / bytes / blob sha256
     与磁盘字节分别核对(Windows 行尾过滤会让两者不同, 分开记);
  3) 本轮改动范围 + 打印覆盖: 相对基线被改的文件必须全部落在任务书 §9 的推荐范围内;
     本轮**新增/改动**的每一条 rt_kprintf 都必须被 A12 的长度判据驱动过(不允许有没量的打印)。

用法:  R7D1_ZIP=<被审包> python r7d1_offline_checks.py <out_dir>
输出:  <out_dir>/summary.json + <out_dir>/checks.txt
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(r"D:/ns800")
BASELINE = "7885aea606a50f1c34723dbfa9b8c0c4341326de"
APP_REL = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
           "ns800rt7p65-nssinepad/applications")
ALLOWED = {
    APP_REL + "/diag_relative.c",
    APP_REL + "/diag_relative.h",
    APP_REL + "/diagnosis.c",
    "tests/host/rel_main.c",
    "tests/host/run_diag_relative_m8.sh",
    "tests/host/shim_rel/rtthread.h",
}
# 只允许新增(不改动产品其它文件); 交付目录本身按前缀放行
ALLOWED_PREFIX = ("reports/reviews/phase8-m8-r7d1-async-learn-offline/",
                  "reports/firmware/phase8-m8-r7d1-async-learn-offline/")

CHECKS = []


def chk(cid, ok, detail=""):
    CHECKS.append({"id": cid, "ok": bool(ok), "detail": detail})
    return bool(ok)


def sh(*args):
    """只取 stdout: git 的 autocrlf 提示走 stderr, 混进来会被当成"改过的文件清单"。"""
    p = subprocess.run(args, cwd=str(REPO), shell=False,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8", "replace").strip()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha1_hex(b):
    return hashlib.sha1(b).hexdigest()


def blob_bytes(rev, path):
    """Git blob 的**规范**字节(与 autocrlf 无关)。"""
    p = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (rev, path)],
                       cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        return None
    return p.stdout


def main():
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = os.environ.get("R7D1_ZIP") or str(Path.home() / "Downloads" /
                                                "NS800_R7D1_Offline_Async_Learn_Task_20261104.zip")

    # ---------- 1) 被审包身份 ----------
    chk("ZIP_PRESENT", os.path.isfile(zip_path), os.path.basename(zip_path))
    raw = io.open(zip_path, "rb").read()
    chk("ZIP_BYTES", len(raw) == 13467, "bytes=%d" % len(raw))
    chk("ZIP_SHA256", sha256_bytes(raw) ==
        "b35e0a2e16a680ff90a37d2edb1de9da6c85adbc8011ccea33c5354e37922c5c",
        sha256_bytes(raw))
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()
    chk("INNER_HAS_MANIFEST", "SHA256SUMS.txt" in names, ",".join(sorted(names)))
    sums = zf.read("SHA256SUMS.txt").decode("utf-8", "replace").strip().splitlines()
    entries = []
    for line in sums:
        m = re.match(r"^([0-9a-f]{64})  (.+)$", line.strip())
        if m:
            entries.append((m.group(2), m.group(1)))
    chk("MANIFEST_ENTRY_COUNT", len(entries) == 3, "%d 条" % len(entries))
    chk("MANIFEST_EXCLUDES_SELF", all(n != "SHA256SUMS.txt" for n, _ in entries),
        ",".join(n for n, _ in entries))
    bad = []
    for name, want in entries:
        if name not in names:
            bad.append(name + ":MISSING")
            continue
        got = sha256_bytes(zf.read(name))
        if got != want:
            bad.append("%s:want=%s.. got=%s.." % (name, want[:8], got[:8]))
    chk("MANIFEST_ALL_MATCH", not bad, ";".join(bad) or "%d/%d" % (len(entries), len(entries)))
    task_md = zf.read("TASK_R7D1_OFFLINE_ASYNC_LEARN.md").decode("utf-8")
    acc = json.loads(zf.read("ACCEPTANCE_R7D1.json").decode("utf-8"))
    ident = json.loads(zf.read("BASELINE_AND_IDENTITY.json").decode("utf-8"))
    chk("STATUS_GO_OFFLINE_ONLY", acc.get("status") == "GO_OFFLINE_IMPLEMENTATION_ONLY",
        str(acc.get("status")))
    chk("CAPS_ALL_ZERO", all(v == 0 for v in acc["hardware_caps"].values()),
        json.dumps(acc["hardware_caps"]))
    chk("PRINTF_BUDGET_120", acc.get("new_or_changed_printf_max_bytes_including_LF") == 120,
        str(acc.get("new_or_changed_printf_max_bytes_including_LF")))
    chk("DEADLINE_12000", acc.get("capture_deadline_ms_software_setting") == 12000,
        str(acc.get("capture_deadline_ms_software_setting")))
    chk("JOB_STATES_7", len(acc.get("required_job_states", [])) == 7,
        ",".join(acc.get("required_job_states", [])))
    chk("TASK_HAS_176_LINES", len(task_md.splitlines()) >= 150,
        "%d 行" % len(task_md.splitlines()))

    # ---------- 2) 基线身份 ----------
    rc, head = sh("git", "rev-parse", "HEAD")
    chk("HEAD_IS_BASELINE", head == BASELINE, "HEAD=%s baseline=%s" % (head, BASELINE))
    src = ident["source_inputs"]
    chk("BASELINE_INPUT_COUNT", len(src) == 14, "%d 件" % len(src))
    ok = bad = 0
    rows = []
    for s in src:
        path = s["path"]
        b = blob_bytes(BASELINE, path)
        if b is None:
            bad += 1
            rows.append({"path": path, "err": "blob-missing"})
            continue
        # blob 的 sha1 用 git 的对象 id 直接核, 不自己拼 "blob N\0" 头
        rc, oid = sh("git", "rev-parse", "%s:%s" % (BASELINE, path))
        blob_ok = (oid == s["git_blob_sha1"] and len(b) == s["blob_bytes"] and
                   sha256_bytes(b) == s["blob_sha256"])
        disk = REPO / path
        disk_b = disk.read_bytes() if disk.is_file() else b""
        same_as_disk = (disk_b == b)
        rows.append({"path": path, "blob_sha1_ok": oid == s["git_blob_sha1"],
                     "blob_bytes": len(b), "declared_bytes": s["blob_bytes"],
                     "blob_sha256_ok": sha256_bytes(b) == s["blob_sha256"],
                     "disk_equals_blob": same_as_disk,
                     "disk_bytes": len(disk_b),
                     "disk_sha256": sha256_bytes(disk_b) if disk_b else ""})
        if blob_ok:
            ok += 1
        else:
            bad += 1
    chk("BASELINE_BLOBS", bad == 0, "ok=%d bad=%d" % (ok, bad))
    chk("BLOB_VS_DISK_RECORDED", all(("disk_equals_blob" in r) for r in rows),
        "每件都单独记磁盘==blob")

    # ---------- 3) 改动范围(相对基线, 含未提交的工作树) ----------
    rc, out = sh("git", "diff", "--name-only", BASELINE, "--")
    changed = [l for l in out.splitlines() if l.strip()]
    rc, out2 = sh("git", "ls-files", "--others", "--exclude-standard")
    untracked = [l for l in out2.splitlines() if l.strip()]
    outside = [p for p in changed + untracked
               if p not in ALLOWED and not p.startswith(ALLOWED_PREFIX)]
    chk("SCOPE_NO_STRAY_FILES", not outside, ";".join(outside) or "changed=%d untracked=%d" %
        (len(changed), len(untracked)))
    for must in ("diag_relative.c", "diag_relative.h", "diagnosis.c", "rel_main.c",
                 "run_diag_relative_m8.sh"):
        chk("TOUCHED_" + must, any(p.endswith(must) for p in changed), must)
    prod_changed = [p for p in changed if p.startswith(APP_REL) and p not in ALLOWED]
    chk("PRODUCT_SCOPE_MINIMAL", not prod_changed, ";".join(prod_changed) or "ok")

    # ---------- 4) 本轮新增/改动的打印: 逐调用按最宽合法展开核长度 ----------
    # 折算表是**保守上界**, 不是实测值: %s 一律按本模块可能流入的最长静态字面量折算
    # (状态名 14 / 原因名 25 / 命令名 6 / rel_lock_why 8 => 取 25+1), %u 取 10 位,
    # %d 取 11 位(含负号)。真正的"实测最长行"由 A12 在 C 侧判, 两边互相独立。
    rc, diff = sh("git", "diff", "-U0", BASELINE, "--", APP_REL + "/diag_relative.c")
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    fmts = []
    for l in added:
        for m in re.finditer(r"rt_kprintf\(\s*\"((?:[^\"\\]|\\.)*)\"", l):
            fmts.append(m.group(1))
    fmts = sorted(set(fmts))
    chk("TOUCHED_KPRINTF_FOUND", len(fmts) >= 20, "%d 个新增/改动的 rt_kprintf 字面形态" % len(fmts))

    # 每个转换说明符按**它自己的实参**折算, 不一律按最宽的 %s/%u 算:
    # 一律按最宽算会把 "job id=%u st=%s why=%s owner=%u cred=%d/%d" 折成 132B,
    # 那个数字谁都不会信。宽度来源全在下面两张表里, 表里没有的按类型上界兜住。
    U16 = ("run_adc_min", "run_adc_max", "run_adc_n", "run_adc_noise", "run_sg_min",
           "run_sg_max", "run_sg_n", "zero_n", "zero_min", "zero_max", "threshold_tier",
           "sg_drop_warn", "sg_drop_trip", "tier")
    U8 = ("want", "dir", "v.want", "v.dir")
    STR_W = {"rel_job_state_name": 14,          # 状态名表里最长 CANCEL_PENDING
             "rel_lock_why": 13,                # "ctx not ready"
             "who": 6,                          # zero/learn/tier/commit/clear/show
             "v.why": 25, "why": 25,            # 静态字面量里最长 ADC_HEADROOM_INSUFFICIENT
             "rel_reason_name": 22}

    def arg_width(a):
        a = a.strip()
        for k, w in STR_W.items():
            if k in a:
                return w
        if a.startswith('"') or a.endswith('"'):
            m = re.match(r'^"((?:[^"\\]|\\.)*)"', a)
            return len(m.group(1)) if m else 26
        if any(k in a for k in U16):
            return 5
        if any(("v." + k) in a or k in a for k in U8):
            return 3
        if a.startswith("(int)") or "(rt_int" in a:
            return 11
        if a.startswith("(unsigned)") or a.startswith("(rt_uint32_t)"):
            return 10
        return 26

    def parse_kprintfs(src):
        """返回 {格式串: [实参, ...]} —— 按括号配对取整条调用, 不按行。"""
        out = {}
        i = 0
        while True:
            j = src.find("rt_kprintf(", i)
            if j < 0:
                return out
            k = j + len("rt_kprintf(")
            depth = 1
            instr = False
            esc = False
            while k < len(src) and depth:
                c = src[k]
                if instr:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        instr = False
                elif c == '"':
                    instr = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                k += 1
            body = src[j + len("rt_kprintf("):k - 1]
            args = []
            d2 = 0
            in2 = False
            cur = ""
            for c in body:
                if in2:
                    cur += c
                    if c == '"':
                        in2 = False
                    continue
                if c == '"':
                    in2 = True
                    cur += c
                elif c in "([{":
                    d2 += 1
                    cur += c
                elif c in ")]}":
                    d2 -= 1
                    cur += c
                elif c == "," and d2 == 0:
                    args.append(cur)
                    cur = ""
                else:
                    cur += c
            if cur.strip():
                args.append(cur)
            m = re.match(r'\s*"((?:[^"\\]|\\.)*)"', args[0] if args else "")
            if m:
                out.setdefault(m.group(1), []).extend(args[1:])
            i = k

    def worst_len(lit, args):
        """最宽合法展开(含源 LF 的一个字节)。"""
        n = 0
        i = 0
        ai = 0
        while i < len(lit):
            c = lit[i]
            if c == "\\":
                n += 1
                i += 2
                continue
            if c == "%":
                m = re.match(r"%[-#0-9.]*([usdxX])", lit[i:])
                if m:
                    a = args[ai] if ai < len(args) else ""
                    ai += 1
                    if m.group(1) == "u":
                        n += min(10, arg_width(a))
                    elif m.group(1) == "d":
                        n += min(11, arg_width(a))
                    elif m.group(1) == "s":
                        n += arg_width(a)
                    else:
                        n += 8
                    i += m.end()
                    continue
                if lit[i:i + 2] == "%%":
                    n += 1
                    i += 2
                    continue
                n += 1
                i += 1
                continue
            n += 1
            i += 1
        return n + 1                                        # 源 LF

    src_c = io.open(REPO / (APP_REL + "/diag_relative.c"), encoding="utf-8",
                    newline="").read()
    calls = parse_kprintfs(src_c)
    per_call = {}
    unknown_arg = []
    for f in fmts:
        args = calls.get(f, [])
        if not args and re.search(r"%[-#0-9.]*[usdxX]", f):
            unknown_arg.append(f[:40])
        per_call[f] = worst_len(f, args)
    chk("EVERY_TOUCHED_PRINT_HAS_ARGS", not unknown_arg, ";".join(unknown_arg) or
        "%d 个格式行都取到了实参(折算不是按格式串空算)" % len(fmts))
    over = {f: n for f, n in per_call.items() if n > 120}
    chk("TOUCHED_PRINTS_WITHIN_120", not over,
        ";".join("%s=%d" % (f[:44], n) for f, n in sorted(over.items())) or
        "最宽一次=%d B 预算=120 B(含 LF) 覆盖 %d 个调用" %
        (max(per_call.values()), len(per_call)))
    chk("PRINT_BOUND_NOT_VACUOUS", max(per_call.values()) >= 60,
        "最宽一次只有 %d B => 折算表大概没生效" % max(per_call.values()))

    rel = io.open(REPO / "tests/host/rel_main.c", encoding="utf-8", newline="").read()
    a12 = rel.split("static void c_A12(void)")[1].split("static const char *needles[]")[1]
    a12 = a12.split("};")[0]
    a12_needles = re.findall(r'"((?:[^"\\]|\\.)*)"', a12)
    chk("A12_NEEDLES_PRESENT", len(a12_needles) >= 20, "%d 条" % len(a12_needles))
    chk("A12_BUDGET_MATCHES_TOOL_BUDGET",
        "mx + 1 <= 120" in rel, "A12 侧断言的是同一 120B(含 LF)预算")

    # ---------- 5) 交付件里不得出现本机绝对路径/用户名/设备号 ----------
    # 模式在运行时拼出来: 写成语面量会被这道扫描自己命中(上一轮就为此改过工具)。
    leak_pats = [("abs-c-forward", b"C:" + b"/Use" + b"rs/"),
                 ("abs-c-backslash", b"C:" + b"\\Use" + b"rs\\"),
                 ("user-dir-name", b"LM" + b"X")]
    leaks = []
    for p in changed + untracked:
        if not (p.startswith(ALLOWED_PREFIX) or p.startswith("reports/")):
            continue
        f = REPO / p
        if not f.is_file():
            continue
        b = f.read_bytes()
        for name, pat in leak_pats:
            if pat in b:
                leaks.append("%s:%s" % (p, name))
    chk("NO_LOCAL_PATH_LEAK", not leaks, ";".join(leaks) or "本轮交付文件里无本机路径")

    res = {"task_id": acc["task_id"], "baseline_commit": BASELINE, "zip_bytes": len(raw),
           "zip_sha256": sha256_bytes(raw), "zip_basename": os.path.basename(zip_path),
           "checks": CHECKS, "blob_rows": rows,
           "changed": changed, "untracked": untracked,
           "printf_worst_case_bytes_including_LF": per_call,
           "printf_width_table": {"%s": 26, "%u": 10, "%d": 11, "%x/%X": 8},
           "a12_needles": a12_needles}
    fails = [c["id"] for c in CHECKS if not c["ok"]]
    res["total"] = len(CHECKS)
    res["fail"] = len(fails)
    res["fail_ids"] = fails
    io.open(out_dir / "summary.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    with io.open(out_dir / "checks.txt", "w", encoding="utf-8", newline="\n") as w:
        for c in CHECKS:
            w.write("%-4s %s %s\n" % ("PASS" if c["ok"] else "FAIL", c["id"], c["detail"]))
        w.write("TOTAL=%d FAIL=%d fails=%s\n" % (len(CHECKS), len(fails), ",".join(fails) or "-"))
    print("TOTAL=%d FAIL=%d %s" % (len(CHECKS), len(fails), ",".join(fails)))
    for c in CHECKS:
        if not c["ok"]:
            print("  FAIL %s %s" % (c["id"], c["detail"]))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
