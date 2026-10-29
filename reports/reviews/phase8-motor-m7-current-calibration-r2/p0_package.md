# NS800 M7 P0 电压观察包（采集器补正候选）

2026-10-30。**离线已验证；尚未获现场批准，未执行。**
取代 208bf102 的 p0_package.md 操作口径，不改其历史执行状态。
计算器 R3 已关闭；P1 METHOD_NOT_READY，P2 NOT_AUTHORIZED，示波器/A0 仍暂缓。

## 1. 固定输入

| 对象 | 字节 | SHA-256 |
|---|---:|---|
| M7-R1 motor BIN | 266244 | e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da |
| M7-R1 motor AXF | 1688696 | 2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584 |
| p0_voltage_observation.py | 19642 | a2c75ea83cd3842246397a6eff33eea52745c69f8926fb6800a2561fe36cb95a |
| verify_p0_candidate.py | 16343 | bc438094d4193d31fba710622c13d41d3fa19208658dec4b4015f11b444769a2 |
| m7_calcalc.py（只作为已接受的采样格式解析器） | 54267 | 5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad |

固件输入 5b564eb962e8518920f581111d7a460a2b1fce64，镜像路径仍 reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.bin/.axf。
BIN 中日期 Oct 29 2026 @260674、时间 23:11:52 @260665（零基字节；旧计划 259925/259916 不适用于该 BIN，作废）。
期望横幅：`5.3.0 build Oct 29 2026 23:11:52`。
启动就绪另需完整源码原文：`[MAIN] boot complete: READY (motion still gate-locked)`。
不等自发 msh 提示符，不要求 1200 ms 静默；危险告警立即停。

## 2. 一次批准额度与前置

现场有人；沿用已知 COM5/板载 DAP、供电与接线，不要求例行拨 SW1、断电回报或拆电机。
电机按现场实际记录连接状态；本包无任何运动命令，不能把历史“未接”代填为现场事实。
P0 只要求本次启动后软件可确认静止/禁能；不要求 CONFIGURED/IRQ OPEN/arm_gate_mask=0。

| 动作 | 上限 |
|---|---:|
| sector 下载同一 AXF | 1 次 |
| 0x08000000 起 0x41004（266244 B）回读 | 1 次 |
| COM5 打开 | 1 次 |
| 显式 SYSRESETREQ | 1 次，由采集器在串口打开后执行 |
| DAP 会话 | 3（下载/回读/复位各一次） |
| safety_state / safety_status / motor_status / step_status | 各 1 次 |
| current_cal show | 2 次 |
| current_cal sample 128 | 1 次 |
| UT39A+ 电压读数 | 4 个，两个节点各前后一次 |

**六种不同命令，合计七次发送**。单个 CR 结束一条命令，不发 CRLF 所造成的第二个空命令。
没有清故障、配置 apply/save/nominal、TMC 压测、额外 query、arm/start/DIR/STEP/HIGH。
下载器内部复位不独立可观测，不能称物理复位总量只有一次。
额度失败也消耗，不重试、不补发。真实危险沿用既有硬件阻断；脚本不擅自加电机命令或自动恢复。

## 3. 下载与回读（批准后才执行）

先离线检查工具版本、pack、已知 UID 来源与全部文件散列。可执行 `python -m pyocd reset --help` 验证 CLI 选项，**不能为查参数去连接或枚举设备**。
UID 从既存私有文件读取，48 字符，sha256(uid) 前12位 d2f530dbd062；不打印、不上传。
pyOCD 0.45.1 与厂商 pack 及 target 来自 M5 已成功记录，不改算法、不 mass erase/unlock。
以下为 Git Bash 形式；DAPUID 为私有变量，RUN_ROOT 为本次全新仓外目录。

```bash
python -m pyocd flash --pack "C:/Keil_v5/ARM/PACK/.Download/NOVOSENSE.NS800RT7XXX.0.5.3.pack" \
 -t ns800rt7p65d_dhqfby2 -u "$DAPUID" -f 1000000 --no-config \
 -Oauto_unlock=false -Ocache.enable_memory=false -Ocache.read_code_from_elf=false -e sector \
 reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.axf

python -m pyocd commander --no-config -t cortex_m -u "$DAPUID" -f 1000000 \
 -O auto_unlock=false -O connect_mode=halt -O resume_on_disconnect=false \
 -O cache.enable_memory=false -O cache.read_code_from_elf=false -O scan_all_aps=false \
 -c "status" -c "savemem 0x08000000 0x41004 $RUN_ROOT/readback.bin" -c "status" -c "exit"
```

下载原文与回读文件先保存，回读必须长度/散列/逐字节全等；返回码不能单独充当通过。
**不要另手工发复位命令**：下一步采集器已固定发一次已知 UID 的 `reset --method sysresetreq --no-config --no-wait --connect attach --core 0 -t cortex_m`。
后者明确方法与已知 UID，避免旧裸 `pyocd reset -t cortex_m` 的默认选择/等待。
`--no-wait` 是不等待缺席探针，不是第二次试连。工具内部定位该 UID 的行为不冒称零内部枚举。

## 4. 启动采集器（同一串口会话持续到结束）

离线补正定稿并且**本文件整包获得一次批准后**才允许运行。OUT/P0 必须不存在；readback.bin 在其父目录。
通过本次回读后，`--site-confirmed` 与批准来源才能如实填写。

```bash
python reports/reviews/phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py \
 --execute-approved-p0 --approval-source "本次明确整包批准的消息或记录" --site-confirmed \
 --payload-bin reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.bin \
 --payload-axf reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.axf \
 --readback-bin "$RUN_ROOT/readback.bin" --uid-file "<既存私有UID文件>" --out "$RUN_ROOT/P0"
```

Agent 应后台启动并逐次短轮询 stdout，不用一个阻塞调用等15分钟；本脚本没有无人监测运动。
脚本先校验 BIN/AXF/回读/计算器与私有UID，再创建串口；打开后先建立台账，再做唯一显式复位并保存启动流。
复位后启动等待上限30s，命令单写上限2s、响应12s（按驱动遵守 timeout 的合同）；不调用可能无界的 flush。
每块接收数据 fsync 后才判定，读异常也保留已有字节。每条状态响应有完整字段验证，拒绝/缺失/冲突立即停。

静止门：READY/fault0；Motor state0、armed0、cur/tgt0、LOW/pad0、ENN HIGH/verdict OK；Motor stop api-accepted(rc0/无锁存)、diag_off YES；诊断 epwm1 enabled0、无关断失败。
`drv_en_request` 历史影子不作为许可焊盘凭据。STEP_DIAG=0 不能代替上述 Motor 侧合同。

已知 safety_status 的 ENN 解释长行会在第126字节后 NUL 截断、与下一短行粘连。仅对**已知该非权威解释行、精确长度126、随后完整 [SAFETY] request 行**建立协议分界；原字节不改写。
该解释行不是通过依据，短 request/pad/FB/verdict 行必须完整匹配。其他权威行含 NUL/截断拒绝；sample 含任何 NUL 拒绝。
不为了消掉这条非权威长行而改 C 或重编。

## 5. 你只需四个读数

收到 `WAIT_DMM_BEFORE` 后，Agent 才请现场用 UT39A+ 的 COM/电压插孔、直流4V档：
1. 黑笔 H10-2/GND，红笔 H10-1/CURRENT_ADC，记前电压与实际时刻；
2. 红笔 H10-3/3V3，记前电压与实际时刻。

Agent 将实际读数写入 `P0/dmm_before.json`（先临时文件，再原子改名）；**不要预建**正式文件。结构：

```json
{"meter":"UT39A+","range_V":4,"mode":"DCV",
 "Vadc":{"V":null,"observed_at":"UNKNOWN_TIME"},
 "V3v3":{"V":null,"observed_at":"UNKNOWN_TIME"}}
```

null 必须替换为用户实报的伏特数，否则脚本拒绝；时刻没记就 UNKNOWN_TIME，不拿日志时刻冒充。
这时脚本依次 show→sample128→show，核对系数/来源/版本稳定，然后发 `WAIT_DMM_AFTER`。
再按 H10-1、H10-3 各读一次，按同结构原子写 `dmm_after.json`。
两个等待各上限15分钟，期间持续被动保存/检查危险，不把正常无输出当设备故障。
没有 before 文件不采样；没有 after 文件不产生完成结果；不补造、不超四个读数。
不串表，不动VM/相线/模块/供电/SW1/VREF。H10针脚来源为10月19日NS800原理图，非另一个同名项目。

## 6. 判定与交付

正常：进程退出0 + ledger COMPLETE + result.status=VOLTAGE_OBSERVATION_ONLY 三者同时成立。
result.json 保留四个读数、各自误差限、名义ADC均值/极值与两个 H10-1 前后偏差。UT39A+4V档误差限按 ±(0.5%读数+5mV)列出。
H10-3 只是测点，未证等于 VDDA；3300/4095 只是名义换算。未知 ADC 参考/转换误差不作精度 PASS/FAIL，不产 MEASURED 系数，不外推相电流RMS。
只把本轮观察登记为 VOLTAGE_OBSERVATION_ONLY；不能关闭独立电流标定、DIAG/SG真实触发、最坏停机时延或整机验收。

所有 raw_boot/七份响应/人工等待流、原始 reset 输出、ledger/result、DMM原件、下载/回读证据均保存。
证据发布前脱敏且区分原件/脱敏件；base64 保留串口CR/NUL，不用 autocrlf 文本归档冒充原字节。
失败保留首错与已耗额度；串口关闭，不自动重烧、复位、重开、清故障或恢复；不例行索取断电终态。

## 7. 一次批准文字（本包当前未获批准）

> 批准执行本文件 M7 P0 电压观察包：现场有人，沿用现有接线和SW1位置，电机不动作；使用 e614a075…f7da /266244 B 镜像与本文件绑定的修正版采集器。允许下载1次、定长回读1次、COM5打开1次、串口打开后的SYSRESETREQ1次、四条状态各1次、show2次、sample128一次和四个DMM电压读数。失败即停，无重试、无配置写入、无电机动作。

不因文件准备完成而推定批准；P1/P2 均不在该批准内。
