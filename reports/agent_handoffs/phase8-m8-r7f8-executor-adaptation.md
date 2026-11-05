# 交接：M8-R7F8E 执行器离线适配轮（2026-11-05）

**状态：OFFLINE_DEVELOPMENT_DONE / SOFTWARE_REVIEW_PENDING / HARDWARE_ACTIONS=0**
**基线 `d6bbc1b5795f`；分支 `feature/m8-r7d1-async-learn-offline`；任务书见
`reports/reviews/phase8-m8-r7f8-executor-adaptation/inputs/A_R7F8E_PKG/TASK_R7F8E_EXECUTOR_OFFLINE.md`。**
机器可读同源件：`reports/agent_handoffs/phase8-m8-r7f8-executor-adaptation.json`（数字当场从盘上算）。

## 1. 一句话

按 A 的任务书把"异步学习的有界轮询 / 动态 JOB_ID / 取消显式额度 / arm 通电截止"四项落进一个
**派生执行器**，并用 65 条矩阵判据、27 条整包判据、8 条无回归判据（全 FAIL=0）证明它会怎样收口；
顺带修掉两处被新反例照出来的既有工具缺口。**现场什么都没做**：设备动作 0、未构建固件、未开现场账。

## 2. 交付件（新目录 `reports/reviews/phase8-m8-r7f8-executor-adaptation/`）

| 件 | 身份 | 作用 |
| --- | --- | --- |
| `tools/m8r3_serial_chain_r7f8.py` | 245730 B / `4c962170b2357eef94afd73d43790d61880635a3d13287d29d543035768d6fed`；4447 行（旧 3977，+485/−15） | 派生执行器：E1–E4 四项适配 + T3/T4 附带修正 |
| `tools/r7f8e_build_executor.py` | 29 处逐字锚点编辑；`--verify` 仓外重建逐字节相等 | 派生件可由旧件原字节复算，旧件不动 |
| `patches/r7f8e_executor.patch` | 38672 B / `27d3d49133721775…` | 与旧版的明确差异原件 |
| `tools/r7f8e_case_matrix.py` + `proof/r7f8e_case_matrix.txt` | 判据 65 / FAIL 0 | §4 全 11 组 + 5 项元负控 + 4 项原始字节探针 + 4 项身份反例 + 自证 |
| `proof/r7f8e_case_matrix_before_fix.txt` | 18 条 FAIL 原文 | 修前基线留档（含 T1 崩溃形状） |
| `tools/r7f8e_stub_package.py` + `proof/r7f8e_stub_package.txt` + `plan/*` | 判据 27 / FAIL 0 | STUB_ONLY 取消＋DONE 整包与四场等价负例；合成计划/行表（无端口、无 UID、JOB_ID 非固定） |
| `tools/r7f8e_no_regression.py` + `proof/r7f8e_no_regression.txt` | 判据 8 / FAIL 0 | 冻结 R7F7 定稿计划新旧逐行对照：旧 57 条 0 洗掉，新增 5 条全为 `R7F8E_` |
| `ERRATA_R7F8E.md` | 9593 B / `6740ba6a7cb934b7…` | 三句旧解释逐字勘误 + T1–T4 工具缺口登记 |
| `inputs/A_R7F8E_PKG/` | 24 文件 / 165145 B | A 的任务书、修订草案、IDENTITY、23 项输出、主机探针与失败首次尝试、包内清单 |
| `R7F8E_DELIVERY.md` | — | 本轮交付正文（含复算口径与自伤清单） |

同步更新：`phase8-m8-r7f8-closed-loop-draft/R7F8_DRAFT_MERGED.md`
→ 36273 B / `e9a5253de02476538f6e746583f78717a838e940ed6c5c54de4d892502a85912`，
正文 272 行逐字承接 `R7F8_DRAFT_UPDATED.md`（`INHERIT_EXACT|equal=True`）；
`A_DRAFT_ORIGINAL.md` 仍 24780 B / `b572c1928f60` 未改。
`run_record.md` 追加 §23（共 23 节，§23 只出现 1 次）；`.gitattributes` 为新目录加 `-text`。

## 3. 请 A 复审的四件事（不复审我就不动下一步）

1. **E1 的"接受面"是否够窄**：DONE 正例要求 `owner=0(FREE)`＋`cred=1/1`＋`adc/sg≥want`，
   且进行中集合只认 `RUNNING/CANCEL_PENDING`；其他终态立即收口。请核这是否就是任务书 §3 E1 的意思。
2. **E2 的绑定与不回写**：批准绑定的是"固定计划＋解析/生成规则"，运行时派生命令、ID 来源 RX hash、
   来源步骤与规则身份进台账（`JOB_ID_BOUND` / `JOB_CMD_RESOLVED`），获批 plan 字节不动（`I4` 判据）。
   请核"派生命令不进获批 plan、但必须进台账"这条口径。
3. **T3 是否接受**：单会话路径的清理预留账未接（`cleanup(..., quota_box if plan else None, ...)`
   传形参，CLI 会话永不传）——我改成传有效账本，并证明 dispatch 现场路径**零行为变化**
   （那里 `quota is quota_box`），`X5` 元负控作证。这是第 5 处改动，超出 §3 的四项，请裁定留还是撤。
4. **E4 的"两组都列"**：合成整包里业务段 stop/disarm 与最终固定清理各记各的（实测
   `motor_stop=2、motor_disarm=2、motor_status=5`，5 = 基线 1 + 巡航轮询 2 + 收尾 1 + 清理 1）。
   请核这个分解与现场轮的额度口径是否一致。

## 4. 本轮没验证、也没声称的

板上正常巡航 DONE、活动取消、SG/ADC+IMU 相对保护触发、加减速窗冲击保护、恢复语义的板端证据、
R7F8 现场的刺激方法/速度窗/时限/真实动作预算 —— 全部仍待现场轮。
矩阵与整包只证明"工具会怎样收口"；A 包内那四例主机 C 探针是合成输入下的主机逻辑证据
（含 1 条既有夹具警告，未声称 0 warning），不写成板端已验证。

## 5. 下一步归属

- **A / 操作者**：复审本包（含上面四问），并定稿 R7F8 现场参数与真实动作预算。
- **我（拿到定稿后，仍离线）**：出可 dispatch 的现场包（真实端口/UID 绑定、额度与机械前置就绪门）
  并单独请求现场批准。
- **我（不做）**：本轮不进入现场执行、不构建固件、不开真实批准账；设备按上一轮约定保持通电收口。
