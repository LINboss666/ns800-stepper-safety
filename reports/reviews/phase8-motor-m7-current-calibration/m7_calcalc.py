#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NS800 M7-R1 bus-current calibration fit / qualification tool (v2).

Offline only: no serial, DAP, instrument or device code, and nothing here can
touch a board. Inputs are declared records; the tool fits raw code -> reference
current, checks the qualification gates that were fixed before any field result
existed, and prints a conclusion that matches its own failures.

Two things v1 got wrong and this revision is explicitly about:

* Conclusion consistency - a failed qualification or a failed held-out point now
  makes the whole run REJECT with a non-zero exit and no applicable command. The
  diagnostics are still printed, but labelled not-applicable.
* Error arithmetic - a meter's +/- limit is a **bound**, not a standard
  deviation. v1 root-sum-squared those limits and called the result a
  conservative bound. Here two fit points use an exact corner envelope on
  declared input intervals; first-order sensitivity terms are diagnostic only, and the
  repeatability estimate is carried linearly on top, labelled as a 1-sigma
  estimate rather than folded into a coverage factor.

v1's bytes remain recoverable from git (f188339 / 68d0774); this file replaces it.
"""

import argparse
import ast
import itertools
from fractions import Fraction
import hashlib
import json
import math
import os
import re
import sys

TOOL_ID = "ns800-m7-calcalc-v2.1-cloud-candidate"
SCHEMA = "ns800.m7.calinputs/2"
V1_SCHEMA = "ns800.m7.calinputs/1"

# current_adc.c nominal conversion assumes raw*3300/4095 mV per count.
# 3300 is a configuration assumption, not a measured VDDA.
NOMINAL_K_MV_PER_COUNT = 3300.0 / 4095.0
RAW_BITS_MAX = 4095
RAW_COUNT_BOUND = 0.5           # single-sample quantisation is bounded by half a count
MIN_SAMPLES_PER_POINT = 2       # one sample cannot support any dispersion statement

# Gates fixed before any field result exists (task section 7 / review section 4).
REF_SPAN_ERROR_RATIO_MIN = 10.0
RAW_SPAN_NOISE_RATIO_MIN = 10.0
HOLDOUT_TARGET_ABS_MA = 5.0
HOLDOUT_TARGET_REL = 0.10

PROVENANCE_ALLOWED = ("HOST_SYNTHETIC", "FIELD_MEASURED")
INDEPENDENT_REFERENCE_SOURCES = ("METER_IN_SERIES",
                                 "METER_ACROSS_KNOWN_RESISTOR",
                                 "METER_ACROSS_STABLE_LOAD")
FAIL = "REJECT"
NOT_READY = "METHOD_NOT_READY"

SAMPLE_HEADER_RE = re.compile(r"\[CAL\] sample n_req=(\d+) n_seen=(\d+) n_valid=(\d+)")
SAMPLE_RAW_RE = re.compile(r"\[CAL\] raw sum=(\d+) min=(\d+) max=(\d+) mean_x1000=(\d+)")
SAMPLE_SQ_RE = re.compile(r"\[CAL\] raw sum_sq=(\d+)")
SAMPLE_SEQ_RE = re.compile(r"\[CAL\] sample seq=(\d+)\.\.(\d+) tick=(\d+)\.\.(\d+)")
SAMPLE_FIT_RE = re.compile(r"fit=(YES|NO)")


class Reject(Exception):
    def __init__(self, code, detail):
        Exception.__init__(self, "%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------- primitives

def _num(value, field, allow_zero=True):
    if value is None:
        raise Reject("MISSING_FIELD", field + " is null/absent; not defaulted to 0")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Reject("NON_NUMERIC_FIELD", "%s=%r" % (field, value))
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        raise Reject("NON_FINITE_VALUE", "%s=%r" % (field, value))
    if not allow_zero and f <= 0.0:
        raise Reject("NON_POSITIVE_VALUE", "%s=%r must be > 0" % (field, value))
    return f


def _int_count(value, field):
    """Sample counts must be whole numbers: 128.5 samples is not a measurement."""
    f = _num(value, field)
    if f != math.floor(f):
        raise Reject("FRACTIONAL_COUNT",
                     "%s=%r is not a whole number of samples" % (field, value))
    return int(f)


def _bool(value, field):
    if not isinstance(value, bool):
        raise Reject("NOT_A_BOOLEAN",
                     "%s=%r must be a real true/false, not a string or a number"
                     % (field, value))
    return value


def _req(obj, field, path):
    if field not in obj:
        raise Reject("MISSING_FIELD", "%s.%s absent" % (path, field))
    return obj[field]


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- validation

POINT_REQUIRED_FIELDS = ("id", "role", "used_for_fit", "reference_source",
                         "reference_mA", "reference_error_mA", "raw_sum",
                         "raw_sum_sq", "raw_min", "raw_max", "n_valid",
                         "n_requested", "seq_first", "seq_last", "tick_first",
                         "tick_last", "read_window_s", "h10_mv", "vdd33_mv",
                         "temp_c", "raw_source_file", "raw_source_sha256")


def validate(doc, base_dir=None):
    if doc.get("schema") == V1_SCHEMA:
        raise Reject("SCHEMA_MISMATCH",
                     "input declares the v1 schema; v2 needs %s because the error "
                     "model and the conclusion rule changed" % SCHEMA)
    if doc.get("schema") != SCHEMA:
        raise Reject("SCHEMA_MISMATCH", "expected %s got %r" % (SCHEMA, doc.get("schema")))
    prov = doc.get("provenance")
    if prov not in PROVENANCE_ALLOWED:
        raise Reject("UNKNOWN_PROVENANCE",
                     "provenance=%r must be one of %s" % (prov, list(PROVENANCE_ALLOWED)))
    points = _req(doc, "points", "root")
    if not isinstance(points, list) or not points:
        raise Reject("NO_POINTS", "points must be a non-empty list")

    ids = []
    for p in points:
        pid = str(_req(p, "id", "point"))
        for fld in POINT_REQUIRED_FIELDS:
            _req(p, fld, pid)
        ids.append(pid)
        role = p["role"]
        if role not in ("fit", "holdout"):
            raise Reject("BAD_ROLE", "%s role=%r" % (pid, role))
        uff = _bool(p["used_for_fit"], "%s.used_for_fit" % pid)
        if role == "holdout" and uff:
            raise Reject("HOLDOUT_NOT_INDEPENDENT",
                         "%s declares used_for_fit=true and cannot also be an "
                         "independent verification" % pid)
        if role == "fit" and not uff:
            raise Reject("BAD_ROLE", "%s is role=fit but used_for_fit=false" % pid)
        src = p["reference_source"]
        if src not in INDEPENDENT_REFERENCE_SOURCES:
            raise Reject("NO_INDEPENDENT_REFERENCE",
                         "%s reference_source=%r is not an independent current "
                         "reference. IRUN/VREF math, the same ADC back-solved, "
                         "driver-disabled baselines and adapter total current "
                         "are all forbidden here." % (pid, src))
        _num(p["reference_mA"], "%s.reference_mA" % pid)
        err = _num(p["reference_error_mA"], "%s.reference_error_mA" % pid)
        if err < 0.0:
            raise Reject("NEGATIVE_ERROR_LIMIT",
                         "%s.reference_error_mA=%r is a bound and cannot be "
                         "negative; a negative value also shrinks the span gate "
                         "denominator" % (pid, err))
        if _num(p["read_window_s"], "%s.read_window_s" % pid, allow_zero=False) <= 0.0:
            raise Reject("NON_POSITIVE_VALUE", "%s.read_window_s" % pid)
        for fld in ("raw_sum", "raw_sum_sq", "raw_min", "raw_max", "seq_first",
                    "seq_last", "tick_first", "tick_last", "h10_mv", "vdd33_mv",
                    "temp_c"):
            _num(p[fld], "%s.%s" % (pid, fld))
        nv = _int_count(p["n_valid"], "%s.n_valid" % pid)
        nq = _int_count(p["n_requested"], "%s.n_requested" % pid)
        if nv < 1 or nq < 1:
            raise Reject("INVALID_SAMPLE", "%s n_valid=%r n_requested=%r" % (pid, nv, nq))
        if nv != nq:
            raise Reject("INCOMPLETE_SAMPLE",
                         "%s n_valid=%d != n_requested=%d; a completed sample is "
                         "required, and the previous run's values may not top it up"
                         % (pid, nv, nq))
    if len(set(ids)) != len(ids):
        raise Reject("DUPLICATE_POINT_ID", "point ids must be unique")

    # Independence is settled from the declaration alone, for every provenance: the
    # HOST_SYNTHETIC exemption is about not opening invented files, not about letting a
    # re-used capture stand in as an independent held-out sample.
    _verify_capture_independence(points)

    method = _req(doc, "method", "root")
    meter = _req(doc, "meter", "root")
    if prov == "FIELD_MEASURED":
        for fld in ("branch", "connection", "fuse_rating", "series_voltage_drop_mV",
                    "surcharge_handling", "environment"):
            if method.get(fld) in (None, ""):
                raise Reject(NOT_READY,
                             "FIELD_MEASURED needs method.%s filled; an unfilled gap "
                             "is not evidence that the gap was excluded" % fld)
        for fld in ("model", "dc_range", "resolution", "error_expression"):
            if meter.get(fld) in (None, ""):
                raise Reject(NOT_READY, "FIELD_MEASURED needs meter.%s filled" % fld)
        cov = method.get("covers_all_measured_current")
        if cov is None:
            raise Reject(NOT_READY, "method.covers_all_measured_current undecided")
        _bool(cov, "method.covers_all_measured_current")
        if cov is False:
            if method.get("uncovered_branch_error_mA") is None:
                raise Reject(NOT_READY,
                             "method.uncovered_branch_error_mA must be declared when "
                             "the reference does not cover every ampere flowing "
                             "through the图纸 shunt branch")
            if _num(method["uncovered_branch_error_mA"],
                    "method.uncovered_branch_error_mA") < 0.0:
                raise Reject("NEGATIVE_ERROR_LIMIT",
                             "method.uncovered_branch_error_mA must be >= 0")
        _verify_field_evidence(points, base_dir)
    return doc


def _parse_capture(path):
    """Exactly one complete real current_cal response per FIELD source file.

    LF/CRLF and a known command-echo CR are accepted. Raw files are never rewritten.
    Missing evidence is rejected rather than conditionally skipping its comparisons.
    """
    data = open(path, "rb").read()
    if b"\x00" in data:
        raise Reject("FIELD_SOURCE_PROTOCOL", "NUL in sample source")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError:
        raise Reject("FIELD_SOURCE_PROTOCOL", "invalid source encoding")
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if "[CAL" not in line:
            continue
        at = line.index("[CAL")
        prefix, line = line[:at], line[at:]
        if prefix and not re.fullmatch(
                r"(?:msh\s*(?:>|/>)\s*)?current_cal sample [0-9]+\r", prefix):
            raise Reject("FIELD_SOURCE_PROTOCOL", "interleaved sample line")
        if "\r" in line:
            raise Reject("FIELD_SOURCE_PROTOCOL", "bare CR inside protocol line")
        lines.append(line)
    patterns = (
        r"\[CAL\] sample n_req=(\d+) n_seen=(\d+) n_valid=(\d+) dup=(\d+) invalid=(\d+) skip=(\d+)",
        r"\[CAL\] sample seq=(\d+)\.\.(\d+) tick=(\d+)\.\.(\d+) ver=(\d+)\.\.(\d+) reset=(\d+)",
        r"\[CAL\] raw sum=(\d+) min=(\d+) max=(\d+) mean_x1000=(\d+)",
        r"\[CAL\] raw sum_sq=(\d+)",
        r"\[CAL\] sample cal src=(NONE|THEORETICAL|MEASURED) changed=([01]) fit=(READY|NO) reason=([A-Z_]+)",
        r"\[CAL\] note: sample is observation only; no cal/flash/motion change",
    )
    if len(lines) != len(patterns):
        raise Reject("FIELD_SOURCE_PROTOCOL", "need one complete six-line sample response")
    matches = [re.fullmatch(pat, line) for pat, line in zip(patterns, lines)]
    if not all(matches):
        raise Reject("FIELD_SOURCE_PROTOCOL", "wrong order, missing field or malformed token")
    h, seq, raw, sq, decision, note = matches
    nreq, seen, nv, dup, invalid, skip = map(int, h.groups())
    sf, sl, tf, tl, vf, vl, reset = map(int, seq.groups())
    sm, lo, hi, mean1000 = map(int, raw.groups())
    square = int(sq.group(1))
    src, changed, fit, reason = decision.groups()
    if any(v > 0xFFFFFFFF for v in (nreq,seen,nv,dup,invalid,skip,sf,sl,tf,tl,vf,vl,reset,sm,lo,hi)):
        raise Reject("FIELD_SOURCE_PROTOCOL", "u32 field outside storage domain")
    if square > 0xFFFFFFFFFFFFFFFF or not 32 <= nreq <= 256:
        raise Reject("FIELD_SOURCE_PROTOCOL", "count or sum_sq outside export domain")
    if nv != nreq or seen != nv + invalid or mean1000 != sm * 1000 // nv:
        raise Reject("FIELD_SOURCE_PROTOCOL", "sample count or derived mean contradiction")
    if fit != "READY" or reason != "OK" or changed != "0" or vf != vl or reset != 0:
        raise Reject("FIELD_SOURCE_NOT_FITTABLE", "source did not positively prove a stable READY sample")
    return dict(n_requested=nreq,n_valid=nv,raw_sum=sm,raw_min=lo,raw_max=hi,
                raw_sum_sq=square,seq_first=sf,seq_last=sl,tick_first=tf,tick_last=tl,
                fit=fit,cal_source=src,cal_version=vf)


def _verify_capture_independence(points):
    """Declaration-only independence, run for EVERY provenance.

    The HOST_SYNTHETIC exemption in the task book covers *artifact existence* only: an
    invented file name may not be opened.  It does not license a re-used capture to be
    presented as an independent held-out sample, and that question is answerable from
    the declared fields alone, so it must not be gated on provenance.  Sharing one
    session transcript stays legal exactly when the declared response ranges are disjoint.
    """
    fit_spans = [(q.get("raw_source_file"), int(q["seq_first"]), int(q["seq_last"]), q["id"])
                 for q in points if q["role"] == "fit"]
    for p in points:
        if p["role"] != "holdout":
            continue
        lo, hi = int(p["seq_first"]), int(p["seq_last"])
        for fname, flo, fhi, fid in fit_spans:
            if not fname or fname != p.get("raw_source_file"):
                continue
            if (flo <= lo <= fhi) or (flo <= hi <= fhi) or (lo <= flo <= hi):
                raise Reject("HOLDOUT_NOT_INDEPENDENT",
                             "%s reuses the same capture range as fit point %s in %r; "
                             "changing an id or a bool does not make a sample "
                             "independent, and this check does not depend on provenance"
                             % (p["id"], fid, fname))


def _verify_field_evidence(points, base_dir):
    """Bind every FIELD point to one complete, immutable response and its digest."""
    identities = []
    for p in points:
        pid = p["id"]
        name = p["raw_source_file"]
        if not isinstance(name, str) or not name:
            raise Reject("FIELD_SOURCE_MISSING", "%s has no source filename" % pid)
        path = name if os.path.isabs(name) else os.path.join(base_dir or ".", name)
        if not os.path.isfile(path):
            raise Reject("FIELD_SOURCE_MISSING", "%s source does not exist" % pid)
        got = sha256_file(path)
        if got != str(p["raw_source_sha256"]):
            raise Reject("FIELD_SOURCE_SHA_MISMATCH", "%s digest differs from its file" % pid)
        cap = _parse_capture(path)
        for fld in ("n_requested","n_valid","raw_sum","raw_min","raw_max","raw_sum_sq",
                    "seq_first","seq_last","tick_first","tick_last"):
            if float(cap[fld]) != float(p[fld]):
                raise Reject("FIELD_SOURCE_STATS_MISMATCH", "%s %s differs from the source" % (pid,fld))
        p["_capture"] = cap
        identities.append((p["role"],pid,os.path.normcase(os.path.realpath(path)),got))
    for role,pid,path,digest in identities:
        if role != "holdout":
            continue
        for frole,fid,fpath,fdigest in identities:
            if frole == "fit" and (path == fpath or digest == fdigest):
                raise Reject("HOLDOUT_NOT_INDEPENDENT", "%s copies fit source %s, including renamed copies" % (pid,fid))


def point_stats(p):
    pid = p["id"]
    n = _int_count(p["n_valid"], pid + ".n_valid")
    s = _int_count(p["raw_sum"], pid + ".raw_sum")
    sq = _int_count(p["raw_sum_sq"], pid + ".raw_sum_sq")
    rmin = _int_count(p["raw_min"], pid + ".raw_min")
    rmax = _int_count(p["raw_max"], pid + ".raw_max")
    mean = s / n
    if rmin < 0 or rmax > RAW_BITS_MAX or mean < 0 or mean > RAW_BITS_MAX:
        raise Reject("RAW_OUT_OF_RANGE",
                     "%s min=%g max=%g mean=%.4f outside 0..%d"
                     % (pid, rmin, rmax, mean, RAW_BITS_MAX))
    if mean < rmin or mean > rmax:
        raise Reject("RAW_MEAN_INCONSISTENT",
                     "%s mean=%.4f outside its own [%g,%g] window" % (pid, mean, rmin, rmax))
    if n < MIN_SAMPLES_PER_POINT:
        raise Reject("TOO_FEW_SAMPLES",
                     "%s n_valid=%d; one sample cannot support a dispersion claim"
                     % (pid, n))
    if rmin == 0 or rmax == RAW_BITS_MAX:
        raise Reject("SATURATED_ENDPOINT",
                     "%s touches a rail (min=%g max=%g); saturated raw codes are "
                     "recordable but cannot carry a linear gain fit" % (pid, rmin, rmax))
    # Cauchy-Schwarz: sum_sq >= sum^2/n, equality only when every sample is equal.
    floor = s * s / n
    if sq + 1e-6 < floor:
        raise Reject("IMPOSSIBLE_SECOND_MOMENT",
                     "%s raw_sum_sq=%g < raw_sum^2/n=%g; that variance does not "
                     "exist. It is not clamped to zero." % (pid, sq, floor))
    ceil = n * max(rmin * rmin, rmax * rmax)
    if sq - 1e-6 > ceil:
        raise Reject("IMPOSSIBLE_SECOND_MOMENT",
                     "%s raw_sum_sq=%g cannot come from values inside [%g,%g]"
                     % (pid, sq, rmin, rmax))
    if sq > (rmin+rmax)*s - n*rmin*rmax:
        raise Reject("IMPOSSIBLE_SECOND_MOMENT", "range and sum imply a smaller second moment")
    if rmin < rmax:
        if n == 2:
            attainable = s == rmin+rmax and sq == rmin*rmin+rmax*rmax
        else:
            remaining = s-rmin-rmax
            attainable = ((n-2)*rmin <= remaining <= (n-2)*rmax and
                          sq+1e-6 >= rmin*rmin+rmax*rmax+remaining*remaining/(n-2))
        if not attainable:
            raise Reject("IMPOSSIBLE_SECOND_MOMENT", "declared min/max cannot both have occurred")
    var = (sq - floor) / (n - 1.0)
    if var < 0.0:
        var = 0.0
    sd = math.sqrt(var)
    # Bounded quantisation plus the repeatability estimate, summed by absolute
    # value. No distribution is assumed for the bounded part.
    u_bound = RAW_COUNT_BOUND
    u_repeat = sd / math.sqrt(n)
    return {"id": pid, "n": n, "mean": mean, "sd": sd, "var": var,
            "u_bound_counts": u_bound, "u_repeat_counts": u_repeat,
            "u_counts": u_bound + u_repeat,
            "seq_first": int(p["seq_first"]), "seq_last": int(p["seq_last"]),
            "tick_first": int(p["tick_first"]), "tick_last": int(p["tick_last"]),
            "read_window_s": float(p["read_window_s"]),
            "h10_mv": float(p["h10_mv"]), "vdd33_mv": float(p["vdd33_mv"]),
            "temp_c": float(p["temp_c"])}


def split_points(points):
    fit = [p for p in points if p["role"] == "fit"]
    hold = [p for p in points if p["role"] == "holdout"]
    if len(fit) < 2:
        raise Reject("INSUFFICIENT_FIT_POINTS", "need >=2 fit points, got %d" % len(fit))
    if len(hold) < 1:
        raise Reject("INSUFFICIENT_HOLDOUT_POINTS", "need >=1 held-out point, got %d" % len(hold))
    if len(fit) != 2:
        raise Reject("UNSUPPORTED_BOUND_MODEL", "exactly two fit points are supported by the verified interval model")
    return fit, hold


def _lstsq(xs, ys):
    n = float(len(xs))
    if n == 2:
        dx = xs[1] - xs[0]
        if dx == 0.0:
            raise Reject("DUPLICATE_X", "the two fit raw means are identical")
        a = (ys[1] - ys[0]) / dx
        return a, ys[0] - a * xs[0]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0.0:
        raise Reject("DUPLICATE_X", "all fit raw means are identical")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = sxy / sxx
    return a, my - a * mx


def predict_from(xs, ys, x_h):
    a, b = _lstsq(list(xs), list(ys))
    return a * x_h + b


def fit_line(fit):
    xs = [p["_stats"]["mean"] for p in fit]
    ys = [float(p["reference_mA"]) for p in fit]
    a, b = _lstsq(xs, ys)
    if a <= 0.0:
        raise Reject("NON_POSITIVE_SLOPE",
                     "slope a=%r mA/count; a correctly potted bus shunt is positive" % a)
    if not math.isfinite(a) or not math.isfinite(b):
        raise Reject("NON_FINITE_FIT", "a=%r b=%r" % (a, b))
    return a, b


def jac(fit, x_h, h=1e-5):
    """d(prediction)/d(each input), by re-running the fit on a perturbed input.

    Re-fitting keeps the slope/intercept correlation inside the derivative, so
    the lever-arm term that a naive a/b propagation invents does not appear.
    """
    xs = [p["_stats"]["mean"] for p in fit]
    ys = [float(p["reference_mA"]) for p in fit]
    gx = []
    for i in range(len(xs)):
        xp, xm = list(xs), list(xs)
        xp[i] += h
        xm[i] -= h
        gx.append((predict_from(xp, ys, x_h) - predict_from(xm, ys, x_h)) / (2.0 * h))
    gy = []
    for i in range(len(ys)):
        yp, ym = list(ys), list(ys)
        yp[i] += h
        ym[i] -= h
        gy.append((predict_from(xs, yp, x_h) - predict_from(xs, ym, x_h)) / (2.0 * h))
    gh = (predict_from(xs, ys, x_h + h) - predict_from(xs, ys, x_h - h)) / (2.0 * h)
    return gx, gy, gh


def _two_point_envelope(fit, held):
    if len(fit) != 2:
        raise Reject("UNSUPPORTED_BOUND_MODEL", "this verified interval model supports exactly two fit points")
    q0,q1 = sorted(fit, key=lambda q: q["_stats"]["mean"])
    F = Fraction.from_float
    def interval(center, half):
        c,h = F(float(center)),F(float(half))
        return (c-h,c+h)
    x0 = interval(q0["_stats"]["mean"],q0["_stats"]["u_counts"])
    x1 = interval(q1["_stats"]["mean"],q1["_stats"]["u_counts"])
    if x1[0] <= x0[1]:
        raise Reject("UNCERTAINTY_INTERVALS_OVERLAP", "fit denominator can reach zero")
    y0 = interval(q0["reference_mA"],q0["reference_error_mA"])
    y1 = interval(q1["reference_mA"],q1["reference_error_mA"])
    xh = interval(held["_stats"]["mean"],held["_stats"]["u_counts"])
    values = [a+(b-a)*(x-u)/(v-u) for u,v,a,b,x in itertools.product(x0,x1,y0,y1,xh)]
    return min(values),max(values)

def bounds(doc, fit, hold, a, b):
    """Worst-case (absolute-sum) interval on the prediction at each held-out point."""
    unc = 0.0
    meth = doc.get("method") or {}
    if meth.get("covers_all_measured_current") is False:
        unc = float(meth.get("uncovered_branch_error_mA") or 0.0)
    rows = []
    all_pass = True
    lo_x = min(q["_stats"]["mean"] for q in fit)
    hi_x = max(q["_stats"]["mean"] for q in fit)
    for p in hold:
        x_h = p["_stats"]["mean"]
        if not (lo_x <= x_h <= hi_x):
            raise Reject("EXTRAPOLATION_HOLDOUT",
                         "%s raw mean %.4f is outside the fitted raw-code range "
                         "[%.4f,%.4f]; a held-out point verifies interpolation, "
                         "not extrapolation" % (p["id"], x_h, lo_x, hi_x))
        i_ref = float(p["reference_mA"])
        i_pred = a * x_h + b
        gx, gy, gh = jac(fit, x_h)
        terms = []
        for g, q in zip(gx, fit):
            st = q["_stats"]
            terms.append({"term": "fit_raw_code:%s" % q["id"],
                          "kind": "bounded+repeatability",
                          "half_width_mA": abs(g) * st["u_counts"],
                          "bounded_mA": abs(g) * st["u_bound_counts"],
                          "repeatability_mA": abs(g) * st["u_repeat_counts"]})
        for g, q in zip(gy, fit):
            terms.append({"term": "fit_reference:%s" % q["id"],
                          "kind": "bounded(meter limit, not a standard deviation)",
                          "half_width_mA": abs(g) * float(q["reference_error_mA"])})
        st = p["_stats"]
        terms.append({"term": "holdout_raw_code:%s" % p["id"],
                      "kind": "bounded+repeatability",
                      "half_width_mA": abs(gh) * st["u_counts"],
                      "bounded_mA": abs(gh) * st["u_bound_counts"],
                      "repeatability_mA": abs(gh) * st["u_repeat_counts"]})
        total = sum(t["half_width_mA"] for t in terms)
        # Absolute sum, then the held-out point's own meter limit and any declared
        # uncovered-branch term. Nothing is root-sum-squared here: a +/- limit is a
        # bound, and v1's RSS made the "bound" smaller than the meter alone allows.
        low,high = _two_point_envelope(fit,p)
        centre = Fraction.from_float(float(i_pred))
        exact_radius = max(abs(low-centre),abs(high-centre))
        exact_bound = exact_radius + Fraction.from_float(float(p["reference_error_mA"])) + Fraction.from_float(unc)
        bound = math.nextafter(float(exact_bound),math.inf)
        target = max(HOLDOUT_TARGET_ABS_MA, HOLDOUT_TARGET_REL * abs(i_ref))
        err = abs(i_pred - i_ref)
        bound_ok = bound <= target
        ok = (err <= target) and bound_ok
        all_pass = all_pass and ok
        rows.append({
            "id": p["id"], "raw_mean": x_h, "n_valid": st["n"],
            "reference_mA": i_ref, "predicted_mA": i_pred,
            "abs_error_mA": err, "target_mA": target,
            "target_rule": "PROPOSED_FOR_APPROVAL max(5 mA, 10%% of reference); not "
                           "an inherited project safety limit",
            "error_model": "exact two-point interval / absolute-sum; no distribution, coverage "
                           "factor or independence claim is made for the bounded "
                           "meter and quantisation terms; the repeatability term "
                           "is a 1-sigma estimate carried linearly",
            "terms": terms,
            "interval_method": "exact 32-corner two-point envelope; rational arithmetic; absolute-sum held reference and branch limits",
            "interval_prediction_min_mA": math.nextafter(float(low),-math.inf),
            "interval_prediction_max_mA": math.nextafter(float(high),math.inf),
            "first_order_terms_are_diagnostic_only": True,
            "coverage_scope": "conditional on the declared input intervals; repeatability remains an estimate, not an unconditional physical confidence guarantee",
            "worst_case_bound_mA": bound,
            "holdout_reference_limit_mA": float(p["reference_error_mA"]),
            "uncovered_branch_mA": unc,
            "bound_exceeds_target": (not bound_ok),
            "pass": bool(ok),
        })
    return rows, all_pass


def qualify(fit):
    xs = [p["_stats"]["mean"] for p in fit]
    ys = [float(p["reference_mA"]) for p in fit]
    ey = [float(p["reference_error_mA"]) for p in fit]
    lo_x, hi_x = min(xs), max(xs)
    span_x = hi_x - lo_x
    span_y = max(ys) - min(ys)
    e_sum = ey[xs.index(lo_x)] + ey[xs.index(hi_x)]
    ref_ratio = span_y / e_sum if e_sum > 0 else float("inf")
    ref_ok = e_sum > 0 and ref_ratio > REF_SPAN_ERROR_RATIO_MIN
    u_sum = 0.0
    for q in fit:
        if q["_stats"]["mean"] in (lo_x, hi_x):
            u_sum += q["_stats"]["u_counts"]
    noise_ratio = span_x / u_sum if u_sum > 0 else float("inf")
    noise_ok = u_sum > 0 and noise_ratio > RAW_SPAN_NOISE_RATIO_MIN
    checks = [
        {"gate": "REF_SPAN_GT_10x_SUM_REF_ERROR_BOUNDS",
         "expr": "span_I > %.0f * (E_lo + E_hi)" % REF_SPAN_ERROR_RATIO_MIN,
         "substituted": {"span_I_mA": span_y, "E_lo_plus_E_hi_mA": e_sum,
                         "ratio": ref_ratio, "limit": REF_SPAN_ERROR_RATIO_MIN},
         "pass": bool(ref_ok),
         "reason": "the reference span must dominate the meter error limits at both "
                   "ends, otherwise the fitted slope sits inside the instrument"},
        {"gate": "RAW_SPAN_GT_10x_SUM_CODE_UNCERTAINTY",
         "expr": "span_raw > %.0f * (u(lo) + u(hi))" % RAW_SPAN_NOISE_RATIO_MIN,
         "substituted": {"span_raw_counts": span_x, "sum_u_counts": u_sum,
                         "ratio": noise_ratio, "limit": RAW_SPAN_NOISE_RATIO_MIN},
         "pass": bool(noise_ok),
         "reason": "raw-code span must dominate the half-count bound plus the "
                   "dispersion of the two end points"},
    ]
    return ref_ok and noise_ok, checks


def compute(doc, base_dir=None):
    validate(doc, base_dir)
    for p in doc["points"]:
        p["_stats"] = point_stats(p)
    fit, hold = split_points(doc["points"])
    a, b = fit_line(fit)
    qualified, checks = qualify(fit)
    hrows, hold_pass = bounds(doc, fit, hold, a, b)

    k = NOMINAL_K_MV_PER_COUNT
    gain_v_per_a = k / a
    offset_mv = -b * gain_v_per_a
    if not (0.0 <= offset_mv <= 3300.0):
        raise Reject("OFFSET_OUTSIDE_ADC_RANGE",
                     "offset_mv=%.6f outside the 0..3300 mV window" % offset_mv)
    if not (0.001 <= gain_v_per_a <= 100.0):
        raise Reject("GAIN_OUTSIDE_SAFE_RANGE",
                     "gain_v_per_a=%.9f outside 0.001..100 V/A" % gain_v_per_a)
    offset_uv = int(round(offset_mv * 1000.0))
    gain_uv_per_a = int(round(gain_v_per_a * 1e6))

    usable = bool(qualified and hold_pass)
    refs = sorted([float(p["reference_mA"]) for p in doc["points"]])
    out = {
        "tool": TOOL_ID,
        "provenance": doc["provenance"],
        "status": ("DRAFT_NOT_APPROVED" if usable else FAIL),
        "usable": usable,
        "candidate": {
            "offset_uV": offset_uv,
            "gain_uV_per_A": gain_uv_per_a,
            "offset_mV": offset_mv,
            "gain_V_per_A": gain_v_per_a,
            "slope_mA_per_count": a,
            "intercept_mA": b,
            "nominal_mv_per_count_assumed": k,
            "applicable": usable,
        },
        "candidate_command_DRAFT_NOT_APPROVED": (
            "current_cal apply %d %d CONFIRM_MEASURED" % (offset_uv, gain_uv_per_a)
            if usable else None),
        "not_applicable_reason": None if usable else [
            c["gate"] for c in checks if not c["pass"]
        ] + ([] if hold_pass else ["HOLDOUT_FAILED"]),
        "valid_range": {
            "reference_mA": [refs[0], refs[-1]],
            "raw_counts": [min(p["_stats"]["mean"] for p in doc["points"]),
                           max(p["_stats"]["mean"] for p in doc["points"])],
            "note": "Only inside the measured current, supply, temperature and load "
                    "window of these points. No extrapolation to over-current or "
                    "stall thresholds.",
        },
        "qualification": {"qualified": bool(qualified), "checks": checks},
        "fit_points": [public_point(p) for p in fit],
        "holdout_points": hrows,
        "holdout_all_pass": bool(hold_pass),
        "method": doc.get("method"),
        "meter": doc.get("meter"),
        "notes": [
            "the mV display uses the nominal 3300/4095 assumption; it is not proof "
            "that VDDA was measured",
            "a pass here qualifies the fitted link only, not the whole protection "
            "chain",
            "status stays DRAFT_NOT_APPROVED until an independent review and one "
            "approval of the concrete field package",
            "the fit numbers above are still reported when the run REJECTs, so a "
            "reviewer can see how close it was; they are not an applicable candidate",
        ],
    }
    return out


def public_point(p):
    s = p["_stats"]
    return {"id": p["id"], "raw_mean": s["mean"], "n_valid": s["n"],
            "sd_counts": s["sd"], "u_bound_counts": s["u_bound_counts"],
            "u_repeat_counts": s["u_repeat_counts"], "u_counts": s["u_counts"],
            "reference_mA": float(p["reference_mA"]),
            "reference_error_mA": float(p["reference_error_mA"]),
            "reference_source": p["reference_source"],
            "raw_source_file": p["raw_source_file"],
            "raw_source_sha256": p["raw_source_sha256"],
            "seq": [s["seq_first"], s["seq_last"]],
            "tick": [s["tick_first"], s["tick_last"]],
            "read_window_s": s["read_window_s"], "h10_mv": s["h10_mv"],
            "vdd33_mv": s["vdd33_mv"], "temp_c": s["temp_c"]}


def load_input(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------- selftest
#
# Every number below is invented for tool testing. Nothing here is a field
# current reference, and the emitted command text must never be sent to hardware.

def _pt(pid, role, mean, iref, ierr, n=128, half=2.0):
    """A real two-level code sequence: n/2 samples at mean-half, n/2 at mean+half.

    v1 declared mean+-20 as min/max while its total squared deviation only
    supported +-2, so the "healthy" control could not correspond to 128 samples.
    These moments are computed from the sequence itself.
    """
    assert n % 2 == 0
    lo, hi = mean - half, mean + half
    s = (n // 2) * (lo + hi)
    sq = (n // 2) * (lo * lo + hi * hi)
    return {"id": pid, "role": role, "used_for_fit": role == "fit",
            "reference_source": "METER_IN_SERIES",
            "reference_mA": iref, "reference_error_mA": ierr,
            "raw_sum": s, "raw_sum_sq": sq, "raw_min": lo, "raw_max": hi,
            "n_valid": n, "n_requested": n,
            "seq_first": 10, "seq_last": 10 + n - 1,
            "tick_first": 1000, "tick_last": 1000 + 20 * n,
            "read_window_s": 12.0, "h10_mv": mean * 3300.0 / 4095.0,
            "vdd33_mv": 3300.0, "temp_c": 25.0,
            "raw_source_file": "synth_%s.txt" % pid.lower(),
            "raw_source_sha256": "0" * 64}


def synthetic_doc(mut=None):
    doc = {
        "schema": SCHEMA, "provenance": "HOST_SYNTHETIC",
        "method": {"branch": "HOST_SYNTHETIC", "connection": "HOST_SYNTHETIC",
                   "covers_all_measured_current": True, "fuse_rating": None,
                   "series_voltage_drop_mV": None, "surcharge_handling": None,
                   "environment": None, "uncovered_branch_error_mA": None},
        "meter": {"model": "UT39A+", "dc_range": "400mA", "resolution": "0.1mA",
                  "error_expression": "0.8%+3"},
        # x=2000 -> 20 mA, x=2100 -> 140 mA, held out x=2050 -> 80 mA.
        "points": [_pt("F1", "fit", 2000.0, 20.0, 0.5),
                   _pt("F2", "fit", 2100.0, 140.0, 1.42),
                   _pt("H1", "holdout", 2050.0, 80.0, 0.94)],
    }
    if mut:
        mut(doc)
    return doc


def _check_ok(lines, name, cond, val):
    if cond:
        lines.append("PASS %s" % name)
    else:
        lines.append("FAIL %s value=%s" % (name, val))
    return bool(cond)


def _expect(lines, name, fn, want):
    try:
        fn()
    except Reject as exc:
        if exc.code == want:
            lines.append("PASS %s -> %s" % (name, exc.code))
            return True
        lines.append("FAIL %s wanted %s got %s (%s)" % (name, want, exc.code, exc))
        return False
    except Exception as exc:  # noqa: BLE001
        lines.append("FAIL %s wanted %s got %s: %r" % (name, want, type(exc).__name__, exc))
        return False
    lines.append("FAIL %s wanted %s got NO REJECTION" % (name, want))
    return False


def selftest():
    lines = []
    ok = True

    res = compute(synthetic_doc())
    c = res["candidate"]
    k = NOMINAL_K_MV_PER_COUNT
    checks = [
        ("M7C01-slope", abs(c["slope_mA_per_count"] - 1.2) < 1e-12, c["slope_mA_per_count"]),
        ("M7C02-gain", abs(c["gain_V_per_A"] - k / 1.2) < 1e-12, c["gain_V_per_A"]),
        ("M7C03-offset", abs(c["offset_mV"] - 1598.2905982905984) < 1e-6, c["offset_mV"]),
        ("M7C04-gain-uV", c["gain_uV_per_A"] == 671551, c["gain_uV_per_A"]),
        ("M7C05-offset-uV", c["offset_uV"] == 1598291, c["offset_uV"]),
        ("M7C06-command-draft",
         res["candidate_command_DRAFT_NOT_APPROVED"] ==
         "current_cal apply 1598291 671551 CONFIRM_MEASURED",
         res["candidate_command_DRAFT_NOT_APPROVED"]),
        ("M7C07-status-draft", res["status"] == "DRAFT_NOT_APPROVED", res["status"]),
        ("M7C08-qualified", res["qualification"]["qualified"] is True,
         res["qualification"]["checks"]),
        ("M7C09-holdout-pass", res["holdout_all_pass"] is True and
         res["holdout_points"][0]["abs_error_mA"] < 1e-9, res["holdout_points"]),
        ("M7C10-provenance-synthetic", res["provenance"] == "HOST_SYNTHETIC",
         res["provenance"]),
    ]
    for name, cond, val in checks:
        ok &= _check_ok(lines, name, cond, val)

    # v1's control data was impossible; recompute the moments the sequence gives.
    p0 = res["fit_points"][0]
    ok &= _check_ok(lines, "M7C01b-control-moments-consistent",
                    abs(p0["sd_counts"] - math.sqrt(128 * 4.0 / 127.0)) < 1e-12 and
                    abs(p0["raw_mean"] - 2000.0) < 1e-12 and
                    p0["u_bound_counts"] == 0.5,
                    (p0["sd_counts"], p0["u_bound_counts"]))

    def m_drop_fit(d):
        d["points"] = [d["points"][0], d["points"][2]]
    def m_dup_x(d):
        d["points"][1].update(_pt("F2", "fit", 2000.0, 140.0, 1.42))
    def m_zero_slope(d):
        d["points"][1]["reference_mA"] = 20.0
    def m_neg_slope(d):
        d["points"][1]["reference_mA"] = -140.0
    def m_nan(d):
        d["points"][0]["reference_mA"] = float("nan")
    def m_raw_oob(d):
        d["points"][0].update(_pt("F1", "fit", 5000.0, 20.0, 0.5))
    def m_sat(d):
        d["points"][0]["raw_min"] = 0
    def m_mean_incon(d):
        d["points"][0]["raw_sum"] = 3000.0 * 128
    def m_theory(d):
        d["points"][0]["reference_source"] = "IRUN_VREF_THEORY"
    def m_adapter(d):
        d["points"][0]["reference_source"] = "ADAPTER_TOTAL_CURRENT"
    def m_disabled(d):
        d["points"][0]["reference_source"] = "DRIVER_DISABLED_ZERO"
    def m_no_holdout(d):
        d["points"][2]["role"] = "fit"
        d["points"][2]["used_for_fit"] = True
    def m_holdout_shared(d):
        d["points"][2]["used_for_fit"] = True
    def m_extrap(d):
        d["points"][2].update(_pt("H1", "holdout", 2400.0, 80.0, 0.94))
    def m_low_span(d):
        d["points"][1]["reference_mA"] = 23.0
    def m_missing(d):
        del d["points"][0]["raw_source_sha256"]
    def m_incomplete(d):
        d["points"][0]["n_valid"] = 100
    def m_null(d):
        d["points"][0]["raw_sum"] = None
    def m_schema(d):
        d["schema"] = V1_SCHEMA
    def m_prov(d):
        d["provenance"] = "BOARD_ACCEPTED"
    def m_role(d):
        d["points"][0]["role"] = "close_enough"
    def m_dup_id(d):
        d["points"][1]["id"] = "F1"
    def m_uff_str(d):
        d["points"][2]["used_for_fit"] = "false"
    def m_cov_str(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": "VM leg", "connection": "series",
                           "fuse_rating": "declared", "series_voltage_drop_mV": 12.0,
                           "surcharge_handling": "declared", "environment": "25C",
                           "covers_all_measured_current": "yes",
                           "uncovered_branch_error_mA": 0.0})
    def m_neg_err(d):
        for q in d["points"]:
            q["reference_error_mA"] = -0.01
    def m_zero_window(d):
        d["points"][0]["read_window_s"] = 0.0
    def m_sq_zero(d):
        for q in d["points"]:
            q["raw_sum_sq"] = 0
    def m_frac(d):
        for q in d["points"]:
            mean = q["raw_sum"] / q["n_valid"]
            q["n_valid"] = 128.5
            q["n_requested"] = 128.5
            q["raw_sum"] = mean * 128.5
            q["raw_sum_sq"] = (mean * mean + 4.0) * 128.5
    def m_one_sample(d):
        q = d["points"][0]
        q["n_valid"] = q["n_requested"] = 1
        q["raw_sum"] = 2000.0
        q["raw_sum_sq"] = 2000.0 * 2000.0
        q["raw_min"] = q["raw_max"] = 2000.0
    def m_field_missing(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": "VM leg", "connection": "series",
                            "fuse_rating": "declared", "series_voltage_drop_mV": 12.0,
                            "surcharge_handling": "declared", "environment": "25C",
                            "covers_all_measured_current": True,
                            "uncovered_branch_error_mA": 0.0})
        for q in d["points"]:
            q["raw_source_file"] = "/definitely/nonexistent/m7-%s.bin" % q["id"]
            q["raw_source_sha256"] = "0" * 64
    def m_field_no_method(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": None, "connection": "",
                            "covers_all_measured_current": True})
    def m_field_partial(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": "VM leg", "connection": "series",
                            "fuse_rating": "declared", "series_voltage_drop_mV": 12.0,
                            "surcharge_handling": "declared", "environment": "25C",
                            "covers_all_measured_current": False,
                            "uncovered_branch_error_mA": None})

    negatives = [
        ("M7C11-insufficient-fit", m_drop_fit, "INSUFFICIENT_FIT_POINTS"),
        ("M7C12-duplicate-x", m_dup_x, "DUPLICATE_X"),
        ("M7C13-zero-slope", m_zero_slope, "NON_POSITIVE_SLOPE"),
        ("M7C14-negative-slope", m_neg_slope, "NON_POSITIVE_SLOPE"),
        ("M7C15-nan-reference", m_nan, "NON_FINITE_VALUE"),
        ("M7C16-raw-out-of-range", m_raw_oob, "RAW_OUT_OF_RANGE"),
        ("M7C17-saturated-endpoint", m_sat, "SATURATED_ENDPOINT"),
        ("M7C18-raw-mean-inconsistent", m_mean_incon, "RAW_MEAN_INCONSISTENT"),
        ("M7C19-theory-reference", m_theory, "NO_INDEPENDENT_REFERENCE"),
        ("M7C20-adapter-total", m_adapter, "NO_INDEPENDENT_REFERENCE"),
        ("M7C21-disabled-baseline", m_disabled, "NO_INDEPENDENT_REFERENCE"),
        ("M7C22-missing-heldout", m_no_holdout, "INSUFFICIENT_HOLDOUT_POINTS"),
        ("M7C23-heldout-not-independent", m_holdout_shared, "HOLDOUT_NOT_INDEPENDENT"),
        ("M7C24-extrapolating-heldout", m_extrap, "EXTRAPOLATION_HOLDOUT"),
        ("M7C25-missing-field", m_missing, "MISSING_FIELD"),
        ("M7C26-incomplete-sample", m_incomplete, "INCOMPLETE_SAMPLE"),
        ("M7C27-null-not-zeroed", m_null, "MISSING_FIELD"),
        ("M7C28-schema", m_schema, "SCHEMA_MISMATCH"),
        ("M7C29-provenance", m_prov, "UNKNOWN_PROVENANCE"),
        ("M7C30-bad-role", m_role, "BAD_ROLE"),
        ("M7C31-duplicate-id", m_dup_id, "DUPLICATE_POINT_ID"),
        ("M7C32-field-method-missing", m_field_no_method, "METHOD_NOT_READY"),
        ("M7C33-field-partial-branch", m_field_partial, "METHOD_NOT_READY"),
        ("M7C38-used-for-fit-not-bool", m_uff_str, "NOT_A_BOOLEAN"),
        ("M7C39-cover-not-bool", m_cov_str, "NOT_A_BOOLEAN"),
        ("M7C40-negative-error-limit", m_neg_err, "NEGATIVE_ERROR_LIMIT"),
        ("M7C41-zero-read-window", m_zero_window, "NON_POSITIVE_VALUE"),
        ("M7C42-impossible-second-moment", m_sq_zero, "IMPOSSIBLE_SECOND_MOMENT"),
        ("M7C43-fractional-sample-count", m_frac, "FRACTIONAL_COUNT"),
        ("M7C44-single-sample-point", m_one_sample, "TOO_FEW_SAMPLES"),
        ("M7C45-field-source-missing", m_field_missing, "FIELD_SOURCE_MISSING"),
    ]
    for name, mut, want in negatives:
        ok &= _expect(lines, name, lambda m=mut: compute(synthetic_doc(m)), want)

    # Low reference span must fail the pre-fixed gate rather than be rescued by
    # widening the reference range afterwards.
    low = compute(synthetic_doc(m_low_span))
    ok &= _check_ok(lines, "M7C34-low-reference-span-rejected",
                    low["status"] == FAIL and low["usable"] is False and
                    low["candidate_command_DRAFT_NOT_APPROVED"] is None and
                    "REF_SPAN_GT_10x_SUM_REF_ERROR_BOUNDS" in
                    low["not_applicable_reason"],
                    (low["status"], low["not_applicable_reason"]))

    # Bounded meter limits summed by absolute value, not root-sum-squared: the
    # review's counterexample. Three 5.9 mA limits, span 120 mA still qualifies.
    def m_bound(d):
        for q in d["points"]:
            q["reference_error_mA"] = 5.9
    be = compute(synthetic_doc(m_bound))
    row = be["holdout_points"][0]
    meter_only_worst = 0.5 * 5.9 + 0.5 * 5.9 + 5.9
    ok &= _check_ok(lines, "M7C35-bound-exceeds-target-not-pass",
                    be["qualification"]["qualified"] is True and
                    row["bound_exceeds_target"] is True and
                    be["holdout_all_pass"] is False and
                    be["usable"] is False and
                    be["candidate_command_DRAFT_NOT_APPROVED"] is None and
                    row["worst_case_bound_mA"] >= meter_only_worst,
                    (row["worst_case_bound_mA"], row["target_mA"], meter_only_worst))
    ok &= _check_ok(lines, "M7C35b-no-rss-of-bounded-terms",
                    all(t["kind"].startswith("bounded") or
                        t["half_width_mA"] >= 0.0 for t in row["terms"]) and
                    "absolute-sum" in row["error_model"],
                    row["error_model"])

    # Reused capture cannot become an independent held-out point (real files).
    import tempfile

    def field_shell(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": "VM leg", "connection": "series",
                           "fuse_rating": "declared", "series_voltage_drop_mV": 12.0,
                           "surcharge_handling": "declared", "environment": "25C",
                           "covers_all_measured_current": True,
                           "uncovered_branch_error_mA": 0.0})

    def write_capture(td, p, name):
        path = os.path.join(td, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[CAL] sample n_req=%d n_seen=%d n_valid=%d dup=0 invalid=0 skip=0\n"
                     % (p["n_requested"], p["n_valid"], p["n_valid"]))
            fh.write("[CAL] sample seq=%d..%d tick=%d..%d ver=3..3 reset=0\n"
                     % (p["seq_first"], p["seq_last"], p["tick_first"], p["tick_last"]))
            fh.write("[CAL] raw sum=%d min=%d max=%d mean_x1000=%d\n"
                     % (p["raw_sum"], p["raw_min"], p["raw_max"],
                        int(round(p["raw_sum"] * 1000.0 / p["n_valid"]))))
            fh.write("[CAL] raw sum_sq=%d\n" % p["raw_sum_sq"])
            fh.write("[CAL] sample cal src=THEORETICAL changed=0 fit=READY reason=OK\n")
            fh.write("[CAL] note: sample is observation only; no cal/flash/motion change\n")
        return path

    with tempfile.TemporaryDirectory() as td:
        # M7C46: every point claims the *same* capture and the *same* response
        # range, so no held-out point has any evidence of its own.
        def m_reuse(d, td=td):
            field_shell(d)
            shared = None
            for q in d["points"]:
                shared = shared or write_capture(td, q, "shared_capture.txt")
                q["raw_source_file"] = shared
                q["raw_source_sha256"] = sha256_file(shared)
        ok &= _expect(lines, "M7C46-reused-capture-not-independent",
                      lambda: compute(synthetic_doc(m_reuse), base_dir=td),
                      "HOLDOUT_NOT_INDEPENDENT")

        # M7C53: the same reuse under HOST_SYNTHETIC, where the task book exempts the
        # artifact from *existence* checks.  Independence is a declaration question, so
        # the exemption must not silence it (this is the review's REUSED_HOLDOUT_ARTIFACT
        # input, which used to come back qualified=true).
        def m_reuse_synth(d):
            for q in d["points"]:
                q["raw_source_file"] = "same_capture.bin"
                q["raw_source_sha256"] = "a" * 64
        ok &= _expect(lines, "M7C53-synthetic-reused-capture-not-independent",
                      lambda: compute(synthetic_doc(m_reuse_synth), base_dir=None),
                      "HOLDOUT_NOT_INDEPENDENT")

        # Separate real captures per point: the independence question is settled,
        # so what is left to catch is a lying declaration.
        def m_stats_lie(d, td=td):
            field_shell(d)
            for q in d["points"]:
                path = write_capture(td, q, "cap_%s.txt" % q["id"])
                q["raw_source_file"] = path
                q["raw_source_sha256"] = sha256_file(path)
            d["points"][0]["raw_sum_sq"] += 1000.0
        ok &= _expect(lines, "M7C47-declared-stats-not-in-artifact",
                      lambda: compute(synthetic_doc(m_stats_lie), base_dir=td),
                      "FIELD_SOURCE_STATS_MISMATCH")

        def m_sha_lie(d, td=td):
            field_shell(d)
            for q in d["points"]:
                path = write_capture(td, q, "sha_%s.txt" % q["id"])
                q["raw_source_file"] = path
                q["raw_source_sha256"] = sha256_file(path)
            d["points"][0]["raw_source_sha256"] = "a" * 64
        ok &= _expect(lines, "M7C48-declared-sha-not-artifact",
                      lambda: compute(synthetic_doc(m_sha_lie), base_dir=td),
                      "FIELD_SOURCE_SHA_MISMATCH")

        def m_good_field(d, td=td):
            field_shell(d)
            for q in d["points"]:
                path = write_capture(td, q, "good_%s.txt" % q["id"])
                q["raw_source_file"] = path
                q["raw_source_sha256"] = sha256_file(path)
        good = compute(synthetic_doc(m_good_field), base_dir=td)
        ok &= _check_ok(lines, "M7C49-field-evidence-accepted",
                        good["usable"] is True and
                        good["status"] == "DRAFT_NOT_APPROVED" and
                        good["provenance"] == "FIELD_MEASURED",
                        (good["status"], good["not_applicable_reason"]))

    # CLI exit codes: the review asked that the *process* conclusion match the
    # internal one, for the bound failure and for the low-span failure too.
    import tempfile as _tf
    import io as _io

    def _cli(name, mut):
        with _tf.TemporaryDirectory() as run_dir:
            path = os.path.join(run_dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(synthetic_doc(mut), indent=2, sort_keys=True))
            buf = _io.StringIO()
            real, sys.stdout = sys.stdout, buf
            try:
                rc = main(["--input", path])
            finally:
                sys.stdout = real
            return rc, buf.getvalue()

    rc, text = _cli("cli_lowspan.json", m_low_span)
    ok &= _check_ok(lines, "M7C50-cli-low-span-exits-nonzero",
                    rc == 1 and "current_cal apply" not in text and
                    '"usable": false' in text, (rc, text[-120:]))
    rc, text = _cli("cli_bound.json", m_bound)
    ok &= _check_ok(lines, "M7C51-cli-bound-failure-exits-nonzero",
                    rc == 1 and "current_cal apply" not in text, (rc, text[-120:]))
    rc, text = _cli("cli_healthy.json", None)
    ok &= _check_ok(lines, "M7C52-cli-healthy-exits-zero-draft-only",
                    rc == 0 and "DRAFT_NOT_APPROVED" in text and
                    "current_cal apply" in text, (rc, text[-120:]))

    src = open(os.path.abspath(__file__), "r", encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    banned = {"serial", "usb", "pyocd", "ctypes", "socket", "subprocess"}
    ok &= _check_ok(lines, "M7C36-no-transport-or-device-imports",
                    not (mods & banned), sorted(mods))

    npass = sum(1 for ln in lines if ln.startswith("PASS"))
    for ln in lines:
        print(ln)
    print("SUMMARY cases=%d pass=%d fail=%d" % (len(lines), npass, len(lines) - npass))
    print("M7 CALCALC SELFTEST: %s" % ("PASS" if ok else "FAIL"))
    return ok, lines


def cli_result(res):
    """A REJECT must leave the process non-zero and must not print an apply line."""
    print(json.dumps(res, indent=2, sort_keys=True))
    if not res["usable"]:
        print("REJECT %s: %s" % (res["status"], ", ".join(res["not_applicable_reason"])))
        return 1
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="m7_calcalc.py")
    ap.add_argument("--input")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--hash")
    ap.add_argument("--emit-example", metavar="PATH")
    args = ap.parse_args(argv)

    if args.hash:
        print(sha256_file(args.hash))
        return 0
    if args.emit_example:
        with open(args.emit_example, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(synthetic_doc(), indent=2, sort_keys=True) + "\n")
        print("wrote HOST_SYNTHETIC example to %s" % args.emit_example)
        return 0
    if args.selftest:
        ok, _ = selftest()
        return 0 if ok else 1
    if not args.input:
        print("usage: --selftest | --input FILE [--out FILE]", file=sys.stderr)
        return 2
    try:
        result = compute(load_input(args.input),
                         base_dir=os.path.dirname(os.path.abspath(args.input)))
    except Reject as exc:
        print("REJECT %s" % exc)
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return cli_result(result)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
