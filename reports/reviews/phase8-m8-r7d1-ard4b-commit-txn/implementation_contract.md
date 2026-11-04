# M8-R7D1 ARD4b — `diag_profile commit` 的事务合同(实现说明)

轮次 `phase8-m8-r7d1-ard4b-commit-txn`。被审件 `e1d7f3d`(A 的续修包
`NS800_R7D1_ARD4_Followup_e1d7f3d_20261105.zip`, 21347 B,
sha256 `411ccf2d7ca72af4784c903225828d4a00973a68a27f633d15792a2b42c1d02c`)。
本轮设备动作 0: 不烧录、不回读、不复位、不开串口、不动逻辑分析仪、不 arm/start/运动、
不做真实学习、不写配置、不供断电。板上仍是历史 `b502 / 502dca5d…` 的证明, 本轮新镜像
一律 **NOT_ON_BOARD**。

## 1. 被审的那条缺陷(A 的 ARD4, P2, `diag_relative.c:2195-2209`)

被审版的提交序列是:

```
持 mot 静止事务 -> 全部复核 -> rel_pending_write(cand)   [可失败: 取 rel_ctx]
                -> project_config_set_rprof(cand)        [可失败: 取 cfg]
                -> 若上一步失败: rel_pending_write(prev)  [可失败: 再取一次 rel_ctx]
```

于是存在一个组合: **配置写失败 + 回滚取锁也失败**。此时命令报 `REFUSED`, 但候选
(d_pending)已经换成新版、RAM 配置还是旧版, 且 learn/zero 两份凭据都还在 ——
半份发布。A 的组合探针原文(`proof/compound_probe/probe.stdout.txt`, 我在修前用
同一份 reproducer 逐字复现):

```
OBS|COMMIT_CFG_AND_ROLLBACK_FAULT|refused=1 no_rollback=1 cfg_changed=0 pending_changed=1
    creds_kept=1 cfg_id=1 pending_id=2 ctx_takes=5 cfg_steps=3
REVIEW|REFUSED_COMMIT_PRESERVES_ALL_STATE_ON_ROLLBACK_LOCK_FAILURE|FAIL   (run_rc=1)
```

## 2. 本轮的设计: 把"可失败"全部搬到提交点之前

提交点(commit point)定义为下面这一对内存写, 它们在**两把锁都已到手**之后发生:

```
fin = project_config_rprof_begin(&cand);   /* 校验 + 取 cfg 锁: 本轮最后一个可失败步骤 */
if (fin != RT_EOK) -> REFUSED(cfg-lock-commit)   共享状态一字未动
if (rel_ctx_take() != RT_EOK)                    /* 有界等待 REL_CTX_TAKE_MS=20ms */
    { project_config_rprof_end(); -> REFUSED(ctx-lock-commit) }   共享状态一字未动
rel_pending_publish_locked(&cand, -1, -1);   /* 提交点 1: 已在 ctx 内, 只做赋值 */
project_config_rprof_publish_locked(&cand);  /* 提交点 2: 已在 cfg 内, 只做赋值 */
rel_ctx_give_held();  project_config_rprof_end();
```

**没有第三步, 也没有回滚。** 两个提交写都不取锁、不校验、不做 I/O, 因此不可能失败,
也就不需要"靠另一次可能失败的取锁来撤销"。

结果合同收敛到 A 要求的两种:

| 结果 | 配置(RAM profile) | 候选 d_pending | learn/zero 凭据 | 两把锁 |
|---|---|---|---|---|
| `REFUSED` | 与调用前逐字节相同 | 与调用前逐字节相同 | 不变 | 不持锁(拒后 `held==0`) |
| `OK` | 与候选 **memcmp 相等** | 同上 | learn 作废、zero 保持 | 已归还 |

`OK` 那一行的"配置与候选完整一致"由主机链 A16 ① 直接逐字节比 `cfg == d_pending`,
不是从打印推断。

### 2.1 锁序(本轮实际形成的顺序)

```
motor 静止事务(mot 互斥量, 跨整个命令持有, 由 motor_calibration_static_begin/end 管)
   └─ cfg(project_config 内部互斥量, cfg_take_checked / cfg_release)
        └─ rel_ctx(叶锁 d_ctx_lock, REL_CTX_TAKE_MS 20ms 有界等待)
```

- 只在 `mot -> cfg -> ctx` 方向嵌套, 与既有方向一致; 反向一个都不新增。
- **rel_ctx 仍是叶锁**: 临界区里只有 `d_pending = *cand` 与两个旗标的赋值
  (`rel_pending_publish_locked`), 不调 motor/cfg/Flash/UART, 不 `rt_device_read`, 不 `rt_mdelay`。
- 持 cfg 期间: 一次**有界**的 ctx 取锁(最坏 20 ms, 不是 sleep)加两次结构体写入。
  这是 A 第 2 节「按既有锁序拿齐所需锁并完成全部可失败检查」的直接落实; 代价写在 §5。
- 没有无界重试、没有强行解锁、没有吞错误: ctx 取锁失败会显式 `project_config_rprof_end()`
  归还**自己刚拿的** cfg 锁, 然后按 `-RT_ETIMEOUT` 报 `REFUSED`。
- 错误身份保持可辨: ctx 未初始化 `-RT_EINVAL` / ctx 有界等待失败 `-RT_ETIMEOUT` /
  采集器占用 `-RT_EBUSY`(本轮把原先 `-RT_EIO` 的 `ctx-lock-publish` 换成了
  `-RT_ETIMEOUT` 的 `ctx-lock-commit`, 因为那一处现在真的是取锁超时)。

### 2.2 为什么必须动 `project_config.c/.h`

原来的 `project_config_set_rprof()` 把"校验 + 取锁 + 写入"绑成一个原子对外函数,
调用方无法在**取锁成功之后、写入之前**插入自己的第二份发布, 所以两个可失败写入必然
分居提交点两侧。A 明确允许改直接依赖(`FIX_SCOPE.json: product_scope` 含
"direct configuration transaction dependencies if needed")。

新增的是三条拆开的窄路径, 不引入任何框架:

```c
rt_err_t project_config_rprof_begin(const diag_rprof_t *p);   /* 会失败: 全套校验 + cfg 取锁 */
void     project_config_rprof_publish_locked(const diag_rprof_t *p);  /* 不会失败: 假定已持锁, 只赋值 */
void     project_config_rprof_end(void);                       /* 不会失败: 归还 cfg 锁 */
```

合同:
- `begin()` 的三步里**只有取锁会失败**; 校验(`rel_profile_validate` + 用临时副本过
  `config_in_range`)全部在取锁之前完成, 失败时既不持锁也不改共享状态。
- `begin()` 成功即**持 cfg 返回**, 调用方必须成对 `end()`; 这条约定写在头文件注释里,
  并且 A16 ⑥ 的"拒绝后不得仍持锁"断言把它钉在测试里。
- `publish_locked()` 不校验、不取锁, 因此不存在失败路径; 它只能在 `begin()` 成功后调用。
- `config_in_range(&tmp)` 用"现有配置 + 新 rprof"的合成分做范围复核, 避免把新阈值
  单独放进整套约束里误判(其它字段仍是旧值)。
- 既有 `project_config_set_rprof()` **保留未动**(其他主机用例与 `config_load` 路径仍在用)。

## 3. A 的组合探针本轮能打到哪里、打不到哪里(如实登记)

修后同一份探针(`host/probe_compound_before_after.txt`):

```
OBS|COMMIT_CFG_AND_ROLLBACK_FAULT|refused=1 no_rollback=0 cfg_changed=0 pending_changed=0
    creds_kept=1 cfg_id=1 pending_id=1 ctx_takes=3 cfg_steps=3
REVIEW|REFUSED_COMMIT_PRESERVES_ALL_STATE_ON_ROLLBACK_LOCK_FAILURE|PASS   (run_rc=0)
```

- 探针同时装两个故障: cfg 第 `cfg_steps` 次取锁失败 + ctx 第 5 次取锁失败。
- 修后命令只取 ctx **3** 次(gate / read / read), 且 cfg 先失败, 所以 **ctx 那一臂不可能命中**。
  这不是"变好了"的证据, 只是"回滚这一步在设计上已经不存在"的证据。
- 因此本件的 PASS 只覆盖"cfg 失败时三份状态未动"。"cfg 已持锁 + 发布取 ctx 失败"这一
  等价故障点由两处承担:
  1. A 原版四探针第 4 条(`COMMIT_POSTWRITE_CTX_FAULT`, ctx 第 4 次失败)—— 同一注入
     现在命中的是"配置已持锁、发布前取 ctx 失败", 仍产生 `refused=1`、`cfg_changed=0`,
     拒绝身份由 `ctx-lock-publish/-12` 变成 `ctx-lock-commit/-117`(见
     `host/probe_four_before_after.txt`);
  2. 主机链 A16 的 ②/⑤ 段(已有配置与空配置两种世界各测一遍)。
- 另有一条特异性检查 A16 ④: 把 ctx 故障点后移一步(commit 真的成功、没有 REFUSED),
  证明锚点不是"随便卡在哪一步都会绿"。

## 4. 与 ARD1–ARD3 的关系(不重开)

`diag_relative.h` 与 `diagnosis.c` 本轮一字未改(`offline/checks.txt` 的
`NOT_TOUCHED_*` 判据)。A 原版四探针里 ARD1/ARD2/ARD3 那三条的 6 个观察量
在 `e1d7f3d` 与本轮逐项相同(`ARD123_BEHAVIOUR_UNCHANGED_ON_PROBES`)。

## 5. 资源与代价(不靠加大栈或延长超时绕过)

| 项 | 被审 `e1d7f3d` | 本轮 | 差 |
|---|---|---|---|
| Program Size | Code=209474 RO=87422 RW=2196 ZI=85944 | Code=209490 RO=87378 RW=2196 ZI=85944 | +16 / −44 / 0 / 0 |
| BIN | 297476 B `165d8bdc…` | 297452 B `23691e8d…` | −24 B(构成对账残差 0) |
| `rel_cmd_commit` 帧 / 子树 | 608 / 1556 | 496 / 1404 | −112 / −152 |
| 命令线程最深根 | rel_cmd_commit 1556 | rel_cmd_clear 1460 | −96 |
| `DIAG_THREAD_STACK` / `FINSH_THREAD_STACK_SIZE` / `REL_CMD_DEADLINE_MS` | 4096 / 4096 / 12000 | 4096 / 4096 / 12000 | 0(三份值与基线 blob 相同) |

上一轮为回滚新增的那份 `diag_rprof_t` 局部副本 `prev`(类型尺寸 116 B, 取自 map 符号表)
与 `project_config_set_rprof` 的 `cfg_take_checked -> rt_mutex_take` 子树(948 B)一起消失,
所以本轮把上一轮登记的栈成本**收回**; 没有任何子树变高(`子树变高的命令根: 无`)。
逐对象非零差分只有 `diag_relative.o`(Code+8 RO−42)与 `project_config.o`(Code+2),
其余 109 行 `.o` 与 49 行库成员逐字段相同 —— 见 `build/resource_stack_r7d2.txt`。

## 6. 本轮没有主张的事

- 没有上板, 没有现场学习, 没有写配置到 Flash(`config_save` 是操作者显式动作)。
  本契约只说 RAM 侧两份发布的一致性; 断电后的持久化仍不在本轮证据范围内。
- `polarity_measured=false`、`core_protection_verified=false` 保持;
  R7F5A 的物理断电回报仍是 `PENDING_OPERATOR_REPORT`。
- armlink 的调用图数字是静态下界(不含异常/ISR 帧, 解析不了函数指针), 只用于两版比较,
  不是现场最坏栈实测。
