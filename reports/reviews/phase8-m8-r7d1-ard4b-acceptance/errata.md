# 勘误登记（A 签收轮 2026-11-05；两处文档口径 + 一处产品注释漂移）

来源：`NS800_R7D1_ARD4B_Acceptance_00718b4_20261105.zip`（24196 B，sha256
`49681a0f8dc92fff5b55c49a67afa09c37fbaae5b4a516bc17e87cd9fe8fa97f`），
`IDENTITY.json.verdict = ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA`，
`blocking_product_findings = 0`，`documentation_errata` 两条原文：

> 1. Profile validation precedes cfg acquisition; full config range check executes under cfg;
>    ctx acquisition is still fallible after begin succeeds.
> 2. Successful commit preserves both zero and learn credentials; documentation saying learn is
>    invalidated is incorrect.

A 同时写明：「交接归档时同步即可；本回执已经明确正确口径，不为此再开产品返修或构建轮。」
本轮因此**只改文档**：产品 3 件与主机 2 件字节与镜像 `23691e8d…` 一字未动，未重新构建（判据
`PRODUCT_AND_HOST_BYTES_UNCHANGED` / `NO_NEW_BUILD_*` 见 `proof/acceptance_checks.txt`）。

## 勘误 1：校验与取锁的先后

旧文字（逐字保留，作废）：

- `implementation_contract.md` §2 代码块注释：
  `fin = project_config_rprof_begin(&cand);   /* 校验 + 取 cfg 锁: 本轮最后一个可失败步骤 */`
- `implementation_contract.md` §2.2：
  「`begin()` 的三步里**只有取锁会失败**; 校验(`rel_profile_validate` + 用临时副本过
  `config_in_range`)全部在取锁之前完成, 失败时既不持锁也不改共享状态。」
- `phase8-m8-r7d1-ard4b-commit-txn.md` §2：「`begin()` 在取锁之前完成全套校验…」，函数声明旁注
  「/* 唯一会失败的一步 */」
- handoff JSON：`fix_oneliner` 内「begin(校验+取 cfg, 唯一会失败的一步…」

源码事实（`project_config.c:503-510`，逐行）：

```c
if (p == RT_NULL) return -RT_EINVAL;                        /* 锁外 */
if (rel_profile_validate(p, RT_NULL) != RT_EOK) return -RT_EINVAL;   /* 锁外 */
e = cfg_take_checked();
if (e != RT_EOK) return e;                                  /* 没拿到锁: 未持锁, 状态未改 */
tmp = cfg;                                                  /* ---- 以下在 cfg 锁内 ---- */
tmp.rprof = *p;
if (!config_in_range(&tmp)) { cfg_release(); return -RT_EINVAL; }
return RT_EOK;                                              /* 持锁返回 */
```

即：profile 自身的校验在取锁前，**整套配置的范围复核在 cfg 锁内**；范围不过先 `cfg_release()`
再返回，所以"失败时不持锁、不改共享状态"仍然成立。另外 begin 成功之后 `rel_ctx_take()` 仍会失败，
所以 begin **不是**最后一个可失败步骤。

正确总口径（与 A §3 一致）：**全部校验和所有可能失败的取锁，都在两份共享状态写入之前完成。**
范围复核放在 cfg 锁内是当前实现，不为迁就旧文字搬到无锁读 `cfg`（那会变成数据竞争）。
已改写位置：`implementation_contract.md` §2 代码块、§2.2 合同段、新增 §8；handoff md §2 与 §8；
handoff JSON 的 `fix_oneliner`/`commit_point`/`fallible_steps_before_commit_point`。

## 勘误 2：commit 成功后的两枚凭据

旧文字（作废）：结果合同表 `OK` 行写「learn 作废、zero 保持」（contract 与 handoff md 各一处）。

源码事实（`diag_relative.c:2215`）：

```c
rel_pending_publish_locked(&cand, -1, -1);   /* 提交点 1: 已在 ctx 内, 只做赋值 */
```

`rel_pending_publish_locked(cand, set_zero, set_learn)`（`diag_relative.c:1468`）对 `set_zero/set_learn`
只在 `>= 0` 时才写旗标，提交点传的是 `-1, -1` ⇒ **两枚凭据都保持原值**。
A 的独立观察与此一致（`creds_kept=1`）。作废 learn 凭据发生在 **learn 受理成立那一刻**（`diag_relative.c:1647`，上一轮
ARD4 的既有行为），不在 commit 成功时。

正确口径：**`OK` 时 zero/learn 凭据保持原值，配置与候选同版。** 本轮不引入"成功后额外作废凭据"
的新需求（A 明确）。

## 同源漂移，本轮不修（登记）

`project_config.h:82-83` 的接口注释：

```
*   begin(p)          : 校验(rel_profile_validate + 整套 config_in_range, 都在副本上算),
*                       然后取 cfg 锁; 返回 RT_EOK 时**锁处于持有状态**, 返回错误时未持锁。
```

`都在副本上算 …… 然后取 cfg 锁` 这半句把 `config_in_range` 说成在取锁之前，与勘误 1 同源。
它是**产品源文件里的注释**：改它就要重新编译，而 A 明确不为此开产品返修或构建轮，签收候选
`23691e8d…` 的字节必须保持。因此本轮只登记、不改文件，留到下一次合法触产品源码的轮次随该轮
构建一起修（该轮的判据会把注释与实现的一致性重新钉住）。
