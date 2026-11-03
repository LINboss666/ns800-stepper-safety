# NS800 M8-R7 复审与校正版启动/短打印现场候选

日期：2026-11-03（北京时间）。云端A。

## 结论

**原R7不直接进入现场；A已在审查副本中完成最小JSON校正并复核。使用本包的 `plan_core_r7_reviewed.json` 作为唯一新候选，无需再开一轮Python工具返修。现场仍待操作者一次明确整包批准，本包硬件授权为0。**

读取的远端收口头为 `0df9937abb4831c8c0fd6734a2e45c9fbdf5aca0`，交付提交为 `ea22ba52d675ffc698aedabd5e9ab4db289cdc92`。原R7、R6C执行器和全部固件归档未改；本包是A提供的独立候选文件，尚未写回用户仓库或现场执行。

## 原R7的实际问题

1. **正常启动回包会被误拒。** A取当前Git对象中的 `reports/reviews/phase8-m8-idle-stack-r4/field/field-20261101T151533Z/S_S3/01_sysboot.rx.bin`（980 B），同一份原字节逐次喂给实际EXEC：

   - 原R7 sysboot → `BUSINESS_FAILED`，原因是状态查询披露 `[Motor] arm gate fail mask=0x8 (arm REFUSED)` 被当成动作失败。
   - 只恢复旧的整行状态白名单 → `FIELD_NOT_EXACTLY_ONE`，因为 `polarity=\w+` 不接受真实的 `not-confirmed`。
   - 复用R6C已验证的查询判据 → `OK`。

   R7 sysafter同样丢了状态白名单。R7的17项干跑使用的是M4结束时 `polarity=CONFIRMED / irq_gate=OPEN / arm gate=0` 的回包，因而没有检出这个新启动场景回归。它们不是新固件现场回包。

2. **新镜像构建来源混入旧dab登记。** 实际5ae的R6A manifest登记构建时HEAD为44e493c，且有未提交输入；相关五项输入在后来的9e2eb9c中都匹配。44e493c的motor.c不匹配该manifest，58904e1属于旧idle/dab镜像。原R7的相关描述与顶层提交字段没有一起正确重绑。

3. **“缺加载手段必然阻断普通disarm”是误读。** EXEC按阶段的 `prerequisite_gates` 检查具体前置；并不把load_means自动挂到每个motor_arm/motor_start。普通RUN→disarm验证需要具体短运动范围及已确认装配，不需要人为增加加载夹具。只做arm→disarm也不能证明此前RUN→READY缺口已修。本候选继续只测启动/打印，这是范围选择，load_means仍NOT_PROVIDED。

## A已完成的校正与验证

- 两个system_status步骤恢复已验证的整行状态披露白名单、允许连字符取值，并硬性要求bootstrap完成、READY/fault0、Motor静止未武装、实时请求LOW/pad0、ENN反馈HIGH/1/resolved/OK。动作命令本身被拒仍停，不全局豁免REFUSED。
- 新5ae输入登记以R6A build_manifest为准，44e493c明确仅为当时有未提交改动的构建HEAD；9e2eb9c为相关提交输入参考，五项source_inputs逐项相符。旧dab输入只放历史块，不冒充新镜像完整构建树。
- 校正加载手段范围说明；保留无arm/start、无学习/保存/清日志的现有阶段链、全部额度及执行器字节。
- 澄清启动原流归档与命令响应字节门的区别：本包不能声称已经自动证明全部启动行<=120B或全仓输出都修好了。

A实跑结果：

| 验证 | 结果 |
|---|---|
| 原R7既有readiness入口，读取最终校正版 | 17/17，退出0；仅替换该入口的PLAN_PATH指向本候选，未改检查函数 |
| 两个system_status步骤：真实启动正控及fault/armed/实际动作拒绝/许可HIGH/ENN错误反例 | **12/12符合要求**；正常回包OK，五类错误均STOP |
| BIN/AXF/map、download/readback与EXEC登记 | 六项实物身份现算相符 |
| 通用validate-plan | 47项，其中46项通过；唯一运动预算项判定见下文 |
| 设备动作/编译/用户仓库写入 | 0/0/0 |

### 无起转预算项的明确处置

现有通用检查 `PLAN_MOTION_CAP_BUDGET` 要求caps非空，所以本候选仍会输出：

```text
PLAN_MOTION_CAP_BUDGET|all|FAIL|caps=[] sum=0 total=0 starts=0
```

**A仅对此固定候选将该项判为N/A。**依据是所有实际步骤均无motor_arm/motor_start，运动、学习、保存及清理相关额度全0，其余46项通过，且既有无运动就绪检查通过。不填虚假运动预算，不修改EXEC，不把通用校验退出1伪写成0。

该限定不适用于任何增加arm/start、运动预算或改变步骤的计划；修改计划后本次审查与批准身份均不能沿用。

## 固定身份

| 对象 | bytes | SHA-256 |
|---|---:|---|
| **plan_core_r7_reviewed.json** | 108021 | `fea903b4f9530cab6d6bec16d2a4beb1b85901e441e18c9872831f121c645c16` |
| EXEC（R6C原件） | 219491 | `33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca` |
| 下载AXF | 1773648 | `581f1155166374cf96cda1281320dbcfd31968f1bf5a898a6dd0e2a804a52df1` |
| 回读比对BIN | 292164 | `5ae4110d14fdbb40f49cc6f0deca474cf6d0b33b3ee9305e83681dbdd4a53825` |

配套rows.json沿用远端R7原件（49352 B / `b66e33fced562863…`），只作为离线装配参考，不能把其行表当现场证据。所有固件路径仍指向仓内R6A归档；不重复编译、不下载旧dab件。

## 待一次整包批准的现场范围

| 阶段 | 具体范围 |
|---|---|
| S0 | 实际计划/执行器/新镜像绑定，身份不符或计划漂移即停 |
| S1 | 下载一次上述5ae候选对应AXF |
| S2 | 一次定长292164 B回读，与归档BIN逐字节比较 |
| S3 | COM5开口后先复位一次，被动启动观察75s，再做既有只读查询 |
| S4 | 一次tmc_config_apply CONFIRM，取三条目标短行，再只读复核CONFIGURED、READY及禁能状态 |

非零额度保持原提案：**port_open≤2、download≤1、readback≤1（292164 B）、explicit_reset≤1、tmc_config_apply≤1**。其余为0。一次批准可以覆盖这五项，不需要五次独立批准。

不包含arm/start、学习、归零、保存/提交profile、清日志、升频升流或现场自动重试；不声称验证正常RUN→disarm状态收口，也不声称SG/ADC/IMU真实保护已通过。

既有装配、供电、接线确认沿用，不重新索要同一份证明。实际执行前，由现场操作者/本地Agent核对机构静止、现场有人可观察并能在异常时中断、COM5未被其他程序占用。若接线/装配有实际改动或异常，则先停下回报。

当前安全态已由两个system_status步骤的具体字段约束。若出现意外运动或禁能无法确认，应停止后续动作，由现场操作者使用既有硬件阻断手段处理；关闭串口不等于禁能。

## 给本地Qoder的直接操作说明

1. 本包尚未授权设备操作。先接收此候选，**不新增派生器/校验器/回读框架，不再跑120项历史链或127项全量自测，不编译固件**。
2. 将ZIP里的 `plan_core_r7_reviewed.json` 原字节复制到现有 `reports/reviews/phase8-m8-r7-field-package-5ae/field/`，与原rows.json同目录；保留原plan_core_r7.json。不要重跑旧派生器覆盖校正版。确认计划、EXEC、AXF、BIN与上表相符。
3. 只有操作者明确批准此固定整包后，才按既有EXEC的整包dispatch路径执行。新批准通过独立批准身份/持久账绑定本计划SHA、EXEC SHA和镜像SHA；不复活旧4cb批准，不改JSON的字节来“登记已批准”，不提前开立可用现场批准。复用本机既有私有UID文件，不公开UID、不枚举新设备。
4. 任何实际失败不自行增加次数、改判据、修改计划或重跑整包。保留首错、原始字节、工具退出码及实际额度。整包结束后集中归档一次现场回执和原件并常规推送；由A按真实字节复审。
5. 若只是本地接收与核对计划，不需要先提交一轮登记再等A复审；此文件就是对固定候选的审查记录。未来若任何实际输入身份改变，需先说明变化，不能配着本次批准继续。

本候选的 `hardware_actions_authorized_by_this_package=0` 表示准备时未授权。它不会因本次A软件复核自动变成硬件批准；得到操作者授权后，批准记录在外部审批/持久账中，固定JSON保持原字节。

```text
original_R7_plan = DO_NOT_EXECUTE_AS_SUBMITTED
A_reviewed_candidate = READY_FOR_ONE_OPERATOR_PACKAGE_APPROVAL
EXEC = R6C_UNCHANGED
product_field_fix = NOT_VERIFIED
safe_for_enable = false
new_hardware_authorization = 0
A_device_actions = 0
```
