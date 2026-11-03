#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 M8-R7 的审核交接回执 JSON(给云端 A / 操作者)。

纪律(与前几轮同一条):
  * 所有 bytes/sha256 都是**当场从磁盘算**的, 抄不进任何自述值;
  * 顶层不放本件自己的哈希(一条清单不能给自己的字节作证);
  * 门与自检的数字**从入库原文里 grep 出来**, 不手抄; 原文不存在就把 None 交出去, 如实暴露缺口;
  * 远端逐 blob 回读结果在收口一步补进来(回读原文先入库), 不在这里预写。
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
    "R7_package = R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED",
    "R7_scope_boot_validation = PREPARED_OFFLINE(未现场执行)",
    "R7_scope_short_prints = PREPARED_OFFLINE(判据来自产品源码字面量)",
    "R7_scope_new_image_identity = PREPARED_OFFLINE(下载+定长回读闭环已绑定)",
    "disarm_closeout = OUT_OF_SCOPE(需 motor_arm; load_means=NOT_PROVIDED)",
    "load_means = NOT_PROVIDED",
    "motion = NOT_INCLUDED(整包 0 起转步骤, 运动类额度全 0)",
    "validate_plan = 47 项 / fail=1(唯一 FAIL 已登记为恒红门)",
    "readiness = 17/17 OK(含整包 stub 干跑, 0 设备)",
    "R6A_candidate_image(5ae) = ARCHIVED_NOT_ON_BOARD(本包正是要把它上板)",
    "R6/d70 与在板 dab356c6 归档 = 原件未动",
    "executor/selftest = 字节与 R6C 相同(本轮未改执行器)",
    "product_C_and_firmware_archive = 本轮 0 改动",
    "product_field_fix = NOT_VERIFIED",
    "overall_offline_release = 待 A 独立复审(本轮不自签)",
    "safe_for_enable = false",
    "new_hardware_authorization = 0",
    "device_actions_this_round = 0",
    "keil_builds = 0",
    "next_work = 交 A 复审; 现场执行另需操作者一次明确批准",
]

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
    "status": "M8_R7_OFFLINE_CANDIDATE_DELIVERED / AWAITING_INDEPENDENT_REVIEW",

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
        "what_is_still_needed": "操作者对 5 个非零额度逐条批准(port_open 2 / download 1 / readback 1 / "
                                "explicit_reset 1 / tmc_config_apply 1, 尤其 apply 会写驱动芯片寄存器), "
                                "并回报现场物理状态(供电、接线、是否允许开 COM5、机械是否静止)",
        "this_file_is_not_an_approval": True},

    "device_actions": {"serial_ports_opened": 0, "dap_connections": 0, "downloads": 0, "flashes": 0,
                       "readbacks": 0, "resets": 0, "motor_starts": 0, "target_power_cycles": 0,
                       "keil_builds": 0},
    "private_material_handling": "UID 一律以 @UID_FILE 占位, 未把 48 位十六进制私有机密写进聊天或仓库; "
                                 "本轮没有产生任何台账/批准令牌",
    "not_verified_after_this_round": [
        "SG/ADC 真实加载、阈值生成与有效性、ACTIVE 覆盖、REL_IMPACT、保存恢复与故障恢复闭环",
        "正常 disarm 之后的 Safety 收口(需 motor_arm)",
        "idle1024 带载最坏栈余量、现场精确线程交错、0x00bd80b0 写入者(旧裁定保持)",
        "新镜像 5ae 在板(本包的目的就是去证它, 本轮未上板)"],
    "safe_for_enable": False,
    "review_points_for_A": [
        "本包是否可以在 load_means=NOT_PROVIDED 下只跑这三条覆盖, 还是应先补加载手段",
        "tmc_config_apply 单列一次额度是否必要(不 apply 就取不到 5ae 拆短的 CONFIGURED 三条短行)",
        "PLAN_MOTION_CAP_BUDGET 对无起转包的恒红门是否要在执行器侧正式修(本轮只登记不修)",
        "四处登记更正是否都只落在新包、没有回改任何已签收件"],
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
    print("device_actions=0|keil_builds=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
