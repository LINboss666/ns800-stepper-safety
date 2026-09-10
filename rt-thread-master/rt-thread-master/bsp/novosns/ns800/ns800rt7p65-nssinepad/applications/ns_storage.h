#ifndef NS_STORAGE_H
#define NS_STORAGE_H
#include "ns_flash.h"
#ifdef __cplusplus
extern "C" {
#endif
#define NS_PARAM_BASE       0x000000u
#define NS_PARAM_SIZE       0x010000u
#define NS_LOG_BASE         0x010000u
#define NS_LOG_SIZE         0x100000u
#define NS_WAVE_BASE        0x110000u
#define NS_WAVE_SIZE        0x6ef000u
#define NS_TEST_BASE        0x7ff000u
#define NS_TEST_SIZE        0x001000u
#define NS_LOG_SLOTS         (NS_LOG_SIZE / 256u)
#define NS_PARAM_MAX        224u
#define NS_VALID_SG         (1u << 0)
#define NS_VALID_CURRENT    (1u << 1)
#define NS_VALID_ACCEL      (1u << 2)
#define NS_VALID_STEP       (1u << 3)
#define NS_VALID_VIBRATION  (1u << 4)
#define NS_EVENT_TEST       1u
#define NS_EVENT_COLLISION  2u
#define NS_EVENT_STALL      3u
#define NS_EVENT_DRIVER     4u
/* Values without a valid bit are absent, not measured zero.
 * session_id is supplied by the application (0 = not assigned).
 * time_ms is uptime/sample time supplied by caller; not wall-clock UTC.
 */
typedef struct {
    uint32_t session_id, time_ms, event, valid;
    int32_t sg, current_ma, ax_mg, ay_mg, az_mg, step_hz, vibration_mg;
    uint32_t flags;
} ns_fault_sample_t;
typedef struct {
    uint32_t used_slots, valid_records, bad_records;
    uint32_t accepted, completed, failed, dropped;
    rt_err_t last_error;
} ns_log_stats_t;
/* Initialize before motors/IMU start. Scans the 1 MiB log (~10s at 1MHz).
 * No automatic format. All APIs are thread-only; do not call in ISR.
 */
rt_err_t ns_storage_init(void);
/* Blocking confirmed-persistent append; async submit copies sample into RAM.
 * submit==EOK means queued, not yet durable. A full queue returns an error.
 */
rt_err_t ns_log_append(const ns_fault_sample_t *sample);
rt_err_t ns_log_submit(const ns_fault_sample_t *sample);
rt_err_t ns_log_flush(int timeout_ms);
rt_err_t ns_log_stats(ns_log_stats_t *out);
rt_err_t ns_log_read_slot(uint32_t slot, ns_fault_sample_t *out, uint32_t *sequence);
/* Maintenance only: caller stops submissions; rejects pending work.
 * Clear is destructive and not atomic across power loss; run again if interrupted.
 */
rt_err_t ns_log_clear(void);
/* Two alternating 4KiB copies, CRC + final commit; length <=224 bytes.
 * load sets *length to required length on too-small buffer; capacity 0 is allowed.
 * Parameter data is opaque bytes: application owns schema/version/endian format.
 */
rt_err_t ns_params_save(const void *data, rt_size_t length);
rt_err_t ns_params_load(void *data, rt_size_t capacity, rt_size_t *length);
/* Offsets are relative to wave partition; erase/program are explicit. */
rt_err_t ns_wave_read(uint32_t offset, void *data, rt_size_t length);
rt_err_t ns_wave_program(uint32_t offset, const void *data, rt_size_t length);
rt_err_t ns_wave_erase(uint32_t offset, rt_size_t length);
#ifdef __cplusplus
}
#endif
#endif
