#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6C 修前/修后的**同一输入**对照: 复审附录 B 探针分别打在 `6435328` 的执行器字节与本轮修后的字节上。

为什么这样取"修前": 任务书 §6.2 第1条要求先按附录在原 6435328 字节上复现并保留输出。两处缺陷都是
**执行器自身**的行为, 所以对照的两边只能是两份执行器字节 —— 同一份探针、同一份临时账、同一套内存
端口/动作后端。基线那份用 `git cat-file blob` 从对象库直接取, 不碰工作树、不改历史提交。

基线侧需要一个"能跑 S0 的仓根": S0 会 `git rev-parse` 历史提交并在真实 C 源码里证明每条 needle。
所以这里在仓库外建一份最小副本(EXEC/自检/plan/rows/needles/镜像/manifest + needles 引用的那几个 C
源码), 再放一个 `.git` **gitfile** 指向本仓的对象库(只读复用, 不写任何东西、不改索引/工作树)。
这条装配路径与 §6.1 的"若内部 helper 改名可调整装配入口"一致, 探针输入与判据一字未改。

判据(原文都在输出里, 不用汇总标签代替):
  C1a 坏账 + 披露日志写一次失败   修前=只发 motor_start/UNKNOWN/框架异常  修后=三条必要撤能都发/端口关闭/仍不成功
  C1b 合法完整账 cleanup cap=0 + 同样一次写失败   修前=只发 motor_start      修后=同上(且没有 CLEANUP_FRAME_EXCEPTION)
  C1c 两个正控(日志正常)未回退   修后仍四条都发、CONFIRMED_DISABLED、success=False
  C2  S0 在"读出 P 之后把磁盘换成 Q"的交错   修前=S0记Q/执行P/rc=0/下载1次  修后=动作前 S0_PLAN_DISK_DRIFT/零调用
      (C2 的"文件未变"正控与"持续写失败"负控在自检 FS125/FS123, 附录探针本身只安排换文件那一种)

设备动作 0: 全程内存端口 + 内存动作后端, 不起 pyOCD/Keil/串口, 不连 DAP, 不碰设备。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                      # 仓库根 D:/ns800
BASE = "64353285e9b34df1c54ccea4011ac555c482e626"
FIELD = "reports/reviews/phase8-m8-relative-protection-r3/field"
PROBE = HERE / "remaining_probes.py"        # 复审附录 B 逐字节入库的副本(仅一处 Windows 编码适配)
C1_DISARM = ["motor_stop", "motor_disarm", "motor_status"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def blob(rev, rel):
    r = subprocess.run(["git", "-C", str(REPO), "cat-file", "blob", "%s:%s" % (rev, rel)],
                       capture_output=True)
    if r.returncode:
        raise SystemExit("R6C_PROBE_FAIL|基线里取不到 %s:%s|%s" % (rev[:12], rel,
                                                                   r.stderr[-200:]))
    return r.stdout


def build_base_tree(work):
    """把基线那份的输入取到仓库外副本里, 并列出每个文件的 blob sha 与"和工作树是否同字节"。

    S0 需要: 计划里登记的镜像 + build_manifest + 每条 needle 引用的那几个真实 C 源码 +
    能 `git rev-parse` 到历史提交的仓库(用 .git gitfile 只读复用本仓对象库)。
    """
    need = [FIELD + "/m8r3_serial_chain.py", FIELD + "/m8r3_field_selftest.py",
            FIELD + "/plan.json", FIELD + "/rows.json", FIELD + "/needles.json"]
    written = {}
    for rel in need:
        b = blob(BASE, rel)
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b)
        written[rel] = b
    plan = json.loads(written[FIELD + "/plan.json"].decode("utf-8-sig"))
    needles = json.loads(written[FIELD + "/needles.json"].decode("utf-8-sig"))
    img = plan["identity_binding"]["image"]
    src_root = plan.get("needles_source_root", ".")
    extra = [img["path"], img["manifest"]] + \
        sorted({src_root + "/" + n["source"] for n in needles["needles"]})
    for rel in extra:
        b = blob(BASE, rel)
        dst = work / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b)
        written[rel] = b
    (work / ".git").write_text("gitdir: %s\n" % (REPO / ".git").as_posix(), encoding="utf-8")
    return written


def main():
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    work = Path(tempfile.mkdtemp(prefix="ns800-r6c-ba-"))       # 仓库外
    print("R6C_BEFORE_AFTER | 基线 blob=%s | 工作树=%s | 探针=%s(%d B)" %
          (BASE[:12], REPO, PROBE.name, PROBE.stat().st_size))
    written = build_base_tree(work)
    for rel in sorted(written):
        disk = REPO / rel
        b = written[rel]
        if not disk.exists():
            state = "工作树里没有这个路径"
        elif disk.read_bytes() == b:
            state = "同字节"
        elif disk.read_bytes().replace(b"\r\n", b"\n") == b:
            # 产品 C 源码在盘上是 CRLF、在 blob 里是 LF(core.autocrlf), 逐字节比较必然"不同";
            # 这里把它和真正的内容改动分开, 免得读者把行尾差异读成"本轮改了产品 C"。
            state = "仅行尾不同(盘 CRLF/blob LF; git diff 该文件=%s)" % (
                "未变" if subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", BASE, "--", rel],
                                         capture_output=True).returncode == 0 else "**已改**")
        else:
            state = "**内容已不同(本轮改动)**"
        print("  BASE|%s|%d B|sha=%s…|与工作树 %s" % (rel, len(b), sha(b)[:12], state))

    def run(root, tag):
        out = subprocess.run([sys.executable, str(PROBE), str(root)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        if out.returncode != 0:
            raise SystemExit("R6C_PROBE_FAIL|%s rc=%d|%s" % (tag, out.returncode, out.stderr[-600:]))
        return json.loads(out.stdout)

    before, after = run(work, "before"), run(REPO, "after")
    shutil.rmtree(work, ignore_errors=True)

    checks = []

    def ck(name, ok, detail):
        checks.append((name, ok, detail))
        print("%-5s|%s|%s" % ("OK" if ok else "FAIL", name, detail))

    def pick(doc, probe):
        return [r for r in doc["results"] if r["probe"] == probe][0]

    b, a = pick(before, "corrupt-log-failure"), pick(after, "corrupt-log-failure")
    ck("C1a_修前_一次记账写失败吃掉三条必要撤能",
       b["injected_events"] == ["CLEANUP_RESERVE_REFUSED_SENT_ANYWAY"] and b["tx"] == ["motor_start"] and
       [e["kind"] for e in b["frame_errors"]] == ["CLEANUP_FRAME_EXCEPTION"] and
       b["result"]["disarm"] == "UNKNOWN" and b["result"]["success"] is False and
       any("CLEANUP_FRAME_RAISED_OSError" in x for x in b["result"]["cleanup_errors"]),
       "tx=%s 框架异常=%d disarm=%s success=%s 首错=%s" %
       (",".join(b["tx"]), len(b["frame_errors"]), b["result"]["disarm"],
        b["result"]["success"], b["result"]["cleanup_errors"][0][:60]))
    ck("C1a_修后_同输入三条必要撤能都发且端口关闭仍不成功",
       a["injected_events"] == ["CLEANUP_RESERVE_REFUSED_SENT_ANYWAY"] and
       a["tx"] == ["motor_start"] + C1_DISARM and a["closed"] is True and a["frame_errors"] == [] and
       a["result"]["success"] is False and
       any("LEDGER_EVENT_WRITTEN_AFTER_RETRY:CLEANUP_RESERVE_REFUSED_SENT_ANYWAY" in x
           for x in a["result"]["cleanup_errors"]),
       "tx=%s closed=%s 框架异常=%d success=%s disarm=%s 披露=%s" %
       (",".join(a["tx"]), a["closed"], len(a["frame_errors"]), a["result"]["success"],
        a["result"]["disarm"], ";".join(a["result"]["cleanup_errors"])[:120]))

    b2, a2 = pick(before, "exhausted-log-failure"), pick(after, "exhausted-log-failure")
    ck("C1b_修前_合法账额度耗尽+写失败同样只发一条",
       b2["tx"] == ["motor_start"] and b2["result"]["disarm"] == "UNKNOWN" and
       b2["result"]["success"] is False and bool(b2["frame_errors"]),
       "tx=%s 框架异常=%d disarm=%s success=%s" %
       (",".join(b2["tx"]), len(b2["frame_errors"]), b2["result"]["disarm"],
        b2["result"]["success"]))
    ck("C1b_修后_同输入三条都发且逐条隔离(不是一句整体吞掉)",
       a2["tx"] == ["motor_start"] + C1_DISARM and a2["closed"] is True and a2["frame_errors"] == [] and
       a2["result"]["success"] is False and
       len(a2["result"]["cleanup_overruns"]) >= 2,
       "tx=%s closed=%s success=%s 披露=%s" %
       (",".join(a2["tx"]), a2["closed"], a2["result"]["success"],
        ";".join(a2["result"]["cleanup_overruns"])[:120]))
    ck("C1c_两个正控未回退(日志正常时行为与R6B一致)",
       pick(after, "corrupt-control")["tx"] == ["motor_start"] + C1_DISARM and
       pick(after, "corrupt-control")["result"]["disarm"] == "CONFIRMED_DISABLED" and
       pick(after, "exhausted-control")["tx"] == ["motor_start"] + C1_DISARM and
       pick(after, "exhausted-control")["result"]["success"] is False,
       "坏账正控 tx=%s 额度正控 tx=%s" %
       (",".join(pick(after, "corrupt-control")["tx"]),
        ",".join(pick(after, "exhausted-control")["tx"])))

    sb, sa = pick(before, "s0_record_uses_disk_instead_of_execution_snapshot"), \
        pick(after, "s0_record_uses_disk_instead_of_execution_snapshot")
    ck("C2_修前_S0把磁盘换过的Q记成本次执行身份且下载照跑",
       sb["switched"] and sb["rc"] == 0 and sb["s0_records_execution_snapshot"] is False and
       sb["s0_recorded_plan_sha"] == sb["changed_disk_sha"] and
       sb["s0_recorded_plan_sha"] != sb["approved_execution_sha"] and sb["memory_backend_calls"] == 1,
       "rc=%s 执行P=%s… 磁盘Q=%s… S0记=%s… 后端调用=%d" %
       (sb["rc"], sb["approved_execution_sha"][:12], sb["changed_disk_sha"][:12],
        (sb["s0_recorded_plan_sha"] or "-")[:12], sb["memory_backend_calls"]))
    try:
        after_out = Path(sa["stdout"]).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        after_out = "<读不到|%s>" % e
    ck("C2_修后_同一交错在任何动作之前具名拒绝",
       sa["switched"] and sa["rc"] != 0 and sa["memory_backend_calls"] == 0 and
       "S0_PLAN_DISK_DRIFT" in after_out and
       "execution=%s" % sa["approved_execution_sha"][:12] in after_out,
       "rc=%s 后端调用=%d 拒绝身份S0_PLAN_DISK_DRIFT=%s 原文里执行身份=%s…" %
       (sa["rc"], sa["memory_backend_calls"], "S0_PLAN_DISK_DRIFT" in after_out,
        sa["approved_execution_sha"][:12]))
    ck("C2_修后_基线登记的那两个SHA与本轮执行身份仍可逐字节复算",
       sb["approved_execution_sha"] == sa["approved_execution_sha"] and
       sb["changed_disk_sha"] == sa["changed_disk_sha"],
       "P=%s… Q=%s…(修前/修后同一交错输入)" %
       (sa["approved_execution_sha"][:12], sa["changed_disk_sha"][:12]))

    print("\n---- 修前(6435328 的 EXEC 字节)原文 ----")
    print(json.dumps(before["results"], ensure_ascii=False, indent=1))
    print("\n---- 修后(本轮 EXEC 字节)原文 ----")
    print(json.dumps(after["results"], ensure_ascii=False, indent=1))

    bad = [n for n, ok, _d in checks if not ok]
    print("\nRESULT %s (checks=%d fails=%d)" %
          ("BEFORE_AFTER_OK" if not bad else "PROBLEMS:" + ",".join(bad), len(checks), len(bad)))
    print("device_actions=0|serial_ports=0|dap=0|downloads=0|flash=0|readback=0|reset=0|"
          "learns=0|zeros=0|motor_starts=0(仅内存端口)|config_saves=0|keil_builds=0")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
