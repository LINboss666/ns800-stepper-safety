# -*- coding: utf-8 -*-
"""R7F6 现场证据归档(仓外原件 -> 仓库新目录), 带隐私脱敏与逐字节留证。

规则:
  - 五份响应原字节、执行器 stdout/stderr/台账 一律**原样**入库, 不修剪不重排。
  - 分析仪调用件里的 deviceId(16 位十六进制) 与原生 .sal 内 meta.json 的 legacyDevice.deviceId
    (20 位十进制序列号) 不入公开仓库: JSON 里换成 MASKED_SHA12:<前12位>, .sal 另存一份
    "只替换该字段"的脱敏副本并写明原字节/原散列; 原件留在仓外不动。
  - dispatch.meta.json 的 argv 含批准令牌原文 -> 用 <TOKEN> 占位, 令牌只留字节数与散列。
  - 每个入库件都算 sha256 写进 instrument_manifest.json, 供独立复算工具比对。

用法: python tools/r7f6_field_archive.py --repo D:/ns800 --src <仓外运行根> --instr <仓外仪器根>
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import shutil
import sys
import zipfile

LINES = []


def note(s):
    LINES.append(s)
    print(s)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def mask_hex16(obj, path):
    """把 deviceId 字段换成 sha12 掩码; 返回命中次数。"""
    hits = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == "deviceId" and isinstance(v, str) and len(v) >= 8:
                obj[k] = "MASKED_SHA12:%s" % sha(v.encode())[:12]
                hits += 1
            else:
                hits += mask_hex16(v, path)
    elif isinstance(obj, list):
        for v in obj:
            hits += mask_hex16(v, path)
    return hits


def mask_numeric_serial(obj):
    hits = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == "deviceId" and isinstance(v, str) and v.isdigit():
                obj[k] = "MASKED_SHA12:%s" % sha(v.encode())[:12]
                hits += 1
            else:
                hits += mask_numeric_serial(v)
    elif isinstance(obj, list):
        for v in obj:
            hits += mask_numeric_serial(v)
    return hits


def scrub(obj, secrets):
    """把**任何**字符串里出现的明文仪器标识(包括嵌在 JSON-in-string 的 text 字段里)换成掩码。
    key 级掩码不够: 执行器/接口的回执把 deviceId 原样写在 content[].text 与时间线 args 里。"""
    n = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if isinstance(v, str):
                new = v
                for s in secrets:
                    if s in new:
                        new = new.replace(s, "MASKED_SHA12:%s" % sha(s.encode())[:12])
                        n += 1
                if new != v:
                    obj[k] = new
            else:
                n += scrub(v, secrets)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                new = v
                for s in secrets:
                    if s in new:
                        new = new.replace(s, "MASKED_SHA12:%s" % sha(s.encode())[:12])
                        n += 1
                if new != v:
                    obj[i] = new
            else:
                n += scrub(v, secrets)
    return n


def put(dst: pathlib.Path, data: bytes, reg):
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    reg.append({"path": dst.relative_to(REPO).as_posix(), "bytes": len(data), "sha256": sha(data)})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--src", required=True, type=pathlib.Path, help="仓外 run_field.py 根")
    ap.add_argument("--instr", required=True, type=pathlib.Path, help="仓外仪器件根(同 src)")
    a = ap.parse_args()
    global ROOT, REPO
    repo = a.repo.resolve()
    REPO = repo
    ROOT = repo / "reports/reviews/phase8-m8-r7f6-la-path-static"
    src = a.src.resolve()
    reg = []
    fails = []

    run_dirs = sorted(p for p in src.glob("run-*") if p.is_dir())
    if len(run_dirs) != 1:
        note("ARCHIVE_REFUSED|run-* 目录数=%d (必须恰好一次现场运行)" % len(run_dirs))
        return 2
    run = run_dirs[0]
    note("ARCHIVE_RUN|%s" % run.name)

    # 明文仪器标识只在内存里存在(从仓外原件读出), 不写进任何入库件, 也不硬编码进本脚本
    _dev0 = json.loads((src / "la_devices.json").read_text(encoding="utf-8"))
    _sal0 = json.loads(zipfile.ZipFile(io.BytesIO((src / "native" / "capture_id1.sal").read_bytes()))
                       .read("meta.json"))
    SECRETS = [_dev0["response"]["result"]["structuredContent"]["devices"][0]["deviceId"],
               str(_sal0["data"]["legacyDevice"]["deviceId"])]
    note("SCRUB_SECRETS|两类明文标识长度=%d/%d (值不入仓不硬编码), 逐字符串替换为 sha12 掩码"
         % (len(SECRETS[0]), len(SECRETS[1])))

    # 1) 五份响应原字节 + 会话台账 + dispatch 三件
    for f in sorted((run / "S_S1").iterdir()):
        put(ROOT / "field_run" / run.name / "S_S1" / f.name, f.read_bytes(), reg)
    for name in ("dispatch.stdout.txt", "dispatch.stderr.txt", "dispatch_ledger.jsonl"):
        put(ROOT / "field_run" / run.name / name, (run / name).read_bytes(), reg)
    meta = json.loads((run / "dispatch.meta.json").read_text(encoding="utf-8"))
    argv = meta.get("argv") or []
    masked = []
    prev = ""
    tok_hits = 0
    for x in argv:
        if prev in ("--approval-token", "--expected-token"):
            masked.append("<TOKEN>")
            tok_hits += 1
        else:
            masked.append(x)
        prev = x
    meta["argv_masking"] = ("--approval-token/--expected-token 的原文不入仓; 令牌=操作者要求执行本包的原话, "
                           "仓外 C:/m8r_approvals/token_r7f6.txt, 登记 token_bytes/token_sha256")
    meta["argv"] = masked
    note("MASK_SCRUB|dispatch.meta 字符串级命中=%d" % scrub(meta, SECRETS))
    put(ROOT / "field_run" / run.name / "dispatch.meta.json",
        (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), reg)
    note("MASK_ARGV_TOKEN|替换=%d 处 token_bytes=%s token_sha=%s"
         % (tok_hits, meta.get("token_bytes"), str(meta.get("token_sha256"))[:12]))
    if tok_hits != 2:
        fails.append("MASK_ARGV_TOKEN")

    # 2) 开账三件
    for name in ("init.stdout.txt", "init.stderr.txt", "init.meta.json"):
        put(ROOT / "field_init" / name, (src / name).read_bytes(), reg)

    # 3) 仪器调用件(脱敏) + 工具清单
    for tag in ("start", "stop", "save", "export"):
        p = src / ("la_%s.json" % tag)
        d = json.loads(p.read_text(encoding="utf-8"))
        n = mask_hex16(d, tag)
        m = scrub(d, SECRETS)
        d["masking_note"] = ("args.deviceId 以及**任何字符串里**出现的仪器标识(含接口回执 content[].text "
                             "内嵌的 JSON 原文)已换成 sha256 前 12 位掩码; 其余字段与原始回执逐字未动。"
                             "未脱敏原件留在仓外 %s" % p.as_posix())
        put(ROOT / "instrument" / ("la_%s.json" % tag),
            (json.dumps(d, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), reg)
        note("MASK_DEVICEID|%s key级=%d 字符串级=%d" % (tag, n, m))
        if n != 1 and tag == "start":
            fails.append("MASK_DEVICEID_" + tag)
    dev = json.loads((src / "la_devices.json").read_text(encoding="utf-8"))
    nd = mask_hex16(dev, "devices") + scrub(dev, SECRETS)
    note("MASK_DEVICEID|devices key级+字符串级合计=%d" % nd)
    put(ROOT / "instrument" / "la_devices_masked.json",
        (json.dumps(dev, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), reg)
    tl = json.loads((src / "la_tools_list_raw.json").read_text(encoding="utf-8"))
    names = [t["name"] for t in tl["result"]["tools"]]
    put(ROOT / "instrument" / "la_tools_list_names.txt",
        ("tools/list 只读拉取, 工具名=%d\n%s\n" % (len(names), "\n".join(names))).encode("utf-8"), reg)

    # 4) 原生 .sal: 脱敏副本 + 原字节登记
    sal = src / "native" / "capture_id1.sal"
    raw = sal.read_bytes()
    zin = zipfile.ZipFile(io.BytesIO(raw))
    meta_json = json.loads(zin.read("meta.json"))
    nser = mask_numeric_serial(meta_json["data"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zo:
        for it in zin.infolist():
            data = zin.read(it.filename)
            if it.filename == "meta.json":
                data = json.dumps(meta_json, ensure_ascii=False).encode("utf-8")
            zo.writestr(it.filename, data)
    san = buf.getvalue()
    zchk = zipfile.ZipFile(io.BytesIO(san))
    same = [n for n in zin.namelist() if n != "meta.json" and zchk.read(n) != zin.read(n)]
    note("SAL_SANITIZE|非 meta 条目逐字节不变=%s (条目=%d)" % (not same, len(zin.namelist()) - 1))
    if same:
        fails.append("SAL_ENTRIES_CHANGED")
    put(ROOT / "instrument" / "capture_id1_sanitized.sal", san, reg)
    note("SAL_SANITIZE|meta.json legacyDevice.deviceId 替换=%d 原字节=%d 原 sha=%s 脱敏字节=%d 脱敏 sha=%s"
         % (nser, len(raw), sha(raw), len(san), sha(san)))
    if nser != 1:
        fails.append("SAL_SANITIZE")
    put(ROOT / "instrument" / "digital.csv", (src / "csv" / "digital.csv").read_bytes(), reg)
    put(ROOT / "instrument" / "SAL_MASKING_NOTE.txt",
        ("capture_id1_sanitized.sal 是把原生件的 meta.json 里 legacyDevice.deviceId(20 位十进制仪器序列号) "
         "换成 sha12 掩码后**重建的 zip**; 其余 8 个 digital-*.bin 与 trigger-store.bin 条目逐字节未动(工具已核)。\n"
         "原生未脱敏件留在仓外: %s\n原字节=%d 原 sha256=%s\n"
         % (sal.as_posix(), len(raw), sha(raw))).encode("utf-8"), reg)
    for png in ("la_window_before_start.png", "la_window_running.png"):
        p = src / png
        if p.exists():
            put(ROOT / "instrument" / png, p.read_bytes(), reg)
    # 采集元数据摘录(脱敏后), 便于人读
    keep = {k: meta_json["data"].get(k) for k in
            ("name", "captureStartTime", "captureSettings", "legacySettings", "digitalTriggerTime",
             "timeManager")}
    keep["legacyDevice_isSimulation"] = meta_json["data"].get("legacyDevice", {}).get("isSimulation")
    keep["legacyDevice_name"] = meta_json["data"].get("legacyDevice", {}).get("name")
    keep["note"] = "captureSettings/legacySettings 为原生文件内**已登记的设置**, 不是物理缓冲或采样率的独立测量"
    put(ROOT / "instrument" / "capture_meta_extract.json",
        (json.dumps(keep, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), reg)

    # 5) 离线前置件
    summ = json.loads((src / "field_run_summary.json").read_text(encoding="utf-8"))
    ns = scrub(summ, SECRETS)
    summ["masking_note"] = "start.args.deviceId 等字符串里的仪器标识已换 sha12 掩码; 时刻与回执原文未动"
    put(ROOT / "field_run" / run.name / "field_run_summary.json",
        (json.dumps(summ, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), reg)
    note("ARCHIVE_TIMELINE|field_run_summary.json 事件=%d 掩码替换=%d"
         % (len(summ["timeline"]), ns))
    pre = pathlib.Path("C:/r7f6_field/proof/preflight_r7f6.txt")
    if pre.exists():
        put(ROOT / "proof" / "preflight_r7f6.txt", pre.read_bytes(), reg)
    for name in ("rehearsal_stub.stdout.txt", "rehearsal_stub.stderr.txt"):
        p = src / name
        if p.exists():
            put(ROOT / "proof" / name, p.read_bytes(), reg)
    put(ROOT / "proof" / "REHEARSAL_NOTE.txt",
        ("本目录两份 rehearsal_stub.* 是 A 定稿计划在本机的**离线预跑**(transport=stub backend=sim profile=healthy),\n"
         "作用是确认参数与路径可用, 不产生任何硬件结果, 也不占本轮任何额度。\n"
         "真实现场件在 field_run/ 与 instrument/。\n").encode("utf-8"), reg)

    # 6) 持久台账副本(其中无私有序列号)
    led = pathlib.Path("C:/m8r_approvals/approval_M8R7F6-b502-la-path-static-20261105.json")
    put(ROOT / "ledgers" / led.name, led.read_bytes(), reg)

    # 脱敏完整性硬门: 刚写进仓库的每一件都不许再含任何明文标识
    resid = []
    for rec in reg:
        data = (repo / rec["path"]).read_bytes()
        for s in SECRETS:
            if s.encode() in data:
                resid.append("%s 仍含 %d 字符标识" % (rec["path"], len(s)))
    note("SCRUB_RESIDUE|入库件仍含明文标识=%d %s" % (len(resid), resid[:6]))
    if resid:
        fails.append("SCRUB_RESIDUE")

    man = {"task_id": "M8R7F6-b502-la-path-static-20261105",
           "run_root_out_of_repo": src.as_posix(),
           "run_id": run.name,
           "files": reg,
           "privacy": {"la_deviceId_hex16": "masked in every committed copy",
                       "la_serial_numeric": "masked in .sal copy; original kept out of repo",
                       "approval_token": "argv masked; only bytes+sha registered",
                       "known_historical_leak": "上一轮 R7F5A 入库的 field_r7f5a/la_capture_id8.sal 的 meta.json "
                                                "含同一台仪器的 20 位十进制序列号(本轮才发现, 冻结件不回改)"}}
    put(ROOT / "instrument_manifest.json",
        (json.dumps(man, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), reg)
    note("ARCHIVE_FILES|%d 件入库" % len(reg))
    note("ARCHIVE_FAILS|%d %s" % (len(fails), fails))
    (ROOT / "proof" / "archive_notes.txt").write_text("\n".join(LINES) + "\n", encoding="utf-8", newline="\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
