# NS800 M8-R3 最终现场包复审与精确补正

## 结论与进度

复审对象：`15745e548dcfc4983fb91563fdac81ad5fdf3dc9`，分支 `feature/m8-sg-adc-relative-protection`。

**核心软件/镜像接受结论继续有效，输入仍为 `805c29bd73c4ea861c9b9c65f61e946aec9548e3`。现场执行包仍有下表五处具体问题，原字节暂不现场放行。** 本包给出集中修好的精确补丁，已独立运行原 73 项、22 项边界检查与三条整包假端口路径。无需重新设计框架、重编固件或重跑未改动的 C 链。

以下是“精确候选 + Windows 本机复核”的条件式离线接受：本机按相同字节补正、满足本文验收条件后，现场包离线接受生效；无需再开纯 GO 登记或等待相同字节再审一遍。若有偏离/失败，保留原文并报告实际差异。

## 五处实证问题与修法

定位均以被审 `15745e5` 的 `reports/reviews/phase8-m8-relative-protection-r3/field/` 为基准。

| 项 | 原字节与实证 | 精确修正 |
| --- | --- | --- |
| F1 启动边界 | `m8r3_serial_chain.py:1003` 要求自发启动流以提示符结束。历史真实 3381 B、3757 B 启动件均会被 `POST_RESET_BOOT_PROMPT_MISSING` 拦下。计划引用的 `002_version.bin` 实际以 `version` 开头，是命令回复，不能证明自发启动提示符。 | 复位后有界保存被动原字节；不要求自发提示符，不据启动文本认定 READY。已有第一条 `system_status` 只发一次，仍必须完整提示符，并唯一核对 bootstrap YES/abort 0、Safety READY/fault 0。危险文本仍立即停止。 |
| F2 工具 argv | `m8r3_serial_chain.py:653` 把所有含斜杠的相对参数当路径。展开后的 `-c "savemem ... /run/readback.bin"` 被改成 `/repo/savemem ...`，不是合法 pyOCD 命令。独立注入捕获了实际 argv。 | `-c` 后的完整命令字符串不做路径映射；镜像/独立文件参数继续以 repo_root 解析。没有真实调用 pyOCD。 |
| F3 有符号 CSV | `m8r3_serial_chain.py:1717` 对全部 14 列使用 `isdigit()`。产品 `ns_flash_shell.c` 的第 6..12 零基列用 `%d`，如 `ax_mg=-23` 合法，却被当成没有日志帧。 | 只对产品的有符号列允许负号；slot/session/valid 等无符号列仍不允许负值。缺帧、设备读槽错误仍拒绝。 |
| F4 新日志槽位 | `plan.json:1327` 固定 `flash_dump 0 8`。事件存储为追加、满即停；已有 M5 记录时开头不是本次事件，即使初始为空，事件前 200 帧也不一定含停后帧。session 标签可能重用，仅凭 session 不够。 | S8 起转前增加一次只读 `flash_logstat` 记录已用槽位并要求剩余至少 300 槽；S9 从实际新增区间选末尾最多 8 帧，按当前 used 推导 first/count。逐槽覆盖、session、event=9、step_hz=0 都要成立。不得为腾空间自动清日志。 |
| F5 PowerShell 真入口 | `M8R3Field.ps1:36` 的 `ValidateSet` 不含 `response_driven`，脚本后文却要求 real 必须采用它；因此正确实机参数也会在绑定阶段被拒绝。 | 只在参数白名单增加 `response_driven`。real+all 禁止、传输/后端分流与真实响应决定分支的门继续保留。 |

F4 夹具明确改成离线声明的“原有 300 槽→新增后 600 槽→读 592..599”，并加入负加速度。它不是设备证据。旧 `flash_dump 0 8` 的行表 key 仅保留为兼容的 stub 选择键，实际发送命令由新增区间计算。

## 已完成的独立验证

- 从远端 Git 对象读取被审提交；两个清单对应 3336 个唯一显式文件逐件 SHA 匹配，另外复算 6 镜像/3 map。唯一文件数与申报 3338 条关系数分开计，不把重复引用算新增文件。
- `4ba9ae5..15745e5` 在产品、tests、reports/firmware 中无改动；main/phase7/phase8/debug/tag 及解引用与冻结值相同。
- 原执行器原 73 项：73/73，rc=0。候选同 73 项：73/73，原 25 身份保留、11 项定向检出保留。
- 本包 `tail_boundary_probes.py`：候选 22/22，rc=0。包括两份真实启动字节重放、`-c` 原样参数、有符号日志、旧槽/重槽/缺槽拒绝、容量不足门、第一条查询故障拒绝与 READY 正控。输入与输出原文并列保留。
- 同一边界程序跑原交付字节时 7/22；其中新 helper 不存在的测试只表明尚无该功能，**不是额外产品缺陷数**。F1/F2/F3/F4/F5 分别有独立的旧行为复现，不靠总失败数判定。
- 候选计划结构 154 条、0 失败；healthy/no_trigger/trip 三条完整 stub+sim 调度分别 rc=0。假端口通过不等于物理 SG/ADC/IMU 触发通过。
- 候选行表来源：41 组、28 条采集引用、33 条源码锚点，problems=0、notes=0。
- 精确补丁在原五文件 LF blob 副本上 `git apply --check`、`git apply` 均 rc=0；五个输出逐字节等于候选，身份见 `patch_identity.json`。
- 云端没有 Windows PowerShell 5.1；F5 在本机仍需原生参数/语法核对。没有 Keil 重建、串口、DAP、复位、下载、TMC 写或起转。

完整源文件从 Git 取得；网页抓取不可用，未把网页搜索失败冒充独立验证成功。

## 给本地 Agent 的执行任务（本轮只做离线补正）

1. 在现有分支顺序提交，基线是上述 HEAD；不 reset/rebase/force/merge。若 HEAD 已前进，先只读确认这五个输入 blob 仍等于身份表；不覆盖历史产物。
2. 对仓外规范 LF 的基线副本应用 `tail_candidate.patch`；先 `git -c core.autocrlf=false apply --check` 再 apply。输入不匹配即报告，不手抄修改、不静默放宽判据。把核过散列的候选写回五路径；暂存后按 Git blob 核候选 SHA，不拿 CRLF 工作树冒充相同字节。
3. 原生 Windows PowerShell 5.1 运行既有 `-SelfTest`、`-Precheck` 与 `-RunField -Transport stub -Backend sim -Profile all`，各新目录。仍用已有执行器；不可为本轮自检打开 COM5、DAP 或真实后端。并离线用 Parser/参数 AST 确认 Profile 白名单含 `response_driven`，不通过调用 real 入口来试绑定。
4. 同字节运行原 73 项；运行本包 `python tail_boundary_probes.py --root <仓库绝对路径> --inputs <解压目录>`，要求 22/22。脚本只导入执行器、使用假端口/假子进程，不需要 pyserial，不访问硬件。跑既有行表来源检查，新增行组必须仍有真实 C 格式出处。PS 结果来自本机原文，云端结果不能代填。
5. 更新当前现场包说明与交接：启动文本不作 READY 证明；真实入口用 response_driven；S8 新增一次只读日志基线查询；S9 读取实际新增槽位，旧固定 0..7 已作废。重新生成本轮修改件的清单，不改历史清单或历史证据。保留既有 73/25 身份与十一项反向，不重造完整性框架。
6. 软件变动限上述五个 field 文件；另可新增本文边界脚本/相应输入的复审归档及本轮证据/交接。产品 C/H、Keil 工程、六镜像、三 map、固件 manifest、UART/内核/安全宏、Flash schema 一律不改。不重编、不重跑不变 C 链；不为本轮修旧 64/32 历史问题。
7. 满足候选散列、73/73、22/22、三条 stub 路径、原生 PS 前置和来源检查后，可记 `M8_R3_FIELD_PACKAGE_OFFLINE_REVIEW_GO_CONDITIONS_MET`，与实质补正一起提交推送，做一次远端回读。最终头验证结果可直接进回执，**不要为记录刚验证的头无限追加提交**。
8. 这些条件齐备后直接向用户呈现一份绑定现有 M8 候选散列的完整现场批准，不另开同字节云端 GO 轮。本文件没有给出硬件许可；未获该整包批准前不得连接/下载/复位/起转。

## 后续真实现场目标与边界

下一步仍是 M8 主线：静止零点→100 Hz 巡航学习→静止 commit/save→重启实际比较还原→RELATIVE 观察/真实触发分支→新日志取证与恢复门。不是再做 UART 演示，也不要求恢复 P1 高精度测量。

整包审批必须明确：学习最多 15 s、保护观察最多 5 s，最多 2 次 start、合计最多 20 s；保持 IRUN20、100 Hz、DIR0，其余额度按修正 plan。新增的 S8 日志查询是只读动作，需与完整包一起列清。无物理触发就记 NO_TRIGGER，不伪造 PASS；SG 分离度、ADC 相对阈值实际作用、IMU 激励与物理保护效果仍待现场验证。不得因日志空间不够自动 clear、增加试转、升流/升频。

核心 motor 候选仍为 BIN 290916 B / SHA-256 `00608ec93c6a3027d68b44b5317574209fccfb0ba82a9974793d13b54b79e304`，AXF 1771288 B / SHA-256 `0bba8ca74ed8c11e149395efd64c42072fce0b3ced5ab76281210ddc04550963`。

## 精确身份

补丁：21667 B / SHA-256 `07de65f0e94f6ebe682eb6a29c96fe4626d7e1e479423347146ebd19a6045eee`。

| 文件（均位于既有 field/） | 基线 SHA-256 | 候选 SHA-256 |
| --- | --- | --- |
| m8r3_serial_chain.py | `d0baddce2bc5a12a79d3c5d19fc657d84804578ec8196f769ed49ef225603fbc` | `16478ac308c7e72bcf8c08e0822f351879088763cdd8dcf745adc9bfafce1d1b` |
| plan.json | `dfa63ab6039a628a27358ecc76d55d3d7b4e686623ad69cab77b386eb4aace67` | `0347230df1f36c94bc5b1e984a347787508d0930ae653125a0494a182e825926` |
| rows.json | `05efa69e4bd608b3ac1d5bef73c14f7ec111951e39d3809e765903f7eb08748b` | `f21b8ac346dcb68f798c6578726fe8fe7e6d761fcf9e3f4556bbb8e721e727fa` |
| m8r3_field_selftest.py | `b832a275322588e3a0f512b3ee7bf76039d11e6cd2f28c7ee3378af024f962bd` | `ce4efe0200a037fb7af0e36c25d12f14c7f0750b975cd9efb28b9eae3d8fe864` |
| M8R3Field.ps1 | `02ccee449087e53d1d9ab3abced3745ba55a6a7e2f2af2534001b4dc90415084` | `908c0d87b3352ee99839ff1f57381ba4b5a4aded32c56c397ed3bb6ae408ab13` |

## 证据说明

`selftest_original.stdout.txt`、`selftest_candidate.stdout.txt` 为原 73 项两份实跑；`boundaries_baseline.stdout.txt` 与 `boundaries_candidate.stdout.txt` 是同一程序/同一输入对照。`prior_raw/` 是被审 Git 历史对象导出的归档输入，供边界重放，不当作新板级事实。

复审过程中先因本地导出缺四件历史 P0 文件得到 71/73，补齐同提交 Git 对象后才得 73/73；首跑保留在云端工作目录。新增探针首跑误把 archive 围栏当成带 base64 标签而报错，修正提取器后核到历史原始 SHA 才继续；未改输入字节以凑通过。没有把这些检查环境错误归给设备。
