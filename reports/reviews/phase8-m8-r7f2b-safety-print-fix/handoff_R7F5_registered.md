# 交接回执：R7F5 盲测静态 IRQ 试验包登记（设备动作 0）

- 轮：`R7F5-D`（登记轮，纯离线）
- 日期：2026-11-04
- 被审件：`NS800_R7F5_Blind_IRQ_Static_Trial_20261104.zip`，60623 B，SHA256 `b326cd8543eec1db232ec5781c03b7fae721eeaf1ef6af6640bc97321a789a2c`，33 条目
- 基线：`D:/ns800` @ `0cd64587739cca41a22903e075b6fe37b17c765b`（= 计划里的 `reviewed_delivery_commit`，两者相等）
- 结论：**包接受，可进入现场执行，但停在批准门**（见 §6）

## 1. 身份与原件（全部我这边重算，不采信自报）

| 项 | 声明 | 我复算 |
| --- | --- | --- |
| 内层清单 | 32 条 | 32/32 OK，清单不含自身 |
| `IDENTITY.json` 落位件 | plan `ee3b2651…` / rows `4f094de3…` | 逐字节相同，已按相对路径放入 `field/` |
| 执行器 | R6C 原件 `33d3f0af…` | 仓库现件逐字节相同（未改） |
| b502 镜像 | 292708 B `502dca5d…` | 磁盘字节相同，且与计划绑定字段一致 |
| `software_input_commit` / `tested_image_build_commit` | `99414c7a` / `70deff80` | 两个提交在仓库里真实存在 |
| 产品源码 | 自 `0d8239f` 未变 | `git diff 0d8239f..HEAD -- rt-thread-master` 为空 |

## 2. 离线判据复算（24 项 PASS，fails=0，`offline_r7f5/`）

- **步骤摘要**：用未改执行器 `canonical_steps_sha(stage_steps(S1,"response_driven"))` 重算 = `4a80dcb6dd43028c…`，与 A 声明逐字符相同。
- **S0 绑定**：除绝对路径外逐字段一致；`needles_total=40`、`plan_placeholders=0`、`plan_forbidden_hits=0`。
- **validate_plan**：57 项、fail=1，唯一 FAIL 就是已登记的 `PLAN_MOTION_CAP_BUDGET`；我的 57 行输出与 A 的落盘件**逐行相同**。
- **8 个整包回放（正常 1 + 反例 7）**：状态、阶段序列、额度实耗逐例与 A 一致。
  正常场景只消耗 `port_open=1 / tmc_config_apply=1 / irq_polarity_prep=2 / safety_irq_disable=1`；
  故障前置**没发** apply；DIAG 已活动**没置标志也没开门**；台账里**没有任何** `motor_arm`/`motor_start`。
- **rows 的真实/预测分区**：7 条标为真实旧回包的条目，用 `split_rows` 重切仓库归档原件后**逐行相等且零 NUL**；
  其余 12 条是 source_shape 预测，已按 `kind` 分开登记，**不充当本轮现场证据**。
- **三条新命令 shape**：行长度与 A 一致，最长 124 B（含 LF），≤127 B 门限内，无 NUL。
- **源码侧核对**：shape 断言用到的 11 条字符串（含 `(operator must have MEASURED these)`、
  `MARKED VALIDATED`、`NO (IRQ gate still CLOSED)`、`detach: … detached (%d)`、
  `OPEN (EXTI registered)` / `CLOSED (polled only)`、`operator CONFIRMED` / `NOT confirmed`）
  逐条在 `safety_thread.c` 里存在；`supervisor.c:93` 的三元组确实是 `"CONFIRMED" : "not-confirmed"`（大写）。

## 3. A 的 host 桩形状，我用本机 C 编译器重跑了一遍

编译器：`C:/Program Files (x86)/Dev-Cpp/MinGW64/bin/gcc.exe`，参数与 A 一致（`-std=c99 -Wall -Wextra -Werror -Wno-misleading-indentation`），编译返回 0，六组模式重跑。两处口径如实登记：

1. **行尾**：我这边的 stdout 带 CRLF（Windows C 运行库文本模式），A 的落盘件是 LF（Linux 工作区）。
   原始字节**不相等**；把 CRLF 归一成 LF 后**六组逐字节相同**（CR 数 9/12/2/9/2/9，与行数一致）。
2. **比对基准**：host 桩只打负载行，而交付 rows 的 `safety_irq_status@*` 还含命令回显与行末 `msh >`
   （那两条来自真实串口捕获）。所以逐行比对只取 `[SAFETY]` 开头的负载行，被排除的行原样登记在
   `host_shape_recompile.json` 的 `delivered_rows_excluded_from_compare`，没有静默过滤。

形状证据的边界照 A 的口径保留：**只证形状与流程，不证中断交付、板端时序或物理极性。**

## 4. 本包固定的动作上限（照计划登记，不视为授权）

`port_open=1`、`tmc_config_apply=1`、`irq_polarity_prep=2`（极性置位 1 + enable 1）、`safety_irq_disable=1`；
下载/回读/显式复位/arm/start/运动/学习/零点/保存/清日志/fault_reset/注入信号/重试 **全部 0**；
`forbidden_commands` 12 条且没有任何一步使用它们；总时限申报 90 s（步骤边界检查，不是任意时刻强制断电）。
操作者现场供断电**一对，上限 1**，属执行器无法证明的人工作业，单独登记。

## 5. 必须保留的例外（固定记录）

`polarity_measured=false`、`trial_only_software_flag_override=true`、`core_protection_verified=false`。
`safety_polarity_confirm CONFIRM` 本次只置 RAM 标志，不测量；源码那两条
`operator must have MEASURED` / `MARKED VALIDATED` 原样保留，不得把 CONFIRMED、`protect_ready=YES`
或 `mask=0x0 ALL-PASS` 写成物理极性/完整保护已证。**注意**：第 10–11 步门全绿意味着固件此刻会**接受**
arm/start——本包靠步骤清单与 `forbidden_commands` 挡住，不靠固件自身拒绝；收口后由操作者切断 VM+MCU
供电清掉易失标志。

## 6. 停点：等操作者本人回答（我不代填）

1. 是否按上面这个固定范围批准**一次**执行（新批准身份 `M8R7F5-b502-blind-irq-static-20261104`；
   旧批准与剩余额度一律不结转；令牌必须用你本次的真实原话）。
2. 现场事实：MCU 与 VM 现在是否断电、接线/机械装配与上次是否一致、
   **DAPLink/COM5 通路是否已恢复在位**（上一轮诊断时探针不在位，COM5 是 DAPLink 的 CDC 口）。
3. 是否接受 §5 那条"只置软件标志、不实测"的临时例外，以及无论成败结束后切断两路供电。

## 7. 本轮之后仍未验证

DIAG 物理极性与两态电平、实际故障触发与恢复、SG/ADC/IMU 通道、arm/disarm 与运动、带载最坏栈、
中断是否真被投递。本轮结论最多到"静态注册与关门流程的形状与流程一致"。

## 8. 提交前预检与一条历史披露

暂存 blob 预检（`tools/r7f5_staged_preflight.py`）：本轮清单 146 条，登记值 == 暂存 blob == 磁盘字节，
problems=0；两项自检均 FIRED（故意写错第一条必须被报；三类泄漏样例必须全命中，且 64 位 sha 不得被当成 48 位 UID）。
本轮 47 个暂存件的泄漏扫描命中 0。

如实登记一条**历史**命中（不是本轮引入，也不在本轮改动）：
`offline/package_integrity.txt` 与 `offline/package_integrity_r7f4.txt` 的第一行
记录了当时交付 zip 的本机绝对路径，其中含用户名目录段。这两件是已推送的冻结证据件，
本轮按"失败与历史字节不删不改"的纪律**不回改**；后续新轮的取证件一律只记文件名与散列，不记绝对路径。
若要清理需另开一轮并在提交信息里写明是对历史件的改动（会破坏"历史字节未动"这一条，需操作者决定）。

## 9. 复算入口

```
python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5_registered_checks.py \
    --zip <A 交付 zip> --repo <仓库根> --box <仓库外工作区> \
    --out reports/reviews/phase8-m8-r7f2b-safety-print-fix/offline_r7f5
python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/gen_handoff_r7f5_registered.py \
    --repo <仓库根> --zip <A 交付 zip>
```
