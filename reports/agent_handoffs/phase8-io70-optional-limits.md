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

普通提交 + 普通 push 到 `phase8/tmc-drive-config`，`git ls-remote` 回读完整 SHA 与本地 HEAD 一致后 **STOP**：

- 等待 ChatGPT 对**实际源码**的独立复审；不拿 F2d 的旧 `SOFTWARE_REVIEW_GO` 代替本轮审核；
- **不执行硬件**：烧录、复位、串口、通断电测量、真实寄存器写入、接线变更、运动试验都需用户分别授权；
- 不自动进入运动测试，不重开 F2d 工具返修；
- 用户本轮已确认的三项（现板=该图纸、`PC.6`=H6-5 反馈、坏门已更换）**不再作为待回答问题**；
  仍缺的只有：连线归属证据（网表/截图）、外部触点形式、仪器条件。
