# M8-R7F1 产品 C 修复 · 审核交接（离线，设备动作 0）

日期：2026-11-03。基线 `ed8d7f4`（feature/m8-sg-adc-relative-protection）。
任务书：`NS800_R7F1_A_Startup_C_Fix_20261103.md` §1–§6 全部按原文完成，无越界。

**本文件不含硬件批准，不代替操作者回答现场安全门；本轮设备动作 0。**

## 1. 改了什么（产品 C，7 个文件）

- **逐调用失败原因**（§2）：`tmc2209.h/.c` 新增 `tmc_xfer_stat_t`（48 B：rc、reason、tx_written、rx_bytes、
  flush_bytes、used、ifcnt 前后与是否校验）+ `tmc2209_read_register_stat()`；
  `tmc2209_read_register()` 变成传 `RT_NULL` 的薄封装，其它调用方行为不变。
  reason 直接复用既有传输状态出口（`TMC_XFER_OK/NO_DEVICE/BAD_CONTEXT/BUDGET_EXHAUSTED/LOCK_BUSY/RX_FLOOD/`
  `TX_FAILED/RX_TIMEOUT_ECHO/RX_TIMEOUT_PART/RX_CRC/NOT_ARRIVED/STALE`），没有共享"最后一次错误"。
  启动自检必需项④的 IOIN 读改走带状态接口，失败时在传输锁释放后的普通线程上下文打两行：
  `[SS] [REQ FAIL] TMC2209 IOIN rc=%d reason=%s` + `[SS] [REQ FAIL] TMC2209 IOIN tx=%u rx=%u flush=%u used=%u`。
- **启动顺序**（§3）：`sensor_service.c` 的 SG 分频分支加 `&& !s_sg_startup_gated`（默认 1=关闭），
  只让 SG 这一路在 stage 7→11 这段窗口不发 UART 读；关闭期的帧走既有"非分频帧"分支
  （`fresh_sg=0`，值与 `valid_sg` 继承基帧，`sg_seq/sg_tick` 不推进），不新增第三种状态。
  `supervisor.c` 在 `safety_startup_selftest()` 返回之后释放门一次（PASS 与 FAIL 都释放，不永久禁用 SG）。
  epoch/锁存规则、真读失败/身份不符仍判自检失败，全部未动；没强行 READY、没忽略 EBUSY、没重试、没放大超时。
- **三处已证截断的打印**（§4）：`motor.c` `[MOT] init OK` 一行→两行；`safety_state.c` `[SS] STEP:` →3 行（成功分支）
  /4 行（故障分支）；`[SS] ==> FAULT_LATCHED` →4 行。`RT_CONSOLEBUF_SIZE=128` 未改，未重写全仓打印。
  同一条 `if/else` 里那对 `!! STEP 关断未确认` 也一并拆了（拆 STEP 必然触及它的兄弟分支）。
  登记未改：`[MOT]   注: STEP=api-accepted…` 运行时实测 126 B，在 127 线内但丢换行，本轮不在任务书三条之列，不改。

测试侧：`tests/host/leaf_main.c` 新增 `tmc_boot` 组 BTB01–BTB10、`tests/host/sp_conc_main.c` 新增 `boot_print`
组 BPT01–BPT05 与一套 128/127/126 的控制台裁剪量具（复刻 `kservice.c:405-421` 的裁剪口径）、
`sd_main.c` 新增 SD13a/b/c、`ps_main.c`/`io_main.c` 补两个诚实的假总线 stub、runner 台账计数重算。

## 2. 验证到哪里（离线，全绿；边界照实说）

| 判据 | 结果 |
|---|---|
| S2 全链（22 组 × target/host_ovr） | `S2 RUNNER RESULT: PASS (rc=0)`，必需用例 target=68 / host_ovr=143 全真执行，新两组无 SKIP |
| SD / IO / S1 / M8 / V1V2 | 全部 `PASS (rc=0)`（SD 25+24、IO 4 变体、S1 两变体、M8 主机链、V1/V2） |
| 启动竞争形状 | BTB01 rc=-23 且线上 0 B、设备读请求 0 次；BTB03 同负载下前台读成功 → 同一笔读只差"后台有没有持锁" |
| reason 分流 | BTB06 lock-busy(tx=0) / BTB07 bad-context(同为 -23) / BTB08 tx-failed / BTB09 rx-timeout-echo(tx=4) / BTB10 两次调用互不继承 |
| 门语义 | SD13a 关闭期 25 帧 SG UART 调用=0 而 imu/adc/snap 各 25；SD13b 关闭帧继承 `valid_sg/sg_seq` 且 `fresh=0`；SD13c 释放后恢复分频读 |
| 打印宽度（静态） | `LINE_WIDTH_RESULT PASS (bad=0)`，15 站点按真实 UTF-8 实参上界；`--selfcheck` 里 4 条修前原样（最坏 139/207/221/326 B）全部命中 |
| 打印宽度（动态） | BPT01 58/95 B；BPT02 STEP 57/86/83 + FAULT_LATCHED 56/63/44/42；BPT03 故障分支 58/85/49/94；BPT04 归因两行 66/54；BPT05 裁剪模型元负控 fired |
| 构建 | `0 Error(s), 0 Warning(s)`；BIN 292628 B `e5b51b6b810f215e…`；Code+272 RO+192 RW+4 ZI+4，与 BIN +464 对账；RW+4 唯一来源 `.data.s_sg_startup_gated` |
| 栈深 | 自检子树 1220 B（已含新帧，实测 `SUB sp,sp,#0x30`=48 B）；main 栈瓶颈在 `supervisor_boot` 的 SPI 线 2020 B（本轮一行未改，也无修前对照数） |

**验证边界（不得当成已证）**：
1. 现场那一次 IOIN 到底落在哪个 reason —— 未测，要下一次开机看新的两行。
2. 夹具证的是"若后台先持锁，前台必 -23 且 0 字节"，不是"板上确实这样发生过"。
3. `msh system_selftest` 重入不受门约束（头文件注释已登记），该路径未覆盖。
4. sensor 线程若在 stage 7 之后、11 之前已在半帧中途，门只挡下一次分频读，在途那笔仍可能持锁 —— 本轮未处理。
5. 阈值、ACTIVE、运动授权、带载保护、IMU/SG 相对判定的现场有效性：全部未验证，本轮一律未触碰。
6. 新镜像未在任何硬件上验证过；板上仍是 R6A 的 `5ae4110d…`，本轮没下载。

## 3. 下一次现场要看什么（有明确上限，等 A 按这枚固件身份安排）

绑定对象必须是本轮新镜像：BIN 292628 B `e5b51b6b810f215e284ea6317a4b1bef6b3faddc6f1ac9f8947804f261fa5732`、
AXF 1775784 B `96bfe5cdbed9d4c773c96a3e190e5dda819ae2f38340e5f1c17750975b5986eb`、
MAP 2114770 B `6138de0593df9a18c5902b7e5014918f8db2aa02fdf5bad516d4aef94efb0194`。

看三件事，就这三件：

1. **启动自检的具体分支**：若仍失败，直接读新增的两行 `[SS] [REQ FAIL] TMC2209 IOIN rc=… reason=…` 与
   `tx=… rx=… flush=… used=…`。判读表：`reason=lock-busy` 且 `tx=0` = 还没发出去就被锁挡（启动顺序类）；
   `reason=bad-context` = 上下文错（不该出现在 stage 11，出现即为新问题）；`tx=4 …` + `rx-timeout-*` =
   发出去了没应答（通信/器件类，与上一场的 -23 不同因）；`reason=ok` 但仍 `[REQ FAIL] … VERSION mismatch` =
   身份不符（传输通了、芯片不对）。
2. **是否进 READY**：`safety_status` 期望 READY/fault=0；`[BOOT] 11` 之后门应已释放。
   仍 FAULT_LATCHED 就按第 1 条的 reason 定分支，不再靠"再等 75 秒"。
3. **三处打印是否完整**：`[MOT] init OK` 两行、`[SS] STEP:`（或 `!! STEP`）三/四条、`[SS] ==> FAULT_LATCHED`
   四条，每条以换行收尾、无 NUL、无半截 UTF-8。这一步同时是对"126 B + NUL、丢换行"那个机制的现场反证。

本阶段无需 arm/start/学习/保存/清日志，不需要先断电或 fault_reset。上一场批准的 download/readback/reset
额度已用尽，剩余 port_open/apply 不构成新一轮完整验证额度。

## 4. 可复算来源

- 构建：`reports/reviews/phase8-m8-r7f1-startup-c-fix/build/rt-thread-motor-r7f1.log`（CRLF 原件）
- 栈深：`…/build/stack_callgraph_r7f1.txt`（数字源自入库的 `…/rt-thread-motor-m1-callgraph.htm` 与两版 map）
- 镜像：`reports/firmware/phase8-m8-r7f1-startup-c-fix/`（BIN/AXF/MAP/htm/build_manifest.json）
- 主机链：`reports/reviews/phase8-m8-r7f1-startup-c-fix/host/`（`*_after.txt` 定稿 + `iterations/` 中途失败原文）
- 检查器：`host/line_width_check.py`（R6 同判据函数的拷贝，R6 原件散列未动）
- 执行记录：`reports/reviews/phase8-m8-r7f1-startup-c-fix/run_record.md`
