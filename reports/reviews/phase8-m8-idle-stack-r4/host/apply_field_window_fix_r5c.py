#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R5-C 续测包(离线生成, 零设备): 从**已执行的** R4 包派生一份只用剩余额度的续测计划。

动因(全部是可回指的实测事实, 不是推测):
  现场轮 field-20261101T142722Z 在 S3 具名停止 `MISSING_RESPONSE_PROMPT as=sysboot bytes=0`。
  该轮已经证到: 唯一一次下载 rc=0、唯一一次 290916 B 定长回读与绑定镜像**逐字节相同**(新镜像在板),
  唯一一次显式复位 rc=0, 复位后的启动流真字节 1027 B —— **断在 `[FLASH] scanning 1 MiB event log...`**。
  之后 9.016 s(申报 6 s + SLACK 3 s)一个字节都没收到。整段观测从复位完成起只有约 11.0 s, 因为
  `drain_post_reset_boot()` 有一条**未申报**的静默早退(≥min(2.0,window) 且静默 0.2 s 即 break),
  把计划申报的 `boot_drain_seconds: 20` 实截成 2.0 s。对照: 同板同日的 cont 轮用 `boot_window_s: 75`
  拿到 3439 B 完整启动到 READY 并答满四条查询; stage-5 的 1 MiB 扫描按配置值估 ≥8.5 s
  (reports/reviews/phase8-m8-relative-protection-r3/host/m8diag_source_audit.txt §3, 该文自陈不是界限)。
  => 观测窗比启动本身短, 是这个包的真实缺陷; 它把"没观察到"变成了"停止", 不能算板子的结论。

本脚本只做三件事, 不改产品 C、不重编、不动已执行包的字节:
  ① 额度按**剩余**重算: download/readback/explicit_reset = 0(一次性批准已烧掉), 因此删掉 S1/S2 两个
     设备阶段和 S3 的复位步; 续测从"开一次端口、直接问第一条查询"开始。
  ② 观测窗放宽并自证: S3 首条查询 `system_status` 的 `seconds 6 -> 75`(执行器到提示符就返回, 健康板
     不会白等), 并标 `first_query_is_boot_probe`; 阶段总时限 90 -> 300 s(无运动阶段, 只是失控保护)。
  ③ 把新执行器身份**只**绑进续测包: 已执行的 R4 包继续登记它当时真正用过的 `40517501b3be…`/148267 B。

幂等: 每次都从 `plan_core_r4.json` 当前字节重新派生(散列、额度、序列), 再跑一遍得到同一份字节。
用法: python apply_field_window_fix_r5c.py [--check]
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                  # .../phase8-m8-idle-stack-r4/host
ROUND = HERE.parent
ROOT = HERE.parents[3]                                  # 仓库根
FIELD = ROUND / "field"
SRC = FIELD / "plan_core_r4.json"
OUT = FIELD / "plan_core_r5c.json"
CHAIN = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
ROWS = FIELD / "rows.json"

RUN_DIR = "reports/reviews/phase8-m8-idle-stack-r4/field/field-20261101T142722Z"
APPROVED_TIP = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"
R4_EXECUTOR_SHA = "40517501b3bedde928b338d56e4e9d89d1b1e91a32405f2b1ae20f1dd473ed40"
READBACK_SHA = "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439"
BOOT_SHA = "1947fbaca20a1f048ed7525c8961d559ae02256e48e1866e9f288e36df36bf95"
CONT_REF = "reports/reviews/phase8-m8-diag-stack-r1/field/cont-20261101T090512Z/RESULT.json"
AUDIT_REF = "reports/reviews/phase8-m8-relative-protection-r3/host/m8diag_source_audit.txt"

BOOT_MIN = 75           # cont 轮实测拿满整段启动所用的窗(RESULT.json 的 boot_window_s)
FIRST_QUERY_SECONDS = 75
S3_TOTAL_DEADLINE = 300
# 现场轮实测: system_status 的响应里带开机默认安全态 `[Motor] arm gate fail mask=0x18 (arm REFUSED)`,
# 那是"此刻还没 apply/确认极性, 所以 arm 会被拒"的状态披露, 不是我们发的命令被拒。业务失败扫描按
# REFUSED 关键字把它盖成 BUSINESS_FAILED => 状态查询本身通不过。白名单只在这一行**同时被
# expect_contains 正面要求**时才生效(执行器 PLAN_STATUS_ALLOW 门), 掩码变成别的值就照原样停。
ARM_REFUSED_REST = r"^\[Motor\] arm gate fail mask=0x[0-9A-Fa-f]+ \(arm REFUSED\)$"
ARM_REFUSED_TEXT = "[Motor] arm gate fail mask="


def allow_rest_state(step):
    step.setdefault("expect_contains", [])
    if ARM_REFUSED_TEXT not in step["expect_contains"]:
        step["expect_contains"] = step["expect_contains"] + [ARM_REFUSED_TEXT]
    step["status_lines_allow"] = [ARM_REFUSED_REST]
    step["status_lines_allow_why"] = (
        "开机/重启后的默认安全态: 极性与配置未确认前 arm 必须被拒(mask=0x18), 这一行是**状态披露**而不是"
        "本条命令被拒。它同时被 expect_contains 正面要求 => 掩码或文案变了就停止, 不是无限放行")
    return step


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def nbytes(p):
    return len(Path(p).read_bytes())


def load_chain():
    spec = importlib.util.spec_from_file_location("chain_for_r5c", str(CHAIN))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fix_stages(plan, by_id):
    # ① 删掉两个已烧掉一次性批准的设备阶段(下载/回读), 以及 S3 的复位步(显式复位 1/1 已用尽)。
    plan["stages"] = [s for s in plan["stages"] if s["id"] not in ("S1", "S2")]
    s3 = by_id["S3"]
    steps = s3["steps"]
    rst = next(i for i, x in enumerate(steps) if x.get("action") == "reset")
    s3["steps"] = steps = [x for i, x in enumerate(steps) if i != rst]
    s3["reset_after_port_open"] = False
    s3["total_deadline_seconds"] = S3_TOTAL_DEADLINE
    first = next(x for x in steps if x.get("cmd"))
    assert first["as"] == "sysboot", "续测的第一条查询必须还是 system_status: %s" % first.get("as")
    allow_rest_state(first)
    first["seconds"] = FIRST_QUERY_SECONDS
    first["seconds_note"] = (
        "75 s 是**上界不是等待时长**: read_until_prompt 一看到完整提示符就返回, 健康板在毫秒级就答完; "
        "把它拉长的唯一目的是让 stage-5 的 1 MiB 扫描(源码层估算 ≥8.5 s, cont 轮实测用 75 s 窗才拿满) "
        "有机会在同一个窗里把启动尾流 + 回显 + 响应一起交出来。R5 现场轮就是因为 6 s + 3 s 的窗被剪在 "
        "启动本身之前, 才把 0 字节当成了结论")
    s3["first_query_is_boot_probe"] = True
    s3["requires"] = []
    # 同一个缺陷在 S6 人工断电重启之后还会再咬一次: S7 的第一条查询也只有 6 s, 而重启后是一次**全新启动**
    # (stage-5 扫描 >=8.5 s, cont 轮要 75 s 窗才拿满)。这里一并放宽, 并标成启动探针交给同一条离线门管。
    s7 = by_id["S7"]
    s7["first_query_is_boot_probe"] = True
    s7["total_deadline_seconds"] = S3_TOTAL_DEADLINE
    q7 = next(x for x in s7["steps"] if x.get("cmd"))
    allow_rest_state(q7)
    q7["seconds"] = FIRST_QUERY_SECONDS
    q7["seconds_note"] = (
        "S6 是**操作者物理断电重上电**, 所以 S7 的第一条查询面对的是又一次冷启动(与 S3 同一判据): "
        "75 s 是上界, read_until_prompt 到完整提示符就返回; 这一窗仍然 0 字节就只能具名停止")
    s3["continuation_note"] = (
        "续测会话**不复位**: 板上跑的已经是本轮逐字节证过的那件镜像, 显式复位的唯一一次已在 %s 用掉; "
        "核此刻是在跑的状态(R5 轮 1.875 s 完成复位并收到 1027 B 启动流, 之后再没有 attach/halt)。"
        "若这条 75 s 窗仍然一个字节都没有, 具名停止 MISSING_RESPONSE_PROMPT, 由操作者做一次"
        "**计划内人工断电重启**(approved_reboot 仍有 1 次), 不在本包里自动复位" % RUN_DIR)
    for i, stg in enumerate(plan["stages"]):
        stg["order"] = i
    return plan


def fix_identity(plan):
    ident = plan["identity_binding"]
    ident["executor"] = {
        "path": "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py",
        "bytes": nbytes(CHAIN), "sha256": sha(CHAIN),
        "changes_in_this_round": [
            "drain_post_reset_boot(): 删掉**未申报**的静默早退(旧: elapsed>=min(2.0,window) 且静默 0.2 s "
            "即 break, 于是申报 20 s 实跑 2.0 s)。现在申报的预算走满, 只有两种提前结束: 看到正信号 "
            "boot_done_needle(产品自己的 bootstrap-complete 打印)再加一小段尾巴, 或显式申报了 "
            "boot_quiet_seconds",
            "BOOT_STREAM_DRAINED 事件与 boot_stream 结果新增 drain_declared_seconds / "
            "drain_spent_seconds / boot_done_needle / boot_quiet_seconds(申报与实耗一起进台账, "
            "下次再不一致就是自曝); end_reason 拆成 BOOT_DONE_NEEDLE / QUIET_BREAK / "
            "DRAIN_BUDGET_REACHED(旧值 BOUNDED_DRAIN 退役, 它曾经同时表示两种事)",
            "validate_plan(): 新增 PLAN_BOOT_WINDOW_COVER —— 计划申报 observation_bounds."
            "boot_window_seconds_min 时, 每个复位会话或首查询即启动探针的会话, 从复位/开口到第一条查询的"
            "可观测总量(申报的 drain + 首查询 seconds + SLACK)必须 ≥ 下界; 申报了 boot_quiet_seconds 或"
            "下界没有出处也判红; 未申报下界的历史包只登记 NA(不回改它们的字节, 也不给红)"],
        "unchanged": ("产品 C 源码、镜像与构建产物、下载/回读/复位 argv 语义、判据与清理/禁能合同、"
                      "机械前置门与 requires 传播、目标通道分类、首因四腿、event=5 取证门"),
        "executed_r4_package_bound_executor": R4_EXECUTOR_SHA,
        "why_two_hashes": (
            "R4 包(%s)已经**执行过**, 它当时用的是 %s/148267 B —— 那份字节与那份计划是配对跑过的, 所以 R4 "
            "包里的登记值保持不动(改它等于伪造执行输入)。本续测包绑定的是修好之后的执行器, 两者分列, "
            "谁在跑哪一轮一眼可查" % (SRC.name, R4_EXECUTOR_SHA[:12]))}
    ident["note"] = (ident.get("note", "") +
                     " || R5-C: 镜像/AXF/MAP 身份与 R4 包同一件(未重编, 未覆盖); 执行器换了身份, "
                     "因为观测窗缺陷修在执行器侧; 已在板的镜像由 R4 轮的回读逐字节证过, 本包不再下载/回读。")
    return plan


def fix_quota(plan, chain):
    needs = chain.derive_quota_needs(plan)
    q = dict(plan["quota"])
    for name, want in needs.items():
        q[name] = want
    for name in ("download", "readback", "explicit_reset"):
        q[name] = 0
    for name in ("retry", "physical_collision", "blackbox_clear", "dmm_readings"):
        q[name] = 0
    caps = sum(int(s["motion_cap_seconds"]) for s in plan["stages"] if s.get("motion_cap_seconds"))
    q["motor_run_seconds_total"] = max(caps, 0)
    plan["quota"] = q
    plan["quota_note_r5c"] = {
        "zero_because_spent": {
            "download": "1/1 已用(R5 轮 rc=0, 23.687 s, argv_sha256=922ae5a7…)",
            "readback": "1/1 已用(R5 轮 rc=0, 290916 B 与绑定镜像逐字节相同 => 镜像在板已证)",
            "explicit_reset": "1/1 已用(R5 轮 rc=0, 1.859 s, 之后收到 1027 B 启动流)"},
        "still_available": {k: plan["quota"][k] for k in
                            ("port_open", "motor_run", "motor_run_seconds_total",
                             "motion_cap_seconds_per_run", "approved_reboot", "config_save",
                             "tmc_config_apply", "diag_profile_learn", "diag_profile_commit",
                             "diag_profile_zero", "irq_polarity_prep",
                             "cleanup_motor_stop", "cleanup_motor_disarm",
                             "cleanup_motor_status") if k in plan["quota"]},
        "basis": ("派生量由执行器 derive_quota_needs() 从**删掉下载/回读/复位之后的真实执行路径**重算, "
                  "不是手填; 与 PLAN_QUOTA_NEED 同一口径"),
        "not_relaxed": ("起转次数、每次起转硬截止、学习 15 s、IMU 5 s、SG/ADC 加载链跳过、不清日志、"
                        "不升流升频 —— 全部沿用原批准, 本包只改观测窗")}
    return plan


def fix_bounds(plan):
    plan["observation_bounds"] = {
        "boot_window_seconds_min": BOOT_MIN,
        "why": ("这是**观测下界**, 不是对板子的断言: 从复位/开口到第一条查询之间, 主机至少要有这么长"
                "时间在听, 才有资格说它观察过启动。低于它而拿不到字节, 结论只能是'没观察到'"),
        "boot_window_provenance": [
            "经验上拿满: %s 的 boot_window_s=75 拿到 3439 B 完整启动(READY)+四条查询全部回答" % CONT_REF,
            "源码层耗时: 1 MiB 事件日志扫描按 NSF_SPI_HZ=1000000u 估 >=8.5 s, 且该文自陈是吞吐估算"
            "不是界限: %s §3" % AUDIT_REF,
            "反例(本包要修的正是这个): %s 的窗只有 2.0 s(被动)+ 9.0 s(首查询), 断在扫描行之后, "
            "实测 MISSING_RESPONSE_PROMPT as=sysboot bytes=0" % RUN_DIR],
        "applies_to": "每个含复位步的 serial_session + 每个标 first_query_is_boot_probe 的 serial_session",
        "enforced_by": "m8r3_serial_chain.validate_plan() 的 PLAN_BOOT_WINDOW_COVER(离线, 不开端口)",
        "executor_side_fix": "申报的 boot_drain_seconds 现在真的走满(旧实现被未申报的静默早退剪短)",
        "not_a_claim": "本下界不证明板子在 75 s 内一定应答; 仍然 0 字节就是具名停止 + 人工断电重启门"}
    return plan


def fix_record(plan, r2):
    plan["package"] = "M8 idle 栈 1024 核心现场包 R5-C 续测(零下载/零回读/零显式复位, 观测窗修好之后)"
    plan["status"] = "BOUND_NOT_EXECUTED"
    plan["task_id"] = "phase8-m8-idle-stack-r4"
    plan["continuation_of"] = {
        "approved_tip": APPROVED_TIP,
        "run_dir": RUN_DIR,
        "plan_executed": "reports/reviews/phase8-m8-idle-stack-r4/field/plan_core_r4.json",
        "executor_executed": {"sha256": R4_EXECUTOR_SHA, "bytes": 148267},
        "proven_by_that_run": [
            "新镜像在板: readback.bin 290916 B sha256=%s, 与 identity_binding.image 及归档 BIN 逐字节相同"
            % READBACK_SHA,
            "下载/回读/显式复位各恰好一次且 rc=0(一次性额度已尽)",
            "复位后有**真实启动流** 1027 B(sha256=%s), 末行 [FLASH] scanning 1 MiB event log..." % BOOT_SHA,
            "零次起转: motor_run 键缺席, 台账里没有任何 motor_arm/motor_start"] ,
        "where_it_stopped": {
            "stage": "S3", "code": "MISSING_RESPONSE_PROMPT", "detail": "as=sysboot bytes=0",
            "observed_horizon_seconds": 11.0,
            "declared_but_not_honored": "boot_drain_seconds=20 实截为 2.0 s(未申报的静默早退)",
            "flow_complete": False, "core_protection_verified": False, "claims": {}},
        "not_claimed": ("这一轮没有证伪也没有证实任何东西: H1 无界 SPI 自旋 / H2 核没在跑 / "
                        "H3 主机到板 RX 不通 / H4 只是启动比 11 s 慢, 四条并列(与 OFFLINE_JUDGEMENT.txt §2 "
                        "同一口径)。idle 栈在板容量没读到(ps 从未发出)"),
        "sequence_diff": "删 S1/S2 + 删 S3 的复位步; 其余阶段与场景链原样(含 SG/ADC 那场仍被 load_means 拒绝)"}
    plan["changes_this_round_r5c"] = [
        "① 额度: download/readback/explicit_reset 归零并删掉对应设备阶段(一次性批准已烧掉)",
        "② 观测: S3 首查询 6 -> 75 s + first_query_is_boot_probe + 阶段总时限 90 -> 300 s; "
        "执行器按申报走满被动窗",
        "③ 身份: 新执行器散列只绑进本续测包; 已执行的 R4 包保留它当时真正跑过的 40517501b3be…",
        "④' 判据: system_status 响应里的开机默认安全态行 arm gate fail mask=0x18 (arm REFUSED) "
        "不再被当成 BUSINESS_FAILED(执行器新增**整行锚定**且必须被 expect_contains 正面要求该行的白名单; "
        "掩码值不写死——现场实测同一块板先后给出 0x18 与 0x8, 写死就会把真实状态误判成违规)。"
        "离线门 PLAN_STATUS_ALLOW 管住它的形状(必须是前缀要求 + 锚定 + 无 .* 通配)",
        "④ 门: PLAN_BOOT_WINDOW_COVER 把'观测窗短于启动本身'变成离线就红, 不再靠现场去发现",
        "⑤ 登记: R5 现场轮逐场分类见 run_record §13 与 %s/OFFLINE_JUDGEMENT.txt" % RUN_DIR]
    fs = plan.get("field_sequence_before_start") or {}
    if fs:
        seq = r2.blocked_stages(plan)
        fs["expected_executed_stages"] = seq["executed_stages"]
        fs["expected_skipped_stages"] = seq["skipped_stages"]
        fs["expected_executed_count"] = len(seq["executed_stages"])
        fs["sequence_basis"] = (
            "由 r2.blocked_stages() 从计划数据推导(与执行器的门规则同一份实现), 再用真实 stub 干跑的 "
            "prerequisite_skips 对账(idle_learn_check 的 P6) —— 不手填序列")
        fs["continuation_of_run"] = RUN_DIR
        fs["no_download_readback_reset"] = (
            "本续测包的设备动作集合里没有 download / readback / explicit_reset(额度 1/1 已在 R5 轮用尽), "
            "第一条设备动作是开一次 COM5 然后直接问 system_status")
        plan["field_sequence_before_start"] = fs
    plan["hardware_actions_this_round"] = []
    plan["device_actions_statement"] = (
        "本包只是**绑定**: 生成本文件时零设备动作(transport=stub/sim 的离线工装上跑, 假端口, 不下载、"
        "不回读、不复位、不起转)。现场执行需要复审对这份续测包 + 修好的执行器的一次原文批准。")
    plan["plan_identity"] = {"path": "reports/reviews/phase8-m8-idle-stack-r4/field/plan_core_r5c.json",
                             "rows_file": {"path": "reports/reviews/phase8-m8-idle-stack-r4/field/"
                                                   "rows.json", "bytes": nbytes(ROWS),
                                           "sha256": sha(ROWS)}}
    return plan


def load_r2():
    return load_module(HERE / "apply_package_corrections_r2.py", "r2_for_r5c")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



# 上机第一次真答回来才发现的三处**计划侧正则**缺陷(现场响应是既存事实, 不是推测):
#  a) polarity=not-confirmed 含连字符, \w+ 匹配不到 => FIELD_NOT_EXACTLY_ONE(实测 count=0);
#  b) ps 的 diag/tidle0 行在 `NN%` 之后还有 left tick / error / tcb addr / usage 四列, 旧模式以 % 收尾,
#     锚在行尾就永远取不到行(实字: "diag  9  suspend 0x000003cc 0x00001000  28%  0x0000000a EINTRPT ...");
#  c) 同一形状在 S3/S7 两个会话里都有, 必须一起改。
ROW_FIXES = [(r"polarity=\w+ protect_ready=\w+$", r"polarity=[\w-]+ protect_ready=[\w-]+$")]
# ps 的行以 `NN%` 结束但没有行尾锚, fullmatch 就把后面 4 列(left tick/error/tcb addr/usage)当成多余字符 =>
# 取不到行(实测 count=0)。补一个**尾列**分组: 只允许"空白 + 非空白"重复, 不放开任何已捕获字段的判据。
PS_TAIL = r"(?:\s+\S+)*$"


def fix_row_patterns(plan):
    n = 0
    for stg in plan["stages"]:
        for st in stg.get("steps", []):
            for ev in st.get("expect_values", []) or []:
                row = ev.get("row", "")
                for old, new in ROW_FIXES:
                    if old in row:
                        ev["row"] = row = row.replace(old, new)
                        n += 1
                if row.rstrip().endswith("%") and not row.rstrip().endswith("$"):
                    ev["row"] = row = row.rstrip() + PS_TAIL
                    n += 1
            for i, (pat, what) in enumerate(st.get("expect_fields", []) or []):
                for old, new in ROW_FIXES:
                    if old in pat:
                        pat = pat.replace(old, new)
                        n += 1
                if pat.rstrip().endswith("%") and not pat.rstrip().endswith("$"):
                    pat = pat.rstrip() + PS_TAIL
                    n += 1
                st["expect_fields"][i] = (pat, what)
    plan["row_pattern_fixes_r5c"] = {
        "count": n,
        "why": ("现场第一次答回来才发现的取行失败: polarity 值含连字符(实测 not-confirmed), "
                "ps 的 NN% 之后还有 4 列(left tick/error/tcb addr/usage)。两处都按板上实字改, "
                "证据: field-20261101T151317Z/S_S3/01_sysboot.rx.bin(983 B 完整响应) 与 "
                "cont-20261101T090512Z/S4_ps.rx.bin 的真实行"),
        "not_relaxed": "栈大小/取行唯一性/must 值这些判据一个没删, 只是把行尾的列补全"}
    return plan


def build():
    chain = load_chain()
    r2 = load_r2()
    plan = json.loads(SRC.read_text(encoding="utf-8-sig"))
    by_id = {s["id"]: s for s in plan["stages"]}
    fix_stages(plan, by_id)
    fix_identity(plan)
    fix_quota(plan, chain)
    fix_bounds(plan)
    fix_row_patterns(plan)
    fix_record(plan, r2)
    return plan, chain


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对, 不写盘(幂等证明)")
    a = ap.parse_args()
    plan, chain = build()
    body = dump(plan)
    lines, bad, _ = chain.validate_plan(json.loads(body), json.loads(ROWS.read_text(
        encoding="utf-8-sig")), str(ROOT / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                                                   "ns800rt7p65-nssinepad/applications"))
    cover = [l for l in lines if l.startswith("PLAN_BOOT_WINDOW_COVER")]
    print("R5C_VALIDATE|checks=%d|fail=%d|%s" % (len(lines), bad, ";".join(cover)))
    needs = chain.derive_quota_needs(plan)
    print("R5C_NEEDS|%s" % json.dumps(needs, sort_keys=True))
    if a.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8-sig") == body
        print("R5C_CHECK|%s|bytes=%d|sha=%s" % ("IDEMPOTENT" if same else "DIFFERS",
                                                len(body.encode("utf-8")),
                                                hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]))
        return 0 if (same and bad == 0) else 1
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("R5C_WRITE|%s|bytes=%d|sha=%s" % (OUT.as_posix(), len(body.encode("utf-8")), sha(OUT)[:16]))
    if bad:
        print("\n".join(l for l in lines if "|FAIL|" in l))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
