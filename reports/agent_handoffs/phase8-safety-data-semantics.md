# 交接：安全数据语义一致性修正（phase8-safety-data-semantics）

任务书：`NS800_Safety_Data_Semantics_Qoder_Task_20261022.md`
日期：2026-10-22 起（跨到 10-23 完成验证与证据落盘）
性质：离线固件开发 + 主机验证 + Keil 编译 + 普通提交/push。**本任务不命名为 F2e/F3，不含任何硬件动作。**

## 1. 分支 / 基线 / 提交

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/LINboss666/ns800-stepper-safety |
| 分支 | `phase8/tmc-drive-config` |
| 开发基线（开工时本地=远端，工作树 clean） | `9430dabb86227a819e23e2269a7e4694acd7c2dd` |
| **实现 checkpoint（本轮全部验证实际执行的对象）** | `e974d488408630348401c1b7a13606a61d6fab7a` |
| 历史软件 GO 接受对象（不变） | `296c369607ea69c201f797b80950b76ff312c0e0`；本轮有生产改动 ⇒ **等独立复审**，不自动继承 GO |
| 板上固件身份 | 仍 `USER_REPORTED_E40`（`e40a9b95…`）；本轮未读回 Flash、未烧录 |
| 冻结引用 | main `1b3757ee…`、phase7 `6d3fbc55…`、标签 `097935fe…`→`e40a9b95…` 未动 |
| 最终 SHA | 在交付回执给出并 `git ls-remote` 回读；正文不自引用（避免循环提交） |

## 2. 实际改动

生产（`bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/`）：

| 文件 | 改动 |
|---|---|
| `safety_io_semantics.h` **（新增）** | 安全输入位序（bit0=ENN、1=LIMIT_MIN、2=LIMIT_MAX、3=DIAG）、`SAI_ACTIVE_MASK`（按配置 = 0x9 / 0xF）、flags 位移/掩码、`BBFL_SCHEMA_CURRENT=1`、版本枚举与解释函数原型 |
| `sensor_service.h/.c` | `estop` → **`enn_feedback`**；新增 `sai_active`/`sai_valid`；`valid_safety` 改为聚合"活动输入本帧全部读到"；每帧重算 valid；严格 `PIN_LOW/PIN_HIGH` 判定；OFF 时限位连 `safety_pin()` 都不调用；`sensor_snapshot` 逐路 `VALID / INVALID_NO_SAMPLE / DISABLED_BY_CONFIG` |
| `blackbox.h/.c` | `bb_frame_t` 用两掩码替换未使用的 `valid_safety`（仍 48B，编译期断言）；`bb_sample` 从**同一源帧**传递电平+active+valid；`bb_to_sample` 按新布局打包并写 schema=1；新增生产解释函数与 `blackbox_dump` 的版本分派（V1 / LEGACY_UNVERSIONED / UNKNOWN_SCHEMA） |
| `project_board.h` | 删除 `PIN_NAME_ESTOP` 遗留别名（已无生产消费者）与过时注释；`FAULT_ESTOP` 等历史枚举**未动** |

测试与文档：`tests/host/sd_main.c`（新增，919 行）、`tests/host/shim_sd/{rtthread.h,rtdevice.h}`（新增）、
`tests/host/run_safety_data_semantics.sh`（新增 runner，含 `REVERSE=1` 两项反向）、`tests/host/README.md`（三链并列说明）、
`reports/reviews/phase8-safety-data-semantics/`（5 份脱敏证据）、BSP `README.md`/`开发进度.md`/`待办事项.md` 同步。

**未改**：`motor.*`、`safety_gpio.*`、`safety_thread.*`、`safety_state.*`、`tmc2209.*`、`step_pwm.*`、`diagnosis.*`、
`supervisor.c`、`ns_storage.*`、`ns_flash.*`、传感器驱动/标定算法、`project.uvprojx`、`.gitignore`、旧采集脚本与旧证据、
既有测试的断言/判据/期望/runner（只新增专用夹具与 runner）、`env.sh`。

### 2.1 flags 应用载荷（语义版本 1）

```
7:0   fault_code                （保持）
11:8  电平 bit8=ENN 反馈 / 9=LIMIT_MIN / 10=LIMIT_MAX / 11=DIAG （位置保持）
15:12 安全输入 active mask
19:16 motor_state               （保持）
23:20 safety_state              （保持）
27:24 安全输入 valid mask
31:28 应用安全输入语义版本 = 1   （不是 LOG1 外层版本，也不是硬件验收版本）
```

版本分派（`blackbox_dump` 与主机链共用同一实现）：`1` ⇒ 逐路标注可读性；`0` ⇒
`LEGACY_UNVERSIONED`，bit8 的 ESTOP/ENN 角色与活动/有效集合**不可追认**，只打印 `raw_flags`；
其他 ⇒ `UNKNOWN_SCHEMA`，同样只留 raw，**绝不默认成 V1**。旧记录不迁移、不重写、不清空。

### 2.2 sizeof 与内存差异

| 对象 | 前 | 后 | 说明 |
|---|---|---|---|
| `sensor_frame_t` | 52 | **56** | 两个掩码字节超出原 padding（+4B） |
| `bb_frame_t` | 48 | **48** | 掩码落在原 3 字节填充里，编译期断言钉住 |
| `ns_fault_sample_t` | 48 | 48 | `offsetof(flags)=44` 未变；页面偏移（12B 头 + 44 = 56）由 `ns_storage.c` 决定，本轮**零改动** |

Keil Rebuild All 对比上一轮（2026-10-21 返修后）：`Code 147610→148530 (+920)`、
`RO 64214→64730 (+516)`、`RW 2176→2176 (0)`、`ZI 83404→83412 (+8)`。
ZI 的 +8 与 `s_frame`+`s_prev` 两处静态实例各 +4 一致；黑匣子 35.2KB BSS 未增长；
缓冲容量 `BB_PRE/BB_POST_FRAMES` 未改，**没有扩大任何数组或存储格式**。

## 3. 新主机链（真编译 `sensor_service.c` + `blackbox.c`）

- 变体与必需集合：`off_target` 21 条、`on_target` 20 条（`SD05b` 仅 OFF 有意义，ON 不发该 id）。
- 覆盖：SD01/SD02（活动与有效掩码、限位触发态仍有效）、SD03（逐源解析失败只清对应位、active 不缩水、聚合=0）、
  SD04（-1/2/7 非法读数不得变成有效 HIGH）、SD05（0xAB 脏基帧/旧掩码不污染本帧、失败后可恢复）、
  SD06（私有上下文不改生产 seq/prev，SG 10 分频语义保持）、SD07（同一源帧传递，且**落盘时不重算**）、
  SD08（flags 位置对独立期望向量 + `NS_VALID_STEP`/event/pre-post 职责未重排）、
  SD09（版本 0 与未知版本走真实解释路径仍为 LEGACY/UNKNOWN + raw）、
  SD10（`sensor_snapshot`/`blackbox_dump` 输出区分有效/无效/禁用/旧格式，无"独立急停状态"虚构）、
  SD11（零 GPIO 写、UART 只有分频 SG 读、结构与外层偏移不变）。
- 真实结果：`off_target`/`on_target` 均 proc_rc=0、自报 fail=0、缺席=重复=未登记=0，runner **rc=0**。

## 4. 两项局部反向验证（只改仓外一次性副本）

| 注入 | 目标 | 期望失败身份 | 结果 |
|---|---|---|---|
| R1 聚合有效性退回"四路均解析/有效" | `sensor_service.c` | `SD01-off-masks-limits-never-touched` | **META-OK**（proc_rc=1，锚点恰好命中一次） |
| R2 黑匣子 valid 掩码打包改成恒等 active | `blackbox.c` | `SD07`、`SD07b`、`SD10b` | **META-OK**（proc_rc=1，三条全部命中） |

干净对照由正常变体提供（同一 `sd_main.c`、未注入源码，两变体全 PASS）。
未新增元负例框架，未扩修 F2d 历史验证链。

## 5. 既有回归与目标编译

| 项 | 真实结果 |
|---|---|
| `build_and_run.sh` | V1 `CHECK_PASS=25 FAIL=0 BAD_SELFTEST=0 MISSING_CASE=0`；V2 `22/0/0/0`；RUNNER RESULT PASS，rc=0 |
| `run_io_behavior.sh` | 四变体 27 / 29 / 40 / 42（合计 138）全 PASS；非法限位宏值编译被拒绝（rc=1）；PASS，rc=0 |
| Keil `UV4 -r project.uvprojx -j0` | **0 Error / 0 Warning**，工具退出码 0，131 编译单元，`sensor_service.c`/`blackbox.c` 均在内 |
| 构建钩子（先只读核查） | BeforeMake 两条均关闭；AfterMake 仅 `fromelf --bin`；**无下载/复位/串口**钩子，也未执行 |
| 工程隔离 | `project.uvprojx` 未改；`sd_main`/`shim_sd`/`io_main`/`host_main` 命中数 0（主机源不进目标工程） |
| 四道宏 | 全部仍 `RT_FALSE`（LIMIT-ON 只用主机命令行覆盖，未写回 Keil） |
| 按要求未重复跑 | 13 例旧负例、M1–M5、R4 路径守卫、SW1 PowerShell 自检（公共工具未改，任务书 §6C 明确不要求） |
| 未执行目标命令 | 板上 `sensor_selftest`/`blackbox_selftest` 未运行（可能发 UART/写真实 Flash），只允许主机桩环境 |

## 6. 真实失败与修正（原样保留，见 `NOTES.txt` 第 4 节）

1. `sd_main.c` 编译错误：`sd06` 第二段用了未声明的 `seq` ⇒ 补声明。生产代码未改。
2. `SD05c` 首跑 FAIL：夹具恢复步骤只重设读数、没恢复解析句柄（`handle=-1` 残留）⇒ 补
   `sim_restore_resolved()`。期望未放宽。
3. `SD11c` 首跑 FAIL：我把结构体字段偏移（44）与 `ns_storage.c` 的页面偏移（56）混为一谈
   ⇒ 判据改为结构大小/末字段偏移/`bb_frame_t` 不变，页面不变性由"该文件零改动"保证。
4. 工具使用失误：第一次 `REVERSE=1` 复用同一 `OUT_DIR`，runner 开头的清理删掉了正常变体的
   `*_out.txt` ⇒ 用 `SD_OUT_DIR=…/sd_rev` 分离重跑，两份证据都来自真实执行，未挑选轮次。

## 7. 未验与边界

- 本轮源码**未上板**：板上仍是冻结 `e40a9b9`，新语义的真实运行行为、真实 Flash 写入均 NOT TESTED。
- LIMIT-ON 只在主机分支通过 ≠ 限位上板验收；`H2` 现板未插。
- 不证明也不宣称：保护能力、停机时延/上界（RISK-03/03a）、复位与上电瞬态、STEP 波形、
  反馈线连续性、外接 NC 按钮、断线容错、急停安全等级（RISK-04-b）、TMC 写握手、运动。
- `METER_NOT_MEASURED_THIS_RUN` / `ANALOG_NOT_VERIFIED` 仍是 2026-10-22 两轮采集记录内的原文事实；
  静态电气与现有 SW1 功能的 `PASS (USER_CONFIRMED)`（草案 A29）不因本轮改动而改变或扩大。
- 采集脚本 `sw1_cut.ps1` 复用仍 HOLD（T-01/T-02 本轮**未触碰、未修改**）。

## 8. STOP

**`AWAITING_INDEPENDENT_REVIEW`。** 只证明数据语义与离线逻辑一致；不进 F2e/F3，不烧录、
不写 TMC、不输出 STEP、不接电机、不翻宏、不动串口与硬件。

## 9. 独立复审返修（同日追加，2026-10-23；返修基线 = 复审对象 `2399d28`）

复审件：`NS800_Safety_Data_Semantics_Review_2399d28_20261023.md`
**结论：`SOFTWARE_REVIEW_REWORK`，返修仅限新增主机 runner 的两个错误通过入口（均 P1，验证工具）。**
生产实现被复审 §2 认可，并在 §3 由审查端（Linux + GCC 13.3 + 仓外兼容头）**独立复跑通过**
（默认 OFF 21 / ON 20、两项反向、V1 25 / V2 22、I/O 138 例，外层 rc 均为 0）；
**不重做生产代码、不重建 Keil、不重做硬件、不扩大既有验证框架**。

### 9.1 本轮实际改动（只动 runner 与其文档/证据）

| 文件 | 改动 |
|---|---|
| `tests/host/run_safety_data_semantics.sh` | SD-NEW-01 + SD-NEW-02 两处入口（见 9.2） |
| `tests/host/sd_runner_rework_check.sh` **（新增）** | 两个缺陷的有限回归：真实调用同一个 runner，断言其打印身份与真实退出码，**不另写复制判定的"自检器"**；全部在 `mktemp -d` 仓外沙盒内进行 |
| `tests/host/README.md` | 入口纪律、台账复核模式、新回归入口 |
| BSP `README.md` | 修复复审 §5 指出的"引脚表 ENN 行被拆成三行导致 GFM 表格断开" |
| `reports/plans/phase8-safety-chain-acceptance-draft.md` | A26 追加"已由 `e974d48` 关闭"的日期覆盖指向（原文保留，不改写历史描述） |
| 本 MD/JSON + `reports/reviews/phase8-safety-data-semantics/rework_*.txt` | 返修记录与场景证据 |

**未改**：全部 APP 源码/头（含 `sd_main.c` 夹具、两份 shim、`env.sh`、既有 runner 与期望）、
`project.uvprojx`、宏、门禁、身份设置；旧证据与旧脚本原样保留。

### 9.2 两个入口的修法与真实回归结果

| 缺陷 | 修法 | 真实结果 |
|---|---|---|
| **SD-NEW-01** 未知 `ONLY` 零执行却 PASS（复审复现 `ONLY=ZZZ_NO_VARIANT` → 目录内无 exe、外层 rc=0） | `ONLY` 在**任何** `rm`/`mkdir`/重定向/编译**之前**验证：只接受完整名称 `off_target`/`on_target`；未知词、合法词夹带未知词、空或纯空白、重复词，以及 `ONLY` 与 `REVERSE=1` 组合一律 `rc=4` 且不动任何文件；新增"选择集合 / 实际执行集合 / NOT_RUN"三段披露，只有默认两变体都真执行且通过才声明"两个变体全部真执行" | 5 个拒绝场景全部 `rc=4`，每次核对"目标目录未被创建 + 沙盒 KEEP 标记完好"；合法单变体 `ONLY=off_target` → `rc=0`、只声明 `off_target`、`on_target` 标 `NOT_RUN`，不再出现"两个变体全部真执行"措辞 |
| **SD-NEW-02** 反向异常终止被算成检测成功（复审复现 SIGTERM → `proc_rc=143` 仍 `META-OK`、外层 rc=0） | `META-OK` 必须同时满足：副本构建成功 + 夹具**合同退出码 `rc=1`** + 完整且自洽台账（必需 id 唯一出现、无未登记用例、`SUMMARY` 恰好一条且 `cases/pass/fail` 与实际行对齐、`pass+fail==cases`）+ 指定业务失败身份全部命中。非 0 非 1 退出（信号/异常终止）或台账不完整 ⇒ `INFRA-FAIL`（外层 `rc=4`）；`rc=0` 或缺期望身份 ⇒ `NOT-DETECTED`（外层 `rc=1`）。台账校验抽成 `validate_ledger()`，**正常变体与反向共用同一实现** | 用仓外 `-Wl,--wrap=main` 包装编译器（不改仓库任何源）把"本应 rc=1"的失败运行改成非合同退出：`exit3` 与 `sigterm` 两种注入下 R1/R2 **均记 `INFRA-FAIL 非合同退出码 rc=3`，外层 `rc=4`，无 META-OK**（本机 `raise(SIGTERM)` 实测也返回 3，故按"非 0 非 1"通用规则拒绝，**不特判 143 也不只看 rc≥128**）；同一编译器未注入的对照 → 两项 `META-OK`、外层 `rc=0` |
| 判据未放宽（复审 §6.2.4） | R1 附带 `SD05c` 失败是当前已知结果：runner 如实登记为"额外失败"但**不改变判定** —`R1 … META-OK 命中 1 个业务失败身份 (proc_rc=1 必需用例=21 实际FAIL行=2 额外失败=1)`、`R2 … 命中 3 个 (实际FAIL行=3 额外失败=0)` | 正常反向外层 `rc=0`；默认链 OFF 21 / ON 20 全 PASS `rc=0` |

`sd_runner_rework_check.sh` 汇总：**通过=21 不符=0 环境故障=0 → `SD REWORK CHECK RESULT: PASS`，外层 rc=0**。
其中"删一条必需 CASE（同时把 SUMMARY 计数改成与实际行一致，以隔离出唯一失配点）"→ `rc=4 缺席: SD01-…`；
"删 SUMMARY 行"→ `rc=4 SUMMARY 行 0 条(必须恰好 1 条)`；未篡改副本的正对照 → `rc=0 台账合同成立`；
包装器自身先用最小程序证明 `--wrap` 通道有效（探针确实变成非 1 退出），否则整项记环境故障而不算检出。

### 9.3 计数勘误（复审 §1/§5，本轮更正）

上一轮回执口头说的"18 个改动路径"是**远端 blob 对账的抽样数**；完整增量实为 **21 个文件，+2405 / −41**
（6 个 APP + 5 个 `tests/host` + 7 个 `reports` + 3 份 BSP 文档）。JSON 的 `evidence_paths` 从不声称是
全部改动清单，该计数错误不代表文件缺失。

### 9.4 登记但**不在本轮修**的一项

复审 §5 指出 `blackbox.c` 的尺寸注释"新布局…0 填充"不准确：48B 断言本身正确，实际是旧结构总 padding 3B
（SG 后 2B、故障码前 1B），新结构仍保留 SG 后 2B，靠替换聚合副本后多出的 1B 占掉尾部 1B padding，
帧大小没有增长。因复审 §6.1 明确"全部 APP 源码/头均不改，且不要求单独生产改动/Keil 重建"，
本轮**只在文档登记该勘误**，留待下一次真实生产改动时一并修正注释。

### 9.5 未重跑项与原因（不冒称为本轮重新执行）

APP 源码与既有测试夹具本轮**冻结**，因此按复审 §6.3 不重跑：Keil、V1/V2、138 例 I/O、旧 13 例负例、
M1–M5、R4 路径守卫、SW1 工具与任何硬件动作。原因如实记录为"相关源未变"；上一轮的真实结果见 §5 与
`existing_regression_summary.txt`。

### 9.6 本轮边界

零硬件动作（未开串口、未读写真实 GPIO/TMC、未烧录/复位/下载、未动供电/模块/接线/SW1、未接电机、
未输出 STEP、未清故障、未动 IRQ/极性/门禁、未翻宏、未运行历史 PowerShell 采集器）；
四道目标宏仍 `RT_FALSE`；板上固件身份仍 `USER_REPORTED_E40`；RISK-03/03a 与 RISK-04-b 不变；
`sw1_cut.ps1` 的 T-01/T-02 仍 HOLD；静态电气与现有 SW1 的 `PASS (USER_CONFIRMED)` 作用域不变。

**STOP：`AWAITING_INDEPENDENT_REVIEW`（本轮只应审 runner 两处入口与 §6.3 的有限验证）。**
