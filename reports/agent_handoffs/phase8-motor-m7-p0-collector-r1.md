# NS800 M7 P0-R1 交接：P0 采集器补正（离线，未执行）

任务 `phase8-motor-m7-p0-collector-r1`，日期 2026-10-30。
复审对象 `208bf102e6b3bce7159eec672b83a0d6b77f8811`；输入包
`NS800_M7_R2_Calculator_GO_P0_Collector_Fix_20261030.zip`（166113 B /
`3ee2c9005e99b5485ed15d66d82db527eef50e265ca0ca3c193b905d9a5ba686`）。
分支仍是 `debug/tmc-uart-rootcause-20261028`，未 reset/rebase/merge/force。

## 0. 判定登记

- 计算器：接受独立复审结论 **`M7_R2_CALCULATOR_REVIEW_GO`**（accepted=208bf10），
  R3 的来源完整性与区间界两项已闭合；本轮不重跑、不再改 `m7_calcalc.py`。
- 原 P0 采集器 `1e34589f…c44f`：**`P0_COLLECTOR_REWORK_REQUIRED`** 成立——六项缺陷我在这台
  Windows 交付机上用包内探针原样复现（§2）。
- 修正候选已同字节应用并在本机 37/37 通过；P0 整包进入
  **`READY_FOR_SINGLE_USER_APPROVAL / NOT_BOARD_EXECUTED`**。
- 固件、六件镜像、R1a/R1b/R2 产品修正、M6/M5/日志成果：维持已接受，本轮零漂移、未重编。
- 硬件：**本轮没有连接任何设备**，本文不是现场批准。P1 `METHOD_NOT_READY`，P2 `NOT_AUTHORIZED`。

## 1. 角色提交（三种身份分开，不互相冒充）

| 角色 | 值 |
|---|---|
| `firmware_code_commit` | `5b564eb962e8518920f581111d7a460a2b1fce64`（六件镜像的实际构建输入，本轮未重编） |
| `reviewed_delivery` | `208bf102e6b3bce7159eec672b83a0d6b77f8811` |
| `script_code_commit` | 见交接 JSON：本轮脚本/计划提交（不自引用证据提交） |
| `delivery_head` | 证据提交，用 `git log -1 --format=%H -- reports/reviews/phase8-motor-m7-p0-collector-r1/remote_readback_r1.md` 解析 |

## 2. 修前基线：六项缺陷在本机复现，且与云端记录逐字节相同

`probe_original_collector.py`（2558 B / `00ef4e91…30d4`，包内原样，只构造内存假端口，
不导入 pyserial、不创建设备对象）对 `208bf10` 的采集器字节运行：rc=0，
`BASELINE_OBSERVATIONS=6/6`。

| # | 观察 | 本机实测 |
|---|---|---|
| 1 | 只有回显 `msh >safety_state\r\n` 也算响应 | `ECHO_ONLY_ACCEPTED_AS_RESPONSE accepted=true` |
| 2 | 六条垃圾响应也能 COMPLETE | `ALL_GARBAGE_RESPONSES_ACCEPTED rc=0`，6 次发送 |
| 3 | 首条含 `stack is close` 仍发满六条才停 | `writes` 长度 6，之后才 STOP |
| 4 | 启动横幅同块含告警仍返回 0 并发命令 | `BOOT_DANGER_IGNORED rc=0` |
| 5 | 读异常时已收 20 B 不落盘 | `bytes_returned=20 raw_file_exists=false` |
| 6 | `write()` 返回 0 仍继续 | `ZERO_BYTE_WRITE_RETURN_IGNORED accepted=true` |

本机 stdout 是 Windows 重定向件（56 CR / 1276 B）；去掉 CR 后与云端
`collector_baseline_probes.txt`（`6a693f7f…f14e`）**逐字节相等**。

## 3. 应用与身份：一次合并补丁，字节分毫不差

`p0_collector_r1_combined.patch` = 74411 B / `ff7a03e9…c4d9`（LF-only，3 文件整文件替换）。
仓外规范 LF 字节上 `git -c core.autocrlf=false apply --check` 一次通过，再 apply，未叠加"完整文件覆盖"：

| 文件 | 结果（= 任务书声明 = 包内完整文件） |
|---|---|
| `p0_voltage_observation.py` | 19642 B / `a2c75ea83cd3842246397a6eff33eea52745c69f8926fb6800a2561fe36cb95a` |
| `verify_p0_candidate.py` | 16343 B / `bc438094d4193d31fba710622c13d41d3fa19208658dec4b4015f11b444769a2` |
| `p0_package.md` | 9345 B / `dd4924b75e1bd70c13fca8198f1628a390a15534605e6079ef41a647a866e26b` |

索引 blob 与上述一致（CR=0）；`core.autocrlf` 仍 `true` 且未改任何配置。全过程见
`patch_application_log.txt`。

## 4. 本机实际执行

- `p0_voltage_observation.py --selftest`（输入取自**索引 blob**）→ `RC=0`、stderr 0 字节、
  `SUMMARY cases=37 pass=37 fail=0`、`DEVICE_ACTIONS=0 / NO_REAL_TRANSPORT`。
- `python -m pyocd --version` = 0.45.1；`python -m pyocd reset --help` rc=0（不为查参数连设备）
  → `--method sysresetreq`、`--connect attach`、`--core 0`、`--no-config`、`--no-wait` 全部存在。
- 没跑 Keil；没重跑未变化的 54+21 计算器链、S1/S2、I/O、SD、V1/V2；产品码/工程/镜像零改动。

## 5. 我另外做的两项独立核对（不是复述云端 PASS）

1. **真实线上字节重放**：云端四份 M5 重放输入里 `016/018/019` 是去 CR 的 LF 转写件
   （68/634/227 B），只有 `017` 与原归档相同。我改用仓库
   `phase8-motor-m5-fault-log/cp1/final_pass/` 的**原字节**（71/488/650/233 B，CR=3/7/16/6，
   017 含 1 个 NUL 粘连）直接喂新校验器：`VERBATIM_M5_REPLAY=4/4 accept`，
   四份的响应前缀与帧尾提示符都在场。见 `m5_verbatim_replay.txt`、`replay_verbatim_m5.py`。
2. **BIN 时间戳偏移**：全文扫描交付 BIN，`Oct 29 2026` 唯一命中 260674、`23:11:52` 唯一命中 260665；
   旧计划的 259925/259916 处是 `b' \n\x00[INFO] N'` / `b'number:%'`。新计划的修正是真的。见
   `bin_timestamp_offset_check.txt`。仍是归档字节恢复，不是板上实读。

顺带登记一条停止语义（我不改候选逻辑）：`cc_show` 的 `ma_uA` 有数值与 `N/A (no valid fresh frame)`
两个分支，新校验器只收数值分支并要求 `valid=1 fresh=1`。若 show 时刻没有 valid+fresh 帧，
采集器会停在第 5 条发送上，原始响应已先落盘、不重试、额度照耗。这是有意的 fail-closed 前置，
现场不应把它读成串口故障。板上实际走哪个分支 `NOT_MEASURED`。分析见 `plan_diff_notes.md` §2b。

## 6. 计划差异

新版把旧的"裸 `pyocd reset`"换成明确方法+已知 UID 的一次复位，补齐七次发送/两次 show/四个 DMM
握手/写返回值合同/fsync-先落盘/126 字节非权威行精确分界等。逐条在场性核对结论是
**没有削弱任何既有条款**，详见 `plan_diff_notes.md` §1 的对照表。

## 7. 未闭合（不自我关闭）

- P0 仍是 `READY_FOR_SINGLE_USER_APPROVAL`，需用户对该整包**一次**批准；本任务不含现场批准。
- 板上最后已证镜像仍是 M5 的 `01d3e5ab…`；M7-R1 motor 镜像从未在本轮被读过或写入过。
- 横幅 `5.3.0 build Oct 29 2026 23:11:52` 是归档 BIN 的字节恢复 + M5 先例的方法可信度，不是板上实读。
- `VOLTAGE_OBSERVATION_ONLY` 之外的一切（实际 VDDA/ADC 误差、独立母线参考、真实 DIAG/SG 端到端、
  最坏停机时延、整机安全验收、相电流 RMS）保持开放。
- P1 仍 `METHOD_NOT_READY`；`>2` 拟合点仍 `UNSUPPORTED_BOUND_MODEL`；共用会话文件不自动分段。
- 六族反向维持 AGENT_LOCAL 记录与云端 REV3 `rc=4` 并列，原因未定。
- 旧 P0 的 11 项自检原文（`p0_collector_selftest.txt`）保留为历史，未被 37 项覆盖或删除。

## 8. 回执

```text
M7_R2_CALCULATOR_REVIEW_GO(accepted=208bf10)
P0_COLLECTOR_EXACT_CANDIDATE_APPLIED / WINDOWS_SELFTEST_37_PASS / REMOTE_BYTES_MATCH
P0_PACKAGE_READY_FOR_SINGLE_USER_APPROVAL / NOT_BOARD_EXECUTED
FIRMWARE_IMAGES_UNCHANGED / NO_KEIL_REBUILD / HARDWARE_ACTIONS=[]
P1_METHOD_NOT_READY / P2_NOT_AUTHORIZED / M5_BOARD_IMAGE_UNCHANGED
```

第三行里的 `REMOTE_BYTES_MATCH` 只在推送后的远端 blob 直取成立时才登记，见
`remote_readback_r1.md`；下一轮不做任何设备动作，等用户对 P0 整包的一次批准。

## 9. 远端回读已成立（只追加）

`REMOTE_BYTES_MATCH` 已由 `reports/reviews/phase8-motor-m7-p0-collector-r1/remote_readback_r1.md` 证实：
远端 tip = `699bbf4be80653fc3aed68d478a777efbb01232b`；采集器/验证器/计划的远端 blob 分别 =
`a2c75ea8…b95a`(19642 B) / `bc438094…69a2`(16343 B) / `dd4924b7…e26b`(9345 B)，CR 均为 0；
16 个交付路径逐一实测，`reports/firmware/`、`rt-thread-master/`、`tests/` 在本区间变更路径数 = 0，
六件镜像与 M5 归档 BIN 不变，四项冻结引用不变。
用远端字节重跑：`--selftest` 37/37（rc=0、stderr 0 字节）、真实 M5 原字节重放 4/4 accept。
`roles.script_code_commit` 已填 `699bbf4…`；`delivery_head` 仍用解析规则，不自引用。
五行测点原文不改，登记顺序为：脚本提交 `699bbf4` → 本证据提交（回读件所在）。
