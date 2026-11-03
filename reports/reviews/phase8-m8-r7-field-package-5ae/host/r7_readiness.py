#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R7 候选包的**零设备**就绪检查 + 整包 stub 干跑(不开串口、不连 DAP、不起任何工具子进程)。

为什么要有这个文件: validate-plan 只有 47 项且其中 PLAN_MOTION_CAP_BUDGET 对"无起转"的包是恒红门
(见 plan 的 motion_budget.registered_checker_gap)。本包真正需要成立的合同是另外几条, 它们不在那条门里:
  R7-1 镜像身份三处同源且**不等于**旧在板件(dab356c6…)
  R7-2 整包没有任何起转步骤, 运动类额度全 0, 两个会话阶段都挂了 forbidden_commands 且步骤不碰它们
  R7-3 被动启动窗算术(复位在首条查询之前 + drain+首条查询+3s >= observation_bounds 下界, 且不申报静默)
  R7-4 干跑夹具的每一行都有来源(board_capture / predicted_from_source), 预测行不许混充实抓
  R7-5 短打印判据的每条 needle 都能在产品源码那一行找到, 且保守字节上界 <=120
  R7-6 stub 整包贯通: S0..S4 全部跑到终态, sim 后端确实" attempted "了 download/readback/reset,
       而设备动作计数为 0
  R7-7 本轮两处登记更正本身可核对: 执行器路径改成仓内相对且磁盘实算相符; "下载之前板上是哪一件"
       等于最新一次逐字节回读(readback.bin)的实算身份且!==下载对象; 被更正的旧口径也回算得到
退出 0 = 全部 OK; 任何一条不成立都打印在 SUMMARY 里, 不折叠成"通过"。
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FIELD = HERE.parent / "field"
PLAN_PATH = FIELD / "plan_core_r7.json"
EXEC = REPO / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
APP = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
       "ns800rt7p65-nssinepad/applications")
MOTION_CMDS = {"motor_arm", "motor_start"}
OLD_IMAGE = "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439"

results = []


def ck(name, ok, detail):
    results.append((name, bool(ok)))
    print("%-5s|%s|%s" % ("OK" if ok else "FAIL", name, detail))


def sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    plan = json.loads(PLAN_PATH.read_bytes().decode("utf-8-sig"))
    img = plan["identity_binding"]["image"]
    dl, rb = plan["tools"]["download"], plan["tools"]["readback"]

    # R7-1 三件同源且是新镜像
    trip = lambda d: (d["path"], int(d["bytes"]), d["sha256"])
    now = {"bin": (img["path"], img["bytes"], img["sha256"]),
           "axf": (img["axf"]["path"], img["axf"]["bytes"], img["axf"]["sha256"]),
           "dl": (dl["image"]["path"], dl["image"]["bytes"], dl["image"]["sha256"]),
           "rb": (rb["image"]["path"], rb["image"]["bytes"], rb["image"]["sha256"])}
    for k, v in now.items():
        p = REPO / v[0]
        ck("R7-1_%s 磁盘实算相符" % k, p.is_file() and (v[1], v[2]) == (p.stat().st_size, sha(p)),
           "%s %d B %s…" % (v[0], v[1], v[2][:12]))
    ck("R7-1 四处同一镜像且不是旧在板件",
       now["bin"] == now["rb"] and now["axf"] == now["dl"]
       and img["sha256"] != OLD_IMAGE and dl["argv"][-1] == img["axf"]["path"],
       "bin=axf 各自成对, download.argv 末项==axf 路径, sha!=%s…" % OLD_IMAGE[:8])

    # R7-2 无起转
    steps = [s for stg in plan["stages"] for s in (stg.get("steps") or [])]
    cmds = [" ".join(str(s.get("cmd", "")).split()) for s in steps]
    banned = set()
    for stg in plan["stages"]:
        banned |= set(stg.get("forbidden_commands") or [])
    q = plan["quota"]
    ck("R7-2 没有任何起转/带电步骤",
       not [c for c in cmds if c.split(" ")[0] in MOTION_CMDS] and
       not [c for c in cmds if c.startswith("diag_profile learn")],
       "步骤命令=%s" % ",".join(sorted({c.split(" ")[0] for c in cmds})))
    ck("R7-2 运动与学习类额度全 0",
       all(q.get(k, 0) == 0 for k in ("motor_run", "motor_run_seconds_total", "config_save",
                                      "diag_profile_learn", "diag_profile_zero",
                                      "diag_profile_commit", "blackbox_clear",
                                      "irq_polarity_prep", "cleanup_motor_stop",
                                      "cleanup_motor_disarm", "cleanup_motor_status")),
       "motor_run=%s total=%s learn=%s cleanup=%s/%s/%s" %
       (q["motor_run"], q["motor_run_seconds_total"], q["diag_profile_learn"],
        q["cleanup_motor_stop"], q["cleanup_motor_disarm"], q["cleanup_motor_status"]))
    sess = [stg for stg in plan["stages"] if stg["kind"] == "serial_session"]
    ck("R7-2 两个会话阶段都挂禁令且步骤不撞禁令",
       len(sess) == 2 and all(stg.get("forbidden_commands") for stg in sess) and
       not [c for c in cmds if c.split(" ")[0] in banned or c in banned],
       "禁令集合命中=%s" % (sorted(banned & {c.split(" ")[0] for c in cmds}) or "无"))

    # R7-3 启动窗算术
    ob = plan["observation_bounds"]["boot_window_seconds_min"]
    rows = json.loads((FIELD / "rows.json").read_bytes().decode("utf-8-sig"))
    st = sess[0]
    rst = st["steps"][0]
    first = next(s for s in st["steps"][1:] if s.get("cmd"))
    total = float(rst.get("boot_drain_seconds", 0)) + float(first.get("seconds", 0)) + 3
    ck("R7-3 复位在首条查询之前 + 被动窗覆盖下界",
       rst.get("action") == "reset" and st.get("reset_after_port_open") and
       st.get("post_reset_boot") and "boot_quiet_seconds" not in rst and total >= float(ob),
       "drain=%s + 首条=%s + 3s = %.0f >= 下界 %s; 未申报静默=%s" %
       (rst.get("boot_drain_seconds"), first.get("seconds"), total, ob,
        "boot_quiet_seconds" not in rst))

    # R7-4 夹具来源可分
    kinds = {}
    for name, blk in rows["commands"].items():
        kinds.setdefault(blk.get("_provenance_r7", {}).get("kind", "board_capture"), []).append(name)
    ck("R7-4 每一块行表都有具名来源",
       all(b.get("_provenance") for b in rows["commands"].values()) and
       rows["_about"]["two_sources"] and "not_evidence_of" in rows["_about"],
       "来源分布=%s" % {k: len(v) for k, v in sorted(kinds.items())})

    # R7-5 短打印判据回到源码
    tgt = plan["print_width_targets"]
    # 回到派生器**再算一遍**(同一函数、同一行号), 而不是拿源码文本硬凑: C 的相邻字面量会被
    # 拼接, 原样在 body 里搜 needle 会跨在两个字符串字面量的引号边界上而永远搜不到。
    dspec = importlib.util.spec_from_file_location(
        "r7_derive", str(HERE / "derive_plan_r7.py"))
    der = importlib.util.module_from_spec(dspec)
    sys.modules["r7_derive"] = der
    dspec.loader.exec_module(der)

    def real_needle(e):
        rel, nums = e["source"].split(":")
        again = der.source_literal(rel, [int(n) for n in nums.split(",")])
        return (again["needle"] == e["needle"] and
                again["line_bytes_upper_bound"] == e["line_bytes_upper_bound"])
    items = tgt["limit_line"] + tgt["tmc_configured_lines"]
    ck("R7-5 每条 needle 都在其登记源码行里",
       all(real_needle(e) for e in items),
       ";".join("%s<=%dB" % (e["source"].split("/")[-1], e["line_bytes_upper_bound"])
                for e in items))
    ck("R7-5 保守宽度全部 <=120 B",
       all(e["line_bytes_upper_bound"] <= 120 for e in items +
           tgt["mot_withheld_lines_not_in_scope"]["sources"]),
       "最宽=%d B(含不在本包范围的 withheld 四条)" %
       max([e["line_bytes_upper_bound"] for e in items +
            tgt["mot_withheld_lines_not_in_scope"]["sources"]]))

    # R7-6 stub 整包干跑(零设备)
    sys.path.insert(0, str(FIELD))
    spec = importlib.util.spec_from_file_location("r7_exec", str(EXEC))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    snap = m.read_plan_snapshot(str(PLAN_PATH))
    out = Path(tempfile.mkdtemp(prefix="ns800-r7-dryrun-"))
    calls = []

    class CountingSim(m.SimActionBackend):
        def download(self, act, ctx):
            calls.append("download"); return super().download(act, ctx)

        def readback(self, act, ctx):
            calls.append("readback"); return super().readback(act, ctx)

        def reset(self, act, ctx):
            calls.append("reset"); return super().reset(act, ctx)

    old = m.SimActionBackend
    m.SimActionBackend = CountingSim
    try:
        res = m.dispatch_plan(snap["doc"], "stub", "sim", out, m.SimClock(), "healthy",
                              "IN_MEMORY", str(REPO), plan_dir=str(FIELD),
                              plan_path=str(PLAN_PATH), plan_snapshot=snap)
    finally:
        m.SimActionBackend = old
    ids = [d["id"] for d in res["stages"]]
    ck("R7-6 stub 整包贯通(0 设备动作)",
       res["status"] == "OK" and ids == ["S0", "S1", "S2", "S3", "S4"] and
       not res.get("not_run") and calls.count("download") == 1 and
       calls.count("readback") == 1 and calls.count("reset") >= 1,
       "status=%s 阶段=%s 后端调用=%s 未跑=%s 额度剩余=%s" %
       (res["status"], ids, calls, res.get("not_run"),
        {k: v for k, v in (res.get("quota_left") or {}).items() if v != 0}))
    print("DRYRUN_DIR|%s" % out)
    # R7-7 本轮两处登记更正的可核对性(执行器路径 + 下载前在板件)
    ex = plan["identity_binding"]["executor"]
    exp = REPO / ex["path"]
    ck("R7-7 执行器登记是仓内相对路径且磁盘实算相符",
       not os.path.isabs(ex["path"]) and ":" not in ex["path"][:3] and exp.is_file() and
       (int(ex["bytes"]), ex["sha256"]) == (exp.stat().st_size, sha(exp)) and
       str(ex.get("path_r6c_template_verbatim", "")).startswith("D:"),
       "%s %s…/%d B 绝对路径原样保留=%s" %
       (ex["path"], ex["sha256"][:12], exp.stat().st_size, bool(ex.get("path_r6c_template_verbatim"))))
    bib = plan["identity_binding"]["board_image_before"]
    rbfile = REPO / "reports/reviews/phase8-m8-idle-stack-r4/field/field-20261101T142722Z/readback.bin"
    ck("R7-7 下载前在板件=最新一次逐字节回读的实算身份",
       rbfile.is_file() and sha(rbfile) == bib["sha256"] and
       rbfile.stat().st_size == int(bib["bytes"]) and bib["sha256"] != img["sha256"] and
       bib.get("supersedes_in_r6c_template", {}).get("sha256") ==
       sha(REPO / "reports/reviews/phase8-m8-diag-stack-r1/field/field-20261101T084256Z/flash_readback.bin"),
       "readback.bin=%s…/%d B == board_image_before.sha; 与下载对象(%s…)不同; "
       "被更正的旧口径 f5c2fd89… 也对得上它那轮回读" %
       (sha(rbfile)[:12], rbfile.stat().st_size, img["sha256"][:12]))
    ck("R7-7 键的形状: 继承是对象的就是对象(tuple 门的外部证明)",
       isinstance(plan["motion_budget"], dict) and
       isinstance(plan["stage_numbering_and_legacy_fields"]["legacy_history_only_blocks"], dict),
       "motion_budget=%s legacy_blocks 条目=%d" %
       (type(plan["motion_budget"]).__name__,
        len(plan["stage_numbering_and_legacy_fields"]["legacy_history_only_blocks"])))
    ck("R7-7 顶层身份字段写的是本轮, 不是上一轮的 R5-C 续测包",
       plan.get("task_id") == "phase8-m8-r7-field-package-5ae" and
       plan.get("status") == "R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED" and
       plan.get("package_status") == plan.get("status") and
       "R7" in str(plan.get("package")) and "R5-C 续测" not in str(plan.get("package")),
       "task_id=%s status=%s package=%s…" %
       (plan.get("task_id"), plan.get("status"), str(plan.get("package"))[:28]))

    print("\nR7_READINESS_SUMMARY|checks=%d|fails=%d|device_actions=0|serial_ports_opened=0|"
          "dap=0|downloads=0|flashes=0|readbacks=0|resets=0|motor_starts=0|keil_builds=0" %
          (len(results), sum(1 for _n, ok in results if not ok)))
    return 1 if any(not ok for _n, ok in results) else 0


if __name__ == "__main__":
    sys.exit(main())
