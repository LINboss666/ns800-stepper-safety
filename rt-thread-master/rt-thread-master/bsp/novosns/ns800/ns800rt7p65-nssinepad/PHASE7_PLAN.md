# Phase 7 实施计划（四阶段拆分）

> 本文件固定记录 Phase 7 的阶段划分，不随进度变化。
> 进度见 `PHASE7_PROGRESS.md`。
> 铁律：已真机验证的底层（Flash SPI1 / TMC2209 UART2 协议 / ADXL345 SPI3 / SPI3 BSP fix /
> MCU_DRV_ENABLE=PC23/J4-21 / UART1 console / STEP=PA0 EPWM1_A / DIR=PA2）禁止破坏或重新实现。

## Phase 7-A — 底层模块正式化 + 当前状态清理（本轮）

- 统一 subsystem health 约定：UNINIT / OK / DEGRADED / FAILED（`app_health.h`）
- `adxl345.h` 正式 API：init / read_raw / read_mg / get_health / read_int_source；
  关键寄存器写全部检查返回值；初始化失败不假装成功；不重复 attach/configure；
  为 FIFO/INT1 预留接口（不实现完整 FIFO worker）
- `current_adc.h` 正式 API：init / read_raw / read_mv / read_ma / get_health /
  set_calibration（offset/gain/calibration_valid，允许理论默认值但 valid 默认 false）；
  轻量滤波（EMA 状态），不做复杂 DSP
- `tmc2209.h` 正式 API：init / read_register / write_register_confirmed（IFCNT+1 验证，
  禁止 OTP）/ read_sg_result / read_status / get_health
- 保留全部既有 MSH 命令
- 清理过期信息（README/开发进度/旧注释）；PF21 诊断命令编译隔离
  （`#ifdef NS800_ENABLE_LEGACY_PF21_DIAG`，默认关闭，代码证据保留）

## Phase 7-B — Runtime Core

- Sensor Thread（周期采集 IMU + Current，健康感知）
- Motor Service（STEP/DIR 使能管理，封装 step_pwm，仅 READY 条件下放行）
- Safety Thread（最高应用优先级，消费 ESTOP/LIMIT/DIAG 事件，调用
  safety_force_shutdown）
- 事件与队列：GPIO 中断（EXTI）→ rt_event → Safety Thread（ISR 内零阻塞操作）

## Phase 7-C — Diagnosis

- 诊断帧（docx §13 sample_frame）周期生成与工况上下文
- 速度分区阈值基线（低速/中速/高速，NORMAL→LOAD_WARNING→ABNORMAL 判据）
- 多源一致性判据（SG + Current + Vibration）

## Phase 7-D — Blackbox + Integration

- RAM 环形缓冲 + 故障冻结（前 3~5s + 后 0.2~1s）
- Logger 线程批量写 Flash（复用 ns_storage/ns_flash）
- 全链路联调：状态机 → 停机 → 黑匣子 → 复位可读
- 对照实验数据采集准备（方案 docx §14 A/B/C）
