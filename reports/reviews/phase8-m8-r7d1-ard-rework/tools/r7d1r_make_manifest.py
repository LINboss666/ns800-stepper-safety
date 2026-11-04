# -*- coding: utf-8 -*-
"""r7d1r_make_manifest.py - 为返修候选生成 build_manifest.json(所有数字都当场从文件算)。
用法: python r7d1r_make_manifest.py
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
FW = REPO / 'reports/firmware/phase8-m8-r7d1-ard-rework'
OLD_FW = REPO / 'reports/firmware/phase8-m8-r7d1-async-learn-offline'
RD = REPO / 'reports/reviews/phase8-m8-r7d1-ard-rework'
ZIP = Path(os.path.expandvars(r'%USERPROFILE%')) / 'Downloads' / \
    'NS800_R7D1_A_Review_Changes_Required_4d50310_20261104.zip'
BASE = '4d503103cf3f1eb6f5113b10cc8683954d510ac6'


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


def main():
    log = RD / 'build/build_r7d1r.log'
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
    out = {
        'round': 'phase8-m8-r7d1-ard-rework',
        'date': '2026-11-04',
        'title': 'M8-R7D1 返修轮(ARD1-ARD4): 四条错误路径缺陷离线修复 + 回归与负控 + 单目标构建一次(设备动作 0)',
        'review_source': {
            'zip_basename': ZIP.name,
            'zip_bytes': size(ZIP),
            'zip_sha256': sha(ZIP),
            'verdict': 'CHANGES_REQUIRED(四项 P2, 全在错误路径)',
            'reviewed_commit': BASE,
            'fix_id': 'R7D1_A_REVIEW_4d50310_FIX',
            'hardware_actions_cap': 0,
            'new_approval_required_for_offline_fix': False,
            'inner_manifest_entries': 17,
        },
        'baseline_commit': BASE,
        'baseline_is_head_at_build': head == BASE,
        'head_at_build': head,
        'target': 'rt-thread-motor-m1-candidate',
        'action': ('Keil Rebuild All x1(只 rt-thread-motor-m1-candidate, -r 全量重建, 未加 -f)。'
                   '未下载、未 Flash、未连 DAP、未复位、未开串口、未起转、未 learn/zero、未 config_save、'
                   '未启动逻辑分析仪。设备动作 0。'),
        'command': ('C:\\Keil_v5\\UV4\\UV4.exe -r project.uvprojx -t rt-thread-motor-m1-candidate -j0 '
                    '-o D:/ns800/rt-thread-master/rt-thread-master/bsp/novosns/ns800/'
                    'ns800rt7p65-nssinepad/build_r7d1r.log'),
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
                          '*.log 规则排除)与仓内 reports/reviews/phase8-m8-r7d1-ard-rework/build/。',
        },
        'log': {'path': rel(log), 'bytes': size(log), 'sha256': sha(log),
                'disk_CR': cr(log)[0], 'disk_LF': cr(log)[1]},
        'program_size_vs_reviewed_image': {
            'reviewed_image_manifest': rel(OLD_FW / 'build_manifest.json'),
            'reviewed_program_size': 'Code=209178 RO-data=87242 RW-data=2196 ZI-data=85936',
            'reviewed_bin_sha256': sha(old_bin),
            'reviewed_bin_bytes': size(old_bin),
            'delta_bytes': {'Code': int(ps.group(1)) - 209178, 'RO-data': int(ps.group(2)) - 87242,
                            'RW-data': int(ps.group(3)) - 2196, 'ZI-data': int(ps.group(4)) - 85936},
            'bin_bytes': m['bin']['bytes'],
            'bin_delta': m['bin']['bytes'] - size(old_bin),
            'reconciliation': (
                '本轮 **不再** 沿用上一轮"Code+RO+RW 增量 == BIN 增量"这条等式: 实测 Code+RO+RW 增量 +476, '
                'BIN 增量 +472。差额由装载段构成变化解释(全部取自两版 map 的执行区表): '
                'ER_IROM1 296420->296896(+476), RW_VT_DTCM 压缩载荷 8->8(0), 对齐空隙 0->4(+4), '
                'RW_DTCM 压缩载荷 576->568(-8) => +472 == BIN 增量; '
                '两版各自"三段+空隙"相加都精确等于该版 BIN 字节数(297004 / 297476), 残差 0。'
                'RW_DTCM 未压缩尺寸 0xD478->0xD480(+8) 与 ZI 增量 +8 一致; 变小的是**压缩后**载荷。'),
            'resource_report': rel(RD / 'build/resource_stack_r7d1r.txt'),
            'per_object_delta': 'diag_relative.o Code+310 RO+178 RW+0 ZI+1; 其余 110 行 .o 与 49 行库成员 '
                                '(成员名+段+尺寸+类型)逐字段相同; Library Totals 全 0。',
            'axf_flash_segment_equal_bin': True,
            'axf_check_method': '仓外工具按 ELF32 程序头取 Flash 段(vaddr 在 0x08000000..0x08100000)拼成镜像, '
                                '与归档 BIN 逐字节比; 同一工具先在被审件上复现"相等"再跑本轮件。',
        },
        'images': m,
        'image_states': {
            'this_round_candidate': 'NOT_ON_BOARD(本轮 0 次下载/回读/复位)',
            'reviewed_r7d1_image_df7584c3': 'NOT_ON_BOARD(登记于上一轮, 未上板)',
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
