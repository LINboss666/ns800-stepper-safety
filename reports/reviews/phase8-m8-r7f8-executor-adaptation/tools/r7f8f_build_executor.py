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

# ==================================================================== F5b. A 的 stub 复现的两条漏项
# 操作者 2026-11-05 实测确认(并入同一编号, 不另开轮):
#   ① "开 IRQ 后、arm 前失败, 会漏掉 IRQ 收口。当前 finally 只看电机 obligation。"
#   ② "五条清理仍沿用默认 4 秒, 没有落实 7 秒……最后的 safety_irq_status 被
#      IRQ_TAIL_HARD_DEADLINE_BEFORE_safety_irq_status 跳过。"
# 下面 15 条逐条对应这两项, 每项都要在**矩阵里**同时有一条正向(补齐后各条都发)和一条
# 反向(申报排不下 / 截止夹短)——只加常量不判红, 等于没修。

edit("F5B_RESERVE_CONST",
     '''CLEANUP_RESERVE_SECONDS = 4          # 起转硬截止之后留给 stop+disarm+复核的时间
''',
     '''# F5b(A 的 stub 复现第二项): 4 s 排不下**五条**各自的 1.2 s 确认窗(3 条电机 + 2 条 IRQ 尾部
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
     '''        # F5b①(A 的 stub 复现第一项): 任务书 §2 要的是"**尝试过**开 IRQ 本身就建立尾部清理义务",
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


# ==================================================================== F5c. 预留不许被业务吃掉
# A 的 stub 复现(2026-11-05, 未动硬件): 会话 10 s + 申报预留 7 s, 前置等待耗到第 9 秒仍然允许
# arm; 业务跑到第 10 秒才结束, 收口剩 0 秒 => 五条全被 *_HARD_DEADLINE_BEFORE_* 跳过, 禁能 UNKNOWN。
# F5b 只把预留排满(6.0 <= 7.0)并被总截止夹住, 没有把预留**从业务时间里扣出来** —— 这一条补上:
# 有收口义务来源的场, 业务绝对截止 = 总截止 - 预留, 读/轮询/等待/写前检查共用它。

edit("F5C_BUDGET_BUSINESS_DL",
     '''        # F5b: 会话绝对截止(台账 t0 + 总时限)。cleanup_deadline() 用它把收口再夹一次,
        # 落实"7 s 预留仍受会话总截止约束"——预留不是把会话无上限拉长的手段。
        self.session_abs_dl = None
''',
     '''        # F5b: 会话绝对截止(台账 t0 + 总时限)。cleanup_deadline() 用它把收口再夹一次,
        # 落实"7 s 预留仍受会话总截止约束"——预留不是把会话无上限拉长的手段。
        self.session_abs_dl = None
        # F5c: 业务侧绝对截止 = session_abs_dl - reserve(只有本会话可能建立收口义务时才设)。
        self.business_dl = None
''')

edit("F5C_BUSINESS_DEADLINE",
     '''    def business_deadline(self, want_seconds, slack=SLACK_SECONDS):
        """业务动作的截止 = min(本步想等的, 硬截止) ; 硬截止已过 => 0(不许再做业务动作)。"""
        if self.t0 is None:
            return self.clock.monotonic() + want_seconds + slack
        hard = self.t0 + self.cap
        return min(self.clock.monotonic() + want_seconds + slack, hard)
''',
     '''    def business_deadline(self, want_seconds, slack=SLACK_SECONDS):
        """业务动作的截止 = min(本步想等的+余量, 起转硬截止, **收口前的业务截止**)。

        F5c: 第三条以前不存在。收口预留必须从业务时间里**先扣掉**, 否则前置步骤(等待/轮询/
        只读查询)把会话跑到总截止, 收口剩 0 秒, 五条撤能/关 IRQ 全被 *_HARD_DEADLINE_BEFORE_*
        跳过 —— 那时"具名判红"只是把失败写进账, 设备仍留在带电+IRQ OPEN。
        """
        base = self.clock.monotonic() + want_seconds + slack
        if self.t0 is not None:
            base = min(base, self.t0 + self.cap)
        if self.business_dl is not None:
            base = min(base, self.business_dl)
        return base
''')

edit("F5C_SESSION_WIRE",
     '''    # F5b②: 收口仍然被会话**绝对**截止封顶(修前 cleanup 只认 t0+cap+reserve, 总时限管不到它)。
    budget.session_abs_dl = session_abs_dl
''',
     '''    # F5b②: 收口仍然被会话**绝对**截止封顶(修前 cleanup 只认 t0+cap+reserve, 总时限管不到它)。
    budget.session_abs_dl = session_abs_dl
    # F5c: 本会话**可能**建立收口义务(步骤里出现 motor_arm/motor_start/safety_irq_enable 任一)时,
    # 业务绝对截止提前到"总截止 - 收口预留"。这条判据只看获批步骤里有没有这三条命令, 不看运行
    # 结果: 义务还没建立也得先把撤能窗口留在预算外, 否则"能不能留出 7 秒"取决于前面跑顺不顺。
    OBLIGATION_COMMANDS = ("motor_arm", "motor_start", "safety_irq_enable")
    may_obligate = any(str(s.get("cmd", "")).split(" ")[0] in OBLIGATION_COMMANDS
                       for s in steps if isinstance(s, dict))
    business_abs_dl = (session_abs_dl - float(reserve_declared)) if may_obligate else session_abs_dl
    budget.business_dl = business_abs_dl if may_obligate else None
''')

edit("F5C_STEP_DL",
     '''            want = int(st.get("seconds", 3))
            step_dl = min(clock.monotonic() + want + SLACK_SECONDS, session_abs_dl)
''',
     '''            want = int(st.get("seconds", 3))
            # F5c: 这一步(以及它下面的读/轮询/等待)受**业务**截止约束, 不是会话总截止 ——
            # 差额就是留给五条收口的预留。may_obligate=False 的只读场两者相同。
            step_dl = min(clock.monotonic() + want + SLACK_SECONDS, business_abs_dl)
''')

edit("F5C_STEP_DL_DETAIL",
     '''                     "session_remaining_s": round(session_abs_dl - clock.monotonic(), 3),
                     "energize_remaining_s": energize.remaining()}, ensure_ascii=False))
''',
     '''                     "session_remaining_s": round(session_abs_dl - clock.monotonic(), 3),
                     # F5c: 首错细节里同时给"会话还剩多少"和"收口前业务还剩多少", 让人一眼看出
                     # 这条是被预留挡住的, 不是总截止到了才顺手红。
                     "business_remaining_s": round(business_abs_dl - clock.monotonic(), 3),
                     "cleanup_reserve_seconds": float(reserve_declared),
                     "cleanup_reserved": bool(may_obligate),
                     "energize_remaining_s": energize.remaining()}, ensure_ascii=False))
''')

edit("F5C_RESULT_FIELDS",
     '''                "session_abs_deadline": round(session_abs_dl, 6),
''',
     '''                "session_abs_deadline": round(session_abs_dl, 6),
                "business_abs_deadline": round(business_abs_dl, 6),
                "cleanup_reserved": bool(may_obligate),
''')

edit("F5C_PLAN_GATE",
     '''        bad_r = []
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
''',
     '''        bad_r = []
        if rs is None:
            bad_r.append("cleanup_reserve_seconds 不是数字(=%r)" % (sg.get("cleanup_reserve_seconds"),))
        else:
            if rs + 1e-9 < floor_r:
                bad_r.append("预留=%s < 五条发送窗合计=%.1f" % (rs, floor_r))
            if td is not None and mc is not None and mc + rs > td:
                bad_r.append("motion_cap=%s + 预留=%s > 本阶段总窗=%s(预留排不进会话)"
                             % (mc, rs, td))
        # F5c: 只看 cap+reserve<=total 不够 —— 起转**之前**已经消耗的步骤时间同样吃会话预算。
        # 这里按获批步骤自己申报的窗口求最坏情况(轮询取 until_seconds, 每条再加 SLACK 余量),
        # 与 cap、reserve 一起必须住在本阶段总窗之内。只读阶段(无义务来源命令)不受这条约束。
        steps_r = sg.get("steps", [])
        may_ob = [i for i, s in enumerate(steps_r) if isinstance(s, dict)
                  and str(s.get("cmd", "")).split(" ")[0] in
                  ("motor_arm", "motor_start", "safety_irq_enable")]
        pre = 0.0
        for s in steps_r:
            if not isinstance(s, dict) or not s.get("cmd"):
                continue
            # SLACK_SECONDS 是**单步**的到达容差(每步最多晚 SLACK 秒), 不是可以按步数求和的
            # 预算 —— 38 步 × 3 s 会把任何现场计划都判死。保证预留的是运行时 business_abs_dl
            # 硬夹(与本门同一判据的强制版), 本门只核"申报窗口合不合得下总窗"。
            w = ((s.get("poll") or {}).get("until_seconds") or s.get("seconds") or 0)
            pre += float(w)
        if may_ob and td is not None and pre + (mc or 0.0) + (rs or 0.0) > td:
            bad_r.append("业务最坏合计=%s + cap=%s + 预留=%s > 总窗=%s"
                         "(起转前还要吃掉 %s 秒, 收口实际拿不到预留)"
                         % (round(pre, 3), mc, rs, td, round(pre, 3)))
        ok_split = not (may_ob and td is not None and pre + (mc or 0.0) + (rs or 0.0) > td)
        out.append("PLAN_CLEANUP_RESERVE|%s|%s|reserve=%s floor=%.1f total=%s cap=%s%s" %
                   (sg["id"], "PASS" if not bad_r else "FAIL", rs, floor_r, td, mc,
                    "" if not bad_r else "|" + ";".join(bad_r)))
        # 同一条缺陷只进一个计数(上面已经计过), 这一行专门把"起转前已消耗多少"摊开给人看。
        out.append("PLAN_BUSINESS_RESERVE_SPLIT|%s|%s|may_obligate=%s first@%s "
                   "business_worst=%s cap=%s reserve=%s total=%s" %
                   (sg["id"], "PASS" if ok_split else "FAIL(与 PLAN_CLEANUP_RESERVE 同一条)",
                    bool(may_ob), may_ob[:1], round(pre, 3), mc, rs, td))
        if bad_r:
            bad += 1
''')


# ==================================================================== F6. 静态预算三段
# A 的补充审核项(2026-11-05): 修前的 F5c 计划门把**全部**步骤的申报窗口累加成 `pre`,
# 后面又加一整条 motion_cap —— 起转后的步骤因此被计了两次(一次进 pre, 一次进 cap 覆盖的
# 同一段时间)。这里按执行器自己的计时点把业务时间拆成互不重叠的三段, cap 只算一次。
edit("F6_SEGMENTS_FUNC",
     '''class ApprovalLedger:
    """绑定批准身份的**跨进程**持久预占账''',
     '''def business_segments(steps, cap=None, reserve=None, total=None, slack=SLACK_SECONDS):
    """F6: 一次会话的业务时间按**互不重叠**的三段拆开, 并给出与执行器同一条 min() 的窗口上界。

    分段点不靠人眼猜, 用执行器自己的计时点:
      pre       —— motor_start 的写入尝试之前(MotionBudget.t0 还没置位, 不受运动 cap 夹);
      in_motion —— 从 motor_start 尝试到第一次 motor_stop/motor_disarm;
      post_stop —— 停机/禁能之后直到业务循环结束。
    cap 为什么只加一次: MotionBudget.t0 一旦在 motor_start **写之前**置位就**不清**(正常 stop
    也不清, 见 EnergizeBudget 上方那段注释), 于是 business_deadline() 把 in_motion 与 post_stop
    的每一步都夹在 t0+cap 之内 —— 那两段吃的是**同一段**时间, 再各自加一遍 cap 就是重复计时。
    单步窗口用执行器同一表达式 `poll.until_seconds or seconds`; `*_bound` 每步再加一次 SLACK
    (执行器 step_dl = now + want + SLACK_SECONDS), 所以"包含余量的总窗"是逐步计的, 既没漏算
    余量, 也没把 38 步的容差折成整场一次。`sim` 用执行器那条 min() 逐步推进, 给出"会话夹/
    cap 夹第一次让某一步在写之前就没时间"的那一步 —— 这是最坏情况下的**业务尾巴能跑到哪**。
    """
    SPIN_UP = ("motor_start",)
    STOP_DOWN = ("motor_stop", "motor_disarm")

    def _c(s):
        return str(s.get("cmd", "")).split(" ")[0]

    def _w(s):
        p = s.get("poll") or {}
        return float(p.get("until_seconds") or s.get("seconds") or 0)

    have = [(i, s) for i, s in enumerate(steps) if isinstance(s, dict) and s.get("cmd")]
    i_spin = next((i for i, s in have if _c(s) in SPIN_UP), None)
    i_stop = next((i for i, s in have if i_spin is not None and i >= i_spin and
                   _c(s) in STOP_DOWN), None)
    seg = {"pre": [], "in_motion": [], "post_stop": []}
    for i, s in have:
        if i_spin is None or i < i_spin:
            seg["pre"].append(s)
        elif i_stop is None or i < i_stop:
            seg["in_motion"].append(s)
        else:
            seg["post_stop"].append(s)
    out = {"spin_up_index": i_spin, "stop_index": i_stop}
    for k in ("pre", "in_motion", "post_stop"):
        out[k + "_steps"] = len(seg[k])
        out[k + "_declared"] = round(sum(_w(s) for s in seg[k]), 3)
        out[k + "_bound"] = round(sum(_w(s) + float(slack) for s in seg[k]), 3)
    # 没有 motor_start 的会话(纯只读/只开 IRQ)不进运动 cap: window 段就是 0, 全部内容
    # 都在 pre 里按申报窗口计。cap 缺省同样如此。
    out["window_seconds"] = float(cap) if (i_spin is not None and cap is not None) else 0.0
    out["worst_declared"] = round(out["pre_declared"] + out["window_seconds"], 3)
    out["worst_bound"] = round(out["pre_bound"] + max(out["window_seconds"],
                                                     out["in_motion_bound"] +
                                                     out["post_stop_bound"]), 3)
    out["after_spin_declared"] = round(out["in_motion_declared"] + out["post_stop_declared"], 3)
    out["after_spin_bound"] = round(out["in_motion_bound"] + out["post_stop_bound"], 3)
    out["business_budget_seconds"] = (round(float(total) - float(reserve), 3)
                                      if total is not None and reserve is not None else None)
    # 逐步仿真: 与 exec_session 里 step_dl 那条 min() 同一顺序、同一常数。
    t = 0.0
    t_spin = None
    first_refused = None
    for i, s in have:
        dl = t + _w(s) + float(slack)
        if t_spin is not None and cap is not None:
            dl = min(dl, t_spin + float(cap))
        if out["business_budget_seconds"] is not None:
            dl = min(dl, out["business_budget_seconds"])
        if first_refused is None and dl <= t:
            first_refused = {"index": i, "as": s.get("as") or ("step%02d" % (i + 1)),
                             "cmd": str(s.get("cmd", ""))[:40], "t_s": round(t, 3),
                             "want_seconds": _w(s), "slack_seconds": float(slack),
                             "cap_or_budget_ceiling_s": round(dl, 3)}
        if i == i_spin:
            t_spin = t
        t = dl
    out["sim_end_seconds"] = round(t, 3)
    out["first_step_refused_before_write"] = first_refused
    return out


class ApprovalLedger:
    """绑定批准身份的**跨进程**持久预占账''')


# ============================================================ F6. 计划门改用三段(去掉重复计时)
edit("F6_PLAN_SPLIT",
     '''        # F5c: 只看 cap+reserve<=total 不够 —— 起转**之前**已经消耗的步骤时间同样吃会话预算。
        # 这里按获批步骤自己申报的窗口求最坏情况(轮询取 until_seconds, 每条再加 SLACK 余量),
        # 与 cap、reserve 一起必须住在本阶段总窗之内。只读阶段(无义务来源命令)不受这条约束。
        steps_r = sg.get("steps", [])
        may_ob = [i for i, s in enumerate(steps_r) if isinstance(s, dict)
                  and str(s.get("cmd", "")).split(" ")[0] in
                  ("motor_arm", "motor_start", "safety_irq_enable")]
        pre = 0.0
        for s in steps_r:
            if not isinstance(s, dict) or not s.get("cmd"):
                continue
            # SLACK_SECONDS 是**单步**的到达容差(每步最多晚 SLACK 秒), 不是可以按步数求和的
            # 预算 —— 38 步 × 3 s 会把任何现场计划都判死。保证预留的是运行时 business_abs_dl
            # 硬夹(与本门同一判据的强制版), 本门只核"申报窗口合不合得下总窗"。
            w = ((s.get("poll") or {}).get("until_seconds") or s.get("seconds") or 0)
            pre += float(w)
        if may_ob and td is not None and pre + (mc or 0.0) + (rs or 0.0) > td:
            bad_r.append("业务最坏合计=%s + cap=%s + 预留=%s > 总窗=%s"
                         "(起转前还要吃掉 %s 秒, 收口实际拿不到预留)"
                         % (round(pre, 3), mc, rs, td, round(pre, 3)))
        ok_split = not (may_ob and td is not None and pre + (mc or 0.0) + (rs or 0.0) > td)
        out.append("PLAN_CLEANUP_RESERVE|%s|%s|reserve=%s floor=%.1f total=%s cap=%s%s" %
                   (sg["id"], "PASS" if not bad_r else "FAIL", rs, floor_r, td, mc,
                    "" if not bad_r else "|" + ";".join(bad_r)))
        # 同一条缺陷只进一个计数(上面已经计过), 这一行专门把"起转前已消耗多少"摊开给人看。
        out.append("PLAN_BUSINESS_RESERVE_SPLIT|%s|%s|may_obligate=%s first@%s "
                   "business_worst=%s cap=%s reserve=%s total=%s" %
                   (sg["id"], "PASS" if ok_split else "FAIL(与 PLAN_CLEANUP_RESERVE 同一条)",
                    bool(may_ob), may_ob[:1], round(pre, 3), mc, rs, td))
        if bad_r:
            bad += 1
''',
     '''        # F5c: 只看 cap+reserve<=total 不够 —— 起转**之前**已经消耗的步骤时间同样吃会话预算。
        # F6(A 的补充审核项)修掉这里原来的**重复计时**: 上一版把**全部**步骤的申报窗口累加成
        # 一个"起转前耗时", 后面又补一整条 motion_cap —— 起转后那些步骤同一段时间被算了两次。
        # 现在按执行器自己的计时点分三段(见 business_segments 的 docstring), cap 只进一次;
        # 并且"申报窗口"和"每步再加 SLACK 的最坏上界"分开各判一条, 余量既不漏算也不折成一次。
        steps_r = sg.get("steps", [])
        may_ob = [i for i, s in enumerate(steps_r) if isinstance(s, dict)
                  and str(s.get("cmd", "")).split(" ")[0] in
                  ("motor_arm", "motor_start", "safety_irq_enable")]
        bs = business_segments(steps_r, cap=mc, reserve=rs, total=td)
        bad_s = []
        if may_ob and td is not None and rs is not None:
            if bs["worst_declared"] + rs > td + 1e-9:
                bad_s.append("三段互不重叠合计(申报窗, cap 只算一次)=%s + 预留=%s > 总窗=%s"
                             % (bs["worst_declared"], rs, td))
            if bs["spin_up_index"] is not None and mc is not None and \\
                    bs["after_spin_declared"] > mc + 1e-9:
                bad_s.append("起转后申报内容=%s(运动窗内 %s + 停机后 %s) > cap=%s"
                             "=> 执行器不会在 stop 清 t0, 这些步骤同被 t0+cap 夹住, 计划自己排不下"
                             % (bs["after_spin_declared"], bs["in_motion_declared"],
                                bs["post_stop_declared"], mc))
        bad_w = []
        if may_ob and bs["business_budget_seconds"] is not None:
            fr = bs["first_step_refused_before_write"]
            if fr is not None:
                bad_w.append("每步都慢到 SLACK=%s 上限时, 第 %d 步(%s)在写之前就没有时间"
                             "(已用 %s s, 上限 %s s); 总窗=%s 预留=%s"
                             % (bs.get("slack_seconds", SLACK_SECONDS), fr["index"] + 1,
                                fr["as"], fr["t_s"], fr["cap_or_budget_ceiling_s"], td, rs))
        # 计划自己申报的三段镜像必须与这里现算的**同一套数**一致(A 的要求: 检查器与执行器
        # 使用同一值)。没申报镜像的历史包不判红 —— 它们字节冻结, 只登记 NA。
        decl = (plan.get("motion_budget") or {}).get("business_segments")
        mirror = "NA(计划未申报 motion_budget.business_segments)"
        if decl:
            keys = ("spin_up_index", "stop_index", "pre_steps", "in_motion_steps",
                    "post_stop_steps", "pre_declared", "pre_bound", "in_motion_declared",
                    "in_motion_bound", "post_stop_declared", "post_stop_bound",
                    "worst_declared", "worst_bound", "after_spin_declared",
                    "after_spin_bound", "window_seconds", "business_budget_seconds")
            drift = []
            for k in keys:
                if k not in decl:
                    drift.append("%s=缺失" % k)
                elif isinstance(decl[k], (int, float)) and isinstance(bs[k], (int, float)):
                    if abs(float(decl[k]) - float(bs[k])) > 1e-6:
                        drift.append("%s=%s!=%s" % (k, decl[k], bs[k]))
                elif decl[k] != bs[k]:
                    drift.append("%s=%r!=%r" % (k, decl[k], bs[k]))
            mirror = ("一致(17 项)" if not drift else "漂移: " + ",".join(drift[:6]))
            if drift:
                bad_s.append("计划申报的三段镜像与执行器现算不符 => %s" % ",".join(drift[:3]))
        out.append("PLAN_CLEANUP_RESERVE|%s|%s|reserve=%s floor=%.1f total=%s cap=%s%s" %
                   (sg["id"], "PASS" if not bad_r else "FAIL", rs, floor_r, td, mc,
                    "" if not bad_r else "|" + ";".join(bad_r)))
        if bad_r:
            bad += 1
        out.append("PLAN_BUSINESS_RESERVE_SPLIT|%s|%s|may_obligate=%s spin@%s stop@%s "
                   "pre=%s/%s(%d步) in_motion=%s/%s(%d步) post_stop=%s/%s(%d步) "
                   "cap=%s reserve=%s total=%s worst=%s after_spin=%s mirror=%s%s" %
                   (sg["id"], "PASS" if not bad_s else "FAIL", bool(may_ob),
                    bs["spin_up_index"], bs["stop_index"],
                    bs["pre_declared"], bs["pre_bound"], bs["pre_steps"],
                    bs["in_motion_declared"], bs["in_motion_bound"], bs["in_motion_steps"],
                    bs["post_stop_declared"], bs["post_stop_bound"], bs["post_stop_steps"],
                    mc, rs, td, bs["worst_declared"], bs["after_spin_declared"], mirror,
                    "" if not bad_s else "|" + ";".join(bad_s)))
        if bad_s:
            bad += 1
        # 最坏情况那条单独成门: 它不改运行时(收口预留始终由 business_abs_dl 硬夹保护),
        # 只回答"计划里的业务尾巴在最坏到达下跑到第几步" —— 红了就是**取证完整性**风险,
        # 不是撤能风险, 两者必须分开写, 不许混成一条"看起来更严"的判据。
        out.append("PLAN_BUSINESS_WORST_CASE_FIT|%s|%s|worst_bound=%s budget=%s sim_end=%s "
                   "first_refused=%s%s" %
                   (sg["id"], "PASS" if not bad_w else "FAIL", bs["worst_bound"],
                    bs["business_budget_seconds"], bs["sim_end_seconds"],
                    json.dumps(bs["first_step_refused_before_write"], ensure_ascii=False)
                    if bs["first_step_refused_before_write"] else "NONE",
                    "" if not bad_w else "|" + ";".join(bad_w)))
        if bad_w:
            bad += 1
''')


# =========================================== F6b. 整包 dispatch 也要把 F5b/F5c 的回执字段带出来
edit("F6_DISPATCH_RESERVE_FIELDS",
     '''                             "scenario": sc, "branch": ap,
                             "expected_refusal": res.get("expected_refusal"),''',
     '''                             # F6b: 整包 dispatch 才是现场轮真正走的那条路。上一版这里只搬
                             # status/code/disarm/terminal, 把 F5b/F5c 新加的**预留与夹短回执字段**
                             # 留在了会话结果里 —— 现场 stdout 看不到"这 7 秒到底留没留住",
                             # 离线门也只能判成 code=None(工具的 parse 缺陷之外还有一层数据缺陷)。
                             "scenario": sc, "branch": ap,
                             "cleanup_reserve_seconds": res.get("cleanup_reserve_seconds"),
                             "cleanup_hard_deadline": res.get("cleanup_hard_deadline"),
                             "session_abs_deadline": res.get("session_abs_deadline"),
                             "business_abs_deadline": res.get("business_abs_deadline"),
                             "cleanup_reserved": res.get("cleanup_reserved"),
                             "expected_refusal": res.get("expected_refusal"),''')


# =========================================== F6c. 整包 stub 也要能给 IRQ 固定尾部喂行
edit("F6_STUB_TAIL_ROWS",
     '''    for cmd in CLEANUP_CMDS:
        if cmd not in responses:
            responses[cmd] = rows_for(table, cmd, profile)
    return {"boot_bytes": list(stage.get("boot_bytes", [])), "responses": responses,''',
     '''    for cmd in CLEANUP_CMDS:
        if cmd not in responses:
            responses[cmd] = rows_for(table, cmd, profile)
    # F6c: F5b 的 IRQ 尾部两条和上面三条一样是**执行器自己发的**, 不是阶段步骤。修前这里只给
    # CLEANUP_CMDS 兜底, 于是整包 stub 里 safety_irq_disable/safety_irq_status 永远无行可喂 =>
    # 本轮最想要的那条"整包跑到 IRQ 尾部并且确认 CLOSED"在离线**判不了**(只能在矩阵里手搓夹具)。
    # 只在行表确实带了这个键时才注入: 历史包的行表没有这两个键, 它们的字节行为一丁点都不变。
    for cmd in IRQ_TAIL_CMDS:
        if cmd not in responses and cmd in (table.get("commands") or {}):
            responses[cmd] = rows_for(table, cmd, profile)
    return {"boot_bytes": list(stage.get("boot_bytes", [])), "responses": responses,''')


# ============================== F6d. 申报过的响应序列只归**轮询那一步**用(离线注入侧缺陷)
edit("F6_SEQUENCE_SCOPE",
     '''            key = st.get("key") or cmd
            seq = (fixture.get("sequence") or {}).get(key)
            if seq:                     # 轮询用**申报过的**响应序列(如先 ACCEL 后 CRUISE), 越界取最后一份''',
     '''            key = st.get("key") or cmd
            # F6d: 序列只在**这一步自己申报了轮询**时才生效。修前只要 fixture 里存在这个键的序列,
            # 任何同键的非轮询步骤(整包 §5 行 10 的"取消后仍巡航"查询就撞上)都会取到序列的**第一份**
            # (ACCEL), 于是那一步在自己的单次尝试里读到爬坡行 => PROFILE_NOT_RUNNING。序列是"这一
            # 步轮询期间的可见状态轨迹"的申报, 不是按命令键给全场发牌; 只影响离线注入形状,
            # 现场真串口不查这张表。
            seq = ((fixture.get("sequence") or {}).get(key)
                   if (st.get("poll") or st.get("poll_sequence")) else None)
            if seq:                     # 轮询用**申报过的**响应序列(如先 ACCEL 后 CRUISE), 越界取最后一份''')


# ==================== F5e. IRQ 尾部的 CLOSED 证据按产品**两条真行**认(disable 与 status 各一行)
edit("F5B_TAIL_CONST_ROWS",
     '''IRQ_TAIL_ROW = r"^\\[SAFETY\\] IRQ gate CLOSED"''',
     '''IRQ_TAIL_ROW = r"^\\[SAFETY\\] IRQ gate CLOSED"
# F5e: 同一个"门已关"事实在产品里有**两条不同的打印**, 各自只跟在自己的命令后面:
#   safety_thread.c:306  st_irq_detach_all() => "[SAFETY] IRQ gate CLOSED - 活动安全输入(N 路)..."
#                        (safety_irq_disable 自己打的)
#   safety_thread.c:388  safety_irq_status()  => "[SAFETY] irq gate   = CLOSED (polled only)"
#                        (尾部第二条状态回读打的)
# 修前只在 status 的回包上认第一条, 于是整包程序(先 disable 再 status)永远判
# IRQ_TAIL_NOT_CLOSED。两行都收进集合, 但 disable 那一步单独要求 :306(不许拿 status 顶掉
# "disable 确实动手了"这条证据)。
IRQ_TAIL_CLOSED_ROWS = (IRQ_TAIL_ROW, r"^\\[SAFETY\\] irq gate\\s+= CLOSED\\b")''')

edit("F5B_TAIL_ROWS_REAL",
     '''            if cmd == "safety_irq_status" and not any(
                    re.match(IRQ_TAIL_ROW, r) for r in trows if r):
                errors.append("IRQ_TAIL_NOT_CLOSED")''',
     '''            if cmd == "safety_irq_disable" and not any(
                    re.match(IRQ_TAIL_ROW, r) for r in trows if r):
                errors.append("IRQ_TAIL_DISABLE_NO_DETACH_LINE")
            if cmd == "safety_irq_status" and not any(
                    re.match(x, r) for r in trows if r for x in IRQ_TAIL_CLOSED_ROWS):
                errors.append("IRQ_TAIL_NOT_CLOSED")''')


# ================================================================== G. A 预算裁定(两条窄适配)
# 裁定二: 显式单步总窗 —— 检查器/执行器/镜像/仿真**共用一个解析函数**, 不再逐步叠 SLACK。
edit("G_WINDOW_HELPERS",
     '''class MotionBudget:
    """motor_start 的发送尝试一旦建立, 后续任何动作只能用剩余预算; 清理另给保留段。"""
''',
     '''STEP_TOTAL_KEY = "step_total_seconds"
MOTION_END_KEY = "motion_end_evidence"
# §4/裁定一: 正常 motor_stop 的**发送尝试**必须排在起转后 8 s 内 —— 这是计划侧的静态界,
# 现场判读单独核实际 TX 时刻。常量放在这里让检查器与交接引用同一个数。
NORMAL_STOP_SEND_SECONDS = 8


def step_total_problem(st):
    """裁定二第 1 条: 申报的单步总窗必须是**有限正数**(非 bool、不 int 截断)。

    返回具名问题串, None=合法或没申报。轮询步骤还要求 `poll.until_seconds <= 本步总窗`
    (裁定二第 2 条): 否则"总窗"含不住它自己申报的轮询时间, 就是漏算。
    """
    if STEP_TOTAL_KEY not in st:
        return None
    v = st[STEP_TOTAL_KEY]
    if isinstance(v, bool):
        return "BOOL_VALUE"
    if not isinstance(v, (int, float)):
        return "NOT_A_NUMBER(%s)" % type(v).__name__
    f = float(v)
    if f != f or abs(f) == float("inf"):
        return "NOT_FINITE(%r)" % v
    if f <= 0:
        return "NON_POSITIVE(%r)" % v
    p = st.get("poll") or {}
    if p and float(p.get("until_seconds", 0) or 0) > f:
        return "POLL_UNTIL_EXCEEDS_STEP_TOTAL(until=%r>total=%r)" % (
            p.get("until_seconds"), v)
    return None


def step_window(st, slack=SLACK_SECONDS):
    """这一步的等待上限(秒)与其来源 —— 执行器、计划校验、预算镜像与仿真**都调这里**。

    * 申报了 `step_total_seconds`: 该值**已经包含本步全部等待与余量** => 原样取用(保留 0.5
      这样的小数), **不再加 SLACK**(裁定二第 1 条);
    * 没申报: 回到 legacy = 申报秒数 + SLACK_SECONDS —— 冻结的 R7F7 计划与旧计划一字不变
      (裁定二第 3 条), 也不许把全局 SLACK 改成 0 来"统一"。
    返回 (窗口秒数, 来源标签)。
    """
    v = st.get(STEP_TOTAL_KEY)
    if v is not None:
        return float(v), STEP_TOTAL_KEY
    return int(st.get("seconds", 3)) + slack, "legacy_seconds_plus_slack"


class MotionBudget:
    """motor_start 的发送尝试一旦建立, 后续任何动作只能用剩余预算; 清理另给保留段。"""
''')

# 裁定一: 运动计时的**结束点**由一份可信零速快照给出(不是 stop 的回话), 且不清 t0。
edit("G_BUDGET_ZERO_FIELDS",
     '''        # F5c: 业务侧绝对截止 = session_abs_dl - reserve(只有本会话可能建立收口义务时才设)。
        self.business_dl = None
''',
     '''        # F5c: 业务侧绝对截止 = session_abs_dl - reserve(只有本会话可能建立收口义务时才设)。
        self.business_dl = None
        # G1(A 裁定一): 可信零速快照的确认时刻(**相对 t0 的运动耗时**)与证据摘要。
        # None => 运动 cap 仍约束后面的业务与清理; 置位后只解开**运动**那一项,
        # arm 通电 18 s 与收口预留 7 s / 会话 90 s 封顶**一律不变**。
        self.motion_end = None
        self.motion_end_evidence = None
''')

edit("G_BUDGET_CAP_RELEASE",
     '''    def business_deadline(self, want_seconds, slack=SLACK_SECONDS):
        """业务动作的截止 = min(本步想等的+余量, 起转硬截止, **收口前的业务截止**)。
''',
     '''    @property
    def cap_active(self):
        """运动 cap 是否还在约束本会话 —— 裁定一: 可信零速确认后**不再**约束停机后的业务。"""
        return self.t0 is not None and self.motion_end is None

    def confirm_zero_speed(self, elapsed_from_t0, evidence):
        """裁定一: 唯一正常业务结束点。保留 t0(不许靠清空 t0 放行第二次 start), 记下结束时刻。"""
        if self.t0 is None:
            raise Stop("MOTION_END_WITHOUT_START", "运动计时还没起算就收到零速确认申报")
        if self.motion_end is not None:
            raise Stop("MOTION_END_ALREADY_CONFIRMED",
                       json.dumps({"already_at_s": round(self.motion_end, 3),
                                   "attempt_at_s": round(elapsed_from_t0, 3)}))
        if elapsed_from_t0 > self.cap:
            # "12 秒已到不能用一份迟到零速回包倒填为按时完成"。
            raise Stop("MOTION_ZERO_CONFIRMATION_LATE", json.dumps(
                {"elapsed_from_start_s": round(elapsed_from_t0, 3),
                 "motion_cap_seconds": self.cap}, ensure_ascii=False))
        self.motion_end = float(elapsed_from_t0)
        self.motion_end_evidence = evidence
        return self.motion_end

    def past_business_hard_deadline(self):
        """轮询/等待用: 现在是否已越过"仍生效的最早硬截止"。

        等价于修前那句 `business_deadline(0) <= now`(cap 项 + 业务项), 但零速确认后 cap 项
        自动退出 —— 停机后的 commit/show/ps/free 不该再被旧的 t_start+12 夹住。
        """
        now = self.clock.monotonic()
        dl = []
        if self.cap_active:
            dl.append(self.t0 + self.cap)
        if self.business_dl is not None:
            dl.append(self.business_dl)
        return bool(dl) and now >= min(dl)

    def business_deadline_window(self, window_seconds):
        """和 business_deadline() 同一组夹紧项, 但入参是**已解析好的单步总窗**(不再加 slack)。

        裁定二第 3 条的落点: 检查器用总窗、执行器仍加旧 3 秒 是不允许的, 所以两边都走
        step_window() 取值, 再由这个方法夹截止。legacy 计划传 want+SLACK 时与修前逐值相同。
        """
        base = self.clock.monotonic() + float(window_seconds)
        if self.cap_active:
            base = min(base, self.t0 + self.cap)
        if self.business_dl is not None:
            base = min(base, self.business_dl)
        return base

    def business_deadline(self, want_seconds, slack=SLACK_SECONDS):
        """业务动作的截止 = min(本步想等的+余量, 起转硬截止, **收口前的业务截止**)。
''')

edit("G_CLEANUP_DEADLINE_AFTER_ZERO",
     '''        base = (self.t0 + self.cap + self.reserve) if self.t0 is not None \\
            else self.clock.monotonic() + self.reserve
''',
     '''        # 裁定一: 运动确认后不能再把收口夹在旧的 t_start+12(+7) 里 —— 改用"从现在起 7 秒预留",
        # 但下面的 session_abs_dl 封顶一字不动(预留不是把会话拉长的手段)。
        if self.motion_end is not None:
            base = self.clock.monotonic() + self.reserve
        else:
            base = (self.t0 + self.cap + self.reserve) if self.t0 is not None \\
                else self.clock.monotonic() + self.reserve
''')

edit("G_STEP_DL_TOTAL_WINDOW",
     '''            want = int(st.get("seconds", 3))
            # F5c: 这一步(以及它下面的读/轮询/等待)受**业务**截止约束, 不是会话总截止 ——
            # 差额就是留给五条收口的预留。may_obligate=False 的只读场两者相同。
            step_dl = min(clock.monotonic() + want + SLACK_SECONDS, business_abs_dl)
            if cmd == "__watch__" or budget.armed:
                step_dl = min(step_dl, budget.business_deadline(want))
''',
     '''            want = int(st.get("seconds", 3))
            # G2(裁定二): 单步窗口由 step_window() 给出 —— 申报了 step_total_seconds 就是那个
            # 值(含全部等待与余量, **不再加 SLACK**, 也不 int 截断); 没申报走 legacy。
            problem = step_total_problem(st)
            if problem:
                # 非法值在**开口与写入之前**具名拒绝(不等到超时才红)。
                raise Stop("STEP_TOTAL_SECONDS_INVALID", json.dumps(
                    {"as": asname, "cmd": cmd, "index": idx, "problem": problem,
                     "declared": st.get(STEP_TOTAL_KEY)}, ensure_ascii=False))
            window, window_src = step_window(st)
            # F5c: 这一步(以及它下面的读/轮询/等待)受**业务**截止约束, 不是会话总截止 ——
            # 差额就是留给五条收口的预留。may_obligate=False 的只读场两者相同。
            step_dl = min(clock.monotonic() + window, business_abs_dl)
            if cmd == "__watch__" or budget.armed:
                step_dl = min(step_dl, budget.business_deadline_window(window))
            st_window_gate = dict(window_seconds=round(window, 3), window_from=window_src)
''')

edit("G_STEP_DL_DETAIL_WINDOW",
     '''                raise Stop("STEP_DEADLINE_BEFORE_WRITE", json.dumps(
                    {"as": asname, "want_seconds": want,
''',
     '''                raise Stop("STEP_DEADLINE_BEFORE_WRITE", json.dumps(
                    {"as": asname, "want_seconds": want,
                     # G2: 首错里同时给"这一步实际用的总窗与它的来源", 便于核对检查器与执行器
                     # 用的是不是同一个值(裁定二明令不许两边不一致)。
                     "step_window_seconds": st_window_gate["window_seconds"],
                     "window_from": st_window_gate["window_from"],
''')

edit("G_POLL_CLAMP_USES_GATE",
     '''        if clock.monotonic() + interval >= until or (budget.armed and
                                                    budget.business_deadline(0) <= clock.monotonic()):
''',
     '''        # G1/G2: 轮询的"最早硬截止"改走 past_business_hard_deadline() —— 与修前逐值等价
        # (cap 项 + 收口前业务项), 但零速确认后 cap 项退出; 且**每份尝试共用同一个 until**,
        # 不随次数刷新(裁定二第 1/5 条)。
        if clock.monotonic() + interval >= until or budget.past_business_hard_deadline():
''')

edit("G_ZERO_CONTRACT_FN",
     '''    if hits[0].group("grant") != "verified":
        return False, "许可 LOW %s(未 verified, 禁能动作没证得)" % hits[0].group("grant")
    return True, ""


def _ev_or_defer(led, deferred, kind, **kw):
''',
     '''    if hits[0].group("grant") != "verified":
        return False, "许可 LOW %s(未 verified, 禁能动作没证得)" % hits[0].group("grant")
    return True, ""


ZERO_ARMED_TRUE_SHAPE = ("HIGH", 1, "LOW", 0)
ZERO_ARMED_FALSE_SHAPE = ("LOW", 0, "HIGH", 1)


def zero_speed_contract(rows):
    """裁定一: "新鲜、完整、可信的零速快照" => (summary, bad)。

    六项合同字段必须来自**同一份** motor_status 响应(复用 disable_contract 的唯一性/齐备要求,
    不跨响应拼字段); 然后:
      * `state=0`、`cur_hz=0`、`tgt_hz=0`、`health=OK`;
      * 反馈解算必须 resolved 且判决不是 CUT_BY_HARDWARE;
      * 使能侧两套自洽形状: 仍武装(armed=1) => 请求 HIGH/pad=1 且 ENN 预期 LOW/反馈 0;
        已禁能(armed=0) => 请求 LOW/pad=0 且 ENN 预期 HIGH/反馈 1。别的组合一律不算。
    注意这里**不**要求 diag_off_confirmed=YES —— 那是"禁能已证得"的字段, 不是零速字段,
    stop 之后仍武装的那份真实回包就是 NO。
    """
    summ, _bad = disable_contract(rows)
    if summ is None:
        return None, ["ZERO_SNAPSHOT_FIELDS_INCOMPLETE"]
    bad = []
    if summ["state_num"] != 0:
        bad.append("state=%d(要求 0)" % summ["state_num"])
    if summ["cur_hz"] != 0 or summ["tgt_hz"] != 0:
        bad.append("cur_hz=%d/tgt_hz=%d(要求 0/0)" % (summ["cur_hz"], summ["tgt_hz"]))
    if summ["health"] != "OK":
        bad.append("health=%s" % summ["health"])
    if summ["fb_state"] != "resolved":
        bad.append("fb=%s(未解算的不算可信快照)" % summ["fb_state"])
    if summ["verdict"] == "CUT_BY_HARDWARE":
        bad.append("verdict=CUT_BY_HARDWARE(硬件切断不作为正常完成证据)")
    shape = ZERO_ARMED_TRUE_SHAPE if summ["armed"] == 1 else \\
        (ZERO_ARMED_FALSE_SHAPE if summ["armed"] == 0 else None)
    if shape is None:
        bad.append("armed=%d(只认 0/1)" % summ["armed"])
    else:
        got = (summ["drv_request"], summ["pad"], summ["expected_ENN"], summ["enn_fb"])
        if got != shape:
            bad.append("使能侧形状=%s 要求 %s(armed=%d 那一套自洽值)"
                       % (got, shape, summ["armed"]))
    return summ, bad


def motion_end_gate(budget, st, cmd, txt, start_attempts, stop_confirmed, asname, led, clock):
    """裁定一: 只在**实际执行路径**上按证据释放运动计时(不许只在静态表上切开)。

    全部前提缺一不可: 本会话已尝试过那唯一一次 start; 其后有一条**正常 motor_stop** 已发送
    并拿到成功响应(stop_confirmed 由调用点在判 OK 后置位); 这一步自己申报 motion_end_evidence;
    命令就是 motor_status; 同一份回包通过 zero_speed_contract。任何一项没满足 => 运动 cap 继续。
    """
    if not st.get(MOTION_END_KEY) or cmd != "motor_status":
        return None
    miss = []
    if start_attempts < 1:
        miss.append("本会话没有 motor_start 的写入尝试(不能关闭一笔不存在的运动)")
    if not stop_confirmed:
        miss.append("本会话没有已发送且判 OK 的正常 motor_stop(回话本身不算零速)")
    if not budget.armed:
        miss.append("运动计时还没起算")
    if miss:
        raise Stop("MOTION_END_PRECONDITION_MISSING", json.dumps(
            {"as": asname, "missing": miss}, ensure_ascii=False))
    summ, bad = zero_speed_contract(_rows_of(txt))
    if bad:
        # 残缺/矛盾/旧形状的快照 => 运动义务照旧, 并给出具名原因(不静默放行)。
        raise Stop("MOTION_END_SNAPSHOT_NOT_TRUSTWORTHY", json.dumps(
            {"as": asname, "bad": bad, "snapshot": summ}, ensure_ascii=False))
    elapsed = clock.monotonic() - budget.t0
    budget.confirm_zero_speed(elapsed, {"as": asname, "snapshot": summ})
    led.ev("MOTION_TIMER_ENDED", **{"as": asname, "elapsed_from_start_s": round(elapsed, 3),
                                    "motion_cap_seconds": budget.cap,
                                    "arm_deadline_kept": True})
    return round(elapsed, 3)


def _ev_or_defer(led, deferred, kind, **kw):
''')

edit("G_STOP_CONFIRMED_INIT",
     '''    start_attempts = 0
    irq_attempted = False          # F5: 只记"是否**尝试过** safety_irq_enable", 决定固定尾部走不走
''',
     '''    start_attempts = 0
    stop_confirmed = False         # G1: 业务步里"已发送且判 OK 的正常 motor_stop"(零速确认的前提之一)
    irq_attempted = False          # F5: 只记"是否**尝试过** safety_irq_enable", 决定固定尾部走不走
''')

edit("G_POLL_ZERO_GATE",
     '''                _polled, polled_txt = poll_command_step(
                    port, cmd, st, asname, idx, out, step_dl, transport, fixture,
                    silent, led, clock, budget, results, bindings)
                continue
''',
     '''                _polled, polled_txt = poll_command_step(
                    port, cmd, st, asname, idx, out, step_dl, transport, fixture,
                    silent, led, clock, budget, results, bindings)
                # G1: 轮询到那份**新**零速快照之后同样走释放门(证据不齐就抛具名 STOP)。
                motion_end_gate(budget, st, cmd, polled_txt, start_attempts, stop_confirmed,
                                asname, led, clock)
                continue
''')

edit("G_STOP_OK_AND_ZERO_GATE",
     '''            bind_job_id(st, txt, raw, bindings, led, asname, cmd)
            check_job_expect(st, txt, bindings, asname)
            if st.get("job_contract") and not st.get("poll"):
''',
     '''            bind_job_id(st, txt, raw, bindings, led, asname, cmd)
            check_job_expect(st, txt, bindings, asname)
            if cmd == "motor_stop":
                # G1: stop 判 OK 只证"已请求且产品接单", 它**不**结束运动计时;
                # 但它是随后那份零速快照的前提之一(裁定一第 1 条第 1 小项)。
                stop_confirmed = True
                led.ev("MOTOR_STOP_CONFIRMED", **{"as": asname, "ends_motion_timer": False})
            motion_end_gate(budget, st, cmd, txt, start_attempts, stop_confirmed, asname,
                            led, clock)
            if st.get("job_contract") and not st.get("poll"):
''')


# ---- G3(裁定落地后的静态三段) ----------------------------------------------
G3_RULING_FUNC = '''

def business_segments_ruling(steps, cap=None, reserve=None, total=None,
                             slack=SLACK_SECONDS):
    """A 预算裁定(2026-11-05)之后的静态三段: 起转前 / 活动(起转→可信零速) / 零速后。

    与 business_segments() 的三点区别, 一一对应裁定的两句:
      * 运动计时的**结束点**是申报 `motion_end_evidence` 的那一步(一份可信零速快照), 不是第一条
        stop/disarm —— stop 的回话本身不结束运动计时(裁定一);
      * 每步窗口取 step_window() 的**解析值**: 申报了 step_total_seconds 就是含全部等待与余量的
        总窗, **不再叠 SLACK**(裁定二), 因此 declared==bound; 未申报的步仍按 seconds+SLACK;
      * 旧语义算出来的 91.0/23.0 原样留在 `legacy` 字段里并写明它对应"每步再加 SLACK / stop 不
        解除运动计时", 转绿不等于抹掉(A 的 §4 明令)。
    时间轴与 A 的 `budget_check.py` 同一定义: 步的 start_s 是前面所有步窗口之和, end_s 再加上
    本步窗口; stop/零速/禁能的界都按这个累计轴取。
    """
    rows = [s for s in steps if isinstance(s, dict) and s.get("cmd")]
    cmd0 = [str(s.get("cmd", "")).split(" ")[0] for s in rows]
    asname = [s.get("as") for s in rows]

    def idx_of(name):
        return asname.index(name) if name in asname else None

    spin = cmd0.index("motor_start") if "motor_start" in cmd0 else None
    zero = next((i for i, s in enumerate(rows) if s.get(MOTION_END_KEY)), None)
    stop = next((i for i in range(spin if spin is not None else 0, len(rows))
                 if cmd0[i] in ("motor_stop", "motor_disarm")), None)
    arm = cmd0.index("motor_arm") if "motor_arm" in cmd0 else None
    disabled = idx_of("mot_disabled")

    win = [step_window(s, slack)[0] for s in rows]
    starts, acc = [], 0.0
    for w in win:
        starts.append(acc)
        acc += w
    ends = [starts[i] + win[i] for i in range(len(rows))]

    def seg(lo, hi):
        """[lo, hi] 闭区间的 (步数, 申报总窗); 区间无效返回 (0, 0.0)。"""
        if lo is None or hi is None or hi < lo:
            return 0, 0.0
        return hi - lo + 1, round(sum(win[lo:hi + 1]), 3)

    pre_n, pre_w = seg(0, (spin - 1) if spin is not None else None)
    act_n, act_w = seg(spin, zero)
    post_n, post_w = seg((zero + 1) if zero is not None else None, len(rows) - 1)
    if spin is not None and zero is None:
        # 没有可信零速结束的申报 => 运动计时**不解除**, 起转之后所有内容都还在 t0+cap 里
        # (这正是裁定一之前那条红的成因; 这里如实把它算回 cap, 不留绿灯)。
        act_n, act_w = len(rows) - spin, round(sum(win[spin:]), 3)
        post_n, post_w = 0, 0.0
    business_total = round(sum(win), 3)
    business_budget = (round(float(total) - float(reserve), 3)
                       if total is not None and reserve is not None else None)
    # 保守式(裁定 §3 最后一行): 用**整段 12 s cap** 替掉实际活动窗, 再加上停机后内容与预留。
    conservative_total = (round(pre_w + float(cap) + post_w + float(reserve), 3)
                          if cap is not None and reserve is not None else None)
    conservative_arm = (round(starts[spin] - starts[arm] + float(cap)
                              + (ends[disabled] - ends[zero]), 3)
                        if (cap is not None and arm is not None and spin is not None
                            and zero is not None and disabled is not None) else None)
    # legacy: 修前(F6 那一版)的两个数, **只按旧的申报字段**算 —— 加进 step_total_seconds
    # 之后这些步的 seconds/until 原字段不动(裁定二明令), 所以旧口径仍可原样复算。
    def legacy_w(s):
        return float((s.get("poll") or {}).get("until_seconds") or s.get("seconds") or 0)

    legacy_pre_bound = (round(sum(legacy_w(s) + slack for s in rows[:spin]), 3)
                        if spin is not None else None)
    legacy_after_spin = (round(sum(legacy_w(s) for s in rows[spin:]), 3)
                         if spin is not None else None)
    return {
        "semantics": "A_BUDGET_RULING_20261105(motion ends at trusted zero snapshot; "
                     "per-step total window without extra SLACK)",
        "spin_up_index": spin, "stop_request_index": stop, "zero_confirm_index": zero,
        "arm_index": arm, "disabled_confirm_index": disabled,
        "pre_start_steps": pre_n, "motion_active_steps": act_n, "post_zero_steps": post_n,
        "pre_start_seconds": pre_w, "motion_active_seconds": act_w, "post_zero_seconds": post_w,
        "window_seconds": cap, "reserve_seconds": reserve, "session_total_seconds": total,
        "stop_send_after_start_s": (round(starts[stop] - starts[spin], 3)
                                    if stop is not None and spin is not None else None),
        "zero_confirm_after_start_s": (round(ends[zero] - starts[spin], 3)
                                       if zero is not None and spin is not None else None),
        "arm_to_disabled_s": (round(ends[disabled] - starts[arm], 3)
                              if disabled is not None and arm is not None else None),
        "business_total_seconds": business_total,
        "business_budget_seconds": business_budget,
        "worst_declared": (round(pre_w + float(cap), 3) if cap is not None else None),
        "conservative_total_with_cap_s": conservative_total,
        "conservative_arm_with_cap_s": conservative_arm,
        "steps_total": len(rows),
        "legacy_semantics_note": "对应旧口径: 每步再加 SLACK_SECONDS、且正常 stop 不解除运动计时",
        "legacy_pre_bound_seconds": legacy_pre_bound,
        "legacy_after_spin_declared_seconds": legacy_after_spin,
        "legacy_slack_seconds": slack,
    }
'''

edit("G3_SEGMENTS_RULING_FUNC",
     '''class ApprovalLedger:''',
     G3_RULING_FUNC + '''

class ApprovalLedger:''')

edit("G3_PLAN_CALL_RULING",
     '''        bs = business_segments(steps_r, cap=mc, reserve=rs, total=td)
''',
     '''        # G3(A 预算裁定): 静态三段改用**裁定语义**的函数(结束点=可信零速快照那一步,
        # 单步窗=step_window() 的解析值)。旧函数 business_segments() 保留不删 —— 它是
        # legacy 语义的参照实现, 交接里还要用它复算那 91/23 两个数。
        bs = business_segments_ruling(steps_r, cap=mc, reserve=rs, total=td)
        arm_dl = sg.get("arm_energize_deadline_seconds")
''')

edit("G3_PLAN_CONTENT_GATE_RULING",
     '''            if bs["spin_up_index"] is not None and mc is not None and \\
                    bs["after_spin_declared"] > mc + 1e-9:
                bad_s.append("起转后申报内容=%s(运动窗内 %s + 停机后 %s) > cap=%s"
                             "=> 执行器不会在 stop 清 t0, 这些步骤同被 t0+cap 夹住, 计划自己排不下"
                             % (bs["after_spin_declared"], bs["in_motion_declared"],
                                bs["post_stop_declared"], mc))
''',
     '''            # 裁定一: 运动 cap 只约束**起转→可信零速确认**那一段; 零速之后的内容改由会话业务
            # 截止(总窗-预留)约束, 不再叠在 cap 上。零速申报缺失(zero_confirm_index=None)时
            # 运动计时不解除, 这条按旧口径判(活动+停机后都算进 cap) => 不给"忘了申报"留绿灯。
            if bs["spin_up_index"] is not None and mc is not None:
                act = bs["motion_active_seconds"]
                if bs["zero_confirm_index"] is None:
                    act = round(act + bs["post_zero_seconds"], 3)
                if act > mc + 1e-9:
                    bad_s.append("起转→可信零速确认的活动内容=%s > cap=%s"
                                 "(裁定一后运动 cap 只管这一段)" % (act, mc))
                if bs["post_zero_seconds"] > (bs["business_budget_seconds"] or 0.0) + 1e-9:
                    bad_s.append("零速确认后内容=%s > 业务预算=%s"
                                 % (bs["post_zero_seconds"], bs["business_budget_seconds"]))
            if bs["stop_send_after_start_s"] is not None and \\
                    sg.get("normal_stop_send_by_seconds") is not None and \\
                    bs["stop_send_after_start_s"] > \\
                    float(sg["normal_stop_send_by_seconds"]) + 1e-9:
                # 这条只在阶段**申报了**那个数时才判(现场包申报 §4 的 8 s): 合成夹具的窗口是按
                # 它自己的场景放大的(62 s cap), 没申报这条业务规则, 不许拿现场的值去判它。
                bad_s.append("正常 stop 的发送尝试排在起转后 %s s > 申报的 %s s"
                             % (bs["stop_send_after_start_s"], sg["normal_stop_send_by_seconds"]))
            if bs["zero_confirm_after_start_s"] is not None and mc is not None and \\
                    bs["zero_confirm_after_start_s"] > mc + 1e-9:
                bad_s.append("按申报窗累计, 可信零速确认要到起转后 %s s > cap=%s"
                             % (bs["zero_confirm_after_start_s"], mc))
            if bs["arm_to_disabled_s"] is not None and \\
                    bs["arm_to_disabled_s"] > (arm_dl if arm_dl is not None else 1e9) + 1e-9:
                bad_s.append("按申报窗累计, arm→可信禁能确认 %s s > 通电截止=%s"
                             % (bs["arm_to_disabled_s"], arm_dl))
''')

edit("G3_PLAN_SPLIT_LINE_RULING",
     '''        out.append("PLAN_BUSINESS_RESERVE_SPLIT|%s|%s|may_obligate=%s spin@%s stop@%s "
                   "pre=%s/%s(%d步) in_motion=%s/%s(%d步) post_stop=%s/%s(%d步) "
                   "cap=%s reserve=%s total=%s worst=%s after_spin=%s mirror=%s%s" %
                   (sg["id"], "PASS" if not bad_s else "FAIL", bool(may_ob),
                    bs["spin_up_index"], bs["stop_index"],
                    bs["pre_declared"], bs["pre_bound"], bs["pre_steps"],
                    bs["in_motion_declared"], bs["in_motion_bound"], bs["in_motion_steps"],
                    bs["post_stop_declared"], bs["post_stop_bound"], bs["post_stop_steps"],
                    mc, rs, td, bs["worst_declared"], bs["after_spin_declared"], mirror,
                    "" if not bad_s else "|" + ";".join(bad_s)))
''',
     '''        out.append("PLAN_BUSINESS_RESERVE_SPLIT|%s|%s|may_obligate=%s spin@%s stop@%s zero@%s "
                   "pre_start=%s(%d步) motion_active=%s(%d步) post_zero=%s(%d步) "
                   "stop_after_start=%s zero_after_start=%s arm_to_disabled=%s "
                   "cap=%s reserve=%s total=%s business_total=%s worst=%s cons_total=%s "
                   "cons_arm=%s mirror=%s%s" %
                   (sg["id"], "PASS" if not bad_s else "FAIL", bool(may_ob),
                    bs["spin_up_index"], bs["stop_request_index"], bs["zero_confirm_index"],
                    bs["pre_start_seconds"], bs["pre_start_steps"],
                    bs["motion_active_seconds"], bs["motion_active_steps"],
                    bs["post_zero_seconds"], bs["post_zero_steps"],
                    bs["stop_send_after_start_s"], bs["zero_confirm_after_start_s"],
                    bs["arm_to_disabled_s"], mc, rs, td, bs["business_total_seconds"],
                    bs["worst_declared"], bs["conservative_total_with_cap_s"],
                    bs["conservative_arm_with_cap_s"], mirror,
                    "" if not bad_s else "|" + ";".join(bad_s)))
        # 旧口径的两个数继续留原文(A: 原来的 91/23 秒红原文继续保留, 注明对应旧语义),
        # 这一行**不参与判红**(bad 不加), 只把"如果还按每步叠 SLACK / stop 不解除运动计时"
        # 会算出的数摆在这里, 让人看见转绿是语义变了, 不是把数改了。
        out.append("PLAN_BUSINESS_RESERVE_SPLIT_LEGACY_SEMANTICS|%s|INFO|%s "
                   "legacy_pre_bound=%s legacy_after_spin=%s 本行不判红, 仅留档旧口径" %
                   (sg["id"], bs["legacy_semantics_note"], bs["legacy_pre_bound_seconds"],
                    bs["legacy_after_spin_declared_seconds"]))
        # 裁定二第 1/2/3 条的静态面: 每一步的总窗必须合法、轮询窗不得超出本步总窗、
        # 没申报的步继续走 legacy(冻结的 R7F7 计划因此一字不变)。非法值在这里就判红,
        # 不等运行时开口(运行时那侧是 STEP_TOTAL_SECONDS_INVALID)。
        bad_t = []
        n_decl = 0
        for s in steps_r:
            if not isinstance(s, dict) or not s.get("cmd"):
                continue
            prob = step_total_problem(s)
            if prob:
                bad_t.append("%s:%s" % (s.get("as"), prob))
            if STEP_TOTAL_KEY in s:
                n_decl += 1
        if may_ob and bs["zero_confirm_index"] is not None:
            z = bs["zero_confirm_index"]
            if str(steps_r[z].get("cmd", "")).split(" ")[0] != "motor_status":
                bad_t.append("零速确认申报落在 %s(命令 %s), 裁定一只认 motor_status"
                             % (steps_r[z].get("as"), steps_r[z].get("cmd")))
        out.append("PLAN_STEP_TOTAL_WINDOWS|%s|%s|declared=%d/%d legacy=%d 非法=%s "
                   "zero_evidence_as=%s%s" %
                   (sg["id"], "PASS" if not bad_t else "FAIL", n_decl, bs["steps_total"],
                    bs["steps_total"] - n_decl, bad_t[:4] or "无",
                    steps_r[bs["zero_confirm_index"]].get("as")
                    if bs["zero_confirm_index"] is not None else "NONE(运动计时不解除)",
                    "" if not bad_t else "|" + ";".join(bad_t[:3])))
        if bad_t:
            bad += 1
''')

edit("G3_MIRROR_KEYS_RULING",
     '''            keys = ("spin_up_index", "stop_index", "pre_steps", "in_motion_steps",
                    "post_stop_steps", "pre_declared", "pre_bound", "in_motion_declared",
                    "in_motion_bound", "post_stop_declared", "post_stop_bound",
                    "worst_declared", "worst_bound", "after_spin_declared",
                    "after_spin_bound", "window_seconds", "business_budget_seconds")
''',
     '''            keys = ("spin_up_index", "stop_request_index", "zero_confirm_index",
                    "pre_start_steps", "motion_active_steps", "post_zero_steps",
                    "pre_start_seconds", "motion_active_seconds", "post_zero_seconds",
                    "stop_send_after_start_s", "zero_confirm_after_start_s",
                    "arm_to_disabled_s", "window_seconds", "reserve_seconds",
                    "session_total_seconds", "business_total_seconds",
                    "business_budget_seconds", "worst_declared",
                    "conservative_total_with_cap_s", "conservative_arm_with_cap_s",
                    "steps_total", "semantics")
''')

edit("G3_MIRROR_COUNT_TEXT",
     '''            mirror = ("一致(17 项)" if not drift else "漂移: " + ",".join(drift[:6]))
''',
     '''            mirror = ("一致(%d 项)" % len(keys) if not drift
                      else "漂移: " + ",".join(drift[:6]))
''')

edit("G3_WORST_GATE_RULING",
     '''        bad_w = []
        if may_ob and bs["business_budget_seconds"] is not None:
            fr = bs["first_step_refused_before_write"]
            if fr is not None:
                bad_w.append("每步都慢到 SLACK=%s 上限时, 第 %d 步(%s)在写之前就没有时间"
                             "(已用 %s s, 上限 %s s); 总窗=%s 预留=%s"
                             % (bs.get("slack_seconds", SLACK_SECONDS), fr["index"] + 1,
                                fr["as"], fr["t_s"], fr["cap_or_budget_ceiling_s"], td, rs))
''',
     '''        bad_w = []
        if may_ob and bs["business_budget_seconds"] is not None:
            # 裁定二之后: 申报的**单步总窗已含余量**, 所以这里核的是"38 步总窗合计 + 预留
            # 排不排得进总窗"以及"保守式(整段 cap)排不排得进总窗", 不再按每步叠 SLACK 推最坏。
            if bs["business_total_seconds"] + float(rs) > float(td) + 1e-9:
                bad_w.append("38 步总窗合计=%s + 预留=%s > 总窗=%s"
                             % (bs["business_total_seconds"], rs, td))
            if bs["business_total_seconds"] > bs["business_budget_seconds"] + 1e-9:
                bad_w.append("38 步总窗合计=%s > 业务预算(总窗-预留)=%s"
                             % (bs["business_total_seconds"], bs["business_budget_seconds"]))
            if bs["conservative_total_with_cap_s"] is not None and \\
                    bs["conservative_total_with_cap_s"] > float(td) + 1e-9:
                bad_w.append("保守式(起转前 + 整段 cap + 零速后 + 预留)=%s > 总窗=%s"
                             % (bs["conservative_total_with_cap_s"], td))
''')

edit("G3_WORST_LINE_RULING",
     '''        out.append("PLAN_BUSINESS_WORST_CASE_FIT|%s|%s|worst_bound=%s budget=%s sim_end=%s "
                   "first_refused=%s%s" %
                   (sg["id"], "PASS" if not bad_w else "FAIL", bs["worst_bound"],
                    bs["business_budget_seconds"], bs["sim_end_seconds"],
                    json.dumps(bs["first_step_refused_before_write"], ensure_ascii=False)
                    if bs["first_step_refused_before_write"] else "NONE",
                    "" if not bad_w else "|" + ";".join(bad_w)))
''',
     '''        out.append("PLAN_BUSINESS_WORST_CASE_FIT|%s|%s|business_total=%s budget=%s "
                   "cons_total=%s cons_arm=%s%s" %
                   (sg["id"], "PASS" if not bad_w else "FAIL", bs["business_total_seconds"],
                    bs["business_budget_seconds"], bs["conservative_total_with_cap_s"],
                    bs["conservative_arm_with_cap_s"],
                    "" if not bad_w else "|" + ";".join(bad_w)))
''')


# =========================================== G4. F11 现场首错暴露的洞: 披露豁免必须被正面要求
edit("G4_STATUS_ALLOW_MUST_BE_EXPECTED",
     """    pats = status_lines_allow_patterns(st)
    allow_hit = [r.strip() for r in rows
                 if any(p.fullmatch(r.strip()) for p in pats)] if pats else []
    rest = [r for r in rows if r.strip() not in allow_hit]""",
     """    pats = status_lines_allow_patterns(st)
    allow_hit = [r.strip() for r in rows
                 if any(p.fullmatch(r.strip()) for p in pats)] if pats else []
    # F11 现场补正(设备动作已发生那一次): 上面那句注释从 R7F8E 起就写着"必须同时被本步 expect
    # 正面要求", 但**代码从来没核过** —— 于是写一条 status_lines_allow 实际等于"这步里凡是长得
    # 像拒绝的行都免检"。计划侧那道门(validate-plan)要求锚定 + 硬字段, 运行期却没有对应强制,
    # 两边口径不一致。这里把注释兑现成判据: 被放行的每一行都必须含本步 expect_contains 的某个
    # 字面片段, 否则当场具名停止(fail-closed), 不做"看着像就放过"。
    if allow_hit:
        want = [e for e in st.get("expect_contains", []) if e]
        loose = [l for l in allow_hit if not any(w in l for w in want)]
        if loose:
            led.ev("STATUS_ALLOW_NOT_POSITIVELY_REQUIRED",
                   **{"as": asname, "lines": loose, "expect_contains": want})
            raise Stop("STATUS_ALLOW_NOT_POSITIVELY_REQUIRED",
                       "%s 用 status_lines_allow 放行了未被本步 expect_contains 正面要求的行: %s"
                       % (asname, ";".join(loose)[:200]))
    rest = [r for r in rows if r.strip() not in allow_hit]""")


# ================================= G5. 计划侧那道门补上"被正面要求"这一条(与运行期同一口径)
# G4 把运行期补成了 fail-closed, 但 validate-plan 的 PASS 话术一直写着"锚定且被正面要求",
# 代码只核了锚定/查询步/硬字段三条。计划能过 validate-plan 却在现场被 STATUS_ALLOW_NOT_POSITIVELY_REQUIRED
# 停住, 就是两边口径不一致 —— 而 F11 那次现场停止正是这一类。这里让计划侧静态核"正面要求":
# 只要某个 expect_contains 片段落在模式的**字面前缀**里, 任何 fullmatch 该行必然含它(前缀是模式的
# 确定部分), 于是运行期那条门在静态上就被保证不会翻红。
edit("G5_PLAN_ALLOW_LITERAL_PREFIX_HELPER",
     """        except re.error:
            raise Stop("STATUS_ALLOW_PATTERN_INVALID", "%s:%s" % (st.get("as", "?"), p))
    return pats""",
     """        except re.error:
            raise Stop("STATUS_ALLOW_PATTERN_INVALID", "%s:%s" % (st.get("as", "?"), p))
    return pats


def status_allow_literal_prefix(pat):
    # 取模式串在第一个**真正通配**的结构之前的字面片段(去掉首尾锚点)。
    # 给 PLAN_STATUS_ALLOW 用: 这段是"凡能被该模式 fullmatch 的行都必须以此开头"的确定前缀,
    # 因此 expect_contains 里只要有它的子串, 运行期的正面要求门就一定成立。
    # 反斜杠后面跟**字符类/断言**(d s w 与其大写、b B A Z)或反向引用才是通配; 跟在 \\[ \\] \\(
    # \\) \\. \\* 以及**转义空格**后面的, 就是那个字符本身(冻结的 R7F7 计划整条模式都用反斜杠转义
    # 空格, 把它当通配会把前缀截成 "[DIAG]" 而假判红)。
    _CLS = "dDsSwWbBAZnrtfv0123456789"
    s = pat[1:] if pat.startswith("^") else pat
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _CLS:
                break                               # 字符类/断言/反向引用: 不是字面
            out.append(nxt)                         # 转义的字面字符(含转义空格)
            i += 2
            continue
        if c in "$.*+?|{[(":
            break
        out.append(c)
        i += 1
    return "".join(out)""")

edit("G5_PLAN_ALLOW_MUST_BE_POSITIVELY_REQUIRED",
     """                elif not st.get("expect_values"):
                    sa_bad.append("%s/%s:豁免步骤没有 expect_values 硬字段判据"
                                  % (stg["id"], st.get("as")))
                else:
                    sa_seen.append("%s:%s" % (stg["id"], st.get("as")))""",
     """                elif not st.get("expect_values"):
                    sa_bad.append("%s/%s:豁免步骤没有 expect_values 硬字段判据"
                                  % (stg["id"], st.get("as")))
                elif not any(w and w in status_allow_literal_prefix(p)
                             for w in (st.get("expect_contains") or [])):
                    sa_bad.append("%s/%s:豁免行的字面前缀 %r 没被本步 expect_contains 正面要求"
                                  " => 运行期会具名停 STATUS_ALLOW_NOT_POSITIVELY_REQUIRED"
                                  % (stg["id"], st.get("as"), status_allow_literal_prefix(p)))
                else:
                    sa_seen.append("%s:%s" % (stg["id"], st.get("as")))""")


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
