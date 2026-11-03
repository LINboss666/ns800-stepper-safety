# M8-R7F1-A 最小补丁跟修 · 审核交接（离线，设备动作 0）

日期：2026-11-03。基线 `adeeedc`（上一轮 R7F1 交付头）。
输入：A 的 `NS800_R7F1_A_Review_and_Minimal_C_Patch_20261103.zip`（zip 原字节
`4d5f4664c51468ea957f90f10e547653fe32bd987b9ce2ed3a082b798f9b6b1b`，已连同补丁原件与 A 的
修前/修后逐项输出入库到 `host/reviewer/`）。

**本件不含硬件批准，不代替操作者回答现场安全门；本轮设备动作 0。**

## 1. 改了什么（三处，都在产品/配置侧，逻辑判据未动）

- **`rtconfig.h:118` main 线程栈 2048 → 4096**（复审 §1）。上一轮那份栈报告拿 `supervisor_boot`
  子树 2020 B 说"余量 28 B"，根选错了：真实入口链是 `main_thread_entry → $Super$$main →
  supervisor_boot`（`src/components.c:200-204` 在 `__ARMCC_VERSION` 下调 `$Super$$main()`），
  按 `main_thread_entry` 为根实测 **2076 B**，对 2048 是**超出 28 B**。
- **`.config:194` 同步为 4096**（操作者本轮指令，不在 A 补丁内）。理由：`.config` 是 menuconfig 真源，
  留着 2048 下一次生成会把 `rtconfig.h` 改回去。单行替换，CRLF 原样，前后同为 52103 B；
  同步后逐条比对 `rtconfig.h` ↔ `.config` 的 21 个带值项，**值不一致 0 个**。
- **`tmc2209.c` 四个提前返回补记 `st->used`**（复审 §2）：`BAD_CONTEXT`(1123)、静止门 `NO_DEVICE`(1147)、
  懒打开失败 `NO_DEVICE`(1156)、取锁失败(1182) 各写 `rt_tick_get() - b->start`；预算尚未建立就失败的
  那条(1104) 不写，不读未初始化起点。不增加等待、不改超时、不改返回码、不吞错误、不引入共享状态。
- 测试侧：`BTB06` 从"打印 used"改成"断言 used == 本次夹具推进 tick 且 == 20"；新增
  `BTB11`/`BTB12` 覆盖两条 `NO_DEVICE` 提前返回；台账 `tmc_boot` 12 条，总数 target 68→70 / host_ovr 143→145。

## 2. 验证到哪里（离线全绿；现场仍为 NOT_EXECUTED）

| 判据 | 结果 |
|---|---|
| 修前/修后同输入对照（SRC_APP 快照） | 修前 `tmc_boot` 两变体各 12 条中 **只 BTB06 FAIL**（`used=0` 而推进=20），修后 PASS ⇒ used 断言不是空转 |
| S2 全量 22 组 × 2 变体 | `PASS (rc=0)`，target=70 / host_ovr=145，无 SKIP；`tmc_boot` 12/12、`read_obs` 30/30、`boot_print` 5/5 |
| V1/V2（真 `tmc2209.c`+`motor.c`） | `PASS (rc=0)` |
| line_width_check | `PASS (bad=0)`（本轮未增改任何 rt_kprintf 站点） |
| 构建 | `0 Error(s), 0 Warning(s)`；`Code=205922 RO=86186 RW=2192 ZI=85596`；对 adeeedc `Code +56`，其余 0；`BIN 292628 → 292684`(+56) |
| 栈（换根后） | `main_thread_entry` 2076 B，**修前修后同值（差 0）**；预算 4096 ⇒ 静态余量 2020 B |

**资源口径**：main 栈 2048→4096 没动静态 RAM（`RT_USING_HEAP` 下主线程栈由 `rt_thread_create` 从
堆取，`components.c:172-177` 的静态数组本目标不编），代价是**运行时堆 +2048 B**；堆区间固定
`board.h:30-31` = 32768 B。堆峰值离线测不到。

**边界（不得当成已证）**：
1. Max Depth 是 armlink 静态下界（不解析函数指针、不含异常/ISR 帧与上下文保存）；"余量 2020 B"≠"不会溢出"。
2. 新镜像未在任何硬件上验证过；板上仍是 R6A 的 `5ae4110d…`。
3. 夹具证的是"后台若先持传输锁，前台必需读必得 -23 且线上 0 字节"，不是"板上确实这样发生过"。
4. 运行中 msh `system_selftest` 重入不受门约束（未覆盖）。
5. 阈值、ACTIVE、运动授权、带载保护、IMU/SG 相对判定的现场有效性：全部未验证，本轮一律未触碰。

**上一轮两条说法作废**（旧入库文件按原字节保留，改它会让其 SHA256SUMS/manifest 自证失效）：
① "main 栈余量 28 B / 瓶颈在 supervisor_boot 2020 B" —— 根选错，以 `main_thread_entry` 2076 B 为准；
② "sensor 在 stage 7–11 已半帧进入 SG，门挡不住在途读" —— 不适用冷启动，门在静态初始化即关闭，
先于线程创建；③ "`[MOT]   注:` 126 B 丢换行" —— 后半句错，127 B 格式化容量容得下它加终止 NUL，
发送长度就是 126，不产生裸 NUL 也不丢换行；它超 120 B 目标但不是截断修复对象。

## 3. 下一次现场要看什么（范围由 A 按这枚身份另发，本轮不含授权）

绑定对象只能是本轮新镜像：BIN 292684 B `b4243886818b6b2cf2e30d10864272225e1b0e66c8435c8a50bb449053e153bf`、
AXF `ea4f8dd1…`、MAP `757701a5…`、调用图 `f97857ab…`。

1. **启动自检落在哪个分支**：读新的两行 `[SS] [REQ FAIL] TMC2209 IOIN rc=… reason=…` 与
   `tx=… rx=… flush=… used=…`。判读：`lock-busy`+`tx=0` = 还没发出就被锁挡（顺序类）；
   `bad-context` = 上下文错（stage 11 不该出现，出现即新问题）；`tx=4`+`rx-timeout-*` = 发出去了没应答
   （通信/器件类，与上一场 -23 不同因）；`reason=ok` 但仍 `VERSION mismatch` = 身份不符。
   `used` 现在是这次调用自己的 tick 数：`lock-busy` 那一路应接近取锁上限 20，`bad-context`/静止门/
   懒打开失败这三条不等锁，应为 0。
2. **是否进 READY**：`safety_status` 期望 READY/fault=0；`[BOOT] 11` 之后门应已释放。
3. **`[BOOT]` 横幅是否逐行照常出来**：这是"堆里那 +2048 B 主线程栈分配成功了吗"的唯一现场判据
   （`rt_thread_create` 返回空会在 `components.c:221` 的 `RT_ASSERT` 早于横幅炸掉）。
4. **三处打印族是否完整**：`[MOT] init OK` 两行、`[SS] STEP:`（或 `!! STEP`）三/四条、
   `[SS] ==> FAULT_LATCHED` 四条，逐条换行收尾、无 NUL、无半截 UTF-8。
   按可达分支说：若正常进入 READY，本来就**不会**打印 FAULT_LATCHED/故障收口那几组短行，
   没看到不等于该分支已被现场验证，也**不为了看它去触发故障**。

本阶段无需 arm/start/学习/保存/清日志，不需要先断电或 fault_reset。上一场批准的
download/readback/reset 额度已用尽，剩余 port_open/apply 不构成新一轮完整验证额度。

## 4. 可复算来源

- 复审输入与 A 的独立输出：`reports/reviews/phase8-m8-r7f1a-minimal-c-patch/a_review_*.md`、`host/reviewer/`
- 构建：`…/build/rt-thread-motor-r7f1a.log`（CRLF 原件）；栈：`…/build/stack_callgraph_r7f1a.txt`
- 镜像：`reports/firmware/phase8-m8-r7f1a-minimal-c-patch/`（BIN/AXF/MAP/调用图/build_manifest.json）
- 主机链：`…/host/r7f1a_*.txt` 与 `…/host/tmc_boot_{target,host_ovr}_out.txt`
- 执行记录：`…/run_record.md`
