# phase8-stop-priority-s2-integrated — CP1+CP2 交接 (2026-10-23)

任务书: `NS800_S2_Integrated_Implementation_Qoder_Task_1348eb0_20261023.md`
性质: 软件实现 + 必要主机验证 + Keil 构建 + 普通提交/push; **硬件动作不授权**。

## 1. 身份与 SHA

| 项 | 值 |
|---|---|
| 分支 | `phase8/tmc-drive-config` |
| 基线 analyzed_commit | `1348eb098ce51e40303b2685bce19d67da67e276` |
| CP1 提交 | `6726fa60721c1da2ec578a15a04d2ca899e470d3` |
| head / tested_commit | `776a91e669dcac84e53d49e4838611a2a4454705` |
| Keil 构建对应树 | CP2 = `776a91e`(Rebuild All, 非增量) |
| 远端 | 本轮**尚未 push**(下一步随最终交接一起推) |

整体状态: **IN_PROGRESS**。CP1/CP2 的代码与 Keil 证据是真的; §11 的集中验证
(C01–C20 + 两类反向 + S1/V1V2/I/O 回归)**一条都没执行** —— 夹具还没接入 S2。
不称完整, 不称 GO。

## 2. 真实修改列表(逐文件)

新增
- `applications/stop_priority.h` / `.c` — 协调层本体: `epoch/owner/phase/inhibit/exhausted`
  + 两个 `*_latched` + `diag_ever_reserved` + `metadata_pending` + `first_fault_code`
  + `reason_events` + `pwm{attempted,result,raw_rc,src,epoch}` +
  `permit{written,write_rc,src,write_epoch,confirmed,confirm_rc,confirm_epoch}` +
  `first_unrecovered`(首个未恢复失败) + 计数器 `trips/merged/stale_dropped`。
  头文件里的"五个共享字"说法已按任务书 §4 更正为完整字段清单。
- 导出: `sp_bind_pwm/sp_bind_permit_pin/sp_get_bound`、`sp_trip_fast`、
  `sp_trip_for_operation`、`sp_off_attempt(…,release_on_ok,…)`、`sp_reserve/sp_phase_set/sp_release`、
  `sp_enter_token/sp_leave_token`、`sp_commit_permit_high`、`sp_pwm_start_held/sp_commit_pwm_start`、
  `sp_note_permit_confirmed/sp_note_low_confirmed`、`sp_token_current`、`sp_mirror_motor`、
  视图 `sp_get/sp_inhibited/sp_inhibited_held/sp_epoch/sp_epoch_exhausted/sp_owner_is/sp_get_phase/
  sp_motor_stop_latched/sp_diag_stop_latched/sp_diag_ever_reserved/sp_view_motor/sp_view_diag/
  sp_diag_off_confirmed/sp_diag_output_active/sp_take_pending`、
  恢复 `sp_recovery_start/sp_recovery_probe_off/sp_recovery_valid/sp_recovery_valid_held/
  sp_finish_recovery_held/sp_clear_latches_held`、窗口 `sp_win_begin/sp_win_end`。

修改(生产/驱动)
- `applications/motor.c` `.h` — 见 §4/§6 的真实写清单; `motor_reconcile_after_trip`、
  `motor_lock_for_recovery/motor_recovery_fields_held/motor_unlock_for_recovery`、
  `motor_pwm_grant_to_diag(sp_token_t*)` + `motor_diag_gate_check()`;
  **删除** `motor_clear_stop_latch()`。
- `applications/step_pwm.c` `.h` — 关断结果/所有权/锁存改读 canonical;
  `pwm_test` 走"先校验 hz → 句柄一致性核对 → 预约 → 门外 prepare → 门内 commit+enable";
  新增 `step_pwm_reconcile_after_trip()`、`step_pwm_recovery_fields_held()`;
  **删除** `step_pwm_force_stop()`、`step_pwm_clear_stop_latch()`。
- `applications/safety_state.c` `.h` — `safety_force_shutdown()` 改为"先短保护再慢整理";
  `safety_post_event()` 对故障类事件先保护; 新增 `SAFETY_EVT_FAULT_MASK`、
  `safety_notify_trip_done()`、`safety_reconcile_trip()`、`safety_trip_pending()`;
  `safety_state_get()` 抑制优先且不先等 `ss_lock`; `safety_transition()` 在抑制期间拒绝
  非故障转换(白名单未改); `safety_startup_selftest()` 进入时捕获 epoch、进 READY 前比较;
  `fault_reset()` 改为"票据 + 专用复核 + 一次最终提交"。
- `applications/safety_thread.c` — 事件消费者只整理不再关断; 动作前的打印移到动作后;
  接收超时拾取 `metadata_pending`。
- `applications/safety_gpio.c` `.h` — `safety_drv_permit_low_raw_held()` /
  `safety_drv_permit_high_raw_held()`(窗口内单次 SET/CLR, 无延时/无回读/无打印)、
  `safety_drv_permit_confirm_read(level)`(只回读不再写)、`safety_drv_permit_confirm_low()`
  改为纯确认; `safety_drv_enable_write(1)` 在生产路径返回 `-RT_EPERM`(堵掉无身份 HIGH 旁路)。
- `applications/supervisor.c` — runtime_selftest 的 STEP 诊断体检改用只读
  `motor_diag_gate_check()`(不再产生预约)。
- `libraries/HAL_Drivers/drivers/drv_epwm.c` `.h` — 拆出 `ns800_epwm_prepared` +
  `ns800_epwm_prepare()` + `ns800_epwm_commit_prepared_held()`; 配置路径**不再把 TBCTL
  旧计数模式写回**(停机冻结后旧 `rt_pwm_set` 会复活运行模式)。
- `libraries/HAL_Drivers/drivers/drv_uart.c` `.h` — 新增
  `ns800_uart_write_budgeted(dev,data,size,start,total,&written)`。
- `applications/tmc2209.c` — 预算表/`tmc_budget_t`/`tmc_xfer_stat_t`、flush 双上限、
  RX 上限 = min(100ms, 总剩余)、cfg/xfer 取锁带上限且只释放本层取得的、
  无等待有效性摘要、`tmc_cfg_fail_ctx` 先保护后整理、假总线接缝同步加预算参数。
- `ns800rt7p65-nssinepad/project.uvprojx` — 把 `stop_priority.c` 加入 rt-thread target。

## 3. 状态 / 所有权 / 锁序

- canonical 只有一份(协调层内部 `sp`), 所有读写都在同一个短临界区内
  (`rt_hw_interrupt_disable/enable`, Cortex-M7 上即 PRIMASK)。
- owner 迁移: `NONE →(sp_reserve) RESERVED →(HIGH) ARMED →(enable) RUNNING`;
  短保护成功 → `NONE`; 关断失败 → `HELD_UNKNOWN`(谁都不许认领, 也不许声称已交回)。
  `sp_off_attempt` 成功且 `release_on_ok` 时**只在 owner 本来就是自己/无人/未知**时撤销,
  不越权撤销别的 owner 的预约(CP1 自查补上的)。
- 锁序保持: `ss_lock → mot_lock`(恢复路径 `ss_lock → mot_lock → 短窗口`),
  `txn → mot_lock`(配置门禁), 短窗口内**不取任何锁**。快保护从不等 `ss_lock`/`mot_lock`,
  所以"人工恢复正持锁做慢复核"不再挡住第一次关断。
- 换 epoch 的事件: 每次故障/新失败请求、NONE→新预约、正式释放、成功人工恢复;
  每拍 ramp 与同一有效预约内的 HIGH 重试**不**换 epoch。epoch 为 24 位, 到顶饱和
  (`exhausted`)后禁止新的预约与人工恢复, 但每次显式保护仍照做关闭。

## 4. 真实写清单(本轮新落到寄存器/GPIO 的动作点)

| 写点 | 位置 | 是否在短窗口内 | 与资格判定的关系 |
|---|---|---|---|
| `rt_pwm_disable` | `sp_protect_held` / `sp_off_attempt` / `sp_recovery_probe_off` | 是 | 与抑制/换 epoch/许可 LOW 同窗 |
| `rt_pwm_enable` | `sp_pwm_start_held`(经 `sp_commit_pwm_start` 或门内调用) | 是 | 与 token 校验同窗; Motor 需 phase≥ARMED |
| EPWM 寄存器提交 | `ns800_epwm_commit_prepared_held` | 是(调用方已 `sp_enter_token`) | 与 `sp_pwm_start_held` 同一窗口, 不写回计数模式 |
| GPIO 许可 LOW 裸写 | `safety_drv_permit_low_raw_held` | 是 | 与关断同窗, 独立尝试 |
| GPIO 许可 HIGH 裸写 | `safety_drv_permit_high_raw_held`(`sp_commit_permit_high`) | 是 | 判定与写在同一 token 门内 |
| DIR 写 | `motor_set_direction` | 是(`sp_win_begin/end` 内重核抑制位) | 静止+关断可信两道判据在窗口外, 抑制位在窗口内重核 |
| UART 单帧 TX | `ns800_uart_write_budgeted` | **否**(线程态, 含 `rt_thread_mdelay(1)`) | 预算锚在操作 start 上 |

窗口内**没有**: 取锁、`rt_thread_mdelay`、UART、`rt_kprintf`、动态分配、循环重试、浮点。
根因事实(本地亲读): `drv_uart.c:403-427` 的 TX 等待按 `rt_tick_get()` 判超时, 而 PRIMASK
屏蔽 SysTick ⇒ 窗口内 tick 不推进, 按 tick 超时的等待永不退出。

## 5. 预算实际调用链(初始软件参数)

```
tmc2209_read_register()            -> tmc_read_reg(NULL budget => 本层建 125ms)
tmc2209_write_register_confirmed() -> 400ms  (IFCNT 读 + 写帧 + IFCNT 读 共用)
tmc_cfg_apply_ctx()                -> 1500ms (所有帧共用, 子调用只取剩余)
tmc_cfg_integrity_ctx()            -> 250ms  (GCONF + CHOPCONF 两个寄存器共用)
tmc_xfer_take()      <= min(20ms, 总剩余)    tmc_cfg_lock_take() <= min(10ms, 总剩余)
tmc_tx_frame()       <= min(20ms 整帧, 总剩余), 传原 start + effective_total
tmc_rx_flush()       <= 2ms 且 <=256B, 任一先到即 RX_FLOOD(不再 TX, 不称已清空)
RX 等待              = min(100ms 阶段上限, 总剩余), 空轮 2ms 不得越过所剩预算
```
`ms->tick` 舍成 0 或 ≥0x80000000 一律拒绝(`-RT_EINVAL`), 预算耗尽返回 `-RT_ETIMEOUT`
且不映射成成功。这些是**保守软件预算, 不是实测物理性能或最坏停机指标**。

SDK 叶子已本地核实无等待(§10.3 要求): `uart.h:696 UART_isSpaceAvailable` = 单次读
`STAT.TDRE`; `uart.h:757 UART_writeChar` = 单次写 `DATA`。因此带预算循环是唯一等待点;
`UART_writeCharArray` 内含两处无界 `while`, 本路径不调用它。

## 6. 原始结果与首错保存方式

- PWM 与许可是**两个独立结果**: 对外返回"PWM 关断失败优先, 否则许可的原始错误",
  两个原始 rc 各自留档(`sp.pwm.raw_rc` / `sp.permit.write_rc`), 不用合并码覆盖原始值。
- `sp.pwm` = 最近一次**实际**关断尝试(`attempted/result/raw_rc/src/epoch`);
  从未占用过通道的诊断路径保持 `NOT_ATTEMPTED`, 不伪造一次 API 调用。
- `sp.first_unrecovered` = **首个未恢复**的失败原始结果(不是最后一笔成功), 一次后续
  成功只更新"当前结果"、不抹首错; 只有 `fault_reset` 的最终提交能清。
- 失败锁存 `motor_stop_latched`/`diag_stop_latched` 按"失败时的前 owner"归属, 诊断侧
  失败不污染 Motor 视图, 反之亦然。
- 裸写与确认分开: `permit.written=TRUE/EOK` 只说明写请求已发给寄存器, 回读确认由
  门外 `safety_drv_permit_confirm_*` 给出并绑定 `confirm_epoch`。

## 7. C01–C20 适用性与执行结果

**本轮执行数 = 0。** 下表只声明适用性与当前状态(全部 NOT EXECUTED), 不冒充任何结果。

| ID | 适用性 | 状态 | 阻塞点 |
|---|---|---|---|
| C01,C02 | 适用 | NOT EXECUTED | 夹具需要真实持有 `ss_lock`/`mot_lock` 的第二线程(§11 禁止再用计数空操作) |
| C03 | 适用 | NOT EXECUTED | 需要 ISR 模型 + `!ss_inited` 接缝 |
| C04 | 适用 | NOT EXECUTED | 需要在"发布/认领"旧接缝处安排抢占; S2 已取消三段式, 预期为"无空洞" |
| C05,C06 | 适用 | NOT EXECUTED | 需要可编程 `rt_pwm_disable`/许可桩(仓外冒烟已覆盖同类断言, 但不是正式链) |
| C07,C08,C09 | 适用(host_ovr 前置) | NOT EXECUTED | 目标四宏 FALSE 下 arm/HIGH 不可达, 必须走 host_ovr 并在台账里写明 |
| C10 | 适用 | NOT EXECUTED | 需要 `drv_epwm` 主机接缝(计算/提交叶子), 且不得把夹具里的副本称生产 HAL |
| C11 | 适用 | NOT EXECUTED | 诊断 grant/set/enable 各接缝 |
| C12,C13,C14 | 适用 | NOT EXECUTED | 需要两个 reset 并发的真实夹具; 仓外冒烟已单线程验证 C12/C13 语义 |
| C15 | 适用 | NOT EXECUTED | 旧 ramp/旧自检/旧 cfg 晚到 |
| C16,C17,C18 | 适用 | NOT EXECUTED | 需要预算/洪泛/TX 无空间的桩(`tmc2209.c` 上链代价最大的一环) |
| C19 | 适用 | NOT EXECUTED | 16.7M 次推进到 24 位饱和, 需时限与进度断言 |
| C20 | 适用(host_ovr) | NOT EXECUTED | 正常能力回归: arm/start/ramp/stop/start/disarm/rearm |

已完成但不是 §11 正式链的证据: 仓外 `stop_priority` 冒烟 **67 项断言全 PASS, rc=0**
(见 `build_and_smoke.txt` [E3])。它只覆盖单线程可判定的合同(两动作/记账/token 失效/
恰好一次 disable/跨 owner 不越权/恢复票据与新故障赢/未绑设备不碰 NULL),
**不能**替代 C01–C20。

## 8. 既有链与构建结果

| 项 | 结果 |
|---|---|
| Keil Rebuild All (CP1 树) | **0 Error / 0 Warning**, Code=160186 RO=71958 RW=2188 ZI=83576 |
| Keil Rebuild All (CP2 树 = head) | **0 Error / 0 Warning**, Code=164602 RO=72758 RW=2188 ZI=83616 |
| `run_stop_result_s1.sh` 修前对照(基线 1348eb0 源码) | **PASS** target 48/48, host_ovr 64/64, 台账自洽 |
| `run_stop_result_s1.sh` 对 S2 树 | **COMPILE_FAILED / rc=4(基础设施故障)** — 夹具缺 `drv_epwm.h` 等, 见 `s1_chain_before_after.txt` |
| V1/V2 (`build_and_run.sh`) | 本轮未执行 |
| I/O 四变体 (`run_io_behavior.sh`) | 本轮未执行 |
| SD 链 / 元负例 | 本轮未触及数据语义, 未执行 |
| 两项必要反向(S2) | 未执行; 两项 S1 旧反向的注入锚已因重排失效, 等价合同需重指 |

## 9. 未测边界与不得越界的说法

- 不声称任意 shell/其它核/NMI/HardFault 下全局安全: 本协议只协调**本核普通可屏蔽上下文**;
  通用 `pin` 命令、`pin_status` 主动重写、selftest 的初始化写仍在协议之外。
- 不给出任何停机时延数字: 窗口长度与各段实际耗时 NOT_TESTED(RISK-03/03a 仍开放)。
- `MOT_STOP_API_ACCEPTED` 只表示驱动 API 接受了关断请求, **不是**示波器证明无脉冲;
  `current_hz=0` 是软件命令值; `rt_pin_write` 返回 void ⇒ 裸写成功不等于焊盘确认。
- PC.6/IO70 是被动 ENN 反馈(HIGH=禁能侧), 不是急停输入; 不改极性、不驱动它、
  不注册为急停。PC.23/IO87 LOW = 撤销软件许可。线路完整性仍属硬件验收(RISK-04-b)。
- BUG-008(STEP 频率波形/示波器验收)仍 NOT TESTED; CP1 顺带修掉的
  `signal_freq` 未初始化缺陷属于**计数模式取源变更**的副作用, 其对频率的影响未上板验证。
- 用户已确认的静态电气与 SW1 功能按 `USER_CONFIRMED` 保留, 不重复索取五点电压。

## 10. 四宏与硬件

- `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED = RT_FALSE`
- `STEP_DIAG_ISOLATED_ENV_CONFIRMED = RT_FALSE`
- `SAFETY_LIMIT_INPUTS_ENABLED = RT_FALSE`
- `TMC_DRIVE_CONFIG_HW_VALIDATED = RT_FALSE`
  (本轮实测复核: `motor.h:69`、`motor.h:83`、`project_board.h:51`、`tmc2209.h:99`)
- 硬件动作: **无**(AGENT_REPORTED)。未烧录、未接串口、未动 GPIO/TMC/STEP/电机,
  未运行真机 selftest。

## 11. 下一步(顺序明确, 不需要用户做架构选择)

1. CP3 夹具接入(`s1_chain_before_after.txt` [C] 的 7 项), 使 `run_stop_result_s1.sh`
   在 S2 树上重新可构建; 逐项解释"共享实际动作"导致的调用计数/来源变化,
   保留 S1 的全部失败/恢复/门禁断言。
2. 建 `run_stop_priority_s2.sh`: 真实互斥量 + 真实屏障 + 待处理 ISR 队列, 执行
   C01–C20 的适用集合, target 宏 FALSE 的拒绝用例在 target 变体真执行,
   需要成功 arm/诊断前提的在 host_ovr 真执行。
3. 两类必要反向(仓外副本, 构建成功 + rc=1 + 指定失败身份 + 正常对照):
   ① 去掉真实写提交门(旧 set / 旧 token 越门) ② 去掉 TX/flush 预算或恢复 epoch 检查。
4. 集中回归(V1/V2 + I/O + S1 两变体 + 两项 S1 反向重指), Keil 再一次 Rebuild All,
   最终交接与 push + `ls-remote` 回读。

STOP: CP1/CP2 为真; §11 集中验证未开始; 本轮不申请也不自称任何 GO。


---

# CP3 追加轮（2026-10-24，Asia/Shanghai）

任务书：`NS800_S2_CP3_Review_and_Completion_Task_4641d49_20261023.md`
（结论 `IMPLEMENTATION_REWORK`，要求"实现修复 + 夹具接入 + 原定集中验证"同批完成）。
基线核对：`4641d491c6ff1251730c99f6ad14eb4424f1dbac`；工作树在本轮开始时已有 4 个
未提交同文件改动（`motor.c`/`safety_state.c`/`stop_priority.[ch]`），属上一轮 CP1/CP2
之后的续做，**未** reset/rebase/merge。冻结的 `main`/`phase7/full-embedded-software`/
tag `v0.7-*` 未触碰。

## 12. R1–R6 逐项修改位置

| 项 | 改法（一句话） | 主要位置 |
|---|---|---|
| R1 pending 生命周期 + 慢结果身份 | `sp_take_pending()`/`sp_note_low_confirmed()` 删除，改成 `sp_pending_peek` + 窗口内 `sp_pending_ack_held(expected_epoch)` + `sp_reconcile_gate_held`/`sp_op_current_held` 两道身份门；确认一律带 `expected_epoch + requested_level`（`sp_note_permit_confirmed_for`）；`safety_force_shutdown` 与 `safety_reconcile_trip` 合并为**同一个** `ss_reconcile_once()`：peek → 窗口外慢观察 → `ss_lock`→`mot_lock` 在窗口外取 → **一个**短窗口内 gate+Motor/诊断/SS 落位+ack → 仍持 `ss_lock` 时 `blackbox_trigger` 恰好一次 → 放锁后打印 | `stop_priority.c`（确认/peek/gate/ack）、`stop_priority.c`（`sp_retain_held` 是唯一 pending 写入点）、`safety_state.c`（统一入口）、`motor.c` 的 `motor_trip_observe`/`motor_commit_off`/`motor_shadow_from_proof_held`/`motor_fault_fields_held`、`step_pwm.c` |
| R1 附带：`inhibit` 覆盖与 `ss_state` 写同窗 | `safety_transition()` 原来"判抑制"和"写 `ss_state`"分在两次窗口里 ⇒ 合并为一次 `sp_win_begin()` 内的 `sp_inhibited_held()` + 落位 | `safety_state.c` |
| R2 操作 token 局部快照 + txn 平衡 | `motor_arm` 用局部 `sp_token_t local_res`，HIGH/重试/确认/回滚全部显式收该快照；只有 armed 提交过门的**同一窗口**才发布 `mot_res/mot_res_ok/mot.armed`；`motor_start` 在第一次慢动作之前复制 `start_res`，之后只用快照；ramp 每拍复制 `tick_res`；`ce_sub`（确认提交返回码）参与成功判定；arm 取得 txn 之后的所有出口收敛到唯一 `out:` | `motor.c`（arm）、`motor.c`（start 快照）、`motor.c`（`mot_pwm_commit_gated` 用调用方快照） |
| R2 附带：apply/ramp 失败支不再重采 epoch | `mot_pwm_output_off(src, &pf)` 带回 proof；`mot_stop_unconfirmed_close(&pf)`/`mot_fail_closed(&pf)` 用 proof 的 rc/epoch；`mot_fail_closed` 在"关断已失败"那一支**不再第二次写 LOW**，只做带 settle 的确认 | `motor.c`、`motor.c`、`motor.c` |
| R3 DIR 全部静止条件与真实写同窗 | 数值 pin 先解析；持 `mot_lock` 后 `sp_win_begin()`，窗口内 `sp_peek_held()` 一次取全量 canonical（inhibit/exhausted/phase/owner/两路 latched/两路 output_active）+ 读 `mot.current_hz/state` 静止性，**同窗**写 DIR 与 `mot.dir`；退出窗口后才放锁打印；新增无窗口视图 `sp_view_motor_held`/`sp_view_diag_held`/`sp_diag_output_active_held` | `motor.c`、`stop_priority.c+`（`sp_peek_held`）、`stop_priority.h` 对应声明 |
| R4 保护前打印 | `motor_ramp_tick` 的 ENN 不一致：先 `mot_guard_shutdown`，后打印（只留静态名字指针）；`tmc2209.c` 的 IFCNT 不符 / 读回不符 / GCONF/CHOPCONF 漂移 / apply 预算区内的 4 段进度打印**全部取消**，细节改由 ctx/st 字段（`fail_exp`/`fail_got`/`fail_kind`、`ifcnt_before/after`）留档，只在保护完成后的那条摘要里打印 | `motor.c`；`tmc2209.c`、`1665-1700`、`1975-2005`（apply）、`2050-2095`（integrity） |
| R4 上下文判断 | `tmc_ctx_ok_for_wait()` 不再只看 `rt_thread_self()!=NULL`：`rt_interrupt_get_nest()!=0` ‖ `ns800_intr_masked()`（新增 HAL 查询原语，读 PRIMASK）‖ 无线程对象 ⇒ 拒绝，且**拒绝路径不在 ISR/屏蔽态打印** | `tmc2209.c`；`drv_uart.c`/`drv_uart.h` 新增 `ns800_intr_masked()` |
| R4 预算与提交闭合 | RX：CRC 通过后除总预算外**再查 `eff_end` 阶段期限**；TX：最后一个字节发出后、返回成功前**再查同一条 `start/total` 与阶段上限**；integrity 已进入操作后拿不到叶锁 ⇒ 按本操作 proof 立即保护（只保留最外层 txn 竞争的 `EBUSY` 容忍）；apply 的最终比较（剩余预算 + `inv_gen` + `op_epoch`）与 `CONFIGURED/done_valid/摘要发布`放进**同一个短窗口**；`tmc_cfg_view_publish(ctx, commit_same_gen)` 只有"同一代成功提交"才清失效标记，晚到成功只计数 `view_invalid_kept`；通信失败改投 `FAULT_TMC_COMM + EVT_SOFT_FAULT`（不再冒充物理 `FAULT_TMC_DIAG/EVT_TMC_DIAG`） | `tmc2209.c`（RX）、`380-405`（TX）、`1195-1235`（publish/invalidate）、`1607-1700`（fail_ctx/write_one）、`1860-1930`（apply 最终窗口）、`1988-2040`（integrity 叶锁） |
| R5 新增生产接口的编译期许可 | `sp_commit_permit_high` 只允许 `SP_MOTOR` 且要求 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` + Motor 侧配置许可；Motor RUN 另需 `phase>=ARMED`；诊断 RUN 保留"硬件宏 或 隔离环境宏"双宏合同；HIGH 裸写成功**不**等于 ARMED，`sp_arm_commit(_held)` 才抬到 `SP_ARMED`；`sp_pwm_start_held` 由真实 enable 成功建立 `SP_RUNNING`；非法 owner 取值一律 `-RT_EINVAL`；stop/LOW/保护在所有宏 FALSE 下继续允许 | `stop_priority.c`（`sp_high_permit_ok_held`/`sp_run_permit_ok_held`/`sp_owner_valid`/`sp_arm_commit*`） |
| R6 32 位饱和与恢复语义 | `SP_EPOCH_BITS=24` 的旧限制删除，统一 `SP_EPOCH_MAX=0xFFFFFFFFu`，不回绕、不运行中重置；`sp_can_advance_held()` 与 `sp_advance_held()` 的**成败条件对齐**（`epoch < MAX-1`，即"再加一还会不会撞顶"）；预约/释放/恢复都消费 advance 返回值：边界失败**不返回 EOK**、保持 `inhibit/exhausted`、仍照做实际关断；`sp_recovery_valid_held` 预检"还可前进 + 本次 PWM 被接受 + 许可写请求与确认都属 ticket"；成功恢复保留本次真实 `API_ACCEPTED` 证据、不清 `diag_ever_reserved` 语义历史；快照新增 `motor_output_active/diag_output_active/pending_epoch/advance_failed/first_unrecovered_valid/first_permit_unrecovered/first_permit_valid` 使"首错是否仍有效"可判定 | `stop_priority.c`（advance/can_advance）、`960-1125`（恢复三段 + 分 owner 记账）、`stop_priority.h` 快照与 proof 字段 |

## 13. 本轮自己引入并当场修掉的三处（如实登记）

1. `stop_priority.h` 的 R6 注释里写了 `(motor_*/diag_*)` —— 其中的 `*/` **提前闭合了块注释**，
   使头文件从该行开始被当成代码解析（主机 gcc 直接报 `unknown type name 'diag_'`）。
2. `sp_diag_output_active()` / `sp_motor_output_active()` 在改写为 R6 保守判据时丢了
   `l = sp_win();`，变成"`rt_base_t l;` 未初始化就 `sp_end(l)`"（gcc `-Wuninitialized` 抓出；
   在主机桩上还会把 `review_mask` 写成垃圾值）。**这类缺陷 4641d49 的 Keil 0/0 覆盖不到**，
   因为这三处都在该提交之后的未提交改动里 —— 也说明"上一轮 Keil 0/0"不等于本轮字节可编译。
3. `sp_copy_proof_held()` 里误写成 `sp.diag_off_confirmed_held()`（把自由函数当成员），
   以及 `sp_can_advance_held` 的前向声明放在使用点之后。
三处都由"先把四条既有链编起来"这一步暴露，修在 `stop_priority.c` 内，不改变协议语义。

## 14. 探针基线与修后证明

- 基线（`cp3_baseline_observation.txt`）：任务书 §12 附录探针脚本在**未修复**的精确
  `4641d49` 只读副本上真执行，本机 gcc 4.9.2（审查方为 13.3.0）——
  **12/12 基线观察成立**（10 个缺陷观察到 + 2 个正向对照 rc=0 + target 变体构建并执行）。
  按 §10.1：这里 rc=0 只表示"缺陷确实可复现"，不作为任何软件 PASS。
- 修后（`cp3_post_fix_proofs.txt`，构造脚本 `_s2work/probe_cp3_proof.py`）：
  同一业务身份逐项换成修后等价前置，**13/13 rc=0**（缺陷未复现）：
  `normal-disarm-pending`（disarm 成功后 `pending=0`、`inhibit=0`、`first_code=0`）、
  `reconcile-requeues-itself`（第一次整理后 `pending=0`、两次调用黑匣子只 1 次、`trips=1`）、
  `arm-txn-leak`（`txn_begin==txn_end==1`）、`dir-check-write-race`（DIR 写次数 +0 且 rc=-EPERM）、
  `guard-log-before-cut`（**停机前 0 条** `runtime guard:` 日志；顺序在日志里可核：GUARD 摘要行
  出现在保护动作之后）、`late-low-confirmation`（旧一代 LOW 确认返回 `-RT_EINTR`，`confirmed=0`、
  `stale_dropped+1`、新预约的 HIGH 焊盘保持 HIGH）、`old-start-adopts-new-token`（旧 start 被拒
  `rc=-EPERM`、`running=0`，新一代的 `target=777` 保持为其自己的请求）、
  `stale-slow-reconcile`（恢复+新预约+起转后，用旧一代身份再整理 ⇒ `ss_state` 未被改写、
  黑匣子增量 0、`mot.armed` 未被清）、`saturated-recovery-clears-inhibit`
  （`epoch=MAX-1`：`valid=0/commit=0/epoch 不动/inhibit 保持`）；
  正向对照 `control-healthy-arm-dir`、`control-core-stale-token` 与新增边界对照
  `boundary-recovery-just-below-max-control`（`MAX-2` 那一代恢复**成功**，证明边界拒绝
  不是"恢复永远不可达"）；target 变体 `core-bypass-target`（三宏 FALSE 时
  `reserve→HIGH→PWM_START` 被 Core 自己拒绝：`rc=-EPERM`、`pad` 未变 HIGH、`phase=RESERVED`）。
- 前置不成立（`rc=2`）在本轮**一次也没有**发生：13 例的前置全部真实建立。

## 15. §9 七项夹具接入：实际状态

| 项 | 状态 | 落点 |
|---|---|---|
| 1 `drv_epwm` 头/端口 | **已完成** | S1/F2d/I-O 三条链都编译**真实公开** `drivers/drv_epwm.h`（runner 新增 `-I HAL_DRV`），只在仓外输出目录生成最小 `<rtdef.h>`/`<rthw.h>`/`<drv_common.h>` 兼容头；端口 `ns800_epwm_prepare/commit_prepared_held` 用真实签名。**注意**：这只是"头 + 端口"，真实 HAL 计算/提交叶子（`drv_epwm.c`）**没有**在主机执行 |
| 2 IRQ / rthw | **部分完成** | `rt_hw_interrupt_disable/enable` 改为**递归保存**语义（`disable` 返回进入前的值、`enable` 恢复入参）；新增窗口内禁止项观察 `ps_win_note()`（`rt_kprintf`/`rt_mutex_take`/`rt_thread_mdelay`）与台账用例 `PS16a-window_forbidden_calls_zero`（两变体实执行，命中 0）；`rt_interrupt_get_nest`/`ns800_intr_masked` 端口显式表态。**未做**：真实线程互斥/屏障 + 待处理 ISR 队列（属 C01/C02/C03 的夹具，见 §16） |
| 3 删除的 `force_stop` | **已完成** | `PS05h/PS05a/PS05b/PS09/PS10c/PS14h` 的前置与断言改走生产入口 `pwm_test stop`（内部即唯一的 checked 关断路径）；失败未知、不释放所有权、无事件自激、人工恢复可达等断言**全部保留**；`io_main.c` 的 `step_pwm_force_stop`/`step_pwm_clear_stop_latch` 桩换成 `step_pwm_shadow_from_proof_held`/`step_pwm_recovery_fields_held` |
| 4 grant 参数 | **已完成** | 真借用调用一律带 `sp_token_t *out_token`（`PS11b`、`PS05f`、`IO9c`、`C1`）；被拒时断言 `owner==SP_NONE && epoch==0`；只读体检新增用例 `PS11b2-gate_check_is_read_only`（`motor_diag_gate_check()` 零 enable/零 disable/不产生预约） |
| 5 GPIO 新端口 | **已完成** | raw 请求（`enable_write_rc`/`shim_permit_raw_rc`）、实际 pad（`pad_level`/`shim_pad_low`）、确认返回（`safety_drv_permit_confirm_read` 只看 pad）、ENN 反馈（`fb_*`）四条独立注入；确认带 `epoch + 请求电平`；窗口内禁止项被 `PS16a` 记录 |
| 6 反向锚 | **未做** | S1 两项反向（`rev_motor_off_ignores_failure` / `rev_diag_fail_releases_owner`）在 CP3 的字节上**没有重指**，S2 两类反向（去掉真实写提交门 / 去掉 TX-flush 预算或恢复 epoch 检查）也**未建立**。既有反向脚本仍指向 CP1/CP2 的锚点文本，未执行 |
| 7 编译新模块 | **已完成** | `stop_priority.c` 作为**真实实现**编入 S1（`ps_main.c`）、F2d（`host_main.c`）、I/O（`io_main.c`）三条链；核心实现未换成桩 |

`§9` 末段点名的"旧 runner 打印 `HEAD` 而非 `SRC_APP` 的标签问题"已就地更正：S1/F2d/I-O
三条链的头部同时打印 `SRC_APP=` 与 `基线=`（后者是 `git rev-parse HEAD`，即工作树所在提交，
快照目录由 `SRC_APP` 显式给出）。

## 16. S2-C01…C20：适用性与**实际状态**（关键未决项）

- 本轮**未执行**任何一条 `S2-Cxx`。原因如实写清：
  §11.1 要求 C01/C02 用**另一线程真实持有 `ss_lock`/`mot_lock`**、C03 用**模拟 ISR +
  事件发送失败**、C10/C17 覆盖**真实新增的 HAL 叶子**（`drv_epwm.c` 的 prepare/commit、
  `drv_uart.c` 的 TX 等待），而本轮只完成了"头 + 端口"与窗口内禁止项观察（§15 的 1/2/5/7 项）。
  单线程探针（含本轮 §14 的 13 例）**不能**替代这些用例，本轮也不把它们算成通过。
- 已具备的前置（不是结果）：MinGW `-mthreads` + pthread 已实测可用（`/tmp/pt.exe` 连通），
  三条既有链已能在 S2 字节上构建运行，`sp_*` 生产 API 与 pending/ack/gate 原语已定型。
- 待建夹具清单（下一步直接照此执行，无需再选架构）：`tests/host/run_stop_priority_s2.sh` +
  `tests/host/sp_conc_main.c` + `tests/host/shim_s2/`（pthread 后端真实互斥量、屏障、
  串行化短窗口的 IRQ 模型与待处理 ISR 队列）；`C10` 需把 `drv_epwm.c` 编进来（配一个
  寄存器层 shim），`C17` 需 `drv_uart.c` 的 `ns800_wait_tx_space` 真等待叶子或其可注入等价物。
- §11.2 要求的两项 S2 局部反向同样**未执行**（见 §15 第 6 项）。

## 17. 本轮集中回归（四条既有链 + Keil）

| 链 | 变体 | 实际结果 |
|---|---|---|
| `run_stop_result_s1.sh` | target | `必需=50 唯一出现=50 行数=50 SUMMARY(cases=50 pass=50 fail=0) 自洽=1 proc_rc=0` → **PASS**（48 项 S1 合同 + 新增 `PS11b2`/`PS16a`） |
| 同上 | host_ovr | `必需=65 全部 PASS fail=0 proc_rc=0` → **PASS**（64 项 + `PS16a`） |
| `build_and_run.sh` | V1 / V2 | `V1: CHECK_PASS=25 CHECK_FAIL=0`；`V2: CHECK_PASS=22 CHECK_FAIL=0`；`RUNNER RESULT: PASS (rc=0)` |
| `run_io_behavior.sh` | off_target/on_target/off_ovr/on_ovr + 非法宏 | `27/29/40/42 必需用例全部 PASS`，`IO1_CFG-illegal_macro_rejected OK(拒绝编译 rc=1)`；`IO RUNNER RESULT: PASS` |
| `run_safety_data_semantics.sh` | 两变体 | `SD RUNNER RESULT: PASS (rc=0)` |
| Keil Rebuild All | `rt-thread` | `0 Error(s), 0 Warning(s)`；`Code=168018 RO-data=74602 RW-data=2188 ZI-data=83696`；armclang V6.24；**只构建，未烧录** |

共享实际动作导致的计数/来源变化，逐项解释已写进用例注释与日志（§10.4 要求）：
`PS06a/PS10a` 的 `want_dis` 由 S1 的"+2（Motor 与诊断各一次）"改为"+1（一次共享 disable）"；
`PS05a/PS05b` 由"恰好 1 次 disable"改为"+2 且 `sp_epoch()` 确实前进"（`pwm_test stop` 失败
交接给既有链 = 一次**新的显式保护请求**，§4.2 允许且必须换代的形状）；
`PS14a` 的"disarm 之后 Safety 仍 READY"改为"S2 §8.2 的可观测性合同：视图立即为
`FAULT_LATCHED`，且该事实来自 canonical 抑制位（`sp_inhibited()`）并留下待整理义务
（`safety_trip_pending()`）"；`IO5a` 的等待条件改成守卫自己的归因记录（真实线程链路上
`FAULT` 可由两条路径产生，盯状态会在守卫之前取样）；`IO4c/IO8b` 改用 S2 许可的两条原语
建立/判定。**没有任何检查被删成更弱**，也没有为过测试放宽门禁。

## 18. 仍未验的边界（不因本轮而改变）

- RISK-03/03a：停机最坏时延仍**无**上界证明（`tmc_xfer_lock`/`mot_lock`/`ss_lock`/`cfg_lock`
  的 `RT_WAITING_FOREVER` 未全部改掉；`§10.1` 预算表仍是**源码结构合同**）。
- RISK-04-b（物理）：Safety IRQ 门仍 CLOSED、输入极性 NOT CONFIRMED、诊断 MONITOR_ONLY；
  电气/模拟层、波形、瞬态、连续性、外接 NC、断线容错、安全等级全部未验。
- 主机三条链的 `drv_epwm.c`（真实计数模式/寄存器提交）与 `drv_uart.c`
  （`ns800_wait_tx_space` 真等待、`UART_isSpaceAvailable`/`UART_writeChar`）**都没有执行**，
  只有端口桩；`rt_hw_interrupt_disable` 是掩码模拟，不是 PRIMASK。
- 目标 RT 调度、真实线程交错、Flash/黑匣子写盘时序均未在主机覆盖。

## 19. 下一步（顺序明确）

1. 建 S2 并发夹具并执行 `S2-C01…C20` 的适用集合（§16 清单）；target 与 host_ovr 分别真测。
2. 两项 S2 局部反向 + 把 S1 两项反向锚点重指到本轮真实动作/提交点，逐身份判定。
3. 之后再一次集中回归 + Keil + 交接 + push + `ls-remote` 回读。

## 20. CP4（phase8-stop-priority-s2-cp4，2026-10-24，修复与验收同批）

起点身份：CP4 起点 = 远端回读 tree `0fdd0801430a41f3fcf8e6032eb3a634442686de`；本轮两个
提交 = `05d0518`（K1–K6 + 夹具与 runner）与 `5138577`（终字节 Keil 修复 + 反向重指向）。
冻结引用未动：main `1b3757ee…`、phase7 `6d3fbc55…`、tag object `097935fe…` → firmware
`e40a9b95…`。工作树在取证据时为 clean（证据文件里逐字记录了 `SOURCE_HEAD` 与构建输入
blob 清单）。

### 20.1 §10.1 基线取证（DIRECTLY_VERIFIED）

在**未改动**的 0fdd080 仓外副本上执行任务书 §12 探针脚本（逐字复制）：12 项具名缺陷观察
全部复现，且数值与 §2 表一致（例如 `empty-pending-stale-reconcile running=1 ss=7
inhibit=0 epoch=4`、`low-write-error-hidden-disarm rc=0 low_rc=-12 confirmed=0
write_epoch=0 epoch=1`、`release-off-boundary-reports-success rc=0 epoch=4294967295
advance_failed=1`、`returned-diag-off-view-diverges diag_result=4 diag_off_confirmed=0`）。
`OBSERVATION_CHECKS 12 / 12`。**这里的 rc=0 只表示"该基线预期缺陷观察成立"，不是软件 PASS。**

### 20.2 K1–K6 落地（生产代码）

- **K1**：删 `ss_reconcile_once` 的 `!had` 旧状态补写旁路（只说明"没有待整理的事实"）；
  `motor_lock_take_checked()` 返回 `rt_mutex_take` 的**真实**返回值；`ss_lock`/`mot_lock`
  在 `ss_reconcile_once`/`fault_reset`/最终提交三处逐处判返回值，失败即**不写 Motor/SS/诊断、
  不 ack、不触发黑匣子**；锁序 `ss → mot → 短窗口` 不变；`motor_trip_observe()` 让消费方
  能区分"电平观察成功"与"确认未提交"（返回提交状态，`out_read_rc` 另给原始读结果）。
- **K2**：身份覆盖整条命令 —— 新增 `sp_reserve_for(expected_epoch, want, out)`（arm 的
  origin epoch 在入口、第一次等待之前捕获）；`motor_start` 的 `READY→RUN` 与 `MOTOR_ACCEL`
  在**一次身份绑定提交**里完成；斜坡 tick 令牌改在**第一个** `mot_lock` 窗口采集（删掉后来
  的二次采样）；普通状态提交走带检查入口 `safety_transition_for()/safety_transition_held()`；
  启动自检的 epoch 比较 + 白名单 + READY 写在 `ss_lock` 下的同一个窗口；修掉"归零后提前
  return 跳过 RUN→READY"；C20 断言 SS=READY、预约保留、可再启动。
- **K3**：任何真实写之前先做 token/expected_epoch 检查的关闭；旧回滚一律不写硬件、不清新
  owner、不留 pending；业务失败（prepare/commit/enable/ENN）经 `mot_fail_op()` 按固定次序
  产生 inhibit+换代+pending（核原身份 → 换代/抑制/留 pending → 真实关闭一次 → 独立 LOW
  一次 → 存两个 raw rc 与原业务错误 → 带回本次 proof → 窗口外才确认/整理/通知/打印）；
  TMC 改为回传 `_for(expected_epoch, result)` 摘要，Motor 不再二次采样 epoch、不再二次保护；
  `sp_trip_for_operation` 的 EINTR 分成"本次保护 / 同一次已保护 / 过期丢弃"；保护一律先于
  打印与慢锁（含 arm `:1100/:1116`、start `:1402/:1419`）。
- **K4**：一次关闭动作的 `proof.epoch`/`pwm.epoch`/`permit.write_epoch`/确认 `expected_epoch`
  是同一个事实；PWM/LOW/确认/换代的返回值全部被消费，对外优先序 PWM > permit > 身份/边界，
  raw 分列（LOW 裸写失败而焊盘碰巧为 LOW 时**不**把 API 返回改成成功）；恢复复核的 LOW-only
  失败与确认失败都是新失败（换代 + 保持抑制）；边界（reserve/release/off/recovery）全判且
  边界仍照做关闭但返回非 EOK；错误/边界处理不清首故障、不放松锁存。
- **K5**：ISR/屏蔽上下文拒绝路径不打印（改为 raw 错误 + pending/notify 记账，
  `ss_evt_no_system/ss_evt_send_fail/ss_evt_log_suppressed` 可查）；
  `ns800_uart_write_budgeted` 自检 `rt_interrupt_get_nest()!=0 || ns800_intr_masked() ||
  rt_thread_self()==NULL` 并无打印、无延时地拒绝；末字节后再核一次 `start/total` 且
  `*written` 保持真实。
- **K6**：分 owner 的诊断视图（`view_diag`/`diag_off_confirmed`/`diag_output_active`/proof）
  同源于一套判据；HIGH/RUN 额外核 TMC 编译期许可 + 同代无等待配置摘要（不拿锁、不 UART、
  不延时）；ARMED 只在同 token 的 HIGH 已写且已确认、必需 ENN 复核通过后才成立；
  `sp_phase_set` 删除；RUNNING 只能来自真实 enable 成功；诊断保持双宏合同且永不取得 HIGH；
  关闭动作在四宏全 FALSE 时始终可用。

### 20.3 夹具实证抓到的两处真实产品缺陷（不是测试问题）

1. `sp_off_attempt_for` 的"正式释放"分支只在 PWM 关断失败时追加义务；许可 LOW 裸写**单独**
   失败时既不抑制也不留 pending，还停留在"撤销预约 + 输出影子已清空"的假事实上。
2. `sp_recovery_probe_off` 的 if 链把 le-only 失败整支漏掉（第一个条件先命中并把 owner 清成
   `SP_NONE`），夹具实测 `permit.write_rc=-12` 而 `epoch 7→7、advance_failed=0`，同时
   `fault_reset` 已在打印"已换 epoch 并保持抑制"——那句话当时是假的。票据因此仍然"有效"，
   正是 §6.3 禁止的形状。两处都已修（C05c/C12b 现真执行且 PASS）。

### 20.4 §9 并发夹具与 C 集合（DIRECTLY_VERIFIED）

`tests/host/sp_conc_main.c`：真实 pthread 互斥量（按持有线程递归计数 + 所有权，与
RT-Thread `rt_mutex` 语义一致）、真实线程、全局独占短临界区（等价 PRIMASK 屏蔽期的不可抢占）、
被屏蔽期间排队并在窗口结束后由同一线程交付的 ISR 队列、四个显式接缝（`rt_pwm_disable` /
`permit_confirm_read` / `integrity_check_for` / `drv_feedback_confirm`）、可注入取锁错误与
event 发送失败；墙钟 timeout 只作死锁守卫。构造源入库于 `tests/host/sp_conc_gen/`
（三个生成脚本 + README；两种行尾检出生成逐字节一致的夹具，已各验一次）。

`tests/host/run_stop_priority_s2.sh` 复用既有守卫/台账纪律：ONLY 先验证、执行集合披露、
逐组逐 id 必需且出现恰好一次判 PASS、SKIP 只允许出现在本变体声明过的 id、`pre_bad!=0` 与
异常退出/超时一律 INFRA-FAIL、变体用例总数对账。真实执行结果（同一终字节）：

| 变体 | 组 | 用例 | 结果 |
|---|---|---|---|
| target | 13 | 22（另有 10 条按声明 SKIP） | 全部 PASS，runner rc=0 |
| host_ovr | 13 | 40（1 条按声明 SKIP） | 全部 PASS，runner rc=0 |

覆盖 ID：C01/C01b/C01c/C02/C02b、C03a/b/c、C04a/b、C05a/b/c/d、C06、C07a/b、C08a/b、
C09a/b/c、C11a–d、C12/C12b/C13/C14、C15a/b/c、C19a/b、C20a–e、C21a/b。需要成功输出前提的
用例在 host_ovr 真执行；目标 FALSE 的拒绝（C21a/b）真在 target 执行。C15c 的换代由**另一线程**
完成（停机+恢复+新一轮 arm/start），不是同线程在接缝里重入业务。

### 20.5 §10.1 修后证明 + §10.2/§10.3 反向 + §10.4 回归（DIRECTLY_VERIFIED）

- 修后等价前置：12/12 项 `pre=1 defect=0 safe=1 => PROVEN`（含对照 `control-healthy-cycle`）；
  四项签名变化的观察改用新的等价入口，未把"编译不过"当作缺陷消失。
- S1 反向①（关断忽略失败）注入点重指到协调层结果判定：META-OK，命中
  PS01d/PS02d/PS04a/PS10b/PS13d，额外 FAIL 12 例如实计入台账；原登记 PS01a/PS02a 只断言对外
  rc，本注入触及不到，留着会把"注入没触及"误报成"没检出"。
- S1 反向②（失败提前交回 owner）在本链**不可观测**：三次试投（不退回 HELD_UNKNOWN /
  不恢复 `diag_output_active` / 撤销预约不看持有者）都是"注入落地 + 台账自洽 + proc rc=0"，
  故按 §10.2 重指向 S2 并发链；runner 里以 `REV-POINTED` 行登记，不冒充本链检出。
- §10.3 两项 S2 反向 META-OK：`rev_stale_identity_gate`（去掉 `sp_off_attempt_for` 的
  expected_epoch 门）→ C09b/C15b 命中；`rev_recovery_ticket_check`（去掉
  `sp_note_permit_confirmed_for` 的 epoch/电平/写代一致门）→ C08b 命中。两者均要求副本
  构建成功 + 台账自洽 + 业务 rc=1；先试的 `sp_recovery_probe_off` 票据门在 recovery 组
  没有任何用例消费（NOT-DETECTED），已据此改选门并把该记录写进注释。
- 共享动作计数变化的解释：按 §5.2 的"一次关断"合同，普通 disarm 失败从"再关一次"改为
  "复用同一次关闭结果 + 只 notify"，PS05a 的期望由 `disable ≥2` 重锚为 `disable==+1` 且
  换代/pending/事件计数各判一次，失败与门禁断言未删弱。
- 同批回归（终字节全部真执行）：S1 target 50 / host_ovr 65 PASS；I/O 四变体 27/29/40/42 +
  非法宏值拒绝编译；SD 21/20；V1 25/0；V2 22/0。V1/V2 与 I/O 各跑一次；SD 因数据语义未动
  只做一次全量（不机械重跑）。

### 20.6 §10.5 Keil（DIRECTLY_VERIFIED，只构建）

第一次 `-b` 增量构建暴露一条真实缺陷（原文保留在证据文件 B.6）：
`stop_priority.c(109): error: call to undeclared function '__get_PRIMASK'` —— 本模块没有
包含带进该 intrinsic 的设备/CMSIS 头。改为与文件内 `__DSB` 同一原则：本模块自带读 PRIMASK
的原语（仅 `__ARM_ARCH>=7` 分支），语义反而更严（读真实 PRIMASK，别处关中断也算）。
终字节 `Rebuild All(-r)`：**0 Error(s), 0 Warning(s)**，
`Program Size: Code=172978 RO-data=76562 RW-data=2188 ZI-data=83912`；
工程 target=rt-thread、armclang V6.24、After Build 仅 `fromelf --bin`（无烧录/下载钩子）；
四个目标宏构建前复核仍全部 RT_FALSE（`motor.h:69`、`motor.h:83`、`project_board.h:51`、
`tmc2209.h:100`）。

### 20.7 §11 未执行（NOT_EXECUTED，不得当作通过）

- **C10/C16/C17/C18 未执行**：本轮没有把 `drv_epwm.c`（prepare/commit 与 TBCTL 接缝）与
  `drv_uart.c`（`ns800_uart_write_budgeted` 的等待环）编进主机链。现有夹具只提供端口副本，
  按 §9 不得称真实 HAL；因此 §10.3 家族①的"去掉真实危险写"与家族②的"去掉真实 TX/flush
  预算"两半同样未执行（已执行的是这两个家族的另两道门）。
  下一步的具体做法：优先 SDK/寄存器 shim 直接编 `drv_epwm.c`/`drv_uart.c`；若需要最小函数
  抽离，必须目标与主机共用同一实现并接入 Keil 工程，不允许复制伪算法。
- 黑匣子持久化格式、无关内核/框架、既有故障数值：未改。
- RISK-03/03a（停机时延上界）与物理 RISK-04-b 继续开放；本轮不声称任何波形或物理证明。
- 无烧录、无串口/调试器、无设备扫描、无读 Flash、无真实 GPIO/TMC/STEP/电机/供电动作，
  未执行真机清故障、自检或改 SW1。

### 20.8 证据与工件

同一 evidence 目录，cp4 只有**一个**原始结果文件
`reports/reviews/phase8-stop-priority-s2-integrated/cp4_baseline_observation.txt`：第一部分
= 基线 12 项观察（0fdd080），第二部分 = 修后证明、并发链、反向、回归与 Keil 的原始输出，
逐节标 `SOURCE_HEAD` 与构建输入 blob 清单；AGENT_REPORTED / DIRECTLY_VERIFIED /
NOT_EXECUTED 分列。三份 cp3 证据只做身份段脱敏（用户目录/仓库绝对路径 → `<USER>`/`<REPO>`），
错误原文、时间戳与 rc 未改。`git diff --check`：源码/测试/runner 干净；证据文件里只剩 1 处
行尾空白，来自 Keil 日志的 `Program Size: …` 原样捕获——按"证据不得改写"保留，不做格式化。

STOP: AWAITING_INDEPENDENT_IMPLEMENTATION_REVIEW
NO SOFTWARE_REVIEW_GO claimed by Agent
NO board/serial/GPIO/TMC/STEP/motor authorization
FOUR target macros remain RT_FALSE
RISK-03/03a and physical RISK-04-b remain open


--------------------------------------------------------------------------------

## CP5 追加(2026-10-24): §5 真实 HAL 叶子接入完成 + §6 反向补齐 + §7 取证修复

接续任务 ID `phase8-stop-priority-s2-hal-closeout`。起点 = 远端分支头 `2fbac9a`
(本次独立 ls-remote 实读的完整 SHA 见下), 本轮提交前的工作树差异全部记录在下面。

### 1. 本轮落地的生产改动
- §2(P1) TMC 每次调用改用**局部 op 身份** `tmc_op_t{epoch/did_protect/outcome/proof}`,
  贯穿 apply/integrity/fail_ctx/result_fill; 未取得 txn 的调用不再改写正在执行那笔操作
  的身份, 出参只反映本调用; `expected_epoch` 的"0 当未指定"哨兵已去掉(0 是合法初始代次)。
  共享 ctx 上只保留 `op_epoch/op_protect` 两个**受保护观测**字段(拿到事务锁才写)。
- §3.1(P1) arm 失败摘要改为**先受检保护、后打印**; HIGH 已发出之后的两处最终提交拒绝
  出口统一走 `mot_fail_op()`(带原 token), 不再只释放预约而遗留本次 HIGH。
- §3.2 ramp 的 TMC 复核改为 `tmc2209_config_integrity_check_for(guard_epoch, &gres)`
  (拍初快照身份), 守卫消费该局部结果/proof; 慢复核之后、写 Motor 的
  current/target/state 之前, 持 mot_lock 复核本拍身份, 被取代就整笔丢弃并直接返回。
- §5 HAL 侧只做**编译边界**调整: `ns800_intr_masked` + `ns800_uart_write_budgeted` 逐字
  移入 `drivers/drv_uart_txleaf.inc`, EPWM 的三个叶子函数逐字移入
  `drivers/drv_epwm_leaf.inc`, 私有结构共用声明放在 `drivers/drv_epwm_leaf.h`;
  `drv_uart.c` / `drv_epwm.c` 在原位置 include 同一份文件。算法一行未改。

### 2. §5 真实叶子链(C10/C16/C17/C18)已真实执行
新增第二个夹具 `tests/host/leaf_main.c` + `tests/host/shim_leaf/`(最小 SDK/寄存器 shim,
每条声明带本地 SDK 原文出处, 布局用 `_Static_assert` 钉住), 在**同一份 runner** 里作为
第二二进制执行 4 个组 27 条用例:
- C10(7): prepare 零寄存器写 / 换算在门外 / period-compare 与独立复算逐位一致 /
  有效身份真写 20 个寄存器 / 冻结的 TBCTL.CTRMODE 不被配置提交恢复 /
  **旧 token 提交寄存器写计数必须 0**(§5 核心) / 旧 set 路径同判据。
- C16(7): 另一条真线程持事务锁时预算内拒绝且不产生保护与总线流量 / 未取得不归还 /
  恰好一次归还 / **BUSY 的竞争调用不改活跃 op 身份**(§2 反例并入, 旧身份与活跃身份
  是两个不同的数) / 真实失败仍恰好保护一次 / RX 洪泛的字节上限与时间上限都在 TX 之前。
- C17(7): 真 TDRE 等待环的 无空间 / 部分发送(原始 written=3) / 末字节后被抢占才过期
  (written=8 仍不发布成功) / 正常整帧对照 / ISR+掩码+空设备三条上下文判据静默拒绝 /
  应答完整但迟到 / CRC 错 —— 失败一律先保护后日志(错误打印时许可仍 HIGH 的次数=0)。
- C18(6): 一次 apply 只用同一条 1500 tick 外层预算跨 4 写 + 8 读 + 2×IFCNT(实测 87 tick,
  写帧 4/读请求 12/IFCNT 4/GCONF 0x141/CHOPCONF 0x15030053) / 预算耗尽不重开也不发布
  过期成功 / tick 跨回绕的两种判据 / 晚到的真实成功不刷新摘要与健康(state 仍非
  CONFIGURED, ok_for_arm 仍 FALSE, 而芯片确实收到了那些写)。

### 3. §6 反向: 命名准确化 + 三项新增(共 5 个家族)
- 改名 `rev_recovery_ticket_check` → `rev_permit_confirm_identity_gate`, 并明说它没有
  移除恢复最终票据判据(恢复最终窗口用 C14a/C14b 的确定接缝)。
- `rev_config_write_token_gate`: 保持窗口原语, 只取消 `sp_enter_token` 的资格检查
  → C10e 具名检出。
- `rev_tx_flush_budget`: 去掉真实叶子的末字节预算复核 + 去掉真实 flush 字节上限
  → C17c 与 C16c 具名检出(两处都是有限输入, 错误版本正常返回, 不拿墙钟 timeout 当检出)。
- `rev_fail_returns_owner_early`: 先把 C11d 扩到对 canonical `owner==SP_HELD_UNKNOWN`、
  锁存、输出未交回三者同时断言(夹具与生成源同步改, 重新生成逐字节一致), 再在 **C11d
  真正经过的 release 失败分支**去掉那句收回 → 具名检出。
  如实记录: 首轮注入落在 `release_on_ok=FALSE` 分支, 结果是 NOT-DETECTED, 改对分支后
  才算覆盖。

### 4. §7 取证缺口
- 根因与逐条交代写在同一份原始结果文件的 **[C.0]** 节; CP4 遗留在 bash /tmp 的原始 stdout
  与那条首次 `__get_PRIMASK` 构建错误原文均**不可恢复**, 没有人为重造, 只登记差异并在
  定稿字节上完整重跑(构建命令行/manifest/blob 全路径/最终 Keil 全文都已附)。

### 5. 结果与边界
- S2 + 叶子链: target 23 例全 PASS; host_ovr 68 例全 PASS; 5 个反向家族均 META-OK
  (业务 rc=1, 台账自洽, 预登记失败身份全部命中)。原始输出见同一份 cp4 结果文件 [C] 段。
- Keil Rebuild All: 0 Error / 0 Warning(只构建); `.dep` 证明两个 `.inc` 真被目标编译包含。
- 未烧录/未接串口/未做任何真实 GPIO/TMC/STEP/电机/Flash/供电/接线/SW1 动作;
  四个目标宏保持 RT_FALSE; 板上状态按 USER_REPORTED_E40, RISK-03/03a 与物理 RISK-04-b
  继续开放。新登记的 STEP 频率换算口径见 [C.8]。

### 6. 负例链(13 例)的两件如实交代(CP5 §8)
- `tests/host/inject.py` 的 `probe_writeback` 注入配方仍按 S2 之前的两参签名调用
  `tmc_read_reg` / `tmc2209_write_register_confirmed`, 副本编译失败 => N9/N10/N10b 被记
  INFRA-FAIL(编译失败不算检出)。本轮给同一处注入补 `(RT_NULL, RT_NULL)` 两个尾参,
  断言与期望身份一字未改; 修后三例真执行并具名抓到 FD2/FD3/FD7b。
- `N7_helper_thread_startup_fail` 在全量顺序下出现过一次 `NOT-DETECTED(V2.run 期望=3
  实际=1)`, 而三次独立复跑与另一次全量运行都是 `OK=13 / NOT-DETECTED=0 / INFRA-FAIL=0`。
  根因本轮未定位, 作为开放项登记, 不记为通过; 两份原始日志都保留在证据目录
  (negatives.txt 与 negatives_flake_record.txt)。元负例 M1..M5 均 META-OK。

---

## CP6 追加(任务书 `phase8-stop-priority-s2-cp6-closeout`, 接续 bd8548c)

日期: 2026-10-25(Asia/Shanghai)。本轮在同一批里收 §2(EPWM 纳秒换算)、§3(真实 cfg/xfer
叶锁的有界等待)、§4(N7 根因 + 取证原文)与 §5(集中回归/Keil/交付)。未 rebase、未 force、
未 merge; 起点 HEAD = `bd8548cce8dfc7643dfc8dfa0884d5d2ea940d4d`, 本轮定稿 = `c84457b3e4f8960d14a750e87553b33c845d71a2`。

### 1. §2 P1: EPWM 换算按纳秒重做, 窄化前先判可表达

生产文件 `libraries/HAL_Drivers/drivers/drv_epwm_leaf.inc` 的 `ns800_epwm_prepare()` 整段
重写(其余形状一字未改: prepare 门外纯计算 -> token 门内固定整数写, 不写 TBCTL.CTRMODE):

- 单位: 调用方传 ns(RT-Thread PWM 契约、motor.c/step_pwm.c 的 `1000000000u / hz`), 不再把
  period 当微秒用; SDK 原式把频率小了 1000 倍, 1kHz 请求得到 5.0e7 个计数再窄化成 uint16_t
  (未定义行为, 云端观察到的 61568 不是合同)。
- 算法: 全 uint64 整数。`counts = round(period_ns*clk/(div*1e9))`; UP_DOWN 的
  `TBPRD = round(counts/2)`(一周期 = 2*TBPRD 个计数), UP/DOWN 的 `TBPRD = round(counts)-1`
  (一周期 = TBPRD+1); 比较值按模式的"高电平计数"关系给(UP: CMP=off; DOWN: scale-off;
  UP_DOWN: tb-off), 50% 在三种模式下都保持 50%。**先判范围再窄化**: TBPRD 与 CMP 任一超出
  16 位、或 counts 低于模式下限(UP_DOWN<4 / UP|DOWN<2)、或 period_ns 超出驱动 ns 域(>1e9)、
  时钟为 0/异常大、计数模式非法(含 STOP_FREEZE)、分频枚举越界 => `-RT_EINVAL`、`valid=0`、
  零寄存器写。极限占空比的口径写在函数头注释里(需要 CMP=TBPRD+1=65536 的那一端明确拒绝)。
- 分频因子: 按 SDK 枚举原文分 CLKDIV=2^n 与 HSPCLKDIV=1/2/4/6/8/10/12/14 两张表;
  **原配分频能表达就沿用**(不引入无关变化), 否则在全组合里取最小可用总分频, 使 20Hz 斜坡
  首拍与 1Hz 这类合法低频也能表达 —— 正常启动不再"首拍因范围错误而停机"。分频作用于整个
  EPWM 实例 => M-2 硬件边界保留, 本轮不据此翻宏。

夹具侧(同一批真实叶子, 不是端口桩):

- C10 组从"用生产公式逐句重算"改为**行为反推 oracle**: 夹具按 SDK 枚举自己写分因子表,
  由 `prepared` 里的枚举/计数反推回 ns 周期与占空比, 断言"请求 ns == 反推 ns(在声明的计数
  舍入界限内)"。实测: 1kHz/50% -> TBPRD=50000、CMP=25000、counts=100000、反推 1,000,000ns
  (界限 +20); 20kHz -> TBPRD=2500/CMP=1250/50,000ns; 20Hz 首拍 -> 选到 cd=3,hs=5(总分频 80)
  TBPRD=62500 精确; 1Hz -> 选到 cd=7,hs=6(总分频 1536)TBPRD=65104, 反推 999,997,440ns,
  误差 2560ns <= 声明界限 15360ns。
- 新增 C10g(20kHz 反推)、C10h(20Hz+1Hz 分频选择)、C10i(六种非法/不可表达输入: 0 周期、
  pulse>period、period 超出 ns 域、非法计数模式、时钟 0、时钟大到不可表达 —— 全部
  rc=-RT_EINVAL、valid=0、寄存器写增量 0)。C10a..C10f 一条未删, epwm_leaf 由 7 条增至 10 条。
- 脚本化时钟注入(`leaf_clock_force`)用于 C10i 的时钟无效/不可表达两种情形, 其余用例仍走
  原 200MHz 链(RCC_getPclk1Frequency 单位 Hz 的依据在 shim_leaf/drv_common.h 里逐行引用)。
- 旧换算缺陷的观察结论没有删除: [C.8] 里那条"STEP 频率换算分母口径"仍是历史登记, 本轮把它
  从"未修的既存缺陷"变成"已修 + 有反推用例", 交接里两处并存说明。

### 2. §3 P1 验证缺口: 真实 cfg/xfer 叶锁 + 三种等待形态

夹具 `tests/host/leaf_main.c`:

- 条件变量初始化器修正(`PTHREAD_COND_INITIALIZER`), 叶子链不再被 `[built-in] macro
  expansion` 警告卡成 INFRA-FAIL; 本轮**没有**任何仓外补丁 —— 叶子链 15+10+7+6 条直接在
  仓内字节上跑。
- `rt_mutex_take()` 改成三形态可区分: `RT_WAITING_NO` 一次 trylock; `timeout<0` 真阻塞
  (FOREVER); 有限 timeout 每轮真实 trylock + ~1ms 墙钟 + 脚本时钟 +1 tick, 走完即
  `-RT_ETIMEOUT`。记账 `leaf_mtx_takes/taken/timeouts/releases/last_request/last_wait`;
  不再"任意正数 = FOREVER", 脚本时钟与墙钟 1:1, 不会被别人续期。
- 新增 8 条(全部由**另一条真线程**用生产 `tmc_cfg_lock_take()` / `tmc_xfer_take()` 取得并
  持有叶锁, 主业务线程从生产入口进去):
  - C16d: cfg 叶锁被持有 -> 生产复核入口 `tmc2209_config_integrity_check_for()` 在 10ms 上限
    内到期(请求值实测 10, 到期次数 +2 = 受理出口 + fail_ctx 再取), 按本次身份(`op_epoch`,
    先经生产 `sp_reserve`/`sp_release` 真实推进过一代)保护一次: PWM 关断与许可 LOW 各 1 次、
    inhibit=1、pending=1(FAULT_TMC_COMM)、`protect=BY_THIS_CALL`、`proof.valid=1`, 且前置
    把许可**观测为 HIGH**(结束为 LOW), HIGH 期间错误打印 0 次, 摘要作废(ok_for_arm=0)。
    失败出口再取同一把锁也取不到, 但没有第二次关闭(cuts/lows 仍为 1/1)。
  - C16d2/C16d3: 竞争失败者没把持有者的锁放掉(第三方 trylock 仍 -RT_EBUSY); 持有者真归还后
    恢复可取; 全程 `真实取得 == 真实归还`(实测 14/14)。
  - C16e/C16e3/C16e2: xfer 叶锁被持有 -> 生产公开读 `tmc2209_read_register()` 在 20ms 上限内
    到期(实测请求 20), 零写帧零读请求、出参 value 未被改写; 同一把锁再用"外层预算只剩 3ms"
    走生产读事务本体 `tmc_read_reg()` -> 等待值 = min(20,3)=3 且 `reason=LOCK_BUSY`;
    归还后同一入口真读成功(GCONF=0x101)。
  - C16f/C16f2: cfg 锁 `min(cap=10, 剩余=3)` 实测 3; 预算已尽时 `-RT_ETIMEOUT` 且**取锁调用
    增量为 0**(不尝试)。
  - tmc_lock 组由 7 条增至 15 条; 显式用例数 `S2_EXPECT_host_ovr` 68 -> 79(不删断言维持计数)。
- 生产侧没有为本批改动: §3 要求的八条都在夹具/等待模型上成立, 未发现需要窄修的 TMC 预算或
  失败出口缺陷(按任务书"没有发现缺陷就不要为了本轮有代码改动改产品")。

### 3. §4 N7 根因(按现存原文的实际身份)与一处夹具模型修复

- 读的是保留下来的 `negatives_flake_record.txt` 那一次(V2 `expected=3 actual=1`)对应的
  **完整失败身份原文**: V2 `CHECK_PASS=15 CHECK_FAIL=6 BAD_SELFTEST=0 MISSING_CASE=3` ->
  `PROCESS RC=1`; 六条 FAIL 是 FD7a/FD7b/FD7c + FD8a/FD8b/FD8c(apply=-23, probe 读不到),
  三条 MISSING 恰是 N7 期望具名的缺席(C7 / FD4 / T14)。**同一次运行里 C5e 是 PASS** ——
  任务书静态候选的那条 C5 接缝(armed 与 notify 交错)不是这次的根因, 因此没有为它增加会合,
  也没有放宽任何期望 rc。
- 实际机制: `tests/host/host_main.c` 的屏蔽位是**进程级全局**, 而本链从 `motor_init()`
  起有一条真的生产线程(motor.c:1049 `rt_thread_create("motor", ...)`); 斜坡线程进自己的短
  临界窗口时, 主线程的 `ns800_intr_masked()`/`rt_thread_self()` 一起变成"不可等待",
  `tmc2209.c:157/:166 tmc_ctx_ok_for_wait()` 按设计**静默**拒绝 -> apply/probe 的读得到
  -RT_EBUSY -> 六条判据 FAIL; 总返回码按"FAIL 优先于 MISSING"变成 1, 把 N7 的 rc=3 盖住。
  目标上单核 + PRIMASK 期内不会被调度, 主线程根本观察不到"别人在屏蔽" => 夹具模型缺陷。
- 修复(最小): 屏蔽位/嵌套深度改成 `__thread`(同一处声明), 判据、期望 rc、断言一字未动,
  没有增加固定 sleep。同负载 A/B(6 路并发进程压 CPU, 各 24 次):
  修前 `rc=1/FAIL=6` × 2, `rc=3/FAIL=0/MISSING=3` × 22; 修后 24/24 全部
  `rc=3/FAIL=0/MISSING=3`。无负载单独复跑 20 次(修前字节)亦全部 rc=3。
- ⚠ 这不是"重现了原来那一场运行": 是按现存原文的失败身份, 在同一判据下确定性复现并消除。
  N7 的判定仍由 runner 的逐例预期矩阵给出(具名 MISSING + 真实 rc=3), 不用成功次数投票关项。

### 4. §4 取证原文与 runner 状态输出

- 同一份 `cp4_baseline_observation.txt` 追加 [D] 段(不另建取证平台): 现存两份负例日志全文
  (`negatives_flake_record.txt` 与 `negatives.txt`, 只做绝对路径前缀的机械替换)、N7 那一次
  V1/V2 的完整判定行原文、本轮 A/B 分布、定稿字节的最终正向/反向台账与负例/元负例真实退出码,
  输入 bd8548c 与定稿 blob(带路径)一并登记。CP4 那六份已明确不可恢复的 stdout 未重造。
- **对上一轮一句引用的更正(不删原文)**: [C.10] 第 2) 条曾写"另一次全量运行也是
  `OK=13 / NOT-DETECTED=0` —— 见 negatives.txt"。按留存工件核对, 该目录里的两份分别是
  `OK=9 / NOT-DETECTED=1 / INFRA-FAIL=3`(修注入配方之前)与 `OK=12 / NOT-DETECTED=1`(修之后),
  **没有** `OK=13` 的那一份被留下 => 该引用没有对应工件支撑, 更正登记在 [D.2b]; N7 当时
  两次都不是 OK(与本节根因一致)。本轮 `OK=13` 的证据是定稿字节上的**新一次**全量(见 §5),
  不是拿旧日志改口。同样说明: 两份日志头部记的基线 SHA 是运行时的 HEAD(0b0f1661), 内容才是
  后来的定稿字节 —— "定稿字节"与"SHA 标记"的口径在 [D.1]/[D.2] 里对齐。
- S2 runner 的 `grep -c "^injected " ... | xargs -I{} note ...` 改成 shell 内变量后调用
  `note`(note 是 shell 函数, xargs 里必然 `No such file or directory`); 本轮日志里
  "注入替换 N 处"五行即修正后的真实输出。
- 交接 JSON 里 CP5 遗留的一处非法转义(`%LOCALAPPDATA%\Temp` 的单反斜杠)使整份文件无法被
  `json.loads` 解析; 本轮机械修复(反斜杠翻倍, 显示内容不变), 现可解析。

### 5. §5 集中回归与 Keil(定稿字节)

- S1: target 必需 50 条全 PASS; host_ovr 必需 65 条全 PASS; rc=0; 反向: PASS —— 本链注入命中预登记业务失败身份(rc=1, 台账自洽); 第②项按 §10.2 重指向 S2 并发链代跑(见上方 REV-POINTED 行)
- S2 + 叶子链(REVERSE=1, 先过完整正常对照): 正向(REVERSE=1 的完整对照阶段): target 17 组 用例=23 全 PASS(SKIP 行 14, 整族按声明跳过); host_ovr 17 组 用例=79 全 PASS(叶子链 epwm_leaf 10 / tmc_lock 15 / uart_leaf 7 / budget_leaf 6, 期望用例数与实跑一致); 5 个反向家族 META-OK ×5 -> S2 REVERSE RESULT=PASS
- V1V2 / I-O: V1 rc=0, V2 rc=0; 两份终态: CHECK_PASS=25 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0 | CHECK_PASS=22 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0 / I-O: off_target | OK 必需用例=27 全部PASS 自报fail=0 proc_rc=0 (输出 8396 字节); on_target | OK 必需用例=29 全部PASS 自报fail=0 proc_rc=0 (输出 9682 字节); off_ovr | OK 必需用例=40 全部PASS 自报fail=0 proc_rc=0 (输出 23778 字节); on_ovr | OK 必需用例=42 全部PASS 自报fail=0 proc_rc=0 (输出 26194 字节); PASS (rc=0) —— 4 个变体的必需用例全部真执行且判 PASS, 非法宏值被编译拒绝
- 负例 13 例 + 元负例: OK=13 NOT-DETECTED=0 INFRA-FAIL=0; 所有负例按预期被检出且身份齐备; 干净回归通过 (rc=0); N7 本轮判定: OK | N7_helper_thread_startup_fail | rc 真实=3 自报=3 期望=3 | V1=RAN/0/3 V2=RAN/0/3 (期望 V1=RAN/0/3 V2=RAN/0/3) / 元负例 META-OK 行数=10; 末行: META-OK | M5_wrong_reason_guard
- Keil Rebuild All(仅构建): Rebuild All 退出码=0; 尾部 0 Error / 0 Warning; 日志 141 行; .inc 包含证据: build/project_rt-thread.dep 列出 drv_epwm_leaf.inc 与 drv_uart_txleaf.inc 各 1 处; 日志尾部 '0 Error(s), 0 Warning(s)', Code=173762 RO=76806 RW=2188 ZI=83784

### 6. 边界(不撤回既有结论)

- EPWM 的 ns/计数/分频反推与脚本化时间预算都是**寄存器模型 + 受控时间**上的证明, 不等于
  物理验收: BUG-008 波形/频率/占空比实测、RISK-03/03a、物理 RISK-04-b 继续开放;
  分频选择作用于整个 EPWM 实例, M-2 保留。
- 四个目标宏独立源码回读仍 RT_FALSE: motor.h:69 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED`、
  motor.h:83 `STEP_DIAG_ISOLATED_ENV_CONFIRMED`、project_board.h:51
  `SAFETY_LIMIT_INPUTS_ENABLED`、tmc2209.h:100 `TMC_DRIVE_CONFIG_HW_VALIDATED`;
  host_ovr 只存在于命令行覆盖。
- 本批无烧录/复位/串口/调试器/设备扫描/真实 GPIO/TMC/STEP/电机/Flash/供电/模块/接线/SW1/
  真机清故障/selftest 动作; 板上状态仍按 USER_REPORTED_E40。
- 原已接受的 S1/F2d 范围不撤回; SD 数据语义本批未改, 未机械重复。

`STOP: AWAITING_INDEPENDENT_IMPLEMENTATION_REVIEW`
`NO SOFTWARE_REVIEW_GO claimed by Agent`
