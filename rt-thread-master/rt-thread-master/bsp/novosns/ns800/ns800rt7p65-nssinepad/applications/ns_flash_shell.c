#include "ns_storage.h"
#include "ns_flash_config.h"
#include <finsh.h>
#include <string.h>
#include <stdlib.h>

static void result(const char *op,rt_err_t e)
{ rt_kprintf("[FLASH] %s: %s (%d)\n",op,e==RT_EOK?"PASS":"FAIL",e); }
static void flash_info(void)
{
    uint8_t id[3],sr[3];
    rt_err_t e=ns_flash_init();
    if(e==RT_EOK) e=ns_flash_identify(id,sr);
    if(e!=RT_EOK) {result("info",e);return;}
    rt_kprintf("bus=%s device=%s CS=%d SPI=%u Hz Mode0\n",
        NSF_BUS_NAME,NSF_DEVICE_NAME,(int)NSF_CS_PIN,(unsigned)NSF_SPI_HZ);
    rt_kprintf("JEDEC=%02X %02X %02X SR1=%02X SR2=%02X SR3=%02X\n",
        id[0],id[1],id[2],sr[0],sr[1],sr[2]);
    rt_kprintf("capacity=8388608 page=256 sector=4096\n"
        "params 000000-00FFFF (A:000000 B:001000)\n"
        "events 010000-10FFFF (4096 records; stop when full)\n"
        "waves  110000-7FEFFF (reserved)\n"
        "test   7FF000-7FFFFF\n");
}
MSH_CMD_EXPORT(flash_info, show flash ID status pins and partitions);
static void flash_init(void) {result("storage init",ns_storage_init());}
static void flash_unlock(void)
{
    uint8_t id[3],sr[3];
    rt_err_t e=ns_flash_init();
    if(e==RT_EOK) e=ns_flash_clearwp();
    if(e==RT_EOK) e=ns_flash_identify(id,sr);
    if(e!=RT_EOK) {result("unlock",e);return;}
    result("unlock (BP/WPS cleared)",e);
}
MSH_CMD_EXPORT(flash_unlock, clear BP/WPS write-protect bits in status registers);
MSH_CMD_EXPORT(flash_init, initialize storage and scan event log);

static void flash_test(int argc,char **argv)
{
    uint8_t *tx,*rx;
    unsigned i;
    rt_err_t e;
    if(argc!=2 || strcmp(argv[1],"run"))
    {rt_kprintf("flash_test run : erases ONLY 0x7FF000-0x7FFFFF\n");return;}
    e=ns_flash_init();
    if(e!=RT_EOK) {result("init",e);return;}
    tx=rt_malloc(600);rx=rt_malloc(NS_TEST_SIZE);
    if(!tx || !rx) {rt_free(tx);rt_free(rx);result("allocate",-RT_ENOMEM);return;}
    for(i=0;i<600;++i) tx[i]=(uint8_t)((i*37u) ^ (i>>3) ^ 0xa5u);
    rt_kprintf("[FLASH] erasing dedicated test sector...\n");
    e=ns_flash_erase(NS_TEST_BASE,NS_TEST_SIZE);
    if(e==RT_EOK) e=ns_flash_program(NS_TEST_BASE+240,tx,600);
    if(e==RT_EOK) e=ns_flash_read(NS_TEST_BASE,rx,NS_TEST_SIZE);
    if(e==RT_EOK)
    {
        for(i=0;i<NS_TEST_SIZE;++i)
        {
            uint8_t expected=(i>=240 && i<840)?tx[i-240]:0xff;
            if(rx[i]!=expected) {e=-RT_EIO;rt_kprintf("mismatch +0x%X\n",i);break;}
        }
    }
    /* Leave the deterministic data in flash for reset/power-cycle verification. */
    result("erase + 600-byte cross-page write + whole-sector compare",e);
    rt_free(rx);rt_free(tx);
}
MSH_CMD_EXPORT(flash_test, run destructive test in dedicated final sector);
static void flash_verify(void)
{
    uint8_t buf[128];
    uint32_t off;
    unsigned i;
    rt_err_t e=ns_flash_init();
    for(off=0;e==RT_EOK && off<NS_TEST_SIZE;off+=sizeof(buf))
    {
        e=ns_flash_read(NS_TEST_BASE+off,buf,sizeof(buf));
        if(e!=RT_EOK) break;
        for(i=0;i<sizeof(buf);++i)
        {
            uint32_t j=off+i;
            uint8_t expected=0xff;
            if(j>=240 && j<840) {j-=240;expected=(uint8_t)((j*37u)^(j>>3)^0xa5u);}
            if(buf[i]!=expected) {e=-RT_EIO;break;}
        }
    }
    result("read-only retention verify",e);
}
MSH_CMD_EXPORT(flash_verify, read back last test after reset without writing);
static void flash_logstat(void)
{
    ns_log_stats_t s;
    rt_err_t e=ns_log_stats(&s);
    if(e!=RT_EOK) {result("logstat - run flash_init first",e);return;}
    rt_kprintf("slots=%u/%u valid=%u bad=%u\n",(unsigned)s.used_slots,
        (unsigned)NS_LOG_SLOTS,(unsigned)s.valid_records,(unsigned)s.bad_records);
    rt_kprintf("queued=%u completed=%u failed=%u queue_dropped=%u last_error=%d\n",
        (unsigned)s.accepted,(unsigned)s.completed,(unsigned)s.failed,
        (unsigned)s.dropped,s.last_error);
}
MSH_CMD_EXPORT(flash_logstat, show event storage and async worker status);
static void flash_event(void)
{
    ns_fault_sample_t s;
    rt_err_t e;
    memset(&s,0,sizeof(s));
    s.event=NS_EVENT_TEST;
    s.time_ms=(uint32_t)(((uint64_t)rt_tick_get()*1000u)/RT_TICK_PER_SECOND);
    /* No fabricated IMU/current/SG readings; valid remains zero. */
    e=ns_log_submit(&s);
    if(e==RT_EOK) e=ns_log_flush(5000);
    result("queue + persistent event (valid=0)",e);
}
MSH_CMD_EXPORT(flash_event, submit one test event then wait for persistence);
static void flash_dump(int argc,char **argv)
{
    uint32_t first=0,count=10,i,seq;
    ns_fault_sample_t s;
    rt_err_t e;
    char *end;
    unsigned long value;
    if(argc>3) {rt_kprintf("flash_dump [first_slot] [count<=100]\n");return;}
    if(argc>1) {value=strtoul(argv[1],&end,0);if(*end || value>=NS_LOG_SLOTS)return;first=(uint32_t)value;}
    if(argc>2) {value=strtoul(argv[2],&end,0);if(*end || value>100 || !value)return;count=(uint32_t)value;}
    rt_kprintf("slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,az_mg,step_hz,vibration_mg,flags\n");
    for(i=first;i<NS_LOG_SLOTS && i<first+count;++i)
    {
        e=ns_log_read_slot(i,&s,&seq);
        if(e==-RT_EEMPTY) continue;
        if(e!=RT_EOK) {rt_kprintf("slot %u invalid/read error %d\n",(unsigned)i,e);continue;}
        rt_kprintf("%u,%u,%u,%u,%u,%u,%d,%d,%d,%d,%d,%d,%d,%u\n",(unsigned)i,
            (unsigned)seq,(unsigned)s.session_id,(unsigned)s.time_ms,(unsigned)s.event,
            (unsigned)s.valid,(int)s.sg,(int)s.current_ma,(int)s.ax_mg,(int)s.ay_mg,
            (int)s.az_mg,(int)s.step_hz,(int)s.vibration_mg,(unsigned)s.flags);
    }
}
MSH_CMD_EXPORT(flash_dump, export event slots as CSV);
static void flash_logclear(int argc,char **argv)
{
    if(argc!=2 || strcmp(argv[1],"ERASE"))
    {rt_kprintf("flash_logclear ERASE : deletes ALL events (1 MiB); may take minutes\n");return;}
    result("clear event partition",ns_log_clear());
}
MSH_CMD_EXPORT(flash_logclear, explicitly erase the event partition);
static void flash_params(int argc,char **argv)
{
    char buf[NS_PARAM_MAX+1];
    rt_size_t n=0;
    rt_err_t e;
    if(argc==3 && !strcmp(argv[1],"write"))
    {
        n=strlen(argv[2]);
        e=ns_params_save(argv[2],n);
        result("save parameters",e);
    }
    else if(argc==2 && !strcmp(argv[1],"read"))
    {
        e=ns_params_load(buf,NS_PARAM_MAX,&n);
        if(e==RT_EOK) {buf[n]=0;rt_kprintf("params[%u]=%s\n",(unsigned)n,buf);}
        else result("load parameters",e);
    }
    else rt_kprintf("flash_params read | flash_params write <one_word>\n"
                    "write replaces saved application parameters\n");
}
MSH_CMD_EXPORT(flash_params, read or replace demo parameter string);
