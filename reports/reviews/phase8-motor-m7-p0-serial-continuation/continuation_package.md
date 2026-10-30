# M7 P0 仅串口续测包（绑定真实字节，2026-10-30）

复审对象 `24ab5a7c1cff259b209f78fcb1abbb5f48271581`；输入包
`NS800_P0_Board_Review_Serial_Continuation_20261030.zip`（60080 B /
`d8add10e515d2781117bde3c3c3d443f153fa7216241d7fde23afad2b00aaa7b`）。
任务 `phase8-motor-m7-p0-serial-continuation`。

**身份依据已换，但不等于"版本已重新证明"**：本包不要求新横幅，
`PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY / fresh_banner_required=false` 会写进台账；
"本次没有重新核对启动横幅"必须如实登记（见 §5）。

## 1. 绑定输入

| 对象 | 字节 | SHA-256 |
|---|---:|---|
| 采集器（续测版） | 23374 | `0e02ccfcef178f0ceee9dc3fd5aeb17f7b9a3bb924e4b83bd7a0cecde9505467` |
| 验证器（含 11 项续测） | 21742 | `2cf87b1dad0c57be3ea0b57c63269fce648093a301766400976b2b1186cfa8c7` |
| 计算器（未改，仅解析器） | 54267 | `5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad` |
| 载荷 BIN | 266244 | `e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da` |
| 载荷 AXF | 1688696 | `2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584` |
| 上一轮回读件（现场不再读 Flash） | 266244 | 同上 BIN（`cmp` 已证逐字节相等） |
| 证明件 board-proof.json @24ab5a7 | 10116 | `e8df121f3f58af5fadf3d60be24fd8aaf7d88c381ae740be86173c43e6604246` |
| 证明件 boot.b64 @24ab5a7 | 4508 | `46b53e93137a7a4f90ecd16ea73bf70435bc14514e323032d04d0665c478dd46` → 解回 3381 B / `bc47dc02…14fa` |

固件输入仍 `5b564eb962e8518920f581111d7a460a2b1fce64`；板上镜像仍 `e614a075…f7da`（上一轮已回读证明）。

## 2. 额度（新批准，不结转上一轮）

| 动作 | 上限 |
|---|---:|
| COM5/115200/8N1 打开尝试 | 1（失败也消耗） |
| 下载 / 目标 Flash 读 / DAP / pyOCD 子进程 / 主动复位 / 电机动作 / 配置写 / 清故障 | **全部 0** |
| `safety_state` / `safety_status` / `motor_status` / `step_status` | 各 1 |
| `current_cal show` | 2 |
| `current_cal sample 128` | 1 |
| UT39A+ 电压读数 | 4（H10-1、H10-3 各前后一次） |

上一轮没发出的 0/7 不结转；上一轮所谓的"剩余 DAP 额度"不成立（见 §4）。

## 3. 顺序与判据（采集器硬编码，Agent 不另发命令）

1. 先离线绑定：BIN/AXF/回读三者散列与逐字节相等、计算器散列、证明件 JSON 与启动流散列，
   并检查 `[MAIN] boot complete` 与 `Oct 29 2026 23:11:52` 后缀确在旧流中——**全在打开串口之前**。
2. 打开 COM5（唯一一次），进入 **2 秒被动窗口**：不要求有字节、不要求静默、不发空行、不复位。
   原字节先落盘 `raw_attach.bin` 再判定；含危险词立即停；
   若出现 `[MAIN] boot complete` / `build Oct ` / `[BOOT]` 即 `STARTUP_BYTES_DURING_ATTACH` 停止，
   不擅自解释成"重启"或"缓存旧字节"。
3. 静止/禁能门（全部用本次现时响应，不用旧 boot 文本）：READY/fault0；state0/armed0/cur=tgt0；
   许可 LOW/pad0/ENN 禁能侧；Motor 与诊断无未知关断。`UNCONFIGURED`/`IRQ CLOSED` 照实记录。
4. 四条状态查询 → `WAIT_DMM_BEFORE` → show → sample128 → show → `WAIT_DMM_AFTER`。
   两次 show 的系数/来源/版本必须一致，样本必须被已接受的六行解析器接受且 n=128。
5. 人工读数按 `dmm_before.json` / `dmm_after.json` 原子握手，不预建、不补造，
   时刻未记就 `UNKNOWN_TIME`；无 before 不采样，无 after 不出结果。
6. `show` 若打印 `ma_uA=N/A (no valid fresh frame)` 或 `valid/fresh≠1`，按业务数据前置失败停，
   不称串口故障、不重试。

成功 = rc 0 + 台账 `COMPLETE` + `result.json` 的 `VOLTAGE_OBSERVATION_ONLY` 三者同时成立。
这仍不是电流标定，不是 ADC 精度验收，不产 `MEASURED` 系数，不改保护阈值，不外推相电流 RMS。

## 4. 上一轮两条登记的作废（旧原件不改写）

- **"DAP 会话 2/3"作废**：实际有**三次**工具启动尝试（下载、回读、内部 reset 子进程）。
  reset 子进程是否建立了 DAP 会话、其退出码为何，都**未单独观测**（两个复位输出件均 0 字节，
  读流先超时导致 `p.wait` 分支未执行），记 `UNKNOWN`。因此**不得宣称还剩一次可用 DAP 额度**，
  本包也就此把 DAP 类动作全部定为 0。
- **"台账 6 行"更正为 5 行**：`BIND / PORT_OPEN_ATTEMPT / PORT_OPEN / RESET_ATTEMPT / PORT_CLOSED`。
  另外干净启动文本里出现的 `fault_code=0` 只是 `BOOT_TEXT_ONLY`，
  不能替代续测中必须真实发送的现时查询。

## 5. 本轮仍不声称的事

- 不声称启动横幅本轮被核对过（`new_banner_checked=false` 写在证明结构里）。
- 不声称 `CONSOLE_STREAM_INTEGRITY_UNRESOLVED` 已解决：续测成功也不关闭它，
  更不关闭独立电流标定、真实 DIAG/SG 端到端、最坏停机时延或整机验收。
- 不把两轮拼成"一轮全通过"：上一轮的失败原件与本次结果同时保留。
- 不改动任何 C/.h/HAL/内核/四宏/时钟引脚/预算/阈值/Keil 工程/镜像；不重编、不重跑未变的 C 测试链。
