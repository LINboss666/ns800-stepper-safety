# M7-R1 主机验证台账（本机实际执行）

日期：2026-10-29。对象：`phase8-motor-m7-current-calibration-r1` 的产品与夹具最终字节。
工具：MinGW-w64 `gcc.exe (tdm64-1) 4.9.2` + 本机 Python（离线，无任何设备对象）。
原始日志逐字节归档在 `host_runs/`，身份见 `host_runs/identity.json`。

## 1. 本轮真实执行的链

| 链 | 入口 | 结果（本机原文摘一行） | 日志 |
|---|---|---|---|
| current_cal | `run_current_cal_m7.sh` | `SUMMARY cases=34 pass=34 fail=0 pre_bad=0` → `RESULT: PASS` | `host_runs/current_cal.log` |
| S1 停机结果 | `run_stop_result_s1.sh` | target 61/61、host_ovr 78/78，两组台账自洽 → `PASS` | `host_runs/s1.log` |
| S2 并发/身份 | `run_stop_priority_s2.sh` | 19 组；target 53、host_ovr **118**，全 PASS，`pre_bad=0` → `PASS` | `host_runs/s2.log` |
| M6 CLI | `run_m6_cli.sh` | 两变体各 10/10 → `PASS` | `host_runs/m6_cli.log` |
| I/O 行为 | `run_io_behavior.sh` | 4 变体必需用例全真执行，非法宏值被编译拒绝 → `PASS` | `host_runs/io.log` |
| SD 数据语义 | `run_safety_data_semantics.sh` | 两变体必需用例全真执行 → `PASS` | `host_runs/sd.log` |
| V1/V2 既有回归 | `build_and_run.sh` | `CHECK_PASS=25 FAIL=0` 与 `CHECK_PASS=22 FAIL=0` → `PASS` | `host_runs/v1v2.log` |
| calcalc 自检 | `m7_calcalc.py --selftest` | `SUMMARY cases=54 pass=54 fail=0` → `PASS` | `host_runs/calcalc_selftest.log` |

S2 用例总数从上一轮的 116 增加到 **118**：新增 `C22f-qualification-and-reservation-are-one-hold`
与 `C22f2-probe-not-vacuous`（`concurrency_contract.md` §3/§4）。target 变体仍是 53 例 +
`cal_static` 整族 1 行按声明 SKIP（无 STEP 输出许可，预约路径不可达），没有拿"门永远拒绝"
冒充验证。

## 2. 六族局部反向（`run_m7_reverse.sh`，全部在仓外源码副本上突变）

```text
REV1 s1_rc=1 hits=3/3          静止检查被绕过            -> PS19b/PS19c/PS19d 具名 FAIL
REV2 cc_rc=1 hits=1/1          来源已变、系数仍旧        -> CC14-apply_success_measured FAIL
REV3 s2_rc=1 hits=1/1          gate+预约回到锁外         -> C22f 具名 FAIL（首轮 NOT DETECTED，见 §3）
REV4 cc4_rc=1 hits=2/2         µA 导出重新挤过 int32     -> CC28/CC29 FAIL
REV5 calcalc_rc=1 hits=3/3     仪表限重新 RSS + CLI 永远 0 -> M7C35/M7C50/M7C51 FAIL
REV6 cc6_rc=1 hits=2/2         cfg 取锁失败重新被吞      -> CC27/CC27b FAIL
M7 REVERSE RESULT: PASS (all six named failure families detected)
```

每条都要求"注入真落地 + 副本编译成功 + 业务 rc≠0 + 逐组台账自洽 + 预登记失败身份命中"，
崩溃/超时/编译失败/台账不完整一律 INFRA-FAIL，不算检出。

## 3. 首轮 REV3 未被检出（原文保留，这是本轮最有价值的一条负面结果）

```text
REV1 s1_rc=1 hits=3/3
REV2 cc_rc=1 hits=1/1
REV3 s2_rc=0 hits=0/2
  REV3 NOT DETECTED
REV4 cc4_rc=1 hits=2/2
REV5 calcalc_rc=1 hits=3/3
OUT_DIR=/tmp/ns800-m7r1-hostchain/m7-reverse/20261029T142857Z-1679
M7 REVERSE RESULT: FAIL
```

根因是用例判据不足，不是脚本 bug：修前的诊断线程只是**碰巧**卡在
`motor_diag_gate_check() → motor_get_snapshot()` 内部那次 FOREVER 取锁上，
所以"别人持锁时预约出不来"（C22b/C22c）在修前修后同样成立。补 C22f 之后
同一突变被具名检出，失败原文：

```text
CASE|C22f-qualification-and-reservation-are-one-hold|FAIL|grant=0 认领=1 owner=2 预约时 mot_lock 层数=0 - 检查还锁之后才预约就是修前那个窗口(期望层数>=1, 即仍在 mot_lock 持有期内)
```

REV6 也是同一类校准：`CC27c`（未取锁不得 release）在 v1 形状下**仍然绿**，
因为旧 `cfg_take()` 在 `cfg_lock_ok=FALSE` 时本身就是空操作，既不会取也不会放。
所以 REV6 的具名检出只登记 CC27 与 CC27b，CC27c 的"没检出"如实写成不具区分力，
不当作命中，也不当作漏检掩饰。

## 4. 本轮不是本机执行的（来源分别标注）

| 项 | 状态 | 来源 |
|---|---|---|
| UBSan / `float-cast-overflow` | **未在本机执行**：GCC 4.9.2 无 `libubsan`、不支持该 sanitizer | 复审 §3 云端原文（`current_cal.c:74:41: runtime error: 3.29919e+09 ...`） |
| Keil 三目标 0 Error / 0 Warning | 本机执行（见 `builds_r1/`） | 本轮构建日志 |
| 板上启动横幅实读 | **未执行**：本轮无设备接入 | 无 |
| 云端 `probes/reproduce.py` | 本机照跑在锚点处按要求中止；显式适配后复现见 `probes_r1.md` | 本机 + 复审包 |

## 5. 证据文件的双重身份（实测，不是推断）

本机 `core.autocrlf = true`（`git config core.autocrlf` 实读），且这些路径
`git check-attr text` 报 `unspecified`，所以提交时每个 CR 都会被去掉。逐文件实测后，
`host_runs/` 里 **8 个**文件的工作树字节 ≠ 预期 blob 字节（含 CR），其余为纯 LF 两处一致：

```text
normalized_by_autocrlf = calcalc_selftest.log, current_cal.log, v1v2.log,
                         probe_calcalc_postfix_rows.txt, probe_cfg_mutex_postfix.txt,
                         probe_diag_threads_postfix.txt, probe_show_overflow_postfix.txt,
                         reverse_cc6_detail.txt
```

这 8 个的 `lossless` 记 **false**（对"工作树原字节"这个说法而言），转换规则可以直接断言：
`blob == 工作树字节.replace(b"\r\n", b"\n")`。两个身份（worktree + expected_blob，含 bytes
与 sha256）都写进 `host_runs/identity.json`；该文件不哈希自身字节。推送后一律按**远端 blob**
复算，不拿工作树字节当远端身份（见 `remote_readback_r1.md`）。
这些含 CR 的字节是 Windows 端 stdout 的原样，**不**为了字节一致而重写或重造首轮输出。
