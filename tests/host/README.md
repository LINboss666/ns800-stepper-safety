# tests/host — 主机验证链（离线，不接触硬件）

在 PC 上编译并执行**真实的** `applications/tmc2209.c` 与 `applications/motor.c`，
用内存假芯片与 RT-Thread API 兼容层跑离线单元测试、负例与元负例。
目标平台的权威构建仍是 Keil Rebuild All（要求 0 Error / 0 Warning）；本目录只证明
**逻辑与状态机行为**，不证明 UART 时序、优先级继承、目标栈余量或物理停机时延。

## 前置与探测

- 需要：Bash 4+、任一 C 编译器（GCC/Clang/MinGW）、Python 3、Git。
- 所有路径从 git 仓库根解析，**不写死盘符**；一切构建产物与隔离快照都落在**仓库外**工作区
  （默认 `${TMPDIR:-/tmp}/ns800-host-chain`）。
- 编译器解析顺序：`GCC=<路径>` → PATH 里的 `gcc`/`cc` → 常见 MinGW 安装位置。
  找不到就报错退出，不会静默跳过测试。

| 环境变量 | 作用 |
|---|---|
| `REPO_ROOT` / `SRC_APP` / `HOST_SRC` / `SHIM_DIR` | 覆盖路径解析 |
| `GCC` | 指定编译器或编译器桩 |
| `OUT_DIR` / `NEG_WORK` / `META_WORK` / `WORK_ROOT` | 输出与工作区（必须在仓库外） |
| `BASE_HEAD` | 负例快照基线（默认当前 HEAD） |
| `RUN_V1` / `RUN_V2` / `V1_EXTRA` / `V2_EXTRA` | 变体开关与编译期注入 |
| `ONLY` | 用例选择：完整名或**无歧义前缀**；未知/零匹配/歧义一律报错非零 |
| `ONLY_META` / `META_DEEP` / `M1_GCC` | 元负例选择、防递归、错误归因场景 |

## 命令

```bash
bash tests/host/build_and_run.sh                 # 正常回归: V1(三宏 FALSE) + V2(仅主机覆盖)
bash tests/host/run_negatives.sh                 # 13 例负例/回归 + 自动调用元负例
bash tests/host/run_meta_negatives.sh            # 只跑元负例(M1..M5 + 对照组)
ONLY="N10b" bash tests/host/run_negatives.sh     # 无歧义前缀, 只跑 1 例
```

N3 的先决步骤：脚本会先在仓库外做一次 V1-only 正常构建，取得可用的 `host_v1.exe`
作为"陈旧产物样本"，再把它预置进 N3 输出目录并制造编译失败；准备失败直接以 rc=4 终止，
不会退化成"没构造也算过"。

## 判据与分级

`run_negatives.sh` 逐例比对**预期矩阵**：V1/V2 各自的 `state/build rc/run rc`、
runner 的**真实进程退出码**、状态文件里的**自报退出码**、以及指定失败身份（ERE）。
三个 rc 分别记录（`runner_proc_rc` / `runner_reported_rc` / `expected_rc`），
自报与真实不一致 = `INFRA-FAIL`（回执失真），期望非零而真实为零 = `NOT-DETECTED`（漏检）。
类别只有三种：`OK` / `NOT-DETECTED` / `INFRA-FAIL`；总链退出码 `0/1/4`。
产物：`verdict_matrix.txt`（人读矩阵）、`case_rc_ledger.txt`（机器可读三 rc）、
每例目录含 `runner_status.txt`、`v?_out.txt`、`v?_executed.marker`、`v?_build.txt`。

`run_meta_negatives.sh` 按**原因**验收元负例，不接受"只要非零就算检出"：

| 用例 | 场景 | 必须成立 |
|---|---|---|
| M1 | 隔离夹具副本把 V2 的 FD7b 判据换成恒真（复现 A） | rc=1 + `N10b` NOT-DETECTED + 原因含 `V2.run:期望=1 实际=0` 与 FD7b 身份缺席 |
| M1c | 同一用例未致盲 | rc=0 + `N10b` OK |
| M2 | 编译器桩让 N1..N10b 全部编译失败（复现 B） | rc=4 + 11 例全部 INFRA-FAIL + 原因含 `runner.proc_rc` 失配与 `COMPILE_FAILED` |
| M2c | 真实编译器同一集合 | rc=0 + 11 例全部 OK + 集合一致 |
| M3 | runner 副本末尾 `exit "$overall"` 改 `exit 0`（F2d-R2-NEW-01） | 非零 + `runner.proc_rc:期望=1 实际=0` + `runner.reported_rc:期望=0 实际=1` |
| M3c | 未篡改 runner 同例 | rc=0 + `rc 真实=1 自报=1 期望=1` |
| M4 | 歧义前缀 / 未知选择 / 无歧义前缀 | 前两者 rc=4 且给出原因且发生在执行前；第三者真跑 1 例且集合一致 |
| M5 | M1 场景被换成必然编译失败的桩（错误归因） | 助手**不得**记 META-OK，必须报 META-LEAK 且自身非零 |

注入统一由 `inject.py` 完成：锚点必须**恰好命中 1 次**，替换处插入 `NEGATIVE-INJECTION`
标记，调用方复查标记与退出码；所有被改副本只存在于仓库外工作区，跑完即弃。

## 仓库规则与本次窄例外

本仓库根 `.gitignore` 采用"默认全忽略 + 白名单放行"（1.3 GB 的 BSP 树只跟踪项目需要部分），
本地另有未跟踪的 `rt-thread-master/rt-thread-master/agents.md`，其中禁止把生成物、日志、
二进制、临时文件入库，并要求不无故新建 Markdown。本次入库依据用户任务书明确要求，
**只在两个路径上开窄例外**，其余忽略规则与隐私禁令保持不变：

- `tests/host/`：手写验证源码与脚本（非生成物）。生成物一律外置：`_out/`、`*.exe`、
  `*.o`、`*.s`、`probe_errcodes.c` 的再生成产物、隔离快照、被篡改副本都不提交。
- `reports/agent_handoffs/`、`reports/reviews/`：本轮审查文本证据（矩阵、真实/自报 rc 台账、
  关键原始日志摘录、验证源码哈希清单）。原件留本地，仓库内只放核查所需的文本；
  未删改任何失败内容，未把失败写成成功。

`.gitignore` 的对应放行改动随本次提交一起可见（`!/tests/`、`!/reports/` 两行 +
针对生成物的再忽略），因此例外本身可被审查，不是口头声称。

## 已知边界（请勿据此推断硬件已通过）

- 单一主机环境结果，不等同跨平台或目标机行为。
- 夹具把两个 `.c` 并入单一翻译单元，可直接调用 static 函数（含确认写入口）；
  这是主机仪器自检通道，不代表目标固件存在公开写入口。
- 主机 `CRITICAL_SECTION` 只对齐全 RT-Thread `rt_mutex` 的"同线程可重入 + 持有计数 +
  非阻塞 try"，优先级继承语义不同。
- 假芯片不严格校验所有请求 CRC。
- 元负例覆盖"检测器致盲 / 根本没执行 / 出口码丢失 / 选择集合失真 / 错误归因"，
  不覆盖判据正则被写宽这类失效。
