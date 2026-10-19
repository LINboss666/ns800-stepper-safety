# Phase 8 安全链验收草案 v0.2（文档修正）交接记录

日期：2026-10-19（北京时间）。任务 ID：`phase8-safety-plan-doc-fix`。
分支：`phase8/tmc-drive-config`。性质：**纯文档修正**（按独立审查 PLAN-01～PLAN-04）；
不是 F2d 工具返修，不启动 F2e/F3，**没有任何硬件授权或硬件动作**。

## 1. 基线与 SHA 口径

| 项 | 值 |
|---|---|
| 本轮起点 / 编写基线 | `f52f6374a36847b7e2f719eb1fa6cdab0a85e6dc`（v0.1 草案 + F2d 软件收口；开始时本地 HEAD 与远端一致、工作树 clean） |
| 代码复审对象（保持不变） | `32f3a416bdf8c20e96d3ae41f314c236c96b5d61`（`SOFTWARE_REVIEW_GO`，限定软件与主机验证工具范围） |
| 本轮文档提交 SHA | 不在本文件内自引用；在推送后的交付消息里给出 |
| 冻结对象 | `main = 1b3757ee…`、`phase7/full-embedded-software = 6d3fbc55…`、标签 `v0.7-phase7-software-board-validated` → 对象 `097935fe…` / 固件 `e40a9b95…`，全部未动 |
| 交付方式 | 仅 GitHub 普通提交 + 普通 push，**无 ZIP** |
| 复审意见来源 | 用户转交的 `Phase8_Closeout_SafetyPlan_Review_and_Doc_Fixes.md`（ChatGPT Work 独立审查）。本 Agent 未访问用户与复审方的完整对话，不虚构报告 URL |

## 2. 本轮实际做了什么（对应 PLAN-01～04）

1. **PLAN-01 事实继承**：把 2026-10-18 交接已记录的实物信息恢复为独立来源等级 `[U]`
   （H7-1≈3.3 V、H7-10≈0 V、Q1 Gate≈3.3 V、`TMC_ENN`≈0 V、"U5 输入 A/B≈0 V 而输出≈3.3 V、
   判与门与急停非门异常"），并逐条标注**本轮未独立测量、未独立核图**，不当作新的板级 PASS。
   撤回 v0.1 的"IO70 一律按 `PC.6` 急停感测处理"，改写为真实冲突（实物按 ENN 反馈接线 vs
   软件仍按急停输入解释 vs 图纸两者都没有）；撤回"`H2` 未定义"，改为"H2 已由图纸与交接定义为
   限位/LED/蜂鸣器连接器，现板未插接 → 限位保护能力仍为 0，暂缓 ≠ 风险关闭"。
   新增 **A2. 冲突登记表 X-1～X-4**，冲突不择一采信。
2. **纳入原理图（新增来源等级 `[G]`）**：用户 2026-10-19 提供 `SCH_Schematic1_2026-10-19.pdf`
   （标题栏：板子 `NS800_Stepper_Safety_RevA`、图页 `Schematic1 / P1`、版本 `V1.0`、创建 2026-08-16、
   更新 2026-10-19、嘉立创EDA，单页；SHA-256 `7b5d5325d4f73f43da916149d1972e95a048d6138ea83f98476f5d2fd5b05a0f`，
   305 535 字节）。**该 PDF 未入库**（本轮允许路径不含它），仅以哈希固定引用。
   读出的定论：与门 = **U5 / QN74LVC1G08DBVR**；**U9 = TMC2209SILENTSTEPSTICK 模块本体**（"U9 是与门"作废）；
   急停反相器 = **U13 / SN74LVC1G14DBVR-MS**，限位反相器 U12/U11 同型号；
   `DRV_ENABLE_SAFE` → R9(1 k) → Q1(BSS138) 栅，R8(100 k) 栅-源下拉，Q1 漏极 = `TMC_ENN`，
   R10(10 k) 上拉到 3V3；急停 = 常闭触点 → `ESTOP_NC` → R6(1 k) → `ESTOP_SENSE`（R5 10 k 上拉 + C7 100 nF）
   → U13 → `ESTOP_OK` → 与门；`MCU_DRV_ENABLE` 节点有 R7(100 k) 到 GND；**`TMC_ENN` 未引到任何 MCU 连接器**；
   两路限位 `LIMIT_*_OK` 引到 H2-1/H2-2，**限位不进硬件与门**。据此新增 **B2 使能链真值表**，
   并明确它是**逻辑推导而非实测**。
3. **PLAN-02 命令副作用**：L2 的 D6 从"板上自测、纯只读、预期全通过"改写为**副作用分级表**，
   逐项标注"真实 UART 读芯片？/ 改状态机？/ GPIO-PWM 影响 / 适用配置状态 / 板上冻结固件里有没有"；
   未授权命令一律 **DEFERRED**，不列入"静态安全链首次检查必跑项"，本轮不新增诊断入口。
   事实依据：`APP/supervisor.c:209-334`（投递 `EVT_SOFT_FAULT` → `FAULT_LATCHED` → `safety_fault_reset_manual`
   → `MANUAL_CLEAR` → 重跑 `safety_run_selftest()` → 可能 `safety_force_shutdown`）、
   `APP/motor.c:898-924`（probe 真实读 GCONF/CHOPCONF，失败把生产配置打成 `FAILED`）、
   `APP/motor.c:880-889` 与 `APP/tmc2209.c:2250`（两个 `*_selftest` 是离线假总线/纯判定，明示"不产生任何 UART 流量"）、
   `APP/tmc2209.c:1302-1306`（未 `CONFIGURED`/快照无效时先返回 `-RT_EPERM`，在真实读寄存器之前退出 →
   **不得要求 `integrity=0`，不得为拿 PASS 先写真实配置**）。
   另按 A24 实际比对 `git show e40a9b9:…applications` 与 HEAD 的 `MSH_CMD_EXPORT` 集合：
   `motor_drv_guard_probe`、`motor_drv_guard_selftest`、`tmc_cfg_selftest`、`tmc_calc_selftest`、
   `tmc_config_apply`、`tmc_config_show` **只存在于分支 HEAD**（HEAD 59 条 vs 冻结固件 53 条），
   且冻结版 `runtime_selftest` 不含 Phase 8 的 2e/2f 两步（`SELFTEST` 行数 25 vs 32）→
   **板上仍是冻结固件，重新烧录不是默认前置动作**。
4. **PLAN-03 时延**：L3 拆成三段（故障实际发生/受控注入 → 检测完成；检测完成 → 关断请求；
   关断请求 → STEP 实际停止且 `TMC_ENN` 到禁能侧），并声明**重复测得分布不能单独证明最坏时延上界**，
   必须同时说明阻塞路径如何受限；列出未解决的 `RT_WAITING_FOREVER` 路径
   （`APP/tmc2209.c:282`、`APP/motor.c:102`、`APP/safety_state.c:100/129/165/276`、`APP/project_config.c:32`）；
   目标上界数值仍"待批准/待测"，不据此启动线程重构。
5. **PLAN-04 前置条件**：D3 判据改为"**实际 `TMC_ENN` 始终处于禁止侧**"（初始化顺序不保证复位保持期与
   启动代码前的焊盘电平，`PC.23` 复位态需 NS800 文档 + R7 外部下拉共同判定）；D2 标 **BLOCKED_METHOD**
   （三宏 `RT_FALSE` 且不绕过门禁时没有合规的许可激励方法；不得用 `motor_arm`、临时命令、裸 GPIO 写或
   跳线绕过代替）；C1 的历史 60–70 kΩ / ≈5 kΩ 降为"不同装配/测量路径的历史参考，非现板合格值"，
   L0 未定稿前不指定 Q1 引脚与连通判据；C4 升级为**任何急停/限位刺激用例的前置条件**，
   对已判异常器件的复测以用户是否继续维修为前提，不重复催修。
6. **官方依据补齐（只补文档，不改引脚命名或安全代码）**：TMC2209 Datasheet **Rev.1.09，2023-02-16**
   印刷第 9 页 §2.2（ENN 低有效使能输入，拉高则功率级关断、输出浮空；低电平不能断言存在转矩或一定转动）、
   印刷第 24 页 GENERAL CONFIGURATION REGISTERS（IOIN `0x06` bit0 = ENN），官方 PDF 链接写入草案与
   BSP `README.md`。据此**从"需用户补材料"里删除数据手册页码项**。
7. **阶段文档口径同步**：BSP `README.md`（扩展板条目 + 构建段硬件阻塞项条目 + 位号说明条目，共 3 处
   带日期覆盖）、`开发进度.md`（修改时间行、阶段总表该行状态补充、新增"草案 v0.2"整段、对同日
   软件收口段的 v0.1 口径加覆盖说明）、`待办事项.md`（更新行、覆盖说明第 3 条 RISK-04 补充、
   🔴 首条材料清单改为 5 项并保留 v0.1 原文注记、"已完成"顶部新增一条）。
   **不删除历史测量、不撤销 `SOFTWARE_REVIEW_GO`、不把旧的"当时待审"批量改成通过。**

## 3. 本轮实际执行的检查（非硬件、非重跑测试链）

| 检查 | 做法 | 结果 |
|---|---|---|
| 基线一致 | `git rev-parse HEAD` / `--abbrev-ref` / `git status --porcelain`（开始前） | HEAD = `f52f6374…`，分支 `phase8/tmc-drive-config`，开始时工作树 clean |
| 改动范围 | `git status --porcelain` + `git diff --name-only` | 仅 4 个允许路径（草案 + 3 份阶段文档）+ 2 个新增交接文件；无其他文件 |
| `git diff --check` | 直接执行 | rc=0，无空白错误（仅有 autocrlf 的 LF→CRLF 提示，属既有仓库行为） |
| 生产与测试工具零改动 | `git diff --name-only f52f6374 -- applications project.uvprojx tests/host reports/reviews` | **0 个文件** |
| 三宏取值 | `grep` 源码定义 | `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED`(`motor.h:38`) / `STEP_DIAG_ISOLATED_ENV_CONFIRMED`(`motor.h:48`) / `TMC_DRIVE_CONFIG_HW_VALIDATED`(`tmc2209.h:92`) 均 `RT_FALSE` |
| 相对链接可定位 | 从 BSP 目录解析 `../../../../../../reports/plans/phase8-safety-chain-acceptance-draft.md` | 可解析到实际文件（README 3 处、开发进度、待办 2 处） |
| 草案状态 | 关键字计数 | `NOT EXECUTED` 出现 15 处；状态行含 `DRAFT` / 尚非可执行验收单 |
| 风险编号一致 | 草案 / 开发进度 / 待办 对照 | RISK-03、RISK-03a、RISK-04 三处编号与含义一致，未互相覆盖 |
| 提交身份 | `git config --local user.email` | 仓库 local 身份为公开 `237816241+LINboss666@users.noreply.github.com`；global 身份未读取入交接、未修改 |
| 原理图引用 | `sha256sum` + 标题栏文本 | 哈希与标题栏（板子/版本/日期）已写入草案与交接；PDF **未入库** |

## 4. 未执行项（如实记录）

- 未重跑主机验证链（V1/V2、13 例负例、8 组元负例/对照、路径守卫沙盒）；未编写或修改任何验证脚本。
- 未运行 Keil，未重建固件；沿用 F2d-R4 交付的 R2 历史构建日志，不冒充本轮新构建。
- 未做任何硬件动作：未烧录、未复位、未连接或操作 COM5、未向真实 TMC2209 发读写、未接电机、
  未输出 STEP、未通电测量、未断电测量、未改线/飞线/换器件、未翻转宏、未修改安全门禁。
- 未 merge、未改 `main`/冻结分支/标签、未 force push、未改写已推送提交、未生成 ZIP。
- 未把原理图 PDF 或解析脚本入库；本轮解析脚本是一次性读取工具，不作为交付物。

## 5. 图纸结论的限制（必须与结论一起读）

- 读的是 PDF **文本层**（本机无 `pdftoppm`，poppler 缺 `Adobe-GB1 / UniGB-UCS2-H` CMap，故自写脚本
  解析对象与内容流，得到 603 条带坐标文本项）。**没有网表，没有整页图形目视核对**。
- 已定论：器件位号、型号、引脚名/号、网名、阻值/容值。
- 高置信推断：双列连接器的网标↔引脚号归属（该几何规则在 **H7**（pin1=3V3、pin10=`MCU_DRV_ENABLE`）
  与 **H2**（pin1/2=两路限位、pin3/4/5=灯、pin6=蜂鸣器）上与两处**独立的**用户交接读数/描述吻合）；
  **H6（模块座）无独立佐证**，其 pin1=STEP/pin3=DIR/pin5=ENN/pin7=DIAG/pin9=UART 仅作待核推断。
- 不确定：单列连接器（H3/H4/H5/H9/H10）的引脚编号归属、导线实际连通性、跨页连接与"未连接"关系。
- **所有 `[G]` 结论都带前提**：现用板是否即该 `RevA / V1.0` 图纸尚未确认（X-3）。读图 ≠ 实测。

## 6. 仍待用户确认的材料（草案 D-1～D-5；执行授权已单列 E 节，不混列）

1. 现用板与该 `NS800_Stepper_Safety_RevA / V1.0`（更新 2026-10-19）图纸的**版本对应关系**（X-3）。
2. 可核对**连线归属**的网表 / BOM+引脚连线表 / 能看清走线的放大截图（D-2）。
3. **`PC.6` / `IO70` 的实物接线确认**：现在到底是急停输入还是 `TMC_ENN` 反馈、接在哪对端子（X-1、D-3）。
4. 急停与限位**外部触点形式**（NC/NO、是否已接；H2 现板未插）（D-4）。
5. **仪器条件**：示波器/逻辑分析仪是否可用（当前无示波器）、万用表型号（D-5）。

## 7. 停止点

草案 v0.2 已按 PLAN-01～04 修订并完成文档同步，普通提交并普通 push 到 `phase8/tmc-drive-config`，
`git ls-remote` 回读完整 SHA 与本地 HEAD 一致后 **STOP**：

- 草案状态仍为 `DRAFT` / `NOT EXECUTED` / **尚非可执行验收单**，等待独立审查；
- **不自动执行方案**，不进入 F2e/F3，不启动新一轮工具返修；
- 实际硬件检查（断电检查、无电机上电检查、时延注入测量）与任何硬件动作都必须在可执行方案形成后
  由用户**分别**明确授权。
