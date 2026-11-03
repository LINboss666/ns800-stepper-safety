# M8-R7F2B 审核交接:补丁落地 + source_shape 承接件 + 一次构建(离线轮,设备动作 0)

本轮 = A 复审 `NS800_R7F2B_A_Review_and_Print_Fix_20261104.zip`(sha256 `9fad8f1d…b6e1`,基线 `70deff8`)的 5 项离线落地。
机器可判定版同目录 `handoff_R7F2B_offline.json`(16548 B,`55d715c83df1227353ca17aed8fce12b1209987e0f8e0e18a21480a1149f20fb`)。

## 0 一句话

**设备动作 0**:未下载、未回读、未复位、未开串口、未 apply/save/起转。新镜像 `502dca5d…` **不在板上**,板上仍是 `b4243886…`。
本轮证明的是"补丁进了这一版链接产物"以及"A 的判据/模型在本机可复算",不是任何现场验收。

## 1 五项落点

1. **补丁**:`safety_status_short_lines.patch` 已应用(`git apply --check` 先跑 rc=0)。
   `safety_gpio.c` blob `b242dfad…` → `2055c7496be91830709155a9ffb0720a0b0bdb8d`(= 补丁声明的 postimage),
   `git diff --numstat` = 2 增 2 删。磁盘工作树一度被 `git apply` 在 `core.autocrlf=true` 下写成 CRLF,已归一回 LF-only,
   版本化 blob 不受影响。
2. **新 rows**:`field/rows_r7f2b_source_shape.json`(54287 B,`80466bc3…`)。底稿 `rows_startup_b424.json`(53635 B,`916eddaa…`)
   与全部现场原件逐字节未动;只替换 `commands.safety_status` / `commands.motor_status`,A 的行与 `_provenance` 一字未改,
   每条附 `_derivation_r7f2b`(含被替换掉的旧行原文 + 底稿 hash)。全件标 `kind = source_shape 预测,不是板级采集`。
3. **计划承接**:`field/plan_r7f2b_newimage_draft.json` 由已复审计划派生(派生编辑逐条在 `derivation_edits`);
   `motor_status` 步骤 = A 的片段逐字;**执行器未改**(仍是 R6C 原件 `33d3f0af…`,219491 B)。
4. **不再假装 ALL-PASS**:新 rows 的 motor_status 用 `arm_gate_mask=0x18 REFUSED` + `mot_print_gate_fail(0x18)` 的
   gate_reason/gate4..7 明细,25 行全部标预测。
5. **一次构建**:`rt-thread-motor-m1-candidate` Rebuild All 一次,rc=0,`0 Error(s), 0 Warning(s)`,
   `Program Size: Code=205930 RO-data=86198 RW-data=2192 ZI-data=85596`(比上一场 +8 / +12)。
   BIN **292708 B** `502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a`(比 b424 +24 B)、
   AXF 1775972 B `fc567635…`、MAP 2114829 B `fc33986f…`、调用图 htm 1078228 B。旧 b424 身份与旧批准**没有**套到新镜像。

## 2 生效证明(镜像内,不是"应该生效")

按 NUL 分串枚举带标签前缀的格式串(`host/image_strings_r7f2b.txt`):
合并形态 1→**0** 次;拆分 A 段 / B 段各 **1** 次;ENN 格式串 147 B→**61 B**;>127 B 的 tagged 串 36→**35** 条。
运行时长度由 host 模型承担:`host/enn_model_repro.txt` 复算出修前 471 B/1 NUL/非严格 UTF-8/最宽 214 B
(`intended=150 emitted=127 truncated=1`),修后 503 B/0 NUL/严格 UTF-8/最宽 106 B(`intended=64`+`intended=95`),
与 A 的 `verification.json` 逐项一致;108 组实参扫描修前 108/108 有 NUL、修后 0。
受影响回归:`host/io_chain_r7f2b.txt`(4 变体 33/35/46/48 必需用例全 PASS + 非法宏值拒绝编译)、needles 40 条 unproven=0。

## 3 如实登记的 5 条

- **GF1** A 的 `host_before.c` 在本机 MinGW 4.9.2 原样编译后**段错误** rc=3221225477(本机 msvcrt 的 `vsnprintf` 截断时返回 −1,
  模型把 `(size_t)-1` 交给 `fwrite`)。加 `-D__USE_MINGW_ANSI_STDIO=1` 后与 A 同值。这是量具的编译器可移植性,不是被测缺陷;失败原文保留。
- **GF2** 离线门 38 项 checks / **fail=1**,唯一 FAIL 是 `PLAN_MOTION_CAP_BUDGET`(caps=[],A 已裁零运动链包记 N/A;不改执行器不塞动作凑绿)。
- **GF3** `safety_irq_status` **没有**新固件行形预测(A 只给了两条命令)。草稿计划该步只留 `expect_contains=["[SAFETY] "]`,
  缺口登记在 rows 的 `_provenance_r7f2b_gap`,不代编。
- **GF4** 同一枚新镜像仍有 **35 条** >127 B 的带标签格式串(最宽 257 B 的 `[MOT] !! 业务失败…`)。本轮按 A §5 只改指定那一处,未扩改。
- **GF5** 勘误:上一场写「150 B 正文 + LF = 151 B」,正确是 **149 B 正文 + LF = 150 B**(源码转义 `\n` 被多算一次)。
  越限结论与"前 126 B 逐字节相等"不受影响;已提交件不回改,勘误进 manifest 与本件。
- **GF6** 本轮**第一次提交**(`275c37f`)的两件派生件自身有缺陷:仓库相对路径被 `str(WindowsPath)` 写成反斜杠形式,
  且把上一场启动原件名凭记忆写成 `01_boot.rx.bin`(目录实名 `01_rst.boot.rx.bin`)。远端逐 blob 回读抓到,
  本轮以第二次提交修正(路径全改正斜杠、文件名按实名),`275c37f` 保留不回改;改后重跑 `--validate-plan` 仍是 38 项 / fail=1(同一项 GF2)。

## 4 下一场(草稿,不是批准)

`plan_r7f2b_newimage_draft.json`:S0 离线绑定 → S1 下载新 AXF → S2 定长回读 292708 B 逐字节比对 →
S3 复位 + 75 s 被动窗 + `ps/free/system_status/safety_status/motor_status/safety_irq_status` 同会话。
额度**提案** 1/1/1/1,其余全 0;`approval.hardware_actions_authorized_by_this_package = 0`;
状态自证 `AGENT_AUTHORED_DRAFT…REQUIRES_NEW_EXPLICIT_OPERATOR_APPROVAL`。
本件不创建批准身份、不向操作者索要批准。`tmc_config_apply/CONFIGURED` 短打印验收明确不在本草稿内
(A:那是需要另加一次配置写入及其判据的另一件事,不算只读查询,也不沿用旧写入额度)。

## 5 请 A 定

1. 本镜像的实际现场范围是否就按 §4 草稿(同会话四条只读 + 启动流)定稿;
2. GF4 其余 35 条超长格式串是否逐条清点;
3. GF5 的勘误口径要不要同步进 A 侧记录;
4. 是否需要为 `safety_irq_status` 补一份本镜像的 source_shape(GF3)。
