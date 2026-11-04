# M8-R7F4 登记交接：A 签收 R7F3 并定稿 CONFIGURED 续测包；本机离线复算一致，停在批准门

机器可判定版 `handoff_R7F4_registered.json`。输入 = `NS800_R7F4_A_GO_B502_CONFIGURED_20261104.zip`
（36146 B，sha256 `337cb3058ee27e8de96299ceffe672eca0a293f759b87f554c94ac27b83a0b6a`，8 内件 + 包内清单自洽）。
**本轮设备动作 = 0**：未开口、未 apply、未复位、未下载、未回读；也未创建批准身份。

## 0 A 的结论

`a_review_R7F4_A_GO_CONFIGURED.md:5-14`：**R7F3 的启动与指定短打印验收通过，可以收口**；
并写明"不代表全部 35 条长格式串已修复，也不代表 SG/ADC/IMU、带载最坏栈或正常 disarm 已过现场验证"。
下一步定为：**先做一次 CONFIGURED 验收**（一次 `tmc_config_apply CONFIRM`），沿用已逐字节证在板的 b502，
**不重新编译、不下载、不回读、不复位**。

## 1 本机独立复算（在未改动的执行器 `33d3f0af…` 上）

| A 的声称 | 本机复算 |
|---|---|
| 新计划过原执行器 S0 绑定 | `--validate-plan` = **33 checks / fail=1**，唯一 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`；`PLAN_BOOT_WINDOW_COVER` 按 A 的"本场无启动不申报 75 s 窗"返回 **NA**（不是 PASS 也不是红）。原文 `offline/validate_plan_r7f4_local.txt`，与包内 `a_review/validate_plan.txt` 同源 |
| 一次完整 stub/sim 正例通过 | `--dispatch --transport stub --backend sim`（不带 `--approval-id`）：rc=0、`stages_executed=2`、`QUOTA_AUDIT port_open declared=1 used=1`、`tmc_config_apply declared=1 used=1`、`APPROVAL_LEDGER NONE`。原文 `offline/dispatch_r7f4_stub_sim.txt`
（A 侧对应件是包内 `a_review/positive_full_dispatch.txt`）。 |
| **四种前置失败 apply 消耗 0；进入 apply 的反例只消耗一次、不重试** | 我另跑 6 个负例（仓外改夹具副本，不动计划/执行器）：`pre_fault` / `pre_not_idle` / `pre_already_configured` → `STOP / FIELD_VALUE_MISMATCH` 且 **apply_used=0**；`apply_refused` → `BUSINESS_FAILED`、`apply_nul` → `NUL_BYTE_IN_RESPONSE`、`post_not_configured` → `FIELD_VALUE_MISMATCH`，三者 **apply_used 恰为 1**（不重试）。6/6 与 A 登记的码一致，`offline/negative_repro_r7f4.txt` |
| CONFIGURED 八条按最宽数字展开 ≤120 B | A 预测行（已代入具体值）实测最长 **63 B 含 LF**；全 `[TMC-CFG]` 家族 41 条格式串原始最长 117 B。**登记我自己的一次算错**：我先用的"最宽展开"粗上界把每个 `%u/%d` 一次换成 6 个 8 位十六进制词，得到 407 B 之类的数——那不构成任何界限，正确口径是上面这组已代入值的预测行 |
| 包身份 | `plan 0b6e1eb8…`(33012 B) / `rows 64e8a868…`(60116 B) / `executor 33d3f0af…` / `bin 502dca5d…`(292708 B) 四项 A=磁盘=IDENTITY 三方一致；两份 field 件 zip 内字节 == 落位字节；a_review 5 件与 README 逐字节相同（`offline/package_integrity_r7f4.txt`） |

## 2 固定范围（批准后，A 已定稿）

批准身份 `M8R7F4-b502-configured-20261104`。**COM5 开口 1 次（一个连续会话）+ `tmc_config_apply CONFIRM` 1 次**；
下载 / 回读 / 复位 / arm / start / 运动 / 学习 / 零点 / 提交 / 保存 / 清日志 / 开 IRQ / 极性确认 / 重试 / 其他写入全 **0**；会话硬截止 90 s，本场无启动窗。
一次 apply 是一次命令事务（内部 GCONF / CHOPCONF / IHOLD_IRUN / TPOWERDOWN 各写并逐次 IFCNT 确认），**不把内部四寄存器记成四次 apply**。
会话顺序：`system_status` → `motor_status` → **apply 一次** → `system_status` → `motor_status` → `safety_status`；
预期写后 `arm_gate_mask` 由 `0x18` 降到 `0x8`（保护未就绪那一条拒绝**保留**），`CONFIGURED` 与 `protect_ready=NO` 可同时成立。
前置任一不满足就**不得发 apply**（只消耗已尝试的开口额度）；apply 一旦发出，无论成功/失败/响应不完整都**不再第二次下发**。
计划参数（来自镜像里的 `tmc_cfg_planned`）：irun 20 / ihold 8 / ihold_delay 4 / tpowerdown 8 / vsense 1 / mstep_reg_select 0 / mres 0，
`IHOLD_IRUN=0x00041408`、`TPOWERDOWN=0x00000008`；电流仍是理论值，本场不测电流或转矩。

## 3 我这一侧的两个诚实注记

1. 落位时我先把包内 `SHA256SUMS.txt` 自己那行标成了"不符"——它按规则**不哈希自身**，已在档中正名为 `SKIP(清单不哈希自身字节)`，其余 7 条为 OK。
2. A 在 README 里点名提醒"不要再漏传 `--profile response_driven`"——这是对上一轮我那三次工具侧失败的直接反馈，已记在案；本轮真实执行若要跑，命令形如
   `--dispatch --transport real --backend real --profile response_driven --plan <本包计划> --approval-id M8R7F4-b502-configured-20261104 --approval-root D:/ns800_field_approvals --approval-token=--expected-token`，
   **本场不调用 DAP，也不需要读 UID 文件**。
3. 我第一版 JSON 把负例一致数写成了 `10`（场景只写 6，两者对不上）：根因是计数用了裸子串 `MATCH`，被 `FIELD_VALUE_MISMATCH` 里的 `MATCH` 吃掉 4 次。
   现改为只数行尾 ` MATCH`，得 **6/6**，与转录里的 `负例复跑: 场景=6 与 A 登记一致=6 不符=0` 一致；并加了一条自检：若子串计数不再大于行尾计数（说明码名已变、该判据不再被检验）就当场报错。
   **只改我自己 JSON 里的计数口径，负例复跑结果与 `offline/negative_repro_r7f4.txt` 字节未变**（`5e6d2861…`，979 B）。

## 4 停点

A 原文：「本包当前硬件批准数为 0」。所以我不创建批准身份、不开口、不发 apply。
等您本人对 §2 这一枚固定包明确批准；批准后按 A 的规定直接落位件 + 新台账 + dispatch 一次，不再交准备轮、不重复确认同一范围。
包内计划或动作范围若改动，就不再是这一次批准对象。
