# M7-R2 计算器补正（同字节应用 + 本机复现）

日期：2026-10-29。任务：`phase8-motor-m7-current-calibration-r2`。
被审对象 `c78f7865292d387a0533c2f22c39e8c848bcd689`；本轮**不改 C、不重编 Keil、不烧录、不动电机**。

## 1. 身份与来源分开登记

| 角色 | 身份 |
|---|---|
| 交付/返修起点（被审） | `c78f7865292d387a0533c2f22c39e8c848bcd689` |
| 固件构建输入（不变，仍为镜像真实输入） | `5b564eb962e8518920f581111d7a460a2b1fce64` |
| 起点工具 blob（规范 LF） | 49672 B / `622ad049ee8f520ffc14247986a5192e143c58ef10e9185e823c646bdb528c63` |
| 候选工具 blob（规范 LF） | 54267 B / `5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad` |
| 合并补丁 | `c0d1d67abf6826db3bc73443065bf3c3ba930ba0af1ba42fdf1c51cd18e7f7f0` |
| 本轮计算器提交 | 见交接 JSON 的 `calculator_code_commit`（不自我引用） |

开工只读核对：`HEAD = 远端 = c78f786`，工作树干净，`main 1b3757ee…` /
`phase7/full-embedded-software 6d3fbc55…` / `phase8/tmc-drive-config 1696c59f…` /
tag 对象 `097935fe…` 解引用 `e40a9b95…` 与复审读回逐字相等；无 reset/rebase/merge/force。

## 2. 应用过程（含一条必须说明的行尾坑）

`git apply --check` 通过后**首次应用产出的字节不是候选**：

```text
patched tool bytes: 55420  cr=1153  sha256=c1f62e1e31f6f010…
candidate  bytes:   54267  cr=0     sha256=5c7a2340b4d350b1…
行内容逐行相等（unified diff 0 行），差异 = 1153 个 CR = 行数
```

即补丁内容正确，`git apply` 在写入时做了 CRLF 转换（本机 `core.autocrlf=true`）。
按任务书 §6.A.2 的要求改为**对规范 Git 字节处理、不改全局配置**：把两个目标的
HEAD blob 导出到仓外，用 `git -c core.autocrlf=false apply` 单命令覆盖后逐字节比对：

```text
tool  bytes=54267 cr=0 sha256=5c7a2340b4d350b1e8932798a57b85e8a6f4818501e469ad6dfd5c1bd29163ad
patched == packaged candidate (byte-for-byte): True
```

入库后暂存 blob 复算同为 `5c7a2340…`（见 `patch_application_log.txt`）。
**没有修改候选字节、没有放松任何判据、没有改 git 配置。**

## 3. 本机实际执行（全部 rc=0，除注明者）

| 项 | 命令 | 结果 | 证据 |
|---|---|---|---|
| 原自检 | `m7_calcalc.py --selftest` | `SUMMARY cases=54 pass=54 fail=0` | `calcalc_selftest_r2.txt` |
| 专项 21 项 | `python verify_calc_candidate.py <规范LF工具> <全新仓外目录>` | `SUMMARY 21 pass 21 fail 0` | `verify_calc_candidate_r2.txt` |
| 同输入修前/修后 | `probe_r3_remaining.py` 分别喂起点与候选工具 | 见 §4 | 两份 `probe_r3_same_inputs_*.txt` |
| 局部反向 | `run_m7_reverse.sh` 全六族 | `REV1 3/3 · REV2 1/1 · REV3 1/1 · REV4 2/2 · REV5 3/3 · REV6 2/2` → `PASS` | `reverse_r2_full_local.txt` |

原有 54 个 ID 一个没删、没放松；REV5 锚点随补丁更新（`bound =
math.nextafter(float(exact_bound),math.inf)`），所以改完工具后旧反向入口不会变成 INFRA-FAIL。
其余五族产品/夹具未改；本轮整套重跑的目的在于同时验证**被补丁改过的反向脚本本身**没坏。

## 4. 反例的修前 / 修后（同输入，逐字来自两份 probe 文件）

| 输入（全为 HOST_REVIEW_SYNTHETIC） | 起点工具 | 候选工具 |
|---|---|---|
| `CONTROL_FULL_REAL_FORMAT`（真实六行完整件） | rc=0 `DRAFT_NOT_APPROVED` + 草案 | **仍 rc=0 同一草案**（不过度收紧） |
| `HEADER_ONLY`（只剩统计首行） | rc=0 错误接受 | **rc=1，无命令** |
| `MISSING_DECISION_TAIL` | rc=0 错误接受 | **rc=1** |
| `NUL_IN_NEGATIVE_DECISION` | rc=0 错误接受 | **rc=1** |
| `CHANGED_VERSION`（原始件 ver=1..2） | rc=0 错误接受 | **rc=1** |
| `REUSED_FIT_AS_HOLDOUT_RENAMED`（复制改名当留出点） | rc=0 错误接受 | **rc=1** |
| `NONLINEAR_INTERVAL_CORNER` | 界 `9.614344956063261` ≤ 10 ⇒ usable=true | 界 **`10.317726165210626`** > 10 ⇒ `REJECT`，usable=false |

非线性反例的精确顶点差本机复算与复审一致：`corner_prediction 108.90979800000059`、
`exact_corner_difference 10.009798000000586`、参考限 `[0.48596, 1.11428, 1.1]`。

## 5. 候选工具的实质改动（按 §4/§5 合同）

- **来源件从"能找到就算"改为"正向要求完整真实格式"**：六行逐条匹配生产 `current_cal`
  的输出语法，含 `tick` 与 `n_req`；`fit=READY`/`reason=OK`、`changed=0`、版本稳定、
  `reset=0`；LF/CRLF 与已知 msh 回显可接受，原始件不改写；NUL、乱序、重复、
  数值后缀、声明与文件不符一律具名拒。旧正则认 `fit=YES` 而真实输出是 `fit=READY` ——
  自检正控的 `write_capture` 也一并改成生产真实格式（这是数据修正，不是放宽验证）。
- **独立性不再被 provenance 豁免**：合成豁免只豁免"不存在的文件不去打开"，
  不豁免"同一笔采样改名当留出点"。
- **误差界改成可论证的全区间最界**：恰好两个拟合点 + ≥1 个独立留出点；
  超过两个拟合点明确 `UNSUPPORTED_BOUND_MODEL`（不把未证明的多点回归区间说成已支持）。
  两点映射在分母为正且区间不重叠时对每个输入坐标是分式线性/线性函数，
  矩形输入域极值在顶点 ⇒ 枚举 32 个顶点、`Fraction` 有理数运算、`float` 导出向外取界；
  参考限与未覆盖支路限再绝对相加。局部敏感度保留但**明确不用于**最终保守界。
- 适用前提照旧披露：界限于声明的输入区间；重复性项仍是 1σ 估计，不是物理精度保证；
  不给保护阈值或全工况认证。

## 6. 与云端结果的差异（不掩盖、不择一）

| 项 | 云端 | 本机 |
|---|---|---|
| REV3 子 runner 退出码 | `rc=4`（INFRA-FAIL 类），总入口 rc=1，但 C22f 确实 FAIL；随后 focused `cal_static` 8 PASS/1 FAIL 确认具名检出 | `rc=1`，`hits=1/1`，focused 与全量均一致 |
| 六族整包 | 云端**不**称整包 PASS，原因未定 | 本机整包 PASS（来源等级=Agent 本机，不外推为跨平台绿灯） |
| Keil | 本轮未执行（也未需要） | 本轮未执行：`KEIL_BUILDS_EXECUTED=[]` |
| I/O、SD、V1/V2 | 云端本轮未复跑 | 本轮也未复跑（产品/夹具未改；上一轮结果与归档仍可查） |

这与既有纪律一致：本机全绿不等于跨平台正确，差异原样并列，不写成对方错了，也不据此说产品坏了。

## 7. 仍未闭合（不自我关闭）

- `>2` 拟合点的区间界模型仍**不支持**（显式拒绝），多点回归的最坏界没有证明。
- 共用会话文件的自动分段**没有实现**：本工具要求每条采样各自的原始响应文件。
- 旧 `cfg_take()` 用户（set/load/default/boot）仍吞取锁返回码 —— 复审明确不在本轮范围。
- P1 独立母线参考仍 `METHOD_NOT_READY`；合成系数（含本目录
  `example_result_HOST_SYNTHETIC.json` 的 `current_cal apply 1598291 671551 CONFIRM_MEASURED`）
  **禁止下发实物**。
