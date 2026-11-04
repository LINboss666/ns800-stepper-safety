# -*- coding: utf-8 -*-
"""r7d1r_make_host_artifacts.py - 把主机链原文与 A 探针的前后结果整成可审工件(设备动作 0)

只读输入(都在仓外, 由 run_diag_relative_m8.sh / A 的 run_review_probes.py 当场产出):
  --normal 正常链日志  --reverse 反向链日志  --pre 修前探针输出目录  --post 修后探针输出目录
写到 --out 指定目录(仓内交付目录):
  normal_chain_out.txt / case_ledger.txt / normal_chain_build.txt / normal_chain_warning_count.txt
  reverse_run_full.txt / reverse_meta_lines.txt / probe_before_after.txt
判据本身不在这里算(由 r7d1r_offline_checks.py 独立复算), 本脚本只做**原文搬运 + 台账重排**。
"""
import argparse
import io
import json
import os
import re
import sys
from pathlib import Path


def read(p):
    return io.open(str(p), encoding='utf-8', errors='replace').read()


def write(p, txt):
    os.makedirs(os.path.dirname(str(p)), exist_ok=True)
    io.open(str(p), 'w', encoding='utf-8', newline='\n').write(txt)


def ledger(txt):
    lines = [l for l in txt.splitlines() if l.startswith('CASE|')]
    summ = [l for l in txt.splitlines() if l.startswith('SUMMARY|')]
    w = max(len(l.split('|')[1]) for l in lines)
    out = ['# 返修轮主机链逐例台账(取自 host/normal_chain_out.txt 的 CASE 行, 未重排未省略)',
           '# 汇总行: %s' % (summ[-1] if summ else 'MISSING')]
    for l in lines:
        _, cid, st, ck = l.split('|')
        out.append('%-*s %s %s' % (w, cid, st, ck.replace('checks=', 'checks=')))
    return '\n'.join(out) + '\n', len(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--normal', required=True)
    ap.add_argument('--reverse', required=True)
    ap.add_argument('--pre', required=True)
    ap.add_argument('--post', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = Path(a.out)
    ntxt, rtxt = read(a.normal), read(a.reverse)
    write(out / 'normal_chain_out.txt', ntxt)
    lg, ncase = ledger(ntxt)
    write(out / 'case_ledger.txt', lg)
    hdr = [l for l in ntxt.splitlines()
           if re.match(r'^(compiler=|src_app=|out_dir=|selected=|build_rc=|proc_rc=)', l)]
    write(out / 'normal_chain_build.txt', '\n'.join(hdr) + '\n')
    m = re.search(r'build_rc=(\d+) warnings=(\d+)', ntxt)
    write(out / 'normal_chain_warning_count.txt', '%s\n' % (m.group(2) if m else 'MISSING'))
    write(out / 'reverse_run_full.txt', rtxt)
    meta = [l.strip() for l in rtxt.splitlines() if '|' in l and ('META-OK' in l or 'META-' in l)]
    write(out / 'reverse_meta_lines.txt',
          '# 反向家族逐条判定原文(从 reverse_run_full.txt 的汇总块摘出, 未改写)\n'
          + '\n'.join(meta) + '\n')

    def probe(d):
        p = Path(d)
        res = json.loads(read(p / 'result.json'))
        txt = read(p / 'probe.stdout.txt')
        return res, txt
    rpre, tpre = probe(a.pre)
    rpost, tpost = probe(a.post)
    su = lambda t: [l for l in t.splitlines() if l.startswith('REVIEW_SUMMARY|')][0]
    lines = ['# A 复审探针(review_probes.c, 直接编入产品 C)的前后结果',
             '# 修前 = 在 4d50310 的逐字节副本上跑(仓外 root, 五件输入 sha256 与 A 的 IDENTITY.json 相同)',
             '# 修后 = 在本轮定稿工作树上跑; 两边同一份 A 的原版脚本与同一台机器同一 gcc。',
             '# 编译器: %s' % rpre.get('argv', ['?'])[0],
             '',
             '## 修前(被审件 4d50310) run_rc=%s' % rpre.get('run_rc'),
             tpre.rstrip('\n'),
             '',
             '## 修后(本轮返修候选) run_rc=%s' % rpost.get('run_rc'),
             tpost.rstrip('\n'),
             '',
             '## 对照小结']
    keys = [('NULL_STATUS', 'ctx_held'), ('ZERO_RETRY_AFTER_FAULT_CLEARED', 'owner'),
            ('CLEAR_STATIC_REFUSED', 'pending_changed'), ('COMMIT_POSTWRITE_CTX_FAULT', 'cfg_changed')]
    for tag, k in keys:
        def grab(t):
            for l in t.splitlines():
                if l.startswith('OBS|') and tag in l:
                    mm = re.search(r'%s=(\S+)' % k, l)
                    return mm.group(1) if mm else '?'
            return 'MISSING'
        lines.append('  %-22s %s: 修前 %s -> 修后 %s' % (tag, k, grab(tpre), grab(tpost)))
    lines.append('  REVIEW_SUMMARY 修前 %s -> 修后 %s' % (su(tpre), su(tpost)))
    write(out / 'probe_before_after.txt', '\n'.join(lines) + '\n')
    print('WROTE|%s|cases=%d|reverse_meta=%d' % (out, ncase, len(meta)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
