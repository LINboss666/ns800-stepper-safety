# R7F5：一次无分析仪的静态 IRQ 试验

日期：2026-11-04。审查基线：0cd64587739cca41a22903e075b6fe37b17c765b。A 只做了离线准备，设备动作 0。

本包承接操作者原话“我们盲测一次 如果失败再考虑逻辑分析仪测试”，把现场范围固定为一次静态 IRQ 就绪试验。软件配置成功后开门、观察、读取状态并关门；全程不 arm、不 start、不发 STEP、不做学习或人为触发。它不替代后续保护实际命中与恢复试验。

## 必须如实保留的例外

操作者已经明确 DIAG 未实测。现有 `safety_polarity_confirm CONFIRM` 不进行测量，只置 RAM 标志；原固件仍会打印“operator must have MEASURED”和“MARKED VALIDATED”。本包把这条命令明确登记为**本次无使能试验的临时软件标志置位**，是原实测前置条件的一次有限试验例外，不是实测声明。不得把打印出的 CONFIRMED、protect_ready=YES 或 ALL-PASS 写成物理极性/完整保护已证。

固定记录：`polarity_measured=false`、`trial_only_software_flag_override=true`、`core_protection_verified=false`。这次例外绝不延伸到 arm、运动、带载或保护通道验收。成功或失败都在端口关闭后切断 VM 和 MCU 供电，清掉 RAM 标志，不让它作为下一轮运动的前置证据。

## 身份与原件

- 已经在板逐字节证过的 b502：292708 B，SHA256 `502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a`。不重编译、不下载、不回读。
- 沿用原执行器 `reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py`，SHA256 `33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca`；未改执行器、固件或保护门实现。
- 审查基线相对 R7F4 的 0d8239f 没有产品源码变化。把包内两份 field JSON 按相对路径放入仓库即可；本包不要求为了准备再提交一轮。
- rows 明确区分 R7F3/R7F4 真实旧回包与 source_shape 预测。预测与 host 结果都不是本轮现场证据。

## 本轮动作上限

| 动作 | 上限 |
| --- | ---: |
| COM5 开口，一个连续会话 | 1 |
| tmc_config_apply CONFIRM | 1 |
| safety_polarity_confirm CONFIRM，限上述试验置位 | 1 |
| safety_irq_enable | 1 |
| safety_irq_disable | 1 |
| 下载、回读、显式复位、arm、start、运动、学习、零点、保存、清日志、fault_reset、注入信号、重试 | 0 |

原执行器把极性确认和 IRQ enable 合计记在 `irq_polarity_prep=2`；disable 单列 `safety_irq_disable=1`，步骤显式申报 quota。只读查询、有限被动观察不记成 apply 重试。cleanup 三项仍为 0：本包没有 arm/start 撤能义务，不暗中追加电机命令。

另有操作者现场手动供电动作：**一对初次上电/结束断电，上限 1**。这是执行器无法执行或证明的人工作业，必须单独登记，不能冒称设备动作全由串口账本覆盖。

## 开跑前与结束

起点按现有事实：MCU 与 VM 均断电、DIAG 未测。接线/机械装配沿用既有确认，不另改线。

1. 操作者先使 VM 就绪，再给 MCU 供电一次，确保启动 IOIN 读取前 VM 已经有效。等待本次 bootstrap 完成，然后执行本包。不要为等到 READY 另加复位或重试；启动失败即本轮结束。
2. 本场不调用 DAP，不需要读取 UID 文件。原工具的下载/回读/reset 元数据只为兼容离线校验保留，相应阶段不存在、额度都是 0。
3. 全部正常或出现第一个异常时，由原执行器关闭端口；操作者随后切断 VM 与 MCU 供电并记录观察。DAP 若仍供 MCU，单断 VM 不算 MCU 已掉电。若输出异常、程序失去响应或到外部 90 s 截止，操作者直接切断两路供电，不等待串口恢复。
4. 不改旗标记录来消除红项，不再开口补采，不调整参数后重跑。失败后的下一步才是针对首错安排分析仪采集。

## 固定的 16 步

| 顺序 | 命令/动作 | 必须取得 |
| --- | --- | --- |
| 1 | system_status | READY/fault0、UNCONFIGURED、IRQ CLOSED、未确认、protect NO、禁能反馈一致 |
| 2 | motor_status | IDLE/armed0/cur=tgt0，mask 0x18 |
| 3 | tmc_config_apply CONFIRM | 八条 CONFIGURED 完整短行、参数 20/8/4/8/1 |
| 4–5 | system_status / motor_status | CONFIGURED、仍禁能，mask 0x8 |
| 6 | safety_irq_status | DIAG pin3/raw0/safe0/RISING、IRQ CLOSED、软件标志未置位 |
| 7 | safety_polarity_confirm CONFIRM | 源码的 12 条完整打印；只登记试验置位，未测事实保留 |
| 8 | safety_irq_enable | 1 路活动输入注册；完整两条打印 |
| 9 | safety_irq_status | IRQ OPEN、软件 CONFIRMED、软件 protect YES、DIAG raw0、ENN HIGH |
| 10 | 原执行器 __watch__，请求 3 s | 有限被动观察，无物理刺激；危险文本/字节缺陷即停 |
| 11–12 | system_status / motor_status | READY/fault0、软件门 0/ALL-PASS，仍 armed0/频率0/请求LOW/ENN HIGH |
| 13 | safety_irq_disable | PA.3 detach rc0、IRQ gate CLOSED 两条完整打印 |
| 14–16 | safety_irq_status / system_status / safety_status | IRQ CLOSED、protect NO、READY/fault0、禁能一致；软件确认标志此时仍为真，随后掉电清除 |

门全绿不授权发 arm/start。闭门后的 0x8 REFUSED 状态披露是预期，只有指定整行放行；真正动作拒绝、故障、缺行/重复字段、裸 NUL、非法 UTF-8、反馈矛盾均停止。

申报总时限参数为 90 s，工具逐步超时和 slack 原样保留。该参数在步骤边界检查，不宣称为任意时刻 90 s 强制断电器；外部操作者的 90 s 断电截止另行负责。被动观察的实际时长按台账报告，不把请求的 3 s 冒称仪器实测值。

## 离线验证已经完成

- S0 绑定通过。原执行器 57 项结构检查：56 PASS，唯一 FAIL 是既定 `PLAN_MOTION_CAP_BUDGET` 的非空 cap 要求。本固定零 arm/start/learn 包按 N/A 登记，不填假运动预算，不写成全绿。
- 原执行器完整 stub/sim dispatch：正常场景 1 个 + 反例 7 个。故障前置不发 apply；DIAG 已活动不置标志/开 IRQ；enable 拒绝、NUL、开门后未就绪、输出变 HIGH、detach 非零都停止。未发送 arm/start，没有业务重试。
- 从生产 `safety_thread.c` 抽取原函数，用明确标注的 host GPIO/IRQ 桩编译，六组输出逐行等于 rows。新调用的最大长度分别为：置位 124 B、enable 114 B、disable 74 B（含 LF），没有 NUL。host 只证明形状，不证明中断交付或板端时序。
- `supervisor.c` 的成功极性字样是大写 `CONFIRMED`；固定判据已按源码核清。

准备阶段自纠如实登记：初版按固定下标切输入行误含命令回显；误把 real 的 response_driven 用作 stub profile；复用旧测试目录被 LEDGER_PREEXISTING 拒绝；初版预测把 supervisor 的 CONFIRMED 写成小写。均发生于仓外离线准备，已经修正并重新回放；不涉及现场字节或设备动作。未修改执行器来消除这些拒绝。

## 现场执行入口

使用新的批准身份 `M8R7F5-b502-blind-irq-static-20261104`，旧批准不结转。按原有 `--approval-init` 机制为本计划身份开新账；令牌采用本次用户真实原话，并在交接中附本固定范围。不要填“已测极性”作为令牌或前置事实。

真实入口仍必须同时有：`--dispatch --transport real --backend real --profile response_driven`，`--plan` 指向本包固定计划，以及本次 `--approval-id`、`--approval-root` 和匹配的 `--approval-token`/`--expected-token`。不需要新包装器；包内 checks 仅是 A 的离线复核材料。

原始 RX/TX 字节、两级台账、实际供断电记录、首错和关闭端口事件一并入档。真正成功的结论只写 **STATIC_IRQ_READINESS_TRIAL_PASS**；无论结果，DIAG 两态、实际故障触发、SG/ADC/IMU、arm/disarm、运动和带载全部仍属未验证。
