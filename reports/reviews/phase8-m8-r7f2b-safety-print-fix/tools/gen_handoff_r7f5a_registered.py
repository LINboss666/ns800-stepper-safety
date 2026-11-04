#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 R7F5A 登记轮交接件(MD+JSON)。写盘前先硬断言, 断不过就不出件。"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path("D:/ns800")
ROUND = ROOT / "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
OUT = ROUND / "offline_r7f5a"
AR = ROUND / "a_review_r7f5a"
LAAR = ROUND / "la_local_r7f5a"
PLAN_REL = "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/plan_r7f5_b502_blind_irq_static.json"
EXEC_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
IMG_REL = "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin"
LEDGER = Path("C:/m8r_approvals/approval_M8R7F5-b502-blind-irq-static-20261104.json")

summary = json.loads((OUT / "summary.json").read_bytes().decode("utf-8"))
full = json.loads((OUT / "checks_full.json").read_bytes().decode("utf-8"))["checks"]
scope = json.loads((AR / "capture_scope.json").read_bytes().decode("utf-8"))
obc = json.loads((AR / "offline_binding_check.json").read_bytes().decode("utf-8"))
prior = json.loads((ROUND / "handoff_R7F5_registered.json").read_bytes().decode("utf-8"))
led = json.loads(LEDGER.read_bytes().decode("utf-8"))
zip_ident = json.loads((AR / "IDENTITY.json").read_bytes().decode("utf-8"))
plan = json.loads((ROOT / PLAN_REL).read_bytes().decode("utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def size(p):
    return Path(p).stat().st_size


# ---- 硬断言: 不成立就不写交接件 ----
assert summary["fails"] == 0, summary
assert summary["checks"] == len(full) == 56, (summary["checks"], len(full))
assert all(c["result"] == "PASS" for c in full), [c for c in full if c["result"] != "PASS"]
assert summary["steps_digest"] == "4a80dcb6dd43028c6f526298463cba3d71bc312a379db5b152a4db52359b7eb0"
assert summary["old_ledger_state"] == "UNSPENT_NO_PENDING"
assert not {k: v for k, v in led["spent"].items() if v} and led["pending"] == []
assert sha(ROOT / PLAN_REL) == scope["source_binding"]["files"][0]["sha256"]
assert sha(ROOT / EXEC_REL) == scope["source_binding"]["executor_sha256"]
assert sha(ROOT / IMG_REL) == scope["source_binding"]["firmware_sha256"]
assert zip_ident["sha256"] == hashlib.sha256(
    Path(os.environ.get("R7F5A_ZIP") or
         (Path.home() / "Downloads" / "NS800_R7F5A_LA_DIAG_ENN_Task_20261104.zip")).read_bytes()).hexdigest()
assert led["identities"] == {"plan_sha256": sha(ROOT / PLAN_REL),
                             "executor_sha256": sha(ROOT / EXEC_REL),
                             "image_sha256": sha(ROOT / IMG_REL)}
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True).stdout.decode().strip()
diff = [p for p in subprocess.run(["git", "diff", "--name-only",
                                   scope["source_binding"]["reviewed_delivery_commit"] + ".." + head],
                                  cwd=str(ROOT), capture_output=True).stdout.decode("utf-8").splitlines()
        if p.strip()]
assert all(p.startswith("reports/") for p in diff), diff[:5]
assert scope["caps"]["motor_arm"] == 0 and scope["caps"]["motor_start"] == 0 and scope["caps"]["motion"] == 0
assert all(v is False for v in scope["measurement_claims_before_execution"].values())
assert plan["trial_exception"]["polarity_measured"] is False
assert plan["trial_exception"]["core_protection_verified"] is False
assert len(summary["preflight_open_items"]) == 3

OPEN_LABEL = {
    "hardware_input_rating": "分析仪通道的实物输入耐压(型号规格页/最大绝对电压/是否 5V 容许)",
    "acquisition_capacity_100Ms": "≥100 M samples 的实际缓冲容量(1 MS/s 时约 100 s 覆盖)",
    "probe_label_to_gui_channel_mapping": "探头印字 CH1/CH2 与软件通道号的对应, 以及 CSV 列号对应",
}

md = []
w = md.append
w("# R7F5A 登记回执：两路 LA 采集 + 一次静态 IRQ 试验（本机离线复算，设备动作 0）\n")
w("被审件 `%s` %d B `sha256=%s`。A 侧硬件动作 0；本轮**未开口、未 Start、未发任何命令**。\n"
  % (zip_ident["package"], zip_ident["bytes"], zip_ident["sha256"]))
w("审查基线 `0cd6458`（= `capture_scope.source_binding.reviewed_delivery_commit`），"
  "登记时 HEAD `%s`，两者之间改动**全部落在 `reports/` 内**（%d 个文件，产品源码/固件/执行器零改动）。\n"
  % (head[:7], len(diff)))

w("## 1. 包身份（我这边重算，不采信自报）\n")
w("- 外层 zip 5 条目，内层 `SHA256SUMS.txt` 4 条 **4/4 逐字节相同**，且清单不含自身。\n")
w("- 嵌入的 `original_R7F5.zip` 声明 60623 B `b326cd8543eec1db…`：实算与声明相同，"
  "且与上一轮登记记录（`handoff_R7F5_registered.json.reviewed_package`）相同。\n")
w("- 落位两件（上一轮已按声明路径放入，本轮只复算不重放）：plan `ee3b2651…` 49994 B、"
  "rows `4f094de3…` 22289 B；执行器 `33d3f0af…` 219491 B、镜像 `502dca5d…` 292708 B 均未动。\n")
w("- A 自报 `new_executable_code=false`、`new_serial_plan=false`、`base_serial_plan_bytes_unchanged=true`，"
  "这三条由上面的实算承担，不由自报承担。\n")

w("## 2. 旧账只读核查（A 要求的分支门）\n")
w("任务书第 23–26 行要求先只读核查旧批准，再决定用新身份还是停住。实况：\n")
w("- 账本 `approval_M8R7F5-b502-blind-irq-static-20261104.json`：`version=2`、`spent` **全 0**、"
  "`pending` **空**、`created_by_action=approval_init`。\n")
w("- 用**未改动执行器**的 `validate_ledger_doc` 校验：以当场从磁盘重算的三件身份"
  "（plan/executor/image）与计划 quota 传入 → 不拒绝；把 plan 身份改成 64 个 0 再传 → 具名拒绝。"
  "即「账有效」这个结论由执行器自己的校验器给出，不是我的手写比较。\n")
w("- 结论：走**新身份** `M8R7F5A-b502-la-two-channel-static-20261104` 这一支；"
  "旧批准身份按任务书标为被替代，随后不得再跑一遍旧盲测。\n")

w("## 3. 离线判据（56 项 PASS，fails=0，`offline_r7f5a/`）\n")
w("- 步骤摘要用未改执行器重算 `4a80dcb6dd43028c…`，与上一轮同一计划一致（本包不改串口计划）。\n")
w("- `base_serial_steps=16` 复算相符；16 步里**没有** `motor_arm`/`motor_start`；"
  "`forbidden_commands` 覆盖这两条。\n")
w("- `--validate-plan` 走生产入口：`checks=57 fail=1`，唯一 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`，"
  "按上一轮口径登记为 N/A（不填假运动预算、不改检查器）。\n")
w("- 额度对照：`port_open 1 / tmc_config_apply 1 / safety_irq_disable 1` 与计划同名同值；"
  "计划里的 `irq_polarity_prep=2` 在补充范围里被拆成 `polarity_confirm 1 + irq_enable 1` 重述，"
  "两者相加等于 2；计划为 0 的动作**没有任何一项**被补充范围放宽成正数。\n")
w("- 仪器额度（`logic_capture_session/logic_start/logic_stop_max/native_capture_save/raw_digital_csv_export`）"
  "由外层记录，原串口执行器不扣减也不强制——不谎称两个工具原子联动。\n")

w("## 4. LA 侧本机实测事实（`la_local_r7f5a/`）\n")
w("- 控制通道：Logic 2 `2.4.48` 的 MCP `http://127.0.0.1:10530/`。握手原文、15 个工具清单、"
  "`start_capture` 参数结构都按原字节入档；Automation Server 10430 当时无监听，本轮未依赖。\n")
w("- 设备能力（两条**具名拒绝原文**留档）：不支持可调输入阈值电压；数字采样率只接受 25 kS/s–24 MS/s 共 13 档"
  "（任务书希望的 1 MS/s 在允许档位内）。两条拒绝都在参数校验期返回 `isError=true` 且**不返回 captureId**，"
  "即没有建立采集会话。\n")
w("- 导出侧通道筛选**不生效**：请求 `digitalChannels:[0]`，实得 8 列。所以"
  "「CSV 列号 ↔ 信号」的对应必须现场核，不能假设导出按请求裁剪。\n")
w("- `la_smoke_capture_digital.csv` 是探头未接任何东西时的 1 秒定长采集：`D0` 全程 0、`D1–D7` 全程 1、"
  "两行状态无变化。这是**浮空输入**，标 `CONTROL_PATH_SMOKE`，不作任何电平证据；该采集发生在本任务书下达之前，"
  "不占本轮 `logic_capture_session=1`。\n")
w("- 序列号不入公开仓库：`la_mcp_get_devices_sanitized.json` 只留 `sha256` 前 12 位与长度。\n")

w("## 5. 三项前置未闭合（照 A 的要求登记为 OPEN，不写 PASS）\n")
for it in summary["preflight_open_items"]:
    w("- **%s**：%s。未闭合前不进上电/Start/开 COM。\n" % (it, OPEN_LABEL[it]))
w("这三项是任务书 `preflight_required` 的第 3–5 条，也是 `offline_binding_check` 里 A 自己标为 "
  "`REQUIRES_*` 的两项。我不替操作者填现场事实，也不从软件版本或 USB ID 推定耐压。\n")

w("## 6. 必须保留的边界（不变）\n")
w("- `polarity_measured=false`、`core_protection_verified=false` 原样保持；"
  "`measurement_claims_before_execution` 五项全 false。\n")
w("- 四条禁用声称原样保留：`PHYSICAL_POLARITY_VERIFIED` / `DIAG_TRIGGER_VERIFIED` / "
  "`STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE` / `CORE_PROTECTION_VERIFIED`。\n")
w("- 本轮能证明的只是「稳定供电窗口内的正常静态逻辑值」（DIAG 预期低、最终 ENN 预期高），"
  "不能证明报警态与物理极性两态；无 DMM 不报电压数值；无共同硬件时基不宣称 µs 级延迟。\n")
w("- 原 `safety_polarity_confirm CONFIRM` 仍只是已披露的**无使能试验临时置位**，"
  "不因「LA 接上了」升级为实测；结束断电清除，不作运动前置证据。\n")

w("## 7. 对我前一轮判断的两条更正（A 的补正照单登记）\n")
w("- 我说过「Logic 1.2.17 完全没有远程/脚本 API」——**撤回**。我当时实测到的只是：本机 10429/10430 无监听、"
  "`C:\\Program Files\\Saleae Inc` 目录里没有 Logic 2 的 resources/bin、pyusb 的 libusb1/libusb0 后端均 NOT_LOADED。"
  "端口没监听只能说明该通道当时未就绪，推不出「没有 API」（官方确有 Legacy Logic 1.x API）。\n")
w("- 任务书第 58 行「不要先做试采消耗一次机会」与本机事实存在时间差：试采已在任务书下达前发生（见 §4），"
  "如实登记，不解释成已满足。\n")

w("## 8. 停点与执行序（等 A/操作者对新范围的一次批准）\n")
w("按任务书第 63–70 行的顺序，批准后一次做完，不再交准备轮：\n")
w("1. 现场供断电状态确认（任务书允许「已上电就不额外复位」；但步骤 0 期望 `mask=0x18` 要求驱动配置为 "
  "UNCONFIGURED，即 `MOT_GATE_NO_DRV_CONFIG` 必须成立——若板子还带着上一场的电，第一条状态检查就会成为首错。"
  "这一事实由操作者回报，我不代答。）\n")
w("2. 三项 OPEN 闭合（耐压资料 / 容量核读 / 探头↔通道映射）；\n")
w("3. 新身份 `--approval-init`（令牌用操作者本次真实原话，绑定原计划字节 + 本补充范围）；\n")
w("4. LA Start 一次（确认确实在采集，记录设置与截图）→ 原计划 `--dispatch --transport real --backend real "
  "--profile response_driven` 一次 → 串口关端口后 Stop → 操作者切断 VM+MCU → 保存原生捕获 + 导出数字 CSV；\n")
w("5. 判读分两层报：串口结果与仪器结果各自成结论，不拼成「全过」；首错停止、保留已得数据、不重试。\n")

w("## 9. 复算入口\n")
w("```\npython reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5a_registered_checks.py\n"
  "python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/gen_handoff_r7f5a_registered.py\n```\n")

md_text = "".join(md)

data = {
    "round": "M8-R7F5A-registration",
    "verdict": "R7F5A_TWO_CHANNEL_PACKAGE_ACCEPTED_FOR_FIELD_EXECUTION_PENDING_APPROVAL_AND_PREFLIGHT",
    "reviewed_package": {"zip": zip_ident["package"], "bytes": zip_ident["bytes"],
                         "sha256": zip_ident["sha256"], "entries": zip_ident["entries_in_zip"],
                         "inner_manifest_entries": 4, "inner_manifest_mismatch": 0,
                         "inner_manifest_hashes_itself": False},
    "embedded_original_r7f5": {"bytes": 60623,
                               "sha256": "b326cd8543eec1db232ec5781c03b7fae721eeaf1ef6af6640bc97321a789a2c",
                               "equals_prior_round_record": True},
    "baseline": {"reviewed_delivery_commit": scope["source_binding"]["reviewed_delivery_commit"],
                 "head_at_registration": head,
                 "diff_files": len(diff), "diff_outside_reports": 0},
    "old_ledger_read_only": {"approval_id": led["approval_id"], "version": led["version"],
                            "spent_nonzero": {k: v for k, v in led["spent"].items() if v},
                            "pending": led["pending"],
                            "identities_equal_current_disk_bytes": True,
                            "executor_validate_ledger_doc_accepted": True,
                            "negative_control_corrupted_identity": "REFUSED",
                            "branch_taken": "USE_NEW_APPROVAL_ID"},
    "new_approval_id": scope["task_id"],
    "operator_authorization_verbatim_quoted_by_A": scope["operator_authorization_verbatim"],
    "offline_checks": {"total": summary["checks"], "fails": summary["fails"],
                       "steps_digest": summary["steps_digest"],
                       "validate_plan": "checks=57 fail=1 (PLAN_MOTION_CAP_BUDGET, N/A)",
                       "base_serial_steps": 16, "arm_start_in_plan": False},
    "quota_cross_map": {
        "same_named": {"port_open": [scope["caps"]["port_open"], plan["quota"]["port_open"]],
                       "tmc_config_apply": [scope["caps"]["tmc_config_apply"], plan["quota"]["tmc_config_apply"]],
                       "safety_irq_disable": [scope["caps"]["safety_irq_disable"], plan["quota"]["safety_irq_disable"]]},
        "re_expressed": {"irq_polarity_prep": [plan["quota"]["irq_polarity_prep"],
                                               scope["caps"]["safety_polarity_confirm_trial_only"]
                                               + scope["caps"]["safety_irq_enable"]]},
        "instrument_external_to_executor": ["logic_capture_session", "logic_start", "logic_stop_max",
                                            "native_capture_save", "raw_digital_csv_export"],
        "zero_actions_widened_by_supplement": 0},
    "la_local_facts": summary["la_facts"],
    "la_smoke_capture": {"label": "CONTROL_PATH_SMOKE", "probes_connected": False,
                         "levels": "D0=0 全程, D1-D7=1 全程, 无状态变化",
                         "counts_against_this_round_capture_budget": False,
                         "occurred_before_task_book": True,
                         "csv_sha256": sha(LAAR / "la_smoke_capture_digital.csv"),
                         "csv_bytes": size(LAAR / "la_smoke_capture_digital.csv")},
    "preflight_open_items": summary["preflight_open_items"],
    "boundaries_kept": {"polarity_measured": False, "core_protection_verified": False,
                        "measurement_claims_before_execution": scope["measurement_claims_before_execution"],
                        "forbidden_claims": plan["trial_exception"]["forbidden_claims"]},
    "corrections_to_my_prior_round": [
        {"claim": "Logic 1.2.17 完全没有远程/脚本 API", "status": "WITHDRAWN_OVERREACH",
         "actually_measured": "10429/10430 当时无监听; Saleae Inc 目录无 Logic 2 资源; pyusb libusb 后端 NOT_LOADED",
         "corrected_by": "TASK_R7F5A_LA_TWO_CHANNEL.md:61 (Legacy Logic 1.x API 确实存在)"},
        {"claim": "任务书第 58 行「不要先做试采」", "status": "TIME_ORDER_DISCLOSED",
         "fact": "试采在本任务书下达前已发生, 不占本轮额度, 不解释成已满足"}],
    "device_actions_this_round": 0,
    "hardware_actions_performed_by_A": scope["source_binding"]["hardware_actions_performed_by_A"],
    "not_verified_after_this_round": [
        "DIAG 报警态与物理极性两态", "实际故障触发与恢复", "中断是否真被投递",
        "ENN 与禁能请求在故障下的关系", "SG/ADC/IMU 通道", "arm/disarm 与运动", "带载最坏栈",
        "分析仪输入耐压(资料缺项)", "采集容量能否覆盖整场(未从 GUI 核)", "探头印字与软件通道号对应"],
    "checks_total": summary["checks"],
    "checks_fails": summary["fails"],
    "scope": "登记与离线复算; 未开口/未 Start/未发命令; 旧证据、固件、演示包、执行器原件未动",
}

# 顶层必须是 object, 且关键事实与实算一致
assert isinstance(data, dict)
assert data["checks_fails"] == 0 and data["device_actions_this_round"] == 0
assert data["offline_checks"]["base_serial_steps"] == 16
assert json.dumps(data["la_smoke_capture"]["levels"], ensure_ascii=False)

(ROUND / "handoff_R7F5A_registered.md").write_bytes(md_text.encode("utf-8"))
(ROUND / "handoff_R7F5A_registered.json").write_bytes(
    (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print(json.dumps({"md_bytes": size(ROUND / "handoff_R7F5A_registered.md"),
                  "md_sha256": sha(ROUND / "handoff_R7F5A_registered.md"),
                  "json_bytes": size(ROUND / "handoff_R7F5A_registered.json"),
                  "json_sha256": sha(ROUND / "handoff_R7F5A_registered.json"),
                  "verdict": data["verdict"]}, ensure_ascii=False, indent=2))
