# 远端逐 blob 回读（M8-R3 交付）

- 远端：`https://github.com/LINboss666/ns800-stepper-safety.git` 分支 `feature/m8-sg-adc-relative-protection`
- 回读方式：在**新建的空 bare 仓**（仓库外 `D:/ns800_m8r3_scratch/remotecheck`）里
  `git -c http.proxy=… fetch --depth=1 --no-tags` 只取远端一次，之后所有字节都由
  `git cat-file blob <tip>:<path>` 从**远端取回的对象**读出再算 SHA256。本地工作树与本地对象库都不参与
  （新仓里本来就没有对象），所以这不是"拿本地哈希自证"。原始 stdout 逐字归档在
  `host/remote_readback_console.txt`；程序在 `host/remote_readback.py`。
- 本机执行环境：Windows 10.0.26200 / Python 3.12 / git for Windows，`PYTHONIOENCODING=utf-8`，
  `RBI_TIP` 传入期望头（短写会被拒：`TIP_MISMATCH`，必须整 40 位）。

## 1. 本轮交付（SCOPE_R3）—— 通过

被回读的交付头：`26809b9222b10abe894bdbd75a334d9b27531573`（本轮第 3 枚提交：代码 `805c29b` + 证据 `dc43923` + 回读程序返修 `26809b9`）
源码与验证链提交：`805c29bd73c4ea861c9b9c65f61e946aec9548e3`；document_base：`5c549f2af0740425c4014f374d064f77ee62d6b4`

```
SCOPE_R3|entries=1597 verified=1597 problems=0
REMOTE_READBACK_SUMMARY|tip=26809b9222|declared=2590|verified=2528|r3_entries=1597|r3_verified=1597
  |r3_problems=0|prior_problems=64|prior_hard=14|no_path=32|unlisted=4530
hardware_actions=0|sources_read_from=remote_git_protocol_only
```

本轮四张清单的键数（程序逐清单打印，非人工相加）：评审 `SHA256SUMS.txt` **1585** 键、固件 `SHA256SUMS.txt` **10** 键、
`field/plan.json` **2** 键（它的基准目录与两份清单不同，所以是独立键），合计 **1597**；
`build_manifest.json` 的 6 镜像 + 3 map 与固件清单**同键去重**，因此不额外计数 —— 也就是说它申报的每一件都在
固件清单里被逐字节验过。1597 条逐条满足"存在 + sha256 相等 + 声明字节数相等"。
`declared=2590` 是仓库内全部清单登记条目（含往轮）的键数，`verified=2528` 是其中真从远端取回并算对的条数。

本轮范围内**没有任何一条**未登记件，除了按规则本就不该被散列的四件：

```
   UNLISTED_R3 reports/agent_handoffs/phase8-m8-relative-protection-r3.json
   UNLISTED_R3 reports/agent_handoffs/phase8-m8-relative-protection-r3.md
   UNLISTED_R3 reports/firmware/phase8-m8-relative-protection-r3/SHA256SUMS.txt
   UNLISTED_R3 reports/reviews/phase8-m8-relative-protection-r3/SHA256SUMS.txt
```

- 两份 `SHA256SUMS.txt`：清单不得散列自己的字节（一条清单不能给自己的字节作证）；
- 两份交接件 `reports/agent_handoffs/phase8-m8-relative-protection-r3.{md,json}`：沿用仓库既有惯例，交接件不登记；
- `remote_readback.md`（本文件）：在清单生成规则里就被显式排除，理由同上 —— 回读记录不能散列自己正在写的字节。

## 2. 真跑这遍回读**改出来的两处判据缺陷**（不是先想好再跑的）

1. **反向"清单未登记"按候选键判定是错的**。R2 版的 `declare()` 用 `cps[0]`（清单目录基准展开后的第一个候选）做键，
   在 R2 那种"清单写目录相对路径"的场合键恰好等于真实路径；本轮清单改成仓库相对路径后，`cps[0]` 变成
   `reports/reviews/<R3>/reports/firmware/...` 这种拼接串，于是第一轮真跑出现
   `SCOPE_R3 entries=1594 verified=1594 problems=0` 却同时报 `unlisted_r3=1584` —— 把每一件已验证件都说成未登记。
   修：反向判定改按**解析后的真实路径**集合（`resolved`），修后本轮 `unlisted_r3=4`，且那 4 件正是上面按规则排除的。
2. **`build_manifest.json` 的 `map` 没有 `path` 字段**，回读只能按后缀猜，三条 map 落进 `NO_PATH`
   （首跑 `no_path=35` 里有 3 条是本轮自己的）。修：生成器给 6 枚 image + 3 份 map 补显式仓内 `path`；
   重算后与原 manifest 逐字段比对，**只有这 9 个 `path` 字段新增**，bytes/sha256/有效宏/Program Size/`copy_verification` 一字未变。
   修后 `no_path=32`，与 R2 已披露的往轮 32 条**完全一致**（本轮不再贡献新的 NO_PATH）。

顺带补了一条正向能力：同一远端文件被两份清单登记成不同散列 => 具名 `cross_manifest_sha_conflict`。
本轮没触发（`problems=0`），但它使"plan.json 申报的镜像"与"固件清单登记的镜像"不一致时当场失败，而不是等人比对。

## 3. 往轮清单（SCOPE_PRIOR）—— 逐条披露，不由本轮改写

```
SCOPE_PRIOR|problems=64|declared_but_absent=50,duplicate_conflict=2,hash_mismatch=4,suffix_ambiguous=8
```

与 R2 交付头 `5c549f2` 上记录的 64 条**完全同一**：`declared_but_absent=50`、`hash_mismatch=4`、
`duplicate_conflict=2`、`suffix_ambiguous=8`，其中"硬"问题 14 条（除 absent 之外的那些）。
本轮不替往轮改历史；这些条目逐条打印在 `host/remote_readback_console.txt` 里。

## 4. 交付头自己的复跑（`3411d926a45a8849495782175aed3d086b3fbf98`）

同一程序、同一远端方式，在上一节那次提交推送之后再跑一次（`host/remote_readback_console_finaltip.txt`）：

```
SCOPE_R3|entries=1598 verified=1598 problems=0
REMOTE_READBACK_SUMMARY|tip=3411d926a4|declared=2591|verified=2529|r3_entries=1598|r3_verified=1598
  |r3_problems=0|prior_problems=64|prior_hard=14|no_path=32|unlisted=4531
```

比上一轮多的那 1 条正是新登记的 `host/remote_readback_console.txt`；本轮 `unlisted_r3=5` 就是按规则
本就不该被散列的五件：两份 `SHA256SUMS.txt`、两份交接件、以及本文件 `remote_readback.md`。
往轮 64 条披露与 `no_path=32` 保持完全一致。**本文件与最终交付头之后新加入的件，由复审方独立回读覆盖**
—— 一条记录不能给自己的字节作证，这条边界在 R1/R2 就已按同一方式处理并被接受。

## 5. 这份记录本身的可核对性

- 本文件与 `host/remote_readback_console.txt` 的字节都在 Git 里；`remote_readback.md` 按规则不被任何清单散列，
  `host/remote_readback_console.txt` 在**下一次清单重算**里被登记（提交顺序：本次回读 → 归档 → 重算清单 → 提交），
  所以它的 blob 由后续提交里的清单与再下一次远端回读覆盖。
- `hardware_actions=0`：整个过程只有 git 协议 fetch 与本地读文件，未连接任何设备。


## 6. R3 收尾轮交付头的回读（tip `0959f76111…`，两份提交之后）

```text
REMOTE_READBACK_SUMMARY|tip=0959f76111|declared=4336|verified=4274|r3_entries=3337|r3_verified=3337|r3_problems=0|prior_problems=64|prior_hard=14|no_path=32|unlisted=4531
```

- 本轮范围 `SCOPE_R3` = **3337** 条全部按远端 Git blob 逐条复算通过，`r3_problems=0`；
  清单条目数从 1587 涨到 3325 是因为 `offline/psrun-*` 又新增 5 个运行根（每个会话的
  原字节 `.rx.bin` + 判读 `.txt` + 台账都在内），未删除任何既往证据。
- 往轮清单口径不变：`prior_problems=64 / prior_hard=14 / no_path=32` 原样披露，
  本轮不修历史 64 项，也不为它加通用清单工具。
- 提交前另有 `host/staged_blob_preflight.txt` 同口径预检（`STAGED_PREFLIGHT_TOTAL problems=0`），
  两段一起构成"清单=磁盘=远端 blob"的三方一致。
- 收尾轮的两件审核观察程序对照已入库：修前 `host/reviewer_probes_prefix_r3closeout.txt`
  （两份 stdout 与审核包 expected 逐行一致）与修后
  `host/reviewer_probe_post_fix_r3closeout.txt`（其修前断言不再成立 = 缺陷行为已关闭）。

## 7. 最终补正轮(FF)交付头的回读(tip `e1778ed07f9d`)

命令(逐字): `RBI_ROOT=D:/ns800_m8r3_scratch/remotecheck_ff RBI_TIP=e1778ed07f9d2566b9f4ddcb21edb98811b628d9 python
reports/reviews/phase8-m8-relative-protection-r3/host/remote_readback.py`
(退出码 0; 只经 git 协议从远端取 blob, 未开串口/DAP/下载)。原文
`host/remote_readback_console_r3finalfix.txt`(58 行)。

- `SCOPE_R3|entries=4202 verified=4202 problems=0`
- `REMOTE_READBACK_SUMMARY|tip=e1778ed07f|declared=5203|verified=5141|r3_entries=4202|r3_verified=4202|r3_problems=0|prior_problems=64|prior_hard=14|no_path=32|unlisted=4533`
- 本轮范围条目数从往轮 3337 涨到 4202: 增的是三入口新 run 目录的逐步骤原字节/台账(863 件)、
  本机 9 份验证原文、审核方输入 13 件, 以及候选五件本身; **不是**新增判据框架。
- UNLISTED_R3 由往轮 5 条变 7 条, 多出的两条是 `field/__pycache__/*.cpython-312.pyc` ——
  本机 `import` 副产物被一次目录级 `git add -f` 误带进 `e1778ed`(先前的 `git restore --staged`
  被后面的再添加覆盖)。它们是可从 `.py` 重建的编译件, 清单规则本来就排除 `__pycache__`,
  所以既不在登记内也没被核对过。**本条随记录提交一并撤出索引与磁盘**, 见
  `run_record.md` 末尾与提交信息; 其余 5 条与往轮同形(两份清单自身、`remote_readback.md`
  与两份交接件), 属既有披露。

撤出后最终 tip 的逐 blob 回读结果按任务书 §4 第 7 条写进当轮回执, 不再为"记录刚验证过的头"
继续追加提交。

## 8. 第 0 条源码审计轮的逐 blob 回读(tip `3b3b36e0…`)

命令(逐字, 全新裸库根 `D:/ns800_m8r3_scratch/remotecheck_audit`):

```
RBI_ROOT=D:/ns800_m8r3_scratch/remotecheck_audit \
RBI_TIP=3b3b36e0085e741e90f197cbe3d9136f2c27cb39 \
PYTHONIOENCODING=utf-8 python reports/reviews/phase8-m8-relative-protection-r3/host/remote_readback.py
```

退出码 0；**只经 git 协议从远端取 blob**，本轮零设备动作、零构建、零产品改动。原文
`host/remote_readback_console_audit.txt`(7446 B / LF / sha256 前 16 `5e1dc81131fba2c2`，
56 行；Windows 重定向带来的 56 个 CR 已按既有纪律剥净后再入清单)。

- `remote_tip=3b3b36e0085e741e90f197cbe3d9136f2c27cb39` —— 与本次推送头逐字符相同(绑定生效)。
- `SCOPE_R3|entries=5106 verified=5106 problems=0` —— 本轮四条清单(评审 SHA256SUMS 5094 条、
  固件 SHA256SUMS 10 条、`build_manifest.json` 9 件、`field/plan.json` 申报的镜像)解析去重后的
  5106 件，逐条从**远端字节**复算相符。
- `REMOTE_READBACK_SUMMARY|…|prior_problems=64|prior_hard=14|no_path=32|unlisted=4531` ——
  往轮披露项一字未改(64/14/32 与 §1/§7 同形)，不由本轮改写。
- `of_which_in_r3_paths=5`：两份清单自身、`remote_readback.md`、两份交接件 —— 就是 §7 记下的
  **既有披露基线**，`__pycache__` 计数 **0**(本轮没有目录级 `git add -f`，八件全部按名相加，
  提交后仍以 `git diff --cached --name-only | grep -c __pycache__` 复核，结果 0)。

本轮入库相对上一头的增量只有四件审计证据(`host/m8diag_source_audit.txt` 判词、
`host/m8diag_source_audit_check.py` 机器复算、`host/m8diag_source_audit_console.txt` 真实输出、
`host/m8diag_spi_h_excerpt.txt` 仓库外厂商头的本机身份与最小摘录)加三处文档追加
(`run_record.md` §11–§12、交接 MD §10–§12、交接 JSON 追加三键 + 一条板级取证口径更正)。
**没有**新增判据框架、没有改计划/执行器/产品件 —— 审计本身是只读的。

自我指涉按既有纪律处理，不留含糊：本件 console 由**这次**回读产生，因此它不在 `3b3b36e` 自己的清单里
(那 5094 条写于它生成之前)，而是进入随后那次"证据归档头"的清单；而归档头自己的回读原文
按 §7 末条"不为记录刚验证的头无限追加提交"**只进回执与仓外 scratch**，不再落进仓库。
