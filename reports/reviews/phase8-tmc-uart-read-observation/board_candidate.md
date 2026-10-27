# 下一次有界板级字节采集包 —— 草案

**状态：DRAFT / NOT_AUTHORIZED / NOT_EXECUTED**

本轮（`phase8-tmc-uart-read-observation`）**没有执行本文任何一步**：设备动作 0，未创建
SerialPort、未打开 COM5、未发目标命令、未枚举设备/端口/探针、未调用 pyOCD/DAP、未读 Flash、
未下载/复位。上一轮 M0 的批准与其"包外重试"指令**不构成**本包的下载或串口授权；
执行需要针对本文的一次整包批准（由用户在真实回合给出）。

本文用本轮**真实**镜像与脚本散列填写，无虚构占位。云端复审后可直接据此给一次整包批准文本，
不需要另开"离线采集器准备轮"。

## 1. 绑定身份（待下载件）

| 项 | 值 |
|---|---|
| 目标 | Keil `rt-thread-measure-a0`（沿用板上同族测量镜像，不新建目标） |
| BIN（入库件） | `reports/firmware/phase8-tmc-uart-read-observation/measure-a0.bin` 267652 B `b54387fddbb6ecf77bd0c90d4abc0584212396ddbf884955e44fd76956a4b4d3` |
| 精确 AXF（本地，不入库） | `build/a0/rt-thread-a0.axf` 1691884 B `eb1d3fa85b0064193a52ced8bc96ca6f519b7ff8266141258c3a77198c2ec121` |
| 镜像内构建横幅 | `Oct 27 2026 23:50:33`（采集器 `-ExpectedBuildBanner` 必填，形状 `5\.3\.0 build …`） |
| 采集脚本 | `reports/reviews/phase8-tmc-uart-read-observation/UARTReadDiagSerial.ps1` 37063 B `2ada6b88a0631ceeff1e9f890466e96e55180031c5831a8b5859c114366d349d` |
| 脚本离线自检 | Windows PowerShell 5.1.26100.5074，`-SelfTest` 退出码 0，stdout 一行 PASS，stderr 0 B（见 `collector_selftest.txt`） |
| 代码字节 | `code_commit = ab76a07b5d48dbd8c7ee9ff6dea0c9bf90fcc5d4`（产品字节）；验证链锚点修正 `fb1a72e710c07ecf07aa432e0a3c74fa13c116d6` 不是任何镜像输入 |
| 四宏有效值 | 默认与 A0 目标均 0（ARMCLANG 真预处理 `-dM` 实证，见 `keil_builds.txt` §3） |
| map（本地） | `build/a0/rt-thread-a0.map` 2017042 B `8fe79f00d68114e28b5e90fef3b1f620b87c42fa23d16de99e66f760cc7822a6`；`Load Region LR_IROM1 Base 0x08000000 Size 0x00041bd0 Max 0x00080000`，`Image Entry point : 0x080003cd` |

对照件（**不下载**）：默认目标 `default.bin` 257348 B `ad5daaa36974d62ccac911bd1fa46f3c18b139c6ef42444026a75708715cdbd8`，横幅 `Oct 27 2026 23:50:13`，AXF `ce09d2d6e45694d2…`。
第三个目标 `rt-thread-motor-m1-candidate` 与其历史 BIN（`a76b845d…`）本轮不改、不下载；
若将来要采用新软件制电机候选，必须重新构建并重新审核，**不得把 `a76…` 称作本轮新代码镜像**。

旧件一律不覆盖：板上最后已证镜像 `5f30d3f0…`（261452 B，=
`reports/firmware/phase8-target-measurement-a0-export-r1/measure-a0.bin`）、同批
`02e98458…`、A0 首轮 `358fd81a…`（261100 B）、`e40` 快照 1 MiB
`b89f8a7cde7b944a5b5425f512a715d27a17c2e6c25822f36b2205bbefd0cac2` 与恢复 HEX
`b3ac97b976bc476556818f2bbaa977d15854a5c1d342f4b9c9d3ed825deb2c65`。

## 2. 待审额度（不是授权，是上限）

| 项目 | 待审上限 / 规则 |
|---|---|
| 现场供电 | 维持当前正常供电；确需常规上电至多 1 次；**不安排例行断电循环，不索取既往关电回报** |
| 新 A0 镜像下载 | 1 次；精确 AXF 与其 BIN 绑定（上表两个 SHA 必须同批）；用既有已验证的板载 DAP/下载参数；不 mass erase / 不 unlock / 不碰 OTP |
| 逐字节回读 | 新 BIN 长度范围 1 次并与新 BIN 比较；**不再读取 1 MiB 快照** |
| DAP 会话 / 主动复位 | 建议 2 会话 / 1 次 `SYSRESETREQ`；下载与回读流程隐含的复位**必须计入同一预算**，不得用"参数名里没有 reset"含混带过；预算用尽即停 |
| COM5 | 打开尝试 1 次，115200/8N1，无流控，DTR/RTS=false；打开失败同样消耗额度；不自动换端口、不枚举端口 |
| 命令 | 按 §7 顺序最多 7 条、各 1 次：`spm status`、`spm cap`、`safety_state`、`safety_status`、`motor_status`、`step_status`、`tmc_uart_probe`；probe 最多 1 次，即最多 3 笔寄存器读、0 个 WRITE 帧；不追加第二次对照、不加 `pin_status`/`tmc_config_show`/scan/自动重试 |
| 失败处置 | 原字节落盘 → 关闭端口 → 记录已用额度 → 停；不自动复位、不重烧、不读 Flash、不补发、不恢复快照 |
| 收尾 | 关闭 agent 创建的串口/日志句柄，只记录能实际观察到的状态；**不发送例行"关 24 V / 拔 USB"提示，不凭空填写最终电源值** |

就绪方案沿用已审 M0 传输层：被动至少 2 秒、末尾静默 1.2 秒、最多 20 秒；没有自发尾提示符
不算失败，允许第一支只读 `spm status` 作一次主动查询；查询响应仍必须有可靠提示符，
静默本身不作为响应完成。

## 3. 两个结论必须分开（不得合并成"整包 PASS"）

- `OBSERVATION_CAPTURE_COMPLETE` —— 新 `[TMRO]` 证据是否完整可分析：BEGIN/END 齐、
  `writes=0`、`export_err=0`、无 NUL、每条 `[TMRO]` 行以 CRLF 收尾、每条命令有可靠提示符。
- `TMC_IDENTITY_READ_SUCCESS` / `TMC_READ_REPORTED_FAILURE` —— 本次通信业务结果：
  `attempted_reads`、`link`、IOIN 的 VERSION 是否 0x21 且既有字段语义成立。

本轮夹具已证明这两件事可以分离：同一条 probe 应答 `capture=COMPLETE` 同时
`business=FAIL`（reads=1、link=UNPROVEN）。因此**业务超时正是本轮想完整采集的诊断结果**，
不能一看到 `read failed` 就丢弃后面的 `[TMRO]` 证据；也不能因为捕获完整就把通信失败记成 PASS。

若仍失败但阶段已看清：按逐笔阶段证据（未发请求 / TX 未完成 / RX 零字节 / 仅精确回显 /
其它字节 / 帧不完整 / CRC 错 / 完整合法帧被期限拒绝）决定下一步。**不**自动改地址、波特率或
预算常量，**不**以改变 ENN 电平作对照，**不**接电机。

## 4. 现场确认的最小集合

只保留现场"有人可操作、设备可连接、本包之外无其它操作"这三项必要确认。
不重新索取已确认的供电说明、断电顺序回报、扩展板整体照、接线两端照；不把电机铭牌、
实际模块限流依据或 A0 时长门塞进只读诊断的前置条件。若后续现场条件改变，再针对变化单独处理。

历史最终供电状态仍可记 `UNKNOWN`：它不阻塞离线工作，也**不得**为了补齐这个字段而连接设备。

## 5. 本包完成后仍不解锁的事

本包是只读通信观测。即便拿到 `TMC_IDENTITY_READ_SUCCESS`，也**不**构成电机使能许可：
四个安全宏仍为 0，`safe_for_enable` 在任何 probe 输出里都必须保持 0，arm/start/ramp/
STEP 输出仍需各自独立的人工确认与验收。
