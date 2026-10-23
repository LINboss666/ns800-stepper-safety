# S2-A 停机优先关系 —— 设计方案（DESIGN ONLY，未实现）

任务 ID：`phase8-stop-priority-s2a`
分析对象（analyzed_commit）：`b410da36a383a367e448cdf1f0fad58eb28b7530`（本轮审计与设计的固定基线）
返修/接受历史：S1 生产 `8ca4f6a` → GO `32a311a` → GO 登记 `b410da3`（本轮起点）
范围：**只交方案**。生产代码、`tests/`、Keil 工程、旧证据全部冻结；本轮 `tests_executed = []`、
`hardware_actions = []`。设计审查通过 ≠ 实现批准。

配套证据文件：`reports/reviews/phase8-stop-priority-s2a/source_audit.txt`（下称 **审计**，条目号 `W-*`/`R-*`/`E*`/`C*`）

---

## 1. 要解决的问题（一句话）与验收目标

**问题**：安全停机的"第一次真正降险动作"目前排在可等待的状态锁、可忙等的控制台写与可长耗时的
许可回读之后；同时"使能/输出提交"是一条**跨多步、无序列化边界**的序列，停机请求落在任何一步间隙里
都可能被随后的提交动作重新打开输出。

**目标（可判定的不变量，全部由 §4 定义）**：请求一旦形成，(a) 不再有任何新的使能提交能够生效；
(b) 两条独立降险动作（STEP 冻结、许可 LOW）各自都会被无条件尝试；(c) 以上都不依赖任何可以无限等待的
锁、控制台或 UART；(d) S1 已接受的合同（两个原始 rc 分列、失败锁存、诊断所有权、人工恢复唯一清除、
一次成功不自动恢复、统一停机不自激、API 接受 ≠ 无脉冲）全部保持。

**不是本方案目标的**：不宣称停机时延上界；不关闭 RISK-03/03a；不做硬件动作；不翻四宏；不改黑匣子格式。

---

## 2. 证据等级与本方案的假设

沿用审计 §1 的等级（DV 亲读 / AC 抽查 / AC! 未复核 / ED 推导 / TBD 未知）。
本方案**只**建立在下列事实上：

| 编号 | 事实 | 等级 | 出处 |
|---|---|---|---|
| P-1 | ss_lock / mot_lock / tmc_xfer_lock / cfg `lock` 全部 `RT_WAITING_FOREVER`，且返回值一律未接收 | DV | 审计 W-1/W-2/W-3/W-4 |
| P-2 | cfg `txn` 是 `RT_WAITING_NO`（非阻塞） | DV | 审计 W-4 |
| P-3 | 单核：`RT_CPUS_NR 1`、无 SMP、编译 `scheduler_up.c` | DV | 审计 §2 |
| P-4 | `rt_hw_interrupt_disable` = `MRS PRIMASK / CPSID I`（全掩码，3 条指令） | DV | context_rvds.S:38-43 |
| P-5 | 许可与 DIR 的 `rt_pin_write` 落到 GPIO **SET/CLR** 寄存器写，驱动内无锁、无循环 | DV(叶子) / AC!(HAL 细节) | 审计 §9 drv_gpio.c |
| P-6 | EPWM enable/disable = 对 `TBCTL.CTRMODE` 的一次 MODIFY_REG，**模块级**、无 per-channel 语义、返回值恒 EOK | DV | 审计 §8 |
| P-7 | `rt_device_control` 不检查 `ref_count`；epwm1 从未 open 也能到达驱动 | DV | 审计 W-11 |
| P-8 | 控制台写走 poll 分支、逐字符**忙等**（不让出 CPU），无 `THREADSAFE_PRINTF`，TX 超时 6000ms/字符 | DV(.config/rtconfig) + AC!(循环体) | 审计 W-7 |
| P-9 | `rt_thread_mdelay` 是让出 CPU 的 tick 等待；无 soft timer 线程 | DV | 审计 §2/W-6 |
| P-10 | 优先级继承存在且由 `RT_USING_MUTEX` 决定（本板开启），但**不缩短**持锁者自身的 mdelay/忙等 | DV | 审计 W-10 |
| P-11 | 事件投递只做 `set |= bits` + 唤醒；返回值不表示"已被执行"；Safety 线程不存在时事件被静默搁置 | DV | 审计 W-9 |
| P-12 | `blackbox_trigger` 已在用 `rt_hw_interrupt_disable` + 只置标志 + 早到不覆盖并计数 | DV | 审计 W-8 |
| P-13 | 本构建把 AHBAHB/APB 外设归属寄存器写 0（本核），`DUAL_CORE_ENABLE=0` | DV | board/board.c:140-143,180-190 |

**残余假设（必须显式承认，不可当已证明）**
- A-1（TBD）P-8 的循环体与 `CONSOLE_TAKE` 空宏是子代理摘录，实现轮要逐行复核同一函数后才能引用为判据。
- A-2（静态证据，无运行时证明）P-13 只说明本固件的初始化写法；不证明另一核在任何复位/引导状态下不写 GPIO。
  ⇒ 因此本方案的临界区**不依赖**"另一核绝不写"这一前提，只依赖"同一核内不并发"（P-3/P-4）。
- A-3（TBD）外设寄存器写是否需要 `__DSB()`/缓存屏障才能保证"写已生效"：本 SDK 在 SYSCON 写后确实使用
  `SYSCON_UNLOCK` 宏内含 `__DSB()`（DV，`STD/StdDriver/Inc/syscon.h:449`），而工程编译了 `cpu_cache.c`
  （DV，project.uvprojx:503-505）。⇒ 记为**设计阻断 B-2**（§8），实现轮必须以源码（不需要硬件）确认 GPIO/EPWM
  区域的缓存/缓冲属性后再定临界区写法。

---

## 3. 现状：请求 → 首次保护动作的真实等待链（DV，行号取 b410da3）

```
硬件事件 ISR ──post──▶ rt_event ──▶ Safety(4) 线程
                                        │ rt_kprintf("[SAFETY] EVT_*")      ◀── 忙等控制台(P-8)，在任何动作之前
                                        ▼
                              safety_force_shutdown(code)
                                        │ ① rt_mutex_take(ss_lock, FOREVER)  ◀── 可能被 tshell(20) 的
                                        │                                       fault_reset 持有（内含
                                        │                                       2×有界反馈确认 mdelay +
                                        │                                       motor_disarm + ≥10 次打印）
                                        ▼
                              motor_emergency_stop()
                                        │ ② rt_mutex_take(mot_lock, FOREVER) ◀── 可能被 motor(8) 线程的
                                        │                                       rt_pwm_set/enable 持有
                                        ▼
                              ③ rt_pwm_disable → TBCTL=STOP_FREEZE   ◀── 真正的第一个降险动作在这里
                              ④（已释放 mot_lock）许可 PC.23 写 LOW，内含 mdelay(1)×≤2 + 回读
                                        ▼
                              ⑤ 打印/反馈采样/blackbox_trigger/写 FAULT_LATCHED
```
另有两条**不经过** Safety 线程的自停路径（守卫与诊断命令），它们已经"自己先做动作"：
`mot_guard_shutdown`（审计 E3，motor.c:380-406）与 `motor_disarm`（E2/E6）。

**使能提交侧今天的形状**（审计 C1–C5）：`查门 → (UART 复核) → 再查门 → 有界反馈确认(mdelay) → 写 HIGH
→ 反馈确认(mdelay) → armed=1`，共 7 步、跨 2 把锁、无序列化边界；诊断侧 `grant → pwm_set → pwm_enable →
step_enabled=TRUE` 4 步、**完全无锁**（C3，审计 R-2 成立）。

静态交错见审计 §6（R-1 arm 在停机后仍可能提交 armed、R-2 诊断在停机后仍可 enable、R-4/R-5 人工恢复与
新故障互相搅扰且可能抹掉较新锁存）。全部标 `STATIC_INTERLEAVING`，**不是运行复现**。

---

## 4. 不变量（方案的验收定义）

| ID | 不变量 | 现在的违反形状（审计） |
|---|---|---|
| I1 | 请求可见性：停机请求一经形成，任何**之后**的使能提交都必须失败，且不依赖慢锁是否已到手 | R-1, R-2 |
| I2 | 动作独立性：STEP 冻结与许可 LOW 各自被无条件尝试一次；一条不成功/不可用不豁免另一条 | 现状成立（顺序执行），但排在锁后 |
| I3 | 快路径无阻塞源：形成请求 + 两个降险动作的路径上不得出现 `RT_WAITING_FOREVER`、控制台写、UART、`mdelay` | W-1/W-2/W-7/W-6 |
| I4 | 线性化点：使能是否生效由**一个**可比较的事实决定（寄存器写与请求标志在同一临界区内比较） | C1–C5 的 TOCTOU |
| I5 | 恢复单调：人工恢复只能清除**它自己观察到的那一代**请求；较新的请求/失败锁存不得被旧恢复清掉 | R-4, R-5 |
| I6 | 证据不丢：元数据锁不可得时，最小事实（谁、什么结果、计数）仍被记录，且不因此推迟保护 | W-1 持锁打印、W-8 已有 pending 不覆盖并计数 |
| I7 | 可启动性不被永久封死：受控减速 `motor_stop`、成功 `disarm` 都不形成抑制；只有"故障类请求"与"关断失败/未知"形成 | 审计 E8 |
| I8 | S1 合同零回退：`api-accepted` 语义、两个原始 rc、失败锁存、诊断所有权、唯一人工清除点、不自激，全部保持 | 审计 §8 |

---

## 5. 推荐方案：短保护阶段 + 抑制代次 + 提交门（并把慢处理留在原线程）

### 5.1 状态与职责划分（回答"谁持有 inhibit/代次/所有权"）

新增一组**极小**共享事实，放在 `safety_state.c`（它是安全决策的 owner，且 fault_reset 已在此），
对外只暴露 4 个函数；不新增线程、不新增生产 `.c`（若实现轮认为体积/耦合更合适，可拆成
`stop_priority.c` —— 属于实现细节，不在本轮决定）。

```c
/* 单一决策字: 所有相互依赖的事实必须能在一次 32 位读里同时取到（§5.4） */
volatile rt_uint32_t sp_ctrl;
/*   bit 0      : inhibit   —— 1 = 禁止一切新的使能提交
 *   bit 1..3   : owner     —— 所有权状态机（见 §5.3）：
 *                              0=NONE 1=GRANTED_MOTOR 2=RUN_MOTOR 3=GRANTED_DIAG 4=RUN_DIAG
 *   bit 4..31  : generation —— 每次 inhibit 边沿 +1（28 位）
 */
#define SP_INHIBIT   (1u << 0)
#define SP_OWNER_MSK (7u << 1)
#define SP_GEN_SH    4u

/* 独立证据字（不参与门比较，各自单字读写，允许在掩码窗口外写） */
volatile rt_uint32_t sp_reason;    /* 请求原因位图，幂等 OR 合并 */
volatile rt_uint32_t sp_fast_seq;  /* 已完成快保护动作的次数 */
volatile rt_uint32_t sp_merged;    /* 被合并掉的重入次数（不丢证据） */
volatile rt_int32_t  sp_fast_code; /* 最后一次快动作结果码（记账待补时也能看到） */
```

| 概念 | 谁置位 | 谁清除 | 与 S1 的关系 |
|---|---|---|---|
| `inhibit`（禁止再使能） | 任何"故障类停机请求"的形成者：ISR 回调、守卫、`pwm_test stop` 失败、`disarm` 失败、boot 失败 | **只有** `fault_reset()` 在 I5 的代次比较下清除 | 与 `stop_fail_latched` 是**不同职责**：latch 是"证据/未复核的失败"，inhibit 是"许可权限"。两者都要成立才允许清除（沿用现有唯一清除点） |
| `generation` | 每次 inhibit 边沿（置位与清除）都 `+1` | 同上 | 使"在途旧资格"必然失配，不需要给每个请求发 ID |
| `owner` | 提交门授予（MOTOR / DIAG） | 冻结成功、提交失败或快保护执行后归 NONE | 取代今天 `step_enabled`/`mot_dev_ok` 的隐式所有权语义，且把诊断侧无锁状态纳入同一机制（修 R-2） |

**为什么用"比较相等"而不是"数值差判新鲜度"**：代次只用于**相等性**判定（token 内记录的 gen 与当前 gen
是否相同）。28 位计数回绕（2^28 = 268,435,456 次 inhibit 边沿）只有在"一次使能尝试的毫秒级窗口内发生
约 2.7 亿次 inhibit 边沿"时才可能造成误判，而 inhibit 的清除只能通过人工恢复（人手速度）、置位由故障
驱动 ⇒ 实际不可能跨越一整圈。明确**禁止**把代次用于"按年龄过期"的逻辑（那才会被回绕伤害）。
旧 token 的 ABA 由"预约式所有权"排除：owner 状态必须由 `sp_enable_ask` 在同一个掩码窗口里从
`NONE→GRANTED_x` 独占取得，取得后**别人一律被拒**，因此不存在"期间被别的申请改了又改回来"的形状；
token 本身不进任何池，只携带 `(owner_state, gen)` 值。


### 5.2 短保护阶段（fast path）—— 唯一允许的动作集合

```
sp_fast_protect(reason):            /* 任意线程上下文可调用；ISR 也可调用 */
    level = rt_hw_interrupt_disable();
        old  = sp_ctrl;
        gen  = old >> SP_GEN_SH;
        owner = old & SP_OWNER_MSK;
        if (!(old & SP_INHIBIT)) {
            sp_ctrl = ((gen + 1u) << SP_GEN_SH) | SP_INHIBIT | SP_OWNER_NONE;  /* gen+1, 置位, 所有权强制归零 */
            first = 1;
        } else {
            sp_merged++;                                                          /* 已有请求在办 */
            first = 0;
        }
        sp_reason |= bit(reason);
    rt_hw_interrupt_enable(level);

    if (!first) return;             /* 不重复降险、不重复投递、不自激 */

    /* 两个彼此独立的降险动作，各自无条件尝试一次，各自记账： */
    e_step = fast_pwm_freeze();     /* 无条件一次 TBCTL=STOP_FREEZE 写（P-6：寄存器写本身无循环无锁） */
    e_perm = fast_permit_low();     /* GPIO CLR 寄存器写（P-5），不回读、不延时 */
    sp_fast_code = merge(e_step, e_perm);
    sp_fast_seq++;
    /* 结果先落到最小原子字段，慢路径再补全 S1 的 stop_result/src/rc */
```

窗口内**只有**：一次读-改-写一个 `rt_uint32_t`、一次 `|=`、两个计数器自增；以及各自的寄存器写。
不含函数调用可能睡眠的东西。理由与证据：P-4（3 条指令的掩码）、P-5/P-6（寄存器写无循环无锁）。
禁止清单（I3）：`rt_mutex_*`、`rt_event_*`、`rt_device_*`、`rt_kprintf`、`rt_thread_mdelay`、
`safety_drv_enable_write`（它含 mdelay，见 W-6）、`tmc2209_*`（UART）、`blackbox_*` 写盘。
（`blackbox_trigger` 本身只做掩码+置标志，P-12 已证明可用，但为把窗口长度压到最低，仍放慢路径。）

慢路径（Safety 线程或调用线程自己）继续做今天 `force_shutdown` 的全部事：回读确认、ENN 反馈采样、
详细打印、`blackbox_trigger`、状态锁存、S1 记账。**顺序改变但语义不变**：先降险，后整理。

### 5.3 使能提交门（回答"真正的序列化边界与线性化点在哪里"）

所有 `C1/C2/C3/C4` 提交点统一改成"测量在锁外、提交在比较门内"：

```
token = sp_enable_token_ask(owner);   /* 掩码窗口内: 要求 inhibit==0 且 owner==NONE，
                                         然后把 owner 置成 GRANTED_<owner>(独占预约) 并记下当时的 gen */
... 慢校验（门求值、UART 完整性复核、许可回读、反馈采样）...   /* 可长，可失败，不持决策字 */
if (sp_enable_commit(token) != RT_EOK) { /* 被拒：不许诺，不写寄存器 */ return -RT_EPERM; }
/* 只有越过这一行才允许写使能寄存器（EPWM CTRMODE / GPIO SET） */
```
`sp_enable_commit` 是唯一的线性化点：
```
level = rt_hw_interrupt_disable();
    u   = sp_ctrl;
    ok  = !(u & SP_INHIBIT)                                    /* 期间没有新的停机请求 */
          && ((u >> SP_GEN_SH) == token->gen)                  /* 同一代：没人恢复又触发过 */
          && ((u & SP_OWNER_MSK) == GRANTED(token->owner));    /* 预约仍在自己手里 */
    if (ok) sp_ctrl = u | RUN(token->owner);                   /* GRANTED -> RUN（所有权提交） */
rt_hw_interrupt_enable(level);
if (!ok) { sp_revoke(token); return -RT_EPERM; }
```
要点（直接回答任务书 §5 第 3 条）：
- 单加一句"enable 前再查一次 epoch"**不够** —— 查到写之间仍可被抢占。方案把"三项比较 + 所有权提交"
  放进同一个掩码窗口（P-3 单核 + P-4 全掩码 ⇒ 窗口内不可能有并发），因此**检查与提交是一个原子动作**。
  真正的外设写仍留在窗口外（不把 `MODIFY_REG` 的读-改-写拉长），其安全性由"只有 `RUN_<self>` 持有者
  才允许写"这一被原子提交过的事实保证；停机侧则在窗口内把 owner 强制归 `NONE` 并 bump 代次，
  两侧比较的是同一个字，不存在"双侧都通过"的世界。
- 被拒后的 `sp_revoke` 只做 `RUN/GRANTED -> NONE` 的归还，不重试、不改代次。
- STEP 的 `pwm_set`（周期/脉宽）可以留在窗口外：时间基处于 STOP_FREEZE 时改比较值不产生脉冲；
  **frozen→running 的那一次写才是危险转移**，它就是线性化点（P-6）。

- `mot_drv_en_request(1)` 必须拆成 `commit`（掩码内比较 + GPIO SET）与 `confirm`（settle/回读/重试，慢，
  在锁外）。回读失败不再"打开后又悄悄留着"：它把结果记入 S1 记账并**再次**走 `sp_fast_protect`
  （幂等，因为 `first` 已消费过，会用新的原因位合并）。
- ISR 只允许：置 inhibit + 置原因（`sp_fast_protect` 的前半段），**不允许**写许可寄存器（避免在
  掩码嵌套里再做外设 RMW；且当前 IRQ 门 CLOSED，审计 E1）。ISR 之后的完整降险由它唤醒/或由高优先级
  线程完成 —— 若 Safety 线程被锁挡住，守卫线程（motor 8 / diag 9）自己就能完成快路径（§5.2 可被任意
  线程调用正是为此）。

### 5.4 临界区的正确性论证（`volatile` 不是充分论证）

- 互斥：单核（P-3）+ 全掩码（P-4）⇒ 窗口内没有其它线程、没有 ISR ⇒ 对 `sp_ctrl` 的读-改-写不会交错。
  这不是"volatile 提供原子性"，而是"临界区内根本没有并发"。**必须**统一使用 `rt_hw_interrupt_disable`
  （内核自身与其它驱动都这么用，P-12），不得混用 `rt_enter_critical`（只挂起调度，不挡 ISR）。
- 可见性：`volatile` 只保证编译器每次都真的访问内存，不提供顺序、不保证外设写已被观察到。
  因此：(1) 所有跨上下文状态放**对齐 32 位字**；(2) 需要外设"写生效"的顺序处，实现轮按 A-3/B-2 的结论
  决定是否需要 `__DSB()`（本 SDK 已有先例，P-13 的 SYSCON 写法）；(3) 任何"两个字段合起来才成立"的断言
  都禁止 —— 状态必须编码在同一字里。
- 掩码代价：PRIMASK 全掩码会推迟 UART RX/GPIO EXTI。⇒ 硬约束：**窗口内指令数固定且无循环**，
  实现轮必须写出窗口内容清单并把它作为用例判据（S2-P08），不得把 `rt_kprintf` 之类"顺手加进临界区"。

### 5.5 UART 与配置新鲜度（回答"UART 不能阻塞快保护"）

- 快路径零 UART（I3）：现状已经如此（审计 E3/E4 的关断与许可写都不读总线），差别是把它从"排在锁后"
  变成"不排队"。
- 配置守卫保持**在当前线程**做有截止期的复核（沿用 `TMC_REPLY_TIMEOUT_MS` 与 `-RT_ETIMEOUT` 出口，
  审计 W-5），**不预先新建线程**。规则改为可判定的两条：
  1. 复核结果必须携带它所属的 `(gen, config_epoch)`；与当前不符的晚到结果一律丢弃，且**不得**据此
     维持运行或复活许可（回答"晚到结果不得复活已失效许可"）。
  2. `-RT_EBUSY`（txn 被占）的容忍轮数 `MOT_GUARD_BUSY_MAX` 语义保留，但"忙"不能无限掩盖过期：
     超限即按"配置不可信"处理 = 走 `sp_fast_protect`，而不是继续等下一次（现状已有该上限，
     motor.c:361-371，本方案只把"过期"定义绑到代次上，避免用"上次成功时间"无限延长有效期）。
- 若未来采用异步 UART 工作线程（备选 B，§6）：必须规定 `last_success_seq`、请求超时=过期=保护动作、
  结果代次绑定，且工作线程自身不得成为快保护的依赖。

### 5.6 多请求合并与自激（回答 I6/I8 的"不自激"）

- 合并：`inhibit` 已置位时后来的请求只做 `sp_reason |= bit` + `sp_merged++`，**不**重做降险、**不**重投事件。
  首故障优先语义与 `blackbox_trigger` 的既有实现一致（P-12）。
- 自激：`force_shutdown`/`step_pwm_force_stop` 仍然绝不投事件（I8，审计 §8）；`motor_disarm`/`pwm_test stop`
  的失败投递保留一次，但因为 `sp_fast_protect` 幂等 + "投了不等于执行"（P-11），Safety 线程再执行
  `force_shutdown` 时看到的是"已降险"的状态，只做整理。闭环仍不存在。

### 5.7 元数据锁不可得时（回答"如何不丢证据又不挡保护"）

- 快路径只写 4 个原子字（原因位图、fast_seq、merged、`sp_ctrl`），**不取任何互斥量**。
- S1 的富记账（`stop_result/stop_src/stop_rc/latch`）继续在 `mot_lock` 内更新；若 `mot_lock` 拿不到
  （理论上 FOREVER 会拿到，但为消除"证据阻塞保护"这一顺序依赖）：**保护已经做完**，记账在慢路径补；
  同时把"最后一次快动作的结果码"写入 `sp_fast_code`（单字）以便状态命令如实报告"记账待补"。
- 状态命令（`motor_status`/`safety_status`）必须显示 `inhibit/gen/merged/fast_seq`，否则"看不见"就等于
  "无法证伪"。这一条是验收判据，不是可选优化。

### 5.8 人工恢复与竞争（回答"fault_reset 与新故障谁赢"）

`fault_reset` 的流程改为：
1. 掩码内快照 `(gen0, inhibit0)`，要求 `inhibit0 == 1`（仍被抑制）否则按现状拒绝。
2. 慢校验全部在**不持 ss_lock** 的情况下做（活动输入实测、许可焊盘 LOW、ENN 前后两次有界确认、
   disarm、诊断 force_stop 刷新）—— 这是消除 R-5（恢复期挡住新停机）的关键改动。
3. 提交：掩码窗口内比较 `sp_ctrl` 的 gen 是否仍为 `gen0`；不等 ⇒ 有更新的一代 ⇒ **拒绝**并打印
   `refused: newer inhibit(gen=%u)`；等 ⇒ 清 `inhibit`、`gen+1`、清两条 S1 锁存、写 `MANUAL_CLEAR`。
4. 进入 `MANUAL_CLEAR` **不**自动 arm/enable（沿用现状），旧 token 因 gen 变化必然失配（I5）。
⇒ **新故障赢**：任何在 2/3 之间形成的请求都会 bump gen，使这次恢复拒绝，且不会清掉较新的失败锁存。

### 5.9 普通受控减速 vs 安全停机（回答 I7）

| 动作 | 形成抑制？ | 说明 |
|---|---|---|
| `motor_stop()`（target=0，保持 armed） | **否** | 审计 E8；只记 `CMD_ZERO_UNCONFIRMED` |
| `motor_disarm()` 成功 | **否** | 只是释放许可，随后仍可 arm |
| `motor_disarm()` 关断失败 | **是**（已有 EVT_SOFT_FAULT，改为先走快保护） | S1-R1 R03 的语义强化 |
| `safety_force_shutdown` / 守卫 / 诊断 stop 失败 / boot 失败 | **是** | 故障类 |
| 人工恢复 | 清抑制（带代次比较） | §5.8 |

### 5.10 fail-closed 规则（回答"锁超时/初始化失败/设备不可用怎么办"）

- 抑制结构本身无需 init（静态零初值 = `gen=0, inhibit=0`）；**但**上电安全态必须由
  `safety_gpio_boot` 的"许可 LOW 写+回读"建立（现状已有，审计 safety_gpio.c:129）。
  若该建立失败 ⇒ `sg_ready=FALSE` ⇒ 现有第 3/4 门已经拒绝一切使能（不改）。
- `rt_mutex_take` 一律**改为检查返回值**，任何非 `RT_EOK` 的处理是：不调用可能睡眠的确认、
  仍执行快路径降险（不依赖锁）、把状态记为 `NOT-verified` 并拒绝一切使能提交。
  ⚠ 明确否决两种做法：(a) 只把 `FOREVER` 改成常数而不处理 `-RT_ETIMEOUT`；
  (b) `*_lock_ok==FALSE` 时"跳过取锁继续写共享结构"（审计 W-1 的现状 idiom）—— 在引入抑制字之后，
  这条 idiom 必须收窄为"只允许写单字抑制/原因，禁止写需要一致性的复合状态"。
- 设备不可用（`mot_pwm==RT_NULL` / `mot_dev_ok=FALSE`）⇒ 提交门必拒 + `DEV_UNAVAILABLE` 记账（S1 现状）
  + 快路径仍必须尝试许可 LOW（R02 已确立，保留）。

---

## 6. 备选方案比较

| 维度 | **A 短保护+提交门（推荐）** | **B 单一输出执行者线程** | **C 最小改动：锁超时+提交前复查** |
|---|---|---|---|
| 满足 I1/I4（停机后不能再提交） | 是（掩码内比较，线性化点唯一） | 是（所有 enable 串行化到一个线程） | **否** —— 复查与写之间仍有窗口（§5.3） |
| 满足 I3（快路径无阻塞） | 是（快路径无锁无 UART 无 mdelay） | **有条件**：执行者自身若等任何锁/队列即失败；必须让它也不持互斥量 | 部分（超时后仍可能带着未确认状态返回） |
| 满足 I5（新故障不被旧恢复抹掉） | 是（gen 比较） | 需要额外代次，否则仍会 | 否 |
| 新增运行时对象 | 4 个静态字 + 1 个掩码窗口用法；**无线程** | 1 线程（栈/优先级）+ 1 队列 + 所有权转移协议 | 无 |
| 对现有结构改动面 | motor.c/step_pwm.c/safety_state.c 三处的提交点改写 + 新 helper；不改锁序、不改黑匣子 | 需要把 motor/诊断/许可的所有硬件写重排到一个线程（触及 E3/E4/E6/C1–C5 全部），改动面最大 | 最小 |
| 调度影响 | 关中断窗口需实测长度（TBD，B-1）；PRIMASK 全掩码（P-4）会推迟 UART RX/GPIO EXTI | 多一次上下文切换；执行者优先级必须高于 sensor/diag 才有意义，会挤压实时性 | 不改变 |
| 可验证性（主机） | 强：单字状态 + 掩码语义可在夹具里用"两线程 + 确认屏障"复现 | 强，但夹具要模拟线程调度与队列，接近"新造一套框架"（任务书禁止） | 弱：窗口类缺陷难以复现，容易退化成"只测窗口两侧"（正是任务书警告的） |
| 残余风险 | 掩码窗口内不得膨胀（需以用例把守）；A-3/B-2 屏障问题 | 执行者成为单点，且它自己阻塞即整体失效（任务书 §5 明确要说明这点） | 竞态未消除 |

**推荐 A**。理由：它把"停机优先"落到一个可比较的单字事实与两个寄存器写上，不引入新线程/新框架，
且能在既有 `ps_main.c` 夹具上扩展验证（§7）。
对 B 的诚实评估：**如果**将来 UART 复核、Flash 日志与状态整理都要求移出决策线程，B 是自然的第二步；
但 B 只有在"执行者的首次动作不依赖任何锁/队列/控制台"时才满足 I3 —— 而那正是 A 已经提供的性质，
所以 A 是 B 的必要前置，不是 B 的替代品。C 单独采用不可接受（不满足 I1/I4/I5）。

预计改动文件（实现轮，非本轮）：`APP/motor.c`、`APP/step_pwm.c`、`APP/safety_state.c`、
`APP/motor.h`、`APP/step_pwm.h`、`APP/safety_state.h`、（或新增 `APP/stop_priority.h` 纯头文件）；
测试侧扩展 `tests/host/ps_main.c`、`tests/host/shim_ps/`、`tests/host/run_stop_result_s1.sh` 的用例登记。
**不改**：`tmc2209.c`（除 §5.5 需要把结果打上代次标签这一处最小改动，列为后续 WP3 的独立决策点）、
Keil 工程、黑匣子格式、锁顺序（`ss→mot`、`txn→mot` 保持）。

---

## 7. 后续验证设计（**PLANNED / NOT EXECUTED**，本轮一律不填结果）

夹具纪律（先写死，避免实现轮临时编框架）：
- 必须用**真实**互斥/可确认屏障（`shim_ps` 提供 `ps_barrier_hold(lock)`：另一线程确实持锁且不释放，
  由测试观测到"锁被占用"的事实后才判前置成立）；**不得**用空锁桩、不得先释放锁再测。
- 两种宏环境都跑（`target` 四宏 FALSE / `host_ovr` 相关宏 TRUE），因为使能分支只在覆盖变体可达。
- 判定只看：返回值/状态字/寄存器写次数与顺序/事件计数；文本判据仅限"输出本身即被测行为"的既有两类。
- 反向注入必须同时满足：构建成功 + 合同 `rc=1` + 台账自洽 + 指定失败身份全部命中；
  崩溃/异常退出记 `INFRA-FAIL`，不算检出。
- 复用 S1 既有夹具与 runner，不新建元负例平台。

| 计划身份 | 动作顺序 | 必须出现 | 对照/注入 | 状态 |
|---|---|---|---|---|
| S2-P01 | 线程 B 显式持有 `ss_lock` 且不释放；线程 A 触发守卫停机 | A 的 `fast_pwm_freeze`+`permit_low` 写次数各 +1，`sp_fast_seq`+1；B 释放后慢处理才完成 | 注入：把快路径改回经 `force_shutdown` ⇒ 必须检不出来吗？不 —— 必须让 STEP 写次数为 0 且用例 FAIL（证明判据真的看锁） | PLANNED |
| S2-P02 | 线程 B 持有 `mot_lock`（模拟斜坡正在 `rt_pwm_set/enable`）不释放 | 同 P01，且许可 LOW 不依赖 mot_lock（单独尝试一次） | 对照：B 释放后两动作仍只各一次（无重复关断，S1-R1 R04 合同保持） | PLANNED |
| S2-P03 | `tmc_read_reg_locked` 注入超时；同时 `txn` 被长期占用 | 快保护不受影响；`-RT_ETIMEOUT` 走"过期=保护"；晚到成功结果不得撤销抑制 | 注入：让"忙"计数无限顺延 ⇒ 必须被 busy_max 过期判据抓到 | PLANNED |
| S2-P04 | arm 停在 `sp_enable_commit` 之后、GPIO SET 之前；触发停机 | 提交门返回失败/写不生效；`armed` 不提交 | 对照：无停机时 commit 成功 | PLANNED |
| S2-P05 | arm 已写 HIGH、`armed=1` 未提交时停机；随后恢复 A | A 不得提交 armed；回滚路径只做一次降险（合并计数+1） | 观测 `sp_merged`、许可写次数、事件次数 | PLANNED |
| S2-P06 | 诊断 grant 之后停机 | grant token 失配，`pwm_test` 拒绝 enable；所有权位不变 | 注入：去掉 owner 位比较 ⇒ 用例必须抓到 enable 成功（反向） | PLANNED |
| S2-P07 | 诊断 `rt_pwm_set` 后、`enable` 前停机；斜坡旧任务同理 | 冻结发生在 enable 之前 ⇒ 提交被拒；不出现"停机后又开始输出"的交错 | 与 S2-P02 组合跑（斜坡+诊断并发） | PLANNED |
| S2-P08 | 最窄提交窗口：夹具让停机恰好落在比较与写之间（有效确定性接缝，例如把写做成可注入延后） | 单字状态证明**只有一侧**成立：要么 commit 成功且 inhibit==0，要么 inhibit==1 且 STEP 写次数==0 | 台账必须记录 `sp_ctrl` 快照序列，能证伪"两侧都通过" | PLANNED |
| S2-P09 | 新故障与 `fault_reset` 交错、连续多次停机、延迟结果 | `fault_reset` 拒绝并打印 `newer inhibit(gen=..)`；较新的失败锁存不被清；事件计数不增长（不自激） | 对照：无并发时恢复成功进 `MANUAL_CLEAR` | PLANNED |
| S2-P10 | `rt_pwm_disable` 失败/设备 NULL/许可 LOW 失败/无反馈样本/事件投递被吞（`ss_inited=FALSE`） | 两动作各自尝试、S1 记账不变；`fast_seq` 与 `merged` 如实；事件被吞时状态命令仍可观测到"已降险但未整理" | 注入：桩按序列返回失败（沿用 S1-R1 的 `ps_sim_pwm_disable_seq`） | PLANNED |
| S2-P11 | 真正常对照：`motor_stop`/成功 `disarm` 之后再次 arm | 不形成抑制；恢复必须走原 `fault_reset` 复核；四宏 FALSE 时危险动作仍全部被拒 | 断言 inhibit==0、gen 不变、arm 在门全通时成功 | PLANNED |

时间要求分层（写死在方案里，实现轮不得混淆）：
- 主机只证 **动作顺序、有限/无等待路径、竞争失效**；主机墙钟**不能**充当目标机最大时延。
- 四个量分开记录：检测时间（守卫周期）、请求→保护动作、反馈确认、记录完成。前两者是 S2 的关注点；
  后两者是整理。
- 目标机待测点（全部**延期，需另行授权**，本轮不测）：关中断窗口实际长度与 UART RX 丢失率、
  STEP 冻结后 PA0 波形（无示波器 ⇒ `BLOCKED_INSTRUMENT`）、最坏停机时延分布、各线程栈余量。
  ⇒ 结构性消除某个 `FOREVER` **不等于** RISK-03/03a 已关闭。

---

## 8. 设计阻断与最小补证方式（可交 PARTIAL，不编造可实现性）

| ID | 阻断内容 | 为什么现在没有依据 | 最小补证（不需要硬件的先做） |
|---|---|---|---|
| B-1 | 关中断窗口的可接受长度 / 是否必须改用 `rt_spin_lock` 语义 | 只知道 `CPSID I` 是 3 条指令（P-4），不知道 UART RX 溢出/EXTI 丢失的容忍度；`RT_SERIAL_RB_BUFSZ=64`（DV） | 源码补证：读 `drv_uart.c` RX ISR 与 `dev_serial.c` 环形缓冲溢出行为；算出"窗口 ≤ N 条指令"对应丢失字节数。不需要硬件 |
| B-2 | 外设寄存器写是否需要屏障/缓存维护（A-3） | 工程编译 `cpu_cache.c`（AC）但本轮未确认 GPIO/EPWM 区域的缓存属性 | 源码补证：读 `board.c`/`cpuport.c` 的 MPU 与 cache 配置、`SYSCTL` 区域属性；必要时查厂商手册版本并记录链接 |
| B-3 | EPWM"模块级"冻结对同模块其它通道/功能的影响 | P-6 显示 enable/disable 是整个时间基；本轮未确认还有谁在用同一 EPWM 实例 | 源码补证：全仓搜索 `EPWM1`/`epwm_conf` 使用者与 `ns800_epwm_drv_set` 的写入面 |
| B-4 | `rt_pwm_disable` 恒返回 EOK ⇒ S1 的 `api-failed` 锁存在本驱动上不可达（审计 §8） | 没有"驱动能力"层的定义，也没有波形证明 | 需要一次**真机波形**（示波器 + 授权，延期）或至少驱动级"写后回读 TBCTL"的可行源码评估；本轮只登记缺口，不改 S1 合同 |
| B-5 | `pin` 命令/`pin_status`/`system_selftest` 这些旁路写入者是否在协调机制之外仍能翻高 | 已确认 `pin` 存在（dev_pin.c:382）且 `pin_status` 会主动写（审计 C7），但本轮未测量它们能否触及 PC.23 编号 | 源码补证：核对 `rt_pin_get`/引脚编号映射，确认 `pin write` 能否命中 PC.23；**本轮不改通用驱动、不删命令** |

**权限与适用范围边界（任务书 §4 末条）**：协调机制只覆盖"经 APP 生产 API 的写"。通用 `pin` 命令、
`pin_status` 的主动重写、遗留 PF21 诊断（当前未编译）、以及未来任何裸写都属于**机制之外**，
必须靠"物理上关掉/不启用维护通道"或"把这些写入口也接到提交门"来解决 —— 这是运维授权边界，
不能靠"所有写入口收口"这句话蒙混过去（本轮只登记，不改通用驱动、不删命令）。

---

## 9. 后续最小实现工作包（推荐顺序；每包都要独立可审查）

- **WP1 抑制字与提交门**（`safety_state.c` + `motor.c` 的 `C1/C4` 与 `step_pwm.c` 的 `C3` 改为 token 提交；
  无新线程）。验收：S2-P04..P08 在覆盖变体真执行；S1 的 48/64 与两项反向全部保持；Keil 0/0。
- **WP2 短保护阶段与故障整理分离**（`safety_force_shutdown` 拆成 fast/slow；`mot_guard_shutdown`、
  `disarm` 失败、`pwm_test stop` 失败改走 fast；`fault_reset` 按 §5.8 重排锁范围）。
  验收：S2-P01/P02/P09/P10/P11；`ss_lock` 不再出现在降险动作之前；不自激断言保持。
- **WP3 UART 截止期与代次绑定**（守卫结果带 `(gen, config_epoch)`；`-RT_EBUSY` 过期语义）。
  验收：S2-P03 + 既有 TMC/IFCNT 合同用例全部不变弱。
- **WP4 目标机延期项**（波形、时延分布、栈余量、B-1/B-2 的实测）——**需要新的授权与仪表，本轮不排期**。

依赖：WP2 依赖 WP1 的状态字；WP3 依赖 WP1 的代次；WP4 独立但**必须**在 WP2 之后才有意义。
每个 WP 都要单独交独立复审；不合并成大提交。

---

## 10. 停止点

本文件是**设计提案**，不是实现、不是测试通过、不是硬件结论。
状态：`AWAITING_INDEPENDENT_DESIGN_REVIEW`。
不登记任何 S2 实现 GO，不进入实现/返修/板级/F2e/F3；四宏保持 `RT_FALSE`，本轮零硬件动作、零测试执行。
审查通过后，实现需要**新的自包含任务书与用户明确授权**。
