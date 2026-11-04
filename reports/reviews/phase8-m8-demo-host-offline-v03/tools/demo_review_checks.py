# -*- coding: utf-8 -*-
"""V0.3 演示端离线复核取证: 全部产物写到 --out, 真实仓库只读、一字不改。

用法(路径全部走参数, 本机不留默认绝对路径):
  python tools/demo_review_checks.py \
      --zip PATH/NS800_Demo_Offline_V0_3.zip \
      --zip-prev PATH/NS800_Demo_Offline_V0_2.zip \
      --zip-prev2 PATH/NS800_Demo_Offline_V0_1.zip \
      --repo PATH/to/ns800 --box WORKDIR --out DIR/host
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BANNED = ("serial", "socket", "subprocess", "os", "ctypes", "threading", "multiprocessing")
PY = sys.executable
SYSTEMROOT = str(Path(sys.executable).anchor)


def run(args, cwd, env_extra=None):
    env = {"PYTHONIOENCODING": "utf-8", "SYSTEMROOT": SYSTEMROOT}
    env.update(env_extra or {})
    return subprocess.run([PY] + [str(a) for a in args], cwd=str(cwd), capture_output=True, env=env)


def wtxt(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def wbytes(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "verdict": "PASS" if ok else "FAIL", "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + detail if detail else ""))
    return bool(ok)


def pkg_root(d: Path) -> Path:
    """zip 顶层可能带一层同名文件夹: 一律以真正含 demo.py 的那一层为包根。"""
    hits = [p.parent for p in d.rglob("demo.py")]
    if len(hits) != 1:
        raise SystemExit("包里 demo.py 不唯一或不存在: %s -> %s" % (d, hits))
    return hits[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--zip-prev", required=True, type=Path)
    ap.add_argument("--zip-prev2", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--box", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    repo, box, out = a.repo.resolve(), a.box.resolve(), a.out.resolve()
    if box == repo or repo in box.parents:
        raise SystemExit("--box 必须在仓库外, 否则会把临时件写进仓库")
    box.mkdir(parents=True, exist_ok=True)
    ident = []

    # 1) 三份 zip 的字节身份 + V0.3 内层清单
    for role, z in (("V0_3", a.zip), ("V0_2", a.zip_prev), ("V0_1", a.zip_prev2)):
        b = z.read_bytes()
        ident.append({"role": role, "name": z.name, "bytes": len(b), "sha256": sha(b)})
        print("%-5s %8d B  %s" % (role, len(b), sha(b)))
    check("三份 zip 身份已按磁盘字节登记", len(ident) == 3)

    pkg = box / "V0_3"
    prev = box / "V0_2"
    for target, z in ((pkg, a.zip), (prev, a.zip_prev)):
        if target.exists():
            shutil.rmtree(target)
        shutil.unpack_archive(str(z), str(target))
    pkg, prev = pkg_root(pkg), pkg_root(prev)
    entries = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file())
    check("V0.3 解包条目数 == 内层清单条目数+1(清单自身)", len(entries) == 11, "entries=%d" % len(entries))

    manifest = (pkg / "SHA256SUMS.txt").read_bytes()
    mrows = []
    for line in manifest.decode("utf-8").splitlines():
        m = re.match(r"^([0-9a-f]{64})  (.+)$", line)
        if not m:
            continue
        want, rel = m.groups()
        got = sha((pkg / rel).read_bytes()) if (pkg / rel).is_file() else None
        mrows.append({"path": rel, "listed": want, "disk": got, "ok": got == want})
    check("V0.3 内层 SHA256SUMS 逐条重算全对", all(r["ok"] for r in mrows),
          "%d/%d" % (sum(r["ok"] for r in mrows), len(mrows)))
    check("清单未把自身写进清单", "SHA256SUMS.txt" not in [r["path"] for r in mrows])
    wtxt(out / "manifest_recompute.txt",
         "# V0.3 包内 SHA256SUMS.txt 逐条重算(清单不哈希自身)\n" +
         "\n".join("%s  %s  %s" % ("OK" if r["ok"] else "MISMATCH", r["listed"], r["path"]) for r in mrows) + "\n")

    # 2) 包内自检 / 仓库逐字节比对(原样存程序 stdout 字节)
    r1 = run(["demo.py", "--check"], pkg)
    wbytes(out / "package_check.json", r1.stdout)
    p1 = json.loads(r1.stdout.decode("utf-8"))
    check("包内自检 --check 退出码 0 且 7 帧", r1.returncode == 0 and p1["frames"] == 7,
          "rc=%d frames=%d" % (r1.returncode, p1["frames"]))
    check("无 --repo-root 时明确 SKIPPED_NO_REPOSITORY(不降为跳过式假通过)",
          p1["archive_sources"]["status"] == "SKIPPED_NO_REPOSITORY")

    r2 = run(["demo.py", "--check", "--repo-root", repo], pkg)
    wbytes(out / "repo_check.json", r2.stdout)
    p2 = json.loads(r2.stdout.decode("utf-8"))
    check("仓库比对 PASS 且 matched_frames=7",
          r2.returncode == 0 and p2["archive_sources"]["status"] == "PASS"
          and p2["archive_sources"]["matched_frames"] == 7,
          "rc=%d status=%s matched=%s" % (r2.returncode, p2["archive_sources"]["status"],
                                          p2["archive_sources"]["matched_frames"]))

    # 3) 我独立复算七份仓库原件(不信程序自报, 直接读盘重算)
    data = json.loads((pkg / "data" / "replay.json").read_text(encoding="utf-8"))
    rows = []
    for f in data["frames"]:
        p = repo / f["source_path"]
        b = p.read_bytes()
        rows.append({"source_path": f["source_path"], "title": f["title"], "disk_bytes": len(b),
                     "disk_sha256": sha(b), "embedded_bytes": f["source_bytes"],
                     "embedded_sha256": f["sha256"],
                     "embedded_text_equal": b == f["text"].encode("utf-8"),
                     "equal": len(b) == f["source_bytes"] and sha(b) == f["sha256"]})
    wtxt(out / "archive_recompute.json", json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    check("七份仓库原件磁盘字节 == 包内内嵌原文(逐字节)", all(r["equal"] and r["embedded_text_equal"] for r in rows),
          "%d/%d" % (sum(r["equal"] and r["embedded_text_equal"] for r in rows), len(rows)))
    order = [r["title"] for r in rows]
    check("帧顺序与 R7F4 现场执行顺序一致",
          order == ["启动自检", "配置前状态", "写前电机状态", "配置命令回包",
                    "配置后状态", "写后电机状态", "安全反馈"], str(order))

    # 4) 补丁回环: 被补丁改动的三件 + 未进补丁的 run_demo.bat
    patch = pkg / "upgrade_v02_to_v03.patch"
    patched_files = sorted({re.sub(r"^b/", "", m) for m in
                            re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(encoding="utf-8"), re.M)})
    rt = {"patch_bytes": len(patch.read_bytes()), "patch_sha256": sha(patch.read_bytes()),
          "patched_files": patched_files, "runs": []}
    for flag, label in ((["-c", "core.autocrlf=false"], "autocrlf_off"), ("", "system_default")):
        work = box / ("rt_" + label)
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(prev, work)
        cmd = ["git"] + (flag or []) + ["apply", "-p1", str(patch)]
        pr = subprocess.run(cmd, cwd=str(work), capture_output=True)
        cmp_rows = []
        for rel in ("demo.py", "README.md", "data/replay.json", "run_demo.bat", "SHA256SUMS.txt"):
            got = (work / rel).read_bytes()
            want = (pkg / rel).read_bytes()
            lf = got.replace(b"\r\n", b"\n")
            cmp_rows.append({
                "path": rel, "in_patch": rel in patched_files,
                "bytes_applied": len(got), "bytes_v03": len(want),
                "byte_equal": got == want, "cr_in_applied": got.count(b"\r"), "cr_in_v03": want.count(b"\r"),
                "equal_after_lf_normalize": lf == want})
        rt["runs"].append({"label": label, "git_cmd": " ".join(cmd), "apply_rc": pr.returncode,
                           "apply_stderr": pr.stderr.decode("utf-8", "replace")[:400],
                           "files": cmp_rows})
        print("[%s] apply rc=%d 四件相等=%s" % (label, pr.returncode,
              [r["byte_equal"] for r in cmp_rows]))
    wtxt(out / "patch_roundtrip.json", json.dumps(rt, ensure_ascii=False, indent=2) + "\n")
    off = next(r for r in rt["runs"] if r["label"] == "autocrlf_off")
    four = [r for r in off["files"] if r["path"] in ("demo.py", "README.md", "data/replay.json", "run_demo.bat")]
    check("关闭 autocrlf 后: 被补丁三件逐字节 == 完整版",
          all(r["byte_equal"] for r in four if r["in_patch"]) and off["apply_rc"] == 0,
          str([(r["path"], r["byte_equal"]) for r in four if r["in_patch"]]))
    check("未进补丁的 run_demo.bat 同样逐字节 == 完整版",
          [r for r in four if r["path"] == "run_demo.bat"][0]["byte_equal"])
    check("SHA256SUMS.txt 在补丁路线下仍是 V0.2 清单(预期不等)",
          not [r for r in off["files"] if r["path"] == "SHA256SUMS.txt"][0]["byte_equal"])
    dflt = next(r for r in rt["runs"] if r["label"] == "system_default")
    sysrows = [r for r in dflt["files"] if r["in_patch"]]
    check("系统默认 autocrlf=true 时确有行尾改写(仅行尾不同, 内容一致)",
          all(not r["byte_equal"] and r["equal_after_lf_normalize"] and r["cr_in_applied"] > 0 for r in sysrows),
          str([(r["path"], r["cr_in_applied"], r["cr_in_v03"]) for r in sysrows]))

    # 4b) 补丁路线产物能否自检 + 旧清单过期程度
    work_off = box / "rt_autocrlf_off"
    r3 = run(["demo.py", "--check"], work_off)
    wbytes(out / "patched_pkg_check.json", r3.stdout)
    p3 = json.loads(r3.stdout.decode("utf-8"))
    check("补丁路线产出的包同样通过 --check(7 帧)",
          r3.returncode == 0 and p3["frames"] == 7, "rc=%d frames=%d" % (r3.returncode, p3["frames"]))
    stale = []
    for line in (work_off / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([0-9a-f]{64})  (.+)$", line)
        if m and (work_off / m.group(2)).is_file():
            stale.append({"path": m.group(2), "listed": m.group(1),
                          "disk": sha((work_off / m.group(2)).read_bytes())})
    bad = [r for r in stale if r["listed"] != r["disk"]]
    wtxt(out / "patched_manifest_staleness.txt",
         "# 关闭 autocrlf 应用补丁后, 用包内 V0.2 清单复核补丁后的树: 不符项即清单过期项\n" +
         "\n".join("%s  %s" % ("MISMATCH" if r["listed"] != r["disk"] else "OK", r["path"]) for r in stale) + "\n")
    check("补丁后 V0.2 清单只对三件过期(补丁未更新清单, 属已知口径)",
          sorted(r["path"] for r in bad) == ["README.md", "data/replay.json", "demo.py"], str(sorted(r["path"] for r in bad)))

    # 5) 反例复跑: 由 tools/demo_negative_controls.py 单独执行并原样留档, 本脚本只核对其落盘结果
    neg_txt = out / "negative_controls.txt"
    neg_lines = neg_txt.read_text(encoding="utf-8").splitlines() if neg_txt.exists() else []
    summary_line = next((l for l in neg_lines if l.startswith("反例复跑:")), None)
    m = re.search(r"场景=(\d+) 与声称一致=(\d+) 不符=(\d+)", summary_line or "")
    check("反例落盘存在且逐例判定齐全", bool(m), str(summary_line))
    check("反例复跑与声称一致(不符=0)", bool(m) and m.group(3) == "0", str(summary_line))
    check("反例落盘无异常回溯(Traceback 不出现)", "Traceback" not in "".join(neg_lines))

    # 6) 导入面与设备动作面: 判据只看 AST, 全文禁词只作旁证(文档串里"没有串口"这句话本身会命中)
    tree = ast.parse((pkg / "demo.py").read_text(encoding="utf-8"))
    mods, names = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {al.name.split(".")[0] for al in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    hits = sorted(m for m in mods if m in BANNED)
    code_hits = sorted(n for n in names if n in BANNED)
    body = (pkg / "demo.py").read_text(encoding="utf-8")
    text_hits = sorted({m for m in BANNED if re.search(r"\b%s\b" % m, body)})
    docstring = ast.get_docstring(tree) or ""
    note = "全文禁词命中为空"
    if text_hits:
        line = next((l for l in docstring.splitlines()
                     if any(re.search(r"\b%s\b" % m, l) for m in text_hits)), None)
        assert line, "禁词命中不在模块文档串里, 判据需重新分级: %s" % text_hits
        note = "全文禁词命中 %s 全部落在模块文档串那句声明里: %r" % (text_hits, line)
    wtxt(out / "imports_audit.txt",
         "# demo.py 设备动作面判据(只看 AST; 全文禁词只作旁证)\n\n"
         "imports: %s\nbanned_in_imports(AST): %s\nbanned_names_or_attrs(AST): %s\n"
         "banned_in_raw_text(旁证): %s\n%s\nhardware_actions: 无串口/DAP/子进程入口, 设备动作 0\n"
         % (sorted(mods), hits, code_hits, text_hits, note))
    check("导入面仅标准库+tkinter, 无 serial/socket/subprocess/os/ctypes", not hits, str(sorted(mods)))
    check("AST 里不存在对禁用模块的名字或属性引用(设备动作面为空)", not code_hits, str(code_hits))

    # 7) A 自带截图身份
    gui_rows = []
    for p in sorted((pkg / "verification" / "gui").glob("*")):
        b = p.read_bytes()
        gui_rows.append({"path": "verification/gui/" + p.name, "bytes": len(b), "sha256": sha(b)})
    wtxt(out / "author_gui_identity.txt", json.dumps(gui_rows, ensure_ascii=False, indent=2) + "\n")
    check("A 包内两张写前/写后截图存在且身份已登记", len(gui_rows) == 2, str([r["path"] for r in gui_rows]))

    summary = {"identity": ident, "checks": CHECKS,
               "fails": sum(1 for c in CHECKS if c["verdict"] == "FAIL"),
               "total": len(CHECKS),
               "scope": "离线复核: 只读三份 zip、解包副本与 D:/ns800 归档原件; 未连接设备, 设备动作 0; "
                        "仓库比对只证磁盘字节, 不证 Git HEAD, 也不证当前板端状态。"}
    wtxt(out / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print("SUMMARY total=%d fails=%d" % (len(CHECKS), summary["fails"]))
    return 1 if summary["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
