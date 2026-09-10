# NS800 步进电机异常检测与安全控制系统

> 2026 RT-Thread 嵌入式大赛作品 · 基于 NS800RT7P65D / NSSinePad V1.2 扩展板方案
> 文档语言：中文 · 维护者：LMX + 编码 Agent（ZCode）

## 1. 项目简介

本项目面向**低成本开环 STEP/DIR 步进系统**，在不增加位置编码器的前提下，通过多源信息融合提高异常负载、机械碰撞、卡滞与堵转的检测可靠性，并在故障发生时快速、安全停机，同时保存故障前后的"黑匣子"数据。

单一 StallGuard4 判据容易受速度、电流、电机个体差异和机械共振影响，本项目的核心价值是把四个信息源放到同一时间轴上做工况自适应判断：

| 信息源 | 器件 | 物理意义 | 接口 |
|---|---|---|---|
| 无传感器负载角 | TMC2209 StallGuard4 | 电磁负载 | UART2（单线半双工） |
| 母线电流 | NSCSA240A1 + 30mΩ 分流 | 电气负载/回馈 | ADCA CH15 (PH2) |
| 机械振动 | ADXL345 | 碰撞/卡滞/共振 | SPI1 + INT1 中断 |
| 运动工况 | 软件内部状态 | 速度/加速度/方向 | — |

异常发生时：停止 EPWM STEP 脉冲 → 硬件安全链禁能 TMC2209 ENN → 状态机进入 FAULT_LATCHED（禁止自动重启）→ 后台线程把故障前后数据写入 W25Q64 Flash。

## 2. 硬件平台

- **MCU**：NOVOSENSE NS800RT7P65D（Cortex-M7，1024KB FLASH / 770KB RAM）
- **开发板**：NSSinePad-NS800RT7P65x V1.2（板载 DAPLink 调试器）
- **外接模块**（杜邦线阶段）：W25Q64 Flash 模块（SPI1、CS=PF.12，已验证）；ADXL345 模块（计划）；TMC2209 StepStick（计划）
- **扩展板**：Rev.A 原理图设计阶段（详见《硬件扩展板详细方案_RevA.docx》）

### 引脚映射（冻结，修改需重新做 Pinmux 冲突分析）

| 信号 | 排针 | MCU | RT-Thread Pin | 功能 |
|---|---|---|---|---|
| 调试串口 | — | PA12/PA13 | — | UART1 console（**禁止挪用**） |
| TMC2209 UART | J1-37/40 | PB6/PB7 | — | UART2 TX/RX |
| SPI1 总线 | J2-9/10/11 | PA16/PA17/PA18 | — | MOSI/MISO/SCK（Flash+IMU 共享） |
| Flash CS | J2-12 | PF12 | `PF.12` | 外接 W25Q64 模块（已验证） |
| 板载 Flash 位 | J2-13 | PA19 | `PA.19` | U4 未焊接，CS 保持常高 |
| IMU CS / INT1 | J2-14/15 | PA20/PA21 | `PA.20` / `PA.21` | ADXL345（INT1=EXTI5） |
| TMC_STEP | J4-18 | PA0 | — | EPWM1_A（channel 0） |
| 保留 | J4-17 | PA1 | — | EPWM1_B（**禁止做 DIR**） |
| TMC_DIR | J4-16 | PA2 | `PA.2` | 方向输出 |
| TMC_DIAG | J4-15 | PA3 | `PA.3` | 堵转诊断输入（EXTI3） |
| LIMIT_MIN | J4-19 | PF14 | `PF.14` | 下限位（EXTI14） |
| MCU_DRV_ENABLE | J4-20 | PF21 | `PF.21` | 软件使能许可，**上电默认 LOW** |
| LIMIT_MAX | J4-25 | PF15 | `PF.15` | 上限位（EXTI15） |
| BUZZER | J4-26 | PC19 | `PC.19` | 蜂鸣器 |
| ESTOP_SENSE | J4-40 | PC6 | `PC.6` | 急停检测（EXTI6） |
| RUN/WARN/FAULT LED | J4-38/37/36 | PC8/PC9/PC10 | `PC.8`/`PC.9`/`PC.10` | 状态灯 |
| CURRENT_ADC | J1-3 | PH2 | — | ADCA CH15 |

EXTI 占用：EXTI3/5/6/14/15，无冲突。

## 3. 软件架构

```
applications/
├── main.c              LED 心跳 + Flash CS 上电常高（防总线竞争）
├── project_board.h     冻结引脚表（全部 rt_pin_get() 字符串解析）
├── flash_demo.c        flash_id 命令（板载 U4 位测试，PA19）
├── ns_flash.c/h        W25Q64 存储驱动（探测/读/写/擦，含写保护检查）
├── ns_flash_config.h   Flash 总线/CS/分区配置
├── ns_flash_shell.c    flash_info/unlock/test/verify 等命令
├── ns_storage.c/h      事件记录存储层（异步 Logger 线程）
├── safety_gpio.c/h     安全 GPIO 初始化 + pin_status/safety_status 命令
├── adxl345.c/h         ADXL345 驱动 + imu_id/imu_raw 命令
├── current_adc.c/h     母线电流采样 + current_raw 命令
├── step_pwm.c/h        EPWM1 STEP 输出 + pwm_test 命令（默认不使能）
├── tmc2209.c/h         TMC2209 UART 协议层（CRC 校验）+ tmc_* 命令
└── safety_state.c/h    安全状态机骨架（NORMAL→...→MANUAL_CLEAR）
```

线程规划（优先级：数字越小越高）：Safety(4) > Sensor(7) > Motion(8) > Diagnosis(9) > Logger(18) > Shell(30)。

## 4. 构建与烧录

### 方式一：Keil μVision（图形界面）

打开 `project.uvprojx`（ARM Compiler 6.24），Build / Download。
注意：Agent 在后台修改过工程文件时，μVision 会弹 "has been modified externally, Reload?"，**选"是"**。

### 方式二：命令行（Agent 自动化流程）

```bash
# 编译（0 error 才算通过）
C:\Keil_v5\UV4\UV4.exe -b project.uvprojx -j0 -o build.log

# 烧录（走 CMSIS-DAP，配置在 project.uvoptx）
C:\Keil_v5\UV4\UV4.exe -f project.uvprojx -j0 -o flash.log

# 复位运行（工程未勾选 Reset and Run，烧完芯片是 halted）
pyocd reset -t cortex_m
```

### 方式三：RT-Thread Env（menuconfig / scons）

```bash
set RTT_CC=keil     # rtconfig.py 默认 gcc，本工程必须 keil(armclang)
scons
scons --target=mdk5 # 重新生成 Keil 工程
```

menuconfig 关键配置：`BSP_USING_GPIO/PIN_IRQ、UART1/UART2、ADC、SPI1、EPWM1`。

## 5. 自动化验证流程

编码 Agent 的标准闭环（每次代码修改后执行）：

```
UV4 -b 编译（0 错误）→ UV4 -f 烧录 → pyocd 复位 → COM5 串口读取验证
```

串口：COM5 / 115200 / 8N1。工程未开 "Reset and Run"，烧录后必须复位才有输出。

## 6. Shell 调试命令

| 命令 | 功能 | 状态 |
|---|---|---|
| `flash_id` | 读板载 U4 位（PA19）JEDEC ID | 可用（U4 未焊，预期读 FF） |
| `flash_info` | 模块（PF12）识别信息+分区表 | ✅ 已验证 |
| `flash_unlock` | 清除模块 BP/WPS 写保护位 | ✅ 已验证 |
| `flash_test run` | 破坏性测试（仅 0x7FF000 扇区） | ✅ 已验证 |
| `flash_verify` | 只读保留校验 | ✅ 已验证 |
| `pin_status` / `safety_status` | 安全输入电平/初始化状态 | 代码就绪，待接线验证 |
| `imu_id` / `imu_raw` | ADXL345 DEVID / 三轴原始值 | 代码就绪，待接线验证 |
| `current_raw` | 母线电流 ADC 原始值 | 代码就绪，待标定 |
| `pwm_test <hz>` | STEP 输出测试（默认不使能） | 代码就绪，频率待示波器验收 |
| `tmc_uart_probe` / `tmc_status` | TMC2209 寄存器探测 | 代码就绪，待扩展板 |
| `tmc_crc_test` | CRC 算法自测（纯软件） | ✅ 可验证 |

## 7. 安全红线（任何修改不得违反）

1. UART1 (PA12/13) 永远是 console；TMC 只用 UART2 (PB6/PB7)
2. PA1 是 EPWM1_B 保留脚；DIR 用 PA2，**不得"顺手"改回 GPIO1**
3. 上电/初始化失败后 `MCU_DRV_ENABLE` 必须保持 LOW
4. 两颗 SPI Flash 的 CS（PA19/PF12）以及 IMU CS（PA20）从上电起保持常高，防止总线竞争（见 调试记录.md BUG-001）
5. 新版 RT-Thread SPI 框架的 `rt_spi_configure` 返回 `-RT_EBUSY` 不是错误（详见 调试记录.md BUG-002）
6. ISR 内只置事件/信号量，禁止 SPI/UART/Flash/mdelay/大打印
7. 汇报严格区分"编译通过"与"硬件验证通过"，禁止制造测试结果

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| [开发进度.md](开发进度.md) | 各阶段状态总表 + 变更历史（中文） |
| [调试记录.md](调试记录.md) | 重大 Bug 破案记录（根因分析）+ 错误码速查表 |
| Agent 交接文档 | 引脚冻结、验证状态标记、Agent 约束 |
| RevA 方案 docx | 扩展板硬件设计（原理图前方案） |

## 9. 版本管理

- GitHub：`LINboss666/ns800-stepper-safety`（私有，比赛提交时转公开）
- 每完成一个阶段提交一次，提交信息为中文，注明改动原因与验证结果
- 原 RT-Thread 上游内容沿用 Apache-2.0 协议；本项目自有代码同协议

---
*原 RT-Thread 官方 BSP README（5 行英文简介）已被本文件替换，官方原版见 RT-Thread 仓库 master 分支同名目录。MCU 规格：NS800RT7P65D @最高400MHz, 1024KB FLASH, 770KB RAM。*
