# 竞赛演示端 V0.3 复审轮 现场记录

轮名：`DEMO-V03-review`
日期：2026-11-04
基线：`D:/ns800` @ `0d8239f`（分支 `feature/m8-sg-adc-relative-protection`）
执行范围：离线复核 + 本机一次真实 Tk 启动。**设备动作 0**（未连接 COM5/DAPLink，未下载、未复位、未发指令）。

## 1. 输入

- `NS800_Demo_Offline_V0_3.zip` 179861 B `b1e257cc7edfd108…638a8113`（被审件，A 交付）
- `NS800_Demo_Offline_V0_2.zip` 402497 B `426ebca8…`、`NS800_Demo_Offline_V0_1.zip` 11445 B `ba6b63a2…`（旧包，仅核未被改动）
- 七份归档原件：`reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/field-20261104T015928Z/**`（只读）
- 本机 git 配置：`core.autocrlf=true` 为 **system 级**，在仓库外同样生效（这是补丁路线必须显式关闭它的依据）

## 2. 动作与产物

| 步 | 动作 | 产物 |
| --- | --- | --- |
| 1 | 三份 zip 字节身份 + 包内清单逐条重算 | `host/manifest_recompute.txt` |
| 2 | 跑被审程序自身 `--check` 与 `--check --repo-root` | `host/package_check.json`、`host/repo_check.json` |
| 3 | 不信自报，直接读盘重算七份原件 | `host/archive_recompute.json` |
| 4 | 补丁回环两跑（关/不关 autocrlf）+ 旧清单过期度 | `host/patch_roundtrip.json`、`host/patched_manifest_staleness.txt`、`host/patched_pkg_check.json` |
| 5 | 反例复跑 6 例（含状态中性篡改与不规范路径） | `host/negative_controls.txt` |
| 6 | 导入面与设备动作面（AST 判据） | `host/imports_audit.txt` |
| 7 | 本机真实启动两帧实测 + 客户区截图 | `gui/frame3_0x18.*`、`gui/frame6_0x8.*`、`gui/README.txt` |
| 8 | 集中判据 21 项 | `host/summary.json`（fails=0） |
| 9 | 回执由证据回读重算 | `handoff_DEMO_V03_review.md` / `.json` |

## 3. 结果

- 三条声明全部 CONFIRMED；V0.3 作为竞赛演示端定稿收口。
- 唯一更正（文字，不动程序）：补丁实际改动 `demo.py`、`README.md`、`data/replay.json` 三件；
  与完整版逐字节相等的**四件**是这三件 **+ 未进补丁的 `run_demo.bat`**，第四件不是 `SHA256SUMS.txt`。
- 附带口径登记：补丁不更新清单，故走补丁路线后包内 V0.2 清单对那三件过期（其余条目仍对）；清单以完整版为准。

## 4. 停点

本轮为纯文档/证据轮，无待批事项，无额度消耗。硬件验证继续暂停。
下一场现场轮（极性/IRQ 就绪、短动、SG/ADC 学习、目标通道触发）仍需 A 出新包 + 操作者逐项批准，
且 DAPLink/COM5 通路要先恢复物理在位。

## 5. 遗留（不在本轮范围）

- 其他 DPI / 字体 / 系统缩放组合的界面布局：NOT_VERIFIED
- `--repo-root` 只证磁盘字节，不证 Git HEAD、不证当前板端状态
- SG/电流/振动动态曲线、真实运动、保护触发、正常 disarm、带载最坏栈：NOT_VERIFIED

## 6. 提交链与我方工具自伤（原样登记，不删不改）

| tip | 内容 | 远端逐 blob 回读 |
| --- | --- | --- |
| `79c4343` | 复审签收 + 26 件证据入档 + `.gitattributes` 本轮 `-text` 规则 | 跑到回读脚本自身抛 `TypeError`（`run()` 缺 `cwd`）与 `UnboundLocalError`（`fails` 被当局部），未跑完即失败 |
| `f68ea64` | 修该脚本并重算清单 | 26 条登记件 25 条 远端==登记==磁盘，唯一不符正是该脚本自身（磁盘已改、远端仍指旧字节）；其余判据全过。另报 2 条假 `UNEXPECTED_MODIFY` 与 1 条假 `SELFCHECK_NOT_FIRED` |
| `801fb18` | 把"允许改动集"与"`.gitattributes` 是否应变"改成显式参数 | **fails=0**，26/26 全对（原文归档为 `remote_readback_at_801fb18.txt`） |
| `be74662` | 归档该回读原文 + 重算清单 | **fails=0**，27/27 全对，原件 8 件 tip/tip^ 逐字节相同，三项自检均 FIRED |

- 三条被审声明的判据（21 项 PASS、6 反例 rc 全对、两帧 0x18/0x8、七帧 7/7 逐字节）在上述任何一步都没有变化；
  四个 tip 全部只动本轮目录与 `.gitattributes`，固件、旧包与归档原件始终未改。
- 教训登记：回读脚本自身也在清单里，改它必然让"磁盘==远端"在它自己那一条上暂时不成立；
  因此收口顺序必须是 内容定稿 → 重算清单 → 提交 → 推送 → 回读，且判据不许写死"首提交形态"。

