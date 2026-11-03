#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""派生 M8-R7 **有界现场验证候选包**(离线准备; 本包不执行任何设备动作)。

为什么必须新写一份而不是把 R6C 模板换个批准 ID(任务书 §7 明确禁止):
  * R6C 那份是**离线返修模板**, image_binding 仍绑在已证在板的 dab356c6…/290916 B;
    要烧 5ae4110d…/292164 B 就必须把 S0/下载/回读三处一起重绑到同一件新镜像
    (EXEC 的 identity_consistency 门就是为"回读仍绑上一轮 BIN"这种错造的)。
  * 新镜像的回读长度不同(292164 vs 290916) => @IMAGE_BYTES 展开值不同, 定长回读判据也不同。
  * 本轮**只覆盖不需要加载手段的部分**(下面 ① ② ③), 因为 `motor_arm`/`motor_start` 属
    EXEC 的 MOTION_OBLIGATION_CMDS, 而包级 mechanical_prerequisites.load_means 仍是
    NOT_PROVIDED => 执行器会在**开端口之前**具名拒绝整条运动链。把运动步骤写进包里再指望它
    "现场再看"等于制造一条一定会烧掉批准的中途失败, 所以不写。

范围(全部为非运动动作):
  ① 启动: 一次显式复位 + 被动启动流采集(申报窗走满 >= observation_bounds 下界) + 只读状态查询
  ② 相关短打印: 5ae 里被拆短的 [SAFETY] LIMIT 行(只读 safety_irq_status/safety_status 取证)
     与 [TMC-CFG] CONFIGURED 行(一次 tmc_config_apply, 非运动但会写驱动芯片寄存器 => 待批)
  ③ 新镜像身份闭环: 一次下载 + 一次定长 292164 B 回读逐字节等于归档 BIN

纪律: 源件冻结校验 / --check 幂等 / 不带 --force 不覆写 / 所有镜像与源码字节**当场从磁盘算** /
      approval.hardware_actions_authorized_by_this_package=0(本脚本不创建可用现场批准)。
"""
import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
ROUND = HERE.parent
FIELD = ROUND / "field"
SRC_PLAN = (REPO / "reports/reviews/phase8-m8-r6c-cleanup-evidence-and-s0"
            / "field" / "plan_core_r6c.json")
OUT = FIELD / "plan_core_r7.json"
ROWS = FIELD / "rows.json"
FW5 = REPO / "reports/firmware/phase8-m8-r6a-offline-rework"      # 5ae 那一次构建的归档
EXEC_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
APP = "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications"
# R6C 交付件的字节(= A 本次复审 §3 复算过的登记); 源被改过就拒绝派生
FROZEN_SRC_SHA = "408c18ecaf50f62a0620c904011f0c60bea3681087875a2ada64efbae7b6de73"
BOOT_DRAIN_SECONDS = 75          # 被动启动窗; 与首条查询合计须 >= observation_bounds 下界
FIRST_QUERY_SECONDS = 10
OLD_TOKEN = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def source_literal(rel_path, lines_1based, prefix_len=60):
    """从**产品源码**取一条 rt_kprintf 的字面量(C 里相邻字面量会拼接), 生成现场可机械判定的
    判据前缀与保守字节上界。跨多行的调用必须一次给全行号, 否则只会拿到续行片段。

    needle 在**第一个格式说明符之前**截断: 预测判据不许要求某个具体数值落在那一行里。
    宽度按与 host/line_width_check.py 同一类保守代入(%s=18、%d/%u=11/10、%08X=8)。
    """
    src = (REPO / rel_path).read_text(encoding="utf-8").splitlines()
    body = " ".join(src[n - 1].strip() for n in lines_1based)
    lits, i = [], 0
    while True:
        j = body.find(chr(34), i)
        if j < 0:
            break
        k = j + 1
        while k < len(body) and body[k] != chr(34):
            k += 2 if body[k] == chr(92) else 1
        lits.append(body[j + 1:k])
        i = k + 1
    if not lits:
        raise SystemExit("R7_DERIVE_REFUSE|%s:%s 取不到字面量: %s" % (rel_path, lines_1based, body[:80]))
    full = "".join(lits).replace(chr(92) + "n", " ").replace(chr(92) + chr(34), chr(34))
    spec = "%[-+ #0]*[0-9.]*[sdioxXeEgGfup]"
    cut = re.split(spec, full)[0]
    text = re.sub(chr(92) + "s+", " ", cut).strip()
    width = {"s": "X" * 18, "d": "-1111111111", "u": "1111111111", "X": "0" * 8, "x": "0" * 8}
    worst = re.sub(spec, lambda m: width.get(m.group(0)[-1], "X" * 11), full)
    return {"needle": text[:prefix_len],
            "source": "%s:%s" % (rel_path, ",".join(str(n) for n in lines_1based)),
            "literal_with_specifiers": full[:160],
            "line_bytes_upper_bound": len(worst.encode("utf-8"))}


def image_identity():
    """5ae 三件产物 + manifest 里对应行, 全部现算; manifest 不符就拒绝派生。"""
    binp, axf, mp = (FW5 / "rt-thread-motor-m1-candidate.bin", FW5 / "rt-thread-motor-m1.axf",
                     FW5 / "rt-thread-motor-m1.map")
    man = json.loads((FW5 / "build_manifest.json").read_bytes().decode("utf-8-sig"))
    tgt = [t for t in man["targets"] if t.get("target") == "rt-thread-motor-m1-candidate"]
    if not tgt:
        raise SystemExit("R7_DERIVE_REFUSE|manifest 里没有 target=rt-thread-motor-m1-candidate")
    t = tgt[0]
    images = t.get("images") or {}
    for fn, p in (("rt-thread-motor-m1-candidate.bin", binp), ("rt-thread-motor-m1.axf", axf),
                  ("rt-thread-motor-m1.map", mp)):
        rec = images.get(fn)
        if not rec:
            raise SystemExit("R7_DERIVE_REFUSE|manifest 的 images 里没有 %s" % fn)
        got, got_bytes = sha(p), p.stat().st_size
        if rec.get("sha256") != got or rec.get("bytes") != got_bytes:
            raise SystemExit("R7_DERIVE_REFUSE|%s 磁盘实算 %s…/%d B != manifest %s…/%s B"
                             % (fn, got[:12], got_bytes, str(rec.get("sha256"))[:12],
                                rec.get("bytes")))
    return t, {"bin": binp, "axf": axf, "map": mp}


def build():
    if sha(SRC_PLAN) != FROZEN_SRC_SHA:
        raise SystemExit("R7_DERIVE_REFUSE|%s 的字节不是 R6C 交付那份 %s…, 拒绝从被改过的源派生"
                         % (SRC_PLAN.name, FROZEN_SRC_SHA[:12]))
    plan = json.loads(SRC_PLAN.read_text(encoding="utf-8-sig"))
    t, art = image_identity()
    rel = {k: art[k].relative_to(REPO).as_posix() for k in art}
    size = {k: art[k].stat().st_size for k in art}
    dig = {k: sha(art[k]) for k in art}

    # ---------------- 镜像身份整体重绑到 5ae(S0/下载/回读三处必须同一件) ----------------
    img = plan["identity_binding"]["image"]
    img["path"], img["bytes"], img["sha256"] = rel["bin"], size["bin"], dig["bin"]
    img["manifest"] = (FW5 / "build_manifest.json").relative_to(REPO).as_posix()
    img["manifest_target"] = t["target"]
    for role in ("axf", "map"):
        d = img.get(role) or {}
        d.update({"path": rel[role], "bytes": size[role], "sha256": dig[role]})
        img[role] = d
    img["identity_source"] = ("本包 identity_binding.image 由 host/derive_plan_r7.py 在派生时刻从 "
                              "reports/firmware/phase8-m8-r6a-offline-rework/ 的磁盘字节现算; "
                              "manifest 的 target=%s 行逐字段相符(不符则拒绝派生)" % t["target"])
    plan["identity_binding"]["tested_image_build_commit"] = \
        plan["identity_binding"].get("software_input_commit") or "44e493cea9bbbc35e18394ca23dd1a8502519448"
    plan["identity_binding"]["software_input_commit"] = "44e493cea9bbbc35e18394ca23dd1a8502519448"
    plan["identity_binding"]["reviewed_delivery_commit"] = \
        subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
    plan["identity_binding"]["image_rebind_note"] = {
        "from": {"path": "reports/firmware/phase8-m8-idle-stack-r4/rt-thread-motor-m1-candidate.bin",
                 "bytes": 290916, "sha256": "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439"},
        "to": {"path": rel["bin"], "bytes": size["bin"], "sha256": dig["bin"]},
        "why": ("R6C 模板绑的是**最后已证在板**的那件(dab); 本包要把 5ae 那次构建的候选件真正上板并"
                "逐字节证明, 所以 S0/下载/回读三件一起重绑。旧包不回填: R6C/R6B/R6A/R6/R5-C 计划与"
                "其镜像登记保持原字节"),
        "program_size_from_build_log": t.get("program_size"),
        "readback_length_note": ("定长回读取 identity_binding.image.bytes=%d => 展开成 @IMAGE_BYTES=0x%X"
                                 % (size["bin"], size["bin"]))}
    plan["identity_binding"]["accepted_image_not_overwritten"] = (
        "5ae/d70/dab 三件归档 BIN 一件都不覆盖; 下载只往板上写, 归档字节不动")

    # ---------------- 下载之前板上是哪一件: 按**最新一次逐字节回读**更正 ----------------
    # R6C 模板这一段仍是 f5c2fd89…("板现运行该镜像"), 那是 diag 栈轮(1084256Z)的回读结论;
    # 之后 idle-stack-r4 轮(142722Z)的一次下载 + 一次定长回读把在板件换成了 dab356c6…。
    # 本包要下载覆盖的正是这一件, "下载前板上是谁"写错会让现场判读错位, 所以就地更正(旧包不回改)。
    plan["identity_binding"]["board_image_before"] = {
        "path": "reports/firmware/phase8-m8-idle-stack-r4/rt-thread-motor-m1-candidate.bin",
        "bytes": 290916,
        "sha256": "dab356c69fbf4ca747e838580c16982bd56b53b1367f84b97ab705c2fbd25439",
        "proven_on_board": ("reports/reviews/phase8-m8-idle-stack-r4/field/field-20261101T142722Z/"
                            "readback.bin 实测 290916 B、sha256 与归档 BIN 相同且逐字节相等"
                            "(同目录 OFFLINE_JUDGEMENT.txt §'S2 定长回读': OK, 1/1 用尽)"),
        "role": ("只登记**下载之前**板上最近一次逐字节证到的那一件; 不作回读比较对象"
                 "(回读比较对象是 identity_binding.image=5ae)"),
        "not_a_claim_about_power_state": ("不据此推断此刻是否上电、是否停在 HardFault 或 Halted; "
                                          "板态是现场事实, 由操作者回报"),
        "supersedes_in_r6c_template": {
            "sha256": "f5c2fd89bbf28255a5b26c9cca495bc0fb1e39d401d0bb7bea19d051221708ce",
            "bytes": 290916,
            "proven_by": "reports/reviews/phase8-m8-diag-stack-r1/field/field-20261101T084256Z/flash_readback.bin",
            "why_stale": ("它是更早一轮(DS)证到的在板件; idle-stack-r4 轮那一次下载+回读之后, "
                          "在板件已换成 dab356c6…。R6C 是 A 已签收的冻结件, 不回改; 本包在自己的"
                          "登记里更正并保留这条旧口径以便对账")}}

    # ---------------- 执行器登记: 本机绝对路径 -> 仓内相对路径 ----------------
    ex = plan["identity_binding"]["executor"]
    ex["path_r6c_template_verbatim"] = ex.get("path")
    ex["path"] = EXEC_REL
    ex["path_note_r7"] = ("R6C 里这个 path 是本机绝对路径(D:/ns800/…), 换机器按该字符串找不到文件; "
                          "本包改成仓内相对路径, bytes/sha256 一字未动, 绝对路径保留在 "
                          "path_r6c_template_verbatim 以便对账")

    # ---------------- 工具 argv: 只换下载载荷与回读镜像身份 ----------------
    tools = plan["tools"]
    dl = list(tools["download"]["argv"])
    dl[-1] = rel["axf"]
    tools["download"]["argv"] = dl
    tools["download"]["image"] = {"path": rel["axf"], "bytes": size["axf"], "sha256": dig["axf"],
                                  "identity_source": "与 identity_binding.image.axf 同一件(现算)"}
    tools["download"]["payload_note"] = (
        "载荷 = 5ae 那次构建的 AXF(%s…, %d B); 与 image 的 BIN 同源同批(build_manifest 的 "
        "target=%s 行), 因此回读逐字节相等才是这条链的判据" % (dig["axf"][:12], size["axf"], t["target"]))
    tools["readback"]["image"] = {"path": rel["bin"], "bytes": size["bin"], "sha256": dig["bin"],
                                  "identity_source": "与 identity_binding.image 同一件(本脚本现算)"}
    tools["readback"]["start_address"] = "0x08000000"
    tools["readback"]["out_file"] = "readback.bin"

    # ---------------- 短打印判据: 从产品源码取字面量, 不抄旧抓包 ----------------
    lit = lambda path, line: source_literal("%s/%s" % (APP, path), line)
    limit_cfg = lit("safety_thread.c", [374])
    limit_off = lit("safety_thread.c", [376, 377])
    cfg1, cfg2, cfg3 = (lit("tmc2209.c", [2842]), lit("tmc2209.c", [2843]),
                        lit("tmc2209.c", [2844]))
    plan["print_width_targets"] = {
        "gate": "产品控制台 128 B 裁剪会让超长行剪掉尾部(含 NUL); 本轮 5ae 把这些行拆成多次调用",
        "limit_line": [limit_cfg, limit_off],
        "tmc_configured_lines": [cfg1, cfg2, cfg3],
        "mot_withheld_lines_not_in_scope": {
            "why": ("[MOT] 关闭已落位但未授予恢复资格… 这条 withheld 打印只在**禁能侧证据不足**分支出现, "
                    "而进入该分支需要 motor_arm/motor_start(=MOTION_OBLIGATION_CMDS), 被 load_means "
                    "NOT_PROVIDED 的机械前置门拒绝 => 本包不声称覆盖它"),
            "sources": [lit("motor.c", [1883]), lit("motor.c", [1885]), lit("motor.c", [1889]),
                        lit("motor.c", [1892])]},
        "assertion_mode": ("现场只声明**可机械判定**的东西: 抓到的原始字节逐行长度 <=120 B、"
                           "无裸 NUL、且上面这些字面量前缀按行出现; 不拿'看到一行'推断产品全对")}

    plan["stub_branches"] = {}
    plan["motion_budget"] = {
        "starts": 0, "seconds_total": 0, "per_stage_cap": "无(本包没有任何起转步骤)",
        "registered_checker_gap": (
            "EXEC 的 PLAN_MOTION_CAP_BUDGET 对**无起转**的包是恒红门: "
            "caps 只收 motion_cap_seconds 为真值的阶段(m8r3_serial_chain.py:2678-2679), 而 "
            "ok_t 要求 caps 非空且 sum(caps)<=quota.motor_run_seconds_total(:2683)。"
            "本包 motor_run=0/没有任何 motor_start 步骤 => 只能靠**谎报一个非零上限并要求总额>=它**"
            "才能变绿, 那是给一个不打转的包申报运动预算。本轮不为了让门变绿改判据: "
            "validate-plan 因此 rc=1 且只有这一项 FAIL, 已如实登记。本包该满足的合同由 "
            "host/r7_readiness.py 的三条 R7-2 独立判据承担: 没有任何起转/带电步骤、"
            "运动与学习类额度全 0、两个会话阶段都挂禁令且步骤不撞禁令")}
    # ---------------- 阶段链: 无运动、无学习、无保存 ----------------
    reset_argv = list(tools["reset"]["argv"])
    plan["stages"] = [
        {"id": "S0", "kind": "offline", "title": "真实文件与来源绑定(镜像/AXF/map/manifest/needles/提交)",
         "steps": []},
        {"id": "S1", "kind": "download", "requires": ["S0"],
         "title": "一次 sector 下载 5ae 载荷", "steps": []},
        {"id": "S2", "kind": "readback", "requires": ["S1"],
         "title": "一次定长 %d B 回读并逐字节比对归档 BIN" % size["bin"], "steps": []},
        {"id": "S3", "kind": "serial_session", "requires": ["S2"], "port": "COM5",
         "total_deadline_seconds": 260, "reset_after_port_open": True, "halt_after": False,
         "first_query_is_boot_probe": True,
         "post_reset_boot": ["msh >"], "boot_bytes": [],
         "post_reset_boot_provenance": ("r3 现场包与 R5-C 续测包同形状(EXEC 的 PLAN_RESET_BEFORE_FIRST_QUERY "
                                        "要求 reset 在第一条查询之前且会话申报 post_reset_boot); "
                                        "被动窗 75 s + 首条查询 10 s 覆盖 observation_bounds.boot_window_seconds_min"),
         "forbidden_commands": ["motor_arm", "motor_start", "diag_profile learn", "diag_profile commit",
                                "config_save", "blackbox_clear", "safety_irq_enable",
                                "safety_polarity_confirm", "motor_dir", "motor_target"],
         "title": "启动与只读取证(一次显式复位 + 被动启动窗 + 只读查询)",
         "steps": [
             {"action": "reset", "as": "rst", "quota_name": "explicit_reset",
              "command": " ".join(reset_argv), "argv": reset_argv,
              "boot_drain_seconds": BOOT_DRAIN_SECONDS,
              "boot_drain_note": ("被动启动流采集 %d s: 不猜『没输出就是没起来』; 采集原文入证据目录, "
                                  "行宽/裸 NUL/字面量都按原始字节判。不申报 boot_quiet_seconds"
                                  "(静默会把窗重新剪短)" % BOOT_DRAIN_SECONDS)},
             {"cmd": "system_status", "as": "sysboot", "seconds": FIRST_QUERY_SECONDS,
              "expect_contains": ["=== SYSTEM STATUS ===", "[Boot]   bootstrap-complete=YES"],
              "expect_values": [{"row": "^\\[Boot\\]   bootstrap-complete=(?P<bc>\\w+) abort-stage=(?P<ab>\\d+)$",
                                 "must": {"bc": "YES", "ab": "0"}},
                                {"row": "^\\[Safety\\] state=(?P<state>\\w+) fault=(?P<fault>\\d+) "
                                       "irq_gate=\\w+ polarity=\\w+ protect_ready=\\w+$",
                                 "must": {"state": "READY", "fault": "0"}}]},
             {"cmd": "safety_status", "as": "safboot", "seconds": 6,
              "expect_contains": ["[SAFETY] "], "note": "只读; 行宽判据覆盖它打出的每一行"},
             {"cmd": "safety_irq_status", "as": "irqstat", "seconds": 6,
              "expect_contains": [limit_cfg["needle"], limit_off["needle"]],
              "expect_contains_note": ("构建期 SAFETY_LIMIT_INPUTS_ENABLED=RT_FALSE "
                                       "(project_board.h:50-51) => 期望 OFF 分支那一行; ON 分支字面量也登记在 "
                                       "print_width_targets.limit_line; R6 拆出来的两条短行都要出现, "
                                       "少一条就是该行没打全(现场裁剪/顺序错乱), 判失败不判通过",
                                       "按失败处理而不是随手放宽判据")},
             {"cmd": "tmc_config_show", "as": "cfgboot", "key": "tmc_config_show@boot", "seconds": 6,
              "expect_contains": ["[TMC-CFG] state=", "[TMC-CFG] planned irun=20"],
              "note": "只读; 开机未 apply 时 state=UNCONFIGURED 是既有实抓事实, 不当失败"},
             {"cmd": "motor_status", "as": "motboot", "seconds": 6,
              "expect_contains": ["[MOT] "], "note": "只读查询, 不占动作额度"}]},
        {"id": "S4", "kind": "serial_session", "requires": ["S3"], "port": "COM5",
         "total_deadline_seconds": 120, "halt_after": False,
         "forbidden_commands": ["motor_arm", "motor_start", "diag_profile learn", "diag_profile commit",
                                "config_save", "blackbox_clear", "safety_irq_enable",
                                "safety_polarity_confirm", "motor_dir", "motor_target"],
         "title": "一次 tmc_config_apply 取证 CONFIGURED 短行(非运动, 但会写驱动芯片)",
         "steps": [
             {"cmd": "tmc_config_apply CONFIRM", "as": "cfgapply", "key": "tmc_config_apply CONFIRM",
              "seconds": 12, "quota": {"name": "tmc_config_apply", "n": 1},
              "expect_absent": ["REFUSED"],
              "expect_contains": [cfg1["needle"], cfg2["needle"], cfg3["needle"]],
              "expect_contains_source": [cfg1["source"], cfg2["source"], cfg3["source"],
                                        "%s:2853" % (APP + "/tmc2209.c")],
              "note": ("5ae 把原来一条 124 B(现场实抓) 的 CONFIGURED 摘要拆成逐条短行; 本步骤判的是"
                       "**逐条都出现**且逐行不超限")},
             {"cmd": "tmc_config_show", "as": "cfgafter", "seconds": 6,
              "expect_contains": ["[TMC-CFG] state=CONFIGURED"]},
             {"cmd": "system_status", "as": "sysafter", "seconds": 6,
              "expect_contains": ["=== SYSTEM STATUS ==="]},
             {"cmd": "motor_status", "as": "motafter", "seconds": 6,
              "expect_contains": ["[MOT] "]}]},
    ]
    for s in plan["stages"]:
        s.setdefault("steps", [])

    # ---------------- 额度: 每一条都给"为什么是这个数", 全部非运动 ----------------
    plan["quota"] = {"port_open": 2, "download": 1, "readback": 1, "explicit_reset": 1,
                     "tmc_config_apply": 1, "approved_reboot": 0, "config_save": 0,
                     "irq_polarity_prep": 0, "diag_profile_zero": 0, "diag_profile_learn": 0,
                     "diag_profile_commit": 0, "motor_run": 0, "motor_run_seconds_total": 0,
                     "motion_cap_seconds_per_run": 0, "cleanup_motor_stop": 0,
                     "cleanup_motor_disarm": 0, "cleanup_motor_status": 0, "retry": 0,
                     "physical_collision": 0, "blackbox_clear": 0, "dmm_readings": 0}
    plan["quota_rationale"] = {
        "port_open": "两个会话各开一次(S3 启动取证 / S4 apply 取证); 不多开",
        "download": "一次 sector 下载新载荷; 与旧批准一样不重试",
        "readback": "一次定长 %d B 回读 => 新镜像在板的逐字节证明" % size["bin"],
        "explicit_reset": "一次复位把新镜像跑起来并做被动启动流采集; 之后不再复位",
        "tmc_config_apply": ("一次(非运动): 5ae 拆短的 [TMC-CFG] CONFIGURED 行只有走 apply 提交路径才打; "
                             "它写驱动芯片寄存器但不通电到绕组, 故仍需单独批准"),
        "zero_because": ("motor_run/motor_arm 相关与 learn/config_save/blackbox_clear 全为 0: "
                         "本包不含起转(见 mechanical_prerequisites.load_means)也不做学习与保存"),
        "approved_reboot": "0: 本包不需要断电再上电门; 若现场要人工重启须另批"}
    plan["quota_note_r7"] = ("额度是**提案不是批准**(numbers_are_proposals_not_authorization 同样适用): "
                             "旧批准 %s 的额度已用尽且不结转, 本包登记 hardware_actions_authorized_by_this_package=0"
                             % OLD_TOKEN)
    plan["numbers_are_proposals_not_authorization"] = True

    # ---------------- 机械前置: 如实保持, 并写清它挡掉了什么 ----------------
    mp = plan.setdefault("mechanical_prerequisites", {})
    mp.setdefault("load_means", {})
    mp["load_means"].update({
        "status": "NOT_PROVIDED", "blocks_motion": True,
        "what_this_package_does_because_of_it": (
            "本包的阶段链里**没有** motor_arm/motor_start/diag_profile learn 等运动步骤; "
            "复审 §1 的第三条优先项'正常 disarm 收口'需要先把电机 arm 起来(motor_arm 属 "
            "MOTION_OBLIGATION_CMDS), 在当前 load_means 下执行器会在开端口之前具名拒绝整条链, "
            "所以它不进本包 —— 要么操作者提供加载手段的具体档位/字段并登记为 PROVIDED, "
            "要么改走别的取证路径, 不允许把未测写成已验"),
        "would_be_used_by": "未来含 motor_arm/motor_start 的 disarm 收口包"})
    plan["scope_this_package"] = {
        "covers": ["① 启动(一次显式复位 + 被动启动窗 + 只读状态查询)",
                   "② 相关短打印([SAFETY] LIMIT 行 / [TMC-CFG] CONFIGURED 三条短行, 逐行宽度与字面量)",
                   "③ 新镜像身份闭环(一次下载 + 一次 %d B 定长回读逐字节等于归档 BIN)" % size["bin"]],
        "does_not_cover": ["正常 disarm 之后的 Safety 收口(需 motor_arm => 被 load_means 门拒)",
                           "[MOT] withheld 四条短行的现场出现(同因)",
                           "SG/ADC 真实加载、阈值生成与有效性、ACTIVE 覆盖、REL_IMPACT、"
                           "保存恢复与故障恢复闭环",
                           "idle1024 带载最坏栈余量、现场精确线程交错、0x00bd80b0 写入者"]}
    plan["package_status"] = "R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED"
    # 顶层三个身份字段(package/task_id/status)在继承链上一直写着上一轮"R5-C 续测包"的名字。
    # 本包是另一条阶段链+另一件镜像, 名字不对会让远端回读与现场判读指错对象; 执行器不读这三条,
    # 所以改它们不改变任何行为。旧包不回改。
    plan["package"] = ("M8-R7 有界现场验证候选包(5ae 新镜像上板逐字节证明 + 启动与相关短打印取证; "
                       "零起转/零加载/零保存)")
    plan["task_id"] = "phase8-m8-r7-field-package-5ae"
    plan["status"] = "R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED"
    plan["identity_fields_note_r7"] = (
        "package/task_id/status 这三个顶层身份字段在 R6C/R6B/R6A 里仍写着上一轮的 'M8 idle 栈 1024 核心现场包 "
        "R5-C 续测'(task_id=phase8-m8-idle-stack-r4)。本包不是那一轮的续测: 阶段链、镜像、额度都不同, "
        "所以这里改成自己这一轮。执行器不读这三个字段, 改动不影响行为; 旧包不回改。")
    # 本包只把阶段收成 S0–S4, 但整块派生自 R6C 模板 => 继承的历史字段仍按旧全包的 S0–S11 编号说话。
    # 两边会撞号(旧 S4=巡圈学习要起转, 本包 S4=tmc_config_apply 不起转), 所以把撞号点显式登记出来,
    # 而不是让读者自己猜。逐块扫描而不是手抄清单: 内容再变也不会漏登记。
    legacy = {}
    for k, v in plan.items():
        if k == "stages":
            continue
        hits = sorted(set(re.findall(r"\bS(?:[5-9]|1[01]b?)\b", json.dumps(v, ensure_ascii=False))))
        if hits:
            legacy[k] = hits
    plan["stage_numbering_and_legacy_fields"] = {
        "this_package_stages": [s["id"] for s in plan["stages"]],
        "collision_warning": ("旧全包(上一轮的 S0–S11)与**本包**的 S0–S4 会撞号: 旧块的 S4 是"
                              "'巡圈零点+学习'(要 motor_start), 本包的 S4 是一次 tmc_config_apply 取证"
                              "(不起转)。下面这些块里出现的 S5–S11 以及其中的 S4/S8/S10 等, 一律按"
                              "**旧全包**理解, 不是本包要跑的阶段。"),
        "legacy_history_only_blocks": legacy,
        "prerequisite_gates_in_this_package": (
            "本包 %d 个阶段都没有声明 prerequisite_gates, 且整包步骤里没有 MOTION_OBLIGATION_CMDS"
            "(motor_arm/motor_start) => load_means 那道机械前置门在本包不会被触发。**这不等于门被拆掉**: "
            "任何含起转的包在 load_means 仍 NOT_PROVIDED 时, 执行器依旧在开端口之前具名拒绝整条链"
        % len(plan["stages"])),
        "why_kept_not_deleted": ("这些历史块是上一轮 A 复审已逐字节核对过的登记内容(复审更正、额度口径、"
                                 "验证矩阵、机械前置等)。新包重写它们会丢掉对账线索, 也不在本轮范围; "
                                 "本轮只做**重新绑定 + 加法 + 显式标注**")}
    plan["derived_from"] = {"path": SRC_PLAN.relative_to(REPO).as_posix(),
                            "sha256": sha(SRC_PLAN), "bytes": SRC_PLAN.stat().st_size,
                            "note": "R6C 交付件保持冻结(A 复审 §3 复算过其身份); 本包只在**新路径**上重绑镜像与阶段"}
    plan["executor_reused"] = ("%s(sha256 %s…, 与 R6C 登记同一件: 本轮未改执行器)"
                               % (EXEC_REL, plan["identity_binding"]["executor"]["sha256"][:12]))
    plan["approval"] = {
        "status": "REQUIRES_NEW_APPROVAL", "prior_token": OLD_TOKEN,
        "prior_token_state": ("EXHAUSTED_AND_NOT_CARRIED: 旧批准下 download/readback/explicit_reset 各 1/1 "
                              "已用满, learn 与两次起转额度用尽; 换计划文件/换输出目录都不重新发额度"),
        "hardware_actions_authorized_by_this_package": 0,
        "what_is_still_needed": ("操作者对上面 5 个非零额度逐条批准(尤其 tmc_config_apply: 它会写驱动芯片), "
                                 "并回报现场物理状态(供电/接线/是否允许开 COM5/机械是否静止); "
                                 "本文件不含任何硬件批准, 也不代替操作者回答现场安全门"),
        "why": ("R7 是**候选包准备轮**: 全部 0 设备动作, 只把身份/阶段/额度/判据定稿到可审形态; "
                "批准后仍须由操作者明确发出一次现场执行批准")}
    plan["changes_this_round_r7"] = [
        "① 镜像三件重绑到 5ae4110d…/292164 B(bin/axf/map + manifest target 现算比对), 回读定长随之改变",
        "② 阶段链收成 5 段无运动链: S0 绑定 -> S1 下载 -> S2 定长回读 -> S3 复位+被动启动窗+只读查询 "
        "-> S4 一次 tmc_config_apply 取证 CONFIGURED 短行; 每段都挂 forbidden_commands 明示不许发的命令",
        "③ 短打印判据从**产品源码字面量**取(带 文件:行号), 不抄旧抓包文本; withheld 那条如实标为不在范围",
        "④ 额度逐条给理由, 全为提案; 旧批准不结转; hardware_actions_authorized_by_this_package=0",
        "⑤ 执行器与自检字节与 R6C 相同(本轮不改执行器), 因此 identity_binding.executor 保持 R6C 登记值",
        "⑥ identity_binding.board_image_before 按最新一次逐字节回读更正为 dab356c6…/290916 B "
        "(模板里还是更早一轮的 f5c2fd89…); 旧口径留在 supersedes_in_r6c_template 里对账, R6C 原件不回改",
        "⑦ identity_binding.executor.path 由本机绝对路径改成仓内相对路径(跨机器可核对), "
        "bytes/sha256 不变; 原字符串保留在 path_r6c_template_verbatim",
        "⑧ 继承的历史字段(S0–S11 那一套编号)与本页 S0–S4 会撞号, 新增 "
        "stage_numbering_and_legacy_fields 逐块登记哪些只是历史口径; 派生器加了 tuple 门, "
        "防止赋值行尾逗号把对象静默写成 JSON 数组(motion_budget 上一版就中过)",
        "⑨ 顶层身份字段 package/task_id/status 从上一轮的 'R5-C 续测包' 改成本轮自己的 "
        "phase8-m8-r7-field-package-5ae / R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED; 执行器不读这三条, "
        "旧包不回改"]
    return plan


def dump(doc):
    # 防"尾逗号把 dict 变成 tuple": json.dumps 会把 tuple 静默写成 JSON 数组, 上一版
    # motion_budget 就是这么从对象变成了单元素数组(键的形状也是登记内容的一部分)。
    # 继承下来的**列表**是本来就该是列表的, 所以只拦 tuple —— tuple 在本文件里没有任何用途。
    bad = sorted(k for k, v in doc.items() if isinstance(v, tuple))
    if bad:
        raise SystemExit("R7_DERIVE_REFUSE|这些顶层键算出来是 tuple(多半是赋值行尾多打了个逗号): %s"
                         % ",".join(bad))
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    body = dump(build())
    want = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if not ROWS.exists():
        print("R7_ROWS_MISSING|%s 需要先放本轮 rows.json(见 host/make_rows_r7.py)" % ROWS)
    if a.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8-sig") == body
        print("R7_CHECK|%s|bytes=%d|sha=%s|源仍冻结=%s" %
              ("IDEMPOTENT" if same else "DIFFERS", len(body.encode("utf-8")), want[:16],
               sha(SRC_PLAN) == FROZEN_SRC_SHA))
        return 0 if same else 1
    if OUT.exists() and sha(OUT) != want and not a.force:
        print("R7_REFUSE_WRITE|%s 已存在且字节不同(sha %s), 不静默覆盖" % (OUT.name, sha(OUT)[:12]))
        return 1
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("R7_WRITE|%s|bytes=%d|sha=%s" % (OUT.relative_to(REPO).as_posix(),
                                           len(body.encode("utf-8")), want[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
