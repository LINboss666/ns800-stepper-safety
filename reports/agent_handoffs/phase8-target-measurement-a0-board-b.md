# NS800 交接：phase8-target-measurement-a0-board-b —— CP0 checkpoint（离线，未获本包批准）

日期：2026-10-27（Asia/Shanghai）
任务书：`NS800_Gate_R3_GO_and_A0_Board_B_Package_358e1ca_20261026.md`
document_base / 开工 HEAD：`358e1cace6e64bbd1d767076f940dd3aaaf9cd46`（= 上一轮被审交付 = 开工时远端分支头）
`accepted_firmware_code_commit`：`cd8d9f095030c08634a082dd2bb9bc622d92fb1d`（本轮未改一字节）
`gate_code_commit`：`b187cdd2ffc3225474006d28c1dca3eca591351d`（最终门字节就在这一提交里）

## 1. 结论登记（随本次实际工作，不另开纯登记轮）

**`A0_EXPORT_GATE_REVIEW_GO`（任务书 §1）在此登记并接受**：接受 `358e1ca…` 里的最终采集门，
R3 两组问题（分类前的原始字节完整性、`%u` 打印提升被当成存储宽度）关闭，无新增必须返修项。
此前固件与两份镜像的限定 GO 保持不撤回、不扩大。本 GO 只接受**离线采集完整性合同**。

本轮实际工作是该包的 **CP0（离线准备与采集器硬门前置）**。
**硬件状态：包已准备，本包执行批准尚未取得**；CP0 本身不构成批准，也没有任何设备动作。
`builds_executed=[]`、`hardware_actions=[]`、`firmware_changed=false`、`production_changed=false`。

## 2. CP0 做了什么，结果如何

| 前置（任务书 §4 条目） | 结果 |
|---|---|
| §4.1 §3 全部输入实读 | **通过**：下载 BIN `5f30d3f0…` 261452 B、AXF `8b95464e…` 1675944 B、门 `94e83175…` 50246 B、默认 BIN `02e98458…` 251148 B 全部与 §3 逐字节一致；载荷跨度 `0x3FD4C`/末端不含 `0x803FD4C` 一致；测量 BIN 内构建戳离线实读到 `Oct 26 2026` + `20:57:05`。**未重编、未重新转换 AXF 凑哈希** |
| §4.1 四宏 | 4/4 仍 `RT_FALSE`（`motor.h:69`、`motor.h:83`、`project_board.h:51`、`tmc2209.h:100`） |
| §4.1 既存恢复材料 | 只按本地磁盘字节核：before-a/b 各 1048576 B `b89f8a7c…`；恢复 HEX 两处各 3014958 B `b3ac97b9…`。**哈希相同不证明恢复下载已演练**（仍 `NOT_EXECUTED`） |
| §4.2 采集器提取 | 从任务书 MD 的 ```powershell 围栏用 python 切正文 + `write_bytes`（未用 `>`/`Set-Content` 转存二进制）：**19432 B / sha256 `521d758b2cae…` 与 §3 记录一致**，非 ASCII 字节 0、CRLF 0 |
| §4.2 门冻结副本 | 从 `b187cdd…` 的 git 对象取原始字节写仓外副本：50246 B / `94e83175…`，与工作树门字节逐字节相同 |
| §4.3 工具版本与离线帮助 | Keil UV4 **5.43.1.0**、pyOCD **0.45.1**、Python **3.12.5**、PowerShell **5.1.26100.5074**；未安装/升级任何东西。`commander --help` 里 `--no-config/-O/-u/-t/-f` 都在；本包用到的 `savemem/reset/status/exit` 与 6 个 `-O` 选项在安装源码里都存在 |
| §4.3 DAP UID | 沿用上一轮本地既存日志里的板载 DAP 序列号（48 位十六进制，形状符合采集器 `^[0-9A-Fa-f]{20,64}$`）；**未重新列探针、未猜选第一个**，UID 不写入仓库 |
| §4.4(a) 语法检查 | `Parser::ParseFile` → `TOKENS=3883 PARSE_ERRORS=0 => PARSE-OK`，rc=0 |
| §4.4(b) `-SelfTest` | rc=0，stdout 原文 `BOARD-B-OFFLINE-SELFTEST PASS; device_actions=0`，stderr 0 字节；OutDir 为全新空目录 |
| §4.4(c) ledger 实读 | `OFFLINE_SELFTEST.wire_calls = 0`（按字段读，不是叙述）；无 `SERIAL_OPEN/TX_ATTEMPT/RESET_ATTEMPT` 条目 ⇒ 自检分支未创建串口对象、未起子进程 |
| §4.6/4.7 下载副本 | 全新 `BOARD_B_ROOT/download-b/`：副本 uvprojx 231472 B `d53c62a3…`（与 BOARD-A 成功副本同号），uvoptx 与原文件**逐字节相同**，载荷副本 AXF 与 §3 一致；副本相对原工程**只有 4 行**差异（行 51/52/83/85），第二目标逐字节未动；原工程一字未改 |
| §4.7 FLM/DLL | Bank1 55504 B `1ae62260…`、Bank2 55508 B `aaa50012…` 与 §4.7 一致（磁盘各 1 份、内容唯一）；`CMSIS_AGDI.dll` 2410776 B `05c8cd29…`、`UL2CM3.dll` 2535192 B `7a5c69da…`；`sIfile/tIfile` 均空 ⇒ 无下载初始化脚本 |
| §4.8 pyOCD 隔离目录 | `BOARD_B_ROOT/pyocd-work-b` 已建且为空：无 `pyocd_user.py/pyocd.yaml/pyocd.yml`，后续一律显式 `--no-config` |

`tests_executed` 只列上面这些本轮真实执行过的离线检查；未重跑 A0/S1/S2/V1/V2 固件主机链，
未运行 Keil 编译或下载，未连接任何设备（任务书 §5.6/§8）。

## 3. 两条需要复审知晓的判断（不是结论式口号）

1. **`savemem` 的"纯内存读"是从安装源码判出来的**：`coresight_target.py:76` 把 `0x00000000..0x1FFFFFFF`
   定义为 `RamRegion(name="Code")` ⇒ `0x08000000` 落在 Code（`:80` 的 `RAM2 @0x80000000` 与本地址无关，
   上一轮 CAL-4 更正继续成立）；`commands.py:628` 的 `flash_init_required` 要求 `region.is_flash`，
   RamRegion 为假 ⇒ 不进 `flash.init(VERIFY/ERASE)`，`:635` 直接 `read_memory_block8`。
   因此 §6 那一次回读不调用设备 Flash 算法、不引 ELF 代读；并且该版本 commander 的进程退出码
   不作成败判据，判据只用日志文本 + 落盘字节 + SHA（上一轮实测的"rc 恒 0"教训继续生效）。
2. **"Reset and Run" 在两份工程文件里没有可核对标签**（grep 命中 0）：它落在 Keil 私有位域
   （`CMSIS_AGDI` 的 `-O206`），状态 **`UNPROVEN_OFFLINE`**。缓解沿用上一轮：CP2 回读用
   `connect_mode=halt` + `resume_on_disconnect=false`，下载后核心真实状态由 `status` 行与日志暴露，
   按实际次数登记，不并成"计划内一次"。

## 4. 本包上限（照任务书 §2 登记，未被本轮任何动作消耗）

| 动作 | 上限 | 本轮已用 |
|---|---:|---:|
| 初始供电过程 | 1 | 0 |
| 新 A0 镜像下载调用（Keil `-f`，不编译） | 1 | 0 |
| 下载后片内载荷回读（261452 B） | 1 | 0 |
| 已知 DAP 的 pyOCD 会话 | 2 | 0 |
| 主动 `SYSRESETREQ` | 1 | 0 |
| COM5 打开（115200/8N1/无流控/DTR·RTS 不激活） | 1 | 0 |
| `spm trip` | 17（批 1×1 + 批 2×16） | 0 |
| `motor_disarm` | 1（批 3，走 off_attempt） | 0 |
| `spm init` | 1（静止前置后；会设置 DWT/DEMCR 位） | 0 |
| `spm clear_records` | 最多 3（每批须门 + 关闭事实双双通过） | 0 |
| 下一批开始 | 最多 2（最终批后无第 4 批） | 0 |
| 串口命令总数（完整成功路径） | 54 | 0 |

不做探针/串口/设备枚举、不读新的全片备份、不演练恢复下载、不自动回刷、不启用四宏、
不请求许可 HIGH、不开 STEP、不改 DIR、不 arm/start/ramp、不接电机。
正常启动与 17 次保护都有真实 GPIO/PWM/存储副作用，**不写成"只读测量"**。

## 5. 首次失败与自身缺陷（一律保留）

1. 提取脚本首版把任务书 §4 的 ParseFile 片段当附录源码按 ASCII 写出 ⇒ `UnicodeEncodeError`
   （该片段含中文占位符）。处置：该片段是"检查命令"而非附录源码，改自写 ASCII 检查脚本并按 §4.5
   只填运行参数；**附录采集器本体一字未改**。
2. 工具取证脚本首版 `import pyocd.utility.mask.match_mask`（该版本无此名）⇒ `ImportError`。
   处置：改为只读安装源码，不构造 Target、不连设备。
3. CP0-4 首版把"工作树原始字节 == `cd8d9f09` blob"当准入条件，而 `project.uvprojx` 在树里是 CRLF、
   blob 是 LF（autocrlf）⇒ **假 ABORT**。处置：改用 LF 归一比较，并新增"等于 BOARD-A 登记原值"的
   连续性核对；未放宽任何输入判据（AXF/BIN/门仍按原始字节直算）。
4. 工件归档首版把 0 字节文件写成非 base64 文本 ⇒ 复算脚本解码失败。处置：空块显式写注释行，
   12 个块现在逐块复算一致（长度 + SHA-256）。

## 6. 证据与边界

证据：[cp0_offline_precheck.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp0_offline_precheck.txt)
（7 节，含脱敏代号表与逐条判据）、[A0BoardB.ps1](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/A0BoardB.ps1)
（与任务书附录逐字节相同的副本）、[cp0_selftest_artifacts.md](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp0_selftest_artifacts.md)
（12 块可复算 base64，全部是离线夹具与门输出，**不是 UART 线上采样**）、
[cp1_cp2_download_readback.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp1_cp2_download_readback.txt)、
[cp3_capture_stop.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp3_capture_stop.txt)、
[cp3_wire_archive.md](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp3_wire_archive.md)
（4 块可复算 base64：真实 UART 被动窗口、启动窗口、采集器台账、复位工具 stdout；
`session.rx.bin` 声明为前两块按序拼接，原件另留本地）。

脱敏：仓库里只出现代号（`WORK_OUT/BOARD_B_ROOT/KEIL_ROOT/PY_SITE_ROOT/PYTHON_EXE/GATE_FROZEN_COPY/PROJECT_ROOT/BSP`）
+ 相对路径；完整"代号→本机路径"映射留在仓外 `local_path_index.json`。转换是纯字符串替换，
所有散列按原始字节直算；探针 UID 与本机用户名未写入仓库。

未覆盖/继续开放：B-1、BUG-008、RISK-03/03a、物理 RISK-04-b、M-1/M-2/M-3、外部时基证明；
`sw1_cut.ps1` 仍 HOLD；A0 时长证据仍 `NOT_ACHIEVED` —— CP3 在启动门即停（见 §9），一条目标记录都没采到。
（§6 这段写于 CP0 时点：当时板上仍是旧 `358fd81a…`、新镜像未下载；CP2 之后已被本次唯一一次
下载替换为 `5f30d3f0…`，见 §8。）

## 7. 批准与执行范围

任务书 §9 的批准文字已由用户实际发出：**「批准 BOARD-B 整包，允许执行 CP1」**（并当场确认四项
现场前置成立）。据此整包进入执行，包内不再逐条索要确认；前置失效、真实失败或需要越界改动即停。

## 8. CP1 与 CP2 已执行（结果与判据）

证据原文见 [cp1_cp2_download_readback.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp1_cp2_download_readback.txt)。

| 项 | 实际结果 |
|---|---|
| CP1 供电（上限 1 次，已用 1） | USB（板载 LDO 逻辑电源）→ 既有 24 V，由操作者手工做；接线/模块/SW1 未变。**两个时刻与"有无异常运动/发热/指示"未回报 ⇒ 登记 `NOT_REPORTED`**：不补造时刻，也不把"未收到异常回报"写成"已确认无异常"。板上电的旁证只有工具侧（DAP 连接、片内擦写、`Verify OK`、回读时两条 `Halted`） |
| CP2 唯一一次下载 | `UV4 -f BOARD_B_ROOT/download-b/download-b.uvprojx -t rt-thread -j0`；日志逐字 `Erase Done.Programming Done.Verify OK.Flash Load finished at 11:35:12`；实际 Load 载荷 = 副本 `dl/rt-thread-a0.axf`（`8b95464e…`，与 §3 一致）；`mass erase/unlock/选项字/OTP/系统区` 命中 0；**UV4 退出码只表示进程结束，不作判据** |
| CP2 唯一一次回读 | pyOCD 会话 #1（整包上限 2 次）：`--no-config -t cortex_m -u <DAP_UID> -f 1000000` + §6 全部 `-O` 选项 + `status` → `savemem 0x08000000 0x3FD4C` → `status`；两次 status 都是 `Core 0 (None):  Halted`；`commander` rc=0 不作判据 |
| 回读字节身份 | **261452 B / SHA-256 `5f30d3f058ceff51…`**，与任务书 §3/§6 记录一致；与仓内已接受 `measure-a0.bin` 做 `cmp` **无差异**；先落盘后算哈希，输出是全新文件（不是从 BIN 复制） |
| 板上载荷状态变化 | 新测量镜像 `5f30d3f0…` 现在真实在板上；旧 `358fd81a…` 被这一次（也是唯一一次）下载替换 |
| 本轮首次失败 `CP2-F1` | 我给 `savemem` 传了带反斜杠的绝对路径，pyOCD 的 `-c` 命令串把每个 `\` 当转义吞掉 ⇒ 落盘名变成 `ns800-board-breadbackafter-export-r1.bin`。处置：按日志 `Saved 261452 bytes to …` 找回真实文件再直算 SHA/`cmp`；**未重发 `savemem`**（那要多耗一次会话），未改附录采集器之外的任何越界动作 |

## 9. CP3 执行即停止（附录采集器的启动门在本板上不可满足）

证据原文见 [cp3_capture_stop.txt](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp3_capture_stop.txt)
与可逐块复算的字节归档 [cp3_wire_archive.md](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/cp3_wire_archive.md)。

| 项 | 实际结果 |
|---|---|
| 运行前只读预检 | 采集器 `19432 B / 521d758b…`、冻结门 `50246 B / 94e83175…` 均等于已审字节；DAP UID 从两处既存日志唯一解析（未列探针）；pyOCD 目录无隐式配置；`capture_run1` 全新 |
| 串口与复位 | `SERIAL_OPEN`（整包唯一一次，115200/8N1，`dtr=false` `rts=false`）→ 被动 2 秒 → `RESET_ATTEMPT SYSRESETREQ attempt=1`（整包唯一一次）；复位工具 stdout 逐字 `Resetting target` + `Core 0 (None):  Running` |
| 被动 2 秒观察 | `001_open_passive.rx.bin` 194 B 高熵非文本字节（3 个 NUL、无 CR、97 个 >0x7F），不是 RT-Thread 文本 ⇒ 采集器按 §7 走主动复位。**成因未证**，登记为未观察清楚的事实，原件在归档内可逐字节复算 |
| 启动窗口 | `002_boot.rx.bin` 3757 B / `97b37f32…`；构建戳逐字 `5.3.0 build Oct 26 2026 20:57:05` = 新镜像旁证；阶段 1–11 全打印，终态 `[MAIN] boot complete but state=FAULT_LATCHED fault=8`、`[Motor] arm gate fail mask=0x1B (arm REFUSED)`、`[TMC]/[ADC] DEGRADED` |
| 停止点 | **`Wait-Prompt` 超时** ⇒ `STOP_FAILURE "Reply timeout; no retry and no cleanup."`，采集器 rc=1，`finally` 已关串口。启动窗口里 `msh >` 出现 **0** 次：`msh` 只在偏移 489 B 出现一次且尾部 `>` 未落到流里，其后还有约 3.3 KB 阶段输出 ⇒ 提示符既不在流尾、复位后也不会自发打印第二次 |
| 根因（有对照，不靠猜） | ① 既存已证材料同型：BOARD-A 旧镜像 `02_boot.txt`（3762 B）里 `msh >` 同样 0 次、末行同为 `[BB] session 1 written`；② BOARD-A 采集器把 `msh >` 只用作**发出命令之后**的响应结束标记（本地既存材料 `RAWDATA/cp4_run.ps1:101`，另有 1200 ms 静默兜底），其真实回复 `cmd_100_boot_free.txt` 尾部确有 `msh >`；③ 附录采集器 [A0BoardB.ps1:81](file:///D:/ns800/reports/reviews/phase8-target-measurement-a0-board-b/A0BoardB.ps1#L81) 在复位后、**TX 计数为 0** 时要求先看到行尾提示符（抛错在 :84）⇒ 与本板既证行为矛盾。固件侧提示符形状本身没错：`"msh "`（[shell.c:93](file:///D:/ns800/rt-thread-master/rt-thread-master/components/finsh/shell.c#L93)）在 [shell.c:120](file:///D:/ns800/rt-thread-master/rt-thread-master/components/finsh/shell.c#L120) 判长度后由 :122 追加 `>`，错的是"必须自发出现在流尾"这个前提 |
| 判定 | 附录采集器启动门缺陷；**不是**设备失败、**不是**采集门（完整性合同）失败、**不是**镜像问题。按任务书 §4.5/§7 停在 CP3，不在本地改附录核心逻辑后自行跳过复审 |
| 本轮实际消耗 | 供电 1/1、下载 1/1、载荷回读 1/1、pyOCD 会话 **2/2**、主动复位 **1/1**、COM5 **1/1**；串口命令 **0/54**、trip 0/17、disarm 0/1、stop/dump 0/3、clear 0/3、下一批 0/2、`spm init` 0/1 ⇒ **COM5/复位/会话三项已用满，本包无法再续跑 CP3** |
| 未做（全部保持） | 任何目标命令与门判定、clear/dump/trip/disarm、STEP 使能、DIR 变更、许可 HIGH、arm/start/ramp、电机接线、四宏启用、构建、第二次下载、恢复下载演练、设备枚举、`sw1_cut.ps1` |
| 时长证据 | 仍 **NOT_ACHIEVED**：一条 47 列记录都没采到，也没有 ACCEPT/REJECT 发生；`ps` 栈高水位、cycles 统计全部 `NOT_OBTAINED`，不补造 |
| 本轮新增首失败 | `CP3-F1` 启动门不可满足（上表）；`CP3-F2` 我的包装脚本 `cp3_run.py` 用 UTF-8 + `errors='replace'` 收 PowerShell 的 OEM/GBK stderr ⇒ 本地化错误文本字节被替换字符吃掉、不可复原（ASCII 抛错文本与行号完整；采集器自己落盘的 wire/台账不经过这次解码，仍逐字节可复算）。只登记不修补历史 |

## 10. 本轮收口时自查出的两处登记缺陷（勘误，不改写历史）

- **勘误 E1（CP0 遗留的字节登记错误）**：`evidence_identities` 里 `cp0_selftest_artifacts.md` 曾登记为
  `19237 B / c3192745…`，而该文件自 CP0 提交 `7c5eada` 起的真实字节一直是 `19236 B / 93898d96…`
  （工作树、`7c5eada` 的 blob、`HEAD` 的 blob 三处一致，且该路径只有这一个提交）。
  成因是登记发生在提交前的重新生成之前，登记值没跟着更新。影响面：只错在 handoff JSON 的一个字段，
  全仓 grep `19237` 与 `c3192745` 均 0 命中，没有任何证据文本引用过该错值；采集器/门/镜像的判据字节不受影响。
  处置：按当前提交字节重算登记 + 本节公开差异；**未删除或重做已发布产物，也未回收旧提交**。
- **勘误 E2（本轮内自查的取证顺序缺陷）**：`cp3_capture_stop.txt` §6 首稿写了"本机无残留 powershell"，
  但写那句时**没有实测**——属未验证断言。12:00:38 才用 `Win32_Process` 列举
  （`Name=powershell.exe/python.exe` + `CreationDate`）补做复核：只剩本轮自己启动的两个短命进程
  （11:58:58 / 12:00:38），11:49 启动的采集器进程已退出 ⇒ 无宿主进程占住 COM5。
  处置：证据正文补上实测时刻/方法/读数并注明晚于首稿；不删原结论，也不假装一开始就取过证。
- **本轮内纠正的两处引用行号（提交前修正，非历史勘误）**：`shell.c` 追加 `>` 的行号原写 `:120-121`，
  实为 [shell.c:120](file:///D:/ns800/rt-thread-master/rt-thread-master/components/finsh/shell.c#L120) 判长度、
  :122 追加；`A0BoardB.ps1` 的 Wait-Prompt 判据行原写 `:83`，实为 :81（抛错在 :84）。
  证据文件因此重生成两次（`cp3_capture_stop.txt` 15866 → 15880 → 16249 B，最后一次含 E2 的补证），
  JSON 的 `evidence_identities` 同步按最终字节登记。

## 11. STOP

```
A0_BOARD_B_CP3_STOPPED / AWAITING_INDEPENDENT_BOARD_EVIDENCE_REVIEW
IMAGE_DOWNLOADED_AND_READBACK_VERIFIED 5f30d3f0… (板载新镜像, 启动戳旁证一致)
LIMITS_EXHAUSTED: POWER_ON 1/1, DOWNLOAD 1/1, READBACK 1/1, PYOCD_SESSIONS 2/2, ACTIVE_RESETS 1/1, COM5_OPENS 1/1
SERIAL_COMMANDS 0/54, TRIPS 0/17, DISARM 0/1, STOPS 0/3, DUMPS 0/3, CLEARS 0/3, SPM_INIT 0/1 — 采集门从未被调用
A0_DURATION_EVIDENCE NOT_ACHIEVED / NO_TARGET_RECORDS
NO MOTOR / NO STEP_ENABLE / NO DIR CHANGE / NO PERMIT-HIGH REQUEST / FOUR SAFETY MACROS RT_FALSE
NO BUILD / NO SECOND DOWNLOAD / NO AUTOMATIC RESTORE / NO DEVICE ENUMERATION / NO sw1_cut.ps1
NO_RETRY_NO_BACKFILL: 已停 TX、已关串口、原字节全部保留; 续跑需要新批准与新包
```

现场收尾请求（本轮已发出，等操作者实际回报）：**先关 24 V，再拔 USB，SW1 保持阻断位**。
在收到回报之前，电源终态登记为 `UNKNOWN`，不把登记时刻当作断电时刻。
