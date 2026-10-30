# 远端逐 blob 回读（M8-R1 交付头）

- 远端：`https://github.com/LINboss666/ns800-stepper-safety.git`
  分支 `feature/m8-sg-adc-relative-protection`
- 被回读的交付头：`e320232cf7233d7e26df3fee8049ad9ce9202253`（本轮证据+交接提交）
- 源码与验证链提交：`3275356a46e0b503888e9a4869da3b2b41f027a8`（`e320232` 的父）
- 回读方式（脚本 `host/remote_readback.py`，本轮真跑，rc=0）：
  在一个**新建的空 bare 仓**里 `git fetch --depth=1` 只取远端一次 shallow 包，
  之后所有字节都由 `git cat-file blob <oid>` 从**远端取回的对象**读出，
  再算 SHA256 与远端两份 `SHA256SUMS.txt` 逐条比对。
  本地工作树与本地对象库都不参与（新仓里本来就没有对象），所以这不是"拿本地哈希自证"。

## 1. 结果

```
remote_tip=e320232cf7233d7e26df3fee8049ad9ce9202253 expect_equal=True
remote_files=4980
listed_and_hash_matched=340 listed_but_mismatched=0
manifest_vs_remote_bytes=ALL_EQUAL
M8_manifest_entries_verified_from_remote=23 problems=0
REMOTE_READBACK_SUMMARY|files=4980|listed_checked=340|mismatch=0|manifest_bytes_ok=True
```

| 判据 | 结果 |
|---|---|
| 远端分支 tip == 本地交付头 | 是（`e320232…`） |
| 两份清单登记的每一条目，用远端字节复算 | **340/340 相符，0 不一致** |
| `build_manifest.json` 里 6 枚镜像/AXF + 3 份 map 的 `bytes` 与 `sha256` | **全部等于远端 blob 复算值** |
| 旧 M8 已发布清单的条目（含本轮补归档的三目标日志与 rc.txt） | **23 条全部用远端字节复算相符，0 问题** |
| 未出现在任何清单里的交付件 | 0（`NOT_IN_MANIFEST` 无输出） |

## 2. 覆盖范围与不自证之处

- 本文件与 `host/remote_readback.py` 是**回读的输出物**，产生于 `e320232` 之后，
  因此不在这 340 条的覆盖范围内；任何人可用同一脚本对更新的 tip 复跑（脚本本身已入远端）。
- 清单不哈希自己的字节；`SHA256SUMS.txt` 与回读后的输出物按 `run_record.md` §9 的规则排除。
- 远端 tip 的 blob id 只证明"仓库里存的就是这些字节"，
  **不证明这些字节等于板上 flashing 的字节**——后者要等被授权的现场轮做定长回读。

## 3. 本轮推送后的最终头

推送后远端分支头即交付头 `e320232`；追加"回读记录+脚本"的下一次提交只新增这两个文件，
不改任何既有工件。`hardware_actions = []`、`safe_for_enable = false` 在本轮结束时仍然成立。

## 4. 追加一次：把回读覆盖推到"含本文件"的头

上一节只覆盖交付头 `e320232`（本文件与回读脚本产生于其后，不在其清单内）。
把本文件与脚本提交并推送后，用同一脚本对新头再跑一次（远端一次 shallow fetch，
全部字节从远端对象复算）：

```
target=98866124d909fd46fc778fe8d07863d2facaae52
remote_tip=98866124d909fd46fc778fe8d07863d2facaae52 expect_equal=True
remote_files=4982
listed_and_hash_matched=341 listed_but_mismatched=0
NOT_IN_MANIFEST reports/reviews/phase8-m8-relative-protection-r1/remote_readback.md
manifest_vs_remote_bytes=ALL_EQUAL
M8_manifest_entries_verified_from_remote=23 problems=0
REMOTE_READBACK_SUMMARY|files=4982|listed_checked=341|mismatch=0|manifest_bytes_ok=True
```

即：除本文件自身（按 §9 的排除规则不进清单）之外，**新头上每一条清单登记件都相符**。
写这份记录的那次提交若再被改动，就同样只能由下一次复跑覆盖——这是自指限界的固有形态，
不靠"回头改数字"解决；任何人可用 `host/remote_readback.py` 改一个 `EXPECT_TIP` 复跑。

