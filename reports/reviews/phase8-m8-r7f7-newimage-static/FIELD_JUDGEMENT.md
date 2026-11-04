# M8-R7F7 现场轮判读（新镜像上板 + 真实异步作业创建与越窗失败收口）

轮次：`M8-R7F7-2369-async-job-static-20261105`。执行日期本地 2026-11-05 04:14:58 起。
设备动作：下载 1、定长回读 1、COM5 开口 1、显式复位 1、`zero 32` 1、`learn 8 450 550 0` 1、dispatch 1。
**电机全程未转，未武装，驱动保持禁能，IRQ 保持 CLOSED。**

判据原文：`proof/field_judgement_r7f7.txt`（**99 条，FAIL=0**，其中 10 条是"必须会命中"的负控：名字含 FIRES/SELFCHECK 且判定为 PASS）。
原始字节：`field/field-20261104T201458Z-r7f7/`（53 件，含 `readback.bin` 与两份台账）。
离线轮（0 设备动作）的核对与批准门见 `OFFLINE_REVIEW.md`；本轮的授权来自操作者原话
「这一轮没问题就直接批准你执行」，逐字登进 `run_log.txt` 第 2 行。

## 1. 板端事实（本轮实际看到的，不是推的）

### 1.1 镜像身份：逐字节在板

- 回读 **297452 B**，SHA256 `23691e8d8aa8c1d3…`，与被签收 BIN **整文件逐字节相等**
  （`IMAGE_READBACK_BYTE_EQUAL_TO_ACCEPTED_BIN`）。不是"只 hash 相等"，也不是"读了任意窗口"。
- 这条判据先自证会命中：中间 1 字节取反 → 命中；少 1 字节（任意窗口）→ 命中。
- 下载侧旁证：pyOCD `flash` rc=0，`Erasing...`/`Programming.` 各 1 次，erase=program=**297984 B**
  （291 扇区整片编程，**≥ 镜像 297452 B**，扇区对齐的正常结果，只作"下载确实发生过"的旁证）。
  唯一载荷 = 被签收 AXF `0f421595…`；**没有回烧 b502**，没有换其他候选。
- 启动横幅 `5.3.0 build Nov  5 2026 00:35:48`；计划里那张 b502 夹具横幅是 `Nov  4 2026 01:35:43`。
  横幅与新版构建时刻一致，但**它只是旁证**——在板身份由 §1.1 的逐字节回读承担。
- b502 仍是历史证明，本轮之后**不再把 b502 当当前在板身份**。

### 1.2 启动静态状态：75 s 观测窗真的走满了

| 项 | 实测 |
|---|---|
| 开口 t | 0.047 s（1 次尝试 1 次成功，115200 8N1） |
| 显式复位 | rc=0，elapsed 1.625 s，stdout/stderr 各 0 B（sha 前缀 `e3b0c442`＝空串摘要） |
| 被动启动窗 | 申报 75.0 s / 实耗 **75.0 s**，结束原因 `DRAIN_BUDGET_REACHED`，捕获 **3468 B** |
| 首条查询 | t=76.813 s ⇒ 75 s 下界被真正覆盖（不是静默早退） |
| 会话结束 | 最后响应 81.797 s，`PORT_CLOSED` 81.828 s；`SESSION_OK steps=16` |

`steps=16` 与计划 `steps=15` 差 1：执行器把启动流采集单独记一条结果
（`m8r3_serial_chain.py:1820` `kind=boot_stream`），15 个计划步骤 + 1 条启动流记录 = 16。口径已核，不是矛盾。

启动流字节：严格 UTF-8 通过、裸 NUL=0、CR=97 / LF=75 / 总 3468 B、最长行 126 B。
>120 B 的行存在——未改动的执行器对被动启动流只跑原有的危险文本门，**完整字节验收由本判读承担**，
没有为它临时改执行器冒充实时门。

启动内容正面读到：TMC `IOIN` 成功且 `VERSION=0x21`、`BOOT→INIT→SELF_TEST→READY` 链完整、
`required=PASS degraded=0x00`、终态 `state=READY fault_code=0`、`Phase-7 runtime bootstrap complete`，
**无** `FAULT_LATCHED`、**无** `[REQ FAIL]`。

### 1.3 十三步时序（13 条命令 + 1 次复位 + 1 s 被动观察）

顺序与 A §5 表逐条一致（t 为会话内秒）：

| # | as | 命令 | t | 响应 B | 关键事实 |
|---:|---|---|---:|---:|---|
| 1 | rst.boot | (复位后被动 75 s) | 76.703 | 3468 | 见 §1.2 |
| 2 | threads | `ps` | 76.813 | 1192 | main/diag/tshell 栈 **4096**；高水位 40 %/28 %/16 % |
| 3 | heap | `free` | 76.828 | 90 | total 32672 used 22120 maximum 22120 available 10552；`total=used+available`、`maximum≥used` 都核；**新四项，不沿用 b502 旧值** |
| 4 | sys_initial | `system_status` | 76.922 | 983 | READY/fault=0 |
| 5 | safety_static | `safety_status` | 76.985 | 522 | ready=YES first_err=0 |
| 6 | mot_static | `motor_status` | 77.094 | 1121 | 未武装、请求禁能、arm gate `0x18` REFUSED |
| 7 | irq_static | `safety_irq_status` | 77.156 | 612 | IRQ CLOSED、极性未确认、protect_ready NO、drive_cfg UNCONFIGURED |
| 8 | job_initial | `diag_profile learn_status` | 77.172 | 130 | id=0 **IDLE** owner=0 **cred=0/0** |
| 9 | zero_static | `diag_profile zero 32` | 77.516 | 106 | **zero OK n=32 raw 2055..2065 mean=2060437 (cal source untouched)** |
| 10 | job_zero | `diag_profile learn_status` | 77.531 | 130 | id=0 IDLE owner=0 **cred=1/0** |
| 11 | learn_start | `diag_profile learn 8 450 550 0` | 77.563 | 171 | **learn STARTED id=1 n=8 win=450..550 dir=0** + `learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED` |
| — | (被动 1 s) | 不发命令 | 81.578 | 0 | `WATCH_DONE rx_bytes=0` |
| 12 | job_failed | `diag_profile learn_status` | 81.594 | 140 | **id=1 FAILED why=WINDOW_BROKEN owner=0 cred=1/0**，`n=0/0 want=8 win=450..550` |
| 13 | sys_final | `system_status` | 81.688 | 983 | 仍 READY/fault=0 |
| 14 | mot_final | `motor_status` | 81.797 | 1128 | 仍未武装、频率 0、请求禁能、ENN HIGH |

- 13 条命令的判据我用**仓库原执行器自己的 `assert_step`** 在归档字节上逐条复算，全 OK
  （`ASSERT_STEP_RECOMPUTED_ALL_13_OK`），不只信执行器自报。
- 每份原始响应的开头就是所发命令的板端回显（`ECHO_PRESENT_AND_EQUALS_PLAN_CMD`，查的是剥之前的字节）。
- 开口后残留提示符被**单独排走另存**为 `07_irq_static.pre.rx.bin`（7 B，尾 `\r\nmsh >`）和
  `14_sys_final.pre.rx.bin`（5 B，`msh >`），**没有回填进对应步骤的响应**。这是历史上把 5 B 假响应
  当成响应的坑，本轮按先例处理并留原件。
- 13 份全部严格 UTF-8、裸 NUL=0；每份字节数按 115200 8N1 的下界耗时都在该步申报秒数内。
- 13 条实发命令里没有任何禁用命令（判据自带负控：注入 `motor_start` 会命中）。

### 1.4 真实作业的受理与失败收口（本轮的主命题）

- **受理**：`learn 8 450 550 0` 回 `STARTED id=1`，参数与计划一致。**不是 REFUSED，不是 IDLE**。
  这就是 A §2 要的"必须受理为 1 个真实作业"。
- **后台推进**：learn 响应 t=77.563 → `WATCH_DONE` t=81.578 实测 4.015 s（申报 1 s 窗，
  `ended_by_budget=False`）。差值是执行器自身步间歇，**期间未发任何命令**；
  终态查询在 81.594。⇒ 判越窗失败的是板上的 Diagnosis Thread 自己，不是我补发命令催出来的。
- **终态**：`id=1 st=FAILED why=WINDOW_BROKEN owner=0 cred=1/0`；采集器已释放（owner=0），
  未 DONE，`want=8` 保留、`n=0/0`。静止条件下零点到模式合格，`rel_job_start()` 真的建了任务，
  巡航窗复核在后台新帧的 `rel_cap_feed()` 里失败——这正是本轮要在板上验的生命周期。
- **零点**：32 个不同有效帧（raw 2055..2065），`cal source untouched` ⇒ 只更新 RAM 里的候选零点，
  **没有** config_save、没有 clear、没有复位。
- **失败词没有全局放行**：`FAILED/WINDOW_BROKEN` 只在 `job_failed` 一处按整行 + id/owner/凭据全锁定的
  白名单出现（`status_lines_allowed` 逐行原文见 `proof/field_business_outcomes.json`）；
  其他 `REFUSED` 行是 arm gate `0x18` 静止态的正常拒绝，`allowed=false`。

### 1.5 额度与授权对账

- `dispatch_ledger.jsonl` 21 行 = 6 次 `APPROVAL_RESERVED` + 6 次 `APPROVAL_COMMITTED` + 动作/绑定/S0/收口；
  `APPROVAL_LEDGER_END` 的 `pending=0`，`spent` 恰为
  `download=1 readback=1 port_open=1 explicit_reset=1 diag_profile_zero=1 diag_profile_learn=1`，
  其余 15 键 0。**没有第 7 次预约**（负控：构造 n=2 重复预约会被这条计数式抓到）。
- dispatch rc=0，墙钟 117.2 s ≤ 计划 `total_deadline` 210 s（含下载与回读，不只是串口段）。
- 开口尝试 1 / 成功 1 / 关闭 1；本轮只 dispatch 一次，**未重跑、未补测、未重试**。
- 仪表标识披露按 A §1 仍**暂缓**：本轮未启动分析仪，未产生新的原生捕获文件。

### 1.6 隐私

私有 DAP UID 只登记长度 48 与 sha256 前 12（小写 `d2f530dbd062` / 大写 `586cb3f3ac73`）；
COM5 是同一探针的 CDC 接口。整包 53 件里 UID 明文（两种大小写）、用户主目录片段、
漫游数据目录片段命中 = 0；这五类模式在合成脏样本上 5/5 会命中才允许宣布干净。

## 2. 本轮结论（只写这些，不越界）

**新镜像逐字节在板；启动静态状态通过；静止 zero 通过；异步作业真实受理（STARTED id=1）及越窗失败收口
（FAILED/WINDOW_BROKEN + owner 释放 + 未 DONE）通过。** 全程电机未转、未武装、禁能、IRQ CLOSED。

## 3. 仍未证（不得因整包 OK 而升级）

正常巡航下的采样进度与 DONE 有效基线；活动作业取消与取消竞争；`commit`/`config_save` 与重启恢复；
SG/ADC/IMU 真实保护触发与恢复；实际 STEP/ENN 动态时延；带载最坏栈（`ps` 高水位只证空载静止）。
故 `polarity_measured=false`、`core_protection_verified=false` 保持；
执行器自报 `flow_complete=True` 只说明申报阶段跑完且账目一致，不等于目标通道验证。

## 4. 收口状态

COM5 已关闭；**保持 VM + MCU 供电**（操作者口径"默认不断电"，A 已在 §3 采纳）；
保持未武装、禁能、IRQ CLOSED；没有为清理候选零点或失败作业记录而额外复位/断电/clear/save。
下轮从实际状态重新定范围，不假定 RAM 里那些记录已消失。

## 5. 判读与工具自伤（7 项，全部当场修并重跑）

1. **补丁脚本把源码写坏**：我用 heredoc 打补丁时，`b"\r\n"` 被写成真实 CR/LF，
   `r7f7_field_judgement.py` 直接 `SyntaxError`（行 176）。而且 `Read` 的行视图与 python
   `newline=''` 的行切分不一致，替换区间要按后者定。修完必须 `py_compile` 再跑。
2. **第一版判读 3 处判据设计错**：`ASSERT_STEP` 的 `all()` 扫了台账所有行（含非步骤行）；
   回显判据拿"剥完之后"的行去比命令名（应当查剥之前的原始字节）；
   启动捕获文件名写成 `01_rst.rx.bin`，实际是 `01_rst.boot.rx.bin`（执行器 `m8r3_serial_chain.py:1788`
   给启动流单独加了 `.boot` 标记）。
3. **归档清单用手工累加**，漏了 `readback.bin`、`readback_identity.json`、`dispatch_ledger.jsonl`
   三件（手工 50 vs 磁盘 53）。改为落盘后 `walk` 生成 + 负控（漏掉 `readback.bin` 同一比较即不成立）。
   决定性证据没进自己的清单，等于"我以为归档了"。
4. **跨阶段台账第一版没归档**，而 idle-stack-r4 的先例是归档的；补入并按原字节核对 21 行。
5. **隐私判据第一次写成恒真比较**（把 UID 拼进被检数据再断言能命中）。改成合成脏样本 5/5 命中。
6. **模式字面量第三次差点复发**：`AppData`、`Users\` 直接写进检查器源码就会被本轮自己的泄漏扫描抓到，
   已改为运行时 `bytes([92])`/`bytes([47])` 拼装，并避免连续字面量。
7. **类型混用**：`uid` 是 `read_text()` 的 `str`，被检数据是 `bytes`，`pat in data` 直接 `TypeError`
   （同类缺陷在我自己的工具里第三次出现）。

已知的非身份性残留：仓外 scratch 路径以 JSON 转义形态留在
`dispatch.stdout.txt`/`dispatch_ledger.jsonl`/`ledger.jsonl` 三件工具元数据里。我**没有**为"装干净"
去反掩改台账字节——台账是执行器原输出，`<RUNROOT>` 掩码只覆盖单反斜杠形态，这与既往轮次一致，
在此如实登记而不是把它说成已清除。

## 6. 交付件

- `field/field-20261104T201458Z-r7f7/`（53 件，`field_manifest.json` 按磁盘实有枚举）
- `proof/field_judgement_r7f7.txt`（99 条，FAIL=0）、`proof/field_business_outcomes.json`
- `tools/r7f7_field_judgement.py`（本轮判读 + 归档，含 10 条必须会命中的负控）
- `tools/r7f7_staged_preflight.py`（新增 UID **大写形态**专用模式；五类样例自检必须全命中）
- `ROUND_SHA256SUMS.txt`（重算）、`run_record.md` §21、`phase8-m8-r7f7-newimage-static-field.{md,json}`
- 产品代码、固件、A 的包原件、旧轮证据与旧账本：**0 改动**
