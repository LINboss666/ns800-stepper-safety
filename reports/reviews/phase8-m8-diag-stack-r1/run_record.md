# M8 diag 线程栈修复轮(R1, 2026-11-01)—— 运行记录

任务书(复审, 逐字要点): "先保持当前板子暂停，完成离线最小修复：将 diagnosis.c 中 diag 栈从1024改为4096字节候选值，
核算堆余量。只构建用于此次对照的 motor 目标，交付新镜像散列、实际编译参数和上述调用链依据；4096暂不称为已验证上界。
再绑定一次完整板级对照包：必要现场保存、下载、回读、完整启动采集、串口查询及栈观察，零电机动作，由你一次批准。
这轮可以开始修真实代码了，无需先为'再取一次串口 dump'单开一轮。"

## 1. 本轮设备动作 = 0

板子保持 (c)/frame 轮结束时的 **Halted**，本轮没有碰 DAP、没有串口、没有下载、没有复位、没有起电机。
检查器 `host/diag_stack_check.py` 的 `G_no_device_action_roots` 断言本轮目录下没有 `offline/`、`field/` 执行根。

## 2. 唯一一处产品改动(确定缺陷, 不是猜测)

| 位置 | 改动 |
| --- | --- |
| [diagnosis.c:48](file:///D:/ns800/rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/diagnosis.c#L48) | `+#define DIAG_THREAD_STACK 4096` |
| diagnosis.c:528 | `rt_thread_create("diag", …, 1024, 9, 10)` → `…, DIAG_THREAD_STACK, 9, 10)` |

`git diff --numstat` = 3 insertions / 1 deletion，仅此一个文件；旧 blob(`e489438`) 里确实是 `1024, 9, 10`
且没有这个宏 —— 由检查器 `C_base_blob_had_1024` / `C_base_blob_had_no_macro` 用 `git cat-file` 复算绑定。
命名沿用同目录既有约定(`motor.c:90 MOTOR_THREAD_STACK`、`sensor_service.c:35 SENSOR_THREAD_STACK`)。

依据链(三条独立来源，全部实读)：
1. **调用边**：`rt-thread-motor-m1.map` 交叉引用表里 `diag_thread_entry → rel_prod_evaluate → rel_engine_step`
   两条 refers-to 逐条在，所以三帧同时在栈上；
2. **每帧固定占用**：已接受的 R3 AXF 全文反汇编里三处 `PUSH {r7,lr}` + `SUB sp,#0x270/#0xf0/#0xb0`
   = 632 / 248 / 184 B，与 armlink 调用图 `Stack size 632/248/184 bytes` 完全一致 ⇒ 三帧下界 **1064 B > 1024 B**
   (复审给的下界口径，本机逐地址复算一致)；
3. **armlink 累计**：`diag_thread_entry [Stack]Max Depth = 1580 B`
   (链：`diag_thread_entry ⇒ rel_prod_evaluate ⇒ rel_prod_publish ⇒ rel_ctl_take ⇒ rt_mutex_take ⇒ …`)。
   ⇒ 旧配置连静态已知的该线程最深链都装不下，不是"可能不够"而是"必然不够"。
4. **MONITOR_ONLY 也走这条路**：[diagnosis.c:446](file:///D:/ns800/rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/diagnosis.c#L446)
   的 `(void)rel_prod_evaluate(&a, (rt_uint8_t)d_mode, …)` 是无条件调用，模式只是**实参**；
   `:445` 注释说明分模式的只有"真实 post"。

## 3. 构建：只动了 motor 目标一次

`C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 -o <本轮 build/…log>`
退出码 0，`0 Error(s), 0 Warning(s)`，`Build Time Elapsed: 00:00:12`，
`Program Size: Code=204762 RO-data=85578 RW-data=2188 ZI-data=84824`（与 R3 同目标逐字相同）。
默认目标与 A0 目标**本轮未构建**；R3 的六镜像/三映射目录一件未覆盖(检查器 `F_r3_axf_untouched`、
`F_r3_bin_still_onboard_identity` 复算 sha 前缀 `0bba8ca7` / `00608ec9`)。

## 4. 新镜像散列(归档 `reports/firmware/phase8-m8-diag-stack-r1/`)

| 件 | bytes | sha256 |
| --- | --- | --- |
| `rt-thread-motor-m1.axf` | 1771288 | `fb26d5a0e866b2c8852c989c98f158d99d0511ffdc6ce4e9b8c8add3372ed6ea` |
| `rt-thread-motor-m1-candidate.bin` | 290916 | `f5c2fd89bbf28255a5b26c9cca495bc0fb1e39d401d0bb7bea19d051221708ce` |
| `rt-thread-motor-m1.map` | 2108456 | `fd334a83da24162ef8512b1537ec5b66cc84deee3e9a281ba70b4af4fb8ad579` |

实际编译参数逐字在 `stack_report.txt` 第 3 节(该 Target 的 `<Define>` 原文含
`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=1, TMC_DRIVE_CONFIG_HW_VALIDATED=1,
STEP_DIAG_ISOLATED_ENV_CONFIRMED=0, SAFETY_LIMIT_INPUTS_ENABLED=0`；uAC6=1，
Device=NS800RT7P65D-DHQFBY2，Optim=1，project.uvprojx bytes=348371 sha256=`17a34efa…41f4`)。

## 5. 生效证明(不信 Program Size)

栈是**运行时堆参数**，不占静态段，所以四项增量为 0 是预期的。真正的证明是全文反汇编逐行对照
(`disasm_diff.txt` 原样入库)：差异只 4 类，且检查器把**每一条差异行强行归类**，未归类数必须为 0：
(a) fromelf 的输入路径头；(b) **`0x0800ca26: MOV r3,#0x400` → `MOV r3,#0x1000`**(位于 `diagnosis_init`，
就是 `rt_thread_create` 的第 4 个整型实参 r3 = 栈大小)；(c) 两条 `__DATE__/__TIME__` 取址行互换；
(d) 构建时间串本身。⇒ 除栈立即数与时间串外，整镜像没有任何其它代码/数据变化。

## 6. 堆余量核算(枚举，不是"够用"的结论)

堆总容量 32768 B(`link.sct: RW_RT_HEAD 0x20012000 EMPTY 0x8000` + `board.h` 的 `HEAP_BEGIN/HEAP_END`)。
六枚线程栈：diag 1024→4096、sensor 4096、motor 2048、bblog 1024、ui 512、tshell 4096
⇒ 合计 12800 → **15872 B(+3072)**，占堆 39.1% → 48.4%。
**未计入**：每个 `rt_thread_create` 还另配一个 TCB(`rt_object_allocate`)，以及组件层其它 `rt_malloc`
—— 所以本节是下界式枚举。堆真不够时的行为是显式的：`diagnosis_init` 拿不到线程即返回 `-RT_ERROR`
并置 `SUBSYS_FAILED`(fail-closed)，不会静默降级。实际余量要在板级对照包里测(见第 8 节)。

## 7. 本轮**没有**证明的四件事

1. **4096 不是已验证上界** —— armlink 镜像级自己写着 `Maximum Stack Usage = 2996 bytes + Unknown
   (Cycles, Untraceable Function Pointers)`，静态调用图不给闭合上界；异常进入线程栈的硬件压栈不计入。
2. **"diag 栈溢出 = 本次链表坏指针的来源"未证**。已证的只有"diag 的静态已知最深链 1580 B 超过它原来的
   1024 B 栈"这条确定缺陷；它与板子上那次 HardFault 之间的因果需要板上对照。
3. 复审第二条线索(r1=0x20015b78 与 r3=0x20015b28 差 0x50 ⇒ 线程内置定时器；1024/18/10 与 bblog 创建参数
   相符 ⇒ 坏的是**定时器对象链表**)本轮登记为**有依据的推断**：`offsetof(rt_thread, thread_timer)==0x50`
   尚未独立核得，且 `r4..r6` 与 §14.2 同类，本身是残留寄存器。
4. **新镜像未在板**：板上仍是 R3 的 `00608ec9…e304`，核心仍是 Halted。

## 8. 下一步(只准备，不执行；需要复审一次批准)

板级对照包顺序：必要现场保存 → 下载(唯一一次) → 定长回读 → 完整启动采集(窗口必须覆盖到 HardFault 一次性
dump 之后) → 串口查询 → 栈观察；零电机动作。
两条便宜且**必须在下载之前**做的只读 RAM 判定(旧帧还在，未复位就还在)：
- **A** 读 `0x200127D8` 起 40 B = `rt_object_init` 出错前的局部槽 ⇒ 直接给出 `type/information/name`，
  即"哪一个对象类的表坏了"。(复审把出错前 SP 修正为 0x200127D8；我上一轮"局部槽已被异常帧覆盖"的说法
  已在 R3 `run_record §15.1` 撤回。)
- **B** 读 `0x20015b28` 起 48 B = 推断的 TCB 起点 ⇒ `name` 字段(偏移 4)会直接印出线程名，一次判定
  "是不是 bblog""r1 是不是它的 thread_timer"，顺带核 `offsetof(thread_timer)==0x50`。

## 9. 复算

```
python reports/reviews/phase8-m8-diag-stack-r1/host/diag_stack_check.py
  -> CHECKS=37  DIAGSTACK_PROBLEMS=0  NEGATIVE_CONTROL fired=3/3
```
真实输出原样在 `host/diag_stack_console.txt`(41 行，37 条 OK / 0 条 PROBLEM，LF-only)。
## 10. 板级对照包已绑定（DS-2）—— 等复审一次批准，本轮零设备动作

单一事实来源 = `field/run_diag_stack_compare.py` 里的 `PLAN`；`--emit-plan` 生成 `field/plan.json`（9625 B），
`--mode sim` 只做 argv 展开与静态门禁（`SIM_TOTAL problems=0`），`--mode selftest` 用**假端口注入**跑判定逻辑，
`--mode real` 需要含"批准"二字的原文令牌才会走，且当前无人跑过。

三处更正已并入包（不再单开登记轮）：
1. **M7 扩展帧 = 26 字 = 104 B**（可能另有 4 B 对齐填充）；S16-S31 属软件保存部分，本次 HardFault 入口
   汇编没有保存它们；栈帧 `psr=0x81000000` 的 bit9=0 ⇒ 无填充 ⇒ **出错前 SP = 0x20012770+104 = 0x200127D8**。
   我在 §15.1 里写的"104 或 168 都可能"作废，A 项按固定地址读。
2. `offsetof(rt_thread, thread_timer)=0x50` 复审由新 AXF 的 DWARF 核得；本机第二路独立证据 = 新镜像反汇编里
   `_rt_event_recv`/`_rt_mq_recv` 等多处 `LDR r0,[sp,#..] / ADDS r0,r0,#0x50` 紧跟 `BL rt_timer_control|rt_timer_start`。
3. `offsetof(rt_object, name)=0`（`include/rtdef.h:284`，RT_NAME_MAX=16 分支下 name 在最前，type@16/flag@17/list@0x14，
   与 `SUBS r0,r0,#0x14` 一致）⇒ B 项从 `0x20015B28` 直接解释线程名。

下载前两条只读 RAM：A `savemem 0x200127D8 40`、B `savemem 0x20015B28 48`（都在 RW_RT_HEAD 预留块内，门禁按地址区间核）。
A 带两个哨兵自定位：`+8 == 0x00bd80b0`（node）与 `+0x1c == 0x20015b78`（object）；命中即证明局部槽未被覆盖。

动作序列（一次批准，零电机）：S-1 两条只读 RAM → S0 离线门禁 → S1 唯一一次下载（新 AXF，`-e sector`）→
S2 唯一一次 290916 B 回读并与登记 BIN 逐字节比较 → S3 **串口先 Open 后复位**再采集 75 s（原始字节先落盘）→
S4 先探回显，再 `system_status` / `ps` / `free` / `diag_status`，每条原始响应单独存文件。
quota：`motor_run 0`、`config_save 0`、`blackbox_clear 0`、`download 1`、`flash_readback 1`、`explicit_reset 1`、
`port_open 1`、`retry 0`；静态门禁扫的是**会被执行的 argv 与 stage 绑定**（上一版扫整个 PLAN，被自己的
forbidden 清单打出 9 条假报警，已改）。

判据（不接受自动判的部分明写）：启动完成 = 采集里出现 `[BOOT] Phase-7 runtime bootstrap complete (state=`
（`supervisor.c:499`）；可交互 = 回显存在且四查询响应非空；无再硬fault = 两个 fail needle 都不出现；
diag 栈余量与堆余量 = `ps`/`free` 两个文件**由人读**，不自动判 PASS。
停点标签含 `BOOT_INCOMPLETE` / `NO_ECHO` / `HARDFAULT_NEEDLE_SEEN` / `READBACK_MISMATCH` / `PLAN_GATE_REFUSED`。

零设备自证：`--mode selftest` 5/5 OK（健康流通过、HardFault needle 触发、无回显时不发查询、静默流不算通过），
且注入模式下 `S3_reset.rc.txt` 内容为 `SKIPPED_INJECTION(零设备: 不碰 DAP)` —— 复位那条 argv 在自检里**没有执行**。

本轮仍然没有设备动作：板子保持 (c)/frame 轮结束时的 Halted，板上镜像仍是 `00608ec9…e304`。
