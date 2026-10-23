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
