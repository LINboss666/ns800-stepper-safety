/*
 * flash_main.c - M5 flash_dump 导出边界链 (2026-10-29)
 *
 * 真编译 applications/ns_flash_shell.c(不是复制伪实现), 直接调用其 static
 * flash_dump() 入口; RT 控制台桩按目标语义建模:
 *   rt_kprintf : rt_vsnprintf(buf, 127) 后按 min(len,127) 原样 _kputs
 *                => 长行被剪断且把结尾 NUL 也送进流(目标已知行为)
 *   rt_kputs   : _kputs(str, strlen) 完整输出(与 BSP kservice.c 一致)
 * 因此"导出退回单次受限长 rt_kprintf"这一缺陷可被边界用例真实检出。
 *
 * 全部 ns_flash_* / ns_storage_* / ns_log_* 是显式可注入桩; 本链**不**证明
 * Flash 驱动、页布局、CRC 或真实落盘, 只证明 CSV 导出格式与参数/错误边界。
 *
 * 用例 id: FE01..FE05(正常构建) / FE06(仅 -DFLASH_CAP_PROBE, 容量不足分支)。
 * 退出码: 0=全 PASS; 1=有 FAIL; 2=夹具前置故障。
 */
#include <rtthread.h>
#include <stdint.h>
#include <inttypes.h>
#include <limits.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif

#include "ns_storage.h"

/* ======================= 输出捕获 ======================= */
#define FL_CAP_SIZE (1u << 20)
static char   fl_cap[FL_CAP_SIZE];
static size_t fl_cap_len;
static int    fl_cap_overflow;
static int    fl_kputs_calls;
static int    fl_kprintf_calls;

static void cap_reset(void)
{
    fl_cap_len = 0;
    fl_cap[0] = 0;
    fl_cap_overflow = 0;
    fl_kputs_calls = 0;
    fl_kprintf_calls = 0;
}

static void cap_add(const char *s, size_t n)
{
    if (fl_cap_len + n >= FL_CAP_SIZE) { fl_cap_overflow = 1; return; }
    memcpy(fl_cap + fl_cap_len, s, n);
    fl_cap_len += n;
    fl_cap[fl_cap_len] = 0;
}

void rt_kputs(const char *str)
{
    if (str == RT_NULL) return;
    fl_kputs_calls++;
    cap_add(str, strlen(str));
}

void rt_kprintf(const char *fmt, ...)
{
    /* 目标 rt_kprintf: rt_vsnprintf(buf,127) 后按 min(len,127) 原样 _kputs。
     * 先用足够大的临时区拿到真实长度(MinGW 的 vsnprintf 截断时返回 -1,
     * 不能直接拿它模拟目标), 再按目标 126 字符 + 结尾 NUL 的语义送入捕获。 */
    char tmp[2048];
    va_list ap;
    int n;

    va_start(ap, fmt);
    n = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    fl_kprintf_calls++;
    if (n < 0) return;
    if ((size_t)n < sizeof(tmp))
    {
        size_t emit;
        if ((size_t)n <= 126) emit = (size_t)n;
        else { tmp[126] = '\0'; emit = 127; }
        cap_add(tmp, emit);
    }
}

void *rt_malloc(rt_size_t size) { return malloc(size); }
void rt_free(void *ptr)         { free(ptr); }
rt_tick_t rt_tick_get(void)     { return 0u; }

/* ======================= 可注入 Flash 服务 ======================= */
enum { SLOT_EMPTY = 0, SLOT_OK = 1, SLOT_ERR = 2 };

typedef struct {
    int state;
    ns_fault_sample_t sample;
    uint32_t seq;
    rt_err_t err;
} fl_slot_t;

static fl_slot_t fl_slots[NS_LOG_SLOTS];
static unsigned  fl_reads;
static unsigned  fl_reads_at_slot[NS_LOG_SLOTS];

static void model_reset(void)
{
    memset(fl_slots, 0, sizeof(fl_slots));
    memset(fl_reads_at_slot, 0, sizeof(fl_reads_at_slot));
    fl_reads = 0;
    cap_reset();
}

static void slot_set_ok(unsigned slot, const ns_fault_sample_t *s, uint32_t seq)
{
    fl_slots[slot].state = SLOT_OK;
    fl_slots[slot].sample = *s;
    fl_slots[slot].seq = seq;
}

static void slot_set_err(unsigned slot, rt_err_t err)
{
    fl_slots[slot].state = SLOT_ERR;
    fl_slots[slot].err = err;
}

rt_err_t ns_log_read_slot(uint32_t slot, ns_fault_sample_t *out, uint32_t *sequence)
{
    fl_reads++;
    if (slot < NS_LOG_SLOTS) fl_reads_at_slot[slot]++;
    if (slot >= NS_LOG_SLOTS) return -RT_EINVAL;
    if (fl_slots[slot].state == SLOT_EMPTY) return -RT_EEMPTY;
    if (fl_slots[slot].state == SLOT_ERR)   return fl_slots[slot].err;
    *out = fl_slots[slot].sample;
    *sequence = fl_slots[slot].seq;
    return RT_EOK;
}

rt_err_t ns_flash_init(void) { return RT_EOK; }
rt_err_t ns_flash_identify(uint8_t id[3], uint8_t sr[3])
{ (void)id; (void)sr; return RT_EOK; }
rt_err_t ns_flash_read(uint32_t address, void *data, rt_size_t length)
{ (void)address; (void)data; (void)length; return RT_EOK; }
rt_err_t ns_flash_program(uint32_t address, const void *data, rt_size_t length)
{ (void)address; (void)data; (void)length; return RT_EOK; }
rt_err_t ns_flash_erase(uint32_t address, rt_size_t length)
{ (void)address; (void)length; return RT_EOK; }
rt_err_t ns_flash_clearwp(void) { return RT_EOK; }
rt_err_t ns_storage_init(void)  { return RT_EOK; }
rt_err_t ns_log_submit(const ns_fault_sample_t *sample) { (void)sample; return RT_EOK; }
rt_err_t ns_log_flush(int timeout_ms) { (void)timeout_ms; return RT_EOK; }
rt_err_t ns_log_stats(ns_log_stats_t *out)
{ if (out) memset(out, 0, sizeof(*out)); return RT_EOK; }
rt_err_t ns_log_clear(void) { return RT_EOK; }
rt_err_t ns_params_save(const void *data, rt_size_t length)
{ (void)data; (void)length; return RT_EOK; }
rt_err_t ns_params_load(void *data, rt_size_t capacity, rt_size_t *length)
{ (void)data; (void)capacity; if (length) *length = 0; return RT_EOK; }

/* ======================= 被测生产代码 ======================= */
#include "ns_flash_shell.c"

/* ======================= 判定小工具 ======================= */
static int fl_cases, fl_pass, fl_fail;

static const char *fl_fm(const char *fmt, ...)
{
    static char b[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof(b), fmt, ap);
    va_end(ap);
    return b;
}

static void fl_check(const char *id, int ok, const char *detail)
{
    fl_cases++;
    if (ok) fl_pass++; else fl_fail++;
    printf("CASE|%s|%s|%s\n", id, ok ? "PASS" : "FAIL", detail);
}

static int cap_has(const char *needle)
{
    size_t n = strlen(needle);
    size_t i;
    if (n == 0 || n > fl_cap_len) return 0;
    for (i = 0; i + n <= fl_cap_len; ++i)
        if (memcmp(fl_cap + i, needle, n) == 0) return 1;
    return 0;
}

static int cap_has_nul(void)
{
    size_t i;
    for (i = 0; i < fl_cap_len; ++i)
        if (fl_cap[i] == '\0') return 1;
    return 0;
}

static int cap_ends_lf(void)
{
    return fl_cap_len > 0 && fl_cap[fl_cap_len - 1] == '\n';
}

static size_t line_len_at(size_t start)
{
    size_t i = start;
    while (i < fl_cap_len && fl_cap[i] != '\n') i++;
    return i - start;
}

/* 严格十进制: 可选负号 + 数字, 无前导空格/尾随; 溢出返回 0。 */
static int parse_i64(const char *s, size_t n, int64_t *out)
{
    size_t i = 0;
    int neg = 0;
    uint64_t v = 0;
    if (n == 0) return 0;
    if (s[i] == '-') { neg = 1; i++; if (i == n) return 0; }
    for (; i < n; ++i)
    {
        unsigned d;
        if (s[i] < '0' || s[i] > '9') return 0;
        d = (unsigned)(s[i] - '0');
        if (v > (UINT64_MAX - d) / 10u) return 0;
        v = v * 10u + d;
    }
    if (!neg && v > (uint64_t)INT64_MAX) return 0;
    if (neg && v > (uint64_t)INT64_MAX + 1u) return 0;
    *out = neg ? -(int64_t)v : (int64_t)v;
    return 1;
}

/* 解析一条 CSV 行(不含 LF)为 14 个 int64; 每列都必须是完整十进制。 */
static int parse_csv14(const char *s, size_t n, int64_t v[14])
{
    size_t i = 0, start = 0, col = 0;
    for (i = 0; i <= n; ++i)
    {
        if (i == n || s[i] == ',')
        {
            if (col >= 14) return 0;
            if (!parse_i64(s + start, i - start, &v[col])) return 0;
            col++;
            start = i + 1;
        }
    }
    return col == 14;
}

static const char *const FL_HEADER =
    "slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,az_mg,"
    "step_hz,vibration_mg,flags\n";

static int is_csv_header(const char *line, size_t n)
{
    size_t h = strlen(FL_HEADER) - 1;
    return n == h && memcmp(line, FL_HEADER, h) == 0;
}

static int csv_line_count(void)
{
    size_t i = 0;
    int count = 0;
    while (i < fl_cap_len)
    {
        size_t n = line_len_at(i);
        if (n > 0 && memchr(fl_cap + i, ',', n) != RT_NULL &&
            !is_csv_header(fl_cap + i, n))
            count++;
        i += n + 1;
    }
    return count;
}

/* 返回第 k 条 CSV 数据行(跳过表头)的起点; 找不到返回 (size_t)-1 */
static size_t csv_data_line(size_t k)
{
    size_t i = 0;
    int seen_header = 0, seen = 0;
    while (i < fl_cap_len)
    {
        size_t n = line_len_at(i);
        if (n > 0 && memchr(fl_cap + i, ',', n) != RT_NULL)
        {
            if (is_csv_header(fl_cap + i, n)) seen_header = 1;
            else if (seen_header && seen++ == (int)k) return i;
        }
        i += n + 1;
    }
    return (size_t)-1;
}

static void dump_call(unsigned first, unsigned count, int with_count)
{
    char a[16], b[16];
    char *argv[3];
    snprintf(a, sizeof(a), "%u", first);
    argv[0] = (char *)"flash_dump";
    argv[1] = a;
    if (with_count)
    {
        snprintf(b, sizeof(b), "%u", count);
        argv[2] = b;
        flash_dump(3, argv);
    }
    else
    {
        flash_dump(2, argv);
    }
}

static void sample_normal(ns_fault_sample_t *s)
{
    memset(s, 0, sizeof(*s));
    s->session_id = 3;
    s->time_ms = 123456;
    s->event = 9;
    s->valid = 31;
    s->sg = -6;
    s->current_ma = -7;
    s->ax_mg = -8;
    s->ay_mg = -9;
    s->az_mg = -10;
    s->step_hz = -11;
    s->vibration_mg = -12;
    s->flags = 13;
}

static void sample_extreme(ns_fault_sample_t *s)
{
    s->session_id = UINT32_MAX;
    s->time_ms = UINT32_MAX;
    s->event = UINT32_MAX;
    s->valid = UINT32_MAX;
    s->sg = INT32_MIN;
    s->current_ma = INT32_MAX;
    s->ax_mg = INT32_MIN;
    s->ay_mg = INT32_MAX;
    s->az_mg = INT32_MIN;
    s->step_hz = INT32_MAX;
    s->vibration_mg = INT32_MIN;
    s->flags = UINT32_MAX;
}

static int sample_eq_row(const ns_fault_sample_t *s, unsigned slot, uint32_t seq,
                         const int64_t v[14])
{
    return v[0] == (int64_t)slot && v[1] == (int64_t)seq &&
           v[2] == (int64_t)s->session_id && v[3] == (int64_t)s->time_ms &&
           v[4] == (int64_t)s->event && v[5] == (int64_t)s->valid &&
           v[6] == (int64_t)s->sg && v[7] == (int64_t)s->current_ma &&
           v[8] == (int64_t)s->ax_mg && v[9] == (int64_t)s->ay_mg &&
           v[10] == (int64_t)s->az_mg && v[11] == (int64_t)s->step_hz &&
           v[12] == (int64_t)s->vibration_mg && v[13] == (int64_t)s->flags;
}

/* ======================= 正常构建用例 ======================= */
#ifndef FLASH_CAP_PROBE
static void case_header_and_normal_row(void)
{
    ns_fault_sample_t s;
    char expect[160];
    int ok;

    model_reset();
    sample_normal(&s);
    slot_set_ok(0, &s, 17);
    dump_call(0, 1, 1);
    snprintf(expect, sizeof(expect),
             "0,17,3,123456,9,31,-6,-7,-8,-9,-10,-11,-12,13\n");
    ok = !fl_cap_overflow && !cap_has_nul() && cap_ends_lf() &&
         fl_cap_len == strlen(FL_HEADER) + strlen(expect) &&
         memcmp(fl_cap, FL_HEADER, strlen(FL_HEADER)) == 0 &&
         memcmp(fl_cap + strlen(FL_HEADER), expect, strlen(expect)) == 0 &&
         fl_reads == 1 && fl_reads_at_slot[0] == 1;
    fl_check("FE01-header_and_normal_row", ok,
             fl_fm("reads=%u kputs=%d kprintf=%d len=%u cap=%s",
                   fl_reads, fl_kputs_calls, fl_kprintf_calls,
                   (unsigned)fl_cap_len, fl_cap));
}

static void case_extreme_values(void)
{
    ns_fault_sample_t ext, nrm;
    int64_t v[14];
    size_t p0, p3;
    size_t n0, n3;
    int ok;

    model_reset();
    memset(&ext, 0, sizeof(ext));
    sample_extreme(&ext);
    sample_normal(&nrm);
    slot_set_ok(0, &ext, UINT32_MAX);
    /* slot 1 empty, slot 2 error */
    slot_set_err(2, -RT_EIO);
    slot_set_ok(3, &nrm, 17);

    dump_call(0, 4, 1);

    p0 = csv_data_line(0);
    p3 = csv_data_line(1);
    n0 = (p0 == (size_t)-1) ? 0 : line_len_at(p0);
    n3 = (p3 == (size_t)-1) ? 0 : line_len_at(p3);

    ok = !fl_cap_overflow && !cap_has_nul() && cap_ends_lf() &&
         csv_line_count() == 2 &&
         p0 != (size_t)-1 && n0 >= 138 && n0 <= 155 &&
         parse_csv14(fl_cap + p0, n0, v) && sample_eq_row(&ext, 0, UINT32_MAX, v) &&
         p3 != (size_t)-1 && parse_csv14(fl_cap + p3, n3, v) &&
         sample_eq_row(&nrm, 3, 17, v) &&
         cap_has("slot 1 empty\n") && cap_has("slot 2 error -12\n") &&
         fl_reads == 4;
    fl_check("FE02-extreme_values_full_roundtrip", ok,
             fl_fm("reads=%u rows=%d n0=%u n3=%u kputs=%d kprintf=%d nul=%d cap=%s",
                   fl_reads, csv_line_count(), (unsigned)n0, (unsigned)n3,
                   fl_kputs_calls, fl_kprintf_calls, cap_has_nul(), fl_cap));
}

static void case_invalid_args(void)
{
    static const struct { int argc; const char *a1; const char *a2; const char *a3; } bad[] = {
        { 2, "4096",   RT_NULL, RT_NULL },
        { 2, "4095",   RT_NULL, RT_NULL },   /* 默认 count=10 越界 */
        { 3, "0",      "0",     RT_NULL },
        { 3, "0",      "101",   RT_NULL },
        { 3, "4096",   "1",     RT_NULL },
        { 3, "-1",     "1",     RT_NULL },
        { 3, "+1",     "1",     RT_NULL },
        { 3, "0x10",   "1",     RT_NULL },
        { 3, "abc",    "1",     RT_NULL },
        { 3, "0",      "1x",    RT_NULL },
        { 3, "0",      " 1",    RT_NULL },
        { 3, "",       "1",     RT_NULL },
        { 3, "4294967296", "1", RT_NULL },
        { 3, "0", "4294967296", RT_NULL },
        { 3, "0", "999999999999999999999", RT_NULL },
        { 4, "0",      "1",     "extra" },
    };
    unsigned i;
    int ok = 1;
    char why[256];
    why[0] = 0;

    for (i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i)
    {
        char *argv[4];
        int argc = bad[i].argc;
        model_reset();
        argv[0] = (char *)"flash_dump";
        if (argc > 1) argv[1] = (char *)bad[i].a1;
        if (argc > 2) argv[2] = (char *)bad[i].a2;
        if (argc > 3) argv[3] = (char *)bad[i].a3;
        flash_dump(argc, argv);
        if (fl_reads != 0 || cap_has("slot,") || fl_cap_len == 0)
        {
            ok = 0;
            snprintf(why, sizeof(why), "case %u (argc=%d a1=%s a2=%s): reads=%u cap=%s",
                     i, argc, bad[i].a1 ? bad[i].a1 : "-",
                     bad[i].a2 ? bad[i].a2 : "-", fl_reads, fl_cap);
            break;
        }
    }
    fl_check("FE03-invalid_args_zero_reads", ok,
             ok ? fl_fm("16 malformed arg sets rejected before any ns_log_read_slot")
                : why);
}

static void case_last_slot(void)
{
    ns_fault_sample_t s;
    int64_t v[14];
    size_t p;
    int ok;

    model_reset();
    sample_normal(&s);
    slot_set_ok(NS_LOG_SLOTS - 1, &s, 4095);
    dump_call(NS_LOG_SLOTS - 1, 1, 1);
    p = csv_data_line(0);
    ok = !cap_has_nul() && csv_line_count() == 1 && p != (size_t)-1 &&
         parse_csv14(fl_cap + p, line_len_at(p), v) &&
         sample_eq_row(&s, NS_LOG_SLOTS - 1, 4095, v) &&
         fl_reads == 1 && fl_reads_at_slot[NS_LOG_SLOTS - 1] == 1;
    fl_check("FE04-last_slot_boundary_accepted", ok,
             fl_fm("reads=%u rows=%d p=%d cap=%s", fl_reads, csv_line_count(),
                   (int)p, fl_cap));
}

static void case_read_error_continues(void)
{
    ns_fault_sample_t s;
    int64_t v[14];
    size_t p;
    int ok;

    model_reset();
    sample_normal(&s);
    slot_set_err(5, -RT_EIO);
    slot_set_ok(6, &s, 21);
    dump_call(5, 2, 1);
    p = csv_data_line(0);
    ok = !cap_has_nul() && cap_ends_lf() && csv_line_count() == 1 &&
         cap_has("slot 5 error -12\n") &&
         p != (size_t)-1 && parse_csv14(fl_cap + p, line_len_at(p), v) &&
         sample_eq_row(&s, 6, 21, v) &&
         fl_reads == 2;
    fl_check("FE05-read_error_no_partial_row", ok,
             fl_fm("reads=%u rows=%d cap=%s", fl_reads, csv_line_count(), fl_cap));
}
#endif /* !FLASH_CAP_PROBE */

/* ======================= 容量不足探针(仅收缩缓冲的构建) ======================= */
#ifdef FLASH_CAP_PROBE
static void case_capacity_overflow(void)
{
    ns_fault_sample_t ext;
    int ok;

    model_reset();
    memset(&ext, 0, sizeof(ext));
    sample_extreme(&ext);
    slot_set_ok(0, &ext, UINT32_MAX);
    dump_call(0, 1, 1);

    ok = !fl_cap_overflow && !cap_has_nul() && cap_ends_lf() &&
         csv_line_count() == 0 &&
         fl_cap_len == strlen(FL_HEADER) +
                       strlen("[FLASH] flash_dump ERROR: line overflow at slot 0\n") &&
         memcmp(fl_cap + strlen(FL_HEADER),
                "[FLASH] flash_dump ERROR: line overflow at slot 0\n",
                strlen("[FLASH] flash_dump ERROR: line overflow at slot 0\n")) == 0 &&
         fl_reads == 1 && fl_kputs_calls == 1 && fl_kprintf_calls == 1;
    fl_check("FE06-capacity_overflow_named_no_partial", ok,
             fl_fm("reads=%u rows=%d kputs=%d kprintf=%d cap=%s",
                   fl_reads, csv_line_count(), fl_kputs_calls,
                   fl_kprintf_calls, fl_cap));
}
#endif

/* 给归档判读链提供一批"真实导出字节"(仍走同一个 flash_dump 入口)。 */
static int emit_batch(void)
{
    ns_fault_sample_t s;
    int i;

#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);   /* 不让主机 CRT 把 LF 改成 CRLF */
#endif
    model_reset();
    for (i = 0; i < 4; ++i)
    {
        memset(&s, 0, sizeof(s));
        if (i == 0) sample_extreme(&s); else sample_normal(&s);
        slot_set_ok((unsigned)i, &s, (i == 0) ? UINT32_MAX : (uint32_t)(100 + i));
    }
    dump_call(0, 4, 1);
    if (fl_cap_overflow) return 1;
    fwrite(fl_cap, 1, fl_cap_len, stdout);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "emit") == 0)
        return emit_batch();
#ifdef FLASH_CAP_PROBE
    case_capacity_overflow();
#else
    case_header_and_normal_row();
    case_extreme_values();
    case_invalid_args();
    case_last_slot();
    case_read_error_continues();
#endif
    printf("SUMMARY|cases=%d pass=%d fail=%d\n", fl_cases, fl_pass, fl_fail);
    return fl_fail == 0 ? 0 : 1;
}
