# NS800 M7 P0 仅串口续测轮交接（第二次如实停止）

任务 `phase8-motor-m7-p0-serial-continuation`，2026-10-30。
离线起点 `24ab5a7c1cff259b209f78fcb1abbb5f48271581`；本轮脚本提交 `1d13ea58df7f1bf9138dc2adf5fd56a2d479e2e5`。
输入包 `NS800_P0_Board_Review_Serial_Continuation_20261030.zip`（60080 B /
`d8add10e515d2781117bde3c3c3d443f153fa7216241d7fde23afad2b00aaa7b`）。
批准 = 用户"批准 P0 仅串口续测"（§5.5 范围：一次 COM5、七次固定发送、四个 DMM 读数；
不烧录/不读 Flash/不连 DAP/不复位/不转电机/不改配置；失败即停不重试）。

## 0. 一句话

续测模式本身工作正常：证明件绑定通过、静默挂载允许、唯一一次 COM5 打开成功、
第一条查询写入并有回包；但回包的**业务字段中段字节被替换**，采集器按合同拒绝接受，
12 s 超时停止。`BOOT_IDENTITY_GATE_FAILED` 之后停点向深处走了一层，
现在是 `FIRST_FIELD_GATE_BYTE_CORRUPTION`。**仍未取得电压数据，四个 DMM 读数一个都没发生。**

## 1. 角色提交

| 角色 | 值 |
|---|---|
| `firmware_code_commit` | `5b564eb962e8518920f581111d7a460a2b1fce64`（未重编） |
| `prior_board_commit`（证明绑定） | `24ab5a7c1cff259b209f78fcb1abbb5f48271581` |
| `script_code_commit` | `1d13ea58df7f1bf9138dc2adf5fd56a2d479e2e5`（采集器 `0e02ccfc…`、验证器 `2cf87b1d…`） |
| `delivery_head` | 本证据提交，解析规则 `git log -1 --format=%H -- reports/agent_handoffs/phase8-motor-m7-p0-serial-continuation.json`，不自引用 |

## 2. 离线段（设备动作 0，已完成）

- 补丁 `candidate/continuation.patch`（13482 B / `f6b69d20…b68f`，LF）在仓外规范 LF 字节上
  `git -c core.autocrlf=false apply --check` 一次通过再 apply；结果与包内完整文件
  `cmp` 逐字节相等；**只改这两个脚本**，计算器仍 `5c7a2340…63ad`。
- Windows 交付机用索引 blob 实跑：`--selftest` **37/37 rc=0 stderr 0 B**；
  `--selftest-continuation`（喂固定 `24ab5a7` 导出的证明件）**11/11 rc=0**，
  `NO_PYOCD_SUBPROCESS / DEVICE_ACTIONS=0`。
- 推送后远端 blob 复算两脚本 = `0e02ccfc…5467` / `2cf87b1d…a8c7`；
  证明件与旧失败原件在远端未变（`e8df121f…4246` / `46b53e93…dd46` / `run_evidence.md`）；
  `reports/firmware/`、`rt-thread-master/`、`tests/` 在 `24ab5a7 → 1d13ea5` 区间变更 0 路径；
  四项冻结引用不变。

## 3. 现场段：新事实

同一签名、第二个独立流。上一轮在启动流，本轮在**静默期单条查询响应**：

```text
实到 b'safety_state\r\nMMu\x81st\xd1\x95\xf5READY fault_code=0 degraded=0x00\r\nmsh >'   62 B
     头 14 B（回显）完好，尾 39 B（含帧尾 msh >）完好
     中段应为 '[SS] state=' 11 B (5b 53 53 5d 20 73 74 61 74 65 3d)
     实到 9 B (4d 4d 75 81 73 74 d1 95 f5)，4 个非 ASCII
```

同一块板、同一条命令在仓库既存原件里是**逐字节干净**的
（`…/m5-fault-log/cp1/final_pass/016_safety_state.bin` = 6a59468a…dacd）。
上一轮 SWD 定长回读 266244 B 逐字节相等，所以问题不在那条通路。

因此：
- "只是开机阶段并发写控制台"被**削弱但未否掉**（无法从 PC 侧字节区分"别的线程打印过"）；
- "目标 UART / DAPLink CDC / PC 读取路径按字节出错或丢失"**未排除**，
  且目前最强的相关性是**两次污染都由本 Python 采集器采到**，历史干净取样来自 PowerShell 链；
- 本轮**不定根因**，`CONSOLE_STREAM_INTEGRITY_UNRESOLVED` 继续开放。

判开需要新批准，本轮一条都没做：同一静默时刻用历史 PowerShell 采集链对 `safety_state`
再取一次比对；或做去掉每块 fsync 的采集器对照（后者属判据变更，须复审）。

## 4. 采集器合同表现：这次停点不是它的缺陷

身份依据如实记 `PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY` 且 `fresh_banner_required=false`；
"本轮没有重新核对横幅"照此登记。静默没被当成设备故障；`STARTUP_BYTES_DURING_ATTACH` 未触发；
原字节先落盘（62 B 完整保住）；字段被污染时**拒绝放行**，没有拿"看到 READY"当通过；
异常出口仍写 `PORT_CLOSED`。我不改候选字节、不放松判据。

## 5. 上一轮两条登记的作废（写在这里，旧原件不改）

- **"DAP 会话 2/3"作废**：实际有三次工具启动尝试（下载、回读、内部 reset 子进程）；
  reset 子进程是否建会话、退出码为何均未观测（两个复位输出件 0 字节，读流先超时使
  `p.wait` 分支未执行），记 `UNKNOWN`；不得宣称还剩 DAP 额度。
- **"台账 6 行"更正**：`24ab5a7` 那轮实为 5 行（本轮续测台账是 6 行，属不同运行，别混）。
  干净启动文本里的 `fault_code=0` 只是 `BOOT_TEXT_ONLY`，不能替代现时查询——
  本轮正是靠现时查询把它判成了不可接受，这条纪律有效。

## 6. 未闭合

- P0 电压观察仍未取得；`current_cal sample 128` 从未在板上真实运行（M7 采样链 `UNPROVEN_ON_BOARD`）。
- 控制台/串口流完整性根因未定；它现在同时影响横幅与业务字段两条判据路径。
- 栈告警充分性、`tshell` 栈高水位仍 `NOT_MEASURED`；本轮无危险标记只是一次覆盖。
- 独立母线电流参考、真实 DIAG/SG 端到端、最坏停机时延、整机安全验收全部开放。
- P1 `METHOD_NOT_READY`、P2 `NOT_AUTHORIZED`。
- 两次批准的额度都已消耗（下载/回读/复位那次，以及本轮 COM5 那次）。

## 7. 回执

```text
P0_SERIAL_CONTINUATION_EXECUTED / FIRST_FIELD_GATE_BYTE_CORRUPTION / TX_ATTEMPTS=1 ACCEPTED=0
CONTINUATION_IDENTITY_BASIS_USED / PRIOR_READBACK_PLUS_CONFIRMED_CONTINUITY fresh_banner_required=false
NO_VOLTAGE_OBSERVATION_OBTAINED / DMM_READINGS_TAKEN=0 / RESULT_JSON_ABSENT
COM5_OPENS=1 DOWNLOADS=0 TARGET_FLASH_READS=0 DAP_ACTIONS=0 ACTIVE_RESETS=0 MOTOR_ACTIONS=0 RETRIES=0
CONSOLE_STREAM_INTEGRITY_UNRESOLVED / APPROVAL_QUOTA_CONSUMED / ROOT_CAUSE_NOT_DETERMINED
```

下一轮不自行取任何额度。要推进 P0，需要先判开"字节污染来自哪一段"，
否则任何现场重跑都可能再次停在同一条判据上。

## 8. 第二次续测（用户已把 USB 线换回原口）追加登记

用户报告曾更换 USB 口（仍枚举为 COM5），随后换回原口并授权再试一次。同字节采集器、同证明件、
同 `readback.bin`，输出目录 `P0_try2`。结果**形状完全相同**：

```text
P0_FAILED:Stop:REPLY_TIMEOUT:01_safety_state.rx.bin          rc=1
attach 0 B（静默同样允许）
61 B / 54ee1e7e…0cb8
b'safety_state\r\n[\xaa\x05\x9aate=READY fault_code=0 degraded=0x00\r\nmsh >'
  '[SS] state=' 的 11 B 变成 8 B：5b aa 05 9a 61 74 65 3d
  首字节 '[' 正确、'ate=' 起正确、行尾含帧末提示符全部正确，只有中间 3 B 是垃圾
```

判定：

- **换 USB 口不是原因**（原口照样坏，且每次坏的位置与内容都不同 ⇒ 非确定性）。
- **"一整条别的文本被插进来"这一版控制台竞争被削弱**：垃圾段含 `0x05 / 0x02 / 0x09`，
  这些既不是 ASCII 文本也不是 UTF‑8 序列的成分；`91 81`、`91 95` 两对在冻结固件的全部打印
  字符串里 **0 命中**（本次检索见 `port_swap_and_three_capture_analysis.txt`）。
- **仍与形状最吻合但未证明**的机制还是固件侧那一条：单个共享 `static rt_log_buf[128]` +
  逐字符 `_serial_poll_tx` + 保护宏为空——行首已发出的字节保留，随后读到别人写进缓冲区前段的
  碎片，碎片用尽后又读回本行剩余字节，并且长度会变短。要判开必须受控干预，不能再靠重跑投票。
- **可操作的强结论**：这个签名在的时候，**任何靠控制台文本做的读数判定都不可信**。
  采集脚本改不动它，换 USB 口也绕不过它。

额度：用户第二次授权给出的是第二次仅串口额度，同样耗尽（COM5 1/1、发送尝试 1、接受 0、
DMM 0/4、下载/DAP/复位/电机/配置写 0、重试 0）。板子未写入任何东西，串口已正常关闭；
`e614a075…f7da` 在板的既有证明不受影响。P0 电压观察仍未取得。
