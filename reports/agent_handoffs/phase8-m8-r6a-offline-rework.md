# M8-R6A 离线返修轮 · 审核回执(给云端 A / 操作者)

- 任务书:`NS800_M8_R6_Review_REWORK_20261103.md`(A 对 R6 交付的独立复审,裁定整体 REWORK、继续停在离线)
- 基线 = 任务书指定的**实际交付完整头** `44e493cea9bbbc35e18394ca23dd1a8502519448`
- 软件输入提交 = `9e2eb9cfe26688ae8ae57ece248fcf089433b7e1`;证据提交 = 本文件所在提交(其后顺序追加)
- 分支 `feature/m8-sg-adc-relative-protection`;未做 reset/clean/rebase/force,未新建分支,未碰冻结引用
- **本轮设备动作全部为 0**(不开串口、不连 DAP、不烧录/回读/复位、不提示重上电、不 learn/zero/起转/save)
- `safe_for_enable = false`;本包不携带任何硬件额度,旧批准不结转,也**没有**因为修了账本就开立可用的新现场批准

## 1 四项必修的落点与实测

| # | 落点 | 修前(A 的实测)/ 修后(同一输入) | 判据在哪 |
|---|---|---|---|
| R1 | EXEC `ApprovalLedger`(创建 vs 读取分离 + 严格校验) | 删身份 SHA → snapshot **正常返回** / `APPROVAL_LEDGER_CORRUPT`;删整个 `spent` → 再预占**成功** / `CORRUPT`;`spent=-1` → 再预占变 0 并出 `apply#0` / `CORRUPT` 且文件保持 -1;绑定耗尽后删文件 → **自建满额账** / `APPROVAL_LEDGER_ABSENT` 且不重建 | `host/r6a_before_after.txt`(rc=0)+ FS93–FS100 |
| R2 | EXEC `exec_session`/`dispatch_plan`/CLI + `QUOTA_COMMAND_KEYS` | cap apply=0 且步骤不声明 quota → **照发** `tmc_config_apply CONFIRM`、开口 1 次 / `REAL_SESSION_REQUIRES_APPROVED_PACKAGE`、开口 0、TX 空;CLI 单会话 real → rc=4 且**不建输出目录** | FS101–FS108(含 FS107 摘表元负控) |
| R3 | EXEC `guard_partial_bytes`/`guard_stream_bytes` + `split_rows`/`_cleanup_read`/观察窗/pre | 清理用的 motor_status 多一条独立裸 NUL → `disarm=CONFIRMED_DISABLED`、`cleanup_errors=[]` / `UNKNOWN` + `motor_status:NUL_BYTE_IN_RESPONSE` + `success=False`,原字节 NUL 仍是 1 个 | FS109–FS114(FS114 同时摘两个门 → 退回旧行为) |
| R4 | `APP/motor.c` `mot_commit_gated()` 的 withheld 打印 + `line_width_check.py` | 单条最坏 285 B(检查器保守口径;A 口径 237 B)/ 拆 4 次,最长 101 B | `CHANGED_SITES` 从 `git diff` 新增行取本轮 18 个站点逐条量宽,**空扫描即判失败** |

细节、命令与退出码、边界全部在 `reports/reviews/phase8-m8-r6a-offline-rework/run_record.md`(§17.0–§17.8)。

## 2 构建与镜像身份(单次、只建 motor 候选)

`UV4 -r project.uvprojx -t rt-thread-motor-m1-candidate` rc=0,`0 Error(s), 0 Warning(s)`,
`Code=205594 RO=85994 RW=2188 ZI=85592`(与 R6 差 Code +24 B、RO +24 B)。

| 件 | bytes | sha256 |
|---|---|---|
| `reports/firmware/phase8-m8-r6a-offline-rework/rt-thread-motor-m1-candidate.bin` | 292164 | `5ae4110d14fdbb40f49cc6f0deca474cf6d0b33b3ee9305e83681dbdd4a53825` |
| `…/rt-thread-motor-m1.axf` | 1773648 | `581f1155166374cf96cda1281320dbcfd31968f1bf5a898a6dd0e2a804a52df1` |
| `…/rt-thread-motor-m1.map` | 2110730 | `4f58a3eea493d8c532ecceba3d16db1e59750066ec75cede8647197563d412c3` |

新件**从未上板**(`new_image_on_board=false`);R6 的 `d70a9180…` 与板上逐字节证过的 `dab356c6…`
都原样保留(manifest 里当场复算 R6 归档件哈希相符)。**不能**把本包换批准 ID / `approval-init`
当成修复镜像现场包:`plan_core_r6a.json` 的 `r6a_candidate_image.bind_state =
NOT_BOUND_IN_THIS_PACKAGE`,`image_binding` 仍指向在板那件。

## 3 冻结件未回改(逐条复算)

| 件 | 状态 |
|---|---|
| `plan_core_r5c.json` | `05bcf96aa7a6bf89…` 未变;`apply_field_window_fix_r5c.py --check` rc=0 → `executed_bytes_unchanged=True`、`ORDER_ONLY_DELTA` |
| `plan_core_r6.json` | `6e0b97f251a61493…` 未变(它已被上一轮的 CLI 证据用过);R6A 另写新文件、新路径 |
| R5-C 的 26 场原件/台账 | 未动;r3 轮清单重算后**只有两条脚本条目**移动(逐行 diff 已核) |
| R6 归档镜像三件 | 未动,`archive_build_r6a.py` 当场复算 `d70a9180…` 相符 |
| 派生工具 `line_width_check.py`、`idle_learn_check.py`、`staged_preflight_batch.py` | 按 §6/§7 要求扩展;所在轮的 `SHA256SUMS.txt` 作为**派生件**同步重算(清单跟随数据) |

## 4 复跑范围(只跑受影响项)与退出码

`ONLY=disarm_ready` S2 → rc=0(host_ovr 10/10 PASS,target 按声明 SKIP);`REVERSE=1` → rc=0
(八项注入均命中预登记业务失败身份);`m8r3_field_selftest.py` → rc=0
`cases=114 pass=114 fail=0 negative_injections=17 hardware_actions=0`;
`line_width_check.py` 与 `--selfcheck` → rc=0/0;`r6a_before_after.py` → rc=0;
`derive_plan_r6a.py` 写/拒/`--force`/`--check` → 0/1/0/0;`--validate-plan plan_core_r6a.json` →
rc=0 `checks=232 fail=0`;`idle_learn_check.py` → rc=0 `CHECKS=120 problems=0`(元负控 6/6 + 9/9);
`archive_build_r6a.py` 与 `--check` → rc=0/0;`staged_preflight_batch.py --selfcheck` → 命中 1 条,
正式跑 → `problems=0`。S2 其余 19 组与历史 5943 条清单未重跑(任务书 §7 不要求)。

## 5 保持不变的旧裁定与剩余未验

阈值、ACTIVE 覆盖、REL_IMPACT、`load_means`(仍 NOT_PROVIDED)、idle1024 带载最坏栈余量、
现场精确线程交错(NOT_PROVEN)、`0x00bd80b0` 写入者未证 —— 一律保持;既有四项装配前提仍 PROVIDED,
未重问;算法/阈值/速度/电流/引脚/锁存恢复资格未扩展。

未验清单:`product_field_fix`(修后的 disarm 收口、字节门与额度门都只有主机/内存证据);
新镜像 `5ae4110d…` 未上板、未回读;`overall_offline_release` 待 A 复审;本轮所有反例都是
主机/内存输入构造,**不是新的现场异常**。

## 6 本轮自曝的文本缺陷(不影响判据)

1. 上一轮提交信息 `a8d9033` 与交接件里 `APPROVAL_LEDGER_ROOT_UNSABLE` 拼写有误,正确名
   `APPROVAL_LEDGER_ROOT_UNUSABLE`;按 §7 随本轮实质修复登记,不回改历史提交/已归档 JSON。
2. 本轮软件提交 `9e2eb9c` 的正文里 R3 段有一处括号排版错(`清理_split_rows … 对完整原始字节判]`),
   文字缺陷,不影响任何判据或字节;证据提交里如实记这一条。
3. `r6a_before_after.txt` 入库前做了 CRLF→LF 归一(与上一轮 CLI 实证同一处理),对照的**内容**
   未改;磁盘字节 == blob 字节由本轮 `.gitattributes -text` 保证。
