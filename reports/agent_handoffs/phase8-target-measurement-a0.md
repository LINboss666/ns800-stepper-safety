# phase8-target-measurement-a0 / -r1 —— 目标机测量 A0 离线固件轮与其返修轮（实现 + 主机验证 + 两镜像构建，零板级动作）

日期：2026-10-25（Asia/Shanghai）。任务书：
`NS800_Prep_R1_Review_GO_and_A0_Firmware_Task_bbf66ba_20261025.md`。
性质：**软件实现 / 主机验证 / 离线 Keil 构建 / 工件归档 / 普通提交推送**。三个 checkpoint 在同一轮自主推进。

> **当前状态 = r1**（任务 `phase8-target-measurement-a0-r1`，任务书 `NS800_A0_Independent_Review_and_R1_Task_c5028f4_20261025.md`）：本文 1..7 节是上一轮（`c5028f4`）的原样记录，不改写；r1 的结论、身份与逐项映射只出现在第 8 节和 JSON 的 `a0_r1_review_resolution` / `a0_round_c5028f4` 两块里。

## 1. 登记上游复审结论（任务书 §0，不另开纯登记轮）

**`PREP_REVIEW_GO`：独立复审接受 `bbf66ba34d7b31a217c643921aa4192df2f59a56` 的离线测量准备计划，
R1/R2a/R2b/R3a/R3b/F1 全部关闭，无新增必须返修项。** 复审端只做了 Git/源码/文档静态核对
（远端 `ls-remote`、`b27c0c4→bbf66ba` 恰 3 条白名单路径 +494/−72、与 `bcdec631` 相比代码/脚本/工程
差异为空、三 blob 逐字节复算一致、JSON 与 `review_resolution` 六项映射、`git diff --check` rc=0、
四宏与冻结引用回读），**未构建、未执行测试链、未操作硬件**。

S2 `SOFTWARE_REVIEW_GO` 仍只接受 `bcdec631…` 的既有软件/主机合同；本 `PREP_REVIEW_GO` 不等于
测量代码 GO，也不等于目标机执行 GO。**本轮结束时 A0 的代码与镜像仍未被任何独立复审接受。**

## 2. 开工只读核对

| 项 | 值 |
|---|---|
| 本地 `HEAD` = 远端 `phase8/tmc-drive-config` | `bbf66ba34d7b31a217c643921aa4192df2f59a56`（本轮 document_base） |
| 工作树 | clean；未 reset/rebase/force/merge，未覆盖任何未提交内容 |
| `bcdec631..bbf66ba` | 生产/夹具/工程/宏无变化（全为文档），与任务书 §2 一致 |
| main / phase7 / 标签对象 / 解引用 | `1b3757ee…` / `6d3fbc55…` / `097935fe…` / `e40a9b95…`，本轮复核仍未动 |
| 四宏回读（开工与收尾各一次） | `motor.h:69`、`motor.h:83`、`tmc2209.h:100`、`project_board.h:51` 全部 `RT_FALSE` |

## 3. 本轮交付（按三个 checkpoint）

### CP-A0-1 记录器与三入口插桩 + 主机验证（定稿为 `code_commit`）

- 新增 `APP/sp_measure_a0.h` / `APP/sp_measure_a0.c`：功能开关 `NS800_TARGET_MEASUREMENT_A0` 默认 0；
  64 槽、单批次只追加、不覆盖、不循环复用；槽状态 `RESERVED→COMPLETE`；预约/发布用**原始 PRIMASK 短段**
  （不递归 `sp_win`、不取锁、不分配、不打印）；每条记录带
  `counter_status / clock_basis / body_valid / enclosing_valid / invalid_reason / guard_metadata /
  guard_load_seen` 与 `pre/body_begin/body_end/post` 四个原始时间戳；`spm` 命令族
  （`cap/init/start/stop/status/dump/clear_records/trip`）只在测量变体存在，非法输入与非法上下文在
  任何计数读取、状态变化与安全动作之前拒绝且不打印。
- `APP/stop_priority.c`：三个被测入口（`sp_trip_fast` / `sp_trip_for_operation` / `sp_off_attempt_for`）
  经编译期宏接入私有 measured-enter/exit，**只在原 `sp_end` 序列之间插入寄存器读**：屏障顺序、动作次数、
  返回优先序、事件/黑匣子语义与公开签名一律不变；早退出口也结束本次记录并写"本次未执行动作"。
- `APP/stop_priority.h`：只顺带更正此前登记的旧 TBCTL 注释（现码 `drv_epwm.c:293-318` +
  `drv_epwm_leaf.inc:232` 已不写回计数模式），公开接口不变。
- 新增 `tests/host/run_sp_measure_a0.sh` + `sp_measure_a0_main.c` + `sp_measure_a0_port.h`：真编译
  生产实现；夹具只提供线程本地 PRIMASK 模型 + 跨线程窗口独占 + 脚本化 PPB 寄存器模型 + 总线动作计数，
  **记录/判定/发布逻辑不再重写一份**。生产侧 `rt_kprintf` 经 `-Wl,--wrap=printf` 计入
  "窗口内打印"台账，于是"窗口内不许打印"是可判定的观察。
- 结果：`TMA01..TMA18` 全部真执行且 PASS（合计 226 项断言，逐组 `checks=` 相加）；两个具名反向家族
  `rev_spm_unavailable_is_valid`（具名检出 TMA02/TMA03）与 `rev_spm_snapshot_after_unmask`
  （具名检出 TMA11）均 `META-OK`；反向首轮的两次失败尝试（注入文本没写成注释 → 编译不过 = INFRA-FAIL；
  runner 的 `local fam="$1" shift` 误把家族名当期望 id → NOT-DETECTED；以及 TMA11 接缝点选在 `pin`
  之后导致检不出）全部如实保留在 `host_results.txt`，纠正注入点/接缝后复跑才算数。
- `code_commit = 678768739dd8e964fdcc428ef1168e4353c167f7`。

### CP-A0-2 集中回归 + 两目标 Rebuild All + 镜像与输入清单

- 集中回归（改动后的同一棵树，测量默认关闭）：S1 target **50**/host_ovr **65** 全 PASS；
  S2 target **17 组 23**/host_ovr **17 组 79** 全 PASS（叶子四组 10/15/7/6 在其中），
  `pre_bad=0`、两组 SKIP 与声明一致；未改且无接口影响的 V1/V2、I/O、SD、旧负例/元负例记 `NOT_RERUN`
  并引用已接受结果，不当作本轮新证据。
- Keil（批处理 `-r` Rebuild All，两个目标各一次，未进下载/调试模式，未做设备探测）：
  默认目标 `rt-thread` **0 Error 0 Warning**，`Code=173762 RO=76806 RW=2188 ZI=83784`
  —— 与已接受的 CP6 构建逐项相同 ⇒ 空翻译单元的直接体现；
  测量目标 `rt-thread-measure-a0` **0 Error 0 Warning**，`Code=179858 RO=79430 RW=2188 ZI=91504`。
- 编译期绕开的两条独立证据：默认映像 `map` 内 `spm_` 符号行 **0**（测量映像 361 行，
  `spm_rec` 在 `0x2000ac58` 占 **7680 B** = 64×120）；主机链 `a0_off` 可执行体里生产记录器符号=0、
  记录数组=0，`a0_on` 分别 22 / 1。`ZI` 增量 7720 B 与记录数组加控制状态一致。
- 工程改动：`BSP/project.uvprojx` 新增目标 `rt-thread-measure-a0`（整段克隆 + 6 处文本替换）；
  XML 逐叶子对比证明两目标只差 `TargetName / Cads.Define(+NS800_TARGET_MEASUREMENT_A0=1) /
  OutputDirectory / OutputName / ListingPath / AfterMake.UserProg1Name`，源文件清单各 135 项且逐条一致；
  `SConscript` 的 `Glob('*.c')` 未改。既有钩子核对：`BeforeCompile/BeforeMake` 全 0，
  `AfterMake` 只有 `fromelf --bin` 纯转换 ⇒ 无下载/设备动作。
- 镜像归档：`reports/firmware/phase8-target-measurement-a0/default.bin`
  `ef69404d388a7977f170550e324dfa50fd0cf152e41bc2831410c05628a92a20`（251148 B）、
  `measure-a0.bin` `e9ba8dae1a4b4d59697d5844551bfcd3fbba9061cc8fbcd11784cd7b61a8fc63`（259868 B）。
  ELF/AXF 只在本地（调试信息可能带本机路径），散列记在 `build_manifest.json`。
  `default.bin` **不是**板上 e40 的回退镜像 ⇒ 回退能力 `BLOCKED_ROLLBACK`。

### CP-A0-3 命令包、证据、交接与推送

- 新增 `reports/plans/phase8-target-measurement-a0-operator-pack.md`：以 §1 的真实镜像散列为批准对象，
  P-01..P-14 逐步列命令/次数/真实副作用/停止条件/证据字段/未满足前置；缺现场信息的步骤直接标
  `NOT_READY_FOR_AUTHORIZATION`；`pin_status` 因会重写输出脚默认电平被明确排除，`ss_test` 与各
  selftest/flash 写命令一律不放行。
- 证据：`reports/reviews/phase8-target-measurement-a0/` 下 `source_audit.txt`、`host_results.txt`、
  `keil_builds.txt`（含两份完整日志逐字 + 非平凡行核对）、`build_manifest.json`。
- 计划：`reports/plans/phase8-target-measurement-plan.md` 追加 §11（PREP_REVIEW_GO 登记 + A0 覆盖边界
  逐条对应），并把已被本轮允许范围内的 `stop_priority.h:23` 注释不一致结掉（另一处
  `开发进度.md` 行号过期仍开放，该文件不在本轮白名单）。

## 4. counter / clock / guard 前提（不预先宣称目标可用）

- 未做能力检查 ⇒ `COUNTER_UNCHECKED`，且此阶段**不触碰任何 PPB 地址**（TRCENA 未开时读 DWT 属实现定义）。
- `NOCYCCNT=1` 或两次读取不递增 ⇒ `COUNTER_UNAVAILABLE` ⇒ 没有有效计数差；**零值不是零耗时**，
  0 也不作为有效 cycles 参与统计。
- 主体区间"小于一圈"的依据是**窗口内 SysTick COUNTFLAG 未置位**（期间无重装），不是 `VAL` 模差分；
  前提 = `ENABLE && CLKSOURCE=处理器时钟 && LOAD!=0` 且**没有其它 CTRL 读取者**。审计：现构建内
  运行期读 `SysTick->CTRL` 的只有本记录器（`SysTick_Config` 只在启动写一次；`SysTick_Handler` 只做
  `rt_interrupt_enter/rt_tick_increase/rt_interrupt_leave`；`rt_hw_us_delay` 只读 `LOAD/VAL`）⇒
  清旗副作用对现工程无依赖破坏；但**未覆盖**调试器/另一核/NMI 类读取者。
- 嵌套扰动时**外层**也标 `NESTED_GUARD`（目标上只有不可屏蔽源能造成这种交错，记录器同步合同不覆盖它们）。
- `clock_basis` 初值 `SOURCE_ASSUMED`，成功 init 最多升到 `CONFIG_READBACK`；CLI 没有把它改成
  `EXTERNAL_VERIFIED` 的入口 ⇒ 本批不输出"已校准 µs"，晶振/核心频率真值仍待独立时基。
- `stop` 不清共享 DWT/DEMCR 位；调试暂停/睡眠使来源无法保证时相应样本判无效，不解释成"正常短耗时"。

## 5. 本轮未做（逐条）

未连串口/调试器/仪器；未扫描端口或设备；未读 Flash；未烧录；未复位；未运行任何目标命令；
未做 GPIO/TMC/STEP/DIR 操作；未接电机；未改供电或接线；未拨 SW1；未做真机清故障或 selftest；
未运行旧 `sw1_cut.ps1`（T-01/T-02 继续 `HOLD`）；未调用任何会在启动时探测硬件的脚本"查看环境"。
未改 HAL、内核、四个安全宏、时钟/引脚配置、其它生产模块、BSP 阶段总文档、`.gitignore`、SDK/第三方包；
未新增线程、采集平台或通用生成器；未修改任何既有 runner/shim/generator。
Keil 只用批处理构建模式；未进下载/调试会话。

## 6. 仍开放（不因 A0 而关闭）

`B-1` 门内窗口真实时长与 RX/EXTI 丢失容忍、`RISK-03/03a` 目标调度与最坏响应、`BUG-008` STEP 波形、
物理 `RISK-04-b`、`M-1/M-2/M-3`（含"寄存器写→焊盘"时间关系，需另行批准的共同事件/标记）、
T03–T13 与包 B/C。主机真线程 + 寄存器模型**不等于**目标 RT-Thread 调度、真实 PPB 行为或物理停机证明；
离线 call graph 的 `Maximum Stack Usage`（两目标同为 2996 B + Unknown）也不是目标栈高水位验收。

## 7. 下一步

本批产物（代码 + 两镜像 + 命令包）**先等独立复审**；复审通过后再由用户按 operator pack 的
P-01..P-14 **整包**决定是否批准，且必须先补齐 §4 那三样现场/工具信息。本轮不自授任何硬件权限，
不启动任何测量，不声称任何目标机结论。
---

## 8. r1 层（任务 `phase8-target-measurement-a0-r1`，2026-10-25）

### 8.1 上游结论与本轮性质

独立复审对 `c5028f4`（其 `code_commit` `6787687`）给出 **`A0_REVIEW_REWORK_REQUIRED`**：A0 测量镜像与操作包不予放行；常规链确实通过，但新增探针查出命令集成、计时判据与批次顺序缺陷，以及新 runner 的反向台账漏检。S2 `SOFTWARE_REVIEW_GO`（`bcdec631`）与 `PREP_REVIEW_GO`（`bbf66ba`）**不撤回**，本轮也没有发现默认关闭配置下的 S1/S2 合同回归。四组问题（A0-01..04）与第 7 节取证精度项在同一轮一次修完，不拆小返修。

本轮性质仍是**软件 / 主机验证 / 离线 Keil 构建 / 工件归档 / 普通提交推送**，不扩展到板级。

### 8.2 身份（构建直接发生在这一棵定稿树上）

| 项 | 值 |
|---|---|
| base / document_base | `c5028f4c502596d24cb81b80e927e599e0f3d6fd` |
| 被返修的 code_commit | `678768739dd8e964fdcc428ef1168e4353c167f7` |
| **本轮 code_commit** | `b27e5f14bdb62f645b93eace23800b40369717d4`（已含完整双目标工程状态；`engineering_commit` 说法在 r1 退休） |
| 默认镜像 | `default.bin` 251148 B `41717367e4e1401deb5f59eb27d1f35483108c6241eb30a5b58b28aad5b425de`；`Code=173762 RO=76806 RW=2188 ZI=83784`，0 Error / 0 Warning |
| 测量镜像 | `measure-a0.bin` 261100 B `358fd81ae5853ea52df1e1ab367ea41af0f5e86679082a755c17dac3429b0e1b`；`Code=180802 RO=79718 RW=2188 ZI=91504`，0 Error / 0 Warning |
| 上一轮两份镜像 | 只保留为 `historical`（`ef69404d…` / `e9ba8dae…`），不称本轮产物 |
| 默认镜像差分 | 与上一轮逐字节比较**只有 6 个字节**不同，位于版本横幅的 `__TIME__`（`src/kservice.c:162`）；字节数与 Program Size 四项逐字相同 |
| 命令名 | `spm`（AXF 里 `__fsym_spm` 的 name 串、func == `spm_cmd`、`__fsym_spm_cmd` 不存在；map 有 `sp_measure_a0.o(FSymTab) refers to .text.spm_cmd`） |
| schema | 2；记录仍 120 B（`spm_rec` 仍 7680 B = 64×120，ZI 未变）；CSV 47 列（新增 `gates`） |

### 8.3 四组返修的落点（细节与逐项映射见 JSON `a0_r1_review_resolution`）

- **A0-01**：`MSH_CMD_EXPORT_ALIAS(spm_cmd, spm, …)`；命令行先由 `spm_validate()` 完整验证成子命令枚举再 dispatch（单参数子命令要求 `argc` 恰为 2，`start` 恰为 3 且 entry 只在三个合法值内）；缺参/多参/未知子命令/非法 entry 都在任何计数读取、状态变化与关闭动作之前拒绝；合法线程上下文可打印用法，非法上下文静默。主机链新增 `tests/host/sp_measure_a0_finsh.h`（任务书允许的"该链专用一个短文件"），让同一条生产注册语句在主机变体里也参与编译，并**按名查表**分发。
- **A0-02**：每条样本自己核起点/终点门控（`counter_gates`，缺位 `0x0800`），`body_end == body_begin` 记 `0x0080 NO_PROGRESS` 而不是 0 耗时；`SPM_IV_FLAG_PRE` 退役为观测位（起点读早已把它读清），真重装/回绕/配置漂移/嵌套判据不变；`spm init` 只在静止态允许，非静止态零 PPB 访问且不换能力缓存。
- **A0-03**：操作包分批闭环（start → N 次 → stop → status 确认无在途 → dump 核对并**先**保存 → clear_records → 下一批）；删掉把合法状态当故障的停止判据；判据改用实际存在的 `body_valid`/`enclosing_valid`（无 `sample_valid` 列）；P-13 只用已核对的正式 `motor_disarm`，不可达记 `NOT_REACHED`，不加硬件故障注入；副作用分类按源码更正三条命令。
- **A0-04**：`ledger_check()` 被正向与反向共用，逐组核身份缺席/重复/CASE-RESULT 成对/未登记身份/数量/SUMMARY 与 EXIT 自洽/退出码合同；家族一的注入点移到 `spm_eval()` 的真实判据块；新增"省略非锚身份 TMA18 的 RESULT 行"的判定器负控，必须被自己判成 INFRA-FAIL。
- **第 7 节取证**：manifest 分开 Git blob 身份、blob 内容(LF)散列与工作区原始散列 + 行尾统计；`keil_builds.txt` 的 `spm_rec 76800 B` 笔误以 `[K]` 勘误标注（不改原日志数值）；原证据段落一律保留，新结果只追加为 r1 层。

### 8.4 实际结果（全部在定稿字节集上跑一次）

| 链 | 结果 |
|---|---|
| A0 正向 | 8 组 TMA01..TMA24 全 PASS，335 项断言，runner rc=0（原 18 个身份一个没删） |
| A0 反向 | `rev_spm_unavailable_is_valid` 精确具名检出 TMA02/TMA03/TMA21；`rev_spm_snapshot_after_unmask` 检出 TMA11；其余每组台账逐条一致且保持 PASS |
| 判定器负控 | 按合同判 INFRA-FAIL（"声明的 RESULT 身份缺席: TMA18"），不再报成"其余保持 PASS" |
| 符号门 | a0_off：记录器符号 0 / 数组 0 / `__fsym_spm` 0，三个被测入口 3；a0_on：符号 30、数组 1、`__fsym_spm` 恰好 1、`__fsym_spm_cmd` 0 |
| S1 正向 | rc=0；target 50/50、host_ovr 65/65（与 S1 反向**分别**运行） |
| S1 反向 | rc=0；`rev_motor_off_ignores_failure` 命中 5/5，第②项仍由 S2 的 C11d 家族代跑 |
| S2 正向 | rc=0；17 组各独立进程，target 23/23、host_ovr 79/79，pre_bad=0，叶子 10/15/7/6 |
| S2 反向 | rc=0；五家族 META-OK |
| Keil | 两目标各一次 Rebuild All，UV4 rc=0，均 0 Error / 0 Warning |
| V1/V2、I/O、SD、旧负例矩阵、元负例 | `NOT_RERUN`（未改其输入，也不为 A0 改旧链用例数） |

基线探针（修前，跑在 `c5028f4` 快照上，仓外）：`extra_arg rc=0 shutdown_calls=1`、`counter_disabled`/`counter_stalled` 均 `begin=end=1014 body_valid=1`（两条真实关闭动作仍执行：`pwm_disable=1 permit_low=1`）、`historical_countflag 1021->1028 body_valid=0 invalid=0x104`、`pack_sequence second_start_rc=1 records_after_17_calls=1 state=2`、`init_lifecycle active_rc=0 inflight_rc=0 ctrl_reads=3->4`、负控"少 TMA18 的 RESULT 仍 META-OK"。修后同一组探针除 `pack_sequence`（复现的是被审原文顺序，修的是文档不是状态机）外全部 defect=0，修订后的顺序 `pack_batches batch1=1 batch2=16 defect=0`。

### 8.5 边界与下一步

本轮**没有任何目标机动作**：未连串口/调试器/仪器、未扫描设备或端口、未读或写 Flash、未烧录/复位、未运行任何目标命令、未操作 GPIO/TMC/STEP/DIR、电机、供电、接线、SW1、未做真机清故障或 selftest，旧 `sw1_cut.ps1` 继续 `HOLD`。四个目标安全宏继续 `RT_FALSE`，未对目标用 `-D` 覆盖。

仍然开放：`B-1`、`BUG-008`、`RISK-03/03a`、物理 `RISK-04-b`、`M-1/M-2/M-3`、T03–T13 与包 B/C；目标上 `counter_status/clock_basis` 的真值、FSymTab 的运行时枚举与 msh 真实解析全部 `NOT_EXECUTED`；AXF/map 与 Keil 结果仍是 `AGENT_REPORTED`（云端未重建）；`default.bin` 不是板上 e40 的回退镜像（`BLOCKED_ROLLBACK`），板上身份仍 `USER_REPORTED_E40`，既有 `USER_CONFIRMED` 静态电气与 SW1 结论保留，不重复索取五点电压。

下一步：把 r1 的软件与两份镜像交独立复审；只有拿到 `SOFTWARE_REVIEW_GO` 之后，才谈用户对操作包P-01..P-14 的板级整包批准。本轮不自授任何 GO，不启动任何测量，不声称任何目标机结论。

`STOP: A0_R1_OFFLINE_FIRMWARE_READY / AWAITING_INDEPENDENT_SOFTWARE_AND_IMAGE_REVIEW；NO BOARD/SERIAL/DEBUGGER/GPIO/TMC/STEP/MOTOR AUTHORIZATION；FOUR TARGET SAFETY MACROS RT_FALSE。`
