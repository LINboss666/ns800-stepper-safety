# M8-R7F7 离线核对轮交接（0 设备动作，停在批准门）

机器可判定版：`phase8-m8-r7f7-newimage-static-offline.json`（本轮所有数值都从那里对账）。
判读正文：`reports/reviews/phase8-m8-r7f7-newimage-static/OFFLINE_REVIEW.md`。
判据原文：`…/proof/offline_preflight_r7f7.txt`（109 条判据，FAIL=0）。

## 1. 触发与边界

操作者 2026-11-05 03:35 只转交了附件 `NS800_R7F7_NewImage_Real_Async_Job_Task_20261105.zip`
（312901 B，`15bf813b…`），**没有给执行指令**。本包要做的是一次 flash 下载、一次 297452 B 定长回读、
一次 COM5 会话（开口/复位/`zero 32`/`learn 8 450 550 0` 各 1，且 A 明令"必须真实受理为 1 个作业"）。
这些都是设备动作，所以本轮只做离线核对：下载 0、回读 0、串口 0、复位 0、zero/learn 0、分析仪 0，
pyOCD/Keil/串口一个都没调；现场账本 `M8R7F7-2369-async-job-static-20261105` **未开**
（核准根目录下匹配 0 件，判据 `FIELD_APPROVAL_LEDGER_ABSENT|PASS`）。对照 R7F6：那一轮操作者说了
「按此包执行」，所以我一次跑完——本轮缺的就是那句话。

## 2. 复算到的事实（挑要紧的）

- 镜像 BIN `23691e8d…` 297452 B / AXF `0f421595…` 1790048 B 在 A IDENTITY、A 派生绑定、真实
  `build_manifest.json`、磁盘**四方一致**；AXF 的唯一 PT_LOAD（`p_paddr=0x08000000`，`filesz=297452`）
  **逐字节等于 BIN**。`memsz>filesz` 是 RAM 里的 RW/ZI，回读判据只覆盖 flash 段。
- 包内 10 件 frozen `.c` == `00718b4` 的 blob == 工作树（按行尾归一）；`00718b4..7a08f9d` 非 `reports/`
  只有 `.gitattributes` 一处改动 → 产品与主机源码 0 改动。
- 本机 `--validate-plan` = 58 行 / checks=57 / fail=1 / msh=74，与 A 原文判定名+PASS/FAIL 逐行相同，
  唯一原始 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`（A 保留原 FAIL，未改执行器、未填假额度）。
- 本机 `stub+sim` 整包：S0–S3 全 OK，`quota_used` 恰为 6 项正额度各 1、15 项 0 额度 0 消耗。
- 41 条 needle：冻结件全命中，**产品工作树也全命中**；旧同步 learn 的 OK/FAILED 两条确已换成异步字面量。
- A 的 host C 探针在本机重编译重跑，输出**逐字节等于** A 的 stdout（CRLF 归一后同 sha）；
  `state=4` 按 C 隐式枚举推算就是 `REL_JOB_FAILED`。这仍只是 host 模型，板端一项都没证。
- 计划侧我自己另算：15 步=1 复位+13 命令+1 被动观察；17 条禁用命令零出现（注入 `motor_start` 会被抓）；
  75 s 启动窗+91 s 命令窗=166 s ≤ 210 s；`learn` 硬要求 `STARTED id=1`、终态锁死
  `id=1 FAILED/WINDOW_BROKEN owner=0 cred=1/0`；失败词白名单 5 条锚定且只 `job_failed` 一处含 FAILED。

## 3. 供电口径：变更请求被 A 采纳

任务书 §3 与计划 `operator_power_policy`：已上电就保持供电、收口不再要求断电、
`normal_end_powerdown=0`、`additional_powercycles=0`、必要时建立供电至多 1 次、
通电状态以现场实报为准、异常使能时操作者硬件禁能优先。
来源是操作者 R7F6 收口那句「我希望默认不断电」——我当时按变更请求转交，本轮 A 采纳。
R7F6 已登记事实与三个超额度标签不回改。

## 4. 自伤 18 项（正文 §7 全列）

其中第 14 项是**同类复发**：我新写的提交前置检查器自带路径字面量样本，连它自己的模式定义行都满足自己的判据，
被本轮泄漏扫描当场抓到（改成运行时 `chr(92)` 拼装才干净）——检查器自证必须每轮真跑，不能靠上一轮经验默认它还在工作。

第 16 项：我把该写进 run_record 的补句误编辑进本文件 §5，删掉一行后按原文恢复（改文档也要核对目标与范围）。
三条值得单独留：① 我把会话预算先手算成 162 s 写进判据，实算 166 s——算术错的判据会把正确的计划钉成失败；
② PASS/FAIL 取固定下标 `[2]`，步骤级行的判定在第 4 段，造成假红（同一写法用在泄漏扫描上就会漏报）；
③ 掩码先短后长，把仓外 `D:/ns800_r7f1_scratch` 掩成 `<REPO>_r7f1_scratch`，读起来像证据在仓库里。
另外两条是口径取舍并写明理由：通用 `token=` 命中的是执行器形参名、通用 `X:/` 命中的是 A 包里合法的
`C:/Keil_v5` 工具链路径 —— 撤销模式并登记，不改包件字节，不靠删命中装干净。

## 5. 下一步（等操作者）

要跑现场就回一句明确指令（如「按 R7F7 包执行」）。我按 A §2 的额度开新账、只 dispatch 一次：
S0 绑定 → S1 下载 1 次 → S2 定长回读 1 次 → S3 开口+复位+75 s 被动窗+13 条命令+1 s 观察，
首错即停、不重烧 b502、不补测、不重试；收口保持通电、保持未武装/禁能/IRQ CLOSED，
不为清理候选零点或失败作业记录而额外复位/断电/clear/save。
通过时结论只写：新镜像逐字节在板、启动静态状态通过、静止 zero 通过、异步作业真实受理及越窗失败收口通过。
