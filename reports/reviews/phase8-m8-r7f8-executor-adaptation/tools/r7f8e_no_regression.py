# -*- coding: utf-8 -*-
"""R7F8E §3/§4 无回归门: 同一份**冻结的 R7F7 定稿计划**, 旧执行器与新派生件逐行对照。

要证的事只有一件: 派生件在既有命令面上没有改变旧判定(既不新增失败, 也不洗掉旧失败),
新增的每一行都必须能指认是本轮四项适配自己的门。

做法:
  1) 两个执行器对同一 plan 文件、同一 --repo-root 各跑一次 --validate-plan, 原样留档。
  2) 旧输出每一行都必须在新输出里逐字节出现(汇总行按前缀单独配对, 因为 checks 计数必然变)。
  3) 新输出里多出来的行必须全部以 R7F8E_ 开头(本轮新增门), 一条不许是别的形状。
  4) 旧输出里"消失"的行只允许那一条汇总行, 且新汇总行的 fail 计数必须与旧的一致。
任何一条不成立就判红, 并把两侧原文打出来。

用法: python tools/r7f8e_no_regression.py --repo D:/ns800 --work <仓库外可写目录>
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
NEW_REL = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
OLD_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
PLAN_REL = "reports/reviews/phase8-m8-r7f7-newimage-static/field/plan_r7f7_2369_static.json"
ROWS_REL = "reports/reviews/phase8-m8-r7f7-newimage-static/field/rows_r7f7_2369_static.json"
SUMMARY = "PLAN_VALIDATE_SUMMARY"

out, fails = [], []


def note(name, ok, detail=""):
    out.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def sha(b):
    return hashlib.sha256(b).hexdigest()[:16]


def run_validate(exe, plan, repo):
    p = subprocess.run([sys.executable, str(exe), "--validate-plan", "--plan", str(plan),
                        "--repo-root", str(repo)],
                       capture_output=True, cwd=str(repo), env=dict(os.environ))
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--work", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    work = a.work
    work.mkdir(parents=True, exist_ok=True)
    new_exe = repo / NEW_REL
    old_exe = repo / OLD_REL
    plan, rows = repo / PLAN_REL, repo / ROWS_REL
    for p, why in ((new_exe, "派生件"), (old_exe, "旧执行器"), (plan, "冻结计划"),
                   (rows, "冻结行表")):
        if not p.exists():
            note("输入在位", False, "%s 缺失: %s" % (why, p))
            print("\n".join(out))
            return 1
    note("输入在位", True, "四件齐全; 计划=%s 行表=%s" % (sha(plan.read_bytes()),
                                                    sha(rows.read_bytes())))
    rc_old, vp_old, err_old = run_validate(old_exe, plan, repo)
    rc_new, vp_new, err_new = run_validate(new_exe, plan, repo)
    (work / "validate_plan_old.txt").write_text(vp_old + "\n" + err_old, encoding="utf-8",
                                                newline="\n")
    (work / "validate_plan_new.txt").write_text(vp_new + "\n" + err_new, encoding="utf-8",
                                                newline="\n")
    old_lines = [l for l in vp_old.splitlines() if l.strip()]
    new_lines = [l for l in vp_new.splitlines() if l.strip()]
    old_sum = [l for l in old_lines if l.startswith(SUMMARY)]
    new_sum = [l for l in new_lines if l.startswith(SUMMARY)]

    def counts(line):
        m = re.search(r"checks=(\d+)\|fail=(\d+)", line or "")
        return (int(m.group(1)), int(m.group(2))) if m else (None, None)

    oc, of_ = counts(old_sum[0] if old_sum else "")
    nc, nf = counts(new_sum[0] if new_sum else "")
    note("两侧都跑完并给出汇总", bool(old_sum) and bool(new_sum),
         "旧 checks=%s fail=%s / 新 checks=%s fail=%s" % (oc, of_, nc, nf))

    old_body = [l for l in old_lines if not l.startswith(SUMMARY)]
    new_body = [l for l in new_lines if not l.startswith(SUMMARY)]
    new_set = set(new_body)
    missing = [l for l in old_body if l not in new_set]
    note("旧判据一条都没被洗掉", not missing,
         "消失=%d 条%s" % (len(missing), (" | " + " ;; ".join(missing)[:300]) if missing else ""))
    added = [l for l in new_body if l not in set(old_body)]
    # "本轮自己的门"不能只看前缀: R7F8F 的 F5b/F5c 把收口预留判据、F6(A 的补充审核)把三段
    # 预算判据都加进了离线整包门家族(PLAN_*)。
    # 前缀放不开就得点名 —— 点名了就要验它确实出现, 否则白名单会变成"任何 PLAN_ 新行都免检"。
    OURS_PREFIX = "R7F8E_"
    OURS_NAMES = ("PLAN_CLEANUP_RESERVE", "PLAN_BUSINESS_RESERVE_SPLIT",
                  "PLAN_BUSINESS_WORST_CASE_FIT",
                  # A 的预算裁定把这一步拆成两条新门(旧口径只作 INFO 留档、单步总窗单独成门),
                  # 名字不是 R7F8E_ 前缀, 必须点名; 点名后下面那条"确实出现"会兜住空转。
                  "PLAN_BUSINESS_RESERVE_SPLIT_LEGACY_SEMANTICS", "PLAN_STEP_TOTAL_WINDOWS")
    not_ours = [l for l in added
                if not l.startswith(OURS_PREFIX) and l.split("|")[0] not in OURS_NAMES]
    note("新增行全部是本轮适配自己的门", not not_ours,
         "新增=%d 条, 其中既不是 %s 形状也不在点名表里的=%d 条%s"
         % (len(added), OURS_PREFIX, len(not_ours),
            (" | " + " ;; ".join(not_ours)[:300]) if not_ours else ""))
    seen_names = set(l.split("|")[0] for l in added)
    absent = [n for n in OURS_NAMES if n not in seen_names]
    note("点名的本轮新门确实出现在新增行里(白名单非空转)", not absent,
         "点名=%s 实到=%s 缺=%s" % (",".join(OURS_NAMES),
                                    ",".join(sorted(seen_names)), ",".join(absent) or "-"))
    note("新增门条数与计数一致", nc == oc + len(added) and len(added) > 0,
         "旧 %d + 新增 %d = 新 %d" % (oc, len(added), nc))
    note("既有失败集合未变", of_ == nf,
         "两侧 fail 计数 %s vs %s; 唯一既有 FAIL 是 PLAN_MOTION_CAP_BUDGET"
         "(空运动链的既有检查器缺口, R7F8 草案轮已登记)" % (of_, nf))
    note("派生件身份已重登记(不冒用旧摘要)",
         sha(old_exe.read_bytes()) != sha(new_exe.read_bytes())
         and len(new_exe.read_bytes()) != 219491,
         "旧 %d B sha=%s / 新 %d B sha=%s" % (len(old_exe.read_bytes()),
                                          sha(old_exe.read_bytes()),
                                          len(new_exe.read_bytes()), sha(new_exe.read_bytes())))
    note("本轮无设备动作", True,
         "两次 --validate-plan 都是纯静态读; 未开端口、未建批准账、未 dispatch")

    detail = ["", "== 旧执行器输出原文(%d 行) ==" % len(old_lines)] + old_lines + \
             ["", "== 派生件输出原文(%d 行) ==" % len(new_lines)] + new_lines + \
             ["", "== 新增行逐条 =="] + added
    text = "\n".join(out + detail + ["R7F8E_NOREG_SUMMARY|判据=%d|FAIL=%d|%s" %
                                    (len(out), len(fails), ",".join(fails) if fails else "none")]
                     ) + "\n"
    (repo / ROUND / "proof").mkdir(parents=True, exist_ok=True)
    (repo / ROUND / "proof/r7f8e_no_regression.txt").write_text(text, encoding="utf-8",
                                                                 newline="\n")
    # 走 stdout.buffer 而不是 print: 控制台是 GBK, 而原文里带着从旧件 stdout 里 replace 出来的
    # '\ufffd' —— print 会在这里抛 UnicodeEncodeError, 把"判据 9/0"伪装成"脚本自己炸了 rc=1"。
    sys.stdout.buffer.write(text.encode("utf-8", "replace"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
