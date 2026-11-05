# M8-R7F2B:safety_status 的 ENN_FEEDBACK 长行拆短 + 一次候选目标构建(离线轮)

日期 2026-11-04。复审基线 `70deff806e8b548dee23afb06a6fcdf20937b015`(上一场 VM 上电续测的归档提交)。
输入 = A 的复审附件 `NS800_R7F2B_A_Review_and_Print_Fix_20261104.zip`(13049 B, sha256
`9fad8f1d1a917bda350047725562279e5a43f971b7a828254443d8fffc04b6e1`;8 个内件逐条对过 SHA256SUMS.txt)。

**本轮设备动作 = 0。** 未下载、未 Flash、未碰 DAP、未复位、未开串口、未起转、未 learn/zero、未 config_save。
新镜像 `502dca5d…` **不在板上**; 板上仍是 `b4243886…`(逐字节回读证据见 §6)。

## 1 A 的 5 项与本轮落点

| A § | 要求 | 落点 |
|---|---|---|
| 1 | 应用 `safety_status_short_lines.patch`,只改 safety_gpio.c 两条打印,应用前 `git apply --check` | 已应用。`--check` rc=0 → 应用后 `git diff --numstat` = 2 增 2 删;blob `b242dfad1fed0c5b5be5869febef6375e2708a8f` → `2055c7496be91830709155a9ffb0720a0b0bdb8d`,与补丁声明的 postimage 一致 |
| 2 | 用新文件承接下一场 rows,保留旧 b424 文件与所有现场原件;A 的两条命令标 `source_shape` | `reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/rows_r7f2b_source_shape.json`(54287 B, `80466bc3a23c60dd2b86a59b91f2e4f8b63d52ac25db49c490cb72c15669daa5`)。底稿 = `rows_startup_b424.json`(53635 B, `916eddaa…`)**逐字节未动**;只替换 `commands.safety_status` 与 `commands.motor_status`,A 的行与 `_provenance` 一字未改,另加 `_derivation_r7f2b`(含被替换掉的旧行原文) |
| 3 | 把 `motor_status_step_replacement.json` 的 step 放入下一场新计划;**不改执行器** | 已放入草稿计划 S3(见 §4)。执行器仍是 R6C 原件 219491 B `33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca`,本轮一次也没改过它 |
| 4 | 新 rows 不得继续假装 `arm_gate_mask=0x0 ALL-PASS`,要用 0x18 REFUSED + `mot_print_gate_fail(0x18)` 的 gate 明细行 | 新 rows 的 motor_status 是 0x18 REFUSED 并带 gate_reason/gate4..7 全套明细(共 25 行,全部标 `source_shape_predictions_NOT_board_capture`) |
| 5 | 针对性验证后**只构建一次**既有 Keil 候选目标,交付新 BIN/AXF/MAP + 日志 + manifest;旧 b424 身份与旧批准不套到新镜像 | 见 §3 与 `reports/firmware/phase8-m8-r7f2b-safety-print-fix/build_manifest.json` |

## 2 主机侧验证(全部离线)

- **受影响面**:`io_main.c` 用 `#include "safety_gpio.c"` 真编译被改文件,所以跑的是独立 I/O 行为链
  `tests/host/run_io_behavior.sh`(不是 sp_conc 那一链,它不编译 safety_gpio.c)。
  4 个变体全 PASS(off_target 33 / on_target 35 / off_ovr 46 / on_ovr 48 必需用例,自报 fail=0 proc_rc=0),
  非法宏值按预期编译拒绝 rc=1。原文 `host/io_chain_r7f2b.txt`。
- **needles**:`--prove-needles needles.json`(40 条)对改后源码树,unproven=0。
  被拆的 ENN 文本不在任何 needle 里(needle 表内搜 `ENN_FEEDBACK` / `只读输入` = 0 命中),所以这条链没有被动到。
- **A 的控制台模型逐条复算**(`host/enn_model_repro.txt`):
  修前 = 471 B / 1 个裸 NUL / 非严格 UTF-8 / 最宽行 214 B,其中 `intended=150 emitted=127 truncated=1`;
  修后 = 503 B / 0 NUL / 严格 UTF-8 / 最宽行 106 B,ENN 两次调用 `intended=64` 与 `intended=95`,truncated=0。
  与 A 的 `verification.json` 逐项一致。
  **本轮如实登记的失败**:A 的 `host_before.c` 按原样在本机 MinGW 4.9.2 编译后**段错误**(rc=3221225477),
  原因是本机 msvcrt 的 `vsnprintf` 在截断时返回 −1,模型的 `k` 变成 `(size_t)-1` 后 `fwrite` 冲过缓冲区。
  加 `-D__USE_MINGW_ANSI_STDIO=1` 取 C99 stdio 后与 A 完全同值。这是**量具的编译器可移植性问题,不是被测缺陷**,
  失败原文保留,不删不改。
- **108 组实参扫描**:修后 0 NUL / 0 越钳位 / 0 半截 UTF-8,最宽 106 B;修前 108/108 都有 NUL 且都有 >127 B 行,
  90/108 还出现半截 UTF-8 ⇒ 缺陷与实参无关,修后与实参无关地安全。
- **提取保真度**:A 的两份提取件与产品 `safety_status()` 函数体逐行比对,47 行 vs 47 行,双向差异 0。
- **motor_status 片段用真实执行器回放**(`host/motor_step_repro.txt`,`--transport stub --backend sim`,
  不带 `--approval-id`,不碰设备):原步骤对已知的 `[MOT] arm_gate_mask=0x18 REFUSED` 报
  `STOP / BUSINESS_FAILED`(A 的误拒成立);A 的替换片段在同一输入上 `OK`;
  7 项突变仍各自具名停止(armed=1→FIELD_VALUE_MISMATCH、请求 HIGH→PROFILE_NOT_AT_REST、
  0x1A→BUSINESS_FAILED、额外拒绝行→BUSINESS_FAILED、缺字段→PROFILE_NOT_AT_REST、
  重复字段→FIELD_NOT_EXACTLY_ONE、health 降级→FIELD_VALUE_MISMATCH)。

## 3 一次构建与新镜像身份

命令(工作目录 = `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad`):
`C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 -o …/build/rt-thread-motor-r7f2b.log`
rc=0,`0 Error(s), 0 Warning(s).`,armclang `V6.24`,`Build Time Elapsed: 00:00:10`,
`Program Size: Code=205930 RO-data=86198 RW-data=2192 ZI-data=85596`
(上一场同目标是 Code=205922 RO-data=86186 ⇒ +8 / +12,RW/ZI 差 0)。

| 产物 | 字节 | sha256 |
|---|---|---|
| `rt-thread-motor-m1-candidate.bin` | 292708 | `502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a` |
| `rt-thread-motor-m1.axf` | 1775972 | `fc567635d98a76cfb5bb32c3df27f611061bdc39887c90fce75da44dac8d86f8` |
| `rt-thread-motor-m1.map` | 2114829 | `fc33986f3e5928d2724812ebe1be7609230312a38d46d0ca4f87806067c1a0db` |
| `rt-thread-motor-m1-callgraph.htm` | 1078228 | `39134ee1…`(见 manifest) |

BIN 比 b424 多 **24 B** = 第二段格式串成为独立常量(自带终止 NUL 与对齐)。

**改动确实进了这一版链接产物**(镜像内按 NUL 分串枚举带标签前缀的格式串,`host/image_strings_r7f2b.txt`):
合并形态 1→0 次;拆分后 A 段与 B 段各 1 次;ENN 格式串 147 B→61 B;>127 B 的 tagged 串 36→35 条。
> 格式串长度 ≠ 运行时展开长度,运行时长度由 §2 的 host 模型承担。

**登记未改**:同一枚镜像里仍有 35 条 >127 B 的带标签格式串(最宽 257 B 的
`[MOT] !! 业务失败(原错误=%d src=%s)…`)。A §5 的范围只到这一处,本轮不扩改;要不要逐条清点请 A 定。

## 4 下一场的承接件(草稿,批准数 0)

`field/plan_r7f2b_newimage_draft.json` 由已复审计划 `plan_startup_b424_reviewed.json`(逐字节未动)派生,
派生编辑逐条记在 `derivation_edits`。要点:
- 下载件/回读件/identity_binding 全换成本轮 AXF/BIN;回读长度规则不变,数值变 292708;
- `board_image_before` = b424(`b4243886…`,292684 B),其板级**字节**证据来自
  `field-20261103T161728Z/readback.bin`;VM 上电那次(`field-20261103T163850Z`)只复位+采集未回读,如实写明;
- S3 同会话装 4 条只读查询 + 启动流:`ps/free/system_status` 逐字未动,`motor_status` 换成 A 的片段,
  新增 `safety_irq_status`(A README「构建后的一次现场目标」建议四条同会话)。
  **该步判据刻意只留 `expect_contains: ["[SAFETY] "]`** —— 本轮没有它的新固件行形预测,不替它编;
  缺口登记在 rows 的 `commands.safety_irq_status._provenance_r7f2b_gap`;
- 额度提案 1/1/1/1(下载/定长回读/显式复位/COM5 开口),其余全 0;
  `approval.hardware_actions_authorized_by_this_package = 0`,`status = AGENT_AUTHORED_DRAFT…REQUIRES_NEW_EXPLICIT_OPERATOR_APPROVAL`。
  本件不创建任何批准身份,也不向操作者索要批准(A:「等新固件身份出来后一次确定实际现场范围」)。
- `tmc_config_apply / CONFIGURED 短打印验收` 明确**不在本草稿里**:A 说那是需要另加一次配置写入及其判据的另一件事,
  不算只读查询,也不得沿用旧写入额度。

离线门 `--validate-plan`:38 项 checks,**fail=1**,唯一 FAIL 是
`PLAN_MOTION_CAP_BUDGET|all|FAIL|caps=[] sum=0 total=0 starts=0` —— 该检查要求 caps 非空,而本包按 A §5 不含
任何起转/学习/阈值/保护触发步骤,属结构性红。A 已在 R7 轮核实并裁定这类零运动链包记 N/A;
不改执行器、不为凑绿塞入动作。原文 `field/validate_plan_offline_draft.txt`。
其余 37 项 PASS,含 `PLAN_IMAGE_IDENTITY_CONSISTENCY`(S0=下载 AXF=回读 BIN 同源且不认旧镜像)、
`PLAN_ROWS_KEY`(新 rows 的两条命令与新增的 safety_irq_status 都取得到行)、`PLAN_BOOT_WINDOW_COVER`(75+6+3=84 s ≥ 75 s)。

## 5 勘误(旧证据不回改)

上一场把这条行记成「150 B 正文 + LF = 151 B」。按 C 转义展开,源码里的 `\n` 是 2 字节但运行时是 1 字节,
所以正确口径是 **149 B 正文 + LF = 150 B**(与 A 一致)。越钳位 23 B 与「前 126 B 与源逐字节相等」两条结论不受影响。
已提交的文件按字节冻结原则不回改,勘误登记在 `build_manifest.json` 与本件。

### 5.2 本轮自纠(第二次提交的内容)

远端逐 blob 回读时发现两件派生件自身的缺陷,当场改掉并重新提交(**不回改第一次提交 `275c37f`**):

- **路径形态**:生成器用 `str(WindowsPath.relative_to(REPO))` 取仓库相对路径,得到的是反斜杠形式
  (`reports\firmware\…`),那不是合法的仓库路径,换机器或按路径取对象都会错。已全部改为正斜杠形态,
  新 rows 54299→**54287 B**(`80466bc3…`)、新计划 30293→**30270 B**。
- **错引用**:计划里把上一场 VM 上电的启动原件写成 `S_S3/01_boot.rx.bin`,目录实名是 `S_S3/01_rst.boot.rx.bin`。
  已按目录实名更正。这次是"凭记忆写文件名"造成的,教训是引用现场文件必须先列目录。

改后重新跑了 `--validate-plan`(仍是 38 项 / fail=1,同一项 GF2),并重算了两份 SHA256SUMS。

## 6 原件保护

未改动:`reports/firmware/phase8-m8-r7f1-startup-c-fix/**`、`phase8-m8-r7f1a-minimal-c-patch/**`、
`field-20261103T161728Z/**`、`field-20261103T163850Z/**`、`rows_startup_b424.json`、
`plan_startup_b424_reviewed.json`、`plan_startup_b424_vm_restart_agent.json`、执行器与批准台账。
本轮新增的两个目录在 `.gitattributes` 里加了 `-text`(必须在提交前生效:`.map` 与 `.htm` 在
`core.autocrlf=true` 下会被判成文本并剥掉 CR,加规则后四条产物全部 blob == 磁盘字节)。

## 7 结论分级

- 已证(离线):补丁应用正确且进了这一版链接产物;A 的模型/片段/108 组结论在本机可复算(附一处量具可移植性失败,原文保留);
  新镜像身份已定并逐件入档。
- **未证**:新镜像在板上(0 次下载)、新镜像的 safety_status 真流不再截断(需下一场同一会话的原始字节)、
  SG/ADC/IMU 相对保护、正常 disarm 收口 —— 全部 NOT_RUN。
- 本轮**不**等于任何现场验收。

## 8 R7F3 登记(A 技术签收 b502 定发包,设备动作仍为 0)

A 复审 `NS800_R7F3_A_GO_B502_Startup_Print_20261104.zip`(41287 B,`1699735a…747a`,技术复审基线 `99414c7`)
把本镜像的现场范围定稿,并**直接修好了我派生件的两处 S0 阻断**(记为 GF7):S0 适配件的 `targets[].images` 键
必须是实际归档文件名(我写成了 build_manifest 的字典键 `bin/axf/map/callgraph`),四个提交字段必须是仓库里真实存在的提交
(我上一轮把值留成 `null`)。两处都在**未改动的执行器**上复现:草稿计划 STOP 于 `S0_IMAGE_NOT_IN_MANIFEST`,
只修键名的仓外探针接着 STOP 于 `S0_COMMIT_NOT_IN_REPO`;原文进 `offline/`。

本机独立复算与 A 逐项一致:bin/axf/map 三方(A `IDENTITY.json` / `build_manifest.json` / 磁盘)同值;
AXF 的唯一 PT_LOAD `p_paddr=0x08000000 p_filesz=292708` 与 BIN **逐字节相同**(段 sha256 = `502dca5d…`);
`safety_gpio.c` == postimage `2055c749…`;main 栈两处配置仍是 4096 且与上一轮登记的编译输入逐字节相同;
`--validate-plan` 38 项 / fail=1(仍是 GF2 那一项),本机前 38 行与 A 的 `validate_plan.txt` 逐字节相同;
`--dispatch --transport stub --backend sim`(不带 `--approval-id`)跑通 4 阶段,`quota_used` 四项各 1、
`actions_attempted=[]`、`APPROVAL_LEDGER|NONE`。执行器仍是 R6C 原件 `33d3f0af…`,未改。

上一轮的 GF3(`safety_irq_status` 无新固件行形预测)由 A 用实际 C 函数提取件关闭(ENN 说明行在正常已解析输入下
124 B 含源 LF,可完整输出;仍属 source_shape)。GF1/GF5 A 已接受且不要求重跑旧量具;GF4(其余 35 条超长带标签格式串)
继续保持缺陷登记。原始构建清单未被 A 改动(`059bebe6…` 现算仍在),旧草稿/旧适配件/现场原件一律不回改。

**停点**:A README 第 4 行原文「技术签收通过,等待操作者对本固定包的一次明确批准。此文件本身不是硬件批准」。
批准后按 A 的规定:用已落位的三份件 + 新批准身份 `M8R7F3-b502-startup-print-20261104` 在既有持久账登记并 dispatch 一次
(下载 1 / 定长回读 1 = 292708 B / 显式复位 1 / COM5 开口 1,其余全 0,失败即停不重试),不再交准备轮、不重复确认同一范围。
本轮**没有**创建批准身份、**没有**碰设备;现场安全门不由我代答、转述或自填。

## 9 R7F3 现场执行(操作者批准后,一次整包)

批准:操作者对本固定包选答「批准，按该范围执行一次」并回报「已核对：供电在、接线无未说明变化」;
两条答复逐字拼成令牌(94 B,`0150b36c…`;其中「（推荐）」是我选项标签自带后缀,未替其删改)。
批准身份 `M8R7F3-b502-startup-print-20261104` 由未改动执行器 `--approval-init` 离线开立,身份三件
plan `080c2297…` / image `502dca5d…` / executor `33d3f0af…`。

执行根 `field/field-20261104T001444Z/`,`--dispatch --transport real --backend real --profile response_driven`
一次跑完 S0→S3:`DISPATCH_SUMMARY status=OK`、4 阶段全执行、台账 `writes=8 pending=0`,
真实消耗 **download 1 / readback 1 / explicit_reset 1 / port_open 1**,其余全 0,无重试。

取得的项(A §本场必须取得 逐项;28 条判据 fail=0,原文 `FIELD_JUDGEMENT.txt`):
回读 292708 B 与 BIN **逐字节相等**(新镜像在板);`[SS] state=READY fault_code=0` + `SELF_TEST -> READY`,
无 `FAULT_LATCHED` 无 `[REQ FAIL]`;`[REQ OK] TMC2209 IOIN=0x21000041 VERSION=0x21`;
ps `main … 0x00001000` = 4096;free `total 32672 / used 22120 / maximum 22120`;
**safety_status 522 B / 10 行 / 0 裸 NUL / 严格 UTF-8**,ENN 两条短行 64 B 与 95 B(含 LF)且与 A 预测行逐字节相等
—— 上一场同一命令是 342 B 且偏移 295 有一个裸 NUL;启动流 3468 B / 76 行 / 0 NUL / 最长 126 B;
motor_status 静止/未武装/当前禁能合同 + `arm_gate_mask=0x18 REFUSED` + gate 明细;safety_irq_status 两条 LIMIT 短行、
`irq gate = CLOSED`、`protection_ready = NO`、最长 124 B(= A 预测值)。三条查询的预测行 7/7、25/25、9/9 全中。

工具侧失败原文(均未触设备,台账 `spent={} writes=0`):我的包装器两次 Python 写法错误、一次漏传
`--profile response_driven` 被 `REAL_PROFILE_NOT_RESPONSE_DRIVEN` 具名拒绝(该拒绝根 `field-20261104T001348Z/` 一并入档);
另第一版判读脚本两条错谓词造成 2 条假 FAIL,修正后重判 28/28 —— **现场字节未变,变的是我的判据写法**。

未证不扩大:SG/ADC/IMU 保护、正常 disarm 收口、带载最坏栈、CONFIGURED 短打印、其余 35 条超长格式串;
`core_protection_verified=False`。板端:VM 供电保持、显式复位后自由运行、未起转、对 Flash 只写过那一次 sector 下载。

## 10 R7F4 登记(A 签收 R7F3 + 定稿 CONFIGURED 一次 apply;设备动作仍为 0)

输入: `NS800_R7F4_A_GO_B502_CONFIGURED_20261104.zip`(36146 B,`337cb3058ee2…`;8 内件 + 包内清单自洽,清单不哈希自身)。
四方身份 **A 登记 = IDENTITY.json = 磁盘** 三方一致: plan 33012 B `0b6e1eb8a8c6…` / rows 60116 B `64e8a8687639…` /
执行器 219491 B `33d3f0af8a4c…`(R6C 原件,本轮一字未改) / BIN 292708 B `502dca5d8c7e…`。
两份 field 件按包内相对路径落位且 zip 内字节 == 落位字节; `a_review/` 5 件与 README 逐字节相同
(`offline/package_integrity_r7f4.txt`)。
(A 的包内清单 `a_review_r7f4/SHA256SUMS.txt` 765 B `c6a741c7e922…` 按本轮清单的**按文件名排除**规则不进 `SHA256SUMS.txt`
 —— 与上一场 `a_review_r7f3/SHA256SUMS.txt` 1117 B 同一口径; 两者的磁盘 sha256 逐条记在 `handoff_R7F4_registered.json`
 的 `reviewed_files_archived` 里, 第三方可按清单头写明的那条排除规则原样复算。)

A 的结论: **R7F3 的启动与指定短打印验收通过,可以收口**; 并自限"不代表全部 35 条长格式串已修复,也不代表
SG/ADC/IMU、带载最坏栈或正常 disarm 已过现场验证"。下一场定为一次 **CONFIGURED 验收**(一次 `tmc_config_apply CONFIRM`),
沿用已逐字节证在板的 b502,**不重新编译、不下载、不回读、不复位**。

本机独立复算(全部在未改动执行器上,`offline/` 原文):
- `--validate-plan` = **33 checks / fail=1**,唯一 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`(A 按零 arm/start 包登记 N/A);
  `PLAN_BOOT_WINDOW_COVER` = **NA**(本场无启动,不申报 75 s 窗)。与包内 `a_review/validate_plan.txt` 同源。
- 一次 stub/sim 正例(不带 `--approval-id`): rc=0、`stages_executed=2`、`port_open declared=1 used=1`、
  `tmc_config_apply declared=1 used=1`、`APPROVAL_LEDGER NONE` —— 没消耗任何真实批准,也没起设备工具。
- 6 个负例(只在仓外的夹具副本上定向改写): `pre_fault`/`pre_not_idle`/`pre_already_configured` →
  `STOP / FIELD_VALUE_MISMATCH` 且 **apply_used=0**; `apply_refused` → `BUSINESS_FAILED`、`apply_nul` →
  `NUL_BYTE_IN_RESPONSE`、`post_not_configured` → `FIELD_VALUE_MISMATCH`,三者 **apply_used 恰为 1**(不重试)。
  **6/6 与 A 登记的码一致** —— A 的两条安全声称(前置不满足不烧 apply、进 apply 后不重试)独立复现。
- CONFIGURED 八条预测行(已代入具体值)实测最长 **63 B 含 LF**,远低于 A 声称的 120 B 界限。

我自己这一轮的**两处口径修正**(都不触碰 A 原件,也不触碰上一场现场字节):
1. 包内 `SHA256SUMS.txt` 自己那一行我先标成"不符"——它按规则不哈希自身,已在档中正名为 `SKIP(清单不哈希自身字节)`,其余 7 条 OK。
2. 第一版 `handoff_R7F4_registered.json` 把负例一致数写成 `10`(场景仍写 6,自相矛盾): 裸 `neg.count("MATCH")` 被
   `FIELD_VALUE_MISMATCH` 里的子串 `MATCH` 吃掉 4 次。改为只数行尾 ` MATCH` 得 **6**,与转录 `负例复跑: 场景=6 与 A 登记一致=6 不符=0`
   对齐,并加了"子串计数必须大于行尾计数"的自检——码名一旦不再含 `MATCH`,该判据会当场报错而不是静默失真。
   负例复跑结果与 `offline/negative_repro_r7f4.txt` 字节未变(`5e6d286107bc…`,979 B),改的只是我自己的计数。

固定范围(批准后,A 已定稿,详见 `handoff_R7F4_registered.md` §2): 批准身份 `M8R7F4-b502-configured-20261104`;
**COM5 开口 1 次(一个连续会话)+ `tmc_config_apply CONFIRM` 1 次**,下载/回读/复位/arm/start/运动/学习/零点/提交/保存/
清日志/开 IRQ/极性确认/重试/其他写入全 **0**;会话硬截止 90 s,本场无启动窗。一次 apply 是一次命令事务(内部四寄存器写 + 逐次 IFCNT),
不记成四次 apply。顺序 `system_status`→`motor_status`→apply 一次→`system_status`→`motor_status`→`safety_status`;
写后预期 `gate5=CONFIGURED`、`arm_gate_mask` 由 `0x18` 降到 `0x8`(保护未就绪那一条拒绝**保留**),
`CONFIGURED` 与 `protect_ready=NO` 可同时成立;计划参数 irun 20 / ihold 8 / ihold_delay 4 / tpowerdown 8 / vsense 1,
`IHOLD_IRUN=0x00041408`、`TPOWERDOWN=0x00000008`(理论值,本场不测电流或转矩)。

**停点**:A README 原文「本包当前硬件批准数为 0」。所以本轮**未创建批准身份、未开口、未发 apply**,设备动作全 0;
现场安全门不由我代答、转述或自填。批准后按 A 的规定:落位件 + 新台账 + `--dispatch --transport real --backend real
--profile response_driven` 一次,不再交准备轮、不重复确认同一范围。

## 11 R7F4 现场执行(操作者批准后,一次整包)

批准:操作者对本固定包选答「批准，按该范围执行一次」并回报「已核对：供电在、接线无未说明变化」;
两条逐字答复拼成令牌(82 B,`98588b80…`)。批准身份 `M8R7F4-b502-configured-20261104` 由未改动执行器 `--approval-init`
离线开立(三件身份 plan `0b6e1eb8…` / image `502dca5d…` / executor `33d3f0af…`),开账 `spent` 全 0。

执行根 `field/field-20261104T015928Z/`,`--dispatch --transport real --backend real --profile response_driven` 一次:
`DISPATCH_SUMMARY status=OK`、`stages_executed=2`,真实消耗 **port_open 1 / tmc_config_apply 1**,其余全 0,无重试;
本场不调用 DAP 故未读 UID 文件;会话末事件 `t_s=0.610 s` < 90 s 硬截止(本场无启动窗,不申报启动观测窗)。

取得的项(包内判据 77 条 fail=0,原文 `FIELD_JUDGEMENT.txt`):
一次真实配置成功 —— `[TMC-CFG] CONFIGURED (各次写入均经 IFCNT 确认)`,响应内无 `REFUSED`;**八条 `[TMC-CFG]` 完整短行全在**,
响应 388 B / 8 内容行 / 最长 62 B / 0 裸 NUL / 严格 UTF-8;打印参数 `irun=20 ihold=8 dly=4 tpd=8 vsense=1` 与计划一致
(换算 `IHOLD_IRUN=0x00041408`、`TPOWERDOWN=0x00000008`;这两个寄存器本场未单独读回打印,电流未测——打印原文自写"仍为理论计算")。
`drive_cfg` `UNCONFIGURED`→`CONFIGURED`、`gate5=CONFIGURED`、`arm_gate_mask` `0x18`→`0x8` 且唯一保留的拒绝是
`gate_reason=protection-not-ready`;`CONFIGURED` 与 `protect_ready=NO` 同时成立(A 预告的预期结果,不为变绿去开 IRQ/确认极性)。
写后仍禁能:`runtime write gate mask=0x0`、`request=LOW pad=0 ENN_fb=1 verdict=OK`、`MCU_DRV_ENABLE = 0 (禁止(安全))`、
`armed=0 cur_hz=0 tgt_hz=0`、`irq_gate=CLOSED polarity=not-confirmed`。六条响应 NUL 全 0、UTF-8 全完整、最长行 120/91/62/120/91/105 B。
GCONF/CHOPCONF 前后值只记录(`00000101->00000141` / `15010053->15030053`,差异位 `0x40`/`0x20000`),**语义本场未解码**。

步骤身份不靠标签:用执行器自己的 `stage_steps + canonical_steps_sha` 从仓库计划重算 `11d42060d97de6b6…`,与现场台账
`steps_sha256` 相同;六份响应原文首行回显(从字节读)也与计划六步同序。上一场那三条工具侧教训本轮已避免:令牌从文件逐字节读并
以 argv 传入(不经 shell 代码页)、路径用字符串拼接、`--profile response_driven` 显式带上。
附加对照 5 条(预测行表逐字节,非包内判据)也 0 不符:`21/21`、`27/27`、`21/21`、`26/26`、`9/9`;apply 里 6 条命中,
未命中的 2 条正是 A 的 provenance 标为示例值的时延(`50/1500 tick`,预测 300)与 TX(`TX=4B`,预测 32B)——未当断言也未算失败。
陈旧提示符 5 B `msh >` 由执行器单独存为 `02_mot_pre.pre.rx.bin` 并登记散列,未追加进响应原文。

未证不扩大:电流/转矩实测、SG/ADC/IMU 相对保护命中、正常 disarm 收口、带载最坏栈、其余 35 条超长格式串、IRQ 开路与极性确认;
执行器自报 `core_protection_verified=False`(`flow_complete=True` 只说申报阶段跑完)。台账 `writes=4` 是落盘次数,不是寄存器写次数。
板端:驱动 CONFIGURED、未武装、频率 0、请求 LOW、焊盘 LOW、ENN 在禁能侧、保护门与 IRQ 未动、未起转,VM 供电保持,端口正常关闭。
**额度已用尽**:本包 port_open 1/1、apply 1/1,旧批准与剩余额度不结转;下一场(极性/IRQ、短动作、学习与目标通道触发)要 A 重新出包并对新范围再批一次。

## 12 R7F5 登记(A 出"盲测一次静态 IRQ"包;设备动作仍为 0)

被审件 `NS800_R7F5_Blind_IRQ_Static_Trial_20261104.zip` 60623 B `b326cd8543eec1db…`,33 条目,内层清单 32/32 OK 且不含自身。
基线 `0cd6458`(= 计划里的 `reviewed_delivery_commit`,登记时 HEAD 与之相等);落位两件按相对路径放入 `field/`:
plan `ee3b2651…` 49994 B、rows `4f094de3…` 22289 B,散列与 `IDENTITY.json` 逐字节相同。

我这边独立复算 24 项全 PASS(`offline_r7f5/`,fails=0),关键几条:

- 步骤摘要用未改执行器重算 `4a80dcb6dd43028c…` == A 声明;S0 绑定除路径外逐字段一致(`needles_total=40`、
  `plan_placeholders=0`、`plan_forbidden_hits=0`);`validate_plan` 57 项 fail=1,我的 57 行输出与 A 落盘件逐行相同。
- 唯一 FAIL 是 `PLAN_MOTION_CAP_BUDGET`(要求非空运动上限)。本包固定零 arm/start/learn,按 **N/A** 登记:
  不填假运动预算,也不改检查器把它抹掉,更不写成全绿。
- 8 个整包回放(正常 1 + 反例 7)状态/阶段/额度实耗逐例与 A 一致;正常场景只消耗四项;
  故障前置没发 apply;DIAG 已活动没置标志没开门;台账里没有任何 `motor_arm`/`motor_start`。
- rows 的 7 条"真实旧回包"用 `split_rows` 重切仓库归档原件后逐行相等、零 NUL;其余 12 条是 source_shape 预测,
  分类登记,不充当本轮现场证据。
- A 的 host 桩我用本机 gcc(`Dev-Cpp/MinGW64`,参数同 A)重编重跑六组:归一行尾后逐字节相同,
  原始字节**不等**(A 在 Linux 跑,stdout 是 LF;本机 Windows C 运行库写 CRLF)。
  逐行比对只取 `[SAFETY]` 负载行,因为交付 rows 的 `safety_irq_status@*` 另含命令回显与行末 `msh >`
  (来自真实串口捕获)——被排除的行原样登记,不静默过滤。形状证据只证形状与流程,不证中断交付/时序/物理极性。

本包固定例外(照计划原样登记,不扩大):`polarity_measured=false`、`trial_only_software_flag_override=true`、
`core_protection_verified=false`。`safety_polarity_confirm CONFIRM` 只置 RAM 标志;源码那两条
`operator must have MEASURED` / `MARKED VALIDATED` 保留。**风险点如实写明**:第 10–11 步 `mask=0x0 ALL-PASS`
意味着固件此刻会**接受** arm/start——挡住它的是步骤清单与 `forbidden_commands`(S0 级结构门),不是固件自身拒绝;
收口后由操作者切断 VM+MCU 供电清掉易失标志。

停点:**等操作者本人批准**(新身份 `M8R7F5-b502-blind-irq-static-20261104`,旧批准与剩余额度不结转,
令牌必须用本次真实原话),并回报 MCU/VM 供电状态、接线是否沿用、**DAPLink/COM5 是否已恢复在位**
(上一轮诊断时探针不在位)。批准前不跑 `--approval-init`,不跑 dispatch。

提交前预检(`tools/r7f5_staged_preflight.py`):清单 146 条 登记值==暂存 blob==磁盘字节(problems=0),
两类自检均 FIRED。泄漏扫描按本轮暂存集判(47 件命中 0);另如实登记 2 条**历史**命中——
`offline/package_integrity.txt` 与 `offline/package_integrity_r7f4.txt` 首行当年记了交付 zip 的本机绝对路径
(含用户名目录),那是已推送的冻结件,本轮不回改、也不让它们 retroactively 判红;后续新轮只记文件名与散列。

## 13 R7F5A 登记(A 补两路 LA 采集范围;设备动作 0)

被审件 `NS800_R7F5A_LA_DIAG_ENN_Task_20261104.zip` 66179 B `575a46cefc761423…`,5 条目;内层清单 4 条
**4/4 逐字节相同**且不含自身。嵌入的 `original_R7F5.zip` 60623 B `b326cd85…` 与 §12 已登记的 R7F5 原件身份相同
(按任务书第 28 行:解在仓库外,只把两份 field JSON 按声明路径入仓——那两件上一轮已放,本轮只复算不重放)。
基线 `0cd6458` = 计划 `reviewed_delivery_commit`;登记时 HEAD 与它之间改动**全在 `reports/` 内**,
产品源码/固件/执行器零改动。四件执行输入实算 4/4 与声明相同。

**旧账只读核查(任务书第 23–26 行的分支门)**:`approval_M8R7F5-b502-blind-irq-static-20261104.json`
`version=2`、`spent` 全 0、`pending` 空;用**未改动执行器**的 `validate_ledger_doc` 以当场重算的三件身份传入不拒绝、
把 plan 身份改成 64 个 0 再传则具名拒绝(即"账有效"由执行器自己的校验器给出,不是手写比较)
=> 走**新身份** `M8R7F5A-b502-la-two-channel-static-20261104`,旧批准按任务书标为被替代,随后不得再跑旧盲测。

离线判据 **56 项 PASS,fails=0**(`offline_r7f5a/`):步骤摘要重算 `4a80dcb6…` 与上一轮同(本包不改串口计划);
16 步无 `motor_arm`/`motor_start` 且 `forbidden_commands` 覆盖;`--validate-plan` 仍 `checks=57 fail=1`,
唯一 FAIL 是 `PLAN_MOTION_CAP_BUDGET`,按 §12 口径登记 N/A。额度对照:`port_open/apply/irq_disable` 同名同值;
计划的 `irq_polarity_prep=2` 在补充范围被拆成 `polarity_confirm 1 + irq_enable 1` 重述(相加=2);
**计划为 0 的动作没有任何一项被放宽成正数**。仪器额度由外层记录,串口执行器不扣减也不强制——不谎称原子联动。

LA 侧本机实测(`la_local_r7f5a/`,含两条**具名拒绝原文**):Logic 2 `2.4.48` 的 MCP `127.0.0.1:10530` 可用
(15 个工具;10430 当时无监听,本轮未依赖);不支持可调输入阈值电压;数字采样率只接受 25 kS/s–24 MS/s 共 13 档
(任务书希望的 1 MS/s 在档内);两条拒绝都在参数校验期返回 `isError=true` 且**不返回 captureId**(未建采集会话);
**导出侧通道筛选不生效**(请求 1 列实得 8 列)=> CSV 列号与信号的对应必须现场核。
`la_smoke_capture_digital.csv` 是探头未接的 1 秒定长采集(`D0` 全程 0、`D1–D7` 全程 1、无状态变化),
属浮空态,标 `CONTROL_PATH_SMOKE`,不作电平证据;它发生在本任务书下达前,不占本轮 `logic_capture_session=1`。

两条更正照单登记:(1) 我上轮说"Logic 1.2.17 完全没有远程/脚本 API"是**过度推断,已撤回**——
当时实测到的只是端口无监听、安装目录无 Logic 2 资源、pyusb 无 libusb 后端;任务书第 61 行指出官方确有
Legacy Logic 1.x API。(2) 任务书第 58 行"不要先做试采消耗一次机会"与本机事实存在时间差,如实登记不解释成已满足。

**三项前置未闭合**(照 `preflight_required` 登记为 OPEN,不写 PASS):分析仪通道实物输入耐压资料 /
≥100 M samples 实际缓冲容量 / 探头印字 CH1-CH2 与软件通道号(及 CSV 列号)对应。
另:步骤 0 期望 `mask=0x18` 要求 `MOT_GATE_NO_DRV_CONFIG` 成立(驱动配置 UNCONFIGURED),
板子若还带着上一场的电,第一条状态检查就会成为首错——该现场事实由操作者回报,我不代答。
停点:不跑 `--approval-init`,不跑 dispatch,不 Start 采集;固件/执行器/归档原件未改。

### 13.1 提交链与远端回读(设备动作 0)

| tip | 内容 | 远端逐 blob 回读 |
| --- | --- | --- |
| `db3c781` | 上一轮 R7F5 登记(本轮基线) | 该轮已报 fails=0 |
| `531f255` | R7F5A 登记: A 任务书 4 件 + 本机 56 项判据 + LA 实测件 + 交接 MD/JSON | **fails=0**: 172/172 远端==登记==磁盘; plan/rows 落位 PASS; 执行器/镜像身份 PASS; 嵌入 zip 声明与上一轮登记一致; 回执四项声称由远端 blob 复算; 新增 26 改动 2 越界 0; 24 件原件 tip 与 tip^ 逐字节相同; 两类自检 FIRED |
| (本轮跟修) | 回读原文 + 回读工具入档 + 清单重算 | 该 tip 自身的回读不再入库(避免"为把回读报告再入库"形成循环, 按任务书第 86 行) |

推送实况登记(不删不改): 第一次 `git push` 被远端以 `! [remote rejected] ... (Internal Server Error)` 拒绝,
`git ls-remote` 证实远端仍指 `db3c781`、本地 `531f255` 是其直接子提交 => 纯快进, 重试一次即成功, 未做任何历史操作。

## 14 R7F5A 现场执行（一次 8 路采集 + 原 16 步串口链；A 补充修订五条随轮落实）

批准身份 `M8R7F5A-b502-la-two-channel-static-20261104` 由未改动执行器 `--approval-init` 离线开立，
令牌取 `capture_scope.operator_authorization_verbatim` **逐字节**（82 B `f2e062a59afc460e…`，A 补充第 5 条，不拼接不询问）。
开账前先只读复核旧盲测账：`spent` 全 0、`pending` 空 => 走"新身份执行一次"这一支。
执行根 `field/field-20261104T101046Z/`。

顺序照任务书第 66–69 行：MCP `start_capture`（`captureId=8`，D0–D7 全开、1 MS/s、手动模式、
请求 `bufferSizeMegabytes=512`；接口只回 captureId，**不回实际接受值**）→ 原计划
`--dispatch --transport real --backend real --profile response_driven` 一次 → 关端口后 `stop_capture`。

**串口层 `SERIAL_16_STEP_STATIC_IRQ_TRIAL_OK`（判读 27 项 fail=0，`field_r7f5a/FIELD_JUDGEMENT.txt`）**：
`status=OK`、`stages_executed=2`、16 份响应原文齐；实耗 `port_open 1 / apply 1 / irq_polarity_prep 2 / irq_disable 1`，
其余 18 项 0，无重试。门链按字节复现 `mask 0x18→0x8→0x0→0x8`、`irq gate CLOSED→OPEN→CLOSED`、
`polarity NOT confirmed→operator CONFIRMED`、`protection_ready NO→YES→NO`（全是软件状态位）。
固件自报同场原文：`TMC_DIAG pin=3 raw=0 safe=0 irq=RISING [safe]`、`PC.6(ENN_FB) pin=ok raw=HIGH`。
命令身份不靠台账标签（阶段台账根本不写 `cmd` 字段，已具名登记），改用响应首行回显：15 条与计划同序，
**无 `motor_arm`/`motor_start`**，全程无 `armed=1`。零裸 NUL、严格 UTF-8、会话末 `t_s=7.344 s` < 90 s。
第 10 步被动观察 0 字节：只说明窗口内无产出，不构成"没触发"的证明。

**仪器层 `CAPTURE_VALID_BUT_NON_DISCRIMINATING`**：采集窗 28.0166 s 完整包住会话
（起 `10:10:37.465` < 首响应写入 `10:10:46.819`；末响应 `10:10:53.945` < 止 `10:11:05.482`），
8 路**全程零跳变**。决定性一条：本轮电平模式 `0,1,1,1,1,1,1,1` 与探头未接时的浮空基线**逐列相同**，
所以这批字节既与"接线正确且全程静默"一致，也与"探头未接触/无共地"一致——电平层无法区分这两者。
通道映射未由现场读得，按 A 补充第 2 条标 `CHANNEL_MAPPING_UNRESOLVED`，不靠"预期一低一高"反推信号身份。
原生件 `la_capture_id8.sal` 3014 B、数字 CSV `la_digital_raw.csv` 196 B（散列见 `field_summary.json`）。

自伤如实登记：第一次 `save_capture` 我传 `directory`，接口具名拒绝 `should have required property 'filepath'`
（原文 `la_save_first_attempt_error.json` 未删）；改用 `filepath` 对**同一次已采完的会话**重存成功——
是采集之后的保存动作，不是重采，不消耗采集额度。

A 补充五条落实对账（原文逐字录入 `a_review_r7f5a/SUPPLEMENT_A_R7F5A.md`，含我的录入补记）：
输入范围按商家页 0–5 V 接受且等级锁在"厂商页声称＋实测特征吻合"（转录与原件散列见
`la_local_r7f5a/vendor_spec_transcription.txt`；截图含账号级界面，不入公开仓库）；8 路全开、映射不反推；
1 MS/s＋显式 512 MB、本机内存 33.3 GB/空闲 14.4 GB、MB 未等同样点数、覆盖性事后验证；
执行前的断电→上电登记为前置供电调整、结束断电为允许的收口动作；令牌用 capture_scope 原文。

结论**不拼成全过**：`STATIC_IRQ_TRIAL_WITH_DIAG_ENN_CAPTURE_PASS` 不使用。
`polarity_measured=false`、`core_protection_verified=false` 保持，四条禁用声称仍全部禁止。
待操作者回报：收口切断 VM+MCU 的时刻与观察到的掉电表现（`PENDING_OPERATOR_REPORT`，我不代填）。

### 14.1 回读自伤与跟修（设备动作 0）

`034e2ff` 推送后的远端逐 blob 回读：**228 条中 mismatch=1** —— `run_record.md`
登记值 `221d24aa…`，远端 blob 与磁盘都是 `cd9b3408…`（`blob==disk=True`）。

根因是我自己的操作顺序，不是仓库内容坏掉：改完标签拼写错误（`SERIAL_16_STEP_STATIC_STATIC_…`
多写一个 STATIC）之后只 `git add` 了 `run_record.md`，**没有重算清单**——清单里那条还是改前的字节散列。
后果：那次提交里的清单对这一件作了过期声称；内容本身三方一致，坏的只是"登记值"这一路。

跟修：重算 `SHA256SUMS.txt` → 暂存 → 预检（登记==暂存 blob==磁盘，problems=0，两类自检 FIRED）
→ 一次提交推送 → 再跑逐 blob 回读。回读报告自身不再入库（任务书第 86 行：不为把回读报告再入库形成循环），
原文留在仓外 `D:/r7f5a_rb_field/remote_readback_r7f5a.txt`。

推送实况（不删不改）：`034e2ff` 那次连续两次被远端 `Internal Server Error` 拒，
每次都先 `ls-remote` 证远端仍是前 tip 且本地是其直接子提交（纯快进）再重试，第三次成功，负载仅 50873 B。

## 15 M8-R7D1 离线异步学习轮（产品 C 改动 + 主机验收 + 离线构建一次交付；设备动作 0）

任务书 `NS800_R7D1_Offline_Async_Learn_Task_20261104.zip`（13467 B，sha256 `b35e0a2e…2c5c`，175 行），
状态 `GO_OFFLINE_IMPLEMENTATION_ONLY`。基线 = 已审 tip `7885aea`（构建时 HEAD 即它，`HEAD_IS_BASELINE` PASS）。
本轮按任务书第 10 节把**实现、测试、归档合为一次交付**：没有先交一份"登记轮"，也没有向操作者等下一次批准。

**改了什么**（六文件，产品三个）：`diag_relative.h` 给出七态作业合同、采集器归属枚举与整份快照类型；
`diag_relative.c` 落 `d_job`（276 B 静态）+ 新叶锁 `d_ctx_lock`（"rel_ctx"）+ `d_cap_owner`，`learn` 改为
受理即返回，新增 `learn_status` / `learn_cancel JOB_ID`；`diagnosis.c` 只加一行
`rel_job_advance(&in, have_frame, d_mode)`，排在 `rel_prod_evaluate()` 之后、所有披露打印之前。
验证链三个文件（`rel_main.c`、`run_diag_relative_m8.sh`、`shim_rel/rtthread.h` 补 `RT_TICK_PER_SECOND 1000`）。
产品 diff +656/−117，逐字节见 `reports/reviews/phase8-m8-r7d1-async-learn-offline/code/`。

**测到什么**：修前基线 `cases=50 pass=50`；实现后 `SUMMARY|cases=62 pass=62 fail=0 pre_bad=0 executed=62`、
`build_rc=0 warnings=0`（`-Wall -Wextra`）、判定行 `M8 RELATIVE RESULT: PASS(主机链, 离线; 现场项未验证)`。
原 50 例一条未删：机器比对 `(id → checks)` 显示 47 例逐例相同，受异步合同影响的 3 例只增
（REL25 41→44、REL39 11→15、REL42 31→35）。负控 **14/14 META-OK**（RV1–RV10 全族仍命中，
本轮新增 RDV1 取消后仍发布→A05/A06、RDV2 无帧不推进超时→A08、RDV3 start 又回到等样本→A01、
RDV4 拒绝前先烧凭据→A10）。

**本轮踩到并如实登记的两处自伤**（失败原文保留在 `host/reverse_run_full.txt` 与工具输出，未删不改）：
① RV6 的反向锚点原本钉在同步 learn 的那一行清凭据代码上，改成异步后该行不存在，第一次跑报
`INFRA-FAIL 注入未落地/锚点不唯一 (ANCHOR-COUNT=0)`；这不是"负控没用了"，是锚点漂移，按等价缺陷
重钉到 `d_pending_learn = RT_FALSE;` 受理行并把期望身份扩为 REL39 + A10。
② 夹具的 `world_reset()` 起初没清 `d_job/d_cap_owner`，于是前一例遗留的 RUNNING 作业让后续每一条 `zero`
都打 `capture busy`——那是夹具污染而非产品行为，按每例复位修正。

**打印限长**（任务书 §6）双路：离线工具按实参最宽合法展开逐调用折算（29 个调用，最宽 120 B 含源 LF；
`PRINT_BOUND_NOT_VACUOUS` 保证折算失效时会报警，不是空判），主机链 A12 用同一 120 B 预算实测，
含裸 NUL 与严格 UTF-8。离线判据 `TOTAL=31 FAIL=0`。

**构建与资源**：`UV4 -r … -t rt-thread-motor-m1-candidate` 一次，rc=0、`0 Error(s), 0 Warning(s)`、V6.24、
`Code=209178 RO=87242 RW=2196 ZI=85936`；构建前后处理脚本只读核对过（AfterMake 仅 `fromelf --bin`，
无下载/调试/串口）。镜像增量 4296 B 与 Code+RO+RW 增量逐字节相符；新增静态 RAM 341 B 逐项取自 map
符号表（`d_job` 276 + `d_ctx_lock` 60 + `d_cap_owner` 1 + `d_job_next_id` 4）。`diag_thread_entry` 帧
632→640、子树 1580→1588，最深路径仍是既有的 `rel_prod_evaluate→…→rt_kprintf` 那条，不是新推手顶上去的；
命令侧 `learn` 836→1084（最深线由 `rt_thread_mdelay` 换成 ctx 取锁，自身帧反而 296→168）。
线程栈 4096 一字未改，**没有**靠增大栈或延长超时规避问题。全部数是 armlink 静态下界，不含异常/ISR 帧，
且链接器解析不了 msh 的函数指针表（`finsh_thread_entry` 两版同 628 恰证明命令处理函数不在其内），
所以本轮不给"shell 栈总共用多少"这种手加出来的总数。

**边界**：设备动作 0；新镜像 `NOT_ON_BOARD`（BIN sha `df7584c3…`），板上仍是历史 b502 证明（`502dca5d…`）；
`polarity_measured=false`、`core_protection_verified=false` 保持，四条禁用声称继续禁止；旧 field 计划/rows/
b502 证据/历史回包按原件保留，本轮未为"假装兼容"去改它们。第 14 节末的 R7F5A 物理断电仍是
`PENDING_OPERATOR_REPORT`，本轮没有靠接设备去关闭它。

## 16 M8-R7D1 返修轮（A 复审 CHANGES_REQUIRED 四项 P2；设备动作 0）

被审件 `NS800_R7D1_A_Review_Changes_Required_4d50310_20261104.zip`（19634 B，sha256 `d9090f96…9b8d`，
内层清单 18 条逐条复算 18/18 且不含自身），`FIX_SCOPE.id=R7D1_A_REVIEW_4d50310_FIX`，
`verdict=CHANGES_REQUIRED`，四条 P2（ARD1–ARD4），`hardware_actions_cap=0`，
`new_approval_required_for_offline_fix=false`。基线 = 被审提交 `4d50310`（构建与判据时 HEAD 即它，
`HEAD_IS_REVIEWED_COMMIT` PASS）。按任务书第 5 行把实现、验收、归档合为一次交付，未开登记轮、未向操作者等批准。
A 的 19 件包内原件（含 `proof/` 的四条 FAIL 原文与 `reproducer/`）按原路径入
`reports/reviews/phase8-m8-r7d1-ard-rework/reviewer_originals/`，未改写。

**四项各自的前后结果**（前 = 本机在 `4d50310` 的仓外逐字节副本上跑 A 的原版探针；该副本 5 件输入的
sha256 与 A 的 `IDENTITY.json` 逐件相同；后 = 本轮定稿工作树，同一台机器同一 gcc 4.9.2）：

| 项 | 修前 | 修后 | 改法一句话 |
|---|---|---|---|
| ARD1 | `rc=-5 ctx_held=1 takes=1 releases=0` | `rc=-5 ctx_held=0 takes=0 releases=0` | NULL 检查前移到取锁之前，拒绝路径不碰锁（因此没有可泄漏的东西，也不需要"测试重置锁"） |
| ARD2 | `owner=1 active=1`，故障消失后再 zero 仍 `busy=1` | `owner=0 active=0 zero_cred=1 busy=0` | 有界延迟清理：`d_zero_cleanup` 旗可查 + `rel_job_advance` 内只服务 BY_ZERO 的收口分支；旗未灭时第二个采集者仍被拒 |
| ARD3 | `refused=1 zero_cred=0 learn_cred=0 pending_changed=1` | `refused=1 zero_cred=1 learn_cred=1 pending_changed=0` | 静止事务门禁前置于任何 clear 副作用；作废失败先放锁再打印 |
| ARD4 | `refused=1 cfg_changed=1`（magic `00000000`→`4E535250`，RAM 非 Flash） | `refused=1 cfg_changed=0` | commit 先发布候选再写配置；配置写失败回滚候选并具名披露 rolled-back / no-rollback |

**锁序与清理归属**（A 第 76 行要求点名）：`rel_ctx` 仍是叶锁，临界区内不调 motor/cfg/Flash/UART/mdelay/print；
嵌套方向只有 mot/cfg→ctx。ARD2 的归属归还由 Diagnosis Thread 的 `rel_job_advance()` 单点完成（命令线程只立旗），
`rel_zero_capture_close()` 改为只回报状态不打印 —— 因为 zero 的"发布失败"出口是在静止事务内调它的，
而 `motor_calibration_static_begin()`（motor.c:2097）成功时持续持有电机锁到 `..._end()`（motor.c:2140），
把披露打印留在清理函数里等于在持锁期间走 UART。ARD4 的两步（发布候选 / 写配置）不能并成一个临界区：
在 `rel_ctx` 里调 cfg 会把嵌套方向翻成 ctx→cfg，本轮因此只换顺序、不调配置事务接口。

**验证**：正常链 `build_rc=0 warnings=0`、`cases=66 pass=66 fail=0 executed=66`（上一轮 62 例一条未删，
新增 A13–A16）；反向链 19 族全 META-OK（原 14 族一条未删，新增 RDV5–RDV9，每族都有 `INJECTED` 行
即副本编译成功且真执行，期望失败身份恰是新增那四条用例）；A 的原版探针脚本未改，修前 4/4 FAIL、修后 4/4 PASS；
离线判据 `TOTAL=64 FAIL=0`；本轮新增/改动的 3 个 rt_kprintf 格式最宽一次 74 B（含源 LF，预算 120 B），
`learn_status` 主行未被改宽，无裸 NUL、严格 UTF-8。

**构建**（一次，`UV4 -r` 单目标）：rc=0，`0 Error(s), 0 Warning(s)`，V6.24，
`Code=209474 RO=87422 RW=2196 ZI=85944`；新 BIN 297476 B `165d8bdc…`，AXF 1789320 B `dc145259…`，
MAP `8229cf75…`，调用图 `16b87c97…`，全部归档到**新目录** `reports/firmware/phase8-m8-r7d1-ard-rework/`。
被审那一版镜像在磁盘上仍是 `df7584c3…`（判据 `REVIEWED_IMAGE_UNCHANGED`），A 的失败原文与旧证据一字未动。
资源对账换了算法并如实登记：`Code+RO+RW` 增量 +476 而 BIN 增量 +472，差额由装载段构成解释到残差 0
（ER_IROM1 +476、RW_VT_DTCM 压缩载荷 0、对齐空隙 +4、RW_DTCM **压缩后**载荷 −8；两版各自
"三段+空隙"精确等于该版 BIN 字节数）；上一轮"Code+RO+RW 增量 == BIN 增量"这条等式本轮不成立，没有沿用旧措辞。
逐对象差分只有 `diag_relative.o`（Code +310 RO +178 RW 0 ZI +1），其余 110 行 .o 与 49 行库成员逐字段相同；
新增静态 `d_zero_cleanup` = 1 B 取自 map 符号表。栈：diag 线程帧与子树 +0（最深线逐字未变，余量 2508 B）；
命令线程 `rel_cmd_commit` 因 ARD4 的回滚副本（`diag_rprof_t`，map 实测 116 B）帧 +112、子树 1444→1556，
**成为新的可见最深线**（余量 2540 B）—— 本轮新增成本如实登记，未靠加大栈或延长超时换绿；
armlink 的数是静态下界且解析不了 msh 的函数指针，故不给 shell 栈总数。

**自伤与工具侧问题（照实）**：
1. ARD2 第一版把披露打印放在清理函数里（= 上述锁内 UART 破口），读 motor.c 持锁区间后自查发现，同轮换法修掉，
   修在构建之前，最终构建字节里没有那一处。
2. 修前基线第一次跑成 4/4 **PASS**：先把 5 个 blob 写进仓外副本、再 `cp -r` 整个 applications，
   拷贝把三件产品文件覆盖成修后版本。当场按"与 A 登记矛盾"识别，重新 `git show 4d50310:path` 写回并逐件
   sha256 对账后才重跑，得到与 A 一字不差的 4/4 FAIL；错误一次的输出目录被正确那次覆盖，未留可误用中间件。
3. 资源报告工具初版把 armlink 的小计行（`ELF Image Totals (compressed)` 等）当对象行比较，误报"非零差分 2 行"；
   排除小计行后判据才真正只允许 `diag_relative.o`。同一工具沿用了上一轮"39 个库成员行"的说法，
   实测 component-sizes 表 111 行全是 .o、库成员行在 map 更靠前的段落 —— 本轮改成按整份 map 比库成员
   （成员名+段+尺寸+类型，49 行相同）。上一轮那句措辞口径不准，在此登记更正，旧报告原文不改。
4. 后台反向链任务被提前报了一次 completed（日志只到 RDV2），未拿截断日志当结果，等真跑完重读全文才登记 19/19。

**边界**：板上仍只承认历史 `b502 / 502dca5d…`；`df7584c3…` 与 `165d8bdc…` 均 NOT_ON_BOARD；
`polarity_measured=false`、`core_protection_verified=false` 保持，四条禁用声称继续禁止；
R7F5A 物理断电回报仍 `PENDING_OPERATOR_REPORT`（未代填、未为关闭它接设备）；
演示端 V0.3、旧包、旧现场原件与旧批准账未动；本轮不构成任何硬件授权。

## 17 M8-R7D1 ARD4b 续修轮（A 复审把 ARD4 判回 CHANGES_REQUIRED；设备动作 0）

被审件 `NS800_R7D1_ARD4_Followup_e1d7f3d_20261105.zip`（21347 B，sha256 `411ccf2d…d02c`，
内层清单 19 条逐条复算 19/19、zip 内 20 个成员、清单不含自身），`FIX_SCOPE.id=R7D1_ARD4_FOLLOWUP_e1d7f3d`，
唯一 finding 是 **ARD4 / P2 / CHANGES_REQUIRED**，位置 `diag_relative.c:2195-2209`；
`closed_without_reopening=[ARD1,ARD2,ARD3]`，`hardware_actions_cap=0`，
授权原文「Continue original offline authorization. No new registration or approval round.」。
基线 = 被审提交 `e1d7f3d`（构建与判据时 HEAD 即它，`HEAD_IS_REVIEWED_COMMIT` PASS）。
A 登记的 7 件源码绑定在 `e1d7f3d` 上逐件复算 blob sha1/字节/sha256 全对；磁盘与被审提交不同的
恰是 5 件（产品 3：`diag_relative.c` `project_config.c` `project_config.h`；主机 2：`rel_main.c`
`run_diag_relative_m8.sh`），`diag_relative.h` 与 `diagnosis.c` 逐字节仍相同 —— ARD1–ARD3 没被重开。
A 的 19 件包内原件按原路径入 `reports/reviews/phase8-m8-r7d1-ard4b-commit-txn/reviewer_originals/`，
逐件与 zip 内字节相同（判据 `REVIEWER_ORIGINALS_VERBATIM`），未改写。

**缺陷与改法**：被审版是"发布候选 → 写配置 → 失败再回滚候选"，回滚自己也要一次可能失败的 ctx 取锁，
于是"配置写失败 + 回滚取锁失败"这一组合留下 `候选=新版 / 配置=旧版 / 两份凭据都还在` 的半份发布
（A 的探针原文 `no_rollback=1 pending_changed=1 creds_kept=1 cfg_id=1 pending_id=2 ctx_takes=5`，
`run_rc=1`；我在 `e1d7f3d` 的仓外副本上用同一份 reproducer 逐字复现，OBS 行与包内原文一字不差）。
本轮按 A 的路线把**提交点**换成"两把锁都到手之后只做不会失败的赋值"：新增
`project_config_rprof_begin()/publish_locked()/end()` 三条窄路径（`begin` 里完成全套校验与 cfg 取锁，
是本轮最后一个可失败步骤），commit 依次 `begin → rel_ctx_take → 两份赋值 → 放锁`；
ctx 拿不到就归还自己刚拿的 cfg 锁并按 `-RT_ETIMEOUT` 具名拒绝。**回滚路径整体消失**，
不再需要"靠另一次可能失败的取锁来撤销"。结果合同只剩两种：`REFUSED` 时配置/候选/凭据逐字节不变
且不持锁；`OK` 时配置与候选 memcmp 相等（A16 ① 直接逐字节比，不从打印推断）。
锁序 `mot(跨命令持有) → cfg → ctx` 不变，`rel_ctx` 仍是叶锁（`rel_pending_publish_locked` 内只有赋值），
没有无界重试、没有强行解锁、没有吞错误；持 cfg 期间只多了一次有界等待（`REL_CTX_TAKE_MS=20ms`）。
既有 `project_config_set_rprof()` 保留未动（别的用例与路径仍在用）。

**验证**：正常链 `build_rc=0 warnings=0`、`cases=66 pass=66 fail=0 executed=66`（上一轮 66 例一条未丢、
本轮不新增编号，checks 只增：A16 35→37，其余逐例相同）；反向链 **20 族**全 META-OK（原 19 族一条未删，
新增 `RDV10_commit_swallow_ctx_take_failure`；`RDV9` 沿用原名换注入体；每族都有 `INJECTED` 行即副本
确实编译并真执行，期望命中 A16）；A 的原版四探针未改脚本，`e1d7f3d` 与本轮都是 4/4 PASS，
ARD1/ARD2/ARD3 那三条的 6 个观察量逐项相同（必要回归未越界）；第 4 条在同一注入下拒绝身份由
`ctx-lock-publish/-12` 变为 `ctx-lock-commit/-117` 且仍 `refused=1 cfg_changed=0`。
**如实登记**：A 的复合探针把 cfg 与 ctx 两个故障同时装上，修后 commit 只取 ctx 3 次且 cfg 先失败，
**ctx 那一臂已经打不到**（`ctx_takes 5→3`）—— 它的 PASS 只证明"cfg 失败时三份状态未动"，
"配置已写成而 ctx 发布失败"这一组合在新设计里不可能发生；等价故障点由 A16 ②/⑤ 与四探针第 4 条承担。
离线判据 `TOTAL=113 FAIL=0`。

**打印预算换了口径并扩了覆盖面**：本轮新增/改动的 rt_kprintf 格式串 0 个（报文形状未动），
但被审版三条与回滚绑在一起的 why 字面量消失、新增两条。上一轮的宽度表对不认识的实参一律按 26 B
兜底，所以只能覆盖"本轮触及的调用点"；本轮把宽度改成当场从 `diag_relative.h` + `project_config.h`
解出的 144 个成员类型折算（同名字段取最大值，只会高估），覆盖面因此扩到整份 `diag_relative.c`：
`commit` 路径最宽一次 80 B、全文件最宽 109 B，均含源 LF 且 ≤120 B；没有任何实参落到兜底
（`PRINT_ARGS_ALL_RESOLVED`），why 的 31 处赋值右值闭合成 4 类、集合最长 25 B（上一轮上界）；
无裸 NUL、逐条严格 UTF-8；`learn_status` 主行未被改宽。

**构建**（一次，`UV4 -r` 单目标 rt-thread-motor-m1-candidate，未加 `-f`）：rc=0，
`0 Error(s), 0 Warning(s)`，V6.24，`Code=209490 RO=87378 RW=2196 ZI=85944`；
新 BIN 297452 B `23691e8d…`，AXF 1790048 B `0f421595…`，MAP `e2d8d081…`，调用图 `a7090b6e…`，
全部归档到**新目录** `reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/`。
被审那一版镜像仍是 `165d8bdc…`、上一轮那版仍是 `df7584c3…`（`REVIEWED_IMAGE_UNCHANGED` /
`OLDER_IMAGE_UNCHANGED`），A 的失败原文与两版旧证据一字未动（`OLD_EVIDENCE_AND_IMAGES_UNTOUCHED`）。
资源侧：`Code+RO+RW` 增量 −28 而 BIN 增量 −24，差 +4 由装载段构成解释（ER_IROM1 −28、
RW_VT_DTCM 压缩载荷 0、对齐空隙 −4、RW_DTCM 压缩载荷 +8），两版各自"三段+空隙"精确等于该版
BIN 字节数（297476 / 297452），残差 0；ER_IROM1 两份 map 相减 −28 与 Code+RO+RW 同向相等（独立复算）。
逐对象非零差分只有 `diag_relative.o`（Code+8 RO−42）与 `project_config.o`（Code+2），其余 109 行 .o
与 49 行库成员逐字段相同；对账 −32 + (incl. Padding) 第 2 行 +4 = −28。AXF 的 Flash 装载段与 BIN
逐字节相等（两版都用仓外 ELF32 程序头工具先在被审件上复现"相等"再跑本轮件）。
栈：`rel_cmd_commit` 帧 608→496、子树 1556→1404 —— 上一轮为回滚新增的 `diag_rprof_t` 局部副本
（map 同类型 116 B）与 `project_config_set_rprof → cfg_take_checked` 那条子树（948 B）一起消失，
命令线程可见最深根回到 `rel_cmd_clear`（1460，余量 2636 B），**没有任何子树变高**；
`DIAG_THREAD_STACK=4096`、`FINSH_THREAD_STACK_SIZE=4096`、`REL_CMD_DEADLINE_MS=12000`
三份值与基线 blob 相同（没有靠加大栈或延长超时绕过）；Diag 线程帧与子树 +0、最深线逐字未变。

**自伤与工具侧问题（照实）**：
1. A16 ④（锚点特异性）第一次跑成 FAIL：做"挪后一步"这次运行前没把 `g_ctx_take_calls` 清零，
   夹具的失败条件是"第 N 次之后"，于是第一条 take 就超阈值，得到一条无关的
   `commit REFUSED: ctx lock`。这条恰好证明该段不是恒绿；加清零并在注释里写明理由后 66/66。
2. 派生自上一轮的资源报告初版仍按"非零差分只有 `diag_relative.o`"判定，本轮 `project_config.o`
   也改了 → 判据与对账两处 FAIL；改成两对象集合 + 通用对账（含链接器 padding 行）后重跑，
   第一版错误输出被正确一版覆盖，未入库。
3. 派生的 manifest 工具初版带着上一轮的过期叙述（`+476/+472` 的对账话、`build_r7d1r.log`、
   `resource_stack_r7d1r.txt`、以 `209178/87242/85936` 为基线的增量）；且把 `REL_CMD_DEADLINE_MS`
   当成在 `diag_relative.h`、把 `rtconfig.h` 当成在 `applications/`（实为 BSP 根，运行即
   FileNotFoundError）。现全部改成当场派生：增量取两份日志相减、被审 Program Size 取上一轮
   manifest、栈/时间常量磁盘值与被审基线 blob 各取一次比对、调用图数字与三处判据行从资源报告
   逐字摘（锚点找不到就抛，不允许手抄）。
4. 打印折算的兜底宽度会把既有 `show` 行误报成超预算（实测 4 条），根因是不认识实参按 26 B 猜；
   换成按头文件类型折之后同一批行实际 ≤109 B。这是判据口径的修正，不是产品变宽，也未因此改任何打印。
5. `META-OK` 子串计数 21 与判定行计数 20 不一致（正文里也出现这个词）；判据改按行取，
   并另加一条"两种计数确实不相等"的判据防止以后退回子串数。

**边界**：本轮 0 次下载/回读/复位/开串口/枚举 DAP/逻辑分析仪/arm/start/运动/真实学习/写配置/供断电；
板上仍只承认历史 `b502 / 502dca5d…`，`23691e8d…` 与 `165d8bdc…`、`df7584c3…` 均 NOT_ON_BOARD；
`polarity_measured=false`、`core_protection_verified=false` 保持，四条禁用声称继续禁止；
R7F5A 物理断电回报仍 `PENDING_OPERATOR_REPORT`（未代填、未为关闭它接设备）；
演示端 V0.3、旧包、旧现场原件与旧批准账未动；本轮不构成任何硬件授权。

## 18 M8-R7D1 ARD4b 签收轮（A 判定 ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA；两处文字勘误；设备动作 0、未重构建）

签收件 `NS800_R7D1_ARD4B_Acceptance_00718b4_20261105.zip`（24196 B，sha256 `49681a0f…fa97f`；
内层清单 19 条逐条复算 19/19、zip 共 20 个成员、清单不含自身；20 件 A 原件按原路径入
`reports/reviews/phase8-m8-r7d1-ard4b-acceptance/reviewer_originals/` 并逐字节相同）。
`verdict=ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA`，`blocking_product_findings=0`，
`closed_findings=[ARD1,ARD2,ARD3,ARD4]`，受审 tip `00718b4`（= 本地 HEAD 与已推送远端 tip），
A 侧 `review_product_edits=0`、`hardware_actions=0`、`arm_compiler_rerun_by_A=false`、
`all_271_remote_entries_replayed_by_A=false`（A 明确没重放我方 271 条远端回读，只核了自己看的清单）。
A 独立重跑：原版四探针 4/4 PASS、复合探针 PASS（`refused=1 no_rollback=0 pending_changed=0
creds_kept=1 ctx_takes=3 cfg_steps=3`，与我方归档 OBS 行一字不差）、全链 66/66、反向 20/20
META-OK、BIN/AXF/MAP/调用图 blob 身份与 manifest 一致、AXF Flash 段逐字节等于 BIN。

**A 也独立认定**复合探针的 ctx 臂本轮未命中（`ctx_fault_arm_reached=false`，等价覆盖=四探针第 4 条
+ A16 两世界），与 §17 的如实登记一致。签收范围只覆盖离线代码与文档，不含真实硬件效果；
`23691e8d…`/`165d8bdc…`/`df7584c3…` 全部保持 NOT_ON_BOARD，板端仍只承认历史 `b502 / 502dca5d…`；
`polarity_measured=false`、`core_protection_verified=false`、R7F5A 断电 `PENDING_OPERATOR_REPORT`
不变；本轮不构成任何硬件授权，回现场后的新版上板范围另行定稿、不结转旧额度。

**两处文档勘误（本轮只改文字，产品与主机 5 件字节一字未动，未重新构建）**，逐字原文与源码证据见
`reports/reviews/phase8-m8-r7d1-ard4b-acceptance/errata.md`：

1. 校验与取锁顺序。§17 与 `implementation_contract.md` §2/§2.2 原话（作废，此处保留以便对账）：
   「`begin()` 的三步里**只有取锁会失败**; 校验(`rel_profile_validate` + 用临时副本过
   `config_in_range`)全部在取锁之前完成」与注释「本轮最后一个可失败步骤」。实际
   `project_config.c:503-510`：判空与 `rel_profile_validate` 在锁外，`cfg_take_checked()` 之后才
   在**锁内** `tmp = cfg; tmp.rprof = *p; if (!config_in_range(&tmp)) { cfg_release(); return -RT_EINVAL; }`；
   且 begin 成功后 `rel_ctx_take()` 仍会失败。正确总口径：**全部校验与所有可能失败的取锁都在两份
   共享状态写入之前完成**；范围复核留在 cfg 锁内是当前实现，不搬到无锁读 `cfg`（那会变成数据竞争）。
2. commit 成功后的凭据。原话（作废）：结果表 `OK` 行「learn 作废、zero 保持」。实际提交点调
   `rel_pending_publish_locked(&cand, -1, -1)`（`diag_relative.c:2215`；函数 `:1468` 对 `-1` 不写旗标）
   ⇒ **zero/learn 两枚凭据都保持原值**；作废 learn 凭据发生在 learn 受理成立那一刻（`:1647`，上一轮
   ARD4 的既有行为）。本轮不引入"成功后额外作废凭据"的新需求。

已改写：`implementation_contract.md` §2 代码块/§2.2 合同段 + 新增 §8 勘误节（逐字保留旧句作对照）；
`phase8-m8-r7d1-ard4b-commit-txn.md` §2 与新增 §8；同名 JSON 的 `fix_oneliner`/`commit_point`/
`fallible_steps_before_commit_point`/`result_contract.OK` + 新增 `acceptance` 块。**§17 本体不改写**
（其中两处旧口径以本节为勘误依据，原文保留）。

**同源但本轮不修的一处**：`project_config.h:82-83` 的接口注释同样把 `config_in_range` 写在
"然后取 cfg 锁"之前。它是产品文件里的文字，改它需要重新编译，而 A 明确「不为此再开产品返修或
构建轮」；因此点名登记、留到下一次合法触产品源码的轮次随该轮构建一起修。签收候选 BIN 的磁盘字节
仍是 `23691e8d8aa8c1d3…`，`project_config.h` 磁盘字节与签收 tip 的 blob 相同（判据
`PRODUCT_HEADER_BYTES_UNCHANGED_SINCE_TIP`）。

**本轮独立复算**：`tools/r7d2a_acceptance_checks.py` → `proof/acceptance_checks.txt`
（判据含：包身份、A 原件逐字节、探针输入与上一份 A 包同字节、A 的 7 件绑定在 tip 上复算且 tip blob
== 磁盘、A 声称的 66/66 与 20/20 从我方归档原文复算、A 包内 proof 三件自洽、三版旧镜像 sha 未变、
AXF Flash 段 == BIN 本地复算、产品与主机路径 0 改动、旧句在三份文档正文中已消失、run_record §18
且 §16/§17 未删、泄漏扫描先自证会命中）。

**自伤与工具侧问题（照实）**：
1. 我自己的老毛病又犯了一次：四探针判定最初用 `count('|FAIL')`，把 A 输出里的
   `REVIEW|FAILED_ZERO_RECOVERS_COLLECTOR_AFTER_LOCK_FAULT|PASS` 当成 FAIL 命中（子串吃掉计数）。
   改成按整行 `^REVIEW\|\w+\|(PASS|FAIL)$` 取判定行后为 4 条全 PASS。
2. 复合探针的 reproducer 比对目标写错一轮：它来自 ARD4 续修包（`ard4b-commit-txn`），我却先拿去和
   ard-rework 那包里"根本没有的文件"比，结果 False；改为按件分源（四探针比 ard-rework、复合比
   ard4b-commit-txn）后 4/4 同字节。
3. A 的 `image_identity_review.json` 结构不是我猜的 `BIN_sha256/flash_segments`，而是
   `images.bin.sha256` 与 `segments[].equal_bin`（一次取键失败暴露我把上一包的结构当作了通用形）。
4. 判据"旧句已消失"最初与"勘误节按设计逐字保留旧句"自相矛盾（我自己造的假红）；改为只在正文部分
   搜索（`(?m)^## 8[ .]` 之前），既保住可复算的对照，又不让对照本身把判据钉死。

## 19 M8-R7F6 现场轮（A 定稿的 b502 被动采集 + 五条只读查询；操作者已批准并执行）

任务书：`reports/reviews/phase8-m8-r7f6-la-path-static/TASK_R7F6_PASSIVE_READONLY.md`
（原件 zip `NS800_R7F6_B502_Passive_Readonly_Task_20261105.zip`，303880 B，
sha256 `6179c093b13566db25af620e4bd05093103e834ff655ebc4c27422f7e2f58dc6`）。
批准身份 `M8R7F6-b502-la-path-static-20261105`；批准令牌 = 操作者要求执行本包的真实原话
`按此包执行`（15 B，sha256 `ffeff4d3ad68…dd106641`，原文留仓外 `C:/m8r_approvals/token_r7f6.txt`）。
`operator_context.txt` 只是编写请求，未当批准使用；旧账/旧回执一字未改，新账 spent 从 0 起。

**设备动作计数（本轮实际发生）**：COM5 开口 1、dispatch 1、逻辑分析仪 Start 1 / Stop 1、
原生保存 1、数字 CSV 导出 1。下载 0、回读 0、DAP 访问 0、复位 0、额外上电 0、apply 0、
极性确认 0、IRQ 开/关 0、arm/start/运动 0、zero/learn/commit/配置保存/清日志 0、现场重试 0、
拆探头/参考电平/注入/DMM 0。主机侧另有一次"启动 Logic 2 程序"（见自伤第 2 条）。

**现场顺序与实测量（本机 +08:00）**：
02:20:42.526 Start → `captureId=1`（42.667 回执）；采集实际起点 42.657835（原生 meta）；
02:20:43.545 dispatch → 端口开 43.591 → 五条只读查询 → 端口关 44.013（会话 t_s 0.046…0.468 s，
硬截止 45 s 未触及）；44.373 dispatch rc=0；02:20:44.498 Stop 同一 captureId；
**Start→Stop 总窗 1.98 s ≤ 60 s**；02:21:19 原生保存、02:21:25 CSV 导出（各一次成功，无失败原文需留）。

**串口层**：5 份响应原字节入库（983/1128/612/522/983 B），裸 NUL=0、严格 UTF-8 通过、危险文本门通过，
每份首行是命令回显且与计划同序；未改执行器的 `split_rows + assert_step`（含 `expect_profile=AT_REST`）
对归档字节重算 5/5 verdict=OK；任务书 §4.B 的 12 项关键预期逐条命中（bootstrap-complete=YES、
READY/fault=0、irq_gate=CLOSED、polarity=not-confirmed、protect_ready=NO、drive_cfg=UNCONFIGURED、
arm mask=0x18 REFUSED、state/cur/tgt/armed=0、request=LOW pad=0、expected_ENN=HIGH ENN_fb=1、
TMC_DIAG pin=3 raw=0 safe=0 RISING）；两次 `system_status` 原始字节散列相同（`73abf301…`）。
账本：`spent={port_open:1}` 其余 21 项 0、`pending=[]`、`commit_failures` 键缺席（本轮无清理义务）、
executor/image/plan 三件散列与 `IDENTITY.json` 全等、22 项额度与 plan.quota 全等。

**仪器层**：设备 `isSimulation=false`；8 路数字通道全开、`digitalSampleRate=1000000`、
`manualCaptureMode`（原生 meta 记 `captureMode=FreeRun`、`digitalTriggerTime=-1`）；
请求 `bufferSizeMegabytes=512`，接口只回 captureId ⇒ 登记"请求 512 MB / 实际容量未回执"，
不写"已证明容纳 512 MB"，MB 不折算样点数。CSV 真实表头 `Time [s],Channel 0…Channel 7`，
2 行数据，8 路全程零跳变，电平 `01111111`（Ch0 低，Ch1-7 高）。
覆盖：采集 42.657…44.378 完整包住会话 43.545…44.373 ⇒ 不判 `COVERAGE_INCOMPLETE`，未重采。
通道身份：GUI 行标签与导出列名都只是 `Channel 0…7`，无信号名 ⇒ `CHANNEL_MAPPING_UNRESOLVED`
（操作者"CH0=DIAG/CH1=ENN"保持申报级，未按界面核实，也不靠一低一高反推）。

**接线解释**：本轮电平图样与上一场**已接线**的 R7F5A 完全相同，也与更早**探头未接**的浮空基线完全相同，
所以电平层无法区分"接线正确且静止"与"未接触/无共地"；本轮零运动零注入零参考电平切换 ⇒ 无事件对照，
不宣布目标接触通路已被电气证明，也不判操作者接错。共地/接触未观察 ⇒ UNKNOWN。
输入范围依据仍只沿用上一轮归档的商家声明 0–5 V，用于现有 3.3 V 接法，不升级为实测耐压。

**边界保持**：`polarity_measured=false`、`core_protection_verified=false`；真实 DIAG 故障边沿、
停 STEP、保护触发与恢复全部未验证；板上仍是历史 b502 `502dca5d…`（本轮 0 下载 0 回读，
新镜像 `23691e8d…`/`165d8bdc…`/`df7584c3…` 继续 NOT_ON_BOARD）；R7F5A 的物理断电仍是
`PENDING_OPERATOR_REPORT`，本轮不靠接设备关闭；产品源码、主机链、旧执行器、旧 plan/rows、
旧镜像与旧证据件全部未改（`PRODUCT_CLEAN` 判据 0 件改动）。

**判读与工具**：`tools/r7f6_preflight.py`（包落位 30/30 逐字节、清单 29 条、身份五件、
冻结源 10 件三方全等、tip 与冻结件差 6 件、VALIDATE_PLAN 唯一原始 FAIL 保留、10 项 scope guard、
台账根与 HEAD 绑定）与 `tools/r7f6_field_judgement.py`（49 条判据 FAIL=0，含 6 项 must-FIRE 自检）
都从**已入库字节**独立复算；`tools/r7f6_field_archive.py` 负责脱敏入库（deviceId/序列号/令牌）。

**自伤与工具侧问题（照实）**：
1. 落位核对第一次报 `与 zip 逐字节不符=1 ['SHA256SUMS.txt']`。实况不是字节不符，而是包内清单这一件
   **根本没被复制进仓库目录**（30 件只落了 29 件）；补齐后 30/30 逐字节相等。教训：报"不符"之前
   要先分开"缺失"与"内容不同"两种情况。
2. 分析仪控制通道一开始不可用：Logic 2 未运行，`10530/10430` 都无监听。按纪律这只证明**通道未就绪**，
   不证明仪器不存在（USB 侧 `Saleae Logic USB Logic Analyzer` 枚举 Status=OK）。我启动了本机已安装的
   Logic 2（主机软件动作，不占设备额度；启动后界面仍是 `Session 0` 空时间轴，未产生采集会话），
   5 s 后 `10530` 就绪。这一步任务书没写，如实登记。
3. 判读工具第一次跑出 **13 条 FAIL**，全部是我自己的工具缺陷而非现场问题：
   `split_rows(command=…)` 传了 bytes 而非 str；锚定正则没先剥 CRLF；会话台账的键名是 `kind` 而我读 `ev`。
   修正后同输入重算 49/49 PASS。
4. 顺带修掉两个**会假绿**的判据：`RX_BYTES_ARE_WIRE_TIME_FEASIBLE` 在事件列表为空时空真通过，
   已加 `len(wire)==5` 前置；`dropped_response_count` 自检原本写成恒真表达式，改成用同一个 `five()`
   判定函数分别跑"少一份"与"齐五份"。
5. 前置工具第一次把 `VALIDATE_PLAN` 的 FAIL 行取成第 2 个字段（打印 `['all']`），改为第 1 个字段，
   现在如实显示唯一原始 FAIL 是 `PLAN_MOTION_CAP_BUDGET`（本零运动范围裁为 N/A，原文保留，未改执行器）。
6. 归档第一次漏了 `field_run_summary.json`（时间线），补进清单后重跑归档，判据文件随之重算一次。
7. **隐私新发现（历史暴露）**：上一轮已公开的 `field_r7f5a/la_capture_id8.sal` 的 `meta.json` 里带着
   同一台仪器的 20 位十进制序列号 —— 本轮才查出。冻结件不回改、不删除；本轮起原生件一律另存
   "只替换该字段"的脱敏副本并登记原字节/原散列（`instrument/SAL_MASKING_NOTE.txt`），未脱敏原件留仓外。
   `instrument_manifest.json.privacy.known_historical_leak` 已写明，等 A/操作者定夺是否另行处置。
8. 离线预跑（stub+sim）刻意**不带 `--approval-id`**：`open_approval` 对 stub 也查同一本账，
   带着新身份预跑会把新账的额度扣掉；不带身份时执行器返回 `APPROVAL_LEDGER|NONE`，新账保持 spent=0。
9. 脱敏第一版只做"键级"替换（`deviceId` 这个键），漏掉了**字符串里**的同一标识：接口回执
   `content[].text` 内嵌的 JSON 原文与时间线 `start.args.deviceId` 仍是明文。暂存预检的泄漏扫描
   抓到 4 处命中（含扫描器自身把明文写成了判据字面量的自命中），随后：归档工具改成键级+字符串级
   双替换并加"入库件残留明文=0"硬门；扫描器改成**不硬编码明文**，改从仓外原件读出后构造判据，
   并自检"扫描器自身不得含任何被扫值"。重跑后 `LEAK_SCAN 命中=0`、`SCRUB_RESIDUE=0`。
10. 清单口径差别：本轮目录里 A 包自带的 `SHA256SUMS.txt` 是**原件**，必须被登记，所以本轮清单取名
    `ROUND_SHA256SUMS.txt` 并按"只排除清单自身"计数（76 条）；r7f2b 目录沿用既有"按文件名排除
    SHA256SUMS.txt"的规则（228 条，条目集合与 HEAD 相比 0 增 0 减，只有 run_record.md 的登记值变化）。
    两种规则写在同一份生成工具里并各自注明理由，避免下一轮误把其中一个当通用规则。
11. 主提交 `d6bbab8` 的提交信息里把工具名打成 `r7f6_staged_preight`（漏了一个 `f`），实际文件是
    `tools/r7f6_staged_preflight.py`。提交信息已发布，不改写历史；正确名字以本条与清单为准。
    （同一毛病上一轮也犯过一次，属我自己在长文本里漏字符，不是文件问题。）

**收口待办**：采集与端口均已关闭。按任务书 §4.C，收口时由操作者切断 VM＋MCU 最多 1 次且不再上电；
时刻与观察结果等回报后登记，没观察就写"未观察"，不倒填 R7F5A 的收口时间。

**推送与远端逐 blob 回读**：主提交 `d6bbab8`（父 `4e533c3`，快进推送一次成功，无 500 重试）。
`tools/r7f6_remote_readback.py` 在 `d6bbab8` 上回读：两条清单合计 **304 条**远端 blob 复算
`ok=304 mismatch=0 not_checked=0`；A 包 30 件在远端与 zip 逐字节相等；
5 件冻结件（旧执行器 `33d3f0af`、b502 BIN `502dca5d`、R7F5A handoff json、R7F5A CSV、R7F5A 原生 .sal）
在 tip 与 `4e533c3` 两个提交上 blob 全等；run_record §16/§17/§18/§19 各命中 1 次；
两份清单自身的远端 blob == 磁盘。回读报告留仓外 `D:/ns800_r7f6_readback/remote_readback_r7f6.txt`
（避免"清单登记自己的回读件"这种自引用）。回读工具本身在清单重算后的追加提交里入库，
追加提交后再跑一次同样的回读，报告同样留仓外。
（追加提交 `7a82147` 上的第二次回读实测：两条清单合计 **305 条**，`ok=305 mismatch=0 not_checked=0`，
A 包 30 件与 zip 逐字节相等，5 件冻结件 blob 全等，§16–§19 各命中 1 次，两份清单远端 blob == 磁盘，fails=0。）

**收口断电（操作者现场动作，登记两次报断）**：操作者先报「断电了刚才」（02:50 前后，未报精确时刻），
随后又报「51分我又重新断电了一次」（自述 02:51，分钟级采信）。任务书 §2 的收口断电上限是 **1 次**，
现场申报到的是 **2 次** ⇒ 如实登记 `OVER_CAP_OPERATOR_REPORTED`，不粉饰成"一次"；随后操作者补充
「每次断电之后我都会重新上电」（本机落盘时刻 02:54:48）⇒ 两次之间确实发生过重新上电，
因此同时构成 `OVER_CAP_ADDITIONAL_POWERUP(>=1 > 0)`，不再是 UNKNOWN；操作者随后确认
「还通着电」（本机落盘 02:57:08）⇒ 本轮收口终态 = `POWERED_ON_OPERATOR_CONFIRMED`，
任务书"不再上电/停在断电态"的收口预期**未达成**（`CLOSEOUT_POWERED_DOWN_EXPECTATION_NOT_ACHIEVED`），
如实登记，不粉饰也不指责。我在两次报断之间没有执行任何设备动作，本轮动作计数不变。
主机侧只读枚举两次（02:50:50 与 02:52:38，未开 COM、未访问 DAP、未动板子）：COM5 与 CMSIS-DAP v2 /
WebUSB: CMSIS-DAP / Saleae Logic 全部仍 Status=OK 枚举，两次之间 USB 拓扑无变化 ⇒ 探针由 PC 的 USB 供电，
与板端 VM/MCU 不是同一电源域，所以这条观察既不能证实也不能证伪 MCU 已断电，标签
`MCU_POWER_STATE_NOT_HOST_OBSERVABLE`；按任务书"DAP/USB 供电也计入 MCU 是否真正断电的现场判断"，
计入的就是"USB 路径未断"这条事实本身。私有 DAP UID（48 位十六进制）不入任何入库件，
只记长度 48 与 sha12 掩码 `586cb3f3ac73`。全部现场数据在 02:20:42–02:21:25 已采集并归档，
断电发生在测量之后，不影响任何判读结论。R7F5A 的 `PENDING_OPERATOR_REPORT` 不回填。
详见 `reports/reviews/phase8-m8-r7f6-la-path-static/POWERDOWN_REPORT.txt`。

操作者随后给出今后口径（本机落盘 03:00:01）：「我希望默认不断电」。采信为**今后现场轮的操作者默认
口径**——收口不再要求切断 VM＋MCU，默认保持通电，我也不再向操作者索要断电动作或时刻。边界说清楚：
这一条写在 A 的任务书里（本轮 §2/§4.C），属 A 定的固定范围，所以这句是**口径变更请求**，要 A 在后续
任务书采纳才生效；我不替 A 改判据，也**不回改**本轮已登记的事实与标签（R7F6 仍按原条款记
`OVER_CAP_POWERDOWN` / `OVER_CAP_ADDITIONAL_POWERUP` / `CLOSEOUT_POWERED_DOWN_EXPECTATION_NOT_ACHIEVED`）。
顺带提请 A 定范围时一并考虑两点：下一轮"已上电"前置条件将常态成立；板端将长时间带电。

## 20 M8-R7F7 离线核对轮（A 的"新版上板＋真实异步作业"包；设备动作 0，停在批准门）

2026-11-05 03:35 操作者转交附件 `NS800_R7F7_NewImage_Real_Async_Job_Task_20261105.zip`
（312901 B，SHA256 `15bf813bbd62ee7fcbc263f2c31c1a92f71a42f6e084498c0fa4f568d1076b62`），
**本轮只有附件、没有执行指令**。包内容是一次 flash 下载 + 一次定长回读 + 一次 COM5 会话
（开口/复位/`zero 32`/`learn 8 450 550 0` 各 1，明令"必须真实受理为 1 个作业，只看到 IDLE 或 REFUSED 不算通过"），
这些全是设备动作。按既有纪律：离线能算的全部算完并入档，**不开现场账、不下载、不回读、不开串口**，
把决定权交回操作者。（对照：R7F6 那一轮操作者说了「按此包执行」，所以我一次跑完。）

判据工具 `phase8-m8-r7f7-newimage-static/tools/r7f7_offline_checks.py`：**109 条判据，FAIL=0**，
原文 `proof/offline_preflight_r7f7.txt`。核心复算（都不采信包内文本自报，回到本机实读）：

- 包 28 件清单逐条磁盘散列相符（清单不含自身，zip 内 29 个文件）；镜像 BIN/AXF/map/callgraph
  在 A IDENTITY / A 派生绑定 / 真实 build_manifest / 磁盘 **四方一致**。
- **AXF 的 PT_LOAD（`p_paddr=0x08000000`, `filesz=297452`）逐字节 == BIN**，段 sha == `23691e8d…`；
  `memsz > filesz` 是 RAM 里的 RW/ZI，回读只比 flash 段。
- 源码：包内 10 件 frozen `.c` == `00718b4` 的 blob == 工作树（按行尾归一）；`source_bindings` 5 件
  （含两个 `.h`）在 tip 上 sha+bytes+**git blob id** 全等；`product_tree_sha1=32470c7e…` 我按"目录树"没找到，
  实读确认它是 `rt-thread-master` 子树对象号且在 `00718b4`/`7a08f9d` 上同一对象 → A 的相等声明成立。
  `00718b4..7a08f9d` 非 `reports/` 改动只有 `.gitattributes` → 产品与主机源码 0 改动。
- 执行器未动（`33d3f0af…`，仓内件与包内见证副本逐字节相同）；本机重跑 `--validate-plan` 得
  **58 行 checks=57 fail=1 msh_commands=74**，与 A 原文判定名+PASS/FAIL **逐行相同**，唯一差异是末行
  `plan=` 路径；唯一原始 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`（A 保留原 FAIL，未改执行器、未填假额度）。
- 本机重跑 `--approval-init` 与 `stub+sim` 整包：caps 21 项、identities 3 散列与 A 一致；S0–S3 全 OK；
  `quota_used` 恰为 6 项正额度各 1、15 项 0 额度 0 消耗；S0 facts 除路径/身份名外与 A 全等；needles_total=41。
- **41 条 needle 不只命中冻结件，也逐条命中产品工作树源码**；旧同步 learn 的 OK/FAILED 两条确已被异步字面量替代。
- A 的 host C 探针 `host_probe.c` 在本机用仓库自己的 `shim_rel`+产品目录**重新编译并运行**：
  rc=0，输出 **逐字节等于** A 的 stdout（CRLF 归一后 sha `fc8747c7…` 相同，字节差恰好等于行数 29）；
  `state=4` 的身份回 `diag_relative.h` 按 C 隐式递增推算 = `REL_JOB_FAILED`，`owner=0` = `REL_CAP_FREE`。
- 计划语义我另算的：15 步=1 复位+13 命令+1 被动观察；13 条命令顺序同 §5 表；17 条禁用命令零出现
  （注入 `motor_start` 会被抓）；75 s 窗+91 s 命令窗=**166 s ≤ 210 s**；`learn` 硬要求 `STARTED id=1`
  且明禁 REFUSED/FAILED；终态锁死 `id=1 FAILED/WINDOW_BROKEN owner=0 cred=1/0` 且明禁 DONE/RUNNING/STARTED；
  失败词白名单 5 条全锚定且只 `job_failed` 一处含 FAILED（未全局放行）。

**供电口径被 A 采纳**：任务书 §3 与计划 `operator_power_policy` 写明"已上电就保持供电、收口不再要求断电、
`normal_end_powerdown=0`、`additional_powercycles=0`、必要时建立供电至多 1 次"，即操作者
「我希望默认不断电」这条变更请求在本轮生效；同时保留"通电状态以现场实报为准、异常使能时操作者硬件禁能优先"。
R7F6 已登记事实与三个超额度标签**不回改**。

自伤 18 项（详见 `OFFLINE_REVIEW.md` §7），值得留的三条：① 我把会话预算先手算成 162 s 就写进判据，
实算 166 s——**算术错的判据会把正确的计划钉成失败**；② 取 PASS/FAIL 用固定下标，步骤级行的判定在第 4 段，
造成假红；同一写法若用在泄漏扫描上就会漏报；③ 掩码顺序先短后长，把仓外 `D:/ns800_r7f1_scratch`
掩成 `<REPO>_r7f1_scratch`，读起来像证据在仓库里。另两条口径取舍如实登记：通用 `token=` 模式命中的是
执行器形参名、通用 `X:/` 模式命中的是 A 包里合法的 `C:/Keil_v5` 工具链路径，我**撤销模式并写明理由**，
没有改包件字节。第 14 项是同类复发：我新写的提交前置检查器自己带了路径字面量样本，连它自己的模式定义行
都满足自己的判据，被本轮泄漏扫描抓到两处（改成运行时用 `chr(92)` 拼装才干净）——检查器自证必须每轮真跑。
第 16 项是我把本该写进本文件的补句误编辑进了交接 md 的 §5，删掉了一行，随即按原文恢复；
说明"改文档"也要有范围核对，不能凭手感连续 Edit。

现场账本仍未开：核准根目录下 `*M8R7F7-2369-async-job-static*` 匹配 0 件（`FIELD_APPROVAL_LEDGER_ABSENT|PASS`）。
本轮交付：A 包 28+1 件原件、6 件本机复算证据、1 件判据工具、`OFFLINE_REVIEW.md`、本节的 §20。
产品代码、固件、其他轮次证据 0 改动。

## 21 M8-R7F7 现场轮（新镜像上板 + 真实异步作业受理与越窗失败收口；操作者已批准，一次跑完）

**授权**：操作者 2026-11-05 04:14 原话「这一轮没问题就直接批准你执行」，逐字登进
`field/.../run_log.txt` 第 2 行；令牌用包内 `approval_token.txt` 原样 58 B（`7c93e682…`），不拼接不改写。
离线核对轮（§20，109 条判据 FAIL=0）已先把 A 的每项声称在本机复算过，才允许这唯一一次 dispatch。

**额度（六项正额度各 1，用满，无第 7 次）**：下载 1 / 定长回读 1 / COM5 开口 1 / 显式复位 1 /
`diag_profile zero 32` 1 / `diag_profile learn 8 450 550 0` 1；`dispatch` 1。
禁用项 0 消耗（arm/start/apply/IRQ/极性/运动/cancel/tier/commit/clear/config_save/重试/分析仪），
`APPROVAL_LEDGER_END` 的 `pending=0`、`spent` 与 caps 逐键相等（负控：构造 n=2 重复预约会被抓到）。
dispatch rc=0，墙钟 **117.2 s ≤ 计划 210 s**（含下载与回读）。

**板端事实**（详见 `phase8-m8-r7f7-newimage-static/FIELD_JUDGEMENT.md`，判据 99 条 FAIL=0）：

- 回读 **297452 B**，SHA256 `23691e8d…`，与被签收 BIN **整文件逐字节相等**；门先在"取反 1 字节"和
  "少 1 字节（任意窗口）"两种缺陷形状上验过会命中。**新版镜像逐字节在板已证**；b502 只作历史证明保留，
  不再是当前在板身份。下载旁证 erase=program=297984 B（291 扇区），唯一载荷是被签收 AXF，未回烧 b502。
- 复位后**被动启动窗申报 75.0 s、实耗 75.0 s**，结束原因 `DRAIN_BUDGET_REACHED`（不是静默早退），
  捕获 3468 B；首条查询出现在开口后 76.813 s ⇒ 观测下界真被覆盖。启动流正面读到 TMC IOIN 成功
  （VERSION=0x21）、`BOOT→INIT→SELF_TEST→READY`、`required=PASS degraded=0x00`、`Phase-7 bootstrap complete`，
  无 FAULT_LATCHED / 无 `[REQ FAIL]`；严格 UTF-8、裸 NUL=0、CR=97 LF=75 最长行 126 B。
- 13 条命令顺序与 A §5 表一致；13 份判据我用**仓库原执行器自己的 `assert_step`** 在归档字节上逐条复算全 OK。
  每份原始响应的开头都是所发命令的板端回显；开口后残留提示符另存为 2 个 `.pre.rx.bin`（7 B/5 B），
  没有回填进响应。13 份全部严格 UTF-8、裸 NUL=0，字节数按 115200 8N1 下界都在该步申报秒数内。
- **真实作业受理**：`learn 8 450 550 0` 回 `STARTED id=1 n=8 win=450..550 dir=0`（不是 REFUSED/IDLE），
  并回 `learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED`。
  **越窗失败收口**：`id=1 st=FAILED why=WINDOW_BROKEN owner=0 cred=1/0`，`n=0/0 want=8`；采集器已释放、未 DONE。
  learn 响应 t=77.563 → `WATCH_DONE` t=81.578（申报 1 s，`ended_by_budget=False`）期间**未发任何命令**，
  终态查询 81.594 ⇒ 判越窗失败的是板上 Diagnosis Thread，不是我补命令催出来的。
- 静止 zero：`zero OK n=32 raw 2055..2065 mean=2060437 (cal source untouched)`，凭据从 0/0 到 1/0，
  未 config_save、未 clear、未复位。`ps` 正面读到 main/diag/tshell 均 4096（高水位 40 %/28 %/16 %）；
  `free` 登记本场新四项 32672/22120/22120/10552 并核 `total=used+available`、`maximum≥used`，不沿用 b502 旧值。
- 全程电机未转：两次 `motor_status` 仍未武装、频率 0、请求禁能、ENN HIGH、arm gate `0x18` REFUSED；
  两次 `system_status` 仍 READY/fault=0、IRQ CLOSED、极性未确认、protect_ready NO、drive_cfg UNCONFIGURED。
  失败词没有全局放行：`FAILED/WINDOW_BROKEN` 只在 `job_failed` 一处按整行 + id/owner/凭据锁定。

**本轮结论只写这些**：新镜像逐字节在板、启动静态状态通过、静止 zero 通过、异步作业真实受理及越窗失败收口通过。
仍未证：正常巡航下的采样进度与 DONE 有效基线、活动取消与取消竞争、commit/config_save 与重启恢复、
SG/ADC/IMU 真实保护触发与恢复、实际 STEP/ENN 动态时延、带载最坏栈。
`polarity_measured=false`、`core_protection_verified=false` 保持；执行器自报 `flow_complete=True` 只是流程与账目一致。

**自伤 7 项**（正文 `FIELD_JUDGEMENT.md` §5）：① 我的 heredoc 补丁把 `b"\r\n"` 写成真实 CR/LF，
判读工具直接语法坏掉（且 Read 的行视图与 `newline=''` 的切分不一致，替换区间要按后者定）；
② 第一版 3 处判据设计错（`ASSERT_STEP` 的 `all()` 扫了台账全部行、回显判据拿"剥完之后"的行比命令、
启动捕获文件名没按执行器的 `.boot` 标记）；③ **归档清单用手工累加，漏了 `readback.bin`/
`readback_identity.json`/`dispatch_ledger.jsonl` 三件**（手工 50 vs 磁盘 53）——决定性证据没进自己的清单，
等于"我以为归档了"，改成落盘后 walk 生成并加负控；④ 跨阶段台账第一版没归档，与 idle-stack-r4 先例不符；
⑤ 隐私判据第一次写成恒真比较（把 UID 拼进被检数据再断言能命中），换成合成脏样本 5/5 命中；
⑥ 模式字面量第三次差点复发（`AppData`/`Users\` 直接写进检查器源码就会被本轮泄漏扫描抓到），已改运行时拼装；
⑦ 类型混用：`uid` 是 `read_text()` 的 `str`，被检数据是 `bytes`，`pat in data` 直接 `TypeError`。
另如实登记一处残留：仓外 scratch 路径以 JSON 转义形态留在 3 件工具元数据里；我**没有**为"装干净"去反掩改台账字节。

**收口**：COM5 已关闭，**保持 VM + MCU 供电**（A §3 已采纳"默认不断电"），保持未武装/禁能/IRQ CLOSED；
候选零点与失败作业记录留在 RAM，未为清理它们额外复位/断电/clear/save。
交付：`field/field-20261104T201458Z-r7f7/`（53 件，含 `readback.bin` 与两份台账）、
`proof/field_judgement_r7f7.txt`（99 条 FAIL=0）+ `proof/field_business_outcomes.json`、
`FIELD_JUDGEMENT.md`、`tools/r7f7_field_judgement.py`、扩展大写 UID 模式的 `tools/r7f7_staged_preflight.py`、
新交接 `phase8-m8-r7f7-newimage-static-field.{md,json}`、重算的 `ROUND_SHA256SUMS.txt`。
产品代码、固件、A 的包原件、旧轮证据与旧账本 **0 改动**。

**§21 追加（同轮，只追加不改上文）**：上面写的「自伤 7 项」在本节入库后又被远端回读抓到 1 项，现登记为第 ⑧ 项：`r7f7_remote_readback.py` 的 `RUN_RECORD_SECTION` 把段计数检查写死成 §16..§20，本节 §21 落在门外面——内容虽然由逐 blob 哈希承担，但「各段恰好一次」这道门没管到它，覆盖声称过宽。补到 §21 后另一次提交、重算清单并重跑回读：远端 tip 上 406 条 blob（本轮 100 + R7F6 78 + r7f2b 228）全相符，A 包原件 29 件与 zip 相符，BIN/AXF/执行器散列相符，产品子树 `32470c7e…` 相符，§16..§21 各恰好一次，fails=0。设备动作仍为 0，上文与旧提交不回改；本节的判据原文与归档字节未因此改动（`field_judgement_r7f7.txt` 仍是 99 条 FAIL=0）。

## 22 M8-R7F8 草案离线核对轮（A 的保护闭环任务书草案 + 操作者四项补充；设备动作 0，停在参数/授权门）

**输入**：操作者转交 A 的草案单文件 `NS800_R7F8_Protection_Closed_Loop_Draft.md`
（24780 B，sha256 `b572c1928f60…`，LF，自标 `DRAFT_NOT_AUTHORIZED`、硬件额度全部 0），
随后四项补充并附一句「补充并更正已下发的 R7F8 任务书，请合并到现有文件继续，不另开纯登记轮」。

**本轮做的事**：① 四项就地合并进同一份任务书（§9 保留修订记录，不另起平行任务书）；
② 草案替我 R7F7 证据加的每一句话回到原始字节复算；③ 草案 §4 的源码合同逐条钉到真实行号；
④ 回答草案 §8 前置第 2 条（未改动的执行器能否表达 R7F8 五类场景）。
**没做的事**：开串口、dispatch、下载/回读/复位、任何写命令、分析仪、Keil、建审批账全部为 0；
`C:/m8r_approvals` 下 `*R7F8*` 匹配 0 件（`NO_R7F8_LEDGER_OPENED`）；也不生成可 dispatch 计划。
判据原文 `proof/r7f8_draft_checks.txt`：**116 条，FAIL=0**，含 3 条必须会命中的负控，另有入库前补的 6 条
字节不变门（`-text` 规则生效 + 匹配器在 R7F7 旧规则命中 + 注释同形不算规则 + 预检白名单解析出 5 项 +
待提交 6 条无越界 + 白名单外合成路径必被判越界）。

**承接对账全部相符**（`E_*`）：清单 100/100、A 包原件 29/29、归档 53 件、readback 与签收 BIN 整文件相等、
启动 3468 B 且 75.0/75.0 `DRAIN_BUDGET_REACHED`、`zero` raw 2055..2065、`STARTED id=1 n=8 win=450..550 dir=0`、
终态 `id=1 FAILED why=WINDOW_BROKEN owner=0 cred=1/0`、`n=0/0`、learn 77.563 与终态 81.594 之间实发命令 0 条、
`[MOT] expected_ENN=HIGH ENN_fb=1 resolved verdict=OK` 与 Safety 侧 verdict=OK 均在原文里、
armed=0/cur=0/tgt=0/IRQ CLOSED/极性未确认/protect_ready NO/UNCONFIGURED/arm mask 0x18、clear/config_save 全 0。
草案说的"A 独立复核 23 项 FAIL=0"我**不背书**：那是 A 侧运行，仓库无其原始输出，只登记为 A 自报。

**草案要按源码改的 5 处**（会改变定稿判据）：
① §4.2"加减速例外只抑制 SG/负载判据"不成立 —— `diag_relative.c:491-497` 在 ACCEL/DECEL 清计数并 return，
而冲击判定在 `:595-610`，**IMU 一起被抑制**；`:493` 的源码注释本身与代码不一致（登记，不改码）。
② ADC 的"新样本数"不是可调参数：`:643` 硬编码 `>= 2u`（融合 `:651` 同 2u），只有 SG 走 `min_new_sg`。
③ 源失效/越窗/覆盖丢失不是"另一条独立降险路径"：7 个原因同属 severe（`:65-69`），同一出口
`safety_post_event(EVT_MULTI_FAULT)`（`:725`），并**共用同一枚一次性票据**（`:1061`）⇒ 三场刺激每场都要重建代次。
④ §4.3 把恢复计数写成 `fault_reset` 的门：`safety_state.c` 里 fault_reset 无任何样本计数（真实门是 pad LOW/
ENN 禁能侧/源电平/安全关闭复核/复核后再确认/STEP 结果），样本判据在引擎侧 `:384-385` 且要 `ok_src_seen == 3u`
（**三源**，不是两个）。⑤ 学习模式条件确实是 `!= DIAG_MODE_MONITOR_ONLY`（`:2016`，每拍复核 `:1725`），
定稿白名单要钉 `learn REFUSED: mode must be MONITOR_ONLY` 整行，不能写成"非 ACTIVE 即可"。
另补两条源码事实：`motor_stop` 后 armed 仍 1（重复 start 不被拒，再 arm 会以 `not at rest (… tgt=…)` 拒
`motor.c:1153`）⇒ 场间必须真 disarm；候选签名吃 `tmc_cfg_state`（`:195`）⇒ UNCONFIGURED 下的零点在 apply 后
必 `SIG_CHANGED`，"配置应用后重取零点"是硬要求。

**执行器答复（草案 §8 前置 2）**：命令面够用（9 条前缀 / 8 个额度键；`learn_cancel`、`motor_stop/disarm` 不在表内，
可作无额度只读/普通步骤）；但有**两条结构性缺口必须先离线适配**——
`poll` 的重试门要求响应里 `[MOT] state=` 属 ACCEL/DECEL（`m8r3_serial_chain.py:1939`），
所以有界轮询 `learn_status` 到 DONE 现结构不支持；`cmd` 全程静态（`:2083`，唯一动态拼接是 `flash_dump`），
所以"JOB_ID 从真实回包取"需要受控的捕获→命令文本替换 + 反例。另两处需口径：清理固定
`motor_stop→motor_disarm→motor_status`（`:1413`）使 stop/disarm 无法分开收口；运动预算只在 `motor_start` 起算
（`:2177`），草案要的"arm 独立截止"得定稿补判据。分析仪不能与串口同一次 dispatch（tools 只认
download/readback/reset/reboot）⇒ §7 分开计数正确；"额度用尽仍发收口"确有实现（`:1638`）。
复用 R7F7 阶段会被其自报禁用项挡下 ⇒ F8 必须新阶段新计划。

**参数分栏**：源码已给 `zero 32..256`、`learn N 8..32`、`LO/HI 1..20000 且 LO<=HI`、`DIR 0|1`、12 s 编译期 deadline、
10 ms 一拍、shell 有界等 20 ms（⇒ 取消窗口是一拍量级）、IMU 默认 2500 mg/新样本 2/100 ms/年龄 100 ms。
只能人给：DIR/HZ/LO/HI/加减速/各段硬截止与总预算、N_CANCEL/N_DONE/N_ZERO 具体值、三通道刺激方法与上限时长次数、
行程与限位、硬件禁能手段位置、仪器设置、极性/IRQ 依据、"负载传动行程相对历史台架有无变化"的现场结论。

**四项补充的落点**（额度不变）：① 1.5 A 以 **USER_REPORTED** 登记（§1 表 + §1.2 + §3），
并显式保留来源等级——本仓曾主动删除未确认的"额定 1500 mA RMS"断言（`phase8-motor-chain-dev.md:32`），
`source_audit.md:107` 亦注明 VREF/R110 是相电流设定口径不得代进母线换算；② 历史值与 M4 的 300 Hz/60 s
沿用为工况设计依据（锚点 `plan_core_r7.json:194` 含"保持 IRUN20 现值, 不抬流"），不升格为已通过也不结转旧运行额度；
③ 启动顺序改到 §1.2/§4.3/§5 P2 第 1 步，两条源码直证（`motor.c:1151-1153` 要求 arm 时 target=0；
`motor_set_target_hz` 体内无 armed）⇒ **无需改产品代码**；④ 承接口径进 §1.2 第 4 条。
§7 表前加"本补充不增加任何既有动作额度"，表体仍全 0/TBD。入库 `A_DRAFT_ORIGINAL.md`（收到的字节，不动）与
`R7F8_DRAFT_MERGED.md`（合并版 31807 B，`270383c12b1c…`）；操作者 Downloads 那份是同一维护线的当前版本，
本轮就地修订的就是它。

**自伤 10 项**（当场修并重跑到 FAIL=0）：① 两份子代理审计各有 1 条判定与源码不符
（"learn 门是非 ACTIVE 即可"、"SG 用引擎侧 run_baseline"），我回源码推翻后按 `:2016`/`:537` 的原文写——
**子代理的结论不能照抄**，它连"草案说法无依据"的判定本身也可能是错的；② 检查器第一版 24 条 FAIL，
成批暴露我自己的判据写错（时间戳取了 rx[11]、把开区间记成含端点、`"".join` 吃掉换行使"99 条判据"读成 1 行、
额度表按 dict 语法解析实际是 tuple、模式写成 `reason = REL_*` 而源码是 `e->verdict = REL_*`）；
③ 钉行号前先核：多枚锚点（`:829`→`:831`、`DG:637`→`:630`、`DH` 枚举空格）经负控暴露后改用
"搜索并登记实际行号"，不再假装精确行号；④ 泄漏模式字面量第四次差点复发（`AppData` 直接写进新脚本），
仍按运行时拼装；⑤ heredoc 里嵌 ASCII 双引号两次把 Python 打断，改「」与 `NL = chr(10)`；
⑥ 合并草案时我先在 §4.1 与 §4.3 各写了一条"起转顺序"造成重复，删掉一条；
⑦ 交接 JSON 的 `live_copy_path` 写了"反斜杠夹 Users"的主目录形态，**会被本轮自己的泄漏扫描抓到**
（预检对暂存 blob 扫同一个模式），改成不含主目录/用户名的位置描述，逐字节对账改由 `RBI_ORIG` 现场比对承担——
同一份生成脚本还把反斜杠双重转义了（JSON 值里出现双份反斜杠），一并消失；本节同样故意不复写该模式；
⑧ 预检白名单写成 `R7F8 + "/"` 拼接形态，新增的 `PREFLIGHT_ALLOWED_PARSED_EXACTLY` 解析后只剩 `"/"`，
三条待提交路径被判越界 => 白名单改为完整字面量（判据逼出来的，不是我先想到的）；
⑨ 新判据的 detail 文案写死"全部落在预检白名单内"，FAIL 时也在念这句 => 改成如实报"白名单外 N 条"；
⑩ 回读工具里留了一次结果被丢弃的 `cat-file --batch` 调用（输出没被用，注释还把原因写反），删掉。

产品代码、固件、执行器、R7F7 归档字节、旧轮证据与旧账本 **0 改动**；本轮只读。
仍待：A/操作者对 5 处口径与待定参数拍板，以及一句"去做执行器适配"的授权（改工具不动板）——
在那之前不开 R7F8 的现场账，也不把草案变成可 dispatch 计划。

## 23 M8-R7F8E 执行器离线适配轮（按 A 任务书 §3/§4/§5 做工具派生件与反例；设备动作 0，停在复审门）

**输入与不变项**：基线 `d6bbc1b5795f`；A 的附件包按 `SHA256SUMS.txt` 23 条逐条相符后整体归档到
`phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8E_PKG/`（24 文件 / 165145 B，含任务书、
修订后的现场草案、IDENTITY、A 侧 R7F7 的 23 项输出、四例主机探针原件与其失败首次尝试）。
产品源码仍绑定已签收 `00718b49e655`；本轮所有 .c/.h、固件、工程、旧执行器
（219491 B / `33d3f0af8a4c…`）、旧现场原件、旧批准账与 R7F7 输入 **字节未动**；
Keil / DAP / COM / 分析仪 / 供断电 / arm / start / 学习 / 取消 **全部为 0**。

**做了什么**：`tools/r7f8e_build_executor.py` 从旧执行器原字节按 **29 处逐字锚点**（每处必须恰好
命中一次）生成派生件 `tools/m8r3_serial_chain_r7f8.py`（3977 → 4447 行，+485/−15，
245730 B / sha256 `4c962170b2357eef94afd73d43790d61880635a3d13287d29d543035768d6fed`，
补丁 `patches/r7f8e_executor.patch` 38672 B / `27d3d49133721775…`），`--verify` 仓外重建逐字节相等。
四项适配按任务书落：E1 有界轮询（`in_progress` 申报＋次数/窗口/间隔三上限＋三者最早截止夹住＋
DONE 正例核 owner/凭据/样本数＋首份坏响应即停）、E2 动态 JOB_ID（只从本步 STARTED 整行取值，
限 1..999999999，命令面只允许 `diag_profile learn_cancel @<绑定>`，无 eval/format/shell，
派生命令进台账不回写获批 plan）、E3 取消显式额度（映射新增 `diag_profile_learn_cancel`，
缺键/错键/n≠1/额度 0/耗尽都在 write 前拒，缺省按 0，不自动升级旧计划）、
E4 通电截止与固定清理对账（`EnergizeBudget` 在 `motor_arm` 写入尝试前建立，未 start/学习/等待/
正常 stop 后仍 armed 都在它里面；清理仍固定三条且重复发送分别记账）。
顺带修掉两处被新反例照出来的**既有**工具缺口：单会话路径 `cleanup(…, quota_box if plan else None, …)`
传形参导致清理预留账永不生效（T3）；`validate_plan` 在计划缺 `tools.*` 时 `AttributeError` 崩在
建账之前而非具名 FAIL（T4）。两者都在 dispatch 现场路径零行为变化，且各配判据（`L3` + `X5`、
`proof/r7f8e_stub_package.txt` 保留旧件崩溃原文）。

**三张判据集**：矩阵 **65 判据 FAIL=0**（11 组 + 5 项元负控真运行被破坏副本 + 4 项原始字节探针 +
4 项进程内身份反例 + "故意写错预期必须判红"的自证）；STUB_ONLY 取消＋DONE 整包 **29 判据 FAIL=0**
（`JOB_ID_BOUND={cancel_job:17, done_job:73}`、实发 `diag_profile learn_cancel 17`、
额度 `learn 2/2`、`cancel 1/1`、`motor_run 1/1`、清理 `1/1/1`，负例四场分别具名
`BUSINESS_FAILED`/`POLL_ATTEMPTS_EXHAUSTED`/`POLL_WINDOW_EXPIRED`/`NUL_BYTE_IN_RESPONSE`，
同一计划走 `--transport real` 在 0 条开口事件前被拒）；无回归门 **8 判据 FAIL=0**
（冻结 R7F7 定稿计划：`checks 57→62`、旧判据 0 条被洗掉、新增 5 条全是 `R7F8E_` 形状、
唯一既有 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`）。

**勘误与草案**：`ERRATA_R7F8E.md` 逐字引用我上一轮三句错解并给源码/行为反证
（`diag_relative.c:669` 置 1u、`:674` 置 2u、`:385` 要 `== 3u` ⇒ 3 是 `1|2` 的**两源位掩码**，
不存在"IMU 正常位"；`learn_cancel` 改作业状态不是只读，旧映射漏列属工具缺口；
"取消窗口就是一拍"从 10 ms 与 20 ms 两处不同等待推不出来）；旧报告、旧 116 条输出、§22
与上一轮交接件一律原样保留，不假装旧工具早已检出。现场草案按 A 的修订快照逐字承接
（`INHERIT_EXACT|equal=True|正文行=272`，合并件 36273 B / `e9a5253de024`），
`A_DRAFT_ORIGINAL.md` 仍 24780 B / `b572c1928f60` 未改。

**自伤 15 项**（都是自己的工具/夹具错，当场修并重跑到 FAIL=0）：① 台账事件字段名撞 `Ledger.ev`
的首位参数 `kind` ⇒ 取消步当场 `TypeError`（第一版矩阵 18 条 FAIL 的主因）；② 想用 JSON 夹具表达
非法 UTF-8 —— 严格 `encode` 根本表达不了，改成进程内向同一条读路径灌原始坏字节；
③ 清理段 TX 按字面 `"\\r"` 切，一条也没切出来 ⇒ 那条断言恒真空（差点把"没观察"当"观察到"）；
④ 用例名里的 `/` 当目录名用，账本写进两层目录；⑤ 元负控 `X2` 一开始写成 `code != None` 的恒真比较，
改成"终态被等成窗口耗尽"的具体新身份；⑥ 合成行表把"响应行列表"再套一层 ⇒ `join` 当场 TypeError；
⑦ 首错去 `DISPATCH_SUMMARY` 找 `code=`，而它在整包级恒空 ⇒ 改读阶段行 JSON；
⑧ Q1–Q3 让"作业未绑定"这道无关门抢在"额度未申报"前面答 ⇒ 三道额度门各自补上绑定后重测；
⑨ 计划缺 `tools` 面时我先以为是自己写错，复算旧件才发现是旧件崩溃（旧件原文留档）；
⑩ 声明"逐字节承接"之前没让工具自己剪节复算，补 `INHERIT_EXACT` 一行后才敢写这句话；
⑪ stub proof 里漏出仓外临时目录真实路径 ⇒ 四种斜杠形态逐一掩成 `<WORK>`，并写成两条判红的断言
（`掩前命中=3/4 种, 掩后残留=0 种` 与"这条掩码判据不是恒真空"），另加一道不计入判据的末次硬门
`rc=2`；这道硬门第一次真跑就烧过——掩码只作用在原文块、没作用在判据行上，改成对整份正文掩之后
才 PASS（两条判据行因此排在掩码**之后**追加：判据行本身不能是被掩的对象）；
⑫ 预检的白名单自解析判据按"恰好 11 空格 + 行尾逗号"取字面量，而首元素写成 `ROUND + "/"` 拼接、
末元素后跟着收尾括号 ⇒ 只解析出 6/8 条，**这条判据把自己的白名单判成不相等**（改为一行一条
完整字面量，另加"字面量与常量同步"防两边漂移）；
⑬ 回读的"产品子树"判据把 A 登记的 `32470c7e…` 当成 `applications` 全路径去取，第一次真跑即报
假 FAIL（实际 `b052c480cf46`）—— 该路径口径在 R7F7 轮 `OFFLINE_REVIEW.md:53` 已写明是**顶层
`rt-thread-master` 子树**的对象号，我没回查就写判据；改正后补 `SELFCHECK|产品子树判据会红`
（取不存在路径，证明这条比对不恒真），判红原文留 `proof/r7f8e_remote_readback_first_attempt.txt`；
⑭ 回读 §23 判据要从 `got` 取 `run_record.md` 而 `EXTRA` 原本没这件 ⇒ 必然报"远端取不到"，
这条是**真跑之前读自己脚本**发现的，不是被执行抓到的（记在这里是为了分清取证来源）；
⑮ 按目录 `git add` 把**推送后才该存在的回读回执** `proof/r7f8e_remote_readback.txt` 一起暂存了，
而当时六项预检全绿也拦不住 ⇒ 补第 0 项 `MANIFEST_COVERAGE`（磁盘件要么在清单里、要么正是那件
声明排除的回执，且回执不得入库），其负控**用同一段判定代码重跑**；我第一版写成
`drop not in man_rel - {drop}` 是恒真，不算负控。

**材料缺口如实登记**：任务书与被引用的现场草案最初不在会话附件里，我先按用户正文口径做了一版
自设计适配；拿到 A 的 zip 后那版整体废弃（`field/m8r4_serial_chain.py`、
`tools/r7f8e_negative_checks.py` 及其 proof 已移出仓库，留在仓外 `ns800_r7f8e_superseded/` 不再引用），
本包命名、键名、门语义与交付清单全部按任务书重做。

产品代码、固件、执行器原件、R7F7 归档字节、旧轮证据与旧账本 **0 改动**；本轮不构建固件、不开现场账、
不 dispatch。仍待：A/操作者复审这份适配（含 T3/T4 两处附带修正是否接受），以及 R7F8 现场轮的
刺激方法、速度窗、各段时限与真实动作预算定稿 + 单独的现场批准。设备按上一轮约定保持通电收口。


## 24 M8-R7F8F 返修+整包轮（A 包 NS800_R7F8F_Repair_And_Field_20261105；设备动作 0，停在静态预算待 A 定口径）

被审件与冻结输入（一字节未动）：派生执行器修前件 `4c962170…`（245730 B，R7F8E 那份）、
更早的冻结旧件 `33d3f0af…`（219491 B）、产品树 `32470c7e…`（对应已签收 `00718b49…`）、
在板镜像 `23691e8d…`（297452 B）。本轮**不构建、不下载、不回读、不复位、不动分析仪、
不断送电**；每次 dispatch 都是 `--transport stub --backend sim` 的 FakePort，批准账只建在
仓外工作目录。离线门台账里 5 条 PORT_OPEN 全部 `transport=stub` 且 `port` 字段为空
（`NO_REAL_PORT_IN_ANY_LEDGER`），所以"本轮没有设备动作"是机器核过的，不是自述。

### 24.1 修后派生件身份

`tools/m8r3_serial_chain_r7f8.py` = 289452 B / sha256 `043d5bdd179c61ea87c8b9f2e095a851560a59ffe656aa7331ff8b43d7e23832`
/ 5100 行 / 51 条精确整串替换；配方 `tools/r7f8f_build_executor.py`，unified diff
`patches/r7f8f_executor.patch`（67742 B，sha `c6c9f60367db…`）。`--verify` 在仓外重建后与
仓库产物逐字节相等（VERIFY|PASS）。

本轮在 §2 F1~F5 之外，按操作者与 A 的三轮实测补了 F5b/F5c/F6/F5e/F6b~F6d：

| 项 | 补的是什么 | 判它的地方 |
| --- | --- | --- |
| F5b① | 尝试过 `safety_irq_enable` 本身就建立尾部义务，与电机义务分列（真实序列是先开 IRQ 再 arm） | 矩阵 H10 + 重放 I4 + 整包 FIRST_ERROR|learn_no_started_row |
| F5b② | 收口预留取申报值 7 s（五条各 1.2 s 窗），排不下就在**开口之前**具名拒；夹短时打 `CLEANUP_RESERVE_SHORT_BY_SESSION_DEADLINE` | 矩阵 H11/H12 + 台账 `CLEANUP_ENTERED` 之后的条数/跨度判据 |
| F5c | 业务绝对截止 = 总截止 − 预留（`may_obligate` 时），读/轮询/等待/写前检查同受它管 | 矩阵 H13/H14 + 突变 X6/X7 + 重放 I7 |
| F6 | 静态预算按**互不重叠**三段算（起转前 / 运动窗内 / 停机后），cap 只加一次；申报镜像与执行器现算逐字段比对 | 计划门 PLAN_BUSINESS_RESERVE_SPLIT + 整包门 NO_DOUBLE_COUNT / MIRROR_SAME_VALUE + 元负控 META_* |
| F5e | IRQ 尾部的 CLOSED 证据按产品**两条真行**认：disable 打 :306 detach 行，status 打 :388 `irq gate   = CLOSED`；两行不再互相顶 | 矩阵 H5/H6/H10/H14 + 整包 STUB_NORMAL_TAIL_SENT |
| F6b/F6c/F6d | 整包 dispatch 把 F5b/F5c 的回执字段带出来；stub 装配给 IRQ 尾部补行；申报过的响应序列只归轮询那一步用 | 整包门 STUB_NORMAL_* + FIRST_ERROR|* |

### 24.2 四族判据与整包离线门（**裁定落地前**的数；落地后见 §24.9）

- 反例矩阵 `proof/r7f8e_case_matrix.txt`：**98 条判据 / FAIL=0**（含元负控 X1~X7 与"故意写错的
  预期必须被判红"的自证）。
- stub 整包 `proof/r7f8e_stub_package.txt`：**29 / FAIL=0**。
- 无回归 `proof/r7f8e_no_regression.txt`：**9 / FAIL=0**；同一份 R7F7 定稿计划上，旧件 57 项
  fail=1、派生件 65 项 fail=1，**旧判据一条都没被洗掉**，唯一既有 FAIL 仍是
  `PLAN_MOTION_CAP_BUDGET`（空运动链的既有检查器缺口，草案轮已登记）。
- 三版本重放 `proof/r7f8f_probe_replay.txt`：**21 件 × 3 侧 / FAIL=0**（同一输入分别喂修后 /
  F5b 修前 / F5c 修前，按各件应有行为申报）。
- 最终离线门 `proof/offline_gate.json` + `proof/r7f8f_offline_gate.txt`：**40 条判据 / FAIL=2**，
  两条红的名字固定是 `PLAN_POST_SPIN_CONTENT_FITS_CAP` 与 `PLAN_WORST_CASE_TAIL_FITS_SESSION`；
  `overall` 因此是 FAIL，不写 PASS。validate-plan 自身：checks=144 / fail=2（就是这两条）。
  第 40 条 `JSON_RECEIPT_SHAPE_ALIGNED` 是本轮补的**回执形状判据**（见 §24.5 第 13 条），
  补正前后同一输入的 PASS/FAIL 结论逐条一致。
- 夹具逐项变更 `proof/r7f8f_fixture_changes.md|.json`：基线 42d7f7d 五个族的判据名逐条对齐
  （65→98 / 27→29 / 32→36，另两件本轮新增文件 7 与 33 条），**removed=0**（基线一条不少），
  新增 33/2/4/7/33 条逐条带人写理由，同名判据无结论翻转，problems=0。

### 24.3 现场包（**裁定落地前**那一版；现件见 §24.9。已作者化，未获批准，未执行）

`field/plan_r7f8f_2369_real.json`（28090 B，sha `66ca8a15…`）+ `field/rows_r7f8f_real.json`
（8168 B，sha `4022306d…`），由 `tools/r7f8f_make_real_package.py` 产出：一个 COM5 会话、38 步、
§4 的四个固定数（cap 12 / arm 18 / 总 90 / 预留 7）逐字段核对；download/readback/reset/reboot/
config_save/clear/retry/物理刺激额度全 0；`polarity_measured=false`、`core_protection_verified=false`
保持原值。镜像摘要由工具当场从盘上原字节重算（`IMAGE_SHA_COMES_FROM_DISK_BYTES`），不是手抄。
拿不到逐字打印形状的查询（tmc_config_apply/show、diag_status、diag_profile show/commit、ps/free）
只断言"命令回显 + 拿到完整回包"，这些步骤列在计划的 `field_assertion_gaps` 与
`_about.unverified_shapes` 里 —— 宁缺勿造。机械前置门在现场原件里保持
`DECLARED_BY_OPERATOR_AT_APPROVAL`，计划不许自证（`FIELD_PLAN_PREREQ_NOT_SELF_CERTIFIED`）。

### 24.4 三段算术算出来的两条真红（交 A 定口径，本轮不自行选择）

> 这两条的口径 A 已在预算裁定里给出，落地后的数与新语义见 §24.8/§24.9；本节原文保留，不改写、不删（裁定二末句也要求 91/23 的红原文继续留档）。

1. **起转后内容排不进 §4 的 cap**：三段是 起转前 19 步（申报窗 34 s）/ 运动窗内 9 步（11 s）/
   停机后 10 步（12 s）。执行器在 `motor_start` 写入尝试时置 `MotionBudget.t0` 且**正常 stop 不
   清**，所以 §5 行 12 之后（含 13 disarm、14 commit、15 ps+free）同被 `t0+12 s` 夹住；而起转后
   申报内容是 23 s。要么放宽 cap（同时动 §6 的 motor_run_seconds_total=12），要么认可"已确认零
   速后解除运动 cap"的运行时语义改动，要么把 §5 行 14/15 挪到下一会话。三条都不在本轮自行决定
   的范围里。
2. **每步都慢到 SLACK=3 s 上限时的最坏情况**：起转前 19 步合计 91 s 已超业务预算 83 s（90−7），
   逐步仿真里第 19 步（`motor_target 300`）会在写之前被 `STEP_DEADLINE_BEFORE_WRITE` 拒。这条是
   **取证完整性**风险，不是撤能风险：收口预留由 `business_abs_dl` 硬夹保护，与运行结果无关；
   申报窗口本身合得下（worst 46 s + 预留 7 s ≤ 90 s）。消红只有减步或放宽总窗两条路，A 已明写
   不许放宽 90 s，本轮也不自行减 §5 的步骤。

两条都写进了计划自己的 `static_budget_open_items`（带数字与三条候选出路），所以审核者在计划字节
里就能看到红在哪，不用翻工具输出。

### 24.5 本轮自伤（照单登记，不改写历史）

1. 整包离线门最初用 `--rows` 覆盖行表 —— CLI 没这个选项，argparse rc=2，八条行为判据一起变成
   "没有回执"。改成把每例的 plan.json/rows.json 写进本例目录（执行器只认 `plan.rows_file`，
   相对计划文件解析）。
2. 改完之后仍然八条全 `code=None`：`--dispatch` 的最后一行不是 JSON，阶段结果在
   `DISPATCH|stage=S1|status=…|kind=…|<json>` 的第 4 段里。上一版的 `last_json(stdout)` 永远取到
   空 dict —— 这是工具缺陷，被审件当时已经跑完了整场。
3. 整包 dispatch 的阶段回执**不带** F5b/F5c 的预留/夹短字段（只在会话结果里），所以现场那一条
   路（dispatch）看不到"7 秒到底留没留住"；已由 F6b 把四个字段搬进阶段载荷。
4. `build_session_fixture` 只给电机三条兜底喂行，IRQ 尾部两条没有来源 => 整包 stub 永远判不到
   "尾部发出去并确认 CLOSED"；F6c 补成"行表带这个键才注入"，历史包字节行为不变。
5. 申报过的响应序列被按**命令键**取用，导致同键的非轮询步骤（§5 行 10）读到序列第一份 ACCEL 行
   而当场停止；F6d 限定只有申报了 poll 的步骤才用序列。
6. 现场包的爬坡行是我用 `MOT_CRUISE[0].replace("CRUISE","ACCEL")` 拼的，得到
   `state=2 name=ACCEL` —— 名字改了、数值没改，ramping 门按数值判"还在爬坡"就看不到 ACCEL。
   已按 `motor.h` 的枚举（ACCEL=1）逐字重写。
7. `safety_irq_disable` 的行我填成 `:290` 的 "IRQ gate stays CLOSED"（attach 失败回滚路径），
   产品实际走 `:306` 的 detach 行；`IRQ_CLOSED` 常量里路数也写成 0，而 `st_inputs[]` 在
   `SAFETY_LIMIT_INPUTS_ENABLED=RT_FALSE`（project_board.h:51）下只剩 TMC_DIAG 一路 => 1 路。
   矩阵与重放的同一处夹具一并改正。
8. 取消场没申报 `job_contract.phase`，默认 "done" 不允许 CANCELLED，整包程序在第 26 步
   `JOB_CONTRACT_VIOLATED` 停 —— 计划作者化时的漏项，已补 `post_cancel`。
9. 一次编辑把离线门里 `FIELD_PLAN_PREREQ_NOT_SELF_CERTIFIED` 的参数行删掉（语法坏），另一次把
   变更表工具里的三元表达式连同引号一起改掉（打印出乱码字面量）。两处都在本轮内修好并重跑。
10. 上一轮留下的远端回读工具把"合并件正文应逐字承接 A 的修订快照"钉在 R7F8E 那份快照上；
    本轮按 A 的 R7F8F 活草案重新合并之后，这条判据假红（正文 290 行 vs 旧快照 272 行）。改成
    比对**当前**来源 `inputs/A_R7F8F_PKG/R7F8_LIVING_DRAFT.md`，旧快照仍在回读集合里保身份。

11. 我自己的畸形行表突变（删掉整个 `healthy` 分支）让执行器在**开端口之前**抛裸 KeyError 带
    traceback 退出。这是继承自冻结件的既有行为，任务书没列这项，本轮不改语义，只在整包门里登记
    成 `ROWS_BRANCH_MISSING_KNOWN_GAP`（保留 rc=1 与 traceback 原文），交 A 定要不要单开一轮。

12. 把清单的回执排除改成"按文件名族"之后，排除口开得过大两处：`tools/r7f8e_remote_readback.py`
    （被交付的代码）被同一条规则排出清单，覆盖面判定当场报
    `未覆盖=1 ['…/tools/r7f8e_remote_readback.py']`；把口子收到"本轮 `proof/` 下的 .txt"之后，
    又发现 `proof/r7f8e_remote_readback_first_attempt.txt`（`e97aaa4` **已提交**的判红原文）也被
    挤了出去 —— 在库证据没有清单条目等于覆盖面判据自己漏了一格。最终口径：只免
    **本轮尚未进 Git 的那一份 proof 回执**（推送之后才存在，清单不能依赖自己交付之后的产物），
    已提交的回执与工具件照常入列；生成器与预检用同一条规则，并给新判据加了元负控
    （抽掉一份在库回执 => `在库回执未列` 必须命中）。`DRAFT` 与 `R7F2B` 沿用各自原有的按文件名
    排除，不受影响。重算后条目数 80/8/228。

13. 离线门的机器回执 `proof/offline_gate.json` 用 `l.split("|", 2)` 按位切文本行来还原
    门名/判定值/详情，但门名本身就允许含竖线（`GATE_PRESENT|PLAN_CLEANUP_RESERVE`、
    `FIRST_ERROR|tail_silent`），于是 **13 条把门名写进了 verdict 字段**（如
    `verdict="PLAN_CLEANUP_RESERVE"`），`verdict != "PASS"` 的机器筛法会把这些误当成未通过。
    已改成在 `note()` 记账时就分开三个字段，并新增第 40 条判据 `JSON_RECEIPT_SHAPE_ALIGNED`
    （结构化条数=文本行数、判定值只会是 PASS/FAIL、FAIL 名单与汇总行一致），同输入重跑后
    40 条的结论与补正前逐条一致（仍 FAIL=2，名字未变）。变更表工具当场把这条新判据拦了一次
    "新增没有逐项理由"，补进 `REASONS` 后 `FIXTURE_TABLE|族=5|problems=0`。

14. 给预检白名单加本轮两件新交接件时，我把它们**插在元组中间**，当场触发
    `ALLOWED_LIST_NOT_PARSEABLE_EXACTLY` —— 那条判据除了"解析出的字面量集合 == 元组"，还按
    **位置**核 `ALLOWED[0]/[3]/[4]/[5]/[6]` 与常量同步，中间插入会把位置整体挪一位。已改成
    一律追加到末尾并在源码里写明这条约束（判据本身不动，位置口径是它自己定的）。

15. 整包离线门的 `PRE_RULING_REFERENCE_PLAN_STILL_RED_UNCHANGED` 里，"仓外副本与原件逐字节相等"
    写成了 `hashlib.sha256(A) == hashlib.sha256(B)` —— 比的是**两个哈希对象本身**（没有
    `.hexdigest()`），恒为 False。也就是说这条判据从写下那天起就带着一格死码，无论基准件怎么
    对都判红。是本轮把它拆成 6 个合取项逐项打印才暴露的（上一版的详情只贴 SPLIT/WORST 两行，
    看不出红在哪一格）。修好后新增第 47 条元负控 `PRE_RULING_BYTECMP_CLAUSE_FIRES_ON_MUTATION`：
    原件副本必须 True、尾部多一个空格的副本必须 False，True/False 配对才允许这项存在。
    **教训**：复合判据不许只报总判定 —— 合取项要逐项记账，且每一项都要有一次能翻的证据。

16. `tools/r7f8f_budget_package_checks.py` 的基准件身份判据拿 A 的基准 plan **与工作树里的现场包
    文件**逐字节比。裁定一落地，计划必然改动，这条就当众报红（`FAIL=1`）—— 它证的根本不是
    "A 钉的是哪份字节"。改成按 Git blob 复算（`git show 6f2b12f:<path>`），判据改名为
    `REFERENCE_BYTES_EQUAL_6f2b12F_BLOB`，并把"工作树现状"两格（plan 已改、rows 未改）写进详情，
    不藏这次改动。11 条重跑 FAIL=0。

17. `tools/r7f8e_no_regression.py` 的本轮新门点名表停在 3 条，裁定新增的
    `PLAN_BUSINESS_RESERVE_SPLIT_LEGACY_SEMANTICS` 与 `PLAN_STEP_TOTAL_WINDOWS` 会被当成
    "不是本轮适配自己的门"。补进点名表（工具自带的"点名的门确实出现在新增行里"反过来兜住
    白名单空转）；9/FAIL=0。

18. `tools/r7f8e_merge_draft.py` 里两处上一轮的硬编码让它拒绝为本轮服务：
    (a) "上一版合并件 = 36273 B / `e9a5253de024`（提交 `d6bbc1b5795f`）" —— 那是 R7F8E 时的值，
        现件已是 40289 B / `bc3710e9917c`；先按 blob 核过（`git show c9ad704:<path>` 与盘上字节
        sha 相同）才把常量改过来，不是"改到能通过为止"。
    (b) 合并记录节把承接来源写死成 `R7F8_DRAFT_UPDATED.md`，并写着"本轮不生成 REAL_GO 计划,
        不编入真实端口/UID" —— 本轮**确实**有可 dispatch 的现场包（只等操作者一次批准），这句
        现在是不实陈述。改成 `--updated`/`--label` 当场填路径与轮名，并把三件交付物的
        bytes/sha 从盘上重算写进记录节。
    另外裁定新增的 13 条判据（矩阵 K1~K8 + 离线门 5 条）在逐项变更表里没有人写理由，被
    `r7f8f_fixture_change_table.py` 判红 13 条 —— 补理由后 `problems=0`（这条拦得对：新增判据
    必须说清为什么存在）。

19. 运动 cap 结束点改到"可信零速快照"之后，我把 §4"正常 stop 必须在起转后 8 s 内发起"写成了
    **无条件**判据，于是 stub 整包那套合成夹具（它自己的场景把 cap 放大到 62 s、从没申报这条
    业务规则）被判红：`29/FAIL=2`。收到"阶段申报了才判"，并让现场包在阶段里显式申报
    `normal_stop_send_by_seconds: 8` —— 规则本身没动，动的是"谁声明谁被核"。

20. 远端逐 blob 回读里"合并件正文逐字承接远端那份修订快照"这条，比对对象写死成
    `inputs/A_R7F8F_PKG/R7F8_LIVING_DRAFT.md`（裁定前那包的 290 行活草案）。裁定包入库后合并件正文
    是 304 行，这条当场报 `正文行=304 快照行=290` —— **红了，但红的原因是"比对对象过期"，不是
    "承接断了"**（本地 `HANDOFF` 那条同源判据仍是 equal=True）。首次判红原文照录留在
    `proof/r7f8f_remote_readback_49a4a39.txt`（不覆盖、不补造）。修法：承接源从**远端合并件自己的
    记录节**读那一行 `- 承接来源: <路径>`），读不到那行就判红并点名"不许退回写死的路径" ——
    与交接 json 里"上一版合并件身份从合并件自身读出"同一手法，把这类漂移一次性堵掉。

21. 把清单说明行的件数改成"当场数盘上文件"之后，`proof` 那一格**没跳过推送后才存在的回读回执**：
    第二次回读(04ddc38)落盘一个新回执，再跑一次生成器，清单头就从 `proof 10 份` 变成 11 份，
    于是**已交付的清单字节被自己交付之后的产物改掉了** —— 这正是 §24.5 第 12 条花大力气堵掉的
    自指回路，从"条目集合"挤到"说明行"里又来了。修法：说明行的 proof 计数复用同一条排除规则
    （本轮 `proof/` 下、文件名含 `remote_readback`、且还没进 Git 的 .txt 不计），修完生成器幂等。
    这条是 04ddc38 推上去之后当场复跑发现并当场修的，未回改已交付的旧字节。

22. 现场包的 `sys_pre`（`system_status`）**漏了 `status_lines_allow` 申报**，而产品
    `supervisor.c:126` 无条件打门位评估行（`motor.h:92/93` 的 bit3|bit4 = 0x18 就是本会话第 1 步的
    正常前态）=> 真机第一次开口就在第 1 步被判业务失败停止。现场事实与逐条判读见 §25；
    电机从未带电，收口五条无需发。修法不在本轮自行放宽判据：要么在承接件里给该查询步补上
    **整行锚定**的披露申报（并给起转类步骤明令禁止这条申报），要么请 A 判产品把"当前会被拒"
    的门位评估换个不带 REFUSED 字样的措辞 —— 本轮按 §3.7 停下报告具体差异，等 A/操作者定。

23. 这条更难看也更要紧：**47 条离线门全绿没抓到上一条**，因为 stub 回灌的是我自造的 rows，
    而那份 rows 把 `mask=0x18` 写在了 TMC 写门行、板子把它打在 Motor 门位评估行 —— 替身替我
    把错处圆过去了。已在 §25 登记行表与实拍的三处字段差异（`irq_gate` 大小写、`polarity` 值、
    `protect_ready` 值），并把"用板子真字节回放 assert_step"列为下一步必须补的判据。

### 24.6 口径更正两件

- 远端回读覆盖失败那一次的**首次失败原文未留存**（被第二次运行的输出覆盖了同一个文件）。
  本轮起每次输出写独立文件，不补造旧记录。
- A 包的清单口径：任务书 zip 内登记 13 件，其中 `SHA256SUMS.txt` 自身不含在清单里，所以
  "清单列出的其余 12 件"才是可比对集合。早先我曾把这一族数成 23 件（把包外 proof 与 tools 也算
  进去），该误算原因已登记；A 的原清单未回改。

### 24.7 停点

本轮交接件：`reports/agent_handoffs/phase8-m8-r7f8f-repair-and-offline-gate.md` + 同名 `.json`
（机器那份由 `tools/r7f8f_make_handoff.py` 当场从盘上算数字，含 `HANDOFF_CROSS_CHECK` 一条
自证：json 判据条数=文本条数、红名单同源、判定值只会是 PASS/FAIL；找不到指定判据名直接拒产）。

工具关键修复已验证（四族判据 + 整包门 + 逐项变更表全绿，除两条已点名的静态预算红）。
**完整现场包尚未就绪，还不能称为现场验收通过**：上机前需要先有 A 对 §24.4 两条的口径答复。
设备动作为 0；一次现场批准仍未取得（未取得就不发任何现场命令）。

### 24.8 A 的预算裁定落地（同轮后半，仍 0 设备动作）

输入是操作者会话附件 `NS800_R7F8F_A_Budget_Ruling_20261105.zip`（42428 B / zip sha256
`87c9d5ea76b3c008…`），解出 10 件入 `inputs/A_R7F8F_BUDGET_PKG/`（含 A 自己的
`SHA256SUMS.txt`，登记 9 件、不列自身）。裁定承接 `6f2b12f` 的交接 §3 与 §24.4，明写"不另开
离线任务、不增加现场动作、**不再另开登记轮或等 A 签第二次软件章**"，所以本轮把它的两条语义
一次并入执行器、计划、判据与交接：

- **裁定一（运动计时的结束点）**：从 `motor_start` 写入尝试起，到本次正常 `motor_stop` 之后
  第一份**新鲜、完整、可信的零速快照**为止；stop 回话本身不算。落地形状：
  `zero_speed_contract()`（`state/cur_hz/tgt_hz/health` 全零全 OK + armed/请求/pad/ENN 预期·
  反馈·verdict 自洽；`CUT_BY_HARDWARE` 与残缺快照不算）+ `motion_end_gate()`（必须本场已尝试过
  唯一一次 start 且随后 stop 成功；证据取自**同一份**响应）；超过 12 s 才到的零速具名
  `MOTION_ZERO_CONFIRMATION_LATE`，第二次确认具名 `MOTION_END_ALREADY_CONFIRMED`；
  `MotionBudget.t0` **不清**（不许靠清 t0 放行第二次 start）。台账新增
  `MOTOR_STOP_CONFIRMED(ends_motion_timer=False)` 与 `MOTION_TIMER_ENDED(arm_deadline_kept=True)`。
  arm 的 18 s 通电计时与业务 83 s 截止都**不随**零速确认放开（K5/K6 专门证这条），零速之后的
  commit/show/ps/free 改由 `now+预留7` 起算、仍被会话 90 s 封顶。
- **裁定二（单步总窗）**：计划新增申报 `step_total_seconds`，该值就是这一步的**整窗**（等待、
  回读、余量都在内），由同一个 `step_window()` 供执行器、计划门、镜像与仿真使用；不再逐步叠加
  `SLACK=3`。非法申报（bool / 非数 / 非正 / nan / inf / until>总窗）在**开端口之前**逐条具名拒绝
  （`STEP_TOTAL_SECONDS_INVALID`、`POLL_UNTIL_EXCEEDS_STEP_TOTAL`）；未申报的旧计划仍按
  `seconds+3` 的 legacy 规则跑（冻结的 R7F7 件一字未动，`PLAN_BUSINESS_RESERVE_SPLIT_LEGACY_SEMANTICS`
  只留档不判红）。
- 计划阶段另显式申报 §4 那条业务规则 `normal_stop_send_by_seconds: 8` —— 谁声明谁被核，合成夹具
  没声明就不拿现场值判它（§24.5 第 19 条）。
- 镜像三段改名并按 0 基下标点出结束位置：`pre_start`(19 步) / `motion_active`(11 步，含 stop 与
  stopped) / `post_zero`(8 步)，`stop_request_index=28`、`zero_confirm_index=29`。行表的停机位
  从"初态禁能快照"换成产品真实的**已零速但仍带电**形状（`motor_status_armed_idle`：armed=1、
  drv HIGH/pad=1、expected_ENN=LOW、ENN_fb=0、resolved、verdict=OK）。

裁定 §3 那张表不是抄进计划里的常数：执行器现算 == 计划镜像 == 检查器读数三处同一函数产出，
K7 逐格核对 31.0 / 9.0 / 7.5、合计 47.5、stop@7.5、零速@9.0、禁能@13.0、保守式 57.5 与 16.0，
与 A 表完全相同（这些是**窗口分配与计时合同**，不是硬件实测耗时，计划与交接都这么写）。

### 24.9 裁定落地后的判据与现场包（现在的数）

- 派生执行器 `tools/m8r3_serial_chain_r7f8.py`：**312387 B / sha256 `093c8408a019…` / 5480 行**，
  配方 `tools/r7f8f_build_executor.py` 现 **70 处 edit**，`patches/r7f8f_executor.patch`
  92610 B / `058a75f153e5`；`--verify` 在仓外重建逐字节相等 = True。修前件与各冻结件一字未动
  （`FROZEN_*` / `PRE_EXECUTOR_BYTES_FROZEN` / `PRODUCT_SOURCE_UNCHANGED`）。
- 反例矩阵 `proof/r7f8e_case_matrix.txt`：**106 / FAIL=0**（98 → 106，新增的是裁定 §5 六项对应的
  K1~K8 进程内探针；K 组必须在进程内跑，因为它们核的是"同一时刻哪个截止还生效"这种运行时态）。
- stub 整包 **29 / FAIL=0**；无回归 **9 / FAIL=0**（旧件 57 项 fail=1、派生件 67 项 fail=1，
  唯一既有 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`）；三版本重放 **21 件 × 3 侧 / FAIL=0**。
- 整包离线门 `proof/r7f8f_offline_gate.txt` + `offline_gate.json`：**47 条 / FAIL=0**
  （40/FAIL=2 → 47/FAIL=0；新增 5 条中与裁定直接相关的是
  `RUN_ZERO_SNAPSHOT_RELEASES_MOTION_TIMER`、`RUN_MOTION_TIMER_ELAPSED_WITHIN_CAP`、
  `PRE_RULING_REFERENCE_PLAN_STILL_RED_UNCHANGED`、`PRE_RULING_BYTECMP_CLAUSE_FIRES_ON_MUTATION`、
  `LEGACY_RED_TEXT_KEPT_VERBATIM`）。validate-plan 对现场包：**checks=146 / fail=0**。
- **两条红的转绿是语义落地，不是把阈值放宽**：A 包里的裁定前基准件
  （`reference_plan_6f2b12f.json` 28090 B / `66ca8a15…`）原字节复制到仓外重跑，仍然报
  `zero@None` + `PLAN_BUSINESS_RESERVE_SPLIT|S1|FAIL` + `PLAN_BUSINESS_WORST_CASE_FIT|S1|FAIL` +
  旧口径 91.0/23.0；91/23 的原文照录留在 `LEGACY_RED_TEXT_KEPT_VERBATIM`。§4 的 12/18/90/7 四个数
  一字未动（`LIMITS_UNCHANGED`），没有负 `seconds`，没有减步骤。
- 现场包：`field/plan_r7f8f_2369_real.json` **31712 B / `eef417ce3152…`**（38 步、逐条带
  `step_total_seconds` 与三段镜像；由 `step_total_problem` + `(as,cmd)` 双核对 A 表的
  `WINDOW_TABLE_REFUSED` 门把守）；`field/rows_r7f8f_real.json` **8168 B / `4022306df3ad…`**
  字节未变。镜像参照仍是盘上 297452 B / `23691e8d…`（本轮不下载/不回读/不复位）。
- 裁定包独立复算 `proof/r7f8f_budget_package.txt|.json`：**11 / FAIL=0**（A 清单 9 件逐条重算、
  A 自己的 `budget_check.py` 在我这台机器上真跑并与 A 的 `PROOF_budget.json` 对账、三条等价突变
  负控、基准件按 `6f2b12f` 的 Git blob 核）。
- 逐项变更表 `proof/r7f8f_fixture_changes.md|.json`：基线 `42d7f7d` 五族逐名对齐，
  矩阵 65→106、stub 27→29、无回归 32→37、重放(7 个判据名)与整包离线门(39 个判据名)为本轮新增
  文件，**removed=0**、同名判据无结论翻转、新增逐条带人写理由，`problems=0`。

### 24.10 活草案重合并

A 的裁定包内 `R7F8_LIVING_DRAFT.md`（38793 B / `14bafe13a8cd`，304 行，含新写的 §7.3
"预算定稿补充"）已重合并进
`reports/reviews/phase8-m8-r7f8-closed-loop-draft/R7F8_DRAFT_MERGED.md`：新件 **41144 B /
`db031f8aa5f3`**，`INHERIT_EXACT|equal=True|正文行=304|快照行=304`（剪掉记录节后与 A 的原件按行
逐字节相等）；上一版合并件 40289 B / `bc3710e9917c`（提交 `c9ad704`）不回改，历史身份由 Git blob
承担；`A_DRAFT_ORIGINAL.md` 24780 B / `b572c1928f60` 一字未动。合并记录节现在当场重算并写出三件
可 dispatch 交付物的 bytes/sha，且明写 DRAFT_NOT_AUTHORIZED：合并件本身不是 dispatch 对象，
上机仍需操作者**一次**整批准。

### 24.11 停点（裁定落地后）

§24.4 那两条的口径已由 A 给出并落地，整包离线门 **47/FAIL=0**、validate-plan **146/fail=0**、
四族判据全绿、逐项变更表 problems=0 —— 对应裁定末段那句"**不再另开登记轮或等 A 签第二次软件章**"
（原文在 `R7F8F_A_BUDGET_RULING.md` 第 83 行，§5 之后）。
**但这不等于现场验收通过**：本轮设备动作仍为 0，批准账未开，
`polarity_measured=false` / `core_protection_verified=false` 保持原值；一次实机整包执行
（F11）只等操作者对"R7F8F 及本预算裁定"的那句真实原话。令牌取自操作者回复，不由 agent 自造，
也不借上一轮的批准。

## 25 M8-R7F8F F11 真机一次整包（2026-11-05 21:23，COM5 唯一一次开口 → 第 1 步首错停止）

操作者给出真实原话令牌「执行 F11 整包实机验证」（同时逐字确认 §4 机械/电气前置已核对、COM5 空闲），
按裁定末段直接接同一包的真实会话。承接件 = `field/plan_r7f8f_2369_real_dispatch.json`
（32832 B / `53c744213964…`）：与已交付原件 `eef417ce3152…` 的差别只有 9 个键（机械前置记 PROVIDED
并写明来源=操作者原话、status 改名、approval 里记令牌与账本位置），38 步/额度/三段预算一字未动。
`--approval-init` 先离线开账（绑三件身份：executor `093c8408a019…` / plan `53c744213964…` /
镜像 `23691e8d…`），随后**一次** `--dispatch --transport real --backend real --profile response_driven`。

- **结果 = 首错停止（不是通过）**：`status=STOP code=BUSINESS_FAILED as=sys_pre`，触发行
  `[Motor] arm gate fail mask=0x18 (arm REFUSED)`；会话台账
  `PORT_OPEN_ATTEMPT → PORT_OPEN(COM5,115200) → RX_DONE(983 B, nul=0, prompt_seen=true,
  raw_sha256=73abf3012386…) → BUSINESS_OUTCOME → SESSION_STOP → PORT_CLOSED`，全程 0.156 s。
- **板子回的是真字节，且回显证明只写出去一条查询**：捕获开头就是 `system_status\r\n` 的回显，
  全文只出现这一条命令回显；`motor_arm / motor_start / safety_irq_enable / tmc_config_apply /
  diag_profile_*` 一条都没写出去。**电机从未带电、从未起转**，收口五条因此无需发（义务未建立）。
- **停止那一刻的板态（逐行取自原始捕获，非我的结论）**：`[Safety] state=READY fault=0
  irq_gate=CLOSED polarity=not-confirmed protect_ready=NO`；`request=LOW pad=0 expected_ENN=HIGH
  ENN_fb=1(resolved) verdict=OK`；`[Motor] state=0 cur=0 tgt=0 armed=0 drv_en_verified=0
  health=OK`；`[TMC] OK drive_cfg=UNCONFIGURED`；`[ADC] DEGRADED cal=THEORETICAL`；
  `[Diag] OK verdict=0 mode=MONITOR_ONLY`；`[UI] led/buzzer output=SAFE-OFF`。
- **根因在包侧，不在板上，也不在产品**：`supervisor.c:126` 在 `system_status` 里**无条件**打印
  门位评估行，`motor.h:92/93` 给出 `mask=0x18 = MOT_GATE_NOT_PROTECTED | MOT_GATE_NO_DRV_CONFIG` ——
  即"这次开机还没开 IRQ 门、TMC 参数还没下发"，正是 §5 第 1 步本来就该看到的前态（开 IRQ 与
  apply 都在后面）。而执行器早就提供了对应的申报口 `status_lines_allow`（整行锚定的状态披露行，
  不参与业务失败扫描），我给 `sys_pre` 那一步**没写这条申报** => 被业务失败线判停。
- **为什么 47 条离线门没抓到它（这条最要紧，别当运气好）**：stub 整包回灌的是我自己造的
  `rows_r7f8f_real.json`——那份行表把 `mask=0x18` 写在了 **TMC 写门**那一行，而板子把它写在
  **Motor 门位评估**那一行；假端口按我的行表回放，于是"包侧缺申报"这件事在离线里永远测不到。
  这正是本仓已登记过的老毛病（注入替身必须覆盖装配层，单元假端口全绿≠现场能跑）。**现场第一次
  真开口就是它的第一个证据**。
- 行表与现场实拍的三处差异一并登记（同一条捕获）：`irq_gate=CLOSED`（我写 `closed`）、
  `polarity=not-confirmed`（我写 `HARDWARE-PENDING`）、`protect_ready=NO`（我写 `YES`）——
  我当时只回源码核到"形状"，没核到这三个字段的实值，`field_assertion_gaps` 里已声明只断言前缀，
  没有据此判过任何东西。
- 额度与纪律：`quota_used={"port_open":1}`，其余（含 `retry/download/readback/explicit_reset`）
  全为 0；账本 `pending=0 commit_failures=[]`；**没有第二次开口、没有重试、没有重启、没有断电**；
  设备按 §4 保持通电（`[UI] SAFE-OFF`、`armed=0`）。产品树与固件一字未动
  （`PRODUCT_TREE_UNTOUCHED_AT_STOP`=PASS）。
- 证据件（新工具 `tools/r7f8f_make_field_evidence.py`，数字全从落盘字节复算，11 条 FAIL=0）：
  `proof/r7f8f_field_f11_sys_pre.rx.bin`（983 B 原始字节，目录 `-text` 保行尾）、
  `…rx.txt`、`…ledger.jsonl`、`…dispatch_ledger.jsonl`（仓外路径掩成 `<WORK>`，掩码本身有判据）、
  `…receipt.txt|.json`。
- 整包离线门的同名判据因此改名收窄：`NO_HARDWARE_ACTIONS_THIS_ROUND` →
  `NO_HARDWARE_ACTIONS_INSIDE_THIS_GATE`，并在详情里明写"本条不声明整轮无设备动作"——
  现场那一次的动作与额度由本 §25 与 `field_run_f11` 块承担，两者不许混用。

## 26 M8-R7F8F F19 包侧披露申报 + 真字节回放（2026-11-05 深夜，离线，设备动作 0）

F11 那次停止的根因判定为**包侧缺申报**（§25），操作者给了两条处置：修法走「补包侧申报（不动冻结
源码）」，离线盲区走「必做：真字节回放三条」。本轮按这两条落地，产品树/固件/旧证据一字未动。

### 26.1 包侧申报（只加在两条 system_status 查询步）

- `sys_pre` / `sys_after_apply` 各加：`status_lines_allow` 一条**整行锚定**的披露形状
  `^\[Motor\] arm gate fail mask=0x[0-9A-Fa-f]+ \(arm REFUSED\)$`，并把 `[Motor] arm gate fail mask=`
  写进 `expect_contains` 作正面要求；`sys_pre` 另加 `[Motor] state=… armed=0 health=OK` 那行的硬字段判据。
  全 38 步里只有这两步申报豁免（`L6` 逐字核）。
- 依据是冻结源码，不是我改判据：`supervisor.c:126` 在 `system_status` 里**无条件**打这一行
  （`gates ? "arm REFUSED" : "all gate conditions pass…"`），`motor.h:92/93` 定 bit3/bit4；
  现场前态实测 `0x18` = 两位同置。mask=0 时该行不含 REFUSED，因此不匹配豁免形状，也不需要豁免。
- 同一轮把 `SYS_READY_APPLIED`（第 7 步切片）三处臆造值改回源码事实：`irq_gate=CLOSED`
  （产品只打 OPEN/CLOSED，我原来写小写 closed）、`polarity=not-confirmed`（我原来写
  `MARKED-VALIDATED`，产品没这个 token）、`protect_ready=NO`（safety_thread.c:315 要
  irq_attached 且 polarity_validated，而第 10/11 步都在第 7 步之后）。该行 mask 值 `0x8` 是
  **推导**（现场 0x18 减去 apply 成功后清零的 bit4），`_about.derived_shape_rows` 里明写它不是实测。
- 派生件里补了一条运行期门（recipe 第 71 项 `G4_STATUS_ALLOW_MUST_BE_EXPECTED`）：被放行的每一行必须
  含本步 `expect_contains` 的某个字面片段，否则具名停 `STATUS_ALLOW_NOT_POSITIVELY_REQUIRED`。这是把
  R7F8E 起就写在注释里、但**代码从未核过**的那句话兑现成判据。

### 26.2 静态与运行期同一口径（新门 G5 两项）

`PLAN_STATUS_ALLOW` 的 PASS 话术一直写着「锚定且被正面要求」，静态代码却只核了锚定/查询步/硬字段三条
—— 计划能过静态门、现场被运行期停住，正是 F11 的形状。本轮补上第四条：豁免模式的**字面前缀**必须
被本步 `expect_contains` 命中（前缀是模式的确定部分 ⇒ 任何 fullmatch 行必含它 ⇒ 运行期那条门静态上
就不会翻红）。新增函数 `status_allow_literal_prefix`（recipe 第 72/73 项）。

**这一条我自己先写错了一次，被无回归族当场抓住**：初版把「反斜杠+空格」也当通配，于是冻结的 R7F7
计划（整条模式用 `\` 转义空格）被截成前缀 `[DIAG]`，`no_regression` 从 9/0 掉到 9/4、整包离线门 8 条
红。修成"只有字符类/断言/反向引用算通配"后两族复原（R7F7 计划仍是绿的 ⇒ 新门没有把历史件改红）。
登记进 §24.5 风格的自伤清单见 §26.5。

### 26.3 真字节回放 FB 族（7 条，输入是板子发的 983 B，不再是我写的行）

`r7f8e_case_matrix.py` 新增 `field_bytes_probes`：把 `proof/r7f8f_field_f11_sys_pre.rx.bin`
（983 B / `73abf3012386…`）经**生产读路径** `read_until_prompt` 解析后喂 `assert_step`。

| 条 | 形状 | 读数 |
| --- | --- | --- |
| FB1 | 落盘字节与捕获逐字节相等，披露行确在流里 | PASS（21 行） |
| FB2 | 正例：交付申报下 verdict=OK，台账 `status_lines_allowed` 逐字带原文 | PASS |
| FB3 | 反例：同一段字节喂 `motor_arm`（未申报）仍 `BUSINESS_FAILED` | PASS（豁免按步） |
| FB4 | 元负控：摘掉豁免 → 同一份字节翻回 `BUSINESS_FAILED` | PASS（豁免是承重的） |
| FB5 | 反例：删正面要求保留豁免 → 运行器具名 `STATUS_ALLOW_NOT_POSITIVELY_REQUIRED` | PASS（台账 1 条） |
| FB6 | 静态：交付计划 `PLAN_STATUS_ALLOW` 绿，且豁免只在两条查询步 | PASS |
| FB7 | 静态元负控：只删正面要求 → 同一道门翻红并写字面前缀 | PASS |

### 26.4 六族 + 计划 + 派生复算（现在的数）

矩阵 113/0 · stub 整包 29/0 · 无回归 9/0 · 整包离线门 47/0 · A 包预算复算 11/0 · 三版本重放 21/0 ·
夹具变更表 族=5 problems=0 · `--validate-plan` 146 项 fail=0（交付计划与 F20 草案件各一次）。
派生件 `tools/m8r3_serial_chain_r7f8.py` 315233 B / `30c67a3e8dbe…` / 5525 行（recipe 73 项，
`--verify` 仓外重建逐字节相等=True）；补丁 `patches/r7f8f_executor.patch` 96676 B / `ecbf6c37f748…`。
现场包：plan 33140 B / `e78519421149…`（与 F11 那一版同一身份，本轮只动 rows）；
rows 10060 B / `c64fdee847af…`（唯一变化 = 披露行 + 三处源码事实）。

### 26.5 本轮自伤与新增工具（照单登记）

1. `status_allow_literal_prefix` 初版把转义空格当通配 ⇒ 冻结 R7F7 计划假判红（9/4、47/8）。
   由**无回归族**抓到，不是靠我重读代码。
2. `r7f8f_fixture_change_table.py` 的 REASONS 里嵌入英文双引号导致 Python 字符串提前终止
   （L 族 7 条理由写完即语法错），改用中文引号后过 AST 检查再跑。
3. 往执行器 recipe 里插 helper 时用了 `"""` 文档串嵌在 `"""…"""` 里 ⇒ 派生脚本自身语法错；
   换成 `#` 注释。**同一类**上一轮已犯过一次（heredoc 把 `\r\n` 写坏）。
4. 新工具 `tools/r7f8f_make_dispatch_copy.py`：F11 那份承接件是我**手工编辑**的，计划身份一变就得
   重抄，抄错一格等于把批准绑到别的字节上。改为派生：只允许那 9 格差异，写完立刻扁平 diff 复算，
   多一格或少一格都 `REFUSED|`；令牌/批准 id 不从命令行给就只产出草案（`approved=False`）。
5. 新判据族一开始起名 `L1_`~`L7_`，与矩阵里**已有**的清理族 `L1_`~`L4_` 撞号（交接工具按名字数 L 族，
   数出 11 条 => 我加的 `HANDOFF_REFUSE|…L 族判据数` 当场拒绝，才去 grep 看到旧族）。全部改名 `FB1_`~`FB7_`
   并重跑矩阵 113/0 + 夹具表 + 交接。**教训**：新增判据族前先 grep 名字空间，别用第二个字母表头。

### 26.6 F20 承接草案（未批准，本轮没开第二个账）

`field/plan_r7f8f_2369_real_dispatch_f20_draft.json` 34898 B / `e59272b6fa88…`：由新工具从
`e78519421149…` 派生，9 格差异，`status=FIELD_DISPATCH_DRAFT_NOT_APPROVED_NEW_PLAN_IDENTITY`，
`approval.token_verbatim=NOT_ISSUED_等待操作者对新身份的真实原话`。机械前置那格沿用操作者在 F11 会话里
逐字给的**事实声明**并把来源写成"承 F11 那一次核对，未重新核对"；它**不是**新一轮批准。
F11 那份承接件按字节冻结在库里不回改（`53c744213964…`，与 §25 的证据链一起保留）。

停点：本账 `port_open` 额度 1 已用满（§25），第二次真机整包要操作者对**新身份**
（plan `e7851942…` / rows `c64fdee8…` / executor `30c67a3e…` / 镜像 `23691e8d…` 四件）给一句新的真实
原话。本轮设备动作 0：未开口、未写任何字节到 COM5、未建第二个批准账。
`polarity_measured=false` / `core_protection_verified=false` 保持原值；仍不是现场验收通过。

## 27 M8-R7F8F F20 第二次真机停点 + F21 门位全量契约 / 真字节纳入 / 令牌门（2026-11-05 深夜～11-06 凌晨，设备动作 0）

操作者批准了 F20 那一次整包（四件身份 + 本轮逐字机械前置声明在同一句里）。跑完的结果是
**第二条只读查询停止**，随后 A 给了三条补齐要求（全量 mask、历史真字节 + 跨计划回归、令牌字节
单独判未通过）并明确 F21 沿原范围/原时限/动作上限、旧账不结转、无需另开登记轮。本节先记现场，
再记三件补齐。产品源码、固件、旧证据、F11/F20 两本账全部一字未动。

### 27.1 F20 现场读回（一次开口，两条只读查询，电机从未带电）

| 项 | 实测 |
| --- | --- |
| 第 1 步 `sys_pre` | **通过**（983 B / `73abf3012386…`，与 F11 那份逐字节相同）——F19 的披露申报在真机生效 |
| 第 2 步 `mot_pre` | `code=BUSINESS_FAILED as=mot_pre`，命中行 `[MOT] arm_gate_mask=0x18 REFUSED`（1128 B / `f0550d313164…`） |
| 之后的配置/学习/运动 | **一条都没发**（`DISPATCH_DONE` 里 `not_run=["S1"]`） |
| 收口 | 固定收口照做，`disarm=NOT_ARMED`、`terminal=KNOWN`；电机从未 arm/start，板子保持禁能、保持通电 |
| 额度 | 该账 `port_open` 1/1 用满，`retry=0`，未开第二个口、未复位、未下载 |

同一类错在两个命令面上各暴露一次：F11 是 `system_status`，F20 是 `motor_status`。R7F7 定稿计划
本来就带着 `^\[MOT\] arm_gate_mask=0x18 REFUSED$` 这条申报，我重建 R7F8F 计划时把它丢了。

### 27.2 F21-a：门位 mask 逐步契约（8 处 motor_status + 2 处 system_status，不是手工数几处）

步骤表里两类查询步共 **10 条**，逐条钉预期 `arm_gate_mask`（表随计划一起交付，字段
`arm_gate_mask_contract_steps`；REAL=该形状在仓内归档真实字节里逐字存在，DERIVED=源码 + 前态推导）：

| 步 | cmd | 预期 mask | 出处 | 豁免 |
| --- | --- | --- | --- | --- |
| `sys_pre` | system_status | `0x18` | REAL（F11/F20 983 B 两次实拍） | 有（锚定 `0x18`） |
| `mot_pre` | motor_status | `0x18` | REAL（1128 B/`f0550d313164`，5 次归档 + F20） | 有 |
| `sys_after_apply` | system_status | `0x8` | DERIVED（apply 清 bit4；0x8 形状另有 1080 B 真件） | 有 |
| `mot_after_irq` | motor_status | `0x0` | REAL（648 B/`10807f63f02a`，9 次归档） | **无** |
| `mot_dir` | motor_status | `0x0` | REAL（同上，仅 dir 值按工况改 1） | **无** |
| `mot_armed` | motor_status | `0x0` | DERIVED（七门里没有 armed 项；这一步无归档原件） | **无** |
| `cruise` | motor_status | `0x2` | REAL（1026 B/`9005a306999a`、1025 B/`57efcc7c90d5`） | 有（锚定 `0x2`） |
| `mot_after_cancel` | motor_status | `0x2` | REAL（同上） | 有 |
| `stopped` | motor_status | `0x0` **或** `0x2` | 唯一边界，两值各有真件 | 有（只锚 `0x2`） |
| `mot_disabled` | motor_status | `0x0` | DERIVED（R6-2 收口已在板源码里；旧 0x2 件属 R6 之前会话，不作预期） | **无** |

- 预期 0 的 4 步**一条豁免都没有**：现场真打出 `REFUSED` 就按 `BUSINESS_FAILED` 停，这就是"仍要求 0"
  的实现方式；动作被产品拒绝与查询自身失败（`diag_relative.c:2041` 的 `learn_status REFUSED:`）
  一律继续 STOP。
- `stopped` 用新的 `expect_values.must_one_of`（枚举，不是通配）：集合外的值具名停
  `FIELD_VALUE_NOT_IN_SET`；矩阵 MC6 连"形状合法但自相矛盾"的 `0x8 ALL-PASS` 也判停。
- 计划侧新门 `R7F8F_ARM_GATE_MASK`（`validate-plan`）逐条核：每步有且只有一条 mask 逐值判据、
  mask 被 `must`/`must_one_of` 钉死、豁免集合恰等于"预期非 0 的那些 mask"各一条整行锚定模式、
  tail 与 mask 自洽。历史冻结包没申报契约标记时只登记 `NA`（与 `observation_bounds` 同一处置，
  不给旧包新红，也不回改旧字节）—— 所以无回归族仍是 9/0。

### 27.3 F21-b：历史真实字节纳入 + 跨计划披露合同继承

- 行表不再只喂我手拼的六行：`motor_status_pre_apply` = 1128 B 归档原件 24 行逐字；
  `motor_status_idle` / 清理那一条 `motor_status` = 648 B 原件 13 行逐字；`motor_status_ready_dir`
  是原件 13 行 + 单格 `dir=1`；armed/运行中的两组是本包声明形状 + 真实披露行。哪些是实拍、
  哪些是推导，全部写进行表 `_about.board_observed_rows` / `derived_shape_rows`。取件函数
  `_observed_mot_rows()` 核字节数与 sha 前缀，不符就拒绝生成。
- 口径按 A 的更正：`1128 B/f0550d313164…` 是"**F20 本会话首次取得**"，同一身份的字节早已在 5 次
  归档会话（R7F2b 三次、R7F6、R7F7）里 —— 上一版把它写成像"历史上没抓过"，历史样本漏了。
  F20 两段原始字节现在也归档进 `proof/`，MC11 逐字节比过。
- 两次 `system_status` 字节相同**只证明输出重复**；"接线无新变化"是操作者报告，判据里明写不升格为实测。
- 继承判据 MC10：把 R7F2b/R7F3/R7F4/R7F5/R7F6/R7F7 六份冻结计划里每条披露豁免取出来（按 cmd +
  字面 mask），要求新计划在同 cmd 上覆盖该值，否则必须点名不继承并给**可按步序复算**的理由。
  本包只有 `motor_status/0x8` 走不继承：第 6 步 apply 与第 11 步 irq_enable 之间没有 motor_status
  查询步（那段窗口里唯一的查询是第 7 步 system_status，它已按 0x8 申报）。

### 27.4 F21-c：令牌字节链（实体绑定与"真用了原话"分开判）

- 新入口 `--approval-token-file` / `--token-verbatim-file`：`token_bytes_from_file()` 在 Python 内
  读字节、只裁尾随 CR/LF、严格 UTF-8 解码 —— 不再有"打印到控制台再经 shell 变量"这一层。
- `approval_init` 把令牌摘要（sha256 + 字节数 + 来源）写进账本的 `approval_token` 格；账本不落原文。
- `verify_token_chain()` 在 **dispatch_plan 之前**核三份摘要：原话文件 / 实际传入令牌 / 账本绑定，
  任一不符具名拒绝（`TOKEN_DIGEST_MISMATCH` / `APPROVAL_TOKEN_NOT_BOUND` / `TOKEN_SOURCE_CONFLICT` /
  `TOKEN_VERBATIM_NOT_UTF8` / `TOKEN_VERBATIM_FILE_ABSENT`），一条命令都不发。
- F20 那一判**单独记未通过**：TG8 拿归档的 F20 账本（`proof/r7f8f_field_f20_approval_ledger.json`，
  原账不补改）用新门复算 => `APPROVAL_TOKEN_NOT_BOUND`，即当时"开口用的是不是那句原话"无从核对；
  承接件里的 `token_verbatim` 与原话文件同 sha（`e3b3a647a359…`），而 CLI 侧实际传入的 sha 是
  `9701aa453fe5…`，两份都登记保留。TG9 单独判四件**实体绑定** PASS：plan/rows/executor 三件在 Git
  历史里能找到内容散列逐字节相符的 blob（`44fa6c1`，不靠 HEAD 因为本轮会改这些文件），镜像按盘上
  字节复算 `23691e8d…`。两判互不掩盖。TG10 证 CLI 级拦截：rc=4、`TOKEN_CHAIN_REFUSED`、输出目录里
  没有任何台账文件；探针给的是不存在的口名 `COM_NONEXISTENT_M8R7F8F`，门若被改坏也碰不到 COM5。
- 承接件生成器跟着收紧：机械前置的声明人/逐字原话改成**必传参数**（旧版写死在源码里，默认值就是
  F20 那句），草案一律写 `NOT_REDECLINED_THIS_ROUND`；派生差异白名单从 10 格改成 13 格（多出的
  三格是令牌的来源与两份摘要），"9 格"这种硬编码话术改成按实际 diff 计。

### 27.5 六族 + 计划 + 派生复算（定稿字节上重跑）

| 族 | 数 |
| --- | --- |
| 反例矩阵 | **135/0**（新增 MC0~MC11 共 12 条 + TG1~TG10 共 10 条；FB6 因覆盖面变化改名） |
| stub 整包 | 29/0 |
| 无回归（冻结 R7F7 计划新旧对照） | 9/0（新门对旧包只登记 NA，点名表加 `R7F8F_ARM_GATE_MASK`） |
| 三版本重放 | 21 件 × 3 侧 = 63 行 / 0 |
| 整包离线门 | **49/0**（新增 GATE_PRESENT 与"门判绿且逐条点名 10 步"的交叉核对） |
| 夹具变更表 | 5 族 problems=0 |
| validate-plan | checks=147 fail=0 |
| 派生复算 `--verify` | PASS（仓外重建 328475 B 与仓库派生件逐字节相等） |

新身份（本轮交付，待一次现场批准）：

| 件 | 字节 | sha256 前缀 |
| --- | --- | --- |
| 派生执行器 `tools/m8r3_serial_chain_r7f8.py` | 328475 | `4363af36159b…` |
| 补丁 `patches/r7f8f_executor.patch`（83 条 edits） | 113931 | `e09d333b2f09…` |
| real plan `field/plan_r7f8f_2369_real.json` | 45398 | `2259359ca3f7…` |
| real rows `field/rows_r7f8f_real.json` | 15460 | `a8456159a45f…` |
| F21 承接**草案**（未批准，非批准件） | 47318 | `4a9460801b46…` |
| 在板镜像参照 | 297452 | `23691e8d8aa8…` |

### 27.6 本轮自伤与工具缺口（照单登记，不粉饰）

1. **变量遮蔽**：我在整包离线门里新加判据时把 validate-plan 的行表变量重命名成了 `body`，与后面
   五条 `PLAN_*` 判据共用的同名变量冲突，导致六条判据一起红。修法是改名 `m_body`；教训是新增判据
   不许复用局部变量名，跑完要复看"我动的那一族之外有没有跟着红"。
2. `bytes` 字面量里放非 ASCII 字符 → SyntaxError（TG6 的 GBK 夹具），改成显式字节列表。
3. 真字节夹具 `real_rows()` 一开始按前缀替换披露行，把 `motor.c:937` 那条**没有 tail 的裸行**也换成了
   REFUSED 行 => 同一行出现两次，`ROW_NOT_UNIQUE` 把它自己判红。改成只用契约的行正则匹配那一条；
   这条红反而证到了"逐值判据要求行唯一"是真的在判。
4. 令牌原话的 CLI 侧字节（`9701aa453fe5…`）当时没有落进任何工件，现在只能作为"会话里打印过的摘要"
   登记，无法从仓内复算 —— 这就是 §27.4 那三道摘要对账要补的洞；F20 的账与原话文件都保留原样。
5. 承接件工具的历史默认值（写死 F20 的机械前置原话）属于"下一轮不传参就会悄悄沿用上一轮现场事实"
   的形状，本轮改成必填参数 + 草案显式 `NOT_REDECLINED_THIS_ROUND`。
6. **交接 JSON 把停止步写成了 `sys_pre`**：生成器用 `BUSINESS_OUTCOME` 的**第一条**当"停在哪一步"，
   而台账里有两条（第 1 条通过、第 2 条才是首错）—— 结果是文档把通过的那一步说成了停止步，同一段
   字节里 `lines=[]` 明明是"没有业务失败线"。本轮改成拿 `SESSION_STOP.detail.lines` 与
   `BUSINESS_OUTCOME.lines` **求同源交集**来定停止步，交集为空就 `HANDOFF_REFUSE` 拒产，不回落
   "最后一条"。这是本会话发现的，修在 `tools/r7f8f_make_handoff.py:139` 起的读取段；§27.1 表格本身
   从头到尾写的是 `as=mot_pre`，与台账一致，未被这个 bug 污染。教训：**从归档字节派生的字段，取值
   规则必须写明"凭哪个键"，`[0]` 不是判据。**
7. **矩阵判据名里带了字段分隔符**：MC6 原名写成 `…stopped 的 0x0|0x2 各判 OK…`，而回执的行格式就是
   `名字|PASS|详情` —— 于是那行的第二格变成 `0x2 各判 OK, …`，交接工具按 `parts[0]/parts[1]` 读出来
   的键是 `MC6_…_0x0`、值是半截话（**PASS 丢了**）。135 条里只有这一条撞上有毒格式，靠肉眼看回执
   数不出来的。已把名字改成 `0x0 与 0x2`，并在 `note()` 里加了"判据名含 `|` 当场 `ValueError`"的硬门
   （`tools/r7f8e_case_matrix.py:40`）；重跑后矩阵仍 135/0。教训：**行式回执的字段名与分隔符是同一
   个命名空间，写名字的那一行就该被门挡住，不该留给下游解析去发现。**
8. **交接生成器的 `next_step_owners` 里写了两条同名的 `"A"` 键**：Python 字典字面量后写覆盖前写，
   F19 那条（"无需再定口径。仅当…才按裁定末段回报"）被 F21 那条静默吃掉，JSON 里只剩一条、也不报错。
   本轮删掉旧的那条并把归属改成"第三次现场尝试"，并在原位留了注释禁止再加同名键。教训与 §27.6 第 7 条
   同类：**手写字面量的键集合也是判据的一部分，重复键不会红，只会少一条信息。**

停点：本轮设备动作 0（未开口、未写任何字节到 COM5、未建本轮现场批准账；矩阵/门内出现的 dispatch
全是 stub/sim 或真实分支的**开口前拒绝**）。板子保持禁能、保持通电。F21 沿原范围、原时限与动作
上限，F20 那本账不结转；等操作者对上面四件新身份给一句真实原话（含本轮逐字的机械/电气前置声明），
再按 `--approval-token-file` 开新账并只跑一次整包。`polarity_measured=false` /
`core_protection_verified=false` 保持原值；仍不是现场验收通过。
