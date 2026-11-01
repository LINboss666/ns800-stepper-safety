#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C-02 更正(数据侧, 零设备): 把 M8 相对保护的**停后帧事件码**从 9 改成 5, 并给被改的夹具
补上如实的来源分级 + 一条 ADC68/SG8 过量采样健康夹具。

范围是**点名清单**, 不是全局替换:
  reports/reviews/phase8-m8-relative-protection-r3/field/{plan.json,plan_continuation_s2.json,rows.json}
  reports/reviews/phase8-m8-idle-stack-r4/field/rows.json
不碰的东西(实测与历史证据):
  * 任何 offline/psrun-*/** 归档运行输出(里面 neg-stagemutate/plan.json 仍带 event=9, 那是当时那次跑的原件)
  * M5 软件触发原件(event=9=FAULT_SOFT 的那些采集)
  * 固件源码、镜像、构建产物(本轮不重编)

为什么 5: 真实相对保护只投 EVT_MULTI_FAULT(A/diag_relative.c:725) -> ss_code_for_event()
返回 FAULT_MULTI_SOURCE(A/safety_state.c:309), 而 A/safety_state.h:46 定义 FAULT_MULTI_SOURCE=5u。
9 = FAULT_SOFT(A/safety_state.h:50), 只由 EVT_SOFT_FAULT(软件触发/safety_trip)产生, 而 safety_trip
在各场景的 forbidden_commands 里 —— 所以钉 9 会把一次**成功**的保护判成取证未完成。

用法: python apply_c02_event5_fixtures.py [--check]   (--check 只判定不写盘)
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
R3 = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field"
R4 = ROOT / "reports/reviews/phase8-m8-idle-stack-r4/field"

HEADER = "slot,seq,session,time_ms,event,valid,sg,current_ma,ax_mg,ay_mg,az_mg,step_hz,vibration_mg,flags"
OLD_EVENT, NEW_EVENT = "9", "5"

TARGETS = [
    R3 / "plan.json",
    R3 / "plan_continuation_s2.json",
    R3 / "rows.json",
    R4 / "rows.json",
]

FLASH_FILES = [
    "applications/ns_flash_shell.c:159-161 (逐槽行的打印: 第 5 列是 s.event, 第 12 列是 step_hz)",
    "A/safety_state.h:46 FAULT_MULTI_SOURCE=5u; :50 FAULT_SOFT=9u(已用编号是黑匣子持久化格式, 不得重排)",
    "A/safety_state.c:309 ss_code_for_event(): event & EVT_MULTI_FAULT => FAULT_MULTI_SOURCE",
    "A/diag_relative.c:725 相对保护的 severe 边沿只投 safety_post_event(EVT_MULTI_FAULT)",
    "reports/reviews/phase8-motor-m5-fault-log/cp1/fault_attempt/012_flash_logstat.bin "
    "(M5 软件触发的历史原件: 那一批才是 event=9; 本轮不改写它)",
]

FLASH_PROVENANCE = {
    "kind": "source_shape",
    "files": FLASH_FILES,
    "note": "形状取自产品打印语句(ns_flash_shell 的 rt_snprintf 与表头 rt_kputs), 数值为合成。"
            "全库检索: 没有任何一次**板上采集**给出 flash_dump 的 CSV 行 —— 这些行只出现在夹具与 "
            "reports/reviews/phase8-m8-relative-protection-r3/offline/psrun-*/** 的离线运行输出里, "
            "因此本组不是实测件, 不得当现场结论。event 列本轮按 C-02 由 9 更正为 5: 真实相对保护走 "
            "EVT_MULTI_FAULT => FAULT_MULTI_SOURCE=5u; 9=FAULT_SOFT 只由软件触发产生。更正前钉 9 会把"
            "一次成功的保护判成 NO_FRAME_WITH{event:9} => FLASH_FRAMES_NOT_RECOVERED。",
}

LEARN_ADC68 = {
    "_provenance": {
        "kind": "source_shape",
        "files": ["applications/diag_relative.c:1600 (learn 成功支的打印形状)",
                  "A/sensor_service.c:37-38 SENSOR_PERIOD_MS=10 / SENSOR_SG_DIV=10 => SG 10 Hz"],
        "note": "C-01 第 3 节已用过的大样本样例(ADC n=68, SG 仍 n=8): 把 learn 的健康面测到**过量采样**, "
                "不再只落在 must_min 的 8/8 边界上。行形状取自产品打印, 数值合成, 不构成板上学习结论。 "
                "本键只被 field/m8r3_field_selftest.py 的 FS76 使用, 现场计划仍用自己的键。",
    },
    "healthy": [
        "[DIAG] learn OK adc[2050..2090]n68 nz3 sg[68..75]n8 seq1001..1068",
        "[DIAG] learn thr tier=0 dev=-45/-90 drop=40/70 FIELD_UNVERIFIED",
    ],
}
LEARN_ADC68_KEY = "diag_profile learn 8 80 120 0@adc68_over_sample"


def load_keep_shape(path):
    """读 JSON 并**保留原缩进与结尾换行**: 这轮的更正只应改数据, 不该把 6 万字的计划重排一遍。"""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\{[^\S\n]*\n([ \t]+)\"", text)
    indent = len(m.group(1).replace("\t", "    ")) if m else 2
    return json.loads(text), indent, text.endswith("\n")


def dump_keep_shape(doc, indent, trailing):
    return json.dumps(doc, ensure_ascii=False, indent=indent) + ("\n" if trailing else "")


def walk(node, fn):
    """对 JSON 树里每个 dict 调 fn(node)(列表/标量只穿行)。"""
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def fix_rows(path, write):
    doc, indent, trailing = load_keep_shape(path)
    cmds = doc["commands"]
    grp = cmds.get("flash_dump 0 8")
    changed = []
    if grp is not None:
        for prof, rows in list(grp.items()):
            if prof.startswith("_") or not isinstance(rows, list):
                continue
            for i, line in enumerate(rows):
                p = str(line).split(",")
                if len(p) != 14 or p[0] == "slot":
                    continue
                if p[4] == NEW_EVENT:
                    continue
                if p[4] != OLD_EVENT:
                    raise SystemExit("%s: %s 第 %d 行 event=%s, 既不是 %s 也不是 %s —— 先人工核对"
                                     % (path.name, prof, i, p[4], OLD_EVENT, NEW_EVENT))
                p[4] = NEW_EVENT
                rows[i] = ",".join(p)
                changed.append((prof, p[0]))
        if "_provenance" not in grp:
            grp["_provenance"] = FLASH_PROVENANCE
            changed.append(("(_provenance 新增)", "source_shape"))
        elif grp["_provenance"].get("kind") != "source_shape":
            raise SystemExit("%s: flash_dump 的 _provenance 已存在但 kind 不是 source_shape" % path.name)
    if LEARN_ADC68_KEY not in cmds:
        cmds[LEARN_ADC68_KEY] = json.loads(json.dumps(LEARN_ADC68, ensure_ascii=False))
        changed.append((LEARN_ADC68_KEY, "adc68_sg8"))
    doc["_c02_event_note"] = ("停后帧的 event 码: 真实相对保护 => FAULT_MULTI_SOURCE=5u"
                              "(A/safety_state.h:46, safety_state.c:309, diag_relative.c:725)。"
                              "本文件内 flash_dump 夹具的 event 列已由 9 更正为 5; 9=FAULT_SOFT 属于"
                              "软件触发(M5 原件), 那些历史件不在本轮范围。计划侧同一更正见 "
                              "plan.json / plan_continuation_s2.json / plan_core_r4.json 的 require_frames。")
    if write:
        path.write_text(dump_keep_shape(doc, indent, trailing), encoding="utf-8", newline="\n")
    return changed


def fix_plan(path, write):
    doc, indent, trailing = load_keep_shape(path)
    hits = []

    def f(node):
        for spec in node.get("require_frames") or []:
            for need in spec.get("must_include") or []:
                if need.get("event") == OLD_EVENT:
                    need["event"] = NEW_EVENT
                    hits.append(node.get("id"))
                if need.get("event") == NEW_EVENT and "step_hz" not in need:
                    need["step_hz"] = "0"
    walk(doc, f)
    if write and hits:
        path.write_text(dump_keep_shape(doc, indent, trailing), encoding="utf-8", newline="\n")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只判定不写盘")
    a = ap.parse_args()
    write = not a.check
    for p in TARGETS:
        if not p.is_file():
            print("MISSING|%s" % p)
            return 2
    n_written = 0
    for p in (R3 / "plan.json", R3 / "plan_continuation_s2.json"):
        hits = fix_plan(p, write)
        n_written += 1 if (write and hits) else 0
        print("C02_PLAN|%s|stages=%s|written=%s" %
              (p.name, ",".join([h or "?" for h in hits]) or "NONE", bool(write and hits)))
    for p in (R3 / "rows.json", R4 / "rows.json"):
        ch = fix_rows(p, write)
        n_written += 1 if write else 0
        print("C02_ROWS|%s|rows_or_keys=%d|written=%s" % (p.name, len(ch), write))
        for prof, slot in ch[:20]:
            print("   CHANGE|%s|%s" % (prof, slot))
    print("device_actions=0|builds=0|files_written=%d|scope=%s" %
          (n_written, ",".join(p.name for p in TARGETS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
