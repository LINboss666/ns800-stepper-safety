# M8-R6C 执行器收口轮 · 运行记录(离线, 设备动作 0)

任务书：`NS800_M8_R6B_Review_REWORK_and_R6C_Task_20261103.md`(云端 A, 2026-11-03)。
裁定：整体仍 REWORK、继续离线；R6B 已修好上一轮两个主要反例，本轮只处理
**C1 清理发送前的记账异常**与 **C2 S0 执行快照身份贯通**两处直接相关问题。

## 19.1 起点核对（§6.1）

```text
基线（任务书 §2 指定的 A 实际审核头）  64353285e9b34df1c54ccea4011ac555c482e626
开工时                                 本地 HEAD == 远端同头, 工作树干净
分支                                   feature/m8-sg-adc-relative-protection
软件输入提交（本轮 EXEC/自检/模板/工具）   c0f361db5cc02fd087f6f3b404254151411d4a4a
证据提交（对照原文/清单/交接件）           本记录所在提交（在其后顺序追加）
收口提交（推送与远端逐 blob 回读）          见 §19.11
```

未 reset/clean/rebase/amend/force；未新建分支；冻结引用（R5-C/R6/R6A/R6B 计划与三件镜像）未动。

## 19.2 先复现再修（§6.2 第 1 条）

附录 B 的 `remaining_probes.py` 逐字节入库为 `host/remaining_probes.py`（5424 B
`9e8b161c72b87dd5…`），只改一处环境适配并在文件里写明：读自己那份 `dispatch_ledger.jsonl`
时按 UTF-8 取字节（Windows 下无 encoding 的 `read_text` 走 cp936，会炸在中文行）。
判据、输入、失败原文未改。

在 `6435328` 的工作树字节上跑（rc=0，"退出 0"只表示采集完成，不表示业务通过），
原文入库 `field/probes_at_6435328_before_fix.json`（4786 B `cd82c48a7056…`）：

| 探针 | 修前实测（与任务书附录 A 逐项相符） |
|---|---|
| corrupt-control（坏账，日志正常） | tx=motor_start→stop→disarm→status、closed=true、CONFIRMED_DISABLED、success=false |
| **corrupt-log-failure**（坏账 + 披露日志写一次 OSError） | **tx 只剩 motor_start**、closed=true、**disarm=UNKNOWN**、success=false、`CLEANUP_FRAME_EXCEPTION` 一条 |
| exhausted-control（合法完整账、cleanup cap=0，日志正常） | 四条都发、CONFIRMED_DISABLED、success=false |
| **exhausted-log-failure**（同上 + 一次写失败） | **tx 只剩 motor_start**、UNKNOWN、success=false、`CLEANUP_FRAME_EXCEPTION` |
| **s0_record_uses_disk_instead_of_execution_snapshot** | rc=0、后端调用 1、执行快照 P=`23f0fbf8eca1b9a2…`、磁盘被换成 Q=`089514f4fc94dba3…`、**S0 记的是 Q**、`s0_records_execution_snapshot=false` |

两个 SHA 与任务书附录 A 的原文逐字符相符 —— 本机复现的就是 A 那一次的同输入。

## 19.3 C1 落点（EXEC）

新增 `_ev_or_defer(led, deferred, kind, **kw)` 与 `_flush_deferred_ev(led, deferred, errors)`：
清理路径上的记账写入**不再成为发送的前提**。

```text
m8r3_serial_chain.py:1557  _ev_or_defer()        写不进去绝不抛, 原样收进内存清单 deferred
m8r3_serial_chain.py:1576  _flush_deferred_ev()  末尾尽力补记; 成/败都落成独立具名内存错误
m8r3_serial_chain.py:1647  cleanup() 里那条披露   led.ev(...) -> _ev_or_defer(...) + BOOKKEEPING_DEFERRED(:1649)
m8r3_serial_chain.py:1660  DISARM_FIELDS 同形状; :1667 三条撤能发完、禁能算完之后才回头补记
```

按 §4.2 的边界执行：没有全局吞 Ledger 错（只有清理路径这几处走本函数）、没有清空 errors、
没有伪造成功、没有删原始字节门；本场仍 `success=false`；status 证据缺失/损坏仍 `UNKNOWN`；
坏账不被修复/清零、不退款、不自动迁移、额度不足照撤能、commit 错不跳过关闭 —— 全部保留。

## 19.4 C2 落点（EXEC）

```text
m8r3_serial_chain.py:3152  s0_binding(..., snapshot=None)  plan_sha256 = **被执行的那份快照**
m8r3_serial_chain.py:3173  plan_sha256_on_disk = 磁盘现值(具名观察字段, 不覆盖执行身份)
m8r3_serial_chain.py:3179  执行前磁盘 ≠ 快照 => Stop("S0_PLAN_DISK_DRIFT", execution=… disk=…)
m8r3_serial_chain.py:3510  dispatch 的阶段循环把已核快照 snap 交给 S0(:3405 给账本身份核对)
```

选了任务书允许的第二种策略（动作前具名拒绝），因为它把"记 Q 执行 P"变成不可能而不是"记两个字段"。
`snapshot=None`（内存计划/stub 工装）时保持旧形状，121 条历史用例的语义没有被顺手改动。

## 19.5 新增判据（6 条，含 2 条元负控）

`m8r3_field_selftest.py` → rc=0：
`SUMMARY|cases=127 pass=127 fail=0 executed=127|negative_injections=20|hardware_actions=0|transport=stub_only|port=IN_MEMORY`

| # | 判据 | 实测原文（`host/selftest_final_summary.txt`） |
|---|---|---|
| FS122 | 坏账 + 一次写失败：三条必要撤能仍真发、端口关闭、不成功、末尾补记成功 | 实际write=motor_start,motor_stop,motor_disarm,motor_status closed=True 框架异常=0 success=False 披露=LEDGER_EVENT_WRITTEN_AFTER_RETRY:… |
| FS123 | 合法账 cleanup=0 + **持续**写失败：同形，且留 WRITE_FAILED 而非 RETRY | 四条都发 closed=True 写入失败次数=6 success=False 框架异常=0 |
| FS124 | **元负控**：把 `_ev_or_defer` 摘回直接 `led.ev` | 摘门后 实际write=motor_start 框架异常=1 success=False |
| FS125 | S0 记的执行身份 == 快照 == 磁盘（文件未变的正控），下载照跑 | S0记=23f0fbf8eca1… 执行快照=disk字段=同值 下载调用=1 |
| FS126 | P→Q 交错：动作前 S0_PLAN_DISK_DRIFT、后端零调用、download 未扣 | rc/S0 具名停、记的身份=None、后端调用=[]、spent.download=0 |
| FS127 | **元负控**：把 S0 的快照参数摘掉（旧形状） | 摘门后 S0记=089514f4fc94…(Q) ≠ 执行 P=23f0fbf8eca1…，后端调用=1 |

§4.4 的口径落到了实现上：`_b1_session` 现在把**内存端口的实际 write 列表**按 tag 存进 `B1_IO`
（日志被弄坏时 TX 事件会跟着一起消失，只数事件分不清"没落证"和"没发送"）；用例断言的是那个列表。
为保持附录脚本逐字节可用，helper 仍返回原来的 6 元组。

## 19.6 修前/修后同输入对照

`host/r6c_before_after.py`（11621 B `918827114bd01439…`）把基线那份 EXEC/自检/plan/rows/needles/
镜像/manifest 与 needles 引用的 C 源码用 `git cat-file blob 6435328:<path>` 取到仓库外副本，
并放一个 `.git` gitfile 只读指向本仓对象库（S0 要 `git rev-parse` 历史提交），然后
把**同一份附录探针**分别打在基线字节与工作树字节上：

```text
RESULT BEFORE_AFTER_OK (checks=8 fails=0)
device_actions=0|serial_ports=0|dap=0|downloads=0|flash=0|readback=0|reset=0|
learns=0|zeros=0|motor_starts=0(仅内存端口)|config_saves=0|keil_builds=0
```

输入登记里 13 项"同字节"，EXEC+自检标"**内容已不同(本轮改动)**"，
`motor.c`/`supervisor.c` 标"仅行尾不同(盘 CRLF/blob LF; git diff 该文件=未变)" ——
这是 core.autocrlf 的行尾现象，不是本轮改了产品 C；对照器把这两种情形分开写，
免得下一轮读者误读。原文入库 `host/r6c_before_after.txt`（12911 B `bd576f94e79b…`），
修后探针 JSON 入库 `field/probes_after_fix.json`（5020 B `78a9c65b31ad…`）。

## 19.7 受影响项复跑（§6.2 第 4 条，只跑相关段）

| 命令 | rc | 结果 |
|---|---|---|
| `m8r3_field_selftest.py` | 0 | 127/127（R6B 的 121 条原样保留，新增 6 条），硬件动作 0 |
| `host/r6c_before_after.py` | 0 | checks=8 fails=0 |
| `--validate-plan --plan field/plan_core_r6c.json` | 0 | `checks=232 fail=0 msh_commands=74` |
| `host/derive_plan_r6c.py` 五步语义 | 0/0/1/0/0 | 改坏后 `R6C_REFUSE_WRITE`（rc=1），不静默覆盖；见 `host/derive_matrix.txt` |
| `idle_learn_check.py`（r4 离线链） | 0 | `CHECKS=120 problems=0`；P8 执行器身份由四层改五层（r5c/r6/r6a/r6b/r6c 各自对 blob 现算） |
| `r6b_before_after.py` | 0 | `BEFORE_AFTER_OK (checks=8 fails=0)` —— R6B 两项修未回退 |
| `r6a_before_after.py` | 0 | `BEFORE_AFTER_OK (fails=0)` |
| `line_width_check.py` / `--selfcheck` | 0/0 | `LINE_WIDTH_RESULT PASS (bad=0)`；`CHANGE_SCAN_META fired 18 站点`（本轮无产品 C 改动） |

未跑并写明理由：历史数千条清单、S2 其余 19 组、固件链与镜像构建（产品 C 未动、任务书不要求，
`keil_builds=0`）。

## 19.8 新离线模板与身份（不建现场批准）

```text
field/plan_core_r6c.json  172306 B  408c18ecaf50f62a0620c904011f0c60bea3681087875a2ada64efbae7b6de73
  package_status = R6C_DERIVED_NOT_EXECUTED
  approval = REQUIRES_NEW_APPROVAL, hardware_actions_authorized_by_this_package = 0
  derived_from = R6B 的 plan_core_r6b.json(08fd5d52… 冻结校验后派生; 源件字节未动)
  identity_binding.executor = 219491 B  33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca
  identity_binding.image    = 仍 dab356c6…/290916 B(已在板逐字节证过)
  r6c_image_note.rebuilt_this_round = false
EXEC 219491 B 33d3f0af8a4c9c33…      自检夹具 192823 B 4abcd162a7d0c20d…
镜像三件原样: 5ae4110d…/292164(仍未上板)、d70a9180…/292116、dab356c6…/290916(在板)
```

不在旧路径改身份：`plan_core_r6b.json` 一个字节未动，它的登记值仍是 R6B 那一刻的执行器
（`84df1306…/215704 B`，已作为 §19.6 基线侧的输入被逐字节复算）。

## 19.9 复跑对上一轮派生输出的影响（不泛称"什么都没动"）

`idle_learn_check.py` 的 120 项复跑会把 r4 那轮的派生输出重新写到盘上，本轮按 A 的口径
**保留合法派生更新**并逐条写清差异：① 仓外临时 `run_dir` 随机名（`idlecheck_k3_0cara` →
`idlecheck_dvt_1d53`，已逐字符核对差异只在这里）；② 输出里登记的盘上执行器身份
`84df1306…/215704 B` → `33d3f0af…/219491 B`；③ P8 那条的说明文字由四层链改成五层链。
r5c/r6/r6a/r6b 计划、26 场现场原件与台账、镜像归档件、具名冻结件 `git status` 为空（未动）。
派生清单跟随数据：本轮 `SHA256SUMS.txt` 与 r4 链重算（清单不哈希自身）。

## 19.10 停点

设备动作 0（只跑主机脚本与 `git cat-file`）、`safe_for_enable=false`、本包不携带硬件额度、
旧批准不结转、未创建可用现场批准、后续现场包本轮不制定。
`product_field_fix=NOT_VERIFIED`、真实串口时序与新镜像上板仍未验；
阈值/ACTIVE 覆盖/REL_IMPACT/`load_means`(NOT_PROVIDED)/idle1024 带载最坏栈余量/
现场精确线程交错/`0x00bd80b0` 写入者的旧裁定保持；四项装配前提仍 PROVIDED，未重问。
