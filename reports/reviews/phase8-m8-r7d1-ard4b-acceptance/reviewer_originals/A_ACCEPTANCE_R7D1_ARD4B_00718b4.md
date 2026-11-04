# R7D1 / ARD4b 离线代码签收

日期：2026-11-05。受审 tip：`00718b4`，完整提交身份见 `IDENTITY.json`。

**结论：本轮离线代码复审通过，ARD4 收口；承接已收口的 ARD1–ARD3，R7D1 异步学习的本轮离线开发可以签收。无产品代码阻断项。** 这份回执附两组文档勘误，不要求再返修产品、重构建或开登记轮。签收范围不包含真实硬件效果。

## 1. ARD4 的事务问题已从结构上消除

`diag_relative.c:2207` 起的实际顺序为：持有 motor 静止事务 → `project_config_rprof_begin()` 持 cfg 返回 → 有界取得 rel_ctx → 两份状态赋值 → 依次归还 ctx、cfg，最后退出 motor 事务。

- cfg begin 失败：尚未改候选、凭据或配置。
- cfg 已持有、ctx 取锁失败：显式归还这次取得的 cfg 锁，三份状态不变。
- 两把锁拿齐之后：`rel_pending_publish_locked()` 与 `project_config_rprof_publish_locked()` 只做内存赋值，不再取锁、校验、打印或进行 I/O。
- 两份赋值期间 cfg 与 ctx 都处于持有状态；先放 ctx 时两份写入已经全部完成。不存在先发布一份、再依赖另一次可失败操作完成或回滚另一份的出口。

锁序维持 `mot → cfg → ctx`，ctx 内没有新增取其他锁的调用。新增配置窄接口只由本次 commit 按 begin/end 合同使用；既有 `project_config_set_rprof()` 的实现没有改动。

这里的“一致提交”指正常执行下由这些锁保护的 RAM 状态，不含断电原子性、Flash 持久化或硬件异常恢复。cfg 取锁仍沿用既有 `RT_WAITING_FOREVER`，ctx 等待是原来的 20 ms；不能把学习作业的 12 s deadline 写成整个 commit 的最坏耗时上界。

## 2. A 独立重跑的证据

| 检查 | 结果 |
|---|---|
| 上轮 A 原版四条产品 C 探针 | 4/4 PASS，rc=0 |
| 上轮 ARD4 组合故障探针 | PASS，rc=0；三份状态未变 |
| 当前主机全链 | 66/66 PASS，runner rc=0 |
| 当前反向注入 | 20/20 META-OK，20 条 INJECTED |
| 新 BIN/AXF/MAP/调用图 | Git blob 的大小、SHA256 与归档 manifest 对应一致 |
| AXF Flash 装载段与 BIN | 逐字节相等 |
| 产品文件 rt_kprintf 格式串序列 | 相对父提交未改；A12 同链通过 |
| A 的产品源码改动 / 设备动作 | 0 / 0 |

组合探针的当前观察为：

```text
refused=1 no_rollback=0 cfg_changed=0 pending_changed=0 creds_kept=1
cfg_id=1 pending_id=1 ctx_takes=3 cfg_steps=3
```

该探针原先设置的 ctx 第 5 次取锁故障本轮没有命中：cfg 在更早阶段已失败，回滚步骤也已删除。其 PASS 只证明该 cfg 失败分支无副作用，不能单独拿来声称所有组合故障均已覆盖。

等价的 ctx 失败点由原四探针第 4 条及 A16 的已有配置/空配置两组承担：cfg 已取得后，最后一次 ctx 取锁失败，结果 REFUSED、三份状态不变，cfg 与 ctx 均不滞留。A16 还将故障序号后移一步检验锚点特异性；RDV9、RDV10 分别破坏“发布必须在拿齐锁之后”和“不可吞掉 ctx 失败”，均由实际业务断言检出。这些源码与重跑结果共同支持签收。

原探针 C 与 Python driver 保持上一份 A 包字节身份。完整输出、编译记录、源绑定和镜像身份放在本包 `proof/`、`IDENTITY.json` 中。测试运行在 GCC 14.2 与仓库既有 host 桩上；Linux 仅为未使用的 Win32 include 添加仓外空 `windows.h`。旧 fixture 的一条 misleading-indentation 警告仍保留，A 的 Linux 重跑不称为 0 警告。

## 3. 两组文档勘误，以源码为准

下面是文字不准确，不是新的产品缺陷。交接归档时同步即可；本回执已经明确正确口径，不为此再开产品返修或构建轮。

1. **校验与取锁的顺序。** 用户回报、`implementation_contract.md` §2/§2.2 和 `project_config.h` 的说明将完整校验说成取 cfg 锁之前，且把 begin 称作最后一个可失败步骤。实际 `project_config.c:498` 的 begin 先判空并做 `rel_profile_validate`，再取 cfg 锁，在锁内复制当前 cfg、替换临时 rprof、执行 `config_in_range`；范围拒绝会先放 cfg 锁。begin 成功后，commit 还可能在 ctx 取锁处失败。正确总口径是：**全部校验和所有可能失败的取锁都在两份共享状态写入之前完成。** 完整配置的范围复核放在 cfg 锁内符合当前实现，不应为迎合旧文字而搬到无锁读取 cfg 的位置。
2. **成功后的凭据。** `implementation_contract.md` §2 结果表把 OK 写成“learn 作废、zero 保持”。实际提交点调用 `rel_pending_publish_locked(&cand, -1, -1)`，两枚凭据均保持原值；独立观察与既有行为一致。应改成“zero/learn 凭据保持原值，配置与候选同版”。本次不引入成功后额外作废凭据的新需求。

## 4. 签收身份与后续边界

签收候选 BIN：297452 B，SHA256：

```text
23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963
```

A 核对了归档构建日志的身份和 0E/0W 结果行，未在本机重跑 ARM/Keil 构建；未把静态调用图余量当作带载最坏栈实测。本次检查范围与清单结果以 `IDENTITY.json` 为准，不冒称完整重做了对方 271 条远端回读。

`23691e8d…`、`165d8bdc…`、`df7584c3…` 全部保持 NOT_ON_BOARD。板端只承认历史 b502 / `502dca5d…` 的既有逐字节回读证明；本次没有新核验当前板端状态。`polarity_measured=false`、`core_protection_verified=false` 保持；R7F5A 物理断电回报仍 PENDING_OPERATOR_REPORT。

本回执仅作离线签收与归档依据，不授权开串口、DAP、供断电、下载、复位、分析仪采集或任何运动/配置动作。回现场后的范围另行定稿，不结转旧额度。
