/* drv_uart_txleaf.inc —— CP5 §5: TX 预算叶子的**唯一**生产实现体。
 *
 * 为什么是私有 include 而不是副本: 任务书 §5 要求主机与目标共用同一实现 ——
 * 这里放的是**原来就在 drv_uart.c 里的那两个函数**, 逐字搬入, 算法一行未改;
 * 目标由 drv_uart.c 在原位置 include, 主机夹具 TU 在提供真实签名的对象表与
 * 寄存器 shim 之后 include **同一个文件**。等待循环只存在于这一份实现里,
 * 因此 C17 观察到的超时/部分发送/written 语义就是生产语义。
 *
 * 包含者必须先提供: struct ns800_uart 与 uart_obj[]、UART_isSpaceAvailable /
 * UART_writeChar、__get_PRIMASK、rt_interrupt_get_nest / rt_thread_self /
 * rt_tick_get / rt_thread_mdelay(目标侧由 SDK 与 RT-Thread 头提供)。 */

rt_bool_t ns800_intr_masked(void)
{
    return (__get_PRIMASK() != 0u) ? RT_TRUE : RT_FALSE;
}

rt_err_t ns800_uart_write_budgeted(rt_device_t dev, const rt_uint8_t *data,
                                   rt_size_t size, rt_tick_t start, rt_tick_t total,
                                   rt_size_t *written)
{
    struct ns800_uart *uart = RT_NULL;
    rt_size_t i;
    rt_size_t n_obj;

    if (written != RT_NULL) *written = 0;
    if (dev == RT_NULL || data == RT_NULL || written == RT_NULL) return -RT_EINVAL;
    if (total == 0 || total >= (rt_tick_t)0x80000000) return -RT_EINVAL;
    /* CP4 K5(§7): HAL **自己**守上下文, 不能假设"自己永远是那个已做过前置检查的
     * TMC 包装层的唯一调用者"。三条判据与 tmc2209.c 的前置检查同一套:
     *   ① rt_interrupt_get_nest() != 0 => 中断上下文(原实现只看 rt_thread_self(),
     *      被中断打断的主线程仍然非 NULL, 那条判据不成立);
     *   ② ns800_intr_masked() => PRIMASK 已屏蔽, SysTick 被挡住, 而这个循环按
     *      rt_tick_get() 判超时 => 永不退出;
     *   ③ rt_thread_self() == NULL => 没有线程对象, rt_thread_mdelay 非法。
     * 拒绝路径不打印、不延时, 只回原始错误码。 */
    if (rt_interrupt_get_nest() != 0u) return -RT_EBUSY;
    if (ns800_intr_masked())            return -RT_EBUSY;
    if (rt_thread_self() == RT_NULL)    return -RT_EBUSY;

    /* 在**已注册**的对象表里匹配实际 dev; 匹配不到就拒绝, 不对任意指针做
     * container_of 后解引用(rt_hw_uart_init 用 uart_obj[i].serial 注册设备)。 */
    n_obj = sizeof(uart_obj) / sizeof(uart_obj[0]);
    for (i = 0; i < n_obj; ++i)
    {
        if (&uart_obj[i].serial.parent == dev)
        {
            uart = &uart_obj[i];
            break;
        }
    }
    if (uart == RT_NULL || uart->handle.Instance == RT_NULL) return -RT_EINVAL;

    for (i = 0; i < size; )
    {
        if ((rt_tick_t)(rt_tick_get() - start) >= total) return -RT_ETIMEOUT;
        if (!UART_isSpaceAvailable(uart->handle.Instance))
        {
            /* 2026-10-28 TMC-RC 根因: 原实现在这里 rt_thread_mdelay(1)。
             * 4 字节帧因此出现 ~1ms 的字节间隔(整帧 tx_off=3ms), TMC2209 会丢弃
             * 这种带长空闲的帧 —— 线上有回声但永远没有应答。帧内必须保持连续:
             * 这里改为只按 tick 检查预算的短自旋(单字节等待 <= 一个字符时间),
             * 与旧 ns800_putc 的等待方式一致; 预算仍在这里强制执行。 */
            while (!UART_isSpaceAvailable(uart->handle.Instance))
            {
                if ((rt_tick_t)(rt_tick_get() - start) >= total) return -RT_ETIMEOUT;
            }
        }
        UART_writeChar(uart->handle.Instance, data[i]);
        ++i;
        *written = i;
    }
    /* CP4 K5(§7): 末字节之后、返回成功之前**再查一次原 start/total**。
     * 过期时如实返回 -RT_ETIMEOUT, 但 *written 保持**已经发出去的真实字节数** ——
     * 不声称"过期前物理上绝未发送"(那一层软件证明不了)。 */
    if ((rt_tick_t)(rt_tick_get() - start) >= total) return -RT_ETIMEOUT;
    return RT_EOK;
}
