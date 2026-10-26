板级重测候选命令表与采集门流程（phase8-target-measurement-a0-export-r1）
状态：**仅是待审批方案，本轮未执行任何一条**。hardware_actions=[]。
候选绑定镜像：measure-a0.bin sha256 5f30d3f058ceff516b2185aa3a1e597bbe63293b94a6fd801fd989a6f7b2b8e6
             (261452 B，本次新建，尚未复审、尚未上板)
启用前提（缺任一条就不开工，不猜）：
  P-1 云端独立软件与镜像复审通过，并以**真实新散列**制作有界重测包；
  P-2 用户对该重测包的明确批准（一次性，逐批）；
  P-3 现场隔离复核：SW1 阻断位状态须与现板标识对应（上轮 SW1 未回报变更 = 仍阻断）；
        24 V/USB 连接设备与上轮登记不一致时以现场为准重新登记；
  P-4 板上现存镜像仍是旧 measure-a0.bin 358fd81a…（已回读确认），
        1 MiB 双读一致快照 b89f8a7cde7b944a5b5425f512a715d27a17c2e6c25822f36b2205bbefd0cac2
        与恢复输入 b3ac97b976bc476556818f2bbaa977d15854a5c1d342f4b9c9d3ed825deb2c65 仍在两处保存；
        **恢复下载仍未演练**，故回滚能力不得宣称已证明。

====================================================================
1. 每个批次的固定动作序（采集门插在 dump 与 clear_records 之间）
====================================================================
  a) `spm cap`      —— 确认 schema=2 / record=120 / slots=64 / 四道宏=0 / COUNTER 状态
  b) `spm init`     —— 静止态能力检查（只读 PPB，不改 CYCCNT）
  c) `spm start <entry>`
  d) 触发 N 次（每批 N 固定：批1=1，批2=16，批3=1 次 motor_disarm 走 off_attempt）
  e) `spm stop`
  f) `spm status`   —— 必须看到 in_flight=0 dropped=0
  g) `spm dump`     —— **原始接收字节逐批落盘（不删 NUL、不改 CRLF、不补列）**
  h) 运行采集门：
       python tests/host/a0_export_gate.py --raw <本批原始接收文件> \
           --batch <批名> --expected-records <N> --entry <本批 entry> \
           --json-out <本批门结论.json>
     门返回 verdict / action_plan；串口脚本**只能按 action_plan 行动**：
       verdict=ACCEPT  → 才允许发 `spm clear_records`，才允许开下一批
       verdict=REJECT  → clear_records 发送次数=0，下一批触发次数=0，保存证据并停
                         （不得重试、不得"再 dump 一次看看"、不得手工拼接缺失列）
  i) 批末再 dump 一次作为空批次核对（预期 records=0）

采集侧仍用本机现有 PowerShell SerialPort（不装 pyserial）；门本身只读文件、不碰串口。
提示符出现或 rc=0 只说明交互结束，**不作为放行的任何依据**。

====================================================================
2. 与上一轮的关键差异（为什么这次能拿到时长证据）
====================================================================
  上轮：头行与每条数据行被 128 字节控制台缓冲剪成 127 字节（14 列），
        body_valid/gates/invalid_reason/complete 等判据列全部丢失 ⇒ 有效性/时长 NOT_ACHIEVED。
  本轮修后（离线已证，目标未证）：整行经 rt_kputs 一次送出 490 B/≤374 B，
        47 列完整、无 NUL；容量不足时改发具名 EXPORT_ERROR 且 dump 返回 -3。
  因此重测的**先决判据**改为：门的 HEADER_EXACT_MATCH + COLUMN_COUNT=47 + 无 NUL；
  只要门 ACCEPT，逐样本 body_valid/gates/invalid_reason 才可读，§9 纳入规则才可执行。

====================================================================
3. 重测时必须同时登记的观察（不改记录器合同）
====================================================================
  - `spm cap` 的 counter=/clock_basis=/guard_ready= 与 `spm status` 的 gates_now= 完整可读；
  - 出现任何 "[spm] EXPORT_ERROR: line_overflow" ⇒ 该批立即 REJECT（不等计数核对）；
  - 其它线程的异步日志（如 [SS]/[BB] 行）落在 CSV 块外可保留；落进块内 ⇒ 该批 REJECT；
  - 完整但 body_valid=0 的样本：存档、计入条数、按 invalid_reason 排除出时长统计，
    **不得**为了拿到有效样本追加任何保护动作或重触发；
  - 每批结束按原判据逐条判读，判据列名与位表仍以 reports/plans/…operator-pack §3/§9 为准。

====================================================================
4. 明确不做
====================================================================
  不修内核/不改 RT_CONSOLEBUF_SIZE/不改其它模块的历史长日志；
  不启用四道宏、不起电机、不做 ramp、不写许可 HIGH、不动 DIR/STEP 使能；
  不做 mass erase / unlock / under-reset 重试；不演练快照恢复下载；
  不在拿到新散列重测包与用户批准前烧录、追加 trip、重采样或恢复。
