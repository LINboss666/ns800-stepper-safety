# NS800 M7-R1 交接（phase8-motor-m7-current-calibration-r1）

日期：2026-10-29。被审对象：`68d0774e6b32fec28ebca8dd306a23a29a4a2fc9`。
本轮返修起点 = 该提交；本轮交付链：`5b564eb`（软件输入）→ 三目标构建 → 本文件与工件。

**结论口径：本轮不发任何自我授予的 REVIEW GO。** 回执状态见 §6。

## 0. 边界（一句话）

授权范围只有离线返修、主机验证、Keil 构建与 Git 交付。
**本轮硬件动作 = 0**：未创建串口/DAP/仪器对象，未枚举、未读目标存储器、未下载/复位、
未发目标命令、未改供电/接线/SW1、未 arm/start、未输出 STEP/DIR/HIGH。
本文不是 P0/P1/P2 现场批准；M6 现场候选不执行；M5 额度不继承。

## 1. R1a —— 静止事务必须覆盖实际预约

**产品**：`applications/motor.c` 的 `motor_pwm_grant_to_diag()`
（`rt-thread-master/.../applications/motor.c:2224` 起）现在把"最终资格判定 + 预约"
放进同一次 `mot_lock` 持有期；取锁走有返回码的 `motor_lock_take_checked()`，
取不到就既不判定也不预约。锁序 `mot_lock → cfg_lock` 未变，`sp_reserve` 的短窗口
仍在业务锁之内，PRIMASK 窗口里没有打印/等待/浮点。
没有强改 owner、没有用恢复票据、没有改 `stop_priority` 核心、没有加通用回调。

**验证**：S2 第 19 组 `cal_static`（C22a…C22f2，host_ovr 9 例）。
两种确定次序 + 控制 + 对照 + 非恒真负控都在；会合全部用事件
（`S2_SEQ_CONTEND` 只在另一线程确实持锁时置位），没有 `usleep` 猜测。

**本轮最重要的一条负面结果（原文保留）**：首轮反向 REV3 把 gate+预约挪回锁外时
**没有被检出**——

```text
REV3 s2_rc=0 hits=0/2
  REV3 NOT DETECTED
```

因为修前代码会碰巧卡在 gate 内部 `motor_get_snapshot()` 的取锁上，
"别人持锁时预约出不来"这一观察在修前修后同样成立。补 C22f（观察**预约那一刻**本线程
`mot_lock` 持有层数）之后同一突变被具名检出：

```text
CASE|C22f-qualification-and-reservation-are-one-hold|FAIL|grant=0 认领=1 owner=2 预约时 mot_lock 层数=0
```

细节与判读见 `concurrency_contract.md`。真实双线程探针（复审 `probe_diag_threads.c`）
修后观察：`cal_begin_rc=99 cal_guard_held=0 grant_rc=0 owner=2 rendezvous=2`，
对照修前云端 `cal_begin_rc=0 cal_guard_held=1 … rendezvous=1`（`probes_r1.md`）。

## 2. R1b —— 配置取锁失败不得发布系数

**产品**：`project_config.c:36-46` 新增 `cfg_take_checked()`（`!cfg_lock_ok → -RT_EIO`，
并回传 `rt_mutex_take` 的真实返回码）；`project_config_set_calibration()`
（`:214`）在取锁失败时**直接返回，一个字段都不改，也不 release**。

**范围外观察（如实登记，未按"顺手重构"处理）**：旧的 `project_config_set` /
`project_config_load` / `config_default` / 启动路径仍用无返回码的 `cfg_take()`。
复审 §6.1 明确要求不借此重构全部旧业务锁，所以本轮不动它们；这一条留给将来有独立
范围的轮次，不写成"已全部修好"。

**验证**：`CC27 / CC27b / CC27c / CC27d`（`tests/host/cc_main.c:634` 起），
本机链 34/34 PASS；反向 REV6 具名检出 CC27+CC27b（`cc6_rc=1 hits=2/2`）。
CC27c 在该突变下仍绿且**如实登记为不具区分力**（v1 的 `cfg_take()` 在
`cfg_lock_ok=FALSE` 时是空操作，既不会取也不会放）。

**修后观察**：`PROBE cfg_mutex_unavailable rc=-12 version_before=2 version_after=2 changed=0`
（修前云端：`rc=0 … version_after=3 changed=1`）。

## 3. R2 —— 合法参数下 µA 导出溢出

**产品**：`current_cal.c` 删除 `cc_round_scaled`，新增 `cc_scaled_dec()` +
`cc_copy_out()`：先按 `|v·mult| < 1e18` 判范围再转换，越界写 `OUT_OF_RANGE`；
`cc_show` 的 adc/cfg `offset_uV`/`gain_uV_per_A` 与 `sample ma_uA` 全部改走 `%s`。
没有引入 `%lld`，没有为了过测试抬高最小 gain，行仍短于 RT_CONSOLEBUF 裁剪线。

**验证**：`CC28 / CC28b / CC28c`（正负最大、零、合法最小增益的真实输出断言）+
`CC29`（`OUT_OF_RANGE` 标记）；反向 REV4 `cc4_rc=1 hits=2/2`。

**修后观察**：`PROBE legal_gain_rc=0 actual_ma=3299194.000000 raw=4094` ⇒
`[CAL] sample ma_uA=3299194000 src=MEASURED`（`3299194000 > INT32_MAX`，
旧实现是未定义行为）。
**UBSan 那条不是本机复现的**：本机 GCC 4.9.2 无 `libubsan`、不支持
`-fsanitize=float-cast-overflow`；sanitizer 原文仍归云端
（`current_cal.c:74:41: runtime error: 3.29919e+09 …`）。

## 4. R3 —— calcalc 的资格、误差界与结论一致性

工具升为 v2（`TOOL_ID ns800-m7-calcalc-v2`，schema `ns800.m7.calinputs/2`，拒收 `/1`），
路径不变：`reports/reviews/phase8-motor-m7-current-calibration/m7_calcalc.py`。

| 子项 | 做了什么 | 具名证据 |
|---|---|---|
| R3a 结论一致 | 资格失败或任一必需留出点失败 ⇒ 整体 `REJECT`、`cli_result` 非零退出、**不产出可应用命令**；诊断数值仍披露但标 `usable=false`；合格仍只到 `DRAFT_NOT_APPROVED` | `M7C34/M7C50/M7C51/M7C52`；反向 REV5 命中 3/3 |
| R3b 误差界 | 取消"把仪表 ±限当标准差 RSS 后称上界"。改成逐项披露的**绝对相加区间界** `worst_case_bound_mA`（有界项取半宽绝对值，重复性项线性叠加并写明是 1σ 估计），`error_model="absolute-sum"`；给不出可论证的界就不通过 | `M7C35/M7C35b`；复审数值反例本机复算：界 `13.000 mA` ≥ 仪表最坏 `11.8 mA` > 目标 `8 mA` ⇒ `REJECT`（旧实现给 7.238439 并放行） |
| R3c 输入资格 | 计数必须是整数且 `n_valid==n_requested`；二阶矩按 Cauchy–Schwarz 双向核验（不再 clamp）；误差限/时窗非负且时窗为正；`used_for_fit/covers_all_measured_current` 必须真布尔；`FIELD_MEASURED` 必须绑定存在的完整原始响应 + 正确 SHA + 统计一致；独立性按**声明**判且**不分 provenance** | `M7C38…M7C53`；七个复审反例现在全部 `rc=1` 且无命令 |

**R3c 本轮补上的一处真实缺口（如实登记）**：首轮本机复现时
`REUSED_HOLDOUT_ARTIFACT` 仍然 `qualified=true rc=0`——因为独立性检查原先写在
FIELD 分支里，而 HOST_SYNTHETIC 被整体豁免。豁免只应覆盖"文件不存在不用打开"，
不该让"三点共用同一来源件声明 + 同一样本区间"过关。现在
`_verify_capture_independence()` 在任何 provenance 下都先跑，共用会话文件只有
**响应范围互不重叠**才合法；自检新增 `M7C53` 固定这个形状，自检从 53 例增加到 **54 例**。

另：原 `_pt()` 的"健康"统计量确实不可能对应那 128 个样本（n=128、min/max=mean±20
而总离差平方只有 512；仅两个端点就至少贡献 800）。已按端点真实可生成
64×(mean−2) + 64×(mean+2) 重算 `sum/sum_sq/min/max`；原 37 例的身份与业务意图保留，
本条属于**对错误正控的数据修正**，不是放宽验证保绿。

## 5. §5 伴随更正

### 5.1 P0/P1/P2 口径（新文件 `board_candidate.md`，取代件；旧件保留）

- **P0 不再继承 M5 状态**：去掉 `drive CONFIGURED` 与 `IRQ OPEN` 作为全程前置。
  源码依据：`tmc2209.c:2180` 的 `tmc_cfg_ctx_init()` 在启动时把运行时摘要设为
  `TMC_DRV_UNCONFIGURED`（`tmc2209.c:2334` 打印 `"UNCONFIGURED"`），软件摘要由本次启动
  建立，不能从芯片仍留存寄存器推导；且 P0 命令清单里没有任何一步会产生这些状态。
  新前置收紧成 4 条观测（Safety READY/无故障、Motor 静止、STEP 未授权、ENN 在禁能侧），
  `UNCONFIGURED` 作为正常启动事实如实记录。没有为凑状态追加 apply/开 IRQ/确认极性/运动。
- **补了一次明确的身份次序**：下载 → 整片逐字节回读 → 一次复位 → 板上启动文本 →
  软件版本行，并写明 A2（flash 字节 = 归档字节）与 A4（板上真在跑这段字节）
  不可互相替代；本轮两者都没做。
- **电压比较改为"电压观察"口径**：`raw×3300/4095` 里 3300 是名义满量程，实际 ADC 参考
  与量化/转换误差项本轮无依据，且 `H10-3` 是 3V3 测点、未证明等于实际 VDDA。
  因此只登记逐行配对偏差 + 名义假设 + 已知误差项，结论标签 `VOLTAGE_OBSERVATION_ONLY`，
  不写"精度验收"，不据此判 ADC 链不合格，也不为它打开时钟/A0/示波器路线。
- **人工读数额度统一**：给了一张 4 行的配对表，总额度 = 每测点 8 次读数 / 4 次 sample，
  三者自洽、可整包执行；不逐条重新批准，不例行要求拨 SW1 或回报断电时刻。
- **P1 保持 `METHOD_NOT_READY`**：选定 A（串进确实经过 R1 的那一段），
  B（已知负载）与 C（仅电压）写成备选、不索取它们的物料；真实缺项只剩 3 条
  （仪表保险丝/插孔/串联压降/量程持续时间资料、不带电断相的转接夹具、
  实物 VM 焊盘与图纸 U9-8 的对应确认）。资料可离线补查，不因此连硬件。
- **P2 状态口径**：已执行动作而未通过时按 `FAILED`/`PARTIAL`/`NOT_ACHIEVED` 登记并保留
  已执行清单；`NOT_EXECUTED` 只用于从未执行的子项。旧版"否则保持
  `CALIBRATION_ON_BOARD_NOT_EXECUTED`"作废。合成系数绝不下发。

### 5.2 编译期横幅（`banner_erratum.md`）

M7 轮"三枚镜像都没有编译期横幅、M5/M6 的 banner 值不可复现"的结论**撤回**：
`__DATE__/__TIME__` 在 BSP 之外的 `src/kservice.c:161-162`，且是两个独立字符串池对象，
我原先的搜索范围和"日期紧跟时间"的 pattern 都注定 0 命中。本轮按零基字节偏移复核
九枚归档 BIN（M7 三枚 + M5 三枚 + M6 三枚），每枚各 1 次日期与 1 次时间，
与复审表逐条相等。已在旧 `build_manifest.json`（新增 `banner_erratum` + 每目标实测偏移，
原字段留在原地备审）、旧 `build_notes.md` §6.1、旧交接 MD/JSON 三处**追加**勘误；
M5/M6 历史件与旧镜像不重做、不改写。
`FINSH_THREAD_STACK_SIZE=4096`（`rtconfig.h:125`）作为源码事实补上；栈高水位仍
`NOT_MEASURED`。归档 Keil 日志的 CRLF→LF 已如实披露，不因此重跑构建。

## 6. 本轮真实执行 / 未执行

| 项 | 状态 |
|---|---|
| current_cal 34/34、S1（61+78）、S2 19 组（target 53 / host_ovr 118）、M6 CLI 10/10×2、I/O 4 变体、SD 2 变体、V1/V2（25 与 22）、calcalc 自检 54/54 | **本机实际执行，全部 rc=0**（`host_runs/`） |
| 六族局部反向 REV1…REV6 | **本机实际执行，全部具名检出**；首轮 REV3 NOT DETECTED 原文保留 |
| 三目标 Rebuild All（`uv4_rc=0/0/0`，各 0 Error/0 Warning）+ 六件镜像归档 + manifest | **本机实际执行** |
| 宏探针（3 正控 + 3 负控）与 136 源文件/`current_cal.c` 每目标 1 次 | **本机实际执行**（armclang） |
| 复审探针复现（diag 双线程 / cfg mutex / µA 导出 / calcalc 反例） | **本机实际执行**，5 条适配显式声明（`probes_r1.md`） |
| UBSan `float-cast-overflow` | **未在本机执行**（无 libubsan/无该选项），来源=云端复审 |
| 板上启动横幅实读、任何串口/DAP/仪器/下载/复位/命令/运动 | **未执行**，本轮硬件动作 0 |
| M7-R1 新镜像烧录 | **未执行**；板上仍是 M5 `01d3e5ab…64bc` |

## 7. 回执

```text
M7_R1_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW
NEW_IMAGES_NOT_FLASHED / HARDWARE_ACTIONS=[]
P1_METHOD_NOT_READY
M5_BOARD_IMAGE_UNCHANGED / NO_HARDWARE_GO_SELF_CLAIMED
```

`P1_METHOD_NOT_READY` 的依据是 §5.1 里那 3 条选定方法的真实缺项，本轮没有取得
UT39A+/C+ 原厂说明书正文（中/英文手册下载均返回 502，只有产品/规格页可读），
也没有转接夹具与实物 VM 对应确认。

## 8. 下一轮（等复审意见，不自行动手）

复审通过后优先给用户**一个具体的 P0 电压观察包**做一次性批准
（`board_candidate.md` §2，含 §2.1 的身份次序与 §2.3 的整包配对表）；
P1 的参考条件继续独立离线补齐。通信、基础启停、300 帧软件故障演示不重开。
M6 现场候选仍不执行，示波器与 A0 继续暂缓。
