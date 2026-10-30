# NS800 M7 P0 串口对照续测（R2 修正后）现场执行记录

2026-10-30 21:45–21:51。**这是本任务唯一一次现场执行，P0 电压观察完成。**
批准原文（用户消息逐字）：

> 批准按本包精确补丁修正并执行一次P0串口对照续测：离线前置通过后允许COM5打开1次、七次固定查询和四个电压读数；首条严格通过则同会话继续P0。禁止下载、目标Flash读取、DAP、主动复位、电机动作、改配置及重试。

前置：37/37、11/11、9/9 全部在本机默认码页（cp936，`utf8_mode=0`）下 rc 0；API 桩 5→0；
远端 `1e10507` blob 复算 50/50 相符。连续性依据见 `field_continuity_statement.txt`
（24ab5a7 之后只有离线文件工作与只开 COM5 的工装，无任何下载/目标写/DAP 会话）。

## 1. 运行与输入

| 项 | 值 |
|---|---|
| 执行根（仓外，原件留存） | `D:\ns800-m7-p0-r2-20261030T214542` |
| 采集器 | 23430 B / c98bb712…a726（R2 修后，未再改动） |
| 验证器 | 26690 B / 08559720…887e（本轮单行补正后） |
| 载荷 BIN / AXF | e614a075…f7da / 266244 B；2205c7e7…d584 / 1688696 B，未重编未重烧 |
| readback.bin | 前一轮已存的 266244 B 文件，与 BIN 逐字节相同；**本轮未再读目标 Flash** |
| proof | 固定 24ab5a7 对象：board-proof.json e8df121f…4246；boot.b64 46b53e93…dd46 → 3381 B bc47dc02…14fa |
| 模式 | `CONTINUE_PROVEN_BOARD`，身份 `PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY`，`fresh_banner_required=false` |
| 端口 | COM5 / 115200 / 8N1，打开一次；`timeout=0` 非阻塞 + 应用层截止；DTR/RTS 未改 |

## 2. 实际额度消耗（与批准上限逐项对照）

| 动作 | 上限 | 实耗 |
|---|---:|---:|
| COM5 打开 | 1 | **1**（一次成功，无重开） |
| 下载 / 目标 Flash 读取 / DAP / 主动复位 / 电机动作 / 配置写 / 清故障 / 重试 | 0 | **全 0** |
| safety_state / safety_status / motor_status / step_status | 各 1 | 各 1 |
| current_cal show | 2 | 2 |
| current_cal sample 128 | 1 | 1 |
| UT39A+ DC4V 读数 | 4 | 4（H10-1 与 H10-3 各前后一次） |

## 3. 决定性对照：同一块板、同一条线、同一个单 CR，损坏没有再出现

`01_safety_state.rx.bin` = **64 B / `3617c305227f177ddcfd0cc3a8fff23b9385201377b27647771dadbf52374858`**，
与 M5 历史干净件同形状，也与上一轮"历史读法工装"取到的字节**逐字节相同**；
上一轮同一板同一 COM5 用修前采集器拿到的是 **62 B / `c26f480a…8305`**，中段 `4d4d 7581 7374 d195 f5`。

```text
safety_state\r\n[SS] state=READY fault_code=0 degraded=0x00\r\nmsh >        (本轮, 64 B)
safety_state\r\nMMu\x81st\xd1\x95\xf5READY fault_code=0 degraded=0x00\r\nmsh >   (修前, 62 B)
```

七份响应全部完整可读：01 64 B、02 481 B、03 1121 B、04 226 B、05 361 B、06 393 B、07 361 B，
每条都有 `COMMAND_ACCEPTED`。被动窗口 `raw_attach.bin` 0 B（合同允许静默，不是错误）。
唯一一次打开的整条会话内完成，未关口重开。

**判定分级（不过度声明）**：去除"打开后写串口设置属性"这一条路径后，同板现象消失 —— 这**支持 R2-PORT 为该损坏的原因**。
本轮 N=1、没有做 USB/物理层交叉对照，因此不写成"排除了一切其它机制"；
软件侧调用路径（同值 `timeout` 赋值也走 `_reconfigure_port`→`SetCommState`）已由上游源码与假 Win32 桩单独证明。

## 4. 业务门（采集器实判字段，原样）

静止/禁能侧全部按合同通过：`[SS] state=READY fault_code=0 degraded=0x00`；`[SAFETY] ready=YES first_err=0`、
`ENN_FEEDBACK raw=1 resolved=YES`、`request=DISABLE(LOW) pad=0 expected_FB=HIGH FB=1 -> verdict=OK`；
`[MOT] state=0 cur_hz=0 tgt_hz=0 dir=0`、`armed=0`、`stop result=api-accepted rc=0 latched=0`、`diag_off_confirmed=YES`。
`arm_gate_mask=0x18 REFUSED`、`gate5 tmc_drive_cfg=UNCONFIGURED`、`gate4 irq_gate=CLOSED polarity=not-confirmed` 如实记录，
本包不为凑 arm gate 去 apply/开 IRQ/确认极性。`02` 里那条非权威长行仍在第 126 字节后 NUL 截断并与下一短行粘连，
按既有协议分界处理，原字节未改写。

## 5. 电压观察结果

`sample 128`：`n_req=128 n_seen=128 n_valid=128 dup=1164 invalid=0 skip=0`，
raw `sum=263690 min=2034 max=2079 mean_x1000=2060078`（均值 2060.078），`seq 549607..549734`，`reset=0`，`fit=READY`。
`show` 两次系数一致：`offset_uV=1650000 gain_uV_per_A=600000 src=THEORETICAL measured=NO ver=1`，`adc health=DEGRADED`。

| 相位 | 节点 | 万用表 | UT39A+ 4V 档误差限 | 名义 ADC 换算 | 名义−万用表 |
|---|---|---|---|---|---|
| before | H10-1 CURRENT_ADC | 1661.0 mV | ±13.305 mV | 1660.136 mV | −0.864 mV |
| before | H10-3 3V3 | 3324.0 mV | ±21.62 mV | — | — |
| after | H10-1 CURRENT_ADC | 1663.0 mV | ±13.315 mV | 1660.136 mV | −2.864 mV |
| after | H10-3 3V3 | 3323.0 mV | ±21.615 mV | — | — |

名义极值 `min=1639.12 mV`、`max=1675.38 mV`（raw×3300/4095）。H10-1 前后两次读数差 +2 mV。
四个读数的 `observed_at` 均为 `UNKNOWN_TIME`（用户未报时刻，不拿日志时间冒充）。

**结论上限**：`VOLTAGE_OBSERVATION_ONLY`。
H10-3 只是测点，未证等于 VDDA；3300/4095 只是名义换算；实际 VDDA 与 ADC 转换误差未证，
因此这里的名义/万用表接近**不构成精度认证**，不产生 MEASURED 系数，不改保护阈值，不外推相电流 RMS。

## 6. 状态

```text
P0_CONTINUATION_R2_EXECUTED / COLLECTOR_RC=0 / LEDGER_COMPLETE / RESULT_VOLTAGE_OBSERVATION_ONLY
SEVEN_SENDS_ALL_ACCEPTED / DMM_READINGS=4 / COM5_OPENS=1_OF_1 / DOWNLOADS=0 TARGET_READS=0 DAP=0 RESETS=0 MOTOR=0
FIRST_COMMAND_STRICT_PASS_AND_SAME_SESSION_CONTINUED（按批准未另起单条诊断轮）
BYTE_CORRUPTION_DID_NOT_RECUR_UNDER_THE_CORRECTED_COLLECTOR / SUPPORTS_R2_PORT_AS_THE_CAUSE / N=1_NO_USB_PHYSICAL_CROSS_CHECK
CONSOLE_STREAM_INTEGRITY_NO_LONGER_OBSERVED / NOT_A_GENERAL_PROOF
VOLTAGE_OBSERVATION_ONLY / CALIBRATION_NOT_MEASURED / ADC_ACCURACY_NOT_VERIFIED
PRIOR_FAILURES_AND_62B_ORIGINAL_KEPT_UNMODIFIED
```

## 7. 归档件

`field_*.rx.bin.b64`、`field_raw_attach.bin.b64`、`field_passive_dmm_{before,after}.bin.b64`（解码回原字节，01 保留 2 个 CR，散列与台账一致）；
`field_ledger.jsonl`、`field_dmm_before.json`、`field_dmm_after.json`、`field_result.json`、
`field_collector.stdout.txt`、`field_collector.stderr.txt`（0 B）、`field_continuity_statement.txt`、
`field_business_lines_as_read.txt`。仓外执行根的全部原件未删改。
