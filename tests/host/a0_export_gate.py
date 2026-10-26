#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a0_export_gate.py - A0 采样批次"先验完整，再清理"的采集门（BOARD-A-01 §4.2）。

它把"校验成功才允许清理/继续"从一句说明变成**代码行为**：串口脚本必须按本门返回的
action_plan 决定要不要发送 `spm clear_records` 和下一批 `spm trip`。

纪律（与任务书 §4.2 逐条对应）：
  - 输入是**原始接收字节**（逐批一个文件）。NUL/CRLF/异步输出全部保留后再解析；
    绝不先删 NUL、绝不补齐缺失字段、绝不把被剪断的行拼回"看起来对"的样子。
  - 头行必须逐字等于 schema=2 合同里的 47 个列名。
  - 一条完整记录要求：47 列、逐列合法类型/取值域、schema=2、complete=1、
    slot 与代次身份自洽（单批次只追加：slot 从 0 递增、代次逐条 +1、无重复无遗漏）、
    entry 与本批一致、条数 == 本批预期。
  - 批状态（STOPPING / in_flight=0 / dropped=0）必须与记录一致。
  - **完整但 body_valid=0 的记录仍然可以存档**：body_valid=0 是判据结果，不是传输损坏；
    也绝不允许为了"拿到有效样本"而追加保护动作或重触发。
  - 任一失败 => REJECT，且 action_plan 里 clear_records=0、下一批触发=0。
  - 提示符出现 / 进程 rc=0 只证明交互结束，不证明记录完整。
  - 本文件只读文件：不连串口、不发命令、无副作用。

用法：
  python tests/host/a0_export_gate.py --raw <批原始接收文件> --batch <名字> \
      --expected-records <N> --entry <trip_fast|trip_for_operation|off_attempt> \
      [--json-out <文件>]
  python tests/host/a0_export_gate.py --selftest
  python tests/host/a0_export_gate.py --check-header-against-source <sp_measure_a0.c>

退出码：0=ACCEPT（可放行） 1=REJECT（必须停） 4=输入/环境故障（既不算检出也不算放行）
"""

import argparse
import io
import json
import re
import sys

SCHEMA = 2
SLOTS = 64

# 冻结的 47 列合同（顺序与名字即接口；与 BSP/applications/sp_measure_a0.c 的头行逐字一致，
# 由 --check-header-against-source 做双向防漂移）。
COLUMNS = [
    'schema', 'gen', 'slot', 'entry', 'ctx_isr', 'saved_primask', 'rel_ok', 'proof_null',
    'exp_epoch', 'in_owner', 'epoch_before', 'epoch_after', 'owner_after', 'phase_after',
    'inhibit', 'pending', 'pending_epoch', 'latch_m', 'latch_d', 'trips_before', 'trips_after',
    'pwm_epoch', 'pwm_attempted', 'pwm_result', 'pwm_raw_rc', 'permit_epoch', 'permit_written',
    'permit_level', 'permit_write_rc', 'outcome', 'branch', 'pwm_called', 'low_called',
    'pre', 'body_begin', 'body_end', 'post', 'counter_status', 'clock_basis', 'body_valid',
    'enclosing_valid', 'invalid_reason', 'gates', 'guard', 'guard_load', 'dropped_at_publish',
    'complete',
]
HEADER_LINE = ','.join(COLUMNS)

# 列类型直接来自生产数据行的格式串顺序（%u / %d / 0x%04x / 0x%02x / %s 枚举 / 0-1 标志）。
U32_COLS = [n for n in COLUMNS]
COL_TYPE = dict.fromkeys(U32_COLS, 'u32')
for _n in ('pwm_raw_rc', 'permit_write_rc', 'outcome'):
    COL_TYPE[_n] = 'i32'
COL_TYPE['invalid_reason'] = 'hex4'
COL_TYPE['gates'] = 'hex2'
COL_TYPE['guard'] = 'hex2'
for _n in ('rel_ok', 'proof_null', 'body_valid', 'enclosing_valid', 'complete',
           'pwm_called', 'low_called', 'permit_written'):
    COL_TYPE[_n] = 'flag'
COL_TYPE['schema'] = 'schema'
COL_TYPE['slot'] = 'slot'
COL_TYPE['gen'] = 'gen'
COL_TYPE['entry'] = 'enum_entry'
COL_TYPE['counter_status'] = 'enum_counter_status'
COL_TYPE['clock_basis'] = 'enum_clock_basis'

ENUMS = {
    'enum_entry': ('trip_fast', 'trip_for_operation', 'off_attempt'),
    'enum_counter_status': ('COUNTER_OK', 'COUNTER_UNAVAILABLE', 'COUNTER_UNCHECKED'),
    'enum_clock_basis': ('CONFIG_READBACK', 'EXTERNAL_VERIFIED', 'SOURCE_ASSUMED'),
}

U32_MAX = 4294967295
I32_MIN, I32_MAX = -21483648, 21483647

# 纳入时长统计的判据（计划 §9）：只用于分类，不参与"是否放行"的决定。
TIMING_GATES_ALL = '0x%02x' % 0x0F
TIMING_ALLOWED_INVALID_REASON = (0x0000, 0x0100)      # NONE / ENCLOSING_UNPROVEN

RE_DUMP_TAIL = re.compile(r'^\[spm\] dump: records=(\d+) dropped=(\d+)')
RE_STATUS = re.compile(r'^\[spm\] status: state=(\d+) entry=(\S*) records=(\d+) '
                       r'in_flight=(\d+) dropped=(\d+)')
RE_STOP = re.compile(r'^\[spm\] stop: state=(\d+) in_flight=(\d+) records=(\d+) dropped=(\d+)')
RE_INCOMPLETE = re.compile(r'^1,\d+,\d+,INCOMPLETE,gen=\d+')
RE_EXPORT_ERROR = re.compile(r'^\[spm\] EXPORT_ERROR: line_overflow ')
RE_DECIMAL = re.compile(r'^\d')


def split_lines(raw):
    """按真实换行切分，保留 CR 与行内 NUL 的位置信息。

    返回 [(去掉 LF 的行, 该行是否以 LF 结束, 行内 NUL 个数)]。最后一行若无换行，
    ends_with_lf=False —— 被 127 字节窗口剪断的行正是这种（它的 \\n 被剪掉了）。
    """
    out = []
    start = 0
    n = len(raw)
    while start < n:
        j = raw.find(b'\n', start)
        if j < 0:
            seg = raw[start:]
            out.append((seg, False, seg.count(b'\x00')))
            break
        seg = raw[start:j]
        out.append((seg, True, seg.count(b'\x00')))
        start = j + 1
    return out


def decode(seg):
    try:
        return seg.decode('utf-8')
    except UnicodeDecodeError:
        # 原始字节始终保留；解不出 UTF-8 只是说明有非 UTF-8 字节，用 latin-1 让文本可读。
        return seg.decode('latin-1')


def field_ok(name, val):
    """逐列合法类型/取值域，返回 (是否合法, 不合法原因)。"""
    t = COL_TYPE[name]
    if t == 'schema':
        ok = val == str(SCHEMA)
    elif t == 'slot':
        ok = bool(re.match(r'^\d+$', val)) and int(val) < SLOTS
    elif t == 'gen':
        ok = bool(re.match(r'^\d+$', val)) and int(val) >= 1
    elif t == 'flag':
        ok = val in ('0', '1')
    elif t == 'u32':
        ok = bool(re.match(r'^\d+$', val)) and int(val) <= U32_MAX
    elif t == 'i32':
        ok = bool(re.match(r'^-?\d+$', val)) and I32_MIN <= int(val) <= I32_MAX
    elif t == 'hex4':
        ok = bool(re.match(r'^0x[0-9a-f]{4}$', val))
    elif t == 'hex2':
        ok = bool(re.match(r'^0x[0-9a-f]{2}$', val))
    else:
        ok = val in ENUMS[t]
    return ok, None if ok else '%s=%r 不合法(%s)' % (name, val, t)


def evaluate(raw, expected_records, batch_entry, batch_name='batch'):
    """核心判据。raw 是本批的**原始接收字节**。返回结构化结论（含 action_plan）。"""
    f = []
    dec = [(decode(seg).rstrip('\r'), lf, nul) for seg, lf, nul in split_lines(raw)]
    dec = [(t, lf, nul) for t, lf, nul in dec if t != '']

    hdr = [i for i, (t, lf, nul) in enumerate(dec) if t == HEADER_LINE]
    if len(hdr) != 1:
        f.append('HEADER_EXACT_MATCH: 期望恰好 1 行逐字等于 47 列合同, 实际=%d' % len(hdr))
    tails = [(i, RE_DUMP_TAIL.match(t)) for i, (t, lf, nul) in enumerate(dec)]
    tails = [(i, m) for i, m in tails if m]
    if len(tails) != 1:
        f.append('DUMP_TAIL_EXACTLY_ONE: 期望恰好 1 行 "[spm] dump: records=.. dropped=..", 实际=%d'
                 % len(tails))

    block, after = [], []
    if len(hdr) == 1:
        if len(tails) == 1 and tails[0][0] > hdr[0]:
            block = [(i, t, lf, nul) for i, (t, lf, nul) in enumerate(dec)
                     if hdr[0] < i < tails[0][0]]
            after = [(i, t, lf, nul) for i, (t, lf, nul) in enumerate(dec) if i > tails[0][0]]
        else:
            block = [(i, t, lf, nul) for i, (t, lf, nul) in enumerate(dec) if i > hdr[0]]
            f.append('BLOCK_UNTERMINATED: 头行之后没有 dump 汇总行 => 无法可靠界定本批 CSV 块')

    rows = []
    for i, t, lf, nul in block:
        if RE_INCOMPLETE.match(t):
            f.append('INCOMPLETE_ROW: 第 %d 行是未完成槽的导出 => 该批不得继续' % i)
            continue
        if t.startswith('[spm]'):
            f.append('ASYNC_OR_DIAG_LINE_INSIDE_CSV: 第 %d 行不是数据行却在 CSV 块内: %r'
                     % (i, t[:60]))
            continue
        if not RE_DECIMAL.match(t):
            f.append('UNCLASSIFIED_LINE_INSIDE_CSV: 第 %d 行无法归类: %r' % (i, t[:60]))
            continue
        if nul:
            f.append('NUL_INSIDE_CSV_ROW: 第 %d 行含 %d 个 NUL(被 127 窗口剪断的特征)' % (i, nul))
        if not lf:
            f.append('ROW_NOT_TERMINATED: 第 %d 行没有结尾换行(被剪掉的 \\n), 与下一行粘连' % i)
        cols = t.split(',')
        if len(cols) != len(COLUMNS):
            f.append('COLUMN_COUNT: 第 %d 行列数=%d 期望=%d' % (i, len(cols), len(COLUMNS)))
            continue
        bad = [why for nm, v in zip(COLUMNS, cols) for ok, why in [field_ok(nm, v)] if not ok]
        if bad:
            f.append('FIELD_ILLEGAL: 第 %d 行 %s' % (i, '; '.join(bad[:4])))
            continue
        rec = dict(zip(COLUMNS, cols))
        rec['_line'] = i
        rows.append(rec)

    if len(rows) != expected_records:
        f.append('ROW_COUNT: 完整记录条数=%d 期望=%d' % (len(rows), expected_records))

    seen_slot, seen_gen = set(), set()
    for k, r in enumerate(rows):
        sl, gn = int(r['slot']), int(r['gen'])
        if sl != k:
            f.append('SLOT_ORDER: 第 %d 条 slot=%d(单批次只追加, 应=%d)' % (k, sl, k))
        if k and gn != int(rows[k - 1]['gen']) + 1:
            f.append('GEN_SEQUENCE: 第 %d 条 gen=%d 不是上一条 +1' % (k, gn))
        if sl in seen_slot:
            f.append('DUPLICATE_SLOT: slot=%d 重复' % sl)
        if gn in seen_gen:
            f.append('DUPLICATE_GEN: gen=%d 重复' % gn)
        seen_slot.add(sl)
        seen_gen.add(gn)

    for r in rows:
        if r['entry'] != batch_entry:
            f.append('ENTRY_MISMATCH: slot=%s entry=%s 本批=%s' % (r['slot'], r['entry'], batch_entry))
        if r['complete'] != '1':
            f.append('COMPLETE_NOT_1: slot=%s complete=%s' % (r['slot'], r['complete']))

    state = None
    for t, lf, nul in dec:
        m = RE_STATUS.match(t)
        if m:
            state = ('status', int(m.group(1)), int(m.group(3)), int(m.group(4)), int(m.group(5)))
            continue
        m = RE_STOP.match(t)
        if m:
            state = ('stop', int(m.group(1)), int(m.group(3)), int(m.group(2)), int(m.group(4)))
    if state is None:
        f.append('BATCH_STATE_MISSING: 原始接收里没有 status/stop 行, 批状态无法交叉核对')
    else:
        _, st_state, st_records, st_inflight, st_dropped = state
        if st_state not in (1, 2):
            f.append('BATCH_STATE: 状态行 state=%d(批收尾应为 STOPPING=2)' % st_state)
        if st_inflight != 0:
            f.append('BATCH_STATE: in_flight=%d 非 0 => 仍有在途, 记录可能不完整' % st_inflight)
        if st_dropped != 0:
            f.append('BATCH_STATE: dropped=%d 非 0 => 本批有丢弃' % st_dropped)
        if st_records != expected_records:
            f.append('BATCH_STATE: 状态行 records=%d 与本批预期=%d 不一致' % (st_records, expected_records))

    for i, m in tails:
        if int(m.group(1)) != expected_records:
            f.append('DUMP_TAIL_COUNT: dump 汇总 records=%d 期望=%d' % (int(m.group(1)), expected_records))
        if int(m.group(2)) != 0:
            f.append('DUMP_TAIL_DROPPED: dump 汇总 dropped=%d' % int(m.group(2)))

    if any(RE_EXPORT_ERROR.match(t) for t, lf, nul in dec):
        f.append('EXPORT_ERROR_SEEN: 目标自己报了导出失败(容量不足)')

    accepted = not f
    timing_in, timing_out = [], []
    for r in rows:
        ir = int(r['invalid_reason'], 16)
        include = (r['counter_status'] == 'COUNTER_OK' and r['body_valid'] == '1'
                   and r['gates'] == TIMING_GATES_ALL and ir in TIMING_ALLOWED_INVALID_REASON)
        item = {'slot': int(r['slot']), 'gen': int(r['gen']), 'entry': r['entry'],
                'counter_status': r['counter_status'], 'body_valid': int(r['body_valid']),
                'gates': r['gates'], 'invalid_reason': r['invalid_reason'],
                'complete': int(r['complete']),
                'body_begin': r['body_begin'], 'body_end': r['body_end']}
        (timing_in if include else timing_out).append(item)

    return {
        'batch': batch_name,
        'verdict': 'ACCEPT' if accepted else 'REJECT',
        'expected_records': expected_records,
        'batch_entry': batch_entry,
        'raw_bytes': len(raw),
        'received_lines': len(dec),
        'lines_without_terminator': sum(1 for t, lf, nul in dec if not lf),
        'total_nul_bytes': sum(nul for t, lf, nul in dec),
        'csv_rows_parsed': len(rows),
        'async_lines_outside_csv': sum(1 for i, t, lf, nul in after if not t.startswith('[spm] dump')),
        'failures': f,
        'action_plan': {
            'clear_records_send_count': 1 if accepted else 0,
            'next_batch_trigger_send_count': 1 if accepted else 0,
            'extra_protection_actions': 0,          # 绝不为"凑出有效样本"追加动作
        },
        'timing_stats_inclusion': {
            'included_count': len(timing_in),
            'excluded_complete_count': len(timing_out),
            'included': timing_in,
            'excluded_complete': timing_out,
        },
        'meaning': ('ACCEPT 只表示"证据保存与清理可以继续", 不表示时长合格; '
                    'body_valid=0 的完整记录仍然存档, 只是不进入时长统计。'),
    }


# ------------------------------------------------------------------ 自检（离线固件）
def fixture(expected=3, entry='trip_fast', rows=3, body_valid='1', invalid='0x0000',
            gates=TIMING_GATES_ALL, cnt='COUNTER_OK', dropped=0, state_line=True,
            corrupt=None):
    """生成一份"原始接收"字节。corrupt(fn, lines) 在行级别做定向破坏。"""
    lines = []
    if state_line:
        lines.append('[spm] stop: state=2 in_flight=0 records=%d dropped=%d '
                     '(\xe4\xb8\x8d\xe7\xad\x89\xe5\xbe\x85\xe5\x9c\xa8\xe9\x80\x94)'
                     % (rows, dropped))
    lines.append(HEADER_LINE)
    for i in range(rows):
        cols = []
        for nm in COLUMNS:
            if nm == 'schema':
                v = str(SCHEMA)
            elif nm == 'gen':
                v = str(i + 1)
            elif nm == 'slot':
                v = str(i)
            elif nm == 'entry':
                v = entry
            elif nm == 'counter_status':
                v = cnt
            elif nm == 'clock_basis':
                v = 'CONFIG_READBACK'
            elif nm == 'invalid_reason':
                v = invalid
            elif nm == 'gates':
                v = gates
            elif nm == 'guard':
                v = '0x00'
            elif nm == 'body_valid':
                v = body_valid
            elif nm == 'complete':
                v = '1'
            elif nm in ('body_begin', 'body_end'):
                v = str(2000 + i)
            elif COL_TYPE[nm] == 'flag':
                v = '0'
            else:
                v = '0'
            cols.append(v)
        lines.append(','.join(cols))
    lines.append('[spm] dump: records=%d dropped=%d (仅 body_valid=1 的样本进入时长统计)'
                 % (rows, dropped))
    lines.append('msh >')
    if corrupt:
        lines = corrupt(lines)
    return b''.join(x.encode('utf-8') + b'\r\n' for x in lines)


SELFTEST_CASES = [
    ('GATE-POS-COMPLETE', '正控: 3 条完整记录', dict(), 'ACCEPT'),
    ('GATE-POS-BODY-INVALID', '正控: 完整但 body_valid=0',
     dict(body_valid='0', invalid='0x0100'), 'ACCEPT'),
    ('GATE-NEG-TRUNC-HEADER', '负控: 头行剪成 126 可见字节 + NUL 并与数据行粘连',
     dict(corrupt=lambda L: [L[0], L[1][:126] + '\x00' + L[2]] + L[3:]), 'REJECT'),
    ('GATE-NEG-ROW-NUL-GLUED', '负控: 数据行漏 NUL 粘连下一行',
     dict(corrupt=lambda L: [L[0], L[1], L[2] + '\x00' + L[3]] + L[4:]), 'REJECT'),
    ('GATE-NEG-MISSING-COLUMN', '负控: 少一列',
     dict(corrupt=lambda L: [L[0], L[1], L[2].replace(',0x0000,0x0f,', ',0x0f,')] + L[3:]), 'REJECT'),
    ('GATE-NEG-DUP-SLOT', '负控: slot 重复',
     dict(corrupt=lambda L: [L[0], L[1], L[2], L[3].replace('2,2,1,', '2,2,0,', 1)] + L[4:]), 'REJECT'),
    ('GATE-NEG-INCOMPLETE', '负控: 块内出现 INCOMPLETE 槽导出',
     dict(corrupt=lambda L: [L[0], L[1], '1,9,9,INCOMPLETE,gen=9'] + L[2:]), 'REJECT'),
    ('GATE-NEG-ROW-ABSENT', '负控: 条数不足(预期 3 实际 2)',
     dict(expected=3, rows=2), 'REJECT'),
    ('GATE-NEG-ASYNC-IN-BLOCK', '负控: 异步日志插进 CSV 块内',
     dict(corrupt=lambda L: [L[0], L[1], L[2], '[SS] FAULT_LATCHED code=8'] + L[3:]), 'REJECT'),
    ('GATE-NEG-DROPPED', '负控: dropped 非 0',
     dict(dropped=1), 'REJECT'),
    ('GATE-NEG-SCHEMA1', '负控: schema 不是 2',
     dict(corrupt=lambda L: [L[0], L[1], L[2].replace('2,', '1,', 1)] + L[3:]), 'REJECT'),
    ('GATE-NEG-GEN-GAP', '负控: 代次跳号',
     dict(corrupt=lambda L: [L[0], L[1], L[2], L[3], L[4].replace(',3,2,', ',5,2,')] + L[5:]), 'REJECT'),
    ('GATE-NEG-INFLIGHT', '负控: in_flight 非 0',
     dict(corrupt=lambda L: [L[0].replace('in_flight=0', 'in_flight=1')] + L[1:]), 'REJECT'),
    ('GATE-NEG-BAD-ENUM', '负控: counter_status 取值非法',
     dict(cnt='COUNTER_OKAY'), 'REJECT'),
    ('GATE-NEG-EXPORT-ERROR', '负控: 目标自己报了 EXPORT_ERROR',
     dict(corrupt=lambda L: [L[0], L[1], L[2],
                             '[spm] EXPORT_ERROR: line_overflow need=490 cap=256'] + L[3:]), 'REJECT'),
    ('GATE-NEG-HEADER-DRIFT', '负控: 头行列名被改名(不逐字匹配)',
     dict(corrupt=lambda L: [L[0], L[1].replace('ctx_isr', 'ctxisr')] + L[2:]), 'REJECT'),
    ('GATE-NEG-NO-STATE-LINE', '负控: 批状态行整条缺失',
     dict(state_line=False), 'REJECT'),
    ('GATE-NEG-BAD-GATE-BITS', '负控: gates 不是 0x%02x 形状(写成十进制)' % 0x0F,
     dict(corrupt=lambda L: [L[0], L[1], L[2].replace(',0x0f,', ',15,')] + L[3:]), 'REJECT'),
]


def selftest():
    n_pass = n_fail = 0
    print('GATE-SELFTEST 逐例判定（每条 REJECT 还必须把 clear/next 计划数压到 0 才算通过）')
    for cid, kind, kw, want in SELFTEST_CASES:
        expected = kw.pop('expected', 3)
        raw = fixture(**kw)
        r = evaluate(raw, expected, 'trip_fast', batch_name=cid)
        plan = r['action_plan']
        if want == 'ACCEPT':
            ok = (r['verdict'] == 'ACCEPT' and plan['clear_records_send_count'] == 1
                  and plan['next_batch_trigger_send_count'] == 1)
        else:
            ok = (r['verdict'] == 'REJECT' and plan['clear_records_send_count'] == 0
                  and plan['next_batch_trigger_send_count'] == 0)
        n_pass += 1 if ok else 0
        n_fail += 0 if ok else 1
        print('  %-26s verdict=%-6s clear=%d next=%d %-3s  %s'
              % (cid, r['verdict'], plan['clear_records_send_count'],
                 plan['next_batch_trigger_send_count'], 'OK' if ok else 'MISMATCH', kind))
        if not ok:
            for x in r['failures'][:4]:
                print('      failure: %s' % x)

    # 语义补充判据：无效样本可存档但不进时长统计；有效样本才进。
    checks = []
    r = evaluate(fixture(rows=3, body_valid='0', invalid='0x0100'), 3, 'trip_fast',
                 batch_name='GATE-POS-INVALID-ARCHIVED')
    t = r['timing_stats_inclusion']
    checks.append(('GATE-POS-INVALID-ARCHIVED',
                   '完整但无效: 仍解析并存档 3 条 / 纳入 0 条 / 不追加保护动作',
                   r['verdict'] == 'ACCEPT' and r['csv_rows_parsed'] == 3
                   and t['included_count'] == 0 and t['excluded_complete_count'] == 3
                   and r['action_plan']['extra_protection_actions'] == 0))
    r = evaluate(fixture(rows=3), 3, 'trip_fast', batch_name='GATE-POS-TIMING-INCLUDE')
    checks.append(('GATE-POS-TIMING-INCLUDE',
                   'COUNTER_OK + body_valid=1 + gates=0x0f + reason<=0x0100 => 3 条纳入',
                   r['timing_stats_inclusion']['included_count'] == 3))
    print('  语义补充判据:')
    for cid, kind, ok in checks:
        n_pass += 1 if ok else 0
        n_fail += 0 if ok else 1
        print('  %-26s %-58s %s' % (cid, kind, 'OK' if ok else 'MISMATCH'))
    print('GATE-SELFTEST-SUMMARY pass=%d fail=%d' % (n_pass, n_fail))
    return 0 if n_fail == 0 else 1


def check_header_against_source(path):
    """把门里的 47 列合同与生产源码的头行字面量逐字比对（双向防漂移）。"""
    s = io.open(path, encoding='utf-8', newline='').read()
    i = s.find('"schema,gen')
    if i < 0:
        print('CHECK-HEADER: NOT-FOUND 在 %s 里找不到 CSV 头行字面量' % path)
        return 4
    j = s.index('complete\\n"', i) + len('complete\\n"')
    body = s[i:j]
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', body)
    joined = ''.join(parts).replace('\\n', '\n')
    expect = HEADER_LINE + '\n'
    same = joined == expect
    print('CHECK-HEADER gate=%d source=%d literal_pieces=%d identical=%s'
          % (len(expect.encode('utf-8')), len(joined.encode('utf-8')), len(parts),
             'YES' if same else 'NO'))
    if not same:
        print('  source: %r' % joined[:80])
        print('  gate  : %r' % expect[:80])
    return 0 if same else 1


def main(argv):
    ap = argparse.ArgumentParser(description='A0 采集批次先验完整再清理的采集门')
    ap.add_argument('--raw')
    ap.add_argument('--batch', default='batch')
    ap.add_argument('--expected-records', type=int)
    ap.add_argument('--entry', choices=ENUMS['enum_entry'])
    ap.add_argument('--json-out')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--check-header-against-source')
    a = ap.parse_args(argv[1:])

    if a.selftest:
        return selftest()
    if a.check_header_against_source:
        return check_header_against_source(a.check_header_against_source)
    if not (a.raw and a.expected_records is not None and a.entry):
        print('用法错误: --raw/--expected-records/--entry 都必需(或改用 --selftest)')
        return 4
    try:
        raw = io.open(a.raw, 'rb').read()
    except IOError as e:
        print('输入故障: 原始接收文件读不到 (%s)' % e)
        return 4
    r = evaluate(raw, a.expected_records, a.entry, batch_name=a.batch)
    txt = json.dumps(r, ensure_ascii=False, indent=2)
    if a.json_out:
        io.open(a.json_out, 'w', encoding='utf-8', newline='').write(txt + '\n')
    sys.stdout.write(txt + '\n')
    p = r['action_plan']
    sys.stdout.write('GATE %s verdict=%s clear_records=%d next_batch_trigger=%d\n'
                     % (r['batch'], r['verdict'], p['clear_records_send_count'],
                        p['next_batch_trigger_send_count']))
    return 0 if r['verdict'] == 'ACCEPT' else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
