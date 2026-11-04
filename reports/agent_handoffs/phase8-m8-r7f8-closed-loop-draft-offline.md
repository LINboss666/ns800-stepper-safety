# M8-R7F8 草案离线核对轮交接（A 的保护闭环任务书草案 + 操作者四项补充；设备动作 0）

机器可判定版：`phase8-m8-r7f8-closed-loop-draft-offline.json`（本轮所有数值从那里对账）。
判读正文：`reports/reviews/phase8-m8-r7f8-closed-loop-draft/DRAFT_REVIEW.md`。
判据原文：`…/proof/r7f8_draft_checks.txt`（**116 条，FAIL=0**，含 3 条"必须会命中"的负控，以及收口前新增的
6 条字节不变门：`.gitattributes` 的 `-text` 规则已生效 / 同一匹配器在 R7F7 旧规则上命中 / 注释同形文本不算规则 /
预检白名单解析出完整 5 项 / 本轮待提交集无越界 / 白名单外合成路径必被判越界）。

## 1. 本轮做了什么、没做什么

操作者转交了 A 的任务书草案 `NS800_R7F8_Protection_Closed_Loop_Draft.md`（24780 B，`b572c1928f60…`），
它自标 **`DRAFT_NOT_AUTHORIZED`、硬件批准额度全部 0**；随后又发来四项补充与更正，并明确
"补充并更正已下发的 R7F8 任务书，请合并到现有文件继续，不另开纯登记轮"。

所以本轮：四项**就地合并进同一份任务书**（§9 保留修订记录，不另开平行任务书），
然后对草案做离线核对。**零设备动作**：未开串口、未 dispatch、未下载/回读/复位、未发任何写命令、
未启动分析仪、未建审批账（核准根目录 `*R7F8*` 匹配 0 件）。也不生成可 dispatch 计划——草案自己说
"不是可直接 dispatch 的命令表"。

## 2. 草案替我的 R7F7 证据加的话，逐条回原始字节复算：全部相符

清单 100/100、A 包原件 29/29、归档 53 件（不含清单自身）、`readback.bin` 与签收 BIN 整文件逐字节相等、
启动 3468 B 且 75 s 走满、`zero` raw 2055..2065、`STARTED id=1 n=8 win=450..550 dir=0`、
终态 `id=1 FAILED why=WINDOW_BROKEN owner=0 cred=1/0`、采样 0/0、learn 77.563→终态 81.594 之间 0 条实发命令、
末态"请求 LOW／pad=0／ENN 高且反馈一致（`[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK`）"、
armed=0/cur=0/tgt=0/IRQ CLOSED/极性未确认/protect_ready NO/UNCONFIGURED/arm mask 0x18、
RAM 保留旧作业与零点凭据（clear/config_save 全 0）、我这侧 99 条判据含 10 条负控 —— 全部对上。

一处不由我背书：草案说"A 独立复核 23 项 FAIL=0"。那是 A 侧运行，仓库里没有它的原始输出，
只登记为 A 自报；我能证的只有我这边与草案所列一致。

## 3. 草案要按源码改的 5 处（会改变定稿判据，不是措辞）

1. **§4.2"加减速例外只抑制 SG/负载判据"**：实际 `diag_relative.c:491-497` 命中 ACCEL/DECEL 就清计数并 `return`，
   冲击判定在 `:595-610` —— **IMU 也被抑制**；`:493` 的源码注释本身与代码不一致。
   ⇒ P4 的 IMU 刺激必须落在 CRUISE 段，且无刺激正常段要能证明当时确在 CRUISE。
2. **§4.2 ADC 行的"新 ADC 数量要求"不是可调参数**：`:643` 硬编码 `>= 2u`（融合段 `:651` 同样 2u），只有 SG 走 `min_new_sg`。
3. **§4.2 把源失效/覆盖异常写成"另一条独立降险路径"不成立**：7 个原因同属 severe（`:65-69`），
   同一出口 `safety_post_event(EVT_MULTI_FAULT)`（`:725`），并**共用同一枚一次性票据**（`:1061`）
   ⇒ 任一原因先触发就吃票，三场刺激每场都要显式重建代次，额度按这个事实排。
4. **§4.3 把恢复计数写成 `fault_reset` 的门**：`safety_state.c` 的 fault_reset 没有任何样本计数（真实门是 pad/ENN/源电平/停脉冲复核/再确认 ENN/STEP 结果），
   样本判据在引擎侧 `:384-385` 且要 **`ok_src_seen == 3u`（三源）**，不是两个。
5. **学习模式条件写法**：`!= DIAG_MODE_MONITOR_ONLY` 确实存在（`:2016`，每拍复核 `:1725`），
   定稿白名单要钉字面量 `learn REFUSED: mode must be MONITOR_ONLY`，不要写成"非 ACTIVE 即可"。

另外两条源码事实草案没写：`motor_stop` 后 `armed` 仍为 1（重复 `motor_start` 不被拒，但再 `motor_arm` 会以
`not at rest (… tgt=…)` 拒 ⇒ 场间必须真 disarm）；学习候选签名吃 `tmc_cfg_state`（`:195`）
⇒ UNCONFIGURED 下取的零点在 apply 后必然 `SIG_CHANGED`，"配置应用后重新取零点"是硬要求。

## 4. 草案 §8 前置第 2 条的答复：现有未改执行器能不能表达 R7F8

命令面够：`tmc_config_apply CONFIRM`、`diag_profile zero/learn/learn_cancel/commit/learn_status`、
`diag_mode relative CONFIRM_EXPERIMENTAL`、`motor_dir/target/arm/start/stop/disarm`、`fault_reset`
都能作为普通步骤发送并被判读（额度表 9 条前缀 / 8 个键，`learn_cancel` 与 `motor_stop/disarm` 不在表内）。

**两条结构性缺口，必须在定稿前离线适配（不许现场拆错误门）**：

- 有界轮询 `learn_status` 到 DONE 用不了现有 `poll`：`m8r3_serial_chain.py:1939` 要求重试仅在响应里
  `[MOT] state=` 属 ACCEL/DECEL 时才发生，`learn_status` 没有该行 ⇒ 第一次不满足就具名停止；
- 草案要求"JOB_ID 从真实回包取得、不把下一枚写成 id=1"，但执行器 `cmd` 全程静态（`:2083`），
  唯一的动态命令拼接是 `flash_dump %d %d`；`record_from` 的捕获只喂日志窗口，**不能替换命令文本**。

另有两处需要口径：`CLEANUP_CMDS` 固定 `motor_stop→motor_disarm→motor_status` 三条，所以"stop 与 disarm 分开收口"
要么接受重复撤能并披露，要么把收口形状做成可申报；运动预算只在 `motor_start` 起算，草案 §7 要的"arm 独立截止"
需定稿给 arm→start 间隔上限并加判据。分析仪不能与串口同一次 dispatch 编排（tools 后端只认
download/readback/reset/reboot），所以草案 §7 把 LA 分开计数是对的；
"额度用尽仍发收口"确有实现（`:1638 RESERVE_EXHAUSTED_SENT_ANYWAY`），§7 的收口预留要按这条真实语义写。
复用 R7F7 阶段发运动/取消命令会被该阶段自报禁用项挡下 ⇒ F8 必须新阶段新计划（符合草案）。

## 5. §3 待补参数：源码已给的与只能人给的

源码已给死：`N_ZERO∈32..256`、`N∈8..32`、`LO/HI∈1..20000 且 LO≤HI`、`DIR∈{0,1}`、后台 deadline 12 s（编译期常量）、
线程 10 ms 一拍、shell 侧有界等 20 ms（⇒ **取消窗口就是一拍量级**，草案"固定时序+有界查询"要按这个尺度写）、
IMU 门默认 `impact_peak_mg=cfg.vib_impact_mg=2500`（限 1200..8000）、`impact_new_min=2`、`dur_impact_ms=100`、`age_imu_ms=100`。

只能由操作者/A 定（我无法代填）：DIR 与目标 HZ、学习窗 LO/HI、加减速、每段硬截止与总运行预算、
N_CANCEL/N_DONE/N_ZERO 具体值、三通道刺激方法与上限/时长/次数、允许行程与限位、硬件禁能手段位置、
仪器采样率与保留长度、运动门禁的极性/IRQ 依据，以及"负载/传动/行程相较历史空载台架有无变化"的现场结论。

## 6. 四项补充落点（额度不变）

| 补充 | 位置 |
| --- | --- |
| ① 额定电流 1.5 A（USER_REPORTED，不标 RMS/峰值，不授权升流） | §1 表新行 + §1.2 第 1 条 + §3 电气栏去掉该缺项；来源等级另记：本仓曾主动删除未确认的"电机额定 1500 mA RMS"断言（`phase8-motor-chain-dev.md:32`），且 `source_audit.md:107` 注明 VREF/R110 是相电流设定口径、不得代进母线换算 |
| ② 沿用 24 V／TMC2209／R110=0.11 Ω／历史 VREF=1.37 V／IRUN20-IHOLD8-VSENSE1／M4 300 Hz-60 s；机械只核对相对历史台架有无变化 | §1 表新行 + §1.2 第 2 条 + §3 机械/运行工况栏；锚点 `source_audit.md:107`、`plan_core_r7.json:194`（原话"保持 IRUN20 现值, 不抬流"）、`phase8-motor-chain-dev.md:182` |
| ③ 启动顺序 静止确认 → `motor_dir` → `motor_arm` → 确认 → `motor_target` → `motor_start` | §1.2 第 3 条 + §4.3 约束条 + §5 P2 第 1 步整体替换；源码直证：`motor.c:1151-1153` 要求 `target_hz=0`，`motor_set_target_hz` 体内无 armed ⇒ **确实不需改产品代码** |
| ④ 承接：不重复下载/回读/复位、允许 RAM 遗留旧 FAILED、最终配置后重取零点、JOB_ID 取回包 | §1.2 第 4 条（§1 末段与 §1.1 原口径保留，不重复展开） |
| 额度 | §7 表前加声明"本补充不增加任何既有动作额度"，表体仍全 0/TBD；§9 新增修订行，旧行不动 |

入库的是 `A_DRAFT_ORIGINAL.md`（收到的字节，不动）与 `R7F8_DRAFT_MERGED.md`（合并版，31807 B，`270383c12b1c…`）；
操作者 Downloads 里那份是同一维护线的当前版本，本轮就地改的就是它。

## 7. 下一步（分责任人）

1. **A／操作者**：对第 3 节 5 处口径作决定（尤其"IMU 在加减速也被抑制"与"severe 共用一票"会重排 P4 场次与额度），
   并把第 5 节"只能人给"的参数填进正文；
2. **我（离线，不动板）**：做第 4 节两条结构性缺口的执行器适配方案与反例（DONE 轮询合同、受控的命令文本替换）。
   这需要一句"去做执行器适配"的授权 —— 那是改工具，不是动板，但我不会顺手动；
3. **之后**才把 §5 变成可 dispatch 的 plan/rows/needles，离线 `--validate-plan` + stub 整包复跑，再停在一次明确批准前。
