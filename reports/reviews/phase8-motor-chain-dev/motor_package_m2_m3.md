# M2 / M3 电机下一步包（供一次批准）

候选镜像（已构建，未下载、未运行）：
- AXF `0cfa048f…`（1665352 B）/ BIN `397aa844…`（258568 B）
- Target `rt-thread-motor-m1-candidate`：`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=1`、
  `TMC_DRIVE_CONFIG_HW_VALIDATED=1`、STEP 诊断=0、限位=0
- 输入：`916c34ba4032bc7f41e2944c13b449271030191b`

当前状态：**MOTOR_CANDIDATE_BUILT / MOTOR_PARAMETERS_UNRESOLVED**。
相电流定义、同实物 Rsense/VREF 仍未确认；不得把代码中的 1500 mA / 110 mΩ / 1270 mV 当事实。

## M2：电机不接、SW1 阻断下的配置写

1. 烧录候选镜像（BIN 长度 258568 B，回读逐字节比较；不 mass erase/OTP/unlock）。
2. 复位后确认 `state=READY`、`TMC2209 IOIN=0x21000041 VERSION=0x21`、`step_status` 无输出。
3. 执行 `tmc_config_apply`（正式受控 apply，进入写门禁）：
   - 写 GCONF / CHOPCONF / IHOLD_IRUN / TPOWERDOWN 四个寄存器（值见 `motor_target` 相邻的
     `[TMC-CFG] target ...` 输出）；
   - 每次写前/后读 IFCNT，要求 +1；写后回读四寄存器与目标值逐位比较；
   - 任一步失败：保护优先，不得继续写，记录首失败 rc/寄存器，保持不 arm、不出 STEP。
4. 判据（全部满足才算 M2 PASS）：
   - `tmc_config_apply` 返回 OK；四寄存器回读等于目标；IFCNT 增量与写次数一致；
   - `motor_status` 的 `drive_cfg` 变为已配置、`tmc_write_gate=0`、`armed=0`；
   - `step_status enabled=0`、`safety_state=READY`、无新告警；
   - 结束时保持不输出 STEP、不 arm。
5. 失败退出：任一判据不成立即停止，保留原始输出；不重试写、不手工 clear。

## M3：仅在 M2 通过且真实限流前提成立后，首次接电机/允许 SW1/arm/start

字面顺序（每步失败即停在原地，不继续下一步）：
1. 断电接电机、按现场条件允许 SW1；上电后确认 `motor_status` 的 ENN/反馈/arm gate 字段自洽。
2. `motor_target 100`（0=停；100 Hz 仅为待批准候选，不是已验证适用值）。
3. `motor_dir 0`（或 `1`，按现场定义；只接受完整 0/1）。
4. `motor_arm`（无参数）；确认 `armed=1`、`drive_cfg=已配置`、`arm_gate_mask=0`。
5. `motor_start`（无参数）；观察不超过 2 s，记录 `motor_status`/`step_status` 的
   current/target/stop 字段；到点立即 `motor_stop`，随后 `motor_disarm`。
6. 停止判据：`stop result=api-accepted`、`diag_off_confirmed=YES`、`enabled=0`；
   若出现异常运动/发热/真实溢出/HardFault：立即停止，不隐藏告警，回到安全候选修复。

## 需要用户一次批准的动作与范围

- 烧录候选镜像到现有板（1 次）；M2 的四个寄存器配置写（GCONF/CHOPCONF/IHOLD_IRUN/TPOWERDOWN，
  每寄存器写入+回读，IFCNT 前后读）；
- M2 通过后：允许接电机、允许 SW1、`motor_arm`、`motor_start` 100 Hz、最长 2 s、随后 stop/disarm；
- 不包含：OTP/选项字/全片擦除、清故障、selftest 注入、改限流计划、其它电机参数。

## 仍未决（不阻止 M2，但阻止把 M3 当“已验证适用”）

- 电机相电流定义/额定值；同实物 Rsense 与 VREF 实测；ADC 仍理论标定；
- 异常诊断阈值为工程占位；物理 STEP/停机时延未验；BUG-008/RISK-03/03a/04-b 保持开放；
- 恢复下载路径未演练；A0 时长 NOT_ACHIEVED 且暂缓。
