#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 R6A 的单目标构建(motor 候选)归档成可复算的 manifest。

与 R6 那份同一口径, 但只登记**本轮真正改过**的产品文件: R6A 的产品侧改动只有 R4 那一条
`motor.c` 的打印拆分, 其余三件(tmc2209/stop_priority/safety_thread)沿用 R6 的身份并如实标
"本轮未改"。纪律不变:
  * 散列/字节数一律当场从磁盘或 git blob 算, 不抄任何 JSON 自述值;
  * **不覆盖** R6 的镜像目录, 新件写新目录; 上一轮 d70a9180… 原件原样保留;
  * 拷贝后逐字节比对 build 输出与归档件, 不一致就报错退出不写 manifest;
  * 资源差与 R6 同一 Target 的 manifest 比, 数值取两边的 Program Size 行。

用法: python archive_build_r6a.py            # 写 reports/firmware/phase8-m8-r6a-offline-rework/
      python archive_build_r6a.py --check    # 只重算并比对已归档件, 不写
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent                                  # reports/reviews/phase8-m8-r6a-offline-rework
ROOT = HERE.parents[3]                               # 仓库根 D:/ns800
PROJ = ROOT / ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
               "ns800rt7p65-nssinepad")
BUILD = PROJ / "build/motor_m1"
TARGET = "rt-thread-motor-m1-candidate"
LOG = ROUND / "build/rt-thread-motor-r6a.log"
OUT = ROOT / "reports/firmware/phase8-m8-r6a-offline-rework"
PREV_MANIFEST = ROOT / "reports/firmware/phase8-m8-r6-offline-recovery/build_manifest.json"
ARTIFACTS = (("rt-thread-motor-m1-candidate.bin", BUILD / "rt-thread-motor-m1.bin"),
             ("rt-thread-motor-m1.axf", BUILD / "rt-thread-motor-m1.axf"),
             ("rt-thread-motor-m1.map", BUILD / "rt-thread-motor-m1.map"))
KERNEL = "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
SOURCES = [(KERNEL + "/applications/motor.c",
            "R6A-R4 mot_commit_gated(): withheld(未授予恢复资格)那条本轮新增打印拆成 4 次调用 "
            "(字面 211 B / 最坏 237 B => 每条 <=120 B)"),
           (KERNEL + "/applications/stop_priority.c", "本轮未改(沿用 R6 的 sp_peek_held 字段补齐)"),
           (KERNEL + "/applications/tmc2209.c", "本轮未改(沿用 R6 的长打印拆分)"),
           (KERNEL + "/applications/safety_thread.c", "本轮未改(沿用 R6 的 LIMIT 拆分)"),
           (KERNEL + "/rtconfig.h", "本轮未改(沿上一轮 IDLE_THREAD_STACK_SIZE=1024)")]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def blob_of(rel):
    """工作树文件**经 git 过滤器后**进对象库的那份字节 (blob 字节, blob 散列)。"""
    h = subprocess.run(["git", "hash-object", "-w", "--path", rel, "--", rel],
                       cwd=str(ROOT), capture_output=True, text=True)
    if h.returncode != 0:
        return None
    b = subprocess.run(["git", "cat-file", "blob", h.stdout.strip()], cwd=str(ROOT),
                       capture_output=True)
    return b.stdout if b.returncode == 0 else None


def _source_inputs():
    """编译输入的双身份: `sha256`/`bytes` 取仓库 blob(远端逐 blob 回读比的就是这两个字段),
    `compiled_from_disk_*` 记编译器当场真读到的磁盘字节。两者不等的唯一原因是行尾过滤器。"""
    out = []
    for rel, what in SOURCES:
        p = ROOT / rel
        disk = p.read_bytes()
        bb = blob_of(rel)
        same = bb is not None and bb == disk
        out.append({"path": rel,
                    "bytes": len(bb) if bb is not None else len(disk),
                    "sha256": (hashlib.sha256(bb).hexdigest() if bb is not None
                               else hashlib.sha256(disk).hexdigest()),
                    "compiled_from_disk_bytes": len(disk),
                    "compiled_from_disk_bytes_sha256": hashlib.sha256(disk).hexdigest(),
                    "identity_kind": ("disk_equals_blob" if same else
                                      "blob_after_crlf_filter(disk 多 CR, 差 %d B)"
                                      % (len(disk) - (len(bb) if bb else len(disk)))),
                    "what": what})
    return out


def nbytes(p):
    return Path(p).stat().st_size


def size_line(text):
    for l in text.splitlines():
        if l.startswith("Program Size:"):
            return l.strip()
    raise SystemExit("R6A_ARCHIVE_FAIL|日志里没有 Program Size 行: %s" % LOG)


def sizes(line):
    out = {}
    for tok in line.replace("Program Size:", "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = int(v)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只重算并比对已归档件")
    a = ap.parse_args()
    log_txt = LOG.read_text(encoding="utf-8", errors="replace")
    if "0 Error(s), 0 Warning(s)" not in log_txt:
        raise SystemExit("R6A_ARCHIVE_FAIL|构建日志没有 0 Error(s), 0 Warning(s) 这一行")
    prev = json.loads(PREV_MANIFEST.read_text(encoding="utf-8-sig"))
    prev_size = sizes(prev["targets"][0]["program_size"])
    cur_size = sizes(size_line(log_txt))
    delta = {k: cur_size[k] - prev_size.get(k, 0) for k in sorted(cur_size)}
    prev_img = prev["targets"][0]["images"]["rt-thread-motor-m1-candidate.bin"]
    doc = {"round": "phase8-m8-r6a-offline-rework", "target": TARGET,
           "action": ("Keil Rebuild All x1(只 %s); 未下载、未 Flash、未接 DAP、未复位、"
                      "未开串口、未起转、未 learn/zero/保存配置" % TARGET),
           "command": ("C:\\Keil_v5\\UV4\\UV4.exe -r project.uvprojx -t %s -j0 -o %s"
                       % (TARGET, LOG.as_posix())),
           "uv4_return_code_observed_this_round": 0,
           "uv4_return_code_source": ("本轮实际执行时终端打印的 UV4_EXIT=0(命令与退出码记在 "
                                      "run_record 的 R6A 节); 本脚本不重跑构建, 只判日志里的 "
                                      "0 Error(s), 0 Warning(s) 与 Program Size 行"),
           "error_warning_line": "0 Error(s), 0 Warning(s)",
           "compiler_line_from_log": next((l.strip().lstrip("*").strip() for l in
                                           log_txt.splitlines() if "Using Compiler" in l), "-"),
           "program_size": size_line(log_txt),
           "program_size_vs_previous_round_same_target": {
               "previous": prev["targets"][0]["program_size"],
               "previous_manifest": PREV_MANIFEST.relative_to(ROOT).as_posix(),
               "delta_bytes": delta},
           "build_time_elapsed_line": next((l.strip() for l in log_txt.splitlines() if
                                            l.startswith("Build Time Elapsed")), "-"),
           "log_path": LOG.relative_to(ROOT).as_posix(),
           "log_bytes": nbytes(LOG), "log_sha256": sha(LOG),
           "source_inputs": _source_inputs(),
           "code_commit_at_build": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                                   capture_output=True,
                                                   text=True).stdout.strip(),
           "working_tree_at_build": "改动尚未提交(本 manifest 记录的正是待提交的那份字节)",
           "not_overwritten": {"path": prev_img["path"], "bytes": prev_img["bytes"],
                               "sha256": prev_img["sha256"],
                               "statement": ("R6 那件 d70a9180… 已归档的镜像保持原字节, 本轮新件写"
                                             "新目录; 板上实际在板的仍是更早逐字节证过的 dab356c6…"
                                             "(新件**没有**上板)"),
                               "recomputed_now_matches_archive":
                                   sha(ROOT / prev_img["path"]) == prev_img["sha256"]},
           "new_image_on_board": False,
           "device_actions_by_this_build": 0}
    imgs = {}
    for name, src in ARTIFACTS:
        dst = OUT / name
        if a.check:
            ok = dst.is_file() and sha(dst) == sha(src)
            if not ok:
                raise SystemExit("R6A_ARCHIVE_CHECK_FAIL|%s 与 build 输出不再逐字节相同" % name)
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(src), str(dst))
            ok = sha(dst) == sha(src)
            if not ok:
                raise SystemExit("R6A_ARCHIVE_FAIL|拷贝后比对不等: %s" % name)
        imgs[name] = {"bytes": nbytes(dst), "sha256": sha(dst),
                      "path": dst.relative_to(ROOT).as_posix(),
                      "source_path_relative_to_project": src.relative_to(PROJ).as_posix(),
                      "identical_to_build_output": ok}
    doc["targets"] = [{"target": TARGET, "uv4_return_code_observed_this_round": 0,
                       "error_warning_line": "0 Error(s), 0 Warning(s)",
                       "program_size": size_line(log_txt),
                       "log_bytes": nbytes(LOG), "log_sha256": sha(LOG),
                       "log_archive_path": LOG.relative_to(ROOT).as_posix(),
                       "images": imgs,
                       "map": {"bytes": nbytes(OUT / "rt-thread-motor-m1.map"),
                               "sha256": sha(OUT / "rt-thread-motor-m1.map"),
                               "path": (OUT / "rt-thread-motor-m1.map").relative_to(ROOT).as_posix()}}]
    body = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    mp = OUT / "build_manifest.json"
    if a.check:
        same = mp.is_file() and mp.read_text(encoding="utf-8") == body
        print("R6A_ARCHIVE_CHECK|manifest=%s|images=%d|delta=%s" %
              ("IDENTICAL" if same else "DIFFERS", len(imgs), json.dumps(delta, sort_keys=True)))
        return 0 if same else 1
    mp.write_text(body, encoding="utf-8", newline="\n")
    print("R6A_ARCHIVE_WRITE|%s|bytes=%d|sha=%s|delta=%s" %
          (mp.relative_to(ROOT).as_posix(), nbytes(mp), sha(mp)[:16],
           json.dumps(delta, sort_keys=True)))
    for name, v in sorted(imgs.items()):
        print("R6A_IMAGE|%s|bytes=%d|sha256=%s" % (name, v["bytes"], v["sha256"]))
    subprocess.run([sys.executable, str(ROOT / "reports/reviews/phase8-m8-idle-stack-r4/host/"
                                         "make_round_sums.py"), str(OUT)], cwd=str(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
