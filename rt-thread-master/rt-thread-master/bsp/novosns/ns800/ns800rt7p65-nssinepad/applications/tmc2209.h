/*
 * tmc2209.h - TMC2209 UART 协议层正式 API (Phase 7-A)
 *
 * 硬件映射(冻结, 勿动): uart2 / PB6=TX / PB7=RX / 115200 8N1 /
 * TX 经 1k 与 RX 汇合到单线 PDN_UART / slave address 0。
 *
 * 底层协议算法(CRC8-ATM LSB-first、帧格式、回声过滤、应答头搜索)已经真机验证
 * (READ FOUND + WRITE IFCNT+1, 4 帧应答 CRC 独立核算一致), 本 API 只做封装,
 * 禁止改动协议实现细节。
 *
 * 约定(见 app_health.h):
 *   - 读函数非 RT_EOK 时输出参数无效
 *   - WRITE 在 TMC2209 上无应答; write_register_confirmed() 用 IFCNT 自增验证
 *     写入被芯片接受(禁止用 OTP 做测试)
 *   - health: OK=链路通且最近一次传输成功; DEGRADED=链路通但最近一次失败;
 *     FAILED=uart2 打不开; UNINIT=尚未初始化
 */
#ifndef TMC2209_H
#define TMC2209_H

#include <rtthread.h>
#include "app_health.h"

#define TMC_ADDR_DEFAULT 0    /* 单芯片网络, 默认 slave 地址 0 */

/* 寄存器地址(常用) */
#define TMC_REG_GCONF     0x00
#define TMC_REG_GSTAT     0x01
#define TMC_REG_IFCNT     0x02
#define TMC_REG_IOIN      0x06
#define TMC_REG_IHOLD_IRUN 0x10
#define TMC_REG_SG_RESULT 0x41

/* 幂等初始化: 打开 uart2(115200 8N1)。RT_EOK = 链路层就绪。
 * 注意: 链路就绪 ≠ 芯片在位, 在位验证用 tmc2209_write_register_confirmed()
 * 或 MSH 命令 tmc_uart_probe / tmc_scan。 */
rt_err_t tmc2209_init(void);

/* 读寄存器。addr 一般为 TMC_ADDR_DEFAULT。RT_EOK 时 *value 有效。 */
rt_err_t tmc2209_read_register(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);

/* 写寄存器 + 到达确认: 发送 WRITE 帧后读 IFCNT 前后各一次,
 * 验证 (after == before+1) mod 256 —— TMC2209 对 WRITE 无应答,
 * IFCNT 自增是写入被接受的唯一凭证。确认失败返回错误。 */
rt_err_t tmc2209_write_register_confirmed(rt_uint8_t addr, rt_uint8_t reg,
                                          rt_uint32_t value);

/* 读 StallGuard 结果(0x41)。电机静止时通常接近 0; 驱动使能转动后有意义。 */
rt_err_t tmc2209_read_sg_result(rt_uint16_t *sg_result);

/* 读全局状态 GSTAT(0x01): bit0 reset / bit1 driver_error / bit2 sg2。 */
rt_err_t tmc2209_read_status(rt_uint8_t *gstat);

/* 子系统健康(UNINIT/OK/DEGRADED/FAILED) */
subsys_health_t tmc2209_get_health(void);

/* ================= Phase 8-F2: 驱动参数配置(软件准备, 默认不启用) =================
 *
 * 本轮只做"能让配置写下去、能证明写下去、写不成就不许使能"的软件能力。
 * 真实写入被编译期硬门禁 TMC_DRIVE_CONFIG_HW_VALIDATED 挡住:
 * 当前扩展板 U5 与门异常 / 急停非门异常 / TMC_ENN 实测约 0V / H2 限位未接,
 * 安全链未验收 → 该门禁必须保持 RT_FALSE, 直到硬件验证完成并经单独提交翻转。
 *
 * apply 只做"写寄存器"这一件事: 不碰 ENN / MCU_DRV_ENABLE, 不写 VACTUAL,
 * 不产生 STEP, 不会让电机转动。 */

/* 待写寄存器补全 */
#define TMC_REG_CHOPCONF     0x6C
#define TMC_REG_TPOWERDOWN   0x11    /* ⚠ F2 曾误写为 0x3C(未经核实的猜测):
                                      * 芯片对任意地址的 WRITE 都会让 IFCNT 自增,
                                      * 所以写错地址不会被到达确认发现。
                                      * 现按官方确认表定为 0x11, 并由
                                      * tmc_cfg_selftest + 编译期断言钉住。 */

/* GCONF 位(Rev.1.09 §5.1: bit0 复位默认 1, bit8 复位默认 1) */
#define TMC_GCONF_I_SCALE_ANALOG    (1u << 0)   /* 1 = 用 VREF 引脚电压作电流基准 */
#define TMC_GCONF_INTERNAL_RSENSE   (1u << 1)   /* 0 = 用外部采样电阻 */
#define TMC_GCONF_EN_SPREADCYCLE    (1u << 2)
#define TMC_GCONF_PDN_DISABLE       (1u << 6)   /* 用 UART 时必须置 1(手册原文) */
#define TMC_GCONF_MSTEP_REG_SELECT  (1u << 7)   /* 0 = 微步由 MS1/MS2 引脚决定 */
#define TMC_GCONF_MULTISTEP_FILT    (1u << 8)

/* CHOPCONF 位 —— ⚠ vsense 的 bit17/bit18 归属本轮无法从 Rev1.09 文本抽取核实
 * (该寄存器表在 PDF 中为图形)。当前实测 CHOPCONF=0x15010053 时 bit17 与 bit18
 * 同为 0, 故两种假设都读作 vsense=0(V_FS=325mV, 与手册"Default is 325mV"自洽),
 * 无法自证。开启写入门禁之前必须由人工对照手册 §5.5 复核本行。 */
#define TMC_CHOPCONF_TOFF_MASK      0x0000000Fu
#define TMC_CHOPCONF_HSTRT_SHIFT    4
#define TMC_CHOPCONF_HEND_SHIFT     7
#define TMC_CHOPCONF_TBL_SHIFT      15
#define TMC_CHOPCONF_VSENSE         (1u << 17)
#define TMC_CHOPCONF_MRES_SHIFT     24
#define TMC_CHOPCONF_MRES_MASK      0x0Fu

/* IHOLD_IRUN(0x30) 字段布局 —— ⚠ 同样属"未经本轮核实"的假设(按 IHOLD[4:0]、
 * IRUN[12:8]、IHOLDDELAY[19:16], GTCFG/GTCTG 保持 0)。该寄存器为只写:
 * 不得要求读回, 到达凭证只有 IFCNT+1。偏移错误会导致电流档位设错而芯片仍应答,
 * 因此 tmc_calc_selftest 的打包/解包断言 + 人工核对是开启门禁的前置条件。 */
#define TMC_IHOLD_MASK              0x0000001Fu
#define TMC_IRUN_SHIFT              8
#define TMC_IRUN_MASK               0x00001F00u
#define TMC_IHOLDDELAY_SHIFT        16
#define TMC_IHOLDDELAY_MASK         0x000F0000u

/* 理论电流换算用的物理量(全部为整数定点, 避免 rt_kprintf 不支持浮点) */
#define TMC_SENSE_VFS_325_MV        325u    /* vsense=0: 低灵敏度/高采样电压 */
#define TMC_SENSE_VFS_180_MV        180u    /* vsense=1: 高灵敏度/低采样电压 */
#define TMC_SENSE_RDS_MOHM          20u     /* 手册算例 0R15+180mV=1.1A peak 所内蕴 */
#define TMC_VREF_FULL_MV            2500u   /* >=2.5V 等价内部基准(手册 §9.1) */

typedef enum
{
    TMC_DRV_UNCONFIGURED = 0,   /* 初始/未下发: 芯片为复位默认 */
    TMC_DRV_CONFIGURING,        /* 正在逐项写入 */
    TMC_DRV_CONFIGURED,         /* 全部写入均 IFCNT 确认(+ 可读寄存器读回一致) */
    TMC_DRV_FAILED              /* 任一步失败: 序列已中止, 禁止使能 */
} tmc_drv_cfg_state_t;

/* 待下发的驱动参数(理论值, 不代表已实测)。 */
typedef struct
{
    rt_uint8_t  irun;           /* 0..31 -> CS=(irun+1)/32 */
    rt_uint8_t  ihold;          /* 0..31 */
    rt_uint8_t  ihold_delay;    /* 0..15, 0=instant power down */
    rt_uint8_t  tpowerdown;     /* 0..255, >=2 才允许 PWM_OFS_AUTO 自动整定 */
    rt_uint8_t  vsense;         /* 0/1 */
    rt_uint8_t  mstep_reg_select; /* 1 = 用 MRES 设微步; 0 = 由 MS1/MS2 引脚决定 */
    rt_uint8_t  mres;           /* 仅 mstep_reg_select=1 时生效 */
} tmc_drv_config_t;

const char *tmc_drv_cfg_state_name(tmc_drv_cfg_state_t s);
tmc_drv_cfg_state_t tmc2209_get_config_state(void);

/* 取当前计划参数(只读)。 */
void tmc2209_get_planned_config(tmc_drv_config_t *out);

/* 纯计算(无副作用, 可离线单测):
 *   tmc_cs_milli()      : CS*1000 = (field+1)*1000/32
 *   tmc_current_peak_ma(): 该配置下的线圈峰值电流 mA(整数, 四舍五入)
 *   tmc_current_rms_ma() : 同上 RMS mA = peak/√2
 * rtotal_mohm = rsense_mohm + TMC_SENSE_RDS_MOHM; VREF 分档上限 2500 mV。 */
rt_uint32_t tmc_cs_milli(rt_uint8_t field);
rt_uint32_t tmc_current_peak_ma(rt_uint32_t vfs_mv, rt_uint32_t rsense_mohm,
                                rt_uint32_t vref_mv, rt_uint8_t irun);
rt_uint32_t tmc_current_rms_ma(rt_uint32_t vfs_mv, rt_uint32_t rsense_mohm,
                               rt_uint32_t vref_mv, rt_uint8_t irun);
rt_uint32_t tmc_ihold_irun_pack(const tmc_drv_config_t *c);

/* 过流方向守卫: vsense 1->0 抬高满量程(180mV→325mV)。IHOLD_IRUN 只写不可读,
 * 无法证明配套下调了 IRUN, 故该方向一律拒绝(RT_FALSE)。 */
rt_bool_t tmc_change_is_current_safe(rt_uint8_t vsense_old, rt_uint8_t vsense_new);

/* 逐项下发配置: GCONF(RMW) -> CHOPCONF(RMW) -> IHOLD_IRUN -> TPOWERDOWN。
 * 每次写入都读 IFCNT 前后验证 +1; 可读寄存器(GCONF/CHOPCONF)追加读回比对。
 * 任一步失败 → 立即中止并置 TMC_DRV_FAILED(不回滚、不继续写)。
 * 返回 -RT_EPERM = 硬件门禁未开放(编译期), 此时不产生任何总线写且状态不变。 */
rt_err_t tmc2209_apply_config(void);

/* 配置有效性复核(F2a): TMC 断电/复位/重连后寄存器会回到默认值, 而 MCU 侧
 * 状态不会自动知道 → 不能"UART 又能通了"就当作电流配置仍然有效。
 * 本函数把已下发的 GCONF/CHOPCONF 目标值与芯片现值逐项比对:
 *   RT_EOK    = 一致, 配置仍有效
 *   -RT_EPERM = 尚未 CONFIGURED / 链路不健康(不猜, 直接判不可信)
 *   -RT_EIO   = 现值与已下发值不符 或 读不到 → 状态立即降级为 FAILED
 * 只在明确需要的地方调用(motor_arm 决策点、tmc_config_show), 不放进周期任务,
 * 以免每次状态打印都产生总线流量。 */
rt_err_t tmc2209_config_integrity_check(void);

/* 供 motor 门禁使用的纯状态判定(无总线访问、无副作用):
 * 只有 TMC_DRV_CONFIGURED 才返回 RT_TRUE。链路/复位后的真实性由
 * motor_arm() 额外调用 tmc2209_config_integrity_check() 把关。 */
rt_bool_t tmc2209_drive_config_ok_for_arm(void);

/* 已下发配置的只读快照(供诊断显示; 未成功下发过则返回 RT_FALSE)。 */
rt_bool_t tmc2209_get_applied_config(rt_uint32_t *gconf, rt_uint32_t *chop,
                                      tmc_drv_config_t *out);

#endif /* TMC2209_H */
