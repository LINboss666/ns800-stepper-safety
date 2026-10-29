# Phase 8 M6：离线软件与证据收口（无硬件动作）

日期：2026-10-29（Asia/Shanghai）
分支：`debug/tmc-uart-rootcause-20261028`
document_base / source_base：`a052e8e3dc11a4a983fcaecf5fd36ba9e4139af4`
code_commit（产品源码）：`cf133d6a5d539bb5a7c3b9da4d50d53dc3b47055`
测试/证据提交（脚本补正）：`d8ee03c9bb547b26c9362b98c69e8a14f6ed53b7`；构建镜像/清单提交：`9241828d555e1f376bc80ef35521af89f6001d71`
状态：**M6_OFFLINE_CLOSEOUT_COMPLETE / NEW_IMAGES_BUILT_NOT_FLASHED /
FIELD_CANDIDATE_OFFLINE_FIXED_NOT_EXECUTED**
独立复审（2026-10-29）：工作 A/B/C 接受；工作 D 原候选暂不放行，D-1/D-2/D-3 已离线补正，
未执行任何设备动作，也未获现场放行。
上一阶段：`reports/agent_handoffs/phase8-motor-m5-fault-log.md`（M5 最终收尾见 §12）

本包未执行任何硬件动作：无串口/DAP/仪器、无设备枚举、无上电/复位/烧录/读目标内存、
无目标命令、无电机/SW1/线路操作。板上最后已证镜像仍是 M5 候选，未因本轮新 BIN 改写。

## 1. 产品改动（最小面）

- `applications/safety_state.c`：`safety_trip_cmd` 改为**上下文检查优先**——入口先调用
  `ss_log_ctx_ok()`，ISR / PRIMASK 窗口 / 无线程对象时直接返回，不解析参数、不打印；
  合法线程内再做 `argc==2 && argv[1]=="CONFIRM"` 校验并保留 usage；合法参数仍只调用
  一次 `safety_post_event(EVT_SOFT_FAULT)`，返回后打印投递摘要。未改保护、事件整理、
  epoch/owner/恢复票据、故障码或黑匣子逻辑。
- 其余产品源未改；三目标宏策略、SG/电流/振动判据、Flash 布局、stop_priority 核心未动。

## 2. 工作 A：safety_trip 组合验证

`tests/host/ps_main.c` 复用真实 `safety_state.c` 与 S1 夹具，新增/保留：

- PS17a：合法参数 → 恰 1 次事件，摘要不含 persisted/written/stall 误导；
- PS17b：缺参、多参、错误大小写/尾随 → 0 次事件，普通线程仍有 usage 对照；
- PS17c/d/e：ISR、PRIMASK、无线程对象 + 合法参数 → 静默、0 事件、0 捕获；
- PS17f/g：ISR、PRIMASK + 非法参数 → 静默、0 事件、0 捕获；
- PS17h：无线程对象 + 合法与非法参数 → 静默、0 事件、0 捕获；
- PS17e：事件投递失败（-RT_EIO）→ 保护仍执行（`safety_trip_pending()==TRUE` 断言）、
  摘要不声称持久化。

S1 runner 已登记 PS17f/g/h；最终源码复跑 `target 58/58`、`host_ovr 73/73`，反向
`rev_safety_trip_skip_post` 仍具名命中 PS17a/PS17e，rc=0。证据：
`reports/reviews/phase8-motor-m6-offline/s1_normal.txt`、`s1_reverse.txt`。

## 3. 工作 B：M5 十九份最终响应字节归档

范围：`reports/reviews/phase8-motor-m5-fault-log/cp1/final_pass/index.json` 列出的 19 份
最终响应；未重采设备，未改原 index、原 bin 或历史提交。

- 19/19 原始本地文件仍存在且长度/SHA 与 index 相等 → 全部 `ORIGINAL_LOCAL_FILE`；
  base64 归档在 `reports/reviews/phase8-motor-m6-offline/m5_raw_recovery/`。
- 交叉登记：基线 Git blob 3/19 精确；16/19 被 CRLF→LF 标准化，确定性 LF→CRLF 可恢复
  到 index 长度/SHA（manifest 已逐项记录，不声称“仓内一直保持原始字节”）。
- 已从写好的 `.bin.b64` 重新解码：19/19 长度/SHA 与 index 相等。
- 三批 `flash_dump`（009–011）保留 CR、NUL 与交错；用同一 `m5_log_archive_check.py`
  重建 `ARCHIVE=ACCEPT rows=300 batches=3`，CSV 17314 B / SHA-256
  `c26cb752d79051f5914350424af9a28adc4a8ceb22e3bd18c2af77c9abb7567a`，与
  `cp1/new_log_final3.csv` 逐字节相等。
- 方法标记、原 index 长度/SHA、Git blob/字节 SHA、b64 解码长度/SHA 均在
  `m5_raw_recovery/manifest.json`；新增文件只写仓库相对路径。

## 4. 工作 C：CLI / 启动身份 / 关键短行

### C1 严格 CLI（真实生产静态命令函数）

`tests/host/run_m6_cli.sh` 以 `PS_CLI_ONLY` 编译 `ps_main.c`，target 与 host_ovr 各
10/10 PASS，rc=0：

- PS18a/a2：`motor_dir` 缺/多参、10、1junk、负号、+1、0x1 拒绝且 DIR 写计数/值不变；
  0/1 各恰好一次写入。
- PS18b/b2：`motor_target` 负号、尾随、4294967296、上界 20001、多参拒绝且 target
  不变；20000 最大值接受、0 接受。
- PS18c/d/e：`motor_arm/motor_start` 额外参数拒绝（HIGH 写/事件/armed/state 均不动）；
  host_ovr 正向 arm→target→start 到 300 Hz，stop/disarm 归零；target 变体由宏门拒绝
  arm 且有正向对照。
- `motor_stop/motor_disarm` 的降险合同未因验参测试被修改。

### C2 启动 required TMC 身份

`tests/host/io_main.c` 新增 IO11a–f：init 失败、IOIN 读取失败、VERSION=0x20/0x00/0xFF
均不得进入 READY；VERSION=0x21 对照按既有合同进入 READY，但不自动 arm/STEP/HIGH。
I/O 四变体（off/on × target/ovr）全部 PASS；证据 `io_behavior.txt`。

### C3 关键状态短行

- PS18f：最大 target 下 `[MOT] cur_hz=`/`[MOT] armed=`/`[STEP] dev=`/`[SS] state=`
  行均 ≤110 B，无内嵌 NUL；
- PS18g：最长 safety 枚举 `state=FAULT_LATCHED` 行 ≤110 B；
- PS18h：注入 STEP 关断失败后 `[MOT] stop result=api-failed(output-UNKNOWN) src=disarm
  rc=-12 latched=1` 行 72 B，负 rc 与尾字段完整。宿主 shim 定义 `RT_EIO=12`，因此
  行内为 -12；断言使用符号值，不声称目标 errno 数值。
- 未把全仓历史中文说明长行新增为硬门；本包未发现与改动直接相关的 128 B 真实截断。

## 5. 工作 D：未来现场脚本候选（离线）

新增 `reports/reviews/phase8-motor-m6-offline/m6_field_candidate.py`，保留 M5 原脚本。
状态 **FIELD_CANDIDATE_OFFLINE_FIXED / NOT_BOARD_EXECUTED**；独立复审的三项阻断已修，
本轮仍无现场批准。候选 **Git blob SHA-256（远端回读）：`9faaca5904ccd3c2618c4a152cc200f4f756ff45aef3e671cf2815356b706649`**；
工作树 CRLF 副本 SHA-256 `6d0385064c09383f5e8dd30392ce63c56b3242f24dba37cbe9c10f36dc3d2233`
（仅换行差异；两种字节均执行了 32/32 自测，结果分别归档）。

复审补正（D-1/D-2/D-3）：

- **D-1 统一时钟**：`Session`、`FDevice`、`FakeSerial` 共用同一个 `FClock`；`SerialLink`
  在写前用剩余预算设置 `write_timeout`，每次读前重算 `timeout`；`start` 读最多 1.5 s、
  trip 读最多到 start+4 s，兜底 disarm 计划 start+5 s；窗口被挂起读写耗尽时仍做一次
  短有界尝试并记录 `cleanup_late`。M6D13（arm 写阻塞 10.1 s）证明阻塞时间进入同一截止，
  M6D14/M6D15 覆盖 start 写超时与 start 后读阻塞。
- **D-2 首错与清理分离**：`disarm_once` 捕获传输/`OSError`/查询异常，清理错误与 traceback
  独立记录；`run_fault` 保留原始主异常与 traceback；撤能无法用
  `request=DISABLE(LOW)` + `pad=0` 确认则 UNKNOWN，绝不回成功。M6D16–D18、M6D23、M6D30。
- **D-2e 证据失败立即停业务**：普通 `q()` 在原始响应保存失败时立即抛 `EvidenceError`；
  仅清理内部 `motor_disarm`/`safety_status` 使用 `evidence_required=False`，保证降险仍发出。
  证据失败发生在 arm 前不发任何电机命令；发生在 arm/start 后只做一次必要清理，不继续
  trip/恢复；disarm、读回或恢复阶段的证据失败都在 `fault_reset`/`system_selftest` 前停止，
  `--allow-recovery` 不是忽略异常的开关。M6D24–M6D31 覆盖四条实际落盘故障接缝。
- **D-3 规范 arm 判定**：arm 后读取 `motor_status`，要求恰好一组
  `state=0/armed=1/cur_hz=0/tgt_hz=0` 字段；明确拒绝文本、armed=0、字段缺席/重复/冲突
  均不得发送 target/start；不依赖成功摘要子串。M6D19–M6D22 覆盖拒绝文本、状态冲突、
  回执丢失但状态确认/无法确认。
- arm/start 前仍先设置“输出可能已打开”责任位；应答丢失不假定命令未执行；
- 撤能确认以 `safety_status` 合同字段为准；普通与异常路径共享一次 `motor_disarm`；
- 新日志完整读回：`flash_dump` 分批（≤100）覆盖新槽，校验槽号连续、session、
  pre event0/step>0、post event9/step0、时间单调；`accepted==completed` 判排空，
  `queue_dropped=51` 作为重试计数不参与完整性；BB `write drops` 取增量；
- 真实运行必须提供 `--archive-state`：M5 归档未接受、清区后第二份备份缺失或
  `COPY_VERIFIED.json` 不存在时，在导入 pyserial/打开串口之前拒绝，不发任何电机命令；
- 只有读回完整且撤能已确认时才允许显式 `--allow-recovery` 执行 fault_reset/selftest；
  任何异常不得自动清锁存；参数错误/`--selftest` 不导入 pyserial、不构造 SerialPort；
- 每个收发先落 `--out-dir/NNN_*.bin` 与 `raw_index.jsonl`。

离线自测 **32/32 PASS**（`field_candidate_selftest.txt`）；同一脚本的 Git blob 字节
（LF）另跑一次 **32/32 PASS**（`field_candidate_selftest_git_blob.txt`）。原 25 个用例
身份保留，新增 M6D25–M6D31 覆盖证据落盘的四条实际接缝；M6D18/M6D24 按要求收紧到
“证据失败立即停业务”的判据，未删除。首个补正失败运行以**转录**形式保留为
`field_candidate_selftest_first_failure.txt`（20/24；临时原文件被覆盖，文件头注明转录
而非设备证据）；D-2e 首次失败保留为原始控制台日志
`field_candidate_selftest_first_failure_d2e.txt`（23/25，M6D18/M6D24 失败）。候选未上板，
不声称 Windows/USB 硬实时或物理停机上界。

未覆盖/明确不属候选：旧日志归档、清区、两份备份门由 M5 脚本负责并已有 FS01–FS08
自测；候选不执行 fault_reset（除非未来显式批准并传 `--allow-recovery`），不重开串口、
不重发 trip、不复位、不自动恢复。

## 6. 三目标构建与镜像（仅构建，NOT_FLASHED）

UV4 5.43.1.0 / ARMCLANG V6.24，三目标各 Rebuild All 一次，均 0 Error / 0 Warning；
宏探针：默认/A0 `0/0/0/0`，motor `MOTOR=1 TMC=1 STEP=0 LIMIT=0`。

| Target | Program Size | BIN bytes / SHA-256 | AXF bytes / SHA-256 |
|---|---|---|---|
| rt-thread | Code=180586 RO=78306 RW=2188 ZI=83784 | `reports/firmware/phase8-motor-m6-offline/default.bin` 259468 / `ccfae5e37cd010e873f3bf1a52c04e5925840d3e9f869a47d3514946bfbc1eec` | `rt-thread.axf` 1667756 / `377b4e059ebc42aeea999d1d7a9a749e65a57339e86105868ce65b0076d84f96` |
| rt-thread-measure-a0 | Code=187882 RO=81266 RW=2188 ZI=91504 | `measure-a0.bin` 269724 / `6458c8417471bed1ea760a9f4a20949efd45056f13e6177f2913afade42b70c1` | `rt-thread-a0.axf` 1698176 / `9110c5e38d98cab521987d83057f7ae9438e634c74db67011b419622d645de0f` |
| rt-thread-motor-m1-candidate | Code=180818 RO=78186 RW=2188 ZI=83784 | `rt-thread-motor-m1.bin` 259580 / `a1ba8226c5d09578c111b61a0b12a7ac182d572db01cc945081c51185b837f7b` | `rt-thread-motor-m1.axf` 1668244 / `b9bb80ffaa5b7f9a153296a995d21b2e6059e1e6504d3f6c1452315f287af16a` |

- 横幅：`build Oct 29 2026 11:20:17` / `11:20:33` / `11:20:45`。
- manifest：`reports/firmware/phase8-motor-m6-offline/build_manifest.json`
  （code_commit `cf133d6…`，project 路径、Target、宏探针与 source scope 均已绑定）。
- 三份 Keil 完整 stdout 已归档：`reports/reviews/phase8-motor-m6-offline/build_default.log`、
  `build_a0.log`、`build_motor.log`（原始 2026-10-29 11:20 构建；因 `*.log` 被忽略而
  force-add，未为补文件重复构建）。
- **NOT_FLASHED**：板上最后已证仍是 M5 候选
  BIN `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`、
  AXF `8c5bc7bf1efa786013c767ae1e09415ad0d5d5db3e38069c8aeebabf7cebae40`；
  不因工作树存在新 BIN 改写 on-board 身份。

## 7. 测试执行与未重跑

已执行（最终源码）：

| 链 | 结果 |
|---|---|
| S1 target / host_ovr | 58/58、73/73 required PASS，rc=0 |
| S1 反向 | rev_motor_off 5/5、rev_safety_trip_skip_post 1/1，rc=0 |
| I/O 四变体 | 33/35/46/48 required PASS；非法宏值拒绝编译；rc=0 |
| CLI strict target/host_ovr | 10/10 each，rc=0 |
| 现场候选自测 | M6D01–M6D31 32/32 PASS（原 25 + 新增 7） |
| M5 原始字节 | 19/19 解码相等；300 行 CSV 逐字节相等 |
| Keil | 三目标 Rebuild All 0/0 |

NOT_RERUN（说明）：S2 并发链（stop_priority/HAL/persistence 未改）、V1/V2（safety_state.c
在其中有桩，未改 tmc2209/motor 语义）、SD 链（sensor_service/blackbox 未改）、A0 采集门
（未改采集/导出逻辑）。脚本补正轮未重跑 S1/I/O/CLI/S2，未重编 Keil；上表前五项沿用本轮
产品源码未变时的既有结果。GCC 主机诊断与 Keil 0/0 分列；不声称 FinSH 函数指针类通用警告
为主机零警告。

## 8. 当前接受对象与边界（不得被离线结果升级）

- M4 有限运动已接受（功能结论，不外推到安全验收）。
- M5 软件事件停机功能已接受：300 Hz 一次 safety_trip、FAULT_LATCHED code9、操作者
  看到轴停止；无物理时延证明。
- M5 新日志功能已接受：session1、300 帧、pre200 event0/step300、post100 event9；
  三批原始导出重建 CSV 逐字节相等。
- M5 恢复主体通过（MANUAL_CLEAR→required selftest→READY，未自动 arm/start），但
  **故障态 `motor_arm` 拒绝命令未实机单独执行，完整恢复仍 PARTIAL**。
- `queue_dropped=51` 保留为提交失败/队列满重试计数，不等于 51 帧丢失；不得清洗或改写。
- MONITOR_ONLY 是多源诊断引擎模式；硬件 DIAG IRQ 当前 OPEN 但真实物理触发未验证，
  不能写“DIAG 物理路径处于 MONITOR_ONLY”。
- ADC 仍 THEORETICAL，SG 阈值/有效速度负载范围未标定，振动量含约 1 g 重力；
  `PROJECT_SAFETY_ACCEPTANCE_PENDING=OPEN`。
- 工作 D 候选仅完成离线补正，**未获现场放行**；32/32 自测不代表板级超时、撤能或物理
  停机行为，未来使用必须绑定新的具体工作包并重新批准。
- 本包离线测试、候选脚本与新构建**不能**升级上述实机缺项。

## 9. 下一轮现场准备（一页表，不索取材料/不要求操作）

| 项目 | 现场前需具备 | 本轮状态 |
|---|---|---|
| 母线 ADC 独立电流基准/前端校准 | 独立基准或已知负载的电压/电流对照，明确与相电流测量的区别；沿用 VREF1.37/R110 信息但不得据此伪称实测 | PENDING；本轮未接设备、未标定 |
| SG/融合判据标定 | 在最终速度、电流、负载下的真实异常闭环，记录阈值有效范围；保持 MONITOR_ONLY 直至证据成立 | PENDING；不擅自切 ACTIVE |
| DIAG 物理触发 | 明确安全的触发方法与测量点；IRQ OPEN 不等于实测；禁止带电短接模块输出 | PENDING；本轮未设计/未执行 |
| 故障态 arm 拒绝 | 可在未来获批的静止故障场景顺带执行，无需为该条重新转电机 | PARTIAL 来源；未执行 |
| 示波器/A0 | 按用户决定暂缓，不作为下次标定软件准备的硬前置 | DEFERRED |

下一轮建议的最小现场范围（需另行批准，不在本包执行）：使用本轮 motor 候选镜像时先
做身份回读/静止前置；只在已有故障场景内补 fault-state arm 拒绝；ADC/SG 标定另行
设计，不与故障注入混在同一会话。

万用表已确认 **UT39A+**（不再询问型号）；官方规格含 4 V 电压档 1 mV 分辨力、400 mA
直流档 0.1 mA 分辨力。方法必须纳入量程、仪表误差、串联压降与实际采样支路；母线直流
电流不等于相电流 RMS，IRUN/VREF 计算值不得标成 MEASURED；驱动禁能读数只作禁能基线，
不能未经验证定义为“零电流”。

## 10. 证据与隐私

- 原始/汇总证据：`reports/reviews/phase8-motor-m6-offline/`（`s1_normal.txt`、
  `s1_reverse.txt`、`io_behavior.txt`、`cli_strict.txt`、`field_candidate_selftest.txt`、
  `field_candidate_selftest_git_blob.txt`、`field_candidate_selftest_first_failure.txt`、
  `field_candidate_selftest_first_failure_d2e.txt`、`test_summary.md`、三份 build_*.log、
  `macro_probe.c`、`m5_raw_recovery/`）。
- 新文件仅含仓库相对路径；未复制本机私有绝对路径、端口、仪器序列号或凭据。
- 本轮云端 52 MiB 交接 ZIP 未更新；仅交仓库级交接与下次打包摘要。
