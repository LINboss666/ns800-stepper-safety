# 修正版 P0 计划与 208bf10 版的差异核对（本轮不执行）

比较对象：`p0_package.md` 旧 blob `dbb1c5f8…`（12085 B / 198 行）
→ 新 blob `dd4924b7…`（9345 B / 132 行）。行数减少是**结构重写**，不是条款删减。

## 1. 逐条在场性核对

对两份文本按关键字直查（`yes` = 出现，`MISSING` = 未出现）：

| 条款 | 旧 | 新 |
|---|---|---|
| 载荷 BIN `e614a075…f7da` / 266244 B | yes | yes |
| 载荷 AXF `2205c7e7…6d584` / 1688696 B | yes | yes |
| 回读 `0x08000000` 起 `0x41004` | yes | yes |
| 固件输入 `5b564eb…`、镜像路径不变 | yes | yes |
| 期望横幅 `5.3.0 build Oct 29 2026 23:11:52` | yes | yes |
| 启动完整原文 `[MAIN] boot complete: READY (motion still gate-locked)` | **NO** | yes |
| 采集器 `a2c75ea8…` / 验证器 `bc438094…` / 计算器 `5c7a2340…` 散列绑定 | **NO** | yes |
| 六条命令逐字白名单 + 合计七次发送 + show 两次 | 部分 | yes |
| `--method sysresetreq` 明确方法与已知 UID、`--no-config/--no-wait/attach/core 0` | **NO**（旧版是裸 `pyocd reset -t cortex_m`） | yes |
| 四个 DMM 读数 / UT39A+ / 4 V 档 / H10-1·2·3 | yes（4 V 档文字 **NO**） | yes |
| 不要求 1200 ms 静默、不等自发提示符 | yes | yes |
| 下载器隐含复位不可单独观测 | yes | yes |
| 结论上限 `VOLTAGE_OBSERVATION_ONLY`、不产 MEASURED、不外推相电流 RMS | yes | yes |
| 一次批准文字、P1 `METHOD_NOT_READY`/P2 不在批准内 | yes | yes |
| BIN 时间戳偏移 | **259925/259916（错）** | 260674/260665（对，见 §3） |

新版另加了旧版没有的合同条款：额度失败也消耗、不重试不补发；写返回值必须等于全长并设
`write_timeout`；每块接收字节先 fsync 再判定；两个 `WAIT_DMM` 握手不许预建正式文件；
已知非权威 ENN 长行的 126 字节精确分界（不修复、不替代权威字段）。

**结论：没有削弱任何既有安全或证据条款，因此任务书 §4 的"计划无实质改动"条件成立。**

## 2. 我自己的两项独立复核（不是复述云端 PASS）

### a. 用仓库里真实的 M5 终态响应重放新校验器

云端 `results/m5_replay/` 的四份输入里，`016/018/019` 是**去 CR 的 LF 转写件**
（68/634/227 B），只有 `017`（488 B）与原归档逐字节相同。而板子实际发的是 CRLF 流。
所以我用仓库 `phase8-motor-m5-fault-log/cp1/final_pass/` 的**原字节**（71/488/650/233 B，
CR=3/7/16/6，其中 017 含 1 个 NUL 粘连）直接喂给新采集器的四个校验器：

```text
VERBATIM_M5_REPLAY=4/4 accept
```

四份的 `BODY_TAG` 均在、`PROMPT` 均在帧尾、校验器全部接受；对照的 LF 转写件也接受。
结论：新校验器不是只对自己的合成夹具成立，对真实线上字节同样成立。原文见 `m5_verbatim_replay.txt`。

### b. 校验器与冻结固件的输出语法对齐

`current_cal.c` 在 `5b564eb` 的 `cc_show` 恰好打印六条 `[CAL]` 行，note 行文字与校验器逐字相同。
但 `ma_uA` 有**两个分支**（`current_cal.c:179-189`）：

```c
if (f.valid_current && f.fresh_current) rt_kprintf("[CAL] sample ma_uA=%s src=%s\n", ...);
else                                    rt_kprintf("[CAL] sample ma_uA=N/A (no valid fresh frame)\n");
```

新校验器只接受数值分支，并且要求 `sample seq=… valid=1 fresh=1`。也就是说
**若 show 时刻没有 valid+fresh 帧，采集器会停在第五条发送上**（原始响应已先落盘，不重试，额度已耗）。
`valid/fresh` 由 sensor 线程每轮 ADC 读取成功时置位（`sensor_service.c:104-113`），
而 P0 的第一次 show 发生在第一次人工配对读数之后（分钟级），正常应满足。
这是**有意的 fail-closed 前置**，不是缺陷：我不改候选逻辑，只把这条停止语义登记清楚，
免得下一次现场把它误读成串口坏了。板上实际走哪个分支本轮 `NOT_MEASURED`。

## 3. BIN 时间戳偏移的实测

对交付 BIN（266244 B / `e614a075…`）全文扫描：

```text
b'Oct 29 2026' -> [260674]  count=1
b'23:11:52'    -> [260665]  count=1
```

旧计划的 259925/259916 处读到的是 `b' \n\x00[INFO] N'` 与 `b'number:%'`，确实不含戳串；
新计划的偏移正确。横幅字符串本身两版相同，未被推翻。详见 `bin_timestamp_offset_check.txt`。
这是归档 BIN 的字节恢复，**不是本轮板上实读**。

## 4. pyOCD CLI 选项离线核实

本机 `python -m pyocd --version` = `0.45.1`，`python -m pyocd reset --help` rc=0（不连设备）：

```text
-M MODE, --connect MODE   Select connect mode from one of (halt, pre-reset, under-reset, attach)
-m METHOD, --method METHOD (default, hw, sw, sysresetreq, vectreset, emulated)
-c CORE, --core CORE      Core number used to perform software reset. Default is core 0.
-W, --no-wait             Do not wait for a probe to be connected if none are available.
--no-config / -u UNIQUE_ID / -t TARGET / -f FREQUENCY / -O OPTION=VALUE
```

采集器 argv 用的 `--method sysresetreq`、`--connect attach`、`--core 0`、`--no-config`、`--no-wait`、
`-u/-t/-f/-O…` 全部真实存在；`--no-wait` 的官方语义就是"无探针时不等待"，与计划 §3 的说法一致。
原文见 `pyocd_reset_help_offline.txt`。本机 pyserial 3.5 已安装，但自检路径未导入它（P0T03/P0T04）。
