# URO-R2 交接 — sensor 栈与 UART 就绪门合并修复（离线轮）

任务书：`NS800_Sensor_Stack_UART_R2_Fix_Task.md`（bytes=20321，sha256=`2bbf84ca…f4cfd7`）
本轮锚点：`afa922b934f600e4b3301d87df16a99948610a10`　产品软件输入提交：`139e9377ccbc36b878cd577a6b848d974e1e07fa`　上一轮已复审交付：`66f85330…`
分支：`phase8/tmc-drive-config`　日期：2026-10-28（北京时间）

**一句话结论**：sensor 线程栈 1024→4096 已改并构建出两枚新镜像；R2 采集器按任务书附录 A 原样生成并通过
Windows 5.1 完整自检与附录 B 六项（输入就是上一轮那条真实 23845 B 流）；**本轮零设备动作**，
续测包已填实等待用户一次批准。上一轮"0 条命令发出"的停点没有被改写。

## 1. 交付物

| 件 | 身份 |
|---|---|
| `reports/reviews/phase8-sensor-stack-uart-r2/UARTReadDiagSerialR2.ps1` | 92860 B / `9ed8a3e342f7cad8430c42140ffb508d6cc0694e251636c1b93ab2a2a7390a37` |
| 同目录 `make_r2.py` / `test_r2.ps1` | 附录 A/B 原样字节（3327/`11e8aa34…`、4125/`a9090ad8…`） |
| 同目录 `offline_verification.txt` | 身份链、构建、栈报告、采集器差异、Windows 实跑、首失败 |
| 同目录 `stack_report.txt` | armlink 调用图度量 + AXF 全文反汇编前后对照 |
| 同目录 `board_package_r2.md` | 填实的续测包（新散列/长度/横幅/额度/批准文字） |
| `reports/firmware/phase8-sensor-stack-uart-r2/{default.bin,measure-a0.bin,build_manifest.json}` | 见下表 |

| 镜像 | 字节 | SHA-256 | 横幅 |
|---|---:|---|---|
| measure-a0.bin | 268340 | `b1e852949f003965de52d539ca324a11b8b89e17a51a8f34fe3efd01525ae2d1` | `5.3.0 build Oct 28 2026 10:23:50` |
| default.bin | 258036 | `db5db20bb8d6eae1e8e5bed9dd0b70f5ad5c818a6cb3db9333496e0280aebe2d` | `5.3.0 build Oct 28 2026 10:23:37` |

本地 AXF（A0，不入库）= 1693804 B / `15be5f3efc45bf296bc0ef24e3947081372616d819e4bb3b189e9614cf9a5b9a`。

## 2. 产品改动与它的证据边界

唯一改动：`applications/sensor_service.c` 的 `SENSOR_THREAD_STACK` 1024→4096
（修前 blob SHA-256 `5b48e0e6…`，修后 `198ac05e…`，与任务书 §3 声明逐字一致；提交 `139e9377ccbc`）。

- 静态已知最深链 `sensor_thread_entry` = **1228 B**，旧配置 1024 B 装不下它 —— 这是上一轮 20 s 内
  243 次告警的量化解释；新配置对该已知链留 2868 B 余量。
- 镜像级 = `Maximum Stack Usage = 2996 bytes + Unknown(Cycles, Untraceable Function Pointers)`。
- **4096 不是已证明的完整最坏栈界**：报告显式带 Unknown（Cycles + Untraceable Function Pointers），
  调用图里存在互递归段，且异常硬件压栈不在静态度量内。未据此上调 8192，因为报告没有显示 4096 不足。
- 全镜像前后对照（同目标 A0，fromelf 全文反汇编 117145 行）只有 **4 行**不同：
  1 行是 `MOV r3,#0x400`→`#0x1000`（rt_thread_create 的 stack_size 实参），3 行是 `__TIME__` 数据串。
- 堆分配，不是静态数组：动态堆增量 **3072 B**；四项 Program Size 增量为 0 与此一致（不是"没生效"）。
- 没做也不做：关 `RT_USING_OVERFLOW_CHECK`、隐藏 `LOG_W`、停 sensor、降采样、加延时造静默。

## 3. R2 采集器改了什么（以及没改什么）

由附录 A 从冻结 R1（93797 B / `2e9c8ede…`，取自 `git show afa922b:<路径>` 的 LF blob）生成，输出
92860 B / `9ed8a3e342f7…`，与任务书 §4 期望一致。

- 首查询**不再要求 1200 ms 全流静默**；保留 ≥2 s 被动接收、已见 BOOT 未完成时最多 20 s、横幅/镜像身份门、
  单次首查询；新失败身份 `BOOT_WINDOW_INCOMPLETE`。
- `Drain-Rx` 单次最多 16384 B 后交回外层计时循环（有限批次，不是 WCET 证明）。
- 真实危险流不因"想发命令"而被放过：原字节先落盘，再判 `STACK_DANGER_OBSERVED`（含跨块告警）。
- 其余判据未删：提示符/120 ms 尾部、TMRO 完整性、业务/完整性分层、横幅门、七条命令与额度、禁止命令、
  finally、串口参数。实算证明：把有意替换的两段与新增函数整段摘除后，R1 与 R2 的剩余文本
  **90601 B 逐字节相等**。
- 有意的头部差异：附录 A 从 `[CmdletBinding()]` 起重建文本，R1 的 1784 B 前置说明注释被换成 228 B 三行头
  （生成器规定行为，谱系声明仍在 R1 原文件与本轮证据里）。
- CLI 名 `ExecuteApprovedR1ReadDiag` 仅为参数名保留，不代表继承上一轮批准。

## 4. 离线验证结果

| 验证 | 结果 |
|---|---|
| PS 5.1 ParseFile | PARSE_ERRORS=0（PS 5.1.26100.5074） |
| R2 `-SelfTest`（旧 3757 B / `97b37f32…` 启动夹具，全新 OutDir） | rc=0、stderr=0；台账 `NEGATIVE_CONTROL=51`（唯一具名 51）、`SLICE_CONTROL=9`、7 条 mock 命令、9 个 RX_FILE；旧汇总串 `negative_controls=50` 并列保留 |
| 附录 B 六项（输入=本次真实 23845 B 流） | 6/6 PASS、stderr=0、`serial_objects=0`；含"持续正常背景输出也能到首查询调度点""单次 Drain 恰读 16384 B""真实告警流先落盘再拒""按单字节分块的告警""明确 overflow""BOOT 未完成 20 s 期限无 TX" |
| S1 主机链 | target 50/50、host_ovr 65/65 全 PASS，runner rc=0 |
| S2 主机链 | target 53/53、host_ovr 109/109 全 PASS（read_obs 各 30/30），18 组台账自洽，runner rc=0 |
| Keil 两目标 Rebuild All | 各 0 Error / 0 Warning；真实 Program Size 见 §1 表 |
| 四宏 | 目标级 Define 无覆盖；ARMCLANG `-E -dM` 实证四宏均为 `RT_FALSE`（=0），两目标同 |
| 首失败保留 | 附录 B 第一次运行（`OutRoot=r2/apb_run1`）因本 Agent 的解码脚本把"拼接校验"段落当 base64 读入，没写出 `-Wire` 文件，前两例 PASS 后第三例 `FileNotFoundException` 退出 rc=1；修解码后在新 OutRoot 得 6/6；旧目录与两份输出原样保留 |

这些是**离线采集器与离线构建**验证，不是板上栈修复成功，也不是 TMC 通信成功。

## 5. 上一轮事实的继承（不改写）

上一板级轮（`phase8-tmc-uart-read-observation-r1-board`）：下载/回读/唯一复位均成功，新镜像
`a48400c8…` 已逐字节证实在板；采集器停在 1.2 s 静默就绪门，`CAPTURE=INCOMPLETE`、`BUSINESS=NOT_REACHED`、
七条命令 0 发出，额度全部用尽。本轮**没有**重跑任何设备动作，也没有用旧包续额度。

## 6. 边界与未证

- 板上 sensor 栈告警是否消失：未取得（本包才会知道）。
- 板上 TMC 链路状态：仍 `UNPROVEN`；`-117` 根因未知；本轮不支持也不排除任何假设。
- 完整最坏栈界、完整栈高水位：未证明。
- `echo_match_off`：`KNOWN_INVALID_FOR_DIAG`；`safe_for_enable=false`；四个安全宏仍 0。
- `BLOCKED_ROLLBACK` 剩余项（恢复下载未演练）、B-1/BUG-008/RISK-03/03a/物理 RISK-04-b/M-1/M-2/M-3
  按原范围保留；A0 时长仍 `NOT_ACHIEVED`。
- 用户已确认供电/接线/扩展板正常，ENN 不作为本轮查因方向；不重索取照片、供电材料或例行断电回报。

## 7. 下一步（不在我这边）

1. 用户按 `board_package_r2.md` §6 批准（或简写"批准执行 URO-R2 板级包"）。
2. 批准后按 S0→S7 执行；任一步真实失败、前置失效或额度耗尽即停，保存原字节、关句柄、记实际消耗。
3. 本包不预测结果：告警消失只支持本次覆盖路径观察；`-117` 若仍在，按本次 TMRO 真实字段查因。

```text
URO_R2_OFFLINE_FIX_AND_PACKAGE_DELIVERED / HARDWARE_ACTIONS_THIS_ROUND=0
SENSOR_THREAD_STACK=1024->4096(dynamic heap +3072 B, static known depth 1228 B)
R2_COLLECTOR=9ed8a3e342f7… / 92860 B / APPENDIX_B=6-6 / SELFTEST=rc0-NEG51-SLICE9
NEW_IMAGES: measure-a0=b1e852949f00… default=db5db20bb8d6… EXPECTED_BANNER='5.3.0 build Oct 28 2026 10:23:50'
HOST_CHAINS: S1 50-65 PASS / S2 53-109 PASS(read_obs 30-30) / KEIL=0E-0W both
FOUR_SAFETY_MACROS=0 / SAFE_FOR_ENABLE=false / TMC_LINK_ON_BOARD=UNPROVEN
AWAITING_USER_APPROVAL / NO_NEW_HARDWARE_ACTIONS / ECHO_MATCH_OFFSET=KNOWN_INVALID_FOR_DIAG
A0_DURATION_EVIDENCE_NOT_ACHIEVED / BLOCKED_ROLLBACK_OPEN / M2_M3_NOT_AUTHORIZED
```

## 8. 推送后逐 blob 远端独立回读（只读，零设备动作）

远端分支头 = `2554025146f65396266f669206d7ce7812c75d7a`；本轮两个提交：`139e937`（产品软件输入，镜像按它构建）+ `2554025`（证据/镜像/交接/包）。
方法：`gh api .../contents/<path>?ref=HEAD -H Accept: application/vnd.github.raw` 取远端实际字节，
与 `git show HEAD:<path>` 的 blob 字节同时比 **长度与 SHA-256**。

| 件 | bytes | sha256 | 判定 |
|---|---:|---|---|
| `sensor_service.c` | 17734 | `198ac05e2ab8…` | MATCH |
| `UARTReadDiagSerialR2.ps1` | 92860 | `9ed8a3e342f7…` | MATCH |
| `make_r2.py` | 3327 | `11e8aa34460e…` | MATCH |
| `test_r2.ps1` | 4125 | `a9090ad86065…` | MATCH |
| `offline_verification.txt` | 15541 | `93c509a8c059…` | MATCH |
| `stack_report.txt` | 4807 | `2c32f4404932…` | MATCH |
| `board_package_r2.md` | 10082 | `e09078901265…` | MATCH |
| `measure-a0.bin` | 268340 | `b1e852949f00…` | MATCH |
| `default.bin` | 258036 | `db5db20bb8d6…` | MATCH |
| `build_manifest.json` | 24583 | `9c3b492a4f74…` | MATCH |
| `phase8-sensor-stack-uart-r2.md` | 8244 | `c383491b44ea…` | MATCH |
| `phase8-sensor-stack-uart-r2.json` | 9492 | `5ce93470bf37…` | MATCH |
| `phase8-tmc-uart-read-observation-r1-board.md` | 21929 | `b14fd6e63bbd…` | MATCH |
| `phase8-tmc-uart-read-observation-r1-board.json` | 22608 | `e6d74440e2cc…` | MATCH |

A 组 **14/14 MATCH**。

应不变对象（跨头核对）：

- `tmc2209.c` 与 `66f8533` 上的 blob **SAME**（`8a5e03082245…`）
- `leaf_main.c` 与 `66f8533` 上的 blob **SAME**（`6a03b884e7b6…`）
- `run_stop_priority_s2.sh` 与 `66f8533` 上的 blob **SAME**（`0d405b01c886…`）
- `measure-a0.bin` 与 `66f8533` 上的 blob **SAME**（`a48400c881c5…`）
- `default.bin` 与 `66f8533` 上的 blob **SAME**（`dccb7b63c928…`）
- `build_manifest.json` 与 `66f8533` 上的 blob **SAME**（`075bd3ff6eac…`）
- `UARTReadDiagSerialR1.ps1` 与 `66f8533` 上的 blob **SAME**（`2e9c8ede8f75…`）
- `board_run_evidence.txt` 与 `afa922b` 上的 blob **SAME**（`b271a5e5892f…`）
- `passive_window_analysis.txt` 与 `afa922b` 上的 blob **SAME**（`10a248b5fa98…`）
- `passive_capture_b64.txt` 与 `afa922b` 上的 blob **SAME**（`8bd705b585e1…`）
- `s0_offline_binding.txt` 与 `afa922b` 上的 blob **SAME**（`7c36aca3ad8a…`）
- `execution_package.md` 与 `afa922b` 上的 blob **SAME**（`8c399e50e5a8…`）
- `commands.expanded.masked.ps1` 与 `afa922b` 上的 blob **SAME**（`699b759a3c41…`）

B 组 **13/13 SAME**：产品 `tmc2209.c`、夹具、runner、R1 两镜像与 manifest、冻结 R1 采集器，
以及上一板级轮的全部证据与 S0 三件都未动。

冻结引用（与 `-board` 交接 §7.7 逐项相同）：

```text
remote main                        1b3757eec550802a3973e66790af422d81d3d373
remote phase7/full-embedded-software 6d3fbc559d45673d7550eb73dec5a4ade5bb34df
tag v0.7-phase7-software-board-validated   097935fec5a6f2467261ab1ad449ff0b1d398250
tag deref                              e40a9b95ef709e6a80444f9f3949dfdd70ba96ba
本轮改动面                       仅 14 个文件（1 产品 + 6 证据/包 + 3 镜像 + 2 新交接 + 2 指针追加）
```

自指口径：表内两份交接文件与本节自身同源，登记本节会改变这两个 blob，所以最终头上这两件
在推送后另行复验（见回执），不在文件内递归登记。


---

## 9. 板级执行轮指针（2026-10-28 12:01，URO-R2 包已执行并按包停机）

本包已获用户真实回合批准并在本机实跑：下载、定长回读（`b1e85294…` 与板上 `268340` B **逐字节相等**）、
唯一显式复位均 **PASS** —— 新镜像确在板运行。R2 采集器按新设计**先把 166 B 原字节落盘、再判危险**，
0.022 s 内命中并抛 `STACK_DANGER_OBSERVED`，**七条命令一条未发**。

关键结果：**栈加到 4096 没有让告警消失** ⇒ 本文 §2 的"静态已知最深链 1228 B > 旧 1024"作为解释的
**充分性被否定**（不等式本身仍成立）。告警持久化的原因未定案，两个候选（真实用量远超静态度量 /
该检查对这条线程假阳性）需要一次新的只读诊断才能区分，本轮未做也不预选。

本文（`phase8-sensor-stack-uart-r2.md`）§1–§8 的离线交付内容与数字**原样保留**，不回写；
执行结果与停点见 `phase8-sensor-stack-uart-r2-board.md/.json` 与
`reports/reviews/phase8-sensor-stack-uart-r2-board/`。本轮设备动作已全部用尽于批准额度，
停机后 0 新增；下一步需要用户对一份新方案给一次新的批准。
