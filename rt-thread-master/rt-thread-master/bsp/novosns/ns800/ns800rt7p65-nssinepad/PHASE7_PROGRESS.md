# Phase 7 进度

> 每轮 Phase 7 工作结束后更新本文件。

## 当前阶段

Phase 7-A（底层模块正式化 + 当前状态清理）— **进行中 → 本轮收尾**

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

## 未完成项（后续阶段）

- Phase 7-B：Sensor Thread / Motor Service / Safety Thread / EXTI→事件链
- Phase 7-C：诊断帧、速度分区阈值、多源一致性判据
- Phase 7-D：黑匣子（RAM ring + ns_storage 落盘）、全链路联调、对照实验准备
- IMU INT1 中断功能验证（EXTI5，线已接）
- SPI2/SPI4 硬件验证（BUG-013 候选 mux 组，仅静态核验）

## 最新 commit

- 分支 `phase7/full-embedded-software`
- `phase7-a: formalize hardware driver APIs` = fa08d9c（代码）
- `phase7-a: sync project docs and retire legacy diagnostics` = 本次文档提交
- main 分支停在 1b3757e（Phase 7-A 从 main 分叉）

## Build 状态

- Keil UV4 -b：本轮 0 error / 0 warning（ARM Compiler 6.24）
- 烧录冒烟：见下

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
