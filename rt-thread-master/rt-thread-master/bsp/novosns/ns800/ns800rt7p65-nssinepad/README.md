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
| 机械振动 | ADXL345 | 碰撞/卡滞/共振 | **SPI3**（硬件 SPI3，独占；INT1 未启用） |
| 运动工况 | 软件内部状态 | 速度/加速度/方向 | — |

异常发生时：停止 EPWM STEP 脉冲 + MCU_DRV_ENABLE 写 LOW 并**回读确认** →
状态机进入 FAULT_LATCHED（禁止自动重启）→ 后台线程把故障前后数据写入 W25Q64 Flash。
（注：扩展板 Rev A 缺 MCU_DRV_ENABLE 网络，当前 ENN 直挂 3.3V 恒禁用，见 §7 与 BUG-009。）

## 2. 硬件平台

- **MCU**：NOVOSENSE NS800RT7P65D（Cortex-M7，1024KB FLASH / 770KB RAM）
- **开发板**：NSSinePad-NS800RT7P65x V1.2（板载 DAPLink 调试器）
- **外接模块**（杜邦线阶段）：W25Q64 Flash 模块（**SPI1**、CS=PF.12，已验证）；ADXL345 模块（**SPI3**，已验证）；TMC2209 StepStick（UART2，已验证）
- **扩展板**：Rev.A 原理图设计阶段（详见《硬件扩展板详细方案_RevA.docx》）；⚠ Rev A **漏画 MCU_DRV_ENABLE 网络**，故软件使能许可当前不作用于 ENN，RevB 待补

### 引脚映射（冻结，修改需重新做 Pinmux 冲突分析）

| 信号 | 排针 | MCU | RT-Thread Pin | 功能 |
|---|---|---|---|---|
| 调试串口 | — | PA12/PA13 | — | UART1 console（**禁止挪用**） |
| TMC2209 UART | J1-37/40 | PB6/PB7 | — | UART2 TX/RX |
| SPI1 总线 | J2-9/10/11 | PA16/PA17/PA18 | — | MOSI/MISO/SCK（**Flash 独占**，BUG-001 共总线竞争已修） |
| SPI3 总线 | J3-34/35/36 | GPIO_52/50/51 @ALT6 | — | SCK/MOSI/MISO（**ADXL345 独占**，BSP 引脚表已修：BUG-012/013） |
| Flash CS | J2-12 | PF12 | `PF.12` | 外接 W25Q64 模块（已验证） |
| 板载 Flash 位 | J2-13 | PA19 | — | U4 未焊接；BUG-009 迁移后此脚闲置 |
| IMU CS / INT1 | J2-14/15 | PA20/PA21 | `PA.20` / `PA.21` | ADXL345（INT1=EXTI5） |
| TMC_STEP | J4-18 | PA0 | — | EPWM1_A（channel 0） |
| 保留 | J4-17 | PA1 | — | EPWM1_B（**禁止做 DIR**） |
| TMC_DIR | J4-16 | PA2 | `PA.2` | 方向输出 |
| TMC_DIAG | J4-15 | PA3 | `PA.3` | 堵转诊断输入（EXTI3） |
| LIMIT_MIN | J4-19 | PF14 | `PF.14` | 下限位（EXTI14） |
| MCU_DRV_ENABLE | J4-21 | PC23 | `PC.23` | 软件使能许可，**上电默认 LOW**（⚠ 原 PF.21/J4-20 焊盘缺陷弃用，见 调试记录 BUG-009） |
| LIMIT_MAX | J4-25 | PF15 | `PF.15` | 上限位（EXTI15） |
| BUZZER | J4-26 | PC19 | `PC.19` | 蜂鸣器 |
| ESTOP_SENSE | J4-40 | PC6 | `PC.6` | 急停检测（EXTI6） |
| RUN/WARN/FAULT LED | J4-38/37/36 | PC8/PC9/PC10 | `PC.8`/`PC.9`/`PC.10` | 状态灯 |
| CURRENT_ADC | J1-3 | PH2 | — | ADCA CH15 |

EXTI 占用：EXTI3/5/6/14/15，无冲突。

## 3. 软件架构

```
applications/
├── main.c              启动入口: 最早期 CS 常高 → supervisor_boot() → 状态打印 → 板载 LED 心跳
├── project_board.h     冻结引脚表（全部 rt_pin_get() 字符串解析）
├── supervisor.c/h      业务层 bootstrap(11 stage, 返回 rt_err_t) + system_status + UI 管理线程
├── app_health.h        统一子系统健康约定(UNINIT/OK/DEGRADED/FAILED)
│  ── 驱动/服务层 (Phase 7-A) ──
├── ns_flash.c/h        W25Q64 存储驱动（探测/读/写/擦，含写保护检查）
├── ns_flash_config.h   Flash 总线/CS/分区配置
├── ns_flash_shell.c    flash_info/unlock/test/verify 等命令
├── ns_storage.c/h      事件记录存储层（params 双副本+CRC；log 异步 worker）
├── safety_gpio.c/h     安全 GPIO 上电态 + MCU_DRV_ENABLE「写+回读」唯一入口 + pin_status/safety_status
├── adxl345.c/h         ADXL345 驱动(硬件 spi3 独占)正式 API + imu_probe/imu_id/imu_raw
├── current_adc.c/h     母线电流采样正式 API(标定来源 NONE/THEORETICAL/MEASURED + EMA) + current_raw
├── tmc2209.c/h         TMC2209 UART 协议层(已真机验证)正式 API + tmc_* 命令
├── step_pwm.c/h        EPWM1 STEP 输出 + pwm_test 诊断命令（须过 Motor 借用门）
│  ── 安全/运行时层 (Phase 7-B) ──
├── safety_state.c/h    安全状态机(白名单转换 + ss_lock) + force_shutdown 唯一停机路径 + 启动自检
├── safety_thread.c/h   Safety 线程(prio 4) + 四路 EXTI 事务化注册 + 人工 IRQ 门 MSH
├── motor.c/h           Motor Service(斜坡/四门禁/回读/所有权) + motor_* MSH
├── sensor_service.c/h  统一 100Hz 采样帧(seq/valid/fresh 位) + sensor_status/snapshot/selftest
│  ── 诊断/记录层 (Phase 7-C/D) ──
├── diagnosis.c/h       多源融合判据引擎(同帧去重在引擎内) + diag_status/mode/selftest
├── project_config.c/h  阈值+标定来源持久化(全量互斥, Flash IO 在锁外) + config_* 命令
└── blackbox.c/h        故障黑匣子(pre/post 捕获 + 首故障优先 + 有界重试落盘) + blackbox_*
```

（旧版此列表里的 `flash_demo.c` 已随板载 U4 位测试移除而删除。）

线程与优先级（数字越小越高，实测以 `.config`/代码为准）：
Safety(4) > Sensor(7) > Motor(8) > Diagnosis(9) > Blackbox worker(18) >
Flash logger worker(RT_THREAD_PRIORITY_MAX-3) > UI(20) ≈ tshell(20)。

启动顺序由 `supervisor_boot()` 单点决定（见 PHASE7_PROGRESS 的 Fix A 节）：
安全 GPIO → 状态机/事件 → Safety 线程 → 自检所需硬件 → storage（慢）→ config →
motor+sensor → diagnosis → blackbox → UI → 启动自检 + READY 决策。

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
| ~~`flash_id`~~ | 板载 U4 测试命令已随迁移移除（U4 未焊） | — |
| `flash_info` | 模块（PF12）识别信息+分区表 | ✅ 已验证 |
| `flash_unlock` | 清除模块 BP/WPS 写保护位 | ✅ 已验证 |
| `flash_test run` | 破坏性测试（仅 0x7FF000 扇区） | ✅ 已验证 |
| `flash_verify` | 只读保留校验 | ✅ 已验证 |
| `pin_status` / `safety_status` | 安全输入电平/初始化状态（pin_status 对 DRV_ENABLE 做写+回读并标注 READBACK FAIL） | 代码就绪，待接线验证 |
| `safety_irq_status` | 查看 EXTI 门/极性声明/四路 raw 电平/protection_ready | 🖥 软件就绪（Fix A 新增） |
| `safety_irq_enable` / `safety_irq_disable` | 人工开/关四路 EXTI 门；**未做极性 CONFIRM 一律拒绝** | 🖥 软件就绪（Fix A 新增，禁止在接线未实测前开） |
| `safety_polarity_confirm CONFIRM` | 操作员声明极性已实测（必须带字面量 CONFIRM；软件不做推断） | 🖥 软件就绪（硬件仍 HARDWARE-PENDING） |
| `imu_probe` / `imu_id` / `imu_raw` | SPI3 探测 / DEVID / 三轴 mg 值 | ✅ 真机验证 (DEVID=0xE5, 合成≈1g) |
| `current_raw` | 母线电流突发统计 + mV/mA | ✅ raw/min/max 链路曾真机验证；⚠ mV/mA 列在 Fix B 之前打印的是**未初始化值**，历史数字不可信 → 该列现为 🖥 待上板复核 |
| `pwm_test <hz>` / `pwm_test stop` | STEP 输出诊断（须过 Motor 借用门；stop 永远允许） | 代码就绪，频率待示波器验收 |
| `motor_status` | 电机快照 + 四门禁失败位掩码 | 🖥 软件就绪（Fix A 新增） |
| `motor_arm` / `motor_disarm` | 申请/解除使能（**当前必返回 REFUSED，属预期**） | 🖥 软件就绪（Fix A 新增） |
| `motor_dir <0\|1>` / `motor_target <hz>` / `motor_start` / `motor_stop` | 方向（仅静止）/目标步频/起动/受控停止 | 🖥 软件就绪（Fix A 新增，全部只走正式 API） |
| `tmc_scan` | 只读扫描地址0..3找 TMC2209 | ✅ 真机 FOUND (addr0) |
| `tmc_uart_probe` / `tmc_status` | IFCNT写握手 / GSTAT+版本 | ✅ 真机验证 (IFCNT+1) |
| `tmc_regs` | 关键寄存器只读快照 | ✅ 真机验证 |
| `tmc_crc_test` | CRC 算法自测（官方向量） | ✅ 真机 PASS |
| `runtime_selftest` | 软件级运行自检：bootstrap 完成度 / 非法转换 / **四门掩码（含第 4 门参与证明）** / 停机链 / valid 位 / 收尾再确认 arm 仍被拒 | 🖥 软件就绪, 上板待执行 |
| `system_selftest` / `system_status` | 安全自检重跑 / 全子系统状态（含 Boot 行、gate mask、DRV_ENABLE 实测） | 🖥 软件就绪 |
| `diag_status` / `diag_mode` / `diag_selftest` | 诊断引擎状态/模式/合成注入自检（Fix B 重写 3b 与 4c 两条用例） | 🖥 软件就绪(合成验证) |
| `config_show/default/save/load` | 运行时配置查看/恢复/持久化/加载（Fix B：标定来源可持久可还原） | 🖥 软件就绪 |
| `blackbox_status/dump/clear/selftest` | 黑匣子状态/回读/清除/落盘自检（Fix C：post 窗口此前收不满，自检此前必挂） | 🖥 软件就绪(写真实 Flash) |

> 🖥 = 编译与静态检查通过，**未在开发板执行**；上表所有 🖥 项统一为
> HARDWARE-PENDING — deferred to evening on-target validation。

## 7. 安全红线（任何修改不得违反）

1. UART1 (PA12/13) 永远是 console；TMC 只用 UART2 (PB6/PB7)
2. PA1 是 EPWM1_B 保留脚；DIR 用 PA2，**不得"顺手"改回 GPIO1**
3. 上电/初始化失败后 `MCU_DRV_ENABLE` 必须保持 LOW；且该脚的写**必须经
   `safety_drv_enable_write()` 做写后回读确认**（`rt_pin_write` 无返回值，
   裸写不能证明焊盘真的到达目标电平 —— BUG-009 就是焊盘被顶高）。回读只证明
   MCU 焊盘电平，**不替代 ENN 整链硬件验收**
4. SPI CS（PF12 Flash / PA20 IMU）从上电起保持常高，防止总线竞争（见 调试记录.md BUG-001）
5. ADXL345 固定走 **SPI3**（BSP 引脚表已修真机验证），**禁止改回 SPI1**；
   旧"rt_spi_configure 返回 -RT_EBUSY 不是错误"的放行是 SPI1 共总线时代的
   workaround，ADXL 独占 SPI3 后已按 P1-12 退役（见 `adxl345.c` 注释），不要再照搬
6. ISR 内只置事件/信号量，禁止 SPI/UART/Flash/mdelay/大打印
7. 停机只有一条路径：`safety_force_shutdown()`（内部经 `motor_emergency_stop()`
   停 STEP + 使能脚写+回读）。业务/事件处理代码**不得自行拼停机序列或只改状态**
8. EPWM1 ch0 的生产 owner 是 Motor Service；`pwm_test` 只是借用，必须过
   `motor_pwm_grant_to_diag()`，且占用期间 Motor 拒绝 arm/start
9. 开环步进**运行中禁止翻 DIR**（等同注入堵转），`motor_set_direction` 仅静止放行
10. `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED`、`safety_polarity_confirm`、
    `safety_irq_enable`、`diag_mode active` 都是**人工/硬件门禁**，
    禁止为了让自检通过而临时打开
11. 汇报严格区分 BOARD-TESTED / SOFTWARE-VERIFIED / STATIC-VERIFIED /
    HARDWARE-PENDING；编译通过 ≠ 真机验证，禁止制造测试结果

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| [待办事项.md](待办事项.md) | **唯一权威待办清单**（怕漏东西看这里） |
| [开发进度.md](开发进度.md) | 各阶段状态总表 + 变更历史（中文） |
| [调试记录.md](调试记录.md) | 重大 Bug 破案记录（根因分析）+ 错误码速查表 |
| Agent 交接文档 | 引脚冻结、验证状态标记、Agent 约束 |
| RevA 方案 docx | 扩展板硬件设计（原理图前方案） |

## 9. 验证状态标注体系

| 标注 | 含义 |
|---|---|
| **BOARD-TESTED** | 已在真实硬件上验证并留有记录 |
| **STATIC-VERIFIED** | 只读源码/diff/map 得出的结论（逻辑与调用关系成立），未运行 |
| **SOFTWARE-VERIFIED** | 软件逻辑已验证(全量 clean build + 自检设计), 但尚未在板上执行 |
| **HARDWARE-PENDING** | 依赖硬件到位/标定/验收的事项 |

## 10. 版本管理

- GitHub：`LINboss666/ns800-stepper-safety`（私有，比赛提交时转公开）
- 每完成一个阶段提交一次，提交信息为中文，注明改动原因与验证结果
- 原 RT-Thread 上游内容沿用 Apache-2.0 协议；本项目自有代码同协议

---
*原 RT-Thread 官方 BSP README（5 行英文简介）已被本文件替换，官方原版见 RT-Thread 仓库 master 分支同名目录。MCU 规格：NS800RT7P65D @最高400MHz, 1024KB FLASH, 770KB RAM。*
