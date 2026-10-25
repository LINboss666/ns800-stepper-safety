# phase8-target-measurement-prep —— 目标机测量与集成验收准备（离线，零执行）

日期：2026-10-25（Asia/Shanghai）。任务书：
`NS800_Target_Measurement_Prep_Qoder_Task_f919a5c_20261025.md`。
性质：**离线准备任务**。本轮测试执行 = 0，硬件动作 = 0，构建 = 0。

**修订轮 `phase8-target-measurement-prep-r1`**（同日）：响应独立复审
`PREP_REVIEW_CHANGES_REQUIRED`（任务书 `NS800_Target_Measurement_Prep_Review_and_Rework_b27c0c4_20261025.md`），
以 `b27c0c49118c52219383e36a032069d61fa43cfc` 为 document_base，只改计划 + 本交接 MD/JSON 三份文件，
落实 R1/R2/R3/F1。逐项映射与新的离线检查结果见 §10；§1–§9 为首轮原文，其中 §8 的一处检查声明
由本轮更正（原文保留并标注）。

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
引用路径与行号可核对；无本机绝对路径/用户名/邮箱/主机名；`git diff --check` 干净
（**这一条声明由 r1 轮更正**：复审方独立运行 `git diff --check f919a5c b27c0c4` 实得 **rc=2**，命中
`reports/plans/phase8-target-measurement-plan.md:82: trailing whitespace.`，即 F-20 表格行最后一个竖线后
多一个空格；本轮已消除该尾随空格，r1 轮自身的实际 rc 记在 §10.4。不重造旧 stdout、不改写历史结论，
只标注原声明与复审结果不一致）；主机链与 Keil 均未重跑（本轮零执行，因此不声明任何新测试结果）。

## 9. 下一步（r1 口径，取代首轮"待某一包授权后再谈实现"的表述）

本轮交付的是**可复审的计划修订**。它不含目标机指令执行、设备连接、烧录/复位/Flash 读写、
GPIO/TMC/STEP/DIR 操作、电机/供电/接线/SW1/真机清故障/selftest，也不含测量插桩实现或镜像构建。

复审通过后的顺序，**每一步都要另行下达任务书**：

1. **离线测量固件/镜像制作任务**（尚未下达）：产出可审的插桩补丁、完整构建输入、ELF/BIN 的 SHA-256、
   相对 `bcdec631` 的差异，以及**具体命令包**（烧录/启动/白名单命令/取样次数/停止条件）。该轮自己走
   构建 + 主机回归 + Keil，并把镜像散列交出去。
2. **拿到真实镜像散列之后**，才把实际烧录/启动/测量作为操作包 A/B/C 的具体内容交用户批准。
   **不能让用户先批准一个尚不存在的镜像**；本次文档修订也不推导任何软件实现权限或硬件权限。
3. 仪器、现场供电/隔离与电机参数仍是 §7 的三组未决，本轮不把它们升级为离线交付阻塞，也不重复索取
   五点电压或现有 SW1 静态确认。

首轮 STOP 原文保留（已被本轮 §10 末行的 STOP 取代）：
`STOP: TARGET_MEASUREMENT_PREPARED / AWAITING_INDEPENDENT_PREP_REVIEW；NO HARDWARE AUTHORIZATION；FOUR MACROS RT_FALSE；板上 USER_REPORTED_E40`

## 10. r1 修订轮（R1/R2/R3/F1）

### 10.1 开工只读核对

| 项 | 值 |
|---|---|
| 本地 `HEAD` = 远端 `phase8/tmc-drive-config` | `b27c0c49118c52219383e36a032069d61fa43cfc`（即复审对象） |
| 工作树 | 开工时 clean；未 reset/rebase/force/merge，未覆盖任何未提交内容 |
| 直接父提交 | `f919a5c05fbee5457c768d5c1a4ba46c41a9dbfc`（首轮 document_base） |
| `b27c0c4` 相对 `bcdec631` | 全部为文档变化（与复审 §2 一致），生产/HAL/内核/夹具/runner/工程/宏无变化 |
| main / phase7 / 标签对象 / 解引用 | `1b3757ee…` / `6d3fbc55…` / `097935fe…` / `e40a9b95…`，本轮复核仍未动 |
| 四宏源码回读 | `motor.h:69`、`motor.h:83`、`tmc2209.h:100`、`project_board.h:51` 全部 `RT_FALSE` |

### 10.2 R1 —— PendSV 优先级不再是"工程未设置"

计划 §2.1 的 F-11 与 JSON 对应事实项已改写。本轮自行回读的证据：
`BSP/project.uvprojx:503/:505` 把 `KERNEL/libcpu/arm/cortex-m7/context_rvds.S` 列进编译清单；
该文件 `:22` `NVIC_SYSPRI2 EQU 0xE000ED20`、`:23` `NVIC_PENDSV_PRI EQU 0xFFFF0000`（注释即
"PendSV and SysTick priority value (lowest)"）；`rt_hw_context_switch_to` 在 `:166`，其 `:189-194`
对该寄存器做读—OR—写回；`LIB/HAL_Drivers/drv_common.c:49` 的 `NVIC_SetPriority(SysTick_IRQn, 0x0F)`
是更早的 C 侧设置，两条路线同指最低档；`__NVIC_PRIO_BITS = 4`
（`SDK-Device/Inc/NS800RT7P65x.h:301`，附录 A 已补该文件散列）。
**剩余前提**照原样保留：`SHPR3` 的运行期读回值、`0xFFFF0000` 的逐字节归属、其它 IRQ 的优先级分配
都未核对；不猜数值、不改内核。

### 10.3 R2 —— 计数器可用与时钟可校准分开，SysTick 路径保守降级

新增计划 §2.5（`clock_basis` / `counter_status` / `sample_valid` / `invalid_reason` 的统一词表与判据），
并把 F-20、§3 短保护段、§4 纪律第 5 条、§4 替代方案、§6 T00/T02、§8 模板与 B-1 行、JSON 的
F-19/20/21/22 事实项与 U-3 全部改成同一口径：

- 计数器**已证可递增**而频率未证 ⇒ 可报原始计数差，不得报已校准 µs；
- 计数器**未实现/未启用/不递增** ⇒ `COUNTER_UNAVAILABLE`（未检查则 `COUNTER_UNCHECKED`），**没有**有效
  计数差，零值不等于零耗时，也不得作为有效 cycles 参与统计；
- **本批选定保守降级**：DWT 不可用时 SysTick 差分**不产出用于 `B-1` 的有效窗口样本**，登记
  `BLOCKED_COUNTER`，只保留状态与顺序观测。§2.5 附单圈混叠的数学反例（`N = LOAD+1`，`VAL 300→200`
  对 `100` 与 `N+100` 个计数不可区分），并写明 `LOAD` 与模数 `LOAD+1` 的区别、读状态的副作用要求、
  以及"不得重配 SysTick / 不得用忙等验证被测窗口"（`rt_hw_us_delay` 本身就是同类忙等，不能自测）。
  32 位 DWT 差分同样补允许区间与回绕有效性：无法判定或超上界的样本判 `sample_valid=0`。
  本轮不写代码、不加探针，反例只是文档中的数学推演。

### 10.4 R3 —— 配置读回不是独立时钟证明；跨时间基关联仍未闭合

`F-02/F-03/F-04/F-06`、`F-09/F-10`、T00、T06、§8 模板与规则、§9 组 1/组 3 与 JSON 的 U-1/U-2 统一改为
三层 `clock_basis`：软件值互相读回一致只到 `CONFIG_READBACK`（因为都从同一个未确认的
`HXTL_FREQ_VALUE` 算出，不构成外部晶振或实际核心频率的证明）；`EXTERNAL_VERIFIED` 必须是"确认路由 +
分频 + 测点 + 独立时基测量"，而 MCO 的引脚复用与接线是单列的未来授权动作。UART/SPI 明确把**外设使能域**
与**功能时钟来源**分开登记，只写"来源待确认"，**不**推成"波特率必错 4 倍"。
R3b：§6 特殊判据 2 重写，M-3 关闭行与 T06/T09 观测点同步——只有设计出可识别的**共同事件/标记**、
给出对齐方法与误差来源（模板新增 `timebase_linkage` 字段）才允许报"寄存器写→焊盘"的时间关系；当前
**没有任何标记 GPIO 获批准**，因此只报 SW1/许可/ENN/STEP **之间**的电气时序与残余边沿，MCU 的
`__DSB`/cycle 记录单列，M-3 的该部分继续开放。JSON 新增 `U-10` 登记这一未决。

### 10.5 F1 与本轮离线检查

F-20 表格行末尾竖线后的那个空格已随该行整体重写消失。交接 MD §8 保留首轮原句并就地标注其被本轮更正；
JSON 的 `offline_checks` 里对应条目改为"已作废 + 复审实测 rc=2 + 指向 `revision.offline_checks`"。
不重造首轮的 stdout，不改写历史结论，也不为格式项重跑任何测试或 Keil。

本轮实际执行的静态检查（全部离线，零执行）：

| # | 检查 | 结果 |
|---|---|---|
| 1 | `git diff --name-only b27c0c4` | 恰 3 条路径：计划 / 交接 MD / 交接 JSON（全在白名单内，无第四文件） |
| 2 | `git diff b27c0c4 -- ':!reports'` | **空** ⇒ 生产、HAL、内核、四宏、`tests/host`、夹具、runner、Keil 工程、`.gitignore` 零改动 |
| 3 | `git diff --check b27c0c4` | **rc=0**（写入本表数字后复跑仍 rc=0；与首轮被复审证伪的那句已分列） |
| 4 | JSON `json.load` | 解析成功；顶层 32 键；`review_resolution` 六项 = R1/R2a/R2b/R3a/R3b/F1；`tests_executed` / `builds_executed` / `hardware_actions` = `[]` |
| 5 | 新增引用逐条回读 | `project.uvprojx:503`（FileName）/`:505`（FilePath）、`context_rvds.S:22/:23/:166/:189-194`、`NS800RT7P65x.h:301`、`drv_common.c:45/:49`、`board.h:198`、`core_cm7.h:2307-2312` 全部命中 |
| 6 | 四宏源码回读 | `motor.h:69`、`motor.h:83`、`tmc2209.h:100`、`project_board.h:51` 均 `RT_FALSE` |
| 7 | 远端 `ls-remote` 回读 | `phase8/tmc-drive-config` = `b27c0c4…`（开工时仍是本轮基线，无他人前进）；main / phase7 / 标签对象与解引用未动 |
| 8 | 隐私扫描 | 三文件内无本机绝对路径、用户名、邮箱、主机名；SDK 引用仍是 LOCAL_ONLY + 散列 |

未做（延续首轮并加细）：任何目标机动作、任何构建与主机链、插桩实现、MCO 配置、GPIO 标记新增、
镜像制作；本轮不新增脚本、不新增测试平台（修订用的临时脚本留在仓库外/忽略目录）。

### 10.6 边界

S2 `SOFTWARE_REVIEW_GO` 不撤回、不升级，接受对象仍是 `bcdec631`；本轮无任何生产/夹具/工程/宏改动，
`tests_executed = []`、`builds_executed = []`、`hardware_actions = []` 保持真实。板上继续
`USER_REPORTED_E40`；`B-1`、`BUG-008`、`RISK-03/03a`、物理 `RISK-04-b` 与 `M-1/M-2/M-3` 按各自
**实际未关闭部分**保留（M-3 的"写入→焊盘"部分本轮明确继续开放）。

`STOP: TARGET_MEASUREMENT_PREP_REVISED / AWAITING_INDEPENDENT_PREP_REVIEW；NO HARDWARE AUTHORIZATION；FOUR MACROS RT_FALSE；板上 USER_REPORTED_E40`
