# -*- coding: utf-8 -*-
"""R7F8F 反例重放: A 的探针意图 + 操作者实测的两条 F5b 缺口, 在**三份件**上各跑一遍.

三份件(全部只读盘上原件, 本脚本不修改任何件):
  REPAIRED        tools/m8r3_serial_chain_r7f8.py                     本轮返修件(含 F5b)
  F5B_BEFORE      tools/m8r3_serial_chain_r7f8_pre_f5b.py             0ba5035 那份(F1~F5 已落, F5b 未落)
  R7F8F_BEFORE    tools/m8r3_serial_chain_r7f8_pre_r7f8f.py           42d7f7d/e97aaa4 那份(A 复审针对的件)

为什么需要这个文件(而不是只看 A 的 9 条):
  A 的 tools/a_counterexamples.py 直接引用我矩阵里的**夹具名**(MOT_CRUISE / ARM_OK / job('DONE'))。
  返修按 F4/F1 把这些名字改成了**真实产品行形状**, 旧形状原样保留在 *_PRE_FIX 里。于是同名夹具
  的语义变了: A 的 P8 在返修件上变成"合法巡航 => 通过", POS_real_owner_2 变成"DONE 却没候选证据
  => 具名拒"。这两条不是漏修, 是探针引用了被我改名的旧合同。本文件把**意图**而不是**名字**拿出来
  重放, 每条同时声明三件期望——
    REPAIRED: 返修件必须给出的首错/补齐行为;
    F5B_BEFORE / R7F8F_BEFORE: 旧件必须**按各自缺什么复现什么**(否则重放工具本身没在测东西)。
  操作者实测的两条缺口分别落在 F5B_BEFORE 上: I4 尾部一条都没发(缺口①), I5 第五条被
  IRQ_TAIL_HARD_DEADLINE_BEFORE_safety_irq_status 跳过(缺口②)。

真实设备动作: 0(全部 transport=stub / backend=sim, 假端口, 不开串口、不调 pyOCD/Keil/下载器)。
用法: python tools/r7f8f_probe_replay.py --repo <仓库根> --work <仓外工作目录> [--json <输出>]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
SIDES = (("REPAIRED", "tools/m8r3_serial_chain_r7f8.py"),
         ("F5B_BEFORE", "tools/m8r3_serial_chain_r7f8_pre_f5b.py"),
         ("R7F8F_BEFORE", "tools/m8r3_serial_chain_r7f8_pre_r7f8f.py"))
M = None  # 装载后的矩阵模块(夹具与 run_case 的唯一来源)


def load_matrix(repo: pathlib.Path):
    p = repo / ROUND / "tools/r7f8e_case_matrix.py"
    spec = importlib.util.spec_from_file_location("r7f8e_matrix", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fsafe(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))[:80]


def tx_cmds(led):
    return [x.get("bytes", "").split(chr(13))[0].strip()
            for x in led if x.get("kind") == "TX"]


def run_one(repo, work, exe, name, steps, fixture, extra):
    c = dict(name=name, group="R7F8F意图重放", steps=steps, fixture=fixture,
             extra=extra or [], expect={})
    rc, res, led = M.run_case(repo, work, exe, c, name)
    return {"name": name, "rc": rc, "status": res.get("status"), "code": res.get("code"),
            "disarm": res.get("disarm"), "obligation": res.get("obligation"),
            "cleanup_errors": res.get("cleanup_errors"),
            "cleanup_reserve_seconds": res.get("cleanup_reserve_seconds"),
            "sent_results": [x.get("cmd") for x in res.get("results", []) if x.get("cmd")],
            "ledger_kinds": [x.get("kind") for x in led],
            "tx": tx_cmds(led)}


def cases():
    """每条: 名称 / 步骤 / 夹具 / argv / {件: 期望}。三件期望都必须声明, 不许留空当作免检。"""
    m = M
    IRQ_ON = ["[SAFETY] IRQ gate OPEN: 1 active protection input(s) registered"]
    IRQ_OFF = ["[SAFETY] IRQ gate CLOSED - 活动安全输入(0 路)无软件响应路径"]
    IRQ_STILL = ["[SAFETY] IRQ gate stays CLOSED"]
    R, FB, RB = "REPAIRED", "F5B_BEFORE", "R7F8F_BEFORE"
    C = []
    # ①A 的 P8 意图: 合同在禁能侧(expected_ENN=HIGH / ENN_fb=1)绝不能当巡航通过。
    #   输入=最旧那条整合同(一个字段都没改), 而不是改名后的同名夹具。
    C.append(("I1_P8意图_修前整条巡航合同(禁能侧ENN)判红",
              [m.st_("running", "motor_status", key="motor_status", seconds=1,
                    expect_profile="RUNNING")],
              m.fx(responses={"motor_status": m.MOT_CRUISE_PRE_FIX}), [],
              {R: {"status": "STOP", "code": "PROFILE_NOT_RUNNING", "reserve": 7.0},
               FB: {"code": "PROFILE_NOT_RUNNING"},          # F4 已在 0ba5035 落地 => 旧件也拒
               RB: {"status": "OK", "code": ""}}))           # A 复审针对的件: 照样当巡航收了
    # ②A 的 P7 意图: 第二次 arm 不得真的再写一次(旧件会把通电计时丢回)。
    C.append(("I2_P7意图_第二次arm在写入尝试之前拒",
              [m.st_("arm", "motor_arm", key="motor_arm", seconds=2,
                     expect_contains=["ARMED (drv_en request=HIGH"]),
               m.st_("disarm", "motor_disarm", key="motor_disarm", seconds=2,
                     expect_contains=["DISARM (动作已做"]),
               m.st_("ms", "motor_status", key="motor_status", seconds=3,
                     expect_contains=["state="]),
               m.st_("arm2", "motor_arm", key="motor_arm2", seconds=2,
                     expect_contains=["ARMED (drv_en request=HIGH"])],
              m.fx(responses={"motor_arm": m.ARM_OK, "motor_arm2": m.ARM_OK,
                              "motor_disarm": m.DISARM_OK, "motor_status": m.STATUS_DISABLED,
                              "motor_stop": m.STOP_OK}),
              ["--arm-energize-deadline-seconds", "20"],
              {R: {"code": "ARM_SECOND_ATTEMPT_REFUSED", "tx_count": {"motor_arm": 1}},
               FB: {"code": "ARM_SECOND_ATTEMPT_REFUSED", "tx_count": {"motor_arm": 1}},
               RB: {"code": "", "tx_count": {"motor_arm": 2}}}))
    # ③A 的 P6 意图: arm 回包迟到超过通电截止 => 在截止点就进收口(最旧件照样等完那份回包再走)。
    f3 = m.fx(responses={"motor_arm": m.ARM_OK, "motor_status": m.STATUS_DISABLED,
                         "motor_stop": m.STOP_OK, "motor_disarm": m.DISARM_OK})
    f3["block"] = {"motor_arm": 3.0}
    C.append(("I3_P6意图_arm迟到回包在通电截止即进收口",
              [m.st_("arm", "motor_arm", key="motor_arm", seconds=2,
                     expect_contains=["ARMED (drv_en request=HIGH"])],
              f3, ["--arm-energize-deadline-seconds", "2", "--total-deadline-seconds", "30"],
              {R: {"code": "MISSING_RESPONSE_PROMPT",
                   "tx_must": ["motor_stop", "motor_disarm", "motor_status"],
                   "disarm": "UNKNOWN"},
               FB: {"code": "MISSING_RESPONSE_PROMPT", "disarm": "UNKNOWN"},
               RB: {"code": "", "disarm": "CONFIRMED_DISABLED"}}))
    # ④操作者实测 F5b①: 先开 IRQ、随后查询失败(电机从未武装) => 尾部两条仍必须发出。
    C.append(("I4_F5b1_开IRQ后arm前失败_尾部disable与status仍发出",
              [m.st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
                     expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
               m.st_("boom", "motor_status", key="motor_status_running", seconds=2,
                     expect_profile="RUNNING")],
              m.fx(responses={"safety_irq_enable": IRQ_ON,
                              "motor_status_running": m.STATUS_DISABLED,
                              "safety_irq_disable": IRQ_STILL, "safety_irq_status": IRQ_OFF}),
              [],
              {R: {"code": "PROFILE_NOT_RUNNING", "disarm": "NOT_ARMED",
                   "tx_must": ["safety_irq_disable", "safety_irq_status"],
                   "tx_must_none": ["motor_stop", "motor_disarm"],
                   "events_must": ["CLEANUP_MOTOR_NOT_OBLIGATED"]},
               # 缺口①就落在这份件上: 尾部由电机 obligation 决定 => 一条都没发。
               FB: {"code": "PROFILE_NOT_RUNNING", "disarm": "NOT_ARMED",
                    "tx_must_none": ["safety_irq_disable", "safety_irq_status"]},
               RB: {"code": "PROFILE_NOT_RUNNING",
                    "tx_must_none": ["safety_irq_disable", "safety_irq_status"]}}))
    # ⑤操作者实测 F5b②: 收口全部无响应 => 五条各有发送机会(第五条不被 IRQ_TAIL_HARD_DEADLINE 跳过)。
    C.append(("I5_F5b2_收口全部无响应_五条各有发送机会",
              [m.st_("arm", "motor_arm", key="motor_arm", seconds=2,
                     expect_contains=["ARMED (drv_en request=HIGH"]),
               m.st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
                     expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
               m.st_("st", "motor_start", key="motor_start", seconds=2,
                     expect_absent=["REFUSED"], quota={"name": "motor_run", "n": 1}),
               m.st_("w", "__watch__", key="__watch__", seconds=5)],
              m.fx(responses={"motor_arm": m.ARM_OK, "safety_irq_enable": IRQ_ON,
                              "motor_start": ["[MOT] step pwm enabled"],
                              "__watch__": ["[DIAG] tick"]}),
              ["--arm-energize-deadline-seconds", "30", "--motion-cap-seconds", "1",
               "--total-deadline-seconds", "60"],
              {R: {"code": "CLEANUP_UNCONFIRMED", "disarm": "UNKNOWN", "reserve": 7.0,
                   "tx_must": ["motor_stop", "motor_disarm", "motor_status",
                               "safety_irq_disable", "safety_irq_status"],
                   "tx_none_contains": ["HARD_DEADLINE_BEFORE", "RESERVE_SHORT"],
                   "err_must": ["motor_stop:NO_RESPONSE",
                                "IRQ_TAIL_safety_irq_status:NO_RESPONSE"]},
               # 缺口②就落在这份件上: 4 秒预留排不下五条 => 第五条被具名跳过。
               FB: {"code": "CLEANUP_UNCONFIRMED", "disarm": "UNKNOWN",
                    "tx_must": ["motor_stop", "motor_disarm", "motor_status",
                                "safety_irq_disable"],
                    "tx_must_none": ["safety_irq_status"],
                    "err_must": ["IRQ_TAIL_HARD_DEADLINE_BEFORE_safety_irq_status"]},
               RB: {"code": "CLEANUP_UNCONFIRMED", "disarm": "UNKNOWN",
                    "tx_must": ["motor_stop", "motor_disarm", "motor_status"],
                    "tx_must_none": ["safety_irq_disable", "safety_irq_status"]}}))
    # ⑥申报排不下五条的预留 => 返修件在开口之前具名拒; 两份旧件连这个申报口都没有
    #    (--cleanup-reserve-seconds 不认 → argparse 失败), 申报不了也就谈不上守。
    C.append(("I6_F5b2_申报4秒预留在开口之前拒(旧件没有这条 CLI 申报口)",
              [m.st_("arm", "motor_arm", key="motor_arm", seconds=2,
                     expect_contains=["ARMED (drv_en request=HIGH"]),
               m.st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
                     expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1})],
              m.fx(responses={"motor_arm": m.ARM_OK, "safety_irq_enable": IRQ_ON,
                              "safety_irq_disable": IRQ_STILL, "safety_irq_status": IRQ_OFF,
                              "motor_stop": m.STOP_OK, "motor_disarm": m.DISARM_OK,
                              "motor_status": m.STATUS_DISABLED}),
              ["--arm-energize-deadline-seconds", "20", "--cleanup-reserve-seconds", "4"],
              {R: {"code": "CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS",
                   "reserve": 4.0, "tx_must_none": ["motor_arm"]},
               FB: {"code": "RUNNER_PARSE_FAILED"},
               RB: {"code": "RUNNER_PARSE_FAILED"}}))
    return C


def check(res, want, led_kinds=None):
    bad = []
    if "status" in want and res["status"] != want["status"]:
        bad.append("status=%s 期望=%s" % (res["status"], want["status"]))
    if "code" in want and res["code"] != want["code"]:
        bad.append("code=%s 期望=%s" % (res["code"], want["code"]))
    if "disarm" in want and res["disarm"] != want["disarm"]:
        bad.append("禁能终态=%s 期望=%s" % (res["disarm"], want["disarm"]))
    if "reserve" in want and res.get("cleanup_reserve_seconds") != want["reserve"]:
        bad.append("回执收口预留=%s 期望=%s" % (res.get("cleanup_reserve_seconds"),
                                                want["reserve"]))
    for c, n in (want.get("tx_count") or {}).items():
        got = len([x for x in res["tx"] if x == c])
        if got != n:
            bad.append("TX %s 实发 %d 要求 %d(全部 TX=%s)" % (c, got, n, res["tx"]))
    for c in want.get("tx_must", []):
        if c not in res["tx"]:
            bad.append("未发 %s(全部 TX=%s)" % (c, res["tx"]))
    for c in want.get("tx_must_none", []):
        if c in res["tx"]:
            bad.append("不该发却发了 %s(全部 TX=%s)" % (c, res["tx"]))
    for needle in want.get("tx_none_contains", []):
        if needle in "|".join(res["tx"]):
            bad.append("TX 里出现了不该有的 %s(TX=%s)" % (needle, res["tx"]))
    errs = " ".join(res.get("cleanup_errors") or [])
    for needle in want.get("err_must", []):
        if needle not in errs:
            bad.append("清理错误里没有 %s(errors=%s)" % (needle, errs[:160]))
    for needle in want.get("err_none", []):
        if needle in errs:
            bad.append("清理错误里出现了不该有的 %s(errors=%s)" % (needle, errs[:160]))
    for k in want.get("events_must", []):
        if k not in (led_kinds or []):
            bad.append("台账缺事件 %s(实有=%s)" % (k, sorted(set(led_kinds or []))[:12]))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args()
    global M
    repo = a.repo.resolve()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    M = load_matrix(repo)
    out, fails, lines = [], [], []
    for name, steps, fixture, extra, wants in cases():
        missing = [s for s, _ in SIDES if s not in wants]
        if missing:
            fails.append("EXPECT_MISSING|%s|%s" % (name, ",".join(missing)))
            lines.append("REPLAY|ALL|FAIL|%s|期望没声明的件=%s" % (name, ",".join(missing)))
            continue
        for side, rel in SIDES:
            exe = repo / ROUND / rel
            if not exe.is_file():
                fails.append("%s|%s|件不在盘上" % (side, name))
                continue
            d = work / fsafe(side)
            res = run_one(repo, d, exe, name, steps, fixture, extra)
            bad = check(res, wants[side], res.get("ledger_kinds"))
            if bad:
                fails.append("%s|%s" % (side, name))
            out.append({"case": name, "side": side, "executor": exe.name,
                        "sha256_head": hashlib.sha256(exe.read_bytes()).hexdigest()[:12],
                        "want": wants[side], "bad": bad, "result": res})
            lines.append("REPLAY|%s|%s|%s|code=%s|disarm=%s|reserve=%s|tx=%s|%s" % (
                side, "PASS" if not bad else "FAIL", name, res["code"], res["disarm"],
                res.get("cleanup_reserve_seconds"), ",".join(res["tx"]) or "-",
                ";".join(bad)))
    lines.append("REPLAY_SUMMARY|件数=%d|每条件数=%d|FAIL=%d|%s" % (
        len(out), len(SIDES), len(fails), ",".join(fails) or "none"))
    text = "\n".join(lines) + "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))
    if a.json:
        a.json.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
