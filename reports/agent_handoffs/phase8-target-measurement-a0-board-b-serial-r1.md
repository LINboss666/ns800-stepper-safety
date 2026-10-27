# NS800 A0 BOARD-B-SERIAL-R1 交接 —— CP0 离线自检即停（未触碰任何设备）

轮次：phase8-target-measurement-a0-board-b-serial-r1
日期：2026-10-27（Asia/Shanghai）
任务书：NS800_Board_B_Review_and_Serial_Continuation_R1_4bb8e9f_20261027.md
document_base：4bb8e9f50c534fe73d51235f5f7746e17a16ae5f（本地 HEAD = 远端头 = 该 SHA；相对它本轮未新增前置提交，`git diff` 为空）
结论：**A0_BOARD_B_SERIAL_R1_CP0_STOPPED / BOOT_GATE_SELFTEST_FAILURE_REPRODUCED_LOCALLY**

## 1. 一句话

新采集器 `A0BoardBSerialR1.ps1` 的**离线自检**在本机失败（rc=1），失败点是我能稳定复现的附录缺陷；
按任务书 §4 末段与 §6.5/§6.6，这条硬前置不过就不进 CP1 —— 所以本轮**没有任何设备动作**，
§3 续测表的每一项上限消耗都是 0。上一轮 BOARD-B 的断电终态仍 `UNKNOWN`（操作者未回报）。

## 2. 通过的部分（都是实读，不是叙述）

| 项 | 结果 |
|---|---|
| 基线 | 分支 `phase8/tmc-drive-config`，本地 HEAD = `ls-remote` 远端头 = 锚点 4bb8e9f…；工作树 `git status --porcelain` 空 |
| 四宏 | `motor.h:69/:83`、`tmc2209.h:100`、`project_board.h:51` 逐字仍 `RT_FALSE`（在 BSP 的 `applications/`，非 `include/`） |
| 冻结门 | `git cat-file blob b187cdd` → 50246 B / `94e83175…`，与 §5 一致 |
| 镜像连续性 | `measure-a0.bin` 261452 B / `5f30d3f0…` 相符；`default.bin` 251148 B / `02e98458…` 相符（只核身份） |
| 旧件保护 | `A0BoardB.ps1` 19432 B / `521d758b…` 与上一轮 CP3 证据字节均未变、未覆盖 |
| 冻结引用 | main / phase7 / tag object / deref 四个 SHA 全部未动 |
| 附录提取 | 唯一标记 `BEGIN_SERIAL_R1_SOURCE` → **21413 B / `6faa1afb…`** 相符，ASCII/LF/CR=0 |
| 启动夹具 | 从被审 SHA 的 wire 归档解出 **3757 B / `97b37f32…`**，并与上一轮真实落盘原件逐字节相等 |
| 语法检查 | `Parser::ParseFile` → `PARSE_ERRORS=0`（tokens=4083），主机 Windows PowerShell 5.1.26100.5074 |

## 3. 失败：§6.5 逐条机器判定

调用（参数语义照 §6.5，路径为本地工作副本）：

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File WORK_OUT_R1/A0BoardBSerialR1.ps1 -SelfTest \
  -PythonExe <PY_ROOT/python.exe> -GateFile WORK_OUT_R1/freeze/a0_export_gate_R1.py \
  -BootFixture WORK_OUT_R1/fixture/002_boot.rx.bin -OutDir WORK_OUT_R1/selftest_run1
```

`rc = 1`，stdout 0 B，stderr 354 B（**按原始字节另存**，严格 GBK 解码成功 —— 这正是上一轮 `CP3-F2` 的修正）。

| §6.5 判据 | 结果 |
|---|---|
| `rc == 0` | FAIL |
| `stdout 有 BOARD-B-SERIAL-R1-OFFLINE-SELFTEST PASS; device_actions=0` | FAIL |
| `stderr 为空(无抛错)` | FAIL |
| `OFFLINE_SELFTEST 行存在且 device_actions=0 / real_wire_calls=0 / mock_read_probes=3 / mock_forbidden_calls=0 / boot_fixture_bytes=3757` | FAIL |
| `READ_ONLY_PROBE_READY 存在且 startup_prompt_required=false` | PASS |
| `3757 B 真实启动重放落盘且 SHA == 97b37f32…` | PASS |
| `MOCK_TX_ATTEMPT 恰 3 条` | FAIL |
| `无 TX_ATTEMPT(真发命令的账)` | PASS |
| `无 SERIAL_OPEN` | PASS |
| `无 RESET_ATTEMPT(脚本已删该路径)` | PASS |
| `无 STOP_FAILURE` | FAIL |
| `离线门正控 self_positive allowed=True` | PASS |
| `离线门负控 self_negative allowed=False 且 rc=1(NUL 仍立即拒绝)` | PASS |
| `源码里不存在 DAP/pyOCD/复位执行路径` | PASS |

台账 11 行：{"RX_BEGIN": 3, "RX_FILE": 3, "GATE": 2, "READ_ONLY_PROBE_READY": 1, "MOCK_TX_ATTEMPT": 1, "STOP_FAILURE": 1}

## 4. 根因（附录缺陷，已独立最小复现，未改附录一行）

PowerShell 变量名**大小写不敏感**。自检分支在脚本作用域把返回值赋给 `$reply`：

```text
A0BoardBSerialR1.ps1:236  $reply=Cmd 'spm status' 1000; Idle-Status $reply
A0BoardBSerialR1.ps1:240  try {$reply=Cmd 'spm status' 250} catch {
A0BoardBSerialR1.ps1:246  $reply=Cmd 'spm status' 1000; $badStatusSeen=$false
```

`$reply` 与内部状态 `$script:Reply` 是同一个变量 ⇒ 第一支查询把 `End-Reply` 返回的
`PSCustomObject` 顶替了那个 `MemoryStream`；第二支 `Cmd` 的 `Begin-Reply` 见非空即抛
`Previous reply still open.`（台账 `STOP_FAILURE` 原文），`finally` 再对它 `.Dispose()`
又报方法不存在（stderr 原文）：

> WORK_OUT_R1/A0BoardBSerialR1.ps1 : 方法调用失败，因为 [System.Management.Au tomation.PSCustomObject] 不包含名为“Dispose”的方法。 + CategoryInfo : InvalidOperation: (Dispose:String) [A0BoardBSerialR1.ps1]，RuntimeException + FullyQualifiedErrorId : MethodNotFound,A0BoardBSerialR1.ps1
>
> （原文按控制台 80 列折行, 故有 "Au tomation" 这类断词; 反斜杠按呈现层写成正斜杠; 原始字节见 cp0_selftest_artifacts.md 的有损条目说明与本地 .bin）

我自己写的机制复现（独立小脚本，不是附录文件）：`STEP2/3/6` 显示同一变量被返回值顶替，
`STEP4` 得到逐字相同的 `Previous reply still open.`，`STEP5` 得到逐字相同的 Dispose 方法错误。

**影响面**：三处赋值都在自检分支，联机分支不给 `reply` 赋值，`[IO.Ports.SerialPort]` 只在联机分支
与 `finally` 出现 ⇒ 该碰撞阻断的是**离线自检**，我不据此断言联机路径也有此错；
但 §4 末段/§6.5 把自检通过定成硬前置，所以本包无法进入 CP1。

## 5. 已跑到 vs 没跑到的检查（不把没跑到的说成可用）

已通过：原始字节分块落盘一致性；假串口拒绝 `spm clear_records`/`spm start trip_fast`/`pin 1 1`（wire=0）；
离线门正控 `self_positive` rc=0 allowed=True 与负控 `self_negative` rc=1 allowed=False（NUL 仍拒）；
`Check-Decision` 的非零 rc / 旧批 JSON / 缺失 JSON / 缺 clear grant 四项负控；
3757 B 真机启动重放在**无自发提示符**下判出 `READ_ONLY_PROBE_READY`
（`boot_seen=true, boot_done=true, startup_prompt_required=false`）且落盘 SHA 与原件相同；
第一支只读查询收到状态与提示符、`Idle-Status` 通过。

未执行到（状态 = `NOT_EXERCISED`）：**"响应缺提示符必须超时"**那一例（第二支 `Cmd` 在 `Begin-Reply` 即抛，
根本没走到 `Wait-Prompt`）、**"状态污染必须拒绝"**那一例、以及 §6.5 要求的最终汇总
`mock_read_probes=3 / mock_forbidden_calls=0 / real_wire_calls=0`（`OFFLINE_SELFTEST` 行不存在）。

## 6. 本轮实际做过的事 / 没做过的事

做过：读 AGENTS.md；基线与远端只读核对；不变项实读；附录与夹具按字节提取；`ParseFile`；一次 `-SelfTest`
（纯假串口，无设备句柄）；一个机制复现小脚本；证据与判读。

没做：创建串口对象、pyOCD、复位、下载、编译、读 Flash、枚举设备、任何目标命令、清 RAM、
改附录核心逻辑、放宽任何判据、翻四宏、跑 `sw1_cut.ps1`、动冻结引用与旧证据。

## 7. 继续所需（都在我权限之外）

1. 云端修附录：把自检分支那三处 `$reply` 改成不撞内部状态的名字即可（判据形状不用动），
   重新给出新采集器的长度 + SHA。我不本地改名跑一次来"证明可用"——那正是 §6.6 禁止的。
2. 用户发出 §7 的仅串口续测批准文字（本轮未收到）。
3. 上一轮断电终态回报（当前 `UNKNOWN`）；现场确认无另行烧录/改线、SW1 仍阻断、电机未接。

## 8. 证据

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| [A0BoardBSerialR1.ps1](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b-serial-r1/A0BoardBSerialR1.ps1) | 21413 | 6faa1afb1b603833e02b4c2afc4f8e34a49f165ada7da476775ab23338f56ad8 |
| [cp0_offline_selftest_stop.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b-serial-r1/cp0_offline_selftest_stop.txt) | 15537 | 94ad054197b55a65d175def0dfa17d8797727c8782ddddb31d9badd5ecd796fa |
| [cp0_selftest_artifacts.md](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b-serial-r1/cp0_selftest_artifacts.md) | 7589 | 2cf657179dc9bc81669197de2af7b9376d92bda142f3a810e732e7d375ea1e03 |

归档条目（逐块 base64，全部通过隐私扫描）：`001_offline_bytes.rx.bin`、`003_spm_status.rx.bin`、
`ledger.jsonl`、`r1_reply_var_repro.stdout.bin`；`selftest.stderr.bin` 含本机绝对路径，
**只上脱敏文本并声明有损**，原件留本地。`session.rx.bin` 声明为 001+002+003 拼接，
其中 002 块与上一轮 `cp3_wire_archive.md` 的 `002_boot.rx.bin` 逐字节相同，不重复上传。

## 9. STOP

```text
A0_BOARD_B_SERIAL_R1_CP0_STOPPED / AWAITING_FIXED_COLLECTOR_AND_SERIAL_R1_APPROVAL
HARDWARE_ACTIONS_THIS_ROUND = NONE / LIMITS_USED ALL ZERO / NO_SERIAL_OBJECT_CREATED
COLLECTOR_APPENDIX_BYTES_UNMODIFIED 21413 / 6faa1afb1b603833e02b4c2afc4f8e34a49f165ada7da476775ab23338f56ad8
SELFTEST rc=1 / PARSE_ERRORS=0 / BOOT_REPLAY_AND_FIRST_PROBE_OK / TWO_NEGATIVE_CONTROLS_NOT_EXERCISED
BOARD_STILL_RUNNING_5f30d3f0 / BOARD_B_POWER_STATE UNKNOWN (awaiting operator report)
NO MOTOR / NO STEP_ENABLE / FOUR SAFETY MACROS RT_FALSE / NO PROGRAM / NO FLASH READ / NO DAP / NO ACTIVE RESET
NO BUILD / NO DEVICE ENUMERATION / NO sw1_cut.ps1 / NO_RETRY_NO_BACKFILL
```

## 10. R1a 补正（追加层，§1–§9 的 CP0 失败历史原样保留）

任务书 `NS800_Serial_R1a_Collector_Erratum_and_Executed_Verification_004a265_20261027.md` 认下 §4 的
根因归它自己写的自检代码，并给出确定性改名生成器与修正版身份。本轮按 §5 只做离线补正验证：

| 项 | 实际结果 |
|---|---|
| 生成方式 | 逐字使用 §4 的 `make_r1a.py`（内容未改，脚本文件放仓外，以免本轮多出非 reports 路径；运行目录=仓库根） |
| 新身份 | `A0BoardBSerialR1a.ps1` **21453 B / `de70c7a4…`** = §4 登记值；ASCII/LF，CR=0 |
| 差异范围 | 行级对齐只改 **4 行、5 个 token**（`$reply`→`$probeResponse`），全部在自检分支（联机分支首行 255 之后逐字相同）；`$script:Reply` 11 处与形参 `Idle-Status($Reply)` 2 处按 §2 保留 |
| 命名碰撞审计 | 顶层 `$reply` 赋值 0 处；显式 `$script:` 状态名 **15 个**（与云端 §3 同行口径一致）；`ParseFile` errors=0 / tokens=4083 |
| Windows 5.1 实跑 | `powershell.exe -SelfTest`（同一冻结门 `94e83175…` 与同一 3757 B 夹具 `97b37f32…`）：**rc=0**，stdout 逐字 = 原 PASS 行，**stderr 0 B**，3.6 s |
| 硬判据 | §5.4 全部 **21/21 PASS**（含 `OFFLINE_SELFTEST` = mock_read_probes 3 / mock_forbidden_calls 0 / real_wire_calls 0 / device_actions 0 / boot_fixture_bytes 3757；MOCK_TX_ATTEMPT=3；RX_FILE=5；3757 B 重放 SHA 与原件相同） |
| 原 NOT_EXERCISED 的两条负控 | **这次真被执行到**：缺提示符超时例响应 132 B 落盘并命中 `Reply timeout; no retry and no cleanup.`；状态污染例 142 B 落盘并命中 `Idle status malformed or not idle.`（脚本内 `-cne` 精确比较，rc=0 才算两条都命中） |
| 与云端对照 | 事件计数逐项相同（RX_BEGIN 5 / RX_FILE 5 / GATE 2 / READ_ONLY_PROBE_READY 1 / MOCK_TX_ATTEMPT 3 / OFFLINE_SELFTEST 1）；本机是 5.1、云端是 Core 7.4.13，两条独立记录不互替 |
| 真设备事件 | `SERIAL_OPEN`/`TX_ATTEMPT`/`RESET_ATTEMPT`/`STOP_FAILURE` 全为 0；`hardware_actions=[]`，`builds_executed=[]` |
| 本轮首失败 `R1A-F1` | 我第一次实跑（run index 1）PowerShell 侧其实已全绿，但我的判读脚本取键名出错抛 KeyError 没出报告 ⇒ 产物保留不删，按 §5.4 另起全新目录跑 run2 出正式判定；两次 stdout 的 SHA 相同（`64bc1568…`），属可重复性而非挑一次好看的 |
| 变绿手段检查 | 未删负控、未加宽松异常兜底、未改 `finally`；旧 R1 脚本/台账/base64/stdout/stderr 一律未动 |
| 是否继续 CP1 | **没有**。§5.7 要求"Windows 自检通过 + 原仅串口续测批准 + 断电 + 现场前置"三者齐备；本轮只有第一项成立 ⇒ 停在 CP0，不接触设备，也不代填批准或断电回报 |

证据：[r1a_windows_selftest_verification.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b-serial-r1/r1a_windows_selftest_verification.txt)（11098 B / `de14170e0f6ed158a53cc0aa8db07937759df89e5d9af8ad02baf1712e8b2b8e`）、[r1a_selftest_artifacts.md](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b-serial-r1/r1a_selftest_artifacts.md)（6324 B / `938f492658bbe9ef143e9282395ae0cc0e905adcde635001dafa817d40db15a7`）、[A0BoardBSerialR1a.ps1](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b-serial-r1/A0BoardBSerialR1a.ps1)（21453 B / `de70c7a48280550edb5b5c41d9c5e385e5c79b0b5d4ef5dd901473400b31bd7c`）。

## 11. STOP（R1a 之后）

```text
A0_BOARD_B_SERIAL_R1a_COLLECTOR_VERIFIED_ON_WINDOWS / CP1_NOT_ENTERED_AWAITING_APPROVAL_AND_POWER_REPORT
COLLECTOR_R1A 21453 B de70c7a48280550edb5b5c41d9c5e385e5c79b0b5d4ef5dd901473400b31bd7c (差异=4行/5token, 只在自检分支)
WINDOWS PowerShell 5.1 -SelfTest rc=0 / PASS LINE / stderr=0 B / HARD CRITERIA 21/21 PASS / MOCK_TX=3 RX_FILE=5
TWO PREVIOUSLY NOT_EXERCISED NEGATIVE CONTROLS NOW EXECUTED (132 B Reply timeout / 142 B Idle status malformed)
REAL DEVICE EVENTS THIS ROUND = 0 (NO SERIAL_OPEN / NO TX_ATTEMPT / NO RESET_ATTEMPT / NO STOP_FAILURE)
HARDWARE_ACTIONS=[] BUILDS=[] / FOUR SAFETY MACROS RT_FALSE / FIRMWARE AND GATE AND IMAGE UNCHANGED
BOARD_B_LAST_PROVEN_IMAGE 5f30d3f0… / BOARD_B_POWER_STATE=UNKNOWN (awaiting operator report; 不写成"仍在运行")
ORIGINAL CP0 FAILURE AND ALL ITS ARTIFACTS PRESERVED (旧 R1 脚本 6faa1afb… 未覆盖, 台账未改写)
NO MOTOR / NO STEP_ENABLE / NO PROGRAM / NO FLASH READ / NO DAP / NO ACTIVE RESET / NO DEVICE ENUMERATION / NO sw1_cut.ps1
```

需要你做两件事才能继续（我不会替你填）：① 让现场回报上一轮断电终态（先关 24 V 再拔 USB，SW1 保持阻断）；
② 发出 §7 那句批准（"已断电，批准仅串口续测R1…"，按 §5.7 不因文件名 R1a 再索要同范围许可）。
