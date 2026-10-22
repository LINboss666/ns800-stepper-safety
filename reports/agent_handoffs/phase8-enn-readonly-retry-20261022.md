# 交接：恢复旧阻塞点 + 一轮有限只读复查

- 任务 ID：`phase8-enn-readonly-retry-20261022`
- 日期：2026-10-22
- 仓库：`https://github.com/LINboss666/ns800-stepper-safety`　分支：`phase8/tmc-drive-config`
- 本轮基线：`84f60cc20d3b65f2a7ae88ff1305eed58924e9a7`（云端 `git ls-remote` 回读一致；本地 HEAD 相同，工作树 clean）
- 已获软件 GO 的源码对象：`296c369607ea69c201f797b80950b76ff312c0e0`（`9cf8279`、`84f60cc` 为文档提交，本轮未改）
- 板上固件：**USER_REPORTED_E40**（用户历史转交 `e40a9b95ef709e6a80444f9f3949dfdd70ba96ba`；**本轮没有读回 Flash 哈希/版本标识**，不作已证实）
- 冻结对象：`main=1b3757ee…`、`phase7/full-embedded-software=6d3fbc55…`、`v0.7 标签=097935fe…` —— 均未改动
- checkpoint 1（本轮完成 A + B）：见第 1、2 节。第 3 节 C 的前提确认结果与 STOP 原因在 checkpoint 2 补写。

---

## 1. A：恢复出来的"前面卡住的地方"到底是什么（只读，未执行任何动作）

查找范围＝一次集中检索：`BSP/PHASE8_HARDWARE_VALIDATION_20260925.md`、`BSP/调试记录.md`、
`BSP/PHASE7_PROGRESS.md`、`BSP/开发进度.md`、`reports/plans/phase8-safety-chain-acceptance-draft.md`、
本机历史转写目录（`<LOGDIR>/ns800/`，按轮次保存的 COM5 原始字节转写，路径已脱敏）。

### 1.1 记录里真实存在的"旧测试"

| # | 恢复出的内容 | 性质 | 是否含 GPIO 写 / 寄存器写 / STEP / 复位 | 出处 |
|---|---|---|---|---|
| R-1 | **直接把 `IO87 → TMC_ENN` 相连**的测试 | 物理接线（跳线），**已作废** | 改的是接线状态；不涉寄存器 | `PHASE8:57`（"该接法**绕过了 U9 / Q1 硬件安全链**，不是有效的使能链测试，不得以任何形式引用为验证结论"）；草案 A14 同口径 |
| R-2 | `PF.21`（GPIO31/J4-20，**旧版** MCU_DRV_ENABLE）锁存=0 但焊盘被顶在 2.37~3.3V | 历史排障（BUG-009，OPEN/physical validation pending），最终**换脚迁移到 PC.23** | **含 GPIO 配置写**（`pf21_diag` 逐寄存器操作、改输入/Hi-Z），含用户万用表实测 | `BSP/调试记录.md:204-254` |
| R-3 | `tmc_uart_probe` 用"写 GCONF 原值 → IFCNT+1"证明写帧到达 | **旧版写回投毒**；F2c 判为绕过事务锁与写入门禁（F2c-NEW-01），新版已删除 | **含寄存器 WRITE** | `BSP/调试记录.md:353`；`README.md:205` |
| R-4 | `motor_arm` 在板上被门禁拒绝（`arm gate fail mask=0xB`，`arm REFUSED`） | **软件门禁正常拒绝**，不是硬件失败、也不是 UART 无回复 | 无（拒绝路径不写脚） | `<LOGDIR>/ns800/phase8_b1_20260925_transcript.log:82,95,295`（`[Motor] arm gate fail mask=0xB (arm REFUSED)`、`drv_en_verified=0`、`armed=0`） |
| R-5 | 历史"级联逻辑器件损坏：U5 与门异常 + 急停非门异常，实测 `TMC_ENN` 约 0V" | **确定性根因表述**，本轮按 §2 撤回其确定性（见 1.3） | 无 | `PHASE8:40` |

### 1.2 恢复出的、与"ENN 仍为 0V"直接相关的两处**独立**历史观测（2026-09-25/26，板上）

- `safety_status` 打印：`[SAFETY] PC.6  pin=70 raw=0`（同一轮里 `PF.14/PF.15/PA.3` 也 raw=0）
  —— 转写行号 369-372。**注意版本语义**：那是 `e40` 版，它把 `PC.6` 当急停输入命名并打印，
  **只可取原始 0/1，不得按"急停触发"解释**。
- `tmc_regs` / 自检快照：`IOIN=0x21000040` → **bit0 = 0**；同轮更早为 `0x21000041`（bit0=1），
  `GSTAT=0x01` 表示期间发生过复位 —— 当时明确"不断言含义"，并把它留给既存的
  「**ENN 浮空行为实验**」议题（`PHASE8:129-145`）。
- 同轮许可侧：`drv_en_verified=0`、`armed=0`、`state=FAULT_LATCHED fault=8`（后期一轮为 `state=READY fault=0`）。

**含义（仅按图纸推导，不冒充实测）**：许可在禁能侧（LOW/未确认）而两处 ENN 观测都是 0，
组合上等价于新版判定里的 **`UNEXPECTED_ENABLE`（说停没停）**。这与"IO87 拉高后 ENN 仍 0V"并不矛盾，
但也**不能互相证明**：历史那几笔读数的接线/装配条件（模块插拔、VM 是否通电、是否曾接跳线）
在本轮无法确认，所以本轮**不重述**任何"已损坏/已确认"的结论。

### 1.3 §2 要求的更正：撤回"门芯片损坏"的确定性根因

- 用户 2026-10-22 反馈（**USER_REPORTED**）：**"检查过了，门芯片没问题，是我把逻辑搞错了。"**
- 因此 `PHASE8:40` / R-5 里"级联逻辑器件损坏（U5 与门 + 急停非门异常）"这一**确定性根因**在本轮**撤回**，
  降级为"当时的解释候选"。**原始记录一律保留、不删不改**（本轮也不允许改 `PHASE8`/README/开发进度/待办/草案，
  所以这条撤回只登记在本文；对既有文档的同步应作为后续单独一轮处理）。
- 用户的猜测"非门一直输出 0V，所以 IO87 拉高 ENN 仍为 0V"记为**解释候选**：按图纸推导，
  `U13` 输出 0 → `U5` 输出 0 → `Q1` 关 → `TMC_ENN` 应被 `R10` 拉到 **HIGH（禁能）**；
  所以"U13 恒 0"**能解释"许可穿不过与门"，不能解释"最终 ENN 仍是 0V"。
  **目前没有新读数说明那次读到的 0V 究竟是 `U5-4` 输出还是 `H6-5/TMC_ENN`** → 记 `UNKNOWN_PROBE_POINT`。
- 本节没有任何一条是 Qoder 本轮独立测量得出的。

### 1.4 昨晚（2026-10-21）那次 IO87 相关的交流：本地**未找到**原始记录

- `<LOGDIR>/ns800/` 里最新文件停在 2026-10-18；仓库与交接中也没有 2026-10-21/22 的转写或读数登记。
  **结论：关于"把 IO87 拉高"的那次，未找到命令级原始记录**（不猜命令）。
- 可确认的只有：本轮之前提出过"物理跳线 3V3 → J4-21"这条路，而它正是 R-1 已作废接法的同类，
  且**属于本轮 §3 明令禁止的动作**（不改接线/不拉高 IO87）。该跳线**现在是否仍装在板上，我无从确认**——
  这是 §4-C 无法开始的首要缺项（见 3.1）。

---

## 2. B：固件身份与命令副作用核对（逐条读源码，含板上版本）

| 项 | 本轮核对结果 | 依据 |
|---|---|---|
| 固件身份等级 | `USER_REPORTED_E40`。**没有**本轮读回的 Flash 哈希/版本标识；不由内核版本或本地 `rtthread.bin` 反推 Git SHA | §1；`PHASE8:139` 的历史 banner `build Sep 25 2026 21:25:16` 也只属历史记录 |
| e40 的 `safety_status` | 走 `st_inputs[]` 打印 `pin=%d raw=%d`，其中 PC.6 被命名为急停；**只取原始 0/1** | `git show e40a9b9:…/safety_thread.c`（`[SAFETY] %-11s pin=%d raw=%d`；`estop_isr`/`EVT_ESTOP` 仍在） |
| e40 的 `motor_status` | 打印 `arm gate fail mask=0x%X`，**只有四道门**：`MOT_GATE_ENABLE_PATH(1<<0)`、`NOT_READY(1<<1)`、`UNHEALTHY(1<<2)`、`NOT_PROTECTED(1<<3)`；**没有 bit5**（新版才有的反馈一致性门），提示语是 "all four gates pass" | `git show e40a9b9:…/motor.h`（`MOT_GATE_*` 定义 :32-35）、`motor.c:605-606` |
| e40 的 `tmc_regs` | 读集合确为 **7 个 READ**：GCONF、GSTAT、IFCNT、IOIN、VACTUAL(`0x22`)、CHOPCONF(`0x6C`)、SG_RESULT；该命令**无寄存器 WRITE**；但它**确实发送 READ 请求帧**，并会影响链路健康度/计数 | `git show e40a9b9:…/tmc2209.c` 内 `regs[]` 数组 |
| 为什么禁用 `tmc_uart_probe` | e40 里存在 `tmc_write_reg()`（单传输锁、不进门禁）且在 probe 路径调用 `tmc_write_reg(GCONF)` → **原值回写也算写**；新版已删除该函数（F2c-NEW-01） | `git show e40a9b9:…/tmc2209.c:162,404`；R-3 |
| 为什么禁用 `pin_status` | 对 `DRV_ENABLE` 调 `safety_drv_enable_write(0)`，对其余输出脚 `rt_pin_write` 重写默认电平 → **主动写 GPIO** | e40 与 84f60cc 的实现均已核（`safety_gpio.c:317` 起） |
| 为什么禁用 `runtime_selftest` | 投 `EVT_SOFT_FAULT`→锁存→尝试清除等故障/恢复路径（D6 已定级 DEFERRED） | 草案 D6 表 + `runtime_selftest` 实现 |
| 新版（84f60cc）差异，仅登记不上板 | 新增 `PIN_NAME_ENN_FEEDBACK`、`bit5 MOT_GATE_FB_MISMATCH`、`FAULT_DRV_FEEDBACK=11`、disarm 之后再复核反馈等，**都在源码里，板上未运行**；新版可选限位实现冻结固件也没有 | `reports/agent_handoffs/phase8-io70-optional-limits.md` 第 2/9/11 节 |
| GSTAT bit2 名称 | 按 Rev.1.09 是 **`uv_cp`**，不得沿用旧打印里的 `sg2` | `README.md:205`、草案 A23 |
| 官方依据 | TMC2209 Datasheet Rev.1.09 印刷 p9 §2.2（ENN 为 HIGH 时功率级关断、输出浮空）、p24 IOIN `0x06` bit0 = ENN | 任务书 §2 |

**停止条件（B 段）**：只要实际打印/命令行为与上述已读源码明显不符，就停止发送下一条命令；
若连合理的运行版本候选都没有则保持 `FIRMWARE_UNKNOWN`，串口阶段不执行。本轮候选存在（`USER_REPORTED_E40`），
但 §C 的物理前提另有缺项（见下），因此串口阶段的状态在 checkpoint 2 记录。

---

## 3. C：一轮有限只读复查 —— 前提确认状态（checkpoint 2 补写结果）

### 3.1 开始 C 之前必须成立、但本轮我无法自行确认的前提

1. **接线与供电未改变**：`J4-21(IO87)` 上是否仍留着 2026-10-21 讨论过的 **3V3 外部驱动/跳线**？
   若仍在，节点是被外力驱动而非软件许可 —— 读回来的 ENN/IOIN 会全部失去解释基础，
   而且这正是 R-1 已作废接法与 §3 禁止项。
2. **VM / 外供的实际状态**：§2 记"TMC VM 与 IMU 外供"，而 2026-10-21 用户答"VM 未通电"。
   两者时刻不同，当前状态我不知道。
3. **打开 COM5 是否会造成目标复位**：本机文档记载 COM5 是**板载 DAPLink 的虚拟 CDC**
   （`README.md:30` "板载 DAPLink 调试器"；`README.md:188` "COM5 / 115200 / 8N1"），
   仓库里没有"打开该 CDC 不会复位目标"的直接实测记录；§3 要求"若打开端口可能复位且无法保证不触发，停在该处"。
   另外用户可能已占用该控制台（§3 禁止抢占终端）。
4. 电机未连接 / 无运动或 STEP 请求：历史转写支持（`armed=0`、`VACTUAL=0`、PHASE8:184 "电机未连接"），
   但那是 2026-09-25 的记录，**不能当作本轮现场状态**。

→ 处理：不擅自做物理动作来满足前提，也不在解释基础不明的接线下发命令。
待用户就上述 1/2/3 给出确认后，按白名单顺序执行 `safety_status` → `motor_status` → `tmc_regs` → `safety_status`
（最多两轮，任何一步异常即保留完整失败并停止自动重发）。

### 3.2 实际发出的命令与原始读数（checkpoint 2：本轮已执行，两轮）

§3.1 的三项前提由用户当场答复解除：**"跳线已拔掉，接线恢复原状"** + **"由我打开 COM5 执行（115200 8N1，仅白名单三条）"**。
VM/外供的具体路数仍不完整（§2 已记为不完备），本轮不改变任何供电或接线，因此**不据供电条件作任何结论**。

| 轮 | 时刻 | 命令 | 原始值（未改写） |
|---|---|---|---|
| 1 | 17:29:47.314 | `safety_status` | `ready=YES first_err=0`；`MCU_DRV_ENABLE = 0`；`DRV_ENABLE last write+readback verified = 0`；`ESTOP_SENSE … = 1`（e40 命名，只取原始 0/1） |
| 1 | 17:29:50.729 | `motor_status` | `state=4 cur=0 tgt=0 accel=2000 decel=2000 dir=0`；`armed=0 drv_en_verified_pad=0 health=OK`；`arm gate fail mask=0xB (arm will be REFUSED)`；`gate4 detail: irq_gate=CLOSED polarity=not-confirmed gpio_ready=YES` |
| 1 | 17:29:54.130 | `tmc_regs` | `GCONF=0x00000101 GSTAT=0x00000001 IFCNT=0x00000000 IOIN=0x21000041 VACTUAL=0x00000000 CHOPCONF=0x15010053 SGRESULT=0x00000000` |
| 2 | 17:31:41.851 | `safety_status` | 与轮 1 逐项一致 |
| 2 | 17:31:45.223 | `motor_status` | 与轮 1 逐项一致 |
| 2 | 17:31:48.626 | `tmc_regs` | **七个值与轮 1 全同**（含 `IFCNT=0`、`IOIN=0x21000041`） |
| 2 | 17:31:54.966 | 第 4 次采样（应为 `safety_status`） | 我的脚本把标签当命令发出 → `safety_status#2: command not found.`（无副作用，**该次未取到**，见第 5 节失败登记） |

计算值：`VERSION=(IOIN>>24)&0xFF=0x21`（TMC2209 ✓）；`ENN=IOIN&1=1`；`IOIN` bit6=1（PDN_UART 高）；
许可侧 pad=0 且"写+回读已确认=0"；`GSTAT=0x01` 复位位（bit2 正确名称 `uv_cp`，不用旧 `sg2`）；
`IFCNT` 两轮均 0 —— **按 §C 口径只作记录：不变不能单独证明总线上绝无 WRITE**，也不据此归因，且已停止扩大测试。

跨采样一致性：轮 1 与轮 2 的 `safety_status`/`motor_status` 逐项相同，期间未触碰接线或供电，
故"同轮首尾一致"成立；`tmc_regs` 之后那次复核由轮 2 首个 `safety_status` 等效补足（如实标注，不冒充第 4 步完成）。

### 3.3 只读结论判据（预登记，避免事后自圆其说）

- 两轮许可 LOW + GPIO70=1 + 有效 TMC2209 应答且 `IOIN.bit0=1` → 最多
  **`READONLY_DISABLED_SIDE_CONSISTENT`**（并注明本轮是否有仪表数据）；**不关闭 RISK-04**。
- GPIO70 与 IOIN 矛盾 / ENN=0 而许可 LOW / READ 失败 / 状态漂移 → **`MISMATCH` / `READ_FAILED` / `STATE_CHANGED`**，保留实际值。
- 无新万用表读数 → **`METER_NOT_MEASURED_THIS_RUN`**（仪器型号为 UT39A+，只能给静态电压/电阻/通断，
  不能证明"无 STEP 脉冲"、复位毛刺或停机时延）。
- IO87 主动 HIGH → ENN LOW 的跟随：本轮不执行，预期登记
  **`ENABLE_TRANSITION_NOT_EXECUTED / BLOCKED_METHOD`**；`motor_status` 的门禁拒绝 **不等于** 执行过 `motor_arm`。

---

## 4. 本轮结论

1. **`READONLY_DISABLED_SIDE_CONSISTENT`**（三源一致落在禁能侧，两轮相同）：
   许可 pad=LOW 且写+回读已确认；`PC.6`(GPIO70) 原始=1；`IOIN.bit0`=1；`VACTUAL=0`、`armed=0`、`cur/tgt=0`。
   这是本轮**只读观察**到的最大结论，等价于新版判定里"禁能侧一致（`SAFETY_FB_OK`，want=0）"的**现场对应**，
   但板上跑的是 e40，**不含**该判定逻辑 —— 不得写成"新版反馈判定已上板通过"。
2. **本轮没有证明的内容**（全部仍开放）：反馈支线的**物理连通性**（断线/上拉也能看起来一致，需万用表，
   `METER_NOT_MEASURED_THIS_RUN`）；急停能否**独立切断**；许可 HIGH→ENN LOW 的**跟随**；停机**时延**；
   `ENN 浮空行为` 与 `IFCNT` 语义差异（见 5.4）。因此 **RISK-04 不关闭**，RISK-03/03a 继续开放。
3. 与历史记录的对照（只登记差异，不作因果）：`PHASE8:134` 曾记 `IOIN=0x21000040`（bit0=0），
   本轮两次为 `0x21000041`（bit0=1）。两次读数之间的装配/供电/接线变化**未知**（期间发生过模块插拔、
   以及 2026-10-21 讨论过的跳线），因此**不解释**这次翻转，只记为"当前落在禁能侧"。
4. `motor_arm` 的拒绝（mask=0xB = enable-path + safety-not-READY + protection-not-ready）是
   **软件门禁正常拒绝**，与"UART 无回复"或"硬件已验收"都无关；本轮也未调用它。
   `safety-not-READY` 说明板上状态机当前不在 READY（e40 语义），与历史 `state=FAULT_LATCHED fault=8` 记录同类，
   本轮未下任何恢复命令。
5. 用户答复的接线现状（"跳线已拔掉，恢复原状"）记为 **USER_REPORTED**，Qoder 未独立核实。

## 5. 失败与偏差（原样，不粉饰）

1. **轮 1 的第 4 步未落入转写**：脚本以 rc=0 结束并打印 round end，但没有写出 `tmc_regs` 之后的 `safety_status` 采样块，
   原因未查明；本轮**不声称轮 1 完成了四步**。
2. **轮 2 的第 4 步因我的脚本缺陷失败**：`Ask` 函数把采样标签 `safety_status#2` 当命令发出，
   目标回 `command not found.`（不存在的命令，无副作用）。第 3 步要求的"READ 成功后再采一次"
   由轮 2 首个 `safety_status` 等效补足并已如实标注。
3. **打开 COM5 未观察到重启**（两轮均无启动横幅，msh 空闲提示符在预监听里已在）——
   这是本轮的**具体观测**，不推翻 §3 "打开端口可能复位"的保守前提，也不作为跨项目通用结论。
4. **`IFCNT=0/0` 与历史 1→2 的记录不一致**：按 §C 只作记录，未据此下任何结论；
   可能方向（芯片在两次读之间被复位、或 IFCNT 对 READ 数据报的计数语义与预期不同）都**未查明**，
   与既存的「ENN 浮空行为实验」并列为待议项。未重试、未扩大测试。
5. 任务书 §6 要求的两次 MD checkpoint 均已做到（A/B 一次，C/结论一次）；未改 README/开发进度/待办/草案，
   未重建 Keil，未跑主机矩阵或元负例，代码与工程 **0 差异**（下方第 6 节用 Git 核对）。

## 6. 范围核对

- 允许路径内新增/修改：`reports/agent_handoffs/phase8-enn-readonly-retry-20261022.md` / `.json`、
  `reports/reviews/phase8-enn-readonly-retry-20261022/`（脱敏 `.txt`）。
- `applications/`、`tests/`、Keil 工程、`.gitignore`、任何宏与门禁：**0 改动**（用 `git diff --name-only` 核对）。
- 硬件动作清单（本轮实际发生的）：仅"打开 COM5 → 发送 3 条白名单命令 × 2 轮 → 关闭端口"。
  **未**烧录、未用调试器下载/复位、未主动切换 DTR/RTS、未改变供电/断电/模块装配/接线、未接电机、
  未输出 STEP、未向真实 TMC 写任何寄存器（含原值回写）、未拉高 IO87、未驱动 IO70、未开 IRQ 门或极性确认。

## 7. STOP

`AWAITING_INDEPENDENT_REVIEW`。无论本轮结果是 `READONLY_DISABLED_SIDE_CONSISTENT` 还是含失败登记，
都不进入 F2e/F3，不自行开使能/配置/运动试验，不开展新的工具返修循环。
IO87 主动使能仍缺合法入口（`BLOCKED_METHOD`），下一步见交付回执第 ⑥ 项的唯一最小建议。
