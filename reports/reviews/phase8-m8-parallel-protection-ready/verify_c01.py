"""C-01 独立验证: 用 A 当前工作树的真实计划 + 真实执行器 assert_step 驱动正反例。
仓外脚本, 不修改仓库任何文件, 不构建, 不接触设备。"""
import copy
import hashlib
import io
import json
import os
import subprocess
import sys

FIELD = r"D:\ns800\reports\reviews\phase8-m8-relative-protection-r3\field"
OUT = r"C:\Users\LMX\AppData\Local\Temp\c01_verify\results_local.json"
sys.path.insert(0, FIELD)
import m8r3_serial_chain as m  # noqa: E402


class Led(object):
    def __init__(self):
        self.events = []

    def ev(self, name, **kw):
        self.events.append((name, kw))


def find_learn_steps(node, path="$"):
    found = []
    if isinstance(node, dict):
        cmd = node.get("cmd")
        if isinstance(cmd, str) and "diag_profile learn" in cmd:
            found.append((path, node))
        for k, v in node.items():
            found.extend(find_learn_steps(v, path + "." + str(k)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(find_learn_steps(v, path + "[%d]" % i))
    return found


def sha(p):
    return hashlib.sha256(io.open(p, "rb").read()).hexdigest()


def row(adc_n, sg_n, nz=0, alo=2000, ahi=2000, slo=500, shi=500, sf=4, sl=11):
    return ("[DIAG] learn OK adc[%d..%d]n%d nz%d sg[%d..%d]n%d seq%d..%d"
            % (alo, ahi, adc_n, nz, slo, shi, sg_n, sf, sl))


def run(step, rows):
    led = Led()
    res = m.assert_step(step, rows, "learn_probe", led, raw=None)
    return res, led


def cases(step):
    """(名称, 响应行, 期望 code)。期望由源码语义推出, 不是照着实现凑的。"""
    ok = "OK"
    return [
        ("healthy_adc68_sg8", [row(68, 8)], ok),
        ("boundary_adc8_sg8", [row(8, 8)], ok),
        ("boundary_adc8_sg68", [row(8, 68)], ok),
        ("large_adc100_sg79", [row(100, 79)], ok),
        ("below_adc7_sg8", [row(7, 8)], "FIELD_VALUE_BELOW_MIN"),
        ("below_adc68_sg7", [row(68, 7)], "FIELD_VALUE_BELOW_MIN"),
        ("below_both_7", [row(7, 7)], "FIELD_VALUE_BELOW_MIN"),
        ("no_success_row_expectation_layer", ["[DIAG] zero OK n=32"], "EXPECTATION_NOT_MET"),
        ("malformed_row_shape_strict", ["[DIAG] learn OK adc[1..2]n8"],
         "FIELD_NOT_EXACTLY_ONE"),
        ("duplicate_success_row", [row(68, 8), row(68, 8)], "FIELD_NOT_EXACTLY_ONE"),
        ("business_failed", ["[DIAG] learn FAILED timeout 12000ms"], "BUSINESS_FAILED"),
        ("business_refused", ["msh: command not supported"], "BUSINESS_FAILED"),
    ]


def mutation_cases(step):
    """元负控: 证明判据真的在 must_min 上起作用, 而不是碰巧绿。"""
    out = []
    hi = copy.deepcopy(step)
    hi["expect_values"][0]["must_min"]["adc_n"] = 9
    out.append(("mutation_min9_rejects_adc8", hi, [row(8, 8)], "FIELD_VALUE_BELOW_MIN"))
    off = copy.deepcopy(step)
    off["expect_values"][0]["must_min"] = {}
    off["expect_values"][0].pop("must", None)
    out.append(("mutation_gate_removed_admits_adc7", off, [row(7, 8)], "OK"))
    ghost = copy.deepcopy(step)
    ghost["expect_values"][0]["must_min"] = {"adc_n_nope": 1}
    out.append(("mutation_absent_group_named_reject", ghost, [row(68, 8)],
                "FIELD_VALUE_MISMATCH"))
    exact = copy.deepcopy(step)
    exact["expect_values"][0]["must"] = {"adc_n": "8"}
    out.append(("regression_exact8_rejects_adc68", exact, [row(68, 8)],
                "FIELD_VALUE_MISMATCH"))
    return out


def main():
    report = {"repo_head": subprocess.check_output(
        ["git", "-C", r"D:\ns800", "rev-parse", "HEAD"], text=True).strip(),
        "executor_sha256": sha(os.path.join(FIELD, "m8r3_serial_chain.py")),
        "plans": [], "device_actions": 0, "builds": 0}
    total = met = 0
    report["baseline_before_fix"] = []
    for fn in ("plan.json", "plan_continuation_s2.json"):
        # 修前基线: 直接取 48204fe 的已提交字节, 证明同一份健康响应当时会被误拒。
        raw = subprocess.check_output(
            ["git", "-C", r"D:\ns800", "cat-file", "blob", "48204fe:"
             + "reports/reviews/phase8-m8-relative-protection-r3/field/" + fn])
        bplan = json.loads(raw.decode("utf-8"))
        for bpath, bst in find_learn_steps(bplan):
            bspec = bst["expect_values"][0]
            bres, _ = run(bst, [row(68, 8)])
            report["baseline_before_fix"].append({
                "file": fn, "must": bspec.get("must"), "must_min": bspec.get("must_min"),
                "adc68_sg8_verdict": bres.get("verdict"),
                "adc68_sg8_code": bres.get("code"),
                "defect_reproduced": bres.get("code") == "FIELD_VALUE_MISMATCH"})
    for fn in ("plan.json", "plan_continuation_s2.json"):
        p = os.path.join(FIELD, fn)
        plan = json.load(io.open(p, encoding="utf-8"))
        steps = find_learn_steps(plan)
        entry = {"file": fn, "sha256": sha(p), "learn_steps_found": len(steps),
                 "criteria": [], "checks": []}
        for path, st in steps:
            spec = st["expect_values"][0]
            entry["criteria"].append({
                "path": path, "cmd": st["cmd"], "must": spec.get("must"),
                "must_min": spec.get("must_min"),
                "row_unchanged": bool(spec["row"].count("(?P<adc_n>\\d+)"))})
            batch = [(n, st, r, e) for (n, r, e) in cases(st)] + mutation_cases(st)
            for name, step_used, rows, expected in batch:
                res, led = run(step_used, rows)
                got = res.get("code") if res.get("verdict") != "OK" else "OK"
                total += 1
                ok = (got == expected)
                if ok:
                    met += 1
                entry["checks"].append({"name": name, "expected": expected,
                                        "got": got, "verdict": res.get("verdict"),
                                        "detail": {k: v for k, v in res.items()
                                                   if k in ("below_min", "count",
                                                            "absent_groups", "wrong",
                                                            "lines", "missing")},
                                        "ok": ok})
        report["plans"].append(entry)
    report["summary"] = {"checks_total": total, "expectations_met": met,
                         "all_met": (total == met and total > 0)}
    io.open(OUT, "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["summary"], ensure_ascii=True))
    bad = [(e["file"], c["name"], c["expected"], c["got"])
           for e in report["plans"] for c in e["checks"] if not c["ok"]]
    print("FAILED_CASES=%d" % len(bad))
    for b in bad:
        print("  " + json.dumps(b, ensure_ascii=True))


if __name__ == "__main__":
    main()
