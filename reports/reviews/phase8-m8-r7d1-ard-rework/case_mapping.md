# 返修轮用例映射(A 的四项 → 主机链用例 → 负控)

链的规模: 上一轮 62 例(50 REL + 12 A)一条未删, 本轮新增 4 例 A13–A16 = **66 例**;
负控上一轮 14 族(RV1–RV10 + RDV1–RDV4)一条未删, 本轮新增 5 族 RDV5–RDV9 = **19 族**。
机器可读原文: `host/normal_chain_out.txt`(66 例逐条)、`host/case_ledger.txt`(台账)、
`host/reverse_run_full.txt`(19 族逐条)、`host/reverse_meta_lines.txt`(META 判定行摘录)、
`host/probe_before_after.txt`(A 探针的前后原文)。
离线判据 `offline/checks.txt` 里 `PRIOR_62_CASES_KEPT` / `FOUR_NEW_CASES_ADDED` /
`REVERSE_FAMILY_COUNT_19` / `REVERSE_NEW_FAMILIES` 四条专门钉这个"只增不减"。

| A 的编号 | 业务语义(判据看的是状态, 不是文字) | 新增用例 | 用例里真正断言的东西 | 负控(注入 → 被破坏的不变量) |
|---|---|---|---|---|
| ARD1 | 参数拒绝不得留下锁或其他副作用 | A13 | `rel_job_status(NULL)` 返回 `-RT_EINVAL` 且 `d_ctx_lock.held==0`; **release 计数增量 0**(它根本没拿过锁, 不是"拿了又放"); **take 计数增量 0**(拒绝排在取锁之前); 之后合法查询 `RT_EOK`、learn 受理成立、取消走到 `CANCELLED` | RDV5 把 NULL 检查挪回 `rel_ctx_take()` 之后 → 破坏"非法路径不碰锁"这一条, A13 的 take 计数断言先炸, 后续查询/取消也会因锁没放而卡住 |
| ARD2 | 所有 zero 退出分支都要保证清理最终可达: 明确状态 + 真实推进者 + 故障消失后释放本次资源; 清理完成前不得交给第二个采集者 | A14 | 作废凭据失败那条出口: `zero FAILED: ctx lock on invalidate` 与 `zero cleanup DEFERRED` 都出现; `owner==BY_ZERO && active==1`(没被抹掉); 旗子可查(`job_snap().cleanup_pending==1`); `learn_status` 有披露行; **推进者没跑之前**第二条 zero 仍被 `capture busy` 拒; `pump(3)` 之后 `owner==FREE && active==0` 且旗灭; 再 zero 能真采到(不再 busy) | RDV6 删掉"留旗"(改成立即 `return RT_FALSE`)→ 破坏"明确状态": 既不披露也没人回收; RDV7 把推进者条件改成 `if (0u && ...)` → 旗还在但**没有真实推进者**, "有界延迟"退化成永久滞留。两条各打一根支柱, 都具名命中 A14 |
| ARD3 | 先完成全部必要门禁, 再产生 clear 副作用; 被拒绝的命令保留原候选、凭据及配置 | A15 | 先 zero + 巡航 learn 到 DONE 建真凭据(不伪造候选); `g_static_begin_busy=1` 让静止事务**如期**拒绝; 断言 `clear REFUSED: static txn` 出现, 且 `memcmp(before,&d_pending)==0`、`zero/learn` 凭据仍 1/1; 门消失后同一条 clear 必须真成功(`profile voided` + 凭据清零), 证明拒绝没把命令废掉 | RDV8 把 `rt_memset(&blank)` + `rel_pending_write(&blank,0,0)` 挪回 `rel_txn_open("clear")` 之前 → 破坏"拒绝不留副作用", A15 的候选/凭据断言炸(这正是 4d50310 的形状, 也与 A 的 clear_control 对照一致) |
| ARD4 | REFUSED ⇒ 配置、候选、凭据全保持原值; OK ⇒ 配置与候选完整一致 | A16 | 三段: ① 令这次 commit 的第 4 次 ctx 取锁失败 ⇒ `commit REFUSED` 且 `cfg_changed==0 && pend_changed==0 && creds_kept==1`, 且同一次不许既 REFUSED 又 OK; ② 干净一轮 ⇒ `commit OK` 且 `project_config_get_rprof` 读回与 `d_pending` **逐字节相同**(同版), 同时量出这条命令实际取了几次 cfg 锁; ③ 只把**最后一次** cfg 取锁(配置发布)打坏 ⇒ `config-write-rolled-back` 披露出现、配置回到旧值、候选也退回 `pend_before` | RDV9 把顺序换回"先 `project_config_set_rprof` 再发布" → 破坏"REFUSED 时配置不动", A16 的 ① 段 cfg_changed 断言炸(就是 A 登记的 magic 0→4E535250 那条) |

## 注入是不是"真的命中目标分支"

`host/reverse_run_full.txt` 里 19 族每条都有 `INJECTED <族名>` 行 —— 这行只在**副本编译成功**之后才会打出来,
所以"编译错误/前置失败冒充检出"在这条链上不成立(`REVERSE_EACH_COMPILED_AND_RAN` 判据核这 19 行)。
每族另外核两件事: 期望的失败身份**必须**出现, `proc_rc` 必须是 1; 其他用例的连带失败按"额外失败身份"
登记但不改变判定(原文照抄在 `reverse_run_full.txt`, 未删)。
本轮新五族的期望失败身份都是新增那四条用例本身(A13/A14/A15/A16), 不是拿旧用例的连带红当检出。

RDV6 的注入文本因为函数签名从 `void` 改成 `rt_bool_t` 而同步改成 `return RT_FALSE;`。
这是 A 说的"修复可以改变内部取锁顺序; 若需调整故障注入锚点, 保留业务触发意图及可触发性"——
锚点(`d_zero_cleanup = 1u;`)还在, 注入的仍然是"拿不到锁时不留旗、不披露"这个缺陷形状。

## A 自带探针(不是我写的判据, 独立复算)

`run_review_probes.py`(A 的原版脚本, 未改)直接 `#include` 仓库现有 `tests/host/rel_main.c`, 后者编入真实产品 C。
- 修前: 在 4d50310 的仓外逐字节副本(五件输入 sha256 与 A 的 `IDENTITY.json` 完全相同)上跑
  → `REVIEW_SUMMARY|checks=4|failures=4|hardware_actions=0`, `run_rc=1`, 四条 OBS 与 A 包内原文一字不差。
- 修后: 在本轮定稿工作树上跑 → `failures=0`, `run_rc=0`。
- 四个观察量前后: `ctx_held 1→0`、`owner 1→0`、`pending_changed 1→0`、`cfg_changed 1→0`
  (`host/probe_before_after.txt` 有逐字原文与小结)。
- 本机与 A 的编译器不同(gcc 4.9.2 tdm64 vs A 的 gcc 14.2 Debian), 两条都是同一份产品 C 的 host 编译;
  A 那条 Linux 记录带 1 条 misleading-indentation 警告(旧夹具 `adxl345_read_mg()` 同行三个 if),
  本轮不把它说成"零警告", 也不据此否定对方的记录。
