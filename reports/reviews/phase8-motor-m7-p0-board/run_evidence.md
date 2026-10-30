# M7 P0 板级执行轮记录（2026-10-30，一次批准，实际停点）

批准来源（用户原话，2026-10-30）：

> 已批准执行 9c5fe0b 的 P0 整包，按既定镜像、采集器和额度直接执行。到 WAIT_DMM_BEFORE、
> WAIT_DMM_AFTER 时分别提示我测量；失败即停，不转电机。

执行根（仓外，原始件保留、未删除）：`D:\ns800-m7-p0-20261030T182818`
提交基线：`9c5fe0bd088a8a7195a922a2cf3726fb45024503`，开工时工作树 clean，远端 tip 同值。

## 1. 结果一句话

镜像已按额度写入并**逐字节回读证明在板**；采集器在"启动身份"门前如实停止，
**七条只读命令一条都没发**，因此**没有取得任何电压观察数据**，也没有产生 `result.json`。
两个 `WAIT_DMM` 从未出现，人工读数从未索取，四个 DMM 读数一个都没发生。

停点标签：`BOOT_IDENTITY_GATE_FAILED / TX_ATTEMPTS=0 / BUSINESS=NOT_REACHED / CAPTURE=INCOMPLETE_BOOT_ONLY`

## 2. 额度实耗（批准上限 vs 实际）

| 动作 | 批准上限 | 实耗 | 结果 |
|---|---:|---:|---|
| sector 下载同一 AXF | 1 | 1 | rc=0；`Erased 267264 bytes (261 sectors), programmed 267264 bytes … at 13.21 kB/s` |
| `0x08000000` 起 `0x41004` 回读 | 1 | 1 | rc=0；两次 `Halted`；`Saved 266244 bytes`；文件 266244 B / `e614a075…f7da`，与接受 BIN `cmp` 逐字节相等 |
| COM5 打开 | 1 | 1 | 台账 `PORT_OPEN_ATTEMPT` → `PORT_OPEN` 成功；会话末 `PORT_CLOSED` |
| 显式 SYSRESETREQ | 1 | 1 | 台账 `RESET_ATTEMPT`（只记 `sha256(uid)[:12]=d2f530dbd062`）；芯片确已重启（启动流完整出现） |
| DAP 会话 | 3 | 2 | 下载 1、回读 1；第 3 次复位由采集器内部子进程发起 |
| 4 条状态查询 | 各 1 | 0 | 未发 |
| `current_cal show` | 2 | 0 | 未发 |
| `current_cal sample 128` | 1 | 0 | 未发 |
| UT39A+ 电压读数 | 4 | 0 | 未索取（`WAIT_DMM_*` 未出现） |
| 电机动作 / 配置写 / 清故障 / 复位重烧 | 0 | 0 | 无 |

没有重试、没有补发、没有第二次开串口、没有第二次复位、没有额外 A0/采样。

## 3. 采集器实测行为（逐事件）

`S3_ledger.jsonl` 全文只有 6 行：

```text
BIND        payload/readback = e614a075…f7da  axf = 2205c7e7…d584  calculator = 5c7a2340…63ad
PORT_OPEN_ATTEMPT  COM5
PORT_OPEN          COM5
RESET_ATTEMPT      known_uid_digest_prefix=d2f530dbd062
PORT_CLOSED
```

`S3_collector.stderr.txt`（43 B，全文）：

```text
P0_FAILED:Stop:REPLY_TIMEOUT:raw_boot.bin
```

`S3_collector.stdout.txt` = 0 字节（没有任何 `WAIT_DMM_*`）。进程退出码 1。
`P0/reset.stdout.bin` 与 `P0/reset.stderr.bin` 均为 **0 字节** → 复位子进程的返回码
**未被观测**（采集器先在读流上超时，`p.wait` 分支没走到）。因此本轮只能说"芯片确实重启了
（启动流完整出现）"，不能说"复位工具自证成功"。标签：`RESET_TOOL_RC_NOT_OBSERVED`。

## 4. 为什么门没过：启动流里有行内字节污染

`P0/raw_boot.bin` = 3381 B / `bc47dc022b25bd483c16ab749895bf2a9d7dba318fdf88826357d2dbb98214fa`
（已按 base64 原字节归档，`S3_raw_boot.bin.b64` 解回散列一致）。

采集器要求的两个条件：

- 横幅 `5.3.0 build Oct 29 2026 23:11:52` —— **不在流里**（`5.3.0`、`.3.0`、`build` 三个片段各 0 次）
- 完整就绪行 `[MAIN] boot complete: READY (motion still gate-locked)\n` —— **在流里**（逐字完整）

版本行实测（`S3_raw_boot_analysis.txt`）：

```text
expected : b' / | \     5.3.0 build Oct 29 2026 23:11:52'   len=43
observed : b' / | \     5&\t\x02\xd5\xa5\xb1\x91\x81Oct 29 2026 23:11:52'   len=40
  相同前缀 12 字节  -> ' / | \     5'
  相同后缀 20 字节  -> 'Oct 29 2026 23:11:52'
  中段不同：观测 8 字节（其中 7 个非 ASCII/控制字节 09 02 d5 a5 b1 91 81）
             应为 11 字节 '.3.0 build '
```

同一启动流里另有 6 行也是**行内替换式**污染（不是整行丢失）：

```text
line 15  b'[BOQu\x811safe-gpio ....... OK'
line 27  b'[CFG] l\x91\x95\x91\x81O\x81(version 0x00010002 cal_source=1)'
line 29  b'[MOT] init OK (enable-path-validated=YES, arm gate ACTIVE, DRV_ENABLE …'
line 47  b'[SS] self-test done: required=PASS degraded=\xc2\xc10h55\xd5\x05\x9a\x151\x19}Q\x15MQ\x81-> READY'
line 56  b'[Safety] limit_inputs=DISABLED_BY_CO\xca%\x1d\x81(H\x1d!\xf5safe/LOW=trigger when ENA'
line 67  b'T+\x9d   OK verdict=0 mode=MONITOR_ONLY'
```

其余统计：`CRLF=72`、行尾多出一个 `\r` 的 `lone_CR=22`、`NUL=1`。
**`\r\r\n` 不是本轮新现象**：仓库既存的真机启动件
`reports/reviews/phase8-tmc-uart-rootcause/boot_ms2048.txt`（3621 B，历史捕获）同样是 `…\r\r\n` 结尾，
且那份的横幅是**干净的**：` / | \     5.3.0 build Oct 28 2026 17:36:54`。

启动尾部（干净）：

```text
[MAIN] boot complete: READY (motion still gate-locked)
[MAIN] motor arm gate fail mask=0x18 (MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=YES)
```

危险标记独立扫描：`stack is close / stack overflow / SYSTEM_HALT / HardFault / BusFault / UsageFault`
**全部 present=False**。本轮无告警**不等于**栈问题已修（上一轮同样出现过"无告警"覆盖）。

## 5. 机制假设：只列相容项，不择一当结论

**已核实的工程前提（亲读现冻结源码，非推测）：**

- `rt_kprintf` 用**一个 `static char rt_log_buf[RT_CONSOLEBUF_SIZE]`** 共享缓冲
  （[kservice.c:391-422](file:///D:/ns800/rt-thread-master/rt-thread-master/src/kservice.c#L391-L422)），
  本构建 `RT_CONSOLEBUF_SIZE=128`（rtconfig.h:107）。
- 保护该缓冲的 `PRINTF_BUFFER_TAKE/RELEASE` 在 `RT_USING_THREADSAFE_PRINTF` 未定义时是**空宏**
  （kservice.c:324-331 两分支；本仓 `source_audit.txt:178` 与
  `plans/phase8-stop-priority-s2a-design.md` P-8 早就登记过这一事实）⇒ **控制台输出无互斥**。
- 本次启动流里 `msh >` 只出现 1 次，且位置在**偏移 486、夹在 `[BOOT] Phase-7 runtime bootstrap start`
  与 `[SAFETY] init OK …` 之间** ⇒ 打印 shell 提示的线程与 phase-7 引导输出**同时在写控制台**。
- 被替换出来的字节多为 `0x80-0xBF` 形态，与"并发写入把一条 UTF-8 中文行截成碎片"相容
  （紧挨污染点的 `[SAFETY] init OK … 输出安全态已建立` 正是 UTF-8 中文行）。

**相容机制（都未被本轮证明，也都未被排除）：**

1. 目标侧并发 `rt_kprintf` 争用同一 128 B 静态缓冲，造成行内替换/长度缩短；
2. 目标 UART → DAPLink CDC → PC 的链路在复位瞬间丢/错字节；
3. 采集侧读取路径（每块立即 `write/flush/fsync` 后再 `read`）造成缓冲溢出或读窗口损失。

**为什么不能择一：** 三者对"前 12 字节与后 20 字节完好、中段 11→8 字节"都能给出解释，
而本轮只有**一次** COM5 会话（额度已耗），没有同一条流的第二次采样可与既存 PowerShell 采集件对比，
也没有把控制台串行化后的对照。所以本文只登记机制清单，不写根因。

**两个能真正判开的下一步（都需要新的批准，本轮没做）：**

- A：在**不改动已接受采集器字节**的前提下，同一板子用既有历史采集工具（PowerShell 那条链）
  再取一次启动流比对污染位置——只新增一次 COM5 打开与一次复位，须另行批准。
- B：把 `RT_USING_THREADSAFE_PRINTF` 打开（或给 `_kputs` 加自旋锁）后重编一个对照镜像再取一次——
  这是产品/构建动作，本轮明确禁止，只能作为下一轮提案。

## 6. 板终态与现场

- 串口已由采集器正常关闭（`PORT_CLOSED`）。采集器全程未发送任何命令 ⇒ 板上未收到任何字节。
- 电机：本轮无任何运动命令，也未做使能/撤能动作；用户指令"不转电机"得到遵守。
- 板上镜像 = 已回读证明的 M7-R1 motor `e614a075…f7da`（266244 B 逐字节相等）。
  这是本轮**新增的已证事实**：M7-R1 载荷第一次被写进板子并逐字节验证。
- 启动可见状态：`READY`、`fault_code` 未取（未发命令）、`arm gate fail mask=0x18`、
  `verdict=0 mode=MONITOR_ONLY`（该行有污染，只作可见文本登记，不当判据）。
- 未碰供电/接线/SW1/VREF/VM/相线；未做万用表测量；未索取断电回报。

## 7. 数据可用性判定

- 无 `result.json`、无 sample128、无 DMM 读数 ⇒ **P0 业务目标未取得任何数据**。
- 因此**不登记** `VOLTAGE_OBSERVATION_ONLY`（那是执行成功路径的上限标签，本轮没走到）。
- 已证事实只有一条：M7-R1 motor 镜像逐字节在板（`0x08000000` 起 266244 B 回读全等）。
- 额度已用尽的部分：下载 1/1、回读 1/1、COM5 1/1、显式复位 1/1；剩余命令额度**未被使用**，
  但整包批准已消耗——继续执行需要新的一次批准。
