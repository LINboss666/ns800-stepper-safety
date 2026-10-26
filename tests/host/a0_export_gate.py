#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a0_export_gate.py - A0 采样批次"先验完整，再清理"的采集门（BOARD-A-01 §4.2 / GATE-R2）。

它把"校验成功才允许清理/继续"从一句说明变成**代码行为**：串口脚本必须按本门返回的
action_plan 决定要不要发送 `spm clear_records` 和下一批 `spm trip`。

纪律（任务书 §4.2 + GATE-R2 §4 逐条对应）：
  - 输入是**原始接收字节**（逐批一个文件）。NUL/CRLF/异步输出全部保留后再解析；
    绝不先删 NUL、绝不补齐缺失字段、绝不把被剪断的行拼回"看起来对"的样子。
  - 头行必须逐字等于 schema=2 合同里的 47 个列名。
  - 参与放行的**协议行**（头行/数据行/stop|status 状态行/dump 汇总行）逐条核：行内无 NUL、
    以合法 LF/CRLF 收尾、整行完整匹配协议形状、数字令牌后必须是真分隔符 ——
    `dropped=0BAD`/`records=3BAD` 这类**前缀伪匹配必须拒绝**（不做宽松前缀判定）。
  - 帧次序与边界固定：头行 → 数据行 → 汇总行。块外的数值记录/INCOMPLETE/重复头行/重复汇总/
    汇总之后的状态行都属帧污染，**不得**归成普通异步日志忽略。
  - 本批收尾依据只取 **dump 之前最后一条可靠状态行**（合法过渡：stop 时仍在途、稍后 status 在途=0）。
    普通采样批必须 state=STOPPING(2) 且 in_flight=0 且 dropped=0 且 records==预期；
    ACTIVE(1) 不授权清理；不把不同响应拼接成"凑出来的成功"。
  - 一条完整记录要求：47 列、逐列合法类型/取值域（正确的 int32 与 uint32 边界）、schema=2、
    complete=1、slot 与代次身份自洽、entry 与本批一致、条数 == 本批预期。
  - **内容矛盾即拒**：声称 body_valid=1 却 body_end==body_begin（无进展）或 body_end<body_begin
    （回绕）⇒ REJECT，不进有效时间池，也不改写输入字段替它消除矛盾。
  - 完整、合法且 body_valid=0 的记录仍然 ACCEPT 存档，只从时长统计排除，绝不追加保护动作。
  - 时长统计只对 **ACCEPT** 的批可信；REJECT 批的解析片段一律 trusted=False（只留观测值）。
  - 任一失败或解析异常 => REJECT，且 action_plan 里 clear_records=0、下一批触发=0。
  - 提示符出现 / 进程 rc=0 只证明交互结束，不证明记录完整。调用方必须**同时**核对
    进程退出码、JSON verdict 与 action_plan 三者，不得用部分输出或旧 JSON 放行。
  - 本文件只读文件：不连串口、不发命令、无副作用。它算的是"放行计划"，
    真实串口发送由外部脚本执行，本门不声称现场已发送或已清理。

用法：
  python tests/host/a0_export_gate.py --raw <批原始接收文件> --batch <名字> \
      --expected-records <N> --entry <trip_fast|trip_for_operation|off_attempt> [--json-out <文件>]
  python tests/host/a0_export_gate.py --selftest
  python tests/host/a0_export_gate.py --selftest-meta
  python tests/host/a0_export_gate.py --check-header-against-source <sp_measure_a0.c>

退出码：0=ACCEPT（可放行） 1=REJECT（必须停） 4=输入/环境故障（既不算检出也不算放行）
"""

import argparse
import io
import json
import re
import sys

GATE_REVISION = 'r2'

SCHEMA = 2
SLOTS = 64
U32_MAX = (1 << 32) - 1
I32_MIN = -(1 << 31)
I32_MAX = (1 << 31) - 1

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
# spm_rec_t 的整型字段全部是 32 位（rt_uint8_t 字段以 %u 打印，取值域也按 u32 收）：
# 因此每个整型列都有明确上界，没有"只查下界"的漏项。
COL_TYPE = dict.fromkeys(COLUMNS, 'u32')
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
COL_TYPE['gen'] = 'gen_u32'
COL_TYPE['entry'] = 'enum_entry'
COL_TYPE['counter_status'] = 'enum_counter_status'
COL_TYPE['clock_basis'] = 'enum_clock_basis'

ENUMS = {
    'enum_entry': ('trip_fast', 'trip_for_operation', 'off_attempt'),
    'enum_counter_status': ('COUNTER_OK', 'COUNTER_UNAVAILABLE', 'COUNTER_UNCHECKED'),
    'enum_clock_basis': ('CONFIG_READBACK', 'EXTERNAL_VERIFIED', 'SOURCE_ASSUMED'),
}

# 纳入时长统计的判据（计划 §9）：只用于分类，不参与"是否放行"。
TIMING_GATES_ALL = '0x%02x' % 0x0F
TIMING_ALLOWED_INVALID_REASON = (0x0000, 0x0100)      # NONE / ENCLOSING_UNPROVEN

# 协议行一律**整行锚定**（尾部只允许可选的说明性括注），且每个数字后必须紧跟真分隔符：
# (?![0-9A-Za-z_]) 让 dropped=0BAD / records=3BAD 这类前缀伪匹配无法成立。
SEP = r'(?![0-9A-Za-z_])'
RE_STATUS = re.compile(
    r'^\[spm\] status: state=(\d+)' + SEP + r' entry=(\S*) records=(\d+)' + SEP +
    r' in_flight=(\d+)' + SEP + r' dropped=(\d+)' + SEP +
    r' counter=(\S*) clock=(\S*) guard=(\d+)' + SEP + r' gates_now=0x[0-9a-f]+$')
RE_STOP = re.compile(
    r'^\[spm\] stop: state=(\d+)' + SEP + r' in_flight=(\d+)' + SEP +
    r' records=(\d+)' + SEP + r' dropped=(\d+)' + SEP + r'( \(.*\))?$')
RE_DUMP_TAIL = re.compile(
    r'^\[spm\] dump: records=(\d+)' + SEP + r' dropped=(\d+)' + SEP + r'( .*)?$')
RE_DUMP_BUSY = re.compile(r'^\[spm\] dump: BUSY \(state=\d+' + SEP + r' in_flight=\d+' + SEP +
                          r'\)$')
RE_EXPORT_ERROR = re.compile(r'^\[spm\] EXPORT_ERROR: line_overflow need=(-?\d+)' + SEP +
                             r' cap=(\d+)' + SEP + r'$')
RE_INCOMPLETE = re.compile(r'^1,\d+,\d+,INCOMPLETE,gen=\d+$')
RE_NUMERIC_ROW = re.compile(r'^\d+,')
# 出现协议名前缀但整行不匹配 => 协议行被污染/剪断，绝不静默忽略。
RE_PROTO_PREFIX = (re.compile(r'^\[spm\] status: '), re.compile(r'^\[spm\] stop: '),
                   re.compile(r'^\[spm\] dump: '),
                   re.compile('^' + re.escape(HEADER_LINE[:24])),
                   re.compile(r'^\d+,\d+,\d+,'))

STATE_IDLE, STATE_ACTIVE, STATE_STOPPING = 0, 1, 2


def split_lines(raw):
    """按真实换行切分，保留 CR 与行内 NUL 的位置信息。

    返回 [(去掉 LF 的行, 该行是否以 LF 结束, 行内 NUL 个数)]。最后一行若无换行，
    lf=False —— 被 127 字节窗口剪断的行正是这种（它的 \n 被剪掉了）。
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
        return seg.decode('latin-1')


def field_ok(name, val):
    """逐列合法类型/取值域，返回 (是否合法, 不合法原因)。"""
    t = COL_TYPE[name]
    if t == 'schema':
        ok = val == str(SCHEMA)
    elif t == 'slot':
        ok = bool(re.match(r'^\d+$', val)) and int(val) < SLOTS
    elif t == 'gen_u32':
        ok = bool(re.match(r'^\d+$', val)) and 1 <= int(val) <= U32_MAX
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


def classify(text):
    """把"去掉 LF 的行"归到协议角色；协议名前缀但整行不匹配 => PROTOCOL_BROKEN。"""
    if text == HEADER_LINE:
        return 'HEADER'
    if RE_STATUS.match(text):
        return 'STATE'
    if RE_STOP.match(text):
        return 'STATE'
    if RE_DUMP_TAIL.match(text) or RE_DUMP_BUSY.match(text):
        return 'DUMP_TAIL'
    if RE_EXPORT_ERROR.match(text):
        return 'EXPORT_ERROR'
    if RE_INCOMPLETE.match(text):
        return 'INCOMPLETE'
    if RE_NUMERIC_ROW.match(text):
        return 'ROW'
    for p in RE_PROTO_PREFIX:
        if p.match(text):
            return 'PROTOCOL_BROKEN'
    return 'OTHER'


def evaluate(raw, expected_records, batch_entry, batch_name='batch'):
    """核心判据。raw 是本批的**原始接收字节**。返回结构化结论（含放行计划）。"""
    f = []
    dec = []
    for seg, lf, nul in split_lines(raw):
        t = decode(seg).rstrip('\r')
        if t == '':
            continue
        dec.append((t, lf, nul, classify(t)))

    idx = {}
    for role in ('HEADER', 'DUMP_TAIL', 'EXPORT_ERROR', 'PROTOCOL_BROKEN'):
        idx[role] = [i for i, (t, lf, nul, c) in enumerate(dec) if c == role]

    if len(idx['HEADER']) != 1:
        f.append('HEADER_EXACT_MATCH: 期望恰好 1 行逐字等于 47 列合同, 实际=%d'
                 % len(idx['HEADER']))
    if len(idx['DUMP_TAIL']) != 1:
        f.append('DUMP_TAIL_EXACTLY_ONE: 期望恰好 1 行 dump 汇总, 实际=%d' % len(idx['DUMP_TAIL']))
    for i in idx['PROTOCOL_BROKEN'][:4]:
        f.append('PROTOCOL_LINE_MALFORMED: 第 %d 行有协议名前缀但整行不匹配协议形状: %r'
                 % (i, dec[i][0][:64]))
    for i in idx['EXPORT_ERROR']:
        f.append('EXPORT_ERROR_SEEN: 目标自己报了导出失败(容量不足): %r' % dec[i][0][:64])

    hdr = idx['HEADER'][0] if idx['HEADER'] else None
    tail = idx['DUMP_TAIL'][0] if idx['DUMP_TAIL'] else None

    # ---- 帧次序与边界 ----
    lo = hi = None
    if hdr is not None and tail is not None and tail <= hdr:
        f.append('FRAME_ORDER: dump 汇总行出现在头行之前 => 帧次序不成立')
    elif hdr is not None and tail is not None:
        lo, hi = hdr, tail
    elif hdr is not None and tail is None:
        lo, hi = hdr, len(dec)
        f.append('FRAME_UNTERMINATED: 头行之后没有 dump 汇总行 => 无法可靠界定本批 CSV 帧')

    inside = []
    for i, (t, lf, nul, c) in enumerate(dec):
        if lo is not None and lo < i < hi:
            inside.append((i, t, lf, nul, c))
        elif c in ('ROW', 'INCOMPLETE'):
            f.append('ROW_OUTSIDE_CSV_FRAME: 第 %d 行是 %s 却在 CSV 帧外: %r' % (i, c, t[:56]))
        elif c == 'STATE' and hi is not None and i > hi:
            f.append('STATE_LINE_AFTER_FRAME: 第 %d 行状态出现在本帧汇总之后, 不得作为依据: %r'
                     % (i, t[:56]))

    # ---- 协议行的字节完整性（NUL / 换行终止）----
    for i, (t, lf, nul, c) in enumerate(dec):
        if c in ('HEADER', 'ROW', 'INCOMPLETE', 'STATE', 'DUMP_TAIL', 'PROTOCOL_BROKEN'):
            if nul:
                f.append('NUL_IN_PROTOCOL_LINE: 第 %d 行(%s) 含 %d 个 NUL —— 被 127 窗口剪断的特征'
                         % (i, c, nul))
            if not lf:
                f.append('PROTOCOL_LINE_UNTERMINATED: 第 %d 行(%s) 没有结尾换行, 与下一行粘连'
                         % (i, c))

    # ---- 逐条数据行 ----
    rows = []
    for i, t, lf, nul, c in inside:
        if c == 'INCOMPLETE':
            f.append('INCOMPLETE_ROW: 第 %d 行是未完成槽的导出 => 该批不得继续' % i)
            continue
        if c == 'STATE':
            f.append('DIAG_LINE_INSIDE_CSV: 第 %d 行是状态/诊断行却在 CSV 帧内: %r' % (i, t[:56]))
            continue
        if c == 'OTHER':
            f.append('ASYNC_LINE_INSIDE_CSV: 第 %d 行不是数据行却在 CSV 帧内: %r' % (i, t[:56]))
            continue
        if c == 'PROTOCOL_BROKEN':
            continue                                    # 已由 PROTOCOL_LINE_MALFORMED 记过
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

    # ---- slot / 代次 / entry / complete / 内容矛盾 ----
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
        if r['entry'] != batch_entry:
            f.append('ENTRY_MISMATCH: slot=%s entry=%s 本批=%s'
                     % (r['slot'], r['entry'], batch_entry))
        if r['complete'] != '1':
            f.append('COMPLETE_NOT_1: slot=%s complete=%s' % (r['slot'], r['complete']))
        if r['body_valid'] == '1':
            bb, be = int(r['body_begin']), int(r['body_end'])
            if be == bb:
                f.append('VALID_WITHOUT_PROGRESS: slot=%s 声称 body_valid=1 但 '
                         'body_end==body_begin(%d)' % (r['slot'], bb))
            elif be < bb:
                f.append('VALID_WITH_WRAP: slot=%s 声称 body_valid=1 但 '
                         'body_end(%d)<body_begin(%d)' % (r['slot'], be, bb))

    # ---- 本批收尾依据：头行之前最后一条可靠状态行 ----
    state = None
    for i, (t, lf, nul, c) in enumerate(dec):
        if c != 'STATE' or (hdr is not None and i > hdr):
            continue
        m = RE_STATUS.match(t)
        if m:
            state = ('status', i, int(m.group(1)), m.group(2), int(m.group(3)),
                     int(m.group(4)), int(m.group(5)))
            continue
        m = RE_STOP.match(t)
        if m:
            state = ('stop', i, int(m.group(1)), None, int(m.group(3)),
                     int(m.group(2)), int(m.group(4)))
    if state is None:
        f.append('BATCH_STATE_MISSING: 头行之前没有可靠 stop/status 行, 本批收尾无依据')
    else:
        kind, si, st_state, st_entry, st_records, st_inflight, st_dropped = state
        if st_state != STATE_STOPPING:
            f.append('BATCH_NOT_STOPPING: 收尾状态 state=%d(必须 STOPPING=2; ACTIVE=1/IDLE=0 '
                     '都不授权清理) 取自 %s 第 %d 行' % (st_state, kind, si))
        if st_inflight != 0:
            f.append('BATCH_IN_FLIGHT: 收尾状态 in_flight=%d 非 0 => 仍有在途, 记录可能不完整'
                     % st_inflight)
        if st_dropped != 0:
            f.append('BATCH_DROPPED: 收尾状态 dropped=%d 非 0 => 本批有丢弃' % st_dropped)
        if st_records != expected_records:
            f.append('BATCH_STATE_RECORDS: 收尾状态 records=%d 与本批预期=%d 不一致'
                     % (st_records, expected_records))
        if kind == 'status' and st_records > 0 and st_entry != batch_entry:
            f.append('BATCH_STATE_ENTRY: 收尾 status entry=%r 与本批 %s 不一致'
                     % (st_entry, batch_entry))

    # ---- 汇总行自身与预期交叉 ----
    if tail is not None:
        m = RE_DUMP_TAIL.match(dec[tail][0])
        if m:
            if int(m.group(1)) != expected_records:
                f.append('DUMP_TAIL_COUNT: dump 汇总 records=%d 期望=%d'
                         % (int(m.group(1)), expected_records))
            if int(m.group(2)) != 0:
                f.append('DUMP_TAIL_DROPPED: dump 汇总 dropped=%d' % int(m.group(2)))

    accepted = not f

    # ---- 时长统计（只对 ACCEPT 的完整批可信）----
    timing_in, timing_out = [], []
    for r in rows:
        ir = int(r['invalid_reason'], 16)
        include = (r['counter_status'] == 'COUNTER_OK' and r['body_valid'] == '1'
                   and r['gates'] == TIMING_GATES_ALL
                   and ir in TIMING_ALLOWED_INVALID_REASON
                   and int(r['body_end']) > int(r['body_begin']))
        item = {'slot': int(r['slot']), 'gen': int(r['gen']), 'entry': r['entry'],
                'counter_status': r['counter_status'], 'body_valid': int(r['body_valid']),
                'gates': r['gates'], 'invalid_reason': r['invalid_reason'],
                'complete': int(r['complete']),
                'body_begin': r['body_begin'], 'body_end': r['body_end']}
        (timing_in if include else timing_out).append(item)

    plan = {'clear_records_send_count': 1 if accepted else 0,
            'next_batch_trigger_send_count': 1 if accepted else 0,
            'extra_protection_actions': 0}

    return {
        'gate_revision': GATE_REVISION,
        'batch': batch_name,
        'verdict': 'ACCEPT' if accepted else 'REJECT',
        'expected_records': expected_records,
        'batch_entry': batch_entry,
        'raw_bytes': len(raw),
        'received_lines': len(dec),
        'lines_without_terminator': sum(1 for t, lf, nul, c in dec if not lf),
        'total_nul_bytes': sum(nul for t, lf, nul, c in dec),
        'csv_rows_parsed': len(rows),
        'closing_state': ({'kind': state[0], 'line': state[1], 'state': state[2],
                           'entry': state[3], 'records': state[4], 'in_flight': state[5],
                           'dropped': state[6]} if state else None),
        'failures': f,
        'action_plan': plan,
        'timing_stats_inclusion': {
            'trusted': bool(accepted),
            'note': ('ACCEPT 批: 时长统计可作后续判读输入' if accepted else
                     'REJECT 批: 计数一律清零, 下面两个观测值仅作诊断, 不得当有效统计'),
            'included_count': len(timing_in) if accepted else 0,
            'excluded_complete_count': len(timing_out) if accepted else 0,
            'observed_included_count': len(timing_in),
            'observed_excluded_count': len(timing_out),
            'included': timing_in if accepted else [],
            'excluded_complete': timing_out if accepted else [],
        },
        'meaning': ('ACCEPT 只表示"证据保存与清理可以继续", 不表示时长合格; '
                    'body_valid=0 的完整记录仍然存档, 只是不进入时长统计。'
                    '本门输出的是放行计划, 不代表任何串口发送已经发生。'),
        'caller_contract': ('调用方必须同时核对: 进程退出码(0=ACCEPT) + JSON verdict + '
                            'action_plan; 任一不可用或读到旧 JSON 一律不得放行'),
    }


# ------------------------------------------------------------------ 自检固件
def row_indices(lines):
    return [i for i, l in enumerate(lines)
            if l[:1].isdigit() and len(l.split(',')) == len(COLUMNS)]


def with_progress(lines, delta=1000):
    """把每条数据行的 body_end 改成 body_begin + delta（真实正向前进）。

    fixture() 的默认行是 body_end==body_begin —— 那正是**上一轮被误用为"有效样本正控"的原始输入**；
    按 GATE-R2-02 现在它必须是具名矛盾负控，所以正控一律显式套本函数（同名同输入可对比）。
    """
    return edit_row(lines, row='all', body_end='+delta', delta=delta)


def edit_row(lines, row=0, delta=1000, **fields):
    """按**列名**索引化改数据行（不依赖易碎的字符串片段）。

    row='all' 改所有记录行；row=N 改第 N 条。值是 '+delta' 时按该行 body_begin+delta 计算。
    """
    idxs = row_indices(lines)
    targets = idxs if row == 'all' else ([idxs[min(row, len(idxs) - 1)]] if idxs else [])
    bi = COLUMNS.index('body_begin')
    for ti in targets:
        c = lines[ti].split(',')
        bb = int(c[bi])
        for nm, v in fields.items():
            j = COLUMNS.index(nm)
            c[j] = str(bb + delta) if v == '+delta' else str(v)
        lines[ti] = ','.join(c)
    return lines


def edit_state(lines, **fields):
    """按字段名改第一条状态行（stop/status 都用同名词, 值必须是纯数字）。"""
    for i, l in enumerate(lines):
        if l.startswith('[spm] stop:') or l.startswith('[spm] status:'):
            for nm, v in fields.items():
                l = re.sub(r'\b%s=\d+' % nm, '%s=%s' % (nm, v), l, count=1)
            lines[i] = l
            break
    return lines


def append_lines(lines, extra):
    return lines + list(extra)


def fixture(entry='trip_fast', rows=3, body_valid='1', invalid='0x0100',
            gates=TIMING_GATES_ALL, cnt='COUNTER_OK', dropped=0, state='stop',
            state_state=STATE_STOPPING, state_records=None, state_entry=None,
            state_inflight=0, extra=None, corrupt=None, eol=b'\r\n', cut_after=None):
    """生成"原始接收字节"。默认数据行零进展 => 正控必须显式套 corrupt=with_progress。"""
    if state_records is None:
        state_records = rows
    lines = []
    if state == 'stop':
        lines.append('[spm] stop: state=%d in_flight=%d records=%d dropped=%d '
                     '(\xe4\xb8\x8d\xe7\xad\x89\xe5\xbe\x85\xe5\x9c\xa8\xe9\x80\x94)'
                     % (state_state, state_inflight, state_records, dropped))
    elif state == 'stop_then_status':
        lines.append('[spm] stop: state=%d in_flight=1 records=%d dropped=%d '
                     '(\xe4\xb8\x8d\xe7\xad\x89\xe5\xbe\x85\xe5\x9c\xa8\xe9\x80\x94)'
                     % (STATE_STOPPING, state_records, dropped))
        lines.append('[spm] status: state=%d entry=%s records=%d in_flight=0 dropped=%d '
                     'counter=COUNTER_OK clock=CONFIG_READBACK guard=0 gates_now=0x0'
                     % (STATE_STOPPING,
                        entry if state_entry is None else state_entry, state_records, dropped))
    elif state == 'status':
        lines.append('[spm] status: state=%d entry=%s records=%d in_flight=%d dropped=%d '
                     'counter=COUNTER_OK clock=CONFIG_READBACK guard=0 gates_now=0x0'
                     % (state_state, entry if state_entry is None else state_entry,
                        state_records, state_inflight, dropped))
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
                v = str(2000 + i)              # 默认 body_end==body_begin(零进展)
            elif COL_TYPE[nm] == 'flag':
                v = '0'
            else:
                v = '0'
            cols.append(v)
        lines.append(','.join(cols))
    lines.append('[spm] dump: records=%d dropped=%d (仅 body_valid=1 的样本进入时长统计)'
                 % (rows, dropped))
    lines.append('msh >')
    if extra:
        lines += extra
    if corrupt:
        lines = corrupt(lines)
    out = b''.join(x.encode('utf-8') + eol for x in lines)
    if cut_after:
        # 模拟"某协议行被 127 窗口剪掉换行且后面再无字节": 把该行连同其换行之后的一切截掉,
        # 使这条协议行成为"没有结尾换行的最后一行"。
        i = out.index(cut_after.encode('utf-8'))
        j = out.index(b'\n', i) + 1
        out = out[:j - len(eol)]
    return out


def make_row(gen=9, slot=9, entry='trip_fast', body_valid='1', progress=1000):
    cols = []
    for nm in COLUMNS:
        if nm == 'schema':
            v = str(SCHEMA)
        elif nm == 'gen':
            v = str(gen)
        elif nm == 'slot':
            v = str(slot)
        elif nm == 'entry':
            v = entry
        elif nm == 'counter_status':
            v = 'COUNTER_OK'
        elif nm == 'clock_basis':
            v = 'CONFIG_READBACK'
        elif nm == 'invalid_reason':
            v = '0x0000'
        elif nm == 'gates':
            v = TIMING_GATES_ALL
        elif nm == 'guard':
            v = '0x00'
        elif nm in ('body_valid', 'complete'):
            v = body_valid
        elif nm == 'body_end':
            v = str(9000 + progress)
        elif nm == 'body_begin':
            v = '9000'
        else:
            v = '0'
        cols.append(v)
    return ','.join(cols)


C = ' '.join          # 简称

SELFTEST_CASES = [
    # ---- 正控（原 GATE-POS-* 身份保留；输入按 R2 修正为"真实进展"）----
    ('GATE-POS-COMPLETE', '正控: 3 条完整且真实进展',
     dict(corrupt=with_progress), 'ACCEPT', None),
    ('GATE-POS-BODY-INVALID', '正控: 完整但 body_valid=0(零进展输入原样, 它不声称有效)',
     dict(body_valid='0'), 'ACCEPT', None),
    ('GATE-POS-STATE-TRANSITION', '正控: stop 仍在途 -> 收尾 status 在途 0(合法过渡, 取最后可靠证据)',
     dict(state='stop_then_status', corrupt=with_progress), 'ACCEPT', None),
    ('GATE-POS-LF-ONLY', '正控: 设备只发 LF(无 CR)同样合法',
     dict(corrupt=with_progress, eol=b'\n'), 'ACCEPT', None),
    ('GATE-POS-STATUS-ENTRY-OK', '正控: 收尾 status 的 entry 与本批一致',
     dict(state='status', corrupt=with_progress), 'ACCEPT', None),
    ('GATE-POS-I32-BOUNDS', '正控: i32 两极端值合法(-2^31 与 2^31-1 同批)',
     dict(corrupt=lambda L: edit_row(with_progress(L), row='all',
                                     pwm_raw_rc=I32_MIN, outcome=I32_MAX)),
     'ACCEPT', None),

    # ---- 原 20 例中的负控（身份与语义保留）----
    ('GATE-NEG-TRUNC-HEADER', '负控: 头行剪成 126 可见字节 + NUL 并与数据行粘连',
     dict(corrupt=lambda L: [L[0], L[1][:126] + '\x00' + L[2]] + L[3:]), 'REJECT',
     'HEADER_EXACT_MATCH'),
    ('GATE-NEG-ROW-NUL-GLUED', '负控: 数据行漏 NUL 粘连下一行',
     dict(corrupt=lambda L: [L[0], L[1], L[2] + '\x00' + L[3]] + L[4:]), 'REJECT',
     'NUL_IN_PROTOCOL_LINE'),
    ('GATE-NEG-MISSING-COLUMN', '负控: 少一列',
     dict(corrupt=lambda L: [L[0], L[1],
                             ','.join(L[2].split(',')[:46])] + L[3:]), 'REJECT', 'COLUMN_COUNT'),
    ('GATE-NEG-DUP-SLOT', '负控: slot 重复',
     dict(corrupt=lambda L: edit_row(L, row=1, slot=0, gen=2)), 'REJECT', 'DUPLICATE_SLOT'),
    ('GATE-NEG-INCOMPLETE', '负控: 帧内出现 INCOMPLETE 槽导出',
     dict(corrupt=lambda L: [L[0], L[1], '1,9,9,INCOMPLETE,gen=9'] + L[2:]), 'REJECT',
     'INCOMPLETE_ROW'),
    ('GATE-NEG-ROW-ABSENT', '负控: 条数不足(预期 3 实际 2)',
     dict(rows=2, expected_override=3), 'REJECT', 'ROW_COUNT'),
    ('GATE-NEG-ASYNC-IN-BLOCK', '负控: 异步日志插进 CSV 帧内',
     dict(corrupt=lambda L: [L[0], L[1], L[2], '[SS] FAULT_LATCHED code=8'] + L[3:]), 'REJECT',
     'ASYNC_LINE_INSIDE_CSV'),
    ('GATE-NEG-DROPPED', '负控: dropped 非 0',
     dict(dropped=1), 'REJECT', 'BATCH_DROPPED'),
    ('GATE-NEG-SCHEMA1', '负控: schema 不是 2',
     dict(corrupt=lambda L: edit_row(L, row=0, schema=1)), 'REJECT', 'FIELD_ILLEGAL'),
    ('GATE-NEG-GEN-GAP', '负控: 代次跳号',
     dict(corrupt=lambda L: edit_row(L, row=2, gen=5)), 'REJECT', 'GEN_SEQUENCE'),
    ('GATE-NEG-INFLIGHT', '负控: 收尾状态 in_flight 非 0',
     dict(state_inflight=1), 'REJECT', 'BATCH_IN_FLIGHT'),
    ('GATE-NEG-BAD-ENUM', '负控: counter_status 取值非法',
     dict(cnt='COUNTER_OKAY'), 'REJECT', 'FIELD_ILLEGAL'),
    ('GATE-NEG-EXPORT-ERROR', '负控: 目标自己报了 EXPORT_ERROR',
     dict(extra=['[spm] EXPORT_ERROR: line_overflow need=490 cap=256']), 'REJECT',
     'EXPORT_ERROR_SEEN'),
    ('GATE-NEG-HEADER-DRIFT', '负控: 头行列名被改名(不逐字匹配)',
     dict(corrupt=lambda L: [L[0], L[1].replace('ctx_isr', 'ctxisr')] + L[2:]), 'REJECT',
     'HEADER_EXACT_MATCH'),
    ('GATE-NEG-NO-STATE-LINE', '负控: 批状态行整条缺失',
     dict(state=None), 'REJECT', 'BATCH_STATE_MISSING'),
    ('GATE-NEG-BAD-GATE-BITS', '负控: gates 写成十进制(不是 0x%02x 形状)' % 0x0F,
     dict(gates='15'), 'REJECT', 'FIELD_ILLEGAL'),

    # ---- GATE-R2-01 新增（任务书 §4.1 的五个反例 + 帧/令牌扩展）----
    ('GATE-R2-NEG-ACTIVE-STATE', '负控: 收尾 state=1(ACTIVE) 不得授权清理',
     dict(state_state=STATE_ACTIVE, corrupt=with_progress), 'REJECT', 'BATCH_NOT_STOPPING'),
    ('GATE-R2-NEG-IDLE-STATE', '负控: 收尾 state=0(IDLE) 同样不得作为批收尾依据',
     dict(state_state=STATE_IDLE, corrupt=with_progress), 'REJECT', 'BATCH_NOT_STOPPING'),
    ('GATE-R2-NEG-NUL-IN-STATE', '负控: 状态行 dropped=0 之后插入 NUL(整行形状也被破坏)',
     dict(corrupt=lambda L: [L[0].replace('dropped=0', 'dropped=0\x00', 1)] + L[1:]), 'REJECT',
     'NUL_IN_PROTOCOL_LINE'),
    ('GATE-R2-NEG-NUL-IN-STATE-TAIL',
     '负控: NUL 只在状态行括注里(整行仍匹配协议) => NUL 检测必须是唯一原因',
     dict(corrupt=lambda L: with_progress(
         [L[0].replace('\xe9\x80\x94)', '\xe9\x80\x94\x00)', 1)] + L[1:])),
     'REJECT', 'NUL_IN_PROTOCOL_LINE'),
    ('GATE-R2-NEG-STATE-NUMERIC-SUFFIX', '负控: dropped=0BAD 前缀伪匹配必须拒',
     dict(corrupt=lambda L: with_progress([L[0].replace('dropped=0', 'dropped=0BAD', 1)] + L[1:])), 'REJECT',
     'PROTOCOL_LINE_MALFORMED'),
    ('GATE-R2-NEG-STATE-RECORDS-GLUE', '负控: records=3BAD(数字令牌被污染)',
     dict(corrupt=lambda L: with_progress([L[0].replace('records=3', 'records=3BAD', 1)] + L[1:])), 'REJECT',
     'PROTOCOL_LINE_MALFORMED'),
    ('GATE-R2-NEG-STATUS-DROPPED-GLUE', '负控: status 行 dropped=0BAD(整行锚定必须拒)',
     dict(state='status', corrupt=lambda L: with_progress(
         [L[0].replace('dropped=0 counter', 'dropped=0BAD counter', 1)] + L[1:])),
     'REJECT', 'PROTOCOL_LINE_MALFORMED'),
    ('GATE-R2-NEG-STATE-UNTERMINATED', '负控: 协议行没有结尾换行(其换行被剪掉且其后无字节)',
     dict(corrupt=with_progress, cut_after='[spm] dump:'), 'REJECT',
     'PROTOCOL_LINE_UNTERMINATED'),
    ('GATE-R2-NEG-EXTRA-ROW-AFTER-TAIL', '负控: 汇总行之后又来一条数据行(帧外记录唯一原因)',
     dict(corrupt=lambda L: with_progress(L) + [make_row()], expected_override=3), 'REJECT',
     'ROW_OUTSIDE_CSV_FRAME'),
    ('GATE-R2-NEG-INCOMPLETE-AFTER-TAIL', '负控: 汇总后又出现 INCOMPLETE 导出',
     dict(corrupt=lambda L: L + ['1,9,9,INCOMPLETE,gen=9']), 'REJECT', 'ROW_OUTSIDE_CSV_FRAME'),
    ('GATE-R2-NEG-SECOND-HEADER', '负控: 帧后再来一条头行(重复头/帧污染)',
     dict(corrupt=lambda L: L + [L[1]]), 'REJECT', 'HEADER_EXACT_MATCH'),
    ('GATE-R2-NEG-SECOND-TAIL', '负控: 帧后再来一条 dump 汇总',
     dict(corrupt=lambda L: L + ['[spm] dump: records=3 dropped=0 tail']), 'REJECT',
     'DUMP_TAIL_EXACTLY_ONE'),
    ('GATE-R2-NEG-STATE-AFTER-TAIL', '负控: 汇总后又出现状态行, 不得当本批依据',
     dict(corrupt=lambda L: L + ['[spm] status: state=2 entry=trip_fast records=3 in_flight=0 '
                                 'dropped=0 counter=COUNTER_OK clock=CONFIG_READBACK guard=0 '
                                 'gates_now=0x0']), 'REJECT', 'STATE_LINE_AFTER_FRAME'),
    ('GATE-R2-NEG-STATUS-ENTRY-MISMATCH', '负控: 收尾 status 的 entry 与本批不同',
     dict(state='status', state_entry='trip_for_operation', corrupt=with_progress), 'REJECT',
     'BATCH_STATE_ENTRY'),
    ('GATE-R2-NEG-STATUS-ENTRY-DASH', '负控: 收尾 status records>0 却 entry=- (身份不自洽)',
     dict(state='status', state_entry='-', corrupt=with_progress), 'REJECT', 'BATCH_STATE_ENTRY'),
    ('GATE-R2-NEG-STATE-RECORDS-MISMATCH', '负控: 状态行 records 与预期不符(不拼接凑成功)',
     dict(state_records=9, corrupt=with_progress), 'REJECT', 'BATCH_STATE_RECORDS'),
    ('GATE-R2-NEG-ROW-ENTRY-MISMATCH', '负控: 某条记录 entry 与本批不同',
     dict(corrupt=lambda L: edit_row(with_progress(L), row=1, entry='off_attempt')), 'REJECT',
     'ENTRY_MISMATCH'),

    # ---- GATE-R2-02 新增（数值域与"有效时间"夹具）----
    ('GATE-R2-NEG-ZERO-PROGRESS-VALID-CLAIM',
     '负控: body_valid=1 但 body_end==body_begin(上一轮误用为正控的输入, 原样保留)',
     dict(invalid='0x0100'), 'REJECT', 'VALID_WITHOUT_PROGRESS'),
    ('GATE-R2-NEG-VALID-WITH-WRAP', '负控: body_valid=1 但 body_end<body_begin(回绕不当有效差分)',
     dict(corrupt=lambda L: edit_row(L, row=0, body_end=1999), invalid='0x0100'), 'REJECT',
     'VALID_WITH_WRAP'),
    ('GATE-R2-NEG-GEN-OVER-U32', '负控: gen=4294967296 超 uint32 上界必须拒(单条批, 唯一原因)',
     dict(rows=1, corrupt=lambda L: edit_row(L, row=0, gen=U32_MAX + 1, body_end='+delta')),
     'REJECT', 'FIELD_ILLEGAL'),
    ('GATE-R2-NEG-U32-OVER-MAX', '负控: pre=2^32 超 uint32 上界必须拒',
     dict(corrupt=lambda L: edit_row(with_progress(L), row=0, pre=U32_MAX + 1)), 'REJECT',
     'FIELD_ILLEGAL'),
    ('GATE-R2-NEG-I32-OVER-MAX', '负控: outcome=2^31 越界必须拒',
     dict(corrupt=lambda L: edit_row(with_progress(L), row=0, outcome=I32_MAX + 1)), 'REJECT',
     'FIELD_ILLEGAL'),
    ('GATE-R2-NEG-I32-UNDER-MIN', '负控: pwm_raw_rc=-(2^31)-1 越界必须拒',
     dict(corrupt=lambda L: edit_row(with_progress(L), row=0, pwm_raw_rc=I32_MIN - 1)), 'REJECT',
     'FIELD_ILLEGAL'),
    ('GATE-R2-NEG-SLOT-OVER', '负控: slot=64 超槽范围必须拒',
     dict(corrupt=lambda L: edit_row(with_progress(L), row=0, slot=SLOTS)), 'REJECT',
     'FIELD_ILLEGAL'),
]


def case_raw(kw):
    """把一个用例的描述性参数分成 fixture 参数与 evaluate 预期条数。"""
    kw = dict(kw)
    expected = kw.pop('expected_override', kw.get('rows', 3))
    return fixture(**kw), expected


def run_case(mod, cid, kw, want):
    raw, expected = case_raw(kw)
    r = mod.evaluate(raw, expected, 'trip_fast', batch_name=cid)
    plan = r['action_plan']
    if want == 'ACCEPT':
        ok = (r['verdict'] == 'ACCEPT' and plan['clear_records_send_count'] == 1
              and plan['next_batch_trigger_send_count'] == 1)
    else:
        ok = (r['verdict'] == 'REJECT' and plan['clear_records_send_count'] == 0
              and plan['next_batch_trigger_send_count'] == 0)
    return r, plan, ok


def selftest():
    n_pass = n_fail = 0
    print('GATE-SELFTEST 逐例判定（REJECT 例还必须命中预期的具名失败原因, 且 clear/next 计划数为 0）')
    for cid, kind, kw, want, reason in SELFTEST_CASES:
        r, plan, ok = run_case(sys.modules[__name__], cid, kw, want)
        if ok and want == 'REJECT':
            hit = any(x.startswith(reason) for x in r['failures']) if reason else True
            if not hit:
                ok = False
        n_pass += 1 if ok else 0
        n_fail += 0 if ok else 1
        print('  %-34s verdict=%-6s clear=%d next=%d %-9s %s'
              % (cid, r['verdict'], plan['clear_records_send_count'],
                 plan['next_batch_trigger_send_count'], 'OK' if ok else 'MISMATCH', kind))
        if not ok:
            for x in r['failures'][:4]:
                print('      failure: %s' % x)

    checks = []
    r = evaluate(fixture(rows=3, body_valid='0'), 3, 'trip_fast', 'GATE-POS-INVALID-ARCHIVED')
    t = r['timing_stats_inclusion']
    checks.append(('GATE-POS-INVALID-ARCHIVED',
                   '完整但无效: 解析并存档 3 条 / 纳入 0 条 / 不追加保护动作',
                   r['verdict'] == 'ACCEPT' and r['csv_rows_parsed'] == 3
                   and t['included_count'] == 0 and t['excluded_complete_count'] == 3
                   and r['action_plan']['extra_protection_actions'] == 0))
    r = evaluate(fixture(rows=3, corrupt=with_progress), 3, 'trip_fast', 'GATE-POS-TIMING-INCLUDE')
    t = r['timing_stats_inclusion']
    checks.append(('GATE-POS-TIMING-INCLUDE',
                   'COUNTER_OK + body_valid=1 + gates=0x0f + reason<=0x0100 + 有进展 => 纳入 3 条',
                   r['verdict'] == 'ACCEPT' and t['included_count'] == 3 and t['trusted'] is True))
    r = evaluate(fixture(rows=3), 3, 'trip_fast', 'GATE-NEG-TIMING-UNTRUSTED')
    t = r['timing_stats_inclusion']
    checks.append(('GATE-NEG-TIMING-UNTRUSTED',
                   'REJECT 批: trusted=False, included/excluded 计数清零, 只留 observed 观测',
                   r['verdict'] == 'REJECT' and t['trusted'] is False
                   and t['included_count'] == 0 and t['excluded_complete_count'] == 0
                   and t['observed_included_count'] == 0))
    for n, v in (('pwm_raw_rc', str(I32_MIN)), ('outcome', str(I32_MAX)),
                 ('permit_write_rc', '0'), ('gen', str(U32_MAX)), ('slot', str(SLOTS - 1))):
        checks.append(('GATE-POS-FIELD-BOUNDARY %s=%s' % (n, v), '合法边界必须判合法',
                       field_ok(n, v)[0]))
    for n, v in (('pwm_raw_rc', str(I32_MIN - 1)), ('outcome', str(I32_MAX + 1)),
                 ('gen', str(U32_MAX + 1)), ('gen', '0'), ('slot', str(SLOTS)),
                 ('pre', str(U32_MAX + 1))):
        checks.append(('GATE-NEG-FIELD-BOUNDARY %s=%s' % (n, v), '越界必须判非法',
                       not field_ok(n, v)[0]))
    print('  语义与字段边界补充:')
    for cid, kind, ok in checks:
        n_pass += 1 if ok else 0
        n_fail += 0 if ok else 1
        print('  %-42s %-58s %s' % (cid, kind, 'OK' if ok else 'MISMATCH'))
    print('GATE-SELFTEST-SUMMARY pass=%d fail=%d cases=%d'
          % (n_pass, n_fail, len(SELFTEST_CASES)))
    return 0 if n_fail == 0 else 1


# ------------------------------------------------------------------ 元负控
# 把某道门**摘掉**，看具名负控是否因此从 REJECT 翻成 ACCEPT：
#   META-OK      至少 1 条翻转 => 这道门确实是那些用例的**唯一**拦截原因（负载性被证明）。
#   META-BLOCKED 无人翻转，但相关用例的**预期原因消失、被别的原因替上** => 多层防御，
#                摘掉它不会造成误放行；如实报告替代原因，不伪装成"用例约束了这道门"。
#   META-FAIL    无人翻转且所有预期原因仍在 => 没有任何具名用例真正依赖这道门（用例空转）。
#   META-INVALID 锚点不唯一 / 变异体编译不过 / 用例异常 => 基础设施故障，既不算检出也不算通过。
# required=True 的道（任务书 §7.5 点名的 STOPPING 门与完整性/帧边界门）必须 META-OK。
META_GUARDS = [
    ('BATCH_NOT_STOPPING', True, '收尾状态必须 STOPPING=2',
     "        if st_state != STATE_STOPPING:", "        if False:"),
    ('ROW_OUTSIDE_CSV_FRAME', True, '帧外数值记录算帧污染(完整性门)',
     "        elif c in ('ROW', 'INCOMPLETE'):", "        elif False:"),
    ('NUL_IN_PROTOCOL_LINE', True, '协议行 NUL 检测(完整性门)',
     '            if nul:', '            if False:'),
    ('PROTOCOL_LINE_UNTERMINATED', True, '协议行换行终止检测(完整性门)',
     '            if not lf:', '            if False:'),
    ('VALID_WITHOUT_PROGRESS', True, '有效但零进展的内容矛盾检测',
     "            if be == bb:", "            if False:"),
    ('STATUS_TOKEN_ANCHOR', False, 'status 行整行锚定(数字令牌边界)',
     "    if RE_STATUS.match(text):\n        return 'STATE'",
     "    if text.startswith('[spm] status:'):\n        return 'STATE'"),
    ('STOP_TOKEN_ANCHOR', False, 'stop 行整行锚定(数字令牌边界)',
     "    if RE_STOP.match(text):\n        return 'STATE'",
     "    if text.startswith('[spm] stop:'):\n        return 'STATE'"),
    ('GEN_U32_UPPER_BOUND', False, 'gen 的 uint32 上界',
     "        ok = bool(re.match(r'^\\d+$', val)) and 1 <= int(val) <= U32_MAX",
     "        ok = bool(re.match(r'^\\d+$', val)) and int(val) >= 1"),
    ('I32_RANGE', False, 'i32 取值域',
     "        ok = bool(re.match(r'^-?\\d+$', val)) and I32_MIN <= int(val) <= I32_MAX",
     "        ok = bool(re.match(r'^-?\\d+$', val))"),
]


def meta_selftest():
    import types
    full = io.open(__file__, encoding='utf-8', newline='').read()
    cut = full.index('META_GUARDS = [')       # 守卫表自身含锚点文本, 只能在其之前的代码段里数
    src = full[:cut]
    base = sys.modules[__name__]
    n_ok = n_blocked = n_bad = 0
    print('GATE-META 元负控：摘掉每道门后, 看具名负控是否翻转(必填门必须翻转)')
    for gname, required, gdesc, old, new in META_GUARDS:
        hits = src.count(old)
        if hits != 1:
            print('  %-26s META-INVALID 锚点命中=%d(需恰好 1), 未运行业务判定' % (gname, hits))
            n_bad += 1
            continue
        mod = types.ModuleType('gate_mut')
        mod.__dict__['__name__'] = 'gate_mut'
        try:
            exec(compile(src.replace(old, new, 1), '<mutated:%s>' % gname, 'exec'), mod.__dict__)
        except Exception as e:
            print('  %-26s META-INVALID 变异源码无法编译: %s' % (gname, e))
            n_bad += 1
            continue
        flipped, taken_over, still, broken = [], [], [], None
        for cid, kind, kw, want, reason in SELFTEST_CASES:
            if want != 'REJECT':
                continue
            try:
                r0, _p0, _o0 = run_case(base, cid, dict(kw), want)
                r1, _p1, _o1 = run_case(mod, cid, dict(kw), want)
            except Exception as e:
                broken = '%s 异常: %s' % (cid, e)
                break
            if r1['verdict'] == 'ACCEPT':
                flipped.append(cid)
            elif not any(x.startswith(reason) for x in r1['failures']):
                taken_over.append('%s->%s' % (cid, r1['failures'][0].split(':')[0]
                                              if r1['failures'] else 'NO-REASON'))
            else:
                still.append(cid)
        if broken:
            print('  %-26s META-INVALID %s' % (gname, broken))
            n_bad += 1
        elif flipped:
            print('  %-26s META-OK     摘掉后翻转 %2d 条: %s   [%s]'
                  % (gname, len(flipped), ' '.join(flipped[:3]), gdesc))
            n_ok += 1
        elif taken_over:
            tag = 'META-FAIL' if required else 'META-BLOCKED'
            print('  %-26s %-11s 无人翻转, 但 %d 条用例的预期原因被替掉: %s   [%s]'
                  % (gname, tag, len(taken_over), ' '.join(taken_over[:2]), gdesc))
            n_bad += 1 if required else 0
            n_blocked += 0 if required else 1
        else:
            print('  %-26s META-FAIL   摘掉后所有具名负控仍给同一原因 => 无用例真正约束这道门 [%s]'
                  % (gname, gdesc))
            n_bad += 1
    print('GATE-META-SUMMARY ok=%d blocked=%d bad=%d guards=%d required=%d'
          % (n_ok, n_blocked, n_bad, len(META_GUARDS),
             sum(1 for g in META_GUARDS if g[1])))
    return 0 if n_bad == 0 else 1


def check_header_against_source(path):
    """把门里的 47 列合同与生产源码的头行字面量逐字比对（双向防漂移）。"""
    s = io.open(path, encoding='utf-8', newline='').read()
    i = s.find('"schema,gen')
    if i < 0:
        print('CHECK-HEADER: NOT-FOUND 在 %s 里找不到 CSV 头行字面量' % path)
        return 4
    j = s.index('complete\\n"', i) + len('complete\\n"')
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', s[i:j])
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
    ap.add_argument('--selftest-meta', action='store_true')
    ap.add_argument('--check-header-against-source')
    a = ap.parse_args(argv[1:])

    if a.selftest:
        return selftest()
    if a.selftest_meta:
        return meta_selftest()
    if a.check_header_against_source:
        return check_header_against_source(a.check_header_against_source)
    if not (a.raw and a.expected_records is not None and a.entry):
        print('用法错误: --raw/--expected-records/--entry 都必需(或改用 --selftest/--selftest-meta)')
        return 4
    try:
        raw = io.open(a.raw, 'rb').read()
    except IOError as e:
        print('输入故障: 原始接收文件读不到 (%s)' % e)
        return 4
    try:
        r = evaluate(raw, a.expected_records, a.entry, batch_name=a.batch)
    except Exception as e:
        print('输入故障: 采集门解析异常(%s), 不产出放行计划, 调用方必须停' % e)
        return 4
    txt = json.dumps(r, ensure_ascii=False, indent=2)
    try:
        json.loads(txt)
    except ValueError as e:
        print('输入故障: 门结论 JSON 自检失败(%s), 不产出放行计划' % e)
        return 4
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
