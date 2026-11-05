# 交接：M8-R7F8F 返修+整包离线门轮（2026-11-05，含 A 的预算裁定落地）

**状态：OFFLINE_GATE_ALL_GREEN / 现场包已作者化待一次批准 / HARDWARE_ACTIONS=0 / 一次现场批准未取得**
**基线 `d6bbc1b5795f`；分支 `feature/m8-r7d1-async-learn-offline`；
任务书 `reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8F_PKG/R7F8F_REPAIR_AND_FIELD_TASK.md`；
预算裁定 `…/inputs/A_R7F8F_BUDGET_PKG/R7F8F_A_BUDGET_RULING.md`。**
机器可读同源件：`reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.json`
（数字当场从盘上算，与本文件同源；两处不一致以重新生成一次为准）。

## 1. 一句话

前半轮按 A 的 F1~F6（含 F5b/F5c 两项实测缺口）把派生执行器修到"该停的停在写之前、该收口的五条
都拿得到自己的窗"，并把静态预算拆三段后**留下两条真红交 A 定口径**；后半轮把 A 的预算裁定
（可信零速结束运动计时 + 显式单步总窗）落进执行器、计划、行表、判据与本交接，**两条红因此转绿**，
整包离线门 47 条全绿、validate-plan 146 项 0 红。
**现场什么都没做**：设备动作 0、未构建固件、未开现场账、未发一条现场命令。

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
`run_record.md` §24 追加到 §24.11，§24.5 当场数出 **21** 条本轮自伤。

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

设备动作 0（机器核过：台账里每条 `PORT_OPEN` 都是 `transport=stub` 且 `port` 字段为空，
`NO_REAL_PORT_IN_ANY_LEDGER`=PASS）；未开 COM5、未构建、未下载、未回读、未复位、未动分析仪、
未断送电；批准账只建在仓外 `--work`。一次现场批准仍未取得——未取得就不发任何现场命令。

## 7. 下一步归属

- **A**：无需再定口径，也无需第二次软件签收（裁定末段原文："**不再另开登记轮或等A签第二次软件章。**"）。
  只有当真机跑出来必须改产品、或机械条件已变，才按裁定回报范围差异。
- **操作者**：给出一次现场批准的真实原话，对象是"R7F8F 及本预算裁定"（令牌只取原话，
  不借旧批准、不由 agent 自造）。
- **我（拿到那句原话之后）**：接上机执行一次整包，交付原始字节 + 两级台账 + 逐项判读 + 清理与观察。
- **我（本轮不做）**：不进现场执行、不构建固件、不开真实批准账；设备按上一轮约定保持通电收口。
