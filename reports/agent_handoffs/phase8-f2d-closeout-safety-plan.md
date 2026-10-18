# Phase 8-F2d 软件收口 + 板级安全链验收方案 交接记录

日期：2026-10-19（北京时间）。任务 ID：`phase8-f2d-closeout-safety-plan`。
分支：`phase8/tmc-drive-config`。性质：**文档更新与方案设计**，不启动 F2e/F3，不是新一轮工具返修。

## 1. 基线与 SHA 口径

| 项 | 值 |
|---|---|
| 复审对象 / 本轮编写基线 | `32f3a416bdf8c20e96d3ae41f314c236c96b5d61`（远端回读一致，工作区开始时 clean） |
| 本轮文档提交 SHA | 不在本文件内自引用；推送后在交付消息中给出 |
| 冻结对象 | `main = 1b3757ee…`、`phase7/full-embedded-software = 6d3fbc55…`、标签 `v0.7-…` → 对象 `097935fe…` / 固件 `e40a9b95…`，全部未动 |
| 交付方式 | 仅 GitHub，无 ZIP |

## 2. 实际做了什么

1. **写入 `SOFTWARE_REVIEW_GO` 收口结论**（内容来自用户转交的 ChatGPT Work 复审结论，不是本 Agent 自评，
   也未虚构报告 URL）：BSP `开发进度.md` 新增"2026-10-19 · Phase 8-F2d 软件收口"小节；
   `README.md` 在证据等级段后加"2026-10-19 收口"段；`待办事项.md` 在"已完成"顶部加两条并把
   更新行延伸到 10-19。历史记录一律保留，未把旧的"当时待审"批量改成通过，只在 F2d-R4 小节标题
   追加带日期的覆盖说明。
2. **新增板级安全链验收方案草案** `reports/plans/phase8-safety-chain-acceptance-draft.md`：
   A 事实与来源表（20 条，逐条标 `[S]` 源码静态 / `[M]` 历史现场测量 / `[D]` 历史文档断言 / `[?]` 待确认，
   并给 `路径:行号`）；B 信号与引脚职责表（含 ENN 极性/真值表 `TBD`，不猜测试点、电压、阻值、时序）；
   B1 单列 `IO70` 语义冲突与 `H2` 从未定义；C 分层验收矩阵 L0–L3（每个用例给前置条件、授权要求、
   观测位置、预期依据、证据形式、失败停止条件，执行状态全部 `NOT EXECUTED`）；C9 说明 RISK-04 与
   RISK-03 各自需要什么证据；D 需用户补的 6 项最少材料；E 本轮没做的事。
3. 撰写前只做**只读**调查：`project_board.h` 引脚冻结表、`safety_gpio.c/.h`（写+回读唯一入口、
   上电表、FAIL CLOSED 路径）、`motor.c/.h`、`step_pwm.c`、`tmc2209.c/.h`（三宏、运行态门禁、
   apply 不碰使能链）、`PHASE8_HARDWARE_VALIDATION_20260925.md`、`PHASE7_PROGRESS.md`、
   `README.md`、`开发进度.md`、`待办事项.md`、`调试记录.md`。

## 3. 本轮实际执行的检查（非硬件、非重跑测试链）

| 检查 | 命令/做法 | 结果 |
|---|---|---|
| 基线一致 | `git rev-parse HEAD` + `git ls-remote origin …` | 本地 HEAD = 远端 = `32f3a41…`；`main`/冻结分支/标签一致；开始时工作树 clean |
| 改动范围 | `git diff --name-only` 白名单核对 | 仅 6 个允许路径（3 份阶段文档 + 1 份草案 + 交接 md/json），无其他文件 |
| 生产与工具零改动 | `git diff --name-only 32f3a41..HEAD -- applications project.uvprojx tests/host` | 0 个文件 |
| 三宏取值 | `grep` 源码定义 | `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` / `STEP_DIAG_ISOLATED_ENV_CONFIRMED` / `TMC_DRIVE_CONFIG_HW_VALIDATED` 均 `RT_FALSE` |
| 提交卫生 | `git diff --check` | 无空白错误 |
| 相对链接可定位 | 从 BSP 目录解析 `../../../../../../reports/plans/…` | 文件存在，链接可达 |
| 风险编号一致 | 对照 `待办事项.md` §3 与 `开发进度.md` 同日小节 | RISK-03 / RISK-03a（RISK-03 的表现）/ RISK-04（硬件安全链）三处口径一致，无编号互换 |
| 提交身份 | `git config --local user.email` + 提交后 `%ae %ce` | 均为公开 noreply；global 与 GitHub 账户设置未动 |
| 隐私 | 扫描新增/修改文档 | 无私密邮箱、无本机用户名路径 |

按任务书要求，本轮**没有**重跑 V1/V2、负例/元负例链，也**没有**执行 Keil。

## 4. 未执行 / 保持开放

- 草案 L0–L3 全部 `NOT EXECUTED`；RISK-04、RISK-03、RISK-03a 继续开放，未因写方案而关闭。
- 真机项全部仍 `NOT TESTED`：UART 超时与真实寄存器读写、优先级继承、motor 线程栈高水位、
  停机时延上界、`rt_pwm_disable` 返回值、ENN 端电压、STEP 波形、板上自测。
- 历史真机记录（Phase 7 冒烟、Flash/IMU/ADC/TMC 串口）保持原样，既不被本轮抹去，也不算本轮结果。
- Keil 证据仍是 F2d-R2 的历史构建日志，未重编译，不得记作新构建。
- 哈希清单只覆盖 `tests/host` 的 14 个文件，不是全仓或 `reports/` 的哈希证明；
  MSYS 目录软链/junction 落点识别未被证明，哨兵只降低误删风险。

## 5. 草案里登记的疑点（需要人或图纸定论，源码给不出）

1. **位号冲突**：与门到底是 `U5` 还是 `U9`（README 称 `U9` 为历史遗留未核实，同时又称与门是 `U5`）；
   急停非门无位号；`Q1` 型号与 `R10`/3V3 上拉参数无仓库内依据。
2. **板版本自相矛盾**：`PHASE8_HARDWARE_VALIDATION_20260925.md:5` 称当前板为 RevA，`:38-40`
   又称 RevA 是旧板、现用"新扩展板"且网络已引出。板版本不明 → 不签发可执行接线表。
3. **原理图缺失**：`README.md:32` 引用的《硬件扩展板详细方案_RevA.docx》在仓库中不存在。
4. **`TMC_ENN ≈ 0 V`**（2026-10-18）在文档中没有测量点/仪器/日期表 → 只能按历史断言处理。
5. **`IO70` 语义冲突**：源码里 `IO70/GPIO70` 只对应 `PC.6`（急停感测），而 `README.md:110`
   点名"IO70 语义冲突"却无解释，另一读法把它与使能脚（文档中的 IO87/`GPIO87`）混用。
6. **`H2` 从未定义**：`README.md:110` 与 `tmc2209.c:640`、`tmc2209.h:74` 写"H2 限位未接"，
   但仓库没有任何地方定义 `H2` 是什么；主机夹具里的 `H2` 是审核条目编号，属同名巧合。
7. **急停/限位极性从未通断实测**：`PHASE8:84-85` 明写"safe=LOW / 触发=HIGH / RISING 仍是未经实测的
   假设"，`:149-150` 记 `NOT CONFIRMED` 与 IRQ 门 `CLOSED`。
8. **待办清单存在过期项**：`待办事项.md:45` 的 on-target 冒烟仍未勾选，而 `PHASE7_PROGRESS.md:172`
   记 2026-09-25 已完成 —— 只登记，不改写原文。
9. **仪器缺口**：无示波器/逻辑分析仪时，L2 的 D1–D3 与 L3 时延测量无法形成有效证据。

## 6. 停止点

本轮交付到"文档 + 草案已推送并远端回读"为止。**不自动执行方案中的任何步骤**：
烧录、复位、连接或操作 COM5、向真实 TMC2209 发读写、接电机、输出 STEP、通电/断电测量、
改线、飞线、换器件、翻转宏、修改安全门禁都仍需用户分别明确授权。
不进入 F2e/F3，不开展新一轮工具返修。下一步等待 ChatGPT 对草案的独立审查与用户对材料的补充。
