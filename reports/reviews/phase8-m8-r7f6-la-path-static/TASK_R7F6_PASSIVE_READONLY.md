# R7F6：b502 现有接线被动采集＋一次只读状态会话

日期：2026-11-05。本包为 A 定稿的固定现场范围。固件沿用板上历史已证的 b502；本轮不下载新固件。

## 1. 当前事实与操作者修订

- 操作者已在现场，原话：`VM 和 MCU我已经重新上电 在现场`。该上电发生在本包之前；不为本轮再加重启、复位或启动等待窗。
- 物理探头标签：CH1 接 DIAG；CH2 接最终 TMC_ENN。操作者补充：**软件里可能是 CH0＝DIAG、CH1＝最终 ENN**。这是暂定映射，不冒称已从界面核实。
- **取消拆探头及 GND/3.3 V 电平对照，保留当前接线。** 不制造信号跳变，不往 DIAG/ENN 强加电平，不以正控为执行前置。
- 软件若为 CH0…CH7 或 D0…D7，按实际界面/设备元数据登记；CSV 导出后由 agent 读取真实表头与列序。映射暂不能唯一核实时仍允许本包的 8 路被动采集，仪器判读保留“不确定”，不得靠一低一高反推身份。

## 2. 固定额度

| 动作 | 上限 |
|---|---:|
| COM5 开口与 dispatch | 各 1 |
| 逻辑分析仪采集 | 1 次 Start、1 次 Stop |
| 同一次捕获的原生保存、数字 CSV 导出 | 各 1 件成功交付 |
| 下载、回读、DAP 访问、复位、额外上电 | 0 |
| apply、极性确认、IRQ 开/关、arm/start、运动 | 0 |
| zero/learn/commit、配置保存、清日志、现场重试 | 0 |
| 拆探头作对照、参考电平切换、目标信号注入、DMM 测量 | 0 |
| 收口时由操作者切断 VM＋MCU | 最多 1 次；不再上电 |

仪器额度由外部记录独立约束，不能说它与串口账本原子联动。保存/导出参数错误允许针对**同一 captureId**修正并保留失败原文，不允许因此再 Start 或重开 COM。

这是新范围、新账本。操作者转交本包并明确要求执行时，以那条真实指令作为新批准；不要把“已上电”或“编写任务书”的上下文本身冒充执行批准。若操作者已经明确批准本包，不再二次询问，不另开准备提交轮。旧账、旧批准和旧回执均不回写。

新批准身份固定为 `M8R7F6-b502-la-path-static-20261105`。`operator_context.txt` 仅保留本次编写请求，**不是预制批准令牌**。

## 3. 执行前只做必要核对

将本包全部内容按原相对目录落到仓库 `reports/reviews/phase8-m8-r7f6-la-path-static/`；原件先在仓外核对 SHA256SUMS，再逐字节落位。不得覆盖旧目录或改执行器。

1. 核对执行器、plan、rows、b502 BIN 的真实字节身份，结果与 `IDENTITY.json` 相符。旧执行器仍是 `33d3f0af…`，BIN 仍是 292708 B / `502dca5d…`。
2. 当前开发 tip 已包含未上板的 R7D1，不能把它当作板上源码。S0 的原 40 条打印来源核对，已显式绑定本包 `frozen_b502_source/`：来自历史 `0cd6458` 的 10 份 Git 源文件，身份见 `FROZEN_SOURCE.json`。这些是**只供旧镜像打印来源核对的归档副本**，不能回灌产品目录，也不声称是本轮重新构建或读板。
3. 只读检查分析仪已连接、实际设备并非模拟、通道标签、现有线缆与 GND 接法。看不清的映射/共地/接触事实就记 UNKNOWN；不补作电平对照，不把未核实写 PASS，也不因此扩大动作。
4. 已有输入范围依据沿用上一轮归档的商家声明 0–5 V，只用于现有 3.3 V 逻辑接法；不升级成实测耐压或官方型号鉴定。探头和地线均不接 VM、电机相线或不明电源点。发现实际接线超出本包既定低压信号范围时先停止。
5. 旧账只读留存，新账必须与新 plan/执行器/镜像身份绑定，spent=0、pending=0。不开任何设备来探测或验证离线参数。

无需提前要求操作者报 CSV 第几列、软件缓冲样点数或完整启动日志，agent 自己从实际输出读取。

## 4. 唯一一次现场流程

### A. 先启动被动采集

沿用本机已可用的分析仪控制通道，先读当前工具 schema。8 路数字通道全开，1 MS/s，立即采集/手动停止、无触发；请求 512 MB 缓冲并登记请求与接口实际回执。若接口不回实际容量，不写“已证明容纳 512 MB”；MB 也不直接换算成已保留样点数。

Start 后确认真实 captureId 和 RUNNING 状态，记录本机带时区时间及可用的单调时钟。启动失败或无法确认在采集，保存首错并停止，不开 COM、不再 Start。

### B. 只执行固定串口包

采集已确认在跑后，立即运行 `field/plan_r7f6_b502_readonly.json`，严格使用未改执行器和 `--transport real --backend real --profile response_driven`。一个 COM5 会话，硬截止 45 s，命令顺序只有：

```text
system_status
motor_status
safety_irq_status
safety_status
system_status
```

关键预期：bootstrap-complete=YES、READY/fault=0、UNCONFIGURED、arm mask=0x18、armed=0、cur/tgt=0、禁能请求 LOW/pad=0、ENN 反馈 HIGH/1、IRQ CLOSED、polarity not-confirmed、protect_ready=NO。全部已写成 plan 的正面断言，不按终端肉眼猜。

这次重上电若没有实际清掉 MCU RAM，或状态另有变化，首条查询可能具名拒绝；如实 STOP，**不补复位、不 apply、不手动确认极性、不重开口**。已有状态文字中的预期 arm REFUSED 仅按原锚定白名单放行；业务命令错误、危险文本、裸 NUL、非法 UTF-8 的硬门不放宽。

本包 rows 另带三份清理响应夹具，是未改装配器的结构要求，**不是待发送步骤或硬件授权**；stop/disarm/status 清理额度全为 0，本包不建立运动清理义务。

### C. 关闭与保存

串口正常关闭或首错退出后立即 Stop 同一 captureId；采集总窗最多 60 s。若 dispatch 无法按时退出，外部 60 s 截止同样停止采集并登记，不加新业务命令。

保存同一次捕获的原生文件与完整数字 CSV，读取真实导出列。使用工具 schema 的真实参数；已知此前 `save_capture` 使用的是 `filepath`，不能凭记忆改成 `directory`。保存原始接口请求/回执、captureId、有效通道、采样率、起止时间、端口关闭事件与所有响应原字节。

原生捕获必须实际保留从第一条串口发送到最后响应/关闭的整段；如果环形缓冲覆盖了开头或时间基准不足，就登记 COVERAGE_INCOMPLETE，不重采。导出行少、文件小或零跳变均不单独判“没采到”。

完成采集/关端口后，操作者可切断 VM 与 MCU 一次；DAP/USB 供电也计入 MCU 是否真正断电的现场判断。记录实际操作时刻与本人观察；没观察就写“未观察”。不把本次报告倒填成 R7F5A 当时的精确收口时间，旧历史记录继续原样保留。

## 5. 调用口径

现场 agent 用已有执行器路径：

```text
reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py
```

本包 `signed_inputs/` 是该执行器的逐字节见证副本；优先调用仓库原路径，并先核对 SHA256。开账与执行都带相同 plan、repo-root、approval-id、approval-root；真实执行再带原样相同的 approval-token / expected-token。批准令牌从操作者**要求执行本包的真实原话**取得，不能自拟、拼接或拿旧轮次的替代。

命令形态：

```text
python EXECUTOR --approval-init --plan PLAN --repo-root REPO --approval-id M8R7F6-b502-la-path-static-20261105 --approval-root PERSISTENT_NEW_LEDGER_ROOT
python EXECUTOR --dispatch --transport real --backend real --profile response_driven --plan PLAN --repo-root REPO --approval-id M8R7F6-b502-la-path-static-20261105 --approval-root PERSISTENT_NEW_LEDGER_ROOT --approval-token ACTUAL_OPERATOR_GO --expected-token ACTUAL_OPERATOR_GO --out UNIQUE_RUN_ROOT
```

上面大写项是说明变量，由 agent 按本机真实路径和真实执行指令展开，不能当字面量运行。新账根与运行根放仓外持久位置；无需 UID 文件，因为本包所有 DAP 动作为 0。

## 6. 判读与交付

分别交付，不拼“全过”：

- **串口层**：5 条只读命令完成情况、字节门、正面状态断言、唯一开口和零写配置/零运动、spent/pending/cleanup_errors、时限与首错。
- **仪器层**：真实设备/捕获文件、覆盖区间、通道标签与 CSV 列的实际证据、静态电平/跳变数量。软件 CH0/CH1 若只有操作者暂定映射，应标明证据等级；无法核实则 CHANNEL_MAPPING_UNRESOLVED。
- **接线解释**：若仍为全程平线，记录其与上一场浮空基线是否相同。它可能符合正确接线下的正常静态，也可能符合接触/共地问题；不据此归咎操作者接错。没有事件对照时，不宣布目标接触通路已被电气证明。

`polarity_measured=false`、`core_protection_verified=false` 保持；真实 DIAG 故障边沿、停 STEP、保护触发与恢复全部未验证。新镜像 `23691e8d…`/`165d8bdc…`/`df7584c3…` 保持 NOT_ON_BOARD。

原生数据、CSV、五响应原文、两类账本、实际通道记录、判读与 handoff 按惯例入新目录并推送回读。若碰到软件参数/路径错误且尚未碰设备，可以修正调用方式并保留失败原文；不能改本包固定步骤、额度或判据。已发生设备首错则按账本停住，不重试。最终远端回读原文允许留仓外，避免自引用循环。

## 7. A 的离线验证

A 设备动作 0。未改执行器的最终 stub+sim 整包预跑：S0/S1 完成、5 条查询、status=OK；模拟开口 1，其余模拟动作 0，不能当成硬件结果。

`--validate-plan` 的原始输出保留。唯一 FAIL 为已登记的 `PLAN_MOTION_CAP_BUDGET`（空运动包），A 在本零运动范围裁为 N/A；保留原 FAIL，不改执行器或填假运动额度。不声称原检查器全绿。

两次有意义的离线初错也保留：① 当前 R7D1 源码缺少旧同步 learn 的两条打印来源，改为全量历史源码快照后原 40 条均可证；② 精简 rows 后缺少装配器无条件要求的清理夹具，补回原夹具但保持真实清理额度 0。它们都发生在 A 的离线 stub 前置，未开真实 COM 或采集。
