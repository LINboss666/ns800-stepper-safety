# 交接：M8-R7F8F 返修+整包离线门轮（2026-11-05，含 A 的预算裁定落地）

**状态：OFFLINE_GATE_ALL_GREEN / 真机已跑一次并在第 1 步首错停止（`BUSINESS_FAILED`，包侧缺
`status_lines_allow` 申报）/ 电机从未带电、从未起转 / 第二次现场尝试需要操作者新的批准**
**基线 `d6bbc1b5795f`；分支 `feature/m8-r7d1-async-learn-offline`；
任务书 `reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8F_PKG/R7F8F_REPAIR_AND_FIELD_TASK.md`；
预算裁定 `…/inputs/A_R7F8F_BUDGET_PKG/R7F8F_A_BUDGET_RULING.md`。**
机器可读同源件：`reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.json`
（数字当场从盘上算，与本文件同源；两处不一致以重新生成一次为准）。

## 1. 一句话

前半轮按 A 的 F1~F6（含 F5b/F5c 两项实测缺口）把派生执行器修到"该停的停在写之前、该收口的五条
都拿得到自己的窗"，并把静态预算拆三段后**留下两条真红交 A 定口径**；后半轮把 A 的预算裁定
（可信零速结束运动计时 + 显式单步总窗）落进执行器、计划、行表、判据与本交接，**两条红因此转绿**，
整包离线门 47 条全绿、validate-plan 146 项 0 红。取得操作者真实原话令牌后跑了**一次**真机
整包：唯一一次开口只写出一条只读查询，产品回的 `system_status` 门位披露行被我漏申报，于是按
业务失败线在第 1 步停止（0.156 s）；`motor_arm / motor_start / learn / apply` 一条都没发出去，
电机从未带电。现场事实与根因见 §7bis 与 `run_record.md` §25。

## 2. 交付件（同一目录 `reports/reviews/phase8-m8-r7f8-executor-adaptation/`）

| 件 | 身份 | 作用 |
| --- | --- | --- |
| `tools/m8r3_serial_chain_r7f8.py` | 312387 B / `093c8408a019…` / 5480 行 | 派生执行器（F1~F6 + F12 + 裁定一/二） |
| `tools/r7f8f_build_executor.py` | 70 处逐字锚点替换；`--verify` 仓外重建逐字节相等 | 派生件可由冻结旧件 `33d3f0af…` 原字节复算 |
| `patches/r7f8f_executor.patch` | 92610 B / `058a75f153e5…` | 与旧版的明确差异原件 |
| `proof/r7f8e_case_matrix.txt` | **106 / FAIL=0** | §4 全组 + 五族反向 + 元负控 + 裁定 §5 的 K1~K8 进程内探针 |
| `proof/r7f8e_stub_package.txt` | 29 / FAIL=0 | STUB_ONLY 整包与等价负例（含两条掩码判据） |
| `proof/r7f8e_no_regression.txt` | 9 / FAIL=0 | 冻结 R7F7 定稿计划新旧逐行对照；旧判据一条未洗 |
| `proof/r7f8f_probe_replay.txt` | 21 件 × 3 侧 / FAIL=0 | 同一输入喂修后 / F5b 修前 / F5c 修前 |
| `proof/r7f8f_offline_gate.txt` + `proof/offline_gate.json` | **47 / FAIL=0** | 整包离线门；json 与文本条数同源、判定值只会是 PASS/FAIL、红条数与红名单一致 |
| `proof/r7f8f_budget_package.txt\|.json` | 11 / FAIL=0 | A 裁定包 9 件复算 + A 的 `budget_check.py` 在本机真跑对账 + 三条等价突变负控 + 基准件按 `6f2b12f` blob 核 |
| `proof/r7f8f_fixture_changes.md\|.json` | 族=5 / problems=0 | 基线 `42d7f7d` 判据名逐条对齐：removed=0，新增逐条带人写理由 |
| `field/plan_r7f8f_2369_real.json` | 31712 B / `eef417ce3152…` | 最终现场计划（38 步、逐步 `step_total_seconds`、三段镜像、`normal_stop_send_by_seconds: 8`） |
| `field/rows_r7f8f_real.json` | 8168 B / `4022306df3ad…` | 现场行表（停机位改成产品真实的"已零速但仍带电"形状 `motor_status_armed_idle`） |
| `tools/r7f8f_offline_gate.py`、`r7f8f_make_real_package.py`、`r7f8f_probe_replay.py`、`r7f8f_fixture_change_table.py`、`r7f8f_budget_package_checks.py`、`r7f8f_make_handoff.py`、`r7f8e_merge_draft.py` | — | 本轮工具（回执与交接的数字都从盘上算） |

冻结未动：产品树 `32470c7ed44e…`（= 已签收 `00718b49…`）、旧执行器 `33d3f0af…` 219491 B、
A 任务包 13 件、A 裁定包 10 件（含其 `SHA256SUMS.txt`，清单不列自身）、
裁定前基准件 `reference_plan_6f2b12f.json` 28090 B / `66ca8a15…`（重跑仍红，见 §3）、
草案原件 `A_DRAFT_ORIGINAL.md` 24780 B / `b572c1928f60…`、
在板镜像 `23691e8d…` 297452 B（只作身份引用，未回读）。
草案合并件 `R7F8_DRAFT_MERGED.md` → **41144 B / `db031f8aa5f3…`**，正文 304 行逐字承接
`inputs/A_R7F8F_BUDGET_PKG/R7F8_LIVING_DRAFT.md`（38793 B / `14bafe13a8cd…`，本地字节相等；
远端逐 blob 的同一条在回读回执里）；上一版合并件 40289 B / `bc3710e9917c…`（提交 `c9ad704`）不回改。
`run_record.md` 追加到 **§25**（§25 = F11 真机那一次的完整判读），§24.5 当场数出 **23** 条本轮自伤。
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

- **A / 操作者**：§7bis 末的两条出路选一条（补申报 = 只动我这边的包；改产品措辞 = 动冻结源码）。
  另外，那 90 s/12 s/18 s/7 s 与 38 步的算术**尚未被现场验证过**——第 1 步就停了，
  一次业务写入都没发生，所以裁定 §3 那张表现在只是"窗口分配合同"，没有任何一段被实拍证明。
- **操作者（若选补申报这条路）**：包身份会变（计划/行表两件），需要**再一次**真实原话批准才能
  第二次开口；本次账本里 `port_open` 已花完，我不开第二本账、不借这次批准。
- **我（下一步，离线）**：把"板子真字节回放 `assert_step`"补成判据（正反各一条 + 元负控），
  并按选定的那条出路把包修好、四族判据与整包门重跑、清单/预检/提交/推送/逐 blob 回读。
- **我（不做）**：不改产品源码、不构建固件、不重试、不第二次开口；设备保持通电。
