# NS800 交接：phase8-target-measurement-a0-export-r1（离线导出修复与采集门）

日期：2026-10-26（Asia/Shanghai）
document_base：`d03047dbb3aac596a38d1e34f9c6b48c4e513458`
code_commit：`cd8d9f095030c08634a082dd2bb9bc622d92fb1d`（完整软件输入，先提交；证据与镜像只在其后追加）
任务书：`NS800_A0_Board_A_Review_and_Export_Fix_Task_65d35e40_20261026.md`
上一轮结论：`BOARD_A_PARTIAL_RESULTS_ACCEPTED / BOARD_A_01_EXPORT_REWORK_REQUIRED`

## 1. 本轮实际做了什么（只列已完成）

离线导出修复 + 离线验证 + 双目标构建 + 新镜像交付。**未烧录、未读 Flash、未连接探针/串口/仪器、
未复位、未发目标命令、未重跑板级批次**；`hardware_actions=[]`，`production_changed=true`
（不沿用上一轮的 false）。

1. **BOARD-A-01a 生产修复**（只改 `sp_measure_a0.c`/`.h`）
   新增模块私有 `spm_emitfv/spm_emitf`：`rt_vsnprintf` 到调用方栈上的 `SPM_TEXT_CAP(640)` 缓冲，
   整行经 `rt_kputs` 送出。模块内 25 处诊断长行**全部**改道（init/cap/status/stop/usage/错误路径/
   CSV 头与数据行），不是只修 CSV。容量不足只发一条短 ASCII
   `[spm] EXPORT_ERROR: line_overflow need=.. cap=..` 并返回 -1，**不发半条记录**；
   `spm_dump_records()` 传播 -3，`spm_cmd` 的 cap/status 报告分支返回 `SPM_ERR_EXPORT(3)`。
   未改 `kservice.c`、`RT_CONSOLEBUF_SIZE`、线程栈、全局打印配置、四道宏；
   schema=2 / 120 B 布局 / 47 列顺序名字格式 / 64 槽 / 判据 / 门控 / 时间戳位置 / 三入口保护形状不变；
   默认开关=0 时仍是空翻译单元，且**无新增 BSS**（见 §2 的 ZI 增量 0）。
2. **§7 机制核对新增一条事实**：Keil 工程实际链接的是 `src/klibc/rt_vsnprintf_tiny.c`
   （不是 std 版，libc 版被 `RT_KLIBC_USING_LIBC_VSNPRINTF` 编译掉），其"先判 `str<end`
   再无条件 `++str`、末尾 `return str-buf`"意味着返回值是**应写总字节数**（C99 语义）；
   溢出判据在目标上真会触发，不是死代码。测量目标 map 里
   `spm_emitfv refers to rt_vsnprintf_tiny.o(.text.rt_vsnprintf)` 独立证实了这一点。
3. **BOARD-A-01b 采集门做成代码行为**：新增 `tests/host/a0_export_gate.py`。只读**原始接收字节**
   （保留 NUL/CRLF/异步行，不删不补），头行逐字匹配 47 列，完整记录要求 47 列 + 逐列类型/取值域 +
   schema=2 + complete=1 + slot 从 0 递增 + 代次逐条 +1 + entry 与本批一致 + 条数==预期，
   并要求 STOPPING/in_flight=0/dropped=0 与状态行 records 一致；
   完整但 `body_valid=0` 的样本**仍存档**、只从时长统计排除，且 `extra_protection_actions` 恒 0；
   任一失败 ⇒ `clear_records_send_count=0` 且 `next_batch_trigger_send_count=0`。
   无串口副作用；采集侧继续用本机现有 PowerShell SerialPort，不装 pyserial。
4. **离线验证链**：新增 A0 链专用控制台模型 `tests/host/sp_measure_a0_console.h`
   （由 runner 以 `-include` 注入三个编译单元，**公共 `tests/host/shim_ps/*` 一个字节未改**），
   与 `kservice.c:369-411` 及 `rt_vsnprintf_tiny.c` 逐条同形并列出边界；新增 TMA25/TMA26/TMA27、
   第三个可执行体 `a0_min`（`-DSPM_TEXT_CAP=256`，仅主机，用来把"容量不足"变成可执行路径），
   以及新增具名反向家族 `rev_export_header_single_limited_kprintf`。
   TMA01..TMA24 身份与期望未删未放宽。

## 2. 关键结果（原文与身份见 `host_chain_verification.txt` / `keil_build.txt`）

| 项 | 结果 |
|---|---|
| 基线缺陷复现（沿用同名探针，逐字复用 kservice.c 函数体） | `wire=127 visible=126 embedded_nul=1 visible_columns=14`；`rt_kputs` 对照 490 B 完整无 NUL。**与云端探针数字逐项一致**。rc=0 只表示旧缺陷复现，不是生产 PASS |
| 修后 A0 主机链（off/on/min 三构建，编译输出 0 字节） | 正向 9 组 26 例全 PASS；`on/export` 3 例 39 断言 PASS；`min/export` 3 例 26 断言 PASS |
| 头行上线字节（修后） | 行体 489 B + LF = **490 B**，47 列，尾列 `dropped_at_publish,complete` 落在第 127 字节之后，捕获里 trunc=0 nul=0 |
| 诊断长行覆盖面 | 修前 25 处里 9 处最坏值 >127；修后夹具如实数出"超过 127 字节的诊断行数 = 2"（cap 第 3 行 142 B、init 行 129 B） |
| 容量不足 | TMA26：rc=-1 + 具名 `EXPORT_ERROR need=649 cap=640` + 超长内容零片段；a0_min(256)：`dump=-3` + 具名错误 + 半条头行与数据行都不上线 |
| 新增具名反向 | 头行退回单条受限 `rt_kprintf` ⇒ **TMA27 具名检出 11 条断言失败**（489→126、490→127、47→14 列、trunc=1、nul=1、粘连），其余每组台账逐条一致且 PASS |
| 既有反向与台账负控 | `rev_spm_unavailable_is_valid` META-OK(3)；`rev_spm_snapshot_after_unmask` META-OK(1)；省略 TMA18 的 RESULT 仍判 INFRA-FAIL |
| 采集门自检 | `pass=20 fail=0`（2 正控 + 16 负控 + 2 语义补充）；每条 REJECT 的 clear/next 计划数都是 0 |
| 采集门 vs 板级真实原始字节 | 4 批（1/16/1/cleanup）**全部 REJECT，clear=0 next=0** ⇒ 这道门当初会拦下 clear 与后续批次 |
| 采集门 vs 修后真实导出切片 | **ACCEPT**，rows=3、nul=0、failures=[]、纳入时长 3 条 |
| 集中回归 | A0 全链 rc=0；S1 rc=0（反向 5/5，第②项按 §10.2 重指向 S2，如实登记 REV-HANDED-OFF）；S2 rc=0（target 23/23、host_ovr 79/79、五家族反向全 META-OK）。V1/V2、I/O、SD、旧负例/元负例输入未变 ⇒ 登记 NOT_RERUN |
| Keil 双目标各一次 Rebuild All（只构建） | 两目标均 0 Error / 0 Warning，日志 grep 计数 0。默认目标 Program Size 逐项与上轮相同；测量目标 Code +304 / RO +48 / **RW 0 / ZI 0**（640 B 缓冲在栈上，不进 ZI） |
| 默认镜像字节对照 | 251148 B，仅 5 处差异，跨度 `0x03bf68..0x03bf73`，内容就是 `__TIME__/__DATE__` 横幅 ⇒ **无功能字节差异**；map 里 `Removing sp_measure_a0.o(.text), (0 bytes)` 且测量符号全部 NOT-PRESENT |

新镜像完整身份（本轮新建，**未烧录**；板上仍是旧 `358fd81a…`）：

- `reports/firmware/phase8-target-measurement-a0-export-r1/default.bin`
  251148 B `02e984583255f3414f43fb5c077dc9ba82eba98553d2cb9d7f7feee9336ebd80`
- `reports/firmware/phase8-target-measurement-a0-export-r1/measure-a0.bin`
  261452 B `5f30d3f058ceff516b2185aa3a1e597bbe63293b94a6fd801fd989a6f7b2b8e6`
  （vs 板上镜像差 153890 处 / +352 B：导出改道导致代码尺寸与地址整体位移，首处差异即向量表函数指针）
- AXF（本地）：default `1645448 B 016a689b…f70fdb7`；measure `1675944 B 8b95464e…f2b1b959`
- map（本地）：default `1946989 B ab33ac1d…`；measure `1999062 B 07498bc9…`

## 3. 仍然未达成 / 不得外推

- **旧采样的有效性与时长统计仍为 `NOT_ACHIEVED`**；本轮没有、也不能从截断数据补写有效字段、
  换算 µs 或反算窗口上界。窗口最坏值、真实频率、物理波形风险三项均未关闭。
- 主机模型只覆盖内核层；设备层 `\n→\r\n` 展开、UART 时序、其它线程日志的真实插入时机不在模型内。
  因此**不得**把"模型/夹具通过"写成"目标导出已通过"。
- 不承诺一行绝不被插入（`rtconfig.h` 未定义 `RT_USING_THREADSAFE_PRINTF`，
  `CONSOLE_TAKE/RELEASE` 展开为空）；本轮不修所有模块的历史长日志
  （板级 `[SS] STEP:` 行的 2 个特例 NUL 即属此类，已如实留痕）。
- 恢复下载仍未演练 ⇒ 不得宣称完整恢复能力已证明。快照 `b89f8a7c…`、
  恢复 HEX `b3ac97b9…`、两处保存位置、PATH_INDEX 与恢复专用工程均未动。
- B-1、BUG-008、RISK-03/03a、物理 RISK-04-b、M-1/M-2/M-3 继续按未覆盖部分开放；
  A0 既有离线 GO 是历史限定结论，不用于宣称本轮目标导出已通过。

## 4. §7 口径校正（随本交接登记，不回改旧文件）

CAL-1 构建戳一致只是关联证据；CAL-2 实际擦除扇区范围未独立核定；
CAL-3 Halted 不证明此前无 Reset-and-Run（只断言 1 次主动 SYSRESETREQ）；
**CAL-4 更正**：`0x08000000` 不在 `RAM2(0x80000000..0x9FFFFFFF)`，按已安装 pyOCD 0.45.1 的
`_create_default_cortex_m_memory_map` 它落在名为 `Code` 的 RamRegion（`0x00000000` len `0x20000000`），
故 `savemem` 的 `flash_init_required` 为假、走纯 AP 读；CAL-5 主机 `sizeof_ctx=48` 与目标 44
按指针宽度 ABI 差异分别记录；CAL-6 17 次保护日志/锁存/trigger 文本不证明持久 Flash 写入全部完成
（须区分触发/排队/完成/失败）；CAL-7 探针列表 2 次编码重试偏差接受并留痕，不为计数重跑硬件。

## 5. 板级原始记录的脱敏可审副本

`reports/reviews/phase8-target-measurement-a0-export-r1/board_raw_archive.md`：
59 个文件、原始字节 69967。56 个纯接收/台账文件按 **base64 无损**归档（NUL 与 CR 全保留，
每节自带"解码后 SHA-256 == 原件 SHA-256"自检）；3 个含机器路径/探针 UID 的文件
（`serial/01_reset_pyocd.txt`、`cp4_run.ps1`、`cp4_judge.py`）改存**替换后的可读文本**并显式声明
它不再是原字节的无损编码。替换规则逐条列在该文件开头（只描述形状、不把字面量写进公开文件）。
原件与原件 SHA 全部留在本地。提交前泄露扫描 `hits=0`。

## 6. 下一步（不是本轮授权）

`reports/reviews/phase8-target-measurement-a0-export-r1/retest_candidate_plan.md` 是
**待审批方案**：候选命令表 + 采集门插在 dump 与 clear_records 之间的流程。
新镜像经独立软件与镜像复审后，云端再以**真实新散列**制作有界重测包；
未拿到该包及用户明确批准，不得提前烧录、追加 trip、重采样或恢复快照。

## 7. STOP

```
A0_EXPORT_R1_OFFLINE_READY / AWAITING_INDEPENDENT_SOFTWARE_AND_IMAGE_REVIEW
NO NEW HARDWARE ACTIONS / NO FLASH READ / NO PROGRAM / NO RESET / NO SERIAL
NO MOTOR / NO STEP_ENABLE / FOUR SAFETY MACROS RT_FALSE
```
