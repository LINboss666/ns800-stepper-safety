# Phase 8 M5：软件故障停机与新日志闭环（CP0 交付）

日期：2026-10-29（北京时间）
分支：`debug/tmc-uart-rootcause-20261028`
基线：`6218efa251cd6b8a16ad2df8f0fef1efd2ba5eeb`（M4 收口）
software_input / code_commit：`8cf23aa6dda20baf7843798bdef9a02cfd71326f`
状态：**CP0 离线完成；候选镜像已构建归档；未上板、未烧录、未清日志、未起转；现场包等待用户一次批准。**

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
  新增 `m5_log_archive_check.py`（19 例自测）；S1 runner 修 EPIPE 并加未登记 ID 负控；
  S2 夹具假 TMC IOIN 改为合法 0x21 以适配 916c34b 起已接受的启动身份检查。
- 现场脚本：`reports/reviews/phase8-motor-m5-fault-log/m5_field_serial.py`（archive/fault
  两阶段、清区门、3 s/5 s 截止、16 轮有界轮询、失败不自动恢复），自带 4 例离线自测。

## 2. 离线验证结果

| 链 | 结果 |
|---|---|
| S1 target/host_ovr | 55/55、70/70 全部 PASS，runner rc=0（正常） |
| S1 反向 | rev_motor_off 5/5、rev_safety_trip_skip_post 1/1 META-OK，rc=0 |
| flash 正常 | FE01–FE05 PASS；容量探针 FE06 PASS；判读器 19/19；完整批次 ACCEPT/CLEAR_ALLOWED=YES；缺槽 REJECT |
| flash 反向 | 仅 FE02 判 FAIL（126 B 截断+NUL）；截断批次 REJECT NUL_BYTE / CLEAR_ALLOWED=NO；rc=0 |
| S2 选择组 | ONLY=recovery,identity,stale,diag：target 4 组/6 例、host_ovr 4 组/19 例，全部 PASS，rc=0 |
| V1/V2 | CHECK_PASS=22 / FAIL=0，rc=0 |
| I/O 行为 | 四变体 27/29/40/42 必需用例 PASS，rc=0 |
| SD 链 | NOT_RERUN（`sensor_service.c`/`blackbox.c` 本轮未变） |
| 现场脚本自测 | FS01 归档拒绝禁止 clear；FS02 3 s/5 s 顺序；FS03 满日志未批准不清；FS04 无新日志禁止恢复；4/4 PASS |

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
- 判读器：`tests/host/m5_log_archive_check.py`（真实输入和 19 例自测同函数）
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
