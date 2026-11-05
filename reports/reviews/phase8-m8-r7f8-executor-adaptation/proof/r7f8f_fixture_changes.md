# R7F8F 夹具/期望逐项变更说明(基线 42d7f7d -> 工作树)

机器核对入口: `tools/r7f8f_fixture_change_table.py`(就是本文件的产出者)。基线与现件的判据名逐条对齐; 新增/移除必须带人写理由; 同名判据结论变化一律判红。

## proof/r7f8e_case_matrix.txt
- 家族: 反例矩阵(计划驱动的执行器行为反例 + 元负控)
- 基线判据名 65 个 / 现在 113 个 / 同名保留 65 / 新增 48 / 移除 0 / 结论变化 0
- 现在的汇总行: `R7F8E_MATRIX_SUMMARY|判据=113|FAIL=0|none`
  - **新增** `FB1_真字节经生产读路径逐字节留证且披露行在流里` —— 输入换成 2026-11-05 F11 板子真发的 983 B(proof/r7f8f_field_f11_sys_pre.rx.bin), 经**生产读路径** read_until_prompt 解析, 落盘字节必须与原捕获逐字节相等, 且披露行确实出现在流里。为什么不能再用夹具行: 上一版把 mask=0x18 记在 [TMC] 写门行上, 夹具因此永远测不到现场首错。
  - **新增** `FB2_正例_交付的 sys_pre 申报下真字节判 OK 且豁免行入台账` —— 正例: 交付计划里 sys_pre 的申报(整行锚定豁免 + expect_contains 正面要求 + 两行硬字段)对真字节判 verdict=OK, 并且台账里 BUSINESS_OUTCOME.status_lines_allowed 逐字带上被豁免的那一行 —— 豁免必须留痕, 静默丢弃等于把判据藏起来。
  - **新增** `FB3_反例_同一段真字节喂给动作步仍具名业务失败` —— 反例(作用域): 同一份 rows 喂给 arm 步(motor_arm, 未申报豁免)必须仍判 BUSINESS_FAILED 且命中行就是那条披露行。证豁免是**按步**申报的, 不会把一局里所有 REFUSED 线洗白 —— 这正是操作者选的修法①里「动作步一律不许豁免」那一半。
  - **新增** `FB4_元负控_摘掉豁免同一份真字节必须翻红` —— 元负控: 把 sys_pre 的 status_lines_allow 摘掉, 同一份真字节必须翻回 BUSINESS_FAILED。红了才证明这条申报是承重的判据, 不是装饰; 也证明 F11 那一次停止的根因确实在包侧缺申报。
  - **新增** `FB5_反例_豁免未被正面要求时运行期具名停止(fail-closed)` —— 反例(新门本身): 保留豁免但删掉 expect_contains 里那条披露串, 运行期必须具名停 STATUS_ALLOW_NOT_POSITIVELY_REQUIRED 并留台账事件。R7F8E 起注释就写着「必须同时被本步 expect 正面要求」但代码从未核过, 这条就是兑现它 —— 否则写一条豁免等于「长得像拒绝的都免检」。
  - **新增** `FB6_静态门_交付计划 PASS 且豁免只在两条查询步` —— 静态侧口径对齐: validate_plan 的 PLAN_STATUS_ALLOW 必须判绿, 且 38 步里申报豁免的**只有** sys_pre / sys_after_apply 两条 system_status 查询步。计划能过静态门却在现场被运行期停住, 就是 F11 那次的形状, 现在两边用同一条「正面要求」判据。
  - **新增** `FB7_静态门元负控_抽掉正面要求计划门必须翻红` —— 静态门的元负控: 只删 sys_pre 的正面要求, PLAN_STATUS_ALLOW 必须翻 FAIL 并把字面前缀与运行期具名码写进详情。同时它也是新前缀函数 status_allow_literal_prefix 的现场靶子 —— 冻结的 R7F7 计划整条模式用反斜杠转义空格, 把「反斜杠+空格」当通配会把前缀截成 [DIAG] 而假判红(本机第一次跑就撞上, 已按字面转义修正, 无回归族 9/0 复证)。
  - **新增** `G10_单字段变错(ENN_fb=1)` —— F4 反向家族(G9~G17): 把巡航响应里**某一个**字段改成禁能侧或矛盾值, 逐个证 running_contract 的每一项都真在判 —— 只靠 state 数值一个门会在其余字段错时仍放行。
  - **新增** `G11_单字段变错(verdict=CUT_BY_HARDWARE)` —— F4 反向家族(G9~G17): 同上, 改的是 fb 判定链(硬件切断/未解析都不算巡航)。
  - **新增** `G12_单字段变错(fb=UNRESOLVED)` —— F4 反向家族(G9~G17): 同上, 改的是 ENN 反馈解析状态。
  - **新增** `G13_单字段变错(drv_request=LOW)` —— F4 反向家族(G9~G17): 同上, 改的是使能请求脚位与 pad。
  - **新增** `G14_单字段变错(armed=0)` —— F4 反向家族(G9~G17): 同上, 改的是 armed 位(与 state 矛盾)。
  - **新增** `G15_单字段变错(health=FAULT)` —— F4 反向家族(G9~G17): 同上, 改的是子系统健康位。
  - **新增** `G16_单字段变错(!=tgt_hz)` —— F4 反向家族(G9~G17): 同上, 改的是 cur!=tgt(还在爬坡)。
  - **新增** `G17_单字段变错(expect_running.dir)` —— F4 反向家族(G9~G17): 同上, 改的是计划钉死的申报值(expect_running.dir)。
  - **新增** `G1_完整DONE正例(候选统计与进度一致)` —— F4/F1 正例族: 完整 DONE(候选统计与进度行一致)是「提交资格」的唯一正形状, 缺任一条 diag_relative.c:2056/2068/2074 的行都得具名拒。
  - **新增** `G2_受理n与实发n不符在绑定处拒` —— F4/F1 正例族: 受理的 want 与实发 n 不符时, 在**作业绑定**处就拒, 不留给后面某一步翻案。
  - **新增** `G3_取消前出现CANCEL_PENDING即矛盾` —— F4/F1 正例族: 取消之前出现 CANCEL_PENDING 属矛盾(产品只在取消请求之后打这个态)。
  - **新增** `G4_取消后到CANCELLED为止都算进行中` —— F4/F1 正例族: 取消请求之后到 CANCELLED 之前,RUNNING/CANCEL_PENDING 都算进行中 —— 轮询要能等, 但业务终态不算进行中。
  - **新增** `G5_计数单调上升的正例(3/3到6/6到DONE 8/8)` —— F4/F1 正例族: 两路计数单调上升(3/3 到 6/6 到 DONE 8/8)是正形状。
  - **新增** `G6_较快源超过N不是假上限(adc=12 而 want=8 仍算成功)` —— F4/F1 正例族: 较快源可以超过 N(rel_cap_feed 不在单源达 N 时停采), 所以 count<=N 不能当门 —— 这条专门钉住「别把假上限写回判据」。
  - **新增** `G7_候选统计与进度行不符具名拒` —— F4/F1 正例族: 候选统计与进度行不符 => 具名拒绝(不许用后面的 DONE 洗绿)。
  - **新增** `G8_巡航正例(使能侧与申报频率全项自洽)` —— F4 正例: 巡航响应在使能侧字段与申报频率上**全项自洽**才算 RUNNING(与 G9~G17 的逐字段反向配对)。
  - **新增** `G9_单字段变错(expected_ENN=HIGH)` —— F4 反向家族(G9~G17): 把巡航响应里**某一个**字段改成禁能侧或矛盾值, 逐个证 running_contract 的每一项都真在判 —— 只靠 state 数值一个门会在其余字段错时仍放行。
  - **新增** `H10_开过IRQ但从未arm也要收口(尾部两条照发)` —— F5b①(A 的 stub 复现第一项): 真实序列是先开 IRQ 再 arm, 中间任何失败都落在电机义务还没成立的窗口里; 修前 finally 只看电机义务 => 一条 disable/status 都不发。这条正面判「尝试过开 IRQ 本身就建立尾部清理义务」。
  - **新增** `H11_收口全部无响应时五条各有发送机会(7秒预留)` —— F5b②(A 的 stub 复现第二项): 默认 4 s 预留排不下五条各 1.2 s 的确认窗, 全部无响应时第五条必被 *_HARD_DEADLINE_BEFORE_* 静默跳过。这条数 CLEANUP_ENTERED 之后的实发条数与首末跨度, 证「五条都拿到自己的窗」。
  - **新增** `H12_申报排不下五条的预留就在开口之前拒` —— F5b②的开口前那半: 申报的预留排不下五条各 1.2 s 窗时, CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS 在端口/额度/批准账**之前**拒, 一条命令都不发(副作用仍然零)。
  - **新增** `H13_前置等待耗掉会话后业务在收口预留之前停下` —— F5c(A 的补充复现: 10 秒会话 + 申报 7 秒预留, 前置等待后第 9 秒仍允许 arm): 业务绝对截止必须提前到「总截止 - 预留」, 起转前的等待/轮询/读/写前检查一律受它管。这条的反向突变是X6(把 business_abs_dl 退回 session_abs_dl)。
  - **新增** `H14_义务早建立且收口全静默_五条仍拿到预留窗口` —— 同一缺口在 IRQ-only 场上的形状: 撤能尾部必须发得出 safety_irq_status(修前被业务吃光 => 静默跳过)。反向突变是 X7。
  - **新增** `H1_arm迟到回包在通电截止即进收口` —— F2/F3: arm 回包迟到超过通电截止 => 在截止点就进收口, 不等那份迟到回包(A 的 P6 意图)。
  - **新增** `H2_第二次arm在写入尝试之前拒` —— F3: 第二次 motor_arm 在**写入尝试之前**拒(ARM_SECOND_ATTEMPT_REFUSED), 不烧额度。
  - **新增** `H3_第二次start在写入尝试之前拒` —— F3: 第二次 motor_start 同样在写入之前拒(MOTOR_START_SECOND_REFUSED)。
  - **新增** `H4_泛匹配DISARM字样不解除通电义务` —— F3: 泛匹配 DISARM 字样不解除通电义务 —— 只认产品 :1415 那条动作已做的完整行。
  - **新增** `H5_开过IRQ则固定尾部各一条同一端口` —— F5: 开过 IRQ 则固定尾部两条在同一端口内按序各发一次(名字里带触发条件)。
  - **新增** `H6_尾部没回CLOSED只追加披露不改撤能结论` —— F5: 尾部没回 CLOSED 只**追加**披露(IRQ_TAIL_NOT_CLOSED), 不改已经成立的撤能结论 —— 失败归因不许互相顶。
  - **新增** `H7_没开IRQ就不发尾部两条` —— F5: 没开过 IRQ 就不发尾部两条(不发无关命令, 也不占额度)。
  - **新增** `H8_会话截止不随步骤续期` —— F2: 会话截止 = 台账 t0 + 总时限, 不随步骤续期(修前每步重新「当前时刻 + 总时限」)。
  - **新增** `H9_清理后仍巡航必须判红(不是业务码独大)` —— F3/F4: 清理之后仍看到巡航 => 判红, 不被业务成功码顶掉(两条结论各归各的门)。
  - **新增** `K1_step_window_保留小数且不叠_SLACK` —— 裁定二的正面: 计划申报的 step_total_seconds 就是这一步的**整窗**(等待+余量都在里面), 执行器原样取用且保留小数; 只有**未申报**的旧计划才退回 seconds+SLACK(3 s)。这条把「同一值」的读取侧钉住, 否则检查器与执行器会各算一套。
  - **新增** `K2_非法总窗逐项具名且轮询窗受本步总窗约束` —— 裁定二第 2 条的拒绝面: bool / 非数 / 非正 / nan / inf 六种非法申报值必须在**开端口之前**各自具名拒绝(不许静默退回默认窗); 同时轮询步的 until 若大于本步总窗 => POLL_UNTIL_EXCEEDS_STEP_TOTAL, until==总窗必须放行(否则会把合法申报判死)。
  - **新增** `K3_零速合同只认完整自洽的新鲜快照` —— 裁定一里「可信零速」的**定义**: 只有 state/cur/tgt/health/armed/使能侧/verdict 全项自洽的那一行才算(armed=1 的「已零速但仍带电」正是 stop 之后的典型态, 也照认); FAULT / CUT_BY_HARDWARE / 使能侧矛盾一律不算 => 运动计时不解除。这一条不给「看见一个 0 就松手」留口子。
  - **新增** `K4_运动计时只在可信零速处结束_迟到不倒填_t0 不清` —— 裁定一的第 1、2 点: 结束点是**正常 stop 成功之后第一份新鲜完整零速快照**; 13 s 才到的零速具名 MOTION_ZERO_CONFIRMATION_LATE(不许把它倒填成按时), 第二次确认具名MOTION_END_ALREADY_CONFIRMED; 且 MotionBudget.t0 保持不清(修前正是靠 stop 清 t0 才让第二次start 混过去)。
  - **新增** `K5_释放运动计时不释放通电计时_也不放开业务截止` —— 裁定一的边界: 解除的只是 cap(那段不再被 t0+12 夹住), arm→禁能的 18 s 通电截止与业务绝对截止(总窗-预留)都**照旧**; 收口预留从「现在+7 s」起算但仍被会话 90 s 封顶。这条专门防止「为了排下尾巴把别的门一起放开」。
  - **新增** `K6_已零速但仍带电到_18s_仍是到期停止业务` —— K5 的反向配对观察: 零速已解除运动计时(cap 剩余为负)时, 若带电拖到 18.5 s, ARM_ENERGIZE_DEADLINE_EXCEEDED 那条支路**不看** cap_active 仍然开火 => 两个计时器彼此独立不是口头声明。
  - **新增** `K7_38_步总窗逐条等于_A_的表_镜像同函数产出` —— 裁定 §3 那张表的机器化: 31.0/9.0/7.5 三段、合计 47.5、stop@7.5 / 零速@9.0 / 禁能@13.0、保守式 57.5 与 16.0 全部由执行器同一个函数现算, 计划里的镜像也由它产出 => 「检查器与执行器同一值」不是两边各写一遍再对拍。
  - **新增** `K8_轮询步的总窗等于其 until_次数与间隔未动` —— 裁定二末句的落实: 四个轮询步(巡航 4×0.1、取消 3×1、完成 12×2、停机 5×1)的申报总窗 == 原定 until_seconds(1/1/2/1), 次数与间隔一字未改 —— 防止用「缩窗」把尾巴塞进 cap。
  - **新增** `X6_把收口预留从业务预算里抹掉_前置等待后arm仍放出去` —— 元负控(F5c): 把 business_abs_dl 退回 session_abs_dl(等于抹掉收口预留), H13 必须因此失去 STEP_DEADLINE_BEFORE_WRITE 且 arm 真的被放出去 —— 证这道门不是写在纸上的。
  - **新增** `X7_同一道门_静默收口时第五条发不出去` —— 元负控(F5c): 同一道门在「收口第五条发不出去」那一面的形状, 必须让 H14 失去 safety_irq_status。

## proof/r7f8e_stub_package.txt
- 家族: stub 整包(合成计划过全部离线门 + 逐场首错)
- 基线判据名 27 个 / 现在 29 个 / 同名保留 27 / 新增 2 / 移除 0 / 结论变化 0
- 现在的汇总行: `R7F8E_STUB_SUMMARY|判据=29|FAIL=0|none`
  - **新增** `交付件里不留仓外试验目录(四种形态逐一掩成 <WORK>)` —— R7F8E 交付轮就有的脱敏判据(e97aaa4), 在基线 proof 里名字带「<WORK>/」的四种形态; 本轮沿用未改, 之所以出现在「新增」里是因为基线把这一族合成了一条名字不同的判据。
  - **新增** `这条掩码判据不是恒真空(掩前确实出现过)` —— 同上, 脱敏判据的自证: 掩之前必须真的命中过, 否则「全绿」只是没东西可掩。

## proof/r7f8e_no_regression.txt
- 家族: 无回归(冻结旧件 vs 派生件对同一份 R7F7 定稿计划逐行比)
- 基线判据名 32 个 / 现在 37 个 / 同名保留 32 / 新增 5 / 移除 0 / 结论变化 0
- 现在的汇总行: `R7F8E_NOREG_SUMMARY|判据=9|FAIL=0|none`
  - **新增** `REPLAY|business_total=133 budget=203.0 cons_total=None cons_arm=None` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|declared=0/14 legacy=14 非法=无 zero_evidence_as=NONE(运动计时不解除)` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|may_obligate=False spin@None stop@None zero@None pre_start=0.0(0步) motion_active=0.0(0步) post_zero=0.0(0步) stop_after_start=None zero_after_start=None arm_to_disabled=None cap=None reserve=7.0 total=210.0 business_total=133 worst=None cons_total=None cons_arm=None mirror=NA(计划未申报 motion_budget.business_segments)` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|reserve=7.0 floor=6.0 total=210.0 cap=None` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `点名的本轮新门确实出现在新增行里(白名单非空转)` —— 上一版白名单只按前缀放行, 新加的 PLAN_* 门被拒 => 改成点名。点名之后必须**反过来**验它真的出现过, 否则白名单退化成「任何 PLAN_ 新行都免检」。

## proof/r7f8f_probe_replay.txt
- 家族: 三版本重放(修后 / F5b 修前 / F5c 修前 同输入对照)
- 基线判据名 无该件(本轮新增) 个 / 现在 7 个 / 同名保留 0 / 新增 7 / 移除 0 / 结论变化 0
- 现在的汇总行: `REPLAY_SUMMARY|件数=21|每条件数=3|FAIL=0|none`
  - **新增** `REPLAY|I1_P8意图_修前整条巡航合同(禁能侧ENN)判红` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|I2_P7意图_第二次arm在写入尝试之前拒` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|I3_P6意图_arm迟到回包在通电截止即进收口` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|I4_F5b1_开IRQ后arm前失败_尾部disable与status仍发出` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|I5_F5b2_收口全部无响应_五条各有发送机会` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|I6_F5b2_申报4秒预留在开口之前拒(旧件没有这条 CLI 申报口)` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|I7_F5c_前置等待后arm仍放出去_收口拿不到预留` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。

## proof/r7f8f_offline_gate.txt
- 家族: 最终离线门(real plan/rows 整包 + 全首错 + 不变项)
- 基线判据名 无该件(本轮新增) 个 / 现在 39 个 / 同名保留 0 / 新增 39 / 移除 0 / 结论变化 0
- 现在的汇总行: `R7F8F_OFFLINE_GATE_SUMMARY|判据=47|FAIL=0|none`
  - **新增** `BOARD_IMAGE_IDENTITY_STILL_23691E8D` —— 在板镜像原件仍是 297452 B / 23691e8d…, 本轮不下载/不回读, 身份只作参照(§4 第一行)。
  - **新增** `FIELD_FLAGS_STAY_FALSE` —— polarity_measured / core_protection_verified 保持 false(任务书硬边界: 软件 confirm 不升级为极性实测)。
  - **新增** `FIELD_PLAN_PREREQ_NOT_SELF_CERTIFIED` —— 机械前置门在现场原件里保持「等操作者口头确认」, 计划不自证; 离线 dispatch 副本另置 PROVIDED 只为了让行为判据有东西可判。
  - **新增** `FROZEN_OLD_EXECUTOR_UNCHANGED` —— 冻结件身份核对(§3.1): 旧执行器一字节未动。
  - **新增** `IMAGE_SHA_COMES_FROM_DISK_BYTES` —— 计划里的镜像摘要 == 本工具当场对盘上原字节算出的摘要(§4 的 23691e8d… 不是手抄)。
  - **新增** `INPUTS_IN_PLACE` —— 五件输入齐全且身份当场重算(执行器/plan/rows/修前件/BIN)。
  - **新增** `JSON_RECEIPT_SHAPE_ALIGNED` —— 机器回执自身的形状判据: 每条判据在记账时就分成 门名/判定值/详情 三个字段。上一版 json 用按位切分解析文本行, 而门名本身可以含 "|"(GATE_PRESENT|X、FIRST_ERROR|tag), 于是 13 条把门名当成了判定值(如 verdict="PLAN_CLEANUP_RESERVE")。这条判据把"结构化条数=文本行数、判定值只会是 PASS/FAIL、FAIL 名单与汇总行一致"钉住, 属于回执可解析性补正, 不改任何行为判定; 同输入重跑后 40 条的 PASS/FAIL 与补正前的 39 条逐条一致。
  - **新增** `LEGACY_RED_TEXT_KEPT_VERBATIM` —— 裁定前的两条红(91 s / 23 s 那组数)原文照录在回执里, 不因为现在绿了就抹掉(任务书 §3.7 「保留失败原文」)。这条判的是**留痕本身**, 不参与任何放行。
  - **新增** `META_DOUBLE_COUNT_COMES_BACK_GOES_RED` —— 元负控: 故意让镜像漂移/把双算加回来, 要求对应的门**必须变红** —— 证 F6 的新门不是恒绿(任务书 §3.2)。
  - **新增** `META_MIRROR_DRIFT_NAMED` —— 元负控: 故意让镜像漂移/把双算加回来, 要求对应的门**必须变红** —— 证 F6 的新门不是恒绿(任务书 §3.2)。
  - **新增** `META_MUTATION_ANCHOR_HIT` —— 元负控: 故意让镜像漂移/把双算加回来, 要求对应的门**必须变红** —— 证 F6 的新门不是恒绿(任务书 §3.2)。
  - **新增** `MOTION_AND_ENERGIZE_DEADLINES` —— §4 的四个固定数(12/18/90/7)逐字段核对, 并把它按三段拆开(A 的补充审核项)。
  - **新增** `NO_DEVICE_ACTIONS_DECLARED` —— 不变项与「本轮零设备动作」的机器核对(任务书 §3.1/§3.5): 额度申报、台账里的 PORT_OPEN 事件、以及每条 dispatch 都只走 FakePort。
  - **新增** `NO_HARDWARE_ACTIONS_INSIDE_THIS_GATE` —— 不变项与「本轮零设备动作」的机器核对(任务书 §3.1/§3.5): 额度申报、台账里的 PORT_OPEN 事件、以及每条 dispatch 都只走 FakePort。
  - **新增** `NO_REAL_PORT_IN_ANY_LEDGER` —— 不变项与「本轮零设备动作」的机器核对(任务书 §3.1/§3.5): 额度申报、台账里的 PORT_OPEN 事件、以及每条 dispatch 都只走 FakePort。
  - **新增** `PLAN_BUSINESS_MIRROR_SAME_VALUE` —— 整包离线门里的计划侧判据(本轮新增家族): 只读 --validate-plan 的输出并按名字取数, 不在这个工具里另写一份算术。
  - **新增** `PLAN_BUSINESS_SEGMENTS_NO_DOUBLE_COUNT` —— 整包离线门里的计划侧判据(本轮新增家族): 只读 --validate-plan 的输出并按名字取数, 不在这个工具里另写一份算术。
  - **新增** `PLAN_POST_SPIN_CONTENT_FITS_CAP` —— 整包离线门里的计划侧判据(本轮新增家族): 只读 --validate-plan 的输出并按名字取数, 不在这个工具里另写一份算术。
  - **新增** `PLAN_RESERVE_ARITHMETIC` —— 整包离线门里的计划侧判据(本轮新增家族): 只读 --validate-plan 的输出并按名字取数, 不在这个工具里另写一份算术。
  - **新增** `PLAN_WORST_CASE_TAIL_FITS_SESSION` —— 整包离线门里的计划侧判据(本轮新增家族): 只读 --validate-plan 的输出并按名字取数, 不在这个工具里另写一份算术。
  - **新增** `PRE_EXECUTOR_BYTES_FROZEN` —— 修前派生件(245730 B / 4c962170…)一字节未动 —— 返修的输入身份。
  - **新增** `PRE_RULING_BYTECMP_CLAUSE_FIRES_ON_MUTATION` —— 上一条那个死码的元负控: 尾部多写一个空格的副本必须让「逐字节相等」翻 False, 原件副本为 True —— True/False 配对才算这一项既非恒真也非恒假。
  - **新增** `PRE_RULING_REFERENCE_PLAN_STILL_RED_UNCHANGED` —— 裁定不是把旧字节改绿: 拿 A 包里的裁定前基准件(66ca8a15…)原字节复制到仓外重跑, 必须仍是 zero@None + 两条红 + 旧口径 91/23。红了才证明「绿是语义落地带来的, 不是回头改基准件或放宽阈值带来的」。上一版这条里「逐字节相等」写成两个 sha256 对象相比(没有 hexdigest), 属恒假死码, 本轮一并修掉。
  - **新增** `PRODUCT_SOURCE_UNCHANGED` —— 产品树 applications/ 与 HEAD 一致(本轮「产品源码冻结」), 任何判据红都不许靠改产品消红。
  - **新增** `REPLAY|code=CLEANUP_ERRORS disarm=CONFIRMED_DISABLED terminal=KNOWN 收口TX=['motor_status', 'safety_irq_status', 'safety_irq_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_stop', 'motor_status', 'motor_disarm', 'motor_status', 'motor_stop', 'motor_disarm', 'motor_status', 'safety_irq_disable', 'safety_irq_status'] errors=IRQ_TAIL_DISABLE_NO_DETACH_LINE IRQ_TAIL_NOT_CLOSED` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|code=CLEANUP_RESERVE_CANNOT_COVER_ALL_SENDS disarm=NOT_ARMED terminal=KNOWN 收口TX=[] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|code=EXPECTATION_NOT_MET disarm=CONFIRMED_DISABLED terminal=KNOWN 收口TX=['motor_status', 'safety_irq_status', 'safety_irq_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_stop', 'motor_disarm', 'motor_status', 'safety_irq_disable', 'safety_irq_status'] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|code=EXPECTATION_NOT_MET disarm=CONFIRMED_DISABLED terminal=KNOWN 收口TX=['motor_status', 'safety_irq_status', 'safety_irq_status', 'motor_status', 'motor_status', 'motor_status', 'motor_stop', 'motor_disarm', 'motor_status', 'safety_irq_disable', 'safety_irq_status'] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|code=STEP_DEADLINE_BEFORE_WRITE disarm=NOT_ARMED terminal=KNOWN 收口TX=[] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|code=STEP_TOTAL_SECONDS_INVALID disarm=CONFIRMED_DISABLED terminal=KNOWN 收口TX=['motor_status', 'safety_irq_status', 'safety_irq_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_stop', 'motor_stop', 'motor_disarm', 'motor_status', 'safety_irq_disable', 'safety_irq_status'] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|出现在 validate-plan 输出里` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `ROWS_BRANCH_MISSING_KNOWN_GAP` —— 登记一条**观察到的既有缺口**: 行表缺整个 healthy 分支时build_session_fixture 抛裸 KeyError(开端口之前, 零发送)。不是本轮引入, 任务书没列这项, 交给 A 定。
  - **新增** `RUN_MOTION_TIMER_ELAPSED_WITHIN_CAP` —— 释放时刻本身也要被核: MOTION_TIMER_ENDED 里的 elapsed_from_start_s 必须 <= 12 s(cap), 且事件明写 arm_deadline_kept=True —— 解除运动计时不等于解除通电计时(与 K5/K6 同一合同, 只是换成整包运行现场读)。
  - **新增** `RUN_ZERO_SNAPSHOT_RELEASES_MOTION_TIMER` —— 整包正常场的台账侧证据: 事件顺序是先 MOTOR_STOP_CONFIRMED(ends_motion_timer=False) 再由 as=stopped 那份可信零速快照发 MOTION_TIMER_ENDED, 且整场仍成功收口(disarm CONFIRMED)。证释放点在实际 dispatch 路径上被证据门控制, 不是只存在于静态表。
  - **新增** `STUB_NORMAL_DISPATCH_SUCCEEDS` —— 整包离线门的正常流: 用 real plan/rows 在 stub 后端把 §5 全序跑一遍, 判成功/收口五条/预留与夹短回执字段(现场轮走的就是这条 dispatch 路径)。
  - **新增** `STUB_NORMAL_RESERVE_AND_CLAMP_FIELDS` —— 整包离线门的正常流: 用 real plan/rows 在 stub 后端把 §5 全序跑一遍, 判成功/收口五条/预留与夹短回执字段(现场轮走的就是这条 dispatch 路径)。
  - **新增** `STUB_NORMAL_TAIL_SENT` —— 整包离线门的正常流: 用 real plan/rows 在 stub 后端把 §5 全序跑一遍, 判成功/收口五条/预留与夹短回执字段(现场轮走的就是这条 dispatch 路径)。
  - **新增** `VALIDATE_PLAN_FAIL_SET_EXACTLY_AS_REGISTERED` —— 整包离线门: validate-plan 的红项集合必须**恰好**等于登记的那两条(三段预算留下的两条待 A 定口径的静态冲突)。多一条少一条都判红 —— 既不许把新红藏进「工具自己没跑成」, 也不许顺手放宽判据凑绿。
  - **新增** `VALIDATE_PLAN_RAN` —— validate-plan 跑通并有汇总行(计数与失败数都取它自己的输出原文)。

## 表里用「改名/新增」表达不了的事实
- 矩阵里 H5 那条是**同一判据改名**(理由见上), 不是删覆盖; 其余 65 条基线判据名一条不少 —— 本工具按名字对齐, 少一条就会出现在「移除」里。
- stub 整包 27 -> 29: 基线 27 条(含 R7F8E 交付轮补的两条掩码判据, e97aaa4)一条没动, 本轮加 F4 的运行侧行形状核对与作业状态推导两条。
- 无回归 8 -> 9: 新增「点名的本轮新门确实出现在新增行里」, 防白名单空转。
- 三版本重放(21 件)与最终离线门(39 条)整族是本轮新增, 基线里没有对应 proof, 所以它们的每条判据名都按前缀给理由。

PROBLEMS=0
