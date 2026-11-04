# -*- coding: utf-8 -*-
"""V0.3 桌面实测探针: 只调用应用自己的按钮回调, 不改应用源码; 判据取自控件与变量, 不靠肉眼。

用法: python tools/demo_gui_probe.py --pkg PKGDIR --frame 3 --dump OUT.json [--geometry 1180x820] [--hold 12]

产出的 JSON 直接按 UTF-8 + LF 落盘, 不经控制台, 避免 GBK 转码把中文变成乱码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import tkinter as tk
from pathlib import Path

CONTROL_BUTTONS = ["播放全部记录", "暂停回放", "下一步记录", "回到启动记录"]
FUTURE_BUTTONS = ["启动电机 · 待联调", "保护触发 · 待验证", "故障恢复 · 待验证"]


def walk(w, acc):
    for c in w.winfo_children():
        acc.append(c)
        walk(c, acc)
    return acc


def bbox(root, w):
    x = w.winfo_rootx() - root.winfo_rootx()
    y = w.winfo_rooty() - root.winfo_rooty()
    return [x, y, x + w.winfo_width(), y + w.winfo_height()]


def inside(root, box):
    return (box[0] >= 0 and box[1] >= 0
            and box[2] <= root.winfo_width() and box[3] <= root.winfo_height())


def var_text(w):
    tv = w.cget("textvariable")
    return w.tk.getvar(tv) if tv else w.cget("text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True, type=Path)
    ap.add_argument("--frame", required=True, type=int)
    ap.add_argument("--dump", required=True, type=Path)
    ap.add_argument("--geometry", default="1180x820")
    ap.add_argument("--hold", type=float, default=12.0)
    a = ap.parse_args()
    pkg = a.pkg.resolve()
    sys.path.insert(0, str(pkg))
    import demo

    report = {"package": str(pkg), "geometry": a.geometry, "target_frame": a.frame,
              "buttons": [], "future_buttons": [], "string_vars": [], "raw_text": None,
              "tabs": [], "client": None, "verdict": {}}
    orig = tk.Tk.mainloop

    def patched(self, n=None):
        # 必须走一次完整 update(): 只 update_idletasks 时控件尚未映射, winfo_rootx/y 会返回 0,
        # 位置判据就会算出全负的 bbox(我第一版就是这么误判"按钮在客户区外"的)。
        self.update_idletasks()
        self.update()
        widgets = walk(self, [])
        buttons = [w for w in widgets if isinstance(w, tk.Button)]
        by_text = {w.cget("text"): w for w in buttons}
        # 用应用自己的回调走到第 frame 帧: 先"回到启动记录", 再按 (frame-1) 次"下一步记录"
        by_text["回到启动记录"].invoke()
        for _ in range(a.frame - 1):
            by_text["下一步记录"].invoke()
            self.update_idletasks()
        self.update()
        for name in CONTROL_BUTTONS:
            w = by_text[name]
            b = bbox(self, w)
            report["buttons"].append({"text": name, "bbox": b, "inside_client": inside(self, b),
                                      "state": str(w.cget("state"))})
        for name in FUTURE_BUTTONS:
            w = by_text.get(name)
            if w is not None:
                report["future_buttons"].append({"text": name, "state": str(w.cget("state"))})
        seen = set()
        for w in widgets:
            if isinstance(w, tk.Label):
                t = var_text(w)
                if t and t not in seen:
                    seen.add(t)
                    report["string_vars"].append(t)
        tw = [w for w in widgets if isinstance(w, tk.Text)]
        if tw:
            content = tw[0].get("1.0", "end-1c")
            report["raw_text"] = {"chars": len(content), "sha256": hashlib.sha256(
                content.encode("utf-8")).hexdigest(), "head": content[:200]}
        nb = [w for w in widgets if type(w).__name__ == "Notebook"]
        if nb:
            report["tabs"] = [nb[0].tab(i, "text") for i in range(len(nb[0].tabs()))]
        report["client"] = [self.winfo_width(), self.winfo_height()]
        arm = [t for t in report["string_vars"] if "arm 门" in t and "当前频率" in t]
        report["verdict"] = {
            "all_control_buttons_inside": all(x["inside_client"] for x in report["buttons"]),
            "source_line_present": any("第 " in t and "SHA256" in t for t in report["string_vars"]),
            "source_line": next((t for t in report["string_vars"] if "第 " in t and "SHA256" in t), None),
            "facts_block_arm_gate": arm[0].replace("\n", " | ") if arm else None,
            "arm_gate_mentions_all": [t.replace("\n", " | ") for t in report["string_vars"] if "arm 门" in t],
            "param_line": next((t for t in report["string_vars"] if t.startswith("固件设置值")
                                or t.startswith("本段记录")), None)}
        a.dump.parent.mkdir(parents=True, exist_ok=True)
        with open(a.dump, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        deadline = time.monotonic() + a.hold
        while time.monotonic() < deadline:
            self.update()
            time.sleep(0.05)
        self.destroy()

    tk.Tk.mainloop = patched
    try:
        demo.run_gui(demo.load_data(pkg / "data" / "replay.json"), a.geometry)
    finally:
        tk.Tk.mainloop = orig
    print("DUMP " + str(a.dump))
    return 0


if __name__ == "__main__":
    sys.exit(main())
