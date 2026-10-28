# TMC UART 根因轮 — 首轮板上证据（2026-10-28）

分支 `debug/tmc-uart-rootcause-20261028`（起点 `1696c59`）。本轮：读代码、DAP 观察、一次临时诊断构建、
一次烧录并回读、COM5 实测。未写 TMC、未动电机/供电/线路。

## 已证事实（按证据强度）

1. **R2 正常应用下 TMC 读失败**：DAP 读 `tmc_health=DEGRADED`、`sensor_frame.valid_sg=0`；
   在 `sensor_thread_entry` 的 SG 调用返回点设硬件断点，连续 5 次 `r0=-117 (-RT_ETIMEOUT)`。
2. **请求帧成功发出**：预算发送叶子返回 `RT_EOK`，`tx_written=4`；串口观察里
   `req=05 00 06 6F` 与 CRC8(05 00 06)=0x6F 一致。
3. **回声正常、无应答**（隔离诊断镜像，sensor 停发 SG 读，`tmc_uart_probe`）：
   - `result=FAIL rc=-117 reason=rx-timeout-echo`
   - `lock=1/1/0`（拿到锁）、`tx=1/1/0`、`tx_written=4`
   - `rx=1 hdr=0 full=0`、`echo=ECHO_ONLY`、`rx1 n=4 hex=05 00 06 6F`
   ⇒ MCU 的 TX→外部线→RX 通路成立（收到自身 4 字节回声），但 TMC 没有任何 8 字节应答。
4. **地址 0..3 全部无应答**（`tmc_scan`）：`ADDR 0/1/2/3: no valid reply`；
   工具自身结论：`UART MCU TX/RX echo path OK`、`No TMC2209 response on addresses 0..3`。
5. **HAL 无回归**：`drv_uart.c` 与 e40a9b95 逐字 diff 仅为新增预算叶子；open/configure/ISR 未变。
   `uart_obj[1]` 即 uart2（`tmc_serial=0x2000ccfc`），UART2 基址/BAUD 与 UART1 同源。
6. **启动慢**：复位后前 ~5 s `s_seq=0`，CPU 曾停在 `SPI_transmitReceive`（`ns800_spi_xfer` 内，
   `while(RFDN==EMPTY)` 无超时），之后自行继续；未定因，但与 TMC 读无关。

## 诊断镜像（临时，不进入最终）

- `applications/sensor_service.c` 加 `TMC_RC_DIAG_NO_SG=1`，仅停掉周期 SG 读，其余后台保持。
- AXF 1693528 B / `380b2148…`；measure-a0.bin 268196 B / `27c0f14ac87f1e03c1d94e26a6edea5b20ab8b33d588367eea6b9a8a58409bac`
- 烧录：UV4 `-f` 在本机报 `Internal DLL Error`（pyOCD 同探针可连），改用 pyOCD 0.45.1 + 厂商 pack
  `NOVOSENSE.NS800RT7XXX.0.5.3.pack`，`-e sector`（非 chip/mass erase）。
- 回读 `0x08000000` 起 268288 B：前 268196 B 与新 BIN **逐字节相等**，尾部 92 B 为 `0xFF`（擦除态）。
- 启动横幅 `5.3.0 build Oct 28 2026 17:06:47`，boot 全流程到 msh。

## 当前判断

MCU 侧读链（帧、TX、回声、RX、解析、锁）已逐段证明；唯一缺口是 **TMC 芯片完全不应答**。
这不是 -23 上下文拒绝、不是 CRC/身份错误（无帧可验），也不是读代码的解析 bug。

## 阻断（需要授权外的物理/功率级动作）

在不写 TMC 配置、不改供电的授权边界内，无法再让芯片应答。最小必需动作（择一）：
1. 检查/恢复 TMC 模块的 VIO 与 VM 供电（并确认模块已插稳、PDN_UART 未被拉低）；
2. 对该模块做一次**目标性**断电重上电（不是整机例行关电），以清除可能的 PDN/锁死状态；
3. 若 VIO/VM 实测正常仍无应答，则是模块/线路硬件问题，需要换模块或查线。

## 文件

- `probe_r2_contended.txt`：R2 正常应用下 msh probe（锁被 sensor 占用，-23 lock-busy）
- `probe_diag_isolated.txt/.bin`：诊断镜像下的决定性观察（echo-only）
- `scan_diag.txt/.bin`：地址 0..3 扫描
- `boot_diag.txt`：诊断镜像复位启动的原始分块
