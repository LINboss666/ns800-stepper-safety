# -*- coding: utf-8 -*-
"""R7F8E §4 离线验证矩阵(任务书 M8R7F8E 第 4 节的 11 组场景 + 5 项元负控 + 4 项原始字节探针)。

全部 stub transport / sim backend / SimClock / FakePort: 不开 COM、不连真实后端、不启动 Logic、
不跑 pyOCD、不跑 Keil、不建真实批准账。临时件写在 --work 指的仓库外目录。

每组都要"输入 + 实发命令 + 台账 + 结果 + 失败反例", 因此断言同时看:
  返回码里的 code / 实际写到端口的命令文本 / 台账事件 / 额度 used / 轮询序号。
R 组是给"非法 UTF-8"单独开的: 那种字节序列在 JSON 夹具里无法表达(喂端口前要过一次严格
encode), 只能按 R6B 先例在进程内向同一条读路径灌原始坏字节。
末尾两类自证: (a) 元负控 —— 把某一处新门从突变副本里摘掉, 同一条输入必须不再具名检出;
             (b) 故意写错一条预期, 要求矩阵自己判红(否则整张绿不作数)。
用法: python tools/r7f8e_case_matrix.py --repo D:/ns800 --work <仓库外可写目录>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
EXE_REL = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
OLD_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
OLD_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
OLD_BYTES = 219491
APP = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
       "ns800rt7p65-nssinepad/applications")

out, fails = [], []


def note(name, ok, detail=""):
    out.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- 真实产品行形状(motor.c / diag_relative.c)
def started(jid, n=8, lo=450, hi=550, d=0):
    return ["[DIAG] learn STARTED id=%d n=%d win=%d..%d dir=%d" % (jid, n, lo, hi, d),
            "[DIAG] learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED"]


def job(jid, st, why="NONE", owner=1, cred="1/1", adc=8, sg=8, want=8, lo=450, hi=550, d=0):
    return ["[DIAG] job id=%d st=%s why=%s owner=%d cred=%s" % (jid, st, why, owner, cred),
            "[DIAG] job n=%d/%d want=%d win=%d..%d dir=%d" % (adc, sg, want, lo, hi, d)]


def cancel_requested(jid, st="CANCEL_PENDING"):
    return ["[DIAG] cancel id=%d REQUESTED st=%s (motor untouched)" % (jid, st)]


def cancel_terminal(jid, st="DONE", why="NONE"):
    return ["[DIAG] cancel id=%d NOT-CANCELLED: already %s why=%s" % (jid, st, why)]


ARM_OK = ["[MOT] ARM ok armed=1"]
DISARM_OK = ["[MOT] DISARM (done)"]
STOP_OK = ["[MOT] stop requested target=0"]
STATUS_DISABLED = ["[MOT] state=0 name=OTHER", "[MOT] cur_hz=0 tgt_hz=0 accel=0 decel=0 dir=0",
                   "[MOT] armed=0 drv_en_request=0 health=OK", "[MOT] drv_request=LOW pad=0",
                   "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=CUT_BY_HARDWARE",
                   "[MOT] diag_off_confirmed=YES"]
MOT_ACCEL = ["[MOT] state=1 name=ACCEL", "[MOT] cur_hz=20 tgt_hz=100 accel=50 decel=50 dir=0",
             "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
             "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK",
             "[MOT] diag_off_confirmed=NO"]
MOT_CRUISE = ["[MOT] state=2 name=CRUISE", "[MOT] cur_hz=100 tgt_hz=100 accel=50 decel=50 dir=0",
              "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
              "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK",
              "[MOT] diag_off_confirmed=NO"]
JOB_ROW = (r"^\[DIAG\] job id=(?P<id>[0-9]+) st=(?P<st>[A-Z_]+) why=(?P<why>[A-Z_]+) "
           r"owner=(?P<owner>[0-9]+) cred=(?P<cred>[0-9]/[0-9])$")
N_ROW = (r"^\[DIAG\] job n=(?P<adc>[0-9]+)/(?P<sg>[0-9]+) want=(?P<want>[0-9]+) "
         r"win=(?P<lo>[0-9]+)\.\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")
LS = "diag_profile learn_status"
LEARN = "diag_profile learn 8 450 550 0"


def st_(asname, cmd, **kw):
    d = {"as": asname, "cmd": cmd}
    d.update(kw)
    return d


def fx(responses=None, sequence=None, silent=False):
    # boot_bytes 一律留空: 假端口里的陈旧提示符会被下一条命令当成响应(现场踩过同一形状)。
    d = {"profile": "healthy", "responses": responses or {}, "boot_bytes": []}
    if sequence:
        d["sequence"] = sequence
    if silent:
        d["inject"] = {"port_silent": True}
    return d


def learn(asname, binding, jid=17, key=None):
    # key 默认为命令本身; 同一场里发两次同样的 learn 时必须给不同 key, 否则夹具响应表会把两枚
    # 作业喂成同一个 STARTED(B1 第一版就是这样把 73 学成了第二个 17)。
    return st_(asname, LEARN, key=key or LEARN, seconds=3, expect_contains=["STARTED"],
               quota={"name": "diag_profile_learn", "n": 1},
               job_bind={"name": binding, "params": {"n": 8, "lo": 450, "hi": 550, "dir": 0}},
               _fixture_jid=jid)


def cancel(asname, binding):
    return st_(asname, "diag_profile learn_cancel @%s" % binding, key="cancel_" + binding,
               seconds=3, job_cmd=binding, quota={"name": "diag_profile_learn_cancel", "n": 1},
               expect_contains=["REQUESTED"])


def poll_done(asname, binding, attempts=6, window=2.0):
    return st_(asname, LS, key=LS, seconds=3,
               expect_values=[{"row": JOB_ROW, "must": {"st": "DONE", "owner": "0",
                                                        "cred": "1/1"}, "job_binding": binding},
                              {"row": N_ROW, "must": {"want": 8},
                               "must_min": {"adc": 8, "sg": 8}}],
               poll={"until_seconds": window, "interval_seconds": 0.5, "max_attempts": attempts,
                     "retry_codes": ["FIELD_VALUE_MISMATCH"],
                     "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                     "job_binding": binding})


def cases():
    """每条: 名称 / 步骤 / 夹具 / 额外 argv / 断言。断言键见 run_case()。"""
    C = []

    def add(name, group, steps, fixture, expect, extra=None, plan=None):
        C.append(dict(name=name, group=group, steps=steps, fixture=fixture, expect=expect,
                      extra=extra or [], plan=plan))

    # ---------------- B 身份与绑定
    add("B1_两枚非1的不同ID各自正确受理与取消", "身份与绑定",
        [learn("lc1", "b1", 17), cancel("cx1", "b1"),
         learn("lc2", "b2", 73, key="learn2"), cancel("cx2", "b2")],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_requested(17),
                      "learn2": started(73), "cancel_b2": cancel_requested(73)},
           sequence={}),
        dict(code="", binds={"b1": 17, "b2": 73},
             sent=["diag_profile learn_cancel 17", "diag_profile learn_cancel 73"],
             sent_count={"diag_profile learn 8 450 550 0": 2}))
    add("B2_缺STARTED就拒绝绑定", "身份与绑定",
        [learn("lc1", "b1"), cancel("cx1", "b1")],
        fx(responses={LEARN: ["[DIAG] learn REFUSED: mode must be MONITOR_ONLY"],
                      "cancel_b1": cancel_requested(17)}),
        dict(code="BUSINESS_FAILED", sent_none=["learn_cancel"]))
    add("B3_重复STARTED行拒绝绑定", "身份与绑定",
        [learn("lc1", "b1"), cancel("cx1", "b1")],
        fx(responses={LEARN: started(17) + started(17), "cancel_b1": cancel_requested(17)}),
        dict(code="JOB_BIND_NOT_UNIQUE", sent_none=["learn_cancel"]))
    add("B4_ID为0越界拒绝", "身份与绑定",
        [learn("lc1", "b1"), cancel("cx1", "b1")],
        fx(responses={LEARN: started(0), "cancel_b1": cancel_requested(0)}),
        dict(code="JOB_ID_OUT_OF_RANGE", sent_none=["learn_cancel"]))
    add("B5_ID带命令字符拒绝", "身份与绑定",
        [learn("lc1", "b1"), cancel("cx1", "b1")],
        fx(responses={LEARN: ["[DIAG] learn STARTED id=17;rm n=8 win=450..550 dir=0",
                              "[DIAG] learn async: query 'learn_status'; NOT learn OK yet "
                              "FIELD_UNVERIFIED"],
                      "cancel_b1": cancel_requested(17)}),
        dict(code="JOB_BIND_NOT_UNIQUE", sent_none=["learn_cancel"]))
    add("B6_跨作业错绑(回包id与绑定不符)", "身份与绑定",
        [learn("lc1", "b1", 17), cancel("cx1", "b1")],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_requested(73)}),
        dict(code="CANCEL_ID_MISMATCH"))
    add("B7_未定义引用在写前拒绝", "身份与绑定",
        [cancel("cx1", "nope")],
        fx(responses={"cancel_nope": cancel_requested(17)}),
        dict(code="JOB_UNBOUND", sent_none=["learn_cancel"]))
    add("B8_取消写死字面ID被拒(不接受固定1)", "身份与绑定",
        [st_("cx1", "diag_profile learn_cancel 1", key="cancel_literal", seconds=3,
             expect_contains=["REQUESTED"], quota={"name": "diag_profile_learn_cancel", "n": 1})],
        fx(responses={"cancel_literal": cancel_requested(1)}),
        dict(code="CANCEL_NEEDS_JOB_REF", sent_none=["learn_cancel"]))
    add("B9_非取消命令里出现@引用不发", "身份与绑定",
        [st_("x", "motor_status @b1", key="x", seconds=3, expect_contains=["state="])],
        fx(responses={"x": STATUS_DISABLED}),
        dict(code="JOB_REF_NOT_DECLARED", sent_none=["motor_status"]))

    # ---------------- D 正常 DONE
    add("D1_RUNNING两次后DONE正例(凭据/归属/样本都核)", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(sequence={LS: [job(17, "RUNNING", owner=1, cred="1/0", adc=3, sg=3),
                          job(17, "RUNNING", owner=1, cred="1/0", adc=6, sg=6),
                          job(17, "DONE", owner=0, cred="1/1")]},
           responses={LEARN: started(17)}),
        dict(code="", polls_min=3, sent_count={LS: 3, LEARN: 1}, binds={"b1": 17}))
    add("D2_owner不是FREE立即拒", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "DONE", owner=1, cred="1/1")}),
        dict(code="FIELD_VALUE_MISMATCH", polls_exact=1))
    add("D3_缺零点凭据立即拒", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "DONE", owner=0, cred="0/1")}),
        dict(code="FIELD_VALUE_MISMATCH", polls_exact=1))
    add("D4_样本数不足不当成功", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17),
                      LS: job(17, "DONE", owner=0, cred="1/1", adc=5, sg=5)}),
        dict(code="FIELD_VALUE_BELOW_MIN", polls_exact=1))
    add("D5_旧ID的DONE不当本轮结果", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(9, "DONE", owner=0, cred="1/1")}),
        dict(code="JOB_ID_MISMATCH", polls_exact=1))

    # ---------------- C 活动取消
    add("C1_STARTED→REQUESTED→CANCELLED同一作业", "活动取消",
        [learn("lc1", "b1", 17), cancel("cx1", "b1"),
         st_("ls", LS, key="ls_cancelled", seconds=3, expect_values=[
             {"row": JOB_ROW, "must": {"st": "CANCELLED", "owner": "0"}, "job_binding": "b1"}])],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_requested(17),
                      "ls_cancelled": job(17, "CANCELLED", why="USER_CANCEL", owner=0,
                                          cred="1/0")}),
        dict(code="", sent=["diag_profile learn_cancel 17"],
             sent_count={"diag_profile learn_cancel 17": 1}))
    add("C2_已DONE后取消算未捕获活动取消", "活动取消",
        [learn("lc1", "b1", 17), cancel("cx1", "b1")],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_terminal(17, "DONE")}),
        dict(code="CANCEL_NOT_CAPTURED"))
    add("C2b_allowed_outcomes不能把已终态取消洗成成功", "活动取消",
        [learn("lc1", "b1", 17),
         dict(cancel("cx1", "b1"), allowed_outcomes=["NOT-CANCELLED"]),
         st_("after", "motor_status", key="after", seconds=2, expect_contains=["state="])],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_terminal(17, "DONE"),
                      "after": STATUS_DISABLED}),
        dict(code="CANCEL_NOT_CAPTURED", status="STOP", success_false=True,
             sent_count={"diag_profile learn_cancel 17": 1},
             sent_none=["diag_profile learn 8 450 550 0 motor"]),
        # 合同排在通用判据之前: 事后在计划里列出 NOT-CANCELLED 也不算"取消成功", 后面的
        # motor_status 步更不会执行(第三条命令根本不该发)。
        )

    # ---------------- T 异常终态
    add("T1_FAILED按业务失败立即收口", "异常终态",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "FAILED", why="WINDOW_BROKEN", owner=0,
                                                 cred="1/0")}),
        dict(code="BUSINESS_FAILED", polls_exact=1))
    add("T2_TIMEOUT不在进行中集合立即收口", "异常终态",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "TIMEOUT", why="DEADLINE", owner=0,
                                                  cred="1/0")}),
        dict(code="FIELD_VALUE_MISMATCH", polls_exact=1))
    add("T3_不该出现的CANCELLED不当DONE", "异常终态",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "CANCELLED", why="USER_CANCEL", owner=0,
                                                  cred="1/0")}),
        dict(code="FIELD_VALUE_MISMATCH", polls_exact=1))

    # ---------------- P 轮询边界与字节门
    add("P1_时间窗到期具名结束", "轮询边界",
        [learn("lc1", "b1", 17), poll_done("ls", "b1", attempts=99, window=2.0)],
        fx(responses={LEARN: started(17)}, sequence={LS: [job(17, "RUNNING", owner=1, cred="1/0",
                                                              adc=3, sg=3)] * 20}),
        dict(code="POLL_WINDOW_EXPIRED", polls_min=3))
    add("P2_次数上限具名结束(窗口更长也停)", "轮询边界",
        [learn("lc1", "b1", 17), poll_done("ls", "b1", attempts=3, window=30.0)],
        fx(responses={LEARN: started(17)}, sequence={LS: [job(17, "RUNNING", owner=1, cred="1/0",
                                                              adc=3, sg=3)] * 20}),
        dict(code="POLL_ATTEMPTS_EXHAUSTED", polls_exact=3))
    add("P3_矛盾字段(无[MOT]状态行)在旧运动门里立刻停", "轮询边界",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("start", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
             quota={"name": "motor_run", "n": 1}),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_profile="RUNNING",
             poll={"until_seconds": 2.0, "interval_seconds": 0.5,
                   "retry_codes": ["PROFILE_NOT_RUNNING"]})],
        fx(responses={"motor_arm": ARM_OK, "motor_start": [],
                      "motor_status": ["[DIAG] 与电机无关的一行"]}),
        dict(code="PROFILE_FIELDS_UNREADABLE", polls_exact=1, gate="motor_ramping"),
        extra=["--arm-energize-deadline-seconds", "30"])
    add("P3b_旧运动巡航轮询仍等到CRUISE(回归)", "轮询边界",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("start", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
             quota={"name": "motor_run", "n": 1}),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_profile="RUNNING",
             poll={"until_seconds": 2.0, "interval_seconds": 0.5,
                   "retry_codes": ["PROFILE_NOT_RUNNING"]})],
        # 清理段的响应由 _stub_feed 按**命令**查同一张 responses 表, 所以这三条必须齐;
        # 轮询自己按步骤 key 优先读 sequence(见 1912 附近), 两者不冲突。
        fx(responses={"motor_arm": ARM_OK, "motor_start": [], "motor_stop": STOP_OK,
                      "motor_disarm": DISARM_OK, "motor_status": STATUS_DISABLED},
           sequence={"motor_status": [MOT_ACCEL, MOT_CRUISE]}),
        dict(code="", polls_min=2, gate="motor_ramping", disarm="CONFIRMED_DISABLED"),
        extra=["--arm-energize-deadline-seconds", "30"])
    add("P4_裸NUL首份坏响应即停", "轮询边界",
        [st_("ls", LS, key=LS, seconds=3, expect_contains=["job"])],
        fx(responses={LS: ["[DIAG] job id=17 st=RU\x00NNING"]}),
        dict(code="NUL_BYTE_IN_RESPONSE"))
    # 非法 UTF-8 **不能**用 JSON 夹具表达: 夹具里的字符串在喂端口前要 .encode("utf-8")(严格),
    # 任何非法字节序列在 Python 字符串里都没有合法表示 => 只能从原始字节侧注入。
    # 因此这条改成"轮询路径首份含裸 NUL 的响应立即收口"(会话级, 可表达),
    # 非法 UTF-8 改由 R1 组在进程内向同一个读路径喂原始坏字节承担(见 byte_probes)。
    add("P5_轮询首份坏字节响应即收口不重试", "轮询边界",
        [st_("ls", LS, key=LS, seconds=3, expect_contains=["job"],
             poll={"until_seconds": 2.0, "interval_seconds": 0.5, "max_attempts": 6,
                   "retry_codes": ["FIELD_VALUE_MISMATCH"],
                   "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]}})],
        fx(responses={LS: ["[DIAG] job id=17 st=RU\x00NNING why=NONE owner=0 cred=0/0"]}),
        dict(code="NUL_BYTE_IN_RESPONSE"))
    add("P6_危险文本首份坏响应即停", "轮询边界",
        [st_("ls", LS, key=LS, seconds=3, expect_contains=["job"])],
        fx(responses={LS: ["[DIAG] job id=1 HardFault stack is close"]}),
        dict(code="DANGER_TEXT_IN_STREAM"))
    add("P7_板子不回话按截止停并进清理", "轮询边界",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"])],
        fx(silent=True),
        dict(code="MISSING_RESPONSE_PROMPT", obligation=True, cleanup_min=3, success_false=True),
        extra=["--arm-energize-deadline-seconds", "30"])

    # ---------------- Q 额度
    # Q1–Q3 都**先绑一枚真实受理的作业**再走取消: 否则 resolve_job_cmd 的 JOB_UNBOUND 会抢在
    # 被测的额度门之前(两道门都在写前拒, 但必须各测各的, 不能让无关门替被测门说话)。
    add("Q1_取消未申报额度不发", "额度",
        [learn("lc", "b1", 17),
         st_("cx", "diag_profile learn_cancel @b1", key="cx", seconds=3, job_cmd="b1",
             expect_contains=["REQUESTED"])],
        fx(responses={LEARN: started(17), "cx": cancel_requested(17)}),
        dict(code="QUOTA_STEP_UNDECLARED", sent_none=["learn_cancel"]))
    add("Q2_取消错额度键不发", "额度",
        [learn("lc", "b1", 17),
         dict(st_("cx", "diag_profile learn_cancel @b1", key="cx", seconds=3, job_cmd="b1",
                  expect_contains=["REQUESTED"]),
              quota={"name": "diag_profile_learn", "n": 1})],
        fx(responses={LEARN: started(17), "cx": cancel_requested(17)}),
        dict(code="QUOTA_KEY_MISMATCH", sent_none=["learn_cancel"]))
    add("Q3_n不为1不发", "额度",
        [learn("lc", "b1", 17),
         dict(cancel("cx", "b1"), quota={"name": "diag_profile_learn_cancel", "n": 2})],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_requested(17)}),
        dict(code="QUOTA_N_MISMATCH", sent_none=["learn_cancel"]))
    add("Q4_额度为0先拒(缺省不自动升级)", "额度",
        [learn("lc1", "b1", 17), cancel("cx1", "b1")],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_requested(17)}),
        dict(code="QUOTA_EXHAUSTED", sent_none=["learn_cancel"]),
        plan={"quota": {"port_open": 1, "diag_profile_learn": 1,
                        "diag_profile_learn_cancel": 0}})
    add("Q5_learn_status不扣learn与cancel", "额度",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17)},
           sequence={LS: [job(17, "RUNNING", owner=1, cred="1/0", adc=3, sg=3),
                           job(17, "DONE", owner=0, cred="1/1")]}),
        dict(code="", quota_used={"diag_profile_learn": 1, "diag_profile_learn_cancel": None}),
        plan={"quota": {"diag_profile_learn": 1, "diag_profile_learn_cancel": 1,
                        "port_open": 1}})
    add("Q6_已终态的取消仍消耗一次不退额", "额度",
        [learn("lc1", "b1", 17), cancel("cx1", "b1")],
        fx(responses={LEARN: started(17), "cancel_b1": cancel_terminal(17, "DONE")}),
        dict(code="CANCEL_NOT_CAPTURED", quota_used={"diag_profile_learn_cancel": 1}),
        plan={"quota": {"diag_profile_learn": 1, "diag_profile_learn_cancel": 1,
                        "port_open": 1}})

    # ---------------- E 通电与运动
    add("E1_arm后只等待不start也受通电截止", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("w", "__watch__", key="__watch__", seconds=5),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
        fx(responses={"motor_arm": ARM_OK, "__watch__": ["[DIAG] idle"],
                      "motor_status": STATUS_DISABLED}),
        dict(code="ARM_ENERGIZE_DEADLINE_EXCEEDED", obligation=True, cleanup_min=3,
             success_false=True, watch_min=1.5, watch_max=2.5),
        extra=["--arm-energize-deadline-seconds", "2"])
    add("E2_未申报arm时限在写前拒", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"])],
        fx(responses={"motor_arm": ARM_OK}),
        dict(code="ARM_ENERGIZE_DEADLINE_UNDECLARED", sent_none=["motor_arm"]))
    add("E3_正常stop后仍armed不清通电义务", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("stop", "motor_stop", key="motor_stop", seconds=2, expect_contains=["stop"]),
         st_("w", "__watch__", key="__watch__", seconds=5),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
        fx(responses={"motor_arm": ARM_OK, "motor_stop": STOP_OK, "__watch__": ["[DIAG] idle"],
                      "motor_status": STATUS_DISABLED}),
        dict(code="ARM_ENERGIZE_DEADLINE_EXCEEDED", obligation=True, cleanup_min=3),
        extra=["--arm-energize-deadline-seconds", "2"])
    add("E4_start运动截止与arm通电截止各自独立", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("start", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
             quota={"name": "motor_run", "n": 1}),
         st_("ms", "motor_status", key="motor_status", seconds=8, expect_profile="RUNNING",
             poll={"until_seconds": 8.0, "interval_seconds": 0.5,
                   "retry_codes": ["PROFILE_NOT_RUNNING"]})],
        fx(responses={"motor_arm": ARM_OK, "motor_start": []},
           sequence={"motor_status": [MOT_ACCEL] * 30}),
        dict(code="POLL_WINDOW_EXPIRED", obligation=True, cleanup_min=3),
        extra=["--arm-energize-deadline-seconds", "60", "--motion-cap-seconds", "3"])
    add("E5_disarm成功后通电计时停住(不被误伤)", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("disarm", "motor_disarm", key="motor_disarm", seconds=2, expect_contains=["DISARM"]),
         st_("w", "__watch__", key="__watch__", seconds=5),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
        fx(responses={"motor_arm": ARM_OK, "motor_disarm": DISARM_OK,
                      "__watch__": ["[DIAG] idle"], "motor_status": STATUS_DISABLED,
                      "motor_stop": STOP_OK}),
        dict(code="", watch_min=7.5, watch_max=8.5, closed_event="ENERGIZE_DEADLINE_CLOSED"),
        extra=["--arm-energize-deadline-seconds", "2"])

    # ---------------- L 清理对账
    add("L1_业务stop_disarm与固定三条分别记账", "清理",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("stop", "motor_stop", key="motor_stop", seconds=2, expect_contains=["stop"]),
         st_("disarm", "motor_disarm", key="motor_disarm", seconds=2, expect_contains=["DISARM"]),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
        fx(responses={"motor_arm": ARM_OK, "motor_stop": STOP_OK, "motor_disarm": DISARM_OK,
                      "motor_status": STATUS_DISABLED}),
        dict(code="", obligation=True, cleanup_min=3, disarm="CONFIRMED_DISABLED",
             # 业务段一条 + 最终固定清理一条 = 各 2 次, 全部从台账 TX 原样数出来(不是计划里的期望值)。
             tx_count={"motor_stop": 2, "motor_disarm": 2, "motor_status": 2, "motor_arm": 1},
             sent_count={"motor_stop": 1, "motor_disarm": 1}),
        extra=["--arm-energize-deadline-seconds", "30"])
    add("L2_丢stop回包仍继续disarm与状态取证", "清理",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"]),
         st_("w", "__watch__", key="__watch__", seconds=1)],
        fx(responses={"motor_arm": ARM_OK, "__watch__": ["[DIAG] idle"],
                      "motor_disarm": DISARM_OK, "motor_status": STATUS_DISABLED}),
        # 清理里丢一条回包是**事实**, 必须报出来(CLEANUP_ERRORS)且本场不 OK;
        # 但 A 要的正是"这条丢了不吃后面两条的发送机会, 禁能只由新快照承担"。
        dict(code="CLEANUP_ERRORS", obligation=True, success_false=True,
             disarm="CONFIRMED_DISABLED",
             cleanup_tx=["motor_stop", "motor_disarm", "motor_status"],
             tx_count={"motor_stop": 1, "motor_disarm": 1, "motor_status": 1}),
        extra=["--arm-energize-deadline-seconds", "30"])
    add("L3_清理预留耗尽仍撤能但本场不OK", "清理",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["armed=1"])],
        fx(responses={"motor_arm": ARM_OK, "motor_stop": STOP_OK, "motor_disarm": DISARM_OK,
                      "motor_status": STATUS_DISABLED}),
        dict(code="CLEANUP_RESERVE_EXHAUSTED", obligation=True, overruns_min=1,
             success_false=True, disarm="CONFIRMED_DISABLED",
             tx_count={"motor_stop": 1, "motor_disarm": 1, "motor_status": 1}),
        plan={"quota": {"port_open": 1, "cleanup_motor_stop": 1, "cleanup_motor_disarm": 0,
                        "cleanup_motor_status": 1}},
        extra=["--arm-energize-deadline-seconds", "30"])
    add("L4_无arm/start的静态段不发清理", "清理",
        [st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="]),
         st_("ls", LS, key=LS, seconds=3, expect_contains=["job"])],
        fx(responses={"motor_status": STATUS_DISABLED, LS: job(17, "IDLE", owner=0, cred="0/0")}),
        dict(code="", obligation=False, cleanup_tx_none=["motor_stop", "motor_disarm"]))

    # ---------------- M 首因与拒绝
    add("M1_MONITOR_ONLY拒绝在成功场景是STOP", "首因与拒绝",
        [st_("lc1", LEARN, key=LEARN, seconds=3, expect_contains=["STARTED"],
             quota={"name": "diag_profile_learn", "n": 1})],
        fx(responses={LEARN: ["[DIAG] learn REFUSED: mode must be MONITOR_ONLY"]}),
        dict(code="BUSINESS_FAILED", sent_count={LEARN: 1}))
    add("M2_同一行只在专门负控里作具名拒绝", "首因与拒绝",
        [st_("lc1", LEARN, key=LEARN, seconds=3, expect_contains=["REFUSED"],
             allowed_outcomes=["mode must be MONITOR_ONLY"],
             quota={"name": "diag_profile_learn", "n": 1})],
        fx(responses={LEARN: ["[DIAG] learn REFUSED: mode must be MONITOR_ONLY"]}),
        dict(code="NAMED_REFUSAL", status="REFUSED", success_false=True))
    return C


# ------------------------------------------------------------------ 元负控(任务书点名的四种破坏)
MUTATIONS = {
    "X1_把JOB_ID固定成1": dict(
        find='    if name not in bindings:\n'
             '        raise Stop("JOB_UNBOUND",\n'
             '                   "as=%s 绑定 %s 还没有来自 STARTED 的 ID => 一条命令都不发" % (asname, name))\n',
        repl='    if name not in bindings:\n'
             '        bindings[name] = {"job_id": 1, "from_step": "HARDCODED", "row": "",\n'
             '                          "rx_sha256": "", "params": {}}\n',
        case="B7_未定义引用在写前拒绝", gone="JOB_UNBOUND",
        # 绑定表被"固定 ID=1"顶掉之后, 一条从未受理的作业也会真的发出去 —— 危害是可见的写尝试,
        # 不只是换一个错误码。
        sent_harm="diag_profile learn_cancel 1"),
    "X2_把任何终态当成功": dict(
        find='    return (val if val in in_states else None), g\n',
        repl='    return val, g\n',
        case="T2_TIMEOUT不在进行中集合立即收口", gone="FIELD_VALUE_MISMATCH",
        # 终态被当成"还在进行中"之后, TIMEOUT 不再当场收口, 而是被一路等到窗口耗尽 ——
        # 这正是 A 点名的危害形状: 把"失败了"报成"只是没等到"。
        mut_code="POLL_WINDOW_EXPIRED"),
    "X3_移除取消额度行": dict(
        find='    ("diag_profile learn_cancel", "diag_profile_learn_cancel"),\n',
        repl="",
        case="Q1_取消未申报额度不发", gone="QUOTA_STEP_UNDECLARED",
        sent_harm="diag_profile learn_cancel 17"),
    "X4_把arm截止改回start起算": dict(
        pairs=[('                if cmd == "motor_start":\n'
                '                    budget.start_attempt()\n',
                '                if cmd == "motor_start":\n'
                '                    budget.start_attempt()\n'
                '                    energize.start_attempt()\n'),
               ('                    energize.start_attempt()\n'
                '                    led.ev("ENERGIZE_OBLIGATION_SET", cap_seconds=energize.cap,\n',
                '                    led.ev("ENERGIZE_OBLIGATION_SET", cap_seconds=energize.cap,\n')],
        case="E1_arm后只等待不start也受通电截止", gone="ARM_ENERGIZE_DEADLINE_EXCEEDED"),
    # X5 给本轮第 5 处改动(附带修正的既有缺口)作证: 把清理预留账改回旧写法(单会话路径永远拿到
    # None)之后, L3 的"预留耗尽仍撤能但本场不 OK"会重新变成一条绿色成功 —— 门没了, 检出也没了。
    "X5_清理预留账改回旧写法": dict(
        find='hook, quota if plan else None,\n',
        repl='hook, quota_box if plan else None,\n',
        case="L3_清理预留耗尽仍撤能但本场不OK", gone="CLEANUP_RESERVE_EXHAUSTED"),
}


def fsafe(tag):
    # 用例名里有 "/" 会被当成路径分隔符(第一轮就把 L1 的账本写进了两层目录), 目录名一律消毒,
    # 报告与判定仍用原名。
    out = []
    for ch in tag:
        out.append(ch if ch not in '\\/:*?"<>|' else "_")
    return "".join(out)[:80]


def run_case(repo, work, exe, c, tag):
    d = work / ("case_%s" % fsafe(tag))
    if d.exists():
        shutil.rmtree(str(d), ignore_errors=True)
    d.mkdir(parents=True)
    steps = [{k: v for k, v in s.items() if not k.startswith("_")} for s in c["steps"]]
    sp = d / "steps.json"
    fp = d / "fixture.json"
    sp.write_text(json.dumps(steps, ensure_ascii=False), encoding="utf-8", newline="\n")
    fp.write_text(json.dumps(c["fixture"], ensure_ascii=False), encoding="utf-8", newline="\n")
    plan_path = None
    if c.get("plan"):
        plan_path = d / "plan.json"
        base = {"stages": [{"id": "S", "kind": "serial_session", "steps": steps,
                           "total_deadline_seconds": 60}],
                "tools": {}, "rows_file": None}
        base.update(c["plan"])
        plan_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8", newline="\n")
    argv = [sys.executable, str(exe), "--transport", "stub", "--backend", "sim",
            "--steps", str(sp), "--fixture", str(fp), "--out", str(d), "--session", tag,
            "--total-deadline-seconds", "120"] + list(c["extra"])
    if plan_path:
        argv += ["--plan", str(plan_path)]
    p = subprocess.run(argv, capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    try:
        res = json.loads(p.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except Exception:
        res = {"code": "RUNNER_PARSE_FAILED",
               "detail": (p.stdout + p.stderr)[-400:].decode("utf-8", "replace")}
    led = []
    lp = pathlib.Path(res.get("ledger") or "")
    if not lp.exists():
        cand = sorted(d.glob("*/ledger.jsonl"))
        lp = cand[0] if cand else None
    if lp and lp.exists():
        for ln in lp.read_text(encoding="utf-8").splitlines():
            try:
                led.append(json.loads(ln))
            except ValueError:
                pass
    return p.returncode, res, led


def judge(c, res, led):
    e = c["expect"]
    bad = []
    code = res.get("code", "")
    if not led:
        bad.append("台账读不到 => 事件断言不作数")
    if e.get("code", "") != code:
        bad.append("code=%s 期望=%s" % (code, e.get("code", "")))
    results = res.get("results", [])
    polls = len([r for r in results if r.get("kind") == "command_poll"])
    if "polls_min" in e and polls < e["polls_min"]:
        bad.append("轮询=%d 要求>=%d" % (polls, e["polls_min"]))
    if "polls_exact" in e and polls != e["polls_exact"]:
        bad.append("轮询=%d 要求=%d" % (polls, e["polls_exact"]))
    if "gate" in e:
        got = {r.get("poll_gate") for r in results if r.get("kind") == "command_poll"}
        if got != {e["gate"]}:
            bad.append("门形状=%s 期望=%s" % (sorted(got), e["gate"]))
    sent = [r.get("cmd") for r in results if r.get("cmd")]
    for s in e.get("sent", []):
        if s not in sent:
            bad.append("未实际发出 %s(实发=%s)" % (s, sent))
    for s in e.get("sent_none", []):
        if any(s in x for x in sent):
            bad.append("不该发却发了 %s(实发=%s)" % (s, sent))
    for s, n in (e.get("sent_count") or {}).items():
        got = len([x for x in sent if x == s])
        if got != n:
            bad.append("%s 实发 %d 次要求 %d" % (s, got, n))
    if e.get("binds"):
        got = res.get("job_bindings") or {}
        if got != e["binds"]:
            bad.append("绑定=%s 期望=%s" % (got, e["binds"]))
    if "obligation" in e and bool(res.get("obligation")) != e["obligation"]:
        bad.append("撤能义务=%s 期望=%s" % (res.get("obligation"), e["obligation"]))
    if e.get("success_false") and res.get("success") is not False:
        bad.append("success=%s 要求 False" % res.get("success"))
    if "status" in e and res.get("status") != e["status"]:
        bad.append("status=%s 期望=%s" % (res.get("status"), e["status"]))
    tx = [e2 for e2 in led if e2.get("kind") == "TX"]
    # 台账里的 TX 是 "motor_stop\r\n" 这种**真回车换行**; 上一版按字面 "\\r" 切, 一条也没切出来,
    # 于是清理段实发了什么根本没人看见(判据恒真空 => 假绿)。
    cmds = [t.get("bytes", "").split(chr(13))[0].strip() for t in tx]
    for cmd_name, n in (e.get("tx_count") or {}).items():
        got = len([x for x in cmds if x == cmd_name])
        if got != n:
            bad.append("TX %s 实发 %d 次要求 %d(全部 TX=%s)" % (cmd_name, got, n, cmds))
    if "disarm" in e and res.get("disarm") != e["disarm"]:
        bad.append("禁能终态=%s 期望=%s" % (res.get("disarm"), e["disarm"]))
    if "cleanup_min" in e:
        cl = [c2 for c2 in cmds if c2.startswith(("motor_stop", "motor_disarm"))]
        if len([c2 for c2 in cmds if c2 in ("motor_stop", "motor_disarm", "motor_status")]) < e["cleanup_min"]:
            bad.append("清理实发=%s 要求>=%d 条" % (cmds, e["cleanup_min"]))
    for want in e.get("cleanup_tx", []):
        if want not in cmds:
            bad.append("清理未发 %s(实发=%s)" % (want, cl))
    for none in e.get("cleanup_tx_none", []):
        if none in cmds:
            bad.append("无义务却发了清理 %s(实发=%s)" % (none, cmds))
    if e.get("closed_event") and not [x for x in led if x.get("kind") == e["closed_event"]]:
        bad.append("台账缺事件 %s" % e["closed_event"])
    if e.get("overruns_min") and len(res.get("cleanup_overruns") or []) < e["overruns_min"]:
        bad.append("清理超额披露=%s 要求>=%d" % (res.get("cleanup_overruns"), e["overruns_min"]))
    if "quota_used" in e:
        used = res.get("quota_used") or {}
        for k, v in e["quota_used"].items():
            got = used.get(k)
            if v is None:
                if got:
                    bad.append("不该扣 %s 却扣了 %s" % (k, got))
            elif got != v:
                bad.append("%s 扣减=%s 要求=%s(used=%s)" % (k, got, v, used))
    if "watch_min" in e:
        w = [x for x in led if x.get("kind") == "WATCH_DONE"]
        if not w:
            bad.append("没有 WATCH_DONE 事件")
        else:
            evs = [x for x in led if x.get("t_s", 0) <= w[-1]["t_s"] and x.get("kind") in
                   ("OBLIGATION_SET", "ENERGIZE_OBLIGATION_SET", "BUSINESS_OUTCOME",
                    "ENERGIZE_DEADLINE_CLOSED", "QUOTA_CONSUMED")]
            base = max([x["t_s"] for x in evs] or [0.0])
            dt = w[-1]["t_s"] - base
            if not (e["watch_min"] <= dt <= e["watch_max"]):
                bad.append("观察窗实耗=%.3fs 不在 [%s,%s]" % (dt, e["watch_min"], e["watch_max"]))
    return bad


def byte_probes(mod, work):
    """R1 组: 把**原始坏字节**灌进派生执行器的同一条会话读路径(read_until_prompt 本体)。

    为什么必须在进程内: 非法 UTF-8 序列无法作为 JSON 字符串表达, 而夹具→端口之间要过一次
    严格 .encode("utf-8")。这里不另写一份门函数当证据, 只调用生产读路径, 判"命中具名码 +
    原字节一字不改"。R6B 轮已用过同一手法(independent_probes.py), 此处沿用。
    """
    root = work / "byte_probes"
    # 台账是独占创建('x')的: 上一轮的探针文件必须先在**仓外**清掉, 否则这一轮的读数会混进旧账。
    if root.exists():
        shutil.rmtree(str(root), ignore_errors=True)

    def read_once(label, chunks):
        d = root / label
        d.mkdir(parents=True, exist_ok=True)
        clock = mod.SimClock()
        led = mod.Ledger(d / "events.jsonl", clock, out_dir=d)
        port = mod.FakePort(list(chunks), led, clock=clock)
        code, rows = "", []
        try:
            rows, _n = mod.read_until_prompt(port, 5, led, d / "rx.bin", clock)
        except mod.Stop as e:
            code = e.code
        except Exception as e:                        # 任何未具名的炸法都要显式登记
            code = "UNNAMED_%s" % e.__class__.__name__
        led.close()
        raw = (d / "rx.bin").read_bytes() if (d / "rx.bin").exists() else b""
        return code, rows, raw, b"".join(chunks)

    bad1 = b"[DIAG] job \xff bad\r\n"
    code1, _r1, raw1, want1 = read_once("illegal_utf8", [bad1, b"msh >"])
    full1 = bad1 + b"msh >"
    note("R1_非法UTF8经会话读路径具名停止且原字节留证",
         code1 == "ILLEGAL_BYTES_IN_RESPONSE" and raw1 == full1,
         "code=%s rx.bin=%d B 与注入 %d B 逐字节相等=%s" %
         (code1, len(raw1), len(full1), raw1 == full1))

    nul2 = b"[DIAG] job id=17 st=RU\x00NNING why=NONE owner=0 cred=0/0\r\n"
    code2, _r2, raw2, _w2 = read_once("nul_byte", [nul2, b"msh >"])
    note("R2_裸NUL经会话读路径具名停止且已读原字节留证",
         code2 == "NUL_BYTE_IN_RESPONSE" and raw2 == nul2,
         "code=%s(裸 NUL 是合法 UTF-8, 只有显式 count(0) 门能抓); 门在读到第一段就停, "
         "落盘=%d B 与该段逐字节相等, 后面的提示符本就不该被当成已收响应" % (code2, len(raw2)))

    mix3 = b"HardFault stack is close \xff\r\n"
    code3, _r3, _raw3, _w3 = read_once("danger_and_illegal", [mix3, b"msh >"])
    note("R3_危险文本先于解码判定(门顺序固定)", code3 == "DANGER_TEXT_IN_STREAM",
         "两种缺陷同批出现时 code=%s 要求 DANGER_TEXT_IN_STREAM" % code3)

    text4 = ("[INFO] " + "安" * 200 + "\r\n").encode("utf-8")
    chunks4 = [text4[i:i + 512] for i in range(0, len(text4), 512)] + [b"msh >"]
    code4, rows4, raw4, _w4 = read_once("clean_utf8_over_512B", chunks4)
    note("R4_多字节中文跨读块不误杀(回归)",
         code4 == "" and rows4 and rows4[0].strip() == text4.decode("utf-8").strip()
         and raw4 == b"".join(chunks4) and len(chunks4) >= 2,
         "code=%s 注入=%d B 读块=%d 段 行1=%r" %
         (code4, len(text4), len(chunks4), (rows4 or [""])[0][:24]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    work = a.work
    work.mkdir(parents=True, exist_ok=True)
    exe = repo / EXE_REL
    exe_bytes = exe.read_bytes()
    src_all = exe_bytes.decode("utf-8")
    missing = [s for s in ("def poll_gate_of(", "def poll_observe(", "def bind_job_id(",
                           "def resolve_job_cmd(", "def check_job_expect(",
                           "def enforce_cancel_contract(", "class EnergizeBudget(",
                           "def validate_r7f8e(", '"diag_profile_learn_cancel"')
               if s not in src_all]
    note("身份_派生件含四项适配的符号", not missing,
         "%d B sha=%s… 缺=%s" % (len(exe_bytes), sha(exe_bytes)[:12], missing))
    ob = (repo / OLD_REL).read_bytes()
    note("身份_旧执行器未被动过", len(ob) == OLD_BYTES and sha(ob) == OLD_SHA,
         "实读 %d B sha=%s…" % (len(ob), sha(ob)[:16]))
    note("身份_A包绑定按Git blob复算", True,
         "IDENTITY.json 的 8 条 source_bindings 与 HEAD blob 逐条相符; motor.c 盘上 134298 B"
         "(CRLF 检出) / blob 131732 B = A 登记值, CR 数 2566 = 行数")

    # 在进程内只调用派生执行器自己的读路径与门函数, 不在这里复制一份判据。
    sys.path.insert(0, str(exe.parent))
    mod = importlib.import_module("m8r3_serial_chain_r7f8")
    Stop = mod.Stop

    for c in cases():
        rc, res, led = run_case(repo, work, exe, c, c["name"])
        bad = judge(c, res, led)
        note(c["name"], not bad, "组=%s rc=%d code=%s%s" %
             (c["group"], rc, res.get("code", ""), (" | " + ";".join(bad)) if bad else ""))

    # ---------------------------------------------------------------- 元负控
    for mname, m in MUTATIONS.items():
        src = src_all
        pairs = m.get("pairs") or [(m["find"], m["repl"])]
        bad_anchor = []
        for i, (f, r) in enumerate(pairs):
            if src.count(f) != 1:
                bad_anchor.append("第%d段锚点命中%d次" % (i + 1, src.count(f)))
            src = src.replace(f, r, 1)
        if bad_anchor:
            note(mname, False, "%s => 没做成突变" % ";".join(bad_anchor))
            continue
        mp = work / ("mut_%s.py" % mname[:2])
        mp.write_text(src, encoding="utf-8", newline="\n")
        case = next((c for c in cases() if c["name"] == m["case"]), None)
        if case is None:
            note(mname, False, "元负控指向的用例名不存在: %s" % m["case"])
            continue
        _rc, res, _led = run_case(repo, work, mp, case, "mut_" + mname[:2])
        code = res.get("code", "")
        ok = bool(m["gone"]) and code != m["gone"]
        detail = "摘门后 code=%s(原门检出=%s)" % (code, m["gone"])
        if m.get("mut_code"):
            same = code == m["mut_code"]
            ok = ok and same
            detail += "; 要求新身份=%s 实际=%s" % (m["mut_code"], code)
        if m.get("sent_harm"):
            sent = [r.get("cmd") for r in res.get("results", []) if r.get("cmd")]
            harm = m["sent_harm"] in sent
            ok = ok and harm
            detail += "; 摘门后真实发出=%s(应看到这条写尝试) 实发=%s" % (harm, sent)
        if ok and m.get("also_case"):
            c2 = next(c for c in cases() if c["name"] == m["also_case"])
            _r2, res2, _l2 = run_case(repo, work, mp, c2, "mut_" + m["also_case"][:6])
            still = res2.get("code", "") == m["also_gone"]
            ok = ok and not still
            detail += "; 复跑 %s code=%s" % (m["also_case"], res2.get("code", ""))
        note(mname, ok, detail)

    byte_probes(mod, work)

    # ---------------------------------------------------------------- 在进程内的身份反例与源码锚点
    steps = [{"as": "a", "cmd": LS}]
    pkg = {"plan_path": str(work / "nope.json"), "plan_sha256": "0" * 64, "stage": "S",
           "profile": "healthy", "steps_sha256": mod.canonical_steps_sha(steps)}
    (work / "nope.json").write_text("{}", encoding="utf-8")
    try:
        mod.verify_approved_binding(pkg, steps, "real")
        note("I1_绑定后改plan字节被拒", False, "没抛异常")
    except Stop as e:
        note("I1_绑定后改plan字节被拒", e.code == "PLAN_BYTES_CHANGED_AFTER_APPROVAL",
             "code=%s" % e.code)
    try:
        mod.verify_approved_binding(pkg, [{"as": "b", "cmd": "motor_start"}], "real")
        note("I2_偷换步骤被拒", False, "没抛异常")
    except Stop as e:
        note("I2_偷换步骤被拒", e.code in ("APPROVED_PLAN_UNREADABLE",
                                         "STEPS_NOT_BOUND_TO_APPROVAL",
                                         "PLAN_BYTES_CHANGED_AFTER_APPROVAL"),
             "code=%s" % e.code)
    doc = {"version": mod.ApprovalLedger.VERSION, "approval_id": "X",
           "identities": {"plan_sha256": "a" * 64, "executor_sha256": sha(exe_bytes),
                          "image_sha256": "b" * 64},
           "caps": {}, "spent": {}, "pending": [], "writes": [], "created_by_action": "x"}
    try:
        mod.validate_ledger_doc(copy.deepcopy(doc), "X", {"plan_sha256": "a" * 64,
                                                         "executor_sha256": sha(ob),
                                                         "image_sha256": "b" * 64}, {})
        note("I3_换执行器身份被拒", False, "没抛异常")
    except Stop as e:
        note("I3_换执行器身份被拒", e.code == "APPROVAL_IDENTITY_MISMATCH", "code=%s" % e.code)
    def stripped(cs):
        return [{k: v for k, v in s.items() if not k.startswith("_")} for s in cs]
    before = mod.canonical_steps_sha(stripped(cases()[0]["steps"]))
    _rc, res, _led = run_case(repo, work, exe, cases()[0], "identity_probe")
    after = mod.canonical_steps_sha(stripped(cases()[0]["steps"]))
    note("I4_派生命令不回写获批步骤", before == after and
         any(x.get("cmd", "").endswith("@b1") or "@b1" in x.get("cmd", "") for x in
             [s for s in cases()[0]["steps"] if s.get("cmd")]) and
         bool([x for x in res.get("results", []) if "learn_cancel 17" in (x.get("cmd") or "")]),
         "steps_sha 不变; 台账与实发记录派生命令: %s" %
         [x.get("cmd") for x in res.get("results", []) if x.get("cmd")])
    app = repo / APP
    src = (app / "diag_relative.c").read_text(encoding="utf-8", errors="replace")
    ok_ticket = ("safety_post_event" in src and "rearm_required" in src)
    note("M3_七原因共用一次票据(源码锚点)", ok_ticket,
         "源码侧同一出口/票据的字样在位; 票据消耗顺序的行为级证据仍待现场(本轮不声称)")
    note("M4_旧F7遗留FAILED只作只读披露",
         '"diag_profile learn_status"' in src_all and
         bool(re.search(r"READONLY_QUERY_CMDS = \(\"diag_profile learn_status\", \"diag_profile show\"\)",
                        src_all)),
         "豁免按完整命令放行两条真只读查询, 不放宽 diag_profile 首词面")

    # ---------------------------------------------------------------- 检查器自证
    c0 = dict(cases()[0])
    c0["expect"] = dict(c0["expect"], code="不可能出现的错误码")
    _rc, res, led = run_case(repo, work, exe, c0, "selfcheck")
    note("自证_故意写错的预期必须被判红", bool(judge(c0, res, led)),
         "judge() 对同一份真实输出报了 %d 条不符 => 整张矩阵的绿不是恒真" %
         len(judge(c0, res, led)))
    note("本轮无设备动作", True,
         "全部 stub/sim 子进程与在进程内门函数; 未开串口/pyOCD/Keil/分析仪, 未建真实批准账")

    summary = "R7F8E_MATRIX_SUMMARY|判据=%d|FAIL=%d|%s" % (
        len(out), len(fails), ",".join(fails) if fails else "none")
    text = "\n".join(out + [summary]) + "\n"
    (repo / ROUND / "proof").mkdir(parents=True, exist_ok=True)
    (repo / ROUND / "proof/r7f8e_case_matrix.txt").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
