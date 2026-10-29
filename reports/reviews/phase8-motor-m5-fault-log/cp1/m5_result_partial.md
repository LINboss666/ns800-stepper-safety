# NS800 M5 现场执行结果（CP1 停在起转前门禁）

日期：2026-10-29（北京时间）
批准载荷：code_commit `8cf23aa6dda20baf7843798bdef9a02cfd71326f`，
BIN 259580 B / `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，
AXF 1668244 B / `8c5bc7bf1efa786013c767ae1e09415ad0d5d5db3e38069c8aeebabf7cebae40`。
状态：**P0/P1 通过；P2 已真实起转并在运行中投递软件故障，FAULT_LATCHED code=9，BB session=1 已写入 300 帧（used=valid=300）；收尾会话因把 `ns_log` 队列重试计数 `drop=51` 当成硬失败而中止，未读回/未恢复。**
没有第二次下载、没有第二次清区、没有 safety_trip、没有 fault_reset/system_selftest。

## 1. P0 身份与静止前置（PASS）

- 下载 1 次：pyOCD 0.45.1 + `NOVOSENSE.NS800RT7XXX.0.5.3.pack`，
  `ns800rt7p65d_dhqfby2`，sector erase；`Erased 260096 B, programmed 260096 B`，返回码 0。
- 定长回读 1 次：`0x08000000` 起 259580 B，SHA-256
  `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，与候选 BIN 逐字节相等。
- 显式复位/启动 1 次：返回码 0。
- 运行横幅（`version`）：`5.3.0 build Oct 29 2026 00:38:59`。
- 重烧后一次 `tmc_config_apply CONFIRM`（批准的唯一一次）；
  原始件 `archive_013_tmc_config_apply_CONFIRM.bin`。
- 最终只读前置：READY/fault0、armed0/cur=tgt0（起转前）、请求 LOW/pad0、ENN 禁能侧、
  TMC CONFIGURED、MONITOR_ONLY/THEORETICAL、BB 空闲、日志 `0/0/0`。

## 2. P1 旧日志归档与清区（均 PASS）

- 归档前：`slots=4096/4096 valid=4096 bad=0`，BB `session=0 written=0 acc=0 done=0`、fail/drop=0。
- 41 批 `flash_dump`（末批 96 条），重建 CSV 4096 行，槽号 0..4095 恰好一次。
- 首次判读 `TRUNCATED_TAIL`：真实串口每批以裸 `msh >` 结尾（无 LF），被判成数据截断；
  数据行完整。首次失败原件 `old_archive_checker_first_failure.txt` 保留。
- 修正判读器框架行后离线复判：`ARCHIVE=ACCEPT rows=4096 batches=41`；
  正式结果 `old_archive_checker.txt`、`old_archive.csv`、`old_archive_manifest.json`。
- 两份归档逐文件 SHA 复核：45 文件、`copies=2`，`COPY_VERIFIED.json` / `.second.json`。
- 唯一一次 `flash_logclear ERASE`（10:01:11）后，本次追加会话只读确认：
  `slots=0/4096 valid=0 bad=0`、`queued=0 completed=0 failed=0 queue_dropped=0 last_error=0`
  （原始件 `final_precheck/012_flash_logstat.bin`），**清区完成已验证**。

## 3. P2 尝试与停点（BLOCKED_PREREQUISITE）

追加会话按原批准顺序发送：

| 命令 | 实际结果（原始件） |
|---|---|
| `motor_dir 0` | `[MOT] dir=0 (at rest, 全部条件与 DIR 写同窗)` |
| `motor_arm` | **REFUSED**：`arm_gate_mask=0x8`，`gate_reason=protection-not-ready`，`gate4 irq_gate=CLOSED polarity=not-confirmed` |
| `motor_target 300` | `[MOT] set target 300 Hz: OK`（脚本在 arm 结果后仍继续，已修正） |
| `motor_start` | `[MOT] start REFUSED: not armed`，rc=-8 |
| t≈1 s `motor_status` | `state=0 cur=0 tgt=300 armed=0` → 脚本判“未在 300 Hz 运行”并停止 |

- 电机 **从未 armed、从未起转**；无 safety_trip、无新 BB 会话、无新日志帧。
- 停点原因：重烧后 `safety_polarity_confirm`/`safety_irq_enable` 未执行，保护 IRQ 门为 CLOSED；
  这是 M5 包前置“IRQ/软件门就绪”不成立的真实门禁拒绝，不是产品故障。
- 现场副作用：软件 target 留在 **300 Hz**（armed=0、start 已拒绝、无 STEP）；日志仍 `0/0/0`。
- 工具修正（离线，已加自测）：`fault()` 现在先要求 `arm_gate_mask==0`，且 arm 后必须读到
  `armed=1` 才发送 target/start；门禁不成立时**不发送任何电机命令**。自测 FS01–FS08 8/8。

## 4. P3/P4（NOT_REACHED）

- 无故障锁存、无故障态 arm 拒绝验证、无新日志读回、无 `fault_reset`/`system_selftest`。

## 5. 额度与当前状态

- DAP：下载 1、回读 1、复位 1，共 3/3。
- COM5：归档 1、清区 1、追加只读+P2 尝试 1，共 3/3；当前无主机进程占用。
- 板上：批准候选镜像；日志 `0/0/0`；READY/fault0；armed=0；target=300；
  IRQ gate CLOSED / protection not ready；无故障。
- 不自动重开 COM5、不自动起转/清区/重烧。

## 6. 需要的新批准（最小范围）

一次额外 COM5 会话，按以下顺序：
1. `motor_target 0` 清掉留下的软件 target，并确认 `armed=0`、LOW、无 STEP；
2. 现场确认既有 DIAG/H6-7 对地 0 V 条件仍成立后，执行一次 `safety_polarity_confirm CONFIRM`
   与一次 `safety_irq_enable`，只读复核 `irq_gate=OPEN`、`protection_ready=YES`、
   `motor_status` 的 `arm_gate_mask=0x0`；
3. 只有以上全部成立才执行原批准 P2–P4：DIR0/300 Hz 一次空载起转、t≈3 s 一次
   `safety_trip CONFIRM`、t=5 s 一次兜底 `motor_disarm`、故障态一次 arm 拒绝、新日志完整读回、
   一次 `fault_reset` 与一次 `system_selftest`，恢复后不起转；
4. 任一前置不成立立即停止报告；不第二次清区、不重烧、不升流/堵转/注入/改保护模式；
   失败不自动重跑。

## 7. 结论

`M5_CP1_STOPPED_AT_ARM`：镜像身份、旧日志完整归档、两处校验与唯一一次清区均成立；
软件故障停机链在起转前被真实保护门禁（IRQ CLOSED/polarity not confirmed）拒绝，电机未运动；
`M5_SOFTWARE_EVENT_STOP_PASS` / `M5_NEW_FAULT_LOG_PASS` / `M5_MANUAL_RECOVERY_PASS`
均 NOT_REACHED。M4 已接受功能不撤回；安全验收仍 PENDING。


## 8. CP1d 追加尝试（停在 target 清理前的旧值检查）

- 第二次追加会话打开 COM5 后，编排脚本先调用 `preflight`，而板上 target 仍为上次留下的 300 Hz；
  `preflight` 按“target 必须为 0”立即中止，**未发送 `motor_target 0`、未执行
  polarity/IRQ 命令、未发送任何 motor arm/start**。
- 原始件：`irq_pre_target/`（12 个只读响应）与 `irq_fault.log`；gate 仍 0x8、日志 0/0/0。
- 根因是编排顺序错误（应在 preflight 之前先 `motor_target 0`），不是产品/门禁失败。
- 修正：`fault()` 默认先 `motor_target 0` 再 preflight；调用方可传 `clear_target=False`
  避免重复；已加 FS07/FS08 自测，当前 8/8。
- 第三次尝试仍需要一次新的 COM5 会话；板上 target 仍为 300、armed=0、无运动。


## 9. CP1e 最终会话（软件故障已触发，读回/恢复未做）

- 修正编排后同一会话完成：`motor_target 0` → 前置 READY/LOW/无 STEP → 一次
  `safety_polarity_confirm CONFIRM` → 一次 `safety_irq_enable` → `irq_gate=OPEN`、
  `protection_ready=YES`、`arm_gate_mask=0x0`。
- 起转：`motor_dir 0`、`motor_arm`（ARMED，DRV_ENABLE HIGH 回读、ENN LOW confirmed）、
  `motor_target 300`、`motor_start`。
- t≈1 s（`018_motor_status.bin`）：`state=2`、`cur_hz=300 tgt_hz=300`、`armed=1`；
  `019_sensor_snapshot.bin`：`step_hz=300`。**电机确实在 300 Hz 运行。**
- t≈3 s 仅一次 `safety_trip CONFIRM`。原始件 `020_safety_trip_CONFIRM.bin`（496 B，含 NUL/交错）
  可读出：`[SS] ==> FAULT_LATCHED code=9 (DRV_ENABLE LOW verified, ENN feedback OK,
  STEP off-confirmed(api), motor_rc=0 diag_rc=0)`、`[SAFETY] EVT_SOFT_FAULT -> 已整理`、
  `[BB] trigger code=9 session=1 pre_n=200 post_eff=100`。
- 脚本的字符串判据 `EVT_SOFT_FAULT posted rc=0` 被 Safety/BB 并发输出打断而未匹配，
  脚本在 trip 后立即中止：**未发送 t=5 s 兜底 `motor_disarm`、未单独保存 trip 后
  motor/safety 状态、未读回新日志、未恢复**。这是判据实现缺陷，不是事件失败。
- 板上预期：FAULT_LATCHED code 9、DRV_ENABLE 已由 trip 保护拉低、BB session 1 可能已写约
  300 帧；未 `fault_reset`。物理转轴是否停止尚无仪器测量/操作者书面确认。
- 工具修正：trip 接受改为以有界窗口内 canonical `FAULT_LATCHED code=9` 为准，不再依赖
  单行短句；自测 FS01–FS08 8/8。

### 需要的新批准（无起转）

一次 COM5 会话：
1. 只读确认 `safety_state/safety_status/motor_status/step_status/blackbox_status/flash_logstat`：
   FAULT_LATCHED code9、输出 LOW/ENN 禁能、STEP 无输出、BB 空闲、日志 used/valid 与 session1 一致；
2. 用 `flash_dump` 导出新槽（预计 0..used-1，count≤100），以 `--start 0` 通过归档判据，
   核对 session=1、pre 段 event=0 且含 step_hz>0、post 段 event=9；
3. 必要时一次普通 `motor_disarm` 清理（若输出/许可未确认禁用）；
4. 一次 `fault_reset`、一次 `system_selftest`，恢复后不 arm/start；
5. 不第二次清区、不重烧、不再次触发故障、不升流/堵转/注入；失败不自动重跑。


## 10. CP1f 收尾会话（日志已写入，读回/恢复未做）

只读结果（`finalize_pre/`）：
- `safety_state` = FAULT_LATCHED code 9；
- `safety_status` = 请求 LOW/pad0、ENN FB=1 verdict=OK；
- `motor_status` armed=0、cur=0；`step_status` enabled=0；
- `blackbox_status`：state=0、session=1、written=300、last_err=0、health=OK，
  **write drops=0**，窗口 pre_eff=200/post_eff=100；
- `flash_logstat`：`slots=300/4096 valid=300 bad=0`、`queued=0 completed=300 failed=0`，
  `queue_dropped=51`。

判读：
- `[BB] write drops=0` 表示黑匣子没有放弃任何帧；`queue_dropped=51` 是
  `ns_log_submit()` 在队列满时的**重试计数**（源码：失败一次 ++dropped，随后 flush/重试），
  与 `written=300`、`used=valid=300`、`acc=done=300`、`fail=0` 一致，不能单独证明丢帧。
- 脚本把 `ns_log_stats.dropped==0` 设为硬门，因此中止；**未执行 flash_dump 读回、
  未 fault_reset/system_selftest**。板上仍是 FAULT_LATCHED code9，输出由 trip 保护拉低。
- 新日志是否 PASS 必须由 300 个槽的完整读回决定；在完成前不宣称
  `M5_NEW_FAULT_LOG_PASS`。

### 需要的新批准（无起转，最后一次收尾）

一次 COM5 会话：
1. 只读复核 FAULT_LATCHED code9、LOW/ENN、STEP disabled、BB 空闲、session=1、written=300、
   used=valid=300、bad=0、fail=0、`[BB] write drops=0`；`queue_dropped=51` 如实记录为
   队列重试，不作为单独的丢帧证据，也不清洗；
2. `flash_dump` 分批导出 0..299 全部槽（count≤100），按 `start=0 --expected 300` 判读；
   要求每槽恰好一次、session=1、pre 段 event=0 且含 step_hz>0、post 段 event=9、
   CRC/commit/读错误为零；若任一缺失/坏页/读错，立即停止并保留故障态；
3. 完整读回通过后，必要时一次普通 `motor_disarm`；一次 `fault_reset`、一次
   `system_selftest`；恢复后不 arm/start；
4. 不第二次清区、不重烧、不再触发故障、不升流/堵转/注入；失败不自动重跑。
