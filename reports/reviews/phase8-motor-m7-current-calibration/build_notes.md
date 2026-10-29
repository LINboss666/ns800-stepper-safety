# M7 三目标构建记录

## 1. 实际命令与工具

```
cd rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad
C:\Keil_v5\UV4\UV4.exe -r project.uvprojx -t <target> -j0 -o <绝对日志路径>
```
`<target>` 依次取 `rt-thread`、`rt-thread-measure-a0`、`rt-thread-motor-m1-candidate`，
各 Rebuild All **一次**，uv4 返回码均为 0。
`-r` 是 Rebuild All；**没有使用 `-f`（下载）**，没有任何其他参数。

工具版本（实取自日志与 `armclang --version`）：
```
*** Using Compiler 'V6.24', folder: 'C:\Keil_v5\ARM\ARMCLANG\Bin'
Product: MDK Plus 5.43
Component: Arm Compiler for Embedded 6.24
UV4 5.43.1.0
```

## 2. 构建前钩子只读核对

`project.uvprojx`：三个目标的 `<BeforeMake><RunUserProg1>` 均为 `0`。
`<AfterMake><RunUserProg1>` 为 `1`，其 `UserProg1Name` 分别是
```
fromelf --bin !L --output rtthread.bin                          (rt-thread)
fromelf --bin !L --output build\a0\measure-a0.bin               (a0)
fromelf --bin !L --output .\build\motor_m1\rt-thread-motor-m1.bin (motor)
```
全部是镜像格式转换，不是烧录器/下载命令。三份日志尾部的 `After Build - User command #1`
原文与此一致。构建期间未连接 COM5/DAP/仪器。

## 3. 结果

| 目标 | 结果 | Program Size | BIN | AXF |
|---|---|---|---|---|
| rt-thread | 0 Error(s), 0 Warning(s) | Code=185218 RO-data=79590 RW-data=2188 ZI-data=83792 | 265388 B | 1686472 B |
| rt-thread-measure-a0 | 0 Error(s), 0 Warning(s) | Code=192514 RO-data=82550 RW-data=2188 ZI-data=91512 | 275644 B | 1716896 B |
| rt-thread-motor-m1-candidate | 0 Error(s), 0 Warning(s) | Code=185450 RO-data=79466 RW-data=2188 ZI-data=83792 | 265492 B | 1686956 B |

完整 stdout 原文：`builds/build_<target>.log`（三份均含 `compiling current_cal.c...`）。
镜像与散列：`reports/firmware/phase8-motor-m7-current-calibration/build_manifest.json`。

与 M6 相比 ZI-data 只 +8 B（83784→83792），Code 增量大是因为 `current_cal.c` 命令模块
进了三个目标（任务 §9 允许同一源项进既有三目标）。

## 4. 目标宏探针（`builds/macro_probe.txt`）

用与三目标同一条 armclang，`<Define>` 逐字取自 `project.uvprojx`
（默认目标 line 343、A0 目标、motor 候选 line 5821）：

| 目标 | MOTOR | TMC | STEP | LIMIT | 探针 rc |
|---|---|---|---|---|---|
| rt-thread | RT_FALSE（无目标级 -D，取 `motor.h:68-69` 头文件默认） | 同左 | RT_FALSE | RT_FALSE | 0 |
| rt-thread-measure-a0 | RT_FALSE | RT_FALSE | RT_FALSE | RT_FALSE | 0 |
| rt-thread-motor-m1-candidate | **1** | **1** | **0** | **0** | 0 |

负控三条（故意把期望写成 1 / 把 STEP 或 LIMIT 写成 1）均 `armclang_rc=1`，
证明该门真在判而不是恒过。
默认与 A0 目标四宏保持 0；motor 只有既有 MOTOR=1、TMC=1 例外，STEP=0、LIMIT=0。
未新增第四目标、未新增宏例外、未改优化/链接/时钟/引脚。

## 5. 新调用链的已知局部栈（`builds/stack_usage.txt`、`stack_usage_O1.txt`）

用 armclang `-fstack-usage` 编真实产品源文件（include/define 取自工程），分别取
`-O0` 与 `-O1`（工程 `<Optim>1</Optim>` 对应 -O1）。逐函数**自身静态局部帧**字节：

| 函数 | -O0 | -O1 |
|---|---|---|
| `cc_sample` | 304 | 272 |
| `current_cal_cmd` | 24 | 256（-O1 把小 callees 折进调用者帧） |
| `cc_apply_or_nominal` | — | 40 |
| `motor_calibration_static_begin` | — | 272 |
| `project_config_set_calibration` | — | 40 |
| `current_adc_read_measurement_cal` | — | 48 |

新链的帧和（-O1，静态局部，单链下界）：
`current_cal_cmd → cc_sample` ≈ 528 B；
`current_cal_cmd → cc_apply_or_nominal → motor_calibration_static_begin` ≈ 568 B。

**这些不是目标栈高水位。** 未含 ABI 另行登记的 callee-saved 压栈；-O1 内联会让"调用者帧"
与"被调者帧"不能简单相加；命令跑在 FinSH/tshell 线程上，其配置栈大小本轮未重新推导。
**板上实测高水位 = NOT_MEASURED**，留给未来现场轮（现场先做一次最深链的栈水位读取）。

采样不放 2 KiB 数组在 tshell 栈：统计在线累加，最大数组是 `cc_u64_dec` 的 `char tmp[21]`
与 `mean_buf[24]`/`sq_buf[24]`。

## 6. 横幅（banner）——本轮取证限制与既往证据不一致，并列记录

- 本轮实测：三个 BIN 与三个 AXF 里**都找不到**编译期日期时间串；
  `__DATE__` 在 `bsp/novosns/ns800/` 下只出现在一个 markdown 文件里，不在任何源文件。
  因此这三枚镜像没有可回取的 build banner。
- 本轮无设备接入，也没有从板上读到启动横幅。
- 对照：M5/M6 的 `build_manifest.json` 里记有 `banner: "build Oct 29 2026 …"`，
  该值**不能**从已归档的 M5/M6 BIN/AXF 字节复现（本次用同一 pattern 扫过 M5 镜像，0 命中）。
  合理来源是当时的板级启动日志，但本轮没有该原始日志在场，因此不据它下结论。
- 处理：`build_manifest.json` 的 banner 字段记 `NOT_EMBEDDED_IN_IMAGE_BYTES`，
  并把这条差异作为**既往证据限制**并列保留，不改写 M5/M6 原件，也不把本轮的
  "镜像里没有"回写成"M6 当时写错了"。

## 7. 隐私、行尾与空白处理

构建日志中的工具路径为本机安装路径（`C:\Keil_v5\…`），不含用户名；
未归档 `.map`、`.o`、`.crf` 等含绝对路径或机器信息的构建产物。
交接与回执不写串口序列号、DAP UID 或操作者标识。
`reports/reviews/phase8-motor-m6-offline/__pycache__` 之类本机生成物未纳入本轮归档。

**行尾归一（必须披露，lossless=false）**：本机 `core.autocrlf=true`，三份 Keil stdout 与
其余 `.log/.txt` 证据在 `git add` 时把 CRLF 归一为 LF 入库。以
`builds/build_rt-thread.log` 为例：工作树 3779 B / 143 个 CR，Git blob 3636 B / 0 个 CR。
因此**仓库内归档不是 Keil 原始 stdout 的逐字节副本**，只在内容（去行尾分隔符差异）上逐字保留；
带原始 CRLF 的私有原件保留在本机仓库外，未随公开包发布，也不再宣称"全部原样"。
镜像 BIN/AXF 是二进制，不受该归一影响（远端 blob 字节与本地一致，见交接 remote_readback）。

Keil 的 `Program Size:` 行原文带行尾空格，三份日志各 1 处，未为好看而裁掉。
本轮 `git diff --cached --check` 返回 rc=0、无报告行（与 M6 复审当时报出的 3 处不同：
那次是在未暂存/patch 语境下判的）；两种观测并列记录，
未来回执不笼统写"整个历史范围 diff-check 为 0"。

## 8. 远端逐 blob 回读

见 `remote_readback.md`：`4a626bc..424ff30` 全部 51 个变更文件从
`raw.githubusercontent.com` 按 blob 下载后重算，SHA-1 与 SHA-256/字节数
与 `git rev-parse 424ff30:<path>` / `git cat-file -s` 逐项一致，51/51 OK、0 BAD。
冻结引用同时用 `git ls-remote` 复核（main / phase7 / phase8 / tag 对象与解引用）。
