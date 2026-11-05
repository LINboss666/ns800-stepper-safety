# -*- coding: utf-8 -*-
"""R7F8E §5.2/§5.3: STUB_ONLY 的取消＋DONE 合成整包, 加一个等价的负例整包(四种具名收口)。

这一轮**不**生成 REAL_GO 计划, 不编真实端口/UID, 不建真实批准账:
  * transport=stub, backend=sim, SimClock, FakePort —— 一条命令都不会到板上;
  * 批准账只建在 --work 指的仓库外目录, 且绑的就是这份合成计划与本派生件;
  * 计划里的 JOB_ID 不是写死的 1: 取消试验与 DONE 试验各从自己那一步 STARTED 行绑定(合成 17/73)。

四个整包各自必须"预算／首错／清理结果"分别具名:
  N0 正常   —— 取消受理 + 有界轮询到 DONE + 固定清理三条, 全绿且额度账可复算;
  N1 拒绝   —— learn 被产品具名拒绝(SG_HEADROOM_INSUFFICIENT), 首错即停, 清理照跑;
  N2 到期   —— learn_status 永远 RUNNING, 轮询窗口耗尽 => POLL_WINDOW_EXPIRED, 清理照跑;
  N3 证据不完整 —— 状态行里带一个裸 0x00(现场实测过的控制台字节损坏形状) => NUL_BYTE_IN_RESPONSE,
                原字节先落盘, 首错不被后面的"好响应"冲掉, 清理照跑。

用法: python tools/r7f8e_stub_package.py --repo D:/ns800 --work <仓库外可写目录>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
EXE_REL = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
OLD_EXE_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
PLAN_DIR = ROUND + "/plan"
PLAN_REL = PLAN_DIR + "/plan_r7f8e_stub.json"
PLAN_WINDOW_REL = PLAN_DIR + "/plan_r7f8e_stub_window.json"
ROWS_REL = PLAN_DIR + "/rows_r7f8e.json"
IMAGE_REL = "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin"
IMAGE_SHA = "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963"
OLD_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"

JOB_ROW = (r"^\[DIAG\] job id=(?P<id>[0-9]+) st=(?P<st>[A-Z_]+) why=(?P<why>[A-Z_]+) "
           r"owner=(?P<owner>[0-9]+) cred=(?P<cred>[0-9]/[0-9])$")
N_ROW = (r"^\[DIAG\] job n=(?P<adc>[0-9]+)/(?P<sg>[0-9]+) want=(?P<want>[0-9]+) "
         r"win=(?P<lo>[0-9]+)\.\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")
LS = "diag_profile learn_status"
LEARN = "diag_profile learn 8 450 550 0"

out, fails = [], []


def note(name, ok, detail=""):
    out.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- 产品行形状(合成值, 逐字标注)
def started(jid):
    return ["[DIAG] learn STARTED id=%d n=8 win=450..550 dir=0" % jid,
            "[DIAG] learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED"]


def job(jid, st, why=None, owner=None, cred=None, adc=8, sg=8, want=8, lo=450, hi=550, d=0):
    """按**状态**取产品那一行的 why/owner/cred(定稿件与 F1 合同同源, 不再是手写"NONE"/owner=1)。

    diag_relative.c:1637 `nj.why = REL_JOB_WHY_RUN` + :1643 `d_cap_owner = REL_CAP_BY_JOB`
    => RUNNING 那一条印 `st=RUNNING why=RUNNING owner=2 cred=1/0`;
    :1674 取消请求把 why 置 CANCEL_REQUESTED(仍 owner=2); :1719 取消终态 why=CANCELLED/owner 归还 0;
    :1760 DONE 才 cred=1/1。之前这些合成值写的是 why=NONE/owner=1, 与产品不一致,
    返修件的作业合同因此在这一步具名拒绝(那是**合成值写错**, 不是合同过严)。
    """
    shape = {"RUNNING": ("RUNNING", 2, "1/0"),
             "CANCEL_PENDING": ("CANCEL_REQUESTED", 2, "1/0"),
             "CANCELLED": ("CANCELLED", 0, "1/0"),
             "DONE": ("COMPLETE", 0, "1/1"),
             "FAILED": ("WINDOW_BROKEN", 0, "1/0"),
             "TIMEOUT": ("DEADLINE", 0, "1/0"),
             "IDLE": ("IDLE", 0, "1/0")}.get(st, ("NONE", 0, "1/0"))
    why = shape[0] if why is None else why
    owner = shape[1] if owner is None else owner
    cred = shape[2] if cred is None else cred
    return ["[DIAG] job id=%d st=%s why=%s owner=%d cred=%s" % (jid, st, why, owner, cred),
            "[DIAG] job n=%d/%d want=%d win=%d..%d dir=%d" % (adc, sg, want, lo, hi, d)]


DISABLED = ["[MOT] state=0 name=OTHER", "[MOT] cur_hz=0 tgt_hz=0 accel=2000 decel=2000 dir=0",
            "[MOT] armed=0 drv_en_request=0 health=OK", "[MOT] drv_request=LOW pad=0",
            "[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK",
            "[MOT] diag_off_confirmed=YES"]


def done_full(jid, n=8, lo=450, hi=550, d=0, adc=None, sg=None):
    """F1 要的**完整** DONE 证据: 产品只在 state==DONE 且候选有效时才打这三类行
    (diag_relative.c:2056/2068/2074), 两行状态不构成"可提交"。"""
    adc = n if adc is None else adc
    sg = n if sg is None else sg
    return job(jid, "DONE", adc=adc, sg=sg, want=n, lo=lo, hi=hi, d=d) + [
        "[DIAG] job data adc[400..500]n%d nz2 sg[60..80]n%d seq101..%d" % (adc, sg, 100 + sg),
        "[DIAG] job thr tier=1 dev=30/60 drop=15/40 FIELD_UNVERIFIED",
        "[DIAG] job DONE result; cred zero=1 learn=1 commit is separate"]

# R7F8F F4: 运行/加速那两行必须是**使能侧**合同——产品只在请求使能时打 expected_ENN=LOW
# (motor.c:2491 `want ? "LOW" : "HIGH"`), 反馈 ENN_fb=0 才与 safety_gpio.c:273-296 的
# want_enabled=1 一致。修前这两行写的是 HIGH/1 + verdict=OK(禁能侧才有的组合), 那是**合成值写错了**
# 而不是产品会这么印: 返修件按 F4 判红之后, 本包必须换成真实形状, 而不是把判据放宽回去。
ACCEL = ["[MOT] state=1 name=ACCEL", "[MOT] cur_hz=200 tgt_hz=300 accel=2000 decel=2000 dir=0",
         "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
         "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
         "[MOT] diag_off_confirmed=NO"]
CRUISE = ["[MOT] state=2 name=CRUISE", "[MOT] cur_hz=300 tgt_hz=300 accel=2000 decel=2000 dir=0",
          "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
          "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
          "[MOT] diag_off_confirmed=NO"]
# 成功行同样取产品原文(motor.c:1328 arm / motor.c:1415 disarm): 义务只能被**整条**成功行解除,
# "DISARM" 这种子串在 F3 之后不当证据。
ARMED = ["[MOT] ARMED (drv_en request=HIGH readback verified, ENN feedback=LOW confirmed)"]
DISARMED = ["[MOT] DISARM (动作已做: STEP DONE / 许可 LOW verified / ENN feedback=OK)"]
STOPPED = ["[MOT] stop requested: target_hz=0 (软件命令值), STEP 实际关断结果见 "
           "stop_result(当前=OFF_CONFIRMED)"]


def rows_table(variant):
    """四个整包共用的行表形状, 只有被测的那一处不同(拒绝/永不 DONE/坏字节)。

    _about 把每一行的来源写清楚: 这些是**合成响应**, 形状取自产品打印语句, 值不是板端实测。
    每格都是"一个响应的若干行", 所以直接放 job(...) 的返回值, 不能再套一层列表
    (第一版套了层 => 喂端口时 join 到 list 上, 当场 TypeError)。
    """
    learn_row = (["[DIAG] learn REFUSED: SG_HEADROOM_INSUFFICIENT sg[14..26] need>=60"]
                 if variant == "refused" else started(17))
    # 证据不完整: 取消之后那条状态行里带一个裸 0x00(现场实测过的控制台字节损坏形状)。
    cancel_state = (["[DIAG] job id=17 st=CANCEL\x00LED why=USER_CANCEL owner=0 cred=1/0",
                     "[DIAG] job n=8/8 want=8 win=450..550 dir=0"]
                    if variant == "corrupt"
                    else job(17, "CANCELLED"))
    ls_done = (done_full(73) if variant == "normal" else job(73, "RUNNING"))
    return {
        "_about": {
            "kind": "SYNTHETIC_STUB_ROWS_NOT_BOARD_EVIDENCE",
            "shape_source": "行形状逐字取自 motor.c / diag_relative.c / diag_profile 的打印语句"
                            "(R7D1 签收轮已核对), 数值是本包为工装造的合成值",
            "variant": variant,
            "job_ids": "取消试验 17 / DONE 试验 73, 均来自各自那一步的 STARTED 行, 计划里没有写死",
            "hardware_actions": 0},
        "commands": {
            "status_static": {"healthy": DISABLED},
            "motor_dir": {"healthy": ["[MOT] dir=0 requested"]},
            "motor_arm": {"healthy": ARMED},
            "motor_target": {"healthy": ["[MOT] target=100"]},
            "motor_start": {"healthy": []},
            "status_accel": {"healthy": ACCEL},
            "status_cruise": {"healthy": CRUISE},
            "learn_cancel_trial": {"healthy": learn_row},
            "cancel_cmd": {"healthy": ["[DIAG] cancel id=17 REQUESTED st=CANCEL_PENDING "
                                       "(motor untouched)"]},
            "ls_cancelled": {"healthy": cancel_state},
            "learn_done_trial": {"healthy": started(73)},
            "ls_running": {"healthy": job(73, "RUNNING", adc=3, sg=3)},
            "ls_running2": {"healthy": job(73, "RUNNING", adc=6, sg=6)},
            "ls_done": {"healthy": ls_done},
            # 固定清理三条按**命令名**查表, 少一份就会变成假的 NO_RESPONSE。
            "motor_stop": {"healthy": STOPPED},
            "motor_disarm": {"healthy": DISARMED},
            "motor_status": {"healthy": DISABLED},
        }}


def steps():
    return [
        {"as": "baseline", "cmd": "motor_status", "key": "status_static", "seconds": 3,
         "expect_contains": ["state=0"]},
        {"as": "dir", "cmd": "motor_dir 0", "key": "motor_dir", "seconds": 2,
         "expect_contains": ["dir=0"]},
        {"as": "arm", "cmd": "motor_arm", "seconds": 3,
         "expect_contains": ["ARMED (drv_en request=HIGH readback verified"]},
        {"as": "target", "cmd": "motor_target 100", "key": "motor_target", "seconds": 2,
         "expect_contains": ["target=100"]},
        {"as": "start", "cmd": "motor_start", "seconds": 2, "expect_absent": ["REFUSED"],
         "quota": {"name": "motor_run", "n": 1}},
        {"as": "cruise", "cmd": "motor_status", "key": "status_cruise", "seconds": 4,
         "expect_profile": "RUNNING",
         "poll": {"until_seconds": 4.0, "interval_seconds": 0.5,
                  "retry_codes": ["PROFILE_NOT_RUNNING"]},
         "poll_sequence": ["status_accel", "status_cruise"]},
        {"as": "learn_cancel_job", "cmd": LEARN, "key": "learn_cancel_trial", "seconds": 3,
         "expect_contains": ["STARTED"], "quota": {"name": "diag_profile_learn", "n": 1},
         "job_bind": {"name": "cancel_job",
                      "params": {"n": 8, "lo": 450, "hi": 550, "dir": 0}}},
        {"as": "cancel", "cmd": "diag_profile learn_cancel @cancel_job", "key": "cancel_cmd",
         "seconds": 3, "job_cmd": "cancel_job", "expect_contains": ["REQUESTED"],
         "quota": {"name": "diag_profile_learn_cancel", "n": 1}},
        {"as": "cancel_state", "cmd": LS, "key": "ls_cancelled", "seconds": 3,
         "expect_values": [{"row": JOB_ROW, "must": {"st": "CANCELLED", "owner": "0"},
                            "job_binding": "cancel_job"}]},
        {"as": "learn_done_job", "cmd": LEARN, "key": "learn_done_trial", "seconds": 3,
         "expect_contains": ["STARTED"], "quota": {"name": "diag_profile_learn", "n": 1},
         "job_bind": {"name": "done_job",
                      "params": {"n": 8, "lo": 450, "hi": 550, "dir": 0}}},
        {"as": "wait_done", "cmd": LS, "key": "ls_done", "seconds": 4,
         "poll": {"until_seconds": 4.0, "interval_seconds": 0.5,
                  "retry_codes": ["FIELD_VALUE_MISMATCH", "PROFILE_NOT_RUNNING"],
                  "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                  "job_binding": "done_job", "max_attempts": 8},
         "poll_sequence": ["ls_running", "ls_running2", "ls_done"],
         "expect_values": [{"row": JOB_ROW, "must": {"st": "DONE", "owner": "0", "cred": "1/1"},
                            "job_binding": "done_job"},
                           {"row": N_ROW, "must": {"want": 8}, "must_min": {"adc": 8, "sg": 8}}]},
        {"as": "stop", "cmd": "motor_stop", "seconds": 3, "expect_contains": ["stop requested"]},
        {"as": "disarm", "cmd": "motor_disarm", "seconds": 3, "expect_contains": ["DISARM"]},
        {"as": "final", "cmd": "motor_status", "key": "motor_status", "seconds": 3,
         "expect_contains": ["armed=0"]},
    ]


def plan_doc(repo):
    # 镜像身份是**当场从归档文件算**的(不是抄登记值): 本包只把它当字节身份参照, 不读板也不烧板。
    img = repo / IMAGE_REL
    if not img.is_file():
        raise SystemExit("缺少镜像参照文件: %s" % img)
    raw = img.read_bytes()
    if sha(raw) != IMAGE_SHA:
        raise SystemExit("镜像参照文件与 R7F7 签收摘要不符: %s" % sha(raw))
    return {
        "package": "M8-R7F8E-STUB-ONLY",
        "status": "STUB_ONLY_SYNTHETIC_TOOL_VALIDATION",
        "task_id": "M8R7F8E-offline-executor-adaptation-20261105",
        "numbers_are_proposals_not_authorization": True,
        "hardware_actions_this_round": 0,
        "rows_file": "rows_r7f8e.json",
        "scope_this_package": (
            "只验工具适配本身: 异步作业的有界轮询、按 STARTED 绑定的动态 JOB_ID、"
            "取消的显式额度、arm 之后的通电截止与固定清理对账。"
            "不是现场计划, 不授权任何设备动作, 也不构成对下一轮硬件的批准。"),
        "scope_boundaries": {
            "not_a_real_go": "端口写的是占位符 STUB_PORT, 任何 real 分支都会在任何开口之前具名拒绝",
            "job_id_not_fixed": "计划里只有 @cancel_job / @done_job 两个引用; 实际数字来自那一步的"
                                " STARTED 行(合成 17/73), 派生命令与 ID 来源都进台账",
            "rows_are_synthetic": "rows_r7f8e*.json 的 _about.kind 明确标 SYNTHETIC_STUB_ROWS_NOT_"
                                  "BOARD_EVIDENCE; 形状取自产品打印语句, 值不是板端实测",
            "image_reference_only": "identity_binding.image 只是字节身份参照, 本轮不读板也不烧板"},
        "identity_binding": {
            "image": {"path": IMAGE_REL, "bytes": len(raw), "sha256": IMAGE_SHA,
                      "note": "R7F7 已签收在板镜像的字节身份参照(本文件当场重算相符); "
                              "本轮 0 次读取/写入板端, 也没有下载或回读动作"},
            "executor": {"path": EXE_REL, "old_executor_sha256": OLD_SHA,
                         "note": "派生件身份当场由 current_identities 从运行中的文件算, "
                                 "不冒用旧 33d3f0af 摘要"}},
        "byte_integrity_policy": "所有产物按磁盘原字节算 sha256; 清单不哈希自身",
        "operator_power_policy": "本轮不断电也不上电: 没有任何设备在回路里",
        # 起转阶段的机械前置门照常适用, 但这里唯一的"加载手段"就是合成喂入本身。
        # 它不是现场轮的机械事实, 也不是给真实刺激作保; 名称刻意叫 stub_feed_only 就是这个意思。
        "mechanical_prerequisites": {"stub_feed_only": {
            "status": "PROVIDED",
            "means": "FakePort 与本行表就是这个合成流程唯一的加载/激励手段, 不涉及真实机械",
            "not_board_evidence": True}},
        "motion_budget": {
            "per_run_cap": "运动截止从 motor_start 发送尝试起算(motor_run_seconds_total=62)",
            "energize_cap": "通电截止从 motor_arm 发送尝试起算(arm_energize_deadline_seconds=30), "
                            "早于 start 也要受它管",
            # F6(A 的补充审核)之后, 计划门把业务时间按**互不重叠**三段核到 cap/总窗里:
            # 这份合成夹具的起转后步骤申报合计 31 s(含每步 SLACK 则 61 s), 所以夹具的 cap
            # 必须容得下它, 否则整包流程序被静态门判红而到不了最后一条查询。
            # 现场轮的 cap 不是这个数(任务书 §4 固定 start 起 12 s), 由 real 计划的静态门
            # 单独判; 夹具放宽不放宽现场边界。
            "fixture_cap_why": "62 s 只为让夹具自己的申报窗口排得下(静态三段门), "
                               "不代表现场运动上限"},
        "tools": {"sim_backend": "SimActionBackend 只在内存里记账, 不调用任何外部进程",
                  "provenance": {"why": "本轮要求 backend=sim/transport=stub: "
                                        "不开 COM、不导入 pyserial 真后端、不启动 Logic、"
                                        "不跑 pyOCD/Keil"}},
        "quota": {"port_open": 1, "download": 0, "readback": 0, "explicit_reset": 0,
                  "tmc_config_apply": 0, "approved_reboot": 0, "config_save": 0,
                  "irq_polarity_prep": 0, "diag_profile_zero": 0,
                  "diag_profile_learn": 2, "diag_profile_learn_cancel": 1,
                  "diag_profile_commit": 0, "motor_run": 1, "motor_run_seconds_total": 62,
                  "motion_cap_seconds_per_run": 62,
                  "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1, "cleanup_motor_status": 1,
                  "retry": 0, "physical_collision": 0, "blackbox_clear": 0, "dmm_readings": 0},
        "approval": {"note": "临时账只建在仓外 --work 目录, 绑定这份合成计划与派生件字节"},
        "stages": [{
            "id": "S1", "kind": "serial_session", "scenario": "stub_only",
            "title": "取消试验 + DONE 试验 + 固定清理(全合成, 0 设备)",
            "port": "STUB_PORT_NOT_A_COM",
            "arm_energize_deadline_seconds": 30,
            "motion_cap_seconds": 62,
            "prerequisite_gates": ["stub_feed_only"],
            "total_deadline_seconds": 120,
            "steps": steps()}]}


def run(argv, repo, cwd=None):
    p = subprocess.run(argv, capture_output=True, cwd=str(cwd or repo),
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def emit(repo, rel, doc):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                 newline="\n")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    ap.add_argument("--emit-only", action="store_true",
                    help="只写计划/行表交付件, 不跑四个整包(供清单先算身份)")
    a = ap.parse_args()
    repo = a.repo.resolve()
    work = a.work
    exe = repo / EXE_REL
    py = sys.executable

    plan = plan_doc(repo)
    emit(repo, PLAN_REL, plan)
    # 轮询上限定成"次数与窗口都在"两个负例: 同一形状只把 max_attempts 抬高, 让**窗口**成为先到者。
    plan_w = json.loads(json.dumps(plan))
    for stg in plan_w["stages"]:
        for st in stg["steps"]:
            if (st.get("poll") or {}).get("job_binding") == "done_job":
                st["poll"]["max_attempts"] = 400
                st["poll"]["until_seconds"] = 1.5
    emit(repo, PLAN_WINDOW_REL, plan_w)
    emit(repo, ROWS_REL, rows_table("normal"))
    for v in ("refused", "never_done", "corrupt"):
        emit(repo, PLAN_DIR + "/rows_r7f8e_negative_%s.json" % v, rows_table(v))
    note("交付件已写出", (repo / PLAN_REL).exists() and (repo / ROWS_REL).exists(),
         "2 份计划(次数上限版+窗口上限版) + 4 份行表(正常/拒绝/永不DONE/坏字节); "
         "计划 %d B 窗口版 %d B 行表 %d B"
         % (len((repo / PLAN_REL).read_bytes()), len((repo / PLAN_WINDOW_REL).read_bytes()),
            len((repo / ROWS_REL).read_bytes())))
    if a.emit_only:
        text = "\n".join(out + ["R7F8E_STUB_EMIT"]) + "\n"
        print(text, end="")
        return 1 if fails else 0

    rc, vp, vpe = run([py, str(exe), "--validate-plan", "--plan", str(repo / PLAN_REL),
                       "--repo-root", str(repo)], repo)
    (work).mkdir(parents=True, exist_ok=True)
    (work / "stub_package_validate_plan.txt").write_text(vp + "\n" + vpe, encoding="utf-8",
                                                         newline="\n")
    vpfail = [l for l in vp.splitlines() if "|FAIL" in l]
    # 现场包合同里有三类判据天生要求"真实下载/回读工具面 + 真实镜像身份三元组"。
    # STUB_ONLY 合成包没有也不该有这些东西, 所以这三类**必须**红 —— 这正是它不会被误当成
    # 现场计划的结构性证据。除此之外一条都不许红。
    field_only = ("PLAN_TOOL_ARGV|", "PLAN_IMAGE_IDENTITY_CONSISTENCY|")
    unexpected = [l for l in vpfail if not l.startswith(field_only)]
    note("合成计划除现场包专属门外全绿", not unexpected and bool(vpfail) is True,
         "红=%d 条, 其中非现场专属门=%d 条%s" % (len(vpfail), len(unexpected),
                                          (" | " + " ;; ".join(x[:150] for x in unexpected[:5]))
                                          if unexpected else ""))
    note("现场专属门确实红在工具面/AXF 身份这类现场包合同上",
         len([l for l in vpfail if l.startswith("PLAN_TOOL_ARGV|")]) == 3
         and all(l.startswith(field_only) for l in vpfail),
         "红项=%s" % [l.split("|")[0] + ":" + l.split("|")[1] for l in vpfail])
    r7f8e_lines = [l for l in vp.splitlines() if l.startswith("R7F8E_")]
    note("本轮新增门在合成计划上全部通过",
         r7f8e_lines and all("|FAIL" not in l for l in r7f8e_lines),
         "R7F8E_ 行=%d 条, 其中 FAIL=%d" %
         (len(r7f8e_lines), len([l for l in r7f8e_lines if "|FAIL" in l])))

    # 修前基线: 同一份合成计划喂给**旧执行器**, 验证器不是给出具名 FAIL 而是当场崩
    # (AttributeError: 'NoneType' object has no attribute 'get')。原样留档, 不改旧件。
    rc_old, vp_old, vpe_old = run([py, str(repo / OLD_EXE_REL), "--validate-plan",
                                   "--plan", str(repo / PLAN_REL), "--repo-root", str(repo)], repo)
    (work / "stub_package_validate_plan_old_crash.txt").write_text(
        vp_old + "\n" + vpe_old, encoding="utf-8", newline="\n")
    crashed = "AttributeError" in (vp_old + vpe_old)
    note("旧件在同一输入上崩在建账之前(已登记并修)", crashed,
         "rc=%d 崩溃=%s 尾部=%s" % (rc_old, crashed, (vp_old + vpe_old).strip()[-120:]))

    results = {}
    RUNS = (("N0_正常", "normal", PLAN_REL), ("N1_拒绝", "refused", PLAN_REL),
            ("N2_次数上限", "never_done", PLAN_REL),
            ("N2b_窗口到期", "never_done", PLAN_WINDOW_REL),
            ("N3_证据不完整", "corrupt", PLAN_REL))
    for label, variant, plan_rel in RUNS:
        d = work / ("stub_%s" % label)
        if d.exists():
            shutil.rmtree(str(d), ignore_errors=True)
        (d / "ledgers").mkdir(parents=True)
        shutil.copyfile(str(repo / plan_rel), str(d / "plan_r7f8e_stub.json"))
        shutil.copyfile(str(repo / PLAN_DIR / ("rows_r7f8e.json" if variant == "normal"
                                               else "rows_r7f8e_negative_%s.json" % variant)),
                        str(d / "rows_r7f8e.json"))
        pl = d / "plan_r7f8e_stub.json"
        aid = "A_STUB_R7F8E_%s" % label.split("_")[0]
        rc_i, oi, oie = run([py, str(exe), "--approval-init", "--approval-id", aid,
                             "--approval-root", str(d / "ledgers"), "--plan", str(pl),
                             "--repo-root", str(repo)], repo)
        rc_d, od, ode = run([py, str(exe), "--dispatch", "--transport", "stub", "--backend",
                             "sim", "--profile", "healthy", "--plan", str(pl),
                             "--repo-root", str(repo), "--out", str(d / "run"),
                             "--approval-id", aid, "--approval-root", str(d / "ledgers")], repo)
        (d / "dispatch.txt").write_text(od + "\n" + ode, encoding="utf-8", newline="\n")
        # 每场的首错/撤能/清理事实都在**那条阶段行**的 JSON 里; DISPATCH_SUMMARY 的 code 是整包级
        # 汇总(本场 STOP 时它自己就是空的), 拿它当首错会得出"没有错误码"的假结论。
        stage = next((l for l in od.splitlines() if l.startswith("DISPATCH|stage=S1")), "")
        try:
            sj = json.loads(stage.split("|", 4)[4]) if stage else {}
        except (ValueError, IndexError):
            sj = {"code": "STAGE_LINE_UNPARSEABLE", "detail": stage[:200]}
        led = []
        lp = d / "run" / "S_S1" / "ledger.jsonl"
        if lp.exists():
            for ln in lp.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    led.append(json.loads(ln))
                except ValueError:
                    pass
        results[label] = {"init_rc": rc_i, "rc": rc_d, "out": od, "err": ode, "stage": sj,
                          "ledger": led,
                          "summary": next((l for l in od.splitlines()
                                           if l.startswith("DISPATCH_SUMMARY")), ""),
                          "quota": {l.split("|")[1]: (l.split("declared=")[1].split("|")[0],
                                                      l.split("used=")[1].split("|")[0])
                                    for l in od.splitlines() if l.startswith("QUOTA_AUDIT|")}}
        note("%s 离账本建立成功" % label, rc_i == 0,
             "init rc=%d%s" % (rc_i, (" err=" + oie[-200:]) if rc_i else ""))

    # 真实分支的**入口**探针: 同一份合成计划换成 --transport real, 必须在任何端口动作之前就被具名
    # 拒绝。这里刻意不传批准身份也不给端口名 —— 目的是证明"到不了开口那一步", 而不是去试开口。
    d = work / "stub_real_branch_probe"
    if d.exists():
        shutil.rmtree(str(d), ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copyfile(str(repo / PLAN_REL), str(d / "plan_r7f8e_stub.json"))
    shutil.copyfile(str(repo / ROWS_REL), str(d / "rows_r7f8e.json"))
    rc_r, or_, ere = run([py, str(exe), "--dispatch", "--transport", "real", "--backend", "sim",
                          "--profile", "healthy", "--plan", str(d / "plan_r7f8e_stub.json"),
                          "--repo-root", str(repo), "--out", str(d / "run")], repo)
    (d / "dispatch_real.txt").write_text(or_ + "\n" + ere, encoding="utf-8", newline="\n")
    blob_r = or_ + ere
    codes = sorted(set(
        [l.split("APPROVAL_GATE_REFUSED:")[1].split("|")[0]
         for l in blob_r.splitlines() if "APPROVAL_GATE_REFUSED:" in l] +
        [l.split("MODE_COMBINATION_REFUSED:")[1].split("|")[0].strip()
         for l in blob_r.splitlines() if "MODE_COMBINATION_REFUSED:" in l] +
        [l.split("code=")[1].split("|")[0].split(",")[0]
         for l in blob_r.splitlines() if "code=" in l]))
    no_port = not [p for p in d.rglob("*.jsonl")
                   if "PORT_OPEN" in p.read_text(encoding="utf-8", errors="replace")]
    note("合成计划的 real 分支在任何端口动作之前具名拒绝",
         rc_r != 0 and bool(codes) and no_port,
         "rc=%d 拒绝=%s 台账里的开口事件=%s" % (rc_r, codes[:3], "0 条" if no_port else "有!"))

    def first_error(label):
        return results[label]["stage"].get("code") or ""

    def field(label, key):
        return results[label]["stage"].get(key)

    def ledger_of(label, kind):
        return [e for e in results[label]["ledger"] if e.get("kind") == kind]

    n0 = results["N0_正常"]
    note("N0 正常整包收口为成功",
         n0["rc"] == 0 and first_error("N0_正常") == ""
         and n0["stage"].get("success") is True
         and n0["stage"].get("disarm") == "CONFIRMED_DISABLED",
         "rc=%d code=%r success=%s disarm=%s terminal=%s"
         % (n0["rc"], first_error("N0_正常"), n0["stage"].get("success"),
            n0["stage"].get("disarm"), n0["stage"].get("terminal")))
    note("N0 额度账可复算(learn 2 / cancel 1 / motor_run 1 / 清理 1+1+1)",
         n0["quota"].get("diag_profile_learn") == ("2", "2")
         and n0["quota"].get("diag_profile_learn_cancel") == ("1", "1")
         and n0["quota"].get("motor_run") == ("1", "1")
         and all(n0["quota"].get("cleanup_" + c) == ("1", "1")
                 for c in ("motor_stop", "motor_disarm", "motor_status")),
         "learn=%s cancel=%s motor_run=%s cleanup=%s/%s/%s"
         % (n0["quota"].get("diag_profile_learn"), n0["quota"].get("diag_profile_learn_cancel"),
            n0["quota"].get("motor_run"), n0["quota"].get("cleanup_motor_stop"),
            n0["quota"].get("cleanup_motor_disarm"), n0["quota"].get("cleanup_motor_status")))
    binds = {e.get("name"): e.get("job_id") for e in ledger_of("N0_正常", "JOB_ID_BOUND")}
    cancel_tx = [e.get("bytes", "").strip() for e in ledger_of("N0_正常", "TX")
                 if "learn_cancel" in (e.get("bytes") or "")]
    note("N0 动态 JOB_ID 非固定且两次绑定不同(台账+实发双证)",
         binds == {"cancel_job": 17, "done_job": 73}
         and cancel_tx == ["diag_profile learn_cancel 17"],
         "绑定=%s 实发取消命令=%s" % (binds, cancel_tx))
    note("N0 轮询不重发 learn(learn 命令恰好 2 次=两次试验本身)",
         len([e for e in ledger_of("N0_正常", "TX")
              if (e.get("bytes") or "").startswith("diag_profile learn ")]) == 2
         and len(ledger_of("N0_正常", "TX")) >= 3 + 11,
         "learn 发送=%d 全部 TX=%d 条"
         % (len([e for e in ledger_of("N0_正常", "TX")
                 if (e.get("bytes") or "").startswith("diag_profile learn ")]),
            len(ledger_of("N0_正常", "TX"))))
    def tx_count(label, cmd):
        return len([e for e in ledger_of(label, "TX") if (e.get("bytes") or "").strip() == cmd])

    got = [tx_count("N0_正常", c) for c in ("motor_stop", "motor_disarm", "motor_status")]
    # motor_status 的 5 次是分解得出来的, 不是"发多了": 基线 1 + 巡航轮询 2 + 收尾 1 + 固定清理 1。
    note("N0 固定清理三条与业务段分别记账(实发次数可分解)",
         got == [2, 2, 5],
         "stop/disarm/status 实发=%s 分解: stop=业务1+清理1, disarm=业务1+清理1, "
         "status=基线1+巡航轮询2+收尾1+清理1" % (got,))

    exp = {"N1_拒绝": "BUSINESS_FAILED",
           # 两条"到期"分开: 同一形状下次数上限先到, 抬高次数后窗口先到 —— A 的 §4 要的就是
           # "窗口到期与最大次数都能结束", 不能只演其中一种。
           "N2_次数上限": "POLL_ATTEMPTS_EXHAUSTED", "N2b_窗口到期": "POLL_WINDOW_EXPIRED",
           "N3_证据不完整": "NUL_BYTE_IN_RESPONSE"}
    for label, code in exp.items():
        r = results[label]
        note("%s 首错具名为 %s" % (label, code),
             r["rc"] != 0 and first_error(label) == code,
             "rc=%d code=%r detail=%s" % (r["rc"], first_error(label),
                                          str(r["stage"].get("detail"))[:150]))
        note("%s 仍走清理并如实披露撤能结果" % label,
             field(label, "obligation") is True and field(label, "success") is False,
             "obligation=%s disarm=%s success=%s cleanup_errors=%s"
             % (field(label, "obligation"), field(label, "disarm"), field(label, "success"),
                field(label, "cleanup_errors")))
    n3 = results["N3_证据不完整"]
    fields_ev = [e for e in n3["ledger"] if e.get("kind") == "DISARM_FIELDS"]
    note("N3 坏字节不当成功, 禁能只能由清理里那份**新的完整快照**承担",
         first_error("N3_证据不完整") == "NUL_BYTE_IN_RESPONSE"
         and field("N3_证据不完整", "success") is False
         and bool(fields_ev) and not fields_ev[-1].get("unproven")
         and field("N3_证据不完整", "disarm") == "CONFIRMED_DISABLED",
         "首错=%s success=%s 清理快照字段缺失=%s disarm=%s"
         % (first_error("N3_证据不完整"), field("N3_证据不完整", "success"),
            (fields_ev[-1].get("unproven") if fields_ev else "无 DISARM_FIELDS 事件"),
            field("N3_证据不完整", "disarm")))
    note("N1 被拒的 learn 仍消耗一次额度(不退额)",
         results["N1_拒绝"]["quota"].get("diag_profile_learn") == ("2", "1"),
         "learn declared/used=%s" % (results["N1_拒绝"]["quota"].get("diag_profile_learn"),))
    note("本轮无设备动作", True,
         "四个整包都是 --transport stub --backend sim; real 分支只做了入口探针(被具名拒绝, "
         "台账里 0 条开口事件); 批准账只在 --work 仓外目录; 未开 COM/未跑 pyOCD/未跑 Keil/"
         "未启动分析仪")

    lines = []
    for label in ("N0_正常", "N1_拒绝", "N2_次数上限", "N2b_窗口到期", "N3_证据不完整"):
        lines.append("== %s 的阶段行与 DISPATCH_SUMMARY 原文 ==" % label)
        lines.append(json.dumps(results[label]["stage"], ensure_ascii=False))
        lines.append(results[label]["summary"])
        lines.append("   rc=%d init_rc=%d" % (results[label]["rc"], results[label]["init_rc"]))
    # 仓外试验目录的路径不进交付件: 它既非仓库事实, 又把本机目录结构写进公开仓库。
    # 掩的是**整份正文**(判据行自己也可能带路径), 两条新判据的行在掩之后再追加。
    body = list(out) + [""] + lines
    text = "\n".join(body) + "\n"
    forms = [str(work), str(work).replace("\\", "/"), str(work).replace("\\", "\\\\"), work.name]
    hits = [f for f in forms if f in text]            # 掩之前必须真有, 否则这条判据恒真空
    for f in forms:
        text = text.replace(f, "<WORK>")
    rest = [f for f in forms if f in text]
    n_before = len(out)
    # detail 里**不打印形态本身**: 打出来就是这条判据要防的那件泄漏, 只报数量。
    note("交付件里不留仓外试验目录(四种形态逐一掩成 <WORK>)", not rest,
         "掩前命中=%d/4 种, 掩后残留=%d 种" % (len(hits), len(rest)))
    note("这条掩码判据不是恒真空(掩前确实出现过)", bool(hits), "掩前命中=%d/4 种" % len(hits))
    full = text + "\n".join(out[n_before:]) + "\n" + "R7F8E_STUB_SUMMARY|判据=%d|FAIL=%d|%s\n" % (
        len(out), len(fails), ",".join(fails) if fails else "none")
    # 末道硬门(不计入判据, 只拒绝交付): 合成后的整份文件里仍不得出现任何仓外形态。
    leaked = [i for i, f in enumerate(forms) if f in full]
    if leaked:
        print("STUB_PACKAGE_REFUSED|合成后仍残留 %d 种仓外路径形态 => 掩码顺序要修" % len(leaked))
        return 2
    (repo / ROUND / "proof").mkdir(parents=True, exist_ok=True)
    (repo / ROUND / "proof/r7f8e_stub_package.txt").write_text(full, encoding="utf-8", newline="\n")
    print(full, end="")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
