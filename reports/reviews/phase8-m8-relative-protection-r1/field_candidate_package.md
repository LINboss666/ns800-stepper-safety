# NS800 M8-R1 现场候选包（SG/ADC 相对保护 + 碰撞独立保护）

包名：`phase8-m8-relative-protection-r1-FIELD-CANDIDATE`
状态：`NOT_AUTHORIZED` / `NOT_EXECUTED`（本轮 `hardware_actions = []`）
**本包所有数字都是提案，不构成授权**；任何一步（下载、回读、复位、串口写入、起转、写 Flash、
触发故障）都必须由操作者在包外单独授权。本包已"可执行"，但可执行的部分只有离线自检被真跑过。

## 0. 包里有什么（全部入库，可复核）

| 文件 | 作用 |
|---|---|
| `field/plan.json` | 唯一计划来源：阶段、每步命令、断言、额度表、三态处置 |
| `field/M8R1Field.ps1` | Windows PowerShell 5.1 监督器：合同检查(F-01..F-13, G-01)、额度记账、会话调度、场景矩阵判定 |
| `field/m8r1_serial_chain.py` | 串口会话执行器：假端口/真端口同一代码路径；原始字节先落盘；单一写者；总截止时间 |
| `field/fixtures/*.json` | 合成期望转写（正例 + 各失败注入）|
| `field/needles.json` | 每条被断言的文本 → 固件源码 `file:line` 出处 |
| `field/offline/<run_id>/` | 一次真实离线运行的全部产物（计划切片、假端口落盘字节、台账、`result.json`）|
| `host/field_selftest_console.txt`、`host/field_selftest_result.json` | 离线自检的控制台原文与机器可读结果 |

## 1. 绑定的输入身份（§4.1-F-01：源码提交与被测镜像提交分列）

| 项 | 值 | 由谁核 |
|---|---|---|
| 源码与验证链提交（`source_commit`） | `3275356a46e0b503888e9a4869da3b2b41f027a8` | S0 现场 `git rev-parse` |
| 被测镜像构建提交（`tested_image_build_commit`） | `3275356a46e0b503888e9a4869da3b2b41f027a8` | `build_manifest.json.code_commit` |
| 被复审交付 | `f939363449c528892fb3d9978d73e085cfa90c91` | 只读引用，不改动 |
| 构建工具 | `armclang V6.24`（日志原文行）+ UV4 5.43 | 构建日志 |

两个提交是**两个独立字段**，执行轮必须分别核对；只有构建确实取自该源码提交时才允许相等。
镜像散列不抄文档：S0 用 `Get-FileHash` 现场重算并与 `build_manifest.json` 记录值逐字节比，
不等即停（离线自检里这一步是真跑的，`S0|image_recomputed=…|equal_to_plan=YES`）。

镜像（`reports/firmware/phase8-m8-relative-protection-r1/`）：

| 目标 | 文件 | 字节 | SHA256 |
|---|---|---|---|
| rt-thread | `rt-thread.axf` | 1764928 | `c3528d843553d15ee32ddd53b838c258bbf32b642d6dc198e44b436ea36e8499` |
| rt-thread | `rt-thread.bin` | 288916 | `be99bd40d7d87e2ec1751bcdf85c373e51b42ab202beb3bde371112486bc07a9` |
| rt-thread-measure-a0 | `rt-thread-a0.axf` | 1795348 | `1e60762062d722b7c9d2d70ef7a102fb236860016b32d01840e28245f51d4f7c` |
| rt-thread-measure-a0 | `rt-thread-measure-a0.bin` | 299172 | `42487b18636a810a657eb4c62aac0101bb0875272ec7ef57a0adf94d2930b6e7` |
| rt-thread-motor-m1-candidate | `rt-thread-motor-m1.axf` | 1765420 | `e371cebc31b58455365c2fbb923dad470d7ad40185de496d49f00254e019fc2e` |
| rt-thread-motor-m1-candidate | `rt-thread-motor-m1.bin` | 289028 | `04ccfca5d8e7b30cc1685f6cfca3b65b0fc68f51f2c5e52f89bdda2b20fac59c` |

选定镜像：`rt-thread-motor-m1.bin`（唯一 `MOTOR=1/TMC=1` 的候选目标；另两个目标不含电机使能，
走不到 §4 的巡航学习阶段）。若审核要求先用默认目标做 S1–S3（只读 + 门禁拒绝），
分支里不含任何电机阶段。

**AXF / map / DWARF 的核对是离线符号读取**（本机对已归档产物做），不是设备动作，
不占任何现场额度（§4.1-F-08）。

## 2. 额度表（整数；超出即具名停机，重试额度为 0）

| 额度项 | 上限 | 说明 |
|---|---|---|
| `port_open` | **4** | §4.1-F-03：取消"全程只开一次"的自相矛盾。每个串口会话各开一次，会话内部绝不重开 |
| `download` | 1 | 仅选定镜像；单独授权 |
| `flash_readback` | 1 | 紧随下载之后的定长回读 |
| `explicit_reset` | 1 | 只在会话 1 开口之后做一次（§4.1-F-04 顺序） |
| `motor_run` | 3 | 会话 2 一次、会话 4 一次、会话 1 不需要；每步声明 `seconds` |
| `motor_run_seconds_total` | 15 | 按步累计，超过即停 |
| `config_save` | 1 | 只在学习+提交成功之后那一次 |
| `approved_reboot` | 1 | 只发生在会话之间，不持有串口句柄 |
| `retry` | **0** | 任何失败即停；不改参数再试 |
| `physical_collision` | **0** | R6：本轮不授权任何物理碰撞（§4.1-F-12） |
| `blackbox_clear` | **0** | 事故链读回只读 |
| `dmm_readings` | 0 | 本包结论不依赖人工电压读数（§4.1-F-02：不作例行断电/拔线/拨开关要求） |

额度在**动作之前**扣：`Invoke-Session` 先把该会话所有步骤声明的额度扣完，超了就在校验阶段停，
端口一次都不会被打开。离线场景 `QUOTA_MOTOR`、`QUOTA_REBOOT` 就是在证这一点。

## 3. 顺序与硬停点（§4.1-F-04）

```
S0 离线绑定 ──(不等即停)──► S1 唯一一次下载 ──HALT──► S2 唯一一次定长回读 ──HALT──►
S3 会话1：开口 → 显式复位 → 才读首字节 → 只读状态 → 静止期 diag_profile zero
S4 会话2：arm → target → start → (人工确认进入巡航) → learn → stop → disarm →
          (人工确认可信禁用) → tier → commit → config_save
S5 经批准的一次断电重启（会话之间，不持句柄）──HALT──►
S6 会话3：重启后只读核对（profile 已恢复 且 模式仍 MONITOR_ONLY）
S7 会话4：真实保护演示（带负载一次观察，允许自然触发）→ 事故链读回 → 人工恢复
```

## 4. 串口链纪律（§4.1-F-05，复用 P0 已证打法）

1. 端口在构造时一次配齐（`115200 8N1, timeout=0, write_timeout=2, 无流控`），
   **打开之后不写任何串口属性**——Windows pySerial 的同值赋值也会触发 `SetCommState`，
   P0 轮实测会损坏字节。本包有两条判据：AST 静态扫描（`--check-static-port-discipline`，
   合同位 `F-05`）与假端口对属性写入的当场报错。
2. **原始字节先落盘**（`NN_<as>.rx.bin`），判读只从落盘内容出发；另存同名 `.txt` 供上层复核。
3. **单一写者**：一个会话一个进程持有端口；不 flush、不重开、不重试。
4. **总截止时间**（整数秒）+ 逐步截止时间；到点无字节 = `NO_BYTES_BEFORE_DEADLINE`，
   超总窗 = `TOTAL_DEADLINE_EXCEEDED`，两者都是具名停机，离线场景 `PORT_SILENT`、
   `TOTAL_DEADLINE` 各证一条。
5. `config_load` 会改生产状态，**不是只读命令**，已列入只读阶段的 `forbidden_commands`
   （合同位 `F-06`）。

## 5. Flash 参数区三态分列（§4.1-F-07）

| 态 | 处置 |
|---|---|
| `absent` | 只允许 defaults；不得把 defaults 说成"已持久化 profile"；学习/提交从零开始 |
| `v2` | 走 v2→v3 迁移。**迁移只在主机链证过**，现场在第一次 `config_save` 之前不得宣称 profile 已生效；重启后若仍读不到 profile = `PROFILE_NOT_RESTORED_AFTER_SAVE`（离线场景 `PROFILE_V2_LOST` 在证这一条）|
| `v3` | 直接加载，仍要 `diag_profile show` 核对 id/rev 与 live signature |

## 6. 学习/提交与阈值的现场约束

- 学习只在 `arm → target → start` 真正进入巡航之后（人工握手 `cruise_reached`，
  合同位 `F-09`）；模式必须是 `MONITOR_ONLY`（固件侧拒绝，REL16/REL38）。
- 提交只在停转、解除武装并核实**可信禁用**之后（人工握手 `trusted_disable_verified`）；
  固件侧 `commit` 在最终静止事务内重新核资格、签名与来源（`apps/diag_relative.c:1499`）。
- 本轮**只学单方向**，`DIR` 只允许 0 或 1；`dir=2` 标签不再出现（`F-10`，
  固件侧 `learn REFUSED: DIR=2 not supported this round`）。
- 目标频率 ≤ 1000 Hz（`F-11`）；学习窗口取自已验证运动区间的 SG 码，
  `plan.json` 里 `260..340` 只是参数格式示例并写明 `window_basis`；
  学习阶段不碰 IRUN（现值 20）。
- 生成的阈值（`tier`/噪声裕量派生值）永远标 `FIELD_UNVERIFIED`，直到现场真跑过一轮。

## 7. §4.2 演示路径（阶段与判据一一对应）

| 步 | 命令/观察 | 判据 |
|---|---|---|
| D1 真实保护 | 带负载一次起转 + 10 s 被动观察窗 | 出现 `[DIAG] REL TRIP r=`；**无触发只记 `NO_TRIGGER` 观察，不算失败也不许制造触发** |
| D2 故障闩锁 | `diag_status` | `[DIAG] rel LATCH r=` 行存在，`id/rev/ms/post_rc` 可读 |
| D3 解除武装观察 | `motor_status` | armed=0 且状态回到停转 |
| D4 首因 + 新日志 | `blackbox_status`、`flash_logstat` | 首因槽与新日志计数可读 |
| D5 故障态拒绝武装 | 再发一次 `motor_arm` | 被拒绝（`REFUSED`），且只发一次 |
| D6 人工恢复 | `fault_reset` → `system_selftest` | 到 `READY`，`armed=0`，模式 `MONITOR_ONLY`（不自动再武装）|
| D7 持久化 | 一次 `config_save` + 经批准重启 | profile 恢复且模式仍 `MONITOR_ONLY`（D 段与 §5 的 v2/v3 处置一致）|

## 8. 离线自检（本轮真跑，全部命中期望）

```
powershell -NoProfile -ExecutionPolicy Bypass -File field\M8R1Field.ps1
```

- 环境：Windows PowerShell 5.1；`python` 为主机解释器；不开任何串口、不导入 pyserial/pyOCD 设备对象。
- 计划合同：`F-01…F-13` 与 `G-01` 共 14 位在 SHIP 计划上全 PASS。
- 元探针 9 个全 META-OK（每个只在指定合同位制造一次失败且无附带损伤）：
  `F-01_MIXEDCOMMIT`、`F-02_ROUTINE_POWER`、`F-03_TWOOPEN`、`F-06_CFGLOAD`、
  `F-09_ORDER`、`F-10_DIR2`、`F-11_HZ`、`F-12_COLLISION`、`F-13_INVENTED_NEEDLE`。
- 场景矩阵 10 个全 META-OK：
  `POS → COMPLETED`；`READBACK_MISMATCH → STOP:READBACK_HASH_MISMATCH`；
  `PORT_SILENT → STOP:NO_BYTES_BEFORE_DEADLINE`；`TOTAL_DEADLINE → STOP:TOTAL_DEADLINE_EXCEEDED`；
  `QUOTA_MOTOR → STOP:QUOTA_EXCEEDED:motor_run`；`QUOTA_REBOOT → STOP:QUOTA_EXCEEDED:approved_reboot`；
  `MANUAL_DENIED → STOP:MANUAL_GATE_DENIED:cruise_reached=no`；
  `PROFILE_V2_LOST → STOP:PROFILE_NOT_RESTORED_AFTER_SAVE`；`STUB_MISS → STOP:STUB_MISS`；
  `REAL_UNAUTHORIZED → STOP:REAL_TRANSPORT_REFUSED`。
- `M8FIELD RESULT: PASS(离线自检, 无设备动作)`，脚本退出码 0；台账原文见
  `host/field_selftest_console.txt` 与 `host/field_selftest_result.json`
  （后者是 UTF-8 权威件；前者是控制台字节，中文按 cp936 渲染）。

**这些判据证明的是计划与门禁逻辑（含额度、顺序、拒绝路径）自洽，不证明任何板上的事。**
真机行为仍是 `NOT_VERIFIED`；`safe_for_enable = false` 不变。

## 9. 执行轮开始前必须补齐的项（现在没有）

1. `cruise_reached` / `trusted_disable_verified` 的人工握手在现场要由操作者口头+读数确认，
   离线跑里用的是夹具值，不是证据。
2. 学习窗口的 SG 码要换成 M4 已验证巡航区间实测值。
3. 若执行轮换了镜像，`plan.json.identity_binding.image` 的 bytes/sha256 与
   `tested_image_build_commit` 必须一起改并重跑本自检。
