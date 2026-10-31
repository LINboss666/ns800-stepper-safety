# 交接：NS800 M8-R2 相对保护返修轮（离线轮，零设备动作）

- 日期：本机 2026-10-31（权威时间戳见同名 JSON 的 `date_utc`）
- 任务书：`NS800_M8_R2_Review_and_Repair_Task_20261031.md` + `NS800_M8_R2_Review_and_Repair_Package_20261031.zip`
  （操作者本机文件，不记录个人绝对路径），任务号 `phase8-m8-relative-protection-r2`
- 入审结论：**`M8_R1_REVIEW_REWORK_REQUIRED`** → 本轮为集中返修，不发软件 GO、不发镜像 GO、不新增纯 GO 登记轮
- 审核裁定（原样保留，不改写）：
  `ATTRIBUTES_RAW_EVIDENCE_EXCEPTION = ACCEPTED / KEEP_BYTES`、`OLD_13_COUNTEREXAMPLES = NOT_REPRODUCED`、
  `M8_R1_C_CHAIN = 44/44 + SEVEN_REVERSES_META_OK`、`NEW_CONTROL_COUNTEREXAMPLES = REPRODUCED`、
  `FIELD_RUNFIELD_AND_CONTROL_FLOW = REWORK_REQUIRED`、`R1_REVIEW_MANIFEST = 331/336_PRESENT_AND_MATCHED / FIVE_MISSING`、
  `SOFTWARE_IMAGE_FIELD_GO = NOT_GRANTED`、`HARDWARE_ACTIONS_THIS_REVIEW = 0`
- 工作分支：`feature/m8-sg-adc-relative-protection`（起点 `b4d4079c8a2ae22405fa436d4513d0b2807839f8`，直接续作，
  无 reset / rebase / merge / force）
- 源码与验证链提交（`code_commit` / `software_input_commit`）：`207b3747efa5d54f7549dca79ececc81c4766794`
- 被测镜像实际取自（`tested_image_build_commit`）：`207b3747efa5d54f7549dca79ececc81c4766794`
  （三目标各一次 Rebuild All，取自该提交的工作树；两字段本次相等，因为构建确实取自该源码提交）
- 设备动作：**无**。`hardware_actions = []`；未导入 pyserial/pyOCD 设备对象，未枚举/读目标内存或 Flash、
  未烧录、未复位、未开串口、未起转、未写配置、未触发故障、未制造碰撞、未改供电/线路/SW1/ENN。
- 本轮状态：`M8_R2_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable = false` 保持。
- 交付 tip 不写在本文件里：本轮只一次证据提交，其后由 `host/remote_readback.py` 从远端 blob 独立复算，
  结果记在 `reports/reviews/phase8-m8-relative-protection-r2/remote_readback.md`（该文件由推送后的回读产出）。

## 1. R2 条目是否按合同做到（逐条落点见 `run_record.md` §1）

| 要求 | 一句话结论 | 判据 |
|---|---|---|
| R2-A 控制锁必须真拒绝，不许"初始化过"当"这次拿到了" | `rel_ctl_take()` 未初始化返回 `-RT_EINVAL`、取不到返回 `-RT_EBUSY`；`rel_prod_commit` 失败时**一个字段都不写**，错误回到命令调用方 | `REL45/REL46` + 反向 `RV8_ctl_lock_silently_bypassed` META-OK |
| R2-A 快照整份或失败，禁止混帧；锁内不打印/不等待 | `rel_prod_get_snapshot/incident` 要么整份要么报错；镜像重试每帧做、失败计数 `pubfail=` 具名披露；首因权威副本留在唯一写者引擎里，命令侧走 `rel_writer_latch()` | `REL33/REL43`（锁死时 `snap=FAIL inc=FAIL`，恢复后补齐且 `pub_fail>0`） |
| R2-A 单一写者：运行期不得擦生产上下文 | `rel_prod_init_wipe()` 只在线程起来前调用；命令侧只有 `rel_prod_commit` / `rel_prod_request_rearm` | `REL47`（拒绝路径不改任何状态） |
| R2-B1 模式+代际在同一次静止事务内发布，打印排在释放之后 | `diagnosis_set_mode()`：只读门 → `static_begin` → 最终复验 → `rel_prod_commit(mode,bump_gen)` → `d_mode` → `static_end` → 全部打印 | `REL47` 用夹具在"仍持门的最后一刻"读 `d_mode`/`gen_want`/`held`，并断言门内打印字节数==进门时；确定接缝，无 usleep |
| R2-B2 新一代必须作废全部旧判据证据 | `rel_prod_sync_generation()` 在步进前调 `rel_engine_new_generation()`，旧首因挪 `archived` | `REL48` + 反向 `RV9_new_generation_keeps_old_evidence` META-OK |
| R2-B3 新帧 ≠ 运动事实新 | 帧内新增 RAM-only `motion_ok/motion_seq/motion_tick`，只有成功才推进；失败继承身份并置 `motion_ok=0`；未知/过期并入有界降险 `RELCOV_MOTION_UNKNOWN` | `REL49` + `SNS-ST` 双向断言 |
| R2-C1/C2 现场包要真的可执行、后端可注入 | `field/M8R2Field.ps1` 三入口（`-SelfTest`/`-Precheck`/`-RunField` 互斥参数集）+ `m8r2_serial_chain.py` 的 `Sim/Manual/Real` 三种动作后端；假字节喂不进真实身份门（`identity_gate=NOT_EXERCISED(...)`） | Python 25 例全绿；PS 5.1 `SELFTEST_VERDICT|PASS|checks=21`，三入口各真实跑过 |
| R2-C 5 秒硬预算 / watch 失败停机 / finally 撤能 | 预算从 `motor_start` 尝试起算的单调硬预算（含清理预留 4 s）；watch STOP 后危险步骤不再发出；错误路径仍发 `motor_stop→motor_disarm→motor_status`，撤能字段不可确认就写 `UNKNOWN` | `FS08/FS09/FS10/FS11/FS12` + 定向检出 `FS23/FS24` |
| R2-C3 协议完整 ≠ 业务成功 | `MISSING_RESPONSE_PROMPT` / `ILLEGAL_BYTES_IN_RESPONSE` / `FIELD_NOT_EXACTLY_ONE` 是协议层；`FAILED`/`REFUSED`/`VOIDED`/`not supported` 是业务层；正控用本轮真实 C 输出 `mode=RELATIVE_ACTIVE` | `FS01..FS07`（真实学习失败→`BUSINESS_FAILED`；真实成功模式→`OK`）+ `FS25` 定向检出 |
| R2-C4 参数单位与保护前置 | `learn` 的 `LO/HI` 明确是 STEP 频率 Hz（不是 SG 码）；`motor_target 100` + `diag_profile learn 16 80 120 0` 取自同一已验证频率；板级不再继承 M4/M5 的 CONFIGURED/IRQ OPEN（`tmc_config_apply` 2 次、`irq_polarity_prep` 2 次已入额度）；IRUN20 未动；重启后按**本轮** commit 的 id/rev/signature 比对；受控加载 `verdict=NAMED_PREREQUISITE_MISSING`；无触发只写 `OBSERVATION_NO_TRIGGER` | `field/plan.json` + `field/needles.json` 40 条全部由 C 源码字面量证明（`total=40 unproven=0`） |
| R2-D 清单要查"登记但不存在" | `host/remote_readback.py` 改为清单优先：逐条核存在/唯一/字节数/散列，`declared_but_absent`、`duplicate_conflict`、`read_failed`、`hash_mismatch`、`suffix_ambiguous` 任一命中即披露；范围分 `SCOPE_R2`(本轮，必须零问题)与 `SCOPE_PRIOR`(往轮，只披露) | 远端头 `4df1e03`：`SCOPE_R2 171/171 problems=0`，退出码 0；往轮 64 条 + 无路径 32 条逐条披露，见 `remote_readback.md` |

主机链：`SUMMARY|cases=49 pass=49 fail=0 pre_bad=0 executed=49`，`warning_count=0`；
反向 9 家族（RV1–RV9）全 META-OK；三目标各一次 Rebuild All，`0 Error(s), 0 Warning(s)`，`uv4_rc=0`。
现场包：Python `SUMMARY|cases=25 pass=25 fail=0 executed=25`（含 3 项定向检出 RV10a/RV10b/RV10c）；
PowerShell 5.1 三入口各一次真实执行，`SELFTEST_VERDICT|PASS|checks=21`；
离线产物归档 2 次运行、137 件、168,353 B（`host/field_offline_run_manifest.json`）。

## 2. 产物位置

- 证据目录：`reports/reviews/phase8-m8-relative-protection-r2/`
  （`run_record.md`、`field_candidate_package.md`、`host/` 12 件、`build/` 5 件、`field/`（含 `offline/` 两次运行）、`SHA256SUMS.txt`）
- 镜像目录（**新**，6 枚镜像/AXF + 3 份 map + manifest + 清单）：`reports/firmware/phase8-m8-relative-protection-r2/`；
  R1 与 M8 的镜像目录一字未动。
- R1 那 5 件"清单登记但不在 Git 里"的构建日志（`reports/reviews/phase8-m8-relative-protection-r1/build/` 下
  `rt-thread.log`、`rt-thread-measure-a0.log`、`rt-thread-motor-m1-candidate.log`、`session1_rt-thread-motor-m1-candidate.log`、`rc.txt`）
  **原件仍在**，本机 sha256 与 R1 已发布清单逐字相符，本轮按具体路径 `git add -f` 补入库，
  未改 `.gitignore`、未重造历史、R1 清单原文与首轮失败勘误保留。
- `.gitattributes`：按审核裁定保留（KEEP_BYTES）。本轮只追加 R2 路径的窄规则并在文件里写明范围；
  未加全局 `* -text`，未做全仓 renormalize。

## 3. 五条必须说清的取证事实（不掩盖）

1. **字节身份的前置校验方式**：清单散列是按本机磁盘字节算的，而 `core.autocrlf=true` 会在 `git add` 时把带 CRLF
   的文本剥成 LF。本轮在提交前用 `git cat-file blob :<path>` 逐条比对暂存 blob 与清单，抓到 **6 件**
   本机带 CRLF 的交付件（`field/` 两个 `.py`、`field/plan.json`、`field/needles.json`、
   `field_candidate_package.md`、`run_record.md`），给它们所在的**具体 R2 路径**补窄 `-text` 规则后
   `git add --renormalize` 这些路径，复核 `problems=0`；未加全局 `* -text`，未做全仓 renormalize。
   推送后仍由独立远端回读复算，本机结论不替代远端结论。细节见 `run_record.md` §4。
2. **`field/offline/` 台账独占创建**：撞名即 `LEDGER_PREEXISTING`。这条是自检里抓到"变异体读到基线台账"造成的
   **假通过**之后加上的；`FS23/FS24/FS25` 三项定向检出就是为了让"去掉硬截止/去掉撤能清理/去掉业务层"
   必然变成具名失败，而不是让探针只把 OBSERVED 变成 NOT_OBSERVED。
3. **本轮的"门内/门外"结论是确定交错模型**，不是实测调度：夹具在"仍持静止门的最后一刻"回调读状态，
   `review_harness.c` 只增加一个"静止门释放之后"的平台会合钩子，产品源码未因它改动。真实目标调度时序仍属
   `FIELD_UNVERIFIED`。
4. **回读程序是被"真跑"才修好的**（三处，全部由执行暴露，见 `run_record.md` §4）：
   ① 只认本轮 `build_manifest.json` 的 `targets:[{images,map}]` 结构，撞 `AttributeError`——仓库里另有三种
   历史形态，只认一种等于静默跳过别的清单；② 本轮 `field_offline_run_manifest.json` 的 `entries` 键基准
   写错，137 条被解析成 `host/field/...` 报缺件；③ 只按"清单自身目录"这一种基准，把 R1 的离线产物 285 件
   误报缺件（那件的基准写在文档别处）。修法：递归解析任何 JSON 清单 + 三种常规基准（清单自身目录→上一级→
   仓库根）+ **后缀索引兜底**（唯一命中且散列相等才算通过，多条命中报 `suffix_ambiguous` 而不是挑一个）+
   解析不出路径的逐条披露 `sha_declared_without_path`；本轮清单加 `path_resolution` 自描述，143 对
   `(路径, sha256, bytes)` 与改前逐条相等，只改基准。判定分 `SCOPE_R2`（本轮四张清单，必须零问题）与
   `SCOPE_PRIOR`（往轮清单逐条披露，不改写历史）。
5. **远端回读的最终数字**（头 `4df1e03`，原始 stdout `host/remote_readback_console.txt`，判读全文
   `remote_readback.md`）：`SCOPE_R2|entries=171 verified=171 problems=0`，退出码 0；
   仓库内全部清单去重后 `declared=985 verified=923`（其中 `suffix` 兜底 285 条全通过）；
   `SCOPE_PRIOR|problems=64`（缺件 50 / 重复冲突 2 / 散列不符 4 / 后缀歧义 8）+ `no_path=32` 逐条披露，
   4 条散列不符只在记录里登记为"往轮清单登记值与远端字节不一致"，未复算差异是否等于 CR 字节集合，
   因此不写成根因，也不改写任何往轮清单、镜像或原始证据。

## 4. 分级

- `HOST_PROVEN`：R2-A/B1/B2/B3 的判据、时序、并发纪律级结论（虚拟时钟 + 单写者夹具）。
- `BUILD_PROVEN`：三目标各 0 error/0 warning、6 镜像 + 3 map 字节与散列、每目标有效宏披露。
- `OFFLINE_EXECUTED`：现场包的调度、5 秒预算、清理与撤能字段判定、协议/业务分层、额度扣减、动作后端调用序列。
- `FIELD_UNVERIFIED`：真实抢占时序、真实撤能物理时延、profile 现场学习质量、安培标定分离度、v2→v3 迁移。
- `FIELD_NOT_TRIGGERED`：任何板级异常、真实触发、真实碰撞；R6 非破坏激励 `selected=false`。
- `NOT_VERIFIED`：受控加载工具与作用点（`plan.load_step.verdict=NAMED_PREREQUISITE_MISSING`）。
- `NOT_AUTHORIZED`：现场候选包的任何一个阶段（下载/回读/复位/串口/起转/写配置/重启/加载/碰撞）。

额度消耗：设备侧全项 0（本轮无任何设备动作）；离线侧主机链 1 次（定稿字节）、反向 1 次、
现场 Python 1 次、PowerShell 3 入口各 1 次、Keil Rebuild All 3 次（每目标恰好一次）。

## 5. 下一轮需要谁做什么

- 审核方：对 `code_commit 207b374` + 本轮证据目录 + 现场候选包做一次独立复审。重点三件：
  ① R2-A 的"锁失败一个字段都不写"与快照整份/失败是否满足；② R2-B1 的静止门内发布 + 释放后打印是否可接受为
  该风险的闭环（确定交错，非实测调度）；③ R2-C 现场包的额度（含 `tmc_config_apply:2`、`irq_polarity_prep:2`、
  `motor_run:3`/15 s、`motion_cap_seconds_per_run:5`、`config_save:1`、`approved_reboot:1`）与
  `motor_target 100` / `learn 16 80 120 0` 的区间来源是否作为执行轮上限。**本轮不申请 GO。**
- 若复审放行现场轮：需要单独授权下载/定长回读/显式复位/串口/短转/一次 `config_save`/一次经批准重启，
  并且先补上受控加载的具体工具与作用点；`physical_collision` 仍为 0。
- 仍然悬着的实物/现场项（本轮未触碰）：P1 独立电流参考与零点增益、`RT_CONSOLEBUF_SIZE` 128 裁剪在真串口下的表现、
  v2→v3 参数迁移在真 Flash 上的行为、真实触发后的降险计时。
