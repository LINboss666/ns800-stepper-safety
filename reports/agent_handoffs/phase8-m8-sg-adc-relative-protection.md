# 交接：NS800 M8 SG/ADC 相对异常检测与主动保护（离线轮）

- 日期：2026-10-31（UTC 时间戳见 JSON）
- 任务书：`phase8-m8-sg-adc-relative-protection`，基线 `f1aa2e832d536593641cea44ea890fb8ce9558b9`
- 工作分支：`feature/m8-sg-adc-relative-protection`（本轮新建的个人分支，未覆盖调试分支）
- 软件输入 commit：`f9217005f191a601601d315add02c3e8c51f64ed`
- 交付头（delivery head）：本轮最后一次提交（证据+交接），其父即 `f921700`；
  两个提交都在个人分支上，`main`/`phase7`/`phase8`/tag 未动。
- 设备动作：**无**。`hardware_actions = []`。

## 1. CP0 已登记的前一轮 GO（随本次实际工作一并登记，不另开纯登记轮）

复审结论：**接受 `f1aa2e8…`，P0 电压观察完成，无新增必须返修项**；采集器问题按
"修复后现场验证通过"收口，**不再为排除所有可能性重复串口对照**；主线转向
**P1：取得与 ADC 所测同一支路的独立电流参考，确定零点与增益**；该回执**不新增任何
设备动作授权**。本轮 M8 未修改任何保护阈值，也未使用 mA 作为证据（M8 全用 raw 码域）。

P1 前提进展（本轮只做了有界离线核对，未收口）：`meters.uni-trend.com.cn/content/1288.html`
确认为 UT39A+ 产品页（型号-精度-分辨率口径正确），但保险丝额定、插孔额定、压降电压、
量程持续时间在该页未公布；厂商手册与两份镜像手册均为图片扫描件 ⇒ 缺口 1 只是收窄，
未闭合；缺口 2（VM 支路的串联治具）与缺口 3（VM 焊盘↔U9 pin8 对应关系）仍是实物/现场项。

## 2. 本轮做了什么（细节在 `reports/reviews/phase8-m8-sg-adc-relative-protection/`）

| 文件 | 内容 |
|---|---|
| `CP0_gap_table.md` | 已有/需修改/现场待证 12 行功能表 + M8-PRE-1/2/3 复现计划 + 参数表 |
| `run_record.md` | 实现分层、**我自己写的用例抓到并已修掉的 12 条产品缺陷**、全部命令与 rc、结构尺寸、交付分级、遗留 |
| `field_candidate_package.md` | 具体现场候选包：绑定真实镜像散列、阶段 S0–S6、额度表、判据、结论天花板；状态 `NOT_AUTHORIZED/NOT_EXECUTED` |
| `build/` | 三目标 Rebuild All 日志 + uv4 返回码 |
| `host/` | M8 正常/反向、修前基线探针、CC 与 SD/S1/S2/M6/IO 回归归档（含 `stdout_crlf_normalisation.json`） |

新增产品模块 `applications/diag_relative.c/.h`；改动 `sensor_service.{c,h}`、
`diagnosis.{c,h}`、`project_config.{c,h}`、`project.uvprojx`（三目标各一条文件条目）。

## 3. 验证与身份（全部实测）

- M8 主机链 `bash tests/host/run_diag_relative_m8.sh` → rc=0，
  `SUMMARY|cases=27 pass=27 fail=0 pre_bad=0 executed=27`（归档 `host/m8_normal_out.txt`，
  规范化 sha256 前缀 `52c49d04…`）。
- 三项具名反向（同一 runner，`REVERSE=1`）→ RV1/RV2/RV3 全 `META-OK`，
  分别命中 `REL08`/`REL09`/`REL16`；RV1 附带 REL07/REL11 额外失败（如实登记，不改判定）。
- 修前观察 `bash tests/host/run_m8_prefix_probe.sh` → rc=0，3/3 在 `f1aa2e8` 的 **git blob**
  副本上复现（`diagnosis.c` blob `cb5d0a4a3e719f05845a8c0a7d180e534e6bc0bd`，33554 B）。
- 依赖回归全部真执行：CC 34/34、SD 两变体、S1 78 例、S2 19 组、M6-CLI 10、IO 4 变体，全 rc=0。
- Keil 三目标各一次 Rebuild All：`0 Error(s), 0 Warning(s)`，uv4 rc=0；只用 `-r`，**未用 `-f`**。
- 跨编译器结构尺寸一致：`project_config_t` 212（GCC）= `.bss.cfg` 212（ARMCLANG）；
  `sensor_frame_t` 72（基线 56）；`diag_rprof_t` 108；v2 镜像 104。

## 4. 分级与不宣称

`HOST_PROVEN`：判据/资格/身份/持续时间/先保护后打印/不刷屏/迁移与持久化/三项反向。
`BUILD_PROVEN`：三目标 0/0 与镜像身份。
`NOT_VERIFIED`：真机上 RELATIVE 的武装-覆盖-学习-提交-清空全流程、真 Flash 上的 v2 迁移、
任何 `REL_*` 严重触发、阈值物理合理性（仍是学习裕量+设计初值）、真实并发与时序抖动。
`NOT_AUTHORIZED/NOT_EXECUTED`：整个现场候选包。

## 5. 遗留给下一轮（含需要的授权）

1. 现场采集器脚本未写（要在获准执行的那一轮产出并跑 `-SelfTest`，绑定当轮镜像身份）。
2. BSP 三份阶段文档未同步；`run_record.md` §3 的 12 条里至少 3 条应进《调试记录》。
3. 机载侧缺一个 `diag_relative` 自检（现在只能靠 `diag_status`/`diag_profile show` 观察）。
4. 真机严重触发需要另一次显式加载实验授权（含人工切断手段）才可能推进。
5. P1 的三项物料/资料前提仍未闭合（见 §1）。

## 6. 远端逐 blob 回读（独立 fetch 后按 Git blob 复算）

`remote_readback.md`：按**远端真实 ref 全名**复核（`main`、`phase7/full-embedded-software`、
`phase8/tmc-drive-config`、`debug/tmc-uart-rootcause-20261028`、tag
`v0.7-phase7-software-board-validated`），并对 `origin/<本轮分支>` 逐 blob 复算 39 个文件：
判定口径、命中数与不一致数直接记在那份文件的 §3（本处不重复抄数，避免二次转录错误）；
六枚镜像的远端散列与 `build_manifest.json` 登记值逐条 MATCH 记录在其 §4。

证据目录的 `SHA256SUMS.txt` 明确**排除两个文件**：自身（清单不能哈希自己）与
`remote_readback.md`（它是在清单定稿之后生成的，把它纳入清单会制造自我引用）。
