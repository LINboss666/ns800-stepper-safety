# 交接：SW1 硬件急停阻断检查（PHASE8-SW1-HARDWARE-CUT-20261022）

任务书：`NS800_SW1_Hardware_Cut_Qoder_Task_20261022.md`　日期：2026-10-22（北京时间）
性质：**用户单独授权的一次 GPIO 诊断例外**（现场操作已安装的 SW1 + `PC.23` 有限 LOW/HIGH）。
不是运动测试、不是急停安全等级验收、不是解除软件门禁；上一轮授权已结束，不延伸。
**结果：`SW1_HARDWARE_CUT_DIGITAL_PASS` + `ANALOG_NOT_VERIFIED`**；RISK-03/03a/04 继续开放。

## 1. 分支 / 基线 / SHA / 范围

| 项 | 值 |
|---|---|
| 分支 | `phase8/tmc-drive-config` |
| 任务书基线 | `4edcf5e3333aa64ef2ce1f84bb3b903ad395c2c7`（执行前本地 HEAD = 远端，工作树 clean） |
| 软件 GO 对象 | `296c369607ea69c201f797b80950b76ff312c0e0`（未改成文档提交 SHA） |
| 板上固件 | `USER_REPORTED_E40`（`e40a9b95…`，未读回 Flash 哈希/版本标识） |
| A 段 checkpoint 提交 | `a5a5026`（工具 + 离线自检）；本轮交付提交见交付消息，不自引用 |
| 范围 | 只新增本 MD/JSON + `reports/reviews/phase8-sw1-hardware-cut-20261022/`；源码/tests/Keil 工程/`.gitignore`/宏与门禁 **0 改动**；冻结 `main`/`phase7`/`v0.7` 未动 |

## 2. 现场前提与 SW1 对象来源

- 操作者当场确认（`USER_CONFIRMED_LIVE`，非旧记录）：电机物理未接、模块在位、供电正常、在场并准备拨杆。
- SW1 对象：板上**已安装且可拨**的三脚拨杆（用户答复"SW1 已装可拨，前提全部具备"）。
- 用户先给的位置口径：`USER_REPORTED` "**短接 2–3 是允许**"，与任务书 §2 抄录的图纸提取事实相反
  （图纸：1–2 经 R6 接地→放行；2–3 因 pin3 悬空 + R5 上拉→阻断）。按 §5 记为**冲突**，未改图纸解释。
- 因该冲突，先做一次 ≤1 s 识别探针再跑 C 段；HIGH 总次数 2 次（≤ 允许的 3 次）。

## 3. 分段实际命令与读回（机械统计，非手写估值）

| 命令 | identify(2–3) | cut | 合计 |
|---|---|---|---|
| `pin read PC.23` / `pin read PC.6` | 4 / 4 | 27 / 27 | **31 / 31** |
| `pin write PC.23 high` | 1 | 1 | **2** |
| `pin write PC.23 low` | 2 | 1 | **3** |
| `motor_status` | 0 | 1 | **1** |

预检：映射 `PC.23 : 87`、`PC.6 : 70`；`armed=0 cur=0 tgt=0`；初态 `IO87=low / IO70=high`。
识别探针（2–3）：写 HIGH → `pin[87] = high`、`pin[70] = low` → 立即 `pin write PC.23 low` → `low/high`；
HIGH→LOW 发令间隔 **117 ms**（预算 1000 ms）。⇒ **2–3 实测为允许侧**，1–2 为阻断侧（C 段复核一致）。

## 4. C 段：许可仍 HIGH 时的独立阻断样本（`run_cut_1`）

同一连续 HIGH 窗口内、**写 LOW 之前**，交替读回 23 组配对样本：

| 相对写 HIGH | `IO87`/`IO70` | 含义 |
|---|---|---|
| 1255 → 2546 ms（连续多组） | high / **low** | 许可仍 HIGH，SW1 在允许位，硬件链放行 |
| **2609 ms** | **high / high** | 用户拨到 1–2：阻断样本 1 |
| **2671 ms** | **high / high** | 阻断样本 2（相邻一致） |
| 2733 ms | — | 采集器随后才写 `pin write PC.23 low` |
| 清理 / 终态 | low / high + `armed=0` | `LOW_AND_HIGH_CONFIRMED`；SW1 留在阻断位 |

满足 §6.5 全部要点：同窗口 IO70 LOW→HIGH、阻断样本前后 IO87 均实测 HIGH、取证前未写 LOW、两组相邻一致读回。
窗口总时长 2733 ms（预算 10000 ms）——**主机操作量，不是停机时延**；相邻分次读回不证明两次采样之间每一瞬间的焊盘电平。

## 5. 清理与终态

清理判定要求 **IO87=low 且 IO70=high 共同成立**（旧脚本只看 IO87 的缺陷已修）：两段都以
`LOW_AND_HIGH_CONFIRMED` 通过，未出现 `FINAL_STATE_UNKNOWN`、未补发第二次 LOW。
同一会话终态另有 `pin read` 两脚 + `motor_status armed=0`。串口随后关闭，未追加其他测试。

## 6. 判定、未测项与限度

- **`SW1_HARDWARE_CUT_DIGITAL_PASS`**：位置识别 + C 段同窗口阻断样本 + 终态清理完整。
  允许结论只到："在本次 e40 固件、当前装配/供电下，实际许可仍 HIGH 时，操作现有 SW1 可使 IO70 到
  HIGH 禁能侧，随后撤许可成功。"云端审查只能核对提交记录，不是现场见证。
- 不升级：U13/U5 与芯片端 ENN 的**模拟电压**、反馈线完整连续性、外接 NC 急停/断线容错/安全等级、
  无 STEP 波形、复位瞬态、停机时延上界、运行守卫、`296c369` 已上板、TMC 写握手、运动。
  **RISK-03/03a/04 不关闭，不翻转任何硬件验证宏。**
- `ANALOG_NOT_VERIFIED` / `METER_NOT_MEASURED_THIS_RUN`：本轮无任何仪表读数；
  用户先前确认"禁能侧已恢复正常、1.6–1.7 V 不再出现"按 `USER_CONFIRMED_RECOVERY` 登记，不当作本轮测量。
- "VM 未供电是此前根因"仍是未验证假设；本轮未切换 VM 复现。
- 直接写 GPIO 绕过软件请求态记账 ⇒ 一律以 `pin read` 实际读回为准，内部 `request/verified` 不作焊盘证据。
- A 段离线自检 17 项 `rc=0`；过程真抓到 3 个失败（2 处我的合成队列写错、1 处 `$script:out` 变量名错），
  先修再复跑，未改期望凑绿。上轮计数勘误与"旧脚本封存不复用"见同目录 `NOTES.txt` 第 1 节。

## 7. 证据与实际硬件动作

- 工具与自检：`sw1_cut.ps1`、`selftest.txt`、`selftest_summary.txt`
- 运行记录：`run_identify_23/`、`run_cut_1/`（各含 `command_trace.txt`、`rx_full.txt`、`state_ledger.csv`、`summary.txt`）
- 汇总与边界：`NOTES.txt`；本 MD 与同名 JSON

实际硬件动作：COM5 打开/关闭各 2 次（115200 8N1，未主动切 DTR/RTS、未复位）；`PC.23` 写 HIGH×2 / LOW×3；
`PC.23`、`PC.6` 只读；`motor_status` 只读 ×1；用户拨动已安装 SW1 一次（2–3 → 1–2 并保持）。
未做：烧录、调试器下载/复位、改供电/断电、插拔模块、改线、换件、接电机、输出 STEP、写 `PC.6`、
`pin mode`、`pin_status`、`runtime_selftest`、`tmc_uart_probe`、任何 TMC 命令、`motor_*`、`pwm_test`、
故障清除、IRQ 开闸、极性确认、宏置 TRUE、merge/force/改写历史、ZIP。

## 8. STOP

**`AWAITING_INDEPENDENT_REVIEW`。** SW1 留在阻断位、许可 LOW、`armed=0`。本轮 GPIO 授权到此结束，
不延伸为日常绕过门禁的控制方式；不进 F2e/F3、不烧录新版、不写 TMC、不输出 STEP、不接电机、不开宏。
下一步物理层最有价值的是同工况静态仪表五点读数（`U5-1/U5-4/Q1 栅/H6-5/J4-40` + 首尾实测 3V3），
由用户决定是否安排；SW1 pin 序与图纸的冲突留给带 pin 级实测/网表的一轮定论。

## 9. 独立复审合并（同日追加，2026-10-22；纯文档，零硬件动作）

复审件：`NS800_SW1_Cut_Review_f5f7cbf_20261022.md`（审查对象 `f5f7cbf`，含其父 `a5a5026`）。
**判定：`CONDITIONAL_GO`，本轮数字证据 `SW1_HARDWARE_CUT_EVIDENCE_GO`；不要求重做真机。**
本地已按复审逐条核对源码与日志（未修改任何已提交证据，未执行 PowerShell）。

### 9.1 接受与登记

| 条目 | 登记 | 依据 |
|---|---|---|
| C 段动态阻断（许可仍 HIGH 时 `IO70` 由 LOW→HIGH，取证后才撤 LOW） | **接受** | 复审 §3；62 个读回与选取回复／CSV 逐项一致，命令计数 68 与回执一致 |
| 末态 `IO87=LOW`／`IO70=HIGH`、`armed=0 cur=0 tgt=0` | 接受（提交记录支持，已独立对账） | 复审 §3 时序表 |
| 现用安全链**静态电气检查** + 现用 **SW1 急停链连线／功能** | **`PASS (USER_CONFIRMED)`** —— 用户自行检查确认，数值／方法未提供，不是 Agent／云端实测 | 复审 §8；草案 A29／J 节 |
| RISK-04 | **按子项**：4-a（静态电气／现有 SW1 功能）收口；4-b（瞬态、STEP 波形、最坏停机时延、连续性定量、外接 NC、断线容错、安全等级、新版 `296c369` 上板）**开放** | 复审 §8.2；草案 C9 |
| 本脚本 `sw1_cut.ps1` 未来复用 | **HOLD**（T-01／T-02 见 9.3） | 复审 §6 |
| "SW1 实物 pin 序与图纸不一致" | **证据不足**，改记"端子／位置对应有待核对"（草案 X-5）；不判实物接反，不据此排障或改线 | 复审 §5.2 |

### 9.2 本轮回执的三处口径更正（复审 §4／§5）

1. **自检项数是 18，不是 17**：`sw1_cut.ps1` 实有 18 个 `Check` 调用，`selftest.txt` 亦为 18 条 `OK`。
   本文件与 `NOTES.txt` 第 21／29 行、`a5a5026` 提交信息写的"17 项"**作废**（原始文件不改写，以本节为准）；
   非白名单守卫实为**第 18 项**。
2. **"本轮预检"不成立**：`run_identify_23`／`run_cut_1` 两份完整会话里**没有 `pin num`，也没有 HIGH 之前的
   `motor_status`**（唯一一次在 C 段末尾）。§2 的"预检：映射 `PC.23:87`/`PC.6:70`、`armed=0 cur=0 tgt=0`"
   只能按**引用上一轮 `4edcf5e` 的记录 + 本轮读回标签 `pin[87]`/`pin[70]` 的间接对应**来读，
   不是本轮独立预检；脚本入口也未机械强制这些前置。此处**不声称**在证据之外执行过任何动作。
3. **时间字段语义**：CSV 的 `2609`/`2671 ms` 是**开始读该对 GPIO 之前**的循环时刻，不是 `IO70` 到达时刻；
   `117`/`120 ms`、`2733`/`2735 ms` 是计时与日志位置差异，都远在本轮预算内，**都不是硬件响应时间**。
   另：B 段只做了一侧短探针（`2–3`），另一侧由 C 段动态观察到 —— 不能写成"两侧短探针均已执行"。

### 9.3 工具的源码级缺陷（本地按行复核，与复审一致；本轮未修、未跑）

- **T-01**（`sw1_cut.ps1:264-281`）：`cutPairs` 只累计不重置 ⇒ `HIGH/HIGH → HIGH/LOW → HIGH/HIGH`
  这种**不相邻**的两组也会判 `CUT_OBSERVED`，不满足"两组相邻一致阻断态"。本次真实日志最后两组确实相邻，
  故不推翻本次观测；复用前必须重置连续计数并离线覆盖该分隔序列。
- **T-02**（`sw1_cut.ps1:447-455`）：Cut 模式按 `Do-Cut` 定 `rc` 后，`Read-Pair 'terminal'`／`Query-Motor 'terminal'`
  **只写台账**，不检查 `ok`／两脚期望电平／`armed/cur/tgt`，异常终态不降级 `rc` ⇒ "任何终态异常必非零退出"不成立。
- 其余：`Query-Level` 未锚定整行且首匹配即返回；部分 `Ad-Poll` 接收（含开口初始接收）只存档不查 DISQUALIFIERS，
  故"新启动信息一律拒绝"过强；`cut-no-low-before-evidence` 只比第一个 `LOW`/`HIGH` 的发送先后；自检的串口异常场景
  自带 catch/cleanup，未覆盖 `main` 全部退出判定；`rx_full.txt` 是解码文本且 CR/LF 统一为 `\n`，**不是逐字节原始采样**；
  3 次早期自检失败仅事后描述、无原始输出（`AGENT_REPORTED`）。

### 9.4 §8 那句"下一步是五点静态读数"已被同日复审覆盖

复审 §8.1 明确：不再把五点读数列为接受静态验收的**强制前置**，也不要求用户重复提交。
真实缺口转为 **RISK-04-b / RISK-03、03a**（新固件上板验证方案、复位瞬态与 STEP 波形的仪器测量、
无电机条件下的停机时延分布），都需要**新的具体范围 + 用户明确授权**（草案 J.4）。

### 9.5 边界（本节）

差异**只有文档**：草案 v0.4.2（A29/A30/A31、X-5、L1/L2 覆盖说明、C9 子项、D-4/P-2 收窄、J 节）、
本交接 MD/JSON、BSP `待办事项.md` 与 `开发进度.md`。**未**改源码/测试/Keil 工程/`.gitignore`/任何宏；
**未**改 `4edcf5e`／`f5f7cbf` 轮的原始 transcript、台账、`NOTES.txt` 或脚本；
**未**执行 PowerShell／串口／GPIO／TMC／烧录／复位／断电／接线／电机动作；未跑主机回归与 Keil（无需：无代码变化）。
三道硬件宏与 `SAFETY_LIMIT_INPUTS_ENABLED` 仍 `RT_FALSE`；板上固件身份仍 `USER_REPORTED_E40`；
`METER_NOT_MEASURED_THIS_RUN`／`ANALOG_NOT_VERIFIED` 在两次采集记录内**原文保留**，其含义限定为"那两次 Agent 采集没有仪表数据"。

**STOP：`AWAITING_USER_DECISION`。** 本轮零硬件动作、未关闭任何动态/时序类风险、未开宏。
