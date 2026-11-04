M8-R7D1 主机链对应表: 原 50 例的适配、A01..A12 的落点、负控原文
链: tests/host/run_diag_relative_m8.sh + tests/host/rel_main.c(真实产品 C 通过 #include 参与编译,
motor/sensor/Flash 为桩)。两轮都在本机 gcc 4.9.2 -Wall -Wextra 下**真实执行**, 不是静态判读。

## 0. 修前基线与本轮全链

  修前(基线 7885aea 原样, 未改产品 C): SUMMARY|cases=50 pass=50 fail=0 pre_bad=0 executed=50,
    build_rc=0 warnings=0, proc_rc=0, 判定 M8 RELATIVE RESULT: PASS(主机链, 离线; 现场项未验证)。
    逐例台账在仓外工作目录(/tmp/ns800-host-chain/m8/20261104T125524Z-1923), 本轮把它的
    REL25/39/42 三行留作下面的对照数。
  本轮(实现后): SUMMARY|cases=62 pass=62 fail=0 pre_bad=0 executed=62, build_rc=0 warnings=0,
    proc_rc=0; 逐例原文 host/normal_chain_out.txt(37600 B, 654 行, 控制台逐字节含 NUL 检查通过),
    整理后的逐例台账 host/case_ledger.txt(62 行, 不重排不省略)。
  声明用例数 62 == 执行用例数 62(runner 自己的对账行, 不是我用 grep 数出来的)。

## 1. 原 50 例: 全部保留, 三例改的是**推进方式**而不是判据

  50 例的 id 与 checks 数逐条见 host/case_ledger.txt。受异步合同影响的只有三例, 改动都是
  "把'命令线程内一次性完成'换成'受理 + 后台按拍推进 + 读终态', 原有断言一条没删":

  REL25-cli-capture-and-publish  基线 checks=41 -> 本轮 44
    改: do_cmd(cmd_learn) 之后直接断言 d_pending_learn 的地方, 前面插入
        CHECK(learn_run(400) == REL_JOB_DONE, "后台学习没在预算内 DONE: st=%d")
        —— 即先让 Diagnosis Thread 真跑若干拍, 再断言原来那条 pending 已发布的断言。
    改: "学习中途离窗"这一子段原来靠命令线程内的 pump 注入, 现在帧流由推进方带走, 所以注入点
        搬到 diag 节拍里(g_break_at_tick=20 + g_tick_hook=hook_break_window), 断言从
        cap_has("learn FAILED") 升级为 job_wait(...)==REL_JOB_FAILED **且** why=="WINDOW_BROKEN"
        —— 判据变严(原来只要求文本出现, 现在要求具名原因), 不是变松。
    保留: d_pending_learn==TRUE、样本数 adc/sg 各 >=8、签名/窗口/方向字段、发布后 show 的文本。

  REL39-zero-static-and-failed-relearn  基线 checks=11 -> 本轮 15
    改: 重学一次改成 learn_run/job_wait, 并按新合同补断言:
        被拒绝的重学**不得**清掉旧 learn 凭据、受理成立那一刻旧凭据必须已失效。
    保留: 零点必须静止+READY、失败路径不发布候选。

  REL42-low-sg-committable-and-named-reject  基线 checks=31 -> 本轮 35
    改: 走 learn 的部分同样换成受理+推进; 阈值可提交与被具名拒绝两类断言原样保留,
        另把 learn_status 的文本纳入检查(DONE 时才允许出现 "job thr tier=")。
    保留: 低 SG 仍可提交、被拒时 why 具名。

  其余 47 例: 把两轮台账的 (用例 id -> checks 数) 做机器比对, 47 例逐例完全相同, 50 个 id 一个
  没少(missing=0)、没有新增/改名; 原 50 例的断言总数 1148 -> 1159(净 +11, 全部落在上面三例里,
  只增不减), A01..A12 另加 172 条断言。

## 2. 新验收组 A01..A12 -> 具体断言/日志(逐条对应任务书 §7 的表)

  A01-start-returns-without-sample-wait      checks=15
    断言: 零新样本下 do_cmd(cmd_learn) 立即返回且打出 learn STARTED id=…; 作业仍停在
    RUNNING/等待样本(why=RUNNING); 返回后同一条 shell 能立刻进 cmd_zero/cmd_show 路径。
    原文行: host/normal_chain_out.txt 内 [DIAG] learn STARTED id=… / NOT learn OK yet FIELD_UNVERIFIED。
  A02-independent-samples-publish-candidate  checks=15
    断言: 只有 SG/ADC 各拿到独立新样本才发布; 重复 seq 不计数; 发布后 cand 的样本数/出处/
    签名与阈值生成保持原口径, DONE 才出现 job thr tier=… FIELD_UNVERIFIED。
  A03-consistent-read-only-status            checks=8
    断言: RUNNING 与终态各取一次整份快照; 连读两次内容一致; 读状态不推进 got_*、不改凭据。
  A04-strict-reject-and-busy-before-side-effects checks=23
    断言: N=7/窗反/DIR=3/JOB_ID=abc 等非法参数在**任何**副作用前被拒(d_cap、d_pending、
    两个凭据、已提交 profile 全部逐字段不变); 活动任务期间第二条 learn 得 capture busy。
  A05-cancel-terminates-without-new-frames   checks=12
    断言: 零新帧也能取消; 一次推进即终态 CANCELLED(why=CANCELLED), d_cap 释放、owner 回 FREE,
    取消后不发布候选、learn 凭据不被点亮, 且可再次受理。
  A06-cancel-versus-completion-linearized    checks=10
    断言: 两种交错分别正确(cancel 先受理 -> CANCELLED 且不得翻成 DONE; 完成先发生 ->
    cancel 明确回 already DONE, 不谎称取消成功); 终态在一次链里只产生一次。
  A07-stale-id-and-quiescence-guard          checks=11
    断言: 陈旧 job_id 取消不了新任务(NOT-CANCELLED/no such job 两类可辨); 归属未归还前
    不接受新任务; 终结顺序(先拆采集器归还 owner, 再落终态)不可反。
  A08-timeout-advances-without-frames-and-wrap checks=15
    断言: 无帧/重复帧/源冻结下 12 s 预算照样走到 TIMEOUT 且不发布; 含 deadline 回绕用例
    (deadline < now 的跨界仍判到期)。
  A09-motion-mode-signature-change-aborts    checks=10
    断言: 离窗/方向或驱动签名漂移/模式离开 MONITOR_ONLY -> 后续推进中具名终结
    (WINDOW_BROKEN / SIG_CHANGED / MODE_CHANGED), 终结后旧帧不得补成成功; 同一条 shell
    不再等 learn 就能进停止/禁能命令。
  A10-failed-relearn-invalidates-credential  checks=18
    断言: 失败重学保留旧凭据; **受理成立**那一刻旧 learn 凭据即失效(1c 步); 取消/超时
    同样不复用旧资格; 据此不能 commit 新窗口; 完整成功后显式 commit 仍走原静止事务。
  A11-mutator-exclusion-and-lock-context-failures checks=28
    断言: 活动学习期间 zero/tier/commit/clear 各自被拒且不写任何东西; show/learn_status 快照
    一致; 注入"取 rel_ctx 锁第 k 次失败"与"取 cfg 锁失败"两类故障, 拒绝文本可辨且无半写;
    ctx 未初始化时命令明确拒绝而不是假成功。
  A12-bounded-calls-utf8-no-nul              checks=7
    断言: 用最大整数(id 999999999、want/256、hz 20000、负 left_ms)与最宽状态/原因字符串展开
    每次调用, 逐调用 <=120 B 含源 LF; 采集缓冲里无裸 0x00、整体严格 UTF-8 可解码;
    截断标志(vsnprintf 计数 != strlen)必须为假。原文见 host/a12_print_bounds.txt。

  夹具新增件(只为把上述判据变成可判定, 不改产品逻辑): g_cap_truncated + cap_all_ascii()、
  只数 "rel_ctx" 的 g_ctx_take_calls/g_ctx_take_fail_after、只数 "cfg" 的 g_cfg_take_fail、
  g_break_at_tick + hook_break_window/hook_flip_dir、job_wait/job_snap/job_active/learn_run、
  prep_zero_cruise。world_reset 现在还清 d_job/d_cap_owner/d_job_next_id/d_ctx_* —— 上一版
  没清, 于是前一例遗留的 RUNNING 作业会让后续每一条 zero 都打 capture busy(那是夹具污染,
  不是产品行为; 发现后按每例复位处理)。

## 3. 负控(反向注入)原文与判据

  机制: 把产品 C 拷到**副本**上做单点注入, 用同一份 rel_main.c 重编重跑; 判据是
  "注入必须命中(打印 NEGATIVE-INJECTION 且只命中一次)+ 编译成功 + 指定的业务用例具名失败 +
  proc_rc=1"。编译报错不算检出。全链原文 host/reverse_run_full.txt(9536 B), 汇总 14/14 META-OK:

    RV1..RV10(既有十族, 本轮全部仍命中, 证明改动没把旧缺陷族变成看不见。下面每一行
      都是 host/reverse_run_full.txt 里"命中期望失败身份"的原文, 不是我按族名推的)
      RV1_sg_new_sample_identity_gate           -> REL08-stale-sg-cannot-accumulate
      RV2_adc_relative_severe_branch            -> REL09-adc-severe-both-directions,
                                                   REL28-strong-needs-own-evidence
      RV3_relative_mode_profile_qualification   -> REL16-relative-mode-arm-gate
      RV4_severe_borrows_warn_duration          -> REL28-strong-needs-own-evidence
      RV5_coverage_loss_protects_nothing        -> REL22-coverage-loss-protects-first,
                                                   REL33-lock-fail-bounded-degrade,
                                                   REL34-no-frame-still-supervises,
                                                   REL36-null-outparams-still-protects
      RV6_failed_relearn_keeps_credential       -> REL39-zero-static-and-failed-relearn,
                                                   A10-failed-relearn-invalidates-credential
      RV7_impact_independent_trip_removed       -> REL44-impact-fresh-evidence-only
      RV8_ctl_lock_silently_bypassed            -> REL45-ctl-take-failure-writes-nothing,
                                                   REL46-ctl-uninit-take-and-protect
      RV9_new_generation_keeps_old_evidence     -> REL48-new-generation-clears-evidence
      RV10_motion_age_dropped_from_trust        -> REL50-stale-idle-not-static-exemption,
                                                   REL49-frame-not-same-as-motion-known

    RDV1..RDV4(本轮新增四族, 对应任务书点名的"取消后仍发布 / 无帧时不推进超时 / start 又回到
      等待样本"外加一条凭据时序)
      RDV1_cancel_branch_short_circuited(取消分支不再优先, 直接走完成/发布)
        -> 命中 A05-cancel-terminates-without-new-frames 与 A06-cancel-versus-completion-linearized
           (附带 A07/A09/A10 也失败, 如实登记为"额外", 不改变判定)
      RDV2_timeout_only_advances_with_frames(把 deadline 检查挪进"有新帧"分支)
        -> 命中 A08-timeout-advances-without-frames-and-wrap
      RDV3_start_waits_for_samples_again(learn 受理后再等样本地回滚到同步形态)
        -> 命中 A01-start-returns-without-sample-wait(附带 REL25/39/42 与 A02..A10, 共 12 行 FAIL)
      RDV4_credential_cleared_before_refusal(把"作废旧 learn 凭据"挪到拒绝之前)
        -> 命中 A10-failed-relearn-invalidates-credential

  RV6 的锚点本轮必须跟着改: 它的旧锚点(同步 learn 里的那行清凭据)在改成异步后已不存在,
  第一次跑就报 INFRA-FAIL 注入未落地/锚点不唯一(ANCHOR-COUNT=0)。现在钉的是新等价行
      d_pending_learn = RT_FALSE;       /* 新学习受理: 旧 learn 成功凭据立即失效 */
  并同步把期望失败身份扩为 REL39 + A10 —— 这是"锚点漂移"的修正, 不是把负控删掉换全绿。

  判据口径(与上一轮 R7F4 已更正的一致): 负控族数按 TAB/预期矩阵计, "命中 1 个失败身份"与
  "实际 FAIL 行数"分开报; 额外失败身份登记但不改变判定; 异常退出与未命中不算检出。

## 4. 这一节能证明什么、不能证明什么

  能: 受理不等待、后台按拍推进、取消与完成的先后可线性化、陈旧 id 不影响新任务、超时不因
  缺样本而冻结、互斥与拒绝发生在副作用之前、发布与凭据在同一条路上、打印有界且字节完整。
  不能: 板端真实抢占并发下的交错(这里是协作式调度 + 虚拟 tick)、真实 UART/控制台时序、
  真实停机时延(motor 在本链是桩, 只证明入口可达与资格变化)、任何物理保护已证。
  本轮 0 次设备动作; 新镜像 NOT_ON_BOARD; polarity_measured / core_protection_verified 不变。
