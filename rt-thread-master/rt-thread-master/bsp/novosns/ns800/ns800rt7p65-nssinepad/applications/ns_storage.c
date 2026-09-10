#include "ns_storage.h"
#include "ns_flash_config.h"
#include <string.h>

#define LOG_MAGIC 0x31474f4cu /* little endian LOG1 */
#define CFG_MAGIC 0x31474643u /* little endian CFG1 */
#define COMMITTED 0x54494d43u /* little endian CMIT */
static struct rt_mutex service_lock, queue_lock;
static struct rt_messagequeue queue;
static struct rt_thread worker;
rt_align(RT_ALIGN_SIZE)
static rt_uint8_t worker_stack[NS_LOG_STACK_SIZE];
rt_align(RT_ALIGN_SIZE)
static rt_uint8_t queue_pool[RT_MQ_BUF_SIZE(sizeof(ns_fault_sample_t),
                                          NS_LOG_QUEUE_DEPTH)];
static int resources, online, worker_started, accepting;
static uint32_t used_slots, valid_records, bad_records, next_sequence;
static uint32_t accepted, completed, failed, dropped;
static rt_err_t last_error;

static void put32(uint8_t *p, uint32_t v)
{ p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24); }
static uint32_t get32(const uint8_t *p)
{ return (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24); }
static uint32_t crc32(const uint8_t *p, rt_size_t n)
{
    uint32_t c=0xffffffffu;
    unsigned j;
    while(n--) { c ^= *p++; for(j=0;j<8;++j) c=(c>>1) ^ (0xedb88320u & (0u-(c&1u))); }
    return ~c;
}
static int blank(const uint8_t *p)
{
    unsigned i;
    for(i=0;i<256;++i) if(p[i]!=0xff) return 0;
    return 1;
}
static int valid_page(const uint8_t *p, uint32_t magic)
{
    return get32(p)==magic && get32(p+4)==1 &&
           get32(p+252)==COMMITTED && get32(p+248)==crc32(p,248);
}
static void seal(uint8_t *p)
{ put32(p+248,crc32(p,248)); put32(p+252,COMMITTED); }
static rt_err_t commit_page(uint32_t a, uint8_t *p)
{
    rt_err_t e=ns_flash_program(a,p,252);
    /* Payload and CRC are verified before the separate commit write. */
    if(e==RT_EOK) e=ns_flash_program(a+252,p+252,4);
    return e;
}
static int newer(uint32_t a, uint32_t b)
{ return a!=b && (uint32_t)(a-b)<0x80000000u; }
static rt_err_t take_service(void)
{
    rt_err_t e;
    if(!resources || rt_interrupt_get_nest()) return -RT_ERROR;
    e=rt_mutex_take(&service_lock,RT_WAITING_FOREVER);
    if(e!=RT_EOK) return e;
    if(!online) {rt_mutex_release(&service_lock);return -RT_ERROR;}
    return RT_EOK;
}
static rt_err_t scan_log(void)
{
    uint8_t page[256];
    uint32_t i,seq,maxseq=0;
    int have_seq=0;
    rt_err_t e;
    used_slots=valid_records=bad_records=0;
    for(i=0;i<NS_LOG_SLOTS;++i)
    {
        e=ns_flash_read(NS_LOG_BASE+i*256u,page,256);
        if(e!=RT_EOK) return e;
        if(blank(page)) continue;
        /* Do not reuse any page before the last nonblank page after a reset. */
        used_slots=i+1;
        if(valid_page(page,LOG_MAGIC))
        {
            ++valid_records;seq=get32(page+8);
            if(!have_seq || newer(seq,maxseq)) {maxseq=seq;have_seq=1;}
        }
        else ++bad_records;
        if((i&63u)==63u) rt_thread_mdelay(1);
    }
    next_sequence=have_seq ? maxseq+1 : 1;
    return RT_EOK;
}
static void encode_sample(uint8_t *p,const ns_fault_sample_t *s,uint32_t seq)
{
    memset(p,0xff,256);
    put32(p,LOG_MAGIC);put32(p+4,1);put32(p+8,seq);
    put32(p+12,s->session_id);put32(p+16,s->time_ms);
    put32(p+20,s->event);put32(p+24,s->valid);
    put32(p+28,(uint32_t)s->sg);put32(p+32,(uint32_t)s->current_ma);
    put32(p+36,(uint32_t)s->ax_mg);put32(p+40,(uint32_t)s->ay_mg);
    put32(p+44,(uint32_t)s->az_mg);put32(p+48,(uint32_t)s->step_hz);
    put32(p+52,(uint32_t)s->vibration_mg);put32(p+56,s->flags);
    seal(p);
}
static void decode_sample(const uint8_t *p,ns_fault_sample_t *s)
{
    s->session_id=get32(p+12);s->time_ms=get32(p+16);s->event=get32(p+20);
    s->valid=get32(p+24);s->sg=(int32_t)get32(p+28);s->current_ma=(int32_t)get32(p+32);
    s->ax_mg=(int32_t)get32(p+36);s->ay_mg=(int32_t)get32(p+40);
    s->az_mg=(int32_t)get32(p+44);s->step_hz=(int32_t)get32(p+48);
    s->vibration_mg=(int32_t)get32(p+52);s->flags=get32(p+56);
}
static void log_worker(void *unused)
{
    ns_fault_sample_t sample;
    rt_err_t e;
    (void)unused;
    for(;;)
    {
        e=rt_mq_recv(&queue,&sample,sizeof(sample),RT_WAITING_FOREVER);
        if(e<0) continue; /* RT-Thread versions may return 0 or received length. */
        e=ns_log_append(&sample);
        rt_mutex_take(&queue_lock,RT_WAITING_FOREVER);
        ++completed;
        if(e!=RT_EOK) {++failed;last_error=e;}
        rt_mutex_release(&queue_lock);
    }
}
static int storage_resources(void)
{
    rt_err_t e;
    e=rt_mutex_init(&service_lock,"slock",RT_IPC_FLAG_PRIO);
    if(e!=RT_EOK) return e;
    e=rt_mutex_init(&queue_lock,"qlock",RT_IPC_FLAG_PRIO);
    if(e!=RT_EOK) return e;
    e=rt_mq_init(&queue,"flogq",queue_pool,sizeof(ns_fault_sample_t),
                 sizeof(queue_pool),RT_IPC_FLAG_FIFO);
    if(e!=RT_EOK) return e;
    resources=1;
    return RT_EOK;
}
INIT_APP_EXPORT(storage_resources);
rt_err_t ns_storage_init(void)
{
    rt_err_t e;
    if(!resources || rt_interrupt_get_nest()) return -RT_ERROR;
    rt_mutex_take(&service_lock,RT_WAITING_FOREVER);
    if(online) {rt_mutex_release(&service_lock);return RT_EOK;}
    e=ns_flash_init();
    if(e==RT_EOK)
    {
        rt_kprintf("[FLASH] scanning 1 MiB event log...\n");
        e=scan_log();
    }
    if(e==RT_EOK && !worker_started)
    {
        e=rt_thread_init(&worker,"flog",log_worker,RT_NULL,worker_stack,
                         sizeof(worker_stack),NS_LOG_PRIORITY,10);
        if(e==RT_EOK) e=rt_thread_startup(&worker);
        if(e==RT_EOK) worker_started=1;
    }
    if(e==RT_EOK)
    {
        online=1;
        rt_mutex_take(&queue_lock,RT_WAITING_FOREVER);
        accepting=1;
        rt_mutex_release(&queue_lock);
    }
    rt_mutex_release(&service_lock);
    return e;
}
rt_err_t ns_log_append(const ns_fault_sample_t *s)
{
    uint8_t page[256];
    uint32_t slot;
    rt_err_t e;
    if(!s) return -RT_EINVAL;
    if((e=take_service())!=RT_EOK) return e;
    if(used_slots>=NS_LOG_SLOTS) {e=-RT_EFULL;goto out;}
    slot=used_slots;
    e=ns_flash_read(NS_LOG_BASE+slot*256u,page,256);
    if(e!=RT_EOK) goto out;
    if(!blank(page)) {e=-RT_EBUSY;goto out;}
    encode_sample(page,s,next_sequence++);
    /* Consume even a failed write in this session: never overwrite torn data. */
    ++used_slots;
    e=commit_page(NS_LOG_BASE+slot*256u,page);
    if(e==RT_EOK) ++valid_records; else ++bad_records;
out:
    rt_mutex_release(&service_lock);
    return e;
}
rt_err_t ns_log_submit(const ns_fault_sample_t *s)
{
    rt_err_t e;
    if(!s) return -RT_EINVAL;
    if(!resources || rt_interrupt_get_nest()) return -RT_ERROR;
    /* Never wait for storage I/O, even if called by a high-priority detector. */
    e=rt_mutex_take(&queue_lock,0);
    if(e!=RT_EOK) return e;
    if(!accepting) e=-RT_EBUSY;
    else
    {
        e=rt_mq_send(&queue,s,sizeof(*s));
        if(e==RT_EOK) ++accepted; else ++dropped;
    }
    rt_mutex_release(&queue_lock);
    return e;
}
rt_err_t ns_log_flush(int timeout_ms)
{
    rt_tick_t start=rt_tick_get(), limit;
    rt_err_t e;
    int done;
    if(!resources || rt_interrupt_get_nest() || timeout_ms<0) return -RT_EINVAL;
    limit=rt_tick_from_millisecond(timeout_ms);
    for(;;)
    {
        rt_mutex_take(&queue_lock,RT_WAITING_FOREVER);
        done=(accepted==completed);e=accepting ? last_error : -RT_ERROR;
        rt_mutex_release(&queue_lock);
        if(done) return e;
        if((rt_tick_t)(rt_tick_get()-start)>=limit) return -RT_ETIMEOUT;
        rt_thread_mdelay(1);
    }
}
rt_err_t ns_log_stats(ns_log_stats_t *s)
{
    rt_err_t e;
    if(!s) return -RT_EINVAL;
    if((e=take_service())!=RT_EOK) return e;
    s->used_slots=used_slots;s->valid_records=valid_records;s->bad_records=bad_records;
    rt_mutex_take(&queue_lock,RT_WAITING_FOREVER);
    s->accepted=accepted;s->completed=completed;s->failed=failed;s->dropped=dropped;
    s->last_error=last_error;
    rt_mutex_release(&queue_lock);
    rt_mutex_release(&service_lock);
    return RT_EOK;
}
rt_err_t ns_log_read_slot(uint32_t slot,ns_fault_sample_t *s,uint32_t *seq)
{
    uint8_t p[256];
    rt_err_t e;
    if(!s || !seq || slot>=NS_LOG_SLOTS) return -RT_EINVAL;
    if((e=take_service())!=RT_EOK) return e;
    e=ns_flash_read(NS_LOG_BASE+slot*256u,p,256);
    if(e==RT_EOK)
    {
        if(blank(p)) e=-RT_EEMPTY;
        else if(!valid_page(p,LOG_MAGIC)) e=-RT_EIO;
        else {decode_sample(p,s);*seq=get32(p+8);}
    }
    rt_mutex_release(&service_lock);
    return e;
}
rt_err_t ns_log_clear(void)
{
    rt_err_t e, scan_e;
    if((e=take_service())!=RT_EOK) return e;
    rt_mutex_take(&queue_lock,RT_WAITING_FOREVER);
    if(accepted!=completed)
    {rt_mutex_release(&queue_lock);rt_mutex_release(&service_lock);return -RT_EBUSY;}
    accepting=0;
    rt_mutex_release(&queue_lock);
    e=ns_flash_erase(NS_LOG_BASE,NS_LOG_SIZE);
    /* Rescan even on failure: clear may have erased only part of the area. */
    scan_e=scan_log();
    if(scan_e!=RT_EOK) {online=0;if(e==RT_EOK)e=scan_e;}
    rt_mutex_take(&queue_lock,RT_WAITING_FOREVER);
    accepting=online;
    if(e==RT_EOK) {accepted=completed=failed=dropped=0;last_error=RT_EOK;}
    rt_mutex_release(&queue_lock);
    rt_mutex_release(&service_lock);
    return e;
}
/* Caller holds service_lock. Read both copies; never erase on transport failure. */
static rt_err_t cfg_select(uint8_t a[256],uint8_t b[256],int *selected)
{
    int va,vb;
    rt_err_t e=ns_flash_read(NS_PARAM_BASE,a,256);
    if(e!=RT_EOK) return e;
    e=ns_flash_read(NS_PARAM_BASE+4096,b,256);
    if(e!=RT_EOK) return e;
    va=valid_page(a,CFG_MAGIC) && get32(a+12)<=NS_PARAM_MAX;
    vb=valid_page(b,CFG_MAGIC) && get32(b+12)<=NS_PARAM_MAX;
    *selected=va ? (vb && newer(get32(b+8),get32(a+8)) ? 1:0) : (vb ? 1:-1);
    return RT_EOK;
}
rt_err_t ns_params_save(const void *data,rt_size_t n)
{
    uint8_t a[256],b[256];
    uint32_t generation,address;
    int selected;
    rt_err_t e;
    if(!data || !n || n>NS_PARAM_MAX) return -RT_EINVAL;
    if((e=take_service())!=RT_EOK) return e;
    e=cfg_select(a,b,&selected);
    if(e==RT_EOK)
    {
        generation=selected<0 ? 1 : get32((selected?b:a)+8)+1;
        address=NS_PARAM_BASE+(selected==0 ? 4096u:0u);
        memset(a,0xff,sizeof(a));put32(a,CFG_MAGIC);put32(a+4,1);
        put32(a+8,generation);put32(a+12,(uint32_t)n);memcpy(a+16,data,n);seal(a);
        e=ns_flash_erase(address,4096);
        if(e==RT_EOK) e=commit_page(address,a);
    }
    rt_mutex_release(&service_lock);
    return e;
}
rt_err_t ns_params_load(void *data,rt_size_t cap,rt_size_t *n)
{
    uint8_t a[256],b[256],*p;
    int selected;
    rt_err_t e;
    if(!n || (cap && !data)) return -RT_EINVAL;
    *n=0;
    if((e=take_service())!=RT_EOK) return e;
    e=cfg_select(a,b,&selected);
    if(e==RT_EOK)
    {
        if(selected<0) e=-RT_EEMPTY;
        else {p=selected?b:a;*n=get32(p+12);
              if(cap<*n) e=-RT_EFULL; else memcpy(data,p+16,*n);}
    }
    rt_mutex_release(&service_lock);
    return e;
}
static int wave_range(uint32_t a,rt_size_t n)
{ return a<=NS_WAVE_SIZE && n<=NS_WAVE_SIZE-a; }
rt_err_t ns_wave_read(uint32_t a,void *p,rt_size_t n)
{ return wave_range(a,n) ? ns_flash_read(NS_WAVE_BASE+a,p,n) : -RT_EINVAL; }
rt_err_t ns_wave_program(uint32_t a,const void *p,rt_size_t n)
{ return wave_range(a,n) ? ns_flash_program(NS_WAVE_BASE+a,p,n) : -RT_EINVAL; }
rt_err_t ns_wave_erase(uint32_t a,rt_size_t n)
{ return wave_range(a,n) ? ns_flash_erase(NS_WAVE_BASE+a,n) : -RT_EINVAL; }
