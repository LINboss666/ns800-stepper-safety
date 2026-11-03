#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R6B 修前/修后的**同一输入**对照: 复审附录 B 探针分别打在 aab19ff 的执行器字节与本轮修后的字节上。

为什么这样取"修前": 任务书 §7.2.1 要求保留修前失败原文, §4/§5 的失败是**执行器自身**的行为,
所以对照的两边只能是两份执行器字节 —— 同一份探针、同一份临时账、同一套内存端口/动作替身。
基线那份从 git 对象库直接取 blob(`git cat-file`), 不碰工作树、不改历史提交。

三条判据(原文都在下面的输出里, 不用汇总标签代替):
  B1  pending[-1].name=[]      修前=TypeError 逸出/只发 1 条/端口不关  修后=具名拒绝/三条必要撤能都发/关闭
  B2a 内存对象≠批准文件         修前=未批准 argv 到达 download 且扣 1   修后=PLAN_NOT_FROM_APPROVED_SNAPSHOT/零调用
  B2b CLI 读取与哈希之间换文件   修前=rc=0 且未批准 argv 到达后端        修后=身份不符 rc≠0 且零调用

设备动作 0: 全程内存端口 + 内存动作后端, 不起 pyOCD/Keil/串口, 不连 DAP, 不碰设备。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                      # 仓库根 D:/ns800
BASE = "aab19ff5093e177946ce957fbf2bd506e1f63ce5"
FIELD = "reports/reviews/phase8-m8-relative-protection-r3/field"
# 附录 B 声明的最小输入: 执行器 + 自检夹具 + 计划/数据表 + 旧 r3 归档 BIN(身份里 image 要现算)
NEED = [FIELD + "/m8r3_serial_chain.py", FIELD + "/m8r3_field_selftest.py",
        FIELD + "/plan.json", FIELD + "/rows.json",
        "reports/firmware/phase8-m8-relative-protection-r3/rt-thread-motor-m1-candidate.bin"]
PROBE = HERE / "independent_probes.py"      # 复审附录 B 逐字节原样入库的副本


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def blob(rev, rel, dst):
    r = subprocess.run(["git", "-C", str(REPO), "cat-file", "blob", "%s:%s" % (rev, rel)],
                       capture_output=True)
    if r.returncode:
        raise SystemExit("R6B_PROBE_FAIL|基线里取不到 %s:%s" % (rev[:12], rel))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(r.stdout)
    return r.stdout


def run_probe(repo_root, tag):
    """跑附录 B 探针(它自己只用 %TEMP% 下的一次性目录), 返回它打印的 JSON。"""
    out = subprocess.run([sys.executable, str(PROBE), str(repo_root)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit("R6B_PROBE_FAIL|%s rc=%d|%s" % (tag, out.returncode, out.stderr[-400:]))
    return json.loads(out.stdout)


def pick(doc, probe):
    hits = [r for r in doc["results"] if r["probe"] == probe]
    return hits[0] if hits else None


def main():
    work = Path(tempfile.mkdtemp(prefix="ns800-r6b-ba-"))       # 仓库外
    base = work / "base"
    print("R6B_BEFORE_AFTER 输入 | 基线 blob=%s | 工作树=%s | 探针=%s(%d B)" %
          (BASE[:12], REPO, PROBE.name, PROBE.stat().st_size))
    for rel in NEED:
        b = blob(BASE, rel, base / rel)
        disk = REPO / rel
        print("  BASE|%s|%d B|sha=%s…|与工作树当前字节%s" %
              (Path(rel).name, len(b), hashlib.sha256(b).hexdigest()[:12],
               "相同" if disk.exists() and disk.read_bytes() == b else "**已不同(本轮改动)**"))
    before = run_probe(base, "before")
    after = run_probe(REPO, "after")
    shutil.rmtree(work, ignore_errors=True)

    checks = []

    def ck(name, ok, detail):
        checks.append((name, ok, detail))
        print("%-5s|%s|%s" % ("OK" if ok else "FAIL", name, detail))

    b1b, b1a = pick(before, "pending-name-list"), pick(after, "pending-name-list")
    need = ["motor_stop", "motor_disarm", "motor_status"]
    ck("B1_修前_TypeError逸出且必要撤能没发",
       (b1b["raised"] or "").startswith("TypeError:unhashable type: 'list'") and
       b1b["tx"] == ["motor_start"] and b1b["closed"] is False and
       [e["kind"] for e in b1b["cleanup_events"]] == ["CLEANUP_FRAME_EXCEPTION"] and
       b1b["result"] is None,
       "raised=%s tx=%s closed=%s 框架异常=%d result=%s" %
       (b1b["raised"], ",".join(b1b["tx"]), b1b["closed"], len(b1b["cleanup_events"]), b1b["result"]))
    ck("B1_修后_同输入具名拒绝且三条必要撤能都发",
       b1a["raised"] is None and all(c in b1a["tx"] for c in need) and b1a["closed"] is True and
       b1a["cleanup_events"] == [] and b1a["result"]["success"] is False and
       len(b1a["result"]["cleanup_overruns"]) == 3 and
       all("APPROVAL_LEDGER_CORRUPT" in x for x in b1a["result"]["cleanup_overruns"]),
       "raised=%s tx=%s closed=%s success=%s 披露=%s" %
       (b1a["raised"], ",".join(b1a["tx"]), b1a["closed"], b1a["result"]["success"],
        ";".join(b1a["result"]["cleanup_overruns"])))
    ck("B1_修后_正控未变(干净账仍是成功)",
       pick(after, "clean-control")["result"]["success"] is True and
       pick(after, "clean-control")["closed"] is True,
       "clean-control success=%s closed=%s" %
       (pick(after, "clean-control")["result"]["success"], pick(after, "clean-control")["closed"]))

    b2b, b2a = pick(before, "in_memory_plan_differs_from_approved_file"), \
        pick(after, "in_memory_plan_differs_from_approved_file")
    ck("B2a_修前_未批准的argv到达download并扣额度",
       b2b["memory_backend_calls"] and
       b2b["memory_backend_calls"][0]["argv"] == ["MEMORY_ONLY_UNAPPROVED_ARGUMENTS"] and
       b2b["status"] == "OK" and (b2b.get("spent") or {}).get("download") == 1 and
       b2b["approved_file_unchanged"] is True,
       "status=%s 调用=%s download消耗=%s 盘上计划未变=%s" %
       (b2b["status"], json.dumps(b2b["memory_backend_calls"], ensure_ascii=False)[:96],
        (b2b.get("spent") or {}).get("download"), b2b["approved_file_unchanged"]))
    ck("B2a_修后_同输入具名拒绝且后端零调用",
       b2a["status"] == "REFUSED" and b2a["code"] == "PLAN_NOT_FROM_APPROVED_SNAPSHOT" and
       b2a["memory_backend_calls"] == [] and (b2a.get("spent") in (None, {}) or
                                              (b2a.get("spent") or {}).get("download", 0) == 0),
       "status=%s code=%s 调用=%s spent=%s" %
       (b2a["status"], b2a["code"], b2a["memory_backend_calls"], b2a.get("spent")))

    c2b, c2a = pick(before, "cli_plan_read_then_hash_interleaving"), \
        pick(after, "cli_plan_read_then_hash_interleaving")
    # 修后的拒绝身份要从探针留下的 stdout 原文里读, 不拿"rc 非零"当结论
    try:
        cli_out = Path(c2a["stdout"]).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        cli_out = "<读不到|%s>" % e
    ident_after = "APPROVAL_IDENTITY_MISMATCH" in cli_out
    ck("B2b_修前_CLI读取换文件后rc0且argv到达后端",
       c2b["rc"] == 0 and c2b["memory_backend_calls"] == [["MEMORY_ONLY_UNAPPROVED_ARGUMENTS"]] and
       c2b["file_matches_approval"] is True,
       "rc=%s 调用=%s 盘上=批准那份=%s" %
       (c2b["rc"], c2b["memory_backend_calls"], c2b["file_matches_approval"]))
    ck("B2b_修后_同交错下身份不符拒发且后端零调用",
       c2a["rc"] != 0 and c2a["memory_backend_calls"] == [] and ident_after,
       "rc=%s 调用=%s 拒绝身份APPROVAL_IDENTITY_MISMATCH=%s; 注: 附录B把交错安排在 read_text 上, "
       "修后执行器读计划用的是 read_bytes(单次读盘), 因此这里换文件未被触发(触发=%s/盘上=Q那份=%s) "
       "—— 在 read_bytes 上安排同一交错的判据在自检 FS121" %
       (c2a["rc"], c2a["memory_backend_calls"], ident_after, c2a["file_restored_before_hash"],
        c2a["file_matches_approval"]))

    ck("R3字节门正控未受影响(554B 与 1176/1179/1188 矩阵)",
       pick(after, "clean_utf8_crosses_512B")["passed"] is True and
       [m["errors"] for m in pick(after, "cleanup_utf8_crosses_512B")["matrix"]] ==
       [[], ["motor_status:NUL_BYTE_IN_RESPONSE"], ["motor_status:ILLEGAL_BYTES_IN_RESPONSE"],
        ["motor_status:DANGER_TEXT_IN_STREAM"]],
       "554B通过=%s 矩阵=%s" % (pick(after, "clean_utf8_crosses_512B")["passed"],
                                json.dumps([m["errors"] for m in
                                            pick(after, "cleanup_utf8_crosses_512B")["matrix"]],
                                           ensure_ascii=False)))

    print("\n---- 修前(aab19ff 的 EXEC 字节)原文 ----")
    print(json.dumps(before["results"][:5], ensure_ascii=False, indent=1))
    print("\n---- 修后(本轮 EXEC 字节)原文 ----")
    print(json.dumps(after["results"][:5], ensure_ascii=False, indent=1))

    bad = [n for n, ok, _d in checks if not ok]
    print("\nRESULT %s (checks=%d fails=%d)" %
          ("BEFORE_AFTER_OK" if not bad else "PROBLEMS:" + ",".join(bad), len(checks), len(bad)))
    print("device_actions=0|serial_ports=0|dap=0|downloads=0|flash=0|readback=0|reset=0|"
          "learns=0|zeros=0|motor_starts=0(仅内存端口)|config_saves=0")
    return 1 if bad else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
