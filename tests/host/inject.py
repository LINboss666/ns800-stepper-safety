#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""inject.py - 负例/元负例注入的唯一实现（run_negatives.sh 与 run_meta_negatives.sh 共用）

用法: python inject.py <目标文件> <模式>
规则: 每个模式的锚点必须**恰好命中 1 次**, 否则退出码 1 且不写回 —— 宁可终止,
      也不允许"注入没落地却继续跑"。所有替换都插入 NEGATIVE-INJECTION 标记,
      调用方事后仍要复查标记存在, 并确认被改副本只存在于仓库外工作区。
"""
import io
import sys

SUBS = {
    # R02: 让算术自测真实打印 FAILED(只翻判据, 不动夹具)
    'calc_anchor': [(
        'if (v < 323u || v > 329u) pass = 0;',
        'if (1) pass = 0;   /* NEGATIVE-INJECTION(calc anchor) */')],
    # 历史保留组: FD6 GSTAT 期望表篡改(夹具副本)
    'gstat_expect': [(
        '{ 0, 1, 0, 0 }, { 0, 0, 1, 0 },',
        '{ 0, 1, 0, 0 }, { 1, 0, 0, 0 },  /* NEGATIVE-INJECTION(gstat) */')],
    # 历史保留组: probe 插回"读旧值再写回"(固件副本)
    'probe_writeback': [(
        'rt_kprintf("[TMC] probe: 只读身份检查 txn-held IOIN+GSTAT+IFCNT (0 个 WRITE 帧)\\n");',
        'rt_kprintf("[TMC] probe: 只读身份检查 txn-held IOIN+GSTAT+IFCNT (0 个 WRITE 帧)\\n");\n'
        '    /* NEGATIVE-INJECTION: 身份检查顺手把读到的旧值写回(应被 FD3/FD7b 抓到)。\n'
        '     * CP5 修注入本身: S2 §10.3 起 tmc_read_reg / tmc2209_write_register_confirmed\n'
        '     * 都带 (预算, 传输统计) 两个尾参, 旧配方按两参调用 => 副本编译失败\n'
        '     * (N9/N10/N10b 被记 INFRA-FAIL, 不是检出)。这里按当前签名给 RT_NULL 的\n'
        '     * "无预算/不留统计"调用 —— 写帧仍真的发出去, 判据不变。 */\n'
        '    { rt_uint32_t neg_old = 0u;\n'
        '      (void)tmc_read_reg(TMC_ADDR_DEFAULT, TMC_REG_GCONF, &neg_old, RT_NULL, RT_NULL);\n'
        '      (void)tmc2209_write_register_confirmed(TMC_ADDR_DEFAULT, TMC_REG_GCONF,\n'
        '                                             neg_old, RT_NULL, RT_NULL); }')],
    # 元负例 M1(复现 A): 把 V2 的 FD7b 判据换成恒真 => 检测器失效, 链必须拒绝
    'fd7b_blinding': [(
        'CHECK(shim_frame_write_frames == wf0 && shim_chip[0x00] == gchip &&\n'
        '              shim_frame_read_reqs - rr0 == 3,',
        'CHECK(1,   /* NEGATIVE-INJECTION(fd7b_blinding) */')],
    # 元负例 M3(复现 R2-NEW-01): 把 runner 末尾的出口码丢掉(状态文件仍自报 overall),
    # 链必须发现"真实退出码 != 自报退出码"
    'runner_exit_zero': [(
        'exit "$overall"',
        'exit 0   # NEGATIVE-INJECTION(runner_exit_zero): 故意丢掉出口码')],

    # ---- 2026-10-21 I/O 反向验证(三项, 只改**仓库外生产源码副本**) ----
    # R1: 限位宏关闭时仍强制把两路限位纳入解析/配置 => 应由 IO1OFF-* 抓到(目标 safety_gpio.c)
    'rev_limits_forced_off': [(
        '#if SAFETY_LIMIT_INPUTS_ENABLED == RT_TRUE',
        '#if 1   /* NEGATIVE-INJECTION(rev_limits_forced_off): 宏关闭也强制启用限位 */')],
    # R2: 限位宏开启时把"反相后的 OK 信号"写成 LOW 安全/上升沿 => 应由 IO1ON-* 抓到
    'rev_limits_polarity_on': [(
        '{ PIN_NAME_LIMIT_MIN, lim_min_isr,  PIN_IRQ_MODE_FALLING, PIN_HIGH, "LIMIT_MIN_OK" }, /* bit 0 */',
        '{ PIN_NAME_LIMIT_MIN, lim_min_isr,  PIN_IRQ_MODE_RISING,  PIN_LOW,  "LIMIT_MIN_OK" }, /* NEGATIVE-INJECTION(rev_limits_polarity_on) */')],
    # R3: 把与请求不符的 ENN 反馈强行当匹配 => 应由 IO3b / IO5a / IO6b2 抓到
    'rev_fb_always_match': [(
        'if (fb  == want_fb)    return SAFETY_FB_OK;',
        'return SAFETY_FB_OK;   /* NEGATIVE-INJECTION(rev_fb_always_match) */')],
}


def main():
    if len(sys.argv) != 3:
        sys.stderr.write(__doc__)
        return 2
    path, mode = sys.argv[1], sys.argv[2]
    subs = SUBS.get(mode)
    if subs is None:
        sys.stderr.write('unknown inject mode: %s (可用: %s)\n' % (mode, ', '.join(sorted(SUBS))))
        return 2
    s = io.open(path, encoding='utf-8').read()
    out = s
    for old, new in subs:
        n = out.count(old)
        if n != 1:
            sys.stderr.write('INJECT-FATAL: 锚点命中 %d 次(应恰 1 次): %s\n' % (n, old[:70]))
            return 1
        out = out.replace(old, new, 1)
    io.open(path, 'w', encoding='utf-8', newline='').write(out)
    print('injected(mode=%s, replacements=1) -> %s' % (mode, path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
