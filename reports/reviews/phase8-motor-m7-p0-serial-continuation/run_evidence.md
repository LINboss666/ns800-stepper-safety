# 仅串口续测执行记录（2026-10-30，第二次如实停止；额度已耗，未重试）

批准来源（用户本轮原话）：**"批准 P0 仅串口续测"**，范围为 §5.5 那条：一次 COM5 打开、
七次固定查询发送（四条状态各一、show 二、sample128 一）、四个 DMM 电压读数；
不烧录、不读目标 Flash、不连 DAP、不主动复位、不转电机、不改配置；失败即停，不重试。

执行根（仓外原件保留）：`D:\ns800-m7-p0-cont-20261030T194941`
运行字节全部取自 Git 对象：采集器 `0e02ccfc…5467` (23374 B)、验证器 `2cf87b1d…a8c7` (21742 B)、
计算器 `5c7a2340…63ad`、证明件 JSON `e8df121f…4246`、证明件 boot.b64 `46b53e93…dd46`（解回 3381 B /
`bc47dc02…14fa`）、上一轮回读件 `readback.bin` 266244 B / `e614a075…f7da`。

## 1. 停点

```text
P0_FAILED:Stop:REPLY_TIMEOUT:01_safety_state.rx.bin      （进程 rc=1）
```

续测模式**按设计工作了**：绑定/证明校验通过 → 打开 COM5（唯一一次）→ **2 秒被动窗口得到 0 字节**
（静默允许，`raw_attach.bin` 为空文件）→ 台账写入
`CONTINUATION_ATTACH identity_basis=PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY fresh_banner_required=false`
→ 发第一条 `safety_state`（写成功，回包 62 B 原字节先落盘）→ 12 s 内业务门没满足 →
停止并关闭串口。**未走到 `WAIT_DMM_BEFORE`，四个 DMM 读数一个都没发生，无 `result.json`。**

台账（6 行，逐行）：

```text
BIND            mode=CONTINUE_PROVEN_BOARD prior_board_commit=24ab5a7…
PORT_OPEN_ATTEMPT COM5
PORT_OPEN       COM5
CONTINUATION_ATTACH bytes=0 identity_basis=PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY fresh_banner_required=false
TX_ATTEMPT      command=safety_state file=01_safety_state.rx.bin
PORT_CLOSED
```

`COMMAND_ACCEPTED` 0 条；`TX_ATTEMPT` 1 条（失败也计入额度）。

## 2. 决定性新事实：污染出现在**静默期的单条响应**里，不是启动并发窗口

实际回包（`first_response_analysis.txt`，62 B / `c26f480a…8305`）：

```text
b'safety_state\r\nMMu\x81st\xd1\x95\xf5READY fault_code=0 degraded=0x00\r\nmsh >'

  头部 14 B（命令回显 safety_state\r\n）            完好
  尾部 39 B（READY fault_code=0 degraded=0x00\r\nmsh >）完好
  中段应为 b'[SS] state=' (11 B)：5b 53 53 5d 20 73 74 61 74 65 3d
  实到                 ( 9 B)：4d 4d 75 81 73 74 d1 95 f5   ← 4 个非 ASCII 字节
  帧尾提示符 PROMPT 命中：True（说明整帧已经收完，不是"还没回"）
  危险标记：无
```

与上一轮启动流的污染**同一签名**：行头行尾完好、行中段若干字节被替换、行长变短。

```text
上一轮 boot  ：版本行 头 12 B / 尾 20 B 完好，中段 11 B → 8 B（7 个非 ASCII）
本轮 响应    ：回包   头 14 B / 尾 39 B 完好，中段 11 B → 9 B（4 个非 ASCII）
```

关键对照（同一块板、同一条命令、仓库既存原件）：

```text
reports/reviews/phase8-motor-m5-fault-log/cp1/final_pass/016_safety_state.bin
= b'safety_state\r\n[SS] state=READY fault_code=0 degraded=0x00\r\nmsh >\r\nmsh >'
  （6a59468a…dacd，逐字节干净）
```

也就是说 `safety_state` 这条响应以前从这块板、这个 COM5 口取到过**完全干净**的字节。

## 3. 这否掉了什么、还没否掉什么

- **削弱**（不等于否掉）"仅因开机阶段多线程并发写控制台"这一条：本轮挂载前 2 秒完全静默，
  发的是单条查询，回包前后两段都完好，只有中段被替换；这个窗口里没有已知的启动并发写者。
  仍然不能排除并发写，因为"别的线程在这 12 s 内打印过"从 PC 侧看到的字节里无法直接区分。
- **仍未排除**目标 UART/DAPLink CDC/PC 读取路径按字节出错或丢失。两条流的签名相同、
  位置互不相关、且**都发生在本 Python 采集器**下；而此前干净取样来自历史 PowerShell 采集链。
  这是目前最强的相关性线索，但相关不等于因果。
- **不支持**"SWD/回读通路有问题"：上一轮 266244 B 定长回读逐字节相等。
- 本轮**不定根因**。`CONSOLE_STREAM_INTEGRITY_UNRESOLVED` 继续开放。

判开所需、且本轮明确没做的下一步（都要新批准）：在**不改动已接受采集器字节**的前提下，
用历史 PowerShell 采集链对同一条 `safety_state` 静默响应再取一次同一时刻的字节比对；
或反过来把 Python 采集器的读循环换成不做每块 fsync 的对照件——后者属于修改判据，需要复审。

## 4. 采集器合同表现（不是缺陷）

- 身份门（上一轮回读 + 连续性确认）如实生效，没有假装"横幅已核对"：`fresh_banner_required=false`
  与 `PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY` 都写进台账；"本轮未重新核对横幅"照此登记。
- 静默窗口没有被当成设备故障；`STARTUP_BYTES_DURING_ATTACH` 未触发（确实没有启动字节）。
- 原始响应**先落盘再判定**，62 B 完整保住；业务字段被污染时**拒绝接受**，
  没有把 `MMu…st…READY fault_code=0` 当成 `state=READY` 放行，也没有降级成"看到 READY 就算过"。
- 异常出口仍关闭串口并记 `PORT_CLOSED`；没有任何重试、补发或额外命令。

## 5. 额度实耗

| 动作 | 上限 | 实耗 |
|---|---:|---:|
| COM5 打开尝试 | 1 | 1（成功） |
| 下载 / 目标 Flash 读 / DAP / pyOCD 子进程 / 主动复位 | 0 | **0** |
| `safety_state` | 1 | 1 次发送尝试，0 次接受 |
| `safety_status` / `motor_status` / `step_status` | 各 1 | 0 |
| `current_cal show` | 2 | 0 |
| `current_cal sample 128` | 1 | 0 |
| UT39A+ 电压读数 | 4 | 0（`WAIT_DMM_BEFORE` 未出现，未索取） |
| 电机动作 / 配置写 / 清故障 / 重试 | 0 | 0 |

连续性前置也已核对：我自己的操作台账确认 `24ab5a7` 之后到本次运行之间没有任何下载或目标写入；
`1d13ea5`（本轮脚本提交）只改仓库文本，不触及板子。

## 6. 判定

- **没有取得任何电压观察数据**，不登记 `VOLTAGE_OBSERVATION_ONLY`；P0 仍未完成。
- 板上镜像仍是 `e614a075…f7da`（上一轮已逐字节证明，本轮未再读 Flash）。
- 板终态：串口已关闭；本轮只向它发过一条只读查询 `safety_state`；未复位、未烧录、未动电机。
- 两轮失败（`BOOT_IDENTITY_GATE_FAILED` 与本轮 `FIRST_FIELD_GATE_BYTE_CORRUPTION`）
  与上一轮已证的镜像在板事实**并列保留**，不拼成"一轮全通过"。

---

## 8. 第二次续测（用户把 USB 线换回原口，2026-10-30 20:18）

用户报告曾更换 USB 口（仍枚举为 COM5），随后**换回原口**并授权再试一次。同字节采集器、
同一证明件、同一 `readback.bin`，输出目录 `P0_try2`（全新）。

```text
P0_FAILED:Stop:REPLY_TIMEOUT:01_safety_state.rx.bin          rc=1（同样停点）
raw_attach.bin = 0 字节（原口同样静默）
01_safety_state.rx.bin = 61 B / 54ee1e7e7253acb519c586ac96253225504d7cd57c0dc106e4e199889d080cb8
b'safety_state\r\n[\xaa\x05\x9aate=READY fault_code=0 degraded=0x00\r\nmsh >'
  中段期望 '[SS] state=' (11 B)：5b 53 53 5d 20 73 74 61 74 65 3d
  中段实到   8 B：5b aa 05 9a 61 74 65 3d   ← 首字节 '[' 正确、尾四字节 'ate=' 正确，中间 6 B 变成 3 B 垃圾
```

### 三次采样形状对照（细节见 `port_swap_and_three_capture_analysis.txt`）

| 次 | USB 通路 | 行头 | 损坏中段 | 行尾 |
|---|---|---|---|---|
| 1 启动流 | 换后的口 | 前 12 B 完好 | `26 09 02 d5 a5 b1 91 81`（`.3.0 build ` 11 B → 8 B） | 后 20 B 完好 |
| 2 响应 | 换后的口 | 回显 14 B 完好 | `4d 4d 75 81 73 74 d1 95 f5`（`[SS] state=` 11 B → 9 B） | 39 B 完好 |
| 3 响应 | **原口** | 回显+`[` 15 B 完好 | `aa 05 9a`（11 B → 8 B） | `ate=` 起 47 B 完好 |

### 这排除了什么、留下了什么

- **换 USB 口不是原因**：原口照样坏，且坏的位置/内容每次都不同（非确定性）。PC 侧 USB 通路作为触发者被削弱。
- **"别处一整条文本被插进来"这一版的控制台竞争被削弱**：损坏段里出现 `0x05 / 0x02 / 0x09` 这类**既不是 ASCII 文本、也不是 UTF‑8 序列组成部分**的控制字节；且 `91 81`、`91 95` 两对在冻结固件的全部打印字符串里 **0 命中**。
- **与形状最吻合、但本轮仍未证明**的仍是固件侧那条通路：单个共享 `static rt_log_buf[128]` + `_serial_poll_tx` 逐字符发送 + 保护宏为空。该模型自然解释"行首已发出的字节正确 → 之后读到别人写进缓冲区前段的碎片 → 碎片结束后又读回本行剩余字节"，也解释中段为何**变短**。它需要受控干预才能判开。
- **能下的强结论**（比根因更可操作）：只要这个签名还在，**任何依赖控制台文本的读数判定都不可信**；这不是采集脚本能修的，也不是换 USB 口能绕的。

### 额度

用户"我授权你再试试"给出的是第二次仅串口额度，已同样耗尽：COM5 1/1、`TX_ATTEMPTS=1 ACCEPTED=0`、
`DMM_READINGS_TAKEN=0`、无 `result.json`；下载 / 目标 Flash 读 / DAP / 主动复位 / 电机 / 配置写 = 0；重试 0。
板子未被动过任何东西，串口已由采集器正常关闭；`e614a075…f7da` 在板的既有证明不受影响。

## 9. 判别实验：改用 M5 当时的读取原语，同一块板回包逐字节干净

用户授权的选项 1 落地时我做了一处纠正：那条干净的 M5 原件**也是 pyserial 采的**
（`m5_field_serial.py`: `serial.Serial(port,115200,timeout=0.05)` + `read(max(1,in_waiting))` +
0.5 s 静默判完 + CRLF 结束 + 发送前 `reset_input_buffer()`），不是 PowerShell 链。
所以判别量改成"照抄这套历史读取/发送配置"，而不是换语言。

一次 COM5 打开、一条只读命令，另加 **45 秒零发送被动监听**：

```text
{"kind": "OPEN_ATTEMPT", "t": 8492.203, "port": "COM5", "baud": 115200, "timeout": 0.05}
{"kind": "OPENED", "t": 8492.203}
{"kind": "PASSIVE_SILENCE_OK", "t": 8537.437, "seconds": 45.0}
{"kind": "TX", "t": 8537.437, "command": "safety_state", "bytes_written": 14, "terminator": "CRLF"}
{"kind": "RX", "t": 8537.968, "file": "01_safety_state.rx.bin", "bytes": 71, "sha256": "6a59468afb1f1684e77ecaf4ff723a69013c620215dc793b9667f8048361dacd", "response_bytes": 71, "quiet_period_end": true, "elapsed": 0.531}
{"kind": "CLOSED", "t": 8537.968}
{"kind": "VERDICT", "t": 8537.968, "exact_clean_line_present": true, "non_ascii_bytes": 0}
DONE

01_safety_state.rx.bin = 71 B / 6a59468afb1f1684e77ecaf4ff723a69013c620215dc793b9667f8048361dacd
与 reports/reviews/phase8-motor-m5-fault-log/cp1/final_pass/016_safety_state.bin  cmp 全等
```

| 配置 | 结果 |
|---|---|
| 已接受 P0 采集器（单 CR、固定 512 B 读、每块 fsync、body-tag+帧尾判完） | 62 B / 61 B，中段被替换，2/2 复现 |
| 历史读取原语（CRLF、in_waiting 读、0.5 s 静默判完、发送前清缓冲） | **71 B 逐字节干净** |

由此确立与排除：

- **不是 USB 口**：原口下两种配置同时成立，一个干净一个坏。
- **不是并发打印者**：静置板子 45 秒零字节，说明两次损坏发生时没有任何别的线程在打印。
- **板子与当前 M7-R1 镜像有能力输出完全正确的该行**（本次还顺带取到真实的现时状态：
  `state=READY fault_code=0 degraded=0x00`，但它是**经非批准采集器**取到的，只作证据，不算 P0 门通过）。
- 剩下的差异只在**已接受采集器的请求/读取配置**里，可分两个量：
  (a) 终止符单 CR vs CRLF（改变 shell 自己往流里写什么；垃圾里出现 0x02/0x05/0x09=TAB 这类控制值，
      与行编辑/回显活动的形状相容）；
  (b) 读循环（固定 512 B + 每块 fsync vs in_waiting + 静默判完）——PC 侧读法能丢/能迟，
      但**凭空造出** 0xaa/0x9a 很难解释，排位明显低于 (a)。
- 本轮**不宣布根因**：下一步应当只翻一个量（单 CR + 历史读法，或 CRLF + 采集器读法）各一次打开，
  而不是同时改。

额度：用户批准的选项 1 已用掉（COM5 1/1、只读命令 1 条、被动监听 45 s、无发送其它命令、
无 DAP/复位/烧录/写配置/电机、重试 0）。采集器字节未改（仍 `0e02ccfc…5467`）。
判别工装 `historical_reader_probe.py` 随证据入库，但它**不是**被批准的 P0 采集器，
静态自检：`serial.Serial(` 1 次、`ser.write` 1 次且内容为 `safety_state\r\n`、无 subprocess、无 pyOCD、无复位。

## 10A. 单变量翻转结果：终止符被洗清（2026-10-30 20:4x）

把刚才采到 71 B 干净件的工装**只改一行**（`safety_state\r\n` → `safety_state\r`，见
`cr_variant_probe.py` 与 `historical_reader_probe.py` 的一行差异），读循环、45 s 静默前置、
完成规则全部保持：

```text
{"kind": "OPEN_ATTEMPT", "t": 9042.296, "port": "COM5", "baud": 115200, "timeout": 0.05}
{"kind": "OPENED", "t": 9042.312}
{"kind": "PASSIVE_SILENCE_OK", "t": 9087.531, "seconds": 45.0}
{"kind": "TX", "t": 9087.531, "command": "safety_state", "bytes_written": 13, "terminator": "CRLF"}
{"kind": "RX", "t": 9088.078, "file": "01_safety_state.rx.bin", "bytes": 64, "sha256": "3617c305227f177ddcfd0cc3a8fff23b9385201377b27647771dadbf52374858", "response_bytes": 64, "quiet_period_end": true, "elapsed": 0.547}
{"kind": "CLOSED", "t": 9088.078}
{"kind": "VERDICT", "t": 9088.078, "exact_clean_line_present": true, "non_ascii_bytes": 0}
DONE
```

原始回包（`cr_variant_01_safety_state.rx.bin.b64` 解回即此，控制字符按 `cat -v` 写法）：

```text
bytes = 64   sha256 = 3617c305227f177ddcfd0cc3a8fff23b9385201377b27647771dadbf52374858
non-ASCII bytes = 0
safety_state^M
[SS] state=READY fault_code=0 degraded=0x00^M
msh >
```

（这一段在追加时被 shell 的反引号命令替换吃掉一截——我用双引号包了代码围栏，已补回；
其余内容未受影响。）




工装里 `"terminator": "CRLF"` 是**没跟着翻的静态标签**（我的记录缺陷）；实际发出 13 字节
（`safety_state` 12 + 单个 CR），以 `bytes_written=13` 与 64 B 回包为准。登记以免被误读。

五样本对照（唯一稳定共变的是**这次是不是用已接受采集器的那套打开通道/读循环**）：

| # | 打开通道与发送 | 结果 |
|---|---|---|
| 1 | 采集器：打开→复位→启动流即读 | 启动版本行中段被替换 |
| 2 | 采集器：打开→2 s→发送 | 62 B，中段被替换 |
| 3 | 采集器：原口，同样时序 | 61 B，中段被替换 |
| 4 | 历史原语：打开→45 s→发 CRLF | **71 B 干净**（与 M5 原件全等） |
| 5 | 同上但只改单 CR | **64 B 干净** |

因此：
- **终止符不是原因**（单 CR 与 CRLF 都干净）；
- **USB 口不是原因**（原口下 3 坏 2 干净同时成立）；
- **不是并发打印者**：45 s 零发送监听证明静置板子一个字节都不打印；
- **"发送距打开的时间"也不成立**：M5 轮的历史工装是打开后 0.2 s 就发命令并采到干净字节的（反例）；
- 剩下唯一未排除的差别在**已接受采集器自己的通道配置与读循环**：显式
  `xonxoff/rtscts/dsrdtr=False` + 每块重设 `timeout<=0.1` + `read(min(512,left))` + 每块
  `write/flush/fsync` 再判定的组合。PC 侧读法能丢、能迟，**很难凭空造出** `0xaa/0x9a`，
  所以这条也只是相容，未证明。

下一个单变量实验（需再一次 COM5 打开授权）：在历史工装里**只**换成采集器那套 open kwargs +
每块 fsync 读循环，终止符保持单 CR、完成规则不变。干净 → 差别在采集器其它细节；
复现损坏 → 定位到"写文件/fsync 与 read(512) 的组合"这一具体通路，再决定是否值得给采集器提一处最小修正规格。

本轮额度：选项 1 与随后"继续"各一次打开（共 2 次），全部只发 `safety_state`，
无 DAP、无复位、无烧录、无写配置、无电机、无重试；采集器字节未改。
