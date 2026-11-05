# DIR0 变体包：决定、依据与身份（2026-11-06，M8-R7F8F 续）

**谁决定的**：操作者（现场执行者）在本会话里两次表态 —— 先批准 `plan 2259359ca3f7…`（DIR1 交付件），
随后在我报出"本句写 DIR0、包内写 `motor_dir 1`"的冲突后选择「**要 DIR0：我先出变体再取批准**」，
并给出前提「DIR 不是安全相关轴，产品的方向处理已被既有测试覆盖」。
**本文作用**：把那个前提按源码逐条核一遍，写明成立到哪一步、不成立到哪一步，并钉住变体身份，
交云端 A 复审。**产品源码一字未动，未构建、未下载、未回读、未复位、未开串口。**

## 1. 前提核对（逐条带出处）

| 说法 | 核对结果 | 出处（冻结源码，本轮只读） |
| --- | --- | --- |
| DIR 不是**机械安全**相关轴 | **成立** | `motor.c:1470/1496`：DIR 只在通过四道门后写 DIR 寄存器与 `mot.dir`；`motor.c:894-917` 的七个 arm 门位里没有任何方向项（`arm_gate_mask` 的 bit1/bit3/bit4 与方向无关）=> 使能/起转/禁能的安全链与方向无耦合。 |
| 方向已被**既有测试**覆盖 | **成立（板级 + 主机级）** | 板级：M3-3「DIR0 短转」、M3-4「DIR1 短转」功能通过（项目记忆与 `run_record.md` 的 M3 段）；主机级：`tests/host/io_main.c`、`ps_main.c`、`rel_main.c`、`sd_main.c` 里带 `motor_dir` / `DIR_MASK` 的用例。 |
| DIR 与**本次要证的相对保护**无关 | **不成立 —— 方向会绑定证据** | `diag_relative.c:2023` `cand.dir_mask = (1u << dir)`：学习出的 profile 只置**一个**方向位；`diag_relative.c:334` `if ((p->dir_mask & (1u << (in->dir & 1u))) == 0) return 0;`：判定引擎**只对方向位在 mask 里的样本**做相对保护；`diag_relative.c:126` 要求 `dir_mask ∈ {1,2}`（理由码 `DIR_MASK`）。产品注释（`diag_relative.c:2005`）自己写明"只采一个方向却打 `dir_mask=3` 会对外宣称双向都验过，那是第二条假证据链"。 |

**因此**：DIR0 包不会让保护失效，也不会少一条判据 —— 它把本轮要交付的结论**限定成"dir=0 方向上的
SG/ADC 相对保护现场证据"**。这是一次**证据标签**的变更，不是安全包络的变更。若之后还需要 DIR1
方向的同一结论，那要再一次现场尝试（各方向的 `dir_mask` 只能由那一次真实学习给出）。
操作者的决定在此前提下成立，我按 DIR0 出包；证据边界按上面这句话写，不写成"双向已验"。

## 2. 变体怎么来的（不是手改）

- 单一来源：`tools/r7f8f_make_real_package.py --dir 0 --tag _dir0`。DIR 从交付件的 1 变成 0 时，
  **命令串、判据、行表行、结构化 `dir` 参数**一起改，改在生成器里而不是改产物文件。
- **血统门**：派生前先逐字节证明"本次未翻转的生成结果 == 盘上已交付的 DIR1 件"
  （plan `2259359ca3f7…` / rows `a8456159a45f…`），不等就 `REFUSED`。所以变体里登记的
  `derived_from_*_sha256` 是算出来的，不是我写的。
- **值翻转范围**：20 处字符串 + 4 处 `{"dir": 1}` 结构化参数。**残留检查**：翻完后
  `dir=1` / `motor_dir 1` / `DIR1` / `"dir": 1` / `350 1` 五种模式必须一个都不剩，剩就 `REFUSED`。
- **过宽检查**：`motor_status_ready_dir` 翻完必须**逐行等于** 648 B/`10807f63f02a` 的归档原件
  （那份就是 dir=0 时抓的）—— 这一格从"值翻转形状"退回"逐字节原件"，是变体里 provenance
  **更强**的一处。
- **不越界**：`quota["motor_dir"] == 1` 单独断言没被翻走（那是**次数**不是方向）。
- **provenance 更弱的一处，明写不藏**：`motor_status_cruise` / `motor_status_accel` /
  `motor_status_armed_ready` / `motor_status_armed_idle` 四行的 dir 列由 1 翻成 0，而归档的
  **运行中**真实件（1026 B/`9005a306999a`、1025 B/`57efcc7c90d5`、1074 B/`98abbf2d18df`）都是
  dir=1 抓的 => 这四行是"声明形状 + 已申报的值翻转"，不是实拍。已写进
  `rows_r7f8f_real_dir0.json` 的 `_about.derived_shape_rows`。
- 说明性文字（`_about.derived_shape_rows` 与契约表 `mot_dir` 的 `basis`）不走通用文本替换，
  由生成器整条替换；命中数不等于 1 就 `REFUSED`（否则会留下"行已退回原件、句子还说改成 1"的假陈述）。

## 3. 变体在**原有六族离线门**上的结果（操作者要求：完整重算摘要 + 六族全过）

| 族 | 入口 | 结果 | 是否受 DIR 影响 |
| --- | --- | --- | --- |
| 反例矩阵 | `tools/r7f8e_case_matrix.py --package-tag _dir0` | **135 / FAIL=0** | **受影响**：mask 契约、FB/MC/TG 三族读的是被测包；已改读变体，回执 `proof/r7f8e_case_matrix_dir0.txt` |
| 整包离线门 | `tools/r7f8f_offline_gate.py --package-tag _dir0` | **49 / FAIL=0**（内含 validate-plan **147 项 0 红**） | **受影响**：整包 stub 复算与全首错路径跑在变体上；回执 `proof/r7f8f_offline_gate_dir0.txt` + `proof/offline_gate_dir0.json` |
| stub 整包 | `tools/r7f8e_stub_package.py` | 29 / FAIL=0 | 不受影响：它构造的是自己的合成夹具计划（`plan_r7f8e_stub.json`），不读 real plan |
| 无回归对照 | `tools/r7f8e_no_regression.py` | 9 / FAIL=0 | 不受影响：对象是冻结的 R7F7 定稿计划新旧逐行对照 |
| A 包复算 | `tools/r7f8f_budget_package_checks.py` | 11 / FAIL=0 | 不受影响：判据对象是 A 的裁定包与提交 `6f2b12f` 的 Git blob（工作树信息位仍按交付件报） |
| 三版本重放 | `tools/r7f8f_probe_replay.py` | 21 件 × 3 侧 / FAIL=0 | 不受影响：对象是执行器的 F5b/F5c 补丁前后字节 |
| 夹具变更表 | `tools/r7f8f_fixture_change_table.py` | 族=5 problems=0 | 同上（判据名集合未因 DIR 变化） |

执行器与镜像**未重建、未改动**：`tools/m8r3_serial_chain_r7f8.py` 仍是 328475 B /
`4363af36159b…`，镜像仍是 297452 B / `23691e8d8aa8…`（= 已在板那枚）。四件里只有 plan/rows 换摘要。

## 4. 待批准的变体身份（完整摘要）

| 件 | 路径 | 字节 | sha256 |
| --- | --- | --- | --- |
| plan（DIR0） | `field/plan_r7f8f_2369_real_dir0.json` | 46128 | `2f82bf93fddfee9f2cf36a727685612307f52d7166244b78040b58cdad11d662` |
| rows（DIR0） | `field/rows_r7f8f_real_dir0.json` | 15734 | `8dc0b5c06bbc4268652dcc9d5404636cbfbe8112686d297824dc9b73a61a1565` |
| executor | `tools/m8r3_serial_chain_r7f8.py` | 328475 | `4363af36159befa417647680cc27fb5bdb95785a404b14d77602ccdbe253962e` |
| 镜像（在板，未重建） | `reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin` | 297452 | `23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963` |

不变项：38 步、逐段窗口（业务 43.0 s / 总 90 s 含 7 s 收口 / 零速 ≤12 s / arm 至禁能 ≤18 s）、
30 条额度、`forbidden_commands`、`arm_gate_mask_contract=R7F8F_PER_STEP_V1` 与那张 10 步契约表
（预期 mask 的值与 DIR 无关，方向不在七门里）。
DIR1 交付件与它的回执**按字节冻结不回改**，本变体用新文件名并存。

## 5. 开账前仍需要的东西（我不自己填）

一句**新的真实原话**，对象 = 第 4 节那四件（DIR0），并在同一句里逐字重述机械/电气前置。
承接件仍由 `tools/r7f8f_make_dispatch_copy.py` 派生（白名单 13 格）并按
`--approval-token-file` 绑三份摘要；F11/F20 的两本账已各自用满，不结转。
本轮设备动作 0：未建账、未开 COM5、未发任何字节。
