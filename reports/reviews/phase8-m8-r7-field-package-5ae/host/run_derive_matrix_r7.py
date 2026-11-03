#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重跑并归档 `host/derive_matrix.txt`: 派生件的五步写入语义(真实退出码, UTF-8/LF)。

这一步证明的不是"包能跑", 而是**派生件不会被静默改坏**:
  无参重跑   => 算出的字节与盘上完全相同 => 重写同值(不覆盖不同内容)
  盘上被改坏 => 无参重跑必须具名拒绝(R7_REFUSE_WRITE), 退出码非 0
  --force    => 显式覆写才落地(本包派生件从未执行过任何设备动作, 才允许这一步)
  --check    => 复原后重新幂等, 且冻结源(R6C 交付件)字节未变
改坏 → 拒绝 → --force 复原这条链本身也是负控: 拒绝没生效的话, 后面拿不到原身份。
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
HOST = HERE
PLAN = REPO / "reports/reviews/phase8-m8-r7-field-package-5ae/field/plan_core_r7.json"
TOOL = (REPO / "reports/reviews/phase8-m8-r7-field-package-5ae/host/derive_plan_r7.py")
SRC = (REPO / "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0/field/plan_core_r6c.json")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(args, want_rc):
    p = subprocess.run([sys.executable, str(TOOL)] + args, cwd=str(REPO), env=ENV,
                       capture_output=True, text=True, encoding="utf-8")
    out = (p.stdout or "") + (p.stderr or "")
    ok = p.returncode == want_rc
    return p.returncode, out.rstrip("\n"), ok


def main():
    before = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    size = PLAN.stat().st_size
    lines = [
        "# R7 派生件的写入语义: 本机按顺序跑的五步, 每步单独记**它自己的**退出码(不是转述)",
        "# 派生件 = field/plan_core_r7.json (%d B / sha %s); 冻结源 = plan_core_r6c.json (%d B / sha %s)"
        % (size, before[:64], SRC.stat().st_size, hashlib.sha256(SRC.read_bytes()).hexdigest()[:64]),
        "# 落盘口径: 由 host/run_derive_matrix_r7.py 生成, 子进程 print 在 Windows 下的 CRLF 已整体规范化为 LF"
        "(只动行尾, 文本与退出码一字未改)",
        "",
    ]
    bad = 0

    steps = [
        ([], 0, "无参: 目标已存在且算出的字节与之完全相同 => 重写同值(不是覆盖不同内容)"),
    ]
    for args, want, note in steps:
        rc, out, ok = run(args, want)
        lines += ["$ python host/derive_plan_r7.py        # %s" % note, "rc=%d%s" % (rc, "" if ok else "  <== 非预期"),
                  out, ""]
        bad += 0 if ok else 1

    # 就地改坏一个字段(模拟手工编辑/磁盘漂移), 保留原字节以便复原
    original = PLAN.read_bytes()
    doc = json.loads(original.decode("utf-8"))
    doc["quota"]["download"] = 0
    PLAN.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="")
    lines += ["$ python -c \"...把派生件就地改坏: quota.download 1 -> 0...\"   # 模拟磁盘漂移/手工编辑",
              "corrupted sha=%s" % hashlib.sha256(PLAN.read_bytes()).hexdigest()[:64], ""]

    rc, out, ok = run([], 1)
    lines += ["$ python host/derive_plan_r7.py        # 无参: 盘上字节与算出的不同 => 必须具名拒绝, 不静默覆盖",
              "rc=%d%s" % (rc, "" if ok else "  <== 非预期(拒绝没生效就是缺陷)"), out, ""]
    bad += 0 if ok else 1

    rc, out, ok = run(["--force"], 0)
    lines += ["$ python host/derive_plan_r7.py --force   # 显式覆写(本包从未执行过设备动作, 才允许这一步)",
              "rc=%d%s" % (rc, "" if ok else "  <== 非预期"), out, ""]
    bad += 0 if ok else 1

    rc, out, ok = run(["--check"], 0)
    lines += ["$ python host/derive_plan_r7.py --check   # 复原后重新幂等 + 冻结源校验",
              "rc=%d%s" % (rc, "" if ok else "  <== 非预期"), out, ""]
    bad += 0 if ok else 1

    after = hashlib.sha256(PLAN.read_bytes()).hexdigest()
    restored = PLAN.read_bytes() == original
    lines += ["$ 核身份: 五步之后派生件必须回到动手前的同一件",
              "before=%s\nafter =%s\nMATCH=%s (逐字节相等=%s, bytes=%d)"
              % (before, after, before == after, restored, PLAN.stat().st_size), "",
              "device_actions=0|serial_ports_opened=0|dap_connections=0|keil_builds=0"]
    if not (before == after and restored):
        bad += 1
    HOST.joinpath("derive_matrix.txt").write_bytes(
        "\n".join(lines).replace("\r\n", "\n").encode("utf-8"))
    print("R7_MATRIX|bytes=%d|unexpected=%d|identity_restored=%s"
          % (HOST.joinpath("derive_matrix.txt").stat().st_size, bad, before == after))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
