# NS800 交接：phase8-target-measurement-a0-export-gate-r2（采集门返修 + 指定口径修正）

日期：2026-10-26（Asia/Shanghai）
任务起点：`fe3b64b40d009fb5f1527bb05e9dddb0dc454f72`（上一轮 HEAD = 被审对象）
`accepted_firmware_code_commit`：`cd8d9f095030c08634a082dd2bb9bc622d92fb1d`（本轮未改一字节）
`gate_code_commit`：`d4d337a3580cbe5aea85d98b0317761b5c04c403`（单独标识，与固件提交分开；见末尾"提交链"）
门字节有两个状态（复审请钉最终态）：实质返修全在 `gate_code_commit`（blob `f170e1af…`，47960 B，
sha256 `b08e7bdc…`）；随后为过 `git diff --check` 的尾随空白检查，只改了一行用例**描述字符串**
（blob `6582163…`，47964 B，sha256 `ba88f37b…`），随本证据提交入库。两态差异 = 1 文件 1 增 1 删，
未改正则、判定逻辑、用例输入或期望结论；本轮所有离线判定与自检结果都在最终态字节上取，
证据文件 §8 附了逐字重跑的复算复核哈希。
任务书结论：`A0_EXPORT_FIRMWARE_IMAGE_REVIEW_GO / COLLECTION_GATE_REWORK_REQUIRED`
（该限定 GO 随本次真实返修交接登记，未另开纯登记轮）

## 1. 本轮实际做了什么

**只返修 Python 采集门 + 任务书点名的证据口径**。未改固件、未重编 Keil、未重跑 A0/S1/S2、
未换镜像、未动开发板：`firmware_changed=false`、`python_gate_changed=true`、
`documentation_only=false`（不写成"纯文档"）、`builds_executed=[]`、`hardware_actions=[]`。
全部离线：不连串口、不连调试器、不发目标命令。

改动文件（全在 §7 允许清单内）：
- `tests/host/a0_export_gate.py`（采集门本体 + 自检 + 元负控）
- `reports/reviews/phase8-target-measurement-a0-export-r1/board_raw_archive.md`
  （仅 §6 规定的 masked-text 段与变换/来源说明）
- `reports/reviews/phase8-target-measurement-a0-export-r1/retest_candidate_plan.md`
  （仅 §6 指出的采集/初始化口径与门调用方式；仍是待批准方案）
- 新增 `reports/reviews/phase8-target-measurement-a0-export-gate-r2/gate_r2_verification.txt`
- 新增本交接 MD/JSON

## 2. GATE-R2-01 批状态与帧边界误放行（已关闭）

先在**未修改的 fe3b64b 门**上用任务书 §5 探针复现（原文见证据第 1 节）：
`ACTIVE_STATE / NUL_IN_STATE / STATE_NUMERIC_SUFFIX / EXTRA_ROW_AFTER_TAIL` 四条反例
全部误 `ACCEPT` 且 `clear=1 next=1`。修后同名同输入复跑：四条全部 `REJECT` 且 `clear=0 next=0`，
`CONTROL_HEALTHY` 仍 `ACCEPT`（纳入 3 条时长样本）。

实现要点：
- 收尾依据只取 **dump 之前最后一条可靠状态行**；必须 `state=STOPPING(2)`、`in_flight=0`、
  `dropped=0`、`records==预期`。`ACTIVE=1` 不再授权清理（`BATCH_NOT_STOPPING`），
  `IDLE=0` 同拒。合法过渡（stop 时仍有在途、稍后 status 在途=0）仍通过：只认最后一条可靠证据，
  不拼接不同响应。
- 协议行（头行/数据行/状态行/汇总行）逐条核：行内无 NUL（`NUL_IN_PROTOCOL_LINE`）、
  以 LF/CRLF 收尾（`PROTOCOL_LINE_UNTERMINATED`）、**整行锚定**且数字后必须紧跟真分隔符
  （`(?![0-9A-Za-z_])`）⇒ `dropped=0BAD`、`records=3BAD`、`dropped=0<NUL>` 一律
  `PROTOCOL_LINE_MALFORMED`。原始字节先保留再解析，不先清洗。
- 帧次序与边界：块外数值记录/INCOMPLETE ⇒ `ROW_OUTSIDE_CSV_FRAME`；重复头行 ⇒
  `HEADER_EXACT_MATCH` 计数≠1；重复汇总 ⇒ `DUMP_TAIL_EXACTLY_ONE`；汇总后的状态行 ⇒
  `STATE_LINE_AFTER_FRAME`。都不再归成"普通异步日志"忽略。
- `status` 行 `entry` 与本批不符、或 `records>0` 而 `entry='-'` ⇒ `BATCH_STATE_ENTRY`。
- REJECT 与解析异常（退出码 4）一律 `clear=0/next=0`；JSON 落盘前做 round-trip 自检，
  并新增 `caller_contract` 字段：调用方必须同时核对 rc + verdict + action_plan。

## 3. GATE-R2-02 数值域与"有效时间"夹具（已关闭）

- `I32_MIN/I32_MAX` 改成 `-(1<<31)..(1<<31)-1`。修前合法 `-2147483648`/`2147483647` 被误拒，
  修后判合法；外侧一值（`-2147483649`/`2147483648`）判非法；三个 i32 列
  （`pwm_raw_rc`/`permit_write_rc`/`outcome`）两边界与越界各有用例。
- `gen` 从"只查 ≥1"改为 `gen_u32`（1..2^32−1）；`4294967296` 修前被误收，修后判非法；
  其余 u32 列（含 `pre`/`body_*`）统一 ≤2^32−1，`slot` 仍 <64，枚举/布尔/十六进制形状照旧严格。
  未臆造协议之外的阈值。
- **健康夹具修正**：正控数据行改为真实进展 `body_end = body_begin + 1000`。
  原先"`body_begin==body_end` 却 `body_valid=1`+`COUNTER_OK`+`gates=0x0f`"的输入
  **原样保留**为具名负控 `GATE-R2-NEG-ZERO-PROGRESS-VALID-CLAIM`（不靠删用例变绿）。
- 内容矛盾即拒：`body_valid=1` 且 `end==begin` ⇒ `VALID_WITHOUT_PROGRESS`；
  `end<begin` ⇒ `VALID_WITH_WRAP`（当前生产判据不把回绕差分当有效区间，故不收紧源数据合同）。
- 完整、合法、`body_valid=0` 仍 `ACCEPT` 存档（3 条保留）、从时长统计排除、
  `extra_protection_actions` 恒 0。
- 时长统计只对 `ACCEPT` 批可信：REJECT 批 `trusted=false`、`included/excluded` 计数清零，
  只留 `observed_*` 诊断观测。

## 4. 自检、元负控与离线对照

| 项 | 结果 |
|---|---|
| `--selftest` | `pass=60 fail=0 cases=46`；原 20 例身份与语义全部保留 |
| `--selftest-meta` | `ok=7 blocked=2 bad=0 guards=9 required=5`；任务书点名的 5 道必填门（STOPPING + 4 道完整性门）全部 META-OK：摘掉即有具名负控翻成 ACCEPT。两条非必填门为 META-BLOCKED（摘掉后无人误放行，预期原因被 `BATCH_STATE_MISSING` 替上 = 多层防御），如实登记未粉饰 |
| `--check-header-against-source` | `gate=490 source=490 literal_pieces=8 identical=YES` |
| 历史三批 + cleanup（板级原始接收文件离线判定） | 全部 `REJECT`，`clear_records=0 next_batch_trigger=0` ⇒ 旧时间统计不可挽救，未做任何拼接/清洗/补列 |
| 上轮 `a0_on` 真实导出切片（stop 行 + 头行 + 3 条数据行） | `ACCEPT`，`csv_rows_parsed=3`，`failures=[]`，`timing included=3 trusted=true` |
| 首次失败保留 | 基线探针原文与修前冻结门（sha256 `78a976ed…`）都在仓外保留，修前/修后同名同输入并列进证据 |
| 每项检查的输出原文身份 | 7 项 `output_sha256` 逐条写进交接 JSON；复算命令、字节数与逐字重跑的复核见证据 §8。历史四批按原操作包的逐批 `--expected-records/--entry`（1/trip_fast、16/trip_fast、1/off_attempt、0/off_attempt）判定，不是统一取 3 |

## 5. §6 三项伴随修正

1. **归档来源校准**：板级轮采集脚本走 `ReadExisting()` + `Set-Content -Encoding UTF8`，
   56 个归档件都带 UTF-8 BOM ⇒ base64 的"无损"只对**已保存文件**成立（逐段解码回核 SHA 仍全等），
   **不等于原始 UART 线上字节无损**。未删 BOM、未覆盖旧件、未补中文字符；
   未来改用 `SerialPort.Read(byte[], …)` 先落盘字节再解码，这条写进重测候选方案，本轮未打开串口。
2. **探针 UID 整字段替换**：两个含 UID 的 masked-text 段（`01_reset_pyocd.txt` 的 `-u` 参数、
   `cp4_run.ps1` 的 `$UID`）由"只换中间 12+ 位数字"改为整字段替换（规则：≥20 位十六进制且
   内含 12 位以上连续数字，另留长数字串兜底）；**56 个 base64 段与原件散列逐段 SHA 完全不变**
   （结构化 diff 只有 UID 两处 + 规则表 + 新增来源校准节）。
3. **栈口径更正**：撤回"`line[640]`+约 32 ⇒ 总调用栈 ≤ 约 680 B"的写法 —— 640 B 只是**已知的
   单个局部数组**，完整调用链高水位需目标观察，不能用约数证明；旧已存日志 tshell max used
   在 17%–28% 之间，仅登记为后续比较基线，不当新镜像栈验收。本轮不重编、不改线程栈。
   （上轮 `keil_build.txt` 原文件不回改，更正只落在此交接。）
   另按 §6 末段更正：`spm init` **不是只读**，它会置位 `DEMCR.TRCENA` 与 `DWT_CTRL.CYCCNTENA`
   （只是不清零 CYCCNT），cap/init 都必须在明确的静止前置之后执行。
4. **采集门调用方式**（`retest_candidate_plan.md` h 条）：串口脚本必须**同时**核对进程退出码 0 +
   JSON `verdict=ACCEPT` + `action_plan` 两个计数为 1 才放行；JSON 解析失败、字段缺失或读到上一批
   的旧 JSON，以及退出码 4（输入/环境故障），一律按不放行处理。`state=STOPPING(2)` 的收尾要求在
   计划 f 条里同步写明（对应门的 `BATCH_NOT_STOPPING`）。
5. 三项修正的落点与归档守护原文（56 个 base64 段逐块/逐字节/逐摘要一致、解码后 54605 B 不变，
   非 base64 段仅 UID 相关两处变化）见证据 `gate_r2_verification.txt` 第 9 节。

## 6. 冻结与未改（可复核）

13 类冻结件按"工作树字节 vs `cd8d9f09` blob vs `fe3b64b` blob"三方一致核对，逐字原文见证据 §6，
`UNCHANGED-PROOF bad=0`（`OK=14 SKIP=1`）：两份生产 `.c/.h`、`stop_priority.c`、A0 夹具/端口头/控制台头/runner、
**公共 `shim_ps/rtthread.h`**、S1/S2 runner、两份新归档 BIN（`02e98458…` 251148 B /
`5f30d3f0…` 261452 B，与任务书 §3 记录一致）、旧 A0 镜像（`358fd81a…`）。
本地快照 `b89f8a7c…`（before-a/b 各 1 MiB）本轮按磁盘字节直算仍一致；**恢复输入 HEX `b3ac97b9…` 本轮标记为
SKIP**——它在本机不在可读位置、且本轮不重连设备，所以不声称本轮已回读，其身份沿用上一轮登记值。
恢复下载仍 `NOT_EXECUTED`；板上仍是旧 `358fd81a…`，新 `5f30d3f0…` **未烧录**。冻结引用未移动：
`main=1b3757ee`、`phase7/full-embedded-software=6d3fbc55`、tag 对象 `097935fe`、dereference `e40a9b95`。

`not_rerun`：A0/S1/S2/V1/V2/I-O/SD/Keil 本轮按任务书不重跑；门的输入（生产 47 列格式与生产导出行）
未变，已由 `--check-header-against-source` 双向防漂移核对。

## 7. 下一步（不是本轮授权）

任务书 §1 的限定 GO 只覆盖固件/镜像与已声明的主机合同；采集门通过独立复审后，才把
§3 的**同一份 `5f30d3f0…` 镜像** + 通过的采集门 + 字节采集脚本绑定为一次有界板级重测包，
由用户一次批准（包内不逐条索要确认）。本轮未发出任何硬件重测许可，也未执行任何设备动作。

## 8. 提交链

| 提交 | 内容 |
|---|---|
| `accepted_firmware_code_commit` `cd8d9f09…` | 上一轮固件输入（本轮未改） |
| `gate_code_commit` `d4d337a3580cbe5aea85d98b0317761b5c04c403` | 仅 `tests/host/a0_export_gate.py`（门字节态 A：47960 B，blob `f170e1af…`，sha256 `b08e7bdc…`；GATE-R2-01/02 的实质返修全在这里） |
| 证据/交接提交 `098fec16350150697ccb6f2eb8f057c216e84cb0` | `gate_r2_verification.txt` + 两个上轮文件的范围内修正 + 本交接 MD/JSON；门字节态 B 也在这里（47964 B，blob `6582163…`，sha256 `ba88f37b…`；相对态 A 只改一行用例描述字符串以过尾随空白检查，判定逻辑与用例输入未动，本轮结果都在态 B 上取）。本交接对提交号的登记写在该提交之后的一次小提交里 |

## 9. STOP

```
A0_EXPORT_GATE_R2_OFFLINE_READY / AWAITING_INDEPENDENT_GATE_REVIEW
FIRMWARE_AND_IMAGE_BYTES_UNCHANGED
NO BUILD / NO FLASH READ / NO PROGRAM / NO RESET / NO SERIAL / NO MOTOR / NO STEP_ENABLE
```
