# 交接：M8-R7F8F 返修+整包离线门轮（2026-11-05 起，含 A 的预算裁定落地、F11/F20/F22 三次真机与 F21 补齐）

**状态：OFFLINE_GATE_ALL_GREEN / 真机已跑三次，三次都在只读查询步首错停止（F11 第 1 步
`system_status` 缺披露申报、F20 第 2 步 `motor_status` 缺门位申报、F22 第 6 步 `tmc_config_show`
的 `expect_contains` 写了产品不打的 `[TMC]` 前缀）/ 三轮首错全在**包侧申报**，产品一次都没给出
预期外的行 / 电机从未带电、从未起转 / F22 是本会话第三次也是最后一次已批准的尝试，那本账
`port_open` 1/1 已用满 / 下一步是 **F23 包侧修正（离线，0 设备动作）**，见 §12 末**
**基线 `d6bbc1b5795f`；分支 `feature/m8-r7d1-async-learn-offline`；
任务书 `reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8F_PKG/R7F8F_REPAIR_AND_FIELD_TASK.md`；
预算裁定 `…/inputs/A_R7F8F_BUDGET_PKG/R7F8F_A_BUDGET_RULING.md`。**
机器可读同源件：`reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.json`
（数字当场从盘上算，与本文件同源；两处不一致以重新生成一次为准）。

## 1. 一句话

前半轮按 A 的 F1~F6（含 F5b/F5c 两项实测缺口）把派生执行器修到"该停的停在写之前、该收口的五条
都拿得到自己的窗"，并把静态预算拆三段后**留下两条真红交 A 定口径**；后半轮把 A 的预算裁定
（可信零速结束运动计时 + 显式单步总窗）落进执行器、计划、行表、判据与本交接，**两条红因此转绿**，
整包离线门 47 条全绿、validate-plan 146 项 0 红（**当时的条数**；F19/F21 追加判据后是 49 条 /
147 项，见 §10 与 §11）。取得操作者真实原话令牌后跑了**一次**真机
整包：唯一一次开口只写出一条只读查询，产品回的 `system_status` 门位披露行被我漏申报，于是按
业务失败线在第 1 步停止（0.156 s）；`motor_arm / motor_start / learn / apply` 一条都没发出去，
电机从未带电。现场事实与根因见 §7bis 与 `run_record.md` §25。
补完 F19（包侧披露申报 + 真字节回放，§8）后又跑了**第二次**真机：第 1 步这次通过了——F19 的申报
在真机生效——但同一个混淆形状出现在第 2 步 `motor_status` 上，于是在两条只读查询之后停止，
一条业务命令都没发（§9）。F21 按 A 的三条补齐做了三件事：把**全部 10 处状态查询步**的预期门位
mask 逐条钉成机器可判的契约（预期为 0 的 4 步一条豁免都不给）、把历史真实字节接进回放与判定链并
补跨计划继承门、以及把批准令牌的三份摘要绑成开口前的硬门（§10）。那一轮自己设备动作 0。
F21 的新身份交现场后又跑了**第三次**真机（F22，§12）：F21 的门位契约把前两轮那两处首错都
放过去了，前 5 步只读查询逐字节通过；第 6 步 `tmc_config_show` 因我给 `expect_contains` 写了
产品根本不打的 `[TMC]` 前缀而停止 —— 这是三轮里最远的一次，`tmc_config_apply`、DIR、arm、
起转、学习、commit、stop/disarm 一条都没发（台账 `actions_attempted` 为空）。三轮首错都落在
**包侧申报**上，产品没给过任何预期外的行，故按停止-报告口径未动产品源码、未重构建。

## 2. 交付件（同一目录 `reports/reviews/phase8-m8-r7f8-executor-adaptation/`）

| 件 | 身份 | 作用 |
| --- | --- | --- |
| `tools/m8r3_serial_chain_r7f8.py` | 328475 B / `4363af36159b…` / 5735 行 | 派生执行器（F1~F6 + F12 + 裁定一/二 + F19 的 G4/G5 两项豁免门 + **F21 的 H0/H1/H2 门位契约与 T1~T7 令牌链**） |
| `tools/r7f8f_build_executor.py` | 83 处逐字锚点替换；`--verify` 仓外重建逐字节相等 | 派生件可由冻结旧件 `33d3f0af…` 原字节复算 |
| `patches/r7f8f_executor.patch` | 113931 B / `e09d333b2f09…` | 与旧版的明确差异原件 |
| `proof/r7f8e_case_matrix.txt` | **135 / FAIL=0** | §4 全组 + 五族反向 + 元负控 + 裁定 §5 的 K1~K8 进程内探针 + F19 的 FB1~FB7 真字节回放 + **F21 的 MC0~MC11 门位契约族与 TG1~TG10 令牌链族** |
| `proof/r7f8e_stub_package.txt` | 29 / FAIL=0 | STUB_ONLY 整包与等价负例（含两条掩码判据） |
| `proof/r7f8e_no_regression.txt` | 9 / FAIL=0 | 冻结 R7F7 定稿计划新旧逐行对照；旧判据一条未洗 |
| `proof/r7f8f_probe_replay.txt` | 21 件 × 3 侧 / FAIL=0 | 同一输入喂修后 / F5b 修前 / F5c 修前 |
| `proof/r7f8f_offline_gate.txt` + `proof/offline_gate.json` | **49 / FAIL=0** | 整包离线门；json 与文本条数同源、判定值只会是 PASS/FAIL、红条数与红名单一致 |
| `proof/r7f8f_budget_package.txt\|.json` | 11 / FAIL=0 | A 裁定包 9 件复算 + A 的 `budget_check.py` 在本机真跑对账 + 三条等价突变负控 + 基准件按 `6f2b12f` blob 核 |
| `proof/r7f8f_fixture_changes.md\|.json` | 族=5 / problems=0 | 基线 `42d7f7d` 判据名逐条对齐：removed=0，新增逐条带人写理由（含 MC/TG 两族） |
| `field/plan_r7f8f_2369_real.json` | 45398 B / `2259359ca3f7…` | 最终现场计划（38 步、逐步 `step_total_seconds`、三段镜像、`normal_stop_send_by_seconds: 8`；**F21 起带 `arm_gate_mask_contract=R7F8F_PER_STEP_V1` 与 10 条逐步契约表**） |
| `field/rows_r7f8f_real.json` | 15460 B / `a8456159a45f…` | 现场行表（前态 19 行 = 2026-11-05 F11 **实拍字节**；第 7 步切片按源码 token 校正并补披露行；**F21 起 8 处 `motor_status` 逐步分开键位，四份 mask 形状全部来自板上原件**） |
| `field/plan_r7f8f_2369_real_dispatch.json` | 32832 B / `53c744213964…` | F11 那一次真实 dispatch 的承接件：**按字节冻结不回改**（其计划身份已不等于本轮交付件） |
| `field/plan_r7f8f_2369_real_dispatch_f20.json` | 35536 B / `4da0aeb016e8…` | F20 那一次真实 dispatch 的承接件（批准版，已用满）：**同样按字节冻结不回改**；同目录还留着它的草案 `…_f20_draft.json` 34898 B/`e59272b6fa88…` |
| `field/plan_r7f8f_2369_real_dispatch_f21_draft.json` | 47318 B / `4a9460801b46…` | 第三次现场尝试的承接件**草案**：令牌位 `NOT_ISSUED`、机械前置 `NOT_REDECLINED_THIS_ROUND`，未批准、不许直接 dispatch |
| `proof/r7f8f_field_f20_*`（9 件） | `sys_pre.rx.bin` 983/`73abf3012386` · `mot_pre.rx.bin` 1128/`f0550d313164` · 两份 `.rx.txt` · 步台账 1514/`bb3afae013a3` · dispatch 台账 3752/`76d55c52ba26` · 账本副本 1938/`43434767447c` · 原话 743/`e3b3a647a359` · stdout 5081 | F20 现场的**原始字节与账**，逐件入档、不补改（§9） |
| `tools/r7f8f_make_field_evidence.py`、`tools/r7f8f_make_dispatch_copy.py` | — | F11/F20 证据判读 / 承接件派生（**白名单 13 格**，含令牌来源与两份摘要；扁平 diff 复算 + 无令牌即草案） |
| `tools/r7f8f_offline_gate.py`、`r7f8f_make_real_package.py`、`r7f8f_probe_replay.py`、`r7f8f_fixture_change_table.py`、`r7f8f_budget_package_checks.py`、`r7f8f_make_handoff.py`、`r7f8e_merge_draft.py` | — | 本轮工具（回执与交接的数字都从盘上算） |

冻结未动：产品树 `32470c7ed44e…`（= 已签收 `00718b49…`）、旧执行器 `33d3f0af…` 219491 B、
A 任务包 13 件、A 裁定包 10 件（含其 `SHA256SUMS.txt`，清单不列自身）、
裁定前基准件 `reference_plan_6f2b12f.json` 28090 B / `66ca8a15…`（重跑仍红，见 §3）、
草案原件 `A_DRAFT_ORIGINAL.md` 24780 B / `b572c1928f60…`、
在板镜像 `23691e8d…` 297452 B（只作身份引用，未回读）。
草案合并件 `R7F8_DRAFT_MERGED.md` → **41090 B / `951c645ce619…`**，正文 304 行逐字承接
`inputs/A_R7F8F_BUDGET_PKG/R7F8_LIVING_DRAFT.md`（38793 B / `14bafe13a8cd…`，本地字节相等；
远端逐 blob 的同一条在回读回执里）；上一版合并件 41144 B / `db031f8aa5f3…`（= HEAD `fdfb511` 的
blob；合并工具现在**自己取 HEAD blob**，不再手写上一版身份）不回改，本轮内那份未提交的重合并
(40732 B / `08f3c4d64b44…`) 作为留痕写在记录节里。
`run_record.md` 追加到 **§26**（§25 = F11 真机那一次的完整判读，§26 = F19 修包与真字节回放），
自伤登记 §24.5 **23** 条 + §26.5 **5** 条。
新证据件由 `tools/r7f8f_make_field_evidence.py` 从落盘字节复算（11 条 FAIL=0）：
`proof/r7f8f_field_f11_sys_pre.rx.bin`（983 B 板子原始字节）+ `…rx.txt` + 两份台账（仓外路径掩成
`<WORK>`）+ `…receipt.txt|.json`。

## 3. A 的两条裁定如何真正落地（红不是被改掉的）

裁定一（运动计时的结束点）。执行器现在只在**正常 `motor_stop` 成功之后第一份新鲜、完整、可信的
零速快照**处解除运动计时：`zero_speed_contract()` 要求 `state=0 / cur_hz=0 / tgt_hz=0 / health=OK`
且 armed·请求·pad·ENN 预期/反馈/verdict 全项自洽（`CUT_BY_HARDWARE` 与残缺快照不算），
`motion_end_gate()` 要求本场已尝试过唯一一次 start 且证据取自**同一份**响应。stop 回话本身不算，
12 s 之后才到的零速具名 `MOTION_ZERO_CONFIRMATION_LATE`（不倒填），同一场第二次确认具名
`MOTION_END_ALREADY_CONFIRMED`，`MotionBudget.t0` 保持不清。
arm 的 18 s 通电截止与业务 83 s 截止**不随**零速解除（K5/K6 专门配对验证），零速后的
commit/show/ps/free 改由 `now+7 s` 起算、仍被会话 90 s 封顶。台账留
`MOTOR_STOP_CONFIRMED(ends_motion_timer=False)` → `MOTION_TIMER_ENDED(arm_deadline_kept=True)`
两条事件，整包正常场实测到零速确认发生在起转后 9.0 s（cap 内）。

裁定二（单步总窗）。计划申报的 `step_total_seconds` 是**整步总窗**（等待+回读+余量都在里面），
由同一个 `step_window()` 供执行器、计划门、镜像与仿真读取；不再逐步叠加 `SLACK=3`。
非法申报（bool / 非数 / 非正 / nan / inf，以及 `poll.until > 本步总窗`）在开端口之前逐条具名拒绝。
未申报的旧计划仍按 legacy `seconds+3` 跑 —— **冻结的 R7F7 件与 A 的裁定前基准件一字未动**：
基准件原字节复制到仓外重跑，仍是 `zero@None` + 两条红 + 旧口径 91.0/23.0
（`PRE_RULING_REFERENCE_PLAN_STILL_RED_UNCHANGED`），旧红原文由
`LEGACY_RED_TEXT_KEPT_VERBATIM` 照录留档。

§4 的四个固定数一字未改（cap 12 / arm 18 / 总 90 / 预留 7，`LIMITS_UNCHANGED`），
没有负 `seconds`，没有减 A 的步骤，没有放宽总窗。

## 4. 三段与 38 步表（A 裁定 §3 那张表由执行器自己算出来）

`pre_start` 19 步 = 31.0 s / `motion_active` 11 步（含 stop 与 stopped）= 9.0 s /
`post_zero` 8 步 = 7.5 s；38 步总窗合计 47.5 s ≤ 业务 83 s，+预留 = 54.5 s ≤ 90 s；
保守式 31+12+7.5+7 = 57.5 s ≤ 90 s，arm 保守式 2.5+12+1.5 = 16.0 s ≤ 18 s；
stop 发送尝试排在起转后 7.5 s ≤ 申报的 8 s，可信零速在 9.0 s ≤ cap 12 s，arm→禁能确认 13.0 s ≤ 18 s。
这些数由 `business_segments_ruling()` 现算，计划里的 `motion_budget.business_segments` 镜像由
**同一个函数**产出，检查器逐字段核对（漂移即判红）；镜像字段名按裁定改为
`pre_start / motion_active / post_zero` 并点出 `stop_request_index=28`、`zero_confirm_index=29`（0 基）。
三条元负控在场：`META_MUTATION_ANCHOR_HIT`、`META_MIRROR_DRIFT_NAMED`（把镜像加 5 s 必须具名漂移）、
`META_DOUBLE_COUNT_COMES_BACK_GOES_RED`（把停机后内容重新折进 cap，即修前的双算形状，必须变红）。
**这些是窗口分配与计时合同，不是硬件实测**；运行时仍以单调时钟真实耗时裁剪，超界即首错收口。

## 5. 本轮没验证、也没声称的

板上正常巡航 DONE、活动中的取消、SG/ADC+IMU 相对保护的实际触发与恢复、加减速窗冲击保护、
§5 全 15 组在最坏到达下的现场可行性（离线只证申报窗口排得下）、0.5 s 短窗在真实 Windows/USB 上的
传输下界（A 裁定 §2 末段自留的保留意见，本轮未新增仪器结论）—— 全部仍待现场轮。
`polarity_measured=false`、`core_protection_verified=false` 保持不动。
**离线门全绿不等于现场验收通过**：本轮只是把"能不能排得下、什么时候必须停"这一层钉死在软件里。

## 6. 设备动作与停点

离线那三个提交（`49a4a39` / `04ddc38` / `1f6095b`）设备动作 0；整包离线门的
`NO_REAL_PORT_IN_ANY_LEDGER` 只声明**门内**那批 dispatch 全走 stub/FakePort（口径已收窄改名，
见 `run_record.md` §25 末条）。**F11 真机那一次的动作以台账与额度账为准**，不靠本节措辞：
`port_open` 花掉 1 次、`retry/download/readback/explicit_reset/motor_arm/motor_run/learn` 全 0；
未构建、未下载、未回读、未复位、未动分析仪、未断送电；设备按 §4 保持通电，板态停在安全侧
（`armed=0`、`request=LOW`、`[UI] SAFE-OFF`、`mode=MONITOR_ONLY`）。

> **F20/F22 两次追加**：F20（第二次）与 F22（第三次）各自另开一本账、各花掉 1 次 `port_open`，
> 三本账都在盘上且互不结转。F22 的动作集合是三轮里最大的一个：**仍只有 6 条只读查询**，
> `actions_attempted=[]`、`disarm=NOT_ARMED`、`cleanup_errors=[]`、`tmc_config_apply` 0/1 ——
> 逐条判据见 §12 与 `reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.json`
> 的 `field_run_f22`（那一块全部从台账字节派生，取不到就拒产整份交接）。

## 7bis. F11 真机一次：结果、根因、以及离线为什么没抓到

- 令牌原话「执行 F11 整包实机验证」逐字入账（`approval_M8-R7F8F-F11-20261105`，绑
  executor `093c8408a019…` / plan `53c744213964…` / 镜像 `23691e8d…`）；机械前置
  `bench_continuity` 由操作者逐字确认后才记 `PROVIDED`（承接件与已交付原件只差 9 个键，
  38 步/额度/三段预算一字未动）。
- 停止：`code=BUSINESS_FAILED as=sys_pre`，触发行 `[Motor] arm gate fail mask=0x18 (arm REFUSED)`。
  这一行是产品 `supervisor.c:126` 在 `system_status` 里**无条件**打的门位评估
  （`motor.h:92/93`：`0x18 = MOT_GATE_NOT_PROTECTED | MOT_GATE_NO_DRV_CONFIG`，即"本次开机还没开
  IRQ 门 + TMC 参数还没下发"）—— 正是 §5 第 1 步该看到的前态，开 IRQ 与 apply 都在它后面。
  执行器本来就留了申报口 `status_lines_allow`（整行锚定的披露行不参与业务失败扫描），
  **是我给 `sys_pre` 漏了这条申报**。
- 离线 47 条为什么没抓到：stub 回灌的是我自造的 `rows_r7f8f_real.json`，那份行表把
  `mask=0x18` 写在 **TMC 写门**行，而板子写在 **Motor 门位评估**行 —— 假端口按我的错误行表回放，
  把包侧的漏申报圆掉了。行表与实拍的另外三处差异（`irq_gate` 大小写、`polarity=not-confirmed`、
  `protect_ready=NO`）一并登记；下一步必须补"用板子真字节回放 `assert_step`"的判据。
- 我没做的事：没重试、没第二次开口、没改产品源码消红、没把这条判据放宽。两条出路留给
  A/操作者定：①在承接件里给该查询步补**整行锚定**的披露申报（并明令起转类步骤不得有此申报）；
  ②或请产品把"当前会被拒"的门位评估换成不带 `REFUSED` 字样的措辞（那会动已冻结的产品源码）。

## 7. 下一步归属

> 本节写于 F19 交付时，**已被 §9/§10 取代**：F20 已跑（第 2 条只读查询停止），现归属看 §10 末。
> 保留原文是为了让 A 看到我当时的判断链，不当作当前待办。

- **已定（2026-11-05 深夜，操作者选路）**：§7bis 的两条出路选**①补包侧申报**（不动冻结源码），
  并要求"必做：真字节回放三条"补上离线盲区。落地见 §8。出路②（改产品措辞）未采纳，产品一字未动。
- **操作者（第二次开口前）**：F20 需要**一句新的真实原话**，对象 = §8 末那四件新身份
  （plan `e7851942…` / rows `c64fdee8…` / executor `30c67a3e…` / 镜像 `23691e8d…`）。
  F11 那个账的 `port_open` 已用满；我不开第二本账、不借那次批准、不自己造令牌。
- **A**：无需再定口径。仅当 F20 跑出来必须改产品、或机械条件变了，才按裁定末段回报范围差异。
  另外那 90 s/12 s/18 s/7 s 与 38 步的算术**仍未被现场验证**——F11 在第 1 步就停，一次业务写入都没发生，
  裁定 §3 那张表现在只是"窗口分配合同"。
- **我（不做）**：不改产品源码、不构建固件、不重试、不第二次开口；设备保持通电。

## 8. F19：修包侧披露申报 + 真字节回放（操作者两条处置的落地，仍 0 设备动作）

- **包侧申报**：只有 `sys_pre` / `sys_after_apply` 两条 `system_status` 查询步带
  `status_lines_allow = ["^\\[Motor\\] arm gate fail mask=0x[0-9A-Fa-f]+ \\(arm REFUSED\\)$"]`，
  并把 `[Motor] arm gate fail mask=` 写进该步 `expect_contains`；`sys_pre` 另加 `[Motor] state=… armed=0
  health=OK` 硬字段判据。依据是冻结源码（`supervisor.c:126` 无条件打这一行；`motor.h:92/93` 定 bit3/bit4；
  现场前态实测 0x18 = 两位同置；mask=0 时该行不含 REFUSED，因此不匹配豁免、也不需要豁免）。
- **行表**：前态 19 行改成 F11 实拍字节（去掉回显与提示符，工具拒绝凭记忆重造）；第 7 步切片补披露行并把
  三处臆造 token 改回源码事实（`irq_gate=CLOSED`、`polarity=not-confirmed`、`protect_ready=NO`
  —— safety_thread.c:315 要 irq_attached 且 polarity_validated，而第 10/11 步都在第 7 步之后）。
  该行 `mask=0x8` 是**推导**（0x18 减去 apply 成功后清零的 bit4），`_about.derived_shape_rows` 明写不是实测。
- **两道门同一口径**：运行期新增 `STATUS_ALLOW_NOT_POSITIVELY_REQUIRED`（被放行的每一行必须含本步
  `expect_contains` 的某字面片段，兑现 R7F8E 起写在注释里却没核过的那句话）；静态 `PLAN_STATUS_ALLOW`
  补第四条（豁免模式的字面前缀必须被 `expect_contains` 命中 ⇒ 静态绿保证运行期那条门不翻红）。
  新函数 `status_allow_literal_prefix`；配方 73 项，`--verify` 仓外重建逐字节相等。
- **FB1~FB7 真字节回放**（输入 = 板子发的 983 B，经生产读路径 `read_until_prompt`，不再是我写的行）：
  FB1 落盘逐字节相等 + 披露行在流；FB2 正例 verdict=OK 且台账逐字记录被豁免行；
  **FB3 反例**同一段字节喂 `motor_arm`（未申报）仍 `BUSINESS_FAILED`（豁免按步，不是一局通用）；
  **FB4 元负控**摘掉豁免 → 同一份字节翻红（证明申报承重）；FB5 删正面要求保留豁免 → 运行器具名停止；
  FB6 静态绿且 38 步里只有那两条查询步申报；FB7 只删正面要求 → 静态门翻红并写字面前缀。
- **重跑后的数**：矩阵 113/0 · stub 29/0 · 无回归 9/0 · 整包离线门 47/0 · A 包复算 11/0 ·
  三版本重放 21/0 · 夹具表族=5 problems=0 · validate-plan 146 项 0 红（交付件与 F20 草案件各一次）。
- **两处我自己造的坑**（详见 `run_record.md` §26.5）：① 字面前缀函数把"反斜杠+空格"当通配，
  把冻结的 R7F7 计划假判红（无回归 9/4、离线门 47/8）—— 是**无回归族**抓的，不是我重读代码；
  ② 新判据族起名 `L1_`~`L7_` 与既有清理族 `L1_`~`L4_` 撞号，由我在交接工具里加的计数拒绝门抓住，
  已全部改名 `FB1_`~`FB7_`。
- **承接件改为派生**：新工具 `tools/r7f8f_make_dispatch_copy.py` 只允许那 9 格差异，写完立刻扁平 diff
  复算，多一格/少一格都 `REFUSED|`；不给 `--token/--approval-id` 就只出草案（本轮就是草案，
  `status=FIELD_DISPATCH_DRAFT_NOT_APPROVED_NEW_PLAN_IDENTITY`，令牌位 `NOT_ISSUED`）。
  F11 那份承接件按字节冻结不回改，与本轮交付计划已不是同一身份（差 9 格 + F19 的 rows 修）。
- **合并工具的基准不再手写**：`r7f8e_merge_draft.py` 的"上一版合并件身份"改成**自取 HEAD 的 blob**
  （`fdfb511` 里那份 41144 B/`db031f8aa5f3…`），本轮内未提交的重合并（40732 B/`08f3c4d64b44…`）
  作为留痕写在记录节里；交接工具里那条"数不到就拒"的正则同步跟了新格式。
- 本节那串数（矩阵 113 / 离线门 47 / validate-plan 146 / 白名单 9 格）是 **F19 交付时**的快照；
  F21 之后的现值见 §10 第 4 点与 §10 末的身份表。F19 的原件与回执字节不回改，两轮的差异由
  `proof/r7f8f_fixture_changes.md` 逐条带理由对齐。

## 9. F20：第二次真机整包（已执行，第 2 条只读查询停止）

- **账与身份**：`M8-R7F8F-F20-20261105`，绑 executor `30c67a3e8dbe…` / plan `4da0aeb016e8…`
  （= `field/plan_r7f8f_2369_real_dispatch_f20.json`，由 §8 的派生工具产出，差异格全在白名单内）/
  镜像 `23691e8d8aa8…`。操作者原话 743 B / `e3b3a647a359…` 逐字入档
  （`proof/r7f8f_field_f20_operator_verbatim.txt`）。机械前置按本轮原话重新确认后才记 `PROVIDED`。
- **结果**：`code=BUSINESS_FAILED as=mot_pre`。两步只读查询都真的写出现场：

  | 步 | 命令 | 结果 | 原始字节 |
  | --- | --- | --- | --- |
  | 1 `sys_pre` | `system_status` | **通过**（豁免行入台账：`[Motor] arm gate fail mask=0x18 (arm REFUSED)`） | 983 B / `73abf3012386…`，与 F11 那份**逐字节相同** |
  | 2 `mot_pre` | `motor_status` | `BUSINESS_FAILED`，命中 `[MOT] arm_gate_mask=0x18 REFUSED` | 1128 B / `f0550d313164…` |

  之后一条业务命令都没发：`apply / arm / start / learn / commit` 全 0，`not_run` 里 S1 起整段没跑；
  固定收口照做，`motor_arm/motor_run/learn/download/readback/explicit_reset/retry` 全部 0，
  `port_open=1` 用满。板子保持禁能、保持通电。
- **这两行字节说明什么**：第 1 步通过 = **F19 的包侧申报在真机生效**（同一段字节，F11 判停、F20 判 OK）。
  第 2 步停止 = 同一个混淆形状出现在 `motor_status` 上：产品 `motor.c:2479-2480` 那条披露行
  `[MOT] arm_gate_mask=0x18 REFUSED` 被当成业务拒绝线，而我在 F20 的计划里只给 `system_status`
  装了豁免。**这是包侧缺陷，不是产品保护链失败**——0x18 正是这一步该看到的门位（bit3+bit4）。
- **令牌那一判单独记未通过**（A 的第 3 条要求）：F20 那本账里**没有** `approval_token` 这一格
  （`"approval_token_field_present": false`），当时开口传进 CLI 的令牌字节摘要 `9701aa453fe5…`
  与原话文件的 `e3b3a647a359…` **不同**，也没有任何门能发现。矩阵 TG8 拿归档的账本原件按新门复算，
  具名判出 `APPROVAL_TOKEN_NOT_BOUND`，因此这一判**记 FAIL 形状**（`TG8 = PASS` 指的是"门会红"，
  不是"那次开口合规"）。四件实体绑定按 TG9 **单独判 PASS**（提交 `44fa6c1` 的 blob 逐字节 =
  账本里那三格 + 盘上的镜像），两判互不掩盖。原账、原话与两份不同散列都保留，不补改。
- **两次 `system_status` 字节相同只证明输出重复**：983 B 逐字节一致证明的是这一版固件对该查询的
  输出可重复，**不证明接线未变**；"供电、模块、VREF、细分和接线无新变化"是操作者报告，
  按 §27.1 的口径单列来源等级。
- 台账里那条 `not_run_stages: ["S1"]` 与全部 `spent` 逐键原样进了交接（`field_run_f20` 块），
  数字都从归档字节读，不抄我的结论。

## 10. F21：A 的三条补齐（离线，本轮设备动作 0）

沿原范围、原时限（90 s 含 7 s 收口、零速 ≤12 s、arm 至禁能 ≤18 s）和动作上限；F20 那本账**不结转**。

### 10.1 全量核查：10 处状态查询步逐一钉死预期 mask

不是手工数"7 处"，也不是给所有查询统一放行任意 `REFUSED`。计划里新增
`arm_gate_mask_contract = R7F8F_PER_STEP_V1` 和一张 10 条的逐步契约表（`arm_gate_mask_contract_steps`，
每条带 `as/cmd/rows_key/expect_arm_gate_mask/asserted_by/source_grade/basis/status_lines_allow/
positive_requirement`），静态门 `R7F8F_ARM_GATE_MASK` 在 validate-plan 里**逐条**复算"按预期 mask
应有的豁免 = 实际申报的豁免"，少一条或多一条都红。

| 步 `as` | 命令 | 预期 mask | 申报 | 豁免 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `sys_pre` | system_status | `0x18` | must | 锚定 `0x18` | REAL |
| `mot_pre` | motor_status | `0x18` | must | 锚定 `0x18` | REAL |
| `sys_after_apply` | system_status | `0x8` | must | 锚定 `0x8` | DERIVED（0x8 值本身有真原件，这一步这个形状没在板上抓过） |
| `mot_after_irq` | motor_status | `0x0` | must | **无** | REAL |
| `mot_dir` | motor_status | `0x0` | must | **无** | REAL |
| `mot_armed` | motor_status | `0x0` | must | **无** | DERIVED |
| `cruise` | motor_status | `0x2` | must | 锚定 `0x2` | REAL |
| `mot_after_cancel` | motor_status | `0x2` | must | 锚定 `0x2` | REAL |
| `stopped` | motor_status | `0x0` **或** `0x2` | **must_one_of** | 只锚 `0x2` | REAL（两个形状各有原件） |
| `mot_disabled` | motor_status | `0x0` | must | **无** | DERIVED（R6-2 已在板源码里） |

- 原 38 步表的 `motor_status` 是 **8 处**，加 2 处 `system_status` = 10 条，全部进表；
  清理段的 `motor_status` 走禁能合同（`drv_request/pad`），不进这张 mask 表。
- **预期为 0 的 4 步一条豁免都没有**（MC2 判据）。它们收到任何 `REFUSED` 都按 `BUSINESS_FAILED` 停，
  收到集合外的值（如 `0x8 ALL-PASS` 这种自相矛盾形状）走新具名码 `FIELD_VALUE_NOT_IN_SET`（MC6）。
- `stopped` 是真边界，不是含糊：`motor.c:1989 mot_commit_zero_ready` 在 tick 里把 RUN→READY，
  条件是 `armed!=0 && cur==tgt==0`，与本查询先后不定 ⇒ 两个值都合法，所以申报成**枚举**而不是通配。
- 起转/使能/学习类步骤不会被这套豁免碰到：静态门按 cmd 精确匹配两条查询命令，动作侧的门位拒绝
  继续 STOP（FB3/MC5 用同一段真字节分别判 OK 与 STOP）。
- 元负控：MC7 对 10 条**逐条**摘掉那一条判据，静态门必须在那一步翻红并点名它（10/10）；
  MC8 给预期 `0x0` 的步加通配豁免必须判红；MC9 历史冻结包没有契约标记只登记 `NA`、不加新红；
  MC1 从计划反向重算覆盖面，契约没覆盖到任何一条状态查询步就 `MASK_CONTRACT_REFUSED`。

### 10.2 历史真实字节入链 + 跨计划继承门

- 四份 mask 形状的**板上原件**（`_MASK_SAMPLES` 等，缺件或身份不符时生成器直接拒）：
  `0x18` = 1128 B/`f0550d313164…`（5 次归档会话 + F20 逐字节相同）、
  `0x0` = 648 B/`10807f63f02a…`（9 次归档一致）、
  `0x2` = 1026 B/`9005a306999a…` 与 1025 B/`57efcc7c90d5…`（另有 1074 B/`98abbf2d18df…` 是 R6 之前
  静止但 RUN 未落位的那份）、`0x8` = 1080 B/`fa69aa5cf5a1…`。
- **口径按 A 的更正**："F20 本会话首次取得"，不是"史上第一次"。`1128 B / f0550d313164…` 与 R7F4
  写前 `motor_status` 的归档身份一致、V0.3 复核时对过 —— 这条已由 MC3（四件在盘且逐字节相符）与
  MC11（F20 新归档件与历史归档同一身份）钉成判据，历史样本不再漏。
- **旧计划判据未删不等于新计划继承了披露合同**（A 的第二句）。新增 MC10：拿六份冻结旧计划
  （R7F4 / R7F5 / R7F7 / F8 / F11 / F20 承接件）里每一条披露豁免，逐条在新计划找落点，找不到就红。
  结果：唯一一条**明写不继承** = `motor_status` 的 `0x8`，由 R7F4 `mot_post` 与 R7F5 `mot_cfg` 申报过；
  本包在 `tmc_config_apply`（第 6 步）之后、`safety_irq_enable`（第 11 步）之前**不发 `motor_status`**，
  那段窗口里唯一的查询是第 7 步 `system_status`（已按预期 `0x8` 申报），所以不是删豁免而是没有对应步。
  这个"没有对应步"不能靠我说 —— `window_check{after_step:apply, before_step:irq_en, cmd:motor_status}`
  由矩阵当场复算步序。现场若在别的时刻打出 `motor_status 0x8`，按 `BUSINESS_FAILED` 停。
- 派生与实测严格分级：契约表每行带 `source_grade=REAL|DERIVED`，`_about.derived_shape_rows` 明写
  哪些形状是推的；`mot_armed`（armed 且 READY）现网没抓过，直接标 DERIVED，不冒充实测。

### 10.3 令牌门：三份摘要一致才允许开口

- 新 CLI 参数 `--approval-token-file` / `--token-verbatim-file`；`approval_init` 把
  `approval_token = {sha256, bytes, source}` 写进账本（**只写摘要，不落原文**）。
- `dispatch_plan` 之前跑 `verify_token_chain()`，核对**原话文件、实际传入令牌、账本绑定令牌**三份
  摘要，任一不符就拒绝开口，具名拒绝码：`TOKEN_DIGEST_MISMATCH` / `APPROVAL_TOKEN_NOT_BOUND` /
  `TOKEN_SOURCE_CONFLICT` / `TOKEN_VERBATIM_NOT_UTF8` / `TOKEN_VERBATIM_FILE_ABSENT`。
- 原字节改由 Python 内按严格 UTF-8 直接读文件（`token_bytes_from_file()`），不再经控制台打印
  和 shell 变量转运 —— F20 那个 `9701aa453fe5…` 就是这么丢的。
- TG1~TG10 十条判据（正路 TG3、反例 TG4/TG5、非 UTF-8 TG6、文件缺席 TG7 不猜内容、
  F20 原账复算 TG8、实体绑定单独判 TG9、TG10 走 CLI 真实分支：`--transport real --backend real
  --port COM_NONEXISTENT_M8R7F8F` 在 dispatch 之前被拦，rc=4 `TOKEN_CHAIN_REFUSED:APPROVAL_LEDGER_ABSENT`，
  本轮零台账文件、零开口、未导 pyserial）。

### 10.4 本轮重跑的数（全部在定稿字节上）

矩阵 **135/0**（MC0~MC11 共 12 条 + TG1~TG10 共 10 条并入）· stub 整包 29/0 · 无回归 9/0 ·
整包离线门 **49/0**（新增 `ARM_GATE_MASK_GATE_IS_GREEN_AND_COVERS_EVERY_QUERY_STEP`）·
A 包复算 11/0 · 三版本重放 21 件×3/0 · 夹具表族=5 problems=0 · validate-plan **147 项 0 红**
（交付件与 F21 草案各一次）· 配方 83 处替换、`--verify` 仓外重建逐字节相等。

### 10.5 本轮我自己造的坑（`run_record.md` §27.6，八条）

变量遮蔽把离线门六条判据带红 · `bytes` 字面量含非 ASCII · 真字节夹具把 `motor.c:937` 的裸披露行
也换掉造成行不唯一 · F20 的 CLI 令牌字节没落进任何工件（只能登记为"会话里打印过的摘要"）·
承接件工具写死上一轮现场原话 · **交接 JSON 把通过的第 1 步当成停止步**（生成器取了
`BUSINESS_OUTCOME[0]`；已改成用 `SESSION_STOP.detail.lines` 求同源交集，交集为空就 `HANDOFF_REFUSE`）·
**矩阵判据名里带了字段分隔符 `|`**（MC6 的 `0x0|0x2` 把回执第三格切开、PASS 丢了；已改名并在
`note()` 里加了含 `|` 当场 `ValueError` 的硬门）· **`next_step_owners` 里写了两条同名 `"A"` 键**
（字典字面量后写覆盖前写，F19 那条被静默吃掉；已删旧条并在原位留注释禁止再加同名键）。
后三条是本会话发现的，都随本轮修正并登记；交接工具里还加了一条"§27.6 实际条数 ≠ 交接标题清单条数
就 `HANDOFF_REFUSE`"的同步门，防止清单与文档各说各话。

### 10.6 新身份（第三次现场尝试的对象）

| 件 | 字节 | sha256 |
| --- | --- | --- |
| `field/plan_r7f8f_2369_real.json` | 45398 | `2259359ca3f775bc478fd8db30535485cc9e589d6a5c45d7ac9e948b40326a6b` |
| `field/rows_r7f8f_real.json` | 15460 | `a8456159a45f229601e030b69d0034894d1b0df19b191ca4672a1fe0e5279737` |
| `tools/m8r3_serial_chain_r7f8.py` | 328475 | `4363af36159befa417647680cc27fb5bdb95785a404b14d77602ccdbe253962e` |
| 镜像 `rt-thread-motor-m1-candidate.bin`（未重建，仍是在板那枚） | 297452 | `23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963` |
| `patches/r7f8f_executor.patch` | 113931 | `e09d333b2f09…` |
| F21 承接件**草案**（未批准） | 47318 | `4a9460801b46…` |

### 10.7 停点与归属

- **本轮设备动作 0**：未开串口、未写任何字节到 COM5、未下载/回读/复位/构建、未动分析仪、
  **未建本轮现场批准账**；板子保持禁能、保持通电（§4 的收口纪律）。
- **操作者**：需要**一句新的真实原话**，对象 = §10.6 的四件新身份，并且本轮要**逐字重述机械/电气
  前置**（空载、轴可自由转动、供电/接线无变化等），我用 `--approval-token-file` 开一本新账、只跑一次
  整包；我不会自造授权句，也不借 F11/F20 的批准。
- **A**：无需另开登记轮。若第三次仍因包侧形状停止，我按同一口径补包侧申报并再给新身份；
  若必须改产品才能过，当场停下回报，不动冻结源码。
- **我（不做）**：不改产品源码、不构建固件、不重试、不第二次开口；`polarity_measured=false`、
  `core_protection_verified=false` 保持原值。90/12/18/7 s 与 38 步的算术**仍未被现场验证**——
  F11/F20 都停在只读查询，一次业务写入都没发生，裁定 §3 那张表现在仍是"窗口分配合同"。
  **本轮不是现场验收通过。**
## 11. DIR0 变体包（操作者现场指令的方向变更，仍 0 设备动作）

> **本节产物最终未被使用**：操作者随后改回 DIR1（原话「DIR1现在可以了 前面是滑块在边缘容易堵转
> 我前面电机已经装上去了 不方便拆下来」），第三次真机（§12）用的是 §10 末那四件 **DIR1 交付件**
> 身份。DIR0 变体两件与它的两份回执按字节冻结留在盘上、不回改，作为"派生变体链"的存档；
> 血统门（`package_variant.derived_from_*_sha256` 与盘上交付件摘要当场对账）仍然成立。

操作者随后判定「DIR 不是安全相关轴、方向处理已被既有测试覆盖，别为标注差异卡住现场」，选择
**要 DIR0：先出变体再取批准**，并要求**完整重算摘要 + 原有六族离线门全过**。全文依据与身份在
[`field/DIR0_variant_rationale.md`](file:///D:/ns800/reports/reviews/phase8-m8-r7f8-executor-adaptation/field/DIR0_variant_rationale.md)，
机器可读那份在交接 JSON 的 `dir0_variant` 块；记录见 `run_record.md` §28。

- **前提核对（按源码，逐条带出处）**：机械安全层成立——`motor.c:894-917` 七个 arm 门位里没有方向项，
  `motor.c:1470/1496` 的 DIR 只在过门后才写；既有覆盖成立——板级 M3-3「DIR0 短转」与 M3-4「DIR1 短转」
  都通过，主机级 `tests/host/{io,ps,rel,sd}_main.c` 里有 `motor_dir`/`DIR_MASK` 用例。
  **但证据适用范围要收窄**：`diag_relative.c:2023` 学习只置一个方向位（`cand.dir_mask = 1u << dir`），
  `:334` 判定引擎只对方向位在 mask 内的样本生效，`:126` 要求 `dir_mask ∈ {1,2}`，产品注释 `:2005`
  自己写明"只采一个方向却打 `dir_mask=3` 是第二条假证据链"。所以本轮结论写成
  **「dir=0 方向上的 SG/ADC 相对保护现场证据」**，不写成双向已验。
- **变体怎么来的**：`tools/r7f8f_make_real_package.py --dir 0 --tag _dir0`。派生前先逐字节证明
  "未翻转的生成结果 == 盘上 DIR1 交付件"（**血统门**，所以 `derived_from_*_sha256` 是算的不是写的）；
  翻 20 处字符串 + 4 处 `{"dir":1}`；翻后做**残留检查**（`dir=1`/`motor_dir 1`/`DIR1`/`"dir": 1`/`350 1`
  一种不剩）、**过宽检查**（`motor_status_ready_dir` 必须逐行等于 648 B/`10807f63f02a` 原件）与
  **不越界检查**（`quota["motor_dir"]==1` 是次数不是方向）；两处说明性文字整条替换，命中数≠1 就拒。
- **判据工具参数化**：只有反例矩阵与整包离线门真读被测包，各加 `--package-tag`，回执跟着换名
  （`proof/r7f8e_case_matrix_dir0.txt`、`proof/r7f8f_offline_gate_dir0.txt`、`proof/offline_gate_dir0.json`），
  已交付回执不覆盖。绑历史的判据（TG9 的 F20 四件、MC10 的六份冻结旧计划、A 包复算族与 `6f2b12f`
  的 blob 比对）**继续读原始冻结字节**——换被测包就换错了对象。
- **六族在变体上**：矩阵 **135/0** · 整包离线门 **49/0**（内含 validate-plan **147 项 0 红**）·
  stub 29/0 · 无回归 9/0 · A 包复算 11/0 · 三版本重放 21×3/0 · 夹具表 problems=0。
  其中后四族不读 real plan（合成夹具 / 冻结 R7F7 计划 / A 的裁定包与 blob / 执行器补丁前后字节），
  与 DIR 无关。
- **待批准的 DIR0 四件**：plan `field/plan_r7f8f_2369_real_dir0.json` 46128 B /
  `2f82bf93fddfee9f2cf36a727685612307f52d7166244b78040b58cdad11d662`；
  rows `field/rows_r7f8f_real_dir0.json` 15734 B /
  `8dc0b5c06bbc4268652dcc9d5404636cbfbe8112686d297824dc9b73a61a1565`；
  executor `4363af36159b…`（未变）；镜像 `23691e8d8aa8…`（未重建，仍是在板那枚）。
  38 步、逐段窗口、30 条额度、10 步 mask 契约全部不变（方向不在七门里）。
- **不变纪律**：DIR1 交付件与它的回执按字节冻结不回改，变体用新文件名并存；本轮仍未建账、
  未开 COM5、未发任何字节；`polarity_measured=false`、`core_protection_verified=false` 保持原值。
- **我（本轮判断偏差）**：第一次上报 DIR 冲突时，我把 provenance（四行运行态由实拍退回"声明形状 +
  值翻转"）摆到接近阻断的位置，却没有先去读 `diag_relative.c` 的方向位语义——真正必须核的是
  `dir_mask` 决定**证据适用范围**那一处。已随 §11 修正，登记在 `run_record.md` §28。

## 12. F22：第三次真机整包（已执行，第 6 步只读查询停止；三轮最远；电机从未带电）

- **批准与原话**：操作者本会话原话「**电机装在丝杆上 滑块在中间位置 继续按包执行**」逐字入档
  `proof/r7f8f_field_f22_operator_verbatim.txt`（63 B / `3aa0d2fcb488…`；裁尾随 CR/LF 后
  62 B / `5a26a1bf5ab4…`）。"按包"指向上一轮我逐字列出的四件 DIR1 交付件摘要，承接件当场复算相符：
  `field/plan_r7f8f_2369_real_dispatch_f22.json` 47367 B / `32a5373eb5df…`，派生自
  plan `field/plan_r7f8f_2369_real.json` 45398 B / `2259359ca3f7…` +
  rows 15460 B / `a8456159a45f…`，executor 328475 B / `4363af36159b…`，
  镜像 297452 B / `23691e8d8aa8…`（R7F7-F1 已逐字节证在板）。机械前置由草案的
  `NOT_REDECLINED_THIS_ROUND` 变成 `PROVIDED` + 那句逐字原话（同一句，不另造）。
- **令牌链（F21 的门第一次在真实分支上真跑）**：`TOKEN_CHAIN_VERIFIED` 里传入令牌 == 原话文件
  裁尾 == 账本绑定 == 承接件申报，四份摘要同为 `5a26a1bf5ab4…`；字节由 Python 内从文件按严格
  UTF-8 取得（runner 已归档），不经控制台、不经 shell 变量。账本 `approval_token` 这一格在，
  就是 F21 那条 `APPROVAL_TOKEN_NOT_BOUND` 门补上之后的形状。
- **结果**：`code=EXPECTATION_NOT_MET`，停在第 6 步 `cfg_pre` = `tmc_config_show`（只读），
  `missing=["[TMC]"]`；`steps=6`、`disarm=NOT_ARMED`、`obligation=false`、`cleanup_errors=[]`、
  `quota_used={port_open:1}`、`actions_attempted=[]`、`not_run=["S1"]`。通过的 5 步原始字节逐件在
  `proof/r7f8f_field_f22_0*.rx.bin`：`sys_pre` 983/`73abf3012386`（与 F11/F20 **第三次逐字节相同**）、
  `mot_pre` 1128/`f0550d313164`（同一身份第 6 次）、`irq_pre` 612/`25bef65c2b36`、
  `diag_pre` 1169/`d515de38891b`、`ls_pre` 130/`acd145c616a2`；停止那步 `cfg_pre` 1011/`303eade7bb77`。
- **三条根因全在包侧（源码锚点，`run_record.md` §29/§30）**：① `cfg_pre`/`apply` 的
  `expect_contains` 写 `[TMC]`，而该命令族全部打 **`[TMC-CFG]`**（`tmc2209.c:3154/3158` show、
  `:2844` CONFIGURED、`:2645/2791` 拒绝路径）——`[TMC]` 这个前缀只在 `system_status` 里由
  `supervisor.c:128` 打；② 行表 `tmc_config_show`/`tmc_config_apply` 的 healthy 是我**手写的**
  `"[TMC] config show"` / `"[TMC] apply CONFIRM"`，源码里没有这两行，所以假端口不可能暴露——这与
  F11（漏申报披露行）、F20（漏 mask 申报）是同一个"我写的期望把自己圆过去"的形状，第三次；
  ③ `sys_after_apply` 期望 `runtime write gate mask=0x8` 把 **arm 门位 mask**（`motor.c:894-917`
  七门，`supervisor.c:126`）与 **TMC 写门 mask**（`tmc2209_get_write_gate_mask()`→
  `tmc_cfg_gate_prod()`，`tmc2209.c:2874`，`supervisor.c:128`）当成同一个数；本次实拍写门 `0x0`，
  apply 成功也不会把它变成 `0x8`。
- **撞出来的工具洞（属 F21 覆盖洞，本轮未改码）**：真实传输预检查 `m8r3_serial_chain_r7f8.py:5530`
  只认 `--approval-token`，不认 `--approval-token-file` => 第一次用纯文件令牌跑真实分支被
  `REAL_TRANSPORT_REFUSED:approval_token_missing_or_mismatch` 拒在开口前（rc=4，账本 `spent` 合计 0，
  未建执行根，确实 0 字节发出）。本轮**没为此改执行器字节**（改了就不是操作者批准那件），改为在
  Python 内取规范字节同时喂四个令牌口——**这是绕行，不是修复**；F23 要补的正路判据见 §30.2 第 6 条。
- **F22 那本账**：`M8-R7F8F-F22-20261106`，`port_open` 1/1 用满，其余全 0（含
  `tmc_config_apply` 0/1、`motor_arm` 0/1、`diag_profile_learn` 0/2）；F11/F20 两本账原样不动，
  三本都不结转到下一轮。板态：TMC `state=UNCONFIGURED`、drv 未使能、`live GCONF=00000101 /
  CHOPCONF=15010053`，**保持通电**（未要断电动作，按既有收口口径）。
- **交接工具这一轮新增的取证**（`tools/r7f8f_make_handoff.py` 的 `field_run_f22` 块，取不到就
  拒产整份交接）：停止步只取台账里那条唯一的 `PROTOCOL_OR_CONTENT_FAIL` 并与 `SESSION_STOP`
  的 `missing` 同源对账；6 份捕获按台账 `RX_DONE` 的**顺序+字节+散列**三重逐条对账，最后一步必须
  等于停止步；`quota_used` 与账本 `spent` 归一到非零项后逐项相等；`actions_attempted` 必须为空
  否则"电机从未带电"这句话就不许写；令牌四份摘要、账本三个身份摘要与承接件自报的"来源原件
  N/摘要前缀"全部当场在盘上复算。另外点名引用的判据（TG10/MC11）若在回执里取不到 PASS 也拒产。
- **停点**：本会话第三次、也是最后一次已批准的现场尝试已消耗。要继续到第 7 步之后需要
  **F23 包侧修正**（离线，0 设备动作；清单见 `run_record.md` §30.2 与本文件 §12 上面的三条根因）
  落地后的新四件身份 + 操作者一句**新的**真实原话并在同一句里逐字重述机械/电气前置。
  `polarity_measured=false`、`core_protection_verified=false` 保持原值；仍不是现场验收通过。



