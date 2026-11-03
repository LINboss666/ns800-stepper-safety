# M8-R6A 离线返修轮执行记录

任务书:`NS800_M8_R6_Review_REWORK_20261103.md`(云端 A 对 R6 交付的独立复审,裁定整体 REWORK、
继续停在离线)。基线 = 实际拉取并审核的完整交付头 `44e493cea9bbbc35e18394ca23dd1a8502519448`。
本轮只做 R1–R4 四项执行控制/输出问题,**设备动作全部为 0**。

APP = `rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/`
EXEC = `reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py`
R6 = `reports/reviews/phase8-m8-r6-offline-recovery/`(本轮只读、不回改)

## 17.0 开工核对

| 项 | 实测 |
|---|---|
| `git rev-parse HEAD` | `44e493cea9bbbc35e18394ca23dd1a8502519448`(= 任务书指定的完整头) |
| 分支 | `feature/m8-sg-adc-relative-protection`(个人修复分支,不新建、不 reset/rebase/force) |
| 远端是否已推进 | 开工时 `HEAD == origin` 该分支 tip;未覆盖任何用户新修改 |
| 冻结件 | R5-C 原计划、其 26 场原件/台账、R6 的派生计划与 `d70a9180…` 镜像,本轮**一个字节都没改**(§17.6 逐项验证) |

## 17.1 R1 账本:创建与读取分离,每次读盘都严格

EXEC `ApprovalLedger`:

- `_load_or_create()` → **`_load()`**:文件缺失 = `APPROVAL_LEDGER_ABSENT`;运行期没有任何一条路径
  能"从空开始"。创建只允许 `approval_init`。复审表里的第④行(绑定后删账本 → 自动建满额账)
  就是这条分支造成的,现在两侧(real 与 stub)同一条门:`open_approval()` 只验不建,缺目录也不再
  被顺手 `mkdir`。
- 新增模块级 `validate_ledger_doc()`,在**每一次**加锁读盘与每一次写盘前调用(不是只在开账时查
  一次):对象类型 / 必需字段 / `version` / 批准令牌 / **三件完整 SHA(64 位小写十六进制,缺一件
  或畸形即拒)** / caps **键集合与值都必须精确相符** / spent 键集合 == caps / 每个计数是**真 int**
  (`bool`/负数/小数/字符串数字一律拒) / spent ≤ caps / pending 的 id 形状、序号、名称、n、去重 /
  `writes` 计数。全部只拒绝,不补零、不迁移、不"修好再继续"。
- `approval_init`:先校验 caps 与三件身份(`APPROVAL_CAPS_INVALID` / `APPROVAL_IDENTITIES_INCOMPLETE`
  / 非 16 进制同样在开账时就拒),再按 caps 的每个动作键预置 `spent=0`;创建走**同一把**
  `ledger_lock()` + `O_CREAT|O_EXCL`,消灭"exists 检查后 os.replace"的并发覆盖窗口。
- `reserve(name, n, kind)`:`name` 非空字符串、`n` 必须是正整数(`APPROVAL_RESERVE_N_INVALID`)、
  `kind` 非空;额度与消耗从已校验的结构里取,不再 `setdefault`/`int()` 兜底。
- `commit()`:`_store` 失败即 `APPROVAL_LEDGER_WRITE_FAILED` 抛出并落 `APPROVAL_COMMIT_FAILED` 事件,
  不再在没落成的情况下声称"已确认消耗";pending 留在盘上按已消耗计(不退款纪律不变)。
- `ApprovalLedger.VERSION` 1 → 2:上一轮那本账由 `APPROVAL_LEDGER_VERSION` 具名拒绝,**不**自动
  迁移成"未消费"状态。历史不兼容 = 另批,不是重置。
- 保留的既有方向:发送前持久预占、崩溃不退款、撤能预留独立(业务拒绝不得挡住必要撤能)。

**四个反例的修前/修后同一输入对照**(`host/r6a_before_after.txt`,rc=0):

| §3 表 | 修前(基线 44e493c 的执行器) | 修后 |
|---|---|---|
| 删 `identities.executor_sha256` | `snapshot` **正常返回** `spent={'apply':1}` | `snapshot`/`reserve` 双双 `APPROVAL_LEDGER_CORRUPT`,缺字段不被补回 |
| 删整个 `spent`(仍是合法 JSON) | 再预占**成功** `tag=apply#1`(账上仍是 1) | `APPROVAL_LEDGER_CORRUPT`(`spent_keys!=caps_keys`),不补零 |
| `spent.apply = -1` | 再预占**成功**且凭据 `tag=apply#0`、账被改写成 0 | `APPROVAL_LEDGER_CORRUPT`(`spent_not_count`),文件保持 `-1` |
| 绑定并耗尽后删账本文件 | 再预占**成功**,自建 `spent={'apply':1}` 的新账 | `APPROVAL_LEDGER_ABSENT`,文件未被重建 |
| 完整账重复预占(正控) | `APPROVAL_QUOTA_EXHAUSTED` | 同样 `APPROVAL_QUOTA_EXHAUSTED`(未退化) |

新增自检用例 FS93–FS100(临时账,全部在系统临时目录,不产生任何可用于现场的批准):
缺身份 SHA / 缺 spent / 非法计数与非法 `n`(0、-1、`True`、`"1"`、1.5、None)/ 缺 SHA 与非 16
进制开不了账 + caps 少键多键 + 账本缺 cap 键 + 账本 SHA 畸形 / 双 init 线程竞争(2 线程同刻,
只有一个 `init` 成功、另一个 `APPROVAL_LEDGER_ALREADY_EXISTS`,盘上只有一份账、开账时 `spent`
全 0、已消费后再 init 仍被拒)/ 存盘失败不谎称落成 / v1 历史账具名拒绝且不被改写。

## 17.2 R2 执行输入绑定批准 + 命令→额度键集中映射

采用任务书 §4 的**最小修法**:真实后端暂时只允许经批准的整包 dispatch。

- `exec_session(..., approved=...)`:`transport=="real"` 且没有 `approved` =>
  `REAL_SESSION_REQUIRES_APPROVED_PACKAGE`(在任何端口、任何工具、任何账本动作之前);没有
  `plan` => `REAL_SESSION_REQUIRES_PLAN`。stub 工装入口原样保留。
- `verify_approved_binding()` 两道都查:①盘上 `--plan` 字节的 sha256 仍等于**账本登记**的
  `plan_sha256`(不是"此刻盘上的值",变了就 `PLAN_BYTES_CHANGED_AFTER_APPROVAL`);②本会话实际
  步骤等于从**那份字节**里同一阶段重新推导出的步骤,否则 `STEPS_NOT_BOUND_TO_APPROVAL`。
  核对通过后在会话台账落 `APPROVED_PACKAGE_VERIFIED`。`dispatch_plan` 的 real 分支还加了
  `REAL_DISPATCH_REQUIRES_PLAN_FILE`(没有可重读的字节来源就没法谈"一致")。
- CLI:`--transport real` 只有 `--dispatch` 一条路。单会话 `--steps` 直接
  `REAL_TRANSPORT_REFUSED:single_session_steps_not_approved_package`,rc=4 且**不创建输出目录**。
- `QUOTA_COMMAND_KEYS` 把 `tmc_config_apply` / `config_save` / `motor_start→motor_run` /
  `safety_polarity_confirm`+`safety_irq_enable→irq_polarity_prep` / `diag_profile zero|learn|commit` /
  `blackbox_clear` 钉到额度键;运行期在**那条 write 之前**判:缺申报 `QUOTA_STEP_UNDECLARED`、
  错键 `QUOTA_KEY_MISMATCH`、`n != 1` `QUOTA_N_MISMATCH`。只读查询与有限轮询不进这张表(不把
  轮询误计成 apply 重试)。`spend()` 自己补了 `QUOTA_N_INVALID`(没有批准账的那条路同样守)。
  离线侧 `validate_plan` 加 `PLAN_QUOTA_MAP` 用同一张表;`derive_quota_needs` 改成"命令在表上就
  算一次消耗",删字段过不了"申报==推导"。
- 阶段级工具(download/readback/reset/reboot/人工重启门)沿 R6 的 `reserve_stage`,位置不变。

反例对照(同一文件 rc=0):A 的探针形状(cap `tmc_config_apply=0`,步骤不声明 quota)
修前 **TX 里有 `tmc_config_apply CONFIRM`、端口开了 1 次、`quota_used={port_open:1}`**;
修后 `REAL_SESSION_REQUIRES_APPROVED_PACKAGE`、`ports_opened=0`、`tx=[]`。CLI 侧修前是
`APPROVAL_LEDGER_ABSENT`(账本门,但仍会建输出目录),修后是装配层的具名拒绝、`run_dir_created=false`。

新增用例 FS101–FS108:真实单会话不发(开口 0)/ CLI rc=4 而 stub 入口照跑 / 改步骤不改计划字节
被拒(正控里能看到 `APPROVED_PACKAGE_VERIFIED`)/ 绑定后计划文件漂移被拒 / 缺声明、错键、`n=2`、
`n=0`、未映射命令 `n=-1` 五种全在 write 前拒且 `quota_used` 只有 port_open / 只读 `motor_status`
轮询与 `diag_status` 不扣任何写额度 / 元负控 FS107 摘掉映射表后那条 apply **确实被发出** /
cap=0 但正确申报也拒发。

## 17.3 R3 原始字节门集中复用:清理、观察窗、发送前排空

- 新增 `guard_partial_bytes()`(危险文本 → 裸 NUL)与 `guard_stream_bytes()`(再 + 非法 UTF-8),
  成为唯一入口;`split_rows()` 在**剥离提示符/回显之前**对完整原始字节调用它,普通响应、清理
  (`_cleanup_read`)、观察窗(`split_rows(require_prompt=False)`)、发送前排空(`command_attempt`)
  四条路径复用的是同一个函数,不再各写一份。
- `read_until_prompt()` 的循环里危险/NUL 门现在排在 `RESPONSE_BYTE_CAP` **之前**,与注释一致;
  半截缓冲只判危险/NUL(中文行被 512 B 读块剪开是正常现象,解码判在完整字节流上)。
- 清理某条响应损坏:`_cleanup_read` 记**一条**独立证据错误(不再与普通判读重复),继续向后执行
  必要撤能;若坏的是最后那份 `motor_status` => 六字段一律 `FIELD_MISSING_*` => 禁能 `UNKNOWN`
  且整场 `success=False`。原始字节先落盘、不改一字,也不用另一场的干净回包补这一场。
- 观察窗里的字节损坏从"记 PROTOCOL_FAIL 后照常判 NO_TRIGGER"改成**硬停止**
  (`STREAM_BYTE_DEFECTS` 一律 raise),由会话层转成具名 STOP 并照常清理。
- 发送前排空的 `pre` 字节:先原样存成 `NN_<as>.pre.rx.bin`,再在同一会话里判危险门,命中就在
  下一条业务 write 之前停(实测 `writes=[]`)。

新增用例 FS109–FS114:清理 status 裸 NUL / 非法 UTF-8 / 危险+非法混合(报危险)/ 观察窗裸 NUL /
pre 残留带 HardFault / 元负控 FS114 同时摘掉两个门 => 同一条 NUL 回包又变成
`CONFIRMED_DISABLED` 且 `success=True`(说明 UNKNOWN 真的来自这道门)。修前/修后同一输入对照见
`r6a_before_after.txt` 的 R3 段:修前 `cleanup_errors=[]`、`disarm=CONFIRMED_DISABLED`(把含裸
NUL 的回包当成禁能证明),修后 `UNKNOWN` + 具名错误,两边证据文件里 NUL 都还是 1 个。
注:该探针故意不给业务 `motor_start` 回包(与 §4 的判定无关),所以两边 `status` 都是 STOP;
`success` 维度的同一反例由 FS109(修后 False)与 FS114(摘门后 True)成对覆盖。

## 17.4 R4 本轮新增错误打印的宽度

`APP/motor.c` `mot_commit_gated()` 里 `if (withheld)` 那条(R6 新增,A 量得格式串 211 B / 最坏
237 B):拆成 4 次调用,逐条按检查器的**保守**口径(%s 未登记时按 18 B 展开)量得
101 B / 79 B / 67 B / 65 B,全部 ≤120 B;字段(epoch、permit 确认与电平@代次、inhibit、
motor_stop_latched)与"未授予恢复资格"的含义一个不减。

`host/line_width_check.py` 补了两件事:

- FIXED 表加入本轮 motor.c 的 4 条新短行与 R6 的 `关闭落位**未提交**`;
- 新增 **CHANGED_SITES**:直接从 `git diff <base>` 的新增行里取出本轮真正改动过的每个
  `rt_kprintf` 站点逐个量宽(base 默认 `535459f`,可 `--since` 覆盖),并且**空扫描即判失败**
  (`CHANGE_SCAN_META|FAIL`),因为上一轮的 PASS 正是因为这条约束不存在才漏掉新站点。
  本轮命中 18 个站点,最长 101 B;`--selfcheck` 的靶子多了第三条(R6 新增那条 withheld,
  worst=285 B 判红)。`LINE_WIDTH_RESULT PASS (bad=0)` rc=0,`--selfcheck` rc=0。
- `REPO_SCAN >120B` 站点数按本轮改动后重算(登记不改,`SCOPE_NOTE` 保留),不扩成全仓清理。

## 17.5 §7.5 构建:motor 候选只建一次

```
$ "C:/Keil_v5/UV4/UV4.exe" -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 \
    -o D:/ns800/reports/reviews/phase8-m8-r6a-offline-rework/build/rt-thread-motor-r6a.log
UV4_EXIT=0
"...\build\motor_m1\rt-thread-motor-m1.axf" - 0 Error(s), 0 Warning(s).
Program Size: Code=205594 RO-data=85994 RW-data=2188 ZI-data=85592
Build Time Elapsed:  00:00:09
*** Using Compiler 'V6.24', folder: 'C:\Keil_v5\ARM\ARMCLANG\Bin'
```

与 R6 同一 Target(`Code=205570 RO=85970 RW=2188 ZI=85592`)的资源差:
**Code +24 B、RO-data +24 B、RW 0、ZI 0** —— 只多了一条 `rt_kprintf` 调用与它的字面串,
静态数据没长。没重建其它 Target,没拿旧镜像充新结果。

归档件(`reports/firmware/phase8-m8-r6a-offline-rework/`,身份由 `host/archive_build_r6a.py`
当场算,拷贝后与 build 输出逐字节比对;`--check` 亦 rc=0):

| 件 | bytes | sha256 |
|---|---|---|
| rt-thread-motor-m1-candidate.bin | 292164 | `5ae4110d14fdbb40f49cc6f0deca474cf6d0b33b3ee9305e83681dbdd4a53825` |
| rt-thread-motor-m1.axf | 1773648 | `581f1155166374cf96cda1281320dbcfd31968f1bf5a898a6dd0e2a804a52df1` |
| rt-thread-motor-m1.map | 2110730 | `4f58a3eea493d8c532ecceba3d16db1e59750066ec75cede8647197563d412c3` |

R6 的 `d70a9180b40c5c6a…`(292116 B)**原样保留**,manifest 里 `recomputed_now_matches_archive`
当场复算为 true;新件从未上板(`new_image_on_board=false`),板上逐字节证过的仍是 `dab356c6…`。

## 17.6 派生 R6A 离线候选(不给任何硬件额度)

`host/derive_plan_r6a.py` 从 R6 的派生件 `plan_core_r6.json`(`6e0b97f251a61493…`,脚本对它做
只读校验,字节不对就拒绝派生)派生 `field/plan_core_r6a.json`:169310 B `556a044e195705d1…`。

- 执行器登记换成修后那份 209786 B / `99d6c9600b05d610…`,并逐条写清本轮改了哪些判据;
- 新增 `r6a_candidate_image`:三件产物身份 + `bind_state=NOT_BOUND_IN_THIS_PACKAGE` +
  `supersedes` 里如实记 R6 那件 d70…;**`image_binding` 不动**,继续指向已在板证过的 `dab356c6…`;
- `approval.status=REQUIRES_NEW_APPROVAL`、`hardware_actions_authorized_by_this_package=0`;
- `derived_from` 记源件路径/字节/sha,并注明 R6 与 R5-C 两份派生件同样冻结不回改。
- 守卫都实测过:`--force` 缺失时改过字节 -> `R6A_REFUSE_WRITE` rc=1;`--force` rc=0;
  `--check` -> `R6A_CHECK|IDEMPOTENT|源仍冻结=True` rc=0。
- 离线门:`PLAN_VALIDATE_SUMMARY|checks=232|fail=0|msh_commands=74`(新增的 `PLAN_QUOTA_MAP`
  接管了原来那批 `PLAN_QUOTA` 步骤行,所以总检查数不变)。

## 17.7 复跑范围与退出码(只跑受影响项,无新失败不重复全量)

| 命令 | 退出码 | 结果 |
|---|---|---|
| `ONLY=disarm_ready bash tests/host/run_stop_priority_s2.sh` | 0 | host_ovr 必需 10/10 PASS;target 按声明 SKIP=1(本机 MinGW `-mthreads` 可用,未改产品/断言) |
| `ONLY=disarm_ready REVERSE=1 …run_stop_priority_s2.sh` | 0 | 八项注入均命中预登记业务失败身份 |
| `python …/m8r3_field_selftest.py` | 0 | `cases=114 pass=114 fail=0 negative_injections=17 hardware_actions=0 transport=stub_only port=IN_MEMORY`(R6 的 92 条原样保留,新增 22 条) |
| `python …/line_width_check.py` 与 `--selfcheck` | 0 / 0 | `LINE_WIDTH_RESULT PASS (bad=0)`;三条 known-too-long 全部 fired;CHANGED_SITES 18 站点 |
| `python …/r6a_before_after.py` | 0 | `RESULT BEFORE_AFTER_OK (fails=0)` 六组反例修前放行/修后拒绝 |
| `python …/derive_plan_r6a.py`(写/拒/`--force`/`--check`) | 0 / 1 / 0 / 0 | 见 §17.6 |
| `…/m8r3_serial_chain.py --validate-plan --plan plan_core_r6a.json` | 0 | 232 检查 fail=0 |
| `python …/idle_learn_check.py`(r4 离线链回归) | 0 | `CHECKS=120 problems=0`,元负控 6/6 + 9/9 fired;P8 改成 r5c/r6 双"冻结↔冻结"+ r6a 对盘上 |
| `python …/apply_field_window_fix_r5c.py --check` | 0 | `executed_bytes_unchanged=True(05bcf96aa7a6bf89)`、`ORDER_ONLY_DELTA` |
| `python …/archive_build_r6a.py` 与 `--check` | 0 / 0 | 三件产物与 build 输出逐字节相同 |

`host_ovr` 之外未重跑 S2 其余 19 组、未重跑历史 5943 条清单(任务书 §7 明确不要求)。

## 17.8 状态与边界(保持复审 §8 的全部裁定)

```text
R1_approval_ledger = REWORK_DONE_OFFLINE(R6A)
R2_execution_input_binding = REWORK_DONE_OFFLINE(R6A)
R3_cleanup_byte_integrity = REWORK_DONE_OFFLINE(R6A)
R4_new_error_print_width = REWORK_DONE_OFFLINE(R6A) + 新镜像已构建并归档(未上板)
disarm_normal_close = HOST_TARGETED_TESTS_PASSED(本轮复跑 10/10 + 八项反向)
product_field_fix = NOT_VERIFIED
overall_offline_release = 待 A 复审
safe_for_enable = false
new_hardware_authorization = 0
device_actions_this_round = 0
```

保持不动的旧裁定:阈值、ACTIVE 覆盖、REL_IMPACT、`load_means`(仍 NOT_PROVIDED)、
idle1024 带载最坏栈余量、现场精确线程交错(NOT_PROVEN)、`0x00bd80b0` 写入者未证。
既有四项装配前提仍 PROVIDED,未重问;算法/阈值/速度/电流/引脚/锁存恢复资格一律未扩展。
本轮所有反例都是主机/内存输入构造,不是新的现场异常;新固件编译成功不等于已经上板修复。

勘误随本轮实质修复登记:上一轮提交信息 `a8d9033` 与交接件里的
`APPROVAL_LEDGER_ROOT_UNSABLE` 拼写有误,正确名是 `APPROVAL_LEDGER_ROOT_UNUSABLE`
(EXEC 现行号见 `open_approval`/`approval_init` 两处);历史提交与已归档 JSON 不回改,只在此声明。
