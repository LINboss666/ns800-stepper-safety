# M8-R3 现场候选包（可执行，未获批准，未执行任何设备动作）

目录：`reports/reviews/phase8-m8-relative-protection-r3/field/`。身份绑定见 `plan.json:identity_binding`
（`software_input_commit = tested_image_build_commit = 805c29b…`，`reviewed_delivery_commit = 5c549f2…`，
镜像 `reports/firmware/phase8-m8-relative-protection-r3/rt-thread-motor-m1-candidate.bin`，
`bytes=290916 sha256=00608ec93c6a3027…`）。

## 1. 包内文件

| 文件 | 作用 |
| --- | --- |
| `m8r3_serial_chain.py` | 唯一实现：会话执行器（单一写者、原始字节先落盘、起转即硬截止、清理分段、动作后端可注入）+ 整包调度 `--dispatch` + 只读门 `--check-static-port-discipline / --prove-needles / --validate-plan` |
| `plan.json` | 一次批准的计划：S0…S11 阶段、整包共用额度与**清理预留**、真实工具 argv（含 `@UID_FILE/@RUN_ROOT/@IMAGE_BYTES` 三个机械占位）、身份绑定、预算算式、残留风险、对复审条目的逐条对账 |
| `rows.json` | 响应行表，逐组（含 `_provenance_trip` / `_provenance_drift` 等档案级）标注来源：板上原始采集 / 128 字节截断按内容拆分 / 两块真机采集并成一份（`board_capture_composed`）/ 源码字面量形状+本轮下发值，后两类不构成设备证据。每一处声明都由 `../host/rows_provenance_check.py` 做**行级**核对（采集件路径存在 + 行号落在解码后行数内；`applications/*.c:NN` 存在 + 该行 ±6 行内确有 `rt_kprintf`） |
| `needles.json` | 40 条 needle，每条由 `prove_needles()` 回到 `applications/*.c` 的 `rt_kprintf` 字面量证明 |
| `m8r3_field_selftest.py` | 55 条离线场景（R2 的 FS01…FS25 身份原序保留 + R3 边界/整包/负控），含 7 项仓库外副本的定向检出 |
| `M8R3Field.ps1` | Windows PowerShell 5.1 三个互斥入口（`-SelfTest / -Precheck / -RunField`），只做参数门、证据落盘、退出码与独立复算；**不另实现一套调度或判定** |

## 2. 三个入口

```powershell
# 离线自检（假端口 + 假动作后端；本轮唯一被真正跑过的路径）
powershell -NoProfile -ExecutionPolicy Bypass -File field\M8R3Field.ps1 -SelfTest
# 只读前置核对（含镜像字节/散列独立复算、两个提交存在性、来源文件存在性）
powershell -NoProfile -ExecutionPolicy Bypass -File field\M8R3Field.ps1 -Precheck
# 执行一次批准计划（本轮只以 stub/sim 跑过三个档案；real 需要新的具体批准）
powershell -NoProfile -ExecutionPolicy Bypass -File field\M8R3Field.ps1 -RunField `
    -Transport stub -Backend sim -Profile all
```

`-RunField -Transport real -Backend real` 还需要 `--approval-token`（与计划里 `expected_token` 相等）与
`--uid-file`（既存私有 DAP UID 文件，48 字符；永不入库、永不打印，只披露长度与 sha256 前 12 位）。
缺任一件：`REAL_TRANSPORT_REFUSED` / `UID_FILE_REQUIRED`，一条命令都不发（PS 退码 4）。

## 3. 停点策略（与自检同一条）

一次批准从 `S0` 连续走到计划里的最后一个适用阶段；**成功阶段只记检查点**，不要求为下一阶段重新批准、
不重新下载；**失败才终止**，未执行阶段以 `DISPATCH|stage=…|status=NOT_RUN` 明列，绝不计入
`stages_executed`。阶段序列还与"按 `profiles` 过滤后的期望序列"逐个比对，多一个少一个都算非成功。

阻断后续的阶段结果（一律非成功、非零退出）：`terminal=UNKNOWN`、`cleanup_errors` 非空、
`cleanup_overruns`（预留未申报或耗尽，安全动作仍发出但具名 `CLEANUP_RESERVE_EXHAUSTED`）、
证据失败（`EVIDENCE_OPEN_FAILED`/`EVIDENCE_WRITE_FAILED`）、恢复比较不等
（`RESTORE_MISMATCH`）、新增 session 未增大（`SESSION_NOT_INCREASED`）、额度审计不符
（`QUOTA_AUDIT_FAIL|undeclared_used|negative_left`）。

## 4. 阶段一览（`plan.json:stages`）

`S0` 离线绑定（重算镜像 bytes/sha256 并与 `build_manifest.json` 逐字段比 + `git rev-parse <sha>^{commit}` +
40 条 needle 回源码 + 计划占位/禁止命令静态拒绝）→ `S1` 唯一一次下载（`pyocd flash … -e sector <axf>`，
成功原文按 `Erasing…/Programming…/Erased N bytes (M sectors)` 核对）→ `S2` 唯一一次定长回读
（`savemem 0x08000000 @IMAGE_BYTES @RUN_ROOT/readback.bin`，交付物文件与镜像逐字节比）→
`S3` 会话1：开口→一次显式 DAP 复位→前置重新证明（`tmc_config_apply CONFIRM`、极性声明、IRQ 开）→静止期零点 →
`S4` 会话2（唯一一次学习起转）：静止证明→`motor_dir 0`→`motor_arm`→`motor_target 100`→`motor_start`
（硬截止起点）→本轮 `motor_status` 证 RUNNING→`diag_profile learn 8 80 120 0`→5 s 观察窗→清理并证禁能 →
`S5` 静止会话：`tier`→`commit`→`show`（抓 id/rev/flags/live sig/match，并与 commit 行交叉核对）→`config_save` →
`S6` 断电重启交付门（操作者事实，不是一条伪命令）→ `S7` 重启后会话：前置重新证明 + **与 S5 记录逐项实际比较** →
`S8` 武装 RELATIVE + 一次 5 s 有界观察（`when`/`profiles` 分流）→ `S9` 事故取证（锁存视图、按**实际新增 session**
的黑匣子、新日志计数、锁存态仍可证禁能）→ `S10` 锁存态 `motor_arm` 具名拒绝（计划申报 `expected_terminal=REFUSED`，
会话到此结束）→ `S11` 人工恢复（`fault_reset`→`MANUAL_CLEAR`、重跑自检、就绪复核，`forbidden_commands` 含 `motor_start`）。

## 5. 真实现场执行前仍需的物料 / 前置（本包不给假值）

1. 用户对该硬件候选的**一次具体批准**（下载 + 回读 + 复位 + 串口 + 起转 + apply + 触发观察 + 故障恢复的明确范围）；
   不继承 M4/M5/M6/M7/P0/M8-R2 的任何额度。
2. 既存私有 DAP UID 文件（48 字符）与 COM 口名；`-f 1000000`、pack 路径与 `-t ns800rt7p65d_dhqfby2`
   沿用 M5/M7/P0 已成功的记录，未做任何参数猜测。
3. 一次断电重启由操作者完成（先关 24 V 再拔 USB），并把完成事实交付给 `S6` 的门。
4. 受控加载工具与作用点：本轮申报 `NAMED_PREREQUISITE_MISSING`（`plan.json:load_step`），不许以"允许自然触发"
   代替；IMU 非破坏激励是**可单独选择**的批准范围，未选择则保持 `FIELD_NOT_TRIGGERED`。
5. `rows.json` 里标为 `source_shape` 的行必须由现场真实响应替换 —— 它们只是离线控制流的形状。
   特别是 `REL TRIP/REL LATCH`、`[BB] session` 增量、重启后的 `diag_profile show` 值：
   记录值/比较判据都是真的，但**值本身**目前来自夹具。

## 6. 本轮的执行边界

`hardware_actions_this_round = []`。`transport` 只用过 `stub`，端口只用过内存 `FakePort`，
动作后端只用过 `sim`；`RealActionBackend` 的 subprocess 路径、`ManualActionBackend` 的交付物门、
`pyocd`/串口的任何调用都**从未运行**（其参数与成功原文只做了离线核对与静态证明）。
因此本包的任何"通过"都只是离线控制流，不是设备 PASS。
