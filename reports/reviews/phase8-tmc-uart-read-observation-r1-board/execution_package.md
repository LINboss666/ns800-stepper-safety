# URO-R1 板级只读通信诊断包 —— 已绑定参数的执行单

状态：**S0 已绑定完成 / 待用户一次批准 / 未执行任何设备动作**
（`SOFTWARE_IMAGE_CAPTURE_REVIEW_GO_FOR_BOUNDED_READ_DIAG` → `BOARD_PACKAGE_READY` →
`AWAITING_USER_APPROVAL` → `NO_NEW_HARDWARE_ACTIONS`）

复审对象 = `66f853309fe22376ffa6b45080e5f79c5891d4d1`；软件/镜像输入 code_commit =
`a483dcba74abd8c04549459bcee2cbcdb1ddea4c`；GO 全文与限制来自外部审核方
`NS800_URO_R1_Review_GO_and_Board_Package_66f8533_20261028.md`。
本单是**已推送文档包**（R1 `board_candidate.md`）的执行实例化：§6 的 S4 顺序修正
**取代**其中旧 S6 的"条件复位"，其余口径不变。历史文档不回写，差异在本单与交接里并列说明。

绑定过程的逐项实算证据见同目录 `s0_offline_binding.txt`。

## 1. 变量绑定（代号 → 本地路径；映射表留仓外 `logs\local_path_index.json`）

| 代号 | 内容 | 本轮核对 |
|---|---|---|
| `$Uv4Exe` | `<工具根>\UV4\UV4.exe` | ProductVersion = 5.43.1.0 |
| `$PythonExe` | `<工具根>\Python312\python.exe` | Python 3.12.5；pyOCD `__version__` = 0.45.1（import 级，未连目标） |
| `$TaskRoot` | 本轮全新仓外执行根（旧 BOARD-B / M0 根不复用、不覆盖） | 子目录 `download-r1`/`pyocd-work-r1`/`readback`/`logs` 已建；`capture\r1_run1` **故意不存在** |
| `$DownloadProject` | `$TaskRoot\download-r1\download-b.uvprojx`（整份复制自上一轮成功下载的 BOARD-B 工程） | `.uvprojx`/`.uvoptx` 与源件逐字节相同 ⇒ 工程、已证 NS800 FLM、`-FS08000000`、CMSIS_AGDI 探针驱动均未改 |
| `$PayloadAxf` | `$TaskRoot\download-r1\dl\rt-thread-a0.axf`（副本既有输出位） | 1693804 B / `a96889c47c9433b132dc4eeecf46be287c13dc1cff11fc0003e6cca10826846f`；目标 `rt-thread` 的 `OutputDirectory=.\dl\` + `OutputName=rt-thread-a0` ⇒ UV4 `-f` 的 Load 解析到该文件 |
| `$FrozenCollector` | `reports/reviews/phase8-tmc-uart-read-observation-r1/UARTReadDiagSerialR1.ps1` | 93797 B / `2e9c8ede8f75e7b513dbd4f2c3091189309454879e98404ba7ec64bbb80f2568`；不改它 |
| `$BinAccepted` | `reports/firmware/phase8-tmc-uart-read-observation-r1/measure-a0.bin` | 268340 B / `a48400c881c539db539876cfd38f3aceac2a78cc7ed0d0f9655c7edafbb19a1e` |
| `$Manifest` | 同目录 `build_manifest.json` | 16057 B / `075bd3ff6eace662a8f4809a4b5810eeddd04997a77f590b2bbef0519db5d2e3` |
| `$FlashLog` | `$TaskRoot\logs\s2_flash.log` | 执行时新建 |
| `$ReadbackPath` | `$TaskRoot\readback\r1-measure-a0-268340.bin` | 当前不存在（本轮新文件；不是旧快照、不是从 BIN 复制） |
| `$CaptureOutDir` | `$TaskRoot\capture\r1_run1` | 当前不存在，由采集器自己新建（它要求全新目录） |
| `$ApprovalReference` | 用户批准原文所在（简写「批准执行 URO-R1 板级包」或全文）＋ 实际时刻 | **不得静默假定**；S2/S5 参数化强制要求，缺即 throw |
| `$DapUid` | 从两处既存本地原始件唯一解析（`01_reset_pyocd.txt` 与 `cp4_run.ps1`），存仓外私有件（权限 600） | 长度 48、形状 `^[0-9A-Fa-f]{20,64}$`、两处一致；`sha256(uid)[:12]=d2f530dbd062`；未枚举探针、值不入仓库 |
| `$ReadbackLenHex` | `0x41834` | 与 BIN 长度 268340 B 及 map `COMPRESSED[0x00041834]` 两个口径互证 |
| 期望横幅 | `5.3.0 build Oct 28 2026 02:23:31` | 逐字必填（默认目标戳 02:23:14 不用于本包） |

## 2. 额度与计数口径（批准即按此执行，包内不逐条再确认）

| 动作 | 上限 | 口径 |
|---|---:|---|
| 维持供电；确实无电时常规上电 | 至多 1 次**新增**上电 | 已供电则不循环；物理插接由现场人员做，Agent 不代填"已供电" |
| 既有 Keil 下载流程 | 1 次尝试 | 新副本 AXF；失败也计入；含工具自身正常初始化/复位行为（沿用已证配置，不猜改私有选项） |
| DAP / pyOCD 会话 | 2 次 | #1 = S3 定长回读（halt 且不恢复）；#2 = S4 显式复位启动 |
| Flash 回读 | 1 次 | `0x08000000` 起 `0x41834` B；不重读 1 MiB、不扩大范围、不读第二遍 |
| Agent 显式 `SYSRESETREQ` | 1 次 | 在**开串口之前**执行（S4） |
| COM5 打开尝试 | 1 次 | 只由冻结采集器打开；失败也消耗；不枚举、不换端口 |
| msh 命令 | 7 条，各最多 1 次 | 固定顺序见 §4；probe 最多 1 次 ⇒ 至多三笔寄存器读、0 个 WRITE 帧 |

复位口径：显式 `SYSRESETREQ` 1 次 **不等于**芯片物理上总共只复位一次。Keil 既有内部初始化/
复位含在那一次下载内；能逐次观测就实记，不能逐次观测记 `NOT_SEPARATELY_OBSERVABLE`，
不虚报物理复位总数。"只读"指本包主动 TMC 诊断命令不写寄存器，不表示固件启动过程完全不写
GPIO/存储；三笔读上限指 msh probe，不含既有启动读事务。

## 3. S1 → S4（下载 → 回读 → 开串口前唯一复位）

```powershell
# S1 供电：保持现场当前正常供电；确实无电时由操作者常规上电一次(USB 板载 LDO 逻辑电源 → 既有 24 V)。
#    不安排例行断电循环；电机未接、SW1 阻断、模块线路不变；不作 ENN 对照实验。

# S2 一次下载（UV4 退出码不单独作判据）
& $Uv4Exe -f $DownloadProject -t rt-thread -j0 -o $FlashLog
```

S2 通过判据（全部要满足）：日志出现实际 `Load "<$PayloadAxf>"`、`Erase Done.`、`Programming Done.`、
`Verify OK.`，且无真实失败；无 mass erase / unlock / 选项字 / OTP / 系统区操作。失败即停，不下载第二次。

```powershell
# S3 pyOCD 会话 #1：唯一定长回读（在 $TaskRoot\pyocd-work-r1 内调用，stdout/stderr 分开落盘）
$DapUid = (Select-String -Path $UidPrivate -Pattern '^dap_uid=([0-9A-Fa-f]{20,64})$').Matches[0].Groups[1].Value
$ReadbackForward = $ReadbackPath.Replace('\', '/')     # 已证坑：-c 串里的反斜杠会被当转义吞掉
& $PythonExe -m pyocd commander --no-config `
  -t cortex_m -u $DapUid -f 1000000 `
  -O auto_unlock=false -O connect_mode=halt -O resume_on_disconnect=false `
  -O cache.enable_memory=false -O cache.read_code_from_elf=false -O scan_all_aps=false `
  -c 'status' -c ('savemem 0x08000000 0x41834 ' + $ReadbackForward) -c 'status' -c 'exit'
```

S3 通过判据：两次 `status` 都是 `Halted`；`savemem` 无真实失败；文件存在、长度 268340 B、
SHA-256 = `a48400c8…` 全文，且与 `$BinAccepted` 逐字节 `Compare-Object` 无差异。pyOCD rc=0 不能
代替这些判据。**只有全部满足才允许给采集器 `-ImageContinuityConfirmed`。**

```powershell
# S4 pyOCD 会话 #2：开串口前唯一一次显式复位启动（取代旧 S6 条件复位）
& $PythonExe -m pyocd commander --no-config `
  -t cortex_m -u $DapUid -f 1000000 `
  -O auto_unlock=false -O connect_mode=attach -O resume_on_disconnect=false `
  -O cache.enable_memory=false -O cache.read_code_from_elf=false -O scan_all_aps=false `
  -c 'reset sysresetreq' -c 'status' -c 'exit'
```

S4 通过判据：原始输出实证 `Resetting target` 与 `Core 0 (None):  Running`，无 error/traceback/failed。
失败不追加 `resume`/`reset`、不建第三次 DAP 会话。**为什么必须在这里：** S3 留下 Halted，而冻结
采集器会自行从被动等待直接进入首条查询，无法在其内部按旧表插入复位 ⇒ 必须先复位启动，再启动采集器。
之后可能漏掉启动横幅 —— 按"已验证镜像 + 未观察横幅"路径继续，不为补横幅再复位。

## 4. S5 → S6（一次 COM5 与七条命令，全部由冻结采集器执行）

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File $FrozenCollector `
  -ExecuteApprovedR1ReadDiag `
  -OutDir $CaptureOutDir `
  -ApprovalSource $ApprovalReference `
  -ExpectedBuildBanner '5.3.0 build Oct 28 2026 02:23:31' `
  -ImageContinuityConfirmed `
  -SiteConfirmed
```

- 不先用串口助手/其它程序试开 COM5；唯一一次打开由脚本完成（115200/8N1、无流控、DTR/RTS=false）。
- 脚本自身：被动收 ≥2 s、要求 1.2 s 静默、最多 20 s；横幅出现则必须逐字匹配且不得互相矛盾；
  无横幅且 S3 已过 ⇒ 记 `BANNER_NOT_OBSERVED` 继续；无身份依据或真实异常 ⇒ 停。
- **第一条查询前不要求自发的行尾 `msh >`**；首条 `spm status` 即那唯一一次主动查询。每条响应仍须
  满足脚本的提示符、静止状态与完整性判据，静默本身不算响应完成。
- 七条由脚本依序各发一次，Agent 不另发：

```text
spm status
spm cap
safety_state
safety_status
motor_status
step_status
tmc_uart_probe
```

- 前六条按脚本固定条件检查静止/四宏状态；最后一条至多三笔读、0 WRITE 帧。probe 业务失败也
  **不**重试、**不**换地址/波特率/期限、**不**加 ENN 实验、**不**清故障、**不**补发 `status`/`probe`。
- 失败由脚本原 `finally` 关闭句柄；不附加任何清理命令。

不授权清单（本包内一律不做）：电机连接/运动、STEP、DIR/许可 HIGH、翻宏、arm/start/ramp、
清故障、配置写、手动 selftest、`pin_status`、额外 A0 采样/清理、自动恢复、外部 SPI Flash 转储、
全片备份、设备/端口/探针枚举、`sw1_cut.ps1`。

## 5. S7 停点、归档与结论分列

任一步真实失败、前置失效或额度耗尽 ⇒ 立即结束后续设备动作：保存已得原字节、关闭现有句柄、
记录实际消耗。不追加复位/probe/下载/回读/清故障/恢复，不用旧包额度续跑。结束不索取例行断电
回报，不为"结束状态"另开 COM5/DAP；不可观察即记 `UNKNOWN` / `NOT_REPORTED`。保留当前固件，
不自动回刷。

| 层 | 登记要求 |
|---|---|
| 镜像/执行 | 新 AXF 实际 Load、新 BIN 回读逐字节相等、复位 `Running`、实际额度；单有下载日志不够 |
| capture | 原始帧/计数/样本/行界完整才 `COMPLETE`；业务失败**不是**证据不完整 |
| business | `SUCCESS`/`FAIL` + 阶段 `OPEN/TXN/READ/IDENTITY_MISMATCH/FIELDS_OUT_OF_DOMAIN` + 原始 rc/reason/期限/收发计数 |
| 根因 | 只按实际板上字节与阶段给证明/排除/未知；**不使用**失真的 `echo_match_off` |

`COMPLETE + FAIL` = 拿到有效失败证据，业务仍是 FAIL，不写成通信 PASS。三读成功、VERSION=0x21、
完整 32 位字段合规 ⇒ 只证明这一次读取/身份检查，不证明全负载长期稳定；`safe_for_enable` 必须仍为 0。

分析最低集合：`ctx_ok`、txn/open 三态、cfg/xfer 是否到达、flush/TX/RX 与 `tx_written`/`rx_total`/
flush 字节、原始 rc/reason、总/阶段期限与 `exp=n/y/-`、逐笔 `val=` 与完整 `raw=`、一致性、样本覆盖
范围。`NOT_REACHED` 不得当成该阶段实测 0。`-117` 单独不证明芯片无应答（完整合法帧也可能被期限
拒绝）；`Motor health=OK` 不证明 TMC 链路健康；`Motor state=4` 是 FAULT；GSTAT/IFCNT 看全 32 位。
本包不执行 A0 样本批次，不从本轮日志关闭物理时延风险。

归档纪律：原始字节先落盘再解码；base64 归档逐块核长度/SHA，且 session 与分响应拼接必须相等；
含 UID/绝对路径的原件仓外保留，脱敏副本标注 `lossless=false` 与替换范围；**不**把转码文本称作
原始串口字节。来源分层照实写：云端实跑 / 本地实跑 / `USER_CONFIRMED`；失败不由补跑覆盖。

结束时按实际填写（现在全部待定，不预先填值）：

```text
URO_R1_BOARD_READ_DIAG_EXECUTED_OR_STOPPED
CAPTURE = COMPLETE / INCOMPLETE / NOT_REACHED
BUSINESS = SUCCESS / FAIL / NOT_REACHED
FAILURE_STAGE = <真实值或 NOT_REACHED>
ECHO_MATCH_OFFSET = KNOWN_INVALID_FOR_DIAG
SAFE_FOR_ENABLE = false
NO MOTOR / NO STEP_ENABLE / FOUR SAFETY MACROS=0
```

`BLOCKED_ROLLBACK` 剩余项按原范围保留：本地 1 MiB 快照 `b89f8a7c…` 与恢复 HEX `b3ac97b9…` 已保存，
恢复下载仍未演练；本包不新增备份、不自动恢复。板上 TMC 状态在取得新实证前仍 `UNPROVEN`。

## 6. 待批准

> 批准执行本文件 URO-R1 板级包：使用 66f8533 的 `a48400c8…` 测量镜像及 `2e9c8ede…` 冻结采集器；
> 现场有人，电机未接、SW1 阻断、模块线路不变。允许维持供电，无电时常规上电一次；一次既有 Keil
> 下载、一次 268340 B 回读、两个 DAP 会话，其中回读后开串口前唯一一次显式 `SYSRESETREQ`；
> 一次 COM5 打开，按序七条查询含一次 probe（三笔读零 WRITE 帧）。包内不逐条确认；真实失败、
> 前置失效或额度用尽即停，不重试、不增动作。

简写：**批准执行 URO-R1 板级包**。需要这次批准，是因为新镜像下载与新的 DAP/COM5 会话不在旧 M0
批准范围内；旧 M0/BOARD-B 的额度与"包外重试"指令不续用到本包。
