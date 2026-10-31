# M8-R2 现场候选包（phase8-m8-relative-protection-r2-FIELD-CANDIDATE）

状态：`NOT_AUTHORIZED / 现场未执行`。本轮全部离线（`hardware_actions=0`），
包里的每个入口都在本机 Windows PowerShell 5.1 上**真实跑过**（假端口/假动作后端），
但没有任何一次连接设备。

## 1. 身份绑定（两个提交分列，必须各自核对）

| 项 | 值 |
|---|---|
| 被复审交付 | `b4d4079c8a2ae22405fa436d4513d0b2807839f8` |
| `software_input_commit`（源码与验证链） | `207b3747efa5d54f7549dca79ececc81c4766794` |
| `tested_image_build_commit`（镜像取自） | `207b3747efa5d54f7549dca79ececc81c4766794` |
| 现场镜像 | `reports/firmware/phase8-m8-relative-protection-r2/rt-thread-motor-m1-candidate.bin` |
| 镜像字节 / sha256 | 290868 / `874df24187c4153058202a4e632ba7ee8eec00a8c7b121d54165a379cd6557e3` |
| 核对方式 | S0 由操作者现场 `Get-FileHash` 重算并要求逐字节相等；清单值只作比对基准 |

`.axf/.map` 内含链接时间戳，因此不宣称跨次构建字节可重放；登记的是本轮每个目标
唯一一次 Rebuild All 的实际产物字节（`build_manifest.json`）。

## 2. 额度表（执行时扣减，不是声明）

| approved_reboot | 1 |
| blackbox_clear | 0 |
| config_save | 1 |
| dmm_readings | 0 |
| download | 1 |
| explicit_reset | 1 |
| flash_readback | 1 |
| irq_polarity_prep | 2 |
| motion_cap_seconds_per_run | 5 |
| motor_run | 3 |
| motor_run_seconds_total | 15 |
| physical_collision | 0 |
| port_open | 4 |
| retry | 0 |
| tmc_config_apply | 2 |

执行语义：`exec_session` 在**发送那条命令之前**扣额度；不足 => `QUOTA_EXHAUSTED` 且那条命令
不会被发出；计划里没有的动作 => `QUOTA_UNDECLINED`。已用额度随结果里的 `quota_used` 披露，
重启后需要重新证明的写（`tmc_config_apply` / `safety_irq_enable`）逐笔计入。

## 3. 阶段顺序与停点

| S0 | offline | 否 | 离线绑定 |
| S1 | action | 是 | 0 步 / 1 动作 |
| S2 | action | 是 | 0 步 / 1 动作 |
| S3 | serial_session | 否 | 11 步 / 0 动作 |
| S4 | serial_session | 否 | 12 步 / 0 动作 |
| S5 | approved_reboot | 是 | 0 步 / 1 动作 |
| S6 | serial_session | 否 | 5 步 / 0 动作 |
| S7 | serial_session | 否 | 12 步 / 0 动作 |

停点语义：`strict`（现场 RunField 默认）到停点即停，继续下一阶段需要新的一次批准；
`full` 只用于离线自检，目的是完整看到 download/readback/reset/reboot/port/start/stop 的调用序列。

## 4. 串口与执行纪律

1. 提示符按 P0 已证的 `msh >` / `msh />` 识别；缺提示符 = `MISSING_RESPONSE_PROMPT`，
   有字节但没提示符**不再**被当成完整响应；非法 UTF-8 字节 = `ILLEGAL_BYTES_IN_RESPONSE`
   （不做 `replace` 清洗后收数）；权威字段必须恰好一条 = `FIELD_NOT_EXACTLY_ONE`。
2. 原始字节先落盘并 `fsync`，判读只读落盘内容；台账独占创建，撞名即 `LEDGER_PREEXISTING`。
3. 开口时一次配齐，开口之后不写任何串口属性（有 AST 静态判据；本轮实测
   `F05_STATIC_PORT_DISCIPLINE|PASS`）。
4. "响应完整"与"业务成功"分两层：`FAILED / REFUSED / VOIDED / not supported` 一律业务失败；
   具名拒绝（`allowed_outcomes`）只是停止，之后不得继续 `commit / save / start / arm / fault_reset`。
5. 起转预算：`motor_start` 的**发送尝试**即建立 5 s 单调硬截止，之后的阻塞写、读、观察窗、
   人工等待、learn 只能用剩余预算；到点必做 `motor_stop -> motor_disarm -> motor_status`
   并核三条权威字段（`state name=IDLE`、`armed=0 drv_en_request=0`、
   `verdict ∈ {OK, CUT_BY_HARDWARE}`），证不到就是 `disarm=UNKNOWN / terminal=UNKNOWN`。
   主机侧截止是调度约束，串口失效不能保证物理停机时延 —— 操作者保留 SW1/断电应急制止。
6. 无触发是 `OBSERVATION_NO_TRIGGER` 路径：先 stop+disarm 确认后结束，绝不执行需要真实故障态的
   `fault_reset` / 故障态 `motor_arm` 分支。
7. 人工门：非 stub 传输下**绝不**读夹具里的 manual/flash_state/responses；必须现场新输入并计
   `operator_calls`。业务/静止/armed 一类前置以本次完整响应的权威字段确证，不靠口头 yes。
8. 动作后端三选一同一接口：`sim`（离线，记录调用序列，真实身份门显式 `NOT_EXERCISED`）、
   `manual`（打印逐字命令，等操作者交付 rc/日志/回读件，缺件即 `ACTION_DELIVERABLES_MISSING`）、
   `real`（subprocess 跑固定 argv，原始 stdout/stderr 落盘，非 0 => `ACTION_NONZERO_RC`）。
   本轮只执行过 `sim`。

## 5. 参数事实（C4）

- 学习窗 `diag_profile learn 16 80 120 0`：LO/HI 是 **STEP 频率 Hz**（`hz_low/hz_high`），
  `hz_nominal` 由产品取窗中值 = 100 Hz；此前"换成 M4 的 SG 码"的备注是单位错误，已作废。
- 100 Hz + [80,120] 的依据：`reports/reviews/phase8-motor-chain-dev/m4/m4_result.txt` 记录
  M4 R1-R4 各以 100 Hz、每段 5 s 在板上运行通过；不再留"待手改占位"。
- 方向：静止状态下先 `motor_dir 0` 再建立单向 profile（`dir_mask` 只含一个方向）。
- 电流：保持 IRUN20，不抬流；学习窗口不因阈值需要而放宽。
- 新烧录/重启后的前置需重新证明：`system_status`、`safety_status`、`tmc_config_show`、
  `tmc_status` 只读 -> 需要的 `tmc_config_apply` -> `safety_irq_enable`/极性确认 -> 只读复核。
  R1 的 F-11 把所有 `tmc_config_apply` 禁掉，导致所需前置无法建立，本轮改为额度受限的正常 apply。
- 重启后的 profile 恢复检查比对本轮已提交的 id/rev/abi/flg/signature match 与关键参数；
  不因夹具写 absent 而跳过，也不能只找 `[DIAG] rel ` 行首。
- 受控加载缺失 => `load_step.verdict = NAMED_PREREQUISITE_MISSING`；R6 非破坏激励未选择 =>
  `FIELD_NOT_TRIGGERED`。真实触发时按 on_trip 分支读回 first-cause 与本轮新增日志（复用 M5 读回链）。

## 6. 本轮离线执行到的判据（可复算）

- `-SelfTest`：SELFTEST_VERDICT|PASS|checks=21|failures=
- `-Precheck`：PRECHECK_VERDICT|PASS|failures=
- `-RunField`（stub/sim，strict 停点）：只调度到 S1 停点即停，动作序列与逐阶段结果落盘
- Python 场景与 PowerShell 检查的完整台账：`host/field_python_selftest_console.txt`、
  `host/field_SelfTest_console.txt`、`host/field_Precheck_console.txt`、
  `host/field_RunField_console.txt`、`field/offline/**`（清单见
  `host/field_offline_run_manifest.json`）

## 7. 复现命令（Windows PowerShell 5.1；UTF-8 控制台）

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
cd D:\ns800\reports\reviews\phase8-m8-relative-protection-r2\field
powershell -NoProfile -ExecutionPolicy Bypass -File .\M8R2Field.ps1 -SelfTest
powershell -NoProfile -ExecutionPolicy Bypass -File .\M8R2Field.ps1 -Precheck
powershell -NoProfile -ExecutionPolicy Bypass -File .\M8R2Field.ps1 -RunField      # 只到 S1 停点
```

现场执行需要新的批准与 `-ApprovalToken`；`-Transport real` 在没有令牌时直接
`RUNFIELD_VERDICT|REFUSED_REAL_TRANSPORT_TOKEN`（本轮从未使用 real/manual）。
