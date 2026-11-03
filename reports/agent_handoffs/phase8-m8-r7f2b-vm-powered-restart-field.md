# M8-R7F2b VM 上电一次启动续测 · 审核交接（已执行，S3 按字节门 STOP，本场额度用尽）

日期：2026-11-04 00:38:50 +0800（UTC 目录 `20261103T163850Z`）。执行头：`0fe28aa`（上一场证据头）。
输入：操作者的两条原话（逐字存于 `…/field/field-20261103T163850Z/APPROVAL_TOKEN.verbatim.txt`）——
"我授权你再重新跑一次 TMC不工作是因为我没有给VM供电" + "VM 已上电，接线无其它变化"。

**这份续测包（计划 + 额度）是本地 agent 自己拟的，没有云端 A 的复核章。** 上一场 A 审定的
4 额度整包（`M8R7F2-startup-b424-20261103`）已 4/4 用尽并关闭，不结转；本场新批准身份
`M8R7F2b-vm-powered-restart-20261104`，账本 `D:\ns800_field_approvals\approval_M8R7F2b-vm-powered-restart-20261104.json`。

## 1. 本场改了什么、没改什么

从 A 的已审定原件（`plan_startup_b424_reviewed.json`，23218 B，`fa333186…`，本场未改写）派生：

| 项 | A 原件 | 本续测包 |
|---|---|---|
| 阶段 | S0 → S1 下载 → S2 回读 → S3 会话 | **S0 → S3**（S3.requires 改为 `["S0"]`） |
| download / readback | 1 / 1 | **0 / 0**（b424 已被上场逐字节证在板，重烧不新增信息） |
| explicit_reset / port_open | 1 / 1 | 1 / 1（不变） |
| tools 三件 argv、S3 六步、expect_values、75 s 窗、needles、rows、observation_bounds、identity_binding | — | **逐字未改** |

新计划 `plan_startup_b424_vm_restart_agent.json` 26940 B，SHA `4404cfd1a043e7b4653aeabe7d27feb3e73a74b5c6b34f564e8e454ad4171a3a`。
派生差异与"为什么保留 download/readback 的 argv 却不执行"写在计划内 `continuation_derivation` 段
（离线门 `PLAN_TOOL_ARGV` / `PLAN_IMAGE_IDENTITY_CONSISTENCY` 要求三件 argv 存在且同源；
本轮没有阶段引用它们，额度 0，运行时真去发会先撞 `QUOTA_UNDECLINED`/`QUOTA_EXHAUSTED`）。
另：`identity_binding.board_image_before` 仍登记 5ae（A 的原值，本场**没改**，因为改它会牵动那条离线门），
真实在板件写在旁字段 `continuation_on_board_proven`。

离线门（0 设备动作）：`--validate-plan` 32 项 = 31 PASS + 1 FAIL，唯一红项
`PLAN_MOTION_CAP_BUDGET|caps=[] sum=0 total=0 starts=0`，沿用 A 的固定"无运动包 N/A"裁定 —— 不说成 32/32。
真实执行前一次 sim 干跑（stub+sim+healthy，故意不带 `--approval-id`）rc=0、`APPROVAL_LEDGER|NONE`，
输出留在仓外 `D:/ns800_r7f1_scratch/sim-cont-b424-vm-20261103T163657Z`。

## 2. 执行结果（一次 dispatch，走到 STOP 即停）

```text
S0 绑定            OK      三件 SHA 与计划声明逐字相符；needles 40、占位符 0、禁词 0
S3 复位            OK      action_reset/rc.txt=0（sysresetreq，唯一一次工具调用）
   75 s 被动启动流  3468 B  NUL=0、UTF-8 有效、最长行 125 B
   ps / free / system_status   OK  四条硬断言全过（见 §4）
   safety_status    STOP    NUL_BYTE_IN_RESPONSE bytes=1 first_offset=295 → 342 B 原样入库
   motor_status     NOT_RUN 不补测
额度实耗           explicit_reset 1/1、port_open 1/1、download 0/0、readback 0/0、其余 21 键 0/0
账本终态           spent={'explicit_reset':1,'port_open':1} pending=0 writes=4 commit_failures=[]
                   上一场两本账未被读写（仍 spent 4/4、writes 8）
会话事件顺序       PORT_OPEN_ATTEMPT → PORT_OPEN(COM5) → 复位 rc=0 → BOOT_STREAM_DRAINED 3468
                   → RX_DONE 1192/90/983 → sysboot BUSINESS_OUTCOME → NUL_IN_RESPONSE → SESSION_STOP → PORT_CLOSED
```

## 3. 本场真正买到的：IOIN 读通了，而且和上一场构成单变量 A/B

```text
[SS] [REQ OK] TMC2209 IOIN=0x21000041 VERSION=0x21
[SS] self-test done: required=PASS degraded=0x00
[SS] SELF_TEST -> READY (identity epoch=0) / state=READY fault_code=0
[BOOT] 11 startup-selftest OK -> state=READY / [MAIN] boot complete: READY (motion still gate-locked)
```

这条打印在产品里只有一条路径（`safety_state.c:683-686`）：init 成功 → `read_register_stat(IOIN)` 返回
`RT_EOK` → `tmc2209_ioin_is_identity()` 为真。所以现场证的是"**拿到了 CRC 通过的应答并且身份判据通过**"。

同一枚 b424、只有 VM 供电状态不同的两场逐行对照（`boot_stream_diff_vs_prev_session.txt`）：
只在 VM-off 场出现的 22 行含 `rc=-117 reason=rx-timeout-echo tx=4 rx=4 flush=0 used=100`、
`required=FAIL`、`FAULT_LATCHED code=8`、`[Motor] state=4`、`[TMC] DEGRADED`、
`write gate mask=0x3A`、`arm mask=0x1A` 与一次 `[BB] session 1 written (113 frames)`；
只在 VM-on 场出现的 13 行是上面那组 READY 侧的对应行；其余 58 行两场相同。

四条不许越过的边界：
- 成功路径**不打印 reason**（`safety_state.c:671-675` 只在失败分支打印）。本场看不到 `reason=ok` 是设计如此，
  不能当成归因字段失效，也不能反过来当"已验 reason=ok"。
- `[BOOT] 4 hw-services tmc=OK` 只是 `tmc2209_init()`（开 UART + 建锁，`tmc2209.c:1223-1239`），不含应答；
  应答证据只有 stage 11 那条 IOIN 行。
- IOIN 身份判据是本项目 `tmc2209.h:93-96` 的 `(ioin>>24)&0xFF==0x21`；本轮未对照厂商手册复核该字段位置。
  跨场旁证：历史真实读值 `0x21000041`（TMC 根因轮 300/300）与 `0x210002C1` 高字节同为 0x21。
- `dmm_readings=0`，没测供电电压。结论级别到"**与操作者解释一致的现场证据**"，不是"供电线路已被直接测量证明"。

## 4. main 栈 / 堆 / 只读查询原值

```text
ps   : main  10  suspend 0x0000018c 0x00001000  40%  0x00000014 EINTRPT 0x20012050
free : total 32672  used 22120  maximum 22120  available 10552      —— 与上一场逐字相同
system_status 四条硬断言全过：bootstrap-complete=YES abort-stage=0 / Safety state=READY fault=0 /
              request=LOW pad=0 expected_ENN=HIGH ENN_fb=1(resolved) verdict=OK /
              Motor state=0 cur=0 tgt=0 armed=0 health=OK
未变的既有事实：protect_ready=NO、polarity=not-confirmed、arm gate fail mask=0x18、
              ADC DEGRADED cal=THEORETICAL、[TMC] OK drive_cfg=UNCONFIGURED
```
栈大小 4096 与堆四项是**快照**，不是最坏栈余量证明；`ps` 的 error 列语义本轮不解释，只登记原值。

## 5. 首错的字节级定位（新缺陷 1 条，产品打印宽度）

`safety_status` 第 4 行 `[SAFETY] ENN_FEEDBACK(PC.6 = 板上 TMC_ENN) …` 按源格式串
（`safety_gpio.c:450-452`）+ 本场实测值展开是 **150 B 正文**，超过 `RT_CONSOLEBUF_SIZE-1 = 127`；
`kservice.c:409-415` 把发出长度钳到 127 ⇒ 现场实际发出 **126 B 正文 + 1 个 NUL**，并丢掉本行 CRLF，
下一行 `[SAFETY] request=…` 紧接在 NUL 之后。捕获前 126 B 与源展开**逐字节相等**。

不是本场引入的回归，三条独立依据（详见 `safboot_truncation_analysis.txt`）：
1. `git blame -L 448,453` 实证这三行属 `c8d9719f`（2026-10-21），本场/上场都没动 `safety_gpio.c`；
2. 同一形状在 M3 的真实板捕获里就有（`phase8-motor-chain-dev/m3/irq.bin.b64` 解码流，126 B 正文 + NUL）；
3. 之前没判红是因为裸 NUL / 非法 UTF-8 硬门是执行器 R6-4 才加的，且上一场在 `system_status`
   就按 `FIELD_VALUE_MISMATCH` 停了，根本没走到 `safety_status`。

顺带登记夹具缺口：`rows_startup_b424.json` 的 `safety_status/healthy` 这条是"截断但**无** NUL"的 125 B 版本
（其 `_provenance` 自己写明来自被 128 B 截断的历史板捕获并按内容拆分）⇒ 离线 sim 这一步的绿不代表真实字节。

## 6. 给 A 的下一步（本轮不动手）

1. 产品侧一条最小改：把 `safety_gpio.c:450-452` 那条 150 B 行按已用过的口径拆成两条 ≤127 B 短行，
   并同步 `rows` 夹具为真实字节；只动打印，不动判定逻辑。是否做、以及要不要为它再上一次板，请 A 定。
2. `motor_status` 仍未取到（本场 NOT_RUN，额度用尽，不补测）。下一场若继续，需要**新的明确批准**，
   且不能援引本包或上一场的额度。
3. 主线目标仍未证：SG/ADC 阈值建立、学习/提交、SG/ADC/IMU 目标通道触发的相对保护（`protect_ready=NO` 未变）、
   以及 TMC 写到达凭证（IFCNT+1）。
4. 另登记一条过期注释（本轮未改）：`tmc2209.c:1250` 仍写 `RT_MAIN_THREAD_STACK_SIZE(2048 B)`，
   自 `3e6a02c` 起实际为 4096。

## 7. 板的终态

镜像仍是 b424（上场逐字节证在板；本场未重烧、未回读）；复位后跑到 READY；DRV_ENABLE LOW、
request LOW、armed=0、未起转；`halt_after=false`，本场没有把核停在 halt；COM5 已关闭。

## 8. 证据索引（全部相对仓库根）

```text
field/field-20261103T163850Z/            本场原始证据 = 9 份文件 + 2 个子目录:
  9 文件: stdout_dispatch.txt / dispatch_start.txt / APPROVAL_TOKEN.verbatim.txt / OFFLINE_JUDGEMENT.txt /
          validate_plan_offline.txt / byte_integrity_scan.txt / boot_stream_diff_vs_prev_session.txt /
          safboot_truncation_analysis.txt / dispatch_ledger.jsonl
  S_S3/  10 件: 01_rst.boot / 02_threads / 03_heap / 04_sysboot 各 rx.bin + rx.bin.txt,
               05_safboot.rx.bin(只有原字节, 没有 .txt —— 解码视图由执行器在响应被接受之后写,
               m8r3_serial_chain.py:2364-2367 emit_text; 本条在字节门就停了) + ledger.jsonl
  action_reset/ 4 件: ARGV.json(UID 已脱敏) / rc.txt=0 / stdout.bin / stderr.bin
field/plan_startup_b424_vm_restart_agent.json   本场计划(agent-authored, 4404cfd1…)
field/plan_startup_b424_reviewed.json           A 的原件, 本场未改写(fa333186…)
field/field-20261103T161728Z/                     上一场(VM off)原件, 原样保留
```
