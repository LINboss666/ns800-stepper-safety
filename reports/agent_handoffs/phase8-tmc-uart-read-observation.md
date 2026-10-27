# 交接 — phase8-tmc-uart-read-observation

- 任务书：`NS800_TMC_UART_Observation_and_Deadline_Fix_Task_a691eb5_20261027.md`（外部审核方撰写，用户附件转达）
- document_base / source_base：`a691eb5c4b5a3e5d639ac9e231ce0920967798de`
- 产品 code_commit：`ab76a07b5d48dbd8c7ee9ff6dea0c9bf90fcc5d4`（两枚镜像的输入字节）
- 验证链提交：`fb1a72e710c07ecf07aa432e0a3c74fa13c116d6`（只改 `tests/host/inject.py`，非镜像输入）
- 本轮阶段：**离线交付完成；设备动作 0**

## 一句话结论

两项产品修改一次落地并可在字节上核对：RX 阶段期限锚点修正（不再把 flush/TX 耗时二次计费），
以及每笔读事务的阶段/原始码/预算时刻/真实计数/有界字节证据接入 `tmc_uart_probe`。
主机侧 24 条 `read_obs` 用例在 **target 与 host_ovr 两个变体都真实执行且全 PASS**，
两项具名反向各自命中预登记失败身份；基线 a691eb5 字节上的同一入口对照复现了缺陷
（110 ms 到达的合法应答在基线被 `-117` 拒掉，修后被接受并发布真实寄存器值）。
两目标真实 Rebuild All 均 0 Error / 0 Warning，新镜像按修正后的 23:50 构建重新归档。
新采集脚本 `UARTReadDiagSerial.ps1` 的传输层与冻结 M0 件逐字节相同，Windows PowerShell 5.1
**离线自检**通过（退出码 0、stderr 0 B、23 条负控、12 条禁止命令全拦）。
下一次板级包只有草案，状态 `DRAFT / NOT_AUTHORIZED / NOT_EXECUTED`。

## 1. 交付物

| 路径 | 说明 |
|---|---|
| `applications/tmc2209.c` | 期限修正 + `tmc_read_observation_t` + probe 逐笔导出（blob `a480960477f34a1a4cf2b4e206d842297aac445f`，180848 B） |
| `tests/host/leaf_main.c` | `read_obs` 组 24 条用例 + 新注入开关 + 基线对照开关（blob `3c5273b0252646cb693f20b3e3804a014e6d0351`） |
| `tests/host/run_stop_priority_s2.sh` | 新组登记（target/host_ovr 都跑）+ 两个新反向家族 + 期望数 47/103（blob `917870ac63a97e2da27a2e3d743131a1a02f5d18`） |
| `tests/host/inject.py` | **白名单外额外改动**：`probe_writeback` 锚点跟随 probe 标题行的 ASCII 短行口径（原因见 §4） |
| `reports/reviews/phase8-tmc-uart-read-observation/` | `source_audit.txt`、`baseline_and_fixed_checks.txt`、`host_results.txt`、`keil_builds.txt`、`collector_selftest.txt`、`board_candidate.md`、`UARTReadDiagSerial.ps1` |
| `reports/firmware/phase8-tmc-uart-read-observation/` | `default.bin`、`measure-a0.bin`、`build_manifest.json` |
| `reports/agent_handoffs/phase8-motor-m0-uart-diagnosis.json` | 仅 `this_round_stage_reality` 一处状态矛盾更正（见 §5） |

## 2. 改了什么（可核对的形状）

- **期限**：`eff_end = b->start + used + min(stage_cap, total - used)`，`used` 在 RX 阶段入口采样一次；
  总预算耗尽由显式前置分支返回（RX 未进入），阶段过期与总预算过期分列；期限比较全部改为
  回绕安全无符号差值；CRC 之后复核期限，过期后到达的合法帧保留 `crc=OK + late=1 + published=0`。
- **观测**：可选、调用者私有的 `tmc_read_observation_t` 穿过**同一份读 core**；
  既有 `tmc_read_reg` / `tmc_read_reg_locked` 保留原签名、薄封装传 NULL；
  写 / confirmed-write / 配置复核仍走同一读本体，没有 probe 专用协议循环副本。
  probe 栈上三笔（单笔实测 `sizeof=176 B`，三笔 528 B，`tmc_xfer_stat_t` 仍 48 B）。
- **八种可区分身份**：未发请求 / TX 未完成 / RX 零字节 / 仅精确回显 / 其它字节 / 帧不完整 /
  CRC 错 / 完整合法帧被期限拒绝 —— 逐条对应用例见 `source_audit.txt` §5。
- **输出**：三笔结果记完、**归还外层 txn 之后**才打印；打开逻辑收成私有 core 走 quiet 模式；
  每行 ≤96 B 纯 ASCII；`BEGIN/END` 明确，END 含 `reads / writes=0 / export_err / observation_complete`；
  记录完整与业务成功分开。
- **未改**：预算常量一个都没动（`BUDGET_AND_BUFFER_DEFINES_UNCHANGED=IDENTICAL`），
  公开 API、HAL、内核、时钟、GPIO、rtconfig、工程源清单、公共 shim、`RT_CONSOLEBUF_SIZE` 都没动。

## 3. 验证（全部在定稿字节上）

- `read_obs`：target 24/24 PASS、host_ovr 24/24 PASS，两侧具名清单一致，且各自有
  `*.executed.marker`（真执行，不是整族 SKIP）。
- S2 台账：target 组=18 用例=47 PASS=47；host_ovr 组=18 用例=103 PASS=103（旧 23/79 合同另计）。
- 两项具名局部反向：`rev_rx_deadline_old_origin` 命中 URO03/URO04/URO16（其余 21 条仍 PASS）；
  `rev_obs_drop_raw_rx_capture` 命中预登记 URO12/URO17，另有 3 条连带检出（如实登记，不粉饰成只见 2 条）。
- 基线 vs 修后：同一 deadline 探针编到 `a691eb5` 字节（151478 B / sha256 `6a3b7c6d…`，
  `git show` 取到仓外）与最终字节上跑，URO03/URO03b 两列差异见 `baseline_and_fixed_checks.txt`。
- 集中回归（§6.4 九条入口 + REVERSE）：run #3 逐条 rc 见交接 JSON `tests_executed.central_regression`。
- 负例/元负例：注入锚点修正后 N9/N10 定向重跑均按 rc=1 检出；run #3 全量结果见 JSON。

## 4. 额外改动的披露（白名单外）

任务书 §10.1 只允许改三条非 reports 文件。本轮额外改了 `tests/host/inject.py` 一个文件，原因：
probe 标题行改成 ASCII 短行后，旧锚点（中文单行 `rt_kprintf(...)`）在定稿字节上命中 0 次，
`run_negatives` 的 N9/N10/N10b 与 `run_meta_negatives` 的 M1/M1_ctl 全部被记 **INFRA-FAIL(rc=4)**
—— 这是验证链自身失效，不是检出。锚点只改文本落点，注入内容（读 GCONF 再写回）与判据
（FD3/FD7b）不变；定向重跑两条均恢复按 rc=1 检出。没有新建任何 Python/CSV 完整性门。
选择"改锚点"而不是"删用例"，因为删用例会静默降低既有覆盖。

## 5. 对上一轮记录的勘误

- `URO-E1`：首次镜像归档取自打印路径修正**之前**的 23:41 构建（`default.bin` 257004 B /
  `7d7e1615…`，`measure-a0.bin` 267268 B / `3d13c555…`），且 manifest 的 code_commit 误指 `d2dd743`。
  三件已从仓库目录移出并完整保留在仓外（未删除、未覆盖），现按 23:50 构建重归档。
- `URO-E2`：manifest 首版把 A0 目标的"上一轮 Program Size"错取了 M1 **候选目标**的
  `173994/83784`，导致虚报 ZI 增量 7720；按 A0 目标自己的 export-r1 登记
  `179858/79430/2188/91504` 更正后，RW/ZI 增量均为 0。
- `URO-E3`：manifest 首版 map 摘要解析为空、构建横幅提取假定 `date` 后紧跟 `time`；
  已改为按本机 armlink 6.24 实样（逗号分隔、`Image Entry point :`）解析，横幅两种排布都找
  且要求全镜像只有一处时分秒串，歧义时报 `AMBIGUOUS_OR_MISSING` 而不是猜。
- `M0-X1`：a691eb5 交接 JSON 末尾 `this_round_stage_reality` 仍写"CP0 完成，CP1/CP2 未进入"，
  与同文件 `m0_status`、`pending_items` 与 CP1 台账矛盾。按用户明确要求更正为：
  **CP0 完成；CP1 已执行并在 IOIN 读超时处停止；CP2 最终状态未回报。**
  旧实测响应、旧退出码、原始归档与历史报告正文结论一律未改。

## 6. 镜像与构建

| 目标 | Program Size | BIN 字节 / SHA-256（前 12） | 横幅 |
|---|---|---|---|
| `rt-thread` | Code=179082 RO=77686 RW=2188 ZI=83784 | 257348 / `ad5daaa36974` | `Oct 27 2026 23:50:13` |
| `rt-thread-measure-a0` | Code=186426 RO=80646 RW=2188 ZI=91504 | 267652 / `b54387fddbb6` | `Oct 27 2026 23:50:33` |

增量全部落在 `.text/.rodata`，RW/ZI 零增量 —— 与"观测对象是 probe 栈上局部"一致。
AXF 与 map 含构建机路径，本地独立保存并登记长度/SHA，**不入库**。
第三个目标 `rt-thread-motor-m1-candidate` 及其历史 BIN 本轮不改、不构建、不下载。

四宏有效值：两个已构建目标的目标级 Define 都不含这四个名字，且用 ARMCLANG **真预处理**
（该目标 Define 逐项 `-D` + 该目标 IncludePath 逐项 `-I`，预处理真实 `applications/motor.c`）
实证四宏均解析为 `RT_FALSE`（`RT_FALSE=0`）。host_ovr 的 `-D…=RT_TRUE` 只存在于主机夹具，
不是目标宏翻转。

## 7. 采集脚本与离线自检

`UARTReadDiagSerial.ps1` 37063 B / sha256 `2ada6b88a0631ceeff1e9f890466e96e55180031c5831a8b5859c114366d349d`，
纯 ASCII（非 ASCII 字节 0）。传输层 16 个函数/块与冻结 `M0Serial.ps1`（24064 B / `2131af0e…`）
逐字节相同，由生成器写盘后自验。Windows PowerShell 5.1.26100.5074 真实执行 `-SelfTest`：
退出码 0、stdout 一行 PASS、stderr 0 B、`Parser::ParseFile` 错误 0、mock 命令 7 条、
负控 23 条、禁止命令拦截 12 条、台账 58 事件、9 个逐命令 rx 件。
关键判据：同一条 probe 超时切片 `capture=COMPLETE` + `business=FAIL`（reads=1）——
观测完整与通信成功分开。

本轮**未运行联机模式**，因此 `-ApprovalSource` / `-ExpectedBuildBanner` 等在线参数未被使用过。

## 8. 安全边界（本轮实际遵守）

设备动作 0：未创建真实 SerialPort、未打开 COM5、未发目标命令、未枚举设备/端口/探针、
未调用 pyOCD/DAP、未读 Flash、未下载/复位、未改线路/模块/供电/ENN/SW1、未输出 STEP/DIR/HIGH、
未做 arm/start/ramp/fault_reset/selftest、未运行 `sw1_cut.ps1`；Keil 仅批处理构建（`-r … -j0`）。
未 reset/rebase/force/merge，未改写冻结引用；Git 身份沿用仓库 local 公开 noreply，未改全局配置。
推送走代理 `127.0.0.1:7892`（仅按命令加 `-c http.proxy=`）。

## 9. 停止线

```
OFFLINE_DELIVERY_COMPLETE_THIS_ROUND / HARDWARE_ACTIONS=0
OBSERVATION_CAPTURE_PROVEN_ON_HOST / TMC_LINK_ON_BOARD_STILL_UNPROVEN
BOARD_PACKAGE board_candidate.md = DRAFT / NOT_AUTHORIZED / NOT_EXECUTED
FOUR_DEFAULT_AND_A0_SAFETY_MACROS=0(ARMCLANG 预处理实证)
SAFE_FOR_ENABLE=false / M2_M3_NOT_AUTHORIZED
M0 CP2 最终状态仍未回报(允许保持 UNKNOWN, 不为此连接设备)
```

## 10. 下一步（不是授权）

1. 外部审核方对 `source_audit.txt` / `baseline_and_fixed_checks.txt` / `host_results.txt` /
   `keil_builds.txt` / `collector_selftest.txt` / `board_candidate.md` 做独立复审。
2. 若复审通过，可直接按 `board_candidate.md` 给一次整包批准（不需要另开离线采集器准备轮）。
3. 待补事实（不阻塞离线工作）：M0 CP2 的最终状态；真机 `tmc_uart_probe` 的逐笔阶段读数。

## 11. R1 指针与本条勘误（2026-10-28 追加；上文 §1–§10 的原文与 stdout 一律未改写）

独立复审对本 HEAD 的判定是 **`URO_OBSERVATION_AND_CAPTURE_REWORK_REQUIRED`**：
`RX_DEADLINE_FIX_ACCEPTED` 与 `EXISTING_HOST_REGRESSIONS_REPRODUCED` 成立，但**未给 SOFTWARE/IMAGE GO，
也未批准下载本文 §1 那两枚镜像**（`ad5daaa3…` / `b54387fd…`）。返修在下一轮一次做完：

- R1 任务 ID `phase8-tmc-uart-read-observation-r1`，锚点即本 HEAD `6324ac40bd8d0f172fdb89e77917be33dc739460`，
  返修 code_commit `a483dcba74abd8c04549459bcee2cbcdb1ddea4c`。
- R1 交付：`reports/reviews/phase8-tmc-uart-read-observation-r1/`、
  `reports/firmware/phase8-tmc-uart-read-observation-r1/`、
  `reports/agent_handoffs/phase8-tmc-uart-read-observation-r1.md/.json`。
  本文那两枚 BIN、`UARTReadDiagSerial.ps1` 与原始报告**原样保留不覆盖**，也不是 R1 候选件。

本条同时登记三条口径更正（不改写历史原文，只在此并列说明）：

1. **URO-E4（A0 增量基线）**：本文 §keil_builds.txt 第 2 节把 A0 的"上一轮"取为 179858/79430，
   于是报出 `+6568/+1216`。复审指出与旧 `5f30…` 阶段对照应取
   `fe3b64b4:reports/reviews/phase8-target-measurement-a0-export-r1/keil_build.txt` 的真实值
   **Code=181106 RO=79766 RW=2188 ZI=91504** ⇒ 本轮 URO 的 186426/80646 实为 **+5320/+880/0/0**。
   R1 的 `keil_builds.txt` §2 与 manifest 已把"vs 上一轮同目标"与"vs 更早阶段"两套对照分列。
2. **提交数**：URO 轮实际是四个提交 `d2dd743 / ab76a07 / fb1a72e / 6324ac4`；本文头部此前只列后三个，
   漏了最早的 `d2dd743`（不影响产品字节身份：最终 `tmc2209.c` 等于 `ab76a07` 内的 180848 B）。
   R1 起按四条引用，不为此另开登记轮。
3. **主机日志措辞**：`tx_bytes_on_wire` / "线上真实字节" 只表示受控寄存器模型的 DATA 提交计数，
   不是目标线路波形。本文历史 stdout 原文保留不改写；R1 的新表述统一为
   "端口真落 DATA 的字节 / 模型字节数"。另 `tmc_obs_echo_eval` 此前只搜前 32 B，
   "前缀内未见"不等于物理无回声 —— R1 改全流滚动窗口并导出样本内命中偏移。
