# C-01 收口交付回执（窗口 C 线 → 主线 A）

状态：离线验证与整理交付。本轮构建 0、设备动作 0、未修改任何产品源码。
本目录在仓库外生成，未写入 A 的工作树；目录结构按 `reports/reviews/phase8-m8-parallel-protection-ready/` 组织，由 A 统一纳入其提交。

## 1. 基线与受测身份

| 项 | 值 |
|---|---|
| 验证时仓库 HEAD（基线） | `48204fe37de3da6f8caeeff467256562f11e58e2` |
| 受测 `field/plan.json` | SHA256 `2cfcf66cabacc47a623c0c11d92793757f5805a89c72485b2efac54fbdb5a309` / 67782 B / CR=0 |
| 受测 `field/plan_continuation_s2.json` | SHA256 `cfeea1deed50ad7b8ac1d1b44612fc2608485db5781bb84e98dd829a28e29ea6` / 62437 B / CR=0 |
| 受测 `field/m8r3_serial_chain.py`（执行器） | SHA256 `1bbf34e2df65d55bbd223e5014206f812ba8066a8db2edee3874fe75aa60aec6` / 123238 B / CR=0 |
| 本目录 `verify_c01.py` | SHA256 `ccc3db0a3cc1ff2903822c6d77987d2c32c97f45c529a442994079e2ac1711e2` / 6845 B / CR=0 |
| 本目录 `results_local.json` | SHA256 `ecf9466e0e3b097b849936360aff83fbfd1cf7749b73303ff8f03c66bf620fb0` / 11478 B / CR=452（Windows python 写出为 CRLF，哈希绑上述字节） |

**入库状态更正（与本回执起草前提不同，按实测记录）**：上表三份受测字节与仓库当前提交态逐字节相等——A 已在 `d4c3108 feat(m8-idle-r4): … learn 判据改 >=8 …` 中把 `must_min` 实现（两份计划 + 执行器）入库，`fe3d9f5` 为其后的构建输入/清单提交；本目录对应文件在工作树已无未提交改动。故"改动尚未提交"一句已不成立，正确表述是：**C-01 的修复实现由 A 提交，本交付只贡献独立验证与待办登记。** `d4c3108` 同时含 A 的 idle 栈与 motor 候选重建，属 A 的工作，不在本回执验证范围。

## 2. C-01 结论

C-01（`diag_profile learn` 要求 ADC/SG **各恰好 8**，会把正常学习的过量采样误拒）在当前主线已修复，采用的是 A 的实现：计划数据里 `must{adc_n:"8",sg_n:"8"}` → `must_min{adc_n:8,sg_n:8}`，执行器 `assert_step` 新增下限比较，非整数/缺组一律具名拒绝（`FIELD_VALUE_BELOW_MIN`），行正则保持 `\d+` 不变。

云端包 `NS800_C01_Minimal_Patch_and_Results_20261101.zip`（`C01_learn_minimum.patch`，SHA256 `f03481303080a9c8c29306caeb3c7410d96c33f5f97b71863f1a1596e29d6c0d`）**未应用**，理由两条，均已实测：

1. 补丁切的是基线提交态字节：其 pre-image blob `2ebb5dc`/`c8317e4` 与 `48204fe` 的两份计划逐字节一致；而其声明的 `executor_sha256=44e2a271…` 等于 `48204fe` 提交态执行器，即云端的 14 项验证跑在**没有 `must_min` 的旧执行器**上，未覆盖 A 现在的实现。
2. `git apply --check` 对两份计划均失败（`patch does not apply`），根因是工作树漂移而非行尾（实测 CR=0）。若强行在干净基线套上，会用 `must:{}`+正则承担下限语义，回退 A 的 `must_min` 与 `criterion_note`。两者接受集等价（≥8 收、<8 拒），但现网实现以 A 的为准。

## 3. 独立验证结果

用两份真实计划文件与真实 `assert_step` 驱动，每份 16 项、共 **32 项期望全部命中**（`results_local.json`：`all_met=true`），两份计划逐项结果一致。

健康与边界：`ADC68·SG8`、`ADC8·SG8`、`ADC8·SG68`、`ADC100·SG79` → `OK`。
保留的拒绝面：任一通道 7 → `FIELD_VALUE_BELOW_MIN`；重复成功行 → `FIELD_NOT_EXACTLY_ONE`；`learn FAILED timeout`、`not supported` → `BUSINESS_FAILED`；仅有 `zero OK` 无学习行 → `EXPECTATION_NOT_MET`（内容缺失先于值判据）；畸形行 `learn OK adc[1..2]n8` → `FIELD_NOT_EXACTLY_ONE`。

元负控（证明判据真在 `must_min` 上起作用）：

| 突变 | 输入 | 期望并实测 |
|---|---|---|
| 下限改为 9 | ADC8·SG8 | `FIELD_VALUE_BELOW_MIN` |
| 清空 `must_min` | ADC7·SG8 | `OK`（放行面确实由该门控制） |
| 指向不存在的组 | ADC68·SG8 | `FIELD_VALUE_MISMATCH` |
| 退回精确 8（`must`） | ADC68·SG8 | `FIELD_VALUE_MISMATCH` |

修前缺陷复现（真实提交字节，非重构）：`48204fe` 的两份计划对同一份 `ADC68·SG8` 健康响应均判 `FIELD_VALUE_MISMATCH`（`baseline_before_fix.defect_reproduced=true` ×2）。

验证边界：只覆盖学习步骤的业务响应判据层，未跑串口/物理学习，不宣称学习在现场已通过；`68` 只是既往离线样例，未写死为验收值。

夹具可复现性与如实说明：`verify_c01.py` 内 `FIELD`/`OUT` 为运行时绝对路径（`D:\ns800\...\field` 与本机 Temp 输出位），A 复跑需按自己的检出路径改这两行；本轮按交付要求未重跑、未改脚本，本目录内字节即原始运行件。本目录之外，唯一进入 A 工作树的是被 `.gitignore:26` 排除的 `__pycache__/m8r3_serial_chain.cpython-312.pyc`（导入执行器的副产物，可再生的字节码，非跟踪内容）；无任何跟踪文件被本窗口写入或改动。

## 4. 待办登记（本轮不动，交 A 处置）

1. **C-02（阻断级，现场开跑前必须处理）**：真实相对保护走 `EVT_MULTI_FAULT` → `FAULT_MULTI_SOURCE = 5`（`safety_state.h:46`、`safety_state.c:309`）→ 停后帧 `event=5`；而两份 R3 计划的取证门、`rows.json` 的 16 行停后夹具、`m8r3_field_selftest.py:1480` 的负控键全部钉在 `event=9`（= `FAULT_SOFT`，仅由 `EVT_SOFT_FAULT` 产生），且 `safety_trip` 在各场景 `forbidden_commands` 中。后果：一次**成功**的 SG/ADC/IMU 触发会判 `NO_FRAME_WITH{event:9}` → `FLASH_FRAMES_NOT_RECOVERED`。
2. `must_min` 无入库自检覆盖：`m8r3_field_selftest.py` 中检索不到 `adc_n`/`sg_n`/`BELOW_MIN`，本回执第 3 节的 32 项只是仓外临时夹具，未进回归。
3. 健康夹具停在边界值：`rows.json:449` 的学习行为 `…n8 … n8`，离线路径永不经过"ADC 过量采样"，建议补一条 `n68` 健康行（与 C-02 同属"夹具与产品不一致"一类）。

## 5. 机械前置与缺项（按最新口径原样保留，不阻塞本交付）

已知实物：T8 丝杆、标称 300 mm 完整导轨滑台，**电机尚未连接**，导程待确认。

约束：现场仅使用中间短行程；不得把 300 mm 当有效行程；不得把 T8 当导程；不新增回零/全程定位功能；不通过撞两端限位制造堵转。导程确认后，再结合实际细分与脉冲参数核算累计运动距离与起转时长。

仍缺：电机与滑台的连接方式；可从非旋转端操作的加载作用点及档位/行程；ADXL345（现知在扩展板上）的实际固定方式。
