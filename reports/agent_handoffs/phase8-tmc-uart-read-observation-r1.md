# 交接 — phase8-tmc-uart-read-observation-r1

- 任务书：`NS800_URO_Independent_Review_and_R1_Task_6324ac4_20261028.md`（外部审核方撰写，用户附件转达）
- document_base / source_base：`6324ac40bd8d0f172fdb89e77917be33dc739460`
- 本轮 code_commit（含产品 + 验证链 + 证据的完整输入提交）：
  `a483dcba74abd8c04549459bcee2cbcdb1ddea4c`
- 复审判定继承：`RX_DEADLINE_FIX_ACCEPTED`、`EXISTING_HOST_REGRESSIONS_REPRODUCED` 保持；
  `URO_OBSERVATION_AND_CAPTURE_REWORK_REQUIRED` 的三组阻断（A1/A2、B1/B2/B3、C 完整性与横幅门）
  在本轮离线关闭；上一轮两枚 URO 镜像**仍未批准下载**，本轮候选件是新哈希（§6）。
- 本轮阶段：**离线返修 + 离线验证 + 双目标构建完成；设备动作 0**

## 一句话结论

结构体里的事实现在真的送到了串口：`tx_written` 独立成行、前缀 32 B 以两块 16 B 全量导出且每块
`n` 等于该行真实字节数；RX 入口被抢占那一支的观测记账与业务返回同为 `-117/budget-exhausted`；
打开身份不再被子读覆盖、未到达不再被零初始化读成"已打开"、事务未取得时不再声称 `txn-held` 且
`[TMRO] BEGIN` 之前不再有中文；采集器的最小 TMRO 完整性核对会拒绝"删光逐笔行仍判完整"、
`END reads=99`、BEGIN/END 矛盾，同时把"完整采集"和"业务失败"分开，允许 open/txn 失败与
VERSION=0x20、GSTAT 高位这类**完整但失败**的采集，横幅未观测时只走"本包逐字节回读已验证 +
`BANNER_NOT_OBSERVED`"这一条路径，不为横幅加复位或重开口。
主机侧 `read_obs` 30 条（原 24 条一条未删）在 target 与 host_ovr **两个变体都真实执行且全 PASS**，
集中回归十条入口 rc=0，三项反向（含本轮新增的**导出侧**第三项）全部 META-OK；
两目标真实 Rebuild All 均 0 Error / 0 Warning；R1 采集器在 Windows PowerShell 5.1 真实 `-SelfTest`
通过（rc=0、stderr 0 B、负控 51 条逐条具名理由、禁止命令 12 条、切片正控 9 条、横幅门 6 条）。
下一次板级包只有草案，状态 `DRAFT / NOT_AUTHORIZED / NOT_EXECUTED`。

## 1. 交付物

| 路径 | 说明 |
|---|---|
| `applications/tmc2209.c` | A1/A2 导出、B1 出口记账、B2 打开身份"先写者胜"、B3 显式 `OPEN_NOT_REACHED`/入口 ctx 记账/quiet 事务核心、NULL 判定顺序、期限三态、全流回声窗口（blob `07c07421c22a1ec27335ae034f722707c9d90263`，191034 B 入库字节 / sha256 `8a5e0308…`） |
| `tests/host/leaf_main.c` | `read_obs` 组 24→30 条（新增 URO23..URO27b 六条**导出侧**断言）+ 确定接缝端口开关 + 原三态期限期望改写（blob `9ba6654fb4316d28a2df42bd944ea28e3d3630ad`，161087 B） |
| `tests/host/run_stop_priority_s2.sh` | 30 条 id 登记、期望 47/103→**53/109**、第三个反向家族 `rev_export_drop_prefix_second_chunk`（blob `461e2937912aee7521dbf0ed6beabd95b5ec729a`，49352 B） |
| `tests/host/inject.py` | 本轮**未改动**（仍为锚点上的 `845ff4c7f3f8069430ecf42ce41a3e5ffe481815`，4947 B）；复审已接受其上一轮的锚点扩展 |
| `reports/reviews/phase8-tmc-uart-read-observation-r1/` | `source_audit.txt`、`baseline_and_fixed_checks.txt`、`host_results.txt`、`keil_builds.txt`、`collector_selftest.txt`、`board_candidate.md`、`UARTReadDiagSerialR1.ps1` |
| `reports/firmware/phase8-tmc-uart-read-observation-r1/` | `default.bin`、`measure-a0.bin`、`build_manifest.json` |
| `reports/agent_handoffs/phase8-tmc-uart-read-observation.md` | 追加 R1 指针与三条口径更正（旧 stdout/旧结论原文未改写） |

上一轮 URO 的源码、测试、两枚 BIN、采集脚本与原始报告**一律保留不覆盖**；本轮新证据只写进新目录。

## 2. A 组（导出 ≠ 结构体）改了什么

- **A1**：`tmc_probe_dump()` 新增独立行 `[TMRO] reg=%02X tx_written=%u`，与 `tx=请求构造/预算写调用/rc`
  分列 —— 旧导出里 written=2 与 written=4 从串口上看不出来。
- **A2**：新增 `tmc_probe_dump_sample()`（附录A 形状，自持 `line` 数组，`sizeof(line)` 不是指针大小），
  `rx1 / rxT / cand` 三个命名样本各按至多 16 B 一块全量导出，每行
  `off=<该样本内偏移> n=<本块真实字节数> hex=<字节>`；空样本 `n=0 hex=-`；`samples pre=/tail=/cand=`
  另列声明总长度；每次打印含 LF ≤96 B。保存容量仍是 32/16/8，**没有**把容量改成 16 或把 n 改成
  16 来规避 32 B 证据需求。
- **截断口径**：`pre_trunc`（前缀样本装不下）与 `gap`（全流超出"前缀 32 + 尾部 16"的覆盖，中间确有
  未取样字节）分列；内部标志名保留 `rx_truncated`，导出与文档按 `pre_trunc` 表述。48 B 输入
  （前缀 32 + 尾部 16 恰好覆盖全流）记 `pre_trunc=1 gap=0`，不宣称"必然丢了中间一段"。
- **证明形状**：URO24 从**导出文本**解码重建前 32 B 并核对块数 2/1/1；URO27b 证明恰好 32 B 时
  前缀可全部重建；URO27 证明零字节时三个样本都是 `n=0 hex=-` 且无虚构 `00`；URO23 用 0/2/4 三档
  真实 TX 叶子输入证明 `tx_written` 与端口真落 DATA 的字节数逐一相等。反向第三项
  （分块循环退回"单次 16 B hex"，结构体一字节未动）只能由 URO24/URO27b 检出 —— 这就是复审
  要求的"检查完整导出后的数值与字节序列"，不是只看 `o.rx_pre[31]`。

## 3. B 组（出口/入口矛盾）改了什么

- **B1**：RX 阶段入口 `used >= b->total` 那一支现在同时落 `st`（reason/rc）与 `obs`
  （`done_off`、`total_expired=YES`），并调用与成功路径同一个 `tmc_obs_exit(..., -RT_ETIMEOUT,
  TMC_XFER_BUDGET_EXHAUSTED, published=0)`；业务返回路径与总预算检查都未改。
  同类出口逐条复核：`b==NULL`、`value==NULL`、flush 失败、TX 失败、CRC 失败、总预算/阶段期限
  拒绝、空轮总预算耗尽、成功发布 —— 每条形成的 `obs.rc` 与返回值一致（见 `source_audit.txt`）。
- **B2**：`tmc_read_reg_obs()` 的"此刻已打开"只在**没人记过**时写 `ALREADY_OPEN`（先写者胜），
  本次真打开由 `tmc_uart_open_core()` 记 `OPENED_NOW`、失败记 `OPEN_FAILED` + 原始码；
  probe 入口在打开前取 `was_open` 并一次记入三笔 ⇒ 复审抓到的 `actual_new_opens=1` 却
  `ALREADY_OPEN` 不再可能。
- **B3**：`open_state` 初值改为显式 `TMC_OBS_OPEN_NOT_REACHED`（0 不再被解释成"已打开"）；
  probe 入口给自己的三笔记 `ctx_ok=1`（入口已过上下文门这一事实），与"各阶段是否到达"分列；
  事务取锁收成私有 quiet/nonquiet 同一核心，公有 `tmc2209_cfg_txn_begin()` 保留原语义与其它
  调用者日志，probe 走 quiet ⇒ `[TMRO] BEGIN` 之前不再有中文；未取到事务时打印
  `probe REFUSED (-45)` 而**不再**无条件声称 `txn-held`（成功分支标题逐字保留，避免无谓移动
  既有负例注入锚点）；全部导出在事务归还之后。
- **附带**：`:810` 的 `b == RT_NULL` 判定移到任何 `b->start/b->total` 读取之前（当前公有入口都带
  预算，因此**不**宣称存在可达的真机崩溃，只是防御性收口）；期限字段改三态
  `UNEVALUATED/NO/YES`，只有真的做过比较才写 NO/YES；回声匹配改全流滚动窗口并导出样本内命中偏移，
  不再以"前 32 B 未见"断言物理无回声（§4.4 口径）。

## 4. C 组（采集器）改了什么

新脚本 `reports/reviews/phase8-tmc-uart-read-observation-r1/UARTReadDiagSerialR1.ps1`
（93797 B / sha256 `2e9c8ede…`，纯 ASCII、LF 行尾）。旧脚本原样保留。

- **16 件复用对象逐字节不变**：15 个 transport/工具函数（`Note … Fixture-Responses`）与 `finally`
  收尾块逐字取自已接受的 `M0Serial.ps1`（24064 B / `2131af0e…`），前六个命令的判据逐字取自上一轮
  `UARTReadDiagSerial.ps1`；生成器落盘后逐函数比对并打印 `IDENTICAL`，六项核对与 `finally` 同为
  `IDENTICAL`（否则拒绝交付）。**没有手写第二套串口层**，也没有整体重写。
- **最小 TMRO 完整性（新 `Check-TmroReply`）**：BEGIN/END 各恰一次且 END 不早于 BEGIN、
  `reads` 两处一致且 0..3、`planned=3`、`v=1 addr=0` 与本包一致、三笔 reg=06/01/02 结果身份各恰一次
  （缺席/重复/越界寄存器都拒）、SUCCESS+FAIL == `reads` 且 NOT_REACHED == 3−`reads`、
  每笔必需事实行（stage/ctx+txn+open+lock/flush/tx/**tx_written**/timing-or-start/
  rx+hdr+full+crc/total+buf+pub+late+exp+pre_trunc+gap/echo+match+val/samples）各恰一次、
  分块 `n` == 该行真实 hex 字节数、块 offset 连续、分块总和 == 声明长度、空样本必须
  `n=0 hex=-`、无 NUL、无 `EXPORT_ERROR`、行干净终止、行长不越过 96 B 打印界限。
  **没有**新建 CSV 平台、字段全域验证引擎、独立 Python 门或新元负例框架。
- **业务与捕获分离**：`read_ok / identity_ok / fields_ok` 与失败阶段
  `OPEN / TXN / READ / IDENTITY_MISMATCH / FIELDS_OUT_OF_DOMAIN` 分列；捕获完整与业务成功永不合并。
  交叉核对用**完整 32 位原值**：`val=`/`raw=` 与逐笔发布值必须一致，掩码低 8 位与低位十进制
  只是摘要；`fields_ok` 与"GSTAT ⊆ 0x7 / IFCNT ⊆ 0xFF"的域判定互相对账，未定义高位不许报全成功；
  `safe_for_enable` 恒 0，任何 1 都当矛盾拒绝。业务失败**不再**要求必须存在逐笔读 FAIL 或旧的
  `probe REFUSED` 文字（open/txn 失败、合法读到错误 VERSION 都是完整采集下的失败）。
- **横幅门（新 `Resolve-BannerGate`）**：`-ExpectedBuildBanner` 仍取本轮真实新镜像横幅
  （`5.3.0 build Oct 28 2026 02:23:31`，不是上一轮的 `23:50:33`）；捕获到横幅 ⇒ 逐字精确核对，
  一个窗口内出现两个互相矛盾的横幅即拒绝；**没捕获到**横幅 ⇒ 仅当该具体板级包已完成本包新 BIN
  长度范围逐字节回读（`-ImageContinuityConfirmed` 只在回读通过后才置真，开关本身不是证据）才允许
  继续，并记 `BANNER_NOT_OBSERVED`，然后按原就绪机制发那唯一一次 `spm status`；无横幅且无身份依据
  ⇒ 拒绝。为获取横幅**绝不**自动复位或重开 COM5。本轮只做这种编排的离线验证，未实例化 SerialPort。
- **Windows 5.1 真实执行**：`-SelfTest` rc=0、stderr 0 B、负控 51 条（`Expect-Rejected` 现在要求
  **具名理由**，理由不符即算自检失败）、禁止命令同 12 条、七命令 mock 各 1 次、被动窗口重放
  3757 B 散列一致、`Port` 对象被断言不是 `[IO.Ports.SerialPort]`。旧 23 条负控的实质约束全部保留
  （两条随语义更名/拆分，见 `collector_selftest.txt` §3），未以"Parser 通过"代替自检。

## 5. 随同更正（非阻断，按任务书 §4）

1. **A0 增量基线（勘误 R1-E1）**：与旧 `5f30…` 阶段对照必须用
   `fe3b64b4:reports/reviews/phase8-target-measurement-a0-export-r1/keil_build.txt` 的真实值
   Code=181106 RO=79766 RW=2188 ZI=91504 ⇒ 上一轮 URO 的 186426/80646 是 **+5320/+880/0/0**，
   本轮 187018/80746 是 **+5912/+980/0/0**；上一轮虚报的 +6568/+1216 不再沿用。manifest 现在把
   "vs 上一轮同目标"与"vs 更早阶段"两套对照分列，历史文档不回写成"一开始就正确"。
2. **提交数**：URO 轮实际是四个提交（`d2dd743 / ab76a07 / fb1a72e / 6324ac4`），上一轮回执漏了
   最早的 `d2dd743`；本轮直接在交接与证据里按四条引用，不为此另开登记轮。
3. **主机日志措辞**：新增/改写的表述统一为"端口真落 DATA 的字节 / 模型字节数"，
   不再使用"线上真实字节"（`tx_bytes_on_wire` 只是受控寄存器模型计数，不是目标线路波形）；
   历史 stdout 原文保留不改写。
4. **回声范围**：`tmc_obs_echo_eval` 不再只看前 32 B，改全流滚动窗口 + 样本内命中偏移导出；
   legacy 枚举名不作为"物理上无回声"的证据。
5. `tests/host/README.md` 里的 "5 项反向家族" 与本 runner 现有的 8 项家族口径不一致；该文件不在
   本轮白名单内，**未改**，在此登记为文档滞后项，留给下一次允许改动该文件时收口。

## 6. 构建与镜像（本轮真实值）

| 目标 | 0E/0W | Program Size | BIN | 横幅 |
|---|---|---|---|---|
| `rt-thread` | 0 / 0 | Code=179674 RO=77786 RW=2188 ZI=83784 | `default.bin` 258036 B `dccb7b63c9284d32…` | `Oct 28 2026 02:23:14` |
| `rt-thread-measure-a0` | 0 / 0 | Code=187018 RO=80746 RW=2188 ZI=91504 | `measure-a0.bin` 268340 B `a48400c881c539db…` | `Oct 28 2026 02:23:31` |

两目标相对上一轮同目标均为 Code +592 / RO +100 / RW 0 / ZI 0，与"只改 `tmc2209.c` 的导出与记账
代码、观测对象仍是 probe 栈上局部"一致。观测结构体在本轮为**单笔 188 B / 三笔 564 B**（上一轮
176/528；主机链在同一批定稿字节上实测 `obs_size=188|obs_x3=564`，合同上限 768 B）。
栈上局部之和**不等于**完整栈高水位 —— 完整水位需上板实测，本轮不声称。
AXF/map 原件本地保存不入库；`.uvprojx` 未改；第三个目标未构建。

## 7. 验证（全部在同一批定稿字节上，集中跑一次）

| 入口 | rc | 关键结果 |
|---|---|---|
| `build_and_run.sh` | 0 | V1/V2 与原负例/元负例既有链保持 |
| `run_io_behavior.sh` | 0 | IO1–IO10 两变体 27/29/40/42 + 非法宏拒绝保持 |
| `run_safety_data_semantics.sh` | 0 | SD 两变体保持 |
| `run_stop_result_s1.sh` | 0 | S1 target 50 / host_ovr 65 保持 |
| `run_stop_priority_s2.sh` | 0 | target **53** / host_ovr **109** 全 PASS；`read_obs` 两变体各 30/30；台账自洽、无 SKIP 名额 |
| `run_sp_measure_a0.sh` | 0 | A0 off/on/min 保持（采样继续暂缓，时长 `NOT_ACHIEVED` 不改） |
| `test_path_guard.sh` | 0 | 路径沙盒回归保持 |
| `REVERSE=1 run_stop_priority_s2.sh` | 0 | **八项**家族全 META-OK：原 7 项 + 新增导出侧第 3 项 read_obs 家族 |
| `run_negatives.sh` | 0 | 既有负例链保持（`probe_writeback` 锚点未再动） |
| `run_meta_negatives.sh` | 0 | 元负例按原因判定保持 |

对照（`baseline_and_fixed_checks.txt`）：同一批输入在 `6324ac4` 冻结生产字节与本轮定稿字节上的
真实输出逐条并列 —— `partial_tx`（written=2 无导出 → `tx_written=2`）、`rx48`（`n=32` 只列 16
字节 → 两块 16 B 可重建）、`rx_entry_expired`（`obs_rc=0 obs_reason=0` → `-117/3` 且接缝第 4 次
tick 命中）、`fresh_open`（`ALREADY_OPEN` + `actual_new_opens=1` → 三笔 `OPENED_NOW`）、
`txn_unavailable` / `open_fail`（假 `ALREADY_OPEN`/`ctx=0`/前置中文 → `OPEN_NOT_REACHED`/`ctx=1`/
纯 ASCII + `REFUSED (-45)`）。C 组八行旧判定 vs 新判定同表并列。

本轮自己引入并被修掉的问题（原文保留在 `host_results.txt` §6 与 `collector_selftest.txt` §4）：
夹具新用例首版 6 条 FAIL（根因是测试自己没调用 `tmc_probe_dump()` —— 单笔读按设计不打印）、
URO26(a) 期望的 OPENED_NOW 口径先写错、采集器摘要捕获组下标偏一位、
`Expect-Rejected` 前缀正则拼错、三条负控拼接自带多余 CR、两处漏传 Command 参数、
生成器把旧 probe 判据连带搬入导致六项核对报 CHANGED 而拒绝落盘。都在本轮范围内修好，未停成另一张任务书。

## 8. 边界确认

- `hardware_actions = []`。未创建真实 SerialPort、未开 COM5、未发目标命令、未枚举设备/端口/探针、
  未读 Flash、未下载/复位、未改供电/线路/ENN/SW1、未接电机或输出 STEP/DIR/HIGH、未做
  arm/start/ramp/fault_reset/selftest、未运行 `sw1_cut.ps1`。Keil 仅批处理 `-r` 构建。
- 四个安全宏在默认与 A0 目标均 0（目标级 Define 无覆盖 + ARMCLANG `-dM` 预处理实证）。
- 预算常量、地址 0、115200/8N1、CRC、帧搜索与成功发布门、无重试、probe 三读 0 写、
  `eff_end = b->start + used + allowance` 全部未动。
- 冻结引用只读核对未动：`main = 1b3757eec550802a3973e66790af422d81d3d373`、
  `phase7/full-embedded-software = 6d3fbc559d45673d7550eb73dec5a4ade5bb34df`、
  标签对象 `097935fec5a6f2467261ab1ad449ff0b1d398250` → 解引用
  `e40a9b95ef709e6a80444f9f3949dfdd70ba96ba`。
- 分支 `phase8/tmc-drive-config` 保持；未 reset/rebase/merge 凑基线，未 force push，未改全局 Git
  配置，沿用仓库 local 公开 noreply 身份。
- 证据脱敏（有损，规则已在每份文件头部声明）：本机绝对路径 → `<仓外工作区>`；
  探针 UID、本机用户名未入仓库；原始未脱敏输出与 AXF/map 留在仓外，未删除。
- 测试、构建、脚本自检都是真实执行，**不**称纯文档轮；失败原文未改写。

## 9. 仍未证明 / 继续开放

- `TMC_LINK_ON_BOARD_UNPROVEN`：本轮全部是受控主机模型与离线负控结果，**不是**真机新观测，
  也不证明板上那次 `-117` 的根因。
- `BLOCKED_ROLLBACK` 未关闭：`default.bin` 不等于板上 e40 原镜像；不许自行读 Flash 求"回退依据"。
- B-1、BUG-008、RISK-03/03a（含 `tmc_xfer_lock`/`mot_lock`/`ss_lock`/`cfg_lock` 的
  `RT_WAITING_FOREVER` 与最坏停机时延）、物理 RISK-04-b、M-1/M-2/M-3 按实际开放部分继续保留。
- A0 目标采样继续暂缓；`A0_DURATION_EVIDENCE_NOT_ACHIEVED` 不变。电机参数/限流实物依据在真正
  电机使能前另用，不阻塞离线工作与零电机只读通信诊断。
- 采集器未由真机运行验证（本轮 0 设备动作）；`tmroAll/tmroOk` 行终止核对分支在现有更具体的前置
  检查之下不可单独触发，作为纵深防御保留并如实登记为"未由负控具名命中"的残余边界。
- S2 既有 GO 不因本次局部观测返修撤回；本包也不构成任何板级动作授权。

## 10. 停止位置

```text
URO_R1_OFFLINE_READY / AWAITING_INDEPENDENT_SOFTWARE_IMAGE_AND_CAPTURE_REVIEW
HARDWARE_ACTIONS_THIS_ROUND=0 / FOUR_DEFAULT_AND_A0_SAFETY_MACROS=0
TMC_LINK_ON_BOARD_UNPROVEN / SAFE_FOR_ENABLE=false
M2_M3_NOT_AUTHORIZED / A0_DURATION_EVIDENCE_NOT_ACHIEVED
```

下一步（需用户与复审分别裁定）：云端对 `a483dcb` 的软件/镜像/采集三件独立复审 ⇒ 通过后由用户对
`board_candidate.md` 这一份**含真实散列**的现场包给一次批准。不再开离线采集器准备轮。
