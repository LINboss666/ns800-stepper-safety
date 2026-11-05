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

# 承接件里必须由**本轮现场事实**填写的三格。这些是"事实声明", 不是批准令牌; 记来源但不冒充
# 新一轮的批准。F21 起这两句改成命令行给(不再写死在工具里): F20 那一版的默认值就是 F20 的原话,
# 留在源码里等于下一轮我不传参就会把上一轮的现场事实悄悄带进新承接件 —— 那正是 A 反复要防的形状。
PREREQ_DRAFT_BY = "本轮未声明(草案)"
PREREQ_DRAFT_VERBATIM = ("草案件不给机械/电气前置结论: 现场前置只能由操作者在**本轮**批准同一条消息里"
                         "逐字声明, 不沿用 F11/F20 那两次的核对文本。本件因此不是批准件, 不许 dispatch。")


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
    ap.add_argument("--token-file",
                    help="从文件读操作者原话(**推荐**): 与执行器的 token_bytes_from_file 同一口径 —— "
                         "读字节、只裁尾随 CR/LF、严格 UTF-8 解码, 不经控制台打印与 shell 变量转运")
    ap.add_argument("--approval-id", help="批准账 id; 不给 => 草案(未批准)")
    ap.add_argument("--prereq-declared-by",
                    help="本轮机械/电气前置的声明人(批准件必填; 草案一律写「本轮未声明(草案)」)")
    ap.add_argument("--prereq-verbatim",
                    help="本轮机械/电气前置的**逐字原话**(批准件必填, 不许沿用上一轮)")
    ap.add_argument("--ledger-root", default="D:/ns800_r7f8f_offline/approvals")
    ap.add_argument("--approved-prefixes", default="",
                    help="批准句里点名的四件身份前缀, 逗号分隔 plan=...,rows=...,executor=...,image=...;"
                         " 给了就必须**当场盘上复算相等**, 否则拒绝(批准与实际承接身份不许靠眼看对上)")
    a = ap.parse_args()
    if a.token and a.token_file:
        raise SystemExit("REFUSED|--token 与 --token-file 只能给一个(两处原话可能不同)")
    tok_raw_sha = tok_canon_sha = tok_source = ""
    if a.token_file:
        tf = pathlib.Path(a.token_file)
        if not tf.is_file():
            raise SystemExit("REFUSED|令牌原话文件不在盘上: %s" % tf)
        raw = tf.read_bytes()
        # 与执行器**同一口径**: 先按字节裁 CR/LF, 再严格 UTF-8 解码(F20 的漂移就出在换编码上)。
        canon = raw.rstrip(b"\r\n")
        try:
            a.token = canon.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SystemExit("REFUSED|令牌原话文件不是合法 UTF-8(%s): 不猜内容" % e)
        tok_raw_sha, tok_canon_sha = (hashlib.sha256(raw).hexdigest(),
                                      hashlib.sha256(canon).hexdigest())
        tok_source = "file:%s" % tf.as_posix()
        print("TOKEN_SOURCE|%s|原字节=%d B sha=%s|裁尾随 CR/LF 后=%d B sha=%s|严格 UTF-8 解码=成功"
              % (tf.as_posix(), len(raw), tok_raw_sha[:12], len(canon), tok_canon_sha[:12]))
    elif a.token:
        b = a.token.encode("utf-8")
        tok_raw_sha = tok_canon_sha = hashlib.sha256(b).hexdigest()
        tok_source = "cli"
        print("TOKEN_SOURCE|cli|bytes=%d|sha256=%s|警告: 命令行原话容易被 shell 转运改了字节"
              % (len(b), tok_canon_sha[:12]))
    repo = a.repo.resolve()
    src = repo / SRC_REL
    src_b = src.read_bytes()
    doc = json.loads(src_b.decode("utf-8"))
    out_path = repo / ROUND / "field" / a.out
    if out_path.exists():
        raise SystemExit("REFUSED|承接件已存在, 不覆盖已交付产物(要出新件请换文件名): %s" % out_path)
    approved = bool(a.token and a.approval_id)

    # 批准句里点名的四件身份必须与**当场盘上字节**对得上: 令牌批的是哪份字节, 承接件就得是哪份。
    # 对不上就拒绝(不生成批准版, 更不发任何现场命令), 因为那等于拿旧批准跑新包。
    if a.approved_prefixes:
        want = dict(kv.split("=", 1) for kv in a.approved_prefixes.split(",") if "=" in kv)
        img = (doc.get("identity_binding") or {}).get("image") or {}
        img_p = repo / img.get("path", "")
        got = {"plan": hashlib.sha256(src_b).hexdigest(),
               "rows": hashlib.sha256((src.parent / doc.get("rows_file", "")).read_bytes())
                       .hexdigest(),
               "executor": hashlib.sha256((repo / ROUND / "tools/m8r3_serial_chain_r7f8.py")
                                          .read_bytes()).hexdigest(),
               "image": hashlib.sha256(img_p.read_bytes()).hexdigest() if img_p.is_file() else ""}
        bad = ["%s: 批准点名=%r 盘上复算=%r" % (k, v, got.get(k, "<无此件>")[:len(v)])
               for k, v in sorted(want.items())
               if not str(got.get(k, "")).lower().startswith(str(v).strip().lower())]
        if not img_p.is_file():
            bad.append("image: 在板镜像参照文件不在盘上(%s) => 镜像身份无法当场复算" % img.get("path"))
        elif got["image"] != img.get("sha256"):
            bad.append("image: 计划登记=%s 盘上复算=%s" % (str(img.get("sha256"))[:12], got["image"][:12]))
        if bad or not approved:
            raise SystemExit("REFUSED|批准身份与盘上不一致或未给令牌(不发任何现场命令): %s"
                             % " ;; ".join(bad or ["给了 --approved-prefixes 但没有 --token"]))
        print("APPROVED_IDENTITIES_MATCH|" + " | ".join(
            "%s=%s…(%d B 复算相符)" % (k, want[k], p.stat().st_size)
            for k, p in (("plan", src),
                         ("rows", src.parent / doc.get("rows_file", "")),
                         ("executor", repo / ROUND / "tools/m8r3_serial_chain_r7f8.py"),
                         ("image", img_p)) if k in want))

    n_steps = len(doc["stages"][0]["steps"])
    quota = doc.get("quota") or doc["stages"][0].get("quota") or {}
    doc["status"] = ("FIELD_DISPATCH_ONE_SESSION_APPROVED_NOT_YET_RUN" if approved
                     else "FIELD_DISPATCH_DRAFT_NOT_APPROVED_NEW_PLAN_IDENTITY")
    mp = doc["mechanical_prerequisites"]["bench_continuity"]
    old_status = mp.get("status")
    mp["status"] = "PROVIDED" if approved else "NOT_REDECLINED_THIS_ROUND"
    if approved and not (a.prereq_declared_by and a.prereq_verbatim):
        raise SystemExit("REFUSED|批准件必须带本轮逐字声明的机械/电气前置"
                         "(--prereq-declared-by / --prereq-verbatim): 沿用上一轮的原话就是替设备编事实")
    mp["declared_by"] = (a.prereq_declared_by if approved else PREREQ_DRAFT_BY)
    mp["declared_verbatim"] = (a.prereq_verbatim if approved else PREREQ_DRAFT_VERBATIM)
    doc["approval"] = {
        "note": doc.get("approval", {}).get("note", ""),
        "approval_id": a.approval_id or "NOT_ISSUED",
        "token_verbatim": a.token or "NOT_ISSUED_等待操作者对新身份的真实原话",
        # 令牌的**字节身份**也进承接件: 开账(--approval-init --approval-token-file)与开口(dispatch)
        # 都按同一份裁尾随 CR/LF 后的字节算摘要, 三处一致才允许发命令。
        "token_source": tok_source or "NOT_ISSUED",
        "token_sha256": tok_canon_sha or "NOT_ISSUED",
        "token_file_raw_sha256": tok_raw_sha or "NOT_ISSUED",
        "ledger_root_offline": a.ledger_root,
        "dispatch_copy_note": (
            "本件由 tools/r7f8f_make_dispatch_copy.py 从已交付原件 %s/%s… 派生, 与原件的唯一差别"
            "就是下面这张白名单里的格子: 机械前置(本轮逐字声明或明确写「本轮未声明」)、status 改名、"
            "approval 里记令牌原话与其**字节摘要**和账本位置。%d 步/额度/三段预算/镜像身份/门位契约"
            "一字未动 => 计划身份哈希因此不同, 交付时两件并列对账。批准状态=%s。"
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
            "/approval/token_source", "/approval/token_sha256",
            "/approval/token_file_raw_sha256",
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
    print("WROTE|%s|bytes=%d|sha256=%s|approved=%s|diff_paths=%d|prereq_status=%s=>%s"
          % (out_path.relative_to(repo).as_posix(), len(b), hashlib.sha256(b).hexdigest(),
             approved, len(diff), old_status, mp["status"]))
    print("SRC|%s|bytes=%d|sha256=%s" % (SRC_REL, len(src_b), hashlib.sha256(src_b).hexdigest()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
