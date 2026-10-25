# 交接：phase8-e40-artifact-search —— e40 旧固件离线查找

任务书：`NS800_E40_Artifact_Search_Qoder_Task_20261025.md`　日期：2026-10-25（Asia/Shanghai）　执行者：本地 Qoder Agent

| 项 | 值 |
|---|---|
| document_base / 远端分支头 | `2ab886ff9a60a2b2283ff3ba200d337235cb2da9` |
| A0 实现 code_commit | `b27e5f14bdb62f645b93eace23800b40369717d4` |
| e40 固件提交 / 标签解引用 | `e40a9b95ef709e6a80444f9f3949dfdd70ba96ba` |
| 搜索整体结论 | **`NOT_FOUND_IN_SEARCHED_SCOPE`** |
| 候选分类计数 | E40_LINKED 0 ／ POSSIBLE 0 ／ OTHER_KNOWN 15 ／ NOT_IDENTIFIED 0（共 15 个候选 + 7 条记录）|
| 板上身份 | 继续 `USER_REPORTED_E40`（本轮未读 Flash）|
| BLOCKED_ROLLBACK | **仍未关闭**（本轮不改动其状态）|
| 测试／构建／硬件动作 | 0 ／ 0 ／ 0（`production_changed=false`）|

## 1. 结论

在任务书允许的三层有限范围内，**没有**找到任何仍然存留的 e40 时代 BIN/HEX/AXF/ELF。找到的是三类东西，它们都不是原烧录文件：

1. e40 的构建与烧录**记录**（`R-01..R-05`）：两次 clean Rebuild All 日志 + 两份烧录回执 + 落盘的下载配置。
2. e40 的**源码**（git 对象完整），且个别文件至今与 e40 blob 逐字节相同 —— 这只支持将来按需重建，不构成"存在过原烧录文件"。
3. 一个此前**文档未记录绝对路径**的旧工作副本 `OLD_PROJECT_01`，内含更早一代（2026-09-10）的真实构建产物与烧录回执（`C-12..C-14`、`R-06/R-07`）。它的最新文件停在 2026-09-10 23:08，e40 时间窗口内整树 0 命中。

`POSSIBLE_E40_ARTIFACT = 0` 不是"没找到东西"，而是"所有命中项的身份都能确定"：唯一还落在当年被烧录那个路径上的 AXF/BIN（`C-01`/`C-02`）与已接受的 A0 归档镜像逐字节相同，判为本轮新产物；其余命中项各有独立排除依据（日期早于 e40／尺寸量级不符／目录内无固件条目／非本项目）。

## 2. 最有价值的产出（不是固件，而是身份链与定位）

| 产出 | 内容 |
|---|---|
| 身份链 | 21:25:21 构建 → 21:26:03 烧录 → 21:28:05 提交 → 23:25:27 标签 → 00:00:44 再构建 → 00:01:24 再烧录；两次烧录之间 `.c/.h/*.uvprojx/rtconfig.h` **零差异** ⇒ 同一份映像 |
| 剩余缺口 | 21:25:21 那次构建的工作树是否恰好等于 `e40a9b9` 的树（提交在 3 分 44 秒之后）—— Git 无法反驳，明确登记为未证明 |
| 代次判别 | 131 个 compiling 行 + Program Size 129306/46578/2200/83212（装载 178084 B）；当前默认目标 133 行 ⇒ 可用于将来反向排除 |
| 「旧仓库」定位 | 由 开发进度.md:881 / 调试记录.md:86 / 调试记录.md:161 三条无路径的记载，按名+内容+日期命中共满足条件的旧副本（**INFER 级**，目录内无 .git、无散列可自证）|

## 3. 候选与记录（散列/尺寸/时间全部本轮实测，详见证据 §D/§F）

| 代号 | 类型 | 字节 | mtime | SHA-256 前 12 | 结论 |
|---|---|---:|---|---|---|
| `C-01` BSP/build/rt-thread.axf | ELF32/AXF (armclang) | 1645448 | 2026-10-25 20:00:47 | `76d19ced06eb` | `OTHER_KNOWN_ARTIFACT` |
| `C-02` BSP/rtthread.bin | raw BIN | 251148 | 2026-10-25 20:00:47 | `41717367e4e1` | `OTHER_KNOWN_ARTIFACT` |
| `C-03` BSP/build/a0/rt-thread-a0.axf | ELF32/AXF (armclang) | 1674612 | 2026-10-25 20:01:08 | `c8176c19188f` | `OTHER_KNOWN_ARTIFACT` |
| `C-04` BSP/build/a0/measure-a0.bin | raw BIN | 261100 | 2026-10-25 20:01:08 | `358fd81ae585` | `OTHER_KNOWN_ARTIFACT` |
| `C-05` PROJECT_ROOT/reports/firmware/phase8-target-measurement-a0/default.bin | raw BIN | 251148 | 2026-10-25 20:16:19 | `41717367e4e1` | `OTHER_KNOWN_ARTIFACT` |
| `C-06` PROJECT_ROOT/reports/firmware/phase8-target-measurement-a0/measure-a0.bin | raw BIN | 261100 | 2026-10-25 20:16:19 | `358fd81ae585` | `OTHER_KNOWN_ARTIFACT` |
| `C-07` ARCHIVE_01 (rt-thread-master.zip) | ZIP (45447 条目) | 605015358 | 2026-09-03 20:29:08 | `eb8f5b5e6867` | `OTHER_KNOWN_ARTIFACT` |
| `C-08` ARCHIVE_GROUP_08 (phase8_f2d_r2_source_review.zip) | ZIP (682 条目) | 2282925 | 2026-10-18 20:36:05 | `a9b728e8b249` | `OTHER_KNOWN_ARTIFACT` |
| `C-09` UNRELATED_MCU_01 (…/<另一个 MCU 工程>.axf) | ELF32/AXF | 1017356 | 2026-09-20 14:00:02 | `41a376ea1ce6` | `OTHER_KNOWN_ARTIFACT` |
| `C-10` DOWNLOAD_01 (NS800_FLASH_Full_v1.0.zip) | ZIP (16 条目) | 26266 | 2026-09-10 19:45:43 | `ea0e63c94c37` | `OTHER_KNOWN_ARTIFACT` |
| `C-11` DEVKIT_01X + DEVKIT_01 (厂商 NS800RT7P65 开发包) | 厂商资料包(索引 HTML) | 9529 | 2026-04-21 19:38:03 | `ec4cf070d2ce` | `OTHER_KNOWN_ARTIFACT` |
| `C-12` OLD_BSP_01/build/rt-thread.axf | ELF32/AXF (armclang) | 947336 | 2026-09-10 21:14:19 | `02d7c6aba978` | `OTHER_KNOWN_ARTIFACT` |
| `C-13` OLD_BSP_01/rtthread.bin | raw BIN | 98228 | 2026-09-10 21:14:19 | `f8f8a08520b1` | `OTHER_KNOWN_ARTIFACT` |
| `C-14` OLD_BSP_01/rtthread.elf + rtthread.map | ELF32 (scons/GCC 路线) | 988632 | 2026-09-03 23:25:05 | `47aca1e94312` | `OTHER_KNOWN_ARTIFACT` |
| `C-15` ARCHIVE_02 (= ARCHIVE_01 同散列) | ZIP (45447 条目) | 605015358 | 2026-09-03 20:29:08 | `eb8f5b5e6867` | `OTHER_KNOWN_ARTIFACT` |

| 记录 | 路径代号 | 字节 | mtime | SHA-256 前 12 |
|---|---|---:|---|---|
| `R-01` | BSP/build_agent2.log | 3538 | 2026-09-25 21:25:21 | `75290cec9007` |
| `R-02` | BSP/build_c.log | 3538 | 2026-09-26 00:00:44 | `665aa8f6a00f` |
| `R-03` | BSP/flash_agent.log | 192 | 2026-09-25 21:26:03 | `fbb47f059834` |
| `R-04` | BSP/flash_c.log | 192 | 2026-09-26 00:01:24 | `1a67dda029f4` |
| `R-05` | BSP/project.uvoptx | 61950 | 2026-09-25 13:46:08 | `62d2d8f14afb` |
| `R-06` | OLD_BSP_01/build_test.log | 3114 | 2026-09-10 21:14:20 | `17ed5c960b45` |
| `R-07` | OLD_BSP_01/flash_test.log | 177 | 2026-09-10 21:22:22 | `35b9f0de1467` |

相同 SHA-256 去重但保留全部来源：`C-02 == C-05`、`C-04 == C-06`、`C-07 == C-15`。本轮未找到 HEX，因此没有做"装载字节摘要"；AXF/ELF 一律按原格式登记，未悄悄转 BIN、未声称存在原 BIN。

## 4. 与已接受 A0 镜像的关系（防误认）

- `default.bin` 251148 B `41717367…425de`、`measure-a0.bin` 261100 B `358fd81a…b0e1b` —— 都属**新默认/新测量目标**，都不是 e40。
- 排除依据：e40 Program Size `129306/46578/2200/83212` 与 `173762/76806/2188/83784`、`180802/79718/2188/91504` 在 Code/RO/RW 三项量级不同；schema=2、120 B 记录、64 槽 7680 B、`spm` 命令注册、三入口插桩只属于新测量实现。
- `default.bin` **不是**板上 e40 的已验证回退镜像 —— 不因文件名含 `default`/`rt-thread` 就认作 e40。

## 5. 每条证据的三分列（证明到哪里／仍不能证明什么）

- **P-1** 直接读到：build_agent2.log / build_c.log 全文(LOG+FS)
  - 证明到：e40 上板那次构建的目标名、131 个编译单元、Program Size 四项、0/0 终态与输出路径; 两次构建输入零变化 ⇒ 两次烧录同源
  - 仍不能证明：日志不含散列/文件清单/源码 SHA ⇒ 无法把某个 BIN 字节流绑定到 e40a9b95ef70
- **P-2** 直接读到：flash_agent.log / flash_c.log 全文(LOG+FS)
  - 证明到：2026-09-25 21:26:03 与 2026-09-26 00:01:24 各完成一次真实烧录, 下载件为工程内 build/rt-thread.axf, Keil 自身 Verify OK
  - 仍不能证明：该 AXF 当时的字节内容/散列; 也不能证明今天板上仍是它(未读 Flash)
- **P-3** 直接读到：project.uvoptx(GIT 跟踪文件 + FS)
  - 证明到：TargetName=rt-thread, pMon=BIN\CMSIS_AGDI.dll ⇒ 当时落盘的下载/调试驱动是 CMSIS-DAP
  - 仍不能证明：具体探针序列号与端口; 本轮未连接任何设备
- **P-4** 直接读到：git show/for-each-ref/diff --numstat(GIT)
  - 证明到：烧录在提交前 2 分 02 秒与提交后 2 小时 33 分; 期间 .c/.h/uvprojx/rtconfig.h 零差异, 全量差异只有 3 份 .md
  - 仍不能证明：21:25:21 那次构建的工作树是否恰好等于 e40a9b9 的树(提交在 3 分 44 秒之后) —— 这是本链剩余的身份缺口
- **P-5** 直接读到：PHASE7_PROGRESS.md / PHASE8_HARDWARE_VALIDATION_20260925.md(DOC)
  - 证明到：上板窗口、板型、COM5/115200、DAPLink 记载、未接电机、逐项现象、最终 Program Size
  - 仍不能证明：任何镜像字节身份(文档不携带散列), 也不覆盖"板上现在仍是该固件"
- **P-6** 直接读到：git hash-object 与 git rev-parse e40a9b9:<path> 逐字节比较(GIT+FS)
  - 证明到：e40 源码树完整可取; diagnosis.c / current_adc.c / project_config.c / main.c 至今与 e40 blob 相同; supervisor.c / motor.c 已不同
  - 仍不能证明：用它重建的映像与当年烧录字节相同(工具链补丁级差异、链接顺序、__DATE__/__TIME__) ⇒ 将来也只能标 E40_SOURCE_REBUILD
- **P-7** 直接读到：OLD_BSP_01 的 build/rt-thread.axf、rtthread.bin、rtthread.elf、build_test.log、flash_test.log(FS+LOG)
  - 证明到：文档所称"旧仓库"这一层已被实地搜索并排除(INFER 级等同 + FS 级日期排除); 该副本属 Phase 2 移植代次(Code=77098, applications/ 内无安全链源码)
  - 仍不能证明：任何 e40 代映像的存在; 也不能证明板上现状。反向提供的只是"同一板卡 + 同一 Keil/CMSIS-DAP 链可反复下载并 Verify 成功"这一能力证据

## 6. 完整性兜底（不依赖文件名）

- **时间兜底**：工作树 mtime 2026-09-25..09-27(排除 .git)共 26 个文件: 5 .c + 6 .h + 8 .py + 4 .log + 2 .md + 1 .uvoptx; mime 判定 11 text/x-c + 14 text/plain + 1 text/xml, 二进制 0 个
- **尺寸兜底**：仓库树内(排除 .git)>900 kB 条目实测列示: 固件类只有 C-01/C-03 两个 1.6 MB 级 AXF 与归档的 C-05/C-06, 全部是 2026-10-25 的本轮产物
- **Git 对象兜底**：log --all 的 *.bin/*.hex/*.axf/*.elf 只命中本轮两份归档; 10 个悬空 blob 最大 53383 B(e40 装载内容 178084 B, AXF 1.3–1.6 MB)

## 7. 搜索范围与缺口

| 代号 | 层 | 覆盖 |
|---|---|---|
| `PROJECT_ROOT` | 1 | BSP/build/**, BSP/build/a0/**, BSP/rtthread.bin, BSP/*.log(23 个), project.uvprojx/uvoptx, _s2work/**(固件类 0 个), reports/**, tests/**, 仓库根 6 个源码审查 zip + ARCHIVE_01 |
| `BSP` | 1 | 当前默认/测量目标产物全部为 2026-10-25 本轮构建, 占据的正是当年被烧录的同一路径 |
| `uvprojx OutputDirectory + 烧录日志 Load 路径` | 2 | OutputDirectory 指向工程内 build/ 与 build/a0/; 两次 e40 烧录的 Load 路径都在 PROJECT_ROOT 内 |
| `OLD_PROJECT_01 / OLD_BSP_01 / SNAPSHOT_01` | 3 | 按名 + 内容 + 日期命中; e40 时间窗口(2026-09-20..10-01)全树 0 命中; 整树最新 mtime 2026-09-10 23:08; 内含 Phase 2 代次的真实构建产物与烧录回执 |
| `ARCHIVE_01 / ARCHIVE_02` | 3 | 45447 条目; ns800rt7p65-nssinepad 33 条目且固件类 0; 条目时间 2026-09-02/03 早于 e40 |
| `ARCHIVE_GROUP_03..08` | 3 | 六个源码审查包(最大 2282925 B, 682 条目)固件类条目 0 |
| `SCRATCH_GROUP_01..10 + SCRATCH_LOG_01` | 3 | 十个目录 .axf/.bin/.hex 全部 0 个(另一处按名探测到的 r1_smoke2 实为盘根的 .log 散件而非目录) |
| `EVIDENCE_TEMP` | 3 | 均为历轮主机夹具/探针/日志, 无固件原件 |
| `DOWNLOAD_01 / DOWNLOAD_01X` | 命中后排除 | 本项目 Flash 移植源码包, 最新条目 2026-09-10 07:37, 固件类 0 |
| `DEVKIT_01 / DEVKIT_01X` | 命中后排除 | 解包树最新 2026-04-21 19:38(zip 本体 2026-08-29); 唯一固件类文件为 2025-12-08 的 PCAN 下载器工具 11192 B ⇒ 日期整体排除 |
| `UNRELATED_MCU_01` | 命中后排除 | 另一 MCU 工程的 AXF(2026-09-20, 早于 e40), 与 NS800 无关 |
| `RECYCLE_D / RECYCLE_C` | 名字级探测 | 按 *rt-thread*/rtthread*/*.axf/*.hex 匹配 0 命中; 未解 $I 元数据 |
| `CLOUD_SYNC_01 / CLOUD_SYNC_02` | 名字级探测 | 按 *ns800*/*sinepad*/*novosns*/rtthread*/rt-thread* 匹配 0 命中; 未调客户端、未登录 |

缺口逐条（详见证据 §I）：`G-1` "旧仓库"文档三处提到但从未记录绝对路径 ⇒ 本轮定位为 `OLD_PROJECT_01`（INFER 级），残留"若用户另有一个更早副本请给路径"；`G-2` 旧副本全树时间窗扫描本轮**已完整跑完**（0 命中）；`G-3..G-9` 分别为网盘/回收站仅名字级探测、卷影副本未查、编译器目录未查、上游 zip 未逐个解包、无任何外部检索、以及板上现状未验证。

执行错误如实登记：一次全仓 find 与一次旧副本全树 mtime 扫描先后超时（前者改有界查询，后者改后台跑完、退出码 0）；一次 `git for-each-ref --date=iso` 因选项不存在而失败后改用 `--format`。没有因权限失败的根。

## 8. BLOCKED_ROLLBACK 现状

本轮**没有**可供审查的 e40 恢复镜像 ⇒ 状态保持 `BLOCKED_ROLLBACK_OPEN`，不改写、不自我关闭。要关闭还缺（按成本与价值排序）：

- 用户提供其它备份位置(更早工作副本 / 其它机器 / 网盘具体目录 / 移动介质) —— G-1 残留
- 或另行授权一次板上 Flash 只读转储 + 散列(唯一能回答"板上现在是什么"的直接证据, 本轮未申请)
- 或明确决定以 e40a9b9 源码重建并登记 E40_SOURCE_REBUILD, 同时接受它与当年烧录件不逐字相同

已确定可用的事实只是**能力证据**，不是回退依据：

- 板载 DAP + uvoptx pMon=BIN\CMSIS_AGDI.dll
- COM5 / 115200 / 8N1(文档记载, 本轮未打开)
- 四次成功烧录回执(e40 两次 + 旧副本一次 + 更早一次)

本报告不授予下载、恢复、Flash 读取或重建权限。

## 9. 原件保护、隐私与写入自证

- 所有被搜索文件仅以读方式打开；未移动／重命名／改时间／chmod／删除／覆盖；未运行任何候选程序；未调用历史下载脚本；zip 只列目录、未解包、未执行包内脚本。
- 仓库内只新增任务书 §5 允许的三份文件；未改生产/HAL/内核、四宏、Keil 工程、`tests/host`、既有 A0 证据、操作包、阶段文档、`.gitignore`；未上传任何 AXF/ELF/压缩包/固件原件。
- 脱敏规则：仓库内证据不出现本机用户名/邮箱/主机名; 绝对路径写成「代号 + 相对路径」; Keil 的 project.uvguix.<USER> 中 <USER> 为替换项; 与文件身份有关的日志原文按原样保留, 未改写任何身份数据以消除矛盾
- 完整路径映射只在仓外留存：`EVIDENCE_TEMP/PATH_INDEX.txt`（不入库）。**本地索引位置**即此代号，其绝对路径见回执口头说明。

## 10. 继承的既有结论与现场信息

- A0-R1 交付 HEAD `2ab886ff9a60a2b2283ff3ba200d337235cb2da9`（code_commit `b27e5f14bdb62f645b93eace23800b40369717d4`）已取得限定离线软件、已声明主机合同与两份归档镜像身份的 **SOFTWARE_REVIEW_GO**；A0-01..04 四组阻断关闭，无新增必须返修项。本 GO **不是**烧录/启动/复位/目标命令/测量执行许可。按复审要求不另开纯登记轮，此处随实际授权任务登记，未为此重跑任何主机链或重建 Keil。
- S2 软件 GO 仍是 `bcdec631`，准备轮 GO 仍是 `bbf66ba`；四宏继续 `RT_FALSE`。
- 现场与工具信息按 **USER_REPORTED** 登记（本轮未实测）：外接 AC/DC 24 V 供电；逻辑电源来自 USB 经板载 LDO，最近确认未上电，断电=拔 USB 或适配器开关；TMC 模块在位、可拔出；电机物理未接；SW1 当前使能；有人在现场；板载 DAP（DAPLink/CMSIS-DAP）；控制台 COM5 / 115200 / 8N1
- **这些现场与工具信息不构成本任务的连接、上电、串口或烧录授权。**

## 11. STOP

```text
E40_ARTIFACT_SEARCH_COMPLETE / AWAITING_ARTIFACT_REVIEW
NO HARDWARE AUTHORIZATION
NO FLASH READ / NO BUILD / NO SERIAL / NO PROGRAM / NO RESET
```

随后等云端把恢复条件与已接受的 A0 镜像、板载 DAP、COM5、24 V 现场信息合并进具体板级包，由用户一次批准。本轮未自行启动下一阶段。
