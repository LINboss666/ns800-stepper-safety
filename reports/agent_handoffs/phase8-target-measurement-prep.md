# phase8-target-measurement-prep —— 目标机测量与集成验收准备（离线，零执行）

日期：2026-10-25（Asia/Shanghai）。任务书：
`NS800_Target_Measurement_Prep_Qoder_Task_f919a5c_20261025.md`。
性质：**离线准备任务**。本轮测试执行 = 0，硬件动作 = 0，构建 = 0。

## 1. 只读核对（开工前）

| 项 | 值 |
|---|---|
| 本地 HEAD = 远端 `phase8/tmc-drive-config` | `f919a5c05fbee5457c768d5c1a4ba46c41a9dbfc` |
| 工作树 | clean（`git status --short` 空） |
| `bcdec631..f919a5c` | 8 份文档 +478/−16（与任务书 §0 一致；生产、夹具、shim、runner、Keil 工程输入未变） |
| `f919a5c` 之后是否有新提交 | 无（未发生他人前进） |
| main / phase7 / 标签对象 / 解引用 | `1b3757ee…` / `6d3fbc55…` / `097935fe…` / `e40a9b95…`，均未动 |

未执行 reset / rebase / force / merge，未覆盖任何用户未提交内容。计划提交不自引用本轮 SHA。

## 2. 本轮产出（严格四文件）

1. 新建 `reports/plans/phase8-target-measurement-plan.md`：§2 本地事实清单（`F-01..F-24` + 信号表 +
   电机/诊断参数）、§3 五段分段、§4 插桩设计与 `I-1..I-7` 插桩点表、§5 预算/`ACCEPTANCE_LIMIT_TBD`/
   取样方案、§6 未来执行矩阵 `T00..T13`（含 5 条特殊判据）、§7 三个待授权包 A/B/C、§8 证据模板与
   风险关闭对应、§9 未决三组、附录 A 引用与散列（LOCAL_ONLY）、附录 B 编号对照。
2. 本交接 MD。
3. 同名 `.json`（机器可解析的身份、许可范围、用例状态、未决字段）。
4. 仅改 BSP `待办事项.md` 中上一轮新增的 S2 完成段（见下节）。

未新增脚本、测试平台或任何测量补丁；伪码只写在计划 Markdown 内。

## 3. §3 措辞收敛（不改产品）

BSP `待办事项.md` 的 S2 完成段原写"①无界等锁（`ss_lock`/`mot_lock`/TMC 配置事务/UART xfer 的
`RT_WAITING_FOREVER`）改为带上限"，**超出实际实现**。已按任务书 §3 收敛为：S2 收口的是**结构性保护
顺序**——短保护排在慢整理锁之前、开启提交与恢复用原身份门；而 `ss_lock`/`mot_lock` 仍是
`RT_WAITING_FOREVER`（源码事实：`safety_state.c:157/:207/:251`、`motor.c:143/:1756`，另有
`ns_flash.c:87/108/347/413`、`ns_storage.c` 多处、`project_config.c:32`），TMC 只有**外层 txn** 是
非阻塞竞争，cfg/xfer/UART 才用分段与总预算，且 **UART 完整性复核仍在 Motor 斜坡线程内**
（`motor.c:661-728`，只在 `mot.armed` 时按 200 ms 节奏、在 `mot_lock` 外执行，无独立 UART 守卫线程）。
同段补回"协调层身份门 + 先保护后日志"这一确已实现的部分，避免收敛时丢信息。
**不撤回软件 GO，不改锁策略、不加线程、不重跑测试**；未批量翻修其它历史段落。

## 4. 只读调查得到的主要新事实（全部进计划的 §2）

- **时钟链存在本地自相矛盾**：寄存器配置选 `RCC_HXTL_24M_AGC_DISABLE`（该枚举注释 24 MHz），
  而所有频率计算用 `HXTL_FREQ_VALUE = 20000000UL` ⇒ 代码推导的 400 MHz SYSCLK / 200 MHz HCLK/PCLK1
  依赖"晶振实为 20 MHz"这一未证前提；`project.uvprojx:21` 的 `CLOCK(12000000)` 只是 IDE 字段。
  ⇒ 任何 µs/ms 换算在未来必须先做 T00 级核对（读回 `SystemCoreClock`/TBCTL/MCO）。
- **UART 功能时钟未知**：驱动按 `PCLK2` 算波特率（`uart.c:44-45`），而 `board.c:252-256` 把 UART1–4
  使能在 APB5（=50 MHz）⇒ 影响 T04 的位时间/帧时长物理含义；SPI 同类（`spi.c:49`）。
- **EPWM 有两处分频写入来源**：冷启动 `hw_init`（`CLKDIV_1/HSDIV_2`）与驱动配置表
  （`UP_DOWN/CLOCK_DIVIDER_2/HSCLOCK_DIVIDER_1`），后者在配置提交时覆盖 ⇒ 换算必须回读 TBCTL 位。
  `SYSCON_setTbClkSync`（`drv_epwm.c:444/466`）会整体影响所有 TB 计数 ⇒ 归入 M-2，本地不能关闭。
- **周期计数器**：`DWT/CYCCNT/TRCENA` 在编译清单内的 CMSIS `core_cm7.h` 有定义，工程零启用代码，
  **芯片是否实现只能运行时读**（`DWT_CTRL.NOCYCCNT`）；无 `PMCCNT/PMU` 定义。已给替代方案
  （`SysTick->VAL` 差分，量程 1 tick；或 tick+`ps`+黑匣子只给 ms 量级），并规定"未证时钟只报 cycles"。
- **单核与无 DMA 已可离线确定**：`DUAL_CORE_ENABLE=0`、`RT_CPUS_NR 1`、`sema.c/mu.c/mcm.c` 编译但
  零调用点；无任何 DMA 配置（`RT_SERIAL_USING_DMA is not set`、HAL 无 `drv_dma.c`）⇒ 未知项收敛为
  "出厂是否另烧 CPU2 半图"（`IROM2 0x08080000` 预留）。
- **观测接口现状**：内核无 `rt_thread_stat`，未开 `RT_USING_STAT`/`CPU_USAGE_TRACER` ⇒ `ps` 的
  usage 列恒 `N/A`，只有栈高水位 `max used%`；console 是 uart1（PA12/13），与 TMC 的 uart2 不同外设。
- **信号写入者已穷举**：PA.0/PA.1 只经 EPWM；PA.2 唯一写点 `motor.c:1488`；PC.23 的**唯一 HIGH 生产点**
  是 `stop_priority.c:810`；PC.6 输入且有 fail-close（禁止被配成输出）、**不注册 IRQ**；
  `pf21_diag`/`pf21_ab` 会真的驱动已弃用 PF.21 ⇒ 已列入未来命令**排除清单**。
- **阈值全面缺约定**：窗口时长、RX/EXTI 丢失容忍、STEP 频率/占空比公差、物理停止延迟、
  诊断检出/误报率均无项目约定 ⇒ 一律 `ACCEPTANCE_LIMIT_TBD`；并明确禁止把实测最大值当阈值、
  禁止"200 ms + 250 ms ⇒ 450 ms 最坏上界"。

## 5. 来源等级与继承纪律

云端实际执行（S1/S2 正反向、叶子四组、20006 次数值检查、上下文机制探针）与 `AGENT_REPORTED`
（V1V2、I/O、负例、元负例、Keil、N7 同负载 A/B）在计划里分列，不混写。板上身份仍是
`USER_REPORTED_E40`（未读回 Flash），旧 e40 命令语义（含 `tmc_uart_probe` 可能写回 GCONF）不移用。
既有 `USER_CONFIRMED` 静态电气与 SW1 结论保留，本轮不重复索取，也不把五点电压重测当准备前置。
SW1 端子/位置对应仍是待核对（草案 X-5），不判"接反"。

## 6. 本轮未做（逐条）

未做：生产/HAL/内核/宏/`tests/host`/Keil 工程/`.gitignore` 任何改动；测量插桩实现；任何编译
（含 Keil）；任何主机链重跑；运行 `sw1_cut.ps1` 或其它既有采集器；端口/设备扫描；串口、调试器、
仪器连接；烧录、读 Flash、复位；真实 GPIO/TMC/STEP/DIR 操作；电机、供电、接线、SW1 动作；
真机清故障或 selftest；也没有调用任何会在启动时探测硬件的脚本"查看环境"。
未新增脚本或测试平台。未批量清理其它历史段落。

## 7. 未决（三组，不让其阻塞离线交付）

1. 当前可用仪器（示波器/逻辑分析仪型号、通道、采样与触发、探头与差分能力；万用表当前型号未知）
   ⇒ 缺则 `T06/T09` 与全部瞬态条目保持 `BLOCKED_INSTRUMENT`。
2. 现场供电/隔离与电机参数（VM 与逻辑电源来源及断电判据、模块可否拔出、电机铭牌额定电流、
   ADC 前端实测标定、ENN 线路连续性）⇒ 缺则 `T12/T13` 受阻。
3. 三个待授权操作包 A/B/C 的批准（镜像、宏例外、命令白名单与次数、仪器与接线前提）。

另登记两处**超出本轮白名单**的文档不一致（留待下次真实生产改动一并整理，不改文件）：
`stop_priority.h:23` 仍描述旧的"set 会抓 TBCTL 旧模式再写回"，与现码
`drv_epwm.c:293-318` + `drv_epwm_leaf.inc` 的"不写回计数模式"不一致；
`phase8-safety-chain-acceptance-draft.md:269/433` 引用的 `开发进度.md:33` 行号过期（实际 `:104`）。

## 8. 本轮检查

四路径白名单内改动；`git diff` 对生产/HAL/内核/宏/`tests/host`/工程为**空**；JSON 可解析；
引用路径与行号可核对；无本机绝对路径/用户名/邮箱/主机名；`git diff --check` 干净；
主机链与 Keil 均未重跑（本轮零执行，因此不声明任何新测试结果）。

## 9. 下一步

把计划与三包清单交用户/复审方审议；获得某一包的**具体授权**后再谈插桩实现与镜像制作，
且实现轮须单独走构建与主机回归 + Keil。本轮不自授任何硬件权限、不启动任何测量。

`STOP: TARGET_MEASUREMENT_PREPARED / AWAITING_INDEPENDENT_PREP_REVIEW；NO HARDWARE AUTHORIZATION；FOUR MACROS RT_FALSE；板上 USER_REPORTED_E40`
