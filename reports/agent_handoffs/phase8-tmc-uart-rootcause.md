# TMC UART 根因轮交接 — 查因、修复、烧录与正常应用验证

任务书：`NS800_TMC_Autonomous_Debug_Fix_and_Flash_Task.md`（18081 B / `7848c373…`）
分支：`debug/tmc-uart-rootcause-20261028`（基线 `1696c59`，**未合并 main**）
代码提交：`c2d29d99e81d23ac3451480eb6f5bade5f8b5981`
最终镜像：AXF `a66673f0…`（1693876 B）/ measure-a0.bin `c635d1d1…`（268372 B），横幅 `5.3.0 build Oct 28 2026 17:36:54`

**一句话结论**：根因是预算 TX 叶子在字节间调用 `rt_thread_mdelay(1)`，把 4 字节请求拉成约 1 ms
字节间隔，TMC2209 丢弃整帧（MCU 只看到自身回声）。改为按 tick 检查预算的短自旋后，读链恢复；
最终正常应用在 3 次重启中完成 **300/300** 次 IOIN/GSTAT/IFCNT 读，无 -117/-23/CRC。
TMC 读通后暴露的 motor 线程 768 B 栈溢出也一并修复（2048 B，实测高水位 728 B）。

## 1. 因果链（板上 A/B）

| 步骤 | 实验 | 结果 |
|---|---|---|
| 1 | 当前固件 `tmc_uart_probe` | `req=05 00 06 6F`、CRC 正确、`tx_written=4`、`echo=ECHO_ONLY`、无应答、-117 |
| 2 | 同板烧 e40a9b9（历史已验证） | `tmc_regs` 7/7 读出，`IOIN=0x21000041` ⇒ 模块/线路正常 |
| 3 | A/B：`tmc_tx_frame` 换回 `rt_device_write` | 启动自检即读通 `TMC2209 (IOIN=0x21000041)` |
| 4 | 修叶子（去 mdelay，短自旋） | 三读全 SUCCESS、`crc=OK`、`tx_off=0`、`IOIN=0x21000041` |

关键量化：带 mdelay 的帧 `tx_off=3 ms`（3 个 1 ms 间隙）；短自旋后 `tx_off=0`。
被 A/B 排除的假设：`GCONF.pdn_disable`（经用户授权补写后仍 echo-only；e40 的 GCONF=0x101、
该位为 0 也能读）。最终固件撤回该写，保持只读。

## 2. 修复内容

1. `libraries/HAL_Drivers/drivers/drv_uart_txleaf.inc`
   - 删除字节间 `rt_thread_mdelay(1)`；改为只按 `rt_tick_get()` 检查预算的短自旋。
   - 预算语义不变（总预算/阶段上限仍在此强制执行）；帧内字节保持连续。
2. `applications/motor.c`
   - `MOTOR_THREAD_STACK` 768 → 2048。TMC 读通后正常应用走到 integrity 路径时真实溢出；
     修后高水位 728 B、canary `0x23`。
3. `tests/host/{leaf_main.c,shim_leaf/drv_common.h}`
   - 生产等待环改为自旋后，夹具在轮询无空位时推进 1 ms 模型时间（`ns800_leaf_uart_poll`）。
   - C17 7/7 + C18 6/6 PASS，runner rc=0。

## 3. 最终验证（正常应用）

- 启动：`SELF_TEST -> READY`；自检 `TMC2209 (IOIN=0x21000041)`；`[TMC] OK`。
- 后台：sensor 100 Hz，`valid_sg=1`，`tmc_health=OK`。
- **300 读**：100 组 × (IOIN/GSTAT/IFCNT) = 300 次，**300/300 SUCCESS**；
  每组 `IOIN=0x21000041`，全部 `crc=OK`；0 个 -117/-23/CRC/身份错误；无重试。
- **3 次受控复位**：34 + 33 + 33 组，分属 3 次启动；每次启动自检重新初始化成功。
- 栈：motor 2048/高水位 728/canary 0x23；sensor 4096/高水位 900/canary 0x23；
  全程无 `stack overflow` 或 `close to end of stack` 文本。
- 镜像：pyOCD + 厂商 pack 烧录（sector erase）；`0x08000000` 起 268372 B 回读与 BIN 逐字节相等。

原始件：`reports/reviews/phase8-tmc-uart-rootcause/`
（base64 原样保存，含 CR：`stress_ms_boot1/2/3.bin.b64`、`boot_ms2048.txt`、`probe_fix1.txt`、`probe_ab_rtdev.txt`、`e40_tmc_regs.txt`）。

## 4. 首失败与保留

1. `GCONF.pdn_disable=1` 假设：授权补写后仍 echo-only（已撤回，不掩盖）。
2. motor 线程 768 B 栈溢出：TMC 修通后真实出现（已修，未删告警/未停 sensor）。
3. UV4 `-f` 在本机报 `Internal DLL Error`（pyOCD 同探针正常）；改用 pyOCD + 厂商 pack，
   并以整段回读证明镜像。后续候选均按此路径。
4. 主机叶子链首次因模型时间不前进而超时；更新夹具轮询时间模型后 13/13 PASS。
5. 本包早期的“无应答=模块坏/PDN”判断被 e40 A/B 否定，记录在此。

## 5. 安全边界与最终状态

电机未接、SW1 阻断；四个安全宏无目标级覆盖；未 arm/STEP/DIR/HIGH、未清故障、
未做 TMC 配置写。板上最终态：**运行中，state=READY，motion gate-locked**；
TMC `drive_cfg=UNCONFIGURED`，`safe_for_enable=false`；A0 时长仍暂缓。
未合并 main/phase7/tag，未改全局 Git 配置。

## 后续指针（2026-10-28）

本轮的电机链开发与源码/镜像收口见 `reports/agent_handoffs/phase8-motor-chain-dev.md`；
本文件的历史镜像、300 读日志与旧 manifest 保留不改，旧 manifest 的 code_commit 声明已在新交接中失效说明。
