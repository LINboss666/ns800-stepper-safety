# R7F8F 夹具/期望逐项变更说明(基线 42d7f7d -> 工作树)

机器核对入口: `tools/r7f8f_fixture_change_table.py`(就是本文件的产出者)。基线与现件的判据名逐条对齐; 新增/移除必须带人写理由; 同名判据结论变化一律判红。

## proof/r7f8e_case_matrix.txt
- 家族: 反例矩阵(计划驱动的执行器行为反例 + 元负控)
- 基线判据名 65 个 / 现在 135 个 / 同名保留 65 / 新增 70 / 移除 0 / 结论变化 0
- 现在的汇总行: `R7F8E_MATRIX_SUMMARY|判据=135|FAIL=0|none`
  - **新增** `FB1_真字节经生产读路径逐字节留证且披露行在流里` —— 输入换成 2026-11-05 F11 板子真发的 983 B(proof/r7f8f_field_f11_sys_pre.rx.bin), 经**生产读路径** read_until_prompt 解析, 落盘字节必须与原捕获逐字节相等, 且披露行确实出现在流里。为什么不能再用夹具行: 上一版把 mask=0x18 记在 [TMC] 写门行上, 夹具因此永远测不到现场首错。
  - **新增** `FB2_正例_交付的 sys_pre 申报下真字节判 OK 且豁免行入台账` —— 正例: 交付计划里 sys_pre 的申报(整行锚定豁免 + expect_contains 正面要求 + 两行硬字段)对真字节判 verdict=OK, 并且台账里 BUSINESS_OUTCOME.status_lines_allowed 逐字带上被豁免的那一行 —— 豁免必须留痕, 静默丢弃等于把判据藏起来。
  - **新增** `FB3_反例_同一段真字节喂给动作步仍具名业务失败` —— 反例(作用域): 同一份 rows 喂给 arm 步(motor_arm, 未申报豁免)必须仍判 BUSINESS_FAILED 且命中行就是那条披露行。证豁免是**按步**申报的, 不会把一局里所有 REFUSED 线洗白 —— 这正是操作者选的修法①里「动作步一律不许豁免」那一半。
  - **新增** `FB4_元负控_摘掉豁免同一份真字节必须翻红` —— 元负控: 把 sys_pre 的 status_lines_allow 摘掉, 同一份真字节必须翻回 BUSINESS_FAILED。红了才证明这条申报是承重的判据, 不是装饰; 也证明 F11 那一次停止的根因确实在包侧缺申报。
  - **新增** `FB5_反例_豁免未被正面要求时运行期具名停止(fail-closed)` —— 反例(新门本身): 保留豁免但删掉 expect_contains 里那条披露串, 运行期必须具名停 STATUS_ALLOW_NOT_POSITIVELY_REQUIRED 并留台账事件。R7F8E 起注释就写着「必须同时被本步 expect 正面要求」但代码从未核过, 这条就是兑现它 —— 否则写一条豁免等于「长得像拒绝的都免检」。
  - **新增** `FB6_静态门_交付计划 PASS 且豁免只在预期非 0 的那几条查询步` —— 同一判据改名, 不是删覆盖: F19 版写死「豁免只在两条 system_status 查询步」, 而 F21 按 A 的第 1 点把 motor_status 的 8 处也纳入逐步契约后, 申报豁免的是 6 条(预期 0x0 的 4 条查询步一条豁免都没有)。旧名里的「两条」在新口径下本身就是错的, 保留旧名会让判据名与实际断言不一致。
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
  - **新增** `MC0_包作者工具可导入(归档原件身份在它手里核过)` —— 本轮新增族(F21 第 1、2 点): 判据先证「行表里的真实字节是作者工具从盘上归档件读进来的」—— r7f8f_make_real_package 在 import 阶段就跑 _observed_sys_ready/_observed_mot_rows 的字节+散列核对, 缺件或身份不符直接 SystemExit。矩阵不复制那份检查, 只证它过不去时自己也红。
  - **新增** `MC10_跨计划继承_六份冻结旧计划的每条披露豁免都在新计划里有落点` —— A 指出「旧计划判据未删, 还不足以证明新计划完整继承了其披露合同」。这条把六份冻结旧计划(R7F2b/R7F3/R7F4/R7F5/R7F6/R7F7)的每条 (cmd, 字面 mask) 豁免都取出来, 要求新计划要么在同 cmd 上覆盖那个值, 要么**点名不继承并给可按步序复算的理由**(window_check: 那两步之间不许有同类查询步)。本包只有 motor_status/0x8 走不继承那条, 理由是可复算的步序事实。
  - **新增** `MC11_F20原始字节入档且与历史归档逐字节同一身份(口径=F20本会话首次取得)` —— F20 的两段原始字节(983 B/1128 B)从仓外运行目录归档进 proof/, 并与历史归档逐字节比: 0x18 那一份身份与 R7F7 等 5 次会话相同(口径按 A 的更正写「F20 本会话首次取得」, 不写成「第一次拿到」); 983 B 那一份与 F11 相同 —— 而这只证**输出重复**, 接线未变化属于操作者报告, 判据里明写不升格成实测。
  - **新增** `MC1_全量覆盖_计划里每条状态查询都钉了 mask 且与契约表逐条相等` —— A 的原话是「按最终计划全量核查, 别只手工数 7 处」: 判据从**计划字节**自己数出 10 条查询步(8 处 motor_status 含 stopped/mot_disabled + 2 处 system_status), 并把每步的 mask 集合与豁免集合原样打在详情里供逐条对。契约表少一步或多一步都不算绿。
  - **新增** `MC2_预期为0的步骤要求0且一条豁免都没有` —— A 的「预期为 0 的步骤仍要求 0, 不能给所有查询统一放行任意 REFUSED」的直接判据: mot_after_irq / mot_dir / mot_armed / mot_disabled 四步的预期都是 0x0, 它们的 status_lines_allow 必须是空列表 —— 豁免缺席才是「要求 0」的实现方式, 而不是靠一句说明。
  - **新增** `MC3_归档真实字节四份都在盘且身份逐字节相符` —— F21 第 2 点「纳入历史真实字节」: 0x18/0x0/0x2/0x8 四种 mask 形状各取一份**仓内归档原件**(1128 B/f0550d313164、648 B/10807f63f02a、1026 B/9005a306999a、1080 B/fa69aa5cf5a1), 判字节数与sha 前缀, 并在原件里逐字找到那条披露行。取不到就整族红, 不允许改用我写的行继续跑。
  - **新增** `MC4_真字节正例_mot_pre 收到 0x18 归档行判 OK 且豁免行原文入台账` —— F20 停下来的那一步(mot_pre)现在拿**同一段真字节**判绿, 且台账 status_lines_allowed 带上原文。这一条就是「F19 的修复方式在 motor_status 上也成立」的证据, 不是只在 system_status 上证过。
  - **新增** `MC5_真字节反例_预期 0x0 的步收到 0x18 真行必须具名 STOP` —— 同一段 0x18 真字节喂给预期 0x0 的 mot_after_irq 必须判 BUSINESS_FAILED 并命中那一行。正例(MC4)与反例(MC5)用**同一份字节**分别判, 才证得到差别在「本步申报的预期值」上, 而不是在字节内容上。
  - **新增** `MC6_边界步_stopped 的 0x0 与 0x2 各判 OK, 0x18 与 0x8-ALL-PASS 都停` —— 38 步里唯一的真边界(motor.c:1989 mot_commit_zero_ready 提交 RUN->READY 要 armed!=0 且 cur==tgt==0, 与本查询先后不定)。判据是**四个方向**: 0x2 真行判 OK 且留痕、0x0 真行判 OK、0x18 真行停、形状合法但自相矛盾的「0x8 ALL-PASS」按 FIELD_VALUE_NOT_IN_SET 停 —— 枚举而不是通配。
  - **新增** `MC7_元负控_逐步摘掉 mask 判据静态门都点名翻红(10/10)` —— 对 10 条查询步**逐条**摘掉那一条 mask 逐值判据, 静态门必须在**那一步**上翻红并点名它。只数总数或只看是否变红不够: 红在别的步上等于这道门没在判这一步。
  - **新增** `MC8_反向_给预期 0x0 的步加一条通配豁免必须判红` —— 反向: 给预期 0x0 的步加一条 0x[0-9A-Fa-f]+ 通配豁免(= A 明令禁止的「统一放行任意 REFUSED」), 门必须判红并把「应有的豁免=[]」与实有模式一起打出来。这条也是 MC2 那道静态门的元负控。
  - **新增** `MC9_历史包无契约标记只登记NA且不加新红` —— 冻结的旧计划(含 R7F7 定稿件)没申报这份契约, 门只登记 NA、不判红 —— 与 observation_bounds 同一处置: 历史件字节不回改, 也不给它们新增失败(否则无回归族的「既有失败集合未变」会被我的新门自己打破)。
  - **新增** `TG10_CLI 真实分支被令牌门拦在 dispatch 之前(不导 pyserial、零台账零发送零开口)` —— CLI 级证据: transport=real 时令牌门在 dispatch_plan **之前**拒回(rc=4, 名 TOKEN_CHAIN_REFUSED), 输出目录里没有任何台账文件 => 未导入 pyserial、未开口、未发送。探针给的是不存在的口名 COM_NONEXISTENT_M8R7F8F: 万一将来这道门被改坏, 也只会撞在一个不存在的口上, 碰不到现场口 COM5。
  - **新增** `TG1_Python 内按严格 UTF-8 取原字节(不经控制台/shell 变量)` —— A 要求的修法本体: 「下一次直接在 Python 内读取严格 UTF-8 原字节」。token_bytes_from_file 只读文件、只裁尾随 CR/LF、非 UTF-8 即拒 —— 判据把文件 sha 与取到 sha 一起打, 并申报尾随换行是否存在。
  - **新增** `TG2_开账即绑令牌摘要, 且账本里不落原文` —— approval_init 把令牌 sha256/字节数/来源写进账本的 approval_token 格(四件实体身份之外的一格)。同时判「原文不出现在账本文件里」: 原话是操作者的材料, 账本进证据链, 只登摘要不登原文。
  - **新增** `TG3_正路_原话文件/传入令牌/账本绑定三份摘要一致才放行` —— 开口前核对**原话文件、实际传入令牌、账本绑定令牌**三份摘要, 判它们确实同值(输出三个 sha 前缀)。只比命令行两处的等号(A 之前指出的旧口径)不算: 那种比法对 F20 那种漂移是瞎的。
  - **新增** `TG4_反例_传入令牌与账本绑定不符必须拒绝开口` —— 反例(漂移形状): 把原话里一个全角逗号换成半角(与 F20 同类的字节漂移) => TOKEN_DIGEST_MISMATCH。这一条证明三道门里「传入 vs 绑定」这一对真的在咬。
  - **新增** `TG5_元负控_账本没绑令牌摘要时必须拒绝开口` —— 元负控: 把账本里的 approval_token 格删掉后必须 APPROVAL_TOKEN_NOT_BOUND。摘要缺席是**开不了口**, 不是「当作一致」放行 —— 否则这道门只要不调用 init 就自动失效。
  - **新增** `TG6_非 UTF-8 原话文件具名拒绝` —— 反例: 原话文件不是合法 UTF-8(GBK 字节)时具名拒绝, 不猜内容。F20 的漂移正是「换编码」这一类。
  - **新增** `TG7_原话文件缺席时不猜内容` —— 反例: 原话文件不存在时具名拒绝(TOKEN_VERBATIM_FILE_ABSENT), 不允许用「上一轮那句话」代替。
  - **新增** `TG8_F20 那次开口按新门复算=令牌判据不成立` —— 把 F20 那本**已花掉的账**原样归档进 proof/ 后用新门复算: 账里没有 approval_token => 当时「开口用的是不是那句原话」无从核对 => 这一判单独记 FAIL 形状(码 APPROVAL_TOKEN_NOT_BOUND)。原账、原话、两份不同散列都保留不补改, 与新账并存供对账。
  - **新增** `TG9_F20 的四件实体绑定单独判 PASS(与令牌那一判互不掩盖)` —— 实体绑定与令牌使用**分开判**的那一半: 批准句点名的 plan/rows/executor 三件在 Git 历史里能找到内容散列逐字节相符的 blob(不靠 HEAD, 因为本轮会改这些文件), 镜像按盘上字节复算 => 实体绑定 PASS。这条绿不掩盖 TG8 的红, 反之亦然。
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
- 基线判据名 无该件(本轮新增) 个 / 现在 40 个 / 同名保留 0 / 新增 40 / 移除 0 / 结论变化 0
- 现在的汇总行: `R7F8F_OFFLINE_GATE_SUMMARY|判据=49|FAIL=0|none`
  - **新增** `ARM_GATE_MASK_GATE_IS_GREEN_AND_COVERS_EVERY_QUERY_STEP` —— 整包离线门里对 F21 新门的**交叉核对**(不只是「在不在」): validate-plan 输出里这一条必须只有一行、判绿, 且它自己数出来的查询步集合要与本工具从计划 JSON 独立数出的 10 条**同名同序**, 汇总话术里的条数也要相等。于是「门被删掉」「退化成 NA」「只数了一半」三种情况都会在这里红, 不依赖矩阵 MC 族那一套。
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
  - **新增** `REPLAY|code=EXPECTATION_NOT_MET disarm=CONFIRMED_DISABLED terminal=KNOWN 收口TX=['motor_status', 'safety_irq_status', 'safety_irq_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_stop', 'motor_status', 'motor_stop', 'motor_disarm', 'motor_status', 'safety_irq_disable', 'safety_irq_status'] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
  - **新增** `REPLAY|code=EXPECTATION_NOT_MET disarm=CONFIRMED_DISABLED terminal=KNOWN 收口TX=['motor_status', 'safety_irq_status', 'safety_irq_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_status', 'motor_stop', 'motor_disarm', 'motor_status', 'safety_irq_disable', 'safety_irq_status'] errors=` —— 三版本重放的逐条件行(本轮新增家族): 同一份输入喂给 修后 / F5b 修前 / F5c 修前 三个字节件, 每件按**该件应有的行为**申报期望, 用来证「修前误放行」。
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
