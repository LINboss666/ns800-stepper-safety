# M7 CP0 — 基线源码事实审计

基线：`4a626bc49831345d8366deeb6b01cc81e77ec79d`（= 本轮开工时的本地 HEAD = 远端
`debug/tmc-uart-rootcause-20261028` HEAD，工作树干净）。
下面每一条都是**改前**在该提交上实读得到的；`git show 4a626bc:<path>` 可逐字复现。
标注 `BASELINE:` 的是改前事实，`NOW:` 的是本轮改后落点（相对当前工作树行号）。

BSP 根：`rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad`
APP = `BSP/applications`。

## A. current_adc.c/.h（换算与系数所有权）

1. BASELINE `APP/current_adc.c:28-29` `CUR_VREF_MV=3300`、`CUR_RAW_MAX=4095`；
   raw→mV 用 `raw*3300/4095`。3300 是**配置假设**，本轮没有把它写成"已测 VDDA"。
2. BASELINE `:37-40` 默认系数 `cur_offset_mv=1650.0f`、`cur_gain_v_per_a=0.6f`
   （注释即 `30mΩ × 20V/V` 理论值），`cur_calibrated=RT_FALSE`，
   `cur_cal_source=CURRENT_ADC_CAL_NONE`。
3. BASELINE `:129` `cal_apply` 的唯一数值门是 `if (gain_v_per_a <= 0.0f) return -RT_EINVAL;`。
   NaN/Inf、offset 范围、来源枚举合法性都没有检查：`NaN <= 0.0f` 为假，因此 **NaN 与 Inf
   当时能写进系数**。
4. BASELINE `:131-134` 四个字段（offset/gain/source/calibrated）分四次赋值，**没有任何临界区**，
   而 Sensor 线程同一帧里先后读 mA 和来源（见 C.1）。因此"旧 gain + 新 MEASURED"是可实现的交错。
5. BASELINE 不存在系数版本号，也没有能一次取回整套元组的接口。
6. NOW `APP/current_adc.c:151` `cal_input_ok()`：offset 必须有限且落在 0..3300mV，
   gain 必须有限且落在 `CURRENT_ADC_GAIN_MIN_V_PER_A..MAX`（0.001..100 V/A），来源必须是
   NONE/THEORETICAL/MEASURED 之一。NaN 用 `x == x` 判，不依赖 isfinite 宏是否可用。
   NOW `:168-183` `cal_apply()` 校验后在 `rt_enter_critical()/rt_exit_critical()` 内一次发布
   offset/gain/source/calibrated/**version**；`:42` 新增 `cur_cal_version`。
   NOW `:80-110` `current_adc_read_measurement_cal()`：先取 EMA/raw，再在**同一个**临界区里
   （`:101-106`）快照 offset/gain/source/version，换算在临界区外做。`:77` 老接口
   `current_adc_read_measurement()` 改为转调它并传 `RT_NULL`，语义与返回合同保持。
   NOW `:198-216` `current_adc_get_calibration()` / `current_adc_cal_version()` 短临界区读取。
   披露：gain 下界 0.001 V/A 与上界 100 V/A 是本轮**新增的数值安全收紧**，不是本硬件已验证的
   工作范围；本硬件名义值仍是 0.6 V/A。

## B. project_config.c/.h（配置发布与持久化）

1. BASELINE `:32-33` `cfg_take()/cfg_release()` 包的是 `cfg_lock` 互斥量。
2. BASELINE `:123-131` `cfg_apply_from()` 返回 void，直接把 cfg 的 cal 字段灌进
   `current_adc_set_calibration_ex()`，不做任何自己的校验。
3. BASELINE `project_config_set()`（`:163-180`）：锁内 `cfg = *c` 并取 `applied` 副本，
   **`cfg_release()` 之后**才 `cfg_apply_from(&applied)`。load（`:224`）、default（`:303`）、
   降级路径（`:342`）同形。
   ⇒ 可实现序：线程 A 取得 applied=v1 并解锁；线程 B 完成 set(v2) 且已把运行时系数发到 v2；
   A 随后才执行 `cfg_apply_from(v1)`，**把运行时系数晚到地回写成 v1**，而 cfg 工作集仍是 v2。
   这就是任务 §4 要求审视的"两次并发发布的顺序"。
4. BASELINE `config_in_range()`（`:81`）对 cal 的检查弱于 A.3（同样不拒 NaN/Inf，
   gain 只有正性/上界）。
5. NOW `APP/project_config.c:81-127` `config_in_range()` 复用与 ADC 侧一致的域：
   offset 0..3300mV、gain 0.001..100 V/A 且有限，来源只接受 THEORETICAL/MEASURED
   （**配置结构里 NONE 仍非法**，按原契约；而 A 里的 ADC 运行时 API 保留 NONE 语义，
   两者的差别未改写）。
6. NOW `:129-147` `cfg_apply_from()` 返回 `rt_err_t`，下发前先校验；失败者不改动运行时系数。
7. BASELINE 的四个下发点（set `:177`、load `:224`、`config_default` `:303`、boot `:342`）
   全部在 `cfg_release()` 之后。
   NOW 同名四处（set `:189`、`config_default` `:360`、boot `:398`，以及 load 的
   `:263/:274/:284`）全部移到 `cfg_take()/cfg_release()` 之间；锁内只做 RAM 拷贝与校验，
   Flash I/O 仍在锁外（`project_config_save()` `:234-246` 保持"锁内取一致副本、锁外擦写"）。
   这样"A 的 applied 晚于 B 的 applied"不可能发生：下发与 `cfg` 赋值同属一次持锁。
8. NOW `:200-229` 新增 `project_config_set_calibration(offset_mv, gain_v_per_a, source)`：
   参数先校验；锁内取 cfg 副本→只改 cal 三字段→同锁内下发→成功才把三字段写回工作集；
   不动其它 cfg 字段，不自动 `config_save()`。`:398` 的存储初始化失败路径显式回 THEORETICAL。
9. 未做：Flash 布局、结构体尺寸、版本号、双副本机制一律未改（任务 §6.4）。仪表/接法/拟合
   证据留在报告与标定 manifest，不塞进小配置结构。

## C. sensor_service.c（同一帧的同源要求）

1. BASELINE `:103` 与 `:110` 分两次读：`current_adc_read_measurement(&raw, NULL, &ma)` 取 mA，
   随后 `current_adc_cal_source()` 取来源。两次读之间若 `cal_apply` 生效，帧里的
   `current_ma` 与 `current_cal_source` 就来自**两套系数**。
2. NOW `APP/sensor_service.c:104` 改为
   `current_adc_read_measurement_cal(&raw, RT_NULL, &ma, &cal)`，
   `f->current_ma = ma; f->current_cal_source = cal.source;` —— 两者出自同一快照。
3. `sensor_frame` 布局、日志 schema、`valid_current`/`fresh_current` 含义未改。
   标定更新不清故障、不自动 arm、不重置 EMA。

## D. motor.c/.h 与静止门

1. BASELINE `motor_arm()`/诊断入口用 `motor_lock_take_checked()`（`mot_lock` 的所有权票据）
   取得输出控制权；头文件把该接口标注为恢复用途专用，**没有**把它当配置旁路使用。
2. BASELINE 只有两个软件频率字段能表达"没在跑"，单看它不足以证明输出已关断。
3. NOW `APP/motor.c:2026-2078` 新增窄接口 `motor_calibration_static_begin()/end()`
   （`APP/motor.h:271-272`）：
   - `begin` 先 `motor_lock_take_checked()`；失败直接返回，不留下部分持锁状态。
   - 持锁内复核：未 armed、phase==IDLE、cur/tgt 频率均为 0、停止未锁存、
     Safety 处于 READY、`sp_snapshot_t` 的 owner 为 NONE、无 inhibit/exhausted/
     motor/diag 输出活动、无关断锁存、无 pending 元数据、无未恢复的 PWM/permit 失败、
     `step_pwm_off_confirmed()`、`!step_pwm_output_active()`、`safety_drv_enable_is_low()`。
   - 成功时**保持 `mot_lock` 并置 `mot_cal_guard_held`**，由调用方在提交系数后 `end` 释放；
     因此"判断—解锁—随后更新"的窗口不存在。arm 与诊断取输出需要同一把锁，在此期间被阻塞。
   - 重入返回 `-RT_EBUSY`（`:2033`）。`end()` 幂等，非持有者调用不改状态。
   - 未导出通用回调、不改 owner/phase、不跳过恢复流程。
4. arm/ramp/停止核心语义未改；`stop_priority` 核心未改。

## E. 原理图口径（CLOUD_READ_DOCUMENT 级，非实物实测）

随包 `sources/NS800_RevA_20261019.pdf`（305535 B /
`7b5d5325d4f73f43da916149d1972e95a048d6138ea83f98476f5d2fd5b05a0f`）读到的连接：
`+24V_RAW → R1(图纸 30mΩ) → VM_MOTOR(储能电容 + U9 TMC VM)`；
TPS5430 的 VIN 接在 **R1 之前**的 `+24V_RAW`；
U3 = NSCSA240A1（A1 标称增益 20V/V），IN+/IN− 跨 R1，REF 由 U4 OPA333 缓冲的 3V3 分压给出
（`VREF_1V65` ≈1.65V）；OUT=CURRENT_RAW 经 R4 1kΩ→CURRENT_ADC，C4 10nF 对 GND；
H10：1=CURRENT_ADC、2=GND、3=3V3；主板侧 PH2/ADCA CH15/J1-3。

推论（写进现场候选，不写成板上已证）：在 24V 适配器入口串表读到的是**整板电流**，
含 R1 之前的 TPS5430 支路，不能直接当 R1 支路的独立参考。
另：TMC 的 VREF=1.37V / R110=0.11Ω 是**相电流设定**口径，不得代进母线换算，
也不得把 1.37V 当 ADC 零点。图纸上 U9 的 VM 引脚号是 8，但实物接法须按模块 VM 丝印与
对应焊盘确定，不允许从某个视角数针脚。

## F. 未闭合范围（本轮不声称关闭）

- 板上 ADC 电流实测、真实 DIAG/SG 触发、完整恢复补漏（故障态 `motor_arm` 拒绝的实机证据）、
  示波器与 A0 相关：均保持开放/DEFERRED。
- M6 候选 `66847 B / 9faaca5904ccd3c2618c4a152cc200f4f756ff45aef3e671cf2815356b706649`
  仍 `NOT_BOARD_EXECUTED`；本轮**没有**调用 `run_fault`，也没有为脚本收口重烧镜像。
