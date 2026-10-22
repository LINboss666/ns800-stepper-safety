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
