# -*- coding: utf-8 -*-
"""r7d2_make_host_artifacts.py - 把本轮主机链原文与 A 两组探针的结果整成可审工件(设备动作 0)

只读输入(都在仓外或已归档的上一轮交付里, 由 run_diag_relative_m8.sh / A 的原版脚本当场产出):
  --normal 正常链日志  --reverse 反向链日志  --four 本轮四探针目录
  --compound-pre 复合故障探针在 e1d7f3d 上的结果目录  --compound-post 本轮结果目录
  --prev-probe-artifact 上一轮归档的 probe_before_after.txt(它的"修后"块就是 e1d7f3d 的四探针原文)
写到 --out(仓内交付目录):
  normal_chain_out.txt / case_ledger.txt / normal_chain_build.txt / normal_chain_warning_count.txt
  reverse_run_full.txt / reverse_meta_lines.txt
  probe_four_before_after.txt / probe_compound_before_after.txt
判据本身不在这里算(由 r7d2_offline_checks.py 独立复算), 本脚本只做原文搬运 + 台账重排。
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
    d = os.path.dirname(str(p))
    if d:
        os.makedirs(d, exist_ok=True)
    io.open(str(p), 'w', encoding='utf-8', newline='\n').write(txt)


def ledger(txt, header):
    lines = [l for l in txt.splitlines() if l.startswith('CASE|')]
    summ = [l for l in txt.splitlines() if l.startswith('SUMMARY|')]
    if not lines:
        raise SystemExit('NO-CASE-LINES: 正常链日志里没有 CASE| 行, 输入不对')
    w = max(len(l.split('|')[1]) for l in lines)
    out = [header, '# 汇总行: %s' % (summ[-1] if summ else 'MISSING')]
    for l in lines:
        _, cid, st, ck = l.split('|')
        out.append('%-*s %s %s' % (w, cid, st, ck))
    return '\n'.join(out) + '\n', len(lines)


def block(txt, marker):
    """从上一轮归档件里取 '## <marker>' 到下一个 '## ' 之间的原文(逐字, 不重排)。"""
    parts = txt.split('\n## ')
    for p in parts[1:]:
        if p.startswith(marker):
            return p.rstrip('\n')
    raise SystemExit('ANCHOR-MISSING in prev artifact: %r' % marker)


def obs(t, tag, key):
    for l in t.splitlines():
        if l.startswith('OBS|') and tag in l:
            m = re.search(r'%s=(\S+)' % key, l)
            if m:
                return m.group(1)
            return 'KEY-MISSING'
    return 'OBS-MISSING'


def review(t, tag):
    for l in t.splitlines():
        if l.startswith('REVIEW|') and tag in l:
            return l.split('|')[-1]
    return 'REVIEW-MISSING'


def diag_commit_lines(t):
    return [l.strip() for l in t.splitlines() if '[DIAG] commit' in l]


def load_probe(d):
    p = Path(d)
    return json.loads(read(p / 'result.json')), read(p / 'probe.stdout.txt')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--normal', required=True)
    ap.add_argument('--reverse', required=True)
    ap.add_argument('--four', required=True)
    ap.add_argument('--compound-pre', required=True)
    ap.add_argument('--compound-post', required=True)
    ap.add_argument('--prev-probe-artifact', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = Path(a.out)
    ntxt, rtxt = read(a.normal), read(a.reverse)

    write(out / 'normal_chain_out.txt', ntxt)
    lg, ncase = ledger(ntxt, '# 本轮主机链逐例台账(取自 host/normal_chain_out.txt 的 CASE 行, 未重排未省略)')
    write(out / 'case_ledger.txt', lg)
    hdr = [l for l in ntxt.splitlines()
           if re.match(r'^(compiler=|src_app=|out_dir=|selected=|build_rc=|proc_rc=)', l)]
    write(out / 'normal_chain_build.txt', '\n'.join(hdr) + '\n')
    m = re.search(r'build_rc=(\d+) warnings=(\d+)', ntxt)
    write(out / 'normal_chain_warning_count.txt', '%s\n' % (m.group(2) if m else 'MISSING'))
    write(out / 'reverse_run_full.txt', rtxt)
    # 只取判定行(形如 "  <family> | META-OK ..."), 不按子串粗匹配: 正文里也出现 "META-OK" 这个词。
    meta = [l.strip() for l in rtxt.splitlines() if re.search(r'\|\s*META-OK|\|\s*META-FAIL', l)]
    write(out / 'reverse_meta_lines.txt',
          '# 反向家族逐条判定原文(从 reverse_run_full.txt 的汇总块摘出, 未改写)\n'
          + '\n'.join(meta) + '\n')

    prev = read(a.prev_probe_artifact)
    rfour, tfour = load_probe(a.four)
    tprev_four = block(prev, '修后(本轮返修候选)')
    fam = ['# A 原版四探针(review_probes.c, 直接编入产品 C)——本轮在定稿工作树上复跑',
           '# 对照基线 = 上一轮归档件 host/probe_before_after.txt 的"修后"块, 那一次跑的正是被审件 e1d7f3d;',
           '# 两边同一份 A 的原版 reproducer(C:\\r7d1_rev\\reproducer\\review_probes.c)、同一台机器、同一 gcc,',
           '# 只有产品三件与工作树夹具不同。编译器: %s' % rfour.get('argv', ['?'])[0],
           '# 本轮 run_rc=%s build_rc=%s hardware_actions=%s'
           % (rfour.get('run_rc'), rfour.get('build_rc'), rfour.get('hardware_actions')),
           '',
           '## 基线(e1d7f3d, 上一轮归档件逐字摘录)',
           tprev_four,
           '',
           '## 本轮(ARD4b 定稿工作树)',
           tfour.rstrip('\n'),
           '',
           '## 逐条对照(只列会变的观察值与拒绝身份)']
    keys = [('NULL_STATUS', 'ctx_held'), ('NULL_STATUS', 'takes'), ('NULL_STATUS', 'releases'),
            ('ZERO_FAILED_EXIT', 'ctx_takes'), ('ZERO_RETRY_AFTER_FAULT_CLEARED', 'owner'),
            ('ZERO_RETRY_AFTER_FAULT_CLEARED', 'busy'),
            ('CLEAR_STATIC_REFUSED', 'pending_changed'), ('CLEAR_STATIC_REFUSED', 'zero_cred'),
            ('CLEAR_STATIC_REFUSED', 'learn_cred'),
            ('COMMIT_POSTWRITE_CTX_FAULT', 'cfg_changed'),
            ('COMMIT_POSTWRITE_CTX_FAULT', 'ctx_takes'),
            ('COMMIT_POSTWRITE_CTX_FAULT', 'refused')]
    for tag, k in keys:
        fam.append('  %-27s %-16s e1d7f3d %s -> 本轮 %s' % (tag, k, obs(tprev_four, tag, k), obs(tfour, tag, k)))
    fam.append('  四条 REVIEW: e1d7f3d %s -> 本轮 %s' % (
        '/'.join(review(tprev_four, t) for t, _ in
                 [('NULL_STATUS_DOES_NOT_LEAK_LOCK', 1),
                  ('FAILED_ZERO_RECOVERS_COLLECTOR_AFTER_LOCK_FAULT', 1),
                  ('REFUSED_CLEAR_PRESERVES_PENDING_AND_CREDENTIALS', 1),
                  ('COMMIT_OUTCOME_MATCHES_COMPLETE_TRANSACTION', 1)]),
        '/'.join(review(tfour, t) for t in
                 ['NULL_STATUS_DOES_NOT_LEAK_LOCK',
                  'FAILED_ZERO_RECOVERS_COLLECTOR_AFTER_LOCK_FAULT',
                  'REFUSED_CLEAR_PRESERVES_PENDING_AND_CREDENTIALS',
                  'COMMIT_OUTCOME_MATCHES_COMPLETE_TRANSACTION'])))
    fam.append('  REVIEW_SUMMARY: %s -> %s' % (
        [l for l in tprev_four.splitlines() if l.startswith('REVIEW_SUMMARY|')][0],
        [l for l in tfour.splitlines() if l.startswith('REVIEW_SUMMARY|')][0]))
    fam.append('')
    fam.append('## 第 4 条(commit 事务)的拒绝身份变化 —— 这是本轮设计变化的直接后果, 不是锚点失效')
    fam.append('  e1d7f3d 的 [DIAG] commit 行: %s' % ' | '.join(diag_commit_lines(tprev_four)))
    fam.append('  本轮的     [DIAG] commit 行: %s' % ' | '.join(diag_commit_lines(tfour)))
    fam.append('  同一注入(ctx 第 4 次取锁失败)现在命中的是"配置已持锁、发布前取 ctx 失败"这个等价故障点;')
    fam.append('  它仍然产生 refused=1 且 cfg_changed=0(不是绕过), 拒绝身份由 ctx-lock-publish/-12 变成 ctx-lock-commit/-117。')
    write(out / 'probe_four_before_after.txt', '\n'.join(fam) + '\n')

    rcpre, tcpre = load_probe(a.compound_pre)
    rcpost, tcpost = load_probe(a.compound_post)
    cmp_ = ['# A 续修包的复合故障探针(commit_rollback_probe.c)——修前必须 FAIL, 修后核验不变量',
            '# 输入同一份: C:\\r7d1r2_rev\\reproducer\\commit_rollback_probe.c(本轮未改), 同一 gcc, 同一台机器。',
            '# 修前 = 在被审件 e1d7f3d 的逐字节副本上跑; 修后 = 在本轮定稿工作树上跑。',
            '# 编译器: %s' % rcpre.get('argv', ['?'])[0],
            '',
            '## 修前(e1d7f3d) run_rc=%s build_rc=%s hardware_actions=%s'
            % (rcpre.get('run_rc'), rcpre.get('build_rc'), rcpre.get('hardware_actions')),
            tcpre.rstrip('\n'),
            '',
            '## 修后(ARD4b 定稿工作树) run_rc=%s build_rc=%s hardware_actions=%s'
            % (rcpost.get('run_rc'), rcpost.get('build_rc'), rcpost.get('hardware_actions')),
            tcpost.rstrip('\n'),
            '',
            '## 逐项对照(OBS|COMMIT_CFG_AND_ROLLBACK_FAULT)']
    for k in ['refused', 'no_rollback', 'cfg_changed', 'pending_changed', 'creds_kept',
              'cfg_id', 'pending_id', 'ctx_takes', 'cfg_steps']:
        cmp_.append('  %-16s 修前 %s -> 修后 %s' % (k, obs(tcpre, 'COMMIT_CFG_AND_ROLLBACK_FAULT', k),
                                                obs(tcpost, 'COMMIT_CFG_AND_ROLLBACK_FAULT', k)))
    cmp_.append('  REVIEW 修前 %s -> 修后 %s' % (
        review(tcpre, 'REFUSED_COMMIT_PRESERVES_ALL_STATE_ON_ROLLBACK_LOCK_FAILURE'),
        review(tcpost, 'REFUSED_COMMIT_PRESERVES_ALL_STATE_ON_ROLLBACK_LOCK_FAILURE')))
    cmp_.append('  拒绝身份: 修前 %s' % ' | '.join(diag_commit_lines(tcpre)))
    cmp_.append('            修后 %s' % ' | '.join(diag_commit_lines(tcpost)))
    cmp_.append('')
    cmp_.append('## 如实登记: 复合探针的 ctx 那一臂在本轮打不到(不是"变好了"的证据)')
    cmp_.append('  探针把两个故障同时装上去: cfg 第 (cfg_steps-1) 次之后失败 + ctx 第 5 次失败。')
    cmp_.append('  修前 commit 会取 ctx 5 次(第 5 次就是回滚), 两个臂都命中 => no_rollback=1。')
    cmp_.append('  修后 commit 只取 ctx %s 次且 cfg 先失败, 回滚这一步在设计上已经不存在,' % obs(tcpost, 'COMMIT_CFG_AND_ROLLBACK_FAULT', 'ctx_takes'))
    cmp_.append('  所以 ctx 臂不再可能命中 => 本件的 PASS 只证明"cfg 失败时三份状态未动",')
    cmp_.append('  不证明"配置写成而 ctx 发布失败"这一组合(它已不可能发生)。等价故障点由两处承担:')
    cmp_.append('    1) 原版四探针第 4 条(ctx 第 4 次失败, 此时 cfg 已持锁): 见 host/probe_four_before_after.txt;')
    cmp_.append('    2) 主机链 A16 的 ②/⑤ 段(cfg 已持锁 + 发布取 ctx 失败 => REFUSED, 配置/候选/凭据/锁全回到调用前)。')
    write(out / 'probe_compound_before_after.txt', '\n'.join(cmp_) + '\n')
    print('WROTE|%s|cases=%d|reverse_meta=%d|four_rc=%s|compound_post_rc=%s'
          % (out, ncase, len(meta), rfour.get('run_rc'), rcpost.get('run_rc')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
