# phase8-m8-idle-stack-r4 —— idle 栈容量修复、learn 判据修正、motor 候选重建与核心现场包绑定

基线 `48204fe`。本轮设备动作 = **0**：不起 pyocd、不开 COM5、不接 Keil 下载、不动板子。
板子仍停在上一次续测后的状态（4096 diag 镜像在跑，控制台可交互）。

## 1. 任务书四条与落点

| 条 | 落点 | 证据 |
| --- | --- | --- |
| 1 idle 栈 | `rtconfig.h:79` `IDLE_THREAD_STACK_SIZE 256 → 1024`（带一行缘由注释） | §2、`stack_report.txt`、`host/idle_learn_console.txt` A1–A11 |
| 2 learn 计划 | 两份 R3 计划的 learn 步骤 `must{adc_n:"8",sg_n:"8"}` → `must_min{adc_n:8,sg_n:8}` + `criterion_note` | §3、B/C 组 |
| 3 只构建一次 | motor 候选 Rebuild All，新三件套另存新目录 | §4、`build_manifest.json` |
| 4 核心现场包 | `field/plan_core_r4.json`（复用 R3 执行器） | §5、D/E 组 |

## 2. idle 栈：容量缺陷成立，但任务书的 264 B 不是我这路读到的数

- 旧 map（已接受镜像那份，`reports/firmware/phase8-m8-diag-stack-r1/rt-thread-motor-m1.map:17160`）：
  `idle_thread_stack 0x20009de8 Data 256`。新 map 同一符号是 `Data 1024`（`0x400`）。
- 同一轮构建的 armlink Static Call Graph 给 `idle_thread_entry` 的 `[Stack] Max Depth = 668`，链是
  `idle_thread_entry ⇒ rt_defunct_execute ⇒ rt_object_delete ⇒ rt_free ⇒ _heap_lock ⇒ rt_mutex_take ⇒
  _rt_mutex_take ⇒ rt_thread_suspend_to_list ⇒ rt_sched_thread_timer_stop ⇒ rt_timer_stop ⇒ …`
  （逐帧抄本在 `stack_report.txt`，含各函数 own frame：24/24/24/16/16…）。
- **两数并列，不互相替换**：任务书的"固定帧合计 264 B"是我核过的一条**可达路径的固定帧下界**；668 B 是 armlink
  自己给的**更深静态合计**。两者口径不同、同时成立，不存在谁推翻谁。本轮按更保守的 668 取配置值 1024，
  但**不**把 `1024 − 668 = 356 B` 称作"已验证安全余量"——那只是一个算术差，没有任何实测或静态证据说明这 356 B
  够；`668`、`1024` 也都不称"已验证上界"，1024 是否够用仍是 NOT_VERIFIED（需板上水位或栈填充实测）。
  勘误：本节早前的写法是"1024 覆盖 668 并留 356 B"，该措辞把算术差说成了余量，已按复审口径改掉。
- 板上实测的另一路水位并列登记：上一轮续测的 `ps` 里 `tidle0 … 0x00000100 64%` ⇒ 163 B。这只说明那次启动
  没走到 defunct 深链，不说明走不到。
- ZI-data `84824 → 85592` = **+768 B**，正好等于 1024−256；Code/RO/RW 一字未变 ⇒ 改的是 `.bss` 里的
  idle 栈，不是别处。
- 只改了这一处：`git show HEAD:rtconfig.h` 与工作树逐行比，差异行数 = 1 且就是 IDLE 那行（A6）；
  `diagnosis.c` 的 sha256 仍是上一轮登记的 `e1696ec9…`（A7）⇒ diag 4096 未被回退也不是本轮改的。

## 3. learn 判据：改成"各 ≥8"，三类拒绝留着

执行器 `m8r3_serial_chain.assert_step` 的 `expect_values` 原来只做字符串等值比较，所以新增
`must_min`：数值下限，非整数/缺组具名拒绝，命中下限失败返回 `FIELD_VALUE_BELOW_MIN`。判据矩阵（真执行器 +
真计划步骤）：

- 接受：`8/8`、`9/8`、`8/12`、`32/32`（不设上限）；
- 仍拒：`7/9`、`8/7`、`0/8` → `FIELD_VALUE_BELOW_MIN`；重复成功行 → `FIELD_NOT_EXACTLY_ONE`；
  只有提示符/空响应（学习超时路径）→ `EXPECTATION_NOT_MET`；`[DIAG] learn FAILED:` 行仍先按**业务失败**停。

元负控（源码字面替换造突变体，写到仓外再导入）证明这些不是空判据：去掉下限比较 → `7/9` 被接受；
去掉行唯一性 → 重复行被接受；把判据换回等值 `must{8,8}` → `9/8` 又被拒（⇒ "恰好 8"确实被删）；
去掉内核内置命令扫描 → `ps` 重新被判成"发明命令"。

## 4. 构建与新镜像身份（一次，未覆盖已接受件）

- 命令逐字沿用上一轮：`C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 -o <log>`；
  退出码 0，日志 `0 Error(s), 0 Warning(s)`，`Build Time Elapsed: 00:00:10`。
- 新三件套（`reports/firmware/phase8-m8-idle-stack-r4/`）：BIN 290916 B `dab356c6…fbd25439`、
  AXF 1771288 B `0babe9f1…736aba8c`、map 2108456 B `b6163224…16c275b7`。
- 已接受的 `f5c2fd89…` 仍在原位且 sha 未变（A8/A9）——新镜像另存新目录，没有先删再建。
- 边界核对（新 map）：`RW_RT_HEAD Exec 0x20012000 Size 0x8000 Max 0x8000`（堆 32768 B 未动）、
  `RW_DTCM Size 0xd318 < Max 0x1fc00`、`ER_IROM1 Size 0x46e24 < Max 0x80000`。
- 新 BIN 与已接受 BIN 同为 290916 B 但差 **1831 字节**；我**没有**逐字节归因这 1831 字节（登记为未证），
  只证它不是同一枚镜像。

## 5. 核心现场包（绑定，不执行）

`field/plan_core_r4.json` 复用 R3 执行器与阶段骨架，16 个阶段（10 个有步骤）：

S0 离线绑定 → S1 唯一一次下载 → S2 唯一一次定长回读 → S3 会话1（复位+启动采集，**新增栈容量断言**：
`ps` 里 `diag` 行栈必须 `0x00001000`、`tidle0` 行栈必须 `0x00000400`，各恰好一条）→ 静止零快照 →
S4 唯一一次学习起转（learn 走 ≥8 新判据）→ S5 静止 tier/commit/show/config_save → S6 断电重启（人工）→
S7 重启后按 S5 记录比较恢复 → S8 SG/ADC 受控负载 → S8b IMU 非破坏激励 → S9/S9b 新故障取证（真实新增
session 的后向加法 + flash_dump 帧）→ S10/S10b 锁存态 arm 拒绝 → S11/S11b 人工恢复。

首因合同（两路激励各一份，写进计划而不是口头）：

- 必须从 `flash_dump`/blackbox 帧读出**先越限的那一路**与命中规则、首个 seq 与样本值；
- 明写 `safety_trip` 只能作为被验证的结果，**不得**作为使停机/锁存成立的原因；窗口内无真实越限帧 ⇒
  `NO_TRIGGER`，不算通过，也不许补一次软件 trip 变成通过；
- S8/S8b 各在 arm/start 之前挂了一道人工门（`gate_load_means` / `gate_impact_excitation`），real 下必须
  操作者现场新输入。

机械/夹具缺口如实标注并阻断（`not_provided_summary` 5 条）：SG/ADC 受控负载手段 `NOT_PROVIDED`（⇒ 该路
预期不起转）、示波器/电流钳对 STEP 无脉冲的独立证明 `NOT_PROVIDED`（BUG-008）、IRQ 极性 `hardware-pending`、
IMU 装配刚度 `NOT_PROVIDED`、阈值 `NOT_ESTABLISHED`。

配额（申报值，非授权值）：download 1、readback 1、explicit_reset 2、approved_reboot 1、port_open 10、
motor_run 3 / 总 25 s / 单次 5 s 硬截止、config_save 1、diag_profile learn/commit/zero 各 1、
**retry 0**、blackbox_clear 0。注意 `motor_run=3` 是上限申报：S8 的人工门没过就停在门上，实际预期 2 次。

零设备端到端复核（把包塞进仓外临时目录跑 `--dispatch --transport stub --backend sim`）：
`DISPATCH_SUMMARY|status=OK|stages_executed=10|stages_not_run=0`、`actions_attempted=[]`（原文存
`field/dispatch_stub_healthy.txt`）；把 S8/S8b 的 `manual_answers` 清空后同一命令必须
`status=STOP / MANUAL_GATE_MISSING_STUB_ANSWER` 且 `motor_run` 只用掉 1 次（`field/dispatch_stub_nogate.txt`）
—— 这道门是真的在拦运动，不是文字。

`--validate-plan` 对本包：`checks=207 fail=0 msh_commands=74`。`msh_commands` 从 70 涨到 74 是因为执行器原来
只扫 `applications/*.c`，把内核 `components/finsh/msh.c` 里导出的内置命令（help/ps/free/bind）判成"不存在的
命令"；本轮补上这一路并按 D4/D5 元负控证明它仍是"发明命令"的门。

## 6. 本轮汇总数字

`host/idle_learn_console.txt`：**CHECKS=45 problems=0，META_NEGATIVES=6 fired=6**。
A 组 11 项 = idle 证据链与镜像身份；B 组 12 项 = learn 判据矩阵；C 组 4 项 = 启动确认的栈断言
（新镜像期望过、旧镜像板上实录必须被拒）；D 组 6 项 = 元负控；E 组 12 项 = 包结构与干跑。

## 7. 仍未证 / 下一步

1. **1024 在板上是否够用未证**：本轮没上板，`tidle0` 的水位仍是旧镜像的 163 B；defunct 深链是否会在现场被走到
   也没有观测。包里的 S3 栈断言就是为拿这个证据而绑的。
2. idle 溢出与上一轮 Timer 链表损坏**没有建立因果**：`bblog.thread_timer` 的链表是 Timer 类，坏指针
   `0x00bd80b0`；idle 栈越界会写到哪里本轮没算，不许把两件事接起来。
3. 新镜像 `dab356c6…` 与 `f5c2fd89…` 的 1831 字节差异未逐字节归因。
4. 学习/提交/恢复/两路激励的现场结论全部未取（本包 NOT_EXECUTED）。
5. 下一步：等复审对本包的一次批准（绑 `plan_core_r4.json` 与执行器、rtconfig 的 blob 散列）。批准后按
   S0→S11b 一次整包执行；S8 若受控负载手段仍未到位，就停在 `gate_load_means` 上并如实登记，**不**用软件 trip 顶替。

## 8. 本轮自己抓到的两处（都记进清单与预检，不留到事后）

1. `host/staged_preflight_batch.py` 第一版把 `git cat-file --batch` 的响应头读成 `<sha> <size>`，实际是
   `<sha> <type> <size>` ⇒ 每条都解析失败成 `missing_blob`，而且 `--selfcheck` 因错误原因"命中"（它报的是
   missing 而不是 sha 不符）。修好后同一条 selfcheck 报的是 `sha_mismatch want=0000… got=757b…`，
   才是"比较器会命中"的正确证据。这条正是上一轮教训的复发版：**检查器先要证明自己会以正确的原因失败**。
2. 第一次提交（`d4c3108`）里**漏了 `rtconfig.h`**：`git status` 显示它 ` M` 未暂存——BSP 目录里的这个头文件被
   `.gitignore` 的构建目录规则连带忽略，`git add -A <round目录>` 覆盖不到它，必须 `git add -f` 具体路径。
   后果不是构建错（构建读的是工作树，新 map 的 `idle_thread_stack=1024` 与 ZI +768 B 都证明改动生效），
   而是"版本里的源码 = 256、构建输入 = 1024"这种**源码与镜像不配对**。补法：`git add -f` 该文件入本次提交，
   并给 `build_manifest.json` 加 `build_inputs`（rtconfig sha256 `1927ed1c…` + `line_79` 原文 +
   `diagnosis.c` 仍为 `e1696ec9…`），让任何人都能用散列复核"这次构建用的是哪一份源"。
   我的 A6 检查当时输出的就是"工作树与 HEAD 差一行"——这个信号本来就该在提交前被读出来，而不是提交后被读到。

## 9. 现场包返修 R2（复审 2026-11-01：5 项包问题 + 2 项登记问题 + C-01 待办；离线，零设备，不重编）

### 9.1 复审的五项，逐条先复现再改

| 项 | 复现到的原始事实 | 改法 |
| --- | --- | --- |
| ① 回读仍认旧镜像 | `tools.readback.image` 指向 R3 的 BIN `00608ec9…`，`tools.download.image` 指向 R3 的 AXF `0bba8ca7…`（而它自己的 argv 已经写的是 R4 AXF——同一个对象内部就矛盾）。用**本轮新 BIN 的原字节**跑现存回读判据（`ManualActionBackend.readback`），绑旧镜像时如复审所说立刻 `READBACK_HASH_MISMATCH`，绑新镜像时 `REAL_HASH_EQUAL`（检查 F7） | 两处 image 全部重绑 `dab356c6…`/`0babe9f1…`；`identity_consistency()` 新增为 `validate_plan` 的 `PLAN_IMAGE_IDENTITY_CONSISTENCY`，逐字段核 S0=AXF=回读目标、argv 末参数=AXF 路径、且回读目标不得等于 `board_image_before` |
| ② 两场顺序与基线 | 旧顺序是 `S8,S8b,S9,S9b,…`（同一分支标签下两场交错），且 `branch`/`active_profile` 是**包级单变量** ⇒ 一场触发、另一场未触发会撞 `RESPONSE_BRANCH_CONFLICT`（2157 行）；两场还把 `bb_session/flash_used_before` 记进**同一批键**，后一场覆盖前一场 | 数组顺序改成 `S8,S9,S10,S11,S8b,S9b,S10b,S11b`（执行器按数组迭代）；分支/档案按 `stages[].scenario` 存（`scenario_branches`/`scenario_profile`，事件与阶段条目都带 scenario）；基线键加链后缀 `_sg`/`_imu`，比较规格、`require_frames.session_key`、`new_log_window.before_key` 一起跟着改 |
| ③ 额度 | 按真实路径数：serial_session 阶段共 **12** 个 ⇒ `port_open` 10 少报；能建立撤能义务的阶段是 S4、S8、S8b、S10、S10b（`MOTION_OBLIGATION_CMDS` 含 `motor_arm`）⇒ 清理 **5** 组，申报 3 少报；`explicit_reset` 真实只有 S3 一处 ⇒ 申报 2 是多报 | `derive_quota_needs()` 从计划自己推导（阶段 kind、步骤 `quota`/`action`+`quota_name`、义务阶段的三条清理各一），`PLAN_QUOTA_NEED` 逐项对账：`need>declared` 即离线 FAIL。本轮 `port_open 12 / cleanup_* 5 / motor_run 3 / explicit_reset 1 / 25 s 总额` |
| ④ 首因 | 合同原来只是 `first_cause_contract` 的说明文字；执行器没有"缺证"这一层，只有 trip/no_trigger 两支 | `stages[].first_cause_legs` + `first_cause_verdict()`：同会话的 TRIP 规则/代际、`val=/thr=`、`sg=/cur=/imu=`、本会话 `diag_profile show` 的 id/rev（且等于 S5 提交的 `recorded.id/rev`）、本场事故基线已记录 —— 缺任一条 ⇒ `EVIDENCE_INCOMPLETE`（阶段不成功）；取证会话再要求 LATCH 行的规则/代际等于**本场** TRIP。`response_branch()` 加降级守卫：窗内已见触发针却记 `OBSERVATION_NO_TRIGGER` ⇒ `TRIP_REWRITTEN_AS_NO_TRIGGER` |
| ⑤ 机械前置 | `load_means` 的 `NOT_PROVIDED` 只在 `prerequisites` 字段里；`gate_load_means` 是人工门，stub 下有申报答案就放行 ⇒ 仍会走到 `motor_arm/motor_start` | 新增包级 `mechanical_prerequisites`（T8 丝杆+标称 300 mm 完整滑台、**尚未连接**、导程待确认、仍缺的三项），带 `blocks_motion: true` 的前置必须出现在起转阶段的 `prerequisite_gates` 上（`PLAN_PREREQUISITE_GATE` 检查这点）；执行器在**开端口之前**跳过整条链并具名 `STAGE_SKIPPED_BY_PREREQUISITE` + 汇总 `PREREQUISITE_REFUSAL`，不扣 `motor_run`、不发 arm/start。示波器/电流钳 `blocks_motion: false`：继续暂缓，不作前置也不新增仪器 |

登记两点：`identity_binding` 不再把 `48204fe` 写成新镜像的构建输入 —— 实测该提交的树里 `rtconfig.h` 是 **256**（`git show 48204fe:…` 的 blob `9fe0efdf…`/8689 B），1024 版第一次入库是 `fe3d9f5`（blob `b66753d6…`/8850 B），tip `58904e1` 未再改它 ⇒ `software_input_commit=58904e1`、`tested_image_build_commit=fe3d9f5`、`build_time_head_commit=48204fe` 三个事实分列。rtconfig 双身份同时进 `build_manifest.json.build_inputs`：本机 CRLF `1927ed1c…`/9304 B 与远端 LF `b66753d6…`/8850 B，`git show tip:` 的 blob 与归一后的工作树字节逐字节相同 ⇒ 只差行尾，不因此重编。

### 9.2 C-01 交付包与 C-02/C-03

`NS800_C01_Delivery_20261101.zip`（sha256 `3eb4c1d3…`）的 4 件按原路径放进
`reports/reviews/phase8-m8-parallel-protection-ready/`，逐字节比对与 zip 内清单一致（`L1` 表），不覆盖、不重跑、不改脚本内绝对路径。
它的三条待办本轮全部接走：

1. **C-02 停后帧事件码 9→5**：源码可判 —— 相对保护只投 `EVT_MULTI_FAULT`（`diag_relative.c:725`），
   `ss_code_for_event()` 映射到 `FAULT_MULTI_SOURCE`（`safety_state.c:309`），`safety_state.h:46` 定义其值 **5u**；
   `9 = FAULT_SOFT`（`:50`）只由 `EVT_SOFT_FAULT` 产生，而 `safety_trip` 在各场景 `forbidden_commands` 里。
   范围只点名：R4 计划两处 `require_frames`、仍被引用的 R3 两份计划、两份 `rows.json` 的 16 帧夹具。
   归档运行输出 `offline/psrun-*/neg-stagemutate/plan.json`（7 份，5 份仍含 event 9）与 M5 软件触发原件一律不动；
   自检里两条负控证明确实是定向：`FS78` 把判据退回 9 后，同一批 event=5 的帧被 `NO_FRAME_WITH` 具名拒绝（修前形态复现）；
   `FS79` 摘掉 event 腿后，只有 event=9 的停后帧被放行（证明拒绝来自这条腿）。
   夹具的 `flash_dump 0 8` 原本**没有 `_provenance`**（全库检索也没有任何板上采集给出 flash_dump 的 CSV 行）⇒ 补
   `kind=source_shape` 并写明"形状取自 `ns_flash_shell.c:159-161` 的打印，数值合成，不构成实测"。
2. **must_min 并入已有自检**：R3 自检新增 `FS76`，用两份真实计划字节 + 真实 `assert_step` 跑 34 例
   （健康 5、拒绝 8、超时/不支持/重复行/畸形行，加 4 个元负控：下限改 9、摘掉 must_min、指向不存在的组、退回精确 8）。
3. **ADC68/SG8 健康夹具**：两份 `rows.json` 新增键
   `diag_profile learn 8 80 120 0@adc68_over_sample`（同样标 source_shape），`FS76` 直接吃这条夹具行，健康面不再只落在 8/8 边界。

### 9.3 四种触发组合的定向对照（`host/verify_four_branch_matrix.py`，零设备）

离线分支按链申报（`plan.stub_branches`，`PLAN_STUB_BRANCHES` 校验拼写），因此两场的
触发/未触发组合能真实跑：`V1` 四种组合全部 `status=OK`；`V2` 每场都是 运动→取证→拒绝→恢复，
且 SG 场整体先于 IMU 场；`V3` 两场均触发时**实测用量** `port_open=12、cleanup_* =5、motor_run=3`
（QUOTA_AUDIT 逐条 `used=12/5`，余额非负）；`V4` 事故基线按场隔离，一触发一未触发时只留触发那场的
`trip_*` 身份；`V5` 交付态（`load_means=NOT_PROVIDED`）下 SG 链四阶段在汇总里一条行都没有、`S_S8` 目录未建、
实际只用 8 次开口，而 IMU 链照常发出 `motor_start`；`V6` 摘掉源样本行 ⇒
`code=EVIDENCE_INCOMPLETE` 且 `status` 不是 `OBSERVATION_NO_TRIGGER`，同时 `disarm=CONFIRMED_DISABLED`、
`terminal=KNOWN`（失败仍先完成必要撤能）；`V7` 降级注入被 `TRIP_REWRITTEN_AS_NO_TRIGGER` 挡住；`V8` 无
`RESPONSE_BRANCH_CONFLICT`。汇总 8/8，原文 `host/four_branch_matrix.txt`。

### 9.4 本轮自检与仍**未证**的东西

`host/idle_learn_check.py`：**CHECKS=92 problems=0，META_NEGATIVES=6 fired=6**（A–M 组，F/G/H/I/J/K/L/M 是本轮新增）；
`m8r3_field_selftest.py`：**79/79**（新增 FS76–FS79，负控注入 13）；
`--validate-plan`：**checks=233 fail=0**；`staged_preflight_batch.py --selfcheck` 仍以 `sha_mismatch` 命中。
本轮自己抓到并修掉的三处检查器缺陷：`FS69` 的突变用 `all(x.isdigit())` 筛帧，把含负数列的真实行整行丢掉，
于是"缺停后帧"和"空帧"退化成同一个用例；`A6` 的"差一行"其实是拿未跟踪时 `git show` 返回空凑出来的巧合；
`identity_consistency` 之外还漏了 argv 末参数与 AXF 路径的一致性（现并入同一条门）。
仍未证（不得说成通过）：谁写入 `0x00bd80b0`；idle/diag 栈越界与该损坏的因果；1024 是否够；
`f5c2fd89…` 与新 BIN 的 1831 字节差异未逐字节归因；学习/提交/重启恢复/两路激励的**现场**结论全部未取；
`protect_ready=NO`、`threshold_validation=NOT_ESTABLISHED`、`rel cov=NOT-ACTIVE` 不因包的绑定而改变。
本包状态仍是 `BOUND_NOT_EXECUTED`：执行需要复审对 `plan_core_r4.json` + 执行器 + 两份计划/夹具的一次原文批准。

## 10. R5 两项收口（复审 2026-11-01 实测；只改计划、结果分类与对应少量用例；不改 C、不重编、零设备）

### 10.1 修前的字节级复现（不是照抄复审的话）

拿上一版交付 `1101479` 的**已提交字节**读出 IMU 那两场：`S8b.steps[trip_window].key = "__watch__"`、
`S9b.steps[latch].key = "diag_status@latch"` —— 两个键都是 SG 那一组夹具（`__watch__.trip` 是
`r=REL_SG_SEVERE … imu=0`），而 `S8b` 既没有 `target_channel` 也没有 `prerequisite_gates`（`[]`）。
所以"IMU 场吃 SG 的数据还算通过"和"S4/S8b 不受装配门约束"都是包里当时的真实状态，不是描述问题。
同一件事在修后工装上也复现：把 `S8b.target_channel` 摘掉（回到修前的判定形态），喂 SG 行的 IMU 场
立刻变成 `core_protection_verified=True`（矩阵 `V12`，原文
`field/dispatch_stub_target_gate_removed.txt`）—— 说明 ① 的拒绝确实来自这个声明。

### 10.2 ① 流程跑完 与 目标通道验证成功 分开判

- 每场在 `stages[].target_channel` 里申报：`rule_key`（本场记录下来的 TRIP 规则名）、`rule_names`
  （本场认的规则名）、`source_nonzero_keys`（该通道必须非零的源样本列）、`known_rule_names`
  （产品枚举全集）。IMU 场 = `REL_IMPACT` + `trip_imu_imu≠0`；SG/ADC 场 = `REL_SG_SEVERE`/
  `REL_ADC_SEVERE`/`REL_FUSED_SEVERE` + `trip_sg_sg`、`trip_cur_sg` 非零。规则名不是自己写的：
  `rel_rule_names()` 同时从 `diag_relative.h` 的 `rel_reason_t` 与 `diag_relative.c` 的 `verdict_name()`
  两头取（实测 7 个：`REL_ADC_SEVERE, REL_COVERAGE_LOST, REL_FUSED_SEVERE, REL_IMPACT,
  REL_OUT_OF_WINDOW, REL_SENSOR_UNAVAILABLE, REL_SG_SEVERE`），两头不一致直接
  `REL_RULE_NAMES_SOURCE_MISMATCH`。
- 执行器 `target_channel_claim()` 给出四种具名结果：`VERIFIED`、
  `OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)`、`TARGET_RULE_BUT_SOURCE_FIELD_ZERO(键)`、
  `TARGET_UNPROVEN(…)`；`dispatch` 结果与台账同时带 `protection_claims` /
  `core_protection_verified` / `flow_complete`，汇总行 `PROTECTION_CLAIM|…` 与
  `FLOW_VS_VERIFICATION|flow_complete=…|core_protection_verified=…`。**其他来源触发不减少任何动作**：
  撤能、取证、`event=5` 停后帧读回照做（`V10` 实测整链 4 阶段仍走完、`status=OK`）。
- 夹具按场分开（`host/apply_target_channel_fixtures.py`，全部标 `kind=source_shape`）：
  `__watch__@imu`（`r=REL_IMPACT … imu=3789`）、`diag_status@latch@imu`、以及只给元负控用的
  `__watch__@imu_zero`（规则名对但 `imu=0`）。这些行的**形状**取自 `applications/diagnosis.c:469-475`
  的三行 `REL TRIP`/`rel LATCH` 打印，数值是合成的 —— 没有任何板上采集给出过 `REL_IMPACT` 的触发行，
  所以它们只让分类判据有一份形状正确的输入，不构成设备证据。
- 离线新增两条门：`PLAN_TARGET_CHANNEL`（每个分支决定点必须声明，且规则名必须在产品枚举里）与
  已有的 `PLAN_FIRST_CAUSE_LEGS` 并存。两条元负控都 fire：`N5` 摘掉声明 → 数据不再被拒但该突变计划被离线拒绝；
  `N6` 把规则名换成 `REL_IMU_MADE_UP` → 离线具名拒绝。

### 10.3 ② 装配就绪门覆盖**所有**起转阶段，机械事实按现场口径更新

- 事实改为：**现有 T8 丝杆套件，标称导程 8 mm；已按照片装配，紧固、螺母防转及导向尚待确认。**
  轴承/底座目前只能手动固定（操作者 2026-11-01 原话）。旧的"完整滑台 / 尚未连接 / 导程待确认"三段
  已从执行字段里消失（`J2` 扫描 `mechanical_prerequisites + load_step + not_provided_summary`，
  不扫全文，免得把本节的勘误引用当成过期事实）。
- 新增包级 `assembly_readiness`（`status: NOT_CONFIRMED`，`blocks_motion: true`），挂在
  **每一个**含 `motor_arm`/`motor_start` 的阶段上：`S4`（带起转的学习）、`S8`/`S10`
  （`[load_means, assembly_readiness]`）、`S8b`/`S10b`（`[assembly_readiness]`）。
  `PLAN_SPINUP_GATE_COVERAGE` 逐阶段检查这点，构建脚本另有一道自检：计划里"有起转命令的阶段集合"
  与"写了门的阶段集合"不一致就直接拒绝生成。
- 拒绝**沿 requires 传播**：`S4` 被挡后，依赖本次学习结果的 `S5`（tier/commit）、`S6`（重启）、
  `S7`（恢复复核）一并具名跳过（台账 `STAGE_SKIPPED_BY_PREREQUISITE` + `via_requires=S4`）。
  这一条不是为了好看：现场若在没有任何一次 `learn` 的前提下继续 `tier/commit/save`，唯一一次批准
  会在中途失败上烧掉。
- 因此**交付态实测**（矩阵 `V5`，原文 `field/dispatch_stub_healthy.txt`）：真正执行的只有 `S0 S1 S2 S3`；
  `S4…S11b` 共 12 个阶段在开端口之前跳过；`port_open` 用量 **1**（申报仍是 12，那是全路径上限）；
  `motor_run` 用量 **0**（一次 `motor_arm`/`motor_start` 都没发出）；两场的分类都是
  `SKIPPED_BEFORE_SPIN_UP(PREREQUISITE)`；`flow_complete=True` 而 `core_protection_verified=False`。
  只缺加载、装配已确认的那一面（`V5b`）：跳过 `S8 S9 S10 S11`，`port_open=8`、`motor_run=2`、
  清理 3 组，`S4` 与 `S8b` 仍起转，IMU 场 `VERIFIED` 但 `core_protection_verified=False`
  （因为 SG 那场没跑）。
- 现场口径一并进包：滑台起点在左端且与末端限位留间隙；顺时针=向右要在**第一次被批准的短移动**里核实；
  软件限位输入仍 `DISABLED_BY_CONFIG`（不把左侧起点当限位保护、不撞端制造堵转）；
  IMU 用绝缘胶带固定在电机上（此前的缺失项"ADXL345 实际固定方式"已闭合，但刚度未测，不做精确振动标定）；
  不通电脱开丝杆、只做空载学习与 IMU 演示的那条路记为**备选且本轮未采纳**，它同样受装配就绪门控制。

### 10.4 R3 的两份历史计划在当前执行器下的结论（当场复算，不回写）

`plan.json` 与 `plan_continuation_s2.json` 现在各得 **fail=3**（`PLAN_SPINUP_GATE_COVERAGE`、
`PLAN_TARGET_CHANNEL`、`PLAN_FIRST_CAUSE_LEGS`）—— 它们是在这两项补正之前定稿并执行过的。
本轮**不修改**这两份字节、也不重跑它们绑定的轮次；这个数字由构建脚本当场调用当前执行器复算后写进
`plan_core_r4.json.r3_historical_plans_under_current_executor`，`N8` 再独立复算一次核对是否相等。
以后若要复用，必须先按同样两项补正并重新绑定，不能拿旧计划当"现在这套判据也能跑"。

### 10.5 本轮数字

`host/verify_four_branch_matrix.py`：**14/14**（V1–V13 + V5b；四种触发组合、顺序、额度、按场基线、
缺证撤能、不降级，加本轮新增的分类四例与两条离线负控）。
`host/idle_learn_check.py`：**CHECKS=104 problems=0**，`META_NEGATIVES=6 fired=6`、
`META_NEGATIVES_R5=6 fired=6`（新增 N 组 8 条与 J6/J7/J8、E12）。
`m8r3_field_selftest.py`：**79/79**（负控注入 13，hardware_actions=0）。
`--validate-plan`：**checks=236 fail=0**（原 233）。
字节身份：计划 `plan_core_r4.json` 140800 B（原 112003），执行器 `m8r3_serial_chain.py`
sha256 `e39efd9819fe…`/148016 B，两份 `rows.json` 51963 / 51944 B；
**镜像与构建产物未动**：BIN `dab356c69fbf…`/290916 B、AXF `0babe9f1…`，产品 C 源码零改动，构建 0 次、设备动作 0 次。

### 10.6 这版交付**不能**声称的事

包状态仍是 `BOUND_NOT_EXECUTED`。交付态没有任何一次起转，因此：学习是否发生、SG/ADC 受控加载、
IMU 冲击演示、停机锁存与人工恢复的**现场**结论一个都没有；`flow_complete=True` 只说明申报的阶段
都到了各自的终态（含具名跳过），**不等于核心保护功能全部通过**，也不等于装配/加载前置已满足。
`protect_ready=NO`、`threshold_validation=NOT_ESTABLISHED`、`rel cov=NOT-ACTIVE`、
`limit_inputs=DISABLED_BY_CONFIG` 全部照旧。装配就绪的 4 项（松手保持固定、紧固、螺母防转、导向）
要操作者逐条确认才能把状态写成 `PROVIDED`，我不代填。

## 11. R5 收尾：汇总标签修正 + 现场状态绑定（复审补测一项；不重编、不重跑历史清单、零设备）

复审补测抓到的是一行聚合判定：让 `S9b` 取证失败，整包已经 `STOP`、`flow_complete=false`，
汇总却仍报 `core_protection_verified=true`。原因在旧写法只看各场声明 —— 声明是**逐场**在各自的
运动会话结束时写下的，两场都写成了 `VERIFIED`，而失败发生在之后的取证会话里，标签没看整包结果。
现在按复审给的形状判（`m8r3_serial_chain.py` 的 `dispatch_plan` 末段）：

```python
core_verified = bool(ok) and bool(claims) and all(v == "VERIFIED" for v in claims.values())
```

- 反例进现有定向矩阵，成为 `V14`：`S9b` 的锁存回读换成 SG 那组夹具 ⇒ `must_equal_recorded` 咬不住 ⇒
  `first_cause=EVIDENCE_INCOMPLETE`、整包 `STOP`、`flow_complete=False`，此时
  `protection_claims` 两场**仍然都是** `VERIFIED`（这正是旧标签出错的地方），而
  `core_protection_verified=False`；撤能在运动会话 `S8b` 里仍是 `CONFIRMED_DISABLED`，
  取证会话自己不带电机义务所以是 `NOT_ARMED`。把 `ok` 这一项摘掉，`V14` 必须翻红 —— 这条反例
  本身就是对该判据的元负控。
- 计划里 `dispatch_semantics.flow_vs_verification` 写明机器写法，另加 `claims_are_per_stage`：
  整包结论只看 `core_protection_verified` 与 `flow_complete` 两个标签，必须同时为真才可以说"验过"。
- 检查器加 `N9`（核执行器源码里的写法与交付态干跑的 `flow=True/core=False` 实测行）。

现场状态绑定（`plan_core_r4.json.field_state_binding`）只登记已经收到的操作者事实与指令，并逐条列出
**未收到**的确认：紧固、螺母防转、导向、支座松手保持固定、第一次短移动的方向核实。
因此 `assembly_readiness` 维持 `NOT_CONFIRMED`（所有起转阶段仍被门挡在开端口之前），
操作者指令"暂不试转"记入 `operator_instructions`，本轮 `approvals_consumed_this_round = 0`；
空载脱开丝杆那条仍是**备选且未采纳**。这层绑定不产生任何现场结论。

数字（全部在同一份最终字节上重跑）：定向矩阵 **15/15**（V1–V14 + V5b）；
`idle_learn_check` **CHECKS=105 problems=0**，元负控 6（D 组）+6（R5 组）全部 fire；
`m8r3_field_selftest` **79/79**；`--validate-plan` **checks=236 fail=0**；
`staged_preflight_batch` 六份清单 problems=0。
身份：计划 145308 B，执行器 sha256 `40517501b3be…`/148267 B，两份 `rows.json` 未变
（51963 / 51944 B）；镜像、AXF、MAP 与 `build_manifest.json` 未动（`dab356c6…`/290916 B、
`0babe9f1…`/1771288 B），产品 C 源码零改动，构建 0 次、设备动作 0 次。

## 12. 装配就绪确认落地 + 启动前实际计划序列（2026-11-01；不改 C、不重编、零设备）

操作者在审核窗口逐条确认：**支座松手保持固定、连接紧固、螺母防转、移动块导向均无问题**。
按这条确认把 `assembly_readiness` 改为 `PROVIDED`，四条从 `field_state_binding.not_received` 移除并
改记为 `operator_confirmed`（原文引用写在 `provenance` 里）。`load_means` 不代填，SG/ADC 加载链继续跳过。

**启动前实际计划序列**（`plan_core_r4.json.field_sequence_before_start`，由构建脚本的
`blocked_stages()` 从计划数据推导，检查器 `E13` 与真实干跑逐条对账）：

| 序列 | 阶段 | 说明 |
| --- | --- | --- |
| 实际执行（12 个） | `S0 S1 S2 S3 S4 S5 S6 S7 S8b S9b S10b S11b` | 下载→回读→启动核对→零点/学习→tier/commit/save→重启恢复→IMU 激励→取证→锁存态拒绝→人工恢复 |
| 起转前跳过（4 个） | `S8 S9 S10 S11` | `PREREQUISITE_REFUSAL|scenario=SG_ADC_controlled_load|gates=load_means` —— 只具名真正缺的那一项 |
| 实测用量 | `port_open 8/12`、`motor_run 2/3`、必要清理 3 组 | 起转 2 次（S4 学习、S8b 激励）+ S10b 锁存态再 arm；仍 ≤3 次起转、学习≤15 s、IMU≤5 s |
| 汇总标签 | `flow_complete=True`、`core_protection_verified=False` | SG/ADC 那场没跑，核心保护不能称全部通过 |

门没有拆：`assembly_readiness` 仍挂在 `S4/S8/S8b/S10/S10b` 每一个起转阶段上（`J6`），
`PLAN_SPINUP_GATE_COVERAGE` 与 `PLAN_PREREQUISITE_GATE` 仍 PASS；任何一项回退成未确认，该阶段又在
开端口之前被具名跳过。回退路径不是纸面话：`E14` 用显式场景（把 `assembly_readiness` 设回
`NOT_CONFIRMED`）真实干跑一次，实测 `prerequisite_skips=S4,S5,S6,S7,S8,…,S11b`、`via_requires=S4`、
`port_open` 降到 1、台账里零条 `motor_start`（原文 `field/dispatch_stub_assembly_not_confirmed.txt`）。
定向矩阵的 `V5` 因此改成显式构造该场景，`V5b` 才对应交付态 —— 场景不随计划状态推进而失真。

本轮仍**未收到**的两项：第一次短移动里核实"顺时针=向右"（操作者明确保留）、累计运动距离按标称
8 mm 与实际细分/脉冲核算。空载脱开丝杆那条备选从未采纳，装配确认后其动因消除，但状态回退时仍受门控制。

重跑（最终字节）：定向矩阵 15/15；`idle_learn_check` **CHECKS=107 problems=0**（新增 E13/E14，
元负控 6+6 fired）；`--validate-plan` **236 项 0 失败**；`m8r3_field_selftest` 未重跑（执行器字节未变，
仍是 `40517501b3be…`/148267 B）。计划 150962 B；两份 `rows.json`、镜像、AXF、MAP 与
`build_manifest.json` 全部未动。包状态仍是 `BOUND_NOT_EXECUTED`，本轮消耗现场批准 0 次；
现场执行等待复审对这份最终绑定的批准。

