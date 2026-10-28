# SR 交接 — 现有 R2 镜像的单次栈 RAM 诊断包

任务书：`NS800_Stack_RAM_Diagnosis_Task_7380856.md`（bytes=11829，sha256=`cd8e72d8b6df0a272755ef9b67830d95274c7bb1784404306da6c6edb4905d87`）
本包 Git 锚点：`7380856149752506286714e9e82eda6e205abb40`　离线轮开工分支头：`7a6e31efb3b5b4391299d2ac3db86742e8e65113`
R2 软件输入：`139e9377ccbc36b878cd577a6b848d974e1e07fa`　分支：`phase8/tmc-drive-config`　日期：2026-10-28（北京时间）

**一句话结论**：按用户一次批准执行了 **1 个目标会话**；sensor TCB 身份四门全过，4096 B 栈与元数据已取回并复读一致；
但**本快照没有捕获告警状态**（保存 SP 距栈底 3684 B、历史最深约 996 B），故对“4096 是否足够/告警成因”的
解释等级为 **INCONCLUSIVE**。结束 **Halted**；写/下载/复位/串口/msh/TMC 均为 0。

## 1. 时间线（离线轮与批准轮分开记账）

1. 离线轮（批准前）：完成绑定、反汇编核对、工具与八组 dry-run、证据与交接，提交 `597a051`，停在批准门。
2. 用户给出批准全文（「批准执行本文栈 RAM 诊断包…」），本文件以下内容为批准后的执行结果。
3. 设备执行：2 次构造但**未 open**的失败（见 §8），1 次打开会话并完成读取；额度未超。

## 2. 交付物（字节与 sha256 由脚本对本地文件复算）

| 件 | 字节 | sha256 |
|---|---:|---|
| `reports/reviews/phase8-sensor-stack-ram-diagnosis/stack_ram_diag.py` | 29997 | `1a62fc4dc3381777b21fb700c15fe31536ee2fb9527325cc91bd74407c51ee17` |
| 同目录 `binding_report.txt` | 16693 | `91fb6516eb4059440bbe43e657b5536e6679b108eca166da82a7befb51735dc0` |
| 同目录 `dryrun_matrix.txt` | 4593 | `d269599825760e6530b2016ee545f333f863356bc344b8bb26a65120ba2398c4` |
| 同目录 `live_package.txt`（执行包存档） | 3969 | `a9d94a41052e08ab74fa1ff65d8d16c01cf2b64d731b5d7ef8e5ed7484d43bd7` |
| 同目录 `live/live_report.txt` | 4107 | `294142dcb9987b7676847de9e1ded2e4befae6d435474f08727f454b23224d6b` |
| 同目录 `live/{s_tid,tcb,cpu,stack,s_tid_reread,tcb_reread,ccr,dtcmcr}.bin` + `result.json` + `session.log` | 14961 合计 | 见 `items_manifest.txt` |
| 同目录 `items_manifest.txt` | 2080 | 自散列不写入自身；以 git blob 与远端回读为准 |

公开副本已脱敏：无 UID、无本机绝对路径、控制字节按文本记；原始私有件保留在仓库外执行目录。

## 3. 设备执行结果（单会话，原始件见 `live/`）

| 项 | 实测 | 判定 |
|---|---|---|
| 初始状态 | Running | — |
| halt | 显式 1 次 → Halted | **PASS（额度 1/1）** |
| s_tid | `0x20014098`（窗口内、已对齐） | **PASS** |
| TCB | `0x20014098`，name=`sensor`，type_byte=1，entry=`0x08020e19`，stack_size=4096，base=`0x20014150` | **PASS（四门）** |
| 栈 | 4096 B @ `0x20014150`；stack[0]=`0x23`；底部连续 `'#'` 3100 B；首个非 `'#'` 偏移 3100；最深估计 996 B | **PASS（读全）** |
| 保存 SP | `0x20014fb4`；saved_free=3684 B；saved_used=412 B | — |
| 告警/溢出带 | in_warning_band=**false**；in_overflow_band=**false**；fill_vs_sp_consistent=**true** | — |
| 同会话复读 | s_tid 与 TCB 均逐字节相同 | **PASS** |
| 读取总量 | 8 次 / **4456 B**（≤16384） | **PASS** |
| 核心寄存器 | 1 组 25 个；mvac 不可用记 `unavailable(KeyError)`，未补零 | **PASS** |
| CCR | `0xE000ED14` = `0x00070200` → DCache/ICache 均启用 | **PASS（4 B）** |
| DTCMCR | `0xE000EF94` = `0x00000041` → bit0(ENABLE)=1；其余位不解释 | **PASS（4 B）** |
| 结束状态 | Halted，`resume_on_disconnect=False`，未 reset、未再连接验证 | **PASS** |
| 写/下载/目标 Flash 读/复位/串口/msh/TMC | 0 | **PASS** |

## 4. 解释（按任务书 §5 的表，不越级）

- `_cpu.current_thread = 0x20009978 ≠ sensor TCB`：本轮 halt 落在**另一个线程**上；sensor 的 SP 是它上次被切换出去时的保存值，不是即时 PSP。
- 保存 SP 正常（距栈底 3684 B，当前使用 412 B），且填充未接近底部（历史最深约 996 B）→ 对应任务书表第 4 行：
  **本快照没有捕获告警状态**；不否定先前真实告警，也不据此把 H2（检查本身有问题）定案。
- 上下文：CONTROL=0x2（线程模式用 PSP），PRIMASK=1；PC=`rt_hw_interrupt_enable+0x0`，LR=`rt_spin_unlock_irqrestore+0x21`；r4–r11=`0xdeadbeef`（原样记录，不解释）。
- **缓存限制**：栈与 TCB 位于 `RW_RT_HEAD` 区 `[0x20012000,0x2001A000)`（可缓存 SRAM，非 DTCM 段），CCR 显示 D/I Cache 均启用；本轮未做也未获准做 cache clean/invalidate。故 `'#'` 填充只能作指示，**不能**据此宣布“从未写过”或“告警是假阳性”。
- 本轮对 4096 是否足够的解释等级：**INCONCLUSIVE**。最有证据的下一步（本包不执行、不自动扩权）：在**告警发生的上下文**取样（在切换点重复抓 sensor 保存 SP/PC），而不是继续加栈。

## 5. 绑定与指令事实（离线，不变）

| 对象 | 值 |
|---|---|
| `s_tid` | `0x2000a480`（CU `sensor_service.c`，DWARF 定义数=1） |
| `_cpu` | `0x200008d0`（CU `cpu_up.c`，定义数=1） |
| `sensor_thread_entry` | low_pc `0x08020e18` / 期望 TCB entry `0x08020e19` |
| `rt_thread` | 168 B；sp=28 entry=32 stack_addr=40 stack_size=44 |
| CCR / DTCMCR | `0xE000ED14`(偏移 20) / `0xE000EF94`(偏移 660)，DWARF 与 CMSIS 注释双源一致 |
| 检查函数 | `0x0801ad80`，94 条指令；判据用 TCB 保存的 sp；告警带为栈底 32 B；唯一 BL 调用点 `0x0801acc6` |
| 窗口 | `[0x20000000, 0x20020000)`；HEAP `[0x20012000,0x2001A000)` |

## 6. dry-run 与工具（定稿字节）

八组夹具（任务书要求的三类全覆盖 + 五组附加）全部符合期望，越界/错名/错入口均**读前**拒绝，最大 4456 B。
对 `contradict` 只记 `fill_vs_sp_consistent=false`，不把自相矛盾快照当结论。工具 sha256=`1a62fc4d…`。

## 7. 上一轮事实的继承（不改写）

`STACK_DANGER_OBSERVED`（R1 / R2 第 1 轮，0 条命令）与 `BOOT_WINDOW_INCOMPLETE`（R2 第 2 轮，0 条命令）并列保留；
`STACK_4096_CHANGE_DID_NOT_CLEAR_WARNING=true`。第 2 轮的无告警只是窗口未完成，不是修复证据；第 1 轮的告警也不能当“4096 不足已证明”。

## 8. 本轮首失败（保留，不重跑补救）

1. **第 1 次 live 尝试**：`TargetSupportError: Target type stm32f103rb not recognized`。原因：未显式指定 target，DAP 默认 target 名在本机无支持包。会话在构造阶段失败，**未 open**，0 halt / 0 读 / 0 写。
2. **第 2 次 live 尝试**：`KeyError: 0`。原因：`session_with_chosen_probe` 返回惰性会话，脚本未调用 `open()` 就取 `primary_core`，核列表为空。同样**未 open**，0 halt / 0 读 / 0 写。
3. 修正（离线）：显式 `target_override=cortex_m`、`halt_on_connect=False`、`sess.open()`、改取 `selected_core`、记录 cores 数量与 traceback 尾部；随后重跑八组 dry-run 再执行第 3 次。
4. 另有离线证据缺陷 6 项（匿名 SCB 绑定、ok 夹具自相矛盾、按助记符归类误标、manifest 自散列、合计式算错、contradict 标记位置）均已在离线轮修正并保留记录。

## 9. 停点标签

```text
STACK_RAM_SNAPSHOT = OBTAINED (NOT_IN_WARNING_STATE)
EXPLANATION_LEVEL  = INCONCLUSIVE
FINAL_CORE_STATE   = HALTED
FINAL_CORE_STATE_NOTE = 断开时 resume_on_disconnect=False；未再连接复核
HARDWARE_ACTIONS   = 1 目标会话（另 2 次构造未 open）/ 1 halt / 8 read(4456 B) / 1 寄存器组(25) / CCR+DTCMCR
DEVICE_WRITES      = 0 / FLASH = 0 / RESET = 0 / SERIAL = 0 / MSH = 0 / TMC = 0
```

**设备读取门 PASS ≠ 栈安全 PASS。** TMC 链路仍未经过本包测试，`safe_for_enable=false`；A0 时长继续暂缓。

## 10. 安全边界（不变）

电机未接；SW1 阻断；四个安全宏有效值均为 0；未动 ENN/STEP/DIR/许可 HIGH；未清故障、未 selftest、未扫描设备。
