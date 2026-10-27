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
（12 块可复算 base64，全部是离线夹具与门输出，**不是 UART 线上采样**）。

脱敏：仓库里只出现代号（`WORK_OUT/BOARD_B_ROOT/KEIL_ROOT/PY_SITE_ROOT/PYTHON_EXE/GATE_FROZEN_COPY/PROJECT_ROOT/BSP`）
+ 相对路径；完整"代号→本机路径"映射留在仓外 `local_path_index.json`。转换是纯字符串替换，
所有散列按原始字节直算；探针 UID 与本机用户名未写入仓库。

未覆盖/继续开放：B-1、BUG-008、RISK-03/03a、物理 RISK-04-b、M-1/M-2/M-3、外部时基证明；
`sw1_cut.ps1` 仍 HOLD；A0 时长证据仍 `NOT_ACHIEVED`（要等本包执行）；板上仍是旧 `358fd81a…` 载荷，
新 `5f30d3f0…` **尚未下载**；电源终态沿用上一轮 `USER_REPORTED` 断电，本轮未触碰设备。

## 7. 下一步（需要用户的一次批准）

任务书 §9 的批准文字如下，**在用户实际发出前只是待审批准文本，不构成许可**：

> 批准本文件 BOARD-B 整包：CP0 通过后，允许一次新镜像下载、一次指定载荷回读、一次主动复位、
> 固定 17 次 trip 与一次 disarm，以及包内状态查询和有条件 RAM 清理。包内不逐条确认，失败即停，
> 不重试、不自动回刷、不启用 STEP 或电机；我配合现场隔离和最后断电。

取得该批准后，按 CP1（现场隔离与一次供电）→ CP2（一次下载 + 一次载荷回读）→
CP3（字节采集、一次主动复位、固定 54 条命令，逐批门 + 关闭事实双判据）→ CP4（收尾并登记实际电源终态）
顺序执行，包内不逐条再问；前置失效、真实失败或需要越界改动就停，保存已有结果，不补跑凑数。
另需现场操作者配合 §5 的隔离确认与最后断电（先关 24 V 再拔 USB，SW1 保持阻断）。

## 8. STOP

```
A0_BOARD_B_CP0_READY / AWAITING_BOARD_B_PACKAGE_APPROVAL
NO HARDWARE EXECUTED / IMAGE_NOT_DOWNLOADED / BOARD_STILL_RUNNING_358fd81a
NO BUILD / NO FLASH READ / NO PROGRAM / NO RESET / NO SERIAL / NO MOTOR / NO STEP_ENABLE
FOUR SAFETY MACROS RT_FALSE
```
