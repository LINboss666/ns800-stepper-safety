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
- **两数并列，不互相替换**：任务书的"固定帧合计 264 B"像是这条链上前若干帧的部分合计；668 是 armlink 自己算的
  最深合计。本轮按更保守的 668 取 ⇒ 1024 覆盖 668 并留 356 B；`668` 与 `1024` 都**不**称"已验证上界"。
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
