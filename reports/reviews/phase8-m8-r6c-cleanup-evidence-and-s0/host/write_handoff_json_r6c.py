#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 M8-R6C 的审核交接回执 JSON(给云端 A / 操作者)。

纪律: 所有 bytes/sha256 都是**当场从磁盘算**的(抄不进任何自述值); 顶层不放本件自己的哈希
(一条清单不能给自己的字节作证); 远端回读结果在收口一步补进去, 不在这里预写。
"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent
REPO = ROUND.parents[2]
OUT = REPO / "reports/agent_handoffs/phase8-m8-r6c-cleanup-evidence-and-s0.json"
R6C = "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0"
R6B = "reports/reviews/phase8-m8-r6b-executor-binding"
R3F = "reports/reviews/phase8-m8-relative-protection-r3/field"

EXEC = R3F + "/m8r3_serial_chain.py"
SELFTEST = R3F + "/m8r3_field_selftest.py"
PLAN = R6C + "/field/plan_core_r6c.json"

BASE = "64353285e9b34df1c54ccea4011ac555c482e626"
SOFTWARE = "c0f361db5cc02fd087f6f3b404254151411d4a4a"


def sha(rel):
    return hashlib.sha256((REPO / rel).read_bytes()).hexdigest()


def nb(rel):
    return (REPO / rel).stat().st_size


STATUSES = [
    "R6B_evidence = ACCEPTED",
    "B1_original_pending_type_failure = CLOSED_OFFLINE",
    "B1_reserve_commit_exception_and_port_close = HOST_TARGETED_TESTS_PASSED",
    "cleanup_pre_send_evidence_failure_isolation = REWORK_DONE_OFFLINE(R6C)",
    "B2_core_execution_snapshot_binding = ACCEPTED_OFFLINE",
    "S0_execution_snapshot_identity = REWORK_DONE_OFFLINE(R6C)",
    "R3_cleanup_byte_integrity = ACCEPTED_OFFLINE(不等于真实串口已验)",
    "R4_new_error_print_width = ACCEPTED_OFFLINE(新镜像仍未上板)",
    "product_field_fix = NOT_VERIFIED",
    "overall_offline_release = REWORK(本轮两处补完待 A 复审)",
    "safe_for_enable = false",
    "new_hardware_authorization = 0",
    "device_actions_this_round = 0",
    "keil_builds_this_round = 0",
    "next_work = R6C 复审; 后续现场包本轮不制定",
]

doc = {
    "task": "M8-R6C-CLEANUP-EVIDENCE-AND-S0",
    "task_book": "NS800_M8_R6B_Review_REWORK_and_R6C_Task_20261103.md",
    "task_book_verdict_verbatim":
        "**整体仍为 REWORK，继续离线。R6B 已修复上一轮两个主要反例；当前剩余一个撤能阻断，"
        "以及一处 S0 快照身份记录缺口。下一轮仅处理这两处直接相关问题，不改产品 C、不重编。**",
    "date_local": "2026-11-03",
    "branch": "feature/m8-sg-adc-relative-protection",
    "remote": "https://github.com/LINboss666/ns800-stepper-safety",
    "base_commit": BASE,
    "base_commit_note": "任务书 §2 指定的 A 实际审核头; 开工时本地 HEAD 与之相同且工作树干净, 远端同头",
    "software_input_commit": SOFTWARE,
    "evidence_commit": "本 JSON 所在提交(在 software_input_commit 之后顺序追加)",
    "delivery_head_rule": "无 rebase/merge/amend/force; 不新建分支; 不碰冻结引用; "
                          "常规 push 后独立逐 blob 回读精确 SHA 即停止, 不为回读自己的新头循环追加提交",
    "roles": "云端 A 出任务书与独立复审; 本地 Qoder 执行; 操作者=人(本文件不含任何硬件批准, "
             "也不代替操作者回答现场安全门)",
    "status": "M8_R6C_OFFLINE_DELIVERED / AWAITING_INDEPENDENT_REVIEW",
    "scope_changed_this_round": [
        "EXEC " + EXEC,
        "SELFTEST " + SELFTEST,
        "r4 检查器 " + "reports/reviews/phase8-m8-idle-stack-r4/host/idle_learn_check.py"
        "(P8 执行器身份四层 -> 五层)",
        "提交前预检 " + "reports/reviews/phase8-m8-idle-stack-r4/host/staged_preflight_batch.py"
        "(新增本轮清单)",
        "新离线模板 " + PLAN + "(新路径; 旧轮派生件一个字节未动)",
        ".gitattributes(本轮目录 -text, 保 blob==磁盘字节)",
    ],
    "not_changed_this_round": [
        "产品 C / 头文件 / 参数 / 算法 / 阈值 / 引脚 / 恢复资格: 未动(motor.c 与 supervisor.c 的 "
        "blob-vs-disk 差异只是 CRLF 行尾, git diff 该文件=未变)",
        "固件构建: keil_builds=0; 三件镜像身份原样(5ae4110d…/292164、d70a9180…/292116、dab356c6…/290916)",
        "R5-C/R6/R6A/R6B 已作证据的计划、26 场现场原件与台账、具名冻结件: 未动",
    ],
    "before_after_same_input": {
        "C1_cleanup_bookkeeping_before_send": {
            "before": {
                "corrupt_log_failure": {"tx": ["motor_start"], "closed": True, "disarm": "UNKNOWN",
                                        "success": False,
                                        "frame_exception": "OSError('INJECTED: one cleanup "
                                                           "bookkeeping write failure')"},
                "exhausted_log_failure": {"tx": ["motor_start"], "closed": True,
                                          "disarm": "UNKNOWN", "success": False},
                "source": R6C + "/field/probes_at_6435328_before_fix.json",
            },
            "after": {
                "corrupt_log_failure": {
                    "tx": ["motor_start", "motor_stop", "motor_disarm", "motor_status"],
                    "closed": True, "disarm": "CONFIRMED_DISABLED", "success": False,
                    "disclosure": "LEDGER_EVENT_WRITTEN_AFTER_RETRY:CLEANUP_RESERVE_REFUSED_SENT_ANYWAY",
                    "frame_exception": "无"},
                "exhausted_log_failure": {
                    "tx": ["motor_start", "motor_stop", "motor_disarm", "motor_status"],
                    "closed": True, "success": False,
                    "disclosure": "同形(合法账 cleanup cap=0 也照发三条)"},
                "persistent_write_failure": {
                    "fs": "FS123", "hits": 6, "writes": 4, "closed": True, "success": False,
                    "disclosure": "LEDGER_EVENT_WRITE_FAILED:CLEANUP_RESERVE_REFUSED_SENT_ANYWAY"},
                "source": R6C + "/field/probes_after_fix.json",
            },
            "meta_negative": {
                "fs": "FS124",
                "what_it_disables": "把 _ev_or_defer 摘回直接 led.ev(...) 的旧形状",
                "must_recur": "端口实际 write 退回只有 motor_start + CLEANUP_FRAME_EXCEPTION",
                "measured_reappeared": True},
        },
        "C2_s0_execution_snapshot_identity": {
            "before": {"rc": 0, "memory_backend_calls": 1,
                       "approved_execution_sha_P": "23f0fbf8eca1b9a2b06dba822b97df3afaff880b35e8e2ac54a2f2193230a940",
                       "disk_after_swap_Q": "089514f4fc94dba348cdee73054fe9d52f7ccf0980e7b1f9b0fd98f0d20cc15a",
                       "s0_recorded_plan_sha": "089514f4fc94dba348cdee73054fe9d52f7ccf0980e7b1f9b0fd98f0d20cc15a",
                       "s0_records_execution_snapshot": False},
            "after": {"named_refusal": "S0_PLAN_DISK_DRIFT",
                      "refused_before": "任何动作之前(S0 阶段 STOP, 后续 download 未跑)",
                      "memory_backend_calls": 0, "download_quota_spent": 0,
                      "disk_value_now_recorded_as": "plan_sha256_on_disk(具名观察字段, 不覆盖执行身份)",
                      "positive_control": "FS125: 文件未变时 plan_sha256 == 快照 == 磁盘, 下载照跑"},
            "meta_negative": {"fs": "FS127",
                              "what_it_disables": "把 S0 的 snapshot 参数摘掉(回到另读一次磁盘)",
                              "must_recur": "S0 重新记 Q(089514f4…)而执行 P(23f0fbf8…)且下载跑一次",
                              "measured_reappeared": True},
            "scope_note": "这是证据身份缺口(实际动作仍来自已批准的 P), 不据此撤回已通过的 B2 核心快照绑定"
        },
        "comparator": {
            "script": R6C + "/host/r6c_before_after.py",
            "baseline_source": "git cat-file blob 6435328:<path> 取到仓库外副本 + .git gitfile 只读指向本仓对象库",
            "result": "RESULT BEFORE_AFTER_OK (checks=8 fails=0)",
            "output": R6C + "/host/r6c_before_after.txt",
        },
    },
    "new_negative_controls": [
        "FS122 坏账 + 披露日志写一次 OSError: 断言内存端口实际 write 列表四条齐全、closed、success=false",
        "FS123 合法完整账 cleanup cap=0 + 持续写失败: 三发 + WRITE_FAILED 披露 + 不成功",
        "FS124 反向-摘掉清理记账隔离: 必须退回只发 motor_start",
        "FS125 S0 记实际执行快照身份(正控)",
        "FS126 S0 磁盘漂移在执行前具名拒绝、后端零调用、额度未扣",
        "FS127 反向-摘掉 S0 快照贯通: 必须重现'记 Q 执行 P 且下载照跑'",
    ],
    "regression_scope": "只跑受影响项(任务书 §6.2 第4条): FS80–121+新增的 127 项一次全量段、"
                        "r6c/r6b/r6a 三个同输入对照器、r4 离线链 120 项、validate-plan、判宽两模式; "
                        "未跑历史数千条清单/S2 其余 19 组/固件链(产品 C 未动)",
    "commands_and_exit_codes": [
        {"cmd": "python %s/m8r3_field_selftest.py" % R3F, "rc": 0,
         "out": "cases=127 pass=127 fail=0 negative_injections=20 hardware_actions=0"},
        {"cmd": "python %s/host/r6c_before_after.py" % R6C, "rc": 0,
         "out": "RESULT BEFORE_AFTER_OK (checks=8 fails=0)"},
        {"cmd": "python %s/remaining_probes.py <仓库根>  # 修前(在 6435328 字节上)" % R6C, "rc": 0,
         "out": "采集完成; 附录 A 的坏结果如实入档, 不当成 PASS"},
        {"cmd": "python %s/m8r3_serial_chain.py --validate-plan --plan %s" % (EXEC, PLAN), "rc": 0,
         "out": "checks=232 fail=0 msh_commands=74"},
        {"cmd": "python %s/host/derive_plan_r6c.py 五步(--check/写/改坏后/force/--check)" % R6C,
         "rc": [0, 0, 1, 0, 0], "out": "改坏后 R6C_REFUSE_WRITE, 不静默覆盖"},
        {"cmd": "python reports/reviews/phase8-m8-idle-stack-r4/host/idle_learn_check.py", "rc": 0,
         "out": "CHECKS=120 problems=0; P8 五层执行器身份逐层对 blob 现算"},
        {"cmd": "python %s/host/r6b_before_after.py" % R6B, "rc": 0,
         "out": "RESULT BEFORE_AFTER_OK (checks=8 fails=0) —— R6B 两项修未回退"},
        {"cmd": "python reports/reviews/phase8-m8-r6a-offline-rework/host/r6a_before_after.py",
         "rc": 0, "out": "RESULT BEFORE_AFTER_OK (fails=0)"},
        {"cmd": "python reports/reviews/phase8-m8-r6-offline-recovery/host/line_width_check.py "
                "以及 --selfcheck", "rc": [0, 0],
         "out": "LINE_WIDTH_RESULT PASS (bad=0); CHANGE_SCAN_META fired 18 站点(本轮无新 C 站点)"},
        {"cmd": "python reports/reviews/phase8-m8-idle-stack-r4/host/staged_preflight_batch.py",
         "rc": 0, "out": "STAGED_PREFLIGHT_TOTAL problems=0(清单与暂存 blob 逐字节相符)"},
    ],
    "artifacts_and_identities": {
        "executor": {"path": EXEC, "bytes": nb(EXEC), "sha256": sha(EXEC)},
        "selftest": {"path": SELFTEST, "bytes": nb(SELFTEST), "sha256": sha(SELFTEST)},
        "offline_template": {"path": PLAN, "bytes": nb(PLAN), "sha256": sha(PLAN),
                             "package_status": "R6C_DERIVED_NOT_EXECUTED",
                             "hardware_actions_authorized_by_this_package": 0},
        "probes_before": {"path": R6C + "/field/probes_at_6435328_before_fix.json",
                          "bytes": nb(R6C + "/field/probes_at_6435328_before_fix.json"),
                          "sha256": sha(R6C + "/field/probes_at_6435328_before_fix.json")},
        "probes_after": {"path": R6C + "/field/probes_after_fix.json",
                         "bytes": nb(R6C + "/field/probes_after_fix.json"),
                         "sha256": sha(R6C + "/field/probes_after_fix.json")},
        "before_after_transcript": {"path": R6C + "/host/r6c_before_after.txt",
                                    "bytes": nb(R6C + "/host/r6c_before_after.txt"),
                                    "sha256": sha(R6C + "/host/r6c_before_after.txt")},
        "round_manifest": {"path": R6C + "/SHA256SUMS.txt",
                           "note": "清单不哈希自身字节; 逐条与 Git blob/远端 blob 的复算由预检与回读完成"},
    },
    "frozen_artifacts_kept": [
        "R5-C 计划 05bcf96a…、R6 计划 6e0b97f2…、R6A 计划 556a044e…、R6B 计划 08fd5d52…: 未动",
        "镜像: 5ae4110d…/292164(仍未上板)、d70a9180…/292116、dab356c6…/290916(最后已证在板)",
        "26 场现场原件与台账、r3 归档件: 未动(git status 为空)",
    ],
    "derived_manifests_regenerated_because_tools_changed": [
        "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0/SHA256SUMS.txt",
        "reports/reviews/phase8-m8-idle-stack-r4/SHA256SUMS.txt",
        "reports/reviews/phase8-m8-relative-protection-r3/SHA256SUMS.txt",
    ],
    "rerun_side_effects_on_previous_rounds": {
        "kept": True,
        "classes": ["仓外临时 run_dir 随机名(idlecheck_k3_0cara -> idlecheck_dvt_1d53, 逐字符核对差异只在这里)",
                    "登记的盘上执行器身份 84df1306…/215704 B -> 33d3f0af…/219491 B",
                    "P8 说明文字由四层链改成五层链"],
        "not_claimed": "不写'所有旧轮派生报告都没动'; 旧现场 raw/台账与具名冻结件未动, 复跑写到本轮目录",
    },
    "device_actions": {
        "total": 0,
        "serial_ports_opened": 0, "dap_connections": 0, "downloads": 0, "flashes": 0,
        "target_readbacks": 0, "resets": 0, "power_cycle_requests": 0, "learns": 0, "zeros": 0,
        "motor_starts_on_device": 0, "config_saves": 0, "blackbox_clears": 0, "keil_builds": 0,
        "note": "附录里的 motor_start 与 download 全部只到内存端口/内存动作后端"
    },
    "device_action_counter": "0",
    "private_material_handling": "未读取私有 48-hex UID(本轮零次真实下载); 计划与证据里只出现 @UID_FILE 占位; "
                                 "回执与仓库不写 UID 值",
    "new_ledger_created_for_field": False,
    "not_verified_after_this_round": [
        "product_field_fix(两条修只有主机/内存端口与内存后端证据)",
        "真实串口下的读块切分、告警刷屏时序与现场清理交错",
        "跨进程真实现场账本(仍未在现场用过一次)",
        "新镜像 5ae4110d… 未上板; dab356c6… 仍是最后已证在板件",
        "overall_offline_release 待 A 独立复审",
    ],
    "next_round_is_not_this_package": "后续现场包本轮不制定; 本包 hardware_actions_authorized=0, "
                                      "旧 learn/起转额度不结转; 四项装配前提仍 PROVIDED, load_means 仍 NOT_PROVIDED",
    "safe_for_enable": False,
    "go_requested": False,
    "review_points_for_A": [
        "C1 的隔离边界: 只隔离清理路径上发送前的记账写入, 其余 Ledger 错照旧上抛; "
        "是否认为 §4.2 的'不全局吞'满足?",
        "C2 选了'动作前具名拒绝'而非'只另记 disk 字段'; 若希望改成两者并存, 只需指明 S0 之后再观察磁盘",
        "FS123 里同一事件命中 6 次(三条命令各一次 + 末尾补记各一次)是否与 §4.4 的'一次/持续'口径一致",
        "本轮把 _b1_session 的实际 write 列表放进 B1_IO 注册表(返回值形状未变以便附录复用), "
        "是否接受这种不改附录脚本的装配方式",
    ],
    "known_text_defects_registered": [
        "任务书 §5 说 S0 位置'约3108/3123行、调用约3448行': 本次头上实际是 3152/3173/3179 与 3510; "
        "本轮按函数与语义定位, 未按行号盲改",
    ],
    "status_block": STATUSES,
}

body = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
OUT.write_text(body, encoding="utf-8", newline="\n")
b = OUT.read_bytes()
print("R6C_HANDOFF_JSON|%s|bytes=%d|sha=%s|keys=%d" %
      (OUT.relative_to(REPO).as_posix(), len(b), hashlib.sha256(b).hexdigest()[:16], len(doc)))
