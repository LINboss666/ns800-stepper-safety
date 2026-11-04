# R7F8 保护闭环任务书 —— 草案离线核对（设备动作 0）

机器可判定版：`proof/r7f8_draft_checks.txt`（**116 条判据，FAIL=0**）。
收到两份输入：A 的草案 `A_DRAFT_ORIGINAL.md`（24780 B，sha256 前缀 `b572c1928f60`，LF），
以及操作者随后的四项补充与更正。**四项已就地合并**为 `R7F8_DRAFT_MERGED.md`
（31807 B，`270383c12b1c`，草案 §9 保留修订记录行），收到的原件字节不动。

草案自标 `DRAFT_NOT_AUTHORIZED`、动作额度全部 0/TBD，所以本轮**没有**任何设备动作：
未开串口、未调 pyOCD/Keil、未启动分析仪、未建审批账
（核准根目录 `C:/m8r_approvals` 下 `*R7F8*` 匹配 0 件，判据 `NO_R7F8_LEDGER_OPENED`）。
本轮也不生成可 dispatch 计划——草案 §5 明写它"不是可直接 dispatch 的命令表"。

## 1. 草案替我的 R7F7 证据加的话，逐条回原始字节复算：全部相符

| 草案 §1.1 的声称 | 我这边复算结果 |
| --- | --- |
| 清单 100/100、A 包原件 29/29、归档 53 件（不含清单自身） | `E_ROUND_100` / `E_PKG_29`（28 条 + 清单自身）/ `E_ARCHIVE_53`（清单=磁盘=53） |
| `readback.bin` 与签收 BIN 整文件逐字节相等 | `E_READBACK_BYTE_EQUAL` 297452 B 相等 |
| 启动捕获 3468 B、75 s 被动窗走满、IOIN 成功、required=PASS、READY/fault=0 | `E_BOOT_3468_75S`（实耗 75.0、`DRAIN_BUDGET_REACHED`） |
| `zero 32` raw 2055..2065；`STARTED id=1 n=8 win=450..550 dir=0`；终态 `id=1 FAILED why=WINDOW_BROKEN owner=0 cred=1/0`；计数 0/0 | `E_ZERO_2055_2065` / `E_STARTED_ID1` / `E_TERMINAL_LINE` / `E_SAMPLES_0_OF_0`（逐字比对归档原文） |
| learn 响应 77.563 s、终态查询 81.594 s、"中间没有其他实发命令" | `E_TS_77563_81594`、`E_NO_CMD_IN_GAP`（开区间内 RX_DONE=0） |
| 末态"请求 LOW／pad=0／ENN 高且反馈一致" | `E_ENN_CONSISTENT_BACKED`：`[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK` 与 `[Safety] … verdict=OK` 均在原文里 |
| armed=0/cur=0/target=0、IRQ CLOSED、极性未确认、protect_ready NO、drive_cfg UNCONFIGURED、arm mask 0x18 | `E_STATIC_FIELDS` / `E_ARM_MASK_0x18` |
| RAM 里保留失败作业与零点凭据 | `E_RAM_KEPT`：全场 `blackbox_clear=0`、`config_save=0` |
| B 侧 99 条 PASS 含 10 条负控 | `E_99_WITH_10_NEGATIVES` |

一处不由我背书：草案说"A 从远端取原件独立复核 **23 项**，FAIL=0"。那是 A 侧的运行，我这仓库里没有它的原始输出，
所以只登记为 A 自报；我能证的只有我这边 100/29/53/99+10 与草案所列一致。

## 2. 草案要按源码改的 5 处（不是措辞问题，会导致定稿判据错）

1. **§4.2 末段"加减速窗口例外只抑制 SG/负载判据"——实际连 IMU 冲击也一起被抑制。**
   `rel_engine_step` 在 `diag_relative.c:491-497` 命中 ACCEL/DECEL 就 `rel_engine_reset_counters` + `return`，
   而冲击判定在 `:595-610`（`A_RAMP_ALSO_SUPPRESSES_IMPACT`）。**源码注释 `:493` 自己写错了**，
   与代码不一致（这属产品注释缺陷，本轮不改码，只登记）。
   设计后果：P4 的 IMU 刺激必须落在 CRUISE 段内，且"无刺激正常段"要能证明当时确实在 CRUISE。
2. **§4.2 ADC 行的"新 ADC 数量要求"不是可调参数**：`diag_relative.c:643` 硬编码 `adc_strong_cnt >= 2u`
   （融合段同样 `:651` 用 2u），SG 才走 `p->min_new_sg`（`A_ADC_COUNT_HARD`）。
3. **§4.2"源失效／覆盖异常是另一条独立降险路径"不成立**：`REL_SENSOR_UNAVAILABLE/OUT_OF_WINDOW/COVERAGE_LOST`
   与 SG/ADC/IMPACT/FUSED 同样被 `rel_reason_is_severe`（`:65-69`）认作严重，共用同一出口
   `rel_request_protection` → `safety_post_event(EVT_MULTI_FAULT)`（`:725`），并且**共用同一枚一次性票据**
   （`:1061` `!latched && !rearm_required`）。后果：任一原因先触发就把这场票吃掉，
   下一场必须显式重建代次——三场刺激的额度要按这个事实排（`A_SEVERE_SET` / `A_TICKET_ONCE`）。
4. **§4.3"恢复计数需要独立新样本与两个源的正常证据"被写成 `fault_reset` 的门——不对。**
   `safety_state.c` 的 fault_reset 里没有任何样本计数判据（`A_FAULT_RESET_HAS_NO_SAMPLE_GATE`），
   它真实的门是：pad 确认 LOW（`:831`）、ENN 在禁能侧（`:842`）、源电平不再 active（`:852`）、
   安全关闭复核（`:867`，内部会真驱动一次电机停止）、复核后再确认 ENN（`:915`）、STEP 关断结果有效（`:947`）。
   "两源/三源正常证据"属于引擎侧票据解除条件 `diag_relative.c:384-385`，而且是 **`ok_src_seen == 3u`**
   （ADC+SG+IMU 三源），不是两个（`A_RECOVER_3SRC`）。
5. **§1/§4.2 的"学习在 MONITOR_ONLY 下进行"是对的，但要写成拒绝字面量**：`diag_relative.c:2016`
   是 `diagnosis_get_mode() != DIAG_MODE_MONITOR_ONLY → learn REFUSED: mode must be MONITOR_ONLY`，
   并且后台每拍复核（`:1725`）。定稿判据应按整行白名单钉这个字面量，不要写成"非 ACTIVE 即可"。

另补两条草案没写、但会影响可分辨性的源码事实：`motor_stop` 之后 `armed` 仍为 1
（`motor.c:883` 注释与 `:1692`），此时重复 `motor_start` 不被拒（`:1564` 只在未 armed 时拒），
但 `motor_arm` 会以 `not at rest (state=… armed=… tgt=…)` 拒（`:1153`）
⇒ 每场之间必须真正 disarm 才能重新 arm；学习候选签名吃 `tmc_cfg_state`（`:195`）
⇒ UNCONFIGURED 下取的零点在 apply 之后必然 `SIG_CHANGED`，草案"配置应用后重新取零点"是硬要求而非建议。

## 3. 现有未改执行器能否表达 R7F8（草案 §8 前置第 2 条的答复）

命令面本身够用：额度映射 `QUOTA_COMMAND_KEYS` 共 9 条前缀 / 8 个键（`X_QUOTA_MAP_9_PAIRS_8_KEYS`），
`tmc_config_apply CONFIRM`、`diag_profile zero/learn/commit`、`diag_mode relative CONFIRM_EXPERIMENTAL`、
`motor_dir/target/arm/start/stop/disarm`、`fault_reset` 都已作为普通步骤发过（R7F7/R4/R5 计划为证）；
`learn_cancel <ID>` 不在额度表内，可作为无额度只读步骤发送。**唯一的结构性缺口有两条，且都在判据侧：**

| 缺口 | 证据 | 定稿前必须做的离线适配 |
| --- | --- | --- |
| 有界轮询 `learn_status` 到 DONE 不被 `poll` 支持 | `m8r3_serial_chain.py:1939` 重试要求响应里 `[MOT] state=` 属 ACCEL/DECEL；`learn_status` 无该行 ⇒ 立刻具名 STOP（`X_POLL_GATE_SHAPE` / `X_DONE_POLL_NEEDS_OFFLINE_ADAPTATION`） | 给 poll 增加"非运动类"合同（按 `until_seconds`/`interval_seconds`/具名 retry code 重试），并配反例：窗口到期要 `POLL_WINDOW_EXPIRED`、字段矛盾要立刻停 |
| 后续 JOB_ID 无法从回包动态取值 | `cmd` 全程静态（`:2083`）；全执行器唯一的动态命令拼接是 `flash_dump %d %d`（`record_from` 只喂给日志窗口，`select_new_log_window`） | 增加"从命名捕获组到命令文本"的替换（并限定只能替换到白名单参数位），否则草案"不把下一枚写成 id=1"无法落实 |
| stop 与 disarm 的分开收口 | `CLEANUP_CMDS` 固定三条 `motor_stop→motor_disarm→motor_status`（`:1413`，`X_CLEANUP_TRIPLE`） | 让收口形状可申报（或明确接受"正常段各发一次 + 清理再兜一次"并披露重复撤能） |
| arm 自身的通电截止 | 运动预算只在 `motor_start` 起算（`:2177-2181` 之前置义务；步内截止 `:2120`，`X_STEP_BUDGET_BRANCH`） | 若草案 §7 要"arm 独立截止"，需在定稿里给 arm→start 间隔上限并加判据，而不是靠额度键暗示 |

其它两点按草案原样即可：分析仪不能与串口同一次 dispatch 编排（tools 后端只认 download/readback/reset/reboot，
`X_ACTION_DISPATCH_TAIL`），所以 §7 的 LA 分开计数是对的；"额度用尽仍发收口"确有实现
（`:1638 RESERVE_EXHAUSTED_SENT_ANYWAY`，`X_QUOTA_EXHAUSTED_SENT_ANYWAY`），§7 的收口预留要按这条真实语义写。
复用 R7F7 阶段发运动/取消命令会被该阶段自报的禁用项挡下（`X_R7F7_STAGE_FORBID_LIST`）——F8 需要新阶段/新计划，这是对的。

## 4. §3 待补参数分栏：哪些源码已给，哪些只能由操作者给

源码已经给死、不需要问人：`N_ZERO∈32..256`、`N∈8..32`、`LO/HI∈1..20000 且 LO≤HI`、`DIR∈{0,1}`
（`SRC_LEARN_WINDOW_BOUNDS`）、后台 deadline 12 s 为编译期常量、线程 10 ms 一拍、
shell 侧有界等 20 ms（⇒ **取消窗口宽度就是一拍量级**，草案"固定时序 + 有界查询"要按这个尺度写）、
IMU 门默认 `impact_peak_mg` 取 `cfg.vib_impact_mg=2500`（校验限 1200..8000），候选初值 `impact_new_min=2`、`dur_impact_ms=100`、`age_imu_ms=100`（`diag_relative.c:2189`，校验限在 `:180/:182/:184`）。

只能由操作者/A 定、我无法代填：**DIR 与目标 HZ、学习窗 LO/HI、加减速、每段硬截止与总运行预算、
N_CANCEL/N_DONE 的具体值、刺激方法与上限/时长/次数、允许行程与限位现状、硬件禁能手段的位置、
仪器采样率与保留长度、以及运动门禁的极性/IRQ 依据**（草案 §3 列的其余项）。
其中三项是**结构性阻塞**而非参数缺值：上面第 3 节的两条适配（DONE 轮询、JOB_ID 动态取值）没做完，
P2 就无法在不动错误门的前提下写进计划。

## 5. 四项补充合并到哪里（额度不变）

| 补充 | 落到本文的位置 |
| --- | --- |
| ① 额定电流 1.5 A，USER_REPORTED，不标 RMS/峰值，不授权升流 | §1 表新增一行 + §1.2 第 1 条 + §3"电气与驱动设置"栏去掉该缺项。来源等级另记：本仓曾**主动删除**未确认的"电机额定 1500 mA RMS"断言（`phase8-motor-chain-dev.md:32`，`REPO_RATED_CURRENT_WAS_UNCONFIRMED_BEFORE`），故 1.5 A 只能作为操作者报告值，不参与任何换算（`source_audit.md:107` 已注明 VREF/R110 是相电流设定口径，不得代进母线换算） |
| ② 沿用 24 V／TMC2209／R110=0.11 Ω／历史 VREF=1.37 V／IRUN20-IHOLD8-VSENSE1／M4 300 Hz-60 s；机械只核对相对历史空载台架有无变化 | §1 表新增"沿用为工况设计依据"行 + §1.2 第 2 条 + §3 机械/运行工况栏改写；仓库锚点分别为 `source_audit.md:107`、`plan_core_r7.json:194`（含"不抬流"原话）、`phase8-motor-chain-dev.md:182` |
| ③ 启动顺序更正为 dir→arm→确认→target→start | §1.2 第 3 条 + §4.3 约束条 + §5 P2 第 1 步整体替换；两条源码直证：`motor.c:1151-1153` arm 要求 `target_hz=0`、`motor_set_target_hz` 体内无 armed（`SRC_ARM_REQUIRES_TARGET_ZERO_VERBATIM` / `SRC_SET_TARGET_HAS_NO_ARMED_GATE`）——**确实不需要改产品代码** |
| ④ 承接：不重复下载/回读/复位、允许 RAM 遗留旧 FAILED、零点在最终配置后重取、JOB_ID 取回包 | §1.2 第 4 条（§1 末段与 §1.1 原有口径保留不重复展开） |
| 额度 | §7 表前新增一行显式声明"本补充不增加任何既有动作额度"，表体仍全 0/TBD（`M_QUOTA_STILL_ZERO`、`DRAFT_STILL_HAS_ZERO_QUOTA_TABLE`）；§9 加一条修订记录，旧行不动（`M_REVISION_ROW_ADDED`） |

## 6. 本轮的边界与下一步

我没有推进任何执行：草案仍是 DRAFT_NOT_AUTHORIZED，§7 额度仍全 0/TBD；产品代码、固件、旧轮证据与本会话
先前的 R7F7 归档字节 0 改动（判读原文仍是 99 条 FAIL=0；本轮只读它，不重写）。

入库前另加了两道**字节不变门**（各配"必须会命中"的负控），因为它们决定本轮登记的 sha 能不能叫"原字节"：
`.gitattributes` 里为本轮目录新增的 `…/phase8-m8-r7f8-closed-loop-draft/** -text` 规则必须已生效
（`GITATTR_TEXT_RULE_FOR_ROUND_DIR`；`core.autocrlf=true`，缺了它 markdown 的 CRLF 会在暂存时被改掉），
且同一匹配器要能在已入库的 R7F7 规则上命中、要把注释里的同形文本判成"不是规则"；
预检白名单要解析出完整 5 项并覆盖 `git status` 实际待提交的 6 条（`PREFLIGHT_ALLOWED_PARSED_EXACTLY` /
`PREFLIGHT_SCOPE_COVERS_ROUND_SET`），白名单外的合成路径必须被判越界。
这两道门都是先出过错再补上的：白名单原先写成 `R7F8 + "/"` 的拼接形态，解析出来只剩 `"/"`，
判据当场把它拒了；交接 JSON 里的 `live_copy_path` 写了"反斜杠夹 Users"的主目录形态，会被本轮自己的
泄漏扫描抓到，已改成不含主目录与用户名的位置描述（逐字节对账改由 `RBI_ORIG` 现场比对承担）。
本段故意不把这个模式原样抄出来——第 4 次差点把泄漏模式当字面量写进被扫的文件里，就是靠这条扫描自己抓住的。

要往前走，需要三件事，各自的责任人不同：
1. **A/操作者**：对第 2 节 5 处口径做决定（尤其"IMU 在加减速也被抑制"和"severe 共用一票"两条，
   会改变 P4 的场次排布与额度），并把第 4 节"只能由人给"的参数填进正文；
2. **我（离线，不需硬件授权）**：在第 3 节两条结构性缺口上做执行器适配方案 + 反例，
   以及 `learn_status` 轮询合同的候选写法——需要一句"去做执行器适配"的授权，我不会顺手动板；
3. **之后**才是把 §5 变成可 dispatch 的 plan/rows/needles 与 `--validate-plan` 离线整包复跑，再停在一次明确批准前。
