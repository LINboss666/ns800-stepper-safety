# A 签收 R7F3；R7F4 CONFIGURED 固定无运动续测

日期：2026-11-04。复审提交：0a75e318f67f75beae7e42eade7d0c4a5a9dddea。

## R7F3 结论

本轮启动与指定短打印验收通过，可以收口。A 独立读取了归档原始字节：

- 现场 readback.bin 与候选 BIN 全部 292708 字节相同，SHA256 为 502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a。
- 启动 IOIN 正常、自检 PASS、READY/fault=0；六条真实查询均通过上一份固定计划的原判据。
- 启动流与六条响应全部无裸 NUL、严格 UTF-8 完整。ENN 两条打印完整。
- safety_status / motor_status / safety_irq_status 的预测行分别 7/7、25/25、9/9 与真实响应逐行相同。

这关闭了本轮指定的启动/短打印问题；不代表全部 35 条长格式串已经修复，也不代表 SG/ADC/IMU、带载最坏栈或正常 disarm 已经过现场验证。

## 下一步决定

先做一次 CONFIGURED 验收，再进入学习和目标通道触发。当前驱动仍为 UNCONFIGURED、保护门关闭；配置下发是后续运动/学习的实际前置条件。

沿用已经逐字节证在板的 b502，自由运行状态接续。无需重新编译、下载、回读或复位。也无需为了准备本包再提交一轮准备件。

## 固定动作与参数

批准身份建议：M8R7F4-b502-configured-20261104。

| 动作 | 上限 |
|---|---:|
| COM5 开口，一个连续会话 | 1 |
| tmc_config_apply CONFIRM | 1 |
| 下载、回读、复位、arm/start、运动、学习、零点、提交、保存、清日志、开关 IRQ、极性确认、重试、额外动作 | 0 |

一次 apply 是一次命令事务，固件内部按既有实现写 GCONF、CHOPCONF、IHOLD_IRUN、TPOWERDOWN，并逐次确认 IFCNT；不把内部四个寄存器写误记成四次 apply。

下发的静态计划参数来自本镜像中的 tmc_cfg_planned：

| 参数 | 值 |
|---|---:|
| irun | 20 |
| ihold | 8 |
| ihold_delay | 4 |
| tpowerdown | 8 |
| vsense | 1 |
| mstep_reg_select / mres | 0 / 0 |
| IHOLD_IRUN | 0x00041408 |
| TPOWERDOWN | 0x00000008 |

GCONF、CHOPCONF 按固件逻辑保留不属于本次修改的位。配置会改变驱动电流档位，但本场始终要求未武装、频率为零、MCU 请求 LOW、焊盘 LOW、最终 ENN 在禁能侧。电流仍是理论值，本场不测量电流或转矩。

## 同一会话顺序

1. system_status：必须 READY/fault=0，TMC OK/UNCONFIGURED，runtime write gate=0，当前请求/焊盘/ENN 禁能一致。
2. motor_status：必须 IDLE、armed=0、cur_hz=tgt_hz=0、health=OK，通过现有 AT_REST 当前禁能合同。只放行已知整行 arm_gate_mask=0x18 REFUSED 状态披露。
3. tmc_config_apply CONFIRM 一次：必须取得八组完整短行，计划参数必须为上述值；裸 NUL、非法 UTF-8、拒绝、失败、缺行或重复字段均按原执行器停止。
4. system_status：必须仍 READY/fault=0、禁能一致、TMC OK/CONFIGURED。
5. motor_status：必须仍 IDLE、armed=0、频率为零且当前禁能合同成立；gate5=CONFIGURED，arm_gate_mask 应从 0x18 降到 0x8，保护未就绪这一个拒绝仍保留。
6. safety_status：再次取得当前禁能反馈和完整短打印。

前置检查不满足时不得发 apply；只消费已尝试的开口额度。apply 已发送后无论成功、失败或响应不完整，都不再次下发。失败即停、保留原始字节、正常关闭端口，不为补采另开口。

本场没有启动/复位测试，因此不沿用启动窗 75 秒。会话硬截止 90 秒。tools 中保留的下载/回读/复位参数仅为既有通用校验器要求的元数据：没有相应阶段，三项额度全部 0。

## A 已做的必要离线验证

- 新计划通过原执行器 S0 绑定，仍使用执行器 33d3f0af…；产品 C 和在板镜像未改。
- 原执行器上 11 项完整 stub/sim 调度回放完成：一个成功场景；十个反例涵盖前置故障、已配置、反馈矛盾、已武装、apply 拒绝/缺尾/参数错/NUL、写后未配置或请求变 HIGH。
- 四种前置失败均证明 tmc_config_apply 消耗为 0；进入 apply 的反例仅消耗一次，且不重试。
- CONFIGURED 八条源格式按最宽数字展开逐条不超过 120 B，不把夹具中的示例时延、寄存器值、TX 字节数当作真实观察或固定断言。
- 通用检查只有既定零运动预算项为 FAIL，按本固定零 arm/start 包登记 N/A；无启动的续测不申报启动观测窗，该项原工具返回 NA。执行器与判据函数未改。

这些回放使用实际上一场响应与明确标注的 source_shape 预测，只证明执行控制和判据，不算本轮硬件结果。

## 批准后的执行

本包当前硬件批准数为 0。操作者明确批准本包后，将两份 field JSON 按包内相对路径放入现有 0a75e31 仓库，先按 IDENTITY.json 核对新计划、原执行器和 b502 身份，再用既有批准机制开一本新的持久账，直接 dispatch 一次。

真实执行必须显式包含 --dispatch --transport real --backend real --profile response_driven，以及 --plan 指向本包固定计划、对应 --approval-id / --approval-root 和本次实际批准令牌。不要再漏传 response_driven。本场不调用 DAP 工具，亦不需要为了本续测读取 UID 文件。

现有 VM 供电保持、装配/接线确认沿用，执行前由现场人员核对当前状态。已批准同一范围后无需重复向操作者确认，也无需先提交一轮准备件。包内计划或动作范围若改变，则不再是这一次固定批准对象。

## 验收边界与 35 条长打印

本场要证明：一次真实配置成功，八条 CONFIGURED 输出完整，写后仍为禁能状态。驱动 CONFIGURED 与 protect_ready=NO 可以同时成立；0x8 拒绝保留是预期结果，不得为变成 ALL-PASS 而开 IRQ、确认极性或修改保护门。

通过后再按具体动作范围安排极性/IRQ 就绪、短动作、SG/ADC 学习和目标通道触发。本轮不授权这些动作。剩余 35 条长格式串按下一阶段实际执行和故障收口路径检查，不先做一次全仓改造来推迟本次限定验收。
