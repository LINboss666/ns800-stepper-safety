#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本轮三份离线证据(计划门 + 就绪检查 + 受影响回归门)按**真实退出码**跑成 UTF-8/LF 原文归档。

为什么要有这一步: 前一次我是直接在 shell 里 `python ... > 文件`, Windows 控制台默认 cp936,
落盘的字节是 GBK —— 本机看着正常, 换一台机器(或云端 A)按 UTF-8 读就是乱码。R6C 那轮的
`derive_matrix.txt`/`r6c_before_after.txt` 都是 UTF-8, 本轮口径要和它们一致。

第三步(`idle_learn_check.py` 那条 120 项链)不是本轮新写的东西, 但本轮**改了它的 P8**(执行器登记
从五层升到六层, 加进 R7), 所以它的当前结果属于本轮该交的证据; 原文不筛不剪。

纪律:
  * 每步单独 subprocess, 记**它自己的**退出码(不 `echo $?` 猜, 也不把管道末端的码当命令的码);
  * 子进程强制 `PYTHONIOENCODING=utf-8`, 捕获的是原始字节, 父进程按 utf-8 **严格**解码;
    解不出来就当场失败, 不允许悄悄退回 gbk 兜底(那等于把乱码写成"证据");
  * 文本一律 LF 落盘(Windows 上 `open(..., 'w')` 会把 \n 变 \r\n, 所以用二进制写);
  * 全程 0 设备动作: 三步都是离线只读(计划门/回归门)与内存桩干跑(就绪检查)。
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
HOST = HERE
EXEC = (REPO / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py")
PLAN_REL = "reports/reviews/phase8-m8-r7-field-package-5ae/field/plan_core_r7.json"

STEPS = [
    ("validate_plan_r7.txt", 1,
     [sys.executable, str(EXEC), "--validate-plan", "--plan", PLAN_REL, "--repo-root", str(REPO)],
     "执行器自带的离线计划门(--validate-plan): 命令真实存在/取行取得到/额度申报过/人工门在运动之前。"
     "预期且如实保留 fail=1(PLAN_MOTION_CAP_BUDGET 对无起转包是恒红门, 见 plan 的 "
     "motion_budget.registered_checker_gap)。"),
    ("readiness_transcript.txt", 0,
     [sys.executable, str(HOST / "r7_readiness.py")],
     "本轮 17 项就绪检查, 含 R7-6 整包 stub 干跑(SimActionBackend + 计数子类, 不开端口)。"),
    ("idle_learn_check_r7.txt", 0,
     [sys.executable, str(REPO / "reports/reviews/phase8-m8-idle-stack-r4/host/idle_learn_check.py")],
     "受本轮影响的既有回归门(P 系列 120 项链 + 元负控): 本轮把它的 P8 执行器登记从五层升到六层"
     "(加进 R7 这一层), 所以它当前的结果属于本轮该交的证据; 原文不筛不剪。"),
]


def main():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    bad = 0
    for fname, want_rc, argv, note in STEPS:
        p = subprocess.run(argv, cwd=str(REPO), env=env, capture_output=True)
        try:
            out = p.stdout.decode("utf-8")
            err = p.stderr.decode("utf-8")
        except UnicodeDecodeError as e:
            print("TRANSCRIPT_REFUSED|%s|%s 子进程输出不是 UTF-8: %s" % (fname, e.encoding, e))
            bad += 1
            continue
        head = [
            "# %s —— 由 host/run_transcripts_r7.py 于本机实跑生成(原文未删改)" % fname,
            "# 说明: %s" % note,
            "$ %s" % " ".join(argv),
            "cwd=%s|python=%s|PYTHONIOENCODING=utf-8" % (REPO, sys.executable),
            "exit_code=%d(本步预期 %d; 退出码是子进程自己的, 不是管道末端的)" % (p.returncode, want_rc),
            "",
        ]
        body = out if not err else out + "\n--- stderr ---\n" + err
        (HOST / fname).write_bytes(("\n".join(head) + body).replace("\r\n", "\n").encode("utf-8"))
        tail = [l for l in body.splitlines() if l.strip()]
        print("TRANSCRIPT|%s|rc=%d|want=%d|bytes=%d|last=%s" %
              (fname, p.returncode, want_rc, (HOST / fname).stat().st_size,
               (tail[-1] if tail else "-")[:110]))
        if p.returncode != want_rc:
            bad += 1
    print("R7_TRANSCRIPTS|steps=%d|unexpected_exit_or_not_written=%d|device_actions=0|"
          "serial_ports_opened=0|keil_builds=0" % (len(STEPS), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
