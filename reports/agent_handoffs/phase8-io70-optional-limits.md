# Phase 8-IO70 适配 + 宏控可选限位 交接记录

日期：2026-10-21（北京时间）。任务 ID：`phase8-io70-optional-limits`。
分支：`phase8/tmc-drive-config`。性质：**离线固件开发 + 主机验证 + Keil 编译 + GitHub 交付**；
不是电机测试，不重开 F2d 工具返修。任务书 `NS800_IO70_OptionalLimits_Qoder_Task_20261021.md`
整体替代此前"限位退出"的旧版任务书，未叠加执行。

## 1. 基线与 SHA 口径

| 项 | 值 |
|---|---|
| 任务书基线 | `9ace7d0cd8bb04dacaf74e101388e540ca49e349`（核对时本地 HEAD、分支、远端三者一致，工作树 clean） |
| 实现检查点 | `c8d9719f7b222c919d4d06cb8ad7455baf5cad94`（生产代码 + 新 I/O 链；当时的 Keil/回归尚未跑，故未在该提交内声称） |
| 本轮最终提交 | 不在本文件内自引用；在推送后的交付消息里给出 |
| 冻结对象 | `main = 1b3757ee…`、`phase7/full-embedded-software = 6d3fbc55…`、标签 `v0.7-…` → `097935fe…` / 固件 `e40a9b95…`，全部未动 |
| 代码复审口径 | F2d 对 `32f3a416` 的 `SOFTWARE_REVIEW_GO` 保持有效；**本轮是新的接口适配，不用旧审核代替本轮审核** |
| 交付方式 | 仅 GitHub 普通提交 + 普通 push，**无 ZIP** |

## 2. 实现内容（按任务书 §5 的 A–E）

**A. 输入职责与宏控可选限位**
- `project_board.h`：`PIN_NAME_ENN_FEEDBACK "PC.6"`（J4-40/GPIO70）取代急停命名；
  新增 `SAFETY_LIMIT_INPUTS_ENABLED`，默认 `RT_FALSE`，非 `RT_TRUE/RT_FALSE` 的取值经编译期断言拒绝
  （`#error`，实测 `-DSAFETY_LIMIT_INPUTS_ENABLED=2` 编译失败）。
- `safety_gpio.c`：反馈脚在引脚表里被强制校验为 `PIN_MODE_INPUT`，否则 `safety_gpio_setup()` 直接
  `-RT_EPERM` fail closed；限位宏关闭时两路限位**不进入引脚表**（不解析/不配置/不读取）；
  记录"最近一次经回读确认的许可请求电平"供请求态判定使用。
- `safety_thread.c`：活动输入集合按宏选择（关闭时只有 DIAG；开启时加两路 `LIMIT_*_OK`，
  **HIGH=安全、LOW=触发、`PIN_IRQ_MODE_FALLING`**），取消 `estop_isr` 与 `PC.6` 的 IRQ 注册；
  `safety_protection_ready()` 逐路按各自 `safe_level` 判定，且**只在没有已确认使能请求时**要求禁能侧反馈一致
  （避免"arm 后正常 ENN=LOW 导致 start 永远失败"的阶段死锁）；IRQ 注册失败仍逆序回滚、门保持 CLOSED。
- 黑匣子兼容：`EVT_ESTOP`/`FAULT_ESTOP`/`SAFETY_ESTOP` 与限位事件位、既有故障码编号**原样不动**，
  只追加 `FAULT_DRV_FEEDBACK=11`、`EVT_DRV_FEEDBACK=bit7`、门禁位 `MOT_GATE_FB_MISMATCH=bit5`、
  守卫原因 `MOT_GUARD_ENN_FEEDBACK=4`；限位宏关闭时相关事件位没有 GPIO 生产者。
- 未新增 MSH 命令、未新增运行时开关、未新增生产 `.c` 文件（Keil 工程文件条目未动）。

**B. 按请求态判定的 ENN 反馈（真实生产判定函数 + 接入生产链）**
`safety_gpio.h/.c`：纯函数 `safety_fb_evaluate(want, pad, pad_ok, fb, fb_ok)` →
`OK / TRANSITION / CUT_BY_HARDWARE / UNEXPECTED_ENABLE / PAD_MISMATCH / NO_SAMPLE`；
单次采样 `safety_fb_now()`（无延时、无 UART，供只读状态路径与写门使用）；
有界确认 `safety_drv_feedback_confirm()`（最多 3 次 × 1 ms；不一致绝不记成功，
稳定矛盾 → `-RT_EIO`，样本不可得 → `-RT_EINVAL`，窗口内仍在变 → `TRANSITION`/`-RT_ETIMEOUT`）。
窗口常数是测试可见参数，**不是**停机时延或物理时序上界。期望关系：禁能请求↔反馈 HIGH、
使能请求↔反馈 LOW（RevA 图纸 NAND + Q1 反相 + R10 上拉 + 手册 §2.2 推导，仍未实测）。
唯一受控写入口 `safety_drv_enable_write()` 的"写 + 1 ms + 回读 + 重试"保持不变，反馈确认在其之上追加；
**反馈异常时停机动作一律照做**（停 STEP、撤销许可、锁存故障），只是不许声称"最终禁能已确认"。
未做递归取锁：反馈判定只用 `rt_pin_read`，不在回调里取 `ss_lock`/`mot_lock`/配置 txn。

**C. 接入的决策点**：required 启动自检新增"禁能侧反馈确认"；静止保护就绪；`motor_arm`
（写 HIGH 前确认禁能侧 → 写后确认使能侧，失败回滚撤销许可 + 投 `EVT_DRV_FEEDBACK` + 保持故障）；
`motor_start`（armed 之后、起转之前按使能侧重新确认）；armed 期每拍（10 ms）无 UART 复核；
`motor_disarm`/`motor_emergency_stop` 如实报告反馈；`fault_reset` 未确认禁能侧不得清除故障；
TMC 静止写门新增 `TMC_WGATE_ENN_FB_NOT_DISABLED`（不再单看 MCU 焊盘 LOW）；诊断 STEP 借用门同样加此条件，
两道 STEP 宏与其他写门原样保留。五道门结构、失败位含义、配置 txn 与真实完整性复核均未删改。

**D. DIAG 与人工确认独立**：DIAG 极性/边沿、失败关闭、注册回滚、锁存路径不受限位宏影响；
`safety_polarity_confirm`/`safety_irq_enable` 仍默认 FALSE/CLOSED，"用户说接了哪根线"与"门已更换"
都不会自动置位（代码里明确写出这一点）。

**E. 状态输出**：`safety_status`/`motor_status`/`system_status`/`safety_irq_status`/`pin_status`
分列 ENN 反馈原始电平与有效性、许可请求、焊盘实测、期望值、verdict、`SAFETY_LIMIT_INPUTS_ENABLED`
实际值（关闭时 `DISABLED_BY_CONFIG`）、"硬件急停独立存在、软件无独立急停按钮输入"、三宏与 RISK 状态；
`runtime_selftest` 新增 2g 步骤，校验掩码 bit5 与判定一致（不要求特定电平，不伪造 PASS）。
未为展示状态新增任何真实 UART 读或配置写。

## 3. 实际执行的验证（全部真实 rc）

| 项 | 结果 |
|---|---|
| 新 I/O 行为链（`run_io_behavior.sh`，真编译 `safety_gpio/safety_thread/safety_state/motor`） | 4 变体全部真执行且 PASS：`off_target 27` / `on_target 29` / `off_ovr 33` / `on_ovr 35` = **124 例**，逐例台账 + 每变体 `proc_rc=0` + 自报 `fail=0`；输出目录先过 `require_outside_repo`，编译失败不跑旧产物 |
| 覆盖范围 | 任务书最少判据 **IO1-OFF / IO1-ON / IO1-CFG / IO2 / IO3 / IO4 / IO5 / IO6 / IO7 / IO8 / IO9 / IO10** 全部有对应用例；非法宏值由 runner 单独验证"编译被拒绝"（`rc=1`） |
| 三项反向验证（仓外源码副本 + `inject.py`） | `R1` 宏关闭仍强制读限位 → 命中 `IO1OFF-limits_not_resolved`+`IO1OFF-limits_never_touched`；`R2` 限位极性/边沿写反 → 命中 `IO1ON-falling_edge_for_limits`+`IO1ON-both_high_is_safe`；`R3` 错误反馈强行当匹配 → 命中 `IO3b`+`IO5a`+`IO6b2`。均为 META-OK（编译意外失败/崩溃不算检出） |
| 既有 V1/V2 | `CHECK_PASS=25` / `CHECK_PASS=22`，`CHECK_FAIL`/`BAD_SELFTEST`/`MISSING_CASE` 均 0；旧夹具只新增"反馈正常"的隔离桩（`safety_fb_*`、`safety_drv_feedback_confirm`、`safety_limit_inputs_enabled=RT_FALSE`），未改任何判据 |
| 既有 13 例负例矩阵 | 全 `OK`，失败身份、真实/自报/期望三个 rc、`ONLY` 选择语义与未执行判定均未放宽 |
| 既有 8 组元负例/对照 | M1/M2/M3/M4/M5 = META-OK，M1c/M2c/M3c = CONTROL-OK，集合一致 |
| 整链 | `run_negatives.sh`（含元负例）真实 rc=0；跑完后 `git status` clean |
| Keil | `UV4 -r project.uvprojx -j0`：**0 Error(s), 0 Warning(s)**，131 个编译单元，`Code=147522 RO-data=64038 RW-data=2176 ZI-data=83404`，UV4 退出码 0。构建钩子只读核查：BeforeCompile/BeforeMake 关闭且命令为空，AfterMake 仅 `fromelf --bin` → 无烧录/复位/串口动作 |
| 范围核对 | 生产改动仅限任务书允许文件（`project_board.h`、`safety_gpio.*`、`safety_thread.*`、`safety_state.*`、`motor.*`、`supervisor.c`、`tmc2209.*`）；`project.uvprojx` 相对基线 **0 差异**；三道宏仍 `RT_FALSE`；`git diff --check` 无错误 |

## 4. 桩与真模块的分界（不冒称覆盖）

新链**真实编译并调用**：`safety_gpio.c`、`safety_thread.c`、`safety_state.c`、`motor.c`
（含保护就绪、故障源实测、写回读+反馈确认、arm/start/守卫/disarm 接入点）。
新链**仿真/桩**：RT 原语（mutex/event/thread 由 shim_io 真实实现但非目标调度）、GPIO/PWM/设备仿真、
`tmc2209_*`/`adxl345_*`/`current_adc_*`/`ns_flash_*`/`blackbox_*`/`step_pwm_*` 假服务。
新链**未编译**：`supervisor.c`、`tmc2209.c`、`diagnosis.c`、`sensor_service.c`、`project_config.c`、
`ns_storage.c`、`ns_flash.c`、`blackbox.c`、`step_pwm.c`（其中 `tmc2209.c` 的新写门位由旧链真编译覆盖）。
旧链（`host_main.c`）仍是"把 safety_* 与 step_pwm 整体替换成桩"的隔离测试，其 25/22 判据
**不能**冒充真实安全模块覆盖 —— 真实覆盖只在新链里。
主机通过 = 逻辑与接入行为通过；不证明物理时序、引脚连接、硬件安全链（那些仍 NOT TESTED）。

## 5. 本轮未执行（NOT EXECUTED / NOT TESTED）

- 未烧录、未复位、未连接或操作 COM5、未向真实 TMC2209 读写、未通电/断电测量、未接电机、未输出 STEP、
  未改接线/飞线/换器件、未翻转三道硬件宏、未改安全门禁或协议/事务锁逻辑。
- 未在 Keil 目标里构建 `SAFETY_LIMIT_INPUTS_ENABLED=RT_TRUE` 分支（目标只构建默认关闭配置）；
  LIMIT-ON 只在主机侧编译执行 → **不等于限位上板验收**。
- 未解决 RISK-03 / RISK-03a：`tmc_xfer_lock`/`mot_lock`/`ss_lock`/`cfg_lock` 仍是
  `RT_WAITING_FOREVER`；未重构线程，未声称任何"多少 ms 内必停"。
- 未重跑 R4 路径守卫沙盒（该路径未改动），未扩建哈希工具；R4 的 14 文件清单不覆盖本轮新增的
  `io_main.c`/`run_io_behavior.sh`/`shim_io/`（本轮未重出清单，任务书不要求）。
- 未 merge、未 force push、未改写已推送提交、未动 `main`/冻结分支/标签、未生成 ZIP。

## 6. 偏差与遗留依赖（如实上报，未顺手重构）

1. **`sensor_service.c`（允许清单之外）未改**：它仍用别名 `PIN_NAME_ESTOP` 读同一颗脚，写进传感器帧
   `estop` 字段与黑匣子 `record.flags` bit8。为使工程可编译且不改黑匣子格式，`project_board.h`
   保留了**文档化别名** `#define PIN_NAME_ESTOP PIN_NAME_ENN_FEEDBACK`。后果（已登记，非本轮修复范围）：
   ① 该位/字段承载的其实是 ENN 反馈电平；② 限位宏关闭时两路限位不被解析，
   `sensor_frame.valid_safety`（要求四路可解析）会变为 0，黑匣子数字输入组记 N/A。
   → 需要单独一轮把帧字段命名与 `valid_safety` 判据改为"按活动输入集合"。
2. **`LIMIT_MAX` 行的反向注入锚点**要求 `safety_limit_inputs_enabled()` 用表达式实现（避免与引脚表
   `#if` 锚点重复），属本轮内部实现细节，不改变语义。
3. 主机 GPIO 仿真的"引脚被拿掉"建模为 `rt_pin_read` 返回非 0/1（无效样本），因为固件在启动时缓存了
   引脚号；`NO_SAMPLE` 分支因此由"样本无效"路径覆盖，而非"重新解析失败"路径。
4. **交接 JSON 的文件清单登记有误，推送后单独修正**：首版把 `doc_files_changed` 写成空数组、
   `changed_file_count_vs_baseline` 写成 17（漏了 `tests/host/inject.py` 与 4 份文档）。
   对账后的正确口径：**生产 12 + 主机与工具 6 = 18 个代码/测试文件，文档 4 份，证据与交接 7 份，
   合计 29**，与 `git diff-tree --name-only 9ace7d0..HEAD` 的 29 行逐一对上（无缺项、无多项）。
   修正方式：另起一次新提交，不改写已推送的 `dca6932`。

## 7. 文档同步（同一次交付内，未另起文档返修）

BSP `README.md`（引脚表两行 + 扩展板条目 + 硬件阻塞项条目）、`开发进度.md`（阶段总表新增一行 +
"2026-10-21"整段）、`待办事项.md`（覆盖说明第 1/3 条更新、🔴 首条材料收敛为 3 项、"已完成"新增一条、
两条被取代的旧条目就地标注）；验收方案草案升级到 **v0.3**（A19/A23/A25/A26、X-1～X-4 收口、
B1/B2/B3、L0/L1/L2 与 D6/D7、C9、D 表关闭两项、E 节维修后验收准备、G 节本轮做了/没做）。
历史读数与旧结论一律保留：维修前异常读数标注为"维修前历史"，不删不改；被撤回的说法留痕可查。

## 8. 停止点

（本节记录**首次交付**的停止点；独立复审已给出 `SOFTWARE_REVIEW_REWORK` 与两项返修，
其修复与复跑见第 9 节。第 1～7 节的口径保留原文不改写，其中"证据文件清单"不含第 9 节新增的三份。）

普通提交 + 普通 push 到 `phase8/tmc-drive-config`，`git ls-remote` 回读完整 SHA 与本地 HEAD 一致后 **STOP**：

- 等待 ChatGPT 对**实际源码**的独立复审；不拿 F2d 的旧 `SOFTWARE_REVIEW_GO` 代替本轮审核；
- **不执行硬件**：烧录、复位、串口、通断电测量、真实寄存器写入、接线变更、运动试验都需用户分别授权；
- 不自动进入运动测试，不重开 F2d 工具返修；
- 用户本轮已确认的三项（现板=该图纸、`PC.6`=H6-5 反馈、坏门已更换）**不再作为待回答问题**；
  仍缺的只有：连线归属证据（网表/截图）、外部触点形式、仪器条件。

## 9. 独立复审返修（同日追加，返修基线 = 审查对象 `cd337b4d013f7ef7f579ea57a42fdcc820befafb`）

复审件：`NS800_IO70_Review_cd337b4_20261021.md`，结论 **SOFTWARE_REVIEW_REWORK**（两项，修复后再审）。
本报告不重开 F2d/R1–R4 工具循环，也不把第 1～7 节的历史口径改写。返修范围按复审 §7 限定执行。

### 9.1 IO70-NEW-01（P1）：故障清除忽略"最后一次关闭动作已观测到的坏反馈"

缺陷（`safety_state.c:fault_reset`）：顺序是 预检查 `safety_drv_feedback_confirm(0)` →
`motor_disarm()`（内部再写许可 LOW 并读一次 ENN）→ 清故障码 → 进 `MANUAL_CLEAR`。
`disarm` 读到的 `UNEXPECTED_ENABLE` / `NO_SAMPLE` 只打印警告，返回值仍是焊盘写入结果，
于是外层打印 `ENN feedback on disabled side confirmed` 并把故障码归零。

修复：在 **disarm 之后**（清除路径上最后一次可能改变许可的动作）再做一次同源的有界禁能侧确认；
不匹配 / 样本不可得 / 窗口内仍在过渡 ⇒ 保持原锁存与非零故障码，且不打印"最终禁能已确认"。
约束都守住了：停 STEP 与撤销许可由 `motor_disarm()` **先行照做、不回退**；确认函数只做
`rt_pin_read` + 有界采样，不在底层 GPIO 路径递归调用停机或状态机；锁序仍是 `ss_lock` →
（`disarm` 内部短暂取放 `mot_lock`），与 `safety_force_shutdown()` 一致；有界窗口仍是既有
`DRV_FB_TRIES × DRV_FB_SETTLE_MS`，**不是**时延上界声明；`motor_disarm()` 公开返回值语义未改
（选择"清除后复核"方案而非"让 disarm 传播失败"，因此 `motor.h` 无需改注释）。

### 9.2 IO70-NEW-02（P2）：已建立使能请求后保护就绪跳过反馈检查

缺陷（`safety_thread.c:safety_protection_ready`）：`if (req != PIN_HIGH && safety_fb_now(0u) != OK) return FALSE;`
—— 请求为 HIGH 时**整个跳过**反馈条件，故 `CUT_BY_HARDWARE` / `NO_SAMPLE` 也返回就绪。

修复：按当前已确认请求侧取期望 —— `want = (req == PIN_HIGH) ? 1 : 0`，再要求
`safety_fb_now(want) == SAFETY_FB_OK`。这样 arm 之后正常的 ENN LOW 仍就绪（不制造 arm→start 死锁），
而 `CUT_BY_HARDWARE` / `PAD_MISMATCH` / `NO_SAMPLE` 一律不放行。活动输入集合、IRQ 门、人工极性确认
等原有条件与 `motor.c` 在 arm/start/运行守卫上的同源判定都保留不变；`safety_thread.h` 接口注释同步。

### 9.3 新增用例与"修前 FAIL"实证

台账新增 7 个必需用例（登记在 `run_io_behavior.sh` 的 `OVERRIDE_CASES`，LIMIT-OFF/ON 两个 OVERRIDE
变体各自真执行）：`IO4c/IO4d/IO4e/IO4f`（就绪 API 按请求侧判定的直接断言）与
`IO7d/IO7e/IO7f`（预检查过了但 disarm 把反馈读坏 ⇒ 保持锁存；`IO7f` 为不布防对照）。
仿真核新增一次性注入 `io_sim_flip_chain_on_disable_write()` + `io_sim_flip_armed()`
（下一次向许可脚写 LOW 时切坏外部链），仅存在于 `tests/host/`，不进生产、不进 Keil。

- 修前（生产源码 = `cd337b4`，用例已加，`ONLY=off_ovr`）：`IO4d`、`IO4e`、`IO7d`、`IO7e` 四例 **FAIL**，
  `SUMMARY|cases=40|pass=36|fail=4`，proc_rc=1，runner `FAIL (rc=1)` —— 用例确实抓到这两项缺陷。
- 修后（同一 runner）：`off_target 27` / `on_target 29` / `off_ovr 40` / `on_ovr 42` = **138 例全部 PASS**，
  自报 `fail=0`、逐变体 proc_rc=0、非法宏值仍被编译拒绝，runner rc=0。
- 三项反向验证复跑仍 META-OK（命中 2/2、2/2、3/3 个指定业务失败身份）。
- 既有回归在返修提交上重跑：V1 `CHECK_PASS=25`、V2 `CHECK_PASS=22`（其余三项 0）、
  13 例负例 OK=13（真实/自报/期望 rc 一致）、8 组元负例与对照全部按正确原因被拒 / 对照通过，
  链真实 rc=0。旧链断言未放宽，`tests/host/host_main.c` 本轮未改动。
- Keil `UV4 -r`（Rebuild All）：**0 Error / 0 Warning**，UV4 退出码 0，131 编译单元，
  `Code=147610 RO-data=64214 RW-data=2176 ZI-data=83404`（返修前 147522/64038/2176/83404）。
  构建钩子再次只读复核：BeforeCompile/BeforeMake 关闭且命令为空，AfterMake 仅 `fromelf --bin`；
  工程内主机注入宏/夹具命中数 0；三道硬件宏仍 `RT_FALSE`，`SAFETY_LIMIT_INPUTS_ENABLED` 仍默认 `RT_FALSE`。

返修改动文件（6 个）：`applications/safety_state.c`、`applications/safety_thread.c/.h`、
`tests/host/io_main.c`、`tests/host/run_io_behavior.sh`、`tests/host/shim_io/rtdevice.h`。
新增证据（3 份，脱敏文本）：`io_rework_ledger.txt`、`regression_rerun_after_rework.txt`、
`keil_rebuild_all_after_rework.txt`。未触碰 UART/配置算法、Keil 工程、线程结构、黑匣子格式、
`sensor_service.c`；未重跑未改动的 R4 路径守卫；未新建 runner/元负例/哈希工具。

返修后仍**未执行任何硬件动作**（不烧录、不复位、不接触 COM5、不向真实 TMC 读写、不通断电测量、
不改线、不接电机、不输出 STEP、不翻硬件门禁宏）。新逻辑仍未在板上运行（板上仍是冻结固件 `e40a9b9`）。
第 6 节登记的 `sensor_service`/黑匣子字段语义与 RISK-03/03a/04 继续开放。

## 10. 返修复审结论与验收清单落成（同日收尾，纯文档，未执行任何步骤）

**复审件**：`NS800_IO70_Rework_Review_296c369_20261021.md` → **`SOFTWARE_REVIEW_GO`**，
接受对象 `296c369607ea69c201f797b80950b76ff312c0e0`（tree `61999121d2c913a7dfcbda96dde269076b9b6127`）。
IO70-NEW-01（P1）与 IO70-NEW-02（P2）**均关闭，本轮无新增返修项**；此前 F2d/R1–R4 的工具收口结论继续有效。
复审方明确该结论**限定于已审源码、主机逻辑与验证接入，不构成硬件安全链验收或任何硬件动作授权**。

复审方的独立核验（不是我复述自己的结果）：
- 独立 fetch 固定 SHA、读 Git 对象与 base/head 差异，重跑 `git ls-remote`；工作树 clean、`diff --check` 通过；
  冻结对象（`main`/`phase7` 分支/两个标签）回读一致。
- 复用上一轮的仓外复现夹具**直接调用本次生产代码**：
  `flip → FAULT_LATCHED / fault=9 / UNEXPECTED_ENABLE`、`lost → FAULT_LATCHED / NO_SAMPLE`、
  `control → MANUAL_CLEAR / fault=0 / OK`；`arm 后切断 → protection_ready=0 / gate_mask=0x28`（LIMIT-OFF/ON 一致）。
- 把新增夹具与 runner 配回 **`cd337b4` 生产原文**跑 `off_ovr`，复现同样四个业务 FAIL
  （`cases=40 pass=36 fail=4`、`BASELINE_RUNNER_RC=1`）→ 确认新用例确实能检出修前缺陷。
- 独立环境 Linux / GCC 13.3.0（仓外 pthread 兼容头，未改仓内夹具）复跑：138 例全 PASS、非法宏值仍拒绝、
  3 项反向命中 2/2、2/2、3/3、旧链 V1 25 / V2 22 / 13 例 / 8 组终态完整。**复审方注明**：元负例首轮组合
  运行终态不完整，随后分两组补齐实际终态，因此不声称"全部来自一次未中断的连贯运行"；
  亦未在云端重跑 Keil、未取得完整原始构建日志（尺寸数字只作链接结果记录）。
- 独立确认相对 `cd337b4` 未改：`motor.*`、`tmc2209.*`、`supervisor.c`、`sensor_service.c`、`project.uvprojx`。

**本轮据此做的收尾（只有文档）**：把 GO 登记进 BSP `README.md`（新增第 ⑤ 条覆盖说明）、
`开发进度.md`（阶段表状态行 + 新的 GO 小节 + 头部修改时间）、`待办事项.md`（🔴 首条改写为
"复审已收口，下一步是 H 节清单的参数与授权"），以及安全链草案升到 **v0.4**：
新增 **H 节"维修后无电机/无 STEP 的可执行验收清单（准备稿）"** —— 按供电状态 S0 断电 → S1 上电拔模块 →
S2 上电模块在位无电机 → S3 时延分组，每步给测点（位号+引脚）/仪器/操作/判据（引用 B2 行号）/
固件适用性（冻结 `e40a9b9` vs 新源码 `296c369`）/失败停止条件与 `NOT EXECUTED` 状态；
另加 H.1 待填参数 P-1…P-7（对应 D-2/D-4/D-5 + 烧录授权 + 断电动作 + 料号 + 记录载体）、
H.3 记录模板、H.4 步骤↔C 节用例映射、H.5 本轮新增的软件侧可观测依据；
C 节顶部加双向指针，E 节把授权类别对到 S0–S3，G 节加 v0.4"做了/没做"。

**没有改变的状态**：`BLOCKED_METHOD`（H-11 = 旧的 D2，合规的许可激励方法仍未确定，明令不得用裸 GPIO、
临时命令、跳线或翻宏代替）与 `BLOCKED_INSTRUMENT`（H-10 复位瞬态、H-13 三段时延；当前无示波器）
**都没有被写成已解决**；RISK-03/03a/04 继续开放；板上仍是冻结固件 `e40a9b9`，新逻辑未烧录；
三道硬件宏与 `SAFETY_LIMIT_INPUTS_ENABLED` 全部仍 `RT_FALSE`。
本轮同样**零硬件动作**：没上电、没断电测量、没烧录、没复位、没接触 COM5、没读写真实 TMC、
没改接线、没接电机、没输出 STEP、没翻宏。草案仍是 `DRAFT`，判据需复审方 + 用户确认后才谈执行授权。

## 11. 清单审查（`NS800_SafetyChecklist_v04_Review_9cf8279_20261021.md`）合并记录

审查对象 `9cf8279`，结论**分开记录**：对 `296c369` 的 **GO 登记接受**（接受对象固定为 `296c369`，
未改成文档提交 SHA），但 **H 节清单执行保持 `HOLD`**，给出三项操作性更正 + 两组勘误，
并明确**不需要再开代码、主机工具或 Keil 返修循环**、本轮无新增软件缺陷结论、未授权任何硬件动作。

**合并前我逐项读了源码核实（没有照抄审查结论）**：

| 审查点 | 我核到的事实 | 出处 |
|---|---|---|
| V04-01 `pin_status` 有写副作用 | 对 `DRV_ENABLE` 调 `safety_drv_enable_write(0)`（含 settle + 回读重试），对其余 `PIN_MODE_OUTPUT` 脚 `rt_pin_write(p->pin, p->level)` → 确属主动动作 | `applications/safety_gpio.c:317` 起 |
| V04-01 `tmc_regs` 不止读 IOIN | 读集合 = GCONF、GSTAT、IFCNT、IOIN、VACTUAL(`0x22`)、CHOPCONF(`0x6C`)、SG_RESULT 共 **7 个 READ**，函数内无 WRITE 调用；会影响链路健康度/失联计数 | `applications/tmc2209.c` 的 `tmc_regs` 数组 |
| V04-01 `motor_status`/`safety_status` 可用 | 状态路径只做状态读与 `rt_pin_read`：`motor_get_gate_fail_mask` → `safety_protection_ready`/`tmc2209_drive_config_ok_for_arm`（`tmc_cfg_ok_for_arm_ctx` 只读 ctx 状态）/`safety_fb_now`；motor.c 的写只在 `mot_drv_en_request`(`:116`) 与 `motor_set_direction`(`:687`) | `applications/motor.c`、`tmc2209.c:1249-1263` |
| V04-02 缺断电转换 | 原文 P-5 只写在 S0 前置，S1→S2 之间无"结束供电→余压确认→插回→重新上电"的顺序 | 草案 v0.4 H.2 |
| V04-03 冻结固件无新版 STEP 门禁 | `e40a9b9` 的 `motor_pwm_grant_to_diag` 只检查快照/`armed`/`state`/`current_hz`/`target_hz`/许可 LOW，**没有**双宏入口检查；新版在 `motor.c:877` 才要求 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED || STEP_DIAG_ISOLATED_ENV_CONFIRMED` 之外还加禁能侧反馈 | `git show e40a9b9:…/motor.c:517-560` vs `motor.c:877` |
| §5.1 分压估算 | `3.3 × 1/(10+1) ≈ 0.30 V` 与图纸 R5=10 k/R6=1 k 一致；工程估算，非实测非容差 | 草案 B2/H-06 |
| §5.2 H.3 时点写反 | 原文确实写"每步执行**前**改为 EXECUTED-PASS/FAIL" → 更正为完成并留证后才标，可用 `IN_PROGRESS`，不得预写 PASS | 草案 v0.4 H.3 |

已合并到草案（升到 **v0.4.1**，新增 **I 节**更正记录）：H-09 调用集合收窄（移除 `pin_status`、完整登记
7 个 READ、不为取 2g 跑 `runtime_selftest`、区分 READ 请求帧与 WRITE 帧）、H.0 与 S1/S2 之间的供电状态
转换规则（`BLOCKED_POWER_STATE`）、H-08 判据与 `DIR` 措辞、S2 固件适用性规则（每轮记录实际固件版本）、
"同一时刻"全部改为"同一稳定工况下分次采样 + 记录次序与时间 + 首尾复核参考点"（H.0 新增第 6 条）、
H.1 改为"每项只阻塞它实际影响的步骤"、H-03 在板等效电阻与蜂鸣器判据告诫、D1/D6/D7 与 C1 同步、
F 节 dated 覆盖（云端已实际渲染并图形追线）、B1 ③ 禁令范围限定。BSP `开发进度.md` / `待办事项.md`
同步 v0.4.1 与 HOLD 口径；本节与 JSON `checklist_review_merge` 记录合并内容。

**边界**：本轮差异**只有文档**（未改源码/测试/Keil 工程/宏/门禁，未新增诊断命令、GPIO 裸写、跳线、
受控写握手或验证平台；未重跑主机回归与 Keil —— 因为审查明确不需要，且差异必须仍只有文档；
未开新审查/元负例体系、未重跑 R4 守卫、未生成 ZIP、未改写已推送提交）。仍**零硬件动作**、
清单一步都没执行、板上仍是冻结固件 `e40a9b9`；RISK-03/03a/04 继续开放；
`BLOCKED_METHOD`（H-11）与 `BLOCKED_INSTRUMENT`（H-10/H-13）**没有被写成已解决**。
