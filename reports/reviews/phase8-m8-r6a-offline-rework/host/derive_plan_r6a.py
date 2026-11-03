#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 **R6 的离线派生件**再派生一份 R6A 离线候选计划(复审 M8-R6A §7 的最后一段)。

为什么不是"改一下 plan_core_r6.json 的批准 ID"就完事:
  * R6 那份已经当作**离线 CLI 证据的执行输入**用过(FS 里那条 rc=1 的越权拒绝就是它跑的);
    在原路径悄悄换身份 = 让证据指向一份已经不是那二进制的计划。所以这里**写新文件、新路径**,
    旧件一个字节不动。
  * 执行器本轮改了 R1/R2/R3 三处 => 登记里的 executor sha 必须跟着变, 否则 S0 当场算出的
    实际哈希与计划登记值不符(那正是 §6 指出的"登记≠实际")。
  * 新镜像(5ae4110d…)只登记身份, `bind_state=NOT_BOUND_IN_THIS_PACKAGE`: 本包**没有**任何
    下载授权, image_binding 继续指向已在板逐字节证过的那件。要烧新件必须由下一个现场包
    重绑 bin/axf/回读长度/下载阶段并按新代码另批。

纪律: 源件冻结校验(sha 不符就拒绝派生) / --check 幂等 / 不带 --force 绝不覆写。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                                   # 仓库根 D:/ns800
ROUND = HERE.parent                                      # .../phase8-m8-r6a-offline-rework
FIELD = ROUND / "field"
PREV = ROUND.parent / "phase8-m8-r6-offline-recovery"
SRC = PREV / "field" / "plan_core_r6.json"               # R6 的离线派生件(从未执行过设备)
OUT = FIELD / "plan_core_r6a.json"
EXEC = (ROUND.parent / "phase8-m8-relative-protection-r3" / "field" / "m8r3_serial_chain.py")
CANDIDATE_FW = ROUND.parent.parent / "firmware/phase8-m8-r6a-offline-rework"
CANDIDATE_BIN = CANDIDATE_FW / "rt-thread-motor-m1-candidate.bin"
CANDIDATE_AXF = CANDIDATE_FW / "rt-thread-motor-m1.axf"
CANDIDATE_MAP = CANDIDATE_FW / "rt-thread-motor-m1.map"
CANDIDATE_MANIFEST = CANDIDATE_FW / "build_manifest.json"
R6_FW = ROUND.parent.parent / "firmware/phase8-m8-r6-offline-recovery"
OLD_TOKEN = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"
# R6 交付时登记的那份派生件字节(44e493c 里就是这个值); 源被改过就拒绝派生
FROZEN_SRC_SHA = "6e0b97f251a61493a10d5d4fe3b3238b84e71214e21c77a40b332c0136785750"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build():
    if sha(SRC) != FROZEN_SRC_SHA:
        raise SystemExit("R6A_DERIVE_REFUSE|%s 的字节不是 R6 交付那份 %s…, 拒绝从被改过的源派生"
                         % (SRC.name, FROZEN_SRC_SHA[:12]))
    plan = json.loads(SRC.read_text(encoding="utf-8-sig"))
    ex_sha, ex_bytes = sha(EXEC), EXEC.stat().st_size
    plan["executor_reused"] = ("%s(sha256 %s…, 逐字节内容见 identity_binding.executor)"
                               % (EXEC.as_posix(), ex_sha[:12]))
    plan["identity_binding"]["executor"] = {
        "path": EXEC.as_posix(), "bytes": ex_bytes, "sha256": ex_sha,
        "changes_in_this_round": [
            "R1 账本: 创建与读取分离(_load 取代 _load_or_create) => 运行期**每一次**加锁读盘都严格, "
            "缺字段/空值/错类型/负数/bool/字符串数字/caps 键集合不符/账本文件消失全部具名拒绝, "
            "不补零不迁移; init 预写 spent=0 且要求键集合==caps; reserve 的 n 必须正整数; "
            "commit 不再忽略存盘失败; approval_init 走同一把锁 + O_CREAT|O_EXCL(重复 init 不覆盖)",
            "R2 绑定: transport==real 的 exec_session 只接受 dispatch 当场构造的 approved 包, "
            "会话在开口前核两件事 —— 计划文件字节仍等于批准登记的 plan_sha256, 且本会话步骤等于"
            "从那份字节里同一阶段重推导出的步骤; CLI 单会话 --steps 在真实传输下 rc=4 拒接",
            "R2 额度: QUOTA_COMMAND_KEYS 把 apply/polarity/zero/learn/commit/config_save/"
            "motor_start/blackbox_clear 钉到额度键, 缺申报=QUOTA_STEP_UNDECLARED、"
            "错键=QUOTA_KEY_MISMATCH、n 不符=QUOTA_N_MISMATCH, 全部在那条 write 之前; "
            "只读查询与有限轮询不进这张表(不把轮询计成 apply 重试); 离线 PLAN_QUOTA_MAP 同判据",
            "R3 字节门: guard_stream_bytes/guard_partial_bytes 成为唯一入口, 普通响应/清理/观察窗/"
            "发送前排空四处复用; 危险文本 -> 裸 NUL -> 非法 UTF-8 顺序固定且先于长度帽; "
            "cleanup 的 motor_status 损坏 => 记独立证据错误 + 禁能 UNKNOWN + 整场 success=False, "
            "但必要撤能照发; 观察窗的字节损坏不再被吞成正常 NO_TRIGGER; pre 残留先存证再拒",
            "版本门: ApprovalLedger.VERSION 1 -> 2, 上一轮那份账由 APPROVAL_LEDGER_VERSION 具名"
            "拒绝(不自动迁移成未消费状态)"],
        "previous_registration_sha_location": (
            "R6 交付件登记的执行器 sha 留在 plan_core_r6.json 本体里(冻结在该文件, 不回改); "
            "本轮只改这份新派生件的登记值")}
    cand = {"bind_state": "NOT_BOUND_IN_THIS_PACKAGE",
            "why": ("本包是 R6A 离线派生件: approval.status=REQUIRES_NEW_APPROVAL 且 "
                    "hardware_actions_authorized_by_this_package=0 => 没有任何一次下载/烧录/回读/复位"
                    "授权。所以 image_binding 继续指向**已在板逐字节证过**的那件; R6A 新候选镜像只"
                    "在这里登记身份, 下一个现场包若要烧它必须自己重绑(bin/axf/回读长度/下载阶段)并"
                    "按新代码另批")}
    for p, k in ((CANDIDATE_BIN, "bin"), (CANDIDATE_AXF, "axf"), (CANDIDATE_MAP, "map")):
        if p.is_file():
            cand["%s_path" % k] = p.relative_to(REPO).as_posix()
            cand["%s_bytes" % k] = p.stat().st_size
            cand["%s_sha256" % k] = sha(p)
    cand["built_from"] = ("R6A-R4 单目标 Rebuild All(0 Error/0 Warning, Code=205594 RO=85994 "
                          "RW=2188 ZI=85592), 逐字段见 "
                          + CANDIDATE_MANIFEST.relative_to(REPO).as_posix())
    cand["manifest_sha256"] = sha(CANDIDATE_MANIFEST) if CANDIDATE_MANIFEST.is_file() else None
    cand["supersedes"] = {"r6_candidate_bin_sha256": sha(R6_FW / "rt-thread-motor-m1-candidate.bin"),
                          "statement": ("R6 那件 d70a9180… 的归档文件与身份原样保留(本包只新增, "
                                        "不删不改); 两件都**没有**上板")}
    plan["r6a_candidate_image"] = cand
    plan["approval"] = {
        "status": "REQUIRES_NEW_APPROVAL",
        "prior_token": OLD_TOKEN,
        "prior_token_state": ("EXHAUSTED_AND_OVERRUN: 该批准下的 26 场把 tmc_config_apply 用到 14/2、"
                              "irq_polarity_prep 18/4、port_open 32/12、diag_profile_zero 6/1, "
                              "learn 与两次起转额度已用尽; 换计划文件/换输出目录都不重新发额度"),
        "hardware_actions_authorized_by_this_package": 0,
        "why": ("R6A 是离线返修轮(复审 §7): 修的是执行控制与输出四项, 全部 0 设备动作。"
                "旧批准不结转, 也**不**因为修了账本就创建一个可用的新现场批准")}
    plan["derived_from"] = {"path": SRC.relative_to(REPO).as_posix(),
                            "sha256": sha(SRC), "bytes": SRC.stat().st_size,
                            "note": ("R6 的离线派生件保持冻结(它是本轮之前那批 CLI 证据的执行输入); "
                                     "R5-C 原计划与其 26 场原件/台账同样冻结")}
    plan["changes_this_round_r6a"] = [
        "① R1: 账本运行期只严格读取已存在账(创建只走 --approval-init); 复审 §3 表的四个反例逐一被拒",
        "② R2: 真实传输只接经批准的整包 + 步骤/计划字节双重核对 + 命令->额度键集中映射(缺/错/n 不符"
        "在发送前拒)",
        "③ R3: 原始字节危险门集中复用到清理/观察窗/排空残留; 损坏的清理回包不再能证成禁能",
        "④ R4(产品侧, 不在本包内): motor.c mot_commit_gated 的 withheld 打印拆成 4 次调用, "
        "本轮新增/改动站点全部纳入 120 B 检查(host/line_width_check.py 的 CHANGED_SITES)",
        "⑤ 新镜像 5ae4110d… 只登记身份(r6a_candidate_image, bind_state=NOT_BOUND_IN_THIS_PACKAGE), "
        "本包不烧写、不下载; 要动它必须由下一个现场包重绑并另批",
        "⑥ 阈值/ACTIVE 覆盖/REL_IMPACT/load_means/idle1024 带载最坏栈余量/现场精确交错与 0x00bd80b0 "
        "写入者的旧裁定全部保持, 本轮没有动任何判据或算法"]
    plan["package_status"] = "R6A_DERIVED_NOT_EXECUTED"
    return plan


def dump(doc):
    return json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对, 不写盘(幂等证明)")
    ap.add_argument("--force", action="store_true",
                    help="显式覆写已存在的派生件(仅限本包从未执行过的情形; 缺省一律拒绝)")
    a = ap.parse_args()
    body = dump(build())
    want = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if a.check:
        same = OUT.exists() and OUT.read_text(encoding="utf-8-sig") == body
        print("R6A_CHECK|%s|bytes=%d|sha=%s|源仍冻结=%s" %
              ("IDEMPOTENT" if same else "DIFFERS", len(body.encode("utf-8")), want[:16],
               sha(SRC) == FROZEN_SRC_SHA))
        return 0 if same else 1
    if OUT.exists() and sha(OUT) != want and not a.force:
        print("R6A_REFUSE_WRITE|%s 已存在且字节不同(sha %s), 不静默覆盖; 需要时显式 --force"
              % (OUT.name, sha(OUT)[:12]))
        return 1
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("R6A_WRITE|%s|bytes=%d|sha=%s" % (OUT.as_posix(), len(body.encode("utf-8")), want[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
