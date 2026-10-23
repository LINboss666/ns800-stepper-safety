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
#define SP_EPOCH_BITS  24u                       /* 见 §4 头文件注释: 不回绕, 到顶即饱和 */
#define SP_EPOCH_MASK  ((1u << SP_EPOCH_BITS) - 1u)

/* ---------------- 屏障 ---------------- */
#if defined(__ARMCC_VERSION) || defined(__CC_ARM) || (defined(__GNUC__) && defined(__arm__))
#ifndef __DSB
#define __DSB() __asm__ __volatile__ ("dsb 0xF" ::: "memory")
#endif
#define SP_BARRIER() __DSB()
#else
#define SP_BARRIER() __atomic_thread_fence(__ATOMIC_SEQ_CST)
#endif

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
    rt_bool_t        diag_ever_reserved;
    rt_bool_t        metadata_pending;
    rt_uint32_t      first_fault_code;
    rt_uint32_t      reason_events;
    rt_uint32_t      trips;
    rt_uint32_t      merged;
    rt_uint32_t      stale_dropped;
    sp_pwm_rec_t     pwm;
    sp_permit_rec_t  permit;
    sp_pwm_rec_t     first_unrecovered;
    rt_bool_t        first_unrecovered_valid;
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

/* 头文件规定的核心控制结构（可适配命名，不改协议）。 */
static rt_bool_t sp_advance_held(void)
{
    if (sp.epoch == SP_EPOCH_MASK)
    {
        sp.exhausted = RT_TRUE;
        sp.inhibit   = RT_TRUE;
        return RT_FALSE;                       /* 饱和: 仍继续本次关闭, 但不再接受预约/恢复 */
    }
    sp.epoch++;
    if (sp.epoch == SP_EPOCH_MASK)
    {
        sp.exhausted = RT_TRUE;
        sp.inhibit   = RT_TRUE;
        return RT_FALSE;
    }
    return RT_TRUE;
}

static rt_bool_t sp_token_ok_held(const sp_token_t *t)
{
    return (t != RT_NULL) && !sp.inhibit && !sp.exhausted &&
           (sp.owner == t->owner) && (sp.epoch == t->epoch) &&
           ((t->owner == SP_MOTOR) || (t->owner == SP_DIAG))
               ? RT_TRUE : RT_FALSE;
}

static rt_bool_t sp_same_owner_epoch_held(const sp_token_t *t)
{
    return (t != RT_NULL) && (sp.owner == t->owner) && (sp.epoch == t->epoch)
               ? RT_TRUE : RT_FALSE;
}

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
    sp.reason_events  |= events;
    sp.inhibit         = RT_TRUE;
    sp.metadata_pending = RT_TRUE;
}

static void sp_note_pwm_held(rt_err_t pe, rt_bool_t attempted, rt_uint8_t src,
                             sp_owner_t before)
{
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
    if (sp.pwm.result == MOT_STOP_API_FAILED ||
        sp.pwm.result == MOT_STOP_DEV_UNAVAILABLE)
    {
        if (before == SP_DIAG || sp.diag_ever_reserved) sp.diag_stop_latched  = RT_TRUE;
        if (before != SP_DIAG)                         sp.motor_stop_latched = RT_TRUE;
        if (!sp.first_unrecovered_valid)
        {
            sp.first_unrecovered      = sp.pwm;
            sp.first_unrecovered_valid = RT_TRUE;
        }
    }
}

static void sp_note_permit_held(rt_err_t le, rt_uint8_t src)
{
    sp.permit.written     = RT_TRUE;
    sp.permit.write_rc    = le;
    sp.permit.src         = src;
    sp.permit.write_epoch = sp.epoch;
    sp.permit.confirmed   = RT_FALSE;         /* 裸写 != 回读确认 */
    sp.permit.confirm_rc  = RT_EOK;
    sp.permit.confirm_epoch = 0u;
}

static void sp_copy_proof_held(sp_proof_t *out)
{
    if (out == RT_NULL) return;
    out->valid          = RT_TRUE;
    out->epoch          = sp.epoch;
    out->pwm            = sp.pwm;
    out->permit         = sp.permit;
    out->diag_attempted = sp.pwm.attempted;
    out->diag_result    = sp.pwm.result;
    out->diag_rc        = sp.pwm.raw_rc;
    out->motor_off_ok   = (sp.pwm.result == MOT_STOP_API_ACCEPTED &&
                           !sp.motor_stop_latched) ? RT_TRUE : RT_FALSE;
    out->diag_off_ok    = (!sp.diag_stop_latched &&
                           (sp.pwm.result == MOT_STOP_API_ACCEPTED ||
                            (sp.pwm.result == MOT_STOP_NOT_ATTEMPTED &&
                             sp.owner != SP_DIAG && sp.owner != SP_HELD_UNKNOWN)))
                              ? RT_TRUE : RT_FALSE;
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
    sp_note_permit_held(le, src);

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

rt_err_t sp_trip_for_operation(rt_uint32_t expected_epoch, rt_uint32_t code,
                               rt_uint32_t events, rt_uint8_t src, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp.epoch != expected_epoch)
    {
        /* 同一操作里已有别的层做过保护/已有更新的请求 => 本次不做第二轮关闭 */
        sp.stale_dropped++;
        sp_end(l);
        if (out != RT_NULL) out->valid = RT_FALSE;
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
        sp_note_permit_held(le, src);
        sp.owner = SP_HELD_UNKNOWN;
        sp.trips++;
        sp_copy_proof_held(out);
        sp_end(l);
        return pe;
    }

    if (release_on_ok)
    {
        le = safety_drv_permit_low_raw_held();
        sp_note_permit_held(le, src);
        /* 只有本来就是这个 owner(或已经无人认领/状态未知)时才撤销预约。
         * 别的 owner 还在持有的时候, 一次 disarm/pwm_test stop 无权把它的身份清掉:
         * 那会让它回来时发现自己"没有输出记录"却仍在输出。共享模块的这次真实关断
         * 仍然照做(朝安全方向), 只是不换 epoch、不动别人的 owner。 */
        if (before == who || before == SP_NONE || before == SP_HELD_UNKNOWN)
        {
            sp.owner = SP_NONE;
            sp.phase = SP_RESERVED;
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
rt_err_t sp_reserve(sp_owner_t want, sp_token_t *out)
{
    rt_base_t l = sp_win();
    rt_err_t  e = -RT_EPERM;

    if (out != RT_NULL && !sp.inhibit && !sp.exhausted &&
        sp.owner == SP_NONE && want != SP_NONE && want != SP_HELD_UNKNOWN)
    {
        if (sp.motor_stop_latched)                      e = -RT_EPERM;   /* 未复核的关断失败 */
        else if (want == SP_DIAG && sp.diag_stop_latched) e = -RT_EPERM;
        else
        {
            sp_advance_held();
            sp.owner = want;
            sp.phase = SP_RESERVED;
            if (want == SP_DIAG) sp.diag_ever_reserved = RT_TRUE;
            if (out) { out->owner = want; out->epoch = sp.epoch; }
            e = RT_EOK;
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
        sp.owner = SP_NONE;
        sp_advance_held();
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
    e = safety_drv_permit_high_raw_held();      /* 同窗的一次 SET 寄存器写 */
    sp.permit.written     = (e == RT_EOK) ? RT_TRUE : RT_FALSE;
    sp.permit.write_rc    = e;
    sp.permit.src         = MOT_STOP_SRC_NONE;   /* HIGH 不是关断动作, 来源位只用于 LOW */
    sp.permit.write_epoch = sp.epoch;
    sp.permit.confirmed   = RT_FALSE;
    if (e == RT_EOK) sp.phase = SP_ARMED;
    sp_copy_proof_held(out);
    sp_end(l);
    return e;
}

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
    if (sp.owner == SP_MOTOR && sp.phase < SP_ARMED)
    {
        return -RT_EPERM;                       /* Motor 未拿到许可前不许起转 */
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

rt_err_t sp_note_permit_confirmed(const sp_token_t *t, rt_err_t confirm_rc)
{
    rt_base_t l = sp_win();
    rt_err_t  e;

    if (sp_token_ok_held(t))
    {
        sp.permit.confirmed   = (confirm_rc == RT_EOK) ? RT_TRUE : RT_FALSE;
        sp.permit.confirm_rc  = confirm_rc;
        sp.permit.confirm_epoch = sp.epoch;
        e = RT_EOK;
    }
    else
    {
        sp.stale_dropped++;                     /* 晚到的旧确认: 不改新 owner 的影子,
                                                   也**不**反向写 LOW 去破坏新预约 */
        e = -RT_EINTR;
    }
    sp_end(l);
    return e;
}

rt_err_t sp_note_low_confirmed(rt_err_t confirm_rc)
{
    rt_base_t l = sp_win();

    if (confirm_rc == RT_EOK)
    {
        sp.permit.confirmed     = RT_TRUE;
        sp.permit.confirm_rc    = RT_EOK;
        sp.permit.confirm_epoch = sp.epoch;
        sp.metadata_pending     = RT_TRUE;
    }
    sp_end(l);
    return confirm_rc;
}

/* ---------------- 观测 ---------------- */
void sp_get(sp_snapshot_t *out)
{
    rt_base_t l;

    if (out == RT_NULL) return;
    l = sp_win();
    out->epoch                = sp.epoch;
    out->owner                = sp.owner;
    out->phase                = sp.phase;
    out->inhibit              = sp.inhibit;
    out->exhausted            = sp.exhausted;
    out->motor_stop_latched   = sp.motor_stop_latched;
    out->diag_stop_latched    = sp.diag_stop_latched;
    out->diag_ever_reserved   = sp.diag_ever_reserved;
    out->metadata_pending     = sp.metadata_pending;
    out->first_fault_code     = sp.first_fault_code;
    out->reason_events        = sp.reason_events;
    out->trips                = sp.trips;
    out->merged               = sp.merged;
    out->stale_dropped        = sp.stale_dropped;
    out->pwm                  = sp.pwm;
    out->permit               = sp.permit;
    out->first_unrecovered    = sp.first_unrecovered;
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

void sp_view_motor(mot_stop_result_t *res, rt_uint8_t *src, rt_err_t *rc,
                   rt_bool_t *latched)
{
    rt_base_t l = sp_win();

    if (res) *res = sp.pwm.result;
    if (src) *src = sp.pwm.src;
    if (rc)  *rc  = sp.pwm.raw_rc;
    if (latched) *latched = sp.motor_stop_latched;
    sp_end(l);
}

void sp_view_diag(mot_stop_result_t *res, rt_err_t *rc, rt_bool_t *latched)
{
    rt_base_t l = sp_win();

    if (!sp.diag_ever_reserved)
    {
        if (res) *res = MOT_STOP_NOT_ATTEMPTED;      /* 从未占用: 不伪造一次 API 调用 */
        if (rc)  *rc  = RT_EOK;
        if (latched) *latched = RT_FALSE;
    }
    else
    {
        if (res) *res = sp.pwm.result;
        if (rc)  *rc  = sp.pwm.raw_rc;
        if (latched) *latched = sp.diag_stop_latched;
    }
    sp_end(l);
}

rt_bool_t sp_diag_off_confirmed(void)
{
    rt_base_t l = sp_win();
    rt_bool_t v;

    if (sp.diag_stop_latched)                 v = RT_FALSE;
    else if (!sp.diag_ever_reserved)          v = (sp.owner == SP_DIAG) ? RT_FALSE : RT_TRUE;
    else v = (sp.pwm.result == MOT_STOP_API_ACCEPTED && sp.owner != SP_DIAG)
                 ? RT_TRUE : RT_FALSE;
    sp_end(l);
    return v;
}

rt_bool_t sp_diag_output_active(void)
{
    rt_base_t l = sp_win();
    rt_bool_t v;

    v = (sp.owner == SP_DIAG || sp.owner == SP_HELD_UNKNOWN || sp.diag_stop_latched)
            ? RT_TRUE : RT_FALSE;
    sp_end(l);
    return v;
}

rt_bool_t sp_take_pending(rt_uint32_t *code, rt_uint32_t *events, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    rt_bool_t had = sp.metadata_pending;

    if (had)
    {
        if (code)   *code   = sp.first_fault_code;
        if (events) *events = sp.reason_events;
        sp_copy_proof_held(out);
        sp.metadata_pending = RT_FALSE;
    }
    sp_end(l);
    return had;
}

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

rt_err_t sp_recovery_probe_off(rt_uint32_t ticket, sp_proof_t *out)
{
    rt_base_t l = sp_win();
    sp_owner_t before = sp.owner;
    rt_err_t  pe, le;

    if (sp.epoch != ticket || !sp.inhibit)
    {
        sp.stale_dropped++;
        sp_end(l);
        return -RT_EINTR;                       /* 期间已有新请求: 本次恢复自然失效 */
    }
    pe = (sp_pwm != RT_NULL) ? rt_pwm_disable(sp_pwm, sp_pwm_ch) : -RT_ENOSYS;
    sp_note_pwm_held(pe, sp_pwm != RT_NULL ? RT_TRUE : RT_FALSE,
                     MOT_STOP_SRC_DISARM, before);
    le = safety_drv_permit_low_raw_held();
    sp_note_permit_held(le, MOT_STOP_SRC_DISARM);
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
 *   就永远进不来(S2-C13 的反例)。真正的判据是**结果属于本票据那一代**:
 *     epoch==ticket 且 inhibit 仍置位 且 未饱和
 *     且 最近一次实际 PWM 关断尝试(attempted, 且其 epoch==ticket)被驱动接受
 *     且 本次许可 LOW 裸写也在 ticket 这一代
 *   任何新故障/新失败都会推进 epoch, 因此"无较新失败"由 epoch 相等保证。 */
rt_bool_t sp_recovery_valid_held(rt_uint32_t ticket, sp_proof_t *proof)
{
    rt_bool_t ok;

    ok = (sp.epoch == ticket && sp.inhibit && !sp.exhausted &&
          sp.pwm.attempted && sp.pwm.result == MOT_STOP_API_ACCEPTED &&
          sp.pwm.epoch == ticket && sp.permit.write_epoch == ticket)
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

rt_bool_t sp_finish_recovery_held(rt_uint32_t ticket)
{
    if (!sp_recovery_valid_held(ticket, RT_NULL)) return RT_FALSE;  /* 较新的请求赢 */

    sp_clear_latches_held();                     /* 只清本次复核已覆盖的历史锁存 */
    sp.owner                = SP_NONE;
    sp.phase                = SP_RESERVED;
    sp.first_fault_code     = 0u;
    sp.reason_events        = 0u;
    sp.metadata_pending     = RT_FALSE;
    sp.first_unrecovered_valid = RT_FALSE;
    sp.pwm.result           = MOT_STOP_NOT_ATTEMPTED;
    sp.pwm.attempted        = RT_FALSE;
    sp.pwm.raw_rc           = RT_EOK;
    sp.pwm.epoch            = 0u;
    sp.permit.written       = RT_FALSE;
    sp.permit.confirmed     = RT_FALSE;
    sp.permit.write_epoch   = 0u;
    /* 诊断"从未占用"的语义在成功恢复后重新成立: 本次复核在 ticket 这一代做了一次
     * **真实且被接受**的共享模块关断, 而 owner 已交回 NONE。不清这一位的话,
     * sp_diag_off_confirmed() 会永远返回 FALSE, 于是 motor_arm 的第 2 门在人工恢复
     * 之后仍然堵死(S2-C13 要求"不永久卡死")。历史失败锁存(diag_stop_latched)已经在
     * 上面随本次复核一并清除。 */
    sp.diag_ever_reserved   = RT_FALSE;
    sp_advance_held();                           /* 恢复也是换 epoch 的状态变化 */
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
