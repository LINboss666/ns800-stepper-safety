/*
 * sp_measure_a0.c - 目标机测量 A0 记录器（任务 phase8-target-measurement-a0）
 *
 * 整个文件被 NS800_TARGET_MEASUREMENT_A0 包住：默认目标编译到这里是一个空翻译单元，
 * 因此默认镜像里既没有 64 槽数组、也没有计数器访问、也没有 spm 命令（编译期事实，
 * 不是"运行期 if 包住了新增调用"）。
 *
 * 纪律（对应任务书 §4/§5 与计划 §2.5）：
 *   - 预约/发布用**原始 PRIMASK 短段**（rt_hw_interrupt_disable/enable），不递归调用
 *     sp_win、不取任何锁、不分配内存、不打印；满了只累加 dropped，**照常执行原关闭动作**。
 *   - 单批次只追加：cursor 到 SPM_SLOTS 就停，不覆盖、不复用未完成槽。
 *   - 每个在途调用持有自己的栈上句柄 + 代次；线程被抢占、ISR 嵌套、旧调用晚发布都只写
 *     自己那一槽。
 *   - 窗口内只允许固定数目的字段赋值与寄存器**读**；无日志/UART/锁/等待/浮点/循环复制。
 *   - 主体区间"小于一圈"的依据是窗口内 COUNTFLAG 未置位（期间没有发生重装），**不是**拿
 *     SysTick->VAL 做模差分；包围区间没有独立时基与完整防混叠依据 ⇒ 恒无效，但保留
 *     pre/body_begin/body_end/post 四个原始值。
 *   - 未通过能力检查前**不触碰任何 PPB 地址**（TRCENA 未开时读 DWT 属实现定义）；
 *     counter_status=UNCHECKED/UNAVAILABLE 时计数读返回 0，且样本判无效 —— 0 从不被
 *     解释成"0 耗时"，也不作为有效 cycles 参与统计。
 *
 * ⚠ 记录器不是关闭成功的前置：三个入口的原动作、原调用次数、原返回优先序、原事件/黑匣子
 *   语义全部不变；所有早退也照样结束本次记录（或计为丢弃）。
 */

#include <rtthread.h>
#include <rtdevice.h>
#include <rthw.h>
#include "sp_measure_a0.h"

#if NS800_TARGET_MEASUREMENT_A0

#include "safety_state.h"        /* FAULT_SELF_TEST + safety_force_shutdown() 的真实定义 */
#include "motor.h"               /* 四宏回读(MSH cap 报告用) */
#include "project_board.h"
#include "tmc2209.h"
#if !defined(NS800_SPM_HOST_BACKEND)
#include <finsh.h>               /* MSH_CMD_EXPORT: 目标构建走真实 shell 注册 */
#endif

/* ---------------- 计数/时基后端 ----------------
 * 目标：当前工程实际编入的 CMSIS/芯片头（board.h 的包含链给出 core_cm7.h），
 *       不硬编码地址、不新增 SDK 依赖包。
 * 主机：夹具的脚本化寄存器模型，体现 CTRL 读清 COUNTFLAG 的副作用；不拿墙钟冒充周期计数。 */
#define SPM_COUNTFLAG_Msk    (1UL << 16)   /* SysTick_CTRL_COUNTFLAG_Pos = 16 */
#define SPM_SYSTICK_ENABLE   (1UL << 0)
#define SPM_SYSTICK_CLKSRC   (1UL << 2)    /* 1 = 处理器时钟(不是 AHB/8) */
#define SPM_DEMCR_TRCENA     (1UL << 24)   /* CoreDebug_DEMCR_TRCENA_Pos = 24 */
#define SPM_CTRL_CYCCNTENA   (1UL << 0)    /* DWT_CTRL_CYCCNTENA_Pos = 0 */
#define SPM_CTRL_NOCYCCNT    (1UL << 25)   /* DWT_CTRL_NOCYCCNT_Pos = 25 (只能运行期读) */

#if defined(NS800_SPM_HOST_BACKEND)
extern rt_uint32_t spm_port_ctr_now(void);            /* CYCCNT 模型值 */
extern rt_uint32_t spm_port_ctr_ctrl(void);           /* DWT_CTRL 模型值 */
extern rt_uint32_t spm_port_demcr(void);              /* DEMCR 模型值 */
extern void        spm_port_ctr_enable(rt_uint8_t trcena, rt_uint8_t cyccntena);
extern rt_uint32_t spm_port_systick_ctrl(void);       /* 读并**清** COUNTFLAG(模型副作用) */
extern rt_uint32_t spm_port_systick_load(void);
extern rt_uint32_t spm_port_primask(void);
#else
#include <board.h>
static rt_uint32_t spm_port_ctr_now(void)   { return DWT->CYCCNT; }
static rt_uint32_t spm_port_ctr_ctrl(void)  { return DWT->CTRL; }
static rt_uint32_t spm_port_demcr(void)     { return CoreDebug->DEMCR; }
static void spm_port_ctr_enable(rt_uint8_t trcena, rt_uint8_t cyccntena)
{
    /* 只置必要的两位，保留其它位；**不清零 CYCCNT**，不抢占调试器已有的计数语义。 */
    if (trcena)    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    if (cyccntena) DWT->CTRL        |= DWT_CTRL_CYCCNTENA_Msk;
}
static rt_uint32_t spm_port_systick_ctrl(void) { return SysTick->CTRL; }  /* 读即清 COUNTFLAG */
static rt_uint32_t spm_port_systick_load(void) { return SysTick->LOAD; }
static rt_uint32_t spm_port_primask(void)
{
    rt_uint32_t pm = 0u;
#if defined(__ARM_ARCH) && (__ARM_ARCH >= 7)
    __asm__ __volatile__ ("mrs %0, primask" : "=r"(pm));
#endif
    return pm;
}
#endif

/* ---------------- 记录器状态（唯一的新增 BSS，只在本变体存在） ---------------- */
static spm_rec_t   spm_rec[SPM_SLOTS];
static rt_uint32_t spm_cursor;                 /* 已追加槽数(不循环复用) */
static rt_uint32_t spm_in_flight_n;            /* RESERVED 未发布数 */
static rt_uint32_t spm_dropped_n;
static rt_uint32_t spm_generation;             /* 每次预约 +1 */
static rt_uint32_t spm_state_v = SPM_STATE_IDLE;
static rt_uint8_t  spm_active_entry;           /* 0 = 未选择 => 不记录 */
static rt_uint8_t  spm_ctr_status = SPM_CNT_UNCHECKED;
static rt_uint8_t  spm_clk_basis  = SPM_CLK_SOURCE_ASSUMED;
static rt_uint32_t spm_guard_load;             /* init 时读到的 SysTick->LOAD */
static rt_uint8_t  spm_guard_ready;            /* 1 = init 已确认 SysTick 判据前提 */
static rt_uint8_t  spm_guard_depth;            /* 嵌套记录层数(内层清旗 => 外层无效) */
static spm_ctx_t  *spm_guard_owner;            /* 当前持有 COUNTFLAG 判据的那一次记录 */

static void spm_barrier(void)
{
#if defined(__ARMCC_VERSION) || defined(__CC_ARM) || (defined(__GNUC__) && defined(__arm__))
#ifndef __DSB
#define __DSB() __asm__ __volatile__ ("dsb 0xF" ::: "memory")
#endif
    __DSB();
#else
    __atomic_thread_fence(__ATOMIC_SEQ_CST);
#endif
}

/* 原始 PRIMASK 短段: 不碰 sp_win/sp_win_nest(见任务书 §4.2)。 */
#define SPM_RAW_WIN(l)   do { (l) = rt_hw_interrupt_disable(); } while (0)
#define SPM_RAW_END(l)   do { spm_barrier(); rt_hw_interrupt_enable(l); } while (0)

/* 未证可用前不访问 PPB：返回 0，并由 counter_status + invalid_reason 说明这不是一次耗时。 */
static rt_uint32_t spm_ctr_read(void)
{
    if (spm_ctr_status != SPM_CNT_OK) return 0u;
    return spm_port_ctr_now();
}

/* ---------------- 预约 ---------------- */
void spm_open(spm_ctx_t *c, rt_uint8_t entry_id, rt_uint32_t input_expected_epoch,
              rt_uint8_t input_owner, rt_uint8_t release_on_ok, const void *proof_out)
{
    rt_base_t l;

    c->rec = RT_NULL;
    c->capture_generation = 0u;
    c->entry_id = entry_id;
    c->guard_owner = 0u;
    c->counter_status = spm_ctr_status;
    c->clock_basis = spm_clk_basis;
    c->guard_metadata = 0u;
    c->guard_load_seen = 0u;
    c->pre = 0u; c->body_begin = 0u; c->body_end = 0u; c->post = 0u;
    c->invalid_reason = SPM_IV_NONE;
    c->saved_primask = 0u;
    c->context_isr = (rt_interrupt_get_nest() != 0) ? 1u : 0u;
    c->open_ok = 0u;

    if (entry_id == 0u) return;                       /* 未知入口: 不记录, 也不动任何东西 */

    SPM_RAW_WIN(l);
    c->open_ok = 1u;
    if (spm_state_v != SPM_STATE_ACTIVE || spm_active_entry != entry_id)
    {
        SPM_RAW_END(l);                               /* 未在测该入口: 不占槽, 也不计丢弃 */
        return;
    }
    if (spm_cursor >= SPM_SLOTS)
    {
        spm_dropped_n++;                              /* 满: 只计丢失, 不等待、不打印 */
        SPM_RAW_END(l);
        return;
    }
    {
        spm_rec_t *r = &spm_rec[spm_cursor];

        r->complete = 0u;                             /* RESERVED: 未完成槽不得当成完整样本 */
        r->schema = SPM_SCHEMA_VERSION;
        r->capture_generation = ++spm_generation;
        r->slot = (rt_uint8_t)spm_cursor;
        r->entry_id = entry_id;
        r->context_isr = c->context_isr;
        r->saved_primask = (spm_port_primask() != 0u) ? 1u : 0u;
        r->input_release_on_ok = release_on_ok;
        r->proof_out_null = (proof_out == RT_NULL) ? 1u : 0u;
        r->input_expected_epoch = input_expected_epoch;
        r->input_owner = input_owner;
        r->epoch_before = 0u; r->epoch_after = 0u;
        r->owner_after = 0u;  r->phase_after = 0u;
        r->inhibit = 0u;      r->pending = 0u;   r->pending_epoch = 0u;
        r->motor_stop_latched = 0u; r->diag_stop_latched = 0u;
        r->trips_before = 0u; r->trips_after = 0u;
        r->observed_pwm_epoch = 0u; r->observed_pwm_attempted = 0u;
        r->observed_pwm_result = 0u;  r->observed_pwm_raw_rc = 0;
        r->observed_permit_epoch = 0u; r->observed_permit_written = 0u;
        r->observed_permit_level = 0u; r->observed_permit_write_rc = 0;
        r->call_outcome_or_branch = 0; r->branch_id = 0u;
        r->pwm_called_by_this_call = 0u; r->low_called_by_this_call = 0u;
        r->pre = 0u; r->body_begin = 0u; r->body_end = 0u; r->post = 0u;
        r->counter_status = spm_ctr_status;
        r->clock_basis = spm_clk_basis;
        r->body_valid = 0u; r->enclosing_valid = 0u;
        r->invalid_reason = SPM_IV_NONE;
        r->guard_metadata = 0u; r->guard_load_seen = spm_guard_load;
        r->dropped_at_publish = spm_dropped_n;

        c->rec = r;
        c->capture_generation = r->capture_generation;
        spm_cursor++;
        spm_in_flight_n++;
    }
    SPM_RAW_END(l);

    if (c->rec != RT_NULL)
    {
        c->pre = spm_ctr_read();                      /* 被测 disable 之前 */
        c->rec->pre = c->pre;
    }
}

/* ---------------- 窗口内: 起点判据 + body_begin ----------------
 * 只在 counter_status=OK 且 init 已确认 SysTick 前提时才读 CTRL —— 读 CTRL 会清 COUNTFLAG，
 * 这是本记录器对系统原有状态的**唯一**副作用(审计: 现构建里没有任何代码在运行期读
 * SysTick->CTRL; SysTick_Handler 只做 rt_interrupt_enter/rt_tick_increase/rt_interrupt_leave,
 * rt_hw_us_delay 只读 LOAD/VAL)。 */
void spm_win_note(spm_ctx_t *c, rt_uint8_t saved_primask_set)
{
    rt_uint32_t ctrl;

    if (c->rec == RT_NULL) return;

    c->saved_primask = saved_primask_set;
    c->rec->saved_primask = saved_primask_set;

    if (c->counter_status != SPM_CNT_OK || !spm_guard_ready)
    {
        c->invalid_reason |= SPM_IV_COUNTER_NOT_OK;
        if (!spm_guard_ready)
        {
            c->invalid_reason |= SPM_IV_GUARD_UNAVAILABLE;
            c->guard_metadata |= spm_guard_ready ? SPM_GD_SYSTICK_ENABLED : 0u;
        }
        c->rec->guard_metadata = c->guard_metadata;
        c->rec->guard_load_seen = spm_guard_load;
        c->body_begin = spm_ctr_read();
        c->rec->body_begin = c->body_begin;
        return;
    }

    ctrl = spm_port_systick_ctrl();                   /* 起点读: 清掉遗留旗 */
    c->guard_metadata = 0u;
    if (ctrl & SPM_SYSTICK_ENABLE)  c->guard_metadata |= SPM_GD_SYSTICK_ENABLED;
    if (ctrl & SPM_SYSTICK_CLKSRC)  c->guard_metadata |= SPM_GD_CLKSOURCE_PROCESSOR;
    if (spm_port_systick_load() != 0u) c->guard_metadata |= SPM_GD_LOAD_CAPTURED;
    if (ctrl & SPM_COUNTFLAG_Msk)   c->guard_metadata |= SPM_GD_FLAG_PRE_SET;
    c->rec->guard_metadata = c->guard_metadata;
    c->rec->guard_load_seen = spm_guard_load;

    if (!(c->guard_metadata & SPM_GD_SYSTICK_ENABLED) ||
        !(c->guard_metadata & SPM_GD_CLKSOURCE_PROCESSOR) ||
        !(c->guard_metadata & SPM_GD_LOAD_CAPTURED))   c->invalid_reason |= SPM_IV_GUARD_UNAVAILABLE;
    if (c->guard_metadata & SPM_GD_FLAG_PRE_SET)      c->invalid_reason |= SPM_IV_FLAG_PRE;
    /* 嵌套: 内层的起点读会清掉外层已建立的"无重装"依据 => **外层**判无效(内层同样判无效)。
     * 目标上只有不可屏蔽源(NMI/HardFault 类)能造成这种交错, 而那本来就在记录器的同步合同
     * 之外(计划 M-1 边界); 本链用显式注入验证"被扰动即标无效"这条标注行为, 不靠普通 ISR
     * 在屏蔽区里"帮忙"跑起来。 */
    if (spm_guard_depth > 0u)
    {
        c->invalid_reason |= SPM_IV_NESTED_GUARD;
        if (spm_guard_owner != RT_NULL)
            spm_guard_owner->invalid_reason |= SPM_IV_NESTED_GUARD;
    }
    else
    {
        c->guard_owner = 1u;
        spm_guard_owner = c;
    }
    spm_guard_depth++;

    c->body_begin = spm_ctr_read();
    c->rec->body_begin = c->body_begin;
}

/* ---------------- 最终屏障之后、恢复中断之前: body_end + 终点判据 ---------------- */
void spm_body_end(spm_ctx_t *c)
{
    rt_uint32_t ctrl;

    if (c->rec == RT_NULL) return;

    c->body_end = spm_ctr_read();
    c->rec->body_end = c->body_end;

    if (c->counter_status == SPM_CNT_OK && spm_guard_ready)
    {
        if (c->guard_owner)
        {
            ctrl = spm_port_systick_ctrl();           /* 终点读: 窗口内是否发生过重装 */
            if (ctrl & SPM_COUNTFLAG_Msk)
            {
                c->guard_metadata |= SPM_GD_FLAG_POST_SET;
                c->invalid_reason |= SPM_IV_RELOAD_IN_BODY;
            }
            if ((ctrl & SPM_SYSTICK_ENABLE) == 0u || (ctrl & SPM_SYSTICK_CLKSRC) == 0u ||
                spm_port_systick_load() != spm_guard_load)
            {
                c->invalid_reason |= SPM_IV_CFG_CHANGED;
            }
            c->rec->guard_metadata = c->guard_metadata;
        }
        /* 深度释放对"取过判据"与"没取到判据(嵌套)"的两次调用都要发生, 否则外层永久滞留。 */
        if (spm_guard_depth > 0u) spm_guard_depth--;
        if (spm_guard_depth == 0u) spm_guard_owner = RT_NULL;
    }
}

/* ---------------- 发布 ---------------- */
static void spm_eval(spm_ctx_t *c, rt_int32_t api_rc, rt_uint8_t branch_id,
                     rt_uint8_t pwm_called, rt_uint8_t low_called)
{
    spm_rec_t *r = c->rec;
    rt_uint16_t why = c->invalid_reason;

    r->call_outcome_or_branch = api_rc;
    r->branch_id = branch_id;
    r->pwm_called_by_this_call = pwm_called;
    r->low_called_by_this_call = low_called;
    r->post = c->post;
    r->counter_status = c->counter_status;
    r->clock_basis = c->clock_basis;
    r->context_isr = c->context_isr;

    if (c->counter_status != SPM_CNT_OK) why |= SPM_IV_COUNTER_NOT_OK;

    /* 32 位计数差: end < begin 说明跨回绕(或计数器异常) => 本批保守判无效,
     * 不把它解释成"一次回绕内的短区间"。 */
    if (c->counter_status == SPM_CNT_OK && c->body_end < c->body_begin)
        why |= SPM_IV_COUNTER_WRAPPED;

    r->body_valid = (why == SPM_IV_NONE) ? 1u : 0u;
    /* 包围区间(post-pre)允许含恢复中断后的 ISR/调度; 本批没有独立时间基与完整防混叠依据
     * => 恒无效。主体的有效性**不得**给包围区间背书。 */
    r->enclosing_valid = 0u;
    why |= SPM_IV_ENCLOSING_UNPROVEN;
    r->invalid_reason = why;
}

void spm_publish(spm_ctx_t *c, rt_int32_t api_rc, rt_uint8_t branch_id,
                 rt_uint8_t pwm_called, rt_uint8_t low_called)
{
    rt_base_t l;
    spm_rec_t *r = c->rec;

    c->post = spm_ctr_read();                         /* 恢复原 PRIMASK 之后尽早 */
    if (r == RT_NULL) return;

    spm_eval(c, api_rc, branch_id, pwm_called, low_called);

    spm_barrier();                                    /* COMPLETE 之前满足写入可见性 */
    SPM_RAW_WIN(l);
    if (r->capture_generation == c->capture_generation && r->slot < SPM_SLOTS)
    {
        r->complete = 1u;                             /* RESERVED -> COMPLETE */
        r->dropped_at_publish = spm_dropped_n;
    }
    else
    {
        r->invalid_reason |= SPM_IV_GENERATION_STALE; /* 代次不符: 留成可判定证据 */
    }
    if (spm_in_flight_n > 0u) spm_in_flight_n--;
    SPM_RAW_END(l);
}

/* ---------------- 只读观测 ---------------- */
rt_uint32_t spm_state(void)           { return spm_state_v; }
rt_uint32_t spm_records_used(void)    { return spm_cursor; }
rt_uint32_t spm_in_flight(void)       { return spm_in_flight_n; }
rt_uint32_t spm_dropped(void)         { return spm_dropped_n; }
rt_uint8_t  spm_counter_status(void)  { return spm_ctr_status; }
rt_uint8_t  spm_clock_basis(void)     { return spm_clk_basis; }
rt_uint32_t spm_guard_load_value(void) { return spm_guard_load; }
rt_uint32_t spm_generation_next(void) { return spm_generation; }

const spm_rec_t *spm_record_at(rt_uint32_t i)
{
    if (i >= spm_cursor) return RT_NULL;              /* 未追加的槽一律不暴露 */
    return &spm_rec[i];
}

const char *spm_counter_status_name(rt_uint8_t v)
{
    if (v == SPM_CNT_OK)          return "COUNTER_OK";
    if (v == SPM_CNT_UNAVAILABLE) return "COUNTER_UNAVAILABLE";
    return "COUNTER_UNCHECKED";
}

const char *spm_clock_basis_name(rt_uint8_t v)
{
    if (v == SPM_CLK_CONFIG_READBACK)   return "CONFIG_READBACK";
    if (v == SPM_CLK_EXTERNAL_VERIFIED) return "EXTERNAL_VERIFIED";
    return "SOURCE_ASSUMED";
}

const char *spm_entry_name(rt_uint8_t v)
{
    if (v == SPM_ENTRY_TRIP_FAST)   return "trip_fast";
    if (v == SPM_ENTRY_TRIP_FOR_OP) return "trip_for_operation";
    if (v == SPM_ENTRY_OFF_ATTEMPT) return "off_attempt";
    return "-";
}

/* ---------------- 能力检查（只由显式命令触发；普通未屏蔽线程上下文） ---------------- */
static rt_bool_t spm_context_ok(void)
{
    if (rt_interrupt_get_nest() != 0)  return RT_FALSE;
    if (rt_thread_self() == RT_NULL)   return RT_FALSE;
    if (spm_port_primask() != 0u)      return RT_FALSE;   /* 已在屏蔽窗口里 => 拒绝且不打日志 */
    return RT_TRUE;
}

int spm_init_counter(void)
{
    volatile rt_uint32_t spin;
    rt_uint32_t ctrl_before, demcr, ctrl, a, b;

    if (!spm_context_ok()) return -1;

    /* 1) 读能力位与当前 DEMCR/DWT_CTRL(不做任何写)。NOCYCCNT 只能运行期读。 */
    ctrl_before = spm_port_ctr_ctrl();
    demcr = spm_port_demcr();
    spm_ctr_status = SPM_CNT_UNCHECKED;

    /* 2) 对已实现的计数器只启用必要的两位, 保留其它位, **不清零 CYCCNT**。 */
    if ((ctrl_before & SPM_CTRL_NOCYCCNT) == 0u) spm_port_ctr_enable(1u, 1u);

    /* 3) 有限次、固定指令间隔的读取确认递增: 不轮询到成功, 不用 rt_hw_us_delay。 */
    a = spm_port_ctr_now();
    for (spin = 0u; spin < 64u; spin++) { }
    b = spm_port_ctr_now();
    ctrl = spm_port_ctr_ctrl();

    if ((ctrl_before & SPM_CTRL_NOCYCCNT) != 0u)       spm_ctr_status = SPM_CNT_UNAVAILABLE;
    else if (a == b)                                   spm_ctr_status = SPM_CNT_UNAVAILABLE;
    else
    {
        spm_ctr_status = SPM_CNT_OK;
        /* 时钟依据: 这里读回的全是软件配置链 => 最多 CONFIG_READBACK。
         * CLI 没有把它改成 EXTERNAL_VERIFIED 的入口(独立时基不在本批)。 */
        if (spm_clk_basis == SPM_CLK_SOURCE_ASSUMED) spm_clk_basis = SPM_CLK_CONFIG_READBACK;
    }

    /* 4) 主体区间判据前提: SysTick 必须已启用 + 处理器时钟 + LOAD 可读(24 位重装值)。 */
    {
        rt_uint32_t sctrl = spm_port_systick_ctrl();   /* 这次读也清掉遗留 COUNTFLAG */

        spm_guard_load = spm_port_systick_load();
        spm_guard_ready = ((sctrl & SPM_SYSTICK_ENABLE) != 0u &&
                           (sctrl & SPM_SYSTICK_CLKSRC) != 0u &&
                           spm_guard_load != 0u) ? 1u : 0u;
    }

    rt_kprintf("[spm] init: counter=%s demcr=0x%08x ctrl=0x%08x a=%u b=%u load=%u "
               "guard=%s clock_basis=%s\n",
               spm_counter_status_name(spm_ctr_status), (unsigned)demcr, (unsigned)ctrl,
               (unsigned)a, (unsigned)b, (unsigned)spm_guard_load,
               spm_guard_ready ? "READY" : "NOT_ESTABLISHED",
               spm_clock_basis_name(spm_clk_basis));
    if (spm_ctr_status != SPM_CNT_OK)
        rt_kprintf("[spm] init: 无有效计数差 => 时长保持 COUNTER_UNAVAILABLE/UNCHECKED, "
                   "只交状态与顺序证据(0 不是零耗时)\n");
    else if (!spm_guard_ready)
        rt_kprintf("[spm] init: 主体区间判据未成立 => BLOCKED_COUNTER/INTERVAL_UNPROVEN, "
                   "不用未证明的 SysTick 差分凑有效值\n");
    return 0;
}

void spm_stop_accepting(void)
{
    if (!spm_context_ok()) return;
    if (spm_state_v == SPM_STATE_ACTIVE) spm_state_v = SPM_STATE_STOPPING;
    rt_kprintf("[spm] stop: state=%u in_flight=%u records=%u dropped=%u "
               "(不等待在途, 不清故障, 不关闭共享 DWT/DEMCR 位)\n",
               (unsigned)spm_state_v, (unsigned)spm_in_flight_n,
               (unsigned)spm_cursor, (unsigned)spm_dropped_n);
}

int spm_dump_records(void)
{
    rt_uint32_t i;

    if (!spm_context_ok()) return -1;
    if (spm_state_v == SPM_STATE_ACTIVE || spm_in_flight_n != 0u)
    {
        rt_kprintf("[spm] dump: BUSY (state=%u in_flight=%u) => 不循环打印活记录\n",
                   (unsigned)spm_state_v, (unsigned)spm_in_flight_n);
        return -2;
    }
    rt_kprintf("schema,gen,slot,entry,ctx_isr,saved_primask,rel_ok,proof_null,exp_epoch,"
               "in_owner,epoch_before,epoch_after,owner_after,phase_after,inhibit,pending,"
               "pending_epoch,latch_m,latch_d,trips_before,trips_after,pwm_epoch,"
               "pwm_attempted,pwm_result,pwm_raw_rc,permit_epoch,permit_written,"
               "permit_level,permit_write_rc,outcome,branch,pwm_called,low_called,"
               "pre,body_begin,body_end,post,counter_status,clock_basis,body_valid,"
               "enclosing_valid,invalid_reason,guard,guard_load,dropped_at_publish,complete\n");
    for (i = 0u; i < spm_cursor; i++)
    {
        const spm_rec_t *r = &spm_rec[i];

        if (r->complete != 1u)
        {
            /* 未完成槽不得导出成完整样本, 也不被新批次复用(见 clear 的前置)。 */
            rt_kprintf("1,%u,%u,INCOMPLETE,gen=%u\n", (unsigned)i, (unsigned)r->slot,
                       (unsigned)r->capture_generation);
            continue;
        }
        rt_kprintf("%u,%u,%u,%s,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,"
                   "%u,%u,%u,%d,%u,%u,%u,%d,%d,%u,%u,%u,%u,%u,%u,%u,%s,%s,%u,%u,0x%04x,"
                   "0x%02x,%u,%u,%u\n",
                   (unsigned)r->schema, (unsigned)r->capture_generation, (unsigned)r->slot,
                   spm_entry_name(r->entry_id), (unsigned)r->context_isr,
                   (unsigned)r->saved_primask, (unsigned)r->input_release_on_ok,
                   (unsigned)r->proof_out_null, (unsigned)r->input_expected_epoch,
                   (unsigned)r->input_owner, (unsigned)r->epoch_before,
                   (unsigned)r->epoch_after, (unsigned)r->owner_after,
                   (unsigned)r->phase_after, (unsigned)r->inhibit, (unsigned)r->pending,
                   (unsigned)r->pending_epoch, (unsigned)r->motor_stop_latched,
                   (unsigned)r->diag_stop_latched, (unsigned)r->trips_before,
                   (unsigned)r->trips_after, (unsigned)r->observed_pwm_epoch,
                   (unsigned)r->observed_pwm_attempted, (unsigned)r->observed_pwm_result,
                   (int)r->observed_pwm_raw_rc, (unsigned)r->observed_permit_epoch,
                   (unsigned)r->observed_permit_written, (unsigned)r->observed_permit_level,
                   (int)r->observed_permit_write_rc, (int)r->call_outcome_or_branch,
                   (unsigned)r->branch_id, (unsigned)r->pwm_called_by_this_call,
                   (unsigned)r->low_called_by_this_call, (unsigned)r->pre,
                   (unsigned)r->body_begin, (unsigned)r->body_end, (unsigned)r->post,
                   spm_counter_status_name(r->counter_status),
                   spm_clock_basis_name(r->clock_basis), (unsigned)r->body_valid,
                   (unsigned)r->enclosing_valid, (unsigned)r->invalid_reason,
                   (unsigned)r->guard_metadata, (unsigned)r->guard_load_seen,
                   (unsigned)r->dropped_at_publish, (unsigned)r->complete);
    }
    rt_kprintf("[spm] dump: records=%u dropped=%u (仅 body_valid=1 的样本进入时长统计)\n",
               (unsigned)spm_cursor, (unsigned)spm_dropped_n);
    return 0;
}

int spm_clear_records(void)
{
    rt_uint32_t i;

    if (!spm_context_ok()) return -1;
    if (spm_state_v == SPM_STATE_ACTIVE)
    {
        rt_kprintf("[spm] clear_records: BUSY (state=ACTIVE) => 先 stop\n");
        return -2;
    }
    if (spm_in_flight_n != 0u)
    {
        rt_kprintf("[spm] clear_records: BUSY (in_flight=%u) => 不等待也不强行清\n",
                   (unsigned)spm_in_flight_n);
        return -3;
    }
    /* 只清测量数据: 绝不调用 fault_reset, 绝不改 sp 的 inhibit/锁存/epoch/pending。
     * 在普通线程上下文里逐字段清(已停止且无在途 => 本记录器独占), 不关中断做整数组 memset。 */
    for (i = 0u; i < SPM_SLOTS; i++)
    {
        spm_rec_t *r = &spm_rec[i];

        r->complete = 0u; r->schema = 0u; r->capture_generation = 0u; r->slot = 0u;
        r->entry_id = 0u; r->context_isr = 0u; r->saved_primask = 0u;
        r->input_release_on_ok = 0u; r->proof_out_null = 0u;
        r->input_expected_epoch = 0u; r->input_owner = 0u;
        r->epoch_before = 0u; r->epoch_after = 0u; r->owner_after = 0u; r->phase_after = 0u;
        r->inhibit = 0u; r->pending = 0u; r->pending_epoch = 0u;
        r->motor_stop_latched = 0u; r->diag_stop_latched = 0u;
        r->trips_before = 0u; r->trips_after = 0u;
        r->observed_pwm_epoch = 0u; r->observed_pwm_attempted = 0u;
        r->observed_pwm_result = 0u; r->observed_pwm_raw_rc = 0;
        r->observed_permit_epoch = 0u; r->observed_permit_written = 0u;
        r->observed_permit_level = 0u; r->observed_permit_write_rc = 0;
        r->call_outcome_or_branch = 0; r->branch_id = 0u;
        r->pwm_called_by_this_call = 0u; r->low_called_by_this_call = 0u;
        r->pre = 0u; r->body_begin = 0u; r->body_end = 0u; r->post = 0u;
        r->counter_status = SPM_CNT_UNCHECKED; r->clock_basis = SPM_CLK_SOURCE_ASSUMED;
        r->body_valid = 0u; r->enclosing_valid = 0u; r->invalid_reason = SPM_IV_NONE;
        r->guard_metadata = 0u; r->guard_load_seen = 0u; r->dropped_at_publish = 0u;
    }
    spm_cursor = 0u; spm_dropped_n = 0u; spm_in_flight_n = 0u;
    spm_state_v = SPM_STATE_IDLE; spm_active_entry = 0u;
    spm_guard_depth = 0u; spm_guard_owner = RT_NULL;
    rt_kprintf("[spm] clear_records: 测量 RAM 已清(未触碰 Safety 故障/抑制/失败锁存)\n");
    return 0;
}

/* 未来 A 包里单列批准的一次正式关闭: 只调一次既有正式入口, 不自动恢复/自检/循环。
 * ⚠ 这条动作会经现有 Safety/黑匣子/存储路径产生后续写入, 不是"只读"命令。 */
int spm_trip_once(void)
{
    if (!spm_context_ok()) return -1;                  /* 非法上下文: 拒绝且不在其中打印 */
    safety_force_shutdown(FAULT_SELF_TEST);            /* 关闭先于任何输出 */
    rt_kprintf("[spm] trip: 已调用一次 safety_force_shutdown(FAULT_SELF_TEST=%u)\n",
               (unsigned)FAULT_SELF_TEST);
    return 0;
}

/* ---------------- 命令族（只在测量变体里存在） ---------------- */
static int spm_start(const char *entry)
{
    rt_uint8_t id = 0u;

    if (!spm_context_ok()) return -1;
    if (entry == RT_NULL || entry[0] == '\0')
    {
        rt_kprintf("[spm] start: 缺 entry (仅 trip_fast/trip_for_operation/off_attempt)\n");
        return -2;
    }
    if (rt_strcmp(entry, "trip_fast") == 0)               id = SPM_ENTRY_TRIP_FAST;
    else if (rt_strcmp(entry, "trip_for_operation") == 0) id = SPM_ENTRY_TRIP_FOR_OP;
    else if (rt_strcmp(entry, "off_attempt") == 0)        id = SPM_ENTRY_OFF_ATTEMPT;
    else
    {
        rt_kprintf("[spm] start: 未知 entry='%s'\n", entry);
        return -2;
    }
    if (spm_state_v != SPM_STATE_IDLE)
    {
        rt_kprintf("[spm] start: BUSY (state=%u) => 先 stop/dump/clear_records\n",
                   (unsigned)spm_state_v);
        return -3;
    }
    if (spm_cursor != 0u || spm_in_flight_n != 0u)
    {
        rt_kprintf("[spm] start: 仍有旧记录或在途 (records=%u in_flight=%u) => 拒绝\n",
                   (unsigned)spm_cursor, (unsigned)spm_in_flight_n);
        return -4;
    }
    spm_active_entry = id;
    spm_state_v = SPM_STATE_ACTIVE;
    rt_kprintf("[spm] start: entry=%s (本命令不触发任何关闭动作)\n", spm_entry_name(id));
    return 0;
}

int spm_cmd(int argc, char **argv)
{
    const char *sub;

    /* 整条命令族只接受普通、未屏蔽的线程上下文：非法上下文一律拒绝，且**不在其中打印**
     * (包括 cap/status 这种"只读"子命令 —— 打印本身就是窗口内禁止项)。 */
    if (!spm_context_ok()) return -1;

    if (argc < 2 || argv[1] == RT_NULL)
    {
        rt_kprintf("usage: spm cap|init|start <entry>|stop|status|dump|clear_records|trip\n");
        return 0;
    }
    sub = argv[1];

    /* 未知子命令/参数错误在任何计数读取、控制变化与安全动作**之前**拒绝。 */
    if (rt_strcmp(sub, "cap") == 0)
    {
        rt_kprintf("[spm] schema=%u slots=%u record_bytes=%u sizeof_ctx=%u\n",
                   SPM_SCHEMA_VERSION, SPM_SLOTS, (unsigned)sizeof(spm_rec),
                   (unsigned)sizeof(spm_ctx_t));
        rt_kprintf("[spm] 编译四宏: MOTOR_HW=%d STEP_DIAG_ISO=%d TMC_CFG_HW=%d LIMIT_EN=%d "
                   "(测量开关不改变这四道门)\n",
                   (int)MOTOR_HARDWARE_ENABLE_PATH_VALIDATED,
                   (int)STEP_DIAG_ISOLATED_ENV_CONFIRMED,
                   (int)TMC_DRIVE_CONFIG_HW_VALIDATED,
                   (int)SAFETY_LIMIT_INPUTS_ENABLED);
        rt_kprintf("[spm] counter=%s clock_basis=%s systick_load=%u guard_ready=%s "
                   "state=%u records=%u in_flight=%u dropped=%u\n",
                   spm_counter_status_name(spm_ctr_status),
                   spm_clock_basis_name(spm_clk_basis), (unsigned)spm_guard_load,
                   spm_guard_ready ? "YES" : "NO", (unsigned)spm_state_v,
                   (unsigned)spm_cursor, (unsigned)spm_in_flight_n,
                   (unsigned)spm_dropped_n);
        return 0;
    }
    if (rt_strcmp(sub, "init") == 0)          { (void)spm_init_counter(); return 0; }
    if (rt_strcmp(sub, "start") == 0)         { return spm_start(argc > 2 ? argv[2] : "") == 0 ? 0 : 1; }
    if (rt_strcmp(sub, "stop") == 0)          { spm_stop_accepting(); return 0; }
    if (rt_strcmp(sub, "status") == 0)
    {
        rt_kprintf("[spm] status: state=%u entry=%s records=%u in_flight=%u dropped=%u "
                   "counter=%s clock=%s\n",
                   (unsigned)spm_state_v, spm_entry_name(spm_active_entry),
                   (unsigned)spm_cursor, (unsigned)spm_in_flight_n,
                   (unsigned)spm_dropped_n, spm_counter_status_name(spm_ctr_status),
                   spm_clock_basis_name(spm_clk_basis));
        return 0;
    }
    if (rt_strcmp(sub, "dump") == 0)          { return spm_dump_records() == 0 ? 0 : 1; }
    if (rt_strcmp(sub, "clear_records") == 0) { return spm_clear_records() == 0 ? 0 : 1; }
    if (rt_strcmp(sub, "trip") == 0)          { return spm_trip_once() == 0 ? 0 : 1; }

    rt_kprintf("[spm] 未知子命令 '%s' => 拒绝(未读计数器, 未改状态, 未做关闭)\n", sub);
    return 1;
}

#if !defined(NS800_SPM_HOST_BACKEND)
MSH_CMD_EXPORT(spm_cmd, NS800 target measurement A0 control);
#endif

#endif /* NS800_TARGET_MEASUREMENT_A0 */
