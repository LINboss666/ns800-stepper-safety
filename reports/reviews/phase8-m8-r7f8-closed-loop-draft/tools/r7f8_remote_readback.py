# -*- coding: utf-8 -*-
"""R7F8 草案核对轮推送后的远端逐 blob 回读(仓外裸仓, 不碰工作树)。

核对四件事:
  1) tip 与暂存清单逐条: 远端 blob 的 sha256 == 清单登记值 == 磁盘字节;
  2) 草案原件与合并版的身份(原件字节必须仍是收到时那份), 以及两份交接件的远端散列;
  3) 产品子树与在板镜像散列未被本轮动过;
  4) run_record 各段(§16..§22)在远端恰好一次(append-only 的机器证据)。

报告写到仓外 D:/ns800_r7f8_readback/remote_readback_r7f8.txt —— 不入清单, 避免自引用循环。
用法: python tools/r7f8_remote_readback.py --repo D:/ns800   (环境变量 RBI_TIP 期望 tip, RBI_ORIG 草案原件路径)
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys

R7F8 = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F7 = "reports/reviews/phase8-m8-r7f7-newimage-static"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
FWD = "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn"
MANIFESTS = [R7F8 + "/ROUND_SHA256SUMS.txt", R7F2B + "/SHA256SUMS.txt", R7F7 + "/ROUND_SHA256SUMS.txt"]
EXTRA = [R7F8 + "/A_DRAFT_ORIGINAL.md", R7F8 + "/R7F8_DRAFT_MERGED.md", R7F8 + "/DRAFT_REVIEW.md",
         "reports/agent_handoffs/phase8-m8-r7f8-closed-loop-draft-offline.md",
         "reports/agent_handoffs/phase8-m8-r7f8-closed-loop-draft-offline.json"]
BIN_SHA = "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963"
AXF_SHA = "0f421595ac940863c4d38a2791f31bc8811fc3b8fa40062782f2ac7505c317f6"
TREE = "32470c7ed44ef6ef5f762490a5416912a336cdd9"

out, fails = [], []


def note(name, ok, detail=""):
    out.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def info(name, detail):
    out.append("%s|INFO|%s" % (name, detail))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main() -> int:
    repo = pathlib.Path(sys.argv[sys.argv.index("--repo") + 1]).resolve()
    work = pathlib.Path(os.environ.get("RBI_ROOT", "D:/ns800_r7f8_readback"))
    work.mkdir(parents=True, exist_ok=True)
    bare = work / "remote.bare.git"
    if not bare.exists():
        subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, check=True)
    env = dict(os.environ, HTTP_PROXY="http://127.0.0.1:7892", HTTPS_PROXY="http://127.0.0.1:7892")
    f = subprocess.run(["git", "--git-dir", str(bare), "fetch", "--force",
                        "https://github.com/LINboss666/ns800-stepper-safety.git",
                        "refs/heads/feature/m8-r7d1-async-learn-offline:refs/heads/dev"],
                       capture_output=True, env=env)
    note("FETCH", f.returncode == 0, (f.stderr.decode("utf-8", "replace")[-90:] or "rc=0"))
    tip = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "refs/heads/dev"],
                         capture_output=True).stdout.decode().strip()
    want = os.environ.get("RBI_TIP", "")
    note("TIP", bool(tip) and (not want or tip == want), "%s 期望=%s" % (tip[:12], want[:12]))

    # 逐件 cat-file -p: 不做批量读取(批量输出的分隔与内容里的换行难以可靠切分, 留在此处只会误导)
    blobs = {}
    for s in MANIFESTS + EXTRA:
        r = subprocess.run(["git", "--git-dir", str(bare), "cat-file", "-p", "%s:%s" % (tip, s)],
                           capture_output=True)
        blobs[s] = r.stdout if r.returncode == 0 else None
    note("REMOTE_BLOBS_READ", all(blobs.get(s) is not None for s in MANIFESTS + EXTRA),
         "取到 %d/%d 件" % (sum(1 for v in blobs.values() if v is not None), len(blobs)))

    tot = ok = 0
    for man in MANIFESTS:
        if blobs.get(man) is None:
            note("MANIFEST_BLOB|%s" % man, False, "远端取不到清单")
            continue
        bad = []
        for ln in blobs[man].decode("utf-8", "replace").splitlines():
            m = re.match(r"^([0-9a-f]{64})  (.+)$", ln)
            if not m:
                continue
            want_sha, rel = m.group(1), m.group(2)
            r = subprocess.run(["git", "--git-dir", str(bare), "cat-file", "-p", "%s:%s" % (tip, rel)],
                               capture_output=True)
            tot += 1
            if r.returncode != 0:
                bad.append("missing " + rel)
                continue
            if sha(r.stdout) != want_sha:
                bad.append("mismatch " + rel)
            elif (repo / rel).read_bytes() != r.stdout:
                bad.append("disk_differs " + rel)
            else:
                ok += 1
        note("MANIFEST_BLOBS|%s" % man, not bad, "不符=%d %s" % (len(bad), bad[:3]))
    info("BLOB_TOTAL", "逐条复算 %d 件, 全等 %d 件" % (tot, ok))

    if blobs.get(EXTRA[0]) is not None:
        note("COMPARATOR_FIRES", sha(blobs[EXTRA[0]]) != BIN_SHA,
             "把草案原件的散列拿去跟镜像散列比必然判不符(比较器活着)")
    orig = blobs.get(R7F8 + "/A_DRAFT_ORIGINAL.md")
    note("DRAFT_ORIGINAL_UNCHANGED", orig is not None and len(orig) == 24780 and sha(orig)[:12] == "b572c1928f60",
         "收到的草案原件入库字节仍是 %s B / b572c1928f60…" % (len(orig or b"")))
    live = os.environ.get("RBI_ORIG", "")
    if live and pathlib.Path(live).exists():
        note("MERGED_MATCHES_LIVE_COPY", blobs.get(R7F8 + "/R7F8_DRAFT_MERGED.md") == pathlib.Path(live).read_bytes(),
             "入库合并版与操作者侧当前维护版逐字节相等")
    else:
        info("MERGED_MATCHES_LIVE_COPY", "未给 RBI_ORIG 或该路径不可读 => 只对入库副本负责")
    mrg = blobs.get(R7F8 + "/R7F8_DRAFT_MERGED.md")
    note("MERGED_HAS_REVISION_AND_ORDER", mrg is not None and "### 1.2" in mrg.decode("utf-8") and
         "并入操作者补充" in mrg.decode("utf-8") and "确认 arm 成功" in mrg.decode("utf-8"),
         "合并版远端含 §1.2/修订记录/更正后的起转顺序")
    for name, rel in (("BIN", FWD + "/rt-thread-motor-m1-candidate.bin"), ("AXF", FWD + "/rt-thread-motor-m1.axf")):
        r = subprocess.run(["git", "--git-dir", str(bare), "cat-file", "-p", "%s:%s" % (tip, rel)],
                           capture_output=True)
        note("UNCHANGED_" + name, r.returncode == 0 and sha(r.stdout) == (BIN_SHA if name == "BIN" else AXF_SHA),
             "%s 仍是本轮绑定的散列" % name)
    tr = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "%s:%s" % (tip, "rt-thread-master")],
                        capture_output=True)
    note("PRODUCT_TREE", tr.stdout.decode().strip() == TREE, "产品子树 %s" % tr.stdout.decode().strip()[:12])
    rr = subprocess.run(["git", "--git-dir", str(bare), "cat-file", "-p", "%s:%s/run_record.md" % (tip, R7F2B)],
                        capture_output=True).stdout.decode("utf-8", "replace")
    for sec in ("## 16", "## 17", "## 18", "## 19", "## 20", "## 21", "## 22"):
        note("RUN_RECORD_SECTION|%s" % sec, rr.count("\n" + sec) == 1, "命中=%d" % rr.count("\n" + sec))

    rep = work / "remote_readback_r7f8.txt"
    summary = "R7F8_READBACK_SUMMARY|fails=%d|%s" % (len(fails), ",".join(fails) if fails else "none")
    rep.write_text("\n".join(out + [summary]) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(out[-14:]))
    print(summary)
    print("REPORT|%s" % rep)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
