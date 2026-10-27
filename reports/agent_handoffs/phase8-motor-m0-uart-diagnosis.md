# M0 通信查因包 CP0 —— 交接（phase8-motor-m0-uart-diagnosis）

- 复审对象 / document_base：`d41e644e…`（本轮开工时本地 HEAD == `ls-remote` 远端头）。
- 一句话结论：**CP0 全绿（33/33 条判据）在本机 Windows PowerShell 5.1 上真跑得到**；CP1/CP2 **未进入**，缺你的本包批准与现场前置回报。
- 状态：`M0_CP0_OFFLINE_DELIVERED_AWAITING_PACKAGE_APPROVAL_AND_SITE_REPORT`。离线自检通过**不是**硬件许可。

## 1. 消费的 GO 与 §2 校正（随本轮登记一次，不开纯登记轮）

- `M1_OFFLINE_SOFTWARE_IMAGE_REVIEW_GO`（对象 `d41e644…`）已消费。
- CAL1..CAL8 逐条**回到源码实读**核验（`cal_source_verification.txt`）：其中 CAL1/CAL2/CAL3/CAL6 四条确实指出我 M1 草案与证据里的错句，本轮按校正口径执行，**不回写 M1 已发布文件**：
  - CAL1：`system_selftest`（`supervisor.c:401-405`）只做 `safety_startup_selftest()` + `system_status()`；`motor_arm()/motor_disarm()` 收尾（`:388-392`）属于 `runtime_selftest`。我 M1 现场包把它写成会尝试 arm/disarm，**是错的**。M2 只用字面 `system_selftest`。
  - CAL2：`tmc_uart_probe` 三次读走文件内 `tmc_read_reg`（`tmc2209.c:951/967/988`），**不经** `tmc2209_read_register:734`→`tmc_health_after_xfer:738` 包装器；只有 `tmc_config_show` 的 live 读会改 `tmc_health`。我上一轮把 probe 也算进"改 health 的读"，按 CAL2 更正。
  - CAL3：`tmc_config_show` 里 `:2331` 是纯门禁查询（`tmc2209_get_write_gate_mask`，注释声明不产生总线流量），实读总线只有 `:2357`/`:2368`；我 source_audit §7 用 `:2331` 替换正确调用链的那句"勘误"**不采用**。
  - CAL6：map 的六列相同只证尺寸与链接内容，不升级为 `.o` 逐字节相同；我 M1 用了"字节一致"的措辞，按此更正口径。
- CAL4/CAL5/CAL7/CAL8 同样成立并已写入执行约束：required ④ 只看返回码不查 IOIN VERSION（`safety_state.c:630-636`，与已登记的 F-A 同一处）；M0 具名失败不通往 M2 写入（脚本 `M0_COMPLETE` 恒带 `safe_for_enable=false`）；提示符结论不外推；旧寄存器读数按历史日期登记，不写"本轮现读回"。

## 2. 交付件与字节身份

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `reports/reviews/phase8-motor-m0-uart-diagnosis/M0Serial.ps1` | 24064 | `2131af0ef32b0d9acb3a2a0489d47b90e26968e2fedd7c4ee4624fede0687e3f` |
| `reports/reviews/phase8-motor-m0-uart-diagnosis/cal_source_verification.txt` | 7405 | `fa4c8171a416ade1daaa57f8b7992b0f2b208b74f3e04092e6c15f45d5d82e44` |
| `reports/reviews/phase8-motor-m0-uart-diagnosis/cp0_offline_selftest.txt` | 11761 | `61c0e853cd73b0eda78b3167bea371e517cf868cad6bc1f5c27f76e9448cfd4f` |
| `reports/reviews/phase8-motor-m0-uart-diagnosis/cp0_selftest_artifacts.md` | 29331 | `0e22e95bb6d44b51b67dedc37ee2b6adbeecdc1830c92c1903891b5883e6b864` |

- 采集器 = 任务书附录 A **标记内围栏正文逐字提取**（未手抄、未转义、未改一字节）：24064 B / `2131af0e…`；ASCII=True、BOM=False、CR=0、LF=315、末尾恰一个 LF=True。
- 启动夹具 = 复用既有原件 3757 B / `97b37f32…`（NUL=3/CR=96/LF=74，未删未重编码），并从 `4bb8e9f5…` 的 `cp3_wire_archive.md` base64 块**独立重取一次交叉核对**，逐字节相同。

## 3. CP0 实跑结果（§4.3 全判据）

- 主机 PowerShell `5.1.26100.5074`；`Parser::ParseFile` errors=0（tokens=3585，逐字取自本机输出）。
- `-SelfTest` 在**全新目录**：rc=0，stdout 82 B / `77b6adad28a9a6939acf04e3ce3601c6a2ae152698da1ec5fe7b4e1cfef54273`，stderr 0 B。
- stdout 逐字 = `M0-OFFLINE-SELFTEST PASS; device_actions=0; mock_commands=7; negative_controls=15`；本机实测 **CR=0**，其原始 SHA 与云端 Core 7.4.13 登记值`77b6adad…` **完全相同** => 任务书"后接 LF"在本机逐字成立。
- 台账事件计数复算：`{"RX_BEGIN": 9, "READ_ONLY_PROBE_READY": 1, "RX_FILE": 9, "MOCK_TX_ATTEMPT": 7, "M0_COMMAND_CHECKED": 7, "NEGATIVE_CONTROL": 15, "OFFLINE_SELFTEST": 1}`；真机事件 SERIAL_OPEN_ATTEMPT/SERIAL_OPEN/TX_ATTEMPT/RESET_ATTEMPT/STOP_FAILURE/M0_COMPLETE **全 0**。
- 九份 RX 长度依次 `[3757, 146, 339, 67, 475, 891, 268, 355, 132]`，逐份 hash、session.rx.bin（6430 B / `393a822a47b1`）== 九份按序拼接、区间起点连续无空洞，全部复算相符。
- 15 条具名负控齐（附录 B 逐项一致）；两条关键串口负控**真被执行**：`missing_response_prompt`（响应 132 B 落盘，拒绝原因是脚本内 `-cne` 精确比较的 `Reply timeout; no retry and no cleanup.`，不是"任意异常当成功"）与 14 条身份/宏/反馈/数值矛盾类全 REJECTED。

## 4. 我自己的缺陷（保留）

- `M0-CP0-F1`：我第一遍判定里**假设**本机 Write-Host 输出 CRLF, 据此写的行尾判据 FAIL; 实测本机 stdout 原始字节 82 B / CR=0, 其 SHA 与云端 Core 登记值相同。。处置：不重跑 PowerShell, 用 run1 已保存的原始字节与产物重新套更正后的判据; run1 全部产物字节与第一遍判定文件原样保留, 更正判定另存 *_rejudged.json。归因：本地判读脚本缺陷, 与被测采集器无关
- `M0-CP0-F2`：归档件隐私扫描第一版把 base64 载荷本身当文本扫, 误报四个私有串(base64 字母表巧合); 另有一处是我把被禁词字面写进了说明行。。处置：改为**解码后的原始字节**扫描(14 块 20225 B 命中 0), 说明行改用类别描述不写字面 token; 两处都在入库前修正。归因：检查器口径缺陷

## 5. 边界与下一步

```text
NO PROGRAM / NO FLASH READ VIA TOOL / NO DAP / NO ACTIVE RESET / NO DEVICE ENUMERATION
NO MOTOR / NO STEP_ENABLE / FOUR ON-BOARD MACROS ZERO / NO FAULT_RESET / NO CONFIG WRITE
NO A0 TRIPS / NO A0 INIT / NO CLEAR_RECORDS / NO AUTO RETRY / NO AUTOMATIC RESTORE
A0_DURATION_EVIDENCE NOT_ACHIEVED / M2_M3 NOT_AUTHORIZED
FINAL_POWER_STATE = UNKNOWN(用户未回报, 不推断)
hardware_actions=[]  target_commands_executed=[]  power_on=0  com5_open_attempt=0
tx=0  probe=0  active_reset=0  dap=0  program=0  tool_flash_read=0  enumeration=0
config_write=0  fault_clear=0  motor_action=0  a0_init/trip/dump/clear=0  selftest_on_target=0
keil=0  主机链重跑=0  a0_export_gate.py 未改未调用  sw1_cut.ps1 未运行(HOLD)
board_last_proven_bin=5f30d3f0…  power_state=UNKNOWN  a0_duration=NOT_ACHIEVED
```

要继续到 CP1（真机上电 + 七条查询），需要你两件事，我不会替你填：
1. 现场前置回报：已先关 24 V、再拔 USB；SW1 在阻断位；电机未接；现场有人；模块/接线未改且无人另行烧录。
2. 本包批准（自然语言同范围即可，无需逐字）：一次常规上电、一次 COM5 打开尝试、按序最多七条查询含一次 `tmc_uart_probe`；失败即停，不增加下载/Flash 读取/DAP/主动复位/配置写入/任何电机动作。

§3 的口径变化已采纳：旧 SERIAL-R1a 的 CP1 批准**不再是 M0 前置，也不再索取**；A0 串口续测本身仍按你的决定暂缓。M2/M3 仍缺两组同实物依据（电机铭牌/额定相电流定义；模块品牌型号与同一模块的 Rsense/VREF），候选镜像 `a76b845d…` 本包绝不下载。
