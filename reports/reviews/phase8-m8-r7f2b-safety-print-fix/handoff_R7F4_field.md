# M8-R7F4 现场回执：一次真实配置成功，八条 CONFIGURED 短行完整，写后仍是禁能

机器可判定版 `handoff_R7F4_field.json`。现场根 `field/field-20261104T015928Z/`（原始字节全在，不删不改）。

批准身份 `M8R7F4-b502-configured-20261104`。令牌 = 操作者本人本轮两条逐字答复（82 B，`98588b80…`，值不外传）；
现场前置核对答复为"供电在、接线无未说明变化"。台账 `D:/ns800_field_approvals/approval_M8R7F4-….json`
开账时 `spent` 全 0、终态 `spent={port_open:1, tmc_config_apply:1}`、`pending=[]`。
（`writes=4` 是台账落盘次数，不是寄存器写次数——这个字段证不了寄存器动作，我不拿它当动作计数。）

## 1 实际执行的动作（与批准范围逐项对齐）

`--dispatch --transport real --backend real --profile response_driven` 一次跑完，`DISPATCH_SUMMARY status=OK`、
`stages_executed=2 / stages_not_run=0`。真实消耗 **COM5 开口 1、`tmc_config_apply CONFIRM` 1**，
下载 / 回读 / 复位 / arm / start / 运动 / 学习 / 零点 / 提交 / 保存 / 清日志 / 开 IRQ / 极性确认 / 重试 **全 0**，无重试。
本场不调用 DAP，所以未读 UID 文件（执行器的 `@UID_FILE` 只在 argv 模板用到时才需要）。
会话末事件时刻 `t_s=0.610 s`，远低于 90 s 硬截止；本场无启动窗，也未申报启动观测窗。

步骤身份不是靠标签：我用未改动执行器自己的 `stage_steps + canonical_steps_sha` 从仓库里的计划重算出
`11d42060d97de6b6…`，与现场台账记的 `steps_sha256` 相同；六份响应原文的首行回显（从字节读出）也与计划声明的
`system_status→motor_status→apply→system_status→motor_status→safety_status` 同序一致。

## 2 这一场证明了什么

- **一次真实配置成功**：`[TMC-CFG] CONFIGURED (各次写入均经 IFCNT 确认)`，且响应里没有 `REFUSED`。八条 `[TMC-CFG]`
  完整短行全在，最长 **62 B 含行尾**（响应共 388 B / 8 内容行），**零裸 NUL、严格 UTF-8 完整**。
- **计划参数就是这批值**：`irun=20 ihold=8 dly=4 tpd=8 vsense=1`；按位域换算 `IHOLD_IRUN=0x00041408`、
  `TPOWERDOWN=0x00000008` 与计划十六进制一致。这仍是固件打印的十进制参数换算，**芯片里的这两个寄存器本场没有单独读回打印，
  电流也没测（打印原文自己写着"电流值仍为理论计算, 未实测"），所以不算电气实测**。
- **配置状态真的翻转了**：`drive_cfg` 由 `UNCONFIGURED` → `CONFIGURED`，`gate5 tmc_drive_cfg=CONFIGURED`；
  `arm_gate_mask` 由 `0x18` → `0x8`，剩下的唯一一条拒绝就是 `gate_reason=protection-not-ready`。
  `CONFIGURED` 与 `protect_ready=NO` 同时成立 —— 这正是 A 预告的预期结果，**不是失败，也没有为了让它变绿去开 IRQ 或确认极性**。
- **写后仍然是禁能态**：`runtime write gate mask=0x0`、`request=LOW pad=0 ENN_fb=1 verdict=OK`、
  `MCU_DRV_ENABLE = 0 (禁止(安全))`、`armed=0 cur_hz=0 tgt_hz=0 health=OK`、`irq_gate=CLOSED polarity=not-confirmed`。
  GCONF/CHOPCONF 前后值只记录（`00000101->00000141` / `15010053->15030053`，差异位 `0x40` / `0x20000`），**本场未解码其语义**。
- 六条响应全部无裸 NUL、严格 UTF-8、每条内容行 ≤120 B（最长 120/91/62/120/91/105）。
  开话后排走的上一轮残留 5 B `msh >` 由执行器单独存为 `02_mot_pre.pre.rx.bin` 并登记散列，**没有追加进任何响应原文**。

## 3 判读结果

`FIELD_JUDGEMENT.txt`：**包内判据 77 条，fail=0**；另加 5 条"预测行表逐字节"附加对照（不是 A 的包内判据），也 0 不符：
`system_status@pre 21/21`、`motor_status@pre 27/27`、`system_status@post 21/21`、`motor_status@post 26/26`、`safety_status 9/9`。
apply 八条里 **6 条逐字节命中**；未命中的两条正是 A 在 provenance 里明确标为示例值的
`预算: 已用 50 / 总 1500 tick`（预测 300）与 `flush=0B TX=4B`（预测 32B）——我没有把它们当断言，也没有反过来当成"预测失败"。

## 4 未证与边界

未证：电流/转矩实测、SG/ADC/IMU 相对保护的功能命中、正常 `disarm` 收口、带载最坏栈、镜像内其余 35 条 >127 B 带标签格式串、
IRQ 开路与极性确认（本场额度 0，没做）、GCONF/CHOPCONF 差异位语义。执行器自报 `core_protection_verified=False` ——
`flow_complete=True` 只说明申报的阶段都跑完了，**不等于目标通道保护已验证**。

板端终态：驱动 CONFIGURED、电机未武装、频率 0、MCU 请求 LOW、焊盘 LOW、ENN 反馈在禁能侧、保护门与 IRQ 未动、未起转；
VM 供电保持、端口正常关闭（`PORT_CLOSED` 恰 1 条）。

## 5 下一步（不在本场）

按 A 的路线，下一场才谈极性/IRQ 就绪、短动作、SG/ADC 学习与目标通道触发。那需要 A 重新出包并**对新范围再批一次**：
本包额度已用尽，旧批准与剩余额度不结转，我不会拿这一场的批准去授权任何新动作。
