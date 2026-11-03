# M8-R7F3 登记交接：A 技术签收 b502 定发包，本机独立复算一致，停在操作者批准门

机器可判定版同目录 `handoff_R7F3_go_registered.json`。
输入 = `NS800_R7F3_A_GO_B502_Startup_Print_20261104.zip`（41287 B，sha256
`1699735a5693d7c758e2f0a1798b3307281c6fd2134b3dfe07f41c24f9c4747a`，10 个内件 + 包内清单逐条自洽）。
**本轮设备动作 = 0**：未下载、未回读、未复位、未开串口、未 apply/save/起转，也未创建批准身份。

## 0 A 的结论（原文口径）

`a_review_R7F3_A_GO_B502_Startup_Print.md:4`：**「技术签收通过，等待操作者对本固定包的一次明确批准。此文件本身不是硬件批准。」**
可用镜像 = `502dca5d…` / 292708 B BIN；A 明确「无需再修改产品 C、执行器或重新构建」，并写明「新镜像尚未上板，真实打印修复是否生效要由本场原始串口字节证明」。

## 1 本机独立复算（不采信 README 的文字）

| A 的声称 | 本机复算 |
|---|---|
| 四件产物与构建清单一致 | bin/axf/map 的 bytes+sha256 **三方一致**（A `IDENTITY.json` / 本轮 `build_manifest.json` / 磁盘实读） |
| AXF 从 0x08000000 起 292708 B 装载段与 BIN 逐字节相同 | ELF 只有一个 PT_LOAD，`p_paddr=0x08000000 p_filesz=292708`，段内字节与 BIN **逐字节相同**（段 sha256 = BIN sha256 = `502dca5d…`） |
| safety_gpio.c == 补丁 postimage | 工作树 blob sha1 `2055c7496be9…`；改前形态 `b242dfad1fed…` 在远端 `tip~2` 上仍在（上一轮已证） |
| main 栈仍 4096 | `rtconfig.h` `#define RT_MAIN_THREAD_STACK_SIZE 4096` + `.config` `CONFIG_RT_MAIN_THREAD_STACK_SIZE=4096`，且两件与上一轮登记的编译输入**逐字节相同**（本轮没再动） |
| 38 项检查 37 PASS / 1 FAIL | 本机在未改动的执行器上重跑：同样 **38 项 / fail=1**，唯一 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`；本机 39 行的**前 38 行与 A 的 `validate_plan.txt` 逐字节相同**（本机多出的是末行汇总） |
| 原执行器 S0..S3 stub/sim 整包通过 | 本机 `--dispatch --transport stub --backend sim`（**不带 `--approval-id`**）：rc=0、4 阶段全跑、`quota_used={download:1,readback:1,explicit_reset:1,port_open:1}`、`actions_attempted=[]`、`APPROVAL_LEDGER|NONE`。`flow_complete=True` 只说明账目一致，`core_protection_verified=False` |

执行器未改：`m8r3_serial_chain.py` 219491 B `33d3f0af…`（与 R6C 原件同值）。

## 2 GF7：A 直接修掉的两处，是我本轮派生件的缺陷（已在未改动执行器上复现）

1. `S0_IMAGE_NOT_IN_MANIFEST`（detail = 新 BIN 路径）——我的 S0 适配件把 `targets[].images` 的键写成了
   `build_manifest` 的字典键 `bin/axf/map/callgraph`，而执行器按**实际归档文件名**查找。
   原文 `offline/dispatch_draft_sim_S0_IMAGE_NOT_IN_MANIFEST.txt`（我的草稿计划整包 STOP 于 S0）。
2. 只把键换成真实文件名后 → `S0_COMMIT_NOT_IN_REPO`（detail = `software_input_commit=None`）——
   我上一轮为"不谎报提交"把四个提交字段直接留空，但 S0 要求它是仓库里存在的提交。
   仓外探针件原文 `offline/dispatch_probe_adapter_only_S0_COMMIT_NOT_IN_REPO.txt`。

A 的修法：新名字的适配件（键 = `rt-thread-motor-m1-candidate.bin` / `.axf` / `.map` / `-callgraph.htm`），
提交字段区分 **交付源码 `99414c7`** 与 **实际构建时 HEAD `70deff8`（当时补丁尚未提交）**，
并明确不声称 `70deff8` 的树含补丁——确切编译字节仍以 `build_manifest` 的 `compiled_from_disk` 为准。
**原始构建清单未被改动**（`059bebe6…` 现算仍在）。我的旧草稿/旧适配件按冻结原则不回改。

## 3 上一轮的缺口由 A 关闭

`GF3`：`safety_irq_status` 之前没有新固件行形预测。A 用实际 C 函数提取件（`a_review_r7f3/irq_source_extract.c`）
生成了 source_shape 行与判据；ENN 说明行在正常已解析输入下 **124 B（含源 LF）**可完整输出，A 也写明这不等于全仓都在 120 B 建议值内。
它仍是 source_shape，不是板级证据。
`GF1`（MinGW 4.9.2 旧 `host_before.c` 需 `-D__USE_MINGW_ANSI_STDIO=1`）与 `GF5`（149 B 正文 + LF = 150 B）A 已接受，本包不要求重跑那份旧量具。
`GF4`（镜像内其余 35 条 >127 B 带标签格式串）继续保持缺陷登记；A 的口径是不为清空全仓清单而推迟这枚候选，下一阶段优先处理该阶段会执行及其故障收口涉及的长打印。

## 4 批准后的固定范围（A 定稿，不再改）

批准身份：`M8R7F3-b502-startup-print-20261104`

| 动作 | 上限 |
|---|---:|
| 下载本包指定 AXF（`fc567635…`） | 1 |
| 0x08000000 定长回读 292708 B 并与指定 BIN 逐字节比较 | 1 |
| 显式复位 | 1 |
| COM5 开口，一个连续会话 | 1 |
| `tmc_config_apply` / 其他写入 / arm / start / 运动 / 学习 / 零点 / 保存 / 开 IRQ / 极性确认 / 清日志 / 重试 / 额外复位 | 0 |

会话顺序：开口后复位一次 → 完整被动采集 75 s → `ps` → `free` → `system_status` → `safety_status` → `motor_status` → `safety_irq_status`；
失败即停、保留首错并关闭端口，不为补采后续命令再开口。
执行前由**现场人员**核对 VM 当前供电仍在、装配与接线未发生未说明的变化（沿用已有确认，不再等 A 盖另一轮章）。

必须取得：新镜像逐字节在板、启动 READY/fault=0、main 栈 4096、实际堆统计、`safety_status` 的 ENN 两条完整短行、
`motor_status` 静止/未武装/当前禁能合同、`safety_irq_status` 两条 LIMIT 短行与默认关闭状态。
本场允许 READY 且 `protect_ready=NO`，这不构成可运动结论。

⚠ 被动启动流在执行器里**只有危险文本门**，所以现场结束后必须独立检查其裸 NUL、严格 UTF-8 与完整行尾——
不能凭 `DISPATCH_SUMMARY OK` 宣布所有打印验收通过（这正是上一轮抓到缺陷的路径）。

## 5 停点

A 已规定：**批准后**我直接把包内三份 field 件（已按相对路径落位，逐字节 = zip 内字节 = `IDENTITY.json`）
+ 这次新批准，在既有持久账登记并 dispatch 一次；不需要再交一轮准备件，也不重复向操作者确认同一范围。
若包中字节或范围要改，就不再是本次批准对象。

现在等待的是操作者本人对这枚固定包的一次明确批准。现场安全门不由我代答、转述或自填。
