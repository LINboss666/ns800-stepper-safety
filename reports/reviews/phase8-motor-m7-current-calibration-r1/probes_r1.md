# M7-R1 复审探针复现记录（本机，离线）

日期：2026-10-29。输入 = 复审包 `probes/`（`C:\Users\LMX\Downloads\m7-review-r1\probes`，
包身份 `9d6d80660255d9fcee5470c8e52d668f12949ee687903cff3d14c0eea7c42029`）。
执行位置：本机 Windows / Git Bash / MinGW-w64 GCC 4.9.2 (tdm64) / Python 3.x。
所有被改文件都在**仓外**副本；`D:\ns800` 的产品与夹具源码不被探针脚本编辑。

## 1. 直接照跑 `probes/reproduce.py` 的结果（原文保留）

```text
Seam anchor moved: adapt the host-only hook explicitly; never report a pass
```

这是复审 §7 预定的行为：锚点因 R1a 修复而移动，脚本按要求**中止**而不是当成 PASS。
因此下面 §2 是显式适配后的复现，每条适配都单独声明。

## 2. 声明的适配（共 5 条）

| # | 适配 | 理由 |
|---|---|---|
| 1 | 接缝锚点从 `    if (motor_diag_gate_check() != RT_EOK) return -RT_EPERM;` 改为 `    e = sp_reserve(SP_DIAG, out);\n    motor_lock_give_checked();` 之前 | R1a 已把 gate 判定与预约收进同一次 `mot_lock` 持有期，旧锚点在产品里不存在。新锚点是同一语义位置（最终资格已过、预约尚未取），且此时**锁仍被持有**——这正是要观察的覆盖关系 |
| 2 | 去掉 `-fsanitize=undefined,float-cast-overflow` | 本机 GCC 4.9.2 无 `libubsan`、不支持该 sanitizer 选项；**UBSan 那条运行时错误原文只能归给云端**，本机不复现 |
| 3 | `-pthread` → `-mthreads` | 本机 MinGW 驱动不接受 `-pthread`（复审 §1 也记录了云端在 Linux 上做过反向映射） |
| 4 | `probe_calcalc.py` 里读 `combined_error_bound_mA` 的字段名改为 `worst_case_bound_mA` | R3b 按要求取消了"把有界仪表限 RSS 成 combined bound"这个量，改成显式区间界并改名；除字段名外探针与输入文档**逐字不变** |
| 5 | 接缝副本按 LF 生成 | 复审的字节锚点是 LF（云端读 Git blob），本机工作树是 CRLF（`core.autocrlf=true`）。副本 = blob 形态 + 注入钩子，语义与云端一致 |

适配脚本：`run_probes_local.py`（与原始 `probes/` 同样只读产品源码，输出全在仓外）。

## 3. 修后观察（本机实际执行）

### 3.1 R1a 真实双线程（`probe_diag_threads.c`）

```text
PROBE pthread cal_begin_rc=99 cal_guard_held=0 grant_rc=0 owner=2 expected_none=0 diag=2 rendezvous=2
```

对照修前（复审 §2 云端原文）：

```text
PROBE pthread cal_begin_rc=0 cal_guard_held=1 grant_rc=0 owner=2 expected_none=0 diag=2 rendezvous=1
```

读法：`rendezvous=2` 表示标定线程确实被挡在 `mot_lock` 门外（该位由
`s2_contend_hits++` 的钩子置位，不是时间猜测）；打印那一刻 `cal_guard_held=0`，
静止事务从未与诊断预约同时成立。诊断释放锁之后，`motor_calibration_static_begin()`
因 `owner==SP_DIAG` 被拒（C22d 的形状）。修前是**两者同时为真**，即并发提交。

### 3.2 R1b 配置取锁失败（`probe_cc.c`，默认分支）

```text
PROBE cfg_mutex_unavailable rc=-12 version_before=2 version_after=2 changed=0
```

对照修前（复审 §2 云端原文）：

```text
PROBE cfg_mutex_unavailable rc=0 version_before=2 version_after=3 changed=1
```

`-12 = -RT_EIO`；版本没动、ADC 元组没动、没有为未持有的锁调用 release。

### 3.3 R2 合法参数下的 µA 导出（`probe_cc.c overflow` 分支）

```text
PROBE legal_gain_rc=0 actual_ma=3299194.000000 raw=4094
[CAL] adc offset_uV=0 gain_uV_per_A=1000 measured=YES
[CAL] cfg valid=YES src=2 offset_uV=0 gain_uV_per_A=1000
[CAL] sample seq=1 tick=1 raw=4094 valid=1 fresh=1
[CAL] sample ma_uA=3299194000 src=MEASURED
```

`3299194000 > INT32_MAX(2147483647)`：修前这一路要把 `float` 强转 `int32`，是未定义行为。
本机没有 UBSan，因此**不**声称本机复现了 sanitizer 报错；云端原文是

```text
current_cal.c:74:41: runtime error: 3.29919e+09 is outside the range of representable values of type 'int'
```

本机能给的、也是更硬的证据是**输出本身**：十进制全宽度打印、行仍短于
RT_CONSOLEBUF 裁剪线、`%s` 字段格式受 tiny printf 支持，且 `offset_uV/gain_uV_per_A`
同批导出都不越界。超范围时改为字面量 `OUT_OF_RANGE`（CC29 断言）。

### 3.4 R3 calcalc 反例（`probe_calcalc.py`，全部为合成输入）

| 输入 | 修前（复审 §2/§4 记录） | 修后（本机） |
|---|---|---|
| `CONTROL_HEALTHY` | rc=0 DRAFT | rc=0 `DRAFT_NOT_APPROVED`，bound 3.1000 mA，仍只出草案 |
| `HOLDOUT_FAIL`（留出点参考 80→100 mA） | rc=0，仍给完整 apply 命令 | **rc=1，无命令** |
| `NEGATIVE_REFERENCE_ERROR`（-0.01） | rc=0 qualified=true | **rc=1** |
| `IMPOSSIBLE_SECOND_MOMENT`（sum_sq=0） | rc=0 qualified=true | **rc=1** |
| `FRACTIONAL_SAMPLE_COUNT`（128.5） | rc=0 qualified=true | **rc=1** |
| `FIELD_NONEXISTENT_SOURCE` | rc=0 qualified=true | **rc=1** |
| `REUSED_HOLDOUT_ARTIFACT` | rc=0 qualified=true | **rc=1**（`HOLDOUT_NOT_INDEPENDENT`） |
| `BOUNDED_ERRORS_RSS`（三个 5.9 mA 限） | 界 7.238439 < 目标 8 ⇒ 通过 | **rc=1** |

`REUSED_HOLDOUT_ARTIFACT` 这一条是**本轮补的**：独立性检查原先写在 FIELD 分支里，
HOST_SYNTHETIC 被整体豁免，于是"三点共用同一来源件声明 + 同一样本区间"仍然合格。
现在按声明判（不读文件，因此与豁免不冲突），共用会话文件只在**响应范围互不重叠**时合法；
具名自检 M7C53 固定这个形状。自检从 53 例增加到 **54 例，54/54 PASS**。

### 3.5 R3b 复审给的数值反例（本机按同一数字复算）

输入：拟合 raw=2000/2100 对参考 20/140 mA，留出 raw=2050 对参考 80 mA，
三点仪表误差限都是 5.9 mA，其余自洽。

```text
status= REJECT usable= False
bound= 13.00000013896206 target= 8.0 pass= False
terms= [bounded+repeatability 0.300, bounded+repeatability 0.300,
        bounded(meter limit, not a standard deviation) 2.950,
        bounded(meter limit, not a standard deviation) 2.950,
        bounded+repeatability 0.600]
```

复审算的"仅仪表有界项的最坏方向传播"是 `0.5×5.9 + 0.5×5.9 + 5.9 = 11.8 mA`；
本工具给的界 `13.000 mA ≥ 11.8 mA > 8 mA` ⇒ 拒。修前的 7.238439 mA "界"不再存在。

## 4. 这些数字**不**是什么

- 全部 `HOST_SYNTHETIC`，包括故意伪造的 `FIELD_MEASURED` 声明；**禁止下发实物**。
- 探针返回 0 只表示"实验按设计跑完"；UBSan/自检返回 1 是具名缺陷检出。
- 本机没有设备：没有串口/DAP/仪器对象，没有下载、复位、目标命令、STEP/DIR/HIGH。
- UBSan 与 Keil 两项**不是本机执行**，来源已在交接里分别标注。
