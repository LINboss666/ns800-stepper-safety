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
bash tests/host/run_stop_priority_s2.sh          # 停机优先 S2 链: 端口层二进制(13 组)+ 真实 HAL 叶子二进制(4 组)
REVERSE=1 bash tests/host/run_stop_priority_s2.sh # 该链的 5 项反向家族(仓外多文件副本)
bash tests/host/run_flash_export_m5.sh           # M5 flash_dump 导出边界链: 正常 + 容量探针 + 归档判读门
REVERSE=1 bash tests/host/run_flash_export_m5.sh # 追加"退回受限长 rt_kprintf"反向与截断批次拒绝
```

四条链**并列且互不复用夹具**, 因为"真编译哪几个生产模块"不同:

| 链 | 真编译的生产模块 | 桩 |
|---|---|---|
| `build_and_run.sh` (`host_main.c` + `shim/`) | `tmc2209.c` + `motor.c` | 安全 GPIO/线程/状态/STEP 全为桩 |
| `run_io_behavior.sh` (`io_main.c` + `shim_io/`) | `safety_gpio.c` `safety_thread.c` `safety_state.c` `motor.c` | 传感器/存储/黑匣子等为假服务 |
| `run_safety_data_semantics.sh` (`sd_main.c` + `shim_sd/`) | `sensor_service.c` + `blackbox.c` | GPIO/RT 原语/其他传感器/电机/状态/`ns_log`(内存环) |
| `run_stop_result_s1.sh` (`ps_main.c` + `shim_ps/`) | `step_pwm.c` + `motor.c` + `safety_state.c` | RT 原语(单线程确定性)、安全 GPIO/线程、TMC、传感器、存储、黑匣子 |
| `run_stop_priority_s2.sh` 第 1 个二进制 (`sp_conc_main.c` + `shim_ps/`) | `stop_priority.c` `step_pwm.c` `motor.c` `safety_state.c` | TMC/EPWM/UART 三段是**端口桩**(C01–C21 测应用与协调层) |
| `run_stop_priority_s2.sh` 第 2 个二进制 (`leaf_main.c` + `shim_leaf/`) | `stop_priority.c` `tmc2209.c` + 真实叶子 `drv_uart_txleaf.inc` `drv_epwm_leaf.inc` | 应用层状态(motor 快照/安全状态/许可脚)是受控输入; 寄存器与时间用模型 |
| `run_flash_export_m5.sh` (`flash_main.c` + `shim_flash/`) | `ns_flash_shell.c` 的 `flash_dump` | `ns_flash_*`/`ns_storage_*`/`ns_log_*` 为可注入槽模拟; RT 控制台按 128 B 截断语义建模 |

M5 导出链只证明 **CSV 格式、参数/错误边界与归档判读门**, 不证明真实 Flash 页布局、CRC、
刷新时延或串口传输完整性; 归档判读器 (`m5_log_archive_check.py`) 只承认 14 列全部由生产
`ns_log_read_slot` 读出且逐槽恰好一次的记录, 任何 NUL/截断/缺槽/重复/越界/读错都会输出
`CLEAR_ALLOWED=NO`。正常构建 5 例、容量探针 1 例、判读器自测 19 例; 反向要求仅 `FE02`
具名失败并让截断批次被归档门拒绝。

CP5 §5 的分工写在这里是因为两者**判据不同**: 端口链观察"生产应用有没有按协议调用", 叶子链观察
"生产实现自己写进去的寄存器字节/次数、真 TDRE 等待环、真 cfg/xfer 锁与真 IFCNT"。叶子链的
`shim_leaf/drv_common.h` 每条声明都注了本地 SDK 原文出处并用 `_Static_assert` 钉住寄存器偏移;
它给出的是**寄存器模型 + 受控时间**的结论, 不是目标机最坏时延或真实波形(BUG-008 仍开放)。

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

**两轮执行的归属**（不要读成"每一轮都全量重跑"）：首轮 `62873bd` 实际重跑 V1/V2、I/O 138 例、
13 例负例 + 元负例 M1..M5、SD 41 例；返修轮 `8ca4f6a` 只重跑 S1 链（`target 48` / `host_ovr 64` +
两项既有反向）、V1/V2、I/O 138 例与 Keil，其余按任务书 §6.4.3 **沿用**首轮证据（`host_main.c` /
`io_main.c` / `env.sh` / 旧 runner / `inject.py` 该轮零改动），沿用不等于重跑。

**两条证据口径**（复审件 §5 于同日登记，夹具未改）：`PS12c` 打印的 `pad=` 取自引脚表 level，且夹具
只设置了许可错误码、**未**置 `pad_stuck`，所以那一行 `pad=0` **不构成**"许可焊盘实际保持 HIGH"这一
前提的证明 —— 该前提由复审端独立探针显式置位并读 `pad_level` 取得；以后复用该场景要同步完善前提与
观测。修前对照的真实总数是 `target fail=4 / host_ovr fail=17`（文档里选摘的身份条目数不是总数；反向
的 19/22 同理是各变体**总 FAIL 数**，含已命中的指定身份），且修前 host 的 `pre_bad=21` 说明串行前置
会挡住后面的用例，故 `PS15a` 修前那一行不能单独当作 R04 的独立复现。

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
- **屏蔽状态的建模粒度（CP6 更正）**：V1/V2 链（`host_main.c`）的 PRIMASK 屏蔽位与斜坡线程嵌套深度
  是**每线程**（`__thread`）的 —— 该链由生产 `motor_init()` 起了一条真线程，若用进程级全局位，
  一个线程进自己的短临界窗口会让另一个线程被生产 `tmc_ctx_ok_for_wait()` 误判为"不可等待上下文"
  并**静默**拒绝（日志按设计不打印），实测会污染 FD7/FD8 这类判据。跨线程的"窗口独占"（目标上由
  PRIMASK 保证不会被调度）**只在 S2 并发链建模**（全局互斥量 + 线程本地标志），V1/V2 链不建模。
- **并发观察一律事件会合**：凡断言"另一条线程此刻处于某状态"（在门外等锁、停在某接缝、字段已落位
  而通知未交付），都由被观察方在观察点置事件并 broadcast（`s2_contend_wait_above()`、`leaf_pk_*`、
  `leaf_hk_*`），墙钟上限只作死锁守卫；不用有限 `usleep` 自旋猜时间，到期如实判"没观察到"。
- S2 链的状态：CP6 定稿 `bcdec631…` 已获独立复审 **`SOFTWARE_REVIEW_GO`**（限定软件与主机验证合同）。
  真实叶子四组的 EPWM 数值与 cfg/xfer 叶锁时间预算都是**寄存器模型 + 受控时钟**的结论，
  不覆盖目标 RT-Thread 调度、PWM 物理波形与最坏停机时延（`BUG-008`、`RISK-03/03a` 仍开放）。
