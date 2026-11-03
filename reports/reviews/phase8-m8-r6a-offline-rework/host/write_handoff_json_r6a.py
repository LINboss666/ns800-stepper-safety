#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 R6A 的审核交接 JSON(**机器算出的字段一律当场从磁盘/blob 复算**, 不手抄)。

用法: python write_handoff_json_r6a.py
退出码 0 = 写成功且 JSON 可被 json.load 回读、LF、无 BOM。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ROUND = Path(__file__).resolve().parents[1]
FW = ROOT / "reports/firmware/phase8-m8-r6a-offline-rework"
PREV_FW = ROOT / "reports/firmware/phase8-m8-r6-offline-recovery"
R4_FW = ROOT / "reports/firmware/phase8-m8-idle-stack-r4"
OUT = ROOT / "reports/agent_handoffs/phase8-m8-r6a-offline-rework.json"
BASE = "44e493cea9bbbc35e18394ca23dd1a8502519448"
SOFTWARE = "9e2eb9cfe26688ae8ae57ece248fcf089433b7e1"
EXEC = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
PLAN = ROUND / "field/plan_core_r6a.json"
R6PLAN = ROOT / "reports/reviews/phase8-m8-r6-offline-recovery/field/plan_core_r6.json"
R5CPLAN = ROOT / "reports/reviews/phase8-m8-idle-stack-r4/field/plan_core_r5c.json"
LOG = ROUND / "build/rt-thread-motor-r6a.log"
STATUSES = ["R6_evidence = ACCEPTED", "disarm_normal_close = HOST_TARGETED_TESTS_PASSED (本轮复跑 10/10 + 八项反向)",
            "product_field_fix = NOT_VERIFIED",
            "R1_approval_ledger = REWORK_DONE_OFFLINE (复审 §3 四反例逐一被拒 + 8 条新用例)",
            "R2_execution_input_binding = REWORK_DONE_OFFLINE (真实单会话拒发; 5 条新用例 + 1 条元负控)",
            "R3_cleanup_byte_integrity = REWORK_DONE_OFFLINE (危险门集中到四条入口; 6 条新用例 + 1 条元负控)",
            "R4_new_error_print_width = REWORK_DONE_OFFLINE + 新镜像已构建归档(未上板)",
            "overall_offline_release = REWORK_DONE / AWAITING_INDEPENDENT_REVIEW",
            "safe_for_enable = false", "new_hardware_authorization = 0", "device_actions_this_round = 0"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def nb(p):
    return Path(p).stat().st_size


def blob_sha(commit, rel):
    r = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (commit, rel)],
                       cwd=str(ROOT), capture_output=True)
    if r.returncode != 0:
        return {"available": False}
    return {"available": True, "bytes": len(r.stdout), "sha256": hashlib.sha256(r.stdout).hexdigest()}


def img(p):
    return {"path": Path(p).relative_to(ROOT).as_posix(), "bytes": nb(p), "sha256": sha(p)}


doc = {
    "task": "M8-R6A-OFFLINE-REWORK",
    "task_book": "NS800_M8_R6_Review_REWORK_20261103.md (云端 A 对 R6 交付的独立复审 + 返修任务书)",
    "task_book_verdict_verbatim": ("整体 REWORK, 继续停在离线, 不进入现场轮。必修 R1(账本字段缺失和绑定后消失"
                                   "会恢复额度)/R2(实际执行步骤未绑定批准, 省略 quota 即可绕账)/R3(清理回包中的裸 "
                                   "NUL 仍可获得成功)/R4(本轮新增错误打印仍超过缓冲); §7 要求本轮设备动作全部 0。"),
    "date_local": "2026-11-03",
    "status": "M8_R6A_OFFLINE_DELIVERED / AWAITING_INDEPENDENT_REVIEW",
    "roles": {"executor": "本地 Qoder 执行 Agent (LMX)", "reviewer": "云端 A (独立复审)",
              "hardware_operator": "操作者本人(现场动作与最终决策); 本轮没有被请求任何设备动作",
              "safety_gate": "任何 enable/烧录/起转前的人工安全门只由操作者回答, 执行器不代答"},
    "branch": "feature/m8-sg-adc-relative-protection",
    "remote": "https://github.com/LINboss666/ns800-stepper-safety",
    "base_commit": BASE,
    "base_commit_note": "任务书 §1 指定的**实际拉取并审核的完整交付头**; 本轮从它起做, 未覆盖任何用户新修改",
    "software_input_commit": SOFTWARE,
    "evidence_commit": "本 JSON 所在提交(严格在 software_input_commit 之后顺序追加)",
    "delivery_head_rule": "无 rebase/merge/amend/force; 不新建分支; 不动冻结引用; .gitignore 未改, "
                          ".gitattributes 只追加本轮两行 -text(r6a 轮目录与固件目录)",
    "forbidden_git_ops_performed": [],
    "frozen_refs_untouched": {"main": True, "tags": True, "other_phase8_branches": True},
    "gitattributes_change": "追加 reports/firmware/phase8-m8-r6a-offline-rework/** 与 "
                            "reports/reviews/phase8-m8-r6a-offline-rework/** 一律 -text(与 R6 同一条理由: "
                            "构建日志是 CRLF 原件, 派生件与对照文本是 LF 原件, 任一边被转换都会让远端逐 blob "
                            "回读报假 hash_mismatch)",
    "device_actions": [],
    "device_action_counter": {"ports_opened": 0, "dap_connections": 0, "downloads": 0, "flashings": 0,
                              "readbacks": 0, "resets": 0, "power_cycle_requests": 0, "learns": 0,
                              "zeros": 0, "motor_starts": 0, "config_saves": 0},
    "private_material_handling": "未读 UID; 未打印任何 48-hex 私有 UID; 探针与自检只在 %TEMP% 下的一次性目录里跑",
    "scope_changed_this_round": {
        "product_c": ["applications/motor.c — mot_commit_gated() 的 withheld 打印(R6 新增站点)拆成 4 次调用: "
                      "字面 211 B/最坏 237 B(保守口径 285 B)=> 最长一条 101 B; 字段与『未授予恢复资格』含义保留"],
        "executor": ["EXEC ApprovalLedger: _load_or_create -> _load(运行期不建账) + validate_ledger_doc(严格) "
                     "+ VERSION 1->2 + approval_init 独占创建(O_CREAT|O_EXCL + 同一把锁) + reserve 的 n 正整数 "
                     "+ commit 传播存盘失败 + open_approval 对 real/stub 同一条 ABSENT 门"],
        "executor_2": ["EXEC: verify_approved_binding(计划字节 + 步骤字节两步核对)、exec_session 的 real 门、"
                       "QUOTA_COMMAND_KEYS 与发送前交叉判定、spend 的 QUOTA_N_INVALID、CLI 单会话 real 拒接、"
                       "validate_plan 的 PLAN_QUOTA_MAP、derive_quota_needs 按映射表算需求"],
        "executor_3": ["EXEC: guard_partial_bytes/guard_stream_bytes 集中门, split_rows 入口(剥离前判)、"
                       "_cleanup_read 一条损坏只记一次证据错误且继续撤能、观察窗字节损坏改为硬停、"
                       "read_until_prompt 危险/NUL 门移到长度帽之前、command_attempt 的 pre 残留先存证再判门"],
        "host_tools": ["r6/host/line_width_check.py: FIXED 补本轮 motor.c 站点 + KNOWN_TOO_LONG 第三条 + "
                       "CHANGED_SITES(从 git diff 新增行取站点逐个量宽, 空扫描即判失败)",
                       "r4/host/idle_learn_check.py: P8 改成 r5c/r6 双冻结↔blob + r6a 对盘上字节",
                       "r4/host/staged_preflight_batch.py: MANIFESTS 加入 r6a 两份清单",
                       "新: r6a/host/derive_plan_r6a.py, r6a/host/archive_build_r6a.py, "
                       "r6a/host/r6a_before_after.py, r6a/host/write_handoff_json_r6a.py"],
        "selftest": "FS93-FS114 共 22 条新用例(账本 8 + 绑定/额度 8 + 字节完整性 6), 总数 92 -> 114; "
                    "夹具侧把工装步骤表按同一张映射表补上它本该有的额度申报(stamp), 并给 real 工装会话"
                    "造一份离线批准件(fx_approved) —— 判读路径没有为测试放宽",
        "not_changed": "算法、阈值、ACTIVE 覆盖、REL_IMPACT、load_means、栈配置、引脚、锁存/恢复资格、"
                       "plan.json/plan_core_r5c.json/plan_core_r6.json 与 26 场原件/台账一律未动"},
    "counterexamples_before_after": {
        "source": "reports/reviews/phase8-m8-r6a-offline-rework/host/r6a_before_after.txt (rc=0)",
        "method": "同一探针函数分别跑基线 44e493c 的执行器 blob(取到仓库外一次性副本)与盘上修后执行器; "
                  "账本全在 %TEMP% 一次性目录, 端口全为内存假口",
        "R1_missing_identity_sha": {"before": "snapshot 正常返回 spent={'apply': 1}",
                                    "after": "snapshot 与 reserve 都 APPROVAL_LEDGER_CORRUPT, 缺字段不被补回"},
        "R1_missing_spent_field": {"before": "再预占成功 tag=apply#1(结构丢失被当成从未消耗)",
                                   "after": "APPROVAL_LEDGER_CORRUPT(spent_keys!=caps_keys), 文件未被补写"},
        "R1_negative_spent_count": {"before": "再预占成功且账被改写成 0, 凭据 tag=apply#0",
                                    "after": "APPROVAL_LEDGER_CORRUPT(spent_not_count), 文件保持 -1"},
        "R1_ledger_deleted_after_binding": {"before": "自动建立新满额账, 再预占成功 spent={'apply': 1}",
                                            "after": "APPROVAL_LEDGER_ABSENT, 文件未被重建"},
        "R1_positive_full_ledger_exhausted": {"before": "APPROVAL_QUOTA_EXHAUSTED",
                                              "after": "APPROVAL_QUOTA_EXHAUSTED(正控未退化)"},
        "R2_single_session_real_unbound": {"before": "TX=['tmc_config_apply CONFIRM'], 开口 1 次, "
                                                      "quota_used={'port_open': 1}",
                                           "after": "REAL_SESSION_REQUIRES_APPROVED_PACKAGE, 开口 0, TX 空"},
        "R2_cli_single_session": {"before": "rc=4 APPROVAL_LEDGER_ABSENT(但输出目录已创建)",
                                  "after": "rc=4 REAL_TRANSPORT_REFUSED:single_session_steps_not_approved_package, "
                                           "run_dir_created=False"},
        "R3_cleanup_status_bare_nul": {"before": "disarm=CONFIRMED_DISABLED, cleanup_errors=[](把含裸 NUL 的"
                                                  "回包当成禁能证明)",
                                       "after": "disarm=UNKNOWN + motor_status:NUL_BYTE_IN_RESPONSE + "
                                                "success=False, 证据文件里 NUL 仍是 1 个",
                                       "note": "该探针故意不给业务 motor_start 回包(与 §5 的判定无关), 所以两边 "
                                               "status 都是 STOP; success 维度由 FS109(修后 False)与 FS114"
                                               "(摘门后 True)成对覆盖"},
        "R4_new_error_print": {"before_worst_bytes": 285, "before_bytes_claimed_by_reviewer": 237,
                               "before_literal_bytes": 211, "after_max_call_bytes": 101,
                               "after_calls_in_that_branch": 4, "limit_bytes": 120,
                               "gauge": "host/line_width_check.py 的保守口径(未登记的 %s 按 18 B 展开)"}},
    "new_ledger_format_and_version_gate": {
        "version": 2,
        "historical_v1_handling": "APPROVAL_LEDGER_VERSION 具名拒绝, 不自动迁移成未消费状态(FS100)",
        "init_preseeds_spent_zero_for_every_cap_key": True,
        "read_path_strict_on_every_lock": True,
        "reserve_n_must_be_positive_int": True,
        "commit_propagates_store_failure": True,
        "init_is_exclusive": "同一把 ledger_lock + O_CREAT|O_EXCL; 双 init 只有一个赢(FS98)"},
    "new_stop_codes_this_round": ["APPROVAL_LEDGER_VERSION(语义收紧)", "APPROVAL_IDENTITIES_INCOMPLETE",
                                  "APPROVAL_CAPS_INVALID", "APPROVAL_RESERVE_N_INVALID",
                                  "APPROVAL_RESERVE_NAME_INVALID", "APPROVAL_RESERVE_KIND_INVALID",
                                  "REAL_SESSION_REQUIRES_APPROVED_PACKAGE", "REAL_SESSION_REQUIRES_PLAN",
                                  "APPROVED_PACKAGE_INCOMPLETE", "APPROVED_PLAN_UNREADABLE",
                                  "PLAN_BYTES_CHANGED_AFTER_APPROVAL", "APPROVED_STAGE_NOT_FOUND",
                                  "STEPS_NOT_BOUND_TO_APPROVAL", "REAL_DISPATCH_REQUIRES_PLAN_FILE",
                                  "QUOTA_STEP_UNDECLARED", "QUOTA_KEY_MISMATCH", "QUOTA_N_MISMATCH",
                                  "QUOTA_N_INVALID", "NUL_BYTE_IN_RESPONSE(新增覆盖清理/观察窗/排空)",
                                  "REAL_TRANSPORT_REFUSED:single_session_steps_not_approved_package(CLI)"],
    "commands_and_exit_codes": [
        {"cmd": "ONLY=disarm_ready bash tests/host/run_stop_priority_s2.sh", "rc": 0,
         "result": "host_ovr 必需 10/10 PASS; target 按声明 SKIP=1; 本机 MinGW -mthreads 可用"},
        {"cmd": "ONLY=disarm_ready REVERSE=1 bash tests/host/run_stop_priority_s2.sh", "rc": 0,
         "result": "八项注入均命中预登记业务失败身份"},
        {"cmd": "python reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_field_selftest.py",
         "rc": 0, "result": "cases=114 pass=114 fail=0 negative_injections=17 hardware_actions=0 "
                            "transport=stub_only port=IN_MEMORY"},
        {"cmd": "python reports/reviews/phase8-m8-r6-offline-recovery/host/line_width_check.py",
         "rc": 0, "result": "LINE_WIDTH_RESULT PASS(bad=0); CHANGED_SITES 命中 18 站点最长 101 B; "
                            "三条 known-too-long 全部 fired"},
        {"cmd": "python …/line_width_check.py --selfcheck", "rc": 0, "result": "同上且判红靶子全部 fired"},
        {"cmd": "python reports/reviews/phase8-m8-r6a-offline-rework/host/r6a_before_after.py", "rc": 0,
         "result": "RESULT BEFORE_AFTER_OK(fails=0) —— 六组反例修前放行/修后拒绝"},
        {"cmd": "python …/derive_plan_r6a.py", "rc": 0, "result": "R6A_WRITE bytes=169310 sha=556a044e195705d1…"},
        {"cmd": "python …/derive_plan_r6a.py(改过字节后不带 --force)", "rc": 1,
         "result": "R6A_REFUSE_WRITE(不静默覆盖派生件)"},
        {"cmd": "python …/derive_plan_r6a.py --force / --check", "rc": [0, 0],
         "result": "R6A_CHECK|IDEMPOTENT|源仍冻结=True"},
        {"cmd": "python …/m8r3_serial_chain.py --validate-plan --plan …/plan_core_r6a.json", "rc": 0,
         "result": "PLAN_VALIDATE_SUMMARY|checks=232|fail=0|msh_commands=74"},
        {"cmd": "python reports/reviews/phase8-m8-idle-stack-r4/host/idle_learn_check.py", "rc": 0,
         "result": "CHECKS=120 problems=0; 元负控 6/6 + R5 组 9/9 fired"},
        {"cmd": "python …/apply_field_window_fix_r5c.py --check", "rc": 0,
         "result": "executed_bytes_unchanged=True(05bcf96aa7a6bf89) canon_vs_executed=ORDER_ONLY_DELTA"},
        {"cmd": "python …/archive_build_r6a.py / --check", "rc": [0, 0],
         "result": "三件产物与 build 输出逐字节相同; delta Code=+24 RO=+24 RW=0 ZI=0"},
        {"cmd": "python …/staged_preflight_batch.py(带 --selfcheck 与不带: 同一条代码路径)",
         "rc": [0, 0],
         "result": "两种调用都打印 `SELFCHECK(corrupted first entry) problems=1`(注入错的登记值确实被命中, "
                   "比较器不恒真)并 `STAGED_PREFLIGHT_TOTAL problems=0`(全部登记件的暂存 blob 与清单逐字节相符); "
                   "退出码只由真实登记件决定, 注入的那条不计入 —— 且脚本不解析 --selfcheck 参数(argv 未读), "
                   "所以两次都是 rc=0。上一版交接件把它写成 rc=[1, 0] 是与实现不符的自述, 本轮更正见 "
                   "known_text_defects_registered"},
        {"cmd": "C:/Keil_v5/UV4/UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 "
                "-o …/build/rt-thread-motor-r6a.log", "rc": 0,
         "result": "0 Error(s), 0 Warning(s); Program Size: Code=205594 RO-data=85994 "
                   "RW-data=2188 ZI-data=85592"}],
    "keil_build": {"targets": ["rt-thread-motor-m1-candidate"], "each_target_once": True,
                   "return_code": 0, "error_warning_line": "0 Error(s), 0 Warning(s)",
                   "program_size": LOG.read_text(encoding="utf-8", errors="replace")
                   .split("Program Size:")[1].splitlines()[0].strip(),
                   "compiler": "ARMCLANG V6.24 (C:\\Keil_v5\\ARM\\ARMCLANG\\Bin)",
                   "log": img(LOG), "delta_vs_r6": {"Code": 24, "RO-data": 24, "RW-data": 0, "ZI": 0},
                   "other_targets_rebuilt": False},
    "build_inputs": json.loads((FW / "build_manifest.json").read_text(encoding="utf-8"))["source_inputs"],
    "candidate_image": img(FW / "rt-thread-motor-m1-candidate.bin"),
    "download_payload": img(FW / "rt-thread-motor-m1.axf"),
    "map": img(FW / "rt-thread-motor-m1.map"),
    "new_image_on_board": False,
    "not_overwritten": {"r6_candidate": img(PREV_FW / "rt-thread-motor-m1-candidate.bin"),
                        "in_board_image_proven_byte_for_byte": img(R4_FW / "rt-thread-motor-m1-candidate.bin"),
                        "statement": "本轮新件写新目录; R6 的 d70a9180… 与板上证过的 dab356c6… 原字节未动"},
    "plan_package": {"path": PLAN.relative_to(ROOT).as_posix(), "bytes": nb(PLAN), "sha256": sha(PLAN),
                     "package_status": "R6A_DERIVED_NOT_EXECUTED",
                     "hardware_actions_authorized": 0,
                     "executor_registration": {"sha256": sha(EXEC), "bytes": nb(EXEC)},
                     "baseline_executor_at_44e493c": blob_sha(BASE, EXEC.relative_to(ROOT).as_posix()),
                     "candidate_image_binding_state": "NOT_BOUND_IN_THIS_PACKAGE",
                     "image_binding_still_points_at": sha(R4_FW / "rt-thread-motor-m1-candidate.bin"),
                     "offline_gate": "checks=232 fail=0(新增 PLAN_QUOTA_MAP 接管原 PLAN_QUOTA 步骤行, 总数不变)"},
    "frozen_artifacts_kept": {
        "plan_core_r5c.json": {"sha256": sha(R5CPLAN), "bytes": nb(R5CPLAN),
                               "note": "现场 26 场实际用过的执行输入; 写保护 R5C_REFUSE_WRITE 仍生效"},
        "plan_core_r6.json": {"sha256": sha(R6PLAN), "bytes": nb(R6PLAN),
                              "note": "R6 的离线派生件, 已被上一轮 CLI 证据用过 => 本轮不回改身份, "
                                      "另写 plan_core_r6a.json"},
        "r5c_field_originals_and_ledgers": "reports/reviews/phase8-m8-idle-stack-r4/field/field-20261101T*/ 未动",
        "r3_round_manifest": "reports/reviews/phase8-m8-relative-protection-r3/SHA256SUMS.txt 重算后逐行 diff "
                             "只有两条脚本条目移动(5137 条里 2 条), 26 场原件条目未变"},
    "derived_manifests_regenerated_because_tools_changed": [
        "reports/reviews/phase8-m8-idle-stack-r4/SHA256SUMS.txt(entries=394)",
        "reports/reviews/phase8-m8-r6-offline-recovery/SHA256SUMS.txt(entries=15)",
        "reports/reviews/phase8-m8-relative-protection-r3/SHA256SUMS.txt(entries=5137)",
        "reports/reviews/phase8-m8-r6a-offline-rework/SHA256SUMS.txt(entries=9)",
        "reports/firmware/phase8-m8-r6a-offline-rework/SHA256SUMS.txt(entries=4)"],
    "self_defects_found_and_fixed_this_round": [
        "工装夹具在 R2 新门下需要真实申报(初跑 FS06/FS51/FS67/FS88/FS102 失败): 按**同一张映射表**补声明并给 "
        "real 工装会话造离线批准件, 没有放宽判读路径",
        "元负控突变体原本会撞上新的严格校验(FS92 的失败原因会变成'账本损坏'), 改成同时绕开 _load/_store, "
        "让失败原因回到'cap 没守住'这一件事",
        "FS99 第一版把 _store 打补丁后在中途抛异常导致补丁泄漏到后续用例 => 加 try/finally 复原",
        "FS102 复用同一个 stderr 缓冲区导致真实半段的拒绝文本混进 stub 半段的断言 => 两侧各用独立缓冲区",
        "FS109 清理路径原本会把同一条 NUL 记两次(入口门 + split_rows) => 命中入口门后不再重复判读"],
    "known_text_defects_registered": [
        "上一轮 a8d9033 提交信息与交接 JSON 里 APPROVAL_LEDGER_ROOT_UNSABLE 拼写有误, 正确名 "
        "APPROVAL_LEDGER_ROOT_UNUSABLE(§7 允许随实质修复登记, 不回改历史)",
        "本轮软件提交 9e2eb9c 正文 R3 段有一处括号排版错(文字, 不影响判据或字节)",
        "r6a_before_after.txt 入库前做 CRLF->LF 归一(与上一轮 CLI 实证同一处理), 内容未改",
        "本交接件在证据提交 b78d9e1 那一版把 staged 预检写成 `rc=[1, 0]`(自称 --selfcheck 那次退 1); "
        "读了实现后不成立: 注入错的比对在两种调用里都会跑, 其 problems 只打印不进入退出码, 且脚本不读 "
        "argv 里的 --selfcheck => 实测两次都是 rc=0 且 SELFCHECK 行 problems=1 fired。本轮如实改成本次实测, "
        "判据行为未改(不让检查器退 1 反而说明它把'检出'与'登记件真不符'分开了)"],
    "rerun_side_effect_and_repair": {
        "what": "复跑 r4 离线链 idle_learn_check.py 会把 r4 那轮**已提交**的 8 件产物重写: "
                "6 件 field/dispatch_stub_*.txt + host/idle_learn_console.txt 只是仓外临时 run_dir "
                "随机名变化(idlecheck_vanplaae->idlecheck_ecndg11q), 而 host/idle_learn_check.json "
                "里的'本轮改动=15 项'被改成'10 项' —— 那个数是检查器按当时工作树现数的, 不是 r4 的事实",
        "action": "本轮把这 8 件 `git checkout --` 还原到提交字节(还原前工作树==HEAD, 所以还原即复跑到"
                "之前的状态); 上一轮证据不改写",
        "why_registered": "防复发: 该检查器的输出目录写的是上一轮目录, 复跑即污染; 下一轮复跑前要先看"
                        "这 8 件的状态位, 或把本轮输出写到本轮目录"
    },
    "regression_scope": "只复跑受影响项: disarm_ready(正+反)、r3 全量自检、r4 离线链 120 项、"
                        "r5c 计划校验器 --check、宽度检查器、构建归档、派生计划与离线门、"
                        "staged 预检。S2 其余 19 组与历史 5943 条清单未重跑(§7 不要求)。",
    "not_verified_after_this_round": {
        "product_field_fix": "修后的 disarm 收口/字节门/额度门都只有主机与内存端口证据",
        "new_image_on_board": "5ae4110d… 未烧录、未回读、未复位",
        "approval_ledger": "跨进程真实现场账本(%TEMP% 之外的持久位置)仍未在现场用过一次",
        "overall_offline_release": "待 A 复审",
        "preserved_old_findings": STATUSES},
    "remote_readback": {
        "push": "origin/feature/m8-sg-adc-relative-protection 由 44e493c 常规 fast-forward 到 b78d9e1"
                "(两次提交: 9e2eb9c 软件输入 + b78d9e1 证据); 无 rebase/merge/amend/force",
        "generic_script_rc": 0,
        "generic_summary": "remote_files=10834 sums_manifests=20 json_manifests=41 "
                           "declared=6670 verified=6603 | SCOPE_R3 entries=5149 verified=5149 "
                           "problems=0 | SCOPE_PRIOR problems=72(declared_but_absent=53, "
                           "suffix_ambiguous=8, hash_mismatch=6, duplicate_conflict=2, "
                           "cross_manifest_sha_conflict=3)",
        "round_scoped_script": "reports/reviews/phase8-m8-r6a-offline-rework/host/"
                               "remote_readback_r6a.py",
        "round_scoped_summary": "tip=b78d9e11f427 checks=44 fails=0 not_checked=0",
        "why_a_second_script": "通用回读的 JSON 名单只有 manifest/plan.json/needles.json, 不逐条核 "
                               "plan_core_r6a.json 与交接 JSON 里申报的执行器/镜像/清单身份; "
                               "定向件只从 git fetch + cat-file 取字节, 把这两件内部申报的三元组全部"
                               "重算回远端 blob",
        "transcripts": ["reports/reviews/phase8-m8-r6a-offline-rework/"
                        "remote_readback_generic_at_b78d9e1.txt",
                        "reports/reviews/phase8-m8-r6a-offline-rework/"
                        "remote_readback_at_b78d9e1.txt"],
        "prior_scope_residual_explained": [
            "r6 manifest ↔ motor.c: 那是 R6 构建当时的 blob(3b741233…), R6A 改了 motor.c 后 HEAD 变成 "
            "7305f3b4… => 历史件登记值与 HEAD 不同, 不是本轮缺陷; 冻结件不回改",
            "r6a ↔ r6 的 cross_manifest_sha_conflict(motor.c)是同一条的另一面: 本轮 manifest 自己申报的 "
            "7305f3b4… == 远端 blob(定向件已核)",
            "r6 / r6a ↔ r4 的 cross_manifest_sha_conflict(rtconfig.h)+ r4 的 hash_mismatch 是上一轮已披露的 "
            "『R4 manifest 登记磁盘字节 1927ed1c… 而 blob 是 b66753d6…』; R6 起改登记 blob, 本轮两条都等于远端",
            "其余 declared_but_absent / suffix_ambiguous / duplicate_conflict 与 m7/m5 的三条 hash_mismatch "
            "都在历史轮次, 数量与上一轮同口径(未新增)"],
        "frozen_checked_on_remote": "R5-C 计划 05bcf96a…/164851 B、R6 计划 6e0b97f2…/165522 B、"
                                    "R6 候选 bin d70a9180…/292116 B、在板 bin dab356c6…/290916 B "
                                    "在远端 tip 上逐条等于登记值",
        "baseline_executor_checked_on_remote": "1b23719a3c9a…/184093 B 在 44e493c(按 SHA 定点 fetch 后现算), "
                                               "tip 上的执行器 99d6c960…/209786 B == 候选包登记值",
        "device_actions": 0,
        "reads_from": "remote_git_protocol_only"},
    "next_round_is_not_this_package": "现场包要另起: 匹配新镜像 bin/axf/回读长度/下载阶段与具体额度, "
                                      "并按新代码重绑批准; 本包没有做这件事, 也不提供可用于现场的批准",
    "safe_for_enable": False,
    "go_requested": False,
    "review_points_for_A": [
        "R1 的严格性有没有把**合法**账本一起挡掉(caps 精确相符 + spent 键集合==caps 在真实派生计划上 "
        "是 232/0 fail; 但现场包若给 quota 增删动作键, 开账与执行必须是同一份 caps)",
        "R2 的最小修法把真实单会话彻底关掉了: 如果下一轮需要'一次批准的现场补测', 需要 A 明确要不要为它"
        "另设一份批准的整包子集, 而不是恢复任意 --steps",
        "R3 的清理门在 stub 工装与真串口两条路上共用同一函数; 现场中文行被 512B 读块剪开的情况只在完整字节流上判解码, "
        "请复核这一条与实际板侧换行/告警刷屏的先后",
        "R4 用保守口径(未登记 %s 按 18 B)判宽, 因此 motor.c 三条含 %s 的行仍留有余量; 若认为应按实参 YES/no 计, "
        "请告知, 现口径不改"]
}
body = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
OUT.write_bytes(body.encode("utf-8"))
back = json.loads(OUT.read_bytes().decode("utf-8"))
assert back["candidate_image"]["sha256"] == sha(FW / "rt-thread-motor-m1-candidate.bin")
print("R6A_HANDOFF_JSON|%s|bytes=%d|sha=%s|keys=%d" % (OUT.relative_to(ROOT).as_posix(), nb(OUT),
                                                       sha(OUT)[:16], len(back)))
sys.exit(0)
