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

## 原始日志（本地，不入库）

- `C:\Users\LMX\qoder_logs\ns800\` 下按轮次保存的 COM5 原始字节转写
  （`transcript.log` 及 `round*.log` / `ns800_ontarget_20260925_transcript.log`）
- Phase 7 上板轮：同目录；Keil `build_agent*.log` / `flash_agent.log` 在 BSP 目录（未提交）
