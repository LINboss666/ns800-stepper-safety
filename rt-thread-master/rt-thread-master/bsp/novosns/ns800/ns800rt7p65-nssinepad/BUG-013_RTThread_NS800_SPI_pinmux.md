# BUG-013：RT-Thread NS800 BSP SPI 外设引脚复用配置错误

```
STATUS:                    CONFIRMED / DEFERRED FOR UPSTREAM
PROJECT LOCAL FIX:         YES（仅 SPI3 表项，已真机验证）
SPI3 HARDWARE VERIFIED:    YES
UPSTREAM ISSUE:            NOT SUBMITTED
UPSTREAM PR:               NOT SUBMITTED
归档日期:                   2026-09-13
记录人:                     编码 Agent（证据均来自本项目实测与官方 mux 表核验）
```

## 1. 定性

**RT-Thread NS800 BSP peripheral pinmux configuration bug**
（RT-Thread NS800 BSP SPI 外设引脚复用配置错误）

明确排除：
- 不是 PCB 设计 Bug（NSSinePad V1.2 排针与网络正常，杜邦线验证通过）
- 不是 NS800 芯片硅 Bug（同一 mux 表下修正配置后 SPI3 真机通信成功）
- 不是 RT-Thread 内核 SPI 框架 Bug（rt_spi 框架工作正常，Flash 于 spi1 真机验证）

问题位置：
`bsp/novosns/ns800/libraries/HAL_Drivers/drivers/drv_spi.c`（`spi_config[]` 表）

pinmux 真值来源（权威依据）：
`rt-thread-packages/novosns-series-latest/NS800RT7XXX/StdDriver/Inc/ti/NS800RT7xxx_TI_gpio.h`
（Novosense 官方 DevKit 包内文件，经 RT-Thread pkgs 机制下载）

NSSinePad 排针位置：结合开发板 V1.2 原理图 + 用户实物丝印核对确认。

## 2. 静态审计结论（BSP 表 vs 官方 mux 表）

| 控制器 | BSP `spi_config[]` 表项 | 官方 mux 表核验 | 结论 |
|---|---|---|---|
| SPI1 | PA16/17/18 @ ALT1 | GPIO_16/17/18 ALT1 = SPI1_SIMO/SOMI/CLK | **正确**（本项目 SPI Flash 真机验证正常：JEDEC EF 40 17、读写 PASS） |
| SPI2 | PB0/1/2 @ ALT9 | GPIO_218(PB0) ALT9=`EMIF1_OEN`；GPIO_33(PB1)、GPIO_34(PB2) 无 ALT9 定义 | **错误** |
| SPI3 | PC0/1/2 @ ALT7 | GPIO_64(PC0)、GPIO_65(PC1) 无 ALT7 定义；GPIO_66(PC2) ALT7=`EMIF1_A12` | **错误** |
| SPI4 | PC4/5/6 @ ALT7 | GPIO_68(PC4) 无 ALT7 定义；GPIO_69(PC5) 的 SPI 复用为 `SPI3_SIMO@ALT15`；GPIO_70(PC6) ALT7=`UART4_TX` | **错误** |

定性描述（谨慎表述）：当前 BSP 所配置 GPIO mux 与目标 SPI 控制器功能不匹配，因此这些 GPIO 不能按照当前 BSP 配置正常承担相应 SPI 的 SCK/MOSI/MISO 功能。使能 `BSP_USING_SPI2/3/4` 后编译与运行均无报错，属静默失效。

## 3. 旧调查结论修正

**OLD CONCLUSION INVALID**

早期会话（2026-09-12）曾得出："GPIO_64/65/66 @ ALT15 属于 SPI2 控制器，但该组缺 SIMO，组不齐"。

正确结论（经 mux 表逐脚核验）：

```
GPIO_63 ALT15 = SPI2_SIMO   (GPIOB.31, NSSinePad J3-22)
GPIO_64 ALT15 = SPI2_SOMI   (PC.0,    NSSinePad J3-21)
GPIO_65 ALT15 = SPI2_CLK    (PC.1,    NSSinePad J3-20)
GPIO_66 ALT15 = SPI2_NCS    (PC.2,    NSSinePad J3-19)
```

**GPIO_63~66 @ ALT15 是完整的 SPI2 复用组（四信号齐全）**。旧结论错在只核对了
GPIO_64/65/66 三个脚、未查 GPIO_63——PIN 组数查漏导致误判"缺 SIMO"。

## 4. mux 表中的正确完整信号组（STATICALLY VERIFIED AGAINST MUX TABLE）

以下各组均经官方 mux 表逐脚核验，信号齐全：

| 控制器 | SIMO(MOSI) | SOMI(MISO) | CLK | NCS | ALT |
|---|---|---|---|---|---|
| SPI2（组1） | GPIO_60 | GPIO_61 | GPIO_58 | GPIO_59 | ALT6 |
| SPI2（组2） | GPIO_63 | GPIO_64 | GPIO_65 | GPIO_66 | ALT15 |
| SPI3（组1） | GPIO_50 | GPIO_51 | GPIO_52 | GPIO_53 | ALT6 |
| SPI4（组1） | GPIO_91 | GPIO_92 | GPIO_90 | GPIO_89 | ALT15 |

另注：GPIO_20/21/22/23 @ ALT14 亦为 SPI3 复用组，但与本项目冻结引脚
（IMU_CS=PA.20、IMU_INT1=PA.21、FLASH_CS=PF.12）物理重叠，本项目不可用
（BSP 层面仍是合法复用组，不代表芯片缺陷）。

## 5. SPI3 project-local fix（PROJECT-LOCAL WORKAROUND / VALIDATED FIX）

本项目为正常使用 SPI3，已将 `drv_spi.c` SPI3 表项修改为：

```
SPI3_SIMO = GPIO_50 / PB18 / NSSinePad J3-36 / ALT6
SPI3_SOMI = GPIO_51 / PB19 / NSSinePad J3-35 / ALT6
SPI3_CLK  = GPIO_52 / PB20 / NSSinePad J3-34 / ALT6
```

- 该修改**仅存在于本项目仓库**（commit 8f17537）
- 含义：当前比赛项目为使用 SPI3 采用的本地修复
- **不等于已向 RT-Thread 上游提交修复**（见文首 UPSTREAM 状态）
- NSSinePad 排针位置（J3-34/35/36）由用户实物丝印确认

## 6. SPI3 真机验证证据（HARDWARE VERIFIED）

验证环境：NS800RT7P65D + NSSinePad V1.2 + ADXL345 模块（GY-291 类），杜邦线连接。

最终 IMU 引脚映射（全部真机验证）：

| 信号 | GPIO | 引脚 | 排针 | 备注 |
|---|---|---|---|---|
| SPI3_CLK | GPIO_52 | PB20 | J3-34 | 本地修复引脚 |
| SPI3_SIMO(MOSI) | GPIO_50 | PB18 | J3-36 | 本地修复引脚 |
| SPI3_SOMI(MISO) | GPIO_51 | PB19 | J3-35 | 本地修复引脚 |
| CS（软件） | GPIO_20 | PA.20 | J2-14 | safety_gpio 上电常高 |
| INT1 | GPIO_21 | PA.21 | J2-15 | 已接，**未验证中断功能** |

实测结果（RT-Thread msh，COM5 自动化采集）：

```
msh > imu_probe
[IMU] bus=spi3 dev=adxl345 cs=PA.20 mode=3 hz=1000000
[IMU] DEVID read#1=0xE5 read#2=0xE5 (expect 0xE5 twice)
[IMU] SPI3/ADXL345 PROBE OK

msh > imu_raw
[IMU] raw X=16 Y=81 Z=239  (62, 315, 932) mg @2g
```

- ADXL345 DEVID = 0xE5，双次读取一致 → SPI 读路径 PASS
- 寄存器写入（BW_RATE/DATA_FORMAT/POWER_CTL）+ 读回生效 → SPI 写路径 PASS
- 加速度原始数据有效：静置合成幅值 ≈992mg ≈ 1g（模块 Z 轴朝上），敲击数据变化
- 使能测量后首帧未就绪会读全 0，驱动已加 10ms 延时修复
- SPI1 Flash 回归（JEDEC EF 40 17）PASS → SPI1/SPI3 互不干扰

证据级别说明：
- DEVID/XYZ/Flash 回归 = **HARDWARE VERIFIED**（真机功能通信）
- 示波器/逻辑分析仪波形证据 = **无**（用户无仪器）；本记录以器件级功能通信
  （DEVID 匹配 + 物理自洽的加速度数据）作为验证依据
- INT1 中断功能 = **未验证**（TODO，接线但未写中断代码）

对应 commit：IMU 功能 `8f17537`（迁移+修复）→ `7aa413a`（真机验证+首帧修复），均已 push。

## 7. SPI2 / SPI4 当前状态（只做静态记录，本项目未修改未测试）

| 控制器 | BSP 现状 | 候选合法 mux（本仓库未改） | 静态核验 | 硬件验证 |
|---|---|---|---|---|
| SPI2 | 错误（PB0/1/2@ALT9） | 组1: GPIO_58/59/60/61 @ ALT6；组2: GPIO_63/64/65/66 @ ALT15 | STATICALLY VERIFIED AGAINST MUX TABLE | **TODO** |
| SPI3 | **已修**（GPIO_50/51/52@ALT6） | 同上（本仓库已应用） | STATICALLY VERIFIED AGAINST MUX TABLE | **PASS** |
| SPI4 | 错误（PC4/5/6@ALT7） | GPIO_89(NCS)/90(CLK)/91(SIMO)/92(SOMI) @ ALT15 | STATICALLY VERIFIED AGAINST MUX TABLE | **TODO** |

注意：GPIO_31（SPI4 SOMI 候选）= PF.21，在本开发板上即 BUG-009 的缺陷引脚——
这是板级/引脚个体问题，不影响 SPI4 mux 组在芯片层面的正确性。

## 8. 上游动作：全部暂缓（DEFERRED FOR UPSTREAM）

当前比赛项目未完成，以下动作全部等待用户明确指令"开始处理 RT-Thread NS800 SPI BSP Bug"后进行：

1. 获取 RT-Thread 最新 master
2. 确认 Bug 在最新 master 仍存在
3. Fork RT-Thread 官方仓库
4. 从最新 master 建独立修复 branch
5. 重新生成最小 diff（不得直接使用本项目混合 commit）
6. 按 RT-Thread coding style 整理代码
7. 单独编译
8. 真机复测
9. 整理 Issue
10. 用户确认后发 Issue
11. 再决定是否提交 PR

## 9. 关联索引

- 本项目 Bug 档案总表：`调试记录.md`（BUG-012 为本 Bug 的发现过程记录）
- IMU 实现：`applications/adxl345.c`（rt_spi 标准驱动，Mode 3 @ 1MHz）
- 项目仓库：LINboss666/ns800-stepper-safety（IMU 最终版本 commit `7aa413a`，已 push）
