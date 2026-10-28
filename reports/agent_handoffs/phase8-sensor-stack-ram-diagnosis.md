# SR 交接 — 现有 R2 镜像的单次栈 RAM 诊断包（离线轮）

任务书：`NS800_Stack_RAM_Diagnosis_Task_7380856.md`（bytes=11829，sha256=`cd8e72d8b6df0a272755ef9b67830d95274c7bb1784404306da6c6edb4905d87`）
本包 Git 锚点：`7380856149752506286714e9e82eda6e205abb40`　本包开工时分支头：`7a6e31efb3b5b4391299d2ac3db86742e8e65113`
R2 软件输入：`139e9377ccbc36b878cd577a6b848d974e1e07fa`　分支：`phase8/tmc-drive-config`　日期：2026-10-28（北京时间）

**一句话结论**：离线绑定与有界诊断工具已完成并在定稿字节上跑了八组夹具（任务书要求的三组全覆盖，另加五组）；
**本轮设备动作 = 0**（未连 DAP、未 halt、未读 RAM、未下载、未复位、未开串口、未发 msh/TMC）。
一次批准仍未使用，停在「批准栈 RAM 诊断包」门前。上一轮的两个停点标签原样保留，未改写。

## 1. 本轮决定与它没有做什么

- **决定**：不再加栈、不改内核告警、不再烧录；改为在**现有 R2 镜像**上取一次暂停快照，核对 TCB/保存 SP/整块栈字节与真正链接的检查指令。
- **没有做**：没有新构建、没有新镜像、没有重烧、没有重跑上一包采集器、没有设备读取。产品/内核/工程/采集器/BIN/历史证据一律未改。
- **为什么不能沿用旧额度**：上一包 DAP 额度已用尽，且本包新增“主动暂停”这一动作；两者都必须由本次批准覆盖。

## 2. 交付物（字节与 sha256 由脚本对本地文件复算）

| 件 | 字节 | sha256 |
|---|---:|---|
| `reports/reviews/phase8-sensor-stack-ram-diagnosis/stack_ram_diag.py` | 29335 | `32f4eb7fed3581534fe082cc1a3c056dc2d0cda940179b69cd49350c99badf69` |
| 同目录 `binding_report.txt` | 16693 | `91fb6516eb4059440bbe43e657b5536e6679b108eca166da82a7befb51735dc0` |
| 同目录 `dryrun_matrix.txt` | 4196 | `b29fd4fb759fa88d5bba009db75a77685fe6e5b3a843b02ef8d11abed9aa88be` |
| 同目录 `live_package.txt` | 3740 | `ba0030c46cae4fa93a211aca4e07e1122caa60707b2e0c3618564d050522903c` |
| 同目录 `items_manifest.txt` | 756 | 自散列不写入自身；以提交后 git blob 与远端回读为准 |

仓库内 `stack_ram_diag.py` 与仓外执行副本逐字节相同（同 sha256），八组 dry-run 的执行时间晚于该文件写入。

## 3. 绑定结果（全部取自这枚 AXF，不按网上版本猜）

| 对象 | 值 | 依据 |
|---|---|---|
| `s_tid` | `0x2000a480` | DWARF 唯一具地址定义（CU `sensor_service.c`，定义数=1）；symtab size=4、STT_OBJECT |
| `_cpu` | `0x200008d0` | DWARF 唯一具地址定义（CU `cpu_up.c`，定义数=1） |
| `sensor_thread_entry` | DWARF low_pc=`0x08020e18`；symtab=`0x08020e19` | TCB.entry 须为 `low_pc|1`；不一致工具在连接前拒绝 |
| `rt_scheduler_stack_check` | `0x0801ad81`，size=246，STB_GLOBAL | symtab + fromelf |
| `rt_thread` | 168 B；sp=28 entry=32 parameter=36 stack_addr=40 stack_size=44 error=48 parent=0 | DWARF 30 个定义，成员偏移变体数=1 |
| `rt_object` | 28 B；name@0（长 16）type@16 | DWARF 45 个定义，偏移一致 |
| `rt_cpu` | 8 B；current_thread@0 | DWARF 1 个定义 |
| CCR | `0xE000ED14` | SCS_BASE 0xE000E000 + SCB_BASE 0xD00 + DWARF 偏移 20 = CMSIS `Offset` 注释 20 |
| DTCMCR | `0xE000EF94` | 同法：偏移 660（DWARF 与头文件注释一致）；匿名 SCB 结构定义数=6、size=684 |

窗口 `[0x20000000, 0x20020000)`；`s_tid` 与 `_cpu` 均在其内（脚本断言）。堆 `[0x20012000, 0x2001A000)`（`Image$$RW_RT_HEAD$$Limit`=0x20012000，+0x8000），离窗口上界还有 24576 B。

## 4. 链接后指令事实（94 条逐条列入 binding_report.txt §5）

- 判据用的是 TCB 中**保存的** `thread->sp`（偏移 0x1c），不是当前即时 PSP/MSP；告警带是栈块最低 32 B，与栈大小无关。
- 溢出三条：底部首字节 ≠ `'#'`(0x23)；`sp <= base`（BLS）；`sp > base+size`。
- 告警一条：`sp <= base+0x20` 才打印 `warning: %s stack is close to end of stack address.`（`BHI` 反向跳过）。
- 溢出分支先打印再取 `rt_stack_overflow_hook`（RAM 指针 `0x2000a2e0`）；该 BSP/应用未注册 setter，钩子为 NULL，
  随后 `dummy=1` 原地死循环 —— 这与上一轮“告警刷屏后不再前进”的现场表现相容，但**不是**本轮已验证的设备事实。
- 唯一调用点：`rt_schedule` 内 `BL @0x0801acc6` → `0x0801ad81`；capstone 线性扫描 112670 条指令只此一处。

## 5. dry-run 矩阵（真实执行，不是纸面）

八组夹具，全部在**读之前**按分支拒绝或放行；最大累计 4456 B（低于 16384 B）。任务书要求的三类均已覆盖：

| 任务书要求 | 本次对应夹具 | 结果 |
|---|---|---|
| 正确指针 | `ok`（低端 sp 且填充一致）、`okshallow` | `PASS_ALL_GATES`，8 次读、4456 B |
| 越界指针读前拒绝 | `oor`（s_tid→0x20021000）、`oor2`（base+4096 越界） | `GATE_REFUSED`，被拒那次读未发出 |
| 错误线程名/入口拒绝后续栈读 | `badname`、`badentry`、`biggerstack` | 记录 s_tid 与 TCB 原值后停，栈一次未读（2 次读 / 172 B） |
| （额外）自相矛盾快照 | `contradict` | 放行但记 `fill_vs_sp_consistent=false`，解释留给报告 |

原始 stdout/result.json 的完整 sha256 列在 `dryrun_matrix.txt` 末尾；`pyocd_session_created=false`、`serial_objects_created=false`。

## 6. 上一轮事实的继承（不改写）

| 轮次 | 停点标签 | 事实 |
|---|---|---|
| URO-R1 板级 | `STACK_DANGER_OBSERVED` | 真实危险流 166 B / `075b02dd…`；七条命令 0 发出 |
| URO-R2 第 1 轮 | `STACK_DANGER_OBSERVED` | 新镜像逐字节在板；20 s 内 243 次告警；0 条命令 |
| URO-R2 第 2 轮（重烧） | `BOOT_WINDOW_INCOMPLETE` | 横幅逐字命中；启动头后 20 s 无字节，窗口未完成；0 条命令 |

**`STACK_4096_CHANGE_DID_NOT_CLEAR_WARNING=true` 保留。** 第 2 轮的“无告警”只是窗口未完成，不能当修复证据；
第 1 轮的告警也不能当“4096 不足已证明”。两轮额度各自用尽，本包不续额、不重跑补救。

## 7. 本轮勘误与首失败（离线，全部已修正并保留）

1. 曾把 `Image$$RW_RT_HEAD$$Limit` 按“区域末地址”读成 `0x2001A000`，进而在准备中设想“TM 栈可能落在批准窗口外”。
   实测 symtab 为 `0x20012000`（ZI-only 区域的 Limit 等于基址）→ HEAP=`[0x20012000,0x2001A000)`，全在窗口内。已写入 `binding_report.txt` §6 勘误段，未静默删除。
2. 系统寄存器绑定第一版找名为 `SCB` 的命名类型 → `SYSREG_CCR_BINDING_UNAVAILABLE`；该结构在 DWARF 中是**匿名**的，改为结构特征扫描（成员含 CCR+DTCMCR+SCR）+ 跨 CU 一致性 + CMSIS `Offset` 注释双源核对。
3. `ok` 夹具第一版自相矛盾（画了 1024 B `'#'` 却给 16 B `saved_free`）；拆成 `ok`/`okshallow`/`contradict` 并加 `fill_vs_sp_consistent` 计算字段。
4. 语义注释第一版按 `(助记符, 操作数)` 归类，导致同助记符不同地址被误标/漏标；改为**按指令地址逐条绑定**，并断言 94 条全覆盖。
5. `dryrun_matrix.txt` 第一版对 `contradict` 在 `refusal` 里找标记而误判“存在不符合期望的组”；改为同时检索 `computation`，现判“八组全部符合期望”。
6. `items_manifest.txt` 第一版把自身写入前的散列写进自身；改为不列自散列，并注明以 git blob/远端回读为准。
7. 空 `live_package.txt` 合计式曾写成 `6184 B`（算错）；改为由脚本从绑定值现算（`4+1024+8+4096+4+1024+4+4=6168 B` 上限），并附实测 4456 B。

这些是**离线工具/证据缺陷**，不是设备失败；没有一次因此放宽判据或省略拒绝分支。

## 8. 停点与待批准

本轮无设备动作，故不产生设备 PASS：

```text
STACK_RAM_SNAPSHOT = NOT_ATTEMPTED (待一次批准)
EXPLANATION_LEVEL  = NOT_ASSESSED
FINAL_CORE_STATE   = UNCHANGED (本轮未连接; 上一轮结束状态按上一轮交接)
HARDWARE_ACTIONS   = []  (0 DAP / 0 halt / 0 read / 0 flash / 0 reset / 0 serial / 0 msh / 0 TMC)
```

批准原文（与 `live_package.txt` 一致）：

> 批准执行本文栈 RAM 诊断包：允许一次已知 DAP 连接、最多一次暂停，按可信 R2 AXF 绑定线程，仅限≤16 KiB RAM 及本文所列核心/只读系统状态；不烧录、不读目标Flash、不复位、不串口、不写业务内存、不注入代码，结束保持暂停。离线绑定和检查在同一任务完成，不逐条重复确认。

简写「批准栈 RAM 诊断包」即绑定上述完整范围。

## 9. 安全边界（不变）

电机未接；SW1 阻断；四个安全宏有效值均为 0；未动 ENN/STEP/DIR/许可 HIGH；未清故障、未 selftest、未扫描设备。
TMC 链路仍未经过本包测试，`safe_for_enable=false`；A0 时长继续暂缓。供电、接线、扩展板线路按 `USER_CONFIRMED` 沿用，本轮未新增照片/供电材料/断电回报要求。
