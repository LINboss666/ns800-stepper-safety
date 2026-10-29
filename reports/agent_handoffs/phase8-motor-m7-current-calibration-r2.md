# NS800 M7-R2 交接（计算器补正 + P0 定稿）

日期：2026-10-29。任务：`phase8-motor-m7-current-calibration-r2`。
被审交付：`c78f7865292d387a0533c2f22c39e8c848bcd689`。
固件构建输入仍 `5b564eb962e8518920f581111d7a460a2b1fce64`（本轮未重编，六件镜像字节未变）。

## 0. 本轮范围与判定登记

独立复审对本轮给出了**分项**结论，按原样登记，不外推：

```text
R1a 诊断预约与静止事务   CLOSED
R1b 配置锁失败           CLOSED
R2  µA 导出              CLOSED
三份固件 / 六件 BIN-AXF   ACCEPTED FOR P0
R3  计算工具              PARTIAL / REWORK  ← 本轮做的即是这一项
P0 计划                   可独立定稿（本轮已定稿，不执行）
P1/P2、真实标定、保护验收  未放行
FIRMWARE_REVIEW_GO(scope=P0_OBSERVATION)
```

该 GO 只把 M7-R1 固件接受为**未来 P0 电压观察的载荷**，不含任何设备动作批准；
不登记为整个 M7 标定工作包通过；此前 M6 GO、M4 电机基本功能、M5 软件故障停止与
日志成果保留；示波器与 A0 继续暂缓。**本轮没有自授任何 GO。**

## 1. 做了什么（白名单内）

| 改动 | 位置 | 说明 |
|---|---|---|
| 计算器 v2.1 | `reports/reviews/phase8-motor-m7-current-calibration/m7_calcalc.py` | 同字节应用审查包补丁，工具 blob = `5c7a2340b4d350b1…63ad`（54267 B 规范 LF） |
| REV5 锚点 | `tests/host/run_m7_reverse.sh` | 随同一补丁更新（blob `a32be1db2a579bcd…`），保证旧反向入口不因找不到旧 bound 语句变 INFRA-FAIL |
| R2 证据目录 | `reports/reviews/phase8-motor-m7-current-calibration-r2/` | 补丁应用日志、54 与 21 项台账、修前/修后同输入探针、反向全文、P0 包与采集器、示例输入/输出 |

**未做**：改 C/.h、HAL、内核、`stop_priority`、时钟/引脚/宏默认、TMC/限流/SG、
Flash schema、Keil 工程；三目标未重编；没有把新的 Python 提交号填成固件构建输入
（`firmware_code_commit` 仍是 `5b564eb`）。旧的 M7/M7-R1 证据与三轮镜像原样保留。

## 2. 计算器补正（R3 剩余两项）与证据

- **A 完整来源判定**：生产真实六行语法逐条正向匹配（含 `tick`/`n_req`、`fit=READY`、
  `reason=OK`、`changed=0`、版本稳定、`reset=0`），NUL/乱序/重复/后缀/声明不符具名拒；
  共用会话文件的自动分段**未实现**，因此要求每条采样各自的原始响应文件；
  独立性不再被 `HOST_SYNTHETIC` 豁免（豁免只免"不打开虚构文件"）。
- **B 全区间最坏界**：恰好 2 拟合点 + ≥1 独立留出点，>2 拟合点显式
  `UNSUPPORTED_BOUND_MODEL`；两点映射在分母为正且区间不重叠时对每个坐标是分式线性/线性，
  枚举 32 个顶点、`Fraction` 精确运算、`float` 向外取界；参考限与未覆盖支路限再绝对相加。

同输入修前 → 修后（逐字在两份 probe 文件里）：

| 输入 | 起点工具 | 候选工具 |
|---|---|---|
| `HEADER_ONLY` / `MISSING_DECISION_TAIL` / `NUL_IN_NEGATIVE_DECISION` / `CHANGED_VERSION` / `REUSED_FIT_AS_HOLDOUT_RENAMED` | rc=0 usable=true 且输出 apply 草案（错误接受） | **rc=1，无命令** |
| `CONTROL_FULL_REAL_FORMAT`（完整真实格式） | rc=0 `DRAFT_NOT_APPROVED` | rc=0 同一草案（未过度收紧） |
| `NONLINEAR_INTERVAL_CORNER` | 界 9.614344956063261 ≤ 10 ⇒ usable | **界 10.317726165210626 > 10 ⇒ REJECT**（精确顶点差 10.009798000000586） |

本机实跑：原 54 项 `pass=54 fail=0`；专项 21 项 `pass=21 fail=0`；
反向六族在改过的工具+脚本上 `REV1 3/3 · REV2 1/1 · REV3 1/1 · REV4 2/2 · REV5 3/3 · REV6 2/2 → PASS`。
ID 一个未删、判据一条未放松。

## 3. 一条必须说明的过程事实

首轮 `git apply` 产出 **55420 B / 1153 个 CR**，不是候选的 54267 B —— 逐行内容完全相同，
差异只是行尾：本机 `core.autocrlf=true` 让写文件时加了 CR。按任务书要求改为对规范
Git 字节处理（仓外导出 HEAD blob + 单命令 `-c core.autocrlf=false`，不改任何配置），
之后逐字节等于候选，入库后暂存 blob 复算亦为 `5c7a2340…`。全过程记在
`patch_application_log.txt`。**没有改动候选字节，也没有冒充同一 SHA。**

## 4. 与云端结果的差异（并列，不择一）

REV3 子 runner：云端 `rc=4`（总入口 rc=1，C22f 确实 FAIL，focused `cal_static` 8 PASS/1 FAIL
确认具名检出，原因未定）；本机 `rc=1 hits=1/1`。云端据此**不**称六族整包 PASS，
本机整包 PASS 只按"Agent 本机"来源等级保留 —— 这与既有纪律一致：本机全绿不等于跨平台正确。
云端本轮未复跑的 I/O、SD、V1/V2 本机也未复跑（产品/夹具未改，上一轮结果与归档仍可查）。

## 5. P0 包（定稿、不执行）

`p0_package.md` + `p0_voltage_observation.py`（15503 B / `1e34589f…c44f`，
离线自检 11/11）。载荷 `e614a075…f7da` 266244 B，回读范围 `0x08000000` 起 `0x41004`，
一次显式复位、开串口先于复位、六条只读命令逐字白名单、一次 `sample 128` + 四个 DMM 读数、
结论上限 `VOLTAGE_OBSERVATION_ONLY`。传输参数全部取自真实成功过的 M5 motor 那一轮
（该轮回读逐字节相等且板上横幅与归档字节戳吻合）。本包**未**声称物理复位总量为 1：
下载器内部 halt/启动序列不可单独观测，记 `TOOL_INTERNAL_RESET_NOT_OBSERVABLE`。

## 6. 未闭合（不自我关闭）

- `>2` 拟合点的区间界模型不支持；共用会话文件不自动分段。
- 旧 `cfg_take()` 用户仍吞取锁返回码（复审明确不在本轮范围）。
- P1 `METHOD_NOT_READY`；P2 `NOT_AUTHORIZED`；真实 FIELD 数据尚未产生。
- REV3 云端 rc=4 的原因未定；完整栈高水位仍 `NOT_MEASURED`。
- 完整项目安全验收、独立母线参考、真实 DIAG/SG 端到端、物理电流与波形/时延均保持开放。

## 6A. 远端字节回读（工具提交 `01da9d6` 之后）

详见 `reports/reviews/phase8-motor-m7-current-calibration-r2/remote_readback_r2.md`。要点：

- 远端分支 tip 实测 `01da9d62c65bb482db7eaec2d5e6a62ae492cf86`；`main` / `phase7` / `phase8` /
  tag `v0.7-phase7-software-board-validated`（对象与解引用）四项冻结引用逐字相等。
- 21 个交付路径逐条读远端 blob 字节：计算器 = `5c7a2340…63ad` / 54267 B（CR 字节 0），
  与任务书候选逐字节相等；`run_m7_reverse.sh` = `a32be1db…96d4` / 10083 B；
  P0 采集器 = `1e34589f…c44f` / 15503 B。
- 六件 M7-R1 镜像的远端 blob 与 `build_manifest.json` 登记值全部相等；
  `c78f786 → 远端 tip` 在 `reports/firmware/` 下变更路径数 = 0，M5 归档 BIN 仍 259580 B / `01d3e5ab…`。
- **用远端字节重跑**（不是复述本地结果）：`--selftest` 54/54、`verify_calc_candidate.py` 21/21、
  同一输入探针五个缺陷源全 `rc=1` 且无命令、非线性角点界 `10.317726165210626` → `REJECT`。
- 本文件 §7 第一行的 `AWAITING_REMOTE_BYTE_REVIEW` 描述的是工具提交 `01da9d6` 当时的状态；
  回读本身随本证据提交入库，因此该行的后继状态是 `REMOTE_BYTE_READBACK_DONE / AWAITING_INDEPENDENT_REVIEW`。
  这不是对本轮五行测点契约的改写，只是登记顺序。

## 7. 回执

```text
M7_R2_CALCULATOR_PATCH_APPLIED / OFFLINE_VERIFIED / AWAITING_REMOTE_BYTE_REVIEW
M7_R1_FIRMWARE_ACCEPTED_FOR_P0 / SIX_IMAGE_BYTES_UNCHANGED
P0_PACKAGE_PREPARED_NOT_EXECUTED / P1_METHOD_NOT_READY / P2_NOT_AUTHORIZED
HARDWARE_ACTIONS=[] / KEIL_BUILDS_EXECUTED=[] / NEW_FLASH_ACTIONS=0
M5_BOARD_IMAGE_UNCHANGED / NO_MOTOR_ACTIONS / NO_HARDWARE_GO_SELF_CLAIMED
```

下一轮：等复审对候选工具字节与 P0 整包的意见；P0 需要用户对该整包**一次**批准才执行。
本任务不含现场批准。

## 8. P0-R1 复审指针（只追加，上文不改）

独立复审对象 `208bf102e6b3bce7159eec672b83a0d6b77f8811` 的结论（2026-10-30）：

- 计算器：**`M7_R2_CALCULATOR_REVIEW_GO`**，R3 两项（截断来源冒充完整证据、局部导数冒充全区间最坏界）
  在该版字节上关闭；本文件 §2/§3 的登记照原文有效，不再返修 `m7_calcalc.py`。
- 本文件 §5 的 P0 采集器 `1e34589f…c44f`：**`P0_COLLECTOR_REWORK_REQUIRED`**，不能照该版直接上板。
  六项缺陷（回显当响应、无业务门、告警停得太晚、启动告警漏判、读异常丢原始件、忽略 write 返回值）
  已在 Windows 交付机用包内探针原样复现，与修前基线记录逐字节相符。
- 本文件 §5 的 P0 计划（含旧 BIN 时间戳偏移 259925/259916、单次 show、裸 `pyocd reset`）已被
  `reports/reviews/phase8-motor-m7-p0-collector-r1/plan_diff_notes.md` 记录的修正版取代；
  本文的历史状态与首错仍有效，未被改写。
- 后续：`reports/agent_handoffs/phase8-motor-m7-p0-collector-r1.md` / `.json`。
  P0 仍**未执行**，等用户对该整包一次批准；本文件不承担任何现场批准。
