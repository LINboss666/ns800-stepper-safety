/*
 * app_health.h - 全项目统一的子系统健康状态约定 (Phase 7-A)
 *
 * 约定：
 *   1. 每个底层驱动子系统提供 xxx_get_health() 返回 subsys_health_t
 *   2. 读失败必须通过 health/valid 表达 —— 禁止用数值 0 假装"有效测量值为 0"
 *   3. health 语义：
 *      UNINIT   未初始化（API 未被调用或初始化未完成）
 *      OK       初始化成功且最近一次操作成功
 *      DEGRADED 可用但降级（如：能读但未标定 / 最近一次传输失败但可重试）
 *      FAILED   初始化失败或设备确认不可用（调用方不得把返回数据当有效值）
 *   4. 返回 rt_err_t 的读函数：RT_EOK 时输出参数有效；非 RT_EOK 时输出参数
 *      内容无定义，调用方必须检查返回值
 */
#ifndef APP_HEALTH_H
#define APP_HEALTH_H

typedef enum
{
    SUBSYS_UNINIT = 0,
    SUBSYS_OK,
    SUBSYS_DEGRADED,
    SUBSYS_FAILED,
} subsys_health_t;

static inline const char *subsys_health_name(subsys_health_t h)
{
    switch (h)
    {
    case SUBSYS_UNINIT:   return "UNINIT";
    case SUBSYS_OK:       return "OK";
    case SUBSYS_DEGRADED: return "DEGRADED";
    case SUBSYS_FAILED:   return "FAILED";
    default:              return "?";
    }
}

#endif /* APP_HEALTH_H */
