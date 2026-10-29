# M7-R2 远端字节回读（独立于工作树）

日期：2026-10-30。回读对象：`origin/debug/tmc-uart-rootcause-20261028`。
本轮工具/计划提交：`01da9d62c65bb482db7eaec2d5e6a62ae492cf86`（下称 **工具提交**）。
被审交付：`c78f7865292d387a0533c2f22c39e8c848bcd689`。固件构建输入：`5b564eb962e8518920f581111d7a460a2b1fce64`。

状态：`REMOTE_BYTE_READBACK_DONE` / `AWAITING_INDEPENDENT_REVIEW`。
本轮硬件动作仍然为 0：未创建串口/DAP/仪器对象，未枚举、未读目标存储器、未下载、未复位、
未发目标命令、未碰供电/接线/SW1、未 arm/start、未输出 STEP/DIR/HIGH。本文只登记 Git 远端事实。

## 1. 回读方法（为什么不算"本地绿"）

`core.autocrlf=true`（仓库级，本轮未改任何 git 配置），工作树字节不等于提交字节。
因此全部判据取 **远端 blob**：先 `git -c http.proxy=http://127.0.0.1:7892 ls-remote origin` 与
`fetch --tags`，再用 `git cat-file -p origin/debug/tmc-uart-rootcause-20261028:<path>` 直接读对象，
按字节算 `sha256`。文本件的规范字节是 LF，不经过工作树。

推送前后各读一次远端分支 tip：推送前 `c78f786…`（确认远端未被他人推进，本地仅领先 1 个提交，
可 fast-forward，不需 rebase/reset/force），推送后 `01da9d6…`。

## 2. 引用回读（冻结项逐字相等）

| 引用 | 远端实测 | 任务声明 | 判定 |
|---|---|---|---|
| `refs/heads/debug/tmc-uart-rootcause-20261028` | `01da9d62c65bb482db7eaec2d5e6a62ae492cf86` | 本轮工具提交 | 已推进 |
| `refs/heads/main` | `1b3757eec550802a3973e66790af422d81d3d373` | 同 | MATCH |
| `refs/heads/phase7/full-embedded-software` | `6d3fbc559d45673d7550eb73dec5a4ade5bb34df` | 同 | MATCH |
| `refs/heads/phase8/tmc-drive-config` | `1696c59f4b56bae5d503fb18888e379dfad6f76f` | 同 | MATCH |
| `refs/tags/v0.7-phase7-software-board-validated` | 对象 `097935fec5a6f2467261ab1ad449ff0b1d398250`，解引用 `e40a9b95ef709e6a80444f9f3949dfdd70ba96ba` | 同 | MATCH |

## 3. 本轮 21 个交付路径的远端 blob（锚定 `01da9d6`）

`git diff --name-only c78f786 origin/debug/tmc-uart-rootcause-20261028 | wc -l` = **21**，
与暂存清单逐条一致；产品 `*.c/*.h`、HAL、内核、Keil 工程、`bsp/`、`libraries/` 的变更路径数 = **0**。

| 路径（前缀省略 `reports/`） | blob | 字节 | sha256（远端 blob 字节） |
|---|---|---:|---|
| `agent_handoffs/phase8-motor-m7-current-calibration-r1.json` | `207b13dca6c7…3315` | 20651 | `db37bd8e5b4613ed021ddfb3e5e8be1ca75037a07c16162f66389fada9bc1606` |
| `agent_handoffs/phase8-motor-m7-current-calibration-r1.md` | `132d2b2a4319…1bd09` | 14023 | `9acb902d1dd4f0a8d5518447f314023f8003b185456f3b711fc113cae981fd25` |
| `agent_handoffs/phase8-motor-m7-current-calibration-r2.json` | `5d3c2ad7d205…73c9` | 7154 | `be6506445a62040451cc681c6071f039ee194f28da5ef1ecea248da6a3292f4c` |
| `agent_handoffs/phase8-motor-m7-current-calibration-r2.md` | `098bc54c785a…6f15` | 6557 | `8c93ff0e5989a66017d16202e510f7a98270f1239381f1cb5bf94f978e5250bd` |
| `reviews/…-r2/calcalc_selftest_r2.txt` | `84458ecd9b4e…f889` | 2475 | `b8ea8c13224713ec5e12c15f72a2329b658fdda056fc509feb16f5102745b021` |
| `reviews/…-r2/calculator_r2.md` | `38898a9001eb…92d1` | 7113 | `f013aed6902a52af91d9c0c20cf083d50a59d1059f9897d8b55d149a8589181f` |
| `reviews/…-r2/example_input_HOST_SYNTHETIC.json` | `ecd889ebdba5…d174` | 2575 | `115b6f971eaf20dbdd2bc5958b4cfab1ad73849abe3728d90f35eb17e4757760` |
| `reviews/…-r2/example_result_HOST_SYNTHETIC.json` | `a5418198b306…fff10` | 6380 | `ff054044577d391879dff3557eb06ea32cadb7b64fad437fb3564c35d3dd49fc` |
| `reviews/…-r2/p0_collector_selftest.txt` | `e49bff624303…f42f` | 789 | `c4aef656032e5b2230f240dc852ca08f5b42cc4163b8086834b259abfe41581c` |
| `reviews/…-r2/p0_package.md` | `4f52b148b5a1…07fb` | 12085 | `dbb1c5f886b42cbf5701190037173043d4373aa70fb1123c66e9f68ba67e4542` |
| `reviews/…-r2/p0_voltage_observation.py` | `6ea9689be8e4…670e` | 15503 | `1e34589fd3f0250abc5db6a63df2800cf8883c6f65de6219ff350540d138c44f` |
| `reviews/…-r2/patch_application_log.txt` | `b96949768dda…3832` | 1657 | `597de145c7bab019bf69ab8c337a101fd9a46ff319bfd035022938487c626099` |
| `reviews/…-r2/probe_r3_remaining.py` | `dbae0fcdc653…8c55` | 4667 | `8cd0a5ba2f85a4149c9157322702a311838b185ffb03e4328c7873549c850765` |
| `reviews/…-r2/probe_r3_same_inputs_POST_candidate_tool.txt` | `7215ee6cfb6d…2768` | 1174 | `d8fe5eb80d0f0b6c6047b935248d0706627a3932097fa8d03a88150221b9bfe8` |
| `reviews/…-r2/probe_r3_same_inputs_PRE_start_tool.txt` | `68ae344270d3…43e3` | 1499 | `23ce5d0188c9d527ee1b3ffcc2ea04a64848561363d28096f45f9b0c93dfeaca` |
| `reviews/…-r2/r1_reverse_run1_NOT_DETECTED_local.txt` | `2ff4bdac3d76…00e9` | 227 | `fe35631c0c990e65da6255bdde52c3dafcfef26294d3a3419b2347d028ef04a5` |
| `reviews/…-r2/reverse_r2_full_local.txt` | `ed49a4d4e11e…0334` | 298 | `01990c88be0894b5b541fcb28be166707ad03b2eb7b52ab8f9dba4b863b71c0f` |
| `reviews/…-r2/verify_calc_candidate.py` | `7f91878ea40c…d01c` | 5469 | `18d03637a88820cc5bc962b85b00e1f7d2aa920ce7d9afc00bcea823b13ae0c2` |
| `reviews/…-r2/verify_calc_candidate_r2.txt` | `c74c0a136285…c891` | 982 | `88a081af1669f55d22888ccde529ef4bdf6ddda4695199b208a122f2ad2016ff` |
| `reviews/phase8-motor-m7-current-calibration/m7_calcalc.py` | `24e283d8c1f7…f803` | 54267 | `5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad` |
| `tests/host/run_m7_reverse.sh` | `a754898c5d36…3cd9d` | 10083 | `a32be1db2a579bcda6afe0f45bfba2fe9d9bd5bc66d71f6789aeadc6d1de96d4` |

三个关键锚：

- 计算器远端 blob = `5c7a2340…63ad` / 54267 B，**与任务书 §5 候选逐字节相等**；导出件实测
  CR 字节 0、LF 字节 1153、总 54267，即远端存的就是规范 LF。
- 反向脚本远端 blob = `a32be1db…96d4` / 10083 B，本轮只改 REV5a 注入锚点。
- P0 采集器远端 blob = `1e34589f…c44f` / 15503 B，与 `p0_package.md`、交接 JSON 内登记的值同口径。

## 4. 六件 M7-R1 镜像：远端仍为原字节

按 `reports/firmware/phase8-motor-m7-current-calibration-r1/build_manifest.json` 的登记值核对远端 blob：

| 文件 | manifest 字节 | manifest SHA-256 | 远端实测 | 判定 |
|---|---:|---|---|---|
| `rt-thread.bin` | 266140 | `5f080fbc652599c691e31df9825f1149a89248af786fdeacc7ab889bfbf3f8d7` | 同 | MATCH |
| `rt-thread.axf` | 1688212 | `54479417bff6e9d0be5f2185497621d211dd821e290108a9301a3b918fc6e724` | 同 | MATCH |
| `rt-thread-measure-a0.bin` | 276388 | `f99f3be502341508471ce1c36902126bee9271ed7f8bc92ef565273affbafa7e` | 同 | MATCH |
| `rt-thread-measure-a0.axf` | 1718624 | `9a9a5d92e488a316ebb59d41f0ed4da3c7365f4d7aeeac23915a4e2389a15b6f` | 同 | MATCH |
| `rt-thread-motor-m1.bin`（P0 载荷） | 266244 | `e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da` | 同 | MATCH |
| `rt-thread-motor-m1.axf` | 1688696 | `2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584` | 同 | MATCH |

另：`git diff --name-only c78f786 origin/… -- reports/firmware/ | wc -l` = **0**，
即本轮没有新增或改写任何归档镜像；`reports/firmware/phase8-motor-m5-fault-log/rt-thread-motor-m1.bin`
仍为 259580 B / `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，
与任务书 §2"板上最后已证 BIN"一致。旧三轮（M5/M6/M7）固件与历史证据目录均保留，未删除、未重写。

## 5. 用远端字节重跑（不是复述上一轮结果）

把 §3 的三个 `.py` 从 **远端 blob** 导出到仓外空目录（`C:\Users\LMX\AppData\Local\Temp\m7r2_remote_verify`），
在 Windows Python 3.12 实跑：

| 入口 | 远端字节实测 |
|---|---|
| `python m7_calcalc.py --selftest` | `SUMMARY cases=54 pass=54 fail=0` / `M7 CALCALC SELFTEST: PASS` |
| `python verify_calc_candidate.py m7_calcalc.py out_remote` | `SUMMARY 21 pass 21 fail 0 []` |
| `python probe_r3_remaining.py . probe_out_remote m7_calcalc.py` | `HEADER_ONLY / MISSING_DECISION_TAIL / NUL_IN_NEGATIVE_DECISION / CHANGED_VERSION / REUSED_FIT_AS_HOLDOUT_RENAMED` 全部 `rc=1` 且无命令；`CONTROL_FULL_REAL_FORMAT` `rc=0 status=DRAFT_NOT_APPROVED`；`NONLINEAR_INTERVAL_CORNER` `reported_bound=10.317726165210626 status=REJECT usable=false`（corner_prediction `108.90979800000059`，exact difference `10.009798000000586`，target 10.0） |

结果与 `calcalc_selftest_r2.txt`、`verify_calc_candidate_r2.txt`、
`probe_r3_same_inputs_POST_candidate_tool.txt` 登记值一致，说明远端存的确实是修好的那份字节。

反向电池本轮仍引用工具提交前的本地实跑（`reverse_r2_full_local.txt`：REV1 3/3、REV2 1/1、
REV3 1/1、REV4 2/2、REV5 3/3、REV6 2/2 → PASS，AGENT_LOCAL 来源等级）与云端记录
（REV3 子 runner `rc=4`、总入口 `rc=1`、C22f 确实 FAIL，focused `cal_static` 具名确认）。
两份并列保留，云端原因未定，不据此声称六族在任一平台整包 PASS。

## 6. 角色字段与不自引用

| 角色 | 值 | 依据 |
|---|---|---|
| `firmware_code_commit` | `5b564eb962e8518920f581111d7a460a2b1fce64` | 六件镜像的实际构建输入，本轮未重编 |
| `reviewed_delivery` | `c78f7865292d387a0533c2f22c39e8c848bcd689` | 本次复审对象 |
| `calculator_code_commit` | `01da9d62c65bb482db7eaec2d5e6a62ae492cf86` | 已生成、已推送、远端 tip 实测 |
| `delivery_head` | 本文所在提交，**不在本文写死自己的 SHA** | 解析规则：`git log -1 --format=%H -- reports/reviews/phase8-motor-m7-current-calibration-r2/remote_readback_r2.md` |

交接 JSON `reports/agent_handoffs/phase8-motor-m7-current-calibration-r2.json` 的
`roles.calculator_code_commit` 已在同一后续提交里填成 `01da9d6…`；
`roles.delivery_head` 保留为上面这条解析规则。§3 表格锚定 `01da9d6`，
后续提交会改写该 JSON 与新增本文，因此表内 `…-r2.json` 那一行只代表 `01da9d6` 时的远端字节。

## 7. 本轮不声称的事

- 不声称 P0 现场包已执行：它仍是 `PREPARED_NOT_EXECUTED`，需用户对该整包一次批准。
- 不声称任何板上状态：本轮无设备对象，`M5_BOARD_IMAGE_UNCHANGED` 的依据是
  `NEW_FLASH_ACTIONS=0` 与历史回读，而非本轮实读。
- 不声称 `5.3.0 build Oct 29 2026 23:11:52` 已被板上实读；它是从归档 BIN 恢复的字节，
  并只按 M5 先例给出方法可信度。
- 不给计算器补"完整最坏栈/全工况精度"这类它没证明的结论；两点模型之外的拟合点数
  仍是 `UNSUPPORTED_BOUND_MODEL`。
- 不把 `example_*_HOST_SYNTHETIC.json` 或 `probe_r3_remaining.py` 的任何数值当现场参考或下发命令。
