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
## 7. 收尾轮(R3 复审 §2A–§2D + §3)对账

复审结论原样接受: `M8_R3_CORE_SOFTWARE_IMAGE_REVIEW_GO`(输入 `805c29b`, 限定离线合同)+
`M8_R3_FIELD_PACKAGE_REWORK_REQUIRED` + `NO HARDWARE AUTHORIZATION`。核心 C 与六镜像/三 map 本轮**未动**
(`build/`、`reports/firmware/...` 与镜像散列原样); 本轮只改现场包的 Python/PS/plan/rows/自检与文档。

| 复审条 | 缺陷(修前, 本机已复现) | 现在的落点 | 可检判据 |
| --- | --- | --- | --- |
| §2A | S3 先查询后复位 => Halted 核心不应答, `MISSING_RESPONSE_PROMPT`/resets=0 | `plan.json` S3 = 复位→消费启动流→首条查询; `drain_post_reset_boot()` 原字节落盘并等提示符; `validate_plan` 加 `PLAN_RESET_BEFORE_FIRST_QUERY` | FS56 / FS73; 审核探针修前断言不再成立 |
| §2A | Real 后端从未被真跑 | FS57 零设备注入(既存 P0 原文 + 真实镜像字节) | `host/reviewer_probe_post_fix_r3closeout.txt` 之外的 `python_selftest_r3closeout.txt` |
| §2A | 工具相对路径依赖启动目录 | `RealActionBackend(repo_root=...)`: `_p()` 以 repo_root 解析 argv 与镜像件, `subprocess(cwd=repo_root)` | FS57 断言 `cwd==repo_root` 且 axf 绝对 |
| §2B | stop 读异常穿出 cleanup: 没 disarm、没关端口 | `_cleanup_read` 读异常单独记账; `cleanup()` 每条命令独立 try + `continue`; `exec_session.finally` 再兜一层并仍关端口 | FS59 |
| §2B | 短写当成功; 开口额度开口后才扣; 禁能只核 name | `COMMAND_SHORT_WRITE`; `spend("port_open")` 先于尝试; 运行/静止判据用 `state_num` 数值 | FS60 / FS61 |
| §2B | expected_terminal 把清理未知/错也豁免 | `expected_refusal_ok` 合取门(terminal/cleanup_errors/overruns/disarm/needle 原文) | FS62 |
| §2B | RUNNING 收 "state=0 cur=0 tgt=100 armed=1 request=LOW pad=0" 为 OK | 数值 `state=MOTOR_CRUISE(2)` + cur>0 + drv_request HIGH/pad=1; 常量由 `PLAN_STATE_AND_FLAG_CONSTANTS` 回查 motor.h/diag_relative.h | FS66 |
| §2C | real+sim 被收为 OK; stub+real 无前置拒绝 | `ALLOWED_MODES` + `mode_gate()` 在 UID/工具/端口之前; PS `-Profile all` 真实入口拒 | FS63 / FS49 / PS `ST_NEG_MODE_MIX_REFUSED` / `ST_NEG_REAL_PROFILE_ALL` |
| §2C | real 预选夹具档案决定是否恢复 | `REAL_PROFILE=response_driven`, `decides_response_branch` 的阶段由本次响应决定分支 | FS68 |
| §2C | 真实摘要硬编码 `hardware_actions=[]/0`; 完整 argv(含 UID)落公开件 | `actions_attempted` 由后端实际尝试生成, PS 只累计; `ARGV.json` 脱敏 + 原 argv sha + UID 长度/sha12 | FS58 + `DISPATCH_SUMMARY\|actions_attempted=…` |
| §2D-1 | 两处 `live sig=44100 match=YES` 硬编码 | 只钉形状与产品自己的 `match=YES`; 数字由 `record_from`/`compare_recorded` 跨会话比较 | FS64 |
| §2D-2 | `flags=0010`(无 COMMITTED 位)当合法 | 夹具换成 `0005`, 判定改 `expect_flag_bits`(按 RPF_* 位, 含"未知位"门) | FS65 / FS70 |
| §2D-3 | 一条立即查询当稳定巡航 | `poll`+`poll_sequence`(先 ACCEL 后 CRUISE), 只在数值爬坡态重试 | FS66 / FS72 |
| §2D-4 | S9 只看计数, 没读回帧 | `flash_dump 0 8` + `require_frames`(14 列、session==本轮新增、含 event=9/step_hz=0、无 empty/error) | FS69 |
| §3 | 5 s 起转 × 12 s 同步学习不相容 | 离线新合同: 学习 15 s(S4)/保护 5 s(S8)按阶段传入, 总额 20 s 由 `PLAN_MOTION_CAP_BUDGET` 审计; 忙 msh 用真实 12000 ms 驱动假端口 | FS67 + `plan.quota_note_r3_closeout` |

**15 s 只是离线合同提案, 不是硬件授权, 也没有把旧的 5 秒测试记成通过** —— 将来一次具体板级批准必须
写明"学习 ≤15 s、保护观察 ≤5 s"。本轮同样未做: 不改产品命令、不实现可取消/有界学习接口。

## 8. 最终补正轮(FF, 2026-11-01)对账

任务书: `NS800_M8_R3_Field_Final_Fixes.md`(完整书，接替此前只给了四处的先行消息) +
同名 zip(14 件，按其 `SHA256SUMS` 逐件复算 0 不一致)。复审对象 `15745e5`，核心软件输入 `805c29b` 继续有效。

| 项 | 本机落点 | 判据/证据 |
| --- | --- | --- |
| F1 | `drain_post_reset_boot` 改有界被动存档(chain:1011-1046)，S3 首查新增 `state=READY/fault=0` 唯一判据 | TB01/TB02/TB03/TB04 + TB17(命令响应提示符门仍强制) + TB21/TB22；修前同一输入被 `POST_RESET_BOOT_PROMPT_MISSING` 拦下 |
| F2 | `_run` 对 `-c` 后的整条命令串跳过路径映射(chain:657-661) | TB05; 修前实测 argv 变成 `<repo>\savemem 0x08000000 …` |
| F3 | 帧列按符号分治: 6..12 允许负号，0..5/13 不允许(chain:1767-1769) | TB06/TB07/TB11/TB12 |
| F4 | S8 第 4 步只读 `flash_logstat` + ≥300 空槽门；S9 由 before/after 推导 `flash_dump first count` 并逐槽核对(chain:1717/1720-1740/1228-1230) | TB08-TB10、TB13-TB16、TB18/TB19; stub 实跑 `NEW_LOG_WINDOW{300,600,592,8}` |
| F5 | `M8R3Field.ps1:36` 白名单加 `response_driven` | TB20 + 本机 Parser/参数 AST(0 语法错误)；`real+all` 负控仍 PASS |

本轮结果: 候选 73/73 与 22/22、修前对照 7/22、`--validate-plan` 154/0、rows 41 组 problems=0、
PS 5.1 三入口 22/0 + 14/0 + 3/0(`actions_attempted=0`)。原文与身份表在
`host/*_r3finalfix.*`、`host/ff_identity.json`、`host/ff_runs.json`、`host/reviewer_ff/`。

结论标签(按任务书 §4 第 7 条，本机条件齐备):
`M8_R3_FIELD_PACKAGE_OFFLINE_REVIEW_GO_CONDITIONS_MET` —— 这是"候选散列 + 本机复核"的条件式离线接受，
**不是**硬件许可。

一处偏差照原文保留并登记(见 `run_record.md` §7 末段): `rows.json` 的 `flash_logstat` 系来源锚点
`ns_flash_shell.c:100-110` 与实际打印行 `:89-99`(`:94-95`/`:96-98`)不符；未改候选字节。

**下一步是向用户呈现一份绑定现有 M8 候选散列的完整现场批准**(学习 ≤15 s、保护观察 ≤5 s、
最多 2 次 start、合计 ≤20 s、IRUN20/100 Hz/DIR0，其余额度按修正后的 plan，
并新增 S8 那一次只读 `flash_logstat`); 该批准落地之前不连接、不下载、不复位、不起转。

## 9. 现场轮(FIELD-1)对账 —— 首次 real dispatch 停在 S2

批准：绑定 `1dc4230` + 候选 BIN `00608ec9…e304`，令牌 `M8-R3-1DC4230-20261101`(两参同串)。
UID 由我从本机历史成功烧录记录只读找回并核对(48 位 / `d2f530dbd062`)，原值未入库未进对话。
启动方式：Agent 拉可见 PowerShell 窗口，操作者只在窗口中答人工门(本轮实际弹出 0 次)。

| 环节 | 结果 | 依据 |
| --- | --- | --- |
| S0 绑定 | OK，BIN 290916 B / `00608ec9…` 与 manifest 逐字段相等 | `dispatch_ledger.jsonl` 的 S0_FACTS |
| S1 下载 | rc=0，loader 三条原文齐(285 sectors programmed) | `action_download/{rc.txt,stderr.bin}` |
| S2 回读 | **rc=1 → STOP `ACTION_NONZERO_RC`**，`readback.bin` 未生成 | `action_readback/stderr.bin` 原文 |
| S3–S11 | NOT_RUN；未开口、未复位、未起转、未写配置 | `DISPATCH_SUMMARY` / QUOTA_AUDIT 全表 |
| 板端事实 | Flash 已写入；**在板逐字节一致 UNPROVEN**；工具末态 `Core 0 (None): Halted`，是否在跑新固件 NOT_ASSESSED | 同上 + `run_record.md` §8 |

根因：pyOCD commander 对 `-c` 串做 shlex 解析，Windows 路径的反斜杠被去转义，落盘路径变成
`D:ns800reports…readback.bin`。F2 那一层(argv 不被前缀 repo_root)本轮是**对的**；缺陷在它下一层，
离线假子进程结构上抓不到。最小修正是把 `plan.json` readback argv 里 savemem 的目标路径改正斜杠，
这会改 plan.json 散列，属新一轮绑定；续测只需 1 次回读 + S3 起剩余额度，不需要第二次下载。

本轮我**没有**做也不会做：第二次下载、补一次回读、动用剩余复位。等下一轮批准。

结论标签(本轮实际，不作通过声明)：
`M8_R3_FIELD_FIRST_REAL_RUN_STOP_AT_S2` / `IMAGE_ON_BOARD_BYTE_EQUAL=UNPROVEN` /
`PROTECTION_BRANCH_REACHED=NO` / `ANOMALY_ON_HARDWARE=NONE_OBSERVED`。
证据见 `host/field_run_r3field1*.json|txt` 与 `offline/psrun-20261031T185843Z-20080-RunField/`。

## 10. 续测轮(M8-S2)对账 —— 镜像在板已证得，业务仍一步没走到

绑定提交 `d9ff664`，用 `field/plan_continuation_s2.json`(S1 已移除、头部把上一轮那次下载逐文件
散列绑死)在可见窗口跑唯一一次续测 dispatch，run 根
`offline/fieldcont-d9ff664-20261031T200214Z-41860`。

| 环节 | 结果 | 依据 |
| --- | --- | --- |
| S2 定长回读 | **rc=0**，`readback.bin` 290916 B、sha256 与候选 `00608ec9…e304` **逐字节相等** | `host/field_run_m8s2.json` |
| S3 串口会话 | 开口 1 次 → 唯一一次 `explicit_reset` rc=0 → 被动收 **1027 B** 原字节 → 首条 `system_status` 步窗内 **0 字节** → `MISSING_RESPONSE_PROMPT` 停止 | 同上 + `run_record.md` §10 |
| S4–S11 | NOT_RUN；人工门 0 次；无 `motor_start`，义务未建立，无需撤能 | `DISPATCH_SUMMARY` |

翻案与新增：`IMAGE_ON_BOARD_BYTE_EQUAL` 由 UNPROVEN 翻成 **PROVEN**；F6(`-c` 串里反斜杠被 shlex
去转义)的修正在真工具上生效；新暴露的现场事实是**复位后控制台不回答输入**。
结论标签：`M8_R3_CONTINUATION_READBACK_PROVEN` / `PROTECTION_BRANCH_REACHED=NO` /
`ANOMALY_ON_HARDWARE=NONE_OBSERVED` / `console_interactive=未证得`。

## 11. 一次串口诊断(加强版)对账 —— 只回答"收到什么"

授权：COM5 一次，被动 60 s，`version` 与 `system_status` 各至多一次，无复位、无电机动作，
零 DAP/下载/回读/配置写；复用现有采集路径；本轮不登记保护业务通过。
run 根 `offline/diag-com5-20261031T234328Z-48972`，台账 `sess_diag/ledger.jsonl`(7 行)。

- 被动 60 s：**rx_bytes=0**(原始件 0 B)；
- `version` 一次：**0 字节回复，连回显都没有** → 按合同 `MISSING_RESPONSE_PROMPT as=ver bytes=0` 停止；
- `system_status`：**未发送**(前一步未完整返回)；端口已关，`disarm=NOT_ARMED`，无义务、无清理错误。

本轮没有产出任何"板子在跑什么"的结论；额度按每次调用独立实例化，跨轮开口数只有"3 次(手探1+本轮1
+M8-S2 1)"这一种记法。失败件原样入库(提交 `5f45a90`)，不删不改。

## 12. 第 0 条源码审计对账 —— 永停条件**存在**，板端是否在此**未证**

零设备、零构建、零产品改动。产物四件：`host/m8diag_source_audit.txt`(判词)、
`host/m8diag_source_audit_check.py`(机器复算)、`host/m8diag_source_audit_console.txt`(真实输出)、
`host/m8diag_spi_h_excerpt.txt`(**仓库外件的搬运**，因 `ns800rt7p65-nssinepad/.gitignore:16 packages/`
把厂商 StdDriver 排除在外，远端逐 blob 回读**覆盖不到**它，本机身份
`sha256=90c9a1da446b…` / 51925 B / 1316 行)。

- **PROVEN(源码层)**：阶段5 之路上有且只有一处无界限、不让出、无错误返回的等待 ——
  厂商 `spi.h:479-481` 的 `while (SPRFSR.BIT.RFDN == SPI_FIFO_RXEMPTY) {}`，每字节一次，
  跑在 main 线程(prio 10，`rtconfig.h:119`)，因此饿死 finsh(prio 20，`:124`)；
  而 `applications/`+`board/` 全树**无看门狗引用**。这三件事合起来与三条观察同形。
- **有界限的部分也一并核了**：`wait_ready` 带 tick 上限且每轮 `mdelay(1)`(`ns_flash.c:27-42`)、
  `scan_log` 4096 圈且每 64 圈让出(`ns_storage.c:70/:83`)、BSP 逐字节循环 `length--`(`drv_spi.c:252`) ——
  所以"卡住"只能发生在最底下那层，不在计数层。
- **机器可复核**：52 条引用 + 9 条观察件断言 + 3 条元负控 ⇒ `AUDIT_PROBLEMS=0`，
  检查器先证明"会失败"才允许宣布 0。
- **仍保留三条并存解释**：H1 卡在该自旋 / H2 核未在跑 / H3 主机→板 RX 方向不通。不择一采信。
- **收回一句错话**：上一轮"按配置 1 MHz 约 8.5 s 必然扫完"是吞吐估算，不是界限，
  不能用来排除"卡在阶段5"。

本审计不消耗额度、不放宽任何判据、不登记业务通过。下一步 (a) 断电重上电 + 一个被动窗，或
(b) 一次 DAP 只读 `status`(代价：attach 本身可能把核 halt)，两者都**待新一轮批准**。

## 13. (b) 已执行 —— 核状态实测 `Running`，H2 在该状态上被排除

上面那句"待批准"里，**(b) 已由操作者选定并执行完毕**；(a) 仍未获批准。范围一次 attach + 一次
`status`，零 halt / 零复位 / 零 Flash 读写 / 零下载 / 零配置写 / 零电机动作。现场三项由操作者逐条确认。

| 次 | run 根 | 结果 |
| --- | --- | --- |
| 1 | `offline/dapstatus-20261101T031548Z-9916` | rc=2 `unrecognized arguments: --core 0` —— argparse 层就失败，**未接触目标**；我方 argv 形状缺陷，原样保留 |
| 2 | `offline/dapstatus-20261101T031618Z-44304` | **rc=0 / 2.25 s / stdout 25 B = `Core 0 (None):  Running`**，stderr 0 B |

关键取证点（否则这条读数会被误用）：pyOCD 的 `connect_mode` **默认是 `halt`**，而既往那条
`Core 0 (None): Halted` 是 `field/plan.json` 里 readback 显式传 `-O connect_mode=halt` 造成的 ——
机制已定位到工具参数，不再是板端证据；本次必须且确实传了 `attach`，commander 内建 `status`
（`commands/commands.py:104-119`）只读 `core.get_state()` 不发 halt，且实测文本与该文件 `:119` 的
格式串 `f"Core {i} ({core.node_name}):  {state_desc}"` 逐字符一致。两条计划参数事实都已做成检查器的机器断言
（`plan-readback-halts-on-connect` / `plan-reset-uses-attach`）。

判读边界：**H2 在这个当下状态上排除**；**H1 加强但未证**（坐实要 halt 读 PC，属另一次会改变板子
状态的动作，未获批准）；**H3 未被单独排除但独自解释不了"输出冻结在扫描行"**（TX 在同一会话已收到
1027 B，越过阶段5 必打 `supervisor.c:463` 完成行）。当前事实合起来与"主线程在不让出的循环里占住
CPU"一致。登记件 `host/dap_status_probe.json`；计划外只读探针，不消耗申报额度。

## 14. (c) 一次 halt 定位 —— 实测**不是** SPI 自旋，而是硬fault 死循环

`offline/daphalt-20261101T052450Z-22884`：rc=0 / 6.469 s / stdout 1205 B / stderr 0 B；
halt 前 `Running` → 唯一一次 `Successfully halted device` → 退出时 `Halted`。

- `pc = 0x0801e8a6` = `B .` 自跳转 = `rt_hw_hard_fault_exception + 398` = `cpuport.c:460` 的 `while (1);`
  （其前一行 `:457` 正是 `hard_fault_track();`）；`lr` 在 `bus_fault_track + 232`（该函数读
  `0xE000ED38 = SCB_BFAR`）；采样时 `r2/r12` 落在 `ns800_putc` / `rt_serial_write` ⇒ 处理程序在轮询打印。
- `xPSR=0x61000003` 与 `ICSR=0x0440f803` 独立给出 **IPSR=3 = HardFault**；
  `CFSR=0x00008200`（`BFSR=0x82` = PRECISERR + BFARVALID，`MFSR=UFSR=0`）；
  `HFSR=0x40000000`（只有 FORCED；`VECTTBL=0`、`DEBUGEVT=0` ⇒ 不是这次 halt 造成的）；
  **`BFAR=0x00bd80b0`**；`PRIMASK=1`；SysTick `CSR=0x00010007`、`RVR=399999`、`CVR` 在数
  ⇒ **"心跳尚未启动"这一支排除**（400 MHz / 1000 Hz）。
- 因此 §12/§13 并存的 H1(SPI 无界自旋)/H2(核没跑)/H3(RX 不通) 被**第四条实测事实**替代：
  固件自己进了硬fault 死循环。SPI 那条无界等待仍只是"源码层存在的隐患"，这次测量没证实它被踩到。
- 定位全程离线：`fromelf -c -a` 对**已接受 AXF**（`rt-thread-motor-m1.axf`，其配对 BIN 就是候选
  `00608ec9…`）全文反汇编，解析器只认文本里出现过的标签；三段切片入库
  `host/dap_halt_disasm_window.txt`；零目标内存读取、零 savemem。
- 机器复算：`host/dap_halt_check.py` ⇒ 17 原字节 token + 3 计数 + 9 条源码行引用 + 3 条元负控，
  `DAPHALT_PROBLEMS=0`。
- **勘误（§14.1）**：上一轮"`resume_on_disconnect=true` 是空操作"**说错了** ——
  `cortex_m.py:413-433` 在断开分支总会 `DHCSR=DBGKEY|0`（清 `C_DEBUGEN`）与 `DEMCR=0`（清 `TRCENA`）。
  正确口径是"零显式 halt、零复位，但**不是**完全没改变目标状态"。本轮已改为 `false`。
- 板子现保持 Halted（符合批准口径）。要拿根因位置还差**一次约 12 个目标 RAM 字的只读**
  （帧指针 = `[0x2000d3e8]`，来自切片 1 的 `0x0801e886`），零第二次 halt、零复位、零写 —— 超出本次批准，
  等一次新批准。
## 15. (frame / frame2) 异常栈帧读取 —— 出错指令定位到 `object.c:381`，但成因仍未定

先记账（随本轮生效的口径修正，原文不删；详见 run_record §14.2）：
1. **SysTick 在数 ≠ 板载 LED 心跳跑过。** (c) 轮第 3 条"心跳已启动且在数 ⇒ '闪灯代码尚未启动'被排除"**作废**：
   `COUNTFLAG` 只表示计数器曾数到零，不能证明任何线程被调度。该项回到未决。
2. **PC 已在 `while(1)` ⇒ 此刻 r2/r12/LR 是残留。** (c) 轮"处理程序正在轮询式往外打印"**作废**，
   改为"与此前发生过 `rt_kprintf→rt_serial_write→ns800_putc` 相容"。
3. 我按同一条机制自查后连带降级：`lr` 落在某函数内**只**说明"与该调用链相容"，因为本工程大量函数以
   `POP {r7,pc}` 收尾，尾返回**不还原 LR**。故 (c) 轮"`lr` 在 `bus_fault_track+232` ⇒ 已走到打印 BFAR 那一支"
   也一并降级为"相容"。
4. 我自己的失误：第一次读取的**帧基址预测差 0x28（40 字节）**（按 Handler/MSP 假设算 `0x20012720`，
   实际线程模式/PSP 是 `0x20012748`），88 B 窗口恰好把硬件寄存器帧留在窗外；由第二次单独批准的 32 B 补上。
   两次原件都原样入库。

授权与额度（实测）：两条独立批准 —— "一次异常栈帧读取 …… attach、`resume_on_disconnect=false`、先确认仍为
Halted、不新增 halt、目标 RAM 合计不超过 96 字节 …… 数据矛盾则保留原件、不强行归因、结束保持 Halted"，
以及"批准再读 32 字节 @0x20012770"。实际 `attach` 2、**`halt` 0**（stdout 里只有 `Core 0 (None):  Halted`
的开口/收尾各一条，无 `Successfully halted device`）、`reset`/Flash/RAM 写/串口/电机全 0；
目标 RAM 只读 92 B（批准内）+ 32 B（单独批准）= 124 B。两轮 rc=0、stderr 0 B，板子结束仍 **Halted**。

实测栈帧（`0x20012770..0x2001278c`，逐字在两个 run 根里）：
`r0=0x00bd80b0  r1=0x20015b78  r2=0x00000001  r3=0x20015b28  r12=0x20015b78  lr=0x08021d0d
pc=0x08020096  psr=0x81000000`；帧基址 `[0x2000d3e8]=0x20012748`，`exc_return=flag=0xffffffed`，
`r4..r11=0x400,0x12,0x0a,deadbeef×5`。布局三处独立对上：`cpuport.c:398-400/61-77/51-58` 的字段顺序、
`context_rvds.S:238/240` 两条都是 `STMFD r0!, {lr}`（所以 flag 与 exc_return 同值）、
`context_rvds.S:245` `MSRNE psp, r0`（所以 (c) 轮的 `psp` 就是帧基址）。

定位与 BFAR 关系（离线，只用已接受 AXF `0bba8ca7…0963`；切片 `host/dap_frame_disasm_window.txt`）：
`pc=0x08020096` = `rt_object_init+134` = `6800 LDR r0,[r0,#0]` = **`object.c:381` 的 `node = node->next`**，
即 `RT_DEBUGING_ASSERT` 下那段防重复初始化的检查循环（`:379-387`；`:386` 是 `RT_ASSERT(obj != object)`）。
这条 LDR 的基址寄存器就是栈帧 `r0=0x00bd80b0`，与 (c) 轮独立读的 `SCB_BFAR=0x00bd80b0` **逐位相同**，
PRECISERR 下不回写 r0 —— 复审点名要核的"访问地址与 BFAR 的关系"这里自洽，无矛盾需归因。
`lr=0x08021d0d` 是 `rt_object_init → rt_spin_lock_irqsave → rt_enter_critical → rt_soft_atomic_add`
里 `0x08021d08 BL rt_hw_interrupt_enable` 的返回地址（三层都 `POP {r7,pc}` 不还原 LR）：
它说明 `object.c:377` 的取锁走过，**不说明调用者是谁**。
出错上下文为线程模式（`EXC_RETURN` bit2/bit3 与栈帧 `psr` 的 `IPSR=0`+`T=1` 两路一致）。
`0x00bd80b0` 不在 map 任何一个执行区内（ITCM/DTCM/Flash/RW_RT_HEAD 都覆盖不到），
所以链表里那个 `next` 本身就是个不可能存在的地址。

本轮**没有**得到的东西（不得当作已证）：哪一个对象类的表坏了（`information/type/name` 三个局部槽被异常帧
原地覆盖）、上一个节点地址、迭代次数（表头坏 vs 第 N 个链接坏给出同一个 pc）、以及坏值由谁写入。
因此"拿到位置就直接做针对性修复"这一步我**没有**动产品代码：现在能改的只是症状处加检查，
成因需要一个能指出"哪一类/哪个对象"的新证据（最省的一条是把复位后的串口捕获窗开到覆盖硬fault 那次
一次性 dump —— `cpuport.c:440-444` 会自己打印 `hard fault on thread: <名字>` 加 `list_thread()`）。

与第 12 节 stage-5 审计的关系：本采样瞬间不在 `spi.h:479-481` 的无界 RX 等待里，源码层的"存在无界且不让出的
每字节等待"仍成立；板端这一瞬间是固件自己踩了精确总线错。两条结论并列，谁也没推翻谁。

复算：`python reports/reviews/phase8-m8-relative-protection-r3/host/dap_frame_check.py`
→ `CITES=24 CHECKS=88 DAPFRAME_PROBLEMS=0 NEGATIVE_CONTROL fired=3/3`；
真实输出 `host/dap_frame_console.txt`（LF-only，88 条 OK / 0 条 PROBLEM）。
登记件 `host/dap_frame_probe.json`；原件在两个 run 根
`offline/dapframe-20261101T054539Z-12284`（518 B / sha `32ec174ad685`）与
`offline/dapframe2-20261101T054747Z-38496`（188 B / sha `ca5d411e2ac1`）。
## 16. diag 栈修复轮（R1, 2026-11-01）—— 已按复审指令改真实代码，零设备动作

指针：本轮全部证据在 `reports/reviews/phase8-m8-diag-stack-r1/`（`run_record.md` + `stack_report.txt`
+ `disasm_diff.txt` + `host/diag_stack_check.py|console.txt` + `build/` 日志），
镜像在 `reports/firmware/phase8-m8-diag-stack-r1/`（AXF `fb26d5a0…d6ea` / BIN `f5c2fd89…08ce` /
MAP `fd334a83…ad579`，均 1 次构建、0 Error 0 Warning）。

一句话结论：**diag 线程的栈是确定缺陷** —— armlink 调用图自己给出
`diag_thread_entry [Stack]Max Depth = 1580 B`（本帧 632 + `rel_prod_evaluate` 248 + `rel_engine_step` 184
的三帧下界是 1064），而配置是 1024 B；且 `rel_prod_evaluate` 在 `diagnosis.c:446` 是**无条件调用**，
模式只是实参，MONITOR_ONLY 也走这条路。已把 `diagnosis.c` 的 1024 改为具名 `DIAG_THREAD_STACK 4096`。
生效证明不靠 Program Size（运行时堆参数不占静态段，四项与 R3 逐字相同），而靠全文反汇编差异：
唯一代码级差异是 `0x0800ca26 MOV r3,#0x400 → #0x1000`（`diagnosis_init` 里 `rt_thread_create` 的第 4 实参），
其余只有 `__DATE__/__TIME__` 串。差异行由检查器强制归类，未归类数必须为 0。

堆：32768 B 总容量，线程栈合计 12800 → 15872 B（+3072），未含 TCB 与其它 `rt_malloc`，
所以这是枚举而非"够用"证明；`diagnosis_init` 在建线程失败时 fail-closed（`-RT_ERROR` + `SUBSYS_FAILED`）。

未证四项照登记：4096 不是已验证上界（镜像级 `2996 + Unknown`）；diag 栈溢出与本次 HardFault 坏 next
的因果未证；`offsetof(rt_thread, thread_timer)==0x50` 未独立核得；新镜像未在板。
板子仍是 (c)/frame 轮结束时的 Halted，板上镜像仍是 `00608ec9…e304`。

另：复审纠正、我已撤回的一条见 `run_record.md §15.1`（"局部槽已被异常帧覆盖"不成立，
扩展帧下出错前 SP 应为 `0x200127D8`，局部槽很可能还在 —— 判定动作已排进板级对照包的 A/B 两项）。
## 17. DS-2 板级对照包已绑定（等复审一次批准；本轮零设备动作）

绑定物：`reports/reviews/phase8-m8-diag-stack-r1/field/`（`run_diag_stack_compare.py` 为单一事实来源，
`plan.json` 由 `--emit-plan` 生成，`sim-bound/sim_dispatch.txt` 是零设备展开且 `SIM_TOTAL problems=0`，
`selftest/selftest.txt` 是假端口注入的 5 项判定自检且 `SELFTEST_PROBLEMS=0`）。
序列 = 下载前两条只读 RAM(A 局部槽带哨兵自定位 / B 线程名) → 离线门禁 → 一次下载 → 290916 B 回读逐字节比较
→ 串口先 Open 后复位再采集 75 s → `system_status`/`ps`/`free`/`diag_status` 四条查询，原始字节全部先落盘；
`motor_run`/`config_save`/`blackbox_clear` 全 0，静态门禁只扫 argv 与 stage 绑定。
复审三条更正已并入：M7 扩展帧 104 B 且 bit9=0 ⇒ 出错前 SP=0x200127D8；`thread_timer` 偏移 0x50 另有本机
反汇编第二路独立证据（`ADDS r0,r0,#0x50` + `BL rt_timer_control`）；`rt_object.name` 偏移为 0
（`include/rtdef.h:284`）⇒ B 从 0x20015B28 直接解释线程名。栈/堆余量不自动判 PASS，`ps`/`free` 原文由人读。
