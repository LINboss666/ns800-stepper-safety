# 复审回执：NS800 竞赛演示端 V0.3（离线回放上位机）

- 复核人：本机执行侧（Windows 11 / Python 3.12.5 / Tk 8.6，缩放 100%，单台机器）
- 日期：2026-11-04
- 被审件：`NS800_Demo_Offline_V0_3.zip`，179861 B，SHA256 `b1e257cc7edfd10838b98120f938c974f4c3a63eb002b926f2401f10638a8113`
- 基线：固件仓库 `D:/ns800` @ `0d8239f`（本轮只读，未改固件、未改旧包、未改归档原件）
- 设备动作：**0**（未连接 COM/DAP，未下载、未复位、未发指令）

## 1. 结论

**V0.3 签收，作为竞赛演示端定稿收口。** 三条声明全部独立复核成立，21 项离线判据 + 6 项反例 + 2 帧桌面实测均与声称一致，无返修项。
唯一需要更正的是**我上一轮口头回执里的一处文字**（四件口径），本回执按更正后的口径写死，见 §3.1。

## 2. 输入身份

| 件 | 字节 | SHA256 |
| --- | --- | --- |
| V0.3 zip | 179861 | `b1e257cc7edfd108…638a8113` |
| V0.2 zip（旧包，未改） | 402497 | `426ebca896245777…80d9ab5b` |
| V0.1 zip（旧包，未改） | 11445 | `ba6b63a2f2011508…fbea135e5` |
| 包内 `upgrade_v02_to_v03.patch` | 15266 | `1e00c26bc50e6565…81ea9b60` |

包内 11 个文件；`SHA256SUMS.txt` 列 10 条，逐条重算 **10/10 OK**，且清单不哈希自身（`host/manifest_recompute.txt`）。

## 3. 三条声明逐条判定

### 3.1「README 补齐关闭 autocrlf 的补丁命令；应用后四个文件与完整版逐字节一致」— 成立（口径已更正）

README 新增章节给出 `git -c core.autocrlf=false apply --check/--apply`。我在仓库外把 V0.2 解包副本应用 V0.3 自带补丁：

- 补丁**实际改动三件**：`demo.py`、`README.md`、`data/replay.json`（以补丁头 `+++ b/…` 为准）。
- 应用后与完整版**逐字节相等**的共 **四件** = 上述三件 **+ 未进补丁、本来就不该变的 `run_demo.bat`**（137 B，含 9 个 CR，两边一致）。
- **更正**：我上一轮把这第四件说成 `SHA256SUMS.txt`，错。`SHA256SUMS.txt` 在补丁路线下仍是 V0.2 清单（1844 B ≠ 882 B），**不等**，属预期。程序侧无需返修。
- 对照组（去掉 `-c core.autocrlf=false`，本机 system 级 `core.autocrlf=true`）：三件被写成 CRLF，差异**只在行尾**——`demo.py` CR 0→437（21212→21649 B）、`README.md` 0→68（3880→3948 B）、`data/replay.json` 0→73（12225→12298 B），把 CRLF 归一回 LF 后与完整版逐字节相等。即 README 这条命令不是形式要求，是必要的。
- 补丁路线产出的包同样 `--check` 通过（7 帧）。
- 附带发现（非缺陷，属口径）：补丁不更新清单，所以应用后包内 V0.2 清单只对 `demo.py`/`README.md`/`data/replay.json` 三项散列过期，其余条目仍对（`host/patched_manifest_staleness.txt`）。**清单以完整版为准**；补丁路线只用于拿到可运行程序。

### 3.2「加入写前 motor_status，形成七帧回放，已与归档原件 7/7 比对通过」— 成立

- `python demo.py --check`（不带仓库路径）：7 帧，`data_integrity=PASS` 且明确 `archive_sources.status=SKIPPED_NO_REPOSITORY`，没有把"没核来源"降成跳过式假通过。
- `python demo.py --check --repo-root <仓库根>`：`status=PASS`，`matched_frames=7`。
- 我不信程序自报，另按 `replay.json` 里的 7 条 `source_path` 直接读盘重算（`host/archive_recompute.json`）：3468 / 978 / **1128** / 388 / 980 / 1080 / 522 B，磁盘字节与包内内嵌原文**逐字节相等 7/7**。
- 新增第 3 帧 = `field-20261104T015928Z/S_S1/02_mot_pre.rx.bin`，SHA256 `f0550d3131642cd0…9cf0c3b5`，与 R7F4 现场台账 `RX_DONE` 记录的散列同一枚——是归档原文，不是手抄文本。
- 帧顺序 boot→配置前→写前→配置回包→配置后→写后→安全反馈，与 R7F4 实际执行顺序一致。
- V0.2 的六段内嵌原文与六份散列 6/6 未改。

### 3.3「桌面已核对写前 0x18、写后 0x8，附对应截图」— 成立

包内 A 自带 `verification/gui/motor_pre.jpg`（113847 B，`f0e4d5d1af7a780c…`）与 `motor_post.jpg`（112696 B，`aba8f1c277b487ca…`），我打开看过，分别显示第 3/7 步 `arm 门 0x18` 与第 6/7 步 `arm 门 0x8`。
我另在本机 Windows 独立复现（`gui/`，探针只调用应用自己的按钮回调）：

| 帧 | arm 门 | 参数行 | 来源行 | 原始回包页 sha256 |
| --- | --- | --- | --- | --- |
| 3/7 | `0x18` | 本段记录未提供驱动参数；回放配置回包后显示。 | 1128 B · `f0550d313164`… | `f0550d3131642cd0…` == 仓库原件 |
| 6/7 | `0x8` | 固件设置值：IRUN 20 · IHOLD 8 · DLY 4 · TPD 8 · VSENSE 1（电流未实测） | 1080 B · `fa69aa5cf5a1`… | `fa69aa5cf5a11917…` == 仓库原件 |

客户区 1180×820，四个回放按钮 bbox 全在区内（底部留白 53 px），三个"待联调/待验证"按钮 `state=disabled`。

## 4. 反例复跑（6/6 与声称一致，`host/negative_controls.txt`）

| 场景 | 期望 rc | 结果 |
| --- | --- | --- |
| 仓库根不存在 | 1 | MATCH |
| 少一份来源件 | 1 | MATCH |
| 某份改 1 个字节 | 1 | MATCH（首差异位置 0） |
| 内嵌文本+内嵌散列同时自洽篡改，仍与仓库比对 | 1 | MATCH（首差异位置 26） |
| 同一篡改包不带 `--repo-root` | 0 | MATCH（证明拦截来自外部原件比对，不是包内自证） |
| 五种不规范 `source_path` | 1 | MATCH |

第 4 条的篡改点不再硬编码字段：脚本现场扫描"应用自己的解析器整行都不看"的行（命中 `[MOT] state=0 name=OTHER` 偏移 26，0→1），并用应用的 `Snapshot.update` 当场证明确实不改任何状态，否则测不到外部比对这一层。

## 5. 设备动作面

`demo.py` 导入面 AST 为 `__future__, argparse, dataclasses, hashlib, json, pathlib, re, sys, tkinter`；AST 里不存在 `serial/socket/subprocess/os/ctypes/threading/multiprocessing` 的名字或属性引用。全文禁词旁证只命中模块文档串那句"没有串口/DAP/子进程"声明本身（`host/imports_audit.txt`）。

## 6. 边界（不构成缺陷，但不得越过）

1. `--repo-root` 只比对**磁盘原件字节**，不核验 Git HEAD，也不核验当前板端状态；README 已如实声明。
2. GUI 覆盖只有本机一台 Windows、100% 缩放、默认 1180×820；其他 DPI/字体/系统缩放组合 **NOT_VERIFIED**。
3. 数据是 R7F3 启动 + R7F4 配置的历史回放。SG/电流/振动动态曲线、真实运动、保护触发与正常 disarm、带载最坏栈仍 **NOT_VERIFIED**，硬件验证继续暂停。
4. 本轮不改动板端固件、原始归档与执行器；旧包 V0.1/V0.2 字节未变。

## 7. 我上一轮回执的两处文字更正

1. **四件口径**：见 §3.1——第四件是未改动的 `run_demo.bat`，不是 `SHA256SUMS.txt`。
2. **"来源行被裁"结论作废**：那是我用 PrintWindow 截客户区导致内容整体下移约一个标题栏高度（≈29 px）造成的误判；改用 `GetClientRect + ClientToScreen + CopyFromScreen` 后，来源行完整在区内。
3. 另记两处本轮自纠（工具侧，不影响被审件）：探针只 `update_idletasks()` 时控件未映射、`winfo_rootx/y` 返 0，会把按钮全算成"在客户区外"，改为测量前走完整 `update()`；GUI 文本改由脚本按 UTF-8+LF 直写文件，不再经 cp936 控制台，避免上一轮 dump 全是乱码。

## 8. 复算入口

```
python tools/demo_negative_controls.py --pkg <解包V0.3> --repo <仓库根> --box <仓库外工作区>/neg
python tools/demo_review_checks.py --zip <V0.3> --zip-prev <V0.2> --zip-prev2 <V0.1> \
        --repo <仓库根> --box <仓库外工作区> --out host
powershell -NoProfile -ExecutionPolicy Bypass -File tools/demo_gui_shot.ps1 \
        -PkgDir <解包V0.3> -Probe tools/demo_gui_probe.py -Frame 3 \
        -Dump gui/frame3_0x18.json -Out gui/frame3_0x18.png -HoldSec 20
```

本轮 `host/summary.json`：**21 项判据，fails=0**。
