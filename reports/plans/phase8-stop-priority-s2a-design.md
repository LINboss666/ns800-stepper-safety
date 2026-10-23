# S2-A 停机优先关系 —— 设计方案 v2（DESIGN ONLY，未实现）

任务 ID：`phase8-stop-priority-s2a`（v2 = 同轮限定修正 `phase8-stop-priority-s2a-r1`）
生产分析对象（analyzed_commit）：`b410da36a383a367e448cdf1f0fad58eb28b7530`
文档返修基线：`f4b47e3f145833ffbf7705f09d8343bd1c84fa15`（v1，被本文件按 NEW-01..05 就地修正）
范围：**只交方案**。生产代码、`tests/`、Keil 工程、旧证据冻结；`tests_executed=[]`、`hardware_actions=[]`。
设计审查通过 ≠ 实现批准。

配套：`reports/reviews/phase8-stop-priority-s2a/source_audit.txt`（**审计**，条目 `W-*`/`R-*`/`E*`/`C*`）、
设计审查件 `NS800_S2A_Design_Review_and_R1_Task_f4b47e3_20261023.md`（**审**，第 3 节 NEW-01..05、第 4 节校准）。

### v1 → v2 的变化（先说结论，避免读者按 v1 理解）

| 审查条目 | v1 的说法 | v1 为什么站不住 | v2 的处理 |
|---|---|---|---|
| NEW-01 (P1) | 资格判定在掩码窗口内，真正的寄存器写留在窗口外，靠"只有 RUN 持有者才允许写" | 窗口外仍可被抢占：commit 返回 → 停机完整执行 → 旧操作继续写 running。单核消除的是**窗口内**并发，不是窗口外 | §5.3：危险写（`rt_pwm_enable`、许可 HIGH）与资格判定**同处一个窗口**；窗口内禁止任何按 tick 超时的等待（新事实 P-20） |
| NEW-02 (P1) | 只在 inhibit 由 0→1 时 bump 代次；恢复比较代次 | `fault_reset` 恰好在 inhibit=1 时开始；恢复中来的新故障（含同原因重复）不 bump ⇒ 旧恢复仍能清除新故障，I5 不成立 | §5.5：`REQ` 序列**每个请求都自增**（首发与合并同样）；恢复持票据 `ticket=REQ`，任何后续请求使票据失效；并给出饱和（不回绕）策略 |
| NEW-03 (P1) | ISR 只发布前半段；线程"随后执行"；`first` 消费后别人直接 return | 首发布者是 ISR 时，动作可能永远没人做（Safety 可能卡在 `ss_lock`，守卫可能卡在 UART）；单一 inhibit/first 无法同时表达"已发布/待做/已记录" | §5.4：**发布者自己执行**降险动作（ISR 也执行，动作只有寄存器写）；三态（PENDING/RUNNING/结果）在状态字里分开；任何上下文发现"有 PENDING 未认领"都可认领并执行；事件投递只服务慢整理 |
| NEW-04 (P1) | "复核结果绑代次 + 保留 100ms 截止期"即可 | 100ms 只约束 RX：其前的 `cfg lock`/`xfer lock` 是 FOREVER、`tmc_rx_flush()` 是"只要还有数据就一直读"、TX 是每字符 ≤6000ms 的忙等 ⇒ 同线程的守卫可以不返回 | §5.6：端到端预算**分段**（每段有界并传播错误），过期观察者=调用线程自身；取锁改有界 ⇒ 对阻塞者独立，不需要新建线程 |
| NEW-05 (P1) | `rt_pwm_set` 留在窗口外"无害"，因为时间基是冻结的 | 真实 HAL：`configuration_convert` 在 `drv_epwm.c:318-319` **读** TBCTL 的模式，`configure_signal` 在 `:371` 把它**写回** ⇒ 停机后旧 set 会把时间基恢复成 RUN | §5.7：把"配置写不得恢复计数模式"作为硬要求；给出两处窄改点（`:371` 的模式写条件化）与替代方案（整段 set 进窗口）及其代价；列出实际函数与副作用边界 |
| 审 §4.7 | 报告"四个函数/四个字段" | 实际列了 `ctrl/reason/fast_seq/merged/fast_code` **五个**字；且单字对齐 ≠ 复合事务原子 | §5.1 明确"五个字 + 一个状态机"，并逐字段写清谁在窗口内写、谁在窗口外写 |
| 审 §4.2 | "传 NULL 设备是断言" | `rt_drv_pwm.c:148-150/177-179` 的 `rt_pwm_enable/disable` 对 `RT_NULL` **先返回 `-RT_EIO`**；断言只在直接调 `rt_device_control(NULL,...)` 时成立 | §2 P-18 分开两种包装层的行为 |
| 审 §4.1 | 把 B-4 当"S1 语义需要返工" | S1 只承诺"API 接受请求"，从未承诺探测一切寄存器/物理故障；HAL 正常路径恒 EOK 不构成 S1 返工理由 | §5.8/§9：B-4 降级为"驱动观测能力与硬件未验边界"，不动 S1 合同，不把示波器当设计前置 |
| 审 §4.3 | "控制台忙等会饿死全部线程" | 忙等期间仍可能被更高优先级线程与可响应中断抢占 | 改为"以调用者优先级独占 CPU 至多每字符 6000ms，期间低于该优先级的线程得不到运行" |
| 审 §4.4 | R-1 写成"旧 arm 恢复后把许可拉回 HIGH"却挂在 `:900` | `motor.c:900` 只写 `mot.armed`，不碰 GPIO | 审计 R-1 拆成两条：HIGH 重开必须停在 `:843/:846` 之间；`:897/:900` 之间的交错只推出"陈旧 armed 提交" |

**v1 的"方案本体自洽、已覆盖 I1–I8"结论作废。** v2 的完成度见 §11：协议本体已能解释全部已列交错，
但仍有 2 项只能靠**测量**关闭（窗口时长、写缓冲完成时刻），已明确标 `NOT_TESTED/TBD`，不写成已完成。

---

## 1. 要解决的问题与验收目标（不变）

**问题**：停机怎样摆脱阻塞，并保证进行中的 arm/enable 不会在停机之后重新开启输出。

**目标**：请求一旦形成，(a) 不再有任何新的使能提交能够生效；(b) 两条独立降险动作（STEP 冻结、许可 LOW）
各自都会被无条件尝试**并且真的被执行**；(c) 以上不依赖任何可无限等待的锁、控制台或 UART；
(d) S1 已接受合同（两个原始 rc 分列、失败锁存、诊断失败不交回所有权、唯一人工恢复清除、
一次后续成功不自动恢复、统一停机不自激、API 接受 ≠ 无脉冲）全部保持。

**不是目标**：不宣称停机时延上界；不关闭 RISK-03/03a；不做硬件动作；不翻四宏；不改黑匣子格式。

---

## 2. 证据基础（等级：DV 亲读 / AC 抽查 / ED 推导 / TBD 未知）

v1 表中的 P-1..P-13 保持；本轮把控制台链从 AC! 升级为 DV，并新增 P-14..P-21。

| 编号 | 事实 | 等级 |
|---|---|---|
| P-8（升级） | `rt_kprintf → _kputs → rt_device_write(uart1)`；`RT_USING_THREADSAFE_PRINTF` 未设 ⇒ `CONSOLE_TAKE/RELEASE/PRINTF_BUFFER_TAKE` 为**空宏**（`src/kservice.c:328-330(#ifdef 在 :245, #endif :332)` 亲读）；`rt_serial_write` 因 open_flag 无 `INT_TX`/无 DMA ⇒ 走 `_serial_poll_tx`（`dev_serial.c:1020-1046` 亲读）；`_serial_poll_tx` 是 `while(length)` 逐字符 `putc`，**putc<0 就 break 并返回短计数**（`dev_serial.c:377-403` 亲读） | DV |
| P-14 | 忙等发生在 HAL：`ns800_putc → ns800_wait_tx_space`，`while(!UART_isSpaceAvailable(...))` 仅用 `rt_tick_get()` 比对超时，超时值 `tx_block_timeout = BSP_NS800_UART_TX_TIMEOUT = 6000`（`drv_uart.c:403-427/429-445`、`drv_uart.c:30-46`、`rtconfig.h:440` 亲读）⇒ **每字符 ≤6000ms 的忙等，不让出 CPU** | DV |
| P-15 | `tmc_rx_flush()` = `while (rt_device_read(tmc_serial,0,dump,32) > 0);`（`tmc2209.c:139-142` 亲读）⇒ 只要一直有数据就**不结束**，无迭代上限也无 tick 截止 | DV |
| P-16 | 一次完整性复核的真实等待序：`txn take=WAITING_NO`(`tmc2209.c:1299`) → **`cfg lock take=FOREVER`**(`:735`，`:1301` 调用) → `xfer take=FOREVER`(`:282`) → flush → TX(`:206`) → **此后**才 `deadline=rt_tick_get()+100ms`(`:208`) → RX 轮询每空轮 `mdelay(2)`(`:273`) → 超时 `-RT_ETIMEOUT` | DV |
| P-17 | 本构建**无条件**开缓存：`drv_common.c:119-120` 直接 `#define BSP_SCB_ENABLE_I_CACHE/D_CACHE`（非 Kconfig 项），`:126-133` 调 `SCB_EnableICache()/SCB_EnableDCache()`；未见本工程侧 MPU 编程 | DV |
| P-18 | `rt_pwm_enable(NULL,..)`/`rt_pwm_disable(NULL,..)` 先 `if(!device) return -RT_EIO`（`rt_drv_pwm.c:143-199` 亲读）；只有直接调 `rt_device_control(NULL,..)` 才落到 `RT_ASSERT` | DV |
| P-19 | `rt_pin_write/rt_pin_read` 为裸 ops 分发，无锁无循环（`dev_pin.c:146-160` 亲读）；`ns800rt7_pin_write` = 表内越界检查 + `GPIO_setPin/GPIO_clearPin`（`drv_gpio.c:342-361/273-286` 亲读）；`get_pin_info` 无循环 | DV |
| P-20（新，关键） | `rt_hw_interrupt_disable` = `MRS PRIMASK/CPSID I` ⇒ **SysTick 也被掩**，`rt_tick_get()` 在窗口内不推进 ⇒ 任何"按 tick 判超时"的循环（P-14 的 `wait_tx_space`、`rt_thread_mdelay` 依赖的调度）在窗口内**永不退出**。这条决定"窗口里绝对不能放串口/延时类调用"，也决定窗口只能是纯寄存器写 | DV |
| P-21 | `EPWM1_BASE=0x40000000`、`GPIOA_BASE=0x400A0000`（`NS800RT7P65x.h:34211/34238`）落在 ARMv7-M 默认存储映射的外设（Device）区 ⇒ 即使 D-Cache 开（P-17），外设寄存器读写不被缓存；但 Device 区允许**写缓冲**，故"写完立刻读同一外设并要求生效"需要屏障——本 SDK 自己就在 `SYSCON_UNLOCK` 宏里带 `__DSB()`（`syscon.h:449`） | DV(基址/宏) + ED(区域类型推论) |

**模型边界（必须写在方案前面，不得越界声称）**
- B-a：`CPSID I` 只约束**本核的普通可屏蔽上下文**。它不协调另一核、不屏蔽 NMI/HardFault，
  也不阻止 DMA 或总线层自身的竞争。审 §4.5 明确要求这条界定。
- B-b：P-13 的静态证据（`DUAL_CORE_ENABLE=0` 把 AHB/APB 归属寄存器写 0）只说明"本固件把外设归本核"，
  **不证明**另一核在任何复位/引导状态下不写 GPIO/EPWM。协议的正确性不依赖该假设，
  但"多执行者"这一风险必须在计划用例之外单列（§9 M-1）。
- B-c：任何时间量（窗口时长、写缓冲完成时刻、UART 各段实际耗时、锁等待时长）本轮**全部 NOT_TESTED**；
  只读检索不是功能测试，故本文件不含 PASS/REPRODUCED。

---

## 3. 现状等待链（保持审计 §3；新增"窗口外写"这一失败面）

v1 的链路图仍然成立（`ss_lock(FOREVER) → mot_lock(FOREVER) → STEP 冻结 → 许可 LOW(含 mdelay) → 打印/黑匣子/锁存`）。
v2 新增两点：
1. **危险写有两类**，不只是 `rt_pwm_enable`：`rt_pwm_set` 经 `configuration_convert:318-319` 捕获模式、
   经 `configure_signal:371` 写回模式 ⇒ **set 也是 RUN-capable 写**（NEW-05）。
2. **窗口不是免费的**：P-20 意味着一旦把可等待的东西放进窗口，超时逻辑失效 ⇒ 窗口只能包纯寄存器写。
   于是"整段 `rt_pwm_set` 进窗口"在功能上可行（其内部无 IPC/无延时无循环，只有浮点运算与十余次寄存器写），
   但窗口时长从"几条指令"变成"几十到几百周期以上"，必须由实测决定能否接受（§9 阻断 B-1）。

---

## 4. 不变量（v2，新增 I9/I10）

| ID | 不变量 | v1 是否满足 |
|---|---|---|
| I1 | 请求形成后，任何**之后**的使能提交都不生效 | **v1 不满足**（窗口外写）→ v2 §5.3 满足 |
| I2 | STEP 冻结与许可 LOW 各自无条件尝试一次，互不豁免 | 满足 |
| I3 | 快路径无 `FOREVER`、无控制台、无 UART、无 `mdelay` | 满足（且 P-20 说明为何必须如此） |
| I4 | 使能是否生效由**一个可比较事实 + 同窗口的实际写**决定 | **v1 不满足** → v2 满足 |
| I5 | 人工恢复只清"自己那一代"；较新的请求/失败锁存不被旧恢复清掉 | **v1 不满足** → v2 用 REQ 票据满足 |
| I6 | 元数据不可得时保护不受阻，证据不丢 | 满足 |
| I7 | 受控减速与成功 disarm 不形成抑制；可启动性不被永久封死 | 满足（§5.9） |
| I8 | S1 合同零回退 | **v1 有三处冲突**（owner 提前归零、merge 丢一个 rc、裸写冒充 API_ACCEPTED）→ v2 §5.8 修正 |
| I9（新） | **义务不丢**：发布的降险动作必然被某个上下文执行完成；"已经发布"绝不等于"已经完成" | v1 不成立 → v2 §5.4 |
| I10（新） | **只有门内写能启动运行**：任何可能把时间基恢复到 RUN 或把许可拉 HIGH 的写，必须在资格判定的同一窗口内 | v1 不成立 → v2 §5.3/§5.7 |

---

## 5. v2 协议

### 5.1 共享状态：五个字 + 一张状态机

```c
/* 全部共享可变状态就是这 5 个字。除注明外，写者只在掩码窗口内做整字读-改-写。 */
volatile rt_uint32_t sp_gate;   /* 决策字：唯一被门比较的字，单字装下全部相互依赖的事实 */
volatile rt_int32_t  sp_step_rc;/* STEP 动作的原始结果（不与许可结果混存） */
volatile rt_int32_t  sp_perm_rc;/* 许可动作的原始结果 */
volatile rt_uint32_t sp_reason; /* 请求原因位图：首故障保留，后来者 OR 合并 */
volatile rt_uint32_t sp_stats;  /* 计数与整理义务位（见 §5.4 的 meta_pending） */
```

`sp_gate` 位分配（32 位，无符号）：

```
bit  0        INH        1 = 禁止一切 RUN-capable 提交
bit  1        CLEARING   1 = 人工恢复正在提交（新预约一律被拒，INH 仍为 1）
bit  2..3     OWNER      0=NONE 1=MOTOR 2=DIAG 3=HELD_UNKNOWN(关断失败/未知, 谁都不能用)
bit  4..6     ASTEP      STEP 动作态: 0 IDLE 1 PENDING 2 RUNNING 3 DONE_OK 4 DONE_FAIL 5 NOT_ATTEMPTED
bit  7..9     APERM      许可动作态: 同上 6 态
bit 10..29    REQ        请求序列, 每个请求 +1（首发与合并都加）; 到达 0xFFFFF 即饱和 -> 拒绝恢复(fail-closed)
bit 30..31    RSVD       0
```

合法转移表（`OWNER` / 动作态；未列出的组合非法，实现轮必须按表判非法即拒）：

| 起点 | 事件 | 终点 | 谁执行 |
|---|---|---|---|
| NONE | `sp_enable_ask(MOTOR/DIAG)` 且 INH=0 且 CLEARING=0 | GRANTED 记在 token 里，OWNER 置 MOTOR/DIAG | 申请者 |
| MOTOR/DIAG(=GRANTED) | `sp_run_commit(PWM_START)` 资格通过并写完 TBCTL | OWNER 不变，ASTEP=IDLE(已释放冻结) | 门内 |
| MOTOR | `sp_run_commit(PERMIT_HIGH)` 资格通过 | OWNER 不变，许可已 HIGH | 门内 |
| MOTOR | 受控 `motor_stop`（减到 0，保持 armed） | OWNER 不变，ASTEP 由斜坡的关断提交置 DONE_OK | 门内(冻结是降险方向) |
| MOTOR/DIAG | `sp_revoke(token)` 且 REQ/OWNER 都匹配 | OWNER=NONE | 申请者 |
| 任意 | 停机请求 | OWNER=HELD_UNKNOWN 或 NONE（**只有冻结成功才 NONE**，见 §5.8） | 发布者 |
| HELD_UNKNOWN | `fault_reset` 复核通过并恢复 | NONE | 恢复提交 |

**设备可用性 ≠ 所有权**：`mot_dev_ok`/`step_pwm==RT_NULL` 只决定动作是 `DONE_*` 还是 `NOT_ATTEMPTED`，
不得据此把 OWNER 置成 NONE（这正是 v1 被 NEW-03 指出的错误）。

### 5.2 发布与执行分离（I9）

```
sp_publish(reason):                       /* ISR 与任意线程都可调用 */
    level = rt_hw_interrupt_disable();
        g = sp_gate;
        nreq = ((g >> 10) & 0xFFFFF);
        if (nreq == 0xFFFFF) { ... 饱和: 保持/置 INH=1, 不做恢复承诺, 返回 -RT_EFULL }   /* fail-closed */
        g  = (g & ~REQ_MSK) | ((nreq + 1u) << 10);          /* 每个请求都自增 */
        g |= INH;
        if ((g & ASTEP_MSK) == ASTEP_IDLE)   g = set_astep(g, ASTEP_PENDING);
        if ((g & APERM_MSK) == APERM_IDLE)   g = set_aperm(g, APERM_PENDING);
        sp_gate = g;
        if (reason 是新的) sp_reason |= bit(reason);         /* 首故障优先, 后来者只合并 */
    rt_hw_interrupt_enable(level);
    return 该上下文应执行的动作掩码;      /* 由 §5.4 的认领规则决定, 不是"first"布尔 */
```
关键：`publish` **只发布义务**，不宣称任何物理结果；返回的是"这次上下文该去做哪些仍未完成的动作"。

### 5.3 RUN-capable 提交门（I1/I4/I10；修 NEW-01 与 NEW-05 的窗口外写）

```
sp_run_commit(token, kind):               /* kind: PWM_START | PERMIT_HIGH */
    level = rt_hw_interrupt_disable();
        g = sp_gate;
        ok  = !(g & INH) && !(g & CLEARING)
              && REQ(g) == token->req                    /* 期间没有任何新请求(含合并) */
              && OWNER(g) == token->owner;               /* 所有权仍在申请者手里 */
        if (ok) {
            if (kind == PERMIT_HIGH) rt_pin_write(PIN_NAME_DRV_ENABLE, PIN_HIGH);  /* 1 次 SET 写 */
            else                     e = rt_pwm_enable(mot_pwm_or_diag, ch);        /* 框架->HAL: 一次 TBCTL 模式写 */
        }
        sp_gate = ok ? (g & ~ASTEP_MSK) | ASTEP_IDLE : g;   /* 只有真启动才清 STEP 义务位 */
    rt_hw_interrupt_enable(level);
    if (!ok) { sp_revoke_stale(token); return -RT_EPERM; }
    return e;                            /* 真实 API 返回值原样上抛给 S1 记账, 不伪造 */
```
- **资格判定与实际危险写在同一个窗口内**（审 §3 NEW-01 的硬性要求）。窗口内只有：一次整字读、
  三项比较、一次驱动调用（其内部是 `rt_device_control → _pwm_control → MODIFY_REG(TBCTL.CTRMODE)`，
  或一次 GPIO SET 写）、一次整字写。依据 P-18/P-19 与审 §2.2 对 `drv_epwm.c:581-593/595-630` 的阅读，
  这条链**没有循环、没有 IPC、没有延时代码**。
- 窗口内**禁止**：`rt_thread_mdelay`、`rt_kprintf`/任何 UART、`rt_mutex_*`、`rt_pin_read` 之后的
  按 tick 重试（P-20：窗口内 tick 不推进，任何 tick 超时循环永不退出 ⇒ 确认/回读重试必须放窗口外）。
- 因此"许可写 + settle + 回读 + 重试"必须拆开：**门内写 HIGH**，门外做 settle/回读/重试；
  **每一次重试的 HIGH 写都要重新走 `sp_run_commit`**（审 §3 NEW-01 第 4 条：重写也是开启动作）。
- `rt_pwm_disable`（冻结）是**降险方向**，不属于本门；它由 §5.4 的动作执行器在窗口内无条件尝试。

### 5.4 动作执行器：谁做、怎么保证不漏（修 NEW-03）

```
sp_execute():                              /* ISR 可调用；线程也可调用 */
    /* 1) 认领：把 PENDING 原子改成 RUNNING，认领失败说明别人在做 */
    claim_step = claim_astep();  claim_perm = claim_aperm();   /* 各自一个整字 RMW */

    if (claim_step) {
        level = rt_hw_interrupt_disable();
            if (OWNER(sp_gate) != HELD_UNKNOWN) e = rt_pwm_disable(dev_or_NULL, ch); /* 真 API, 返回真值 */
            else                                 e = -RT_EIO;      /* 所有权未知: 不假装成功 */
            /* 结果与状态同一窗口内写, 保证"有结果必有状态" */
            sp_step_rc = e;
            sp_gate    = set_astep(sp_gate, (e==RT_EOK) ? ASTEP_DONE_OK : ASTEP_DONE_FAIL);
            /* 失败 => OWNER 保持/置 HELD_UNKNOWN, 绝不交回所有权(S1 合同 4.3) */
            if (e != RT_EOK) sp_gate = set_owner(sp_gate, HELD_UNKNOWN);
        rt_hw_interrupt_enable(level);
    }
    if (claim_perm) {
        level = rt_hw_interrupt_disable();
            rt_pin_write(PIN_NAME_DRV_ENABLE, PIN_LOW);            /* 单次 CLR 写 */
            sp_perm_rc = 0;                                        /* 写已发出: 只表示"已请求", 不是"已确认" */
            sp_gate    = set_aperm(sp_gate, APERM_DONE_OK); /* 对许可线 DONE_OK == "LOW 已写出(WRITTEN)",
                                                                "已回读确认(CONFIRMED)"只能由门外的
                                                                safety_drv_enable_write 成功另外记账,
                                                                两者不得混写(§5.8 第三行) */
        rt_hw_interrupt_enable(level);
        /* 回读/settle/重试在门外, 走现有 safety_drv_enable_write 的确认语义并保留其 rc */
        e = sp_permit_confirm_low();       /* 内含 rt_pin_read + mdelay; 失败 -> 记 rc + 重新置 PENDING */
    }
    /* 2) 若仍有未完成的 PENDING(例如本上下文认领前被抢占), 返回给调用方重试点 */
```
- **发布者自己执行**：`sp_publish()` 的返回掩码由调用方立刻交给 `sp_execute()`。ISR 作为首发布者时同样
  在 ISR 内完成这两个寄存器写（P-19/P-6 证明其无循环无 IPC；P-20 禁止它做任何等待类调用）。
  这样"首请求是 ISR"时不存在"等别人来做"的空洞（审 §3 NEW-03 的核心）。
- **不漏**：动作态与 inhibit 位分开，`first` 不再是布尔开关。任何后来者（守卫下一拍、`motor_status` 命令、
  Safety 线程、下一次 publish）只要看到 `PENDING/NOT_ATTEMPTED 且未被认领`，就可以认领并执行；
  已被别人 `RUNNING` 的不会被重复执行 ⇒ 既不漏也不重复处置（不自激）。
- **Safety 线程不可用/事件发送失败**：物理降险已在发布者上下文完成；`sp_stats.meta_pending` 保持置位，
  表示"整理（锁存/黑匣子/状态/详细日志）尚未落地"。`safety_status`/`motor_status` 必须如实打印
  `unreconciled-metadata`。这是"降级但不假装"的边界，不宣称状态机一致。
- **人工恢复期间**：`CLEARING=1` 时新预约被拒；若此时又有请求进来 ⇒ publish 照常 REQ++ 并重新置 PENDING，
  由 §5.5 第 4 步的票据比较使恢复失败关闭。

### 5.5 恢复与新请求竞争（修 NEW-02）

```
fault_reset():
  1) 快照票据:  level=disable(); g=sp_gate; if(!(g&INH)){enable(); refuse("not inhibited");}
                ticket=REQ(g); enable(level);                     /* INH 仍为 1, 期间 RUN 提交一律被拒 */
  2) 慢校验(全部在锁外, 与今天一致): 活动输入实测 / 许可焊盘 LOW / ENN 有界确认(前) /
     motor_disarm() / ENN 有界确认(后) / 诊断侧刷新 / 两条输出路径的本次结果有效性
  3) 进入 CLEARING:  level=disable(); if(REQ(sp_gate)==ticket && (sp_gate&INH)) sp_gate |= CLEARING;
                     else { enable(); refuse("newer request"); }   enable();
  4) 条件清锁存(带身份): motor_clear_stop_latch_if(ticket); step_pwm_clear_stop_latch_if(ticket);
        这两个函数在 mot_lock/无锁内只清"其 latch_req == ticket"的锁存; 若某锁存是被更新的一次请求
        置位的(seq != ticket), 一律保持 -> 审 §3 NEW-02 "不能清掉较新的失败锁存"。
  5) 恢复提交:  level=disable();
         if (REQ(sp_gate)==ticket && (sp_gate&INH) && (sp_gate&CLEARING)) {
             sp_gate = (sp_gate & ~(INH|CLEARING)) | ((REQ+1)<<10);      /* 清抑制 + 再自增, 单字一致 */
             ok = 1;
         } else { sp_gate &= ~CLEARING; ok = 0; }                        /* 保持 INH=1, 失败关闭 */
     enable(level);
     if (!ok) refuse("newer request during recovery"); else ss_state=MANUAL_CLEAR(经 ss_lock);
```
- **REQ 不回绕**：20 位、到达 `0xFFFFF` 即饱和，此时第 1/3/5 步一律拒绝恢复并如实打印
  `request sequence saturated -> 需要重启`，属 fail-closed。因此**不再**依赖"人手速度不可能跨一圈"
  这类被 v1 用来论证的估计（审 §3 NEW-02 末段与 §4.5 的批评）。
- v1 里"使能尝试只有毫秒级"的论证作废：本方案自身尚未消除全部 UART/锁等待，所以用"不回绕 + 票据"
  替代"回绕不可能"。
- 第 4 步使用带身份的比较清除，因此不出现"在固定指令短窗口里调用会取 `mot_lock` 的函数"这种自相矛盾
  （审 §3 NEW-02 对 v1 §5.8 的批评）：短窗口只翻转决策字；需要锁的清除动作放在 CLEARING 保护之下。

### 5.6 UART 端到端预算（修 NEW-04）

真实等待序（P-16）与逐段处理：

| 段 | 现状（file:line） | v2 要求 | 过期观察者 | 返回 |
|---|---|---|---|---|
| txn 取锁 | `WAITING_NO` `tmc2209.c:1299` | 保持非阻塞 | 调用者 | `-RT_EBUSY` → 计入 busy 连击 |
| cfg `lock` 取锁 | **FOREVER** `:735`（`:1301` 调用） | 改有界取锁（预算内），**并检查返回值** | 调用者（`rt_tick_get()` 差值） | 超时 → 整次复核过期 |
| xfer 取锁 | **FOREVER** `:282` | 同上 | 同上 | 同上 |
| flush | `while(rt_device_read(..)>0);` `:139-142` | 加迭代上限 + 截止时间双条件 | 同上 | 超上限 → 记 `RX_FLOOD`，整次过期 |
| TX | `rt_device_write` `:206` → `_serial_poll_tx` → HAL 每字符 ≤6000ms 忙等（P-14） | 传入远小于 6000ms 的 `tx_block_timeout`（或包一层预算），并检查 `!=n` | 同上 | 短写 → `-RT_EIO` 上抛 |
| RX | `deadline=+100ms` `:208-:273` | 保持；但必须从**函数入口**起算总预算，而不是 RX 段自己起算 | 同上 | `-RT_ETIMEOUT` |
| 日志 | `hex_dump`/多路 `rt_kprintf` 在 xfer 锁内（`TMC_TRACE_VERBOSE=0`，`:58`） | 保持默认关闭；任何诊断打印移出锁 | — | — |

- **总预算**由入口 `t0=rt_tick_get()` 起算，每段用剩余预算；因此"卡在锁/flush/TX"也会按时返回，
  不需要新建线程（审 §5 未强制新建线程）。数值本身 `TBD`（未测）。
- **过期即保护**：任何一段超时都按"配置不可信"处理，直接 `sp_publish(reason)+sp_execute()`，
  而不是等下一次复核。`-RT_EBUSY` 连击上限（现有 `MOT_GUARD_BUSY_MAX`）继续存在，但"忙"不再能掩盖
  端到端过期（busy 只对应 txn 非阻塞这一种情况）。
- **每拍 ENN 反馈检查**：保持在 UART 段之前（`motor.c:454-465`，单次 `rt_pin_read`）。因为 UART 段现在
  有界，最坏 ENN 响应推迟 = 有界预算，不再依赖"守卫没卡住"的隐含假设（审 §3 NEW-04 末段的问题）。
- **结果身份**：`config_epoch`（由 apply 成功时自增）+ `REQ`；晚到结果若 `config_epoch` 或 `REQ` 与当前
  不符 → 丢弃，且**不得**据此维持运行或撤销抑制（v1 的这一点保留）。
- 不能只把 RX timeout 改名为 deadline：本表逐段写明谁推进截止期（tick 由 SysTick 中断推进；
  因此任何段都不得在掩码窗口内 —— 与 P-20、§5.3 的禁令一致）。

### 5.7 配置写与模式恢复（修 NEW-05）

事实：`ns800_epwm_drv_set`（`drv_epwm.c:497-518`）= `configuration_convert`（`:289-332`，其中
`:318-319` 从 `TBCTL` 读 `count_mode` 存进局部 `signal_params`）+ `ns800_epwm_configure_signal`
（`:365-...`，其中 **`:371` 把 `signal_params->count_mode` 写回 `EPWM_setTimeBaseCounterMode`**）。
⇒ `rt_pwm_set` 会把"读取时刻"的模式恢复回去，属于 RUN-capable 写。

两种可行解，推荐 (i)：

| 方案 | 做法 | 代价 | 是否引入新窗口 |
|---|---|---|---|
| **(i) 配置写不恢复计数模式（推荐）** | 窄改 `configure_signal`：模式写回改为"仅在调用方显式请求换模式时执行"，`set` 路径不写 `CTRMODE`；RUN 只能由 §5.3 的 `PWM_START` 提交进入 | 需要改 HAL 一个函数的一条语句 + 一个参数/命令位；副作用边界：`configuration_convert` 的周期计算仍按**当前寄存器模式**算（`:307-311`），因此无并发时结果与今天逐位相同；有并发时不再恢复旧模式（正是要的行为） | 无（窗口保持一次模式写） |
| (ii) 整段 set 进窗口 | `rt_pwm_set` 调用移入掩码窗口内 | 窗口从"几条指令"变成"十余次寄存器写 + 浮点除法"（`:372-379` 的频率计算），时长必须实测；且 `RT_ASSERT` 失败即 panic 的风险进窗口 | 无交错，但窗口变长 |

必须纳入"危险/降险"清单的实际函数（审 §3 NEW-05 要求列出）：
`ns800_epwm_drv_enable`（`:581-586`，RUN-capable）、`ns800_epwm_drv_disable`（`:588-593`，降险）、
`ns800_epwm_configure_signal` 的 `:370`（prescaler，运行中改会改变频率——必须在 owner 有效时门内做）
与 `:371`（模式，见上）、`ns800_epwm_hw_init`（`:336-345`，写 STOP_FREEZE，降险方向，仅启动期）、
`ns800_epwm_drv_set` 其余 compare/shadow-load/action-qualifier 写（时间基冻结时不产生边沿 ⇒ 非 RUN-capable）。
⇒ 结论：**"所有 enable 都进门"这句 v1 口号被替换为上面这张按函数列出的清单**。

### 5.8 与 S1 合同的映射（修 NEW-03 的三个兼容问题 + 审 §4.1）

| S1 既有承诺 | v1 的冲突 | v2 的处理 |
|---|---|---|
| 诊断关断失败不释放所有权 | publish 前段就把 OWNER 归 NONE，之后冻结失败也不恢复 | 失败/未知一律 `OWNER=HELD_UNKNOWN`；只有 `rt_pwm_disable` 真返回 EOK 才允许 `OWNER=NONE` |
| PWM 与许可两个原始 rc 分列 | 快路径只存 `merge(e_step,e_perm)` | `sp_step_rc` / `sp_perm_rc` 两个独立字；合并值只作为对外的返回码，记账不留合并值 |
| `MOT_STOP_API_ACCEPTED` 的含义 | 裸寄存器写被记成"API 已接受" | 快路径冻结**调用真实 `rt_pwm_disable()`** 并用其返回值记账；许可方向区分"已写出(WRITTEN)"与"已回读确认(CONFIRMED)"，后者只能由门外 `safety_drv_enable_write` 的成功置位 |
| 人工恢复是唯一清除点 | 引入新清除口 | 仍是 `fault_reset`，只是清锁存改成"带票据身份的条件清除"（§5.5 第 4 步） |
| 一次 apply 失败回退不重复 disable（S1-R1 R04） | 担心与快路径冲突 | 审 §4.1/§3 明确：那是**单次动作**合同，不禁止后续显式停机或人工复核；快路径是另一次显式停机，必须各自记账，不覆盖前一次首错 |
| B-4：HAL 关断恒返回 EOK | v1 把它写成"S1 语义可能需要返工" | 降级为驱动观测能力边界；S1 只称"API 接受请求"，本就未承诺探测所有寄存器/物理故障；不因此改 S1 语义，也不把示波器列为设计前置 |

### 5.9 停止类别与门/宏的兼容（保持 v1 §5.9/§5.10 的结论）

受控减速 `motor_stop()` 与成功 `motor_disarm()` 不发布义务、不置 INH ⇒ 可启动性不被永久封死（I7）；
只有故障类请求与关断失败/未知形成 INH。六道原门、第 7 门、四个宏、白名单、txn 语义、反馈确认、
IFCNT/写入白名单合同均不改；`ss_lock→mot_lock`、`txn→mot_lock` 的既有锁序不改（新协议只用掩码窗口，
不新增第三把可等待的锁）。fail-closed：`-RT_EFULL`（REQ 饱和）、`rt_mutex_take` 非 EOK、
设备未取得、事件投递失败，一律拒绝 RUN 提交并保持 LOW，不得"未持锁继续写复合状态"。

---

## 6. 备选方向与取舍（含 v1 方案的处置）

- **A' v2（推荐）**：门内危险写 + 义务三态 + REQ 票据 + UART 端到端预算 + 配置写不恢复模式。
  不新增线程，共享状态只有 5 个字，窄改点集中在 `safety_state.c`/`motor.c`/`step_pwm.c`
  与 HAL 的一条 `:371` 语句。
- **B 单一输出执行者**：仍然要求执行者的首次动作无锁无控制台 ⇒ 实质仍需 A' 的门与义务模型；
  列为 A' 之后的可选演进，不是替代。
- **C 仅加锁超时 + 提交前复查**：不满足 I1/I4/I5/I10，**不可单独采用**（v1 结论保持）。
- 被 v1 提出但本轮撤回的形式：`sp_ctrl = u | RUN(owner)`（按枚举 `GRANTED_MOTOR=1 | RUN_MOTOR=2 = 3`
  会变成 DIAG 态，属掩码替换错误）；"每拍 set/enable 都要从 NONE 重新预约"（应改为 token 续用，见 §5.1 表）；
  "revoke 不核对身份"（必须比对 REQ+OWNER）。这三处都已在 §5 用状态表与掩码替换写法修正。

---

## 7. 纸面反例表 D1–D8（`STATIC_REASONING / NOT_EXECUTED`，不写 PASS）

| ID | 场景 | 关键步骤（v1 结果 → v2 结果） |
|---|---|---|
| D1 | commit 返回 → 完整快停 → 旧真实 RUN 写 | v1：步骤 1 门内 ok，步骤 2 停机完成，步骤 3 旧写仍执行 ⇒ 违 I1。v2：写本身在门内（§5.3），步骤 1 与步骤 3 之间不存在"已授权但未写"的状态；若停机先执行，后续 `REQ` 变化使旧 token 在第 1 项比较即失败 ⇒ 写不发生 |
| D2 | inhibit=1 的恢复期间来新故障（含同原因重复）、恢复提交最窄窗口 | v1：合并不动代次 ⇒ 恢复成功。v2：§5.5 步骤 2/3/5 三处都比较 `REQ`，重复请求同样 `REQ++` ⇒ 第 3 步或第 5 步拒绝，INH 保持 1，锁存不被清（第 4 步还有 `latch_req==ticket` 身份条件兜底） |
| D3 | ISR 为首请求；线程接续；首执行者被抢占；事件发送失败/Safety 不可用 | v1：ISR 只发布 ⇒ `first` 被消费，无人执行。v2：§5.4 发布者在同一上下文执行两个寄存器写；被抢占时动作态仍是 `PENDING/RUNNING`，任何后续上下文按表认领；Safety 不可用只影响整理，`meta_pending` 如实暴露 |
| D4 | STEP 或许可任一动作失败/不可用 | v2：`sp_step_rc`/`sp_perm_rc` 各自保留；失败 ⇒ `OWNER=HELD_UNKNOWN`（不提前交回所有权）；许可"已写出"与"已确认"分开；`fault_reset` 仍是唯一清除点 ⇒ 人工复核可达 |
| D5 | UART 卡在 cfg/xfer 取锁、flush 持续有数据、TX、RX 四种阶段 | v1：只有 RX 段有截止期 ⇒ 前三种可无限停。v2：§5.6 每段有界且传播错误，过期即 `publish+execute`；`-RT_EBUSY` 只覆盖 txn 一种情形，"忙"不再掩盖过期 |
| D6 | `set` 已捕获 RUN 模式 → 快停冻结 → 旧 `set` 写回 | v1：`configure_signal:371` 把 RUN 写回，时间基恢复 ⇒ 违 I10。v2 方案 (i)：该写被移除，RUN 只能经门内 `PWM_START`；方案 (ii)：整段 set 在门内且资格判定含 `INH` ⇒ 停机先发生则 set 读到的是冻结值 |
| D7 | arm→start→多拍 ramp→受控 stop→再 start；disarm→rearm；诊断成功/失败 | §5.1 转移表 + token 续用：ramp 每拍的 `set`（非 RUN-capable）与需要的 `PWM_START` 都用同一 token 的 `REQ`；受控 stop 不置 INH ⇒ 可再次 start；成功 disarm 后 OWNER=NONE 可 rearm；诊断失败路径 OWNER=HELD_UNKNOWN，需恢复后才再可借用 ⇒ 不永久封死也不放松 |
| D8 | 旧 token 的 revoke/晚到确认在新恢复与新预约之后到达 | `sp_revoke`/确认都必须比对 `REQ + OWNER`：不匹配 ⇒ 不释放新持有者的所有权、不改状态，确认路径只能走降险方向（写 LOW）并返回 `-RT_EPERM`；`REQ` 饱和不回绕（§5.5）⇒ 票据不会因回绕被重新满足 |

---

## 8. 计划用例校准（只设计，不执行）

S2-P01..P11 的前置与断言按 v2 改写，并新增 P12：

- **P01/P02 不变**：另一线程**真实持有** `ss_lock`/`mot_lock` 且不释放，保护阶段仍完成；
  禁止空锁桩或先释放锁的样例。
- **P03**：按 §5.6 的分段过期，分别注入 cfg lock、xfer lock、flush 持续有数据、TX 短写、RX 超时五种，
  断言"过期即 publish+execute"，并断言 `meta_pending` 与实际动作态一致。
- **P04/P05**：arm 停在资格判定与 HIGH 写之间（v2 里这两者在同一窗口 ⇒ 断言"不存在可停的中间态"，
  并断言停机先行时 HIGH 写次数恒 0）；停在 HIGH 之后、`armed` 提交之前 ⇒ 断言陈旧 `armed` 不产生
  许可 HIGH，且回滚只走降险方向。
- **P06/P07**：诊断 grant 后、`set` 后/`enable` 前；断言 `PWM_START` 被门拒且 STEP 写次数 0。
- **P08**：最窄提交窗口的**线性化**判据（按审 §3 NEW-01 的要求重写）：允许合法的"先开启、后停机"顺序，
  只禁止"停机生效后旧操作再开启"；台账需能重建每次比较/写入的顺序（`(REQ,OWNER,kind,写次数)` 序列），
  不得把"不同时刻出现过 RUN 与 inhibit"当作必然错误；模型里不得强行执行本应被屏蔽的 ISR。
- **P09**：D2 的恢复竞争 + 多次合并请求 + 晚到结果；断言旧恢复失效且 `latch_req` 身份保护生效。
- **P10**：D4 的动作失败矩阵（含 `step_pwm` 设备缺失、事件投递失败、Safety 未启动）。
- **P11**：D7 的正常对照；断言四宏 FALSE 时危险动作仍全部被拒，INH 只由故障类请求形成。
- **P12（新增，D6）**：`set` 接缝用例——夹具让 `configuration_convert` 的"读模式"与
  `configure_signal` 的"写模式"之间可插入快停；断言修正后时间基不被恢复；并提供正常对照
  （无停机时 `set` 的周期/比较值仍按当前模式正确计算）。

夹具要求（不变）：真实互斥/可确认屏障、两种宏环境、只看返回值与计数、反向注入须"构建成功 + 合同
`rc=1` + 台账自洽 + 指定身份命中"、零执行/崩溃不算检出、尽量扩展既有 `ps_main.c`/`shim_ps/`，
不新建 runner 或元测试平台。

---

## 9. 剩余阻断与需要后续决定的事项（重新分类）

**已由源码闭合（本轮）**
- B-2（缓存/屏障）：P-17/P-21 —— I/D-Cache 无条件开启；EPWM1/GPIOA 位于 `0x4000_0000/0x400A_0000`
  的默认外设区（Device）⇒ 不被缓存；**但** Device 区允许写缓冲，故"窗口内写完立刻依赖其效果"仍需
  `__DSB()`（SDK 自身在 `SYSCON_UNLOCK` 里就是这么做的）。设计要求：门内写之后若在同窗口内还要读同一
  外设并据值判定，必须插屏障；跨窗口的读由现有 settle+回读覆盖。
- B-3（EPWM 模块级影响）：本轮全仓只找到 `motor.c` 与 `step_pwm.c` 两个 EPWM 使用者（`grep`），
  且 enable/disable 作用于该实例时间基 ⇒ Motor 与诊断共用同一时间基，"冻结一方即冻结另一方"
  是**设计事实**而非缺陷；残余：同实例其它通道 B（`EPWMX_B`）若被其它功能启用会一起被冻结 ⇒ 记 M-2。
- B-5（旁路写入口，按审 §4.6 分成三类）：① 通用 `pin` 命令的有效引脚 write 直达 `rt_pin_write`，
  **足以构成维护旁路**；② `pin_status` 对许可写的是 **LOW**（降险方向，但会改影子/状态）；
  ③ `selftest` 会 `motor_init`/写输出脚。三者职责不同，不能混成一种写。本轮不改通用驱动、不删命令。

**只能靠测量关闭（延期，需要新授权与仪表）**
- B-1：门内窗口的实际时长（enable 路径：一次框架调用 + 一次 `MODIFY_REG`；方案 (ii) 下为十余次写 + 浮点）
  与 UART RX/EXTI 在该时长内的丢失容忍度。硬件 FIFO/波特率/时钟参数本轮未逐项记录，
  且**不得**用 64 字节软件 RX ring 去推断硬件 IRQ 延迟容限（审 §4.5）。
- M-1：另一核/其它总线主设备是否可能写同一 GPIO/EPWM（B-b 模型边界）。
- M-2：EPWM1 同实例其它通道的实际用途。
- M-3：写缓冲完成时刻（是否需要 `__DSB()` 才算"写完"）——源码层面已给出规则，物理上是否必须实测。

**明确不再向用户索取的**：已登记为 `PASS (USER_CONFIRMED)` 的静态电气与现有 SW1 链功能，
本轮不重复索取五点电压，也不新增示波器作为设计前置（审 §4.1/§5）。

**需要审查方/用户决定的取舍（缩小到真正必要的两项）**
1. NEW-05 采用方案 (i)（窄改 `drv_epwm.c:371`，让配置写不恢复计数模式）还是方案 (ii)（整段 `set` 进窗口）？
   v2 推荐 (i)，理由是窗口保持极短且语义在无并发时逐位等价；(ii) 的窗口时长依赖 B-1 实测。
2. `REQ` 位宽与饱和策略（本轮取 20 位 + 饱和即拒绝恢复）是否可接受，或改票据式（独立恢复票）。
   模块归属（放 `safety_state.c` 还是新增窄接口文件）**按审 §5 不再作为用户决策项**：
   v2 采用"一个内部协调 owner + 对外窄接口，优先复用现有模块"。

---

## 10. 实现工作包重排（按审 §5 的建议）

- **WP1 纵向小闭环（第一次实现必须整体成立）**：`sp_gate` 状态机 + `publish/execute/ask/commit/revoke/confirm`
  + 恢复票据 + `latch_req` 身份清除 + NEW-05 选定的模式写处理 —— 请求、真实写提交、撤销、恢复
  **一起**可证明；不接受 v1 的"先做门（WP1）再做发布/快停/恢复（WP2）"的切法，因为那样任何一半
  都不能声称满足停机优先。验收：S2-P01/P02/P04..P09/P11/P12 在两种宏变体真执行 + 既有 S1 台账
  48/64 与两项反向不变弱 + Keil 0/0。
- **WP2 UART 端到端预算**（§5.6 表）：有界取锁并检查返回值、flush 双上限、TX 预算、总预算过期即保护。
  验收：S2-P03 + 既有 TMC/IFCNT/白名单合同用例不变弱。
- **WP3 目标机测量**（B-1/M-1/M-2/M-3）：需要仪表与新授权，继续后置；
  **结构性消除某个 `FOREVER` 不等于 RISK-03/03a 已关闭**。
- 每包单独交独立复审；不合并成大提交；暂不锁定"三轮独立返修"的额度。

---

## 11. 完成度诚实表述与停止点

| 交付要求 | 状态 |
|---|---|
| 关键源码/真实配置有依据 | 完成（§2 P-1..P-21，控制台/UART/缓存/模式回写本轮亲读） |
| 请求到动作的等待表完整 | 完成（审计 §3/§5 + §5.6 分段表） |
| 一个推荐方案能解释所有开启提交与人工恢复竞争 | 协议已给出并逐条对答 NEW-01..05；**残余依赖 B-1 实测与 NEW-05 的方案选择** ⇒ `PARTIAL` |
| 计划用例可证伪 | 完成（§8，含新增 P12 与 D6 接缝；全部 `PLANNED / NOT EXECUTED`） |
| 改动严格限四份文档 | 完成 |
| 不以实现测试通过或硬件结果作为本轮验收 | 保持：`tests_executed=[]`、`hardware_actions=[]` |

v1 的"方案本体自洽、已覆盖 I1–I8"结论已由本文开头的对照表**作废并重写**；v2 仍**不是**实现、
**不是**测试通过、**不是**硬件结论，也不登记 S2 实现 GO。

状态：**`AWAITING_INDEPENDENT_DESIGN_REVIEW`**。不进入实现/返修/板级/F2e/F3；
审查通过并另下实现任务书之后才可动工。本文件不构成任何硬件授权。
