# -*- coding: utf-8 -*-
"""R7F6 执行前核对(任务书 §3 第 1、2、4 项 + §5 第 1 步): 包落位、身份绑定、冻结源来历、固定判据。

每条 SHA 比较都先用一个故意改错的登记值跑一遍并必须被判不符(恒真的比较器不算判据)。
只读: 不打开设备/串口/分析仪, 只读仓库字节与 git blob。

用法: python tools/r7f6_preflight.py --repo D:/ns800 --zip <包 zip> --out <证据文件(仓外)>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import zipfile

LINE = []
Fails = []


def note(s):
    LINE.append(s)
    print(s)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def compare(pairs, read, corrupt_first=False):
    """pairs=[(want_sha, rel)]; read(rel)->bytes; corrupt_first=True 时把首条登记值改错(自检)。"""
    bad = []
    for i, (want, rel) in enumerate(pairs):
        exp = ("0" * 64 if (corrupt_first and i == 0) else want)
        got = sha(read(rel))
        if got != exp:
            bad.append(rel)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--zip", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    rv = repo / "reports/reviews/phase8-m8-r7f6-la-path-static"
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    zbytes = a.zip.read_bytes()
    note("ZIP|%s|bytes=%d sha256=%s" % (a.zip.name, len(zbytes), sha(zbytes)))
    zf = zipfile.ZipFile(a.zip)
    names = [n for n in zf.namelist() if not n.endswith("/")]
    placed = [p for p in rv.rglob("*") if p.is_file()]
    bad = [n for n in names if not (rv / n).exists() or (rv / n).read_bytes() != zf.read(n)]
    note("PLACE|包内文件=%d 目录内文件(含本轮自产件)=%d 包件逐字节不符=%d %s"
         % (len(names), len(placed), len(bad), bad))
    if bad:
        Fails.append("PLACE_BYTE")

    ent = []
    for ln in (rv / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            h, _s, rel = ln.partition("  ")
            ent.append((h.strip(), rel.strip()))
    read_rv = lambda rel: (rv / rel).read_bytes()
    sc = compare(ent, read_rv, corrupt_first=True)
    note("SELFCHECK_MANIFEST|首条登记值故意改错必须被判不符: fired=%s %s" % (bool(sc), sc[:1]))
    if not sc:
        note("PREFLIGHT_ABORT|清单比较器恒真 => 结果不作数")
        return 2
    wrong = compare(ent, read_rv)
    note("MANIFEST|条目=%d 不符=%d %s" % (len(ent), len(wrong), wrong))
    if wrong:
        Fails.append("MANIFEST")

    ident = json.loads((rv / "IDENTITY.json").read_text(encoding="utf-8"))
    ini = ident["inputs"]
    want_map = [("plan", rv / ini["plan"]["path"], ini["plan"]),
                ("rows", rv / ini["rows"]["path"], ini["rows"]),
                ("executor_pkg", rv / ini["executor"]["package_path"], ini["executor"]),
                ("executor_repo", repo / ini["executor"]["repo_path"], ini["executor"]),
                ("image_b502", repo / ini["image"]["repo_path"], ini["image"])]
    for key, path, want in want_map:
        b = path.read_bytes()
        ok = len(b) == want["bytes"] and sha(b) == want["sha256"]
        note("ID_INPUT|%s|%s bytes=%d/%d sha=%s/%s %s"
             % (key, path.relative_to(repo).as_posix(), len(b), want["bytes"],
                sha(b)[:12], want["sha256"][:12], "PASS" if ok else "FAIL"))
        if not ok:
            Fails.append("ID_INPUT|" + key)
        if key == "plan":
            sc = compare([(want["sha256"], ini["plan"]["path"])], read_rv, corrupt_first=True)
            note("SELFCHECK_ID|同一条比较改错后 fired=%s" % bool(sc))
            if not sc:
                Fails.append("SELFCHECK_ID")
    eqex = (rv / ini["executor"]["package_path"]).read_bytes() == (repo / ini["executor"]["repo_path"]).read_bytes()
    note("EXECUTOR_COPY|包内见证副本==仓库原件 %s" % eqex)
    if not eqex:
        Fails.append("EXECUTOR_COPY")

    fr = json.loads((rv / "FROZEN_SOURCE.json").read_text(encoding="utf-8"))
    origin = fr["origin_commit"]
    nv = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (origin, fr["needles_original_path"])],
                        capture_output=True)
    ok = nv.returncode == 0 and sha(nv.stdout) == fr["needles_sha256"]
    note("FROZEN_NEEDLES|%s @%s bytes=%d sha=%s 登记=%s %s"
         % (fr["needles_original_path"], origin[:8], len(nv.stdout), sha(nv.stdout)[:12],
            fr["needles_sha256"][:12], "PASS" if ok else "FAIL"))
    if not ok:
        Fails.append("FROZEN_NEEDLES")
    bad = []
    for f in fr["source_files"]:
        b = (rv / "frozen_b502_source" / f["path"]).read_bytes()
        g = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (origin, f["origin_repo_path"])],
                           capture_output=True)
        if not (len(b) == f["bytes"] and sha(b) == f["sha256"] and g.returncode == 0
                and sha(g.stdout) == f["sha256"]):
            bad.append(f["path"])
    note("FROZEN_SRC|文件=%d 包字节==登记==%s 的 blob 失败=%d %s" % (len(fr["source_files"]), origin[:8], len(bad), bad))
    if bad:
        Fails.append("FROZEN_SRC")
    fpairs = [(f["sha256"], "frozen_b502_source/" + f["path"]) for f in fr["source_files"]]
    sc = compare(fpairs, read_rv, corrupt_first=True)
    note("SELFCHECK_FROZEN|首件登记值故意改错必须被判不符: fired=%s %s" % (bool(sc), sc[:1]))
    if not sc:
        Fails.append("SELFCHECK_FROZEN")

    diff = sorted(f["path"].split("/")[-1] for f in fr["source_files"]
                  if (repo / fr["source_root"] / f["path"]).exists()
                  and (repo / fr["source_root"] / f["path"]).read_bytes()
                  != (rv / "frozen_b502_source" / f["path"]).read_bytes())
    note("TIP_VS_FROZEN|当前 tip(%s) 工作树与冻结件不同=%d 件 %s" % (head[:9], len(diff), diff))
    note("TIP_VS_FROZEN|判定: tip 含未上板 R7D1, 板上仍是 b502 %s; S0 打印来源只用冻结快照, 不回灌产品目录"
         % ini["image"]["sha256"][:8])

    vp = (rv / "VALIDATE_PLAN.txt").read_text(encoding="utf-8").splitlines()
    fl = [l.split("|")[0] for l in vp if "|FAIL|" in l]
    note("VALIDATE_PLAN|行=%d 原始 FAIL=%s (任务书要求保留这一条 FAIL 并裁为 N/A)" % (len(vp), fl))
    if fl != ["PLAN_MOTION_CAP_BUDGET"]:
        Fails.append("VALIDATE_PLAN_FAIL_SET")

    sc_ = ident["scope_guards"]
    need = {"serial_port_open_cap": 1, "instrument_capture_start_cap": 1, "no_motion": True,
            "no_new_firmware_download": True, "no_added_powerup_or_reset": True,
            "no_apply_or_irq_change": True, "no_probe_removal_test": True,
            "no_reference_level_test": True, "no_target_injection": True,
            "new_approval_not_inferred_from_task_writing_context": True}
    miss = ["%s=%r" % (k, sc_.get(k)) for k, v in need.items() if sc_.get(k) != v]
    note("SCOPE_GUARDS|核 %d 条 与任务书不符=%d %s" % (len(need), len(miss), miss))
    if miss:
        Fails.append("SCOPE_GUARDS")

    cs = json.loads((rv / "capture_scope.json").read_text(encoding="utf-8"))
    ss = cs["sample_settings"]
    note("CAPTURE_SCOPE|source_tip=%s firmware=%s A_hardware_actions=%d 总窗=%s s 串口硬截止=%s s 请求缓冲=%s MB(不折算样点数)"
         % (cs["source_tip"][:9], cs["firmware_sha256"][:8], cs["A_hardware_actions"],
            ss["capture_max_seconds"], ss["serial_hard_deadline_seconds"], ss["requested_buffer_megabytes"]))
    note("CAPTURE_SCOPE|source_tip==当前 HEAD %s" % (cs["source_tip"] == head))
    if cs["source_tip"] != head:
        Fails.append("CAPTURE_TIP")
    if cs["firmware_sha256"] != ini["image"]["sha256"]:
        Fails.append("CAPTURE_FW")

    dirty = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "rt-thread-master", "tests"],
                           capture_output=True, text=True).stdout.strip()
    note("PRODUCT_CLEAN|产品与主机链目录待提交改动=%d 件" % len([l for l in dirty.splitlines() if l]))
    if dirty:
        note("PRODUCT_CLEAN|原文=%s" % dirty[:400])
        Fails.append("PRODUCT_DIRTY")

    note("PREFLIGHT_SUMMARY|fails=%d %s" % (len(Fails), Fails))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(LINE) + "\n", encoding="utf-8", newline="\n")
    return 1 if Fails else 0


if __name__ == "__main__":
    sys.exit(main())
