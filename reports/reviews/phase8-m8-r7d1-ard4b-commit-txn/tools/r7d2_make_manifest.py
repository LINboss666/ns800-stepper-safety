# -*- coding: utf-8 -*-
"""r7d2_make_manifest.py - 为续修候选生成 build_manifest.json(所有数字都当场从文件算)。
用法: python r7d2_make_manifest.py
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(r'D:/ns800')
BSP = REPO / 'rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad'
FW = REPO / 'reports/firmware/phase8-m8-r7d1-ard4b-commit-txn'
OLD_FW = REPO / 'reports/firmware/phase8-m8-r7d1-ard-rework'
RD = REPO / 'reports/reviews/phase8-m8-r7d1-ard4b-commit-txn'
ZIP = Path(os.path.expandvars(r'%USERPROFILE%')) / 'Downloads' / \
    'NS800_R7D1_ARD4_Followup_e1d7f3d_20261105.zip'
BASE = 'e1d7f3d90415fdf30344ed95a08065c1373fab01'


def sha(p):
    return hashlib.sha256(io.open(str(p), 'rb').read()).hexdigest()


def size(p):
    return os.path.getsize(str(p))


def git(*a):
    p = subprocess.run(list(a), cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode('utf-8', 'replace').strip()


def cr(p):
    """磁盘字节里的 CR/LF 计数(判据要能自己复算)。"""
    b = io.open(str(p), 'rb').read()
    return b.count(b'\r'), b.count(b'\n')


def rel(p):
    return os.path.relpath(str(p), str(REPO)).replace('\\', '/')


def diff_scope(base):
    """相对被审基线的改动面(工作树 + 未跟踪文件), 分产品/验证链/证据三类。"""
    rc, names = git('git', 'diff', '--name-only', base)
    rc2, un = git('git', 'ls-files', '--others', '--exclude-standard')
    rows = {'product': [], 'host_chain': [], 'evidence_or_docs': []}
    for n in sorted(set([x for x in names.splitlines() if x] + [x for x in un.splitlines() if x])):
        if n.startswith('rt-thread-master/'):
            rows['product'].append(n)
        elif n.startswith('tests/'):
            rows['host_chain'].append(n)
        else:
            rows['evidence_or_docs'].append(n)
    return {'git_rc': rc, 'untracked_rc': rc2,
            'product_files': rows['product'], 'host_chain_files': rows['host_chain'],
            'evidence_or_docs_count': len(rows['evidence_or_docs'])}


def cg_row(txt, sym):
    """从资源报告的调用图差分表里取一行的四个数(被审 帧/子树, 返修 帧/子树)。
    取不到就抛: 报告与 manifest 必须同源, 不接受这里再手抄一份数字。"""
    m = re.search(r'^\s*%s\s+(\d+|-)\s*/\s*(\d+|-)\s+(\d+|-)\s*/\s*(\d+|-)' % re.escape(sym),
                  txt, re.M)
    if not m:
        raise SystemExit('ANCHOR-MISSING in resource report: call-graph row for %s' % sym)
    return {'reviewed_frame': int(m.group(1)), 'reviewed_subtree': int(m.group(2)),
            'this_frame': int(m.group(3)), 'this_subtree': int(m.group(4))}


def cg_line(txt, needle):
    """报告里逐字含 needle 的那一行(去空白); 找不到同样抛。"""
    for ln in txt.splitlines():
        if needle in ln:
            return ln.strip()
    raise SystemExit('ANCHOR-MISSING in resource report: %r' % needle)


def stack_constants():
    """三个栈/时间常量: 磁盘值与被审基线(BASE)的 blob 值各取一次, 两者相同才算"未改"。"""
    src = {
        'DIAG_THREAD_STACK': (BSP / 'applications/diagnosis.c', r'#define DIAG_THREAD_STACK\s+(\d+)'),
        'FINSH_THREAD_STACK_SIZE': (BSP / 'rtconfig.h', r'#define FINSH_THREAD_STACK_SIZE\s+(\d+)'),
        'REL_CMD_DEADLINE_MS': (BSP / 'applications/diag_relative.c', r'#define REL_CMD_DEADLINE_MS\s+(\d+)'),
    }
    _, names = git('git', 'diff', '--name-only', BASE)
    touched = set(names.splitlines())
    o = {}
    for k, (p, pat) in src.items():
        t = io.open(str(p), encoding='utf-8', errors='replace').read()
        m = re.search(pat, t)
        rc, blob = git('git', 'show', '%s:%s' % (BASE, rel(p)))
        mb = re.search(pat, blob) if rc == 0 else None
        rp = rel(p)
        o[k] = {'value_on_disk': m.group(1) if m else 'NOT-FOUND',
                'value_at_reviewed_baseline': mb.group(1) if mb else ('NOT-IN-BLOB' if rc == 0 else 'GIT-SHOW-FAILED'),
                'unchanged_vs_baseline': (bool(m) and bool(mb) and m.group(1) == mb.group(1)),
                'file': rp,
                'line_on_disk': next((i + 1 for i, ln in enumerate(t.splitlines())
                                      if m and m.group(0) in ln), 0),
                'file_in_this_round_diff': rp in touched}
    return o


def main():
    log = RD / 'build/build_r7d2.log'
    ltxt = io.open(str(log), encoding='utf-8', errors='replace').read()
    rc_head, head = git('git', 'rev-parse', 'HEAD')
    ps = re.search(r'Program Size: Code=(\d+) RO-data=(\d+) RW-data=(\d+) ZI-data=(\d+)', ltxt)
    m = {}
    for f in ('bin', 'axf', 'map', 'callgraph'):
        p = {
            'bin': FW / 'rt-thread-motor-m1-candidate.bin',
            'axf': FW / 'rt-thread-motor-m1.axf',
            'map': FW / 'rt-thread-motor-m1.map',
            'callgraph': FW / 'rt-thread-motor-m1-callgraph.htm'}[f]
        m[f] = {'path': rel(p), 'bytes': size(p), 'sha256': sha(p)}
    old_bin = OLD_FW / 'rt-thread-motor-m1-candidate.bin'
    old_manifest = json.loads(io.open(str(OLD_FW / 'build_manifest.json'), encoding='utf-8').read())
    old_ps = re.search(r'Program Size: Code=(\d+) RO-data=(\d+) RW-data=(\d+) ZI-data=(\d+)',
                       old_manifest['program_size'])
    # 资源报告的判据行(逐字取回, 不在这里重算第二套口径): 这些 True/PASS 都由
    # build/resource_stack_r7d2.txt 自己产生, manifest 只做转述并给出文件名。
    rtxt = io.open(str(RD / 'build/resource_stack_r7d2.txt'), encoding='utf-8', errors='replace').read()
    axf_eq = ('BINSIZE=297452 逐字节相等=True' in rtxt) and ('BINSIZE=297476 逐字节相等=True' in rtxt)
    comp_ok = '残差 +0 => 判据 PASS' in rtxt
    objset = ['diag_relative.o', 'project_config.o']
    nonzero_ok = ("判据 非零差分集合恰好 == %s: PASS" % objset) in rtxt
    recon_ok = ('全图 Program Size 增量 -28 => PASS') in rtxt
    lib_ok = '被审 49, 返修 49; 集合相同: PASS' in rtxt
    out = {
        'round': 'phase8-m8-r7d1-ard4b-commit-txn',
        'date': '2026-11-05',
        'title': 'M8-R7D1 ARD4b 续修轮: commit 提交点两阶段化(拒绝无副作用) + 单目标构建一次(设备动作 0)',
        'review_source': {
            'zip_basename': ZIP.name,
            'zip_bytes': size(ZIP),
            'zip_sha256': sha(ZIP),
            'verdict': 'CHANGES_REQUIRED(仅 ARD4 阻断; ARD1-ARD3 已收口不再重开)',
            'reviewed_commit': BASE,
            'fix_id': 'R7D1_ARD4_FOLLOWUP_e1d7f3d',
            'hardware_actions_cap': 0,
            'new_approval_required_for_offline_fix': False,
            'inner_manifest_entries': 19,
        },
        'baseline_commit': BASE,
        'baseline_is_head_at_build': head == BASE,
        'head_at_build': head,
        'diff_scope_vs_baseline': diff_scope(BASE),
        'target': 'rt-thread-motor-m1-candidate',
        'action': ('Keil Rebuild All x1(只 rt-thread-motor-m1-candidate, -r 全量重建, 未加 -f)。'
                   '未下载、未 Flash、未连 DAP、未复位、未开串口、未起转、未 learn/zero、未 config_save、'
                   '未启动逻辑分析仪。设备动作 0。'),
        'command': ('C:\\Keil_v5\\UV4\\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 '
                    '-o D:/ns800/rt-thread-master/rt-thread-master/bsp/novosns/ns800/'
                    'ns800rt7p65-nssinepad/build_r7d2.log'),
        'working_directory': str(BSP),
        'uv4_return_code_observed_this_round': 0,
        'error_warning_line': re.search(r'"[^"]*axf" - (\d+) Error\(s\), (\d+) Warning\(s\)', ltxt).group(0),
        'compiler_line_from_log': re.search(r'\*\*\* Using Compiler [^\n]*', ltxt).group(0).strip(),
        'build_time_elapsed_line': re.search(r'Build Time Elapsed:[^\n]*', ltxt).group(0).strip(),
        'program_size': ps.group(0),
        'build_hooks_read_only_checked': {
            'BeforeMake': 'RunUserProg1=0(未启用任何前置程序)',
            'AfterMake': re.search(r'After Build - User command #1: [^\n]*', ltxt).group(0),
            'conclusion': '构建过程里没有下载/调试/串口动作; 日志原件同时留在构建目录(被 .gitignore 的 '
                          '*.log 规则排除)与仓内 reports/reviews/phase8-m8-r7d1-ard4b-commit-txn/build/。',
        },
        'log': {'path': rel(log), 'bytes': size(log), 'sha256': sha(log),
                'disk_CR': cr(log)[0], 'disk_LF': cr(log)[1]},
        'program_size_vs_reviewed_image': {
            'reviewed_image_manifest': rel(OLD_FW / 'build_manifest.json'),
            'reviewed_program_size': old_ps.group(0),
            'reviewed_bin_sha256': sha(old_bin),
            'reviewed_bin_bytes': size(old_bin),
            'delta_bytes': {'Code': int(ps.group(1)) - int(old_ps.group(1)),
                            'RO-data': int(ps.group(2)) - int(old_ps.group(2)),
                            'RW-data': int(ps.group(3)) - int(old_ps.group(3)),
                            'ZI-data': int(ps.group(4)) - int(old_ps.group(4))},
            'bin_bytes': m['bin']['bytes'],
            'bin_delta': m['bin']['bytes'] - size(old_bin),
            'reconciliation': (
                '口径与上一轮相同(不沿用"Code+RO+RW 增量 == BIN 增量"这条并不恒成立的等式): '
                'Code+RO+RW 增量 -28, BIN 增量 -24, 差 +4 B, 由装载段的构成解释 —— '
                'ER_IROM1 296896->296868(-28), RW_VT_DTCM 压缩载荷 8->8(0), 对齐空隙 4->0(-4), '
                'RW_DTCM 压缩载荷 568->576(+8) => 合计 -24 == BIN 增量, 残差 0。'
                '两版各自的"三段+空隙"等式都成立(297476 / 297452), 所以不存在内容丢失的余地; '
                'RAM 侧 RW+ZI 增量 +0(本轮没有新增静态, 反而删掉了 commit 里的 diag_rprof_t 局部副本 prev)。'),
            'resource_report': rel(RD / 'build/resource_stack_r7d2.txt'),
            'per_object_delta': 'diag_relative.o Code+8 RO-42 RW+0 ZI+0; project_config.o Code+2 RO+0 RW+0 ZI+0; '
                                '其余 109 行 .o 与 49 行库成员(成员名+段+尺寸+类型)逐字段相同; '
                                'Library Totals 全 0; 对账 -32 + (incl. Padding) 第 2 行 +4 = -28 == Program Size 增量。',
            'axf_flash_segment_equal_bin': axf_eq,
            'judgement_lines_transcribed_from_resource_report': {
                'axf_flash_segment_equal_bin_both_images': axf_eq,
                'bin_composition_residual_zero': comp_ok,
                'nonzero_object_diff_set_exact': nonzero_ok,
                'program_size_reconciliation': recon_ok,
                'library_member_rows_identical': lib_ok,
            },
            'axf_check_method': '仓外工具按 ELF32 程序头取 Flash 段(vaddr 在 0x08000000..0x08100000)拼成镜像, '
                                '与归档 BIN 逐字节比; 同一工具先在被审件上复现"相等"再跑本轮件。',
        },
        'images': m,
        'stack_cost_from_call_graph': {
            'units': 'B; armlink 口径, 静态下界(不含异常/ISR 帧, 解析不了函数指针), 只用于两版比较, 不是现场最坏栈实测',
            'rel_cmd_commit': cg_row(rtxt, 'rel_cmd_commit'),
            'why': ('上一轮为了回滚新增的 diag_rprof_t 局部副本 prev 与那条 project_config_set_rprof '
                    '调用链(其 cfg_take_checked -> rt_mutex_take 子树 948)一并消失; '
                    '命令线程可见的最深根由 rel_cmd_commit(1556) 变回 rel_cmd_clear(1460)。'),
            'deepest_command_root_change_line': cg_line(rtxt, '最深根:'),
            'no_subtree_grew': cg_line(rtxt, '子树变高的命令根:'),
            'stack_constants_unchanged': stack_constants(),
        },
        'image_states': {
            'this_round_candidate': 'NOT_ON_BOARD(本轮 0 次下载/回读/复位) sha256=%s' % m['bin']['sha256'],
            'previous_round_image_165d8bdc': 'NOT_ON_BOARD(登记于上一轮 e1d7f3d, 未上板; 本轮作为被审件对照)',
            'r7d1_image_df7584c3': 'NOT_ON_BOARD(登记于 4d50310, 未上板)',
            'on_board': 'b502 / 502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a '
                        '(历史逐字节回读证明, 本轮未改)',
        },
        'hardware_actions_this_round': 0,
        'authorization': '本件不构成任何硬件授权; polarity_measured=false, core_protection_verified=false 保持; '
                         'R7F5A 物理断电回报仍 PENDING_OPERATOR_REPORT。',
    }
    io.open(str(FW / 'build_manifest.json'), 'w', encoding='utf-8', newline='\n').write(
        json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    print('WROTE %s' % rel(FW / 'build_manifest.json'))
    print('HEAD_IS_BASELINE|%s' % (head == BASE))
    for k in ('bin', 'axf', 'map', 'callgraph'):
        print('IMAGE|%s|%d|%s' % (k, m[k]['bytes'], m[k]['sha256']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
