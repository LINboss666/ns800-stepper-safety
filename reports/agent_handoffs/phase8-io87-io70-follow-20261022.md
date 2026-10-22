# 交接：IO87 → ENN / IO70 高低跟随检查（PHASE8-IO87-IO70-FOLLOW-20261022）

任务书：`NS800_IO87_IO70_Follow_Qoder_Task_20261022.md`　日期：2026-10-22（北京时间）
性质：**用户已授权的限定 GPIO 诊断例外**（不是 `motor_arm`、不是运动测试、不是正式解除软件门禁）。
结论：**`GPIO_FOLLOW_PASS` + `ANALOG_NOT_VERIFIED`**；RISK-03/03a/04 继续开放。

## 1. 分支 / 基线 / SHA / 范围 diff

| 项 | 值 |
|---|---|
| 分支 | `phase8/tmc-drive-config` |
| 执行前基线 | `a3434353fc5de3c10eb48318d4031568ff761323`（本地 HEAD = `git ls-remote` 远端，工作树 clean，无并行改动） |
| 软件 GO 对象 | `296c369607ea69c201f797b80950b76ff312c0e0`（保持不变，未改成文档提交 SHA） |
| 冻结对象 | `main=1b3757ee…`、`phase7=6d3fbc55…`、`v0.7 标签=097935fe…`/`e40a9b95…` 全部未动 |
| 范围 diff | 只新增本交接 MD/JSON + `reports/reviews/phase8-io87-io70-follow-20261022/` 下 4 个文件；`applications/`、`tests/`、`project.uvprojx`、`.gitignore`、任何宏/门禁 **0 改动** |
| 本机 OS / 工具 | Windows 11（10.0.26100）+ Git Bash + `powershell -NoProfile -File`（脚本纯 ASCII，避开 PS 5.1 按 ANSI 解析中文的问题） |

## 2. 现场前提、固件身份与实际使用的命令/脚本

- **现场前提**（2026-10-22 20:32 执行前由操作者当场确认，非旧记录）：电机物理未连接、TMC 模块在位、
  供电正常、操作者在场。用户答复原文记录为 `USER_CONFIRMED_LIVE`。
- 板上固件：**`USER_REPORTED_E40`**（`e40a9b95…`）。本轮没有读回 Flash 哈希/版本标识，不作已由硬件校验。
- 命令能力核对（读 e40 固定版本源码，未凭空假设）：
  `dev_pin.c:382 MSH_CMD_EXPORT_ALIAS(_pin_cmd, pin,…)` 在 `RT_USING_FINSH`（:176）内，
  e40 `rtconfig.h:121/:157` 开启 `RT_USING_FINSH` + `RT_USING_PIN`；
  `pin read` 打印 `pin[%d] = high|low`（取 `rt_pin_read`）；**`pin write` 无任何打印**（只调 `rt_pin_write`）
  → 因此"提示符返回 ≠ 写成功"，本轮一律以 `pin read` 实际读回为准。
  BSP 侧 `drv_gpio.c:153` 表项 `[87] = PIN_ENTRY(GPIO_87)`；运行时映射确认 `PC.23 : 87`、`PC.6 : 70`。
- 实际使用脚本：`io87_follow.ps1`（本机一次性采集工具，已入证据目录；不做删除目录/调用构建/后台重试）。

## 3. 逐态原始读数与三轮完整性

预检：`pin num PC.23 → 87`、`pin num PC.6 → 70`、`motor_status → armed=0 cur=0 tgt=0`（`state=4`＝既有 MOTOR_FAULT，
未清除、未调用 `motor_arm`）、初态 `pin[87] = low`、`pin[70] = high`。
B 段先证明 LOW 与采集能力：`pin write PC.23 low` 后读回 `low` / `high`。

| 轮 | 激励前 (IO87/IO70) | HIGH 窗口 (IO87/IO70) | 撤 LOW 后 (IO87/IO70) | HIGH→LOW 发令间隔 | 是否完整 |
|---|---|---|---|---|---|
| 1 | low / high | **high / low** | low / high | 199 ms | 完整 |
| 2 | low / high | **high / low** | low / high | 229 ms | 完整 |
| 3 | low / high | **high / low** | low / high | 201 ms | 完整 |

30 条 REPLY 全部与命令配对；无超时、无 `command not found`、无丢样、无异常状态跳变；
未触发任何停止条件（`DRIVE_NOT_ESTABLISHED` / `ENABLE_PATH_NOT_OBSERVED` / `DISABLE_SIDE_MISMATCH` 均未出现）。
**间隔数字是主机操作预算**（上限 2000 ms），不是 RT-Thread/硬件停机时延证明。

## 4. 终态与清理

- §D 终态（同一会话）：`pin[87] = low`、`pin[70] = high`、`motor_status armed=0 cur=0 tgt=0`。
- `finally` 清理路径实际执行：`FINAL_STATE_LOW_CONFIRMED`（`pin write PC.23 low` → 读回 low/high）。
  未出现"首次 LOW 无法确认需补发"的情况，也没有 `FINAL_STATE_UNKNOWN`。
- 串口随后关闭，未追加任何其他测试。主机 finally 无法保证断线/崩溃时必然执行，这一点如实登记。

## 5. 判定与未证明的内容

- **`GPIO_FOLLOW_PASS`**：仅证明本次数字逻辑跟随 —— 许可 HIGH 时 ENN/IO70 到使能侧（LOW），
  许可 LOW 时回到禁能侧（HIGH）；三轮一致。**不**证明线圈电流、转矩或运动。
- 可推出的观察：三个 HIGH 窗口内 ENN 被拉低 ⇒ `DRV_ENABLE_SAFE`=1 ⇒ **`ESTOP_OK` 当时为高（与门放行）**，
  与"非门一直输出 0V"的猜测不相符。但 `ESTOP_OK` 为高的原因（常闭触点是否真按图接好，还是有别的路径把它拉低）
  **本轮未测**，实体触点形式仍是 D-4/P-2 缺项 → **不得**据此宣布急停接线正确或急停独立切断已验证。
- 附加观察：IO70 随 IO87 同步翻转 3/3，在数字层面与"反馈支线完全断路"不相容；但这仍**不是**连续性证明。
- **`ANALOG_NOT_VERIFIED`**：本轮无仪表读数。MCU 数字 high/low ≠ ENN 电压合格；
  此前记录的 1.6–1.7 V 异常不因读到 high 而自动消失。Rev.1.09 以 VIO=3.3 V 为参考的逻辑界限
  （LOW ≤0.99 V、HIGH ≥2.31 V）只是界限，本轮未实测任何电压。
- 未测/未通过不被关闭：RISK-03/03a（时延与阻塞）、RISK-04（换件后整链）、复位瞬态、STEP 波形、
  反馈支线物理连通性、急停独立切断、运动验收。
- **不**声称"VM 未供电是此前异常的根因"（用户假设按 `USER_REPORTED` + 待验证登记；本轮未切换 VM 复现）。
- **不**声称通过 TMC 配置写 / IFCNT 握手（本轮未发任何 TMC UART 命令；MCU GPIO 命令与 TMC 寄存器 WRITE 是不同动作）。
- 直接写 GPIO 绕过软件请求态记账，故内部 `request/verified` 字段一律不作为焊盘证据。

## 6. 证据路径与实际硬件动作/偏差

- `reports/reviews/phase8-io87-io70-follow-20261022/transcript.txt`（发送命令 + REPLY + 主机时间）
- `.../state_ledger.csv`（17 行逐态台账，含轮次/阶段/期望/实际/HIGH 间隔/清理结果）
- `.../io87_follow.ps1`（实际采集脚本，本地路径脱敏为 `<WORK>\io87_follow`）
- `.../NOTES.txt`（命令计数、离线解析器自检 9 项合成用例、边界与偏差）
- 本文与同名 `.json`

**实际硬件动作**：打开 COM5 → 发送白名单 `pin`/`motor_status` 命令 → 对 `PC.23` 写 LOW/HIGH（HIGH 共 3 次，
每次 ≤230 ms 即撤 LOW）→ 对 `PC.23`/`PC.6` 读取 → 清理确认 LOW → 关闭端口。
**未做**：烧录、调试器下载、复位、改供电、热插拔模块、改线、换件、接电机、输出 STEP、写 `PC.6`、
`pin mode`、`pin_status`、`runtime_selftest`、`tmc_uart_probe`、任何 TMC UART 命令、
`motor_arm`/start/target/dir/`pwm_test`、故障清除、IRQ 开闸、人工极性确认、任何宏置 TRUE、merge/force/改写历史。
偏差：预监听 drained 286 字节未作证据也未定性；`state=4` 既有故障态本轮未清除；本轮无失败轮次。

## 7. STOP

**`AWAITING_INDEPENDENT_REVIEW`。** 本任务的 GPIO 授权**到此为止**，不延伸成日常绕过门禁的控制方式；
不自动进入 F2e/F3、不烧录新版、不开宏、不接电机。`GPIO_FOLLOW_PASS` 只是数字跟随证据，
物理层（电压、瞬态、时延、急停独立切断、反馈线连续性）仍待用户决定是否安排带仪表的下一轮。
