# M8 远端逐 blob 回读（独立于工作树）

- 生成时刻(UTC): 2026-10-30T16:49:37Z
- 判据来源: 远端 \`origin/feature/m8-sg-adc-relative-protection\` 的 **Git blob 字节**（\$GIT_DIR 独立 fetch 后 cat-file）。
  仓库 core.autocrlf=true，所以文本文件入库为 LF，工作树为 CRLF；
  本表两侧身份都给出，避免把'规范化后的相等'说成'原始字节相等'。

## 1. 远端引用（ls-remote 直查）

```
    f1aa2e832d536593641cea44ea890fb8ce9558b9	refs/heads/debug/tmc-uart-rootcause-20261028
    56d67a08719964d57042d1101cf77a830713c58f	refs/heads/feature/m8-sg-adc-relative-protection
    1b3757eec550802a3973e66790af422d81d3d373	refs/heads/main
    6d3fbc559d45673d7550eb73dec5a4ade5bb34df	refs/heads/phase7/full-embedded-software
    1696c59f4b56bae5d503fb18888e379dfad6f76f	refs/heads/phase8/tmc-drive-config
    097935fec5a6f2467261ab1ad449ff0b1d398250	refs/tags/v0.7-phase7-software-board-validated
    e40a9b95ef709e6a80444f9f3949dfdd70ba96ba	refs/tags/v0.7-phase7-software-board-validated^{}
```

按全名复核远端真实 ref:,
```
refs/heads/main                                        1b3757eec550802a3973e66790af422d81d3d373
refs/heads/phase7/full-embedded-software               6d3fbc559d45673d7550eb73dec5a4ade5bb34df
refs/heads/phase8/tmc-drive-config                     1696c59f4b56bae5d503fb18888e379dfad6f76f
refs/heads/debug/tmc-uart-rootcause-20261028           f1aa2e832d536593641cea44ea890fb8ce9558b9
refs/tags/v0.7-phase7-software-board-validated         097935fec5a6f2467261ab1ad449ff0b1d398250
```

期望: main=1b3757e…  phase7/full=6d3fbc5…  phase8/tmc=1696c59…  debug 分支须仍是 f1aa2e8…(本轮只新建个人分支, 不覆盖它)  tag peels 到 e40a9b9…
```

本地 HEAD = 56d67a08719964d57042d1101cf77a830713c58f
本地 f921700(软件输入) = f9217005f191a601601d315add02c3e8c51f64ed

## 2. 逐 blob 复算（origin/feature/m8-sg-adc-relative-protection）

| 文件 | 远端 blob 字节 | 远端 blob SHA256 | 工作树字节 | 工作树 SHA256 | 一致口径 |
|---|---|---|---|---|---|
| `diag_relative.c` | 41722 | `4fb75e160e2b5cea…` | 41722 | `4fb75e160e2b5cea…` | 原始字节逐字节相同 |
| `diag_relative.h` | 10784 | `d4cdf2d3e36d7252…` | 10784 | `d4cdf2d3e36d7252…` | 原始字节逐字节相同 |
| `diagnosis.c` | 43452 | `fbf23c88ba1636f9…` | 43452 | `fbf23c88ba1636f9…` | 原始字节逐字节相同 |
| `diagnosis.h` | 5591 | `09bee9c1aaaedc23…` | 5591 | `09bee9c1aaaedc23…` | 原始字节逐字节相同 |
| `project_config.c` | 22029 | `0b4cb9561c8875ad…` | 22029 | `0b4cb9561c8875ad…` | 原始字节逐字节相同 |
| `project_config.h` | 4775 | `6ab66e7f96de99ba…` | 4775 | `6ab66e7f96de99ba…` | 原始字节逐字节相同 |
| `sensor_service.c` | 19633 | `9e1ee35211bfb6db…` | 19633 | `9e1ee35211bfb6db…` | 原始字节逐字节相同 |
| `sensor_service.h` | 4577 | `da95e7eded3393e8…` | 4577 | `da95e7eded3393e8…` | 原始字节逐字节相同 |
| `project.uvprojx` | 340110 | `971a27a99a924ccb…` | 348371 | `17a34efaca74b622…` | 远端=工作树去 CR(autocrlf 规范化) |
| `rel_main.c` | 72797 | `957e3e0927e2baed…` | 72797 | `957e3e0927e2baed…` | 原始字节逐字节相同 |
| `rel_pre_probe.c` | 12003 | `7baffd23197a26b4…` | 12003 | `7baffd23197a26b4…` | 原始字节逐字节相同 |
| `run_diag_relative_m8.sh` | 15439 | `c14fb4d338a5c6b9…` | 15439 | `c14fb4d338a5c6b9…` | 原始字节逐字节相同 |
| `run_m8_prefix_probe.sh` | 4928 | `30d6f81e40f11f41…` | 4928 | `30d6f81e40f11f41…` | 原始字节逐字节相同 |
| `rtthread.h` | 4528 | `92eefe628ef632ae…` | 4528 | `92eefe628ef632ae…` | 原始字节逐字节相同 |
| `rtdevice.h` | 1206 | `6378eac52f197a2d…` | 1206 | `6378eac52f197a2d…` | 原始字节逐字节相同 |
| `cc_main.c` | 30872 | `5a3cdb31e544c957…` | 31671 | `94c7a2dc0095b81d…` | 远端=工作树去 CR(autocrlf 规范化) |
| `run_current_cal_m7.sh` | 2761 | `7bf2fcd5e33a9088…` | 2805 | `3cf5932954908468…` | 远端=工作树去 CR(autocrlf 规范化) |
| `rtthread.h` | 772 | `9ed89c981faf98ad…` | 772 | `9ed89c981faf98ad…` | 原始字节逐字节相同 |
| `CP0_gap_table.md` | 8185 | `ea40696e569499ff…` | 8185 | `ea40696e569499ff…` | 原始字节逐字节相同 |
| `run_record.md` | 9595 | `6508f6347e17abc0…` | 9595 | `6508f6347e17abc0…` | 原始字节逐字节相同 |
| `field_candidate_package.md` | 7882 | `1f22d621cfcb0d56…` | 7882 | `1f22d621cfcb0d56…` | 原始字节逐字节相同 |
| `SHA256SUMS.txt` | 2121 | `8695301f7cbc75e6…` | 2121 | `8695301f7cbc75e6…` | 原始字节逐字节相同 |
| `m8_normal_out.txt` | 8525 | `52c49d0407061bd8…` | 8525 | `52c49d0407061bd8…` | 原始字节逐字节相同 |
| `m8_reverse_run_out.txt` | 8525 | `52c49d0407061bd8…` | 8525 | `52c49d0407061bd8…` | 原始字节逐字节相同 |
| `m8_reverse_RV1_out.txt` | 11053 | `376b79851a631671…` | 11053 | `376b79851a631671…` | 原始字节逐字节相同 |
| `m8_reverse_RV2_out.txt` | 8725 | `16ca9f20aa17d3d6…` | 8725 | `16ca9f20aa17d3d6…` | 原始字节逐字节相同 |
| `m8_reverse_RV3_out.txt` | 8725 | `97552b8ad1bd18fb…` | 8725 | `97552b8ad1bd18fb…` | 原始字节逐字节相同 |
| `prefix_probe_out.txt` | 355 | `198de90de74bf106…` | 355 | `198de90de74bf106…` | 原始字节逐字节相同 |
| `regression_cc_current_cal_out.txt` | 9437 | `63eac326a7c1d632…` | 9437 | `63eac326a7c1d632…` | 原始字节逐字节相同 |
| `stdout_crlf_normalisation.json` | 7299 | `c6f9128bbb9796b5…` | 7299 | `c6f9128bbb9796b5…` | 原始字节逐字节相同 |
| `build_manifest.json` | 3640 | `cb6a206a8f63694d…` | 3640 | `cb6a206a8f63694d…` | 原始字节逐字节相同 |
| `rt-thread.bin` | 281996 | `ff7883736702eb32…` | 281996 | `ff7883736702eb32…` | 原始字节逐字节相同 |
| `rt-thread.axf` | 1741680 | `0b47baee950fb0a6…` | 1741680 | `0b47baee950fb0a6…` | 原始字节逐字节相同 |
| `rt-thread-measure-a0.bin` | 292252 | `86013070ffa6f0f0…` | 292252 | `86013070ffa6f0f0…` | 原始字节逐字节相同 |
| `rt-thread-a0.axf` | 1772100 | `67d74b6652310f2d…` | 1772100 | `67d74b6652310f2d…` | 原始字节逐字节相同 |
| `rt-thread-motor-m1.bin` | 282100 | `3b226367f9f4a60d…` | 282100 | `3b226367f9f4a60d…` | 原始字节逐字节相同 |
| `rt-thread-motor-m1.axf` | 1742164 | `78bd23e70747773b…` | 1742164 | `78bd23e70747773b…` | 原始字节逐字节相同 |
| `SHA256SUMS.txt` | 598 | `f243f3e30f1f9064…` | 598 | `f243f3e30f1f9064…` | 原始字节逐字节相同 |
| `phase8-m8-sg-adc-relative-protection.json` | 13475 | `fa8425efc14611c5…` | 13475 | `fa8425efc14611c5…` | 原始字节逐字节相同 |

## 3. 汇总

- 复核文件数：39
- 远端 blob == 工作树原始字节（二进制/未转换文本）：36
- 远端 blob == 工作树去 CR 后的字节（core.autocrlf 文本）：3
- 不一致：**0**
- 结论：远端逐 blob 全部可复算，无缺失/无漂移

## 4. 镜像散列与 build_manifest 的交叉核对（远端字节）

```
rt-thread.bin                remote=ff7883736702eb32… manifest=ff7883736702eb32…  MATCH
rt-thread-measure-a0.bin     remote=86013070ffa6f0f0… manifest=86013070ffa6f0f0…  MATCH
rt-thread-motor-m1.bin       remote=3b226367f9f4a60d… manifest=3b226367f9f4a60d…  MATCH
rt-thread.axf                remote=0b47baee950fb0a6… manifest=0b47baee950fb0a6…  MATCH
rt-thread-a0.axf             remote=67d74b6652310f2d… manifest=67d74b6652310f2d…  MATCH
rt-thread-motor-m1.axf       remote=78bd23e70747773b… manifest=78bd23e70747773b…  MATCH
```

本文件自身在写就时未参与上表(它随后才提交)；它的可核身份是
交付头的 \`ls-remote\` 一致性与 §1 中远端分支指向。
