# NS800 目标机测量与集成验收准备计划

任务 ID：`phase8-target-measurement-prep`；日期：2026-10-25（Asia/Shanghai）。
性质：**离线准备**。本文所有测试条目状态一律为 `PLANNED / NOT EXECUTED`；本轮零构建、零主机链重跑、
零硬件动作、零插桩实现。

修订轮 `phase8-target-measurement-prep-r1`（同日，基线 `b27c0c49118c52219383e36a032069d61fa43cfc`）
按独立复审 `PREP_REVIEW_CHANGES_REQUIRED` 落实 R1（PendSV 优先级已有显式汇编设置）、R2（计数器可用性
与时钟可校准分开，SysTick 路径保守降级）、R3（`clock_basis` 分层与跨时间基关联条件）与 F1（尾随空格）；
修订口径见 §2.5、§4、§6 判据 2、§8、§9 与交接 `review_resolution`。**本节不改变任何测试条目状态。**

**本文不是什么**：不是可执行验收单（`reports/plans/phase8-safety-chain-acceptance-draft.md` 自身仍是
DRAFT，见其 `:3`）；不是停机时延承诺；不是硬件授权。文内任何"入口/触发/宏组合"只是**未来需要
单独授权的具体动作清单**。

## 1. 身份与继承

| 角色 | 完整 SHA |
|---|---|
| 本准备轮 document_base | `f919a5c05fbee5457c768d5c1a4ba46c41a9dbfc` |
| accepted_software_commit / tested_commit | `bcdec631266239008734271ba75beab435bf30eb` |
| CP6 implementation_commit | `c84457b3e4f8960d14a750e87553b33c845d71a2` |
| main 冻结 | `1b3757eec550802a3973e66790af422d81d3d373` |
| phase7/full-embedded-software 冻结 | `6d3fbc559d45673d7550eb73dec5a4ade5bb34df` |
| 标签对象 / 解引用固件 | `097935fec5a6f2467261ab1ad449ff0b1d398250` / `e40a9b95ef709e6a80444f9f3949dfdd70ba96ba` |

开工只读核对：本地 `HEAD` = 远端 `phase8/tmc-drive-config` = `f919a5c`，工作树 clean，无他人新提交；
`bcdec631..f919a5c` = 8 份文档 +478/−16（生产、夹具、shim、runner、Keil 工程输入未变）。未
reset/rebase/force/merge。

继承的结论（不改写、不升级）：

| 项目 | 结论 / 使用方式 | 来源等级 |
|---|---|---|
| S2 软件与主机合同 | `SOFTWARE_REVIEW_GO`（接受 `bcdec631`）：S1 50/65、S2 target 23/host_ovr 79、五类反向 META-OK、叶子四组 10/15/7/6 由复审端在 Linux/GCC 13.3 独立复跑；EPWM 另有 20006 次数值检查（0 失败、prepare 零寄存器写）与 UBSan/float-cast-overflow 无诊断 | 云端实际执行 |
| V1/V2、I/O、负例、元负例、Keil | V1 25/V2 22、I/O 27/29/40/42、负例 13/13、M1–M5、Keil 0/0（`Code=173762 RO=76806 RW=2188 ZI=83784`） | **AGENT_REPORTED**（本地日志，云端未重建，勿写成云端执行） |
| 板上固件 | `USER_REPORTED_E40`；未读回 Flash 身份/哈希；旧 e40 命令语义不得移用到新源码（例如旧 `tmc_uart_probe` 可能写回 GCONF） | 用户断言 |
| 静态电气 / 现有 SW1 功能 | 已按 `USER_CONFIRMED` + 既有数字证据收口（RISK-04 的 4-a 子项）；本轮**不**把五点电压重测当准备前置 | 用户确认 + 数字读回 |
| 现场最后记录 | 2026-10-22：SW1 拨到阻断位并保持、许可 LOW、`armed=0`、电机物理未接、模块在位、供电正常、人在场（`reports/agent_handoffs/phase8-sw1-hardware-cut-20261022.md:21/:50/:83/:90`） | 历史记录（**不**当实时现场确认） |
| 仪器 | 唯一型号记录 = UT39A+ 万用表（`reports/agent_handoffs/phase8-enn-readonly-retry-20261022.md:135`，"只能给静态电压/电阻/通断"）；`开发进度.md:104`（2026-09-11 硬件在位表）记"无示波器/逻辑分析仪" ⇒ `BLOCKED_INSTRUMENT` | 历史；**当前是否可用未知** |
| 既有 SW1 采集器 | `sw1_cut.ps1` 的 T-01/T-02 仍 `HOLD`；本轮未运行、未修复、未新建采集平台 | 既有登记 |

开放风险（软件 GO 不替代）：`B-1` 门内窗口实际时长与 RX/EXTI 丢失容忍、`RISK-03/03a` 检测/调度/
最坏响应、`BUG-008` STEP 物理波形、物理 `RISK-04-b`、`M-1` 其它执行者、`M-2` 同 EPWM 实例其它通道、
`M-3` 写完成与引脚响应时刻。

四宏保持 `RT_FALSE`：`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED`（motor.h:69）、
`STEP_DIAG_ISOLATED_ENV_CONFIRMED`（motor.h:83）、`TMC_DRIVE_CONFIG_HW_VALIDATED`（tmc2209.h:100）、
`SAFETY_LIMIT_INPUTS_ENABLED`（project_board.h:51；用户当前不使用限位，功能保留，**不列为默认必开项**）。
`host_ovr`（`-D…=RT_TRUE`）只属于主机编译，**不是**目标机门禁解除方式。

## 2. 本地事实清单

图例：等级 `SRC`=本仓源码/工程可核对；`SDK-LOCAL`=本地 SDK/CMSIS 文件（仅本地存在，见附录 A）；
`DOC`=仓内既有记录；`USER`=用户断言；`TBD`=本地不能解决。
路径前缀 `BSP/` = `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/`，
`LIB/` = `rt-thread-master/rt-thread-master/bsp/novosns/ns800/libraries/`，
`KERNEL/` = `rt-thread-master/rt-thread-master/`（内核与 `libcpu/`）。

### 2.1 时钟链（影响所有"计数→时间"换算，必须先解决）

| # | 结论 | 出处 | 等级 | 已知 / 未知 | 影响的测试 |
|---|---|---|---|---|---|
| F-01 | 生效时钟配置由编译器宏给出：`SYSCLK_USE_PLL`、`SYSCLK_SOURCE_USE_HXTL`、`PLLCLK_SOURCE_USE_HXTL`、`DUAL_CORE_ENABLE=0`、`__CLK_TCK=RT_TICK_PER_SECOND` | `BSP/project.uvprojx:341`；scons 同值 `BSP/board/SConscript:14-15` | SRC | 已知 | T00/T06 |
| F-02 | **HXTL 标称值在本地自相矛盾**：寄存器配置选 `RCC_HXTL_24M_AGC_DISABLE`（该枚举注释 24 MHz），而频率常量是 `HXTL_FREQ_VALUE (20000000UL)`，SDK 计算全部用它 | `BSP/board/board.h:88-92`；`SDK-StdDriver/Inc/rcc.h:1525`；`SDK-Device/Inc/NS800RTxxxx.h:53`；`SDK-StdDriver/Src/rcc.c:42` | SDK-LOCAL | 已知=计算按 20 MHz ⇒ `clock_basis=SOURCE_ASSUMED`；**未知=晶振真实标称**（只能由 `EXTERNAL_VERIFIED` 升级）。两档并列只登记"来源待确认"，**不得**据此断言晶振实为 24 MHz、板子实际超频或配置已错 | 一切 µs/ms 换算、T02/T04/T06 |
| F-03 | 按 F-02（20 MHz）的推导链：PLL `PLLM_40 / PLLN_DIV1 / PLLP_DIV2` ⇒ SYSCLK=400 MHz；CPUCLK=SYSCLK/1；**HCLK=CPUCLK/2=200 MHz**；PCLK1=PCLK2=HCLK/1=200 MHz；PCLK5=HCLK/4=50 MHz | `board.h:118-121`（PLL）、`:175`（CPU 分频）、`:174`（HCLK=`RCC_HCLKSOURCE_CPUCLK_2`，且 `rcc.c:752` 在 configPll 内再次强制）、`:172-173`（APB1/2=DIV1）、`:171`（APB5=DIV4）；公式 `rcc.c:91`；写入 `rcc.c:403-407`；切源 `board.c:161` | SRC + SDK-LOCAL | 已知=代码链（整条链的输入仍是 F-02 的 `SOURCE_ASSUMED` 常量）；**未知=目标机实际时钟**。运行期读回只到 `CONFIG_READBACK` 级（见 §2.5） | T00/T06/T09 |
| F-04 | `SystemCoreClock` 由 SDK 在启动后按运行期读数刷新（= CPUCLK），调用点在板级初始化里 | `SDK-Device/Src/system_NS800RT7xxx.c:33-35`（默认 `SYSTEM_CLOCK=HXTL_FREQ_VALUE`）、`:85-92`（`SystemCoreClockUpdate`）；调用 `LIB/HAL_Drivers/drv_common.c:43`（在 `rt_hw_systick_init`，被 `:144 rt_hw_board_init` 调用） | SRC | 已知=刷新机制；**未知=上机读到的值**。注意：`SystemCoreClock`、`RCC_get*`、TBCTL/PLL 位之间的互相读回**同源**（都从 F-02 的 `HXTL_FREQ_VALUE` 算出），一致只证明软件报告链自洽，构成 `CONFIG_READBACK`，**不构成**外部晶振或实际核心频率的独立证明；MCO 只有在确认输出路由/分频/测点并用独立时基测量后才是 `EXTERNAL_VERIFIED`（引脚复用与接线是单列未来授权动作） | T00 |
| F-05 | 工程内可见的 `CLOCK(12000000)` 是 IDE 调试器/仿真字段，与 F-03 的计算链无关 | `BSP/project.uvprojx:21` | SRC | 已知=不得引用它做换算 | — |
| F-06 | `RCC_getPclk1Frequency()` 单位是 **Hz**，值 = `HCLK >> APB1DIV`；EPWM 模块时钟 = `PCLK1 >> EPWMCLKDIV(本工程 DIV_1)` ⇒ 按 F-03 为 200 MHz | `rcc.c:163`（`@return … in Hz`）、`:165-167`、`:199-201`；`board.h:179` | SDK-LOCAL | 已知=单位与路径（该函数是**按寄存器位算出的软件值** ⇒ `CONFIG_READBACK`，仍受 F-02 前提约束）；**未知=上机 TBCLK 实际频率** | T06（波形周期核对） |
| F-07 | `RCC_getEpwmFrequency()` / `EPWMClock` 在 HAL 与 applications 中**无任何消费者**；频率→TBPRD 的换算只发生在 `ns800_epwm_prepare()`（CP6 已按 ns/计数/分频实现，分频取驱动配置值） | grep 无命中；`LIB/HAL_Drivers/drivers/drv_epwm_leaf.inc`（prepare）；表 `drv_epwm.c:172-174`（EPWM1=UP_DOWN, `EPWM_CLOCK_DIVIDER_2`, `EPWM_HSCLOCK_DIVIDER_1`） | SRC | 已知；**未知=上机 TBCLK 实测** | T06 |
| F-08 | 存在**两处** TBCLK 分频写入来源：冷启动 `ns800_epwm_hw_init` 用 `CLKDIV_1/HSDIV_2`，驱动配置表用 `CLKDIV_2/HSDIV_1`；后者在配置提交时覆盖前者 ⇒ 测量换算必须按**当时生效值**，并回读 TBCTL 的 `CLKDIV/HSPCLKDIV` 位核对 | `drv_epwm.c:261-289`（`:263`）、`:172-174`；写位 `SDK epwm.h:1569-1572`（`EPWM_setClockPrescaler`） | SRC + SDK-LOCAL | 已知 | T06/T07 |
| F-09 | APB5 **使能域**挂 UART1–4（=50 MHz 域），但 UART 驱动按 **PCLK2=200 MHz** 取功能时钟算波特率 ⇒ 两者是"外设时钟使能所在域"与"SCI 功能时钟来源"两件事，本地不能判定实际功能时钟 | `board.c:252-256`（`RCC_enableApb5PeripheralClock(UART1..4EN)`）；`SDK-StdDriver/Src/uart.c:44-45`（`/* UART clock source = PCLK2 clock */ … RCC_getPclk2Frequency()`） | SRC + SDK-LOCAL | **未知**（须厂商确认或实测位时间）。此处只登记"来源待确认"，**不得**推成"波特率必错 4 倍"或任何具体倍数 | T04（帧时长与 2 ms/256 B flush 上限的物理含义） |
| F-10 | SPI 同类：外设使能在 APB5，速率计算取 PCLK2；ADC 输入时钟身份 SDK 未注明（工程用 `ADC_CLK_DIV_4`） | `SDK-StdDriver/Src/spi.c:49`、`board.c:254`；`drv_adc.c:150/180`、`adc.h:1099` | SRC + SDK-LOCAL | 已知=配置事实（使能域与功能来源分开记）；ADC 时钟源**未知** | T11（电流采样时间基准） |
| F-11 | SysTick 用**处理器时钟**（不是 AHB/8），`LOAD = SystemCoreClock/1000` ⇒ tick 1 ms；I/D cache 在板级无条件使能。**PendSV 优先级是显式设置的**（不是"工程未设置"）：工程把 `KERNEL/libcpu/arm/cortex-m7/context_rvds.S` 列进编译清单（`project.uvprojx:503/:505`），该文件 `:22` 定义 `NVIC_SYSPRI2 EQU 0xE000ED20`、`:23` 定义 `NVIC_PENDSV_PRI EQU 0xFFFF0000`（注释即"PendSV and SysTick priority value (lowest)"），`rt_hw_context_switch_to`（`:166`）在**首次线程切换**中 `:189-194` 读该寄存器、OR、写回。`drv_common.c:49` 的 `NVIC_SetPriority(SysTick_IRQn, 0x0F)` 是更早的 C 侧设置，与汇编写入同指最低档 ⇒ 两条路线都存在，只搜到 C 调用不足以断言"未设置"，反之亦然 | `CMSIS core_cm7.h:2307-2312`（`SysTick_CTRL_CLKSOURCE_Msk` 置 1）；`drv_common.c:45`、`:49`、`board.h:198`（`SYSTICK_TIME_UNIT 1000UL`）；`rtconfig.h:73`（`RT_TICK_PER_SECOND 1000`）；`project.uvprojx:503-505`；`context_rvds.S:22-23/166/189-194`；`__NVIC_PRIO_BITS = 4`（`SDK-Device/Inc/NS800RT7P65x.h:301`）；cache `drv_common.c:119-120/126-133` | SRC + SDK-LOCAL | 已知=tick 基准、cache 策略、首次上下文切换显式把 PendSV（及该寄存器内 SysTick 字段）置为**最低可实现优先级**（4 位实现 ⇒ `0xFF` 对应级别 15）；**未知**=`SHPR3` 运行期实际读回值、`0xFFFF0000` 的逐字节归属、其它 IRQ 的优先级分配（不猜未读回的数值，也不改内核） | T02/T03（抢占与调度观测的前提；cache 也改变观测） |

### 2.2 执行者、核与其它总线主设备

| # | 结论 | 出处 | 等级 |
|---|---|---|---|
| F-12 | 工程按**单核**构建运行：`DUAL_CORE_ENABLE=0`、`RT_CPUS_NR 1`；`board.c` 在非双核分支把所有 AHB/APB 外设归 CPU1（`Device_enableAllPeripheralsInCpu1`） | `project.uvprojx:341`、`board/SConscript:14`、`rtconfig.h:69`、`board.c:141-143/180-208` | SRC |
| F-13 | 核身份判断代码仍在跑（`MCM->CPUID.WORDVAL & 0x01`）并按核选择 VTOR/半图地址；链接布局预留第二半图 `IROM2 0x08080000` | `board.c:153`、`system_NS800RT7xxx.c:58/62/76`、`project.uvprojx:21` | SRC + SDK-LOCAL |
| F-14 | `sema.c`/`mu.c`/`mcm.c` **参与编译**（uvprojx 无 `IncludeInBuild` 覆盖 ⇒ 默认编入）且 IPC 时钟被使能，但 `SEMA_*`/`MU_*`/`CPUCTRL` 在 HAL、applications、board 中**零调用点** ⇒ "驱动被编译" ≠ "另一核在跑" | `project.uvprojx:2635`（mcm.c）/`:2640`（mu.c）/`:2670`（sema.c）、`board.c:217-218`；grep 无调用 | SRC |
| F-15 | 本工程**无 DMA**：`rtconfig.h` 无 DMA 项、`.config:235` `RT_SERIAL_USING_DMA is not set`、HAL 只有头文件 `drv_dma.h` 无 `.c`；未发现任何把 GPIO 输出锁存作为 DMA 目标的代码 ⇒ 未知项只剩"出厂是否另烧 CPU2 镜像"（TBD/厂商） | `BSP/.config:235`、`LIB/HAL_Drivers/drivers/`（目录事实）、grep `EDMA\|DMA_\|dma_init` in applications 空 | SRC |
| F-16 | 目标线程清单（名字/优先级/栈/节奏）：`safety` 4/1024（事件驱动）、`motor` 8/768（`rt_thread_mdelay(10)`）、`sensor` 7/1024（tick 10）、`diag` 9/1024（tick 10）、`main` 10/2048、`tshell` 20/4096、`ui` 20/512、`bblog` 18/1024、`flog` 29/4096（= `RT_THREAD_PRIORITY_MAX-3`）；`fxcHold` 22/768 是 `tmc2209.c` 内的**测试钩子一次性线程** | `safety_thread.c:43-44/196-197`、`motor.c:90-91/1049`、`sensor_service.c:35-36/190-191`、`diagnosis.c:370-371`、`supervisor.c:222-223`、`blackbox.c:444-445`、`ns_storage.c:152-153` + `ns_flash_config.h:20-21`、`rtconfig.h:72/118-119/123-125`、`tmc2209.c:2765` | SRC |
| F-17 | `RT_THREAD_PRIORITY_MAX=32`、`RT_USING_OVERFLOW_CHECK`、`RT_USING_IDLE_HOOK`（列表 4、idle 栈 256）、`RT_USING_HEAP`+SMALL_MEM；**未开** `RT_USING_STAT`/`RT_USING_MONITOR`/`RT_USING_TIMER_SOFT`/`RT_USING_CPU_USAGE_TRACER` | `rtconfig.h:71-79/101-103`；`src/Kconfig:187/192`（选项存在但未开） | SRC |
| F-18 | 可用的现成观测接口：`ps`（含 `sp`、`max used%` 栈高水位，由扫描 `#` 填充计算；**usage 列输出 `N/A`**，因 CPU-usage tracer 未开）、`free`、`list_*`、`backtrace`、`memcheck`；**没有** `rt_thread_stat()` | `components/finsh/cmd.c:215/243/314-319/325-328`、`cmd.c:1109/1188`、`msh.c:74/93`、`include/rtthread.h:156`、`src/kservice.c:734` | SRC |
| F-19 | 周期计数器：CMSIS `core_cm7.h` **定义了** `DWT->CYCCNT`、`DWT_CTRL_CYCCNTENA`、`CoreDebug_DEMCR_TRCENA`、`DWT_CTRL_NOCYCCNT`，且 CMSIS 在编译清单内（`NS800RT7P65x.h:314 #include "core_cm7.h"`）；工程内**零启用代码**；`PMCCNT/PMU` 本地未定义 ⇒ 无 PMU 路线 | `packages/CMSIS-Core-latest/Include/core_cm7.h:1143/1168/1180-1181/1204-1205/1222-1223/1665/1714/1791/1803`；grep `DWT\|CYCCNT\|CoreDebug\|TRCENA` 在 HAL/applications/board/StdDriver = 0 | SDK-LOCAL |
| F-20 | **芯片是否真实现 DWT/CYCCNT 本地无从判断**（`NOCYCCNT` 只能运行时读）⇒ 先做可用性检查，再决定能报告什么，两种失败必须分开：① **计数器已证可递增、时钟频率未证** ⇒ 可报告原始计数差（cycles），**不得**报告已校准的 µs；② **计数器未实现 / 未启用 / 不递增** ⇒ 没有有效计数差，只能记 `COUNTER_UNAVAILABLE` 或 `COUNTER_UNCHECKED`；**零值不等于零耗时**，0 或不确定值都不得作为有效 cycles 参与统计 | 同上 `:1180-1181`；判定与样本规则见 §2.5 | TBD |
| F-21 | 已存在的 µs 级延时是用 **SysTick->VAL 忙等**实现的（不是 DWT）：`rt_hw_us_delay()` 按 `reload` 折算 tick 计数并轮询 `VAL`；`rt_thread_delay_until` 存在；无 `udelay` | `drv_common.c:89-113`（`:93` LOAD、`:95` ticks 折算、`:96-113` 轮询）、`src/thread.c:731/783`、`include/rthw.h:195` | SRC |
| F-22 | `rt_hw_interrupt_disable/enable` 就是硬件 PRIMASK（`MRS r0,PRIMASK / CPSID I`、`MSR PRIMASK,r0`），所以屏蔽期内 tick 不推进 | `libcpu/arm/cortex-m7/context_rvds.S:38-43/51-53`、`include/rthw.h:149` | SRC |
| F-23 | 控制台与 TMC 是不同外设：console = `uart1`（PA12/PA13，ALT6，GPIO_12/13 不可用作测量），TMC = `uart2`（PB6 TX / PB7 RX，ALT1） | `rtconfig.h:106-108`、`drv_common.c:160`、`drv_uart.c:105/114-121/128/134-141`、`applications/project_board.h:76`、`tmc2209.c:36` | SRC |
| F-24 | 已有 RAM 记录环（现成、非串口）：黑匣子 `bb_ring`/`bb_cap` 共 48 B/帧、100 Hz、300+150 帧 ≈ 35.2 KB BSS，读出口 `blackbox_status`/`blackbox_dump`；另有 `ns_log_*` 落 flash（`flash_logstat`/`flash_dump` CSV） | `blackbox.c:74-79/450/477/537`、`blackbox.h:38-40`、`ns_storage.h:47-55`、`ns_storage.c:14/108`、`ns_flash_shell.c:100/136` | SRC |

### 2.3 被测信号与物理口径（含"谁还会写它"）

| 信号 | 映射与职责 | 全部生产写入者（文件:行） | 备注 |
|---|---|---|---|
| STEP | PA.0 / EPWM1_A（ALT1）/ J4-18，PWM channel=0；测频率、占空比、关断后残余边沿 | 仅 EPWM 外设：表 `drv_epwm.c:96-98` + `ns800_epwm_gpio_init`（经 `:455` 调用）；应用层**无** `rt_pin_write`/`GPIO_writePin` | 冻结 PWM **不保证**引脚最终为 LOW（§6 判据 1） |
| DIR | PA.2 / J4-16；仅"全部静止 + 可信关闭"下由正式 API 改向 | 唯一写点 `motor.c:1488`；初始化表 `safety_gpio.c:40`（OUTPUT/LOW）+ `:109-110` | 不直接 `pin write` DIR（T08） |
| MCU 许可（DRV_ENABLE） | PC.23 / GPIO87 / J4-21；LOW=请求禁能，HIGH=软件请求放行 | 表 `safety_gpio.c:39`；`safety_gpio.c:177`（HIGH 被 `:162` 拒）、`:213`；调用者 `motor.c:159`、`safety_state.c:603`、`stop_priority.c:399/618/657/1278`（LOW）与 **`:810` 唯一 HIGH 生产点** | 回读 `safety_gpio.c:198/234` |
| ENN 反馈 | PC.6 / GPIO70 / J4-40（历史用户确认接 H6-5）；**输入、被动读**；HIGH=禁能侧、LOW=允许侧 | 表 `safety_gpio.c:47`（PIN_MODE_INPUT）+ fail-close：任何把该名字配成非 INPUT 的项会在 `:80-88` 报错；读路径 `:300-311 sg_fb_read` → `:318/328/333/370/450` | **不注册急停 IRQ**（`safety_thread.c:81-88` 输入表不含 PC.6，仅 LIMIT_* 条件编译 + TMC_DIAG=PA.3），不主动驱动，不加临时上拉 |
| SW1 | 现有三脚拨杆；历史实测 **2–3 允许、1–2 阻断** | 不经 MCU（无独立急停输入 IRQ） | 沿用实测定位，不靠图纸文字重猜方向；端子/位置对应仍登记为待核对（草案 X-5） |
| TMC UART | uart2 / PB6 TX、PB7 RX | 唯一物理发送 `tmc2209.c:397 ns800_uart_write_budgeted`；句柄 `:258`；open `:268`；读 `:314/:527` | 旧 e40 的 probe 可能写回 GCONF ⇒ 本轮与未来 A 包都不运行旧语义 |

**排除清单**（不得选作测量/临时用途）：GPIO_12/13（控制台 uart1）、PF.21（BUG-009 已弃用焊盘，且
`safety_gpio.c:501-509/558-568` 的 `pf21_diag`/`pf21_ab` 会真的驱动它 ⇒ 不进未来命令白名单）、
PA.1（EPWM1_B 保留，`project_board.h:9/21` 明示 never reuse）、限位引脚（不为它加 GND 跳线）。
另有非重叠写者：`board.c:105-116 LED_init` 与 `main.c:73/76/78` 写 GPIO_68/69/41（LED），与被测引脚不重叠。

**EPWM1 的写入者（实例级，M-2 依据）**：`ns800_epwm_epwm_hw_init`（冷启动把 TBCTL 写成
`STOP_FREEZE` + AQCTL/DB 复位）、`ns800_epwm_configure_regs`（prescaler/TBPRD/CMPx/AQCTL/DBCTL，
**不写计数模式**）、`ns800_epwm_drv_enable`（**唯一**写回运行计数模式）、
`ns800_epwm_commit_prepared_held`、`ns800_epwm_set_slave_phase`（`PWM_CMD_EXT_SET_SLAVE_PHASE`，
应用侧无调用点）、以及编译进镜像但零调用的 SDK `EPWM_configureSignal`（`StdDriver/Src/epwm.c:81-134`）。
应用侧只经 `rt_pwm_*`/`sp_*`：`motor.c:282-298/368/980`、`step_pwm.c:180/287/288/308/364`、
`stop_priority.c:396/417-418/616/648/891/1265`。`SYSCON_setTbClkSync`（`drv_epwm.c:444/466`）会整体
影响所有 TB 计数 ⇒ 属 M-2 的一部分，本地无法关闭。

### 2.4 电机 / 驱动 / 诊断参数（现状口径，不作批准值）

| 项 | 值 | 出处 | 性质 |
|---|---|---|---|
| 计划配置 | `irun=20 ihold=8 ihold_delay=4 tpowerdown=8 vsense=1 mstep_reg_select=0 mres=0`；目标 `GCONF=0x00000141`、`CHOPCONF=0x15030053` | `tmc2209.c:1137/1145-1146`；`:1136` 明示"待验证配置，非实测事实" | 代码意图 |
| Rsense / VREF | 110 mΩ（模块商品页暂定）/ 1270 mV（操作员万用表历史） | `tmc2209.c:1141-1142` | ❌ 不可当实测/阈值 |
| 限流公式 | `I_peak = V_FS/(RSENSE+Rds)×CS×min(VREF,2.5)/2.5`，`Rds=20 mΩ` | `tmc2209.c:1587-1603`、`:143-146` | 计算口径可继承 |
| 电机额定电流 | 唯一记录 `1500 mA RMS`，且同处自否"理论值，未实测" | `tmc2209.c:2347/2348` | ❌ 无铭牌佐证 ⇒ TBD |
| CHOPCONF 从未写过 | 明示 | `tmc2209.h:122` | 事实（未来 apply 前芯片态未知） |
| 电流通路标定 | `30 mΩ + NSCSA240A1(20 V/V) + 1.65 V ⇒ (mV−1650)/0.6`，来源标 `THEORETICAL` | `current_adc.h:5`、`.c:4-7`、`project_config.c:44-48` | 理论，非实测 |
| 诊断阈值/帧数 | 分带 `200/1000 Hz`；`sg_warn 30/100/200`、`sg_stall 15/50/100`、`cur_warn 300/500/800 mA`、`cur_stall 800/1200/1500 mA`、`vib_impact 2500 mg`；`persist_warn 50`、`persist_stall 100`、`impact_frames 2`、`hysteresis_frames 100` | `project_config.c:50/52-58/60-63`；`:17` 明示"工程占位（未做真实电机标定）HARDWARE-PENDING" | ❌ 合格阈值 ⇒ ACCEPTANCE_LIMIT_TBD |
| 诊断模式 | 默认 `MONITOR_ONLY`；`ACTIVE_PROTECTION` 需 `MEASURED` 标定来源 | `diagnosis.c:110/383-393`、`diagnosis.h:24-26` | 规格（必须继承） |
| 采样/帧格式 | 传感器 100 Hz（`SENSOR_PERIOD_MS 10`）、黑匣子 48 B/帧 @100 Hz、pre 2000 ms/post 1000 ms、`BB_SUBMIT_TRIES_MAX 20`；帧内 `11:8`=四路数字电平（bit8=ENN、9/10=LIMIT、11=DIAG）、`active=15:12`、`valid=27:24`、语义版本=1、`SCHEMA_LEGACY=0` 时 bit8 角色**不可追认** | `sensor_service.c:37`、`blackbox.c:74-79/106-109/450`、`safety_io_semantics.h:33/42/67-78/89-90` | 格式规格可继承 |
| "软件是第二响应"原文 | `motor.c:23/114/619/1588`、`motor.h:30`、`diagnosis.c:32`、`diagnosis.h:28` | 多处 | 必须逐条保留，不得被任何 PASS 覆盖 |

### 2.5 时间基与样本有效性（本文统一口径）

F-01..F-24 的一切换算、T00 的读回、§4 的插桩纪律与 §8 的记录模板都引用下面三张表，不再各自表述。

**`clock_basis` —— 频率依据分三层，只有最高层才允许"已校准 µs"：**

| 层 | 含义 | 允许报告 | 不允许 |
|---|---|---|---|
| `SOURCE_ASSUMED` | 沿用代码常量（F-02 的 `HXTL_FREQ_VALUE = 20000000UL`）算出的任何频率 | 带前提的换算，且必须显式写"按 20 MHz 假设" | 写成"实测 µs"或"时钟已确认" |
| `CONFIG_READBACK` | 读回 `SystemCoreClock`、`RCC_get*`、TBCTL/PLL 分频位 | 核对**软件报告值与配置链**是否自洽、当时生效的分频位 | 当作外部晶振或实际核心频率的独立证明：这些读回若仍由同一个未确认常量算出，彼此一致只说明计算链自洽 |
| `EXTERNAL_VERIFIED` | 用与 MCU 无关的独立时基，对已核实的测点直接测量 | 校准后的 µs / ms / 频率 | 只有确认输出路由、分频、测点并真的做了独立测量才算；**仅配置或读出 MCO 名字不是测频**；引脚复用与接线是单列的未来授权动作 |

**`counter_status` —— 计数资源状态分开登记（依据 F-19/F-20/F-21）：**

| 值 | 判定条件 | 样本处理 |
|---|---|---|
| `COUNTER_OK` | 已启用，且实测两次读数递增、差分与预期量级一致 | 有效计数差可参与统计 |
| `COUNTER_UNAVAILABLE` | 检查后确认未实现 / 不递增 / 无法启用 | 该场景**没有**窗口时长样本；只保留状态与顺序观测 |
| `COUNTER_UNCHECKED` | 尚未做可用性检查 | 一律不得报告时长（**包括不得报 0**） |

**样本有效性判据**：每条时长记录同时携带 `counter_status / clock_basis / sample_valid / invalid_reason`。
只有 `sample_valid=1` 的样本进入分布与最坏值统计；无效样本要保留并写出 `invalid_reason`，不得静默丢弃。

SysTick 单圈差分为什么不足（§4 替代 A 采用保守降级的依据）：

```text
反例（数学推演，未运行目标或主机测试）：
重装周期 N = LOAD + 1（LOAD 是最大重装值，模数是 LOAD+1，不是同一个数）。
起点 VAL=300、终点 VAL=200 时：
  100 个计数 与 N + 100 个计数（中途跨过一次重装）得到完全相同的起止读数。
只做单圈模差分 ⇒ 第二种长区间也被记成 100。
"预期窗口是 µs 量级"不能证明"必小于一圈"；而 post−pre 允许包含恢复中断后的 ISR/调度，
恰恰不能默认仍在一个 tick 内。
```

所以本计划对 SysTick 差分采用**保守降级**（见 §4 替代 A）：不产出用于 `B-1` 的有效窗口样本。
若未来仍要用 SysTick 测宽，必须先给出发现跨重装 / 多圈 / 无法判定的完整判据、排除样本的规则，以及
读取相关状态对系统原有行为的副作用，并同时区分 `LOAD` 与模数 `LOAD+1`；**不得**重配 SysTick，也**不得**
用忙等去"验证"被测窗口本身。32 位 DWT 差分同样要写明允许区间与回绕有效性：跨回绕、无法判定或超出
声明上界的样本判 `sample_valid=0`，不得把未证明的长区间自动解释成一次回绕内的短区间。

## 3. 测量分段：五段分别取证（不合并成一个"停机耗时"）

| 段 | 起止定义 | 核心观察 | 时间基 | 不能替代的证据 |
|---|---|---|---|---|
| 检测段 | 物理故障或已登记注入发生 → 生产守卫作出故障判定 | 10 ms 斜坡节拍（`motor.c:89`）、200 ms 完整性复核节奏（`motor.c:119`，且**只**在 `mot.armed` 时累计，`motor.c:665-671`）、复核被 UART 占用的时长（`motor.c:720-722`） | tick/日志 | 主机发命令的时刻**不是** MCU 故障入口时刻；`motor.c:99/101-106` 明示 405 ms 与 200 ms 都不是停机上界 |
| 短保护段 | 抑制/请求发布入口 → 两条真实关闭动作与 `__DSB` 完成 | PRIMASK 窗口宽度、PWM 与许可 LOW 的**独立** raw rc、请求身份 epoch | 周期计数器（F-19/F-20，且须先按 §2.5 判为 `COUNTER_OK` 才产生有效时长样本）；tick 在屏蔽期不推进（F-22）⇒ **不能用 tick 测窗口** | 只有 tick/日志时刻不等于窗口时长；`rt_pwm_disable()`/API `RT_EOK` 只表示驱动接受请求（`step_pwm.c:30`） |
| 物理响应段 | 指定电气触发 → STEP 后续边沿 / 许可 / ENN 达到相应状态 | 多通道同步波形、阈值、残余脉冲计数 | 仪器时基 | API 返回、寄存器回读、数字单点都**不等于**物理响应完成（M-3）；单台万用表轮流测六点不算同一时刻（草案 V04-03.3） |
| 慢整理段 | 短保护完成 → 锁取得 / 影子与 Safety 提交 / 通知 / 日志 | pending、epoch、锁等待、黑匣子一次性 | tick + 时间戳 | 日志变晚 ≠ 关闭动作变晚；日志出现也不保证此前已物理停 |
| 恢复段 | 人工恢复票据 → 专用复核 → 唯一最终提交 | 新故障落在最终提交前/后的接缝、旧票据被拒、不自动 RUN/HIGH | tick + epoch | 手工清故障成功 ≠ 硬件故障消失 |

结构性事实（本轮从源码确认，供未来判据引用）：**慢整理仍可能等待 `ss_lock`/`mot_lock`**——
`motor.c:143`（`mot_lock_take` FOREVER）、`motor.c:1756`（`motor_lock_take_checked` 返回真实 FOREVER
结果）、`safety_state.c:157/207/251`（`ss_lock` FOREVER）；存储与配置侧同为无限等待
（`ns_flash.c:87/108/347/413`、`ns_storage.c:58/115/118/142/160/214/228/255/264`、
`project_config.c:32`）。TMC 外层 txn 是 `WAITING_NO`，cfg/xfer 才是 10/20 ms + 总预算
（`tmc2209.c:1335-1350`、`:623-638`）。UART 完整性复核仍在 Motor 斜坡线程内、按 200 ms 节奏在
`mot_lock` 之外执行，**没有**独立 UART 守卫线程（`motor.c:661-728`）。

## 4. 插桩设计（形状与纪律，本轮不实现、不编译、不改四宏）

优先级：**目标周期计数器 + 固定 RAM 记录**，放在**测量专用固件变体**里（默认发布配置不含它）。
不得更改 `stop_priority` 协议、RT 内核或 `__DSB` 顺序。

```c
/* 设计伪码，不是本轮待写入的代码。 */
pre = cycle_now();
saved = rt_hw_interrupt_disable();
body_begin = cycle_now();
/* 原资格比较、抑制/身份、固定寄存器写；保留原屏障 */
SP_BARRIER();
body_end = cycle_now();
/* 恢复中断前把本次身份/原始 rc/时间局部快照钉死 */
rt_hw_interrupt_enable(saved);
post = cycle_now();
/* 固定 RAM 槽记录；测量结束后再导出；不在关键窗口内打印 */
```

必须写进计划的约束（缺一条则该批数据不得用于结论）：

1. `body_end-body_begin` 只覆盖窗口主体与屏障，**不得**称"完整屏蔽宽度"；`post-pre` 覆盖整个窗口，
   也可能包含窗口外 ISR/调度开销，只能作为该样本的**包围区间**。
2. 计数开销、调用序言/尾声、采样代码自身附加开销分别单独记录；不得用"扣掉一次空测量"的负差值
   或乐观差值充当最坏值。
3. 嵌套窗口按 `saved` PRIMASK 识别并原值恢复；ISR 与线程都可能写槽 ⇒ 槽的预约/发布**不得**递归调用
   被测窗口、不得撕裂本次 epoch/proof；槽满只累加丢失计数，不等待、不打印；发生丢失即判"该批分布
   不完整"。
4. 屏蔽窗口内禁浮点/除法/UART/IPC/延时/等待循环（与 S2 合同一致）；周期→时间换算与输出一律窗口外。
5. 每条时长样本必须带 §2.5 的四个字段并逐条判定：`counter_status`（未检查或未递增 ⇒ **没有**有效计数差，
   零值不是零耗时）、`clock_basis`（`SOURCE_ASSUMED` 只允许带假设的换算；`CONFIG_READBACK` 仍不构成外部
   频率证明）、`sample_valid`、`invalid_reason`。32 位计数器要记录无符号差分、允许的最长区间与回绕
   有效性；超出上界或无法判定是否跨回绕的样本判 `sample_valid=0`，不得自动解释成一次回绕内的短区间。
   核心时钟未证时**只报 cycles 不猜 µs**（F-02/F-20 未解决前，µs 一律标换算假设）。
6. 测量期间不用断点/单步制造"运行时长"；调试暂停/睡眠对计数的影响须剔除或单列（F-19 的 CYCCNT
   属调试资源，`CoreDebug->DEMCR.TRCENA` 与调试器行为相关，本地无从判断其实现）。
7. 若要用额外 GPIO 标记或跟踪接口：先确认空闲资源与时钟域，并作为**新的板级许可条目**列出；
   不得临时占用 PA.1、不得覆盖既有 STEP/DIR/许可/ENN 信号。无仪器时相关物理条目保持
   `BLOCKED_INSTRUMENT`。

资源与替代方案（按本地证据）：

- 首选 DWT/CYCCNT：定义在编译清单内的 CMSIS（F-19），但**是否实现未知**（F-20）⇒ 未来第一步是读
  `DWT_CTRL.NOCYCCNT`、置 `TRCENA`+`CYCCNTENA` 后**验证计数确实递增**，据此把 `counter_status` 记为
  `COUNTER_OK` 或 `COUNTER_UNAVAILABLE`；该检查未做之前，一切时长条目都是 `COUNTER_UNCHECKED`。
  即便 `COUNTER_OK`，仍要单独解决 `clock_basis`：原始计数差可报，µs 换算仍受 F-02 前提约束。
- 替代 A（**本批选定：保守降级**）：DWT 判为不可用时，`SysTick->VAL` 差分（工程同原理实现是
  `rt_hw_us_delay`，F-21）**不产出用于 `B-1` 的有效窗口样本**，相应条目登记 `BLOCKED_COUNTER`，
  只保留状态与顺序观测（inhibit / pending / 各动作 raw rc / epoch），等合适计数源或仪器到位再谈时长。
  理由见 §2.5 的单圈混叠反例：模差分无法排除多圈，而"预期 µs 量级"不是上界证明。该路线**不得**重配
  SysTick，也**不得**用忙等去验证被测窗口——`rt_hw_us_delay` 本身就是同类忙等，不能自测。
  量程/分辨率事实仍记录在案：量程 1 tick（1 ms）、分辨率 = 处理器时钟周期。
- 替代 B：tick + `ps` 栈水位/黑匣子帧时刻，只给 ms 量级分布（F-17/F-18/F-24），并如实标精度；
  屏蔽窗口内 tick 不推进（F-22），故替代 B 只适用于窗口外的段。
- 记录载体：新增静态 RAM 环（不与黑匣子 `bb_ring` 混用，避免污染既有语义），导出口在 A 包命令清单里
  单列并批准；`ns_log`/flash 路径只在窗口外使用（flash 锁是 FOREVER，见 §3 末段）。

计划插桩点（未来实现时逐个绑定，本轮不落地）：

| 点 | 位置（当前源码行） | 采什么 | 用于 |
|---|---|---|---|
| I-1 | `stop_priority.c` `sp_trip_fast`/`sp_protect_held` 窗口入口与出口 | `pre/body_begin/body_end/post`、epoch、owner、pwm_raw_rc、permit_write_rc | T02 |
| I-2 | `stop_priority.c:891 sp_pwm_start_held` 与 `:396/:616/:648/:1265` disable 路径 | 动作分支 + 各自 raw rc | T02/T07 |
| I-3 | `LIB/HAL_Drivers/drivers/drv_uart_txleaf.inc` TX 等待环（末字节后复查处） | 每字节轮询次数、written、超时点、tick 差 | T04 |
| I-4 | `tmc2209.c:1335/:623` cfg/xfer take 前后 | 请求等待值、结果、真实取得/归还计数 | T05 |
| I-5 | `motor.c:661-728` 守卫拍（armed 且 due） | 复核进入/退出、`mot_lock` 释放后等待 | T01/T03/T04 |
| I-6 | `safety_state.c` 慢整理与 `ss_lock` 取得处 | 等待时长、pending→ack 顺序、一次性提交 | T03/T10 |
| I-7 | `motor.c:1488` DIR 写点 | 门判定结果 + 是否有写 | T08 |

未来测量固件必须单独保存构建输入、ELF/BIN 的 SHA-256 与相对 `bcdec631` 的差异；**实际烧录前**让用户
看到具体镜像与操作包。

## 5. 预算、接受阈值与取样方案

从源码原样带入（**软件预算，不是已测结果，也不是系统最坏停机承诺**，出处 `tmc2209.c:59-72`、
`motor.c:89/119`）：

| 预算 | 值 | 预算 | 值 |
|---|---|---|---|
| 独立读 | 125 ms | cfg 锁 | 10 ms |
| integrity | 250 ms | xfer 锁 | 20 ms |
| confirmed write | 400 ms | TX 整帧 | 20 ms |
| apply | 1500 ms | flush | 2 ms **与** 256 B 双上限 |
| 斜坡节拍 | 10 ms | 完整性复核节奏 | 200 ms |
| UART 应答 | 100 ms | 许可回读 | 1 ms × 3 次 |

接受阈值：项目内**没有**任何已约定的窗口时长上限、中断丢失容忍、物理停止延迟、STEP 频率/占空比公差、
诊断检出率/误报率（`reports/plans/phase8-stop-priority-s2a-design.md:28/67/276`、
`phase8-safety-chain-acceptance-draft.md:261/266/268` 一致声明未获批准/未测）。因此上述全部记
`ACCEPTANCE_LIMIT_TBD`，在未来执行前由用户/规格定口径；**不得**拿实测最大值反推成接受阈值；
**不得**用"200 ms + 250 ms"直接给整机 450 ms 最坏上界。

首次观测取样方案（**待批准的工程方案**，不代表统计可靠性，也不代表已授权重复动作）：
每个可重复短窗口场景先 1 次确认路径，再计划 16 个样本；正常 PWM 数值点 1 / 20 / 1000 / 20000 Hz，
每点记录 ≥10 个完整周期；竞争类场景（T05）每把锁两种期限各 ≥8 样本。若日后取得更高样本量的规格要求，
以规格为准。

## 6. 未来执行矩阵（最低完整集合；当前全部未授权 / 未执行）

通用前置（适用所有条目）：镜像与构建输入已单独批准、四宏状态与批准范围一致、仪器与测点已记录、
失败即停条件命中即登记 `stop_reason` 不再推进；未满足前置 ⇒ `BLOCKED*`/`NOT EXECUTED`，
**不得**用"被四宏挡住"冒充目标成功运行。

| ID | 包 | 未来要验证什么 | 入口 / 固件与宏要求 | 现场前置 | 观测点 / 证据 | 阈值 | 状态 |
|---|---|---|---|---|---|---|---|
| T00 | A | 执行核、其它写入者、EPWM1 A/B、时钟链、周期计数资源 | 现固件 + 只读 msh；无需开宏 | 板可上电、串口白名单 | §2.1–2.3 表的实测核对，**分层登记**：`SystemCoreClock`/`RCC_get*`/TBCTL 位/`DWT_CTRL` 可读值只算 `CONFIG_READBACK`（核对软件报告链，不升级成外部频率证明）；周期计数资源按 §2.5 判 `counter_status`；`clock_basis` 未到 `EXTERNAL_VERIFIED` 前不得写"实测 µs"。MCO 需先确认路由/分频/测点并用独立时基测量，引脚复用与接线是单列未来授权动作 | 记录即可（不设阈值），但 §2.5 四字段必填 | 仅离线调查（本轮） |
| T01 | A | 新四宏 FALSE 固件的首次启动与无电机冒烟 | **测量/发布镜像**（哈希先经用户确认）；四宏 FALSE | 电机未接、模块在位状态如实记、供电来源与断电判据（P-5） | 启动自动硬件初始化清单、线程/健康/使能/输出状态；不要求故障板硬凑 READY | 逐项与源码预期一致 | 未授权 |
| T02 | A | 正常 / 已抑制重复 / 真实关闭等短窗口路径 | 2 插桩；关闭方向**不要求**先开启输出 | 同上 | `body` 与包围区间（cycles）**仅在 `counter_status=COUNTER_OK` 且 `sample_valid=1` 时进入分布**，其余逐条写 `invalid_reason`；各动作 raw rc、epoch/owner、丢失计数。DWT 判不可用 ⇒ 本例时长登记 `BLOCKED_COUNTER`，只交状态与顺序证据 | 时长 ACCEPTANCE_LIMIT_TBD；raw rc 与合同一致为硬判据 | 未授权 |
| T03 | A | 目标负载下"保护先于慢锁/日志" | 现有任务负载 + 6；`ps` 栈水位 | 同上 | 锁等待/保护时间戳顺序、pending/一次性整理、日志时刻 | 顺序为硬判据；时长 TBD | 未授权 |
| T04 | B | 真 UART 正常 / 无应答 / 部分帧 / CRC / 洪泛 / 末字节过期 | 5；125/250/400/1500 同一条预算 | uart2 可观测（F-09 时钟未知须记录） | 数据量、`tmc_xfer_reason`、截止位置、字节/时间双上限触发点 | 预算内为硬判据；物理位时间 TBD | 未授权 |
| T05 | B | cfg/xfer 竞争与最短剩余预算 | I-4；由**另一上下文**持锁（不同线程/优先级） | 同上 | 请求等待值、真实取得/归还次数、先保护后日志 | `min(cap,剩余)` 一致为硬判据 | 未授权 |
| T06 | B | 新 EPWM 数值的真实波形 | 正式 API + 隔离台架。**任何 STEP 输出**需 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` **或** `STEP_DIAG_ISOLATED_ENV_CONFIRMED` 至少一个为 TRUE（`motor.c:2066`、`step_pwm.c:16-17`）；后者只允许在"TMC 模块拔出 / 功率级断开、只为示波器量 STEP 频率"时由操作员人工翻转并留证（`motor.h:77-79`）；关断失败锁存期间连借用申请都被拒 ⇒ **宏不能越过未复核的关断失败**（`motor.h:82-83`） | 示波器/逻辑分析仪可用且通道/触发已记录 | STEP 同步波形：1/20/1000/20000 Hz 周期与脉宽、分频切换、累计边沿计数；**只报电气信号之间**的时序，换算所用时钟按 §2.5 分层标注；本例**不**产出"寄存器写→焊盘"的时间关系（见判据 2 与 §8 的 M-3 行） | 频率/占空比公差 ACCEPTANCE_LIMIT_TBD；`clock_basis` 未到 `EXTERNAL_VERIFIED` 前不得写"实测 µs" | 未授权 + 受宏与仪器限制 |
| T07 | B | 保护后旧配置 / 旧 enable 不得重新开启 | prepare 完成后的**门外**接缝 → 正式保护 → 原 token 真提交；禁止在屏蔽窗中强行执行普通 ISR | 同 T06（需可达预约前置） | CTRMODE 保持冻结、寄存器写增量 0 | 硬判据 | 未授权 |
| T08 | B | 静止 DIR 门；运行/未知关闭状态拒绝改向 | 正式 `motor_set_direction`；不得 `pin write` DIR | 同上 | 门判定 + 是否有 DIR 写 + 波形/状态 | 硬判据 | 未授权 |
| T09 | B | SW1 动态电气链与软件后续反应 | 正式许可状态下 SW1→ENN→软件 LOW 的同步先后；不暂停保护来强留 HIGH | 仪器 + 现场授权（改线/拨杆为单列动作） | 多通道同步波形、阈值、残余脉冲；继承既有静态/数字结论。SW1→ENN→软件 LOW 只给出**这些电气信号之间**的先后；MCU 侧 `__DSB`/cycle 记录**单列**，两套时间基未做共同事件对齐前**不得**合并成"写入→边沿"耗时（判据 2） | 物理延迟 TBD；不继承为已测 | 未授权 |
| T10 | B | 人工恢复接缝与故障保持 | 原票据、新故障落在最终提交前/后、唯一提交、无自动 HIGH/RUN | 清故障动作单列授权 | epoch/pending/latch 序列 | 硬判据 | 未授权 |
| T11 | A | 无运动传感器 / 状态语义 / 黑匣子记录 | `ps`、`blackbox_status/dump`、`flash_logstat/dump` | 同上 | 有效/活动掩码、ENN 反馈语义、原始帧时间、栈水位；存储写入与读取**分列** | 与 `safety_io_semantics.h` 位域一致 | 未授权（且不是堵转准确率证明） |
| T12 | B | TMC 受控 apply 与配置回读 | 需 `TMC_DRIVE_CONFIG_HW_VALIDATED` 的**有限台架例外**＋运行态门禁；不按主机 `-D` 搬上板；不写 VACTUAL | VM/限流/模块状态确认 | 四寄存器、IFCNT、读回、原预算、失败保护 | VREF/Rsense 依据必须实测或标注理论值 | 未授权且宏关闭 |
| T13 | C | 带电机低速基线与后续诊断验收 | 独立于 A/B；MONITOR_ONLY 起步 | 电机额定/VM/限流/机械/ADC 标定全部先确认 | 带标签工况的原始数据；正常基线、可控异常标签、误报/检出、检测段与关闭段**分开**统计 | 检出率/误报率 TBD；ACTIVE 需 MEASURED 门禁不可跳 | 未授权 |

特殊判据（必须随计划交付）：

1. 冻结 PWM 不保证 STEP 最终为 LOW：观察**后续有效边沿与残余脉冲**；"引脚仍 HIGH"单独不算关断失败，
   一次 LOW 单点也不算"无后续脉冲"。
2. 区分四个时刻：软件写完成、`__DSB` 完成、外设寄存器状态、焊盘/ENN 实际变化时刻；M-3 不由单次回读
   或单个计数记录替代。**MCU cycle 与仪器时间是两套时间基**：只有先设计出可识别的**共同事件/标记**、
   给出对齐方法与误差来源，才允许报告"寄存器写→焊盘"的时间关系；当前**没有任何标记 GPIO 获批准**，
   不得假定它存在。若只同步采集 SW1 / 许可 / ENN / STEP，就只报这些**电气信号之间**的时序与残余边沿，
   MCU 的 DSB/cycle 记录单列，M-3 中"写入到焊盘"的部分**继续开放**。
3. 无应答/CRC/洪泛的**目标机**注入需具体设计；软件测试缝只证明分支行为，不冒称真实断线/FIFO 故障；
   断电/改线/外部发帧设备是单列授权动作。
4. 允许"正常时序先开后停"；禁止"停后旧身份重新开启"。不要把正常时序先后误判成故障。
5. 结果分类只用：`PLANNED_NOT_EXECUTED`、`BLOCKED`、`OBSERVED`、`PASS`/`FAIL`（后两者仅在真实执行且
   满足预先判据时可用）。

## 7. 三个未来操作包（当前均无授权）

**A｜四宏 FALSE、无电机的新固件冒烟与关闭路径观测**：精确镜像（含重建与回退来源）；允许的烧录 /
一次启动或复位；启动自动的 GPIO/PWM 关闭与外设初始化行为；串口白名单命令与必要读写；
T01/T02/T03/T11 中实际可达部分；若含故障请求或人工恢复，**逐项列次数**与实读 GPIO/TMC 的副作用。
不含：许可 HIGH、STEP 开启、TMC apply、改线/供电/SW1、电机。四宏 FALSE 下的启动拒绝分支可测，
但**不得**作为开启路径已通过的证据；旧 e40 命令语义不得移用。

**B｜无电机、隔离台架的时序/波形/TMC 验证**：覆盖 T04–T10/T12 中经授权可达项。逐例列**实际需要的
宏组合与解除依据**：**任何 STEP 输出**需要 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED` **或**
`STEP_DIAG_ISOLATED_ENV_CONFIRMED` 至少一个为 TRUE（`motor.c:2066`、`step_pwm.c:16-17`），后者只能在
"TMC 模块拔出 / 功率级断开、只为示波器量 STEP 频率"的隔离条件下由操作员人工翻转并留证
（`motor.h:74-79`），且关断失败锁存期间连借用申请都被拒——**翻宏不能越过未复核的关断失败**
（`motor.h:82-83`、`motor_pwm_grant_to_diag`）；不能临时调裸 HAL 或 `pin` 命令绕过。
正式 Motor 许可/armed/RUN 还需配置、反馈、owner/phase 全部门（`motor_get_gate_fail_mask` 七位，
`motor.c:898` 起），只翻一个宏不代表可达；TMC `apply` 另需 `TMC_DRIVE_CONFIG_HW_VALIDATED`
（`tmc2209.h:100`）与运行态门禁。不得把待测性质预先写成已验收来翻宏；
既有 `USER_CONFIRMED` 静态电气/SW1 范围可作已有依据，欠缺的新证据与所需有限台架例外要**明确提出供审议**。
宏调整、输出/许可 HIGH、TMC 写、SW1、外部发帧/断线/供电变化逐项进入用户可批准清单；任何新增接线以
断电条件与现场核对为前提。仪器不足时 A 中可达项不整体停等，B 中无法取证的波形/瞬态项保持
`BLOCKED_INSTRUMENT`，不得用 MCU 单点反馈或"低速信任法"填 PASS。

**C｜电机 / ADC / 诊断实物集成**：独立于 A/B。先确认电机额定电流（现仅一处无佐证的 1500 mA 记录）、
VM/限流、ADC 前端拓扑/增益/零点、轴与负载约束，未知列 TBD；源码 `Rsense=110 mΩ`、`VREF=1270 mV`
是计算配置，**不是**当前模块实测值，也不得直接批准默认 `IRUN`。诊断保持 `MONITOR_ONLY` 100 Hz，
分带阈值与持续帧数按实际 `project_config` 读取；`ACTIVE_PROTECTION` 的 `MEASURED` 来源门不可跳过；
不安排手堵轴、制造短路或高速运行。

未来授权按**整个操作包**给出，包内按序推进，不为每条已批准命令反复确认；前置失效、越过已批范围或
出现真实失败才停。本轮只准备清单，**没有任何包获得执行许可**。

## 8. 证据模板、时间基与风险关闭对应

可复用记录行模板（本轮只定义字段，不实现工具）：

```text
case_id / status / software_sha / measurement_variant_sha / image_sha256
source_level / macro_set / instrument_and_setup
clock_basis(SOURCE_ASSUMED | CONFIG_READBACK | EXTERNAL_VERIFIED)
counter_status(COUNTER_OK | COUNTER_UNAVAILABLE | COUNTER_UNCHECKED) / sample_valid / invalid_reason
trigger_definition / input_identity / epoch_before_after / owner_phase
cycles_pre_body_begin_body_end_post（仅 counter_status=COUNTER_OK 且 sample_valid=1 才进入统计）/ tick_entry_exit
pwm_raw_rc / permit_write_rc / permit_confirm_rc / enn_feedback
pending_before_after / latch / metadata_commit / notify_count
wire_edges_or_capture_ref / samples_expected_observed_dropped
timebase_linkage(common_event_or_NONE / alignment_method / error_budget)
acceptance_limit_and_source / actual_result / unresolved / stop_reason
```

规则：原始数据、导出文本与判据结果必须能定位到**同一镜像 + 同一接线/测量配置**；串口日志时刻、
MCU cycle、仪器时间**不默认同一时间基**（F-11/F-21/F-22：tick 与周期计数分属两套基准，屏蔽期 tick
不推进），跨时间基的任何"写入→边沿"关系必须填 `timebase_linkage`，填不出共同事件就写 `NONE` 并保持
该关系开放；`clock_basis / counter_status / sample_valid / invalid_reason` 一律按 §2.5 取值，不得留空。
命令文本、过程截图、结论数字各自注明来源。实测最大值只是指定条件下的观测值，不自动关闭最坏上界或
安全等级类风险。

| 风险 | 哪些证据能关闭**哪一部分** | 关闭不了的剩余部分 |
|---|---|---|
| `B-1` 门内窗口时长 | T02 的 `body`/包围区间分布，**且** §2.5 判为 `counter_status=COUNTER_OK`、逐条 `sample_valid=1`；SysTick 单圈模差分**不能**关闭本行（保守降级为 `BLOCKED_COUNTER`，只交状态与顺序证据） | RX/EXTI 在该时长内的丢失容忍度（无阈值约定 + FIFO/波特率/时钟参数未逐项记录，含 F-09）；计数资源未确认 ⇒ 时长部分整体保持开放 |
| `M-1` 其它执行者 | T00 的执行者与 `ps` 线程实测（核对 §2.2 表） | 出厂是否另烧 CPU2 镜像、SEMA/MU 是否被外部使用（本地无从判断） |
| `M-2` 同 EPWM 实例其它通道 | 无法由本地关闭（"没搜到应用调用点"已列出但不构成证明） | 需厂商资料 / 原理图 / 网表；含 `SYSCON_setTbClkSync` 与保留的 PA.1 |
| `M-3` 写完成 ↔ 引脚响应 | **仅当**存在经批准的共同事件/标记 + 对齐方法 + 误差来源（模板字段 `timebase_linkage`）时，T06/T09 才能给出"寄存器写→焊盘"的时间关系；否则本行只关闭**电气信号之间**（SW1 / 许可 / ENN / STEP）的时序与残余边沿 | 单点回读、周期计数记录都不构成物理时刻证明；无标记 GPIO 授权时"写入→焊盘"部分**继续开放**，MCU 侧 `__DSB`/cycle 记录单列 |
| `RISK-03/03a` | T01/T03/T04/T05/T10 的目标机分布 | 调度覆盖不足的分支、最坏停机时延（阈值未约定 ⇒ 保持开放） |
| 物理 `RISK-04-b` | T09 的瞬态与连续性证据（仪器可用时） | 断线容错、外接 NC、安全等级 |
| `BUG-008` | T06 的频率/占空比与残余脉冲观测 | 停机电气表现与真实波形验收 |

## 9. 未决项（不阻塞本轮离线交付，且**不**要求用户重复既有静态五点/SW1 确认）

1. **当前可用仪器与独立时基**：示波器/逻辑分析仪是否有、型号、通道数、采样/存储深度、触发与探头方式、
   差分测点可行性；万用表当前型号与可用性未知（唯一历史型号 UT39A+）。缺 ⇒ T06/T09 与一切瞬态条目
   `BLOCKED_INSTRUMENT`；万用表只能填静态读数栏，不得填瞬态/脉宽/停机时延。**同组还缺独立时钟依据**：
   没有独立时基就没有 `EXTERNAL_VERIFIED`，`clock_basis` 只能停在 `SOURCE_ASSUMED`/`CONFIG_READBACK`（§2.5），
   周期计数资源的 `counter_status` 也只能在目标机检查后才能脱离 `COUNTER_UNCHECKED`。
2. **现场供电、隔离与电机参数**：VM/逻辑电源来源与断电判据（草案 P-5）、模块是否可拔出、
   电机铭牌额定电流与绝缘/温度限制、ADC 前端实测标定（零点/增益）、ENN 线路连续性。缺 ⇒ T12/T13 受阻。
3. **待批准的三个操作包**：A/B/C 的镜像、宏组合例外、命令白名单与次数、仪器与接线前提（§7）。
   本组另含两项**尚未批准**的具体动作：MCO 输出路由/分频/引脚复用与接线，以及为跨时间基关联而新增的
   标记 GPIO（判据 2 / §8 的 M-3 行）——两者都是新的板级许可条目，本轮只登记需求，不配置、不占用、
   不假定已存在。未获批准前，本轮与任何后续自动流程都不得执行其中任一条。

登记为未决但不需本轮处理的文档不一致：

1. `stop_priority.h:23` 曾描述"set 路径会抓旧模式再写回 TBCTL"，与现码
   `drv_epwm.c:293-318`/`drv_epwm_leaf.inc:232` 的"不写回计数模式"不一致 —— **已在 A0 实现轮随
   允许的生产改动一并更正**（`code_commit` 见 §11），公开接口未变；历史登记保留在此说明去向。
2. 仍未处理：`phase8-safety-chain-acceptance-draft.md:269/433` 引用的 `开发进度.md:33` 行号已过期
   （实际在 `:104`）。该文件不在 A0 白名单内，继续留给下一次允许范围覆盖它的改动。

## 10. 本轮检查与不做项

检查（首轮，四文件）：改动仅 §9 白名单四文件；生产/HAL/内核/所有宏/`tests/host`/工程/`.gitignore`
零改动；JSON 可解析；引用路径与版本可核对；无本机绝对路径、用户名、邮箱或主机名；不重跑任何主机链、
不重编译 Keil。**首轮原文里的"`git diff --check` 干净"一句不成立**：复审方独立运行
`git diff --check f919a5c b27c0c4` 实得 **rc=2**，命中
`reports/plans/phase8-target-measurement-plan.md:82: trailing whitespace.`（F-20 行末竖线后一个空格）；
r1 轮已消除该尾随空格，首轮的 stdout 不重造。

检查（r1 修订轮，三文件）：`git diff --name-only b27c0c4` 恰为"本计划 + 交接 MD + 交接 JSON"三条路径，
全部在白名单内；`git diff b27c0c4 -- ':!reports'` 为空（生产/HAL/内核/四宏/`tests/host`/Keil 工程/
`.gitignore` 零改动）；`git diff --check b27c0c4` **rc=0**（写入本节数字后复跑仍为 rc=0）；JSON 由
`json.load` 解析成功，新增 `review_resolution` 六项（R1/R2a/R2b/R3a/R3b/F1）逐项映射"修订位置 →
剩余前提"，`tests_executed` / `builds_executed` / `hardware_actions` 仍是空数组；本轮新增引用的源码行
已逐条回读（`project.uvprojx:503/:505`、`context_rvds.S:22-23/166/189-194`、`NS800RT7P65x.h:301`、
`drv_common.c:45/:49`）；四宏回读 `RT_FALSE`；隐私扫描无本机绝对路径/用户名/邮箱/主机名；
冻结引用（main / phase7 / 标签对象 / 解引用）远端回读未动。

不做：测量插桩实现、构建、主机链重跑、运行既有采集器（含 `sw1_cut.ps1`）、端口/设备扫描、
串口/调试器/仪器连接、烧录/读 Flash/复位、真实 GPIO/TMC/STEP/DIR、电机、供电、接线、SW1、
真机清故障、selftest；也不调用会在启动时探测硬件的脚本来"查看环境"。r1 轮同样**不**配置 MCO、
**不**新增标记 GPIO、**不**落地插桩、**不**制作或构建镜像。

状态：`TARGET_MEASUREMENT_PREP_REVISED / AWAITING_INDEPENDENT_PREP_REVIEW`；
`NO HARDWARE AUTHORIZATION`；`FOUR MACROS RT_FALSE`；板上 `USER_REPORTED_E40`。

## 11. 准备复审通过（PREP_REVIEW_GO）与 A0 实现轮的覆盖边界

**2026-10-25 独立复审结论：`PREP_REVIEW_GO`，接受 `bbf66ba34d7b31a217c643921aa4192df2f59a56` 的离线
测量准备计划；R1/R2a/R2b/R3a/R3b/F1 关闭，无新增必须返修项。** 复审端只做了 Git/源码/文档静态核对
（远端 `ls-remote`、`b27c0c4→bbf66ba` 恰 3 条白名单路径 +494/−72、与 `bcdec631` 相比代码/脚本/工程
差异为空、三个 blob 逐字节复算一致、JSON 与 `review_resolution` 六项映射、`git diff --check` rc=0、
四宏与冻结引用回读），**未构建、未执行测试链、未操作硬件**。SDK-LOCAL 的具体内容仍是本地报告，
未升级为云端逐文件验真。

继承关系不变：S2 `SOFTWARE_REVIEW_GO` 仍只接受 `bcdec631…` 的既有软件与主机合同；
本 `PREP_REVIEW_GO` **不等于**新的测量代码 GO，也**不等于**目标机执行 GO。

**A0 实现轮（`phase8-target-measurement-a0`，任务书 `NS800_Prep_R1_Review_GO_and_A0_Firmware_Task_bbf66ba_20261025.md`）
把本计划的哪些格子从"纯计划"推进到哪一步：**

| 计划条目 | A0 之后的实际状态 | 仍然开放的部分 |
|---|---|---|
| §4 插桩设计与 `I-1`/`I-2` 形状 | 已在**测量专用编译变体**里实现：`APP/sp_measure_a0.{c,h}` + `APP/stop_priority.c` 三个入口的 measured-enter/exit（原始四时间戳、同窗逐字段钉快照、有效性判定、64 槽单批次只追加、`spm` 命令族） | 目标机上从未运行；`body/包围区间`没有任何真实数值 |
| T00（时钟/执行者/计数资源核对） | 检查**逻辑**已实现并由主机链验证（`spm init` 读 `NOCYCCNT`、只置 `TRCENA/CYCCNTENA`、有限次确认递增、记录 `LOAD` 与 CTRL 配置） | 目标上的 `counter_status/clock_basis` 真值仍未知；未到 `EXTERNAL_VERIFIED` 前仍不输出"实测 µs" |
| T02（短窗口路径） | 主机链 TMA01–TMA18 真执行（含 `COUNTER_UNAVAILABLE/UNCHECKED`、回绕、COUNTFLAG 重装、配置漂移、七出口矩阵、`out==NULL`、在途 BUSY、批次满、命令门） | 目标 RT-Thread 调度、真实 PPB 行为、物理停机仍**不是**本链能证明的；窗口时长与 RX/EXTI 丢失容忍保持 `ACCEPTANCE_LIMIT_TBD` |
| `B-1` 窗口时长 | 记录能力与"什么算有效样本"的判定已可审 | 实际分布仍未测；SysTick 后备路线按 §2.5 保守降级（`BLOCKED_COUNTER`）不变 |
| `M-1/M-2/M-3` | 未推进：本批不插桩慢整理锁、不新增标记 GPIO、不做跨时间基关联 | 全部按原口径继续开放（`M-3` 的"寄存器写→焊盘"部分尤其如此） |
| T03–T13 与包 B/C | 未覆盖（本批明确只取关闭路径 A0） | 继续 `NOT_EXECUTED / NOT AUTHORIZED` |

默认发布配置的绕开性是**可判定**的，不靠措辞：同一份夹具源码编译出 `a0_off`（开关 0）与 `a0_on`
两个可执行体，符号表核对显示 `a0_off` 里生产记录器符号数与 64 槽数组均为 0，而三个被测入口符号仍在；
`a0_on` 分别为 22 与 1。四个目标安全宏在所有变体里继续 `RT_FALSE`，测量开关不参与任何安全判定。

本轮**没有任何目标机动作**：未连串口/调试器/仪器、未扫描设备、未读 Flash、未烧录/复位、未运行目标
命令、未做 GPIO/TMC/STEP/DIR、电机、供电、接线、SW1、真机清故障或 selftest，也未运行旧 `sw1_cut.ps1`。
镜像即使构建成功，也必须等独立复审与用户对具体板级操作包的另行批准之后才可使用；本批的
`reports/plans/phase8-target-measurement-a0-operator-pack.md` 只是待审清单，不构成任何授权。

## 12. A0 独立复审结论与 r1 返修的覆盖映射

**2026-10-25 独立复审（审查对象 `c5028f4`，其 `code_commit` `6787687`）结论：
`A0_REVIEW_REWORK_REQUIRED` —— 当前 A0 测量镜像与操作包不予放行。** 常规链确实通过；复审又发现了
常规链没覆盖的命令集成、计时判据与批次顺序缺陷，以及新 runner 的反向台账漏检。
既有 S2 `SOFTWARE_REVIEW_GO`（`bcdec631`）与 `PREP_REVIEW_GO`（`bbf66ba`）**不撤回**：该轮没有发现
默认关闭配置下的 S1/S2 合同回归。A0 测量代码至今**未**取得 `SOFTWARE_REVIEW_GO`，两份镜像也**没有**
板级使用许可。

返修轮 `phase8-target-measurement-a0-r1` 的处置（§11 保留为上一轮的原样记录，不重写）：

| 复审项 | 修订位置 | 现在可判定什么 |
|---|---|---|
| A0-01 命令名与参数合同 | `APP/sp_measure_a0.c` 的 `spm_validate()`/`spm_cmd()` 与文件末尾 `MSH_CMD_EXPORT_ALIAS(spm_cmd, spm, …)`；新增 `tests/host/sp_measure_a0_finsh.h` | 目标上的命令名逐字是 `spm`（由 AXF 里 `__fsym_spm` 的 name/desc/opt/func 四字段实证，见操作包 §1）；主机链经**名字查表**分发，不再直接调 `spm_cmd()`；缺参/多参/未知子命令/非法 entry 都在任何计数读取、状态变化与关闭动作之前拒绝，合法上下文可打印、非法上下文静默（TMA19/TMA20） |
| A0-02 计时判据（错误接受） | `spm_ctr_pair()`/`spm_ctr_pair_after()`、`spm_win_note()`/`spm_body_end()` 采逐样本门控、`spm_eval()` 单点裁决 | `counter_status` 降级为**能力缓存**；每条样本自己核起点/终点 `DEMCR.TRCENA` + `DWT_CTRL.CYCCNTENA`（新列 `gates`），缺任一位记 `0x0800`；`body_end==body_begin` 记 `0x0080`，原始值保留，绝不作为 0 耗时入池（TMA21） |
| A0-02 计时判据（错误拒绝） | `spm_win_note()` 里 `SPM_IV_FLAG_PRE` 退役为观测位 `SPM_GD_FLAG_PRE_SET`；`spm_init_counter()` 增加 `spm_quiescent()` 门 | 起点读已读清的历史 COUNTFLAG 不再单独否定当前主体（TMA23 正向对照），而真重装/回绕/配置漂移/嵌套仍拒绝；`init` 只在静止态（IDLE、无在途、无旧批次、无人持判据）允许，非静止态零 PPB 访问且不换能力缓存（TMA22） |
| A0-03 操作包 | `reports/plans/phase8-target-measurement-a0-operator-pack.md` 全篇 | 分批闭环"start → N 次 → stop → status(确认无在途) → dump(核对并**先**保存) → clear_records → 下一批"；停止判据不再把 `COUNTER_OK + CONFIG_READBACK + body_valid=1` 的合法组合当故障；判据字段改用**实际存在**的 `body_valid`/`enclosing_valid`（无 `sample_valid` 列）；P-13 只用正式 `motor_disarm`，`spm trip` 不作 `sp_off_attempt_for` 的等价触发，不可达入口记 `NOT_REACHED`；三条命令副作用按源码更正（TMA24 用真实控制函数跑 1 条与 16 条两批） |
| A0-04 反向台账 | `tests/host/run_sp_measure_a0.sh` 的 `ledger_check()`（正向与反向**共用**）与新增判定器负控 | 缺席/重复/未登记身份/CASE 与 RESULT 不成对/SUMMARY 与 EXIT 不自洽/退出码不符一律 INFRA-FAIL；有预期 FAIL 的组必须恰好 `rc=1`、其它组必须 `rc=0`（不再接受 `rc<=1`）；"故意省略非锚身份 TMA18 的 RESULT 行"的负控现在会被自己判成 INFRA-FAIL，而不是报成"其余保持 PASS" |
| §7 取证精度 | 同一轮 | `code_commit` 改成**已含完整双目标工程状态**的 `b27e5f14…`（不再有"工程改动在另一个提交"的含混表述）；manifest 分开 Git blob 身份 / LF 内容散列 / 实际工作区原始散列与行尾信息；`keil_builds.txt` 汇总段的 `spm_rec 76800 B` 笔误以勘误层标注（不改原日志数值）；原证据段落全部保留，新结果只追加为 `r1` 层 |

**仍然开放**：A0 测量代码与两份 r1 镜像等待独立复审；`B-1`、`BUG-008`、`RISK-03/03a`、物理
`RISK-04-b`、`M-1/M-2/M-3` 与 T03–T13、包 B/C 不变。本轮同样**没有任何目标机动作**。

## 附录 A 引用与散列（LOCAL_ONLY）

SDK/CMSIS 文件仅本地存在，未上传；下列 SHA-256 取前 16 位十六进制用于比对（同一目录树内可复核）：

```text
5214f848d1f94c1b  packages/CMSIS-Core-latest/Include/core_cm7.h        (F-19/F-20/F-11)
8485f07a5863e0c9  packages/novosns-series-latest/NS800RT7XXX/StdDriver/Src/rcc.c   (F-02/03/06)
a1b5a88f8c3db2db  packages/novosns-series-latest/NS800RT7XXX/StdDriver/Inc/rcc.h   (枚举值/HXTL 档)
521d4baa2548e20a  packages/novosns-series-latest/NS800RT7XXX/Device/Inc/NS800RTxxxx.h (HXTL_FREQ_VALUE)
391a3c7174f8c7a0  packages/novosns-series-latest/NS800RT7XXX/Device/Inc/NS800RT7P65x.h (F-11 __NVIC_PRIO_BITS / F-19 include)
dbc14cc70cc74efb  packages/novosns-series-latest/NS800RT7XXX/StdDriver/Src/uart.c  (F-09)
807f8d5ad5414c69  BSP/board/board.h        93fc3dca86aa173b  BSP/board/board.c
702e18391bf39cc6  BSP/board/SConscript     d0ffc46154225dd7  BSP/rtconfig.h
179bf4c9704d17a2  BSP/project.uvprojx      5a892ce3d778745c  LIB/HAL_Drivers/drv_common.c
79f81eda809d42fe  LIB/HAL_Drivers/drivers/drv_epwm.c
0d2dd626d3d8a69f  LIB/HAL_Drivers/drivers/drv_epwm_leaf.inc
95dd1a001dabf540  LIB/HAL_Drivers/drivers/drv_uart_txleaf.inc
0478d948bad19d0f  LIB/HAL_Drivers/drivers/drv_uart.c
ebaa5b1c9710a2e7  BSP/applications/motor.c 319f21a2e33c1237  BSP/applications/tmc2209.c
```

## 附录 B 编号对照

本文沿用既有草案体系而不新造判据：`PLAN-01..04`（事实继承 / 命令副作用分级 / 时延三段 / 前置改判据）、
`A1..A32`（事实表 + 来源等级 `[S][G][DS][U][M][D][?]`）、`X-1..X-5`（冲突并列登记，不择一采信）、
`H-0..H-13` 与供电态 `S0..S3`（有序清单）、`P-1..P-7`（现场待填）、`D-1..D-5`（缺口）、
`RISK-03/03a/04-a/04-b`、`BUG-008/009`、`B-1/B-4`、`M-1/M-2/M-3`、停止条件词表
（`NOT EXECUTED / NOT_TESTED / NOT_VERIFIED / BLOCKED_METHOD / BLOCKED_INSTRUMENT /
BLOCKED_POWER_STATE / DISABLED_BY_CONFIG / DEFERRED / HOLD / USER_CONFIRMED /
METER_NOT_MEASURED_THIS_RUN / ANALOG_NOT_VERIFIED`）。
本文新增的编号是 `T00..T13`（未来执行矩阵）、`F-01..F-24`（本地事实清单）、`I-1..I-7`（插桩点），
以及 §2.5 的取值词表 `clock_basis = SOURCE_ASSUMED / CONFIG_READBACK / EXTERNAL_VERIFIED`、
`counter_status = COUNTER_OK / COUNTER_UNAVAILABLE / COUNTER_UNCHECKED`、`sample_valid`、
`invalid_reason` 与停止条件 `BLOCKED_COUNTER`（计数资源不可用 ⇒ 该场景不产出有效时长样本）。
既有草案的现场待填仍叫 `P-1..P-7`，与插桩点**不是同一套编号**：引用时必须写全前缀名
（"插桩点 I-3" / "现场待填 P-5"），避免与草案 `P-5 断电判据` 撞号。
