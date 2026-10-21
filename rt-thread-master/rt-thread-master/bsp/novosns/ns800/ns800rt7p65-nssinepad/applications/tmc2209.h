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
 *   - WRITE 在 TMC2209 上无应答; 写帧的到达凭证只有 IFCNT 自增(禁止用 OTP 做测试)。
 *     F2d 起"写 + IFCNT 确认"收为配置事务内部实现, 不再对外提供裸写入口
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
 * 注意: 链路就绪 ≠ 芯片在位。在位/身份用只读手段核实 —— MSH 命令 tmc_scan
 * (READ IOIN 逐地址)或 tmc_uart_probe(F2d 起严格只读, 持配置事务锁读 IOIN/GSTAT/
 * IFCNT)。二者都**证明不了"写帧能到达"**; 写到达凭证只有 IFCNT+1 确认, 而那属于
 * 配置事务内部(见下方写入口审计), 未过编译期门与运行态门禁时不得为诊断而触发。 */
rt_err_t tmc2209_init(void);

/* 读寄存器。addr 一般为 TMC_ADDR_DEFAULT。RT_EOK 时 *value 有效。 */
rt_err_t tmc2209_read_register(rt_uint8_t addr, rt_uint8_t reg, rt_uint32_t *value);

/* ---------- F2d(F2c-NEW-01)写入口审计 ----------
 * 曾经对外的两条真实写路径的现在状态, 逐条核实过调用方:
 *   tmc_write_reg()                     已删除。它只持单次传输锁、不做到达确认,
 *                                       唯一调用点是旧 tmc_uart_probe() 的
 *                                       "把预读到的 GCONF 原值写回去"。
 *   tmc2209_write_register_confirmed()  改为 tmc2209.c 文件内静态(实现与 IFCNT
 *                                       确认语义一字未改)。唯一调用方
 *                                       tmc_cfg_hw_write() 位于配置事务内部:
 *                                       tmc_cfg_apply_ctx() 先非阻塞持 txn 锁,
 *                                       再求值运行态门禁, 全通才发写。
 * 头文件里不再出现任何"能直接写真实寄存器"的接口。 */

/* 读 StallGuard 结果(0x41)。电机静止时通常接近 0; 驱动使能转动后有意义。 */
rt_err_t tmc2209_read_sg_result(rt_uint16_t *sg_result);

/* 读全局状态 GSTAT(0x01)。位名按官方 Rev.1.09 **印刷第 24 页**寄存器表
 * (DATASHEET-CONFIRMED): bit0=reset / bit1=drv_err / bit2=uv_cp(电荷泵欠压);
 * 三位均为"写 1 清除", 不是读清。
 * F2d(F2c-NEW-02)更正: 旧注释/打印把 bit2 写成 sg2 —— TMC2209 的 SG_RESULT(0x41)
 * 是 StallGuard4 的结果, GSTAT 里没有 sg2, 沿用会误导故障方向。 */
rt_err_t tmc2209_read_status(rt_uint8_t *gstat);

/* 子系统健康(UNINIT/OK/DEGRADED/FAILED) */
subsys_health_t tmc2209_get_health(void);

/* ================= Phase 8-F2/F2b: 驱动参数配置(软件准备, 默认不启用) =================
 *
 * 本轮只做"能让配置写下去、能证明写下去、写不成就不许使能"的软件能力。
 * 真实写入被两层独立挡住, 两层都必须通过:
 *   ① 编译期硬门禁 TMC_DRIVE_CONFIG_HW_VALIDATED —— 安全链未验收 → 必须保持
 *      RT_FALSE, 直到硬件验收完成并经单独提交翻转。
 *      (事实更正 2026-10-18: MCU_DRV_ENABLE 节点在新扩展板上**已引出**, 当时的阻塞项是
 *       级联门电路损坏, 不是"漏画未引出"; 焊盘回读 LOW 证明不了 TMC_ENN 已禁能 ——
 *       这正是"编译期前置验收不能替代每次操作条件检查"的原因。
 *       状态更新 2026-10-21: 用户报告损坏的 U5 与门与急停反相器**已更换**(用户报告,
 *       未提供更换后的读数/波形)。"许可脚约 0V / Q1 栅约 3.3V / TMC_ENN 约 0V /
 *       U5 输入约 0V 而输出约 3.3V" 是**维修前历史读数**, 不得继续当作当前状态;
 *       换件也**不等于**验收通过 → 现记"更换完成(用户报告) + 维修后安全链待验收"。
 *       两路行程限位的实现保留, 由 project_board.h 的 SAFETY_LIMIT_INPUTS_ENABLED
 *       控制, 目标配置默认 RT_FALSE(不要求接 H2, 也不因未接而阻塞当前禁用配置)。)
 *   ② 运行态写入门禁 tmc2209_get_write_gate_mask() —— 每次 apply 都重新求值:
 *      电机必须完全静止且未 armed、DRV_ENABLE 焊盘实测 LOW、**最终 ENN 状态反馈
 *      (PC.6 = 板上 TMC_ENN / H6-5)确认在禁能侧**(2026-10-21 追加
 *      TMC_WGATE_ENN_FB_NOT_DISABLED, 焊盘 LOW 单独不再充分)、Safety 状态机为
 *      READY 且无锁存故障、UART 链路健康、配置事务未被占用。任一项不过 →
 *      不发出任何总线写、状态不变。
 *
 * apply 只做"写寄存器"这一件事: 不碰 ENN / MCU_DRV_ENABLE, 不写 VACTUAL,
 * 不产生 STEP, 不会让电机转动。 */

/* ⚠ 硬件安全链验收开关(真实寄存器写入的编译期门, 见上)。
 * RT_FALSE = tmc2209_apply_config() 一位都不写。翻转必须: ①使能链真机验收完成
 * (含 ENN 端实测, 而不是只看 MCU 焊盘) ②单独提交并附验证记录 ③运行态门禁同时全通。
 * 定义放在头文件, 使 runtime_selftest / system_status 等模块都能如实报告门的状态。 */
#ifndef TMC_DRIVE_CONFIG_HW_VALIDATED
#define TMC_DRIVE_CONFIG_HW_VALIDATED   RT_FALSE
#endif

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

/* CHOPCONF 位。vsense 位归属已按官方 Rev.1.09 寄存器表人工核对: bit17 = vsense
 * (0 -> V_FS 325mV 低灵敏度, 1 -> 180mV 高灵敏度), bit18 属 TOFF/DRV 保留侧。
 * 证据等级: DATASHEET-CONFIRMED(人工对照手册), 仍非本板实测。
 * ⚠ 本板从未写入过 CHOPCONF, 开启写入门禁前必须先做一次真机写+读回核实。 */
#define TMC_CHOPCONF_TOFF_MASK      0x0000000Fu
#define TMC_CHOPCONF_HSTRT_SHIFT    4
#define TMC_CHOPCONF_HEND_SHIFT     7
#define TMC_CHOPCONF_TBL_SHIFT      15
#define TMC_CHOPCONF_VSENSE         (1u << 17)
#define TMC_CHOPCONF_MRES_SHIFT     24
#define TMC_CHOPCONF_MRES_MASK      0x0Fu

/* IHOLD_IRUN(0x10) 字段布局: IHOLD[4:0] / IRUN[12:8] / IHOLDDELAY[19:16]
 * (地址 0x10 与位域均已按官方 Rev.1.09 寄存器表人工核对; 旧注释误写为 0x30)。
 * 该寄存器为只写: 不得要求读回, 到达凭证只有 IFCNT+1 —— 因此偏移/数值错了芯片
 * 照样应答, 电流档位却设错。开启写入门禁的前置条件仍是"真机写后用电流实测验证",
 * 而不是本文件的任何算术。 */
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
 * 任一步失败 → 立即中止并置 TMC_DRV_FAILED(不回滚、不继续写), 同时**失效已下发
 * 快照**(否则旧快照会被当作有效配置继续放行)。
 * 写入前先求值运行态门禁, 返回码把"条件不满足"和"无法串行化"分开(F2c R02):
 *   RT_EOK      已下发并确认
 *   -RT_EPERM   门禁不通过(编译期宏 FALSE 或任一运行态条件不满足): 不产生任何总线动作(连 open 都不调), 状态与快照不变
 *   -RT_EBUSY   另一条配置/arm 事务正持有 txn 锁
 *   -RT_ENOSYS  执行器互斥量没建起来(无法串行化) —— 一律拒绝, 绝不退化成"无锁也能写" */
rt_err_t tmc2209_apply_config(void);

/* 运行态写入门禁位掩码(纯只读, 不产生总线流量; 0 = 全部通过)。
 * 各位定义见 TMC_WGATE_*, 供 apply 自检与 system_status/motor_status 打印。 */
rt_uint32_t tmc2209_get_write_gate_mask(void);

#define TMC_WGATE_HW_NOT_VALIDATED (1u << 0)  /* 编译期安全链验收宏为 FALSE */
#define TMC_WGATE_MOTOR_NOT_QUIET  (1u << 1)  /* 电机 armed / 非 IDLE / hz 非 0 / 快照取不到 */
#define TMC_WGATE_DRV_PAD_NOT_LOW  (1u << 2)  /* DRV_ENABLE 焊盘未实测 LOW */
#define TMC_WGATE_NOT_READY        (1u << 3)  /* Safety 状态机不是 READY */
#define TMC_WGATE_FAULT_LATCHED    (1u << 4)  /* 存在未清除的故障码 */
#define TMC_WGATE_LINK_UNHEALTHY   (1u << 5)  /* UART 链路 FAILED/DEGRADED/UNINIT */
#define TMC_WGATE_NOT_SYNCED       (1u << 6)  /* F2c R02: 执行器的锁没建起来 = 无法串行化 */
/* 追加(2026-10-21, 不复用旧位): 最终 ENN 状态反馈(PC.6 = 板上 TMC_ENN / H6-5)没有确认在
 * 禁能侧。bit2 只证明 MCU 焊盘 LOW(请求侧), 在级联逻辑损坏/断电复位等情况下证明不了
 * 驱动真的被禁能 —— 写电流档/斩波器参数之前必须看最终反馈。 */
#define TMC_WGATE_ENN_FB_NOT_DISABLED (1u << 7)

/* F2b P1-D: 配置事务串行化。apply 与 motor_arm 的"查状态→复核→请求使能"整段
 * 必须持有同一把 txn 锁, 否则二者交错会出现"arm 复核通过后 apply 改了电流档"
 * 这类 TOCTOU。begin 为非阻塞 try-take(忙返回 -RT_EBUSY, 调用方必须 fail closed),
 * 同线程可重入(RT-Thread 互斥量带持有计数), 故 arm 段内的完整性复核不会自锁。
 * ⚠ Safety 停机路径(safety_force_shutdown / motor_emergency_stop)一律不调用
 *   begin —— 停机绝不能被配置事务阻塞。 */
rt_err_t tmc2209_cfg_txn_begin(void);
void     tmc2209_cfg_txn_end(void);

/* 配置有效性复核(F2a/F2b): TMC 断电/复位/重连后寄存器会回到默认值, 而 MCU 侧
 * 状态不会自动知道 → 不能"UART 又能通了"就当作电流配置仍然有效。
 * 本函数把已下发的 GCONF/CHOPCONF 目标值与芯片现值逐项比对:
 *   RT_EOK    = 一致, 配置仍有效
 *   -RT_EPERM = 尚未 CONFIGURED / 快照已失效(不猜, 直接判不可信)
 *   -RT_EIO   = 现值与已下发值不符 或 读不到/链路不健康 → 状态立即降级为 FAILED
 *   -RT_EBUSY = 另一条配置/arm 事务正在进行, 此刻无法复核(调用方必须拒绝使能)
 *   -RT_ENOSYS = 执行器互斥量不可用, 无法保证复核期间没有并发写 → 同样拒绝
 * 调用点: motor_arm 决策点、motor_start 起转前复核(F2c R03)、Motor 线程周期守卫
 * (F2b P1-E)、以及**显式**巡检命令 tmc_drv_probe/motor_drv_guard_probe。
 * ⚠ 本函数会真实读 GCONF/CHOPCONF(UART 流量), 且失败会把生产配置状态打成 FAILED,
 *   因此绝不允许放进"纯软件"真值表自测 —— 见 motor.c 的 R04 拆分。 */
rt_err_t tmc2209_config_integrity_check(void);

/* 供 motor 门禁使用的纯状态判定(无总线访问、无副作用):
 * 只有 TMC_DRV_CONFIGURED **且已下发快照仍有效** 才返回 RT_TRUE。F2b 起加了
 * 后半条件: 配置失败/失联/复位/重新配置中都必须让本门立刻闭合, 不能等到
 * 下一次完整性复核才发现。F2c R02 再加一条: 执行器的锁不可用时一律返回 RT_FALSE
 * (状态无法原子读取, 不猜)。链路/复位后的真实值由
 * tmc2209_config_integrity_check() 在 arm 决策点、start 起转前与运行期守卫把关。 */
rt_bool_t tmc2209_drive_config_ok_for_arm(void);

/* 已下发配置的只读快照(供诊断显示; 未成功下发过则返回 RT_FALSE)。 */
rt_bool_t tmc2209_get_applied_config(rt_uint32_t *gconf, rt_uint32_t *chop,
                                      tmc_drv_config_t *out);

#endif /* TMC2209_H */
