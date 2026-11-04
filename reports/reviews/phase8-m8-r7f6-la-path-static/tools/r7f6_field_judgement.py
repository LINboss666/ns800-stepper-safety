# -*- coding: utf-8 -*-
"""R7F6 现场判读(独立复算): 只读仓库内已归档字节, 重新算出串口层/仪器层/接线解释三层结论。

不信任"跑成功"这句话本身: 五份响应重新过一遍未改执行器的 split_rows + assert_step + check_danger,
正面状态断言逐条从原字节再解析; 采集侧从原生 .sal 的 meta.json 与 CSV 真实表头/行重新推通道、
登记采样率、覆盖区间与跳变数。

每条判据都配"会判红"的自检(改坏一个字节必须被检出), 自检不炸就宣布结果不作数。

用法: python tools/r7f6_field_judgement.py --repo D:/ns800 [--out 文本] [--json 摘要]
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import pathlib
import re
import sys
import zipfile

OUT = []
FAILS = []
SUM = {}


def chk(name, ok, detail=""):
    OUT.append("%-5s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    SUM[name] = {"verdict": "PASS" if ok else "FAIL", "detail": str(detail)[:400]}
    if not ok:
        FAILS.append(name)
    return ok


def note(name, detail):
    OUT.append("INFO  %-46s %s" % (name, detail))
    SUM[name] = {"verdict": "INFO", "detail": str(detail)[:400]}


def iso_ns(s):
    """解析 Saleae 的 ISO8601(可能 9 位小数 + 时区), 返回 aware datetime。"""
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.(\d+))?(Z|[+-]\d{2}:\d{2})$", s.strip())
    if not m:
        raise ValueError("ISO_PARSE " + s)
    frac = (m.group(3) or "")[:6].ljust(6, "0")
    tz = m.group(4)
    off = dt.timezone.utc if tz == "Z" else dt.timezone(
        dt.timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6])) * (1 if tz[0] == "+" else -1))
    return dt.datetime.fromisoformat("%s.%s" % (m.group(1), frac)).replace(tzinfo=off)


def covered(cap_start, cap_end, sess_start, sess_end):
    return cap_start <= sess_start and cap_end >= sess_end


def five(files):
    return len(files) == 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    rv = repo / "reports/reviews/phase8-m8-r7f6-la-path-static"
    sys.path.insert(0, str(repo / "reports/reviews/phase8-m8-relative-protection-r3/field"))
    import m8r3_serial_chain as EX  # 未改执行器: 判读沿用它的同一套合同

    plan = json.loads((rv / "field/plan_r7f6_b502_readonly.json").read_text(encoding="utf-8"))
    s1 = [s for s in plan["stages"] if s["id"] == "S1"][0]
    steps = s1["steps"]
    runs = sorted(p for p in (rv / "field_run").iterdir() if p.is_dir())
    chk("FIELD_RUN_ARCHIVED_ONCE", len(runs) == 1, [r.name for r in runs])
    run = runs[0]
    sess = run / "S_S1"

    # ---------------- 串口层 ----------------
    rx = sorted(sess.glob("*.rx.bin"))
    chk("SERIAL_FIVE_RESPONSE_BYTES", five(rx), [p.name for p in rx])
    files = [re.sub(r"^\d+_", "", p.name).replace(".rx.bin", "") for p in rx]
    chk("SERIAL_FILE_ORDER_EQUALS_PLAN", files == [st["as"] for st in steps],
        "文件=%s 计划=%s" % (files, [st["as"] for st in steps]))

    nul, bad_utf, danger, echo = [], [], [], []
    rows_by = {}
    for st, p in zip(steps, rx):
        raw = p.read_bytes()
        if b"\x00" in raw:
            nul.append(p.name)
        try:
            raw.decode("utf-8", "strict")
        except UnicodeDecodeError:
            bad_utf.append(p.name)
        try:
            EX.check_danger(raw)
        except Exception as e:
            danger.append("%s:DANGER_%r" % (p.name, e))
        try:
            rows_by[p.name] = EX.split_rows(raw, command=st["cmd"])
        except Exception as e:
            rows_by[p.name] = []
            danger.append("%s:SPLIT_%r" % (p.name, e))
        first = raw.decode("utf-8", "replace").split("\n")[0].strip()
        if first != st["cmd"]:
            echo.append("%s 首行=%r 计划=%r" % (p.name, first, st["cmd"]))
    chk("SERIAL_NO_BARE_NUL", not nul, nul)
    chk("SERIAL_STRICT_UTF8", not bad_utf, bad_utf)
    chk("SERIAL_SPLIT_AND_DANGER_GATE_CLEAN", not danger, danger)
    chk("SERIAL_ECHO_FIRST_LINE_EQUALS_PLAN_CMD", not echo, echo)

    class NullLed:
        def ev(self, *n, **kw):
            pass

    def verdict(st, raw):
        try:
            return EX.assert_step(st, EX.split_rows(raw, command=st["cmd"]),
                                  "selftest", NullLed(), raw=raw).get("verdict")
        except Exception as e:
            return "RAISED:%s" % getattr(e, "code", e)

    vres = [(p.name, verdict(st, p.read_bytes())) for st, p in zip(steps, rx)]
    chk("ASSERT_STEP_RECOMPUTED_ALL_OK", all(v == "OK" for _n, v in vres), vres)

    allt = {p.name: p.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n") for p in rx}

    def where(pat):
        return [n for n, t in allt.items() if re.search(pat, t, re.M)]

    POS = [("^\\[Boot\\]   bootstrap-complete=YES abort-stage=0$", "bootstrap_complete_YES"),
           ("^\\[Safety\\] state=READY fault=0 irq_gate=CLOSED polarity=not-confirmed protect_ready=NO$",
            "READY_fault0_irqCLOSED_polNO_readyNO"),
           ("drive_cfg=UNCONFIGURED", "tmc_UNCONFIGURED"),
           ("^\\[Motor\\] arm gate fail mask=0x18 \\(arm REFUSED\\)$", "arm_mask_0x18"),
           ("^\\[Motor\\] state=0 cur=0 tgt=0 armed=0", "motor_rest_zero"),
           ("^\\[Safety\\] request=LOW pad=0 expected_ENN=HIGH ENN_fb=1\\(resolved\\) verdict=OK$",
            "request_LOW_ENN_HIGH"),
           ("^\\[SAFETY\\] request=DISABLE\\(LOW\\) pad=0 expected_FB=HIGH FB=1 -> verdict=OK$",
            "safety_status_request_basis"),
           ("^\\[SAFETY\\] TMC_DIAG\\s+pin=3 raw=0 safe=0 irq=RISING \\[safe\\]$", "diag_pin_idle"),
           ("^\\[SAFETY\\] irq gate   = CLOSED \\(polled only\\)$", "irq_gate_row"),
           ("^\\[SAFETY\\] protection_ready = NO$", "protection_ready_NO"),
           ("^\\[SAFETY\\] polarity   = NOT confirmed", "polarity_not_confirmed_row"),
           ("^\\[MOT\\] arm_gate_mask=0x18 REFUSED$", "mot_arm_gate_refused")]
    for pat, name in POS:
        chk("POSITIVE_" + name, bool(where(pat)), where(pat))

    forb = s1["forbidden_commands"]
    sent = [st["cmd"] for st in steps]
    hit = [f for f in forb if f in sent or (len(f.split()) > 1 and f in sent)]
    chk("NO_FORBIDDEN_COMMAND_SENT", not hit, "禁做清单=%d 条 实发=%s 命中=%s" % (len(forb), sent, hit))

    led = [json.loads(l) for l in (sess / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    kinds = [e.get("kind") or e.get("ev") for e in led]
    chk("PORT_OPENED_EXACTLY_ONCE", kinds.count("PORT_OPEN_ATTEMPT") == 1 and kinds.count("PORT_OPEN") == 1
        and kinds.count("PORT_CLOSED") == 1,
        "attempt=%d open=%d close=%d" % (kinds.count("PORT_OPEN_ATTEMPT"), kinds.count("PORT_OPEN"),
                                         kinds.count("PORT_CLOSED")))
    rxev = [e for e in led if (e.get("kind") or e.get("ev")) == "RX_DONE"]
    chk("RX_DONE_COUNT_FIVE", len(rxev) == 5, len(rxev))
    sess_end_t = max(e.get("t_s", 0) for e in led)
    chk("SESSION_WITHIN_DECLARED_DEADLINE", sess_end_t < s1["total_deadline_seconds"],
        "会话末 t_s=%.3f s < 申报 %d s" % (sess_end_t, s1["total_deadline_seconds"]))
    wire = []
    prev_t = [e["t_s"] for e in led if (e.get("kind") or e.get("ev")) == "PORT_OPEN"] or [0.0]
    prev_t = prev_t[0]
    for st, e in zip(steps, rxev):
        need = e["bytes"] * 10 / 115200.0          # 115200 8N1 => 每字节 10 bit
        gap = e["t_s"] - prev_t
        prev_t = e["t_s"]
        wire.append("%s %dB 线时下界%.3fs 实测%.3fs %s"
                    % (st["as"], e["bytes"], need, gap, "OK" if gap >= need - 0.02 else "TOO_FAST"))
    chk("RX_BYTES_ARE_WIRE_TIME_FEASIBLE", len(wire) == 5 and all("TOO_FAST" not in w for w in wire), wire)

    ledger = json.loads((rv / "ledgers" / ("approval_" + plan["task_id"] + ".json")).read_text(encoding="utf-8"))
    spent = ledger["spent"]
    chk("LEDGER_SPENT_PORT_OPEN_ONLY", spent["port_open"] == 1
        and all(v == 0 for k, v in spent.items() if k != "port_open"),
        "非零项=%s" % {k: v for k, v in spent.items() if v})
    chk("LEDGER_PENDING_AND_COMMIT_ZERO", ledger["pending"] == [] and ledger.get("commit_failures") in ([], None),
        "pending=%s commit_failures=%s" % (ledger["pending"], ledger.get("commit_failures", "键缺席")))
    ids = ledger["identities"]
    ident = json.loads((rv / "IDENTITY.json").read_text(encoding="utf-8"))["inputs"]
    chk("LEDGER_IDENTITIES_MATCH_PACKAGE", ids["executor_sha256"] == ident["executor"]["sha256"]
        and ids["image_sha256"] == ident["image"]["sha256"]
        and ids["plan_sha256"] == ident["plan"]["sha256"], ids)
    diff = {k: (plan["quota"][k], ledger["caps"][k]) for k in plan["quota"] if plan["quota"][k] != ledger["caps"][k]}
    chk("LEDGER_CAPS_EQUALS_PLAN_QUOTA", not diff, "22 项额度%s" % ("全等" if not diff else "不符=%s" % diff))

    fires = []
    p0 = rx[0].read_bytes()
    t1 = p0.replace(b"bootstrap-complete=YES", b"bootstrap-complete=NO")
    fires.append(("expectation_flip", verdict(steps[0], t1) != "OK"))
    fires.append(("nul_injection", verdict(steps[0], p0 + b"\x00") != "OK"))
    fires.append(("business_failure_line", verdict(steps[0], p0 + b"\r\n[TMC] write FAILED\r\nmsh >") != "OK"))
    fires.append(("dropped_response_count", five(rx[:4]) is False and five(rx) is True))
    chk("SELFCHECK_JUDGEMENT_FIRES", all(f for _n, f in fires), fires)

    # ---------------- 仪器层 ----------------
    ins = rv / "instrument"
    st_json = json.loads((ins / "la_start.json").read_text(encoding="utf-8"))
    cid = st_json["response"]["result"]["structuredContent"]["captureId"]
    chk("LA_START_RETURNED_REAL_CAPTUREID", isinstance(cid, int)
        and not st_json["response"]["result"].get("isError"), "captureId=%s" % cid)
    args = st_json["args"]
    chk("LA_START_ARGS_ARE_PACKAGE_SPEC",
        args["logicDeviceConfiguration"]["digitalSampleRate"] == 1000000
        and args["captureConfiguration"]["bufferSizeMegabytes"] == 512
        and args["logicDeviceConfiguration"]["logicChannels"]["digitalChannels"] == list(range(8))
        and "manualCaptureMode" in args["captureConfiguration"], args)
    note("LA_REQUEST_VS_RECEIPT",
         "请求 512 MB; 接口回执只有 captureId=%s, 未回实际容量 => 不写'已证明容纳 512 MB', MB 也不折算成已保留样点数" % cid)
    stop_j = json.loads((ins / "la_stop.json").read_text(encoding="utf-8"))
    save_j = json.loads((ins / "la_save.json").read_text(encoding="utf-8"))
    exp_j = json.loads((ins / "la_export.json").read_text(encoding="utf-8"))
    chk("LA_ALL_CALLS_SAME_CAPTUREID", all(j["args"].get("captureId") == cid
                                         for j in (stop_j, save_j, exp_j)),
        "stop=%s save=%s export=%s" % (stop_j["args"].get("captureId"), save_j["args"].get("captureId"),
                                       exp_j["args"].get("captureId")))
    summ = json.loads((run / "field_run_summary.json").read_text(encoding="utf-8"))
    tl = {e["tag"]: e for e in summ["timeline"]}
    chk("LA_ONE_START_ONE_STOP_IN_TIMELINE",
        [e["tag"] for e in summ["timeline"]].count("A1_START_REQUESTED") == 1
        and [e["tag"] for e in summ["timeline"]].count("C1_STOP_REQUESTED") == 1
        and summ["start"]["captureId"] == cid,
        "A1=%d C1=%d 且 Start/Stop 同一 id" % (sum(1 for e in summ["timeline"] if e["tag"] == "A1_START_REQUESTED"),
                                              sum(1 for e in summ["timeline"] if e["tag"] == "C1_STOP_REQUESTED")))
    chk("LA_SAVE_USED_FILEPATH_NOT_DIRECTORY", set(save_j["args"]) == {"captureId", "filepath"}
        and set(exp_j["args"]) >= {"captureId", "directory"},
        "save args=%s export args=%s" % (sorted(save_j["args"]), sorted(exp_j["args"])))
    dev = json.loads((ins / "la_devices_masked.json").read_text(encoding="utf-8"))
    d0 = dev["response"]["result"]["structuredContent"]["devices"][0]
    chk("LA_DEVICE_IS_REAL_NOT_SIMULATED", d0["isSimulation"] is False and d0["deviceType"] == "Logic", d0)
    chk("LA_DEVICEID_MASKED_IN_REPO", str(d0["deviceId"]).startswith("MASKED_SHA12:"), d0["deviceId"])

    sal = (ins / "capture_id1_sanitized.sal").read_bytes()
    meta = json.loads(zipfile.ZipFile(io.BytesIO(sal)).read("meta.json"))["data"]
    csv = (ins / "digital.csv").read_text(encoding="utf-8").splitlines()
    hdr = csv[0].split(",")
    chk("LA_CSV_HEADER_IS_TIME_PLUS_EIGHT_CHANNELS",
        hdr[0] == "Time [s]" and hdr[1:] == ["Channel %d" % i for i in range(8)], hdr)
    note("LA_CSV_COLUMN_NAMES_ARE_GENERIC",
         "导出列名是 Channel 0..7(无信号名) => 列与 DIAG/ENN 的对应不能从导出本身得出")
    rows = [l.split(",") for l in csv[1:] if l.strip()]
    per = {hdr[i]: [int(r[i]) for r in rows] for i in range(1, len(hdr))}
    trans = {k: sum(1 for x, y in zip(v, v[1:]) if x != y) for k, v in per.items()}
    chk("LA_ZERO_TRANSITIONS_ON_EVERY_CHANNEL", all(v == 0 for v in trans.values()), trans)
    levels = "".join(str(v[0]) for v in per.values())
    chk("LA_LEVEL_PATTERN_BOTH_LEVELS_PRESENT", len(set(levels)) == 2,
        "低=%s 高=%s 全程=%s" % ([k for k, v in per.items() if v[0] == 0],
                               [k for k, v in per.items() if v[0] == 1], levels))
    ls = meta["legacySettings"]
    chk("LA_EIGHT_CHANNELS_AND_RATE_RECORDED_IN_NATIVE_META",
        len(ls["enabledChannels"]) == 8 and ls["sampleRate"]["digital"] == 1000000,
        "enabled=%d 登记采样率=%s (登记值, 不是独立测量)" % (len(ls["enabledChannels"]), ls["sampleRate"]["digital"]))
    cs = meta["captureSettings"]
    chk("LA_MODE_FREERUN_NO_TRIGGER_EVENT", cs["captureMode"] == "FreeRun" and meta["digitalTriggerTime"] == -1,
        "captureMode=%s 登记 bufferSizeMb=%s digitalTriggerTime=%s"
        % (cs["captureMode"], cs["bufferSizeMb"], meta["digitalTriggerTime"]))
    chk("LA_NATIVE_AND_CSV_ONE_SUCCESS_EACH", len(sal) > 0 and len(rows) > 0,
        "原生=%d B CSV 数据行=%d" % (len(sal), len(rows)))

    cap_start = dt.datetime.fromtimestamp(meta["captureStartTime"]["unixTimeMilliseconds"] / 1000.0
                                          + meta["captureStartTime"]["fractionalMilliseconds"] / 1e6,
                                          dt.timezone.utc)
    cap_end = iso_ns(rows[-1][0])

    def loc(tag):
        return dt.datetime.fromisoformat(tl[tag]["local"]).astimezone(dt.timezone.utc)

    d_start, d_end = loc("B1_DISPATCH_REQUESTED"), loc("B2_DISPATCH_DONE")
    ok_cov = covered(cap_start, cap_end, d_start, d_end)
    chk("LA_SPAN_COVERS_SERIAL_SESSION", ok_cov,
        "采集 %s..%s / 串口会话 %s..%s (同一台主机时钟)"
        % (cap_start.isoformat(), cap_end.isoformat(), d_start.isoformat(), d_end.isoformat()))
    win = summ["stop"]["capture_window_s"]
    chk("LA_WINDOW_WITHIN_60S", summ["stop"]["window_within_60s"] and win <= 60,
        "Start->Stop=%.2f s <= 60 s; outcome=%s" % (win, summ["outcome"]))
    chk("LA_DISPATCH_NOT_TIMED_OUT", summ["dispatch"]["timed_out"] is False and summ["dispatch"]["rc"] == 0,
        "rc=%s timed_out=%s" % (summ["dispatch"]["rc"], summ["dispatch"]["timed_out"]))
    shift = dt.timedelta(seconds=3600)
    fires2 = [("coverage_shifted_session", covered(cap_start, cap_end, d_start + shift, d_end + shift) is False),
              ("coverage_truncated_start", covered(cap_start + shift, cap_end, d_start, d_end) is False)]
    chk("SELFCHECK_COVERAGE_GATE_FIRES", all(f for _n, f in fires2), fires2)

    # ---------------- 接线解释 ----------------
    prev = (repo / "reports/reviews/phase8-m8-r7f2b-safety-print-fix/field_r7f5a/la_digital_raw.csv")
    prev_rows = [l.split(",") for l in prev.read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    prev_pat = "".join(prev_rows[0][1:9]) if prev_rows else ""
    smoke = (repo / "reports/reviews/phase8-m8-r7f2b-safety-print-fix/la_local_r7f5a/la_smoke_capture_digital.csv")
    smoke_rows = [l.split(",") for l in smoke.read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
    smoke_pat = "".join(smoke_rows[0][1:9]) if smoke_rows else ""
    chk("LA_PATTERN_MATCHES_PREV_CONNECTED_FIELD", levels == prev_pat,
        "本轮=%s 上一场(已接线 R7F5A)=%s" % (levels, prev_pat))
    chk("LA_PATTERN_MATCHES_UNCONNECTED_BASELINE", levels == smoke_pat,
        "本轮=%s 探头未接基线=%s => 电平层无法区分'接线正确且静默'与'未接触/无共地'" % (levels, smoke_pat))
    note("WIRING_INTERPRETATION_BOUNDARY",
         "本轮零运动/零注入/零参考电平切换, 没有事件对照 => 不宣布目标接触通路已被电气证明, 也不判操作者接错")
    note("CHANNEL_MAPPING_STATUS",
         "GUI 行标签与导出列名均为 Channel 0..7(无信号名); 操作者暂定 CH0=DIAG/CH1=ENN 仍是申报级 => CHANNEL_MAPPING_UNRESOLVED")
    note("GND_STATUS", "共地/接触本轮未观察 => UNKNOWN(不写成 PASS, 也不因此扩大动作)")

    # ---------------- 边界与未验证 ----------------
    note("NOT_ON_BOARD_NEW_IMAGES",
         "板上仍是历史 b502 %s(本轮未下载/未回读/未复位); 23691e8d/165d8bdc/df7584c3 保持 NOT_ON_BOARD"
         % ident["image"]["sha256"][:8])
    note("CLAIMS_KEPT_FALSE",
         "polarity_measured=false core_protection_verified=false; 真实 DIAG 故障边沿/停 STEP/保护触发/恢复全部未验证")
    note("DEVICE_ACTIONS_THIS_ROUND",
         "COM5 开口=1 dispatch=1 LA Start=1 Stop=1 原生保存=1 CSV 导出=1; "
         "下载/回读/DAP/复位/额外上电/apply/arm/start/运动/zero/learn/commit/配置保存/清日志/重试=0")

    n = len([l for l in OUT if l.startswith(("PASS", "FAIL"))])
    OUT.append("TOTAL=%d FAIL=%d %s" % (n, len(FAILS), FAILS))
    txt = "\n".join(OUT) + "\n"
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(txt, encoding="utf-8", newline="\n")
    if a.json:
        a.json.write_text(json.dumps({"task_id": plan["task_id"], "run_id": run.name,
                                      "checks": SUM, "fails": FAILS, "total_predicates": n},
                                     ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(txt)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
