# 交接：S2-A 停机优先关系 —— 只读审计与方案设计（phase8-stop-priority-s2a）

任务书：`NS800_Stop_Priority_S2A_Qoder_Task_20261023.md`
任务 ID：`phase8-stop-priority-s2a`
性质：**DESIGN ONLY** —— 生产代码与测试代码本轮冻结；无构建、无测试执行、无硬件动作。
日期：2026-10-23（Asia/Shanghai）

## 1. 分支 / 基线 / 对象

| 角色 | 完整 SHA |
|---|---|
| 开发分支 | `phase8/tmc-drive-config` |
| 本轮审计/文档起点 = **analyzed_commit** | `b410da36a383a367e448cdf1f0fad58eb28b7530` |
| S1-R1 GO 接受对象 | `32a311a76284796ad535d1f7956e5dc101520561` |
| 对应生产实现 / tested_commit | `8ca4f6aca62779d18345c9c3af44e5419d5c2985` |
| 返修起点（历史） | `78a1333c9338332cbe03d64d8a6f61babacbcb00` |
| 历史接受工作包 | IO70 `296c369607ea69c201f797b80950b76ff312c0e0`；安全数据语义 `689c8b3a8f565df9facbb142b90639aaa127aa78` |
| 板上固件身份 | `USER_REPORTED_E40`（`e40a9b9…`），本轮未读回、未烧录 |

开工前只读核对：本地 HEAD = 远端 `phase8/tmc-drive-config` = `b410da3`；`git status --porcelain` = 0 行；
`main=1b3757ee…`、`phase7=6d3fbc55…`、tag `097935fe…`→`e40a9b95…` 均未移动。未 reset、未覆盖、未 rebase。

规则核对：`rt-thread-master/rt-thread-master/AGENTS.md` 全文已读（本轮四份文档均为任务书明确指定，
符合"没有明确指令不得新建 Markdown"）；根 `.gitignore` 与 `tests/host/.gitignore` 已读并保持产物/隐私禁令。
本轮**未**手工改 `rtconfig.h`（只读引用）。

## 2. 实际读取范围（不是"从回执读"）

生产源码全文/段落：`motor.c`(1491 行全文)、`motor.h`、`step_pwm.c`(287 全文)、`step_pwm.h`、
`stop_result.h`、`safety_state.c`(605 全文)、`safety_thread.c`(436 全文)、`safety_gpio.c`(540 全文)、
`tmc2209.c`(锁/传输/txn 相关段)、`supervisor.c`(停机与自检段)、`project_board.h`、
`diagnosis.c`/`blackbox.c`/`ns_storage.c`/`ns_flash_config.h`(线程参数与触发点)。
配置与内核/HAL：`rtconfig.h`(逐条亲验关键行)、`.config`(关键行)、`project.uvprojx`(Define/设备/hooks/文件表)、
`drv_epwm.c`、`drv_gpio.c`、`dev_uart.c`(AC)、`dev_serial.c`(AC)、`device.c`、`rt_drv_pwm.c`、
`src/ipc.c`(事件/互斥/优先级继承关键行)、`libcpu/arm/cortex-m7/context_rvds.S`、
`components/drivers/pin/dev_pin.c`、`board/board.c`。
文档：S1/S1-R1 交接与 r1 证据（含 `go_registration_and_claims_calibration.txt`）、`tests/host/README.md`、
BSP 三份阶段文档。

取证方式：只读 `grep/sed/Read` + 两个只读检索子代理（内核与 BSP/HAL 配置）。**信任但抽查**：
其中 `dev_serial.c` 的 poll_tx 循环体、`drv_uart.c` 的 `ns800_wait_tx_space` 忙等循环、
`kservice.c` 的 `CONSOLE_TAKE` 空宏、`epwm.h` 的 helper 返回类型 4 项属子代理摘录，
本轮只在 `rtconfig.h`/`.config` 层面亲自确认了使能条件（无 `THREADSAFE_PRINTF`、无 DMA、
控制台未以 `INT_TX` 打开），故在审计中记为 `AC!` 并列入实现轮必须逐行复核的前置（A-1）。

## 3. 关键发现（详见 `reports/reviews/phase8-stop-priority-s2a/source_audit.txt`）

1. **首次降险动作排在可等待锁之后**：`safety_force_shutdown` 先 `RT_WAITING_FOREVER` 取 `ss_lock`
   （`safety_state.c:165`），而 `fault_reset`（tshell，优先级 20）在同一把锁内做
   2 次有界反馈确认（`mdelay`）+ `motor_disarm` + ≥10 次控制台写。守卫/紧急停机的
   STEP 冻结与许可 LOW 都排在锁后面。
2. **控制台是忙等且无互斥**：`rt_kprintf → rt_device_write(uart1) → poll_tx 逐字符`，
   HAL 等待 TX 空间是**不让出 CPU** 的忙循环，上限 6000ms/字符（`BSP_NS800_UART_TX_TIMEOUT`）；
   `RT_USING_THREADSAFE_PRINTF` 未开。而事件分支在**任何保护动作之前**先打印
   （`safety_thread.c:99-141`）。
3. **优先级继承生效但不解决问题**：`src/ipc.c:1425-1430` 无条件继承（`RT_USING_MUTEX` 已开）⇒
   无界反转被挡住，但持锁者自身的 `mdelay`/忙等不受继承影响；boost 后持锁者以等待者优先级忙等，
   会饿死其余线程。
4. **使能是一条 7 步无序列化边界的序列**：`motor_arm` = 事务 → 查门 → UART 复核 → 再查门 →
   有界反馈确认 → **写 HIGH** → 反馈确认 → `armed=1`；`motor_get_gate_fail_mask` 里对 Safety 状态的
   最后一次读取发生在写 HIGH **之前**，因此停机请求落在间隙里时，arm 仍可在停机之后把许可拉回 HIGH。
5. **诊断侧完全无锁**：`step_pwm.c` 的 `step_enabled`/关断记账是裸静态量，`grant → pwm_set → pwm_enable →
   提交` 四步之间没有任何保护；统一停机的 `step_pwm_force_stop` 在 `step_enabled==FALSE` 时走
   "未持有输出"分支返回 EOK（不关通道、不置锁存），随后诊断 enable 仍可提交（`STATIC_INTERLEAVING R-2`）。
   目标宏下今天不可达，但**不是靠宏能解决的缺陷**。
6. **TMC 锁要分两类**：`tmc_xfer_lock` 与配置 `lock` 是 FOREVER，配置 `txn` 是 `RT_WAITING_NO`
   ⇒ 不能笼统写"所有 TMC 锁都无界"。UART 复核在斜坡线程内，一轮约 ≤205ms（既有口径），
   不含锁等待与调度抖动，**不是**时延上界。
7. **驱动能力事实（新）**：`drv_epwm.c:588-593` 的 disable = 一次 `TBCTL.CTRMODE=STOP_FREEZE` 写后
   **无条件 return EOK**，且 enable/disable 是**整个 EPWM 模块**的时间基而非 per-channel；
   框架层 `rt_pwm_disable(channel=0)` 还会先发一个 `PWMN_CMD_ENABLE`（ns800 不认，返回 -RT_EINVAL 后被
   覆盖丢弃）。⇒ 在本驱动上 S1 的"关断 API 失败锁存"几乎不可达，"API 接受"更只能是"寄存器写已发生"。
   已列为设计阻断 **B-4**（需要驱动能力层定义 + 波形补证，本轮不动 S1 合同）。
8. **`rt_device_control` 不检查 `ref_count`** ⇒ epwm1 从未 open 也能到驱动，"没 open"不等于关断是空操作。
   同时 `RT_USING_DEBUG`+断言生效 ⇒ 传 NULL 设备是断言而不是错误返回，实现轮不得依赖"返回 -RT_EIO"。
9. **事件投递不等于执行**：`rt_event_send` 只做 `set |= bits` + 唤醒；`safety_post_event` 的返回值
   多数调用点被 `(void)` 吞掉，且若 Safety 线程不存在则标志被搁置。⇒ "投了事件"不能当作"已停机"的证据。
10. **旁路写入者清单**：通用 `pin` 命令（`dev_pin.c:382`）、`pin_status`（会主动重写输出脚）、
    `system_selftest`/`runtime_selftest`（从 tshell 直接写许可 LOW、重跑 motor_init/UART/Flash，
    **不持** ss_lock/mot_lock）都在协调机制之外；遗留 `pf21_diag/pf21_ab` 因
    `NS800_ENABLE_LEGACY_PF21_DIAG` 未定义而**未编译进目标**。这些是权限与适用范围边界，
    本轮不改通用驱动、不删命令。
11. **影子字段竞争**：`mot.drv_en_request` 在锁外写（`motor.c:141`），而 `pin_status`/selftest 走
    `safety_drv_enable_write` 只更新 `sg_drv_en_request` ⇒ 同一"最近请求"在两个观察点可能不一致
    （`motor.c:574-581` vs `safety_thread.c:311-319`）。另 `motor.c:1263` 无锁直读 `stop_fail_latched`。

## 4. 推荐方案与需要用户/审查决定的事项

推荐 **方案 A：短保护阶段 + 抑制代次 + 提交门**（`reports/plans/phase8-stop-priority-s2a-design.md` §5）：

- 单个对齐 32 位状态字承载 `inhibit + generation (+owner)`；所有突变在
  `rt_hw_interrupt_disable/enable`（Cortex-M7 上即 `CPSID I`）的**固定指令数**窗口内完成；
- 任何故障类停机请求**先**在窗口内置位抑制并做两个彼此独立的降险动作（EPWM 冻结、GPIO CLR 许可），
  **不取任何互斥量、不打印、不碰 UART、不延时**；回读确认、反馈采样、日志、黑匣子、S1 富记账全部后置；
- 所有使能提交改为"慢校验在锁外 + 提交在比较门内"：token 携带 `(owner, gen)`，
  `sp_enable_commit` 在同一窗口内比较并提交所有权，**越过它才允许写使能寄存器**；
  STEP 的 `pwm_set` 可留在窗口外（时间基冻结时改比较值不产生脉冲），
  线性化点是 frozen→running 那一次寄存器写；
- 许可写拆成 `commit`（窗口内 GPIO SET）+ `confirm`（settle/回读/重试，锁外）；确认失败再次走幂等快保护；
- `fault_reset` 只在**代次未变**时清除，慢校验移出 `ss_lock`；新故障必然 bump 代次 ⇒ **新故障赢**，
  旧 token 与旧恢复都不能重新获得资格；进入 `MANUAL_CLEAR` 仍不自动 arm/enable；
- UART 保持有截止期（现状 `TMC_REPLY_TIMEOUT_MS`/`-RT_ETIMEOUT`），复核结果绑定
  `(gen, config_epoch)`，晚到结果丢弃且不得复活许可；"忙"不能无限掩盖过期；
- 与 S1 的职责划分：`stop_fail_latched` 是**证据位**，`inhibit` 是**权限位**，两者独立记账、
  只在 `fault_reset` 同一条人工恢复路上一起解除。

备选 B（单一输出执行者线程）与 C（只加锁超时+提交前复查）的对比见 design §6：
C 不满足 I1/I4/I5，**不可单独采用**；B 是可能的第二步，但它只有在"执行者首次动作不依赖任何锁/队列/控制台"
时才满足要求 —— 那正是 A 提供的性质，故 A 是 B 的前置而非替代。

需要审查方/用户决定的事项：
1. 是否接受"抑制与代次放在 `safety_state.c`"这一 owner 归属（否则需要新头文件/新 `.c`）；
2. 是否允许 WP1–WP3 分成三次独立复审（推荐），还是要求一次实现轮做完；
3. B-1（关中断窗口长度）与 B-2（外设写屏障/缓存属性）是否允许以**纯源码**方式补证，
   还是必须等真机测量（真机需要新授权）；
4. B-4：`rt_pwm_disable` 恒 EOK 这一驱动事实，是否要求 S1 的 `api-failed` 语义另行修订（本轮未动合同）；
5. 旁路写入口（`pin`/`pin_status`/`selftest`）是否要求纳入协调机制（涉及通用驱动，本轮未改）。

## 5. 设计完成度与阻断

| 项 | 状态 |
|---|---|
| 现状调用/等待表、入口清单、交错表 | 完成（审计 §3–§6） |
| 推荐方案能解释所有开启提交与人工恢复竞争 | ~~完成（design §5，覆盖 I1–I8）~~ **本节为 v1 记录，已由下面第 9 节作废**：独立设计审查给出 DESIGN_REWORK，NEW-01/02/03/04/05 五条静态反例推翻 v1 的"自洽"结论 |
| 可证伪的计划用例 S2-P01..P11 | 完成（design §7，全部 `PLANNED / NOT EXECUTED`） |
| 后续最小实现顺序 | 完成（design §9 WP1–WP4） |
| 设计阻断 | **5 项开放**：B-1 关中断窗口代价、B-2 写屏障/缓存、B-3 EPWM 模块级冻结的旁影响、B-4 驱动无失败返回、B-5 旁路命令能否触及 PC.23 |
| 结论 | **PARTIAL-可审**：方案本体自洽且有源码依据；B-1/B-2/B-3 是"实现前必须关闭"的纯源码补证，B-4 需要波形/驱动级定义（延期），B-5 是运维授权边界 |

## 6. 本轮未做的事（不得被读成已做）

- `tests_executed = []`：**未运行** Keil、任何 `tests/host` 链、反向/元负例、PowerShell 采集器、编译器。
  只读检索不是功能测试 ⇒ 本文件不含 `TEST_PASS` / `REPRODUCED` 判定。
- `hardware_actions = []`：未开串口/COM、未接调试器、未扫描设备、未烧录/复位/读 Flash、
  未读写真实 GPIO/TMC（含只读命令）、未动电源/模块/接线/SW1、未接电机、未输出 STEP、
  未清真机故障、未开 IRQ/改极性、未翻宏。
- 四宏保持 `RT_FALSE`（`motor.h:67-68`、`motor.h:81-82`、`tmc2209.h:98-99`、`project_board.h:50-51`，本轮逐条回读）。
- 冻结未动：全部 APP/HAL/内核/头文件、`tests/`（含 runner/shim/期望）、Keil 工程、`.gitignore`、
  旧证据与旧报告；`fault_reset` 旁陈旧注释、`PS12c` 前提、`sw1_cut.ps1` T-01/T-02 **都不在本轮修复**。
- 历史测试引用（S1 的 48/64、反向、V1/V2、I/O 138、Keil 0/0）只作为**旧提交**的结果引用：
  `62873bd`（首轮）与 `8ca4f6a`（返修轮）；**不是**本轮重新执行。
- 不生成 ZIP、不新建测试框架/checker、不改多份 BSP 阶段文档（任务书 §3 只要四份新文件）。

## 7. 交付物

- `reports/plans/phase8-stop-priority-s2a-design.md` —— 设计方案（身份与证据等级/等待图/交错/方案对比/
  不变量与同步恢复合同/计划用例/工作包/阻断/停止点）
- `reports/reviews/phase8-stop-priority-s2a/source_audit.txt` —— 基线与工具、范围、调用与调用者表、
  锁与阻塞表、目标配置依据、源码交错、四宏与冻结检查、"未发现"口径
- `reports/agent_handoffs/phase8-stop-priority-s2a.md`（本文件）与同名 `.json`

## 8. 停止点

`STOP: AWAITING_INDEPENDENT_DESIGN_REVIEW`

不登记 S2 实现 GO，不直接进入实现/返修/板级/F2e/F3。方案由 ChatGPT 审查、用户决定后，
**另下实现任务书**才可动工。本任务书不构成任何硬件授权；"结构性消除某个 `FOREVER`"
也不等于 RISK-03/03a 已关闭。

---

## 9. 2026-10-23 同轮限定修正 `phase8-stop-priority-s2a-r1`（DESIGN ONLY，追加节）

审查件：`NS800_S2A_Design_Review_and_R1_Task_f4b47e3_20261023.md`
**结论 `DESIGN_REWORK`（只修正 S2-A 设计，不进入生产实现）**；五项 NEW-01..05 全部成立，
v1 的"方案本体自洽、已覆盖 I1–I8"结论**作废**（上面第 4/5 节的相应行已就地标注为 v1 记录）。
S1-R1 的 `SOFTWARE_REVIEW_GO`（接受 `32a311a`，生产 `8ca4f6a`）**不受影响、不撤回**。

返修对象与身份：`document_base = f4b47e3f145833ffbf7705f09d8343bd1c84fa15`，
`analyzed_commit = b410da36a383a367e448cdf1f0fad58eb28b7530`（生产分析对象不变，
**不**改成实现 tested_commit）；开工前只读核对本地=远端=`f4b47e3`、工作树 clean、
冻结引用 `main=1b3757ee…`/`phase7=6d3fbc55…`/`tag=097935fe…→e40a9b95…` 未动。

### 9.1 逐条映射：NEW → 方案位置 → 静态表 → 未决

| 审查条目 | v1 错在哪 | v2 的修正（design.md 位置） | 静态反例表 | 仍开放 |
|---|---|---|---|---|
| **NEW-01** P1 资格判定与实际写不同窗口 | "commit 返回 → 停机完整执行 → 旧操作继续写 running"；单核只消除**窗口内**并发 | §5.1 OWNER/动作态转移表、§5.3 `sp_run_commit`（`rt_pwm_enable` 与许可 HIGH **在掩码窗口内执行**）、§5.4 认领规则 | **D1**、D7、D8 | 窗口时长需实测（B-1） |
| **NEW-02** P1 合并请求不 bump 代次 ⇒ 旧恢复能清新故障 | `fault_reset` 恰在 inhibit=1 时开始，恢复中的新请求（含同原因重复）不动代次 | §5.5：`REQ` **每个请求自增**；`ticket=REQ` 三处比较（第 3/4/5 步）+ `CLEARING` 态 + `latch_req==ticket` 身份清除；20 位**饱和即拒绝恢复**（fail-closed，不回绕） | **D2**、D8 | REQ 位宽/饱和策略需审查确认 |
| **NEW-03** P1 置位被当完成，ISR 首请求可能永不做动作 | §5.2 允许 ISR 全程、§5.3 又限制只半程 ⇒ 谁都不做；单一 inhibit/first 表达不了三件事 | §5.2 `sp_publish` 只发布义务并返回**应执行的动作掩码**；§5.4 **发布者自己执行**（ISR 也执行，动作只是寄存器写）；三态 PENDING/RUNNING/DONE 分列；任何上下文可认领未完成动作；`meta_pending` 表示"整理未落地" | **D3**、D4 | Safety 不可用时只保证"物理已降险 + 未整理可见"，不宣称状态机一致 |
| **NEW-04** P1 接收超时 ≠ 整次复核截止期 | 100ms 只管 RX；其前 cfg/xfer 两把 FOREVER、`tmc_rx_flush()` 的 `while(read>0);`、TX 每字符 ≤6000ms 忙等都不在预算内 | §5.6 分段预算表（txn / cfg lock / xfer lock / flush 双上限 / TX 预算 / RX / 日志），总预算自函数入口起算，**过期即 publish+execute**；`-RT_EBUSY` 只覆盖 txn 一种情形；每拍 ENN 检查的最坏推迟变为有界 | **D5** | 各段数值全部 TBD（未测）；不新建线程 |
| **NEW-05** P1 set 也写计数模式 | `drv_epwm.c:318-319` 读 TBCTL 模式 → `:371` 写回 ⇒ 停机后旧 set 会把时间基带回 RUN | §5.7：给出按函数列出的危险/降险清单与两个方案 —— (i) 窄改 `:371` 让配置写不恢复 `CTRMODE`（推荐，无并发时逐位等价）或 (ii) 整段 `set` 进窗口（时长需实测）；新增用例 **P12** 覆盖"已读旧模式、尚未写回时快停"的接缝 | **D6** | 选 (i) 需批准窄改 HAL 一条语句 |

### 9.2 第 4 节事实校准（已同步进四份文档）

- **B-4 重新定性**：HAL 正常路径恒返回 EOK 是**驱动观测能力**边界，不构成 S1 语义返工理由；
  S1 只称"API 接受请求"。不把示波器列为设计前置，不新增五点电压门槛（审查件 §4.1/§5）。
- **NULL 分两层**（§4.2）：`rt_pwm_enable/disable(NULL)` 先 `if(!device) return -RT_EIO`
  （`rt_drv_pwm.c:143-199`，本轮亲读）；断言只适用于直接 `rt_device_control(NULL,...)`。
  审计 W-11 已改写。
- **R-1 拆成两条**（§4.4）：`motor.c:900` 只写 `mot.armed`，**不碰 GPIO**；真正的"停机后许可重新 HIGH"
  必须停在禁能侧确认成功后、`:846` 调用之前（审计 R-1a）；另一种交错只推出陈旧 armed 提交（R-1b）。
- **控制台分支亲读**（§4.3）：`kservice.c:328-330` 空宏（#ifdef 在 :245，#endif 在 :332）、`dev_serial.c:1020-1046`→`:377-403` poll_tx
  （putc<0 即 break）、`drv_uart.c:403-427` 按 tick 忙等、6000ms/字符；措辞改为"以调用者优先级独占 CPU，
  低于该优先级的线程得不到运行，仍可被更高优先级/可响应中断抢占"，**不再写"饿死全部线程"**。
  审计 W-7 整条由 AC! 升级为 DV。
- **字段与原子性**（§4.7）：五个共享字（`gate/step_rc/perm_rc/reason/stats`）+ 一张状态机；
  删除"四个函数/四个字段"的说法与"单字对齐即复合事务原子"的暗示；`|=`/`++` 在窗口外不视为原子。
- **旁路分三类**（§4.6）：`pin` 命令 write 直达 `rt_pin_write`（足够构成维护旁路）；
  `pin_status` 对许可写的是 **LOW**（降险方向但改影子）；`system_selftest` 会 init/写输出。
  审计 C7 已分列，且明确"不承诺任意 shell 操作下的全局安全"。
- **本地 AGENTS.md 与远端可见性分开记录**（§2.2）：`rt-thread-master/rt-thread-master/AGENTS.md` 与
  SDK 的 `epwm.h` 都只存在于本地工作区，**未被远端仓库跟踪** ⇒ 不能写成云端已核实。
- **PRIMASK 的论证范围**（§4.5）：只覆盖本核普通可屏蔽上下文；不协调另一核、不屏蔽 NMI/HardFault、
  不约束 DMA/总线层。审计 §2 的 B-a/B-b 边界与新增 M-1/M-2/M-3 已写入 design。

### 9.3 本轮新增的亲读补证（把可源码闭合的项当轮收齐）

审计 §11 全量记录，摘要：`drv_epwm.c:497-518/318-319/370-371/380-440(无循环)/336-345`；
`tmc2209.c:1299/1301/282/139-142/206/208/58`；`drv_common.c:119-120`（**I/D-Cache 是文件内无条件
`#define` 后开启的**，不是 Kconfig 项）；`NS800RT7P65x.h:34211/34238`（EPWM1=0x40000000、
GPIOA=0x400A0000 ⇒ 默认映射的外设/Device 区，不被缓存，但 Device 区允许写缓冲 ⇒ 同窗口回读需屏障，
厂商自身在 `syscon.h:449` 的 `SYSCON_UNLOCK` 里就带 `__DSB()`）；`dev_pin.c:146-160` 与
`drv_gpio.c:273-286/342-361`（许可写是**有界寄存器写**，可进窗口）；
`context_rvds.S:38-43` + `drv_uart.c:403-427` 的推论：**窗口内 tick 不推进**，所以任何按 tick 判超时的
等待放进窗口就永不返回 ⇒ "窗口只能包纯寄存器写"是源码事实的后果，不是偏好。

### 9.4 完成度（诚实表述，替代 v1 的"已覆盖 I1–I8"）

- 协议已对答 NEW-01..05 并给出 D1–D8 静态反例表与 S2-P01..P12 用例（全部 `PLANNED/NOT EXECUTED`、
  `STATIC_REASONING/NOT_EXECUTED`，不含 PASS 字样）。
- **仍 `PARTIAL`**：B-1（窗口时长与中断丢失容忍）、M-1（另一核/其它总线主设备）、M-2（同 EPWM 实例
  其它通道用途）、M-3（写缓冲完成时刻）只能靠实测或有版本的厂商资料关闭；NEW-05 的 (i)/(ii) 选择
  需审查方/用户裁定。
- 需要后续裁定的只剩两项（§9.1 表末列 + design §9 末段）：NEW-05 方案选择、REQ 位宽与饱和策略。
  模块归属按审查件 §5 **不再作为用户决策项**：采用"一个内部协调 owner + 对外窄接口，优先复用现有模块"。
- 实现工作包已按审查件 §5 重排：**WP1 = 可独立证明的纵向小闭环**（发布/真实写提交/撤销/恢复票据
  一起做，不再拆成"先门后恢复"），**WP2 = UART 端到端预算**，WP3 = 目标机测量（后置授权）。
  暂不锁定三轮返修额度。

### 9.5 本轮的测试与硬件状态（不变）

`tests_executed = []`、`hardware_actions = []`；未运行 Keil/主机链/反向/采集器；未开串口、未接调试器、
未烧录/复位/读 Flash、未读写真实 GPIO/TMC、未动供电/模块/接线/SW1、未接电机、未输出 STEP、
未清真机故障、未开 IRQ/改极性、未翻宏。四宏在 HEAD 仍 `RT_FALSE`；板上仍 `USER_REPORTED_E40`；
RISK-03/03a 与 RISK-04-b 开放；`sw1_cut.ps1` T-01/T-02 仍 HOLD；静态电气与现有 SW1 维持
`PASS (USER_CONFIRMED)`，本轮**不重复索取**。改动严格限于既有四份文件，生产/测试/工程与
`f4b47e3` 逐 blob 零差异。

状态：**`STOP: AWAITING_INDEPENDENT_DESIGN_REVIEW`**（v2，返修对象 `f4b47e3` 之上的文档修正）。
