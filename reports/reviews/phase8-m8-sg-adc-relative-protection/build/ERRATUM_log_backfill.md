# 勘误：M8 三目标 Keil 构建日志在交付时未进入仓库（M8-R1 轮补归档）

- 被勘误的交付：`f939363449c528892fb3d9978d73e085cfa90c91`（M8 交付复审对象）
- 登记轮：M8-R1（`phase8-m8-relative-protection-r1`）
- 本文只追加事实，**不改写** M8 的任何既有工件：`run_record.md`、`SHA256SUMS.txt`、
  `host/`、`field_candidate_package.md`、`remote_readback.md` 与 M8 镜像目录一字未动。

## 1. 事实（逐条可复算）

1. M8 交付的 `SHA256SUMS.txt` 里**已经**登记了四件构建产物的哈希：
   `build/rt-thread.log`、`build/rt-thread-measure-a0.log`、
   `build/rt-thread-motor-m1-candidate.log`、`build/rc.txt`。
2. 这四件文件在交付当时**存在于磁盘但不存在于仓库**：目录成分 `build/` 命中
   `.gitignore:28`，文件后缀 `.log` 又命中 `.gitignore:31`，常规 `git add` 不会收录它们，
   而 M8 的提交没有对具体路径做 `-f`。因此按 `f939363` 检出的审核方在仓库里找不到这四件，
   清单上"已声明"与实际"已交付"不一致。
3. M8 `run_record.md` 第 5 节写的"日志与返回码在
   `reports/reviews/.../build/`"在当时只对磁盘成立、对仓库不成立——**该指针在交付时是错的**。
   本轮不重写那句原文，只在此登记它错在哪。
4. 补归档前先在磁盘上找到了原件（未重跑构建、未重造内容）：
   `rt-thread.log` 3809 B / 2026-10-31 00:36:37，
   `rt-thread-measure-a0.log` 3849 B / 00:36:53，
   `rt-thread-motor-m1-candidate.log` 3909 B / 00:37:07，
   `rc.txt` 108 B / 00:37:07。
5. 四件的 SHA256 与 M8 已发布清单逐字相符，见同目录 `SHA256SUMS_backfill.txt`
   （该文件只覆盖本轮补进仓库的四件，不覆盖 M8 `SHA256SUMS.txt` 自身——清单不哈希自己的字节）。

## 2. 处理方式

- 只对上面四个**具体路径**执行 `git add -f`；没有修改 `.gitignore`，没有放开 `build/`
  或日志目录的忽略策略，没有新增任何一条忽略例外。
- M8 `SHA256SUMS.txt` 保持原字节（它是已发布产物，改了就会破坏它自己声明的身份）。
- 若磁盘上的原件当时已经丢失，本节会写成"日志永久缺失、无法补归档"，而不是重跑一次
  构建再称之为原始日志。本轮**不需要**这一条：原件在，取的是原件。
