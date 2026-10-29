# M7-R1 三目标构建记录（本机 Keil 实际执行）

日期：2026-10-29。任务：`phase8-motor-m7-current-calibration-r1`。
`code_commit = 5b564eb962e8518920f581111d7a460a2b1fce64`（产品与测试最终输入先成提交，
再做唯一一次三目标 Rebuild All；本轮不因纯文档调整重编）。

## 1. 命令与工具（本机执行，非引用）

```
cd rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad
C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t <target> -j0 -o <绝对日志路径>
```

`<target>` 依次 `rt-thread`、`rt-thread-measure-a0`、`rt-thread-motor-m1-candidate`，
各 Rebuild All **一次**，`uv4_rc=0/0/0`（原文见 `builds/build_*.log` 与交接）。
`-r` 是 Rebuild All；**没有使用 `-f`（下载）**，没有任何其他参数，没有连接 COM5/DAP/仪器。

```
*** Using Compiler 'V6.24', folder: 'C:\Keil_v5\ARM\ARMCLANG\Bin'
Product: MDK Plus 5.43 / Arm Compiler for Embedded 6.24 / UV4 5.43.1.0
```

## 2. 构建前后钩子只读核对

`project.uvprojx` 三目标 `<BeforeMake><RunUserProg1>` 均为 `0`；
`<AfterMake><RunUserProg1>=1` 且 `UserProg1Name` 全是 `fromelf --bin … --output …`
（镜像格式转换，不是烧录器）。三份日志尾部 `After Build - User command #1` 原文与此一致，
即 **Before/AfterMake 只有既有安全构建动作**，本轮未新增钩子。

## 3. 结果（逐项从字节/日志读出，不手填）

| 目标 | 结果 | Program Size | BIN | AXF |
|---|---|---|---|---|
| rt-thread | 0 Error(s), 0 Warning(s) | Code=185786 RO-data=79770 RW-data=2188 ZI-data=83792 | 266140 B | 1688212 B |
| rt-thread-measure-a0 | 0 Error(s), 0 Warning(s) | Code=193082 RO-data=82730 RW-data=2188 ZI-data=91512 | 276388 B | 1718624 B |
| rt-thread-motor-m1-candidate | 0 Error(s), 0 Warning(s) | Code=186018 RO-data=79646 RW-data=2188 ZI-data=83792 | 266244 B | 1688696 B |

与 M7（`68d0774`）相比三目标都略增（约 +752 B Code），来源就是本轮 R1a/R1b/R2 的
新增持锁路径、有返回码取锁与十进制导出实现。BIN/AXF 的 bytes+SHA-256 全部写在
`reports/firmware/phase8-motor-m7-current-calibration-r1/build_manifest.json`，
由脚本从文件本身计算，不是抄写。

**六件镜像本轮未烧录**：`NEW_IMAGES_NOT_FLASHED`；板上最后已证仍是 M5 motor
`01d3e5ab…64bc`（259580 B）。上一轮 M7 的六件镜像与 manifest 原样保留，不重做、不删除。

## 4. 宏与源清单（§6.6 的核对项）

`builds/macro_probe.txt`：

```
rt-thread                    (-DMOTOR_EXPECT=0 -DTMC_EXPECT=0; 无额外 -D) -> armclang_rc=0
rt-thread-measure-a0         (NS800_TARGET_MEASUREMENT_A0=1)               -> armclang_rc=0
rt-thread-motor-m1-candidate (A0=0 MOTOR=1 TMC=1 STEP=0 LIMIT=0)           -> armclang_rc=0
NEGATIVE expects MOTOR=1 with no defines -> armclang_rc=1
NEGATIVE STEP_DIAG 被写成 1              -> armclang_rc=1
NEGATIVE SAFETY_LIMIT 被写成 1           -> armclang_rc=1
```

三个正控 + 三个负控都有区分力（同一 armclang、同一 `--target=arm-arm-none-eabi
-mcpu=cortex-m7`）。默认与 A0 目标四宏保持 `RT_FALSE`，motor 候选保持 `1/1/0/0`，
**本轮未新增任何目标级宏例外**。

源清单（逐目标读 `project.uvprojx`）：三目标各 **136** 个源文件，
`current_cal.c / motor.c / project_config.c` 各出现 **1 次**。

> 过程更正（如实登记）：本机第一次跑这套取证时有两处**非产品**错误——
> ① armclang 调用漏了 `--target=`，三个正控与三个负控全部因同一原因失败，负控当时
>    "看起来命中"其实是假象；补上目标三元组后正控才真为 rc=0。
> ② `.su` 行按 4 字段解析（实际是 3 字段 `file:line:func\tbytes\tkind`），导致首次
>    统计 0 行。两处都在取证脚本里修，未触及产品源码，修后结果如上。

## 5. 栈（`builds/stack_usage.txt`、`stack_usage_O1.txt`）

armclang `-fstack-usage` 编真实产品源文件（`IncludePath`/`Define` 逐字取自工程）。
本轮改动涉及的帧（-O0）：

| 函数 | 静态局部帧 |
|---|---|
| `current_cal.c:145 cc_show` | 304 B（M7 是 240 B，+3×24 B 的导出缓冲） |
| `motor.c:2028 motor_calibration_static_begin` | 272 B |
| `project_config.c:214 project_config_set_calibration` | 136 B |
| `current_cal.c:95 cc_scaled_dec` | 56 B |
| `motor.c:2224 motor_pwm_grant_to_diag` | 24 B |
| `current_cal.c:85 cc_copy_out` | 20 B |

这些是**每函数自己的静态局部帧**，不是栈高水位。整机/线程栈高水位本轮
`NOT_MEASURED`（无设备）。`FINSH_THREAD_STACK_SIZE` 在本次 `rtconfig.h:125` 是 **4096**
（源码事实），可以补；但"4096 够用"仍然没有实测依据（M5/M6 的告警史已说明
配置值不等于充分性，见项目记忆与 URO-R2 记录）。

## 6. 横幅（本轮已更正口径）

`builds/banner_probe_r1.txt` 用两条独立 pattern 在**归档 BIN 的零基字节偏移**上实测：

| 目标 | date | time |
|---|---|---|
| rt-thread | `Oct 29 2026@260556` | `23:11:27@260568` |
| rt-thread-measure-a0 | `Oct 29 2026@270805` | `23:11:40@270796` |
| rt-thread-motor-m1-candidate | `Oct 29 2026@260674` | `23:11:52@260665` |

三枚都各命中 1 次日期与 1 次时间，时间戳与本次构建时刻一致。
来源必须分开：这是**归档字节恢复的构建戳**；本轮 `HARDWARE_ACTIONS=[]`，
没有**板上实读的启动文本**。M7 轮"镜像里没有横幅"的错误结论已在
`banner_erratum.md` 与其受影响证据文件里撤回。

## 7. 行尾与隐私

- 归档的 Keil 日志工作树形态含 CRLF（每份 143 CR），提交后被 `core.autocrlf=true`
  归一成 LF：这是**已披露的规范化**，`lossless=false` 对"工作树原字节"这一说法成立，
  转换规则可断言为 `blob == 原字节.replace(b"\r\n", b"\n")`。BIN/AXF 是二进制，不受影响。
- 未重跑构建、未重造 stdout；日志尾部原文保留。
- 日志内只有工具安装路径（`C:\Keil_v5\…`），不含用户名；未归档 `.map/.o/.crf`
  等含绝对路径的构建产物；证据里不含串口序列号、DAP UID 或操作者标识。
