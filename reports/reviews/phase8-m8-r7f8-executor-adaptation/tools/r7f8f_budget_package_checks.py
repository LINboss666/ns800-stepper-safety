# -*- coding: utf-8 -*-
"""R7F8F 预算裁定包(F13): A 包身份逐条复算 + 独立跑 A 的 budget_check + 三条等价突变负控。

三条判据来源分得很清:
  * A 包内的 9 件按 A 自己那份 `SHA256SUMS.txt`(不列自身)当场复算磁盘字节;
  * `budget_check.py` 是 **A 写的**检查器, 我用仓库里的盘上件当输入真跑一次, 再与 A 的
    `PROOF_budget.json` 对账(总条数/FAIL/逐条 passed/bounds 全等) => 这条证的是"我这台机器上
    的这份件算出同样结果", 不证执行器已改好(A 自己在 README 第 3 条就这么声明);
  * A 的三条元负控原文照录, 另跑**我这边的**三条等价突变(不声称与 A 同构造), 证这台机器上的
    检查器不是恒真。
用法: python tools/r7f8f_budget_package_checks.py --repo D:/ns800
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
PKG = ROUND + "/inputs/A_R7F8F_BUDGET_PKG"
MANIFEST = "SHA256SUMS.txt"
OUT = []
FAILS = []


def note(name, ok, detail=""):
    v = "PASS" if ok else "FAIL"
    OUT.append("%s|%s|%s" % (name, v, detail))
    if not ok:
        FAILS.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run_check(repo, table, plan, rows, out_path):
    """真跑 A 的 budget_check.py, 返回 (rc, stdout 末行, 落盘 json)。

    A 的工具在 `raise SystemExit(bool(fail))` **之前**就把 `--out` 写完了, 所以判红的那几次
    同样有完整 JSON 可读 —— 只在 rc=0 时才取结果会把"门变红"错当成"没跑成"(第一版就栽在这里)。
    """
    argv = [sys.executable, str(repo / PKG / "budget_check.py"),
            "--table", str(table), "--plan", str(plan), "--rows", str(rows),
            "--out", str(out_path)]
    p = subprocess.run(argv, capture_output=True,
                       env=dict(__import__("os").environ, PYTHONIOENCODING="utf-8"))
    text = p.stdout.decode("utf-8", "replace").strip().splitlines()
    js = None
    if pathlib.Path(out_path).exists():
        js = json.loads(pathlib.Path(out_path).read_text(encoding="utf-8"))
    return p.returncode, (text[-1] if text else ""), js


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path,
                    help="仓外工作目录(A 的检查器中间件写这里)")
    a = ap.parse_args()
    repo = a.repo.resolve()
    work = a.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    pkg = repo / PKG

    # ---------------------------------------------------------------- 1) 包身份
    man = (pkg / MANIFEST).read_text(encoding="utf-8").splitlines()
    entries = []
    for line in man:
        if not line.strip():
            continue
        h, rel = line.split("  ", 1)
        entries.append((h.strip(), rel.strip()))
    disk = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*")
                  if p.is_file() and p.name != MANIFEST)
    bad = [(rel, sha((pkg / rel).read_bytes())[:12]) for h, rel in entries
           if not (pkg / rel).exists() or sha((pkg / rel).read_bytes()) != h]
    extra = [x for x in disk if x not in [rel for _, rel in entries]]
    note("A_MANIFEST_ITEMS_RECOMPUTE", not bad and not extra,
         "A 清单登记 %d 件(清单不列自身, 与 A 包上一版同一口径); 除清单外磁盘常规文件 %d 件; "
         "逐条复算不符=%d 未列=%s" % (len(entries), len(disk), len(bad), extra))
    zip_note = "zip 原件 42428 B / sha256 87c9d5ea76b3c008…(操作者会话附件, 不入仓)"
    info = ["PKG_ITEM|%s|%d|%s" % (rel, (pkg / rel).stat().st_size,
                                   sha((pkg / rel).read_bytes())[:16])
            for h, rel in entries]

    # 基准件必须逐字节等于我这轮 tip 6f2b12f 的现场包
    ref_plan = pkg / "reference_plan_6f2b12f.json"
    ref_rows = pkg / "reference_rows_6f2b12f.json"
    mine_plan = repo / (ROUND + "/field/plan_r7f8f_2369_real.json")
    mine_rows = repo / (ROUND + "/field/rows_r7f8f_real.json")
    # 基准件必须逐字节等于**裁定钉住的那个提交(6f2b12f)**里的现场包。
    # 上一版这里拿工作树文件比 —— 裁定落地后计划必然改动, 这条就变成了"改一下就假红"的活体
    # 依赖, 证的根本不是基准件的身份。改成按 Git blob 复算(与本仓其它身份判据同一手法),
    # 并把"工作树已经变了"这件事本身写进回执, 不藏。
    def blob(rev, rel):
        r = subprocess.run(["git", "-C", str(repo), "show", "%s:%s" % (rev, rel)],
                           capture_output=True)
        return (r.stdout if r.returncode == 0 else None), \
            r.stderr.decode("utf-8", "replace")[:160]

    full, e0 = (None, "")
    rp = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "6f2b12f^{commit}"],
                        capture_output=True)
    tip = rp.stdout.decode("ascii", "replace").strip() if rp.returncode == 0 else ""
    rel_plan = ROUND + "/field/plan_r7f8f_2369_real.json"
    rel_rows = ROUND + "/field/rows_r7f8f_real.json"
    b_plan, ep = blob(tip, rel_plan) if tip else (None, "提交 6f2b12f 解析失败")
    b_rows, er = blob(tip, rel_rows) if tip else (None, "提交 6f2b12f 解析失败")
    wt_plan_same = sha(ref_plan.read_bytes()) == sha(mine_plan.read_bytes())
    wt_rows_same = sha(ref_rows.read_bytes()) == sha(mine_rows.read_bytes())
    note("REFERENCE_BYTES_EQUAL_6f2b12F_BLOB",
         bool(tip) and b_plan is not None and b_rows is not None
         and sha(ref_plan.read_bytes()) == sha(b_plan)
         and sha(ref_rows.read_bytes()) == sha(b_rows),
         "A 的基准 plan %d B/%s… 与 6f2b12f(完整 %s)的 Git blob(%s) 逐字节相等; rows %s… 同理(%s"
         "%s) => 裁定钉的是**裁定前那个提交**, 不是当前工作树。工作树现状: plan 与基准相等=%s "
         "(裁定落地后已改动), rows 与基准相等=%s。上一版拿工作树比, 计划一改就假红, 本轮改为按 "
         "blob 复算。" % (ref_plan.stat().st_size, sha(ref_plan.read_bytes())[:12],
                          tip or "-", sha(b_plan)[:12] if b_plan else "-",
                          sha(ref_rows.read_bytes())[:12], ep, er, wt_plan_same, wt_rows_same))

    # ---------------------------------------------------------------- 2) 窗口表对齐
    tbl = json.loads((pkg / "step_total_windows.json").read_text(encoding="utf-8"))
    plan = json.loads(ref_plan.read_text(encoding="utf-8"))
    steps = plan["stages"][0]["steps"]
    mism = [(t["index"], t["as"], t["cmd"], s.get("as"), s.get("cmd"))
            for t, s in zip(tbl["steps"], steps)
            if (t["as"], t["cmd"]) != (s.get("as"), s.get("cmd"))]
    ph = {}
    for t in tbl["steps"]:
        ph.setdefault(t["phase"], [0, 0.0])
        ph[t["phase"]][0] += 1
        ph[t["phase"]][1] += t["step_total_seconds"]
    note("WINDOW_TABLE_ALIGNS_38_STEPS", len(tbl["steps"]) == len(steps) == 38 and not mism,
         "38 步的 (as, cmd) 与逐条对齐, 错位=%d; 三段 %s => 合计 %.1f s"
         % (len(mism), {k: (v[0], round(v[1], 2)) for k, v in sorted(ph.items())},
            sum(t["step_total_seconds"] for t in tbl["steps"])))
    note("LIMITS_UNCHANGED", (tbl["limits"]["motion_to_zero_cap_s"],
                              tbl["limits"]["arm_to_disabled_cap_s"],
                              tbl["limits"]["session_total_s"],
                              tbl["limits"]["cleanup_reserve_s"],
                              tbl["limits"]["normal_stop_send_by_s"]) == (12, 18, 90, 7, 8)
         and tbl["stop_request_index"] == 28 and tbl["zero_confirm_index"] == 29,
         "四个固定数与 stop 8 s 上限逐字段核过未变(12/18/90/7/8); stop_request_index=28 "
         "zero_confirm_index=29(均 0 基)")
    note("NO_NEGATIVE_OR_ZERO_WINDOWS", all(
        isinstance(t["step_total_seconds"], float) and 0 < t["step_total_seconds"] <= 13.0
        for t in tbl["steps"]),
         "全部为正的浮点且 <= 13.0 s; 0.5 s 的小数在表里原样保留(不是整数化后的值)")

    # ---------------------------------------------------------------- 3) 真跑 A 的检查器
    mine = work / "budget_check_mine.json"
    rc, last, js = run_check(repo, pkg / "step_total_windows.json", ref_plan, ref_rows, mine)
    a_proof = json.loads((pkg / "PROOF_budget.json").read_text(encoding="utf-8"))
    same_verdicts = js is not None and [
        (c["name"], c["passed"]) for c in a_proof["checks"]] == [
        (c["name"], c["passed"]) for c in js["checks"]]
    note("A_BUDGET_CHECK_RAN_ON_MY_DISK_ITEMS", rc == 0 and js is not None
         and js["total"] == 17 and js["fail"] == 0,
         "A 的检查器 rc=%s, 自报 %s" % (rc, last[:220]))
    note("BOUNDS_MATCH_A_PROOF_EXACTLY", same_verdicts and js is not None
         and js["bounds"] == a_proof["bounds"],
         "逐条 passed 序列相等=%s; bounds 全等=%s(%s)"
         % (same_verdicts, js["bounds"] == a_proof["bounds"] if js else False,
            json.dumps(js["bounds"], ensure_ascii=False)[:200] if js else "-"))
    neg = json.loads((pkg / "PROOF_arithmetic_negative_controls.json")
                     .read_text(encoding="utf-8"))
    note("A_NEGATIVE_CONTROLS_RECORDED", neg["total"] == 3 and neg["fail"] == 0
         and all(c["detected"] for c in neg["controls"]),
         "A 的三条元负控原文照录: %s"
         % " / ".join("%s=>%s" % (c["case"], c["expected_failure"]) for c in neg["controls"]))

    # 我这边三条等价突变(不声称与 A 同构造): 门必须具名变红
    cases = [("MUT_double_every_window_expect_business_red",
              lambda d: [s.update(step_total_seconds=s["step_total_seconds"] * 2)
                         for s in d["steps"]], "BUSINESS_FITS_83"),
             ("MUT_cleanup_reserve_four_expect_five_send_window_red",
              lambda d: d["limits"].update(cleanup_reserve_s=4),
              "CLEANUP_FIVE_WINDOWS_FIT_7"),
             ("MUT_rename_one_command_expect_identity_red",
              lambda d: d["steps"][28].update(cmd="motor_stop_again"),
              "SAME_38_COMMANDS_AND_ORDER")]
    for name, mutate, expect in cases:
        d = copy.deepcopy(tbl)
        mutate(d)
        p = work / ("mut_%s.json" % name.lower())
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8", newline="\n")
        rc_m, _, js_m = run_check(repo, p, ref_plan, ref_rows,
                                  work / ("mut_out_%s.json" % name.lower()))
        fired = [c["name"] for c in (js_m["checks"] if js_m else []) if not c["passed"]]
        # 突变后 rc=1 是**期望行为**(A 的工具用退出码报 fail), 判的是"具名那条红了没有"
        note(name, bool(fired) and expect in fired,
             "突变后 A 检查器 rc=%s(非零符合它的退出码约定), 未通过项=%s, 其中含 %s=%s"
             % (rc_m, fired or "(无 => 这条门是死的)", expect, expect in fired))

    text = "\n".join(OUT + info + ["R7F8F_BUDGET_PKG_SUMMARY|判据=%d|FAIL=%d|%s"
                                  % (len(OUT), len(FAILS), ",".join(FAILS) or "none")]) + "\n"
    (repo / ROUND / "proof").mkdir(parents=True, exist_ok=True)
    (repo / ROUND / "proof/r7f8f_budget_package.txt").write_text(text, encoding="utf-8",
                                                                 newline="\n")
    doc = {"receipt": "budget_package", "round": "M8-R7F8F", "package_dir": PKG,
           "a_manifest_entries": len(entries), "disk_files": len(disk),
           "zip_reference": zip_note,
           "gates": [{"name": l.split("|", 1)[0], "verdict": l.split("|", 2)[1],
                      "detail": l.split("|", 2)[2]} for l in OUT],
           "my_budget_check": {"rc": rc, "self_report": last,
                               "bounds": (js or {}).get("bounds"),
                               "total": (js or {}).get("total"),
                               "fail": (js or {}).get("fail")},
           "a_proof_reference": {"total": a_proof["total"], "fail": a_proof["fail"],
                                 "negative_controls": neg["total"]},
           "summary": {"checks": len(OUT), "fail": len(FAILS), "failed_names": FAILS},
           "not_claimed": {"executor_changed_by_this_receipt": False,
                           "offline_gate_covered_by_this_receipt": False,
                           "hardware_actions": 0,
                           "polarity_measured": False, "core_protection_verified": False}}
    (repo / ROUND / "proof/r7f8f_budget_package.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
