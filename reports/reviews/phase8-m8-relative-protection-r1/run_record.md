# M8-R1 修复轮执行记录（`phase8-m8-relative-protection-r1`）

任务书：`NS800_M8_R1_Review_and_Repair_Task.md`（verdict `M8_IMPLEMENTATION_REVIEW_REWORK_REQUIRED`）。
本轮不发软件 GO、不发镜像 GO、**不含任何设备动作**；集中完成 R1–R6 修复、验证、三目标构建与
可执行现场候选包后一次交付复审。

## 0. 输入与产出身份（逐条可由仓库复算）

| 项 | 值 |
|---|---|
| 被复审交付 | `f939363449c528892fb3d9978d73e085cfa90c91` |
| M8 软件输入 | `f9217005f191a601601d315add02c3e8c51f64ed` |
| M8 工件提交 | `56d67a08…`；M8 起点 `f1aa2e83…` |
| 本轮起点 | 在 `f939363` 之上直接续作；无 reset / rebase / merge / force，`main`、`phase7/full-embedded-software`、`phase8/tmc-drive-config`、`debug/tmc-uart-rootcause-20261028`、tag `097935fe…`/peel `e40a9b95…` 均未移动 |
| 本轮 `code_commit` | `3275356a46e0b503888e9a4869da3b2b41f027a8`（12 文件；提交后工作树对已跟踪文件干净） |
| 镜像归档 | **新**目录 `reports/firmware/phase8-m8-relative-protection-r1/`（6 枚镜像/AXF + 3 份 map + manifest + SHA256SUMS）；M8 目录 `phase8-m8-sg-adc-relative-protection` 一字未动 |

主机链输入身份（Git blob，按 `code_commit` 复算，见 `host/stdout_crlf_normalisation.json`）：
`rel_main.c 2f971b28f2ad`、`run_diag_relative_m8.sh 3d1f71701ba9`、`shim_rel/rtthread.h 69597264cdfa`、
`shim_rel/rtdevice.h 6a15e18cb979`。编译器：`tdm64 gcc 4.9.2`，`-std=gnu11 -Wall -Wextra`。

## 1. R1–R6 落点（实现位置 → 判定用例）

源码路径简写 `apps/` = `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/`。

| 要求 | 实现 | 用例（主机链，离线） |
|---|---|---|
| R1-A1/A2 warn 与 strong 各计各的时长/样本数 | `apps/diag_relative.c:325` `ev_pair()` 每源调用两次；严重判定只读 `*_strong_*` | `REL28-strong-needs-own-evidence`、`REL07`、`REL09` |
| R1-B1 帧 + SG + **ADC(cur_tick)** 三源年龄分别判 | `apps/diag_relative.c` 第 1 节把 `adc_age > age_frame_ms` 单列；IMU 年龄不进 `stale` | `REL29-frozen-adc-age-degrades`、`REL20-age-check-before-dedup`、`REL08` |
| R1 恢复：遇任一新反证即断，只由充分且独立的正常证据推进 | `apps/diag_relative.c:357` `ok_tally()`（需 `span≥dur_recover_ms` **且** `ok_new_cnt≥min_new_sg` **且** `ok_src_seen==3`）+ `ok_clear()` 反证清零 | `REL32-recovery-breaks-on-counter-evidence`、`REL21` |
| R1 fused 起点跨回绕一致 | `apps/diag_relative.c:340` `tick_later()` | `REL11`、`REL31-tick-wrap-and-rate-binding` |
| R1 引擎级（非纯函数）tick 回绕 / 混合速率 / 源断档 | 生产 `rel_engine_step` + 生产线程入口驱动，夹具只注时间 | `REL31`、`REL30-source-gap-restarts-evidence`、`REL26` |
| R2 覆盖丢失（profile 丢失/失效、签名漂移、快照/帧失败）走具名"先保护后披露" | `apps/diag_relative.c:240` `REL_COVERAGE_LOST_MS 500u`；`:408` 仅在 `armed && 运动` 下由 `cov_lost_ms` 触发 `REL_COVERAGE_LOST`；`rel_request_protection` 落 `coverage_at_trip`（`:732`） | `REL22-coverage-loss-protects-first`（**改写**）、`REL33-lock-fail-bounded-degrade`、`REL34-no-frame-still-supervises` |
| R2 真实触发后 → `MONITOR_ONLY` / `REARM_REQUIRED`；`fault_reset`/`system_selftest` 不自动恢复自动武装 | `apps/diagnosis.c:356` `diag_thread_entry` 在 `acted` 后置 `d_mode = DIAG_MODE_MONITOR_ONLY`；事故 ticket 生命周期 `rel_prod_get_incident` | `REL35-incident-ticket-lifecycle`、`REL37-post-failure-keeps-first-cause` |
| R2 显式再武装（静止 + 可信禁用）归档旧事故并开新一代 | `apps/diag_relative.c:879` `rel_prod_sync_generation()`、`:902` `rel_prod_evaluate()` | `REL35`、`REL14-first-latch-immutable` |
| R2 out 参数为 NULL 不得跳过副作用 | `apps/diag_relative.c:902`（副作用先做，`if (post_rc)` 只在末尾写回） | `REL36-null-outparams-still-protects` |
| R3 拒绝一切人工改模式（含关闭） | `apps/diagnosis.c:517` `diag_human_mode_gate()` | `REL38-mode-gate-and-static-transaction`、`REL41-illegal-context-silent` |
| R3 开启 + 提交在**最终静止事务内**复验资格 | `diagnosis_set_mode` 内 `motor_calibration_static_begin/end` 全程；拒绝 `-RT_EBUSY` | `REL38`、`REL16-relative-mode-arm-gate`、`REL17-active-measured-gate-unchanged` |
| R3 所有权/代次与锁顺序；不跨 UART/Flash/打印/等待持锁；不新增线程 | `d_ctl_lock`（短）与记录的顺序 `mot → cfg`；`apps/diag_relative.c:1174` `rel_ctx_ok()` 先于解析/取锁/打印 | `REL33`、`REL41`、`REL40-config-lock-failure-writes-nothing` |
| R3 zero/learn 先作废本次凭证、私有候选 | `apps/diag_relative.c:1304` `rel_cmd_zero`（逐帧 `rel_static_and_ready()` 复核，只在事务内发布）、`:1380` `rel_cmd_learn`（入口作废 `d_pending_learn`，DIR=2 本轮拒绝） | `REL39-zero-static-and-failed-relearn`、`REL25-cli-capture-and-publish` |
| R3 所有 profile 配置路径处理取锁失败 | `apps/project_config.c` 的 `cfg_take_checked` 覆盖 `get_snapshot`/`set`/`save`/加载发布/defaults/启动存储失败 | `REL40`、`REL23-profile-persist-and-v2-migration` |
| R4 分层生成阈值（正常样本/噪声 + 码域可达裕量），低 SG 基线可提交 | `apps/diag_relative.c:1063` `rel_profile_gen_thresholds()`（`SG_HEADROOM_INSUFFICIENT`/`ADC_HEADROOM_INSUFFICIENT`/`TIER_RANGE` 具名拒绝）、`:1031` `rel_adc_reach()`（同一展宽 band 两侧独立核） | `REL42-low-sg-committable-and-named-reject`、`REL05-profile-qualification-matrix` |
| R4 关键行 ≤110 B | `apps/diagnosis.c:686` `diag_relative_status_lines()`（单快照约 15 行）+ `apps/diag_relative.c` CLI 摘要行 | `REL43-max-value-lines-fit-console` |
| R6 ADXL345 碰撞独立保护（只认真新 IMU 证据） | `apps/diag_relative.c` 第 8 节（`fresh_imu` + `dur_impact_ms` 窗口 + `imu_usable` 披露；IMU 静默不清证据、不 blanket 关掉 SG/ADC）；`apps/sensor_service.c` 新增 `imu_seq/imu_tick`（**仅 RAM**，blackbox Flash 记录未动） | `REL44-impact-fresh-evidence-only`、`REL15-monitor-only-never-acts` |

`REL_IMPACT` 独立触发、MONITOR 永不 post、post 失败仍先保护、不自动再武装——分别由 `REL44`、
`REL15`、`REL37`、`REL35` 断言；未把旧 `DIAG_IMPACT` 直接接线当成交付。

## 2. REL22 勘误（错误合同显式更正，保留身份不复用编号）

M8 的 `REL22-coverage-disclosure-no-flood` 断言"覆盖不匹配 ⇒ `post=0`"。审核方判定该判据本身是错的：
覆盖丢失时保护缺席比刷屏危险得多。本轮的处理：

- 身份保留为 `REL22`，名字改为 **`REL22-coverage-loss-protects-first`**（`tests/host/rel_main.c:1918`）。
- 新判据：签名失配持续越过 `REL_COVERAGE_LOST_MS` ⇒ **恰好一次** `safety_post_event`，
  原因具名 `REL_COVERAGE_LOST` 且 `coverage_at_trip == RELCOV_SIG_MISMATCH`；打印在保护之后；
  覆盖行有界（≤3 行，不刷屏）；模式降级 `MONITOR_ONLY`；`MONITOR_ONLY` 下同类失配仍零 post。
- 旧"mismatch ⇒ post=0"不再作为期望存在。反向家族 `RV5` 现在检出的是这条新合同。
- 原 M8 的 `REL22` 原文与台账只在 `reports/reviews/phase8-m8-sg-adc-relative-protection/`
  保留，不改写。

## 3. 主机链与反向（同一 `code_commit`，两次运行逐字节一致）

- 正常链：`SUMMARY|cases=44 pass=44 fail=0 pre_bad=0 executed=44`，`proc_rc=0`，runner rc=0，
  `warning_count=0`（编译 stderr 0 字节）。
- 反向 7 家族全部 META-OK（副本编译成功 + rc=1 + 完整自洽台账 + 指定失败身份命中）：
  `RV1`→REL07/REL11/REL28 等（额外身份如实登记，不改判定）、`RV2`→REL09+REL28、
  `RV3`→REL16、`RV4`→`REL28-strong-needs-own-evidence`、`RV5`→REL22/REL33/REL34/REL36、
  `RV6`→REL39、`RV7`→REL44。
- 复跑一致性：首跑 run_id `20261030T183455Z-1432` / `20261030T183508Z-1799` 与归档跑
  `20261030T185713Z-2046` / `20261030T185724Z-1431` 的 `out.txt` **SHA256 相同**；
  反向基线 `out.txt` 与正常链 `out.txt` 也相同。判据字段
  `cross_run_identity` 见 `host/stdout_crlf_normalisation.json`。
- 首跑只落了 `rel.exe` 的 stdout、没保存 runner 控制台；为把"runner 的判定"也变成可复核原文，
  在同一 `code_commit` 上复跑了一次并把控制台归档（`r1_normal_runner_console.txt`、
  `r1_reverse_runner_console.txt`）。这一点在 JSON 的 `runs.note` 里明说，不装作首跑就全落了盘。

## 4. 审核方 13 项探针在修复后树上的闭合（原文 `host/probe_rerun_out.txt`）

全部 `NOT_OBSERVED`，`REVIEW_SUMMARY|observed=0|hardware_actions=0|sources_unmodified=YES`。
数值即探针打印的原文：

| 探针 | 结果 | 关键读数 |
|---|---|---|
| A1 SG warn 时长被当严重 | NOT_OBSERVED | `warn_ms=500 first_strong_sample_reason=NONE span=510` |
| A2 ADC 同上 | NOT_OBSERVED | `warn_ms=390 first_strong_sample_reason=NONE span=400` |
| B1 ADC 身份冻结不过期 | NOT_OBSERVED | `cur_age=1200 frame_age=0 reason=REL_SENSOR_UNAVAILABLE`（降级，不是"继续当新样本"） |
| B2 生产签名丢失跳过保护 | NOT_OBSERVED | `post_calls=1 mode=0 mismatch_text=1` |
| B3 生产取锁失败跳过保护 | NOT_OBSERVED | `post_calls=1 mode=0 lock_fail_text=1` |
| C1 运动中可关模式 | NOT_OBSERVED | `rc=-8 moving_state=2 armed=1` |
| C2 开启忽略静止事务拒绝 | NOT_OBSERVED | `rc=-23 static_begin_calls=1 mode=0` |
| C3 保存锁失败仍写 Flash | NOT_OBSERVED | `rc=-12 saved=0 releases=1` |
| C4 失败重学复用旧凭证 | NOT_OBSERVED | `learn_credential=0 published_window=0..0 dir_mask=0` |
| C5 zero 接受期间运动 | NOT_OBSERVED | `zero_accepted=0 final_armed=1 zero_band=0..0` |
| C6 第二次事故静默无新请求 | NOT_OBSERVED | `first_posts=1 total_posts=1 mode=0 latched=1` |
| D1 低 SG 基线不可提交 | NOT_OBSERVED | `normal_sg_min=64 generated_drop=32 unreachable=0` |
| E1 关键行超控制台裁剪 | NOT_OBSERVED | `max_line_bytes=101`（预算 110 B，未开 `THREADSAFE_PRINTF`、未改 `RT_CONSOLEBUF_SIZE`、未改 UART 叶子） |

探针复跑的输出是新落盘的（首跑输出只在会话里读过，未存文件）；JSON 的 `probe_rerun_note` 登记了
这一点，并同时给出 `review_probes.exe` 的 SHA256 以便第三方用同一枚二进制复现。

## 5. 变更适用回归（原文 `host/regression_*.txt`）

| 链 | 结果 |
|---|---|
| CC（诊断引擎/电流标定，`regression_cc.txt`） | `SUMMARY|cases=34 pass=34 fail=0 pre_bad=0` → PASS |
| SD 安全数据语义（两变体） | PASS |
| S1 关断结果可信（target + host_ovr，78 必需用例/变体） | PASS |
| S2 停机优先并发身份（19 组 × 2 变体） | PASS |
| M6 CLI | `ledger 10/10 PASS` ×4 变体 → PASS |
| I/O 行为（4 变体 + 非法宏值编译拒绝） | PASS |
| M5 flash 导出（normal + cap + archive gate） | PASS |
| A0 目标机测量记录器 | `A0CHAIN-META: OK` |

未改：HAL/内核/时钟、UART TX 叶子、`stop_priority` 核心、引脚映射、宏默认值、TMC 地址/波特/CRC 预算、
Flash 布局、blackbox 记录 schema。`RT_CONSOLEBUF_SIZE` 未加大，未加全局 printf 锁。

## 6. Keil 三目标构建（只构建）

`UV4.exe -r project.uvprojx -t <target> -j0 -o <绝对日志路径>`，每目标一次 Rebuild All，
**未使用 `-f`**、无下载、无回读、无 DAP、无复位、无串口。全部
`0 Error(s), 0 Warning(s)`、`uv4_return_code=0`（原文在
`reports/reviews/phase8-m8-relative-protection-r1/build/`，见 `build_manifest.json` 逐目标字段）：

| 目标 | Program Size |
|---|---|
| `rt-thread` | Code=203090 RO-data=85246 RW-data=2188 ZI-data=84704 |
| `rt-thread-measure-a0` | Code=210386 RO-data=88206 RW-data=2188 ZI-data=92424 |
| `rt-thread-motor-m1-candidate` | Code=203322 RO-data=85126 RW-data=2188 ZI-data=84704 |

四个安全宏的有效值逐目标登记在 `build_manifest.json.safety_macros_effective`：默认与 a0 目标四项全 0；
motor-m1 候选为 `MOTOR=1`、`TMC=1`、`STEP_DIAG=0`、`LIMIT=0`。

**构建会话披露（不重造历史）**：第一次会话里脚本的 `-o` 路径写成 `"$EVW\\$T.log"`，
`$T` 在双引号里没展开，三目标日志落到同一个文件互相覆盖，只有 motor-m1 那份存活
（保留为 `build/session1_rt-thread-motor-m1-candidate.log`）。随后修正为
`LOG="$(cygpath -w "$EV/$T.log")"` 并**重跑了三个目标各一次**，本轮归档的 Program Size 与日志
取自第二次会话；存活的第一次 motor-m1 日志与第二次同目标数值一致
（Code=203322 RO=85126 RW=2188 ZI=84704），因此两次会话对该目标等价——但这不等于"第一次会话的
另外两份日志被保存了"，它们没有。此事实同时写在 `build_manifest.json.build_session_note`。

## 7. 参数分级与工件分级（不写未测量的 PASS）

设计参数（软件侧，非现场实测边界）：`dur_sg=500 dur_adc=300 dur_fused=300 dur_recover=500`，
`age lim sg=350 frm=100 imu=100`，`imp=2500/2/100`，覆盖丢失门 `REL_COVERAGE_LOST_MS=500`
——该门**故意不进** profile（不放进可被现场改写的参数集），探针输出里的
`[DIAG] rel dur=… age lim=…` 行即其披露原文。本轮**没有**为了掩盖缺陷而放宽任何年龄门。

| 级别 | 内容 |
|---|---|
| `HOST_PROVEN` | §1 全部判据与时序（含 tick 回绕、混合速率、源断档、恢复反证、覆盖丢失先保护、事故 ticket/代次、NULL 出参、post 失败保先因、CLI 上下文门、阈值生成分层与具名拒绝、R6 只用新 IMU 证据）、§3 44 例 + 7 家族反向 |
| `BUILD_PROVEN` | 三目标 0/0、6 枚镜像 + 3 份 map 的字节身份、逐目标有效宏、清单不哈希自身 |
| `FIELD_UNVERIFIED` | 生成的阈值本身（tier/噪声裕量派生的数值是否适合真机 SG/ADC 分布）；profile v3 在真 Flash 上的写入与迁移；DIR 单方向学习窗口是否覆盖真实工况 |
| `NOT_VERIFIED` | 真机上的武装/覆盖/学习/提交全流程；真实并发与调度抖动下的时序；控制台在真串口下的行长与裁剪（P0 只证明了 62 B 损坏未再现） |
| `NOT_AUTHORIZED / NOT_EXECUTED` | 现场候选包的**任何**一步（含下载、烧录、复位、串口、起转、写配置、触发故障、物理碰撞） |

`safe_for_enable = false` 保持不变。

## 8. 旧 M8 缺失构建日志的补归档

见 `reports/reviews/phase8-m8-sg-adc-relative-protection/build/ERRATUM_log_backfill.md`：
四件原件仍在磁盘，SHA256 与 M8 当时已发布的 `SHA256SUMS.txt` 逐字相符，本轮按具体路径
`git add -f` 补进仓库；未改 `.gitignore`（命中的是 `.gitignore:28 build/` 与 `:31 *.log` 两条规则），
未改 M8 的 `SHA256SUMS.txt` 与 `run_record.md` 原文。

## 9. 入库字节身份（autocrlf 修正，交付前发现并处置）

`core.autocrlf=true` 会在 `git add` 时把带 CRLF 的文本剥成 LF。本轮暂存 349 件时实测有
**262 件的 Git blob 与磁盘字节不一致**（逐类：`.map` 3、`.bin` 115、`.txt` 115、`.json` 26、
`.log` 3）——其中 `.bin` 正是"原始字节先落盘"的假端口捕获，`.map` 是链接器产物。
若就这么提交，"逐字节可复算"的声称对这些件不成立。

处置（不改忽略策略）：

1. `git add -f .gitattributes`（该文件此前只存在于本地，被 `.gitignore:2 /*` 默认拒绝；
   用与本轮构建日志同一的**按具体路径 force-add**手法入库，**没有**修改 `.gitignore` 的白名单）；
2. `.gitattributes` 里只对**本轮证据路径**加 `-text`（map / 构建日志 / `field/offline/**` /
   `host/*.txt|json` / 两份 SHA256SUMS / 固件 manifest），不动任何既有规则；
3. `git add --renormalize reports/`；
4. 复核：`staged=349`，`blob_neq_disk=0`。
5. 同一判据回用到 `code_commit 3275356` 的 12 个源码/夹具文件上（磁盘字节 vs 提交内 blob）：
   11 件逐字节相等；唯一例外是 `tests/host/cc_main.c`——磁盘副本含 835 处 CRLF，提交内是 LF 形态
   （`blob 31148 B / CR 0` vs `disk 31983 B / CR 835`），而 `disk.replace(CRLF,LF) == blob` 为真、
   `git diff 3275356 -- 该文件` 输出为空，即差异只在行尾、内容一致。该文件不在本轮 `-text`
   覆盖范围内，也不应改历史，所以按事实登记，不写成"全部相等"。

清单规则：`reports/reviews/phase8-m8-relative-protection-r1/SHA256SUMS.txt` 覆盖该目录 335 件，
**排除自身与后续 `remote_readback.md`**（清单不哈希自己的字节，回读记录在清单生成之后才产生）；
`reports/firmware/phase8-m8-relative-protection-r1/SHA256SUMS.txt` 同理排除自身。
判读依据见 `host/stdout_crlf_normalisation.json` 的 `blob_identity_policy` 字段。

## 10. 现场候选包（可执行，只跑到离线）

`field_candidate_package.md` + `field/`（`plan.json`、`M8R1Field.ps1`、`m8r1_serial_chain.py`、
`fixtures/`、`needles.json`、`offline/<run_id>/`）。§4.1 的 12 条更正与 §4.2 的 7 步演示路径
都落成了可判定的合同位与场景，本轮真跑的只有离线自检：

```
powershell -NoProfile -ExecutionPolicy Bypass -File field\M8R1Field.ps1   → 退出码 0
M8FIELD RESULT: PASS(离线自检, 无设备动作)
```

- 计划合同 14 位（`F-01…F-13` + `G-01`）在 SHIP 计划上全 PASS；`F-05` 是 AST 静态判据
  （源码里不存在"打开之后写串口属性"的语句形态），`F-13` 要求每条被断言的文本都在
  `needles.json` 里有固件 `file:line` 出处。
- 9 个元探针全 META-OK（每个只在指定合同位造成一次失败，无附带损伤）。
- 10 个场景全 META-OK：`POS→COMPLETED`，其余 9 条分别命中
  `READBACK_HASH_MISMATCH`、`NO_BYTES_BEFORE_DEADLINE`、`TOTAL_DEADLINE_EXCEEDED`、
  `QUOTA_EXCEEDED:motor_run`、`QUOTA_EXCEEDED:approved_reboot`、
  `MANUAL_GATE_DENIED:cruise_reached=no`、`PROFILE_NOT_RESTORED_AFTER_SAVE`、`STUB_MISS`、
  `REAL_TRANSPORT_REFUSED`。
- 台账原文：`host/field_selftest_console.txt`（UTF-8 控制台字节）、
  `host/field_selftest_result.json`（脚本自写的机器可读件）、
  `host/field_offline_run_manifest.json`（逐场景子目录与 285 件产物的字节身份）。
- 串口真实分支（`--transport real`）需要令牌匹配，本轮从未走到，也从未 `import serial`；
  场景 `REAL_UNAUTHORIZED` 证的正是"没令牌就进不去"。

**自检过程中的两次失败已如实登记**（不重造历史）：第一次假端口把内部状态写入也一并拦下
（`AssertionError` 让 Python 崩溃被记成 `INFRA:`），第二次 `port_silent` 注入因为响应仍在
喂入而假通过（`PORT_SILENT` 得到 `COMPLETED`）；两条都是判据本身不足，修好之后才有上面这行 PASS。
失败轮的输出留在仓外工作区，不入库，以免被读成最终状态。

