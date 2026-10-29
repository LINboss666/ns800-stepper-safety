# M7 设计 — 母线电流标定的采样、安全应用与一致性

状态：本轮为离线开发。`hardware_actions = []`，未创建串口/DAP/仪器对象，未烧录，未复位，
未发目标命令，未动电机/供电/接线/SW1。

## 1. 不变量（实现与用例都按这几条判）

I1 **同源**：一笔观测的 `raw / mA / cal_source / cal_version` 必须出自同一套系数快照。
I2 **静止**：只有可信静止且输出关断已被确认时才允许改系数；判断与提交之间不允许放开互斥。
I3 **合法或不改**：任何被拒绝的输入，旧系数、旧来源、健康状态与 Flash 内容逐字节不变。
I4 **不冒充**：`CONFIRM_MEASURED` 只是操作者的证据责任确认，不使 MCU 自证仪表，也不是放行 ACTIVE。
I5 **只改 cal**：`apply` / `nominal` 只动 cal 三字段；不清故障、不自动 arm、不重置 EMA、
    不动 SG 阈值/黑匣子时间等其它配置，不自动 `config_save`。
I6 **有界**：采样等待有明确 deadline 与上限；不存在 FOREVER 等待；不在 tshell 栈放大数组。
I7 **短临界区**：PRIMASK 窗口内只做固定 RAM 字段的拷贝/发布，不等待锁、不做 UART/Flash、
    不 tick、不打印、不跑浮点。
I8 **不改布局**：`sensor_frame`、日志 schema、Flash 布局与结构尺寸不变。

## 2. 方案选择

### 2.1 系数快照

| 候选 | 取舍 |
|---|---|
| 每次读时取三次全局字段 | 拒：直接违反 I1，改前正是这个形状（source_audit A.4） |
| seqlock / 双缓冲 | 拒：单核 Cortex-M7 上的短临界区已足够，多一套协议就多一类可失配的代码 |
| `rt_enter_critical()` 内一次拷贝 offset/gain/source/calibrated/version | **采用**：四字节级拷贝，窗口极短，且沿用本仓已有的发布手段 |

version 只在 `cal_apply` 里自增，因此采样窗口内 `ver_lo != ver_hi` 就是"中途换了系数"，
命令必须报"不适合标定"，而不是把两段值拼起来。

### 2.2 静止门

拒掉的做法：
- 只看 `motor_status` 的两个软件频率为 0 —— 不足以证明输出关断（任务 §6.1）。
- 直接把 `motor_lock_take_checked()` 导出成通用票据给标定用 —— 它的头文件合同是恢复用途。
- 先 `motor_get_snapshot()` 判断、解锁、随后改参数 —— 判断与提交之间有窗口。

采用 `motor_calibration_static_begin()/end()`（`APP/motor.c:2026-2078`）：
`begin` 取 `mot_lock` → 持锁复核 armed/state/两路频率/停止锁存/Safety READY/
`sp_snapshot_t` 的 owner、inhibit、exhausted、motor/diag 输出活动、两类关断锁存、
`metadata_pending`、两类 permit/恢复位 → 再加 `step_pwm_off_confirmed()`、
`step_pwm_output_active()`、`safety_drv_enable_is_low()`；成功即**保持持锁**返回。
`current_cal` 在持锁期间调 `project_config_set_calibration()`，随后 `end()` 释放。

锁序（已核对无环）：`mot_lock → cfg_lock`。
`cfg_take()/cfg_release()` 是 `project_config.c` 的 static，`cfg_lock` 持锁期间只可能进
`current_adc_set_calibration_ex()`，从不回调 motor；其它文件只在**不持 cfg_lock** 时调
`project_config_get_snapshot()`（`blackbox.c:258`、`diagnosis.c:66`、`supervisor.c:82`）。
诊断入口 `motor_diag_gate_check()` 的第一段是 `motor_get_snapshot()`（需要 `mot_lock`），
因此事务期间诊断无法取得输出。

### 2.3 配置发布顺序

改前是"锁内取副本 → 解锁 → 锁外下发"，A 的 applied 可以晚于 B 的 applied 覆盖运行时系数
（source_audit B.3）。改后把所有下发点放进 `cfg_lock` 持锁段：`project_config.c` 的
`:189`（set）、`:223`（set_calibration）、`:263/:274/:284`（load）、`:360`（config_default）、
`:398`（boot）。锁内无 I/O，Flash 读写仍在锁外（`:234-246` 的"锁内取一致副本、锁外擦写"未动）。

### 2.4 命令族（唯一逐字表）

```
current_cal show
current_cal sample 32..256
current_cal apply <offset_uV> <gain_uV_per_A> CONFIRM_MEASURED
current_cal nominal CONFIRM
```
`offset_uV`：微伏，`1650000` = 1650 mV。`gain_uV_per_A`：微伏每安培，`600000` = 0.6 V/A。
实现文件 `APP/current_cal.c`，导出 `MSH_CMD_EXPORT_ALIAS(current_cal_cmd, current_cal, ...)`
（`:348`）。没有保留第二套同义入口。

先判上下文再判参数：`rt_interrupt_get_nest()==0 && !sp_intr_masked() && rt_thread_self()!=NULL`。
非法上下文（含 ISR、PRIMASK 窗口、无线程）**静默返回**，不打印、不等 Sensor 帧。
整数参数用自有严格十进制解析（`cc_u64_dec` 等），先防溢出，拒符号位/尾随字符/指数/缺参/多参，
不使用 `atoi/atof`，不用"响应里含 armed 子串"当许可。

采样：轮询 `sensor_service_get_latest()`，只统计新的 `seq`；`fresh_current==0` 或
`valid_current==0` 不算新测量，分别记 `invalid`；`seq` 回退单独记 `reset`；
重复 seq 记 `dup`。在线统计（sum / sum_sq / min / max / mean×1000 用 u64 累加，
输出走十进制字符串转换，不依赖 tiny printf 支持 `%llu`）。
deadline = `N*20ms + 500ms`，硬上限 12 s。
端点：`min==0 || max==4095` 时 `fit=NO reason=saturated_endpoint`；
`>12bit` 越界或 `n_valid < n_req` 或版本/来源中途变化同样给出不可拟合原因。
`show`/`sample` 不写 cal、不写 Flash、不碰 GPIO/TMC、不 arm。

## 3. 数值域（含本轮披露的收紧）

- offset_mV：有限，`0..3300`。
- gain_V/A：有限，`0.001..100`。**这是本轮新增的数值安全范围**，目的是防止极小 gain 让
  现有 mA 输出/整数化溢出；不代表该宽范围经过本硬件验证，名义值仍 0.6 V/A。
- 来源：ADC 运行时 API 保留 NONE/THEORETICAL/MEASURED（原契约未改写）；
  配置结构只接受 THEORETICAL/MEASURED。
- `nominal` 恢复 1650 mV / 0.6 V/A / THEORETICAL，不动其它字段。

## 4. 主机计算工具

`reports/reviews/phase8-motor-m7-current-calibration/m7_calcalc.py`（纯 stdlib，无传输/设备模块，
`--selftest` 37 例）。
拟合 `I_mA = a*raw_mean + b`（≥2 个拟合点 + ≥1 个独立留出点，留出点必须在拟合 raw 区间内），
再按固件名义 `k = 3300/4095` mV/count 取 `gain_V_per_A = k/a`、`offset_mV = -b*gain`。
资格判据在看到现场结果前固定并写进代码：
`span_I > 10*(E_lo+E_hi)` 且 `span_raw > 10*u(span_raw)`；留出点目标
`max(5 mA, 10% 参考读数)` 标 `PROPOSED_FOR_APPROVAL`；
误差界本身大于目标即不算通过。合成低跨度负控（M7C34）确实被拒。

## 5. 反向证明（只做有业务意义的两项）

`tests/host/run_m7_reverse.sh`，在仓外的源码副本上做单点修改，然后跑真实 runner：

| 反向 | 改法 | 必须具名检出 | 实测 |
|---|---|---|---|
| REV1 | 把 `motor_calibration_static_begin()` 的全部静止复核替换成立即 `return RT_EOK` | S1 的 PS19b/PS19c/PS19d | `s1_rc=1 hits=3/3` |
| REV2 | `project_config_set_calibration()` 只发布新来源、运行时系数仍取旧值 | CC14 | `cc_rc=1 hits=1/1`（同时连带 CC16，共 24/26） |

对照（不改源码）两条链均 rc=0，见 `host_s1.txt` / `host_current_cal.txt`；
具名失败原文见 `reverse_detail.txt`。

## 6. 本轮明确没做

- 没有创建真实串口/DAP/仪器对象，没有枚举设备，没有上电/烧录/读目标内存/复位/发目标命令，
  没有转电机，没有改供电/接线/模块/SW1。
- 没有调用 M6 `run_fault`，没有重跑 300 帧软件故障，没有改 SG 阈值、没有切 ACTIVE、
  没有注入 DIAG。
- 没有新增第四构建目标、没有新增宏例外、没有改 HAL/内核/stop_priority 核心/UART 预算/
  时钟/引脚/Flash 布局/schema/`.gitignore`。
- 板上 ADC 电流实测 = 未执行；所有合成标定点均标 `HOST_SYNTHETIC`。
