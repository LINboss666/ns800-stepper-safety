#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
probe_errcodes.py - 用**真实 ARMCLANG + 工程同一宏集/包含路径**导出目标错误码数值

为什么需要它: 上一轮只用 rtconfig.h/.config 里"搜不到 RT_USING_LIBC"就断定 RT-Thread
走非 libc 错误码分支。这是错的: RT_USING_LIBC / RT_USING_ARMLIBC 定义在 Keil 工程的
Target 级 <Define> 里(project.uvprojx 的 <Cads> 段), Applications 组没有相反覆盖。
rttypes.h 的 <sys/errno.h> 实际解析到 RT-Thread 自带的
components/libc/compilers/common/extension/sys/errno.h, 其中 __ARMCC_VERSION 分支
ERROR_BASE_NO=7, 于是 EPERM=8 / EBUSY=23 / ENOSYS=45 / ETIMEDOUT=117 ...

做法: 解析工程里 tmc2209.c/motor.c 实际使用的 <Cads> Define 与 IncludePath, 原样喂给
armclang, 编译一个只做常量定义的探针到汇编(-S -O0), 再从 .s 里读回每个符号的立即数。
不烧录、不运行目标码、不改工程。
"""
import io, os, re, subprocess, sys

def _repo_root():
    """从脚本所在目录向上定位仓库根; 可用 REPO_ROOT 显式覆盖, 不写死盘符。"""
    env = os.environ.get('REPO_ROOT')
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        r = subprocess.run(['git', '-C', here, 'rev-parse', '--show-toplevel'],
                           capture_output=True, text=True).stdout.strip()
        if r:
            return os.path.abspath(r)
    except OSError:
        pass
    d = here
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, 'rt-thread-master')):
            return d
        d = os.path.dirname(d)
    raise SystemExit('无法定位仓库根(设 REPO_ROOT=<路径>)')


ROOT = _repo_root()
RT   = os.path.join(ROOT, 'rt-thread-master', 'rt-thread-master')
BSP  = os.path.join(RT, 'bsp', 'novosns', 'ns800', 'ns800rt7p65-nssinepad')
PROJ = os.path.join(BSP, 'project.uvprojx')
OUT  = os.environ.get('PROBE_OUT') or os.path.join(
    os.environ.get('TMPDIR', os.path.expanduser('~/tmp')), 'ns800-host-chain', 'probe')
os.makedirs(OUT, exist_ok=True)
# armclang 路径不写死: KEIL_ARMCLANG 优先, 其次是常见 MDK 安装位置
AC = os.environ.get('KEIL_ARMCLANG') or r'C:\Keil_v5\ARM\ARMCLANG\bin\armclang.exe'


def _require_armclang():
    if not os.path.isfile(AC):
        raise SystemExit('找不到 armclang: %s (用 KEIL_ARMCLANG=<路径> 指定; '
                         '本探针只在装有 MDK 的机器上跑)' % AC)

SYMS = ['RT_EOK', 'RT_ERROR', 'RT_ETIMEOUT', 'RT_ENOSYS', 'RT_EBUSY', 'RT_EIO',
        'RT_EINVAL', 'RT_EPERM', 'RT_EINTR', 'RT_ENOMEM',
        'RT_WAITING_FOREVER', 'RT_WAITING_NO']
BRANCH = ['RT_USING_LIBC', 'RT_USING_ARMLIBC', 'RT_USING_NANO']

def project_switches():
    s = io.open(PROJ, encoding='utf-8', errors='replace').read()
    cads = re.search(r'<Cads>(.*?)</Cads>', s, re.S).group(1)
    define = re.search(r'<Define>(.*?)</Define>', cads, re.S).group(1).strip()
    incraw = re.search(r'<IncludePath>(.*?)</IncludePath>', cads, re.S).group(1).strip()
    defines = [d.strip() for d in define.split(',') if d.strip()]
    incs = []
    for p in incraw.split(';'):
        p = p.strip().replace('\\', os.sep)
        if not p:
            continue
        full = p if os.path.isabs(p) else os.path.normpath(os.path.join(BSP, p))
        if full not in incs:
            incs.append(full)
    return defines, incs, define, incraw

def write_probe():
    c = [
        '/* probe_errcodes.c - 只定义常量, 供 ARMCLANG 编译到汇编后读回数值。',
        ' * 由 probe_errcodes.py 生成; 不参与固件构建。',
        ' */',
        '#include <rtthread.h>',
        '',
    ]
    for sym in SYMS:
        c.append('volatile int obs_%s = %s;' % (sym, sym))
    for b in BRANCH:
        c += ['#ifdef %s' % b, 'volatile int obs_branch_%s = 1;' % b,
              '#else', 'volatile int obs_branch_%s = 0;' % b, '#endif']
    path = os.path.join(OUT, 'probe_errcodes.c')
    io.open(path, 'w', encoding='utf-8').write('\n'.join(c) + '\n')
    return path

def main():
    _require_armclang()
    defines, incs, define_raw, inc_raw = project_switches()
    probe = write_probe()
    asm = os.path.join(OUT, 'probe_errcodes.s')
    cmd = [AC, '--target=arm-arm-none-eabi', '-mcpu=cortex-m7', '-S', '-O0', '-Wall', '-Wextra']
    for d in defines:
        cmd.append('-D' + d)
    for i in incs:
        cmd += ['-I', i]
    cmd += [probe, '-o', asm]

    log = []
    log.append('== ARMCLANG 目标错误码实证 ==')
    log.append('armclang      : ' + AC)
    r = subprocess.run([AC, '--version'], capture_output=True, text=True)
    log.append('--version     : ' + (r.stdout or r.stderr).strip().replace('\n', ' | ')[:200])
    log.append('project file  : ' + PROJ)
    log.append('Target <Define> (原样) : ' + define_raw)
    log.append('Target <IncludePath> 条目数(去重后): %d' % len(incs))
    log.append('解析到的头文件根(前 12): ')
    for i in incs[:12]:
        log.append('   ' + i)
    log.append('')
    log.append('完整命令:')
    log.append('  ' + ' '.join(('"%s"' % x if ' ' in x else x) for x in cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    log.append('armclang rc = %d' % res.returncode)
    if res.stdout.strip():
        log.append('stdout: ' + res.stdout.strip())
    if res.stderr.strip():
        log.append('stderr: ' + res.stderr.strip())
    if res.returncode != 0:
        log.append('!! 预处理/编译失败, 无证据可采')
        io.open(os.path.join(OUT, 'armclang_errcode_evidence.txt'), 'w', encoding='utf-8').write('\n'.join(log) + '\n')
        print('\n'.join(log))
        return 2

    text = io.open(asm, encoding='utf-8', errors='replace').read()
    # ARMCLANG -O0 把每个全局常量单独放进 .data.obs_X / .bss.obs_X, 值为 .long N
    vals = {}
    for m in re.finditer(r'^(\S*obs_(\w+)):\s*$', text, re.M):
        name = m.group(2)
        tail = text[m.end():m.end() + 200]
        first = re.search(r'\.(?:long|word|int)\s+(-?\d+)', tail)
        if first:
            vals[name] = int(first.group(1))
        elif re.search(r'\.bss\.%s' % re.escape(m.group(1)), text[:m.start()][::-1][::1] or '') or              re.search(r'\.section\s+\.bss\.%s' % re.escape(m.group(1)), text):
            vals[name] = 0                                # .bss = 全零 => 值为 0
        else:
            vals[name] = None
    vals = {k: v for k, v in vals.items() if v is not None}
    log.append('')
    log.append('== 读回的符号值(ARMCLANG 在本工程宏集下的真实数值) ==')
    for sym in SYMS:
        log.append('  %-20s = %s' % (sym, vals.get(sym, 'NOT FOUND')))
    log.append('')
    log.append('== 分支探针 ==')
    for b in BRANCH:
        log.append('  %-20s defined? = %s' % (b, vals.get('branch_' + b, vals.get(b, 'NOT FOUND'))))
    log.append('')
    log.append('说明: RT_USING_LIBC 分支由工程 Target 定义提供; rttypes.h 的 <sys/errno.h>')
    log.append('解析到 components/libc/compilers/common/extension/sys/errno.h, 其中')
    log.append('__ARMCC_VERSION 分支 ERROR_BASE_NO=7 => EPERM=7+1, EBUSY=7+16, ENOSYS=7+38,')
    log.append('ETIMEDOUT=7+110; EINVAL 由 armcc errno.h 直接给 5。因此上轮 shim 采用的')
    log.append('"非 libc 分支"数值(EPERM=13/EBUSY=7/ENOSYS=6/ETIMEOUT=2/EINVAL=10/EIO=8)与目标不一致。')
    out = '\n'.join(log) + '\n'
    io.open(os.path.join(OUT, 'armclang_errcode_evidence.txt'), 'w', encoding='utf-8').write(out)
    print(out)
    # 机器可读, 供主 runner / 文档校准引用
    io.open(os.path.join(OUT, 'armclang_errcodes.json'), 'w', encoding='utf-8').write(
        __import__('json').dumps({k: vals.get(k) for k in SYMS + ['branch_RT_USING_LIBC',
                                    'branch_RT_USING_ARMLIBC', 'branch_RT_USING_NANO']}, indent=2) + '\n')
    return 0 if len(vals) >= len(SYMS) else 3

if __name__ == '__main__':
    sys.exit(main())
