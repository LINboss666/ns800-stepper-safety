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

/* R5(2026-10-23): 新增的生产接口(HIGH / RUN)自己守编译期许可, 不依赖"每个调用者
 * 永远不出错"。这两个宏就是目标工程里那份公开配置(motor.h / project_board.h),
 * 主机夹具要么用真实头, 要么在命令行显式 -D 覆盖 —— 两条构建走同一分支逻辑。 */
#include "motor.h"
#include "project_board.h"

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
static rt_base_t sp_win(void)  { return rt_hw_interrupt_disable(); }
static void      sp_end(rt_base_t l) { SP_BARRIER(); rt_hw_interrupt_enable(l); }

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

/* R5: HIGH 是"让驱动器得电"的动作 => token 资格之外还要过编译期硬件许可,
 * 且**只有 Motor** 能请求 HIGH(诊断借用只允许出 STEP, 不许给驱动供电)。 */
static rt_bool_t sp_high_permit_ok_held(const sp_token_t *t)
{
    if (!sp_token_ok_held(t))                      return RT_FALSE;
    if (t->owner != SP_MOTOR)                      return RT_FALSE;
    if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED)     return RT_FALSE;
    return RT_TRUE;
}

/* R5: RUN(启动共享时间基)的许可 —— Motor 要硬件许可 + 本预约已到 ARMED;
 * 诊断保留原双宏合同(硬件许可 或 操作员已确认隔离环境)。 */
static rt_bool_t sp_run_permit_ok_held(const sp_token_t *t)
{
    if (!sp_token_ok_held(t))                      return RT_FALSE;
    if (t->owner == SP_MOTOR)
    {
        if (!MOTOR_HARDWARE_ENABLE_PATH_VALIDATED) return RT_FALSE;
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
                             sp_owner_t before);
static void sp_note_permit_held(rt_err_t le, rt_uint8_t src, rt_uint8_t level);
static void sp_copy_proof_held(sp_proof_t *out);
rt_bool_t   sp_diag_off_confirmed_held(void);
/* 记录一次**实际**关断尝试。R6: 除了模块的当前输出事实, 还要分 owner 维护各自的
 * "我这一路是否仍未确认交回"与历史失败锁存 —— Motor 新一轮运行不得改写诊断已经
 * 归还过的历史视图(反之亦然)。 */
static void sp_note_pwm_held(rt_err_t pe, rt_bool_t attempted, rt_uint8_t src,
                             sp_owner_t before)
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
    out->diag_attempted = sp.pwm.attempted;
    out->first_fault_code = sp.first_fault_code;
    out->reason_events    = sp.reason_events;
    out->motor_off_ok   = (sp.pwm.result == MOT_STOP_API_ACCEPTED &&
                           !sp.motor_stop_latched) ? RT_TRUE : RT_FALSE;
    out->motor_stop_latched = sp.motor_stop_latched;
    out->diag_off_ok    = (sp_diag_off_confirmed_held()) ? RT_TRUE : RT_FALSE;
}

/* 诊断视图的单一判据(窗口内版本): ①带着未复核的历史锁存 => 无效;
 * ②从未占用过该通道且现在也不是诊断持有 => 有效(不伪造一次成功);
 * ③占用过 => 必须"最近一次实际关断被驱动接受"且诊断这一路已确认交回。 */
rt_bool_t sp_diag_off_confirmed_held(void)
{
    if (sp.diag_stop_latched)                        return RT_FALSE;
    if (sp.owner == SP_DIAG || sp.diag_output_active) return RT_FALSE;
    if (!sp.diag_ever_reserved)                      return RT_TRUE;
    return (sp.pwm.result == MOT_STOP_API_ACCEPTED) ? RT_TRUE : RT_FALSE;
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

    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    sp_note_pwm_held(pe, sp_pwm != RT_NULL ? RT_TRUE : RT_FALSE, src, before);

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
 * 而不是拿着自己的旧数字瞎写; 同时它仍然**不做**第二轮关闭。 */
rt_err_t sp_trip_for_operation(rt_uint32_t expected_epoch, rt_uint32_t code,
                               rt_uint32_t events, rt_uint8_t src, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp.epoch != expected_epoch)
    {
        /* 同一操作里已有别的层做过保护/已有更新的请求 => 本次不做第二轮关闭 */
        sp.stale_dropped++;
        sp_copy_proof_held(out);
        if (out != RT_NULL) out->valid = RT_TRUE;   /* 这是"当前事实", 不是本次动作 */
        sp_end(l);
        return -RT_EINTR;
    }
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

/* ---------------- 关断尝试(ramp/apply/disarm/pwm_test 共用一条路径) ---------------- */
rt_err_t sp_off_attempt(sp_owner_t who, rt_uint8_t src, rt_uint32_t code,
                        rt_uint32_t events, int release_on_ok, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    sp_owner_t before = sp.owner;
    rt_err_t  pe, le;

    if (sp_permit_pin < 0) sp_permit_pin = safety_drv_permit_pin();

    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    sp_note_pwm_held(pe, sp_pwm != RT_NULL ? RT_TRUE : RT_FALSE, src,
                     (before == SP_HELD_UNKNOWN) ? who : before);

    if (pe != RT_EOK)
    {
        /* 同窗追加: 抑制 + 换 epoch + 许可 LOW; **不做第二次 disable**(S1-R1 R04 合同) */
        sp_advance_held();
        sp_retain_held(code, events);
        le = safety_drv_permit_low_raw_held();
        sp_note_permit_held(le, src, 0u);
        sp.owner = SP_HELD_UNKNOWN;
        sp.trips++;
        sp_copy_proof_held(out);
        sp_end(l);
        return pe;
    }

    if (release_on_ok)
    {
        le = safety_drv_permit_low_raw_held();
        sp_note_permit_held(le, src, 0u);
        /* 只有本来就是这个 owner(或已经无人认领/状态未知)时才撤销预约。
         * 别的 owner 还在持有的时候, 一次 disarm/pwm_test stop 无权把它的身份清掉:
         * 那会让它回来时发现自己"没有输出记录"却仍在输出。共享模块的这次真实关断
         * 仍然照做(朝安全方向), 只是不换 epoch、不动别人的 owner。 */
        if (before == who || before == SP_NONE || before == SP_HELD_UNKNOWN)
        {
            sp.owner = SP_NONE;
            sp.phase = SP_RESERVED;
            if (who == SP_DIAG) sp.diag_output_active = RT_FALSE;
            else                sp.motor_output_active = RT_FALSE;
            sp_advance_held();           /* 正式释放换 epoch: 旧 token 一律失效 */
        }
    }
    else if (who == SP_MOTOR && before == SP_MOTOR)
    {
        sp.phase = SP_ARMED;             /* 受控减速到零: 保留预约与许可 */
    }
    sp_copy_proof_held(out);
    sp_end(l);
    return (pe == RT_EOK) ? RT_EOK : pe;
}

/* ---------------- 预约 / 阶段 / 释放 ---------------- */
/* R6(2026-10-23): 预约是"边界失败不得算成功"的典型点 —— advance 返回 FALSE(epoch 到顶)
 * 时保持 inhibit/exhausted 并返回错误, 不发出一个身份不明的 token。
 * 非法 owner 取值(既不是 MOTOR 也不是 DIAG)一律 -RT_EINVAL。 */
rt_err_t sp_reserve(sp_owner_t want, sp_token_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = -RT_EPERM;

    if (!sp_owner_valid(want))
    {
        sp_end(l);
        return -RT_EINVAL;
    }
    if (out != RT_NULL && !sp.inhibit && !sp.exhausted && sp.owner == SP_NONE)
    {
        if (sp.motor_stop_latched)                        e = -RT_EPERM; /* 未复核的关断失败 */
        else if (want == SP_DIAG && sp.diag_stop_latched) e = -RT_EPERM;
        else if (!sp_can_advance_held())                  e = -RT_EPERM; /* 饱和前预检 */
        else
        {
            if (!sp_advance_held())                      e = -RT_EPERM;
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
    sp_end(l);
    return e;
}

rt_err_t sp_phase_set(const sp_token_t *t, sp_phase_t phase)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp_token_ok_held(t)) { sp.phase = phase; e = RT_EOK; }
    else                     { sp.stale_dropped++; e = -RT_EPERM; }
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
        (void)sp_advance_held();
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
    if (!sp_token_ok_held(t)) { sp.stale_dropped++; return -RT_EPERM; }
    if (t->owner != SP_MOTOR) { sp.stale_dropped++; return -RT_EPERM; }
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
        sp_note_pwm_held(-RT_ENOSYS, RT_FALSE, MOT_STOP_SRC_NONE, sp.owner);
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

/* 诊断视图(R6): 诊断**自己那一路**的历史与结果, 不看 Motor 新一轮运行造成的当前
 * 输出事实 —— 已经归还给 NONE 的诊断不该因为 Motor 后来 start 了就被说成"仍在占用"。 */
void sp_view_diag_held(mot_stop_result_t *res, rt_err_t *rc, rt_bool_t *latched)
{
    if (!sp.diag_ever_reserved)
    {
        if (res) *res = MOT_STOP_NOT_ATTEMPTED;      /* 从未占用: 不伪造一次 API 调用 */
        if (rc)  *rc  = RT_EOK;
        if (latched) *latched = RT_FALSE;
    }
    else
    {
        if (res) *res = sp.diag_output_active ? sp.pwm.result : MOT_STOP_API_ACCEPTED;
        if (rc)  *rc  = sp.diag_output_active ? sp.pwm.raw_rc : RT_EOK;
        if (latched) *latched = sp.diag_stop_latched;
    }
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

/* 保守判据(R6): 诊断这一路自己"仍持有/未确认交回"、当前 owner 是诊断、状态未知,
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
    rt_base_t l = sp_win();
    sp_owner_t before = sp.owner;
    rt_err_t  pe, le;

    if (sp.epoch != ticket || !sp.inhibit)
    {
        sp.stale_dropped++;
        sp_copy_proof_held(out);                    /* 把当前事实带回, 调用方照实报告 */
        sp_end(l);
        return -RT_EINTR;                           /* 期间已有新请求: 本次恢复自然失效 */
    }
    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    sp_note_pwm_held(pe, sp_pwm != RT_NULL ? RT_TRUE : RT_FALSE,
                     MOT_STOP_SRC_DISARM, before);
    le = safety_drv_permit_low_raw_held();
    sp_note_permit_held(le, MOT_STOP_SRC_DISARM, 0u);
    if (pe == RT_EOK && before != SP_DIAG) sp.owner = SP_NONE;
    else if (pe != RT_EOK)
    {
        sp.owner = SP_HELD_UNKNOWN;
        sp_advance_held();                       /* 复核失败 = 新失败: 换 epoch 并保持抑制 */
        sp_retain_held(0u, 0u);
    }
    sp_copy_proof_held(out);
    sp_end(l);
    return (pe == RT_EOK) ? ((le == RT_EOK) ? RT_EOK : le) : pe;
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
