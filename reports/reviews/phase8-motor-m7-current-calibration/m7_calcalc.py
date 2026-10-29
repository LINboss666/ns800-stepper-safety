#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NS800 M7 bus-current calibration fit / qualification tool.

Offline only. It reads a declared record file, fits raw-code -> independent
reference current, and emits a DRAFT_NOT_APPROVED candidate. It never talks to
a board and never marks anything as board-accepted.

Provenance levels accepted in the input:
  HOST_SYNTHETIC  numbers invented for this tool's own tests; output stays DRAFT
  FIELD_MEASURED  real meter readings; the method/meter fields must be filled
Anything else is rejected. Missing numbers are never defaulted to 0.
"""

import argparse
import ast
import hashlib
import json
import math
import os
import sys

TOOL_ID = "ns800-m7-calcalc-v1"
SCHEMA = "ns800.m7.calinputs/1"

# current_adc.c nominal conversion assumes raw*3300/4095 mV per count.
# That 3300 is a configuration assumption, not a measured VDDA.
NOMINAL_K_MV_PER_COUNT = 3300.0 / 4095.0
RAW_BITS_MAX = 4095

# Gates are fixed here, before any field result exists (task section 7).
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

REJECT_CODES = (
    "SCHEMA_MISMATCH", "UNKNOWN_PROVENANCE", "NO_POINTS", "MISSING_FIELD",
    "NON_NUMERIC_FIELD", "NON_FINITE_VALUE", "BAD_ROLE", "DUPLICATE_POINT_ID",
    "NO_INDEPENDENT_REFERENCE", "INVALID_SAMPLE", "INCOMPLETE_SAMPLE",
    "RAW_OUT_OF_RANGE", "SATURATED_ENDPOINT", "RAW_MEAN_INCONSISTENT",
    "INSUFFICIENT_FIT_POINTS", "INSUFFICIENT_HOLDOUT_POINTS",
    "HOLDOUT_NOT_INDEPENDENT", "EXTRAPOLATION_HOLDOUT",
    NOT_READY, "DUPLICATE_X", "NON_POSITIVE_SLOPE", "NON_FINITE_FIT",
    "QUALIFICATION_FAILED", "OFFSET_OUTSIDE_ADC_RANGE",
    "GAIN_OUTSIDE_SAFE_RANGE",
)


class Reject(Exception):
    def __init__(self, code, detail):
        Exception.__init__(self, "%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def _num(value, field):
    if value is None:
        raise Reject("MISSING_FIELD", field + " is null/absent; not defaulted to 0")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Reject("NON_NUMERIC_FIELD", "%s=%r" % (field, value))
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        raise Reject("NON_FINITE_VALUE", "%s=%r" % (field, value))
    return f


def _req(obj, field, path):
    if field not in obj:
        raise Reject("MISSING_FIELD", "%s.%s absent" % (path, field))
    return obj[field]


POINT_REQUIRED_FIELDS = ("id", "role", "used_for_fit", "reference_source",
                         "reference_mA", "reference_error_mA", "raw_sum",
                         "raw_sum_sq", "raw_min", "raw_max", "n_valid",
                         "n_requested", "seq_first", "seq_last", "tick_first",
                         "tick_last", "read_window_s", "h10_mv", "vdd33_mv",
                         "temp_c", "raw_source_file", "raw_source_sha256")


def validate(doc):
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
        uff = p["used_for_fit"]
        if uff is None:
            raise Reject("MISSING_FIELD", "%s.used_for_fit undecided" % pid)
        if role == "holdout" and uff is True:
            raise Reject("HOLDOUT_NOT_INDEPENDENT",
                         "%s is declared as fitted and then used as its own "
                         "independent verification" % pid)
        if role == "fit" and uff is False:
            raise Reject("BAD_ROLE", "%s is role=fit but used_for_fit=false" % pid)
        src = p["reference_source"]
        if src not in INDEPENDENT_REFERENCE_SOURCES:
            raise Reject("NO_INDEPENDENT_REFERENCE",
                         "%s reference_source=%r is not an independent current "
                         "reference. IRUN/VREF math, the same ADC back-solved, "
                         "driver-disabled baselines and adapter total current "
                         "are all forbidden here." % (pid, src))
        for nfld in ("reference_mA", "reference_error_mA", "raw_sum", "raw_sum_sq",
                     "raw_min", "raw_max", "n_valid", "n_requested", "seq_first",
                     "seq_last", "tick_first", "tick_last", "read_window_s",
                     "h10_mv", "vdd33_mv", "temp_c"):
            _num(p[nfld], "%s.%s" % (pid, nfld))
        nv = float(p["n_valid"])
        nq = float(p["n_requested"])
        if nv < 1 or nq < 1:
            raise Reject("INVALID_SAMPLE", "%s n_valid=%r n_requested=%r" % (pid, nv, nq))
        if nv < nq:
            raise Reject("INCOMPLETE_SAMPLE",
                         "%s n_valid=%g < n_requested=%g; do not top up with the "
                         "previous run's values" % (pid, nv, nq))
    if len(set(ids)) != len(ids):
        raise Reject("DUPLICATE_POINT_ID", "point ids must be unique")

    method = _req(doc, "method", "root")
    meter = _req(doc, "meter", "root")
    if prov == "FIELD_MEASURED":
        for fld in ("branch", "connection", "fuse_rating", "series_voltage_drop_mV",
                    "surcharge_handling", "environment"):
            if method.get(fld) in (None, ""):
                raise Reject(NOT_READY,
                             "FIELD_MEASURED needs method.%s filled; an unfilled "
                             "gap is not evidence that the gap was excluded" % fld)
        for fld in ("model", "dc_range", "resolution", "error_expression"):
            if meter.get(fld) in (None, ""):
                raise Reject(NOT_READY, "FIELD_MEASURED needs meter.%s filled" % fld)
        cov = method.get("covers_all_measured_current")
        if cov is None:
            raise Reject(NOT_READY, "method.covers_all_measured_current undecided")
        if cov is False:
            # A declared uncovered sibling branch is allowed only with its own
            # error carried into the budget below.
            if method.get("uncovered_branch_error_mA") is None:
                raise Reject(NOT_READY,
                             "method.uncovered_branch_error_mA must be declared "
                             "when the reference does not cover every ampere that "
                             "flows through the图纸 shunt branch")
            _num(method["uncovered_branch_error_mA"], "method.uncovered_branch_error_mA")
    return doc


def point_stats(p):
    pid = p["id"]
    n = _num(p["n_valid"], pid + ".n_valid")
    s = _num(p["raw_sum"], pid + ".raw_sum")
    sq = _num(p["raw_sum_sq"], pid + ".raw_sum_sq")
    rmin = _num(p["raw_min"], pid + ".raw_min")
    rmax = _num(p["raw_max"], pid + ".raw_max")
    mean = s / n
    if rmin < 0 or rmax > RAW_BITS_MAX or mean < 0 or mean > RAW_BITS_MAX:
        raise Reject("RAW_OUT_OF_RANGE",
                     "%s min=%g max=%g mean=%.4f outside 0..%d"
                     % (pid, rmin, rmax, mean, RAW_BITS_MAX))
    if mean < rmin or mean > rmax:
        raise Reject("RAW_MEAN_INCONSISTENT",
                     "%s mean=%.4f outside its own [%g,%g] window" % (pid, mean, rmin, rmax))
    if rmin == 0 or rmax == RAW_BITS_MAX:
        raise Reject("SATURATED_ENDPOINT",
                     "%s touches a rail (min=%g max=%g); saturated raw codes are "
                     "recordable but cannot carry a linear gain fit" % (pid, rmin, rmax))
    var = (sq - s * s / n) / (n - 1.0) if n > 1 else 0.0
    if var < 0.0:
        var = 0.0
    u_noise = math.sqrt(var / n)
    u_quant = 1.0 / math.sqrt(12.0)
    return {"id": pid, "n": n, "mean": mean, "sd": math.sqrt(var),
            "u_x": math.hypot(u_noise, u_quant),
            "seq_first": p["seq_first"], "seq_last": p["seq_last"],
            "tick_first": p["tick_first"], "tick_last": p["tick_last"],
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


def predict(fit, x_h):
    a, b = _lstsq([p["_stats"]["mean"] for p in fit],
                  [float(p["reference_mA"]) for p in fit])
    return a * x_h + b


def predict_from(xs, ys, x_h):
    a, b = _lstsq(list(xs), list(ys))
    return a * x_h + b


def jac(fit, x_h, ux, ui, h=1e-5):
    """First-order sensitivity of the prediction, by central differences.

    Perturbing a fit raw mean or reference current re-runs the whole fit, so
    the strong slope/intercept correlation cancels the way it does in reality
    instead of inflating a lever-arm term.
    """
    xs = [p["_stats"]["mean"] for p in fit]
    ys = [float(p["reference_mA"]) for p in fit]
    gx = []
    for i in range(len(xs)):
        xp = list(xs)
        xm = list(xs)
        xp[i] += h
        xm[i] -= h
        gx.append((predict_from(xp, ys, x_h) - predict_from(xm, ys, x_h)) / (2.0 * h))
    gy = []
    for i in range(len(ys)):
        yp = list(ys)
        ym = list(ys)
        yp[i] += h
        ym[i] -= h
        gy.append((predict_from(xs, yp, x_h) - predict_from(xs, ym, x_h)) / (2.0 * h))
    gh = (predict_from(xs, ys, x_h + h) - predict_from(xs, ys, x_h - h)) / (2.0 * h)
    var = sum((g * u) ** 2 for g, u in zip(gx, ux[:len(xs)]))
    var += sum((g * u) ** 2 for g, u in zip(gy, ui))
    var += (gh * ux[len(xs)]) ** 2
    return math.sqrt(var), {"d_per_raw_mean": gx, "d_per_reference_mA": gy,
                            "d_per_holdout_raw_mean": gh}


def qualify(fit, spans):
    lo_x, hi_x = spans["lo_x"], spans["hi_x"]
    xs = [p["_stats"]["mean"] for p in fit]
    ys = [float(p["reference_mA"]) for p in fit]
    ey = [float(p["reference_error_mA"]) for p in fit]
    span_x = hi_x - lo_x
    span_y = max(ys) - min(ys)
    e_sum = ey[xs.index(lo_x)] + ey[xs.index(hi_x)]
    ref_ratio = span_y / e_sum if e_sum > 0 else float("inf")
    ref_ok = ref_ratio > REF_SPAN_ERROR_RATIO_MIN
    u_diff = math.hypot(*[p["_stats"]["u_x"] for p in fit
                          if p["_stats"]["mean"] in (lo_x, hi_x)])
    noise_ratio = span_x / u_diff if u_diff > 0 else float("inf")
    noise_ok = noise_ratio > RAW_SPAN_NOISE_RATIO_MIN
    checks = [
        {"gate": "REF_SPAN_GT_10x_SUM_REF_ERROR_BOUNDS",
         "expr": "span_I > %.0f * (E_lo + E_hi)" % REF_SPAN_ERROR_RATIO_MIN,
         "substituted": {"span_I_mA": span_y, "E_lo_mA": e_sum, "ratio": ref_ratio,
                         "limit": REF_SPAN_ERROR_RATIO_MIN},
         "pass": bool(ref_ok),
         "reason": "the reference span must dominate the meter error limits at "
                   "both ends, otherwise the slope is inside the instrument"},
        {"gate": "RAW_SPAN_GT_10x_CODE_UNCERTAINTY",
         "expr": "span_raw > %.0f * u(raw_mean_lo - raw_mean_hi)" % RAW_SPAN_NOISE_RATIO_MIN,
         "substituted": {"span_raw_counts": span_x, "u_diff_counts": u_diff,
                         "ratio": noise_ratio, "limit": RAW_SPAN_NOISE_RATIO_MIN},
         "pass": bool(noise_ok),
         "reason": "raw-code span must dominate per-point dispersion plus the "
                   "1/sqrt(12) quantisation of a single count"},
    ]
    return ref_ok and noise_ok, checks


def holdout_rows(doc, fit, hold, a, b):
    unc = 0.0
    meth = doc.get("method") or {}
    if meth.get("covers_all_measured_current") is False:
        unc = float(meth.get("uncovered_branch_error_mA") or 0.0)
    ux_fit = [p["_stats"]["u_x"] for p in fit]
    ui_fit = [float(p["reference_error_mA"]) for p in fit]
    rows = []
    all_pass = True
    for p in hold:
        x_h = p["_stats"]["mean"]
        if not (min(q["_stats"]["mean"] for q in fit) <= x_h <=
                max(q["_stats"]["mean"] for q in fit)):
            raise Reject("EXTRAPOLATION_HOLDOUT",
                         "%s raw mean %.4f is outside the fitted raw-code range; "
                         "a held-out point verifies interpolation, not extrapolation"
                         % (p["id"], x_h))
        i_ref = float(p["reference_mA"])
        i_pred = a * x_h + b
        err = abs(i_pred - i_ref)
        target = max(HOLDOUT_TARGET_ABS_MA, HOLDOUT_TARGET_REL * abs(i_ref))
        u_pred, sens = jac(fit, x_h, ux_fit + [p["_stats"]["u_x"]], ui_fit)
        bound = math.sqrt(u_pred * u_pred +
                          float(p["reference_error_mA"]) ** 2 + unc * unc)
        bound_ok = bound <= target
        ok = (err <= target) and bound_ok
        all_pass = all_pass and ok
        rows.append({
            "id": p["id"], "raw_mean": x_h, "n_valid": p["_stats"]["n"],
            "reference_mA": i_ref, "predicted_mA": i_pred,
            "abs_error_mA": err, "target_mA": target,
            "target_rule": "PROPOSED_FOR_APPROVAL max(5 mA, 10%% of reference); "
                           "not an inherited project safety limit",
            "fit_propagated_mA": u_pred,
            "reference_error_mA": float(p["reference_error_mA"]),
            "uncovered_branch_mA": unc,
            "combined_error_bound_mA": bound,
            "bound_exceeds_target": (not bound_ok),
            "pass": bool(ok),
            "sensitivity": sens,
        })
    return rows, all_pass


def compute(doc):
    validate(doc)
    for p in doc["points"]:
        p["_stats"] = point_stats(p)
    fit, hold = split_points(doc["points"])
    a, b = fit_line(fit)
    xs = [p["_stats"]["mean"] for p in fit]
    spans = {"lo_x": min(xs), "hi_x": max(xs),
             "span_x": max(xs) - min(xs),
             "lo_mA": min(float(p["reference_mA"]) for p in fit),
             "hi_mA": max(float(p["reference_mA"]) for p in fit)}
    qualified, checks = qualify(fit, spans)
    hrows, hold_pass = holdout_rows(doc, fit, hold, a, b)

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

    refs = sorted([float(p["reference_mA"]) for p in doc["points"]])
    out = {
        "tool": TOOL_ID,
        "provenance": doc["provenance"],
        "status": "DRAFT_NOT_APPROVED",
        "candidate": {
            "offset_uV": offset_uv,
            "gain_uV_per_A": gain_uv_per_a,
            "offset_mV": offset_mv,
            "gain_V_per_A": gain_v_per_a,
            "slope_mA_per_count": a,
            "intercept_mA": b,
            "nominal_mv_per_count_assumed": k,
        },
        "candidate_command_DRAFT_NOT_APPROVED":
            "current_cal apply %d %d CONFIRM_MEASURED" % (offset_uv, gain_uv_per_a),
        "valid_range": {
            "reference_mA": [refs[0], refs[-1]],
            "raw_counts": [spans["lo_x"], spans["hi_x"]],
            "note": "Only inside the measured current, supply, temperature and "
                    "load window of these points. No extrapolation to "
                    "over-current or stall thresholds.",
        },
        "qualification": {"qualified": bool(qualified), "checks": checks,
                          "spans": spans},
        "fit_points": [public_point(p) for p in fit],
        "holdout_points": hrows,
        "holdout_all_pass": bool(hold_pass),
        "method": doc.get("method"),
        "meter": doc.get("meter"),
        "notes": [
            "the mV display uses the nominal 3300/4095 assumption; it is not "
            "proof that VDDA was measured",
            "a pass here qualifies the fitted link only, not the whole "
            "protection chain",
            "status stays DRAFT_NOT_APPROVED until an independent review and "
            "one approval of the concrete field package",
        ],
    }
    if not qualified:
        out["status"] = FAIL
        out["reject_code"] = "QUALIFICATION_FAILED"
        out["reject_detail"] = [c["gate"] for c in checks if not c["pass"]]
    return out


def public_point(p):
    s = p["_stats"]
    return {"id": p["id"], "raw_mean": s["mean"], "n_valid": s["n"],
            "sd_counts": s["sd"], "u_raw_mean_counts": s["u_x"],
            "reference_mA": float(p["reference_mA"]),
            "reference_error_mA": float(p["reference_error_mA"]),
            "reference_source": p["reference_source"],
            "raw_source_file": p["raw_source_file"],
            "raw_source_sha256": p["raw_source_sha256"],
            "seq": [s["seq_first"], s["seq_last"]],
            "tick": [s["tick_first"], s["tick_last"]],
            "read_window_s": s["read_window_s"], "h10_mv": s["h10_mv"],
            "vdd33_mv": s["vdd33_mv"], "temp_c": s["temp_c"]}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_input(path):
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc


# ---------------------------------------------------------------- selftest

def _pt(pid, role, mean, iref, ierr, n=128, sd2=4.0):
    s = mean * n
    sq = (mean * mean + sd2) * n
    return {"id": pid, "role": role, "used_for_fit": role == "fit",
            "reference_source": "METER_IN_SERIES",
            "reference_mA": iref, "reference_error_mA": ierr,
            "raw_sum": s, "raw_sum_sq": sq,
            "raw_min": mean - 20, "raw_max": mean + 20,
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
        "meter": {"model": "UT39A+", "dc_range": "400mA",
                  "resolution": "0.1mA", "error_expression": "0.8%+3"},
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


def _expect_reject(lines, name, fn, want):
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
    ok &= _check_ok(lines, "M7C01-slope", abs(c["slope_mA_per_count"] - 1.2) < 1e-12,
                    c["slope_mA_per_count"])
    ok &= _check_ok(lines, "M7C02-gain",
                    abs(c["gain_V_per_A"] - (3300.0 / 4095.0) / 1.2) < 1e-12,
                    c["gain_V_per_A"])
    ok &= _check_ok(lines, "M7C03-offset",
                    abs(c["offset_mV"] - 1598.2905982905984) < 1e-6, c["offset_mV"])
    ok &= _check_ok(lines, "M7C04-gain-uV", c["gain_uV_per_A"] == 671551,
                    c["gain_uV_per_A"])
    ok &= _check_ok(lines, "M7C05-offset-uV", c["offset_uV"] == 1598291, c["offset_uV"])
    ok &= _check_ok(lines, "M7C06-command-draft",
                    res["candidate_command_DRAFT_NOT_APPROVED"] ==
                    "current_cal apply 1598291 671551 CONFIRM_MEASURED",
                    res["candidate_command_DRAFT_NOT_APPROVED"])
    ok &= _check_ok(lines, "M7C07-status-draft", res["status"] == "DRAFT_NOT_APPROVED",
                    res["status"])
    ok &= _check_ok(lines, "M7C08-qualified", res["qualification"]["qualified"] is True,
                    res["qualification"]["checks"])
    ok &= _check_ok(lines, "M7C09-holdout-pass",
                    res["holdout_all_pass"] is True and
                    res["holdout_points"][0]["abs_error_mA"] < 1e-9,
                    res["holdout_points"])
    ok &= _check_ok(lines, "M7C10-provenance-synthetic",
                    res["provenance"] == "HOST_SYNTHETIC", res["provenance"])

    mut_drop_fit = lambda d: d.__setitem__("points", [d["points"][0], d["points"][2]])
    mut_dup_x = lambda d: d["points"][1].update(_pt("F2", "fit", 2000.0, 140.0, 1.42))
    def mut_zero_slope(d):
        d["points"][1]["reference_mA"] = 20.0
    def mut_neg_slope(d):
        d["points"][1]["reference_mA"] = -140.0
    def mut_nan(d):
        d["points"][0]["reference_mA"] = float("nan")
    def mut_raw_oob(d):
        d["points"][0].update(_pt("F1", "fit", 5000.0, 20.0, 0.5))
    def mut_sat(d):
        d["points"][0]["raw_min"] = 0
    def mut_mean_incon(d):
        d["points"][0]["raw_sum"] = 3000.0 * 128
    def mut_theory_ref(d):
        d["points"][0]["reference_source"] = "IRUN_VREF_THEORY"
    def mut_adapter(d):
        d["points"][0]["reference_source"] = "ADAPTER_TOTAL_CURRENT"
    def mut_disabled_baseline(d):
        d["points"][0]["reference_source"] = "DRIVER_DISABLED_ZERO"
    def mut_no_holdout(d):
        d["points"][2]["role"] = "fit"
        d["points"][2]["used_for_fit"] = True
    def mut_holdout_shared(d):
        d["points"][2]["used_for_fit"] = True
    def mut_extrap(d):
        d["points"][2].update(_pt("H1", "holdout", 2400.0, 80.0, 0.94))
    def mut_low_span(d):
        d["points"][1]["reference_mA"] = 23.0
    def mut_missing(d):
        del d["points"][0]["raw_source_sha256"]
    def mut_incomplete(d):
        d["points"][0]["n_valid"] = 100
    def mut_null(d):
        d["points"][0]["raw_sum"] = None
    def mut_schema(d):
        d["schema"] = "other/1"
    def mut_prov(d):
        d["provenance"] = "BOARD_ACCEPTED"
    def mut_bad_role(d):
        d["points"][0]["role"] = "close_enough"
    def mut_dup_id(d):
        d["points"][1]["id"] = "F1"
    def mut_bound(d):
        d["points"][2]["reference_error_mA"] = 51.2
    def mut_field_no_method(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": None, "connection": "",
                            "covers_all_measured_current": True})
    def mut_field_partial(d):
        d["provenance"] = "FIELD_MEASURED"
        d["method"].update({"branch": "VM leg", "connection": "series",
                            "fuse_rating": "declared",
                            "series_voltage_drop_mV": 12.0,
                            "surcharge_handling": "declared", "environment": "25C",
                            "covers_all_measured_current": False,
                            "uncovered_branch_error_mA": None})

    negatives = [
        ("M7C11-insufficient-fit", mut_drop_fit, "INSUFFICIENT_FIT_POINTS"),
        ("M7C12-duplicate-x", mut_dup_x, "DUPLICATE_X"),
        ("M7C13-zero-slope", mut_zero_slope, "NON_POSITIVE_SLOPE"),
        ("M7C14-negative-slope", mut_neg_slope, "NON_POSITIVE_SLOPE"),
        ("M7C15-nan-reference", mut_nan, "NON_FINITE_VALUE"),
        ("M7C16-raw-out-of-range", mut_raw_oob, "RAW_OUT_OF_RANGE"),
        ("M7C17-saturated-endpoint", mut_sat, "SATURATED_ENDPOINT"),
        ("M7C18-raw-mean-inconsistent", mut_mean_incon, "RAW_MEAN_INCONSISTENT"),
        ("M7C19-theory-reference", mut_theory_ref, "NO_INDEPENDENT_REFERENCE"),
        ("M7C20-adapter-total", mut_adapter, "NO_INDEPENDENT_REFERENCE"),
        ("M7C21-disabled-baseline", mut_disabled_baseline, "NO_INDEPENDENT_REFERENCE"),
        ("M7C22-missing-heldout", mut_no_holdout, "INSUFFICIENT_HOLDOUT_POINTS"),
        ("M7C23-heldout-not-independent", mut_holdout_shared, "HOLDOUT_NOT_INDEPENDENT"),
        ("M7C24-extrapolating-heldout", mut_extrap, "EXTRAPOLATION_HOLDOUT"),
        ("M7C25-missing-field", mut_missing, "MISSING_FIELD"),
        ("M7C26-incomplete-sample", mut_incomplete, "INCOMPLETE_SAMPLE"),
        ("M7C27-null-not-zeroed", mut_null, "MISSING_FIELD"),
        ("M7C28-schema", mut_schema, "SCHEMA_MISMATCH"),
        ("M7C29-provenance", mut_prov, "UNKNOWN_PROVENANCE"),
        ("M7C30-bad-role", mut_bad_role, "BAD_ROLE"),
        ("M7C31-duplicate-id", mut_dup_id, "DUPLICATE_POINT_ID"),
        ("M7C32-field-method-missing", mut_field_no_method, "METHOD_NOT_READY"),
        ("M7C33-field-partial-branch", mut_field_partial, "METHOD_NOT_READY"),
    ]
    for name, m, want in negatives:
        ok &= _expect_reject(lines, name, lambda m=m: compute(synthetic_doc(m)), want)

    # Low reference span must fail the fixed qualification gate, not be
    # rescued by widening the reference range afterwards.
    low = compute(synthetic_doc(mut_low_span))
    ok &= _check_ok(lines, "M7C34-low-reference-span-rejected",
                    low["status"] == FAIL and
                    low["reject_detail"] == ["REF_SPAN_GT_10x_SUM_REF_ERROR_BOUNDS"],
                    (low["status"], low.get("reject_detail")))

    # Error bound bigger than the target is not a pass.
    be = compute(synthetic_doc(mut_bound))
    row = be["holdout_points"][0]
    ok &= _check_ok(lines, "M7C35-bound-exceeds-target-not-pass",
                    be["qualification"]["qualified"] is True and
                    row["bound_exceeds_target"] is True and
                    be["holdout_all_pass"] is False,
                    (row["combined_error_bound_mA"], row["target_mA"]))

    # No device or transport module may be reachable from this tool.
    src = open(os.path.abspath(__file__), "r", encoding="utf-8").read()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    banned = {"serial", "usb", "usb1", "pyocd", "ctypes", "socket", "subprocess"}
    ok &= _check_ok(lines, "M7C36-no-transport-or-device-imports",
                    not (mods & banned), sorted(mods))

    ok &= _check_ok(lines, "M7C37-reject-codes-enumerated",
                    all(code in REJECT_CODES for _, _, code in negatives) and
                    "QUALIFICATION_FAILED" in REJECT_CODES, None)

    npass = sum(1 for ln in lines if ln.startswith("PASS"))
    for ln in lines:
        print(ln)
    print("SUMMARY cases=%d pass=%d fail=%d" % (len(lines), npass, len(lines) - npass))
    print("M7 CALCALC SELFTEST: %s" % ("PASS" if ok else "FAIL"))
    return ok, lines


def main(argv):
    ap = argparse.ArgumentParser(prog="m7_calcalc.py")
    ap.add_argument("--input")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--hash")
    ap.add_argument("--emit-example", metavar="PATH",
                    help="write a HOST_SYNTHETIC example input; not field data")
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
        result = compute(load_input(args.input))
    except Reject as exc:
        print("REJECT %s" % exc)
        return 1
    text = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
