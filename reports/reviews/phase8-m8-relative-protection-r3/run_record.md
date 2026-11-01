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
12. **中间两次重跑的运行目录在对照记录之后删除**：为修第 10/11 条共重跑过 4 轮三入口（`0936xx` 缺陷原件、`1033xx`、`1040xx/1042xx`、`1049xx` 权威）。`1033xx` 与 `1040xx/1042xx` 与权威运行的**逐文件归一对照已先写进 `host/offline_run_compare.txt`**（RunField 344/344 全等；SelfTest 418/420，剩两件是夹具临时目录随机名，另加只有权威版才有的 transcript），且都**从未提交入 Git**（`git ls-files` 为 0），因此删除这 6 个目录不损失任何判定；保留的是缺陷原件 3 个 + 权威 3 个。删除只针对本轮自己生成的 `psrun-*` 目录名，逐目录点名执行。

13. **本轮的回读程序自己在真跑时暴露两处判据缺陷**（这正是"必须真跑一遍清单"的价值，R2-D 是同一类）：
    - 反向"树里有但清单没登记"沿用 R2 的**候选键**判重，而本轮清单改用仓库相对路径 + 三种基准后，候选键不再是真实路径 ⇒
      `SCOPE_R3 entries=1594 verified=1594 problems=0` 却同时报 `unlisted_r3=1584`，把每件已验证件都说成未登记。
      修：反向判定改按**解析后的真实路径**集合（`resolved`）；
    - `build_manifest.json` 的 `map` 对象没有 `path`，只有 `bytes/sha256/...` ⇒ 回读只能按后缀猜，三条 map 落进 `NO_PATH`
      （本轮 35 条 NO_PATH 里有 3 条是本轮自己的）。修：生成器给每个 image/map 补显式仓内 `path`，
      重算后与原 manifest 逐字段比对，**只有这 9 个 `path` 字段新增**，bytes/sha256/宏/Program Size/copy_verification 一字未变；
    - 另补一条正向能力：同一远端文件被两份清单登记成不同散列 => 具名 `cross_manifest_sha_conflict`，
      让"plan.json 申报的镜像"与"固件清单"不一致当场炸，而不是等人比对。
14. **补一次 `-Precheck` 真跑**：`build_manifest.json` 字节变了（新增 `path` 字段），所以清单与 blob 必须重新对齐，
    运行 `psrun-20261031T112105Z-41180-Precheck`（14 检查 0 失败）归档在
    `host/field_Precheck_console_after-manifest-path-fix.txt`。已发布的 `104920` 那一次与其归档**保留不动**，不覆盖、不删除。


- 本包尚未获得任何硬件批准：`plan.json:status = NOT_AUTHORIZED / NOT_EXECUTED`，`hardware_actions_this_round=[]`，交接写 `M8_R3_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable=false`。
- 不继承 M4/M5/M6/M7/P0/M8-R2 的任何现场额度；本轮不向用户索要例行断电或 SW1 操作。
- 硬件候选要等软件 + 执行包**一次复审**之后，才交用户一次具体批准；`-RunField -Transport real` 需要 `--approval-token` 与既存私有 `--uid-file`，且 DAP UID 永不入库、永不打印（只披露长度与 sha256 前 12 位）。
---

## 6. R3 收尾轮(复审 §2A–§2D + §3)的实际执行

日期 2026-10-31 → 2026-11-01。同一任务、同一 feature 分支、同一执行器; **未改 C、未重编 Keil、
未重跑未改输入的 C 全链、未改六镜像/三 map、零设备动作**(无串口/DAP/复位/下载/回读/运动/配置写)。

| 项 | 本轮实际数字 | 原始 stdout |
| --- | --- | --- |
| 交付 Python 自检 | 73/73 PASS(原 55 身份与 25 条 R2 身份原序保留, 定向检出 7→11) | `host/python_selftest_r3closeout.txt` |
| 计划离线门 | 151 项 PASS/0 FAIL(新增 PLAN_RESET_BEFORE_FIRST_QUERY、PLAN_STATE_AND_FLAG_CONSTANTS、PLAN_MOTION_CAP_BUDGET) | `host/validate_plan_r3closeout.txt` |
| 行表来源核查 | groups=40 capture_refs=29 source_anchor_refs=31 problems=0 | `host/rows_provenance_console_r3closeout.txt` |
| PS -SelfTest | checks=22 failures=0(run_id `20261031T164542Z`) | `host/field_SelfTest_console_r3closeout.txt` |
| PS -Precheck | checks=14 failures=0(run_id `20261031T164316Z`) | `host/field_Precheck_console_r3closeout.txt` |
| PS -RunField(stub+sim, -Profile all) | checks=3 failures=0 actions_attempted=0(run_id `20261031T164321Z`) | `host/field_RunField_console_r3closeout.txt` |
| 审核方观察程序 `remaining_field_probes.py` 指向收尾后的工作树 | rc=1: 其**修前**断言 `observations[0]["resets"]==0 且 status=="STOP"` 已不再成立(S3 现在先复位) | `host/reviewer_probe_post_fix_r3closeout.txt` |

新落入执行器的确定反例/健康对照(FS56…FS73, 全部跑同一条交付执行器, 只替换纯内存端口/动作后端/
`subprocess`; 不导入 `serial`):

- FS56 回读后 Halted: 复位先于首条查询才通过; 反序 => `MISSING_RESPONSE_PROMPT` 且 resets=0; 无提示符 =>
  `POST_RESET_BOOT_PROMPT_MISSING`。
- FS57 `RealActionBackend` 零设备注入: 只替换 subprocess 与交付文件, 用既存 P0 原文跑下载原文门 /
  定长比较 / 逐字节等值 / 非零 rc / 复位处理, 并核 `cwd=repo_root` 与 argv 已按 repo_root 解析。
- FS58 UID 只在内存: 公开件里 `ARGV.json` 与台账均无 UID, 只登记脱敏 argv + 原 argv 的 sha256 + 长度/sha12。
- FS59 stop 的读异常不阻止唯一必要 disarm, 端口仍关, 主错与清理错分列。FS60 短写具名停止且义务仍成立。
- FS61 开口额度在尝试之前消费(零额度 => 零尝试; 开口抛错 => `PORT_OPEN_FAILED` 且额度已扣)。
- FS62 预期拒绝 + 清理 UNKNOWN/清理错 绝不进恢复; 健康具名拒绝仍作为预期结果接受。
- FS63 混合模式(real+sim / stub+real)在任何动作之前 `MODE_COMBINATION_REFUSED`, 副作用清单为空。
- FS64 一致但不同于夹具的签名通过, 真漂移仍 `RESTORE_MISMATCH`。FS65 非法 flags(0010)按位拒绝。
- FS66 轮询证得 `state=2`, 一直 ACCEL 到期撤能, 矛盾 RUNNING 字段立刻拒绝。FS67 忙 msh 真 12 s 边界。
- FS68 真实入口的分支由本次响应决定, 未定即 `RESPONSE_BRANCH_UNDETERMINED`(零工具调用)。
- FS69 新增 session 无实际帧/帧不属本次 session/无停后帧/槽读错 => `FLASH_FRAMES_NOT_RECOVERED`。
- FS70–FS73 四项新增定向检出(按位判定 / 忙期模型 / 轮询 / 启动流消费), 每项都让对应正控转为 FAIL。

## 7. 最终补正轮(FF, 2026-11-01)的实际执行

基线 `15745e548dcfc4983fb91563fdac81ad5fdf3dc9`(未前进)，核心软件/镜像输入仍为 `805c29b…`，
主机 Windows 10.0.26200 / PowerShell 5.1.26100.9711 / Python 3.12.5。以下每条都是本机真实执行，
退出码取自当次调用。

| # | 命令 | rc | 关键原文 |
| --- | --- | --- | --- |
| 1 | 五件基线: `git cat-file blob 15745e5:<field 路径>` → 仓外 LF 副本 → sha256 对 `patch_identity.json` | 0 | 五件基线全对(表在 `host/ff_identity.json`) |
| 2 | `git -c core.autocrlf=false apply --check -p1 tail_candidate.patch` / `apply` | 0 / 0 | 五件输出散列 = 候选散列; 补丁 21667 B / `07de65f0…45eee` |
| 3 | `python field/m8r3_field_selftest.py` | 0 | `SUMMARY|cases=73 pass=73 fail=0 executed=73 preserved_r2_identities=25|negative_injections=11` |
| 4 | `python host/reviewer_ff/tail_boundary_probes.py --root D:/ns800 --inputs …` | 0 | `BOUNDARY_SUMMARY|pass=22 cases=22 hardware_actions=0 real_tool_calls=0` |
| 5 | 同一程序 `--root <15745e5 的 Git blob 副本>`(修前对照) | 1 | `BOUNDARY_SUMMARY|pass=7 cases=22`，逐件旧行为: F1 三件启动都被 `POST_RESET_BOOT_PROMPT_MISSING` 拦下、F2 的 `-c` 串被前缀成 `<repo>\savemem 0x08000000 …`、F3 有符号帧 `CSV_FRAMES=0`、F4 旧槽/重槽不拒且无区间推导、F5 白名单无 `response_driven` |
| 6 | `python field/m8r3_serial_chain.py --validate-plan --plan … --repo-root D:/ns800` | 0 | `PLAN_VALIDATE_SUMMARY|checks=154|fail=0|msh_commands=70` |
| 7 | `python host/rows_provenance_check.py` | 0 | `ROWS_PROVENANCE|groups=41 capture_refs=28 source_anchor_refs=33 problems=0 notes=0` |
| 8 | `powershell -File field/M8R3Field.ps1 -SelfTest` | 0 | `SELFTEST_SUMMARY|checks=22|failures=0`，含 `ST_NEG_REAL_PROFILE_ALL`/`ST_NEG_MODE_MIX_REFUSED` 仍 PASS |
| 9 | `powershell -File field/M8R3Field.ps1 -Precheck` | 0 | `PRECHECK_SUMMARY|checks=14|failures=0` |
| 10 | `powershell -File field/M8R3Field.ps1 -RunField -Transport stub -Backend sim -Profile all` | 0 | `RUNFIELD_SUMMARY|checks=3|failures=0|actions_attempted=0`; healthy/trip/no_trigger 三条 dispatch 各 rc=0 |
| 11 | `powershell -File <仓外> ff_ps_ast_check.ps1`(Parser + 参数 AST) | 0 | `PARSE_ERRORS=0`、`PARAM=Profile VALIDATE_SET=healthy,trip,no_trigger,all,response_driven` |

新 run 目录(原产物未覆盖): `offline/psrun-20261031T175241Z-48804-SelfTest`、
`offline/psrun-20261031T175308Z-37156-Precheck`、`offline/psrun-20261031T175313Z-16908-RunField`。

候选路径里可核对的新事实(stub 实跑证据): `S_S8/04_log_before.rx.bin.txt` 是
`slots=300/4096 valid=300 bad=0`；`S_S9/ledger.jsonl` 记 `NEW_LOG_WINDOW
{before:300, after:600, first:592, count:8}`；`S_S9/05_frames.rx.bin.txt` 的槽位是 592..599
且 `ax_mg=-23` 被收下；`S_S3/01_rst.boot.rx.bin.txt` 只有原字节的 repr，没有 READY 结论。
容量 4096 与产品一致(`ns_storage.h:10` `NS_LOG_SIZE 0x100000u` / `:15` `NS_LOG_SLOTS = /256u`)。

**本机核对发现的偏差(未改候选字节，只登记)**: `rows.json` 两处 `flash_logstat` 系来源锚点写
`applications/ns_flash_shell.c:100-110`，实读产品源码 `flash_logstat` 函数体是 :89-99，两条
`rt_kprintf` 在 :94-95 与 :96-98，而 :100 是 `MSH_CMD_EXPORT` 行 —— 行号偏了但形状与内容正确，
`host/rows_provenance_check.py` 按窗口命中所以 problems=0。候选散列已绑定，本轮不动这些字节;
准确的锚点应写作 `ns_flash_shell.c:89-99`。

硬件动作: 无。未打开 COM5，未创建 DAP/串口对象，未调用 pyOCD/Keil，未复位/下载/起转。

### 7.1 本轮自己的失误(发现即登记, 不靠工具掩盖)

`e1778ed` 误把 `field/__pycache__/` 的两件 `.pyc`(本机 import 副产物)随目录级 `git add -f`
带进索引: 先前已 `git restore --staged` 撤过一次, 但随后为写前置件又执行了一次目录级 `git add -f`,
把两条又拉回来了; 当时的范围检查只过滤了"是否在 reports/ 之外", 没复核 `__pycache__`。
发现途径 = 推送后的远端逐 blob 回读把 in-r3-paths 未登记项从 5 条披露成 7 条。
处置: `git rm --cached` + 删磁盘(可从 `.py` 重建), 清单条目数因此不变(规则本就排除),
在 `remote_readback.md` §7 与这里各留一条原文记录。教训: 目录级 `-f` 之后必须按
`git diff --cached --name-only | grep __pycache__` 复核, 不能只看顶层目录。

## 8. 现场轮(FIELD-1, 2026-11-01)的首次 real dispatch —— 结果停在 S2

批准范围(操作者原话口径): 绑定交付头 `1dc4230` 与候选 BIN `00608ec9…e304`；下载与定长回读各一次、
学习起转 ≤15 s、保护观察 ≤5 s、最多两次 start 合计 ≤20 s、IRUN20/100 Hz/DIR0，其余复位/串口/配置/
清理按 plan；操作者在场才进运动；失败即停业务并做必要清理；不加重试、不主动碰撞、不清日志。
令牌 `M8-R3-1DC4230-20261101`（两个参数同串，只是本包标识）。UID 由本机历史成功烧录记录只读找回：
`D:\ns800-m7-p0-20261030T182818\dap_uid_bare.txt`（len=48 / sha256 前 12 `d2f530dbd062`，
与 `D:\ns800-r1-board`、`D:\ns800-r2-board`、`D:\ns800-board-a` 的 5 件命令记录互证为同一把 DAP），
原值不入仓库、不入对话，未枚举探针。

执行方式：可见 PowerShell 新窗口（PID 20080，run 目录
`offline/psrun-20261031T185843Z-20080-RunField`），人工门读该窗口 stdin。启动前只读 Precheck
14 项 0 失败（`offline/psrun-20261031T184556Z-55376-Precheck`）。

| 阶段 | 实际结果 | 额度 |
| --- | --- | --- |
| S0 离线绑定 | OK：按路径重算 BIN 290916 B / `00608ec9…e304` 与 build_manifest 逐字段相等；两提交实存 | — |
| S1 唯一一次下载 | **rc=0**，24.14 s；`Erasing… [loader]` / `Programming… [loader]` / `Erased 291840 bytes (285 sectors), programmed 291840 bytes (285 pages), identical 0 bytes (0 pages) at 13.26 kB/s` 三条齐 | download 1/1 已用 |
| S2 唯一一次定长回读 | **STOP `ACTION_NONZERO_RC` rc=1**，11.4 s；`readback.bin` 未生成 | readback 1/1 已用 |
| S3–S11 | 全部 NOT_RUN | port_open 0/8、explicit_reset 0/1、motor_run 0/2、启动秒 0/20、config_save/apply 0、清理 0、approved_reboot 0/1 |

人工门弹出次数 0；未开口、未复位、未起转、未写配置。因此本轮**既不能记 NO_TRIGGER 也不能记触发**，
只能记 `NONE_OBSERVED(业务未走到观察窗)`。镜像已写入 Flash，但**在板逐字节一致 = UNPROVEN**
（回读没落盘），核在工具最后打出的状态是 `Core 0 (None):  Halted`，而计划里的断电重启门 S6 未走到，
所以"板子是否在跑新固件"记 NOT_ASSESSED，恢复需要操作者断电再上电（我不代替操作者动电源）。

根因（实证）：argv 层这轮是对的 —— 传给 pyOCD 的 token 是完整单串
`savemem 0x08000000 0x47064 D:\ns800\...\readback.bin`（F2 修正生效：不再被前缀 repo_root；
长度 `0x47064` = 290916 也对）。缺陷在下一层：**pyOCD commander 把 `-c` 串按 shlex 解析，
Windows 路径里的反斜杠被当转义吃掉**，于是目标文件变成
`D:ns800reportsreviewsphase8-m8-relative-protection-r3…readback.bin`，报
`[Errno 2] No such file or directory`（stderr 原文见 `host/field_run_r3field1_rootcause.txt`）。
离线工装为什么抓不到：假子进程从不真正解析 `-c` 字符串，FS57/TB05 只核 argv 有没有被改写；
这是首次真跑才暴露的形状缺陷。

顺带登记一条可观测性缺口：整包因阶段失败而 STOP 时 `DISPATCH_SUMMARY|status=STOP|code=|` 的 code
是空串，具名 code 只出现在阶段行（`ACTION_NONZERO_RC`）。PS 判据仍按 status/not_run 正确落败
（`failures=["DISPATCH_response_driven"]`），但摘要行不具名。
另一条正面事实：`runfield_summary.json` 的 `actions_attempted` 确实是
`["download:real_tool", "readback:real_tool"]` —— 兑现"真实摘要不得再固定写 hardware_actions=[]"。

本轮**不做**：不发第二次回读、不重下（那是新批准范围）、不动用剩余那一次复位去"救"。
下一步最小修法（属候选五件之一，需新一轮绑定与批准）：`plan.json` 的 readback argv 把 savemem
目标路径写成**正斜杠**形式（Windows API 接受，且 `-c` 串内不再有任何可被去转义的反斜杠）。
板子已经写好，续测只需 1 次回读 + 从 S3 起的剩余额度，不需要第二次下载。

证据：`host/field_run_r3field1.json`（批准绑定/额度/根因/结论标签）、
`host/field_run_r3field1_rootcause.txt`（决定性原文）、
`host/field_run_r3field1_artifacts.json`（24 件逐字节身份 + 重发包身份：`.md` 与已归档
`host/reviewer_ff/README.md` 逐字节相同，说明重发的是同一份任务书，无新增指令；
zip `4e2bf97e…287fe` / 39796 B）。失败件一律原样保留，不删不改。

## 9. 续测轮(M8-S2, 2026-11-01)执行前的绑定与门重跑

现场首次真跑把回读打停之后, 本轮只做一件事: 把那个**只有真跑才会暴露**的形状缺陷修在最里侧,
并把"不重复下载"写成一份可核对的续测计划, 然后在设备上继续。

- 缺陷 F6 的修正在 `field/m8r3_serial_chain.py` 两行(`@RUN_ROOT` 与 `payload["run_root"]` 改
  `out.as_posix()`)。依据是现场实证的机制(commander 对 `-c` 串再分词一次, `\` 在那里是转义符),
  以及既存成功原文里 savemem 目标**本来就是正斜杠**
  (`phase8-motor-m7-p0-board/S2_readback.stdout.txt`: `Saved 266244 bytes to D:/ns800-m7-p0-.../readback.bin`)。
  `plan.json` 的 argv 一个字没动 —— 它写的就是 `@RUN_ROOT/readback.bin`, 错的是展开形式。
- 新增 FS74/FS75 各一项(FS74 走**真 dispatch 的 subs 构造**, 假 pyOCD 会像真的一样分词并落盘;
  FS75 把两处退回 `str(out)`, FS74 必须转为失败 —— 实测 `s2=STOP / backslash_in_c=1 / 文件写不下去`)。
  套件从 73 项变 75 项, `negative_injections` 从 11 变 12, 往轮 25 个身份原序保留。
- 续测计划 `field/plan_continuation_s2.json` 由 `plan.json` 程序化派生: **只**从 stages 里移除 S1,
  并在 `_continuation_s2.carried_in` 里绑定上轮下载的证据(commit `0c96e12`、run 根、
  `action_download/*` 逐件散列、rc=0)。`download` 额度仍申报 1, 本轮 used 必须为 0。
- 本机门(原文在 `host/*_m8s2.*`): 75/75 rc=0; 审核边界探针同一程序同一输入 22/22 rc=0;
  `--validate-plan` 对 `plan.json` 154/0、对续测计划 153/0; rows 来源 41 组 problems=0;
  PowerShell 5.1 三入口各新目录: SelfTest 22/0、Precheck 14/0、RunField(stub/sim/all) 3/0
  且 `actions_attempted=0`。
- 上一轮那次单独串口调用(0 字节回来)的证据 `offline/serialprobe-20261031T193341Z-SERIAL1/`
  随本提交入库(原始件 0 B, sha=`e3b0c442…`, 台账 6 行), 与"操作者口述已断电再上电"分开登记。
- 本轮提交**不含任何设备动作**: 产品 C/H、Keil、六镜像/三 map、固件 manifest 未改;
  `plan.json`/`rows.json`/`M8R3Field.ps1`/`needles.json` 与绑定基线 `0c96e12` 逐字节相同
  (逐条核过, 见 `host/m8s2_identity.json` 的 `unchanged_bound_files`)。

## 10. 续测轮(M8-S2)在设备上的实际结果

绑定提交 `d9ff664`（远端逐 blob 回读 5079/5079 problems=0）之后，用
`field/plan_continuation_s2.json`（S1 已移除）在可见窗口跑唯一一次续测 dispatch，
run 根 `offline/fieldcont-d9ff664-20261031T200214Z-41860`。

| 阶段 | 实际 |
| --- | --- |
| S0 离线绑定 | OK：镜像按路径重算 290916 B / `00608ec9…e304` 与 manifest 逐字段相等 |
| S2 定长回读 | **rc=0，11.578 s**；`readback.bin` 290916 B、sha256 与候选镜像**逐字节相等** |
| S3 串口会话 | 开口 OK → 唯一一次 `explicit_reset` rc=0（`pyocd reset --method sysresetreq`，2.391 s）→ 被动收到 **1027 B 启动流** → 首条 `system_status` 在 6 s 步窗内**一个字节都没有** → `MISSING_RESPONSE_PROMPT` 停止 |
| S4–S11 | NOT_RUN；人工门一次都没弹；没有 `motor_start`，义务未建立，因此无需撤能清理；端口已关 |

额度真实消耗：`port_open 1/8`、`explicit_reset 1/1`、`readback 1/1`；`download` 申报 1 而**用了 0**
（S1 不在本轮 stages 里，这正是续测计划要证的事）。电机/配置/清日志/碰撞激励全 0。

这一轮把两件事同时钉住、又把第三件事暴露出来：

1. **`00608ec9…` 这枚候选镜像确实在板上**，是定长回读逐字节比出来的，不再是"下载 rc=0"的推定。
   上一轮 UNPROVEN 的那条已经翻成 PROVEN，而且直接确认了路径修正对真工具有效（不是假子进程的自证）。
2. F1 的"启动流不要求自发提示符"这轮真用上了：1027 B 是**原字节**收进证据的（sha 见
   `host/field_run_m8s2.json`），修之前这里会直接 `POST_RESET_BOOT_PROMPT_MISSING` 什么都拿不到。
   拿到的启动流末端停在 `[FLASH] scanning 1 MiB event log...` 一行。
3. 新暴露的现场事实：复位后控制台在这 6 s 里不回答输入。**"日志扫描仍在进行/卡住"与"RX 通路问题"
   两个可能都没排除**，不择一采信；配合上一轮"上电约 10 分钟后同样 0 字节"这条观察，
   目前只能记 `console_interactive=未证得`。要分辨它需要再一次串口动作（纯被动观察窗，
   不发命令不复位），或在计划层面放宽首查窗口后重新绑定 —— 两者都超出本轮批准范围，我停在这里。

本轮之后剩余可用额度：`port_open 7/8`、`approved_reboot 1/1`、`motor_run 2/2`、
`config_save/tmc_config_apply/diag_profile/*` 均未动；**`readback` 与 `explicit_reset` 已用满**。

## 11. 一次串口诊断(加强版单次会话)的实际结果

授权原文口径：COM5 只打开一次 → 先被动保存原始字节 **60 s**(不以"自发出现提示符"作前置) →
至多一次只读 `version`，缺回复就停、不重发 → 仅当 `version` 完整返回才发至多一次
`system_status`；全程**零 DAP、零下载、零 Flash 回读、零主动复位、零配置写入、零电机动作**，
结束关口不自动接起转；复用现有采集路径，不写新采集器；两条查询各 15 s 步窗(执行器另有 3 s 传输裕量)；
本轮只判断"收到什么"，不登记任何保护业务通过。

执行：可见 PowerShell 窗口(PID 48972)，run 根
`offline/diag-com5-20261031T234328Z-48972`，台账 7 行 `sess_diag/ledger.jsonl`。

| 步 | 实际 | 依据(台账原文) |
| --- | --- | --- |
| 开口 | OK，COM5@115200，一次 | `PORT_OPEN_ATTEMPT` / `PORT_OPEN`(`transport=real`) |
| 60 s 被动 | **rx_bytes=0**，原始件 0 B(sha `e3b0c442…`) | `WATCH_DONE seconds=60 rx_bytes=0 ended_by_budget=false` |
| `version` 一次 | **0 字节回复，连回显都没有** → 按合同停止 | `RX_DONE bytes=0 prompt_seen=false` + `SESSION_STOP MISSING_RESPONSE_PROMPT as=ver bytes=0` |
| `system_status` | **未发送**(前一步未完整返回，合同规定缺回复即停) | 同上，且台账无该步任何记录 |
| 关口 | 端口已关；`disarm=NOT_ARMED`、`obligation=false`、`cleanup_errors=[]` | 摘要行 `"disarm": "NOT_ARMED", "obligation": false` |

登记的口径差别(不掩盖)：
- 台账里 `quota_left.port_open=7` 是**本次调用重新按 plan 实例化**的结果，不是跨轮累计。跨轮真实
  累计只能按"每次调用各 1 次开口"计：FIELD-1 0 次、M8-S2 1 次、单点手探 1 次、本轮 1 次。
- 摘要里的 `retry: 0` / `physical_collision: 0` / `blackbox_clear: 0` 与 `dmm_readings: 0` 是 plan 的
  申报额度，本轮**未动用**；`terminal=KNOWN` 指停止原因是具名 code，不是指板端状态已知。
- 本轮**没有**得到任何关于"板子在跑什么"的结论：`RX_DONE bytes=0` 只说明这 15 s 内该端口没收到字节。
  失败件(0 B 原始件)一律原样入库，不删不改(提交 `5f45a90`)。

## 12. 第 0 条：stage-5 路径的源码审计(零设备、零构建、零产品改动)

问题只有一个：**这条启动路径上是否存在能永停的条件**。产物：
`host/m8diag_source_audit.txt`(判词) + `host/m8diag_source_audit_check.py`(机器复算) +
`host/m8diag_source_audit_console.txt`(真实输出) + `host/m8diag_spi_h_excerpt.txt`(仓库外件的搬运)。

| 判据 | 实际 | 等级 |
| --- | --- | --- |
| 引用行逐条核对 | 52 条全部命中(`CITE_TEXT_MISMATCH` 0) | `AUDIT_PROBLEMS=0` |
| 观察件字节核对 | 9 条全部命中(1027 B 末端正是扫描行、三个 0 B 窗、历史回显件) | 同上 |
| 检查器元负控 | `NEGATIVE_CONTROL fired=3/3`(错位/错串/缺件三种都会失败) | 同上 |

判词摘要：**存在**一个无界限、不让出、无错误返回的等待(厂商 `spi.h:479-481`，
`SPI_transmitReceive` 的 RX 空循环)，且它在 main 线程(prio 10)上跑，正好饿死 finsh(prio 20) —— 与
"扫描行之后再无一个字节、`version` 无回显、60 s 内无第二次 banner"三条观察同形；应用与 board 目录
**无任何看门狗引用**，所以真在自旋也不会自恢复。同时明确 **NOT_PROVEN**：板子此刻是否就在该循环里，
§6 保留 H1/H2/H3 三条并存解释，不择一采信。另收回一句上一轮的错话：
"按 1 MHz 算约 8.5 s 必然扫完"是**吞吐估算**，不是界限，不能用来排除"卡在阶段5"。

## 13. (b) 一次 DAP 只读核状态的实际结果

授权：操作者选定 (b)，并在执行前对现场三项逐条确认（板子自上一轮上电后未断电、DAP 仍连着板子与
USB、COM5 无其他程序占用）。范围：一次 attach + 一次 `status`，零 halt、零复位、零 Flash 读写、
零下载、零配置写、零电机动作。

| 次 | 结果 | 是否碰到目标 |
| --- | --- | --- |
| 1 `offline/dapstatus-20261101T031548Z-9916` | rc=2，`error: unrecognized arguments: --core 0`（1.6 s） | **没有**：参数在 argparse 层就被拒，未枚举探针、未连调试端口（stderr 只有 usage 两行）。这是我方 argv 形状缺陷（把 `reset` 子命令的 `--core 0` 抄进了 `commander`），原样保留不删 |
| 2 `offline/dapstatus-20261101T031618Z-44304` | **rc=0，2.25 s**；stdout 25 B = `Core 0 (None):  Running`，stderr 0 B | 是，只读一次核状态 |

为什么这条读数是板子自己的状态、而不是工具留下的痕迹（两处离线核实，已并入检查器机器断言）：
- pyOCD 0.45.1 的 `connect_mode` 取值 `('halt','pre-reset','under-reset','attach')` 里**默认是 `halt`**
  （`site-packages/pyocd/core/options.py:56`）。所以既往 FIELD-1 看到的 `Core 0 (None): Halted`
  **不是板端证据**，而是 `field/plan.json:tools.readback.argv` 显式传 `-O connect_mode=halt` 的结果
  （检查项 `plan-readback-halts-on-connect`）；本次两遍都传 `--connect attach` + `-O connect_mode=attach`。
- pyOCD 0.45.1 **没有** `status` 子命令（顶层只有 commander/cmd/erase/load/flash/gdbserver/gdb/json/
  list/pack/reset/server/rtt/run），核状态只能经 commander 内建 `status`（`commands/commands.py:104-119`，
  别名 `st`），它读 `core.get_state()`（`:118`）而不发 halt；实测文本与该文件 `:119` 的格式串
  `f"Core {i} ({core.node_name}):  {state_desc}"` 逐字符一致（两个空格、`node_name` 为 `None`）；
  `-O resume_on_disconnect=true` 因核本就未停而是空操作。

判读（不越界一步）：
- **H2（核未在跑）在这次测量覆盖的那个当下状态上被排除**；同时那条既往 `Halted` 观察的机制已定位到工具参数。
- **H1（卡在该无界自旋）得到加强但仍未证**：坐实它要读 PC，而读 PC 必须 halt + 读寄存器 = 一次会改变
  板子状态的新动作，未获批准，我没做。
- **H3（主机→板 RX 不通）未被单独排除，但它自己解释不了"输出冻结在扫描行"**：同一会话 TX 方向已收到
  1027 B，若启动真越过阶段5，`supervisor.c:463` 的完成行必然出现在 TX 上；而其后三个窗口共 0 字节。
- 当前成立的事实合起来是：**核在跑 + 输出停在 `[FLASH] scanning 1 MiB event log...` + 输入无回显 +
  无看门狗复位** —— 与"主线程在一个不让出的循环里占住 CPU"一致，与"核停了"不一致。

额度与纪律：本动作是计划外的只读探针（计划里没有 `status` 这类动作类型），不消耗申报额度；
第 1 次失败不算"重试设备"，它是我方参数形状缺陷，修正后才是唯一一次对目标的动作。累计真实设备动作：
下载 1、回读 2（1 败 1 成，后者逐字节相等）、显式复位 1、开口 3、本次 attach 1。
登记件：`host/dap_status_probe.json`；检查器复算输出 `host/m8diag_source_audit_console_after_dap.txt`
（52 条引用 + 11 条观察断言 + 3 条元负控 ⇒ `AUDIT_PROBLEMS=0`）。

## 14. (c) 一次 halt 定位的实际结果：不是 SPI 自旋，是**硬fault 死循环**

授权：操作者选定 (c)，范围一次 attach、**恰好一次 halt**、读通用/系统寄存器 + `CFSR/HFSR` 等，
结束保持 Halted；零复位、零 Flash 读写、零 savemem、零串口、零配置写、零电机。选核用 commander
内建 `core 0`（上一轮已证 `commander` 没有 `--core` 选项）。run 根
`offline/daphalt-20261101T052450Z-22884`，rc=0，6.469 s，stdout 1205 B，stderr 0 B。

| 采样 | 实测（全部由 `host/dap_halt_check.py` 按原字节断言） |
| --- | --- |
| halt 前 | `Core 0 (None):  Running`（与上一轮独立一致） |
| halt | `Successfully halted device` —— 整场只有这一次 |
| PC / LR | `pc: 0x0801e8a6` / `lr: 0x08008201` |
| xPSR / ICSR | `0x61000003` 与 `0x0440f803` —— 两处独立给出 **IPSR/VECTACTIVE = 3 = HardFault** |
| SP / MSP / PSP | `0x2000d3dc` / `0x2000d3dc` / `0x20012748`，CONTROL=0 |
| PRIMASK / BASEPRI / FAULTMASK | `1 / 0 / 0` —— 核心级中断被屏蔽 |
| CFSR / HFSR / DFSR / MMAR / BFAR | `0x00008200` / `0x40000000` / `0x00000001` / `0x00bd80b0` / `0x00bd80b0` |
| SysTick CSR / RVR / CVR | `0x00010007` / `0x00061a7f` / `0x00014563` |

定位（离线，用**已接受 AXF**，零目标内存读取）：`fromelf -c -a rt-thread-motor-m1.axf` 全文 116682 行，
解析器只认文本里真实出现过的标签；三段切片入库在 `host/dap_halt_disasm_window.txt`。

- `0x0801e8a6` = `e7fe` = `B .` 自跳转，属 `rt_hw_hard_fault_exception + 398`，源码就是
  [cpuport.c:460](file:///D:/ns800/rt-thread-master/rt-thread-master/libcpu/arm/cortex-m7/cpuport.c#L460)
  的 `while (1);`，紧邻其前一行 `:457` 正是 `hard_fault_track();`；
- `lr` 落在 `bus_fault_track + 232`，该函数在 `0x080081ea` 处 `MOV r0,#0xed38 / MOVT r0,#0xe000`
  读 `0xE000ED38`（`SCB_BFAR`，`cpuport.c:224`）——即处理程序已走到"打印 BFAR"那一支；
- 采样时 `r2 = ns800_putc`、`r12 = rt_serial_write`，说明处理程序正在**轮询式往外打印**。

判读（四条实测结论，各自独立）：
1. **这次采样不在 SPI 的 RX 自旋里**，PC 在硬fault 死循环。`spi.h:479-481` 那条无界等待仍是"源码层存在
   的设计隐患"，本测量**不**构成"板子卡在那里"的证据；也不能排除它在别的启动实例里发生过（一次采样只给一个瞬间）。
2. **精确总线错**：`BFSR=0x82` = bit1 `PRECISERR` + bit7 `BFARVALID`，`MFSR=0`、`UFSR=0` —— 只有一种 fault；
   `HFSR=0x40000000` 只有 `FORCED`（`VECTTBL=0`、`DEBUGEVT=0` ⇒ **不是我们这次 halt 造成的**）。
   出错访问地址 `BFAR = 0x00bd80b0`，在本芯片上不存在的地址。
3. **心跳已启动且在数**：SysTick `CSR=0x00010007`（ENABLE|TICKINT|CLKSOURCE，且 `COUNTFLAG=1`）、
   `RVR=399999` ⇒ 配 `RT_TICK_PER_SECOND=1000` 反推 SysTick 时钟 400 MHz。**"板载心跳尚未启动"这一支被排除。**
4. **控制台彻底哑的机制**现在有了直接解释：`PRIMASK=1` ⇒ 调度与 UART 接收中断都没人处理，
   而处理程序自己用轮询打印（`ns800_putc`/`rt_serial_write` 在册）。
   所以 §13 里并存的 H1/H2/H3 被第四条实测事实替代：不是 SPI 自旋、不是核停了、也不是 RX 方向坏，
   而是**固件自己进了硬fault 死循环**。

可观测性缺口（登记，不掩盖）：处理程序的 dump 是一次性输出，`cpuport.c:420-436` 会把
`psr/r00..r12/lr/pc` 全部打出来；它发生在 M8-S2 那次复位后的捕获窗（约 6 s）关闭之后、60 s 被动窗之前，
所以两头都没收到。当时若把复位后的被动窗开到覆盖"扫描 + 若干秒"，这段 dump 就会成为根因证据。

还差一步才能"针对性修复"：真正的出错位置在**异常栈帧**里（处理程序已把它打印过）。
切片 1 的 `0x0801e886` 说明帧指针 = `[当前 sp + 0xc] = [0x2000d3e8]`。要取它需要在**已经 halt** 的板上
再读约 12 个目标 RAM 字（零第二次 halt、零复位、零写入）——这超出本次批准范围（批准只覆盖寄存器与
`CFSR/HFSR`），所以我停在这里等一次新批准。板子现状：保持 Halted。

## 14.1 勘误（随本轮生效）：`resume_on_disconnect=true` **不是**空操作

§13 里我写了"`-O resume_on_disconnect=true` 因核本就未停而是空操作"，这句**错**。
`pyocd/coresight/cortex_m.py:413-433`（`disconnect(resume=True)` 分支）在断开时无论核此前是否在跑，
都会执行 `write32(DHCSR, DBGKEY|0x0000)`（清掉 `C_DEBUGEN`）与 `write32(DEMCR, 0)`（清 `TRCENA`）；
其中 `self.resume()` 那一步才因"核未 halt"而空转。所以那次探针**确实改过目标状态**（关掉了我方刚启用的
调试逻辑与 DEMCR）。正确口径：**零显式 halt、零复位，但不是"完全没改变目标状态"**。
(c) 轮已按此把该选项改为 `false`（本轮要求"结束保持暂停"，断开时不该恢复/清调试）。

