# Phase 8 硬件验证记录 — 2026-09-25

> 本文件只记录**实测事实与硬件约束**，不记录推断结论。
> 代码基线：`e40a9b9`（tag `v0.7-phase7-software-board-validated`）。
> 板：NSSinePad-NS800RT7P65x V1.2 + 自研扩展板（当前 revision，下称 RevA）。
> 本轮**没有对任何应用源码做行为改动**。

---

## Phase 8-A：驱动使能链（MCU_DRV_ENABLE → TMC_ENN）

### 设计意图（正确版，含此前的误解修正）

扩展板设计的链路**不是** `IO87 → TMC_ENN` 直连，而是：

```
MCU IO87
  → MCU_DRV_ENABLE
  → U9 逻辑（与 ESTOP_OK 合成）
  → DRV_ENABLE_SAFE
  → Q1 BSS138 反相/下拉级
  → TMC_ENN          （TMC_ENN 在扩展板设计上另有 3V3 上拉）
```

因此软件约定 **“MCU_DRV_ENABLE = LOW 表示请求驱动禁用/安全态”** 与设计意图的
硬件逻辑是**相容的**（LOW 经 U9/Q1 后使 TMC_ENN 保持高=禁用）。这一条是设计层面
的确认，不等于硬件已验收。

### 状态：HARDWARE-BLOCKED ON CURRENT PCB REVISION

- **阻塞原因**：当前物理扩展板 revision **漏画 / 未把 `MCU_DRV_ENABLE` 节点引出到
  连接器或测试点**。MCU 侧该网络无落点，无法从 IO87 接入上面的安全链。
- **用户决定**：**不给这块板飞线**（不 bodge）。整链测试**延到下一版 PCB**。
- 因此 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` **必须保持 `RT_FALSE`**，本轮不改动、
  不以任何软件手段绕过。（`motor.h` 的门禁 1 就是这个常量；真机 `arm gate fail
  mask=0x9` 的 bit0 正是它。）

### 隔离测量记录（仅供排障历史，不构成任何验收结论）

| 测量条件 | 结果 | 该结果**不能**说明什么 |
|---|---|---|
| TMC2209 模块拔出后，模块座上 `ENN → 模块 GND` | ≈ **60–70 kΩ** | 只说明模块座自身的等效电阻，不含扩展板逻辑 |
| 扩展板**未上电**、TMC 模块拔出时，`TMC_ENN → GND` | ≈ **5 kΩ** 等效 | ⚠ **不能据此判定 PCB 短路**：`TMC_ENN` 经 **R10** 连到**未上电的 3V3 轨**，测到的是透过该轨看到的整块板阻抗，不是对地短路 |
| 早先直接把 `IO87 → TMC_ENN` 相连的测试 | 已作废 | ⚠ 该接法**绕过了 U9 / Q1 硬件安全链**，不是有效的使能链测试，不得以任何形式引用为验证结论 |

### RevB 需求（只登记要求，不在本轮改原理图）

**必须引出的测试点 / 连接器触点：**

1. `MCU_DRV_ENABLE`（RevA 漏画，这是当前唯一硬阻塞项）
2. `DRV_ENABLE_SAFE`（U9 与 Q1 之间的节点，用于分级排障）
3. `TMC_ENN`（Q1 之后、进模块之前的节点）

**强烈建议增加：**

4. `ESTOP_OK`（U9 的另一路输入；无测试点时急停链与使能链无法分段测量）

与 `待办事项.md` 里既有的 RevB 清单一致（①补 `MCU_DRV_ENABLE` 网络（漏画）
②补完整使能链 Q101/R121/R122/R123 + 急停第一组常闭触点），此处只补充“必须可测”
的测试点要求，不重复原理图工作。

---

## Phase 8-B1：安全输入原始极性实测（ESTOP / LIMIT_MIN / LIMIT_MAX）

### 测量条件与纪律

- 固件：`e40a9b9`，未做任何改动；**IRQ 门全程保持 CLOSED**（纯轮询读数，不开 EXTI）
- **未执行** `safety_polarity_confirm` / `safety_irq_enable`（见下“全局极性为何不确认”）
- 步进电机**未连接**
- 当前软件**假设**（本轮原计划验证它，实际未做通断操作，见下"结果"）：
  safe = LOW，触发 = HIGH，IRQ 模式 RISING。**该假设仍是未经实测的假设。**

| 信号 | MCU 脚 | IO | 软件假设 safe |
|---|---|---|---|
| ESTOP | PC.6 | IO70 | LOW |
| LIMIT_MIN | PF.14 | IO24 | LOW |
| LIMIT_MAX | PF.15 | IO25 | LOW |
| TMC_DIAG | PA.3 | IO3 | LOW（本轮不验证） |

### 基线门禁（§6）实测

本轮出现两种基线状态，都与软件无关，只反映硬件在位情况：

| 时刻 | 链路 | stage 11 结果 | 终态 |
|---|---|---|---|
| 23:27 / 23:4x（模块未在位或未上电） | `RX(echo only, no 05 FF header)`，`tmc_scan` 0..3 **全无有效应答** | `[SS] [REQ FAIL] TMC2209 link` → `required=FAIL` | `FAULT_LATCHED code=8` (FAULT_SELF_TEST)，`arm mask=0xB`，`[Diag] verdict=6 = SENSOR_FAULT`，`[MOT] EMERGENCY STOP (drv_en LOW verified)` |
| 23:44（模块插回、重上电后复位） | `IFCNT 0→1 ALIVE`，`IOIN=0x21000040` | `[SS] [REQ OK] TMC2209` → `required=PASS` | **`state=READY fault_code=0`**，`irq_gate=CLOSED`，`polarity=not-confirmed`，`protect_ready=NO`，`arm mask=0x9 (REFUSED)`，`Motor state=0`，`MONITOR_ONLY` |

> ✅ 顺带得到的真实故障注入证据（不是合成测试）：TMC2209 链路缺失这种**物理**
> 故障下，required 自检把它判为致命项、拒绝进入 READY、锁存 `code=8`、把
> `motor_arm` 掩码从 `0x9` 加到 `0xB`，并让诊断在 `MONITOR_ONLY` 下报
> `SENSOR_FAULT` —— 全链路 fail-closed 成立，且复位后链路恢复即自动回到 READY。
> 这一条只能算**当前代码**在真实硬件故障上的行为记录（23:27 与 23:44 两次），
> 不能外推成"EXTI/极性已验证"。

### 结果：三路安全输入物理极性 —— **本轮未测量**

用户在会话中明确选择**不测试三路安全输入**；且此前状态为"只有部分或都没接"
（与 `待办事项.md` 2026-09-13 的记录一致：ESTOP 未接、限位只做 GND 跳线占位）。

因此本轮**不产生任何 PASS/FAIL 极性结论**，只登记空闲原始电平读数
（`safety_irq_status` @23:43:25 与 `pin_status` @23:43:20，IRQ 门 CLOSED，纯轮询）：

| 信号 | 脚 | 空闲 raw | 触发态 raw | 极性结论 |
|---|---|---|---|---|
| ESTOP | PC.6 / IO70 | **0** | 未测 | HARDWARE-PENDING（未做通断操作） |
| LIMIT_MIN | PF.14 / IO24 | **0** | 未测 | HARDWARE-PENDING |
| LIMIT_MAX | PF.15 / IO25 | **0** | 未测 | HARDWARE-PENDING |
| TMC_DIAG | PA.3 / IO3 | **0** | 无法产生真实事件（电机未接，本轮 TMC 链路一度整体不应答） | HARDWARE-PENDING |

⚠ 「空闲读到 0」**不等于** "safe=LOW 已验证"：未接线的输入读到什么都有可能，
它只证明 MCU 侧该脚当前解析为 0。真正的极性需要"操作源 → 观察翻转 → 恢复"三步，
本轮没有做，所以也不得写进事实库。

### 意外发现（安全相关，未解释，留给下次）

模块拔出/插回前后，`IOIN` 的低-bit 状态**发生变化**：

- 21:23（本会话之前，模块在位）：`IOIN=0x21000041`
- 23:44（重新插回后，`tmc_regs` 三次一致）：`IOIN=0x21000040` → **bit0 由 1 变 0**
  （bit6 仍为 1 = PDN_UART 高；`GSTAT=0x01` 说明期间发生过复位；
  `VACTUAL=0`、`SGRESULT=0`、`GCONF=0x101`、`CHOPCONF=0x15010053` 与历史基线一致）

即 **TMC 侧 `ENN` 输入的状态位翻转了**，而软件没有任何改动（同一 `e40a9b9` 镜像，
banner `build Sep 25 2026 21:25:16`）。我不在这里断言它的含义：TMC2209 `IOIN` bit0
报的是引脚电平还是"禁用"语义、以及 ENN 悬空时算什么，正是
`待办事项.md` 里既存的「**ENN 浮空行为实验**」要回答的问题。**接电机之前必须先搞清
这一位为什么变**；当前无风险仅因为：电机未接、`VACTUAL=0`、无 STEP 输出、
MCU 侧 PC.23 实测并回读为 LOW。

这也再次印证 RevB 需要 `TMC_ENN` 与 `DRV_ENABLE_SAFE` 测试点的理由。

### 本轮结论（不随测量结果变化的部分）

- 全局极性：**NOT CONFIRMED**（未执行 `safety_polarity_confirm`）
- Safety IRQ gate：**CLOSED**（未执行 `safety_irq_enable`）
- `protection_ready`：**NO**
- `motor_arm`：**REFUSED**（mask 0x9）
- 步进电机：**未连接**；`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` 保持 `FALSE`

---

## 全局极性为什么本轮不确认

`safety_polarity_confirm CONFIRM` 把**四路**输入一并标记为已验证，而
`TMC_DIAG` 的真实故障源行为本轮**无法**验证（电机未接，没有真实的
TMC2209 stall/DIAG 事件可产生）。把三路实测 + 一路未知混成一个“已确认”，
正是本项目禁止的那类误导。因此：

- 全局极性：**NOT CONFIRMED**
- Safety IRQ gate：**CLOSED**
- `protection_ready`：**NO**
- 门禁 3/4 依旧关闭，`motor_arm` 依旧被拒

**不得**用“MCU 跳线人为拉高/拉低”来冒充 TMC2209 DIAG 源的实测证据。

---

## Phase 8-C：核心外设最终稳定性验证（2026-09-26 00:00–00:20）

会话元数据：

| 项 | 值 |
|---|---|
| 测试时 repo HEAD | `14e91db` |
| 固件源码提交 | `e40a9b9`（`7f46f06`/`14e91db` 已用 `git diff --name-only` 核实为**仅文档**） |
| 镜像 banner | `RT-Thread 5.3.0 build Sep 26 2026 00:00:39` |
| clean Rebuild All | 0 Error / 0 Warning，`Code=129306 RO=46578 RW=2200 ZI=83212`（与 `e40a9b9` 构建逐项相同） |
| 烧录 | `Erase Done.Programming Done.Verify OK.` |
| 控制台 / 硬件 | COM5 115200 8N1，NSSinePad-NS800RT7P65x V1.2，**电机未连接** |

### 先记录一次套件中断（不得掩盖的间歇性硬件故障）

00:01 与 00:02 两次启动都是 `[SS] [REQ FAIL] TMC2209 link` → `FAULT_LATCHED code=8`、
`arm mask=0xB`、`Motor state=4`；`tmc_scan` 在 **addr 0..3 全无有效应答**，只收到自身回显。
按 §2 要求当时**停住整套件**并向用户报告；用户重新插好模块后，00:07 启动得到
`[REQ OK] TMC2209 (IOIN=0x21000040)` → `state=READY`，套件才继续。

连同 Phase8-B1 的同型事件，`TMC2209 物理在位/接触` 已累计 **3 次失联**
（23:27、00:01、00:02）与 2 次正常（23:44、00:07 起）。这是接线/插座可靠性问题，
不是软件问题；下面的 TMC 稳定性数据只描述"插好之后"的传输质量，
**不能外推为"插座可靠"**。

### 分项结果

| 子系统 | 方法与轮次 | 原始结果 | 判定 | 证据边界 | 剩余工作 |
|---|---|---|---|---|---|
| **W25Q64 / SPI1** | `flash_info` ×20；`flash_test run` ×1（仅 0x7FF000–0x7FFFFF，`NS_TEST_BASE/SIZE` 已核源码确认与 params 0x000000–0x00FFFF、events 0x010000–0x10FFFF 不相交）；`msh reboot`；`flash_verify`；`flash_logstat` 前后 | 20/20 提示符完整、JEDEC 唯一值 `EF 40 17`（`SR1=00 SR2=00 SR3=FF` 每次一致）、无 SPI/超时/身份错乱；erase + 600B 跨页写 + 整扇区比对 **PASS**；重启后只读保持校验 **PASS**；logstat 前后完全相同 `slots=1533/4096 valid=1533 bad=0 queued=0 completed=0 failed=0 queue_dropped=0 last_error=0`；`[Config] valid=YES` 未受影响 | **BOARD-TESTED / STABILITY-VALIDATED** | 只证明器件+存储路径的稳定读写与保持；`SR3=FF` 的含义未深究；未测写保护策略 | 无（该路径可冻结） |
| **ADXL345 / SPI3** | `imu_probe`；`imu_raw` ×50 + 追加 ×20；静置 70 帧 + 旋转 90° 后 5 帧 + 轻敲窗口 30 帧 + 用诊断 100Hz 滑窗峰值连续读 `diag_status` ×50 | DEVID `0xE5`/`0xE5`；**传输 70/70 全部返回且 `health=OK`**；静置模长 1011–1026 mg；旋转后主轴由 `Z+=932` 变为 `X+=939`（`Z=-436`），模长 ≈1034 mg（5/5）；**发现 1 个异常帧（第 27/50 次）`X=Y=Z=-3 mg` 且 `health=OK`**；轻敲尖峰两种采样方式都未接住（`imu_raw` 30 帧最大模长仅 1048；`vib_peak` 50 次读数 1025–1062，无 >1100） | 传输与姿态响应 **BOARD-TESTED / BASIC SAMPLING STABILITY**；⚠ 数据完整性有一例缺陷；轻敲瞬态 **未证实**（不下 PASS） | 见下"缺陷 F-IMU-01"；不声称标定精度、带宽、频率响应、INT1/FIFO | 复现并定位全 0xFF 读；轻敲需改用连续采集（如黑匣子/高速采样）才能判 |
| **TMC2209 / UART2** | `tmc_crc_test`；`tmc_scan`；`tmc_status` ×100；`tmc_regs` ×20；`tmc_uart_probe` ×1 | CRC 官方向量 PASS（`crc=8F/48`）；addr0 `IOIN=0x21000040 VERSION=0x21 FOUND`，addr1..3 无应答（符合预期）；**100/100 每次 GSTAT 与 IOIN 均成功、`VERSION=0x21` 100/100**；0 超时 / 0 CRC 错 / 0 READ FAILED / 0 丢失提示符 / 0 echo-only；`tmc_regs` 20/20 共 140 行寄存器全有效，`IOIN=0x21000040` 20/20，`GCONF=0x101`、`CHOPCONF=0x15010053` 与历史基线一致、`VACTUAL=0`、`SGRESULT=0`；写确认路径 `IFCNT 0 → 1`，GCONF 原值回写且回读仍 `0x101` | **BOARD-TESTED / UART TRANSPORT STABILITY**（插好之后） | `IOIN` bit0 记录为 0（历史某次为 1）**但不解释 ENN**——使能链硬件阻塞；不声称电机驱动、SG 堵转性能、DIAG 极性、ENN 链 | 插座/接触可靠性；`GSTAT=01` 在 120/120 次读取里恒为 1（读清位未被清掉）需查 |
| **电流 ADC / ADCA CH15** | `current_raw` ×30（每命令 64 样点，共 1920 样点） | 30/30 完成、**0 ADC 读失败**；`burst_mean` 均值 2063.8、范围 **2058–2069**（极紧）；单样点总体范围 **min 1622 / max 2412**；最大低偏离 **436 counts**（第 10 次爆发）、最大高偏离 **343 counts**（第 21 次）；`burst_mean-min>100` 的爆发 **2/30**，`max-burst_mean>100` 的爆发 **6/30**；`ema` 始终跟随均值（2062–2071） | **BOARD-TESTED / ACQUISITION PATH** | 上一轮 ~390-count 低离群**已复现且更大**，且**高侧离群更常见**；换算一致性已核（`mV=raw*3300/4095` 误差 <0.001 mV；`mA=(mV-1650)/0.6` 与打印值差 <1 mA，即整数截断） | 离群成因需示波器/前端拓扑确认；**不升级为** MEASURED / CALIBRATED / CURRENT ACCURACY VALIDATED；`cal source` 全程 `THEORETICAL(NOT measured) health=DEGRADED`（30/30） |

### 套件结束后的跨子系统状态（无应力导致的退化）

`state=READY fault=0 irq_gate=CLOSED polarity=not-confirmed protect_ready=NO` ·
`Motor state=0 armed=0 mask=0x9 (arm REFUSED)` · `Sensor thread=running seq=52210` ·
`TMC OK` · `IMU OK` · `ADC DEGRADED cal=THEORETICAL` · `Diag verdict=NORMAL mode=MONITOR_ONLY
cur_filt=21 mA vib rms=1021 peak=1025` · `flash_logstat` 与基线逐字段相同。

### 缺陷 F-IMU-01（本轮发现，**未修补**）

`imu_raw` 第 27/50 次返回 `X=-3 Y=-3 Z=-3 mg` 且 `health=OK`。按
`adxl345.c` 的换算 `mg = raw*39/10`，−3 mg 对应 **raw = −1**，即六个数据字节全为
`0xFF` —— 典型的"总线未被驱动/CS 或连线毛刺"图形。而
`adxl345_read_raw()` 只在 `adxl_read_regs()` 返回码失败时置 DEGRADED，
**传输成功就无条件 `adxl_health = SUBSYS_OK`**，因此一帧全 1 的垃圾数据被当作有效
测量发布（`vib_mg≈1` 的假"安静"样点会进诊断的 16 点滑窗；它不会伪造 IMPACT，
但会稀释真实振动）。

发生率：本轮 1/70（≈1.4%）。这与"sensor missing ≠ 0"这条红线是相邻问题：
项目已经防住"读失败写 0"，但没防住"读成功而数据明显非法"。
**本轮只登记，不改代码**（会话禁止源码改动）。

---

## 明确推迟到新扩展板的项（本文件不得被解读为已完成）

- `MCU_DRV_ENABLE → U9 → DRV_ENABLE_SAFE → Q1 → TMC_ENN` 完整使能链
- `ESTOP` 物理极性与通断行为
- `LIMIT_MIN` / `LIMIT_MAX` 物理极性与通断行为
- `TMC_DIAG` 真实故障源极性与 EXTI 触发
- STEP / DIR 实际波形与物理验证
- 任何电机运转
- `MEASURED` 电流标定（当前无任何代码路径写入 MEASURED）
- `DIAG_MODE_ACTIVE_PROTECTION`（受 MEASURED 门禁保护，仍不可开启）

Phase8-A 的硬件阻塞记录（上方）保持有效，后续轮次不得覆盖或删除。

---

## 原始日志（本地，不入库）

- `C:\Users\LMX\qoder_logs\ns800\` 下按轮次保存的 COM5 原始字节转写：
  - Phase 8-C：`c8_boot.log`(REQ FAIL 启动) / `c8_boot2.log`(READY 启动) /
    `c8_reboot.log` / `c8_base.log` / `c8_flash20.log` / `c8flashtest.log` /
    `c8_verify.log` / `c8_imu50.log` / `c8_imu20b.log` / `c8_imu_rot.log` /
    `c8_imu_tap.log` / `c8_tap2.log` / `c8_tmcbase.log` / `c8_tmc100.log` /
    `c8_tmc20regs.log` / `c8_ifcnt.log` / `c8_adc30.log` / `c8_final.log`
  - Phase 8-B1：`phase8_b1_20260925_transcript.log`
  - Phase 7 上板：`ns800_ontarget_20260925_transcript.log`、`round1_validation.log`、
    `round2_pre_commentfix.log`
- Keil 日志在 BSP 目录（未提交）：`build_c.log`、`flash_c.log`
- 主机侧测试夹具：`stress.ps1`（prompt-wait 循环，20 次重试打开端口）、
  `serial_monitor.ps1`、`send.sh` —— 本地工具，未入库
