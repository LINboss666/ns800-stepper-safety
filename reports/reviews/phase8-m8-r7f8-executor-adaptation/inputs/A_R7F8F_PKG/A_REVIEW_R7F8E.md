# A 对 R7F8E 的独立复审

结论：T3/T4 可接受；现有测试声称得到复现，但当前派生执行器存在未覆盖缺口，需要返修。下一轮明确采用**修复＋离线门＋一次实机验收**，不再只交一个纯离线轮。完整范围见同包 `R7F8F_REPAIR_AND_FIELD_TASK.md`。

## 独立取得的结果

从远端 e97aaa4 取 R7F8E 原件并在仓外副本上运行。B 的原脚本分别得到矩阵65/0、stub29/0、无回归8/0；构建器verify重建相等。附三份重跑原文，不将“原有测试绿”扩写为全部合同已覆盖。冻结旧执行器与产品不改，设备动作0。

T3：finally 应将有效 quota 对象传入清理；直接CLI从plan构造额度时原参数可能为空，原修复有实际意义。T4：工具缺项应保留PLAN_TOOL_ARGV诊断，不抛AttributeError，原修复接受。这两项不用回退。

## 独立反例（均为 stub/sim）

| 编号 | 输入 | 当前实测 | 要求 |
| --- | --- | --- | --- |
| P1 | RUNNING却owner=FREE，然后正常DONE | rc0/OK，继续查询 | 首个矛盾响应STOP |
| P2 | 已受理N8，RUNNING却want9 | rc0/OK，后续DONE洗掉前错 | 参数关联至实际STARTED及命令 |
| P3 | 已受理DIR0，RUNNING却DIR1 | rc0/OK | 首错STOP |
| P4 | 同一作业计数6/6倒退至2/2 | rc0/OK | 分源单调性检查 |
| P5 | 同ID DONE的窗口/方向变了，且没有候选输出 | rc0/OK | 参数合同及完整候选证据 |
| P6 | arm通电cap2s，arm回包阻塞3s | rc0/OK，约3.005s才发清理 | arm首条read受新建截止约束 |
| P7 | arm→disarm→第二次arm→等待8s | rc0/OK，energize_actual仍0 | 第二次arm发前拒绝，本轮限定一次 |
| P8 | 运行request HIGH却ENN HIGH/1 | RUNNING判据仍rc0/OK | 正常运行须ENN LOW/0/OK |

附一个真实owner=2的正向控制。当前B正向夹具RUNNING默认owner=1，与产品FREE0/BY_ZERO1/BY_JOB2枚举不符；MOT_CRUISE夹具也把HIGH/1写作正常使能。修复必须纠正这些正向输入，不能保留错误合同追求原封不动全绿。

P6/P7原输出字段gap_reproduced为false，因为原探针将它们登记为timing observation；它不代表截止通过。判断依据为cap、TX时间线和energize_actual，已在此明确判为缺口。原JSON不回改。

P6/P7最初的探针夹具漏配固定stop回包，曾得到CLEANUP_ERRORS，补完整同一清理响应后两项仍rc0/OK，才形成上表结论；初次输出另存，不把测试夹具错误算作被审工具缺陷。

## 修复边界

作业计数不可加“最多N”的假门：产品rel_cap_feed不在单源达到N时停采，较快源可能超过N；DONE应各至少N、单调并与候选统计一致。本次任务书已按源代码校正这一点。

不修改产品来迎合执行器，不重建镜像。独立复审未声称重跑远端384条清单的全部业务含义；本次取到的对象和本地复跑原文分别记录。没有开COM、调用DAP、启动Logic或创建真实设备批准账。

## 下一轮为何直接包含现场

纯stub不能证明板上RUNNING期间查询、真实取消窗口、正常DONE或RAM提交。返修通过后，继续在已证在板的2369镜像上执行唯一一个空载DIR1/300Hz段，覆盖取消N32、完成N8、RAM提交及正常stop/disarm。离线门与现场共用一个明确任务、一份现场批准和一个动作账；不增加软件签收暂停点。

SG/ADC/IMU目标刺激仍需不同的物理试验设计，不因本轮作业流程通过而写成核心保护闭环通过。polarity_measured与core_protection_verified保持false。
