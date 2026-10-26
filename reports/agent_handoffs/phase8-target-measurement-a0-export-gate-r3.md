# NS800 交接：phase8-target-measurement-a0-export-gate-r3（采集门 R3 返修：原始字节完整性 + 存储宽度）

日期：2026-10-26（Asia/Shanghai）
任务起点：`85522885ec39fbd96f99847a0508c2d7cb137ae6`（= 上一轮被审交付 = 本轮开工时的远端分支头）
`accepted_firmware_code_commit`：`cd8d9f095030c08634a082dd2bb9bc622d92fb1d`（本轮未改一字节）
任务书结论：`GATE_R2_PREVIOUS_FINDINGS_CLOSED / REMAINING_GATE_REWORK_REQUIRED`
本轮状态：`GATE_R3_OFFLINE_READY / AWAITING_INDEPENDENT_GATE_REVIEW`

## 1. 本轮实际做了什么

按任务书 §5 一次完成，**只动 Python 采集门**（外加一份照抄附录 B 的探针件）：

| 对象 | 本轮动作 |
|---|---|
| `tests/host/a0_export_gate.py` | 由附录 A 补丁 `git apply --check`→`git apply` 应用，未手抄任何字符串转义 |
| `tests/host/a0_export_gate_r3_probe.py` | 新增：附录 B 逐字照抄（与工作副本同一散列 `1c7bf519…`），供复审一条命令复跑 |
| 固件 / HAL / 内核 / `rtconfig.h` / Keil 工程 / 两份镜像 / 已有 runner / 公共 shim / 旧证据 / 归档 | **零改动**（§6 逐项回读） |
| 补丁文件本体 | 未进仓（§5.2 只要求在仓外暂存并应用）；身份 `7400 B / 806780ac…` 与任务书 §4 一致 |

`firmware_changed=false`、`python_gate_changed=true`、`documentation_only=false`、
`builds_executed=[]`、`hardware_actions=[]`。未重编 Keil、未重跑 A0/S1/S2 主机链、未连接串口/探针/设备。

## 2. GATE-R3-01 原始字节完整性（已关闭）

根因是**顺序**：NUL 检查写在"该行已被分类为协议行"的条件内部，前导 NUL 反而让额外记录被分类成
`OTHER` 而绕过完整性门；同时 `decode(seg).rstrip('\r')` 会吃掉任意多个 CR，把 `CRCRLF` 洗成合法 CRLF。

改法（补丁原文口径，未自行扩写）：

1. 先核 NUL 再谈分类 —— 本采集包是文本协议，**任何位置**出现 NUL 一律保留原字节并拒绝
   （`NUL_IN_PROTOCOL_LINE` 文案随改为"含无法分类的行"），绝不删 NUL 后重判。
2. 只移除真实 LF **前至多一个** CR：`decode(seg[:-1] if lf and seg.endswith(b'\r') else seg)`；
   协议行里残余的 CR 具名为 `BARE_CR_IN_PROTOCOL_LINE`。
3. CSV 帧外的普通合法异步日志仍允许（未被点名拒绝的 OTHER 行为不变），本轮没有为通过而改写采集文件。

同输入复现与修后（附录 B，44 项）：

| 输入 | 起点门（`ba88f37b…`） | 本轮门（`94e83175…`） |
|---|---|---|
| `CONTROL_HEALTHY` / `CONTROL_LF` | ACCEPT，clear=1 next=1 | ACCEPT，clear=1 next=1（正控保持） |
| `EXTRA_ROW_NUL_PREFIX` | **ACCEPT，clear=1 next=1** | REJECT，clear=0 next=0 |
| `NUL_OTHER` | **ACCEPT，clear=1 next=1** | REJECT，clear=0 next=0 |
| `EXTRA_CR_TAIL` | **ACCEPT** | REJECT（`BARE_CR_IN_PROTOCOL_LINE`） |

基线脚本 `rc=1` 表示"剩余缺口已复现"（mismatch=16 = 3 条字节完整性 + 13 条越界列），
不是修后失败，也不是 INFRA-FAIL；其余 28 项本就正确。

## 3. GATE-R3-02 存储宽度不由打印形式决定（已关闭）

依据 `applications/spm_rec_t` 的真实字段宽度：`%u` 只是打印调用约定，不会把 `rt_uint8_t` 扩成 32 位值域。
13 列按 `0..255` 收：`ctx_isr`、`saved_primask`、`in_owner`、`owner_after`、`phase_after`、`inhibit`、
`pending`、`latch_m`、`latch_d`、`pwm_attempted`、`pwm_result`、`permit_level`、`branch`。

- 这 13 列各填 256：起点门全部 ACCEPT 并授权清理 ⇒ 本轮全部 `FIELD_ILLEGAL` REJECT。
- 各填 0 与 255：两轮都 ACCEPT（`GATE-R3-POS-U8-MAX` 把 255 作为显式正控钉住）。
- 既有 `flag/slot/hex2/枚举` 规则、`int32`/`uint32` 边界、`gen` 上界一律未动；
  **没有**为 owner/phase/branch 发明协议之外的取值阈值。

## 4. 自检、元负控、跨轮回跑与离线对照

| 项 | 结果 |
|---|---|
| 附录 B 基线（起点门） | `cases=44 mismatch=16`，rc=1（16 项逐条具名见证据 §1） |
| 附录 B 修后（同一份输入） | `cases=44 mismatch=0`，rc=0 |
| `--selftest` | `pass=65 fail=0 cases=51`：R2 的 46 例身份与判据一条未删，R3 新增 5 例 |
| `--selftest-meta` | `ok=9 blocked=2 bad=0 guards=11 required=5`；新增 `U8_UPPER_BOUND`、`BARE_CR_PROTOCOL` 均 META-OK；两条 META-BLOCKED 保留原身份（被 `BATCH_STATE_MISSING` 替上，多层防御），未粉饰；必填 5 道全 META-OK，NUL 门现被 3 条用例约束 |
| `--check-header-against-source` | `gate=490 source=490 literal_pieces=8 identical=YES` |
| 跨轮回归（R2 任务书 §5 探针跑在本轮门上） | 健康正控仍 ACCEPT(纳入 3)；ACTIVE/NUL/数值后缀/帧外额外记录/零进展仍全 REJECT；`i32` 两边界合法、`gen=4294967296` 非法 ⇒ R2 结果未被重新打开 |
| 历史四件旧接收文件（逐批 N/entry） | `b1/b2/b3/cleanup` 全部 REJECT，`clear_records=0 next_batch_trigger=0`，逐件 rc=1；未拼接/清洗/补列，时长统计仍 untrusted |
| 现存健康导出切片（上轮 `a0_on`） | ACCEPT，`csv_rows_parsed=3`，`failures=[]`，`included=3 trusted=true`，rc=0（未重跑 A0、未连 COM5） |

## 5. 冻结与未改（可复核）

`UNCHANGED-PROOF-R3 ok=26 bad=0 skip=0`，逐项原文见证据 §7：

- 13 个冻结源文件：工作树(LF) == `cd8d9f09` blob == `8552288` blob —— `sp_measure_a0.c/.h`、
  `stop_priority.c`、`motor.h`、`tmc2209.h`、`project_board.h`、A0 的 main/port/console/runner、
  公共 `shim_ps/rtthread.h`、S1/S2 runner。
- 两份冻结镜像磁盘直算：`default.bin` 251148 B `02e98458…`、`measure-a0.bin` 261452 B `5f30d3f0…`，
  与任务书 §6 及起点对象一致；**未烧录**，板上仍是旧 `358fd81a…`（上一轮 `USER_REPORTED` 断电收尾）。
- 四道安全宏仍 `RT_FALSE`：`motor.h:69`、`motor.h:83`、`project_board.h:51`、`tmc2209.h:100`。
- 构建输入 `project.uvprojx`、`template.uvprojx`、`rtconfig.h` 与起点逐 blob 相同（本轮没碰工程）。
- 本地不动件：1 MiB 快照 before-a/b（各 1048576 B `b89f8a7c…`）与恢复输入 HEX
  （`b3ac97b9…` 3014958 B，快照目录与恢复工程内两处）本轮均按磁盘字节回读一致。
  **口径更正**：上一轮该 HEX 被记为 SKIP，是我方证明脚本把路径猜错（`…/restore/rtthread_restore_input.hex`），
  不是文件缺失；本轮用真实路径复核后两处均一致。恢复下载仍是 `NOT_EXECUTED`，不据此声称可回退。
- 冻结引用未移动：`main=1b3757ee…`、`phase7/full-embedded-software=6d3fbc55…`、
  tag 对象 `097935fe…`、解引用 `e40a9b95…`。

## 6. 本轮首次失败与自身缺陷（一律保留）

1. **`git apply` 在 `core.autocrlf=true` 下把工作树写成 CRLF**：应用后工作树 51229 B / 983 个 CR，
   而索引内 blob 仍是 50246 B 的 LF —— 照此交付会让"本地执行对象"与"远端字节"不是同一份。
   处置：用 `git cat-file` 把 LF blob 原样写回工作树（50246 B、0 CR）后才跑全部检查；未改 git 配置。
2. **证明脚本路径猜错**导致恢复输入 HEX 出现假 SKIP（见 §5 口径更正）；首版输出原样保留在仓外。
3. rc 语义继续按实际记录：基线探针 rc=1（缺口已复现）、历史四件 rc=1（仍不放行）都不是基础设施故障，
   也没有被写成"修后失败"。

## 7. 提交与身份

| 对象 | 身份 |
|---|---|
| 任务起点 | `85522885ec39fbd96f99847a0508c2d7cb137ae6` |
| 起点门字节 | 47964 B，blob `658216314b6c…`，sha256 `ba88f37bb3fd…` |
| 附录 A 补丁（仓外，未进仓） | 7400 B，sha256 `806780ac8830…`（与任务书 §4 记录一致） |
| 附录 B 探针（进仓件） | 2482 B，sha256 `1c7bf5197cea…` |
| 本轮门字节 | 50246 B，blob `0c5994870724…`，sha256 `94e8317577a0…` = 任务书 §4 云端候选逐字节相同 |
| **`gate_code_commit`** | `b187cdd2ffc3225474006d28c1dca3eca591351d`（含且仅含 `tests/host/` 两件；`git cat-file` 该提交里的门对象 = 50246 B / `94e83175…`，正是上面所有结果的执行对象。本轮**没有**上一轮那种"最终字节落在后一个提交"的两态问题：该提交之后门字节未再改动） |
| 证据/交接提交 | `gate_r3_verification.txt` + 本交接 MD/JSON |

绑定规则按 §5.7 执行：`gate_code_commit` 就是**实际包含最终门字节的提交**；若后续再动门字节（哪怕一行），
必须改绑最终对象并在交接里登记差异，不允许只靠前一个提交绑定最终输入。

## 8. 下一步（不是本轮授权）

本附件只预先验证了候选代码，不替代远端最终字节复审。本轮交付停在
`GATE_R3_OFFLINE_READY / AWAITING_INDEPENDENT_GATE_REVIEW`；未发出采集门 GO，也未发出任何硬件许可。
门复审通过后，才把 §5 的**同一份 `5f30d3f0…` 镜像** + 通过的门 + 字节采集脚本绑成一次有界重测包，
由用户一次批准（包内不逐条索要确认）。没有该包与批准，不上电、不连接、不读取、不烧录、不复位、不加 trip。

## 9. STOP

```
GATE_R3_OFFLINE_READY / AWAITING_INDEPENDENT_GATE_REVIEW
FIRMWARE_AND_IMAGE_BYTES_UNCHANGED
NO BUILD / NO FLASH READ / NO PROGRAM / NO RESET / NO SERIAL / NO MOTOR / NO STEP_ENABLE
```
