# 交接：M8-R7F8F 返修+整包离线门轮（2026-11-05）

**状态：OFFLINE_GATE_DONE_WITH_2_NAMED_RED / FIELD_PACKAGE_NOT_READY / HARDWARE_ACTIONS=0 / 一次现场批准未取得**
**基线 `d6bbc1b5795f`；分支 `feature/m8-r7d1-async-learn-offline`；任务书见
`reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8F_PKG/R7F8F_REPAIR_AND_FIELD_TASK.md`。**
机器可读同源件：`reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.json`
（数字当场从盘上算，与本文件同源；两处不一致以重新生成一次为准）。

## 1. 一句话

按 A 的 F1~F6（含 F5b/F5c 两项实测缺口）+ 本轮追加的 F12 静态预算三段拆分，把派生执行器修到
**"该停的停在写之前、该收口的五条都拿得到自己的窗"**，四族判据 + 三版本重放 + 逐项变更表 +
40 条整包离线门全绿（除两条**已具名交 A 定口径**的静态冲突），并作者化了最终现场包。
**现场什么都没做**：设备动作 0、未构建固件、未开现场账、未发一条现场命令。

## 2. 交付件（同一目录 `reports/reviews/phase8-m8-r7f8-executor-adaptation/`）

| 件 | 身份 | 作用 |
| --- | --- | --- |
| `tools/m8r3_serial_chain_r7f8.py` | 289452 B / `043d5bdd179c61ea…` / 5100 行 | 派生执行器（F1~F6 + F5b/F5c + F12） |
| `tools/r7f8f_build_executor.py` | 51 处逐字锚点替换；`--verify` 仓外重建逐字节相等 | 派生件可由冻结旧件 `33d3f0af…` 原字节复算 |
| `patches/r7f8f_executor.patch` | 67742 B / `c6c9f60367db…` | 与旧版的明确差异原件 |
| `proof/r7f8e_case_matrix.txt` | 98 / FAIL=0 | §4 全组 + 五族反向 + 元负控（F5b/F5c 新增 H10~H14、G9~G17） |
| `proof/r7f8e_stub_package.txt` | 29 / FAIL=0 | STUB_ONLY 整包与等价负例（含两条掩码判据） |
| `proof/r7f8e_no_regression.txt` | 9 / FAIL=0 | 冻结 R7F7 定稿计划新旧逐行对照；旧判据一条未洗 |
| `proof/r7f8f_probe_replay.txt` | 21 件 × 3 侧 / FAIL=0 | 同一输入喂修后 / F5b 修前 / F5c 修前的三版本重放 |
| `proof/r7f8f_offline_gate.txt` + `proof/offline_gate.json` | **40 / FAIL=2** | 整包离线门；json 与文本条数同源、判定值只会是 PASS/FAIL |
| `proof/r7f8f_fixture_changes.md\|.json` | 族=5 / problems=0 | 基线 `42d7f7d` 判据名逐条对齐：removed=0，新增逐条带人写理由 |
| `field/plan_r7f8f_2369_real.json` | 28090 B / `66ca8a152ede…` | 最终现场计划（38 步、§4 四个固定数逐字段核对） |
| `field/rows_r7f8f_real.json` | 8168 B / `4022306df3ad…` | 现场行表（ACCEL=1 按 `motor.h` 枚举、IRQ 两条按产品真实行） |
| `tools/r7f8f_offline_gate.py`、`r7f8f_make_real_package.py`、`r7f8f_probe_replay.py`、`r7f8f_fixture_change_table.py`、`r7f8f_make_handoff.py` | — | 本轮新增工具（回执与交接的数字都从盘上算） |

冻结未动：产品树 `32470c7ed44e…`（= 已签收 `00718b49…`）、旧执行器 `33d3f0af…` 219491 B、
A 包 13 件（清单列出的可比对集合是其余 12 件）、草案原件 `A_DRAFT_ORIGINAL.md` 24780 B / `b572c1928f60…`、
在板镜像 `23691e8d…` 297452 B（只作身份引用，未回读）。
草案合并件 `R7F8_DRAFT_MERGED.md` → 40289 B / `bc3710e9917c…`，正文 290 行逐字承接
`inputs/A_R7F8F_PKG/R7F8_LIVING_DRAFT.md`（本地字节相等；远端逐 blob 的同一条在回读回执里）。
`run_record.md` §24 追加，§24.5 当场数出 **14** 条本轮自伤。

## 3. 请 A 裁定的两件事（定了我才动上机）

1. **`PLAN_POST_SPIN_CONTENT_FITS_CAP`**：§4 固定 `cap=12 s`，但起转后申报内容 = 23.0 s
   （运动窗内 11.0 + 停机后 12.0）。执行器在 `motor_start` 写入尝试时置 `MotionBudget.t0` 且
   **正常 stop 不清**，所以 §5 行 12 之后（含 13 disarm / 14 commit / 15 ps+free）同被 `t0+12` 夹住。
   三条出路：放宽 cap（会同时动 §6 的 `motor_run_seconds_total=12`）／认可"已确认零速后解除运动
   cap"的运行时语义改动／把 §5 行 14、15 挪到下一个会话。**本轮一条都没自行选。**
2. **`PLAN_WORST_CASE_TAIL_FITS_SESSION`**：每步都慢到 `SLACK=3 s` 上限时，起转前 19 步的最坏合计
   91.0 s 已超业务预算 83.0 s（= 总 90 − 预留 7），第 19 步 `motor_target 300` 会在写之前被
   `STEP_DEADLINE_BEFORE_WRITE` 拒 ⇒ §5 那 15 组在最坏到达下跑不到起转。这是**取证完整性**风险，
   不是撤能风险（收口预留由 `business_abs_dl` 硬夹保护，与运行结果无关）。申报窗口本身合得下：
   `worst_declared 46.0 + 预留 7 ≤ 90`。消红只有减步或放宽总窗，本轮都不自行做。

两条都已写进计划自己的 `static_budget_open_items`（带数字），审核者在计划字节里就能看到红在哪。

## 4. F12 的口径（A 本轮补的审核意见，已落地）

`pre` 不再遍历全部步骤：三段 = 起转前 19 步 / 运动窗内 9 步 / 停机后 10 步（`spin_up_index=19`、
`stop_index=28`），`cap` 只加一次；单步界 = 申报窗 + SLACK，与执行器 `min()` 链是同一表达式；
计划里的 `motion_budget.business_segments` 镜像由执行器同一个函数 `business_segments()` 产出，
17 个字段逐项机器核对（漂移即判红），三条元负控证明这些门不是恒真
（`META_MUTATION_ANCHOR_HIT` / `META_MIRROR_DRIFT_NAMED` / `META_DOUBLE_COUNT_COMES_BACK_GOES_RED`）。
未用负数 `seconds`、未漏算余量、未扩大 90 s 总窗、未删 A 的步骤。

## 5. 本轮没验证、也没声称的

板上正常巡航 DONE、活动中的取消、SG/ADC+IMU 相对保护的实际触发与恢复、加减速窗冲击保护、
§5 全 15 组在最坏到达下的现场可行性 —— 全部仍待现场轮。
`polarity_measured=false`、`core_protection_verified=false` 保持不动。
**"工具关键修复已验证"不等于"完整现场包就绪"**，本轮不称现场验收通过。

## 6. 设备动作与停点

设备动作 0（机器核过：台账里每条 `PORT_OPEN` 都是 `transport=stub` 且 `port` 字段为空，
`NO_REAL_PORT_IN_ANY_LEDGER`=PASS）；未开 COM5、未构建、未下载、未回读、未复位、未动分析仪、
未断送电；批准账只建在仓外 `--work`。一次现场批准仍未取得——未取得就不发任何现场命令。

## 7. 下一步归属

- **A**：对 §3 两条定口径。
- **操作者**：给出一次现场批准的真实原话（令牌只取原话，不借旧批准、不由 agent 自造）。
- **我（两者都齐之后）**：接上机执行一次整包，交付原始字节 + 两级台账 + 逐项判读 + 清理与观察。
- **我（本轮不做）**：不进现场执行、不构建固件、不开真实批准账；设备按上一轮约定保持通电收口。
