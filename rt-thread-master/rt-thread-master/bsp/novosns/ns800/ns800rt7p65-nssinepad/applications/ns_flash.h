#ifndef NS_FLASH_H
#define NS_FLASH_H
#include <rtthread.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* All functions: thread context only. Success = RT_EOK; errors < 0.
 * Initialize before other SPI1 clients. All access to this chip must use this
 * driver; do not attach SFUD or the old flash_demo to the same chip in parallel.
 * program does NOT erase; preflight rejects any requested 0->1 transition.
 * erase/program verify data; failure may leave a partially changed range.
 */
rt_err_t ns_flash_init(void);
rt_err_t ns_flash_identify(uint8_t id[3], uint8_t sr[3]);
rt_err_t ns_flash_read(uint32_t address, void *data, rt_size_t length);
rt_err_t ns_flash_program(uint32_t address, const void *data, rt_size_t length);
rt_err_t ns_flash_erase(uint32_t address, rt_size_t length);
rt_err_t ns_flash_clearwp(void);
#ifdef __cplusplus
}
#endif
#endif
