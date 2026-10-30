# NS800 M8 现场候选包（SG/ADC 相对保护）—— 已准备，**未获授权，未执行**

包名：`phase8-m8-sg-adc-relative-protection-FIELD-CANDIDATE`
状态：`NOT_AUTHORIZED` / `NOT_EXECUTED`
本包**不**构成烧录、下载、复位、电机动作或 Flash 写入的批准。任何一步都必须由操作者
在本包之外单独授权（"hardware_actions=[]" 是本交付轮的登记事实，见交接）。

## 1. 绑定的输入身份（全部实测，非引用）

| 项 | 值 |
|---|---|
| 基线 commit | `f1aa2e832d536593641cea44ea890fb8ce9558b9` |
| 软件输入 commit（源码+验证链） | `f9217005f191a601601d315add02c3e8c51f64ed` |
| 构建工具 | `armclang V6.24`（`C:\Keil_v5\ARM\ARMCLANG\Bin`），UV4 5.43 |
| 三目标结果 | 各一次 Rebuild All，`0 Error(s), 0 Warning(s)`，uv4 返回码均 0 |

镜像（`reports/firmware/phase8-m8-sg-adc-relative-protection/`，与构建产物逐字节相同）：

| 目标 | 文件 | 字节 | SHA256 |
|---|---|---|---|
| rt-thread | `rt-thread.bin` | 281996 | `ff7883736702eb32e6927a93579e70b5b88d4c6a1018c19f32ab6b7a2466ca9d` |
| rt-thread | `rt-thread.axf` | 1741680 | `0b47baee950fb0a606f3814b142c07ba27cd26f5d087c4714dd91511c5431583` |
| rt-thread-measure-a0 | `rt-thread-measure-a0.bin` | 292252 | `86013070ffa6f0f097d977244abdfef165d59bf75314da460f854f7a27829f75` |
| rt-thread-motor-m1-candidate | `rt-thread-motor-m1.bin` | 282100 | `3b226367f9f4a60da60452c383c134d4e45c121b4ce60603a75df9bcc21a3a86` |

现场执行时必须先做**字节回读**：下载后按定长读回 sector，把读回的字节散列与上表比对；
不相等即停，不进入任何后续阶段。（沿用板级包既有 S0 绑定打法。）

本包**选定镜像**：`rt-thread-motor-m1.bin`（唯一置 `MOTOR=1/TMC=1` 的候选目标，其余两个
目标不含电机使能，无法完成 §4 的巡航学习阶段）。若审核要求用默认目标先做 S1–S3
（只读+门禁拒绝），则用 `rt-thread.bin`，且该分支不含任何电机阶段。

## 2. 额度与硬预算（执行轮总额，超出即停）

| 额度项 | 上限 | 说明 |
|---|---|---|
| COM5 打开次数 | 1 | 打开后**不写任何串口属性**（P0 已证：打开后写设置会重配端口并损坏字节） |
| 下载（flash）次数 | 1 | 仅 `rt-thread-motor-m1.bin`；单独授权才允许 |
| 目标 Flash 定长回读 | 1 | 紧随下载之后 |
| 主动复位（DAP/pyocd） | 0 | 只允许上电顺序造成的自然复位 |
| 电机起转次数 | ≤ 6 | 每次 ≤ 5 s，累计运动 ≤ 60 s |
| 加载窗口（带负载的一次观察） | ≤ 5 s | 见 §4 的 S5 |
| Flash 参数写入（`config_save`） | **0** | profile 只留在 RAM；重启即失（见 §5） |
| 重试 | 0 | 任何阶段失败即停，不重跑，不改参数再试 |
| 人工读数（DMM） | 0 | 本包不依赖电压读数结论 |

## 3. 前置条件（缺任何一项就不许开始）

1. 板级电源序列已知：先 24 V，后 USB；结束反向（先关 24 V，再拔 USB）。
2. SW1/使能链位置与 M3/M4 轮一致并现场确认（ENN 反馈可读）。
3. `sensor_status`、`diag_status` 在**烧录前**的旧固件上可读取一次，用于对照（只读）。
4. COM5 空闲（无 MobaXterm/motty 等持有者）。
5. 操作者在场，能立即切断 24 V。

## 4. 阶段顺序（严格串行，失败即停）

**S0 离线绑定**（本轮已完成，执行时重做）：确认工作树干净、镜像散列与 §1 一致、
`build_manifest.json` 的 `software_input_commit` 与实际 HEAD 一致。

**S1 唯一一次下载 + 唯一一次定长回读** → 散列必须等于 §1 表中值。不等 ⇒ 停。

**S2 上电就绪观察（只读，零写）**：复位→开口→等待横幅；然后依次
`sensor_status`、`config_load`、`config_show`、`diag_status`、`diag_profile show`。
判据（全部是"读到的字符串"级判据，逐条落原始字节）：
- `config_load` 必须打印迁移路径并给出 `version=0x00010003`；若板上的旧参数是 v2，
  应看到字段被保留且 `profile: none`（**这是 v2→v3 迁移在真机上的第一条证据**）；
- `diag_profile show` 必须给出 `[DIAG] profile: none` 与 `pending: none`，
  以及 `live sig=<非 0>`；
- `diag_status` 的 relative 段必须 `mode=MONITOR_ONLY profile=NONE`，
  且 `rel latch: none`。重启后 mode 必须是 MONITOR_ONLY、armed 位为 0（不得残留武装）。

**S3 开启门禁的拒绝路径（零运动，零写）**：
`diag_mode relative`（无 CONFIRM）→ 必须 `RELATIVE REFUSED: needs explicit CONFIRM_EXPERIMENTAL`；
`diag_mode relative CONFIRM_EXPERIMENTAL`（尚无 profile）→ 必须 `no committed profile`，
且模式仍是 `MONITOR_ONLY`。这两条是**产品门禁在真机上的直接验证**，不接受口头推断。

**S4 静止零点（零运动）**：确认 `motor_status` 为 IDLE/未 armed 且 `safety_state` READY，
执行 `diag_profile zero 32`。判据：`zero OK n=32 raw <min>..<max> mean_x1000=<v>`；
若打印 `zero FAILED: 12s timeout` 则记录帧源状态并停（不重试）。

**S5 巡航学习与 ≤5 s 加载窗口（唯一需要电机授权的一步）**：
`diag_profile learn 8 <lo> <hi> 2`（lo/hi 由操作者当场按 `motor_status` 选定，窗口内
必须包含实际步频），随后按 `motor_start` 计划在**一个方向**跑 ≤5 s。
判据：`learn OK adc[..]n.. noise.. sg[..]n.. seq..`；
若中途离窗必须看到 `learn FAILED: left the approved window or source lost` 且
`commit REFUSED: pending incomplete`。然后 `diag_profile commit`（静止态）→
`commit OK id=1 rev=1 flags=0005 FIELD_UNVERIFIED`。
再 `diag_mode relative CONFIRM_EXPERIMENTAL` → 必须打印
`thresholds=FIELD_UNVERIFIED` 与 `mode=RELATIVE_ACTIVE`；
`diag_status` 必须显示 `coverage` 有效、`rel sig ... match=YES`。
**注意**：本阶段只验证"保护被正确武装且工况匹配"，不构造真实堵转；
`REL_SG_SEVERE`/`REL_ADC_SEVERE`/`REL_FUSED_SEVERE` 的**真机触发在本包内不可证**
（不允许人为加载到堵转），保持 `UNPROVEN`。

**S6 收尾（零 Flash 写）**：`diag_profile clear CONFIRM` → `profile voided; mode=MONITOR_ONLY`；
再 `diag_status` 确认 `profile=NONE` 且 `coverage` 不再是 COVERED。
`config_save` **不执行**（额度 0）：profile 与迁移后的配置留在 RAM，重启回到
Flash 里的旧 v2/v3 参数——这是设计规定（重启仍 MONITOR_ONLY），不是遗漏。
最后按 §3 的反向顺序断电。

## 5. 结果登记合同（采集器必须逐行落盘）

- 每条命令的**原始响应字节**先落盘（base64 + sha256 + 长度），再做任何判定；
- 台账行格式 `FIELD|<stage>|<id>|PASS|FAIL|<detail>`，最后 `FIELD-SUMMARY|stages=N ...`；
- 结论天花板：`PROTECTION_ARMED_AND_WINDOW_MATCHED`。**不得**写
  `PROTECTION_TRIGGERED`、`STALL_DETECTED`、`THRESHOLDS_VALIDATED`——
  阈值一律 `FIELD_UNVERIFIED`，只有真实异常样本 + 保护动作记录才允许升级，而本包不构造异常；
- 任一阶段失败即停，保留首条失败原件，不追加、不修改、不重跑。

## 6. 本包尚未落实的部分（如实登记，不做无害化包装）

- 现场脚本（PowerShell 采集器）**尚未编写**：本轮授权里没有现场执行，脚本要在
  真正获准执行的上一轮再产出并做 `-SelfTest`；现在写出来只会是一份无法按当轮
  镜像身份验证的草稿。因此本包是"阶段/额度/判据"级别的具体候选，不是可执行脚本包。
- 选定镜像的 **AXF 符号/DWARF 复核未做**（需要单独的调试授权）。
- `REL_*` 严重触发的真机证据缺口如 §5 所述，属**机制上不可在本包内证**，
  需要另一次显式的加载实验授权（含人工切断手段）才能推进。
