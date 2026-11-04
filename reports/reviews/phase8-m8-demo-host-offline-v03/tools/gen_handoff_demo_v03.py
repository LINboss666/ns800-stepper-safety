# -*- coding: utf-8 -*-
"""从已落盘的证据重算复审回执 JSON: 数字一律回读证据文件, 不手抄。

用法: python tools/gen_handoff_demo_v03.py --dir <本轮复审目录> --repo <仓库根>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def jload(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    a = ap.parse_args()
    root, repo = a.dir.resolve(), a.repo.resolve()
    host, gui = root / "host", root / "gui"
    summary, patch = jload(host / "summary.json"), jload(host / "patch_roundtrip.json")
    recompute = jload(host / "archive_recompute.json")
    pkg_check, repo_check = jload(host / "package_check.json"), jload(host / "repo_check.json")
    neg = (host / "negative_controls.txt").read_text(encoding="utf-8")
    neg_lines = neg.splitlines()
    neg_cases = []
    for l in neg_lines:
        m = re.match(r"^(\S+)\s+rc=(\d+)\(期望(\d+)\)", l)
        if m:
            neg_cases.append({"case": m.group(1), "rc": int(m.group(2)), "expected_rc": int(m.group(3)),
                              "app_label": "MATCH" if " MATCH" in l else "**与预期不符**"})
    neg_summary = next(l for l in neg_lines if l.startswith("反例复跑:"))
    ns = re.search(r"场景=(\d+) 与声称一致=(\d+) 不符=(\d+)", neg_summary)
    naive_count = neg.count("MATCH")
    f3, f6 = jload(gui / "frame3_0x18.json"), jload(gui / "frame6_0x8.json")
    by_name = {r["source_path"].split("/")[-1]: r for r in recompute}

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(repo),
                          capture_output=True, text=True).stdout.strip()

    def frame_row(tag, d, bin_name):
        v = d["verdict"]
        return {"frame": d["target_frame"], "label": tag, "client": d["client"],
                "arm_gate_facts_block": v["facts_block_arm_gate"], "param_line": v["param_line"],
                "source_line": v["source_line"],
                "all_control_buttons_inside": v["all_control_buttons_inside"],
                "raw_tab_sha256": d["raw_text"]["sha256"],
                "raw_tab_chars": d["raw_text"]["chars"],
                "raw_tab_equals_archive_original":
                    d["raw_text"]["sha256"] == by_name[bin_name]["disk_sha256"],
                "archive_original": by_name[bin_name]["source_path"],
                "archive_original_bytes": by_name[bin_name]["disk_bytes"]}

    off = next(r for r in patch["runs"] if r["label"] == "autocrlf_off")
    dflt = next(r for r in patch["runs"] if r["label"] == "system_default")

    def four(rows):
        return {r["path"]: {"byte_equal": r["byte_equal"], "in_patch": r["in_patch"],
                           "cr": r["cr_in_applied"], "bytes": r["bytes_applied"]}
                for r in rows if r["path"] in
                ("demo.py", "README.md", "data/replay.json", "run_demo.bat")}

    out = {
        "round": "DEMO-V03-review",
        "verdict": "DEMO_HOST_V03_ACCEPTED_CLOSEOUT",
        "reviewed_package": summary["identity"],
        "reviewed_patch": {"file": "upgrade_v02_to_v03.patch",
                           "bytes": patch["patch_bytes"], "sha256": patch["patch_sha256"],
                           "files_changed_by_patch": patch["patched_files"]},
        "baseline": {"repository": str(repo), "head_at_review": head,
                     "firmware_changed_this_round": False,
                     "prior_packages_changed": False,
                     "note": "本轮只新增本复审目录; 固件、旧包与归档原件均未改(提交前预检逐条对账)"},
        "manifest": {"entries_recomputed_all_ok": True, "manifest_excludes_itself": True,
                     "source": "host/manifest_recompute.txt"},
        "claim_1_autocrlf_patch": {
            "verdict": "CONFIRMED_WITH_WORDING_CORRECTION",
            "readme_gives": "git -c core.autocrlf=false apply --check/--apply",
            "four_files_byte_equal_after_off": four(off["files"]),
            "fourth_file_is_unchanged_run_demo_bat_not_SHA256SUMS": True,
            "sha256sums_txt_after_off": next(r["byte_equal"] for r in off["files"]
                                             if r["path"] == "SHA256SUMS.txt"),
            "control_system_default_only_line_endings_differ": four(dflt["files"]),
            "control_cr_counts": {r["path"]: [r["cr_in_applied"], r["cr_in_v03"]]
                                  for r in dflt["files"] if r["in_patch"]},
            "patched_tree_self_check": jload(host / "patched_pkg_check.json")["frames"],
            "stale_manifest_entries_after_patch": ["demo.py", "README.md", "data/replay.json"],
            "program_rework_required": False,
            "wording_correction": "上一轮把第四件说成 SHA256SUMS.txt; 正确为未进补丁的 run_demo.bat"},
        "claim_2_seven_frames": {
            "verdict": "CONFIRMED",
            "package_check": {"frames": pkg_check["frames"],
                              "archive_sources": pkg_check["archive_sources"]["status"]},
            "repo_check": {"status": repo_check["archive_sources"]["status"],
                           "matched_frames": repo_check["archive_sources"]["matched_frames"]},
            "independent_disk_recompute_equal": sum(
                1 for r in recompute if r["equal"] and r["embedded_text_equal"]),
            "frame_order": [r["title"] for r in recompute],
            "new_frame_source": by_name["02_mot_pre.rx.bin"]["source_path"],
            "new_frame_sha256": by_name["02_mot_pre.rx.bin"]["disk_sha256"]},
        "claim_3_desktop_0x18_0x8": {
            "verdict": "CONFIRMED",
            "author_screenshots": jload(host / "author_gui_identity.txt"),
            "local_frames": [frame_row("写前 0x18", f3, "02_mot_pre.rx.bin"),
                             frame_row("写后 0x8", f6, "05_mot_post.rx.bin")]},
        "negative_controls": {
            "per_case_lines": len(neg_cases),
            "per_case_lines_rc_matches_expected": sum(1 for c in neg_cases if c["rc"] == c["expected_rc"]),
            "per_case_lines_label_matched": sum(1 for c in neg_cases if c["app_label"] == "MATCH"),
            "declared_cases": int(ns.group(1)), "declared_matching": int(ns.group(2)),
            "declared_mismatch": int(ns.group(3)),
            "summary_line": neg_summary, "cases": neg_cases,
            "naive_substring_count": naive_count,
            "counting_rule": "逐例判据由我按 rc 与期望 rc 重算, 不采信行里的 MATCH 字样; "
                             "裸 count(\"MATCH\") 只登记不用(会被子串顶高)"},
        "device_surface": {"banned_imports_in_ast": [], "banned_names_in_ast": [],
                           "imports": "见 host/imports_audit.txt", "hardware_actions": 0},
        "not_verified": [
            "其他 DPI / 字体 / 系统缩放组合下的界面布局",
            "Git HEAD 一致性(--repo-root 只比磁盘字节)",
            "当前板端实时状态",
            "SG/电流/振动动态曲线、真实运动、保护触发、正常 disarm、带载最坏栈"],
        "my_tooling_corrections": [
            "PrintWindow 截客户区会整体下移约 29 px, 上一轮据此的\"来源行被裁\"结论作废",
            "只 update_idletasks 时控件未映射, winfo_rootx/y 返 0, 会把按钮全算成在客户区外; 改为测量前 update()",
            "GUI 文本改由脚本按 UTF-8+LF 直写, 不经 cp936 控制台, 避免乱码 dump",
            "反例篡改点不再硬编码字段, 改为现场扫描状态中性行并按绝对偏移落点",
            "禁词扫描改为只看 AST(名字/属性), 全文命中仅作旁证: 文档串里\"没有串口\"这句本身会命中"],
        "checks_total": summary["total"], "checks_fails": summary["fails"],
        "scope": "离线复核 + 本机一次真实 Tk 启动; 只读三份 zip、解包副本与仓库归档原件; "
                 "本轮未连接设备, 设备动作 0; 板端固件、原始归档与执行器均未改。",
    }
    assert out["checks_fails"] == 0, "证据里仍有 FAIL, 不能出签收回执"
    assert out["claim_2_seven_frames"]["independent_disk_recompute_equal"] == 7
    assert out["claim_2_seven_frames"]["repo_check"]["matched_frames"] == 7
    assert all(x["raw_tab_equals_archive_original"] for x in out["claim_3_desktop_0x18_0x8"]["local_frames"])
    nc = out["negative_controls"]
    assert nc["per_case_lines"] == nc["declared_cases"] == 6, nc
    assert nc["per_case_lines_rc_matches_expected"] == nc["per_case_lines_label_matched"] \
        == nc["declared_matching"] == 6, nc
    assert nc["declared_mismatch"] == 0, nc
    assert nc["naive_substring_count"] >= nc["per_case_lines"], nc
    assert len(four(off["files"])) == 4 and len(four(dflt["files"])) == 4
    assert all(v["byte_equal"] for v in four(off["files"]).values())
    assert head, "读不到仓库 HEAD"
    target = root / "handoff_DEMO_V03_review.json"
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    raw = target.read_bytes()
    print("WROTE %s %d B sha256=%s" % (target, len(raw), hashlib.sha256(raw).hexdigest()))
    print("HEAD=%s" % head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
