from pathlib import Path
import hashlib, json, re, subprocess
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'repo/rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad/applications'
src = (APP / 'safety_thread.c').read_text()
def function(name):
    start = re.search(r'^(?:static\s+)?(?:void|rt_err_t)\s+' + name + r'\([^;]*?\)\s*\{', src, re.M).start()
    left = src.index('{', start)
    # Remove strings/comments only for brace matching; preserve indices and emitted source.
    masked = re.sub(r'"(?:[^"\\]|\\.)*"|/\*.*?\*/|//[^\n]*', lambda m: ' ' * len(m[0]), src, flags=re.S)
    depth = 0
    for i in range(left, len(src)):
        depth += (masked[i] == '{') - (masked[i] == '}')
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError(name)

prefix = r'''
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
'''
names = ['st_irq_rollback', 'safety_irq_attach', 'st_irq_detach_all', 'st_print_inputs', 'safety_irq_status', 'safety_irq_enable', 'safety_irq_disable', 'safety_polarity_confirm']
main = r'''
int main(int argc,char **argv){char *pol[]={"safety_polarity_confirm","CONFIRM"};if(argc!=2)return 2;
 if(strcmp(argv[1],"pre")==0)safety_irq_status();
 else if(strcmp(argv[1],"pol")==0)safety_polarity_confirm(2,pol);
 else if(strcmp(argv[1],"enable")==0){st_polarity_validated=1;safety_irq_enable();}
 else if(strcmp(argv[1],"open")==0){st_polarity_validated=1;st_irq_attached=1;safety_irq_status();}
 else if(strcmp(argv[1],"disable")==0){st_polarity_validated=1;st_irq_attached=1;safety_irq_disable();}
 else if(strcmp(argv[1],"closed")==0){st_polarity_validated=1;safety_irq_status();}
 else return 3;return 0;}
'''
d = ROOT / 'package/a_review/source_prints'
d.mkdir(parents=True, exist_ok=True)
c = prefix + '\n\n'.join(function(n) for n in names) + main
(d / 'extracted_source.c').write_text(c)
build = subprocess.run(['cc', '-std=c99', '-Wall', '-Wextra', '-Werror', '-Wno-misleading-indentation', str(d/'extracted_source.c'), '-o', str(ROOT/'checks/source_prints')], capture_output=True)
(d / 'compile.stdout.txt').write_bytes(build.stdout)
(d / 'compile.stderr.txt').write_bytes(build.stderr)
assert build.returncode == 0, build.stderr
rows = json.loads((ROOT/'package/reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/rows_r7f5_b502_blind_irq_static.json').read_text())['commands']
report={'scope':'Extracted production print/control code with host GPIO/IRQ/ready stubs. Output-shape verification only, NOT hardware response, IRQ delivery, timing, or protection proof.', 'cases':[]}
for mode,key in [('pre','safety_irq_status@pre'),('pol','safety_polarity_confirm CONFIRM'),('enable','safety_irq_enable'),('open','safety_irq_status@open'),('disable','safety_irq_disable'),('closed','safety_irq_status@closed')]:
    r=subprocess.run([str(ROOT/'checks/source_prints'),mode],capture_output=True)
    (d/(mode+'.stdout.bin')).write_bytes(r.stdout)
    (d/(mode+'.calls.txt')).write_bytes(r.stderr)
    assert r.returncode == 0 and b'\0' not in r.stdout
    got=r.stdout.decode('utf-8').splitlines()
    want=[s for s in rows[key]['healthy'] if s.startswith('[SAFETY]')]
    assert got==want,(mode,got,want)
    lengths=[int(n) for n in re.findall(rb'intended=(\d+)',r.stderr)]
    assert max(lengths)<=127
    report['cases'].append({'case':mode,'rows':len(got),'all_rows_exact':True,'bytes':len(r.stdout),'sha256':hashlib.sha256(r.stdout).hexdigest(),'max_call_bytes_including_LF':max(lengths),'nul':0})
supervisor=(APP/'supervisor.c').read_text()
assert 'safety_polarity_validated() ? "CONFIRMED" : "not-confirmed"' in supervisor
report['supervisor_flag_spelling_source_checked']='CONFIRMED (uppercase)'
(d/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
