# R7F6 现场判读：b502 现有接线被动采集 + 一次只读状态会话

任务身份 `M8R7F6-b502-la-path-static-20261105`。执行批准 = 操作者要求执行本包的真实原话
`按此包执行`（15 B，sha256 `ffeff4d3ad685955daf7ee4f331ab2eabd4606abf3e41a9b056058f7dd106641`；
原文留在仓外 `C:/m8r_approvals/token_r7f6.txt`，仓库内只登记字节数与散列）。
判读由 `tools/r7f6_field_judgement.py` 从**已入库字节**独立复算：`proof/field_judgement.txt`
`TOTAL=49 FAIL=0`。下面三层结论分别交付，不拼成"全过"。

## 0. 时间线（本机 +08:00；同一台主机时钟）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 02:15:44 | 启动 Logic 2 主机程序（控制通道原本未监听，见 §4 自伤） | `proof/field_judgement.txt` 前置说明 |
| 02:15:49 | MCP `127.0.0.1:10530` 就绪（1 次探测） | 同上 |
| 02:16 | 只读枚举设备：`isSimulation=false`，`deviceType=Logic` | `instrument/la_devices_masked.json` |
| 02:18:29 | `--approval-init` 开新账（spent=0 pending=0） | `field_init/`、`ledgers/` |
| 02:20:01 | 定稿计划离线预跑 stub+sim（**0 设备动作**，不带 approval-id 故不污染新账） | `proof/rehearsal_stub.stdout.txt` |
| 02:20:42.526 | A：唯一一次 Start 请求 | `field_run/…/field_run_summary.json` |
| 02:20:42.667 | Start 回执 `captureId=1`，无 isError | `instrument/la_start.json` |
| 02:20:42.657 | 采集实际起点（原生 meta，早于会话） | `instrument/capture_meta_extract.json` |
| 02:20:43.545 → 44.373 | B：唯一一次 dispatch（rc=0，未超时） | `field_run/…/dispatch.*` |
| 02:20:43.59 → 44.01 | 端口开→5 条只读查询→端口关（会话 t_s 0.046…0.468 s） | `field_run/…/S_S1/ledger.jsonl` |
| 02:20:44.378 | 采集末行（CSV） | `instrument/digital.csv` |
| 02:20:44.498 | C：Stop 同一 captureId；总窗 **1.98 s** ≤ 60 s | `instrument/la_stop.json` |
| 02:21:19 / 02:21:25 | 原生保存 / 数字 CSV 导出（各 1 件成功） | `instrument/la_save.json`、`la_export.json` |

## 1. 串口层（只读会话）

发出去的就是计划里的 5 条，顺序一致，一条不多：
`system_status`、`motor_status`、`safety_irq_status`、`safety_status`、`system_status`。

- 5 份响应原字节入库（`01_sys_initial`…`05_sys_final`），**裸 NUL=0、严格 UTF-8 通过、危险文本门通过**；
  每份首行是该命令的回显，与计划逐条对齐。
- 未改执行器的 `split_rows + assert_step`（含 `expect_contains / expect_values / expect_fields /
  expect_profile=AT_REST`）对 5 份字节**重新算一遍，全部 verdict=OK**；判据本身经 4 项突变自检
  （改坏一个预期值 / 注入裸 NUL / 注入业务失败行 / 少一份响应）全部判红。
- 任务书 §4.B 的关键预期逐条命中：`bootstrap-complete=YES abort-stage=0`、`state=READY fault=0`、
  `irq_gate=CLOSED`、`polarity=not-confirmed`、`protect_ready=NO`、`drive_cfg=UNCONFIGURED`、
  `arm gate fail mask=0x18 (arm REFUSED)`、`state=0 cur=0 tgt=0 armed=0`、
  `request=LOW pad=0 expected_ENN=HIGH ENN_fb=1(resolved) verdict=OK`、
  `TMC_DIAG pin=3 raw=0 safe=0 irq=RISING`。
- `PORT_OPEN_ATTEMPT=1 / PORT_OPEN=1 / PORT_CLOSED=1`，端口只开一次；会话 0.468 s < 申报 45 s。
  每条响应的字节数与 115200 8N1 的**线上时间下界**逐条相容（例：983 B 下界 0.085 s，实测 0.079 s
  落在同一次读窗口内；1128 B 下界 0.098 s，实测 0.125 s），所以"快"不是靠丢弃字节得到的。
- 两次 `system_status` 的原始字节散列相同（`73abf301…`）：板子全程静止，状态未变。
- 计划禁做清单 16 条命令**一条都没发**；`retry=0`、`config_save=0`、`motor_run=0`、
  `download/readback/explicit_reset/approved_reboot=0`。
- 台账：`spent={port_open:1}`，其余 21 项 0；`pending=[]`；`commit_failures` 键缺席（本轮没有清理义务）；
  账本绑定的 executor/image/plan 三件散列与 `IDENTITY.json` 全等，22 项额度与 plan.quota 全等。

## 2. 仪器层（被动采集）

- 真实设备：`isSimulation=false`；USB 侧 `Saleae Logic USB Logic Analyzer` 状态 OK（只读枚举）。
  序列号不入公开仓库，登记为 `MASKED_SHA12:42055f5a3c56`。
- 唯一一次 Start：8 路数字通道全开、`digitalSampleRate=1000000`、`manualCaptureMode`（无触发）、
  请求 `bufferSizeMegabytes=512`；回执只有 `captureId=1`，**接口没回实际容量**，因此不写"已证明容纳 512 MB"，
  MB 也不折算成已保留样点数。
- Stop/Save/Export 全部绑同一个 `captureId=1`；总窗 1.98 s ≤ 60 s；未二次 Start，未重采。
- 原生件 `capture_id1_sanitized.sal`：`meta.json` 里登记 `enabledChannels` 8 路、
  `sampleRate.digital=1000000`、`captureMode=FreeRun`、`bufferSizeMb=512`、`digitalTriggerTime=-1`
  （无触发事件）。这些是**文件内登记的设置值**，不是独立测量。
- CSV 真实表头：`Time [s],Channel 0,…,Channel 7`；数据 2 行。8 路**全程零跳变**，
  电平 = `Channel 0` 低，`Channel 1…7` 高（`01111111`）。
- 覆盖：采集 42.657…44.378 s，串口会话 43.545…44.373 s ⇒ **整段会话都在保留区间内**，
  没有环形缓冲吃掉开头的迹象（不判 `COVERAGE_INCOMPLETE`）。该覆盖门经两项自检
  （把会话整体搬到采集开始之前 / 把采集起点搬到会话之后）都会判红。
- 通道身份：GUI 行标签与导出列名都只是 `Channel 0…7`，**没有任何信号名**。操作者的
  "软件里可能 CH0=DIAG、CH1=ENN"仍是申报级，未从界面核实 ⇒ `CHANNEL_MAPPING_UNRESOLVED`。
  按任务书不靠"一低一高"反推身份。
- 保存参数用的是 `filepath`（未凭记忆改成 `directory`）；导出用 `directory`，两者参数集已入库对账。

## 3. 接线解释（只描述，不归因）

- 本轮电平图样 `01111111` 与上一场**已接线**的 R7F5A 完全相同，也与更早**探头什么都没接**的浮空基线完全相同。
  因此这一层证据无法区分"接线正确且系统静止"与"探头未接触/无共地"。
- 本轮零运动、零注入、零参考电平切换、零拆探头对照 ⇒ **没有事件对照**，不宣布目标接触通路已被电气证明，
  也不判操作者接错。
- 共地/接触：本轮未观察 ⇒ `UNKNOWN`（不写成 PASS，也不因此扩大动作）。
- 输入范围依据仍沿用上一轮归档的商家声明 0–5 V，只用于现有 3.3 V DIAG/ENN 接法；不升级成实测耐压或型号鉴定。
  实际接线未超出本包既定低压信号范围（无 VM/电机相线/不明电源点接入探头）。

## 4. 本轮自伤与如实登记（失败原文不删）

1. 落位核对第一次报 `与 zip 逐字节不符=1 ['SHA256SUMS.txt']`：实况是包内清单这一件**根本没落进仓库目录**。
   补齐后 30/30 逐字节相等。
2. 分析仪控制通道一开始不可用：Logic 2 未运行，`10530/10430` 均无监听。这是"通道未就绪"，不是"仪器不存在"
   （USB 侧设备枚举 OK）。我启动了本机已安装的 Logic 2（主机软件动作，不占任何设备额度，未产生采集会话：
   启动后界面仍是 `Session 0` 空时间轴），5 s 后 `10530` 就绪。
3. 判读工具第一次跑出 **13 条 FAIL**，全部是我自己的工具缺陷，不是现场问题：
   `split_rows(command=…)` 传了 bytes 而非 str；锚定正则没先剥 CRLF；会话台账的键名是 `kind` 而我读 `ev`。
   修正后同输入重算 `TOTAL=49 FAIL=0`。
4. 顺带修掉两个**会假绿**的判据：`RX_BYTES_ARE_WIRE_TIME_FEASIBLE` 在事件列表为空时会空真通过，
   已加 `len(wire)==5` 前置；`dropped_response_count` 自检原本写成恒真表达式，改成用同一个
   `five()` 判定函数分别跑"少一份"与"齐五份"。
5. 前置工具第一次把 `VALIDATE_PLAN` 的 FAIL 行取成第 2 个字段（打印出 `['all']`），改为取第 1 个字段，
   现在如实显示唯一原始 FAIL 是 `PLAN_MOTION_CAP_BUDGET`（本零运动范围裁为 N/A，原文保留）。
6. 归档第一次漏了 `field_run_summary.json`（时间线），补进清单后重跑归档；判据文件随之重算一次。
7. **隐私新发现**：上一轮已公开的 `field_r7f5a/la_capture_id8.sal` 的 `meta.json` 里带着同一台仪器的
   20 位十进制序列号（本轮才查出）。冻结件不回改、不删除；本轮起原生件一律另存脱敏副本并登记原字节/原散列，
   未脱敏原件留在仓外。`instrument_manifest.json.privacy.known_historical_leak` 已写明。

8. 脱敏第一版只做"键级"替换，漏掉了**字符串里**的同一仪器标识（接口回执 `content[].text` 内嵌 JSON 原文、
   时间线 `start.args.deviceId`）。暂存预检的泄漏扫描抓到 4 处命中（其中一处是扫描器把明文写成了自己的
   判据字面量）。修正：归档改成键级+字符串级双替换并加"入库件残留明文=0"硬门；扫描器改成不硬编码明文、
   从仓外原件读出后构造判据，并自检"扫描器自身不含任何被扫值"。重跑后 `LEAK_SCAN 命中=0`、`SCRUB_RESIDUE=0`，
   判据 49/49 仍全 PASS。

## 5. 保持不变的边界

`polarity_measured=false`、`core_protection_verified=false` 保持；真实 DIAG 故障边沿、停 STEP、
保护触发与恢复全部未验证。新镜像 `23691e8d…`/`165d8bdc…`/`df7584c3…` 保持 **NOT_ON_BOARD**
（本轮 0 下载、0 回读、0 复位、0 额外上电；板上仍是历史已证的 b502 `502dca5d…`，本轮未重新证明在板）。
R7F5A 的物理断电仍是 `PENDING_OPERATOR_REPORT`，本轮不靠接设备关闭它。
旧包、旧执行器、旧 rows/plan、旧台账、旧批准回执一字未改。

## 6. 收口

采集与端口都已关闭。按任务书 §4.C，操作者可切断 VM 与 MCU 一次（最多 1 次，不再上电），
DAP/USB 供电也计入"MCU 是否真正断电"的现场判断。本轮登记的实际动作计数：
COM5 开口 1、dispatch 1、LA Start 1、Stop 1、原生保存 1、CSV 导出 1；其余全部 0。
断电时刻与观察结果等操作者回报后登记；没观察就写"未观察"，不倒填 R7F5A 的收口时间。

推送与远端回读：主提交 `d6bbab8`（父 `4e533c3`）快进推送一次成功。`tools/r7f6_remote_readback.py`
在 `d6bbab8` 上逐 blob 复算两条清单合计 **304 条 = ok 304 / mismatch 0 / not_checked 0**；
A 包 30 件在远端与 zip 逐字节相等；旧执行器、b502 BIN、R7F5A handoff/CSV/原生 .sal 共 5 件
在 tip 与 `4e533c3` 上 blob 全等（未动）；run_record §16/§17/§18/§19 各命中 1 次；
两份清单自身的远端 blob == 磁盘。回读报告留仓外，避免"清单登记自己的回读件"这种自引用；
回读工具本身随追加提交入库，追加提交后再跑一次同样回读并同样留仓外（追加提交 `7a82147` 上实测
305 条 = ok 305 / mismatch 0 / not_checked 0，fails=0）。

### 6.1 断电登记（操作者报了两次）

| 事实 | 内容 | 等级 |
|---|---|---|
| 操作者原话 1 | 「断电了刚才」（02:50 前后到达，未报精确时刻） | 申报 |
| 操作者原话 2 | 「51分我又重新断电了一次」（自述 02:51，按分钟级采信） | 申报 |
| 操作者原话 3 | 「每次断电之后我都会重新上电」（本机落盘 02:54:48） | 申报 |
| 任务书上限 | 收口切断 VM＋MCU **最多 1 次**，且**不再上电**（`additional_powerup=0`） | 上限 |
| 对账结果 | 切断申报 **2 次** ⇒ `OVER_CAP_POWERDOWN(2>1)`；两次之间确有重新上电 ⇒ `OVER_CAP_ADDITIONAL_POWERUP(>=1>0)`；如实登记，不改写成"一次"、也不写成"没上电" | 判定 |
| 操作者原话 4 | 「还通着电」（本机落盘 02:57:08） | 申报 |
| 收口终态 | `POWERED_ON_OPERATOR_CONFIRMED` ⇒ 任务书"不再上电/停在断电态"的预期 **未达成**（`CLOSEOUT_POWERED_DOWN_EXPECTATION_NOT_ACHIEVED`），如实登记 | 判定 |
| 操作者原话 5（今后口径） | 「我希望默认不断电」（本机落盘 03:00:01）⇒ 采信为**今后现场轮默认保持通电**，我不再索要断电动作/时刻 | 申报 |
| 该口径的生效边界 | 条款写在 A 的任务书里（§2/§4.C），所以这句是**给 A 的变更请求**，需 A 在后续任务书采纳才生效；本轮已登记事实与标签**不回改** | 判定 |
| 当前是否仍在上电 | 已由操作者确认：是。我没有再去核实（核实要动设备，本轮额度已用完），主机侧枚举只与这条申报相容，不构成独立证据 | 申报 + 判定 |
| 主机侧观察 | 02:50:50 与 02:52:38 两次只读枚举：COM5、CMSIS-DAP v2、WebUSB: CMSIS-DAP、Saleae 全部仍 `Status=OK`，两次之间 USB 拓扑无变化 | 实测（只读，未开 COM/未访问 DAP） |
| 观察能证明什么 | 探针由 PC 的 USB 供电，与板端 VM/MCU 不同电源域 ⇒ **既不能证实也不能证伪** MCU 已断电，标签 `MCU_POWER_STATE_NOT_HOST_OBSERVABLE`；任务书要求计入的正是"USB 路径未断"这条事实 | 判定 |
| 对结果的影响 | 无。全部现场数据在 02:20:42–02:21:25 已采集、保存、导出并归档，断电发生在测量之后；两次报断之间我未执行任何设备动作，本轮动作计数不变 | 判定 |
| R7F5A | 仍是 `PENDING_OPERATOR_REPORT`，这两次断电不回填那一轮 | 保持 |

私有 DAP UID（48 位十六进制）不写进任何入库件，只记长度 48 与 sha12 掩码 `586cb3f3ac73`。
原文与两次观察的完整记录见 `POWERDOWN_REPORT.txt`。
