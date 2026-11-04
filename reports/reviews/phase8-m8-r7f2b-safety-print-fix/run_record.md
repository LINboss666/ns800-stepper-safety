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

