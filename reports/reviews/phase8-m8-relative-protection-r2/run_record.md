# M8-R2 回读记录（phase8-m8-relative-protection-r2，离线轮）

## 0. 输入与产出身份（逐条可由仓库/远端复算）

| 项 | 值 |
|---|---|
| 被复审交付 | `b4d4079c8a2ae22405fa436d4513d0b2807839f8` |
| 上一轮复审交付 | `f939363449c528892fb3d9978d73e085cfa90c91` |
| 本轮软件输入 `code_commit` | `207b3747efa5d54f7549dca79ececc81c4766794`（6 个产品文件 + 2 个验证链文件；提交后工作树对已跟踪文件干净） |
| 镜像实际取自 | `207b3747efa5d54f7549dca79ececc81c4766794`（每个目标各一次 Rebuild All，取自上述提交的工作树，无二次构建） |
| 本轮起点 | 在 `b4d4079` 之上直接续作；无 reset / rebase / merge / force；`main 1b3757ee`、`phase7/full-embedded-software 6d3fbc55`、`phase8/tmc-drive-config 1696c59f`、`debug/tmc-uart-rootcause-20261028 f1aa2e83`、tag `097935fe…`/peel `e40a9b95…` 均未移动 |
| 镜像归档 | **新**目录 `reports/firmware/phase8-m8-relative-protection-r2/`；R1 与 M8 的镜像目录一字未动 |
| `.gitattributes` | 按审核裁定保留（KEEP_BYTES）；本轮只新增 R2 路径的窄规则并在文件里写明范围，未加全局 `* -text`，未做全仓 renormalize |

主机链/产品输入身份（Git blob，按 `code_commit` 复算）：

- `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/diag_relative.c` → `926f0dfab4251ab61c7477119ff3bb5ff570115f`
- `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/diagnosis.c` → `0519345849c48f28fb8b50dcaf8391b013200061`
- `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/sensor_service.c` → `788a4604f5c33cbd7fef67dc6bff65321dd386ef`
- `tests/host/rel_main.c` → `8a2d87471f1f2c9fb26259821e2c06721e074173`
- `tests/host/run_diag_relative_m8.sh` → `9cc422a4afa04e790b8f2a81e720b41f3a85e9bb`
- `tests/host/shim_rel/rtdevice.h` → `6a15e18cb979c1ada3cf95f6161e746b6cd13d18`
- `tests/host/shim_rel/rtthread.h` → `69597264cdfa1448ab156d301bf6fb08f55e5860`

编译器：`tdm64 gcc 4.9.2`，`-std=gnu11 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter`。

## 1. 本轮修了什么，落点与判定

| 审核项 | 落点（`code_commit` 内） | 判定 |
|---|---|---|
| R2-A.1 控制锁必须真拒绝 | `applications/diag_relative.c` `rel_ctl_init/rel_ctl_take/rel_ctl_give_held/rel_prod_commit`（"初始化过"与"这次拿到"分开；失败一个字段都不写，错误回调用方） | `REL45` 全绿；反向 `RV8` META-OK |
| R2-A.2 不释放未持有的锁 / 事故票据 | `rel_ctl_give_held` 只在 take 成功路径调用；票据住在引擎 `rearm_required` | `REL45`（`g_mutex_release_calls` 不变 + `held==0`）、`REL35/REL36` |
| R2-A.3 快照整份或失败 | `rel_state_collect/rel_prod_publish/rel_prod_get_snapshot` + `diagnosis.c` 状态行 `snap=/inc=/pubfail=` | `REL33`（锁死时拒绝读、恢复后镜像补齐、pub_fail>0）、`REL43` |
| R2-A.4/A.5 单一写者 | `rel_prod_init_wipe()` 只在线程起来前调用；命令侧只能 `rel_prod_commit/request_rearm` | `REL47`（拒绝路径不改状态）、`REL48` |
| R2-B1 静止门内发布模式+代际 | `diagnosis.c diagnosis_set_mode()`：最终复核→`rel_prod_commit(mode,bump_gen)`→`d_mode`→`static_end`→全部打印 | `REL47` 用夹具在"仍持门的最后一刻"回调读 `d_mode`/`gen_want`/`held`，并断言门内打印字节数==进门时；反向 `RV8` 亦命中 |
| R2-B2 新代清证据 | `rel_prod_sync_generation()` 步进前 `rel_engine_new_generation()` + 旧首因挪 `archived` | `REL48`；反向 `RV9_new_generation_keeps_old_evidence` META-OK |
| R2-B3 新帧≠运动事实 | `sensor_service.c/h` 帧内 `motion_ok/seq/tick`；`diag_relative.c` 规则 0 的 `RELCOV_MOTION_UNKNOWN`；首因记引擎复核档位 | `REL49`（引擎级未知/过期 + 生产入口 `g_snap_fail` 靠缓存 IDLE 免保护被检出）、`SNS-ST` 运动身份断言 |
| R2-C1/C2/C3/C4 | 新包 `reports/reviews/phase8-m8-relative-protection-r2/field/` | 见第 3 节 |
| R2-D 清单缺件 | `host/remote_readback.py` 改为清单优先 | 见第 4 节 |

## 2. 回归（定稿字节，一次）

- 主机链：`SUMMARY|cases=49 pass=49 fail=0 pre_bad=0 executed=49`（49 例 = 原 44 身份 + REL45-REL49 新身份），`warning_count=0`。
- 反向（9 家族，仓外一次性副本 + `NEGATIVE-INJECTION` 标记，编译成功 + rc=1 + 台账自洽 + 指定业务失败身份命中）：
- RV1_sg_new_sample_identity_gate | META-OK 命中 1 个失败身份 (实际FAIL行=7 额外=6 proc_rc=1)
- RV2_adc_relative_severe_branch | META-OK 命中 2 个失败身份 (实际FAIL行=2 额外=0 proc_rc=1)
- RV3_relative_mode_profile_qualification | META-OK 命中 1 个失败身份 (实际FAIL行=1 额外=0 proc_rc=1)
- RV4_severe_borrows_warn_duration | META-OK 命中 1 个失败身份 (实际FAIL行=1 额外=0 proc_rc=1)
- RV5_coverage_loss_protects_nothing | META-OK 命中 4 个失败身份 (实际FAIL行=7 额外=3 proc_rc=1)
- RV6_failed_relearn_keeps_credential | META-OK 命中 1 个失败身份 (实际FAIL行=1 额外=0 proc_rc=1)
- RV7_impact_independent_trip_removed | META-OK 命中 1 个失败身份 (实际FAIL行=1 额外=0 proc_rc=1)
- RV8_ctl_lock_silently_bypassed | META-OK 命中 2 个失败身份 (实际FAIL行=6 额外=4 proc_rc=1)
- RV9_new_generation_keeps_old_evidence | META-OK 命中 1 个失败身份 (实际FAIL行=2 额外=1 proc_rc=1)
- RV8/RV9 的"额外失败身份"逐条披露在 `host/regression_m8_reverse_console.txt`，不改变判定。
- R1 的 13 项反例与 44 正向、7 反向、9 工件身份仍是事实：本轮未删除任何原始件；
  审核包 `probes/original13.out.txt`、`probes/new_control.out.txt` 属被审字节，本轮的等价复算见下节。
- 变更适用的旧链（S1/S2/SD/CC/IO/M5/M6/A0）本轮**未**机械重跑：改动局限在 RELATIVE 控制层与
  REL 状态行，非输入相关；按任务书"非输入相关的旧链不机械重跑"。

## 3. 现场候选包（可执行，离线全链已真跑）

入口：`field/M8R2Field.ps1`，`-SelfTest` / `-Precheck` / `-RunField` 三个参数集互斥。
执行器：`field/m8r2_serial_chain.py`（沿用 P0 的提示符/回显/字段唯一/原始字节先落盘纪律，
沿用 M6 的首错/证据失败/清理 UNKNOWN 语义；未搬回 M6 的开口后 timeout 赋值）。

- Python 场景：`SUMMARY|cases=25 pass=25 fail=0 executed=25`（含 3 项定向检出 RV10a 去掉 5 秒硬截止、RV10b 去掉清理撤能、RV10c 去掉业务失败层）
- PowerShell：`SELFTEST_VERDICT|PASS|checks=21|failures=`（Windows PowerShell 5.1 真实执行，三个入口各一次）
- 审核方 R1 反例在本包上的等价复算：`FIELD|MISSING_PROMPT_ACCEPTED`→verdict=STOP；
  `FIELD|FAILED_WATCH_CONTINUES`→watch STOP 后 motor_arm/fault_reset 不再发出；
  `FIELD|FIVE_SECOND_CAP_NOT_ENFORCED`→start 尝试到 stop 发送 = 5.0 s（声明 5）；
  `FIELD|ERROR_AFTER_START_ONLY_CLOSES_PORT`→清理发出 motor_stop/motor_disarm/motor_status 且 CONFIRMED_DISABLED；
  `FIELD|REAL_MANUAL_USES_SYNTHETIC_YES`→real 传输强制走操作者输入并计 `operator_calls`；
  `FIELD|REAL_C_LEARN_FAILURE_ACCEPTED_AS_STEP_OK`→BUSINESS_FAILED；
  `FIELD|REAL_C_SUCCESS_MODE_REJECTED`→正控改用本轮真实 C 输出 `[DIAG] mode=RELATIVE_ACTIVE`，verdict=OK。
- 正控文本来源：`host/c_chain_output.txt`（本轮主机链真实产品输出行，155 条去重行），
  `needles.json` 的 40 条逐条由 `prove_needles()` 回到 C 源码字面量证明（`unproven=0`）。
- 台账一律独占创建（`x`）：撞名即 `LEDGER_PREEXISTING`；这条是自检里抓到"变异体读到基线台账"的假通过后加的。

## 4. R2-D：清单优先的回读

`host/remote_readback.py` 的判定顺序：先枚举**远端 blob 里的每一张清单**（`SHA256SUMS*.txt`、
`build_manifest.json`、现场运行清单里带 sha256 的条目），逐条要求"存在 + 字节相等 + 散列相等"；
`declared_but_absent` / `duplicate_conflict` / `read_failed` / `hash_mismatch` / `byte_count_mismatch`
任一命中即非成功；清单散列自身被显式拒绝。R1 那 5 件"登记但不存在"的构建日志已按具体路径
`git add -f` 进入本提交（未改 `.gitignore`）。

提交前的本机前置复算（不替代远端回读）：逐条用 `git cat-file blob :<path>` 取**暂存区里的字节**与清单比。
首轮结果：固件清单 9/9 相符；评审清单 162 条里 1 条是已删除的本地 `__pycache__` 残留（重算后消失），
6 条 `SHA_DIFF` —— `field/m8r2_field_selftest.py`、`field/m8r2_serial_chain.py`、`field/needles.json`、
`field/plan.json`、`field_candidate_package.md`、`run_record.md`：本机带 CRLF，`core.autocrlf=true`
在 `git add` 时剥成 LF，于是 blob≠清单。处置是给这 6 件所在的**具体 R2 路径**补窄 `-text` 规则
（`.gitattributes` 里带范围说明；未加全局 `* -text`，未做全仓 renormalize）后 `git add --renormalize` 这些路径，
复核为 `problems=0`；同一判据顺带复算 R1 的 336 条与 M8 的 23 条。远端逐 blob 回读仍单独跑，本机结论不作为交付判定。
原始输出归档在 `host/staged_blob_preflight.txt`（该文件自身的条目由推送后的远端回读覆盖）。

## 5. 构建产物身份（本轮唯一一次 Rebuild All / 目标）

| 目标 | rc | Program Size | 有效宏 |
|---|---|---|---|
| rt-thread | rc=0 | Code=204482 RO-data=85702 RW-data=2188 ZI-data=84824 | MOTOR=0, TMC=0, STEP=0, SAFETY=0 |
| rt-thread-measure-a0 | rc=0 | Code=211778 RO-data=88662 RW-data=2188 ZI-data=92544 | MOTOR=0, TMC=0, STEP=0, SAFETY=0 |
| rt-thread-motor-m1-candidate | rc=0 | Code=204714 RO-data=85578 RW-data=2188 ZI-data=84824 | MOTOR=1, TMC=1, STEP=0, SAFETY=0 |

| 文件 | 字节 | sha256 | 来源 |
|---|---|---|---|
| `rt-thread-a0.axf` | 1801068 | `3a4460ee607fbf0c44f4d6ff65b062a007e511b28ce046f4f4e91fb0fb8f8d5c` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread-a0.map` | 2158732 | `f7be6e7b06d1c906ca57a183d1857ed8db4288c3d4877f950cffab552134741f` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread-measure-a0.bin` | 301020 | `c95dca118bddb7531d13fa623ea22a8c540892e6671354c0f3dcb3a1268a6aa2` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread-motor-m1-candidate.bin` | 290868 | `874df24187c4153058202a4e632ba7ee8eec00a8c7b121d54165a379cd6557e3` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread-motor-m1.axf` | 1771132 | `8f5b597cc789da66259661b696fe091f5c08a4ed25cb5b181886dfef7c65df05` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread-motor-m1.map` | 2108504 | `47542f908ccb803dfd763344bb864611d0785aeb41a74cfa72bc407246c63b2d` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread.axf` | 1770648 | `d034324e0448b44406f1d62a93a356c067cc8bf53e2371207e7d17f90744e885` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread.bin` | 290764 | `fc69c8994ea71a590acd63d9249db1eef4f2cfde79675f4ed8ca068d4b06e126` | 本轮唯一一次 Rebuild All 的实际产物 |
| `rt-thread.map` | 2106944 | `dfab37a7aab2183673e3b561e3fe4a234686a4e76ab453719ca0f2973a1a29ff` | 本轮唯一一次 Rebuild All 的实际产物 |

## 6. 分级与未验证项

- `HOST_PROVEN`：判据/时序/并发纪律的 C 级结论（虚拟时钟 + 单写者夹具，不宣称真实抢占时序）。
- `OFFLINE_EXECUTED`：现场包的调度、5 秒预算、清理与撤能字段判定、协议/业务分层、额度扣减、
  动作后端调用序列 —— 全部在假端口/假动作后端下真实执行过。
- `FIELD_NOT_TRIGGERED`：任何板级异常、真实触发、真实撤能物理时延、profile 现场学习质量、
  安培标定分离度。本轮 `hardware_actions=[]`，`safe_for_enable=false`。
- `NOT_VERIFIED`：受控加载工具与作用点（`plan.load_step.verdict=NAMED_PREREQUISITE_MISSING`）；
  R6 非破坏激励未选择批准 → `FIELD_NOT_TRIGGERED`。
