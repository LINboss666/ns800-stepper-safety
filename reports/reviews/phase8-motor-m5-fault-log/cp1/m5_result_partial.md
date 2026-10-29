# NS800 M5 现场执行结果（CP1 部分完成，停在清区验证前）

日期：2026-10-29（北京时间）
批准载荷：code_commit `8cf23aa6dda20baf7843798bdef9a02cfd71326f`，
BIN 259580 B / `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，
AXF 1668244 B / `8c5bc7bf1efa786013c767ae1e09415ad0d5d5db3e38069c8aeebabf7cebae40`。
状态：**P0 通过；P1 归档通过、清区命令已发送但完成状态未验证；P2–P4 NOT_REACHED。**
没有第二次下载、没有第二次清区、没有起转、没有 safety_trip、没有 fault_reset/system_selftest。

## 1. P0 身份与静止前置（PASS）

- 下载 1 次：pyOCD 0.45.1 + `NOVOSENSE.NS800RT7XXX.0.5.3.pack`，
  `ns800rt7p65d_dhqfby2`，sector erase；`Erased 260096 B, programmed 260096 B`，返回码 0。
- 定长回读 1 次：`0x08000000` 起 259580 B，SHA-256
  `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，
  与候选 BIN **逐字节相等**。
- 显式复位/启动 1 次：`pyocd reset -t cortex_m`，返回码 0。
- 运行横幅（`version` 命令，原始件 `preclear/002_version.bin`）：
  `5.3.0 build Oct 29 2026 00:38:59`，与载荷构建时间一致。
- 运行前静止：READY/fault0、armed0/cur=tgt0、请求 LOW/pad0、ENN 禁能侧、TMC CONFIGURED；
  重烧后执行了**一次** `tmc_config_apply CONFIRM`（原始件 `archive_013_tmc_config_apply_CONFIRM.bin`；该次 apply 是批准包允许的唯一一次）。
- 诊断 MONITOR_ONLY；`adc_cal_source=THEORETICAL`；`threshold_validation=NOT_ESTABLISHED`。

## 2. P1 旧日志归档与清区（归档 PASS；清区完成未验证）

- 旧日志：`slots=4096/4096 valid=4096 bad=0`，BB `session=0 written=0 acc=0 done=0`、fail/drop=0。
- 41 批 `flash_dump`（末批 96 条），每批原始 RX 先落盘；重建 CSV 4096 行，槽号 0..4095 无缺/重。
- 首次判读：`ARCHIVE=REJECT reason=TRUNCATED_TAIL`。原因是所有批次以控制台提示符 `msh >`
  结尾且无 LF，判读器把它当成数据截断；**不是数据行损坏**。首次失败原件保留为
  `old_archive_checker_first_failure.txt`。
- 修正判读器“末尾裸提示符”框架行后，用同一批原始件离线复判：
  `ARCHIVE=ACCEPT rows=4096 batches=41 / CLEAR_ALLOWED=YES`；正式结果见
  `old_archive_checker.txt`、`old_archive.csv`、`old_archive_manifest.json`。
- 两份归档：源目录与第二目录逐文件 SHA 复核，`COPY_VERIFIED.json` 记录 45 个文件、
  `copies=2`、`used=4096`；第二份 `COPY_VERIFIED.second.json` 已归档。
- 清区：在第二次也是批准额度内最后一次 COM5 会话中，先复核计数仍为 4096/4096/0、
  BB 仍空闲，随后发送**唯一一次** `flash_logclear ERASE`（2026-10-29 10:01:11）。
  原始件 `preclear/013_flash_logclear_ERASE.bin` **只有命令回显，没有完成结果**。
- 脚本原实现把“回显后 3 s 静默”当作命令完成；擦除仍在进行时便发送下一条
  `flash_logstat`，该查询无响应（`preclear/014_flash_logstat.bin` 为空，4 s 超时），
  脚本在验证前中止。**清区是否完成未知**；未重试、未第二次清区。

## 3. P2–P4（NOT_REACHED）

- 未发送 `motor_dir/motor_arm/motor_target/motor_start`，电机未起转。
- 未发送 `safety_trip CONFIRM`，无软件故障事件、无锁存、无新黑匣子会话。
- 未发送 `fault_reset`/`system_selftest`，未做人工恢复。
- 因此 `M5_SOFTWARE_EVENT_STOP_PASS`、`M5_NEW_FAULT_LOG_PASS`、`M5_MANUAL_RECOVERY_PASS`
  全部 **NOT_REACHED**。

## 4. 已用额度与当前状态

- DAP：下载 1、回读 1、复位 1，共 3 次（额度用尽）。
- COM5：归档会话 1、清区/故障尝试会话 1，共 2 次（额度用尽）；当前无主机进程占用。
- 板上：批准候选镜像；电机从未被本轮命令使能；日志分区可能已被清空或仍在擦除，
  **没有可核验的 used/valid/bad**。安全状态预计仍为 READY，但本轮未读取。
- 不自动重开 COM5、不自动重清、不自动起转。真实危险仍由既有硬件阻断处理。

## 5. 需要的新批准（最小范围）

只申请**一次额外 COM5 会话**（超出原 2 次额度），用于：
1. 只读查询 `flash_logstat/blackbox_status/safety_state/safety_status/motor_status`，
   确认清区是否完成且系统 READY/静止；
2. 仅当 `used=valid=bad=0`、READY、请求 LOW、TMC CONFIGURED、BB 空闲时，执行原批准的
   P2–P4：DIR0/300 Hz 一次起转、t≈3 s 一次 `safety_trip CONFIRM`、t=5 s 一次兜底
   `motor_disarm`、故障态一次 arm 拒绝、新日志完整读回、一次 `fault_reset` 与一次
   `system_selftest`，恢复后不起转；
3. 若 `used/valid/bad` 不是 0/0/0，立即停止并报告，**不第二次清区**、不触发故障；
4. 不重新烧录、不重复归档、不升流/堵转/注入、不改保护模式。

## 6. 结论

`M5_CP1_PARTIAL`：镜像身份、旧日志完整逻辑归档与两处校验成立；清区命令已按额度发送一次，
但完成状态未验证；软件故障停机、新日志与人工恢复均未开始。M4 已接受功能不撤回；
安全验收仍 PENDING。
