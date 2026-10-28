# URO-R2 板级执行轮交接（task phase8-sensor-stack-uart-r2-board）

执行包：`reports/reviews/phase8-sensor-stack-uart-r2/board_package_r2.md`（bytes=10082，sha256=`e09078901265d957fdef223545e165544f83f530d9ba83e353362090b4a4c934`）
批准：用户真实回合简写「批准执行 URO-R2 板级包」（2026-10-28 12:0x +0800，绑定包 §6）
交付/复审基线：`2053e6adc7d71d892d138754a3955fe91ac31343`　产品软件输入：`139e9377ccbc36b878cd577a6b848d974e1e07fa`
执行时刻：2026-10-28 11:58:54 → 12:01:24 +0800　分支：`phase8/tmc-drive-config`

**一句话结论**：新镜像（sensor 栈 4096）已下载并**逐字节回读证实在板**、复位后确在运行；
但**同一条 sensor 栈告警仍然立即出现**，R2 采集器按新设计"先落盘、再判危险"精确停在
`STACK_DANGER_OBSERVED`，**七条命令一条未发**。TMC 只读诊断仍未取得，链路仍 `UNPROVEN`。

## 1. 逐步结果

| 步 | 判据 | 结果 |
|---|---|---|
| S0 离线绑定 | 四件产物长度+SHA、副本工程与已证源逐字节相同、Load 解析、pyOCD 无隐式配置、UID 只取私有件 | **PASS**（零设备动作） |
| S1 供电 | USER_CONFIRMED 沿用，未新增上电 | **无动作** |
| S2 一次下载 | Load 本轮载荷 + `Erase Done.` + `Programming Done.` + `Verify OK.` | **PASS**（12:00:12；`Start-Process -Wait` 修好了上一轮的判定过早缺陷，该步 rc=0） |
| S3 定长回读 | `0x08000000` 起 `0x41834` = 268340 B，两次 `Halted` | **PASS**：sha256 与接受 BIN 相同且**逐字节相等** |
| S4 唯一显式复位 | `Resetting target` + `Running` | **PASS** |
| S5 一次 COM5 | R2 采集器 + 新横幅 + Image/Site 确认 | **FAIL（危险门）**：`STACK_DANGER_OBSERVED`（UARTReadDiagSerialR2.ps1:54），开口 0.022 s 即判 |
| S6 七条查询 | 各 ≤1，probe ≤3 读、0 WRITE | **NOT_REACHED**（0/7） |
| S7 停机归档 | 保存原字节、关句柄、记实际消耗 | **DONE**（停机后设备动作 0） |

额度：下载 1/1、DAP 2/2、回读 1/1、显式复位 1/1、COM5 1/1 **用尽**；msh 0/7、probe 0/1、
TMC 读事务 0、WRITE 帧 0、新增上电 0、枚举 0。Keil 隐含初始化/复位记 `NOT_SEPARATELY_OBSERVABLE`。

## 2. 本轮最重要的板上事实

`SENSOR_THREAD_STACK=4096` 的镜像**已证在板运行**（回读逐字节相等 + 反汇编里 `MOV r3,#0x1000`），
复位后开口的第一批字节里仍是
`[W/kernel.sched] warning: sensor stack is close to end of stack address.` —— 即**加大到 4096 没有让告警消失**。

这直接否定了上一轮离线推断的**充分性**（"静态已知最深链 1228 B > 旧配置 1024 B 就是这件事的解释"）：
那个不等式仍然成立（说明 1024 确实不够），但它不是"给到 4096 就该安静"的理由。

源码语义（本轮实读，非推测）：`stack_addr` 是分配块**低端**，整块 memset `'#'`，向下增长 CPU 初始
`sp` 在块高端（`src/thread.c:204-219`）；检查在**切入线程时**用其保存的 `sp`（`src/scheduler_up.c:355`
→ `src/scheduler_comm.c:460`）；`sp <= stack_addr+32` 打本轮这句告警（`:507-512`），而溢出分支
（栈首字节非 `'#'` 等）打的是另一句 `stack overflow`（`:477-486`）且本轮**未出现**。
=> 字面含义是该线程切出瞬间用掉约 4064 B 以上，而最底部第 1 字节仍未被写过。

两个候选解释本轮**没能区分**，也不选一个写死：
H1 真实用量远超 armlink 静态度量（镜像级值自带 `+ Unknown(Cycles, Untraceable Function Pointers)`，
函数指针与互递归不参与上界，异常硬件压栈也不计）；
H2 该检查对这条线程给假阳性（若如此，加栈永远不会让它安静）。
区分需要一次新的读取动作（读该线程 sp/stack_addr 或 '#' 高水位），本包额度已尽且明令不追加，故记未知。

## 3. R2 采集器改动的实际效果（这是本轮唯一"按设计成功"的部分）

| 设计意图（任务书 §4/§7） | 本轮是否兑现 | 证据 |
|---|---|---|
| 不再要求 1200 ms 全流静默 | 是 | 0.022 s 就得到判定（上一轮是 20 s 超时） |
| 原字节先落盘，之后才判危险 | 是 | 166 B 已写入 `001_attach_passive.rx.bin`，`session.rx.bin` 同散列 |
| 真实危险流不为发命令而放行 | 是 | 抛 `STACK_DANGER_OBSERVED`，`counts={}`，0 条命令，未重开口 |
| 停点身份可与上一轮区分 | 是 | 新文本 :54 vs 上一轮 :139，两个不同 FAILURE_STAGE |

主任务目标仍未达成：`tmc_uart_probe` 的逐笔读观测**没有取得**；`-117` 根因未知；TMC 链路 `UNPROVEN`。

## 4. 交付物

- `reports/reviews/phase8-sensor-stack-uart-r2-board/board_run_evidence.txt` —— S0–S5 原始件解码、
  下载日志、回读逐字节比较、复位原文、采集台账、166 B 原字节（base64 内联 + `session==被动块` 证明）
- `reports/reviews/phase8-sensor-stack-uart-r2-board/stack_warning_analysis.txt` —— 源码语义、
  被否定说法、两个候选解释、"本轮不能说的话"
- 本交接 `.md` / `.json`；上一份 `phase8-sensor-stack-uart-r2.md/.json` 只追加指针

脱敏：绝对路径 -> `<本地执行根>`/`<仓库根>`（有损，原件留仓外未删）；UID 只登记长度 48 与
`sha256(uid)[:12]=d2f530dbd062`；控制字节在证据文本里一律写成 `<ESC>`/`<NUL>` 形态；行尾空白已剥离。
来源分层：本轮所有板级条目 = **LOCAL-RUN**（本机 Windows 实跑），云端未执行任何一步。

## 5. 停点标签

```text
URO_R2_BOARD_READ_DIAG_EXECUTED_OR_STOPPED = EXECUTED_THEN_STOPPED
IMAGE_ON_BOARD = CONFIRMED(byte-identical readback of b1e85294... + reset Running)
SENSOR_STACK_WARNING_OBSERVED = TRUE(at 4096; 22ms after port open)
CAPTURE = INCOMPLETE(NOT_REACHED for the 7-command sequence)
BUSINESS = NOT_REACHED
FAILURE_STAGE = STACK_DANGER_OBSERVED(collector danger gate; raw bytes saved first)
BANNER_GATE = NOT_EVALUATED(danger check precedes it)
ECHO_MATCH_OFFSET = KNOWN_INVALID_FOR_DIAG
SAFE_FOR_ENABLE = false / FOUR_SAFETY_MACROS=0 / NO MOTOR / NO STEP_ENABLE / NO WRITE FRAME
TMC_LINK_ON_BOARD = UNPROVEN
STACK_4096_SUFFICIENCY = DISPROVED_AS_SUFFICIENT (warning persists; root of warning UNRESOLVED)
QUOTAS_EXHAUSTED = download 1/1 + DAP 2/2 + readback 1/1 + reset 1/1 + COM5 1/1 + msh 0/7
DEVICE_ACTIONS_AFTER_STOP = 0 / NO_RETRY / NO_AUTO_CARRYOVER
NEXT_ROUND_REQUIRES_NEW_USER_APPROVAL
```

## 6. 下一步需要的判断（不在我这边，且需要新批准）

本轮把问题从"采集器要静默"推进到"sensor 线程在 4096 下仍几乎用满栈，而静态度量解释不了这个量级"。
继续之前需要一次新的决定，可选方向（本轮一律未执行、也不预选）：

1. 一次**只读诊断**的新现场包：读该线程 `sp`/`stack_addr`/`stack_size` 与 '#' 高水位，用来在 H1/H2 之间定案；
2. 或先按 H1 继续加栈并复测（但这会重复"改常量再烧一次"的路线，且在 H2 成立时必然无效）；
3. 或先修采集器/内核告警的观测口径（属产品/内核侧改动，需重新走复审）。

同时仍然悬着：`tmc_uart_probe` 的逐笔读观测未取得，TMC 链路 `UNPROVEN`，`-117` 根因未知；
`BLOCKED_ROLLBACK`、B-1/BUG-008/RISK-03/03a/物理 RISK-04-b/M-1/M-2/M-3 按原范围保留，A0 时长 `NOT_ACHIEVED`。


### 8. 第 2 轮（用户指令重烧，2026-10-28 12:24–12:27）

用户在我说明"重烧同一镜像不会改变结果"之后仍指令**重新烧录一次**，经选项确认为"重烧 4096 再跑采集"，
据此按**新的第 2 轮擦写/串口授权**执行：下载 1/1（`Verify OK.` @12:26:01）、定长回读 1/1
（268340 B 与 `b1e85294…` **再次逐字节相等**，第二次独立证明）、DAP 2/2、唯一显式复位 1/1（Running）、
COM5 1/1。私有 runner 参数化为 `-Round`，run2 的日志/回读/采集全部写新路径，第 1 轮证据未被覆盖。

**第 2 轮停在另一个身份上**：这次抓到的 240 B 启动流里有**逐字匹配的横幅** `5.3.0 build Oct 28 2026 10:23:50`
（新镜像第一次在串口上自证身份），随后是启动头与残行 `stages: 1 safe-hw /`，**之后 20 s 内再无任何字节**——
没有 `[BOOT]` 阶段完成行、没有 `[MAIN] boot complete`、**没有任何 sensor 栈告警**、没有提示符。
采集器因此报 `BOOT_WINDOW_INCOMPLETE`（:135），同样先落盘、同样 0 条命令发出。

同一块板、同一份镜像、同一次复位流程，两轮走到完全不同的位置：一轮跑完启动并持续刷栈告警，
一轮在启动头部之后停住。由此两条口径必须钉住：

- 第 2 轮"告警没出现"**不能**当作栈问题已解决 —— 那一轮根本没走到 sensor 线程所在的阶段；
  反过来也不能说第 1 轮的告警是偶发。第 1 轮的 `STACK_DANGER_OBSERVED` 与本轮的停住**并列保留**。
- 第 2 轮"之后没有字节"不是我们读晚了造成的：两轮复位→开口的间隔几乎相同（43.9 s vs 39.5 s），
  缓冲内容却分别是"只剩最新 166 B 告警"与"完整保住最前 240 B 横幅"，唯一自洽读法是第 2 轮自复位起
  总共只产出了 240 B。因此停住是真的，机制仍不能定（固件卡在该阶段 vs 继续跑但控制台静默），
  区分需要一次新的读取动作，本轮未做也不预选。

TMC 仍未取得：`probe` 0 发出、链路 `UNPROVEN`、`-117` 根因未知、栈告警持久化原因未定（H1/H2 未区分）。
证据见同目录 `round2_run_evidence.txt`（含两轮逐项命中对照表与 run2 原字节内联归档）。
