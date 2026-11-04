# M8-R7D1 返修轮交接(ARD1–ARD4 离线返修)

日期 2026-11-04。分支 `feature/m8-r7d1-async-learn-offline`。开工 HEAD = 被审提交 `4d50310`(离线判据
`HEAD_IS_REVIEWED_COMMIT` 当场核过)。设备动作 **0**。

## 1. 输入与判定

- 被审件: A 的复审包 `NS800_R7D1_A_Review_Changes_Required_4d50310_20261104.zip`,
  19634 B, sha256 `d9090f964ed0242f012e40e15ed40f427a3234f72572c88e9468288fb2ce9b8d`,
  判 **CHANGES_REQUIRED**, 四项 P2(`ARD1..ARD4`), `hardware_actions_cap=0`,
  `new_approval_required_for_offline_fix=false` —— 按任务书第 5 行的授权直接在本轮实现/验收/归档收口,
  未开登记轮, 也未向操作者等批准。
- 包内 18 条清单逐条复算通过且不含自身; 原件(含 A 的 `proof/` 与 `reproducer/`)按原路径入
  `reviewer_originals/`。
- A 登记的 5 件源码输入在 `4d50310` 上的 blob sha1 / 字节数 / blob sha256 全部当场复算相符;
  其中 4 件本轮改了磁盘字节, `diagnosis.c` 逐字节未变。

## 2. 本轮做了什么(只列结论, 细节见 implementation_contract.md 与 case_mapping.md)

- **ARD1**: `rel_job_status()` 的 NULL 参数检查前移到取锁之前。探针前后 `ctx_held 1→0`、`takes 1→0`。
  回归 A13 还核"拒绝之后查询/受理/推进/取消照样走得通"。
- **ARD2**: zero 的退出清理改成有界延迟清理 —— 新增可查状态 `d_zero_cleanup`(经
  `rel_job_view_t.cleanup_pending` 暴露), 由 Diagnosis Thread 的 `rel_job_advance()` 在锁恢复后的那一拍
  真正 abort + 归还 owner; 旗未灭时第二个采集者仍一律被拒。五个出口全覆盖。
  过程中发现自己引进的次生问题并同轮修掉: 第一版把披露打印放在清理函数里, 而"发布失败"出口是在静止事务内
  调它的, 静止事务成功时**持续持有电机锁**(motor.c:2097 取 / motor.c:2140 放) ⇒ 等于在持锁期间走 UART。
  终版: 清理函数只回报状态、不打印, 五个出口各自在放锁之后披露。
- **ARD3**: `rel_txn_open("clear")` 移到任何副作用之前; 被拒绝的 clear 保留候选、凭据与配置
  (探针前后 `pending_changed 1→0`, 凭据 0/0→1/1)。A 要求的"配置清理本身失败时的事务语义"写在合同 §3。
- **ARD4**: commit 改成**先发布候选再写配置**; 发布失败 ⇒ 配置一字未动(探针前后 `cfg_changed 1→0`,
  magic 不再出现 4E535250); 配置写失败 ⇒ 用发布前的候选副本回滚并具名披露
  `config-write-rolled-back` / `config-write-no-rollback`。叶锁内不调 cfg/UART/Flash/sleep, 锁序仍是
  mot/cfg→ctx, 未改任何配置事务接口。
- 邻接出口审计(§5 表): `rel_cap_start` 失败、`rel_job_start` 受理失败、`rel_job_status`、
  `rel_prod_publish/rel_ctl_take` 逐个看过, 没有第二处"占了归属又静默返回"。

## 3. 验证结果(全部当场跑, 原文在 host/)

- 正常链: `build_rc=0 warnings=0`, `SUMMARY|cases=66 pass=66 fail=0 pre_bad=0 executed=66`, `proc_rc=0`。
  上一轮 62 例一条未删(`PRIOR_62_CASES_KEPT`), 本轮新增 A13–A16(`FOUR_NEW_CASES_ADDED`)。
- 反向链: 19 族全部 META-OK, 0 族 META-FAIL; 每族都有 `INJECTED` 行(= 副本编译成功且真跑起来);
  期望失败身份分别是新增的 A13/A14/A15/A16, 连带失败按"额外"登记不改变判定。原 14 族一条未删。
- A 的原版探针(未改脚本): 修前在 `4d50310` 逐字节副本上 `checks=4 failures=4 run_rc=1`
  (四条 OBS 与 A 包内原文一字不差), 修后在同一台机器同一 gcc 上 `failures=0 run_rc=0`。
  本机 gcc 是 4.9.2 tdm64, A 是 14.2 Debian —— 两条都是同一份产品 C 的 host 编译, 不互相否定。
- 离线判据: `offline/checks.txt` **TOTAL=64 FAIL=0**(被审包身份、5 件 blob 复算、改动范围、66 例/19 族、
  探针前后、打印折算、镜像身份与 AXF-Flash-段复算、历史件未被改写、隐私扫描)。
- 打印: 本轮新增/改动的 rt_kprintf 共 3 个格式, 最宽一次 74 B(含源 LF), 预算 120 B;
  `learn_status` 主行未被改宽(单独一条 74 B 短行披露 cleanup); 无裸 NUL, 严格 UTF-8。

## 4. 构建侧(一次, 离线)

`UV4 -r project.uvprojx -t rt-thread-motor-m1-candidate -j0`, rc=0, `0 Error(s), 0 Warning(s)`, V6.24。
`Program Size: Code=209474 RO-data=87422 RW-data=2196 ZI-data=85944`。
新镜像 BIN 297476 B sha256 `165d8bdc708b042125d99db88f56a62ddca635d30202e80abe96c148e35bc478`;
AXF 1789320 B `dc145259…`; MAP 2144643 B `8229cf75…`; 调用图 1099033 B `16b87c97…`。
归档在新目录 `reports/firmware/phase8-m8-r7d1-ard-rework/`, **不覆盖** `4d50310` 那一版
(其 BIN 仍是 `df7584c3…`, 判据 `REVIEWED_IMAGE_UNCHANGED` 当场核过; A 的 4 条 FAIL 原文也在
`reviewer_originals/proof/` 原文保留)。
AXF 的 Flash 装载段与 BIN 逐字节相等由仓外 ELF 程序头解析独立复算(先在被审件上复现"相等"再跑本轮件)。
资源对账换算法: `Code+RO+RW` 增量 +476 而 BIN 增量 +472 —— 差额由装载段构成解释干净:
ER_IROM1 +476、RW_VT_DTCM 压缩载荷 0、对齐空隙 +4、RW_DTCM **压缩后**载荷 −8 ⇒ +472;
两版各自"三段+空隙"都精确等于该版 BIN 字节数, 残差 0。逐对象差分只出现在 `diag_relative.o`
(Code +310 RO +178 RW 0 ZI +1), 其余 110 行 .o 与 49 行库成员逐字段相同。
新增静态 `d_zero_cleanup` = 1 B(取自 map 符号表)。
栈: diag 线程帧与子树 +0(最深线逐字未变, 余量 2508 B); 命令线程里 `rel_cmd_commit` 因 ARD4 的回滚副本
帧 +112、子树 1444→1556, 成为新的可见最深线(余量 2540 B, FINSH 4096 未改)。
如实登记为**本轮新增成本**, 没有靠加大栈或延长超时换绿; armlink 的数是静态下界(解析不了 msh 的函数指针,
也不含异常/ISR 帧), 不给 shell 栈总数。

## 5. 边界与保持项

- 板端仍只承认历史 `b502 / 502dca5d…` 的逐字节回读证明; R7D1 `df7584c3…` 与本轮返修候选 `165d8bdc…` 都是
  **NOT_ON_BOARD**。
- `polarity_measured=false`、`core_protection_verified=false` 不变; 四条禁用声称继续禁止。
- R7F5A 物理断电回报仍是 `PENDING_OPERATOR_REPORT`, 本轮没有代填、也没有为了关闭它去接设备。
- 演示端 V0.3、旧包、旧现场原件、旧批准账未动; 本轮不构成任何硬件授权。
- 主机链里的 learn/zero 只是内存模型调用, 不是现场学习、不是硬件时序证明。

## 6. 自伤与工具侧问题(如实)

- 打印位置(§2 ARD2)是本轮自己发现并同轮修掉的次生缺陷, 发现于读 motor.c 静止事务的持锁区间之后,
  修在构建之前 —— 最终构建的字节里没有那一处。
- 修前基线复现第一次跑成了 4/4 **PASS**: 我先把 5 个 blob 写进仓外副本、再 `cp -r` 整个 applications 目录,
  拷贝把三件产品文件覆盖成了修后版本。当场发现(结果与 A 的登记矛盾), 重新以 `git show 4d50310:path`
  写回五件并逐件 sha256 与 A 的 `IDENTITY.json` 对账后才重跑, 得到 4/4 FAIL。失败的输出目录已被
  正确的那次覆盖, 没有留下可误用的中间结果。
- 资源报告工具第一次把 armlink 的"ELF Image Totals (compressed)"等小计行当成对象行比较, 报出
  "非零差分 2 行"; 排除小计行后判据才真正只允许 `diag_relative.o`。同一工具还把上一轮的
  "39 个库成员行"口径沿用过来 —— 实测 component-sizes 表里 111 行全是 .o, 库成员行在 map 的更靠前段落;
  本轮改成按整份 map 比库成员(成员名+段+尺寸+类型, 49 行相同), 上一轮那句措辞口径不准, 在此登记更正
  (旧报告原文不改)。
- 反向链在后台任务里被提前报了一次"completed"而日志只写到 RDV2; 没有拿那份截断日志当结果, 等它真跑完
  重读全文才登记 19/19。

## 7. 下一轮需要 A 看的点

1. ARD2 的"明确状态"目前是 `learn_status` 的一条独立短行 + `cleanup_pending` 字段; 如果 A 要求它进主行,
   主行最宽展开会从 120 B 变成 139 B, 需要重新裁预算(上一轮就是为这个才拆行的)。
2. ARD4 的回滚在"配置写失败且回滚也失败"时只披露 `config-write-no-rollback`(候选=新版, 配置=旧版),
   没有第二次重试; 是否需要一个显式的"重刷候选"动作请 A 定。
3. 命令线程可见最深线由 rel_cmd_commit 顶高 96 B(1556/4096)。若 A 认为这不可接受, 可选方向是
   让回滚复用已有缓冲而不是新增 116 B 局部 —— 那会重新引入并发问题, 需要 A 判优先级。
4. 现场侧仍然一样: 板上是 b502, 相对保护的真实现场证明还没拿到。
