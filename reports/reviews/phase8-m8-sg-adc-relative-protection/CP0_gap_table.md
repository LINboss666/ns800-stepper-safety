# M8 CP0 — 基线、范围与功能缺口表

任务 `phase8-m8-sg-adc-relative-protection`。基线 `f1aa2e832d536593641cea44ea890fb8ce9558b9`，
工作分支 `feature/m8-sg-adc-relative-protection`（自该提交新建，原 debug 分支不动）。
本轮全部离线：设备动作 0、串口对象 0、DAP 0、电机动作 0；Keil 只构建不下载。

## 1. 基线与冻结引用（只读核对，见 evidence/cp0_baseline_readback.txt）

| 项 | 值 | 核对方式 |
|---|---|---|
| 本地基线 = 远端 debug 分支头 | `f1aa2e8` | `git rev-parse` / `ls-remote` |
| main | `1b3757ee…` | `ls-remote` |
| phase7/full-embedded-software | `6d3fbc55…` | `ls-remote` |
| phase8/tmc-drive-config | `1696c59f…` | `ls-remote` |
| 标签对象 / 解引用 | `097935fe…` / `e40a9b95…` | `ls-remote` |
| 板上 M7-R1 motor BIN / AXF | 266244 / `e614a075…f7da`；1688696 / `2205c7e7…d584` | 已在 P0 轮逐字节回读 |
| 固件源码输入 | `5b564eb9…` | 交接 roles.firmware_code_commit |
| 冻结 P0 采集器 / 验证器 / 计算器 | `c98bb712`/23430、`08559720`/26690、`5c7a2340`/54267 | 本轮不修改、不调用 |

同时按任务书 §1 顺带登记：P0 独立复审接受对象 `f1aa2e8`，结论仅 `VOLTAGE_OBSERVATION_ONLY`，
无新增必须返修项；不单开 GO 轮（写入本任务交接）。

## 2. 已有 / 需修改 / 现场待证

符号级定位，行号按 `f1aa2e8` 复核过（不是沿用文档行号）。

| # | 能力 | 现状（已有） | 本轮动作（需修改） | 现场待证 |
|---|---|---|---|---|
| 1 | 模式集合 | `diagnosis.h:48-52` 只有 `MONITOR_ONLY`/`ACTIVE_PROTECTION`；`diagnosis.c:381-400` ACTIVE 门要求 `current_adc_cal_source()==MEASURED` | 新增第三态 `DIAG_MODE_RELATIVE_PROTECTION`；**原 ACTIVE 的 MEASURED 门逐字保留**，THEORETICAL 仍拒绝（用例 M8-11） | 现场切 RELATIVE 需静止+显式命令 |
| 2 | SG 单独严重判定 | `diagnosis.c:274-279` STALL_CONFIRMED 必须 `sg_below_stall_cnt>=persist_stall && cur_above_warn_cnt>=persist_warn`；severe 集合仅 `:310` 的 CONFIRMED/OVERLOAD | 新增 `REL_SG_SEVERE` 独立降险路径，原因不得写成 STALL_CONFIRMED | 真实堵转时 SG 是否可分，只有现场加载能证明 |
| 3 | SG 样本身份 | `sensor_service.c:116-123`：SG 按 `*seq % 10` 分频，非采样帧 `fresh_sg=0` 且值/valid 沿用；`sensor_service.h:36-38` 帧内只有 `sg_result/valid_sg/fresh_sg`，**没有 SG 成功序号与采样 tick** | 帧与诊断输入增加 `sg_seq`/`sg_tick`、`cur_seq`/`cur_tick`；SG 序号只在真实 UART 读成功后递增 | 现场 SG 实际更新率（≈10Hz）需实测记录 |
| 4 | 重复帧与源年龄 | `diagnosis.c:198-200` 同 `seq` 直接 return，年龄/超时判断整段被跳过；`:203-210` 源失效只数帧不核时间 | 年龄检查在去重 return **之前**执行；源过期持续后走 `REL_SENSOR_UNAVAILABLE` | 现场失联/冻结行为 |
| 5 | 先保护后输出 | `diagnosis.c:174-185` `diag_report_severe()` 先 `rt_kprintf` 再 `safety_post_event()` | 改为先 `safety_post_event(EVT_MULTI_FAULT)`（其内部已同步做短保护），打印只在事后；保留 `may_report` 隔离与一次锁存；投递失败仍保留 pending 事实与 rc | 真实触发后 FAULT_LATCHED 与轴停止 |
| 6 | 参数容量与版本 | `project_config.h:21-51` v`0x00010002`、旧结构 104 B；`ns_storage.h:16` `NS_PARAM_MAX=224`；`project_config.c:99` 严格等值版本校验、`:273` 严格 `len==sizeof` | schema → `0x00010003`，profile 追加在同一结构尾部；`_Static_assert(sizeof<=224)`；旧 104 B/`0x00010002` blob **按字段迁移**（新 profile 置无效），不是回退默认 | 一次 `config_save`+读回、一次重启恢复（现场待批准） |
| 7 | 锁返回码 | `project_config.c:33-34` `cfg_take()` 丢弃返回码；`:44-48` 已有 `cfg_take_checked()`，目前只被 `:214-247` 标定发布使用 | profile set/get/commit/clear 与模式启用一律走 `cfg_take_checked()`；锁失败零字段改动、不 release 未持有的锁；**不借此重构全文件旧锁用法** | — |
| 8 | 记录布局 | `blackbox.c:157` `bb_to_sample()` 显式转 `ns_fault_sample_t`（无独立诊断 reason 字段）；`safety_state.h:46` `FAULT_MULTI_SOURCE=5` | 不改 Flash 布局/schema；REL 细分原因只进 RAM 锁存快照 + CLI 短行 + 主机证据 | 事故后短行/会话证据可核对 |
| 9 | 静止事务门 | `motor.h:271-272` `motor_calibration_static_begin/end()`（持 mot_lock 内复核 READY/未 armed/IDLE/cur=tgt=0） | profile 提交/启用借用该门做**最终资格复核**；不在门内打印/Flash/等待采样 | 故障态 `motor_arm` 拒绝（现场单独一次） |
| 10 | TMC 配置身份 | `tmc2209.h:176` 配置态、`:279` `config_view_generation()`、`:283` `get_applied_config(gconf,chop,cfg)` | 用于 profile 签名（IRUN/IHOLD/微步来源/模式/GCONF/CHOP）与运行期失配检出 | 现场一次 TMC 配置后回读 |
| 11 | ADC 换算与来源 | `current_adc.h` raw/mv/ma 与 `current_adc_cal_t{offset,gain,source,version}`；P0 实测 raw 均值 2060.078、health=DEGRADED | 相对判据**只用 raw 码带**，mA 继续按 THEORETICAL 显示且不参与绝对安培承诺 | 运行基线（静止 P0 数不是运行基线） |
| 12 | CLI | 已有 `diag_mode monitor|active`（`diagnosis.c:458-474`）、`config_save/load/default/show` | 扩展 `diag_mode relative CONFIRM_EXPERIMENTAL`；新增 `diag_profile show/zero/learn/set/commit/clear`；复用既有 Flash 命令族，不另建 | 命令逐字进现场包 |

## 3. 已知正常版本 / 失败复现策略（按任务 §8 顺序）

1. **已知正常版本**：`f1aa2e8` 的 S1/S2/SD/M7/M6/IO 主机链全部是绿的（交接已登记），三目标镜像已构建，
   P0 现场七查询已完整接受 —— 这是"正常"的锚点，不在本轮重跑其结论。
2. **同输入复现（先证缺口存在）**：在**仓外** `f1aa2e8` 基线副本上，用同一组合成输入跑三条具名反例，
   证明修前缺陷真实存在，而不是只在新代码上写断言：
   - `M8-PRE-1` 同帧重放下年龄不推进：把 `sg_seq` 固定、`tick` 前进 → 修前不产生任何源失效判定；
   - `M8-PRE-2` SG 强异常 + 电流不升 → 修前 verdict 永不 severe（`diagnosis.c:274-279` 双计数前提）；
   - `M8-PRE-3` severe 边沿的打印/保护次序 → 修前 `diag_report_severe()` 先打印（`diagnosis.c:181`）后 post。
   三条的修前原始输出与退出码入库，**不得改写成"一开始就全绿"**。
3. **相关 Git diff**：只允许本任务 §5 清单内文件的差异；越界文件（stop_priority/HAL/内核/UART 发送叶子/
   黑匣子 schema/四宏默认值/冻结脚本）必须保持 0 变更，并在交付时逐项证明。
4. **最小改动**：新增 `diag_relative.c/.h` 承载 profile 与相对判据；`diagnosis.c` 只做模式接入、
   去重前的年龄检查与 severe 上报次序；`sensor_service` 只补样本身份；`project_config` 只补 schema 迁移、
   profile 存取与所触路径的锁返回码。
5. **直接复验故障**：同一输入在修后必须给出准确的新行为（`REL_SG_SEVERE` / 源失效 / 先保护后打印）。
6. **针对性回归**：S1/S2/SD/current_cal/M6/IO 各跑一次；未改的旧元负例不反复重跑。

## 4. 设计参数（初始软件值，非物理安全上界）

| 参数 | 值 | 说明 |
|---|---|---|
| SG 最大年龄 | 350 ms | 10Hz 采样 + 3 个样本的裕量 |
| ADC / 整帧最大年龄 | 100 ms | 100Hz 帧 |
| SG 强异常持续 | 500 ms 且 ≥3 个新 SG 样本 | 单次低 SG 不得累计成"持续" |
| ADC 强偏离持续 | 300 ms | 上偏或下偏都算偏离正常带 |
| 中等双源重叠持续 | 300 ms 且 ≥3 个新 SG | 才进 `REL_FUSED_SEVERE` |
| 正常回落迟滞 | 500 ms | 回到带内持续后才解除 warn |

任何离线调整都要逐项列差异；不为迁就已发生的现场失败自动加宽。滤波/统计以 raw 码为单位，
首个有效样本初始化，禁止从 SG=0 或 ADC=0 起算。tick 差用无符号回绕安全式，窗口 < 半圈。
