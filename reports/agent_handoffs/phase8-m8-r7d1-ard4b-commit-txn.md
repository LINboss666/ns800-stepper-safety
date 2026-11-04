# M8-R7D1 ARD4b 续修轮交接（提交点两阶段化；设备动作 0）

- 轮次：`phase8-m8-r7d1-ard4b-commit-txn`　日期：2026-11-05　分支：`feature/m8-r7d1-async-learn-offline`
- 被审件：`NS800_R7D1_ARD4_Followup_e1d7f3d_20261105.zip`（21347 B，sha256 `411ccf2d…d02c`，
  内层清单 19 条逐条复算 19/19、zip 共 20 个成员、清单不含自身）
- 基线 = 被审提交 `e1d7f3d`；构建与全部判据时 HEAD 就是它（`HEAD_IS_REVIEWED_COMMIT` PASS）
- 按 A 的授权原文（「Continue original offline authorization. No new registration or approval round.」）
  把实现、验收、归档合为一次交付，未开登记轮、未向操作者等批准。`hardware_actions_cap = 0`。
- 机器可读数：`reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.json`
- 事务合同：[implementation_contract.md](file:///D:/ns800/reports/reviews/phase8-m8-r7d1-ard4b-commit-txn/implementation_contract.md)
  用例与负控映射：[case_mapping.md](file:///D:/ns800/reports/reviews/phase8-m8-r7d1-ard4b-commit-txn/case_mapping.md)

## 1. 这一条到底坏在哪

被审版 `diag_profile commit` 的尾巴是三段"每个都可能失败"的写：

```
rel_pending_write(cand)          取 rel_ctx —— 会失败
project_config_set_rprof(cand)   取 cfg     —— 会失败
rel_pending_write(prev)  回滚    再取 rel_ctx —— 也会失败
```

A 把 cfg 与 ctx 两个故障同时装上，命中"配置写失败 **且** 回滚取锁失败"：命令报 `REFUSED`，
可候选已经是新版（`pending_id=2`）、配置还是旧版（`cfg_id=1`）、learn/zero 两份凭据都还有效。
保护判据读的就是配置，这等于"报拒绝，实际半份发布"。我在 `e1d7f3d` 的仓外逐字节副本上用
**同一份 A 的 reproducer** 复现，OBS 行与包内原文一字不差（`run_rc=1`）。

## 2. 改法：把提交点搬到"两把锁都到手"之后

新增三条窄路径（A 明确允许动 commit 的直接配置事务依赖）：

```c
rt_err_t project_config_rprof_begin(const diag_rprof_t *p);           /* 唯一会失败的一步 */
void     project_config_rprof_publish_locked(const diag_rprof_t *p);  /* 只赋值 */
void     project_config_rprof_end(void);                              /* 只放锁 */
```

`begin()` 在取锁之前完成全套校验（`rel_profile_validate` + 用临时副本过 `config_in_range`），
取锁失败时既不持锁也不改共享状态；成功即持 cfg 返回，调用方必须成对 `end()`。
commit 变成：

```
begin(cand)  失败 -> REFUSED(cfg-lock-commit)     共享状态一字未动
rel_ctx_take() 失败 -> rprof_end(); REFUSED(ctx-lock-commit)   共享状态一字未动
【提交点】rel_pending_publish_locked(&cand) + project_config_rprof_publish_locked(&cand)   两次赋值, 不会失败
放 ctx, 放 cfg
```

**回滚与 `prev` 局部副本整体删除** —— 不再需要"靠另一次可能失败的取锁来撤销"。
合同因此收敛到 A 要的两种结果：

| 结果 | 配置 | 候选 | 凭据 | 锁 |
|---|---|---|---|---|
| `REFUSED` | 与调用前逐字节相同 | 与调用前逐字节相同 | 不变 | 都不持 |
| `OK` | 与候选 **memcmp 相等**（A16 ① 直接逐字节比） | 同上 | learn 作废 | 已归还 |

锁序 `mot(跨命令持有) → cfg → rel_ctx` 未变；`rel_ctx` 仍是叶锁（临界区内只有赋值）；
持 cfg 期间只多了一次有界等待（`REL_CTX_TAKE_MS=20ms`）加两次赋值；
无界重试 / 强行解锁 / 吞错误一个都没有；错误身份照旧可辨（`-RT_EINVAL` / `-RT_ETIMEOUT` / `-RT_EBUSY`）。
旧的 `project_config_set_rprof()` 保留未动。

## 3. 一条如实登记：A 的复合探针有一个臂本轮打不到

修后同一份探针 `ctx_takes 5 → 3`：新设计只取 3 次 ctx，且 cfg 先失败，**回滚那一步已不存在**，
所以探针的 ctx 臂不可能命中。它的 `PASS` 只证明"cfg 失败时三份状态未动"，不证明
"配置已写成而候选发布失败"这一组合（后者在新设计里不可能发生）。等价故障点由两处承担：

1. A 原版四探针第 4 条（ctx 第 4 次失败，此时 cfg 已持锁）——同一注入仍 `refused=1`、`cfg_changed=0`，
   拒绝身份由 `ctx-lock-publish/-12` 变为 `ctx-lock-commit/-117`（顺序真换了，不是只改报文）；
2. A16 的 ②/⑤ 段（已有配置与空配置两种世界各测一遍）。

另有 A16 ④ 的锚点特异性检查：把注入点后移一步，结果必须是正常 OK —— 防止"卡在哪都红"。

## 4. 验收

| 判据 | 数值 |
|---|---|
| 主机链正常 | `build_rc=0 warnings=0`，`cases=66 pass=66 fail=0 executed=66`（上一轮 66 例一条未丢，本轮不新增编号） |
| 逐例 checks | 只增不减：A16 `35 → 37`，其余逐例相同 |
| 负控 | **20 族**全 META-OK（原 19 族一条未删；新增 `RDV10`；`RDV9` 沿用原名换注入体），20 条 `INJECTED` 行 |
| A 原版四探针 | `e1d7f3d` 与本轮都是 4/4 PASS；ARD1/ARD2/ARD3 那三条的 6 个观察量逐项相同 |
| A 复合探针 | 修前 `run_rc=1` FAIL → 修后 `run_rc=0` PASS（reproducer 一字未改） |
| 离线独立复算 | `TOTAL=113 FAIL=0` |
| 打印 | 本轮新增/改动格式串 0 个；全文件所有调用点最宽 109 B、`commit` 路径最宽 80 B（含源 LF，预算 120 B）；why 集合最长仍 25 B；无裸 NUL、严格 UTF-8 |

## 5. 构建与资源（一次，未上板）

`UV4 -r` 单目标 `rt-thread-motor-m1-candidate`，rc=0，`0 Error(s), 0 Warning(s)`，V6.24，
`Code=209490 RO=87378 RW=2196 ZI=85944`；AfterMake 只有 `fromelf --bin`。

| 产物 | 字节 | sha256 |
|---|---|---|
| BIN | 297452 | `23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963` |
| AXF | 1790048 | `0f421595ac940863c4d38a2791f31bc8811fc3b8fa40062782f2ac7505c317f6` |
| MAP | 2148432 | `e2d8d081958c84bb7f760bd5011a4b0ce962bb5dc8b6c0dff1cc9084efc0872b` |
| 调用图 | 1101259 | `a7090b6eed89f32a0abf4c7ec395cb1a421b909f1c25c436b64f302a02ff0c34` |

归档进**新目录** `reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/`；被审那版仍是 `165d8bdc…`、
再上一版仍是 `df7584c3…`，两版旧镜像与全部旧证据一字未动（判据 `REVIEWED_IMAGE_UNCHANGED` /
`OLDER_IMAGE_UNCHANGED` / `OLD_EVIDENCE_AND_IMAGES_UNTOUCHED`）。

对账（换算法，不沿用不恒成立的等式）：`Code+RO+RW = −28`，BIN `−24`，差 +4 由装载段构成解释到残差 0
（ER_IROM1 −28、两份 map 相减独立复算 == −28；对齐空隙 −4；RW_DTCM 压缩载荷 +8）。
逐对象非零差分只有 `diag_relative.o`（Code+8 RO−42）与 `project_config.o`（Code+2），
其余 109 行 `.o` 与 49 行库成员逐字段相同；AXF 的 Flash 装载段与 BIN 逐字节相等（两版同一工具）。
栈：`rel_cmd_commit` 帧 608→496、子树 1556→1404，命令线程最深根回到 `rel_cmd_clear`（1460，余量 2636 B），
**没有任何子树变高**；`DIAG_THREAD_STACK` / `FINSH_THREAD_STACK_SIZE` / `REL_CMD_DEADLINE_MS`
三份值与基线 blob 相同 —— 上一轮为回滚新增的成本本轮收回，没有靠加大栈或延长超时换绿。

## 6. 本轮自伤（照实，不掩盖）

1. A16 ④ 第一次 FAIL：漏清 `g_ctx_take_calls`（夹具按"第 N 次之后"判），第一条 take 就超阈值 → 无关的
   `commit REFUSED: ctx lock`。这条恰好证明该段不是恒绿；加清零与注释后 66/66。
2. 派生的资源报告初版仍钉"非零差分只有 `diag_relative.o`" → 判据与对账两处 FAIL；改成两对象集合 +
   通用对账后重跑，错误一版被正确一版覆盖，未入库。
3. 派生的 manifest 工具初版带着上一轮的过期叙述（`+476/+472`、`build_r7d1r.log`、旧基线数），
   且把两个常量的所在文件写错（运行即 `FileNotFoundError`）；现全部改为当场派生，锚点找不到就抛。
4. 打印折算的 26 B 兜底把 4 条既有 `show` 行误报超预算；换成按头文件类型折之后实测最宽 109 B。
   判据口径修正，产品打印一字未改。
5. `META-OK` 子串计数 21 与判定行 20 不一致；判据改按行取，并另加一条固定这个差异的判据。

## 7. 边界与状态（保持）

板上仍只承认历史 `b502 / 502dca5d…`；本轮新镜像与两版旧镜像一律 `NOT_ON_BOARD`。
`polarity_measured=false`、`core_protection_verified=false` 保持；四条禁用声称继续禁止；
R7F5A 物理断电回报仍 `PENDING_OPERATOR_REPORT`（未代填、未为关闭它接设备）；
演示端 V0.3、旧包、旧现场原件、旧批准账本未动；本轮不构成任何硬件授权。
未使用 merge / rebase / force-push / reset / clean。
