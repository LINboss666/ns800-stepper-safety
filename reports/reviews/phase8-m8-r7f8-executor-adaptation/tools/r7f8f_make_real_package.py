# -*- coding: utf-8 -*-
"""R7F8F 最终现场包作者: 产出 real plan + real rows(离线, 0 设备动作)。

来源纪律(任务书 §4/§5/§6, A 包 NS800_R7F8F_Repair_And_Field_20261105):
  - 每一个数字都带一句出处; 能在本仓库产品源码里核到的行形状才写字段判据。
  - 本轮**不下载/不回读/不复位/不动分析仪/不断送电**, 所以 plan 里只有一个 serial_session,
    download/readback/reset 额度全 0; 镜像身份只作**参照**(inherited), 不当成本轮动作结果。
  - 拿不准字段形状的查询步只断言"命令回显 + 拿到完整回包"(不造字段名), 这些步骤列在
    `field_assertion_gaps` 里, 交给 A 判是否足够 —— 宁缺勿造。

用法: python tools/r7f8f_make_real_package.py --repo <仓库根>
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
PLAN_REL = ROUND + "/field/plan_r7f8f_2369_real.json"
ROWS_REL = ROUND + "/field/rows_r7f8f_real.json"
# 在板镜像的**归档原件**(R7F7-F1 唯一一次定长回读已证逐字节在板; 摘要由本工具当场重算)
IMAGE_REL = ("reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/"
             "rt-thread-motor-m1-candidate.bin")
# 镜像身份**只从盘上原字节算**; 算不出来就拒绝产出, 不留任何手写摘要。
# (R7F7 轮登记的短前缀 23691e8d… 只是人眼对照用, 完整值一律由本工具当场重算并打印。)
IMAGE_SHA = ""
IMAGE_BYTES = 0
AXF = {}                      # 由 _inherited_tools() 从 R7F7 定稿计划当场读取(见该函数)

# ---------------------------------------------------------------- 产品行形状(全部标出处)
def job(jid, st, adc=None, sg=None, want=8, lo=250, hi=350, d=1, owner=None, cred=None,
        why=None):
    """diag_relative.c:2042 两行形状; why/owner/cred 由状态推导:
    RUNNING  -> nj.why=REL_JOB_WHY_RUN(:1637) + d_cap_owner=REL_CAP_BY_JOB(:1643) => owner=2
    CANCEL_PENDING -> :1674 "CANCEL_REQUESTED"
    CANCELLED      -> :1719 终态 why="CANCELLED", owner 归还 0
    DONE          -> :1760 终态 why="COMPLETE", cred=1/1
    """
    shape = {"RUNNING": ("RUNNING", 2, "1/0"), "CANCEL_PENDING": ("CANCEL_REQUESTED", 2, "1/0"),
             "CANCELLED": ("CANCELLED", 0, "1/0"), "DONE": ("COMPLETE", 0, "1/1"),
             "FAILED": ("WINDOW_BROKEN", 0, "1/0"), "IDLE": ("IDLE", 0, "1/0")}[st]
    why = shape[0] if why is None else why
    owner = shape[1] if owner is None else owner
    cred = shape[2] if cred is None else cred
    adc = want if adc is None else adc
    sg = want if sg is None else sg
    return ["[DIAG] job id=%d st=%s why=%s owner=%d cred=%s" %
            (jid, st, why, owner, cred),
            "[DIAG] job n=%d/%d want=%d win=%d..%d dir=%d" % (adc, sg, want, lo, hi, d)]


def done_full(jid, want=8, lo=250, hi=350, d=1):
    """完整 DONE: 状态两行 + 候选统计/阈值/结果三行(diag_relative.c:2056/2068/2074 一带,
    只在 state==DONE 且 cand_valid 时打)。阈值行按 R7D1 已核对形状给 FIELD_UNVERIFIED。"""
    return job(jid, "DONE", want=want, lo=lo, hi=hi, d=d) + [
        "[DIAG] job data adc[280..320]n%d nz2 sg[60..80]n%d seq101..%d" % (want, want, 100 + want),
        "[DIAG] job thr tier=1 dev=30/60 drop=15/40 FIELD_UNVERIFIED",
        "[DIAG] job DONE result; cred zero=1 learn=1 commit is separate"]


MOT_IDLE = ["[MOT] state=0 name=OTHER", "[MOT] cur_hz=0 tgt_hz=0 accel=2000 decel=2000 dir=1",
            "[MOT] armed=0 drv_en_request=0 health=OK", "[MOT] drv_request=LOW pad=0",
            "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK",
            "[MOT] diag_off_confirmed=YES"]
# arm 之后、start 之前: 已武装但零目标(产品行形状同 motor.c:2455-2504; 使能侧 ENN LOW/0)。
MOT_ARMED_IDLE = ["[MOT] state=0 name=OTHER",
                  "[MOT] cur_hz=0 tgt_hz=0 accel=2000 decel=2000 dir=1",
                  "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
                  "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
                  "[MOT] diag_off_confirmed=NO"]
# 爬坡中的那一行: **数值 state 必须是 ACCEL=1**(motor.c 的枚举), 上一版用
# MOT_CRUISE[0].replace("CRUISE","ACCEL") 得到的是 "state=2 name=ACCEL" —— 名字改了、数值没改,
# 于是轮询的 ramping 门(按 state 数值判"还在爬坡")看不到 ACCEL, 第一次尝试就直接
# PROFILE_NOT_RUNNING 停止。这是本工具自己的缺陷, 按枚举逐字重写。
MOT_ACCEL = ["[MOT] state=1 name=ACCEL",
             "[MOT] cur_hz=210 tgt_hz=300 accel=2000 decel=2000 dir=1",
             "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
             "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
             "[MOT] diag_off_confirmed=NO"]
MOT_CRUISE = ["[MOT] state=2 name=CRUISE", "[MOT] cur_hz=300 tgt_hz=300 accel=2000 decel=2000 dir=1",
              "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
              "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
              "[MOT] diag_off_confirmed=NO"]
# motor.c:2455-2504 是字段清单; 1328/1415/1698 是三条成功/请求行(逐字取自主线代码)。
ARM_OK = ["[MOT] ARMED (drv_en request=HIGH readback verified, ENN feedback=LOW confirmed)"]
DISARM_OK = ["[MOT] DISARM (动作已做: STEP DONE / 许可 LOW verified / ENN feedback=OK)"]
STOP_OK = ["[MOT] stop requested: target_hz=0 (软件命令值), STEP 实际关断结果见 "
           "stop_result(当前=OFF_CONFIRMED)"]
DIR_OK = ["[MOT] dir=1 requested"]
TARGET_OK = ["[MOT] target=300 requested"]
START_OK = ["[MOT] step pwm enabled"]
# safety_thread.c:306 打的是 ST_INPUT_N 路数; 本项目 SAFETY_LIMIT_INPUTS_ENABLED=RT_FALSE
# (project_board.h:51) => st_inputs[] 只剩 TMC_DIAG 一路 => **1 路**, 不是上一版写的 0 路。
IRQ_CLOSED = ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"]
IRQ_OPEN = ["[SAFETY] IRQ gate OPEN: 1 active protection input(s) registered",
            "[SAFETY] IRQ enabled mask=0x00"]
IRQ_STILL = ["[SAFETY] IRQ gate stays CLOSED"]                                 # :290
# safety_thread.c:388-392 的**整行形状**(值按 §4 现场事实: 门 CLOSED / 极性未确认)。
# 上一版把 :392 的"值"和括号里的提示拼成了一条产品不会打印的行, 这轮按源码逐字拆开。
IRQ_STATUS_ARMED = ["[SAFETY] irq gate   = CLOSED (polled only)",
                    "[SAFETY] polarity   = NOT confirmed (HARDWARE-PENDING unless CONFIRMed)"]
# supervisor.c:85/90/128 三条是本轮实读到的形状(字段值按 §4 工况给合成值)。
SYS_READY = ["=== SYSTEM STATUS ===",
             "[Safety] state=READY fault=0 irq_gate=closed polarity=HARDWARE-PENDING "
             "protect_ready=YES",
             "[TMC]    runtime write gate mask=0x18 (0=运行态条件全通,"]
SYS_READY_APPLIED = ["=== SYSTEM STATUS ===",
                     "[Safety] state=READY fault=0 irq_gate=closed "
                     "polarity=MARKED-VALIDATED protect_ready=YES",
                     "[TMC]    runtime write gate mask=0x8 (0=运行态条件全通,"]
LEGACY_ROW = job(1, "FAILED", adc=0, sg=0, want=32, lo=250, hi=350)             # §5 行1 允许的旧终态


def _inherited_tools():
    """从 R7F7 定稿计划继承 tools(download/readback/reset + provenance/substitutions)。

    只改 image 子块为本轮参照身份; 其余字节原样带过来, 避免把已在现场用过的命令行重新手写。
    """
    src = pathlib.Path(__file__).resolve()
    for _up in range(1, 8):
        cand = src.parents[_up] / ("reports/reviews/phase8-m8-r7f7-newimage-static/field/"
                                   "plan_r7f7_2369_static.json")
        if cand.is_file():
            r7 = json.loads(cand.read_text(encoding="utf-8"))
            break
    else:
        raise SystemExit("REFUSED|R7F7 定稿计划不在盘上, tools 块无法继承(不许手写 argv 占位)")
    tools = {k: v for k, v in (r7.get("tools") or {}).items()
             if k in ("provenance", "substitutions", "download", "readback", "reset")}
    ident = {"path": IMAGE_REL, "bytes": IMAGE_BYTES, "sha256": IMAGE_SHA}
    # 下载载荷的 AXF 三元组同样**原样继承** R7F7 登记值: identity 门要求 tools.download.argv 的
    # 末位 == tools.download.image.path, 且 S0/下载/回读三处同源。本轮不执行下载, 它只是身份参照。
    global AXF
    AXF = dict((r7.get("identity_binding") or {}).get("image", {}).get("axf") or {})
    if not (AXF.get("path") and AXF.get("sha256") and AXF.get("bytes")):
        raise SystemExit("REFUSED|R7F7 计划里没有 AXF 三元组, 无法继承身份(不许手写)")
    # 身份门要求的"三处同源"在这里是: S0 参照 image(BIN) == readback.image(BIN);
    # identity.image.axf == download.image == download.axf(下载载荷 AXF)。本轮两者都不执行。
    tools.setdefault("readback", {})["image"] = dict(ident, note=(
        "R7F8F 本轮 0 次回读: 这条 argv 是 R7F7 原样继承的登记值; image 子块给的是**在板 BIN**"
        "的参照身份, 不代表本轮执行过回读。"))
    tools.setdefault("download", {})["image"] = dict(AXF, note=(
        "R7F8F 本轮 0 次下载: image/axf 子块是下载载荷 AXF 的登记身份(与 argv 末位同源), "
        "不是本轮动作结果。"))
    tools["download"]["axf"] = dict(AXF)
    tools["reset"]["note"] = "R7F8F 本轮 0 次复位(§4: 不复位)。"
    return tools


def rows_doc():
    c = {
        "system_status": {"healthy": SYS_READY},
        "system_status_applied": {"healthy": SYS_READY_APPLIED},
        "motor_status_idle": {"healthy": MOT_IDLE},
        "motor_status_cruise": {"healthy": MOT_CRUISE},
        "motor_status_armed_idle": {"healthy": MOT_ARMED_IDLE},
        "motor_status_accel": {"healthy": MOT_ACCEL},
        "motor_arm": {"healthy": ARM_OK},
        "motor_disarm": {"healthy": DISARM_OK},
        "motor_stop": {"healthy": STOP_OK},
        "motor_dir": {"healthy": DIR_OK},
        "motor_target": {"healthy": TARGET_OK},
        "motor_start": {"healthy": START_OK},
        "safety_irq_status_closed": {"healthy": IRQ_STATUS_ARMED + IRQ_CLOSED},
        "safety_irq_status_open": {"healthy": [
            "[SAFETY] irq gate   = OPEN (EXTI registered)",
            "[SAFETY] polarity   = operator CONFIRMED (HARDWARE-PENDING unless CONFIRMed)"]},
        # 固定尾部的最后一条(执行器自己发, 不是步骤): 关掉之后再取一次状态。值按"已经 disable"
        # 给, 形状仍是 :388/:392 那两行。
        "safety_irq_status": {"healthy": [
            "[SAFETY] irq gate   = CLOSED (polled only)",
            "[SAFETY] polarity   = operator CONFIRMED (HARDWARE-PENDING unless CONFIRMed)"]},
        "safety_irq_enable": {"healthy": IRQ_OPEN},
        # 尾部第一条发的是 safety_irq_disable => 产品走 st_irq_detach_all(), 打的是
        # safety_thread.c:306 那行 CLOSED(**不是** :290 的 "stays CLOSED", 那行属于 attach
        # 失败回滚路径)。上一版填了 :290, 于是整包程序的收口尾巴判成 IRQ_TAIL_NOT_CLOSED。
        "safety_irq_disable": {"healthy": IRQ_CLOSED},
        # safety_thread.c:457/452: 只翻转软件标志, 不声称硬件极性已测。
        "safety_polarity_confirm": {"healthy": [
            "[SAFETY] input polarity MARKED VALIDATED (operator confirm)",
            "[SAFETY]       hardware chain; only the polarity flag changes here."]},
        # 未取到逐字打印形状的两条(见 _about.unverified_shapes): 只当回显占位, 步骤也只断言前缀。
        "tmc_config_apply": {"healthy": ["[TMC] apply CONFIRM"]},
        "tmc_config_show": {"healthy": ["[TMC] config show"]},
        "diag_profile_zero": {"healthy": ["[DIAG] zero OK n=32 raw 480..520 mean=500 "
                                          "(cal source untouched)"]},
        "ls_idle": {"healthy": job(0, "IDLE", want=0)},
        "ls_running32": {"healthy": job(41, "RUNNING", want=32, adc=1, sg=1)},
        "ls_cancelled": {"healthy": job(41, "CANCELLED", want=32)},
        "learn32": {"healthy": ["[DIAG] learn STARTED id=41 n=32 win=250..350 dir=1",
                                "[DIAG] learn async: query 'learn_status'; "
                                "NOT learn OK yet FIELD_UNVERIFIED"]},
        "learn8": {"healthy": ["[DIAG] learn STARTED id=57 n=8 win=250..350 dir=1",
                               "[DIAG] learn async: query 'learn_status'; "
                               "NOT learn OK yet FIELD_UNVERIFIED"]},
        "cancel_cmd": {"healthy": ["[DIAG] cancel id=41 REQUESTED st=CANCEL_PENDING "
                                   "(motor untouched)"]},
        "ls_run8a": {"healthy": job(57, "RUNNING", adc=4, sg=4)},
        "ls_done8": {"healthy": done_full(57)},
        "__watch__": {"healthy": ["[DIAG] quiet"]},
        # 固定清理三条按**命令名**查表, 与业务段同一张表但要各给一份(禁能快照 = 静止那份)。
        "motor_status": {"healthy": MOT_IDLE},
        "diag_status": {"healthy": ["[DIAG] mode=MONITOR_ONLY", "[DIAG] live sig=0 owner=FREE"]},
        "diag_profile_show": {"healthy": ["[DIAG] profile UNCONFIGURED pending=VALID "
                                          "id=57 rev=1 FIELD_UNVERIFIED"]},
        "diag_profile_commit": {"healthy": ["[DIAG] commit OK id=57 rev=1 (RAM only; "
                                            "config_save NOT issued) FIELD_UNVERIFIED"]},
        "ps": {"healthy": ["thread    pri  status      sp     stack size max used left tick",
                           "main         10  suspend 0x00000000 0x00000800 06%   00000001"]},
        "free": {"healthy": ["total    : 65536", "used     : 20480", "maximum  : 24576",
                             "available: 45056"]},
    }
    return {
        "_about": {
            "kind": "SYNTHETIC_REAL_SHAPE_ROWS_NOT_BOARD_EVIDENCE",
            "shape_source": "逐字取自产品打印语句(motor.c / diag_relative.c / safety_thread.c / "
                            "supervisor.c), 数值为本包工装造的合成值; 板端实测值一律不在此文件。",
            "assertion_scope": "只给本轮**已回源码核到形状**的字段写判据; 其余查询步只断言"
                               "命令回显与完整回包(见 plan.field_assertion_gaps)。",
            "job_ids": "取消作业 41 / 完成作业 57 均来自各自 STARTED 行; 计划里只写 @引用。",
            "unverified_shapes": [
                "tmc_config_apply", "tmc_config_show",
                "diag_profile_show", "diag_profile_commit",
                "diag_status", "ps/free 的表头取自 R7F7 已用过的形状, 阈值不判"],
            "unverified_note": "这几条本轮**没有**回源码取到逐字打印形状, 因此行内容是回显占位, "
                               "对应步骤只断言前缀存在; §5 里对它们要求的字段判据一律没写 "
                               "(宁缺勿造), 已列进 plan.field_assertion_gaps 交 A 判。",
            "hardware_actions": 0},
        "commands": c}


def steps():
    """§5 的 16 行 => 15 个业务步骤 + finally 固定收口(执行器自己做, 不占步骤)。"""
    s = []

    def add(asname, cmd, **kw):
        d = {"as": asname, "cmd": cmd}
        d.update({k: v for k, v in kw.items() if v is not None})
        s.append(d)

    # 1) 前态核对(全只读)
    add("sys_pre", "system_status", key="system_status", seconds=1,
        expect_contains=["=== SYSTEM STATUS ==="])
    add("mot_pre", "motor_status", key="motor_status_idle", seconds=1,
        expect_contains=["[MOT] state=0", "[MOT] armed=0 drv_en_request=0"])
    add("irq_pre", "safety_irq_status", key="safety_irq_status_closed", seconds=1,
        expect_contains=["[SAFETY] irq gate   = CLOSED"])
    add("diag_pre", "diag_status", seconds=1, expect_contains=["[DIAG]"])
    add("ls_pre", "diag_profile learn_status", key="ls_idle", seconds=1,
        expect_contains=["[DIAG] job id="])
    add("cfg_pre", "tmc_config_show", seconds=1, expect_contains=["[TMC]"])
    # 2) 一次配置应用(§4: 先核目标配置再 apply 一次)
    add("apply", "tmc_config_apply CONFIRM", key="tmc_config_apply", seconds=2,
        expect_contains=["[TMC]"], quota={"name": "tmc_config_apply", "n": 1})
    # 门由 0x18 -> 0x8 用**已核到形状**的 system_status 行来核(supervisor.c:128), 不靠 apply 自己话术。
    add("sys_after_apply", "system_status", key="system_status_applied", seconds=1,
        expect_contains=["=== SYSTEM STATUS ===", "runtime write gate mask=0x8"])
    # 3) 静止零点
    add("zero", "diag_profile zero 32", key="diag_profile_zero", seconds=13,
        expect_contains=["[DIAG] zero OK n=32"], quota={"name": "diag_profile_zero", "n": 1})
    add("zero_state", "diag_profile learn_status", key="ls_idle", seconds=1,
        expect_contains=["[DIAG] job id="])
    # 4) 极性确认 + 开 IRQ(本轮单独额度; confirm 不升级为 polarity_measured)
    add("pol", "safety_polarity_confirm CONFIRM", key="safety_polarity_confirm", seconds=1,
        expect_contains=["[SAFETY] input polarity MARKED VALIDATED",
                         "only the polarity flag changes here"],
        quota={"name": "irq_polarity_prep", "n": 1})
    add("irq_en", "safety_irq_enable", key="safety_irq_enable", seconds=1,
        expect_contains=["IRQ gate OPEN"], quota={"name": "irq_polarity_prep", "n": 1})
    add("irq_state", "safety_irq_status", key="safety_irq_status_open", seconds=1,
        expect_contains=["[SAFETY] irq gate   = OPEN"])
    add("mot_after_irq", "motor_status", key="motor_status_idle", seconds=1,
        expect_contains=["[MOT] armed=0 drv_en_request=0", "cur_hz=0 tgt_hz=0"])
    # 5) DIR1 与静止复核
    add("dir", "motor_dir 1", key="motor_dir", seconds=2, expect_contains=["[MOT] dir=1"],
        quota={"name": "motor_dir", "n": 1})
    add("mot_dir", "motor_status", key="motor_status_idle", seconds=1,
        expect_contains=["[MOT] state=0", "dir=1", "armed=0"])
    # 6) arm + 相邻状态(F3: 一场一次 arm; 义务从写入尝试起算)
    add("arm", "motor_arm", key="motor_arm", seconds=1,
        expect_contains=["ARMED (drv_en request=HIGH readback verified"],
        quota={"name": "motor_arm", "n": 1})
    add("mot_armed", "motor_status", key="motor_status_armed_idle", seconds=1,
        expect_contains=["[MOT] armed=1 drv_en_request=1", "expected_ENN=LOW ENN_fb=0"])
    # 7) target + start + 有界轮询到 CRUISE(§4: start 尝试起 12 s)
    add("target", "motor_target 300", key="motor_target", seconds=2,
        expect_contains=["[MOT] target=300"], quota={"name": "motor_target", "n": 1})
    add("start", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
        quota={"name": "motor_run", "n": 1})
    s.append({"as": "cruise", "cmd": "motor_status", "key": "motor_status_cruise",
              "seconds": 1, "expect_profile": "RUNNING",
              "expect_running": {"dir": 1, "cur": 300, "tgt": 300, "accel": 2000, "decel": 2000},
              "poll": {"until_seconds": 1.0, "interval_seconds": 0.1,
                       "max_attempts": 4, "retry_codes": ["PROFILE_NOT_RUNNING"]},
              "poll_sequence": ["motor_status_accel", "motor_status_cruise"]})
    # 8) 取消试验作业(N=32)
    add("learn_cancel", "diag_profile learn 32 250 350 1", key="learn32", seconds=1,
        expect_contains=["STARTED"], quota={"name": "diag_profile_learn", "n": 1},
        job_bind={"name": "cancel_job", "params": {"n": 32, "lo": 250, "hi": 350, "dir": 1}})
    add("ls_cancel_bound", "diag_profile learn_status", key="ls_running32", seconds=1,
        expect_contains=["[DIAG] job id="])
    # 9) 取消 + 有界轮询到 CANCELLED
    add("cancel", "diag_profile learn_cancel @cancel_job", key="cancel_cmd", seconds=1,
        job_cmd="cancel_job", expect_contains=["REQUESTED"],
        quota={"name": "diag_profile_learn_cancel", "n": 1})
    s.append({"as": "cancel_state", "cmd": "diag_profile learn_status", "key": "ls_cancelled",
              "seconds": 1,
              "expect_values": [{"row": JOB_ROW, "must": {"st": "CANCELLED", "owner": "0",
                                                          "cred": "1/0"},
                                 "job_binding": "cancel_job"}],
              # 取消之后的这一行**必须申报 phase**: 不申报时 job_contract 按默认 "done" 判,
              # 而 done 阶段不允许 CANCELLED(A 的 §2 状态合同: CANCELLED 只属于取消那条链)。
              # 上一版没申报, 整包程序在第 26 步就 JOB_CONTRACT_VIOLATED 停止。
              "job_contract": {"binding": "cancel_job", "phase": "post_cancel"},
              "poll": {"until_seconds": 1.0, "interval_seconds": 0.1, "max_attempts": 3,
                       "retry_codes": ["FIELD_VALUE_MISMATCH"],
                       "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                       "job_binding": "cancel_job"}})
    # 10) 取消不代替停机: 仍巡航
    add("mot_after_cancel", "motor_status", key="motor_status_cruise", seconds=1,
        expect_profile="RUNNING",
        expect_running={"dir": 1, "cur": 300, "tgt": 300, "accel": 2000, "decel": 2000})
    # 11) 完成作业(N=8) + 有界轮询到完整 DONE
    add("learn_done", "diag_profile learn 8 250 350 1", key="learn8", seconds=1,
        expect_contains=["STARTED"], quota={"name": "diag_profile_learn", "n": 1},
        job_bind={"name": "done_job", "params": {"n": 8, "lo": 250, "hi": 350, "dir": 1}})
    s.append({"as": "wait_done", "cmd": "diag_profile learn_status", "key": "ls_done8",
              "seconds": 1,
              "expect_values": [{"row": JOB_ROW, "must": {"st": "DONE", "owner": "0",
                                                          "cred": "1/1"},
                                 "job_binding": "done_job"},
                                {"row": N_ROW, "must": {"want": 8},
                                 "must_min": {"adc": 8, "sg": 8}}],
              "poll": {"until_seconds": 2.0, "interval_seconds": 0.1, "max_attempts": 12,
                       "retry_codes": ["FIELD_VALUE_MISMATCH", "JOB_DONE_CANDIDATE_EVIDENCE_MISSING"],
                       "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                       "job_binding": "done_job"},
              "poll_sequence": ["ls_run8a", "ls_done8"]})
    # 12) 停机(start 后 8 s 内发起; 有界轮询到零速)
    add("stop", "motor_stop", key="motor_stop", seconds=2,
        expect_contains=["stop requested: target_hz=0"], quota={"name": "motor_stop", "n": 1})
    s.append({"as": "stopped", "cmd": "motor_status", "key": "motor_status_idle", "seconds": 1,
              "expect_contains": ["[MOT] cur_hz=0 tgt_hz=0"],
              "poll": {"until_seconds": 1.0, "interval_seconds": 0.1, "max_attempts": 5,
                       "retry_codes": ["EXPECTATION_NOT_MET"]}})
    # 13) disarm + 相邻禁能快照(F3: 不凭 DISARM 子串解除义务)
    add("disarm", "motor_disarm", key="motor_disarm", seconds=2,
        expect_contains=["DISARM (动作已做"], quota={"name": "motor_disarm", "n": 1})
    add("mot_disabled", "motor_status", key="motor_status_idle", seconds=1,
        expect_contains=["[MOT] armed=0 drv_en_request=0", "expected_ENN=HIGH ENN_fb=1",
                        "verdict=OK"])
    # 14) 提交(只发 RAM; 不发 config_save)
    add("show_pre", "diag_profile show", key="diag_profile_show", seconds=1,
        expect_contains=["[DIAG] profile"])
    add("commit", "diag_profile commit", key="diag_profile_commit", seconds=1,
        expect_contains=["[DIAG] commit OK"], quota={"name": "diag_profile_commit", "n": 1})
    add("show_post", "diag_profile show", key="diag_profile_show", seconds=1,
        expect_contains=["[DIAG] profile"])
    add("ls_post", "diag_profile learn_status", key="ls_done8", seconds=1,
        expect_contains=["[DIAG] job id="])
    # 15) 栈/堆高水位(只报本工况)
    add("threads", "ps", key="ps", seconds=1,
        expect_contains=["stack size", "main"])
    add("heap", "free", key="free", seconds=1,
        expect_contains=["total    :", "used     :", "maximum  :", "available:"])
    return s


JOB_ROW = (r"^\[DIAG\] job id=(?P<id>[0-9]+) st=(?P<st>[A-Z_]+) why=(?P<why>[A-Z_]+) "
           r"owner=(?P<owner>[0-9]+) cred=(?P<cred>[0-9]/[0-9])$")
N_ROW = (r"^\[DIAG\] job n=(?P<adc>[0-9]+)/(?P<sg>[0-9]+) want=(?P<want>[0-9]+) "
         r"win=(?P<lo>[0-9]+)\.\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")


def plan_doc():
    st = steps()
    # tools 必须先取: 它会把 AXF 三元组读进全局 AXF, 下面 identity_binding 要用同一份值。
    tools_block = _inherited_tools()
    # F6(A 的补充审核): 三段预算**不在这个工具里另写一份算法** —— 直接 import 派生执行器里的
    # business_segments(), 由它算出申报窗合计与含每步 SLACK 的最坏上界, 再把结果原样写进计划的
    # motion_budget.business_segments 镜像。执行器计划门会逐字段比对这份镜像(PLAN_BUSINESS_
    # RESERVE_SPLIT 的 mirror= 字段), 所以"检查器与执行器使用同一值"是被机器判的, 不靠注释。
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import importlib
    exe = importlib.import_module("m8r3_serial_chain_r7f8")
    bs = exe.business_segments(st, cap=12, reserve=7, total=90)
    return {
        "package": "M8-R7F8F-FIELD-ONE-SESSION",
        "status": "OFFLINE_CANDIDATE_NOT_AUTHORIZED_NOT_EXECUTED",
        "task_id": "M8R7F8F-repair-and-field-20261105",
        "numbers_are_proposals_not_authorization": True,
        "hardware_actions_this_round": 0,
        "rows_file": "rows_r7f8f_real.json",
        "needles_file": None,
        "scope_this_package": (
            "在板固件 23691e8d… 身份连续(§4), 本轮唯一动作 = COM5 一次会话里跑 §5 的 15 步 + "
            "finally 固定收口。不下载、不回读、不复位、不动分析仪、不断送电、不重试。"),
        "field_boundary": {
            "polarity_measured": False,
            "core_protection_verified": False,
            "note": "safety_polarity_confirm 只是软件确认, 不升级为极性实测; 本轮 MONITOR_ONLY, "
                    "不造 SG/ADC/IMU 故障, 因此核心保护闭环未被本包证明。"},
        "identity_binding": {
            "note": "本轮没有 download/readback/reset 动作, 所以**不声明** board_image_before "
                    "字段(那条门的意思是「回读不许绑到旧镜像」; 无回读时它无对象)。旧镜像身份"
                    "只作参照写在 inherited_board_image。",
            "image": {"path": IMAGE_REL, "bytes": IMAGE_BYTES, "sha256": IMAGE_SHA,
                      "axf": dict(AXF),
                      "note": "参照身份: R7F7 已证在板的镜像(逐字节回读在 R7F7-F1/R7F8 轮已闭合)。"},
            "inherited_board_image": {"sha256": IMAGE_SHA, "bytes": IMAGE_BYTES,
                                      "proven_by": "M8-R7F7-F1 现场轮(唯一一次定长回读)"}},
        # tools 的三条 argv 从 R7F7 定稿计划**原样继承**(那些命令行已在现场成功用过), 不是新造;
        # image 子块换成本轮"参照身份"。本轮 download/readback/explicit_reset 额度全 0 且没有
        # 对应阶段, 所以这些 argv 是登记用, 不可能被执行。
        "tools": tools_block,
        "quota": {"port_open": 1, "dispatch": 1, "download": 0, "readback": 0,
                  "explicit_reset": 0, "approved_reboot": 0, "config_save": 0,
                  "tmc_config_apply": 1, "irq_polarity_prep": 2,
                  "diag_profile_zero": 1, "diag_profile_learn": 2,
                  "diag_profile_learn_cancel": 1, "diag_profile_commit": 1,
                  "motor_dir": 1, "motor_target": 1, "motor_arm": 1, "motor_run": 1,
                  "motor_stop": 1, "motor_disarm": 1,
                  "motor_run_seconds_total": 12, "motion_cap_seconds_per_run": 12,
                  "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                  "cleanup_motor_status": 1, "cleanup_safety_irq_disable": 1,
                  "cleanup_safety_irq_status": 1,
                  "retry": 0, "physical_collision": 0, "blackbox_clear": 0, "dmm_readings": 0},
        "motion_budget": {
            "start_deadline_seconds": 12, "arm_energize_deadline_seconds": 18,
            "session_total_seconds": 90, "cleanup_reserve_seconds": 7,
            "business_worst_declared_seconds": bs["worst_declared"],
            "business_segments": bs,
            "segments_source": "由 tools/m8r3_serial_chain_r7f8.py 的 business_segments() 现算"
                               "(cap=12/reserve=7/total=90 取任务书 §4 固定值); 本工具不另写一份"
                               "算术, 计划门再逐字段比对这份镜像 => 检查器与执行器同一值",
            "arithmetic": "会话绝对截止 = 90 s; 业务绝对截止 = 90 - 7 = 83 s(F5c 由执行器强制, "
                          "与运行时的收口预留保护同源); 运动截止从 motor_start 写入尝试起算 12 s "
                          "且**正常 stop 不清 t0**(执行器语义), 所以停机后的查询同被 t0+cap 夹住; "
                          "三段互不重叠: 起转前申报 %s s(最坏含每步 SLACK %s s) + 运动 cap %s s "
                          "+ 预留 %s s; 起转后申报内容 %s s 要与 cap 比, 不再重复累加一遍 cap"
                          % (bs["pre_declared"], bs["pre_bound"], bs["window_seconds"],
                             7, bs["after_spin_declared"])},
        "observation_bounds": {"cruise_poll": "最多 4 次 / 间隔 0.1 s / 整步 1 s(§5 行7)",
                               "cancel_poll": "最多 3 次 / 1 s(§5 行9)",
                               "done_poll": "最多 12 次 / 2 s(§5 行11)",
                               "stop_poll": "最多 5 次 / 1 s(§5 行12)"},
        # F6 静态三段算完之后, 本包在**离线门**上留下的两条真实红项(不放宽 §4 的任何数,
        # 也不靠负 seconds / 漏算余量消红)。两条都交给 A 定口径, 本工具只负责把它们算出来。
        "static_budget_open_items": [
            "PLAN_BUSINESS_RESERVE_SPLIT: 起转后申报内容 %s s(运动窗内 %s + 停机后 %s) > §4 的"
            " cap=12 s。执行器在 motor_start 写入尝试时置 MotionBudget.t0 且**正常 stop 不清**, "
            "所以 §5 行 12 之后(含 13 disarm / 14 commit / 15 ps+free)同被 t0+12 夹住; 按 §4"
            "\"stop 必须在 start 后 8 s 内发起\"排, 剩下约 4 s 排不下 6 条查询。要么 A 放宽 cap"
            "(会同时动 §6 的 motor_run_seconds_total=12), 要么 A 认可\"已确认零速后解除运动 cap\""
            "的运行时语义改动, 要么把 §5 行 14/15 挪到下一个会话。本轮不自行选择。"
            % (bs["after_spin_declared"], bs["in_motion_declared"], bs["post_stop_declared"]),
            "PLAN_BUSINESS_WORST_CASE_FIT: 每步都慢到 SLACK=3 s 上限时, 起转前 19 步的最坏合计"
            " %s s 已超业务预算 %s s, 第 %d 步(%s)会在写之前被 STEP_DEADLINE_BEFORE_WRITE 拒绝"
            " => §5 那 15 组(约 38 步)在**最坏到达**下跑不到起转。这是取证完整性风险, 不是撤能"
            "风险(收口预留由 business_abs_dl 硬夹保护, 与运行结果无关); 申报窗口本身合得下"
            "(worst_declared %s + 预留 7 <= 90)。消红只有减步或放宽总窗两条路, 本轮都不自行做。"
            % (bs["pre_bound"], bs["business_budget_seconds"],
               (bs["first_step_refused_before_write"] or {}).get("index", -1) + 1,
               (bs["first_step_refused_before_write"] or {}).get("cmd", "-"),
               bs["worst_declared"])],
        "field_assertion_gaps": [
            "diag_status: 只断言 [DIAG] 前缀回显(本轮未回源码核到该命令的字段行)",
            "tmc_config_show: 只断言 [TMC] 前缀回显(档位实值核对留给 apply 后的 CONFIGURED 行)",
            "diag_profile show / commit: 只断言 [DIAG] profile / [DIAG] commit OK; §5 行14 要的"
            "pending=VALID、id/rev/统计一致、match=YES 需要产品那几条打印的逐字形状, 本轮未取到,"
            " 因此**没有**写成字段判据(宁缺勿造)",
            "ps/free: 按 R7F7 已用过的表头断言存在, 不设阈值(只报本工况, 不称带载最坏栈)"],
        "byte_integrity_policy": "所有产物按磁盘原字节算 sha256; 清单不哈希自身",
        "operator_power_policy": "正常结束保持 VM+MCU 供电(§4); 不为清 RAM 重启或断电; "
                                 "异常时操作者用现有 SW1 硬件禁能并如实记录",
        "mechanical_prerequisites": {"bench_continuity": {
            "status": "DECLARED_BY_OPERATOR_AT_APPROVAL",
            "means": "§4 的机械/电气连续性由操作者那句真实批准承载, 不由本计划自证",
            "not_board_evidence": True}},
        "prerequisite_gates": ["bench_continuity"],
        "approval": {"note": "批准账只在最终身份(plan/rows/执行器/BIN 四件)确定后开; "
                             "令牌取操作者真实原话(§8 那一句), 不借旧批准。"},
        "stages": [{
            "id": "S1", "kind": "serial_session", "scenario": "monitor_only",
            "title": "COM5 单会话: 前态核对→apply→零点→IRQ→DIR1→arm→起转→取消N32→完成N8→"
                     "停机→disarm→commit→栈高水位(§5 全序)",
            "port": "COM5",
            "prerequisite_gates": ["bench_continuity"],
            "total_deadline_seconds": 90,
            "motion_cap_seconds": 12,
            "arm_energize_deadline_seconds": 18,
            "cleanup_reserve_seconds": 7,
            "forbidden_commands": ["config_save", "motor_run", "tmc_set", "fault_reset",
                                   "blackbox_clear", "diag_mode", "pwm_test", "safety_trip"],
            "steps": st}]}


def main() -> int:
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    img = repo / IMAGE_REL
    global IMAGE_SHA, IMAGE_BYTES
    if not img.is_file():
        raise SystemExit("REFUSED|在板镜像参照文件不在盘上: %s (镜像摘要只能当场重算, 不许手写占位值)" % img)
    b = img.read_bytes()
    IMAGE_SHA, IMAGE_BYTES = hashlib.sha256(b).hexdigest(), len(b)
    rows = rows_doc()
    plan = plan_doc()
    for rel, doc in ((ROWS_REL, rows), (PLAN_REL, plan)):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8", newline="\n")
        raw = p.read_bytes()
        print("WROTE|%s|bytes=%d|sha256=%s" % (rel, len(raw), hashlib.sha256(raw).hexdigest()))
    print("PLAN|steps=%d|business_worst=%s|quota_keys=%d" %
          (len(plan["stages"][0]["steps"]), plan["motion_budget"]["business_worst_declared_seconds"],
           len(plan["quota"])))
    print("IMAGE|bytes=%d|sha256=%s|在盘=%s" % (IMAGE_BYTES, IMAGE_SHA, img.is_file()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
