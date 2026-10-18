# Phase 8-F2d-R3 交接记录（Agent → 独立复审）

日期：2026-10-18（北京时间）。分支 `phase8/tmc-drive-config`。
基线 `de93b197a9381813eab9011b88b529f4f4dcdc16` → 本轮最终提交号只出现在推送后的交付消息里：本报告在提交之前生成，无法也不应自引用自己的最终 SHA。
交付方式：**GitHub 直接审查，本轮起不再生成 ZIP**（用户 2026-10-18 要求）。

范围：只做 R2 复审 §4（R2-NEW-01/P1）与 §5（R2-NEW-02、R2-NEW-03/P2）+ 交付目录迁移。
生产 `applications/` **0 改动**；三道硬件宏保持 `RT_FALSE`；未烧录、未复位、未操作 COM5、
未向真实 TMC2209 发写帧、未接电机、未输出 STEP、未改安全门禁与 Keil 目标配置。

## 三条修复

1. **R2-NEW-01 出口对账**：`run_negatives.sh` 原来保存 `outer=$?` 之后不用它，判定与显示的 outer
   全取自状态文件的自报 `RUNNER_RC`。现在真实进程退出码 / 自报退出码 / 预期退出码 **三个值分别记录**
   （矩阵行 `rc 真实=… 自报=… 期望=…`，机器台账 `case_rc_ledger.txt` 的
   `runner_proc_rc / runner_reported_rc / expected_rc`），并核对两件事：
   `proc_rc == expected` 与 `reported_rc == proc_rc`；自报≠真实一律 `INFRA-FAIL`，
   期望非零而真实为 0 记 `NOT-DETECTED`，总链非零。常驻回归 **M3** 把隔离 runner 副本末尾
   `exit "$overall"` 改成 `exit 0`（状态文件照旧自报 1），必须被拒绝；未篡改对照必须 rc=0。
2. **R2-NEW-02 选择入口**：`ONLY` 统一为"完整名或无歧义前缀"；未知、零匹配、歧义前缀一律 rc=4
   并列出候选/登记表；收尾核对"请求集合 == 实际执行集合"且非空（登记用例身份，不硬编码 PASS 总数）。
   常驻回归 **M4**：`ONLY=N1`（歧义，命中 5 个）、`ONLY=ZZZ_no_such_case`（未知）、
   `ONLY=N10b`（无歧义前缀，必须真跑 1 例且集合一致）。
3. **R2-NEW-03 元负例按原因验收**：助手不再只看 rc 非零，而是核对 rc、用例身份、失败类别、
   原因文本、集合一致性；缺矩阵/空矩阵/意外编译失败一律不得记 META-OK。常驻回归 **M5** 把 M1 的
   致盲场景换成必然编译失败（错误归因），助手必须报 `META-LEAK` 并整体非零。

## 迁移与规则窄例外

`tests/host/`：`env.sh`（仓库根与编译器探测，不写死盘符）、`build_and_run.sh`、`run_negatives.sh`、
`run_meta_negatives.sh`、`inject.py`（注入锚点唯一实现，命中必须恰为 1 次）、`host_main.c`、`shim/*`、
`probe_errcodes.py`/`.c`、`README.md`。构建产物、隔离快照、被篡改副本一律在**仓库外**工作区，
脚本内有断言守卫（工作区落在仓库内直接 rc=4 终止）。
连续性：`host_main.c`、两个 shim、`probe_errcodes.c` 与 F2d-R2 交付副本逐字节相同（`cmp` 验证）。

`.gitignore` 原来**未被跟踪**，因此本轮把它纳入跟踪，使忽略策略本身可被审查；新增三行窄例外
`!/.gitignore`、`!/tests/`、`!/reports/`，并加 `tests/host/.gitignore` 屏蔽 `_out/`、`*.exe`、`*.o`、`*.s`。
其余"默认全忽略"、`build/`、`*.log`、隐私相关禁令保持不变，没有放开日志目录。

## 命令与主机环境

```bash
bash tests/host/build_and_run.sh              # 正常回归 V1/V2
bash tests/host/run_negatives.sh              # 13 例负例/回归 + 自动调用元负例
bash tests/host/run_meta_negatives.sh         # 只跑元负例
BASE_HEAD=de93b197a9381813eab9011b88b529f4f4dcdc16 bash tests/host/run_negatives.sh   # 本轮取证用的固定基线
```

环境：Git Bash 5.2.37（MINGW64_NT-10.0-26100）、主机编译器
`C:\Program Files (x86)\Dev-Cpp\MinGW64\bin\gcc.exe`（tdm64-1 4.9.2，不在 PATH，由脚本探测）、
Python 3.12.5、git 2.49.0。本轮为单一 Windows 主机环境，不是跨平台复跑，也不是真机。

## 执行结果（真实执行，非"仅编译"）

| 项 | 实际 |
|---|---|
| 正常 V1（三宏 FALSE） | HOST TEST SUMMARY: CHECK_PASS=25 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0 |
| 正常 V2（仅主机覆盖） | HOST TEST SUMMARY: CHECK_PASS=22 CHECK_FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0 |
| 负例+回归 | 行数 13：OK=13 NOT-DETECTED=0 INFRA-FAIL=0 |
| rc 台账样例 | `N1_only_V1_injected_fail	class=OK	runner_proc_rc=1	runner_reported_rc=1	expected_rc=1	V1=RAN/0/1 V2=RAN/0/0` |

元负例（按原因）：

| 组 | 判定 |
|---|---|
| M1_blinded_fd7b | META-OK |
| M1_control_unblinded | CONTROL-OK |
| M2_stubbed_compile | META-OK |
| M2_control_real_compiler | CONTROL-OK |
| M3_runner_exit_zero_lost | META-OK |
| M3_control_unmodified | CONTROL-OK |
| M4_selection_semantics | META-OK |
| M5_wrong_reason_guard | META-OK |

- M1 原因行：`NOT-DETECTED | N10b_writeback_both_variants | rc 真实=1 自报=1 期望=1 | V1=RAN/0/1 V2=RAN/0/0 (期望 V1=RAN/0/1 V2=RAN/0/1)  V2.run:期望=1 实际=0 缺席身份: V2[\[FAIL\] FD7b 已提交快照后再 probe: 写帧增量 1\(应 0\)]`
- M3 原因行：`INFRA-FAIL | N1_only_V1_injected_fail | rc 真实=0 自报=1 期望=1 | V1=RAN/0/1 V2=RAN/0/0 (期望 V1=RAN/0/1 V2=RAN/0/0)  runner.proc_rc:期望=1 实际=0  runner.reported_rc:期望=0 实际=1`
- M2 的 INFRA-FAIL 行数：11（编译器桩让 N1..N10b 全部没执行）
- M4 三条：`FATAL: ONLY 里的选择 "N1" 歧义, 命中 5 个: N1_only_V1_injected_fail N10_probe_writeback_V2 N10b_writeback_both_variants N11_clean_snapshot_regression N12_works` ／ `FATAL: ONLY 里的选择 "ZZZ_no_such_case" 不匹配任何用例(已登记: N1_only_V1_injected_fail N2_only_V2_injected_fail N3_V1_stale_exe_not_reused N4_fw_calc_selftest_FAIL` ／ `执行集合(1) 与请求集合(1) 一致: 是`
- M5 嵌套助手输出：`  => META-LEAK | M1_blinded_fd7b (不符 3 项)`

Keil：沿用 F2d-R2 原始日志(build_f2d_r2_final.log), 本轮未重建; 固件源码 0 改动。程序尺寸 `Code=144082 RO-data=60130 RW-data=2204 ZI-data=83400`（131 C + 2 asm）。

## 失败与偏差（不掩盖）

1. `env.sh` 的 `env_work_root` 写成 `cd && pwd -W || cd && pwd`：结合顺序让两条 `pwd` 都执行，
   工作区路径里带进换行 → 快照目录指向不存在 → 第一次全链里 N1/N2 双双 `COMPILE_FAILED`、
   N3 直接 `FATAL` 终止。链表现为失败（没有把失败说成通过），但根因是我方脚本缺陷，已改为单一赋值。
2. 元负例的**集合对账**把"组键"和"显示名"混用：8 组逐组都判 `META-OK`/`CONTROL-OK`，但收尾拿
   请求集合（`M1/M1c/…`）与实际执行组（显示名 `M1_blinded_fd7b/…`）比对，于是全部算"未执行"、
   总行 rc=1。由真实运行检出（表现为链拒绝，而不是链放行），已用 `CUR_GROUP` 显式记录组键。
   这条恰好说明"按原因验收"必须把集合一致性也纳入判定。
3. `set -u` 下空数组未初始化：`run_negatives.sh` 的 `NOT_RAN` 曾在命令替换里报 `unbound variable`
   （只影响汇总行显示，不影响判定），`run_meta_negatives.sh` 的 `TABLE/CHECK_NOTES/REQUESTED_META/
   META_NOT_RAN` 同类风险，现全部显式 `=()` 初始化。
4. `run_meta_negatives.sh` 的 `need_all_class` 参数顺序 bug（`shift` 之后仍引用 `$2`）让 M2 段中断、
   整条元负例链 rc=1；已改为 `<用例列表> <期望类别>` 两个具名参数。
5. 上一轮记录过的三个通用陷阱仍然有效：patch 回环的临时目录必须在仓库外；`core.autocrlf` 会造成
   假失配；Windows `tar` 解不开中文名归档。
6. 本轮未重建 Keil（固件源码 0 改动），沿用 R2 原始日志，并在 `reports/reviews/phase8-f2d-r3/`
   的文件名与正文里显式标注"沿用"。
7. `.gitignore` 此前是**未被跟踪但生效**的本地文件；本轮把它纳入跟踪，所以提交里看到的是整份文件的
   新增，相对原本的本地内容实际只多了三行放行（`!/.gitignore`、`!/tests/`、`!/reports/`）再加一份
   `tests/host/.gitignore`。

## 未验证 / 明确不做

真机全部 NOT TESTED（UART 时序与超时、优先级继承、motor 线程栈高水位、守卫物理停机时延上界、
`rt_pwm_disable` 返回值、ENN 端电压、STEP 波形、真实寄存器读写）。RISK-03 / RISK-03a / RISK-04
继续开放。本轮不新增受控写握手，不扩大到 F2e/F3，不修改真实硬件行为。

## 证据位置（仓库内）

- `reports/reviews/phase8-f2d-r3/`：逐例矩阵、三个 rc 的机器台账、每例回执与身份行、
  元负例子链原因行、正常回归摘要、Keil 沿用日志与依赖摘录、`verification_source_hashes.txt`
  （把日志绑定到具体脚本版本）。
- `reports/agent_handoffs/phase8-f2d-r3.md` / `.json`：本文件与机器可读副本。
- 原始完整日志与全部被篡改副本留在仓库外工作区，不入库。

## 下一步

等待 ChatGPT Work 独立复审；通过前不进 F2e/F3。上板取证与三道宏翻转属另一次授权。
