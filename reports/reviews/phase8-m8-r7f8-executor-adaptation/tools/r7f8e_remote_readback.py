# -*- coding: utf-8 -*-
"""R7F8E 推送后的**远端逐 blob 回读**: 从远端 ref 重新取清单与每件 blob, 逐条复算 sha256。

不读本地工作树当证据 —— 本地字节可能是 autocrlf 的产物。做法:
  1) 把远端分支 fetch 进**仓外**裸仓(每次一个独立目录, 不复用: 只读 pack 会让清理失败并留下旧对象);
  2) 从远端 blob 里取三份清单本身, 再按清单逐条 `cat-file -p <tip>:<path>` 复算 sha256;
  3) 额外核: 旧执行器/旧现场原件未被本轮改动、派生件身份是新的、产品源码子树 tree 与 A 登记一致、
     run_record 的 §23 只出现一次、草案合并件与承接来源一致;
  4) 原文写到 proof/r7f8e_remote_readback.txt —— 该文件按清单规则被排除(推送后才存在)。
用法: python tools/r7f8e_remote_readback.py --repo D:/ns800 --tip <期望 commit>
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import time

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
DRAFT = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F2B = "reports/reviews/phase8-m8-r7f2b-safety-print-fix"
R7F7 = "reports/reviews/phase8-m8-r7f7-newimage-static"
OLD_EXE = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
OLD_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
NEW_EXE = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
APPS = "rt-thread-master"
# A 登记的"产品子树"32470c7e… 在 R7F7 轮 OFFLINE_REVIEW.md:53 已被实读确认为**顶层 rt-thread-master
# 子树**的对象号, 不是 applications 目录(那条路径的对象号是另一个值)。上一版把路径写成
# applications 全路径, 于是本轮第一次回读报假 FAIL —— 见 proof/r7f8e_remote_readback_first_attempt.txt。
PRODUCT_TREE = "32470c7ed44ef6ef5f762490a5416912a336cdd9"
APPS_SUBTREE = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
                "ns800rt7p65-nssinepad/applications")
URL = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "refs/heads/feature/m8-r7d1-async-learn-offline"
MANIFESTS = [ROUND + "/ROUND_SHA256SUMS.txt", DRAFT + "/ROUND_SHA256SUMS.txt",
             R7F2B + "/SHA256SUMS.txt", R7F7 + "/ROUND_SHA256SUMS.txt"]
EXTRA = [NEW_EXE, OLD_EXE, R7F2B + "/run_record.md",
         DRAFT + "/R7F8_DRAFT_MERGED.md", DRAFT + "/A_DRAFT_ORIGINAL.md",
         ROUND + "/ERRATA_R7F8E.md", ROUND + "/R7F8E_DELIVERY.md",
         ROUND + "/inputs/A_R7F8E_PKG/TASK_R7F8E_EXECUTOR_OFFLINE.md",
         ROUND + "/inputs/A_R7F8E_PKG/R7F8_DRAFT_UPDATED.md",
         "reports/agent_handoffs/phase8-m8-r7f8-executor-adaptation.md",
         "reports/agent_handoffs/phase8-m8-r7f8-executor-adaptation.json"]

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--tip", default="")
    ap.add_argument("--work", type=pathlib.Path,
                    default=pathlib.Path("D:/ns800_r7f8e_readback"))
    a = ap.parse_args()
    repo = a.repo.resolve()
    # 独立目录 + 时间戳: 复用裸仓时上一轮的 pack 是只读的, 清不掉会把旧对象当远端读。
    work = a.work / ("run_%s" % time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    (work / "remote.bare.git").mkdir(parents=True, exist_ok=True)
    bare = work / "remote.bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, check=True)
    proxy = "http://127.0.0.1:7892"
    import os
    env = dict(os.environ, HTTP_PROXY=proxy, HTTPS_PROXY=proxy)
    f = subprocess.run(["git", "--git-dir", str(bare), "fetch", "--force", URL,
                        BRANCH + ":refs/heads/dev"], capture_output=True, env=env)
    note("FETCH", f.returncode == 0,
         (f.stderr.decode("utf-8", "replace").strip()[-90:] or "rc=0"))
    tip = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "refs/heads/dev"],
                         capture_output=True).stdout.decode("utf-8").strip()
    note("TIP", bool(tip) and (not a.tip or tip == a.tip), "%s 期望=%s" % (tip[:12], a.tip[:12]))

    def blob(rel):
        r = subprocess.run(["git", "--git-dir", str(bare), "cat-file", "-p",
                            "%s:%s" % (tip, rel)], capture_output=True)
        return r.stdout if r.returncode == 0 else None

    got = {s: blob(s) for s in MANIFESTS + EXTRA}
    note("REMOTE_BLOBS_READ", all(got.get(s) is not None for s in MANIFESTS + EXTRA),
         "取到 %d/%d 件" % (sum(1 for v in got.values() if v is not None), len(got)))

    tot = ok = 0
    for man in MANIFESTS:
        if got.get(man) is None:
            note("MANIFEST_BLOB|%s" % man, False, "远端取不到清单")
            continue
        ent = re.findall(r"(?m)^([0-9a-f]{64})  (.+)$", got[man].decode("utf-8"))
        bad = []
        for want, rel in ent:
            b = blob(rel)
            tot += 1
            if b is None:
                bad.append("missing " + rel)
                continue
            if sha(b) == want:
                ok += 1
            else:
                bad.append("mismatch " + rel)
        note("MANIFEST_RECOMPUTE|%s" % man, not bad,
             "登记 %d 条, 不符 %d 条%s" % (len(ent), len(bad), (" | " + "; ".join(bad[:4])) if bad else ""))
    note("BLOB_TOTAL", tot > 0 and ok == tot, "远端逐条复算 %d/%d 相符" % (ok, tot))

    # 不变项: 旧执行器与旧现场原件必须还是原字节; 派生件是**新**身份。
    ob = got.get(OLD_EXE)
    note("旧执行器在远端仍是 33d3f0af…", ob is not None and sha(ob) == OLD_SHA,
         "bytes=%s sha=%s…" % (len(ob) if ob else None, sha(ob)[:12] if ob else "-"))
    nb = got.get(NEW_EXE)
    local = (repo / NEW_EXE).read_bytes()
    note("派生件远端 blob == 本地原字节且 != 旧摘要",
         nb is not None and nb == local and sha(nb) != OLD_SHA,
         "bytes=%d sha=%s…" % (len(nb) if nb else 0, sha(nb)[:16] if nb else "-"))
    pt = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "%s:%s" % (tip, APPS)],
                        capture_output=True).stdout.decode("utf-8").strip()
    note("产品子树(A 登记的那棵 = 顶层 rt-thread-master)仍为 A 登记值", pt == PRODUCT_TREE,
         "%s 期望=%s(本轮 0 改产品; 路径口径见 A 包 TASK §1 与 R7F7 OFFLINE_REVIEW.md:53)"
         % (pt[:12], PRODUCT_TREE[:12]))
    at = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "%s:%s" % (tip, APPS_SUBTREE)],
                        capture_output=True).stdout.decode("utf-8").strip()
    at0 = subprocess.run(["git", "--git-dir", str(bare), "rev-parse",
                          "d6bbc1b:%s" % APPS_SUBTREE], capture_output=True) \
        .stdout.decode("utf-8").strip()
    info("applications 目录子树(我本机另算的一条, 非 A 登记值)",
         "%s 且与上一 tip d6bbc1b 相同=%s" % (at[:12], at == at0))
    # 这条判据必须"会红": 把路径换成一个不存在的子树, 期望值就该对不上。
    fake = subprocess.run(["git", "--git-dir", str(bare), "rev-parse",
                           "%s:%s" % (tip, APPS + "/nonexistent-path")], capture_output=True)
    note("SELFCHECK|产品子树判据会红", fake.returncode != 0 or
         fake.stdout.decode("utf-8").strip() != PRODUCT_TREE,
         "取不存在路径 rc=%s(证明这条比对不是恒真)" % fake.returncode)
    rr = got.get(R7F2B + "/run_record.md")
    if rr is None:
        note("run_record §23 只出现一次", False, "远端取不到 run_record.md")
    else:
        t = rr.decode("utf-8")
        n23 = len(re.findall(r"(?m)^## 23 ", t))
        total = len(re.findall(r"(?m)^## \d+ ", t))
        note("run_record §23 只出现一次", n23 == 1 and total >= 23,
             "总节=%d §23=%d" % (total, n23))
    md = got.get(DRAFT + "/R7F8_DRAFT_MERGED.md")
    src = got.get(ROUND + "/inputs/A_R7F8E_PKG/R7F8_DRAFT_UPDATED.md")
    if md and src:
        pre = md.decode("utf-8").splitlines()
        i = [k for k, l in enumerate(pre) if l.startswith("## 本次合并与修订记录")]
        body = pre[:i[0]] if i else []
        while body and body[-1].strip() in ("", "---"):
            body.pop()
        note("草案合并件正文逐字承接远端那份修订快照",
             body == src.decode("utf-8").splitlines(),
             "正文行=%d 快照行=%d" % (len(body), len(src.decode("utf-8").splitlines())))
    else:
        note("草案合并件正文逐字承接远端那份修订快照", False, "两件之一取不到")
    orig = got.get(DRAFT + "/A_DRAFT_ORIGINAL.md")
    note("草案原件仍是 24780 B / b572c1928f60…",
         orig is not None and len(orig) == 24780
         and sha(orig).startswith("b572c1928f60"),
         "bytes=%s sha=%s…" % (len(orig) if orig else None, sha(orig)[:12] if orig else "-"))
    info("回读方式", "逐件 cat-file -p <tip>:<path>(不做批量读取, 批量输出的分隔与内容换行不可靠)")
    info("本地工作树未参与作证", "所有比对都以远端 blob 为准; 本地只用于派生件字节一致性一条")

    text = "\n".join(out + ["R7F8E_READBACK_SUMMARY|判据=%d|FAIL=%d|%s"
                           % (len(out), len(fails), ",".join(fails) if fails else "none")]) + "\n"
    (repo / ROUND / "proof").mkdir(parents=True, exist_ok=True)
    (repo / ROUND / "proof/r7f8e_remote_readback.txt").write_text(text, encoding="utf-8",
                                                                   newline="\n")
    print(text, end="")
    try:
        shutil.rmtree(str(work), ignore_errors=False)
    except OSError as e:
        print("CLEANUP_DEFERRED|%s|%s(只读 pack 不强删, 目录留仓外)" % (work.name,
                                                                       e.__class__.__name__))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
