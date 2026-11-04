# M8-R7D1 返修轮实现合同(ARD1–ARD4)

被审提交 `4d503103cf3f1eb6f5113b10cc8683954d510ac6`(= 本轮开工时的 HEAD, 离线判据里 `HEAD_IS_REVIEWED_COMMIT` PASS)。
A 的复审件 `R7D1_A_REVIEW_4d50310_FIX` 判 CHANGES_REQUIRED, 四项 P2 全在错误路径。本轮是**离线返修**:
设备动作 0, 不构成任何硬件授权。改动只落在两个产品文件与两个主机文件(见 `code/before_after_identity.txt`);
`diagnosis.c` 本轮逐字节未动。

## 0. 本轮保持不变的合同(逐条有出处, 不是口头承诺)

| 合同 | 值 | 本轮状态 |
|---|---|---|
| 七态作业 | IDLE/RUNNING/CANCEL_PENDING/DONE/FAILED/CANCELLED/TIMEOUT | 未改(`diag_relative.h` 的 `rel_job_state_t` 不在 diff 里) |
| 命令截止 | `REL_CMD_DEADLINE_MS 12000` | 未改(diff 内不含该宏; 反向链 RDV2 仍命中 A08) |
| 单写者 | 只有 Diagnosis Thread 的 `rel_job_advance` 能推进 RUNNING/CANCEL_PENDING | 未改; 本轮新增的清理分支也放在同一个函数、同一把锁内 |
| 叶锁 | `rel_ctx` 临界区内不调 motor/cfg/Flash/UART/mdelay/print; 嵌套方向只 mot/cfg→ctx | 未改, 且 ARD2 把**唯一一处**违规风险(锁内打印)消掉了, 见 §2 |
| 采样资格 | 只认新帧喂样本、无帧也推进超时、回绕同余判定 | 未改(A08/A10 仍在链上并通过) |
| 保护路径 | 先保护后打印、`rel_prod_evaluate` 判据 | 未改(diagnosis.c 未动; diag_thread_entry 子树 +0) |

## 1. ARD1 — `rel_job_status(RT_NULL)` 泄锁

- 缺陷形状(A 的观察, 本机在 4d50310 的逐字节副本上复现相同): `rc=-5 ctx_held=1 takes=1 releases=0`。
  旧代码先 `rel_ctx_take()` 再判 `v == RT_NULL`, 于是"取锁成功 + 参数非法"这一条路上没人放锁。
- 改法(`diag_relative.c` 的 `rel_job_status`): NULL 检查**移到取锁之前**, 参数拒绝路径一次锁都不碰。
- 修复后同一探针: `rc=-5 ctx_held=0 takes=0 releases=0`。
- 为什么这个方向而不是"拒绝后补一次 give": 补 give 要把"我取过没取过"的状态带进分支, 而参数非法本来
  就不该产生任何可观察副作用(A 的原文要求"参数拒绝不得留下锁或其他副作用")。前移之后这条路径**没有**
  可泄漏的东西, 不需要恢复动作, 也就不存在"测试重置锁冒充正常恢复"的空间。
- 回归: A13(`A13-null-status-rejected-without-lock-leak`)。它在非法参数调用之后**继续**做真实查询、
  真实推进、真实取消并逐个判定结果, 且夹具的 `world_reset()` 不参与恢复(锁计数在非法调用点就是 0)。
  负控 RDV5 把 NULL 检查挪回取锁之后, 具名命中 A13。

## 2. ARD2 — zero 的退出清理拿不到锁 → 归属永久滞留

- 缺陷形状(A 的观察 + 本机复现): zero 退出时 `owner=BY_ZERO active=1`; 解除注入、让后台推进之后再 zero,
  仍报 `capture busy`, `owner=1 active=1` 不恢复。旧 `rel_zero_capture_close()` 拿不到锁就静默 `return`,
  而 `rel_job_advance()` 只认领 `BY_JOB`, 替 zero 收不了口。
- 改法(三件事, 都在 `diag_relative.c`):
  1. 新增明确状态: `static rt_uint8_t d_zero_cleanup;`(1 B, 在 map 符号表可核: `.bss.d_zero_cleanup` = 1),
     经 `rel_job_view_fill` 暴露为 `rel_job_view_t.cleanup_pending`, 由 `learn_status` 单独一条短行披露。
  2. 真实推进者: `rel_job_advance()` 开头新增一条**只服务 BY_ZERO** 的分支——先看旗, 再确认
     `d_cap_owner == REL_CAP_BY_ZERO`, 才 `rel_cap_abort()` + 归还 FREE + 清旗, 全程在同一把 `rel_ctx` 里,
     不打印、不取第二把锁、不 sleep。Diagnosis Thread 每拍都跑这个函数, 所以"故障消失后"的回收有周期保证。
  3. 有界等待: `rel_zero_capture_close()` 改完返回 `rt_bool_t`(RT_TRUE = 留了未收口的清理),
     一次有界取锁(`REL_CTX_TAKE_MS 20`)就返回。
- 清理完成前的排他性没有被放宽: 旗立着、`d_cap_owner` 仍是 BY_ZERO 时, `rel_zero_capture_open()` 的
  `d_cap_owner != FREE` 检查照旧把第二个采集者一律拒掉(A14 断言这一点, 也断言"强行抹掉 owner 但留着
  活动采集器"这条路没有发生: abort 与归还 owner 在同一段持锁代码里一次做完)。
- 覆盖共享这条清理路径的全部出口(`rel_cmd_zero` 五个): 作废凭据失败、采集中失去静止/禁能、
  12 s 超时、静止事务被拒、发布失败。
- 披露位置的叶锁边界(本轮自己发现并修掉的次生问题): 第一版把 `DEFERRED` 打印写在 `rel_zero_capture_close()`
  里, 而"发布失败"那条出口是在静止事务**内**调它的, `motor_calibration_static_begin()`(motor.c:2097)
  成功时持续持有电机锁直到 `..._end()`(motor.c:2140) —— 那就是在持锁期间走 UART。终版把清理函数改成
  只回报状态、不打印, 五个出口各自在**放锁之后**披露(事务内那条排到 `rel_txn_close()` 之后)。
  锁序因此仍是 mot→cfg→ctx, 没有新增嵌套方向。
- 回归: A14(`A14-zero-cleanup-deferred-then-recovered`)。负控两条: RDV6 把"留旗"删掉(回到基线的
  静默滞留形状: 不留旗也不披露), RDV7 保留旗但让推进者不认它——分别打掉"明确状态"与"真实推进者"
  这两个独立支柱, 都具名命中 A14。

## 3. ARD3 — 被拒绝的 clear 已经把候选与凭据擦了

- 缺陷形状(有旧版本对照, A 的 `clear_control.c` 两组都实际跑过 zero/learn 建凭据):
  | 实现 | clear 静止门拒绝 | pending 改变 | zero/learn 凭据 |
  |---|---|---|---|
  | 旧基线 `0cd6458` | 是 | 否 | 1 / 1 |
  | 被审 `4d50310` | 是 | 是 | 0 / 0 |
- 改法(`rel_cmd_clear`): `rel_txn_open("clear")` 移到副作用**之前**。门禁(参数确认 → ctx 门 → 静止事务)
  全部过了之后才 `rel_pending_write(&blank, 0, 0)` 擦候选与凭据; 擦失败时要先 `rel_txn_close()` 再打印。
- 配置清理本身失败的事务语义(如实, 不写成"更保守就等于没事"): 顺序是"先擦候选/凭据, 再 `project_config_clear_rprof()`"。
  擦成功而配置清理失败 ⇒ 输出 `clear FAILED rc=... (pending voided)`, 状态是"候选与凭据已作废、配置未变"——
  这一侧只会让 commit 更不可能成功, 不会留下"配置已清但凭据还在"的半份状态。擦失败 ⇒ 整个 clear 不做,
  配置与候选都保持原值。
- 回归: A15(`A15-refused-clear-keeps-pending-and-creds`)。A04 里 clear 那一步同时被收窄成
  "只证拒绝"(成功清理的语义交给 A15), 以免两条用例互相擦掉对方的前提。
  负控 RDV8 把擦除挪回门禁之前, 具名命中 A15。

## 4. ARD4 — commit 报 REFUSED 时 RAM 配置已经写了

- 缺陷形状(A 的观察 + 本机复现): 输出 `commit REFUSED: (ctx-lock-publish) rc=-12`, 但配置快照 magic 从
  `00000000` 变成 `4E535250`(RAM 中的项目配置; 没有 `config_save`, 不是 Flash 证据)。
  旧顺序是 `project_config_set_rprof()` → `rel_pending_write()`。
- 改法(`rel_cmd_commit`): 顺序换成**先发布候选, 再写配置**。
  - 发布失败 ⇒ `goto out`, 配置一个字没动(A 探针修后 `cfg_changed=0`, magic 仍是 `00000000`)。
  - 配置写失败 ⇒ 用新增局部 `prev`(发布前那份候选)回滚, 且两条结果各自具名披露:
    `config-write-rolled-back`(回到一致状态)与 `config-write-no-rollback`(候选=新版而配置=旧版, 明确说出来);
    两者都仍然是 `commit REFUSED`, 不报 OK。
  - OK 路径: 候选与配置是同一份 `cand`(`signature`/`profile_id`/`revision`/阈值字段都定稿之后),
    所以"配置和候选完整一致"由同一次赋值成立, A16 逐字段比对。
- 叶锁规则保持: 发布走 `rel_ctx`(叶锁), 配置走 `project_config_set_rprof`(cfg 锁)。两步之间**不能**合并成
  一个临界区——在 `rel_ctx` 里调 cfg 会把嵌套方向翻成 ctx→cfg, 而项目既有锁序是 mot/cfg→ctx。
  A 的原文允许"如需要调整直接依赖的配置事务接口, 记录锁序与边界即可"; 本轮没有调整接口, 只换了调用顺序,
  并把这条边界写在函数注释里。`project_config_*` 本身未改。
- 回归: A16(`A16-commit-refused-keeps-config-and-candidate`)。它的配置故障注入靠**自校准**: 先跑一次成功的
  commit 数出这次用了几个 cfg 取锁步, 再把失败武装在第 N-1 步, 使注入确实落在 `project_config_set_rprof`
  而不是别处——否则"看起来命中"可能只是卡在别的锁上。负控 RDV9 把顺序换回"先配置后发布", 具名命中 A16。

## 5. 邻接出口审计(A 要求"核对相同清理/发布模式的直接邻接出口")

同一条"拿锁失败就返回"的形状, 本轮逐个看过分派:
- `rel_zero_capture_close` 的五个出口: 全部改(§2)。
- `rel_cap_start` 失败: `rel_zero_capture_open` 在失败分支先 `rel_ctx_give_held()` 再返回, 归属没被占;
  上一轮已修, 本轮未改, A04/A14 继续覆盖。
- `rel_job_start`(learn 受理): 受理失败一律在持锁段内落 `why` 后放锁返回, 没有"占了 owner 就走"的路径。
- `rel_job_status`(查询): §1。
- `rel_cmd_commit`/`rel_cmd_clear` 的 `rel_pending_write` 失败: §3/§4 已把顺序换成"失败时改动面最小"的一侧。
- `rel_prod_publish` / `rel_ctl_take`(保护路径, diagnosis 线程): 本轮未动, 其取锁失败形状由 REL45/REL33
  等既有用例覆盖, 19 族负控重跑无回归。

## 6. 时间/截止与"不缩短判据"

`REL_CMD_DEADLINE_MS`、`REL_CTX_TAKE_MS`、线程栈、优先级四个常量本轮一字未动。
构建侧如实登记: `rel_cmd_commit` 的回滚需要一份 116 B 的 `diag_rprof_t` 局部副本, armlink 调用图
把它自己的帧推高 +112, 命令线程可见最深线由 rel_cmd_clear(1460) 换成 rel_cmd_commit(1556),
余量 4096-1556 = 2540 B。这是本轮**新增的**栈成本, 不是绕过的问题; 没有用"加大栈"或"延长超时"换绿。
数字出处见 `build/resource_stack_r7d1r.txt`。
