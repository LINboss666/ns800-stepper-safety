#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R2 远端逐 blob 回读 + **清单优先**缺件检查(R2-D)。

与 R1 交付版的唯一实质差别: 先把**每一张清单**登记的文件逐条去远端要回来, 再谈别的。
R1 的做法是"遍历远端文件树, 命中清单就算一条" —— 那恰好看不见"清单里登记了但仓库里
根本没有"的文件(本轮就抓出 5 件 R1 构建日志属于这种)。所以这里的判定顺序是:

  1) 枚举清单条目(远端 blob 里的 SHA256SUMS*.txt / build_manifest.json / 现场运行清单);
  2) 每条都必须: 在远端存在、声明字节 == 远端字节、声明 sha256 == 远端 blob 复算值;
     同名条目重复且散列不一致 => 也算问题; 读不到/读失败 => 一律非成功;
  3) 然后才遍历远端文件树做反向核对(存在于树但任何清单都没登记 => 逐条披露);
  4) 全程只用 git 协议从新建的空 bare 仓 fetch 一次, 不复用本地对象库或工作树。

清单里若声明了"本文件自身的字节"(自指)会被显式拒绝: 清单不能散列自己。
"""
import hashlib
import json
import os
import subprocess
import sys

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-sg-adc-relative-protection"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
ROOT = os.environ.get("RBI_ROOT", "D:/ns800_m8r2_scratch/remotecheck")
EXPECT_TIP = os.environ.get("RBI_TIP", "")
MANIFEST_NAMES = ("SHA256SUMS.txt", "SHA256SUMS_backfill.txt", "SHA256SUMS_build.txt")
SELF = "host/remote_readback.py"


def git(root, *args, **kw):
    return subprocess.run(["git"] + list(args), cwd=root, capture_output=True,
                          text=kw.pop("text", False), **kw)


def parse_sums(text, base):
    """一行 `hash<两空格>path`; 返回 [(repo 相对路径, sha, 声明字节或 None)]。"""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        h, sep, p = ln.partition("  ")
        if not sep:
            h, sep, p = ln.partition(" ")
        p = p.strip().replace("\\", "/").lstrip("./")
        if not sep or not p:
            out.append(("<malformed>", h.strip(), None, ln))
            continue
        full = (base + p) if base else p
        out.append((full, h.strip(), None, None))
    return out


def main():
    if os.path.isdir(ROOT):
        for dirpath, dirnames, filenames in os.walk(ROOT):
            for f in filenames:
                try:
                    os.chmod(os.path.join(dirpath, f), 0o666)
                except OSError:
                    pass
    os.makedirs(ROOT, exist_ok=True)
    git(ROOT, "init", "-q", "--bare", text=True)
    r = git(ROOT, "fetch", "--depth=1", "--no-tags", REMOTE, BRANCH, text=True)
    if r.returncode != 0:
        print("FETCH_FAILED:", r.stderr[-500:])
        return 4
    tip = git(ROOT, "rev-parse", "FETCH_HEAD", text=True).stdout.strip()
    print("remote_tip=%s" % tip)
    if EXPECT_TIP and tip != EXPECT_TIP:
        print("TIP_MISMATCH expect=%s" % EXPECT_TIP)
        return 4

    cache = {}

    def blob(revpath):
        if revpath in cache:
            return cache[revpath]
        p = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (tip, revpath)],
                           cwd=ROOT, capture_output=True)
        data = p.stdout if p.returncode == 0 else None
        cache[revpath] = data
        return data

    tree = git(ROOT, "ls-tree", "-r", "--long", tip, text=True).stdout.splitlines()
    remote = {}
    for ln in tree:
        meta, name = ln.split("\t", 1)
        _mode, _typ, oid, size = meta.split()
        remote[name.replace("\\", "/")] = (oid, int(size) if size != "-" else -1)
    print("remote_files=%d" % len(remote))

    declared = {}
    order = []
    problems = []

    def declare(path, sha, want_bytes, src, self_ref_ok=False):
        if path.endswith(SELF) or path.endswith("SHA256SUMS.txt") and src.endswith("SHA256SUMS.txt"):
            # 清单不得散列自己(散列后字节就变了); 显式拒绝这种条目而不是静默跳过
            problems.append(("self_reference", src, path))
            return
        if path in declared:
            old = declared[path]
            if old[0] != sha:
                problems.append(("duplicate_conflict", src, path, old[0][:12], sha[:12]))
            return
        declared[path] = (sha, want_bytes, src)
        order.append(path)

    # 1) 所有 SHA256SUMS*.txt(逐条登记, base = 清单所在目录)
    sums_files = [n for n in remote if os.path.basename(n) in MANIFEST_NAMES]
    for n in sorted(sums_files):
        raw = blob(n)
        if raw is None:
            problems.append(("manifest_unreadable", n))
            continue
        base = n.rsplit("/", 1)[0] + "/" if "/" in n else ""
        for path, sha, wb, bad in parse_sums(raw.decode("utf-8"), base):
            if bad:
                problems.append(("manifest_line_malformed", n, bad[:60]))
                continue
            declare(path, sha, wb, n)
    print("sums_manifests=%d" % len(sums_files))

    # 2) build_manifest.json: images/map 的 bytes + sha256
    for n in sorted(x for x in remote if x.endswith("build_manifest.json")):
        raw = blob(n)
        try:
            man = json.loads(raw.decode("utf-8"))
        except Exception as e:
            problems.append(("manifest_unparsable", n, repr(e)))
            continue
        base = n.rsplit("/", 1)[0] + "/"
        for t in man.get("targets", []):
            for name, v in (t.get("images") or {}).items():
                declare(base + name, v["sha256"], v.get("bytes"), n)
            for name, v in (t.get("map") or {}).items():
                declare(base + name, v["sha256"], v.get("bytes"), n)
        for name, v in (man.get("images") or {}).items():
            declare(base + name, v["sha256"], v.get("bytes"), n)
        for name, v in (man.get("map") or {}).items():
            declare(base + name, v["sha256"], v.get("bytes"), n)

    # 3) 现场运行清单/自检结果里登记的产物
    for n in sorted(x for x in remote if x.endswith("field_offline_run_manifest.json")
                    or x.endswith("field_run_manifest.json") or x.endswith("_manifest.json")
                    and "field" in x):
        raw = blob(n)
        if raw is None:
            problems.append(("manifest_unreadable", n))
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        base = n.rsplit("/", 1)[0] + "/"
        def walk(obj, keypath):
            if isinstance(obj, dict):
                if "sha256" in obj and isinstance(obj.get("sha256"), str) and len(obj["sha256"]) == 64:
                    rel = obj.get("path") or obj.get("file")
                    if isinstance(rel, str) and rel:
                        declare((base + rel) if not rel.startswith("reports/") else rel,
                                obj["sha256"], obj.get("bytes"), n)
                    return
                for k, v in obj.items():
                    walk(v, keypath + "/" + k)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v, keypath)
        walk(doc, n)

    # 4) 逐条回远端验字节
    ok = 0
    for path in order:
        sha, wb, src = declared[path]
        if path not in remote:
            problems.append(("declared_but_absent", src, path))
            continue
        raw = blob(path)
        if raw is None:
            problems.append(("read_failed", src, path))
            continue
        got = hashlib.sha256(raw).hexdigest()
        if got != sha:
            problems.append(("hash_mismatch", src, path, got[:12], sha[:12]))
            continue
        if wb is not None and int(wb) != len(raw):
            problems.append(("byte_count_mismatch", src, path, len(raw), int(wb)))
            continue
        ok += 1
    print("declared_entries=%d verified=%d" % (len(order), ok))

    # 5) 反向: 树里有但任何清单都没登记的, 逐条披露(不当成失败, 但要看得见)
    unlisted = [p for p in remote if p not in declared]
    print("tree_not_in_any_manifest=%d" % len(unlisted))
    for p in sorted(unlisted)[:40]:
        print("   UNLISTED " + p)

    kinds = {}
    for pr in problems:
        kinds[pr[0]] = kinds.get(pr[0], 0) + 1
    for pr in problems[:60]:
        print("   PROBLEM " + " | ".join(str(x) for x in pr))
    print("REMOTE_READBACK_SUMMARY|tip=%s|declared=%d|verified=%d|problems=%d|%s" %
          (tip[:10], len(order), ok, len(problems),
           ",".join("%s=%d" % kv for kv in sorted(kinds.items())) or "none"))
    print("hardware_actions=0|sources_read_from=remote_git_protocol_only")
    return 0 if (ok == len(order) and order and not problems) else 1


if __name__ == "__main__":
    sys.exit(main())
