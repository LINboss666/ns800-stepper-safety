# R7F8F：执行器返修＋一次巡航作业实机验收

2026-11-05 · A 给执行 agent 的合并任务书。

本任务包含修复、离线门和现场验证三个连续部分，不拆为新的纯离线任务轮。产品源码、固件和旧证据均冻结。操作者对下列现场范围作一次明确批准即可；批准可以在修复前给出。修复及离线门通过后，同一批准内直接执行一次现场计划，不再等 A 签一次软件复审，也不另开“登记轮”。若批准尚未取得，可以先修工具，但不能发现场命令。本文不是已经发生的批准。

## 1. 身份与复审结论

- 被审 tip：`e97aaa4736208fa7fa7e9a86768d5305ab2e4cff`；父提交 `42d7f7d`；原草案基线 `d6bbc1b`。
- 被审派生执行器：`reports/reviews/phase8-m8-r7f8-executor-adaptation/tools/m8r3_serial_chain_r7f8.py`，245730 B，SHA256 `4c962170b2357eef94afd73d43790d61880635a3d13287d29d543035768d6fed`。
- 冻结旧执行器：219491 B，SHA256 `33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca`，本轮不改。
- 产品树 `rt-thread-master`：`32470c7ed44ef6ef5f762490a5416912a336cdd9`，对应已签收产品 `00718b49e65546aa0bae84006059011d68284c4a`。
- 本轮沿用已在板的 297452 B 镜像，SHA256 `23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963`。不重新构建、下载、回读或复位。

A 重跑了 B 的矩阵 65/0、stub 29/0、无回归 8/0，重建 `--verify` 相等。T3（清理使用有效 quota 对象）与 T4（工具缺项保留具名判红）接受。以上通过不覆盖新发现的反例，当前派生执行器暂不签为可直接承担运动的版本。详见 `A_REVIEW_R7F8E.md` 与附带反例。

## 2. 本轮只修工具的五项

### F1. 作业轮询先查合同，再决定是否继续等

固定产品格式解析 STARTED 和 `learn_status` 两行；自定义正则不能替代固定语义或放宽它。绑定参数须与实际发送的 learn 命令完整一致（N、LO、HI、DIR），不只与 plan 中另一份声明自洽。后续每次查询都关联同一真实 JOB_ID 和受理参数。

依据产品枚举：FREE=0、BY_ZERO=1、BY_JOB=2。RUNNING 必须 owner=2、cred=1/0、why=RUNNING；CANCEL_PENDING 必须 owner=2、cred=1/0、why=CANCEL_REQUESTED。正常 DONE 必须 owner=0、cred=1/1、why=COMPLETE；CANCELLED 必须 owner=0、cred=1/0、why=CANCELLED。计数按真实无符号字段解析，同一作业的 ADC、SG 计数分别不倒退。`rel_cap_feed` 不在某一源达到 N 时停止该源累加，因此不能用 count<=N 当门，也不能要求 DONE 恰好 N。各行必须来自同一完整响应，不跨查询拼接。

只有字段全部自洽且仍处于当前步骤允许的活动态，才可继续有限轮询。错误 ID、owner、凭据、参数、原因、计数倒退或缺行在第一次出现时 STOP，不能等后来的 DONE 把前错洗掉。FAILED/TIMEOUT 为首错，正常场景不放行。取消前查询不允许 CANCEL_PENDING，取消后可允许 RUNNING/CANCEL_PENDING 直至 CANCELLED。

DONE 还须看到产品真实输出的 `job data`、`job thr ... FIELD_UNVERIFIED`、`job DONE result`，ADC/SG 计数均至少 N，且与候选统计中的各自计数相等；核统计范围、SG 新样本序号及凭据。缺候选输出不能凭两行状态宣告可提交。本轮后续 `diag_profile show` 再核候选 VALID、窗口、配置签名、样本计数。不要把相同数值误当旧样本，也不要把输出边界推成内部精确时延。

修正主机正向夹具中的 owner=1；修前缺陷输入原样保留。本包 P1–P5 各拆成独立故障形状再补正向例，不能只让一个条件挡住其他错误。

### F2. arm 首次响应受通电截止约束

arm 的发送尝试之前建立计时义务，随后再计算该条命令的实际截止。所有 write 前检查、回读、排残留和等待取会话绝对截止、arm 截止、start 截止及步骤截止的最早值；不能先算旧 step_dl，再建立 arm 计时。

会话截止应固定为会话 t0＋总时限，不能每步用“当前时刻＋总时限”续期。新反例至少包括 arm 回包延迟超限、排残留耗尽剩余时间、poll/read/watch 到期，以及截止后无业务 TX、仍进入预留清理。主机截止只约束主机调度，不宣称物理实时断能保证。

### F3. 一场只允许一次 arm

本轮不用多次通电框架。计划门拒绝第二个 arm；运行时在第二次 arm 写入尝试前再次拒绝，覆盖直接调用接口。不能在 disarm 后因旧 t0/actual 残留而让第二次 arm 失去计时。本轮也只允许一次 start。义务关闭须有真实成功 disarm 输出及相邻可信禁能快照；泛匹配“DISARM”字样不足以解除义务。未知结果继续保留撤能义务。

### F4. 运行状态必须确实在使能侧

本轮的 RUNNING/CRUISE 判据必须同时要求 request=HIGH、pad=1、expected_ENN=LOW、ENN_fb=0、verdict=OK、armed=1、health=OK、cur=tgt=300、dir=1。不能把 ENN HIGH 或 CUT_BY_HARDWARE 当正常运行。修正新夹具、派生执行器的相关检查，并加一个字段一个字段变错的反例。冻结旧执行器与旧计划字节不改。

arm 后尚未起转：state=IDLE、cur=tgt=0、armed=1，同时 request=HIGH/pad=1/ENN LOW/0/OK。正常 disarm 后：armed=0、cur=tgt=0、request=LOW/pad=0/ENN HIGH/1/OK。

### F5. 有界固定收口

保留固定三条 `motor_stop → motor_disarm → motor_status`，不开发任意清理脚本。在本轮计划显式开启固定 IRQ 尾部：若曾尝试 `safety_irq_enable`，在三条之后、同一端口内，再发送一次 `safety_irq_disable → safety_irq_status`。各有独立预留额度，IRQ 尾部失败不能阻断前面的撤能；无第二次开口、无清理重试。这个固定尾部是本任务明确增加的范围。

清理保留段总计 7 s，包含三条电机清理和两条 IRQ 尾部，不在超时后无限等提示符。清理失败与业务首错分列，不能被后来的关闭端口掩盖；清理预占失败仍走撤能并具名报账目错误。任何失败、取消或超时均不得进入后续 learn/commit/再次 arm。

## 3. 离线门与自动续行规则

1. 在同一 R7F8 工作目录维护派生执行器、构建配方、计划、rows、夹具及判据；保留修前版本。仓外重建必须逐字节等于提交的派生执行器，不能只手改产物。
2. 重放本包反例，逐条给出修前被错误放行、修后在首个坏响应 STOP 的证据。P6 另核 2 s 截止前进入收口，P7 第二次 arm 发送前拒绝。正向夹具须改成产品真实 owner/ENN/完整 DONE 输出；不能为保留原有绿色而保留假合同。
3. 保留原 65/29/8 覆盖；允许为修正错误正向夹具更新输入和期望，逐项说明变化。冻结 R7F7 旧行不删除、不改写。无运动包既有 `PLAN_MOTION_CAP_BUDGET` 单独记 N/A；本轮真实有运动的计划该门必须 PASS，不得沿用该豁免。
4. 生成本任务 §4–§6 的最终 real plan、真实源码行形 rows、完整总额度与 source manifest；核运行流程和所有首错路径，包括断串口、候选缺失、取消过晚、截止和清理失败。不能把 stub 的短行（如 `ARM ok armed=1`）当产品真实回包。
5. 检查产品树、BIN、冻结执行器、旧证据未变；新代码导入、身份、两级台账、额度预占、隐私和原始字节门仍有效。
6. 对最终派生执行器、plan、BIN 三份字节算身份，生成机器回执 `offline_gate.json`，逐门具名列结果；失败不得写 overall PASS。批准账只在这些最终身份已确定后开，令牌取操作者真实原话，不能借旧批准或由 agent 自造授权句。
7. **如果操作者已经一次批准了本任务的具体现场范围，以上门全过后直接上板，不再停下来索要 A 的软件复审。** 仅当发现产品必须改动、现场条件变化、计划范围需放宽或离线门无法通过时停止，报告具体差异；不靠额外动作碰运气。

离线准备、修复及证据先做到可复算；不另安排一次“只归档准备”的提交轮。本轮允许最后将修复与现场结果一起交付。A 不远程操作硬件，现场执行由 B 和操作者承担。

## 4. 现场定量边界（本任务的唯一运动段）

| 项目 | 固定值／要求 |
| --- | --- |
| 在板固件 | 上述 23691e8d…，身份连续；不重新下载／回读／复位 |
| 机械条件 | 操作者在场，历史空载台架未增加负载，轴可自由连续转动，无新行程约束，固定可靠，现有 SW1 可立即触及 |
| 电气条件 | 沿用已登记 24 V；未换模块、未调 VREF、未改细分或线圈配对；1.5 A 仅为用户报告额定值，不据此升流 |
| 驱动档位 | IRUN=20、IHOLD=8、DLY=4、TPD=8、VSENSE=1，先核目标配置再 apply 一次，不加 set／升流操作 |
| 模式 | MONITOR_ONLY；本轮不进入相对保护模式，不造 SG／ADC／IMU 故障 |
| 方向／速度 | DIR=1；目标 STEP 300 Hz；沿用固件加减速 2000 Hz/s，不换算为已测转速 |
| 学习窗 | LO=250、HI=350、DIR=1；zero N=32，取消作业 N=32，完成作业 N=8 |
| 运行／通电截止 | start 尝试起 12 s；arm 尝试起 18 s，分别生效；正常 stop 必须在 start 后 8 s 内发起，为零速和 disarm 留时间 |
| 会话／收口 | COM5 一个会话；总 90 s 包含 7 s 收口预留；一次 dispatch，无重试 |
| 独立禁能 | 现有 SW1；异响、失步、机械异常或不能确认撤能时，操作者立即硬件禁能并如实记录，不等待 UART |
| 分析仪 | 本轮 0 次，不动探头；不以仪器未采集阻碍软件作业验证，不声称物理波形或 DIAG 极性已测 |
| 供电 | 正常结束保持 VM＋MCU 供电；不为清 RAM 重启或断电 |

300 Hz/DIR1 取自仓库 M4 曾连续 60 s 且有有效 SG 快照的工况，属于历史选点依据，不是新镜像已验证。当前执行长度和次数仅以上表为准。运动所需极性确认位／IRQ 由本任务单独列额度，采用既有台架与 SW1 受控试验依据；**软件 confirm 不升级为 polarity_measured=true**。若操作者不能确认上述机械／电气连续性，报告变化，不自动照跑。

## 5. 同一会话内的固定业务顺序

每一步先过原始字节、完整回包、命令身份、业务危险门，再查下列精确字段。最大查询次数包含首次；poll 的 window 是整个步骤的时限，不能每次刷新。除列出的轮询外不发补充查询。除下述 R7F7 遗留记录外，FAILED 不作全局白名单。

| 序 | 命令／步骤 | 必须看到的结果与上限 |
| --- | --- | --- |
| 1 | system_status；motor_status；safety_irq_status；diag_status；diag_profile learn_status；tmc_config_show | READY/fault0、禁能、零速、IRQ CLOSED、极性未确认、UNCONFIGURED、MONITOR_ONLY；允许精确的旧 id1 FAILED/WINDOW_BROKEN owner0 cred1/0，或有据登记的上电后 IDLE/FREE；其他前态停止。目标驱动参数必须与 §4 一致；不靠 apply 修未知状态 |
| 2 | tmc_config_apply CONFIRM；system_status | CONFIGURED、IFCNT 确认成功，档位一致，仍禁能／未武装，门由0x18到0x8 |
| 3 | diag_profile zero 32；diag_profile learn_status | 静止零点成功；采集器 FREE、cred=1/0。旧终态可以保留，不要求人为清记录 |
| 4 | safety_polarity_confirm CONFIRM；safety_irq_enable；safety_irq_status；motor_status | 门开放且 arm mask=0，仍零目标／未武装。保留物理极性未测边界 |
| 5 | motor_dir 1；motor_status | DIR1、state IDLE、armed0、cur=tgt0、accel=decel=2000、可信禁能 |
| 6 | motor_arm；motor_status | 产品真实 ARMED 成功行；IDLE/armed1/零目标/ENN LOW0。发送 arm 前开始通电计时 |
| 7 | motor_target 300；motor_start；motor_status 有界轮询 | 先 arm 再 target，再 start；最多4次、间隔0.1 s、整体1 s内到 CRUISE/300/300/DIR1，且 §2 F4 的使能反馈全满足。ACCEL 可按完整运行侧字段有限等，不接受无关异常 |
| 8 | diag_profile learn 32 250 350 1；diag_profile learn_status | 绑定真实 CANCEL_JOB_ID；下一次查询必须仍为同一 RUNNING、owner2、cred1/0、参数一致。若已经 DONE，记取消窗口未命中并收口，不新建替补作业 |
| 9 | diag_profile learn_cancel @CANCEL_JOB_ID；learn_status 有界轮询 | 同一ID REQUESTED；最多3次、间隔0.1 s、整体1 s内 CANCELLED、owner0、cred1/0。NOT-CANCELLED 不是成功。取消只改作业，不作为电机停机 |
| 10 | motor_status | 仍正常 CRUISE/300/300/DIR1，明确取消没有代替 stop |
| 11 | diag_profile learn 8 250 350 1；learn_status 有界轮询 | 新的 DONE_JOB_ID，不能等于上一枚；最多12次、间隔0.1 s、整体2 s内 DONE/COMPLETE、owner0、cred1/1、两种计数各至少8、与候选统计一致，完整候选输出。中间查询响应可验证；不要求一定能观测到 RUNNING，也不补发作业 |
| 12 | motor_stop；motor_status 有界轮询 | start 后8 s内开始 stop；最多5次、间隔0.1 s、整体1 s内 cur=tgt0；仍 armed 时 request HIGH/ENN LOW 不当作禁能。失败立刻进固定清理 |
| 13 | motor_disarm；motor_status | 成功撤能、armed0、零速、LOW/pad0/HIGH1/OK；不凭 DISARM 子串解除义务 |
| 14 | diag_profile show；diag_profile commit；diag_profile show；diag_profile learn_status | 提交前 pending VALID，配置字段和统计对应 DONE 作业；旧 profile 的 match 在提交前可为 NO，不能据此误拒候选；提交 OK，随后 profile 与 pending 的 id/rev/配置/统计一致，match YES、cred仍1/1、ownerFREE。commit只发布RAM，仍 FIELD_UNVERIFIED；不发config_save |
| 15 | ps；free | 保存本次运行后线程栈高水位与堆数据，只报本工况，不称带载最坏栈 |
| 16 | finally 固定收口 | 电机三条；若曾尝试开IRQ，再一次disable/status。确认最后可信禁能与 IRQ CLOSED，关端口；任何失败保留首因与cleanup_errors |

READ_ONLY 的 `diag_profile learn_status` 只在首个遗留终态查询允许精确 F7 记录；后续分别用绑定作业的合同。不要通过广泛忽略 diag_profile 全前缀来绕过危险文本门。最终 show 中 printed config_save 提示不是实际发送命令，实发身份依 TX/命令回显核对。

如果正常 DONE 成功但 commit 被有效门禁拒绝：按失败记录并收口，不改 tier、不降低阈值、不重复学习或提交。参数窗口／受理耗时导致正常时限不足也不现场延长。

## 6. 动作总额度（包含正常与兜底）

| 动作 | 上限 | 备注 |
| --- | ---: | --- |
| dispatch / port_open | 各1 | 一个真实 COM5 会话 |
| tmc_config_apply | 1 | CONFIRM |
| diag_profile_zero | 1 | N32 |
| diag_profile_learn | 2 | 取消N32＋完成N8 |
| diag_profile_learn_cancel | 1 | 动态ID，发送前扣减 |
| diag_profile_commit | 1 | 仅RAM |
| safety_polarity_confirm / safety_irq_enable | 各1 | 原键 irq_polarity_prep 总2，分项台账 |
| motor_dir / motor_target / motor_arm / motor_start | 各1 | target300；start原键motor_run=1 |
| 正常motor_stop / 正常motor_disarm | 各1 | 单列业务额度，不能与finally混算 |
| cleanup_motor_stop / cleanup_motor_disarm / cleanup_motor_status | 各1 | 固定三条；stop和disarm全场上限各2 |
| cleanup_safety_irq_disable / cleanup_safety_irq_status | 各1 | 只在开IRQ尝试后触发的固定尾部 |
| 运行时长 | 12 s | motor_run_seconds_total=12，motion_cap_seconds_per_run=12 |
| 通电保持 | 18 s | 独立于运动时长 |
| 所列只读查询 | 依§5固定次数/轮询上限 | 不按响应缺失追加 |
| download / readback / reset / reboot / config_save / clear / tier / diag mode变更 / fault_reset / 升流 / 改VREF / 改细分 / PWM测试 / 注入 / SG-ADC-IMU物理刺激 / LA / 常规供断电 / 重试 | 全0 | 紧急人工SW1禁能另记真实事实，不被常规0额度阻止 |

派生执行器为新增业务写动作建立发送前核销，不把 motor_dir/target/arm/stop/disarm 漏表解释为只读。正常与清理用不同额度键，并明确映射上下文；两个账本各按职责登记，禁止退款。物理使能被拒也按写入尝试消耗 arm。实际spent可以小于caps（首错跳过剩余步骤），pending必须清晰，不为凑满额度补做动作。

## 7. 交付与能写的结论

同一轮交付：修前反例、返修配方与派生执行器、完整plan/rows/identity、离线门、批准原话及身份、一次现场原始字节/两级台账、逐项判读、清理与操作者观察、manifest、handoff；最后一次提交推送并逐blob回读。可保留必要自纠提交，不开展只为循环归档回读原文而继续提交的自指链。变更限本轮工具／计划／证据及惯例run_record，不动产品、固件、旧原件或演示端。

现场分别判：真实取消、正常DONE、RAM提交、正常零速/禁能、原始字节完整性、预算与收口；用户观察的连续转动与软件CRUISE分开报告。没有物理观测就写未观察，不以主机status替代运动事实。

即使全过，也只说明该空载工况的学习与停机流程完成。`polarity_measured=false`、`core_protection_verified=false` 保持；SG/ADC/IMU真实触发与恢复、Flash保存/复载、STEP物理时延、带载最坏栈和电流实测继续未验证。后续在同一R7F8草案上补目标刺激及相应动作预算，不重开无关准备轮。

## 8. 一次批准的可复制原话

操作者确认事实后可发送：

> 我在现场，仍为空载、轴可自由连续转动，供电/模块/VREF/细分/接线相较已登记台架无变化，SW1可立即操作。按R7F8F任务书执行修复、离线门及一次实机整包：DIR1/300Hz，运行12秒和arm通电18秒截止，一个COM5会话，取消N32、完成N8、RAM提交及正常停机禁能；不下载、不保存Flash、不重试。离线门全过后直接上板，不再等一次软件复审。

事实不符时只报告差异，不照抄虚假确认。本句是供操作者使用的批准模板，agent不得把模板本身当成已经收到的授权。现场原始字节若先失败，本轮停止并保留未执行项，不自动转入下一包。
