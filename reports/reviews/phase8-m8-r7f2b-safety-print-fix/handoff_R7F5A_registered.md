# R7F5A 登记回执：两路 LA 采集 + 一次静态 IRQ 试验（本机离线复算，设备动作 0）
被审件 `NS800_R7F5A_LA_DIAG_ENN_Task_20261104.zip` 66179 B `sha256=575a46cefc7614239d51b78fc290134af07e98b818fb5e0da1d74591db094677`。A 侧硬件动作 0；本轮**未开口、未 Start、未发任何命令**。
审查基线 `0cd6458`（= `capture_scope.source_binding.reviewed_delivery_commit`），登记时 HEAD `db3c781`，两者之间改动**全部落在 `reports/` 内**（48 个文件，产品源码/固件/执行器零改动）。
## 1. 包身份（我这边重算，不采信自报）
- 外层 zip 5 条目，内层 `SHA256SUMS.txt` 4 条 **4/4 逐字节相同**，且清单不含自身。
- 嵌入的 `original_R7F5.zip` 声明 60623 B `b326cd8543eec1db…`：实算与声明相同，且与上一轮登记记录（`handoff_R7F5_registered.json.reviewed_package`）相同。
- 落位两件（上一轮已按声明路径放入，本轮只复算不重放）：plan `ee3b2651…` 49994 B、rows `4f094de3…` 22289 B；执行器 `33d3f0af…` 219491 B、镜像 `502dca5d…` 292708 B 均未动。
- A 自报 `new_executable_code=false`、`new_serial_plan=false`、`base_serial_plan_bytes_unchanged=true`，这三条由上面的实算承担，不由自报承担。
## 2. 旧账只读核查（A 要求的分支门）
任务书第 23–26 行要求先只读核查旧批准，再决定用新身份还是停住。实况：
- 账本 `approval_M8R7F5-b502-blind-irq-static-20261104.json`：`version=2`、`spent` **全 0**、`pending` **空**、`created_by_action=approval_init`。
- 用**未改动执行器**的 `validate_ledger_doc` 校验：以当场从磁盘重算的三件身份（plan/executor/image）与计划 quota 传入 → 不拒绝；把 plan 身份改成 64 个 0 再传 → 具名拒绝。即「账有效」这个结论由执行器自己的校验器给出，不是我的手写比较。
- 结论：走**新身份** `M8R7F5A-b502-la-two-channel-static-20261104` 这一支；旧批准身份按任务书标为被替代，随后不得再跑一遍旧盲测。
## 3. 离线判据（56 项 PASS，fails=0，`offline_r7f5a/`）
- 步骤摘要用未改执行器重算 `4a80dcb6dd43028c…`，与上一轮同一计划一致（本包不改串口计划）。
- `base_serial_steps=16` 复算相符；16 步里**没有** `motor_arm`/`motor_start`；`forbidden_commands` 覆盖这两条。
- `--validate-plan` 走生产入口：`checks=57 fail=1`，唯一 FAIL 仍是 `PLAN_MOTION_CAP_BUDGET`，按上一轮口径登记为 N/A（不填假运动预算、不改检查器）。
- 额度对照：`port_open 1 / tmc_config_apply 1 / safety_irq_disable 1` 与计划同名同值；计划里的 `irq_polarity_prep=2` 在补充范围里被拆成 `polarity_confirm 1 + irq_enable 1` 重述，两者相加等于 2；计划为 0 的动作**没有任何一项**被补充范围放宽成正数。
- 仪器额度（`logic_capture_session/logic_start/logic_stop_max/native_capture_save/raw_digital_csv_export`）由外层记录，原串口执行器不扣减也不强制——不谎称两个工具原子联动。
## 4. LA 侧本机实测事实（`la_local_r7f5a/`）
- 控制通道：Logic 2 `2.4.48` 的 MCP `http://127.0.0.1:10530/`。握手原文、15 个工具清单、`start_capture` 参数结构都按原字节入档；Automation Server 10430 当时无监听，本轮未依赖。
- 设备能力（两条**具名拒绝原文**留档）：不支持可调输入阈值电压；数字采样率只接受 25 kS/s–24 MS/s 共 13 档（任务书希望的 1 MS/s 在允许档位内）。两条拒绝都在参数校验期返回 `isError=true` 且**不返回 captureId**，即没有建立采集会话。
- 导出侧通道筛选**不生效**：请求 `digitalChannels:[0]`，实得 8 列。所以「CSV 列号 ↔ 信号」的对应必须现场核，不能假设导出按请求裁剪。
- `la_smoke_capture_digital.csv` 是探头未接任何东西时的 1 秒定长采集：`D0` 全程 0、`D1–D7` 全程 1、两行状态无变化。这是**浮空输入**，标 `CONTROL_PATH_SMOKE`，不作任何电平证据；该采集发生在本任务书下达之前，不占本轮 `logic_capture_session=1`。
- 序列号不入公开仓库：`la_mcp_get_devices_sanitized.json` 只留 `sha256` 前 12 位与长度。
## 5. 三项前置未闭合（照 A 的要求登记为 OPEN，不写 PASS）
- **hardware_input_rating**：分析仪通道的实物输入耐压(型号规格页/最大绝对电压/是否 5V 容许)。未闭合前不进上电/Start/开 COM。
- **acquisition_capacity_100Ms**：≥100 M samples 的实际缓冲容量(1 MS/s 时约 100 s 覆盖)。未闭合前不进上电/Start/开 COM。
- **probe_label_to_gui_channel_mapping**：探头印字 CH1/CH2 与软件通道号的对应, 以及 CSV 列号对应。未闭合前不进上电/Start/开 COM。
这三项是任务书 `preflight_required` 的第 3–5 条，也是 `offline_binding_check` 里 A 自己标为 `REQUIRES_*` 的两项。我不替操作者填现场事实，也不从软件版本或 USB ID 推定耐压。
## 6. 必须保留的边界（不变）
- `polarity_measured=false`、`core_protection_verified=false` 原样保持；`measurement_claims_before_execution` 五项全 false。
- 四条禁用声称原样保留：`PHYSICAL_POLARITY_VERIFIED` / `DIAG_TRIGGER_VERIFIED` / `STEP_PHYSICALLY_STOPPED_BY_LOG_ALONE` / `CORE_PROTECTION_VERIFIED`。
- 本轮能证明的只是「稳定供电窗口内的正常静态逻辑值」（DIAG 预期低、最终 ENN 预期高），不能证明报警态与物理极性两态；无 DMM 不报电压数值；无共同硬件时基不宣称 µs 级延迟。
- 原 `safety_polarity_confirm CONFIRM` 仍只是已披露的**无使能试验临时置位**，不因「LA 接上了」升级为实测；结束断电清除，不作运动前置证据。
## 7. 对我前一轮判断的两条更正（A 的补正照单登记）
- 我说过「Logic 1.2.17 完全没有远程/脚本 API」——**撤回**。我当时实测到的只是：本机 10429/10430 无监听、`C:\Program Files\Saleae Inc` 目录里没有 Logic 2 的 resources/bin、pyusb 的 libusb1/libusb0 后端均 NOT_LOADED。端口没监听只能说明该通道当时未就绪，推不出「没有 API」（官方确有 Legacy Logic 1.x API）。
- 任务书第 58 行「不要先做试采消耗一次机会」与本机事实存在时间差：试采已在任务书下达前发生（见 §4），如实登记，不解释成已满足。
## 8. 停点与执行序（等 A/操作者对新范围的一次批准）
按任务书第 63–70 行的顺序，批准后一次做完，不再交准备轮：
1. 现场供断电状态确认（任务书允许「已上电就不额外复位」；但步骤 0 期望 `mask=0x18` 要求驱动配置为 UNCONFIGURED，即 `MOT_GATE_NO_DRV_CONFIG` 必须成立——若板子还带着上一场的电，第一条状态检查就会成为首错。这一事实由操作者回报，我不代答。）
2. 三项 OPEN 闭合（耐压资料 / 容量核读 / 探头↔通道映射）；
3. 新身份 `--approval-init`（令牌用操作者本次真实原话，绑定原计划字节 + 本补充范围）；
4. LA Start 一次（确认确实在采集，记录设置与截图）→ 原计划 `--dispatch --transport real --backend real --profile response_driven` 一次 → 串口关端口后 Stop → 操作者切断 VM+MCU → 保存原生捕获 + 导出数字 CSV；
5. 判读分两层报：串口结果与仪器结果各自成结论，不拼成「全过」；首错停止、保留已得数据、不重试。
## 9. 复算入口
```
python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/r7f5a_registered_checks.py
python reports/reviews/phase8-m8-r7f2b-safety-print-fix/tools/gen_handoff_r7f5a_registered.py
```
