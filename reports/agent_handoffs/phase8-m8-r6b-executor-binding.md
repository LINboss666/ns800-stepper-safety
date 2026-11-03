# M8-R6B 执行器集中离线返修轮 · 审核回执(给云端 A / 操作者)

- 任务书:`NS800_M8_R6A_Review_REWORK_and_R6B_Task_20261103.md`(裁定:整体仍 REWORK、继续离线;
  接收 R6A 的证据;新增两条已复现阻断;**下一轮只集中修这两处执行器问题**)
- 基线 = 任务书 §2 指定的实际交付头 `aab19ff5093e177946ce957fbf2bd506e1f63ce5`(开工时本地同头、工作树干净、远端同头)
- 软件输入提交 = `ecfd055e4acb3e99772228aa7087a2ea5b2c190d`;证据提交 = 本文件所在提交(其后顺序追加)
- 分支 `feature/m8-sg-adc-relative-protection`;未 reset/clean/rebase/amend/force,未新建分支,未碰冻结引用
- **本轮设备动作全部为 0**(不开串口、不连 DAP、不烧录/回读/复位、不提示重上电、不 learn/zero/起转/save);
  也**未重编固件**(§7.1:不需要改产品 C),`keil_builds=0`
- `safe_for_enable=false`;本包不携带任何硬件额度,旧批准不结转,也没有因为换了执行器就开立可用的新现场批准

## 1 两条必修的落点与实测(修前 = 任务书探针在同一输入上的原文)

| # | 落点(EXEC) | 修前实测 / 修后实测 |
|---|---|---|
| B1-1 | `validate_ledger_doc()`:`pending[i].name` 在**成员查找之前**先验非空 str | 修前 `raised=TypeError:unhashable type:'list'` → 修后 `APPROVAL_LEDGER_CORRUPT\|pending[0]_name_form=[]`;`5`/`{}`/`""` 同样具名拒绝 |
| B1-2 | `cleanup()`:撤能预占除 `Stop` 外也捕普通异常,记 `APPROVAL_RESERVE_FRAME_ERROR_<类型>_SENT_ANYWAY` 后**继续发送** | 修前该路径的普通异常离开清理框架(§4 原文:三条清理一条不发);修后同一输入三条必要撤能全发、端口关闭、`success=False` |
| B1-3 | `exec_session()` finally:确认消耗按条捕 `Stop`/普通异常,账本收尾整段 `try/except/finally`,句柄关闭进 `finally` | 修前 `closed=False` 且无正常汇总;修后 `closed=True`、台账有 `PORT_CLOSED`、错误列有 `APPROVAL_COMMIT_FRAME_ERROR_RuntimeError` |
| B2-1 | 新增 `read_plan_snapshot()`(一次 `read_bytes` → 同一份字节给 sha256 + 解析) + `current_identities(..., snapshot=)` | 修前"内存对象与批准文件不同"可跑到后端:`status=OK`、`spent.download=1`;修后 `PLAN_NOT_FROM_APPROVED_SNAPSHOT`、后端零调用、额度未扣 |
| B2-2 | `dispatch_plan(..., plan_snapshot=)` 在 real 下**先核对再动作**;通过后执行对象换成快照那份,`stages/tools/argv/quota/时间窗`只从它推导;`main()` 改单次读盘 | 修前 CLI 交错:`rc=0` + 未批准 argv 到达后端;修后:`rc=1` + 零调用 + 拒绝身份 `APPROVAL_IDENTITY_MISMATCH` |

保持不变的合同:真实单会话仍只接经批准的整包(CLI `--steps` 真实路径 rc=4)、`QUOTA_COMMAND_KEYS`
发送前拒绝、只读轮询不占动作额度、R3 危险字节门顺序、账本运行期不建账与 `VERSION=2` 版本门、
不退消费不自动迁移;坏账不被修复/清零(逐值复算盘上字节原样);清理证据不足仍 `UNKNOWN`,
首错与清理错分列 —— **没有**用"忽略异常报成功"实现隔离。

## 2 判据(新增 7 条)与元负控

`m8r3_field_selftest.py` → rc=0,`cases=121 pass=121 fail=0 negative_injections=18 hardware_actions=0`
(R6A 的 114 条原样保留)。新增:FS115 类型损坏具名拒绝(含"未保护成员查找确实抛 TypeError"的元证据)、
FS116 端到端"坏账仍完成三条撤能并关端口"、FS117 预占普通异常隔离、FS118 确认普通异常隔离、
FS119 内存计划非批准快照拒发、**FS120 元负控**:把快照门摘回"两次独立读取"的旧形状后,未批准的
`MEMORY_ONLY_UNAPPROVED_ARGUMENTS` 必须**重新到达后端**(实测到达)——证明 FS119 的"零调用"真长在这扇门上;
FS121 在**实际读盘接口**(`read_bytes`)上安排换文件交错 → 身份不符拒发且后端零调用。

修前/修后同输入对照 = `reports/reviews/phase8-m8-r6b-executor-binding/host/r6b_before_after.py`
(基线那份执行器用 `git cat-file` 从 `aab19ff` 取出):
`RESULT BEFORE_AFTER_OK (checks=8 fails=0)`;A 附录 B 的 7 条完整 JSON 原文入库为
`field/probes_at_aab19ff_before_fix.json` 与 `field/probes_after_fix.json`。

一处如实分工:附录 B 把 CLI 交错安排在 `Path.read_text` 上,而修后的读盘是 `read_bytes`,所以那一条
在修后没有触发换文件(实测 `file_restored_before_hash=False`),它的结论靠"身份哈希与执行对象同一份字节"
成立;同一交错的读盘接口版本由 FS121 承担。

## 3 复跑范围与退出码(§7.2.4 只跑受影响项)

| 命令 | rc | 结果 |
|---|---|---|
| `m8r3_field_selftest.py` | 0 | 121/121,`executed=121`,硬件动作 0 |
| `r6b_before_after.py` | 0 | 8 项判定全 OK(两条反例修前失败/修后拒绝原文) |
| `--validate-plan --plan field/plan_core_r6b.json` | 0 | `checks=232 fail=0 msh_commands=74` |
| `derive_plan_r6b.py`(写/改坏后重跑/`--force`/`--check`) | 0/1/0/0 | 字节不同时 `R6B_REFUSE_WRITE`,不静默覆盖 |
| `idle_learn_check.py`(r4 离线链) | 0 | `CHECKS=120 problems=0`;P8 执行器身份改成四层 r5c/r6/r6a/r6b 逐层对 blob |
| `r6a_before_after.py`(R6A 六组) | 0 | `RESULT BEFORE_AFTER_OK (fails=0)` 未回退 |
| `line_width_check.py` / `--selfcheck` | 0/0 | `PASS (bad=0)`;`CHANGE_SCAN_META fired 18 站点`(本轮无产品 C 改动) |
| `staged_preflight_batch.py` | 0 | 注入错那条 `problems=1` fired;真实登记件 `problems=0` |

未重跑并写明理由:历史数千条清单、S2 其余 19 组、固件链与镜像构建(产品 C 未动,任务书也不要求)。

## 4 新派生模板与身份(不建现场批准)

```text
field/plan_core_r6b.json  171427 B  08fd5d522d85f05a1eb8d0c51f339ccd05c18761b85990d6454022d6686069bb
  package_status = R6B_DERIVED_NOT_EXECUTED
  approval = REQUIRES_NEW_APPROVAL, hardware_actions_authorized_by_this_package = 0
  derived_from = R6A 的 plan_core_r6a.json(556a044e… 冻结校验后派生;源件字节未动)
  identity_binding.executor = 215704 B  84df1306f48e947349b9669577a6faf03273f22fe47f2f87407bf54f90a66d1b
  identity_binding.image    = 仍 dab356c6…/290916 B(已在板逐字节证过)
  r6b_image_note.rebuilt_this_round = false(未新增构建件)
自检夹具 180687 B  b41b3fe83d36760768b5813421d531c672572840598b8e490603c9418970ae9f
镜像三件原样: 5ae4110d…/292164(R6A 候选,仍未上板)、d70a9180…/292116(R6 候选)、dab356c6…/290916(在板)
```

不在旧路径改身份:`plan_core_r6a.json` 一个字节未动,它的登记值仍是 R6A 那一刻的执行器;
新执行器只由这份新路径的 R6B 模板登记。派生件是离线模板,**不是**现场包:要动新镜像必须另起包
重绑 bin/axf/回读长度/下载阶段并按新代码另批。

## 5 保持不变的旧裁定与剩余未验

阈值未建立、ACTIVE 覆盖未建立、REL_IMPACT 未到达、`load_means` 仍 NOT_PROVIDED、idle1024 带载最坏栈余量、
现场精确线程交错(NOT_PROVEN)、`0x00bd80b0` 写入者未证 —— 一律保持;四项装配前提仍 PROVIDED,未重问。

未验:`product_field_fix`(两条修只有主机/内存端口证据)、真实串口下的读块切分与告警刷屏时序、
跨进程真实现场账本(仍未在现场用过一次)、新镜像未上板、`overall_offline_release` 待 A 复审。
本轮所有反例都是主机/内存输入构造,**不是新的现场异常**,也不意味着旧现场确曾发生这两个交错。

## 6 复跑对上一轮派生输出的影响(不泛称"什么都没动")

`idle_learn_check.py` 的 120 项复跑会把 r4 那轮的派生输出重新写到盘上。按 A §6 的口径,本轮
**保留这些合法派生更新**并逐条写清差异:① 仓外临时 `run_dir` 随机名;② 输出里登记的执行器身份
`99d6c960…` → `84df1306…`;③ `idle_learn_check.json` 里"本轮改动=15 项" → "6 项" —— 那是检查器
按当时工作树现数的字段,本身就随环境漂移。r4/r5c 计划、26 场现场原件与台账、镜像归档件、具名冻结件
`git status` 为空(未动)。派生清单跟随数据:r6b/r3/r4 三份 `SHA256SUMS.txt` 重算(清单不哈希自身)。

防复发口径(给下一轮):检查器的输出写到**本轮**目录;复跑历史链之后先 `git status` 看清哪些派生件
被动过,再区分"合法更新"与"无意义抖动",不写"所有旧轮报告都没动"这类泛称。R6A 回执里那句
"8 件已还原"只描述当时的处理,其后的提交里 R4 派生输出确有更新 —— 该更正已按 §6 记入此处。

## 7 推送与独立远端回读

(本轮收口一步补:推送区间、远端精确 SHA、逐 blob 回读的定向结果与残差口径。)
