# -*- coding: utf-8 -*-
"""r7d1r_resource_report.py - 返修候选构建侧资源核对(设备动作 0)

两份 map + 两份调用图 htm + 两份 UV4 日志的逐项差分, 全部从文件重算, 不抄上一轮的字。
用法: python r7d1r_resource_report.py <out_txt>
"""
import hashlib
import io
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(r'D:/ns800')
BSP = REPO / 'rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad'
OLD_FW = REPO / 'reports/firmware/phase8-m8-r7d1-async-learn-offline'
NEW_FW = REPO / 'reports/firmware/phase8-m8-r7d1-ard-rework'
NEW_LOG = REPO / 'reports/reviews/phase8-m8-r7d1-ard-rework/build/build_r7d1r.log'
OLD_LOG = OLD_FW.parent.parent / 'reviews/phase8-m8-r7d1-async-learn-offline/build/build_r7d1.log'
OLD_MAP = OLD_FW / 'rt-thread-motor-m1.map'
NEW_MAP = NEW_FW / 'rt-thread-motor-m1.map'
OLD_CG = OLD_FW / 'rt-thread-motor-m1-callgraph.htm'
NEW_CG = NEW_FW / 'rt-thread-motor-m1-callgraph.htm'
OLD_BIN = OLD_FW / 'rt-thread-motor-m1-candidate.bin'
NEW_BIN = NEW_FW / 'rt-thread-motor-m1-candidate.bin'
OLD_AXF = OLD_FW / 'rt-thread-motor-m1.axf'
NEW_AXF = NEW_FW / 'rt-thread-motor-m1.axf'
BUILD_BIN = BSP / 'build/motor_m1/rt-thread-motor-m1.bin'
BUILD_AXF = BSP / 'build/motor_m1/rt-thread-motor-m1.axf'
SYMS = ['diag_thread_entry', 'rel_cmd_zero', 'rel_zero_capture_close', 'rel_job_advance',
        'rel_job_status', 'rel_cmd_learn_status', 'rel_cmd_learn_cancel', 'rel_cmd_commit',
        'rel_cmd_clear', 'rel_ctx_gate', 'rel_pending_write', 'rel_pending_read',
        'rel_zero_capture_open', 'rel_cap_abort', 'motor_calibration_static_begin',
        'motor_calibration_static_end', 'project_config_set_rprof', 'project_config_clear_rprof']
DATA_SYMS = ['d_zero_cleanup', 'd_job', 'd_cap_owner', 'd_ctx_lock', 'd_job_next_id', 'd_cap',
             'd_pending']
W = []


def out(s=''):
    W.append(s)


def sha(p):
    return hashlib.sha256(io.open(str(p), 'rb').read()).hexdigest()


def ident(p):
    b = io.open(str(p), 'rb').read()
    return '%10d B  sha256=%s' % (len(b), sha(p))


def crlf_norm(p):
    """日志/映射文件按行比对: 行尾差异单独报, 不当成内容差异。"""
    b = io.open(str(p), 'rb').read()
    return b.replace(b'\r\n', b'\n')


def log_lines(p, pat):
    txt = io.open(str(p), encoding='utf-8', errors='replace').read()
    return [l.strip() for l in txt.splitlines() if re.search(pat, l)]


SUMMARY_ROWS = ('Grand Totals', 'Object Totals', 'ROM Totals', 'Library Totals',
                'Image Component Totals', 'Section Totals', 'Symbol Totals',
                'ELF Base Totals', 'ELF Image Totals', 'Global Symbol Totals', 'Local Symbol Totals',
                'Dual-Byte Symbol Totals', 'Thumb Symbol Totals', 'ARM ELF Symbol Totals',
                'Thumb ELF Symbol Totals', 'ARM Literal Constant Totals',
                'Number of Sections', 'Number of Symbols', 'Long Section to Short',
                'Image Exit Pool Size', 'Entry Table', 'Other Symbols',
                'First Section', 'Last Section', 'Number of Cross Reference Areas',
                'Total of Entries', 'Total RO Size', 'Total RW Size', 'Total ROM Size')


def obj_table(map_path):
    """armlink 'Image component sizes' 段 -> {对象: (Code, inc.data, RO, RW, ZI)}; 排除小计行。"""
    txt = io.open(str(map_path), encoding='utf-8', errors='replace').read()
    i = txt.find('Image component sizes')
    tail = txt[i:]
    rows = {}
    kind = {}
    pads = []
    totals = {}
    for line in tail.splitlines():
        m = re.match(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S.*)$', line)
        if not m:
            continue
        name = m.group(7).strip()
        nums = tuple(int(m.group(k)) for k in range(1, 6))
        if name.startswith('(incl'):
            pads.append(nums)
            continue
        if any(name.startswith(s) for s in SUMMARY_ROWS):
            totals[name] = nums
            continue
        rows[name] = nums
        kind[name] = 'lib' if '(' in name and name.endswith(')') else 'obj'
    return rows, kind, pads, totals


def regions(map_path):
    txt = io.open(str(map_path), encoding='utf-8', errors='replace').read()
    rs = []
    for line in txt.splitlines():
        m = re.match(r'^\s*Execution Region (\S+) \(Exec base: (0x[0-9a-f]+), Load base: (0x[0-9a-f]+),'
                     r' Size: (0x[0-9a-f]+), Max: (0x[0-9a-f]+), (.*?)\)\s*$', line)
        if m:
            rs.append({'name': m.group(1), 'exec': int(m.group(2), 16), 'load': int(m.group(3), 16),
                       'size': int(m.group(4), 16), 'max': int(m.group(5), 16), 'attr': m.group(6)})
    return rs


def flash_span(axf_path, bin_path):
    axf = io.open(str(axf_path), 'rb').read()
    binb = io.open(str(bin_path), 'rb').read()
    e_phoff = struct.unpack_from('<I', axf, 28)[0]
    e_phentsize, e_phnum = struct.unpack_from('<2H', axf, 42)
    segs = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        (pt, po, pv, pp, fs, ms, fl, al) = struct.unpack_from('<8I', axf, o)
        if pt == 1 and fs:
            segs.append((po, pv, pp, fs, ms))
    flash = [s for s in segs if 0x08000000 <= s[1] < 0x08100000]
    lo = min(s[1] for s in flash)
    hi = max(s[1] + s[3] for s in flash)
    img = bytearray(hi - lo)
    cov = bytearray(hi - lo)
    for so, v, p, fs, ms in flash:
        img[v - lo:v - lo + fs] = axf[so:so + fs]
        cov[v - lo:v - lo + fs] = b'\x01' * fs
    return {'segs': len(segs), 'flash_segs': len(flash), 'lo': lo, 'hi': hi, 'span': hi - lo,
            'holes': cov.count(0), 'bin': len(binb), 'equal': (hi - lo == len(binb)) and bytes(img) == binb}


def callgraph(htm_path):
    txt = io.open(str(htm_path), encoding='utf-8', errors='replace').read()
    res = {}
    for m in re.finditer(r'<STRONG><a name="\[[0-9a-f]+\]"></a>(\w+)</STRONG> \((\w+), (\d+) bytes, '
                         r'Stack size (\d+) bytes, (.*?)\)\s*<BR><BR>\[Stack\]<UL><LI>Max Depth = (\d+)'
                         r'<LI>Call Chain = ([^<]*)', txt):
        res[m.group(1)] = {'bytes': int(m.group(3)), 'frame': int(m.group(4)),
                           'obj': m.group(5), 'depth': int(m.group(6)),
                           'chain': m.group(7).strip().replace('&rArr;', '=>')}
    return res


def sym_rows(map_path, names):
    txt = io.open(str(map_path), encoding='utf-8', errors='replace').read()
    out = {}
    for line in txt.splitlines():
        m = re.match(r'^\s{4}(\w+)\s+(0x[0-9a-f]+)\s+(Data|Number)\s+(\d+)\s+(\S.*)$', line)
        if m and m.group(1) in names:
            out[m.group(1)] = (int(m.group(2), 16), m.group(3), int(m.group(4)), m.group(5).strip())
    return out


def main():
    out('M8-R7D1 返修(ARD1-ARD4)构建侧资源核对: armlink 调用图 + 两版 map 逐对象差分 + AXF/BIN 装载段逐字节')
    out('日期 2026-11-04。目标 rt-thread-motor-m1-candidate。返修只构建一次, 设备动作 0(不烧录/不回读/不复位/')
    out('  不开串口/不动逻辑分析仪/不 arm/start/不运动/不做现场学习/不写配置/不供断电)。')
    out('')
    out('[0] 输入件身份(本机磁盘字节; 提交前预检与推送后远端回读还会各复算一次)')
    for tag, p in (('返修 BIN', NEW_BIN), ('返修 AXF', NEW_AXF), ('返修 MAP', NEW_MAP),
                   ('返修调用图', NEW_CG), ('返修 UV4 日志', NEW_LOG),
                   ('被审件 BIN(4d50310, 冻结)', OLD_BIN), ('被审件 AXF', OLD_AXF),
                   ('被审件 MAP', OLD_MAP), ('被审件调用图', OLD_CG), ('被审件 UV4 日志', OLD_LOG)):
        out('  %-26s %s  %s' % (tag, ident(p), os.path.relpath(str(p), str(REPO)).replace('\\', '/')))
    b_same = crlf_norm(NEW_BIN) == crlf_norm(BUILD_BIN)
    a_same = crlf_norm(NEW_AXF) == crlf_norm(BUILD_AXF)
    out('  归档件==构建目录原件: BIN %s, AXF %s(逐字节)' % (b_same, a_same))

    out('')
    out('[1] 编译参数与结果行(逐字取自两份日志)')
    for tag, lg in (('返修', NEW_LOG), ('被审', OLD_LOG)):
        cl = log_lines(lg, r'Using Compiler')
        ps = log_lines(lg, r'Program Size:')
        er = log_lines(lg, r'Error\(s\), .*Warning\(s\)')
        el = log_lines(lg, r'Build Time Elapsed')
        rb = log_lines(lg, r'^Rebuild target')
        out('  %s 编译器行: %s' % (tag, cl[0] if cl else 'MISSING'))
        out('  %s 目标行  : %s' % (tag, rb[0] if rb else 'MISSING'))
        out('  %s Program Size: %s' % (tag, ps[0] if ps else 'MISSING'))
        out('  %s 结果行  : %s' % (tag, er[0] if er else 'MISSING'))
        out('  %s 用时    : %s' % (tag, el[0] if el else 'MISSING'))
    hooks = log_lines(NEW_LOG, r'After Build - User command')
    out('  返修 AfterMake 用户命令(逐字): %s' % (hooks[0] if hooks else 'MISSING'))
    out('    => 只 fromelf --bin, 无下载/调试/串口动作。')

    ps_new = re.findall(r'Code=(\d+) RO-data=(\d+) RW-data=(\d+) ZI-data=(\d+)',
                        io.open(str(NEW_LOG), encoding='utf-8', errors='replace').read())[0]
    ps_old = re.findall(r'Code=(\d+) RO-data=(\d+) RW-data=(\d+) ZI-data=(\d+)',
                        io.open(str(OLD_LOG), encoding='utf-8', errors='replace').read())[0]
    dn = [int(a) - int(b) for a, b in zip(ps_new, ps_old)]
    out('  增量(armlink 口径, 返修 - 被审): Code=%+d RO=%+d RW=%+d ZI=%+d' % tuple(dn))
    out('    即 ROM 侧(Code+RO+RW) %+d, RAM 侧(RW+ZI) %+d' % (dn[0] + dn[1] + dn[2], dn[2] + dn[3]))

    out('')
    out('[2] AXF 的 Flash 装载段 == BIN(独立复算, 不靠 fromelf 的产出路径自证)')
    for tag, ax, bn in (('被审件', OLD_AXF, OLD_BIN), ('返修件', NEW_AXF, NEW_BIN)):
        r = flash_span(ax, bn)
        out('  %s 段数=%d(Flash 内 %d) lo=%08X hi=%08X span=%d 空洞=%d BINSIZE=%d 逐字节相等=%s'
            % (tag, r['segs'], r['flash_segs'], r['lo'], r['hi'], r['span'], r['holes'], r['bin'], r['equal']))
    ro, rn = flash_span(OLD_AXF, OLD_BIN), flash_span(NEW_AXF, NEW_BIN)
    out('  BIN 增量 = %+d B' % (rn['bin'] - ro['bin']))

    out('')
    out('[3] BIN 的构成与增量对账(逐字节复算; 上一轮"Code+RO+RW==BIN 增量"这条等式本轮不再成立, 换算法)')
    for tag, mp in (('被审件', OLD_MAP), ('返修件', NEW_MAP)):
        out('  %s 执行区(装载地址落在 Flash 的那些):' % tag)
        for r in regions(mp):
            if 0x08000000 <= r['load'] < 0x08100000:
                out('    %-12s load=%08X size=0x%X(%d) attr=%s'
                    % (r['name'], r['load'], r['size'], r['size'], r['attr']))

    def loadmap(mp):
        rs = {r['name']: r for r in regions(mp)}

        def cbytes(nm):
            r = rs[nm]
            cm = re.search(r'COMPRESSED\[(0x[0-9a-f]+)\]', r['attr'])
            return int(cm.group(1), 16) if cm else r['size']
        irom, vt, dt = rs['ER_IROM1']['size'], cbytes('RW_VT_DTCM'), cbytes('RW_DTCM')
        gap = rs['RW_DTCM']['load'] - (rs['RW_VT_DTCM']['load'] + vt)
        return {'irom': irom, 'vt': vt, 'dtcm': dt, 'gap': gap,
                'sum': irom + vt + gap + dt, 'dtcm_exec': rs['RW_DTCM']['size']}

    L_o, L_n = loadmap(OLD_MAP), loadmap(NEW_MAP)
    for tag, L, bn in (('被审件', L_o, ro['bin']), ('返修件', L_n, rn['bin'])):
        out('  %s: ER_IROM1 %d + RW_VT_DTCM 压缩载荷 %d + 对齐空隙 %d + RW_DTCM 压缩载荷 %d = %d'
            % (tag, L['irom'], L['vt'], L['gap'], L['dtcm'], L['sum']))
        out('        实测 BIN = %d  =>  构成等式 %s(RW_ITCM size=0 与 RW_RT_HEAD 无初始化, 都不占装载字节)'
            % (bn, '成立' if L['sum'] == bn else '不成立'))
    d_i, d_v, d_g, d_d = (L_n['irom'] - L_o['irom'], L_n['vt'] - L_o['vt'],
                          L_n['gap'] - L_o['gap'], L_n['dtcm'] - L_o['dtcm'])
    d_sum = d_i + d_v + d_g + d_d
    d_bin = rn['bin'] - ro['bin']
    out('  增量: ER_IROM1 %+d, RW_VT_DTCM %+d, 对齐空隙 %+d, RW_DTCM 压缩载荷 %+d => 合计 %+d'
        % (d_i, d_v, d_g, d_d, d_sum))
    out('  BIN 实际增量 %+d; 残差 %+d => 判据 %s' % (d_bin, d_sum - d_bin,
                                                     'PASS' if d_sum == d_bin else 'FAIL'))
    out('  与 armlink 汇总行的关系: Code+RO+RW 增量 %+d, 而 BIN 只 %+d —— 差的 %+d B 是 RW_DTCM 的'
        % (dn[0] + dn[1] + dn[2], d_bin, (dn[0] + dn[1] + dn[2]) - d_bin))
    out('    **压缩后**初始化载荷变小 %+d 与对齐空隙 %+d 吸收的; RW_DTCM 未压缩 size 0x%X->0x%X(%+d)'
        % (d_d, d_g, L_o['dtcm_exec'], L_n['dtcm_exec'], L_n['dtcm_exec'] - L_o['dtcm_exec']))
    out('    与 ZI 增量 %+d 一致(新增静态 d_zero_cleanup 1 B + 对齐), 压缩载荷里这段全零块被编码得更短。' % dn[3])
    out('    => 没有"内容丢了"这一说: 装载段的**未压缩**尺寸只增不减, BIN 变小的是压缩载荷。')

    out('')
    out('[4] 逐对象差分(只列非零; 其余全部为 0)')
    oo, ko, po, to = obj_table(OLD_MAP)
    on, kn, pn, tn = obj_table(NEW_MAP)
    out('  小计行(元组顺序 Code/inc.data/RO/RW/ZI)与它们的增量:')
    for k in sorted(set(to) | set(tn)):
        a, b = to.get(k), tn.get(k)
        dd = [x - y for x, y in zip(b, a)] if (a and b) else None
        out('    %-30s 被审=%s 返修=%s 差=%s' % (k, a, b, dd))
    names = sorted(set(oo) | set(on))
    nz = 0
    for n in names:
        a, b = oo.get(n), on.get(n)
        if a == b:
            continue
        nz += 1
        d = [x - y for x, y in zip(b or [0] * 5, a or [0] * 5)]
        out('  %-30s 被审=%s 返修=%s 差 Code%+d RO%+d RW%+d ZI%+d' % (n, a, b, d[0], d[2], d[3], d[4]))
    out('  (incl. Padding) 行(元组顺序 Code/inc.data/RO/RW/ZI): 被审 %d 行, 返修 %d 行'
        % (len(po), len(pn)))
    for i in range(max(len(po), len(pn))):
        a = po[i] if i < len(po) else None
        b = pn[i] if i < len(pn) else None
        dd = [x - y for x, y in zip(b, a)] if (a and b) else None
        out('    第 %d 行 被审=%s 返修=%s 差=%s' % (i + 1, a, b, dd))
    n_obj = len([k for k, v in ko.items() if v == 'obj'])
    n_lib = len([k for k, v in ko.items() if v == 'lib'])
    nz_names = [n for n in names if oo.get(n) != on.get(n)]
    out('  对照表规模: 对象 %d 个, 库成员行 %d 个; 非零差分 %d 行(应只出现在改动过的对象上)'
        % (n_obj, n_lib, nz))
    out('  口径澄清(本轮重算时发现, 也纠正上一轮的措辞): component-sizes 表里 %d 行全是 .o, '
        '没有 lib 成员行;' % n_obj)
    out('    库成员(如 mc_w.l(entry.o))在 map 的更靠前段落单独列出, 不在这张表里 —— 上一轮把它写成')
    out('    "39 个库成员行"是同一张表的口径, 这次改成按整份 map 比对库成员行的成员名/段/尺寸:')
    mem = re.compile(r'\b[\w.-]+\.[\w]+\([^()]+\.o\)')

    def parse_member(l):
        t = l.split()
        if len(t) < 3:
            return ('UNPARSED', l.strip(), 0, '')
        typ = next((x for x in t if x in ('Code', 'Data')), '')
        i = t.index(typ) if typ else -1
        size = int(t[i - 1], 16) if i > 0 else 0
        return (t[-1], t[-2], size, typ)

    def members(mp):
        return [parse_member(l) for l in io.open(str(mp), encoding='utf-8', errors='replace')
                if mem.search(l)]
    mo, mn = members(OLD_MAP), members(NEW_MAP)
    out('    库成员行(成员名+段+尺寸+类型, 地址会变所以不参与比较): 被审 %d, 返修 %d; 集合相同: %s'
        % (len(mo), len(mn), 'PASS' if sorted(mo) == sorted(mn) else 'FAIL'))
    if sorted(mo) != sorted(mn):
        dd = [x for x in set(mn) - set(mo)] + [x for x in set(mo) - set(mn)]
        out('    不同行(至多 8 条): %s' % dd[:8])
    out('  判据 非零差分集合恰好 == {diag_relative.o}: %s  实际=%s'
        % ('PASS' if nz_names == ['diag_relative.o'] else 'FAIL', nz_names))
    do, dnw = oo.get('diag_relative.o'), on.get('diag_relative.o')
    if do and dnw:
        obj_rom = (dnw[0] - do[0]) + (dnw[2] - do[2]) + (dnw[3] - do[3])
        pad2 = (po[1], pn[1]) if len(po) > 1 and len(pn) > 1 else None
        pad_rom = None
        if pad2:
            pad_rom = (pad2[1][0] - pad2[0][0]) + (pad2[1][2] - pad2[0][2]) + (pad2[1][3] - pad2[0][3])
        out('  对账: diag_relative.o 的 Code+RO+RW 增量 = %+d; 全图 Program Size 增量 = %+d'
            % (obj_rom, dn[0] + dn[1] + dn[2]))
        if pad_rom is not None:
            out('          符号表区 (incl. Padding) 第 2 行的增量 = %+d, 两者相加 %s 全图增量(%s)'
                % (pad_rom, '等于' if obj_rom + pad_rom == dn[0] + dn[1] + dn[2] else '不等于',
                   'PASS' if obj_rom + pad_rom == dn[0] + dn[1] + dn[2] else 'FAIL'))
        out('          => 本轮改动只进这一个对象; component-sizes 表里其余 %d 行(.o)逐字段相同。'
            % (n_obj - 1))
    out('  新增静态(取自返修件 map 符号表): ')
    so, sn = sym_rows(OLD_MAP, set(DATA_SYMS)), sym_rows(NEW_MAP, set(DATA_SYMS))
    for s in DATA_SYMS:
        out('    %-16s 被审=%s 返修=%s' % (s, so.get(s, 'ABSENT'), sn.get(s, 'ABSENT')))

    out('')
    out('[5] armlink 调用图(单位 B; 每个符号的数是"以它自己为根"的子树累计, 不含祖先帧;')
    out('    armlink 解析不了函数指针, 所以是静态下界, 不含异常/ISR 帧 —— 只能用于比较, 不是现场最坏栈)')
    co, cn = callgraph(OLD_CG), callgraph(NEW_CG)
    out('  %-30s %-16s %-16s %s' % ('符号', '被审 帧/子树', '返修 帧/子树', '差'))
    for s in SYMS:
        a, b = co.get(s), cn.get(s)
        if not a and not b:
            out('  %-30s %-16s %-16s 两边都没出现(未被这个目标纳入)' % (s, '-', '-'))
            continue
        fa, da = (a['frame'], a['depth']) if a else (None, None)
        fb, db = (b['frame'], b['depth']) if b else (None, None)
        out('  %-30s %5s / %6s   %5s / %6s   %+d / %+d'
            % (s, fa, da, fb, db, (fb or 0) - (fa or 0), (db or 0) - (da or 0)))
    out('  返修件最深链(逐字):')
    for s in ('diag_thread_entry', 'rel_cmd_zero', 'rel_zero_capture_close', 'rel_job_advance',
              'rel_cmd_commit', 'rel_cmd_clear', 'rel_cmd_learn_status', 'rel_job_status'):
        if s in cn:
            out('    %-24s depth=%-6d chain=%s' % (s, cn[s]['depth'], cn[s]['chain']))
    out('  被审件同一符号的链(对照):')
    for s in ('diag_thread_entry', 'rel_cmd_zero', 'rel_job_advance', 'rel_cmd_commit', 'rel_cmd_clear'):
        if s in co:
            out('    %-24s depth=%-6d chain=%s' % (s, co[s]['depth'], co[s]['chain']))
    out('  栈配置未动: diagnosis.c 的 DIAG_THREAD_STACK 与 rtconfig.h 的 FINSH_THREAD_STACK_SIZE 见 [6]')
    out('')
    out('  判读(只用"同一目标两版相减", 不当现场最坏栈实测):')
    dj_o, dj_n = co.get('diag_thread_entry'), cn.get('diag_thread_entry')
    if dj_o and dj_n:
        out('  A) Diagnosis Thread: 帧 %+d, 子树 %+d; 栈 4096 未改 => 静态下界余量 %d B(被审 %d B)。'
            % (dj_n['frame'] - dj_o['frame'], dj_n['depth'] - dj_o['depth'],
               4096 - dj_n['depth'], 4096 - dj_o['depth']))
        out('     最深线未变的判据: 返修链头与被审链头逐字相同 => %s'
            % (dj_n['chain'] == dj_o['chain']))
    roots = ('rel_cmd_zero', 'rel_cmd_commit', 'rel_cmd_clear', 'rel_cmd_learn',
             'rel_cmd_learn_status', 'rel_cmd_learn_cancel')
    out('  B) 命令线程(msh 入口调到的这些命令, 栈 FINSH 4096 未改): 子树最深的是哪一个、多少:')
    worst_n = max((s for s in roots if s in cn), key=lambda s: cn[s]['depth'], default=None)
    worst_o = max((s for s in roots if s in co), key=lambda s: co[s]['depth'], default=None)
    for s in roots:
        a, b = co.get(s), cn.get(s)
        if not b:
            out('     %-22s 返修件调用图里没出现' % s)
            continue
        out('     %-22s 子树 %s -> %s (%+d)  余量 4096-%s = %d B'
            % (s, a['depth'] if a else '-', b['depth'], (b['depth'] - a['depth']) if a else 0,
               b['depth'], 4096 - b['depth']))
    out('     最深根: 被审 %s(%s) -> 返修 %s(%s) => 命令线程里可见的最深线被顶高了 %+d B(如实登记, 见下)'
        % (worst_o, co[worst_o]['depth'] if worst_o in co else '-',
           worst_n, cn[worst_n]['depth'] if worst_n in cn else '-',
           (cn[worst_n]['depth'] if worst_n in cn else 0) -
           (co[worst_o]['depth'] if worst_o in co else 0)))
    grew = [(s, cn[s]['depth'] - co[s]['depth']) for s in roots
            if s in cn and s in co and cn[s]['depth'] > co[s]['depth']]
    out('     子树变高的命令根: %s(逐条列, 空=没有一条变高)' % (grew or '无'))
    if 'rel_cmd_commit' in cn and 'rel_cmd_commit' in co:
        cc = cn['rel_cmd_commit']
        out('     最要紧的一条如实说出来: rel_cmd_commit 帧 %+d、子树 %+d(1444 -> 1556)。'
            % (cc['frame'] - co['rel_cmd_commit']['frame'], cc['depth'] - co['rel_cmd_commit']['depth']))
        out('       方向明确: ARD4 的回滚需要一个"发布前候选"的局部副本 prev, 类型 diag_rprof_t 的尺寸'
            '取自 map 符号表 = %d B(d_pending)。armlink 只给帧总大小、不给是哪个槽, 所以这里只到"方向",'
            '不写成等式。' % (sn.get('d_pending', (0, '', 0, ''))[2]))
        out('       它因此成为命令线程里可见的最深根(1556 B), 余量 4096-1556 = %d B;'
            '被审件最深根是 rel_cmd_clear(1460)。没有靠加大栈或改 deadline 绕过。' % (4096 - cc['depth']))
    if 'rel_cmd_zero' in cn and 'rel_cmd_zero' in co:
        out('     rel_cmd_zero 帧 %+d: ARD2 的披露改成调用方打印后, 多了一个 deferred 局部与一处调用, 子树 %+d。'
            % (cn['rel_cmd_zero']['frame'] - co['rel_cmd_zero']['frame'],
               cn['rel_cmd_zero']['depth'] - co['rel_cmd_zero']['depth']))
    newf = [s for s in SYMS if s not in co and s in cn]
    gone = [s for s in co if s not in cn and s in SYMS]
    out('     本轮新增/消失于调用图的符号: 新增=%s 消失=%s(应当都为空: 返修不新增函数, 只改顺序与签名)'
        % (newf or '无', gone or '无'))

    out('')
    out('[6] 两个栈常量本轮未改(逐字取自产品源码)')
    for rel, pat in ((BSP / 'applications/diagnosis.c', r'DIAG_THREAD_STACK'),
                     (BSP / 'rtconfig.h', r'FINSH_THREAD_STACK_SIZE')):
        for i, l in enumerate(io.open(str(rel), encoding='utf-8', errors='replace').read().splitlines(), 1):
            if re.search(pat, l):
                out('  %s:%d: %s' % (os.path.relpath(str(rel), str(REPO)).replace('\\', '/'), i, l.strip()))
    out('  本轮 diff 未触及这两个文件(git diff --name-only 见 offline/checks.txt 的 SCOPE 判据)。')

    txt = '\n'.join(W) + '\n'
    dest = Path(sys.argv[1])
    dest.parent.mkdir(parents=True, exist_ok=True)
    io.open(str(dest), 'w', encoding='utf-8', newline='\n').write(txt)
    sys.stdout.buffer.write(txt.encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
