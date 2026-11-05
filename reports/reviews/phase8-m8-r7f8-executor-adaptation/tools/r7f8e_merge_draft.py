# -*- coding: utf-8 -*-
"""R7F8E §5.5: 把本包(A)修订后的现场草案合入既有 R7F8_DRAFT_MERGED.md, 保留修订记录。

规则:
  * `A_DRAFT_ORIGINAL.md` 与旧冻结件**不改写** —— 它们的历史身份由 Git blob 承担;
  * 上一版合并件的字节/散列在修订记录里点名, 不回改;
  * 新合并件 = A 的修订快照(逐字节承接) + 本次合并与修订记录(逐节差异计数);
  * 差异计数用 `difflib` 对"上一版合并件 vs 本次修订快照"算, 并逐节列出增删行数,
    不写"内容基本一致"这种没法复算的话。
用法: python tools/r7f8e_merge_draft.py --repo D:/ns800 --updated <A 包里的 R7F8_DRAFT_UPDATED.md>
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import pathlib
import re
import sys

DRAFT_DIR = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
MERGED_REL = DRAFT_DIR + "/R7F8_DRAFT_MERGED.md"
ORIG_REL = DRAFT_DIR + "/A_DRAFT_ORIGINAL.md"
ERRATA_REL = "reports/reviews/phase8-m8-r7f8-executor-adaptation/ERRATA_R7F8E.md"
OLD_MERGED_BYTES = 31807
OLD_MERGED_SHA12 = "270383c12b1c"
OLD_ORIG_BYTES = 24780
OLD_ORIG_SHA12 = "b572c1928f60"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def sections(text):
    """按一级到三级标题切段, 标题行本身算段的开始(表格里出现的 | 不算标题)。"""
    out, cur, name = [], [], "(前置段落)"
    for line in text.splitlines():
        if re.match(r"^#{1,3} ", line):
            out.append((name, cur))
            name, cur = line.strip(), []
        else:
            cur.append(line)
    out.append((name, cur))
    return [(n, s) for n, s in out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--updated", required=True, type=pathlib.Path)
    a = ap.parse_args()
    repo = a.repo.resolve()
    merged_p = repo / MERGED_REL
    updated_p = a.updated.resolve()
    txt_old = merged_p.read_text(encoding="utf-8")
    txt_new = updated_p.read_text(encoding="utf-8")
    orig = (repo / ORIG_REL).read_bytes()

    fails = []
    # 1) 输入身份: A 包里的修订快照与上一版合并件都要点名, 且 A_DRAFT_ORIGINAL 未被动过。
    if len(orig) != OLD_ORIG_BYTES or not sha(orig).startswith(OLD_ORIG_SHA12):
        fails.append("A_DRAFT_ORIGINAL 身份与本包登记不符: %d B sha=%s"
                     % (len(orig), sha(orig)[:12]))
    if len(txt_old.encode("utf-8")) != OLD_MERGED_BYTES or \
            not sha(txt_old.encode("utf-8")).startswith(OLD_MERGED_SHA12):
        fails.append("上一版合并件身份与登记不符: %d B sha=%s"
                     % (len(txt_old.encode("utf-8")), sha(txt_old.encode("utf-8"))[:12]))
    if fails:
        for f in fails:
            print("INPUT_REFUSED|%s" % f)
        return 1

    # 2) 逐节差异计数: 只报可复算的数字, 不写"基本一致"。
    old_secs = dict(sections(txt_old))
    new_secs = dict(sections(txt_new))
    sm = difflib.SequenceMatcher(None, txt_old.splitlines(), txt_new.splitlines())
    ops = sm.get_opcodes()
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")
    added_secs = [n for n in new_secs if n not in old_secs]
    dropped_secs = [n for n in old_secs if n not in new_secs]
    per_sec = []
    for n in new_secs:
        if n not in old_secs:
            per_sec.append((n, "新增节", len(new_secs[n]), 0))
            continue
        s = difflib.SequenceMatcher(None, old_secs[n], new_secs[n])
        eq = sum(t[2] - t[1] for t in s.get_opcodes() if t[0] == "equal")
        ch = sum(max(t[2] - t[1], t[4] - t[3]) for t in s.get_opcodes() if t[0] != "equal")
        if ch:
            per_sec.append((n, "改动", ch, len(new_secs[n]) - eq))

    record = [
        "", "---", "",
        "## 本次合并与修订记录（R7F8E 执行器离线适配轮, 2026-11-05）",
        "",
        "- 本节以下的**正文**逐字承接 A 的修订快照（不是我的重写，也不是脚本拼装）：",
        "  去掉本记录节与它前面的分隔线之后，与 `R7F8_DRAFT_UPDATED.md` 按行逐字节相等；",
        "  复算口径见本工具输出的 `INHERIT_EXACT|...` 一行。",
        "- 承接来源: 任务包附件 `R7F8_DRAFT_UPDATED.md`（本包内 SHA256SUMS 覆盖的原件）。",
        "- 上一版合并件: %d B, sha256 前缀 `%s`（提交 `d6bbc1b5795f`）。它的内容不回改, "
        "历史身份由 Git blob 承担。" % (OLD_MERGED_BYTES, OLD_MERGED_SHA12),
        "- 草案原件 `A_DRAFT_ORIGINAL.md`: %d B, sha256 前缀 `%s` —— **本轮未改动**。"
        % (len(orig), sha(orig)[:12]),
        "- 本次修订快照: %d B, sha256 前缀 `%s`（盘上原字节直算）。"
        % (len(txt_new.encode("utf-8")), sha(txt_new.encode("utf-8"))[:12]),
        "- 与上一版合并件的差异（`difflib` 行级可复算）: 变更行数合计 **%d**; "
        "新增节 %d 个, 消失节 %d 个。" % (changed, len(added_secs), len(dropped_secs)),
        "- 状态不变: **DRAFT_NOT_AUTHORIZED**。这份合并件仍不是可 dispatch 的现场计划, "
        "本轮不生成 REAL_GO 计划, 不编入真实端口/UID, 不开 R7F8 的真实批准账。",
        "- 本轮对草案口径的更正集中在 "
        "`reports/reviews/phase8-m8-r7f8-executor-adaptation/ERRATA_R7F8E.md`"
        "（三源恢复 / 取消只读 / 一拍窗口三条逐字旧句与源码、行为反证）。",
        "",
        "### 逐节差异计数",
        "",
        "| 节 | 类型 | 变更行 | 净增行 |",
        "| --- | --- | --- | --- |",
    ]
    for n, kind, ch, net in per_sec:
        record.append("| %s | %s | %d | %d |" % (n.replace("|", "\\|"), kind, ch, net))
    if added_secs:
        record += ["", "新增节标题: " + "; ".join(added_secs)]
    if dropped_secs:
        record += ["", "上一版里有、本次修订快照没有的节: " + "; ".join(dropped_secs)]
    record += ["", "---", ""]

    # 3) 新合并件 = 修订快照 + 记录节(放在末尾, 并把"本节以下"的说明放前面读起来才成立)。
    body = txt_new.rstrip("\n") + "\n" + "\n".join(record) + "\n"
    merged_p.write_text(body, encoding="utf-8", newline="\n")
    # 4) 自己复算一遍"逐字承接"这句话: 剪掉记录节与分隔线, 必须与修订快照按行完全相等。
    back = merged_p.read_text(encoding="utf-8").splitlines()
    cut = [i for i, l in enumerate(back) if l.startswith("## 本次合并与修订记录")]
    pre = back[:cut[0]] if cut else []
    while pre and pre[-1].strip() in ("", "---"):
        pre.pop()
    up = txt_new.splitlines()
    exact = pre == up
    print("INHERIT_EXACT|equal=%s|正文行=%d|快照行=%d|首处不同=%s"
          % (exact, len(pre), len(up),
             next((i for i, (x, y) in enumerate(zip(pre, up)) if x != y), "-")))
    if not exact:
        print("MERGE_REFUSED|承接不是逐字相等, 本轮不声称承接")
        return 1
    print("MERGED|%s|bytes=%d|sha=%s|承接=%d B/%s|变更行=%d|新增节=%d|消失节=%d|逐节计数=%d 行"
          % (MERGED_REL, len(body.encode("utf-8")), sha(body.encode("utf-8"))[:12],
             len(txt_new.encode("utf-8")), sha(txt_new.encode("utf-8"))[:12],
             changed, len(added_secs), len(dropped_secs), len(per_sec)))
    print("ORIGINAL_UNTOUCHED|%s|%d B|%s" % (ORIG_REL, len(orig), sha(orig)[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
