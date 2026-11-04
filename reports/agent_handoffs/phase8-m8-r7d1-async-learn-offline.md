# M8-R7D1 离线异步学习轮 — 交 A 复审的回执

任务书 `NS800_R7D1_Offline_Async_Learn_Task_20261104.zip`(13467 B, sha256 `b35e0a2e16a680ff90a37d2edb1de9da6c85adbc8011ccea33c5354e37922c5c`,175 行),状态 `GO_OFFLINE_IMPLEMENTATION_ONLY`。
基线 = 已审 tip `7885aea606a50f1c34723dbfa9b8c0c4341326de`;构建时 HEAD 就是它(`HEAD_IS_BASELINE` PASS)。
本轮**实现 + 主机验收 + 离线构建合为一次交付**,没有另开登记轮,也没有向操作者等下一次批准。

## 1. 改了什么(只有六个文件,其中产品三个)

产品(逐字节 diff: `code/product_diff_vs_7885aea.diff`,+656/−117):
- `applications/diag_relative.h` — 新合同类型:七态 `rel_job_state_t`、采集器归属 `rel_cap_owner_t`、
  整份快照 `rel_job_view_t`、`REL_CANCEL_*` 显式枚举,以及 `rel_job_start/cancel/status/advance` 原型。
- `applications/diag_relative.c` — 作业表 `d_job`(276 B 静态)、叶锁 `d_ctx_lock`("rel_ctx",
  临界区内不调 motor/cfg/Flash/UART/mdelay/print)、归属 `d_cap_owner`;`learn` 改为受理即返回;
  新增 `learn_status` / `learn_cancel JOB_ID`;`zero` 仍同步但把"拿归属"与"作废旧凭据"的先后改对了;
  `tier/commit/clear/show` 统一走同一把 ctx 锁的整份快照读写。细节与行号见
  `implementation_contract.md`。
- `applications/diagnosis.c` — 只加一行 `rel_job_advance(&in, have_frame, d_mode)`,排在
  `rel_prod_evaluate()` 之后、所有披露打印之前(diagnosis.c:450–456)。保护义务仍在前,新推手不抢占。

验证链(三个): `tests/host/rel_main.c`(A01–A12 + 夹具 knobs)、`tests/host/run_diag_relative_m8.sh`
(ALL_CASES 50→62、RV6 锚点重钉、新增 RDV1–RDV4)、`tests/host/shim_rel/rtthread.h`(补
`RT_TICK_PER_SECOND 1000`,与 rtconfig.h 同值,因新代码的 ms→tick 换算显式依赖它)。

## 2. 怎样测的

- 修前基线原样跑: `cases=50 pass=50 fail=0 executed=50`,warnings=0。
- 实现后全链: **`cases=62 pass=62 fail=0 pre_bad=0 executed=62`**,`build_rc=0 warnings=0`(`-Wall -Wextra`),
  判定行 `M8 RELATIVE RESULT: PASS(主机链, 离线; 现场项未验证)`。原文 `host/normal_chain_out.txt`,
  逐例台账 `host/case_ledger.txt`。
- 原 50 例一条没删: 机器比对 (id→checks) 显示 47 例逐例相同, 受影响的 3 例只增(REL25 41→44、
  REL39 11→15、REL42 31→35);对应关系与理由见 `case_mapping.md`。
- 负控 **14/14 META-OK**(既有 RV1–RV10 全族仍命中 + 本轮 RDV1–RDV4):
  RDV1 取消分支被短路→A05/A06;RDV2 无帧不推进超时→A08;RDV3 start 又回到等样本→A01;
  RDV4 拒绝前先烧凭据→A10。每条都在**产品源副本**上落地一次(打印 `NEGATIVE-INJECTION`、命中一次)、
  编译成功、实跑 rc=1 且命中具名业务失败身份。原文 `host/reverse_run_full.txt`。
- 打印限长双路: 离线工具按实参最宽展开逐调用折算(29 个调用,最宽 120 B 含源 LF,
  非恒真断言由 `PRINT_BOUND_NOT_VACUOUS` 自证)+ 主机链 A12 在同一 120 B 预算下实测,
  含裸 NUL/严格 UTF-8。原文 `host/a12_print_bounds.txt`、`offline/checks.txt`(TOTAL=31 FAIL=0)。
- 离线构建一次(`Keil UV4 -r … -t rt-thread-motor-m1-candidate`,未用 `-f`):
  rc=0,`0 Error(s), 0 Warning(s)`,`V6.24`,`Program Size: Code=209178 RO-data=87242 RW-data=2196 ZI-data=85936`。
  构建钩子只读核对: BeforeMake 未启用,AfterMake 只有 `fromelf --bin`,无下载/调试/串口动作。

## 3. 资源变化的工具支撑评估(要点,全文 `build/stack_resource_r7d1.txt`)

- 镜像增量 Code+RO+RW = 4296 B,与 BIN 292708→297004 的 4296 B 逐字节相符;全局 RAM(RW+ZI)+344 B。
- 全部落在 `diag_relative.o`(+3138 Code/+1047 RO/+4 RW/+341 ZI)与 `diagnosis.o`(+18 Code),
  其余 68 个对象与 39 个库成员行为 0;残差 +92/−3/−1 恰等于链接器 `(incl. Padding)` 行的移动。
- 新增静态 RAM 逐项取自 map 符号表:`d_job` 276 + `d_ctx_lock` 60 + `d_cap_owner` 1 + `d_job_next_id` 4 = 341 B。
- 栈: `diag_thread_entry` 帧 632→640、子树 1580→1588(最深线未变,是既有的
  `rel_prod_evaluate→rel_prod_publish→rel_ctl_take→rt_mutex_take→调度器→rt_kprintf` 那条);
  `rel_job_advance` 自身 48/636 不是上限来源。命令侧 `learn` 836→1084(换线: mdelay 线→ctx 取锁线)、
  `zero` 1164→1276(全部是自身帧)。线程栈 4096 未改,**没有**靠增大栈或延长超时来规避问题。
- 边界(不写成实测): armlink 的数是**静态下界**,不含异常/ISR 帧,且解析不了 msh 的函数指针表
  (`finsh_thread_entry` 两版都是 628,恰证明它没算命令处理函数),所以本轮不给"shell 栈总共用了多少"
  这个手加出来的数。

## 4. 还缺什么(不靠接设备关闭)

- 现场未验证: 异步 `learn` 在真板上的推进/取消/超时时刻、真实 100 Hz 帧流下的行为、
  控制台在真实 RT-Thread 打印路径上的字节完整性 —— 本轮全部 0 次设备动作,一律 NOT_DONE。
- 新镜像 `NOT_ON_BOARD`(BIN sha `df7584c3…`);板上仍是历史 b502 证明(`502dca5d…`)。
  两者没有构成任何"新镜像在板"的证据。
- 后续现场学习计划必须按新命令合同重排:旧 field 计划/rows/b502 证据/历史回包保持原件未改,
  本轮没为"假装兼容"去改它们。
- R7F5A 的物理断电仍是 `PENDING_OPERATOR_REPORT`(操作者回报后才知道时刻与掉电表现);
  本轮未连接任何设备去关闭该待办。
- `polarity_measured=false`、`core_protection_verified=false` 保持;四条禁用声称继续禁止;
  旧的 35 条超长格式串未因此轮关闭,未触及条目仍是待办。

## 5. 边界与卫生

设备动作 **0**(不开 COM5/任何串口、不枚举或连接 DAP、不烧录、不回读、不复位、不向板端发查询、
不启动逻辑分析仪、不 arm/start/运动、不做真实学习/写配置/供断电)。
无 merge/rebase/force/reset/clean,冻结件与旧镜像目录只读引用。
本轮两个目录的每件都加了 `.gitattributes -text`(先加规则再暂存),逐 blob 复算才是原字节。
末轮回读原文按任务书第 10 节留在仓外,不为把回读报告再入库形成循环。

## 6. 目录索引

`reports/reviews/phase8-m8-r7d1-async-learn-offline/`
  `implementation_contract.md` `case_mapping.md` `SHA256SUMS.txt`
  `code/`(产品与验证链两份 diff)
  `build/`(`build_r7d1.log` `stack_resource_r7d1.txt`)
  `host/`(`normal_chain_out.txt` `normal_chain_build.txt` `normal_chain_warning_count.txt`
  `case_ledger.txt` `reverse_run_full.txt` `a12_print_bounds.txt`)
  `offline/`(`checks.txt` `summary.json`)`tools/r7d1_offline_checks.py`
`reports/firmware/phase8-m8-r7d1-async-learn-offline/`
  `rt-thread-motor-m1-candidate.bin` `.axf` `.map` `-callgraph.htm` `build_manifest.json` `SHA256SUMS.txt`
