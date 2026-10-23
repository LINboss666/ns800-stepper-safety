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

---

## 11. 同轮限定返修 `phase8-stop-result-s1-r1`（2026-10-23，任务书＝复审件第 6 节）

独立复审对 `78a1333` 给出 **REWORK**：S1 主链与既有回归被独立复跑通过，但 5 个补充失败
场景复现 4 项合同遗漏。本轮**只修这四项**，不启动 S2、不做任何硬件动作、不放宽既有门。

| 对象 | 完整 SHA |
|---|---|
| 返修起点（＝上轮最终 HEAD） | `78a1333c9338332cbe03d64d8a6f61babacbcb00`（开工前本地＝远端＝该值） |
| 本轮实现 checkpoint（**tested_commit**） | `8ca4f6aca62779d18345c9c3af44e5419d5c2985` |

### 11.1 四项修法与调用方合同（细节见 `reports/reviews/phase8-stop-result-s1-r1/NOTES.txt`）

- **R01（S1-NEW-01）**：`motor_set_direction()` 追加一层**只收紧**的判据 ——
  `mot.stop_fail_latched || !step_pwm_off_confirmed()` 时返回 `-RT_EPERM` 且一次 DIR 写都不
  产生（`motor.c:970/:988`）。原来的"非静止 `-RT_EBUSY`"与"正常静止可设方向"不变。
  复审的复现（真 arm/start/斜坡＋关断与许可都失败＋统一停机后 DIR 竟返回 EOK）现在被
  `PS12c` 抓住并拒绝。
- **R02（S1-NEW-02）**：`motor_init()` 拿不到 PWM 句柄的错误出口（`motor.c:696-697`）
  现在仍调用既有许可 LOW 接口，明确作废 `mot_dev_ok`/句柄（不留 `dev_ok=TRUE` 配 NULL），
  设备不可用（`stop_rc=-RT_ENOSYS`）与 LOW 的原始返回值分列留档，不建斜坡线程、
  `health=FAILED`、对外仍返回既有的 `-RT_ERROR`；桩侧新增"NULL 句柄调 PWM"计数，全用例恒 0。
- **R03（S1-NEW-03）**：普通外部入口的关断失败现在交给**既有**故障链 —— `motor_disarm()`
  （`motor.c:940`）与 `pwm_test stop`（`step_pwm.c:167`）各投**一次** `EVT_SOFT_FAULT` →
  Safety 线程消费 → `safety_force_shutdown` → `FAULT_LATCHED`＋黑匣子，`fault_reset` 从此可达；
  统一停机、`motor_emergency_stop()`、`step_pwm_force_stop()` 的失败分支绝不投事件（不自激）。
  配套修掉复审未点名但同根的第二层死锁：`fault_reset()` 判定前先调一次
  `step_pwm_force_stop()`（`safety_state.c:495`）刷新诊断侧，与它调 `motor_disarm()` 刷新
  Motor 侧对称（纯朝安全方向，失败即拒绝清除）—— 否则普通诊断 stop 失败后
  `api-failed` 永远留着，人工恢复永不通过。消费者在主机链里是**显式桩**，不冒称新线程端到端。
- **R04（S1-NEW-04）**：apply 失败回退改为**一次** checked 关断并把真实 rc 作为入参交给
  `mot_fail_closed(ae)`（`motor.c:538 → :297`），不再"连关两次、用第二次的成功覆盖第一次的
  失败"；`MOT_STOP_SRC_FAIL_CLOSED` 保留为枚举值但不再是 disable 来源（未复用旧号）。
  据此把 `PS07c` 的"至少两次 disable"次数断言改为"恰好一次"（次数从来不是产品要求），
  保留并加强"失败被消费／来源正确／LOW 动作／锁存／事件次数"业务判据。

### 11.2 用例与台账（`tests/host/run_stop_result_s1.sh`）

新增 ID：`PS12a/PS12b`（DIR，两变体）、`PS12c`（真实运行中的 DIR，仅 host_ovr）、
`PS13a–d`（init 缺句柄出口，两变体）、`PS14a–f`（普通 disarm 交接＋不自激＋人工恢复可达，
两变体）、`PS14g/h/i`（诊断侧同形，仅 host_ovr）、`PS15a/b/c`（单次关断与首错不被覆盖，仅 host_ovr）。
夹具新增两项可观测：逐次 disable 失败序列（默认关闭）与"NULL 句柄调 PWM"计数。
修后真实结果：`target 48/48`、`host_ovr 64/64`，runner rc=0；两项既有反向仍 META-OK
（`PS01a/PS02a/PS04a` 3/3、`PS05a/PS05b` 2/2，proc_rc=1，台账完整自洽）。
修前对照（仓外 `78a1333` 快照、同一份夹具）：`PS12a/PS13a/PS13c/PS14a`（target）与
`PS12c/PS14g/PS14i/PS15a-c/PS07c`（host_ovr）全部真实 FAIL，逐条见
`reports/reviews/phase8-stop-result-s1-r1/pre_post_four_items.txt`。

### 11.3 本轮回归与 Keil

`build_and_run` V1 25 / V2 22；`run_io_behavior` 138 例（27/29/40/42）全部 PASS；
Keil Rebuild All **0 Error / 0 Warning**，`Code=152090 RO=69338 RW=2184 ZI=83428`
（相对首轮 `151866/68918/2184/83428`：Code +224、RO +420、**RW/ZI 不变**）。
未重跑项与依据：`host_main.c`/`io_main.c`/`env.sh`/旧 runner/`inject.py` 本轮零改动 ⇒
按任务书 §6.4.3 不重复 13 例旧负例、M1–M5、SD 41 例与 R4/SW1，沿用 S1 首轮
`62873bd` 的 rc=0 证据（明确记为沿用，不冒称本轮重跑）。

### 11.4 审计更正（不改动旧证据原文）

`disable_call_site_audit.txt` 曾声称"每个返回值都被具名变量接住"与"每动作至多一次关断"，
其中两条与 `motor.c:538` 的实际不符 —— 复审 §4 要求 dated 覆盖：更正见
`reports/reviews/phase8-stop-result-s1-r1/call_site_audit_correction.txt`；旧文件原样保留。

### 11.5 本轮真实失败与修正

见 `.../phase8-stop-result-s1-r1/NOTES.txt` 第 2 节（5 条：夹具前置失败跳用例造成缺例、
R03 第一版被诊断侧陈旧失败二次死锁、PS15b 断言写错观测量、shell 内嵌 Python 的 `\n`
被折叠导致一次锚点失配与一处 C 字符串缺终止符、Keil 日志目录不存在导致"rc=0 但无日志"）。

## 12. STOP（覆盖第 10 节的上一状态）

`STOP: AWAITING_INDEPENDENT_REVIEW` —— 返修对象 `8ca4f6a`，等待独立复审按实际代码判定；
本轮不登记 GO（复审 §6.1 明确"本轮 S1 结论 REWORK，不得先登记 GO"），不自动开始 S2/F2e/F3，
不做任何硬件动作。S2 的无界等锁、UART 阻塞与在途 arm/enable 竞争仍未处理，无停机时延上界。
