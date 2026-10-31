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
