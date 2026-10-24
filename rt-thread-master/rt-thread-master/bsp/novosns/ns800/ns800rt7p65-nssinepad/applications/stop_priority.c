/*
 * stop_priority.c - 停机优先协调层 (Phase 8 S2 集中实现, 2026-10-23)
 *
 * 实现 stop_priority.h 承诺的协议。要点全部写在头文件里，这里只列实现纪律：
 *   1) 唯一的共享可变量就是 sp_state_t sp；所有读写都在同一个短临界区
 *      (rt_hw_interrupt_disable/enable) 内完成，导出的每个函数自己开/关这个窗口，
 *      因此调用方不会在窗口里意外拿到一把可 FOREVER 等待的锁。
 *   2) 窗口内的动作集合被严格限制为：固定数目的字段赋值 + 固定数目的寄存器写
 *      (rt_pwm_disable / rt_pin_write)。**没有**取锁、mdelay、UART、rt_kprintf、循环、浮点。
 *      依据: drv_gpio.c 的 pin_write 是 SET/CLR 单写; drv_epwm.c 的 disable 是一次
 *      MODIFY_REG(TBCTL.CTRMODE); 而 drv_uart.c:403-427 那种"按 rt_tick_get() 判超时"的
 *      等待在 PRIMASK 窗口内**永不退出**(SysTick 被同一次屏蔽挡住) —— 这就是禁止清单的根因。
 *   3) MMIO 之后先 __DSB()/等价的完整内存栅栏再恢复中断: 外设区在 ARMv7-M 默认映射里是
 *      Device 类型(EPWM1=0x40000000, GPIOA=0x400A0000)，允许写缓冲，栅栏保证后续观察者
 *      不会看到乱序的寄存器效果。主机侧用真实原子栅栏(不冒称模拟总线)。
 *
 * 不做的两件事（明确写下来，避免被误读成已具备）：
 *   - 不声称本机制能协调另一核/其它总线主设备/NMI/HardFault，也不声称能拦住通用 `pin`
 *     命令一类的维护旁路写；
 *   - 不提供任何停机时延数字：窗口长度与各段实际耗时本轮 NOT_TESTED。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <rthw.h>
#include "stop_priority.h"
#include "safety_gpio.h"

#define SP_EPOCH_MAX   0xFFFFFFFFu

/* ---------------- 屏障 ---------------- */
#if defined(__ARMCC_VERSION) || defined(__CC_ARM) || (defined(__GNUC__) && defined(__arm__))
#ifndef __DSB
#define __DSB() __asm__ __volatile__ ("dsb 0xF" ::: "memory")
#endif
#define SP_BARRIER() __DSB()
#else
#define SP_BARRIER() __atomic_thread_fence(__ATOMIC_SEQ_CST)
#endif

/* 读 PRIMASK 的原语(仅目标分支使用, 见 sp_intr_masked)。 */
#if defined(__ARM_ARCH) && (__ARM_ARCH >= 7)
#define SP_READ_PRIMASK(v)  __asm__ __volatile__ ("mrs %0, primask" : "=r"(v))
#endif

/* R5(2026-10-23): 新增的生产接口(HIGH / RUN)自己守编译期许可, 不依赖"每个调用者
 * 永远不出错"。这两个宏就是目标工程里那份公开配置(motor.h / project_board.h),
 * 主机夹具要么用真实头, 要么在命令行显式 -D 覆盖 —— 两条构建走同一分支逻辑。 */
#include "motor.h"
#include "project_board.h"
#include "tmc2209.h"       /* CP4 §8.2: HIGH/RUN 还要核 TMC 配置许可与同代无等待摘要 */

/* ---------------- 状态 ---------------- */
typedef struct
{
    rt_uint32_t      epoch;
    sp_owner_t       owner;
    sp_phase_t       phase;
    rt_bool_t        inhibit;
    rt_bool_t        exhausted;
    rt_bool_t        motor_stop_latched;
    rt_bool_t        diag_stop_latched;
    rt_bool_t        motor_output_active;
    rt_bool_t        diag_output_active;
    rt_bool_t        diag_ever_reserved;
    rt_bool_t        metadata_pending;
    rt_uint32_t      pending_epoch;
    rt_uint32_t      first_fault_code;
    rt_uint32_t      reason_events;
    rt_uint32_t      trips;
    rt_uint32_t      merged;
    rt_uint32_t      stale_dropped;
    rt_uint32_t      advance_failed;
    sp_pwm_rec_t     pwm;
    sp_pwm_rec_t     diag_pwm;            /* K6.1: 诊断那一路自己的关断证据 */
    rt_bool_t        diag_rec_valid;      /* 诊断那一路是否真的记过一次(不伪造) */
    rt_uint32_t      last_trip_epoch;     /* 最近一次短保护建立的代次(K3 三态判定) */
    sp_permit_rec_t  permit;
    sp_pwm_rec_t     first_unrecovered;
    rt_bool_t        first_unrecovered_valid;
    sp_permit_rec_t  first_permit_unrecovered;
    rt_bool_t        first_permit_valid;
} sp_state_t;

static sp_state_t sp;                                  /* 静态零值 = 未抑制/无持有者 */
static struct rt_device_pwm *sp_pwm  = RT_NULL;
static int                   sp_pwm_ch = 0;
static rt_base_t             sp_permit_pin = -1;       /* 绑定缓存, 窗口内不做字符串查找 */

/* ---------------- 窗口原语 ---------------- */
/* 嵌套深度只为**主机夹具**提供"当前是否处在屏蔽窗口里"的可查询事实(夹具的
 * rt_hw_interrupt_disable 替身不暴露 PRIMASK)。目标上两个来源取并集: PRIMASK 为真
 * 或深度 > 0。计数器本身就是窗口内的一个字段写(固定一次 ++ / 一次 --)。 */
static rt_int32_t sp_win_nest;

static rt_base_t sp_win(void)
{
    rt_base_t l = rt_hw_interrupt_disable();

    sp_win_nest++;
    return l;
}

static void sp_end(rt_base_t l)
{
    SP_BARRIER();
    if (sp_win_nest > 0) sp_win_nest--;
    rt_hw_interrupt_enable(l);
}

rt_bool_t sp_intr_masked(void)
{
    rt_uint32_t pm = 0u;

#if defined(__ARM_ARCH) && (__ARM_ARCH >= 7)
    /* 目标: 读**真实** PRIMASK —— 别人屏蔽的窗口也算(只靠本模块的嵌套计数会把
     * "别处已关中断"说成可以打印/可以忙等)。与上面的 __DSB 同一条原则: 这条原语
     * 由本模块自带, 不依赖上游是否恰好包含了 CMSIS 设备头。 */
    SP_READ_PRIMASK(pm);
    if (pm != 0u) return RT_TRUE;
#else
    (void)pm;
#endif
    return (sp_win_nest > 0) ? RT_TRUE : RT_FALSE;
}

/* 导出给**必须把多个模块的字段写进同一个短临界区**的最终提交点使用
 * (目前唯一用户 = safety_state.c 的 fault_reset 第 6 步)。
 * 目的: 把 ARM 的 PRIMASK/DSB 细节留在本模块里, 不让状态机源码直接写内联汇编,
 *       也让主机夹具能用自己的串行化模型替换这一对原语。 */
rt_base_t sp_win_begin(void)  { return sp_win(); }
void      sp_win_end(rt_base_t saved) { sp_end(saved); }

/* 任务书 §4.1 的控制结构。R6(2026-10-23)取消原先自加的 24 位子集: epoch 按 32 位
 * 使用, 到 0xFFFFFFFF 饱和, 不回绕、运行期不重置。"还能不能前进"用返回值告诉调用方,
 * 因此预约/正式释放/人工恢复这些**边界失败不得算成功**的路径可以先预检再改字段。
 * caller 已屏蔽中断。返回 FALSE 时仍须走本次关闭, 不得早退豁免。 */
static rt_bool_t sp_advance_held(void)
{
    if (sp.epoch == SP_EPOCH_MAX)
    {
        sp.exhausted = RT_TRUE;
        sp.inhibit   = RT_TRUE;
        sp.advance_failed++;
        return RT_FALSE;                       /* 饱和: 仍继续本次关闭, 但不再接受预约/恢复 */
    }
    ++sp.epoch;
    if (sp.epoch == SP_EPOCH_MAX)
    {
        sp.exhausted = RT_TRUE;
        sp.inhibit   = RT_TRUE;
        sp.advance_failed++;
        return RT_FALSE;
    }
    return RT_TRUE;
}

static rt_bool_t sp_can_advance_held(void);

/* 只预检"还能前进", 一个字段都不改(避免"清完历史才发现饱和"的半途提交)。 */
rt_bool_t sp_can_advance(void)
{
    rt_base_t l;
    rt_bool_t v;

    l = sp_win(); v = sp_can_advance_held(); sp_end(l);
    return v;
}

static rt_bool_t sp_can_advance_held(void)
{
    /* R6(2026-10-23 CP3 §8 第 2 条): 判据必须和 sp_advance_held() 的**成败条件**一致 ——
     * advance 在"自增之后撞上 0xFFFFFFFF"时也判失败, 所以这里要问的是"再加一还撞不撞顶",
     * 不是"现在是不是顶"。少这一格就会出现"预检查通过 -> 清完历史字段 -> advance 才发现
     * 饱和"的半途提交(审查探针 saturated-recovery-clears-inhibit 的根因之一)。 */
    return (sp.epoch < SP_EPOCH_MAX - 1u) ? RT_TRUE : RT_FALSE;
}

static rt_bool_t sp_owner_valid(sp_owner_t o)
{
    return (o == SP_MOTOR || o == SP_DIAG) ? RT_TRUE : RT_FALSE;
}

static rt_bool_t sp_token_ok_held(const sp_token_t *t)
{
    return (t != RT_NULL) && !sp.inhibit && !sp.exhausted &&
           (sp.owner == t->owner) && (sp.epoch == t->epoch) &&
           sp_owner_valid(t->owner)
               ? RT_TRUE : RT_FALSE;
}

/* R5 + CP4 §8.2: HIGH 是"让驱动器得电"的动作 => token 资格之外还要过
 * ① MOTOR_HARDWARE_ENABLE_PATH_VALIDATED(Motor 使能链验收) 与
 * ② TMC_DRIVE_CONFIG_HW_VALIDATED(驱动配置路径验收)两道编译期许可, 再加
 * ③**同代无等待配置有效性摘要**仍然放行(第 5 门的那份摘要: 一次保护/失联/晚到成功
 *   都被 inv_gen/invalid 绑住, 这里读取不取锁、不碰 UART、不延时)。
 * 且**只有 Motor** 能请求 HIGH(诊断借用只允许出 STEP, 不许给驱动供电)。 */
static rt_bool_t sp_high_permit_ok_held(const sp_token_t *t)
{
    if (!sp_token_ok_held(t))                      return RT_FALSE;
    if (t->owner != SP_MOTOR)                      return RT_FALSE;
    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)     return RT_FALSE;
    if (!TMC_DRIVE_CONFIG_HW_VALIDATED)            return RT_FALSE;
    if (!tmc2209_drive_config_ok_for_arm())        return RT_FALSE;
    return RT_TRUE;
}

/* R5: RUN(启动共享时间基)的许可 —— Motor 要硬件许可 + 本预约已到 ARMED;
 * 诊断保留原双宏合同(硬件许可 或 操作员已确认隔离环境)。 */
static rt_bool_t sp_run_permit_ok_held(const sp_token_t *t)
{
    if (!sp_token_ok_held(t))                      return RT_FALSE;
    if (t->owner == SP_MOTOR)
    {
        /* CP4 §8.2: RUN 与 HIGH 共用同一套许可(编译期两宏 + 同代配置有效性摘要),
         * 再加上"本预约已经真正到过 ARMED"。 */
        if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED) return RT_FALSE;
        if (!TMC_DRIVE_CONFIG_HW_VALIDATED)        return RT_FALSE;
        if (!tmc2209_drive_config_ok_for_arm())    return RT_FALSE;
        if (sp.phase < SP_ARMED)                   return RT_FALSE;
        return RT_TRUE;
    }
    if (t->owner == SP_DIAG)
    {
        if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED && !STEP_DIAG_ISOLATED_ENV_CONFIRMED)
            return RT_FALSE;
        return RT_TRUE;
    }
    return RT_FALSE;
}

static rt_bool_t sp_same_owner_epoch_held(const sp_token_t *t)
{
    return (t != RT_NULL) && (sp.owner == t->owner) && (sp.epoch == t->epoch)
               ? RT_TRUE : RT_FALSE;
}

/* R1: pending **只由新的故障/失败事实**产生(这里是唯一置位点)。成功的确认、
 * 普通成功的 disarm、状态查询都不再置它 —— 之前"成功 LOW 确认也置 pending"会把
 * 一次正常 disarm 变成一次待整理的故障义务(审查探针 normal-disarm-pending)。
 * pending_epoch 记录义务属于哪一代, 供 peek/gate/ack 配对。 */
static void sp_retain_held(rt_uint32_t code, rt_uint32_t events)
{
    if (!sp.inhibit)
    {
        sp.first_fault_code = code;            /* 首故障保留, 后来者不覆盖 */
    }
    else if (sp.first_fault_code != code)
    {
        sp.merged++;                           /* 同一波里的合并次数(可观测, 不丢原因) */
    }
    sp.reason_events    |= events;
    sp.inhibit           = RT_TRUE;
    sp.metadata_pending  = RT_TRUE;
    sp.pending_epoch     = sp.epoch;
}

static void sp_note_pwm_held(rt_err_t pe, rt_bool_t attempted, rt_uint8_t src,
                             sp_owner_t actor, sp_owner_t before);
static void sp_note_permit_held(rt_err_t le, rt_uint8_t src, rt_uint8_t level);
static void sp_copy_proof_held(sp_proof_t *out);
rt_bool_t   sp_diag_off_confirmed_held(void);
/* 记录一次**实际**关断尝试。R6: 除了模块的当前输出事实, 还要分 owner 维护各自的
 * "我这一路是否仍未确认交回"与历史失败锁存 —— Motor 新一轮运行不得改写诊断已经
 * 归还过的历史视图(反之亦然)。 */
static void sp_note_pwm_held(rt_err_t pe, rt_bool_t attempted, rt_uint8_t src,
                             sp_owner_t actor, sp_owner_t before)
{
    rt_bool_t failed;

    sp.pwm.attempted = attempted;
    sp.pwm.raw_rc    = pe;
    sp.pwm.src       = src;
    sp.pwm.epoch     = sp.epoch;
    if (!attempted)
    {
        sp.pwm.result = MOT_STOP_DEV_UNAVAILABLE;
        sp.pwm.raw_rc = -RT_ENOSYS;
    }
    else
    {
        sp.pwm.result = (pe == RT_EOK) ? MOT_STOP_API_ACCEPTED : MOT_STOP_API_FAILED;
    }
    /* K6.1(§8.1): 发起者**自己那一路**的历史单独留档 —— Motor 后来的一次启动或关断
     * 不得改写诊断已经交回过的证据(反之亦然)。actor = 这次动作的发起者; 短保护的
     * actor 取"当时持有通道的那一路"(同一次 rt_pwm_disable 确实把它的输出也冻住了)。 */
    if (actor == SP_DIAG)
    {
        sp.diag_pwm       = sp.pwm;
        sp.diag_rec_valid = RT_TRUE;
    }
    failed = (sp.pwm.result == MOT_STOP_API_FAILED ||
              sp.pwm.result == MOT_STOP_DEV_UNAVAILABLE) ? RT_TRUE : RT_FALSE;

    /* 模块当前输出事实: 关断被接受 => 时间基已冻结, 两条 owner 的"输出中"都清零;
     * 失败 => 至少发起者这一路仍是"未确认交回"。 */
    if (!failed)
    {
        sp.motor_output_active = RT_FALSE;
        sp.diag_output_active  = RT_FALSE;
    }
    else
    {
        if (before == SP_DIAG || sp.diag_ever_reserved)
        {
            sp.diag_stop_latched  = RT_TRUE;
            sp.diag_output_active = RT_TRUE;
        }
        if (before != SP_DIAG)
        {
            sp.motor_stop_latched = RT_TRUE;
            sp.motor_output_active = RT_TRUE;
        }
        if (!sp.first_unrecovered_valid)          /* 首个未恢复的 PWM 失败(可判定) */
        {
            sp.first_unrecovered       = sp.pwm;
            sp.first_unrecovered_valid = RT_TRUE;
        }
    }
}

/* level: 本次裸写请求的电平(0=LOW 禁能, 1=HIGH 使能)。R1: 确认提交必须能核对
 * "确认的是哪一次请求", 所以请求电平与 epoch 一起记。 */
static void sp_note_permit_held(rt_err_t le, rt_uint8_t src, rt_uint8_t level)
{
    sp.permit.written       = RT_TRUE;
    sp.permit.request_level = level;
    sp.permit.write_rc      = le;
    sp.permit.src           = src;
    sp.permit.write_epoch   = sp.epoch;
    sp.permit.confirmed     = RT_FALSE;         /* 裸写 != 回读确认 */
    sp.permit.confirm_rc    = RT_EOK;
    sp.permit.confirm_epoch = 0u;
    sp.permit.confirmed_level = SP_PERMIT_LEVEL_NONE;
    sp.permit.enn_ok          = RT_FALSE;
    sp.permit.enn_rc          = RT_EOK;
    sp.permit.enn_epoch       = 0u;
    sp.permit.enn_level       = SP_PERMIT_LEVEL_NONE;
    sp.permit.cfg_view_gen    = 0u;
    if (le != RT_EOK && !sp.first_permit_valid) /* 首个未恢复的许可失败也要可判定 */
    {
        sp.first_permit_unrecovered = sp.permit;
        sp.first_permit_valid       = RT_TRUE;
    }
}

static void sp_copy_proof_held(sp_proof_t *out)
{
    if (out == RT_NULL) return;
    out->valid          = RT_TRUE;
    out->epoch          = sp.epoch;
    out->pwm            = sp.pwm;
    out->permit         = sp.permit;
    /* 诊断视图按**诊断自己那一路**的语义取(从未占用=NOT_ATTEMPTED, 已归还=API_ACCEPTED),
     * 不是直接抄模块当前的 pwm 结果 —— 否则 Motor 的新一轮会把诊断的历史说成当前事实。 */
    sp_view_diag_held(&out->diag_result, &out->diag_rc, &out->diag_stop_latched);
    out->diag_attempted = sp.diag_pwm.attempted;   /* K6.1: 诊断那一路自己的记录 */
    out->inhibit          = sp.inhibit;
    out->first_fault_code = sp.first_fault_code;
    out->reason_events    = sp.reason_events;
    out->motor_off_ok   = (sp.pwm.result == MOT_STOP_API_ACCEPTED &&
                           !sp.motor_stop_latched) ? RT_TRUE : RT_FALSE;
    out->motor_stop_latched = sp.motor_stop_latched;
    out->diag_off_ok    = (sp_diag_off_confirmed_held()) ? RT_TRUE : RT_FALSE;
}

/* 诊断视图的**单一**判据(窗口内版本; view_diag / off_confirmed / output_active /
 * proof 都从同一组分 owner 字段出发, 不再单独把某个函数硬改成 TRUE —— K6.1):
 *   ① 带着未复核的历史关断失败锁存 => 无效(交接仍然被挡);
 *   ② 诊断这一路现在仍持有输出, 或当前就是诊断持有通道 => 无效;
 *   ③ 从未占用过该通道, 或占用过但从未产生过需要确认的关断(预约后没起转就正式归还)
 *      => 有效, 且不伪造一次 API 成功;
 *   ④ 真的记过一次 => 必须是**诊断那一路自己**最近一次实际关断被驱动接受。
 * Motor 的新一轮运行/关断只改写模块当前事实(sp.pwm), 不改写 ④ 的证据。 */
rt_bool_t sp_diag_off_confirmed_held(void)
{
    if (sp.diag_stop_latched) return RT_FALSE;
    if (sp.owner == SP_DIAG || sp_diag_output_active_held()) return RT_FALSE;
    if (!sp.diag_rec_valid)   return RT_TRUE;
    return (sp.diag_pwm.result == MOT_STOP_API_ACCEPTED) ? RT_TRUE : RT_FALSE;
}

/* 一次完整的短保护(调用方已屏蔽中断): 换 epoch + 抑制 + 实际关断 + 独立许可 LOW + 记账。 */
static rt_err_t sp_protect_held(rt_uint32_t code, rt_uint32_t events, rt_uint8_t src,
                                sp_proof_t *out)
{
    sp_owner_t before = sp.owner;
    rt_err_t   pe, le;

    sp_advance_held();                         /* 饱和也继续往下关闭 */
    sp_retain_held(code, events);

    if (sp_permit_pin < 0) sp_permit_pin = safety_drv_permit_pin();

    sp.last_trip_epoch = sp.epoch;            /* K3: 这次保护建立的是哪一代 */
    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    sp_note_pwm_held(pe, sp_pwm != RT_NULL ? RT_TRUE : RT_FALSE, src, before, before);

    le = safety_drv_permit_low_raw_held();    /* 与 pe 无关, 独立尝试 */
    sp_note_permit_held(le, src, 0u);

    sp.owner = (pe == RT_EOK) ? SP_NONE : SP_HELD_UNKNOWN;
    sp.phase = (pe == RT_EOK) ? SP_RESERVED : sp.phase;
    sp.trips++;
    sp_copy_proof_held(out);

    if (pe != RT_EOK) return pe;
    return le;
}

/* ---------------- 绑定 ---------------- */
rt_err_t sp_bind_pwm(struct rt_device_pwm *pwm, int channel)
{
    rt_base_t l;

    l = sp_win();
    sp_pwm    = pwm;
    sp_pwm_ch = channel;
    sp_end(l);
    return RT_EOK;                             /* 不重置 epoch/锁存/inhibit */
}

rt_err_t sp_bind_permit_pin(rt_base_t numeric_pin)
{
    rt_base_t l;

    l = sp_win();
    sp_permit_pin = numeric_pin;
    sp_end(l);
    return (numeric_pin >= 0) ? RT_EOK : -RT_EINVAL;
}

void sp_get_bound(struct rt_device_pwm **out_pwm, int *out_ch, rt_base_t *out_pin)
{
    rt_base_t l = sp_win();

    if (out_pwm) *out_pwm = sp_pwm;
    if (out_ch)  *out_ch  = sp_pwm_ch;
    if (out_pin) *out_pin = sp_permit_pin;
    sp_end(l);
}

/* ---------------- 短保护入口 ---------------- */
rt_err_t sp_trip_fast(rt_uint32_t code, rt_uint32_t events, rt_uint8_t src,
                      sp_proof_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = sp_protect_held(code, events, src, out);

    sp_end(l);
    return e;
}

/* R2(2026-10-23): 失配时**不只**返回 -RT_EINTR —— 还把当前 canonical 事实复制给调用方。
 * 守卫(mot_guard_shutdown)拿到"底层已经保护过"的证据后才能照常做自己那一路的状态整理,
 * 而不是拿着自己的旧数字瞎写; 同时它仍然**不做**第二轮关闭。
 * CP4 K3(§5.3): 再加 outcome 三态 —— 调用方**不能**把 out->valid=TRUE 一律读成
 * "这次操作已经保护完"(三种语义见头文件 sp_protect_outcome_t)。 */
rt_err_t sp_trip_for_operation(rt_uint32_t expected_epoch, rt_uint32_t code,
                               rt_uint32_t events, rt_uint8_t src, sp_proof_t *out,
                               sp_protect_outcome_t *outcome)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (outcome != RT_NULL) *outcome = SP_PROTECT_NONE;
    if (sp.epoch != expected_epoch)
    {
        /* 同一操作里已有别的层做过保护/已有更新的请求 => 本次不做第二轮关闭 */
        sp.stale_dropped++;
        sp_copy_proof_held(out);
        if (out != RT_NULL) out->valid = RT_TRUE;   /* 这是"当前事实", 不是本次动作 */
        /* 当前代仍挂着抑制 = 有一条待整理的故障事实覆盖着(本操作更低的层做过, 或另一条
         * 并发故障都算): 调用方只能用带回的 proof 整理自己那一路, 落位仍要过同一代门;
         * 没有抑制 = 已换到人工恢复/新预约/正式释放那一代: 本次结论**过期丢弃**,
         * 既不称"已保护", 也不得据此写任何状态。 */
        if (outcome != RT_NULL)
            *outcome = sp.inhibit ? SP_PROTECT_ALREADY_PROTECTED : SP_PROTECT_SUPERSEDED;
        sp_end(l);
        return -RT_EINTR;
    }
    if (outcome != RT_NULL) *outcome = SP_PROTECT_BY_THIS_CALL;
    e = sp_protect_held(code, events, src, out);
    sp_end(l);
    return e;
}

rt_uint32_t sp_epoch(void)
{
    rt_base_t l;
    rt_uint32_t e;

    l = sp_win(); e = sp.epoch; sp_end(l);
    return e;
}

rt_bool_t sp_inhibited(void)
{
    rt_base_t l;
    rt_bool_t v;

    l = sp_win(); v = sp.inhibit; sp_end(l);
    return v;
}

rt_bool_t sp_inhibited_held(void)
{
    return sp.inhibit;
}

/* epoch 已饱和(禁止新的预约与人工恢复, 但每次显式保护仍照做关闭)。 */
rt_bool_t sp_epoch_exhausted(void)
{
    rt_base_t l;
    rt_bool_t v;

    l = sp_win(); v = sp.exhausted; sp_end(l);
    return v;
}

/* R3(2026-10-23): 已经在窗口内的调用方(如 motor_set_direction 把"全部静止/可信关闭
 * 复核 + 真实 DIR 写"收进同一个短临界区)需要一次**不再开窗**的 canonical 复制 ——
 * 否则它只能拿到 inhibit 一个位, 用偏薄的条件代替完整判据; 而在窗口里再调带窗口的
 * 读取函数在主机模型(非递归互斥)里还会自锁。 */
void sp_peek_held(sp_snapshot_t *out)
{
    if (out == RT_NULL) return;
    out->epoch                  = sp.epoch;
    out->owner                  = sp.owner;
    out->phase                  = sp.phase;
    out->inhibit                = sp.inhibit;
    out->exhausted              = sp.exhausted;
    out->motor_stop_latched     = sp.motor_stop_latched;
    out->diag_stop_latched      = sp.diag_stop_latched;
    out->motor_output_active    = sp.motor_output_active;
    out->diag_output_active     = sp.diag_output_active;
    out->diag_ever_reserved     = sp.diag_ever_reserved;
    out->metadata_pending       = sp.metadata_pending;
    out->pending_epoch          = sp.pending_epoch;
    out->first_fault_code       = sp.first_fault_code;
    out->reason_events          = sp.reason_events;
}

rt_bool_t sp_owner_is(sp_owner_t o)
{
    rt_base_t l;
    rt_bool_t v;

    l = sp_win(); v = (sp.owner == o) ? RT_TRUE : RT_FALSE; sp_end(l);
    return v;
}

sp_phase_t sp_get_phase(void)
{
    rt_base_t l;
    sp_phase_t p;

    l = sp_win(); p = sp.phase; sp_end(l);
    return p;
}

/* ---------------- 关断尝试(ramp/apply/disarm/pwm_test 共用一条路径) ----------------
 * CP4 K3(§5.1) + K4(§6.1/§6.2) 的三条硬约束:
 *   1) **身份先查, 后写**: expected_epoch 是原操作在命令入口第一次等待之前采的代次。
 *      失配时一个寄存器写都不发 —— 不清新 owner、不写新影子、不产生新 pending,
 *      只带回当前事实并返回 -RT_EINTR(旧 arm 回滚 / 旧 apply 回退 / 旧 ramp-zero
 *      失配都从这里被挡在硬件之前)。
 *   2) **一次动作的结果身份预先确定**: 正式释放(release_on_ok)先换到本次动作所在的
 *      那一代, 再把唯一的 disable 与独立 LOW 记进**同一代**; 于是 proof.epoch、
 *      pwm.epoch、permit.write_epoch 与之后窗口外确认的 expected_epoch 指向同一次事实,
 *      不再出现"关断记在旧代、proof 发在新代"(原实现里 disarm 的 LOW 确认因此永远
 *      提交不上, 快照一直是 confirmed=FALSE / write_epoch=旧代)。失败支同理:
 *      换代 + 留 pending 之后的 disable/LOW 全部落在**那一代**。
 *   3) 返回值优先序(§6.2): PWM 失败 > 许可失败 > 换代/边界失败; 三个 raw 各自留档,
 *      不存合并值。撞顶仍照做实际关闭, 但绝不返回 EOK(§6.4)。
 * ⚠ disable 仍然**恰好一次**(S1-R1 R04 的"同一动作不互相覆盖"继续成立)。 */
rt_err_t sp_off_attempt_for(rt_uint32_t expected_epoch, sp_owner_t who, rt_uint8_t src,
                            rt_uint32_t code, rt_uint32_t events, int release_on_ok,
                            sp_proof_t *out)
{
    rt_base_t  l = sp_win();
    sp_owner_t before = sp.owner;
    rt_bool_t  attempted;
    rt_err_t   pe, le, boundary = RT_EOK;

    if (!sp_owner_valid(who))
    {
        sp_end(l);
        return -RT_EINVAL;
    }
    if (sp.epoch != expected_epoch)
    {
        sp.stale_dropped++;
        sp_copy_proof_held(out);
        if (out != RT_NULL) out->valid = RT_TRUE;   /* 当前事实, 不是本次动作 */
        sp_end(l);
        return -RT_EINTR;                           /* 无硬件写, 不动新 owner */
    }
    if (sp_permit_pin < 0) sp_permit_pin = safety_drv_permit_pin();
    attempted = (sp_pwm != RT_NULL) ? RT_TRUE : RT_FALSE;

    if (release_on_ok)
    {
        /* 正式释放 = 一次合法换代(§6.1"身份预先确定"); 撞顶时不产生新身份,
         * 关闭照做但整次动作不报成功。 */
        if (!sp_advance_held()) boundary = -RT_EPERM;
        /* 只有本来就是这个 owner(或已无人认领/状态未知)时才撤销预约; 别的 owner
         * 仍持有时这次 disarm/pwm_test stop 无权清它的身份(共享模块的关断照做)。 */
        if (before == who || before == SP_NONE || before == SP_HELD_UNKNOWN)
        {
            sp.owner = SP_NONE;
            sp.phase = SP_RESERVED;
            if (who == SP_DIAG) sp.diag_output_active  = RT_FALSE;
            else                sp.motor_output_active = RT_FALSE;
        }
        pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
        sp_note_pwm_held(pe, attempted, src, who, before);
        le = safety_drv_permit_low_raw_held();
        sp_note_permit_held(le, src, 0u);
        if (pe != RT_EOK)
        {
            /* 关断本身失败: 同窗追加抑制 + 留 pending(仍在**本次动作那一代**),
             * **不做第二次 disable**(S1-R1 R04 合同)。 */
            sp_retain_held(code, events);
            sp.owner = SP_HELD_UNKNOWN;
            sp.trips++;
        }
        else if (le != RT_EOK)
        {
            /* CP4 K3/K4(§5.2/§6.2): 许可 LOW 裸写**单独**失败同样是业务失败 —— 上面
             * "撤销预约 + 输出影子清空"的那句话此刻并不成立(禁能请求根本没发出去),
             * 所以这条义务必须在**本次动作那一代**留下: 追加抑制 + 留 pending + 通道
             * 归"未知持有" + 发起者那一路的"仍未确认交回"重新置位。对外仍按 §6.2
             * 的优先序返回 le, raw 两个字段各自留档。 */
            sp_retain_held(code, events);
            sp.owner = SP_HELD_UNKNOWN;
            if (who == SP_DIAG) sp.diag_output_active   = RT_TRUE;
            else                sp.motor_output_active  = RT_TRUE;
        }
        sp_copy_proof_held(out);
        sp_end(l);
        if (pe != RT_EOK) return pe;
        if (le != RT_EOK) return le;
        return boundary;
    }

    /* 保留预约的单次关断(受控减速到零 / 初始化): 成功时不换 epoch, 结果记在当前代。 */
    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    if (pe != RT_EOK)
    {
        /* K3(§5.2)/K4(§6.3): 关断失败本身就是一条新失败 —— 先换代 + 留 pending,
         * 再把这一次**唯一**的 disable 与独立 LOW 记进那一代, 保证四个身份一致。 */
        if (!sp_advance_held()) boundary = -RT_EPERM;
        sp_retain_held(code, events);
        sp.last_trip_epoch = sp.epoch;
        sp_note_pwm_held(pe, attempted, src, who, before);
        le = safety_drv_permit_low_raw_held();
        sp_note_permit_held(le, src, 0u);
        sp.owner = SP_HELD_UNKNOWN;
        sp.trips++;
        sp_copy_proof_held(out);
        sp_end(l);
        (void)boundary;                 /* raw 另存(permit.write_rc / advance_failed), 不外传 */
        return pe;                        /* 对外 PWM 失败优先(§6.2) */
    }
    sp_note_pwm_held(pe, attempted, src, who, before);
    if (who == SP_MOTOR && before == SP_MOTOR)
    {
        sp.phase = SP_ARMED;             /* 受控减速到零: 保留预约与许可 */
    }
    sp_copy_proof_held(out);
    sp_end(l);
    return RT_EOK;
}

/* ---------------- 预约 / 阶段 / 释放 ---------------- */
/* R6(2026-10-23): 预约是"边界失败不得算成功"的典型点 —— advance 返回 FALSE(epoch 到顶)
 * 时保持 inhibit/exhausted 并返回错误, 不发出一个身份不明的 token。
 * 非法 owner 取值(既不是 MOTOR 也不是 DIAG)一律 -RT_EINVAL。 */
/* 预约的共用实现(caller 已屏蔽中断)。check_origin=TRUE 时**先**核对命令原身份
 * (CP4 K2: 入口采到的 epoch 仍是当前代), 失配就不产生预约、不产生任何硬件动作。
 * R6 的边界纪律保留: 饱和 => 不发出身份不明的 token, 且 inhibit/exhausted 保持。 */
static rt_err_t sp_reserve_impl_held(rt_uint32_t expected_epoch, rt_bool_t check_origin,
                                     sp_owner_t want, sp_token_t *out)
{
    rt_err_t e = -RT_EPERM;

    if (!sp_owner_valid(want)) return -RT_EINVAL;
    if (out == RT_NULL)        return -RT_EINVAL;
    if (check_origin && sp.epoch != expected_epoch)
    {
        /* 命令入口到预约之间发生了任何保护/恢复/别的预约: 这条**旧命令终止**。
         * 不重采当前数字续命, 也不发新 token(调用方不得重试成新身份)。 */
        sp.stale_dropped++;
        return -RT_EINTR;
    }
    if (!sp.inhibit && !sp.exhausted && sp.owner == SP_NONE)
    {
        if (sp.motor_stop_latched)                        e = -RT_EPERM; /* 未复核的关断失败 */
        else if (want == SP_DIAG && sp.diag_stop_latched) e = -RT_EPERM;
        else if (!sp_can_advance_held())                  e = -RT_EPERM; /* 饱和前预检 */
        else
        {
            if (!sp_advance_held())                       e = -RT_EPERM;
            else
            {
                sp.owner = want;
                sp.phase = SP_RESERVED;
                if (want == SP_DIAG) sp.diag_ever_reserved = RT_TRUE;
                out->owner = want; out->epoch = sp.epoch;
                e = RT_EOK;
            }
        }
    }
    return e;
}

rt_err_t sp_reserve(sp_owner_t want, sp_token_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = sp_reserve_impl_held(0u, RT_FALSE, want, out);

    sp_end(l);
    return e;
}

rt_err_t sp_reserve_for(rt_uint32_t expected_epoch, sp_owner_t want, sp_token_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = sp_reserve_impl_held(expected_epoch, RT_TRUE, want, out);

    sp_end(l);
    return e;
}

rt_err_t sp_release(const sp_token_t *t)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp_same_owner_epoch_held(t) && sp.phase == SP_RESERVED)
    {
        if (!sp_can_advance_held())                 /* 饱和: 不换 epoch 就不发新身份 */
        {
            sp.inhibit = RT_TRUE;
            sp_end(l);
            return -RT_EPERM;
        }
        sp.owner = SP_NONE;
        if (t->owner == SP_DIAG) sp.diag_output_active = RT_FALSE;
        else                     sp.motor_output_active = RT_FALSE;
        /* K4(§6.4): 换代边界失败**不能**算成功 —— 保持抑制并返回非 EOK。 */
        if (!sp_advance_held())
        {
            sp.inhibit = RT_TRUE;
            sp_end(l);
            return -RT_EPERM;
        }
        e = RT_EOK;
    }
    else
    {
        sp.stale_dropped++;                    /* 仍在输出/身份失配: 不释放新 owner */
        e = -RT_EPERM;
    }
    sp_end(l);
    return e;
}

/* ---------------- 门内提交(资格判定与真实写同窗) ---------------- */
rt_err_t sp_enter_token(const sp_token_t *t, rt_base_t *saved)
{
    rt_base_t l = sp_win();

    if (!sp_token_ok_held(t))
    {
        sp.stale_dropped++;
        sp_end(l);
        return -RT_EPERM;
    }
    if (saved != RT_NULL) *saved = l;           /* 调用方仍在窗口内 */
    return RT_EOK;
}

void sp_leave_token(rt_base_t saved)
{
    sp_end(saved);
}

/* HIGH 的资格 = token 有效 + owner 是 Motor + 编译期硬件许可(R5)。
 * 注意这里**不**把 phase 抬到 ARMED: 裸写成功只是"撤销了禁能请求", 真正的 ARMED
 * 要等 GPIO 回读 + ENN 反馈确认之后由 sp_arm_commit*() 提交(R5)。 */
rt_err_t sp_commit_permit_high(const sp_token_t *t, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (!sp_token_ok_held(t))
    {
        sp.stale_dropped++;
        sp_end(l);
        return -RT_EPERM;
    }
    if (!sp_high_permit_ok_held(t))
    {
        sp.stale_dropped++;
        sp_end(l);
        return -RT_EPERM;                      /* 一个寄存器写都不发 */
    }
    e = safety_drv_permit_high_raw_held();      /* 同窗的一次 SET 寄存器写 */
    /* K6.2(§8.2): 记下"授权这次 HIGH 的那份配置有效性摘要代次" —— armed 的最终提交
     * 要核对它没有变化(期间被作废或重新配置过都不许把 phase 抬到 ARMED)。 */
    sp.permit.cfg_view_gen  = tmc2209_config_view_generation();
    sp.permit.written       = (e == RT_EOK) ? RT_TRUE : RT_FALSE;
    sp.permit.request_level = 1u;               /* HIGH 请求 */
    sp.permit.write_rc      = e;
    sp.permit.src           = MOT_STOP_SRC_NONE; /* HIGH 不是关断动作, 来源位只用于 LOW */
    sp.permit.write_epoch   = sp.epoch;
    sp.permit.confirmed     = RT_FALSE;
    sp.permit.confirmed_level = SP_PERMIT_LEVEL_NONE;
    sp_copy_proof_held(out);
    sp_end(l);
    return e;
}

/* R5: 确认过的 HIGH + 同一 token 的 armed 提交成功 => 才进入 SP_ARMED。
 * 与 mot.armed=1 落在同一个短窗口里(sp_arm_commit_held), 因此不存在
 * "门内判定、门外写软件状态"的空隙。 */
rt_err_t sp_arm_commit_held(const sp_token_t *t)
{
    /* CP4 §8.2: ARMED 不再是"谁拿到 token 就能抬"的一次字段赋值。四条证据必须同时
     * 属于**这个 token 那一代**: ①HIGH 裸写请求成功 ②焊盘回读确认成功且针对 HIGH
     * ③所需 ENN 反馈复核成功且针对同一次 HIGH 请求 ④授权这次 HIGH 的配置有效性摘要
     * 代次仍未变化。任一不满足都拒绝升级 —— 手动改 phase 建立不了 ARMED/RUNNING
     * (原 sp_phase_set 已删除)。 */
    if (!sp_token_ok_held(t)) { sp.stale_dropped++; return -RT_EPERM; }
    if (t->owner != SP_MOTOR) { sp.stale_dropped++; return -RT_EPERM; }
    if (!(sp.permit.written && sp.permit.request_level == 1u &&
          sp.permit.write_rc == RT_EOK && sp.permit.write_epoch == t->epoch &&
          sp.permit.confirmed && sp.permit.confirm_rc == RT_EOK &&
          sp.permit.confirm_epoch == t->epoch &&
          sp.permit.confirmed_level == 1u))
    {
        sp.stale_dropped++;
        return -RT_EPERM;
    }
    if (!(sp.permit.enn_ok && sp.permit.enn_rc == RT_EOK &&
          sp.permit.enn_level == 1u && sp.permit.enn_epoch == t->epoch))
    {
        sp.stale_dropped++;
        return -RT_EPERM;
    }
    if (sp.permit.cfg_view_gen != tmc2209_config_view_generation())
    {
        sp.stale_dropped++;
        return -RT_EPERM;
    }
    if (!sp_high_permit_ok_held(t)) { sp.stale_dropped++; return -RT_EPERM; }
    sp.phase = SP_ARMED;
    return RT_EOK;
}

rt_err_t sp_arm_commit(const sp_token_t *t)
{
    rt_base_t l = sp_win();
    rt_err_t  e = sp_arm_commit_held(t);

    sp_end(l);
    return e;
}

/* RUN 的资格 = token 有效 + (Motor: 硬件许可 + 已 ARMED) / (诊断: 原双宏合同)。
 * R5 之后再记 RUNNING, 且**只有真实 rt_pwm_enable 成功**才记输出已请求。 */
rt_err_t sp_pwm_start_held(const sp_token_t *t)
{
    rt_err_t e;

    /* 调用方已屏蔽中断并完成窗口进入(sp_enter_token), 这里不再开关窗口。 */
    if (!sp_token_ok_held(t)) { sp.stale_dropped++; return -RT_EPERM; }
    if (sp_pwm == RT_NULL)
    {
        sp_note_pwm_held(-RT_ENOSYS, RT_FALSE, MOT_STOP_SRC_NONE, t->owner, sp.owner);
        sp.owner = SP_HELD_UNKNOWN;
        return -RT_ENOSYS;
    }
    if (!sp_run_permit_ok_held(t))
    {
        sp.stale_dropped++;
        return -RT_EPERM;                       /* 未过许可 => 不碰时间基 */
    }
    e = rt_pwm_enable(sp_pwm, sp_pwm_ch);
    if (e == RT_EOK)
    {
        sp.phase         = SP_RUNNING;
        sp.pwm.attempted = RT_TRUE;
        sp.pwm.result    = MOT_STOP_OUTPUT_REQUESTED;
        sp.pwm.raw_rc    = RT_EOK;
        sp.pwm.src       = MOT_STOP_SRC_NONE;
        sp.pwm.epoch     = sp.epoch;
        /* K6.1: 发起者那一路的"我在输出中"同样要留下自己的证据, 之后由这一路的
         * checked 关断覆盖 —— Motor 后来的运行/关断不得改写诊断已经交回过的历史。 */
        if (t->owner == SP_DIAG)
        {
            sp.diag_pwm       = sp.pwm;
            sp.diag_rec_valid = RT_TRUE;
        }
        /* 记在**发起者自己**那一路的"输出仍持有"上: 之后 Motor 的运行不会污染
         * 已经归还过的诊断历史(R6)。 */
        if (t->owner == SP_DIAG) sp.diag_output_active  = RT_TRUE;
        else                     sp.motor_output_active = RT_TRUE;
    }
    return e;
}

rt_err_t sp_commit_pwm_start(const sp_token_t *t, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = sp_pwm_start_held(t);

    sp_copy_proof_held(out);
    sp_end(l);
    return e;
}

rt_err_t sp_token_current(sp_owner_t want, sp_token_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = -RT_EPERM;

    /* out 允许为 NULL: 那种调用只求"这个 owner 现在还有没有有效身份", 不取值。 */
    if (want != SP_NONE && sp.owner == want && !sp.inhibit && !sp.exhausted)
    {
        if (out != RT_NULL)
        {
            out->owner = want;
            out->epoch = sp.epoch;
        }
        e = RT_EOK;
    }
    sp_end(l);
    return e;
}

void sp_mirror_motor(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                     rt_bool_t *latched)
{
    rt_base_t l = sp_win();

    if (res) *res = sp.pwm.result;
    if (src) *src = sp.pwm.src;
    if (rc)  *rc  = sp.pwm.raw_rc;
    if (latched) *latched = sp.motor_stop_latched;
    sp_end(l);
}

/* R1: 确认提交必须带身份 —— 请求电平相符 + epoch 仍是那一代才写 confirmed 影子。
 * 晚到的旧 LOW/旧 HIGH 确认: 只计数, 不改新记录, 不清别人 pending, 也不反向写 LOW。
 * (原 sp_note_low_confirmed() 的"任何成功确认都置 pending"正是
 *  normal-disarm-pending / reconcile-requeues-itself 两个探针的根因, 已删除。) */
rt_err_t sp_note_permit_confirmed_for(rt_uint32_t expected_epoch,
                                      rt_uint8_t requested_level, rt_err_t rc)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp.epoch != expected_epoch || sp.permit.request_level != requested_level ||
        sp.permit.write_epoch != expected_epoch)
    {
        sp.stale_dropped++;
        e = -RT_EINTR;
    }
    else
    {
        sp.permit.confirmed       = (rc == RT_EOK) ? RT_TRUE : RT_FALSE;
        sp.permit.confirm_rc      = rc;
        sp.permit.confirm_epoch   = sp.epoch;
        sp.permit.confirmed_level = requested_level;
        e = RT_EOK;
    }
    sp_end(l);
    return e;
}

/* token 版: 请求电平取当前 canonical 记录的那一次(调用方确认的就是它)。 */
rt_err_t sp_note_permit_confirmed(const sp_token_t *t, rt_uint8_t requested_level,
                                  rt_err_t rc)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (!sp_token_ok_held(t) || sp.permit.request_level != requested_level)
    {
        sp.stale_dropped++;
        e = -RT_EINTR;
    }
    else
    {
        sp.permit.confirmed       = (rc == RT_EOK) ? RT_TRUE : RT_FALSE;
        sp.permit.confirm_rc      = rc;
        sp.permit.confirm_epoch   = sp.epoch;
        sp.permit.confirmed_level = requested_level;
        e = RT_EOK;
    }
    sp_end(l);
    return e;
}

/* CP4 K6.2(§8.2): ENN 反馈复核的成功只有**绑到那一代的那次请求**才作数。
 * 请求电平与写请求的代次都核对; 失配只计数、不写新记录(与许可确认同一纪律)。 */
rt_err_t sp_note_enn_confirmed_for(rt_uint32_t expected_epoch, rt_uint8_t requested_level,
                                   rt_err_t rc)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp.epoch != expected_epoch || sp.permit.request_level != requested_level ||
        sp.permit.write_epoch != expected_epoch)
    {
        sp.stale_dropped++;
        e = -RT_EINTR;
    }
    else
    {
        sp.permit.enn_ok    = (rc == RT_EOK) ? RT_TRUE : RT_FALSE;
        sp.permit.enn_rc    = rc;
        sp.permit.enn_epoch = sp.epoch;
        sp.permit.enn_level = requested_level;
        e = RT_EOK;
    }
    sp_end(l);
    return e;
}

/* ---------------- pending: peek / gate / ack (R1) ---------------- */
/* 只读: 不把义务清掉。锁取失败时调用方丢弃本次整理, pending 仍在, 下次还能拾起。 */
rt_bool_t sp_pending_peek(sp_proof_t *proof, rt_uint32_t *code, rt_uint32_t *events)
{
    rt_base_t l;
    rt_bool_t had;

    l = sp_win();
    had = sp.metadata_pending;
    if (had)
    {
        if (code)   *code   = sp.first_fault_code;
        if (events) *events = sp.reason_events;
        sp_copy_proof_held(proof);
    }
    sp_end(l);
    return had;
}

rt_bool_t sp_op_current_held(rt_uint32_t epoch)
{
    return (sp.epoch == epoch) ? RT_TRUE : RT_FALSE;
}

rt_bool_t sp_op_current(rt_uint32_t epoch)
{
    rt_base_t l;
    rt_bool_t v;

    l = sp_win();
    v = sp_op_current_held(epoch);
    sp_end(l);
    return v;
}

rt_bool_t sp_reconcile_gate_held(rt_uint32_t epoch)
{
    return (sp.metadata_pending && sp.inhibit && sp.epoch == epoch &&
            sp.pending_epoch == epoch) ? RT_TRUE : RT_FALSE;
}

/* 只有匹配当前故障那一代才 ack: 较新请求留下的 pending 不会被旧的 ack 吞掉。 */
rt_bool_t sp_pending_ack_held(rt_uint32_t expected_epoch)
{
    if (!sp_reconcile_gate_held(expected_epoch))
    {
        sp.stale_dropped++;
        return RT_FALSE;
    }
    sp.metadata_pending = RT_FALSE;
    sp.pending_epoch    = 0u;
    return RT_TRUE;
}

/* ---------------- 观测 ---------------- */
void sp_get(sp_snapshot_t *out)
{
    rt_base_t l;

    if (out == RT_NULL) return;
    l = sp_win();
    out->epoch                  = sp.epoch;
    out->owner                  = sp.owner;
    out->phase                  = sp.phase;
    out->inhibit                = sp.inhibit;
    out->exhausted              = sp.exhausted;
    out->motor_stop_latched     = sp.motor_stop_latched;
    out->diag_stop_latched      = sp.diag_stop_latched;
    out->motor_output_active    = sp.motor_output_active;
    out->diag_output_active     = sp.diag_output_active;
    out->diag_ever_reserved     = sp.diag_ever_reserved;
    out->metadata_pending       = sp.metadata_pending;
    out->pending_epoch          = sp.pending_epoch;
    out->first_fault_code       = sp.first_fault_code;
    out->reason_events          = sp.reason_events;
    out->trips                  = sp.trips;
    out->merged                 = sp.merged;
    out->stale_dropped          = sp.stale_dropped;
    out->advance_failed         = sp.advance_failed;
    out->pwm                    = sp.pwm;
    out->diag_pwm               = sp.diag_pwm;      /* K6.1: 分 owner 的诊断证据 */
    out->last_trip_epoch        = sp.last_trip_epoch;
    out->permit                 = sp.permit;
    out->first_unrecovered      = sp.first_unrecovered;
    out->first_unrecovered_valid = sp.first_unrecovered_valid;
    out->first_permit_unrecovered = sp.first_permit_unrecovered;
    out->first_permit_valid       = sp.first_permit_valid;
    sp_end(l);
}

rt_bool_t sp_motor_stop_latched(void)
{
    rt_base_t l; rt_bool_t v;
    l = sp_win(); v = sp.motor_stop_latched; sp_end(l); return v;
}

rt_bool_t sp_diag_stop_latched(void)
{
    rt_base_t l; rt_bool_t v;
    l = sp_win(); v = sp.diag_stop_latched; sp_end(l); return v;
}

rt_bool_t sp_diag_ever_reserved(void)
{
    rt_base_t l; rt_bool_t v;
    l = sp_win(); v = sp.diag_ever_reserved; sp_end(l); return v;
}

void sp_view_motor_held(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                         rt_bool_t *latched)
{
    if (res) *res = sp.pwm.result;
    if (src) *src = sp.pwm.src;
    if (rc)  *rc  = sp.pwm.raw_rc;
    if (latched) *latched = sp.motor_stop_latched;
}

void sp_view_motor(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                   rt_bool_t *latched)
{
    rt_base_t l = sp_win();

    sp_view_motor_held(res, src, rc, latched);
    sp_end(l);
}

/* 诊断视图(K6.1, R6 的加强版): 一律读**诊断那一路自己的**关断记录(diag_pwm), 不再
 * 借用模块当前的 sp.pwm —— Motor 新一轮运行/关断改的是模块事实与 Motor 历史, 不能
 * 把诊断"已经真实归还后被接受"的证据改成 OUTPUT_REQUESTED(审查探针
 * returned-diag-off-view-diverges 的根因)。与 sp_diag_off_confirmed_held /
 * sp_diag_output_active / proof 取同一组字段, 四个视图不再互相矛盾。
 * 从未占用过通道, 或占用过但从未产生过需要确认的关断 => NOT_ATTEMPTED(不伪造)。 */
void sp_view_diag_held(mot_stop_result_t *res, rt_err_t *rc, rt_bool_t *latched)
{
    if (!sp.diag_rec_valid)
    {
        if (res) *res = MOT_STOP_NOT_ATTEMPTED;
        if (rc)  *rc  = RT_EOK;
    }
    else
    {
        if (res) *res = sp.diag_pwm.result;
        if (rc)  *rc  = sp.diag_pwm.raw_rc;
    }
    if (latched) *latched = sp.diag_stop_latched;
}

void sp_view_diag(mot_stop_result_t *res, rt_err_t *rc, rt_bool_t *latched)
{
    rt_base_t l = sp_win();

    sp_view_diag_held(res, rc, latched);
    sp_end(l);
}

rt_bool_t sp_diag_off_confirmed(void)
{
    rt_base_t l;
    rt_bool_t v;

    l = sp_win(); v = sp_diag_off_confirmed_held(); sp_end(l);
    return v;
}

/* 保守判据(R6, 与 sp_diag_off_confirmed_held/sp_view_diag_held 同一组字段):
 * 诊断这一路自己"仍持有/未确认交回"、当前 owner 是诊断、状态未知,
 * 或带着未复核的历史锁存 —— 任一成立都不许 Motor 接管。
 * Motor 后来 start 的新运行**不**算在这里(那已经交回过)。 */
rt_bool_t sp_diag_output_active_held(void)
{
    return sp.diag_output_active;
}

rt_bool_t sp_diag_output_active(void)
{
    rt_base_t l = sp_win();
    rt_bool_t v;

    v = (sp.diag_output_active || sp.owner == SP_DIAG ||
         (sp.owner == SP_HELD_UNKNOWN && (sp.diag_ever_reserved || sp.diag_stop_latched)) ||
         sp.diag_stop_latched) ? RT_TRUE : RT_FALSE;
    sp_end(l);
    return v;
}

rt_bool_t sp_motor_output_active(void)
{
    rt_base_t l = sp_win();
    rt_bool_t v;

    v = (sp.motor_output_active || sp.owner == SP_MOTOR ||
         sp.owner == SP_HELD_UNKNOWN || sp.motor_stop_latched) ? RT_TRUE : RT_FALSE;
    sp_end(l);
    return v;
}

/* 原 sp_take_pending()(先清后整理)已删除: 取线程锁失败时唯一义务会被一起丢掉。
 * 改成 sp_pending_peek() + 提交窗口内 sp_pending_ack_held() 两步。 */

/* ---------------- 人工恢复 ---------------- */
rt_bool_t sp_recovery_start(rt_uint32_t *ticket)
{
    rt_base_t l = sp_win();
    rt_bool_t ok = RT_FALSE;

    if (ticket != RT_NULL && sp.inhibit && !sp.exhausted)
    {
        *ticket = sp.epoch;
        ok = RT_TRUE;
    }
    sp_end(l);
    return ok;
}

/* 复核: 先确认票据仍有效, 再做一次真实关闭 + 许可 LOW 裸写并记账。
 * 成功**不**换 epoch(否则自废票据), 失败才换并保持抑制。R1: 不在此置 pending ——
 * 恢复本身不是一条新的故障义务。 */
rt_err_t sp_recovery_probe_off(rt_uint32_t ticket, sp_proof_t *out)
{
    rt_base_t  l = sp_win();
    sp_owner_t before = sp.owner;
    rt_bool_t  adv = RT_TRUE;
    rt_err_t   pe, le;

    if (sp.epoch != ticket || !sp.inhibit)
    {
        sp.stale_dropped++;
        sp_copy_proof_held(out);                    /* 把当前事实带回, 调用方照实报告 */
        sp_end(l);
        return -RT_EINTR;                           /* 期间已有新请求: 本次恢复自然失效 */
    }
    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    sp_note_pwm_held(pe, sp_pwm != RT_NULL ? RT_TRUE : RT_FALSE,
                     MOT_STOP_SRC_DISARM, before, before);
    /* CP4 K6.1: 这一次关断发生在**共享的 epwm1 时间基**上 —— 诊断只要曾经占用过该
     * 通道, 这次真实关断就是它的归还证据(与 fault_reset 里"两路看同一次
     * rt_pwm_disable"的既有说明一致, 不是伪造一次 API 调用)。原来 actor 取
     * before(此时多半是 HELD_UNKNOWN), 于是诊断那一路停在**旧的失败**记录上,
     * 人工恢复在第四步就被自己挡住 -> 关断失败锁存永远清不掉。 */
    if (sp.diag_ever_reserved)
    {
        sp.diag_pwm       = sp.pwm;
        sp.diag_rec_valid = RT_TRUE;
    }
    le = safety_drv_permit_low_raw_held();
    sp_note_permit_held(le, MOT_STOP_SRC_DISARM, 0u);
    /* CP4 K4(§6.3): 原来的链把 le-only 失败漏掉了 —— 第一个条件(pe 成功且不是诊断
     * 持有)先命中并把 owner 清成 SP_NONE, `else if (le != RT_EOK)` 的换代**永远执行
     * 不到**(夹具 C12b 实测 permit.write_rc=-12 而 epoch 不动、advance_failed=0),
     * 于是这张票据在一次根本没发出去的禁能请求上仍然"有效"。改成先分别判两个 raw。 */
    if (pe != RT_EOK)
    {
        sp.owner = SP_HELD_UNKNOWN;
        adv = sp_advance_held();                 /* 复核失败 = 新失败: 换 epoch 并保持抑制 */
        sp_retain_held(0u, 0u);
        sp.last_trip_epoch = sp.epoch;
    }
    else if (le != RT_EOK)
    {
        /* CP4 K4(§6.3): 许可 LOW 裸写**单独**失败同样是新失败 —— 换代 + 保持抑制,
         * 使这张票据自然失效; 通道归属仍按"未知持有"(禁能请求没发出去, 不能说已交回),
         * 诊断持有则不动它的身份(共享模块的关断照做, 但不替它归还)。 */
        if (before != SP_DIAG) sp.owner = SP_HELD_UNKNOWN;
        adv = sp_advance_held();
        sp_retain_held(0u, 0u);
        sp.last_trip_epoch = sp.epoch;
    }
    else if (before != SP_DIAG) sp.owner = SP_NONE;   /* 两路 raw 都成功才撤销预约 */
    sp_copy_proof_held(out);
    sp_end(l);
    /* §6.2 的优先序: PWM 失败 > 许可失败 > 换代/边界失败; raw 各自留档不合并。 */
    if (pe != RT_EOK) return pe;
    if (le != RT_EOK) return le;
    return adv ? RT_EOK : -RT_EPERM;
}

/* CP4 K4(§6.3): 恢复复核**之后**在窗口外发现的问题(LOW 回读未确认、身份化确认被拒、
 * ENN 再确认失败)同样是新失败 —— 在票据仍成立的那一代换 epoch + 保持抑制 + 留 pending,
 * 使旧票据自然失效。票据已被更新的一代取代时返回 -RT_EINTR 且**一个字段都不改**
 * (不给过期动作留补写新记录的机会)。
 * 返回: EOK=已按新失败换代; -RT_EPERM=撞顶(抑制/exhausted 保持); -RT_EINTR=过期。 */
rt_err_t sp_recovery_note_failure(rt_uint32_t ticket, rt_uint32_t code,
                                  rt_uint32_t events, sp_proof_t *out)
{
    rt_base_t l = sp_win();

    if (sp.epoch != ticket || !sp.inhibit)
    {
        sp.stale_dropped++;
        sp_copy_proof_held(out);
        if (out != RT_NULL) out->valid = RT_TRUE;
        sp_end(l);
        return -RT_EINTR;
    }
    /* 首错与历史锁存都不在这里清(§6.5): 换代之后的这一代仍然带着同一条待整理义务。 */
    if (!sp_advance_held())
    {
        sp.inhibit = RT_TRUE;
        sp_copy_proof_held(out);
        sp_end(l);
        return -RT_EPERM;
    }
    sp_retain_held(code, events);
    sp.last_trip_epoch = sp.epoch;
    sp_copy_proof_held(out);
    sp_end(l);
    return RT_EOK;
}

/* 本次复核票据有效性的**唯一**判据(窗口内与窗口外共用同一份逻辑)。
 * ⚠ 这里**不**要求历史锁存已经清了 —— 任务书 §7 第 6 步正是要在最终提交里清除
 *   "已由本次复核覆盖的历史 S1 锁存"; 若把 !latched 当必要条件, 恢复过一次的板子
 *   就永远进不来(S2-C13 的反例)。真正的判据是**证据属于本票据那一代**(R6 加强):
 *     epoch==ticket 且 inhibit 仍置位 且 未饱和 且 **计数还能前进**
 *     且 本次实际 PWM 关断被驱动接受(attempted / result==API_ACCEPTED / epoch==ticket)
 *     且 本次许可 **LOW 裸写请求成功** 且 **同一代的回读确认也成功**
 *   任何新故障/新失败都会推进 epoch, 因此"无较新失败"由 epoch 相等保证。
 * can_advance 预检放在这里(而不是提交后再看), 避免"清了半个历史才发现饱和"。 */
rt_bool_t sp_recovery_valid_held(rt_uint32_t ticket, sp_proof_t *proof)
{
    rt_bool_t ok;

    ok = (sp.epoch == ticket && sp.inhibit && !sp.exhausted &&
          sp_can_advance_held() &&
          sp.pwm.attempted && sp.pwm.result == MOT_STOP_API_ACCEPTED &&
          sp.pwm.epoch == ticket &&
          sp.permit.written && sp.permit.write_rc == RT_EOK &&
          sp.permit.request_level == 0u && sp.permit.write_epoch == ticket &&
          sp.permit.confirmed && sp.permit.confirm_rc == RT_EOK &&
          sp.permit.confirm_epoch == ticket)
           ? RT_TRUE : RT_FALSE;
    if (proof != RT_NULL && ok) sp_copy_proof_held(proof);
    return ok;
}

rt_bool_t sp_recovery_valid(rt_uint32_t ticket, sp_proof_t *proof)
{
    rt_base_t l;
    rt_bool_t ok;

    l = sp_win();
    ok = sp_recovery_valid_held(ticket, proof);
    sp_end(l);
    return ok;
}

void sp_clear_latches_held(void)
{
    sp.motor_stop_latched = RT_FALSE;
    sp.diag_stop_latched  = RT_FALSE;
}

/* R6(2026-10-23)恢复最终提交:
 * 1) 先 sp_recovery_valid_held 预检(含"计数还能前进"), 不满足就整笔不动 —— 之前是
 *    先清字段、最后 advance 才可能发现饱和, 又把 inhibit 无条件清掉, 恢复协议自相矛盾。
 * 2) 成功后**保留**本次真实的 API_ACCEPTED 关断证据与各 owner 的"曾经使用过"历史
 *    (diag_ever_reserved 不再被抹成"从未使用"), 只清**已被本次复核覆盖**的失败锁存
 *    与"输出仍持有"标志。这样第 2/7 门可以重新放行, 而历史仍然是历史。
 * 3) 抑制在**最后一步**才解除, 且解除前已确认这次换代成功; advance 返回 FALSE 时
 *    保持 inhibit/exhausted 并返回 FALSE(fault_reset 因此拒绝写 MANUAL_CLEAR)。 */
rt_bool_t sp_finish_recovery_held(rt_uint32_t ticket)
{
    if (!sp_recovery_valid_held(ticket, RT_NULL))     /* 较新的请求赢 / 计数到顶 */
    {
        if (sp.epoch == ticket) sp.stale_dropped++;
        return RT_FALSE;
    }

    /* K6.1(§8.1): 本次复核的那一次 rt_pwm_disable 发生在**共享的 epwm1 时间基**上,
     * 它同时是两路的归还证据 —— 诊断那一路记过的证据因此更新为这一代的真实结果,
     * 而不是被"硬改成 TRUE"。从未占用过通道的诊断保持 NOT_ATTEMPTED(不伪造)。 */
    if (sp.diag_ever_reserved)
    {
        sp.diag_pwm       = sp.pwm;
        sp.diag_rec_valid = RT_TRUE;
    }
    sp_clear_latches_held();                     /* 只清本次复核已覆盖的历史锁存 */
    sp.motor_output_active     = RT_FALSE;       /* 两条 owner 路都已确认交回 */
    sp.diag_output_active      = RT_FALSE;
    sp.owner                   = SP_NONE;
    sp.phase                   = SP_RESERVED;
    sp.first_fault_code        = 0u;
    sp.reason_events           = 0u;
    sp.metadata_pending        = RT_FALSE;
    sp.pending_epoch           = 0u;
    sp.first_unrecovered_valid = RT_FALSE;       /* 首错证据随本次成功复核失效 */
    sp.first_permit_valid      = RT_FALSE;
    if (!sp_advance_held())                      /* 计数边界失败: 恢复不成立 */
    {
        sp.inhibit = RT_TRUE;                    /* 饱和分支照旧保持抑制 */
        return RT_FALSE;
    }
    sp.inhibit              = RT_FALSE;          /* 最后一步才解除抑制 */
    return RT_TRUE;
}

/* ---------------- 名字 ---------------- */
const char *sp_owner_name(sp_owner_t o)
{
    switch (o)
    {
    case SP_NONE:         return "none";
    case SP_MOTOR:        return "motor";
    case SP_DIAG:         return "diag";
    case SP_HELD_UNKNOWN: return "held-unknown(输出状态未确认)";
    default:              return "?";
    }
}

const char *sp_phase_name(sp_phase_t p)
{
    switch (p)
    {
    case SP_RESERVED: return "reserved";
    case SP_ARMED:    return "armed(许可已请求)";
    case SP_RUNNING:  return "running(时间基已启动)";
    default:          return "?";
    }
}
