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
# R7F8F 更正(A 复审 §"附一个真实 owner=2 的正向控制"): 修前这组正向夹具把合同写错了 ——
#   * `job()` 默认 owner=1/cred=1/1, 而产品的归属枚举是 FREE=0/BY_ZERO=1/BY_JOB_2
#     (diag_relative.h:365-367), RUNNING 的合法归属只能是 BY_JOB=2 且 cred=1/0;
#   * `MOT_CRUISE` 把 expected_ENN=HIGH/ENN_fb=1 当正常使能, 而 motor.c:2491 在请求使能时
#     打的是 LOW, safety_gpio.c:296 的 CUT_BY_HARDWARE 是"要动没让动";
#   * `STATUS_DISABLED` 打 verdict=CUT_BY_HARDWARE, 与同一行里 request=LOW/pad=0 自相矛盾。
# 修前形态不删: 它们改名留在下面的 *_PRE_FIX 里, 作为 F4/F1 的**负例输入**(§3.2 "修前缺陷输入原样保留")。
def started(jid, n=8, lo=450, hi=550, d=0):
    return ["[DIAG] learn STARTED id=%d n=%d win=%d..%d dir=%d" % (jid, n, lo, hi, d),
            "[DIAG] learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED"]


def job(jid, st, why=None, owner=None, cred=None, adc=8, sg=8, want=8, lo=450, hi=550, d=0):
    """按产品状态表生成 `[DIAG] job ...` 两行(R7F8F 更正后的正向形状)。

    owner/cred/why 的合法组合直接取执行器里那份合同的同一个表, 不在夹具里另写一套数;
    显式传 owner/cred/why 就是**故意造矛盾**(反例用), 不传就得到该状态的正确合同。"""
    ok = _CONTRACT_OK[st]
    owner = ok["owner"] if owner is None else owner
    cred = ok["cred"] if cred is None else cred
    why = ok["why"] if why is None else why
    return ["[DIAG] job id=%d st=%s why=%s owner=%d cred=%s" % (jid, st, why, owner, cred),
            "[DIAG] job n=%d/%d want=%d win=%d..%d dir=%d" % (adc, sg, want, lo, hi, d)]


_CONTRACT_OK = {"RUNNING": {"owner": 2, "cred": "1/0", "why": "RUNNING"},
                "CANCEL_PENDING": {"owner": 2, "cred": "1/0", "why": "CANCEL_REQUESTED"},
                "DONE": {"owner": 0, "cred": "1/1", "why": "COMPLETE"},
                "CANCELLED": {"owner": 0, "cred": "1/0", "why": "CANCELLED"},
                # 失败/超时/空闲也各有产品里真打得出的一组值(diag_relative.c:1745/:1732/:1409),
                # 这样夹具不会为了造反例被迫把没定义过的字段留成 "?"; 执行器对这三条一律当首错停。
                "FAILED": {"owner": 0, "cred": "1/0", "why": "WINDOW_BROKEN"},
                "TIMEOUT": {"owner": 0, "cred": "1/0", "why": "DEADLINE"},
                "IDLE": {"owner": 0, "cred": "1/0", "why": "IDLE"}}


def done_full(jid, n=8, lo=450, hi=550, d=0, adc=None, sg=None):
    """F1 要的**完整** DONE 证据: 产品只在 state==DONE 且候选有效时才打这三类行
    (diag_relative.c:2056/2068/2074), 两行状态不构成"可提交"。"""
    adc = n if adc is None else adc
    sg = n if sg is None else sg
    return job(jid, "DONE", adc=adc, sg=sg, want=n, lo=lo, hi=hi, d=d) + [
        "[DIAG] job data adc[400..500]n%d nz2 sg[60..80]n%d seq101..%d" % (adc, sg, 100 + sg),
        "[DIAG] job thr tier=1 dev=30/60 drop=15/40 FIELD_UNVERIFIED",
        "[DIAG] job DONE result; cred zero=1 learn=1 commit is separate"]


def cancel_requested(jid, st="CANCEL_PENDING"):
    return ["[DIAG] cancel id=%d REQUESTED st=%s (motor untouched)" % (jid, st)]


def cancel_terminal(jid, st="DONE", why="NONE"):
    return ["[DIAG] cancel id=%d NOT-CANCELLED: already %s why=%s" % (jid, st, why)]


# 产品真实成功行(motor.c:1328 / :1415 / :1698) —— 修前的三条短行留在 *_PRE_FIX:
ARM_OK = ["[MOT] ARMED (drv_en request=HIGH readback verified, ENN feedback=LOW confirmed)"]
DISARM_OK = ["[MOT] DISARM (动作已做: STEP DONE / 许可 LOW verified / ENN feedback=OK)"]
STOP_OK = ["[MOT] stop requested: target_hz=0 (软件命令值), STEP 实际关断结果见 "
           "stop_result(当前=DONE)"]
STATUS_DISABLED = ["[MOT] state=0 name=OTHER", "[MOT] cur_hz=0 tgt_hz=0 accel=0 decel=0 dir=0",
                   "[MOT] armed=0 drv_en_request=0 health=OK", "[MOT] drv_request=LOW pad=0",
                   "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK",
                   "[MOT] diag_off_confirmed=YES"]
MOT_ACCEL = ["[MOT] state=1 name=ACCEL", "[MOT] cur_hz=200 tgt_hz=300 accel=2000 decel=2000 dir=1",
             "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
             "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
             "[MOT] diag_off_confirmed=NO"]
MOT_CRUISE = ["[MOT] state=2 name=CRUISE", "[MOT] cur_hz=300 tgt_hz=300 accel=2000 decel=2000 dir=1",
              "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
              "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
              "[MOT] diag_off_confirmed=NO"]
# ---- 修前的错误合同原样保留, 只作为**反例输入**(F4 的"一个字段一个字段变错"从这里起手):
STATUS_DISABLED_PRE_FIX = ["[MOT] state=0 name=OTHER", "[MOT] cur_hz=0 tgt_hz=0 accel=0 decel=0 dir=0",
                           "[MOT] armed=0 drv_en_request=0 health=OK", "[MOT] drv_request=LOW pad=0",
                           "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=CUT_BY_HARDWARE",
                           "[MOT] diag_off_confirmed=YES"]
MOT_CRUISE_PRE_FIX = ["[MOT] state=2 name=CRUISE",
                      "[MOT] cur_hz=100 tgt_hz=100 accel=50 decel=50 dir=0",
                      "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
                      "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK",
                      "[MOT] diag_off_confirmed=NO"]
DISARM_OK_PRE_FIX = ["[MOT] DISARM (done)"]
ARM_OK_PRE_FIX = ["[MOT] ARM ok armed=1"]
STOP_OK_PRE_FIX = ["[MOT] stop requested target=0"]
MOT_ARMED_IDLE = ["[MOT] state=0 name=OTHER", "[MOT] cur_hz=0 tgt_hz=0 accel=2000 decel=2000 dir=1",
                  "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
                  "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
                  "[MOT] diag_off_confirmed=NO"]
# 收口的五条固定发送(3 条电机 + 2 条 IRQ 尾部): 跨度/条数判据按这个集合取台账 TX。
CLEANUP_FIVE = ("motor_stop", "motor_disarm", "motor_status", "safety_irq_disable",
                "safety_irq_status")
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
        fx(sequence={LS: [job(17, "RUNNING", adc=3, sg=3),
                          job(17, "RUNNING", adc=6, sg=6),
                          done_full(17)]},
           responses={LEARN: started(17)}),
        dict(code="", polls_min=3, sent_count={LS: 3, LEARN: 1}, binds={"b1": 17}))
    add("D2_owner不是FREE立即拒", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "DONE", owner=1, cred="1/1")}),
        # R7F8F: 修前这条由 expect_values 报 FIELD_VALUE_MISMATCH; 现在合同在**首次出现**就停,
        # 身份变成 JOB_CONTRACT_VIOLATED(D2 的意图"owner 不是 FREE 立即拒"没有变)。
        dict(code="JOB_CONTRACT_VIOLATED", polls_exact=1))
    add("D3_缺零点凭据立即拒", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "DONE", owner=0, cred="0/1")}),
        dict(code="JOB_CONTRACT_VIOLATED", polls_exact=1))
    add("D4_样本数不足不当成功", "正常DONE",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17),
                      LS: done_full(17, adc=5, sg=5)}),
        # R7F8F: 样本不足的 DONE 现在带着**完整候选证据**进来, 由合同判出"计数低于受理的 want";
        # 用 done_full 是为了把这一条与"根本没有候选"那条分开(两种失败原因不能混成一个身份)。
        dict(code="JOB_DONE_CANDIDATE_INCONSISTENT", polls_exact=1))
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
        dict(code="JOB_STATE_TERMINAL_FAILURE", polls_exact=1))
    add("T2_TIMEOUT不在进行中集合立即收口", "异常终态",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "TIMEOUT", why="DEADLINE", owner=0,
                                                  cred="1/0")}),
        dict(code="JOB_STATE_TERMINAL_FAILURE", polls_exact=1))
    add("T3_不该出现的CANCELLED不当DONE", "异常终态",
        [learn("lc1", "b1", 17), poll_done("ls", "b1")],
        fx(responses={LEARN: started(17), LS: job(17, "CANCELLED", why="USER_CANCEL", owner=0,
                                                  cred="1/0")}),
        # R7F8F: CANCELLED 的合法 why 是产品打的 "CANCELLED"(diag_relative.c:1690 的终态写入),
        # 且"不该出现的取消终态"同样由合同的 phase 集合判出 => 身份是合同违例而不是字段不符。
        dict(code="JOB_CONTRACT_VIOLATED", polls_exact=1))

    # ---------------- P 轮询边界与字节门
    add("P1_时间窗到期具名结束", "轮询边界",
        [learn("lc1", "b1", 17), poll_done("ls", "b1", attempts=99, window=2.0)],
        fx(responses={LEARN: started(17)}, sequence={LS: [job(17, "RUNNING",
                                                              adc=3, sg=3)] * 20}),
        dict(code="POLL_WINDOW_EXPIRED", polls_min=3))
    add("P2_次数上限具名结束(窗口更长也停)", "轮询边界",
        [learn("lc1", "b1", 17), poll_done("ls", "b1", attempts=3, window=30.0)],
        fx(responses={LEARN: started(17)}, sequence={LS: [job(17, "RUNNING",
                                                              adc=3, sg=3)] * 20}),
        dict(code="POLL_ATTEMPTS_EXHAUSTED", polls_exact=3))
    add("P3_矛盾字段(无[MOT]状态行)在旧运动门里立刻停", "轮询边界",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
         st_("start", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
             quota={"name": "motor_run", "n": 1}),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_profile="RUNNING",
             poll={"until_seconds": 2.0, "interval_seconds": 0.5,
                   "retry_codes": ["PROFILE_NOT_RUNNING"]})],
        fx(responses={"motor_arm": ARM_OK, "motor_start": [],
                      "motor_status": ["[DIAG] 与电机无关的一行"]}),
        # R7F8F: 修前这条报 PROFILE_FIELDS_UNREADABLE; F4 之后运行合同先要 hz 行,
        # 同一份无关响应照样**当场**判红, 身份改成 PROFILE_NOT_RUNNING(意图"不重试"没变)。
        dict(code="PROFILE_NOT_RUNNING", polls_exact=1, gate="motor_ramping"),
        extra=["--arm-energize-deadline-seconds", "30"])
    add("P3b_旧运动巡航轮询仍等到CRUISE(回归)", "轮询边界",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
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
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"])],
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
           sequence={LS: [job(17, "RUNNING", adc=3, sg=3),
                           done_full(17)]}),
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
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
         st_("w", "__watch__", key="__watch__", seconds=5),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
        fx(responses={"motor_arm": ARM_OK, "__watch__": ["[DIAG] idle"],
                      "motor_status": STATUS_DISABLED}),
        dict(code="ARM_ENERGIZE_DEADLINE_EXCEEDED", obligation=True, cleanup_min=3,
             success_false=True, watch_min=1.5, watch_max=2.5),
        extra=["--arm-energize-deadline-seconds", "2"])
    add("E2_未申报arm时限在写前拒", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"])],
        fx(responses={"motor_arm": ARM_OK}),
        dict(code="ARM_ENERGIZE_DEADLINE_UNDECLARED", sent_none=["motor_arm"]))
    add("E3_正常stop后仍armed不清通电义务", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
         st_("stop", "motor_stop", key="motor_stop", seconds=2, expect_contains=["stop"]),
         st_("w", "__watch__", key="__watch__", seconds=5),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
        fx(responses={"motor_arm": ARM_OK, "motor_stop": STOP_OK, "__watch__": ["[DIAG] idle"],
                      "motor_status": STATUS_DISABLED}),
        dict(code="ARM_ENERGIZE_DEADLINE_EXCEEDED", obligation=True, cleanup_min=3),
        extra=["--arm-energize-deadline-seconds", "2"])
    add("E4_start运动截止与arm通电截止各自独立", "通电与运动",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
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
        # R7F8F 输入更正(F3 要求的形状): disarm 的**产品成功行**只算"动作已做", 义务关闭还要
        # 它**相邻的那份新 motor_status** 证得禁能 => 快照排在 disarm 之后、watch 之前。
        # 修前这里把 "[MOT] DISARM (done)"(产品从不打的行)当成功, 并把 watch 排在快照之前。
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
         st_("disarm", "motor_disarm", key="motor_disarm", seconds=2, expect_contains=["DISARM (动作已做"]),
         st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="]),
         st_("w", "__watch__", key="__watch__", seconds=5)],
        fx(responses={"motor_arm": ARM_OK, "motor_disarm": DISARM_OK,
                      "__watch__": ["[DIAG] idle"], "motor_status": STATUS_DISABLED,
                      "motor_stop": STOP_OK}),
        dict(code="", watch_min=7.5, watch_max=8.5, closed_event="ENERGIZE_DEADLINE_CLOSED"),
        extra=["--arm-energize-deadline-seconds", "2"])

    # ---------------- L 清理对账
    add("L1_业务stop_disarm与固定三条分别记账", "清理",
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
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
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
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
        [st_("arm", "motor_arm", key="motor_arm", seconds=2, expect_contains=["ARMED (drv_en request=HIGH readback verified"])],
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

    # ---------------- G/H: R7F8F 返修的新形状(A 复审 P1~P8 各拆独立故障形状 + 完整正向链)
    # 每条只破坏**一个**条件, 且都另配正向例: 任务书 §2 明说"不能只让一个条件挡住其他错误"。
    def g(name, steps, fixture, expect, extra=None):
        add(name, "R7F8F返修", steps, fixture, expect, extra=extra)

    g("G1_完整DONE正例(候选统计与进度一致)",
      [learn("lc1", "b1", 17), poll_done("ls", "b1")],
      fx(responses={LEARN: started(17)}, sequence={LS: [job(17, "RUNNING"), done_full(17)]}),
      dict(code="", polls_min=2))
    g("G5_计数单调上升的正例(3/3到6/6到DONE 8/8)",
      [learn("lc1", "b1", 17), poll_done("ls", "b1")],
      fx(responses={LEARN: started(17)},
         sequence={LS: [job(17, "RUNNING", adc=3, sg=3), job(17, "RUNNING", adc=6, sg=6),
                        done_full(17)]}),
      dict(code="", polls_min=3))
    g("G6_较快源超过N不是假上限(adc=12 而 want=8 仍算成功)",
      [learn("lc1", "b1", 17), poll_done("ls", "b1")],
      fx(responses={LEARN: started(17)}, sequence={LS: [done_full(17, adc=12, sg=8)]}),
      dict(code="", polls_exact=1))
    g("G7_候选统计与进度行不符具名拒",
      [learn("lc1", "b1", 17), poll_done("ls", "b1")],
      fx(responses={LEARN: started(17)},
         sequence={LS: [job(17, "DONE", adc=9, sg=9) + [
             "[DIAG] job data adc[400..500]n8 nz2 sg[60..80]n8 seq101..108",
             "[DIAG] job thr tier=1 dev=30/60 drop=15/40 FIELD_UNVERIFIED",
             "[DIAG] job DONE result; cred zero=1 learn=1 commit is separate"]]}),
      dict(code="JOB_DONE_CANDIDATE_INCONSISTENT", polls_exact=1))
    g("G2_受理n与实发n不符在绑定处拒",
      [st_("lc", LEARN, key=LEARN, seconds=3, expect_contains=["STARTED"],
           quota={"name": "diag_profile_learn", "n": 1},
           job_bind={"name": "b1", "params": {"lo": 450, "hi": 550, "dir": 0}}),
       poll_done("ls", "b1")],
      fx(responses={LEARN: started(17, n=9), LS: done_full(17, n=9)}),
      dict(code="JOB_ACCEPTED_NOT_SENT", sent=[LEARN]))
    g("G3_取消前出现CANCEL_PENDING即矛盾",
      [learn("lc1", "b1", 17),
       st_("ls", LS, key=LS, seconds=3,
           job_contract={"binding": "b1", "phase": "pre_cancel"},
           poll={"until_seconds": 2.0, "interval_seconds": 0.5, "max_attempts": 3,
                 "retry_codes": ["FIELD_VALUE_MISMATCH"],
                 "in_progress": {"row": JOB_ROW, "states": ["RUNNING"]},
                 "job_binding": "b1"})],
      fx(responses={LEARN: started(17)}, sequence={LS: [job(17, "CANCEL_PENDING")]}),
      dict(code="JOB_CONTRACT_VIOLATED", polls_exact=1))
    g("G4_取消后到CANCELLED为止都算进行中",
      [learn("lc1", "b1", 17), cancel("cx1", "b1"),
       st_("ls", LS, key=LS, seconds=3,
           job_contract={"binding": "b1", "phase": "post_cancel"},
           expect_values=[{"row": JOB_ROW, "must": {"st": "CANCELLED"}, "job_binding": "b1"}],
           poll={"until_seconds": 3.0, "interval_seconds": 0.5, "max_attempts": 6,
                 "retry_codes": ["FIELD_VALUE_MISMATCH"],
                 "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                 "job_binding": "b1"})],
      fx(responses={LEARN: started(17), "cancel_b1": cancel_requested(17)},
         sequence={LS: [job(17, "RUNNING"), job(17, "CANCEL_PENDING"), job(17, "CANCELLED")]}),
      dict(code="", polls_min=3))

    def run_steps():
        return [st_("ms", "motor_status", key="motor_status", seconds=3,
                    expect_profile="RUNNING",
                    expect_running={"dir": 1, "cur": 300, "tgt": 300,
                                    "accel": 2000, "decel": 2000})]

    g("G8_巡航正例(使能侧与申报频率全项自洽)", run_steps(),
      fx(responses={"motor_status": MOT_CRUISE}), dict(code=""))
    for tag, idx, old, new, needle in [
            ("G9", 4, "expected_ENN=LOW", "expected_ENN=HIGH", "expected_ENN=HIGH"),
            ("G10", 4, "ENN_fb=0", "ENN_fb=1", "ENN_fb=1"),
            ("G11", 4, "verdict=OK", "verdict=CUT_BY_HARDWARE", "verdict=CUT_BY_HARDWARE"),
            ("G12", 4, "resolved verdict", "UNRESOLVED verdict", "fb=UNRESOLVED"),
            ("G13", 3, "drv_request=HIGH pad=1", "drv_request=LOW pad=0", "drv_request=LOW"),
            ("G14", 2, "armed=1", "armed=0", "armed=0"),
            ("G15", 2, "health=OK", "health=FAULT", "health=FAULT"),
            ("G16", 1, "cur_hz=300 tgt_hz=300", "cur_hz=200 tgt_hz=300", "!=tgt_hz"),
            ("G17", 1, "decel=2000 dir=1", "decel=2000 dir=0", "expect_running.dir")]:
        bad = list(MOT_CRUISE)
        bad[idx] = bad[idx].replace(old, new)
        assert bad[idx] != MOT_CRUISE[idx], tag
        g("%s_单字段变错(%s)" % (tag, needle), run_steps(),
          fx(responses={"motor_status": bad}),
          dict(code="PROFILE_NOT_RUNNING", bad_contains=[needle]))

    ARM_LATE = [st_("arm", "motor_arm", key="motor_arm", seconds=2,
                    expect_contains=["ARMED (drv_en request=HIGH readback verified"])]
    fx_late = fx(responses={"motor_arm": ARM_OK, "motor_status": STATUS_DISABLED,
                            "motor_stop": STOP_OK, "motor_disarm": DISARM_OK})
    fx_late["block"] = {"motor_arm": 3.0}
    g("H1_arm迟到回包在通电截止即进收口", ARM_LATE, fx_late,
      dict(code="MISSING_RESPONSE_PROMPT", cleanup_enter_max_s=2.05, cleanup_min=3,
           obligation=True, success_false=True),
      extra=["--arm-energize-deadline-seconds", "2", "--total-deadline-seconds", "30"])
    g("H2_第二次arm在写入尝试之前拒",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("disarm", "motor_disarm", key="motor_disarm", seconds=2,
           expect_contains=["DISARM (动作已做"]),
       st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="]),
       st_("arm2", "motor_arm", key="motor_arm2", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"])],
      fx(responses={"motor_arm": ARM_OK, "motor_arm2": ARM_OK, "motor_disarm": DISARM_OK,
                    "motor_status": STATUS_DISABLED, "motor_stop": STOP_OK}),
      dict(code="ARM_SECOND_ATTEMPT_REFUSED", sent_count={"motor_arm": 1}),
      extra=["--arm-energize-deadline-seconds", "20"])
    g("H3_第二次start在写入尝试之前拒",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("s1", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
           quota={"name": "motor_run", "n": 1}),
       st_("s2", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
           quota={"name": "motor_run", "n": 1})],
      fx(responses={"motor_arm": ARM_OK, "motor_start": [], "motor_status": MOT_CRUISE,
                    "motor_stop": STOP_OK, "motor_disarm": DISARM_OK}),
      dict(code="MOTOR_START_SECOND_REFUSED", sent_count={"motor_start": 1}),
      extra=["--arm-energize-deadline-seconds", "20"])
    g("H4_泛匹配DISARM字样不解除通电义务",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("disarm", "motor_disarm", key="motor_disarm", seconds=2, expect_contains=["DISARM"])],
      fx(responses={"motor_arm": ARM_OK, "motor_disarm": DISARM_OK_PRE_FIX,
                    "motor_status": STATUS_DISABLED, "motor_stop": STOP_OK}),
      dict(code="DISARM_SUCCESS_ROW_UNPROVEN", obligation=True, success_false=True),
      extra=["--arm-energize-deadline-seconds", "20"])
    IRQ_ON = ["[SAFETY] IRQ gate OPEN: 1 active protection input(s) registered",
              "[SAFETY] IRQ enabled mask=0x00"]
    IRQ_OFF = ["[SAFETY] IRQ gate CLOSED - 活动安全输入(0 路)无软件响应路径"]
    IRQ_BAD = ["[SAFETY] IRQ gate OPEN: 1 active protection input(s) registered"]


    def irq_steps():
        return [st_("arm", "motor_arm", key="motor_arm", seconds=2,
                    expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
                st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
                    expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
                st_("boom", "motor_status", key="motor_status_running", seconds=2,
                    expect_profile="RUNNING")]


    # 业务步读的是**响应表**里的另一枚 key: 清理路径的注入只按命令查表(_stub_feed 无 sequence),
    # 若两处共用 "motor_status" 就会把巡航那份喂给清理 => 禁能一律 UNKNOWN(夹具错, 不是被审件的错)。
    def irq_fx(off_rows):
        return fx(responses={"motor_arm": ARM_OK, "safety_irq_enable": IRQ_ON,
                             "safety_irq_disable": ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"],
                             "safety_irq_status": off_rows,
                             "motor_status_running": MOT_CRUISE,
                             "motor_status": STATUS_DISABLED,
                             "motor_stop": STOP_OK, "motor_disarm": DISARM_OK})

    g("H5_开过IRQ则固定尾部各一条同一端口", irq_steps(), irq_fx(IRQ_OFF),
      # 修前的 MOT_CRUISE 夹具是错的合同(ENN HIGH/1 当正常), 所以这条原本靠 PROFILE_NOT_RUNNING
      # 触发清理; 夹具按 F4 改对之后业务链是真跑得通的 => 现在证的是"正常收口里固定尾部各一条"。
      dict(code="", cleanup_tx=["motor_stop", "motor_disarm", "motor_status",
                                "safety_irq_disable", "safety_irq_status"],
           tx_count={"safety_irq_disable": 1, "safety_irq_status": 1},
           disarm="CONFIRMED_DISABLED", status="OK"),
      extra=["--arm-energize-deadline-seconds", "20"])
    g("H6_尾部没回CLOSED只追加披露不改撤能结论",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
           expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
       st_("disarm", "motor_disarm", key="motor_disarm", seconds=2,
           expect_contains=["DISARM (动作已做"]),
       st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
      fx(responses={"motor_arm": ARM_OK, "safety_irq_enable": IRQ_ON,
                    "safety_irq_disable": ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"],
                    "safety_irq_status": IRQ_BAD, "motor_disarm": DISARM_OK,
                    "motor_status": STATUS_DISABLED, "motor_stop": STOP_OK}),
      dict(code="CLEANUP_ERRORS", disarm="CONFIRMED_DISABLED", success_false=True,
           errors_contains=["IRQ_TAIL_NOT_CLOSED"]),
      extra=["--arm-energize-deadline-seconds", "20"])
    g("H7_没开IRQ就不发尾部两条",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("disarm", "motor_disarm", key="motor_disarm", seconds=2,
           expect_contains=["DISARM (动作已做"]),
       st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
      fx(responses={"motor_arm": ARM_OK, "motor_disarm": DISARM_OK,
                    "motor_status": STATUS_DISABLED, "motor_stop": STOP_OK,
                    "safety_irq_disable": ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"],
                    "safety_irq_status": IRQ_OFF}),
      dict(code="", cleanup_tx_none=["safety_irq_disable", "safety_irq_status"]),
      extra=["--arm-energize-deadline-seconds", "20"])
    g("H9_清理后仍巡航必须判红(不是业务码独大)",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("ms", "motor_status", key="motor_status", seconds=2, expect_contains=["armed=1"])],
      fx(responses={"motor_arm": ARM_OK, "motor_status": MOT_CRUISE,
                    "motor_stop": STOP_OK, "motor_disarm": DISARM_OK}),
      dict(code="CLEANUP_UNCONFIRMED", disarm="UNKNOWN", success_false=True,
           cleanup_tx=["motor_stop", "motor_disarm", "motor_status"]),
      extra=["--arm-energize-deadline-seconds", "20"])
    g("H8_会话截止不随步骤续期",
      [st_("w1", "__watch__", key="__watch__", seconds=2),
       st_("ms", "motor_status", key="motor_status", seconds=3, expect_contains=["state="])],
      fx(responses={"__watch__": ["[DIAG] quiet"], "motor_status": STATUS_DISABLED}),
      # 时间线(不放宽截止来消红): t0=0, 总时限 5 s + SLACK 3 s => w1 的窗取绝对截止 5.0;
      # 安静窗一直待到 5.0(它本来就是"观察到那时为止"), 第二条业务步在 **write 之前** 就没剩预算。
      # 修前的"当前时刻 + 总时限"会把会话续到 10 s, 这条 ms 会真的发出去 => code 变空、
      # motor_status 实发 1 次, 两条断言同时红, 危害看得见。
      dict(code="STEP_DEADLINE_BEFORE_WRITE", sent_count={"motor_status": 0}, watch_min=4.8,
           watch_max=5.05), extra=["--total-deadline-seconds", "5"])

    # ------- F5b: 操作者 2026-11-05 实测确认的两条(开 IRQ 后 arm 前失败漏收口 / 4 秒预留排不下五条)
    FIVE = ["motor_stop", "motor_disarm", "motor_status", "safety_irq_disable",
            "safety_irq_status"]
    # ①IRQ 尝试自己建立收口义务: 真实序列是先 safety_irq_enable 再 motor_arm, 中间任何失败
    #   都落在电机 obligation 还是 False 的窗口里。修前的 `finally: if obligation:` 一条尾部
    #   都不发 => IRQ 门留在 OPEN(操作者模拟"先开 IRQ、随后查询失败"实测到的形状)。
    g("H10_开过IRQ但从未arm也要收口(尾部两条照发)",
      [st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
           expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
       st_("boom", "motor_status", key="motor_status_running", seconds=2,
           expect_profile="RUNNING")],
      # 清理五条的响应**一律不配**: 电机那三条本来就不该发(发了就会多一条 NO_RESPONSE 错),
      # 尾部两条配了才证"照发且读到了 CLOSED"。
      fx(responses={"safety_irq_enable": IRQ_ON, "motor_status_running": STATUS_DISABLED,
                    "safety_irq_disable": ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"],
                    "safety_irq_status": IRQ_OFF}),
      dict(code="PROFILE_NOT_RUNNING", obligation=False, success_false=True,
           disarm="NOT_ARMED", status="STOP",
           cleanup_tx=["safety_irq_disable", "safety_irq_status"],
           cleanup_tx_none=["motor_stop", "motor_disarm"],
           # motor_status 只允许**业务那一条**(boom 步发的查询): 若收口又把电机三条发出去,
           # 这里会数到 2 条 => 判据把"无义务却发撤能"抓出来, 而不是靠 cleanup_tx_none 的
           # 命令名(它会误伤业务查询本身)。
           tx_count={"safety_irq_disable": 1, "safety_irq_status": 1},
           sent_count={"motor_status": 1, "motor_stop": 0, "motor_disarm": 0},
           cleanup_span=[2, 2, 0.0, 1.5],
           closed_event="CLEANUP_MOTOR_NOT_OBLIGATED",
           errors_none_contains=["DISARM_NOT_CONFIRMED", "IRQ_TAIL", "HARD_DEADLINE_BEFORE"]))
    # ②本轮申报的 7 s 预留要真的容得下五条: 运动截止后全部无响应 => 每条在自己的 1.2 s 窗里
    #   试完, 五次发送、跨度 4.8 s(=4 个窗), 收尾仍在预留 7 s 之内。修前的 4 s 常量只排得下
    #   四条, 第五条 safety_irq_status 直接不出现在 TX 里(A 的 stub 复现到的形状)。
    g("H11_收口全部无响应时五条各有发送机会(7秒预留)",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
           expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
       st_("st", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
            quota={"name": "motor_run", "n": 1}),
       st_("w", "__watch__", key="__watch__", seconds=5)],
      fx(responses={"motor_arm": ARM_OK, "safety_irq_enable": IRQ_ON,
                    "motor_start": ["[MOT] step pwm enabled"],
                    "__watch__": ["[DIAG] tick"]}),
      dict(code="CLEANUP_UNCONFIRMED", obligation=True, success_false=True, status="OK",
           disarm="UNKNOWN", cleanup_tx=FIVE, tx_count={k: 1 for k in FIVE},
           cleanup_span=[5, 5, 4.7, 6.05],
           errors_contains=["motor_stop:NO_RESPONSE", "motor_status:NO_RESPONSE",
                            "IRQ_TAIL_safety_irq_status:NO_RESPONSE", "DISARM_NOT_CONFIRMED"],
           errors_none_contains=["HARD_DEADLINE_BEFORE",
                                 "CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE"],
           res_near={"cleanup_reserve_seconds": 7.0}),
      extra=["--arm-energize-deadline-seconds", "30", "--motion-cap-seconds", "1",
             "--total-deadline-seconds", "60"])
    # ②的反向: 预留申报得排不下五条 => 在开口/记账之前具名拒绝(一条命令都不发),
    #   而不是把电机转起来之后才发现收不了尾。这条同时是"7 秒不是随口写的数字"的锚点。
    g("H12_申报排不下五条的预留就在开口之前拒",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
           expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1})],
      fx(responses={"motor_arm": ARM_OK, "safety_irq_enable": IRQ_ON,
                    "safety_irq_disable": ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"],
                    "safety_irq_status": IRQ_OFF, "motor_stop": STOP_OK,
                    "motor_disarm": DISARM_OK, "motor_status": STATUS_DISABLED}),
      dict(code="CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS", obligation=False,
           success_false=True, cleanup_tx_none=FIVE, sent_none=["motor_arm"],
           bad_contains=["required_seconds", "checked_before", "reserve_seconds"],
           quota_used={"port_open": None}, closed_event="PORT_NEVER_OPENED",
           res_near={"cleanup_reserve_seconds": 4.0}),
      extra=["--arm-energize-deadline-seconds", "20", "--cleanup-reserve-seconds", "4"])
    # ②的边界: 会话总截止确实**夹住**了收口(预留没被放宽)。少发是截止造成的, 但这件事在第一条
    #   清理命令之前就以 CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE 说清楚, 不靠事后归因。
    # F5c(A 的 stub 复现): 会话 10 s、申报预留 7 s、前置等待 8 s。修前第 9 秒仍然允许 arm,
    # 业务跑到第 10 秒才结束 => 收口剩 0 秒, 五条全被 *_HARD_DEADLINE_BEFORE_* 跳过(A 的形状)。
    # 现在业务绝对截止 = 10 - 7 = 3.0 s: 等待自己在 3.0 收(它的申报秒数 2+SLACK 本来就 < 会话
    # 总窗, 所以这条等待**只**能被预留挡住), 之后的 arm 在**写之前**被拒, 电机从未带电 =>
    # 那 7 秒整段留在那里。等待若写成 8 s, 突变后的对照会撞在"会话总截止"上而不是"预留"上,
    # 危害就看不见了(X6 第一版正是这样, 已按此改)。
    g("H13_前置等待耗掉会话后业务在收口预留之前停下",
      [st_("pre", "__watch__", key="__watch__", seconds=2),
       st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"])],
      fx(responses={"__watch__": ["[DIAG] tick"], "motor_arm": ARM_OK,
                    "motor_status": STATUS_DISABLED, "motor_stop": STOP_OK,
                    "motor_disarm": DISARM_OK}),
      dict(code="STEP_DEADLINE_BEFORE_WRITE", obligation=False, success_false=True,
           sent_count={"motor_arm": 0},
           cleanup_tx_none=["motor_stop", "motor_disarm", "safety_irq_disable",
                            "safety_irq_status"],
           watch_done_at=[2.95, 3.15], bad_contains=["business_remaining_s"],
           res_near={"cleanup_reserve_seconds": 7.0, "business_abs_deadline": 3.0,
                     "cleanup_reserved": True}),
      extra=["--total-deadline-seconds", "10", "--arm-energize-deadline-seconds", "30"])
    # F5c 的正面对偶: 义务在业务截止之前建立, 收口全静默 => 五条仍各拿到自己的窗(首末跨度 4.8),
    # 一条都不被截止跳过; 收口起点恰好落在"总截止 - 预留"= 3.0 s 上。
    g("H14_义务早建立且收口全静默_五条仍拿到预留窗口",
      [st_("arm", "motor_arm", key="motor_arm", seconds=2,
           expect_contains=["ARMED (drv_en request=HIGH readback verified"]),
       st_("irq", "safety_irq_enable", key="safety_irq_enable", seconds=2,
           expect_contains=["IRQ"], quota={"name": "irq_polarity_prep", "n": 1}),
       st_("pre", "__watch__", key="__watch__", seconds=8)],
      fx(responses={"motor_arm": ARM_OK, "safety_irq_enable": IRQ_ON,
                    "__watch__": ["[DIAG] tick"]}),
      dict(code="CLEANUP_UNCONFIRMED", obligation=True, success_false=True,
           disarm="UNKNOWN", cleanup_tx=FIVE, tx_count={k: 1 for k in FIVE},
           cleanup_span=[5, 5, 4.7, 6.05],
           errors_none_contains=["HARD_DEADLINE_BEFORE",
                                 "CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE"],
           res_near={"cleanup_reserve_seconds": 7.0, "business_abs_deadline": 3.0,
                     "cleanup_reserved": True},
           closed_event="CLEANUP_ENTERED"),
      extra=["--total-deadline-seconds", "10", "--arm-energize-deadline-seconds", "30"])

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
        # R7F8F 更正: 摘掉旧的字段门之后, **合同**在第一次看到 TIMEOUT 就收口,
        # 所以突变后的新身份是 JOB_STATE_TERMINAL_FAILURE(而不是修前的"等成窗口耗尽")。
        # 这条元负控现在证的是"合同这道新门也在同一处生效", 不是旧门的必要性。
        mut_code="JOB_STATE_TERMINAL_FAILURE"),
    "X3_移除取消额度行": dict(
        find='    ("diag_profile learn_cancel", "diag_profile_learn_cancel"),\n',
        repl="",
        case="Q1_取消未申报额度不发", gone="QUOTA_STEP_UNDECLARED",
        sent_harm="diag_profile learn_cancel 17"),
    "X4_把arm截止改回start起算": dict(
        # R7F8F 重挂: arm 义务的建立已经搬进 arm_attempt_gate()(在算 step_dl **之前**),
        # 旧的两段锚点文本在返修件里不存在了。现在按修前的顺序做突变 —— 先按申报秒数算这条命令
        # 的截止, 之后才立 arm 义务 —— 被破坏副本必须在 H1 上露出危害: 迟到 3 s 的 arm 回包
        # 又被等满, 收口第一条 TX 落到 3 s 之后而不是 2 s 截止处(所以这条判的是**时刻**, 不是码)。
        pairs=[('            arm_attempt_gate(energize, led, clock, asname, cmd)\n'
                '            want = int(st.get("seconds", 3))\n',
                '            want = int(st.get("seconds", 3))\n'),
               ('            if budget.armed and step_dl <= clock.monotonic():\n'
                '                raise Stop("MOTION_BUDGET_EXHAUSTED", "as=%s want=%ds" % (asname, want))\n',
                '            if budget.armed and step_dl <= clock.monotonic():\n'
                '                raise Stop("MOTION_BUDGET_EXHAUSTED", "as=%s want=%ds" % (asname, want))\n'
                '            arm_attempt_gate(energize, led, clock, asname, cmd)\n')],
        case="H1_arm迟到回包在通电截止即进收口", gone="MISSING_RESPONSE_PROMPT",
        # 危害不只是换一个码: 被破坏副本会**等满迟到回包**并把 arm 当成功继续跑,
        # 于是那条命令的回读不再受新立截止约束(正是复审 P6 的形状)。
        sent_harm="motor_arm"),
    # X5 给本轮第 5 处改动(附带修正的既有缺口)作证: 把清理预留账改回旧写法(单会话路径永远拿到
    # None)之后, L3 的"预留耗尽仍撤能但本场不 OK"会重新变成一条绿色成功 —— 门没了, 检出也没了。
    "X5_清理预留账改回旧写法": dict(
        find='hook, quota if plan else None,\n',
        repl='hook, quota_box if plan else None,\n',
        case="L3_清理预留耗尽仍撤能但本场不OK", gone="CLEANUP_RESERVE_EXHAUSTED"),
    # F5c: "业务截止 = 总截止 - 收口预留"这道门的两条危害形状。突变点必须打在 **business_abs_dl**
    # 本身(它是写前检查真正读的那个值), 只摘 budget.business_dl 不够 —— 上一版把锚点挂在后者,
    # 两条突变都变成"摘了门也没变化", 证不到任何东西(自己踩完才发现, 已改)。
    "X6_把收口预留从业务预算里抹掉_前置等待后arm仍放出去": dict(
        find='    business_abs_dl = (session_abs_dl - float(reserve_declared)) if may_obligate '
             'else session_abs_dl\n',
        repl='    business_abs_dl = session_abs_dl\n',
        case="H13_前置等待耗掉会话后业务在收口预留之前停下",
        gone="STEP_DEADLINE_BEFORE_WRITE", sent_harm="motor_arm"),
    "X7_同一道门_静默收口时第五条发不出去": dict(
        find='    business_abs_dl = (session_abs_dl - float(reserve_declared)) if may_obligate '
             'else session_abs_dl\n',
        repl='    business_abs_dl = session_abs_dl\n',
        case="H14_义务早建立且收口全静默_五条仍拿到预留窗口",
        must_lose=["safety_irq_status"]),
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
    # runner 在**创建台账之前**就失败时(例如 argparse 不认这个开关 → rc=2, res 里没有 ledger),
    # `Path("")` 在 Windows 上就是 `Path(".")` —— 上一版拿它去 read_text 直接 PermissionError,
    # 把"runner 拒了这条 CLI"伪装成"判据脚本自己炸"。空串一律当"没有台账", 只回退到本例目录里找。
    raw_led = str(res.get("ledger") or "")
    lp = pathlib.Path(raw_led) if raw_led else None
    if lp is None or not lp.is_file():
        cand = sorted(d.glob("*/ledger.jsonl"))
        lp = cand[0] if cand else None
    if lp and lp.is_file():
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
    if e.get("errors_contains"):
        errs = " ".join(res.get("cleanup_errors") or [])
        if not (res.get("cleanup_errors") or []):
            bad.append("清理错误清单是空的 => 尾部披露没进账")
        for needle in e["errors_contains"]:
            if needle not in errs:
                bad.append("清理错误里没有 %s(errors=%s)" % (needle, errs[:160]))
    # F5b: 预留排不下 / 截止夹短这类"少发"必须**只**在申报有问题时出现, 所以正向例要能判
    # "这些具名披露一条都不许在"。只断言错误清单里有什么, 挡不住"多一条也算过"的漂移。
    for needle in e.get("errors_none_contains") or []:
        if needle in " ".join(res.get("cleanup_errors") or []):
            bad.append("清理错误里出现了不该有的 %s(errors=%s)"
                       % (needle, " ".join(res.get("cleanup_errors") or [])[:200]))
    if "cleanup_span" in e:
        # [条数下限, 条数上限, 首末发送跨度下限秒, 上限秒]: 五条各有发送机会 = 收口段 TX 里
        # 五条都在, 且每条各等满自己的确认窗(修前 4 s 预留的缺陷正是第五条根本不发)。
        # 只取 CLEANUP_ENTERED **之后**的 TX: 业务里也可能发 motor_status(相邻禁能快照),
        # 按命令名全表取会把业务查询算进收口, 条数就虚高。
        n_min, n_max, s_min, s_max = e["cleanup_span"]
        ent = [i for i, x in enumerate(led) if x.get("kind") == "CLEANUP_ENTERED"]
        tail = led[ent[-1]:] if ent else []
        five = [x for x in tail if x.get("kind") == "TX" and
                x.get("bytes", "").split(chr(13))[0].strip() in CLEANUP_FIVE]
        got_n = len(five)
        if not ent:
            bad.append("台账没有 CLEANUP_ENTERED => 收口段 TX 无从判定")
        elif not (n_min <= got_n <= n_max):
            bad.append("收口实发 %d 条(=%s) 要求在 [%d,%d]"
                       % (got_n, [x.get("bytes", "").split(chr(13))[0].strip() for x in five],
                          n_min, n_max))
        elif got_n >= 2:
            span = float(five[-1].get("t_s", 0)) - float(five[0].get("t_s", 0))
            if not (s_min <= span <= s_max):
                bad.append("收口首末发送跨度=%.3fs 不在 [%s,%s](每条该各占自己的窗)"
                           % (span, s_min, s_max))
        elif s_max > 0:
            bad.append("收口只有 %d 条发送, 跨度判据无从计算" % got_n)
    for k, v in (e.get("res_near") or {}).items():
        got = res.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if not isinstance(got, (int, float)) or isinstance(got, bool) or \
                    abs(got - v) > 0.06:
                bad.append("回执 %s=%r 要求≈%s" % (k, got, v))
        elif got != v:
            bad.append("回执 %s=%r 要求=%r" % (k, got, v))

    # R7F8F 修判据自身的坑: 原来 `cl` 只在 cleanup_min 分支里赋值, 而 cleanup_tx 的失败文案也用它
    # => 只申报 cleanup_tx 的用例(本轮 H5/H6)会先炸 UnboundLocalError, 而不是给出判红原因。
    cl = [c2 for c2 in cmds if c2.startswith(("motor_stop", "motor_disarm"))]
    if "cleanup_min" in e:
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
    if e.get("bad_contains"):
        det = res.get("detail") or ""
        for needle in e["bad_contains"]:
            if needle not in det:
                bad.append("首错细节里没有 %s(detail=%s)" % (needle, det[:150]))
    if "cleanup_enter_max_s" in e:
        # F2 的 P6 口径: 通电截止一到就必须进收口, 不能等 arm 那条迟到回包等完再开始清理。
        st_i = [i for i, x in enumerate(led) if x.get("kind") == "SESSION_STOP"]
        after = [x for x in led[st_i[0] + 1:] if x.get("kind") == "TX"] if st_i else []
        if not st_i:
            bad.append("没有 SESSION_STOP 事件 => 收口时刻不作数")
        elif not after:
            bad.append("SESSION_STOP 之后没有任何清理 TX")
        else:
            got = float(after[0].get("t_s", 9e9))
            if got > e["cleanup_enter_max_s"]:
                bad.append("收口第一条 TX 在 %ss > %.2fs(截止后还在等业务回包)" %
                           (got, e["cleanup_enter_max_s"]))
    if "watch_done_at" in e:
        # 观察窗**自己**落在绝对时刻上的判据(F5c)。watch_min 是"相对前一个业务事件"的差值,
        # 而 H13 那条等待之前已经有 PORT_OPEN 的 OBLIGATION_SET 记在 3.0 s 上, 差值被判成 0
        # —— 那是差值口径不适配这条时间线, 不是等待没走满, 所以这里直接钉 WATCH_DONE 的时刻。
        lo, hi = e["watch_done_at"]
        w = [x for x in led if x.get("kind") == "WATCH_DONE"]
        if not w:
            bad.append("没有 WATCH_DONE 事件")
        else:
            got = float(w[-1].get("t_s", -1))
            if not (lo <= got <= hi):
                bad.append("WATCH_DONE 落在 t=%.3fs 不在 [%s,%s](业务绝对截止=%s)"
                           % (got, lo, hi, res.get("business_abs_deadline")))
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
        # 原门的证据有两种形状: 换错误码(gone), 或**少发一条安全命令**(must_lose)。
        # 只声明 must_lose 的突变不要求 code 改变 —— 收口被饿死时代码常常仍是同一个。
        ok = (code != m["gone"]) if m.get("gone") else bool(m.get("must_lose"))
        detail = "摘门后 code=%s(原门检出=%s)" % (code, m.get("gone") or "-")
        if m.get("mut_code"):
            same = code == m["mut_code"]
            ok = ok and same
            detail += "; 要求新身份=%s 实际=%s" % (m["mut_code"], code)
        if m.get("sent_harm"):
            sent = [r.get("cmd") for r in res.get("results", []) if r.get("cmd")]
            harm = m["sent_harm"] in sent
            ok = ok and harm
            detail += "; 摘门后真实发出=%s(应看到这条写尝试) 实发=%s" % (harm, sent)
        if m.get("must_lose"):
            # F5c 的元负控形状: 这道门的作用不是"换一个错误码", 而是"让五条收口真的发得出去"。
            # 摘掉之后必须看到**少发**, 只报 INJECTED 或只换 code 都不算证到危害。
            txs = [x.get("bytes", "").split(chr(13))[0].strip() for x in _led
                   if x.get("kind") == "TX"]
            lost = [c for c in m["must_lose"] if c not in txs]
            ok = ok and bool(lost)
            detail += "; 摘门后收口少发=%s(预留被业务吃掉才会少发) 全部TX=%s" % (lost, txs)
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
