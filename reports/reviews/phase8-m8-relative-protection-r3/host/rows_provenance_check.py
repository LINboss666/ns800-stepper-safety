#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3 静态取证: `rows.json` 每一组 `_provenance` 声称的来源, 必须真的对得上实物。

PS 侧 `-Precheck` 只查"声称的来源文件存在"(M8R3Field.ps1 的 Get-RowsProvenanceFiles)。
本脚本把同一件事查到**行级**:

  1) kind 必须在 `_provenance_legend` 里(或它的合法复合 `board_capture+source_shape`);
     不在 => UNKNOWN_KIND —— 防止"造一个没人解释过的来源等级"。
     每组的**所有** `_provenance*` 键都要查(含 `_provenance_trip` / `_provenance_drift` 这类档案级说明),
     漏查其中任何一个就等于给负控行开了免检口。
  2) 声明的 `reports/**` 采集件: 路径必须存在于仓库; 给了 `:N` / `:N-M` 的, 行号必须落在
     该采集件的**可读行数**内。`.b64` 先按 base64 解码(板上 RX 原始字节的存档形态),
     `.bin` 用 UTF-8 errors=replace 后数行 —— 与执行器实际读到的行序一致。
  3) 声明的 `applications/*.c:N` 字面量锚点: 驱动源码必须存在、行号在文件行数内,
     且该行号 ±6 行内必须出现 `rt_kprintf`(字面量形状的唯一来源)。
     命中不到 => ANCHOR_NO_PRINTF, 因为它意味着"这一行不是打印语句", 形状声明就站不住。

只查"声明与实物是否相符", 不判内容对错。`problems>0` => 退出码 1。零设备动作、零写动作。
"""
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]           # 仓库根(本文件在 reports/reviews/<轮>/host/)
BSP = ROOT / "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
ROWS = Path(__file__).resolve().parents[1] / "field" / "rows.json"   # 被测对象固定在包内 field/rows.json
CAP_RE = re.compile(r"reports/[^\s,;()\"']+\.(?:txt|bin|b64|json)")
SRC_RE = re.compile(r"applications/[A-Za-z0-9_]+\.[ch]")
LOC_RE = re.compile(r":(\d+)(?:-(\d+))?")
LINE_CACHE = {}


def text_lines(path):
    """采集件的"可读行数": .b64 按 base64 解码, 其余按字节解码; 统一去掉 CR 再数行。"""
    if path in LINE_CACHE:
        return LINE_CACHE[path]
    raw = path.read_bytes()
    if path.suffix == ".b64" or path.name.endswith(".bin.b64"):
        try:
            body = base64.b64decode(raw)
        except Exception as e:
            raise SystemExit("B64_UNDECODABLE %s %r" % (path, repr(e)[:60]))
    else:
        body = raw
    n = len(body.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    LINE_CACHE[path] = n
    return n


def src_lines(rel):
    key = "src:" + rel
    if key in LINE_CACHE:
        return LINE_CACHE[key]
    p = BSP / rel
    if not p.exists():
        LINE_CACHE[key] = -1
        return -1
    n = len(p.read_bytes().decode("utf-8", "replace").split("\n"))
    LINE_CACHE[key] = n
    return n


def src_has_printf(rel, lo, hi):
    lines = (BSP / rel).read_bytes().decode("utf-8", "replace").split("\n")
    a = max(1, lo - 6)
    b = min(len(lines), hi + 6)
    return any("rt_kprintf" in lines[i - 1] for i in range(a, b + 1))


def main():
    if not ROWS.exists():
        print("ROWS_NOT_FOUND|%s" % ROWS)
        return 2
    doc = json.loads(ROWS.read_text(encoding="utf-8"))
    legend = set(doc["_provenance_legend"].keys()) | {"board_capture+source_shape"}
    commands = doc["commands"]
    problems, notes = [], []
    n_cap = n_src = n_groups = 0

    for name, grp in sorted(commands.items()):
        provs = [(k, v) for k, v in grp.items() if k.startswith("_provenance")]
        n_groups += 1
        if not provs:
            problems.append(("NO_PROVENANCE", name, ""))
            continue
        for pk, prov in provs:
            where = name if pk == "_provenance" else "%s/%s" % (name, pk)
            if not isinstance(prov, dict):
                problems.append(("NO_PROVENANCE", where, type(prov).__name__))
                continue
            kind = prov.get("kind", "")
            if kind not in legend:
                problems.append(("UNKNOWN_KIND", where, kind))
            blob = " | ".join(prov.get("files") or []) + " | " + str(prov.get("file") or "") + \
                   " | " + str(prov.get("lines") or "") + " | " + str(prov.get("note") or "")
            has_cap = bool(CAP_RE.search(blob))
            has_src = bool(SRC_RE.search(blob))

            if kind.startswith("board_capture") and not has_cap:
                problems.append(("BOARD_KIND_WITHOUT_CAPTURE", where, kind))
            if kind.startswith("board_capture") and "source_shape" in kind and not has_src:
                problems.append(("COMPOUND_WITHOUT_SOURCE_ANCHOR", where, kind))
            if kind == "source_shape" and not has_src:
                problems.append(("SHAPE_KIND_WITHOUT_SOURCE_ANCHOR", where, kind))

            # 2) 采集件: 存在 + 行号可落
            for m in CAP_RE.finditer(blob):
                rel = m.group(0)
                p = ROOT / rel
                if not p.exists():
                    problems.append(("CAPTURE_ABSENT", where, rel))
                    continue
                n_cap += 1
                tail = blob[m.end():m.end() + 40]
                lm = LOC_RE.match(tail)
                if lm:
                    lo = int(lm.group(1))
                    hi = int(lm.group(2) or lm.group(1))
                    total = text_lines(p)
                    if hi > total:
                        problems.append(("CAPTURE_LINE_OUT_OF_RANGE", where,
                                         "%s:%d-%d 但可读行数=%d" % (rel, lo, hi, total)))

            # 3) 驱动锚点: 存在 + 行号可落 + 附近确有 rt_kprintf
            for sm in SRC_RE.finditer(blob):
                rel = sm.group(0)
                total = src_lines(rel)
                if total < 0:
                    problems.append(("SOURCE_ABSENT", where, rel))
                    continue
                tail = blob[sm.end():sm.end() + 60]
                lm = LOC_RE.match(tail)
                if not lm:
                    notes.append(("ANCHOR_NO_LINE", where, rel))
                    continue
                lo = int(lm.group(1))
                hi = int(lm.group(2) or lm.group(1))
                n_src += 1
                if hi > total:
                    problems.append(("SOURCE_LINE_OUT_OF_RANGE", where,
                                     "%s:%d-%d 但文件行数=%d" % (rel, lo, hi, total)))
                    continue
                if not src_has_printf(rel, lo, hi):
                    problems.append(("ANCHOR_NO_PRINTF", where, "%s:%d-%d" % (rel, lo, hi)))

    # _board_sources 里声明的仓库路径也要真的在(它同时是 PS 侧的来源清单)
    for tag, val in doc.get("_board_sources", {}).items():
        m = CAP_RE.search(str(val))
        if m and not (ROOT / m.group(0)).exists():
            problems.append(("BOARD_SOURCE_ABSENT", tag, m.group(0)))

    print("ROWS_PROVENANCE|groups=%d capture_refs=%d source_anchor_refs=%d problems=%d notes=%d"
          % (n_groups, n_cap, n_src, len(problems), len(notes)))
    for pr in problems:
        print("   PROBLEM " + " | ".join(pr))
    for nt in sorted(set(notes)):
        print("   NOTE " + " | ".join(nt))
    print("hardware_actions=0|writes=0|reads=repo_files_only")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
