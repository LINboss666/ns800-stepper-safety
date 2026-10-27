# M0 通信查因包 CP0+CP1 —— 交接（phase8-motor-m0-uart-diagnosis）

- 复审对象 / document_base：`d41e644e…`（本轮开工时本地 HEAD == `ls-remote` 远端头）。
- 一句话结论：CP0 全绿（33/33 条判据，本机 Windows PowerShell 5.1 真跑）之后，CP1 已按批准执行一次会话：**七条只读查询全部发出，前六条被接受，第 7 条 `tmc_uart_probe` 命中具名失败并停止**；只剩 CP2 的断电回报未到手。
- 状态：`M0_UART_DIAGNOSIS_EXECUTED_AND_STOPPED_AT_NAMED_FAILURE / AWAITING_INDEPENDENT_TARGET_EVIDENCE_REVIEW`。离线自检通过不是硬件许可；本轮硬件动作只发生在被批准的那一次会话里。

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
| `reports/reviews/phase8-motor-m0-uart-diagnosis/cp1_target_fault_analysis.txt` | 13733 | `f732102af0d4a2e6558faddb21f08958d25697d23981f578fd600e9751236b17` |
| `reports/reviews/phase8-motor-m0-uart-diagnosis/cp1_onboard_capture.md` | 22118 | `36644cc6716495532f525297f1feb868bdf4590ef33ebd0573cf85545fe3cb30` |

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

- `M0-CP0-F1`：我第一遍判定里**假设**本机 Write-Host 输出 CRLF, 据此写的行尾判据 FAIL; 实测本机 stdout 原始字节 82 B / CR=0, 其 SHA 与云端 Core 登记值相同。处置：不重跑 PowerShell, 用 run1 已保存的原始字节与产物重新套更正后的判据; run1 全部产物字节与第一遍判定文件原样保留, 更正判定另存 *_rejudged.json。归因：本地判读脚本缺陷, 与被测采集器无关
- `M0-CP0-F2`：归档件隐私扫描第一版把 base64 载荷本身当文本扫, 误报四个私有串(base64 字母表巧合); 另有一处是我把被禁词字面写进了说明行。处置：改为**解码后的原始字节**扫描(14 块 20225 B 命中 0), 说明行改用类别描述不写字面 token; 两处都在入库前修正。归因：检查器口径缺陷
- `M0-CP1-F1`：CP1 入库前的隐私自检有两处不实: (a) "盘符+分隔符"这一类从未真正执行过——正则字符类里的反斜杠被转义掉, 字符类实际只剩正斜杠, 因此单字母+冒号+反斜杠开头的绝对路径一律漏检; (b) 归档件说明行把"没执行的检查"写成"命中 0"的事实。用更正后的正则实测: 12 块原字节里 run1.stderr.bin 有 1 处仓根绝对路径(PowerShell 抛 STOP_FAILURE 时回显自身脚本位置), 其余 11 块为 0。处置：改正字符类为同时匹配两种分隔符并对解码后的原始字节执行; 该块按字节整段替换私有根后重新 base64, 块头同时登记"归档"与"原字节"的长度+SHA+替换处数(lossless=false, 485 B -> 489 B), 其余 11 块两种身份完全相同; 另写逐块复算校验器 m0_verify_capture.py 现场复算 12/12 通过; 原字节未删改仍在仓外运行目录; 交接的 audit() 也加了同类形状硬停; 顺带用更正后的正则重扫了已入库的 CP0 归档 14 块, 也是 0 命中(那件的"命中 0"到本轮才被真正执行过一次)。归因：我的检查器缺陷与说明失真, 与被测采集器/板上固件无关, 不改变本轮任何结论

## 4.5 CP1 真机会话（已批准的七条只读查询）

- 批准与现场前置都来自你的真实回合（选项式批准 + "已先关 24 V、再拔 USB；SW1 阻断；电机未接；现场有人；模块/接线未改且无人另行烧录"）。上电由操作者执行，**实际时刻未回报**，只登记 `USER_REPORTED`。
- 会话主机时刻 `2026-10-27T20:11:36.0877991+08:00 .. 2026-10-27T20:11:39.5650473+08:00`（跨度约 3.5 s）；台账事件 M0_COMMAND_CHECKED=6 READ_ONLY_PROBE_READY=1 RX_BEGIN=8 RX_FILE=8 SERIAL_OPEN=1 SERIAL_OPEN_ATTEMPT=1 STOP_FAILURE=1 TX_ATTEMPT=7。
- 四项预算全部用尽且各只用一次：常规上电 1/1、COM5 打开尝试 1/1（成功）、目标命令 7/7、`tmc_uart_probe` 1/1；下载/读 Flash/DAP/主动复位/设备枚举/配置写入/清故障/任何电机动作仍为 0。
- 命令次序 `spm status → spm cap → safety_state → safety_status → motor_status → step_status → tmc_uart_probe`；被接受 6 条，最后完成的是 `step_status`；停止消息 `M0_LINK_NOT_CONFIRMED: probe reported failure`。
- 失败身份（本轮最有价值的新事实）：探针首行已打印（说明 uart2 打开成功、配置事务已取得），随后 `[TMC] IOIN read failed (-117) -> 无法确认身份; 这不等于芯片损坏`，固件自报 WRITE 帧 0 个。`-117` 在本配置解析为 `-RT_ETIMEOUT`（`RT_USING_LIBC=1` → `ETIMEDOUT = ERROR_BASE_NO(7) + 110`；同一张表算出的 EBUSY=23、EIO=12 与既往实测一致，且码表取自 M1 真实目标构建的宏转储，`rtconfig.h`/`kerrno.h` 的 blob 在板载 code_commit `cd8d9f09` 与 HEAD 之间未变）。
- 因此**排除**"设备打不开"与"取不到事务锁/能力缺失"（它们给别的码、打印别的行）；但**没有**收敛到"TX 段还是 RX 段超时"—— 同一个 `-RT_ETIMEOUT` 有三段能产出（rx_flush 未排空 / TX 整帧预算耗尽 / RX 等不到合法数据报），区分的内部 `reason` 没有被任何只读命令导出，要导出属产品改动、本任务书不授权。
- MCU 侧六条互相自洽：`spm cap` 四宏全 0（能力面连续性）、`[SS] state=FAULT_LATCHED fault_code=8`（8 = `FAULT_SELF_TEST`，不是 TMC_COMM/BOOT）、许可焊盘 LOW + 回读 0 + ENN 在禁能侧 `verdict=OK`、`[MOT] armed=0`、`state=4`（在判据允许集 {0,4} 内）、`[STEP] enabled=no last_stop=not-attempted stop_rc=0`、arm 门 `0x1B` 与写入门禁 `0x3B` 复现既往形态。
- 三条采集面事实：`001_attach_passive.rx.bin` = 0 B（操作者在我 attach 前已上电，启动流已过 ⇒ 本次拿不到版本横幅，连续性只能靠 BOARD-B 下载+逐字节回读与本轮四宏全 0 交叉支撑，未再读 Flash）；safety_status / motor_status(两处) / step_status 的旧长行仍在第 126 可见字符处吐 NUL 并粘连下一个 `[标签]`，`diag_off_confirmed` 与 `gate7` 尾值按"本来不可导出"记未知、不补值；`[MOT] … health=OK` 打的是 **Motor 子系统**快照字段（`motor.c:2296-2297` 的 `snap.health`），不是 TMC 链路健康。
- 原始字节 12 块 base64 归档：`reports/reviews/phase8-motor-m0-uart-diagnosis/cp1_onboard_capture.md`；故障分析：`reports/reviews/phase8-motor-m0-uart-diagnosis/cp1_target_fault_analysis.txt`。
  归档不是逐字无损：唯一改动是把私有根（仓根/仓外工作目录/构建目录）整段换成代号，实测只有 `run1.stderr.bin` 命中 1 处（485 B → 489 B，块头标 `lossless=false` 并登记两种身份），其余 11 块原字节与归档字节完全相同；逐块复算（长度/SHA/替换数/lossless 标志/解码零私有形状）12/12 通过，`session.rx.bin == 八份 RX 按序拼接` 也按解码字节复算为真。原字节未删改，仍在仓外运行目录，可按登记的原字节 SHA 取回比对。见 `M0-CP1-F1`。

## 5. 边界与下一步

```text
NO PROGRAM / NO FLASH READ VIA TOOL / NO DAP / NO ACTIVE RESET / NO DEVICE ENUMERATION
NO MOTOR / NO STEP_ENABLE / FOUR ON-BOARD MACROS ZERO / NO FAULT_RESET / NO CONFIG WRITE
NO A0 TRIPS / NO A0 INIT / NO CLEAR_RECORDS / NO AUTO RETRY / NO AUTOMATIC RESTORE
A0_DURATION_EVIDENCE NOT_ACHIEVED / M2_M3 NOT_AUTHORIZED
FINAL_POWER_STATE = UNKNOWN(用户未回报, 不推断)
hardware_actions = 操作者常规上电1次 + COM5打开1次 + 只读命令7条(其中 tmc_uart_probe 1次)
仍为 0: 下载/程序 0  读片内Flash 0  DAP 0  主动复位 0  端口设备枚举 0  配置写入 0
        清故障 0  arm/start/stop/disarm/DIR/STEP/HIGH 0  a0 init/trip/dump/clear 0
        目标上自检 0  地址扫描 0  Keil 构建 0  主机链重跑 0
a0_export_gate.py 未改未调用  sw1_cut.ps1 未运行(HOLD)  safe_for_enable=false
board_last_proven_bin=5f30d3f0…  power_state=已上电(断电未回报)  a0_duration=NOT_ACHIEVED
```

只剩 CP2 收尾这一件，仍需现场回报，我不代填、不自派：
1. 断电回报：先关 24 V、再拔 USB（SW1 保持阻断），给实际时刻与结束时刻；不给就按 `UNKNOWN` 登记，我不会把记录时刻冒充操作时刻。
2. 本包四项预算已用尽 —— 追加任何一次上电/打开/命令（含"再敲一次 probe"、`safety_selftest`、`diag_status`、改波特率或地址）都需要**新的具体批准**。
3. 链路下一步候选（都要新的任务书，不属本轮）：(i) 现场断电检查模块在位/线序/供电（F-B 类，由操作者做）；(ii) 把 `st->reason` 导出到某条只读命令以分清 TX 还是 RX 段超时（产品改动）；(iii) 示波器看 TX/RX 波形（本机无示波器，仍 `BLOCKED_INSTRUMENT`）。

§3 的口径变化已采纳：旧 SERIAL-R1a 的 CP1 批准**不再是 M0 前置，也不再索取**；A0 串口续测本身仍按你的决定暂缓。M2/M3 仍缺两组同实物依据（电机铭牌/额定相电流定义；模块品牌型号与同一模块的 Rsense/VREF），候选镜像 `a76b845d…` 本包绝不下载。
