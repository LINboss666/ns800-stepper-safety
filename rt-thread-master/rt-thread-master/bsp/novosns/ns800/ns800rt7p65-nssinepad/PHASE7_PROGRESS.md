# Phase 7 进度

> 每轮 Phase 7 工作结束后更新本文件。

## 当前阶段

**Phase 7 软件 A/B/C/D 写完 + Qoder 接管审计 + Fix A/B/C 修复完成** —
全部为 **STATIC / SOFTWARE-VERIFIED**，**尚未在开发板上执行**（on-target 冒烟
HARDWARE-PENDING，deferred to evening on-target validation）。

⚠ 重要事实修正：`d47b1d9` 时 Phase 7 业务 runtime **实际上从不启动** ——
`supervisor_boot()` 已实现但没有任何调用者（main 未调、业务 INIT_APP 已删），
且 `safety_irq_attach()` 既无调用者也无 MSH 命令，四路安全输入在运行时
没有任何软件响应路径。由 `a94e150`(Fix A) 修好。见下文
「接管审计声明核实」与「Qoder Fix A/B/C」两节。

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
- ⚠ 已记录风险（2026-09-25 接管审计修正，下列三点的实际后果见说明）：
  ① INIT_APP 执行顺序依赖链接顺序 —— 实际后果远重于"依赖顺序"：业务 INIT_APP
     删除后 `supervisor_boot()` 无人调用，Phase 7 runtime 从不启动（Fix A 已修）；
  ② ss_state 转换无锁 —— Round 2 的 ss_lock 属实，已落实；
  ③ sensor 线程在 TMC 失联时每 10 帧有 100ms 超时占用（降速不阻塞）—— 仍成立

## 未完成项（后续阶段）

- Phase 7 全阶段遗留：on-target 冒烟（板未连 USB，NOT EXECUTED）；
  ESTOP EXTI 实测；diag/blackbox selftest 上板执行
- 硬件联动：电机+ADC 联合测试（四级方案已备）、使能链验收、INT1、
  STEP 示波器验收、ENN 浮空实验、标定流程
- Phase 7-D：黑匣子（RAM ring + ns_storage 落盘）、全链路联调、对照实验准备
- IMU INT1 中断功能验证（EXTI5，线已接）
- SPI2/SPI4 硬件验证（BUG-013 候选 mux 组，仅静态核验）
- **Fix A/B/C 的 on-target 验证全部未执行**（HARDWARE-PENDING，deferred to
  evening on-target validation）：`[BOOT] 1..11` 真实顺序、safety_irq_* 人工门、
  DRV_ENABLE 写+回读、motor_* 命令、runtime_selftest、diag_selftest 新用例、
  blackbox post 落盘与丢弃计数、config MEASURED 重启保持、current_raw 实数。
- 代码侧遗留（非本轮范围）：没有任何流程把 `cur_cal_source` 写成 MEASURED，
  真实零点/增益标定命令待建；扩展板 RevB 补 MCU_DRV_ENABLE 网络后才能验收 ENN 链。

## 最新 commit

- 分支 `phase7/full-embedded-software`
- main 分支停在 1b3757e（未 merge，禁止 merge）
- phase7/full-embedded-software 分支：
  - Phase 7-A：fa08d9c（API）+ a33b64b（文档）
  - Phase 7-B：8548ccc（motor+sensor）+ c90792a（safety thread+状态机+supervisor）
  - Phase 7-C：9334d2b + 86e09ee；Phase 7-D：aa24534 + ac32300
  - 审查修复轮：e9c47c7（R1）+ d47b1d9（R2）
  - **Qoder Fix A/B/C：a94e150 + 6662194 + f0c4ea9（本轮，详见文末）**

## Build 状态

- ⚠ 口径修正（2026-09-25）：此前记录的"0 error / 0 warning"来自 **incremental
  build**（日志里只有 `compiling blackbox.c` + link），不能证明全量无警告。
- 本轮每次改动都做真实 clean rebuild（删 `build/` 后 `UV4 -r` = Rebuild all）：
  - Fix A `a94e150`：0 Error 0 Warning，Code=125818 RO=42878 RW=2200 ZI=83204
  - Fix B `6662194`：0 Error 0 Warning，Code=126730 RO=43498 RW=2200 ZI=83204
  - Fix C `f0c4ea9`：0 Error 0 Warning，Code=127330 RO=44302 RW=2200 ZI=83212
    （Fix C 过程中 clean build 暴露了一条 `-Wcomment`：注释里写了 `build/*.map`，
     其中的 `/*` 被当作嵌套块注释起始 —— 正是 incremental 口径会漏掉的那类问题）
- 烧录冒烟：Phase 7 全程 NOT EXECUTED；本轮按指示不做任何硬件动作

## Hardware pending

- **Fix A/B/C 全部逻辑的上板执行（统一标注：HARDWARE-PENDING — deferred to
  evening on-target validation）**：`[BOOT] 1..11` 真实顺序与 state 终值、
  `runtime_selftest`（含 ⓪ bootstrap 完成度 / ② 四门掩码 / ⑥ 收尾仍拒绝 arm）、
  DRV_ENABLE 写+回读在真机上的稳定性、`safety_irq_*` 人工门与回滚路径、
  `motor_*` MSH、`diag_selftest`（3b/4c 新用例）、`blackbox_selftest`
  （post 是否真落盘、pre/post 计数、两个丢弃窗口计数）、
  `config_save`→复位→`config_show` 的标定来源保持、`current_raw` 实数
- 电机+ADC 联合测试（四级方案已备，待用户下发；前置 4 项确认见 待办事项.md）
- J4-21（PC.23）万用表终验（≈0V）
- 安全输入（ESTOP 常闭+上拉 / LIMIT GND 跳线占位）接线与验证
- IMU INT1 中断验证
- ENN 浮空行为实验（先拔 VM 再拔 ENN，看 IOIN bit0）
- STEP 频率示波器验收（无仪器，替代=TMC 回读/低速信任法）
- 电流标定流程：需已知负载才能产生 MEASURED 来源（Fix B 已打通持久化与还原，
  但**没有任何代码路径**会写入 MEASURED，待标定命令与真机条件）

## 真机验证事实库（勿重测、勿降级）

- Flash：spi1，JEDEC EF 40 17，unlock/test/verify PASS
- TMC2209：uart2，READ FOUND（IOIN=0x21000041 VERSION=0x21）+ WRITE IFCNT+1 ALIVE；
  4 帧应答 CRC 独立核算一致；上电基线 GCONF=0x101/CHOPCONF=0x15010053
- ADXL345：spi3（BSP fix 后），DEVID=0xE5 双读一致，静置合成 ≈1g
- 安全停机链：safety_force_shutdown → FAULT_LATCHED → 实测故障源 → MANUAL_CLEAR（ss_test）
  ⚠ 该条是 Phase 7-D 重构**之前**的实测记录（当时状态机还挂在 INIT_APP 上）。
  Fix A 之后 force_shutdown 内部改为"停 STEP + 使能脚写+回读 + 独立复核"，
  且 `fault_reset` 新增"使能脚未确认 LOW 则拒绝清除"前置 —— 故本链条需在上板时
  **重新跑一次 `ss_test`/`runtime_selftest` 才算当前代码的 BOARD-TESTED**。
  在此之前不得把它当作 Fix A 后的验证结论。

---

## Phase 7 独立审查修复轮（GPT review @ac32300 → 本轮修复）

### P0（全部修复）
1. **blackbox 确定性越界**：旧实现把 pre 200 帧拷进 bb_post[100] 数组。
   重构为单一捕获区 bb_cap[450](编译期断言) + 独立 pre 环 300；
   pre 不足只写实际帧数（开机前 2s 不造假历史）；触发策略=捕获/写盘中
   新触发忽略并计数（首个故障优先）；selftest 竞态修复（等待 session
   递增且回 IDLE，非轮询瞬时状态）。
2. **sensor_frame 栈垃圾**：s_collect 基帧=显式上一帧（静态 s_prev，零初始化）；
   新增 fresh_imu/fresh_current/fresh_sg 位区分"本帧采样"与"保留值"；
   失败源 valid=0（垃圾可留在值字段但不得标有效）；
   sensor_selftest 用 0xAB 模式注入验证 valid/fresh 行为。
3. **LIMIT/DIAG 生产者缺失**：safety_irq_attach 一次性注册四路 EXTI
   （ESTOP=EXTI6/LIMIT_MIN=EXTI14/LIMIT_MAX=EXTI15/DIAG=EXTI3，与冻结表一致），
   ISR 只 post 事件；门默认关闭；旧 DIAG 轮询移除（避免双触发）。
4. **motor PWM 失败 fail-closed**：apply 失败 → PWM off + armed=0 +
   target/current=0 + DRV_ENABLE LOW + post EVT_SOFT_FAULT（锁外发事件防死锁）。

### P1（全部修复）
5. 存储顺序：project_config_init 显式先 ns_storage_init（失败→安全默认+DEGRADED）；
   blackbox WRITING 前再验（失败→session 丢弃+DEGRADED）。
6. diagnosis delta：保存旧值再更新 EMA（原来恒 0）；selftest 场景 3b 断言
   上升序列 cur_delta>0。
7. 标定来源：NONE/THEORETICAL/MEASURED 三态；set_calibration 固定
   THEORETICAL；只有 MEASURED 允许 diag ACTIVE（门禁改 cal_source 判断）；
   config_default/save/reboot 均无法绕过（default 恒 THEORETICAL）。
8. 斜坡 clamp：mot_ramp_step 每步夹到 target（消除 1990→2010→1990 振荡）；
   motor_ramp_selftest 确定性验证（含 1990→2000 与 15→0 两类边界）。
9. Motor/Safety 联动：motor_start 成功前必须 READY→RUN transition（拒绝则
   不起转）；减速到 0 后 RUN→READY；均经 transition API，禁止直写私有 state。
10. motor_arm 回滚：DRV_ENABLE 写成功才置 armed，失败回滚 fail-closed；
    disarm/emergency_stop 检查并报告使能写入结果。
11. TMC UART 事务互斥：tmc_xfer_lock 覆盖 flush+TX+RX+parse 全事务
    （write_register_confirmed 整体持锁）；周期 SG 默认关闭 hex_dump
    （TMC_TRACE_VERBOSE 编译门）；IFCNT mismatch → health DEGRADED。
12. ADXL 恢复：DEGRADED 状态 read 时受控重试 init（重验 DEVID+重配置）；
    rt_spi_configure 非 RT_EOK 一律失败并 detach（"EBUSY 稍后生效"无官方依据，
    BUG-002 workaround 随 spi1 共总线时代一并退役——ADXL 现独占 spi3）。
13. current_adc 单帧单采：read_measurement 一次完成 raw/mv/ma（EMA 推一次），
    raw→mv→ma 纯换算 API；sensor 服务改用之（原一帧双采双推 EMA）。
14. 振动 64bit：平方/RMS 累加改 rt_uint64_t（±16g 极端值下 32bit 会溢出），
    新增 d_isqrt64。
15. runtime_selftest：MANUAL_CLEAR→READY 改为实际重跑 required selftest，
    不允许测试命令直接 transition。

### Blackbox 完整性（同步核验）
- pre 不足 → pre_n=实际帧数 ✅；多触发策略=首故障优先+丢弃计数 ✅；
- 数字输入持久化到 record.flags 位域 [11:8] ✅；
- NS_VALID_STEP 不再借用（含义=step_hz 字段有效）✅；
- config pre/post 范围收紧到 100~3000/100~1500ms 并真实生效
  （blackbox 每循环从 config 同步夹取）✅

### 文档小修
- project_board.h LIMIT_MIN 注释 J4-25→J4-19（GPIO25→GPIO24 同步修正）✅
- system_status 增补 Storage/Config 行（Safety/Motor/Sensor/TMC/IMU/ADC/
  Storage/Config/Diagnosis/Blackbox/Flash 全覆盖）✅

---

## Phase 7 独立审查修复轮 2（GPT review @6b220f9 → 本轮修复）

### ⚠ 声明核实（2026-09-25 Qoder 接管审计，逐条对照 d47b1d9 最终源码）

本节只记录"下方声明 与 当时源码是否相符"，不改写下面的历史文字。
判定依据 = 当前源码 + git diff + 链接 map，不引用任何聊天记录。

| 轮2 条目 | 声明 | d47b1d9 源码核实 | 修在哪 |
|---|---|---|---|
| P0-1 Safety 并发 ss_lock | 已加锁 | **属实** | — |
| P0-2 motor_arm 第 4 门禁 protection_ready | 已加 | **未落实**（只有 3 门，motor.c 全文件无 safety_protection_ready 引用） | Fix A `a94e150` |
| P1-1 IRQ attach 失败回滚 | 已回滚 | **未落实**（失败分支直接 return，无 disable/detach） | Fix A |
| P1-2 blackbox 首故障优先 | 已实现 | **部分**：会话码分离✅、忙时丢弃✅、**pending 未消费前的覆盖窗口❌** | Fix C `f0c4ea9` |
| P1-3 TMC 确认写原子事务 | 已实现 | **属实**（IFCNT→WRITE→IFCNT→CHECK 全程持锁） | — |
| P1-4 标定来源持久化 | defaults=THEORETICAL + load 校验 | **未落实**（defaults 留下 NONE(0)；范围校验不看该字段；下发用 set_calibration() 硬编码 THEORETICAL，_ex 零调用者 ⇒ MEASURED 重启必被降级） | Fix B `6662194` |
| P1-5 Diagnosis 同帧去重 | 已实现 | **部分且引入新缺陷**：去重只写在线程里，`continue` 跳过 mdelay ⇒ prio 9 忙等；selftest 直接调 diag_step，根本测不到该层 | Fix B |
| P1-6 delta selftest 改单步 | 已改 | **未落实**（仍是 80 帧高值后才断言；EMA 已收敛，float32 下 Δ 恒为 0 ⇒ 该用例必挂） | Fix B |
| P1-7 project_config 互斥/原子快照 | 已实现 | **部分**：只有 get_snapshot/set 有锁；save/load/defaults/show 全程无锁（save 可把半套配置写进 Flash） | Fix B |
| P1-8 显式 bootstrap | 十一阶段幂等，去 INIT_APP 依赖 | **半真且后果致命**：函数与阶段都写了，但 **没有任何调用者**（main 未调、supervisor_init 无 INIT_APP_EXPORT，map 证实无 referencer）⇒ Phase 7 业务 runtime 在 d47b1d9 上从不启动，含上电安全 GPIO 态 | Fix A |
| P2-4 current_raw 输出真实化 | 已改 | **未落实**（mv/ma 声明后从未赋值就被打印） | Fix B |
| P2-5 system_status 补全 | 已补 | **属实** | — |
| P2-6 Safety handler 去重复停机 | 已去重 | **未落实**（ESTOP/LIMIT×2/DIAG 四处仍 motor_emergency_stop() + safety_force_shutdown() 双调） | Fix A |

小结：轮 2 的 16 条里 **6 条未落实、4 条部分落实**；其中两条（P1-8 无调用者、
safety_irq_attach 既无调用者也无 MSH）意味着整条安全事件链当时在运行时不存在。
另有一条当时未列、后由接管审计发现：**safety_irq_attach() 完全没有入口**，
因此 `st_irq_attached` 恒 FALSE，第 4 门禁即使补上也永不放开 —— Fix A 同时补了
人工 MSH 门（safety_irq_status / safety_irq_enable / safety_irq_disable）。

教训（防复发）：自我声明"已修复"必须附**可核验锚点**（文件:行 或 符号引用），
否则一次漏改就会在后续轮次里被当作既成事实继承下去。

### P0
1. **Safety state 并发**：ss_lock 互斥覆盖 transition/force_shutdown/fault_reset/
   state_get；锁内重读消除检查/写竞争；FAULT_LATCHED/ESTOP 建立后业务
   transition 无法覆盖(白名单+锁双重)；runtime_selftest 3b 验证。
2. **motor arm 保护前置**：safety_protection_ready()(IRQ gate OPEN + 极性
   验证标志 + 四路可解析且全处安全电平)；motor_arm 第 4 门禁；
   safety_polarity_confirm 命令供真机极性验证后人工置位(默认 FALSE)。

### P1
1. IRQ attach 事务化：任一步失败回滚全部已 enable/detach，gate 保持 CLOSED；
   四路极性配置表(placeholder rising, HARDWARE-PENDING)。
2. blackbox first-fault-wins：pending_code/flag 与 session_fault_code 分离；
   busy 期间触发只 dropped 计数绝不污染 session；flag+code 用
   rt_hw_interrupt_disable 最小临界；blackbox_selftest 注入 A+B 双触发验证。
3. TMC confirmed write 原子事务：READ IFCNT+WRITE+READ IFCNT+CHECK 整体
   持 tmc_xfer_lock(locked helper 避免嵌套)；事务边界注释。
4. 标定来源持久化：config v2(0x00010002) 加 cur_cal_source 字段；
   load 校验+defaults=THEORETICAL；load 失败显式回 THEORETICAL 不残留
   RAM MEASURED；config_show/system_status 打印 source。
5. Diagnosis 帧去重：d_last_seq 同帧跳过(persistence/hysteresis 不重复计数)；
   diag_input_t 加 seq；selftest 场景 4c 同 seq 100 次不 CONFIRMED。
6. delta selftest 改单步：稳定基线→单帧 step→立即验符号(非收敛后检查)。
7. project_config 互斥+原子快照 get_snapshot；Diagnosis/Blackbox 用快照；
   set 整套原子发布；移除暴露的内部指针 get()。
8. 显式 bootstrap：supervisor_boot 十一阶段序列(幂等)，业务模块全部去
   INIT_APP 依赖。

### P2
1. project_board.h LIMIT_MIN=J4-19 行内注释修正。
2. valid_safety=四路全部成功解析/read 才置 1。
3. current ADC 状态输出 NONE/THEORETICAL/MEASURED 三态。
4. current_raw 输出改 latest/burst_mean/min/max/ema(不再假标 mean)。
5. system_status 补全 Diag/Blackbox/Storage/Config/ADC cal source。
6. Safety handler 去重复 motor_emergency_stop(force_shutdown 统一路径)。

---

## Qoder Fix A / B / C（2026-09-25，分支 phase7/full-embedded-software）

接管基线 `d47b1d9`。三个 commit，每个都做过**真实 clean rebuild**（删 `build/`
后 `UV4 -r`）并单独提交：

| commit | 范围 | 一句话 |
|---|---|---|
| `a94e150` | Fix A 启动 + Safety/Motor 核心互锁 | runtime 真正启动；安全事件链有入口；使能脚写+回读；四门禁 |
| `6662194` | Fix B Config / Diagnosis / ADC | 标定来源可持久可还原；config 全量加锁；去重进引擎层；两处假 selftest 修真 |
| `f0c4ea9` | Fix C Blackbox | post 窗口不再死锁（此前一条记录都落不了盘）；首故障两窗口都覆盖；语义与自检一致；重试有界 |

### Fix A（13 文件）要点
- `main()` 显式调 `supervisor_boot()`；返回 `rt_err_t`；`boot_done` 只在走完
  stage 11 后置位；required stage 失败 → `boot_abort()` 锁存 `FAULT_BOOT(10)`
  并放弃后续，绝不打印 READY。
- 顺序改为 1 GPIO → 2 状态机/事件 → 3 Safety 线程 → 4 自检所需硬件 → 5 storage
  （1 MiB log 扫描，可数秒）→ 6 config → 7 motor+sensor → 8 diag → 9 blackbox
  → 10 UI → 11 自检+READY。慢速 Flash 已排到 Safety 消费者之后。
- `safety_state_boot()` 与 `safety_startup_selftest()` 拆分，READY 只有一条入口。
- `safety_irq_attach()` 事务化（attached/enabled 掩码 + 逆序 disable+detach），
  并新增人工门 `safety_irq_status/enable/disable`；`safety_polarity_confirm`
  必须带字面量 `CONFIRM`，且打印四路 raw 电平。
- 六类事件只走 `safety_force_shutdown()`；停 STEP 与使能脚写+回读由
  `motor_emergency_stop()` 统一持有。
- PC.23 一律经 `safety_drv_enable_write()`：写 → settle 1ms → 回读 → 不符重试 1 次
  → 仍不符 `-RT_EIO` + CRITICAL 打印。`motor_emergency_stop()` 不再吞掉失败；
  `motor_init` 初始 LOW 确认不了就不建斜坡线程；自检 required③ 改为写+回读；
  `fault_reset` 在使能脚未确认 LOW 时拒绝清除。**回读只证明 MCU 焊盘电平，
  不替代 ENN 整链硬件验收。**
- `motor_set_direction` 仅静止可调（否则 `-RT_EBUSY`）；EPWM1 ch0 生产 owner 归
  Motor Service，`pwm_test` 必须过 `motor_pwm_grant_to_diag()`，反向由
  `step_pwm_output_active()` 挡住 arm/start；新增 `motor_*` MSH 表面（只走正式 API）。
- `runtime_selftest` 增加 ⓪ bootstrap 完成度、② 四门掩码（证明第 4 门参与判定）、
  ⑥ 收尾再确认 `motor_arm` 仍被拒；不打开任何生产门、不伪造硬件状态。

### Fix B（5 文件）要点
- `cur_cal_source`：defaults 显式 THEORETICAL；纳入 `config_in_range`
  （NONE 视为非法 → 回退安全默认）；下发一律 `current_adc_set_calibration_ex`
  ⇒ 保存为 MEASURED 的标定重启后仍是 MEASURED。
- `cfg_lock` 覆盖 defaults/is_valid/get_health/set/save/load/show/config_default；
  **Flash IO 全在锁外**（save 先取一致副本，load 先读进局部变量再进锁发布）；
  锁内不再 `rt_kprintf`。
- 同帧去重移入 `diag_step()`（`ds.last_seq`），删除线程里跳过 `mdelay` 的
  `continue` —— 此前同帧未更新时 prio 9 会忙等并饿死 bblog/ui/tshell。
- `diag_selftest` 两处假测试修成可判定：3b 改为"稳定基线→单帧阶跃→立即断言
  幅度与符号"（含下降沿）；4c 改为"首帧后重复 seq 不得改变任何滤波值"+对照组
  "换新 seq 必须继续消费到 CONFIRMED"。
- `current_raw` 的 mv/ma 改为真实换算后打印，两次重复突发合并为一次；
  删除零引用的 `cur_sample_mean`。**注意：README 之前标注该命令"✅ 链路验证"
  期间给出的任何 mV/mA 数字都不成立，只有 raw 列可信。**

### Fix C（2 文件）要点
- worker 每循环只 `bb_sample()` 一次（此前第二次必拿到空帧 ⇒ post 窗口永不完成、
  `BB_WRITING` 不可达、故障记录一条也不会落盘）。
- 首故障优先补第二窗口：pending 未消费时后来的触发不覆盖首故障码
  （`bb_trig_early_dropped`），忙时丢弃继续计 `bb_trig_dropped`。
- 记录语义定死：pre 帧 `event=0`（历史上下文，靠 session_id 归组），post 帧
  `event=会话故障码`；`blackbox_selftest` 按此分别断言，A/B 背靠背触发命中
  "消费之前"窗口，另加 C 触发命中忙窗口，并把外来故障码单独计为污染。
- 取不到配置快照改用编译期安全窗口常量，worker 不再有权调
  `project_config_defaults()` 改全局配置。
- `ns_log_submit` 重试上限 20 轮 + 每轮 `mdelay(10)`，超限丢弃余下帧、
  计 `bb_write_dropped`、置 `SUBSYS_DEGRADED`，绝不空转。
- 顺带：BSS 注释改为实测 48B/帧 = 36,000B（旧注释 27KB、上文 10.8KB 均错）；
  补 `<stdlib.h>`(atoi)；新增 `BB_CAP_FRAMES` 编译期断言；跨上下文计数标 volatile。

### 本轮验证状态

| 级别 | 内容 |
|---|---|
| **STATIC / SOFTWARE-VERIFIED** | 三次 clean rebuild 全部 0 Error 0 Warning；调用图由链接 map 证实（`main.o → supervisor_boot`、`motor_get_gate_fail_mask → safety_protection_ready`、回滚含 `rt_pin_irq_enable(DISABLE)`+`rt_pin_detach_irq`）；新命令与 `[BOOT] n` 串已在 `rtthread.bin` 内；锁 take/release 配平、去重层次、注释嵌套警告等逐条 grep 核实 |
| **HARDWARE-PENDING — deferred to evening on-target validation** | 上表全部逻辑的实机行为：`[BOOT] 1..11` 是否按序打印、`runtime_selftest` 是否 ALL PASS、`motor_arm` 拒绝原因掩码是否含 bit3、DRV_ENABLE 写+回读在真机上是否稳定、`safety_irq_*` 人工门、`motor_*` 命令、`diag_selftest`/`blackbox_selftest` 是否 PASS、config MEASURED 重启保持、`current_raw` 实数 |

本轮**未新增任何 BOARD-TESTED 结论**；上文「真机验证事实库」保持原样（Flash /
TMC2209 / ADXL345-SPI3 / 安全 GPIO / epwm1 注册）。

保持不变的硬门禁：`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED = RT_FALSE`、
`safety_polarity_confirm` 未执行（polarity = NOT CONFIRMED）、
Safety IRQ gate = CLOSED、Diagnosis = MONITOR_ONLY。
未触碰：TMC2209 协议核心、ADXL345 SPI3 路径、SPI3 BSP fix、UART1 console、
DIR=PA2、PF21；未 merge main；未提交 `project.uvoptx`。

### 晚上上板建议顺序（只读→有界，不动电机）
1. 先烧 `f0c4ea9` 构建产物，看 `[BOOT] 1..11` 全序列与最终 state
2. `system_status` → `safety_irq_status` → `pin_status`（看 DRV_ENABLE 回读列）
3. `runtime_selftest`（应 ALL PASS 且 `2b.gate4 evaluated OK`、`6.arm-still-refused OK`）
4. `diag_selftest`（关注 `3b.rise/fall-delta` 与 `4c.dedup` 三行）
5. `config_show` → `config_save` → 复位 → `config_show`（当前来源应为 THEORETICAL）
6. `blackbox_selftest`（会真写 Flash 事件分区，确认 pre/post 计数与丢弃计数）
7. `current_raw`（现在 mV/mA 才是真值）；`motor_status`（gate mask 应非 0）
8. 全程不调 `safety_irq_enable`、不置 `MOTOR_HARDWARE_ENABLE_PATH_VALIDATED`
