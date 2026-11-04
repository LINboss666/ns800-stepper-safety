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
