# R7F7：新版上板＋真实异步作业创建与失败收口

日期：2026-11-05。本包替代未下发的“只查询、只拒绝受理”草稿。**必须实际创建一个学习作业；只看到 IDLE 或 learn REFUSED 不能算本轮通过。**

本轮实际验证的是：新镜像在板 → 静止零点采集 → learn STARTED → 后台因静止条件不符合巡航窗而 FAILED/WINDOW_BROKEN → 采集器释放、learn 凭据仍无效、驱动仍禁能。电机保持不转。

正常巡航下学出 DONE、活动作业取消、commit/保存、SG/ADC/IMU 真实保护触发不在本轮证据范围，不能把本轮通过说成整个学习功能已经上板验完。

## 1. 固定镜像与范围

- 被签收源码：`00718b4`；当前交接基线：`7a08f9d`。A 已核对两者产品与主机源码无差异。
- BIN：297452 B，SHA256 `23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963`。
- AXF：1790048 B，SHA256 `0f421595ac940863c4d38a2791f31bc8811fc3b8fa40062782f2ac7505c317f6`。
- 只用 `reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/` 这套已签收成品；不改源码、不重新构建、不换其他候选或备用工具。
- b502 的旧证明仍作为历史保留。下载一旦发生，不能继续把 b502 当作当前在板身份；只有新镜像定长回读逐字节相等后，才登记新版在板。
- 仪器标识披露按用户本条决定暂缓，不再阻挡本轮；不把它改成“已修复”，不改 Git 历史。本轮不启动分析仪，不产生新的原生捕获文件。

## 2. 新额度

| 动作 | 上限 |
|---|---:|
| 下载指定 AXF | 1 |
| 定长回读 297452 B 并比较 | 1 |
| COM5 开口／dispatch | 各 1 |
| 开口后的显式复位 | 1 |
| `diag_profile zero 32` | 1 |
| `diag_profile learn 8 450 550 0` | 1 次请求，必须受理为 1 个真实作业 |
| arm/start、运动、驱动 apply、IRQ 开/关、极性确认 | 0 |
| learn_cancel、tier、commit、clear、config_save、清日志、故障复位、重试 | 0 |
| 分析仪 Start、信号注入、拆探头作对照 | 0 |

新批准身份：`M8R7F7-2369-async-job-static-20261105`。旧 R7F6 额度不结转，不写旧账。批准依据为操作者“开启下一轮”的真实指令及本条“必须创建作业”的范围更正；`approval_token.txt` 保留开启下一轮原话的 UTF-8 字节，不拼接其他句子。现场 agent 已收到本包具体执行指示时不再二次索要同意、不另开准备提交轮。

**预算按尝试记账**：zero 和 learn 各一次；learn 未受理即停止，不能用剩余时间再发一次。pyOCD 各外部调用至多一次；工具内部正常连接/复位行为如实记录，不冒称物理零内部动作。

## 3. 供电与必要前置

最新交接登记当前仍通电，并转达操作者希望今后默认不断电。本轮采纳：**已上电就保持供电，正常收口保持禁能供电，不再要求额外断电或报告收口断电时刻。** 旧轮次的实际动作与旧判据不回改。

若现场状态已改变为断电，操作者可为本轮建立供电一次：VM 稳定后再给 MCU 供电；不反复断上电试错。当前是否通电依实际现场报告，不由 USB 枚举推断 MCU/VM 状态。

必要的前置只包括：操作者在场、电机当前无运动、未另发控制命令、COM5 未被其他程序占用、所需文件字节正确。不开设备“试探”参数。

使用此前成功的私有 DAP UID 文件。调用设备工具前，在本机将其规范化内容与当前 OS 已观察到的 DAP 标识作对应核对；只记录成功/失败及脱敏信息，不输出或入库 UID，不凭旧叙述中的某个摘要猜设备，也不调用 pyOCD 枚举来猜。对应不上或私有文件缺失，就在工具调用前停止报告。

如发生异常使能或运动，操作者硬件禁能优先；正常保持供电的口径不能阻止必要的紧急处置。本包不授权额外软件 stop/disarm 或自动复测。

## 4. 输入落位与唯一一次 dispatch

先在仓外核对整包 SHA256SUMS，再按原相对目录放到：

```text
reports/reviews/phase8-m8-r7f7-newimage-static/
```

使用仓库原执行器 `reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py`，SHA256 仍为 `33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca`；`signed_inputs/` 是其见证副本，不是新执行器。

`field/manifest_binding_r7f7_2369.json` 是从实际新构建 manifest 派生的 S0 兼容绑定，不是新构建或现场证明。`frozen_newimage_source/` 从已签收 `00718b4` 逐字节取出，只供打印来源核对，不回灌产品目录。旧同步 learn 两条 needle 已由新异步接口来源替代，其余共 41 条在新源码上核对。

先离线 `--approval-init` 开新账，spent=0、pending=0；再只执行一次：

```text
--dispatch --transport real --backend real --profile response_driven
```

同时带相同 plan、repo-root、approval-id、持久化仓外 approval-root、唯一仓外 out、私有 uid-file，以及从本包实际指令取得且原样相同的 approval-token / expected-token。plan 为 `field/plan_r7f7_2369_static.json`。不要沿用离线 `healthy` 档进入真实路径。

S0 绑定 → S1 下载 → S2 回读 → S3 开口并复位，依赖由计划固定。S2 要求与指定 BIN 逐字节相等；只 hash 相等、只工具 rc=0、或回读了任意窗口都不替代它。下载/回读失败不进入启动阶段，不自动回烧 b502。若编程尝试后身份未被证实，当前 Flash 状态记未知，不声称旧镜像仍在。

## 5. S3 的固定命令链

开口后显式复位一次，**被动启动窗走满 75 s**，不按静默提前结束。整个会话上限 210 s，区别于未改动的固件 12 s 作业 deadline。随后顺序固定：

| 顺序 | 命令/观察 | 必须得到的事实 |
|---|---|---|
| 1–6 | ps、free、system_status、safety_status、motor_status、safety_irq_status | 新版启动后主/诊断/shell 栈信息；READY/fault=0；静止未武装、请求禁能、ENN HIGH；IRQ CLOSED、polarity not-confirmed、protect_ready NO；drive_cfg UNCONFIGURED、arm gate 0x18 |
| 7 | diag_profile learn_status | job id=0、IDLE、owner=FREE、zero/learn=0/0；上下文已真实初始化 |
| 8 | diag_profile zero 32 | 静止采到 32 个不同有效 ADC 帧，输出 zero OK；只更新候选零点，不保存配置 |
| 9 | diag_profile learn_status | 仍为 id=0、IDLE、owner=FREE，但凭据为 1/0 |
| 10 | diag_profile learn 8 450 550 0 | **真实 STARTED id=1**，参数与计划一致；不能把 REFUSED 或 IDLE 当通过 |
| 11 | 被动观察 1 s | 不发新命令，让 Diagnosis Thread 推进；源码周期 10 ms，学习窗仍为 450..550，电机仍静止 |
| 12 | diag_profile learn_status | id=1、FAILED、why=WINDOW_BROKEN、owner=FREE、zero/learn=1/0；不能 DONE，不能仍占采集器 |
| 13–14 | system_status、motor_status | 仍 READY/fault=0、未武装、频率 0、请求禁能、ENN HIGH、IRQ CLOSED、保护未就绪 |

表中观察窗不是串口命令，所以实际发送 **13 条命令**，另有 1 个 reset 动作和 1 s 被动观察。plan 已列出每个步骤与完整判据。

为什么静止也能真实 STARTED：当前代码在零点与模式合格后，`rel_job_start()` 创建任务；巡航窗复核发生在后台新帧的 `rel_cap_feed()`。本场有意保留 IDLE，后台应明确失败并释放资源。这是在板上验 **真实作业的受理与失败生命周期**，不是伪造巡航，不是有效运行基线学习。

`job_failed` 那条只读查询中，`st=FAILED why=WINDOW_BROKEN` 是本轮正面要求的终态披露，按整行、id/owner/凭据全部锁定的白名单处理。其他动作的 FAILED/REFUSED、其他 why、非零 owner、意外 DONE 仍是首错 STOP；不把失败词全局放行。

## 6. 字节证据与验收边界

原始启动流、13 份响应、1 s 观察流、S0 事实、回读文件、各阶段账本与工具输出全部先保存。命令响应的裸 NUL、非法 UTF-8、危险文本、状态不符按原执行器当场停止，不补测。对被动启动流，未改执行器只执行原有危险文本门，完整字节验收由现场后独立判读承担；发现问题则本场验收不通过，不能因整包 status=OK 就跳过或补测，也不临时改执行器来冒充已有实时门。

启动原始字节独立核对：TMC IOIN 成功、SELF_TEST → READY、required PASS/degraded 0、state READY/fault_code 0、bootstrap complete；无 FAULT_LATCHED 或 REQ FAIL；严格 UTF-8、裸 NUL=0。行宽分别登记正文、行尾和总字节，不能用终端视觉换行证明未截断。

ps 要正面读到 main 4096，也记录 diag/tshell 4096 与实际高水位，不能据此声称带载最坏栈已验。free 记录新四项并核对可解析/非负、maximum≥used、total=used+available；**不沿用 b502 的旧四项精确数值或编造新阈值**。

新镜像在板依靠本场回读证明，不靠串口自报。STARTED 证明受理；随后 id=1 的 FAILED/WINDOW_BROKEN 与 owner=0、learn=0 证明本场预期的终态/资源收口。若作业仍 RUNNING、出现别的失败原因或异常 DONE，按首错停住，不能改判据接纳，也不能重发 learn/cancel 补场。

通过时结论只能写：**新镜像逐字节在板、启动静态状态通过、静止 zero 通过、异步作业真实受理及越窗失败收口通过。**

仍未证：正常巡航下采样进度与 DONE 有效基线、活动取消/取消竞争、commit/config_save、真实保护触发/恢复、实际 STEP/ENN 动态时延、带载最坏栈。因此 `polarity_measured=false`、`core_protection_verified=false` 保持，不作真实有效学习完成声明。

## 7. 收口与交付

正常结束关 COM，保持 VM/MCU 供电，保持未武装、禁能、IRQ CLOSED。候选零点与失败作业记录可以留在 RAM；不得为清理它们额外复位、断电、clear 或保存。下轮从实际状态重新定范围，不假定它们已消失。

首错保存原始证据并停止。未执行阶段写 NOT_RUN；如果尚未读到最终状态，写状态未确认，不能拿中间 READY 代替。只改新轮次 reports/ 台账与交接，不回写旧证据和旧账。推送与远端回读按原惯例，最终回读允许留仓外避免自引用循环。

## 8. A 离线核对

A 实际设备动作 0。`proof/host_probe.c` 直接编入真实产品 C，实际跑出：IDLE 0/0 → zero OK → IDLE 1/0 → STARTED id=1 → FAILED/WINDOW_BROKEN、owner FREE、learn=0；这只是 host 模型，现场必须重新取证。

未改执行器的整包 stub+sim 已跑通 S0–S3；`--validate-plan` 57 项，唯一原始 FAIL 为既有空运动包 `PLAN_MOTION_CAP_BUDGET`，本零运动范围裁为 N/A，原 FAIL 保留，不改执行器或填假运动额度。所有离线结果均不冒充板端结果。
