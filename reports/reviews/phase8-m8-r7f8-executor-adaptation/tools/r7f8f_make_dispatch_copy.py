#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""r7f8f_make_dispatch_copy.py —— 从**已交付原件**逐格派生真实 dispatch 承接件(不手抄)。

为什么要有这个工具: 承接件与原件的唯一合法差别只有下面 CHANGES 里那 9 格(机械前置声明、状态改名、
批准记录、本轮硬件动作口径)。上一版(F11)是我手工编辑的, 计划身份一变就要重抄, 抄错一格就是"批准
绑到别的字节上"。这里改成派生: 其余每一格都必须与原件相等, 派生完当场用扁平 diff 复算并拒绝任何
多出来的差异。

批准令牌只能由操作者原话给出: --token/--approval-id 缺省 => 本件写 NOT_APPROVED, 状态是草案,
运行时 --approval-init 用不了它(那需要真实令牌)。本轮(修包之后)**没有**第二次开口的批准, 所以
默认产出的是草案; 不得把它当批准件 dispatch。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
SRC_REL = ROUND + "/field/plan_r7f8f_2369_real.json"

# 承接件里必须由**现场事实**填写的三格(F11 那次操作者在会话内逐字给的)。这些是"事实声明",
# 不是批准令牌; 借过来记录来源, 不冒充新一轮的批准。
PREREQ_DECLARED_BY = "操作者(现场)于 2026-11-05 会话内逐字声明(承 F11 那一次核对, 未重新核对)"
PREREQ_VERBATIM = ("已核对，记 PROVIDED 并整包执行(§4 机械/电气前置) + COM5 空闲,可以开"
                   " + 令牌原话「执行 F11 整包实机验证」")


def flat(d, p="", out=None):
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            flat(v, p + "/" + k, out)
    elif isinstance(d, list):
        for i, v in enumerate(d):
            flat(v, p + "/%d" % i, out)
    else:
        out[p] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--out", required=True, help="承接件文件名(在 field/ 下, 必须尚不存在)")
    ap.add_argument("--token", help="操作者批准令牌原话; 不给 => 草案(未批准)")
    ap.add_argument("--approval-id", help="批准账 id; 不给 => 草案(未批准)")
    ap.add_argument("--ledger-root", default="D:/ns800_r7f8f_offline/approvals")
    a = ap.parse_args()
    repo = a.repo.resolve()
    src = repo / SRC_REL
    src_b = src.read_bytes()
    doc = json.loads(src_b.decode("utf-8"))
    out_path = repo / ROUND / "field" / a.out
    if out_path.exists():
        raise SystemExit("REFUSED|承接件已存在, 不覆盖已交付产物(要出新件请换文件名): %s" % out_path)
    approved = bool(a.token and a.approval_id)

    n_steps = len(doc["stages"][0]["steps"])
    quota = doc.get("quota") or doc["stages"][0].get("quota") or {}
    doc["status"] = ("FIELD_DISPATCH_ONE_SESSION_APPROVED_NOT_YET_RUN" if approved
                     else "FIELD_DISPATCH_DRAFT_NOT_APPROVED_NEW_PLAN_IDENTITY")
    mp = doc["mechanical_prerequisites"]["bench_continuity"]
    old_status = mp.get("status")
    mp["status"] = "PROVIDED"
    mp["declared_by"] = PREREQ_DECLARED_BY
    mp["declared_verbatim"] = PREREQ_VERBATIM
    doc["approval"] = {
        "note": doc.get("approval", {}).get("note", ""),
        "approval_id": a.approval_id or "NOT_ISSUED",
        "token_verbatim": a.token or "NOT_ISSUED_等待操作者对新身份的真实原话",
        "ledger_root_offline": a.ledger_root,
        "dispatch_copy_note": (
            "本件由 tools/r7f8f_make_dispatch_copy.py 从已交付原件 %s/%s… 派生, 与原件的唯一差别"
            "就是下面这 9 格: 机械前置改成 PROVIDED(来源=操作者原话, 不是计划自证, 也不是板上实测)、"
            "status 改名、approval 里记令牌与账本位置。%d 步/额度/三段预算/镜像身份一字未动 => "
            "计划身份哈希因此不同, 交付时两件并列对账。批准状态=%s。"
            % (len(src_b), hashlib.sha256(src_b).hexdigest()[:12], n_steps,
               "已给令牌" if approved else "**未批准**(草案: --approval-init 用不了本件)")),
    }
    doc["hardware_actions_this_round_note"] = (
        "hardware_actions_this_round=0 描述的是离线准备提交; 本件是真实 dispatch 的承接件, 本次会发生"
        "的动作以 quota 为准(%s), 不下载/不回读/不复位/不动分析仪/不断送电/不重试。"
        % (",".join("%s=%s" % (k, v) for k, v in sorted(quota.items())) or "未申报额度"))

    # 派生完立刻复算: 差异必须**恰好**是声明的那 9 格, 多一格都不许出货。
    fa, fb = flat(json.loads(src_b.decode("utf-8"))), flat(doc)
    paths = sorted(set(fa) | set(fb))
    diff = [p for p in paths if fa.get(p, "<abs>") != fb.get(p, "<abs>")]
    want = {"/status", "/approval/approval_id", "/approval/token_verbatim",
            "/approval/ledger_root_offline", "/approval/dispatch_copy_note",
            "/approval/note", "/hardware_actions_this_round_note",
            "/mechanical_prerequisites/bench_continuity/status",
            "/mechanical_prerequisites/bench_continuity/declared_by",
            "/mechanical_prerequisites/bench_continuity/declared_verbatim"}
    extra = [p for p in diff if p not in want]
    missing = sorted(p for p in want - {"/approval/note"} if p not in diff)
    if extra or missing:
        raise SystemExit("REFUSED|派生差异与申报不符: 多=%s 少=%s" % (extra, missing))
    txt = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    out_path.write_text(txt, encoding="utf-8", newline="\n")
    b = out_path.read_bytes()
    print("WROTE|%s|bytes=%d|sha256=%s|approved=%s|diff_paths=%d|src_status=%s=>PROVIDED"
          % (out_path.relative_to(repo).as_posix(), len(b), hashlib.sha256(b).hexdigest(),
             approved, len(diff), old_status))
    print("SRC|%s|bytes=%d|sha256=%s" % (SRC_REL, len(src_b), hashlib.sha256(src_b).hexdigest()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
