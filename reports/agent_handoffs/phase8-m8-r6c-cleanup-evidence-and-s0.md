# M8-R6C 执行器收口轮 · 审核回执(给云端 A / 操作者)

- 任务书:`NS800_M8_R6B_Review_REWORK_and_R6C_Task_20261103.md`(裁定:整体仍 REWORK、继续离线;
  R6B 已修好上一轮两个主要反例;本轮只处理 C1 清理发送前的记账异常、C2 S0 执行快照身份)
- 基线 = 任务书 §2 指定的实际交付头 `64353285e9b34df1c54ccea4011ac555c482e626`
  (开工时本地同头、工作树干净、远端同头)
- 软件输入提交 = `c0f361db5cc02fd087f6f3b404254151411d4a4a`;证据提交 = `6e120199a317a55e1ddd83e66ce2137b898c45b5`;
  本文件随收口提交再更新一次(只补 §8 的推送与远端回读原文与跟随数据的清单, 执行器与判据不再改)
- 分支 `feature/m8-sg-adc-relative-protection`;未 reset/clean/rebase/amend/force,未新建分支,未碰冻结引用
- **本轮设备动作全部为 0**(不开串口、不连 DAP、不烧录/回读/复位、不提示重上电、不 learn/zero/起转/save);
  未重编固件(§6.1),`keil_builds=0`;产品 C / 参数 / 算法 / 阈值 / 引脚 / 恢复资格 / 固件归档均未动
- `safe_for_enable=false`;本包不携带任何硬件额度,旧批准不结转,也没有因为换了执行器就开立可用的新现场批准

## 1 先复现(§6.2 第1条):附录 B 探针打在 6435328 的字节上

`host/remaining_probes.py` 是附录 B 逐字节入库的副本(5424 B `9e8b161c72b87dd5…`),只改一处环境适配并
在文件里写明:读自己那份 `dispatch_ledger.jsonl` 时按 UTF-8 取字节 —— Windows 下无 encoding 的
`read_text` 走 cp936,会炸在中文行。判据、输入、失败原文未改。原文入库
`field/probes_at_6435328_before_fix.json`(4786 B `cd82c48a7056…`),与任务书附录 A 逐项相符:

| 探针 | 修前实测 |
|---|---|
| corrupt-control(坏账,日志正常) | `motor_start→motor_stop→motor_disarm→motor_status`、closed=true、CONFIRMED_DISABLED、success=false |
| **corrupt-log-failure**(坏账 + 披露日志写一次 OSError) | **只有 `motor_start`**、closed=true、**UNKNOWN**、success=false、`CLEANUP_FRAME_EXCEPTION` |
| exhausted-control(合法完整账、cleanup cap=0,日志正常) | 四条都发、CONFIRMED_DISABLED、success=false |
| **exhausted-log-failure**(同上 + 一次写失败) | **只有 `motor_start`**、UNKNOWN、success=false、`CLEANUP_FRAME_EXCEPTION` |
| **S0 P→Q 交错** | rc=0、后端调用 1、P=`23f0fbf8eca1b9a2…`、Q=`089514f4fc94dba3…`、**S0 记的是 Q**、`s0_records_execution_snapshot=false` |

两个 SHA 与附录 A 的原文逐字符一致 —— 本机跑的是同一份输入,不是另造一个近似用例。

## 2 两条必修的落点与修后实测

| # | 落点(EXEC) | 修前 → 修后(同一输入) |
|---|---|---|
| C1-1 | `_ev_or_defer() :1557` | 清理路径上的记账写入**不再是发送的前提**:写不进去绝不抛,原样收进 `deferred` 内存清单 |
| C1-2 | `cleanup() :1647/1649` | 修前该披露一次 OSError 就让 cleanup 退出(**只发 motor_start**);修后同一输入实际写 = `motor_start,motor_stop,motor_disarm,motor_status`、closed=true、无 `CLEANUP_FRAME_EXCEPTION`、success=false、`LEDGER_EVENT_WRITTEN_AFTER_RETRY:CLEANUP_RESERVE_REFUSED_SENT_ANYWAY` |
| C1-3 | `_flush_deferred_ev() :1576/1667` | 三条撤能发完、禁能算完之后才回头尽力补记;**持续**失败留 `LEDGER_EVENT_WRITE_FAILED:<kind>:<异常>`(FS123:同一事件命中 6 次仍三发+关闭+不成功) |
| C2-1 | `s0_binding(..., snapshot=) :3152/:3173` | `plan_sha256` = **被执行的那份快照**字节;磁盘现值另记具名字段 `plan_sha256_on_disk`,不覆盖执行身份 |
| C2-2 | `:3179` + 阶段循环 `:3510` | 修前"记 Q 执行 P + rc=0 + 下载 1 次";修后同一交错在任何动作之前 `S0_PLAN_DISK_DRIFT`(detail 同时给出 execution=23f0fbf8… 与 disk=089514f4…)、后端零调用、`download` 额度未扣、盘上留的就是 Q(不悄悄改回) |

按 §4.2 的边界执行:没有全局吞 Ledger 错(只有清理路径这几处走本函数)、没有清空 errors、没有伪造成功、
没有删原始字节门;该场仍 `success=false`,status 证据缺失/损坏仍 `UNKNOWN`;坏账不被修复/清零、不退款、
不自动迁移、不发新额度;额度不足照撤能、commit 错不跳过端口与日志关闭 —— 这些旧合同逐条保留。
`snapshot=None`(内存计划/stub 工装)时 S0 保持旧形状,没有顺手改动 121 条历史用例的语义。

## 3 判据(新增 6 条)与元负控

`m8r3_field_selftest.py` → rc=0,
`SUMMARY|cases=127 pass=127 fail=0 executed=127|negative_injections=20|hardware_actions=0|transport=stub_only|port=IN_MEMORY`
(R6B 的 121 条原样保留)。新增:

- FS122 坏账 + 一次写失败 → 三条必要撤能仍真发、端口关闭、不成功、末尾补记成功
- FS123 合法完整账(cleanup cap=0)+ **持续**写失败 → 同形,且留 WRITE_FAILED 而非 RETRY
- **FS124 元负控**:把 `_ev_or_defer` 摘回直接 `led.ev(...)` 的旧形状 → 实测退回"**只有 motor_start**"+
  `CLEANUP_FRAME_EXCEPTION`,证明 FS122/123 的"三发"判据真长在这扇门上
- FS125 S0 执行身份正控:文件未变时 `plan_sha256 == 快照 == 磁盘`,且下载照跑(合法整包不被新门挡)
- FS126 S0 磁盘漂移:动作前具名拒绝、后端零调用、`spent.download=0`
- **FS127 元负控**:摘掉 S0 的快照贯通 → 实测重新出现"S0 记 `089514f4…`(Q)≠ 执行 `23f0fbf8…`(P)、
  后端调用 1",即复审 §5 那个假身份

§4.4 的口径落到实现:`_b1_session` 现在把**内存端口的实际 write 列表**按 tag 存进 `B1_IO`,
用例断言的是这个列表 —— 日志被弄坏时 TX 事件会跟着一起消失,只数事件分不清"没落证"与"没发送"。
helper 的返回值形状保持不变,所以附录 B 探针可以继续逐字节复用。

## 4 修前/修后同输入对照与复跑范围

`host/r6c_before_after.py`(11621 B `918827114bd01439…`)用 `git cat-file blob 6435328:<path>` 把基线那份
EXEC/自检/plan/rows/needles/镜像/manifest + needles 引用的 C 源码取到仓库外副本,再放一个 `.git` gitfile
只读指向本仓对象库(S0 要 `git rev-parse` 历史提交),然后把同一份附录探针分别打在基线字节与工作树字节上:
`RESULT BEFORE_AFTER_OK (checks=8 fails=0)`(原文 `host/r6c_before_after.txt` 12911 B `bd576f94e79b…`)。
输入登记里 13 项"同字节",EXEC/自检标"内容已不同(本轮改动)",`motor.c`/`supervisor.c` 标
"仅行尾不同(盘 CRLF/blob LF; git diff 该文件=未变)" —— 这是 core.autocrlf 的行尾现象,本轮没有改产品 C。

| 命令 | rc | 结果 |
|---|---|---|
| `m8r3_field_selftest.py` | 0 | 127/127(20 项反向注入),硬件动作 0 |
| `r6c_before_after.py` | 0 | 8 项判定全 OK(两条必修的修前失败/修后拒绝原文) |
| `--validate-plan --plan field/plan_core_r6c.json` | 0 | `checks=232 fail=0 msh_commands=74` |
| `derive_plan_r6c.py` 五步语义 | 0/0/1/0/0 | 改坏后 `R6C_REFUSE_WRITE`(rc=1),不静默覆盖;见 `host/derive_matrix.txt` |
| `idle_learn_check.py`(r4 离线链) | 0 | `CHECKS=120 problems=0`;P8 执行器身份由四层改**五层**(r5c/r6/r6a/r6b/r6c 各自对 blob 现算) |
| `r6b_before_after.py` / `r6a_before_after.py` | 0/0 | 均 `BEFORE_AFTER_OK (fails=0)` —— 前两轮未回退 |
| `line_width_check.py` / `--selfcheck` | 0/0 | `LINE_WIDTH_RESULT PASS (bad=0)`;`CHANGE_SCAN_META fired 18 站点`(本轮无新 C 站点) |

未重跑并写明理由:历史数千条清单、S2 其余 19 组、固件链与镜像构建(产品 C 未动,任务书也不要求)。

## 5 新派生模板与身份(不建现场批准)

```text
field/plan_core_r6c.json  172306 B  408c18ecaf50f62a0620c904011f0c60bea3681087875a2ada64efbae7b6de73
  package_status = R6C_DERIVED_NOT_EXECUTED
  approval = REQUIRES_NEW_APPROVAL, hardware_actions_authorized_by_this_package = 0
  derived_from = R6B 的 plan_core_r6b.json(08fd5d52… 冻结校验后派生; 源件字节未动)
  identity_binding.executor = 219491 B  33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca
  identity_binding.image    = 仍 dab356c6…/290916 B(已在板逐字节证过)
  r6c_image_note.rebuilt_this_round = false(未新增构建件)
自检夹具 192823 B  4abcd162a7d0c20ded108f5de64fecb8505f460ace3f2f9bcdf7a1048a410f65
镜像三件原样: 5ae4110d…/292164(R6A 候选,仍未上板)、d70a9180…/292116(R6 候选)、dab356c6…/290916(在板)
```

不在旧路径改身份:`plan_core_r6b.json` 一个字节未动,它的登记值仍是 R6B 那一刻的执行器
(`84df1306…/215704 B`)—— 那份字节正是本轮 §4 对照的**基线侧输入**,已逐字节复算。
新执行器只由这份新路径的 R6C 模板登记。派生件是离线模板,**不是**现场包:要动新镜像必须另起包
重绑 bin/axf/回读长度/下载阶段并按新代码另批。

## 6 复跑对上一轮派生输出的影响(不泛称"什么都没动")

`idle_learn_check.py` 的 120 项复跑会把 r4 那轮的派生输出重新写到盘上,本轮按上一轮 §6 的口径
**保留合法派生更新**并逐条写清差异:① 仓外临时 `run_dir` 随机名(`idlecheck_k3_0cara` →
`idlecheck_dvt_1d53`,已逐字符核对差异只在这里);② 输出里登记的盘上执行器身份 `84df1306…/215704 B`
→ `33d3f0af…/219491 B`;③ P8 那条的说明文字由四层链改成五层链。
r5c/r6/r6a/r6b 计划、26 场现场原件与台账、镜像归档件、具名冻结件 `git status` 为空(未动)。
派生清单跟随数据:r6c/r4/r3 三份 `SHA256SUMS.txt` 重算(清单不哈希自身)。

## 7 剩余未验与保持不变的旧裁定

未验:`product_field_fix`(两条修只有主机/内存端口与内存后端证据)、真实串口下的读块切分与告警刷屏时序、
跨进程真实现场账本(仍未在现场用过一次)、新镜像 5ae 未上板、`overall_offline_release` 待 A 复审。
本轮所有反例都是主机/内存输入构造,**不是新的现场异常**,也不意味着旧现场确曾发生这两个交错。

保持不变:阈值未建立、ACTIVE 覆盖未建立、REL_IMPACT 未到达、`load_means` 仍 NOT_PROVIDED、
idle1024 带载最坏栈余量、现场精确线程交错(NOT_PROVEN)、`0x00bd80b0` 写入者未证;
四项装配前提仍 PROVIDED,未重问。真实单会话只接经批准的整包、命令→额度键集中映射、
只读轮询不占动作额度、R3 危险字节门顺序、账本运行期不建账与 `VERSION=2` 版本门 —— 一条没退。

## 8 推送与独立远端回读

```text
推送区间  6435328 之后顺序追加 c0f361db5cc02fd087f6f3b404254151411d4a4a(软件输入)
          + 6e120199a317a55e1ddd83e66ce2137b898c45b5(证据); 常规 fast-forward, 无 force/rebase/merge/amend
回读方式  仓外裸克隆 git fetch --depth=1 + git cat-file blob <tip>:<path> 逐 blob 现算; 历史头按精确 SHA 定点 fetch
```

本轮范围 `host/remote_readback_r6c.py` 打在证据头 `6e120199a317`(rc=0):

```text
R6C_REMOTE_READBACK_SUMMARY|tip=6e120199a317|checks=35|fails=0|not_checked=0
device_actions=0|serial_ports_opened=0|dap_connections=0|keil_builds=0|reads_from=remote_git_protocol_only
```

35 项覆盖: 四份清单逐条对远端 blob(r6c 13 / r3 5137 / r4 394 / r6b 16,mismatch=0)、候选模板申报的
执行器/在板镜像三件/派生源(R6B 计划)/R6A 候选 bin/`rebuilt_this_round=false`/`authorized=0`、
交接 JSON 里**每个带 path+sha256 的条目**逐个重算 + 顶层不自证字节 + 设备动作 0、七件冻结旧件
(R5C/R6/R6A/R6B 计划 + 5ae/d70/dab 三个 BIN)、**基线头 `6435328` 上的执行器 `84df1306…/215704 B`**
(定点 fetch 后现算 —— 正是 §1 修前那一栏的输入字节)、`motor.c`/`supervisor.c` 在 tip 与基线的 blob
逐字节相同(产品 C 未改)、软件输入提交里的 EXEC/自检 == 模板与交接登记、"证据提交未偷改执行器"。

一处口径提醒(防误读): 附录 B 的探针名仍是 `s0_record_uses_disk_instead_of_execution_snapshot`,
修后这条路**在 S0 就被具名拒绝**, 没有 `S0_FACTS` 事件可记 —— 所以修后 JSON 里
`s0_records_execution_snapshot=false` 的含义是"没有那条记录"(rc=1、`memory_backend_calls=0`、
stdout 里 `S0_PLAN_DISK_DRIFT` 且 detail 同时给出 execution=P/disk=Q), **不是**"仍在记磁盘那份"。
修后的正控在 FS125(文件未变 => 执行身份、快照、磁盘三者同值, 下载照跑)。

全仓通用回读(同一头, rc=0):`remote_files=10872 declared=6702 verified=6635`、
`SCOPE_R3|entries=5149 verified=5149 problems=0`、
`SCOPE_PRIOR|problems=72`(cross_manifest_sha_conflict=3、declared_but_absent=53、hash_mismatch=6、
duplicate_conflict=2、suffix_ambiguous=8)—— 与 R6B 收口那次同类别同数量, **本轮零新增**;
`declared/verified` 各 +16 就是本轮新增登记件, `unlisted` 4544→4547。那 72 条是旧轮 manifest 之间的
历史口径差(本轮没有新增构建件, 故没有新的 R6C 侧冲突), 属残差口径而非本轮回归。
两份原文入库:`remote_readback_at_6e12019.txt`、`remote_readback_generic_at_6e12019.txt`。

§6.2 的停点按字面执行:收口提交里放的是对**证据头**的回读原文;对收口头本身只再跑一次并把结论写进会话
回执,**不再为"回读这份回读"追加提交**。
