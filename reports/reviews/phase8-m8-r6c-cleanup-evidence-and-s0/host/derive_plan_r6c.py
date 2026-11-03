#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 **R6B 的离线派生件**再派生一份 R6C 离线模板(复审 M8-R6C §6.1 的最后一段)。

为什么是新路径而不是把 plan_core_r6b.json 的执行器 sha 就地改掉:
  * R6B 那份已经作为本轮交付证据的执行输入被远端逐 blob 回读核过身份(28 项里就有它的登记),
    在原路径悄悄换身份 = 让证据指向一份已经不是那个字节的件。旧件一个字节不动。
  * R6C 改了执行器两处(清理发送前的记账隔离、S0 用实际执行快照的身份) => 登记里的 executor
    sha 必须跟着变, 否则 S0 当场算出的实际哈希与计划登记值不符。
  * 本轮**不重编固件、不改产品 C**(任务书 §6.1): 5ae4110d… 仍是 R6A 那次构建的候选件,
    继续 NOT_BOUND_IN_THIS_PACKAGE; image_binding 仍指向已在板逐字节证过的那件。

纪律: 源件冻结校验(sha 不符就拒绝派生) / --check 幂等 / 不带 --force 绝不覆写 /
      hardware_actions_authorized_by_this_package=0, 不创建任何可用于现场的批准账。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                                   # 仓库根 D:/ns800
ROUND = HERE.parent                                      # .../phase8-m8-r6c-cleanup-evidence-and-s0
FIELD = ROUND / "field"
PREV = ROUND.parent / "phase8-m8-r6b-executor-binding"
SRC = PREV / "field" / "plan_core_r6b.json"              # R6B 的离线派生件(从未执行过设备)
OUT = FIELD / "plan_core_r6c.json"
EXEC = (ROUND.parent / "phase8-m8-relative-protection-r3" / "field" / "m8r3_serial_chain.py")
OLD_TOKEN = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"
# R6B 交付时登记的那份派生件字节(180cce8/6435328 里都是这个值); 源被改过就拒绝派生
FROZEN_SRC_SHA = "08fd5d522d85f05a1eb8d0c51f339ccd05c18761b85990d6454022d6686069bb"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build():
    if sha(SRC) != FROZEN_SRC_SHA:
        raise SystemExit("R6C_DERIVE_REFUSE|%s 的字节不是 R6B 交付那份 %s…, 拒绝从被改过的源派生"
                         % (SRC.name, FROZEN_SRC_SHA[:12]))
    plan = json.loads(SRC.read_text(encoding="utf-8-sig"))
    ex_sha, ex_bytes = sha(EXEC), EXEC.stat().st_size
    prev_ex = plan["identity_binding"]["executor"]
    plan["executor_reused"] = ("%s(sha256 %s…, 逐字节内容见 identity_binding.executor)"
                               % (EXEC.as_posix(), ex_sha[:12]))
    plan["identity_binding"]["executor"] = {
        "path": EXEC.as_posix(), "bytes": ex_bytes, "sha256": ex_sha,
        "changes_in_this_round": [
            "C1 清理发送前的记账隔离: 新增 _ev_or_defer()/_flush_deferred_ev() —— 披露事件"
            "(CLEANUP_RESERVE_REFUSED_SENT_ANYWAY)与 DISARM_FIELDS 的**日志写入**不再是发送的前提; "
            "写失败时事件原样收进内存清单, 本条与后续必要撤能照发, 清理末尾再尽力补记, "
            "成/败都落成独立具名内存错误(WRITTEN_AFTER_RETRY / WRITE_FAILED) => 本场仍不成功。"
            "上一版这里直接 led.ev(...), 一次 OSError 就让 cleanup 退出, 三条撤能一条没发",
            "C2 S0 身份贯通: s0_binding(..., snapshot=) 用**实际执行的那份快照字节**记 plan_sha256, "
            "不再另读一次磁盘哈希; 磁盘现值另记为具名字段 plan_sha256_on_disk(观察值, 不覆盖执行身份), "
            "两者在执行任何动作之前不一致 => S0_PLAN_DISK_DRIFT 具名拒绝。上一版记的是磁盘那一刻的值: "
            "'读出 P→核对 P→执行 P' 之后把文件换成 Q, S0 就把 Q 记成本次执行身份",
            "保持不变(复审 §4.3/§5 第4条): 坏账一律具名拒绝且不被修复/清零、不退款、不自动迁移、"
            "不发新额度; 额度不足照撤能; commit 错不跳过端口与日志关闭; 真实单会话只接经批准的整包; "
            "命令->额度键集中映射在发送前拒; 只读轮询不占动作额度; 原始字节危险门顺序不动"],
        "previous_registration_sha_location": (
            "R6B 交付件登记的执行器 sha=%s…/%d B 留在 plan_core_r6b.json 本体里(冻结在该文件, "
            "不回改); 本轮只改这份新派生件的登记值" % (prev_ex.get("sha256", "")[:12],
                                                     prev_ex.get("bytes", -1)))}
    plan["r6c_image_note"] = {
        "rebuilt_this_round": False,
        "why": ("任务书 §6.1: 只修 C1/C2 及直接相关测试, **产品 C、参数、算法、阈值、引脚、"
                "恢复资格和固件归档都不动; keil_builds=0** —— 所以没有新构建件"),
        "candidate_still": ("5ae4110d…/292164 B(R6A 的 r6a_candidate_image, bind_state="
                            "NOT_BOUND_IN_THIS_PACKAGE)仍未上板"),
        "on_board_image_kept": ("image_binding 仍指向 dab356c6…/290916 B —— 最后已证在板的那件; "
                                "换批准 ID/重开账本都不会把它变成新固件现场包")}
    plan["approval"] = {
        "status": "REQUIRES_NEW_APPROVAL",
        "prior_token": OLD_TOKEN,
        "prior_token_state": ("EXHAUSTED_AND_OVERRUN: 该批准下的 26 场把 tmc_config_apply 用到 14/2、"
                              "irq_polarity_prep 18/4、port_open 32/12、diag_profile_zero 6/1, "
                              "learn 与两次起转额度已用尽; 换计划文件/换输出目录都不重新发额度"),
        "hardware_actions_authorized_by_this_package": 0,
        "why": ("R6C 是离线补漏轮(复审 §6): 全部 0 设备动作。旧批准不结转, 也不因为换了执行器"
                "就开立可用的新现场批准; 后续现场包本轮不制定(§6.2 末段)")}
    plan["derived_from"] = {"path": SRC.relative_to(REPO).as_posix(),
                            "sha256": sha(SRC), "bytes": SRC.stat().st_size,
                            "note": ("R6B 的离线派生件保持冻结(它是 R6C 任务书 §3 里 A 复算过身份的那份); "
                                     "R6A/R6/R5-C 派生件与其 26 场原件/台账同样冻结")}
    plan["changes_this_round_r6c"] = [
        "① C1: 清理路径上**发送之前**的记账/披露写入逐条隔离 —— 日志写不成也不再吃掉必要撤能; "
        "判据断言内存端口的实际 write 列表(日志坏了 TX 事件会一起消失, 数事件分不清'没落证'与'没发送')",
        "② C2: S0 登记的 plan 身份 = 实际执行的那份快照字节; 磁盘现值另记 plan_sha256_on_disk, "
        "执行前不一致就 S0_PLAN_DISK_DRIFT 拒(不留'记 Q 执行 P'的假身份)",
        "③ 新增 6 条针对用例 FS122–FS127, 其中 FS124/FS127 是元负控: 分别把记账隔离与 S0 快照贯通"
        "摘回旧形状, 必须**重新**出现'只发 motor_start'与'记 Q 执行 P 且下载照跑'(复审 §7.2.5 口径)",
        "④ 没有产品 C 改动、没有重编、没有新镜像: 三件镜像身份原样保留, 本包只登记不绑定",
        "⑤ 阈值/ACTIVE 覆盖/REL_IMPACT/load_means(仍 NOT_PROVIDED)/idle1024 带载最坏栈余量/"
        "现场精确线程交错与 0x00bd80b0 写入者的旧裁定全部保持; 四项装配前提仍 PROVIDED, 未重问"]
    plan["package_status"] = "R6C_DERIVED_NOT_EXECUTED"
    return plan


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对, 不写盘(幂等证明)")
    ap.add_argument("--force", action="store_true",
                    help="显式覆写已存在的派生件(仅限本包从未执行过设备的情形; 缺省一律拒绝)")
    a = ap.parse_args()
    body = dump(build())
    want = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if a.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8-sig") == body
        print("R6C_CHECK|%s|bytes=%d|sha=%s|源仍冻结=%s" %
              ("IDEMPOTENT" if same else "DIFFERS", len(body.encode("utf-8")), want[:16],
               sha(SRC) == FROZEN_SRC_SHA))
        return 0 if same else 1
    if OUT.exists() and sha(OUT) != want and not a.force:
        print("R6C_REFUSE_WRITE|%s 已存在且字节不同(sha %s), 不静默覆盖; 需要时显式 --force"
              % (OUT.name, sha(OUT)[:12]))
        return 1
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("R6C_WRITE|%s|bytes=%d|sha=%s" % (OUT.as_posix(), len(body.encode("utf-8")), want[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
