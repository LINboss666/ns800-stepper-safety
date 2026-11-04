# M8-R7D1 ARD4b — 用例与负控映射(本轮改了哪些验收, 各自检出什么)

被审件 `e1d7f3d`。A 的第 2 节要求「将本组合故障并入现有 A16 或等价业务回归」「加入能检出
该不变量破坏的负控, 并证明注入确实命中、编译成功、业务断言失败」「保留原 66 项及 19 族的
业务含义」。这份文件把这三条逐一对到工件上。

## 1. 主机链 A16(现有用例内扩展, 不新增编号)

`tests/host/rel_main.c:4199 c_A16()` + 辅助 `tests/host/rel_main.c:4171 a16_refusal()`。
A16 的 `checks` 从上一轮的 35 增到 37(`offline/summary.json` 的
`case_checks_delta.grew == [["A16-…", 2]]`, `shrunk == []`)。

| 段 | 位置 | 装什么故障 | 断言的合同 | 失败时打印的身份 |
|---|---|---|---|---|
| ① | rel_main.c:4210-4221 | 无(干净世界) | 量出 `ctx_steps`/`cfg_steps`; commit 必须 OK; **OK 之后 `cfg == d_pending` 逐字节相同** | `干净世界里的 commit 没成功` / `commit OK 之后配置与候选不一致` |
| ② | rel_main.c:4223-4225 | 已有有效配置 + 提交点上第 `ctx_steps` 次 ctx 取锁失败 | 报 `(ctx-lock-commit)` 且拒绝后配置/候选/凭据逐字节相同、不持锁 | `已有配置下提交点 ctx 取锁失败却没保持原状: flags=…` |
| ③ | rel_main.c:4226-4228 | 已有有效配置 + `project_config_rprof_begin()` 里第 `cfg_steps` 次 cfg 取锁失败 | 报 `(cfg-lock-commit)` 且同上三份不变 | `已有配置下 cfg 取锁失败却没保持原状` |
| ④ | rel_main.c:4229-4239 | 把 ctx 注入**挪后一步**(第 `ctx_steps+1` 次) | 这条命令必须正常 OK 且不出现 REFUSED —— 证明 ②/③ 的"命中"来自那一次具体取锁, 不是"卡在哪都红" | `锚点不特异: 挪后一步仍改变了结果` |
| ⑤ | rel_main.c:4241-4261 | 换一个世界(配置为空, `magic==0`)重跑 ②③, 然后真提交 | 空配置下两条拒绝同样无副作用; 拒绝之后同一条 commit 仍能提交成功且 `profile_id==1` 且两份逐字节一致 | `空配置下…没保持原状(magic 应仍为 0)` / `两条拒绝之后同一条 commit 提交不了` |

`a16_refusal()` 的 `flags` 每一位都是一个可辨的不变量破坏(不是"没通过就完事"):

```
bit0 没命中目标拒绝身份   bit1 没按 REFUSED 披露      bit2 意外报了 OK
bit3 配置(RAM profile)被改 bit4 候选 d_pending 被改    bit5 learn/zero 凭据被动
bit6 拒绝之后仍持锁        bit7 前置/后置读不到配置(夹具本身坏了)
```

bit6 就是 A 说的「不以…强行解锁或吞掉错误替代正确事务」在测试里的落点:
`begin()` 成功但 ctx 拿不到时必须归还**自己刚拿的** cfg 锁。

## 2. 负控(反向验证)

链总数从 19 族增到 **20 族**(`host/reverse_meta_lines.txt` 20 条判定行,
`INJECTED` 行 20 条 = 每族都真的编译并在仓外副本里跑过)。

| 家族 | 注入 | 破坏的不变量 | 预期命中的业务失败 |
|---|---|---|---|
| `RDV9_commit_config_before_publish` (改写) | 把 `project_config_rprof_publish_locked(&cand)` 提到 `rel_ctx_take()` 之前(标记「NEGATIVE-INJECTION RDV9: 配置先落地, 候选发布仍可能失败」) | 提交点之后的两份发布必须同批发生 | `A16-commit-refused-keeps-config-and-candidate` |
| `RDV10_commit_swallow_ctx_take_failure` (新增) | 把 `if (rel_ctx_take() != RT_EOK) {…goto out;}` 整块换成 `(void)rel_ctx_take();`(标记「NEGATIVE-INJECTION RDV10: 取锁失败也继续发布(吞错误)」) | 拿不齐锁就必须拒绝, 不许用"继续走"凑成 OK | 同上 |

RDV9 的名字沿用上一轮(家族身份不变: 都是"配置先落地、候选还没发布"), 注入体换成本轮
新顺序下的等价缺陷 —— 上一轮那条锚点(`rel_pending_write` 在 `project_config_set_rprof`
之前)随着旧顺序一起消失了。这一点在 `offline/checks.txt` 的
`REVERSE_RDV9_RENAMED_NOT_REUSED` 里对照上一轮归档件核过。

## 3. 保留与不越界

| 判据 | 结果 |
|---|---|
| `PRIOR_66_CASES_KEPT` | 上一轮台账 66 例, 本轮一例未丢 |
| `NO_NEW_CASE_ID_THIS_ROUND` | 本轮不新增用例编号(A 要求并入现有 A16) |
| `CASE_CHECKS_NEVER_SHRINK` | 逐例 checks 只增不减; 变多的只有 A16(+2) |
| `REVERSE_FAMILY_COUNT_20` / `REVERSE_META_OK_20_LINES` / `REVERSE_NO_META_FAIL` | 20 族全部 META-OK |
| `ARD123_BEHAVIOUR_UNCHANGED_ON_PROBES` | A 原版四探针中 ARD1/ARD2/ARD3 那三条的 6 个观察量在 `e1d7f3d` 与本轮逐项相同 |
| `NOT_TOUCHED_diag_relative.h / diagnosis.c / motor.c / sensor_service.c` | 已收口的三项没有被重开 |
| `OLD_EVIDENCE_AND_IMAGES_UNTOUCHED` | 上一轮/被审轮的证据目录与两版旧镜像文件一个都没改 |

计数口径的自伤与修正(照实登记): `META-OK` 在日志正文里也作为词出现, 子串计数是 21
而判定行是 20。判据改成按 `^\s*<家族>\s+\|\s*META-OK` 取行, 并额外保留一条
`REVERSE_META_OK_COUNT_NOT_SUBSTRING_TRAP`(断言两种计数确实不相等), 防止以后有人再按子串数。

## 4. 打印预算(本轮改的是判据口径, 不是报文)

本轮**没有**新增或改动任何 `rt_kprintf` 格式串(`ADDED_KPRINTF_FORMATS_AS_EXPECTED`
= 0 个)。但被审那一版的三条 why 字面量
(`ctx-lock-publish` / `config-write-rolled-back` / `config-write-no-rollback`)随回滚一起
消失, 新增两条(`cfg-lock-commit` / `ctx-lock-commit`)。

上一轮的宽度表把手抄不认识的实参一律按 26 B 兜底, 因此只能覆盖"本轮触及的调用点"。
本轮把宽度改成**当场从两份头文件的成员类型折算**(144 个成员, 同名字段取最大值, 只会
高估不会低估), 于是覆盖面扩到整份 `diag_relative.c`:

| 判据 | 结果 |
|---|---|
| `PRINT_ARGS_ALL_RESOLVED` | 没有任何实参落到 26 B 兜底(未识别实参 = 空) |
| `WHY_VALUE_SET_CLOSED` | why 的 31 处赋值右值只有字面量/`REL_JOB_WHY_*` 宏/转发/三元默认四类, 集合内最长 25 B |
| `COMMIT_PRINTS_WITHIN_120` | `rel_cmd_commit` 内全部调用点最宽 80 B(含源 LF) |
| `ALL_DIAG_RELATIVE_PRINTS_WITHIN_120` | 全文件所有调用点最宽 109 B, 没有一条超 120 B |
| `LEARN_STATUS_MAIN_LINE_UNWIDENED` | 上一轮已证的 learn_status 主行格式串原样保留 |

`host/` 与 `offline/` 里没有裸 NUL、格式串逐条严格 UTF-8 解码通过
(`NO_BARE_NUL_IN_FORMATS` / `FORMATS_STRICT_UTF8`)。

## 5. A 的验收句 → 工件对照

| A 的要求(逐字摘) | 证据 |
|---|---|
| 「修复 commit 的事务边界…使提交点后的两份内存发布不再失败」 | `implementation_contract.md` §2; `diag_relative.c` 的 `rel_cmd_commit` 尾段 |
| 「允许修改直接依赖的 project_config.c/.h 接口, 必须说明锁序与提交点」 | `project_config.h` 三条新声明 + 注释; `implementation_contract.md` §2.1/§2.2 |
| 「保留 rel_ctx 叶锁约束; 不在其内部调用会再取 cfg/motor 锁的函数」 | `rel_pending_publish_locked()` 只做赋值; `build/resource_stack_r7d2.txt` [5] 的链逐字 |
| 「REFUSED 时配置、候选、凭据全部与调用前相同; OK 时配置与候选完整一致」 | A16 ①②③⑤ + `a16_refusal` bits; `host/probe_compound_before_after.txt` |
| 「将本组合故障并入现有 A16…覆盖初始配置为空和已有有效配置两种情形」 | 本文件 §1 的 ②③(已有配置)与 ⑤(空配置) |
| 「加入能检出该不变量破坏的负控, 并证明注入确实命中、编译成功、业务断言失败」 | 本文件 §2; `host/reverse_run_full.txt` 的 `INJECTED`/`META-OK` 行 |
| 「保留原 66 项及 19 族的业务含义; 复跑原版四探针、完整主机链与负控」 | 本文件 §3; `host/probe_four_before_after.txt` |
| 「所附探针在当前代码上必须先复现 FAIL, 再对修复版核验不变量」 | `host/probe_compound_before_after.txt`(修前 run_rc=1 FAIL, 逐 OBS 值与 A 包内原文一致) |
| 「如果新设计消除了回滚步骤, 按等价故障点验证拒绝无副作用」 | `implementation_contract.md` §3 的"ctx 臂本轮打不到" + A16 ②⑤ + 四探针第 4 条 |
| 「改动的打印仍按最宽实参核验每次 ≤120 B…无裸 NUL、UTF-8 完整」 | 本文件 §4 |
| 「完成后离线构建最终候选, 更新 BIN/AXF/MAP 与资源记录; 不要覆盖旧失败证据或旧镜像」 | `reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/`(新目录)+ `build/resource_stack_r7d2.txt`; `OLD_EVIDENCE_AND_IMAGES_UNTOUCHED` |
| 「在开发分支提交推送, 按原惯例做远端 blob 回读后交接」 | 见本轮 handoff 与推送后的 `remote_readback` 回执 |
