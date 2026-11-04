# M8-R7F7 现场轮交接（新镜像上板 + 真实异步作业受理与越窗失败收口）

机器可判定版：`phase8-m8-r7f7-newimage-static-field.json`（本轮所有数值都从那里对账）。
判读正文：`reports/reviews/phase8-m8-r7f7-newimage-static/FIELD_JUDGEMENT.md`。
判据原文：`…/proof/field_judgement_r7f7.txt`（**99 条，FAIL=0**，其中 10 条是"必须会命中"的负控）。
离线轮（0 设备动作）见 `phase8-m8-r7f7-newimage-static-offline.{md,json}` 与 `OFFLINE_REVIEW.md`。

## 1. 触发与授权

操作者 2026-11-05 04:14 的原话「这一轮没问题就直接批准你执行」就是 A §2 要的那条执行指令，
逐字登进 `run_log.txt` 第 2 行。令牌用包内 `approval_token.txt` 原样 58 B（sha12 `7c93e682…`），
未拼接、未改写。新批准身份 `M8R7F7-2369-async-job-static-20261105`；旧 R7F6 等账本只读、未写、不结转。

现场只 dispatch **一次**：rc=0，墙钟 117.2 s ≤ 计划 210 s。首错即停、不重烧 b502、不补测、不重试——
本轮没有触发任何一条停止条件。

## 2. 六项正额度各 1，用满，无第 7 次

下载 1 / 定长回读 1 / COM5 开口 1 / 显式复位 1 / `diag_profile zero 32` 1 / `diag_profile learn 8 450 550 0` 1；
其余 15 个额度键 0 消耗（arm、start、apply、IRQ 开/关、极性、运动、cancel、tier、commit、clear、
config_save、重试、分析仪）。台账 `APPROVAL_LEDGER_END`：`pending=0`，`spent` 与 caps 逐键相等；
`dispatch_ledger.jsonl` 21 行 = 6 预约 + 6 提交 + 动作/绑定/S0/收口。负控：构造一次 n=2 的重复预约，
这条计数式判据会抓到。

## 3. 板端事实（三层里的第一层）

- **新镜像逐字节在板**：回读 297452 B，SHA256 `23691e8d…`，与被签收 BIN **整文件逐字节相等**；
  该判据先在"取反 1 字节"和"少 1 字节（任意窗口）"两种缺陷形状上验过会命中。
  下载旁证 erase=program=297984 B（291 扇区），唯一载荷是被签收 AXF `0f421595…`，**未回烧 b502**。
  b502 保留为历史证明，不再是当前在板身份。
- **启动静态状态通过**：复位后被动窗申报 75.0 s / 实耗 75.0 s，结束原因 `DRAIN_BUDGET_REACHED`
  （不是静默早退），捕获 3468 B；首条查询在开口后 76.813 s。启动流正面读到 TMC IOIN 成功
  （VERSION=0x21）、`BOOT→INIT→SELF_TEST→READY`、`required=PASS degraded=0x00`、
  `Phase-7 runtime bootstrap complete`，无 `FAULT_LATCHED`、无 `[REQ FAIL]`；严格 UTF-8、裸 NUL=0、
  CR=97 / LF=75 / 最长行 126 B。`ps` 正面读到 main/diag/tshell 均 4096（高水位 40 %/28 %/16 %）；
  `free` 登记本场新四项 32672/22120/22120/10552，并核 `total=used+available`、`maximum≥used`，
  未沿用 b502 旧值、未设编造阈值。
- **静止 zero 通过**：`zero OK n=32 raw 2055..2065 mean=2060437 (cal source untouched)`，凭据 0/0 → 1/0；
  未 config_save、未 clear、未复位。
- **异步作业真实受理及越窗失败收口通过**：`learn 8 450 550 0` 回 **`STARTED id=1 n=8 win=450..550 dir=0`**
  （不是 REFUSED、不是 IDLE），另有 `learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED`；
  终态 **`id=1 st=FAILED why=WINDOW_BROKEN owner=0 cred=1/0`**、`n=0/0 want=8`（采集器已释放、未 DONE）。
  learn 响应 t=77.563 → `WATCH_DONE` t=81.578 期间**未发任何命令**，终态查询 81.594 ⇒
  判越窗失败的是板上 Diagnosis Thread，不是我补命令催出来的。
- 13 条命令顺序同 A §5 表；13 份判据我用**仓库原执行器自己的 `assert_step`** 在归档字节上逐条复算全 OK；
  每份原始响应开头都是所发命令的板端回显；开口后残留提示符另存 2 个 `.pre.rx.bin`（7 B / 5 B），
  没有回填进响应；13 份全部严格 UTF-8、裸 NUL=0，字节数按 115200 8N1 下界都在该步申报秒数内；
  实发命令里没有任何禁用命令（注入 `motor_start` 会被抓）。
- **电机全程未转**：两次 `motor_status` 仍未武装、频率 0、请求禁能、ENN HIGH、arm gate `0x18` REFUSED；
  两次 `system_status` 仍 READY/fault=0、IRQ CLOSED、极性未确认、protect_ready NO、drive_cfg UNCONFIGURED。
  失败词未全局放行：`FAILED/WINDOW_BROKEN` 只在 `job_failed` 一处按整行 + id/owner/凭据锁定。

## 4. 本轮只允许写这些结论

新镜像逐字节在板、启动静态状态通过、静止 zero 通过、异步作业真实受理及越窗失败收口通过。

**仍未证**：正常巡航下的采样进度与 DONE 有效基线、活动作业取消与取消竞争、`commit`/`config_save` 与
重启恢复、SG/ADC/IMU 真实保护触发与恢复、实际 STEP/ENN 动态时延、带载最坏栈。
所以 `polarity_measured=false`、`core_protection_verified=false` 保持；执行器自报 `flow_complete=True`
只代表"申报阶段跑完且额度/取证/撤能账目一致"。不能说"整个学习功能已上板验完"。

## 5. 供电与收口

**保持 VM + MCU 供电**（A §3 已采纳操作者"默认不断电"口径），COM5 已关闭（尝试 1 / 成功 1 / 关闭 1），
保持未武装、禁能、IRQ CLOSED。候选零点与失败作业记录留在 RAM，未为清理它们额外复位/断电/clear/save。
仪表标识披露仍按 A §1 暂缓，本轮未启动分析仪、未产生新的原生捕获文件。

## 6. 判读与工具自伤 8 项（正文 §5 全列）

① 我的 heredoc 补丁把 `b"\r\n"` 写成真实 CR/LF，判读工具源码直接语法坏掉——
`Read` 的行视图与 python `newline=''` 的切分不一致，替换区间要按后者定；
② 第一版判读有 3 处判据设计错（`ASSERT_STEP` 的 `all()` 扫了台账全部行、回显判据拿"剥完之后"的行
比命令名、启动捕获没按执行器的 `.boot` 文件命名）；③ **归档清单用手工累加，漏了 `readback.bin`、
`readback_identity.json`、`dispatch_ledger.jsonl` 三件**（手工 50 vs 磁盘 53）——决定性证据没进自己的清单，
等于"我以为归档了"；改成落盘后 walk 生成 + 负控；④ 跨阶段台账第一版没归档，与 idle-stack-r4 先例不符；
⑤ 隐私判据第一次写成恒真比较（把 UID 拼进被检数据再断言命中），换成合成脏样本 5/5 命中；
⑥ 泄漏模式字面量第三次差点复发（`AppData`、`Users\` 直写进检查器就会被自己的扫描抓到），改运行时拼装；
⑦ `uid` 是 `str`、被检数据是 `bytes`，`pat in data` 直接 `TypeError`（我自己工具里第三次类型混用）；
⑧ **远端回读的门覆盖不足**：`RUN_RECORD_SECTION` 写死 `§16..§20`，本轮新段 `§21` 不在门内（内容仍由逐 blob
哈希承担）。补到 `§21` 后另一次提交推送并重跑回读：406 条 blob 与远端 tip 相符、包原件 29 件与 zip 相符、
fails=0。本交接件因此被更新，旧提交不回改。

一处残留如实登记，不说成已清除：仓外 scratch 路径以 JSON 转义形态留在
`dispatch.stdout.txt`/`dispatch_ledger.jsonl`/`ledger.jsonl` 三件工具元数据里。我**没有**为"装干净"
去反掩改台账字节——台账是执行器原输出，改动它比留着它更糟。

## 7. 下一步

本轮通过后，学习链还剩的正常巡航 DONE、活动取消、提交/保存、真实保护触发四类需要**新的现场范围与
新的额度**，且 DONE 那条依赖能在板上造出巡航窗（本场是有意保留静止）。下轮从实际状态重新定范围，
不假定 RAM 里的候选零点与失败作业记录已消失。要继续就请操作者给下一包或一句明确指令。
