# A0 目标机测量操作包（r1；待整包审议，本轮零执行）

任务 ID：`phase8-target-measurement-a0-r1`；日期：2026-10-25（Asia/Shanghai）。
性质：**未来动作清单**。本文件不授予任何权限；除另有明确批准外，本文所有步骤的状态一律是
`NOT EXECUTED / NOT AUTHORIZED`。它与 `reports/plans/phase8-target-measurement-plan.md` §7 的包 A 是
"子集"关系：本包只覆盖 **A0 关闭路径**，不含 `HIGH/STEP 输出`、`DIR` 改变、TMC apply、改线/供电/SW1、
真机清故障、selftest 与任何电机动作。

> **r1 改了什么（对应复审 A0-03 的五项）**
> 1. 分批之间补上 `spm clear_records`，并把每批写成可执行的闭环（§2.1）：
>    原稿 P-11 停止导出后仍是 `STOPPING` 且带着旧记录，P-12 直接 `start` 必然被拒，后面 16 次关闭
>    不会产生第二批。现在明确"停止 → 确认无在途 → 导出 → 核对数量并保存 → 清测量 RAM → 下一批"。
> 2. 删掉一条会把**合法状态当故障**的停止判据：`body_valid=1` 且 `clock_basis` 未升级**本来就是本批
>    唯一合法组合**（§4）。真正要停的是"擅自宣称已校准 µs / 无来源升级 clock_basis / 无效记录参与时长统计"。
> 3. 所有判据与证据字段改成**导出表实际存在的列名**：`body_valid` / `enclosing_valid`；
>    原稿写的 `sample_valid` 那一列**不存在**，已删除（§4）。
> 4. P-13 不再把 `spm trip` 当成 `sp_off_attempt_for` 的等价触发；该入口只用**已核对的正式
>    `motor_disarm`**，自然不可达就记 `NOT_REACHED`，不增设硬件故障注入（§2.2）。
> 5. 收尾状态统一：清完测量 RAM 之后最后一次 `dump` 只能是空批次，所以"最后有效 CSV"必须在
>    **清理之前**保存（§2.1 第 5 步、P-14）。
> 6. 顺带按源码更正三条命令的副作用分类：`safety_status` 不止读 PC.23 也读 PC.6 反馈；
>    `ns_log_stats()` 是取锁后复制内存统计，**本函数不直接读 Flash**；`motor_status` 在快照之后还会
>    读许可/反馈并核配置摘要，不是纯 RAM 查询（§3.2）。

## 1. 本包绑定的镜像与源码身份（批准对象就是这两个散列，不再是"以后某个镜像"）

| 项 | 值 |
|---|---|
| 构建用源码（`code_commit`，含代码/测试/**完整双目标工程状态**） | `b27e5f14bdb62f645b93eace23800b40369717d4` —— 构建直接在这一棵定稿树上做，不再有"工程改动在另一个提交里"的含混表述 |
| 被返修的上一轮对象 | `c5028f4c502596d24cb81b80e927e599e0f3d6fd`（审查对象）/ 其 `code_commit` `678768739dd8e964fdcc428ef1168e4353c167f7` |
| 接受软件对象（不变，不撤回） | `bcdec631266239008734271ba75beab435bf30eb`（S2 `SOFTWARE_REVIEW_GO`） |
| 准备轮接受对象（不变，不撤回） | `bbf66ba34d7b31a217c643921aa4192df2f59a56`（`PREP_REVIEW_GO`） |
| 默认镜像（r1） | `reports/firmware/phase8-target-measurement-a0/default.bin` = `41717367e4e1401deb5f59eb27d1f35483108c6241eb30a5b58b28aad5b425de`，251148 B；Keil 目标 `rt-thread`，`Code=173762 RO=76806 RW=2188 ZI=83784`，0 Error / 0 Warning；`NS800_TARGET_MEASUREMENT_A0` 未定义(=0) |
| ↳ 默认镜像与上一轮的差 | **只差 6 个字节**，位置 BIN 偏移 `0x03bf65..0x03bf6c`（VA `0x0803bf65`），内容就是 RT-Thread 版本横幅里的 `__TIME__`（`src/kservice.c:162`，`18:17:01` → `20:00:43`）；字节数与 Program Size 逐字相同。上一轮散列 `ef69404d…` 在 `build_manifest.json` 里保留为 `historical` |
| 测量镜像（r1） | `reports/firmware/phase8-target-measurement-a0/measure-a0.bin` = `358fd81ae5853ea52df1e1ab367ea41af0f5e86679082a755c17dac3429b0e1b`，261100 B；Keil 目标 `rt-thread-measure-a0`，`Code=180802 RO=79718 RW=2188 ZI=91504`，0 Error / 0 Warning；相对默认目标只多一个宏 `NS800_TARGET_MEASUREMENT_A0=1` 与独立输出目录（上一轮 `e9ba8dae…`/259868 B 记为 `historical`） |
| 命令名（A0-01 修后事实） | 目标上的命令名是 **`spm`**，不是 `spm_cmd`。ELF 实证：`__fsym_spm` @`0x0803f858`（16 B，map 行 17529 `sp_measure_a0.o(FSymTab)`），原始 16 字节 `84d7020888d7020800000000f9380208` → `name=0x0802d784` 处串为 `"spm"`（长 3，NUL 在 +3；BIN 偏移 186244），`desc=0x0802d788` 为 `"NS800 target measurement A0 control"`（BIN 偏移 186248），`opt=0`，`func=0x080238f9` == 符号 `spm_cmd`。两个镜像里都**没有** `__fsym_spm_cmd`；默认镜像里 `__fsym_spm`/`spm_cmd`/`spm_rec` **全部不存在**，且 map 有 `Removing sp_measure_a0.o(.text), (0 bytes).` |
| 记录布局（schema=2） | `SPM_SCHEMA_VERSION=2`，`SPM_SLOTS=64`，一条记录 **120 B**（主机 `sizeof` 与目标 map 的 `spm_rec` 7680 B = 64×120 双向一致），`sizeof(spm_ctx_t)=48`；ZI 在 r1 与上一轮相同（91504），本轮**没有**改变记录大小，只是把原 `pad2` 字节启用为 `gates` 列并加了 CSV 列 |
| 两目标差异（XML 逐叶子核对） | 只有 `TargetName / Cads.Define / OutputDirectory / OutputName / ListingPath / AfterMake.UserProg1Name` 六处；源文件清单两目标各 135 项且逐条一致（c5028f4 已核；本轮工程文件未改） |
| 四宏（两个变体都相同） | `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` `motor.h:69`、`STEP_DIAG_ISOLATED_ENV_CONFIRMED` `motor.h:83`、`TMC_DRIVE_CONFIG_HW_VALIDATED` `tmc2209.h:100`、`SAFETY_LIMIT_INPUTS_ENABLED` `project_board.h:51` 全部 `RT_FALSE`；`NS800_TARGET_MEASUREMENT_A0` 只是记录开关，**不参与任何安全判定**；不得用 `-D` 覆盖任一宏 |
| ELF/AXF | 只留在本地（调试信息可能带本机路径，未脱敏不上传）；`build_manifest.json` 记 ELF 的本地 SHA-256（默认 `76d19ced…`，测量 `c8176c19…`） |
| 回退能力 | **`BLOCKED_ROLLBACK`**：`default.bin` 是本轮默认变体，**不是**板上那颗 e40 的已验证回退镜像；板上固件的确切文件与散列未知（本轮未读 Flash），不得凭标签名假装已有可回退镜像 |

## 2. 步骤表

状态列含义：`READY` = 只缺用户批准；`NOT_READY_FOR_AUTHORIZATION` = 还缺现场信息，**不能现在批准**；
`NOT EXECUTED` = 本轮未做任何一条。

### 2.1 分批闭环（每一批都必须走完这 6 步，才允许进下一批）

```text
1) spm start <entry>            # 只有静止态才允许（state=IDLE 且 records=0 且 in_flight=0）
2) 触发 N 次被测路径            # N 按该步已批准的次数，逐次之间允许人工 spm status
3) spm stop                     # 停止接收；不等待在途、不清故障、不关共享 DWT/DEMCR 位
4) spm status                   # 必须看到 in_flight=0；不为 0 就停（不得强行清）
5) spm dump  ->  核对条数 = N，并把 CSV **保存**到本批文件   # 保存必须早于清理
6) spm clear_records            # 只清测量 RAM；绝不清 Safety 故障/抑制/失败锁存
   → 下一批从 1) 重新开始
```

- 第 5 步的核对是**逐批**的：本批条数必须等于本批触发次数；不等就停并保留现场，不带病进下一批。
- 第 6 步之后 `spm dump` 只会是空批次，因此"最后一次有意义的 CSV"永远是某批的第 5 步产物，
  不是收尾时的一次 dump（原稿 P-14 的说法已改）。
- 若某批的 `start` 被拒（`BUSY` 或"仍有旧记录或在途"），**说明漏了第 3/4/6 步**，按本闭环补齐，
  不得改判据、不得用 `clear_records` 之外的手段绕过。

### 2.2 步骤表

| # | 步骤 / 命令 | 次数 | 真实副作用（按源码逐条分类，不按名字猜） | 停止条件 | 证据字段 | 前置 | 状态 |
|---|---|---|---|---|---|---|---|
| P-01 | 现场确认并拍照记录：电机是否物理未接、TMC 模块在位状态、VM 与逻辑电源来源、断电判据（草案 `P-5`）、SW1 当前位、人在场 | 1 | 无（只读现场） | 任一项说不清 ⇒ 整包不开始 | 现场记录表 + 时间 | 现场信息 | **NOT_READY_FOR_AUTHORIZATION** |
| P-02 | 烧录 `measure-a0.bin`（散列见 §1）到板 | 1 | 擦写应用区；启动后自动执行硬件初始化 | 散列与 §1 不一致即拒绝烧录 | 工具输出、镜像散列、目标名、四宏 | 端口/探针具体型号未知（本轮未扫描设备） | **NOT_READY_FOR_AUTHORIZATION** |
| P-03 | 上电/一次复位 + 控制台观察启动 | 1 | **启动路径本身不是只读**：GPIO 表初始化（输出脚写默认电平）、EPWM 硬件初始化（写 TBCTL 分频/模式）、uart1/uart2、ADC/SPI、传感器与黑匣子线程、flash 配置加载、自检与状态机 | 启动异常（意外 HIGH、意外 STEP、卡死、断言） ⇒ 停 | 启动日志全文 + `ps` | P-01/P-02 | NOT EXECUTED |
| P-04 | `free`；`ps` | 各 1 | 纯 RAM/内核对象遍历，无外设访问 | 无 | 输出原文 + 栈 `max used%`（`usage` 列恒 `N/A`，CPU-usage tracer 未开） | 串口白名单 | NOT EXECUTED |
| P-05 | `safety_status`；`step_status`；`diag_status` | 各 1 | `safety_status` **不只是**读 PC.23：`safety_gpio.c:427-470` 里对 DRV_ENABLE 脚 `rt_pin_read`，并经 `safety_fb_snapshot()` 读 **PC.6 = 板上 TMC_ENN 反馈**；全是**读**输出锁存/输入脚，不写。`step_status`/`diag_status` 只读模块内 RAM 影子 | 出现"!!已使能!!"或 `step_status` 报"输出中" ⇒ 立即停并记录 | 输出原文 + `enn_feedback` 口径说明 | 同上 | NOT EXECUTED |
| P-06 | `motor_status` | 1 | **不是纯 RAM 查询**：`motor_get_snapshot()` 先取 `mot_lock`（`RT_WAITING_FOREVER`）；快照之后还会 `step_pwm_off_confirmed()`、`motor_get_gate_fail_mask()`（内含 `safety_fb_now()` 的 2 次 `rt_pin_read`）与 `tmc2209_get_write_gate_mask()`（读固定复制的配置/许可摘要，不产生总线流量）。**不写** GPIO/TMC/UART | 长时间无返回 ⇒ 记录"被慢锁挡住"，**不得**据此判保护失效 | 返回耗时（人工/日志时刻，非窗口时长）、`armed/health/stop_result` | 同上 | NOT EXECUTED |
| P-07 | `config_show` | 1 | 取 `cfg` 锁（`project_config.c:136-143`，同一把可 FOREVER 等待的配置锁）；不碰 UART | 同上 | 配置快照原文 | 同上 | NOT EXECUTED |
| P-08 | `blackbox_status` | 1 | `ns_log_stats()`（`ns_storage.c:222-234`）取 `service_lock` 与 `queue_lock`（`RT_WAITING_FOREVER`）后**复制内存里的计数**（`used_slots/valid_records/bad_records/accepted/completed/failed/dropped/last_error`）；**本函数不直接读 Flash**（读 Flash 的是 `ns_log_read_slot()`，本包不调用） | 同上；出现写失败/丢弃计数增长要单列 | 原文 + `pending_before_after` 口径 | 同上 | NOT EXECUTED |
| P-09 | `spm cap` | 1 | 只读打印：`schema=2`/槽数/**一条记录字节 120**/数组字节 7680/`sizeof_ctx` 48/四宏编译值/`counter_status`/`clock_basis`/`systick_load`/`guard_ready`/`quiescent`/状态计数 | 显示的四宏不是 `RT_FALSE` ⇒ 立即停（拿错了镜像）；`schema` 不是 2 ⇒ 停（镜像与本包不同一轮） | 输出原文 + `image_sha256` 对账 | 测量镜像已启动 | NOT EXECUTED |
| P-10 | `spm init` | 1 | **唯一的寄存器写**：置 `CoreDebug->DEMCR.TRCENA` 与 `DWT_CTRL.CYCCNTENA`（保留其它位，不清 `CYCCNT`）；读 `DWT_CTRL`（含 `NOCYCCNT`）、`SysTick->CTRL/LOAD`；两次有界读取确认递增。r1 起 **只在静止态允许**：`state=IDLE` 且 `in_flight=0` 且 `records=0` 且无人持有判据；不满足 ⇒ 拒绝且**一个 PPB 都不碰**、不换能力缓存 | 报 `COUNTER_UNAVAILABLE` ⇒ 本包时长项转 `BLOCKED_COUNTER`，只交状态/顺序；报 `guard=NOT_ESTABLISHED` ⇒ 主体区间保持 `INTERVAL_UNPROVEN`；报"非静止态"⇒ 先走完 §2.1 的第 3–6 步 | `counter_status`、`clock_basis`、`DEMCR/CTRL/a/b/LOAD` 原值（P-10 在普通线程上下文，非屏蔽窗口） | 同上 | NOT EXECUTED |
| P-11 | `spm start trip_fast` → **1 次** `spm trip` → 按 §2.1 走完 stop/status/dump/核对保存/clear_records | 1 场景 × 1 次确认 | `spm trip` = **一次** `safety_force_shutdown(FAULT_SELF_TEST=8)`：经既有 Safety/黑匣子/存储路径可触发采集与 **Flash 写**；**不是只读命令**，也不做人工恢复 | 未出现预期抑制/许可 LOW 序列；或本批条数 ≠ 1；或 §4 的"越界宣称"出现 ⇒ 停并保留现场 | 完整 CSV 行（`body_valid`/`enclosing_valid`/`invalid_reason`/`gates`/四时间戳/`counter_status`/`clock_basis`）+ `dropped` | P-09/P-10 | NOT EXECUTED |
| P-12 | **第二批**：`spm start trip_fast` → **16 次** `spm trip`（每次之间允许人工 `spm status`）→ 按 §2.1 走完 stop/status/dump/核对保存/clear_records。⚠ 本批的 `start` 只有在 P-11 的第 6 步（`clear_records`）做完之后才被允许 | 16 | 每次一笔正式关闭 + 可能一次黑匣子提交；64 槽单批次只追加，**第 65 次起只计 `dropped`**，不覆盖旧槽 | `dropped>0` ⇒ 该批分布不完整，如实标注；本批条数 ≠ 16 ⇒ 停；关闭失败或证据无法绑定镜像 ⇒ 停 | 16 条 CSV + `samples_expected_observed_dropped` | P-11 已按 §2.1 收尾 | NOT EXECUTED |
| P-13 | `spm start off_attempt` → 只由**已核对的正式 `motor_disarm`**（`motor.c:2343-2344` `MSH_CMD_EXPORT_ALIAS(cmd_motor_disarm, motor_disarm, …)`，走正式门 + 一次 `rt_pwm_disable` + 独立许可 LOW + `mot_lock`，**不**请求 HIGH）产生该入口样本；`trip_for_operation` 只在**其真实调用点**自然出现时记录。⚠ **`spm trip` 不是 `sp_off_attempt_for` 的等价触发**：`safety_force_shutdown → sp_trip_fast →` 慢整理链不会为凑该入口再执行一次 off。自然不可达的入口记 `NOT_REACHED` | 1 + 已批准次数 | 同 `motor_disarm` 的分类；**不**新增硬件故障注入、不翻宏、不改接线来"造"入口 | 入口一次都没被走到 ⇒ 记 `NOT_REACHED`（这是结果，不是失败）；出现任何 HIGH/RUN 请求 ⇒ 立即停 | 各出口的 `branch`、`pwm_called`/`low_called`，并与现场人工观察的独立动作口径对齐 | 现场 armed 状态已知 | **NOT_READY_FOR_AUTHORIZATION** |
| P-14 | 收尾：确认最后一条 CSV 已在**清理之前**保存 → 空批次上再 `spm dump` 一次（此时**必须**是 0 条，只用于证明 RAM 已清）→ `spm status` → 断电判据按 P-01 执行 | 1 | 只有 `state!=ACTIVE` 且 `in_flight=0` 才允许导出/清 RAM；清 RAM **不动** Safety 故障、抑制或失败锁存（主机链 TMA24 已判定 `sp_epoch()` 与 pending 观测不被 clear 改变） | 收尾 dump 不是空批次 ⇒ 说明某批漏了核对；任何 BUSY 被强行绕过 ⇒ 停 | 最终空批次导出文本 + 各批 CSV 清单 + 断电记录 | P-11..P-13 | NOT EXECUTED |

补充纪律（对整包生效）：

- **参数合同（A0-01）**：`cap / init / stop / status / dump / clear_records / trip` 的 `argc` 必须**恰好为 2**，
  `start` 必须**恰好为 3** 且 entry 只在 `trip_fast / trip_for_operation / off_attempt` 三者内。
  无参数、缺参数、多余参数、未知子命令、非法 entry 五类都在**任何**计数读取、状态变化与安全动作之前
  被拒绝，返回码非 0；合法线程上下文里的参数错误**会**打印用法，非法上下文（中断里或已在屏蔽窗口里）
  **静默**拒绝。这一点主机链 TMA19/TMA20 已判定，上机只需按同一命令序列复现其文本可见性。
- `spm` 没有 `arm/start`（开启输出）、许可 HIGH、DIR、STEP 频率、TMC 写、清故障、改宏或直接改 `epoch`
  的子命令；不使用 `ss_test`（它会继续走 `fault_reset`）、`runtime_selftest`/`motor_ramp_selftest`/
  `diag_selftest`/`blackbox_selftest`/`flash_test`/`flash_unlock`/`config_save` 等未列入本包的命令，
  也不使用裸 `pin`/`pwm`/`tmc_uart_probe`；旧 e40 固件的命令语义**不得**移用以本。
- **排除清单**：`pin_status` 会重新写每个输出脚的默认电平（`safety_gpio.c:402-417`，对 DRV_ENABLE 走
  `safety_drv_enable_write(0)`），因此它不是只读命令，本包不放行；`GPIO_12/13`（控制台）、已弃用
  `PF.21`（`pf21_diag`/`pf21_ab` 会真的驱动它）、`PA.1`（EPWM1_B 保留）一律不得占用。

## 3. 导出表实际列（判据只能引用这些名字）

`spm dump` 的 CSV 共 **47 列**，头行逐字为：

```text
schema,gen,slot,entry,ctx_isr,saved_primask,rel_ok,proof_null,exp_epoch,in_owner,epoch_before,
epoch_after,owner_after,phase_after,inhibit,pending,pending_epoch,latch_m,latch_d,trips_before,
trips_after,pwm_epoch,pwm_attempted,pwm_result,pwm_raw_rc,permit_epoch,permit_written,permit_level,
permit_write_rc,outcome,branch,pwm_called,low_called,pre,body_begin,body_end,post,counter_status,
clock_basis,body_valid,enclosing_valid,invalid_reason,gates,guard,guard_load,dropped_at_publish,complete
```

- 有效性的列名是 **`body_valid`** 与 **`enclosing_valid`**；**没有 `sample_valid` 这一列**（原稿引用它
  是错的，操作员按原名会找不到）。
- `invalid_reason` 是 `0x%04x` 位集合，可同时命中多条；`gates` 是 `0x%02x` 的逐样本门控字节
  （bit0/bit1 = 本次**起点** `DEMCR.TRCENA` / `DWT_CTRL.CYCCNTENA`，bit2/bit3 = 本次**终点**同两位）。
- 未完成的槽（`complete=0`，即 RESERVED）会被导出成 `INCOMPLETE,gen=…` 一行，**不得**当完整样本计数。

`invalid_reason` 位（schema=2）：

| 位 | 名字 | 含义 |
|---|---|---|
| `0x0001` | `COUNTER_NOT_OK` | 能力缓存 UNCHECKED/UNAVAILABLE ⇒ 没有有效计数差 |
| `0x0002` | `GUARD_UNAVAILABLE` | SysTick 未启用 / 非处理器时钟 / LOAD 不可读 |
| `0x0008` | `RELOAD_IN_BODY` | **终点读**看到 COUNTFLAG ⇒ 主体 ≥ 一圈，混叠不可排除 |
| `0x0010` | `COUNTER_WRAPPED` | `body_end < body_begin` ⇒ 保守判无效 |
| `0x0020` | `NESTED_GUARD` | 内层记录清掉了外层判据 ⇒ 内外层都不可判 |
| `0x0040` | `CFG_CHANGED` | 两次读之间 LOAD/CTRL 配置变化 |
| `0x0080` | `NO_PROGRESS` | `body_end == body_begin` ⇒ 本次计数没往前走，**不是 0 耗时** |
| `0x0100` | `ENCLOSING_UNPROVEN` | 包围区间恒无效（本批无独立时基与完整防混叠依据） |
| `0x0200` | `GENERATION_STALE` | 发布时槽代次与预约代次不符 |
| `0x0400` | `SLOT_LOST` | 批次已满被丢弃（记在批计数器上，不落槽） |
| `0x0800` | `GATE_LOST` | **本次样本自己**的门控不成立（缓存曾通过不算数） |
| `0x0004` | *（已退役）* | schema=1 的 `FLAG_PRE`。起点读已把该历史旗读清，schema=2 起它只作为 `guard` 的观测位 `0x04` 留档，**不再**单独否定当前主体 |

## 4. 计时口径（合法状态与真正的停止判据）

- 时长统计的入池条件：`counter_status=COUNTER_OK` **且** `body_valid=1`（即 `invalid_reason` 除
  `0x0100` 的包围区间恒无效位之外没有别的位）**且** `gates=0x0f`。
- **合法状态**（不是故障）：`counter_status=COUNTER_OK` + `clock_basis=CONFIG_READBACK` +
  `body_valid=1`，**只报原始 cycles**。本批没有任何独立时基，`clock_basis` 到 `EXTERNAL_VERIFIED`
  不是本包的期望值。
- 真正的停止/纠偏判据是下面这几条，任何一条出现都要停并如实记录：
  1. 把原始 cycles 说成已校准的 µs，或在没有批准的情况下给 µs；
  2. 没有任何来源就宣称 `clock_basis` 升级（CLI 里根本没有升级入口，`EXTERNAL_VERIFIED` 需要另行批准的
     MCO/独立时基 + 路由/分频/测点，见计划 §2.5 R3a）；
  3. `body_valid=0` 的记录参与时长统计，或 `invalid_reason` 被裁剪成单一归因；
  4. 拿 `body_valid=1` 给包围区间（`post-pre`）背书 —— `enclosing_valid` 本批恒为 0；
  5. `COUNTER_UNAVAILABLE` / `COUNTER_UNCHECKED` / `GATE_LOST` / `NO_PROGRESS` 时**不产出**窗口时长
     （转 `BLOCKED_COUNTER`）；零值绝不解释成 0 耗时。
- 主体区间"小于一圈"的依据只有终点读的 COUNTFLAG（窗口内无重装），上界 `LOAD+1`；**不得**用
  `SysTick->VAL` 做模差分（计划 §2.5 的混叠反例：`N=LOAD+1` 时 `300→200` 无法区分）。

## 5. 失败即停（不自动重试到绿）

出现以下任一项 ⇒ 立刻停止本包，保留现场与原始输出，**不**自动清故障、不自动复位掩盖、不重复烧录：
关闭动作报错或 `sp_protect` 任一路 raw 非 `RT_EOK`；证据丢失/无法绑定到 §1 的镜像散列；观察到意外
HIGH 或意外 STEP；接线/供电前置无法确认；`dump` 出现未完成槽被当完整样本；某批条数与触发次数不符；
`spm` 的名字/参数合同被违反（例如只能用 `spm`，不能用 `spm_cmd`）；`spm cap` 显示的四宏不为 `RT_FALSE`
或 `schema` 不为 2。

## 6. 现在需要用户给的三样东西（缺任何一样，本包保持 NOT_READY_FOR_AUTHORIZATION）

1. 现场信息：P-01 那七项（含 VM/逻辑电源来源与断电判据、模块可否拔出、SW1 现位、电机是否物理未接、
   是否有人在场操作）。
2. 工具信息：具体串口/烧录通道与探针（本包未做任何设备扫描），以及是否允许一次烧录 + 一次启动/复位。
3. 批准范围：是否按**整个包**批准（P-01..P-14 按序推进，包内不为每条命令重复确认；只在前置失效、
   越过已批范围或出现真实失败时停）。

不需要用户重复提供的：既有 `USER_CONFIRMED` 静态电气与现有 SW1 功能结论保留，五点电压不作本包前置；
仪器（示波器/逻辑分析仪）缺失不阻塞本包 —— 那只会让 T06/T09 类瞬态项继续 `BLOCKED_INSTRUMENT`，
而 A0 只交状态/顺序与（在计数可用时的）有效原始 cycles。

## 7. 本包完成后仍然开放的东西

`B-1`（门内窗口真实时长 + RX/EXTI 丢失容忍）、`RISK-03/03a`（目标调度与最坏响应）、
`BUG-008`（STEP 物理波形）、物理 `RISK-04-b`、`M-1/M-2/M-3`（含"寄存器写→焊盘"时间关系，
需另行批准的共同事件/标记）、T03–T13 与包 B/C。主机链 TMA01..TMA24 的 PASS **只**说明
记录/判定/命令门/命令名接缝在模型内正确，绝不关闭任何板级风险；A0 软件与两份镜像目前
**尚未**取得 `SOFTWARE_REVIEW_GO`，也没有板级使用许可。

`STOP: A0_R1_OFFLINE_FIRMWARE_READY / AWAITING_INDEPENDENT_SOFTWARE_AND_IMAGE_REVIEW；
NO BOARD/SERIAL/DEBUGGER/GPIO/TMC/STEP/MOTOR AUTHORIZATION；FOUR TARGET SAFETY MACROS RT_FALSE。`
