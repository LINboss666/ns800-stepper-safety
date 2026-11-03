# M8-R7 有界现场验证候选包 · 审核交接（离线准备，设备动作 0）

日期：2026-11-03。执行方：本地 Qoder。上游：云端 A 的
`NS800_M8_R6C_Independent_Review_OFFLINE_GO_20261103.md`
（`overall_offline_release = OFFLINE_REVIEW_GO`，`next_work = PREPARE_BOUNDED_FIELD_VALIDATION_PACKAGE`）。

**本文件不含任何硬件批准，也不代替操作者回答现场安全门。**
`approval.hardware_actions_authorized_by_this_package = 0`，本轮设备动作 0。

## 1. 交付物

| 件 | bytes | sha256 |
|---|---:|---|
| `reports/reviews/phase8-m8-r7-field-package-5ae/field/plan_core_r7.json` | 102115 | `e87dd11ee71bb905…`（`--check` 幂等，源仍冻结） |
| `…/field/rows.json`（离线干跑夹具，非现场实抓副本） | 49352 | `b66e33fced562863…` |
| `…/host/derive_plan_r7.py` | 32805 | `1d7dcc8bf3d23c40…` |
| `…/host/make_rows_r7.py` | 5969 | `522a47c6795c4e78…` |
| `…/host/r7_readiness.py` | 12078 | `f75de2b8d62c06d0…` |
| `…/host/run_transcripts_r7.py` | 4243 | `49b0fd0d027711fe…` |
| `…/host/run_derive_matrix_r7.py` | 4762 | `19387402c6ae58d4…` |
| `…/host/write_handoff_json_r7.py` | 16267 | `ad5d7b5727e72f02…` |
| `…/host/remote_readback_r7.py`（收口一步用，本轮不预写结果） | 16657 | `352ab89dd5ada2d4…` |
| `…/host/validate_plan_r7.txt`（47 项，fail=1，退出码如实 1） | 4280 | `58de0168c7e1f7fe…` |
| `…/host/readiness_transcript.txt`（17/17 OK，退出码 0） | 3219 | `08e2ef952c46fb02…` |
| `…/host/idle_learn_check_r7.txt`（受本轮影响的 120 项回归门，problems=0） | 5937 | `b6e382c8ad9fc9f2…` |
| `…/host/derive_matrix.txt`（派生件五步写入语义，逐步真实退出码） | 1909 | `c4f5741638d1d55c…` |
| `…/run_record.md`、`…/SHA256SUMS.txt` | 见清单 | 清单不哈希自身 |
| `reports/agent_handoffs/phase8-m8-r7-field-package-5ae.md` / `.json` | 本件 / 17341 | JSON 由 `write_handoff_json_r7.py` 现算生成 |

复用件字节未动：EXEC `m8r3_serial_chain.py` 219491 B `33d3f0af8a4c9c33…`、
SELFTEST `m8r3_field_selftest.py` 192823 B `4abcd162a7d0c20d…`、产品 C、固件归档三件 BIN。
派生源是冻结的 `plan_core_r6c.json`（172306 B `408c18ecaf50f62a…`），派生器先校验源散列，不符即拒绝。

## 2. 包的形状（新写，不是改 R6C 模板的批准 ID）

| 阶段 | kind | 动作 | 额度 |
|---|---|---|---|
| S0 | offline | 真实文件与来源绑定（镜像/AXF/map/manifest/needles/提交）；磁盘漂移在动作前具名拒绝 | — |
| S1 | download | 一次 sector 下载 5ae 的 AXF | download 1 |
| S2 | readback | 一次定长 292164 B 回读（`@IMAGE_BYTES=0x47544`）逐字节等于归档 BIN | readback 1 |
| S3 | serial_session | 开口→复位→被动启动窗 75 s→`system_status/safety_status/safety_irq_status/tmc_config_show/motor_status` | port_open 1、explicit_reset 1 |
| S4 | serial_session | 一次 `tmc_config_apply CONFIRM` 取证 `[TMC-CFG] CONFIGURED …` 三条短行 + 三条只读复核 | port_open 1、tmc_config_apply 1 |

镜像三件重绑并四处同源（`identity_binding.image` / `axf` / `tools.download.image` / `tools.readback.image`）：
BIN 292164 `5ae4110d14fdbb40…`、AXF 1773648 `581f1155166374cf…`、MAP 2110730 `4f58a3eea493d8c5…`，
均与 `reports/firmware/phase8-m8-r6a-offline-rework/build_manifest.json` 的
`targets[rt-thread-motor-m1-candidate].images` 现算相符；下载对象 ≠ 上一轮已证在板的 `dab356c6…`。

## 3. 范围与它的理由（A §1 三条优先项里，本包只覆盖两条）

- 覆盖 **启动** 与 **相关短打印**，外加新镜像身份的逐字节闭环。
- **不覆盖"正常 disarm 之后的 Safety 收口"**：那需要 `motor_arm`，而 `motor_arm`/`motor_start` 是执行器的
  运动义务命令（`m8r3_serial_chain.py:1412`），包级 `mechanical_prerequisites.load_means` 仍是
  `NOT_PROVIDED`，执行器会在开端口之前具名拒绝整条链。把运动步骤写进包里再"现场看"只会制造一次
  中途烧额度的失败，所以不写；`motor.c:1883/1885/1889/1892` 那四条 withheld 短行同因不在范围。
  要覆盖它，需要先由操作者给出加载手段的具体档位/字段并登记为 `PROVIDED`，或改走别的取证路径。
- 短打印判据从 `44e493c` 的产品源码字面量再算（`safety_thread.c:374`、`:376,377`、`tmc2209.c:2842/2843/2844`），
  保守上界 80/97/55/38/49 B，全部 <=120 B；`SAFETY_LIMIT_INPUTS_ENABLED=RT_FALSE`（`project_board.h:50-51`），
  故期望 OFF 分支，两条短行缺一按未打全判失败。

## 4. 离线门结果（本机实跑，原文入库）

```text
EXEC --validate-plan --plan field/plan_core_r7.json
  PLAN_VALIDATE_SUMMARY|checks=47|fail=1|msh_commands=74   退出码 1
  唯一 FAIL: PLAN_MOTION_CAP_BUDGET|all|FAIL|caps=[] sum=0 total=0 starts=0
host/r7_readiness.py
  R7_READINESS_SUMMARY|checks=17|fails=0|device_actions=0|serial_ports_opened=0|dap=0|
  downloads=0|flashes=0|readbacks=0|resets=0|motor_starts=0|keil_builds=0   退出码 0
reports/reviews/phase8-m8-idle-stack-r4/host/idle_learn_check.py（本轮把它的 P8 执行器登记升到六层）
  CHECKS=120 problems=0 / META_NEGATIVES=6 fired=6 / META_NEGATIVES_R5=9 fired=9 /
  IDLE_LEARN_CHECK ALL_PASS_WITH_META_NEGATIVES   退出码 0
```

`PLAN_MOTION_CAP_BUDGET` 对无起转的包是恒红门：`caps` 只收 `motion_cap_seconds` 为真值的阶段
（`:2678-2679`），通过条件要求 `caps` 非空且 `sum(caps) <= quota.motor_run_seconds_total`（`:2683`）。
本包不起转，要变绿只能谎报一个非零运动预算。本轮不为了让门变绿改判据，登记在
`motion_budget.registered_checker_gap`，并用 R7-2 三条独立判据（无起转步骤 / 运动与学习额度全 0 /
两个会话阶段的 `forbidden_commands` 都不被步骤命中）作为本包真正该满足的合同。

## 5. 本轮顺手改掉的四处登记缺陷 + 一处撞号登记（都带 R7-7 独立对账）

1. `identity_binding.board_image_before`：模板里还是 `f5c2fd89…` 并称"板现运行"，实际最新一次逐字节回读
   （`phase8-m8-idle-stack-r4/field/field-20261101T142722Z/readback.bin`）证到的是 `dab356c6…/290916 B`。
   本包更正为后者，旧口径放进 `supersedes_in_r6c_template`；R6C 原件不回改。
2. `identity_binding.executor.path`：绝对路径 `D:/ns800/…` 改仓内相对路径，`bytes/sha256` 不变，
   原字符串留在 `path_r6c_template_verbatim`。
3. 赋值行尾逗号把 `motion_budget` 从对象静默写成单元素数组；派生器现在拒绝任何算成 `tuple` 的顶层键。
4. 顶层 `package`/`task_id`/`status` 还写着上一轮的"R5-C 续测包"；改成
   `phase8-m8-r7-field-package-5ae` / `R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED`（执行器不读这三条）。
5. 另加 `stage_numbering_and_legacy_fields`：本包只有 S0–S4，继承的历史字段仍按旧全包 S0–S11 说话且**撞号**
   （旧 S4=巡圈学习要起转，本包 S4=`tmc_config_apply` 不起转），逐块登记为历史口径。

## 6. 落盘口径的一处自伤与修正

本轮最初用 shell 直接重定向归档证据，Windows 控制台默认 cp936，落盘是 **GBK 字节**（本机可读、按 UTF-8 读即乱码，
且与 R6C 那批 UTF-8/LF 证据口径不一致）。新增 `run_transcripts_r7.py` / `run_derive_matrix_r7.py`：
子进程强制 `PYTHONIOENCODING=utf-8`、父进程严格解码（解不出来就失败，不退回 gbk 兜底）、
每步记**它自己的**退出码、整体 LF 写盘。当前三份文本证据都由这两个脚本重新生成。

## 7. 额度是提案

```text
port_open 2 | download 1 | readback 1 | explicit_reset 1 | tmc_config_apply 1 | 其余全 0
numbers_are_proposals_not_authorization = true
approval.status = REQUIRES_NEW_APPROVAL
approval.hardware_actions_authorized_by_this_package = 0
```

旧批准 `4cb9dac0f2a47beb494a4fb41090cefe8da76075` 的 download/readback/explicit_reset 各 1/1 已用尽且**不结转**；
换计划文件或换输出目录都不重新发额度。`tmc_config_apply` 单列：它不起转、不通电到绕组，但会写驱动芯片寄存器，
因此仍需操作者单独批准。

## 8. 保留状态、回读与下一步

交付提交 `ea22ba5`（基线 `f15ba46`，常规 fast-forward push）之后按 Git 协议做了一次**本轮范围**的
逐 blob 回读：`R7_REMOTE_READBACK_SUMMARY|tip=ea22ba52d675|checks=48|fails=0|not_checked=0`（退出码 0），
原文入库 `…/remote_readback_r7.txt`。第一次跑同一脚本时报了 2 条 `frozen …BIN` 失败，
根因是**回读脚本自己**把 R6/R4 的归档 BIN 路径写成 `reports/reviews/...` 而非 `reports/firmware/...`
（取不到对象，不是仓库字节变了）；那次失败原文未删未改，留在
`…/remote_readback_r7_first_run_wrong_paths.txt`（`exit_code=1`）。收口头只归档这两份原文、
它们的清单行、修正后的回读脚本与本 JSON 的 `remote_readback` 字段，不再"回读自己的新回读"。

```text
R7_package              = R7_FIELD_CANDIDATE_PREPARED_NOT_EXECUTED
R7_evidence             = 待 A 独立复审
device_actions_this_round = 0
keil_builds             = 0
motion                  = NOT_INCLUDED(load_means=NOT_PROVIDED)
disarm_closeout         = OUT_OF_SCOPE(需 motor_arm)
SG/ADC 真实加载·阈值生成与有效性·ACTIVE 覆盖·REL_IMPACT·保存恢复·故障恢复闭环 = NOT_VERIFIED
idle1024 带载最坏栈余量 / 现场精确线程交错 / 0x00bd80b0 写入者 = 旧裁定保持
product_field_fix       = NOT_VERIFIED
safe_for_enable         = false
new_hardware_authorization = 0
```

下一步是把这份候选包交给 A 独立复审；现场执行另需**一次由操作者明确发出的批准**，内容至少包括：
5 个非零额度的逐项放行 + 现场物理状态回报（供电、接线、是否允许开 COM5、机械是否静止）。
本轮没有执行任何串口/DAP/烧录/回读/复位/运动，也没有修改任何冻结件。

## 9. R7-F1 现场整包执行（操作者一次批准，跑到 S3 STOP）

A 的独立校正版候选（`plan_core_r7_reviewed.json` 108021 B / `fea903b4f9530cab…`）按原字节接收后，
操作者发出一次整包批准（原文见 `field/field-20261103T132348Z/APPROVAL_TOKEN.verbatim.txt`）。
新批准身份 `M8R7F1-reviewed-bootprint-20261103` 的持久账绑定
计划 `fea903b4…` / 执行器 `33d3f0af…`（R6C 原件未改）/ 镜像 `5ae4110d…` 三件 SHA，
一条 `--dispatch --transport real --backend real --profile response_driven` 走完，工具退出码 **1**。

```text
S0 OK | S1 OK(一次下载, rc=0) | S2 OK(readback.bin 292164 B 与归档 BIN 逐字节相等=True)
S3 STOP FIELD_VALUE_MISMATCH  wrong={"state":["FAULT_LATCHED","READY"], "fault":["8","0"]}
S4 未执行(tmc_config_apply 0/1)
实耗 download 1/1, readback 1/1, explicit_reset 1/1, port_open 1/2; 运动/学习/保存/清日志额度 0 且未尝试
cleanup_errors=[] ; disarm=NOT_ARMED ; PORT_CLOSED 正常收口 ; commit_failures=[] pending=0
```

- **本次唯一成立的现场证明**：5ae 新镜像**逐字节在板**（此前它只是归档件）。
- **本次的失败是产品事实**：启动自检 required 项 `[SS] [REQ FAIL] TMC2209 IOIN read rc=-23` →
  `required=FAIL` → `FAULT_LATCHED code=8`（`safety_state.h:48` = `FAULT_SELF_TEST`）。
  判据没有误拒：A 校正的 `arm gate fail mask` 白名单与 `polarity=not-confirmed` 都按预期放行，
  停因是真值不符。rc=-23 根因未定，不据症状改产品代码。
- **未到达因而仍未验**：`safety_irq_status` 的 `[SAFETY] LIMIT …` 两条、S4 的 `[TMC-CFG] CONFIGURED …`
  三条；"启动到 READY"；`product_field_fix`；disarm 收口。
- **行宽现场事实（两条分开说）**：单条响应 21 行最长 120 B、0 行超 120 B、无 NUL；
  启动被动流 75 分段里 2 个 252 B / 305 B，含 3 个裸 NUL（1318/2268/2395）与整体非法 UTF-8（首错 1317），
  一条分段里咬着 2–3 次并发打印。同时登记：被动启动流只过危险文本门，不过 NUL/UTF-8 门。
- **板最后可读状态（执行当时）**：输出停在安全态 —— DRV_ENABLE pad LOW 已证、request=LOW、pad=0、
  ENN 在禁能侧 verdict=OK、`[Motor] armed=0`、arm gate REFUSED、UI SAFE-OFF；Safety 处于
  `FAULT_LATCHED fault=8`。断电/上电或 `fault_reset` 人工恢复复核需操作者另发批准。
- 纪律：不重跑整包、不加次数、不改判据/计划/执行器，剩下 1 次 `port_open` 与 1 次 `tmc_config_apply`
  不用来"顺手再看"。全部原件（含首错与原始字节）已入本目录，交 A 按真实字节复审。
