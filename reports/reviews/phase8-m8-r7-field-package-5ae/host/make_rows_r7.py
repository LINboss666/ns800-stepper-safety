#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成本轮离线干跑夹具 field/rows.json —— **不是现场实抓数据的副本**。

为什么要另造一份: R7 的阶段链要能在零设备上真实跑通(整包贯通), 而 5ae 把两条超长打印拆成了短行,
旧实抓行表里那些行**在新镜像上不会再以那个形状出现**。干跑用的响应因此分两种来源, 每条都标出处:
  * kind=board_capture          : 逐行照抄 r3 field/rows.json 的现场实抓(未受本轮产品改动影响的行)
  * kind=predicted_from_source  : 由 44e493c 的产品源码字面量拼出的**预测行**(带 文件:行号)
这份夹具只证明"阶段/额度/argv/判据在形状上自洽", **不证明**行宽或产品行为 —— 行宽的证据是现场抓到的
原始字节 + 产品侧 host/line_width_check.py 的保守口径, 二者都不能被这份夹具代替。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FIELD = HERE.parent / "field"
SRC = REPO / "reports/reviews/phase8-m8-relative-protection-r3/field/rows.json"
SRC_COMMIT = "44e493cea9bbbc35e18394ca23dd1a8502519448"
APP = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
       "ns800rt7p65-nssinepad/applications")
# 整表照抄 r3 的那份实抓行表(不是只挑本包用得到的几条): 执行器在会话收尾时会按 CLEANUP_CMDS
# 逐条要 motor_stop/motor_disarm/motor_status 的行(缺一条就 ROWS_NOT_DECLARED 具名停),
# 只挑子集会造出一个"干跑因为夹具缺行而停"的假信号。本包不起转, 那三行因此只是备而不用。
USE = None                      # None => 全部命令块都照抄


def kprintf(rel_path, lines_1based):
    """取 C 源码里一条 rt_kprintf 的完整字面量(相邻字面量拼接), 原样带格式说明符。"""
    src = (REPO / rel_path).read_text(encoding="utf-8").splitlines()
    body = " ".join(src[n - 1].strip() for n in lines_1based)
    lits, i = [], 0
    while True:
        j = body.find(chr(34), i)
        if j < 0:
            break
        k = j + 1
        while k < len(body) and body[k] != chr(34):
            k += 2 if body[k] == chr(92) else 1
        lits.append(body[j + 1:k])
        i = k + 1
    if not lits:
        raise SystemExit("R7_ROWS_REFUSE|%s:%s 取不到字面量" % (rel_path, lines_1based))
    return "".join(lits).replace(chr(92) + "n", "")


def fill(text, values):
    out, idx = [], 0
    for m in re.finditer("%[-+ #0]*[0-9.]*[sdioxXeEgGfup]", text):
        out.append(text[idx:m.start()])
        out.append(str(values[m.group(0)[-1]]))
        idx = m.end()
    out.append(text[idx:])
    return "".join(out)


def main():
    doc = json.loads(SRC.read_bytes().decode("utf-8-sig"))
    cap = doc["commands"]
    limit_cfg = fill(kprintf("%s/safety_thread.c" % APP, [374]), {"s": "DISABLED_BY_CONFIG"})
    limit_off = kprintf("%s/safety_thread.c" % APP, [376, 377])
    # 5ae 把这一条摘要拆成 8 次 rt_kprintf 调用(逐条字面量取自源码, 数字按最宽口径代入:
    # %u=4294967295, %08X=8 位, 与 tmc2209.c:2839-2840 注释里那条拆分口径一致)
    WID = {"u": "4294967295", "d": "-2147483648", "s": "X" * 18, "X": "00000400", "x": "00000400"}
    tm = "%s/tmc2209.c" % APP
    cfg = [kprintf(tm, [2842]), kprintf(tm, [2843]), kprintf(tm, [2844]),
           fill(kprintf(tm, [2845]), WID), fill(kprintf(tm, [2847]), WID),
           fill(kprintf(tm, [2849]), WID), fill(kprintf(tm, [2852]), WID),
           fill(kprintf(tm, [2854]), WID)]
    out = {"_about": {
        "what": "M8-R7 离线干跑夹具(整包贯通用), 不是现场实抓数据的副本",
        "two_sources": ["board_capture: 照抄 r3 field/rows.json 的现场实抓行",
                        "predicted_from_source: 由 %s 的产品源码字面量拼出的预测行" % SRC_COMMIT[:12]],
        "not_evidence_of": ("行宽与产品行为都不由本夹具证明: 行宽看现场抓到的原始字节 + "
                            "host/line_width_check.py 的保守口径; 产品行为要现场实抓"),
        "source_rows_file": SRC.relative_to(REPO).as_posix()},
        "commands": {}}
    for name in (sorted(cap) if USE is None else USE):
        blk = json.loads(json.dumps(cap[name]))
        out["commands"][name] = blk
        blk.setdefault("_provenance", {})
        blk["_provenance"]["copied_from"] = "r3 field/rows.json:%s" % name
        if name == "safety_irq_status":
            old = [l for l in blk["healthy"] if "LIMIT inputs config" in l]
            blk["healthy"] = [limit_cfg, limit_off] + \
                [l for l in blk["healthy"] if "LIMIT inputs config" not in l and "LIMIT OFF" not in l]
            blk["_provenance_r7"] = {
                "kind": "predicted_from_source", "commit": SRC_COMMIT,
                "replaced": [old[0] if old else "(没有旧行)", "%s/safety_thread.c:374-376" % APP,
                             "r3 那一次实抓的 LIMIT 行是 126 B(超 120), 5ae 起改为两条短行"]}
        if name == "tmc_config_apply CONFIRM":
            old = list(blk["healthy"])
            blk["healthy"] = list(cfg)
            blk["_provenance_r7"] = {
                "kind": "predicted_from_source", "commit": SRC_COMMIT,
                "replaced": old,
                "why": ("旧实抓是**一条 124 B** 的 CONFIGURED 摘要(超 120 => 控制台会剪尾); "
                         "5ae 拆成 %s/tmc2209.c:2842..2854 的 8 次调用, 预测行逐条取自那些字面量" % APP)}
    (FIELD / "rows.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + chr(10),
                                      encoding="utf-8", newline="\n")
    print("R7_ROWS|%s|commands=%d|预测行: safety_irq_status + tmc_config_apply" %
          ((FIELD / "rows.json").relative_to(REPO).as_posix(), len(out["commands"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
