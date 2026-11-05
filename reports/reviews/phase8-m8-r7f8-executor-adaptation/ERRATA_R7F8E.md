# R7F8E 勘误：本轮复核里被证伪的三条旧解释

**任务：M8R7F8E-offline-executor-adaptation-20261105 §5.4**
**状态：OFFLINE_DEVELOPMENT_GO / HARDWARE_ACTIONS=0。**

本轮不重写任何已提交的历史件。下面三段引用**逐字**来自 `d6bbc1b5795f`（R7F8 草案离线核对轮）
的我自己的输出，位置与行号一并登记；原文一律**原样保留**，包括那份 116 条判据的输出：

| 原件（未改动） | 字节 | 位置 |
| --- | --- | --- |
| `reports/reviews/phase8-m8-r7f8-closed-loop-draft/DRAFT_REVIEW.md` | 见 `ROUND_SHA256SUMS.txt` | 第 44–52、62–68、80–86 行 |
| `reports/reviews/phase8-m8-r7f8-closed-loop-draft/proof/r7f8_draft_checks.txt` | 116 条，FAIL=0 | 原样保留 |
| `reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md` §22 | 第 926–931 行 | 原样保留（本轮只追加 §23） |
| `reports/agent_handoffs/phase8-m8-r7f8-closed-loop-draft-offline.{md,json}` | 第 42 行 / 第 90 项 | 原样保留 |

**这三条旧解释当时没有被任何一道检查器抓到。** 那句 116 条 FAIL=0 是"当时那 116 条判据"的
运行结果，不是"我对源码的每句解释都对"的凭据；本轮也不得把这三条改写成"旧工具早已检出"。

源码行号绑定本包输入（`d6bbc1b` 那一份产品源码），不按后续 tip 漂移。

---

## 勘误 1 — "恢复要 ADC+SG+IMU 三源"是错的：`ok_src_seen` 是**两源位掩码**

逐字旧句（`DRAFT_REVIEW.md:48–49`，同一说法另见 `run_record.md:929–930`、
`phase8-m8-r7f8-closed-loop-draft-offline.md:42`、同名 JSON 第 90 项）：

> "两源/三源正常证据"属于引擎侧票据解除条件 `diag_relative.c:384-385`，而且是 **`ok_src_seen == 3u`**
> （ADC+SG+IMU 三源），不是两个（`A_RECOVER_3SRC`）。

反证（`rt-thread-master/.../applications/diag_relative.c`）：

- `:669` SG 正常样本累计的是 **`e->ok_src_seen |= 1u`**；
- `:674` ADC 正常样本累计的是 **`e->ok_src_seen |= 2u`**；
- `:376` 定义 `ok_tally()`，`:385` 的解除条件是 **`e->ok_src_seen == 3u`**。

`3 == 1|2`，即**两个源都正常过**（位掩码），源码里根本没有"IMU 正常位"。我把十进制的 3
当成了"三个源"，这是把位掩码读成计数——错了。A 在任务书 §2 的"恢复与 fault_reset"一行给出的
裁定与此一致。

主机侧行为反证（未修改的产品 C，编译自本包输入字节）：
`reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8E_PKG/proof/probe_run.stdout`
的四例里

- `two_source_recovery|seen=3|imu_seq=0|severe=0` —— SG 与 ADC 正常、**IMU 新样本序号仍为 0**
  时，引擎严重态已经解除；
- `single_source_not_recovered|seen=1|severe=1` —— 只有 SG 新鲜、ADC 身份冻结时不解除。

（该散列文件在 A 包内；本轮把它当**输入原件**只读引用，不改写，也不把它的合成结果说成板端实测。
仓库内的对应说明见 `R7F8E_DELIVERY.md` §3。）

同时保留 A 已裁定的另外两点：引擎回落计数**不是** `fault_reset` 的门；回落也**不等于**事故锁存
被清除或保护被重新使能。

**本轮落成的新判据**：不再用脚本关键字声称"两源/三源"。矩阵里与恢复有关的只有源码锚点
`M3_七原因共用一次票据(源码锚点)`，它显式声明"票据消耗顺序的行为级证据仍待现场"；
恢复合同本身留给现场轮，本轮不声称任何已验证。

---

## 勘误 2 — "`learn_cancel` 是无额度只读步骤"是错的：它改作业状态，必须单列额度

逐字旧句（`DRAFT_REVIEW.md:65`）：

> `learn_cancel <ID>` 不在额度表内，可作为无额度只读步骤发送。

反证分两层：

1. **命令语义**：`diag_profile learn_cancel <ID>` 走 `rel_job_cancel()`，把该作业置为
   `CANCEL_PENDING`（打印 `[DIAG] cancel id=%u REQUESTED st=%s (motor untouched)`）。
   它改变的是持久运行状态，不是查询。旧映射里没这条**属于工具的覆盖缺口**，
   不能反过来当作"这条命令免记账"的依据（任务书 §2 "learn_cancel" 一行的裁定）。
2. **旧执行器的实际行为**：因为 `QUOTA_COMMAND_KEYS` 漏了它，一条未申报额度的取消
   会在旧件里**一条账都不扣地发出去**。这是可发的写动作绕过额度，不是"只读"。

**本轮落成的实现与判据**：

- 派生件把 `("diag_profile learn_cancel", "diag_profile_learn_cancel")` 加进集中映射
  （9 键 / 10 前缀 → 见 `proof/r7f8e_case_matrix.txt` 身份行的复算口径）；缺键、错键、
  `n≠1`、额度为 0、已耗尽都在**那条 write 之前**具名拒绝。
- 矩阵 Q 组逐条命中：`Q1_取消未申报额度不发=QUOTA_STEP_UNDECLARED`、
  `Q2_取消错额度键不发=QUOTA_KEY_MISMATCH`、`Q3_n不为1不发=QUOTA_N_MISMATCH`、
  `Q4_额度为0先拒(缺省不自动升级)=QUOTA_EXHAUSTED`、
  `Q6_已终态的取消仍消耗一次不退额=CANCEL_NOT_CAPTURED`；
  `Q5_learn_status不扣learn与cancel` 证明纯查询轮询不消耗 learn/cancel。
- 元负控 `X3_移除取消额度行` 真运行被破坏副本：摘掉那一行后，同一输入的首错从
  `QUOTA_STEP_UNDECLARED` 变成空（会话绿色成功），且台账里**确实发出了**
  `diag_profile learn_cancel 17` —— 危害可观察，不是打印一句 "INJECTED"。

---

## 勘误 3 — "取消窗口宽度就是一拍量级"不成立：窗口不是由那 10 ms 与 20 ms 直接推出的

逐字旧句（`DRAFT_REVIEW.md:82–83`）：

> 后台 deadline 12 s 为编译期常量、线程 10 ms 一拍、
> shell 侧有界等 20 ms（⇒ **取消窗口宽度就是一拍量级**，草案"固定时序 + 有界查询"要按这个尺度写）

反证与更正口径：

- "线程 10 ms 一拍"与"shell 有界等 20 ms"是**两处不同的等待**，二者相减/相比都推不出
  "有效活动窗口就是一拍"。作业的有效活动期还取决于 `N`、SG/ADC 新样本到达速度、调度、
  传输时延与步间隔；`diag_profile learn_status` 的一次查询本身就要过原字节门与提示符门。
- 行为侧证据：本轮矩阵里同一条 `diag_profile learn_status` 轮询，
  在申报 `max_attempts` 时先到的是 `POLL_ATTEMPTS_EXHAUSTED`
  （`P2_次数上限具名结束(窗口更长也停)`、整包 `N2_次数上限`），
  抬高次数后先到的是 `POLL_WINDOW_EXPIRED`（`P1_时间窗到期具名结束`、整包 `N2b_窗口到期`）。
  两个不同界限在同一家族里都能先到——窗口不是"一拍"这种单一定值。
- 定稿口径改为：**刺激与查询的时序参数必须由操作者/A 现场给定**；执行器只做"申报的窗必须
  被兑现，且不越过会话／通电／运动三者最早截止"的硬约束。
  本轮**没有**为了造出可观测的窗口去改产品 deadline（12 s 编译期常量未动），
  也没有暂停采样。

---

## 附带登记：本轮在自己工具里发现的四处缺口（都属于工具侧，不是产品事实）

这几条都是"新写的反例第一次真跑才暴露"的形状，逐条给修前现象与修后判据；
旧执行器与旧件字节未动，修都在派生件里。

| # | 缺口 | 修前现象（原样留档） | 本轮处置 |
| --- | --- | --- | --- |
| T1 | 取消结果台账字段叫 `kind`，而 `Ledger.ev(kind, **kw)` 的首个位置参数就叫 `kind` | 取消步当场 `UNEXPECTED_EXCEPTION / TypeError: Ledger.ev() got multiple values for argument 'kind'`（`proof/r7f8e_case_matrix_before_fix.txt` 的 B1/C1/B6） | 字段改名 `row`，并在注释里钉住"ev 的首位参数名不可复用" |
| T2 | 非法 UTF-8 无法经 JSON 夹具表达：夹具→端口之间要过一次严格 `encode("utf-8")` | 矩阵第一版当场 `UnicodeEncodeError: 'utf-8' codec can't encode character '\udcff'`（本轮第一次运行的原始报错在开发记录里，未入库为"证据"） | 该组改为进程内向**同一条会话读路径** `read_until_prompt` 灌原始坏字节（R1–R4 探针，沿用 R6B `independent_probes.py` 手法）；会话级另开 `P5_轮询首份坏字节响应即收口不重试` |
| T3 | 既有：单会话路径下清理预留账根本没接上——`cleanup(..., quota_box if plan else None, ...)` 传的是形参，而 CLI 会话从不传 `quota_box` | 同一份 `cleanup_motor_disarm=0` 的计划：清理三条照发，但 `cleanup_overruns` 恒空、会话报 `success=true`（L3 第一版就是这么绿的） | 派生件把这一处改成传有效账本 `quota`；dispatch 路径本来就传 `quota_box`，`quota` 与它是同一个对象，**现场路径行为零变化**。`L3` 现在具名 `CLEANUP_RESERVE_EXHAUSTED` 且 `success=False`，元负控 `X5_清理预留账改回旧写法` 证明这道门不可省 |
| T4 | 既有：`validate_plan` 在计划缺 `tools.download/readback/reset` 时**崩**而不是具名 FAIL（`" ".join(t.get("argv", []))` 对 `None` 取值） | 旧件对同一份合成计划 `AttributeError: 'NoneType' object has no attribute 'get'`（`proof/r7f8e_stub_package.txt` 的"旧件在同一输入上崩在建账之前"一行，rc=1 原文留档） | 改为 `(t or {}).get(...)`：门照常判 `PLAN_TOOL_ARGV|...|FAIL`，空 argv 只落进 detail |

T1/T2 是我本轮新写的派生件/夹具自己的错，T3/T4 是旧执行器里既存、被本轮新反例照出来的缺口。
四条都没有借"反正旧件也是这样"放过：T3、T4 都在派生件里改掉并各配了可复算的判据。
