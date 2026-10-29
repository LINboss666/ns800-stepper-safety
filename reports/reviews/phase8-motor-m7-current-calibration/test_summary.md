# M7 主机验证台账

全部在 `D:\ns800`（工作树 = code_commit 内容）真实执行。
`WORK_ROOT = C:/Users/LMX/AppData/Local/Temp/ns800-m7-hostchain`（仓库外）。
主机 GCC 与 Keil 结果分列：本文件只记主机链，Keil 见 `builds/`。

## 1. 新标定链（本轮新增）

`bash tests/host/run_current_cal_m7.sh` → rc=0
原文 `host_current_cal.txt`

```
SUMMARY|cases=26 pass=26 fail=0 pre_bad=0
ledger=26/26 bad=0
M7 CURRENT_CAL RESULT: PASS
```

编译的是**真实产品文件**（单一 TU 内 `#include "current_adc.c"` /
`"project_config.c"` / `"current_cal.c"`），不是另写一份校验算法。

| 身份 | 判据 |
|---|---|
| CC01-show_no_side_effects | show 不写 cal/Flash/GPIO/TMC、不 arm |
| CC02-sample_128_normal | 128 点正常统计与 seq/tick 范围 |
| CC03-sample_dup_invalid_skip | 同 seq 重复、fresh=0、valid=0、断流分别计入 |
| CC04-sample_endpoint_rejected | 0/4095 端点仍可记录但不给拟合资格 |
| CC05-sample_no_frames_bounded | 无新帧时在预算内退出，不 FOREVER |
| CC06/07/08 | ISR 上下文 / PRIMASK 窗口 / 无线程 三情形静默拒绝 |
| CC09/10/11 | 非法 N、缺 CONFIRM、错 CONFIRM 词 → usage，不执行 |
| CC12-apply_numeric_reject | NaN/Inf/越域/极小 gain 拒绝且旧元组不变 |
| CC13-apply_static_refused | 静止门拒绝时不提交系数 |
| CC14-apply_success_measured | 一次静止事务内 offset/gain/MEASURED 全部生效，RAM-only |
| CC15-apply_other_cfg_kept | 只改 cal，其它字段逐字段相等 |
| CC16-nominal_theoretical | nominal 只回 1650mV/0.6V/A/THEORETICAL |
| CC17-config_publish_order | set/load/default 与运行时发布不出现晚到覆盖 |
| CC18-save_load_preserves_measured | 来源与系数经 save/load 保留 |
| CC19-load_failure_theoretical | load 失败按既有合同回 THEORETICAL/DEGRADED |
| CC20-cal_snapshot_same_read | raw/mA/source/version 同一次读取同源 |
| CC21-key_lines_ascii_110 | 关键 `[CAL]` 行 ≤110B、无 NUL、最长来源完整 |
| CC22-msh_export_name | 导出名 `current_cal` 存在 |
| CC23-sample_cal_changed | 采样中途系数版本/来源变更 → 报告不可拟合 |
| CC24-sample_256_boundary | N=256 累加边界（u64 累加 + 十进制输出） |
| CC25-nan_inf_range_reject | 具名坏输入逐个拒绝 |
| CC26-api_none_legal_config_rejects | 原 ADC API 保留 NONE；配置结构拒 NONE |

## 2. 集中回归（按实际受影响的输入）

| 链 | 命令 | 结果 | 原文 |
|---|---|---|---|
| S1 停止结果 | `run_stop_result_s1.sh` | rc=0，target 必需 61 全 PASS，host_ovr 必需 78 全 PASS | `host_s1.txt` |
| S2 协调/所有权 | `run_stop_priority_s2.sh` | rc=0，18 组 × 2 变体，用例 109 全 PASS | `host_s2.txt` |
| SD 数据语义 | `run_safety_data_semantics.sh` | rc=0，off_target 22 / on_target 21 全 PASS | `host_safety_data_semantics.txt` |
| M6 CLI 严格分发 | `run_m6_cli.sh` | rc=0，target 10/10、host_ovr 10/10 | `host_m6_cli.txt` |
| I/O 行为 | `run_io_behavior.sh` | rc=0，33/35/46/48 全 PASS + 非法宏值被编译拒绝 | `host_io_behavior.txt` |
| V1/V2 既有回归 | `build_and_run.sh` | rc=0，V1 CHECK_PASS=25 FAIL=0，V2 CHECK_PASS=22 FAIL=0 | `host_v1v2.txt` |
| M7 反向 | `run_m7_reverse.sh` | rc=0，REV1 3/3 具名、REV2 1/1 具名 | `host_reverse.txt`、`reverse_detail.txt` |

新增身份：`PS19a-static_idle_begin_accepted`、`PS19b-static_armed_running_refused`、
`PS19c-static_diag_owner_refused`、`PS19d-static_stop_unknown_refused`、
`PS19e-static_lock_unavailable_refused`（b/c 仅 host_ovr 变体，a/d/e 两变体共有）；
`SD12-cal-source-same-snapshot`。

## 3. NOT_RERUN（未跑及理由）

| 链 | 理由 |
|---|---|
| `run_sp_measure_a0.sh`（A0） | 其共享输入不含本轮改动的四个文件；用户已决定 A0 暂缓 |
| `run_flash_export_m5.sh` | 输入未改（flash_dump/blackbox 路径未动），且 M5 归档判据已冻结 |
| `run_negatives.sh` / `run_meta_negatives.sh` | 元负例针对 runner 选择/台账语义；runner 的 ONLY/台账代码本轮未改 |
| `test_path_guard.sh` | 工作目录守卫未改 |
| S2/HAL/A0 的旧负例整套 | 未重跑整套已冻结负例；只有确实改动其共享输入时才扩展（本轮 S2 因 motor.c 改动**已重跑**） |
| M6 `run_fault` / 300 帧软件故障 | 任务明确禁止在 M7 里"顺手测一下"；M6 候选保持 NOT_BOARD_EXECUTED |

## 4. 计算工具链

`python m7_calcalc.py --selftest` → rc=0，`SUMMARY cases=37 pass=37 fail=0`
原文 `calcalc_selftest.txt`。
首次失败原件 `calcalc_selftest_first_failure.txt`（11 项 FAIL：留出误差界模型把
slope/intercept 的强相关当成杠杆臂放大、`validate()` 只在 `load_input()` 路径生效导致
`compute()` 直调时漏校验、`DUPLICATE_X` 被 `ZeroDivisionError` 抢先、自研"无串口"检查
匹配到了它自己的模式字符串）。这些是**本包工具自身缺陷**，修复后重跑，不冒充一次即通过。

`example_input_HOST_SYNTHETIC.json` / `example_result_HOST_SYNTHETIC.json` 为
`HOST_SYNTHETIC` 合成样例（x=2000→20mA、x=2100→140mA、留出 x=2050→80mA →
slope 1.2 mA/count、gain 0.671551 V/A、offset 1598.2906 mV）。
**这些数字禁止下发实物**；输出恒为 `DRAFT_NOT_APPROVED`。

## 5. 仍未完成（不在主机验证里冒充关闭）

- `target_calibration_cases = NOT_EXECUTED`；板上 ADC 实测未完成。
- 真实 DIAG/SG 触发、故障态 `motor_arm` 拒绝的实机补漏、示波器/A0：开放或 DEFERRED。
- 合成系数不是 `CALIBRATED_ON_BOARD`。
