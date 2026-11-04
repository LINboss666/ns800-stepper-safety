# M8-R7D1 ARD4b 签收轮交接（A 判 ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA；设备动作 0，未重构建）

- 轮次：`phase8-m8-r7d1-ard4b-acceptance`　日期：2026-11-05　分支：`feature/m8-r7d1-async-learn-offline`
- 本轮性质：**只改文档与证据**。产品 3 件、主机链 2 件、四件镜像产物一字未动；没有重新构建。
- 机器可读数：`reports/agent_handoffs/phase8-m8-r7d1-ard4b-acceptance.json`
- 判据：`reports/reviews/phase8-m8-r7d1-ard4b-acceptance/proof/acceptance_checks.txt`（`TOTAL=51 FAIL=0`）
- 勘误逐字对照：`reports/reviews/phase8-m8-r7d1-ard4b-acceptance/errata.md`

## 1. A 的签收

`NS800_R7D1_ARD4B_Acceptance_00718b4_20261105.zip`（24196 B，sha256 `49681a0f…fa97f`；内层清单 19 条
逐条复算 19/19、zip 共 20 个成员、清单不含自身；20 件原件已按原路径逐字节入库到
`reviewer_originals/`，包括 A 自己的 `SHA256SUMS.txt`）。

```
verdict                        = ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA
review_commit                  = 00718b49e65546aa0bae84006059011d68284c4a (= 本地 HEAD = 远端 tip)
blocking_product_findings      = 0
closed_findings                = ARD1, ARD2, ARD3, ARD4
A 产品改动 / 设备动作 / 重跑 arm 构建 / 重放我方 271 条回读 = 0 / 0 / false / false
```

A 独立重跑并记录：原版四探针 4/4 PASS、ARD4 复合探针 PASS（`refused=1 no_rollback=0
pending_changed=0 creds_kept=1 ctx_takes=3 cfg_steps=3` —— 与我方归档的 OBS 行一字不差）、
全链 66/66、反向 20/20 META-OK（20 条 `INJECTED`）、BIN/AXF/MAP/调用图 blob 身份与 manifest 一致、
AXF 的 Flash 装载段逐字节等于 BIN、`rt_kprintf` 字面量序列相对父提交未变。
A 也独立认定复合探针的 ctx 臂本轮未命中（`ctx_fault_arm_reached=false`），等价覆盖=四探针第 4 条 +
A16 两世界 —— 与交付轮的如实登记一致。

## 2. 两处勘误已同步（以源码为准）

| # | 旧文字（作废，逐字留在 `errata.md` 与两份 §8） | 源码事实 | 现在的口径 |
|---|---|---|---|
| 1 | 「`begin()` 的三步里只有取锁会失败；校验（`rel_profile_validate` + 用临时副本过 `config_in_range`）全部在取锁之前完成」「本轮最后一个可失败步骤」 | `project_config.c:503-510`：判空与 `rel_profile_validate` 在锁外；`cfg_take_checked()` 之后才在**锁内** `tmp = cfg; tmp.rprof = *p; config_in_range(&tmp)`，不过则先 `cfg_release()` 再返回 | 全部校验与所有可能失败的取锁都在**两份共享状态写入之前**完成；begin 成功后 `rel_ctx_take()` 仍会失败。范围复核留在 cfg 锁内是当前实现，不搬到无锁读 `cfg`（那才是数据竞争） |
| 2 | 结果表 `OK` 行「learn 作废、zero 保持」 | `diag_relative.c:2215` 提交点调 `rel_pending_publish_locked(&cand, -1, -1)`；该函数（`:1468`）对 `-1` 不写旗标；作废 learn 凭据发生在 learn 受理那一刻（`:1647`） | `OK` 时 zero/learn 两枚凭据**都保持原值**，配置与候选同版；本轮不引入"成功后额外作废凭据"的新合同 |

安全性未变：两处都属文字不准，`REFUSED 无副作用 / OK 两份同版` 的合同与实现都没动，因此按 A 的要求
不重开产品返修、不重新构建（判据 `NO_PRODUCT_OR_HOST_CHANGE_THIS_ROUND`：相对签收 tip 产品与主机链路径
被改动数 = 0；`PRODUCT_HEADER_BYTES_UNCHANGED_SINCE_TIP`；候选 BIN 仍是 `23691e8d…` 297452 B）。

改动面（28 件，全在文档/证据白名单内）：`implementation_contract.md`（§2 代码块、§2.2 合同段、新增 §8）、
交付轮 handoff 的 md（§2、新增 §8）与 json（三处字段 + 新增 `acceptance` 块）、本签收轮的
`reviewer_originals/`（20 件）、`errata.md`、`proof/`、`tools/`、`run_record.md` 新增 §18
（§16/§17 原文未删）、`.gitattributes` 一条 `-text` 规则、两份 SHA256SUMS 重算。

## 3. 本轮登记但**不修**的一处

`project_config.h:82-83` 的接口注释同样把 `config_in_range` 说成发生在取 cfg 锁之前（与勘误 1 同源）。
它是产品源文件里的文字：改它需要重新编译，而 A 明确「不为此再开产品返修或构建轮」。
因此本轮只在 `errata.md`、run_record §18、本文件与 handoff JSON 的 `deferred_this_round` 里点名登记，
留到下一次合法触产品源码的轮次随该轮构建一起修；那一轮的判据会把"注释与实现一致"重新钉住。

## 4. 边界（与签收前一致，且 A 重申）

本轮 0 次设备动作：不开串口、不枚举/连 DAP、不烧录、不回读、不复位、不启动逻辑分析仪、
不 arm/start/运动、不做真实学习、不写配置、不供断电。
`23691e8d…`（签收候选）、`165d8bdc…`、`df7584c3…` 全部保持 NOT_ON_BOARD；板端只承认历史
`b502 / 502dca5d…` 的既有逐字节回读证明，本轮未复核板端状态。
`polarity_measured=false`、`core_protection_verified=false` 保持；R7F5A 物理断电回报仍
`PENDING_OPERATOR_REPORT`；四条禁用声称继续禁止；本回执不构成任何硬件授权，
回现场后的新版上板范围另行定稿、不结转旧额度。无 merge / rebase / force-push / reset / clean。
