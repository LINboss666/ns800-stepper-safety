# 下一次有界板级只读通信诊断包 —— 草案(R1)

**状态：DRAFT / NOT_AUTHORIZED / NOT_EXECUTED**

本轮（`phase8-tmc-uart-read-observation-r1`）**没有执行本文任何一步**：设备动作 0，未创建
SerialPort、未打开 COM5、未发目标命令、未枚举设备/端口/探针、未调用 pyOCD/DAP、未读 Flash、
未下载/复位、未碰供电/接线/ENN/SW1、未输出 STEP/DIR/HIGH、未运行 `sw1_cut.ps1`。
上一轮 M0/BOARD-B 的批准与其"包外重试"指令**不构成**本包的下载或串口授权；执行需要针对
本文的一次整包批准（由用户在真实回合给出）。**本文本身不授权其中任何动作。**

本文用本轮 R1 的**真实**镜像、AXF、脚本散列与横幅填写，无占位串。复审通过后可直接据此给
一次整包批准文本，**不需要再开"离线采集器准备轮"**。

与 §2 的关系：§2 把"建议上限"收敛成**明确的实际执行顺序与确切次数**——现场不得临时发挥，
任一步的前置失效、真实失败或额度用尽即停。

## 1. 绑定身份（待下载件）

| 项 | 值 |
|---|---|
| 目标 | Keil `rt-thread-measure-a0`（沿用板上同族测量镜像目标，不新建目标、不改 Define） |
| BIN（入库件） | `reports/firmware/phase8-tmc-uart-read-observation-r1/measure-a0.bin` **268340 B** `a48400c881c539db539876cfd38f3aceac2a78cc7ed0d0f9655c7edafbb19a1e` |
| 精确 AXF（本地，不入库） | `build/a0/rt-thread-a0.axf` 1693804 B `a96889c47c9433b132dc4eeecf46be287c13dc1cff11fc0003e6cca10826846f` |
| 长度依据（独立口径） | map 的 `Load Region LR_IROM1 … COMPRESSED[0x00041834]` = 268340 B = BIN 长度（两口径相同，回读范围据此） |
| 镜像内构建横幅 | `5.3.0 build Oct 28 2026 02:23:31`（采集器 `-ExpectedBuildBanner` 必填；**不是**上一轮的 `23:50:33`） |
| 采集脚本 | `reports/reviews/phase8-tmc-uart-read-observation-r1/UARTReadDiagSerialR1.ps1` **93797 B** `2e9c8ede8f75e7b513dbd4f2c3091189309454879e98404ba7ec64bbb80f2568` |
| 脚本离线自检 | Windows PowerShell 5.1.26100.5074 真实 `-SelfTest`：rc=0、stderr 0 B、负控 51 条（逐条具名理由）、禁止命令 12 条、切片正控 9 条、横幅门 6 条（见 `collector_selftest.txt`） |
| 代码字节 | `code_commit = a483dcba74abd8c04549459bcee2cbcdb1ddea4c`（产品 + 验证链同一提交，即镜像输入字节） |
| 四宏有效值 | 默认与 A0 目标均 0（目标级 Define 无覆盖 + ARMCLANG `-dM` 实证，见 `keil_builds.txt` §3） |
| map（本地） | `build/a0/rt-thread-a0.map` 2019991 B `e9e808d2d103c337…`（全长见 manifest） |

对照件（**不下载**）：默认目标 `default.bin` 258036 B
`dccb7b63c9284d320016a42e1fc8b7f62d544b97ca9cb433604e412bbbff55e3`，横幅
`Oct 28 2026 02:23:14`，AXF `a985ee184e5a7d37…`，map `COMPRESSED[0x0003eff4]` = 258036 B。

上一轮 URO 的两枚镜像（`ad5daaa3…` / `b54387fd…`）复审**未批准下载**，本轮候选件只有上面这两枚
新哈希；旧件一律不覆盖、不删除。第三个目标 `rt-thread-motor-m1-candidate` 与其历史 BIN
（`a76b845d…`）本轮不改、不构建、不下载；若将来要采用新软件制电机候选，必须重新构建并重新审核。
板上最后已证镜像仍是 `5f30d3f0…`（261452 B），同批 `02e98458…`、A0 首轮 `358fd81a…`、
`e40` 快照 1 MiB `b89f8a7c…` 与恢复 HEX `b3ac97b9…` 全部保持原样。
**BLOCKED_ROLLBACK 仍未关闭**：`default.bin` 不等于板上 e40 原镜像，不能从本文推出"已有可回退依据"。

## 2. 实际执行顺序与确切额度（不是"建议上限"）

整包合计：**下载 1 次、逐字节回读 1 次、pyOCD/DAP 会话 2 次、主动复位 1 次、COM5 打开尝试 1 次、
按序 7 条命令各 1 次（其中 probe 至多 3 笔寄存器读、0 个 WRITE 帧）**。下载与回读流程里工具
隐含的复位计入同一预算，不得用"参数名里没有 reset"含混带过。额度用尽即停。

| 步 | 动作 | 确切次数 | 前置 / 停止条件 |
|---|---|---|---|
| S0 | 只读预检（本机，零设备动作）：核对 §1 三个散列与 manifest 相同、`-OutDir` 全新空目录、UV4/pyOCD 版本与既有已证材料一致、pyOCD 目录内无 `pyocd.yaml/pyocd_user.py`（一律显式 `--no-config`） | — | 任一散列不符 ⇒ 不接触设备，停 |
| S1 | 供电：维持现场当前正常供电。仅当板子当前确实无电时按"先 USB（板载 LDO 逻辑电源）→ 再既有 24 V"上电 | 至多 1 次（当前已供电 ⇒ 0） | **不安排例行断电循环，不索取既往关电回报**；操作者手工做，两个时刻与有无异常运动/发热/指示如实回报，未回报即记 `NOT_REPORTED` |
| S2 | 下载：`UV4 -f <下载工程副本>/download-b.uvprojx -t rt-thread -j0`（沿用已证 BOARD-B 的下载副本工程与其 AXF 绑定，参数不猜、不新增选项） | **1** | 判据取日志文本 `Erase Done.Programming Done.Verify OK.`；UV4 退出码只表示进程结束，不作判据。载荷必须就是 §1 那枚 AXF。不 mass erase、不 unlock、不碰选项字/OTP/系统区 |
| S3 | 逐字节回读：pyOCD 会话 #1 `--no-config -t cortex_m -u <DAP_UID> -f 1000000 <既有 -O 选项> -c "status" -c "savemem 0x08000000 0x41834 <仓外新文件>" -c "status"` | **1**（会话 #1） | 回读范围 = 新 BIN 长度（0x41834 = 268340 B，与 map 的 COMPRESSED 长度同口径）。先落盘再算散列，与 §1 的 `measure-a0.bin` 做 `cmp`。`savemem` 路径必须用正斜杠（既证缺陷：反斜杠被 `-c` 串当转义吞掉）。**不再读 1 MiB 快照** |
| S4 | `ImageContinuityConfirmed` 的置真：**只有 S3 的 `cmp` 无差异才置真**，然后打开 COM5 一次：115200/8N1，无流控，`DtrEnable=false`，`RtsEnable=false`，不枚举端口、不自动换端口 | **1** 次打开尝试 | 打开失败同样消耗额度 ⇒ 停。`-ExpectedBuildBanner` 必须逐字等于 §1 横幅 |
| S5 | 采集器 `UARTReadDiagSerialR1.ps1 -ExecuteApprovedR1ReadDiag -OutDir <全新目录> -ApprovalSource "<批准原文所在>" -ExpectedBuildBanner "5.3.0 build Oct 28 2026 02:23:31" -ImageContinuityConfirmed -SiteConfirmed`：被动窗口（≥2 s 静默 1.2 s，最多 20 s）→ 横幅门 → 按序 7 条命令各 1 次 | 命令 7 条各 1 | 横幅门三种结果：① 捕获到版本横幅 ⇒ 必须逐字等于本包横幅，且一个窗口内不得出现两个互相矛盾的横幅，否则拒绝；② 没捕获到横幅（0 B 或已静默，板上 M0 真机被动窗口就出现过 0 B）⇒ 在 S3 已通过的前提下继续并记 `BANNER_NOT_OBSERVED`，身份依据来自本包逐字节回读，**不编造横幅**；③ 没捕获横幅且 S3 未通过 ⇒ 拒绝。为获取横幅**绝不**追加复位或重开 COM5 |
| S6 | 条件步：仅当被动窗口结束后既无横幅也无可信行尾提示符、且尚未发出任何命令时，允许唯一一次主动 `SYSRESETREQ`（pyOCD 会话 #2） | **1**（会话 #2） | 这是本文里唯一的复位。若已拿到可信提示符或命令序列已经开始 ⇒ **不复位**（中途复位会毁掉证据链）。`Assert-NoReset` 若在应答里再次看到启动横幅即停止并保留原字节 |
| S7 | 收尾：`finally` 只关流/端口并注销事件；记录每类动作的实际消耗 | — | 失败时：原字节已落盘 ⇒ 关闭句柄 ⇒ 记实际次数 ⇒ 停。**不**重试、**不**补发、**不**清 RAM、**不**清故障、**不**恢复快照、**不**自动清理命令 |

就绪机制沿用已审传输层（16 件复用对象逐字节不变）：没有自发尾提示符**不算**失败，允许第一支
只读 `spm status` 作一次主动查询；查询响应仍必须有可靠提示符，静默本身不作为响应完成。

命令顺序（各 1 次，白名单外一律被 `Send-Tx` 拒绝）：
`spm status`、`spm cap`、`safety_state`、`safety_status`、`motor_status`、`step_status`、
`tmc_uart_probe`。probe 最多 1 次 ⇒ 最多 IOIN/GSTAT/IFCNT 三笔读、0 个 WRITE 帧；不追加第二次
对照、不加 `pin_status`/`tmc_config_show`/`tmc_scan`/`fault_reset`/`selftest`/`arm`/`start`/ramp。

## 3. 两个结论必须分开（不得合并成"整包 PASS"）

R1 采集器把判定分成两层，且**两层各自都有正/负控**（见 `collector_selftest.txt`）：

- `capture` —— 证据完整性：BEGIN/END 各恰一次且顺序正确、`reads` 两处一致且在 0..3、
  planned=3、v=1/addr=0 与本包一致、三笔 reg=06/01/02 结果身份各恰一次、SUCCESS+FAIL 与
  `reads` 相等、NOT_REACHED 与"计划数−尝试数"相等、每笔的必需事实行齐全、样本分块的 `n` 等于
  该行真实 hex 字节数、块 offset 连续、分块总数等于声明长度、空样本为 `n=0 hex=-`、无 NUL、
  无 `EXPORT_ERROR`、每条 `[TMRO]` 行干净终止且不越过 96 B 打印界限。
  缺任何一项 ⇒ 保存原字节后停止，**不标 COMPLETE、不自动补发**。
- `business` —— 通信业务结果，并单列失败阶段：
  `read_ok`（三笔全 SUCCESS）/ `identity_ok`（IOIN VERSION=0x21 且与逐笔原值一致）/
  `fields_ok`（GSTAT/IFCNT 的**完整 32 位原值**只含已定义位）；阶段取
  `OPEN` / `TXN` / `READ` / `IDENTITY_MISMATCH` / `FIELDS_OUT_OF_DOMAIN`。
  已证明可以分离：真实 open 失败、事务不可用、读到 VERSION=0x20、GSTAT 高位有未定义置位
  这四种输入，R1 脚本一律判 **capture=COMPLETE + business=FAIL**（上一轮脚本会误拒前两种、
  误收后一种）。人类摘要把 GSTAT/IFCNT 掩成低 8 位，所以本文要求核对**逐笔 `val=` 与摘要
  `raw=`**，不接受只看截断后的 0x00/十进制小值。

本轮夹具在同一定稿字节上还证明了期限事实的分列（`exp=<总>/<阶段>` 三态、`pre_trunc` 与
`gap` 分列），因此若再次超时，证据会直接指出是"TX 已交 4 字节而 RX 一字节未见"还是
"完整合法帧被期限拒绝"。**不**据此自动改地址、波特率或预算常量，**不**以改变 ENN 电平作对照，
**不**接电机。用户已确认供电/接线/扩展板线路正常（`USER_CONFIRMED`，非云端实测），本轮不重索取
此类材料；历史最终供电状态仍可记 `UNKNOWN`，不为补齐该字段连接设备。

## 4. 现场确认的最小集合

只保留三项必要确认：有人可操作、设备可连接、本包之外无其它操作。
不重新索取供电说明、断电顺序回报、扩展板整体照、接线两端照；不把电机铭牌、实际模块限流依据
或 A0 时长门塞进只读诊断的前置条件。A0 采样继续暂缓，`A0_DURATION_EVIDENCE_NOT_ACHIEVED` 不变。
若后续现场条件改变，再针对变化单独处理。

## 5. 本包完成后仍不解锁的事

本包是只读通信观测。即便拿到 `read_ok=1 identity_ok=1 fields_ok=1`，也**不**构成电机使能许可：
四个安全宏仍为 0，`safe_for_enable` 在任何 probe 输出里都必须保持 0（R1 采集器把它当作
矛盾即拒绝），arm/start/ramp/STEP 输出仍需各自独立的人工确认与验收。
B-1、BUG-008、RISK-03/03a、物理 RISK-04-b、M-1/M-2/M-3 按实际开放部分继续保留；主机模型或
新镜像"构建成功"不能关闭任何物理风险。
