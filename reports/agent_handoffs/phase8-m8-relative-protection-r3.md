# 交接：NS800 M8-R3 相对保护集中返修轮（离线轮，零设备动作）

- 日期：本机 2026-10-31（权威时间戳见同名 JSON 的 `date_utc`）
- 任务书：`NS800_M8_R3_Concentrated_Repair_Task_20261031.md` + `NS800_M8_R3_Review_Repair_Package_20261031.zip`
  （操作者本机文件，不记录个人绝对路径），任务号 `phase8-m8-relative-protection-r3`
- 入审结论：**`M8_R2_REVIEW_REWORK_REQUIRED。不进入现场。`** → 本轮集中返修，不发软件 GO、不发镜像 GO、不新增纯 GO 登记轮
- 审核裁定（原样保留，不改写）：
  `M8_R2_REVIEW = REWORK_REQUIRED`、`R2_CURRENT_MANIFEST = 173/173_MATCHED`、
  `R2_ORIGINAL_C_CHAIN = 49/49 + RV1..RV9_META_OK`、`R2_ORIGINAL_FIELD_SELFTEST = 25/25 (remaining boundaries reproduced separately)`、
  `REVIEWED_SMALL_PRODUCT_PATCH = SAME_INPUT_FIXED + 49/49 + RV1..RV9_META_OK`、`FIELD_REAL_EXECUTION_CHAIN = REWORK_REQUIRED`、
  `HARDWARE_ACTIONS_THIS_REVIEW = 0`、`NO_HARDWARE_GO`
- 工作分支：`feature/m8-sg-adc-relative-protection`（document_base `5c549f2af0740425c4014f374d064f77ee62d6b4`，直接续作，
  无 reset / rebase / merge / force；本轮只在原分支追加提交）
- 源码与验证链提交（`code_commit` / `software_input_commit` / `tested_image_build_commit`）：
  `805c29bd73c4ea861c9b9c65f61e946aec9548e3`（三目标各一次 Rebuild All 取自该提交的工作树；
  构建时 `git status --porcelain` 对已跟踪文件为空，之后的提交只新增 `reports/**` 与 `.gitattributes` 的本轮窄规则）
- 设备动作：**无**。`hardware_actions = []`；未创建 DAP/串口对象，未下载、未读 Flash、未复位、未开串口、
  未起转、未写配置、未触发故障、未制造碰撞、未改供电/线路/SW1/ENN。pyserial 只在从未走到的 real 分支里 import。
- 本轮状态：`M8_R3_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable = false` 保持，`go_requested = false`。
- 交付 tip 不写在本文件里：本轮的证据提交之后，由 `host/remote_readback.py` 从远端 blob 独立复算，
  结果记在 `reports/reviews/phase8-m8-relative-protection-r3/remote_readback.md`（该文件由推送后的回读产出）。

## 1. R3 条目是否按合同做到（逐条落点与可检判据见 `run_record.md` §3）

| 要求 | 一句话结论 | 判据 |
|---|---|---|
| R3-A 产品边界：过期的 IDLE 快照不得再豁免覆盖丢失保护 | `motion_trusted = in->motion_known && diag_rel_tick_diff(now, in->motion_tick) <= motion_age_limit`，`motion_age_limit` 取 `p->age_frame_ms`（20..1000 内）否则 100；过期即并入有界降险，不再"状态是 IDLE 就当没丢" | `tests/host/rel_main.c` 的 `REL50`（8 支：新鲜/恰好等限/超 1 tick/零龄/未落时间基/非 IDLE…）+ 反向 `RV10_motion_age_dropped_from_trust` **只摘年龄项**（不删整个覆盖保护），命中 `REL50` 与 `REL49` 两个身份；原 49 身份与 RV1…RV9 一字未改 |
| R3-B1 `motor_start` 写入报错不得跳过撤能 | 运动义务与硬截止起点在 `port.write` **之前**建立；写失败也走 finally 清理，且 `success` 与 `disarm==CONFIRMED_DISABLED` 绑定 | `FS26` + 定向检出 `FS54`（把义务推回写之后 ⇒ `FS26` 必须转 FAIL） |
| R3-B2 写耗时进预算、5 s 硬截止真约束业务动作 | `MotionBudget` 从"尝试之前"取起点；`hard = t0 + cap` 之后仍约束业务步 | `FS10/FS27` + 定向检出 `FS23`（把 `hard` 改成"再给一点" ⇒ `FS10` 必须转 FAIL） |
| R3-B3 `motor_stop` 无应答不得饿死 `motor_disarm` | `cleanup()` 里三条命令各 1.2 s **独立**确认窗，到硬截止是 `continue` 不是 `break`；每条按 `cleanup_<cmd>` 真扣预留 | `FS28/FS34/FS41/FS52`；实测时刻 `S_S4 5.005→5.015`、`S_S8 5.010→5.020`（观察窗跑满 5 s 后三条仍各自发出并确认） |
| R3-B4 运动窗内不许出现阻塞式人工步骤 | `plan_motion_window_violations()` 在**开口之前**拒绝，零发送 | `FS29`（`writes=[]` + `PLAN_MANUAL_AFTER_MOTION`）；本轮所有 manual 步骤都排在 arm/start 之前 |
| R3-B5 禁能判据必须与真实固件字段一致 | `disable_contract()` 要求**同一条响应**里 `state/drv_request/ENN_fb/verdict/stop result/diag_off` 六字段齐备，许可状态取 `(FAULT, OTHER)`（真实产品只有这两个 + 运动中态，不存在 `IDLE`）；`drv_en_request` 只作历史披露、`note: … 非波形证明` 不得当物理证明 | `FS30/FS31/FS32/FS34/FS35/FS36` + 定向检出 `FS53`（退回"只认 IDLE" ⇒ `FS30` 必须转 FAIL） |
| R3-C 整包贯通（一次批准走到该走的位置） | `dispatch_plan()` 单入口按 `stages` 调度，成功阶段只记检查点、失败才停；`NOT_RUN` 一律明列且**绝不计入**已执行阶段数；已执行序列还要与按 `profiles` 过滤后的期望序列逐个相等 | healthy `run=9 not_run=0`、no_trigger `run=9 not_run=0`、trip `run=12 not_run=0`（全 rc=0）；`FS42..FS45` |
| R3-C 提交/保存与重启恢复比较**真被执行** | `commit OK` / `config_save` 在静止会话 S5 内发生；S7 用 `record_from` + `compare_recorded` 与**本轮**记录值逐项比较（记录 `id=7 rev=1 flags=0010 sig=44100 match=YES`）；漂移即 `RESTORE_MISMATCH` | `FS46`（漂移具名停止）/`FS47`（事故 session 不增大即 `SESSION_NOT_INCREASED`）；实测 `bb_session 0 -> 8`、`bb_written 0 -> 612` |
| R3-C 未触发 / 真实触发两条分支 | `OBSERVATION_NO_TRIGGER` 只作为"无异常观察"的结论、不冒充触发；trip 分支 S9 取证、S10 锁存态 `motor_arm` 具名拒绝（计划申报 `expected_terminal=["REFUSED"]`，会话到此结束）、S11 人工恢复且 `forbidden_commands` 含 `motor_start` | `FS43/FS44` + `plan.json:stages` 的 `profiles`/`expected_terminal`/`forbidden_commands`（禁令真能拒绝：`S0_PLAN_FORBIDDEN_COMMAND_PRESENT`） |
| R3-C 额度是"整包共用一本" | 单一 `quota_box` 贯穿所有阶段；`cleanup_<cmd>` 独立预留；结束逐键打印 `QUOTA_AUDIT|key|declared|used|left`，用了未申报的键或余额为负即 `QUOTA_AUDIT_FAIL` ⇒ 整体非成功 | `FS41` + 实测 `quota_used`（healthy `port_open=5 / motor_run=2 / cleanup 2/2/2`；trip 增到 `port_open=8 / cleanup 3/3/3`） |
| R3-V 最终验证与构建 | 原 C 链 **50/50**（`executed=50`，本机 `warnings=0`）、九旧 + 新新鲜度共 **10 家族 META-OK**、变更适用链 34/34 与 3/3、现场包 **55/55**（保留 R2 的 25 条身份 + 7 组 `NEGATIVE-INJECTION`）、PS 5.1 三入口 20/14/3 检查全 0 失败、三目标各一次 Rebuild All `0 Error(s), 0 Warning(s)` | `host/*.txt`、`build/*.log`、`build/rc.txt`、`offline/psrun-*` 三次运行原始件 |
| R3-D 逐清单回读 | 沿用 R2-D 已修好的**清单优先**判定（先把每张清单逐条去远端要回来），范围只换本轮四张清单；`plan.json` 也进 JSON 解析集合，使"计划申报的镜像 bytes/sha256"由远端字节复算 | `host/remote_readback.py`（本轮版）+ 推送后的 `remote_readback.md` / `host/remote_readback_console.txt` |

## 2. 产物位置

- 证据目录：`reports/reviews/phase8-m8-relative-protection-r3/`
  （`run_record.md`、`field_candidate_package.md`、`field/`（执行器 + 自检 + `plan.json`/`rows.json`/`needles.json` + `M8R3Field.ps1`）、
  `host/` 19 件原始输出与核对件（含 `remote_readback.py`、`rows_provenance_check.py` 与其控制台、
  `staged_blob_preflight.txt`、`offline_run_compare.txt`、`encoding_gate_negative_control.txt`，
  以及改名保留的 `field_*_console_superseded-cp936capture.txt` 三件）、`build/` 4 件、`offline/` 六个 PowerShell 运行目录、`SHA256SUMS.txt`）
- 镜像目录：`reports/firmware/phase8-m8-relative-protection-r3/`（6 枚镜像/AXF + 3 份 map + `build_manifest.json` + `SHA256SUMS.txt` 10 条）；
  R1/R2/M8 的镜像目录一字未动。
- 候选镜像：`rt-thread-motor-m1-candidate.bin` = 290,916 B，`sha256=00608ec9…79e304`；下载载荷 `rt-thread-motor-m1.axf` = 1,771,288 B，
  `sha256=0bba8ca7…550963`。两者由 S0 现场按路径重算并与 `build_manifest.json` 逐字段要求相等。
- 往轮事实：`reports/reviews/phase8-m8-relative-protection-r2/**` 与 `reports/firmware/phase8-m8-relative-protection-r2/**`
  逐字节未改；已接受的 173/173、历史 64 问题与 `no_path=32` 的披露保持原样，本轮不替往轮改写历史。
- `.gitattributes`：只追加本轮两个目录的 `-text` 窄规则（整轮目录一次写全，避免 R2 那种"提交后才逐条发现"的事后修补）；
  未加全局 `* -text`、未做全仓 renormalize、未改 `.gitignore`。

## 3. 四条必须说清的取证事实（不掩盖）

1. **离线绿色的边界**：现场包的全部结论（含 9/12/9 阶段、额度序列、清理时刻、恢复比较）都是在
   **假端口 + 假动作后端（SimClock / stub transport / SimActionBackend）** 上取得的。它证明的是**控制流**，
   不是设备行为。`RealActionBackend` 与 `ManualActionBackend` 本轮从未执行，其 argv、成功原文判据与交付物门只做了离线核对。
2. **`rows.json` 里有一部分不是现场观察**：`kind=source_shape` 或 `board_capture_composed` 的行
   （REL TRIP / REL LATCH / `[BB]` session 增量 / 锁存态 `motor_status` / 重启后的 `diag_profile show` 值）
   行形状取自产品 `rt_kprintf` 字面量、数值为下发或注入 ⇒ 只喂离线控制流。现场执行时**必须**由真实响应逐行替换，
   这一点写在 `field/rows.json` 的 `provenance` 字段和 `field_candidate_package.md` 的前置条件里，不在本轮"算已通过"。
3. **清单散列的字节基准**：`SHA256SUMS.txt` 的散列按本机磁盘字节计算，而 `core.autocrlf=true` 会在 `git add` 时剥 CR，
   所以清单不能自证。提交前置校验用 `git cat-file blob :<path>`（暂存区）逐条复算，与清单登记的散列逐条对齐；
   两份 `SHA256SUMS.txt` 都不哈希自身字节，`build_manifest.json` 由固件清单登记。回读程序只从**新建空 bare 仓**
   经 git 协议 fetch 一次，之后一律 `git cat-file blob <tip>:<path>`，不复用本地对象库或工作树。
4. **PS 5.1 的两个本机坑（本轮真实踩到并修）**：变量名大小写不敏感使局部 `$entry` 顶掉脚本级 `$script:Entry`，
   运行目录名被塞进整张镜像对象、`New-Item` 报"不支持给定路径的格式"；含中文脚本必须 UTF-8 BOM + LF。
   两处修正都写进 `M8R3Field.ps1` 的注释里，判据是三个入口在 5.1 上真跑过（20/14/3 检查、0 失败）。

## 4. 本轮"真跑"暴露的缺陷（不是设计时想到的）

| # | 缺陷 | 后果（如果不跑） | 处置 |
|---|---|---|---|
| 1 | `emit_text()` 定义了但从未接线（上一段补丁自称已接） | 判读文本 `.txt` 根本不落盘，`record_from`/`compare_recorded` 无数据可读，恢复比较是空的 | 接线并加 `EVIDENCE_WRITE_FAILED`（写失败即整体非成功）+ `FS39/FS40` |
| 2 | `assert_step` 先判"期望文本缺失"再判业务层 | 真实 `learn FAILED` 被报成 `EXPECTATION_NOT_MET`，业务失败身份被吞 | 业务层先判（`BUSINESS_FAILED`/`NAMED_REFUSAL`），`FS05/FS25` 仍成立 |
| 3 | `cleanup()` 的 `quota_box/overruns` 是死参数，从未扣减 | "清理必发"只写在文档里，额度实际不成立 | 按 `cleanup_<cmd>` 真扣、耗尽仍发出即 `CLEANUP_RESERVE_EXHAUSTED` 且不算成功（`FS41`） |
| 11 | 本轮新写的判据表照抄了旧草稿的 `FSxx` 编号 | 自检用例分批补入使编号从 `FS39` 起整体下移一位 ⇒ 表里凡 `FS39` 之后的引用都指向**别的用例**（最典型：`FS51` 实为"learn 撞 12 s 超时仍具名清理"，却被写成"清理退回共享截止"的反向判据）。测试抓不到它——错位只存在于文档 | 按 `m8r3_field_selftest.py` 的 `CASES` 表逐条回读、重算本文 §1 与 `run_record.md` §3/§5 的全部 `FSxx` 引用，两处均写明"编号以 `CASES` 表为准" |
| 12 | `rows.json` 的来源声明经不起机器核对（新增 `host/rows_provenance_check.py` 首跑即 3 problems + 4 notes） | 现场执行者按"来源可核"信任这些声明，而 `board_capture_composed` 这一级根本没写进 legend、`__watch__` 只给缩写路径、`diag_profile show@after_reboot` 写"同上"、`_provenance_no_trigger` 声称板级采集其实依据的是打印条件 | 逐项改为全仓相对路径 + 精确行段 + 正确等级，4 个无行号锚点补到 `file:line` ⇒ `problems=0 notes=0`。只动元数据，重跑 PS 三入口与 dispatch 结果不变（`host/offline_run_compare.txt`） |
| 13 | **交付归档里的中文是坏的**：`host/field_*_console.txt` 原先是 PS 进程 stdout 的直接重定向 | python 的 stdout 被 `Start-Process -RedirectStandardOutput` 落盘时按本机 cp936 编码，脚本再按 UTF-8 读回 ⇒ 带中文的行从一开始就是乱码/U+FFFD；"严格 UTF-8 可解码"掩盖了它，所以此前没有任何检查报出来 | ①`Invoke-Py` 钉 `PYTHONIOENCODING=utf-8` 并把"出现 U+FFFD"变成具名停止 `PYTHON_OUTPUT_NOT_UTF8`；②`Say` 逐行缓冲 + .NET UTF-8 写 `transcript_console.txt`，`host/` 三件改为它的字节副本；③反向自证：仓库外副本去掉那一行 ⇒ `-Precheck` rc=1 并命中 `PYTHON_OUTPUT_NOT_UTF8`；④旧三件改名保留 `*_superseded-cp936capture.txt`，未删除 |
| 14 | 修 12/13 期间重跑过 4 轮三入口，留下 6 个中间运行目录 | 交付里混进多份近重复运行，审核面翻倍且分不清哪份是准 | 先把**逐文件归一对照**写进 `host/offline_run_compare.txt`（RunField 344/344 全等；SelfTest 418/420，差的只是夹具临时目录随机名与只有权威版才有的 transcript），确认"重跑没改变任何判定"之后，按目录名逐个删除那 6 个从未提交（`git ls-files`=0）的中间目录；保留缺陷原件 3 个 + 权威 3 个 |
| 4 | 自检里仓库根层级少一层（`parents[2]` 应为 `parents[3]`） | `--dispatch` 一开场就 `S0_IMAGE_ABSENT` | 修正路径基准 |
| 5 | msh 命令名刮取把 `MSH_CMD_EXPORT` 的第二参（描述词）当命令名（得 45） | `--validate-plan` 的"每条命令都存在"检查形同虚设 | 修后 70 条真实命令，146 项检查才有意义 |
| 6 | 按 `--profile` 选行会误伤无关命令（trip 档案把 S4 的 `motor_arm` 也换成拒绝行） | 场景隔离靠隐式选行，健康分支被污染 | 场景差异改用显式行键 + `stages[].profiles`；`--profile` 只影响被动观察窗 |
| 7 | 观察窗吃满 5 s 后，同会话排在其后的业务步必然 `MOTION_BUDGET_EXHAUSTED` | "整包贯通"永远停在 S4，commit/save 从未被执行 | 提交/保存与事故取证拆到独立静止会话（S5/S9..S11），**不放宽截止点** |
| 8 | 阶段失败时次级 code 覆盖了主 code（`COMPARE_STEP_MISSING` 盖掉 `QUOTA_EXHAUSTED`） | 停止原因失真，现场无法归因 | 主 code 保留，比较结论另记 `record_code` |
| 9 | 一次 docstring 编辑自伤（少一个三引号）使执行器短暂不可解析 | 若未复验就是"交出去的是坏文件" | 修复后以 18/18 边界探针 + 55/55 自检 + 三档案 dispatch + PS 三入口复验 |
| 10 | 被取代的 5 个 `offline/psrun-*` 运行目录（3 空 + 2 共 771 文件） | 交付里混进已失效的运行 | 提交**前**删除并在此披露；交付只保留修复后的 093624 / 093638 / 093642 三次运行（都不是已发布件） |

## 5. 明确未验证 / 未完成（不得当成通过）

- 设备侧一切：下载、Flash 读、复位、串口通信、起转、`tmc_config_apply` 的真实效果、触发观察、清故障、
  重启后配置版本迁移是否真成立 ⇒ 本轮全部 **NOT_VERIFIED**（无一项有设备证据）。
- `RealActionBackend` / `ManualActionBackend` 的执行路径从未运行；工具参数只做离线核对（pyocd 0.45.1 的
  `--version` 与 `reset --help` 参数存在性）。
- `diag_profile learn` 撞产品自身 12 s 超时（> 本轮 5 s 运动硬截止）时的真实行为只按计划分支建模，
  风险登记在 `plan.json:residual_risk`。
- SG 在该工作点是否有可靠分离度、真实触发条件、IMU 现场激励、保护动作的物理效果与安全验收
  ⇒ 仍 `FIELD_UNVERIFIED` / `PENDING`；受控加载工具与作用点仍 `NAMED_PREREQUISITE_MISSING`，
  `impact_r6_excitation.selected=false`。
- 交付 tip 的远端逐 blob 回读尚未由本轮完成的部分，以 `remote_readback.md` 为准；本文件不预填其结论。

## 6. 下一步与授权边界

- 本轮按任务书只做离线工作：**未请求任何 GO**。软件 + 执行包**一次复审**（A/B 不拆成两轮）通过后，
  才向用户请求一次具体的硬件批准。
- 不继承任何既往现场额度，也不向用户索要本轮例行断电 / SW1 操作。
- 现场执行时的硬前置（写在 `field_candidate_package.md`）：`rows.json` 的 `source_shape/composed` 行逐行由真实响应替换；
  重启后的 profile 值与本轮 commit 记录实际比较；事故链按实际新增 session 取证；`--transport real` 必须带操作者令牌，
  否则 `REAL_TRANSPORT_REFUSED`（本轮作为负控真跑过）。
- R1 的 `B-CP4` / `R1-CP1` 等历史现场待批事项与本轮无关，保持原状态。
