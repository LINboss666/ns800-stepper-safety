#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r7d2a_acceptance_checks.py - A 签收轮的离线独立复算(纯文档/证据轮; 设备动作 0, 未重新构建)

不复用 A 的结论文本当判据: 每条都自己算一遍。
  1) 签收包身份: zip bytes+sha256、内层清单逐条复算且不含自身、IDENTITY 的关键字段;
  2) A 的 20 件原件与被 zip 内字节逐字节相同(含 A 自己的清单), 4 件探针输入与上一份 A 包同字节;
  3) A 登记的 7 件源码绑定在受审 tip 00718b4 上复算 blob sha1/字节/sha256;
  4) A 声称的结果从**我方归档原文**复算: 66/66、20 族 META-OK + 20 条 INJECTED、四探针 4/4 PASS、
     复合探针 OBS 值逐字段相同(A 的 proof 与我方 host 件互相印证);
  5) A 包内 proof/full_chain.log、image_identity_review.json、manifest_checks.json 的自洽性;
  6) 镜像侧本轮未重建: 三版 BIN 的磁盘 sha 不变, AXF 的 Flash 装载段仍逐字节等于 BIN(本地独立复算);
  7) 本轮确实没动产品与主机链: git diff 对这些路径为空, 改动面全在白名单内;
  8) 两处勘误已落地(旧句在四份文档里都不再出现, 新口径与源码一致), 产品注释漂移按登记不修;
  9) run_record 新增 §18 且 §16/§17 原文未删;
 10) 交付件里不得出现本机用户目录/用户名 —— 扫描器先用合成样本自证"会命中"。

用法: python r7d2a_acceptance_checks.py [out_dir]   (环境变量 R7D2A_ZIP 可覆盖签收包路径)
"""
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(r'D:/ns800')
TIP = '00718b49e65546aa0bae84006059011d68284c4a'
PARENT = 'e1d7f3d90415fdf30344ed95a08065c1373fab01'
APP_REL = ('rt-thread-master/rt-thread-master/bsp/novosns/ns800/'
           'ns800rt7p65-nssinepad/applications')
ACC = 'reports/reviews/phase8-m8-r7d1-ard4b-acceptance'
DEL = 'reports/reviews/phase8-m8-r7d1-ard4b-commit-txn'
PREVD = 'reports/reviews/phase8-m8-r7d1-ard-rework'
FWD = 'reports/firmware/phase8-m8-r7d1-ard4b-commit-txn'
RR = 'reports/reviews/phase8-m8-r7f2b-safety-print-fix'
ZIP_DEFAULT = Path(os.path.expandvars(r'%USERPROFILE%')) / 'Downloads' / \
    'NS800_R7D1_ARD4B_Acceptance_00718b4_20261105.zip'
ZIP_BYTES = 24196
ZIP_SHA256 = '49681a0f8dc92fff5b55c49a67afa09c37fbaae5b4a516bc17e87cd9fe8fa97f'
CAND_BIN_SHA = '23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963'
PREV_BIN_SHA = '165d8bdc708b042125d99db88f56a62ddca635d30202e80abe96c148e35bc478'
OLDER_BIN_SHA = 'df7584c3df2e682d07564ee70f05ecc7b608052a7fa739fb0c21b4f942b15c2b'
ALLOWED = {
    DEL + '/implementation_contract.md',
    RR + '/run_record.md',
    RR + '/SHA256SUMS.txt',
    DEL + '/SHA256SUMS.txt',
    'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.md',
    'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.json',
    'reports/agent_handoffs/phase8-m8-r7d1-ard4b-acceptance.md',
    'reports/agent_handoffs/phase8-m8-r7d1-ard4b-acceptance.json',
    '.gitattributes',
}
ALLOWED_PREFIX = (ACC + '/', FWD + '/')
CHECKS = []


def chk(cid, ok, detail=''):
    CHECKS.append({'id': cid, 'ok': bool(ok), 'detail': detail})
    return bool(ok)


def sh(*args):
    p = subprocess.run(list(args), cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode('utf-8', 'replace').strip()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read(p):
    return io.open(str(p), encoding='utf-8', errors='replace').read()


def blob_bytes(rev, path):
    p = subprocess.run(['git', 'cat-file', 'blob', '%s:%s' % (rev, path)],
                       cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout if p.returncode == 0 else None


def axf_flash_equal_bin(axf_p, bin_p):
    axf, binb = io.open(str(axf_p), 'rb').read(), io.open(str(bin_p), 'rb').read()
    e_phoff = struct.unpack_from('<I', axf, 28)[0]
    e_phentsize, e_phnum = struct.unpack_from('<2H', axf, 42)
    segs = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        (pt, po, pv, pp, fs, ms, fl, al) = struct.unpack_from('<8I', axf, o)
        if pt == 1 and fs:
            segs.append((po, pv, fs))
    fl_sh = [s for s in segs if 0x08000000 <= s[1] < 0x08100000]
    if not fl_sh:
        return False, 'no-flash-segment'
    lo = min(s[1] for s in fl_sh)
    hi = max(s[1] + s[2] for s in fl_sh)
    img = bytearray(hi - lo)
    for po, pv, fs in fl_sh:
        img[pv - lo:pv - lo + fs] = axf[po:po + fs]
    return (hi - lo == len(binb) and bytes(img) == binb), 'span=%d bin=%d' % (hi - lo, len(binb))


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / (ACC + '/proof')
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = os.environ.get('R7D2A_ZIP') or str(ZIP_DEFAULT)

    # ---------- 1) 签收包身份 ----------
    chk('ZIP_PRESENT', os.path.isfile(zip_path), os.path.basename(zip_path))
    raw = io.open(zip_path, 'rb').read()
    chk('ZIP_BYTES', len(raw) == ZIP_BYTES, 'bytes=%d 期望=%d' % (len(raw), ZIP_BYTES))
    chk('ZIP_SHA256', sha256_bytes(raw) == ZIP_SHA256, sha256_bytes(raw))
    zf = zipfile.ZipFile(zip_path)
    entries = []
    for line in zf.read('SHA256SUMS.txt').decode('utf-8', 'replace').strip().splitlines():
        m = re.match(r'^([0-9a-f]{64})  (.+)$', line.strip())
        if m:
            entries.append((m.group(2), m.group(1)))
    chk('MANIFEST_ENTRY_COUNT', len(entries) == 19, '%d 条(1 件正文 + 1 件 IDENTITY + 11 件 proof + 4 件 reproducer + 2 件 A 自己的清单行)'
        % len(entries))
    chk('ZIP_MEMBER_COUNT', len(zf.namelist()) == 20, 'zip 成员=%d = 19 条登记 + SHA256SUMS.txt 自身' % len(zf.namelist()))
    chk('MANIFEST_EXCLUDES_SELF', all(n != 'SHA256SUMS.txt' for n, _ in entries),
        ','.join(n for n, _ in entries if n == 'SHA256SUMS.txt') or '清单不含自身')
    bad = ['%s:want=%s.. got=%s..' % (n, w[:8], sha256_bytes(zf.read(n))[:8])
           for n, w in entries if n not in zf.namelist() or sha256_bytes(zf.read(n)) != w]
    chk('MANIFEST_ALL_MATCH', not bad, ';'.join(bad) or '%d/%d' % (len(entries), len(entries)))
    ident = json.loads(zf.read('IDENTITY.json').decode('utf-8'))
    chk('VERDICT_ACCEPT_WITH_ERRATA',
        ident.get('verdict') == 'ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA' and
        ident.get('blocking_product_findings') == 0 and
        ident.get('closed_findings') == ['ARD1', 'ARD2', 'ARD3', 'ARD4'],
        'verdict=%s blocking=%s closed=%s' % (ident.get('verdict'),
                                              ident.get('blocking_product_findings'),
                                              ident.get('closed_findings')))
    chk('REVIEW_COMMIT_IS_PUSHED_TIP',
        ident.get('review_commit') == TIP and ident.get('parent_commit') == PARENT,
        'review=%s parent=%s' % (str(ident.get('review_commit'))[:9], str(ident.get('parent_commit'))[:9]))
    rc, head = sh('git', 'rev-parse', 'HEAD')
    chk('HEAD_IS_ACCEPTED_TIP', head == TIP, 'HEAD=%s 签收 tip=%s' % (head[:9], TIP[:9]))
    chk('A_EDITS_AND_ACTIONS_ZERO',
        ident.get('review_product_edits') == 0 and ident.get('hardware_actions') == 0 and
        ident.get('arm_compiler_rerun_by_A') is False and
        ident.get('all_271_remote_entries_replayed_by_A') is False,
        '产品改动=%d 设备动作=%d A 重跑 arm=%s A 重放我方 271 条=%s' % (
            ident.get('review_product_edits'), ident.get('hardware_actions'),
            ident.get('arm_compiler_rerun_by_A'), ident.get('all_271_remote_entries_replayed_by_A')))
    chk('A_OUTSIDE_SCOPE_EMPTY', ident.get('outside_allowed_scope') == [],
        str(ident.get('outside_allowed_scope'))[:70])
    fb = ident.get('field_boundaries', {})
    chk('A_FIELD_BOUNDARIES_KEPT',
        fb.get('candidate') == 'NOT_ON_BOARD' and fb.get('candidate_sha256') == CAND_BIN_SHA and
        fb.get('on_board_proof', '').endswith('b502 / 502dca5d') and
        fb.get('polarity_measured') is False and fb.get('core_protection_verified') is False and
        fb.get('R7F5A_physical_poweroff') == 'PENDING_OPERATOR_REPORT' and
        fb.get('new_hardware_authorization') is False,
        'candidate=%s.. on_board=%s polarity=%s core=%s R7F5A=%s 新授权=%s' % (
            str(fb.get('candidate_sha256'))[:8], fb.get('on_board_proof'), fb.get('polarity_measured'),
            fb.get('core_protection_verified'), fb.get('R7F5A_physical_poweroff'),
            fb.get('new_hardware_authorization')))
    chk('A_ACCEPTS_COMPOUND_LIMITATION',
        ident.get('results', {}).get('compound_probe', {}).get('ctx_fault_arm_reached') is False and
        'A16' in str(ident.get('results', {}).get('compound_probe', {}).get('equivalent_ctx_fault_coverage', '')),
        'A 也独立认定复合探针 ctx 臂未命中, 等价覆盖=四探针第 4 条 + A16 两世界')

    # ---------- 2) 原件逐字节入库 + 探针输入身份沿用上一份 A 包 ----------
    ro = REPO / ACC / 'reviewer_originals'
    mism = [n for n, w in entries if not (ro / n).is_file() or sha256_bytes((ro / n).read_bytes()) != w
            or (ro / n).read_bytes() != zf.read(n)]
    chk('REVIEWER_ORIGINALS_VERBATIM', not mism,
        ';'.join(mism) or '%d 件与 zip 内字节逐字节相同' % len(entries))
    am_o, am_d = zf.read('SHA256SUMS.txt'), (ro / 'SHA256SUMS.txt').read_bytes()
    chk('A_SELF_MANIFEST_VERBATIM', am_o == am_d and len(am_d) > 0,
        'A 自己的清单(第 20 个成员)%d B sha=%s 逐字节相同' % (len(am_d), sha256_bytes(am_d)[:12]))
    # 上一份 A 包 = 四探针在 ard-rework, 复合探针在 ard4b-commit-txn(ARD4 续修包)
    SRC = {PREVD: ('reproducer/original_four/review_probes.c', 'reproducer/original_four/run_review_probes.py'),
           DEL: ('reproducer/compound/commit_rollback_probe.c',
                 'reproducer/compound/run_commit_rollback_probe.py')}
    PREV_NAME = {'reproducer/original_four/review_probes.c': 'reproducer/review_probes.c',
                 'reproducer/original_four/run_review_probes.py': 'reproducer/run_review_probes.py',
                 'reproducer/compound/commit_rollback_probe.c': 'reproducer/commit_rollback_probe.c',
                 'reproducer/compound/run_commit_rollback_probe.py': 'reproducer/run_commit_rollback_probe.py'}
    same = []
    for rd, news in SRC.items():
        for a_new in news:
            nb = zf.read(a_new) if a_new in zf.namelist() else None
            old_p = REPO / rd / 'reviewer_originals' / PREV_NAME[a_new]
            ob = old_p.read_bytes() if old_p.is_file() else None
            same.append((os.path.basename(a_new), nb is not None and nb == ob, len(nb or b'')))
    chk('PROBE_INPUTS_SAME_IDENTITY_AS_PRIOR_A_PACKAGE', all(x[1] for x in same),
        ';'.join('%s 与上一份 A 包同字节=%s(%d B)' % (n, e, b) for n, e, b in same))
    for b in ident.get('reviewer_probe_input_bindings', []):
        chk('PROBE_INPUT_SHA_' + os.path.splitext(b['file'])[0],
            sha256_bytes(zf.read([n for n in zf.namelist() if n.endswith(b['file'])][0])) == b['sha256'],
            '%s=%s' % (b['file'], b['sha256'][:12]))

    # ---------- 3) A 登记的源码绑定在 tip 上复算 ----------
    binds = ident.get('source_bindings', [])
    badn = 0
    rows = []
    for s in binds:
        path = s['path']
        b = blob_bytes(TIP, path)
        rc, oid = sh('git', 'rev-parse', '%s:%s' % (TIP, path))
        okb = (b is not None and oid == s.get('git_blob') and len(b) == s['bytes']
               and sha256_bytes(b) == s['sha256'])
        rows.append({'path': path, 'ok': okb, 'blob_sha1': oid, 'bytes': len(b or b''),
                     'sha256': sha256_bytes(b) if b else '', 'disk_equal': b == (REPO / path).read_bytes()})
        badn += 0 if okb else 1
    chk('A_SOURCE_BINDINGS_RECOMPUTED_AT_TIP', badn == 0 and len(binds) == 7,
        '%d 件绑定在 %s 上逐件复算 blob sha1/字节/sha256(坏 %d)' % (len(binds), TIP[:9], badn))
    chk('TIP_BLOBS_EQUAL_DISK', all(r['disk_equal'] for r in rows),
        'A 复算用的那 7 件与本机磁盘字节相同(未提交任何产品改动的前提)')

    # ---------- 4) A 声称的结果从我方归档原文复算 ----------
    host = REPO / DEL / 'host'
    ntxt = read(host / 'normal_chain_out.txt')
    m = re.search(r'SUMMARY\|cases=(\d+) pass=(\d+) fail=(\d+) pre_bad=(\d+) executed=(\d+)', ntxt)
    chk('OUR_CHAIN_66_MATCHES_A_CLAIM',
        m and m.group(1) == m.group(2) == m.group(5) == '66' and m.group(3) == '0' == m.group(4) and
        ident['results']['full_chain']['cases_pass'] == 66, m.group(0) if m else 'MISSING')
    rtxt = read(host / 'reverse_run_full.txt')
    meta = re.findall(r'(?m)^\s*(\S+)\s+\|\s*META-OK', rtxt)
    chk('OUR_REVERSE_20_MATCHES_A_CLAIM',
        len(meta) == ident['results']['full_chain']['reverse_meta_ok'] == 20 and
        rtxt.count('INJECTED ') == ident['results']['full_chain']['INJECTED_lines'] == 20,
        '我方判定行=%d INJECTED=%d; A 登记 meta_ok=%s injected=%s'
        % (len(meta), rtxt.count('INJECTED '), ident['results']['full_chain']['reverse_meta_ok'],
           ident['results']['full_chain']['INJECTED_lines']))
    four_a = zf.read('proof/original_four/probe.stdout.txt').decode('utf-8', 'replace')
    four_mine = read(host / 'probe_four_before_after.txt')
    # 判定行必须按整行取: '|FAIL' 同时是 '|FAILED_ZERO_...' 的前缀(子串计数会误判)
    verd_a = re.findall(r'(?m)^REVIEW\|(\w+)\|(PASS|FAIL)$', four_a)
    verd_mine = re.findall(r'(?m)^REVIEW\|(\w+)\|(PASS|FAIL)$', four_mine)
    chk('FOUR_PROBES_BOTH_VERSIONS_SAY_ALL_PASS',
        'REVIEW_SUMMARY|checks=4|failures=0|hardware_actions=0' in four_a and
        len(verd_a) == 4 and {v for _n, v in verd_a} == {'PASS'} and
        len(verd_mine) == 8 and {v for _n, v in verd_mine} == {'PASS'} and
        {n for n, _v in verd_a} == {n for n, _v in set(verd_mine)},
        'A 侧判定行=%d 全 PASS; 我方归档件里基线+本轮共 %d 行全 PASS, 且两边 REVIEW 身份集合相同'
        % (len(verd_a), len(verd_mine)))
    cp_a = zf.read('proof/compound_probe/probe.stdout.txt').decode('utf-8', 'replace')
    obs_a = [l for l in cp_a.splitlines() if l.startswith('OBS|COMMIT_CFG_AND_ROLLBACK_FAULT')]
    obs_mine = [l for l in four_mine.splitlines() if l.startswith('OBS|COMMIT_CFG_AND_ROLLBACK_FAULT')]
    cp_mine = read(host / 'probe_compound_before_after.txt')
    obs_mine2 = [l for l in cp_mine.splitlines() if l.startswith('OBS|COMMIT_CFG_AND_ROLLBACK_FAULT')]
    chk('COMPOUND_OBS_IDENTICAL_BETWEEN_A_AND_US',
        len(obs_a) == 1 and obs_a[0] in cp_mine,
        'A 与我方对同一次修后观察一字不差: %s' % (obs_a[0][:96] if obs_a else 'MISSING'))
    chk('COMPOUND_A_CLAIMS_MATCH_OBS_FIELDS',
        all(ident['results']['compound_probe'][k] is not None for k in ('pass', 'rc')) and
        'refused=1' in obs_a[0] and 'no_rollback=0' in obs_a[0] and 'ctx_takes=3' in obs_a[0] and
        ident['results']['compound_probe']['cfg_fault_arm_reached'] is True, obs_a[0][:110])

    # ---------- 5) A 包内 proof 的自洽 ----------
    fcl = zf.read('proof/full_chain.log').decode('utf-8', 'replace')
    chk('A_FULL_CHAIN_LOG_SELF_CONSISTENT',
        re.search(r'SUMMARY\|cases=66 pass=66 fail=0', fcl) is not None and
        len(re.findall(r'(?m)^\s*\S+\s+\|\s*META-OK', fcl)) == 20 and fcl.count('INJECTED ') == 20,
        'A 侧日志: 66/66 行存在, META-OK 判定行=%d, INJECTED=%d'
        % (len(re.findall(r'(?m)^\s*\S+\s+\|\s*META-OK', fcl)), fcl.count('INJECTED ')))
    img = json.loads(zf.read('proof/image_identity_review.json').decode('utf-8'))
    our_bin = REPO / (FWD + '/rt-thread-motor-m1-candidate.bin')
    Aimg = img.get('images', {})
    chk('A_IMAGE_CLAIMS_MATCH_OUR_ARCHIVE',
        all(Aimg.get(k, {}).get('sha256') == v['sha256'] and Aimg[k].get('bytes') == v['bytes']
            for k, v in json.loads(read(REPO / (FWD + '/build_manifest.json')))['images'].items()) and
        Aimg.get('bin', {}).get('sha256') == CAND_BIN_SHA and Aimg['bin']['bytes'] == our_bin.stat().st_size,
        'A 登记的 BIN/AXF/MAP/调用图四件 bytes+sha256 与我方 manifest 与磁盘全一致(BIN=%s %d B)'
        % (str(Aimg.get('bin', {}).get('sha256'))[:12], Aimg.get('bin', {}).get('bytes') or 0))
    chk('A_CLAIMS_AXF_EQUAL_BIN_AND_WE_RECOMPUTE',
        img.get('Flash_segment_byte_equal_to_BIN') is True and
        all(s.get('equal_bin') for s in img.get('segments', [])),
        'A 侧 Flash 段==BIN 为真(%d 段); 本工具下一判据独立复算' % len(img.get('segments', [])))
    mc = json.loads(zf.read('proof/manifest_checks.json').decode('utf-8'))
    chk('A_MANIFEST_CHECKS_COVER_OUR_TWO_LISTS',
        json.dumps(mc).count('phase8-m8-r7d1-ard4b') >= 2,
        'A 的 manifest_checks.json %d B, 点名本轮两份清单=%s' % (len(zf.read('proof/manifest_checks.json')),
                                                  'phase8-m8-r7d1-ard4b' in json.dumps(mc)))

    # ---------- 6) 镜像侧本轮未重建 ----------
    binp = REPO / (FWD + '/rt-thread-motor-m1-candidate.bin')
    chk('CANDIDATE_IMAGE_BYTES_UNCHANGED', sha256_bytes(binp.read_bytes()) == CAND_BIN_SHA,
        '签收候选 BIN 仍是 %s.. (%d B)' % (CAND_BIN_SHA[:12], binp.stat().st_size))
    eq, note = axf_flash_equal_bin(REPO / (FWD + '/rt-thread-motor-m1.axf'), binp)
    chk('AXF_FLASH_SEGMENT_EQUAL_BIN_RECOMPUTED', eq, note)
    for p, w in ((PREVD.replace('reviews', 'firmware') + '/rt-thread-motor-m1-candidate.bin', PREV_BIN_SHA),):
        chk('PREVIOUS_IMAGE_UNCHANGED', sha256_bytes((REPO / p).read_bytes()) == w,
            '上一版(e1d7f3d)仍是 %s..' % w[:8])
    chk('OLDER_IMAGE_UNCHANGED',
        sha256_bytes((REPO / ('reports/firmware/phase8-m8-r7d1-async-learn-offline/'
                              'rt-thread-motor-m1-candidate.bin')).read_bytes()) == OLDER_BIN_SHA,
        '再上一版(4d50310)仍是 %s..' % OLDER_BIN_SHA[:8])
    ltxt = read(REPO / (DEL + '/build/build_r7d2.log'))
    chk('BUILD_LOG_STILL_ZERO_ERROR_ZERO_WARNING',
        re.search(r'- 0 Error\(s\), 0 Warning\(s\)', ltxt) is not None,
        '本轮不重构建: 沿用已签收那一版日志的 0E/0W 结果行')

    # ---------- 7) 本轮改动面 ----------
    rc, out = sh('git', 'diff', '--name-only', 'HEAD', '--')
    unstaged = [l for l in out.splitlines() if l.strip()]
    rc, out2 = sh('git', 'diff', '--name-only', TIP, '--')
    since_tip = [l for l in out2.splitlines() if l.strip()]
    rc, out3 = sh('git', 'ls-files', '--others', '--exclude-standard')
    untracked = [l for l in out3.splitlines() if l.strip()]
    chk('NO_PRODUCT_OR_HOST_CHANGE_THIS_ROUND',
        not [p for p in since_tip + unstaged if p.startswith('rt-thread-master/') or p.startswith('tests/')],
        '相对签收 tip %s: 产品与主机链路径被改动数=%d(必须 0)' % (
            TIP[:7], len([p for p in since_tip + unstaged if p.startswith('rt-thread-master/') or p.startswith('tests/')]))
        )
    outside = [p for p in set(since_tip + unstaged + untracked)
               if p not in ALLOWED and not p.startswith(ALLOWED_PREFIX)]
    chk('SCOPE_DOCS_AND_EVIDENCE_ONLY', not outside,
        ';'.join(outside[:8]) or '改动 %d 件, 全部在文档/证据白名单内' % len(set(since_tip + unstaged + untracked)))
    for i, must in enumerate((DEL + '/implementation_contract.md',
                              'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.md',
                              'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.json',
                              RR + '/run_record.md'), 1):
        chk('TOUCHED_DOC_%02d_%s' % (i, os.path.basename(must).replace('.', '_')),
            must in set(since_tip + untracked), must)

    # ---------- 8) 两处勘误落地 ----------
    contract = read(REPO / (DEL + '/implementation_contract.md'))
    hmd = read(REPO / 'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.md')
    hjson = read(REPO / 'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn.json')
    OLD_SENTENCES = [
        ('contract:begin 是最后一个可失败步骤', '本轮最后一个可失败步骤', contract),
        ('contract:校验全部在取锁之前', ')全部在取锁之前完成', contract),
        ('contract:OK 行 learn 作废', 'learn 作废、zero 保持', contract),
        ('md:唯一会失败的一步', '唯一会失败的一步', hmd),
        ('md:begin 在取锁之前完成全套校验', '在取锁之前完成全套校验', hmd),
        ('md:OK 行 learn 作废', '| learn 作废 |', hmd),
        ('json:唯一会失败', '唯一会失败的一步', hjson),
        ('json:learn 作废(OK 行)', '"OK": "learn 作废', hjson),
    ]
    # 勘误节(§8)按设计逐字保留旧句作对照, 所以判据只看正文部分
    def body(t):
        return re.split(r'(?m)^## 8[ .]', t)[0]
    still = []
    for n, needle, hay in OLD_SENTENCES:
        if needle in body(hay):
            still.append(n)
    chk('OLD_WRONG_SENTENCES_GONE', not still,
        ';'.join(still) or '八处旧句在三份文档的正文里都已不再出现(§8 勘误节按设计保留原文对照, '
                          '另有 %s/errata.md 逐字登记)' % ACC)
    chk('NEW_WIRING_STATED_WITH_CODE_EVIDENCE',
        'config_in_range' in contract and '锁内' in contract and '两枚凭据' in contract and
        '两枚凭据都保持原值' in hmd.replace('**', '') and 'project_config.c:503-510' in contract and
        'diag_relative.c:2215' in contract,
        '新口径都点名了源码位置(begin 的锁内范围复核 + 提交点 -1/-1)')
    chk('CREDENTIAL_ROW_CONSISTENT_WITH_CALL',
        re.search(r'rel_pending_publish_locked\(&cand, -1, -1\)', contract) is not None,
        '合同里写明的调用参数就是 -1/-1(两枚旗标都不写)')
    pc_h = read(REPO / (APP_REL + '/project_config.h'))
    drift = '都在副本上算' in pc_h and '然后取 cfg 锁' in pc_h
    chk('PRODUCT_COMMENT_DRIFT_REGISTERED_NOT_FIXED', drift and
        'project_config.h:82-83' in read(REPO / (ACC + '/errata.md')),
        '产品注释仍写"都在副本上算 …… 然后取 cfg 锁"(本轮不改产品), 已在 errata.md 点名登记')
    chk('PRODUCT_HEADER_BYTES_UNCHANGED_SINCE_TIP',
        blob_bytes(TIP, APP_REL + '/project_config.h') == (REPO / (APP_REL + '/project_config.h')).read_bytes(),
        'project_config.h 的磁盘字节与签收 tip 上的 blob 相同 => 镜像仍是被签收那一版')

    # ---------- 9) run_record ----------
    rr = read(REPO / (RR + '/run_record.md'))
    chk('RUN_RECORD_SECTION_18_WITH_17_AND_16_KEPT',
        '## 18' in rr and '## 17' in rr and '## 16' in rr and
        'ACCEPT_OFFLINE_WITH_DOCUMENTATION_ERRATA' in rr,
        '§18 已追加; §17/§16 原文未删(§17 的两处旧口径以 §18 的逐字对照更正, 不覆写)')

    # ---------- 10) 泄漏扫描 ----------
    leak_pats = [('abs-c-forward', b'C:' + b'/Use' + b'rs/'),
                 ('abs-c-backslash', b'C:' + b'\\Use' + b'rs\\'),
                 ('user-dir-name', b'LM' + b'X')]
    synthetic = {'abs-c-forward': b'x C:/Use' + b'rs/y', 'abs-c-backslash': b'C:' + b'\\Use' + b'rs\\y',
                 'user-dir-name': b'a' + b'LM' + b'Xb'}
    miss = [n for n, pat in leak_pats if pat not in synthetic[n]]
    chk('LEAK_SCANNER_FIRED_ON_SYNTHETIC', not miss,
        ';'.join(miss) or '三条模式各自在合成样本上命中(判据不是空转)')
    leaks = []
    for p in sorted(set(since_tip + unstaged + untracked)):
        f = REPO / p
        if not f.is_file():
            continue
        b = f.read_bytes()
        for name, pat in leak_pats:
            if pat in b:
                leaks.append('%s:%s' % (p, name))
    chk('NO_LOCAL_PATH_LEAK_IN_THIS_ROUND', not leaks,
        ';'.join(leaks) or '本轮改动件(%d)里没有本机用户目录/用户名' % len(set(since_tip + unstaged + untracked)))

    res = {'accepted_review_commit': TIP, 'package_bytes': len(raw), 'package_sha256': sha256_bytes(raw),
           'verdict': ident.get('verdict'), 'checks': CHECKS,
           'source_binding_rows': rows, 'compound_obs_a': obs_a,
           'changed_this_round': sorted(set(since_tip + unstaged + untracked)),
           'old_sentences_removed': [n for n, _x, _h in OLD_SENTENCES]}
    fails = [c['id'] for c in CHECKS if not c['ok']]
    res['total'] = len(CHECKS)
    res['fail'] = len(fails)
    res['fail_ids'] = fails
    io.open(str(out_dir / 'acceptance_summary.json'), 'w', encoding='utf-8', newline='\n').write(
        json.dumps(res, ensure_ascii=False, indent=1) + '\n')
    with io.open(str(out_dir / 'acceptance_checks.txt'), 'w', encoding='utf-8', newline='\n') as w:
        w.write('# A 签收轮(纯文档/证据)离线独立复算: 设备动作 0, 未改产品, 未重构建\n')
        w.write('# 包: %s %d B sha256=%s\n' % (os.path.basename(zip_path), len(raw), sha256_bytes(raw)))
        for c in CHECKS:
            w.write('%-4s %s %s\n' % ('PASS' if c['ok'] else 'FAIL', c['id'], c['detail']))
        w.write('TOTAL=%d FAIL=%d fails=%s\n' % (len(CHECKS), len(fails), ','.join(fails) or '-'))
    print('TOTAL=%d FAIL=%d %s' % (len(CHECKS), len(fails), ','.join(fails)))
    for c in CHECKS:
        if not c['ok']:
            print('  FAIL %s %s' % (c['id'], c['detail']))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
