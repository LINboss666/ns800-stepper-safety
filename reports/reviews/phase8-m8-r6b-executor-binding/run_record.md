# M8-R6B 执行器集中离线返修轮 · 运行记录

任务书: `NS800_M8_R6A_Review_REWORK_and_R6B_Task_20261103.md`(云端 A 对 R6A 的独立复审 + R6B 任务)
裁定: 整体仍 REWORK、继续离线; 接收 R6A 已完成的修复与产物证据; 新增两个已复现阻断(B1/B2),
本轮只集中修这两处执行器问题。日期 2026-11-03(北京时间)。

## 18.0 开工核对

```text
分支            feature/m8-sg-adc-relative-protection
任务书基线      aab19ff5093e177946ce957fbf2bd506e1f63ce5   (= A 本次实际 ls-remote/fetch 的交付头)
本地 HEAD       aab19ff5093e177946ce957fbf2bd506e1f63ce5   工作树 clean(开工时 git status 为空)
远端同头        ls-remote refs/heads/feature/... = aab19ff5093e…  => 无需登记"远端已推进"的额外差异
上一轮的账      44e493c(任务书基线) -> 9e2eb9c(软件输入) -> b78d9e1(证据) -> aab19ff(回读收口)
```

未做 reset/clean/rebase/amend/force, 未新建分支, 未碰冻结引用; 本轮改动全部为**执行器与其测试/工具**,
产品 C、算法、阈值、引脚、速度、电流、锁存恢复资格一律未动, 也**没有再编译**。

## 18.1 §7.2.1 修前复现(附录 B 探针, 同一输入)

探针 = 任务书附录 B 逐字节原样入库的 `host/independent_probes.py`; 只显式 init 临时账,
内存端口/内存动作后端, 全在 %TEMP% 一次性目录里跑。跑在 `aab19ff` 的工作树(即任务书的交付头)上,
输出与 A 的附录 A **逐条一致**:

| 探针 | 修前实测(原文见 `field/probes_at_aab19ff_before_fix.json`) |
|---|---|
| `clean-control` | tx=motor_start,motor_stop,motor_disarm,motor_status; closed=True; CONFIRMED_DISABLED; success=True |
| `negative-spent-control` | 同四条 tx; code=CLEANUP_RESERVE_EXHAUSTED; success=False; 三条 `…_SENT_ANYWAY` 披露 |
| `pending-name-list` | **raised=`TypeError:unhashable type: 'list'`; tx 只有 motor_start; closed=False; CLEANUP_FRAME_EXCEPTION×1; result=null** |
| `in_memory_plan_differs_from_approved_file` | **status=OK; 后端收到 `MEMORY_ONLY_UNAPPROVED_ARGUMENTS`; spent.download=1; 盘上计划未变** |
| `cli_plan_read_then_hash_interleaving` | **rc=0; 后端收到未批准 argv; file_matches_approval=True** |
| 554 B / 1176-1179-1188 B 矩阵 | 与 A 相同(正控通过; NUL/非法/危险三类具名且不证禁能, 原字节保留) |

`git cat-file` 取基线 blob 复核本轮改动面的入口件:

```text
BASE(aab19ff)|m8r3_serial_chain.py|209786 B|sha=99d6c960…   与工作树当前字节 **已不同(本轮改动)**
BASE(aab19ff)|m8r3_field_selftest.py|165107 B|sha=986c0ff6… 与工作树当前字节 **已不同(本轮改动)**
BASE(aab19ff)|plan.json 67782 B / rows.json 51944 B / r3 归档 BIN 290916 B  与工作树相同(未动)
```

## 18.2 B1 修: 账本类型损坏一律具名拒绝, 且不再切断必要撤能与关闭

EXEC `reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py` 三处:

1. `validate_ledger_doc()`: 在**成员查找之前**先验 `pending[i].name` 是非空 str
   (旧版 `p["name"] not in have_caps` 对不可哈希值抛 TypeError) => 一律
   `APPROVAL_LEDGER_CORRUPT|pending[i]_name_form=…`。同函数里其余类型敏感处(`n`/`kind`/`id`/
   `spent`/`caps`/`writes`)本就先 `is_count`/`isinstance` 再下标, 逐条复核未再发现漏口。
2. `cleanup()` 撤能预占: 除 `Stop` 外**也捕普通异常**, 记成
   `APPROVAL_RESERVE_FRAME_ERROR_<类型>_SENT_ANYWAY` 具名披露后继续发送, 后续必要撤能不再被吃掉;
   "绝不抛"的函数合同由此真正成立。
3. `exec_session()` 的 finally: 确认消耗(commit)按条捕 `Stop`/普通异常, 整段账本收尾包进
   `try/except/finally`, 端口与日志关闭放在 `finally` 里 => 任何账本错都不再跳过
   `port.close()`/`led.close()`。关端口只算交还句柄, 禁能仍只由本场新的完整 status 证明。

保持的旧合同: 不修复/清零盘上坏账(`FS115` 逐值复算登记值原样), 不退消费, 不自动迁移,
不发新额度; 清理证据不足一律 `UNKNOWN`; 首错与清理错分列。

## 18.3 B2 修: 执行对象与身份哈希来自同一份原始字节快照

四处落点:

1. 新增 `read_plan_snapshot(path)`: `read_bytes()` **一次**, 同一份字节同时给 sha256 与
   `json.loads` 的解析结果; 解析不出/不是 object => `PLAN_SNAPSHOT_UNDECODABLE`/
   `PLAN_SNAPSHOT_UNPARSEABLE`。
2. `current_identities(plan, plan_path, repo_root, snapshot=None)`: 给了快照就用**那份字节**的哈希,
   不再另读一次文件; 且先要求 `snapshot["doc"] == plan`, 否则
   `PLAN_SNAPSHOT_NOT_EXECUTED_OBJECT`(不拿快照哈希替别的对象作证)。
3. `dispatch_plan(..., plan_snapshot=None)`: `transport == "real"` 时先取快照再动任何东西 ——
   内存对象≠快照 => `PLAN_NOT_FROM_APPROVED_SNAPSHOT`, 这一刻 UID 未读、工具未起、端口未开、
   额度未预占; 核对通过后 `plan` **换成快照那份**, 后续 stages/tools/argv/quota/时间窗只从它推导。
4. `main()`: 计划改为经 `read_plan_snapshot()` 单次读盘, 同一快照同时供 `--approval-init`、
   `--validate-plan` 与 `--dispatch` 使用。

保持: 真实单会话仍只接经批准的整包(CLI `--steps` 真实路径 rc=4)、`QUOTA_COMMAND_KEYS` 集中映射
(缺申报/错键/n 不符都在发送前拒)、只读轮询不占动作额度、`REAL_DISPATCH_REQUIRES_PLAN_FILE`。

## 18.4 新增判据(7 条)与元负控

`host/selftest_final_summary.txt`(原文):

| 用例 | 断言的东西 | 实测 |
|---|---|---|
| FS115 | `pending[i].name` 为 `[]`/`5`/`{}`/`""` 四种值都落 `APPROVAL_LEDGER_CORRUPT|pending[0]_name_form`; 盘上坏账一字不改 | PASS; 元证据: 未受保护的成员查找对 `[]`/`{}` 确实抛 TypeError(说明类型门不是空转), 对 `5`/`""` 不抛但仍按非字符串键拒绝 |
| FS116 | 端到端: 起转后把盘上 `pending.name` 改成 `[]` => 无异常逸出、三条必要撤能全发、端口关闭、无 CLEANUP_FRAME_EXCEPTION、`success=False`、三条具名披露 | PASS `tx=motor_start,motor_stop,motor_disarm,motor_status closed=True` |
| FS117 | `reserve` 抛普通异常(只对 `cleanup_*`) => 三条照发、端口关闭、披露 `APPROVAL_RESERVE_FRAME_ERROR_RuntimeError_SENT_ANYWAY`、不成功 | PASS |
| FS118 | `commit` 抛普通异常 => `PORT_CLOSED` 事件仍在台账、`APPROVAL_COMMIT_FRAME_ERROR_RuntimeError` 入错误列、`disarm=CONFIRMED_DISABLED` 但 `success=False` | PASS |
| FS119 | 内存计划≠批准文件 => `PLAN_NOT_FROM_APPROVED_SNAPSHOT`、后端零调用、download 一格不扣、盘上计划仍是批准那份 | PASS |
| FS120 | **元负控**: 把快照门摘回"两次独立读取"的旧形状 => 未批准 argv 必须**重新到达后端**(证明 FS119 不是空判) | PASS `摘门后 后端调用=[['MEMORY_ONLY_UNAPPROVED_ARGUMENTS']] status=OK` |
| FS121 | CLI 级: 在**实际读盘接口**(`read_bytes`)上安排换文件交错 => 身份不符拒发、rc≠0、后端零调用 | PASS |

## 18.5 §7.2 复跑范围与退出码(只跑受影响项)

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python …/m8r3_field_selftest.py` | 0 | `cases=121 pass=121 fail=0 executed=121 negative_injections=18 hardware_actions=0 transport=stub_only port=IN_MEMORY`(R6A 的 114 条原样保留) |
| `python …/r6b_before_after.py` | 0 | `RESULT BEFORE_AFTER_OK (checks=8 fails=0)` 两条反例的修前/修后同输入对照 + 正控未退 |
| `python …/derive_plan_r6b.py` / 改坏后重跑 / `--force` / `--check` | 0 / 1 / 0 / 0 | 见 `host/derive_matrix.txt`: `R6B_REFUSE_WRITE` 在字节不同时拒绝静默覆盖 |
| `…/m8r3_serial_chain.py --validate-plan --plan field/plan_core_r6b.json` | 0 | `checks=232 fail=0 msh_commands=74`; 75 s 观测下界与 `PLAN_BYTE_RECOVERY` 无恢复例外照旧 PASS |
| `python …/idle_learn_check.py`(r4 离线链) | 0 | `CHECKS=120 problems=0`, 元负控 6/6 + 9/9 fired; P8 执行器身份改成 **四层** r5c/r6/r6a/r6b 逐层对 blob |
| `python …/r6a_before_after.py`(R6A 六组对照) | 0 | `RESULT BEFORE_AFTER_OK (fails=0)`; motor.c 该分支仍 4 次调用/最长 101 B |
| `python …/line_width_check.py` 与 `--selfcheck` | 0 / 0 | `LINE_WIDTH_RESULT PASS (bad=0)`; `CHANGE_SCAN_META\|fired\|命中 18 个站点` —— 本轮无产品 C 改动, 站点集与 R6A 相同, 未新增 |
| `python …/staged_preflight_batch.py`(提交前) | 0 | 注入错的那条 `SELFCHECK … problems=1` fired; 真实登记件 `problems=0` |

未重跑项与理由(任务书 §7.2.4 明确允许): 产品 C 与固件未动 => 不重编、不重跑 S2 其余 19 组与
`disarm_ready` 之外的固件链; 历史数千条清单不重算; 121 条自检里 FS80–FS114 作为针对段一并跑到了
(A 建议的那 35 项包含在内), 不另开"只跑 35 项"的重复轮。

## 18.6 派生模板与身份(不建现场批准)

```text
field/plan_core_r6b.json   171427 B  sha256=08fd5d522d85f05a1eb8d0c51f339ccd05c18761b85990d6454022d6686069bb
  package_status = R6B_DERIVED_NOT_EXECUTED
  approval.status = REQUIRES_NEW_APPROVAL, hardware_actions_authorized_by_this_package = 0
  derived_from    = R6A 的 plan_core_r6a.json(6e0b…/556a044e… 冻结校验后派生, 源件字节未动)
  identity_binding.executor = 215704 B / 84df1306f48e947349b9669577a6faf03273f22fe47f2f87407bf54f90a66d1b
  image_binding   = 仍 dab356c6…/290916 B(已在板逐字节证过); r6b_image_note.rebuilt_this_round = false
本轮改动件身份:
  EXEC            215704 B  84df1306f48e9473…  (盘上字节 == 待提交 blob, 由 staged 预检逐条核)
  自检夹具        180687 B  b41b3fe83d367607…
镜像三件未新增: 5ae4110d…/292164 B(R6A 候选)、d70a9180…/292116 B(R6 候选)、dab356c6…/290916 B(在板)
```

## 18.7 复跑对上一轮派生输出的影响(如实登记, 不泛称"什么都没动")

`idle_learn_check.py` 的 `CHECKS=120` 复跑会把 **r4 那轮的派生输出**重新写到盘上, 本轮按 A 复审 §6
的口径**保留这些合法派生更新**(不假装什么都没变, 也不去回滚), 并逐条看清改了什么:

```text
6 件 field/dispatch_stub_*.txt + host/idle_learn_console.txt + host/idle_learn_check.json
差异只有三类: ① 仓外临时 run_dir 的随机名(idlecheck_vanplaae -> idlecheck_k3…);
             ② 输出里登记的执行器身份 99d6c960… -> 84df1306…(本轮 EXEC 确实变了);
             ③ idle_learn_check.json 里"本轮改动=15 项" -> "6 项" —— 那是检查器按**当时工作树**
                现数的改动项数, 不是 r4 那轮的事实(这个字段本身就随环境漂移, 已在防复发清单里)
未动的: r4/r5c 计划、26 场现场原件与台账、镜像归档件、具名冻结件 —— `git status` 对这些路径为空
派生清单跟随数据: r4 / r3 / r6b 三份 SHA256SUMS.txt 作为派生件重算(清单不哈希自身)
```

防复发口径(给下一轮): 检查器把输出写到**本轮**目录; 复跑历史链之后, 先 `git status` 看清哪些
派生件被动过, 再决定保留(合法派生更新)还是还原(无意义抖动), 不写"所有旧轮派生报告都没动"这类
泛称。上一轮 R6A 回执里那句"8 件已还原"只描述当时的处理, 其后的提交里 R4 派生输出确有更新 ——
按 A §6 的更正记录在此。

## 18.8 状态与边界(保持复审 §8 的全部裁定)

```text
R6A_evidence                          = ACCEPTED
R1_approval_ledger_normal_paths       = HOST_TARGETED_TESTS_PASSED
R1_cleanup_exception_isolation        = REWORK_DONE_OFFLINE(R6B)
R2_single_real_session_gate           = HOST_TARGETED_TESTS_PASSED
R2_full_dispatch_snapshot_binding     = REWORK_DONE_OFFLINE(R6B)
R3_cleanup_byte_integrity             = ACCEPTED_OFFLINE(不等于真实串口已验)
R4_new_error_print_width              = ACCEPTED_OFFLINE(新镜像仍未上板)
R6A_candidate_image                   = ARCHIVED_NOT_ON_BOARD
disarm_normal_close                   = HOST_TARGETED_TESTS_PASSED
product_field_fix                     = NOT_VERIFIED
overall_offline_release               = 待 A 复审
safe_for_enable                       = false
new_hardware_authorization            = 0
device_actions_this_round             = 0
next_work                             = 等 A 复审(后续现场包本轮不制定)
```

保持不动的旧裁定: 阈值未建立、ACTIVE 覆盖未建立、REL_IMPACT 未到达、`load_means` 仍 NOT_PROVIDED、
idle1024 带载最坏栈余量、现场精确线程交错 NOT_PROVEN、`0x00bd80b0` 写入者未证;
四项装配前提仍 PROVIDED, 未重问。本轮所有反例都是主机/内存输入构造, **不是新的现场异常**,
也不意味着旧现场确曾发生这两个交错; 修后的账本门与绑定门仍只有主机证据。

私有材料: 未读 UID、未打印任何 48-hex 私有 UID(探针里 download argv 的 `-u @UID_FILE` 只是占位串,
且由内存动作后端替身接收, 从未执行)。临时账本与临时输出目录全在 `%TEMP%` 下的一次性路径里
(探针 `a-r6a-probes-*`、对照器 `ns800-r6b-ba-*`、自检 `m8r3-field-selftest-*`), 仓库工作树没有
被这些探针写过; 仓内只留脱敏后的 JSON 原文与断言摘要。

## 18.9 提交、推送与独立远端回读(本轮收口)

```text
push        origin/feature/m8-sg-adc-relative-protection  aab19ff..180cce8   常规 fast-forward, 无 force/rebase
软件输入    ecfd055e4acb3e99772228aa7087a2ea5b2c190d      (EXEC + 自检 + r4 工具 + r6b 模板/证据)
证据        180cce894b71cfbe250c0b5e49c34259119f31d9      (对照原文 + 派生清单 + 交接件)
设备动作      0(本节只跑 git fetch / git cat-file / 主机测试)
```

定向逐 blob 回读 `host/remote_readback_r6b.py` 打在 `180cce8`(rc=0):

```text
R6B_REMOTE_READBACK_SUMMARY|tip=180cce894b71|checks=28|fails=0|not_checked=0
device_actions=0|serial_ports_opened=0|dap_connections=0|keil_builds=0|reads_from=remote_git_protocol_only
```

覆盖: 四份清单逐条对远端 blob(r6b 13 / r3 5137 / r4 394 / r6a 13, mismatch=0)、候选模板内部申报的
执行器/在板镜像三件/派生源/R6A 候选/不重编与零额度声明、交接件申报的三件产物身份与"顶层不自证字节"、
六件冻结旧件(R5-C `05bcf96a…`、R6 `6e0b97f2…`、R6A `556a044e…`、`5ae4110d…`/`d70a9180…`/`dab356c6…`)、
**基线头 `aab19ff` 上的执行器 `99d6c960…/209786 B`**(按 SHA 定点 fetch 后现算, 正是 §18.1 修前那一栏的输入)、
软件输入提交 `ecfd055` 里的 EXEC == 模板登记值, 以及"证据提交没有偷改执行器"(tip 与该提交的 EXEC 逐字节相同)。

通用全仓回读(`phase8-m8-relative-protection-r3/host/remote_readback.py`, rc=0) 同一头上:
`remote_files=10853 declared=6686 verified=6619`;冻结的 r3 范围 `entries=5149 verified=5149 problems=0`;
`SCOPE_PRIOR|problems=72`(cross_manifest_sha_conflict=3、declared_but_absent=53、hash_mismatch=6、
duplicate_conflict=2、suffix_ambiguous=8)—— **与 R6A 收口那次同类别同数量, 本轮零新增**;
那三条 cross_manifest 仍是旧轮 manifest 之间的登记口径差(R6 那份记它自己构建当时的 motor.c blob;
R4 那份记 `rtconfig.h` 的磁盘字节), 本轮没有新增构建件, 故没有新的 R6B 侧冲突。

两份原文入库: `remote_readback_at_180cce8.txt`(定向 28 项) 与
`remote_readback_generic_at_180cce8.txt`(全仓)。回读自己的新头只做一次并在回执里给结论,
不再为"回读这份回读"继续追加提交。

## 18.10 收口提交前的最后一步(本节即最后一步)

- 交接 MD `§7 推送与独立远端回读` 由占位符改为实测数字(推送区间 `aab19ff..180cce8`、
  定向 `checks=28 fails=0 not_checked=0`、全仓 `declared=6686 verified=6619`、
  `SCOPE_R3 problems=0`、`SCOPE_PRIOR problems=72` 与 R6A 同类别同数量 = 残差口径而非本轮回归)。
- 交接 JSON 追加 `remote_readback` 块后重生成: 18440 B `3075c409f2669846…`, keys=33。
- 本轮清单跟随数据重算: `SHA256SUMS.txt` 13 → **16 条**(新增 `host/remote_readback_r6b.py` 与两份
  回读原文), 清单自身不入清单。
- `staged_preflight_batch.py`(本批 8 件全部 `git add` 之后) → `STAGED_PREFLIGHT_TOTAL problems=0`,
  r6b 清单 `entries=16 problems=0`;四条 LF、无 BOM 逐文件实测 CR=0。
- 本步之后只允许**一次**提交与推送, 再对新头跑一次定向回读并把结论只写进会话回执
  (§7.3: 不为回读自己的新头无限追加提交)。
