# NS800 M7 P0 板级执行轮交接（已批准、已执行、停在启动身份门）

任务 `phase8-motor-m7-p0-board`，2026-10-30。执行基线 `9c5fe0bd088a8a7195a922a2cf3726fb45024503`。
批准 = 用户本轮原话："已批准执行 9c5fe0b 的 P0 整包，按既定镜像、采集器和额度直接执行。
到 WAIT_DMM_BEFORE、WAIT_DMM_AFTER 时分别提示我测量；失败即停，不转电机。"

细节全部在 `reports/reviews/phase8-motor-m7-p0-board/run_evidence.md`，本文只登记判定与边界。

## 0. 一句话结果

镜像写入并逐字节回读证明在板；采集器在读到启动流后的"横幅 + 完整就绪行"双门前超时停止，
**七条只读命令一条都没发**，两个 `WAIT_DMM` 从未出现，**P0 未取得任何电压数据**。
`BOOT_IDENTITY_GATE_FAILED / TX_ATTEMPTS=0 / BUSINESS=NOT_REACHED / CAPTURE=INCOMPLETE_BOOT_ONLY`。

## 1. 已证 / 未取得 / 未声称

| 类别 | 内容 |
|---|---|
| 已证（本轮新增） | M7-R1 motor 载荷 `e614a075…f7da` / 266244 B **第一次真正在板**：`0x08000000` 起 `0x41004` 回读，两次 `Halted`，文件与接受 BIN `cmp` 逐字节相等 |
| 已证（本轮新增） | 该镜像本次启动确实跑起来了并打印到 `READY`：`[MAIN] boot complete: READY (motion still gate-locked)` 在流里逐字完整 |
| 已取得的数据 | 仅启动流一份：3381 B / `bc47dc02…14fa`（base64 原字节归档）。无 sample、无 DMM、无 `result.json` |
| 未声称 | 不登记 `VOLTAGE_OBSERVATION_ONLY`（那是成功路径的上限标签）；不声称复位工具自证成功（`RESET_TOOL_RC_NOT_OBSERVED`，两个复位输出件均 0 字节）；不声称 ADC/链路/控制台谁坏了；不声称栈问题已修（本轮"无危险标记"只是一次覆盖） |

## 2. 停点的原始事实

- 期望横幅 `5.3.0 build Oct 29 2026 23:11:52` **不在流里**：`5.3.0`、`.3.0`、`build` 片段各出现 0 次。
- 版本行前 12 字节与后 20 字节（`Oct 29 2026 23:11:52`）完好，中段 11 字节 `'.3.0 build '`
  变成 8 字节、其中 7 个是非 ASCII/控制字节（`09 02 d5 a5 b1 91 81`）。整行 43→40 字节。
- 同一条流里另有 6 行同类**行内替换**污染（`[BOQu\x81…`、`[CFG] l\x91\x95…`、`[SS] self-test done…`、
  `[Safety] limit_inputs=…\xca%…`、`T+\x9d   OK verdict=0…` 等）。
- `\r\r\n`（本轮 lone_CR=22）**不是新现象**：既存历史真机件
  `phase8-tmc-uart-rootcause/boot_ms2048.txt` 同样是 `\r\r\n` 收尾，且那份横幅干净
  （`5.3.0 build Oct 28 2026 17:36:54`）。这说明同一条板/口/工具链以前取到过完好版本行。
- 危险标记独立扫描全 False。

## 3. 机制清单（三条相容，都不择一）

已核实前提：`rt_kprintf` 用单个 `static rt_log_buf[128]`（kservice.c:391-422），
`RT_USING_THREADSAFE_PRINTF` 未开 ⇒ `PRINTF_BUFFER_TAKE/RELEASE` 是空宏（kservice.c:324-331，
本仓 `phase8-stop-priority-s2a/source_audit.txt:178` 早已登记）⇒ **控制台无互斥**；
本轮污染流里 `msh >` 唯一一次出现在偏移 486、夹在两条 `[BOOT]/[SAFETY]` 输出之间，
说明 shell 线程与 phase-7 引导**并发写控制台**；替换出的高字节多为 `0x80-0xBF` 形态，
与 UTF-8 中文行被截碎相容。

1. 目标侧并发 `rt_kprintf` 争用共享缓冲；
2. UART→DAPLink CDC→PC 在复位瞬间丢/错字节；
3. 采集侧每块 `fsync` 后再读的窗口损失。

判开需要新批准，本轮一条都没做：(A) 不改采集器字节、用历史 PowerShell 链对同一板子再取一次启动流比对；
(B) 开 `RT_USING_THREADSAFE_PRINTF` 或给 `_kputs` 加锁后重编对照镜像再取一次（属产品/构建动作）。

## 4. 额度与纪律

实耗：下载 1/1、定长回读 1/1、COM5 1/1、显式 SYSRESETREQ 1/1、DAP 会话 2/3、命令 0/7、DMM 0/4。
零重试、零补发、零额外复位/重烧/开串口；无电机命令、无配置写、无清故障、无 pin_status、无 TMC 压测。
未碰供电/接线/SW1/VREF/VM/相线；未索取断电回报。串口由采集器自行关闭，板子留在已重启的 `READY` 态。
整包批准已消耗：继续执行需要用户对**新的具体整包**再批准一次。

## 5. 采集器合同本身的表现（对照 R1 的 37 项）

这次停点**没有暴露采集器缺陷**，反而按合同做了它该做的事：先落盘再判定（3381 B 完整保住）、
绝不当"回显即响应"、门不过就不发任何一条命令、异常出口仍关串口并写 `PORT_CLOSED`。
需要讨论的是**门的设计**而不是字节：横幅是开机第一行，正落在并发写最密集的窗口，
而 `[MAIN] boot complete: READY` 这条要求是开机尾部、本轮逐字完好。是否把启动身份判据改成
"以完整就绪行为准、横幅降级为可选核对"属于判据变更，必须由独立复审决定，我不自行放松。

## 6. 未闭合

- P0 电压观察未取得；`current_cal sample 128` 从未在板上运行过（M7 采样链仍 `UNPROVEN_ON_BOARD`）。
- 控制台并发写是否会在**响应**里同样污染字段（那会直接威胁七条命令的判据可靠性）未测。
- 栈告警充分性、`tshell` 栈高水位仍 `NOT_MEASURED`。
- 独立母线电流参考、真实 DIAG/SG 端到端、最坏停机时延、整机安全验收：全部开放。
- P1 `METHOD_NOT_READY`、P2 `NOT_AUTHORIZED`。
- 计算器与六件镜像维持已接受；本轮未改任何脚本字节（散列可核）。

## 7. 回执

```text
P0_BOARD_RUN_EXECUTED / BOOT_IDENTITY_GATE_FAILED / TX_ATTEMPTS=0 / BUSINESS=NOT_REACHED
M7_R1_MOTOR_IMAGE_ON_BOARD_PROVEN / READBACK_266244B_BYTE_IDENTICAL
NO_VOLTAGE_OBSERVATION_OBTAINED / DMM_READINGS_TAKEN=0 / RESULT_JSON_ABSENT
DOWNLOADS=1 READBACKS=1 COM5_OPENS=1 EXPLICIT_RESETS=1 RETRIES=0 / NO_MOTOR_ACTIONS
ROOT_CAUSE_NOT_DETERMINED / CONSOLE_RACE_HYPOTHESIS_LISTED_NOT_CHOSEN / APPROVAL_QUOTA_CONSUMED
```

下一轮等我方给出上面的 A/B 判开方案，由独立复审与用户决定授权哪一条。本轮不追加任何设备动作。
