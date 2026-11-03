# M8-R7 有界现场验证候选包 · 运行记录（离线准备，设备动作 0）

上游：`NS800_M8_R6C_Independent_Review_OFFLINE_GO_20261103.md`（云端 A，2026-11-03）裁定
`overall_offline_release = OFFLINE_REVIEW_GO`、`next_work = PREPARE_BOUNDED_FIELD_VALIDATION_PACKAGE`，
并在 §1 要求下一步"优先验证启动、相关短打印和正常 disarm 收口"，在 §7 明确
**不要因换执行器重做无关工具工程，也不直接拿 R6C 模板改批准 ID 进入现场**。
本轮按用户在会话内的选择做**离线候选包准备并入库**，不含任何设备动作。

## 20.1 起点核对

```text
上一轮收口头(= A 本次实际 ls-remote/fetch 并审核的 R6C 交付头)  f15ba464628d282f5d7fca91de79b6f702c54dfa
开工时本地 HEAD                                                  与上同, 工作树干净(除本轮新目录)
分支 / 远端                                                      feature/m8-sg-adc-relative-protection
                                                                https://github.com/LINboss666/ns800-stepper-safety
产品 C / 参数 / 阈值 / 引脚 / 固件归档                            本轮 0 改动
执行器 EXEC / 自检 SELFTEST                                       本轮 0 改动(直接复用 R6C 签收那份)
Keil 构建                                                        0
```

未 reset/clean/rebase/amend/force；未新建分支；R5-C/R6/R6A/R6B/R6C 的计划、候选与在板镜像登记保持原件。
`identity_binding.reviewed_delivery_commit` 写入的就是 `f15ba46…`，包内所有"与上一轮相同"的声称都回指它。

## 20.2 范围是从执行器事实推出来的，不是许愿

A §1 的第三条优先项是"正常 disarm 收口"。它进不了本轮的包，理由是可核对的三条事实：

| 事实 | 出处 |
|---|---|
| `motor_arm` / `motor_start` 属运动义务命令 | `m8r3_serial_chain.py:1412` `MOTION_OBLIGATION_CMDS` |
| 包级 `mechanical_prerequisites.load_means` 仍是 `NOT_PROVIDED` | 冻结件与本轮 `plan_core_r7.json` 同一状态；操作者口径为不代填 |
| 该状态下执行器在**开端口之前**具名拒绝整条运动链（不起转、不开口、不扣额度） | 执行器 `STAGE_SKIPPED_BY_PREREQUISITE` / `PREREQUISITE_REFUSAL` 路径 |

所以把运动步骤写进包里再"现场再看"，等于制造一条一定会在中途烧掉批准额度的失败。本轮包**不含任何起转**，
并把这条不覆盖如实写进 `scope_this_package.does_not_cover`：

- 覆盖 ① 启动（一次显式复位 + 被动启动窗 + 只读状态查询）
- 覆盖 ② 相关短打印（`[SAFETY] LIMIT …` 与 `[TMC-CFG] CONFIGURED …` 三条短行，逐行宽度与字面量）
- 覆盖 ③ 新镜像身份闭环（一次下载 + 一次 292164 B 定长回读逐字节等于归档 BIN）
- 不覆盖：正常 disarm 之后的 Safety 收口（需 `motor_arm`）、`[MOT] withheld` 四条的现场出现（同因）、
  SG/ADC 真实加载与阈值生成/有效性、ACTIVE 覆盖、REL_IMPACT、保存恢复与故障恢复闭环、
  idle1024 带载最坏栈余量、现场精确线程交错、`0x00bd80b0` 写入者

## 20.3 派生方式与镜像重绑

派生源是**冻结的** R6C 交付件 `plan_core_r6c.json`（172306 B / `408c18ecaf50f62a…`）。派生器先校验源散列，
不符就拒绝派生；产物落新路径，不改 R6C 原件。三处镜像身份一起重绑并现场可复算：

| 件 | bytes | sha256（磁盘实算 = manifest `targets[].images` 登记） |
|---|---:|---|
| `rt-thread-motor-m1-candidate.bin`（下载后要逐字节证到的那件） | 292164 | `5ae4110d14fdbb40f49cc6f0deca474cf6d0b33b3ee9305e83681dbdd4a53825` |
| `rt-thread-motor-m1.axf`（下载载荷） | 1773648 | `581f1155166374cf96cda1281320dbcfd31968f1bf5a898a6dd0e2a804a52df1` |
| `rt-thread-motor-m1.map` | 2110730 | `4f58a3eea493d8c532ecceba3d16db1e59750066ec75cede8647197563d412c3` |

`tools.download.argv` 末项与 `tools.readback.image` 都指向同一批；定长回读的 `@IMAGE_BYTES` 由
`identity_binding.image.bytes` 展开，本轮即 `0x47544`（=292164）。执行器的
`PLAN_IMAGE_IDENTITY_CONSISTENCY` 就是为"回读还绑上一轮 BIN"那种错造的，本轮由 R7-1 独立复算过。
`5ae / d70 / dab` 三件归档 BIN 一件都不覆盖。

## 20.4 本轮顺手改掉的四处登记缺陷

这四条不是"顺手优化"，是本轮包自己会误导读者的地方，都带独立对账判据（R7-7 四项）：

1. **`identity_binding.board_image_before` 过期**。R6C 模板这一段仍写 `f5c2fd89…/290916 B` 并称"板现运行该镜像"，
   那是 diag 栈轮 `field-20261101T084256Z/flash_readback.bin` 的结论。之后 idle-stack-r4 轮
   `field-20261101T142722Z/readback.bin`（实测 290916 B、sha256 `dab356c6…`、逐字节等于归档 BIN）
   把在板件换成了 `dab356c6…`。本包要下载覆盖的正是这一件，"下载前板上是谁"写错会让现场判读错位。
   本轮在自己的登记里改成 `dab356c6…`，并把被更正的旧口径放进 `supersedes_in_r6c_template` 以便对账；
   R6C 原件不回改。
2. **`identity_binding.executor.path` 是本机绝对路径** `D:/ns800/…`。执行器只用 `__file__` 记自己的路径，
   这个字段纯登记，但绝对路径换一台机器（含审核方）按字符串找不到文件。本包改成仓内相对路径，
   `bytes/sha256` 一字未动，原字符串保留在 `path_r6c_template_verbatim`。
3. **赋值行尾逗号把对象静默写成数组**。`plan["motion_budget"] = {…},` 让它在 JSON 里变成单元素数组，
   而继承链上 R5-C/R6C 的这一键都是对象。派生器现在拒绝任何算成 `tuple` 的顶层键，
   R7-7 再从包外证明 `motion_budget` 确实是对象。
4. **顶层身份字段还写着上一轮的包**。`package` / `task_id` / `status` 在 R6A/R6B/R6C 里都仍是
   "M8 idle 栈 1024 核心现场包 R5-C 续测" 与 `task_id=phase8-m8-idle-stack-r4`、
   `status=BOUND_NOT_EXECUTED`。本包不是那一轮的续测（阶段链、镜像、额度都不同），
   名字不对会让远端回读与现场判读指错对象，故改成 `phase8-m8-r7-field-package-5ae` /
   `R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED`（与 `package_status` 一致）。
   执行器不读这三个字段，所以改动不影响任何行为；旧包不回改。

另外新增 `stage_numbering_and_legacy_fields`：本包只有 `S0–S4`，而整块派生继承的历史字段仍按旧全包的
`S0–S11` 编号说话，两边**撞号**（旧 S4 = 巡圈零点+学习要起转；本包 S4 = 一次 `tmc_config_apply` 不起转）。
派生器逐块扫描把这些块登记成"历史口径"，不再让读者自己猜。本包五个阶段都没声明 `prerequisite_gates`，
所以 `load_means` 门在本包不会被触发——这不等于门被拆掉，含起转的包照旧在开端口前被拒。

## 20.5 阶段链（全部非运动）

| 阶段 | kind | 内容 | 额度 |
|---|---|---|---|
| S0 | offline | 真实文件与来源绑定（镜像/AXF/map/manifest/needles/提交），磁盘漂移当场具名拒 | — |
| S1 | download | 一次 `pyocd flash -e sector` 载入 5ae 的 AXF | download 1 |
| S2 | readback | 一次 `savemem 0x08000000 @IMAGE_BYTES` 定长回读并逐字节比对归档 BIN | readback 1 |
| S3 | serial_session | 开端口后**先复位**，被动启动窗 75 s，再取 `system_status / safety_status / safety_irq_status / tmc_config_show / motor_status` | port_open 1、explicit_reset 1 |
| S4 | serial_session | 一次 `tmc_config_apply CONFIRM` 取证 `[TMC-CFG] CONFIGURED …` 三条短行，随后三条只读复核 | port_open 1、tmc_config_apply 1 |

启动窗算术（R7-3）：`drain 75 + 首条查询 10 + 3 s 余量 = 88 s >= observation_bounds.boot_window_seconds_min 75`，
且不申报 `boot_quiet_seconds`（静默不等于观察）；`post_reset_boot=["msh >"]` 与 `boot_bytes=[]` 沿用 r3/R5-C 同形状。
两个会话阶段都挂了 `forbidden_commands`（`motor_arm`、`motor_start`、`diag_profile learn/commit`、`config_save`、
`blackbox_clear`、`safety_irq_enable`、`safety_polarity_confirm`、`motor_dir`、`motor_target`），
R7-2 逐步骤核对没有一条撞禁令。

## 20.6 短打印判据回到产品源码

判据不是抄旧抓包文本，而是由派生器从 `44e493c` 那一版产品源码的**字面量**再算一遍（相邻 C 字面量按
拼接处理，在截到第一个格式符之前的保守宽度：`%s`=18、`%d`=-1111111111、`%u`=1111111111、`%08X`=8）：

| needle | 源码行 | 保守字节上界 |
|---|---|---:|
| `[SAFETY] LIMIT inputs config =` | `safety_thread.c:374` | 80 |
| `[SAFETY] LIMIT OFF => PF.14/PF.15 不解析/不配置/不读/不注册, 也不参与就绪判定` | `safety_thread.c:376,377` | 97 |
| `[TMC-CFG] CONFIGURED (各次写入均经 IFCNT 确认)` | `tmc2209.c:2842` | 55 |
| `[TMC-CFG] GCONF/CHOPCONF 读回一致` | `tmc2209.c:2843` | 38 |
| `[TMC-CFG] 电流值仍为理论计算, 未实测` | `tmc2209.c:2844` | 49 |

`SAFETY_LIMIT_INPUTS_ENABLED` 在 `project_board.h:50-51` 是 `RT_FALSE`，所以 S3 期望 OFF 分支那一行；
R6 拆出来的两条短行**都要出现**，少一条按该行没打全判失败，不随手放宽判据。`motor.c:1883/1885/1889/1892`
那四条 withheld 短行只在禁能侧证据不足分支出现，而进入该分支需要起转，故本包如实标为不在范围。
R7-5 把每条 needle 用**同一个函数、同一行号**再算一遍并比字面量与上界，全部 <=120 B（最宽 99 B）。

## 20.7 干跑夹具与它的边界

`field/rows.json`（49352 B / `b66e33fced562863…`）是**离线干跑夹具，不是现场实抓数据的副本**：
45 个命令块里 43 块逐行照抄 r3 现场实抓（`kind=board_capture`），2 块是 5ae 拆短后的
**由源码拼出的预测行**（`kind=predicted_from_source`，带 `文件:行号`）。整表照抄而不是只挑本包用得上的几条，
因为执行器在会话收尾会按 `CLEANUP_CMDS` 逐条要 `motor_stop/motor_disarm/motor_status` 的行，
缺一条就 `ROWS_NOT_DECLARED` 具名停——那会造出一个"夹具缺行导致的假信号"。

R7-4 判每一块都有具名来源，并在 `_about.not_evidence_of` 写明：这份夹具只证明阶段/额度/argv/判据在形状上自洽，
**不证明**行宽或产品行为；行宽的证据是现场抓到的原始字节加产品侧保守口径，二者不能被夹具代替。

## 20.8 额度是提案，不是批准

```text
port_open 2 | download 1 | readback 1 | explicit_reset 1 | tmc_config_apply 1
其余(motor_run / motor_run_seconds_total / motion_cap_seconds_per_run / learn / zero / commit /
     config_save / blackbox_clear / approved_reboot / irq_polarity_prep / cleanup_* / retry /
     physical_collision / dmm_readings) 全为 0
```

`numbers_are_proposals_not_authorization = true`、`approval.status = REQUIRES_NEW_APPROVAL`、
`approval.hardware_actions_authorized_by_this_package = 0`。旧批准 `4cb9dac0…` 的 download/readback/
explicit_reset 各 1/1 已用尽且**不结转**，换计划文件或换输出目录都不重新发额度。
`tmc_config_apply` 单列：它不起转、不通电到绕组，但会写驱动芯片寄存器，因此仍要操作者单独批。

## 20.9 离线门与实跑结果

| 检查 | 本机实跑结果 |
|---|---|
| `EXEC --validate-plan --plan field/plan_core_r7.json` | **47 项、fail=1、msh_commands=74，退出码 1**；唯一 FAIL 是 `PLAN_MOTION_CAP_BUDGET`（下面那条已登记门） |
| `host/r7_readiness.py`（17 项，含整包 stub 干跑） | **17/17 OK，退出码 0** |
| `phase8-m8-idle-stack-r4/host/idle_learn_check.py`（本轮把它的 P8 升到六层） | **CHECKS=120 problems=0，元负控 6/6 与 R5 系列 9/9 全部 fired，退出码 0** |
| 派生件写入语义五步（幂等/改坏必拒/`--force`/`--check`/身份复原） | `host/derive_matrix.txt`，每步真实退出码，末步逐字节回到 `e87dd11ee71bb905…` |
| `derive_plan_r7.py --check` | `IDEMPOTENT bytes=102115 sha=e87dd11ee71bb905 源仍冻结=True` |

三条门的原文都由 `host/run_transcripts_r7.py` 一次生成（UTF-8/LF + 各自真实退出码），
不筛不剪；`idle_learn_check` 的输出里带临时 scratch 目录路径与 `ACTION` 提示行，那是该回归门自己的
正常输出，保留原样。`readiness_transcript.txt` 每次重跑只有 `DRYRUN_DIR` 那一行会换（临时目录名），
其余字节稳定。

`PLAN_MOTION_CAP_BUDGET` 对**无起转**的包是恒红门：`caps` 只收 `motion_cap_seconds` 为真值的阶段
（`m8r3_serial_chain.py:2678-2679`），而通过条件要求 `caps` 非空且 `sum(caps) <= quota.motor_run_seconds_total`
（`:2683`）。本包 `motor_run=0`、没有任何 `motor_start` 步骤，要变绿只能谎报一个非零上限并要求总额不小于它——
那是给一个不打转的包申报运动预算。本轮**不为了让门变绿改判据，也不谎报预算**，把这条登记进
`motion_budget.registered_checker_gap`，并用 R7-2 三条独立判据（无起转步骤 / 运动与学习额度全 0 /
禁令不撞）作为本包真正该满足的合同。

## 20.10 落盘口径的一处自伤与修正

本轮最初直接把 `python … > 文件` 的重定向输出归档，Windows 控制台默认 cp936，落盘的是 **GBK 字节**：
本机看着正常，按 UTF-8 读就是乱码（R6C 那轮的文本证据都是 UTF-8/LF，口径也不一致）。
修法是新增两个跑证脚本 `host/run_transcripts_r7.py`、`host/run_derive_matrix_r7.py`：
子进程强制 `PYTHONIOENCODING=utf-8`、父进程严格解码（解不出来就当场失败，不许悄悄退回 gbk 兜底）、
每步单独记**它自己的**退出码、整体按 LF 写盘。当前四份文本证据
（`validate_plan_r7.txt`、`readiness_transcript.txt`、`idle_learn_check_r7.txt`、`derive_matrix.txt`）
都由这两个脚本重新生成，乱码原件没有被当作证据留在仓库里。
跑证脚本里对就绪检查条数的措辞也跟着改成了当前条数（17 项），不留旧数字。
## 20.11 本轮没有动的东西

```text
EXEC  m8r3_serial_chain.py        219491 B  33d3f0af8a4c9c33…   (与 R6C 登记同一件)
SELF  m8r3_field_selftest.py      192823 B  4abcd162a7d0c20d…   (本轮未重跑全量 127 项)
产品 C / project_config / 阈值 / 引脚 / 恢复资格   0 改动
固件归档 5ae / d70 / dab 三件 BIN  0 改动、0 覆盖
keil_builds 0 | device_actions 0 | serial_ports_opened 0 | dap_connections 0
```

## 20.13 推送与本轮范围的独立远端回读

```text
交付提交(evidence)   ea22ba5  evidence(m8-r7): 有界现场验证候选包离线定稿(5ae 重绑/无运动链/17 项就绪门); 设备动作 0
push                 f15ba46..ea22ba5  常规 fast-forward, 无 rebase/merge/amend/force
远端回读对象          ea22ba5(= A 可以直接 ls-remote/fetch 的那一枚)
                     R7_REMOTE_READBACK_SUMMARY|tip=ea22ba52d675|checks=48|fails=0|not_checked=0  退出码 0
收口提交              只归档两份回读原文 + 它们在清单里的行 + 修正后的回读脚本 + 交接 JSON 的 remote_readback 字段
                     收口头不再另起一轮"回读自己的新回读"
```

回读覆盖：本轮清单 14 条逐 blob 复算 + R6C/R3/R4/R6B/R6A 与两处 firmware 清单 + 候选包内部申报的
执行器/镜像三件/下载载荷/回读对象/派生源/下载前在板件身份 + `build_manifest` 的 target 行 +
交接 JSON 里 21 条 `artifacts_and_identities` + 10 件要求保持原字节的旧件（含两份历史在板回读原件）+
基线头 `f15ba46` 上 EXEC/SELFTEST/R6C 派生源的"本轮未改" + "未新增构建件"两条。

第一次跑这个回读脚本时报了 2 条 `frozen rt-thread-motor-m1-candidate.bin` 失败，原因是**回读脚本自己**
把 R6 与 R4 的归档 BIN 路径写成了 `reports/reviews/...` 而不是 `reports/firmware/...`——取不到对象，
不是仓库里的字节变了。那次失败原文完整保留在同目录
`remote_readback_r7_first_run_wrong_paths.txt`（6640 B，`exit_code=1`，未删未改），
修正路径后的第二次运行才是上面归档的 48/0。两条 BIN 的远端字节在本轮两次运行里都由
`FROZEN` 段的散列约束着（`d70a9180…`、`dab356c6…`），与 A 上一轮 §6 登记的完全一致。

## 20.14 停点

本包是**候选**：现场执行需要操作者对上述 5 个非零额度逐条明确批准（尤其是会写驱动芯片的
`tmc_config_apply`），并回报现场物理状态（供电、接线、是否允许开 COM5、机械是否静止）。
本记录与包内任何字段都不含硬件批准，也不代替操作者回答现场安全门。

```text
R7_package            = R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED
device_actions        = 0
hardware_actions_authorized_by_this_package = 0
motion                = NOT_INCLUDED(load_means=NOT_PROVIDED)
disarm_closeout       = OUT_OF_SCOPE(需 motor_arm)
validate_plan         = 47 checks / fail=1(PLAN_MOTION_CAP_BUDGET, 已登记恒红门)
readiness             = 17/17 OK
remote_readback       = 48 checks / fails=0 / not_checked=0 (对象=交付头 ea22ba5)
product_field_fix     = NOT_VERIFIED
safe_for_enable       = false
next_required_human   = 操作者逐项批准额度 + 回报板态
```

## 20.15 A 的复审校正版与本包的关系

操作者把 A 的独立候选包（`NS800_M8_R7_A_Reviewed_Boot_Print_Package_20261103.zip`，7 件）转来，
A 的裁定是 `original_R7_plan = DO_NOT_EXECUTE_AS_SUBMITTED`、`A_reviewed_candidate =
READY_FOR_ONE_OPERATOR_PACKAGE_APPROVAL`。本轮**原字节接收**，不重跑派生器、不改写它：

```text
plan_core_r7_reviewed.json  108021 B  fea903b4f9530cab6d6bec16d2a4beb1b85901e441e18c9872831f121c645c16
                                     -> 复制到 field/, 与原 plan_core_r7.json 并存(原件未动)
A 的审查记录 MD              8286 B  30ae7c0a4d98aae1…  -> 逐字入库 a_review_NS800_M8_R7_Boot_Print_Package_20261103.md
reviewed_identities.json     1328 B  76c6f4dca1bb7a46…   reviewed_readiness.txt  2640 B  43a2e5c04785c59f…
reviewed_results.json        2351 B  a8556de7ed71c969…   reviewed_validate_plan.txt 3375 B  89559a32fbda5708…
SHA256SUMS.txt(ZIP 内)        574 B  9b89fbaa5412b047…   —— 七件全部与 A 上表逐字相符(本机现算)
```

A 指出的两条实质缺陷都在**校正版**里解决：① 原 R7 的 `system_status` 判据会把"动作被拒"的披露行
当成业务失败、且 `polarity=\w+` 不接受真实的 `not-confirmed`，正常启动回包会被误拒；
② 新镜像的构建来源混入了旧 dab 登记。另有一条口径纠正：**"缺加载手段必然阻断普通 disarm"是我写错的**
—— EXEC 是按阶段的 `prerequisite_gates` 查具体前置，不会把 `load_means` 自动挂到每次
`motor_arm/motor_start`；本包不含运动仍然是范围选择，不是被那道门逼出来的。这条我认。

## 20.16 R7-F1 现场整包执行（一次批准，跑到 S3 STOP）

操作者批准原文见 `field/field-20261103T132348Z/APPROVAL_TOKEN.verbatim.txt`（79 字，未改写）。
新批准身份 `M8R7F1-reviewed-bootprint-20261103` 由 `--approval-init` 离线开立，账本
`D:\ns800_field_approvals\approval_M8R7F1-reviewed-bootprint-20261103.json` 绑定的三件 SHA 就是
A 的固定表（计划 `fea903b4…` / 执行器 `33d3f0af…` / 镜像 `5ae4110d…`）。真实整包一条命令走完：
`--dispatch --transport real --backend real --profile response_driven`（21:23:58 +0800 起，工具退出码 1）。

```text
S0 OK  绑定核对通过(APPROVED_PACKAGE_VERIFIED, steps=6, steps_sha256=e420992f5ce9…)
S1 OK  一次 sector 下载 5ae 的 AXF           action_download rc=0
S2 OK  一次定长 292164 B 回读                 readback.bin == 归档 BIN 逐字节相等=True   ← 新镜像已在板
S3 STOP FIELD_VALUE_MISMATCH                  wrong: state=FAULT_LATCHED(要 READY) fault=8(要 0)
S4 未执行                                     tmc_config_apply 0/1
实耗 download 1/1 readback 1/1 explicit_reset 1/1 port_open 1/2  其余全 0; commit_failures=[] pending=0
不发任何运动义务命令(motor_* 额度 0); cleanup_errors=[]; disarm=NOT_ARMED; PORT_CLOSED 正常收口
```

判读全文在 `field/field-20261103T132348Z/OFFLINE_JUDGEMENT.txt`，其中三条要点：

1. **成立**：5ae 这枚镜像第一次被逐字节证明在板（292164 B，散列与归档件相同）。
   **不成立**：`product_field_fix`、"启动到 READY"、两组目标短打印（`safety_irq_status` 与 S4 的
   `[TMC-CFG] CONFIGURED` 三条都未到达）—— 全是 NOT_VERIFIED，不换算成通过。
2. **失败是产品事实，判据没冤枉它**：启动被动流里 `[SS] [REQ FAIL] TMC2209 IOIN read rc=-23` →
   `self-test done: required=FAIL` → `[BOOT] 11 startup-selftest FAIL -> state=FAULT_LATCHED`，
   `fault=8` 就是产品自己的 `FAULT_SELF_TEST`（`applications/safety_state.h:48`）。
   A 校正的两处当场可见：`[Motor] arm gate fail mask=0x1A (arm REFUSED)` 走白名单放行、
   `polarity=not-confirmed` 被接受，停因落在真值不符上 —— 这条只有现场字节能说明。
   rc=-23 的根因**不猜**（丢帧 / 总线未就绪 / 时序都未排除），也不据症状改产品码。
3. **行宽与并发**：单次响应侧干净（`system_status` 992 B、21 行、最长 120 B、超 120 B 为 0 行、
   无裸 NUL、UTF-8 完整）；启动被动流 3810 B 里 75 个分段有 **2 个 252 B / 305 B** 且 **3 个裸 NUL**
   （偏移 1318/2268/2395）、整体 UTF-8 解码异常（首错 1317），形态是一条分段里咬着 2～3 次不同打印。
   顺带登记执行器覆盖面：命令响应有"危险文本→裸 NUL→非法 UTF-8"三级硬门，被动启动流只调
   `check_danger()`，所以这些 NUL 没成为具名失败 —— 属判据设计问题，本轮不自行收紧也不放宽。

停在这里：不重跑整包、不加任何一次动作、不改判据/计划/执行器、不用剩下那次 `port_open` 与
`tmc_config_apply` 去"顺手再看"。板最后可读证据是停在安全态（DRV_ENABLE pad LOW 已证、request=LOW、
pad=0、ENN 在禁能侧、armed=0、arm gate 拒绝、UI SAFE-OFF）同时 Safety `FAULT_LATCHED fault=8`；
断电/上电或 `fault_reset` 人工恢复复核要操作者另发批准。
