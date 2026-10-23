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
（**本小节措辞以第 13.2 节为准**：应撤回的是那两条**总结性结论**，旧审计调用表里 `538` 那一行
没有具名接收者是**当时正确**的历史观察，属证据、保留不撤回。）

### 11.5 本轮真实失败与修正

见 `.../phase8-stop-result-s1-r1/NOTES.txt` 第 2 节（5 条：夹具前置失败跳用例造成缺例、
R03 第一版被诊断侧陈旧失败二次死锁、PS15b 断言写错观测量、shell 内嵌 Python 的 `\n`
被折叠导致一次锚点失配与一处 C 字符串缺终止符、Keil 日志目录不存在导致"rc=0 但无日志"）。

## 12. STOP（覆盖第 10 节的上一状态）

`STOP: AWAITING_INDEPENDENT_REVIEW` —— 返修对象 `8ca4f6a`，等待独立复审按实际代码判定；
本轮不登记 GO（复审 §6.1 明确"本轮 S1 结论 REWORK，不得先登记 GO"），不自动开始 S2/F2e/F3，
不做任何硬件动作。S2 的无界等锁、UART 阻塞与在途 arm/enable 竞争仍未处理，无停机时延上界。

## 13. 独立复审结论与 GO 登记（2026-10-23 同日，本轮＝文档轮）

复审件：`NS800_S1_R1_Independent_Review_32a311a_20261023.md`
**结论 `SOFTWARE_REVIEW_GO`（限定软件与指定主机验证范围）** —— `S1-NEW-01..04` 关闭，
**无新增阻断返修项**，S1＋R1 收口；最终接受与阶段推进由用户决定。

登记口径（复审 §6 原文口径）：

> 2026-10-23：独立复审 `SOFTWARE_REVIEW_GO`，接受 `32a311a76284796ad535d1f7956e5dc101520561`，
> 生产实现与 tested_commit 为 `8ca4f6aca62779d18345c9c3af44e5419d5c2985`。S1-NEW-01..04 关闭，
> 无新增阻断返修项，S1＋R1 的软件与指定主机验证工作包收口。云端 Linux/GCC13.3 独立复跑修后
> 48/64、两项反向、V1/V2、I/O 138 及 6 个补充场景通过；Keil 仅核阅 Agent 本轮 0/0 摘录，
> 未由云端重建。本 GO 不覆盖目标 RT 调度、PWM 物理波形或停机时延，也不授权任何硬件动作。

### 13.1 复审端的独立执行（与本 Agent 证据相互独立）

修后 `target 48/48`、`host_ovr 64/64`（pre_bad=0，进程 0／runner 0）；Motor 反向命中
`PS01a/PS02a/PS04a`、诊断反向命中 `PS05a/PS05b`（各自构建成功 + 进程 1 + 完整台账，总出口 0）；
`V1 25 / V2 22`、I/O 138 例、修前同夹具对照 `44/48` 与 `47/64`、以及 6 个新进程补充场景全部按其
真实结果记录。Keil 为 `AGENT_REPORTED`（云端未重建），真板／波形／最坏时延 `NOT_TESTED`。

### 13.2 三条口径合并（复审 §5，均**不**新增返修项）

1. **应撤回的是结论，不是历史观察**：上一版审计里应撤回的是"返回值一律被具名变量接住"与
   "每个停机动作内 disable 至多一次"这两条**总结**；调用表中 `motor.c:538` 那一行（无具名接收者）
   是当时**正确**的观察，保留、不改写成"当时就对"。已按此改写 `call_site_audit_correction.txt`
   的开头表述（正文调用表未动，复审确认其正确）。
2. **`PS12c` 的焊盘前提**：仓库夹具那行打印的 `pad=` 取自引脚表 level 且未置 `pad_stuck`，
   因此 `pad=0` **不能**证明"许可实际保持 HIGH"。R01 的该前提由复审端独立探针显式置
   `pad_stuck`＋读 `pad_level` 取得（`pad=1 running=1 dir_rc=-8 dir_writes=0`）。本轮**未改夹具**
   （改前提就要重跑该链）；以后复用该场景时同步完善前提与观测。
3. **总数不得用选摘充当**：修前真实总数是 `target fail=4`、`host_ovr fail=17`（另选摘条目数不是
   总数）；反向的"额外 FAIL=19/22"是各变体**总 FAIL 数**（含已命中的指定身份），不是"另有 19/22 项"。
   修前 host 的 `pre_bad=21` 说明串行前置会挡住后续用例，故 `PS15a` 修前那一行不能单独当作 R04
   的独立复现 —— R04 由新进程探针直接复现并在修后确认。

另：`safety_state.c` 中 `fault_reset()` 旁"这里只读、不再关断"一类旧措辞已被本轮新增的
`step_pwm_force_stop()` 刷新行为覆盖；按复审 §5.3 **不为注释单独改 APP／重建 Keil**，已登记为
BSP `待办事项.md` 的未决项，等下次真实生产改动时一并整理。

### 13.3 本轮（文档轮）改了什么

只改文档与证据文字：本 MD/JSON、BSP `README.md`／`开发进度.md`／`待办事项.md`、
`tests/host/README.md`、r1 证据目录内 `NOTES.txt`／`call_site_audit_correction.txt` 的表述，
并新增 `reports/reviews/phase8-stop-result-s1-r1/go_registration_and_claims_calibration.txt`。
**生产 `.c/.h`、夹具、shim、runner、Keil 工程一律未动 ⇒ 未重跑任何链**，引用的数字全部来自
`62873bd`（首轮）与 `8ca4f6a`（返修轮）的真实执行。

顺带校准**返修轮（r1）的生产范围**（复审 §1）：实际变更只有 `motor.c`、`motor.h`、`step_pwm.c`、
`step_pwm.h`、`safety_state.c` 五个文件 —— `stop_result.h` 是**首轮**新增的语义头，r1 零改动；
两份头文件只改注释/契约文案，未改结构字段、枚举值或接口签名。第 2 节表格里那行 `stop_result.h`
属于首轮记录，不应被读成 r1 的增量。范围数字：`78a1333..32a311a` = 21 文件 `+1267/−66`；
`8ca4f6a..32a311a` 的 `.c/.h/.sh` 差异为空。

## 14. STOP（覆盖第 12 节的上一状态）

`STOP: SOFTWARE_REVIEW_GO_REGISTERED / AWAITING_USER_DECISION` —— S1＋R1 在**软件与指定主机
验证范围**收口，接受对象 `32a311a`。本 GO **不等于**"安全停机可靠性全部完成"：下一主线是 **S2
的方案制定**（锁等待、UART 阻塞、在途 arm/enable 与停机的优先关系），其**尚未设计、尚未验收、
任务书未下达**，未经用户授权不得开工。F2e/F3、烧录、串口、GPIO/TMC、STEP／运动、开宏同样
不在本结论内；板上仍是 `USER_REPORTED_E40`，RISK-03/03a 与 RISK-04-b 开放，`sw1_cut.ps1`
T-01/T-02 仍 HOLD。
