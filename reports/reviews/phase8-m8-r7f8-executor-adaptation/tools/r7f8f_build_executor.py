# -*- coding: utf-8 -*-
"""R7F8F 返修构建器: 从修前派生件按 EDITS 逐条**精确整串替换**产出返修派生件。

输入(冻结, 一字节不动):
  reports/reviews/phase8-m8-r7f8-executor-adaptation/tools/m8r3_serial_chain_r7f8_pre_r7f8f.py
  = A 复审的 e97aaa4 那份派生件(245730 B, sha256 4c962170b2357eef…)
输出:
  tools/m8r3_serial_chain_r7f8.py     返修后的派生执行器(A 任务书 §2 F1~F5)
  patches/r7f8f_executor.patch        修前->修后 unified diff(给人读; 重放以本脚本为准)

每条 find 必须**恰好命中一次**, 命中 0 次或 >1 次当场拒绝并指名哪一条; `--verify` 在仓外重建一遍
并与仓库里的产物逐字节比对(任务书 §3.1: 不许只手改产物)。真实设备动作: 0。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import pathlib
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
PRE_REL = ROUND + "/tools/m8r3_serial_chain_r7f8_pre_r7f8f.py"
NEW_REL = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
PATCH_REL = ROUND + "/patches/r7f8f_executor.patch"
PRE_SHA = "4c962170b2357eef94afd73d43790d61880635a3d13287d29d543035768d6fed"
PRE_BYTES = 245730

EDITS = []


def edit(name, find, repl):
    EDITS.append((name, find, repl))


# ================================================================= F4. 运行侧使能合同
edit("HEADER",
     '"""M8-R7F8E 派生执行器(离线适配候选, 逐字节派生自 33d3f0af8a4c9c33 的 M8-R3; 旧文件未动) ',
     '"""M8-R7F8F 派生执行器(返修候选, 逐字节派生自 4c962170b2357eef 的 R7F8E 件; 旧两份均未动) ')

RUNNING_OLD = '''    elif st.get("expect_profile") == "RUNNING":
        summ, _ = disable_contract(rows)
        if summ is None:
            return {"verdict": "STOP", "code": "PROFILE_FIELDS_UNREADABLE"}
        # "能解析出一份完整 summary" **不等于**"在跑"(审核 §2B 第 5 条): 同一份
        # state=0 cur=0 tgt=100 armed=1 request=LOW pad=0 ENN=HIGH 之前会被收为 OK。
        # 运行只能由数值状态 + 实际频率证明: state=MOTOR_CRUISE(2) 且 cur_hz>0。
        inconsistent = []
        if summ["state_num"] != RUNNING_REQUIRED_STATE:
            inconsistent.append("state=%d(要求 MOTOR_CRUISE=%d)" % (summ["state_num"],
                                                                    RUNNING_REQUIRED_STATE))
        if summ["armed"] != 1:
            inconsistent.append("armed=%d(要求 1)" % summ["armed"])
        if summ["cur_hz"] <= 0:
            inconsistent.append("cur_hz=%d(没在动)" % summ["cur_hz"])
        if summ["tgt_hz"] <= 0:
            inconsistent.append("tgt_hz=%d(没在动)" % summ["tgt_hz"])
        if summ["drv_request"] != "HIGH" or summ["pad"] != 1:
            inconsistent.append("drv_request=%s/pad=%d(与武装运行不相容)" %
                                (summ["drv_request"], summ["pad"]))
        if summ["verdict"] not in ("OK", "CUT_BY_HARDWARE") or summ["fb_state"] != "resolved":
            inconsistent.append("反馈未解析或判决不可用")
        if inconsistent:
            led.ev("PROFILE_FAIL", **{"as": asname, "profile": "RUNNING",
                                       "bad": inconsistent})
            return {"verdict": "STOP", "code": "PROFILE_NOT_RUNNING", "bad": inconsistent,
                    "fields": summ}
'''
RUNNING_NEW = '''    elif st.get("expect_profile") == "RUNNING":
        # F4(复审 P8): 修前把 verdict=CUT_BY_HARDWARE 也当"在跑"收, 于是
        # expected_ENN=HIGH/ENN_fb=1(硬件把驱动切了)照样 PASS。运行必须**确实在使能侧**:
        # 产品只在请求使能(want=1)时才打 expected_ENN=LOW(motor.c:2491 的 want ? LOW : HIGH),
        # 而 safety_gpio.c:296 规定 CUT_BY_HARDWARE 只在 want_enabled=1 且 fb=1 时出现 —— 那正是
        # "要动没让动", 不是正常巡航。逐项清单见 running_contract()。
        summ, bad_r = running_contract(st, rows)
        if bad_r:
            led.ev("PROFILE_FAIL", **{"as": asname, "profile": "RUNNING", "bad": bad_r})
            return {"verdict": "STOP", "code": "PROFILE_NOT_RUNNING", "bad": bad_r,
                    "fields": summ}
'''
edit("F4_RUNNING_PROFILE", RUNNING_OLD, RUNNING_NEW)

# ---------------------------------------------------------------- 新增: 运行合同与 disarm 证据
OLD_TAIL_ANCHOR = '''def _ev_or_defer(led, deferred, kind, **kw):'''
NEW_HELPERS = '''RUNNING_HZ_ROW = (r"^\\[MOT\\] cur_hz=(?P<cur>[0-9]+) tgt_hz=(?P<tgt>[0-9]+) "
                  r"accel=(?P<accel>[0-9]+) decel=(?P<decel>[0-9]+) dir=(?P<dir>[0-9]+)$")
# 产品真实成功行(motor.c:1415)。修前的夹具写 "[MOT] DISARM (done)", 产品**从不**打这一行,
# 而 F3 要的正是"义务关闭须有真实成功 disarm 输出", 泛匹配 DISARM 字样不算。
DISARM_DONE_ROW = r"^\\[MOT\\] DISARM \\(动作已做: STEP (?P<step>\\S+) / 许可 LOW (?P<grant>[A-Za-z_]+)"
ARMED_OK_ROW = r"^\\[MOT\\] ARMED \\(drv_en request=HIGH readback verified, ENN feedback=LOW"


def running_contract(st, rows):
    """F4: "在跑"= 巡航态 + 武装 + 非零且等于目标频率 + 使能侧反馈自洽。

    逐项都从**同一份响应**里取(不跨响应拼字段)。计划可用 `expect_running` 再钉死
    dir/cur/tgt/accel/decel 的实值(本轮任务书 §4: DIR=1、300 Hz、加减速 2000 Hz/s)。
    返回 (fields, bad): bad 非空即这份响应没证得运行。"""
    summ, bad = disable_contract(rows)
    if summ is None:
        return None, ["PROFILE_FIELDS_UNREADABLE"]
    bad = []
    hits = [m for m in (re.fullmatch(RUNNING_HZ_ROW, r) for r in rows if r) if m]
    if len(hits) != 1:
        return summ, ["RUNNING_HZ_ROW_MISSING_or_DUPLICATE=%d" % len(hits)]
    g = hits[0].groupdict()
    if summ["state_num"] != RUNNING_REQUIRED_STATE:
        bad.append("state=%d(要求 MOTOR_CRUISE=%d)" % (summ["state_num"], RUNNING_REQUIRED_STATE))
    if summ["armed"] != 1:
        bad.append("armed=%d(要求 1)" % summ["armed"])
    if int(g["cur"]) <= 0:
        bad.append("cur_hz=%s(没在动)" % g["cur"])
    if int(g["tgt"]) <= 0:
        bad.append("tgt_hz=%s(没在动)" % g["tgt"])
    if g["cur"] != g["tgt"]:
        bad.append("cur_hz=%s!=tgt_hz=%s(还在爬坡/减速, 不算巡航)" % (g["cur"], g["tgt"]))
    if summ["drv_request"] != "HIGH" or summ["pad"] != 1:
        bad.append("drv_request=%s/pad=%d(与武装运行不相容)" % (summ["drv_request"], summ["pad"]))
    if summ["expected_ENN"] != "LOW" or summ["enn_fb"] != 0:
        bad.append("expected_ENN=%s/ENN_fb=%d(要求 LOW/0: 硬件没让动就不是正常巡航)" %
                   (summ["expected_ENN"], summ["enn_fb"]))
    if summ["fb_state"] != "resolved" or summ["verdict"] != "OK":
        bad.append("fb=%s/verdict=%s(要求 resolved/OK; CUT_BY_HARDWARE 与 UNRESOLVED 都不放行)" %
                   (summ["fb_state"], summ["verdict"]))
    if summ["health"] != "OK":
        bad.append("health=%s" % summ["health"])
    want = st.get("expect_running") or {}
    for k in ("dir", "cur", "tgt", "accel", "decel"):
        if k in want and str(want[k]) != g[k]:
            bad.append("expect_running.%s=%s 而响应是 %s" % (k, want[k], g[k]))
    return summ, bad


def disarm_success_evidence(rows):
    """F3: 只认产品那条 `[MOT] DISARM (动作已做: ... / 许可 LOW verified / ...)`,
    且许可那一路必须 verified; 子串 "DISARM" 不算。"""
    hits = [m for m in (re.match(DISARM_DONE_ROW, r) for r in rows if r) if m]
    if len(hits) != 1:
        return False, "DISARM_DONE_ROW_MISSING_or_DUPLICATE=%d" % len(hits)
    if hits[0].group("grant") != "verified":
        return False, "许可 LOW %s(未 verified, 禁能动作没证得)" % hits[0].group("grant")
    return True, ""


'''
edit("F4_F3_HELPERS", OLD_TAIL_ANCHOR, NEW_HELPERS + OLD_TAIL_ANCHOR)

# ================================================================= F1. 作业合同
JOB_HELPERS = '''# ------------------------------------------------- F1(复审 P1~P5): 作业轮询先查合同, 再决定要不要等
# 状态名/归属/原因都来自产品源码, 不是本轮自造:
#   diag_relative.h:353-358  RUNNING / CANCEL_PENDING / DONE / FAILED / CANCELLED / TIMEOUT
#   diag_relative.h:365-367  REL_CAP_FREE=0 / BY_ZERO=1 / BY_JOB=2
#   diag_relative.c:1372 REL_JOB_WHY_RUN "RUNNING" / :1674 "CANCEL_REQUESTED"
#   diag_relative.c:1760  DONE 的 why 是 "COMPLETE"; :1690 终态把 why 落成传入值(CANCELLED 同路)
# cred 两位 = zero_cred/learn_cred(diag_relative.c:2042-2044 的 cred=%d/%d)。
JOB_CAP_ENUM = {"FREE": 0, "BY_ZERO": 1, "BY_JOB": 2}
# 产品固定行形(diag_relative.c:2042 主行 / :2050 进度行)。F1 要的是"固定格式解析",
# 所以这两条**写在执行器里**当基准, 计划里的 row 只允许与它们一致(由 r7f8f_plan_gates 核)。
JOB_ROW = (r"^\\[DIAG\\] job id=(?P<id>[0-9]+) st=(?P<st>[A-Z_]+) why=(?P<why>\\S+) "
           r"owner=(?P<owner>[0-9]+) cred=(?P<cred>[0-9]/[0-9])$")
N_ROW = (r"^\\[DIAG\\] job n=(?P<adc>[0-9]+)/(?P<sg>[0-9]+) want=(?P<want>[0-9]+) "
         r"win=(?P<lo>[0-9]+)\\.\\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")
JOB_CONTRACT_TABLE = {
    "RUNNING": {"owner": "2", "cred": "1/0", "why": "RUNNING"},
    "CANCEL_PENDING": {"owner": "2", "cred": "1/0", "why": "CANCEL_REQUESTED"},
    "DONE": {"owner": "0", "cred": "1/1", "why": "COMPLETE"},
    "CANCELLED": {"owner": "0", "cred": "1/0", "why": "CANCELLED"},
}
JOB_TERMINAL_FAILURE = ("FAILED", "TIMEOUT")
JOB_PHASE_STATES = {
    "pre_cancel": ("RUNNING",),               # 取消之前出现 CANCEL_PENDING 就是矛盾
    "post_cancel": ("RUNNING", "CANCEL_PENDING", "CANCELLED"),
    "done": ("RUNNING", "CANCEL_PENDING", "DONE"),
}
JOB_DATA_ROW = (r"^\\[DIAG\\] job data adc\\[(?P<amin>[0-9]+)\\.\\.(?P<amax>[0-9]+)\\]n(?P<an>[0-9]+)"
                r" nz(?P<anz>[0-9]+) sg\\[(?P<smin>[0-9]+)\\.\\.(?P<smax>[0-9]+)\\]n(?P<sn>[0-9]+)"
                r" seq(?P<qf>[0-9]+)\\.\\.(?P<ql>[0-9]+)$")
JOB_THR_ROW = (r"^\\[DIAG\\] job thr tier=(?P<tier>[0-9]+) dev=(-?[0-9]+)/(-?[0-9]+)"
               r" drop=[0-9]+/[0-9]+ FIELD_UNVERIFIED$")
JOB_DONE_RESULT_ROW = (r"^\\[DIAG\\] job DONE result; cred zero=(?P<zero>[0-9]) learn=(?P<learn>[0-9])"
                       r" commit is separate$")


def _one_row(rows, pat, what):
    hits = [m for m in (re.match(pat, r) for r in rows if r) if m]
    if len(hits) != 1:
        return None, "%s_ROW_MISSING_or_DUPLICATE=%d" % (what, len(hits))
    return hits[0], ""


def job_contract(st, txt, bindings, led, asname, attempt=1):
    """F1: 每次查询都先把**这一行自己**的合同判完, 再决定"还在进行中 => 继续等"。

    修前的门只看 `st=` 在不在申报集合里, 于是 owner=FREE 的 RUNNING、want 与受理值不符、
    方向不符、计数倒退、DONE 没有候选输出(P1~P5)都被当成"还没到终态"等过去, 甚至被后面
    那条 DONE 洗绿。这里任何一项在**首次出现**就具名停止; 计数用无符号实值解析, 且
    **不**设 count<=N 的假上限(rel_cap_feed 不在单源达到 N 时停采, 较快源可以超过 N)。"""
    spec = st.get("job_contract")
    if not spec:
        # 合同**默认生效**: 只要这一步带着作业绑定(轮询的 job_binding / 状态断言的
        # expect_values[*].job_binding / 本步自己的 job_bind), 就先判完合同再决定等不等。
        # 计划里的 job_contract 只是额外申报 phase, 不是开关 —— 否则旧计划形状(复审的
        # P1~P5 都不申报)整条绕过, 这道门就只管新计划, 那正是任务书 §3.3 不许的做法。
        bn = (st.get("poll") or {}).get("job_binding")
        if not bn:
            for sp in st.get("expect_values") or []:
                if sp.get("job_binding"):
                    bn = sp["job_binding"]
                    break
        if not bn:
            bn = (st.get("job_bind") or {}).get("name")
        if not bn or bn not in bindings:
            return
        spec = {"binding": bn, "phase": "done"}
    name = spec["binding"]
    if name not in bindings:
        raise Stop("JOB_UNBOUND", "as=%s 作业合同引用未绑定的 %s" % (asname, name))
    b = bindings[name]
    rows = _rows_of(txt)
    main, why = _one_row(rows, JOB_ROW, "job id")
    if why:
        raise Stop("JOB_CONTRACT_ROW_MISSING", "as=%s %s(第 %d 次查询)" % (asname, why, attempt))
    g = main.groupdict()
    nrow, why = _one_row(rows, N_ROW, "job n")
    if why:
        raise Stop("JOB_CONTRACT_ROW_MISSING", "as=%s %s(第 %d 次查询)" % (asname, why, attempt))
    ng = nrow.groupdict()
    bad = []
    if int(g["id"]) != b["job_id"]:
        raise Stop("JOB_ID_MISMATCH", "as=%s 行 id=%s 但本轮绑定=%s(来自 %s)" %
                   (asname, g["id"], b["job_id"], b["from_step"]))
    stt = g["st"]
    if stt in JOB_TERMINAL_FAILURE:
        raise Stop("JOB_STATE_TERMINAL_FAILURE", json.dumps(
            {"as": asname, "binding": name, "job_id": b["job_id"], "st": stt,
             "why": g["why"], "attempt": attempt}, ensure_ascii=False))
    if stt not in JOB_CONTRACT_TABLE:
        raise Stop("JOB_STATE_UNKNOWN", "as=%s st=%s 不在产品状态集合 %s" %
                   (asname, stt, sorted(JOB_CONTRACT_TABLE) + list(JOB_TERMINAL_FAILURE)))
    want = JOB_CONTRACT_TABLE[stt]
    for k in ("owner", "cred", "why"):
        if g[k] != want[k]:
            bad.append("%s=%s(%s 要求 %s)" % (k, g[k], stt, want[k]))
    phase = spec.get("phase", "done")
    allowed = JOB_PHASE_STATES.get(phase)
    if allowed is None:
        raise Stop("JOB_PHASE_UNKNOWN", "as=%s phase=%s" % (asname, phase))
    if stt not in allowed:
        bad.append("phase=%s 不允许状态 %s(允许 %s)" %
                   (phase, stt, "/".join(allowed)))
    acc = b.get("accepted") or {}
    for k, col in (("n", "want"), ("lo", "lo"), ("hi", "hi"), ("dir", "dir")):
        if k in acc and str(acc[k]) != ng[col]:
            bad.append("%s=%s 与本作业**受理行**的 %s=%s 不符" % (col, ng[col], k, acc[k]))
    prev = b.get("counts")
    if prev is not None:
        for k, col in (("adc", "adc"), ("sg", "sg")):
            if int(ng[col]) < int(prev[col]):
                bad.append("%s 计数从 %s 倒退到 %s(同一作业不可倒退)" % (col, prev[col], ng[col]))
    b["counts"] = dict(ng)
    led.ev("JOB_CONTRACT", **{"as": asname, "binding": name, "attempt": attempt, "st": stt,
                              "owner": g["owner"], "cred": g["cred"], "why": g["why"],
                              "adc": ng["adc"], "sg": ng["sg"], "want": ng["want"],
                              "phase": phase, "bad": bad})
    if bad:
        raise Stop("JOB_CONTRACT_VIOLATED", json.dumps(
            {"as": asname, "binding": name, "job_id": b["job_id"], "st": stt,
             "attempt": attempt, "bad": bad}, ensure_ascii=False))
    if stt == "DONE":
        # 两行状态就宣告"可提交"是不够的: 产品只在 state==DONE 且 cand_valid 时才打
        # job thr/job DONE result(diag_relative.c:2068-2076), job data 要 has_data(:2056)。
        d, why = _one_row(rows, JOB_DATA_ROW, "job data")
        t, why2 = _one_row(rows, JOB_THR_ROW, "job thr")
        r, why3 = _one_row(rows, JOB_DONE_RESULT_ROW, "job DONE result")
        missing = [x for x in (why, why2, why3) if x]
        if missing:
            raise Stop("JOB_DONE_CANDIDATE_EVIDENCE_MISSING", json.dumps(
                {"as": asname, "binding": name, "attempt": attempt, "missing": missing,
                 "basis": "diag_relative.c:2056/2068/2074"}, ensure_ascii=False))
        dg = d.groupdict()
        cbad = []
        if int(dg["an"]) != int(ng["adc"]) or int(dg["sn"]) != int(ng["sg"]):
            cbad.append("候选统计 adc/sg=%s/%s 与进度行 n=%s/%s 不等" %
                        (dg["an"], dg["sn"], ng["adc"], ng["sg"]))
        w = int(ng["want"])
        if int(ng["adc"]) < w or int(ng["sg"]) < w:
            cbad.append("DONE 但某一路计数 %s/%s 低于受理的 want=%s" %
                        (ng["adc"], ng["sg"], ng["want"]))
        if int(dg["amin"]) > int(dg["amax"]) or int(dg["smin"]) > int(dg["smax"]):
            cbad.append("统计范围倒挂 adc[%s..%s] sg[%s..%s]" %
                        (dg["amin"], dg["amax"], dg["smin"], dg["smax"]))
        if int(dg["qf"]) > int(dg["ql"]):
            cbad.append("SG 新样本序号倒挂 seq%s..%s" % (dg["qf"], dg["ql"]))
        if int(dg["sn"]) <= 0:
            cbad.append("SG 序号区间存在但样本数=0(把同一枚旧样本当新样本)")
        rg = r.groupdict()
        if (rg["zero"], rg["learn"]) != (g["cred"].split("/")[0], g["cred"].split("/")[1]):
            cbad.append("job DONE result 的 cred=%s/%s 与主行 cred=%s 不符" %
                        (rg["zero"], rg["learn"], g["cred"]))
        if cbad:
            raise Stop("JOB_DONE_CANDIDATE_INCONSISTENT", json.dumps(
                {"as": asname, "binding": name, "attempt": attempt, "bad": cbad},
                ensure_ascii=False))
        led.ev("JOB_DONE_EVIDENCE", **{"as": asname, "binding": name, "adc_n": dg["an"],
                                       "sg_n": dg["sn"], "sg_seq": "%s..%s" % (dg["qf"], dg["ql"]),
                                       "tier": t.group("tier"), "cred": g["cred"]})


'''
JOB_ANCHOR = '''def poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture, silent,
                      led, clock, budget, results, bindings=None):
'''
edit("F1_JOB_CONTRACT", JOB_ANCHOR, JOB_HELPERS + JOB_ANCHOR)

edit("F1_POLL_CALL",
     '''        results.append(dict({"as": asname, "kind": "command_poll", "cmd": cmd, "attempt": n,
                             "poll_gate": gate,
                             "rx_bytes": nbytes, "rx_sha256": sha_of(raw), "txt": txt}, **v))
        if v["verdict"] == "OK":
''',
     '''        results.append(dict({"as": asname, "kind": "command_poll", "cmd": cmd, "attempt": n,
                             "poll_gate": gate,
                             "rx_bytes": nbytes, "rx_sha256": sha_of(raw), "txt": txt}, **v))
        # F1: 合同排在"要不要继续等"之前。响应里只要有 [DIAG] job id= 行就判完; 一条业务拒绝行
        # (learn_status REFUSED: ...)根本没这行, 交给下面的 verdict 门给准确身份, 不在这伪装成缺行。
        if v["verdict"] == "OK" or any(r.startswith("[DIAG] job id=") for r in _rows_of(txt) if r):
            job_contract(st, txt, bindings, led, asname, n)
        if v["verdict"] == "OK":
''')

# ---------------------------------------------------------------- F1/F2: 绑定与步骤侧调用
edit("F1_BIND_CMD",
     '''            bind_job_id(st, txt, raw, bindings, led, asname)
            check_job_expect(st, txt, bindings, asname)
            if cmd == "motor_disarm" and energize.armed:
                led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                       cap_seconds=energize.cap)
''',
     '''            bind_job_id(st, txt, raw, bindings, led, asname, cmd)
            check_job_expect(st, txt, bindings, asname)
            if st.get("job_contract") and not st.get("poll"):
                job_contract(st, txt, bindings, led, asname)
            if cmd == "motor_disarm" and energize.armed:
                # F3: 这条命令判 OK 之后还只算"动作已做且许可路 verified" => 把义务**转成待快照确认**,
                # 关闭要靠相邻那份**新的** motor_status 禁能合同(清理里那份同样可以)。
                okd, whyd = disarm_success_evidence(_rows_of(txt))
                if not okd:
                    raise Stop("DISARM_SUCCESS_ROW_UNPROVEN",
                               "as=%s %s => 不解除通电义务" % (asname, whyd))
                energize.await_disable_snapshot = True
                led.ev("DISARM_ACTION_DONE", **{"as": asname, "await": "motor_status"})
            if cmd == "motor_status" and energize.await_disable_snapshot:
                summ2, bad2 = disable_contract(_rows_of(txt))
                if bad2:
                    led.ev("DISARM_SNAPSHOT_NOT_PROVEN", **{"as": asname, "bad": bad2})
                else:
                    led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                           cap_seconds=energize.cap, basis="motor_status_step")
                    energize.await_disable_snapshot = False
''')

edit("F1_BIND_PARAMS",
     '''def bind_job_id(st, txt, raw, bindings, led, asname):
''',
     '''def bind_job_id(st, txt, raw, bindings, led, asname, cmd=None):
''')

edit("F1_BIND_SENT",
     '''    if name in bindings and bindings[name]["job_id"] != jid:
        raise Stop("JOB_BIND_DUPLICATE",
                   "name=%s 已绑到 %s(来自 %s), 本步又要绑成 %d => 不覆盖" %
                   (name, bindings[name]["job_id"], bindings[name]["from_step"], jid))
    bindings[name] = {"job_id": jid, "from_step": asname, "row": hits[0].string.strip(),
                      "rx_sha256": sha_of(raw), "params": dict(g)}
    led.ev("JOB_ID_BOUND", name=name, job_id=jid, source_step=asname,
           rx_sha256=sha_of(raw), row=bindings[name]["row"])
''',
     '''    if cmd:
        toks = str(cmd).split()
        sent = dict(zip(("n", "lo", "hi", "dir"), toks[2:6])) if len(toks) >= 6 else {}
        sc = {k: (sent.get(k), g.get(k)) for k in sent
              if k in ("n", "lo", "hi", "dir") and str(sent[k]) != str(g.get(k))}
        if sc:
            raise Stop("JOB_ACCEPTED_NOT_SENT",
                       "as=%s name=%s 实发 %s 与产品受理行不符 %s" % (asname, name, cmd, sc))
    if name in bindings and bindings[name]["job_id"] != jid:
        raise Stop("JOB_BIND_DUPLICATE",
                   "name=%s 已绑到 %s(来自 %s), 本步又要绑成 %d => 不覆盖" %
                   (name, bindings[name]["job_id"], bindings[name]["from_step"], jid))
    bindings[name] = {"job_id": jid, "from_step": asname, "row": hits[0].string.strip(),
                      "rx_sha256": sha_of(raw), "params": dict(g), "accepted": dict(g),
                      "counts": None}
    led.ev("JOB_ID_BOUND", name=name, job_id=jid, source_step=asname,
           accepted={k: g.get(k) for k in ("n", "lo", "hi", "dir")},
           rx_sha256=sha_of(raw), row=bindings[name]["row"])
''')

# ================================================================= F2/F3. 截止与单次 arm
edit("F2_ENERGIZE_FIELDS",
     '''    def __init__(self, clock, cap_seconds):
        self.clock = clock
        self.cap = float(cap_seconds) if cap_seconds else None
        self.t0 = None
        self.actual = None
''',
     '''    def __init__(self, clock, cap_seconds):
        self.clock = clock
        self.cap = float(cap_seconds) if cap_seconds else None
        self.t0 = None
        self.actual = None
        # F3: 本场第几次 arm 写入尝试, 以及"已发过 disarm、等一份新的禁能快照来关闭义务"。
        self.arm_attempts = 0
        self.await_disable_snapshot = False
''')

edit("F2_ENERGIZE_ARM_ATTEMPT",
     '''    def start_attempt(self):
        if self.cap is not None and self.t0 is None:
            self.t0 = self.clock.monotonic()
''',
     '''    def start_attempt(self):
        self.arm_attempts += 1
        if self.cap is not None and self.t0 is None:
            self.t0 = self.clock.monotonic()
''')

ARM_GATE_HELPER = '''def arm_attempt_gate(energize, led, clock, asname, cmd):
    """F2+F3: 在 arm 的**写入尝试之前**建立通电计时义务, 并在这一步就拒绝第二次 arm。

    修前的顺序是"先按申报秒数算 step_dl, 再在写之前 start_attempt", 于是 arm 那条命令自己的
    首次回读/排残留/等待都没被新成立的截止约束(复审 P6: cap 2 s 而回包阻塞 3 s 照样 OK)。
    现在义务先立, 截止后算, 且 disarm 之后再来一条 arm 也在写之前拒(复审 P7)。"""
    if cmd == "motor_arm":
        if energize.cap is None:
            raise Stop("ARM_ENERGIZE_DEADLINE_UNDECLARED",
                       "as=%s 阶段没申报 arm_energize_deadline_seconds => 不发 motor_arm" % asname)
        if energize.arm_attempts >= 1:
            raise Stop("ARM_SECOND_ATTEMPT_REFUSED", json.dumps(
                {"as": asname, "attempts_so_far": energize.arm_attempts,
                 "first_from_t0_s": round(clock.monotonic() - energize.t0, 3)
                 if energize.t0 is not None else None,
                 "closed": energize.actual is not None,
                 "note": "本场只允许一次 arm(任务书 §2 F3); 第二次在写入尝试之前拒"},
                ensure_ascii=False))
        energize.start_attempt()
        led.ev("ENERGIZE_OBLIGATION_SET", cap_seconds=energize.cap,
               t_s=round(clock.monotonic() - led.t0, 3), before="step_deadline")


'''
edit("F2_ARM_GATE",
     '''def exec_session(session_id, steps, outdir, transport, fixture, port_name,
''',
     ARM_GATE_HELPER + '''def exec_session(session_id, steps, outdir, transport, fixture, port_name,
''')

edit("F2_DEADLINES",
     '''            # ---- 业务动作一律受"起转之后的剩余预算"约束 ----
            want = int(st.get("seconds", 3))
            step_dl = budget.business_deadline(want) if (cmd == "__watch__" or budget.armed) \\
                else min(clock.monotonic() + want + SLACK_SECONDS,
                         clock.monotonic() + total_deadline_seconds)
            if energize.armed:
                # 申报的等待窗要兑现, 但取会话/通电/运动三者最早的那个截止, 不在 read 或 sleep 里放宽。
                step_dl = min(step_dl, energize.deadline())
                if energize.expired():
                    raise Stop("ARM_ENERGIZE_DEADLINE_EXCEEDED", json.dumps(
                        {"as": asname, "want_seconds": want, "cap_seconds": energize.cap,
                         "elapsed_s": round(clock.monotonic() - energize.t0, 3)},
                        ensure_ascii=False))
''',
     '''            # ---- F2: 义务先立, 截止后算; 会话截止是**绝对**的 t0+总时限, 不逐步续期 ----
            arm_attempt_gate(energize, led, clock, asname, cmd)
            want = int(st.get("seconds", 3))
            step_dl = min(clock.monotonic() + want + SLACK_SECONDS, session_abs_dl)
            if cmd == "__watch__" or budget.armed:
                step_dl = min(step_dl, budget.business_deadline(want))
            if energize.armed:
                step_dl = min(step_dl, energize.deadline())
                if energize.expired():
                    raise Stop("ARM_ENERGIZE_DEADLINE_EXCEEDED", json.dumps(
                        {"as": asname, "want_seconds": want, "cap_seconds": energize.cap,
                         "elapsed_s": round(clock.monotonic() - energize.t0, 3),
                         "checked_before_write": True}, ensure_ascii=False))
            if step_dl <= clock.monotonic():
                raise Stop("STEP_DEADLINE_BEFORE_WRITE", json.dumps(
                    {"as": asname, "want_seconds": want,
                     "remaining_s": round(step_dl - clock.monotonic(), 3),
                     "session_remaining_s": round(session_abs_dl - clock.monotonic(), 3),
                     "energize_remaining_s": energize.remaining()}, ensure_ascii=False))
''')

edit("F2_SESSION_ABS",
     '''    bindings = {}
    energize = EnergizeBudget(clock, energize_cap_seconds)
''',
     '''    # F2: 会话绝对截止 = 台账 t0 + 总时限。修前每步用"当前时刻 + 总时限", 相当于无上限续期。
    session_abs_dl = clock.monotonic() + float(total_deadline_seconds)
    bindings = {}
    energize = EnergizeBudget(clock, energize_cap_seconds)
''')

edit("F3_OBLIGATION_BLOCK",
     '''                if cmd == "motor_arm":
                    # E4: arm 时限缺失/为 0 就在**真实写之前**具名拒绝, 不允许"arm 了但没人管通电时长"。
                    if energize.cap is None:
                        raise Stop("ARM_ENERGIZE_DEADLINE_UNDECLARED",
                                   "as=%s 阶段没申报 arm_energize_deadline_seconds => 不发 motor_arm" %
                                   asname)
                    energize.start_attempt()
                    led.ev("ENERGIZE_OBLIGATION_SET", cap_seconds=energize.cap,
                           t_s=round(clock.monotonic() - led.t0, 3))
''',
     '''                # F2/F3: arm 的计时义务与第二次拒绝都在 arm_attempt_gate() 里(算 step_dl 之前),
                # 这里只留"起转"那一支的运动预算起点。
                if cmd == "motor_start":
                    start_attempts += 1
''')

edit("F3_START_COUNTER",
     '''                obligation = True
                if cmd == "motor_start":
                    budget.start_attempt()
''',
     '''                obligation = True
                if cmd == "motor_start":
                    # F3: 本场只允许一次 start。计数在**写之前**加, 预算起点也在那一刻。
                    if start_attempts >= 1:
                        raise Stop("MOTOR_START_SECOND_REFUSED",
                                   "as=%s 本场已尝试过 %d 次 motor_start" % (asname, start_attempts))
                    budget.start_attempt()
''')

edit("F3_STATE_INIT",
     '''    cleanup_overruns = []
    reservations = []
''',
     '''    cleanup_overruns = []
    reservations = []
    start_attempts = 0
    irq_attempted = False          # F5: 只记"是否**尝试过** safety_irq_enable", 决定固定尾部走不走
''')

edit("F5_IRQ_TRACK",
     '''            # ---- R6A §4: 命令 -> 额度键的交叉核对, 缺声明/错键/n 不符都在这条 write 之前拒 ----
            need_key = quota_key_for_command(cmd)
''',
     '''            if cmd.split(" ")[0] == "safety_irq_enable" and not irq_attempted:
                # F5: 尾部由"是否尝试过开 IRQ"决定, 在写之前置位 => 连写失败也要收口。
                irq_attempted = True
                led.ev("IRQ_ENABLE_ATTEMPTED", **{"as": asname})
            # ---- R6A §4: 命令 -> 额度键的交叉核对, 缺声明/错键/n 不符都在这条 write 之前拒 ----
            need_key = quota_key_for_command(cmd)
''')

# ================================================================= F5. 固定收口 + IRQ 尾部
edit("F5_TAIL_CONST",
     '''CLEANUP_CMDS = ("motor_stop", "motor_disarm", "motor_status")
CLEANUP_WINDOW_SECONDS = 1.2   # 每条清理命令自己的确认窗(三段互不抢占)
''',
     '''CLEANUP_CMDS = ("motor_stop", "motor_disarm", "motor_status")
# F5(任务书 §2 明示增加的范围): 只在**尝试过** safety_irq_enable 的场里, 三条之后同一端口内
# 再发这两条; 各一次、各占自己的预留额度, 失败不阻断前面的撤能, 也不关闭第二次端口。
IRQ_TAIL_CMDS = ("safety_irq_disable", "safety_irq_status")
# 产品闭合态那一条来自 safety_thread.c:306 的 `IRQ gate CLOSED - 活动安全输入…无软件响应路径`
# (大写 CLOSED; 修前我这正则写的是小写 closed, 那样**永远匹配不上**, 尾部就成了恒红)。
IRQ_TAIL_ROW = r"^\\[SAFETY\\] IRQ gate CLOSED"
CLEANUP_WINDOW_SECONDS = 1.2   # 每条清理命令自己的确认窗(五条互不抢占)
''')

edit("F5_CLEANUP_SIG",
     '''def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None,
            quota_box=None, overruns=None, used_box=None, approval=None, reservations=None):''',
     '''def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None,
            quota_box=None, overruns=None, used_box=None, approval=None, reservations=None,
            energize=None, irq_attempted=False):''')

edit("F5_CLEANUP_TAIL",
     '''    if bad:
        errors.append("DISARM_NOT_CONFIRMED " + " ".join(bad))
    else:
        state = "CONFIRMED_DISABLED"
    # 三条必要撤能已经发完、禁能判据已经算完, 现在才回头尽力落证。失败也只是多一条披露:
''',
     '''    if bad:
        errors.append("DISARM_NOT_CONFIRMED " + " ".join(bad))
    else:
        state = "CONFIRMED_DISABLED"
        if energize is not None and energize.await_disable_snapshot:
            led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                   cap_seconds=energize.cap, basis="cleanup_status")
            energize.await_disable_snapshot = False
    # F5: 固定 IRQ 尾部排在**禁能判据算完之后** —— 尾部任何失败只追加具名披露,
    # 绝不回头改上面已经成立的 state, 也不吃掉撤能(任务书 §2: 尾部失败不能阻断前面的撤能)。
    if irq_attempted:
        for cmd in IRQ_TAIL_CMDS:
            dl = min(hard, clock.monotonic() + CLEANUP_WINDOW_SECONDS)
            if clock.monotonic() >= hard:
                errors.append("IRQ_TAIL_HARD_DEADLINE_BEFORE_" + cmd)
                continue
            key = "cleanup_" + cmd
            if quota_box is not None:
                if key not in quota_box:
                    overruns.append(key + ":RESERVE_NOT_DECLARED")
                else:
                    if approval is not None:
                        try:
                            res = approval.reserve(key, 1, kind="cleanup")
                            if reservations is not None:
                                reservations.append(res)
                        except Stop as e:
                            overruns.append("%s:%s_SENT_ANYWAY" % (key, e.code))
                        except Exception as e:
                            overruns.append("%s:APPROVAL_RESERVE_FRAME_ERROR_%s_SENT_ANYWAY"
                                            % (key, e.__class__.__name__))
                    if quota_box[key] <= 0:
                        overruns.append(key + ":RESERVE_EXHAUSTED_SENT_ANYWAY")
                    else:
                        quota_box[key] -= 1
                        if used_box is not None:
                            used_box[key] = used_box.get(key, 0) + 1
            try:
                trows, _, _, _, terrs = _cleanup_read(port, led, clock, dl, cmd,
                                                      out_dir, feed_hook)
            except Exception as e:
                errors.append("IRQ_TAIL_RAISED_%s:%s" % (cmd, e.__class__.__name__))
                continue
            errors.extend("IRQ_TAIL_" + x for x in terrs)
            if cmd == "safety_irq_status" and not any(
                    re.match(IRQ_TAIL_ROW, r) for r in trows if r):
                errors.append("IRQ_TAIL_NOT_CLOSED")
                led.ev("IRQ_TAIL_STATE", closed=False, rows=len(trows))
            elif cmd == "safety_irq_status":
                led.ev("IRQ_TAIL_STATE", closed=True, rows=len(trows))
    # 三条必要撤能已经发完、禁能判据已经算完, 现在才回头尽力落证。失败也只是多一条披露:
''')

edit("F5_CLEANUP_CALL",
     '''                disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture,
                                             hook, quota if plan else None,
                                             cleanup_overruns, used,
                                             approval=approval, reservations=reservations)''',
     '''                disarm_state, cerr = cleanup(port, led, clock, budget, transport, fixture,
                                             hook, quota if plan else None,
                                             cleanup_overruns, used,
                                             approval=approval, reservations=reservations,
                                             energize=energize, irq_attempted=irq_attempted)''')

# ==================================================================== F5b. 操作者实测的两条漏项
# 操作者 2026-11-05 实测确认(并入同一编号, 不另开轮):
#   ① "开 IRQ 后、arm 前失败, 会漏掉 IRQ 收口。当前 finally 只看电机 obligation。"
#   ② "五条清理仍沿用默认 4 秒, 没有落实 7 秒……最后的 safety_irq_status 被
#      IRQ_TAIL_HARD_DEADLINE_BEFORE_safety_irq_status 跳过。"
# 下面 15 条逐条对应这两项, 每项都要在**矩阵里**同时有一条正向(补齐后各条都发)和一条
# 反向(申报排不下 / 截止夹短)——只加常量不判红, 等于没修。

edit("F5B_RESERVE_CONST",
     '''CLEANUP_RESERVE_SECONDS = 4          # 起转硬截止之后留给 stop+disarm+复核的时间
''',
     '''# F5b(操作者实测第二项): 4 s 排不下**五条**各自的 1.2 s 确认窗(3 条电机 + 2 条 IRQ 尾部
# = 6.0 s), 于是全部无响应时第五条 safety_irq_status 必被 IRQ_TAIL_HARD_DEADLINE_BEFORE_*
# 跳过。本轮任务书 §5 明写"会话 90 s 含 7 s 收口预留", 这里落实成申报值 7 s;
# exec_session 开口前用 CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS 拒掉任何 < 6.0 s 的申报。
CLEANUP_RESERVE_SECONDS = 7          # 起转硬截止之后留给 stop+disarm+复核+IRQ 尾部的时间
''')

edit("F5B_BUDGET_INIT",
     '''    def __init__(self, clock, cap_seconds, reserve=CLEANUP_RESERVE_SECONDS):
        self.clock = clock
        self.cap = float(cap_seconds)
        self.reserve = float(reserve)
        self.t0 = None
        self.actual = None
''',
     '''    def __init__(self, clock, cap_seconds, reserve=CLEANUP_RESERVE_SECONDS):
        self.clock = clock
        self.cap = float(cap_seconds)
        self.reserve = float(reserve)   # F5b: 由会话/阶段**申报**, 不再只靠模块常量
        self.t0 = None
        self.actual = None
        # F5b: 会话绝对截止(台账 t0 + 总时限)。cleanup_deadline() 用它把收口再夹一次,
        # 落实"7 s 预留仍受会话总截止约束"——预留不是把会话无上限拉长的手段。
        self.session_abs_dl = None
''')

edit("F5B_CLEANUP_DEADLINE",
     '''    def cleanup_deadline(self):
        return (self.t0 + self.cap + self.reserve) if self.t0 is not None \\
            else self.clock.monotonic() + self.reserve
''',
     '''    def cleanup_deadline(self):
        """收口绝对截止 = 起转硬截止 + **申报的**收口预留, 再被会话总截止夹一次。

        F5b 两项都在这里落地: 预留取申报值(本轮 7 s, 足够五条各 1.2 s 窗), 会话总截止仍然
        封顶(不因为申报了预留就把 90 s 放宽)。被夹短到排不下时不静默少发——cleanup() 入口
        先具名披露 CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE, 每条自己的窗到点也照旧具名。
        """
        base = (self.t0 + self.cap + self.reserve) if self.t0 is not None \\
            else self.clock.monotonic() + self.reserve
        if self.session_abs_dl is not None:
            return min(base, self.session_abs_dl)
        return base
''')

# ---- ① IRQ 尝试自己建立收口义务(与电机义务分列) ----
edit("F5B_CLEANUP_SIG2",
     '''def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None,
            quota_box=None, overruns=None, used_box=None, approval=None, reservations=None,
            energize=None, irq_attempted=False):''',
     '''def cleanup(port, led, clock, budget, transport, fixture, feed_hook=None,
            quota_box=None, overruns=None, used_box=None, approval=None, reservations=None,
            energize=None, irq_attempted=False, motor_obligation=True):''')

edit("F5B_MOTOR_SENDS",
     '''    hard = budget.cleanup_deadline()
    out_dir = led.out
    status_rows = []
    for cmd in CLEANUP_CMDS:
''',
     '''    hard = budget.cleanup_deadline()
    out_dir = led.out
    status_rows = []
    # F5b①: 电机那三条只由**电机义务**决定发不发。"先开 IRQ、随后失败"这一类场里电机从未
    # 武装, 那三条与本场义务无关(发它们既花额度又造出无关响应), 但 IRQ 尾部**必须**照发。
    sends = list(CLEANUP_CMDS) if motor_obligation else []
    chances = len(sends) + (len(IRQ_TAIL_CMDS) if irq_attempted else 0)
    # F5b②: 会话总截止把预留夹短到排不下本场需要的全部发送窗时, 在**第一条清理命令之前**
    # 就把这件事具名披露出来。修前只有第 N 条 *_HARD_DEADLINE_BEFORE_* 才露一手, 四条之内
    # 看不出"第五条本来就来不及"——那是事后归因, 不是事前可见。
    need_windows = chances * CLEANUP_WINDOW_SECONDS
    left = hard - clock.monotonic()
    if left < need_windows:
        errors.append("CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE " + json.dumps(
            {"left_seconds": round(max(0.0, left), 3),
             "required_seconds": round(need_windows, 3), "send_chances": chances,
             "reserve_seconds": budget.reserve,
             "session_abs_deadline_set": budget.session_abs_dl is not None},
            ensure_ascii=False))
    # 收口起点具名入台账: 之后每一条 TX 才**可判定**属于收口还是业务(判据不能靠命令名猜——
    # 业务里也允许发 motor_status, 例如 disarm 后的相邻禁能快照)。
    _ev_or_defer(led, deferred_ev, "CLEANUP_ENTERED", motor_obligation=bool(motor_obligation),
                 irq_attempted=bool(irq_attempted), send_chances=chances,
                 reserve_seconds=budget.reserve, hard_deadline=round(hard, 3),
                 left_seconds=round(max(0.0, left), 3))
    for cmd in sends:
''')

edit("F5B_DISABLE_GUARD",
     '''    summary, bad = disable_contract(status_rows)
    _ev_or_defer(led, deferred_ev, "DISARM_FIELDS", fields=summary, unproven=" ".join(bad))
    if bad:
        errors.append("DISARM_NOT_CONFIRMED " + " ".join(bad))
    else:
        state = "CONFIRMED_DISABLED"
        if energize is not None and energize.await_disable_snapshot:
            led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                   cap_seconds=energize.cap, basis="cleanup_status")
            energize.await_disable_snapshot = False
''',
     '''    if not motor_obligation:
        # "不需要禁能"是**已知事实**(电机从未武装 => 终态 KNOWN), 不是"证实了禁能"。
        # 这两个字符串不能互换: 把 NOT_ARMED 写成 CONFIRMED_DISABLED 就是替设备编证据。
        state = "NOT_ARMED"
        _ev_or_defer(led, deferred_ev, "CLEANUP_MOTOR_NOT_OBLIGATED",
                     irq_attempted=bool(irq_attempted), sends=0)
    else:
        summary, bad = disable_contract(status_rows)
        _ev_or_defer(led, deferred_ev, "DISARM_FIELDS", fields=summary, unproven=" ".join(bad))
        if bad:
            errors.append("DISARM_NOT_CONFIRMED " + " ".join(bad))
        else:
            state = "CONFIRMED_DISABLED"
            if energize is not None and energize.await_disable_snapshot:
                led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),
                       cap_seconds=energize.cap, basis="cleanup_status")
                energize.await_disable_snapshot = False
''')

edit("F5B_FINALLY_ENTER",
     '''        if obligation:                     # 不只是"起转成功之后": 潜在已武装同样清理
''',
     '''        # F5b①(操作者实测第一项): 任务书 §2 要的是"**尝试过**开 IRQ 本身就建立尾部清理义务",
        # 与电机义务无关。真实序列正是先 safety_irq_enable 再 motor_arm, 中间任何一步失败(查询
        # 失败/判据红/截止)都落在 `obligation` 还是 False 的窗口里 => 修前一条 disable/status
        # 都不发, IRQ 门留在 OPEN。电机那三条仍按 obligation 走(见 cleanup 的 motor_obligation)。
        if obligation or irq_attempted:    # 潜在已武装**或**已尝试开 IRQ => 都要收口
''')

edit("F5B_FINALLY_CALL",
     '''                                             energize=energize, irq_attempted=irq_attempted)
''',
     '''                                             energize=energize, irq_attempted=irq_attempted,
                                             motor_obligation=obligation)
''')

# ---- ② 预留申报值贯通(会话参数 / CLI / 整包阶段) + 开口前的排不下拒绝 ----
edit("F5B_SESSION_SIG",
     '''                 approved=None, energize_cap_seconds=None):
''',
     '''                 approved=None, energize_cap_seconds=None, cleanup_reserve_seconds=None):
''')

edit("F5B_SESSION_GATE",
     '''    budget = MotionBudget(clock, motion_cap_seconds) if motion_cap_seconds else \\
        MotionBudget(clock, total_deadline_seconds)
''',
     '''    # F5b②: 收口预留取**申报值**(没申报就用本轮常量 7 s)。类型在这里就判, 和
    # PLAN_MANUAL_AFTER_MOTION 同一层: 畸形申报在台账/端口/账本之前拒, 一条命令都不发。
    reserve_declared = CLEANUP_RESERVE_SECONDS if cleanup_reserve_seconds is None \\
        else cleanup_reserve_seconds
    if isinstance(reserve_declared, bool) or \\
            not isinstance(reserve_declared, (int, float)):
        raise Stop("CLEANUP_RESERVE_NOT_A_NUMBER",
                   "cleanup_reserve_seconds=%r(%s)" % (reserve_declared,
                                                       type(reserve_declared).__name__))
    budget = MotionBudget(clock, motion_cap_seconds,
                          reserve=reserve_declared) if motion_cap_seconds else \\
        MotionBudget(clock, total_deadline_seconds, reserve=reserve_declared)
    # F5b②: 收口仍然被会话**绝对**截止封顶(修前 cleanup 只认 t0+cap+reserve, 总时限管不到它)。
    budget.session_abs_dl = session_abs_dl
''')

# 拒绝点放在 `try` 之内、`spend("port_open")` 之前: 台账照样记下这次具名 STOP(可审计),
# 而端口、额度、批准账三处的副作用仍然为零。
edit("F5B_RESERVE_STOP",
     '''    try:
        spend("port_open", 1)               # 先记账再尝试开口(尝试数 == 扣减数)
''',
     '''    try:
        # F5b②: 预留必须容得下**全部**必要发送各自的确认窗(3 条电机 + 2 条 IRQ 尾部 = 5 条
        # × 1.2 s = 6.0 s)。修前的 4 s 常量正是这样静默吃掉第五条 safety_irq_status 的;
        # 现在申报排不下就在第一条命令之前具名拒绝 —— 端口、额度、批准账三处仍然零副作用,
        # 但这次拒绝本身进台账(可审计的"没发生", 而不是事后靠 stdout 抓一行)。
        reserve_floor = (len(CLEANUP_CMDS) + len(IRQ_TAIL_CMDS)) * CLEANUP_WINDOW_SECONDS
        if float(reserve_declared) + 1e-9 < reserve_floor:
            raise Stop("CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS", json.dumps(
                {"reserve_seconds": float(reserve_declared),
                 "required_seconds": round(reserve_floor, 3),
                 "send_chances": len(CLEANUP_CMDS) + len(IRQ_TAIL_CMDS),
                 "window_seconds_each": CLEANUP_WINDOW_SECONDS,
                 "checked_before": "port_open_and_quota"}, ensure_ascii=False))
        spend("port_open", 1)               # 先记账再尝试开口(尝试数 == 扣减数)
''')


edit("F5B_RESULT_RESERVE",
     '''                "budget_seconds": elapsed,
''',
     '''                "budget_seconds": elapsed,
                # F5b: 回执里带上**这一场实际生效**的收口预留与是否被会话截止夹过, 让 7 s
                # 不只是文档里的字: 现场判读能直接看到申报值与绝对截止的差。
                "cleanup_reserve_seconds": budget.reserve,
                "cleanup_hard_deadline": round(budget.cleanup_deadline(), 6),
                "session_abs_deadline": round(session_abs_dl, 6),
''')

edit("F5B_PLAN_GATE",
     '''    out.append("PLAN_MOTION_CAP_BUDGET|all|%s|caps=%s sum=%d total=%d starts=%d" %
               ("PASS" if ok_t else "FAIL", caps, sum(c for _, c in caps), total, starts))
    if not ok_t:
        bad += 1
''',
     '''    out.append("PLAN_MOTION_CAP_BUDGET|all|%s|caps=%s sum=%d total=%d starts=%d" %
               ("PASS" if ok_t else "FAIL", caps, sum(c for _, c in caps), total, starts))
    if not ok_t:
        bad += 1
    # F5b②(离线整包侧, 与 exec_session 里那条同一判据): 预留(a)容得下全部必要发送窗,
    # (b)住在本阶段总窗之内(motion_cap + 预留 <= 总时限)——"仍受会话总截止约束"要在**申报**
    # 阶段成立, 不能等现场清理时才发现排不下。只读盘上申报值, 不发任何命令。
    floor_r = (len(CLEANUP_CMDS) + len(IRQ_TAIL_CMDS)) * CLEANUP_WINDOW_SECONDS

    def _num_or_none(v):
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)

    for sg in plan["stages"]:
        if sg.get("kind") != "serial_session":
            continue
        rs = _num_or_none(sg.get("cleanup_reserve_seconds", CLEANUP_RESERVE_SECONDS))
        td = _num_or_none(sg.get("total_deadline_seconds"))
        mc = _num_or_none(sg.get("motion_cap_seconds"))
        bad_r = []
        if rs is None:
            bad_r.append("cleanup_reserve_seconds 不是数字(=%r)" % (sg.get("cleanup_reserve_seconds"),))
        else:
            if rs + 1e-9 < floor_r:
                bad_r.append("预留=%s < 五条发送窗合计=%.1f" % (rs, floor_r))
            if td is not None and mc is not None and mc + rs > td:
                bad_r.append("motion_cap=%s + 预留=%s > 本阶段总窗=%s(预留排不进会话)"
                             % (mc, rs, td))
        out.append("PLAN_CLEANUP_RESERVE|%s|%s|reserve=%s floor=%.1f total=%s cap=%s%s" %
                   (sg["id"], "PASS" if not bad_r else "FAIL", rs, floor_r, td, mc,
                    "" if not bad_r else "|" + ";".join(bad_r)))
        if bad_r:
            bad += 1
''')

edit("F5B_ARGV",
     '''    ap.add_argument("--arm-energize-deadline-seconds", type=int, default=0,
                    help="E4: motor_arm 之后的通电保持截止(单会话工装入口; 整包按阶段申报)")
''',
     '''    ap.add_argument("--arm-energize-deadline-seconds", type=int, default=0,
                    help="E4: motor_arm 之后的通电保持截止(单会话工装入口; 整包按阶段申报)")
    ap.add_argument("--cleanup-reserve-seconds", type=float, default=0.0,
                    help="F5b: 收口预留(默认 %s s, 必须容得下五条各自的确认窗; 整包按阶段申报)"
                         % CLEANUP_RESERVE_SECONDS)
''')

edit("F5B_MAIN_CALL",
     '''                           energize_cap_seconds=a.arm_energize_deadline_seconds or None,
''',
     '''                           energize_cap_seconds=a.arm_energize_deadline_seconds or None,
                           cleanup_reserve_seconds=a.cleanup_reserve_seconds or None,
''')

edit("F5B_DISPATCH_CALL",
     '''                                   energize_cap_seconds=stg.get("arm_energize_deadline_seconds"),
''',
     '''                                   energize_cap_seconds=stg.get("arm_energize_deadline_seconds"),
                                   cleanup_reserve_seconds=stg.get("cleanup_reserve_seconds"),
''')


# ============================================================================ 应用
def apply(text, edits):
    for name, find, repl in edits:
        n = text.count(find)
        if n != 1:
            raise SystemExit("EDIT_%s_HITS=%d(必须恰好 1) —— %r 的前 60 字节: %s"
                             % (name, n, find[:20], find[:60].replace("\\n", "\\n")))
        text = text.replace(find, repl)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", type=pathlib.Path, help="仓外目录: --verify 在那里重建再比对")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    root = a.repo.resolve()
    pre = (root / PRE_REL).read_text(encoding="utf-8")
    b = (root / PRE_REL).read_bytes()
    if hashlib.sha256(b).hexdigest() != PRE_SHA or len(b) != PRE_BYTES:
        raise SystemExit("PRE_IDENTITY_REFUSED|修前件字节不符(=%d/%s…) 期望 %d/%s…"
                         % (len(b), hashlib.sha256(b).hexdigest()[:12], PRE_BYTES, PRE_SHA[:12]))
    new = apply(pre, EDITS)
    if a.verify:
        if a.work is None:
            raise SystemExit("VERIFY_REFUSED|--verify 需要 --work <仓外目录>")
        out = a.work.resolve() / "r7f8f_rebuild" / "m8r3_serial_chain_r7f8.py"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(new, encoding="utf-8", newline="\n")
        same = out.read_bytes() == (root / NEW_REL).read_bytes()
        print("VERIFY|%s|仓外重建 %d B 与仓库派生件逐字节相等=%s"
              % ("PASS" if same else "FAIL", out.stat().st_size, same))
        return 0 if same else 1
    (root / NEW_REL).write_text(new, encoding="utf-8", newline="\n")
    nb = (root / NEW_REL).read_bytes()
    diff = "".join(difflib.unified_diff(pre.splitlines(True), new.splitlines(True),
                                        "a/" + PRE_REL, "b/" + NEW_REL))
    (root / PATCH_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / PATCH_REL).write_text(diff, encoding="utf-8", newline="\n")
    o, n2 = pre.splitlines(), new.splitlines()
    print("BUILT|edits=%d|bytes=%d|sha256=%s|lines=%d(+%d/-%d)|patch=%d B sha=%s"
          % (len(EDITS), len(nb), hashlib.sha256(nb).hexdigest(), len(n2),
             sum(1 for l in difflib.ndiff(o, n2) if l.startswith("+ ")),
             sum(1 for l in difflib.ndiff(o, n2) if l.startswith("- ")),
             len(diff.encode("utf-8")), hashlib.sha256(diff.encode("utf-8")).hexdigest()[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
