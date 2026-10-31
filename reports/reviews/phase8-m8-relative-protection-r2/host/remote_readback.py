#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8-R2 远端逐 blob 回读 + **清单优先**缺件检查(R2-D)。

与 R1 交付版的实质差别:
  1) 先把**每一张清单**登记的文件逐条去远端要回来, 再谈别的。R1 的做法是"遍历远端文件树,
     命中清单就算一条", 那恰好看不见"清单里登记了但仓库里根本没有"的文件(本轮抓出 5 件
     R1 构建日志属于这种)。
  2) 清单条目不止 `hash  path` 一种形态: 仓库里历史 `build_manifest.json` 有三种结构
     (targets 为 list / targets 为 dict / 顶层 images), 只认自己那一种就会把别的清单**静默跳过**
     —— 那是同一个缺陷的第二种表现。本版逐条解析并按 JSON 指针披露"有散列但解析不出路径"的条目,
     不静默丢弃。
  3) 判定分两层范围: 本轮交付(`SCOPE_R2`)必须零问题; 往轮清单(`SCOPE_PRIOR`)的问题**逐条披露**
     但不由本轮改写历史来"修好"。这样既不会把别人的缺口藏起来, 也不会让本轮替往轮背判定。
  4) 全程只用 git 协议从新建的空 bare 仓 fetch, 不复用本地对象库或工作树; 清单散列自身被显式拒绝。
"""
import hashlib
import json
import os
import posixpath
import subprocess
import sys

REMOTE = "https://github.com/LINboss666/ns800-stepper-safety.git"
BRANCH = "feature/m8-sg-adc-relative-protection"
PROXY = ["-c", "http.proxy=http://127.0.0.1:7892"]
ROOT = os.environ.get("RBI_ROOT", "D:/ns800_m8r2_scratch/remotecheck")
EXPECT_TIP = os.environ.get("RBI_TIP", "")
MANIFEST_NAMES = ("SHA256SUMS.txt", "SHA256SUMS_backfill.txt", "SHA256SUMS_build.txt")
RECORD = "remote_readback.md"
R2_SCOPE_MARKS = ("phase8-m8-relative-protection-r2",)
# 本轮交付的清单(必须零问题); 其余清单只披露不判
R2_MANIFESTS = (
    "reports/reviews/phase8-m8-relative-protection-r2/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-relative-protection-r2/SHA256SUMS.txt",
    "reports/firmware/phase8-m8-relative-protection-r2/build_manifest.json",
    "reports/reviews/phase8-m8-relative-protection-r2/host/field_offline_run_manifest.json",
)


def git(root, *args, **kw):
    return subprocess.run(["git"] + list(args), cwd=root, capture_output=True,
                          text=kw.pop("text", False), **kw)


def parse_sums(text, base):
    """一行 `hash<两空格>path`; 返回 [(repo 相对路径, sha, 声明字节或 None, 畸形原文)]。"""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        h, sep, p = ln.partition("  ")
        if not sep:
            h, sep, p = ln.partition(" ")
        p = p.strip().replace("\\", "/").lstrip("./")
        if not sep or not p or len(h.strip()) != 64:
            out.append(("<malformed>", h.strip(), None, ln))
            continue
        out.append((resolve(base, p), h.strip(), None, None))
    return out


def is_sha(v):
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


PATH_EXT = (".txt", ".json", ".bin", ".axf", ".map", ".hex", ".log", ".py", ".ps1",
            ".md", ".csv", ".out", ".html", ".yaml", ".yml")


def looks_like_path(key):
    """键名当路径用的判据: 含斜杠, 或以已知文件后缀结尾。
    'axf' / 'bin' / 'load' 这类**角色名**不算路径 -> 走披露分支, 不猜文件名。"""
    k = str(key).lower()
    return ("/" in k) or k.endswith(PATH_EXT)


def resolve(base, rel):
    """清单声明的相对路径一律相对**清单自身所在目录**解析; 越出仓库的一律拒绝并披露。"""
    norm = posixpath.normpath((base + rel) if base else rel)
    if norm.startswith("../") or norm.startswith("/"):
        return None
    return norm


def collect_json(path, node, ptr, base, resolved, unresolved):
    """递归找带 sha256 的对象。路径来源: 显式 path/file 字段; 否则若**键名本身像路径**就用键名;
    否则登记为"有散列但解析不出路径"(逐条披露, 不静默跳过)。"""
    if isinstance(node, dict):
        for k, v in node.items():
            origin = "%s/%s" % (ptr, k)
            if isinstance(v, dict) and is_sha(v.get("sha256")):
                rel = v.get("path") or v.get("file")
                if not (isinstance(rel, str) and rel) and looks_like_path(k):
                    rel = k
                if isinstance(rel, str) and rel:
                    rp = resolve(base, rel)
                    if rp is None:
                        unresolved.append((path, origin, "声明路径越出仓库: %s" % rel))
                    else:
                        resolved.append((rp, v["sha256"], v.get("bytes"), path, origin))
                else:
                    unresolved.append((path, origin, "有 sha256 但解析不出路径(键名是角色名)"))
                continue
            if k == "sha256" and is_sha(node[k]):
                rel = node.get("path") or node.get("file")
                if isinstance(rel, str) and rel:
                    rp = resolve(base, rel)
                    if rp is None:
                        unresolved.append((path, ptr, "声明路径越出仓库: %s" % rel))
                    else:
                        resolved.append((rp, node[k], node.get("bytes"), path, ptr))
                else:
                    unresolved.append((path, ptr, "有 sha256 但无 path/file"))
                continue
            collect_json(path, v, origin, base, resolved, unresolved)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            collect_json(path, v, "%s[%d]" % (ptr, i), base, resolved, unresolved)


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
    r = git(ROOT, *PROXY, "fetch", "--depth=1", "--no-tags", REMOTE, BRANCH, text=True)
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
        cache[revpath] = None if p.returncode else p.stdout
        return cache[revpath]

    tree = git(ROOT, "ls-tree", "-r", "--long", tip, text=True).stdout.splitlines()
    remote = {}
    for ln in tree:
        meta, name = ln.split("\t", 1)
        _mode, _typ, oid, size = meta.split()
        remote[name.replace("\\", "/")] = (oid, int(size) if size != "-" else -1)
    print("remote_files=%d" % len(remote))

    declared = {}
    order = []
    problems_r2 = []
    problems_prior = []
    unresolved = []

    def declare(path, sha, want_bytes, src, origin=None):
        scoped = problems_r2 if src in R2_MANIFESTS else problems_prior
        if path == src:
            # 清单不得散列自己(散列之后字节就变了): 显式报错而不是静默跳过
            scoped.append(("self_reference", src, path))
            return
        if path.endswith(RECORD):
            # 回读记录本身: 记录里要写这次的结果, 所以它不可能被自己覆盖的清单散列。
            # 该件已从 SHA256SUMS.txt 显式排除, 这一支只是防它被别的清单登记进来。
            scoped.append(("record_excluded", src, path))
            return
        if not is_sha(sha):
            scoped.append(("manifest_line_malformed", src, path))
            return
        if path in declared:
            old = declared[path]
            if old[0] != sha:
                scoped.append(("duplicate_conflict", src, path, old[0][:12], sha[:12]))
            return
        declared[path] = (sha, want_bytes, src)
        order.append(path)

    # 1) SHA256SUMS*.txt(逐条登记, base = 清单所在目录)
    sums_files = sorted(n for n in remote if os.path.basename(n) in MANIFEST_NAMES)
    for n in sums_files:
        raw = blob(n)
        if raw is None:
            problems_prior.append(("manifest_unreadable", n))
            continue
        base = n.rsplit("/", 1)[0] + "/" if "/" in n else ""
        for path, sha, wb, bad in parse_sums(raw.decode("utf-8"), base):
            if bad:
                (problems_r2 if n in R2_MANIFESTS else problems_prior).append(
                    ("manifest_line_malformed", n, bad[:70]))
                continue
            declare(path, sha, wb, n)
    print("sums_manifests=%d" % len(sums_files))

    # 2) 所有 JSON 清单: 按结构递归解析, 解析不出路径的一律披露
    json_manifests = sorted(x for x in remote if x.endswith(".json")
                            and ("manifest" in os.path.basename(x) or x.endswith("needles.json")))
    for n in json_manifests:
        raw = blob(n)
        if raw is None:
            problems_prior.append(("manifest_unreadable", n))
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except Exception as e:
            (problems_r2 if n in R2_MANIFESTS else problems_prior).append(
                ("manifest_unparsable", n, repr(e)[:60]))
            continue
        base = n.rsplit("/", 1)[0] + "/"
        resolved, unres = [], []
        collect_json(n, doc, "", base, resolved, unres)
        for path, sha, wb, src, origin in resolved:
            declare(path, sha, wb, src, origin)
        for p, origin, why in unres:
            unresolved.append((p, origin, why))
    print("json_manifests=%d sha_declared_without_path=%d" % (len(json_manifests), len(unresolved)))

    # 3) 逐条回远端验字节
    ok = 0
    ok_r2 = 0
    for path in order:
        sha, wb, src = declared[path]
        scoped = problems_r2 if src in R2_MANIFESTS else problems_prior
        if path not in remote:
            scoped.append(("declared_but_absent", src, path))
            continue
        raw = blob(path)
        if raw is None:
            scoped.append(("read_failed", src, path))
            continue
        got = hashlib.sha256(raw).hexdigest()
        if got != sha:
            scoped.append(("hash_mismatch", src, path, got[:12], sha[:12]))
            continue
        if wb is not None and int(wb) != len(raw):
            scoped.append(("byte_count_mismatch", src, path, len(raw), int(wb)))
            continue
        ok += 1
        if src in R2_MANIFESTS:
            ok_r2 += 1

    # 4) 反向: 树里有但任何清单都没登记的, 逐条披露(不当失败, 但要看得见)
    unlisted = sorted(p for p in remote if p not in declared)
    print("tree_not_in_any_manifest=%d" % len(unlisted))

    print("declared_entries=%d verified=%d" % (len(order), ok))
    print("R2_scope_entries=%d verified=%d problems=%d" %
          (sum(1 for p in order if declared[p][2] in R2_MANIFESTS), ok_r2, len(problems_r2)))
    for pr in problems_r2[:40]:
        print("   R2_PROBLEM " + " | ".join(str(x) for x in pr))
    kinds_prior = {}
    for pr in problems_prior:
        kinds_prior[pr[0]] = kinds_prior.get(pr[0], 0) + 1
    print("PRIOR_scope_problems=%d %s" % (len(problems_prior),
          ",".join("%s=%d" % kv for kv in sorted(kinds_prior.items())) or "none"))
    for pr in problems_prior[:40]:
        print("   PRIOR_PROBLEM " + " | ".join(str(x) for x in pr))
    for u in unresolved[:20]:
        print("   NO_PATH %s | %s | %s" % u)
    for p in unlisted[:40]:
        print("   UNLISTED " + p)
    print("REMOTE_READBACK_SUMMARY|tip=%s|declared=%d|verified=%d|r2_entries=%d|r2_verified=%d"
          "|r2_problems=%d|prior_problems=%d|no_path=%d|unlisted=%d" %
          (tip[:10], len(order), ok, sum(1 for p in order if declared[p][2] in R2_MANIFESTS),
           ok_r2, len(problems_r2), len(problems_prior), len(unresolved), len(unlisted)))
    print("hardware_actions=0|sources_read_from=remote_git_protocol_only")
    return 0 if (problems_r2 == [] and ok_r2 == sum(1 for p in order if declared[p][2] in R2_MANIFESTS)
                 and order) else 1


if __name__ == "__main__":
    sys.exit(main())
