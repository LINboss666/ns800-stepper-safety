#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从已归档的现场台账复算 run_record §15.1 逐场停点表与 §15.4 轮级额度累计。

为什么要有这个文件: 轮级越权(apply 14/2 等)不能只写在正文里由 Agent 自证, 必须能被第三方
用同一份原始台账复算出来。本脚本**只读** `field-*/dispatch_ledger.jsonl` 与 `field-*/S_*/ledger.jsonl`,
不做任何判定, 也不改任何字节。

用法: python round_quota_and_stops.py            # 从仓库根跑
      python round_quota_and_stops.py --write     # 同时把输出落到 host/round_quota_and_stops.txt
"""
import collections
import glob
import json
import os
import sys

FIELD = "reports/reviews/phase8-m8-idle-stack-r4/field"
OUT = "reports/reviews/phase8-m8-idle-stack-r4/host/round_quota_and_stops.txt"


def run_rows():
    rows = []
    for d in sorted(glob.glob(os.path.join(FIELD, "field-2026110*Z"))):
        dl = os.path.join(d, "dispatch_ledger.jsonl")
        if not os.path.exists(dl):
            continue
        used = {}
        for line in open(dl, encoding="utf-8"):
            ev = json.loads(line)
            if ev.get("kind") == "DISPATCH_DONE":
                used = ev.get("quota_used", {})
        sessions = []
        for sd in sorted(glob.glob(os.path.join(d, "S_*"))):
            nrx = len([x for x in os.listdir(sd) if x.endswith(".rx.bin")])
            lg = os.path.join(sd, "ledger.jsonl")
            stop = ""
            if os.path.exists(lg):
                evs = [json.loads(l) for l in open(lg, encoding="utf-8") if l.strip()]
                hit = [e for e in evs if e.get("kind") in ("SESSION_STOP", "SESSION_OK")]
                if hit:
                    e = hit[-1]
                    stop = "%s:%s" % (e["kind"], (e.get("detail") or e.get("code") or ""))[:120]
            sessions.append("%s[%d rx]%s" % (os.path.basename(sd), nrx, (" " + stop) if stop else " -"))
        rows.append((os.path.basename(d), sessions, used))
    return rows


def main():
    rows = run_rows()
    cum = collections.Counter()
    plan = json.load(open(os.path.join(FIELD, "plan_core_r5c.json"), encoding="utf-8"))["quota"]
    out = ["# 复算自 field-*/dispatch_ledger.jsonl 与 field-*/S_*/ledger.jsonl(只读, 无判定)",
           "# 包内额度列取自 plan_core_r5c.json 的 quota(一次批准的边界被写进包的那组数字)",
           "# 口径提醒: OVER_PLAN 是\"累计 vs R5-C 包额度\"的机械比较, 其中 download/readback/explicit_reset",
           "#   各 1 次来自 R4 包(field-20261101T142722Z, 那份包的 quota 是各 1), 与被 R5-C 归零不是同一份账;",
           "#   真正越权的是 tmc_config_apply/irq_polarity_prep/port_open/diag_profile_zero —— 两次批准都限得更死。", ""]
    for name, sessions, used in rows:
        out.append("%s | %s | %s" % (name, " ;; ".join(sessions),
                                     json.dumps(used, sort_keys=True)))
        for k, v in used.items():
            cum[k] += v
    out.append("")
    out.append("RUNS=%d" % len(rows))
    out.append("CUMULATIVE=%s" % json.dumps(dict(sorted(cum.items())), sort_keys=True))
    out.append("PLAN_QUOTA=%s" % json.dumps(plan, sort_keys=True))
    out.append("OVER_PLAN=%s" % json.dumps({k: {"cum": v, "plan": plan.get(k), "over": v - plan.get(k, 0)}
                                           for k, v in sorted(cum.items())
                                           if v > plan.get(k, 0)}, sort_keys=True))
    out.append("UNUSED=%s" % json.dumps(sorted(k for k in plan if cum.get(k, 0) == 0), sort_keys=True))
    text = "\n".join(out) + "\n"
    sys.stdout.write(text.encode("utf-8", "replace").decode("utf-8"))
    if "--write" in sys.argv[1:]:
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        sys.stderr.write("WROTE %s (%d B)\n" % (OUT, len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
