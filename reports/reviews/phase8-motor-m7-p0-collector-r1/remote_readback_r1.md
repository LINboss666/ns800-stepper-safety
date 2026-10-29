# M7 P0-R1 远端字节回读（独立于工作树）

日期 2026-10-30。回读对象 `origin/debug/tmc-uart-rootcause-20261028`。
脚本提交 `699bbf4be80653fc3aed68d478a777efbb01232b`；复审对象 `208bf102e6b3bce7159eec672b83a0d6b77f8811`；
固件构建输入仍 `5b564eb962e8518920f581111d7a460a2b1fce64`。

状态：`REMOTE_BYTES_MATCH` / `P0_PACKAGE_READY_FOR_SINGLE_USER_APPROVAL` / `NOT_BOARD_EXECUTED`。
本轮硬件动作仍为 0：未创建串口/DAP/仪器对象，未枚举、未读目标存储器、未下载、未复位、未发目标命令、
未碰供电/接线/SW1、未 arm/start、未输出 STEP/DIR/HIGH。本文只登记 Git 远端事实与离线重放。

## 1. 方法

`core.autocrlf=true`（仓库级，未修改），工作树字节不等于提交字节，所以判据一律取远端 blob：
推送前 `ls-remote` 得 `208bf10…`（确认远端未被推进、本地仅领先 1，可 fast-forward，
不需要 rebase/reset/force），推送后 `ls-remote` 得 `699bbf4…`；再 `fetch` 后用
`git cat-file -p origin/debug/tmc-uart-rootcause-20261028:<path>` 直接按对象字节算 `sha256`。
本轮文本件的规范字节是 LF。CR 计数一律用 `tr -dc '\r' | wc -c`，不用 `grep -c $'\r'`
（后者在本机 Git Bash 会把单 CR 参数吃掉并返回行数）。

## 2. 引用

| 引用 | 远端实测 | 期望 | 判定 |
|---|---|---|---|
| `refs/heads/debug/tmc-uart-rootcause-20261028` | `699bbf4be80653fc3aed68d478a777efbb01232b` | 本轮脚本提交 | 已推进 |
| `refs/heads/main` | `1b3757eec550802a3973e66790af422d81d3d373` | 同 | MATCH |
| `refs/heads/phase7/full-embedded-software` | `6d3fbc559d45673d7550eb73dec5a4ade5bb34df` | 同 | MATCH |
| `refs/heads/phase8/tmc-drive-config` | `1696c59f4b56bae5d503fb18888e379dfad6f76f` | 同 | MATCH |
| `refs/tags/v0.7-phase7-software-board-validated` | 对象 `097935fec5a6f2467261ab1ad449ff0b1d398250` | 同 | MATCH |

## 3. 本轮 16 个交付路径的远端 blob

`git diff --name-only 208bf10 origin/debug/tmc-uart-rootcause-20261028 | wc -l` = **16**；
`reports/firmware/`、`rt-thread-master/`、`tests/` 三个范围在该区间的变更路径数 = **0**。

| 路径 | 字节 | sha256（远端 blob） |
|---|---:|---|
| `reports/agent_handoffs/phase8-motor-m7-current-calibration-r2.json` | 9566 | `6b792991cb824506a8d55acd3c399c456287fbaf37c5ac5a6806de5b54465097` |
| `reports/agent_handoffs/phase8-motor-m7-current-calibration-r2.md` | 9140 | `08e4d3c7d4628935142c9ce118f4a45a9de5b35a41c85d47c9ecec0edcbfddcc` |
| `reports/agent_handoffs/phase8-motor-m7-p0-collector-r1.json` | 10369 | `d6558e729d302e71f13c8328b3ec147697207e24d9221c12badf8072a82596ea` |
| `reports/agent_handoffs/phase8-motor-m7-p0-collector-r1.md` | 7372 | `a80580ff48a09f1f6ae7ef860d542589403e91c1ac2ed295dca1b9e42591e513` |
| `reports/reviews/phase8-motor-m7-current-calibration-r2/p0_package.md` | 9345 | `dd4924b75e1bd70c13fca8198f1628a390a15534605e6079ef41a647a866e26b` |
| `reports/reviews/phase8-motor-m7-current-calibration-r2/p0_voltage_observation.py` | **19642** | **`a2c75ea83cd3842246397a6eff33eea52745c69f8926fb6800a2561fe36cb95a`** |
| `reports/reviews/phase8-motor-m7-current-calibration-r2/verify_p0_candidate.py` | **16343** | **`bc438094d4193d31fba710622c13d41d3fa19208658dec4b4015f11b444769a2`** |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/bin_timestamp_offset_check.txt` | 994 | `758293f4ae2c4884f310a470c7868bcb28d66dc4749d7896e82c0676a6afc2ac` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/m5_verbatim_replay.txt` | 2035 | `5fd7a5d843711bc06930885afd60caa8fa2c4c84d19cb357e7db4e23ba69f29a` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/original_collector_baseline_probes_windows.txt` | 1220 | `6a693f7f906b17699d936d734f02952c06e6aac611847e0bd8d1fa255f1ff14e` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/patch_application_log.txt` | 4704 | `d11e25d59ccd8bc43264ce13b52e07525513387bf35d73d1b25372c166b2addb` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/plan_diff_notes.md` | 5428 | `e4f59ee0bc79207013c73cdc0606e49f65042fe64060abb441d7c671326ec2c5` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/probe_original_collector.py` | 2558 | `00ef4e91c336a37569959b637e52a2c28a8d96b0755a6e5e6e4d8887605c30d4` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/pyocd_reset_help_offline.txt` | 3818 | `1895f52e1ffe3ad9784ce40007202b11bcbde6681cd184b101f9153659dcba6b` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/replay_verbatim_m5.py` | 2654 | `91b43e5b6ea07fbf187f2113c6972304817d76654195e93d96877f9d5fa1ed5c` |
| `reports/reviews/phase8-motor-m7-p0-collector-r1/selftest_r1_windows.txt` | 2700 | `2a84a5a468babb3b8f5024b1e6289a71e258ea9f67c6cf5658d4c4c4cf519aee` |

三个锚：

- 采集器远端 blob = `a2c75ea8…b95a` / 19642 B / CR=0，等于任务书 §4.2 声明值，也等于包内
  `candidate/p0_voltage_observation.py`。
- 验证器远端 blob = `bc438094…69a2` / 16343 B / CR=0，同上。
- 修前基线件在远端是 1220 B / `6a693f7f…f14e`：我本机那份是 CRLF 重定向件（1276 B / 56 CR），
  Git 归一为 LF 后的 blob 与**云端 `results/collector_baseline_probes.txt` 逐字节相等**。
  这既是跨平台一致性的旁证，也说明归档件没有被我改写过。

计算器与反向脚本仍为 `5c7a2340…63ad` / `a32be1db…96d4`，本轮未触碰。

## 4. 六件 M7-R1 镜像与历史证据

远端 blob 实测，全部等于 R1 manifest 与上一轮登记值：

```text
rt-thread.bin              266140  5f080fbc652599c691e31df9825f1149a89248af786fdeacc7ab889bfbf3f8d7
rt-thread.axf             1688212  54479417bff6e9d0be5f2185497621d211dd821e290108a9301a3b918fc6e724
rt-thread-measure-a0.bin   276388  f99f3be502341508471ce1c36902126bee9271ed7f8bc92ef565273affbafa7e
rt-thread-measure-a0.axf 1718624   9a9a5d92e488a316ebb59d41f0ed4da3c7365f4d7aeeac23915a4e2389a15b6f
rt-thread-motor-m1.bin     266244  e614a075ab63eb5b1c4b08e1ab51fb4043f474037ce734b4bcaf666899c8f7da
rt-thread-motor-m1.axf   1688696   2205c7e7ad6bad93e848fd24d1e5f38c76796865a2b21ebcd2d4e22bb4b6d584
```

P0 固定载荷仍是 `reports/firmware/phase8-motor-m7-current-calibration-r1/rt-thread-motor-m1.bin`
= 266244 B / `e614a075…f7da`，AXF = 1688696 B / `2205c7e7…d584`。
M5 归档 motor BIN 仍 259580 B / `01d3e5ab1ad245949385d50c9f1d5fd2bbaac0806c3920e897321bc46db764bc`，
与任务书"板上最后已证 BIN"一致。旧三轮（M5/M6/M7）固件与全部历史证据目录保留，未删除、未重写；
被否定的旧 P0 采集器 `1e34589f…c44f` 仍可从 `208bf10` 的原路径取出，旧 11 项自检
`p0_collector_selftest.txt` 未被 37 项覆盖。

## 5. 用远端字节重跑（不是复述索引结果）

从 `origin/debug/tmc-uart-rootcause-20261028` 直接导出采集器、验证器与计算器到仓外镜像目录
（三个件 CR=0、散列同上表），在 Windows Python 3.12 实跑：

| 入口 | 远端字节实测 |
|---|---|
| `p0_voltage_observation.py --selftest` | `SELFTEST_RC=0`，stderr 0 字节，`SUMMARY cases=37 pass=37 fail=0`，`DEVICE_ACTIONS=0 / NO_REAL_TRANSPORT` |
| `replay_verbatim_m5.py <采集器> <M5 final_pass 原字节>` | `REPLAY_RC=0`，`VERBATIM_M5_REPLAY=4/4 accept` |

本机 pyOCD `0.45.1`，`python -m pyocd reset --help` rc=0（不为查参数连设备），
采集器 argv 用到的 `--method sysresetreq` / `--connect attach` / `--core` / `--no-config` / `--no-wait`
逐项在帮助文本里存在；原文见 `pyocd_reset_help_offline.txt`。

## 6. 角色字段与不自引用

| 角色 | 值 | 依据 |
|---|---|---|
| `firmware_code_commit` | `5b564eb962e8518920f581111d7a460a2b1fce64` | 六件镜像的实际构建输入，本轮未重编 |
| `reviewed_delivery` | `208bf102e6b3bce7159eec672b83a0d6b77f8811` | 本次复审对象，也是采集器的起点 |
| `script_code_commit` | `699bbf4be80653fc3aed68d478a777efbb01232b` | 已生成、已推送、远端 tip 实测 |
| `calculator_commit` | `01da9d62c65bb482db7eaec2d5e6a62ae492cf86`（R2 工具提交，本轮未改） | 计算器 GO 所依据的字节 |
| `delivery_head` | 本文所在提交，**不在本文写死自己的 SHA** | `git log -1 --format=%H -- reports/reviews/phase8-motor-m7-p0-collector-r1/remote_readback_r1.md` |

P0-R1 交接 JSON 的 `roles.script_code_commit` 在同一后续提交里填成 `699bbf4…`；
`delivery_head` 保留解析规则。§3 表锚定 `699bbf4`，后续提交只新增本文并改写该 JSON 的两个角色字段。

## 7. 本轮不声称的事

- 不声称 P0 已执行：`NOT_BOARD_EXECUTED`，需用户对该整包**一次**批准。
- 不声称任何板上状态：`M5_BOARD_IMAGE_UNCHANGED` 的依据是 `NEW_FLASH_ACTIONS=0` 加历史回读，不是本轮实读。
- 不声称横幅 `5.3.0 build Oct 29 2026 23:11:52` 已被板上读到；它仍是归档 BIN 的字节恢复。
- 37/37 与 4/4 只证明**脚本合同**，不证明新镜像已在板上运行；四份 M5 输入是历史归档，不是新测量。
- 不给 P0 结果预设任何精度结论：上限仍是 `VOLTAGE_OBSERVATION_ONLY`。
- `example_*_HOST_SYNTHETIC.json`、合成夹具与 `WAIT_DMM` 假读数一律禁止下发或当现场参考。
