# M7-R1 R1a：静止事务与诊断预约的串行化合同

日期：2026-10-29。对应独立复审 §2 R1a。产品点：
`applications/motor.c` 的 `motor_pwm_grant_to_diag()`（当前 2224-2261 行区段）。

## 1. 缺陷形状（复审抓到的那一条）

`motor_calibration_static_begin()` 取 `mot_lock` 并把静止事务**持到系数提交结束**，
注释据此声称"诊断不能在标定期间取得输出"。但修前的 `motor_pwm_grant_to_diag()` 是：

```c
if (motor_diag_gate_check() != RT_EOK) return -RT_EPERM;   /* gate 内部取放 mot_lock */
if (sp_reserve(SP_DIAG, out) != RT_EOK) return -RT_EPERM;  /* 此刻锁已经还掉了 */
```

`motor_diag_gate_check()` → `motor_get_snapshot()` 自己取放 `mot_lock`
（`motor.c:2079-2085`），返回时锁已经释放；随后的 `sp_reserve()` 不在任何持锁期内。
于是"最终资格判定"与"预约"之间存在真实窗口。云端 `probe_diag_threads.c` 在
gate 返回之后、预约之前注入会合点，另一条线程执行标定 begin，实测：

```text
PROBE pthread cal_begin_rc=0 cal_guard_held=1 grant_rc=0 owner=2 expected_none=0 diag=2 rendezvous=1
```

含义：标定静止事务已经成立（`cal_guard_held=1`）的同一时刻，诊断仍然拿到了
`SP_DIAG` 预约（`grant_rc=0 owner=2`）。这不是"强行执行本应被屏蔽的 ISR"，
没有物理 STEP/DIR，也没有电机动作。

## 2. 修法

把"最终资格判定 + 预约"放进**同一次** `mot_lock` 持有期（`motor.c:2224` 起）：

```c
e = motor_lock_take_checked();          /* 有返回码；失败就不判定也不预约 */
if (e != RT_EOK) { 打印原因; return -RT_EPERM; }
gate = motor_diag_gate_check();          /* 同一线程递归取放，判据不变 */
if (gate != RT_EOK) { motor_lock_give_checked(); return -RT_EPERM; }
if (mot_cal_guard_held) { motor_lock_give_checked(); return -RT_EBUSY; }
e = sp_reserve(SP_DIAG, out);
motor_lock_give_checked();
```

锁序与递归语义核对：

- `mot_lock` 是 RT-Thread 的**按线程递归**互斥量（`tests/host/sp_conc_main.c:126-131`
  登记，主机模型用 `PTHREAD_MUTEX_RECURSIVE`）。因此 gate 内部的
  `motor_get_snapshot()` 再取一次是**同线程重入**，不是自我死锁；取放仍成对。
- 既有锁序 `mot_lock → cfg_lock` 未变；`sp_reserve()` 自己开的短窗口
  (`sp_win()`) 在 `mot_lock` 之内，与 `motor_calibration_static_begin()`
  已在锁内调用 `sp_get()` 的既有接受做法同一条形状。
- PRIMASK 窗口里没有打印、没有等待、没有浮点：拒绝分支的 `rt_kprintf`
  全部在 `sp_end()` 之外（预约本身成功时无输出）。
- 没有强改 owner、没有用恢复票据、没有改 `stop_priority` 核心、没有加通用回调。

第一次尝试用的是"gate 之后再 `RT_WAITING_NO` 试取"，**不可达**：gate 内部已经
FOREVER 取过锁，试取永远走不到；S2 该组因此挂死 400 s（墙钟超时原文保留）。
定稿把有返回码的阻塞取锁提到 gate 之前，并删掉那个死代码 `motor_lock_trytake_checked()`。

## 3. 两种确定次序（S2 第 19 组 `cal_static`，C22a…C22f2）

会合点全部用夹具既有事件，不用 `usleep` 猜：`S2_SEQ_CONTEND` / `s2_contend_hits`
**只在另一条线程确实持有该业务锁时**置位（`sp_conc_main.c:235-252`）。

| ID | 次序 | 判据 |
|---|---|---|
| C22a | 控制：静止、无事务 | 借用路径真的能取到并归还（否则后面的"被拒"没有意义） |
| C22b | 标定先持事务 → 另一线程请求预约 | 事务期间没有新预约（`owner==SP_NONE && !taken`） |
| C22b2 | 事务结束后同一线程重试 | 必须重新可用（区分串行化与永久封锁） |
| C22c | 无关线程持 `mot_lock` → 诊断请求 | 同样不得出现预约 |
| C22c2 | 持锁者释放后 | 同一借用路径重新可用 |
| C22d | 诊断已持预约 → 标定 begin | 拒绝且不残留（M7 原语义未退化） |
| C22e | 被串行化过的失败出口 | 锁没被留在被拒的那一刻（后续取放仍正常） |
| **C22f** | **已取得资格但尚未预约** | 预约**那一刻**本线程 `mot_lock` 持有层数 `>=1` |
| C22f2 | C22f 的负控 | 探针不是恒真：只读快照之后必须报"没有认领" |

target 变体（四宏 `RT_FALSE`，无 STEP 输出许可）整族按声明 `SKIPV("C22", ...)`，
不拿"门永远拒绝"冒充标定串行化已被验证。

## 4. 为什么必须有 C22f：反向第一轮的真实失败（原文保留）

首轮五项反向按 "gate+reserve 挪回锁外" 做突变，`REV3` **没有被检出**：

```text
REV1 s1_rc=1 hits=3/3
REV2 cc_rc=1 hits=1/1
REV3 s2_rc=0 hits=0/2
  REV3 NOT DETECTED
REV4 cc4_rc=1 hits=2/2
REV5 calcalc_rc=1 hits=3/3
M7 REVERSE RESULT: FAIL
```

根因（不是脚本 bug，是**用例判据不足**）：修前的 grant 里诊断线程会卡在
`motor_diag_gate_check() → motor_get_snapshot()` 内部那次 FOREVER 取锁上，
于是"别人持锁时预约出不来"这一观察在修前修后**同样成立**。C22b/C22c 只能证明
排他，不能证明"检查+预约整段被覆盖"。这正是复审 §2 R1a 说的"原任务书 §6.1
要求的并发合同缺口"，也符合本项目既有纪律：单线程"锁永远成功"的桩证明不了它。

C22f 把观测点放到**预约当场**：生产里每次预约都在一个最外层屏蔽窗口内把
`sp.owner` 从 `SP_NONE` 认领成自己（`sp_reserve → sp_win → sp_reserve_impl_held`，
`stop_priority.c:793-800`），所以窗口的进入/退出就是那个确定时刻，
不需要在产品里加任何会合点或回调：

- 夹具的 `rt_mutex_take/release` 按锁名 `"mot"` 维护**线程本地**递归持有层数；
- 最外层窗口进入时记下该层数与 `owner`，退出时若 `owner` 由 `SP_NONE` 变成别的值，
  就把"这次认领发生时的层数"登记下来；
- C22f 判：诊断借用路径的这次认领层数必须 `>=1`。

修后：层数 `1`（外层持锁 + gate 递归一层已还）⇒ PASS。
突变（gate 与 reserve 回到锁外）：层数 `0` ⇒ 具名 FAIL：

```text
CASE|C22f-qualification-and-reservation-are-one-hold|FAIL|grant=0 认领=1 owner=2 预约时 mot_lock 层数=0 - 检查还锁之后才预约就是修前那个窗口(期望层数>=1, 即仍在 mot_lock 持有期内)
```

## 5. 修后的同一对探针输入（真实双线程）

`probe_diag_threads.c` 原样使用，只在仓外副本里注入会合钩子；接缝锚点因本次修改
移动，按复审 §7 显式适配并说明（见 `probes_r1.md`）。修后同一观察：

```text
PROBE pthread cal_begin_rc=99 cal_guard_held=0 grant_rc=0 owner=2 expected_none=0 diag=2 rendezvous=2
```

`rendezvous=2` = 标定线程确实卡在锁外（`m7_port_contend_hook` 由
`s2_contend_hits++` 触发），`cal_guard_held=0` 表示打印那一刻静止事务从未成立；
诊断释放锁之后 `motor_calibration_static_begin()` 因 `owner==SP_DIAG` 被拒
（即 C22d 的形状）。两条路径互斥，不再出现"事务开着 + 预约已得"的并发提交。

对照修前（复审 §1 的云端原文）：`cal_begin_rc=0 cal_guard_held=1 ... rendezvous=1`。

## 6. 保留项

- 普通静止成功（C22a）、已持诊断拒绝（C22d）、Motor arm 竞争与失败不泄锁（C22e）
  全部保留原 ID 与原断言，没有为凑绿放松期望。
- 首轮 REV3 的 NOT-DETECTED 原文与 `hits=0/2` 一并归档，不删。
- 本轮 `HARDWARE_ACTIONS=[]`：没有 STEP/DIR/HIGH 输出，没有下载、没有设备对象。
