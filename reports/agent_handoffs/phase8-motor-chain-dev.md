# 电机链开发轮交接（phase8-motor-chain-dev）

任务书：`NS800_Motor_Chain_Development_Task_20261028.md`（21586 B / `7c4c3c15…`）
分支：`debug/tmc-uart-rootcause-20261028`（基线 `9761e23`，未合并 main/phase7/phase8）
代码提交：`916c34ba4032bc7f41e2944c13b449271030191b`
日期：2026-10-28（Asia/Shanghai）

**一句话结论**：已把上一轮 UART 修复收口为“源码=实际镜像”的可继续开发基线，完成启动 TMC 身份
VERSION 校验、MSH 严格验参、关键状态短行化；三目标 Rebuild All 0 Error/0 Warning；默认安全镜像
已在板验证（90/90 次三读、3 次重启、无 NUL/裁尾）；电机候选已构建但**未下载**，M2/M3 包已给出。
**未完成项**：§5 要求的“新 CLI/身份/短行专用 host 用例”本轮没有新增（见 §5）。

## 1. 源码/镜像不一致的收口（§3）

- 旧 manifest 称输入 `c2d29d9`，但该提交仍含 `TMC_RC_DIAG_NO_SG=1` 与 init 无条件 GCONF 实验写；
  实际镜像由清理后的工作树构建。**旧 manifest 中“code_commit 代表构建输入”的声明失效**，历史文件保留不改。
- 提交 `01025ce` 恢复 sensor 正常 SG 读、删除 init GCONF 实验写；本轮所有镜像由 `916c34b` 构建。
- 旧 300 读原始日志、镜像、失败记录均保留；未重写历史。

## 2. 产品修改（§4）

1. `safety_run_selftest required④`：init 失败 / IOIN 读失败 / VERSION≠0x21 分开打印；
   仅 `init OK + 读 OK + IOIN[31:24]==0x21` 通过并进 READY；保留完整 IOIN 与 rc；
   `0x20/0x00/0xFF` 一律 required 失败，不写寄存器“修身份”。
2. `tmc2209_ioin_is_identity()` 头内联，生产 probe 与自检共用同一判据。
3. MSH：
   - `motor_dir` 只接受完整 `0`/`1`；缺/多参、空、`10`、`1junk` 均拒绝且不写 DIR；
   - `motor_target` 严格十进制非负、按 `MOTOR_MAX_HZ` 防溢出；负号/垃圾/尾随/多参/上界+1 拒绝且目标不变；
   - `motor_arm`/`motor_start` 改为 argc==1 包装，多余参数拒绝且不触发 HIGH/预约/RUN；
   - `stop`/`disarm` 未改，降险动作不受参数门影响。
4. 短行化：`motor_status`、`step_status`、arm gate 详情、`tmc_config_apply/show` 拆成 ≤110 B 的
   ASCII 键值行；删除未确认的“电机额定 1500 mA RMS”断言，理论 Rsense/VREF 保持“未实测”。

## 3. 三目标构建（§6）

| Target | 关键 Define | Program Size | BIN sha256（前 16） |
|---|---|---|---|
| rt-thread | 四宏无目标级覆盖 | Code=180034 RO=77710 RW=2188 ZI=83784 | `ef02100c694f9bba` |
| rt-thread-measure-a0 | 四宏 0；A0=1 | Code=187378 RO=80670 RW=2188 ZI=91504 | `5ac237109ecda842` |
| rt-thread-motor-m1-candidate | MOTOR_HW=1, TMC_CFG_HW=1；STEP/LIMIT=0 | Code=180266 RO=77586 RW=2188 ZI=83784 | `397aa844afdeb2f2` |

UV4 5.43.1.0 / ARMCLANG V6.24，三目标均 0 Error / 0 Warning。镜像与 manifest：
`reports/firmware/phase8-motor-chain-dev/`。

## 4. 默认安全镜像真机验证（§6）

- 烧录：pyOCD 0.45.1 + 厂商 pack，sector erase，259072 B；`0x08000000` 起 258324 B 回读与 BIN 逐字节相等。
- 启动：`SELF_TEST -> READY`；`[SS] [REQ OK] TMC2209 IOIN=0x21000041 VERSION=0x21`。
- 读：3 次受控复位 × 10 组 × 3 读 = **90/90 SUCCESS**，全部 `crc=OK`，0 个 -117/-23/身份错误。
- 后台：sensor 100 Hz，`valid_sg=1`；motor 2048/高水位 704 B、sensor 4096/900 B，canary `0x23`；
  全程无 `stack overflow` / `close to end of stack` 文本。
- 状态行：`motor_status` 最大 91 B、`step_status` 67 B、`safety_state` 43 B，**NUL=0、无 >110 B 行**。
- 未执行：arm/start/dir/配置 apply、fault_reset、手工 selftest、TMC 写。

## 5. Host 验证状态（§5）—— 有未完成项

- `S1` target 变体：50/50 PASS，rc=0。
- `S1` host_ovr：65/65 CASE PASS、rc=0；runner 台账仍报 `未登记=1`。逐项复核 65 个 ID 全部在
  登记表内，判为**台账解析假阳性**（基础设施待修），未通过修改判据来“变绿”。
- **未新增**：针对 CLI 验参、启动身份（0x20/读失败/init 失败）、短行宽度的专用 host 用例。
  这使 §5 的“必要主机验证”未完全满足；不得声称已完成。下一步应先补这三组再进入 M2 实机。

## 6. 电机候选与下一次一次批准（§7）

候选已构建、未下载：AXF `0cfa048f…` / BIN `397aa844…`（258428 B），Target 宏见 §3。
M2/M3 字面步骤、判据、失败退出与一次批准范围见
`reports/reviews/phase8-motor-chain-dev/motor_package_m2_m3.md`。
状态：**MOTOR_CANDIDATE_BUILT / MOTOR_PARAMETERS_UNRESOLVED**（相电流定义、同实物 Rsense/VREF 仍缺）。

## 7. 安全边界与板上状态

电机未接、SW1 阻断；四宏默认 RT_FALSE，目标级例外只在第三 Target；未 arm/STEP/DIR/HIGH、
未清故障、未写 TMC 配置。板上最终态：**运行中，state=READY，motion gate-locked**；
TMC `drive_cfg=UNCONFIGURED`，`safe_for_enable=false`；A0 时长 NOT_ACHIEVED 且暂缓。
main/phase7/tag 未动；未改全局 Git 配置。

## 8. M2 配置测试结果（2026-10-28，用户「批准M2」）

候选已下载并逐字节回读（258428 B / `397aa844…`），启动 READY、IOIN=0x21000041 VERSION=0x21。
单次 COM5 会话按包内 15 步执行，`tmc_config_apply CONFIRM` 仅一次：

| 项 | 实测 |
|---|---|
| IFCNT | before=6，after=10，`(10-6)&0xFF=4` |
| GCONF | before=0x00000040 → RMW 期望 0x00000041；live/applied 均 **0x41** |
| CHOPCONF | before=0x15010053 → RMW 期望 0x15030053；live/applied 均 **0x15030053** |
| IHOLD_IRUN / TPOWERDOWN | 写 0x00041408 / 0x08（只写；由逐笔 IFCNT 与首尾 +4 证明到达） |
| 配置状态 | `state=CONFIGURED snapshot_valid=1 last_err=0` |
| 运动态 | armed=0、cur/tgt=0、stop api-accepted rc=0 latch=0、step enabled=0 |
| 安全态 | READY fault=0；request/pad LOW、ENN 反馈禁能侧 resolved；IRQ CLOSED / polarity NOT confirmed / protection_ready=NO |
| arm gate | 0x18 → 配置后 **0x08**（仅 protection-not-ready，符合包预期） |

**M2 = PASS（M2_CONFIGURED_AND_VERIFIED）**。`safe_for_enable` 仍 false；不证明电流适合电机；
M3 未执行。原始串口/启动/下载日志以 base64 存于
`reports/reviews/phase8-motor-chain-dev/m2/`，`m2_result.txt` 为逐项结果。
板当前运行候选镜像（禁能、无电机、未 arm）。

## 9. M3 首次电机短转结果（2026-10-28，用户「批准本文件 M3 包…」）

§8 为 M2 结束时状态；本节为 M3 后当前状态。

- **启动/配置**：MCU 未重启（system_status 仍 READY/CONFIGURED），但 TMC IFCNT=0、
  live GCONF=0x00000101 / CHOPCONF=0x15010053，说明接线时 TMC 掉电，M2 的 CONFIGURED
  不可继承。未取得新启动原流；以当前状态查询 + **一次** `tmc_config_apply CONFIRM` 重建：
  IFCNT 0→4；applied/live GCONF=0x00000141、CHOPCONF=0x15030053；snapshot_valid=1。
- **保护**：操作者万用表 **H6-7 对地 0 V**；PA.3 raw=0/safe=0。一次
  `safety_polarity_confirm CONFIRM`、一次 `safety_irq_enable`；IRQ OPEN、polarity CONFIRMED、
  protection_ready=YES（连读两次）、arm_gate_mask=0x0；无风暴/新 fault。
  限制：H6-7→PA.3 的独立连通/无反相仍缺网表或导通证据（H6 pin7 为图纸推断）。
- **短转**：任务书 target→arm 与固件“arm 时 target=0”冲突；首次 DIR0 arm 被拒（未通电），
  随即 disarm；改为 arm→target→start。DIR0 1.5 s：CW、幅度小、非平顺、无异常；
  DIR1 1.5 s：CCW（平滑性未报告）；stop/disarm 均 api-accepted rc=0。
- **用户授权 5 s 变更**：DIR1 5 s：CW、幅度更大、有抖动；DIR0 5 s：CW、幅度更大、
  抖动（操作者判断为自身惯性）。两次均 5000/5001 ms、stop/disarm 通过。
- **终态**：armed=0、cur/tgt=0、drv_request=LOW pad=0、ENN_fb=HIGH、STEP enabled=0、
  READY/fault=0、IFCNT=4、drive_cfg=CONFIGURED；IRQ OPEN/protection_ready=YES；
  SW1 保持允许位（操作者明确不再拨动）。`motor_status` 的 `drv_en_request` 仍显示 1，
  是“最近一次回读确认值”的显示语义，实际 safety request=LOW / last readback=0。
- **结论**：`M3_FIRST_FUNCTIONAL_ROTATION_PARTIAL_DIRECTION_UNRESOLVED`（不登记 VERIFIED）。
  有限 PASS：apply、保护就绪、两方向原始 1.5 s 各一次 arm/start/stop/disarm 与转动观察；
  UNRESOLVED：方向一致性（DIR1 5 s CW 与 1.5 s CCW 矛盾）、平顺性/抖动、实测电流、
  波形/频率/停机时延。`safe_for_enable=false`。5 s 两次为授权变更，超出原包 ≤2 s。
- **证据**：`reports/reviews/phase8-motor-chain-dev/m3/`（`*.bin.b64` + `m3_result.txt` +
  `run_timings.txt`）。未清故障、未升流/升频、未改宏、未重烧、未读 DAP/Flash。
