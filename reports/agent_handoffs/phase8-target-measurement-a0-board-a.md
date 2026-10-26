# 交接：phase8-target-measurement-a0-board-a —— A0 板级执行（CP0 离线 → CP4 采样，止于证据身份门）

任务书：`NS800_E40_Search_Review_and_A0_Board_Package_c06be762_20261026.md`
SHA-256 `3cfdf310ace04277bab176acf465a8a7feb33deba4430542860c3321f492ba00`
日期：2026-10-26（Asia/Shanghai）　document_base（开工）：`81c366238f72dbd9fbcb3f0f91f55e9fade4bf34`
授权：用户一次批准整包（含义等同 §12 文字，含 A0 下载与固定采样）；CP1 前置由现场操作者当天确认。

| 项 | 实况 |
|---|---|
| CP0 离线前置 | 完成（已单独提交推送） |
| CP1 现场与供电 | 完成：SW1 切阻断位、接 USB、开 24 V；**供电过程 1 次**，无异常 |
| CP2 双读备份 | 完成：六道门全通过（门5 修正判定器后）；快照 `BOARD_FLASH_SNAPSHOT_20261026` |
| CP3 一次下载 | 完成：`Erase Done / Programming Done / Verify OK`；回读 261100 B **逐字节等于已接受 measure-a0.bin** |
| CP4 串口+启动+采样 | 执行后**止于 §9 证据身份门**：51 条命令 0 超时，但 **CSV 被目标控制台剪断** |
| CP5 收尾断电 | 完成：现场回报"关了"（本地记录 2026-10-26 18:35 +08:00），先关 24 V 再拔 USB；SW1 仍阻断位（USER_REPORTED） |
| 测试／构建 | 0／0（主机链 NOT_RERUN、Keil NOT_BUILT；硬件动作据实登记，不再写 `hardware_actions=[]`） |
| 生产改动 | 无；四宏继续 `RT_FALSE`；冻结引用未动 |

## 1. 本轮得到的确证（目标实测，非主机模型）

1. **DWT 周期计数器在本板本构建上存在且确实递增**：`spm init` → `counter=COUNTER_OK demcr=0x01000000 ctrl=0x40000001 a=612446415 b=612447085（Δ=670）load=399999 guard=READY`。这条长期开放项在目标侧落定；但 `clock_basis=CONFIG_READBACK` 仍**不是**外部频率证明。
2. **17 次真实关断结果一致**：17/17 `DRV_ENABLE LOW verified` + `ENN feedback OK` + `motor_rc=0 diag_rc=0`，并触发黑匣子（`[BB] trigger code=8 session=2 pre_n=200 post_eff=100`、`[SS] ==> FAULT_LATCHED code=8`）——含外部 SPI 事件写。
3. **批 3 的 `motor_disarm` 真实到达并产出 1 条 off_attempt 样本**（`proof epoch=19`，`DISARM … LOW verified / ENN feedback=OK`），不是 `NOT_REACHED`。
4. **批次语义与清理语义正确**：`records=1 / 16 / 1`，每批 `in_flight=0 dropped=0`；收尾 `dump records=0`、`status state=0`，`clear_records` 未改 epoch。
5. **§8 的 `spm cap` 判据满足**：schema=2、slots=64、record_bytes=120、array_bytes=7680，四宏运行期 `MOTOR_HW=0 STEP_DIAG_ISO=0 TMC_CFG_HW=0 LIMIT_EN=0`。
6. **下载身份有两条独立证据**：回读逐字节等于 `358fd81a…`；启动横幅 `5.3.0 build Oct 25 2026 20:01:04` 与该镜像构建时刻一致。
7. **意外 HIGH/RUN 未发现**：6 处 `HIGH` 全是预期语义（`request=DISABLE(LOW) → expected_ENN=HIGH, FB=1 → verdict=OK`）。

## 2. BOARD-A-01（阻断 A0 时长证据的目标侧缺陷）

- **现象**：每条超过 126 字节的 `rt_kprintf` 输出被剪成**恰好 126 字节**并漏出 `0x00`，行尾分隔符丢失。`spm dump` 头行断在 `…owner_after,phase_af`；**每条 CSV 记录都在 126 字节处断**（b2 的 16 段全部 len=126，尾串 `…,COUNTER`）。
- **机制（配置/源码实证）**：`BSP/rtconfig.h:107 #define RT_CONSOLEBUF_SIZE 128`；`applications/sp_measure_a0.c` 的头行(:563)与每条记录(:582)各用**一条** `rt_kprintf`。b2_dump 实测 2267 B = 17 段×126 + 摘要。
- **波及**：51 条响应中 **33 条含 NUL**，被剪断的行 **35** 行。
- **后果**：丢失的正是 **CSV 第 39–47 列**（`clock_basis/body_valid/enclosing_valid/invalid_reason/gates/guard/guard_load/dropped_at_publish/complete`）⇒ §9 的纳入判据（`COUNTER_OK 且 body_valid=1 且 gates=0x0f 且 invalid_reason 仅 0x0100`）**无法在目标数据上执行**。前 38 列（含 `pre/body_begin/body_end/post`）可见，但没有有效性字段 ⇒ **不产出任何"有效时长统计"，不换算 µs**。
- **状态记法**：`A0 记录有效性与时长统计 = NOT_ACHIEVED`（命令确实跑了、槽确实写了、导出确实发生了，缺陷在导出通道）——不写 `NOT_EXECUTED`，也不写 PASS。
- **为何主机链没抓到**：夹具 stdout 无 `RT_CONSOLEBUF_SIZE` 裁剪，TMA 台账"47 列完整"只在主机成立。**教训：导出通道本身的长度限制只能在目标上验。**
- **本轮不可复得**：操作包 §2.1 要求每批 `dump→保存/核条数→clear_records`，本轮按序执行，三批记录已清（收尾 `dump records=0` 为证）。重跑需突破 17 次 trip 上限并追加真实关断/黑匣子写 ⇒ **不在本包授权内**，未重试、未补跑、未改数。

## 3. 快照与恢复输入（片内 Flash）

- 双读一致：两份各 1048576 B，SHA-256 `b89f8a7cde7b944a5b5425f512a715d27a17c2e6c25822f36b2205bbefd0cac2`；两次读来自同会话内两条独立 `savemem`（缓存与 ELF 代读均关闭），未用复制造第二份。
- 向量初筛：MSP `0x2000CDD8`（在链接器 IRAM 内、4/8 对齐）；复位向量 `0x0800051D`（Thumb=1，入口在片内 Flash）；非全 0/全 FF。
- 内容边界：最后一个非 0x00/0xFF 在 `0x0802B14E`，4 KiB 粒度唯一有内容段 `0x08000000..0x0802BFFF`（180224 B）——**完全落在新映像之内**，故板上旧应用字节已被覆盖，其唯一存留形式就是这份快照与它的 HEX。
- **从芯片读到的构建戳**：整 1 MiB 内唯一一处 `Sep 26 2026` @`0x08029CAB`，紧随 `00:00:39` ⇒ 对上 `build_c.log`(00:00:44) 与 `flash_c.log`(00:01:24)：**板上原存的是第二次 e40 时代烧录的映像**（不再是口述）。标记词 `[BOOT]=15 / safe-gpio=1 / startup-selftest=1 / motor_arm=3 / tmc_uart_probe=1 / blackbox=11`，而 `spm / schema / stop_priority / step_diag / io70` 全为 0 ⇒ 早于 Phase 8 的 IO70/S2/A0 工作。
- 边界：这是**内容级线索 + 直接读回**，不证明它与 `e40a9b9` 源码树重建出的映像逐字节相同（当年工作树身份缺口仍开放）。
- 恢复输入：`…_restore.hex` 3014958 B `b3ac97b9…`；类型 00=65536、04=16、01=1；正确测试下格式/校验和 0 错、无重叠越界空洞；解码回 1 MiB 逐字节等于快照。恢复工程 `restore-snap.uvprojx` `016d6629…`（相对下载副本仅 2 行差异）。**恢复下载 NOT_EXECUTED**。
- 保存：D: 与 C: 两个不同分区的目录，复制后散列一致；**不上传**原始快照与 HEX；**未**声称异介质备份。

## 4. 硬件动作台账（据实）

探针列表 **2 次**（第 1 次被主机 GBK 编码打断致"是否只有一个探针"不可判定，故加 UTF-8 重试——偏差登记，未按"一次"美化）；调试连接 **3 次**（CP2 halt、CP3 halt 回读、CP4 attach+`reset sysresetreq`）；CPU halt **2 次**；系统复位 **1 次**（SYSRESETREQ，未用 reset halt，未另发 continue）；擦写 **1 次**（UV4 `-f`，覆盖 `0x08000000..0x0803FBEB`）；`savemem` **3 次**；串口打开 **1 次**（115200 8N1，DTR/RTS 打开前即为 False；开端口未诱发启动，`boot_like=False`）；msh 命令 **51 条**（0 超时、0 合同拒绝）；`spm trip` **17**；`motor_disarm` **1**；`spm init` 写 DWT/DEMCR **1**；`clear_records` **3**。
禁止项全部未触碰：无 `arm`/`motor_start`/`ramp`、无许可 HIGH、无 DIR/STEP 开启、无 TMC 读写探针、无 `fault_reset`/`selftest`/`ss_test`/`pin`/`pwm`/`flash_*`、未跑旧 `sw1_cut.ps1`（T-01/T-02 仍 HOLD）。

## 5. 勘误与保留原文

- CP2 门5 首判"坏校验和=65286 ⇒ 不满足"是**我方判定器缺陷**（校验字节重复计入）；原判定件保留未覆写，修正判定（格式 0 错、校验和 0 错、无重叠越界空洞、往返逐字节相等）在 `cp2_gates_result_r1` 层，且直接解析**已落盘 HEX**。
- 查找轮三条口径校正（同一份映像／压缩包尺寸／段和与时间线索）已在 `cp0_offline_precheck.txt` §7 U-4 登记，未改写查找轮原文件。
- 本轮串口实现形态是 PowerShell `System.IO.Ports.SerialPort`（pyserial 未装且 §4.3 禁止安装），已按实际登记。

## 6. 边界与下一步（不自授许可）

`BLOCKED_ROLLBACK`：已关闭"未保存现存片内 Flash 字节"；仍开放"恢复下载未演练"、外部 SPI Flash 内容未备份、全板运行状态未备份、快照与 `e40a9b9` 构建的逐字节关系未证明。本轮**未**自我关闭任何状态。
需要新批准才能做的事：BOARD-A-01 的导出路径修复（分块写出或缩短每行格式）+ 重编 + 重烧 + 重跑；任何追加 trip；恢复下载演练；再次读 Flash。
`B-1 / BUG-008 / RISK-03/03a / 物理 RISK-04-b / M-1/M-2/M-3` 只按本轮实际覆盖部分评价，本包未预授其关闭。

**收尾断电已完成**：现场操作者回报"关了"（本地记录 2026-10-26 18:35 +08:00），顺序为先关 24 V 再拔 USB；
SW1 未回报变更（仍阻断位，USER_REPORTED）；串口与测量 RAM 此前已收尾。VM 与 USB 逻辑电源现均断开，
板子不再可达——此为用户口述，Agent 未实测，也不把记录时刻当作断电时刻。

```text
A0_BOARD_A_EXECUTED_OR_STOPPED / AWAITING_INDEPENDENT_BOARD_EVIDENCE_REVIEW
NO MOTOR / NO STEP_ENABLE / NO SAFETY_MACRO_CHANGE
NO AUTOMATIC RESTORE / NO FURTHER HARDWARE WORK
```
