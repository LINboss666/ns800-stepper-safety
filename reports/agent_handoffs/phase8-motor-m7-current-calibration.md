# M7 交接 — 母线电流标定落地开发与现场包准备

任务：`phase8-motor-m7-current-calibration`
日期：2026-10-29（Asia/Shanghai）
分支：`debug/tmc-uart-rootcause-20261028`
document_base / source_base：`4a626bc49831345d8366deeb6b01cc81e77ec79d`
code_commit = tested_commit：**f1883393f37d58f41bb506603e2e54dbf91776d6**
构建输入 = 同一 code_commit（产品文件在其后未变，其后提交只加 reports/ 与交接）。
**hardware_actions = []**（全程离线）。

## 1. 做成了什么

四项交付全部落地，不是计划表：

1. **标定命令族**（新文件 `APP/current_cal.c`，导出名逐字 `current_cal`）：
   `show` / `sample 32..256` / `apply <offset_uV> <gain_uV_per_A> CONFIRM_MEASURED` /
   `nominal CONFIRM`。复用 Sensor 现有帧（`sensor_service_get_latest()`），
   不新建线程、不新开 ADC 句柄、不与 Sensor 争 ADC、不改时钟/通道/参考配置。
   采样在线统计（u64 累加 + 十进制字符串输出），tshell 栈上最大数组 24 B；
   deadline = `N*20ms + 500ms`，硬上限 12 s，无 FOREVER。
2. **受静止门约束的应用 + 一致性**：
   `motor_calibration_static_begin()/end()` 在**同一次持 `mot_lock`** 内完成静止/所有权/
   关断复核与系数提交；`current_adc` 增加 offset/gain/source/calibrated/**version**
   的单临界区快照发布与 `current_adc_read_measurement_cal()`；
   `sensor_service.c` 的 `current_ma` 与 `current_cal_source` 改为同一快照同读；
   `project_config.c` 的全部运行时下发点移入 `cfg_lock` 持锁段（修掉改前
   "锁内取副本→解锁→锁外 apply" 的晚到回写窗口）。
   持久化完全复用现有 `ns_params_save/load` 与双副本，Flash 布局/schema/结构尺寸未动；
   `apply`/`nominal` 只改 RAM，`config_save` 仍是独立显式动作。
3. **主机验证与三目标构建**：新链 26/26、S1 61/78、S2 18 组 109 例、SD 22/21、
   M6 CLI 10/10×2、I/O 33/35/46/48+非法宏拒绝、V1 25/V2 22，全部 rc=0；
   两项具名反向均被检出；三目标各 Rebuild All 一次，全部 0 Error / 0 Warning。
4. **现场候选包**：`board_candidate.md` 用本轮真实镜像散列，P0/P1/P2 三段分别写清
   测点依据、逐字命令、每次影响、失败处理与额度上限。状态 DRAFT/NOT_AUTHORIZED/NOT_EXECUTED。

## 2. 真实测试/反向结果

| 链 | 结果 |
|---|---|
| `run_current_cal_m7.sh` | rc=0，`cases=26 pass=26 fail=0 pre_bad=0` |
| `run_stop_result_s1.sh` | rc=0，target 必需 61 全 PASS，host_ovr 必需 78 全 PASS |
| `run_stop_priority_s2.sh` | rc=0，target/host_ovr 各 18 组，用例 109 全 PASS |
| `run_safety_data_semantics.sh` | rc=0，off_target 22 / on_target 21 全 PASS |
| `run_m6_cli.sh` | rc=0，10/10 两变体 |
| `run_io_behavior.sh` | rc=0，四变体 33/35/46/48 + 非法宏值编译拒绝 |
| `build_and_run.sh` | rc=0，V1 25、V2 22，FAIL=0 |
| `run_m7_reverse.sh` | rc=0，REV1 `s1_rc=1 hits=3/3`（PS19b/PS19c/PS19d），REV2 `cc_rc=1 hits=1/1`（CC14，连带 CC16） |
| `m7_calcalc.py --selftest` | rc=0，`cases=37 pass=37 fail=0` |

新增用例身份：`PS19a/b/c/d/e`（静止事务）、`SD12-cal-source-same-snapshot`、CC01–CC26。

反向的两条都是**有业务意义的局部反向**，不是新平台：
REV1 把静止复核整个短路 ⇒ 三个拒绝用例同时具名失败；
REV2 让配置只发布来源不发布系数 ⇒ CC14 具名失败。二者在干净对照下均 rc=0。

## 3. 首次失败（原文保留，未删改）

| # | 首失败 | 定位 | 之后 |
|---|---|---|---|
| F1 | `NameError: gain_uv_per_A` | `m7_calcalc.py` 变量名大小写不一致 | 改名后重跑 |
| F2 | `--selftest` 11 项 FAIL（`calcalc_selftest_first_failure.txt`） | 留出误差界把 slope/intercept 强相关当杠杆臂放大；`validate()` 只在 `load_input()` 路径生效导致 `compute()` 直调漏校验；`DUPLICATE_X` 被 `ZeroDivisionError` 抢先；自研"无串口"检查匹配到它自己的模式字符串；`raw_mean` 与自身 min/max 不一致没被判 | 改用数值雅可比一阶传播 + 校验移入 `compute()` + 先判 dx==0 + 改 AST 导入扫描 + 新增 `RAW_MEAN_INCONSISTENT` ⇒ 37/37 |
| F3 | `armclang_rc` 被管道里 `sed` 的退出码顶替 | 宏探针首次运行 | 改为直取编译器 rc 并加三条负控 |
| F4 | `-fstack-usage` 探针因手抄 include 列表缺项而 fatal | 第二轮探针 | 改从 `project.uvprojx` 逐字取 IncludePath/Define |
| F5 | `git diff --cached --check` 首次与 stderr 混流 | 取证方式 | 分流后再判 |

（更早的 `cc_main.c` 编译失败、`shim_cc/rtdevice.h` 结构不完整、SD runner 拒绝仓内
`/tmp` 哨兵路径、反向脚本 `^CASE` 锚点漏匹配等首失败也在实现过程中发生并已修复；
它们属于本包普通实现/工具错误，按任务允许自主修复并继续，原文未全部留存。）

## 4. 三目标镜像身份（已构建，未烧录）

| 目标 | BIN | BIN SHA-256 | AXF SHA-256 |
|---|---|---|---|
| motor-m1-candidate | 265492 B | `4717a718b84043210a52dfedd80fae57bcecc64473ec4f23317b2066162aee53` | `8337f9564e3740a1e7708fd9a7b4021f98cdc69aa098b35e769e1c41efd8f63a` (1686956 B) |
| rt-thread | 265388 B | `1bf17ed8bc4580ea8c2599a458afbe31c689c1ea9c0e3c6a4e65ead6509de542` | `54f9c6c3c98d8a30d29d18a874170bd6c22b663c5259d49e52c8e409a78dfa87` (1686472 B) |
| measure-a0 | 275644 B | `8307cffdcaa132e78bde182eb750209602168095269fbf20a44d46cfb1ab8d49` | `9e5c9d7c5340c98e31e5d56ab778ac10600d3979dccfbde4cdf7d41b9421f08e` (1716896 B) |

宏探针、Program Size、钩子核对、栈取证与 banner 限制见 `build_notes.md`。
**板上最后已证仍是 M5 motor 候选**
`01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`（259580 B），未变更。
M6 motor 候选 `a1ba8226…7f7b` 仍未烧录；不先烧 M6 再烧 M7。

## 5. 能执行的现场步骤 / 仅剩的夹具前提

`board_candidate.md`：P0（身份/静止/电压链自洽）可在电流参考尚未 READY 时**单独**获批；
P1（独立母线参考+拟合+留出验证）当前 `METHOD_NOT_READY`；P2（apply→对照→一次
`config_save`→重启还原）依赖 P1 的合格系数。

现场前最小缺项（一次性列出，不以此停止软件侧）：
1. UT39A+ 原厂说明书的电流档保险丝额定、插孔额定、串联压降、量程持续时间 —— 未取得；
2. 能把表安全串进"确实经过 R1 那段"的 VM 支路转接夹具 —— 未有；
3. 已知且稳定的直流负载（B 方案） —— 未有；
4. 实物 VM 焊盘/丝印与图纸 U9 引脚 8 的对应确认 —— 未做。

## 6. 没做的动作

未创建串口/DAP/仪器对象、未枚举设备、未上电、未烧录、未读目标内存、未复位、
未发目标命令、未转电机、未改供电/接线/模块/SW1。
未调 M6 `run_fault`，未跑 300 帧软件故障，未注入 DIAG，未改 SG 阈值，未切 ACTIVE，
未再开发通用采集器/权限门，未新增第四构建目标或宏例外，
未改 HAL/内核/stop_priority 核心/UART 预算/时钟/引脚/Flash 布局/schema/`.gitignore`，
未动历史 M5/M6 镜像与冻结引用。未索取断电时刻或操作 SW1。

## 7. M6 复审 GO 登记（按复审要求顺带登记，不另开纯登记轮）

`M6_OFFLINE_REVIEW_GO`，接受对象 **4a626bc49831345d8366deeb6b01cc81e77ec79d**（即本包
document_base/source_base）。D-1 / D-2 / D-2e / D-3 全部关闭，本轮不重开 M6、
不重跑其已接受用例。M6 现场候选 66847 B /
`9faaca5904ccd3c2618c4a152cc200f4f756ff45aef3e671cf2815356b706649` 保持
`NOT_BOARD_EXECUTED`。M6 新 motor BIN 未烧录。

## 8. 远端逐 blob 回读

`4a626bc..424ff308f56d95fb7c1c670231d44639752e8ba7` 的**全部 51 个变更文件**已从
`raw.githubusercontent.com` 按 blob 下载并重算：SHA-1 与 `git rev-parse <ref>:<path>`
逐项相等、字节数与 `git cat-file -s` 逐项相等 ⇒ **51 OK / 0 BAD**。
逐行清单见 `reports/reviews/phase8-motor-m7-current-calibration/remote_readback.md`。
三枚 BIN/AXF 的**远端字节** sha256 与本包 manifest 完全一致（例：motor BIN
`4717a718b84043210a52dfedd80fae57bcecc64473ec4f23317b2066162aee53`，265492 B）。
同一批 `git ls-remote` 复核 main / phase7 / phase8 / tag 对象与解引用，均与冻结值一致。

**lossless=false 的一项**：本机 `core.autocrlf=true`，三份 Keil 构建 stdout 及其余文本证据
在入库时把 CRLF 归一为 LF。以 `builds/build_rt-thread.log` 为例：工作树 3779 B/143 CR，
Git blob 3636 B/0 CR。因此仓库内的构建日志**不是工具原始 stdout 的逐字节副本**；
带原始行尾的私有件保留在本机仓库之外。镜像 BIN/AXF 为二进制，不受影响。
（上一轮我把"远端 blob 复算"当成可省略的一步，这次它确实改变了结论：只比工作树会把 51 个
文件全部误报成 MISMATCH。）

## 9. 新发现的既往证据限制（并列保留，不择一采信）

本轮实测：三枚 M7 镜像的 BIN 与 AXF 中都**没有**编译期日期时间串，
`__DATE__` 在 `bsp/novosns/ns800/` 下只出现在一个 markdown 文件里。
而 M5/M6 的 `build_manifest.json` 各自记有 `banner` 值，该值用同一 pattern 扫 M5 归档镜像为
0 命中，即**不能从已归档镜像字节复现**；本轮无设备接入，也没从板上读到启动横幅。
处理方式：M7 manifest 的 banner 记 `NOT_EMBEDDED_IN_IMAGE_BYTES` 并附本段说明；
M5/M6 原件不改写，也不据此断言当时如何取得。

## 10. 未闭合与不外推

- `target_calibration_cases = NOT_EXECUTED`；板上 ADC 电流实测仍未完成。
- 真实 DIAG/SG 端到端触发、故障态 `motor_arm` 拒绝的实机补漏：开放，留给未来有依据的轮。
- 示波器与 A0 相关：按用户决定继续 DEFERRED，不改成 PASS。
- 本包不等于"整机安全验收只剩这一项"。主机算法通过 ≠ `CALIBRATED_ON_BOARD`；
  合成点全部标 `HOST_SYNTHETIC`，`example_*_HOST_SYNTHETIC.json` 里的系数禁止下发实物。
- 新发现的既往证据限制：M5/M6 manifest 的 `banner` 值不能从已归档镜像字节复现，
  本轮镜像内也确实不含编译期日期串；已作为并列事实记录，未择一采信。
