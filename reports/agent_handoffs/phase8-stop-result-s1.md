# 交接：安全停机可靠性 S1 —— 关断结果可信与失败锁存（phase8-stop-result-s1）

任务书：`NS800_Stop_Result_S1_Qoder_Task_20261023.md`
日期：2026-10-23（Asia/Shanghai）
性质：**离线固件 + 主机注入验证 + Keil 编译 + 普通 commit/push**；本轮**零硬件授权**，
不进入 F2e/F3，不做运动测试，不烧录、不读回、不开任何串口/调试会话。

## 1. 分支 / 基线 / 提交

| 项 | 值 |
|---|---|
| 仓库/分支 | `LINboss666/ns800-stepper-safety` @ `phase8/tmc-drive-config` |
| 开发基线 | `1c45823d7b498706f166fc856521b8d564870748`（开工前本地=远端=基线，无他人前进） |
| 实现 checkpoint（**被测生产内容**） | `62873bd8aa851ae3010dd1c04cba0aa3e1e22821` |
| tested_commit | 同上 `62873bd`（全部主机验证与 Keil 都在该 commit 之后执行，未再测旧 HEAD） |
| 上一工作包 | 安全数据语义 `SOFTWARE_REVIEW_GO`（GO 对象 `689c8b3`，生产内容 `e974d48`）—— 本轮未改写 |
| 冻结引用 | main `1b3757ee…`、phase7/full-embedded-software `6d3fbc55…`、v0.7 标签对象 `097935fe…`（解引用 `e40a9b95…`）—— 未移动 |

## 2. 实际改动（范围严格限于任务书 §5）

| 文件 | 改动 |
|---|---|
| `applications/stop_result.h` | **新增（本轮唯一新增头，无新增 .c/线程）**：关断结果语义枚举（未尝试 / 未取得设备 / 已请求输出 / 命令 0Hz 未确认 / API 接受 / API 失败）、调用来源枚举、`mot_stop_result_is_off()` / `is_uncertain()` / 名称函数 |
| `applications/motor.h` | 快照末尾追加 4 个字段（旧字段次序不变）；新增第 7 门 `MOT_GATE_STOP_UNCONFIRMED (1u<<6)`；`motor_get_stop_state()` / `motor_clear_stop_latch()`；disarm/estop 返回值合同改写 |
| `applications/motor.c` | 唯一 checked 关断入口 `mot_pwm_output_off(src)`；init/ramp 到 0/apply 失败/arm 回滚/守卫停机/disarm/estop 全部消费真实 rc；PWM 与许可两个原始 rc 分列保存，对外失败优先返回 PWM 错误；`motor_stop()`/`set_target_hz(0)` 只记"软件命令值"；斜坡体抽成 `motor_ramp_tick()`；注释按 2026-10-23 口径更正 |
| `applications/step_pwm.h/.c` | `step_pwm_force_stop()` 返回 `rt_err_t`；pwm_test stop 与统一停机共用一条 checked 路径；失败**不释放诊断所有权**、不允许再 enable 覆盖；未持有/无句柄/曾失败三种情形分开；`step_status` 不再写 `no(安全)`；新增 `step_pwm_get_stop_state/off_confirmed/clear_stop_latch` |
| `applications/safety_state.c` | 统一停机收集两路结果（谁失败都不跳过另一路、不再打印 `STEP stopped`、黑匣子仍只一次、内部不投事件）；`fault_reset` 增加"两条输出路径本次均有有效关断结果"的只收紧复核，并保留 IO70 的 disarm 后置 ENN 确认；S1 锁存唯一解除点 |
| `tests/host/host_main.c` / `io_main.c` | 仅同步新接口签名与桩的真实返回值 + 三个新只读桩；**未改任何既有断言或用例集合** |
| `tests/host/ps_main.c` + `shim_ps/` + `run_stop_result_s1.sh` | **新增第四套并列链**（不复用其他链夹具） |
| 文档 | BSP `开发进度.md` / `待办事项.md`、`tests/host/README.md`、本交接件与证据包 |

未触碰：TMC 驱动/配置/UART 算法、安全 GPIO 与 IRQ 线程、传感器/标定/诊断、刚 GO 的帧与
黑匣子语义/存储格式、底层 PWM 与 RT-Thread 驱动、`project.uvprojx`、`.gitignore`、公共 env、
已审 runner、`inject.py`、旧证据与旧采集脚本。

## 3. 新的返回值 / 锁存 / 清除合同（本轮对外契约）

- `mot_pwm_output_off(src)`：`RT_EOK` = **驱动 API 接受了关断请求**（不是"无脉冲"）；
  `-RT_EIO` 等 = 输出状态未知并锁存；未取得句柄 = `-RT_ENOSYS` 并锁存。不做自动重试。
- `motor_disarm()` / `motor_emergency_stop()`：返回值 = PWM 失败优先，否则许可写确认结果；
  两个原始 rc 分别留在快照 `stop_rc` 与日志中，调用方**不得**把任何非零解释成"只是 GPIO 失败"。
- `motor_init()`：关断失败 ⇒ 仍尝试许可 LOW，但不创建斜坡线程、`health=FAILED`、不称 init 成功。
- 失败锁存 `stop_fail_latched`：一次后续成功的普通 stop **只**更新"当前关断结果"，不清锁存、
  不解 Safety 故障、不自动使能；解除只发生在 `fault_reset()` 全部复核通过之后。
- 第 7 门 `MOT_GATE_STOP_UNCONFIRMED`：锁存期间拒绝 arm/start/诊断借用；前六道门与四个宏
  条件**一字未改**（只新增、只收紧）。
- `step_pwm_output_active()` 判据保守化：仍启用 **或** 关断失败/未取得设备 ⇒ TRUE，
  Motor 不得接管；`step_pwm_off_confirmed()` 供借用门与统一停机报告使用。
- 统一停机：两路都执行、一次黑匣子触发、`FAULT_LATCHED` 照常锁存、故障码编号与格式不变
  （软件关断失败沿用投递方给的 code，通常是 `FAULT_SOFT`），详细 rc 由状态/日志保存。

## 4. 新主机链（真编译 step_pwm.c + motor.c + safety_state.c）

- runner：`bash tests/host/run_stop_result_s1.sh`（退出码 0/1/4/127；无 ONLY、无只读校验等可选入口）
- 夹具：`tests/host/ps_main.c`；兼容层：`tests/host/shim_ps/rtthread.h + rtdevice.h`
- 桩纪律：PWM 每个 API 的 rc 可独立注入，并**独立**保存"模拟输出是否仍在跑"——
  **disable 失败时桩保留输出运行，不替固件清掉**；许可写结果、许可脚实测电平、ENN 反馈判定
  是三个独立注入量；前置"已关断"一律由生产路径自己真实取得，不伪造成功过门。
- 单线程确定性：`rt_thread_create` 只计次数；斜坡的一拍由夹具直接调用生产 `motor_ramp_tick()`
  单步执行（等价性论证见 motor.c），因此本链不测并发、不测停机时延。
- 变体与登记：`target`（四宏全 FALSE）36 例、`host_ovr`（三宏仅主机 TRUE）45 例；
  台账逐 id 必须"出现恰好一次且判 PASS"，缺席/重复/未登记/SUMMARY 对不上=基础设施故障。
- 覆盖：PS01 init、PS02/PS03 estop 与两 rc 分列、PS04 disarm、PS05/PS05h-i 诊断所有权、
  PS06 统一停机、PS07 四条生产路径、PS08/PS09 失败锁存与人工恢复、PS10 ENN 无样本 +
  IO70 后置复核保留、PS11 目标宏门与状态查询零副作用。

## 5. 两项局部反向验证（只改仓外一次性副本）

| 注入 | 变体 | 形状 | 指定失败身份 | 真实 rc |
|---|---|---|---|---|
| `rev_motor_off_ignores_failure`（`motor.c`） | target | 关断 helper 恒记 API 接受并把 rc 覆写成 EOK | `PS01a`、`PS02a`、`PS04a` | rc=1，命中 3/3，额外 FAIL 14，台账自洽 |
| `rev_diag_fail_releases_owner`（`step_pwm.c`） | host_ovr | 失败也清 `step_enabled` 且不置锁存 | `PS05a`、`PS05b` | rc=1，命中 2/2，额外 FAIL 12，台账自洽 |

正常对照（同一 commit、未注入）：target 36/36、host_ovr 45/45，proc_rc=0。
判据与正向共用同一个 `validate_ledger()`；构建失败、非 0/1 退出、缺例或未登记 id 一律判
INFRA-FAIL，不当作检出（本轮反向②第一次就是这样被拒后修的，见证据包 NOTES 第 2.6 条）。

## 6. 既有回归与目标编译（全部真实执行）

| 链 | 结果 |
|---|---|
| `build_and_run.sh` | V1 25/25、V2 22/22，runner rc=0（旧 ID 与断言未变） |
| `run_io_behavior.sh` | off_target 27 / on_target 29 / off_ovr 40 / on_ovr 42 = 138 例全 PASS，非法宏值仍被编译拒绝，rc=0 |
| `run_negatives.sh`（内含元负例） | 13 例全 OK（执行集合=请求集合）；元负例 M1 M1c M2 M2c M3 M3c M4 M5 全 META-OK/CONTROL-OK，rc=0（未重复跑同一组） |
| `run_safety_data_semantics.sh` | OFF 21 / ON 20 全 PASS，rc=0（源与夹具未改，仅因 `motor.h` 可能影响编译而重跑） |
| Keil Rebuild All | `0 Error(s), 0 Warning(s)`，工具退出码 0；Code=151866 RO=68918 RW=2184 ZI=83428（较上一轮 +3336/+4188/+8/+16）；BeforeMake 全 0、AfterMake 仅 `fromelf --bin` |
| 未重跑 | R4/SW1 采集工具（未改且 T-01/T-02 仍 HOLD）、`sd_runner_rework_check.sh`（该 runner 未改） |

四道宏 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED`、`STEP_DIAG_ISOLATED_ENV_CONFIRMED`、
`TMC_DRIVE_CONFIG_HW_VALIDATED`、`SAFETY_LIMIT_INPUTS_ENABLED` 目标值仍全部 `RT_FALSE`，
主机覆盖只存在于测试编译命令行，不进工程、不进正式固件。

## 7. 真实失败与修正

见 `reports/reviews/phase8-stop-result-s1/NOTES.txt` 第 2 节（7 条，含两处"判据被自家文案
触发"与"人工恢复被自家判据堵死"的鸡生蛋问题、以及反向②第一次被判 INFRA-FAIL 的形状）。
本轮没有隐藏失败，也没有把夹具不自洽当成检出。

## 8. 未验与边界

- 未上板：板上仍是 `USER_REPORTED_E40`；不读回、不烧录、不开串口、不写真实 GPIO/TMC。
- `RT_EOK` 只称"驱动接受了关断请求"；STEP 波形仍未验收（BUG-008），主机桩的"输出是否还在跑"
  不是波形证据。
- 停机时延**没有**上界结论；405ms 那段注释已去掉"可靠上界"的误导表述，保留未测量口径。
  RISK-03/03a、RISK-04-b 继续开放。
- S2 未做：`ss_lock`/`mot_lock`/TMC 配置事务/UART xfer 的 `RT_WAITING_FOREVER`、
  在途 arm/enable 与停机的竞争、线程重构。本轮不声称在途操作取消已被验证。
- 主机链不模拟调度；PS05a..g / PS07* / PS09c 的前置在目标宏下不可达，只在 host_ovr 登记。
- RO 增量主要来自新增中文日志字面量，逐项归属未单独测量。
- "门芯片损坏"是维修前的历史解释；静态电气与现有 SW1 功能已按用户确认登记为
  `PASS (USER_CONFIRMED)`，本轮未索取重复测量，也未把宏的 FALSE 归因于已证实故障。

## 9. 链接

- 交付比较（基线 → 实现）：https://github.com/LINboss666/ns800-stepper-safety/compare/1c45823...62873bd
- 交接：`reports/agent_handoffs/phase8-stop-result-s1.md` / `.json`
- 证据：`reports/reviews/phase8-stop-result-s1/`（NOTES.txt、disable_call_site_audit.txt、
  s1_chain_ledger.txt、s1_reverse_result.txt、existing_regression_summary.txt、
  keil_rebuild_all_after.txt；二进制/完整日志/工作目录一律留在仓外，不生成 ZIP）

## 10. STOP

`STOP: AWAITING_INDEPENDENT_REVIEW` —— 不自动开始 S2、F2e、F3，不做任何硬件动作。
待用户把本交付转交独立复审后，由复审方按实际代码给判定。本轮不解决的锁等待、UART 阻塞、
在途 arm/enable 竞争与物理停机时延已完整记录在上第 8 节，未补写任何时间上界。
