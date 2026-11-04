# R7D1 返修复审：ARD1–ARD3 收口，ARD4 仍需修复

复审日期：2026-11-05。受审提交及父提交见 `IDENTITY.json`。总体结论：**CHANGES_REQUIRED，仅 ARD4 留有阻断项（P2）**。

这次修复有实际效果：原版四条 A 探针在 `e1d7f3d` 全部 PASS；现有 66 个主机用例全部 PASS，19 族反向链全部 META-OK，均由 A 独立复跑。ARD1 的 NULL 拒绝、ARD2 的 zero 延迟清理、ARD3 的静止门前置，可按本轮检查范围收口。

问题集中在 ARD4 新增的回滚失败出口。这不是扩大需求，上一份复审已明确要求：**REFUSED 时，配置、候选和凭据均保持原值；不能保留半份发布。** 原四条探针只击中了候选发布失败；本轮 A16 还覆盖了“配置失败、回滚成功”，但两者都没有覆盖“配置失败、回滚也失败”。

## 1. 唯一阻断项：commit 回滚仍可失败

产品位置：`rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications/diag_relative.c:2195–2209`（行号对应 `e1d7f3d`）。

当前顺序：先 `rel_pending_write(&cand, -1, -1)` 发布候选，再 `project_config_set_rprof(&cand)` 写 RAM 配置。若后者失败，调用 `rel_pending_write(&prev, -1, -1)` 回滚；但回滚也需要一次可能失败的 ctx 取锁。该失败分支只设置 `why="config-write-no-rollback"`，最后仍输出 `commit REFUSED`。

独立反例全部调用真实产品 C，复用当前 host 桩中已经存在的故障注入设施；没有修改产品函数或替换配置算法：

1. 实际执行 zero、learn 到 DONE、静止后 commit 成功，建立 id=1 的配置与候选。
2. 让第二次 commit 的 gate、两次快照、候选发布取锁全部成功。
3. 只在配置发布时注入 cfg 取锁失败，同时让随后回滚所需的 ctx 取锁失败。
4. 解除注入后比较配置、候选和凭据。

原始输出摘录（完整日志在 `proof/compound_probe/`）：

```text
[DIAG] commit REFUSED: (config-write-no-rollback) rc=-12
OBS|COMMIT_CFG_AND_ROLLBACK_FAULT|refused=1 no_rollback=1 cfg_changed=0 pending_changed=1 creds_kept=1 cfg_id=1 pending_id=2 ctx_takes=5 cfg_steps=3
REVIEW|REFUSED_COMMIT_PRESERVES_ALL_STATE_ON_ROLLBACK_LOCK_FAILURE|FAIL
```

编译 rc=0，运行 rc=1，准备条件全部通过。结论是：命令报拒绝，但候选已经改变，且保留两枚有效凭据；不满足原事务合同。这里没有证明新配置已在保护判据生效——本反例恰好读到 RAM 配置未变。没有 Flash 保存或真实硬件动作。

把“回滚失败”具名披露是有用的，但披露本身不能替代恢复事务一致性，也不能把该分支登记为完整修复。

## 2. 限定的离线返修任务

继续原 R7D1 离线授权，直接完成下面工作，不再开准备轮、不需要追加离线批准。设备动作全部为 0。

- 修复 commit 的事务边界。不要只把两个可失败写入再换一次顺序，也不要依赖另一次可能失败的取锁来保证撤销。可在修改共享状态前，按既有锁序拿齐所需锁并完成全部可失败检查，使提交点后的两份内存发布不再失败；或采用具有同等原子结果的设计。允许修改直接依赖的 `project_config.c/.h` 接口，必须说明锁序与提交点，不引入无关框架。
- 保留 `rel_ctx` 叶锁约束；不在它内部调用会再取 cfg/motor 锁的函数，不在持相关锁期间走 UART、sleep、Flash；不以无界重试、强行解锁或吞掉错误替代正确事务。
- 完成后只有两种结果：REFUSED 时配置、候选、凭据全部与调用前相同；OK 时配置与候选完整一致。不得仅改报文为 FAILED 或改验收口径来接受半份发布。
- 将本组合故障并入现有 A16 或等价业务回归：覆盖 cfg 发布失败且 ctx 回滚不可用，初始配置为空和已有有效配置两种情形。加入能检出该不变量破坏的负控，并证明注入确实命中、编译成功、业务断言失败。
- 保留原 66 项及 19 族的业务含义；复跑原版四探针、完整主机链与负控。可改变内部取锁顺序，但故障注入不能因锚点失效而假通过。所附探针在当前代码上必须先复现 FAIL，再对修复版核验不变量；如果新设计消除了回滚步骤，按等价故障点验证拒绝无副作用。
- 改动的打印仍按最宽实参核验每次 ≤120 B（含源 LF），无裸 NUL、UTF-8 完整。完成后离线构建最终候选，更新 BIN/AXF/MAP 与资源记录；不要覆盖旧失败证据或旧镜像。
- 在开发分支提交推送，按原惯例做远端 blob 回读后交接。未改的 ARD1–ARD3 不重开需求；若事务调整影响它们，只做必要回归。

## 3. 一键复现

解压本包，确保 Python 3 和 GCC 可用，使用受审分支的实际仓库：

```text
python reproducer/run_commit_rollback_probe.py --repo-root D:/ns800 --out C:/r7d1_ard4_followup
```

`--out` 必须在仓库外。rc=0 表示这条断言通过，rc=1 表示业务断言失败，rc=4 为环境或准备失败。脚本只编译和执行 host C，不打开设备。编译输入直接包含仓库当前 `tests/host/rel_main.c`，再由该 TU 编入真实产品 C。

Linux 沿用上一轮兼容方式：仅为未使用的 Win32 include 提供仓外空 `windows.h`；没有替换产品算法或 RTOS 桩。本次 GCC 14.2 仍报告旧 fixture `adxl345_read_mg()` 的一条 misleading-indentation 警告，原文保留。主机模型的取锁失败不等于真实 RTOS 抢占、物理电平或保护时延证据。

## 4. 已独立确认与保持的边界

| 检查 | 结果 |
|---|---|
| e1d7f3d 的父提交 | 4d503103cf3f1eb6f5113b10cc8683954d510ac6 |
| 上轮 A 原版四探针 | 4 PASS / rc=0，脚本与 C 原件未改 |
| 现有主机链 | 66/66 PASS，19/19 META-OK，runner rc=0 |
| 本次 ARD4 组合故障 | FAIL / rc=1 |
| 新 BIN | 297476 B；SHA256 165d8bdc708b042125d99db88f56a62ddca635d30202e80abe96c148e35bc478 |
| AXF 的 Flash 装载段与 BIN | 逐字节相等 |
| A 的产品源码改动 / 设备动作 | 0 / 0 |

本次从远端 Git 拉取代码并在独立 worktree 复审；网页入口未能获取 commit 内容，结论没有依赖网页摘要。A 未重跑 ARM/Keil 产品构建，未独立重做对方全部 283 条远端清单，亦未把静态调用图余量当作带载栈实测。

板端仍只承认历史 b502 / `502dca5d…` 的逐字节回读证明。`df7584c3…`、`165d8bdc…` 及后续修复候选保持 NOT_ON_BOARD。`polarity_measured=false`、`core_protection_verified=false` 不变；R7F5A 断电回报仍为 PENDING_OPERATOR_REPORT。演示端、历史现场原件和旧批准账保持原样。本件不构成任何硬件授权。
