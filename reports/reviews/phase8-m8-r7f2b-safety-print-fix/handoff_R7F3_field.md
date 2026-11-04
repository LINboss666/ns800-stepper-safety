# M8-R7F3 现场轮交接：b502 一次整包执行，新镜像逐字节在板，ENN 两条短行完整

机器可判定版 `handoff_R7F3_field.json`（13271 B，`334172c6…`）。执行根
`field/field-20261104T001444Z/`。批准身份 `M8R7F3-b502-startup-print-20261104`（操作者本人答复后由本机 `--approval-init` 开立）。

## 0 结论

**本轮的四项"必须取得"全部取得，28 条判据 fail=0**（`field/field-20261104T001444Z/FIELD_JUDGEMENT.txt`）：

| A 要求 | 现场结果 |
|---|---|
| 新镜像逐字节在板 | 定长回读 **292708 B**，sha256 `502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a`，与候选 BIN **逐字节相等**（无首个差异位置） |
| 启动 READY / fault=0 | `[SS] state=READY fault_code=0 degraded=0x00`；`[SS] SELF_TEST -> READY (identity epoch=0)`；**无** `FAULT_LATCHED`、**无** `[REQ FAIL]` |
| main 栈 4096 | ps `main … 0x00001000`（=4096 B），max 占用列 40% |
| 实际堆统计 | free `total 32672 / used 22120 / maximum 22120` |
| safety_status 的 ENN 两条完整短行 | `[SAFETY] ENN_FEEDBACK(PC.6 = 板上 TMC_ENN) raw=1 resolved=YES` = **64 B 含 LF**；`[SAFETY] [只读输入, …]` = **95 B 含 LF**。整包 522 B / 10 行 / **0 个裸 NUL** / 严格 UTF-8；两条都与 A 的预测行**逐字节相等** |
| motor_status 静止/未武装/当前禁能合同 | `state=0 name=OTHER`、`armed=0 drv_en_request=0 health=OK`、`cur_hz=0 tgt_hz=0`、`arm_gate_mask=0x18 REFUSED` + gate 明细；无额外业务失败 |
| safety_irq_status 两条 LIMIT 短行与默认关闭 | `LIMIT inputs config = DISABLED_BY_CONFIG (…)`、`LIMIT OFF => PF.14/PF.15 …`；`irq gate = CLOSED`、`protection_ready = NO`；最长行 124 B 含 LF（= A 预测的 124） |

**对照上一场**：同一命令 `safety_status` 在 b424 上是 342 B 且偏移 295 有一个裸 NUL（被 127 钳位裁尾）；本场 522 B、0 NUL、最长 106 B。
**启动流**同样干净：3468 B / 76 行 / 0 NUL / 严格 UTF-8 / 最长 126 B（≤127）。TMC 身份行 `[SS] [REQ OK] TMC2209 IOIN=0x21000041 VERSION=0x21`。
预测对照：三条只读查询的 A 预测行 **7/7、25/25、9/9 全部逐字节出现在真实响应里**；现场多出的只有命令回显与两次 `msh >` 提示符。

## 1 真实消耗（台账为准）

`download 1 / readback 1 / explicit_reset 1 / port_open 1` 各用满，其余全 0；`retry 0`、`tmc_config_apply 0`、
`config_save 0`、`blackbox_clear 0`、`dmm_readings 0`、无 arm/start/learn/zero/commit。
`DISPATCH_SUMMARY|status=OK|stages_executed=4|stages_not_run=0`，台账 `writes=8 pending=0`，
身份三件 `plan 080c2297… / image 502dca5d… / executor 33d3f0af…`（执行器未改）。
令牌 = 操作者两条真实答复逐字拼接（94 B，`0150b36c…`；其中「（推荐）」是我选项标签自带后缀，未替您删改）。

## 2 工具侧失败原文（都未触碰设备，逐条登记）

1. 包装器 `bytes` 字面量含中文 → SyntaxError，未到 subprocess（`spent={} writes=0`）。
2. `str(WindowsPath / "…%s" % STAMP)` 优先级写错 → TypeError，未到 subprocess（同上）。
3. 漏传 `--profile response_driven` → 执行器具名拒绝 `REAL_PROFILE_NOT_RESPONSE_DRIVEN`；
   拒绝发生在模式门（读私有 UID 与起工具之前）。拒绝根目录 `field/field-20261104T001348Z/` 一并入档。
4. 第四次（带 `--profile response_driven`）即本件记录的正式执行。

另：第一版判读脚本自身有两条错谓词（说明行结尾断成 `)`、ps 栈列按十进制 4096 找），曾产出 2 条 FAIL；
修正为"与 A 预测行逐字节相等"与"十六进制 0x1000 解析"后重判 28/28 PASS。**现场字节从头到尾没有变化**，
变的只是我的判据写法——所以两版都在档里可比对。

## 3 本场未证（不许扩大）

SG/ADC/IMU 相对保护（没学习、没阈值、没触发，`protect_ready=NO` 原样）；正常 disarm 收口（没 arm/start）；
真实带载最坏栈深（空载静止，只读到 40% 占用）；`tmc_config_apply` 后的 CONFIGURED 短打印；
镜像内其余 35 条 >127 B 带标签格式串（GF4 继续登记）。
**`flow_complete=True` 与 28 条判据都不等于"保护已验证"**——`core_protection_verified=False`。

## 4 板端终态

VM 供电保持（您本场核对）；显式复位后自由运行，msh 可用；未起转；对 Flash 的写入只有那一次 sector 下载，
回读是读操作。未清日志、未保存配置。

## 5 请 A 判

1. 上述四项"必须取得"是否判为达成，本轮是否关闭"启动 + 打印完整性"这条线；
2. 下一步：进入带一次配置写入的 CONFIGURED 短打印验收，还是直接排 SG/ADC 学习+触发链（后者需要新的具体动作范围与新的批准）；
3. GF4 其余 35 条超长格式串的处置时点。
