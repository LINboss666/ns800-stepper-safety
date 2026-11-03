# R7F1A A 签收与 b424 一次启动验证

审核日期：2026-11-03。
审核头：3e6a02ce62725fc99c6b645d44204dd9c3921c85。
技术裁定：R7F1A_C_AND_BUILD_REVIEW = GO_FOR_ONE_BOUNDED_STARTUP_TRIAL。
这不是现场执行批准；必须得到操作者对本附件整包的一次明确批准后才能执行。当前新增硬件授权为 0。

## 本轮已经签收，不再开离线返工轮

main 栈及 menuconfig 配置均为 4096，四条提前失败路径补记本次 used，实际改动与 A 最小补丁一致。新断言在真实产品函数路径中验证等锁 20 tick；运行时的其他提前失败耗时按真实 tick 记录，不把夹具中的 0 硬套为现场必须 0。

A 独立复跑 tmc_boot(12) 和 read_obs(30)，两变体合计 84/84，rc=0。归档四件产物、五项输入声明的 blob/磁盘字节重建、构建日志及 main 配置，合计 16 项身份核对全通过。该核对没有替代本地 Keil 构建，也不声称 A 直接见证了编译器读盘。

调用图以 main_thread_entry 为根：2076 B；配置 4096 B，已知静态深度相对预算的差值为 2020 B。它不覆盖所有间接调用和上下文保存，不能当成实测最坏余量。堆中 main 栈多占 2048 B，需现场读真实堆统计。

本轮 BIN：292684 B，SHA256：
b4243886818b6b2cf2e30d10864272225e1b0e66c8435c8a50bb449053e153bf
AXF：1775920 B，SHA256：
ea4f8dd1f337796fe824cc262b8fc27f13920a7c260d1a27737b065369124d16
MAP：2114770 B，SHA256：
757701a509e659fcc00df55309920455badb15434e34867a3bc9f0ec34100f53
执行器保持 R6C 原件：219491 B，
33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca。

板上最后已证镜像仍为 5ae4110d…，本次 A 没有设备操作。

## 一次现场整包，只有四个非零额度

| 动作 | 总上限 |
|---|---:|
| download（本候选 AXF，sector） | 1 |
| readback（292684 B，逐字节比较本 BIN） | 1 |
| explicit_reset（sysresetreq） | 1 |
| port_open（COM5，一个会话） | 1 |

apply、arm/start、学习、零点、提交、保存、清日志、物理碰撞、重试、额外复位均为 0；旧批准和剩余额度不结转。只读查询在这同一个串口会话内进行，不新增开口。

固定执行顺序：
1. S0 核对计划/执行器/镜像与清单、来源。
2. S1 一次下载上述 AXF。
3. S2 一次定长 292684 B 回读，与上述 BIN 逐字节相等。
4. S3 先打开 COM5，再执行一次显式复位；被动抓启动流 75 秒。
5. 同会话依次 ps、free、system_status、safety_status、motor_status，随后关闭端口。

ps/free 放在 READY 判据之前，使启动自检仍失败时也有机会留下栈分配及堆快照；如果第一条就无响应或出现字节/危险文本错误，按现有执行器立即停止，不为了补数据多开口。

ps 对 main 行正面要求 stack size=0x00001000。free 保存 total/used/maximum/available 实数，不编造空闲堆通过阈值。system_status 仍要求 bootstrap-complete=YES/abort-stage=0、READY/fault=0、未武装静止、请求 LOW 与 ENN 禁能侧反馈。读到 fault=8 等真实不符即 STOP；新 reason/tx/rx/flush/used 从启动流判读，不重跑碰运气。

现有装配、供电及接线确认沿用，不要求再填一遍；若现场有实际变化或异常，由操作者停止并报告。本包不要求先断电或 fault_reset。

## 横幅与打印怎么判

横幅出现证明 main 线程已经运行到打印处。它不是确认“堆还有多少”的唯一证据，不能证明实测栈大小或最坏栈余量。应结合本轮镜像身份、ps 的 main 栈大小及使用统计、free 的当前及历史峰值快照判读。

如果正常进入 READY，FAULT_LATCHED/故障收口打印本来就不会出现：对应故障分支仍只能登记主机验证，不能算现场通过，也不为了看打印制造故障。

命令响应沿用严格危险文本/NUL/UTF-8 门。被动启动流目前仍只自动过危险文本门，因此执行后须按原始 boot.rx.bin 复核 NUL、UTF-8 与本轮可达短打印是否完整；dispatch=OK 本身不等于所有启动打印已验收。未改的 MOT 注释行 126 B 含换行，超过 120 B 目标但不截断，不应为它误报 NUL。

## 本附件已把执行输入做完

把附件 reports/ 目录按原路径复制进本地 3e6a02c 仓库，只新增下面三个文件，不覆盖历史原件：
- reports/reviews/phase8-m8-r7f1a-minimal-c-patch/field/plan_startup_b424_reviewed.json
- 同目录 rows_startup_b424.json
- 同目录 manifest_binding_b424.json

计划固定字节：23218 B，
SHA256 fa3331862d498bf7aa17966558c535429c4b6696653cb52ea67a3106c0a43d13。

不要把“已批准”写进 JSON 改掉这些字节。用户批准存到新身份的仓外持久账本；建议本轮批准 ID 为 M8R7F2-startup-b424-20261103，沿用现有 D:/ns800_field_approvals 根及已有私有 UID 文件。真实入口保持 transport=real/backend=real/profile=response_driven，整包一次，绑定本计划；不得直接调用单会话绕过账本。

候选的原始 build_manifest.json 是平铺 images，本来不符合旧 S0 只读取 targets[].images 的形状。本附件的 manifest_binding_b424.json 只是已核实字段的入口适配，写明原始 manifest 的路径/字节数/SHA；未更改原始构建清单、未伪造新构建，也不需要改执行器。S0 已用这个固定适配件跑通。

rows 中 ps/free 数字是显式的离线夹具；system_status 正控来自历史真实 READY 字节。它们不是真实 b424 启动记录，不构成现场结果。

离线计划检查：36 项中 35 项 PASS，唯一红项 PLAN_MOTION_CAP_BUDGET 沿用此前 A 的固定无运动包裁定 N/A：caps 空、starts=0、所有运动额度为 0。本范围允许按此固定裁定执行；不改执行器、不填写假运动预算，不把它说成 36/36 全绿。任何未来含 arm/start 的包不能援引此裁定。

最终字节的整包 stub/sim 干跑 rc=0；“main 栈仍 2048”与“Safety fault8”两条负控都按 FIELD_VALUE_MISMATCH 停止。模拟下载/回读/复位计数不是设备动作，A 本次设备动作为 0。

复制后只需按上表核对四个额度，取得一次明确用户批准，再沿用上一场成功的真实入口执行：更新 plan、approval-id 和全新 out，UID 使用原私有文件。无需再编译、无需先提交这三个新增文件再等 A 签一次，也无需新增 Python。现场完成后原始证据与这三个输入可以一并入库。

若失败，原始回执与首错保留，按已有规则关闭端口；不动用“剩下的额度”临时补测，不重跑整包。预算到期或状态不符不放宽判据。

## 可以一次回复的批准原话

“同意按附件 b424 固定计划执行一次启动验证：download 1、292684 B readback 1、explicit_reset 1、COM5 port_open 1；其余动作全 0。”

在收到这句话或等价明确批准之前，只能做离线核对。A 的技术 GO 不是操作者硬件批准。

