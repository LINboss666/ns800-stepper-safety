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
| `m8r3_field_selftest.py` | 73 条离线场景（R2 的 FS01…FS25 身份原序保留 + R3 边界/整包/负控 + R3 收尾轮的 §2A–§2D 反例与健康对照），含 7 项仓库外副本的定向检出（收尾后共 11 项） |
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


## 4. R3 收尾轮(2026-10-31)之后的合同变化

复审 `M8_R3_FIELD_PACKAGE_REWORK_REQUIRED` 的五条真实执行路径缺陷已在**同一执行器**里关闭, 并留下
确定反例 + 健康对照(FS56…FS73, 11 项定向检出)。要点:

- **模式分流**: 只允许 `stub+sim`(离线) / `real+real` / `real+manual`(受同一身份交付门约束)。
  混合模式在任何 UID 读取、工具调用、开口动作之前 `MODE_COMBINATION_REFUSED`, 副作用清单为空。
  真实入口的档案只接受 `response_driven`: 事故支线由本次响应决定, `-Profile all` 只属于离线。
- **回读后 Halted**: S3 现在是 开口 → 唯一一次已批准复位 → **消费启动流**(原字节 `NN_rst.boot.rx.bin`
  并等到提示符) → 首个合法查询; 反序仍会 `MISSING_RESPONSE_PROMPT`, 没有提示符则
  `POST_RESET_BOOT_PROMPT_MISSING`, 两者都由自检真跑断言。
- **失败门**: 清理的读/写/证据异常各自独立捕获(stop 的异常不再吞掉唯一必要 disarm), 短写具名停止,
  开口额度在尝试之前消费, `expected_terminal` 只豁免业务结果本身。
- **夹具值 → 固件事实**: 删除两处 `live sig=44100 match=YES` 硬编码(改为记录本次真实签名 +
  `match=YES` + 重启后逐项比较); flags 按 `RPF_*` 位判定, 夹具改 `0005`; 起转后有限轮询必须证得
  数值 `state=MOTOR_CRUISE(2)`; S9 在计数严格增大之后用 `flash_dump` 读回**实际帧**(取不回来 =
  `FLASH_FRAMES_NOT_RECOVERED`, 报取证未完成)。
- **时间合同(仅离线提案)**: 学习起转 15 s、保护观察 5 s 按阶段传入, 整包 ≤ 2 次 start、总额 20 s 由
  `PLAN_MOTION_CAP_BUDGET` 审计; 假端口按产品源码的 12000 ms 与本轮 probe 观察建模"忙 msh",
  stop/disarm 在忙期只能排队。**15 s 不是硬件授权**, 也没有把旧的 5 秒测试记成通过。
- **隐私**: 公开件只登记脱敏 argv + 原 argv 的 sha256 + UID 长度/sha12; 真实 UID 不入库、不打印。
- `--validate-plan` 现在 151 项(新增首查顺序、常量回查、时间预算三项门); `rows.json` 40 组的来源核查
  problems=0; 三入口在 Windows PowerShell 5.1 各跑一遍到新 run 目录(`offline/psrun-*`),
  汇总见 `host/r3_closeout_runs.json`。

## 7. 最终补正轮(FF, 2026-11-01)之后的读法

本轮软件变动**只在 `field/` 的既有五件**，逐字节等于审核方 `patch_identity.json` 的候选散列:
基线取 `15745e5` 的 Git blob 复算(五件全对)，候选由仓外规范 LF 副本上的 `tail_candidate.patch`
(21667 B / `07de65f0e94f6ebe682eb6a29c96fe4626d7e1e479423347146ebd19a6045eee`) 产出，
`git -c core.autocrlf=false apply --check` 与 `apply` 均 rc=0，五件输出散列与申报一致
(复算表见 `host/ff_identity.json`)。产品 C/H、Keil 工程、六镜像、三 map、固件 manifest
一律未改，未重编，未重跑不变的 C 链。

| 项 | 现在必须按这个口径读 | 落点(候选字节) |
| --- | --- | --- |
| F1 启动边界 | 复位后只**有界被动**收启动字节(`BOOT_STREAM_DRAINED` 带 `prompt_required=false` 与 `end_reason`)，结果里写死 `readiness=NOT_ASSESSED_QUERY_REQUIRED`: 启动文本不作 READY 证明，也不得据它继续。就绪只由**同一条**首查 `system_status` 的完整提示符响应证明，并新增唯一核对 `[Safety] state=READY fault=0`(与既有 `bootstrap-complete=YES abort-stage=0` 同响应) | `m8r3_serial_chain.py:1011-1046`; `plan.json` 的 `boot_drain_note`/`order_note`/S3 新行判据 |
| F2 工具 argv | `-c` 之后的整条命令串**不再**被当相对路径加 repo_root 前缀(`savemem 0x08000000 <len> <file>` 原样交给 pyOCD)；镜像/独立文件参数仍以 repo_root 解析 | `m8r3_serial_chain.py:657-661` |
| F3 有符号日志列 | 14 列里只有产品的有符号列(零基 6..12: `sg,current_ma,ax_mg,ay_mg,az_mg,step_hz,vibration_mg`)允许负号；`slot/seq/session/time_ms/event/valid` 与 `flags`(13)仍不许。合法 `ax_mg=-23` 的帧现在算证据，缺帧与 `slot N error` 仍拒绝 | `m8r3_serial_chain.py:1767-1769` |
| F4 新日志槽位 | S8 起转前**新增一次只读** `flash_logstat`(`min_free_log_slots=300`，不足即 `EVENT_LOG_CAPACITY_LOW` 且不发运动命令)；S9 的 `flash_dump first count` 由 S8 记录的 used 与 S9 当前 used 推导末尾最多 8 槽，并逐槽核对覆盖/session/event=9/step_hz=0。事件存储是追加、满即停，不是环；**不得**为腾空间自动清日志 | `m8r3_serial_chain.py:1717/1720-1740/1228-1230`; `plan.json` S8 第 4 步 + S9 第 5 步 `new_log_window`; `rows.json:805` 新组 |
| F5 PS 真入口 | `-Profile` 白名单加入 `response_driven`，因此正确的实机参数在绑定阶段不再被拒；`real + all` 仍禁止、传输/后端分流与"分支由本次响应决定"都保留 | `M8R3Field.ps1:36` |

**作废的旧口径(不得再引用)**:
- `POST_RESET_BOOT_PROMPT_MISSING` 与"启动流必须以提示符结束"这条前置已删除 —— 两份真实历史启动件(3381 B / 3757 B)在原字节下会被它拦死，`002_version.bin` 也确实是 `version` 命令的回复而不是自发启动证据。
- S9 的 `flash_dump 0 8` 只剩下 **stub 取行键**的用途；真实发送的命令由新增区间算出(离线声明的对照场景 300→600 → `flash_dump 592 8`)。固定读槽位 0 会把上一轮旧日志当成取证通过。
- `flash_logstat` 与 `flash_dump` 两组行表是**离线声明的追加场景**(kind=source_shape)，不是板级观测。

真实入口参数是 `-Transport real -Backend real -Profile response_driven` 加审批令牌与 UID 文件；
`-Profile all` 只用于离线，real+all 在 PS 自己的门上即拒(`REAL_PROFILE_ALL_FORBIDDEN`，rc=4，
不起 Python)。

额度与硬件边界不变: 学习起转 ≤15 s(S4)、保护观察起转 ≤5 s(S8)、最多 2 次 start、合计 ≤20 s，
由 `PLAN_MOTION_CAP_BUDGET` 审计; 保持 IRUN20/100 Hz/DIR0。**15 s 仍只是离线合同提案，不是硬件授权**，
也没有把旧的 5 秒测试记成通过。

本机验证(原文见 `host/*_r3finalfix.*` 与 `host/reviewer_ff/`): 原 73 项 73/73 rc=0；同一边界程序
跑候选 22/22 rc=0、跑 `15745e5` 原交付字节 7/22 rc=1(逐件旧行为复现)；`--validate-plan` 154 项
0 失败；rows 来源检查 41 组 problems=0 notes=0；PowerShell 5.1 三入口到新 run 目录
SelfTest 22/0、Precheck 14/0、RunField(stub/sim/all) 3/0 且 `actions_attempted=0`；
Parser/参数 AST 离线复核 0 语法错误且白名单含 `response_driven`(未调用 real 入口试绑定)。

假端口 + 假子进程通过**不等于**物理 SG/ADC/IMU 触发通过；SG 分离度、ADC 相对阈值实际作用、
IMU 激励与物理保护效果仍待现场验证。
