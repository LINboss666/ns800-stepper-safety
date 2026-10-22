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
