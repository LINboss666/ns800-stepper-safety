#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7F5A 现场轮归档 + 交接件生成。写盘前硬断言, 断不过就不出件。"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path("D:/ns800")
ROUND = ROOT / "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
FIELD = ROUND / "field/field-20261104T101046Z"
OUT = ROUND / "field_r7f5a"
LAR = ROUND / "la_local_r7f5a"
AR = ROUND / "a_review_r7f5a"
LA = Path("D:/ns800_r7f1_scratch/r7f5a_field")
SPEC_IMG = LA / "la_vendor_spec_page.jpg"

summary = json.loads((OUT / "field_summary.json").read_bytes().decode("utf-8"))
judg = (OUT / "FIELD_JUDGEMENT.txt").read_text(encoding="utf-8")
scope = json.loads((AR / "capture_scope.json").read_bytes().decode("utf-8"))
handoff_d = json.loads((ROUND / "handoff_R7F5A_registered.json").read_bytes().decode("utf-8"))
led = json.loads(Path("C:/m8r_approvals/approval_M8R7F5A-b502-la-two-channel-static-20261104.json")
                 .read_bytes().decode("utf-8"))
old_led = json.loads(Path("C:/m8r_approvals/approval_M8R7F5-b502-blind-irq-static-20261104.json")
                     .read_bytes().decode("utf-8"))
init_meta = json.loads((LA / "init.meta.json").read_bytes().decode("utf-8"))
disp_meta = json.loads((LA / "dispatch.meta.json").read_bytes().decode("utf-8"))

assert summary["fails"] == 0 and summary["checks"] == 27
assert summary["device_actions"]["arm"] == 0 and summary["device_actions"]["start"] == 0
assert summary["device_actions"]["logic_capture_session"] == 1
assert led["spent"] == {"irq_polarity_prep": 2, "port_open": 1, "safety_irq_disable": 1,
                        "tmc_config_apply": 1} or all(
    v == ({"irq_polarity_prep": 2, "port_open": 1, "safety_irq_disable": 1, "tmc_config_apply": 1}.get(k, 0))
    for k, v in led["spent"].items()), led["spent"]
assert not {k: v for k, v in led["spent"].items()
            if v and k not in ("irq_polarity_prep", "port_open", "safety_irq_disable", "tmc_config_apply")}
assert led["pending"] == []
assert not {k: v for k, v in old_led["spent"].items() if v}, "旧盲测账必须仍未被消耗"
assert handoff_d["checks_fails"] == 0
assert "TOTAL=27 FAILS=0" in judg

# ---------- 归档仪器件(序列号掩码) ----------
OUT.mkdir(parents=True, exist_ok=True)
shutil.copyfile(LA / "csv" / "digital.csv", OUT / "la_digital_raw.csv")
shutil.copyfile(LA / "native" / "capture_id8.sal", OUT / "la_capture_id8.sal")
for f in ("la_start.json", "la_stop.json", "la_export.json", "la_save.json",
          "la_save_first_attempt_error.json"):
    shutil.copyfile(LA / f, OUT / f)
for f in ("init.meta.json", "init.stdout.txt", "dispatch.meta.json", "dispatch.stdout.txt"):
    shutil.copyfile(LA / f, OUT / f)
dev = json.loads((LA / "la_devices.json").read_bytes().decode("utf-8"))
ser = dev["response"]["result"]["structuredContent"]["devices"][0]["deviceId"]
dev["response"]["result"]["structuredContent"]["devices"][0]["deviceId"] = \
    "MASKED_SHA12:" + hashlib.sha256(ser.encode()).hexdigest()[:12]
dev["response"]["result"]["content"][0]["text"] = json.dumps(
    {"devices": [{"deviceId": "MASKED", "deviceType": "Logic", "isSimulation": False}]})
dev["masking_note"] = "分析仪序列号不入公开仓库; 原文留在仓外 la_devices 记录"
(OUT / "la_devices_masked.json").write_bytes((json.dumps(dev, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
assert ser not in (OUT / "la_devices_masked.json").read_text(encoding="utf-8")

# la_start.json 的 args.deviceId 同样带序列号原文: 逐字段掩码并写明改动了什么。
st = json.loads((OUT / "la_start.json").read_bytes().decode("utf-8"))
st["args"]["deviceId"] = "MASKED_SHA12:" + hashlib.sha256(ser.encode()).hexdigest()[:12]
st["masking_note"] = ("本件由仓外 la_start.json 复制而来, 唯一改动是把 args.deviceId 换成 sha256 前 12 位; "
                      "响应体与 UTC 时刻未动。原文仍在仓外 D:/ns800_r7f1_scratch/r7f5a_field/la_start.json")
(OUT / "la_start.json").write_bytes((json.dumps(st, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
leaked = [p.name for p in OUT.iterdir() if p.is_file() and ser.encode() in p.read_bytes()]
assert not leaked, ("分析仪序列号仍出现在", leaked)

spec = json.loads((LA / "la_start.json").read_bytes().decode("utf-8"))
LAR.mkdir(parents=True, exist_ok=True)
(LAR / "vendor_spec_transcription.txt").write_bytes((
    "逻辑分析仪输入范围资料(操作者提供的商家详情页转录, 2026-11-04)\n"
    "  证据等级: 商家页声称 + 两项实测特征吻合(8 路数字通道; 数字采样率上限恰为 24000000)\n"
    "  未升级为数据手册确认, 也不是本机实测耐压。A 补充修订第 1 条接受它作为本次 3.3 V 逻辑采集的依据。\n"
    "\n"
    "转录内容:\n"
    "  最大采样速度: 24Msps\n"
    "  输入电压范围: 0~5V\n"
    "  数字通道: 8\n"
    "  输入低电平: <0.8V\n"
    "  输入高电平: >1.4V\n"
    "  支持协议: SPI,IIC,UART,SMBus,I2S,CAN,Parallel,Custom,Search,Async,1-Wire,PS/2\n"
    "\n"
    "对上被测信号: DIAG(PA.3) 与 ENN(PC.6) 均为 3.3 V 逻辑 => 在 0~5 V 内; 3.3V>1.4V 判高, 0V<0.8V 判低。\n"
    "约束: 探头不得接 VM(24 V) 或电机相输出; 上限只有 5 V。\n"
    "原件截图不入公开仓库(画面含账号级界面), 只登记字节与散列:\n"
    "  bytes=%d sha256=%s\n"
    "  原件位置(仓外): D:/ns800_r7f1_scratch/r7f5a_field/la_vendor_spec_page.jpg\n"
    % (SPEC_IMG.stat().st_size, hashlib.sha256(SPEC_IMG.read_bytes()).hexdigest())).encode("utf-8"))

(SPEC_IMG.parent / "PROVENANCE_DO_NOT_COMMIT_IMAGE.txt").write_bytes((
    "这张 jpg 是操作者手机拍的商品页, 画面含购物车/会员券等账号级界面, 不入公开仓库。\n"
    "登记的是转录文本 + 原件字节数与 sha256(见 la_local_r7f5a/vendor_spec_transcription.txt)。\n"
).encode("utf-8"))

# ---------- 交接 MD ----------
md = []
w = md.append
w("# R7F5A 现场执行回执：一次 8 路静态采集 + 原 16 步串口链（两层分报，不拼全过）\n")
w("执行根 `field/field-20261104T101046Z/`；批准身份 `%s`；令牌取 `capture_scope.json` 的 "
  "`operator_authorization_verbatim` 逐字节（%d B，`sha256=%s…`），未拼接其他句子。\n"
  % (scope["task_id"], init_meta["token_bytes"], init_meta["token_sha256"][:16]))
w("## 1. 串口层：`SERIAL_16_STEP_STATIC_IRQ_TRIAL_OK`\n")
w("- `DISPATCH_SUMMARY status=OK`、`stages_executed=2`、16 步全部有响应原文；实耗 "
  "`port_open 1 / tmc_config_apply 1 / irq_polarity_prep 2 / safety_irq_disable 1`，其余 18 项 0，无重试。\n")
w("- 门链按字节复现：`arm gate fail mask` `0x18`(前置)→`0x8`(配置后)→`0x0`(开 IRQ 后)→`0x8`(关 IRQ 后)；"
  "`irq gate` CLOSED→OPEN→CLOSED；`polarity` NOT confirmed→operator CONFIRMED；"
  "`protection_ready` NO→YES→NO。**这些是软件状态位，不是物理结论。**\n")
w("- 固件自报（同一场原文）：`TMC_DIAG pin=3 raw=0 safe=0 irq=RISING [safe]`、"
  "`PC.6(ENN_FB) pin=ok raw=HIGH = TMC_ENN feedback (HIGH=disabled side)`；全程无 `armed=1`。\n")
w("- 实际发送的命令由响应首行回显取证，15 条与计划同序（第 10 步是被动观察，无请求），"
  "其中**没有** `motor_arm`/`motor_start`；阶段台账本身不写 `cmd` 字段，所以命令身份不靠台账标签。\n")
w("- 16 份响应零裸 NUL、严格 UTF-8；会话末 `t_s=7.344 s` < 申报 90 s。\n")
w("- 第 10 步被动观察 `10_quiet_observation.rx.bin` 为 0 字节：只说明窗口内无产出，"
  "**不构成「没触发」的证明**（被动零字节不算证据）。\n")

w("## 2. 仪器层：`CAPTURE_VALID_BUT_NON_DISCRIMINATING`\n")
w("- 一次采集，`captureId=8`，D0–D7 全开、1 MS/s、手动模式、请求缓冲 512 MB（接口只回 `captureId`，"
  "**不回实际接受的缓冲值**，所以按 A 补充第 3 条改由事后覆盖性验证承担）。"
  "原生件 `la_capture_id8.sal` %d B `sha256=%s…`；数字 CSV `la_digital_raw.csv` %d B `sha256=%s…`。\n"
  % (summary["la"]["native_bytes"], summary["la"]["native_sha256"][:16],
     summary["la"]["csv_bytes"], summary["la"]["csv_sha256"][:16]))
w("- 采集窗 %s → %s，跨 %.4f s；8 路**全程零跳变**（每路 initial=final，上升 0、下降 0）。\n"
  % (summary["la"]["start_utc"][:26], summary["la"]["stop_utc"][:26], summary["la"]["span_s"]))
w("- **决定性一条**：本轮电平模式 `0,1,1,1,1,1,1,1` 与探头未接时的浮空基线**逐列相同**。"
  "所以这批字节既与「接线正确且全程静默」一致，也与「探头未接触/无共地」一致——"
  "**电平层无法区分这两者**，仪器判据不具区分力。\n")
w("- 通道映射未由现场读得：`CHANNEL_MAPPING_UNRESOLVED`。按 A 补充第 2 条，"
  "不靠「预期一低一高」反推哪一路是 DIAG/ENN；固件自报的 `raw=0`/`raw=HIGH` 只到软件读数层，"
  "不能与某一列绑定成「该列即 DIAG」。\n")
w("- 保存环节自伤如实登记：第一次 `save_capture` 我传了 `directory`，接口具名拒绝 "
  "`should have required property 'filepath'`（原文 `la_save_first_attempt_error.json` 未删）；"
  "改用 `filepath` 后对**同一次已采完的会话**重存成功——这是采集之后的保存动作，不是重采。\n")

w("## 3. 对齐与收口\n")
w("- 采集起 `10:10:37.465` 早于首个响应写入 `10:10:46.819`；末响应写入 `10:10:53.945` "
  "早于采集止 `10:11:05.482` ⇒ 采集窗完整包住串口会话，两端各有约 9 s 与 11 s 余量。\n")
w("- `stop_capture` 在收口断电之前完成 ⇒ 断电尾段不在这 28.02 s 内，按任务书第 69 行单独标识，"
  "不混入稳定供电的值判定。\n")
w("- **待操作者回报**：收口切断 VM+MCU 的时刻与观察到的掉电表现（`PENDING_OPERATOR_REPORT`）。"
  "我不代填。\n")

w("## 4. A 补充修订五条的落实\n")
w("| 条 | 落实 |\n| --- | --- |\n")
w("| 1 输入范围 | 按商家页 0–5 V 接受，等级锁在「厂商页声称＋实测特征吻合」，未升级成手册/实测耐压 |\n")
w("| 2 通道映射 | 8 路全开采集；映射未核清即标 `CHANNEL_MAPPING_UNRESOLVED`，未反推身份；"
  "CSV 列号导出后读取（实测导出给全部 8 列） |\n")
w("| 3 缓冲 | 1 MS/s＋显式 512 MB；本机物理内存 33.3 GB、空闲 14.4 GB；接口不回实际值，"
  "改由覆盖性事后验证（见 §3）；MB 未等同样点数 |\n")
w("| 4 供断电 | 执行前的断电→上电登记为前置供电调整；结束断电为允许的收口动作，待回报；"
  "未因此再启动或复测 |\n")
w("| 5 令牌 | 取 `capture_scope` 原文，未拼接、未再询问 |\n")

w("## 5. 边界（不变）\n")
w("- `polarity_measured=false`、`core_protection_verified=false` 保持；四条禁用声称仍全部禁止："
  "`PHYSICAL_POLARITY_VERIFIED` / `DIAG_TRIGGER_VERIFIED` / `STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE` / "
  "`CORE_PROTECTION_VERIFIED`。\n")
w("- 本轮**没有**取得：报警态与物理极性两态、中断是否真被投递、实际触发与恢复、电压数值（无 DMM）、"
  "µs 级 DIAG→IRQ 延迟（无共同硬件时基）、SG/ADC/IMU、arm/disarm 与运动、带载最坏栈。\n")
w("- 全过名称 `STATIC_IRQ_TRIAL_WITH_DIAG_ENN_CAPTURE_PASS` **不使用**：串口层成立、仪器层不具区分力，"
  "按任务书第 82 行分报。\n")

w("## 6. 旧盲测账未被动用\n")
w("`M8R7F5-b502-blind-irq-static-20261104` 仍 `spent` 全 0、`pending` 空；按任务书第 25 行它已被新身份替代，"
  "我不会再拿它跑一遍旧盲测。\n")

w("## 7. 复算入口\n")
w("```\npython reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5a_field_judgement.py\n"
  "python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5a_field_archive.py\n```\n")

md_text = "".join(md)
(ROUND / "handoff_R7F5A_field.md").write_bytes(md_text.encode("utf-8"))

data = {
    "round": "M8R7F5A-field", "verdict_split": {
        "serial": summary["serial_verdict"], "instrument": summary["instrument_verdict"],
        "combined": summary["combined_verdict"]},
    "approval": {"id": scope["task_id"], "token_bytes": init_meta["token_bytes"],
                 "token_sha256": init_meta["token_sha256"],
                 "token_source": "capture_scope.operator_authorization_verbatim (逐字节)",
                 "ledger_spent": {k: v for k, v in led["spent"].items() if v},
                 "ledger_pending": led["pending"],
                 "old_blind_ledger_still_unspent": not {k: v for k, v in old_led["spent"].items() if v}},
    "execution_root": "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/field-20261104T101046Z",
    "judgement": {"checks": summary["checks"], "fails": summary["fails"],
                  "path": "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field_r7f5a/FIELD_JUDGEMENT.txt"},
    "serial_facts": {"steps": 16, "responses": summary["session"]["responses"],
                     "mask_chain": ["0x18", "0x8", "0x0", "0x8"],
                     "irq_gate": ["CLOSED", "OPEN", "CLOSED"],
                     "firmware_self_report": {"TMC_DIAG": "pin=3 raw=0 safe=0 irq=RISING [safe]",
                                              "ENN_FB": "PC.6(ENN_FB) pin=ok raw=HIGH"},
                     "arm_or_start_sent": False, "armed_one": False,
                     "session_t_s": 7.344, "declared_deadline_s": 90,
                     "passive_watch_bytes": 0},
    "instrument_facts": summary["la"],
    "instrument_caveat": "电平模式与探头未接的浮空基线逐列相同 => 无法区分'接线正确且静默'与'未接触/无共地'",
    "channel_mapping": summary["channel_mapping"],
    "self_error_registered": "save_capture 首次参数用错(directory 而非 filepath)的具名拒绝原文留档; "
                             "对同一已采会话重存, 非重采",
    "alignment": {"capture_start": summary["la"]["start_utc"], "first_rx_write": summary["session"]["first_rx_utc"],
                  "last_rx_write": summary["session"]["last_rx_utc"], "capture_stop": summary["la"]["stop_utc"]},
    "pending": summary["pending"],
    "boundaries": {"polarity_measured": False, "core_protection_verified": False,
                   "forbidden_claims_still_blocked": summary["forbidden_claims_still_blocked"],
                   "full_pass_name_used": False},
    "device_actions": summary["device_actions"],
    "unchanged_inputs": {"plan_sha256": handoff_d["offline_checks"]["steps_digest"] and
                         "ee3b26513fc7a572daff0c5c26124a9a0f7fbfa20632bd7088fb84d9d0ca4402",
                         "executor_sha256": "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca",
                         "image_sha256": "502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a",
                         "product_source_changed": False, "executor_changed": False},
    "supplement_by_A": "5 条, 落实见 handoff §4; 原文 a_review_r7f5a/SUPPLEMENT_A_R7F5A.md",
}
assert isinstance(data, dict) and data["judgement"]["fails"] == 0
assert data["serial_facts"]["arm_or_start_sent"] is False
(ROUND / "handoff_R7F5A_field.json").write_bytes(
    (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print(json.dumps({"md_bytes": (ROUND / "handoff_R7F5A_field.md").stat().st_size,
                  "json_bytes": (ROUND / "handoff_R7F5A_field.json").stat().st_size,
                  "archived": sorted(p.name for p in OUT.iterdir()),
                  "serial": data["verdict_split"]["serial"],
                  "instrument": data["verdict_split"]["instrument"]}, ensure_ascii=False, indent=2))
