# NS800 M5 现场包：软件故障停机与新日志闭环

日期：2026-10-29（北京时间）
状态：**CP0 已完成，未上板、未烧录、未清日志、未起转。** 本文是待用户一次批准的现场包，不是已执行记录。
代码提交（software_input / code_commit）：`8cf23aa6dda20baf7843798bdef9a02cfd71326f`

## 1. 载荷身份（唯一允许上板的候选）

| 项 | 字节数 | SHA-256 |
|---|---:|---|
| BIN `reports/firmware/phase8-motor-m5-fault-log/rt-thread-motor-m1.bin` | 259580 | `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc` |
| AXF `reports/firmware/phase8-motor-m5-fault-log/rt-thread-motor-m1.axf` | 1668244 | `8c5bc7bf1efa786013c767ae1e09415ad0d5d5db3e38069c8aeebabf7cebae40` |

- 启动横幅：`build Oct 29 2026 00:38:59`（现场作为第二身份证据）。
- PT_LOAD：base `0x08000000`，文件载荷 259580 B，内存镜像 344976 B。
- 候选 Target 宏：`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=1`、`TMC_DRIVE_CONFIG_HW_VALIDATED=1`、
  `STEP_DIAG_ISOLATED_ENV_CONFIRMED=0`、`SAFETY_LIMIT_INPUTS_ENABLED=0`。
  armclang 6.24 探针实测 `MOTOR=1 TMC=1 STEP=0 LIMIT=0`；默认/A0 为 `0/0/0/0`。
- 三目标均为 Rebuild All 0 Error / 0 Warning（default/A0/motor；见 `build_*.txt`）。
- 本包不改变 IRUN20/IHOLD8/VSENSE1、细分、频率范围、诊断阈值或持久化 schema。

## 2. 现场范围与额度（一次批准）

- 候选下载 1 次；`0x08000000` 起按 BIN 实际长度 259580 B 定长回读 1 次并逐字节比较；显式复位/启动 1 次。
- 复用已知板载 DAP 与 COM5/115200/8N1；不扫描设备/端口/探针。DAP UID 从本地私有文件读取，不写入本包。
- 已知 DAP 会话最多 3 个（下载 1、回读 1、复位 1），COM5 正常连接最多 2 次（archive 1、fault 1）。
- 必要时 1 次既有原参数 `tmc_config_apply CONFIRM`（4 写，仅恢复同一 IRUN20 配置）。
- 旧日志完整归档通过且空间不足时，1 次 `flash_logclear ERASE`，只清 `[0x010000,0x110000)` 1 MiB。
- 电机起转 1 次：DIR0、300 Hz、空载；计划 t≈3 s 投一次软件故障；t=5 s 兜底 `motor_disarm`。
- `safety_trip CONFIRM` 1 次；故障态 `motor_arm` 拒绝验证最多 1 次（不产生第二段运动）。
- 新日志完整导出；BB 写盘状态轮询最多 16 轮；`fault_reset` 1 次；`system_selftest` 1 次。
- 恢复后不 arm、不 start、不再 apply。
- 不含：真实堵转/带载、拔线/带电改线、DIAG 注入、改 VREF/升流/升频/细分、`diag_mode active`、
  限位例外、参数区写/清、全片擦除/unlock/OTP/选项字、恢复下载、外部 Flash 全片转储、
  设备枚举、`sw1_cut.ps1`、A0 测量、`runtime_selftest`/`ss_test`/`blackbox_selftest`。
- 失败不自动重跑/重烧/二次清区/第二次触发。真实危险仍用既有硬件阻断。

## 3. 下载/回读/启动命令（已成功过的同一工具参数）

工具：pyOCD 0.45.1 + `NOVOSENSE.NS800RT7XXX.0.5.3.pack`，target `ns800rt7p65d_dhqfby2`。
DAP UID 只从 `local-private-uid-file` 读取，不打印。

```bash
# 1) 下载（1 次；sector erase；AXF 的 PT_LOAD 才是实际载荷）
pyocd flash --pack "C:/Keil_v5/ARM/PACK/.Download/NOVOSENSE.NS800RT7XXX.0.5.3.pack" \
  -t ns800rt7p65d_dhqfby2 -u "$DAPUID" -f 1000000 --no-config \
  -Oauto_unlock=false -Ocache.enable_memory=false -Ocache.read_code_from_elf=false -e sector \
  "reports/firmware/phase8-motor-m5-fault-log/rt-thread-motor-m1.axf"

# 2) 定长回读（1 次；259580 = 0x3F5FC）
python -m pyocd commander --no-config -t cortex_m -u "$DAPUID" -f 1000000 \
  -O auto_unlock=false -O connect_mode=halt -O resume_on_disconnect=false \
  -O cache.enable_memory=false -O cache.read_code_from_elf=false -O scan_all_aps=false \
  -c "status" \
  -c "savemem 0x08000000 0x3F5FC <run-outside-repo>/m5_readback.bin" \
  -c "status" -c "exit"
sha256sum <run-outside-repo>/m5_readback.bin   # 必须等于 01d3e5ab…b764bc

# 3) 显式复位/启动（1 次）
pyocd reset -t cortex_m
```

下载返回码与 commander 退出码都**不能单独当成功**：以回读字节 SHA/逐字节比较 + 启动横幅为准。

## 4. P0/P1 串口归档与有条件清区（COM5 第 1 次）

```bash
python reports/reviews/phase8-motor-m5-fault-log/m5_field_serial.py archive \
  --port COM5 --out <run-outside-repo> \
  --expect-banner "build Oct 29 2026 00:38:59" \
  --allow-apply --allow-clear --second-copy <second-outside-repo-dir>
```

脚本强制顺序：
1. 查询 `system_status/safety_state/safety_status/motor_status/step_status/tmc_config_show/
   diag_status/config_show/sensor_snapshot/blackbox_status/flash_logstat`，原始 RX 逐条先落盘。
2. 要求 READY/fault0、armed0/cur=tgt=0、请求 LOW/pad0/ENN 禁能侧、TMC CONFIGURED、
   diag MONITOR_ONLY 且 `adc_cal_source=THEORETICAL`；BB 空闲且 `acc==done`；`valid==used`、`bad==0`。
   仅当 TMC 未配置时，自动执行**一次** `tmc_config_apply CONFIRM`。
3. 按 `flash_dump first count`（count≤100）逐槽导出全部 `used` 槽；每批原始件落盘后才判读。
4. 只有 `ARCHIVE=ACCEPT` 且 `CLEAR_ALLOWED=YES` 才继续；导出后 used/valid/bad 与
   BB session/acc/done/fail/drop 必须不变。
5. 生成 `old_archive_manifest.json`；清区前必须把原始件/重建 CSV/清单复制到第二处并逐文件 SHA 复核（`--second-copy`），两份 `COPY_VERIFIED.json` 均成立才继续。
   `free = 4096 - used`：`free >= 400` 不清区；否则在 `--allow-clear` 下只执行一次
   `flash_logclear ERASE`（600 s 上限），随后要求 used=valid=bad=0、queued=0、failed=0、dropped=0。
6. 写 `archive_state.json`；任何缺槽/重复/NUL/截断/越界/读错 → 停止，不清区、不起转。

当前 M4 在板日志 used=valid=4096、bad=0（现场须重新查询）。预计需要清区；清区前必须完成
全部 41 批（末批 96 条）归档并两处保存原始件与重建 CSV。

## 5. P2–P4 软件故障、新日志与人工恢复（COM5 第 2 次）

```bash
python reports/reviews/phase8-motor-m5-fault-log/m5_field_serial.py fault \
  --port COM5 --out <run-outside-repo>
```

脚本只在 `archive_state.json` 为 PASS 且（已清区或空间足够）时启动，然后：
1. 再查静止/READY/LOW/CONFIGURED/BB 空闲并记录 `pre_used` 与 BB session/written。
2. `motor_dir 0` → `motor_arm` → `motor_target 300` → `motor_start`（t=0）。
3. t≈1 s 查询 `motor_status` 与 `sensor_snapshot`：要求 state∈{ACCEL,CRUISE}、tgt=300、cur>0。
4. t≈3 s **只发一次** `safety_trip CONFIRM`；立即保存响应与 `motor_status/safety_state/safety_status`。
5. t=5 s 发一次兜底 `motor_disarm`；前后两次结果分别保存，不用后一次覆盖前一次。
6. 要求 FAULT_LATCHED code=9、请求 LOW/pad0、ENN 禁能侧；故障态 `motor_arm` 必须 REFUSED 且 STEP `enabled=0`。
7. 最多 16 轮、每轮约 2 s（总截止 45 s）轮询 `blackbox_status/flash_logstat`：
   - BB 回到空闲、`acc==done`；session 恰好 +1；fail/drop 无新增、last_err=0；
   - used/valid 增加量 = written 增加量 = 新会话帧数；bad 无新增。
8. 用 `flash_dump` 导出新槽（`--start pre_used`），归档判读必须 ACCEPT；主机重建 14 列 CSV 并检查：
   session 全部等于新会话、事件序列为若干 0 后接 9、pre 段至少一条 `step_hz>0`、time_ms 单调。
   sidecar 明确 `cal_source=THEORETICAL`；不把电流字段改成 MEASURED。
9. 只有新日志 PASS 且输出 LOW/禁能时：一次 `fault_reset` → 要求 MANUAL_CLEAR；一次
   `system_selftest` → 要求 required PASS 并 READY；终态 armed0/cur=tgt0/LOW/STEP disabled。
   恢复后不 arm/start/apply。任何一步失败：保持故障态与全部证据，不自动重试。

## 6. 结果登记口径

分项登记，不合并成整机安全 GO：
- `M5_SOFTWARE_EVENT_STOP_PASS`：运行中正式软件事件 → 目标机快保护/锁存/撤能链成立；
- `M5_NEW_FAULT_LOG_PASS`：本次新会话真实落盘并读回，不是旧记录；
- `M5_MANUAL_RECOVERY_PASS`：锁存拒绝、人工恢复不自动起转成立；
- `MONITORING_VALIDITY_DISCLOSED`：SG/ADC/振动依据和未标定边界如实可见；
- `PROJECT_SAFETY_ACCEPTANCE_PENDING`：DIAG 物理触发、真实堵转/负载标定、实测电流、
  物理 PWM 精度/停机时延等仍未关闭。

未进入的步骤记 NOT_REACHED；尝试而未达判据记 FAIL/PARTIAL。无示波器时不给出微秒级停机时延，
主机应答时长不等于物理停机时长；“posted rc=0 / stop=api-accepted”与“操作者看到停转”分别记录。

## 7. 一次批准文本（载荷已填真实值）

> 批准 M5 现场包，载荷为 code_commit `8cf23aa6dda20baf7843798bdef9a02cfd71326f`，
> 候选 BIN 259580 B / SHA-256 `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，
> AXF 1668244 B / SHA-256 `8c5bc7bf1efa786013c767ae1e09415ad0d5d5db3e38069c8aeebabf7cebae40`。
> 允许一次下载、一次定长回读及一次显式启动，按包复用已知 DAP/COM5。先完整导出并核验旧日志，
> 确认两处归档后在空间不足时只清一次 1 MiB 日志区；必要时一次原 IRUN20 参数 apply。
> 允许 DIR0、300 Hz 一次空载起转，约 3 秒投一次软件故障、最迟 5 秒兜底撤能；确认锁存拒绝并完整
> 读回本次新日志，满足条件后一次 fault_reset 和一次 system_selftest，恢复后不起转。
> 不升流、不堵转、不做电气故障注入、不改保护模式，失败不自动重跑。

意思与范围相同即可，不要求逐字复述。

## 8. 未执行声明

本包制作期间没有连接任何目标设备：未打开 COM5、未连接 DAP、未烧录、未回读目标 Flash、
未清日志、未起转、未复位。第 1–5 节命令均在现场获得上述批准后才允许执行。
