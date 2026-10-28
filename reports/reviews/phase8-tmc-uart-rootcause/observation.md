# TMC UART 根因轮 — 结论与修复（2026-10-28）

分支 `debug/tmc-uart-rootcause-20261028`（基线 `1696c59`）。最终正常应用已在板运行。
本轮未写 TMC 配置（临时 GCONF 实验已撤回）、未动电机/供电/线路、未输出 STEP/DIR/HIGH。

## 1. 根因（已由板上 A/B 证明）

预算发送叶子 `drv_uart_txleaf.inc` 在 TDRE 未就绪时调用 **`rt_thread_mdelay(1)`**：
4 字节请求因此被拉成 **~1 ms 的字节间隔**（整帧 `tx_off=3 ms`）。TMC2209 的 UART
按连续帧解析，遇到这种帧内长空闲会**丢弃整帧**——所以 MCU 能看到自身 4 字节回声，
却永远收不到 8 字节应答（旧日志里全部表现为 `rx-timeout-echo` / -117）。

证据链：
1. 同板同模块烧 **e40a9b9**（历史已验证版本）：`tmc_regs` 7 个寄存器全部读出，
   `IOIN=0x21000041` ⇒ 模块与线路正常（`e40_tmc_regs.txt`）。
2. 当前固件：请求 `05 00 06 6F`、CRC 正确、`tx_written=4`、回声完整，但 `hdr=0`；
   `tmc_scan` 0..3 全无应答。
3. A/B 把 `tmc_tx_frame()` 临时换回 `rt_device_write`（e40 路径）：启动自检立即读出
   `[SS] [REQ OK] TMC2209 (IOIN=0x21000041)`（`probe_ab_rtdev.txt`）。
4. 修叶子为**只按 tick 检查预算的短自旋**（不再 mdelay，帧内保持连续）后：
   `tmc_uart_probe` 三读全 SUCCESS、`crc=OK`、`tx_off=0`、`IOIN=0x21000041`
   （`probe_fix1.txt`）。

另一个被 A/B 排除的假设：`GCONF.pdn_disable`。经用户单独授权补写过一帧该位，
仍 echo-only；e40 实测 `GCONF=0x00000101`（pdn_disable=0）却能正常读 ⇒ 该位不是本故障原因。
最终固件已撤回这帧写，保持只读。

## 2. 第二处真实缺陷：motor 线程栈溢出

TMC 读通后，正常应用继续走到更深路径，`thread:motor stack overflow` 在启动约 30 s 后
真实出现（768 B 栈）。电机线程会调用 `tmc2209_config_integrity_check_for()`（含 TMC 读与
较大局部结构）。将 `MOTOR_THREAD_STACK` 768 → **2048**；修后实测高水位 **728 B**、
canary `0x23`、当前 SP 距底 1828 B。sensor 栈 4096、高水位 900 B、canary 完好。
未删除告警、未停 sensor。

## 3. 最终板上验证（正常应用，非最小例程）

| 项 | 结果 |
|---|---|
| 镜像 | AXF 1693876 B / `a66673f0…`；measure-a0.bin 268372 B / `c635d1d1…` |
| 烧录/回读 | pyOCD + 厂商 pack，Erase/Program/Verify；读回与 BIN **逐字节相等** |
| 启动 | `SELF_TEST -> READY`，自检 `TMC2209 (IOIN=0x21000041)`，`[TMC] OK` |
| 后台 | sensor 100 Hz，`valid_sg=1`，`tmc_health=OK` |
| 300 读 | 100 组 IOIN/GSTAT/IFCNT（3 读/组）：**300/300 SUCCESS**，全部 `crc=OK`，每组 `IOIN=0x21000041` |
| 3 次重启 | 34 + 33 + 33 组，分属 3 次受控复位；每次重启自检再次初始化成功 |
| 失败/重试 | 0 个 -117、0 个 -23、0 个 CRC/身份错误；无自动重试 |
| 栈 | motor 2048 / 高水位 728；sensor 4096 / 高水位 900；两次采样无 overflow 文本 |

原始件：`stress_ms_boot1/2/3.txt`（含每组完整 TMRO 行）、`boot_ms2048.txt`、
`probe_fix1.txt`、`probe_ab_rtdev.txt`、`e40_tmc_regs.txt`。

## 4. 临时实验与已撤回内容

- 诊断镜像 `TMC_RC_DIAG_NO_SG=1`（停周期 SG 读）——已删除。
- `GCONF.pdn_disable=1` 一次性写——已删除（假设被 A/B 否定）。
- `tmc_tx_frame` 临时改走 `rt_device_write`——已恢复为预算叶子（修复后的版本）。
- 主机叶子夹具：生产等待环由 mdelay 改为自旋后，夹具的时间模型改由
  `ns800_leaf_uart_poll()` 在轮询时推进 1 ms（C17/C18 13/13 PASS）。

## 5. 安全边界与状态

电机未接、SW1 阻断；四个安全宏无目标级覆盖（沿用 R2 的 `-dM` 实证，本轮未改）；
未动 ENN/STEP/DIR/许可 HIGH；未清故障、未 selftest 主动注入、未写 TMC 配置。
板上最终态：**运行中，state=READY，motion gate-locked**；TMC drive_cfg=UNCONFIGURED，
`safe_for_enable=false`，A0 时长仍暂缓。
