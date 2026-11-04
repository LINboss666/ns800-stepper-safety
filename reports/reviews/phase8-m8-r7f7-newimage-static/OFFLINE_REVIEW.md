# M8-R7F7 离线轮判读：新版上板＋真实异步作业包（0 设备动作）

轮次目录：`reports/reviews/phase8-m8-r7f7-newimage-static/`
A 的包：`NS800_R7F7_NewImage_Real_Async_Job_Task_20261105.zip`，312901 B，
SHA256 `15bf813bbd62ee7fcbc263f2c31c1a92f71a42f6e084498c0fa4f568d1076b62`（zip 原件留在仓外，本机 Downloads 目录）。
批准身份（尚未开账）：`M8R7F7-2369-async-job-static-20261105`。

## 0. 本轮做了什么、没做什么

做了什么：把 A 的每一个声称回到本机可实读的事实上重算（`tools/r7f7_offline_checks.py`，109 条判据，FAIL=0，
原文 `proof/offline_preflight_r7f7.txt`）。

**没做什么（全部是设备动作，等操作者的真实执行指令）**：

| 动作 | 本轮 | 现场轮上限（A §2） |
|---|---:|---|
| 下载 `0f421595` AXF | 0 | 1 |
| 297452 B 定长回读并逐字节比较 | 0 | 1 |
| COM5 开口 / dispatch | 0 | 各 1 |
| 开口后显式复位 | 0 | 1 |
| `diag_profile zero 32` | 0 | 1 |
| `diag_profile learn 8 450 550 0` | 0 | 1 次请求，必须受理为 1 个真实作业 |
| arm/start/运动/apply/IRQ 开关/极性确认 | 0 | 0 |
| learn_cancel/tier/commit/clear/config_save/黑区清除/故障复位/重试 | 0 | 0 |
| 分析仪 Start / 注入 / 拆探头 | 0 | 0 |
| 逻辑分析仪、串口、DAP、pyOCD、Keil | 全程未调用 | — |

现场批准账本仍未开：核准根目录下 `*M8R7F7-2369-async-job-static*` 匹配 0 件
（判据 `FIELD_APPROVAL_LEDGER_ABSENT|PASS`）。旧 R7F6 额度不结转、旧账不写。

## 1. 包与镜像身份（三方→四方对账）

- `SHA256SUMS.txt` 列 **28** 件（不含自身），zip 内 29 件文件；28/28 磁盘散列==清单，0 件不符。
- 镜像 BIN：297452 B / `23691e8d8aa8c1d3…`，在
  `reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/rt-thread-motor-m1-candidate.bin`，
  **四方一致**：A 的 `IDENTITY.json` == A 的派生绑定 `manifest_binding_r7f7_2369.json` == 真实构建清单
  `build_manifest.json` == 磁盘实读。AXF/map/callgraph 同样四方一致。
- **AXF 的 Flash 装载段逐字节 == BIN**：ELF32 小端，`e_phnum` 里只有 1 条 PT_LOAD，
  `p_paddr=0x08000000`、`filesz=297452`、段 sha == BIN sha（`23691e8d…`）。
  `memsz=… > filesz` 是 RW/ZI 的 RAM 映像，回读只比 flash 段——这一点显式登记，不混称"整文件相等"。
- 该镜像构建轮 `hardware_actions_this_round=0`，构建时 HEAD `e1d7f3d` 与计划
  `tested_image_build_commit`、派生绑定 `actual_build_time_HEAD` 一致；
  签收源码提交是交付件所在提交 `00718b4`（A 的 `legacy_build_commit_field_note` 说明了这个错位）。
- b502（`502dca5d…`）仍只作历史证明；`IDENTITY.image.before_execution = NOT_ON_BOARD`。
  **下载一旦发生就不再是 b502 在板**，只有本场定长回读逐字节相等后才登记新版在板。

## 2. 源码身份

- 包内 `frozen_newimage_source/applications/` 的 10 件 `.c`：
  包内字节 == `00718b4` 的 git blob == 当前工作树（按行尾归一），三方逐字节一致。
- `IDENTITY.source_bindings` 5 件（含 `diag_relative.h`、`project_config.h`）在 tip `7a08f9d` 上
  sha256 + bytes + git blob id 全等。
- `product_tree_sha1 = 32470c7e…` 我最初按"源码目录树"去找没找到；实读确认它是提交里
  **`rt-thread-master` 这一子树**的对象号，且在 `00718b4` 与 `7a08f9d` 上是同一个对象 →
  `product_tree_equal_to_accepted` 成立。
- `00718b4..7a08f9d` 之间非 `reports/` 的改动只有 `.gitattributes` 一件 → 产品与主机源码 0 改动，
  A §1 的"两者无差异"独立复算通过。
- 事实登记（不是问题，但必须写出来）：工作树里 `motor.c` / `supervisor.c` / `tmc2209.c` 三件是 **CRLF**
  （`git ls-files --eol` = `i/lf w/crlf attr/text`），blob 与包内冻结是 LF。
  去掉行尾后逐字节相等；镜像是已构建产物，本次差异不参与镜像身份。

## 3. 计划判据在本机重跑

用仓库原执行器（未改动，sha `33d3f0af…` 与包内见证副本逐字节相同）本机重跑三件事，原文入 `proof/*_local.txt`：

| 复算 | 本机 | A 包原文 | 判定 |
|---|---|---|---|
| `--validate-plan` | 58 行 / checks=57 / fail=1 / msh_commands=74 | 同 | 判定名与 PASS/FAIL **逐行相同**，58 行里唯一差异是末行 `plan=` 路径（A 在自己沙箱） |
| 唯一原始 FAIL | `PLAN_MOTION_CAP_BUDGET`（caps=[] sum=0 total=0 starts=0） | 同 | A 把空运动包裁为 N/A 并**保留原 FAIL**，未改执行器、未填假额度 |
| `--approval-init`（离线 stub 身份） | caps 21 项、identities 3 散列 | 同 | 完全一致 |
| `--dispatch --transport stub --backend sim` | S0–S3 全 OK，`quota_used` 恰为 6 项正额度各 1，0 额度项 0 消耗 | 同 | S0 facts 除路径/身份名外全等；needles_total=41；steps=16，disarm=NOT_ARMED |
| `--prove-needles`（冻结源） | `NEEDLES|total=41|unproven=0` | — | 41 条字面量**在产品工作树源码里也逐条命中**；旧同步 learn 的 OK/FAILED 两条确已不在 |

计划语义侧我自己另算的（不依赖执行器）：15 步 = 1 复位 + 13 命令 + 1 被动观察；13 条命令顺序与任务书 §5 表
逐项相同；17 条禁用命令一条都没出现在计划命令/argv 里（注入 `motor_start` 会被抓到）；
75 s 启动窗 + 14 个观测/命令窗 91 s = **166 s ≤ 申报 210 s**；
`learn` 那一步硬要求 `STARTED id=1 n=8 win=450..550 dir=0` 且明禁 `REFUSED/FAILED`；
终态那一步锁死 `id=1 FAILED why=WINDOW_BROKEN owner=0 cred=1/0` 且明禁 `STARTED/RUNNING/job DONE`；
5 条失败词白名单全部整行锚定、逐条等于同步骤被正面要求的文本，且**只有 `job_failed` 一处含 FAILED**
（即失败词没有被全局放行）。

## 4. A 的 host C 模型在本机重编译重跑

`proof/host_probe.c` 是 A 直接编入真实产品 C 的探针（`#include REVIEW_HOST_MAIN` → 夹具
`tests/host/rel_main.c`）。本机用仓库自己的 `shim_rel` + 产品目录编译并运行：

- 编译 rc=0；运行 rc=0（探针自带判据：`id=1 && state=REL_JOB_FAILED && owner=REL_CAP_FREE && zero && !learn && !active`）。
- 输出 **逐字节等于** A 的 `host_probe.stdout.txt`（本机 CRLF 归一后 sha `fc8747c7…` 相同；
  字节差恰好等于行数 29 → 只是行尾差）。
- 帧顺序 `job_initial → zero → job_zero → learn_start → job_failed`；
  `HOST_STATE|id=1 state=4 owner=0 zero=1 learn=0 active=0`。
- `state=4` 的身份回到产品头文件推算：`rel_job_state_t` 里 `REL_JOB_IDLE=0` 起隐式递增 →
  `REL_JOB_FAILED=4`；`REL_CAP_FREE=0`。不是照抄 host 行里的数字。
- 工具侧差异如实登记：本机 gcc 4.9.2 不发 `-Wmisleading-indentation`，所以本机 warning=0 而 A 沙箱=1。
  这**不能**读成"本机构建更干净"。A 日志指向 `rel_main.c:351`，本机同文件该行原文存在 → 夹具同源。
- 元负控：把 `why` 改成别的身份后逐字节判据即失配（`HOST_GATE_FIRES_ON_TAMPERED_OUTPUT`）。

这只是 host 模型。**现场必须重新取证**，本轮没有任何一项板端结论。

## 5. 供电口径：A 本轮采纳了操作者的变更请求

任务书 §3 原文：「最新交接登记当前仍通电，并转达操作者希望今后默认不断电。本轮采纳：**已上电就保持供电，
正常收口保持禁能供电，不再要求额外断电或报告收口断电时刻。** 旧轮次的实际动作与旧判据不回改。」
计划里 `operator_power_policy` 逐条对上：`normal_end_powerdown=0`、`additional_powercycles=0`、
`initial_power_establish_max=1`、`if_already_powered="No action; do not power-cycle for this task."`，
并且保留两条边界：通电状态以现场实报为准（不由 USB 枚举推断）、异常使能/运动时操作者硬件禁能优先。

这就是 R7F6 收口时我转给 A 的那条变更请求（操作者原话「我希望默认不断电」）在本轮被采纳。
R7F6 已登记的事实与三个超额度标签不回改。

## 6. 仍未证 / 不写进本轮结论

A §6 的通过口径只到：新镜像逐字节在板 + 启动静态状态通过 + 静止 zero 通过 + 异步作业真实受理及越窗失败收口通过。
本轮一条都没声明（0 设备动作）。仍未证清单（保持 `polarity_measured=false`、`core_protection_verified=false`）：
正常巡航下的采样进度与 DONE 有效基线、活动作业取消与取消竞争、commit/config_save、
SG/ADC/IMU 真实保护触发与恢复、实际 STEP/ENN 动态时延、带载最坏栈。
仪器标识披露按操作者本条决定**暂缓**，未改成"已修复"，Git 历史未动。

## 7. 本轮自伤（照实登记，共 18 项）

1. 判据工具把仓库根算成 `parents[3]`（正确是 `parents[4]`），首次运行 `FileNotFoundError`，
   报出的路径是 `…\reports\reports\reviews\…`——多套了一层目录却差点被我当成"包没放对"。
2. 期望清单条目数写死 29，实际 `SHA256SUMS.txt` 只列 28 件（清单不含自身）。
   我把"zip 内 29 个文件"与"清单 28 条"混为一谈；改判据时同时写明两者关系。
3. 会话时间预算我先手算成 162 s 并把它写进判据，实算是 166 s（漏了 `job_failed` 那一步的 5 s）。
   **算术错的判据会把正确的计划钉成失败**——已按实测值改，并保留这条记录。
4. 取 PASS/FAIL 用了固定下标 `split("|")[2]`；步骤级行是 `NAME|S3|as|PASS|…`（判定在第 4 段），
   导致 `VP_*` 假红。改成"取第一个等于 PASS/FAIL 的字段"。这次是**假红不是假绿**，但同一写法若用在
   泄漏扫描上就会漏报。
5. `"%s" % m.groups()`（3 元组配 1 个占位）触发 `TypeError`，工具整跑挂在格式化上。
6. 泄漏扫描用 str 正则去搜 bytes → `TypeError: cannot use a string pattern on a bytes-like object`。
7. 我新加的通用 `token=|password=` 模式命中了 `signed_inputs/m8r3_serial_chain.py` 里执行器自己的形参名
   `approval_token=`——那是代码标识符不是秘密。撤销该模式并在此登记理由，**没有**改包件原文。
8. 通用盘符模式 `X:/` 会命中 A 包件里合法存在的 `C:/Keil_v5/…`（任务书自带的工具链路径）。
   放弃该模式并登记取舍理由：一刀切会逼我改包件字节，而"删命中"不等于"没泄漏"。
9. 掩码顺序先短后长：`D:/ns800`（仓库根）是 `D:/ns800_r7f1_scratch`（仓外工作目录）的前缀，
   先替仓库根把仓外路径写成 `<REPO>_r7f1_scratch`，读起来像"证据在仓库里"。改为先长后短并重跑全部证据。
10. 泄漏自证的合成样本被后一次赋值覆盖（旧行没删干净），`LEAK_SCAN_GATE_FIRES` 报 2/3。
    这条恰好证明 must-FIRE 设计有效：**检查器不会命中时，"全部干净"这句话就不作数**。
11. `REL_JOB_FAILED` 在头文件里没有显式 `= 4`，我最初按 `NAME\s*=\s*(\d+)` 取值取不到 → 差点把
    "枚举身份"降级成"照抄 host 行的数字"。改为按 C 隐式递增从 enum 头部推算。
12. gcc 版本差异（本机 4.9.2 / A 沙箱 ≥6）会让 warning 计数不同。我没有把它当成"本机更干净"，
    而是登记为编译器版本差异，并用 `rel_main.c:351` 的原文锚点证明夹具同源。
13. stub 复跑第一次撞 `APPROVAL_LEDGER_ALREADY_EXISTS`(rc=4)，第二次 dispatch 撞 rc=1——
    台账按设计拒绝同一身份重复消耗额度（正是历史上 apply 用到 14/2 的那条路的堵法）。
    我的工具因此改为每次运行用带 RUNID 的新仓外台账，而不是复用旧账把红当绿。
14. **同类复发**：我新写的提交前置检查器自己带了路径字面量样本，连它自己的模式定义行
    （一个匹配"斜杠+Users+斜杠"的字符类）都满足自己的判据，被本轮泄漏扫描当场抓到两处命中。
    改成运行时用 `chr(92)` 拼装模式与样本后才干净。这条与第 10 项同一个教训：
    **"检查器先自证会命中"必须每轮真的跑一遍，不能靠上一轮的经验默认它还在工作。**
15. `dap_uid_bare.txt` 的机器本地默认路径我差点当成隐私问题处理：它不含用户名也不含 UID 明文
    （工具只打印长度 48 并搜明文是否混进入库件），真正的问题是**换机器就失效**，所以保留为
    `--uid-file` 的默认值并写明可覆盖，而不是删掉它。
16. 我把本该追加进 `run_record.md` §20 的补句**误编辑进了交接 md 的 §5**，删掉了一行
    "要跑现场就回一句明确指令…"，随即按原文恢复（恢复后已核对 §5 全文在位）。
    教训：改文档同样要核对目标文件与改动范围，不能凭手感连着 Edit——这条与"改代码要有 diff 核对"是同一纪律。
17. 远端回读工具我直接把 R7F6 那份清单抄过来用，`run_record.md` 在**那一轮**是冻结件，本轮它被我
    追加了 §20，于是被判 `FROZEN_UNCHANGED|FAIL`。这是**假红不是假绿**（348 条登记逐条相符、镜像四方
    一致、产品树同一对象都仍 PASS），但冻结集该按本轮意图重列：已把它移出冻结清单并注明
    "只追加不改旧"由 §16–§20 各一次 + HEAD 版是严格前缀这两条承担。
18. 判据 `IDENTITY_TIP_IS_DEV_BASELINE` 我把"A 声明的开发基线"写成"必须等于本机 HEAD"，而本轮我自己
    刚提交过一次，HEAD 前进就把它钉成假红。改成断言基线是 HEAD 的祖先或自身，并单独登记当前 HEAD；
    同时把 `RUN_RECORD_IS_APPEND_ONLY` 的基线从 HEAD 换成**本轮起点常量** `7a08f9d`——要保的是旧 §1–§19，
    不是禁止我在本轮内改自己刚写的 §20（例如把自伤条数从 16 更正到 17）。

## 8. 交给操作者的一个决定

现场轮要做的三件不可逆/半可逆事：① 一次 flash 下载会**替换 b502 的在板身份**（A §1 明写下载后不能继续把
b502 当作当前在板证明）；② 一次 `learn 8 450 550 0` 会**真实创建作业**（这是本轮的目的，电机仍不转、
不武装、ENN 保持禁能侧）；③ 一次显式 pyOCD 复位 + 75 s 被动启动窗（会话上限 210 s）。

离线核对全部通过，我不替操作者批准。要执行就请回一句明确的执行指令（例如「按 R7F7 包执行」），
我就按 A §2 的额度开新账 `M8R7F7-2369-async-job-static-20261105` 并一次跑完；不重烧、不补测、不重试。
