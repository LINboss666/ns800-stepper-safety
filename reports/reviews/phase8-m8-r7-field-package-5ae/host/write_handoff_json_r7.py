#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 M8-R7 的审核交接回执 JSON(给云端 A / 操作者)。

纪律(与前几轮同一条):
  * 所有 bytes/sha256 都是**当场从磁盘算**的, 抄不进任何自述值;
  * 顶层不放本件自己的哈希(一条清单不能给自己的字节作证);
  * 门与自检的数字**从入库原文里 grep 出来**, 不手抄; 原文不存在就把 None 交出去, 如实暴露缺口;
  * 远端逐 blob 回读结果在收口一步补进来(回读原文先入库), 不在这里预写。

R7-F1 之后本件多了一个 `field_execution` 块: 现场一轮的原文摘录 + 从原始字节**现算**的行宽/NUL/UTF-8
事实, 且 `device_actions` 改成 F1 实耗(准备轮的 0 由 `device_actions_note` 单独留着, 两条时间线不混写)。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ROUND = HERE.parent
OUT = REPO / "reports/agent_handoffs/phase8-m8-r7-field-package-5ae.json"
R7 = "reports/reviews/phase8-m8-r7-field-package-5ae"
R6C = "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0"
R3F = "reports/reviews/phase8-m8-relative-protection-r3/field"
FW6A = "reports/firmware/phase8-m8-r6a-offline-rework"
R4F = "reports/reviews/phase8-m8-idle-stack-r4/field"
DSF = "reports/reviews/phase8-m8-diag-stack-r1/field"

EXEC = R3F + "/m8r3_serial_chain.py"
SELFTEST = R3F + "/m8r3_field_selftest.py"
PLAN = R7 + "/field/plan_core_r7.json"
ROWS = R7 + "/field/rows.json"
SRC_PLAN = R6C + "/field/plan_core_r6c.json"

BASE = "f15ba464628d282f5d7fca91de79b6f702c54dfa"      # A 实际审核并签收的 R6C 收口头
IMAGE_BUILD_INPUT = "44e493cea9bbbc35e18394ca23dd1a8502519448"   # 5ae 那次构建的源码输入
OLD_TOKEN = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"

ARTIFACTS = [
    ("plan_package", PLAN),
    ("dryrun_fixture_rows", ROWS),
    ("deriver", R7 + "/host/derive_plan_r7.py"),
    ("fixture_maker", R7 + "/host/make_rows_r7.py"),
    ("readiness_harness", R7 + "/host/r7_readiness.py"),
    ("transcript_runner", R7 + "/host/run_transcripts_r7.py"),
    ("derive_matrix_runner", R7 + "/host/run_derive_matrix_r7.py"),
    ("validate_plan_transcript", R7 + "/host/validate_plan_r7.txt"),
    ("readiness_transcript", R7 + "/host/readiness_transcript.txt"),
    ("derive_write_semantics_matrix", R7 + "/host/derive_matrix.txt"),
    ("run_record", R7 + "/run_record.md"),
    ("handoff_md", "reports/agent_handoffs/phase8-m8-r7-field-package-5ae.md"),
    ("frozen_derivation_source", SRC_PLAN),
    ("executor_reused", EXEC),
    ("selftest_reused", SELFTEST),
    ("candidate_image_bin", FW6A + "/rt-thread-motor-m1-candidate.bin"),
    ("candidate_image_axf", FW6A + "/rt-thread-motor-m1.axf"),
    ("candidate_image_map", FW6A + "/rt-thread-motor-m1.map"),
    ("build_manifest", FW6A + "/build_manifest.json"),
    ("proven_on_board_readback", R4F + "/field-20261101T142722Z/readback.bin"),
    ("earlier_on_board_readback", DSF + "/field-20261101T084256Z/flash_readback.bin"),
]

# ---- R7-F1 现场执行轮(操作者一次整包批准)新增的输入与原件 ------------------------------
A_REVIEW_MD = R7 + "/a_review_NS800_M8_R7_Boot_Print_Package_20261103.md"
REVIEWED_PLAN = R7 + "/field/plan_core_r7_reviewed.json"
FIELD = R7 + "/field/field-20261103T132348Z"
FIELD_ARTIFACTS = [
    ("approval_token_verbatim", FIELD + "/APPROVAL_TOKEN.verbatim.txt"),
    ("dispatch_stdout", FIELD + "/stdout_dispatch.txt"),
    ("dispatch_ledger", FIELD + "/dispatch_ledger.jsonl"),
    ("session_ledger_S3", FIELD + "/S_S3/ledger.jsonl"),
    ("boot_stream_raw", FIELD + "/S_S3/01_rst.boot.rx.bin"),
    ("sysboot_response_raw", FIELD + "/S_S3/02_sysboot.rx.bin"),
    ("field_readback_bin", FIELD + "/readback.bin"),
    ("rc_download", FIELD + "/action_download/rc.txt"),
    ("rc_readback", FIELD + "/action_readback/rc.txt"),
    ("rc_reset", FIELD + "/action_reset/rc.txt"),
    ("field_judgement", FIELD + "/OFFLINE_JUDGEMENT.txt"),
    ("a_review_document", A_REVIEW_MD),
    ("a_reviewed_plan", REVIEWED_PLAN),
]
for _k, _p in FIELD_ARTIFACTS:
    if (REPO / _p).exists():
        ARTIFACTS.append((_k, _p))


def sha(rel):
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def nb(rel):
    return (REPO / rel).stat().st_size


def grep(rel, needles):
    """从入库原文里取汇总行 —— 数字不手抄; 文件不存在返回 None(缺口如实暴露)。"""
    p = REPO / rel
    if not p.exists():
        return None
    out = []
    for ln in p.read_bytes().decode("utf-8", "replace").splitlines():
        if any(n in ln for n in needles):
            out.append(ln.strip())
    return out


plan = json.loads((REPO / PLAN).read_bytes().decode("utf-8-sig"))
ib = plan["identity_binding"]

READBACK_TRANSCRIPT = R7 + "/remote_readback_r7.txt"
READBACK_TOOL = R7 + "/host/remote_readback_r7.py"


def readback_block():
    """收口一步跑完远端逐 blob 回读并入库原文后, 重跑本脚本就会自动填上; 之前是 None(如实缺口)。"""
    lines = grep(READBACK_TRANSCRIPT, ["R7_REMOTE_READBACK_SUMMARY", "FAILED_CHECKS=", "NOT_CHECKED="])
    if not lines:
        return None
    return {"transcript": READBACK_TRANSCRIPT,
            "transcript_bytes": nb(READBACK_TRANSCRIPT),
            "transcript_sha256": sha(READBACK_TRANSCRIPT),
            "tool": READBACK_TOOL,
            "summary_lines_grepped_not_hand_copied": lines}

STATUSES = [
    "R6C_evidence = ACCEPTED(A 2026-11-03 独立复审, OFFLINE_REVIEW_GO)",
    "R7_package_offline_prep = R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED(准备轮 0 设备动作)",
    "R7_original_plan_field_use = DO_NOT_EXECUTE_AS_SUBMITTED"
    "(A 复审判: 原状态判据会把正常启动回包误拒, 且构建来源混入旧 dab 登记)",
    "R7_F1_field_run = EXECUTED_ONCE_STOPPED_AT_S3(工具退出码 1; 执行对象是 A 校正版原字节)",
    "new_image_5ae_on_board = PROVEN_BYTE_EXACT(292164 B, readback.bin 与归档 BIN 逐字节相等)",
    "boot_to_READY = FAILED_IN_FIELD(state=FAULT_LATCHED fault=8=FAULT_SELF_TEST, safety_state.h:48; "
    "生产者=[SS] [REQ FAIL] TMC2209 IOIN read rc=-23)",
    "A_corrections_effective_in_field = CONFIRMED(arm-gate 披露行走白名单, polarity=not-confirmed 被接受; "
    "停因是真值不符而不是误拒)",
    "short_print_target_lines = NOT_REACHED / NOT_VERIFIED(safety_irq_status 与 S4 三条 CONFIGURED 未到达)",
    "line_width_field_fact = 单条响应 0/21 行超 120B 且无 NUL; 启动被动流 2/75 分段超 120B + 3 裸 NUL + "
    "整体非法 UTF-8(并发打印互相咬, 不择一归因)",
    "executor_coverage_gap = REGISTERED(被动启动流只过危险文本门, 不过 NUL/UTF-8 门; 本轮不修)",
    "motion = NONE_SENT(motor_*/learn/zero/commit/config_save/blackbox_clear 额度 0 且未尝试)",
    "disarm_closeout = NOT_ATTEMPTED(本包无运动; A 并纠正了'load_means 必然阻断普通 disarm'这一口径)",
    "load_means = NOT_PROVIDED",
    "validate_plan_on_reviewed_bytes = 47 项 / fail=1(唯一 FAIL 仍是 PLAN_MOTION_CAP_BUDGET 恒红门)",
    "readiness = 17/17 OK(含整包 stub 干跑, 0 设备)",
    "R6A_candidate_image_5ae = ON_BOARD_AND_BYTE_PROVEN(不再是'仅归档'; 旧 d70/dab 归档未覆盖)",
    "executor/selftest = 字节与 R6C 相同(现场轮也没改执行器)",
    "product_C_and_firmware_archive = 本轮 0 改动; keil_builds=0",
    "quota_after_F1 = download 1/1, readback 1/1, explicit_reset 1/1, port_open 1/2, apply 0/1; "
    "不结转也不重跑",
    "board_state_at_run_time = 输出禁能已证(pad LOW + ENN 禁能侧 + armed=0) 且 Safety FAULT_LATCHED; "
    "断电/上电或 fault_reset 人工恢复需操作者另批",
    "product_field_fix = NOT_VERIFIED",
    "overall_release = 待 A 按现场真实字节复审(本轮不自签)",
    "safe_for_enable = false",
    "new_hardware_authorization = 0(本件不发批准; F1 的批准=操作者那条原话, 记在仓外持久账)",
    "device_actions_offline_prep = 0 | device_actions_F1 = download 1 / readback 1 / reset 1 / port_open 1",
    "keil_builds = 0",
    "next_work = 交 A 复审现场原件; 在那之前不再动设备",
]

def line_stats(rel):
    """从**原始字节**现算行宽/NUL/UTF-8 事实(不抄任何自述数字)。"""
    import re
    raw = (REPO / rel).read_bytes()
    parts = re.split(b"\r\n|\n", raw)
    widths = [len(p) for p in parts]
    try:
        raw.decode("utf-8")
        u = {"utf8_valid": True}
    except UnicodeDecodeError as e:
        u = {"utf8_valid": False, "utf8_first_bad_offset": e.start}
    return dict(bytes=len(raw), sha256=sha(rel), segments=len(parts),
                longest_segment_bytes=max(widths) if widths else 0,
                segments_over_120B=sum(1 for w in widths if w > 120),
                nul_bytes=raw.count(b"\x00"),
                nul_offsets=[i for i, c in enumerate(raw) if c == 0][:8], **u)


def field_exec_block():
    if not (REPO / (FIELD + "/OFFLINE_JUDGEMENT.txt")).exists():
        return None
    stages = grep(FIELD + "/stdout_dispatch.txt", ["DISPATCH|stage="]) or []
    summ = grep(FIELD + "/stdout_dispatch.txt", ["DISPATCH_SUMMARY|", "APPROVAL_LEDGER|"]) or []
    qa = [l for l in (grep(FIELD + "/stdout_dispatch.txt", ["^QUOTA_AUDIT|download",
                                                            "^QUOTA_AUDIT|readback",
                                                            "^QUOTA_AUDIT|explicit_reset",
                                                            "^QUOTA_AUDIT|port_open",
                                                            "^QUOTA_AUDIT|tmc_config_apply"]) or [])]
    stop = grep(FIELD + "/S_S3/ledger.jsonl", ["VALUE_ASSERT_FAIL", "SESSION_STOP",
                                               "BOOT_STREAM_DRAINED", "RX_DONE",
                                               "APPROVED_PACKAGE_VERIFIED", "PORT_CLOSED"]) or []
    tags = grep(FIELD + "/OFFLINE_JUDGEMENT.txt", ["R7_F1_field_run", "new_image_5ae_on_board",
                                                   "boot_to_READY", "TMC_IOIN_selftest_read",
                                                   "short_print_target_lines", "line_width_observed",
                                                   "executor_coverage_gap", "motion",
                                                   "board_output_state", "product_field_fix",
                                                   "safe_for_enable", "quota_this_package",
                                                   "device_actions_this_round"]) or []
    rb = (REPO / (FIELD + "/readback.bin")).read_bytes()
    arch = (REPO / (FW6A + "/rt-thread-motor-m1-candidate.bin")).read_bytes()
    return {
        "approved_by": "操作者 2026-11-03 一次整包批准(原文逐字在 approval_token_verbatim 指到的文件里)",
        "approval_id": "M8R7F1-reviewed-bootprint-20261103",
        "approval_ledger_root": "D:/ns800_field_approvals (仓外持久账, 换 --out/重开进程不清零)",
        "executed_plan_bytes_are": "A 的校正版 plan_core_r7_reviewed.json(fea903b4…) 原字节; "
                                   "未重跑派生器, 未改任何字节来登记批准",
        "command": "python m8r3_serial_chain.py --dispatch --transport real --backend real "
                   "--profile response_driven --plan <reviewed> --repo-root D:/ns800 "
                   "--approval-id <id> --approval-root D:/ns800_field_approvals "
                   "--approval-token=<原文> --expected-token=<同一原文> --uid-file <既存私有 UID 文件> "
                   "--port COM5 --out " + FIELD,
        "tool_exit_code": 1,
        "stage_lines_grepped": stages,
        "stop_detail_grepped_from_session_ledger": stop,
        "quota_audit_grepped": qa,
        "summary_grepped": summ,
        "image_on_board_byte_exact": {"bytes": len(rb), "equal_to_archived_bin": rb == arch,
                                      "sha256": hashlib.sha256(rb).hexdigest()},
        "line_and_byte_facts_recomputed_from_raw": {
            "sysboot_response": line_stats(FIELD + "/S_S3/02_sysboot.rx.bin"),
            "passive_boot_stream": line_stats(FIELD + "/S_S3/01_rst.boot.rx.bin")},
        "judgement_status_tags_grepped": tags,
        "not_verified_after_this_run": [
            "启动到 READY(本场 FAIL: FAULT_LATCHED / fault=8 = FAULT_SELF_TEST, safety_state.h:48)",
            "[SAFETY] LIMIT 两条与 [TMC-CFG] CONFIGURED 三条(safety_irq_status 与整个 S4 未到达)",
            "正常 RUN→disarm 收口 / SG/ADC 真实加载 / 阈值 / ACTIVE / REL_IMPACT / 保存恢复 / 故障恢复闭环",
            "TMC IOIN rc=-23 的根因(丢帧 / 总线未就绪 / 时序 均未排除; 不据症状改产品码)"],
        "did_not_do": ["不重跑整包", "不增加任何一次动作", "不改判据/计划/执行器",
                        "不用剩下的 1 次 port_open 与 1 次 tmc_config_apply 顺手再看",
                        "不清黑匣子", "不保存配置", "不起转", "不复活旧 4cb9dac0 批准"]}


doc = {
    "task": "M8-R7-BOUNDED-FIELD-VALIDATION-PACKAGE-5AE",
    "upstream_review": "NS800_M8_R6C_Independent_Review_OFFLINE_GO_20261103.md(云端 A, 2026-11-03)",
    "upstream_verdict_verbatim": (
        "接收R6C交付，C1/C2在本轮限定范围内离线收口，裁定OFFLINE_REVIEW_GO。……"
        "下一步应转向新镜像的有界现场验证准备，优先验证启动、相关短打印和正常disarm收口。"
        "现场执行仍须有匹配新镜像、AXF、回读长度、步骤与具体额度的独立现场包；"
        "本文件不提供该包或批准，旧额度不结转。"),
    "how_this_round_was_authorized": (
        "A 的 GO 只给方向(next_work=PREPARE_BOUNDED_FIELD_VALIDATION_PACKAGE), 不发设备额度; "
        "本轮范围由用户在会话内选定为'开 R7 离线准备并入库'。全程 0 设备动作。"),
    "date_local": "2026-11-03",
    "branch": "feature/m8-sg-adc-relative-protection",
    "remote": "https://github.com/LINboss666/ns800-stepper-safety",
    "base_commit": BASE,
    "base_commit_note": "开工时本地 HEAD == A 签收的 R6C 收口头, 工作树干净; 未新建分支, "
                        "未 reset/clean/rebase/amend/force, 冻结引用未动",
    "image_build_source_input_commit": IMAGE_BUILD_INPUT,
    "image_build_source_input_note": "本包不重编; 5ae4110d…/292164 B 是 R6A 轮那次构建的归档件, "
                                     "其源码输入登记在该轮的 build_manifest 里",
    "evidence_commit": "本 JSON 所在提交",
    "closure_commit": "推送与远端逐 blob 回读所在提交(在其后顺序追加)",
    "delivery_head_rule": "常规提交/push 后按精确 SHA 独立逐 blob 回读一次即停止; "
                          "不为回读自己的新头反复追加提交",
    "roles": "云端 A 出方向与独立复审; 本地 Qoder 准备候选包; 操作者=人(本件不含任何硬件批准, "
             "也不代替操作者回答现场安全门)",
    "status": "M8_R7_OFFLINE_CANDIDATE_DELIVERED + R7_F1_FIELD_RUN_EXECUTED_STOPPED_AT_S3 "
              "/ AWAITING_INDEPENDENT_REVIEW_OF_FIELD_BYTES",

    "scope_covered": plan["scope_this_package"]["covers"],
    "scope_not_covered": plan["scope_this_package"]["does_not_cover"],
    "why_disarm_closeout_is_excluded": (
        "正常 disarm 之后的 Safety 收口要先 motor_arm; motor_arm/motor_start 属执行器的 "
        "MOTION_OBLIGATION_CMDS(m8r3_serial_chain.py:1412), 而包级 mechanical_prerequisites."
        "load_means 仍是 NOT_PROVIDED => 执行器在开端口之前具名拒绝整条链。把运动步骤写进包里再"
        "'现场再看'等于制造一条一定会烧掉批准的中途失败, 所以本包不含运动, 也不声称覆盖它。"),
    "package_shape": {
        "task_id": plan["task_id"],
        "status": plan["status"],
        "package_status": plan["package_status"],
        "stages": [{"id": s["id"], "kind": s["kind"], "steps": len(s.get("steps") or []),
                    "requires": s.get("requires")} for s in plan["stages"]],
        "quota_proposal_nonzero": {k: v for k, v in plan["quota"].items() if v},
        "quota_zero_because": plan["quota_rationale"]["zero_because"],
        "boot_window_arithmetic": "drain 75 + 首条查询 10 + 3s 余量 = 88 >= 下界 75; 未申报 boot_quiet_seconds",
        "forbidden_commands_per_session": [sorted(s.get("forbidden_commands") or [])
                                           for s in plan["stages"] if s["kind"] == "serial_session"],
        "readback_image_bytes_hex": "0x%X" % ib["image"]["bytes"]},

    "image_identity": {
        "bin": {"path": ib["image"]["path"], "bytes": ib["image"]["bytes"],
                "sha256": ib["image"]["sha256"]},
        "axf": ib["image"]["axf"], "map": ib["image"]["map"],
        "manifest": ib["image"]["manifest"],
        "board_image_before_this_download": {"path": ib["board_image_before"]["path"],
                                            "bytes": ib["board_image_before"]["bytes"],
                                            "sha256": ib["board_image_before"]["sha256"]},
        "not_overwritten_archives": ["5ae4110d…", "d70a9180…", "dab356c6… 三件归档 BIN 一件都不覆盖"]},

    "corrections_this_round": [
        "board_image_before: 模板里的 f5c2fd89… 更正为最新一次逐字节回读证到的 dab356c6…/290916 B "
        "(proven_by=field-20261101T142722Z/readback.bin); 旧口径放进 supersedes_in_r6c_template; R6C 原件不回改",
        "executor.path: 本机绝对路径 D:/ns800/… 改仓内相对路径; bytes/sha256 不变; "
        "原串保留在 path_r6c_template_verbatim",
        "motion_budget: 赋值行尾逗号把对象静默写成单元素数组 => 派生器现在拒绝任何算成 tuple 的顶层键",
        "package/task_id/status: 还写着上一轮 'R5-C 续测包' => 改成本轮 "
        "phase8-m8-r7-field-package-5ae / R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED(执行器不读这三条)",
        "新增 stage_numbering_and_legacy_fields: 本包 S0–S4 与继承历史字段的旧全包 S0–S11 撞号"
        "(旧 S4=巡圈学习要起转, 本包 S4=tmc_config_apply 不起转), 逐块登记为历史口径"],

    "transcript_encoding_self_injury_and_fix": (
        "本轮最初用 shell 直接重定向归档证据, Windows 控制台默认 cp936 => 落盘是 GBK 字节, "
        "按 UTF-8 读即乱码, 也与 R6C 那批 UTF-8/LF 证据口径不一致。修法是新增 "
        "run_transcripts_r7.py / run_derive_matrix_r7.py: 子进程强制 PYTHONIOENCODING=utf-8, "
        "父进程严格解码(解不出来就失败, 不退回 gbk), 每步记自己的退出码, 整体 LF 写盘; "
        "当前三份文本证据都由这两个脚本重新生成, 乱码原件没有被当作证据留在仓库里。"),

    "commands_and_exit_codes": {
        "validate_plan": grep(R7 + "/host/validate_plan_r7.txt", ["PLAN_VALIDATE_SUMMARY"]),
        "validate_plan_only_fail": grep(R7 + "/host/validate_plan_r7.txt", ["|FAIL|"]),
        "readiness": grep(R7 + "/host/readiness_transcript.txt", ["R7_READINESS_SUMMARY"]),
        "derive_idempotency_last_line": grep(R7 + "/host/derive_matrix.txt", ["MATCH=True"]),
        "note": "以上数字是从入库原文 grep 出来的, 不是手抄; 退出码记在各原文的头部"},

    "registered_checker_gap": {
        "gate": "PLAN_MOTION_CAP_BUDGET",
        "why_red": "caps 只收 motion_cap_seconds 为真值的阶段(m8r3_serial_chain.py:2678-2679), "
                   "通过要求 caps 非空且 sum(caps)<=quota.motor_run_seconds_total(:2683); "
                   "本包不起转 => 只能谎报一个非零运动预算才能变绿",
        "what_was_not_done": "不为了让门变绿改判据, 也不谎报预算",
        "what_plays_that_role_instead": "host/r7_readiness.py 的 R7-2 三条独立判据(无起转步骤 / "
                                       "运动与学习额度全 0 / 会话禁令不被步骤命中)"},

    "artifacts_and_identities": {k: {"path": p, "bytes": nb(p), "sha256": sha(p)}
                                 for k, p in ARTIFACTS},
    "unchanged_this_round": {
        "executor": {"path": EXEC, "bytes": nb(EXEC), "sha256": sha(EXEC),
                     "note": "与 R6C 登记同一件; 本轮未改执行器, 也未重跑全量 127 项自检"},
        "selftest": {"path": SELFTEST, "bytes": nb(SELFTEST), "sha256": sha(SELFTEST)},
        "product_C_and_firmware": "本轮 0 改动(工作树除新目录外干净); keil_builds=0",
        "frozen_plans": "R5-C/R6/R6A/R6B/R6C 计划与其镜像登记保持原字节, 不回改"},

    "approval": {
        "hardware_actions_authorized_by_this_package": 0,
        "prior_token": OLD_TOKEN,
        "prior_token_state": "EXHAUSTED_AND_NOT_CARRIED(download/readback/explicit_reset 各 1/1 用尽; "
                             "换计划文件/换输出目录不重新发额度)",
        "numbers_are_proposals_not_authorization": True,
        "what_is_still_needed": "F1 之前的批准已由操作者那条原话给出并记在仓外持久账; 现在需要的是"
                                "A 对现场真实字节的复审。要继续动设备(断电/上电、fault_reset 人工恢复复核、"
                                "重跑到 READY、或用掉剩下的 1 次 port_open 与 1 次 apply)都需操作者另发一次批准; "
                                "本件不代替操作者回答现场安全门",
        "this_file_is_not_an_approval": True},

    "field_execution": field_exec_block(),
    "device_actions_note": (
        "两条时间线分开记账: (a) 候选包准备轮 = 全程 0 设备动作(下面的 keil_builds=0 也属于这一轮); "
        "(b) R7-F1 现场轮 = 操作者一次整包批准后真实执行, 到 S3 STOP。下面这组数字是 **F1 之后**的实耗, "
        "由仓外持久批准账与 dispatch 原文现算/摘录, 不是准备轮的 0。"),
    "device_actions": {"serial_ports_opened": 1, "pyocd_tool_invocations": 3,
                       "downloads": 1, "flashes": 1,
                       "readbacks": 1, "resets": 1, "motor_starts": 0, "motor_arms": 0,
                       "tmc_config_apply": 0, "config_save": 0, "blackbox_clear": 0,
                       "target_power_cycles": 0, "keil_builds": 0},
    "private_material_handling": "UID 一律以 @UID_FILE 占位入库(执行器自己脱敏成 "
                                 "@UID_FILE<redacted len=48 sha256_…>), 未把 48 位十六进制私有机密写进聊天或仓库; "
                                 "现场轮复用的是本机既有私有 UID 文件(与上一轮真实 dispatch 登记的那一枚同一件, "
                                 "也等于当前实际连着的探针), 未枚举新设备; 新批准身份 M8R7F1-… 的持久账在仓外",
    "not_verified_after_this_round": [
        "SG/ADC 真实加载、阈值生成与有效性、ACTIVE 覆盖、REL_IMPACT、保存恢复与故障恢复闭环",
        "正常 RUN→disarm 之后的 Safety 收口(本包不含运动, 且 A 纠正了我对 load_means 的口径)",
        "idle1024 带载最坏栈余量、现场精确线程交错、0x00bd80b0 写入者(旧裁定保持)",
        "启动是否到 READY —— 现场结果是 FAIL 到 FAULT_LATCHED/8, 不是未测; 但'修好了'也不成立",
        "[SAFETY] LIMIT 两条与 [TMC-CFG] CONFIGURED 三条短打印(步骤未到达)"],
    "safe_for_enable": False,
    "review_points_for_A": [
        "现场首错(state=FAULT_LATCHED/fault=8, 生产者=[SS] [REQ FAIL] TMC2209 IOIN read rc=-23)怎么归因: "
        "丢帧 / 总线未就绪 / 自检时序 三条机制本轮都没排除, 也没有据此改产品码",
        "启动被动流里 2 个 >120 B 分段与 3 个裸 NUL: 需要的是产品侧输出串流判据, 还是执行器把 "
        "NUL/UTF-8 门也盖到启动流上(本轮只登记, 两边都没动)",
        "校正版判据在真实回包上的表现: 白名单与连字符取值按预期放行, 停因落在真值不符 —— 请复核这条"
        "是否足以说明'误拒'已修, 而本场 STOP 是产品事实",
        "PLAN_MOTION_CAP_BUDGET 对无起转包的恒红门是否要在执行器侧正式修(本轮只登记不修)",
        "额度剩 1 次 port_open 与 1 次 apply: 下一场是续测还是先修 rc=-23, 由 A 与操作者定"],
    "remote_readback": readback_block(),
    "status_block": STATUSES,
}


def main():
    head_commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    doc["generated_while_head"] = head_commit
    doc["artifacts_and_identities_note"] = (
        "bytes/sha256 全部在生成时刻从磁盘现算; 本 JSON 不自记自己的哈希(清单不哈希自身是同一条纪律); "
        "远端逐 blob 回读在收口一步补进 remote_readback 字段")
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    print("R7_HANDOFF|%s|bytes=%d|sha=%s|keys=%d|remote_readback=%s" %
          (OUT.relative_to(REPO).as_posix(), nb("reports/agent_handoffs/phase8-m8-r7-field-package-5ae.json"),
           sha("reports/agent_handoffs/phase8-m8-r7-field-package-5ae.json")[:16], len(doc),
           doc["remote_readback"]))
    print("device_actions_offline_prep=0 | device_actions_F1=download 1 / readback 1 / reset 1 / "
          "port_open 1 | keil_builds=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
