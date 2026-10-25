# A0 目标机测量操作包（待整包审议，本轮零执行）

任务 ID：`phase8-target-measurement-a0`；日期：2026-10-25（Asia/Shanghai）。
性质：**未来动作清单**。本文件不授予任何权限；除另有明确批准外，本文所有步骤的状态一律是
`NOT EXECUTED / NOT AUTHORIZED`。它与 `reports/plans/phase8-target-measurement-plan.md` §7 的包 A 是
"子集"关系：本包只覆盖 **A0 关闭路径**，不含 `HIGH/STEP 输出`、`DIR` 改变、TMC apply、改线/供电/SW1、
真机清故障、selftest 与任何电机动作。

## 1. 本包绑定的镜像与源码身份（批准对象就是这两个散列，不再是"以后某个镜像"）

| 项 | 值 |
|---|---|
| 构建用源码（`code_commit`） | `678768739dd8e964fdcc428ef1168e4353c167f7`（生产实现 + 主机链 + 审计） |
| 工程登记提交（含 `project.uvprojx` 双目标改动） | 见交接 `phase8-target-measurement-a0` 的 `engineering_commit`；构建即在该定稿树上执行 |
| 接受软件对象（不变） | `bcdec631266239008734271ba75beab435bf30eb`（S2 `SOFTWARE_REVIEW_GO`） |
| 准备轮接受对象 | `bbf66ba34d7b31a217c643921aa4192df2f59a56`（`PREP_REVIEW_GO`） |
| 默认镜像 | `reports/firmware/phase8-target-measurement-a0/default.bin`
  = `ef69404d388a7977f170550e324dfa50fd0cf152e41bc2831410c05628a92a20`，251148 B；
  Keil 目标 `rt-thread`，`Code=173762 RO=76806 RW=2188 ZI=83784`，0 Error / 0 Warning；
  `NS800_TARGET_MEASUREMENT_A0` 未定义(=0)，链接映像内 `spm_` 符号行数 **0** |
| 测量镜像 | `reports/firmware/phase8-target-measurement-a0/measure-a0.bin`
  = `e9ba8dae1a4b4d59697d5844551bfcd3fbba9061cc8fbcd11784cd7b61a8fc63`，259868 B；
  Keil 目标 `rt-thread-measure-a0`，`Code=179858 RO=79430 RW=2188 ZI=91504`，0 Error / 0 Warning；
  仅相对默认目标多一个宏 `NS800_TARGET_MEASUREMENT_A0=1` 与独立输出目录 |
| 两目标差异（XML 逐叶子核对） | 只有 `TargetName / Cads.Define / OutputDirectory / OutputName / ListingPath / AfterMake.UserProg1Name` 六处；源文件清单两目标各 135 项且逐条一致 |
| 四宏（两个变体都相同） | `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` `motor.h:69`、`STEP_DIAG_ISOLATED_ENV_CONFIRMED` `motor.h:83`、`TMC_DRIVE_CONFIG_HW_VALIDATED` `tmc2209.h:100`、`SAFETY_LIMIT_INPUTS_ENABLED` `project_board.h:51` 全部 `RT_FALSE`；`NS800_TARGET_MEASUREMENT_A0` 只是记录开关，**不参与任何安全判定** |
| ELF/AXF | 只留在本地（调试信息可能带本机路径，未脱敏不上传）；`build_manifest.json` 记录其本地 SHA-256 |
| 回退能力 | **`BLOCKED_ROLLBACK`**：`default.bin` 是本轮默认变体，**不是**板上那颗 e40 的已验证回退镜像；板上固件的确切文件与散列未知（本轮未读 Flash），不得凭标签名假装已有可回退镜像 |

## 2. 步骤表（每步：命令 / 次数 / 副作用 / 停止条件 / 证据字段 / 未满足的现场前置）

状态列含义：`READY` = 只缺用户批准；`NOT_READY_FOR_AUTHORIZATION` = 还缺现场信息，**不能现在批准**；
`NOT EXECUTED` = 本轮未做任何一条。

| # | 步骤 / 命令 | 次数 | 真实副作用（按源码逐条分类，不按名字猜） | 停止条件 | 证据字段 | 前置 | 状态 |
|---|---|---|---|---|---|---|---|
| P-01 | 现场确认并拍照记录：电机是否物理未接、TMC 模块在位状态、VM 与逻辑电源来源、断电判据（草案 `P-5`）、SW1 当前位、人在场 | 1 | 无（只读现场） | 任一项说不清 ⇒ 整包不开始 | 现场记录表 + 时间 | 现场信息 | **NOT_READY_FOR_AUTHORIZATION** |
| P-02 | 烧录 `measure-a0.bin`（散列见 §1）到板 | 1 | 擦写应用区；启动后自动执行硬件初始化 | 散列与 §1 不一致即拒绝烧录 | 工具输出、镜像散列、目标名、四宏 | 端口/探针具体型号未知（本轮未扫描设备） | **NOT_READY_FOR_AUTHORIZATION** |
| P-03 | 上电/一次复位 + 控制台观察启动 | 1 | **启动路径本身不是只读**：GPIO 表初始化（输出脚写默认电平）、EPWM 硬件初始化（写 TBCTL 分频/模式）、uart1/uart2、ADC/SPI、传感器与黑匣子线程、flash 配置加载、自检与状态机 | 启动异常（意外 HIGH、意外 STEP、卡死、断言） ⇒ 停 | 启动日志全文 + `ps` | P-01/P-02 | NOT EXECUTED |
| P-04 | `free`；`ps` | 各 1 | 纯 RAM/内核对象遍历，无外设访问 | 无 | 输出原文 + 栈 `max used%`（`usage` 列恒 `N/A`，CPU-usage tracer 未开） | 串口白名单 | NOT EXECUTED |
| P-05 | `safety_status`；`step_status`；`diag_status` | 各 1 | 只读：`safety_status` 对 PC.23 做 `rt_pin_read`（**读**输出锁存，不写）；`step_status`/`diag_status` 只读模块内 RAM 影子 | 出现"!!已使能!!"或 `step_status` 报"输出中" ⇒ 立即停并记录 | 输出原文 + `enn_feedback` 口径说明 | 同上 | NOT EXECUTED |
| P-06 | `motor_status` | 1 | **可无限等待**：`motor_get_snapshot()` 先取 `mot_lock`（`motor.c:143` = `RT_WAITING_FOREVER`），之后 `sp_get()` 无等待；不写 GPIO/TMC/UART | 长时间无返回 ⇒ 记录"被慢锁挡住"，**不得**据此判保护失效 | 返回耗时（人工/日志时刻，非窗口时长）、`armed/health/stop_result` | 同上 | NOT EXECUTED |
| P-07 | `config_show` | 1 | 取 `cfg` 锁（`project_config.c:136-143`，同一把可 FOREVER 等待的配置锁）；不碰 UART | 同上 | 配置快照原文 | 同上 | NOT EXECUTED |
| P-08 | `blackbox_status` | 1 | `ns_log_stats()` 取 `service_lock`/`queue_lock`（`ns_storage.c:226-232`，`RT_WAITING_FOREVER`）；**读 Flash 统计**，不写 | 同上；出现写失败/丢弃计数增长要单列 | 原文 + `pending_before_after` 口径 | 同上 | NOT EXECUTED |
| P-09 | `spm cap` | 1 | 只读打印：schema/槽数/记录数组字节/四宏编译值/`counter_status`/`clock_basis`/状态计数 | 显示的四宏不是 `RT_FALSE` ⇒ 立即停（拿错了镜像） | 输出原文 + `image_sha256` 对账 | 测量镜像已启动 | NOT EXECUTED |
| P-10 | `spm init` | 1 | **唯一的寄存器写**：置 `CoreDebug->DEMCR.TRCENA` 与 `DWT_CTRL.CYCCNTENA`（保留其它位，不清 `CYCCNT`）；读 `DWT_CTRL`（含 `NOCYCCNT` 位）与 `SysTick->CTRL/LOAD`；两次有界读取确认递增 | 报 `COUNTER_UNAVAILABLE` ⇒ 本包时长项转 `BLOCKED_COUNTER`，只交状态/顺序；报 `guard=NOT_ESTABLISHED` ⇒ 主体区间保持 `INTERVAL_UNPROVEN` | `counter_status`、`clock_basis`、`DEMCR/CTRL/a/b/LOAD` 原值（P-10 在普通线程上下文，非屏蔽窗口） | 同上 | NOT EXECUTED |
| P-11 | `spm start trip_fast` → 1 次 `spm trip` → `spm stop` → `spm dump` | 1 场景 × 1 次确认 | `spm trip` = **一次** `safety_force_shutdown(FAULT_SELF_TEST=8)`：经既有 Safety/黑匣子/存储路径可触发采集与 **Flash 写**；**不是只读命令**，也不做人工恢复 | 未出现预期抑制/许可 LOW 序列、或 `dump` 里出现 `body_valid=1` 却 `clock_basis` 未升级 ⇒ 停并保留现场 | 完整 CSV 行（含 `epoch_before/after`、两路 raw、四时间戳、`counter_status/clock_basis/sample_valid/invalid_reason`）+ `dropped` | P-09/P-10 | NOT EXECUTED |
| P-12 | 同一入口取样：`spm start trip_fast` → **16 次** `spm trip`（每次之间允许人工 `spm status`）→ `spm stop` → `spm dump` → `spm clear_records` | 16 | 每次一笔正式关闭 + 可能一次黑匣子提交；64 槽单批次只追加，**第 65 次起只计 `dropped`**，不覆盖旧槽 | `dropped>0` ⇒ 该批分布不完整，如实标注；关闭失败或证据无法绑定镜像 ⇒ 停 | 16 条 CSV + `samples_expected_observed_dropped` | P-11 已确认路径 | NOT EXECUTED |
| P-13 | `spm start off_attempt` → 由正式 `motor_disarm`（或已在 P-11/P-12 覆盖的 `spm trip` 链）取得该入口样本；随后 `trip_for_operation` 只在**其真实调用点**自然出现时记录 | 1 + 16 | `motor_disarm` 走正式门与一次 `rt_pwm_disable` + 独立许可 LOW，并取 `mot_lock`（可 FOREVER）；**不**请求 HIGH | 入口一次都没被走到 ⇒ 记 `NOT_REACHED`，**不得**为凑样本强造真机故障或翻宏 | 各出口的 `branch_id`、`pwm_called/low_called`、与夹具独立计数口径对应的现场人工观察 | 现场 armed 状态已知 | **NOT_READY_FOR_AUTHORIZATION** |
| P-14 | 收尾：`spm dump`（最后一次）、控制台 `spm status`、`clear_records`、断电判据按 P-01 执行 | 1 | 只有 `state!=ACTIVE` 且 `in_flight=0` 才允许导出/清 RAM；清 RAM **不动** Safety 故障、抑制或失败锁存 | 任何 BUSY 被强行绕过 ⇒ 停 | 最终导出文本 + 断电记录 | P-11..P-13 | NOT EXECUTED |

补充纪律（对整包生效）：

- 参数错误/未知子命令/非法上下文在**任何**计数读取、状态变化与安全动作之前被拒绝且不打印；
  这一点在主机链 TMA17 已经判定，上机只需按同一命令序列复现其文本可见性。
- `spm` 没有 `arm/start`（开启输出）、许可 HIGH、DIR、STEP 频率、TMC 写、清故障、改宏或直接改 `epoch`
  的子命令；不使用 `ss_test`（它会继续走 `fault_reset`）、`runtime_selftest`/`motor_ramp_selftest`/
  `diag_selftest`/`blackbox_selftest`/`flash_test`/`flash_unlock`/`config_save` 等未列入本包的命令，
  也不使用裸 `pin`/`pwm`/`tmc_uart_probe`；旧 e40 固件的命令语义**不得**移用以本。
- **排除清单**：`pin_status` 会重新写每个输出脚的默认电平（`safety_gpio.c:402-417`，对 DRV_ENABLE 走
  `safety_drv_enable_write(0)`），因此它不是只读命令，本包不放行；`GPIO_12/13`（控制台）、已弃用
  `PF.21`（`pf21_diag`/`pf21_ab` 会真的驱动它）、`PA.1`（EPWM1_B 保留）一律不得占用。
- 计时口径：只有 `counter_status=COUNTER_OK` 且逐条 `sample_valid=1` 才进入时长统计；
  `COUNTER_UNAVAILABLE/UNCHECKED` 时**不产出**窗口时长（`BLOCKED_COUNTER`），零值绝不解释成 0 耗时；
  `clock_basis` 未到 `EXTERNAL_VERIFIED` 前只报**原始 cycles**，不给 µs；包围区间（`post-pre`）本批
  恒无效，不得拿主体有效性给它背书（计划 §2.5/§8）。

## 3. 失败即停（不自动重试到绿）

出现以下任一项 ⇒ 立刻停止本包，保留现场与原始输出，**不**自动清故障、不自动复位掩盖、不重复烧录：
关闭动作报错或 `sp_protect` 任一路 raw 非 `RT_EOK`；证据丢失/无法绑定到 §1 的镜像散列；观察到意外
HIGH 或意外 STEP；接线/供电前置无法确认；`dump` 出现未完成槽被当完整样本；`spm cap` 显示的四宏不为
`RT_FALSE`。

## 4. 现在需要用户给的三样东西（缺任何一样，本包保持 NOT_READY_FOR_AUTHORIZATION）

1. 现场信息：P-01 那七项（含 VM/逻辑电源来源与断电判据、模块可否拔出、SW1 现位、电机是否物理未接、
   是否有人在场操作）。
2. 工具信息：具体串口/烧录通道与探针（本包未做任何设备扫描），以及是否允许一次烧录 + 一次启动/复位。
3. 批准范围：是否按**整个包**批准（P-01..P-14 按序推进，包内不为每条命令重复确认；只在前置失效、
   越过已批范围或出现真实失败时停）。

不需要用户重复提供的：既有 `USER_CONFIRMED` 静态电气与现有 SW1 功能结论保留，五点电压不作本包前置；
仪器（示波器/逻辑分析仪）缺失不阻塞本包 —— 那只会让 T06/T09 类瞬态项继续 `BLOCKED_INSTRUMENT`，
而 A0 只交状态/顺序与（在计数可用时的）有效原始 cycles。

## 5. 本包完成后仍然开放的东西

`B-1`（门内窗口真实时长 + RX/EXTI 丢失容忍）、`RISK-03/03a`（目标调度与最坏响应）、
`BUG-008`（STEP 物理波形）、物理 `RISK-04-b`、`M-1/M-2/M-3`（含"寄存器写→焊盘"时间关系，
需另行批准的共同事件/标记）、T03–T13 与包 B/C。主机链 TMA01..TMA18 的 PASS **只**说明
记录/判定/命令门在模型内正确，绝不关闭任何板级风险。

`STOP: A0_OFFLINE_FIRMWARE_READY / AWAITING_INDEPENDENT_SOFTWARE_AND_IMAGE_REVIEW；
NO BOARD/SERIAL/DEBUGGER/GPIO/TMC/STEP/MOTOR AUTHORIZATION；FOUR TARGET SAFETY MACROS RT_FALSE。`
