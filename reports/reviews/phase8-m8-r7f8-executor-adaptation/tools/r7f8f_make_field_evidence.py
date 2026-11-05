# -*- coding: utf-8 -*-
"""R7F8F F11 现场执行证据归档(设备动作已发生, 本工具**只读**那份产物, 不再碰设备)。

为什么要有这个工具, 而不是手抄一段结论:
  * 现场只跑了一次 dispatch(唯一一次开口), 结论必须能从**落盘字节**复算, 不能靠我复述;
  * 停止原因是**包侧缺一条申报**(sys_pre 没带 status_lines_allow), 不是板上出了危险状态 ——
    这两件事必须用同一条判据分开钉死: 板态行、写入条数、额度账各自单独判;
  * 原始 983 B 一律逐字节进仓(该目录 .gitattributes 已 -text, 不被 autocrlf 改行尾),
    台账里的仓外路径全部掩成 <WORK>; 掩码不生效就判红(不靠"看起来干净")。
用法: python tools/r7f8f_make_field_evidence.py --repo D:/ns800 --run D:/ns800_r7f8f_offline/f11_run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
FIELD = ROUND + "/field"
RUN_REL = "S_S1"
OUT_REL = "proof"
MASKED = "<WORK>"
GATES = []
OUT = []


def note(name, ok, detail=""):
    OUT.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    GATES.append({"name": name, "verdict": "PASS" if ok else "FAIL", "detail": detail})
    return ok


def sha(b):
    return hashlib.sha256(b).hexdigest()


def read_jsonl(p):
    rows = []
    for ln in pathlib.Path(p).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass
    return rows


def mask(text, roots):
    for r in roots:
        for form in (r, r.replace("/", "\\")):
            text = text.replace(form, MASKED)
    return re.sub(r"[A-Za-z]:\\\\?ns800_r7f8f_offline[^\"'\s]*", MASKED, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--run", required=True, type=pathlib.Path)
    ap.add_argument("--approval-file", default="D:/ns800_r7f8f_offline/approvals/"
                                             "approval_M8-R7F8F-F11-20261105.json",
                    help="现场那本持久额度账的落盘位置(仓外); 本判据只读它, 用它核三件身份")
    a = ap.parse_args()
    repo = a.repo.resolve()
    run = a.run.resolve()
    src_rx = run / RUN_REL / "01_sys_pre.rx.bin"
    if not src_rx.is_file():
        print("REFUSED|现场捕获不在盘上: %s" % src_rx)
        return 1
    rx = src_rx.read_bytes()
    outd = repo / ROUND / OUT_REL
    outd.mkdir(parents=True, exist_ok=True)
    dst_rx = outd / "r7f8f_field_f11_sys_pre.rx.bin"
    dst_rx.write_bytes(rx)
    txt = rx.decode("utf-8", "replace")
    lines = [l.strip() for l in txt.splitlines()]
    (outd / "r7f8f_field_f11_sys_pre.rx.txt").write_text(
        txt, encoding="utf-8", newline="\n")

    sess = read_jsonl(run / RUN_REL / "ledger.jsonl")
    disp = read_jsonl(run / "dispatch_ledger.jsonl")
    roots = sorted({str(run), str(repo)}, key=len, reverse=True)
    for tag, ev in (("ledger", run / RUN_REL / "ledger.jsonl"),
                    ("dispatch_ledger", run / "dispatch_ledger.jsonl")):
        m = mask(pathlib.Path(ev).read_text(encoding="utf-8", errors="replace"), roots)
        (outd / ("r7f8f_field_f11_%s.jsonl" % tag)).write_text(m, encoding="utf-8", newline="\n")
    so = (run.parent / "f11_stdout.txt")
    stdout_txt = so.read_text(encoding="utf-8", errors="replace") if so.is_file() else ""

    # 1) 原始字节逐字节进仓, 且与台账里 RX_DONE 记的散列/条数对上(不是"我另存了一份")
    rxd = [e for e in sess if e.get("kind") == "RX_DONE"]
    note("RX_RAW_VERBATIM_AND_MATCHES_LEDGER",
         dst_rx.read_bytes() == rx and len(rxd) == 1
         and rxd[0].get("raw_sha256") == sha(rx) and rxd[0].get("bytes") == len(rx),
         "%d B sha=%s… 台账 RX_DONE 记的 bytes/raw_sha256 与之相等=%s nul=%s prompt_seen=%s"
         % (len(rx), sha(rx)[:12], bool(rxd) and rxd[0].get("raw_sha256") == sha(rx),
            (rxd[0].get("nul_bytes") if rxd else "-"),
            (rxd[0].get("prompt_seen") if rxd else "-")))

    # 2) 掩码必须真的把仓外路径清掉(四种形态逐一查), 否则证据里带机器目录
    leaked = []
    for f in outd.glob("r7f8f_field_f11_*"):
        t = f.read_text(encoding="utf-8", errors="replace") if f.suffix != ".bin" else ""
        if "ns800_r7f8f_offline" in t or re.search(r"[A-Za-z]:[\\/]{1,2}\w", t):
            leaked.append(f.name)
    note("OFFLINE_PATHS_MASKED", not leaked, "残留文件=%s(判据=文本里不得出现仓外根目录或盘符路径)"
         % (leaked or "无"))

    # 3) 停止身份: 哪个步、哪个码、哪一行触发的
    st = [e for e in sess if e.get("kind") == "SESSION_STOP"]
    bo = [e for e in sess if e.get("kind") == "BUSINESS_OUTCOME"]
    trig = (bo[0].get("lines") or [""]) if bo else [""]
    note("STOP_IS_STEP1_QUERY_BUSINESS_RULE",
         len(st) == 1 and st[0].get("code") == "BUSINESS_FAILED"
         and bo and bo[0].get("as") == "sys_pre" and trig[0] in lines,
         "code=%s as=%s 触发行=%r 该行确实逐字出现在捕获第 %d 行"
         % (st[0].get("code") if st else "-", bo[0].get("as") if bo else "-", trig[0],
            (lines.index(trig[0]) + 1) if trig[0] in lines else -1))

    # 4) 现场到底写了什么: 捕获里只允许有 system_status 的回显, 一条起转/使能类命令的回显都不许有
    echoed = [c for c in ("system_status", "motor_arm", "motor_start", "motor_stop",
                          "motor_disarm", "safety_irq_enable", "safety_irq_disable",
                          "tmc_config_apply", "diag_profile_zero", "diag_profile_learn",
                          "diag_profile_learn_cancel", "diag_profile_commit")
              if re.search(r"(^|\r\n)%s\r\n" % c, txt)]
    note("ONLY_THE_QUERY_EVER_WRITTEN", echoed == ["system_status"],
         "捕获里的命令回显=%s(1 条查询的回显; 电机/使能/配置/学习类命令一条都没写出去)" % echoed)

    # 5) 额度账: 只有 port_open 被花掉, 其余(含 retry/download/readback/reset)一条没动
    endd = [e for e in disp if e.get("kind") == "APPROVAL_LEDGER_END"]
    spent = (endd[0].get("spent") or {}) if endd else {}
    spent_nonzero = {k: v for k, v in spent.items() if v}
    note("QUOTA_SPENT_EXACTLY_PORT_OPEN",
         bool(endd) and spent_nonzero == {"port_open": 1}
         and spent.get("retry", 0) == 0 and spent.get("motor_arm", 0) == 0
         and spent.get("motor_run", 0) == 0,
         "spent=%s(整本账只剩 port_open 被花; 不重试、不下载、不回读、不复位)"
         % json.dumps({k: v for k, v in spent.items() if v}, ensure_ascii=False))

    # 6) 停止那一刻的板态(从原始捕获逐行取, 不从我自己的结论取)
    def find(prefix):
        return next((l for l in lines if l.startswith(prefix)), "")
    snap = find("[Motor] state=")
    req = find("[Safety] request=")
    ui = find("[UI]")
    diag = find("[Diag]")
    safe = ("armed=0" in snap and "state=0" in snap and "cur=0" in snap and "tgt=0" in snap
            and "request=LOW" in req and "pad=0" in req and "verdict=OK" in req
            and "SAFE-OFF" in ui and "MONITOR_ONLY" in diag)
    note("BOARD_WAS_LEFT_SAFE_AT_STOP", safe,
         "%s ;; %s ;; %s ;; %s" % (snap, req, ui, diag))

    # 7) 根因定位到产品源码行(只读核对): 这行是 system_status **无条件**打的门位评估
    sup = repo / ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                  "ns800rt7p65-nssinepad/applications/supervisor.c")
    mot = repo / ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                  "ns800rt7p65-nssinepad/applications/motor.h")
    stxt = sup.read_text(encoding="utf-8", errors="replace").splitlines()
    # 只认 system_status 那条**无条件**打印(同一文件里 :261 的 SELFTEST 行也含同样字样, 不能混)
    ln = [i + 1 for i, l in enumerate(stxt)
          if 'rt_kprintf("[Motor] arm gate fail mask=' in l]
    mtxt = mot.read_text(encoding="utf-8", errors="replace")
    b3 = re.search(r"#define\s+MOT_GATE_NOT_PROTECTED\s+\(1u << 3\)", mtxt)
    b4 = re.search(r"#define\s+MOT_GATE_NO_DRV_CONFIG\s+\(1u << 4\)", mtxt)
    plan0 = json.loads((repo / FIELD / "plan_r7f8f_2369_real.json").read_text(encoding="utf-8"))
    syspre = plan0["stages"][0]["steps"][0]
    note("ROOT_CAUSE_IS_PACKAGE_SIDE",
         len(ln) == 1 and b3 and b4 and "status_lines_allow" not in syspre,
         "supervisor.c:%d 无条件打这行(motor.h 的 bit3=%s bit4=%s => 0x18 是"
         "\"此刻还没开 IRQ 门 + TMC 参数还没下发\", 正是 §5 第 1 步该看到的前态); "
         "而计划 sys_pre=%s 里**没有** status_lines_allow 这条申报 => 执行器按业务失败线停了。"
         "离线那 47 条门没暴露它的原因是 stub 回灌的是我自己造的行表, 不是板子真打印。"
         % (ln[0] if ln else "-", "MOT_GATE_NOT_PROTECTED", "MOT_GATE_NO_DRV_CONFIG",
            json.dumps({k: syspre.get(k) for k in ("as", "cmd", "expect_contains")},
                       ensure_ascii=False)))

    # 8) 产品/固件一字未动(本轮硬边界), 现场停止不许靠改产品消红
    d = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--",
                        "rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                        "ns800rt7p65-nssinepad"], capture_output=True)
    note("PRODUCT_TREE_UNTOUCHED_AT_STOP", d.stdout.decode("utf-8", "replace").strip() == "",
         "git status 该产品目录待提交改动=%r" % (d.stdout.decode("utf-8", "replace").strip() or "空"))

    # 9) 一次开口, 没有第二次: 台账里 PORT_OPEN/PORT_CLOSED 各 1, 预留只有 port_open
    note("ONE_PORT_OPEN_NO_SECOND_TRY",
         len([e for e in sess if e.get("kind") == "PORT_OPEN"]) == 1
         and len([e for e in sess if e.get("kind") == "PORT_CLOSED"]) == 1
         and len([e for e in disp if e.get("kind") == "APPROVAL_RESERVED"]) == 1,
         "PORT_OPEN=%d PORT_CLOSED=%d APPROVAL_RESERVED=%d(唯一一次开口已按账扣掉; 我没有开第二次)"
         % (len([e for e in sess if e.get("kind") == "PORT_OPEN"]),
            len([e for e in sess if e.get("kind") == "PORT_CLOSED"]),
            len([e for e in disp if e.get("kind") == "APPROVAL_RESERVED"])))

    # 10) 现场身份绑定: 账本记的 plan/executor/image 三件 == 盘上那份承接件
    dp = repo / FIELD / "plan_r7f8f_2369_real_dispatch.json"
    bnd = [e for e in sess if e.get("kind") == "APPROVED_PACKAGE_VERIFIED"]
    apx = pathlib.Path(a.approval_file)
    ids = json.loads(apx.read_text(encoding="utf-8")).get("identities") or {}         if apx.is_file() else {}
    note("FIELD_IDENTITIES_BOUND_TO_DISPATCH_COPY",
         bool(bnd) and dp.is_file()
         and bnd[0].get("plan_sha256") == sha(dp.read_bytes())
         and ids.get("executor_sha256")
         == sha((repo / ROUND / "tools" / "m8r3_serial_chain_r7f8.py").read_bytes())
         and ids.get("image_sha256") == sha((repo / (plan0["identity_binding"]["image"]["path"]))
                                            .read_bytes())
         and bnd[0].get("transport") == "real" and bnd[0].get("steps") == 38,
         "账本=%s 三件身份当场复算: plan=%s…(=盘上承接件逐字节) executor 相符=%s 镜像相符=%s "
         "transport=%s steps=%s; 令牌原话记在承接件里=%s"
         % (MASKED + "/approvals/approval_M8-R7F8F-F11-20261105.json",
            (bnd[0].get("plan_sha256") or "")[:12],
            ids.get("executor_sha256")
            == sha((repo / ROUND / "tools" / "m8r3_serial_chain_r7f8.py").read_bytes()),
            ids.get("image_sha256")
            == sha((repo / (plan0["identity_binding"]["image"]["path"])).read_bytes()),
            bnd[0].get("transport") if bnd else "-", bnd[0].get("steps") if bnd else "-",
            json.loads(dp.read_text(encoding="utf-8")).get("approval", {})
            .get("token_verbatim") if dp.is_file() else "-"))

    # 11) 这条专门防我把它写成"验收通过"
    fc = "flow_complete=False" in stdout_txt and "core_protection_verified=False" in stdout_txt
    note("NOT_A_FIELD_ACCEPTANCE", fc,
         "DISPATCH_SUMMARY=STOP / FLOW_VS_VERIFICATION 两个 False 都在原文里 => 本件记录的是"
         "**首错停止的现场事实**, 不是保护闭环验收; 电机从未带电, 相对保护从未触发")

    text = "\n".join(OUT) + "\nR7F8F_FIELD_F11_SUMMARY|判据=%d|FAIL=%d|%s\n" % (
        len(OUT), sum(1 for g in GATES if g["verdict"] == "FAIL"),
        ",".join(g["name"] for g in GATES if g["verdict"] == "FAIL") or "none")
    (outd / "r7f8f_field_f11_receipt.txt").write_text(text, encoding="utf-8", newline="\n")
    (outd / "r7f8f_field_f11_receipt.json").write_text(json.dumps(
        {"receipt": "field_f11", "round": "M8-R7F8F", "run_dir_offline": True,
         "raw_capture": {"path": OUT_REL + "/r7f8f_field_f11_sys_pre.rx.bin",
                         "bytes": len(rx), "sha256": sha(rx)},
         "stop": {"code": st[0].get("code") if st else None,
                  "step": bo[0].get("as") if bo else None,
                  "trigger_line": trig[0]},
         "quota_spent": spent, "gates": GATES,
         "summary": {"checks": len(OUT),
                     "fail": sum(1 for g in GATES if g["verdict"] == "FAIL")},
         "not_claimed": {"flow_complete": False, "core_protection_verified": False,
                         "polarity_measured": False, "motor_ever_energized": False}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(text.splitlines()[-1])
    print("WROTE|%s|raw=%d B|%s" % (OUT_REL, len(rx), sha(rx)[:12]))
    return 0 if not any(g["verdict"] == "FAIL" for g in GATES) else 1


if __name__ == "__main__":
    sys.exit(main())
