# M1 电机整批离线准备 —— 交接（phase8-motor-bringup-m1-prep）

- 输入锚：`ea519f29…`（= 开工时本地 HEAD 与远端头）；软件输入提交：`70416e7f…`（含候选工程字节的那次提交，先提交工程再构建）。
- 一句话结论：**五项交付齐（查因树/限流依据/候选 Target/两目标真实构建/现场包草案），全部离线**；候选镜像 `a76b845d…` 已构建但**不可编程**，板上最后已证镜像仍是 `5f30d3f0…`，供电终态仍 `UNKNOWN`。
- 结论强度：`M1_OFFLINE_PREP_DELIVERED_AWAITING_INDEPENDENT_REVIEW`。本轮没有软件 GO、没有硬件 GO、没有自派现场执行。

## 1. 交付清单与身份（远端可回读）

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `reports/reviews/phase8-motor-bringup-m1-prep/source_audit.txt` | 21087 | `d0263137a18acee1fc9b83e561720046fba6429629473d45537ffecddcf69f0f` |
| `reports/reviews/phase8-motor-bringup-m1-prep/motor_current_basis.md` | 10071 | `7444f7a90f290e36ed4fa9de570b316e444b87dc5fb5c16c12114c3985a27814` |
| `reports/reviews/phase8-motor-bringup-m1-prep/board_execution_candidate.md` | 20844 | `60c3635a3e6cb7581fb2b3d2442adfe926c41d53bb0a29604dbcf0c97343e9d1` |
| `reports/reviews/phase8-motor-bringup-m1-prep/keil_builds.txt` | 15984 | `6d6427e3b46317a871f7be157c939cbb8e79249d54eb6ba189082b0ce810c836` |
| `reports/reviews/phase8-motor-bringup-m1-prep/project_scope_verification.txt` | 5170 | `edc6de73043ab485d9444c4e1d303a77b964f5968d453010bb865cd9de845908` |
| `reports/reviews/phase8-motor-bringup-m1-prep/candidate_link_extract.txt` | 9602 | `242eb943d63929e673892548ba05d774de2b6eb1cbc21abc118feae4a3046896` |
| `reports/reviews/phase8-motor-bringup-m1-prep/build_log_default.txt` | 4079 | `322ff127dada1459fde2b9d55d2548a5cc67561a100f50dfa29599a8551fb7c8` |
| `reports/reviews/phase8-motor-bringup-m1-prep/build_log_candidate.txt` | 4012 | `c3031eecb7c6eed6914add7c8dc6fb7abbf8526fbe8044d637f3ac4957ab8948` |
| `reports/reviews/phase8-motor-bringup-m1-prep/make_motor_m1_target.py` | 3596 | `d5dbce28f3ed65610b7e81ef1339a2dd440b5c831de8a0602bdfa5ccc5ea9f49` |
| `reports/firmware/phase8-motor-bringup-m1-prep/default-control.bin` | 251148 | `7f9baed96ecb23cdf7cb9d1c0ccd4c28f2946bc9f3d720a560f7741cb0f4e45d` |
| `reports/firmware/phase8-motor-bringup-m1-prep/motor-m1-candidate.bin` | 251260 | `a76b845db4ba1d8872f9db395c2e6e796bdb9416c398f010b669538006cf2777` |
| `reports/firmware/phase8-motor-bringup-m1-prep/motor-m1-candidate.map` | 1948503 | `9f6d37203ca6226ef74323096d48fe5b6719300ab3cfec35f88bfc45833b586f` |
| `reports/firmware/phase8-motor-bringup-m1-prep/build_manifest.json` | 15253 | `0b145d2ae989e7c37300c5be90c5249f0e731468cda475d70da03fff64f6304f` |

未入库（构建区，被工程目录 `.gitignore` 排除；只登记字节与散列，不称云端可见）：默认 AXF `92169bef54992fa7f28d2d52280c6130e2cd8e9c8702d3ca92c09b4b1d74a43f`、默认 map `ab33ac1de309b2ce40402627c3b8a6dff0d81ec0104f590a7723d49f5a5f22f7`、候选 AXF `c1e9d4e78c424b3631b2d30cbe6061f32074b30c7518ed2aa681771d6600a86f`；完整预处理转储与两份 `.log` 原件在 `M1_BUILDS`。

## 2. CP1 —— 通信查因、限流依据、门禁顺序（`source_audit.txt` / `motor_current_basis.md`）

- 现有证据能**排除**的两类原因：uart2 设备打不开、执行器锁未创建 —— 因为启动流里的健康名是 `DEGRADED` 而不是 `FAILED`（`tmc2209.c:709-713`、`:716-732`、`:734-740` 的语义），且门禁位图解出 `arm=0x1B`、`write=0x3B`。
- 仍**未证**的是 B..E 各子因（地址/在位/时序/时钟域）。`tmc_link_status` 保持 `LINK_UNPROVEN`，没有因为离线分析变健康。
- 限流：按任务书 §5.3 公式独立复算（Rsense=110 mΩ、vsense=1、VREF=1.27 V）→ run 326.40 mA RMS、hold 139.88 mA、IRUN=0 时 15.54 mA（**非零**）；固件整型口径给出 peak 461 / RMS 326。`1500 mA`、`110 mΩ`、`1.27 V` 三个数**没有同实物依据**，已在 §1 分级表里标成文档/名义值，不当新证据用。
- 门禁顺序图是"读代码得到的必要条件"（含 `tmc_cfg_gate_prod` 实际在 `:1456`、`fault_reset` 实际起于 `safety_state.c:684` 等**对任务书行号的勘误**）。

## 3. CP2 —— 第三个候选 Target（唯一非 reports 改动）

- 生成方式：**只用附录 A 的确定性生成器逐字运行**（`REV/make_motor_m1_target.py`，3596 B / `d5dbce28f3ed65610b7e81ef1339a2dd440b5c831de8a0602bdfa5ccc5ea9f49`，已实测与任务书代码块字节相同），工程输入取自 git 对象而非工作树。
- 工程 blob：锚点 `c18d31f991f6` → HEAD `8f8d7ce31298`（338976 B），前两个 Target 的原始块**逐字节相同**；目标名顺序 `rt-thread / rt-thread-measure-a0 / rt-thread-motor-m1-candidate`。
- 差异只六个叶子（TargetName / OutputDirectory / OutputName / ListingPath / 目标级 Define / AfterMake 的 UserProg1Name）；三目标都**没有文件级 Define 覆盖（0/135）**，BeforeMake 全 0、AfterMake 只有 `fromelf --bin`。
- 生产件/历史件 blob 逐个不变（18 项断言，见 `project_scope_verification.txt` 第 3 节），冻结引用 `main` / `phase7/…` / tag 对象与解引用全部未动。

## 4. CP3 —— 两目标真实 Rebuild All

| 目标 | rc | 用时 | Program Size | BIN |
|---|---:|---|---|---|
| rt-thread | 0 | 15.1 s | Program Size: Code=173762 RO-data=76806 RW-data=2188 ZI-data=83784 | 251148 B `7f9baed9…` |
| rt-thread-motor-m1-candidate | 0 | 15.0 s | Program Size: Code=173994 RO-data=76682 RW-data=2188 ZI-data=83784 | 251260 B `a76b845d…` |

- 默认目标正好落在任务书 §7.1.1 的预期值（Code=173762 / RO=76806 / RW=2188 / ZI=83784），这一条是**实测相等**，不是假定。
- 默认镜像与已接受 `default.bin`（`02e98458…`）**只差 6 个字节**，偏移全部落在同一段 `__TIME__/__DATE__` 横幅（`20:56:52`→`16:54:05`），横幅外零差异 —— 差异定位过，不是随口归因。
- 链接侧：两目标各 107 个对象、**对象名集合完全相同**，只有 `motor.o`/`stop_priority.o`/`supervisor.o`/`tmc2209.o` 四个尺寸变化（恰是两项宏例外影响的单元）；五个 HAL 叶子逐列相同；`spm` 前缀符号名 默认 0 / 候选 0 / A0 对照 35，且 A0 多出 `sp_measure_a0.o` —— 正向对照有效，"候选没夹带 A0 记录器"不是漏检。
- 失败尝试保留：第 1 次调用停滞（UV4 存活 ~10 分钟、CPU ~3 s、无编译器子进程、无日志无产物），已终止并登记为 `M1-CP3-F1`；改的两项环境形态同时生效，因此**不声称单因**。
- 主机链 A0/S1/S2/V1V2/I-O/SD/反向/元负例：`NOT_RERUN`（输入未变），不为凑 PASS 重跑；0 警告只适用于 ARMCLANG V6.24。

## 5. CP4 —— 现场包草案（`board_execution_candidate.md`，一条也未执行）

- M0：7 条命令的字面序列 + **逐字判据**（每条都取源码里的 `rt_kprintf` 原文与行号）+ 副作用 + 预算与停止规则；不重烧，用板上 `5f30d3f0…`。
- M2：候选镜像 + 配置路径（`fault_reset`→`system_selftest`→`tmc_config_apply CONFIRM`→`tmc_config_show`→`motor_status`），额度单列、不从上轮借用；写明 `IHOLD_IRUN` 只写、不得伪造读回。
- M3：首转准备（空载、低速 100 Hz、名义 2 s、单方向一次），全部 `NOT_AUTHORIZED / NOT_EXECUTED`；`motor_start` 成功路径**无输出**这一事实已写进判据。
- 新登记的现场事实（写进包内防自欺）：**读命令也会改 `tmc_health`**（`tmc2209.c:709-713` 由 `:738` 调用），所以"probe 之后 health 变好"不构成链路健康证据。
- 引用纪律：本文件 129 项 `file:line` 断言全部命中（mismatch=0），初稿 5 处行号错误已更正（改文档，不改源码）。

## 6. 边界（本轮没做的事）

```text
NO HARDWARE ACTIONS / NO SERIAL / NO DAP / NO FLASH READ / NO PROGRAM / NO RESET
NO MOTOR CONNECTION / NO STEP / NO HIGH / NO DIR / NO FAULT_RESET / NO SELFTEST ON TARGET
A0_DURATION_EVIDENCE NOT_ACHIEVED / BOARD_POWER UNKNOWN
hardware_actions=[]  target_commands_executed=[]  serial_open=0  dap=0  program=0
flash_read=0  active_reset=0  device_enumeration=0  sw1_cut.ps1=NOT_RUN(HOLD)
电机未接、未产生 STEP/HIGH/DIR 动作、未清故障、未在目标上跑自检
未改 .c/.h/SConscript/.gitignore/HAL/内核/测试链/历史证据; 未放宽任何门
```

提交前自检：自产文本 0 行尾空格、0 CR、JSON 可解析、隐私扫描 0 命中；唯一 `git diff --check` 命中在按原字节归档的候选 map 自身内容（armlink 表头行尾空格 1 处 + 文件末空行 1 处），为保住可复算字节身份未做美化。

## 7. §9 验收表逐项（不凑全绿）

| 验收项 | 结论 |
|---|---|
| anchor_scope | PASS |
| original_two_targets_unchanged | PASS |
| new_target_matches_appendix_A | PASS |
| real_builds | PASS |
| effective_macros | PASS |
| image_identity | PASS |
| comms_cause_tree | PASS(离线交付); 板上区分 = M0 NOT_EXECUTED |
| current_basis | PASS(名义值可复算); 同实物依据缺失 -> 阻断 M2/M3 |
| future_field_package | DRAFTED_NOT_EXECUTED |
| safety_boundary | PASS |
| history_protection | PASS |

## 8. 首次失败与自身缺陷（保留，不改写历史）

- `M1-CP3-F1`：首次 Rebuild All 停滞(无日志无产物), 已终止并保留登记（登记于 reports/firmware/phase8-motor-bringup-m1-prep/build_manifest.json(build_attempts[0])、reports/reviews/phase8-motor-bringup-m1-prep/keil_builds.txt 第 1 节）
- `M1-CP3-F2`：默认目标 BIN 不在 build/ 下, collector 记为 missing; 按日志 fromelf 行在工程根取件并散列（登记于 reports/reviews/phase8-motor-bringup-m1-prep/keil_builds.txt 第 2 节、M1_BUILDS/default.build.json）
- `M1-DOC-F1`：证据初稿里有两处未经实测的数字(spm 符号计数写成 56、文件级 Define 覆盖写成 32 处); 出稿前改为现算值(35 与 0), 未提交错误版本（登记于 reports/reviews/phase8-motor-bringup-m1-prep/keil_builds.txt 第 4 节、reports/reviews/phase8-motor-bringup-m1-prep/project_scope_verification.txt 第 2 节）
- `M1-DOC-F2`：现场包初稿 5 处源码行号引用错误, 逐行断言后按实测更正(改的是文档, 不是源码)（登记于 reports/reviews/phase8-motor-bringup-m1-prep/board_execution_candidate.md 文首引用口径）
- `M1-DOC-F3`：提交前隐私扫描发现 motor_current_basis.md 第 4-5 行漏出两个仓外工作目录的本机绝对路径; 已改为代号并声明该行是有损表述, 在入库前拦截（登记于 本地提交前扫描(断言 15 个文件 x 9 个禁用串, 命中 1 处)）

## 9. 下一步（等你，不自派）

1. 云端独立复核实际 SHA、工程差异、构建/产物、通信包与电流依据。
2. 复核通过后，现场包绑定真实散列与预算交你**一次批准**（可只批 M0；参数与候选审查齐备时可批 M0→M2→M3 有界整包）。
3. 最小缺项（只列一次）：电机铭牌/型号与额定相电流定义；驱动模块型号与 Rsense/VREF 的同实物依据。缺参不阻断已交付项，只阻断 M2/M3。
4. 另有两件旧欠：BOARD-B 的断电终态回报（`CP4` 待登记）、SERIAL-R1/R1a 的 CP1 仅串口续测批准 —— 你已说明 A0 续测先放，故本轮未推进。

## 10. 最终交付登记（非自引用）

- 本 JSON/MD 与全部证据在同一次提交里入库；**最终提交号不写进自身提交**。
- 推送后的远端头由 `ls-remote` 回读并逐件比对（工程 blob、manifest、候选 BIN、候选 map、两份交接），登记在回执里；`final_delivery_commit` 字段在本文件中保持 `null` 即为此设计。
