# M7-R1 P0 电压观察现场包（定稿，本轮不执行）

日期：2026-10-29。状态：**P0_PACKAGE_PREPARED_NOT_EXECUTED / HARDWARE_ACTIONS=[]**
批准对象只能是本文件整包；**本文不是批准，也没有任何设备动作**。

固件输入仍 `5b564eb962e8518920f581111d7a460a2b1fce64`；被审交付 `c78f786…`。
载荷已被独立复审判为 `ACCEPTED FOR P0`（`FIRMWARE_REVIEW_GO(scope=P0_OBSERVATION)`），
该接受不含任何设备动作批准。不先烧 M6/M7 旧候选。

## 1. 固定载荷与回读范围

| 对象 | 路径（仓库相对） | 字节 | SHA-256 |
|---|---|---:|---|
| 待下载 BIN（唯一） | `reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.bin` | 266244 | `e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da` |
| 下载用的 AXF | `reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.axf` | 1688696 | `2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584` |
| P0 采集器 | `reports/reviews/phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py` | 15503 | `1e34589fd3f0250abc5db6a63df2800cf8883c6f65de6219ff350540d138c44f` |
| 专项计算器回归 | `.../verify_calc_candidate.py` | 5469 | `18d03637a88820cc5bc962b85b00e1f7d2aa920ce7d9afc00bcea823b13ae0c2` |

定长回读范围：`0x08000000` 起 `0x00041004` = **266244 B**（等于 BIN 实际长度；
不用 `Code+RO` 代替，不扩到 1 MiB）。
期望启动横幅（逐字）：

```text
5.3.0 build Oct 29 2026 23:11:52
```

推导链：`/usr` 无关，纯源码事实 —— `src/kservice.c:161-162` 打印
`" / | \     %d.%d.%d build %s %s\n"`，版本三元组取 `include/rtdef.h:85-87`
（5 / 3 / 0），日期时间取该 BIN 内恢复出的 `__DATE__`= `Oct 29 2026`@259925 与
`__TIME__`=`23:11:52`@259916（零基字节偏移，`builds/banner_probe_r1.txt`）。
**同类做法已被实物验证过一整轮**：M5 motor 那枚镜像板上实读横幅是
`5.3.0 build Oct 29 2026 00:38:59`，与它归档 BIN 内恢复的日期/时间戳逐字段相等
（`phase8-motor-m5-fault-log/cp1/m5_result_partial.md` + `cp1/readback_sha256.txt`）。
本轮没有板上实读值，所以这一行是**待核对的预期**，不是观测。

## 2. 传输参数：来自真实成功过的同一链，不猜、不扫描

依据 `phase8-motor-m5-fault-log/m5_field_package.md` §3 与 `cp1/flash.log` /
`cp1/readback_sha256.txt`（该轮真的下载过 motor 镜像并做到逐字节回读相等）：

```bash
# 1) 下载 1 次（sector erase；AXF 的 PT_LOAD 才是实际载荷）
pyocd flash --pack "C:/Keil_v5/ARM/PACK/.Download/NOVOSENSE.NS800RT7XXX.0.5.3.pack" \
  -t ns800rt7p65d_dhqfby2 -u "$DAPUID" -f 1000000 --no-config \
  -Oauto_unlock=false -Ocache.enable_memory=false -Ocache.read_code_from_elf=false -e sector \
  "reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.axf"

# 2) 定长回读 1 次（266244 = 0x41004），输出为本轮全新文件
python -m pyocd commander --no-config -t cortex_m -u "$DAPUID" -f 1000000 \
  -O auto_unlock=false -O connect_mode=halt -O resume_on_disconnect=false \
  -O cache.enable_memory=false -O cache.read_code_from_elf=false -O scan_all_aps=false \
  -c "status" \
  -c "savemem 0x08000000 0x00041004 <执行根>/m7r1_motor_readback.bin" \
  -c "status" -c "exit"

# 3) 显式复位/启动 1 次（开串口之后立即被动采集，见 §3）
pyocd reset -t cortex_m
```

工具与本机私有项：pyOCD 0.45.1、pack `NOVOSENSE.NS800RT7XXX.0.5.3.pack`、
target `ns800rt7p65d_dhqfby2`、COM5/115200/8N1、PS 5.1 或本机 Python。
`$DAPUID` 只从既存私有件读（长度 48，`sha256(uid)[:12] = d2f530dbd062`），
不枚举、不打印、不入库；`<执行根>` 是本轮**全新仓外目录**，不复用旧日志/旧回读件。
不"试连接以确认参数"：以上每一项都出自已成功的记录。

**下载器隐含复位不可单独观测**：`pyocd flash`/`Load` 自身会做 halt 与启动序列，
所以本包不宣称"物理复位总量只有 1"；只登记 §4 的显式复位为**唯一一次由我发出的
SYSRESETREQ**，其余下载器内部行为记为 `TOOL_INTERNAL_RESET_NOT_OBSERVABLE`。

## 3. 顺序（先后不可交换）

```text
S0 离线绑定  → S1 供电确认 → S2 下载(1) → S3 定长回读(1) + 逐字节比较
             → S4 开 COM5 并开始被动采集 → S5 显式复位(1) → 读启动横幅
             → S6 四条状态查询 → S7 配对表一轮（show/sample/四个 DMM 读数）→ S8 停点登记
```

- COM5 在**显式复位之前**就已打开并在读（S4→S5），否则会漏掉启动横幅；
  不用"必须连续静默 1200 ms"或"必须自发看到行尾提示符"这类已证不可靠的前置。
- 回读不等 = 身份未证：`S3` 的 SHA 必须等于 §1 的 BIN SHA 且**逐字节相等**，
  pyOCD 返回码与 commander 退出码都不单独作判据。不通过就停在 S3，不开串口、不发命令。
- 危险告警（`stack is close`/`stack overflow`/`HardFault` 等）**先落原始字节再判**，
  命中即 `STACK_DANGER_OBSERVED` 并停止；不为发命令而关闭 Sensor、改内核或压掉告警。

## 4. 前置（按本次读回判，全部是观测判据）

| 前置 | 读回来源 | 必须看到 |
|---|---|---|
| Safety 无故障 | `safety_state` / `safety_status` | `READY` 且 `fault 0` |
| Motor 静止 | `motor_status` | `armed 0` / `cur=0` / `tgt=0` / `pad 0` / 请求 LOW |
| STEP 确实没被授权输出 | 构建宏 + `step_status` + 关断记账 | 本目标 `STEP=0`，且 Motor/诊断两侧都无未确认关断；**不**只凭 `STEP_DIAG=0` 宣称全部 STEP 未输出，也不称这是示波器证明 |
| 驱动在禁能侧 | ENN 反馈（`safety_status` 里的 IO70/PC.6 观测） | 禁能侧电平 |
| 所有权/锁存 | `safety_status` | 无未知关断、无 diag/motor 关断锁存 |

新启动的 `drive UNCONFIGURED` 与 `IRQ CLOSED` **照实登记为正常启动事实**
（`tmc2209.c:2180` 启动即置 `TMC_DRV_UNCONFIGURED`）：不 apply、不开 IRQ、不确认极性、
不清故障、不手工 selftest 去凑状态。P0 不 arm、不 start、不输出 STEP/DIR/HIGH。

## 5. 只读命令白名单（逐字固定，采集器硬编码同一份）

```text
safety_state
safety_status
motor_status
step_status
current_cal show
current_cal sample 128
```

不发探测空行、不清屏、不用 `pin_status`、不重复 TMC 通信压测、不调 `run_fault`、
不 `config_save`、不 `apply`、不 `nominal`、不 `fault_reset`。白名单外的字符串在
写到线之前就被拒（自检 `P0T06` 证明写出字节数为 0）。

## 6. 一轮配对：一次 sample + 四个 DMM 读数

| 序 | 动作 | 记录 |
|---|---|---|
| 1 | UT39A+ 直流电压 4.000 V 档；黑表笔 H10-2（GND），红表笔 H10-1（CURRENT_ADC） | `Vadc_before` + 实际读数时刻 |
| 2 | 红表笔移到 H10-3（3V3） | `V3v3_before` + 时刻 |
| 3 | Agent 发 `current_cal sample 128`，完整原始响应落盘；前后各一次 `current_cal show` | 参数/来源/版本 + 原始件 SHA |
| 4 | 回读 H10-1、H10-3 | `Vadc_after`、`V3v3_after` + 时刻 |

两个节点各 2 次，**总 4 个读数**；时刻由读数人当时记录，人没记就如实 `UNKNOWN_TIME`，
**不拿日志时刻冒充测量时刻**。原理图与前端截图在本审查包 `context/`
（`NS800_RevA_20261019.pdf`，SHA `7b5d5325…05a0f`）：H10-1=CURRENT_ADC、H10-2=GND、
H10-3=3V3；主板 PH2 / ADCA CH15 / J1-3。
**全程电压档：不串表、不动 VM/电机相线/模块/供电接线/SW1/VREF。**
UT39A+ 型号与 4.000 V 档规格已确认，不再索取；TMC 侧 R110=0.11 Ω、VREF=1.37 V
属相电流设定，**不代入**这条母线 ADC 的零点或增益。

判读：名义 `raw × 3300 / 4095` 与前后人工读数并列，给逐行偏差与区间（不只给均值）。
实际 ADC 参考、量化/转换误差与 VDDA 均未证明，`H10-3` 也只是一个测点而非已证等价的 VDDA，
所以结论上限是 **`VOLTAGE_OBSERVATION_ONLY`**：偏差超出 DMM 误差**既不判 ADC 损坏，
也不判精度验收**；不产 MEASURED 系数、不改保护阈值、不外推相电流 RMS。

## 7. 额度（整包一次批准的范围）

| 动作 | 上限 |
|---|---:|
| 下载（已知 DAP/算法/载荷） | 1 次（失败也计入，不自动重跑） |
| 定长回读（0x08000000 起 266244 B） | 1 次（不重读、不扩范围） |
| 显式复位 SYSRESETREQ | 1 次（S5 唯一由我发出；下载器内部行为另记，见 §2） |
| COM5 打开 | 1 次 |
| 四条状态查询 | 各 ≤1 次 |
| `current_cal show` | 2 次（sample 前后各一次） |
| `current_cal sample 128` | 1 次 |
| DMM 读数 | 4 个（§6 那张表；每节点 2 个） |

读数不是有效数据时**不补值、不自动重采**，停在该步并登记。真实危险仍走既有硬件阻断；
**不例行要求拨 SW1、不反复证明供电/接线、不索取断电时刻回报**（既有偏好，本包沿用）。

## 8. 停止与降险约定

- 先落原始字节 → 再解释 → 后判定；证据落盘失败即停止业务与自动恢复。
- 前置任一条不成立 ⇒ 停在原地登记（不是"重跑一遍试试"）。
- **仅在已有启用/运动证据时**允许一次有界撤能（既有的 `motor_disarm`/急停路径），
  静止前置失败或单纯证据错误时**不**无关地发电机命令；不自动恢复、不二次烧录、不 `config_save`。
- 状态用词：已执行未通过记 `FAILED`/`PARTIAL`/`NOT_ACHIEVED` 并保留已执行动作清单；
  从未执行的子项才记 `NOT_EXECUTED`。
- 采集器一次失败即 `SystemExit`（自检 `P0T08` 证明只写了 1 次、不重发）。

## 9. 采集器的离线自检（本轮真实执行）

`python p0_voltage_observation.py --selftest` → `SUMMARY cases=11 pass=11 fail=0`
（`p0_collector_selftest.txt`）。它证明的不是板子行为，而是脚本自身的边界：

| ID | 判据 |
|---|---|
| P0T01/T02 | 白名单逐字就是那六条；不含任何特权命令 |
| P0T03/T04 | 模块作用域不 import 传输库；真实端口构造只在 `--execute-approved-p0` 分支里 |
| P0T05 | 横幅字节**先落盘**并带 SHA，才做判定 |
| P0T06 | 白名单外命令在写线之前被拒（写出 0 字节） |
| P0T07 | 读取有界：生产者不停也只能取 `MAX_BANNER_BYTES` |
| P0T08 | 未见帧尾即停，且只写一次（不重发） |
| P0T09 | 危险标记只在字节保存之后才据此停止 |
| P0T10 | 六条按固定顺序、每条恰好一次写 |
| P0T11 | 台账把 TX 尝试与结果分列 |

## 10. 一次批准文字（未来整包用）

> 批准执行本文件 M7-R1 P0 电压观察包：使用 5b564eb 构建的
> `e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da` motor 镜像（266244 B）
> 与 `1e34589f…c44f` 采集器；现场有人、电机未接、SW1 不变、不改供电与接线。
> 允许一次下载、一次 `0x08000000` 起 266244 B 定长回读并逐字节比较、一次开 COM5 且先建被动采集、
> 一次显式 SYSRESETREQ、四条状态查询各一次、`current_cal show` 两次、`current_cal sample 128` 一次、
> 万用表四个电压读数（每节点前后各一）。启动横幅按 `5.3.0 build Oct 29 2026 23:11:52` 逐字判；
> 新启动的 UNCONFIGURED/IRQ CLOSED 照实记录，不 apply、不开 IRQ、不清故障、不动电机。
> 真实失败、前置失效或额度用尽即停，不重试、不增动作、不补读数。

也可简写：**批准执行 M7-R1 P0 电压观察包**。

## 11. P1/P2 与本包的关系

P1 仍 `METHOD_NOT_READY`（缺项见 R1 交接：仪表电流档额定/串联压降资料、不带电断相的转接夹具、
实物 VM 与图纸 U9-8 对应确认），P0 不等它们。P2 `NOT_AUTHORIZED`：
本轮与 P0 都不产生 `MEASURED` 系数；`example_result_HOST_SYNTHETIC.json` 里的
`current_cal apply 1598291 671551 CONFIRM_MEASURED` 是工具自检合成值，**禁止下发实物**。
