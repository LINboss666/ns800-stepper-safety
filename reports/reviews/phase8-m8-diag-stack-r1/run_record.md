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

## 11. 现场脚本集中补正 r2（复审 2026-11-01 任务书；本轮设备动作 = 0）

复审用零设备桩实读 `8bf93fb` 的执行器，复现出 5 项会让现场直接失败的问题。我逐项回读源码确认，全部成立：

| # | 缺陷（在 `8bf93fb` 版里） | 我核对到的代码事实 |
| --- | --- | --- |
| 1 | `-u` 收到的是**文件路径**而不是 UID 内容 | `main()` 里 `uid = a.uid_file`，`expand()` 直接把路径塞进 `@UID_FILE`；pyocd 按 unique-id 匹配探针，路径永远匹配不到 |
| 2 | savemem 目标路径是反斜杠形态 | `expand()` 用 `str(run_root)`（WindowsPath→`D:\…`）。本机 pyocd 0.45.1 实测分词：`split_command_line("savemem 0x08000000 290916 D:\\ns800\\a.bin")` → `['savemem','0x08000000','290916','D:','s800\x07.bin']`；单引号+正斜杠形态被正确剥成单个路径 |
| 3 | 四条查询零字节仍能走到 `PASS` | `_serial_and_capture` 只记 `len(resp)`，`real()` 的判据只有 `boot_complete/hardfault/echo_bytes>0` |
| 4 | 复位 `rc=1` 照样继续发 5 次写入 | 写了 `S3_reset.rc.txt` 但**没有任何地方读它** |
| 5 | 读取异常时端口不关、已收字节不存 | `p` 无 try/finally；`S3_boot_capture.bin` 在 75 s 循环**之后**才 `write_bytes`，中途抛异常就整窗丢失 |

补正（只限现有脚本与它生成的 PLAN，不动 C/H、不动工程、不重编）：

1. `uid_from_file()` 读**内容**并 strip；原值只在内存里，公开件只有 `@UID_FILE<redacted len=… sha256_…>`；
   `argv_gate()` 逐项要求 `-u` 的值 **等于** UID 内容，路径形态具名拒绝 `uid_arg_not_content(path)`。
2. `@ROOT` 一律 `run_root.as_posix()`，savemem 目标写成 `'…/A_locals.bin'`（单引号）；
   `SAVEMEM_RE` 只接受"十六进制地址 + 十进制长度 + 单引号 posix 路径且在 run_root 内"，
   反斜杠/未加引号/越出 run_root/长度不等于接受值 → 各自具名拒绝。
3. `ACCEPTED` 是**源码常量**（BIN 290916 B / `f5c2fd89…1708ce`，AXF 1771288 B / `fb26d5a0…2ed6ea`），
   启动时先与已登记的 `reports/firmware/phase8-m8-diag-stack-r1/SHA256SUMS.txt` 逐字对照
   （`verify_accepted_literals`，常量与清单不符即拒绝——常量不是我编的），再用它复核盘上文件
   （`verify_products`）；两者都排在任何设备动作之前。原 PLAN 里"现算 sha 再当接受值"的写法作废。
4. 串口段 `serial_stage()` 统一 try/finally：`capture_window`/`read_until_prompt` 每收到分片立刻
   `write+flush`（异常时已收到的启动字节仍在盘上），读取异常具名 `SERIAL_READ_EXCEPTION`；
   复位 `rc≠0` → `RESET_FAILED` 且**一条查询都不发**；HardFault → `HARDFAULT_NEEDLE_SEEN`；
   启动未完成 → `BOOT_INCOMPLETE`；无提示符 → `NO_PROMPT`。下载前额外要求 A=40 B / B=48 B 精确长度
   （`PRE_READ_SHORT_FILE`）与 `status` 末次读数为 `Halted`（`CORE_NOT_HALTED`，措辞取自往轮
   `action_*/stdout.bin` 的 `Core 0 (None):  Halted`）。
5. 每条查询：单调时钟 `QUERY_WINDOW_S=12` 截止、核对 `write()` 返回全量（`QUERY_SHORT_WRITE`）、
   要求"命令回显 + 回显之后还有非空白正文 + 以 `msh >` 收尾"三段齐全，缺一段 → `QUERY_TIMEOUT` /
   `QUERY_INCOMPLETE`；只有回显或空垃圾**不算**查询成功。提示符取自源码而非猜测：
   `components/finsh/shell.c:93` `_MSH_PROMPT "msh "` 与 `:122` 追加的 `'>'`。
6. 结果标签只剩 `CAPTURE_COMPLETE_REVIEW_PENDING`（栈/堆余量待离线判读）与 `FAIL_OR_INCOMPLETE`；
   脚本不再自宣 `PASS`。`quota.dap_sessions` 从 3 改 **4**（RAM 读取/下载/回读/复位 = 4 次工具连接），
   并删掉 S1 上"回传 rc 由复审批准下一次"的 `operator_checkpoint`（与整包一次批准冲突）。

复核（`host/field_check.py`，零设备：不起 pyocd、不开真实端口；假时钟只服务自检，
真机 `BOOT_WINDOW_S` 仍是 75 s 未缩短）：**CHECKS=32 problems=0，元负控 4/4 fired**。
A 组 12 项 = 固定接受值与 argv 形态（含"篡改盘上件必须被拒""完好副本必须过"）；
B 组 16 项 = 上述每个停点都按同输入触发、健康正控走到 PENDING 且四查询都有正文、异常时端口已关且
部分启动字节完整留存在盘上；C 组 4 项 = 分别把 `argv_gate`/`verify_products`/查询截止/会话数判据
换成"永远放行"，对应 A3、A8、B14 的期望**必须翻成失败**（不翻说明判据恒真）。

自伤登记：往轮登记的 `host/staged_preflight_diagstack.txt` 末尾其实是 **traceback**——该脚本假设
`build_manifest.json` 的 `images/maps` 是列表，而 R3 那份是 **名字→字典**，于是跑到 R3 build_manifest
就 `TypeError: string indices must be integers`，退出码 1、`STAGED_PREFLIGHT_TOTAL` 那行从未打印。
我在上一轮回执里把它读成"problems=0"，这是错的：能证明的只是 4 份清单与本轮 build_manifest 逐 blob 相符。
本轮已修两种形态并加 `--round-only`（按复审口径只重核本轮两份清单，不重走 R3 那 5137 条）。

`field/selftest/**` 保持**修前**那次的原样（含被复审判为不足的 5 项判据），作为这份缺陷清单的现场留证；
补正后的证明在 `host/field_check_console.txt` / `host/field_check.json`。

## 12. 半程执行 `field-20261101T084256Z`：S-1/S1/S2 已证，S3/S4 未跑（第 6、7 项自伤）

操作者一次批准（原文存 `APPROVAL_TOKEN.verbatim.txt`）。执行前核对：tip `4272f92`、工作树干净、
待执行脚本的 Git blob sha256 = `35efccb5…41c815`（与批准同一枚）、UID 文件在场 48 B、COM5 空闲。

实际发生的设备动作（原始件全在该 run 根，未删未改）：

| 段 | 工具 | rc | 证据 | 结果 |
| --- | --- | --- | --- | --- |
| S-1 | read_ram | 0 | `action_read_ram/stdout.bin` 两行 `Core 0 (None):  Halted`；`A_locals.bin` 40 B、`B_tcb.bin` 48 B | 长度精确、核心确为 Halted，门禁放行 |
| S1 | download | 0 | `action_download/stdout.bin` 176 B（进度条两格） | 唯一一次 `-e sector` 下载完成 |
| S2 | readback | 0 | `flash_readback.bin` = 290916 B，sha256 `f5c2fd89bbf28255a5b26c9cca495bc0fb1e39d401d0bb7bea19d051221708ce` | **与固定接受值逐字节相同 ⇒ 4096 新镜像已在板** |
| S3/S4 | reset + 串口 | — | 无 | **没有执行**：脚本在 `serial_stage` 之前抛 `TypeError` |

停点原因（我自己的第 6 项缺陷，零设备桩当时没抓到）：`real()` 把 `open_port or make_real_port()`
——**调用后的 Serial 实例**——当工厂传下去，`serial_stage()` 里再 `open_port()` 就是对实例求调用，
`TypeError: 'Serial' object is not callable`（完整 traceback 在本轮提交说明与回执里）。它发生在
`make_real_port()` 已经打开 COM5 之后、`reset` argv 已经拼好但**一条都没发**之前，所以：
复位 0 次、启动采集 0 B、四条查询 0 条、`RESULT.json` 不存在。COM5 的泄漏句柄随进程退出释放（实测重开成功）。

配额实况（与绑定包对照）：`dap_sessions` 用 3/4（read_ram、download、readback）；`download` **1/1 用尽**；
`flash_readback` **1/1 用尽**；`port_open` **1/1 用尽**；`explicit_reset` 0/1 未用；`retry 0` ⇒ 我没有自行重试，
停在原地等下一次批准。零电机、零配置写、零擦除，全程没有发过任何 `writemem/loadmem/erase`。

第 7 项缺陷由本轮新增的 **D 组端到端干跑**抓出：`serial_stage()` 在成功路径上先写 `RESULT.json`，
`port_closed` 要等 `finally` 才赋值 ⇒ 归档里的 `port_closed` 永远是空。现已把 `RESULT.json` 在 `finally`
里**末次重写**，句柄是否真关掉才进得了证据。修好之后：`CHECKS=39 problems=0，元负控 5/5 fired`
（新增 C5 = 把实例当工厂传进去必须炸 `not callable`，证明 D1 不是空跑）。B 组当时全绿而现场仍崩，
教训是：**接线层没走一遍，就等于没测**——只测被调函数不够，`real()` 的装配也要端到端干跑一次。

## 13. 下载前两条读取的判读（`pre_download_reads_decode.txt`，归属仍未定）

三个哨兵**全部命中**，而且把受害对象钉死了：

- A（0x200127D8，`rt_object_init` 局部槽）：`+0x08 node = 0x00bd80b0`（与既往 `r0`/`BFAR` 同值）、
  `+0x1c object = 0x20015b78`、`+0x20 = 0xdeadbeef`（栈填充标记原样未被写）、`+0x24 = 0x080236db`。
  ⇒ 我此前"局部槽已被异常帧覆盖"的判断在此**直接被否掉**，出错瞬间的 `node` 值原封保留。
- A 的 `+0x14 name = 0x20015b28`、`+0x1b type = 0x0a`。
- B（0x20015B28）：`name = "bblog"`、`+0x10` 低字节 `type = 0x01 = RT_Object_Class_Thread`
  （`rtdef.h:332`）、`+0x2c stack_size = 0x400 = 1024`（偏移按 `rtdef.h:851-862` 的字段顺序）。
  ⇒ 0x20015b28 是 **bblog 线程的 TCB**。

合起来：`object = TCB + 0x50 = thread_timer`（`offsetof(rt_thread, thread_timer)=0x50` 由复审从新 AXF 的
DWARF 核得），`type=0x0a` 正是 `RT_Object_Class_Timer`（`rtdef.h:341`），入口是 `thread.c:245
rt_timer_init(&(thread->thread_timer), …)` → `timer.c:280 rt_object_init(&(timer->parent), RT_Object_Class_Timer, name)`。
⇒ 出错那一行 `object.c:381 node = node->next` 当时遍历的是 **Timer 类的对象链表**，被初始化的对象是
**bblog 的内置线程定时器**；`node - 0x14 = A+0x00` 又正好等于 `rt_list_entry` 求 object 的算法
（`offsetof(rt_object, list)=0x14`）。复审上一轮"创建 bblog 内置定时器时撞上已损坏的定时器对象链表"这条
推断，现在有了字节级支撑，**但仍未定**：谁把 `0x00bd80b0` 写进链表、diag 栈溢出是否就是那次写、
4096 是否足够、板上重启后会不会再 HardFault。新镜像已在 Flash，核心仍 Halted，从未复位。

下一小步只要一次追加批准：续测包只做 **S3+S4**（`port_open 1`、`explicit_reset 1`、75 s 启动采集 +
提示符 + `system_status/ps/free/diag_status`），**零下载、零回读、零电机、零写 Flash**，因为这两项配额
已经用尽而镜像身份已证。

## 14. S3/S4 续测入口（`--mode cont`，本轮零设备动作）

复审指出一个入口问题：`--mode real` 会重走 RAM 读取/下载/回读，不能拿来续测。故新增 `cont()`：
**复用同一个 `serial_stage()`，可达工具只有 `reset`**（`cont_only_reset()` 对其它任何工具名抛断言），
不调用 `real()`、不重新下载/回读、不重编。镜像前提改成可核对的闸：`prior_image_proven()` 复核既往那一次
唯一回读的原字节仍在且等于固定接受散列，不满足即 `PRIOR_READBACK_NOT_PROVEN` 停。台账写进
`CONT["port_open_ledger_before"]`：**复审提醒，我之前那次"COM5 空闲检查"本身就是一次真实开口**，
所以历史上已开 2 次（探测 1 + 崩溃那次 1），续测这次是第 3 次，且不再做任何额外探测。

零设备复核（`host/field_check.py`）：**CHECKS=48 problems=0，元负控 6/6 fired**。新增两组：
- E 组 8 项 = 续测入口端到端（`tools_called == ["reset"]`、只生成 `ARGV.masked.reset.txt`、
  不产生 `flash_readback.bin`/`A_locals.bin`、被篡改的既往回读必须拒、非 reset 工具名逐个拒、
  配额里 download/readback/read_ram/flash_write/ram_write/rebuild 全 0 而 port_open=reset=1、
  窗口仍 75 s、台账两条 + "next_open_is = 第 3 次"）。
- C6 元负控 = 把 `prior_image_proven` 换成"永远为真"，E5 的拒绝必须失效 ⇒ 证明它靠的是真复算。

E 组又抓到第 8 项同类问题：`serial_stage()` 的 `finally` 早于 `cont()` 赋 `tools_called` 就写档案，
所以 `RESULT.json` 里 `tools_called` 是空 ⇒ 续测末次再写一次 `RESULT.json`，`tools_called` 与开口台账进档。

本轮**没有任何设备动作**：板子仍是 §12 的状态（4096 新镜像已在 Flash 且逐字节证得，核心 Halted，从未复位）。
等"跑续测"这句话，才执行 `--mode cont`。

## 15. 续测 `cont-20261101T090512Z`：启动到 READY、四查询拿满、HardFault 未复现（因果仍未证）

执行前核对：tip `00f5508`、工作树干净、待执行脚本 blob sha256 = `a30537da…c5151`（与批准同一枚）、UID 文件在场。
**没有做任何开口探测**：第 3 次打开 COM5 就是正式会话本身。设备动作只有一次 `reset`
（`RESULT.json` 里 `tools_called == ["reset"]`、`S3_reset.rc.txt = 0`、`port_closed = true`）。

测到（逐条回指原文见 `field/cont-20261101T090512Z/OFFLINE_JUDGEMENT.txt`）：

- 3439 B 启动流里出现绑定针脚 `[BOOT] Phase-7 runtime bootstrap complete (state=READY)`，
  两个 HardFault 针脚 **0 命中**；`[BOOT] 8 diagnosis OK` 与 `[BOOT] 9 blackbox OK` 都过去了 ——
  既往坏现场正是 bblog 的 `thread_timer` 初始化卡在 `object.c:381`。
- `ps` 实测：`diag … 0x00001000 28%` ⇒ 栈 4096 已在运行时生效，高水位 ≈ **1146 B > 旧值 1024**。
  这把 §2 的 armlink 静态下界升级成**板上实测**：旧栈确实装不下。对照 `bblog 0x400 60%` ≈ 614 B。
- `free`：total 32672 / used 20072 / available **12600**（≈39%）⇒ diag 多要的 3072 B 已支付且全部线程创建成功。
- 零电机是读出来的不是承诺：`Motor state=0 armed=0`、`arm gate fail mask=0x18 (arm REFUSED)`、
  `diag_status` 计数全 0、`rel latch: none`、`verdict=NORMAL mode=MONITOR_ONLY`。
- 交互合同第一次成立：提示符探针 7 B，四条查询正文分别 830 / 793 / 49 / 978 B，每条都"回显+正文+`msh >` 收尾"
  —— 本项目第一次拿满四条查询（往轮三轮静默）。

不许从这些推的：**因果仍未证**。这次通过既可以是 1024→4096 消除了溢出，也可以只是堆布局变了
（bblog 的 TCB 从坏现场的 `0x20015b28` 变成 `0x20016728`，`diag` 是 `0x20015660`）使溢出不再命中 Timer 链表；
单次启动分不出两种解释，不择一采信。且 **N=1**（一次复位、一个 75 s 窗，无重复启动无长跑）、
4096 仍不是已验证上界（1146 B 只是本次 MONITOR_ONLY、无告警打印路径的高水位）、坏指针写入者仍未定。
保护验收也没开始：`ADC DEGRADED cal=THEORETICAL`、`protect_ready=NO`、`threshold_validation=NOT_ESTABLISHED`、
`rel cov=NOT-ACTIVE(no-profile) lost=64660`、`limit_inputs=DISABLED_BY_CONFIG`。
只登记不解释：`ps` 的 error 列除 `tshell`/`tidle0` 外都是 `EINTRPT`。

脚本终态按约定仍是 `CAPTURE_COMPLETE_REVIEW_PENDING`（不是 PASS）：栈/堆余量的判读由本轮离线完成，
不并入自动判定。
