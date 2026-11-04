# -*- coding: utf-8 -*-
"""V0.3 反例独立复跑: 只在仓库外的解包副本与假仓库根上动手, 真实仓库只读。

复算 README §已验证 那几条声称: 缺目录/缺文件/一字节变化/内外同时篡改都必须失败,
不规范来源路径必须被拒; 并证明拦截来自外部原件比对而不是包内自证。

用法: python tools/demo_negative_controls.py --pkg PKGDIR --repo REPODIR --box WORKDIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(args, cwd, script):
    env = {"PYTHONIOENCODING": "utf-8", "SYSTEMROOT": str(Path(sys.executable).anchor)}
    return subprocess.run([sys.executable, str(script)] + [str(a) for a in args],
                          cwd=str(cwd), capture_output=True, env=env)


def fake_root(box, name, data, repo, idx, mutate=None):
    root = box / name
    if root.exists():
        shutil.rmtree(root)
    for f in data["frames"]:
        dst = root / f["source_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / f["source_path"], dst)
    if mutate:
        mutate(root, data)
    return root


def pkg_copy(box, name, pkg, tweak=None):
    d = box / name
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(pkg, d, ignore=shutil.ignore_patterns("__pycache__"))
    if tweak:
        p = d / "data" / "replay.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        tweak(data)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2))
    return d


def pick_neutral_edit(pkg_dir, text):
    """在"应用自己的解析器整行都不看"的那行里挑一个数字 +1, 并当场证明确实中性。

    不这样选的话, 篡改会被应用自身的状态断言先拦下, 就测不到"外部原件比对"这一层了。
    落点按绝对偏移量, 不做整行替换: 同一行文本可能在一份记录里重复出现, 按行替换不唯一。
    返回 (新文本, 命中行, 偏移, 原数字, 新数字)。
    """
    sys.path.insert(0, str(pkg_dir))
    import demo
    pos = 0
    for line in text.split("\n"):
        body = line.rstrip("\r")
        if body.strip():
            a, b = demo.Snapshot(), demo.Snapshot()
            b.update(body)
            va = {k: v for k, v in vars(a).items() if k != "seen"}
            vb = {k: v for k, v in vars(b).items() if k != "seen"}
            m = re.search(r"\b\d+\b", body)
            if va == vb and m:  # 该行不改任何状态 => 状态中性行
                start, end = pos + m.start(), pos + m.end()
                new_num = str(int(m.group()) + 1)
                new_text = text[:start] + new_num + text[end:]
                assert new_text != text, "篡改没有生效"
                return new_text, body, start, m.group(), new_num
        pos += len(line) + 1
    raise SystemExit("该帧里找不到整行不被解析的数字, 无法构造状态中性篡改")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--box", required=True, type=Path)
    ap.add_argument("--new-frame-index", type=int, default=2)
    a = ap.parse_args()
    pkg, repo, box = a.pkg.resolve(), a.repo.resolve(), a.box.resolve()
    if repo in box.parents or box == repo:
        raise SystemExit("--box 必须在仓库外")
    box.mkdir(parents=True, exist_ok=True)
    data = json.loads((pkg / "data" / "replay.json").read_text(encoding="utf-8"))
    idx = a.new_frame_index
    frame = data["frames"][idx]
    new_text, hit_line, hit_off, old_num, new_num = pick_neutral_edit(pkg, frame["text"])
    print("状态中性篡改选点: 偏移=%d 行=%r  %s -> %s" % (hit_off, hit_line, old_num, new_num))

    results = []

    def add(name, proc, want):
        try:
            payload = json.loads(proc.stdout.decode("utf-8", "replace"))
            st = payload.get("archive_sources", {}).get("status")
        except ValueError:
            st = None
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        results.append({"case": name, "rc": proc.returncode, "expected_rc": want,
                        "match": proc.returncode == want, "archive_status": st,
                        "first_error_line": (err[0][:160] if err else "-")})

    r = run(["--check", "--repo-root", box / "does_not_exist"], pkg, pkg / "demo.py")
    add("repo_missing_root", r, 1)

    root = fake_root(box, "missing_one", data, repo, idx,
                     lambda rt, d: (rt / d["frames"][1]["source_path"]).unlink())
    add("archive_file_missing", run(["--check", "--repo-root", root], pkg, pkg / "demo.py"), 1)

    def flip(rt, d):
        p = rt / d["frames"][idx]["source_path"]
        b = bytearray(p.read_bytes())
        b[0] = (b[0] + 1) % 256
        p.write_bytes(bytes(b))
    root = fake_root(box, "one_byte", data, repo, idx, flip)
    add("one_byte_changed", run(["--check", "--repo-root", root], pkg, pkg / "demo.py"), 1)

    def tamper_neutral(d):
        f = d["frames"][idx]
        assert f["text"] == frame["text"], "篡改目标帧与选点时读到的不是同一帧"
        raw = new_text.encode("utf-8")
        assert hashlib.sha256(raw).hexdigest() != f["sha256"], "篡改后内嵌散列没变, 说明改动为空"
        f["text"], f["sha256"], f["source_bytes"] = new_text, hashlib.sha256(raw).hexdigest(), len(raw)
    d1 = pkg_copy(box, "tamper_neutral", pkg, tamper_neutral)
    add("self_consistent_tamper_vs_repo",
        run(["--check", "--repo-root", repo], d1, d1 / "demo.py"), 1)
    add("tampered_pkg_only_blind", run(["--check"], d1, d1 / "demo.py"), 0)

    def bad_paths(d):
        bad = ["reviews/x.rx.bin", "reports/../reports/x", "C:/abs/x.bin",
               "reports\\a\\b.bin", "reports/./x.bin"]
        for i, v in enumerate(bad):
            d["frames"][i % len(d["frames"])]["source_path"] = v
        d["frames"][0]["source_path"] = bad[0]
    d2 = pkg_copy(box, "bad_paths", pkg, bad_paths)
    add("malformed_source_paths", run(["--check"], d2, d2 / "demo.py"), 1)

    print("== 反例复跑(V0.3) ==")
    bad = 0
    for x in results:
        bad += not x["match"]
        print("%-32s rc=%d(期望%d) %s archive=%s 首行错误=%s" % (
            x["case"], x["rc"], x["expected_rc"],
            "MATCH" if x["match"] else "**与预期不符**", x["archive_status"], x["first_error_line"]))
    print("反例复跑: 场景=%d 与声称一致=%d 不符=%d" % (len(results), len(results) - bad, bad))
    print("注: tampered_pkg_only_blind 期望 rc=0 —— 被篡改的包只查包内时应当通过, "
          "用来证明拦截确实来自外部归档原件比对, 不是包内自证。")
    print("注: 篡改选点由 pick_neutral_edit 现场挑出, 并用应用自己的 Snapshot.update 逐行证过状态中性。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
