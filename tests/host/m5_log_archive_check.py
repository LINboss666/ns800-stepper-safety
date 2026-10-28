#!/usr/bin/env python3
"""m5_log_archive_check.py - M5 旧日志逻辑归档判读 (2026-10-29)

输入: 每批 `flash_dump <first> <count>` 的**原始串口字节**文件 + 批次声明。
判据(任何一条不满足即整体 REJECT, 并输出 CLEAR_ALLOWED=NO):
  - 原始件无 NUL; CR 只允许作为 CRLF 行尾的一部分(串口终端的行框, 不改字段);
  - 文件以 LF 结尾(排除尾部截断); 仅允许表头、14 列数据行和少量控制台框行;
  - 表头精确匹配 14 列; 每个数据行恰好 14 个严格十进制字段且类型范围合法;
  - 每批的数据槽必须恰好是声明的 [first, first+count), 无重复、无空槽/读错标记;
  - 跨批必须连续覆盖 0..expected-1, 无重复、无空洞、无越界;
  - 全部通过才输出 ARCHIVE=ACCEPT / CLEAR_ALLOWED=YES (退出码 0)。

拒绝时退出码 2, 并打印具名 reason; 原始件只读, 不清洗、不补齐。
--selftest 用同一个 check_archive() 验证接受与全部拒绝分支。
"""
import argparse
import hashlib
import json
import re
import sys

HEADER = ("slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,"
          "az_mg,step_hz,vibration_mg,flags")
HEADER_FIELDS = HEADER.split(",")
U32_MAX = 4294967295
I32_MIN = -2147483648
I32_MAX = 2147483647
NS_LOG_SLOTS = 4096

# 列类型: True=uint32, False=int32; slot 单独限制 0..4095
# slot,seq,session,time_ms,event,valid | sg,current,ax,ay,az,step,vibration | flags
COL_UINT = [True] * 6 + [False] * 7 + [True]

RE_SLOT_LINE = re.compile(r"^slot ([0-9]+) (empty|error -?[0-9]+)$")
RE_ECHO = re.compile(r"^(?:msh >)?flash_dump ([0-9]+) ([0-9]+)$")
# 采集期间其他线程的正常短行(原始件保留); 它们不是 CSV 数据, 跳过而不清洗。
BACKGROUND_PREFIXES = ("[SENSOR]", "[DIAG]", "[MOT]", "[SS]", "[BB]",
                       "[TMC]", "[CUR]", "[CFG]", "[STEP]", "[SUP]", "[SAFETY]")


class Reject(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _parse_field(text, is_uint):
    if text == "" or text == "-" or text == "+":
        raise Reject("FIELD_FORMAT", "empty/sign-only field")
    if text[0] == "-":
        if is_uint:
            raise Reject("VALUE_RANGE", "negative in uint column: %r" % text)
        digits = text[1:]
    else:
        if text[0] == "+":
            raise Reject("FIELD_FORMAT", "leading plus: %r" % text)
        digits = text
    if not digits.isdigit():
        raise Reject("FIELD_FORMAT", "non-decimal field: %r" % text)
    if len(digits) > 1 and digits[0] == "0":
        raise Reject("FIELD_FORMAT", "leading zero: %r" % text)
    value = int(text, 10)
    if is_uint:
        if value < 0 or value > U32_MAX:
            raise Reject("VALUE_RANGE", "uint32 out of range: %r" % text)
    else:
        if value < I32_MIN or value > I32_MAX:
            raise Reject("VALUE_RANGE", "int32 out of range: %r" % text)
    return value


def parse_batch(data, first, count):
    """解析单批原始字节; 返回 [(slot, [14 fields])]。"""
    if b"\x00" in data:
        raise Reject("NUL_BYTE", "raw batch contains NUL (truncated console line)")
    body = data.replace(b"\r\n", b"\n")
    if b"\r" in body:
        raise Reject("BARE_CR", "CR not followed by LF")
    try:
        text = body.decode("ascii")
    except UnicodeDecodeError:
        raise Reject("NON_ASCII", "raw batch is not ASCII")
    if not text.endswith("\n"):
        raise Reject("TRUNCATED_TAIL", "last line has no LF")
    lines = text.split("\n")[:-1]

    rows = []
    seen_header = False
    seen_slot = set()
    for idx, line in enumerate(lines):
        if line == "msh >":
            continue
        if not seen_header and RE_ECHO.match(line):
            continue
        if not seen_header and line == HEADER:
            seen_header = True
            continue
        if line == HEADER:
            raise Reject("HEADER", "duplicate header at line %d" % (idx + 1))
        m = RE_SLOT_LINE.match(line)
        if m:
            raise Reject("READ_ERROR" if m.group(2) != "empty" else "EMPTY_SLOT",
                         "slot %s marker %r" % (m.group(1), m.group(2)))
        if line.startswith("[FLASH] flash_dump ERROR"):
            raise Reject("EXPORT_ERROR", line)
        if any(line.startswith(p) for p in BACKGROUND_PREFIXES):
            continue
        if not seen_header:
            raise Reject("HEADER", "non-framing line before header: %r" % line[:80])
        parts = line.split(",")
        if len(parts) != 14:
            raise Reject("FIELD_COUNT", "line %d has %d fields" % (idx + 1, len(parts)))
        values = [_parse_field(parts[k], COL_UINT[k]) for k in range(14)]
        slot = values[0]
        if slot > NS_LOG_SLOTS - 1:
            raise Reject("VALUE_RANGE", "slot %d beyond %d" % (slot, NS_LOG_SLOTS - 1))
        if slot in seen_slot:
            raise Reject("DUPLICATE_SLOT", "slot %d repeated in batch" % slot)
        seen_slot.add(slot)
        if slot < first or slot >= first + count:
            raise Reject("RANGE", "slot %d outside batch [%d,%d)"
                         % (slot, first, first + count))
        rows.append((slot, line, values))

    if not seen_header:
        raise Reject("HEADER", "header not found")
    if len(rows) != count:
        missing = sorted(set(range(first, first + count)) - seen_slot)
        raise Reject("MISSING_SLOT",
                     "batch [%d,%d): %d/%d rows, missing %s"
                     % (first, first + count, len(rows), count,
                        missing[:8] if missing else "(count mismatch)"))
    return rows


def check_archive(batches, expected, start=0):
    """batches: [(first, count, bytes)]; 返回 (rows, meta); 失败抛 Reject。"""
    if expected < 1 or expected > NS_LOG_SLOTS:
        raise Reject("EXPECTED_RANGE", "expected=%d" % expected)
    if start < 0 or start + expected > NS_LOG_SLOTS:
        raise Reject("EXPECTED_RANGE", "start=%d expected=%d" % (start, expected))
    if not batches:
        raise Reject("NO_BATCH", "no batch supplied")
    rows = []
    seen = set()
    prev_end = start
    meta_batches = []
    for i, (first, count, data) in enumerate(batches):
        if count < 1 or count > 100:
            raise Reject("BATCH_RANGE", "batch %d count=%d" % (i, count))
        if first < 0 or first >= NS_LOG_SLOTS or first + count > NS_LOG_SLOTS:
            raise Reject("RANGE", "batch %d [%d,%d) outside 0..4095"
                         % (i, first, first + count))
        if first != prev_end:
            raise Reject("GAP" if first > prev_end else "OVERLAP",
                         "batch %d starts %d, expected %d" % (i, first, prev_end))
        if first + count > start + expected:
            raise Reject("EXPECTED_RANGE", "batch %d ends %d > expected end %d"
                         % (i, first + count, start + expected))
        batch_rows = parse_batch(data, first, count)
        for slot, line, values in batch_rows:
            if slot in seen:
                raise Reject("DUPLICATE_SLOT", "slot %d across batches" % slot)
            seen.add(slot)
        rows.extend(batch_rows)
        prev_end = first + count
        meta_batches.append({
            "first": first, "count": count,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    if prev_end != start + expected:
        raise Reject("COVERAGE", "batches end %d, expected %d"
                     % (prev_end, start + expected))
    rows.sort(key=lambda r: r[0])
    if [r[0] for r in rows] != list(range(start, start + expected)):
        raise Reject("COVERAGE", "slot set is not exactly %d..%d"
                     % (start, start + expected - 1))
    return rows, {"expected": expected, "start": start, "rows": len(rows),
                  "batches": meta_batches}


def emit_csv(rows):
    return HEADER + "\n" + "".join(line + "\n" for _, line, _ in rows)


def run_cli(argv):
    ap = argparse.ArgumentParser(description="M5 flash_dump archive checker")
    ap.add_argument("--expected", type=int, required=False)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--batch", nargs=3, action="append", metavar=("FIRST", "COUNT", "RAW"))
    ap.add_argument("--csv")
    ap.add_argument("--json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.expected is None or not args.batch:
        ap.error("--expected and at least one --batch are required")
    try:
        batches = []
        for first, count, path in args.batch:
            with open(path, "rb") as fh:
                batches.append((int(first, 10), int(count, 10), fh.read()))
        rows, meta = check_archive(batches, args.expected, args.start)
    except Reject as exc:
        print("ARCHIVE=REJECT reason=%s detail=%s" % (exc.reason, exc.detail))
        print("CLEAR_ALLOWED=NO")
        return 2
    except (OSError, ValueError) as exc:
        print("ARCHIVE=REJECT reason=INPUT_ERROR detail=%s" % exc)
        print("CLEAR_ALLOWED=NO")
        return 2
    if args.csv:
        with open(args.csv, "w", encoding="ascii", newline="\n") as fh:
            fh.write(emit_csv(rows))
        meta["csv"] = args.csv
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
            fh.write("\n")
    print("ARCHIVE=ACCEPT rows=%d batches=%d" % (len(rows), len(meta["batches"])))
    print("CLEAR_ALLOWED=YES")
    return 0


# ============================== 自测 ==============================
def _row(slot, seq=1, session=2, time_ms=3, event=4, valid=5,
         sg=-6, current=-7, ax=-8, ay=-9, az=-10, step=-11, vib=-12, flags=13):
    return "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d" % (
        slot, seq, session, time_ms, event, valid,
        sg, current, ax, ay, az, step, vib, flags)


def _raw(lines, newline="\n", tail="\n"):
    return (HEADER + newline + newline.join(lines) + tail).encode("ascii")


def _selftest_cases():
    b1 = _raw([_row(0), _row(1, seq=U32_MAX, sg=I32_MIN, current=I32_MAX,
                     ax=I32_MIN, ay=I32_MAX, az=I32_MIN, step=I32_MAX,
                     vib=I32_MIN, flags=U32_MAX), _row(2), _row(3)])
    b2 = _raw([_row(4), _row(5), _row(6), _row(7)])
    good = [(0, 4, b1), (4, 4, b2)]
    cases = []

    cases.append(("AR01-complete_batches_accept", "accept", None, good, 8))
    cases.append(("AR02-missing_slot_rejected", "reject", "MISSING_SLOT", [
        (0, 4, b1), (4, 4, _raw([_row(4), _row(5), _row(7)]))], 8))
    cases.append(("AR03-duplicate_slot_rejected", "reject", "DUPLICATE_SLOT", [
        (0, 4, b1), (4, 4, _raw([_row(4), _row(5), _row(6), _row(5)]))], 8))
    cases.append(("AR04-uint32_overflow_rejected", "reject", "VALUE_RANGE", [
        (0, 4, _raw([_row(0, flags=U32_MAX + 1), _row(1), _row(2), _row(3)])),
        (4, 4, b2)], 8))
    cases.append(("AR05-int32_overflow_rejected", "reject", "VALUE_RANGE", [
        (0, 4, _raw([_row(0), _row(1, sg=I32_MAX + 1), _row(2), _row(3)])),
        (4, 4, b2)], 8))
    cases.append(("AR06-nul_rejected", "reject", "NUL_BYTE", [
        (0, 4, b1[:len(b1) // 2] + b"\x00" + b1[len(b1) // 2:]),
        (4, 4, b2)], 8))
    cases.append(("AR07-missing_column_rejected", "reject", "FIELD_COUNT", [
        (0, 4, _raw([_row(0), "1,1,2,3,4,5,-6,-7,-8,-9,-10,-11,-12",
                     _row(2), _row(3)])), (4, 4, b2)], 8))
    cases.append(("AR08-read_error_rejected", "reject", "READ_ERROR", [
        (0, 4, _raw([_row(0), "slot 1 error -12", _row(2), _row(3)])),
        (4, 4, b2)], 8))
    cases.append(("AR09-batch_out_of_range_rejected", "reject", "EXPECTED_RANGE", [
        (0, 4, b1), (4, 4, b2), (8, 1, _raw([_row(8)]))], 8))
    cases.append(("AR10-gap_rejected", "reject", "GAP", [
        (0, 4, b1), (5, 3, _raw([_row(5), _row(6), _row(7)]))], 8))
    cases.append(("AR11-truncated_tail_rejected", "reject", "TRUNCATED_TAIL", [
        (0, 4, b1[:-1]), (4, 4, b2)], 8))
    cases.append(("AR12-header_missing_rejected", "reject", "HEADER", [
        (0, 4, b"\n".join(b1.split(b"\n")[1:])), (4, 4, b2)], 8))
    cases.append(("AR13-header_renamed_rejected", "reject", "HEADER", [
        (0, 4, b1.replace(b"vibration_mg", b"vibration", 1)), (4, 4, b2)], 8))
    cases.append(("AR14-malformed_extra_line_rejected", "reject", "FIELD_COUNT", [
        (0, 4, b1.replace(b"\n", b"\n[BOGUS]\n", 1)), (4, 4, b2)], 8))
    cases.append(("AR15-crlf_framing_accepted", "accept", None, [
        (0, 4, b1.replace(b"\n", b"\r\n")),
        (4, 4, b2.replace(b"\n", b"\r\n"))], 8))
    cases.append(("AR16-expected_short_rejected", "reject", "EXPECTED_RANGE", good, 7))
    cases.append(("AR17-background_line_tolerated", "accept", None, [
        (0, 4, _raw([_row(0), "[SENSOR] seq=1 tick=2 ax=1",
                     _row(1), _row(2), _row(3)])), (4, 4, b2)], 8))
    cases.append(("AR18-export_error_rejected", "reject", "EXPORT_ERROR", [
        (0, 4, _raw([_row(0),
                     "[FLASH] flash_dump ERROR: line overflow at slot 1",
                     _row(2), _row(3)])), (4, 4, b2)], 8))
    return cases


def selftest():
    cases = _selftest_cases()
    passed = failed = 0
    for case_id, want, want_reason, batches, expected in cases:
        try:
            rows, meta = check_archive(batches, expected)
            got, detail = "accept", "rows=%d" % len(rows)
            if want == "accept":
                csv_ok = emit_csv(rows).encode("ascii").count(b"\n") == expected + 1
                if not csv_ok:
                    got, detail = "reject", "csv lines mismatch"
        except Reject as exc:
            got, detail = "reject", "%s:%s" % (exc.reason, exc.detail)
        ok = (got == want)
        if ok and want == "reject":
            ok = detail.split(":", 1)[0] == want_reason
        passed += 1 if ok else 0
        failed += 0 if ok else 1
        print("CASE|%s|%s|want=%s%s got=%s %s"
              % (case_id, "PASS" if ok else "FAIL", want,
                 ("(" + want_reason + ")") if want_reason else "", got, detail))
    # 新日志从既有 used 槽开始: --start 偏移必须按绝对槽号校验
    try:
        rows, meta = check_archive([(4090, 2, _raw([_row(4090), _row(4091)]))],
                                   2, 4090)
        ok = [r[0] for r in rows] == [4090, 4091] and meta["start"] == 4090
        detail = "rows=%s start=%s" % ([r[0] for r in rows], meta.get("start"))
    except Reject as exc:
        ok, detail = False, "%s:%s" % (exc.reason, exc.detail)
    passed += 1 if ok else 0
    failed += 0 if ok else 1
    print("CASE|AR17-start_offset_accepted|%s|want=accept got=%s %s"
          % ("PASS" if ok else "FAIL", "accept" if ok else "reject", detail))
    print("SUMMARY|cases=%d pass=%d fail=%d" % (passed + failed, passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
