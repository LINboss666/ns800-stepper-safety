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
import subprocess
import sys

DRAFT_DIR = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
MERGED_REL = DRAFT_DIR + "/R7F8_DRAFT_MERGED.md"
ORIG_REL = DRAFT_DIR + "/A_DRAFT_ORIGINAL.md"
ERRATA_REL = "reports/reviews/phase8-m8-r7f8-executor-adaptation/ERRATA_R7F8E.md"
# R7F8F: 上一版合并件的**字节身份不再手写**。曾经每轮手更这三行, 忘了就撞上"身份不明拒绝",
# 更错就把差异算到别的版本上(§24.5 已登记过一次)。现在直接取 HEAD 里那份 blob 与其短号:
# 承接对象因此永远是**已交付**的那一版, 未提交的重合并不会把基准悄悄换掉。
def head_merged_blob(repo):
    p = subprocess.run(["git", "-C", str(repo), "show", "HEAD:" + MERGED_REL],
                       capture_output=True)
    if p.returncode != 0:
        raise SystemExit("REFUSED|取不到 HEAD 里的合并件 blob(%s): 不许在基准不明时覆盖 %s"
                         % (p.stderr.decode("utf-8", "replace").strip()[:160], MERGED_REL))
    c = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                       capture_output=True)
    return p.stdout, c.stdout.decode("ascii", "replace").strip()

OLD_ORIG_BYTES = 24780
OLD_ORIG_SHA12 = "b572c1928f60"
# 记录节里的本轮名与承接件路径由 --label / --updated 当场填, 不留上一轮的措辞。
# 快照的**作者**同样要当场说: 上一轮的快照是 A 包里的原件, 本轮(F21)的快照是我在上一版正文
# 之上追加的修订 —— 那句话如果继续写"承接 A 的修订快照", 就是把 agent 的改动冒充成 A 签收件。
LABEL = "R7F8F 预算裁定落地轮"
DATE = "2026-11-05"
SNAPSHOT_PROVENANCE = "A 包内 SHA256SUMS 覆盖的原件, 本工具只读不改"
SRC_REL = "-"


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
    ap.add_argument("--label", default="R7F8F 预算裁定落地轮",
                    help="写进合并记录节标题的本轮名(每条判据的来源要能被复算)")
    ap.add_argument("--date", default="2026-11-05", help="记录节标题里的本轮日期")
    ap.add_argument("--snapshot-provenance",
                    default="A 包内 SHA256SUMS 覆盖的原件, 本工具只读不改",
                    help="本次修订快照是**谁**写的(A 签收件 / agent 在上一版正文上追加)——"
                         "这句不能沿用上一轮, 否则把 agent 的改动冒充成 A 签收件")
    a = ap.parse_args()
    repo = a.repo.resolve()
    merged_p = repo / MERGED_REL
    updated_p = a.updated.resolve()
    global LABEL, DATE, SNAPSHOT_PROVENANCE, SRC_REL
    LABEL, DATE, SNAPSHOT_PROVENANCE = a.label, a.date, a.snapshot_provenance
    try:
        SRC_REL = updated_p.relative_to(repo).as_posix()
    except ValueError:
        SRC_REL = updated_p.name
    txt_new = updated_p.read_text(encoding="utf-8")
    orig = (repo / ORIG_REL).read_bytes()
    old_blob, head_short = head_merged_blob(repo)
    txt_old = old_blob.decode("utf-8")
    old_merged_bytes, old_merged_sha12, old_merged_commit = \
        len(old_blob), sha(old_blob)[:12], head_short

    fails = []
    # 1) 输入身份: A 包里的修订快照与上一版合并件都要点名, 且 A_DRAFT_ORIGINAL 未被动过。
    if len(orig) != OLD_ORIG_BYTES or not sha(orig).startswith(OLD_ORIG_SHA12):
        fails.append("A_DRAFT_ORIGINAL 身份与本包登记不符: %d B sha=%s"
                     % (len(orig), sha(orig)[:12]))
    disk_merged = merged_p.read_bytes()
    uncommitted = disk_merged != old_blob
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

    def disk_id(rel):
        p = repo / rel
        if not p.is_file():
            return 0, "NA(不在盘上)"
        b = p.read_bytes()
        return len(b), sha(b)[:12]

    ADAPT = "reports/reviews/phase8-m8-r7f8-executor-adaptation/"
    plan_b, plan_s = disk_id(ADAPT + "field/plan_r7f8f_2369_real.json")
    rows_b, rows_s = disk_id(ADAPT + "field/rows_r7f8f_real.json")
    exe_b, exe_s = disk_id(ADAPT + "tools/m8r3_serial_chain_r7f8.py")
    record = [
        "", "---", "",
        "## 本次合并与修订记录（%s, %s）" % (LABEL, DATE),
        "",
        "- 本节以下的**正文**逐字承接本次修订快照（%s）：" % SNAPSHOT_PROVENANCE,
        "  去掉本记录节与它前面的分隔线之后，与承接件按行逐字节相等；",
        "  复算口径见本工具输出的 `INHERIT_EXACT|...` 一行。",
        "- 承接来源: `%s`。" % SRC_REL,
        "- 上一版合并件: %d B, sha256 前缀 `%s`（提交 `%s` = 本工具取 HEAD 的 blob, 不再手写）。"
        "它的内容不回改, 历史身份由 Git blob 承担。"
        % (old_merged_bytes, old_merged_sha12, old_merged_commit),
        ("" if not uncommitted else
         "- 另有**未提交**的上一份盘上合并件: %d B, sha256 前缀 `%s` —— 差异仍按 HEAD 那份算基准, "
         "这份只作本轮内重合并的留痕(不覆盖、不冒充已交付)。"
         % (len(disk_merged), sha(disk_merged)[:12])),
        "- 草案原件 `A_DRAFT_ORIGINAL.md`: %d B, sha256 前缀 `%s` —— **本轮未改动**。"
        % (len(orig), sha(orig)[:12]),
        "- 本次修订快照: %d B, sha256 前缀 `%s`（盘上原字节直算）。"
        % (len(txt_new.encode("utf-8")), sha(txt_new.encode("utf-8"))[:12]),
        "- 与上一版合并件的差异（`difflib` 行级可复算）: 变更行数合计 **%d**; "
        "新增节 %d 个, 消失节 %d 个。" % (changed, len(added_secs), len(dropped_secs)),
        "- 状态不变: **DRAFT_NOT_AUTHORIZED**。这份合并件本身不是可 dispatch 的对象; 能 dispatch 的"
        "是本轮现场包三件 —— 计划 %d B/`%s…`、行表 %d B/`%s…`、派生执行器 %d B/`%s…`（数字当场从盘"
        "上重算）。离线门全绿之后仍要操作者**一次**整批准才动手; 本轮(F19)设备动作 0、未新增批准账"
        "（F11 那一个账的 port_open=1 已用满, 不借它开第二次; 见交接件 field_run_f11 块）。"
        % (plan_b, plan_s, rows_b, rows_s, exe_b, exe_s),
        "- 本轮口径变化的来源是 A 的预算裁定原文 "
        "`inputs/A_R7F8F_BUDGET_PKG/R7F8F_A_BUDGET_RULING.md` 与裁定包独立复算回执 "
        "`proof/r7f8f_budget_package.txt`; R7F8E 那三条逐字勘误仍留在 `ERRATA_R7F8E.md`, "
        "本轮没有改写它。",
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
