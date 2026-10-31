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
R2_MARK = "phase8-m8-relative-protection-r2"
BASE_NAMES = ("manifest_dir", "parent_dir", "repo_root")
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


def parse_sums(text, bases, comment_total):
    """一行 `hash<两空格>path`; '#' 开头按注释计数(历史清单里有带说明头的)。
    返回 (条目[(候选路径, sha, 声明字节)], 畸形行, 本清单注释行数)。"""
    out, bad, ncom = [], [], 0
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if ln.lstrip().startswith("#"):
            ncom += 1
            comment_total[0] += 1
            continue
        h, sep, p = ln.partition("  ")
        if not sep:
            h, sep, p = ln.partition(" ")
        p = p.strip().replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        if not sep or not p or not is_sha(h.strip()):
            bad.append(ln)
            continue
        out.append((candidates(bases, p), h.strip(), None, p))
    return out, bad, ncom


def is_sha(v):
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


PATH_EXT = (".txt", ".json", ".bin", ".axf", ".map", ".hex", ".log", ".py", ".ps1",
            ".md", ".csv", ".out", ".html", ".yaml", ".yml")


def looks_like_path(key):
    """键名当路径用的判据: 含斜杠, 或以已知文件后缀结尾。
    'axf' / 'bin' / 'load' 这类**角色名**不算路径 -> 走披露分支, 不猜文件名。"""
    k = str(key).lower()
    return ("/" in k) or k.endswith(PATH_EXT)


def candidates(bases, rel):
    """历史清单有三种基准混用(清单自身目录 / 上一级目录 / 仓库根), 只认一种就会把别的清单
    误报成 declared_but_absent —— 那正是 R2-D 要修的同一类缺陷的反面。这里对声明路径依次
    试这三种基准, 取**在远端树里存在**的那个; 三个都不存在才判缺件, 并把三种尝试都列出来。
    字节判据不变: 选中之后仍要 sha256(+字节数)相符, 所以不会靠"换个基准"蒙过去。"""
    out = []
    for b in bases:
        norm = posixpath.normpath((b + rel) if b else rel)
        if norm.startswith("../") or norm.startswith("/") or norm == ".":
            continue
        if norm not in out:
            out.append(norm)
    return out


def kinds(lst):
    d = {}
    for x in lst:
        d[x[0]] = d.get(x[0], 0) + 1
    return ",".join("%s=%d" % kv for kv in sorted(d.items())) or "none"


def base_dirs(manifest_path):
    d = manifest_path.rsplit("/", 1)[0] + "/" if "/" in manifest_path else ""
    parent = d.rstrip("/")
    parent = (parent.rsplit("/", 1)[0] + "/") if "/" in parent else ""
    return [d, parent, ""]


def collect_json(path, node, ptr, bases, resolved, unresolved):
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
                    resolved.append((candidates(bases, rel), v["sha256"], v.get("bytes"),
                                     path, origin, rel))
                else:
                    unresolved.append((path, origin, "有 sha256 但解析不出路径(键名是角色名)"))
                continue
            if k == "sha256" and is_sha(node[k]):
                rel = node.get("path") or node.get("file")
                if isinstance(rel, str) and rel:
                    resolved.append((candidates(bases, rel), node[k], node.get("bytes"),
                                     path, ptr, rel))
                else:
                    unresolved.append((path, ptr, "有 sha256 但无 path/file"))
                continue
            collect_json(path, v, origin, bases, resolved, unresolved)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            collect_json(path, v, "%s[%d]" % (ptr, i), bases, resolved, unresolved)


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

    # 声明路径的基准在各轮清单里并不统一(清单自身目录 / 上一级 / 仓库根 / 文档里另写的运行目录)。
    # 先按三种基准精确解析; 都不存在时再用**后缀索引**在远端树里找同名相对路径。后缀命中唯一
    # 才算解析成功; 命中多条就把歧义披露出来(不猜)。这样"登记了但仓库里确实没有"仍然报得出来,
    # 而不会因基准不同被误判成缺件, 也不会把随意一个同名文件当成已验证。
    suffix = {}
    for full in remote:
        parts = full.split("/")
        for i in range(len(parts)):
            suffix.setdefault("/".join(parts[i:]), []).append(full)

    declared = {}
    order = []
    problems_r2 = []
    problems_prior = []
    unresolved = []
    comment = [0]
    base_used = dict((b, 0) for b in BASE_NAMES)
    base_used["suffix"] = 0
    tally = {"suffix_ambiguous": 0}

    def declare(cps, sha, want_bytes, src, origin=None, rel=None):
        scoped = problems_r2 if src in R2_MANIFESTS else problems_prior
        if not cps:
            scoped.append(("declared_path_outside_repo", src, str(origin), str(sha)[:12]))
            return
        if src in cps:
            # 清单不得散列自己(散列之后字节就变了): 显式报错而不是静默跳过
            scoped.append(("self_reference", src, cps[0]))
            return
        if any(c.endswith(RECORD) for c in cps):
            # 回读记录本身: 记录里要写这次的结果, 所以它不可能被自己覆盖的清单散列。
            # 该件已从 SHA256SUMS.txt 显式排除, 这一支只是防它被别的清单登记进来。
            scoped.append(("record_excluded", src, cps[0]))
            return
        key = cps[0]
        if key in declared:
            old = declared[key]
            if old[0] != sha:
                scoped.append(("duplicate_conflict", src, key, old[0][:12], sha[:12]))
            return
        declared[key] = (sha, want_bytes, src, cps, rel or key)
        order.append(key)

    # 1) SHA256SUMS*.txt(逐条登记; 声明路径按三种基准展开, 见 candidates 的说明)
    sums_files = sorted(n for n in remote if os.path.basename(n) in MANIFEST_NAMES)
    for n in sums_files:
        raw = blob(n)
        if raw is None:
            problems_prior.append(("manifest_unreadable", n))
            continue
        entries, bad, _ = parse_sums(raw.decode("utf-8"), base_dirs(n), comment)
        for cps, sha, wb, rel in entries:
            declare(cps, sha, wb, n, rel=rel)
        for ln in bad:
            (problems_r2 if n in R2_MANIFESTS else problems_prior).append(
                ("manifest_line_malformed", n, ln[:70]))
    print("sums_manifests=%d comment_lines_skipped=%d" % (len(sums_files), comment[0]))

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
        base = base_dirs(n)
        resolved, unres = [], []
        collect_json(n, doc, "", base, resolved, unres)
        for cps, sha, wb, src, origin, rel in resolved:
            declare(cps, sha, wb, src, origin, rel)
        for p, origin, why in unres:
            unresolved.append((p, origin, why))
    print("json_manifests=%d sha_declared_without_path=%d" % (len(json_manifests), len(unresolved)))

    # 3) 逐条回远端验字节(候选路径里取**远端存在**的那个; 三个都不存在才算缺件)
    ok = 0
    ok_r2 = 0
    r2_total = sum(1 for k in order if declared[k][2] in R2_MANIFESTS)
    for key in order:
        sha, wb, src, cps, rel = declared[key]
        scoped = problems_r2 if src in R2_MANIFESTS else problems_prior
        hit = next((c for c in cps if c in remote), None)
        how = None
        if hit is None:
            alts = suffix.get(rel, [])
            matched = [a for a in alts if hashlib.sha256(blob(a) or b"").hexdigest() == sha]
            if len(matched) == 1:
                hit, how = matched[0], "suffix"
            elif len(alts) > 1:
                tally["suffix_ambiguous"] += 1
                scoped.append(("suffix_ambiguous", src, rel, "candidates=%d" % len(alts)))
                continue
        else:
            how = BASE_NAMES[cps.index(hit)]
        if hit is None:
            scoped.append(("declared_but_absent", src, rel, "tried=" + ",".join(cps)))
            continue
        base_used[how] += 1
        raw = blob(hit)
        if raw is None:
            scoped.append(("read_failed", src, hit))
            continue
        got = hashlib.sha256(raw).hexdigest()
        if got != sha:
            scoped.append(("hash_mismatch", src, hit, got[:12], sha[:12]))
            continue
        if wb is not None and int(wb) != len(raw):
            scoped.append(("byte_count_mismatch", src, hit, len(raw), int(wb)))
            continue
        ok += 1
        if src in R2_MANIFESTS:
            ok_r2 += 1

    # 4) 反向: 树里有但任何清单都没登记的, 披露计数 + 本轮路径内的逐条列出
    unlisted = sorted(p for p in remote if p not in declared)
    unlisted_r2 = [p for p in unlisted if R2_MARK in p]
    print("tree_not_in_any_manifest=%d of_which_in_r2_paths=%d" % (len(unlisted), len(unlisted_r2)))
    for p in unlisted_r2[:20]:
        print("   UNLISTED_R2 " + p)

    print("declared_entries=%d verified=%d base_used=%s" % (len(order), ok, base_used))
    print("SCOPE_R2|entries=%d verified=%d problems=%d" % (r2_total, ok_r2, len(problems_r2)))
    for pr in problems_r2[:40]:
        print("   R2_PROBLEM " + " | ".join(str(x) for x in pr))
    hard = [pr for pr in problems_prior if pr[0] != "declared_but_absent"]
    print("SCOPE_PRIOR|problems=%d|%s" % (len(problems_prior), kinds(problems_prior)))
    for pr in hard[:40]:
        print("   PRIOR_HARD " + " | ".join(str(x) for x in pr))
    bysrc = {}
    for pr in problems_prior:
        bysrc[pr[1]] = bysrc.get(pr[1], 0) + 1
    for src, cnt in sorted(bysrc.items(), key=lambda kv: -kv[1])[:25]:
        print("   PRIOR_BY_MANIFEST %s | %d" % (src, cnt))
    for u in unresolved[:20]:
        print("   NO_PATH %s | %s | %s" % u)
    print("REMOTE_READBACK_SUMMARY|tip=%s|declared=%d|verified=%d|r2_entries=%d|r2_verified=%d"
          "|r2_problems=%d|prior_problems=%d|prior_hard=%d|no_path=%d|unlisted=%d" %
          (tip[:10], len(order), ok, r2_total, ok_r2, len(problems_r2), len(problems_prior),
           len(hard), len(unresolved), len(unlisted)))
    print("hardware_actions=0|sources_read_from=remote_git_protocol_only")
    return 0 if (not problems_r2 and ok_r2 == r2_total and r2_total) else 1


if __name__ == "__main__":
    sys.exit(main())
