# Phase 7 进度

> 每轮 Phase 7 工作结束后更新本文件。

## 当前阶段

**Phase 7 全部软件阶段（A/B/C/D）完成** — 待 on-target 冒烟 + 硬件联动

## 完成项

- Phase 7-A 全部代码项（本轮完成，见 git log `phase7-a:` 前缀提交）：
  - `app_health.h`：统一 subsystem health 枚举（UNINIT/OK/DEGRADED/FAILED）+ 约定
    （读失败必须经 health/valid 表达，禁止用数值 0 假装有效测量）
  - `adxl345.h` + adxl345.c 重构为正式 API（init 幂等、寄存器写全查返回值、
    health 跟踪、read_int_source、FIFO/INT1 接口预留）；MSH: imu_probe/imu_id/imu_raw 保留
  - `current_adc.h` + current_adc.c 重构（read_raw/mv/ma、set_calibration、
    calibration_valid 默认 false（理论默认值）、EMA 轻量滤波、health）；current_raw 保留
  - `tmc2209.h` + tmc2209.c 增补正式 API（init/read_register/write_register_confirmed
    =IFCNT+1 验证/read_sg_result/read_status/get_health）；底层协议算法未动；
    MSH: tmc_scan/tmc_uart_probe/tmc_status/tmc_regs/tmc_crc_test 全部保留
  - PF21 诊断（pf21_diag/pf21_ab）编译隔离 `#ifdef NS800_ENABLE_LEGACY_PF21_DIAG`，默认关闭（代码证据保留）
- 构建: Keil UV4 -b 0 error / 0 warning（ARM Compiler 6.24）
- 文档清理（第二轮提交 `phase7-a: sync project docs...`）：
  README 架构/命令表、开发进度 Phase 6 状态、project_board.h LIMIT 注释与
  底部旧 PF21 注释、adxl345/current_adc 旧"未接线"注释

## Phase 7-B 完成项（本轮，双提交）

- **phase7-b: add motor and sensor runtime services**
  - `motor.h/.c` Motor Service：init/arm/disarm/set_direction/set_target_hz/start/
    stop/emergency_stop/get_snapshot；状态 IDLE/ACCEL/CRUISE/DECEL/FAULT；
    线性斜坡线程（prio 8, 10ms）；**MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=false →
    motor_arm 一律拒绝（失效安全门）**；pwm_test 在 motor 非 IDLE 时拒绝执行
  - `sensor_service.h/.c` sensor_frame_t + Sensor Thread（prio 7, 100Hz）；
    源失败清 valid 位保留旧值（禁止 0 冒充）；SG_RESULT 10Hz 分频；
    MSH: sensor_status / sensor_snapshot
  - current_adc read_raw 改单次快读+EMA（64 点均值保留给诊断命令）
- **phase7-b: implement safety thread and guarded state machine**
  - `safety_state.h/.c` 正式状态机：白名单转换表 safety_transition()（业务禁止直写）、
    safety_force_shutdown 唯一旁路（停 STEP→DRV_EN LOW→锁存）、启动自检
    （required=GPIO/PWM/DRV_ENABLE_LOW/TMC；degraded=IMU/ADC/Flash）、
    fault_reset 实测源+人工命令、EVT_SOFT_FAULT、FAULT_SOFT
  - `safety_thread.h/.c` Safety Thread（prio 4）rt_event_recv 消费
    ESTOP/LIMIT_MIN/LIMIT_MAX/TMC_DIAG/MULTI/SOFT → 分类 force_shutdown；
    estop_isr 只 post 事件（§14.3 纪律）；EXTI 注册运行门默认关闭
    （safety_irq_attach 显式开启，防悬空 interrupt storm）
  - `supervisor.h/.c` system_status + runtime_selftest（软件级：非法转换拒绝/
    arm 门禁/故障停机链/事件处理/sensor valid，测毕恢复 READY）
  - safety_events_ready() 启动门（Safety Thread 等状态机事件系统就绪）

## Phase 7-C 完成项（本轮，双提交）

- **phase7-c: implement multi-source diagnosis engine**（9334d2b）
  - `diagnosis.h/.c`：prio 9 线程 100Hz 消费最新 sensor_frame；
    特征=SG/电流 EMA 滤波+Δ、振动滑窗 RMS/峰值、速度分带、加减速相位；
    判定=NORMAL/LOAD_WARNING/IMPACT/OVERLOAD/STALL_SUSPECT/STALL_CONFIRMED/
    SENSOR_FAULT
  - 反误报纪律：单 sample 永不 severe（persistence）；SG 阈值分带；sensor
    missing 冻结特征只计 bad（禁止当 0）；SG/电流判据仅 CRUISE；
    CONFIRMED 需 SG+电流双源；恢复 hysteresis
  - 模式 MONITOR_ONLY 默认；ACTIVE_PROTECTION 需电流已标定（标定门禁），
    severe 边沿 → EVT_MULTI_FAULT 交 Safety（引擎不碰 Flash）
  - diag_selftest：7 场景确定性合成输入（软件级，非硬件验证）
- **phase7-c: add persistent calibration and threshold config**
  - `project_config.h/.c`：magic/version/范围三重校验；非法→安全默认+DEGRADED；
    标定合法自动下发 current_adc；ns_params 双副本持久化；
    MSH: config_show/config_default/config_save/config_load
  - diagnosis 阈值改为每帧从 project_config 同步（diagnosis_init/selftest 也同步）

## Phase 7-D 完成项（本轮，双提交）

- **phase7-d: add fault blackbox and system supervisor**
  - `blackbox.h/.c`：100Hz 静态 RAM 环（pre 200 帧=2s + post 100 帧=1s，
    ≈10.8KB BSS 零 malloc）；触发 O(1)（Safety 只置标志，绝不等待 Flash）；
    worker（prio 18）逐帧 ns_log_submit + 队列满 flush 重试（不暴力打爆队列）；
    valid 位映射 NS_VALID_*；MSH blackbox_status/dump/clear/selftest
  - main.c 升级为启动入口（七阶段编排 + 状态打印 + 板载心跳）；
    supervisor 增加 UI 管理线程（prio 20，LED/蜂鸣器单一写者，
    `UI_OUTPUT_ENABLED=false` 极性未确认安全全灭[hardware pending]）
  - safety_force_shutdown 挂接 blackbox_trigger（O(1)）
  - project_config 默认 pre=2s/post=1s
- **整体软件 review**（结论写下方 Software Review）
- **phase7-d: finalize embedded software and documentation**：四文档同步 +
  三级验证标注体系（BOARD-TESTED / SOFTWARE-VERIFIED / HARDWARE-PENDING）

## Software Review 结论（2026-09-13 静态审查）

- 线程优先级：Safety(4)>Sensor(7)>Motor(8)>Diagnosis(9)>Blackbox(18)>UI(20)>Shell(30) ✅ 符合方案
- 栈尺寸：safety 1024(静态)/sensor 1024/motor 768/diag 1024/bb 1024/ui 512 ✅
- 阻塞路径：Safety 线程零 Flash 零 SPI/UART ✅；Flash 重 IO 全在 bb worker ✅
- 互斥/事件：mot_lock 单一互斥无嵌套；s_frame 临界区拷贝；事件 ISR 安全 ✅
- tick 回绕：deadline 比较全部使用无符号回绕安全写法 ✅
- 计数溢出：persistence 计数带衰减钳制；seq 允许回绕 ✅
- NULL/错误路径：全部读 API 检查返回；health 跟踪失败路径 ✅
- ⚠ 已记录风险：INIT_APP 执行顺序依赖链接顺序（当前恰好满足
  safety_gpio→safety_state→supervisor）；sensor 线程在 TMC 失联时每 10 帧
  有 100ms 超时占用（降速不阻塞）；ss_state 转换无锁（当前多写者场景仅
  Safety Thread + MSH，实际冲突面小，Phase D 联调观察项）

## 未完成项（后续阶段）

- Phase 7 全阶段遗留：on-target 冒烟（板未连 USB，NOT EXECUTED）；
  ESTOP EXTI 实测；diag/blackbox selftest 上板执行
- 硬件联动：电机+ADC 联合测试（四级方案已备）、使能链验收、INT1、
  STEP 示波器验收、ENN 浮空实验、标定流程
- Phase 7-D：黑匣子（RAM ring + ns_storage 落盘）、全链路联调、对照实验准备
- IMU INT1 中断功能验证（EXTI5，线已接）
- SPI2/SPI4 硬件验证（BUG-013 候选 mux 组，仅静态核验）

## 最新 commit

- 分支 `phase7/full-embedded-software`
- main 分支停在 1b3757e
- phase7/full-embedded-software 分支：
  - Phase 7-A：fa08d9c（API）+ a33b64b（文档）
  - Phase 7-B：8548ccc（motor+sensor）+ c90792a（safety thread+状态机+supervisor，HEAD）

## Build 状态

- Keil UV4 -b：0 error / 0 warning（ARM Compiler 6.24，Phase 7-A 与 7-B 均实跑）
- 烧录冒烟：Phase 7-B NOT EXECUTED（开发板未连 USB）

## Hardware pending

- 电机+ADC 联合测试（四级方案已备，待用户下发；前置 4 项确认见 待办事项.md）
- J4-21（PC.23）万用表终验（≈0V）
- 安全输入（ESTOP 常闭+上拉 / LIMIT GND 跳线占位）接线与验证
- IMU INT1 中断验证
- ENN 浮空行为实验（先拔 VM 再拔 ENN，看 IOIN bit0）
- STEP 频率示波器验收（无仪器，替代=TMC 回读/低速信任法）

## 真机验证事实库（勿重测、勿降级）

- Flash：spi1，JEDEC EF 40 17，unlock/test/verify PASS
- TMC2209：uart2，READ FOUND（IOIN=0x21000041 VERSION=0x21）+ WRITE IFCNT+1 ALIVE；
  4 帧应答 CRC 独立核算一致；上电基线 GCONF=0x101/CHOPCONF=0x15010053
- ADXL345：spi3（BSP fix 后），DEVID=0xE5 双读一致，静置合成 ≈1g
- 安全停机链：safety_force_shutdown → FAULT_LATCHED → 实测故障源 → MANUAL_CLEAR（ss_test）
