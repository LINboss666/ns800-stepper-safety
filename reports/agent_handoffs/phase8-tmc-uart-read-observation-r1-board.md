# 交接 — phase8-tmc-uart-read-observation-r1-board

- 任务书：`NS800_URO_R1_Review_GO_and_Board_Package_66f8533_20261028.md`（外部审核方撰写，用户附件转达）
- 复审对象 / 本轮基线：`66f853309fe22376ffa6b45080e5f79c5891d4d1`
- 软件、构建与镜像输入 code_commit：`a483dcba74abd8c04549459bcee2cbcdb1ddea4c`（本轮未改动任何产品/夹具/工程/脚本字节）
- 阶段：**S0 离线绑定完成 + 执行包已实例化；设备动作 0；停在用户一次批准门**

## GO 登记（按复审 §10：随本次真实板级工作一并登记，不开纯登记轮）

```text
SOFTWARE_IMAGE_CAPTURE_REVIEW_GO_FOR_BOUNDED_READ_DIAG   复审对象 66f8533（2026-10-28）
BOARD_PACKAGE_READY / AWAITING_USER_APPROVAL / NO_NEW_HARDWARE_ACTIONS
```

接受范围：R1 三组返修（A 导出、B 出口一致性、C 采集完整性/业务分离/横幅门）关闭；现有镜像与
冻结采集器**可用于本包的一次有界板级只读通信诊断**。该 GO 限定诊断用途，不构成电机使能、STEP
输出、物理时序验收或"产品无缺陷"结论；上一轮 URO 的两枚镜像仍未获下载批准。

## 一句话结论

S0 六项离线绑定全部通过并可复算：本地=远端=`66f8533` 且跟踪/未跟踪工作树均干净、三个冻结引用
逐字未动；第 3 节四件加本地 AXF 与新副本载荷共六项**长度+SHA-256 现场直算全 MATCH**（含 AXF 的
本地独有口径）；下载工程整份复制到全新执行根后 `.uvprojx`/`.uvoptx` 与源件逐字节相同（工程、已证
FLM、`-FS08000000`、CMSIS_AGDI 均未改），新副本 `dl\rt-thread-a0.axf` 已换成固定 AXF 并由目标
`rt-thread` 的 `OutputDirectory=.\dl\` + `OutputName=rt-thread-a0` 证明 Load 解析到它；工具版本
UV4 5.43.1.0 / Python 3.12.5 / pyOCD 0.45.1 / PowerShell 5.1.26100.5074 与复审要求一致；DAP UID
从两处既存本地原始件唯一交集解析（长度 48，形状符合，值不入仓库）；pyOCD 工作目录无隐式配置；
采集器散列等于冻结值且未修改；OutDir 与回读文件均"尚不存在"。展开命令已参数化并过 PowerShell
5.1 语法门（`PARSE_ERRORS=0`）与 `-Step Precheck` 真实只读跑（rc=0）。S1–S6 一步都未执行。

## 1. 交付物

| 路径 | 说明 |
|---|---|
| `reports/reviews/phase8-tmc-uart-read-observation-r1-board/s0_offline_binding.txt` | S0 六项绑定的逐项实算原文（基线/散列/副本绑定/工具版本/UID 依据/语法门与 Precheck 输出） |
| `…-board/execution_package.md` | 代号绑定的执行单：S1→S7 顺序、每步判据、额度口径、不授权清单、结论四层分列、待批准文字 |
| `…-board/commands.expanded.masked.ps1` | 私有执行脚本的脱敏公开副本（`lossless=false`，替换规则写在文件头），逐行 158 对齐 |
| `reports/agent_handoffs/phase8-tmc-uart-read-observation-r1-board.md/.json` | 本交接（含 GO 登记、§4 限制、S4 顺序修正、额度、停点） |
| 仓外（不入库） | 新执行根 `<本地执行根>` = `download-r1/`、`pyocd-work-r1/`、`readback/`、`logs/`（含 `s1_s6_commands.ps1` 真路径件、`dap_uid_private.txt` 权限 600、`local_path_index.json` 权限 600）、`logs/s0_precheck.out` |

本轮**没有**改动：生产源码、验证链夹具、runner、`.uvprojx`、采集器脚本、任何历史原证据或镜像。
旧 BOARD-B / M0 执行根只读引用，未写入、未覆盖。

## 2. 与上一轮已推送文档包的差异（顺序修正，不回写历史）

| 项 | R1 `board_candidate.md`（已推送） | 本包（实例化，以此为准） |
|---|---|---|
| 复位位置 | 旧 §2 的 **S6 条件步**："被动窗口既无横幅也无提示符且未发命令时"才复位 | **S4**：S3 回读之后、**开串口之前**的唯一一次显式 `reset sysresetreq`（pyOCD 会话 #2） |
| 为什么必须改 | 表内假设可在采集器内部插入复位 | S3 会留下 `Halted`，而冻结采集器自行从被动等待进入首条查询，**无法在其内部插复位** ⇒ 必须先复位启动再启动采集器 |
| 后续窗口 | 未细化 | 明确"之后可能漏掉启动横幅 ⇒ 走已验证镜像 + `BANNER_NOT_OBSERVED` 继续，不为补横幅再复位" |
| 其余额度 | 1 下载 / 1 回读 / 2 会话 / 1 复位 / 1 次 COM5 / 7 条命令各 1 | 逐字沿用，未增加任何动作 |

历史文件不回写；本表与本交接 §3 的 `S4 顺序修正` 登记即为差异说明（复审 §7 明确"取代远端
board_candidate.md 旧 S6 条件复位"）。

## 3. GO 携带的非阻断限制（本包按此执行与登记）

- **GO-OBS-01 `echo_match_off` = `KNOWN_INVALID_FOR_DIAG`**：复审独立复现该字段在"一次读 12 B
  （4 B 回声 + 8 B 应答）"时给出 `match=1/8` 而真实偏移是 0，且受接收分块影响。本包保留导出原文，
  **禁用**该字段做定位、应答起点或时序结论；真实 `rx_total`、收发计数、业务返回、原始样本与
  命中存在性不受影响，采集器也未用该偏移作判据。即便某次导出恰为 0，也不因此恢复可信等级。
  修复留到下一次真实产品工作（须用同一字节流的 `[12]` 与 `[4,8]` 分块一致性验证）；
  **本包不为该可选字段返修或重烧一个版本**。
- **51 vs 50**：自检实际 51 个唯一具名负控，脚本汇总串声明 50（= `Expect-*` 调用数；差的那 1 条是
  `missing_response_prompt`，由 `Wait-Prompt` 真实超时路径产生）。并列保留，以逐事件台账为准，不改脚本散列。
- **证据排版例外**：`git diff --check 6324ac4..66f8533` 非 rc=0（复审逐行指认的尾空白/EOF 空行）。
  历史证据原文**不改写做绿**；本轮新增文件按声明规则不保留行尾空白（见 `s0_offline_binding.txt` 自检）。
- **未单独具名**：`tmroAll != tmroOk` 的行终止分支被更具体的前置检查覆盖，保持"未单独具名命中"，
  不声称所有内部条件各自验证过。
- **复位计数口径**：显式 `SYSRESETREQ` 1 次 ≠ 芯片物理总共只复位一次；Keil 既有初始化/复位沿用已证
  配置并计入那一次下载。能逐次观测就实记，不能就记 `NOT_SEPARATELY_OBSERVABLE`，不虚报物理总数。
- **"只读"含义**：指本包主动 TMC 诊断命令不写寄存器，不表示整个固件启动过程不写 GPIO/存储；
  三笔读上限指 msh `tmc_uart_probe`，不含既有启动读事务。

## 4. 现场依据与额度（批准即生效；本轮未用掉任何一项）

| 动作 | 上限 | 本轮 | 待批执行 |
|---|---:|---:|---:|
| 常规上电（仅当前确实无电） | ≤1 | 0 | 由现场人员做，Agent 不代填"已供电" |
| 既有 Keil 下载 | 1 | 0 | S2，一次尝试，失败也计入 |
| pyOCD/DAP 会话 | 2 | 0 | S3 回读（halt 不恢复）、S4 复位启动 |
| Flash 定长回读 | 1 | 0 | S3，`0x08000000` + `0x41834`，不重读 1 MiB |
| 显式 `SYSRESETREQ` | 1 | 0 | S4，开串口之前 |
| COM5 打开尝试 | 1 | 0 | S5，仅由冻结采集器打开 |
| msh 命令（含 probe 至多三读、0 WRITE 帧） | 7×1 | 0 | S5，脚本依序各一次 |

沿用条件（本包批准即确认此次沿用，不逐条重索取许可）：现场有人可操作、设备可连接、本包之外无
其它动作；电机物理未接、SW1 阻断、模块与线路不变；供电/接线/扩展板线路按用户 `USER_CONFIRMED`，
不作 ENN 对照，不重索取供电说明、断电顺序回报、整体照或接线两端照；A0 采样继续暂缓、
`A0_DURATION_EVIDENCE_NOT_ACHIEVED` 不改；最终供电状态未知就如实记未知，不为填字段连接设备。

不授权清单（一律不做）：电机连接/运动、STEP、DIR/许可 HIGH、翻宏、arm/start/ramp、清故障、配置写、
手动 selftest、`pin_status`、额外 A0 采样/清理、自动恢复、外部 SPI Flash 转储、全片备份、
设备/端口/探针枚举、`sw1_cut.ps1`。

## 5. 边界与未证

设备动作 0：未创建 SerialPort、未开 COM5、未发目标命令、未 `pyocd list`/未枚举 USB、未读 Flash、
未下载、未复位、未连接任何目标（pyOCD 只做 import 级版本查询）、未碰供电/接线/ENN/SW1、未输出
STEP/DIR/HIGH、未运行 `sw1_cut.ps1`；Keil 未构建（副本仅复制+放载荷）。

未证/继续开放：板上 TMC 链路与状态（`TMC_LINK_ON_BOARD_UNPROVEN`，取得实证前不变）、板上原 `-117`
根因、完整栈高水位、目标 RT 调度与物理波形/时延、`B-1`、`BUG-008`、`RISK-03/03a`、物理
`RISK-04-b`、`M-1/M-2/M-3`、`BLOCKED_ROLLBACK` 剩余项（1 MiB 快照 `b89f8a7c…` 与恢复 HEX
`b3ac97b9…` 已保存，恢复下载仍未演练）。四个安全宏仍为 0；`safe_for_enable` 在任何 probe 输出里
都必须为 0；通信成功不解锁电机。

冻结引用未动：`main` `1b3757ee…`、`phase7/full-embedded-software` `6d3fbc55…`、
tag 对象 `097935fe…` → 解引用 `e40a9b95…`。未 reset/rebase/merge/force；Git 身份沿用仓库 local
公开 noreply；推送走命令级代理。脱敏为有损替换（规则在各文件头声明），原件留仓外未删除。

## 6. 下一步（不在我这边）

1. 用户按 `execution_package.md` §6 的一次批准文字（或简写「批准执行 URO-R1 板级包」）批准整包。
2. 批准后按 S1→S7 顺序执行；任一步真实失败、前置失效或额度耗尽即停，保存原字节、关句柄、记实际
   消耗，不追加动作、不用旧包续额；结束不索取例行断电回报。
3. 执行轮在同一 `-board` 目录里补 S1–S7 原始日志/回读比对/复位原文/采集 ledger 与分响应归档
   （base64 逐块核长度与 SHA、session 与分响应拼接相等），并按实际填写 §5 的停点标签；
   失败不由补跑覆盖。

当前停止位置：

```text
URO_R1_BOARD_PACKAGE_BOUND_AND_READY / S0_OFFLINE_BINDING_COMPLETE
HARDWARE_ACTIONS_THIS_ROUND=0 / AWAITING_USER_APPROVAL / NO_NEW_HARDWARE_ACTIONS
GO_REGISTERED=SOFTWARE_IMAGE_CAPTURE_REVIEW_GO_FOR_BOUNDED_READ_DIAG(66f8533)
ECHO_MATCH_OFFSET=KNOWN_INVALID_FOR_DIAG / FOUR_DEFAULT_AND_A0_SAFETY_MACROS=0
TMC_LINK_ON_BOARD_UNPROVEN / SAFE_FOR_ENABLE=false
BLOCKED_ROLLBACK_OPEN / M2_M3_NOT_AUTHORIZED / A0_DURATION_EVIDENCE_NOT_ACHIEVED
```


---

## 7. 真实执行结果（2026-10-28 09:18:20 → 09:20:13 +0800，本机实跑）

批准 = 用户真实回合简写「批准执行 URO-R1 板级包」（2026-10-28 09:18 +0800）。
本节覆盖 §6「当前停止位置」里的 `AWAITING_USER_APPROVAL` 一项：批准已到达并已按 S1→S6 执行；
§6 其余历史文字与 S0 证据按原文保留，不回写。

### 7.1 逐步结果

| 步 | 内容 | 结果 | 关键原文位置 |
|---|---|---|---|
| 前置 | 执行前只读身份复核 | PASS（BIN/manifest/采集器/AXF 四件一致） | `board_run_evidence.txt` §0 |
| S1 | 供电 | 无动作（USER_CONFIRMED 沿用） | 同上 §1 |
| S2 | 一次 Keil 下载 | **PASS**：`Load <新副本>\download-r1\dl\rt-thread-a0.axf` + Erase Done. + Programming Done. + Verify OK. | 同上 §2 |
| S2′ | 私有 runner 工具缺陷 | 判定过早（UV4 `-f` 异步落日志）→ 该步 rc=1；未重跑下载，只读 09:19:07 生成的日志判据 | 同上 §2 |
| S3 | pyOCD 会话 #1 定长回读 | **PASS**：268340 B，sha256 `a48400c8…` 与接受 BIN **逐字节相等**；两次 status 均 Halted | 同上 §3 |
| S4 | pyOCD 会话 #2 唯一显式复位 | **PASS**：`Resetting target` + `Core 0 (None):  Running` | 同上 §4 |
| S5 | 冻结采集器一次 COM5 + 七条查询 | **FAIL（就绪门）**：`No bounded quiet probe window; no command sent and no retry.`（脚本 :139）；0 条命令发出 | 同上 §5 |
| S6 | 命令序列 | **NOT_REACHED**（0/7） | 同上 §6 |
| S7 | 停机与归档 | 已按包执行：保存原字节、台账、句柄由脚本 finally 关闭；停机后 0 新动作 | 同上 §6/§8 |

### 7.2 额度实际消耗

下载 1/1（用尽）· DAP 会话 2/2（用尽）· 回读 1/1（用尽）· 显式 SYSRESETREQ 1/1（用尽）·
COM5 打开 1/1（用尽）· msh 命令 **0/7** · probe **0/1** · TMC 读事务 **0** · WRITE 帧 0 ·
新增上电 0 · 枚举 0 · 清故障/配置写/arm/start/ramp/STEP/DIR/翻宏/备份/恢复/`sw1_cut.ps1` 全部 0。

Keil 内部隐含初始化/复位包含在一次下载内，记 `NOT_SEPARATELY_OBSERVABLE`，因此不宣称芯片物理复位总量为 1。

### 7.3 新板上事实（被动窗口 23845 B，`passive_window_analysis.txt`）

1. **新镜像确在板上运行**：启动横幅 `5.3.0 build Oct 28 2026 02:23:31` 命中 1 次，与复审 §3 期望逐字一致——
   这是回读逐字节相等之外的第二重身份证据。
2. **阻塞根因（通信之外的新缺陷）**：内核持续输出
   `[W/kernel.sched] warning: sensor stack is close to end of stack address.` —— 20.017 s 窗口内
   243 次（子串/前缀/`\x1b[33m` 三口径同为 243；去 ANSI 后整行精确匹配 237，差 6 行是被并发输出切断的碎片）。
   采集器要求距上一字节 ≥1200 ms 才允许首条查询，因此就绪门在 20 s 上限内不可能满足。
3. **多线程控制台交错 + NUL**：内核告警从中间插进主线程的 `[MAIN] boot complete but state=FAULT_L…` 行；
   流内有 5 个 NUL、243 对 ANSI 转义、17 个裸 CR。这是响应切分侧的真实约束。
4. **固件自检自身的 TMC 判定**：`[SS] [REQ FAIL] TMC2209 link`、`[SS] self-test done: required=FAIL`、
   `[BOOT] 11 startup-selftest FAIL -> state=FAULT_LATCHED`、`[TMC] DEGRADED drive_cfg=UNCONFIGURED`、
   `[Motor] state=4 … health=OK`、`arm gate fail mask=0x1B (arm REFUSED)`、`runtime write gate mask=0x3B`。
   这些是被动观察，**不是** `tmc_uart_probe` 的逐笔读观测，不能给出 OPEN/TXN/锁/flush/TX/RX 阶段或原始 rc。
5. `msh` 提示符在整个窗口内 0 次（本包已明令不把它当首条查询前置，故不影响批准有效性）。

### 7.4 分层与结论

- 镜像/执行层：**已证**（AXF 实际 Load + 268340 B 逐字节相等回读 + 复位 Running + 横幅匹配）。
- capture 层：**INCOMPLETE**（有被动 RX，无分响应、无 TX、无七条查询）。
- business 层：**NOT_REACHED**（probe 未发出）。
- 根因层：板上原 `-117` 仍 **UNPROVEN**；本轮既不支持也未排除任何假设。
- 本轮的诊断目标（区分 OPEN/TXN/锁/flush/TX/RX/期限/身份）**未取得**，推进点改为上面第 2、3 条新缺陷。
- `echo_match_off` 仍 `KNOWN_INVALID_FOR_DIAG`；无 probe 证据更无从使用。

### 7.5 下一轮需要的新授权（本包额度已用尽，不自动续）

本轮停机后未追加任何设备动作。要继续查因必须先解决"控制台不静默"这一层，
可选方向（都需要新的批准，本轮一律未执行）：

1. 让 `sensor` 线程栈告警不再刷屏（提高该线程栈、或抑制该告警）——属于产品改动，需新任务书。
2. 或把就绪门改成"容忍持续告警、只按 `[MAIN] boot complete` 之后的行界判定"——属于采集器改动，
   会破坏 R1 冻结件的已复审身份，需重新走复审。
3. 或在不改固件、不改采集器前提下，用一次新的、明确扩权的现场包。

本节不预设选哪条，也不代为执行任何一条。

### 7.6 停点标签（复审 §10 口径，按实际填写）

```text
URO_R1_BOARD_READ_DIAG_EXECUTED_OR_STOPPED = EXECUTED_THEN_STOPPED
CAPTURE = INCOMPLETE (NOT_REACHED for the 7-command sequence)
BUSINESS = NOT_REACHED
FAILURE_STAGE = READ_ONLY_PROBE_WINDOW(no 1.2 s quiet; no command sent)
ECHO_MATCH_OFFSET = KNOWN_INVALID_FOR_DIAG
SAFE_FOR_ENABLE = false
NO MOTOR / NO STEP_ENABLE / FOUR SAFETY MACROS=0
IMAGE_ON_BOARD = CONFIRMED(byte-identical readback + matching boot banner)
TMC_LINK_ON_BOARD = UNPROVEN(no probe evidence this round)
NEW_BLOCKER = kernel sensor-stack warning storm blocks the quiet-readiness gate
DEVICE_ACTIONS_AFTER_STOP = 0
```

原始/脱敏口径：本轮三件证据（`board_run_evidence.txt`、`passive_window_analysis.txt`、
`passive_capture_b64.txt`）为本地实跑产物的有损脱敏副本（绝对路径→`<本地执行根>`/`<仓库根>`，
UID 只登记长度 48 与 `sha256(uid)[:12]=d2f530dbd062`）；原件留在仓外
`<本地执行根>\logs`、`\readback`、`\capture\r1_run1` 未删除。base64 段是原字节的无损转码文本，
已从写好的文本件本身复验：3 块逐块 SHA 吻合、拼回 23845 B 与原串口件逐字节相等。
来源等级全部为 LOCAL-RUN（本机 Windows 实跑），云端未执行任何一步。


### 7.7 推送后逐 blob 远端独立回读（只读，零设备动作）

远端分支头 = `0b9dd5dd41d317503c1814c283532273d9a56064`（本轮两个提交：`5360a5e` 执行证据 + `0b9dd5d` 下面第 4 条勘误）。
方法：`gh api repos/LINboss666/ns800-stepper-safety/contents/<path>?ref=HEAD -H Accept: application/vnd.github.raw` 取实际字节，
与 `git show HEAD:<path>` 的 blob 字节散列逐件比对（大小与 SHA-256 同时相等才算 MATCH）。

| 件 | bytes | sha256(本地=远端) | 判定 |
|---|---:|---|---|
| `board_run_evidence.txt` | 16656 | `b271a5e5892f…` | MATCH |
| `passive_window_analysis.txt` | 9665 | `10a248b5fa98…` | MATCH |
| `passive_capture_b64.txt` | 34008 | `8bd705b585e1…` | MATCH |
| `phase8-tmc-uart-read-observation-r1-board.md` | 16786 | `7fbd9392b23a…` | MATCH |
| `phase8-tmc-uart-read-observation-r1-board.json` | 14988 | `dbb3c80e3061…` | MATCH |

A 组 **5/5 MATCH**（本轮新增/修改五件，远端字节与本地提交一致）。

自指口径：上表的 `.md`/`.json` 两行取的是**本 §7.7 写入之前**那个头（`0b9dd5d`）的字节；
把本节写进交接本身就会改变这两个 blob，所以最终提交头上这两件在推送后另行复验（见回执），
不在文件里递归登记。三件证据文件不受本节影响，其散列对最终头同样成立。

应不变对象（跨头三点核对）：

- `tmc2209.c` — 与 `66f8533` 上的 blob **SAME**（`8a5e03082245…`）
- `leaf_main.c` — 与 `66f8533` 上的 blob **SAME**（`6a03b884e7b6…`）
- `run_stop_priority_s2.sh` — 与 `66f8533` 上的 blob **SAME**（`0d405b01c886…`）
- `measure-a0.bin` — 与 `66f8533` 上的 blob **SAME**（`a48400c881c5…`）
- `default.bin` — 与 `66f8533` 上的 blob **SAME**（`dccb7b63c928…`）
- `build_manifest.json` — 与 `66f8533` 上的 blob **SAME**（`075bd3ff6eac…`）
- `UARTReadDiagSerialR1.ps1` — 与 `66f8533` 上的 blob **SAME**（`2e9c8ede8f75…`）
- `s0_offline_binding.txt` — 与 `35fc9ac` 上的 blob **SAME**（`7c36aca3ad8a…`）
- `execution_package.md` — 与 `35fc9ac` 上的 blob **SAME**（`8c399e50e5a8…`）
- `commands.expanded.masked.ps1` — 与 `35fc9ac` 上的 blob **SAME**（`699b759a3c41…`）

B 组 **10/10 SAME**：产品 `tmc2209.c`、夹具 `leaf_main.c`、runner `run_stop_priority_s2.sh`、
两个镜像 BIN、`build_manifest.json`、冻结采集器 `UARTReadDiagSerialR1.ps1` 以及 S0 三件全部未动。

冻结引用（与 `s0_offline_binding.txt` 第 18–22 行所记逐项相同）：

```text
remote main                          1b3757eec550802a3973e66790af422d81d3d373
remote phase7/full-embedded-software   6d3fbc559d45673d7550eb73dec5a4ade5bb34df
tag v0.7-phase7-software-board-validated        097935fec5a6f2467261ab1ad449ff0b1d398250
tag deref                                  e40a9b95ef709e6a80444f9f3949dfdd70ba96ba
本轮改动面                           仅 reports/{reviews,agent_handoffs} 下 5 个文件
旧固件目录/1 MiB 快照/恢复 HEX/下载工程  未写（git diff 35fc9ac..HEAD 对 reports/firmware 为空）
```

**本轮勘误（不掩盖，随轮登记）**：`5360a5e` 的交接 §7.3 把 ANSI 前缀名写成裸 `0x1B` 控制字节，
使交付的 `.md` 里真存了一个 ESC 字节（GitHub contents API 因此拒绝直接回显该件）。
`0b9dd5d` 只把该一个字符改为字面 `\x1b` 文本，不动任何事实与计数；因上一提交已推送，故不 amend，
另立勘误提交。三件证据文件经复检均无裸控制字节。


---

## 8. 后续轮指针（2026-10-28，URO-R2）

本轮交付之后, 任务书 `NS800_Sensor_Stack_UART_R2_Fix_Task.md` 要求把"栈告警刷屏"与"就绪门要求全流静默"
合并修复。该离线轮已交付, 结果与本文的关系如下(**本文 §1–§7 的历史文字与停点标签原样保留, 不回写**):

- 产品侧: `applications/sensor_service.c` 的 `SENSOR_THREAD_STACK` 1024 → 4096(软件输入提交
  `139e9377ccbc36b878cd577a6b848d974e1e07fa`); 静态已知最深链 `sensor_thread_entry` = 1228 B, 旧配置装不下它, 这就是本文 243 次告警的量化解释。
- 采集器侧: 新 `UARTReadDiagSerialR2.ps1`(92860 B / `9ed8a3e342f7…`)按任务书附录 A 原样生成, 首查询不再要求 1200 ms
  全流静默, 改为"先落盘再判栈危险, 命中即 `STACK_DANGER_OBSERVED` 停"; 附录 B 六项以**本文这条真实
  23845 B 流**为输入全 PASS。
- 镜像侧: 两枚新 BIN(`measure-a0.bin` 268340 B / `b1e852949f00…`, 期望横幅 `5.3.0 build Oct 28 2026 10:23:50`)已归档; 本文已证的板上旧镜像
  `a48400c8…` 与其归档件未覆盖、未删除。
- 状态侧: 本文的 `CAPTURE=INCOMPLETE / BUSINESS=NOT_REACHED / msh 0/7 / 额度全部用尽`**继续成立**,
  未被后续轮改写; 板上 TMC 链路仍 `UNPROVEN`, `-117` 根因仍未知。
- 下一轮: 见 `reports/reviews/phase8-sensor-stack-uart-r2/board_package_r2.md`(填实的续测包)与
  `reports/agent_handoffs/phase8-sensor-stack-uart-r2.md/.json`; 需要用户对那份新包的一次批准,
  本文的批准不续用。
