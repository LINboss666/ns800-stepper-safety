# NS800 电机 M1 后续现场包草案（REV/board_execution_candidate.md）—— 本轮一条也不执行

- 轮次：`phase8-motor-bringup-m1-prep`；输入锚 `ea519f29af24c6f9353de67b7bb4d9f60baf5e1a`；
  软件输入提交 `70416e7ffb9e704a7f6008b310c6223dbda3a669`（第三个 Target 的工程字节）。
- 本文件状态：**NOT_AUTHORIZED / NOT_EXECUTED**。本轮 `hardware_actions=[]`、`target_commands_executed=[]`，
  没有开过串口、没有连 DAP、没有读 Flash、没有复位、没有下载、没有发过任何目标命令。
- 任务书 §8 原话"本任务书本身不批准它们"同样适用于本文件：它只是把未来要执行的字面序列、
  判据与预算写清，供复审后由用户**一次批准具体范围**（可只批 M0）。
- 绑定的真实镜像散列（本轮实测字节）：

  | 用途 | 文件 | 字节 | SHA-256 |
  |---|---|---:|---|
  | 板上最后已证镜像 | measure-a0.bin（上一轮回读一致） | 261452 | `5f30d3f058ceff516b2185aa3a1e597bbe63293b94a6fd801fd989a6f7b2b8e6` |
  | M0 使用（不重烧） | 同上 | 261452 | `5f30d3f0…` |
  | 默认对照镜像 | `FW/default-control.bin` | 251148 | `7f9baed96ecb23cdf7cb9d1c0ccd4c28f2946bc9f3d720a560f7741cb0f4e45d` |
  | M2/M3 候选镜像 | `FW/motor-m1-candidate.bin` | 251260 | `a76b845db4ba1d8872f9db395c2e6e796bdb9416c398f010b669538006cf2777` |
  | 候选 AXF（仅本地，云端不可回读） | build/motor_m1/rt-thread-motor-m1.axf | 1645936 | `c1e9d4e78c424b3631b2d30cbe6061f32074b30c7518ed2aa681771d6600a86f` |
  | 候选 map（已入仓，原字节） | `FW/motor-m1-candidate.map` | 1948503 | `9f6d37203ca6226ef74323096d48fe5b6719300ab3cfec35f88bfc45833b586f` |

  候选镜像带 §3.4 登记的两项编译期宏例外（`MOTOR_HARDWARE_ENABLE_PATH_VALIDATED=1`、
  `TMC_DRIVE_CONFIG_HW_VALIDATED=1`），当前用途标记为
  `NOT_FOR_PROGRAMMING_PENDING_REVIEW_AND_MOTOR_DATA`。下载它需要单独一次的现场授权。
- 引用口径：`applications/…` 与 `rtconfig.h` 都是本轮 HEAD 的真实仓库内路径与行号，可直接复算。
  本文件写完后用一次性断言脚本逐行核对了全部 `file:line` 引用（129 项断言，mismatch=0；
  脚本为本地件 9400 B / `e7201959c802a610ca16d5aa1e45729491bf5df3eeff21b6ceb2b59466b69f56`，
  内含本机绝对路径故不入库）。核对过程中改掉的是**本文件的引用**而不是源码：
  `spm_ctr_pair` 调用行、`tmc_health_after_xfer` 行区间、GCONF 写入行、`done_valid` 赋值行、
  `fault_reset` 的取锁/归还锁行，各有一处初稿写错，已按实测行号更正。

## 0. 现场前置（M0/M2/M3 共同，缺一不进下一条）

1. **补齐电源终态**。当前登记值是 `board_current_power_state=UNKNOWN`（上一轮断电未回报），
   现场必须先实测并回报：24 V 是否通电、USB/DAPLink 是否插着、板上最后已证镜像是否仍是
   `5f30d3f0…`（无人另行烧录）。回报之前不假设"仍在运行"，也不假设"已断电"。
2. **电机物理未接**（M0 与 M2 全程）；模块与线束未做过任何改动；VREF 未被旋钮动过。
3. **SW1 处于阻断位**（T-01/T-02 仍 HOLD，`sw1_cut.ps1` 不在任何自动化里运行；
   SW1 的阻断→允许→收尾阻断只由操作者手工执行）。
4. **有人在场可立即断电**：断电顺序固定为**先切 24 V，再拔 USB**；禁止带电插拔电机、
   TMC 模块或调 VREF。
5. 原始收发一律**先落字节再解码**：保留 NUL/CR/回显/未知字节；stdout/stderr 以二进制另存
   （上一轮 CP3-F2 的教训：不得再用 `utf-8 errors=replace` 解码，那会不可逆地毁掉 GBK 错误文本）。
6. 提示符原则（已实证的结构性事实）：RT-Thread 的 `_MSH_PROMPT "msh "`（`shell.c:93`）+ `'>'`
   （`shell.c:122`）只在一条命令之后打印，启动流**不会**自发以 `msh >` 收尾
   （3757 B 真机启动字节里 `msh >` 出现 0 次，这是上一轮 BOARD-B CP3 具名停止的根因）。
   因此判据是"先等静默/启动完成，再用一次只读查询的响应尾取提示符"，
   不是"要求启动流以提示符结尾"。
7. 控制台行宽：`rtconfig.h:107` 的 `RT_CONSOLEBUF_SIZE=128` 会把 `rt_kprintf` 的渲染结果截到
   约 126 可见字符。本包里凡是可能超宽的行都在判据里**只断言前缀**，并明令
   **不得从截断日志里补出未出现的关键字段**（关键判据字段一律在行首）。
   已知可能超宽：`safety_status` 的 ENN 行（字面 149 B，`safety_gpio.c:450`）、
   `motor_status` 的门禁明细行（134/180 B，`motor.c:937-959`）、`step_status` 的说明行
   （153 B，`step_pwm.c:361`）、`fault_reset` 的结论行（138 B，`safety_state.c:957`）、
   `tmc_config_apply` 的成功行（284 B，`tmc2209.c:2031`）。
   例外：`spm` 的记录走 `spm_emitf`→640 B 缓冲 + `rt_kputs`（`sp_measure_a0.c:483-506`），
   不受该裁剪；超 640 B 时它自己打 `[spm] EXPORT_ERROR: line_overflow need=%d cap=%d`（`:490`）。

## 1. M0 —— 用板上现有镜像查通信，不重烧（7 条命令）

镜像 `5f30d3f0…`（四宏全 0）。M0 只做"能不能分"，不做任何写配置、不清故障。

预算（本轮草案上限，最终批准时逐字固定）：常规上电 ≤ 1 次；COM5（115200 8N1，用户提供的实际
授权参数，不作为隐私抹除）打开 ≤ 1 次；目标命令 ≤ 7 条；`tmc_uart_probe` ≤ 1 次；
Keil/下载/读 Flash/DAP/主动复位（含 SYSRESETREQ）/设备枚举/`tmc_scan` 一律 **0**。
停止规则：首条无响应、响应污染或取不到提示符 ⇒ 立即停；**不改波特率、不改地址、不空回车探测、
不自动重试**；probe 拿不到合法 IOIN 或 GSTAT/IFCNT 不完整 ⇒ 本分支结束，不进入写配置或清故障。

| # | 字面命令 | 目的 / 允许的副作用 | 通过判据（逐字，取自源码） | 失败/停止判据 |
|---:|---|---|---|---|
| 1 | `spm status` | 单次只读通信探针；只读计数器门控位（`:894` 调 `:135` 的 `spm_ctr_pair()`，内部经 `spm_port_demcr()`/`spm_port_ctr_ctrl()` 读，`:140`/`:143`），不加锁不改状态 | 一行 `[spm] status: state=%u entry=%s records=%u in_flight=%u dropped=%u counter=%s clock=%s guard=%u gates_now=0x%x`（`:888`），字段名与顺序逐字如此，`key=value` 单空格分隔；随后出现 `msh >` | 无该行、字段缺失、`entry=` 不是 `trip_fast|trip_for_operation|off_attempt|-` 之一（`:446-452`）、或响应里混有上一条的尾巴 ⇒ 停整包 |
| 2 | `spm cap` | schema/四宏/能力交叉核对；无总线流量 | 三行逐字：`[spm] schema=%u slots=%u record_bytes=%u array_bytes=%u sizeof_ctx=%u`（`:863`，schema 应为 2、slots 64）、`[spm] 编译四宏: MOTOR_HW=%d STEP_DIAG_ISO=%d TMC_CFG_HW=%d LIMIT_EN=%d …`（`:866`，四项必须全 0，否则镜像不是 `5f30d3f0…`）、`[spm] counter=%s clock_basis=%s …`（`:872`） | 任一宏读回非 0 ⇒ 镜像连续性存疑，停；本分支不重烧 |
| 3 | `safety_state` | 纯全局读 | `[SS] state=%s fault_code=%u degraded=0x%02X`（`safety_state.c:967`） | 行不出现或字段错位 ⇒ 停 |
| 4 | `safety_status` | 只 `rt_pin_read`（`safety_gpio.c:436`、`:452`），不写任何引脚 | 至少 `[SAFETY] ready=%s first_err=%d`（`:431`）+ `[SAFETY] MCU_DRV_ENABLE = %d (%s)`（`:436`）+ `[SAFETY] DRV_ENABLE last write+readback verified = %d (-1 = never verified)`（`:441`）；`request/pad/expected_FB/FB/verdict` 行（`:460`）**按前缀断言**（149 B 行会被裁） | `verdict` 不是 OK/NO_SAMPLE 词表内值 ⇒ 停并保留原始字节 |
| 5 | `motor_status` | 只读快照与掩码（`motor.c:2020`；`tmc2209_get_write_gate_mask` `tmc2209.c:2058` 注释"不产生总线流量"） | `[MOT] state=%d cur=%u tgt=%u accel=%u decel=%u dir=%u`（`:2293`）、`[MOT] armed=%d drv_en_verified_pad=%d health=%s`（`:2296`）、`[MOT] STEP stop: result=%s src=%s rc=%d fail_latched=%u \| diag_off_confirmed=%s`（`:2303`）、`[MOT] arm gate fail mask=0x%X (%s)`（`:2310`）、`[MOT] tmc runtime write gate mask=0x%X`（`:2335`）。启动字节期望：fail mask=0x1B、写入门禁=0x3B | `[MOT] snapshot failed`（`:2291`）⇒ 停 |
| 6 | `step_status` | 全局读，不动外设 | `[STEP] dev=%s ch=%d enabled=%s last_stop=%s stop_rc=%d`（`step_pwm.c:358`） | 行缺失 ⇒ 停；说明行（`:361`）被裁剪不算失败 |
| 7 | `tmc_uart_probe` | **实际 UART 读流量**（3 次 `tmc_read_reg`：IOIN/GSTAT/IFCNT，`tmc2209.c:951/967/988`）；`:934` 调 `tmc2209_cfg_txn_begin()`（定义 `:2101`，内部 `RT_WAITING_NO` 非阻塞取事务锁 `:2108`）、`tmc_uart_open()`（`:942`）；**0 个 WRITE 帧**（函数体内不出现任何写寄存器调用，`:1001` 自证） | 首行 `[TMC] probe: 只读身份检查 txn-held IOIN+GSTAT+IFCNT (0 个 WRITE 帧)`（`:949`）→ `[TMC] IOIN = 0x%08X VERSION = 0x%02X -> %s`（`:961`，**VERSION 必须 0x21**）→ `[TMC] GSTAT = 0x%02X reset=%u drv_err=%u uv_cp=%u`（`:974`）→ `[TMC] IFCNT = %u …`（`:991`）→ `[TMC] probe 结束: 读事务 %d 次, WRITE 帧 0 个`（`:1001`） | `[TMC] probe REFUSED (%d): 配置事务不可用或被占用`（`:937`）、`uart2 打不开`（`:944`）、`IOIN read failed (%d)`（`:956`）、`GSTAT read failed`（`:985`）、`IFCNT read failed`（`:997`）任一 ⇒ 分支结束；`VERSION != 0x21` ⇒ 记 LINK_UNPROVEN 的具体子因，不改地址重试 |

M0 的两条必须写进现场记录的"已知事实"（否则就是自欺）：

- **读命令也会改 health**：`tmc_health_after_xfer()`（`tmc2209.c:709-713`）按最近一次传输把
  `tmc_health` 置为 `SUBSYS_OK` 或 `SUBSYS_DEGRADED`，而 `tmc_config_show` 的读也走这条路径
  （`tmc2209.c:2357`/`:2368`→`:738`）。所以"probe 之后 health 变好了"**不构成链路健康证据**，
  只是最近一次传输的结果；命令本身也不是电流测量。
- 提示符只证明该条响应结束，不证明 TMC 总线健康，也不证明 Safety READY。

M0 与旧采集器的关系：R1a 采集器的传输与落字节原则已被验收，但它的命令白名单与联机分支是
A0 专用（含 17 次 trip 流程），**不能直接跑旧脚本继续电机主线**；本轮按任务书 §8.1 不重写采集器，
只交这张命令表与判据，由复审方据此绑定最小收发入口。不要把"还没写适配脚本"说成"已有可一键跑的整包"。

## 2. M2 —— 候选固件与配置路径（电机仍不接）

进入条件：候选镜像通过独立软件/镜像复审；§3.4 两项宏例外的现场授权**绑定到具体散列**
（BIN `a76b845d…` + AXF `c1e9d4e7…`）；M0 的通信结论已成立或已具名失败。
额度必须在最终包里**单列**（下载 1、读回 1、复位 1、COM5 打开 1…按最终批准文本），
不从上一轮 BOARD-B 借用额度，也不因为存在恢复快照/恢复工程就声称"恢复演练通过"。

字面次序（电机未接、SW1 阻断）：

```text
fault_reset          # 仅当确有锁存故障且前置成立；成功只到 MANUAL_CLEAR
system_selftest      # required 集真通过才允许 READY，不可省略
tmc_config_apply CONFIRM
tmc_config_show
motor_status
```

- `fault_reset`（`safety_state.c:684` 起，导出 `:961`）副作用是真实的：取 `ss_lock`（`:717`，成功路径
  在 `:956` 前归还）、经 `motor_lock_take_checked()` 取电机锁（取 `:912`，归还 `:943`
  的 `motor_lock_give_checked()`）、`sp_trip_fast`（`:742`）与
  `sp_recovery_start`（`:744`）、`rt_pin_read`（`:769`）、真实 STEP 关断与 DRV_ENABLE LOW 写
  （`sp_recovery_probe_off()` `:812`、`safety_drv_permit_confirm_low()` `:826`），
  成功时清锁存 `ss_fault_code=FAULT_NONE`（`:939`）并进入 `SAFETY_MANUAL_CLEAR`（`:940`）。
  判据：`[SS] ==> MANUAL_CLEAR …`（`:957`，按前缀断言）；出现任一 REFUSED（`:895` 关断结果无效、
  `:914` 拿不到 mot_lock、`:947` 票据新代）⇒ 停，不重复敲。
- `system_selftest`（`supervisor.c:401`，导出 `:406`）会真实写 `safety_drv_enable_write(0)`
  （`safety_state.c:603`）、尝试 `motor_arm()`/`motor_disarm()`（`supervisor.c:388-392`），
  并按结果转 READY 或 `safety_force_shutdown(FAULT_SELF_TEST)`（`safety_state.c:1045`）。
  判据：`[SS] [REQ OK] …` 全项 + 末态 `state=READY`；出现 `[SS] !! SELF_TEST->READY refused (rc=%d)`
  （`:1034`）⇒ 停。
- `tmc_config_apply` 不带 `CONFIRM` 时只打 usage 与当前状态并**直接返回，不产生任何总线流量**
  （`tmc2209.c:2379-2386`）。带 `CONFIRM` 才走 `tmc2209_apply_config()`（`:2391`→`:2051`），
  非阻塞取 txn、评估运行态门禁，然后**真实 WRITE**：GCONF `:1910`、CHOPCONF `:1948`、
  IHOLD_IRUN `:1956`、TPOWERDOWN `:1966`（都是 `tmc_cfg_write_one_ctx`），逐次以 IFCNT 确认；
  成功行 `[TMC-CFG] CONFIGURED …`（`:2031`，
  284 B 会被裁剪，按前缀 `[TMC-CFG] CONFIGURED` 断言），状态转 `TMC_DRV_CONFIGURED`（`:2007`）。
  前置全通才算数：Motor IDLE/unarmed、current=0、target=0、请求 LOW、ENN 在禁能侧、UART 健康、
  写入门禁 8 位全 0（门在 `tmc2209.c:1456-1488`，位值 `:2058`）。
- `tmc_config_show` **不是无副作用日志**：它做两次真实读（GCONF `:2357`、CHOPCONF `:2368`）并会
  改 `tmc_health`；但不发 WRITE、不取 txn 锁（`:2313` 注释）。
- `IHOLD_IRUN`(0x10) 是**只写**寄存器（`tmc2209.h:131-135`）：不得伪造它的读回，
  到达凭证只有 IFCNT 递增与配置快照 —— 快照在同一个静止窗口里连写 `done_gconf`/`done_chop`/`done`/
  `done_valid=TRUE`（`tmc2209.c:2003-2006`），代次不符时这份"晚到的成功"会在 `:2020-2029`
  被具名拒绝提交（`apply-stale-success`），不刷新健康。
- 已 READY 且无锁存故障时，**不为了凑命令数**执行 `fault_reset`。
- 禁止裸写 GCONF/CHOPCONF/GSTAT、禁止禁用保护、禁止伪造 CONFIGURED 状态；任何失败停止分支。
- 电机未接时的电源电流读数不能作为绕组限流验证；`[TMC-CFG] 理论电流 …`（`:2346`）是**计算值**。

## 3. M3 —— 第一次空载、低速、短时试转（本文件只做完整准备）

状态：**NOT_AUTHORIZED / NOT_EXECUTED**。以下条件缺一，不接电机、不使能：

1. 电机型号/额定相电流的定义、模块型号、Rsense 与限流模式的同实物依据齐备（见
   `REV/motor_current_basis.md` §1/§4 的来源分级），且"当前计划电流 + 不确定性"被最终批准包接受，
   实际采用值写进批准文本。
2. M0 通信与 M2 配置已成立；本次启动的 required 自检真通过、无锁存故障、STEP 关断结果可信、
   运行态门禁全通。**ADC 仍为理论标定、异常诊断阈值仍为工程占位**这两条事实必须保留：
   这次试转不叫"异常保护系统验收"，也不关闭 BUG-008、物理停机时延（RISK-03/03a）或安全完整性验收。
3. 接线只由操作者在 **USB 与 VM 均已切断**且符合现场断电判据时完成；不带电插拔电机/模块/调 VREF
   禁止；线序与两相配对有依据；空载固定；轴上做可见标记；人能手边立即切 24 V。
4. 候选镜像与两项宏例外已获明确批准；`STEP_DIAG_ISOLATED_ENV_CONFIRMED` 与限位宏继续保持 0。
5. SW1 的阻断→允许→收尾阻断三步行文进入最终包：许可 LOW 下准备，只有进入正式 arm 时才切到
   已批准的使能位置；**不得为使 arm 通过而忽略 ENN 反馈**。
6. 因为接电机必须断电，重新上电后 TMC 易失配置丢失：**必须在本次启动重新跑 required 自检与
   `tmc_config_apply CONFIRM`**，不复用上一轮的 CONFIGURED 印象；最终包给出新的上电/串口预算。

首转字面序列（现场批准时固定，不自动增速、不加电流、不反转）：

```text
[本次启动 required 自检 + 必要的 fault_reset / system_selftest]
tmc_config_apply CONFIRM
tmc_config_show
motor_dir 0
motor_target 100
[按包确认 SW1 位置、许可/ENN 前置]
motor_arm
motor_start
[观察窗口名义 2 s；首转只做一个方向一次]
motor_stop
[确认 current=0；斜坡未归零就不声称停稳]
motor_disarm
motor_status
safety_status
```

逐条判据（源码口径）：

| 命令 | 副作用 | 通过判据 | 注意 |
|---|---|---|---|
| `motor_dir 0` | 持 `mot_lock` 后在全部静止条件同窗写 DIR GPIO（`motor.c:1429`→`:1510`） | `[MOT] dir=%u (at rest, 全部条件与 DIR 写同窗)`；参数非 `0/1` 只打 `usage: motor_dir 0 or 1`（`:2349`） | 静止复核不过 ⇒ 拒绝行（`:1438`/`:1446`/`:1497`），停 |
| `motor_target 100` | 改 `mot.target_hz` | `[MOT] set target %d Hz: %s`，`%s` 必须是 `OK`（`:2363`） | 出现 `REJECTED(over MOTOR_MAX_HZ)` 或 `bad hz` ⇒ 停；**API 上限仍是源码的 20000 Hz，候选镜像没有硬定时/硬封顶** |
| `motor_arm` | 真实 DRV_ENABLE HIGH 写 + 回读，`mot.armed=1`（`motor.c:1317`） | `[MOT] ARMED (drv_en request=HIGH readback verified, ENN feedback=LOW confirmed)`（`:1321`） | 任一拒绝（`:1094`…`:1311`）⇒ 停，不重复敲 |
| `motor_start` | 产生**真实 STEP 输出**（READY→RUN 提交） | **成功无输出**：`motor_start` 成功路径不打字，仅 `return RT_EOK`（`motor.c:1677`）⇒ 判据是"无 REFUSED 行 + 随后 `motor_status` 的 `state=RUN`/`cur>0`" | 出现 `[MOT] start REFUSED …`（`:1545`…`:1671`）或 `start **未提交**`（`:1873`/`:1880`）⇒ 停 |
| `motor_stop` | `mot.target_hz=0`（软件命令值，`motor.c:1685`） | `[MOT] stop requested: target_hz=0 (软件命令值), STEP 实际关断结果见 stop_result(当前=%s)`（`:1691`） | `stop_result` 未可信 ⇒ 不声称停稳 |
| `motor_disarm` | STEP 关断 + 许可 LOW 真实写，可能投 `EVT_SOFT_FAULT`（`:1402`） | `[MOT] DISARM (动作已做: STEP %s / 许可 LOW %s / ENN feedback=%s%s)`（`:1408`） | 该行可能达 126+ 字符，按前缀断言；拒绝/放弃行（`:1383`/`:1386`/`:1389`）⇒ 停 |

收尾必须记录的观察项（有示波器更好，没有也先做这一层）：实际是否看到转动、旋向、
是否异常抖动/异响/温升、是否出现故障、正常 `motor_stop`/`motor_disarm` 的原始返回与最终 ENN 读数。
`100 Hz` 与 `2 s` 是首转草案的保守操作设定，**不是已测物理频率/转角**；细分与步距角未知时
不给 rpm 或转角数字；小角度无可见运动记 `NOT_OBSERVED`，不自动升频、不加流、不"再来一次"。

应急额度（必须在最终包与正常额度分列）：

- 正常：`motor_stop` 1 次、`motor_disarm` 1 次。
- 预授权紧急：额外 **1 次** `motor_disarm` —— 仅用于"已使能/运行后发生响应异常且串口仍可用"，
  此时停止一切普通命令，只用这次预授权撤能。
- 若串口不可用，或撤能后没有立即可信的关闭结果：现场直接**阻断 SW1 并切断 24 V**，
  随后按现场规程再拔 USB。
- 明确不承诺"串口命令一定能停机"：物理停机时延仍未测（RISK-03/03a、M-1/M-2/M-3 开放），
  也不把"失败即停止发命令"写成让正在运行的电机继续无限输出。

## 4. 最终批准前一次性列出的最小缺项

已在 `REV/motor_current_basis.md` §4 逐项登记，这里只列真正卡住 M2/M3 的两组：

1. **电机铭牌/型号与额定相电流的定义**（相电流 RMS 还是 DC、是否含保持工况）。
2. **驱动模块型号与 `Rsense=110 mΩ`、`VREF=1.27 V` 的同实物依据**（是同一块板同一时刻的实测，
   还是原理图名义值/文档值）。

其余（细分、步距角、VREF 的现场实测、模块散热条件）在 M3 批准文本里补即可，本轮不重复追问
已有明确资料的部分。缺参不改变本文件已交付的 M0 命令包与候选镜像身份。

## 5. 本轮实际执行状态（与 §9 验收表逐项对应）

```text
M0 = NOT_AUTHORIZED / NOT_EXECUTED（命令表与判据已写好，一条也未发）
M2 = NOT_AUTHORIZED / NOT_EXECUTED（候选镜像未下载，宏例外的现场授权未取得）
M3 = NOT_AUTHORIZED / NOT_EXECUTED（电机未接、无电流依据）
hardware_actions = []        target_commands_executed = []
serial_open = 0              dap = 0    program = 0    flash_read = 0    active_reset = 0
device_enumeration = 0       sw1_cut.ps1 = 未运行(HOLD 保持)
board_last_proven_bin = 5f30d3f0…   board_current_power_state = UNKNOWN
a0_duration_evidence = NOT_ACHIEVED
tmc_link_status = LINK_UNPROVEN（不因离线分析变健康）
```
