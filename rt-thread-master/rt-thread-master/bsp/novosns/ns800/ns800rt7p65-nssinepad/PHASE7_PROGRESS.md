# Phase 7 进度

> 每轮 Phase 7 工作结束后更新本文件。

## 当前阶段

Phase 7-B（Runtime Core）— **代码完成，真机冒烟待板连接**

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

## 未完成项（后续阶段）

- Phase 7-B 遗留：真机冒烟（板未连 USB，NOT EXECUTED）；ESTOP EXTI 实测
- Phase 7-C：诊断帧、速度分区阈值、多源一致性判据
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
