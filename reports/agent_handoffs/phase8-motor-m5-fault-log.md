# Phase 8 M5：软件故障停机与新日志闭环（CP0 交付）

日期：2026-10-29（北京时间）
分支：`debug/tmc-uart-rootcause-20261028`
基线：`6218efa251cd6b8a16ad2df8f0fef1efd2ba5eeb`（M4 收口）
software_input / code_commit：`8cf23aa6dda20baf7843798bdef9a02cfd71326f`
状态：**CP0 完成；CP1 部分执行：P0 通过、P1 归档通过、清区命令已发送但完成未验证、P2–P4 NOT_REACHED。**
CP1 原始证据与停点：`reports/reviews/phase8-motor-m5-fault-log/cp1/m5_result_partial.md`。

## 1. 产品改动（最小面）

- `safety_state.c`：新增 `safety_trip CONFIRM`。仅接受 argc==2 且逐字 `CONFIRM`；
  普通线程、非 ISR、PRIMASK 未置位才执行；只调用一次
  `safety_post_event(EVT_SOFT_FAULT)`，由正式路径先做短保护再投事件；不直接改
  `ss_state`、不自己调 `blackbox_trigger`、不自动恢复；摘要只写投递结果，不声称持久化或真实堵转。
- `ns_flash_shell.c`：`flash_dump` 参数改为严格十进制/完整边界检查（first 0..4095、
  count 1..100、first+count≤4096），任何越界在 Flash 读之前拒绝；头行与数据行改为
  受检 `rt_snprintf` + `rt_kputs`（256 B 行缓冲，14 列不变），溢出报具名错误且不输出半条；
  空槽/读错分别输出 `slot N empty` / `slot N error <rc>`。
- `diagnosis.c`：状态输出新增 ADC 校准来源（THEORETICAL 明确“不是实测电流”）、
  `threshold_validation=NOT_ESTABLISHED`、MONITOR_ONLY 与振动含 ~1 g 重力基线；判据/阈值未改。
- `motor.c`：仅给 `cmd_motor_arm/cmd_motor_start` 补 `(void)argv;`，使 S2 的“0 warning
  构建门”继续有意义，无行为变化。
- 验证链：S1 增加 PS17a–e 与 `rev_safety_trip_skip_post` 反向；新增
  `shim_flash/`+`flash_main.c`+`run_flash_export_m5.sh`（FE01–FE06、容量探针、反向）；
  新增 `m5_log_archive_check.py`（20 例自测）；S1 runner 修 EPIPE 并加未登记 ID 负控；
  S2 夹具假 TMC IOIN 改为合法 0x21 以适配 916c34b 起已接受的启动身份检查。
- 现场脚本：`reports/reviews/phase8-motor-m5-fault-log/m5_field_serial.py`（archive/fault
  两阶段、清区门、3 s/5 s 截止、16 轮有界轮询、失败不自动恢复），自带 4 例离线自测。

## 2. 离线验证结果

| 链 | 结果 |
|---|---|
| S1 target/host_ovr | 55/55、70/70 全部 PASS，runner rc=0（正常） |
| S1 反向 | rev_motor_off 5/5、rev_safety_trip_skip_post 1/1 META-OK，rc=0 |
| flash 正常 | FE01–FE05 PASS；容量探针 FE06 PASS；判读器 20/20；完整批次 ACCEPT/CLEAR_ALLOWED=YES；缺槽 REJECT |
| flash 反向 | 仅 FE02 判 FAIL（126 B 截断+NUL）；截断批次 REJECT NUL_BYTE / CLEAR_ALLOWED=NO；rc=0 |
| S2 选择组 | ONLY=recovery,identity,stale,diag：target 4 组/6 例、host_ovr 4 组/19 例，全部 PASS，rc=0 |
| V1/V2 | CHECK_PASS=22 / FAIL=0，rc=0 |
| I/O 行为 | 四变体 27/29/40/42 必需用例 PASS，rc=0 |
| SD 链 | NOT_RERUN（`sensor_service.c`/`blackbox.c` 本轮未变） |
| 现场脚本自测 | FS01 归档拒绝禁止 clear；FS02 3 s/5 s 顺序；FS03 满日志未批准不清；FS04 无新日志禁止恢复；5/5 PASS |

说明：S1 的 `safety_trip` 用例验证的是**正式路径的事件投递/上下文/失败摘要**；Safety 线程
消费者在该链中是桩，端到端锁存与黑匣子由现场 P2–P4 验证。flash 链只证明导出格式与边界，
不证明真实 Flash 页布局/CRC/串口完整性。

## 3. 三目标构建与镜像身份

UV4 5.43.1.0 / ARMCLANG V6.24，三目标均 Rebuild All 0 Error / 0 Warning。

| Target | Program Size | BIN bytes / SHA-256 | AXF bytes / SHA-256 |
|---|---|---|---|
| rt-thread | Code=180586 RO=78306 RW=2188 ZI=83784 | `reports/firmware/phase8-motor-m5-fault-log/default.bin` 259468 / `fdab581e63ccb19b7617a8430a0847eb1b7c1d53b3c97aae6ed21da5162f94b2` | `rt-thread.axf` 1667756 / `cb3d4e7507459a98fb6b268b3dcff7714c3da5bb58bc0975844e5cd790cdbbab` |
| rt-thread-measure-a0 | Code=187882 RO=81266 RW=2188 ZI=91504 | `measure-a0.bin` 269724 / `17c6f191dbf80635b903d88919b25ef7ba7ae5cffa784be093f59fd0062a1771` | `rt-thread-a0.axf` 1698176 / `3adf06f1061c78e212f052d219289433106afa9896889938e5071d6598777786` |
| rt-thread-motor-m1-candidate | Code=180818 RO=78186 RW=2188 ZI=83784 | `rt-thread-motor-m1.bin` **259580 / `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`** | `rt-thread-motor-m1.axf` **1668244 / `8c5bc7bf1efa786013c767ae1e09415ad0d5d5db3e38069c8aeebabf7cebae40`** |

- 候选宏：`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=1`、`TMC_DRIVE_CONFIG_HW_VALIDATED=1`、
  `STEP_DIAG_ISOLATED_ENV_CONFIRMED=0`、`SAFETY_LIMIT_INPUTS_ENABLED=0`；armclang 探针输出
  `MOTOR=1 TMC=1 STEP=0 LIMIT=0`，默认/A0 为 `0/0/0/0`。
- 候选 PT_LOAD：base `0x08000000`，文件载荷 259580 B，内存镜像 344976 B。
- 启动横幅：`build Oct 29 2026 00:38:59`。
- manifest：`reports/firmware/phase8-motor-m5-fault-log/build_manifest.json`。

## 4. 现场包与批准门

- 包：`reports/reviews/phase8-motor-m5-fault-log/m5_field_package.md`
- 现场脚本：同目录 `m5_field_serial.py`（archive/fault；离线自测 4/4）
- 判读器：`tests/host/m5_log_archive_check.py`（真实输入和 20 例自测同函数）
- 允许：1 次下载、1 次定长回读、1 次显式启动；COM5 ≤2 次；必要时 1 次原 IRUN20 apply；
  归档通过且空间不足时 1 次清 1 MiB 日志区；DIR0/300 Hz 一次空载起转，t≈3 s 一次
  `safety_trip CONFIRM`、t=5 s 一次兜底 disarm；故障态 arm 拒绝 ≤1 次；16 轮写盘轮询；
  1 次 `fault_reset`、1 次 `system_selftest`；恢复后不起转。
- 禁止：升流/堵转/电气注入/改保护模式/改细分/改线/全片擦除/OTP/选项字/恢复下载/设备枚举/
  `sw1_cut.ps1`/A0 测量/`runtime_selftest`/`ss_test`/`blackbox_selftest`。
- 失败不自动重跑/重烧/二次清区/第二次触发；真实危险由既有硬件阻断处理。

## 5. 结论分级（本轮）

- `M5_CP0_OFFLINE_COMPLETE`：产品/验证/构建/包已完成并通过离线证据；
- 现场三项 `M5_SOFTWARE_EVENT_STOP_PASS`、`M5_NEW_FAULT_LOG_PASS`、`M5_MANUAL_RECOVERY_PASS`
  均 **NOT_REACHED**（等待批准）；
- `MONITORING_VALIDITY_DISCLOSED`：本机输出已如实披露，但 SG 负载/速度适用性仍未标定；
- `PROJECT_SAFETY_ACCEPTANCE_PENDING`：DIAG 物理触发、真实堵转/负载诊断标定、实测电流、
  物理 PWM 精度/停机时延等仍开放。
- M4 已接受的两项目标级例外（使能链、TMC 配置）沿用；`safe_for_enable` 仍为 false。


## 6. CP1 现场执行（2026-10-29，部分完成）

- P0 PASS：候选下载 1 次（260096 B 擦写）、`0x08000000` 起 259580 B 回读与 BIN
  逐字节相等、显式复位 1 次；运行横幅 `5.3.0 build Oct 29 2026 00:38:59`。
- P1 归档 PASS：4096/4096 valid、bad=0；41 批导出；首次判读因末尾裸 `msh >`
  被误判 TRUNCATED_TAIL（数据行完整），修正框架行后离线复判 ACCEPT；两份归档
  逐文件 SHA 复核（45 文件、copies=2）。
- 清区：唯一一次 `flash_logclear ERASE` 已发送（10:01:11），但原始件只有命令回显；
  脚本把回显后 3 s 静默当作完成，随后即时 `flash_logstat` 无响应而中止。
  **清区完成状态未验证**；未重试、未第二次清区。
- P2–P4 NOT_REACHED：电机未起转、未 `safety_trip`、无新日志、无恢复。
- 额度：DAP 3/3、COM5 2/2 已用尽；无主机进程占用 COM5。
- 结果：`M5_CP1_PARTIAL`；三项现场 PASS 均 NOT_REACHED；需一次额外 COM5 会话批准
  （先验证日志 0/0/0，若成立再做 P2–P4；不成立则停止，不清第二次）。

## 7. CP1b 追加会话（2026-10-29，停在起转前门禁）

- 只读确认清区完成：`slots=0/4096 valid=0 bad=0`、队列 0/0/0；READY/fault0、LOW/pad0、
  TMC CONFIGURED、BB 空闲。
- P2 尝试：`motor_dir 0` OK；`motor_arm` **REFUSED**（`arm_gate_mask=0x8`、
  `gate4 irq_gate=CLOSED polarity=not-confirmed`、`protection-not-ready`）；
  `motor_target 300` 被脚本在拒绝后仍发送（已修正）；`motor_start` REFUSED not armed；
  t≈1 s 状态仍 `armed=0 cur=0`，脚本停止。**电机从未起转。**
- 未发送 `safety_trip`、未产生新日志、未做恢复。日志仍 `0/0/0`；软件 target 留在 300 Hz。
- 工具修正：fault 前置要求 `arm_gate_mask==0`，arm 后必须 `armed=1` 才发 target/start；
  自测 FS01–FS06 6/6。
- 结果：`M5_CP1_STOPPED_AT_ARM`；需新批准一次 COM5 会话：target 0 清理 + 现场确认
  DIAG/H6-7 0 V 后一次 `safety_polarity_confirm CONFIRM` + 一次 `safety_irq_enable`，
  复核 mask=0 再执行 P2–P4；不清区/不重烧/不自动重跑。


## 8. CP1d 追加尝试（2026-10-29，停在 target 清理前的旧值检查）

- 第二次追加会话在 `preflight` 阶段因 target 仍为 300 Hz 中止；**未发送 motor_target 0、
  未执行 polarity/IRQ、未发送 arm/start**。板上 gate 仍 0x8、日志 0/0/0。
- 根因：编排顺序错误；`fault()` 已改为默认先 `motor_target 0` 再 preflight，并提供
  `clear_target=False` 供编排方避免重复；自测 FS01–FS08 8/8。
- 需要再一次 COM5 会话：target 0 → preflight → `safety_polarity_confirm CONFIRM` +
  `safety_irq_enable` → 复核 mask=0 → P2–P4；不清区/不重烧。


## 9. CP1e 最终会话（2026-10-29，软件故障已触发）

- 修正编排后：target 0 → 前置 READY/LOW/无 STEP → 一次 polarity confirm + 一次 IRQ enable
  → 复核 `irq_gate=OPEN`、`protection_ready=YES`、`arm_gate_mask=0x0` → DIR0/300 Hz 起转。
- t≈1 s：`state=2 cur=tgt=300 armed=1`，sensor `step_hz=300`（确实运行）。
- t≈3 s 一次 `safety_trip CONFIRM`；原始流含 `FAULT_LATCHED code=9`（DRV_ENABLE LOW verified、
  ENN OK、STEP off-confirmed(api)）、`[SAFETY] EVT_SOFT_FAULT -> 已整理`、
  `[BB] trigger code=9 session=1 pre_n=200 post_eff=100`。
- 脚本的固定短句匹配被并发输出打断，trip 后立即中止：未兜底 disarm、未读回新日志、未恢复。
  判据已改为以 canonical `FAULT_LATCHED code=9` 为准；自测 FS01–FS08 8/8。
- 板上预期 FAULT_LATCHED code9；物理停止未测量/未取得操作者书面确认。
- 需一次**无起转**COM5 会话：只读确认锁存/禁能/BB 空闲 → 导出新日志并判读 →
  必要时一次 disarm → 一次 fault_reset + system_selftest；不清区/不重烧/不再触发。


## 10. CP1f 收尾会话（2026-10-29，日志已写入、读回未做）

- 只读：FAULT_LATCHED code9、请求 LOW/ENN OK、STEP enabled=0、BB state0 session1
  written300 last_err0 health OK、`[BB] write drops=0`；flash used=valid=300 bad0、
  acc=done=300 fail0、`queue_dropped=51`。
- `queue_dropped=51` 是 `ns_log_submit` 队列满重试计数，不是已放弃帧；`write drops=0`
  与 written/used/valid=300 支持日志完整，但必须读回 300 槽才能判 PASS。
- 脚本把 drop==0 设硬门而中止：未 flash_dump 读回、未 fault_reset/system_selftest；
  板仍 FAULT_LATCHED。新日志与恢复仍 NOT_REACHED。
- 需一次无起转收尾会话：导出 0..299 并判读；通过后可选一次 disarm、一次 fault_reset、
  一次 system_selftest；不清区/不重烧/不再触发。


## 11. CP1g 收尾重试（2026-10-29）

- 只读再次确认 FAULT_LATCHED code9、LOW/ENN、STEP off、BB session1 written300、
  `[BB] write drops=0`；flash used=valid=300 bad0、accepted=completed=300 failed0、
  queue_dropped=51（重试计数）。
- 脚本把 flash_logstat 的 `queued=`（实际是 accepted）误当待处理队列，要求 0 而中止；
  未读回/未恢复。已改为 `accepted==completed` 且只看批准判据。
