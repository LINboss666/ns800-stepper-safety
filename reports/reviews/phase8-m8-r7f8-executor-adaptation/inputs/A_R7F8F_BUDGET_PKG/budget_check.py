#!/usr/bin/env python3
"""R7F8F A预算独立复算；只读JSON、算时间/散列，不导入执行器、不碰设备。"""
from pathlib import Path
import argparse
import hashlib
import json
import math


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check(table_path, plan_path, rows_path):
    table = json.loads(table_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    reference = plan["stages"][0]["steps"]
    steps = table["steps"]
    limits = table["limits"]
    checks = []

    def ck(name, passed, detail=None):
        checks.append(dict(name=name, passed=bool(passed), detail=detail))

    ck("BASE_PLAN_IDENTITY", digest(plan_path) == table["reference_plan_sha256"])
    ck("BASE_ROWS_IDENTITY", digest(rows_path) == table["reference_rows_sha256"])
    ck("SAME_38_COMMANDS_AND_ORDER", len(steps) == len(reference) == 38 and all(
        (x["index"], x["as"], x["cmd"]) == (i, y["as"], y["cmd"])
        for i, (x, y) in enumerate(zip(steps, reference))))
    ck("FINITE_POSITIVE_WINDOWS", all(not isinstance(x["step_total_seconds"], bool)
        and math.isfinite(x["step_total_seconds"]) and x["step_total_seconds"] > 0
        for x in steps))
    ck("POLL_WINDOWS_UNCHANGED", all(
        x["step_total_seconds"] == y["poll"]["until_seconds"]
        for x, y in zip(steps, reference) if y.get("poll")))
    time = 0.0
    timeline = []
    by_as = {}
    for x in steps:
        end = time + x["step_total_seconds"]
        y = dict(x, start_s=time, end_s=end)
        timeline.append(y)
        by_as[x["as"]] = y
        time = end
    start = by_as["start"]["start_s"]
    arm = by_as["arm"]["start_s"]
    stop_elapsed = by_as["stop"]["start_s"] - start
    zero_elapsed = by_as["stopped"]["end_s"] - start
    arm_elapsed = by_as["mot_disabled"]["end_s"] - arm
    post_zero = time - by_as["stopped"]["end_s"]
    whole_with_cap = start + limits["motion_to_zero_cap_s"] + post_zero + limits["cleanup_reserve_s"]
    arm_with_cap = start - arm + limits["motion_to_zero_cap_s"] + (
        by_as["mot_disabled"]["end_s"] - by_as["stopped"]["end_s"])
    counts = {k: sum(x["phase"] == k for x in steps)
              for k in ["pre_start", "motion_active", "post_zero"]}
    ck("SEGMENTS_COVER_ONCE", counts == {"pre_start": 19, "motion_active": 11, "post_zero": 8})
    ck("STOP_REQUEST_WITHIN_8", stop_elapsed <= limits["normal_stop_send_by_s"], stop_elapsed)
    ck("ZERO_CONFIRM_WITHIN_12", zero_elapsed <= limits["motion_to_zero_cap_s"], zero_elapsed)
    ck("DISABLED_WITHIN_ARM_18", arm_elapsed <= limits["arm_to_disabled_cap_s"], arm_elapsed)
    ck("BUSINESS_FITS_83", time <= limits["business_absolute_s"], time)
    ck("BUSINESS_PLUS_RESERVE_FITS_90", time + limits["cleanup_reserve_s"] <= limits["session_total_s"])
    ck("FULL_MOTION_CAP_PLUS_POST_ZERO_FITS_90", whole_with_cap <= limits["session_total_s"], whole_with_cap)
    ck("ARM_WITH_FULL_MOTION_CAP_FITS_18", arm_with_cap <= limits["arm_to_disabled_cap_s"], arm_with_cap)
    ck("CLEANUP_FIVE_WINDOWS_FIT_7", 5 * 1.2 <= limits["cleanup_reserve_s"])
    ck("ZERO_DOES_NOT_RELEASE_ARM", table["motion_end_preserves_arm_deadline"] is True)
    ck("ORIGINAL_FIXED_LIMITS", (
        limits["motion_to_zero_cap_s"], limits["arm_to_disabled_cap_s"],
        limits["session_total_s"], limits["cleanup_reserve_s"],
        limits["port_open"], limits["motor_run"]) == (12, 18, 90, 7, 1, 1))
    wire = []
    for x, y in zip(steps, reference):
        key = y.get("key") or y["cmd"]
        source_shape = (rows.get("commands", {}).get(key) or {}).get("healthy")
        if isinstance(source_shape, list) and all(isinstance(z, str) for z in source_shape):
            data = (y["cmd"] + "\r\n" + "\r\n".join(source_shape) + "\r\nmsh >").encode("utf-8")
            lower = len(data) * 10 / 115200
            wire.append(dict(as_name=x["as"], key=key, source_shape_bytes=len(data),
                             wire_lower_bound_s=round(lower, 6),
                             step_total_seconds=x["step_total_seconds"]))
    ck("SOURCE_SHAPE_WIRE_LOWER_BOUND", len(wire) == 38 and all(
        z["wire_lower_bound_s"] < z["step_total_seconds"] for z in wire),
        "Source-shape lower bound only; not a measured complete response or Windows latency guarantee")
    return dict(scope="Arithmetic/reference byte identities only; not executor/offline_gate/hardware validation",
        based_on_commit=table["based_on_commit"], hardware_actions=0,
        actual_executor_changed=False, field_authorization_received=False,
        checks=checks, total=len(checks), fail=sum(not x["passed"] for x in checks),
        phase_counts=counts,
        bounds=dict(pre_start_s=start, stop_request_after_start_s=stop_elapsed,
                    zero_confirm_after_start_s=zero_elapsed, arm_to_disabled_s=arm_elapsed,
                    post_zero_s=post_zero, business_total_s=time,
                    total_with_cleanup_s=time + limits["cleanup_reserve_s"],
                    conservative_total_with_full_motion_cap_s=whole_with_cap,
                    conservative_arm_with_full_motion_cap_s=arm_with_cap),
        timeline=timeline, source_shape_wire_bounds=wire)


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", type=Path, default=base / "step_total_windows.json")
    ap.add_argument("--plan", type=Path, default=base / "reference_plan_6f2b12f.json")
    ap.add_argument("--rows", type=Path, default=base / "reference_rows_6f2b12f.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = check(args.table, args.plan, args.rows)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({k: result[k] for k in ["scope", "total", "fail", "bounds"]}, ensure_ascii=False))
    raise SystemExit(bool(result["fail"]))
