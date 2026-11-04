# -*- coding: utf-8 -*-
"""R7F5 登记轮取证: 用未改动的原执行器与本机 C 编译器, 独立复算 A 交付包的每一条离线声称。

真实仓库只读; 全部产物写到 --out。设备动作 0(不碰 COM/DAP, 不下载不复位)。

用法:
  python tools/r7f5_registered_checks.py --zip <A 交付 zip> --repo <仓库根> --box <仓库外工作区> \
      --out <本轮 offline 目录> [--cc <C 编译器>]
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

CC_CANDIDATES = [r"C:/Program Files (x86)/Dev-Cpp/MinGW64/bin/gcc.exe", "cc", "gcc"]
REAL_KIND = "actual_previous_field_capture_NOT_current_trial"
A_STEPS_SHA = "4a80dcb6dd43028c6f526298463cba3d71bc312a379db5b152a4db52359b7eb0"
A_EXECUTOR_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
A_IMAGE_SHA = "502dca5d8c7ebfa794f890a9c03bc258fd9753f57b6a80c2f1e08add69888a7a"

CHECKS = []


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "verdict": "PASS" if ok else "FAIL", "detail": str(detail)[:400]})
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + str(detail)[:200] if detail else ""))
    return bool(ok)


def wjson(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--box", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cc", default="")
    a = ap.parse_args()
    repo, box, out = a.repo.resolve(), a.box.resolve(), a.out.resolve()
    if repo in box.parents or box == repo:
        raise SystemExit("--box 必须在仓库外")
    box.mkdir(parents=True, exist_ok=True)
    pkg = box / "pkg"
    if pkg.exists():
        shutil.rmtree(pkg)
    shutil.unpack_archive(str(a.zip), str(pkg))
    FIELD = pkg / "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field"
    plan = json.loads((FIELD / "plan_r7f5_b502_blind_irq_static.json").read_text(encoding="utf-8"))
    rows = json.loads((FIELD / "rows_r7f5_b502_blind_irq_static.json").read_text(encoding="utf-8"))
    a_res = json.loads((pkg / "a_review/review_result.json").read_text(encoding="utf-8"))

    # 1) 包身份
    zb = a.zip.read_bytes()
    ident = {"zip": a.zip.name, "bytes": len(zb), "sha256": sha(zb),
             "entries": len([p for p in pkg.rglob("*") if p.is_file()])}
    mrows = []
    for line in (pkg / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        parts = line.split("  ")
        if len(parts) == 2 and len(parts[0]) == 64:
            p = pkg / parts[1]
            mrows.append({"path": parts[1], "listed": parts[0],
                          "disk": sha(p.read_bytes()) if p.exists() else None})
    check("zip 身份已按磁盘字节登记", len(zb) > 0, "%d B %s" % (len(zb), sha(zb)[:16]))
    check("内层清单逐条重算全对且不含自身",
          all(r["listed"] == r["disk"] for r in mrows) and len(mrows) == 32,
          "%d/%d" % (sum(r["listed"] == r["disk"] for r in mrows), len(mrows)))
    check("IDENTITY.json 声明的两件落位件散列 == 交付字节",
          all(sha((pkg / f["path"]).read_bytes()) == f["sha256"]
              for f in json.loads((pkg / "IDENTITY.json").read_text(encoding="utf-8"))["files"]),
          "plan+rows")
    ident["manifest_rows"] = mrows

    # 2) 执行器与镜像未改
    exe = repo / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
    check("执行器仍是 R6C 原件(逐字节未动)", sha(exe.read_bytes()) == A_EXECUTOR_SHA, sha(exe.read_bytes())[:16])
    img = repo / "reports/firmware/phase8-m8-r7f2b-safety-print-fix/rt-thread-motor-m1-candidate.bin"
    check("b502 镜像身份与计划绑定一致",
          sha(img.read_bytes()) == plan["identity_binding"]["image"]["sha256"]
          and img.stat().st_size == plan["identity_binding"]["image"]["bytes"],
          "%d B" % img.stat().st_size)
    sys.path.insert(0, str(exe.parent))
    import m8r3_serial_chain as m
    check("计划绑定的两个提交在仓库里真实存在",
          all(subprocess.run(["git", "cat-file", "-e", c], cwd=str(repo)).returncode == 0
              for c in (plan["identity_binding"]["software_input_commit"],
                        plan["identity_binding"]["tested_image_build_commit"])),
          "software_input_commit + tested_image_build_commit")
    diff = subprocess.run(["git", "diff", "--name-only", "0d8239f", "HEAD", "--", "rt-thread-master"],
                          cwd=str(repo), capture_output=True, text=True).stdout.strip()
    check("产品源码自 R7F4 基线以来未变(0d8239f..HEAD)", diff == "", repr(diff[:120]))

    # 3) 步骤摘要 / S0 / validate_plan 由未改执行器重算
    steps_sha = m.canonical_steps_sha(m.stage_steps(plan["stages"][1], "response_driven"))
    check("步骤摘要 == A 声明值", steps_sha == A_STEPS_SHA, steps_sha)
    plan_file = FIELD / "plan_r7f5_b502_blind_irq_static.json"
    s0 = m.s0_binding(plan, str(repo), plan_path=str(plan_file),
                      snapshot=m.read_plan_snapshot(plan_file))
    s0_cmp = {k: v for k, v in s0.items() if "path" not in k and "source" not in k}
    a_cmp = {k: v for k, v in a_res["S0"].items() if "path" not in k and "source" not in k}
    check("S0 绑定事实与 A 逐字段一致(剔除绝对路径)", s0_cmp == a_cmp,
          [k for k in s0_cmp if s0_cmp.get(k) != a_cmp.get(k)])
    lines, bad, ncmd = m.validate_plan(plan, rows, str(repo / m.PLAN_APP_REL))
    (out / "validate_plan_local.txt").parent.mkdir(parents=True, exist_ok=True)
    (out / "validate_plan_local.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    fail_lines = [l for l in lines if "|FAIL|" in l]
    check("validate_plan 57 项且唯一 FAIL 就是已登记的 N/A 项",
          len(lines) == 57 and bad == 1 and len(fail_lines) == 1
          and fail_lines[0].startswith("PLAN_MOTION_CAP_BUDGET|"), "%d 项 fail=%d %s" % (len(lines), bad, fail_lines))
    a_val = (pkg / "a_review/validate_plan.txt").read_text(encoding="utf-8").splitlines()
    check("我的 validate_plan 逐行 == A 的落盘件", lines == a_val, "%d vs %d 行" % (len(lines), len(a_val)))

    # 4) 9 个 stub/sim 整包回放
    cases = [("positive", None, None),
             ("pre_fault", "system_status@pre",
              lambda rs: [r.replace("state=READY fault=0", "state=FAULT_LATCHED fault=8") for r in rs]),
             ("diag_already_active", "safety_irq_status@pre",
              lambda rs: [r.replace("raw=0 safe=0 irq=RISING [safe]", "raw=1 safe=0 irq=RISING [!!ACTIVE!!]") for r in rs]),
             ("enable_refused", "safety_irq_enable",
              lambda rs: ["[SAFETY] irq enable REFUSED: input polarity NOT confirmed"]),
             ("enable_nul", "safety_irq_enable", lambda rs: rs + ["\0"]),
             ("open_not_ready", "safety_irq_status@open",
              lambda rs: [r.replace("protection_ready = YES", "protection_ready = NO") for r in rs]),
             ("open_output_high", "system_status@open",
              lambda rs: [r.replace("request=LOW pad=0", "request=HIGH pad=1") for r in rs]),
             ("disable_bad_rc", "safety_irq_disable",
              lambda rs: [r.replace("detached (0)", "detached (-1)") for r in rs])]
    batch = box / ("replay-" + str(int(time.time())))
    replays = []
    for name, key, mutate in cases:
        d = batch / name
        d.mkdir(parents=True)
        cr, cp = copy.deepcopy(rows), copy.deepcopy(plan)
        if key:
            cr["commands"][key]["healthy"] = mutate(cr["commands"][key]["healthy"])
        wjson(d / "rows.json", cr)
        cp["rows_file"] = "rows.json"
        rb = (d / "rows.json").read_bytes()
        cp["plan_identity"] = {"path": "offline-test-only",
                               "rows_file": {"path": "rows.json", "bytes": len(rb), "sha256": sha(rb)}}
        wjson(d / "plan.json", cp)
        with contextlib.redirect_stdout(io.StringIO()) as cap:
            res = m.dispatch_plan(cp, "stub", "sim", str(d / "run"), m.SimClock(), "healthy", "COM5",
                                  str(repo), plan_dir=str(d), plan_path=str(d / "plan.json"),
                                  plan_snapshot=m.read_plan_snapshot(d / "plan.json"))
        (d / "dispatch.txt").write_text(cap.getvalue() + json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
        led = [json.loads(l) for l in (d / "run/S_S1/ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        replays.append({"case": name, "status": res["status"],
                        "stages": [{"id": s.get("id"), "status": s.get("status"), "code": s.get("code")}
                                   for s in res["stages"]],
                        "quota_used": res.get("quota_used", {}),
                        "ledger_rows": len(led),
                        "motion_cmds_in_ledger": sorted({v.get("cmd") for v in led
                                                          if v.get("cmd") in ("motor_arm", "motor_start")}),
                        "dispatch_txt_sha256": sha((d / "dispatch.txt").read_bytes())})
    wjson(out / "replays_local.json", replays)
    a_rep = a_res["full_original_executor_replays"]
    same = all(x["status"] == y["status"] and x["stages"] == y["stages"] and x["quota_used"] == y["quota_used"]
               for x, y in zip(replays, a_rep))
    check("8 个回放(正常 1 + 反例 7)的状态/阶段/额度实耗与 A 逐例一致", same and len(replays) == len(a_rep),
          [(x["case"], x["status"]) for x in replays])
    check("正常场景 OK 且只消耗申报的四项",
          replays[0]["status"] == "OK"
          and set(replays[0]["quota_used"]) == {"port_open", "tmc_config_apply", "irq_polarity_prep",
                                                "safety_irq_disable"},
          json.dumps(replays[0]["quota_used"], sort_keys=True))
    check("任何回放都没发 arm/start(台账级)",
          not any(x["motion_cmds_in_ledger"] for x in replays),
          [x["motion_cmds_in_ledger"] for x in replays if x["motion_cmds_in_ledger"]])
    check("故障前置未发 apply / DIAG 已活动未置标志未开门",
          "tmc_config_apply" not in replays[1]["quota_used"]
          and "irq_polarity_prep" not in replays[2]["quota_used"],
          [replays[1]["quota_used"], replays[2]["quota_used"]])

    # 5) rows 的"真实旧回包"必须等于仓库归档原件重切
    real = []
    for key, ent in rows["commands"].items():
        prov = ent["_provenance"]
        if prov["kind"] == REAL_KIND:
            rel = prov["source"] if isinstance(prov["source"], str) else prov["source"][0]
            raw = (repo / rel).read_bytes()
            real.append({"key": key, "source": rel, "bytes": len(raw), "nul": raw.count(0),
                         "rows_equal_archive": m.split_rows(raw) == ent["healthy"]})
    wjson(out / "rows_real_capture_check.json", real)
    check("7 条真实旧回包 rows == 归档原件重切(且无 NUL)",
          len(real) == 7 and all(r["rows_equal_archive"] and r["nul"] == 0 for r in real),
          [(r["key"], r["bytes"], r["rows_equal_archive"]) for r in real])
    kinds = {}
    for ent in rows["commands"].values():
        kinds[ent["_provenance"]["kind"]] = kinds.get(ent["_provenance"]["kind"], 0) + 1
    check("rows 的预测与真实来源已分类登记(预测不计现场证据)",
          kinds.get(REAL_KIND, 0) == 7 and sum(v for k, v in kinds.items() if k != REAL_KIND) > 0,
          json.dumps(kinds, ensure_ascii=False))

    # 6) 新 shape 的行长度上限
    lens = {k: [len((r + "\n").encode()) for r in rows["commands"][k]["healthy"]]
            for k in ("safety_polarity_confirm CONFIRM", "safety_irq_enable", "safety_irq_disable")}
    wjson(out / "line_lengths.json", lens)
    check("三条新命令 shape 行长度与 A 一致且 <=127 B",
          lens == a_res["new_source_shape_line_lengths_including_LF"]
          and max(n for v in lens.values() for n in v) <= 127,
          {k: max(v) for k, v in lens.items()})

    # 7) 源码侧核对: shape 字符串确实来自生产码
    src = (repo / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
           "/applications/safety_thread.c").read_text(encoding="utf-8")
    probes = ["(operator must have MEASURED these)",
              "this is an OPERATOR DECLARATION. Software does NOT",
              "input polarity MARKED VALIDATED (operator confirm)",
              "NO (IRQ gate still CLOSED)",
              "IRQ gate OPEN: %d active protection input(s) registered",
              "detached (%d)",
              "OPEN (EXTI registered)", "CLOSED (polled only)",
              "operator CONFIRMED", "NOT confirmed",
              "活动安全输入(%d 路)无软件响应路径"]
    missing = [p for p in probes if p not in src]
    wjson(out / "source_string_probes.json", {"probes": probes, "missing": missing,
                                              "source": "applications/safety_thread.c"})
    check("shape 断言用到的字符串逐条在生产源码里存在", not missing, missing)
    sup = (repo / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
           "/applications/supervisor.c").read_text(encoding="utf-8")
    pol_line = next((l.strip() for l in sup.splitlines() if "safety_polarity_validated()" in l), "")
    check("supervisor 极性字样按源码三元组: 成功=大写 CONFIRMED, 未确认=not-confirmed",
          '"CONFIRMED"' in pol_line and '"not-confirmed"' in pol_line, pol_line)

    # 8) A 的 host 桩编译件: 本机 C 编译器复跑
    cc = a.cc or next((c for c in CC_CANDIDATES
                       if shutil.which(c) or Path(c).exists()), None)
    host = {"cc": cc}
    if cc:
        srcdir = pkg / "a_review/source_prints"
        exe_out = box / "host_shape.exe"
        b = subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-Wno-misleading-indentation", str(srcdir / "extracted_source.c"),
                            "-o", str(exe_out)], capture_output=True)
        host["compile_rc"] = b.returncode
        host["compile_stderr"] = b.stderr.decode("utf-8", "replace")[:400]
        rows_by_case = {"pre": "safety_irq_status@pre", "pol": "safety_polarity_confirm CONFIRM",
                        "enable": "safety_irq_enable", "open": "safety_irq_status@open",
                        "disable": "safety_irq_disable", "closed": "safety_irq_status@closed"}
        cases_local = []
        for name, key in rows_by_case.items():
            r = subprocess.run([str(exe_out), name], capture_output=True)
            got = r.stdout
            ref = (srcdir / (name + ".stdout.bin")).read_bytes()
            lf = got.replace(b"\r\n", b"\n")
            lines = [l for l in lf.decode("utf-8", "replace").split("\n") if l]
            payload = [l for l in lines if l.startswith("[SAFETY]")]
            ref_rows = rows["commands"][key]["healthy"]
            cases_local.append({"case": name, "rc": r.returncode, "bytes": len(got),
                                "sha256": sha(got), "nul": got.count(0),
                                "cr_in_local_run": got.count(b"\r"), "cr_in_A_file": ref.count(b"\r"),
                                "raw_equal_to_A_stdout": got == ref,
                                "equal_to_A_stdout_after_lf_normalize": lf == ref,
                                "local_payload_lines": len(payload),
                                "delivered_rows_excluded_from_compare":
                                    [l for l in ref_rows if not l.startswith("[SAFETY]")],
                                "payload_equal_to_delivered_safety_rows":
                                    payload == [l for l in ref_rows if l.startswith("[SAFETY]")],
                                "max_call_bytes": max(int(x.split("intended=")[1].split()[0])
                                                      for x in r.stderr.decode().splitlines()
                                                      if "intended=" in x)})
        host["cases"] = cases_local
        host["compare_basis"] = (
            "host 桩只打印负载行; 交付 rows 的 safety_irq_status@* 还含命令回显与行末 `msh >` 提示符"
            "(那两条来自真实串口捕获)。因此逐行比对只取 [SAFETY] 开头的负载行, 被排除的行原样登记在 "
            "delivered_rows_excluded_from_compare, 不做静默过滤。")
        host["line_endings_note"] = (
            "本机重跑的 stdout 带 CRLF, A 的落盘件是 LF: A 在 Linux 工作区跑, 本机是 Windows, "
            "C 运行库对 stdout 文本模式做了行尾转换。差异只在行尾(归一后逐字节相同), 不是形状分歧。")
        wjson(out / "host_shape_recompile.json", host)
        check("A 的 host 桩源码在本机 C 编译器下重编成功", b.returncode == 0, host["compile_stderr"][:160])
        check("六组重跑 stdout 归一行尾后与 A 落盘件逐字节相同",
              all(c["equal_to_A_stdout_after_lf_normalize"] for c in cases_local),
              [(c["case"], c["bytes"], c["cr_in_local_run"], c["cr_in_A_file"],
                c["raw_equal_to_A_stdout"]) for c in cases_local])
        check("六组重跑的 [SAFETY] 负载行逐行 == 交付 rows 的对应负载行",
              all(c["payload_equal_to_delivered_safety_rows"] for c in cases_local),
              [(c["case"], c["local_payload_lines"]) for c in cases_local
               if not c["payload_equal_to_delivered_safety_rows"]])
        check("重跑无裸 NUL 且单次最长 <=127 B",
              all(c["nul"] == 0 and c["max_call_bytes"] <= 127 for c in cases_local),
              [(c["case"], c["max_call_bytes"]) for c in cases_local])
    else:
        host["cases"] = "NOT_RUN: 本机没有可用的 C 编译器, A 的 host 桩形状未复算(不写 PASS)"
        wjson(out / "host_shape_recompile.json", host)
        check("host 桩形状复算状态已如实登记", True, "NOT_RUN")

    summary = {"zip_identity": ident, "steps_sha256": steps_sha, "checks": CHECKS,
               "fails": sum(1 for c in CHECKS if c["verdict"] == "FAIL"), "total": len(CHECKS),
               "device_actions": 0,
               "scope": "离线复算: 只读三份交付件与仓库原件; 执行器/固件/保护门实现未改; "
                        "host 桩与 stub 回放只证形状与流程, 不证中断交付、板端时序或物理极性。"}
    wjson(out / "summary.json", summary)
    print("SUMMARY total=%d fails=%d" % (len(CHECKS), summary["fails"]))
    return 1 if summary["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
