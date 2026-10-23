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
bash tests/host/test_path_guard.sh               # 工作目录守卫的沙盒回归(只用新建的一次性假仓库)
bash tests/host/run_io_behavior.sh               # 独立 I/O 行为链(io_main.c + shim_io, 4 变体 138 例)
REVERSE=1 bash tests/host/run_io_behavior.sh     # 该链的 3 项反向注入(只改仓外副本)
bash tests/host/run_safety_data_semantics.sh     # 安全数据语义链(sd_main.c + shim_sd, OFF/ON 两变体)
REVERSE=1 bash tests/host/run_safety_data_semantics.sh   # 该链的 2 项反向注入
bash tests/host/sd_runner_rework_check.sh        # 该 runner 两个入口的返修回归(全在仓外沙盒)
bash tests/host/run_stop_result_s1.sh            # 关断结果可信链(ps_main.c + shim_ps, target/host_ovr)
REVERSE=1 bash tests/host/run_stop_result_s1.sh  # 该链的 2 项反向注入(只改仓外单文件副本)
```

四条链**并列且互不复用夹具**, 因为"真编译哪几个生产模块"不同:

| 链 | 真编译的生产模块 | 桩 |
|---|---|---|
| `build_and_run.sh` (`host_main.c` + `shim/`) | `tmc2209.c` + `motor.c` | 安全 GPIO/线程/状态/STEP 全为桩 |
| `run_io_behavior.sh` (`io_main.c` + `shim_io/`) | `safety_gpio.c` `safety_thread.c` `safety_state.c` `motor.c` | 传感器/存储/黑匣子等为假服务 |
| `run_safety_data_semantics.sh` (`sd_main.c` + `shim_sd/`) | `sensor_service.c` + `blackbox.c` | GPIO/RT 原语/其他传感器/电机/状态/`ns_log`(内存环) |
| `run_stop_result_s1.sh` (`ps_main.c` + `shim_ps/`) | `step_pwm.c` + `motor.c` + `safety_state.c` | RT 原语(单线程确定性)、安全 GPIO/线程、TMC、传感器、存储、黑匣子 |

关断结果链(S1)的三条桩纪律：`rt_pwm_disable` 的返回码可注入，且桩**独立**保存"模拟输出是否
仍在跑"——**disable 失败时桩保留输出运行，不替固件把输出清掉**；许可写结果、许可脚实测电平、
ENN 反馈判定是三个独立注入量；前置"已关断"一律由生产路径自己真实取得，不伪造成功状态过门。
它的斜坡节拍由夹具直接单步调用生产 `motor_ramp_tick()`（`rt_thread_create` 只计次数），
所以是确定性的，也**不**模拟调度竞争与停机时延——那属 S2。判定仍只用生产 API 返回值/状态 +
`ps_sim_*` 计数；仅有的两处文本判据是任务书明文要求的"输出本身就是被测行为"
（`step_status` 不得写 `no(安全)`、统一停机不得写 `STEP stopped`）。
该 runner 不设 `ONLY`/只读校验等可选入口；台账判据与反向判据共用同一个 `validate_ledger()`：
必需 id 出现恰好一次且判 PASS，缺席/重复/未登记/SUMMARY 与实跑行数对不上算基础设施故障，
反向还要求副本构建成功 + 夹具合同退出码 `rc=1` + 台账自洽 + 指定失败身份全部命中。

关断结果链的同轮限定返修（`phase8-stop-result-s1-r1`，tested_commit `8ca4f6a`）：复审补了 4 项
合同遗漏，用例登记随之扩到 `target 48 / host_ovr 64`——新增 `PS12*`（关断未确认时不得改 DIR）、
`PS13*`（`motor_init` 拿不到句柄的出口仍要撤许可、不调 NULL PWM、不留陈旧 `dev_ok`）、
`PS14*`（普通 `motor_disarm`/`pwm_test stop` 失败各投一次 `EVT_SOFT_FAULT` 交给既有链，统一停机与
`force_stop` 绝不投）、`PS15*`（apply 回退只做一次 checked 关断且首错不被后一次成功覆盖）。
夹具为此加两个观测点：`ps_sim_pwm_disable_seq()`（前 N 次 disable 强制失败，默认关闭）与
`ps_sim_pwm_null_calls()`（用 NULL 句柄调 PWM 的次数，必须恒 0）；`PS07c` 的"至少两次 disable"
按修正后的动作合同改为"恰好一次"（次数不是产品要求，业务判据保留并加强）。
R03 用例里"Safety 消费者"是**显式桩**（`safety_thread.c` 不在本链编译，用 `safety_force_shutdown`
代表既有消费者），不冒称新线程端到端测试。

语义链的存储侧只是**内存环模拟** `ns_log_submit/read_slot/stats`, 不重建真实 Flash 驱动;
它的线程不真起(`rt_thread_create` 返回假句柄), `rt_kprintf` 走捕获缓冲, 捕获文本仅用于
"输出本身就是被测行为"的用例(逐路三态显示、旧记录只留 raw), 其余判定一律来自掩码/返回值/计数器。

### 语义链 runner 的入口纪律(2026-10-23 返修, 复审件 §4/§6)

独立复审复现出两个**误报通过**入口，已按下述规则关闭；两条判据都由 runner 自己执行，
回归入口是 `bash tests/host/sd_runner_rework_check.sh`（真实调用同一个 runner，不另写复制判定的自检器）：

- **SD-NEW-01（ONLY 零执行却 PASS）**：`ONLY` 在**任何** `rm` / `mkdir` / 重定向 / 编译之前验证。
  **未设置或 `ONLY=''`(空串)＝默认跑两个变体**（这是默认路径，不是零执行）；**只含空白**的 `ONLY`、
  未知词、合法词夹带未知词、重复词，以及 `ONLY` 与 `REVERSE=1` 的组合，一律 `rc=4` 拒绝且**不动任何文件**。
  汇总只声明**实际执行集合**，未执行的显式写 `NOT_RUN`；只有默认(两变体)选择全部执行并通过，
  才会说"两个变体全部真执行"。
- **SD-NEW-02（异常终止被算成反向成功）**：反向项记 `META-OK` 必须同时满足
  副本构建成功 + 夹具**合同退出码 `rc=1`** + 完整且自洽的适用台账(必需 id 唯一出现、无未登记用例、
  `SUMMARY` 恰好一条且 `cases/pass/fail` 与实际行对齐) + 指定业务失败身份全部命中。
  非 0 非 1 的退出(信号/异常终止，Linux 143、本机实测 3 等)或台账不完整 ⇒ `INFRA-FAIL`(外层 `rc=4`)，
  不得记 `META-OK`；`rc=0` 或缺少期望身份 ⇒ `NOT-DETECTED`(外层 `rc=1`)。
  R1 附带 `SD05c` 失败是当前已知结果，runner 如实登记为"额外失败"但**不改变判定**，判据未被放宽。
- **台账复核入口** `SD_VALIDATE_FILE=<既有输出> SD_VALIDATE_VARIANT=<变体>`：只复核产物台账，
  不编译、不运行、不创建或清理任何目录；用于回归"删一条必需 CASE"和"删 SUMMARY"两类不完整输出。

N3 的先决步骤：脚本会先在仓库外做一次 V1-only 正常构建，取得可用的 `host_v1.exe`
作为"陈旧产物样本"，再把它预置进 N3 输出目录并制造编译失败；准备失败直接以 rc=4 终止，
不会退化成"没构造也算过"。

## 工作目录守卫（F2d-R4）

任何 `rm -rf` / `mkdir` / 输出重定向 / 编译**之前**，三个入口都会把目标路径规范化并验证
（`env.sh` 的 `require_outside_repo`），失败立即非零退出且**不动任何文件**：

- 拒绝：空值（不静默改用默认路径）、文件系统根、仓库根、仓库内任何子目录、以及"包含仓库的
  祖先目录"（清理它会连带把仓库一起删掉）。
- 判定按**目录成分**而非字符串前缀：`<仓库同级>-sibling` 这类同前缀兄弟目录不会被误拒。
- 规范化处理 `.`/`..`、混合分隔符与 `cygpath` 转换；Windows 下大小写不敏感比较。
- 只删自己标过的目录：工作区与各用例输出目录里写有哨兵文件 `.ns800_host_work`，
  目录存在但没有哨兵 => 视为他人现场并拒绝删除。
- 默认输出全部在仓库外：`build_and_run.sh` 的 `OUT_DIR` 默认 `${TMPDIR:-/tmp}/ns800-host-chain/out`
  （早期版本默认落在 `tests/host/_out`，已改）。
- **能力边界如实记录**：本机 MSYS 把目录软链表现为普通目录（`test -L` 为假，`realpath`/`pwd -P`
  都不解析），所以"经由链接绕回仓内"这一条不声称被词法守卫覆盖；对该风险的实际保护是哨兵式删除。
  回归脚本会在能识别符号链接的环境下断言落点解析，否则打印 `[NOTE]` 说明。

`test_path_guard.sh` 只在 `mktemp -d` 新建的一次性沙盒里跑（内含假 git 仓库与标记文件），
断言上述拒绝发生在破坏之前、标记文件零丢失，并验证含空格路径与兄弟目录不被误伤；
绝不拿真实项目目录试删除保护。

## 源码哈希清单的字节语义

`make_source_manifest.sh` 生成的清单针对 **Git blob 原始字节**（对象库内容），不是工作树字节：
本机 `core.autocrlf` 会把文件在签出时改成 CRLF，两者哈希不同（F2d-R3 的清单就因此被远端实校为
10/11）。用法：先 `git add`，再 `--out` 从索引生成；提交并推送后用
`--verify origin/<分支> <清单>` 对远端提交对象逐项复算。若某文件工作树字节与 blob 不同，
清单会在末尾**另列一段带标签**的工作树哈希，仅供本地对照，不作为校验对象。


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
