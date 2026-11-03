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

