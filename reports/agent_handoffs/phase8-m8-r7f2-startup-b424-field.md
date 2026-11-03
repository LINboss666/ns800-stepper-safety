# M8-R7F2 b424 一次启动现场验证 · 审核交接（已执行，额度用尽，S3 按判据 STOP）

日期：2026-11-04 00:17 +0800。执行头：`3e6a02c`（A 签收头）。
输入：A 的 `NS800_R7F1A_A_GO_and_B424_Startup_Trial_20261103.zip`
（zip 原字节 `743c5f8066df912e31b15feda641cc1a52a3328c27146f47c019ef0202b9b993`），
技术裁定 `R7F1A_C_AND_BUILD_REVIEW = GO_FOR_ONE_BOUNDED_STARTUP_TRIAL`。
操作者批准原文逐字存于 `…/field/field-20261103T161728Z/APPROVAL_TOKEN.verbatim.txt`（未改写）。
批准身份 `M8R7F2-startup-b424-20261103`，账本 `D:\ns800_field_approvals\approval_M8R7F2-startup-b424-20261103.json`。

## 1. 执行结果（一次 dispatch，走到 STOP 即停）

| 阶段 | 结果 | 关键证据 |
|---|---|---|
| S0 绑定 | OK | plan `fa333186…`(23218 B) / EXEC `33d3f0af…`(219491 B, R6C 原件) / image `b4243886…`(292684 B) 三件与计划声明逐字相符；needles 40 条、占位符 0、禁词 0 |
| S1 下载 | OK | `action_download/rc.txt=0`，一次 sector 下载 |
| S2 定长回读 | OK | `readback.bin` 292684 B，**与归档 BIN 逐字节相等**，两边 SHA 相同 → **b424 已被证明在板** |
| S3 启动流+查询 | **STOP `FIELD_VALUE_MISMATCH`** | `state=FAULT_LATCHED`(要 READY)、`fault=8`(要 0)；`ps`/`free`/`system_status` 已取，`safety_status`/`motor_status` 未发 |
| S4 及以后 | 无 | 本包不含 apply/运动 |

额度实耗：download 1/1、readback 1/1、explicit_reset 1/1、port_open 1/1，其余全 0；
`commit_failures=[]`、`pending=0`、`cleanup_errors=[]`、`disarm=NOT_ARMED`、`PORT_CLOSED t=77.359 s`（75 s 申报窗走满）。未重试、未追加动作。

## 2. 这一场真正买到的东西：失败分支换了，而且可读

```text
[SS] [REQ FAIL] TMC2209 IOIN rc=-117 reason=rx-timeout-echo
[SS] [REQ FAIL] TMC2209 IOIN tx=4 rx=4 flush=0 used=100
[SS] self-test done: required=FAIL degraded=0x00
[SS] ==> FAULT_LATCHED code=8 (DRV_ENABLE LOW verified)
```

- 现场读到的**不是** `lock-busy`：这笔 IOIN 读已经取到传输锁、真的发出 4 B、收回来 4 B 是自家回显、
  在 100 tick（≈100 ms 应答超时）上到期。上一场只有一个 `rc=-23`，这一场是 `rc=-117` 且带阶段计数
  —— 两场**不同因**，任务书 §2 要的"逐调用可区分"在板上成立。
- 下一处该查的是 TMC UART 的收发通路本身（回显进来、芯片没应答），而不是继续动启动顺序。
  本轮不动手，等 A 按这条现场分支出下一步任务书。
- 门没有误伤：`[SNS] sensor thread started (100Hz, prio 7)`、`[SS] [OK] IMU`、`[SS] [OK] ADC`、
  `[SS] [OK] Flash (JEDEC EF 40 17)` 都在。`[TMC] DEGRADED drive_cfg=UNCONFIGURED`、
  `[ADC] DEGRADED cal=THEORETICAL` 与该分支一致。
- 边界（不得换算成通过）：单次冷启动样本**不能**证明锁竞争在任何时序下都不会发生，只能证明这一次没发生；
  门"关闭期不发 SG UART 读"这个直接可观察量板上没有打印，本场只给间接证据。

## 3. main 栈与堆（复审 §1 的现场判据）

```text
ps   : main  10  suspend  0x0000018c  0x00001000  40%  0x00000003 EINTRPT  0x20012050
free : total 32672   used 22120   maximum 22120   available 10552
```

`main` 线程栈现场就是 **0x00001000 = 4096**；`[BOOT]` 一路打到 `11 startup-selftest FAIL` 与
`bootstrap complete`，说明堆里那次 4096 B 分配成了、main 线程跑到了打印处。
`maximum == used == 22120` 说明这次会话之后没有再涨过堆，所以这两个数只描述本次启动的形状，
不是"堆够用"的一般性证明。

## 4. 三处打印与字节完整性（任务书 §4 的现场验收）

启动流 4100 B：**NUL=0**、strict UTF-8 解码通过、84 个换行、末段为空（每条都以换行收尾）。
按 `\n` 切行的各条长度（不含 CR）：

| 家族 | 条数 | 字节 |
|---|---:|---|
| `[MOT] init OK` | 2 | 58, 94 |
| `[SS] STEP:` | 3 | 56, 85, 82 |
| `[SS] ==> FAULT_LATCHED` | 3（+ 1 条 `整理身份` 41 B） | 55, 62, 43 |
| `[SS] [REQ FAIL] TMC2209 IOIN` | 2 | 59, 55 |

全部 ≤120 B 且完整。整段唯一超 120 B 的行是登记未改的 `[MOT]   注:`（内容 125 B、含换行 126 B），
**没有被截断**——它后面紧跟 `[BOOT] 7 motor-service ... OK`。这现场证实复审 §5 第二条更正：
"126 B"不等于"已截断"。

**打印覆盖按可达分支说**：本场走 required-FAIL → FAULT_LATCHED，所以 READY 路径与 `!! STEP 关断未确认`
那条故障分支本来就不会出现；未出现不等于已被现场验证，也不为了看打印去造故障。

## 5. 判定与未验证项

- 成立：b424 在板（逐字节）；启动自检具体分支现场可读（`rx-timeout-echo` + `tx/rx/flush/used`）；
  三处打印族现场完整无 NUL；main 栈现场 4096 且分配成功；IMU/ADC/Flash 三项自检照旧 OK。
- 不成立：`READY/fault=0`（本场 FAULT_LATCHED/fault=8）；`product_field_fix`；SG/ADC 与 IMU 相对保护的
  现场有效性；带载保护；启动锁竞争被普遍消除。
- 现场终态：电机未 armed、`DRV_ENABLE` 禁能侧、`irq_gate=CLOSED`、`arm gate fail mask=0x1A`（arm 仍被拒）。
  四个额度全部用尽，本包到此关闭。

## 6. 一处需要登记的执行器输出怪癖

`DISPATCH_SUMMARY` 里同一个 `S3` 既以 `status=STOP` 打印，又在末尾被列进 `stages_not_run=[S3]`。
按采集件（boot/ps/free/system_status 四份，00:18→00:19）与台账判定，串口会话只跑过一次、
额度只扣一次。本轮不改执行器，只把这条记下来，避免下一个人把它读成"跑了两次"或"两笔 port_open"。

## 7. 可复算来源

- 现场原件（原始字节，不删不改）：`reports/reviews/phase8-m8-r7f1a-minimal-c-patch/field/field-20261103T161728Z/`
  （`readback.bin`、`S_S3/*.rx.bin(+.txt)`、`action_*/`、`dispatch_ledger.jsonl`、`stdout_dispatch.txt`、
  `dispatch_start.txt`、`APPROVAL_TOKEN.verbatim.txt`、`OFFLINE_JUDGEMENT.txt`）
- A 的 GO 与三件执行输入：同 `field/` 下 `plan_startup_b424_reviewed.json` / `rows_startup_b424.json` /
  `manifest_binding_b424.json`，GO 正文 `a_review_R7F1A_A_GO_and_B424_Startup_Trial.md`
- 镜像与构建：`reports/firmware/phase8-m8-r7f1a-minimal-c-patch/`
- 离线侧记录：`…/run_record.md`（3e6a02c 时点，未改写）、交接 `phase8-m8-r7f1a-minimal-c-patch.md/.json`
- 私有 UID 只以脱敏形式出现在台账里：`@UID_FILE<redacted len=48 sha256_d2f530dbd062>`
