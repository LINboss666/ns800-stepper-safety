# R7F8E 交付：M8-R7F8 现场保护闭环的执行器离线适配

**任务：M8R7F8E-offline-executor-adaptation-20261105（任务书 `inputs/A_R7F8E_PKG/TASK_R7F8E_EXECUTOR_OFFLINE.md`）**
**状态：OFFLINE_DEVELOPMENT_DONE / SOFTWARE_REVIEW_PENDING / HARDWARE_ACTIONS=0。**
**审查基线：`d6bbc1b5795fc12d27ee8aaa0939f0408dc43699`。**

本轮全部工作在 `reports/reviews/phase8-m8-r7f8-executor-adaptation/` 新目录内；
真实设备动作 **0**：未开任何 COM、未导入 pyserial 真后端、未跑 pyOCD、未跑 Keil、
未启动 Logic/分析仪、未切供断电、未 arm/start/学习/取消；
产品 .c/.h、固件、工程文件、旧执行器、旧现场原件、旧批准账与 R7F7 输入 **字节不变**（下方有复算）。
本轮不生成 REAL_GO 计划，不开 R7F8 的真实批准账，不进入现场执行 —— 交付即提交复审并停止。

---

## 1. 做了什么（任务书 §3 的四项，加两处被反例照出来的既有缺口）

派生执行器 `tools/m8r3_serial_chain_r7f8.py` 由 `tools/r7f8e_build_executor.py`
从**旧执行器原字节**按 **29 处逐字锚点编辑**生成（每处必须恰好命中一次，否则 `EDIT_REFUSED`）；
`--verify` 在仓外重建并逐字节比对：`VERIFY|PASS|重建=245730 B 仓库派生件=245730 B|sha=4c962170b2357eef`。
差异 **3977 → 4447 行（+485 / −15）**；补丁原件 `patches/r7f8e_executor.patch`
38672 B，sha256 `27d3d49133721775aadc765a943499a7f5fa015bf324ed0466bf75c5fb55bdba`。

**新执行器身份（本轮登记，不冒用旧摘要）**：245730 B，
sha256 `4c962170b2357eef94afd73d43790d61880635a3d13287d29d543035768d6fed`。
旧执行器复算仍为 219491 B / `33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca`
（见 `proof/r7f8e_case_matrix.txt` 的"身份_旧执行器未被动过"）。

| 项 | 落地位置（派生件内） | 合同要点 |
| --- | --- | --- |
| E1 有界状态轮询 | `poll_gate_of()` / `poll_observe()` / `poll_command_step()` 的 `in_progress` 申报 | 只发预先声明的只读查询；`max_attempts`＋`until_seconds`＋`interval_seconds` 三个上限同时生效，且被会话／通电／运动三者最早截止夹住；RUNNING/CANCEL_PENDING 才算进行中，DONE 正例还要 owner=FREE＋凭据＋ADC/SG 独立样本达数；协议矛盾/坏字节/缺提示符**首份**即停；纯查询不扣 learn/cancel，也不当 retry |
| E2 动态 JOB_ID | `bind_job_id()` / `resolve_job_cmd()` / `check_job_expect()` | 只从**本步自己**那一份完整响应里唯一整行的 `learn STARTED id=` 取值，先过原字节门与步骤判据再发布绑定；ID 限 `1..999999999`（产品 `learn_cancel JOB_ID must be 1..999999999`）；命令面只有 `diag_profile learn_cancel @<绑定名>` 这一种形态，无 eval/exec/format/shell 展开；派生命令与 ID 来源 RX hash 进台账，不回写获批 plan |
| E3 取消显式额度 | `QUOTA_COMMAND_KEYS` 新增 `("diag_profile learn_cancel","diag_profile_learn_cancel")` + `validate_r7f8e` 的额度/形状门 | 缺键、键不符、`n≠1`、额度 0、已耗尽全在那条 write 之前具名拒；缺省新键按 0 处理，不把旧计划自动升级为含取消授权；已终态的取消仍算一次发送不退额；`learn_status` 不误计 |
| E4 通电截止与固定清理 | `class EnergizeBudget` + 会话顶部 `ARM_ENERGIZE_DEADLINE_EXCEEDED` + `ARM_ENERGIZE_DEADLINE_UNDECLARED` | 通电时限在 `motor_arm` **写入尝试之前**建立，未 start、学习/查询/等待、正常 stop 后仍 armed 的区间都在它里面；清理仍是固定三条 `motor_stop→motor_disarm→motor_status`，未开发可编辑清理脚本；预留耗尽/账本异常照发撤能但本场不得 OK |
| 附带修正 T3 | 会话 `finally` 里 `cleanup(..., quota if plan else None, ...)` | 旧写法传形参 `quota_box`，单会话路径永远为 `None` ⇒ 清理预留账根本不接（L3 第一版因此错报绿色成功）。dispatch 现场路径本来就传同一个对象，**行为零变化**；元负控 `X5` 作证 |
| 附带修正 T4 | `validate_plan` 的 `PLAN_TOOL_ARGV` detail 取值改为 `(t or {})` | 计划缺 `tools.download/readback/reset` 时旧件当场 `AttributeError` 崩在建账之前，而不是给出具名 FAIL；改后门照常判红（`proof/r7f8e_stub_package.txt` 保留了旧件崩溃原文） |

## 2. 离线验证（任务书 §4 全 11 组，全部 stub transport / sim backend / SimClock / FakePort）

| 判据集 | 结果 | 说明 |
| --- | --- | --- |
| `proof/r7f8e_case_matrix.txt` | **判据=65 FAIL=0** | B 身份与绑定 / D 正常 DONE / C 活动取消 / T 异常终态 / P 轮询边界与字节门 / Q 额度 / E 通电与运动 / L 清理对账 / M 首因与拒绝 / I 身份反例（进程内）/ R 原始字节探针，加 4 例源码锚点与"故意写错预期必须判红"的自证 |
| 元负控（真运行被破坏副本） | **5 例全部命中** | `X1 固定 ID=1`（摘门后真的发出了 `diag_profile learn_cancel 1`）、`X2 把任何终态当成功`（TIMEOUT 被等成 `POLL_WINDOW_EXPIRED`）、`X3 移除取消额度行`（摘门后取消命令无账发出且会话变绿）、`X4 把 arm 截止改回 start 起算`（`ARM_ENERGIZE_DEADLINE_EXCEEDED` 消失）、`X5 清理预留账改回旧写法`（L3 错报 success=True）。没有一处是靠打印 "INJECTED" 代替执行 |
| `proof/r7f8e_stub_package.txt` | **判据=27 FAIL=0** | STUB_ONLY 取消＋DONE 合成整包（JOB_ID 非固定，台账 `JOB_ID_BOUND={cancel_job:17, done_job:73}`，实发 `diag_profile learn_cancel 17`），加等价负例四场：`N1_拒绝=BUSINESS_FAILED`、`N2_次数上限=POLL_ATTEMPTS_EXHAUSTED`、`N2b_窗口到期=POLL_WINDOW_EXPIRED`、`N3_证据不完整=NUL_BYTE_IN_RESPONSE`；每场都给额度账、首错、清理与撤能披露。同一份合成计划走 `--transport real` 在**任何端口动作之前**被具名拒绝，台账里 0 条开口事件 |
| `proof/r7f8e_no_regression.txt` | **判据=8 FAIL=0** | 同一份冻结 R7F7 定稿计划：旧件 `checks=57 fail=1` → 派生件 `checks=62 fail=1`。旧判据 0 条被洗掉；新增 5 条全部以 `R7F8E_` 开头；唯一既有 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`（空运动链的既有检查器缺口，R7F8 草案轮已登记） |
| `proof/r7f8e_case_matrix_before_fix.txt` | 修前基线（18 条 FAIL） | 本轮第一次真跑判红原文留档，含 T1 的 `Ledger.ev(kind=…)` TypeError 与清理段 TX 判据恒真空两处自伤 |

计划与行表交付件（合成、STUB_ONLY，不含端口/UID）：
`plan/plan_r7f8e_stub.json` 8215 B、`plan/plan_r7f8e_stub_window.json`、
`plan/rows_r7f8e.json` 3219 B 与三份负例行表（`_refused` / `_never_done` / `_corrupt`）。
每份行表的 `_about.kind` 都是 `SYNTHETIC_STUB_ROWS_NOT_BOARD_EVIDENCE`。

## 3. 勘误与草案同步

- `ERRATA_R7F8E.md`（9593 B，sha256 `6740ba6a7cb934b7d8e6e5b8a0f85bc87d4e31644c8472d335b7e757211431e4`）
  逐字引用我自己上一轮的三句旧解释（"三源"、"取消无额度只读"、"取消窗口就是一拍量级"），
  给源码与行为反证：`diag_relative.c:669` 置位 1u、`:674` 置位 2u、`:385` 要求 `ok_src_seen == 3u`
  ⇒ 3 是 `1|2` 的**两源位掩码**，没有 IMU 正常位。旧报告、旧 116 条判据输出、§22、上一轮交接件
  **一律原样保留**，不假装旧工具早已检出这些语义错误。
- 现场草案：`reports/reviews/phase8-m8-r7f8-closed-loop-draft/R7F8_DRAFT_MERGED.md`
  更新为 36273 B（sha 前缀 `e9a5253de024`）。正文按行逐字承接任务包里的
  `R7F8_DRAFT_UPDATED.md`（33703 B / `1c7c94b3297b`，`INHERIT_EXACT|equal=True|正文行=272`），
  末尾新增"本次合并与修订记录"节（与上一版 31807 B / `270383c12b1c` 的行级差异 55 行、
  新增节 2、消失节 1、逐节计数 13 行）。`A_DRAFT_ORIGINAL.md` 仍为 24780 B / `b572c1928f60`，未改。
- 任务包原件（任务书、修订草案、IDENTITY、A 的 23 项输出、主机探针与失败首次尝试、包内清单）
  逐文件归档在 `inputs/A_R7F8E_PKG/`：24 个文件、165145 B，按盘上原字节进本轮清单。

## 4. 仍然没有验证的东西（任务书 §6，本轮一律不声称）

- 真实设备动作 0；未构建新固件；板端最新证明仍是 R7F7 的镜像
  `23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963`（本轮只作为字节身份参照引用）。
- 正常巡航下的 DONE 学习、活动中的取消、SG/ADC+IMU 相对保护的实际触发**全部未验证**；
  矩阵与整包证明的是**工具会怎样收口**，不是板上行为。
- 加减速窗内的冲击保护本轮不声称通过：任务书 §2 已裁定 ACCEL/DECEL 早退会连冲击一起跳过，
  目标刺激必须放在已确认的巡航窗内。
- R7F8 现场轮的机械与刺激方法、速度窗、各段时限、真实动作预算仍待操作者/A 定稿；
  恢复（`ok_src_seen`）与 `fault_reset` 的关系、票据消耗顺序的行为级证据仍留现场轮。
- 主机 C 探针的四例是**合成输入下的主机逻辑证据**（A 包内原件，含 1 条既有夹具的
  misleading-indentation 警告，未声称 0 warning），不写成板端字段已验证。

## 5. 复算口径与本轮自伤

- 散列口径：A 的 `IDENTITY.json` 绑的是 **Git blob 字节**；本包 `ROUND_SHA256SUMS.txt` 按**盘上原字节**
  直算。`core.autocrlf=true` 下同一文本可以有这两种长度（`motor.c` 盘上 134298 B / blob 131732 B，
  CR 数 2566 = 行数）。新目录已加 `-text` 规则，故本轮交付件的盘上字节与远端 blob 字节一致；
  这一点由推送后的逐 blob 回读独立复算，不由本文件自证。
- 本轮自伤（都是自己的工具/夹具错，逐条已在证据里留原文）：① 台账字段撞 `Ledger.ev` 首位参数名；
  ② 想用 JSON 夹具表达非法 UTF-8（表达不了，改进程内灌原始字节）；③ 把清理段 TX 按字面 `"\\r"` 切，
  导致一条也没切出来 ⇒ 判据恒真空；④ 用例名里的 `/` 当目录名用，账本写进两层目录；
  ⑤ 元负控 `X2` 一开始写成恒真比较（`gone=None`）；⑥ 合成行表把"响应行列表"又套了一层
  ⇒ 喂端口时 `join` 当场 TypeError；⑦ 首错去 `DISPATCH_SUMMARY` 里找 `code=`，而那一条在整包级恒空；
  ⑧ 把"额度未申报"与"作业未绑定"两道都在写前的门混着测，Q1–Q3 全部被无关门抢答；
  ⑨ 计划缺 `tools` 面时旧件崩溃而我没有先复算就以为是自己写错；⑩ 声明"逐字节承接"之前
  没有让工具自己剪节复算一遍（现补 `INHERIT_EXACT` 一行）。
- 材料缺口（如实登记）：任务书与被引用的现场草案最初不在会话附件里，我先按用户正文口径做了一版
  自设计适配；拿到 `NS800_R7F8E_Executor_Offline_Adaptation_20261105.zip` 后，那版自设计件
  （`field/m8r4_serial_chain.py`、`tools/r7f8e_negative_checks.py` 及其 proof）已整体废弃删除，
  本包全部命名、键名、门语义与交付清单按 A 的任务书 §3/§4/§5 重做。

## 6. 停止点

按用户与任务书的要求，本轮**不进入现场执行**：不开 R7F8 现场账、不做 dispatch、不需要新的固件构建。
下一轮若要把这份适配用到现场，需要 A/操作者先定稿刺激方法、速度窗、时限与真实动作预算，
并单独批准现场执行包。设备保持上一轮的收口约定（保持通电，不索要断电动作）。
