M8-R7D1 实现合同说明(产品 C 改动做什么、凭什么、以及没做什么)
基线 7885aea606a50f1c34723dbfa9b8c0c4341326de; 任务书 NS800_R7D1_Offline_Async_Learn_Task_20261104.zip
(13467 B, sha256=b35e0a2e16a680ff90a37d2edb1de9da6c85adbc8011ccea33c5354e37922c5c, 175 行)。
本轮全部证据为离线: 主机 C 真实执行 + 本机 Keil 构建。设备动作 0, 新镜像 NOT_ON_BOARD。

产品侧改动只有三个文件(逐字节 diff 在 code/product_diff_vs_7885aea.diff, +656/-117 行含上下文头):
  applications/diag_relative.c   103684 B  disk sha256=f2c7089b3d5a21479a5936bab5b…(manifest 有全值)
  applications/diag_relative.h    25422 B
  applications/diagnosis.c        54696 B  (只加一行调用 + 注释)
行号都取自本轮磁盘字节, 与 manifest 的 source_inputs_compiled_from_disk 同一份文件。

## 1. 命令合同的变化(任务书 §2 第一条)

旧: `diag_profile learn N LO HI DIR` 在命令线程里 rt_thread_mdelay 轮询, 最长约 12 s 不返回。
新: 受理即返回, 采样与判定由 Diagnosis Thread 每拍推进, 另开两条查询/取消命令。

  rel_cmd_learn(diag_relative.c:1943) — 只做四道门与一次受理: DIR 只允许 0/1(1950-1955)、
    rel_pending_read 取一次整份快照(取不到就具名拒绝)、零点凭据必须在(1960-1962)、
    模式必须是 MONITOR_ONLY(1964-1966); 然后把窗口/方向/签名改在**私有候选**这份拷贝上,
    交 rel_job_start。打印
      [DIAG] learn STARTED id=%u n=%u win=%u..%u dir=%u
      [DIAG] learn async: query 'learn_status'; NOT learn OK yet FIELD_UNVERIFIED
    它不再调 rel_cap_*、不再 rt_thread_mdelay、不再 rt_tick_get 轮询 —— 这一条不止是我写的 C
    长这样, 链接器调用图也证实了(rel_cmd_learn 的子级里 rt_thread_mdelay/rel_cap_start/
    rel_cap_feed/rel_cap_done/rel_cap_finish/rel_cap_abort/rel_frame_to_input 全部消失,
    见 build/stack_resource_r7d1.txt §3)。
  learn_status(diag_relative.c:1982) — 一次 rel_job_status() 取到**整份**快照后打印, 不改任何状态。
    行: job id/st/why/owner/cred、job n=/want/win/dir、job left=%dms of %ums software budget、
    job data adc[..]n.. nz.. sg[..]n.. seq..; 终态 DONE 另加 job thr tier=… FIELD_UNVERIFIED 与
    job DONE result; cred zero=%d learn=%d commit is separate。
  learn_cancel JOB_ID(diag_relative.c:2023) — JOB_ID 严格 1..999999999; 五种结果分别是
    REQUESTED / NOT-CANCELLED: already / no such job / ctx lock / not initialized,
    互相可辨, 不把"取消不了"说成"取消成功"。
  MSH 用法行扩到 …|clear CONFIRM|learn_status|learn_cancel ID(diag_relative.c:2247, 整串 106 B,
    含源 LF 共 107 B)。

## 2. 谁能改任务状态: 单写者 + 一把叶锁

  状态机七态(diag_relative.h 的 rel_job_state_t): IDLE / RUNNING / CANCEL_PENDING / DONE /
    FAILED / CANCELLED / TIMEOUT。
  写 RUNNING→终态的**唯一**位置是 rel_job_advance(diag_relative.c:1661), 它只在 Diagnosis Thread
    的每拍里被调用(diagnosis.c 的那一行排在 rel_prod_evaluate 之后、所有披露打印之前)。
    命令线程只有两种动作: rel_job_start 受理(→RUNNING)与 rel_job_cancel 立旗(→CANCEL_PENDING)。
  所以"取消刚返回 OK, 完成又把终态翻回 DONE"这种交错在源码上不成立: 两个候选终态由同一段
    持锁代码按固定顺序产生 —— 先看 CANCEL_PENDING(diag_relative.c:1673)直接终结为 CANCELLED,
    才轮到资格/超时/喂样本/发布。用例 A06 用真实 C 调度把两种交错各跑一遍, 断言终态只出现一次。
  d_ctx_lock("rel_ctx", diag_relative.c:1371 附近的定义与 rel_ctx_take_tmo)是**叶子锁**:
    临界区内不调 motor/cfg/Flash/UART/mdelay/print。允许的嵌套方向只有 mot/cfg → ctx,
    反向不存在(这点沿用 M8-R1 R3.3 记录的既有锁序, 本轮没有新增反向)。
    取不到锁的三个来源在合同上可辨: 未初始化 = -RT_EINVAL, 20 ms 有界等待失败 = -RT_ETIMEOUT,
    业务上的"采集器已被占" = -RT_EBUSY(diag_relative.c:1588-1609)。
    rel_lock_why(1477)/rel_ctx_gate(1555)/rel_accept_refused(1847) 把这三条落到不同文本上,
    所以拒绝理由不会说谎(例如锁失败绝不会被印成 capture busy)。

## 3. 采集器排他与归属(旧实现没有的东西)

  d_cap 一直存在, 但旧注释写的是"只有命令线程用它"; learn 改后台之后这个前提就没了。
  新增 d_cap_owner(diag_relative.h 的 rel_cap_owner_t): FREE / BY_ZERO / BY_JOB, 由受理者写、
    终结者清。关键是**归属与 rel_cap_start/abort 在同一个临界区里换**, 不引入第二把锁:
      rel_zero_capture_open(1486) — zero 用; rel_job_start(1611-1612) — 作业用;
      rel_job_term_locked(1653-1659) — 先 rel_cap_abort() + 归还 FREE, **再**落终态。
    顺序反过来会留下"终态已可见但 d_cap 还占着"的组合, 而那正是"刚答应取消就允许新任务复用
    旧推进正在用的采集器"的入口(A07 的判定点)。

## 4. 修掉的两条基线缺陷(方向都是"被拒绝不得销毁既有资格")

  旧 learn: 先把 d_pending_learn 清掉, 再发现采集器忙而返回拒绝 —— 一条被拒绝的命令把上一轮
    learn 的成功凭据烧了。新写法在 rel_job_start 里反过来了: 先在局部 nj 装好新任务
    (diag_relative.c:1596-1606), 唯一可能失败的 rel_cap_start 一旦失败就原样退出(1608-1609,
    此时 d_job / d_cap_owner / d_pending_learn 一个都没动), **受理成立**才 d_pending_learn=FALSE
    (1615)。同一行的语义由反向 RDV4 钉住: 把它挪回入口处(作废旧凭据先于拒绝), A10 必须具名失败。
  旧 zero: 同一个方向错误(zero 分支在 1860 起, 现在先 rel_zero_capture_open 拿到归属,
    才 rel_pending_write(RT_NULL,0,0) 作废零点凭据; 作废时取不到锁就整条回滚并打印
    "zero FAILED: ctx lock on invalidate", 不留半写状态)。

## 5. 时间预算、回绕与"没有样本也要推进"

  REL_CMD_DEADLINE_MS 12000u(diag_relative.c:1337)保持旧命令的约 12 s 总预算, 没有为了"看起来不卡"
    缩短; REL_MS_TO_TICK(1377)把 ms→tick 的换算集中在一处并以 RT_TICK_PER_SECOND==1000 为前提
    (主机 shim 也显式定义了这个值, 让离线模型与固件同一口径); 到期判定用
    (rt_int32_t)(deadline - rt_tick_get()) <= 0(diag_relative.c:1686), 对 tick 回绕是同余正确的。
  rel_job_advance 在 have_frame=0 的拍里照样走完 1)/2)/3) 三段, 所以"帧流停了/源冻结"不会把
    12 s 超时一起冻住(A08; 反向 RDV2 把 deadline 检查移到需要新帧的分支里, A08 具名失败)。
  喂样本只认**新帧**(in->seq != d_job.last_frame_seq, diag_relative.c:1692), 与旧命令线程
    rel_frame_to_input 的 seen 去重语义一致; 重复旧帧既凑不够样本也开不了新窗口。

## 6. 发布、凭据与 commit 的分界

  成功路径在同一个临界区里做四件事(diag_relative.c:1706-1715): rel_cap_finish 生成到静态槽
    d_job.out(不在 4 KB 的 Diagnosis Thread 栈上再开一份 diag_rprof_t)→ d_pending = d_job.out →
    d_pending_learn = TRUE → out_valid=1 → 然后才是 DONE。因此 learn_status 读到 DONE 时,
    候选与凭据必然已经可读, 不存在"DONE 但候选是空的"这种组合(读者要么整份要么明确读不到)。
  失败/超时/取消路径一个字段都不写, 也不置凭据; commit 仍走原有的显式静止事务(rel_cmd_commit,
    diag_relative.c:2085), 本轮没有为让学习"通过"而改模式、自动 arm/start、自动提交或改阈值公式。
  打印末尾一律 FIELD_UNVERIFIED: 学习成功 ≠ 保护已现场验证。

## 7. 打印限长(任务书 §6)

  本轮新增/改动的 29 个 rt_kprintf 调用逐个按**实参最宽合法展开**折算(不是数格式串长度), 每次调用
  最宽 120 B 含源 LF, 等于申报预算; 判据既在离线工具里(r7d1_offline_checks.py 的
  TOUCHED_PRINTS_WITHIN_120 + PRINT_BOUND_NOT_VACUOUS, 后者保证"若折算没生效就报警"), 也在
  主机链 A12 里用同一 120 B 预算实测(含裸 NUL/UTF-8 完整性)。%s 的宽度取自有限来源:
  rel_job_state_name 最长 14、rel_lock_why 最长 13、why 字符串最长 25、who 最长 6 —— 逐条列在
  offline/summary.json 的 printf_width_table 与 host/a12_print_bounds.txt。
  旧镜像登记的 35 条超长格式串没有因此关闭, 未触及条目仍是待办; 本轮没做全仓打印改造。

## 8. 本轮没有做的(不要从上面的数反推)

  没有现场执行: 不碰 COM5/DAP/烧录/复位/回读/逻辑分析仪/arm/start/运动/真实学习/写配置/供断电。
  新镜像未上板(NOT_ON_BOARD), 板上仍是历史 b502 证明(502dca5d…)。
  主机链的虚拟 tick 与协作调度只证明软件逻辑, 不证明板端抢占并发、真实停止时延或物理保护。
  motor 侧用的是既有桩: 只能据其证明入口可达与学习资格变化, 不写成真实停机已证。
  R7F5A 的物理断电待办仍是 PENDING_OPERATOR_REPORT, 本轮未连接任何设备去关闭它。
  后续现场学习计划必须按新命令合同重排(旧的 field 计划/rows/b502 证据/历史回包保持原件未改)。
