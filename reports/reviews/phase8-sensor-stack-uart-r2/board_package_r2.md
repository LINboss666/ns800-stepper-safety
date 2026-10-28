# NS800 URO-R2 续测包（sensor 栈修复后的板级只读诊断）

日期：2026-10-28（北京时间）
软件输入提交：`139e9377ccbc36b878cd577a6b848d974e1e07fa`（产品补丁，镜像按该字节构建）；本文随本轮证据提交一起入库。
分支：`phase8/tmc-drive-config`
状态：**BOARD_PACKAGE_FILLED / AWAITING_USER_APPROVAL / NO_HARDWARE_ACTIONS_THIS_ROUND**

本文只填实，不执行。本轮 `hardware_actions=[]`：未下载、未读 Flash、未建 DAP 会话、未开 COM5、
未发目标命令、未枚举、未碰供电/接线/ENN/SW1。

## 1. 固定产物身份（本轮真实新值，禁止复用上一轮散列）

| 对象 | 字节数 | SHA-256 |
|---|---:|---|
| **唯一待下载 BIN：measure-a0.bin** | **268340** | **`b1e852949f003965de52d539ca324a11b8b89e17a51a8f34fe3efd01525ae2d1`** |
| 对照 default.bin（不下载） | 258036 | `db5db20bb8d6eae1e8e5bed9dd0b70f5ad5c818a6cb3db9333496e0280aebe2d` |
| 构建 manifest | 24583 | `9c3b492a4f7487b850becaa182cea7214ff84f495af621c56d34a9f1f645194e` |
| **R2 采集器** `UARTReadDiagSerialR2.ps1` | 92860 | `9ed8a3e342f7cad8430c42140ffb508d6cc0694e251636c1b93ab2a2a7390a37` |
| 本地 AXF（A0，不入库，执行前本地直算） | 1693804 | `15be5f3efc45bf296bc0ef24e3947081372616d819e4bb3b189e9614cf9a5b9a` |

目标：`rt-thread-measure-a0`。采集器期望启动横幅必须逐字为：

```text
5.3.0 build Oct 28 2026 10:23:50
```

定长回读范围：`0x08000000` 起 `0x00041834` = 268340 B（与接受 BIN 实际长度两个口径相符；不用 Code+RO 代替）。
Program Size（本轮实测）：`Code=187018 RO-data=80746 RW-data=2188 ZI-data=91504`；构建汇总 `0 Error(s), 0 Warning(s)`；四宏默认与 A0 均为 0。
产品输入：`applications/sensor_service.c` blob SHA-256 `198ac05e2ab8a2df23189c2476acc5a139d2c6c3c805dcd5e7d3b2fa392c2d0e`（SENSOR_THREAD_STACK=4096，动态堆增量 3072 B）。

## 2. 与上一轮（URO-R1 板级包）的差异

| 项 | R1 板级包 | 本包 R2 |
|---|---|---|
| 采集器 | `2e9c8ede…`（93797 B） | `9ed8a3e342f7…`（92860 B） |
| 首查询前置 | 需连续 1200 ms 全流静默 | **不要求静默**；仍要求已见 BOOT 时等到 `[MAIN] boot complete`，20 s 上限 |
| 栈危险 | 无判据 | 保存字节后检查，命中即 `STACK_DANGER_OBSERVED` 并停，不为发命令而 ACCEPT |
| Drain-Rx | 无上限（生产者不停则不返回） | 单次最多 16384 B 后交回外层计时循环 |
| 镜像 | `a48400c8…` | `b1e852949f00…`（SENSOR_THREAD_STACK 4096） |
| 期望横幅 | `5.3.0 build Oct 28 2026 02:23:31` | `5.3.0 build Oct 28 2026 10:23:50` |
| 下载判据 | 日志四项 | 同，另：私有 runner **必须先等 UV4 进程退出**再读日志（上一轮判定过早的缺陷已离线修） |

上一轮已证事实继续有效：板上原 `a48400c8…` 镜像逐字节回读相等、唯一显式复位得 `Running`；
上一轮的停点（`CAPTURE=INCOMPLETE / BUSINESS=NOT_REACHED / 0-7 TX`）保留，不被本轮改写。

## 3. 动作上限（与上一包同量级，不自动续额）

| 动作 | 上限 | 口径 |
|---|---:|---|
| 维持正常供电；无电时常规上电 | 至多 1 次新增上电 | 已供电则不循环；物理插接由现场人员做 |
| 既有 Keil 下载流程 | 1 次尝试 | 新 A0 AXF；失败也计入；含工具自身正常初始化行为 |
| DAP/pyOCD 会话 | 2 次 | #1 回读；#2 显式复位启动 |
| Flash 回读 | 1 次 | `0x08000000` 起 0x00041834 B，不重读 1 MiB |
| Agent 显式 SYSRESETREQ | 1 次 | 在开串口前执行 |
| COM5 打开尝试 | 1 次 | 仅冻结的 R2 采集器打开；失败也消耗 |
| msh 命令 | 7 条，各最多 1 次 | 固定顺序，见 §5 |
| 其中 probe | 最多 1 次 | IOIN/GSTAT/IFCNT 最多三笔读、0 WRITE 帧 |

不授权：电机连接/运动、STEP、DIR/许可 HIGH、翻宏、arm/start/ramp、清故障、配置写、手动 selftest、
pin_status、额外 A0 采样/清理、自动恢复、外部 SPI Flash 转储、全片备份、设备/端口/探针枚举、`sw1_cut.ps1`。
现场沿用：有人、电机物理未接、SW1 阻断、模块线路不变（本包批准即确认，不逐条重索取）。
ENN 不作为本轮查因方向；供电/接线/扩展板已由用户确认（USER_CONFIRMED），不重索取照片或断电回报。

## 4. 步骤（S0 绑定 → S1 供电 → S2 下载 → S3 回读 → S4 复位 → S5/S6 采集 → S7 停点）

S0（离线，零设备动作）：核本地/远端 HEAD 与工作树干净；核 §1 全部长度/SHA（AXF 必须本地直算）；
把已证 BOARD-B 下载工程整份复制到**全新仓外执行根**，只在副本既有输出位放入本包固定 AXF；
pyOCD 用新独立工作目录，每次显式 `--no-config`；R2 采集器散列不符就停，不临时改它；
DAP UID 只从既存私有件取，不枚举、不公开。

S2 下载（等进程退出再判）：

```powershell
$p = Start-Process -FilePath $Uv4Exe -ArgumentList @('-f', $DownloadProject, '-t', 'rt-thread',
                              '-j0', '-o', $FlashLog) -Wait -PassThru
$log = Get-Content -Raw $FlashLog   # 进程退出后才有完整日志
foreach ($k in @('Load "' + $PayloadAxf.Replace('\','\\'), 'Erase Done.',
                  'Programming Done.', 'Verify OK.')) {
  if ($log -notlike ('*' + $k + '*')) { throw ('下载日志缺少判据: ' + $k) }
}
if ($log -match '(mass\s*erase|unlock|option\s*bytes|OTP)') { throw '出现越界操作字样, 立即停' }
```

S3 会话 #1 定长回读（与上一包同参数，范围换成 `0x00041834`，输出为**本轮新文件**）：

```powershell
$ReadbackForward = $ReadbackPath.Replace('\', '/')
$ReadArgs = @('-m','pyocd','commander','--no-config','-t','cortex_m','-u',$DapUid,'-f','1000000',
  '-O','auto_unlock=false','-O','connect_mode=halt','-O','resume_on_disconnect=false',
  '-O','cache.enable_memory=false','-O','cache.read_code_from_elf=false','-O','scan_all_aps=false',
  '-c','status','-c',('savemem 0x08000000 0x00041834 ' + $ReadbackForward),'-c','status','-c','exit')
& $PythonExe @ReadArgs
```

通过判据：两次 `Halted`、`savemem` 无真实失败、文件存在、长度 268340 B、SHA 为 `b1e852949f003965de52d539ca324a11b8b89e17a51a8f34fe3efd01525ae2d1`，
且与接受 BIN 逐字节相等。pyOCD rc 不单独作判据；不重读第二遍、不扩大范围。

S4 会话 #2（开串口前唯一显式复位，顺序同上一包）：`connect_mode=attach` + `reset sysresetreq`，
原始输出必须实证 `Resetting target` 与 `Core 0 (None):  Running`；失败不追加 resume/reset、不建第三次会话。

S5/S6 采集器一次 COM5 与七条命令：

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File $FrozenCollectorR2 `
  -ExecuteApprovedR1ReadDiag `
  -OutDir $CaptureOutDir `
  -ApprovalSource $ApprovalReference `
  -ExpectedBuildBanner '5.3.0 build Oct 28 2026 10:23:50' `
  -ImageContinuityConfirmed `
  -SiteConfirmed
```

`-ExecuteApprovedR1ReadDiag` 只是保留的 CLI 参数名，**不代表继承上一轮的批准**；本包的授权来自用户对
本包的一次批准。`-ImageContinuityConfirmed` 只能来自本轮 S3 的新回读。

七条固定顺序（由脚本发出，Agent 不另发）：

```text
spm status
spm cap
safety_state
safety_status
motor_status
step_status
tmc_uart_probe
```

## 5. 分列登记（本包必须分开回答的四件事）

| 层 | 登记内容 | 判据 |
|---|---|---|
| 栈告警 | `SENSOR_STACK_WARNING_OBSERVED` / `SENSOR_STACK_WARNING_ABSENT` | 采集器保存的原始字节里是否仍有 `sensor stack is close`；命中则 R2 会停在 `STACK_DANGER_OBSERVED` 且不发命令 |
| 命令发出 | 实际 TX 事件数（0..7）与逐条命令 | 台账 `TX_ATTEMPT` 与 `counts` |
| 证据完整性 | `COMPLETE` / `INCOMPLETE` + 缺哪项 | 帧界、tx_written/rx_total、样本、BEGIN/END 计数 |
| 业务 | `SUCCESS` / `FAIL` + 阶段 + 原始 rc | OPEN/TXN/READ/IDENTITY_MISMATCH/FIELDS_OUT_OF_DOMAIN |

注意：`SENSOR_STACK_WARNING_ABSENT` 只支持本次覆盖路径的功能观察，**不等于**完整最坏栈界已证明；
若告警重现，报 `STACK_DANGER_OBSERVED` 并停，不再退回到“等 20 秒说静默不足”。
业务若仍 `-117`，按本次 TMRO 的真实 `tx_written/rx_total`、阶段、期限、exp 值查因，
不重新退回供电、接线、ENN 猜测；`echo_match_off` 仍禁用（`KNOWN_INVALID_FOR_DIAG`）。

## 6. 一次批准文字

> 批准执行本文件 URO-R2 板级包：使用 139e937 的 `b1e85294…` measure-a0 镜像（268340 B）与 `9ed8a3e3…` R2 采集器；
> 现场有人、电机未接、SW1 阻断、模块线路不变。允许维持供电，无电时常规上电一次；
> 一次既有 Keil 下载（等进程退出再按日志判定）、一次 268340 B 定长回读（0x08000000 起 0x00041834）、两个 DAP 会话，
> 其中回读后开串口前唯一一次显式 SYSRESETREQ；一次 COM5 打开，按序七条查询含一次 probe（三笔读零 WRITE 帧）。
> 启动横幅按 `5.3.0 build Oct 28 2026 10:23:50` 逐字判。包内不逐条确认；真实失败、前置失效或额度用尽即停，不重试、不增动作。

也可简写：**批准执行 URO-R2 板级包**。

## 7. 待绑定变量（S0 由本地已知路径填实，公开副本脱敏）

| 变量 | 含义 |
|---|---|
| `$Uv4Exe` `$PythonExe` | 已证工具（UV4 5.43.1.0 / Python 3.12.5 / pyOCD 0.45.1 / Win PS 5.1） |
| `$TaskRoot` | 本轮全新仓外执行根（不复用 ns800-r1-board 目录里的日志/回读/采集件） |
| `$DownloadProject` `$PayloadAxf` | 新副本 download-b.uvprojx 与其 dl/rt-thread-a0.axf（§1 固定 SHA） |
| `$FrozenCollectorR2` | `reports/reviews/phase8-sensor-stack-uart-r2/UARTReadDiagSerialR2.ps1`（92860 B / 9ed8a3e342f7…） |
| `$FlashLog` `$ReadbackPath` `$CaptureOutDir` `$ApprovalReference` | 本轮新文件；回读件不得指向旧快照 |
| `$DapUid` | 既存私有依据（长度 48，`sha256(uid)[:12]=d2f530dbd062`），不枚举 |
