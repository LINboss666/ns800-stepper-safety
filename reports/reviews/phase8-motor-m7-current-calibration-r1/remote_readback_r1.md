# M7-R1 远端回读（按 Git blob 字节，不按 autocrlf 工作树）

日期：2026-10-29。远端：`origin/debug/tmc-uart-rootcause-20261028`
HEAD = `bcf3693b96807377f207646308d3139152b7e244`（先 fetch，再对远端 ref 取对象，
不比较本地分支引用）。

## 1. 方法

```
git -c http.proxy=http://127.0.0.1:7892 push origin debug/tmc-uart-rootcause-20261028
git -c http.proxy=http://127.0.0.1:7892 fetch origin debug/tmc-uart-rootcause-20261028
git rev-parse origin/debug/tmc-uart-rootcause-20261028      # 远端指针
git diff --name-only 68d0774..bcf3693                        # 本轮全部交付路径
git cat-file -p <远端HEAD>:<路径>                             # 逐路径取**远端 blob 字节**
```

比对基准是**远端 blob**，不是工作树：本机 `core.autocrlf=true`，工作树的 CRLF 与
仓库 blob 的 LF 是两种字节形态，拿工作树比会把文本件全部误报成 MISMATCH
（M7 轮已实测过一次这种假失配）。

## 2. 结果

| 项 | 数 |
|---|---|
| 交付路径（`68d0774..bcf3693`） | 57 |
| 远端 blob == 本地 HEAD blob | **57 / 57**，0 MISMATCH |
| 工作树字节 == 远端 blob（二进制与本来就是 LF 的文本） | 33 |
| 仅工作树 **CRLF→LF 归一后** == 远端 blob（`blob == 工作树.replace(b"\r\n", b"\n")`） | 24 |
| 工作树与远端 blob 在归一后仍不等 | 0 |

那 24 个是本轮新增/修改的文本证据与文档（交接、构建记录、日志、清单、源码头注释等），
形态与 M6/M7 一致：仓库里存的是 LF，检出到这台 Windows 上是 CRLF。
按既有纪律，声称"逐字节归档"的地方一律给**双身份**（工作树 bytes+SHA 与预期 blob
bytes+SHA），并标 `lossless`；明细见 `host_runs/identity.json` 与
`build_manifest.json` 里每个 `build_log` 的 `cr / expected_blob_bytes / lossless` 字段。
`git cat-file` 的 8 字段级原始清单在 `m7r1_readback.json`（本机仓外，按需可复算）。

## 3. 六件镜像按远端 blob 复核 manifest

对 manifest 里每条 `bin`/`axf`，从**远端 HEAD** 取该路径字节后重算长度与 SHA-256：

```text
manifest image entries verified against the remote blob: 6  mismatches: 0
```

| 目标 | 远端 blob 字节 | SHA-256（前 16） | 与工作树字节相同 |
|---|---|---|---|
| rt-thread.bin | 266140 | `5f080fbc652599c6` | 是 |
| rt-thread.axf | 1688212 | `54479417bff6e9d0` | 是 |
| rt-thread-measure-a0.bin | 276388 | `f99f3be502341508` | 是 |
| rt-thread-measure-a0.axf | 1718624 | `9a9a5d92e488a316` | 是 |
| rt-thread-motor-m1.bin | 266244 | `e614a075ab63eb5b` | 是 |
| rt-thread-motor-m1.axf | 1688696 | `2205c7e7ad6bad93` | 是 |

`build_manifest.json`（6325 B / `97886e8b74b32e8f…`）本身也按同一方法核对。
manifest **不哈希自身字节**（与任务清单同一规则）：它的 bytes/SHA 由本轮读回结果与
交接登记，不由它自己声明。

## 4. 这些字节不等于什么

- 不等于板上运行：六件镜像本轮 `NEW_IMAGES_NOT_FLASHED`，板上最后已证仍是 M5 motor
  `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`（259580 B）。
  远端 blob 相符只证明"仓库里的字节 = 我本机算出的字节"，不证明 flash 里是什么。
- 不等于复审通过：本轮不自我授予任何 REVIEW GO，回执见交接 §7。
- 冻结引用未动：`main`、`phase7`、`phase8`、tag 与 `68d0774` 之前的历史全部原样；
  本轮只追加两个提交（`5b564eb` 软件输入、`bcf3693` 构建工件），无 reset/rebase/merge/force。
