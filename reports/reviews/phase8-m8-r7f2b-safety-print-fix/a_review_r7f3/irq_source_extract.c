#include <stdio.h>
#include <stdarg.h>
#include <stddef.h>
#define ST_INPUT_N 1
#define PIN_IRQ_MODE_FALLING 1
#define PIN_HIGH 1
#define PIN_NAME_ENN_FEEDBACK "PC.6"
typedef int rt_base_t;
struct {const char *name,*label;int safe_level,irq_mode;} st_inputs[]={{"DIAG","TMC_DIAG",0,2}};
static int st_irq_attached=0,st_polarity_validated=0,st_thread_up=1,st_health=0;
static int safety_pin(const char *name){return name[0]=='P'?6:3;}
static int rt_pin_read(int pin){return pin==6?1:0;}
static int safety_limit_inputs_enabled(void){return 0;}
static int safety_gpio_ready(void){return 1;}
static int safety_protection_ready(void){return 0;}
static const char *subsys_health_name(int v){(void)v;return "OK";}
static int rt_kprintf(const char *fmt,...){char buf[128];va_list ap;int n,k;
va_start(ap,fmt);n=vsnprintf(buf,127,fmt,ap);va_end(ap);if(n<0)return n;
k=n>127?127:n;fprintf(stderr,"CALL intended=%d emitted=%d\n",n,k);fwrite(buf,1,(size_t)k,stdout);return k;}
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

int main(void){safety_irq_status();return 0;}
