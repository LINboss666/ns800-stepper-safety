#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 IMU 那场补**自己通道**的正控夹具, 并把运动会话的观察窗键分开。

复审实测: 交付包把 IMU 场喂了 SG 的触发数据(`r=REL_SG_SEVERE`、`imu=0`), 首因判据与正控夹具
都是这份数据 => "流程跑完"被当成了"IMU 通道验证成功"。本脚本只加夹具数据, 不改判据代码:

  __watch__@imu            触发三行取 r=REL_IMPACT 且 imu 非零(A/diagnosis.c:469-475 的打印形状,
                           规则名取自 A/diag_relative.h 的 rel_reason_t 与 verdict_name())
  diag_status@latch@imu    锁存视图同样取 REL_IMPACT + imu 非零(A/diagnosis.c 的 rel LATCH 三行)
  __watch__@imu_zero       只给元负控用: 规则名对(REL_IMPACT)但 imu 列仍是 0 —— 用来证明
                           target_channel.source_nonzero_keys 这道腿真的在起作用, 不是摆设

三组都标 kind=source_shape: **形状**来自产品打印语句, 数值合成, 不构成任何板上采集或现场结论。
范围: R4 本轮 rows.json 与仍被引用的 R3 副本; 归档运行输出与 M5 原件不动。
幂等: 键已存在且内容一致时不改写。用法: python apply_target_channel_fixtures.py [--check]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
R3_ROWS = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field/rows.json"
R4_ROWS = ROOT / "reports/reviews/phase8-m8-idle-stack-r4/field/rows.json"

WATCH_IMU = {
    "healthy": [],
    "no_trigger": [],
    "trip": [
        "[DIAG] REL TRIP r=REL_IMPACT id=7 rev=1 post_rc=0",
        "[DIAG] REL TRIP val=61 thr=45 ms=120 cov=0",
        "[DIAG] REL TRIP sg=1008 cur=10041 imu=3789 tick=4588411 n=3",
        "[DIAG] REL trip ticket used; mode=MONITOR_ONLY rearm required",
    ],
    "_provenance": {
        "kind": "source_shape",
        "files": ["applications/diagnosis.c:469-475 (REL TRIP 的三行打印: r= 规则名、val/thr、sg/cur/imu)",
                  "A/diag_relative.h 的 rel_reason_t 与 A/diag_relative.c 的 verdict_name(): "
                  "冲击证据的规则名是 REL_IMPACT(REL_SG_SEVERE/REL_ADC_SEVERE/REL_FUSED_SEVERE 是 SG/ADC 那三路)",
                  "A/diag_relative.c:610 (e->verdict = REL_IMPACT 的判定支)"],
        "note": ("IMU 那场的正控夹具: 触发规则名换成 REL_IMPACT 且 imu 列非零。数值全部合成"
                 "(没有任何板上采集给出过 REL_IMPACT 的触发行), 因此**不构成设备证据**, 只用来让离线"
                 "分类判据有一份形状正确的输入。与 SG 严重那组(__watch__.trip)的唯一区别就是规则名与 imu 列。"),
    },
}

LATCH_IMU = {
    "healthy": [
        "[DIAG] verdict=STALL_SUSPECT mode=MONITOR_ONLY health=OK",
        "[DIAG] rel snap=OK inc=OK seq=12 pubfail=0",
        "[DIAG] rel mode=MONITOR_ONLY prof=PRESENT id=7 rev=1 flg=0005 FIELD_UNVERIFIED",
        "[DIAG] rel cov=COVERED lost=0 gen=1 rearm=1",
        "[DIAG] rel LATCH r=REL_IMPACT id=7 rev=1 ms=120 post_rc=0",
        "[DIAG] rel LATCH val=61 thr=45 n=3 cov=0 tick=4588411",
        "[DIAG] rel LATCH sg=1008 cur=10041 imu=3789",
    ],
    "_provenance": {
        "kind": "source_shape",
        "files": ["applications/diagnosis.c:469-475 与同族的 rel LATCH 三行打印(锁存视图回读同一批字段)",
                  "A/diag_relative.h 的 rel_reason_t(REL_IMPACT)"],
        "note": "IMU 那场取证会话的锁存夹具: 规则名与 imu 列要和**本场**观察窗一致, 否则 S9b 的 "
                "must_equal_recorded 会把跨场拼字段当场抓出来。数值合成, 不是板上采集。",
    },
}


WATCH_IMU_ZERO = {
    "healthy": [],
    "no_trigger": [],
    "trip": [
        "[DIAG] REL TRIP r=REL_IMPACT id=7 rev=1 post_rc=0",
        "[DIAG] REL TRIP val=61 thr=45 ms=120 cov=0",
        "[DIAG] REL TRIP sg=1008 cur=10041 imu=0 tick=4588411 n=3",
        "[DIAG] REL trip ticket used; mode=MONITOR_ONLY rearm required",
    ],
    "_provenance": {
        "kind": "source_shape",
        "files": ["applications/diagnosis.c:469-475(REL TRIP 三行的字段形状)",
                  "A/diag_relative.h 的 rel_reason_t(REL_IMPACT)"],
        "note": ("**元负控专用夹具, 不是正控**: 规则名换成 REL_IMPACT 但 imu 列仍是 0 —— 只用来证明"
                 "target_channel.source_nonzero_keys 这道腿真的在起作用(把该腿摘掉后这份数据必须"
                 "被判成通过, 否则判据不足)。没有任何板上采集给出过这一行, 不构成设备证据。"),
    },
}


def load_keep_shape(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\{[^\S\n]*\n([ \t]+)\"", text)
    indent = len(m.group(1).replace("\t", "    ")) if m else 1
    return json.loads(text), indent, text.endswith("\n")


def apply(path, write):
    doc, indent, trailing = load_keep_shape(path)
    cmds = doc["commands"]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    added = []
    for key, val in (("__watch__@imu", WATCH_IMU), ("diag_status@latch@imu", LATCH_IMU),
                     ("__watch__@imu_zero", WATCH_IMU_ZERO)):
        if key not in cmds:
            cmds[key] = json.loads(json.dumps(val, ensure_ascii=False))
            added.append(key)
        elif cmds[key].get("trip") != val.get("trip") or \
                cmds[key].get("_provenance", {}).get("kind") != "source_shape":
            raise SystemExit("%s: 键 %s 已存在但内容不同 —— 先人工核对再改" % (path.name, key))
        else:
            added.append(key + "(已存在)")
    doc["_target_channel_note"] = (
        "运动会话的观察窗键分场: SG/ADC 场用 __watch__(r=REL_SG_SEVERE, imu=0), IMU 场用 "
        "__watch__@imu(r=REL_IMPACT, imu 非零); 锁存视图同理分 diag_status@latch 与 @imu。"
        "三个键都是 source_shape(形状取自产品打印语句, 数值合成, 不是板上采集)。执行器按计划里的 "
        "target_channel 声明分类: 规则名不对或该通道源样本为 0 只登记 OTHER_SOURCE_TRIGGERED"
        "(目标通道未证), 撤能与取证照做, 但不计成保护已验证。")
    if write:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=indent) + ("\n" if trailing else ""),
                        encoding="utf-8", newline="\n")
    return added, before, hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    for p in (R3_ROWS, R4_ROWS):
        added, before, after = apply(p, not a.check)
        print("TARGET_FIXTURES|%s|%s|written=%s|sha=%s->%s" %
              (p.parent.name + "/" + p.name, ",".join(added), not a.check, before[:10], after[:10]))
    print("device_actions=0|builds=0|固件与镜像未动|归档输出未动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
