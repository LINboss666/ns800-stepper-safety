# 远端逐 blob 回读（M8-R2 交付）

- 远端：`https://github.com/LINboss666/ns800-stepper-safety.git` 分支 `feature/m8-sg-adc-relative-protection`
- 回读方式：在**新建的空 bare 仓**（仓库外 `D:/ns800_m8r2_scratch/remotecheck`）里
  `git -c http.proxy=… fetch --depth=1 --no-tags` 只取远端一次，之后所有字节都由
  `git cat-file blob <tip>:<path>` 从**远端取回的对象**读出再算 SHA256。
  本地工作树与本地对象库都不参与（新仓里本来就没有对象），所以这不是"拿本地哈希自证"。
  原始 stdout 逐字节归档在 `host/remote_readback_console.txt`；程序在 `host/remote_readback.py`。
- 本机执行环境：Windows 10.0.26200 / Python 3.12 / git for Windows，`PYTHONIOENCODING=utf-8`，`RBI_TIP` 传入期望头。

## 1. 本轮交付（SCOPE_R2）—— 通过

被回读的交付头：`4df1e03246c11c1172f0c897371314dbdb83aa9d`（本轮第二枚提交：证据交付 `d6a03b5` + 回读程序返修 `4df1e03`）
源码与验证链提交：`207b3747efa5d54f7549dca79ececc81c4766794`

```
SCOPE_R2|entries=171 verified=171 problems=0
REMOTE_READBACK_SUMMARY|tip=4df1e03246|declared=985|verified=923|r2_entries=171|r2_verified=171
  |r2_problems=0|prior_problems=64|prior_hard=14|no_path=32|unlisted=4544
hardware_actions=0|sources_read_from=remote_git_protocol_only
```

本轮四张清单的全部 171 条（评审清单 162 条 + 固件清单 9 条镜像/map + 固件 build_manifest 的 images/map
+ 现场离线运行清单的 143 对里与固件重复者按唯一路径合并后的 171 个唯一路径）逐条满足
"存在 + sha256 相等 + 声明字节数相等"。`declared=985` 是仓库内全部清单登记条目去重后的总数（含往轮清单）。

覆盖关系（避免"清单散列自己"）：
- 两份 `SHA256SUMS.txt` 不含自身字节；`build_manifest.json` 不被固件清单登记，因此它的字节
  不在任何散列覆盖内 —— 本轮沿用既有惯例并在此显式披露（`UNLISTED_R2` 列出的 5 件正是这两份清单
  与两份交接件，交接件按仓库惯例不登记）。
- 本记录 `remote_readback.md` 从评审清单里**显式排除**（记录不能散列自己正在写的字节），
  排除规则写在清单生成脚本里；若它被别的清单登记，程序具名报 `record_excluded`。

## 2. 这台"清单优先"的检查器是怎么修对的（三条，都是它自己跑出来的）

1. **只认一种清单结构 = 静默跳过别的清单**。首版撞 `AttributeError: 'str' object has no attribute 'get'`：
   它只认本轮/R1 那种 `targets: [{images, map}]`，而仓库里另有 `targets` 为 dict、顶层 `images`、
   键名是角色名(`axf`/`bin`/`load`)三种历史形态。现版递归解析任何 JSON，
   解析不出路径的逐条披露为 `sha_declared_without_path`（本轮共 32 条），不猜文件名也不当成已通过。
2. **只认一种路径基准 = 把别的轮次误判成缺件**。第二版只按"清单自身目录"解析，于是把 R1 的
   `field_offline_run_manifest.json` 里 285 件（声明为 `<run_id>/…`，真实位置在
   `field/offline/<run_id>/…`）报成 `declared_but_absent`。现版先按三种常规基准（清单自身目录 →
   上一级 → 仓库根）精确解析，都不存在时再用**后缀索引**在远端树里找同名相对路径：唯一命中且
   **散列也相等**才算通过（本轮 `suffix=285` 全部通过），命中多条就报 `suffix_ambiguous`（8 条）而不是挑一个。
   这样"登记了但仓库里确实没有"照样报得出来，而不会因基准不同被误判、也不会把随意一个同名文件当成已验证。
3. **本轮自己的清单也有一处基准不自洽**：`host/field_offline_run_manifest.json` 的 `entries` 键原先写成
   评审目录相对，而清单住在 `host/`，被同一判据报 137 条缺件。修法是让清单自描述（键改 `host/` 相对 +
   新增 `path_resolution` 字段），143 对 `(解析后路径, sha256, bytes)` 与改前逐条相等，只改基准；
   同时把 `#` 注释行按注释处理（历史 P0-R2 清单带说明头，5 行）。

## 3. 往轮清单的披露（SCOPE_PRIOR，不由本轮改写历史）

```
SCOPE_PRIOR|problems=64|declared_but_absent=50,duplicate_conflict=2,hash_mismatch=4,suffix_ambiguous=8
PRIOR_BY_MANIFEST reports/reviews/phase8-motor-m5-fault-log/cp1/old_archive_manifest.json | 42
PRIOR_BY_MANIFEST reports/firmware/phase8-motor-bringup-m1-prep/build_manifest.json | 9
PRIOR_BY_MANIFEST reports/reviews/phase8-target-measurement-a0/build_manifest.json | 4
PRIOR_BY_MANIFEST reports/firmware/phase8-motor-m7-current-calibration-r1/build_manifest.json | 3
PRIOR_BY_MANIFEST reports/firmware/phase8-sensor-stack-uart-r2/build_manifest.json | 2
PRIOR_BY_MANIFEST reports/firmware/phase8-tmc-uart-read-observation-r1/build_manifest.json | 2
PRIOR_BY_MANIFEST reports/firmware/phase8-tmc-uart-read-observation/build_manifest.json | 2
```

四条 `hash_mismatch` 逐条列在这里（登记散列 ≠ 远端字节复算值）：

| 清单 | 件 | 登记 | 复算 |
|---|---|---|---|
| `reports/firmware/phase8-motor-m7-current-calibration-r1/build_manifest.json` | `…/builds/build_rt-thread.log` | `9e0e6eff14d5…` | `f733710c26d5…` |
| 同上 | `…/builds/build_rt-thread-measure-a0.log` | `b46709232a56…` | `68011af4cf18…` |
| 同上 | `…/builds/build_rt-thread-motor-m1-candidate.log` | `8490e08931d1…` | `a6d071e0968e…` |
| `reports/reviews/phase8-motor-m5-fault-log/cp1/old_archive_manifest.json` | `…/old_archive_checker_first_failure.txt` | `c0c6a9b6b65f…` | `9e1b65dc092e…` |

判读边界（不越界下结论）：这四件的形态与 M8-R1 已确认的 `core.autocrlf=true` 剥 CR 一致
（R1 那轮为此给证据路径加了窄 `-text` 并复算 `blob_neq_disk=0`），但**本轮没有去复算"差异是否恰好等于 CR 字节集合"**，
因此只登记为"往轮清单登记值与远端字节不一致"这一事实，不写成根因结论。
两条 `duplicate_conflict`（A0 的 `build_manifest.json` 对同一 `default.bin`/`measure-a0.bin` 登记两个不同散列）
与 8 条 `suffix_ambiguous`（裸文件名 `rt-thread.axf` 在多个轮次目录里同名，最多 9 个候选）同理只披露不改写。

**本轮不修改任何往轮清单、镜像或原始证据**：这些都是历史交付件，改写就等于重造历史。
若审核方认为往轮的这 64 条需要处置，应作为独立工作项布置（本轮任务书只授权修"当前回读程序的缺件检查"）。

## 4. 与审核方逐条结论的对账

- 审核方给的 `R1_REVIEW_MANIFEST = 331/336_PRESENT_AND_MATCHED / FIVE_MISSING` 本轮已核销：
  R1 评审清单 336 条在远端逐条**存在且散列相符**（含那 5 件构建日志，本机原件散列与清单逐字相符后
  按具体路径 `git add -f` 入库；未改 `.gitignore`，未改 R1 清单原文）。
- 旧 M8 清单 23 条同样逐条相符（`reports/reviews/phase8-m8-sg-adc-relative-protection/SHA256SUMS.txt`）。
- R1 的 9 件镜像/map 工件身份（`reports/firmware/phase8-m8-relative-protection-r1/` 的 6 镜像 + 3 map）
  在远端字节相符，本轮未触碰该目录。

## 5. 状态

- 零设备动作：`hardware_actions = []`；本程序只读远端 Git 对象，不建立串口/DAP 对象，不接触目标。
- `M8_R2_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable = false`，不申请任何 GO。
- 第 1 节里的 `4df1e03` 是"包含本轮全部证据字节"的头的复算；本记录与修好后的回读程序随下一枚提交进入
  仓库，该最终头的同判据复算结果写在下一节（追加而不重写本节）。

## 6. 追加：最终交付头的复跑

（本节在 §1–§5 定稿并提交、推送之后追加；追加会改变本文件字节，故本文件仍按 §1 的排除规则不进清单。）

把 §2 那三处修正一并提交后的头是 `d80ebbbbfe969a144b284d8d6c22261ea22679f6`。同一程序、同一判据再跑一次
（只读远端 Git 对象，`hardware_actions=0`）：

```
remote_tip=d80ebbbbfe969a144b284d8d6c22261ea22679f6
sums_manifests=9 comment_lines_skipped=5
json_manifests=20 sha_declared_without_path=32
declared_entries=986 verified=924 base_used={'manifest_dir': 620, 'parent_dir': 4, 'repo_root': 19, 'suffix': 285}
SCOPE_R2|entries=172 verified=172 problems=0        (退出码 0)
SCOPE_PRIOR|problems=64|declared_but_absent=50,duplicate_conflict=2,hash_mismatch=4,suffix_ambiguous=8
REMOTE_READBACK_SUMMARY|tip=d80ebbbbfe|declared=986|verified=924|r2_entries=172|r2_verified=172|r2_problems=0
  |prior_problems=64|prior_hard=14|no_path=32|unlisted=4545
hardware_actions=0|sources_read_from=remote_git_protocol_only
```

与 §1 的差别只在于条目从 171 变 172：新增的一条正是这一版 `host/remote_readback.py` 自身
（§1 那次跑的时候，仓库里还是修好前的程序）。`SCOPE_PRIOR` 的 64 条与 §3 逐条同一集合，未增减 ——
即"本轮提交没有改动任何往轮证据"这一点被同一条判据复算证明。
`UNLISTED_R2` 6 件是本记录 + 两份评审/固件清单 + `build_manifest.json` + 两份交接件，全部属于
"清单不散列自身、交接件不登记"的既有惯例，已显式披露。

结论：M8-R2 交付头 `d80ebbbb…` 的本轮四张清单逐条存在、散列与字节数相符，零问题；
`M8_R2_OFFLINE_READY / AWAITING_INDEPENDENT_REVIEW`，`safe_for_enable = false`，零设备动作，不申请 GO。
