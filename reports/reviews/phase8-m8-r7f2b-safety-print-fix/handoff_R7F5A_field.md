# R7F5A 现场执行回执：一次 8 路静态采集 + 原 16 步串口链（两层分报，不拼全过）
执行根 `field/field-20261104T101046Z/`；批准身份 `M8R7F5A-b502-la-two-channel-static-20261104`；令牌取 `capture_scope.json` 的 `operator_authorization_verbatim` 逐字节（82 B，`sha256=f2e062a59afc460e…`），未拼接其他句子。
## 1. 串口层：`SERIAL_16_STEP_STATIC_IRQ_TRIAL_OK`
- `DISPATCH_SUMMARY status=OK`、`stages_executed=2`、16 步全部有响应原文；实耗 `port_open 1 / tmc_config_apply 1 / irq_polarity_prep 2 / safety_irq_disable 1`，其余 18 项 0，无重试。
- 门链按字节复现：`arm gate fail mask` `0x18`(前置)→`0x8`(配置后)→`0x0`(开 IRQ 后)→`0x8`(关 IRQ 后)；`irq gate` CLOSED→OPEN→CLOSED；`polarity` NOT confirmed→operator CONFIRMED；`protection_ready` NO→YES→NO。**这些是软件状态位，不是物理结论。**
- 固件自报（同一场原文）：`TMC_DIAG pin=3 raw=0 safe=0 irq=RISING [safe]`、`PC.6(ENN_FB) pin=ok raw=HIGH = TMC_ENN feedback (HIGH=disabled side)`；全程无 `armed=1`。
- 实际发送的命令由响应首行回显取证，15 条与计划同序（第 10 步是被动观察，无请求），其中**没有** `motor_arm`/`motor_start`；阶段台账本身不写 `cmd` 字段，所以命令身份不靠台账标签。
- 16 份响应零裸 NUL、严格 UTF-8；会话末 `t_s=7.344 s` < 申报 90 s。
- 第 10 步被动观察 `10_quiet_observation.rx.bin` 为 0 字节：只说明窗口内无产出，**不构成「没触发」的证明**（被动零字节不算证据）。
## 2. 仪器层：`CAPTURE_VALID_BUT_NON_DISCRIMINATING`
- 一次采集，`captureId=8`，D0–D7 全开、1 MS/s、手动模式、请求缓冲 512 MB（接口只回 `captureId`，**不回实际接受的缓冲值**，所以按 A 补充第 3 条改由事后覆盖性验证承担）。原生件 `la_capture_id8.sal` 3014 B `sha256=ce6ce2079b8ec03d…`；数字 CSV `la_digital_raw.csv` 196 B `sha256=7ab2321a014e21eb…`。
- 采集窗 2026-11-04T10:10:37.480095 → 2026-11-04T10:11:05.632902，跨 28.0166 s；8 路**全程零跳变**（每路 initial=final，上升 0、下降 0）。
- **决定性一条**：本轮电平模式 `0,1,1,1,1,1,1,1` 与探头未接时的浮空基线**逐列相同**。所以这批字节既与「接线正确且全程静默」一致，也与「探头未接触/无共地」一致——**电平层无法区分这两者**，仪器判据不具区分力。
- 通道映射未由现场读得：`CHANNEL_MAPPING_UNRESOLVED`。按 A 补充第 2 条，不靠「预期一低一高」反推哪一路是 DIAG/ENN；固件自报的 `raw=0`/`raw=HIGH` 只到软件读数层，不能与某一列绑定成「该列即 DIAG」。
- 保存环节自伤如实登记：第一次 `save_capture` 我传了 `directory`，接口具名拒绝 `should have required property 'filepath'`（原文 `la_save_first_attempt_error.json` 未删）；改用 `filepath` 后对**同一次已采完的会话**重存成功——这是采集之后的保存动作，不是重采。
## 3. 对齐与收口
- 采集起 `10:10:37.465` 早于首个响应写入 `10:10:46.819`；末响应写入 `10:10:53.945` 早于采集止 `10:11:05.482` ⇒ 采集窗完整包住串口会话，两端各有约 9 s 与 11 s 余量。
- `stop_capture` 在收口断电之前完成 ⇒ 断电尾段不在这 28.02 s 内，按任务书第 69 行单独标识，不混入稳定供电的值判定。
- **待操作者回报**：收口切断 VM+MCU 的时刻与观察到的掉电表现（`PENDING_OPERATOR_REPORT`）。我不代填。
## 4. A 补充修订五条的落实
| 条 | 落实 |
| --- | --- |
| 1 输入范围 | 按商家页 0–5 V 接受，等级锁在「厂商页声称＋实测特征吻合」，未升级成手册/实测耐压 |
| 2 通道映射 | 8 路全开采集；映射未核清即标 `CHANNEL_MAPPING_UNRESOLVED`，未反推身份；CSV 列号导出后读取（实测导出给全部 8 列） |
| 3 缓冲 | 1 MS/s＋显式 512 MB；本机物理内存 33.3 GB、空闲 14.4 GB；接口不回实际值，改由覆盖性事后验证（见 §3）；MB 未等同样点数 |
| 4 供断电 | 执行前的断电→上电登记为前置供电调整；结束断电为允许的收口动作，待回报；未因此再启动或复测 |
| 5 令牌 | 取 `capture_scope` 原文，未拼接、未再询问 |
## 5. 边界（不变）
- `polarity_measured=false`、`core_protection_verified=false` 保持；四条禁用声称仍全部禁止：`PHYSICAL_POLARITY_VERIFIED` / `DIAG_TRIGGER_VERIFIED` / `STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE` / `CORE_PROTECTION_VERIFIED`。
- 本轮**没有**取得：报警态与物理极性两态、中断是否真被投递、实际触发与恢复、电压数值（无 DMM）、µs 级 DIAG→IRQ 延迟（无共同硬件时基）、SG/ADC/IMU、arm/disarm 与运动、带载最坏栈。
- 全过名称 `STATIC_IRQ_TRIAL_WITH_DIAG_ENN_CAPTURE_PASS` **不使用**：串口层成立、仪器层不具区分力，按任务书第 82 行分报。
## 6. 旧盲测账未被动用
`M8R7F5-b502-blind-irq-static-20261104` 仍 `spent` 全 0、`pending` 空；按任务书第 25 行它已被新身份替代，我不会再拿它跑一遍旧盲测。
## 7. 复算入口
```
python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5a_field_judgement.py
python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5a_field_archive.py
```
