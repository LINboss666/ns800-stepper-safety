# 交接：NS800 M8-R1 相对保护返修轮（离线轮，零设备动作）

- 日期：本机 2026-10-31 03:34（= UTC 2026-10-30T19:34Z，权威时间戳见同名 JSON）
- 任务书：`NS800_M8_R1_Review_and_Repair_Task.md`（操作者本机文件，不记录个人绝对路径），任务号 `phase8-m8-relative-protection-r1`
- 入审结论：**`M8_IMPLEMENTATION_REVIEW_REWORK_REQUIRED`** → 本轮为集中返修，不发软件 GO、不发镜像 GO
- 工作分支：`feature/m8-sg-adc-relative-protection`（起点 `f9393634…`，未做任何 reset/rebase/merge/force）
- 源码与验证链提交（`code_commit`）：`3275356a46e0b503888e9a4869da3b2b41f027a8`
- 设备动作：**无**。`hardware_actions = []`；未导入 pyserial/pyOCD 设备对象，未枚举/烧写/回读目标内存，
  未复位、未开串口、未起转、未写配置、未触发故障、未制造碰撞。
- 本轮状态：`M8_R1_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable = false` 保持。

## 1. R1–R6 是否按合同做到（逐条落点见 `run_record.md` §1）

| 要求 | 一句话结论 | 判据 |
|---|---|---|
| R1 warn/strong 分离、三源年龄、恢复反证即断、fused 跨回绕 | 引擎级实现，不再由纯函数代跑 | `REL28/REL29/REL30/REL31/REL32` + `RV4` |
| R2 覆盖丢失必须"先保护后披露"；REL22 旧判据是错的合同 | 已显式改写为 `REL22-coverage-loss-protects-first`（身份保留、语义勘误） | `REL22/REL33/REL34/REL35/REL36/REL37` + `RV5` |
| R3 模式/学习/发布/保存的事务一致性 | 人工会改模式（含关闭）一律拒；开启与提交都在最终静止事务内复验；zero/learn 先作废本次凭证 | `REL38/REL39/REL40/REL41` + `RV3/RV6` |
| R4 阈值分层生成 + 关键行 ≤110 B | 低 SG 基线（min=64）可提交；不可分条件具名拒绝；最长关键行实测 101 B | `REL42/REL43` |
| R5 现场候选包要"可执行" | PowerShell 监督器 + Python 会话执行器 + 合成夹具；离线正例与 9 条关键失败路径真跑 | `M8FIELD RESULT: PASS`，退出码 0 |
| R6 ADXL345 独立碰撞保护 | 只认真新 IMU 证据；IMU 失联不 blanket 关 SG/ADC；RAM-only 身份，Flash schema 未动 | `REL44` + `RV7` |

主机链：`SUMMARY|cases=44 pass=44 fail=0 pre_bad=0 executed=44`；反向 7 家族全 META-OK；
审核方 13 项探针在修复后树上全部 `NOT_OBSERVED`；
回归 CC 34/34、SD、S1、S2（19 组×2）、M6-CLI、I/O（4 变体）、M5、A0 全部 PASS。
三目标各一次 Rebuild All：`0 Error(s), 0 Warning(s)`，`uv4_rc=0`。

## 2. 产物位置

- 证据目录：`reports/reviews/phase8-m8-relative-protection-r1/`
  （`run_record.md`、`field_candidate_package.md`、`host/` 34 件 + field 归档、`build/` 5 件、`field/`、`SHA256SUMS.txt`）
- 镜像目录（**新**，6 枚镜像/AXF + 3 份 map + manifest + 清单）：
  `reports/firmware/phase8-m8-relative-protection-r1/`；M8 的镜像目录一字未动。
- 旧 M8 缺的三目标构建日志已按具体路径 `git add -f` 补归档，
  并附 `build/ERRATUM_log_backfill.md` 与 `build/SHA256SUMS_backfill.txt`（哈希与 M8 已发布清单逐字相符）。

## 3. 本轮新登记的三条取证事实（不掩盖）

1. **入库字节身份**：`core.autocrlf=true` 会把带 CRLF 的证据文件在 `git add` 时剥成 LF；
   本轮 349 件暂存中 262 件曾出现 blob≠磁盘（map 3、.bin 115、.txt 115、.json 26、.log 3）。
   处置是 `git add -f .gitattributes`（**没有**改 `.gitignore` 白名单）+ 仅对本轮证据路径加 `-text`
   + `git add --renormalize reports/`，复核 `blob_neq_disk=0`。
   同时按同一判据回查 `code_commit` 的 12 个源码件：11 件相等，`tests/host/cc_main.c`
   差在 835 处行尾（内容一致，`git diff` 为空）。细节见 `run_record.md` §9。
2. **构建会话**：第一次 Keil 会话因脚本 `-o` 路径里 `$T` 未展开，三目标日志写到同一个文件互相覆盖，
   只有 motor-m1 那份存活（已保留为 `build/session1_…log`）；本轮 Program Size 与日志取自修正后的
   第二次会话，两次会话在该目标上的数值一致。见 `build_manifest.json.build_session_note`。
3. **返修过程**：现场候选包的离线自检在修好之前**假过一次**（`PORT_SILENT` 注入没真正生效，
   得到 `COMPLETED`），另一次因假端口守卫过宽而崩在 `INFRA:`。两条都是判据自身不足，
   修后才有那行 PASS；失败轮原文留在仓外，不入库以免被读成最终状态。见 `run_record.md` §10。

## 4. 下一轮需要谁做什么

- 审核方：对 `code_commit 3275356` + 本轮证据目录做一次独立复审（R1–R6 是否真做到、
  REL22 勘误口径、现场候选包的额度与顺序是否可接受）。**本轮不申请 GO**。
- 若复审放行现场轮：需要单独授权下载/回读/复位/串口/起转/一次 `config_save`/一次重启，
  并明确 `plan.json` 里的提案数字是否作为额度上限；学习窗口的 SG 码必须先换成 M4 实测巡航区间。
- 仍然悬着的实物/现场项（本轮未触碰）：P1 独立电流参考与零点增益、
  `RT_CONSOLEBUF_SIZE` 128 裁剪在真串口下的表现、v2→v3 参数迁移在真 Flash 上的行为。

## 5. 追加指针：M8-R2 轮（本节为追加，上文一字未改）

- 独立复审在 `b4d4079c8a2ae22405fa436d4513d0b2807839f8` 上给出的结论是
  `M8_R1_REVIEW_REWORK_REQUIRED`，其中与本交接直接相关的两条事实：
  - `OLD_13_COUNTEREXAMPLES = NOT_REPRODUCED`（R1 已修的 13 项判据缺口，本轮结论保留）；
  - `FIELD_RUNFIELD_AND_CONTROL_FLOW = REWORK_REQUIRED`（R1 的现场 `-RunField` 与控制流不足，转 R2 返修）。
- `R1_REVIEW_MANIFEST = 331/336_PRESENT_AND_MATCHED / FIVE_MISSING` 是本轮新暴露的**取证缺口**，
  与本交接 §2 的"5 件构建日志已入库"说法不符：那 5 件当时只在本机，未进 Git（`.gitignore` 的 `build/` 规则挡住）。
  M8-R2 轮已把 5 件原件（本机 sha256 与 R1 已发布清单逐字相符）按具体路径 `git add -f` 补齐，
  未改 `.gitignore`、未改 R1 清单原文、未重造历史。
- R1 的 C 链事实不变：44 例正向 + 7 组反向 META-OK + 9 件镜像/map 工件身份，
  其 `code_commit 3275356`、镜像目录 `reports/firmware/phase8-m8-relative-protection-r1/` 均未被后续轮次改写。
- 本轮之后的返修与交付见 `reports/agent_handoffs/phase8-m8-relative-protection-r2.md` /
  `.json` 与 `reports/reviews/phase8-m8-relative-protection-r2/`。
