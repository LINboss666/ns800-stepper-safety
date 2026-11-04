# M8-R7F6 现场轮交接：b502 现有接线一次被动采集 + 五条只读查询

日期 2026-11-05（本机 +08:00）。任务书 `NS800_R7F6_B502_Passive_Readonly_Task_20261105.zip`
（303880 B，sha256 `6179c093b13566db25af620e4bd05093103e834ff655ebc4c27422f7e2f58dc6`），
A 定稿范围，操作者转交并明确要求执行 ⇒ 那条原话 `按此包执行` 就是本轮新批准令牌
（15 B，sha256 `ffeff4d3ad68…dd106641`；原文留仓外，仓库内只登记字节数与散列）。
批准身份 `M8R7F6-b502-la-path-static-20261105`，新账从 spent=0 起，旧账旧回执一字未改。

## 一句话结论

现场流程按 §4 的 A→B→C 一次走完，没有重试也没有追加动作：**串口层通过**（一个 COM5 会话、
五条只读查询、12 项关键预期逐条命中、零写配置、零运动、0.468 s 远小于 45 s 硬截止）；
**仪器层通过**（真实设备、一次 Start/Stop、8 路全开、1 MS/s 登记值、无触发、总窗 1.98 s、
采集区间完整包住串口会话，原生件与 CSV 各一件）；**接线解释仍不可分辨**（电平 `01111111`
与上一场已接线、以及更早探头未接的浮空基线**完全相同**，本轮又没有事件对照）。
新镜像继续 NOT_ON_BOARD，`polarity_measured=false`、`core_protection_verified=false` 保持。

## 三层分别交付（不拼"全过"）

| 层 | 结论 | 关键证据 |
|---|---|---|
| 串口 | 通过 | `field_run/run-20261104T182042Z/`（5 份响应原字节 983/1128/612/522/983 B、会话台账、dispatch 原文）；`proof/field_judgement.txt` 里 `ASSERT_STEP_RECOMPUTED_ALL_OK`、12 条 `POSITIVE_*`、`PORT_OPENED_EXACTLY_ONCE`、`LEDGER_*` |
| 仪器 | 通过 | `instrument/`（start/stop/save/export 四次调用的请求与回执原文、脱敏 `.sal`、`digital.csv`、两张界面截图、`capture_meta_extract.json`）；判据 `LA_*` 共 18 条 PASS |
| 接线解释 | 不可分辨（如实） | `LA_PATTERN_MATCHES_PREV_CONNECTED_FIELD` 与 `LA_PATTERN_MATCHES_UNCONNECTED_BASELINE` 同时成立；`WIRING_INTERPRETATION_BOUNDARY` 写明无事件对照 ⇒ 不宣布目标接触通路已被电气证明，也不判操作者接错 |

通道身份：GUI 行标签与导出列名都只是 `Channel 0…7`，没有任何信号名 ⇒ `CHANNEL_MAPPING_UNRESOLVED`；
操作者"软件里可能 CH0=DIAG、CH1=ENN"保持申报级，未按界面核实，也不靠"一低一高"反推。
共地/接触本轮未观察 ⇒ `UNKNOWN`。

## 设备动作计数

开口 1、dispatch 1、Start 1、Stop 1、原生保存 1、CSV 导出 1；
下载/回读/DAP/复位/额外上电/apply/极性确认/IRQ 开关/arm/start/运动/zero/learn/commit/
配置保存/清日志/重试/拆探头/参考电平/注入/DMM 全为 0。
另登记一次主机软件动作：控制通道原本不可用（Logic 2 未运行、`10530/10430` 无监听），
我启动了本机已安装的 Logic 2 把它恢复（不占设备额度；启动后界面仍是 `Session 0` 空时间轴，
未产生采集会话）。这一步任务书没写，如实登记在 run_record §19 自伤第 2 条。

## 判读可复算

`tools/r7f6_preflight.py` → `fails=0`（包落位 30/30 逐字节、清单 29 条、身份五件、
冻结源 10 件"包字节==登记==`0cd6458` blob"三方全等、tip 与冻结件差 6 件、
`VALIDATE_PLAN` 唯一原始 FAIL `PLAN_MOTION_CAP_BUDGET` 保留、10 项 scope guard、
产品与主机链目录 0 改动）。
`tools/r7f6_field_judgement.py` → `TOTAL=49 FAIL=0`，只读**已入库字节**重算，含 6 项 must-FIRE 自检
（改坏一个预期值 / 注入裸 NUL / 注入业务失败行 / 少一份响应 / 把会话搬到采集之外 / 把采集起点搬到会话之后）。

## 隐私与历史暴露

本轮把仪器标识全部脱敏后才入库：`deviceId` 换成 `MASKED_SHA12:42055f5a3c56`，
原生 `.sal` 另存"只替换 `meta.json` 里 legacyDevice.deviceId"的脱敏副本（其余 9 个条目逐字节未动，工具已核），
未脱敏原件留在仓外并登记原字节数与原散列。
**新发现**：上一轮已公开的 `field_r7f5a/la_capture_id8.sal` 的 `meta.json` 里带着同一台仪器的
20 位十进制序列号 —— 本轮才查出。冻结件不回改、不删除，处置方式等 A/操作者定夺。

## 待操作者

采集与端口均已关闭。按任务书 §4.C，收口时切断 VM＋MCU 最多 1 次且不再上电；
我会登记实际时刻与本人观察，没观察就写"未观察"，不把本轮报告倒填成 R7F5A 当时的收口时间。
