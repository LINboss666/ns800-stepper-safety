# M8 执行记录：SG/ADC 相对异常检测与主动保护（离线轮）

任务书：`phase8-m8-sg-adc-relative-protection`，基线 `f1aa2e832d536593641cea44ea890fb8ce9558b9`
授权范围（原文口径）：离线读源码、改代码、主机验证、Keil **只构建**、个人分支提交推送、
远端回读、具体现场候选包制作。**不含任何新设备动作。**

## 1. 分支与冻结引用

| 项 | 值 |
|---|---|
| 工作分支（本轮新建，个人分支） | `feature/m8-sg-adc-relative-protection` |
| 基线 | `f1aa2e8`（P0 电压观察收口提交） |
| 软件输入 commit | `f9217005f191a601601d315add02c3e8c51f64ed` |
| 未触碰 | `main` / `phase7` / `phase8` / 所有既有 tag；`debug/tmc-uart-rootcause-20261028` 未被覆盖 |
| 禁止动作 | 未做 reset / rebase / force / merge；未删任何已发布产物 |
| 设备动作 | **无**（`hardware_actions = []`） |

Keil 工程 `project.uvprojx` 的三个 Target 组各新增一条 `diag_relative.c` 条目（锚点=既有
`diagnosis.c` 块之后，插入脚本要求锚点恰好命中 3 次并拒绝其它情况）。工作树为 CRLF
（347783→348371 B，CR 8246→8261），入库 blob 经 `core.autocrlf=true` 规范化为 LF。

## 2. 实现的东西（一句话一层）

- `sensor_service`：帧里追加 `sg_seq/sg_tick/cur_seq/cur_tick`（RAM-only，黑匣子 Flash 记录格式未动），
  计数只在**该源真的读成功**时前进，且继承调用方传入的基帧 ⇒ 不新增文件静态，保住 Fix E 隔离。
- `diag_relative.c/.h`（新模块）：raw 码域判据（SG 0..1023 / ADC 0..4095，不用 mA）、
  profile 逐项具名资格、tick 跨度持续时间、SG 强/ADC 强/中等融合三类严重原因、
  源年龄检查在帧去重**之前**、先 `safety_post_event` 后打印、首次原因不可覆盖的 latch、
  `diag_profile show|zero|learn|commit|clear` 命令族（复用既有静止事务门与配置锁，不新建 Flash 命令族）。
- `diagnosis.c`：`DIAG_MODE_RELATIVE_PROTECTION` 并行模式 + 六项开启门禁 + 一次性 CONFIRM 凭据 +
  覆盖状态只在变化时打印；`diag_status` 增相对段；旧 ACTIVE 的 MEASURED 门禁文本与语义未改。
- `project_config`：0x00010002→0x00010003，内嵌 `diag_rprof_t`，v2 blob 逐字段迁移后再校验，
  profile 发布不动 `cfg_valid/cfg_health`，取锁失败零字段改动且不 release 未持有的锁。

## 3. 我自己写的用例抓到并已修掉的产品缺陷（这是本轮最该被审核的部分）

| # | 缺陷 | 抓它的用例 | 修法 |
|---|---|---|---|
| 1 | `diagnosis_set_mode` 完全没有 RELATIVE 门禁分支（`diag_mode relative` 会无条件进入，且模式行仍打印 `MONITOR_ONLY`） | REL16（+ 反向 RV3 的必要性） | 补六项门禁；模式行统一走 `mode_name()` |
| 2 | `rel_engine_reset_counters` 把 `age_bad_ms/out_run_ms` 一起抹掉 ⇒ `REL_OUT_OF_WINDOW` 永不成立、源冻结也被"每帧清零"掩盖，而注释宣称"源失效保护仍有效"（注释与代码相反） | REL19、REL08 | 时间类累计与 tick 基准在复位中保留，正常路径显式清零 |
| 3 | 恢复迟滞按"事件数×帧间 dt"累计 ⇒ SG 10Hz 分频下 500 ms 窗口被拉成约 5 s；且无 fresh 门时旧样本会被当成"恢复正常" | REL21（正反两向） | 改 tick 跨度 + 必须两源都带来新样本；无新证据时保持不清零 |
| 4 | 非新 SG 样本帧把 `verdict` 写回 `NONE` ⇒ MONITOR_ONLY 下 `diag_status` 九成概率读到 NORMAL | REL15 | `severe_active/active_reason` 粘性返回，直到恢复条件凑满 |
| 5 | 旧 ACTIVE 链 severe 边沿**先打印后保护**（修前观察 `M8-PRE-3` 已在真基线上复现） | 修前探针 + REL27 | `safety_post_event` 先执行，摘要后打印并附 `post_rc` |
| 6 | 100Hz 线程在无 profile 时**每帧**打印 `protection NOT active` ⇒ 控制台刷屏（本板已有同类先例把采集器打死） | REL22 | 覆盖状态机 + 只在变化时打印；行长 ≤126 B 断言 |
| 7 | `project_config_set_rprof` 顺手把 `cfg_valid/cfg_health` 写成"整套可信" | REL23 | 只发布 rprof |
| 8 | `config_in_range` 被以 v2 指针强转调用 ⇒ 读到对象之外的 `rprof` 字节 | REL23（迁移用例） | 先迁移成完整 v3 副本再校验 |
| 9 | 签名把固件无法在线复算的字段（名义步频、样本数）算进去 ⇒ 永远失配 | REL06 | 签名只覆盖 TMC 配置状态/微步来源/irun/ihold/mres/vsense/采样定义 |
| 10 | `zero → commit`（跳过 learn）可带着上一轮 `learn=TRUE` 发布只含零点的候选；学习阶段的工况门禁因 `signature==0` 而**静默关闭** | REL25 | `zero` 即清 learn 凭据并当场算出签名 |
| 11 | 未确认前的 `diag_relative.h` 缺 `rel_live_signature()` 声明 ⇒ 隐式声明返回 int | 编译告警（`-Wall`） | 补声明 |
| 12 | `diag_relative_status_lines` 在取快照失败时打印未初始化 `p.signature` | REL22/REL12 前的自查 | 先清零再打印 |

## 4. 主机验证（真实执行，非静态检查）

工具：`tdm64-gcc 4.9.2`（`/c/Program Files (x86)/Dev-Cpp/MinGW64/bin/gcc.exe`），
flags `-std=gnu11 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter`，shim `tests/host/shim_rel/`。

| 链 | 命令 | 结果 | 台账 |
|---|---|---|---|
| M8 相对保护 | `bash tests/host/run_diag_relative_m8.sh` | rc=0 PASS | `SUMMARY\|cases=27 pass=27 fail=0 pre_bad=0 executed=27` |
| M8 反向 3 项 | `REVERSE=1 bash tests/host/run_diag_relative_m8.sh` | rc=0 | RV1/RV2/RV3 全 `META-OK`（副本编译成功 + 夹具 rc=1 + 完整自洽台账 + 指定失败身份命中） |
| 修前基线探针 | `bash tests/host/run_m8_prefix_probe.sh` | rc=0 | 3/3 在 `f1aa2e8` 的 **git blob 副本**上复现（diagnosis.c blob `cb5d0a4a3e719f05845a8c0a7d180e534e6bc0bd`，33554 B） |
| M7 current_cal | `bash tests/host/run_current_cal_m7.sh` | rc=0 | 34/34（本链新增真实 `diag_relative.c` 参与链接） |
| SD / S1 / S2 / M6-CLI / IO | 各自 runner | 全 rc=0 PASS | SD 两变体；S1 78 例两变体；S2 19 组两变体；M6 10 例；IO 4 变体 |

时间模型与它的边界写在 `tests/host/rel_main.c` 文件头：虚拟时钟、生产循环体经
setjmp/longjmp 按 mdelay 次数受控退出，sensor 与 diagnosis 严格对在同一 10 ms tick，
SG 仍是 100 ms 一次。**已知偏差**：模型里 diag 读到的帧龄恒为 10 ms，真机在 0..10 ms
抖动；用例只依赖"停止喂帧 ⇒ 年龄增长"，因此**不据此宣称时序精度或抢占并发已验证**。

反向注入的锚点与替换文本硬编码在 runner 内（必须恰好命中 1 次，带
`NEGATIVE-INJECTION` 标记）：RV1 = 去掉 `fresh_sg` 的 `sg_seq` 独立样本门；
RV2 = 删掉 ADC 相对严重分支；RV3 = 删掉 RELATIVE 开启时的 profile 资格校验。
RV1 还额外暴露出 REL07/REL11 也依赖该门（如实登记为"额外失败身份"，不改变判定）。

结构尺寸（两编译器实测，见 `build_manifest.json`）：
`project_config_t` GCC 212 = ARMCLANG `.bss.cfg` 212（≤ NS_PARAM_MAX 224，编译期断言三处）；
`diag_rprof_t` 108；`sensor_frame_t` 72（基线 56 ⇒ +16 = 4 个 u32 身份字段）；
`diag_input_t` 60；v2 镜像 104。ZI 增量 +592 B/目标，具名新增与增长对象合计 576 B，
残差 16 B 未归因（如实记录，不宣称已分解）。

## 5. Keil 构建（只构建）

三目标各一次 `-r`（Rebuild All），日志与返回码在
`reports/reviews/phase8-m8-sg-adc-relative-protection/build/`：
全部 `0 Error(s), 0 Warning(s)`，uv4 返回码 0；`-f`/下载**未使用**。
构建钩子只读核对：三目标 `BeforeMake.RunUserProg1=0`，`AfterMake.RunUserProg1=1`
且命令只有 `fromelf --bin`（镜像转换，不是下载器）。
Program Size 相对 M7 的增量三目标一致（Code +12448、RO +4160、ZI +592）。
镜像归档到**新**目录 `reports/firmware/phase8-m8-sg-adc-relative-protection/`，
`e614…` 等既有产物一字未动；manifest 不哈希自身字节。

## 6. 交付物分级（不写未测量的 PASS）

| 级别 | 内容 |
|---|---|
| `HOST_PROVEN` | §4 全部：判据、资格、身份、时序持续、先保护后打印、覆盖披露不刷屏、v2→v3 迁移与持久化往返、三项反向 |
| `BUILD_PROVEN` | 三目标 0/0、镜像与 AXF 身份、跨编译器结构尺寸一致 |
| `NOT_VERIFIED` | 真机上的 RELATIVE 武装/覆盖/学习/提交全流程；v2 参数在真 Flash 上的迁移；`REL_*` 严重触发；SG/ADC 阈值的物理合理性（仍是"学习裕量 + 设计初值"，非实测边界）；真实并发与调度抖动下的时序 |
| `NOT_AUTHORIZED / NOT_EXECUTED` | 整个 §1–§6 现场候选包（含下载） |

## 7. 遗留与下一步（不粉饰）

1. 现场脚本尚未编写：候选包是"阶段/额度/判据"级别，可执行采集器要在真正获准执行的
   上一轮产出并跑 `-SelfTest`（见 `field_candidate_package.md` §6）。
2. BSP 三份阶段文档（`开发进度.md`/`待办事项.md`/`调试记录.md`）本轮**未同步**；
   §3 那 12 条里至少 3 条属于值得进《调试记录》的破案档案，留给下一轮或审核指定。
3. `diag_relative` 的开机自检（`diag_selftest` 风格的机载自检）未新增——真机侧目前只能
   靠 `diag_status`/`diag_profile show` 的观察，属现场待证。
4. P1（独立电流参考、零点+增益）仍是 mA 域结论的前提，本轮一律用 raw 码域，
   没有把任何 mA 当证据。
