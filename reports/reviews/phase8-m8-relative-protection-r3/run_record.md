# M8-R3 运行记录（phase8-m8-relative-protection-r3）

日期：2026-10-31。分支 `feature/m8-sg-adc-relative-protection`。任务书：`NS800_M8_R3_Concentrated_Repair_Task_20261031.md`（结论 `M8_R2_REVIEW_REWORK_REQUIRED。不进入现场。`）。
本轮范围：**离线开发 + 相关主机执行 + 三目标构建 + 原分支追加提交/推送**；未连接任何设备（`hardware_actions=[]`），未做下载/Flash 读写/DAP/串口/复位/起转/apply/触发/清故障。

## 1. 输入与产物身份

| 身份 | 值 |
| --- | --- |
| document_base（复审交付头） | `5c549f2af0740425c4014f374d064f77ee62d6b4` |
| 本轮 code_commit（产品+验证链） | `805c29bd73c4ea861c9b9c65f61e946aec9548e3` |
| 被测镜像构建取自 | 同上（构建时 `git status --porcelain` 对已跟踪文件为空；构建之后的提交只新增 `reports/**`，不改 `A/`、`tests/`） |
| 补丁输入 / 输出 `A/diag_relative.c` | `3960fb7e4a2d7db31869debc703119f5fe671d6d436aec4976e9372ad89ed57d` → `059fc7535a550462c8c1c10039d5efdc419842811eadcf7ad8f2711bff541ce5`（= 复审候选；补丁 `5baffaeb…f8cd` 逐字节应用） |
| 本轮三目标镜像清单 | `reports/firmware/phase8-m8-relative-protection-r3/build_manifest.json`（`code_commit=805c29b…`） |
| 本轮现场执行器 | `field/m8r3_serial_chain.py`（散列见 `SHA256SUMS.txt`；计划不自引用执行器，执行器也不自记散列） |

## 2. 本轮实际执行的验证（全部为真实执行，不是清单）

| 项 | 结果 | 原始件 |
| --- | --- | --- |
| 原 C 链（含新边界 REL50） | `SUMMARY cases=50 pass=50 fail=0 pre_bad=0 executed=50`，`build_rc=0`，本机 `warnings=0` | `host/regression_m8_normal_console.txt`、`host/regression_m8_ledger.txt`、`host/c_chain_output.txt`（342 行产品输出，派生规则见 `host/c_chain_output.derivation.txt`） |
| 反向验证（九旧 + 新新鲜度） | RV1…RV10 全 **META-OK**，runner rc=0（RV10 = 只把"年龄"从豁免条件里摘掉，命中 `REL50` + `REL49` 两个身份） | `host/regression_m8_reverse_console.txt` |
| 变更适用的主机链 | `run_current_cal_m7.sh` 34/34（`diag_relative.c` 在其编译集合内）；`run_m8_prefix_probe.sh` 3/3（在精确基线 `f1aa2e83…` 上复现修前观察） | `host/regression_current_cal_console.txt`、`host/regression_m8_prefix_console.txt` |
| 现场包 Python 自检 | `SUMMARY cases=55 pass=55 fail=0 executed=55 preserved_r2_identities=25 negative_injections=7` | `host/field_SelfTest_console.txt` 内嵌；工作目录在 `offline/psrun-*SelfTest/console_python_selftest.txt` |
| 审核包同输入边界探针（修后） | `R3_BOUNDARY_SUMMARY cases=18 fail=0 hardware_actions=0 port=PURE_MEMORY`；修前 5/5 缺陷复现原样保留（`fb/boundary_before.out.txt`，与复审包同元组） | 仓外 `D:/ns800_m8r3_scratch/`（`boundary_after*.out.txt`、`fb/boundary_before.out.txt`）；判据已并入 `field/m8r3_field_selftest.py` 的 FS26…FS55（R2 的 FS01…FS25 身份原序保留）|
| 整包调度 `--dispatch`（三档案） | healthy `run=9 not_run=[]`、no_trigger `run=9 not_run=[]`、trip `run=12 not_run=[]`，全部 rc=0 | `offline/psrun-*RunField/dispatch-run-sim-<profile>/`、`host/field_RunField_console.txt` |
| Windows PowerShell 5.1 三入口 | `-SelfTest` 20 检查 0 失败；`-Precheck` 14 检查 0 失败；`-RunField` 3 检查 0 失败（三次都是 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File`，且**不在 shell 里预设任何编码变量** —— 编码钉死是脚本自己的责任，见 §5 第 11 条）。权威三次运行：`psrun-20261031T104907Z-38116-SelfTest` / `psrun-20261031T104920Z-46908-Precheck` / `psrun-20261031T104924Z-52140-RunField` | `host/field_SelfTest_console.txt`、`host/field_Precheck_console.txt`、`host/field_RunField_console.txt`（这三件现在是运行目录 `transcript_console.txt` 的**字节副本**，不再经过 console codepage；换源前后的对照在 `host/offline_run_compare.txt`） |
| `rows.json` 来源行级核对（本轮新增） | `ROWS_PROVENANCE groups=38 capture_refs=27 source_anchor_refs=28 problems=0 notes=0`（`.b64` 采集件先 base64 解码再数行；`applications/*.c:NN` 锚点要求该行 ±6 行内确有 `rt_kprintf`） | `host/rows_provenance_check.py`；首次跑出 3 项 problems 与 4 项 notes 的过程登记在 §5 第 10 条 |
| 提交前置校验（暂存 blob vs 清单） | `MANIFEST 评审清单 entries=1575 problems=0`、`MANIFEST 固件清单 entries=10 problems=0`、`BUILD_MANIFEST resolved=9 problems=0`、`STAGED_PREFLIGHT_TOTAL problems=0`（`git cat-file blob :<path>` 三方对照：清单 sha == blob sha == 磁盘 sha） | `host/staged_blob_preflight.txt`（含范围声明：该文件自身与新清单里它的条目由推送后的远端回读覆盖） |
| 三目标 Keil Rebuild All | 每目标恰好一次（`build/rc.txt` 逐目标一行）：`rt-thread` rc=0 / `0 Error(s), 0 Warning(s)` / `Code=204530 RO-data=85702 RW-data=2188 ZI-data=84824` / 14.3 s；`rt-thread-measure-a0` rc=0 / 同上 / `Code=211826 RO-data=88662 RW-data=2188 ZI-data=92544` / 12.2 s；`rt-thread-motor-m1-candidate` rc=0 / 同上 / `Code=204762 RO-data=85578 RW-data=2188 ZI-data=84824` / 12.2 s。有效宏：默认与 A0 四宏全 0；候选 `MOTOR=1 TMC=1 STEP=0 LIMIT=0`（原样不扩权） | `build/rt-thread*.log`、`build/rc.txt`、`reports/firmware/phase8-m8-relative-protection-r3/`（6 镜像 + 3 map，逐件与构建输出 `BYTE_EQUAL`，见 manifest 的 `copy_verification`） |

### 2.1 整包调度的实际额度序列（`quota_used`，非申报值）

| 档案 | 实际用量 |
| --- | --- |
| healthy / no_trigger | `port_open=5 download=1 readback=1 explicit_reset=1 approved_reboot=1 motor_run=2 config_save=1 tmc_config_apply=2 irq_polarity_prep=4 diag_profile_zero/learn/commit=1/1/1 cleanup_motor_stop/disarm/status=2/2/2` |
| trip | 同上，另 `port_open=8`、`cleanup_motor_stop/disarm/status=3/3/3`（S9 取证、S10 锁存态拒绝、S11 人工恢复各一次开口与清理） |

申报值（`plan.json:quota`）是上限：`port_open=8`、清理预留各 3、`retry/physical_collision/blackbox_clear/dmm_readings=0`。`--dispatch` 结束逐键打印 `QUOTA_AUDIT|<key>|declared|used|left`，出现"用了未申报的键"或"余额为负"即整体非成功（`QUOTA_AUDIT_FAIL`）。

### 2.2 起转硬截止与清理分段实测（SimClock，stub 传输）

`S_S4` / `S_S8` 两次的义务时刻都是 `t=0.005 s`（arm 与 start 都在 `port.write` **之前**建立义务与截止起点），清理三条命令的发送/确认时刻：

```
S_S4  motor_stop 5.005→5.005 | motor_disarm 5.005→5.005 | motor_status 5.005→5.015   (window_s=1.2 each)
S_S8  motor_stop 5.010→5.010 | motor_disarm 5.010→5.010 | motor_status 5.010→5.020   (window_s=1.2 each)
S_S10(trip, 锁存态 arm 被拒) motor_stop 0.005→0.005 | motor_disarm 0.005→0.005 | motor_status 0.005→0.015
```

即：观察窗跑到 5 s 上限后，三条清理仍在 4 s 保留段内**各自**发出并各自确认；`disarm=CONFIRMED_DISABLED`、`terminal=KNOWN`。审核表里"stop 无应答吃掉 disarm 机会"那一支的对照判据在 FS28/FS34/FS41/FS52 里断言的是**发送序列与时刻**，不是汇总标签。

## 3. 逐条对账：复审要求的修法是否落地

| 审核条目 | 落地位置 | 判据（可检） |
| --- | --- | --- |
| R3-A 过期 IDLE 不再豁免 | `A/diag_relative.c` 的 `motion_trusted`/`motion_age_limit`（补丁逐字节） | `tests/host/rel_main.c` 的 `REL50`（8 支）+ RV10 只删年龄项；49 身份原样保留 |
| B1 write 前建义务 | `exec_session` 的 `MOTION_OBLIGATION_CMDS` 支（义务与 `start_attempt` 都在写之前） | FS26 + FS54（把义务推回写之后 → FS26 必须转 FAIL） |
| B2 写耗时进预算 | `MotionBudget.business_deadline`（起点=尝试之前） | FS27（start t=2.0 → stop t=5.0 ≤ cap）+ FS10/FS23 |
| B3 stop 无应答不饿死 disarm | `cleanup()` 每条命令独立 1.2 s 窗 + `continue` 不 `break` | FS28/FS34/FS41 + FS52（退回共享截止 → FS28 转 FAIL） |
| B4 运动窗内不许阻塞 | `plan_motion_window_violations()` 在**开口前**拒绝 | FS29（`writes=[]`、`PLAN_MANUAL_AFTER_MOTION`）；本轮所有 manual 步骤都排在 arm/start 之前 |
| B5 禁能按真实合同字段 | `disable_contract()` 六字段同响应 + `DISABLE_ALLOWED_STATES=(FAULT,OTHER)`；`drv_en_request` 只作历史披露 | FS30/FS31/FS32/FS34/FS35/FS36 + FS53（退回 IDLE-only → FS30 转 FAIL） |
| C1 一次批准走完整包 | `dispatch_plan()`（成功阶段只记检查点，失败才停；NOT_RUN 明列） | FS42/FS43/FS44/FS45 + `-RunField`/`-SelfTest` 实测 9/12/9 阶段 |
| C1b 工具真实参数 | `plan.json:tools`（逐字 argv + `expect_all/expect_counts` 源自 P0 的 S1/S2 成功原文；`@UID_FILE/@RUN_ROOT/@IMAGE_BYTES` 由执行器展开，残留即 `TOOL_ARGV_UNRESOLVED`） | FS48/FS49/FS50；`--validate-plan` 的 `PLAN_TOOL_ARGV` |
| C1c 额度整包共用 | 单一 `quota_box` + `cleanup_<cmd>` 独立预留 + `QUOTA_AUDIT` | FS41（预留扣减/耗尽具名披露且不算成功） |
| C2 真协议/学习/保存恢复 | `tmc_config_apply CONFIRM`；`motor_start` 成功不要求 `[MOT]`（`expect_absent`）；起转后先 `expect_profile=RUNNING` 再 learn；提交与保存在**静止会话 S5**；重启恢复用 `record_from`/`compare_recorded` 实际比较 | FS51 + `--validate-plan` 的 `PLAN_ROWS_KEY/PLAN_QUOTA`；FS46/FS47（漂移、新增不增大都必须具名停止） |
| C3 演示目的不缩水 | S0→S11 全链（含 S9 事故取证按**实际新增 session**、S10 锁存态 arm 具名拒绝、S11 人工恢复且禁 `motor_start`） | FS43/FS44 + `plan.json:stages` |
| 成功口径 | CLI 退出码只认 `success`（清理 UNKNOWN/错误/预留耗尽/证据失败一律非 0）；PS 按同一口径停止 | FS40（证据失败具名停止）+ FS45 + `ST_NEG_STAGE_FAILURE_HALTS`（PS 侧负控） |
| 无占位可留 | `plan_placeholder_scan()`（`<...>`、`packmem`）在 S0 拒绝；`msh_command_names()` 刮真实命令表验证每条命令存在 | FS48 + `--validate-plan checks=146 fail=0 msh_commands=70` |

> 本表的 `FSxx` 一律按 `m8r3_field_selftest.py` 的 `CASES` 表**逐条回读**得到（`FS01…FS25` 是 R2 保留身份，
> `FS26…FS55` 是本轮新增）。中途补用例使编号整体下移过一位，先前草稿里 `FS39…FS54` 的引用因此错位；
> 本节与交接件都已按当前 `CASES` 表重算，登记为本轮真实发现的缺陷之一（见 §5 第 9 条）。

## 4. 明确未验证 / 未完成（不得当成通过）

1. **设备侧全部未验证**：`real`/`manual` 后端本轮从未执行；`transport` 只用过 `stub`，端口只用过纯内存 `FakePort`；`-RunField` 只以 `-Transport stub -Backend sim` 跑过。自检与调度全绿只说明**离线控制流**，不构成任何现场异常或保护动作证据。
2. **合成行的边界**（`field/rows.json` 的 `_provenance.kind` 已逐组标注）：
   - `source_shape`（无板级采集，形状取自产品 `rt_kprintf` 字面量、数值为本轮下发/注入）：`diag_profile zero/learn/tier/commit/show`、`config_save`、`diag_mode relative`、`fault_reset`、`system_selftest`、`__watch__.trip` 的 `REL TRIP/LATCH`、`blackbox_status@after`、`motor_status@latched`、`safety_irq_status@boot` 等。板上从未产生过相对保护的触发行 —— 这些只喂离线控制流。
   - `board_capture_composed`：`motor_status@running`（两块真机采集的逐字行并成一份响应，并把 300 Hz/dir=1 换成本轮下发的 100 Hz/dir=0）。
   - `board_capture_split`：被 `RT_CONSOLEBUF_SIZE=128` 截断、把下一条输出咬进同一行的采集，按内容拆两行（线上两条之间并无 CR/LF）。
3. **恢复比较的"记录值"来自夹具**：S5 记的 `id=7 rev=1 flags=0010 live sig=44100 match=YES` 是本轮 rows.json 的下发值，比较判据是真的（FS46 证明漂移会 `RESTORE_MISMATCH`）；**Flash v2→v3 迁移是否真在板上成立仍需现场证据**。
4. **learn 的 12 s 内部超时 > 5 s 硬截止**且 msh 单线程：该风险只在 `plan.json:residual_risk` 里登记，缓解是 N=8 + 先证 RUNNING；物理制止仍靠操作者（SW1/先关 24 V 再拔 USB）。
5. `SAFETY`/`MOTOR` 的使能链、IRUN20 抬流、示波器、300 帧故障演示、高精度 P1/P2 前置：本轮不涉及（任务书边界）。
6. 历史披露保持：R2 清单的 64 问题与 `no_path=32` 保持原样，未按 R3 扩大修复；本轮 173/173 已接受的工件未改写。

## 5. 本轮已知失败与自伤（原样登记）

1. **PowerShell 变量名大小写不敏感**：局部 `$entry`（清单里的镜像条目）顶掉了脚本级 `$script:Entry`（参数集名），后果是运行目录名里被塞进整张镜像对象、`New-Item` 报"不支持给定路径的格式"。已改名 `$imgEntry`/`$script:EntryName` 并在脚本里写明这条陷阱；证据目录已重建。
2. **自检里仓库根层级算错**（`HERE.parents[2]` 应为 `[3]`）→ 调度 `S0_IMAGE_ABSENT`；修后三档案才全绿。
3. **msh 命令名刮取错**：把 `MSH_CMD_EXPORT(fn, 描述)` 的第二参当命令名，得到 `print/show` 之类描述词（45 个）；改为"仅 `_ALIAS` 取第二参"，真实命令 70 个，`--validate-plan` 才有意义。
4. **`emit_text` 定义了但从未接线**（第 5 段补丁声称已接，实际没有）→ 判读文本 `.txt` 只登记文件名不落盘，`record_from` 就没东西可读。第 7 段真接线并加 FS39/FS40。
5. **`assert_step` 的判定层次顺序**：内容缺失先于业务失败返回，导致真实的 `learn FAILED:` 被报成 `EXPECTATION_NOT_MET`。改为**业务层先判**（FS05/FS25 的定向检出因此仍然成立）。
6. **一次编辑事故**：替换模块 docstring 时切片少删了一个三引号，文件短暂不可解析；已补回闭合符并用 `18/18 边界探针 + 55/55 自检 + 三档案 dispatch + PS 三入口` 复验（本轮的 dispatch/自检证据都是修复后的运行）。
7. **被取代的运行目录**：变量名事故与修复前的 5 个 `offline/psrun-*`（含 3 个空目录、两个共 771 文件的运行）在提交前删除；那一批删除的都不是已发布件。本轮交付最终保留 `0936xx`（缺陷原件，含 §5 第 11 条的坏归档）与 `1049xx`（权威）两组各三个运行；第二次重跑产生的 6 个中间目录在第 12 条里按名删除并先留了逐文件对照。
8. `m8r3_field_selftest.py` 的 FS01…FS25 中依赖旧夹具（`name=IDLE`/`name=CRUISE` 三字段块、manual 排在 arm 之后、`packmem` 命令串）的四条按 R3 真实合同改写（保持身份与性质不变）：FS09/FS15/FS16/FS17-18 + `DISARM_ROWS` 等夹具改为直接取 `rows.json` 的真机行。
9. **文档里的 `FSxx` 编号错位**（本轮写交接/运行记录时真实踩到）：自检用例是分批补进去的，`emit_text`、证据失败、清理预留等用例插入使编号从 `FS39` 起整体下移一位，而 §3 与交接 §1 照旧引用，于是凡 `FS39` 之后的判据都指向别的用例（最典型：把"清理退回共享截止"的反向写成 `FS51`，而 `FS51` 实际是"learn 撞 12 s 超时仍具名清理"）。处置：按 `m8r3_field_selftest.py` 的 `CASES` 表逐条回读重算本文与交接件的全部 `FSxx` 引用，并在 §3 表下写明"编号以 `CASES` 表为准"。这类错位不会被任何测试抓到——它只在文档里，只能靠回读用例表发现。
10. **`rows.json` 的来源声明与实物不符**（新增 `host/rows_provenance_check.py` 第一次跑就抓出来的，共 3 项 problems + 4 项 notes）：
   - `motor_status@running` 的 kind 是 `board_capture_composed`，但 `_provenance_legend` 里**没有这一级**（legend 只解释了三级）⇒ 补进 legend 并写清"两块真机采集并成一份 + 替换计划下发值 ⇒ 不构成板上该状态的证据"；
   - `__watch__` 声称 `board_capture` 却只给了缩写的 `m4/l1_monitor_console.txt`（不带 `reports/**` 前缀，机器无法定位）⇒ 改成全仓相对路径 + 精确行段，并补 `applications/diagnosis.c:467-478` 说明"无触发即无输出"是源码事实；
   - `diag_profile show@after_reboot` 的来源写成**"同上"**（人眼能懂、机器无从解析），`_provenance_no_trigger` 声称 `board_capture` 但其实没有任何采集（空窗口的依据是打印条件）⇒ 两者都改成显式锚点与正确等级；
   - 4 个 notes 是"给了文件没给行号"的锚点（`safety_state.c` / `motor.c` / `safety_thread.c` / `tmc2209.c`）⇒ 全部补到 `file:line`（`:980` / `:1104` / `:385-392` / `:3141 与 :3189`），改完 `problems=0 notes=0`。
   这些改动只动 `_provenance*` / `_about` 元数据，不动任何响应行；为证明"没动判定"，改后重跑了 PS 三入口与三档案 dispatch，并与改动前的运行逐文件对照（`host/offline_run_compare.txt`：缺陷原件 vs 权威运行 410/420 与 338/344 相等，差异只落在 python 派生件与 `rows.json` 副本上）。
11. **交付归档里的中文是坏的**（本轮最实质的取证缺陷）：`host/field_*_console.txt` 原先是把 PowerShell 进程 stdout 直接重定向得到的。`Start-Process -RedirectStandardOutput` 落盘时 python 按**本机 locale（cp936）**编码 stdout，脚本再用 UTF-8 读回来 ⇒ 运行目录里带中文的那几行本身就是乱码/U+FFFD，PS stdout 的二次重定向只是把同一份坏字节存档。**"能严格 UTF-8 解码"不是判据，"有没有替换符"才是**——旧归档正是严格 UTF-8 却含 U+FFFD，所以此前无人发现。修法两半都写在脚本里：①`Invoke-Py` 显式钉 `PYTHONIOENCODING=utf-8` 并**拒绝任何含 U+FFFD 的 python 输出**（具名 `PYTHON_OUTPUT_NOT_UTF8`）；②`Say` 逐行缓冲，入口收口时由 .NET 按 UTF-8 写 `transcript_console.txt`，`host/` 的三件控制台改为它的字节副本。反向自证：把 `$env:PYTHONIOENCODING` 那一行去掉的仓库外副本（`NEGATIVE-INJECTION R3-ENC`）跑 `-Precheck`，rc=1 且具名命中 `PYTHON_OUTPUT_NOT_UTF8|...first_bad_line=PLAN_MOTION_WINDOW|S0|PASS|<乱码>` —— 门是真的会咬，不是摆设。旧的三件归档**改名保留**为 `host/field_*_console_superseded-cp936capture.txt`，未删除。
12. **中间两次重跑的运行目录在对照记录之后删除**：为修 §5 第 10/11 条共重跑过 4 轮三入口（`0936xx` 缺陷原件、`1033xx`、`1040xx/1042xx`、`1049xx` 权威）。`1033xx` 与 `1040xx/1042xx` 与权威运行的**逐文件归一对照已先写进 `host/offline_run_compare.txt`**（RunField 344/344 全等；SelfTest 418/420，剩两件是夹具临时目录随机名，另加只有权威版才有的 transcript），且都**从未提交入 Git**（`git ls-files` 为 0），因此删除这 6 个目录不损失任何判定；保留的是缺陷原件 3 个 + 权威 3 个。删除只针对本轮自己生成的 `psrun-*` 目录名，逐目录点名执行。

## 6. 批准边界

- 本包尚未获得任何硬件批准：`plan.json:status = NOT_AUTHORIZED / NOT_EXECUTED`，`hardware_actions_this_round=[]`，交接写 `M8_R3_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable=false`。
- 不继承 M4/M5/M6/M7/P0/M8-R2 的任何现场额度；本轮不向用户索要例行断电或 SW1 操作。
- 硬件候选要等软件 + 执行包**一次复审**之后，才交用户一次具体批准；`-RunField -Transport real` 需要 `--approval-token` 与既存私有 `--uid-file`，且 DAP UID 永不入库、永不打印（只披露长度与 sha256 前 12 位）。
