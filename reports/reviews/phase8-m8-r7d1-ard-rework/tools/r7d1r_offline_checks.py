#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r7d1r_offline_checks.py - M8-R7D1 返修轮的离线独立复算(设备动作 0)

每一件都自己算一遍, 不引用 A 的结论, 也不引用上一轮工具的结论:
  1) 被审件(A 的复审包)身份: 外层 zip 的 bytes+sha256、内层清单逐条复算且不含自身;
  2) A 登记的源码绑定: 5 件输入在 4d50310 上的 blob sha1/字节/blob sha256 与磁盘字节分别核对;
  3) 本轮改动范围: 相对被审提交被改的文件必须全部落在允许清单内;
  4) 主机链与 A 探针: 66 例全过、19 族负控全 META-OK、探针修前 4 FAIL / 修后 4 PASS 且四个观察量方向正确;
  5) 本轮新增/改动的打印: 逐调用按实参最宽展开核 <=120 B(含源 LF), 无裸 NUL, UTF-8 完整;
  6) 构建侧: 清单里登记的镜像身份与磁盘逐字节复算一致, AXF 的 Flash 段与 BIN 逐字节相等,
     被审提交那一版镜像未被改写(历史保留);
  7) 交付件里不得出现本机用户目录/用户名; 扫描器先用合成样本自证"会命中"。

用法:  python r7d1r_offline_checks.py <out_dir>        (环境变量 R7D1R_ZIP 可覆盖被审包路径)
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
BASE = '4d503103cf3f1eb6f5113b10cc8683954d510ac6'
APP_REL = ('rt-thread-master/rt-thread-master/bsp/novosns/ns800/'
           'ns800rt7p65-nssinepad/applications')
ROUND = 'reports/reviews/phase8-m8-r7d1-ard-rework'
FW = 'reports/firmware/phase8-m8-r7d1-ard-rework'
ALLOWED = {
    APP_REL + '/diag_relative.c',
    APP_REL + '/diag_relative.h',
    'tests/host/rel_main.c',
    'tests/host/run_diag_relative_m8.sh',
    '.gitattributes',
}
ALLOWED_PREFIX = (ROUND + '/', FW + '/',
                  'reports/reviews/phase8-m8-r7f2b-safety-print-fix/',
                  'reports/agent_handoffs/phase8-m8-r7d1-ard-rework')
ZIP_DEFAULT = Path(os.path.expandvars(r'%USERPROFILE%')) / 'Downloads' / \
    'NS800_R7D1_A_Review_Changes_Required_4d50310_20261104.zip'
ZIP_BYTES = 19634
ZIP_SHA256 = 'd9090f964ed0242f012e40e15ed40f427a3234f72572c88e9468288fb2ce9b8d'
REVIEWED_BIN_SHA = 'df7584c3df2e682d07564ee70f05ecc7b608052a7fa739fb0c21b4f942b15c2b'
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


def main():
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / (ROUND + '/offline')
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = os.environ.get('R7D1R_ZIP') or str(ZIP_DEFAULT)

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
    chk('MANIFEST_ENTRY_COUNT', len(entries) == 18, '%d 条(3 件正文 + 12 件 proof + 3 件 reproducer)'
        % len(entries))
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
    chk('VERDICT_CHANGES_REQUIRED', scope.get('verdict') == 'CHANGES_REQUIRED', str(scope.get('verdict')))
    want_ids = ['ARD1_NULL_STATUS_LOCK_LEAK', 'ARD2_ZERO_CLEANUP_ORPHAN',
                'ARD3_REFUSED_CLEAR_MUTATES_PENDING', 'ARD4_REFUSED_COMMIT_WRITES_RAM_PROFILE']
    got_ids = [f['id'] for f in scope.get('findings', [])]
    chk('FINDINGS_IDS_EXACT', got_ids == want_ids, ','.join(got_ids))
    chk('FINDINGS_ALL_P2', all(f.get('priority') == 'P2' for f in scope.get('findings', [])),
        ','.join(f.get('priority', '?') for f in scope.get('findings', [])))
    chk('HW_CAP_ZERO', scope.get('hardware_actions_cap') == 0, str(scope.get('hardware_actions_cap')))
    chk('NO_NEW_APPROVAL_NEEDED', scope.get('new_approval_required_for_offline_fix') is False,
        str(scope.get('new_approval_required_for_offline_fix')))
    pres = scope.get('preserve', {})
    chk('PRESERVE_ON_BOARD_IDENTITY',
        pres.get('on_board_identity', '').startswith('b502 / 502dca5d'), pres.get('on_board_identity', ''))
    chk('PRESERVE_NEW_IMAGES_NOT_ON_BOARD', pres.get('new_images') == 'NOT_ON_BOARD',
        str(pres.get('new_images')))
    chk('PRESERVE_CLAIMS_FALSE', pres.get('polarity_measured') is False and
        pres.get('core_protection_verified') is False, 'polarity=%s core=%s'
        % (pres.get('polarity_measured'), pres.get('core_protection_verified')))
    chk('PRESERVE_R7F5A_PENDING', pres.get('R7F5A_physical_poweroff') == 'PENDING_OPERATOR_REPORT',
        str(pres.get('R7F5A_physical_poweroff')))

    # ---------- 2) A 登记的源码绑定(在被审提交上复算) ----------
    rc, head = sh('git', 'rev-parse', 'HEAD')
    chk('HEAD_IS_REVIEWED_COMMIT', head == BASE, 'HEAD=%s 被审=%s' % (head[:9], BASE[:9]))
    chk('IDENTITY_REVIEW_COMMIT', ident.get('review_commit') == BASE, str(ident.get('review_commit'))[:9])
    binds = ident.get('source_bindings', [])
    chk('IDENTITY_BINDINGS_COUNT', len(binds) == 5, '%d 件' % len(binds))
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
    chk('A_SOURCE_BINDINGS_RECOMPUTED', badn == 0, 'ok=%d bad=%d(在 %s 上逐件复算 blob sha1/字节/sha256)'
        % (ok, badn, BASE[:7]))
    changed_now = [r['path'] for r in rows if not r['disk_equals_blob']]
    chk('DISK_VS_BLOB_RECORDED', len(changed_now) == 4,
        '磁盘已不同于被审提交的件=%d(应为 4: 产品 2 + 主机 2; diagnosis.c 未改) => %s'
        % (len(changed_now), [os.path.basename(p) for p in changed_now]))
    chk('A_EXISTING_CASES_62', ident.get('existing_host_cases', {}).get('pass') == 62,
        str(ident.get('existing_host_cases')))
    chk('A_EXISTING_REVERSE_14', ident.get('existing_reverse_groups', {}).get('meta_ok') == 14,
        str(ident.get('existing_reverse_groups')))
    chk('A_EXTRA_CHECKS_4_FAIL_4', ident.get('additional_review_checks') ==
        {'checks': 4, 'fail': 4, 'run_rc': 1}, str(ident.get('additional_review_checks')))
    # A 自己那份探针原文(在包里)必须确实是 4 FAIL
    ap = zf.read('proof/review_probes/probe.stdout.txt').decode('utf-8', 'replace')
    n_fail_lines = len(re.findall(r'(?m)^REVIEW\|\w+\|FAIL$', ap))
    # 计数判据不能被子串吃掉: OBS|ZERO_FAILED_EXIT 里的 "FAIL" 不是判定行。
    chk('A_PROBE_ORIGINAL_4FAIL', 'failures=4' in ap and n_fail_lines == 4,
        '包内原文 REVIEW 判定行 FAIL=%d, failures 行存在=%s'
        % (n_fail_lines, 'failures=4' in ap))

    # ---------- 3) 改动范围 ----------
    rc, out = sh('git', 'diff', '--name-only', BASE, '--')
    changed = [l for l in out.splitlines() if l.strip()]
    rc, out2 = sh('git', 'ls-files', '--others', '--exclude-standard')
    untracked = [l for l in out2.splitlines() if l.strip()]
    outside = [p for p in changed + untracked
               if p not in ALLOWED and not p.startswith(ALLOWED_PREFIX)]
    chk('SCOPE_NO_STRAY_FILES', not outside,
        ';'.join(outside) or 'changed=%d untracked=%d' % (len(changed), len(untracked)))
    for must in ('diag_relative.c', 'diag_relative.h', 'rel_main.c', 'run_diag_relative_m8.sh'):
        chk('TOUCHED_' + must, any(p.endswith(must) for p in changed), must)
    chk('NOT_TOUCHED_diagnosis.c', not any(p.endswith('diagnosis.c') for p in changed),
        'A 的四项都不需要动 diagnosis.c(它是上一轮 rel_job_advance 的挂点)')
    prod_changed = [p for p in changed if p.startswith(APP_REL) and p not in ALLOWED]
    chk('PRODUCT_SCOPE_MINIMAL', not prod_changed, ';'.join(prod_changed) or 'ok')

    # ---------- 4) 主机链 / 负控 / 探针 前后 ----------
    host = REPO / ROUND / 'host'
    ntxt = read(host / 'normal_chain_out.txt')
    m = re.search(r'SUMMARY\|cases=(\d+) pass=(\d+) fail=(\d+) pre_bad=(\d+) executed=(\d+)', ntxt)
    chk('NORMAL_66_ALL_PASS', bool(m) and m.group(1) == m.group(2) == m.group(5) == '66'
        and m.group(3) == '0' == m.group(4), m.group(0) if m else 'MISSING')
    mb = re.search(r'build_rc=(\d+) warnings=(\d+)', ntxt)
    chk('HOST_BUILD_RC0_WARNINGS0', bool(mb) and mb.group(1) == '0' and mb.group(2) == '0',
        mb.group(0) if mb else 'MISSING')
    prev_ledger = read(REPO / 'reports/reviews/phase8-m8-r7d1-async-learn-offline/host/case_ledger.txt')
    prev_ids = set(re.findall(r'^(\w[\w.-]*)\s+PASS', prev_ledger, re.M))
    now_ids = set(re.findall(r'CASE\|(\S+)\|PASS', ntxt))
    lost = sorted(prev_ids - now_ids)
    chk('PRIOR_62_CASES_KEPT', len(prev_ids) == 62 and not lost,
        '上一轮台账 %d 例, 本轮丢了 %d 例%s' % (len(prev_ids), len(lost), (': ' + ','.join(lost)) if lost else ''))
    new_ids = sorted(now_ids - prev_ids)
    chk('FOUR_NEW_CASES_ADDED', new_ids == ['A13-null-status-rejected-without-lock-leak',
                                            'A14-zero-cleanup-deferred-then-recovered',
                                            'A15-refused-clear-keeps-pending-and-creds',
                                            'A16-commit-refused-keeps-config-and-candidate'],
        ','.join(new_ids))
    rtxt = read(host / 'reverse_run_full.txt')
    fam = re.search(r'执行集合: normal((?: reverse:\S+)+)', rtxt)
    fams = fam.group(1).split() if fam else []
    chk('REVERSE_FAMILY_COUNT_19', len(fams) == 19, '%d 族(原 14 + 新 5)' % len(fams))
    chk('REVERSE_NEW_FAMILIES', all(any(f.endswith(x) for f in fams) for x in
                                    ('RDV5_null_check_after_take', 'RDV6_zero_close_silent_no_arm',
                                     'RDV7_advancer_ignores_zero_flag', 'RDV8_clear_blanks_before_gate',
                                     'RDV9_commit_config_before_publish')),
        ','.join(f.replace('reverse:', '') for f in fams if 'RDV' in f))
    meta_ok = rtxt.count('META-OK')
    chk('REVERSE_META_OK_19', meta_ok == 19, 'META-OK 行数=%d' % meta_ok)
    chk('REVERSE_NO_META_FAIL', not re.search(r'META-(?!OK)\w+', rtxt), '没有 META-FAIL/其他 META 状态')
    chk('REVERSE_EACH_COMPILED_AND_RAN', rtxt.count('INJECTED ') == 19,
        'INJECTED 行=%d(编译失败或没跑到就不会有这行)' % rtxt.count('INJECTED '))
    chk('REVERSE_RAN_ON_FINAL_BYTES', 'M8 RELATIVE RESULT: PASS' in rtxt,
        '反向链尾部总结果行(与 66 例同一次运行)')
    pb = read(host / 'probe_before_after.txt')
    chk('PROBE_PRE_4FAIL', re.search(r'修前.*failures=4', pb, re.S) is not None,
        '修前 REVIEW_SUMMARY failures=4')
    chk('PROBE_POST_0FAIL', re.search(r'修后.*failures=0', pb, re.S) is not None,
        '修后 REVIEW_SUMMARY failures=0')
    for tag, key in (('NULL_STATUS', 'ctx_held'), ('ZERO_RETRY_AFTER_FAULT_CLEARED', 'owner'),
                     ('CLEAR_STATIC_REFUSED', 'pending_changed'),
                     ('COMMIT_POSTWRITE_CTX_FAULT', 'cfg_changed')):
        mm = re.search(r'%s\s+%s: 修前 (\S+) -> 修后 (\S+)' % (tag, key), pb)
        chk('PROBE_DIRECTION_' + key, bool(mm) and mm.group(1) == '1' and mm.group(2) == '0',
            mm.group(0) if mm else 'MISSING')

    # ---------- 5) 本轮新增/改动的打印 ----------
    rc, diff = sh('git', 'diff', '-U0', BASE, '--', APP_REL + '/diag_relative.c')
    added = [l[1:] for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++')]
    fmts = []
    for l in added:
        for mm in re.finditer(r'rt_kprintf\(\s*"((?:[^"\\]|\\.)*)"', l):
            fmts.append(mm.group(1))
    fmts = sorted(set(fmts))
    chk('TOUCHED_KPRINTF_FOUND', len(fmts) >= 2, '%d 个新增/改动的 rt_kprintf 字面形态: %s'
        % (len(fmts), ' | '.join(f[:30] for f in fmts)))
    U16 = ('run_adc_min', 'run_adc_max', 'run_adc_n', 'run_adc_noise', 'run_sg_min',
           'run_sg_max', 'run_sg_n', 'zero_n', 'zero_min', 'zero_max', 'threshold_tier',
           'sg_drop_warn', 'sg_drop_trip', 'tier')
    U8 = ('want', 'dir', 'v.want', 'v.dir')
    # 宽度表: %s 按**该实参可能取到的最长静态字面量**折, 不是一律按最宽。
    # why 集合本轮新增 config-write-rolled-back(24) / config-write-no-rollback(24),
    # 仍在原 25 的上界内(ADC_HEADROOM_INSUFFICIENT 25 才是最长)。
    STR_W = {'rel_job_state_name': 14, 'rel_lock_why': 13, 'who': 6,
             'v.why': 25, 'why': 25, 'rel_reason_name': 22}

    def arg_width(a):
        a = a.strip()
        for k, w in STR_W.items():
            if k in a:
                return w
        if a.startswith('"') or a.endswith('"'):
            mm = re.match(r'^"((?:[^"\\]|\\.)*)"', a)
            return len(mm.group(1)) if mm else 26
        if any(k in a for k in U16):
            return 5
        if any(('v.' + k) in a or k in a for k in U8):
            return 3
        if a.startswith('(int)') or '(rt_int' in a:
            return 11
        if a.startswith('(unsigned)') or a.startswith('(rt_uint32_t)'):
            return 10
        return 26

    def parse_kprintfs(src):
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
                    if mm.group(1) == 'u':
                        n += min(10, arg_width(a))
                    elif mm.group(1) == 'd':
                        n += min(11, arg_width(a))
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

    src_c = io.open(str(REPO / (APP_REL + '/diag_relative.c')), encoding='utf-8', newline='').read()
    calls = parse_kprintfs(src_c)
    per_call, unknown = {}, []
    for f in fmts:
        args = calls.get(f, [])
        if not args and re.search(r'%[-#0-9.]*[usdxX]', f):
            unknown.append(f[:44])
        per_call[f] = worst_len(f, args)
    chk('EVERY_TOUCHED_PRINT_HAS_ARGS', not unknown,
        ';'.join(unknown) or '%d 个格式行都取到了实参(折算不是按格式串空算)' % len(fmts))
    over = {f: n for f, n in per_call.items() if n > 120}
    chk('TOUCHED_PRINTS_WITHIN_120', not over,
        ';'.join('%s=%d' % (f[:44], n) for f, n in sorted(over.items())) or
        '最宽一次=%d B 预算=120 B(含 LF) 覆盖 %d 个调用' % (max(per_call.values()), len(per_call)))
    chk('PRINT_BOUND_NOT_VACUOUS', max(per_call.values()) >= 60,
        '最宽一次只有 %d B => 折算表大概没生效' % max(per_call.values()))
    nul = [f for f in fmts if '\\0' in f or '\x00' in f]
    chk('NO_BARE_NUL_IN_NEW_FORMATS', not nul, ';'.join(nul) or '%d 条格式串里没有裸 NUL' % len(fmts))
    bad_utf8 = []
    for f in fmts:
        lit = f.encode('utf-8', 'surrogateescape')
        try:
            lit.decode('utf-8', 'strict')
        except Exception:
            bad_utf8.append(f[:44])
    chk('NEW_FORMATS_STRICT_UTF8', not bad_utf8, ';'.join(bad_utf8) or '逐条严格 UTF-8 解码通过')
    # 上一轮已证的主行不得被本轮改宽: learn_status 主行仍应是原样
    NEEDLE = 'job id=%u st=%s why=%s owner=%u cred=%d/%d'
    has_main = any(NEEDLE in k for k in calls)
    chk('LEARN_STATUS_MAIN_LINE_UNWIDENED', has_main,
        'learn_status 主行格式串保持不变(cleanup 走单独一条短行); 实参取到=%d 个'
        % (len(next((v for k, v in calls.items() if NEEDLE in k), []))))

    # ---------- 6) 构建侧 ----------
    mf = json.loads(read(REPO / (FW + '/build_manifest.json')))
    chk('MANIFEST_ROUND', mf.get('round') == 'phase8-m8-r7d1-ard-rework', str(mf.get('round')))
    chk('MANIFEST_BASELINE_IS_REVIEWED', mf.get('baseline_commit') == BASE and
        mf.get('baseline_is_head_at_build') is True,
        'baseline=%s head_is_baseline=%s' % (str(mf.get('baseline_commit'))[:7],
                                             mf.get('baseline_is_head_at_build')))
    chk('MANIFEST_REVIEWED_ZIP_MATCHES_PACKAGE',
        mf['review_source']['zip_bytes'] == len(raw) and mf['review_source']['zip_sha256'] == sha256_bytes(raw),
        'manifest 登记的被审包身份 == 本次实算')
    img_bad = []
    for k, v in mf['images'].items():
        p = REPO / v['path']
        if not p.is_file():
            img_bad.append(k + ':MISSING')
            continue
        b = p.read_bytes()
        if len(b) != v['bytes'] or sha256_bytes(b) != v['sha256']:
            img_bad.append('%s:bytes/sha 不符' % k)
    chk('MANIFEST_IMAGES_MATCH_DISK', not img_bad, ';'.join(img_bad) or
        '%d 件(BIN/AXF/MAP/调用图)逐字节复算相同' % len(mf['images']))
    eq, note = axf_flash_equal_bin(REPO / mf['images']['axf']['path'],
                                   REPO / mf['images']['bin']['path'])
    chk('AXF_FLASH_SEGMENT_EQUAL_BIN', eq, note)
    eq_old, note_old = axf_flash_equal_bin(REPO / 'reports/firmware/phase8-m8-r7d1-async-learn-offline/rt-thread-motor-m1.axf',
                                           REPO / 'reports/firmware/phase8-m8-r7d1-async-learn-offline/rt-thread-motor-m1-candidate.bin')
    chk('AXF_FLASH_EQUAL_BIN_ON_REVIEWED', eq_old, '被审件同一工具复算: ' + note_old)
    rb = REPO / 'reports/firmware/phase8-m8-r7d1-async-learn-offline/rt-thread-motor-m1-candidate.bin'
    chk('REVIEWED_IMAGE_UNCHANGED', rb.is_file() and sha256_bytes(rb.read_bytes()) == REVIEWED_BIN_SHA,
        '被审提交那一版镜像仍是 %s..' % REVIEWED_BIN_SHA[:8])
    chk('LOG_LINE_COUNT_MATCH', mf['log']['bytes'] == (REPO / mf['log']['path']).stat().st_size,
        '日志字节数与磁盘一致(%d), CR=%d LF=%d' % (mf['log']['bytes'], mf['log']['disk_CR'],
                                                  mf['log']['disk_LF']))

    # ---------- 7) 隐私/卫生 ----------
    # 模式在运行时拼出来: 写成语面量会被这道扫描自己命中(上一轮就为此改过工具)。
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
    chk('NO_LOCAL_PATH_LEAK', not leaks, ';'.join(leaks) or '本轮交付文件里无本机用户目录/用户名')

    res = {'review_id': scope['id'], 'reviewed_commit': BASE, 'zip_bytes': len(raw),
           'zip_sha256': sha256_bytes(raw), 'zip_basename': os.path.basename(zip_path),
           'head_at_check': head, 'checks': CHECKS, 'source_binding_rows': rows,
           'changed': changed, 'untracked': untracked,
           'printf_worst_case_bytes_including_LF': per_call,
           'printf_width_table': {'STR_W': STR_W, '%u': 10, '%d': 11, '%x/%X': 8},
           'new_case_ids': new_ids, 'reverse_families': [f.replace('reverse:', '') for f in fams]}
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


if __name__ == '__main__':
    sys.exit(main())
