# M8-R6 离线修复轮记录（M8-R6-OFFLINE-RECOVERY）

轮次目录：`reports/reviews/phase8-m8-r6-offline-recovery/`
基线（本轮唯一的起点）：`535459f84724cdb755af00ee4dea4b3f42c93d45`（R5-C 交付提交，个人分支
`feature/m8-sg-adc-relative-protection`）
输入件：复审 `NS800_M8_R5C_Review_and_Offline_Repair_20261103.md`（云端 A 独立复审 + 本轮任务书）
日期：2026-11-03

## 16.0 本轮的边界（先说清"没做什么"）

**设备动作 = 0**：没打开串口、没连 DAP、没烧录、没回读、没复位、没提示重上电、没 learn/zero/起转/
保存配置。全部工作在离线主机（Python 3 + gcc 4.9.2 主机夹具 + Keil 构建，构建只编译不下载）。
旧批准 `4cb9dac…` 的额度不结转、不伪造新批准；派生出来的 R6 候选计划 `approval.status =
REQUIRES_NEW_APPROVAL`、`hardware_actions_authorized_by_this_package = 0`。

复审 §8 的裁定原样继承（本轮不改动任何一项）：`evidence_archive=ACCEPTED_WITH_PROVENANCE_GAP`、
`product_verification=INCOMPLETE`、`executor_field_release=REWORK`、
`Safety_RUN_after_disarm=PRODUCT_DEFECT_CONFIRMED`、`exact_field_interleaving_root_cause=NOT_PROVEN`。

## 16.1 §7.2 产品状态收口：先复现，再改，再给反例

### 16.1.1 修前复现（真实产品函数，不是另写的简化状态机）

夹具 = `tests/host/sp_conc_main.c`（同一份 S2 链，真 pthread 互斥量 + 窗口内排队 ISR），被测源是
`applications/` 里真实编译的 `stop_priority.c / motor.c / step_pwm.c / safety_state.c`。
新增组 `disarm_ready`（C23a…C23h2，10 个必需 id），基线 535459f 上跑：

```
host_ovr/disarm_ready | NOT-PASS 判FAIL=5 自报fail=5 proc_rc=1 必需=10/10
S2 RUNNER RESULT: FAIL (rc=1)
```

失败原文（`host/c23_before_fix.txt`）逐条都是同一个形状 —— **关断在电机侧全成了，Safety 还停在 RUN**：

- `C23a` `disarm rc=0 mot.state=0 armed=0 hz=0/0 ss=RUN pad=0 permit confirmed=YES inhibit=no`
- `C23a2`（同一场的后果）`gate_mask=0x2 arm rc=-8 ss=RUN` —— `0x2 = MOT_GATE_NOT_READY`，
  再武装被产品自己拒了，这就是现场 `field-20261101T15xxxxZ` 里 disarm 之后 arm 一直 rc=-8 的那条。
- `C23b` 斜坡未归零就 disarm：`mot.state=0 armed=0 hz=0 ss=RUN phase=reserved`
- `C23h` 取 Safety 锁失败：`disarm rc=0 ss=RUN mot.state=0 armed=0 取锁失败次数=0`
- `C23h2` 锁恢复后重试：`重试 disarm rc=0 mot.state=0 ss=RUN`

### 16.1.2 根因（两处，都在主机复现里定位，不靠推测）

1. `mot_commit_gated()` 里 Safety 收口与电机侧关断**不是同一笔提交**：它先看 `safety_transition_held()`
   再决定，而成功关断的证据（`sp_peek_held` 的窗口快照）与 permit/pwm 结果没被当成同一件事核。
2. 更硬的一条：**`sp_peek_held()` 本身没把 `pwm` / `permit` / 首个未恢复 epoch 这些字段拷出来**
   （`stop_priority.c` 里那个拷贝列表是半截的）。于是 `ready_ok` 读的是**未初始化栈内存**
   ——第一次修就是因为这个"改完还是 ss=RUN"才暴露出来的。补全字段后同一条判据立刻翻绿，
   说明 1 的收口逻辑本来就成立，只是它的前提观察是垃圾值。

### 16.1.3 修法（只动这一条路径，锁顺序不变）

`applications/motor.c` 的 `mot_commit_gated()`：

- 先 `safety_lock_take_checked()`（失败就具名打印并 fail-closed 返回），再取 `mot_lock`；
  锁顺序仍是既有那条 `ss_lock → mot_lock → 一个短 sp 窗口`，没新增第三把锁。
- 窗口内一次性取 `sp_peek_held(&c)`，`ready_ok` 全部字段来自**同一份快照、同一代 epoch**：
  `pwm.result==MOT_STOP_API_ACCEPTED`、`pwm.epoch==pf->epoch`、`permit.confirmed==RT_TRUE`、
  `permit.confirmed_level==0u`、`permit.confirm_epoch==pf->epoch`、`inhibit==RT_FALSE`、
  `exhausted==RT_FALSE`、`motor_stop_latched==RT_FALSE`。
- `close_clean = (state_after == MOTOR_IDLE)`；只有 `close_clean && ready_ok` 且
  `safety_transition_held(pf->epoch, SAFETY_READY)==RT_EOK`（同一代仍被别的事 held 就不改）才提交
  Safety 收口。**不是在 disarm 末尾无条件 `safety_transition(READY)`**，也没有给旧动作重新采 epoch 续命。
- `if (ready_ok) mot.drv_en_request = 0u;` —— 成功关闭后旧 `drv_en_request` 影子**明确同步**（复审要求
  "明确同步或明确其历史语义"），并且 `withheld` 原因只在窗口结束后打印。
- `applications/stop_priority.c` 的 `sp_peek_held()` 补齐 `trips/merged/stale_dropped/advance_failed/
  pwm/diag_pwm/last_trip_epoch/permit/first_unrecovered(_valid)/first_permit_unrecovered/first_permit_valid`。

### 16.1.4 六项有区分力的反例（都真执行，都要求"不许清故障、不许强改 READY"）

| id | 注入 | 要求的判据 |
|---|---|---|
| C23d | 关断 API 失败 | 不收口，Safety 保持原态 |
| C23e | 许可未确认 | 不收口 |
| C23f | 旧 disarm / 旧归零晚到（代次不符） | 不收口（不许给旧动作续命） |
| C23g | 故障锁存 / inhibit 在 | 不收口，也不清故障 |
| C23h | Safety 取锁失败 | 放弃提交并具名打印（修前这条正是 ss=RUN 漏出去的那条） |
| C23h2 | 取锁失败后重试成功 | 收口成立；`rc` 如实报（不谎称成功路径没发生过错误） |

runner 只在失败时打印逐条 `CASE|…`，通过时的逐条证据是**组台账**：`必需=10 唯一出现=10 实际行数=10
SUMMARY(cases=10 pass=10 fail=0 pre_bad=0 skips=0) 自洽=1`（`host/s2_disarm_ready_after_fix.txt:46,52`）。
上面六条 id 就是这 10 个必需 id 里的六条，全部真执行且判 PASS；"没打印"不等于"没跑"，
这条台账纪律本身就是 S2 链的判据（缺席/重复/未登记都会 NOT-PASS）。

修后（同一份最终字节）：`S2 = PASS rc=0`（target + host_ovr 全部 20 组，host_ovr 期望用例 118→128，
新增 `G_disarm_ready_host_ovr` 10 条）、`REVERSE=1 = PASS rc=0`（八项注入全部命中预登记的失败身份）、
`S1 = PASS rc=0`。原文见 `host/s2_disarm_ready_after_fix.txt`、`host/s2_reverse_after_fix.txt`、
`host/s1_after_fix.txt`（每条都带 runner 自己的 run_id / 选择集合 / 实际执行集合）。

## 16.2 §7.3 长打印与执行器字节门

### 16.2.1 控制台裁剪是机制，不是猜测

`rt-thread-master/.../src/kservice.c:411-413`：`rt_kprintf` 的单次输出被截到
`RT_CONSOLEBUF_SIZE-1 = 127 B`。现场两份被裸 NUL 咬坏的响应，长度都越过了这条线。
本轮口径：**单次格式化调用 ≤120 B**（按 UTF-8 字节、数字按最宽展开、含换行）。

- `tmc2209.c` `[TMC-CFG] CONFIGURED …`：修前单条最坏 **378 B**（元负控实测），现场量得 132 B；
  拆成 8 条短调用，逐条 32–93 B。
- `safety_thread.c` `[SAFETY] LIMIT inputs config = …`：修前单条最坏 **162 B**，现场量得 250 B
  （中文按 UTF-8 字节算）；拆成 2 条，82 B + 99 B。
- 另两处本轮自己拆出来的短调用一度仍然超长（150 B / 166 B / 186 B），是 `line_width_check.py`
  抓出来的，不是人眼看出来的 —— 这条判据现在常驻：`--selfcheck` 要求"已知超标样本必须被判红"，
  全量模式 `LINE_WIDTH_RESULT PASS (bad=0)`，`REPO_SCAN … >120B … =70` 是**登记未修**的其它站点
  （带 `SCOPE_NOTE`，不声称全仓达标）。

### 16.2.2 执行器字节门（`m8r3_serial_chain.py`）

- 原始字节**先落盘**，再对**未解码字节**跑 `check_danger`，然后才数裸 NUL
  → `NUL_BYTE_IN_RESPONSE` 硬停止；原件逐字节不改（FS81 断言 `path.read_bytes() == body`）。
- 危险文本（`HardFault` / stack overflow / SYSTEM_HALT 等）优先级高于解码失败与长度判定
  （FS82：同一批字节里既有 ASCII `HardFault` 又有非法 UTF-8，报的是 `DANGER_TEXT_IN_STREAM`，
  不是解码异常）。
- `byte_corruption_recovery` 合同**退役**：步骤一申报它就 `RETIRED_RECOVERY_CONTRACT` 停（FS83），
  计划门 `PLAN_BYTE_RECOVERY` 改成"申报即判红"（FS84），运行时不再产生 `BYTE_CORRUPT` 判决。
- 正控：干净响应照样通过（FS80）。
- 状态查询白名单（`PLAN_STATUS_ALLOW`）保持整行锚定 + "该行必须被正面要求"，动作命令被拒照停。

### 16.2.3 派生新离线候选计划，历史件冻结

`host/derive_plan_r6.py` 从**已执行且散列相符**的 `plan_core_r5c.json`
（`05bcf96aa7a6bf89…`，脚本对它做只读校验，字节不对就拒绝派生）派生
`field/plan_core_r6.json`：删掉 `cfgapply`/`cfgapply2` 两处损坏恢复例外，绑定本轮执行器身份，
`approval.status=REQUIRES_NEW_APPROVAL`。已归档回包与台账一个字节没动。

- `--check` 幂等：`R6_CHECK|IDEMPOTENT|bytes=165522|sha=6e0b97f251a61493|源仍冻结=True`
- 离线门：`PLAN_VALIDATE_SUMMARY|checks=232|fail=0|msh_commands=74`
- 第一次跑 `--force` 时 argparse 直接把 `--force` 判成未知参数（写保护分支永远走不到）——
  已补成真选项，并先验证"不带 --force 时确实 R6_REFUSE_WRITE rc=1"再覆写。

## 16.3 §7.4 跨 dispatch 的批准台账（复审认定的那条缺失控制）

`ApprovalLedger` + `open_approval()` + CLI `--approval-id / --approval-root / --approval-init`：

- 账本**由批准身份命名、放在 `--out` 之外**（默认 `%TEMP%\m8r6_approvals`，现场可用
  `--approval-root` 指可持久化位置）：换输出目录、重开进程都不清零。
- 身份 = 批准令牌 + `plan_sha256` + `executor_sha256` + `image_sha256`（+ caps）。**当场从真实文件算**，
  不照抄 JSON 登记值；§15.7 埋的那条"下一版 S0 应补 plan/executor SHA 字段"就在 `s0_binding()`
  里落了：`S0_FACTS` 现在带 `approval_id / plan_source / plan_sha256 / executor_path / executor_sha256`
  加上原本就按字节复算的 `image_sha256`。历史 S0 不回填。
- **先落盘预占，再动作**：`tmp + os.replace` 原子换入 + 同目录 `O_CREAT|O_EXCL` 互斥锁；
  `commit()` 只摘 pending、**不退款**（崩溃留下的未确认预占按已消耗计入）。
- 真实传输的门：没有 `--approval-id` → `APPROVAL_ID_REQUIRED`；账本缺失 → `APPROVAL_LEDGER_ABSENT`；
  半截 JSON → `APPROVAL_LEDGER_CORRUPT`；版本/令牌/计划/执行器/镜像/cap 任一不符 →
  `APPROVAL_IDENTITY_MISMATCH`。全部在 UID 读取、工具起来、端口打开**之前**返回 REFUSED，
  且不自动新建一份满额账（唯一的"从空开始"入口是显式 `--approval-init`，重复 init 被拒）。
- 阶段级动作（download/readback/reset/reboot）与人工重启门现在也走同一本账（`reserve_stage`），
  不再是"只扣这一次 dispatch 的内存账"——R5-C 的 14/2 就是从这条路漏的。
- 撤能预留独立：`cleanup()` 预约失败**不阻止发送**（安全动作优先于额度），但落成
  `…:APPROVAL_QUOTA_EXHAUSTED_SENT_ANYWAY` 的具名披露，本会话一律不算成功。

六项要求的验证（全部 stub/内存后端，FS85–FS90）：

| 用例 | 判到的事 |
|---|---|
| FS85 | 同批准连跑两整包：一轮 `download=1` 成功，二轮 `S1=STOP/APPROVAL_QUOTA_EXHAUSTED`、工具 0 次、内存账空、账本仍是 1 |
| FS86 | 占掉最后一格后崩溃：`spent={motor_run:1}`、`pending=1`、再占被拒、台账 1 条 RESERVED + 1 条 REFUSED |
| FS87 | 双执行者交替预占：总消耗恰好 = cap(3)，第 4/5 次具名拒绝；锁被占时 `APPROVAL_LEDGER_LOCKED` |
| FS88 | 账本缺失/半截 JSON：两次都 REFUSED，端口 0 次、阶段 0 个、**没有**擅自重建账 |
| FS89 | 令牌/计划/执行器/镜像/cap 五种改动全部 `APPROVAL_IDENTITY_MISMATCH`，原账消耗不变 |
| FS90 | 三条撤能预留全耗尽仍发送 motor_stop/disarm/status，禁能由真字段证成 `CONFIRMED_DISABLED`，`success=False`，两本账都留痕 |

两个元负控（判据不是摆设）：FS91 摘掉"绑定账本"这一步 → FS85 必须转失败；
FS92 摘掉 `reserve` 的 cap 判定 → FS87 必须转失败（实测账上超到 5>3）。

CLI 级实证（真实命令 + 真实退出码，原文 `host/cli_approval_ledger_demo.txt`）：
init rc=0 → 第 1 整包 rc=0（spent 全记上）→ 只换 `--out` 的第 2 整包 **rc=1**，
`APPROVAL_QUOTA_EXHAUSTED: tmc_config_apply used=2 want=1 cap=2`，13 个阶段 NOT_RUN；
重复 init **rc=4** `APPROVAL_LEDGER_ALREADY_EXISTS`；真实传输无账 **rc=1** `APPROVAL_LEDGER_ABSENT`。
这一步是装配层检查，抓出两个只在真 CLI 下才露的坑：`--approval-init` 被"单会话必须有夹具"的
门挡住，以及账本父目录不存在时 `O_EXCL` 失败被误报成 `APPROVAL_LEDGER_LOCKED`（现改为
先建目录，建不了就具名 `APPROVAL_LEDGER_ROOT_UNUSABLE`）。

## 16.4 §7.5 构建：motor 候选只建一次

```
$ "C:/Keil_v5/UV4/UV4.exe" -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 \
    -o D:/ns800/reports/reviews/phase8-m8-r6-offline-recovery/build/rt-thread-motor-r6.log
UV4_EXIT=0
"...\build\motor_m1\rt-thread-motor-m1.axf" - 0 Error(s), 0 Warning(s).
Program Size: Code=205570 RO-data=85970 RW-data=2188 ZI-data=85592
Build Time Elapsed:  00:00:13
*** Using Compiler 'V6.24', folder: 'C:\Keil_v5\ARM\ARMCLANG\Bin'
```

与上一轮同一 Target 的资源差（`Code=204762 RO=85578 RW=2188 ZI=85592`）：
**Code +808 B、RO-data +392 B、RW 0、ZI 0** —— 增量落在代码与只读字符串（拆短打印 + 同窗提交），
静态数据没长。不重建其它 Target，不拿旧镜像充新结果。

归档件（`reports/firmware/phase8-m8-r6-offline-recovery/`，身份由 `host/archive_build_r6.py` 当场算，
拷贝后与 build 输出逐字节比对）：

| 件 | bytes | sha256 |
|---|---|---|
| rt-thread-motor-m1-candidate.bin | 292116 | `d70a9180b40c5c6a25f3692bd973ffb0309a1da5db9a2f6e0768300405a9b86b` |
| rt-thread-motor-m1.axf | 1773504 | `3c113310dc5c70e4e26e51a91a7fd95a6b5a8e1da639cdd3cbb18e79b99a62cf` |
| rt-thread-motor-m1.map | 2110427 | `40f8e27033678166f1765faad25f984336b191d697b318f915a5b17fba854e84` |
| 构建日志 | 3909 | `104c5fb8f3fc1ae4…`（全量见 manifest） |

上一轮已归档且已逐字节证在板的 `dab356c6…` 原件**没被覆盖**；新件写新目录。
**这枚新镜像没有进板、没有回读、没有板上任何一条证据** —— 它只是编译产物身份。

## 16.5 本轮数字（都在最终字节上跑）

| 项 | 结果 |
|---|---|
| 现场离线自检 `m8r3_field_selftest.py` | `cases=92 pass=92 fail=0 executed=92`，`negative_injections=15`，`hardware_actions=0` |
| 集中复核 `idle_learn_check.py` | `CHECKS=120 problems=0`，`META_NEGATIVES=6 fired=6`，`META_NEGATIVES_R5=9 fired=9` |
| 主机 C：S2 / REVERSE / S1 | 全部 rc=0（S2 host_ovr 期望 128 条，含新 C23 组 10 条） |
| 宽度检查 `line_width_check.py` | `--selfcheck` rc=0（两条已知超标样本都被判红）+ 全量 rc=0（bad=0，70 处登记未修） |
| R6 候选计划 | `bytes=165522 sha=6e0b97f2…`，`--check` 幂等，离线门 232 项 fail=0 |
| 批准台账 CLI 实证 | 5 步退出码 0/0/1/4/1，全部落在预期身份上 |
| 设备动作 | 0（没端口、没 DAP、没烧录、没复位、没起转、没 learn/zero/save） |

## 16.6 本轮**不能**声称的事（STOP 清单）

1. **产品修复未经硬件验证**。`mot_commit_gated` 的收口、`sp_peek_held` 的字段补齐、两处拆短打印
   全部只有主机 C + 离线证据；`Safety_RUN_after_disarm` 从"缺陷已确认"到"已修复"这一步需要上板，
   本轮没有。新镜像 `d70a9180…` 未在板。
2. **现场那次的精确交错序列仍是 NOT_PROVEN**。主机复现给出的是"能造成同一种残留"的一条路径，
   不是"现场就是这条路径"的证明；坏指针 `0x00bd80b0` 的写入者仍未定位。
3. **SG/ADC 阈值与相对保护覆盖仍未成立**：`threshold_validation=NOT_ESTABLISHED`、
   `relative_coverage_active=NOT_ESTABLISHED`、`REL_IMPACT=NOT_REACHED`、`load_means=NOT_PROVIDED`
   原样保留。现场空载建不出阈值（`SG_HEADROOM_INSUFFICIENT sg[14..26]`）这条产品事实没被本轮触碰。
4. **idle1024 的带载最坏栈余量未证**；真实保护触发后的锁存拒绝与人工恢复闭环未证。
5. 70 处 >120 B 的打印站点是**登记未修**（只修了现场实际路径上被字节证实的两条）；
   不声称全仓输出达标。
6. 冻结件（`plan_core_r5c.json`、26 场原件与台账、上一轮镜像）**一律不回改**；
   `plan_core_r6.json` 是派生候选，从未执行。
7. 本轮**没有**任何新硬件授权。下一个现场包要由 A 按新代码、实际工况和操作者设备状态另订，
   并且第一次跑之前必须先离线 `--approval-init` 落一本绑到新批准身份的账。
