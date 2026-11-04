#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r7d2_offline_checks.py - M8-R7D1 ARD4b(提交点两阶段化)续修轮的离线独立复算(设备动作 0)

每一件都自己算一遍, 不引用 A 的结论, 也不引用上一轮工具的结论:
  1) 被审件(A 的续修包)身份: 外层 zip 的 bytes+sha256、内层清单逐条复算且不含自身、
     判定点(ARD4/P2/diag_relative.c:2195-2209)、ARD1-ARD3 明确"收口不重开"、授权与硬件额度;
  2) A 登记的 7 件源码绑定: 在 e1d7f3d 上逐件复算 blob sha1/字节/blob sha256, 并单独登记磁盘与
     被审提交不同的件数与身份;
  3) 归档的 A 原件: reviewer_originals/ 里每一件与被 zip 内字节逐字节相同(我没改过审核人的材料),
     且我自己的修前复现输出与 A 的 compound_probe 原文同一条 OBS/REVIEW 行;
  4) 本轮改动范围: 相对被审提交被改/新增的文件必须全部落在允许清单内; 产品侧只允许三个文件;
     diag_relative.h 与 diagnosis.c 本轮不得被改(ARD1-ARD3 不重开);
  5) 主机链/负控/两组探针: 66 例全过且上一轮 66 例一条不丢、逐例 checks 只增不减; 20 族负控
     全 META-OK 且每族确实编译并跑过; 原版四探针在 e1d7f3d 与本轮的观察值逐条相同(ARD1-3 行为未动);
     复合故障探针修前 FAIL/修后 PASS 且各观察量方向正确, 并核验"ctx 臂本轮打不到"这条如实登记;
  6) 本轮改动的打印: why 字面量集合当场从源码算, 逐调用按实参最宽展开核 <=120 B(含源 LF),
     无裸 NUL、UTF-8 完整, 上一轮已证的主行未被改宽;
  7) 构建侧: 清单登记的镜像身份与磁盘逐字节复算一致, AXF 的 Flash 段 == BIN, 增量对账当场重算,
     两版旧镜像(4d50310 与 e1d7f3d 那一版)未被改写;
  8) 交付件里不得出现本机用户目录/用户名; 扫描器先用合成样本自证"会命中"。

用法:  python r7d2_offline_checks.py <out_dir>   (环境变量 R7D2_ZIP 可覆盖被审包路径)
输出:  <out_dir>/summary.json + <out_dir>/checks.txt
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
BASE = 'e1d7f3d90415fdf30344ed95a08065c1373fab01'
PREV_BASE = '4d503103cf3f1eb6f5113b10cc8683954d510ac6'
APP_REL = ('rt-thread-master/rt-thread-master/bsp/novosns/ns800/'
           'ns800rt7p65-nssinepad/applications')
ROUND = 'reports/reviews/phase8-m8-r7d1-ard4b-commit-txn'
FW = 'reports/firmware/phase8-m8-r7d1-ard4b-commit-txn'
PREV_ROUND = 'reports/reviews/phase8-m8-r7d1-ard-rework'
PREV_FW = 'reports/firmware/phase8-m8-r7d1-ard-rework'
R7D1_FW = 'reports/firmware/phase8-m8-r7d1-async-learn-offline'
ALLOWED = {
    APP_REL + '/diag_relative.c',
    APP_REL + '/project_config.c',
    APP_REL + '/project_config.h',
    'tests/host/rel_main.c',
    'tests/host/run_diag_relative_m8.sh',
    '.gitattributes',
    'reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md',
    'reports/reviews/phase8-m8-r7f2b-safety-print-fix/SHA256SUMS.txt',
}
ALLOWED_PREFIX = (ROUND + '/', FW + '/', 'reports/agent_handoffs/phase8-m8-r7d1-ard4b-commit-txn')
ZIP_DEFAULT = Path(os.path.expandvars(r'%USERPROFILE%')) / 'Downloads' / \
    'NS800_R7D1_ARD4_Followup_e1d7f3d_20261105.zip'
ZIP_BYTES = 21347
ZIP_SHA256 = '411ccf2d7ca72af4784c903225828d4a00973a68a27f633d15792a2b42c1d02c'
REVIEWED_BIN_SHA = '165d8bdc708b042125d99db88f56a62ddca635d30202e80abe96c148e35bc478'
OLDER_BIN_SHA = 'df7584c3df2e682d07564ee70f05ecc7b608052a7fa739fb0c21b4f942b15c2b'
CHECKS = []


def chk(cid, ok, detail=''):
    CHECKS.append({'id': cid, 'ok': bool(ok), 'detail': detail})
    return bool(ok)


def sh(*args):
    p = subprocess.run(list(args), cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode('utf-8', 'replace').strip()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def blob_bytes(rev, path):
    p = subprocess.run(['git', 'cat-file', 'blob', '%s:%s' % (rev, path)],
                       cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.stdout if p.returncode == 0 else None


def read(p):
    return io.open(str(p), encoding='utf-8', errors='replace').read()


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


def obs(t, tag, key):
    for l in t.splitlines():
        if l.startswith('OBS|') and tag in l:
            m = re.search(r'%s=(\S+)' % key, l)
            return m.group(1) if m else 'KEY-MISSING'
    return 'OBS-MISSING'


def function_body(src, name):
    i = src.find(name + '(')
    if i < 0:
        return ''
    j = src.find('{', i)
    d, k = 0, j
    while k < len(src):
        if src[k] == '{':
            d += 1
        elif src[k] == '}':
            d -= 1
            if d == 0:
                return src[j:k + 1]
        k += 1
    return src[j:]


def parse_kprintfs(src):
    """格式串 -> 该格式串所有调用点的实参表(逐调用取, 不合并)。"""
    res = {}
    i = 0
    while True:
        j = src.find('rt_kprintf(', i)
        if j < 0:
            return res
        k = j + len('rt_kprintf(')
        depth, instr, esc = 1, False, False
        while k < len(src) and depth:
            c = src[k]
            if instr:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    instr = False
            elif c == '"':
                instr = True
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            k += 1
        body = src[j + len('rt_kprintf('):k - 1]
        args, cur, d2, in2 = [], '', 0, False
        for c in body:
            if in2:
                cur += c
                if c == '"':
                    in2 = False
                continue
            if c == '"':
                in2 = True
                cur += c
            elif c in '([{':
                d2 += 1
                cur += c
            elif c in ')]}':
                d2 -= 1
                cur += c
            elif c == ',' and d2 == 0:
                args.append(cur)
                cur = ''
            else:
                cur += c
        if cur.strip():
            args.append(cur)
        mm = re.match(r'\s*"((?:[^"\\]|\\.)*)"', args[0] if args else '')
        if mm:
            res.setdefault(mm.group(1), []).extend(args[1:])
        i = k


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / (ROUND + '/offline')
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = os.environ.get('R7D2_ZIP') or str(ZIP_DEFAULT)

    # ---------- 1) 被审包身份 ----------
    chk('ZIP_PRESENT', os.path.isfile(zip_path), os.path.basename(zip_path))
    raw = io.open(zip_path, 'rb').read()
    chk('ZIP_BYTES', len(raw) == ZIP_BYTES, 'bytes=%d 期望=%d' % (len(raw), ZIP_BYTES))
    chk('ZIP_SHA256', sha256_bytes(raw) == ZIP_SHA256, sha256_bytes(raw))
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()
    entries = []
    for line in zf.read('SHA256SUMS.txt').decode('utf-8', 'replace').strip().splitlines():
        m = re.match(r'^([0-9a-f]{64})  (.+)$', line.strip())
        if m:
            entries.append((m.group(2), m.group(1)))
    chk('MANIFEST_ENTRY_COUNT', len(entries) == 19, '%d 条(3 件正文 + 12 件 proof + 4 件 reproducer)' % len(entries))
    chk('MANIFEST_FILE_COUNT', len(names) == 20,
        'zip 内 %d 个成员 = 19 条登记 + SHA256SUMS.txt 自身' % len(names))
    chk('MANIFEST_EXCLUDES_SELF', all(n != 'SHA256SUMS.txt' for n, _ in entries),
        ','.join(n for n, _ in entries if n == 'SHA256SUMS.txt') or '清单不含自身')
    bad = []
    for name, want in entries:
        if name not in names:
            bad.append(name + ':MISSING')
            continue
        got = sha256_bytes(zf.read(name))
        if got != want:
            bad.append('%s:want=%s.. got=%s..' % (name, want[:8], got[:8]))
    chk('MANIFEST_ALL_MATCH', not bad, ';'.join(bad) or '%d/%d' % (len(entries), len(entries)))
    scope = json.loads(zf.read('FIX_SCOPE.json').decode('utf-8'))
    ident = json.loads(zf.read('IDENTITY.json').decode('utf-8'))
    f = scope.get('finding', {})
    chk('VERDICT_CHANGES_REQUIRED', scope.get('base_commit') == BASE and f.get('result') == 'CHANGES_REQUIRED',
        'base=%s result=%s' % (str(scope.get('base_commit'))[:7], f.get('result')))
    chk('SINGLE_FINDING_ARD4_P2', f.get('id') == 'ARD4' and f.get('priority') == 'P2',
        '%s/%s' % (f.get('id'), f.get('priority')))
    chk('FINDING_LOCATION_RESOLVES',
        f.get('path', '').endswith('diag_relative.c') and f.get('line_start') == 2195 and f.get('line_end') == 2209,
        '%s:%s-%s' % (os.path.basename(f.get('path', '')), f.get('line_start'), f.get('line_end')))
    # 判定点指向的行在本轮定稿里已经不再是"先烧配置再回滚"那两句 —— 记下来供人核对, 不当下结论
    body_src = io.open(str(REPO / (APP_REL + '/diag_relative.c')), encoding='utf-8', newline='').read()
    chk('CLOSED_NOT_REOPENED', scope.get('closed_without_reopening') == ['ARD1', 'ARD2', 'ARD3'] and
        ident.get('closed_findings') == ['ARD1', 'ARD2', 'ARD3'],
        'scope=%s identity=%s' % (scope.get('closed_without_reopening'), ident.get('closed_findings')))
    chk('HW_CAP_ZERO', scope.get('hardware_actions_cap') == 0 and ident.get('hardware_actions') == 0,
        'scope=%s identity=%s' % (scope.get('hardware_actions_cap'), ident.get('hardware_actions')))
    chk('NO_NEW_APPROVAL_ROUND', 'No new registration or approval round' in scope.get('authorization', ''),
        repr(scope.get('authorization', ''))[:90])
    fb = scope.get('field_boundaries', {})
    chk('PRESERVE_ON_BOARD_IDENTITY', fb.get('on_board', '').endswith('b502 / 502dca5d'),
        repr(fb.get('on_board', '')))
    chk('PRESERVE_NEW_IMAGES_NOT_ON_BOARD',
        fb.get('df7584c3') == 'NOT_ON_BOARD' and fb.get('165d8bdc') == 'NOT_ON_BOARD',
        'df7584c3=%s 165d8bdc=%s' % (fb.get('df7584c3'), fb.get('165d8bdc')))
    chk('PRESERVE_CLAIMS_FALSE', fb.get('polarity_measured') is False and fb.get('core_protection_verified') is False,
        'polarity=%s core=%s' % (fb.get('polarity_measured'), fb.get('core_protection_verified')))
    chk('PRESERVE_R7F5A_PENDING', fb.get('R7F5A_poweroff') == 'PENDING_OPERATOR_REPORT',
        str(fb.get('R7F5A_poweroff')))
    chk('PRODUCT_SCOPE_AUTHORIZED', 'direct configuration transaction dependencies if needed' in
        json.dumps(scope.get('product_scope', [])),
        'A 明确允许动 commit 的直接配置事务依赖 => 本轮动 project_config.c/.h 在授权内')

    # ---------- 2) A 登记的源码绑定(在被审提交上复算) ----------
    rc, head = sh('git', 'rev-parse', 'HEAD')
    chk('HEAD_IS_REVIEWED_COMMIT', head == BASE, 'HEAD=%s 被审=%s' % (head[:9], BASE[:9]))
    chk('IDENTITY_REVIEW_COMMIT', ident.get('review_commit') == BASE and
        ident.get('parent_commit') == PREV_BASE,
        'review=%s parent=%s' % (str(ident.get('review_commit'))[:7], str(ident.get('parent_commit'))[:7]))
    binds = ident.get('source_bindings', [])
    chk('IDENTITY_BINDINGS_COUNT', len(binds) == 7, '%d 件' % len(binds))
    ok = badn = 0
    rows = []
    for s in binds:
        path = s['path']
        b = blob_bytes(BASE, path)
        if b is None:
            badn += 1
            rows.append({'path': path, 'err': 'blob-missing'})
            continue
        rc, oid = sh('git', 'rev-parse', '%s:%s' % (BASE, path))
        blob_ok = (oid == s['git_blob'] and len(b) == s['bytes'] and sha256_bytes(b) == s['sha256'])
        disk = REPO / path
        disk_b = disk.read_bytes() if disk.is_file() else b''
        rows.append({'path': path, 'blob_sha1_ok': oid == s['git_blob'],
                     'blob_bytes': len(b), 'declared_bytes': s['bytes'],
                     'blob_sha256_ok': sha256_bytes(b) == s['sha256'],
                     'disk_equals_blob': disk_b == b, 'disk_bytes': len(disk_b),
                     'disk_sha256': sha256_bytes(disk_b) if disk_b else ''})
        ok += 1 if blob_ok else 0
        badn += 0 if blob_ok else 1
    chk('A_SOURCE_BINDINGS_RECOMPUTED', badn == 0,
        'ok=%d bad=%d(在 %s 上逐件复算 blob sha1/字节/sha256)' % (ok, badn, BASE[:7]))
    changed_now = [os.path.basename(r['path']) for r in rows if not r.get('disk_equals_blob', True)]
    want_now = ['diag_relative.c', 'project_config.c', 'project_config.h', 'rel_main.c',
                'run_diag_relative_m8.sh']
    chk('DISK_VS_BLOB_RECORDED', sorted(changed_now) == sorted(want_now),
        '磁盘已不同于被审提交的件=%d => %s(应当是产品 3 + 主机 2; diag_relative.h 与 diagnosis.c 未改)'
        % (len(changed_now), changed_now))
    chk('UNCHANGED_BOUNDINGS_EXPLICIT',
        [os.path.basename(s['path']) for s in rows if s.get('disk_equals_blob')] ==
        ['diag_relative.h', 'diagnosis.c'],
        '仍与被审提交逐字节相同的登记件=%s' % [os.path.basename(s['path']) for s in rows
                                             if s.get('disk_equals_blob')])
    res = ident.get('results', {})
    chk('A_EXISTING_CASES_66', res.get('existing_chain', {}).get('cases_pass') == 66, str(res.get('existing_chain')))
    chk('A_EXISTING_REVERSE_19', res.get('existing_chain', {}).get('reverse_meta_ok') == 19,
        str(res.get('existing_chain', {}).get('reverse_meta_ok')))
    chk('A_ORIGINAL_FOUR_PASS_AT_BASE', res.get('original_four') == {'checks': 4, 'pass': 4, 'run_rc': 0},
        str(res.get('original_four')))
    chk('A_COMPOUND_FAIL_RC1', res.get('ARD4_compound_failure', {}).get('run_rc') == 1 and
        res.get('ARD4_compound_failure', {}).get('pending_changed') is True,
        str(res.get('ARD4_compound_failure')))

    # ---------- 3) 归档的 A 原件逐字节 == zip 内字节 ----------
    ro = REPO / ROUND / 'reviewer_originals'
    mism = []
    for name, want in entries:
        p = ro / name
        if not p.is_file():
            mism.append(name + ':MISSING')
            continue
        b = p.read_bytes()
        if sha256_bytes(b) != want or b != zf.read(name):
            mism.append('%s:字节不同于 zip 内原件' % name)
    chk('REVIEWER_ORIGINALS_VERBATIM', not mism,
        ';'.join(mism) or '%d 件 A 的原件与 zip 内字节逐字节相同(未改审核人材料)' % len(entries))
    # A 自己的清单(第 20 个成员)不在上面 19 条里(清单不给自己作证), 单独逐字节核 + 登记 hash
    am_orig = zf.read('SHA256SUMS.txt')
    am_disk = (ro / 'SHA256SUMS.txt').read_bytes() if (ro / 'SHA256SUMS.txt').is_file() else b''
    chk('REVIEWER_SELF_MANIFEST_VERBATIM', am_orig == am_disk and len(am_orig) > 0,
        'A 的 SHA256SUMS.txt 与 zip 内字节相同(%d B, sha256=%s) —— 它进本轮清单会被"清单不哈希自身"的'
        '规则一起排除, 因此这条由本判据承担' % (len(am_disk), sha256_bytes(am_disk)[:16]))
    a_compound = zf.read('proof/compound_probe/probe.stdout.txt').decode('utf-8', 'replace')
    my_pre = read(REPO / (ROUND + '/host/probe_compound_before_after.txt'))
    key_a = [l for l in a_compound.splitlines() if l.startswith('OBS|COMMIT_CFG_AND_ROLLBACK_FAULT')]
    key_mine = [l for l in my_pre.splitlines() if l.startswith('OBS|COMMIT_CFG_AND_ROLLBACK_FAULT')]
    chk('PRE_FIX_REPRO_MATCHES_A_LINE_BY_LINE',
        len(key_a) == 1 and len(key_mine) == 2 and key_a[0] in my_pre,
        'A 包内 OBS 行 %s; 我修前复现的 OBS 行 %s; 逐项对照里含该行=%s'
        % (key_a[0].split('|')[-1][:40] if key_a else 'MISSING',
           (key_mine[0].split('|')[-1][:40] if key_mine else 'MISSING'),
           key_a[0] in my_pre if key_a else False))
    chk('A_PRODUCT_EDITS_ZERO', ident.get('product_edits_by_A') == 0 and
        ident.get('arm_compiler_rerun_by_A') is False,
        'A 自己没改产品码也没重跑 arm 编译器 => 修前 FAIL 只能由我的复现来固定')

    # ---------- 4) 改动范围 ----------
    rc, out = sh('git', 'diff', '--name-only', BASE, '--')
    changed = [l for l in out.splitlines() if l.strip()]
    rc, out2 = sh('git', 'ls-files', '--others', '--exclude-standard')
    untracked = [l for l in out2.splitlines() if l.strip()]
    outside = [p for p in changed + untracked
               if p not in ALLOWED and not p.startswith(ALLOWED_PREFIX)]
    chk('SCOPE_NO_STRAY_FILES', not outside,
        ';'.join(outside[:6]) or 'changed=%d untracked=%d' % (len(changed), len(untracked)))
    for must in ('diag_relative.c', 'project_config.c', 'project_config.h', 'rel_main.c',
                 'run_diag_relative_m8.sh'):
        chk('TOUCHED_' + must, any(p.endswith(must) for p in changed), must)
    for mustnt in ('diag_relative.h', 'diagnosis.c', 'motor.c', 'sensor_service.c'):
        chk('NOT_TOUCHED_' + mustnt, not any(p.endswith(mustnt) for p in changed),
            'ARD1-ARD3 不重开 => %s 本轮一字未动' % mustnt)
    prod_changed = [p for p in changed if p.startswith('rt-thread-master/') and p not in ALLOWED]
    chk('PRODUCT_SCOPE_EXACTLY_THREE',
        sorted(p for p in changed if p.startswith(APP_REL)) ==
        sorted([APP_REL + '/diag_relative.c', APP_REL + '/project_config.c', APP_REL + '/project_config.h']) and
        not prod_changed,
        ';'.join(prod_changed) or '产品侧改动面恰好 3 件: diag_relative.c + project_config.c/.h')
    stale = [p for p in changed + untracked if p.startswith(PREV_ROUND) or p.startswith(PREV_FW)
             or p.startswith(R7D1_FW)]
    chk('OLD_EVIDENCE_AND_IMAGES_UNTOUCHED', not stale,
        ';'.join(stale) or '上一轮(4d50310 复审轮)与被审轮的证据目录、两版旧镜像目录里的文件一个都没改')

    # ---------- 5) 主机链 / 负控 / 两组探针 ----------
    host = REPO / ROUND / 'host'
    ntxt = read(host / 'normal_chain_out.txt')
    m = re.search(r'SUMMARY\|cases=(\d+) pass=(\d+) fail=(\d+) pre_bad=(\d+) executed=(\d+)', ntxt)
    chk('NORMAL_66_ALL_PASS', bool(m) and m.group(1) == m.group(2) == m.group(5) == '66'
        and m.group(3) == '0' == m.group(4), m.group(0) if m else 'MISSING')
    mb = re.search(r'build_rc=(\d+) warnings=(\d+)', ntxt)
    chk('HOST_BUILD_RC0_WARNINGS0', bool(mb) and mb.group(1) == '0' and mb.group(2) == '0',
        mb.group(0) if mb else 'MISSING')
    prev_ledger = read(REPO / (PREV_ROUND + '/host/case_ledger.txt'))
    prev_checks = dict((c, int(k)) for c, k in re.findall(r'^(\S+)\s+PASS\s+checks=(\d+)', prev_ledger, re.M))
    now_checks = dict((c, int(k)) for c, k in re.findall(r'CASE\|(\S+)\|PASS\|checks=(\d+)', ntxt))
    chk('PRIOR_66_CASES_KEPT', len(prev_checks) == 66 and not (set(prev_checks) - set(now_checks)),
        '上一轮台账 %d 例, 本轮丢了 %d 例%s' % (len(prev_checks), len(set(prev_checks) - set(now_checks)),
                                        str(sorted(set(prev_checks) - set(now_checks)))))
    chk('NO_NEW_CASE_ID_THIS_ROUND', not (set(now_checks) - set(prev_checks)),
        '本轮按 A 的要求并入既有 A16, 不新增用例编号; 新增的=%s' % sorted(set(now_checks) - set(prev_checks)))
    shrunk = sorted(c for c in prev_checks if c in now_checks and now_checks[c] < prev_checks[c])
    grew = sorted((c, now_checks[c] - prev_checks[c]) for c in prev_checks
                  if c in now_checks and now_checks[c] > prev_checks[c])
    chk('CASE_CHECKS_NEVER_SHRINK', not shrunk,
        'checks 变少的例=%s(一条断言都没删) 变多的例=%s' % (shrunk, grew))
    chk('A16_CHECKS_GREW', 'A16-commit-refused-keeps-config-and-candidate' in dict(grew),
        'A16 的 checks %s -> %s' % (prev_checks.get('A16-commit-refused-keeps-config-and-candidate'),
                                 now_checks.get('A16-commit-refused-keeps-config-and-candidate')))
    rtxt = read(host / 'reverse_run_full.txt')
    fam = re.search(r'执行集合: normal((?: reverse:\S+)+)', rtxt)
    fams = fam.group(1).split() if fam else []
    chk('REVERSE_FAMILY_COUNT_20', len(fams) == 20, '%d 族(上一轮 19 + 本轮新增 RDV10)' % len(fams))
    chk('REVERSE_NEW_FAMILY_RDV10',
        any(x.endswith('RDV10_commit_swallow_ctx_take_failure') for x in fams),
        ','.join(x.replace('reverse:', '') for x in fams if 'RDV10' in x) or 'MISSING')
    chk('REVERSE_RDV9_RENAMED_NOT_REUSED',
        any(x.endswith('RDV9_commit_config_before_publish') for x in fams) and
        'RDV9_commit_config_before_publish' in read(REPO / (PREV_ROUND + '/host/reverse_meta_lines.txt')),
        'RDV9 名字沿用上一轮(家族身份不变), 注入体换成新顺序的等价缺陷')
    meta = re.findall(r'(?m)^\s*(\S+)\s+\|\s*META-OK', rtxt)
    chk('REVERSE_META_OK_20_LINES', len(meta) == 20, '判定行(带 | META-OK)= %d' % len(meta))
    chk('REVERSE_META_OK_COUNT_NOT_SUBSTRING_TRAP', rtxt.count('META-OK') != len(meta),
        '子串计数=%d 而判定行=%d: 正文里也出现这个词, 判据按行取' % (rtxt.count('META-OK'), len(meta)))
    chk('REVERSE_NO_META_FAIL', not re.search(r'META-(?!OK)\w+', rtxt), '没有 META-FAIL/其他 META 状态')
    chk('REVERSE_EACH_COMPILED_AND_RAN', rtxt.count('INJECTED ') == 20,
        'INJECTED 行=%d(编译失败或没跑到就不会有这行)' % rtxt.count('INJECTED '))
    chk('REVERSE_RAN_ON_FINAL_BYTES', 'M8 RELATIVE RESULT: PASS' in rtxt and 'RDV10' in rtxt,
        '反向链尾部总结果行与 RDV10 在同一次运行里')
    # 标记本身不进日志(它在被注入的那份仓外副本里), 所以这里核两件事: 脚本里有"必须命中该标记"
    # 这道硬门, 且 20 个家族各带一个具名标记 —— 两者一起才说明注入不是空转。
    runner = read(REPO / 'tests/host/run_diag_relative_m8.sh')
    fam_tags = re.findall(r'NEGATIVE-INJECTION (RV\d+|RDV\d+)', runner)
    chk('NEGATIVE_INJECTION_GATE_IN_RUNNER', 'grep -q "NEGATIVE-INJECTION"' in runner,
        'run_diag_relative_m8.sh 里 reverse_case 有一条 grep -q "NEGATIVE-INJECTION" 硬门(缺标记就报错退出)')
    chk('NEGATIVE_INJECTION_MARKERS_EXACTLY_PER_FAMILY',
        len(fam_tags) == 20 and sorted(set(fam_tags)) == sorted(fam_tags),
        '脚本内具名标记 %d 个, 去重后 %d 个(应当一一对应 20 个家族)' % (len(fam_tags), len(set(fam_tags))))
    # 四探针(ARD1-ARD3 的合同)在 e1d7f3d 与本轮必须逐值相同
    ff = read(host / 'probe_four_before_after.txt')
    chk('FOUR_POST_RC0', re.search(r'本轮 run_rc=0', ff) is not None, '本轮四探针 run_rc=0')
    chk('FOUR_FAILURES_ZERO_BOTH',
        'failures=0' in tfour_src(ff, '基线') and 'failures=0' in tfour_src(ff, '本轮'),
        '基线块与本轮块各自内部都有 REVIEW_SUMMARY failures=0(A 合同在两侧都成立)')
    same = [('NULL_STATUS', 'ctx_held'), ('ZERO_FAILED_EXIT', 'ctx_takes'),
            ('ZERO_RETRY_AFTER_FAULT_CLEARED', 'owner'), ('ZERO_RETRY_AFTER_FAULT_CLEARED', 'busy'),
            ('CLEAR_STATIC_REFUSED', 'pending_changed'), ('CLEAR_STATIC_REFUSED', 'zero_cred'),
            ('CLEAR_STATIC_REFUSED', 'learn_cred'),
            ('COMMIT_POSTWRITE_CTX_FAULT', 'cfg_changed'), ('COMMIT_POSTWRITE_CTX_FAULT', 'refused'),
            ('COMMIT_POSTWRITE_CTX_FAULT', 'ctx_takes')]
    drift = []
    for tag, key in same:
        mm = re.search(r'%s\s+%s\s+e1d7f3d (\S+) -> 本轮 (\S+)' % (re.escape(tag), re.escape(key)), ff)
        if not mm:
            drift.append('%s/%s:LINE-MISSING' % (tag, key))
        elif mm.group(1) != mm.group(2):
            drift.append('%s/%s:%s->%s' % (tag, key, mm.group(1), mm.group(2)))
    chk('ARD123_BEHAVIOUR_UNCHANGED_ON_PROBES',
        not [d for d in drift if not d.startswith('COMMIT_POSTWRITE')],
        ';'.join([d for d in drift if not d.startswith('COMMIT_POSTWRITE')]) or
        'ARD1/ARD2/ARD3 三条探针的 6 个观察量本轮与 e1d7f3d 完全相同(必要回归未越界)')
    # 第 4 条: 同一注入换了拒绝身份, 但仍是 refused=1 且 cfg 未变 => 等价故障点, 不是锚点失效
    tnow = tfour_src(ff, '本轮')
    chk('PROBE4_EQUIVALENT_FAULT_POINT_STILL_FIRES',
        obs(tnow, 'COMMIT_POSTWRITE_CTX_FAULT', 'refused') == '1' and
        obs(tnow, 'COMMIT_POSTWRITE_CTX_FAULT', 'cfg_changed') == '0' and
        'ctx-lock-commit' in tnow and 'ctx-lock-publish' not in tnow,
        '本轮该块内 refused=1 cfg_changed=0, 拒绝身份只有 ctx-lock-commit(旧身份 ctx-lock-publish '
        '在本轮的探针输出里已经不存在 => 顺序真的换了, 不是只改了报文)')
    cp = read(host / 'probe_compound_before_after.txt')
    for key, pre, post in [('refused', '1', '1'), ('no_rollback', '1', '0'), ('cfg_changed', '0', '0'),
                           ('pending_changed', '1', '0'), ('creds_kept', '1', '1'),
                           ('cfg_id', '1', '1'), ('pending_id', '2', '1'), ('ctx_takes', '5', '3')]:
        mm = re.search(r'%s\s+修前 (\S+) -> 修后 (\S+)' % key, cp)
        chk('COMPOUND_' + key, bool(mm) and mm.group(1) == pre and mm.group(2) == post,
            mm.group(0) if mm else 'MISSING(期望 修前 %s -> 修后 %s)' % (pre, post))
    chk('COMPOUND_PRE_FAIL_POST_PASS',
        'REVIEW 修前 FAIL -> 修后 PASS' in cp and '修前(e1d7f3d) run_rc=1' in cp and
        '修后(ARD4b 定稿工作树) run_rc=0' in cp, '修前 run_rc=1 FAIL, 修后 run_rc=0 PASS')
    chk('COMPOUND_CTX_ARM_INERT_DISCLOSED',
        'ctx 臂不再可能命中' in cp and '等价故障点由两处承担' in cp,
        '本轮把"回滚取 ctx 失败"这一臂打不到的事实与替代取证(A16 ②/⑤ + 四探针第 4 条)写在同一件里')

    # ---------- 6) 本轮改动的打印 ----------
    src_c = io.open(str(REPO / (APP_REL + '/diag_relative.c')), encoding='utf-8', newline='').read()
    why_lit = sorted(set(re.findall(r'\bwhy\s*=\s*"([^"]*)"', src_c)))
    # v.why / nj.why 也会被赋成宏: 把 REL_JOB_WHY_* 的字符串值并进同一集合(否则上界会漏)
    why_macro = sorted(set(re.findall(r'#define\s+(REL_JOB_WHY_\w+)\s+"([^"]*)"', src_c)))
    why_all = why_lit + [v for _, v in why_macro]
    gone = [x for x in ('config-write-rolled-back', 'config-write-no-rollback', 'ctx-lock-publish')
            if x in src_c]
    chk('OLD_ROLLBACK_WHY_LITERALS_GONE', not gone,
        ';'.join(gone) or '三条与"回滚"绑在一起的 why 字面量已从源码消失(回滚不再存在)')
    new_why = [x for x in ('cfg-lock-commit', 'ctx-lock-commit') if x in why_lit]
    chk('NEW_COMMIT_WHY_LITERALS_PRESENT', len(new_why) == 2, ','.join(new_why))
    why_w = 0
    # 为什么这个上界是闭合的: 逐行看所有 "<名以 why 结尾> = <右值>;" —— 右值只可能是
    # 字面量(已计入)、REL_JOB_WHY_* 宏(已计入)、局部 why 指针/其三元默认(默认值也是字面量)。
    # 先在"抹掉字符串字面量"的文本上定位赋值(否则格式串里的 why=%s 会被当成赋值),
    # 再用同一偏移回原文取真实右值, 这样字面量长度仍然是实测值。
    src_nostr = re.sub(r'"(?:[^"\\]|\\.)*"',
                       lambda mm: '"' + 'x' * (len(mm.group(0)) - 2) + '"', src_c)
    rhs = []
    for m in re.finditer(r'\b\w*(?:->|\.)*why\s*=\s*([^;]+);', src_nostr):
        rhs.append(src_c[m.start(1):m.end(1)].strip().replace('\n', ' '))
    lit_rhs = [mm.group(1) for r in rhs for mm in [re.match(r'^"([^"]*)"$', r)] if mm]
    tern = sorted({m.group(1) for r in rhs for m in [re.match(r'^.*:\s*"([^"]*)"$', r)] if m})
    why_all += tern
    why_w = max(len(x) for x in why_all)
    not_covered = [r for r in rhs if not (
        re.match(r'^"[^"]*"$', r) or re.match(r'^REL_JOB_WHY_\w+$', r)
        or re.match(r'^\w+$', r) or re.match(r'^\w+(?:->|\.)\w+$', r)
        or re.match(r'^[\w.]+\s*\?\s*[\w.]+\s*:\s*"[^"]*"$', r)
        or re.match(r'^[\w.]+\s*\?\s*"[^"]*"\s*:\s*[\w.]+$', r))]
    chk('WHY_VALUE_SET_CLOSED', len(rhs) >= 10 and not not_covered,
        'why 赋值 %d 处: 字面量 %d 个 + 宏 %d 个(%s) + 转发/三元 %d 处(三元默认值 %s); 未识别右值=%s; '
        '集合内最长 %d B(上一轮确立的上界 25 B)'
        % (len(rhs), len(why_lit), len(why_macro), ','.join(m for m, _ in why_macro),
           len([r for r in rhs if not re.match(r'^"[^"]*"$', r) and not re.match(r'^REL_JOB_WHY_\w+$', r)]),
           ','.join(sorted(set(tern))) or '无', not_covered or '无', why_w))
    rc, diff = sh('git', 'diff', '-U0', BASE, '--', APP_REL + '/diag_relative.c',
                  APP_REL + '/project_config.c')
    added = [l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++')]
    fmts = sorted({mm.group(1) for l in added for mm in
                   re.finditer(r'rt_kprintf\(\s*"((?:[^"\\]|\\.)*)"', l)})
    chk('ADDED_KPRINTF_FORMATS_AS_EXPECTED', len(fmts) == 0,
        '%d 个新增/改动的 rt_kprintf 格式串(A 允许改内部取锁顺序, 本轮不改报文形状; '
        'why 字面量最长 %d B 仍 <= 上一轮上界 %d B)' % (len(fmts), why_w, 25))
    # 字段宽度按头文件里的实际类型当场算, 不靠手抄表(手抄表在上一轮只是近似)。
    # 解析 diag_relative.h + project_config.h 里**全部** typedef struct 的成员;
    # 同名字段取最大位宽(只会高估, 不会低估 => 判据偏保守而不是偏乐观)。
    TYPE_DIGITS = {'rt_uint8_t': 3, 'rt_int8_t': 4, 'rt_uint16_t': 5, 'rt_int16_t': 6,
                   'rt_uint32_t': 10, 'rt_int32_t': 11, 'rt_uint64_t': 20, 'rt_int64_t': 21,
                   'rt_bool_t': 1}
    FIELD_W = {}
    field_src = {}
    UNKNOWN_TYPES = []
    for hp in (APP_REL + '/diag_relative.h', APP_REL + '/project_config.h'):
        htxt = io.open(str(REPO / hp), encoding='utf-8', newline='').read()
        for body in re.findall(r'typedef\s+struct\s*\{(.*?)\}\s*\w+\s*;', htxt, re.S):
            for decl in re.finditer(r'(rt_u?int\d+_t|rt_bool_t)\s+([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*)\s*;', body):
                if decl.group(1) not in TYPE_DIGITS:
                    UNKNOWN_TYPES.append(decl.group(1))
                    continue
                w = TYPE_DIGITS[decl.group(1)]
                for nm in re.findall(r'[A-Za-z_]\w*', decl.group(2)):
                    if FIELD_W.get(nm, 0) <= w:
                        FIELD_W[nm] = w
                        field_src[nm] = '%s:%s' % (os.path.basename(hp), decl.group(1))
    chk('FIELD_TABLE_DERIVED_FROM_HEADERS',
        len(FIELD_W) >= 40 and not UNKNOWN_TYPES and
        FIELD_W.get('hz_nominal') == 10 and FIELD_W.get('dir_mask') == 3
        and FIELD_W.get('run_adc_min') == 5 and FIELD_W.get('adc_dev_warn') == 6
        and FIELD_W.get('learn_sg_seq_first') == 10,
        '两份头文件里当场解出 %d 个成员 -> 十进制位宽上界(未识别类型=%s); 抽样 hz_nominal=%s dir_mask=%s '
        'run_adc_min=%s adc_dev_warn=%s learn_sg_seq_first=%s left_ms=%s'
        % (len(FIELD_W), UNKNOWN_TYPES or '无', FIELD_W.get('hz_nominal'), FIELD_W.get('dir_mask'),
           FIELD_W.get('run_adc_min'), FIELD_W.get('adc_dev_warn'),
           FIELD_W.get('learn_sg_seq_first'), FIELD_W.get('left_ms')))
    # tag 是 rel_show_one 的形参: 宽度取该函数全部调用点的字面量最长值(当场从源码取)
    tag_lit = [mm.group(1) for mm in re.finditer(r'rel_show_one\(\s*"([^"]*)"', src_c)]
    STR_W = {'rel_job_state_name': 14, 'rel_lock_why': 13, 'who': 6,
             'v.why': why_w, 'why': why_w, 'rel_reason_name': 22,
             'tag': max(len(x) for x in tag_lit) if tag_lit else 0}
    U8 = ('want', 'dir', 'v.want', 'v.dir')
    UNRESOLVED = []

    def arg_width(a):
        a = a.strip()
        m = re.search(r'(?:->|\.)\s*([A-Za-z_]\w*)\s*$', a)
        if m:
            # 成员访问: 数值成员按类型折; v.why 与 why 是同一组静态字面量(同一套赋值), 上界当场取
            if m.group(1) in FIELD_W:
                return FIELD_W[m.group(1)]
            if m.group(1) == 'why':
                return why_w
            UNRESOLVED.append(a[:36])
            return 11
        if '->' in a or ('.' in a and '(' not in a):
            UNRESOLVED.append(a[:36])
        for k, w in STR_W.items():
            if k in a:
                return w
        if a.startswith('"') or a.endswith('"'):
            mm = re.match(r'^"((?:[^"\\]|\\.)*)"', a)
            return len(mm.group(1)) if mm else 26
        if any(('v.' + k) in a or k in a for k in U8):
            return 3
        if a.startswith('(int)') or '(rt_int' in a:
            return 11
        if a.startswith('(unsigned)') or a.startswith('(rt_uint32_t)'):
            return 10
        UNRESOLVED.append(a[:36])
        return 26

    def worst_len(lit, args):
        n, i, ai = 0, 0, 0
        while i < len(lit):
            c = lit[i]
            if c == '\\':
                n += 1
                i += 2
                continue
            if c == '%':
                mm = re.match(r'%[-#0-9.]*([usdxX])', lit[i:])
                if mm:
                    a = args[ai] if ai < len(args) else ''
                    ai += 1
                    # 不按 32 位截断: 宽度表本身是"该实参类型的最坏十进制位数", 64 位成员也要包住
                    if mm.group(1) in 'ud':
                        n += arg_width(a)
                    elif mm.group(1) == 's':
                        n += arg_width(a)
                    else:
                        n += 8
                    i += mm.end()
                    continue
                if lit[i:i + 2] == '%%':
                    n += 1
                    i += 2
                    continue
                n += 1
                i += 1
                continue
            n += 1
            i += 1
        return n + 1                                     # 源 LF 的一个字节

    calls = parse_kprintfs(src_c)
    commit_body = function_body(src_c, 'static void rel_cmd_commit')
    commit_calls = parse_kprintfs(commit_body)
    per_commit = {f: worst_len(f, a) for f, a in commit_calls.items()}
    chk('COMMIT_PATH_PRINTS_COVERED', len(per_commit) >= 4,
        'rel_cmd_commit 内 %d 个 rt_kprintf 调用点全部逐个折算(不是空判)' % len(per_commit))
    over = {f: n for f, n in per_commit.items() if n > 120}
    chk('COMMIT_PRINTS_WITHIN_120', not over,
        ';'.join('%s=%d' % (f[:44], n) for f, n in sorted(over.items())) or
        'commit 路径最宽一次=%d B 预算=120 B(含源 LF)' % max(per_commit.values()))
    chk('COMMIT_PRINT_BOUND_NOT_VACUOUS', max(per_commit.values()) >= 60,
        '最宽一次只有 %d B => 折算表大概没生效' % max(per_commit.values()))
    chk('COMMIT_REFUSED_LINE_FITS_WIDEST_WHY',
        worst_len('[DIAG] commit REFUSED: (%s) rc=%d\n', ['why', '(int)fin']) <= 120 and why_w <= 25,
        '按本轮 why 全集最宽 %d B(上一轮已确立的上界 25 B)折算该条 = %d B <= 120 B'
        % (why_w, worst_len('[DIAG] commit REFUSED: (%s) rc=%d\n', ['why', '(int)fin'])))
    def over_120(text):
        d = {f: worst_len(f, a) for f, a in parse_kprintfs(text).items()}
        return {f: n for f, n in d.items() if n > 120}
    over_now = over_120(src_c)
    chk('PRINT_ARGS_ALL_RESOLVED', not UNRESOLVED,
        '折算表没有对任何实参取默认 26 B 兜底(未识别实参=%s) —— 每个宽度都能回到源码类型或字面量'
        % sorted(set(UNRESOLVED))[:6])
    chk('ALL_DIAG_RELATIVE_PRINTS_WITHIN_120', not over_now,
        ';'.join('%s=%d' % (f[:40], n) for f, n in sorted(over_now.items())) or
        'diag_relative.c 全部调用点按类型化实参折算后最宽 %d B, 没有一条超 120 B(含源 LF)' %
        max(worst_len(f, a) for f, a in calls.items()))
    allper = {f: worst_len(f, a) for f, a in calls.items()}
    nul = [f for f in allper if '\\0' in f or '\x00' in f]
    chk('NO_BARE_NUL_IN_FORMATS', not nul, ';'.join(nul) or '%d 条格式串里没有裸 NUL' % len(allper))
    bad_utf8 = []
    for f in allper:
        try:
            f.encode('utf-8', 'surrogateescape').decode('utf-8', 'strict')
        except Exception:
            bad_utf8.append(f[:44])
    chk('FORMATS_STRICT_UTF8', not bad_utf8, ';'.join(bad_utf8) or '逐条严格 UTF-8 解码通过')
    NEEDLE = 'job id=%u st=%s why=%s owner=%u cred=%d/%d'
    chk('LEARN_STATUS_MAIN_LINE_UNWIDENED', any(NEEDLE in k for k in calls),
        '上一轮已证的 learn_status 主行格式串本轮保持原样(未被改宽也未被删)')

    # ---------- 7) 构建侧 ----------
    mf = json.loads(read(REPO / (FW + '/build_manifest.json')))
    chk('MANIFEST_ROUND', mf.get('round') == 'phase8-m8-r7d1-ard4b-commit-txn', str(mf.get('round')))
    chk('MANIFEST_BASELINE_IS_REVIEWED', mf.get('baseline_commit') == BASE and
        mf.get('baseline_is_head_at_build') is True,
        'baseline=%s head_is_baseline=%s' % (str(mf.get('baseline_commit'))[:7],
                                         mf.get('baseline_is_head_at_build')))
    chk('MANIFEST_REVIEWED_ZIP_MATCHES_PACKAGE',
        mf['review_source']['zip_bytes'] == len(raw) and mf['review_source']['zip_sha256'] == sha256_bytes(raw)
        and mf['review_source']['inner_manifest_entries'] == len(entries),
        'manifest 登记的被审包身份(字节/散列/内层清单条数) == 本次实算')
    img_bad = []
    for k, v in mf['images'].items():
        p = REPO / v['path']
        if not p.is_file():
            img_bad.append(k + ':MISSING')
            continue
        b = p.read_bytes()
        if len(b) != v['bytes'] or sha256_bytes(b) != v['sha256']:
            img_bad.append('%s:bytes/sha 不符' % k)
    chk('MANIFEST_IMAGES_MATCH_DISK', not img_bad,
        ';'.join(img_bad) or '%d 件(BIN/AXF/MAP/调用图)逐字节复算相同' % len(mf['images']))
    eq, note = axf_flash_equal_bin(REPO / mf['images']['axf']['path'], REPO / mf['images']['bin']['path'])
    chk('AXF_FLASH_SEGMENT_EQUAL_BIN', eq, note)
    eq_rv, note_rv = axf_flash_equal_bin(REPO / (PREV_FW + '/rt-thread-motor-m1.axf'),
                                         REPO / (PREV_FW + '/rt-thread-motor-m1-candidate.bin'))
    chk('AXF_FLASH_EQUAL_BIN_ON_REVIEWED', eq_rv, '被审提交那一版同一工具复算: ' + note_rv)
    chk('REVIEWED_IMAGE_UNCHANGED',
        sha256_bytes((REPO / (PREV_FW + '/rt-thread-motor-m1-candidate.bin')).read_bytes()) == REVIEWED_BIN_SHA,
        'e1d7f3d 那一版镜像仍是 %s..' % REVIEWED_BIN_SHA[:8])
    chk('OLDER_IMAGE_UNCHANGED',
        sha256_bytes((REPO / (R7D1_FW + '/rt-thread-motor-m1-candidate.bin')).read_bytes()) == OLDER_BIN_SHA,
        '4d50310 那一版镜像仍是 %s..' % OLDER_BIN_SHA[:8])
    ltxt = read(REPO / mf['log']['path'])
    ps = re.search(r'Program Size: Code=(\d+) RO-data=(\d+) RW-data=(\d+) ZI-data=(\d+)', ltxt)
    chk('MANIFEST_PROGRAM_SIZE_FROM_LOG', mf['program_size'] == ps.group(0), mf['program_size'])
    chk('LOG_ZERO_ERROR_ZERO_WARNING', re.search(r'- 0 Error\(s\), 0 Warning\(s\)', ltxt) is not None,
        '结果行逐字取日志')
    old_mf = json.loads(read(REPO / (PREV_FW + '/build_manifest.json')))
    ops = re.search(r'Program Size: Code=(\d+) RO-data=(\d+) RW-data=(\d+) ZI-data=(\d+)',
                    old_mf['program_size'])
    d_code, d_ro = int(ps.group(1)) - int(ops.group(1)), int(ps.group(2)) - int(ops.group(2))
    d_rw, d_zi = int(ps.group(3)) - int(ops.group(3)), int(ps.group(4)) - int(ops.group(4))
    chk('DELTA_RECOMPUTED_FROM_TWO_LOGS',
        (d_code, d_ro, d_rw, d_zi) == (16, -44, 0, 0) and
        mf['program_size_vs_reviewed_image']['delta_bytes'] ==
        {'Code': d_code, 'RO-data': d_ro, 'RW-data': d_rw, 'ZI-data': d_zi},
        'Code%+d RO%+d RW%+d ZI%+d(两份日志当场相减, 与 manifest 登记相同)' % (d_code, d_ro, d_rw, d_zi))
    bin_delta = mf['images']['bin']['bytes'] - old_mf['images']['bin']['bytes']
    chk('BIN_DELTA_RECOMPUTED', bin_delta == mf['program_size_vs_reviewed_image']['bin_delta'] == -24,
        'BIN %d -> %d = %+d' % (old_mf['images']['bin']['bytes'], mf['images']['bin']['bytes'], bin_delta))
    maptxt = read(REPO / mf['images']['map']['path'])
    old_maptxt = read(REPO / (PREV_FW + '/rt-thread-motor-m1.map'))

    def er_irom1(t):
        m = re.search(r'Execution Region ER_IROM1 \(Exec base: 0x08000000, Load base: 0x08000000, '
                      r'Size: 0x([0-9a-fA-F]+)', t)
        return int(m.group(1), 16) if m else -1
    rom_delta = d_code + d_ro + d_rw
    chk('ER_IROM1_DELTA_MATCHES_CODE_RO_RW',
        er_irom1(maptxt) >= 0 and er_irom1(old_maptxt) >= 0 and
        er_irom1(maptxt) - er_irom1(old_maptxt) == rom_delta == -28,
        '两份 map 的 ER_IROM1 %d -> %d = %+d, 与 Code+RO+RW 增量 %+d 同向且相等'
        % (er_irom1(old_maptxt), er_irom1(maptxt), er_irom1(maptxt) - er_irom1(old_maptxt), rom_delta))
    chk('ROM_VS_BIN_GAP_EXPLAINED_IN_REPORT',
        rom_delta == -28 and bin_delta == -24 and
        '全图 Program Size 增量 -28 => PASS' in read(REPO / (ROUND + '/build/resource_stack_r7d2.txt')) and
        'BIN 实际增量 -24; 残差 +0 => 判据 PASS' in read(REPO / (ROUND + '/build/resource_stack_r7d2.txt')),
        'Code+RO+RW=%+d 而 BIN=%+d, 差 %+d 由装载段构成(对齐空隙)解释, 资源报告里两条等式各自成立'
        % (rom_delta, bin_delta, bin_delta - rom_delta))
    j = mf['program_size_vs_reviewed_image']['judgement_lines_transcribed_from_resource_report']
    chk('RESOURCE_REPORT_JUDGEMENTS_ALL_TRUE', all(j.values()) and
        mf['program_size_vs_reviewed_image']['axf_flash_segment_equal_bin'] is True,
        json.dumps(j, ensure_ascii=False))
    chk('NONZERO_OBJECT_DIFF_IS_TWO', '判据 非零差分集合恰好 == ' in
        read(REPO / (ROUND + '/build/resource_stack_r7d2.txt')),
        '逐对象差分只出现在 diag_relative.o 与 project_config.o(与产品改动面一致)')
    stk = mf['stack_cost_from_call_graph']
    chk('COMMIT_STACK_COST_RETURNED',
        stk['rel_cmd_commit']['this_subtree'] < stk['rel_cmd_commit']['reviewed_subtree'] and
        stk['rel_cmd_commit']['this_frame'] < stk['rel_cmd_commit']['reviewed_frame'] and
        '无' in stk['no_subtree_grew'],
        'rel_cmd_commit 帧 %d->%d 子树 %d->%d; 子树变高的命令根=%s(本轮把上一轮为回滚新增的成本收回, '
        '没有靠加大栈或延长 deadline 绕过)' % (
            stk['rel_cmd_commit']['reviewed_frame'], stk['rel_cmd_commit']['this_frame'],
            stk['rel_cmd_commit']['reviewed_subtree'], stk['rel_cmd_commit']['this_subtree'],
            stk['no_subtree_grew'].split(':')[-1].strip()))
    sc = stk['stack_constants_unchanged']
    chk('STACK_AND_DEADLINE_CONSTANTS_UNCHANGED',
        all(v['unchanged_vs_baseline'] for v in sc.values()) and
        sc['DIAG_THREAD_STACK']['value_on_disk'] == '4096' and
        sc['FINSH_THREAD_STACK_SIZE']['value_on_disk'] == '4096' and
        sc['REL_CMD_DEADLINE_MS']['value_on_disk'] == '12000',
        json.dumps({k: [v['value_on_disk'], v['value_at_reviewed_baseline']] for k, v in sc.items()}))
    chk('ONLY_ONE_BUILD_THIS_ROUND',
        len(re.findall(r'Rebuild target', ltxt)) == 1, '日志里 Rebuild target 行 = %d' % len(re.findall(r'Rebuild target', ltxt)))
    after = re.findall(r'After Build - User command #\d+: ([^\n]*)', ltxt)
    chk('NO_DOWNLOAD_STEP_IN_LOG',
        len(after) == 1 and after[0].startswith('fromelf --bin') and
        not re.search(r'Downloading|Flash Program|Program\.hex|pyocd|JLink|UV4\.exe -f', ltxt, re.I),
        'AfterMake 只有 1 条且是 fromelf --bin: %s(日志里"flash"字样只出现在 compiling ns_flash.c 这类'
        '源文件名上, 不是下载动作)' % (after[0][:60] if after else 'MISSING'))

    # ---------- 8) 合同/映射文档与 run_record ----------
    for doc in ('implementation_contract.md', 'case_mapping.md'):
        p = REPO / (ROUND + '/' + doc)
        chk('DOC_' + doc.replace('.md', ''), p.is_file() and len(p.read_bytes()) > 800,
            '%s 存在且非空壳(%s B)' % (doc, p.stat().st_size if p.is_file() else 0))
    RR = 'reports/reviews/phase8-m8-r7f2b-safety-print-fix/run_record.md'
    rr = read(REPO / RR)
    chk('RUN_RECORD_SECTION_17', '## 17' in rr and 'ARD4b' in rr,
        '%s 里有第 17 节且点名本轮(§16 及更早的节原文未删)' % RR)
    chk('RUN_RECORD_KEPT_16', '## 16' in rr and '## 15' in rr, '上一轮与上上轮的节仍在')

    # ---------- 9) 隐私/卫生 ----------
    leak_pats = [('abs-c-forward', b'C:' + b'/Use' + b'rs/'),
                 ('abs-c-backslash', b'C:' + b'\\Use' + b'rs\\'),
                 ('user-dir-name', b'LM' + b'X')]
    synthetic = {'abs-c-forward': b'x C:/Use' + b'rs/y', 'abs-c-backslash': b'C:' + b'\\Use' + b'rs\\y',
                 'user-dir-name': b'a' + b'LM' + b'Xb'}
    miss = [n for n, pat in leak_pats if pat not in synthetic[n]]
    chk('LEAK_SCANNER_FIRED_ON_SYNTHETIC', not miss,
        ';'.join(miss) or '三条模式各自在合成样本上命中(判据不是空转)')
    leaks = []
    for p in changed + untracked:
        if not (p.startswith(ALLOWED_PREFIX) or p.startswith('reports/')):
            continue
        f = REPO / p
        if not f.is_file():
            continue
        b = f.read_bytes()
        for name, pat in leak_pats:
            if pat in b:
                leaks.append('%s:%s' % (p, name))
    chk('NO_LOCAL_PATH_LEAK', not leaks,
        ';'.join(leaks) or '本轮交付文件里无本机用户目录/用户名')

    res = {'review_id': scope['id'], 'reviewed_commit': BASE, 'zip_bytes': len(raw),
           'zip_sha256': sha256_bytes(raw), 'zip_basename': os.path.basename(zip_path),
           'head_at_check': head, 'checks': CHECKS, 'source_binding_rows': rows,
           'changed': changed, 'untracked': untracked,
           'why_literals_on_disk': why_lit, 'why_widest_bytes': why_w,
           'printf_worst_case_bytes_including_LF_commit_path': per_commit,
           'printf_width_table': {'STR_W': STR_W, '%u': 10, '%d': 11, '%x/%X': 8},
           'reverse_families': [x.replace('reverse:', '') for x in fams],
           'case_checks_delta': {'grew': grew, 'shrunk': shrunk}}
    fails = [c['id'] for c in CHECKS if not c['ok']]
    res['total'] = len(CHECKS)
    res['fail'] = len(fails)
    res['fail_ids'] = fails
    io.open(str(out_dir / 'summary.json'), 'w', encoding='utf-8', newline='\n').write(
        json.dumps(res, ensure_ascii=False, indent=1) + '\n')
    with io.open(str(out_dir / 'checks.txt'), 'w', encoding='utf-8', newline='\n') as w:
        for c in CHECKS:
            w.write('%-4s %s %s\n' % ('PASS' if c['ok'] else 'FAIL', c['id'], c['detail']))
        w.write('TOTAL=%d FAIL=%d fails=%s\n' % (len(CHECKS), len(fails), ','.join(fails) or '-'))
    print('TOTAL=%d FAIL=%d %s' % (len(CHECKS), len(fails), ','.join(fails)))
    for c in CHECKS:
        if not c['ok']:
            print('  FAIL %s %s' % (c['id'], c['detail']))
    return 1 if fails else 0


def tfour_src(txt, which):
    """从四探针前后件里取指定一侧的原文块。"""
    marker = '## 本轮(ARD4b 定稿工作树)' if which == '本轮' else '## 基线(e1d7f3d, 上一轮归档件逐字摘录)'
    parts = txt.split(marker)
    if len(parts) < 2:
        raise SystemExit('ANCHOR-MISSING in probe_four_before_after.txt: %r' % marker)
    return parts[1].split('\n## ')[0]


if __name__ == '__main__':
    sys.exit(main())
