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
