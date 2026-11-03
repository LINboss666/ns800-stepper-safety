# M8-R6 离线修复轮 · 审核交接（给云端 A）

任务书：`NS800_M8_R5C_Review_and_Offline_Repair_20261103.md`（独立复审 + M8-R6-OFFLINE-RECOVERY）
基线 → 头：`535459f84724cdb755af00ee4dea4b3f42c93d45` → 本轮交付提交（见 JSON `delivery_head`）
分支：`feature/m8-sg-adc-relative-protection`（个人修复分支；不 merge / 不 rebase / 不 force / 不动冻结引用）
执行记录全文：`reports/reviews/phase8-m8-r6-offline-recovery/run_record.md`

## 1. 一句话结论

复审点名的三条（Safety 停在 RUN 的产品缺陷、单次打印超 127 B 被控制台裁断、缺一本跨 dispatch 的
批准额度账）都已改到**可被主机复算**的位置，并各带反向验证；**没有一项是硬件验证**，
本轮设备动作 0，新镜像没进板。

## 2. §7.2 产品状态收口

- 修前复现（真实产品函数，同一套 S2 夹具）：新组 `disarm_ready` 10 个必需 id 里 **5 条 FAIL**，
  形状全是 `disarm rc=0 mot.state=0 armed=0 hz=0/0 ss=RUN`，后果条是 `gate_mask=0x2 arm rc=-8`
  （`0x2 = MOT_GATE_NOT_READY`）——与现场残留同形。原文 `host/c23_before_fix.txt`。
- 根因两条，都在复现里定位：① 成功关断与 Safety 收口不是同一笔提交；② **`sp_peek_held()` 的
  字段拷贝是半截的**，`ready_ok` 读的是未初始化栈内存（`confirm_epoch` 曾打出 6617296 这种值）。
  补全拷贝后同一判据立刻翻绿 —— 这条是"改了没生效"逼出来的，不是先想到的。
- 修法：`mot_commit_gated()` 先取 `safety_lock_take_checked()`（失败 fail-closed + 具名打印）再取
  `mot_lock`，窗口内一份快照同时算 `close_clean`（`state_after==MOTOR_IDLE`）与 `ready_ok`
  （pwm 结果/代次 + permit 确认电平/代次 + inhibit/exhausted/motor_stop_latched 全清），
  两者同代成立且 `safety_transition_held(epoch, SAFETY_READY)==RT_EOK` 才收口；
  `if (ready_ok) mot.drv_en_request = 0u`（旧影子明确同步）。不是无条件 `safety_transition(READY)`，
  也没给旧动作重采 epoch 续命，锁顺序没变。
- 反例六条 C23d/e/f/g/h/h2（关断失败、许可未确认、旧代晚到、锁存/inhibit、取锁失败、失败后重试），
  全在 `host_ovr` 真执行；runner 通过时只报组台账：`必需=10 唯一出现=10 pass=10 fail=0 自洽=1`。
- 回归：`S2 PASS rc=0`（20 组，host_ovr 期望用例 118→**128**）、`REVERSE=1 PASS rc=0`（八项注入
  全部命中预登记失败身份）、`S1 PASS rc=0`。三份原文都在 `host/`。

## 3. §7.3 长打印 + 字节门

- 机制在 `kservice.c:411-413`：单次 `rt_kprintf` 被裁到 127 B。口径改成**每次调用 ≤120 B**
  （UTF-8 字节 + 数字最宽展开 + 换行）。
- 现场路径上被字节证实的两条：`tmc2209.c` CONFIGURED（单条最坏 **378 B** / 现场量得 132 B）
  拆 8 条；`safety_thread.c` LIMIT inputs（最坏 **162 B** / 现场量得 250 B）拆 2 条。
  我自己第一次拆出来的三处"短行"其实还是 150/166/186 B，是 `line_width_check.py` 抓的。
- 常驻判据：`host/line_width_check.py`，`--selfcheck` 要求两条已知超标样本必须被判红（rc=0），
  全量 `PASS (bad=0)`；`REPO_SCAN >120B =70` 是**登记未修**（带 `SCOPE_NOTE`，不声称全仓达标）。
- 执行器字节门：原始字节先落盘 → 对**未解码字节**查危险文本 → 数裸 NUL 硬停止；原件逐字节不改。
  `byte_corruption_recovery` 合同退役（申报即 `RETIRED_RECOVERY_CONTRACT`，计划门"申报即判红"，
  运行时不再产生 `BYTE_CORRUPT`）。正控 FS80 干净响应仍通过；FS81/82/83/84 是四类硬门反例。
- 派生新离线候选计划 `field/plan_core_r6.json`（165522 B，sha `6e0b97f251a61493…`，`--check` 幂等，
  离线门 `checks=232 fail=0`）；`plan_core_r5c.json` 与 26 场原件、台账一字未回改。

## 4. §7.4 跨 dispatch 批准台账（这条是复审认定的缺失控制）

- 账本按**批准身份**命名，位置在 `--out` 之外（默认 `%TEMP%\m8r6_approvals`，现场用
  `--approval-root` 指可持久化位置）：换输出目录、重开进程都不清零。
- 身份 = 批准令牌 + `plan_sha256` + `executor_sha256` + `image_sha256`（+ caps），
  **全部当场从真实文件算**；S0 现在把这些连同 `approval_id` 记进 `S0_FACTS`（15.7 埋的那条已落）。
- **先落盘预占再动作**：`tmp + os.replace` 原子换入 + `O_CREAT|O_EXCL` 互斥锁；
  `commit()` 只摘 pending、**不退款**，崩溃后未确认预占按已消耗计入。
- 真实传输的门在 UID/工具/端口之前：`APPROVAL_ID_REQUIRED` / `APPROVAL_LEDGER_ABSENT` /
  `APPROVAL_LEDGER_CORRUPT` / `APPROVAL_IDENTITY_MISMATCH`，一律 REFUSED 且**不自建满额账**；
  唯一"从空开始"入口是显式 `--approval-init`，重复 init 被拒（覆盖等于退款）。
- 阶段级工具（download/readback/reset/reboot）与人工重启门也走同一本账 —— R5-C 的 14/2 就是从
  这条"只扣本次内存账"的路漏的。撤能预留独立且**预约失败仍发送**（安全动作优先），
  只落成 `…:APPROVAL_QUOTA_EXHAUSTED_SENT_ANYWAY` 具名披露并使本场非成功。
- 六项要求的验证 FS85–FS90 + 两个元负控 FS91/FS92（摘掉绑定门 → FS85 转失败；摘掉 cap 判定 →
  FS87 转失败并真的超到 5>3）。CLI 级实证 `host/cli_approval_ledger_demo.txt`：
  rc=0/0/**1**/**4**/**1**，第二包停在 `tmc_config_apply used=2 want=1 cap=2`、13 阶段 NOT_RUN。
  这一步顺带抓出两个只在真 CLI 下才露的装配缺陷（`--approval-init` 被夹具门挡住、
  父目录不存在被误报成 `APPROVAL_LEDGER_LOCKED`）。

## 5. §7.5 构建（motor 候选只建一次，未下载）

`UV4_EXIT=0`，`0 Error(s), 0 Warning(s)`，`V6.24 (ARMCLANG)`，`Build Time Elapsed: 00:00:13`
`Program Size: Code=205570 RO-data=85970 RW-data=2188 ZI-data=85592`
差（对上一轮同一 Target）：**Code +808 / RO +392 / RW 0 / ZI 0**。
新件归档在 `reports/firmware/phase8-m8-r6-offline-recovery/`（BIN `d70a9180…`/292116 B、
AXF `3c113310…`/1773504 B、map `40f8e270…`/2110427 B；manifest 由 `host/archive_build_r6.py`
当场算并做拷贝后逐字节比对）。编译输入登记成**双身份**：`sha256`/`bytes` 是仓库 blob（远端逐 blob
回读比的就是这两个），`compiled_from_disk_bytes*` 是编译器当场读到的磁盘字节；两者之差只来自
autocrlf（motor.c 2555 B / stop_priority.c 1544 B / rtconfig.h 454 B 的 CR 被折掉，
本轮新写的 tmc2209.c 与 safety_thread.c 磁盘即 LF 故相同）。上一轮 r4 manifest 只登记磁盘那份，
远端回读就把 rtconfig.h 报成 hash_mismatch —— 本轮不再留这条尾巴，r4 那份历史件不回改。
上一轮已逐字节证在板的 `dab356c6…` 未被覆盖。

## 6. 本轮汇总数字

| 项 | 结果 |
|---|---|
| 现场离线自检 | `cases=92 pass=92 fail=0`，`negative_injections=15`，`hardware_actions=0` |
| 集中复核 | `CHECKS=120 problems=0`，元负控 `6/6` 与 `9/9` 全部 fired |
| 主机 C | S2 / REVERSE / S1 全部 rc=0（host_ovr 128 条） |
| 宽度检查 | `--selfcheck` rc=0 + 全量 rc=0（70 处登记未修） |
| R6 候选计划 | 幂等，离线门 232 项 fail=0 |
| 设备动作 | **0** |

## 7. 仍未验（不要把这几条读成已过）

1. 产品修复**未在板**：新镜像 `d70a9180…` 没烧过、没回读过；`Safety_RUN_after_disarm` 只到
   "缺陷已确认 + 主机复现 + 修后主机通过"。
2. 现场那次的**精确交错序列仍是 NOT_PROVEN**；坏指针 `0x00bd80b0` 的写入者仍未定位。
3. `threshold_validation=NOT_ESTABLISHED`、`relative_coverage_active=NOT_ESTABLISHED`、
   `REL_IMPACT=NOT_REACHED`、`load_means=NOT_PROVIDED` 原样保留；空载建不出阈值
   （`SG_HEADROOM_INSUFFICIENT sg[14..26]`）这条产品事实本轮没碰。
4. idle1024 的带载最坏栈余量未证；真实触发后的锁存拒绝与人工恢复闭环未证。
5. 旧批准 `4cb9dac…` 不结转、不续签；`plan_core_r6.json` 是**从未执行**的派生候选，
   它 `approval.status=REQUIRES_NEW_APPROVAL`、`hardware_actions_authorized_by_this_package=0`。
6. 一个新边界请注意：换 plan/executor/image 任一身份，同一批准身份**不能**再沿用旧账
   （`APPROVAL_IDENTITY_MISMATCH`）。续接包若要合法共享总账，必须在批准记录里显式覆盖，
   或者由 A 明确指定账本身份 —— 这是本轮按 §7.4 "身份变更不能默默当同一批准"的字面实现。

## 8. STOP

本轮到此停：没有请求 GO、没有 `safe_for_enable`、没有申请任何硬件额度。
下一步（现场包）由 A 按新代码 + 实际工况 + 操作者设备状态制定；
任何真跑之前需要先离线 `--approval-init` 落一本绑到**新批准身份**的账。
