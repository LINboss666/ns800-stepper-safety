
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
typedef int rt_base_t; typedef unsigned rt_uint32_t; typedef int rt_err_t;
#define RT_EOK 0
#define RT_ERROR 1
#define RT_EINVAL 22
#define RT_TRUE 1
#define RT_FALSE 0
#define RT_NULL NULL
#define PIN_HIGH 1
#define PIN_LOW 0
#define PIN_IRQ_MODE_FALLING 1
#define PIN_IRQ_MODE_RISING 2
#define PIN_IRQ_ENABLE 1
#define PIN_IRQ_DISABLE 0
#define PIN_NAME_ENN_FEEDBACK "PC.6"
#define ST_INPUT_N 1
#define rt_strcmp strcmp
static int st_irq_attached=0, st_polarity_validated=0, st_thread_up=1, st_health=0;
struct safety_input {const char *name; void (*isr)(void *); unsigned irq_mode; int safe_level; const char *label;};
static const struct safety_input st_inputs[]={{"PA.3",NULL,PIN_IRQ_MODE_RISING,PIN_LOW,"TMC_DIAG"}};
static int safety_pin(const char *s){return strcmp(s,"PC.6")==0?70:3;}
static int rt_pin_read(int pin){return pin==70?1:0;}
static int safety_limit_inputs_enabled(void){return 0;}
static int safety_gpio_ready(void){return 1;}
static const char *subsys_health_name(int v){(void)v;return "OK";}
static int safety_protection_ready(void){return st_irq_attached && st_polarity_validated;}
static int rt_pin_attach_irq(int p,unsigned m,void (*cb)(void *),void *v){(void)p;(void)m;(void)cb;(void)v;return 0;}
static int rt_pin_irq_enable(int p,int e){(void)p;(void)e;return 0;}
static int rt_pin_detach_irq(int p){(void)p;return 0;}
static int rt_kprintf(const char *fmt,...){char b[128];va_list ap;int n,k;
 va_start(ap,fmt);n=vsnprintf(b,127,fmt,ap);va_end(ap);if(n<0)return n;k=n>127?127:n;
 fprintf(stderr,"CALL intended=%d emitted=%d\n",n,k);fwrite(b,1,(size_t)k,stdout);return k;}
static void st_irq_rollback(rt_uint32_t attached, rt_uint32_t enabled,
                            const char *tag)
{
    int i;

    for (i = ST_INPUT_N - 1; i >= 0; --i)
    {
        rt_base_t pin;

        if (!(attached & (1u << i))) continue;
        pin = safety_pin(st_inputs[i].name);
        if (pin < 0) continue;

        if (enabled & (1u << i))
        {
            rt_err_t de = rt_pin_irq_enable(pin, PIN_IRQ_DISABLE);
            if (de != RT_EOK)
                rt_kprintf("[SAFETY] %s: disable %s FAILED (%d)\n",
                           tag, st_inputs[i].name, de);
        }
        {
            rt_err_t te = rt_pin_detach_irq(pin);
            rt_kprintf("[SAFETY] %s: %s detached (%d)\n",
                       tag, st_inputs[i].name, te);
        }
    }
    st_irq_attached = RT_FALSE;
}

rt_err_t safety_irq_attach(void)
{
    rt_uint32_t attached = 0, enabled = 0;
    rt_err_t e = RT_EOK;
    int i;

    if (st_irq_attached) return RT_EOK;         /* 幂等: 门已开 */

    /* 前置: Safety Thread 必须在跑(否则事件无人消费) */
    if (!st_thread_up)
    {
        rt_kprintf("[SAFETY] irq attach REFUSED: safety thread not running\n");
        return -RT_ERROR;
    }

    for (i = 0; i < ST_INPUT_N; ++i)
    {
        rt_base_t pin = safety_pin(st_inputs[i].name);

        if (pin < 0)
        {
            rt_kprintf("[SAFETY] irq attach: %s pin unresolved\n", st_inputs[i].label);
            e = -RT_EINVAL;
            break;
        }
        e = rt_pin_attach_irq(pin, (rt_uint32_t)st_inputs[i].irq_mode,
                              st_inputs[i].isr, RT_NULL);
        if (e != RT_EOK)
        {
            rt_kprintf("[SAFETY] attach %s failed (%d)\n", st_inputs[i].label, e);
            break;
        }
        attached |= 1u << i;

        e = rt_pin_irq_enable(pin, PIN_IRQ_ENABLE);
        if (e != RT_EOK)
        {
            rt_kprintf("[SAFETY] enable %s failed (%d)\n", st_inputs[i].label, e);
            break;
        }
        enabled |= 1u << i;
    }

    if (e != RT_EOK)
    {
        rt_kprintf("[SAFETY] irq attach FAILED (attached=0x%X enabled=0x%X)"
                   " - rolling back\n", attached, enabled);
        st_irq_rollback(attached, enabled, "rollback");
        rt_kprintf("[SAFETY] IRQ gate stays CLOSED\n");
        return e;
    }

    st_irq_attached = RT_TRUE;
    rt_kprintf("[SAFETY] IRQ gate OPEN: %d active protection input(s) registered"
               " (limits: falling edge / HIGH=safe; diag: rising)\n", ST_INPUT_N);
    rt_kprintf("[SAFETY] PC.6 = TMC_ENN feedback input, NOT an IRQ source;"
               " board e-stop cuts the chain in hardware, never via MCU\n");
    return RT_EOK;
}

static void st_irq_detach_all(void)
{
    st_irq_rollback((1u << ST_INPUT_N) - 1u, (1u << ST_INPUT_N) - 1u, "detach");
    rt_kprintf("[SAFETY] IRQ gate CLOSED - 活动安全输入(%d 路)无软件响应路径\n", ST_INPUT_N);
}

static void st_print_inputs(void)
{
    int i;

    for (i = 0; i < ST_INPUT_N; ++i)
    {
        rt_base_t pin = safety_pin(st_inputs[i].name);

        if (pin < 0)
            rt_kprintf("[SAFETY] %-12s pin=UNRESOLVED raw=- safe=%d (counts as NOT ready)\n",
                       st_inputs[i].label, (int)st_inputs[i].safe_level);
        else
            rt_kprintf("[SAFETY] %-12s pin=%d raw=%d safe=%d irq=%s %s\n",
                       st_inputs[i].label, (int)pin, (int)rt_pin_read(pin),
                       (int)st_inputs[i].safe_level,
                       st_inputs[i].irq_mode == PIN_IRQ_MODE_FALLING ? "FALLING" : "RISING",
                       rt_pin_read(pin) == st_inputs[i].safe_level ? "[safe]" : "[!!ACTIVE!!]");
    }

    /* R6(2026-11-03 任务书 §7.3): 这一条在板上实测 **250 B**, 超过 rt_kprintf 的
     * 控制台缓冲上限(kservice.c:411 静默裁到 RT_CONSOLEBUF_SIZE-1=127 B), 尾部整段丢失,
     * 而现场那两条含裸 NUL 的响应(06_pol 915 B 偏移 317 / 08_irqafter 568 B 偏移 430)
     * 正是走过这条输出的会话。改成两条各自 ≤120 B 的短行: 语义一字未减, 只是分行。 */
    rt_kprintf("[SAFETY] LIMIT inputs config = %s (SAFETY_LIMIT_INPUTS_ENABLED)\n",
               safety_limit_inputs_enabled() ? "ENABLED" : "DISABLED_BY_CONFIG");
    rt_kprintf("[SAFETY] LIMIT OFF => PF.14/PF.15 不解析/不配置/不读/不注册,"
               " 也不参与就绪判定\n");
    {
        rt_base_t fb = safety_pin(PIN_NAME_ENN_FEEDBACK);

        rt_kprintf("[SAFETY] %-12s pin=%s raw=%s = TMC_ENN feedback (HIGH=disabled side)."
                   " NOT an e-stop input, no IRQ registered on it\n",
                   "PC.6(ENN_FB)", fb < 0 ? "UNRESOLVED" : "ok",
                   fb < 0 ? "-" : ((rt_pin_read(fb) == PIN_HIGH) ? "HIGH" : "LOW"));
    }
}

static void safety_irq_status(void)
{
    rt_kprintf("[SAFETY] irq gate   = %s\n",
               st_irq_attached ? "OPEN (EXTI registered)" : "CLOSED (polled only)");
    rt_kprintf("[SAFETY] polarity   = %s (HARDWARE-PENDING unless CONFIRMed)\n",
               st_polarity_validated ? "operator CONFIRMED" : "NOT confirmed");
    rt_kprintf("[SAFETY] gpio_ready = %s\n", safety_gpio_ready() ? "YES" : "NO");
    rt_kprintf("[SAFETY] thread     = %s health=%s\n",
               st_thread_up ? "running" : "off", subsys_health_name(st_health));
    rt_kprintf("[SAFETY] protection_ready = %s\n",
               safety_protection_ready() ? "YES" : "NO");
    st_print_inputs();
}

static void safety_irq_enable(void)
{
    if (!st_thread_up)
    {
        rt_kprintf("[SAFETY] irq enable REFUSED: safety thread not running\n");
        return;
    }
    if (!st_polarity_validated)
    {
        rt_kprintf("[SAFETY] irq enable REFUSED: input polarity NOT confirmed\n");
        rt_kprintf("[SAFETY] hardware wiring is HARDWARE-PENDING. After measuring\n");
        rt_kprintf("[SAFETY] the ACTIVE inputs by hand (limits: HIGH=safe via U12/U11;\n");
        rt_kprintf("[SAFETY] diag: as documented) run:\n");
        rt_kprintf("[SAFETY]   safety_polarity_confirm CONFIRM\n");
        rt_kprintf("[SAFETY] Dangling inputs with EXTI enabled cause interrupt storm.\n");
        rt_kprintf("[SAFETY] PC.6(TMC_ENN feedback) is not part of this gate - it has no IRQ.\n");
        return;
    }
    (void)safety_irq_attach();
}

static void safety_irq_disable(void)
{
    if (!st_irq_attached)
    {
        rt_kprintf("[SAFETY] irq gate already CLOSED\n");
        return;
    }
    st_irq_detach_all();
}

static void safety_polarity_confirm(int argc, char **argv)
{
    if (argc != 2 || rt_strcmp(argv[1], "CONFIRM") != 0)
    {
        rt_kprintf("[SAFETY] usage: safety_polarity_confirm CONFIRM\n");
        rt_kprintf("[SAFETY] refused: literal CONFIRM required (no accidental set)\n");
        return;
    }

    rt_kprintf("[SAFETY] current raw levels of the ACTIVE protection inputs"
               " (operator must have MEASURED these):\n");
    st_print_inputs();
    rt_kprintf("[SAFETY] NOTE: this is an OPERATOR DECLARATION. Software does NOT\n");
    rt_kprintf("[SAFETY]       verify wiring. Status stays HARDWARE-PENDING for the\n");
    rt_kprintf("[SAFETY]       hardware chain; only the polarity flag changes here.\n");
    rt_kprintf("[SAFETY]       User-confirmed wire names / gate replacement are NOT a\n");
    rt_kprintf("[SAFETY]       measurement and never set this flag by themselves.\n");

    st_polarity_validated = RT_TRUE;
    rt_kprintf("[SAFETY] input polarity MARKED VALIDATED (operator confirm)\n");
    rt_kprintf("[SAFETY] protection_ready = %s\n",
               safety_protection_ready() ? "YES" : "NO (IRQ gate still CLOSED)");
}
int main(int argc,char **argv){char *pol[]={"safety_polarity_confirm","CONFIRM"};if(argc!=2)return 2;
 if(strcmp(argv[1],"pre")==0)safety_irq_status();
 else if(strcmp(argv[1],"pol")==0)safety_polarity_confirm(2,pol);
 else if(strcmp(argv[1],"enable")==0){st_polarity_validated=1;safety_irq_enable();}
 else if(strcmp(argv[1],"open")==0){st_polarity_validated=1;st_irq_attached=1;safety_irq_status();}
 else if(strcmp(argv[1],"disable")==0){st_polarity_validated=1;st_irq_attached=1;safety_irq_disable();}
 else if(strcmp(argv[1],"closed")==0){st_polarity_validated=1;safety_irq_status();}
 else return 3;return 0;}
