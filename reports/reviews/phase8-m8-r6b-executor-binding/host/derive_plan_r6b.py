#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 **R6A 的离线派生件**再派生一份 R6B 离线模板(复审 M8-R6B §7.1 的最后一段)。

为什么是新文件而不是把 plan_core_r6a.json 的执行器 sha 就地改掉:
  * R6A 那份已经作为**本轮交付证据的执行输入**被核过(远端逐 blob 回读 44 项里就有它的登记),
    在原路径悄悄换身份 = 让证据指向一份已经不是那二进制件。旧件一个字节不动。
  * R6B 改了执行器两处(账本类型/异常隔离、整包同源绑定) => 登记里的 executor sha 必须跟着变,
    否则 S0 当场算出的实际哈希与计划登记值不符。
  * 本轮**不重编固件**(任务书 §7.1): 5ae4110d… 仍是 R6A 那次构建的候选件, 继续
    `NOT_BOUND_IN_THIS_PACKAGE`; image_binding 仍指向已在板逐字节证过的那件。

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
ROUND = HERE.parent                                      # .../phase8-m8-r6b-executor-binding
FIELD = ROUND / "field"
PREV = ROUND.parent / "phase8-m8-r6a-offline-rework"
SRC = PREV / "field" / "plan_core_r6a.json"              # R6A 的离线派生件(从未执行过设备)
OUT = FIELD / "plan_core_r6b.json"
EXEC = (ROUND.parent / "phase8-m8-relative-protection-r3" / "field" / "m8r3_serial_chain.py")
FW = ROUND.parent.parent / "firmware/phase8-m8-r6a-offline-rework"
OLD_TOKEN = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"
# R6A 交付时登记的那份派生件字节(b78d9e1/aab19ff 里都是这个值); 源被改过就拒绝派生
FROZEN_SRC_SHA = "556a044e195705d1c27949c1393f60c23ac1e1af01cfa555581350ee69d732c2"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build():
    if sha(SRC) != FROZEN_SRC_SHA:
        raise SystemExit("R6B_DERIVE_REFUSE|%s 的字节不是 R6A 交付那份 %s…, 拒绝从被改过的源派生"
                         % (SRC.name, FROZEN_SRC_SHA[:12]))
    plan = json.loads(SRC.read_text(encoding="utf-8-sig"))
    ex_sha, ex_bytes = sha(EXEC), EXEC.stat().st_size
    prev_ex = plan["identity_binding"]["executor"]
    plan["executor_reused"] = ("%s(sha256 %s…, 逐字节内容见 identity_binding.executor)"
                               % (EXEC.as_posix(), ex_sha[:12]))
    plan["identity_binding"]["executor"] = {
        "path": EXEC.as_posix(), "bytes": ex_bytes, "sha256": ex_sha,
        "changes_in_this_round": [
            "B1 类型门: validate_ledger_doc 在**成员查找/下标这类类型敏感操作之前**先验字段类型 "
            "(pending[i].name 必须是非空 str), 合法 JSON 里的 `name: []` 由 APPROVAL_LEDGER_CORRUPT "
            "具名拒绝; 上一版先做 `p[\"name\"] not in have_caps`, 不可哈希值直接抛 "
            "TypeError: unhashable type => 普通异常跳出清理框架",
            "B1 清理隔离: cleanup() 里每条必要撤能的账本预占除 Stop 外**也捕普通异常**, "
            "落成 APPROVAL_RESERVE_FRAME_ERROR_<类型>_SENT_ANYWAY 具名披露后继续发送, "
            "后续两条必要撤能不再被吃掉(撤能优先于额度, 且不是忽略异常报成功)",
            "B1 收尾隔离: exec_session 的 finally 里, 确认消耗(commit)按条捕 Stop/普通异常, "
            "整段账本收尾包在 try/except/finally 内 => 任何账本错都不再跳过 port.close()/led.close(); "
            "关端口只算交还句柄, 禁能仍只由本场新的完整 status 证明(证据不足一律 UNKNOWN)",
            "B2 同源快照: 新增 read_plan_snapshot() —— 计划**一次读盘**, plan 的 sha256、JSON 解析"
            "与执行对象都出自同一份原始字节; current_identities(plan, path, repo, snapshot=) 用快照"
            "那份字节的哈希, 不再另读一次文件; 传入对象与快照不一致 => 具名拒绝, 不用快照哈希给"
            "别的对象作证",
            "B2 整包绑定: dispatch_plan 在 real 传输下先取快照再动任何东西 —— 内存对象≠快照 => "
            "PLAN_NOT_FROM_APPROVED_SNAPSHOT 在 UID/工具/端口/额度之前拒; 通过核对后 stages/tools/"
            "argv/quota/时间窗**只**从这份已核快照推导(旧版只把 plan_path 交给另一次读取算哈希, "
            "于是被核的和被跑的可以不是同一件)",
            "保持不变: 真实单会话仍只接经批准的整包(CLI --steps 真实路径 rc=4)、命令->额度键集中映射"
            "(缺申报/错键/n 不符都在发送前拒)、只读轮询不占动作额度、原始字节危险门(危险文本 -> "
            "裸 NUL -> 非法 UTF-8, 先于长度帽)、账本运行期不建账与 VERSION=2 版本门"],
        "previous_registration_sha_location": (
            "R6A 交付件登记的执行器 sha=%s…/%d B 留在 plan_core_r6a.json 本体里(冻结在该文件, "
            "不回改); 本轮只改这份新派生件的登记值" % (prev_ex.get("sha256", "")[:12],
                                                    prev_ex.get("bytes", -1)))}
    plan["r6b_image_note"] = {
        "rebuilt_this_round": False,
        "why": ("任务书 §7.1: 本轮集中修执行器两处, **不需要修改产品 C、算法或固件, 不要求再编译**; "
                "所以不产生新的构建件, R6A 那次单目标构建的三件产物身份原样保留"),
        "candidate_still": ("5ae4110d…/292164 B(R6A 的 r6a_candidate_image, bind_state="
                            "NOT_BOUND_IN_THIS_PACKAGE)"),
        "on_board_image_kept": ("image_binding 仍指向 dab356c6…/290916 B —— 已在板逐字节证过的那件; "
                               "换批准 ID/重开账本都不会把它变成新固件现场包")}
    plan["approval"] = {
        "status": "REQUIRES_NEW_APPROVAL",
        "prior_token": OLD_TOKEN,
        "prior_token_state": ("EXHAUSTED_AND_OVERRUN: 该批准下的 26 场把 tmc_config_apply 用到 14/2、"
                              "irq_polarity_prep 18/4、port_open 32/12、diag_profile_zero 6/1, "
                              "learn 与两次起转额度已用尽; 换计划文件/换输出目录都不重新发额度"),
        "hardware_actions_authorized_by_this_package": 0,
        "why": ("R6B 是离线返修轮(复审 §7): 只修执行控制的两处, 全部 0 设备动作。旧批准不结转, "
                "也**不**因为换了执行器就创建一个可用的新现场批准; 后续现场包暂不制定")}
    plan["derived_from"] = {"path": SRC.relative_to(REPO).as_posix(),
                            "sha256": sha(SRC), "bytes": SRC.stat().st_size,
                            "note": ("R6A 的离线派生件保持冻结(它是 R6A 交付证据里远端逐 blob 回读"
                                     "核过身份的那份); R6/R5-C 原计划与其 26 场原件/台账同样冻结")}
    plan["changes_this_round_r6b"] = [
        "① B1: 账本类型损坏一律具名拒绝(不再漏成普通 Python 异常), 必要撤能三条都发、端口与日志都关; "
        "坏账不被修复/清零, 禁能证据不足仍 UNKNOWN(复审 §4 四条合同)",
        "② B2: 真实整包的执行对象与身份哈希来自**同一份原始字节快照**; 内存对象与批准文件不同、"
        "阶段被砍、工具 argv 被换、CLI 读取与哈希之间换文件, 都在 UID/工具/端口/额度之前具名拒绝",
        "③ 没有产品 C 改动、没有重编固件、没有新镜像: 5ae4110d… 与 d70a9180… 与 dab356c6… 三件身份"
        "原样保留, 本包只登记不绑定",
        "④ 新增 7 条针对用例 FS115–FS121(含 1 条元负控 FS120: 摘掉快照门后未批准 argv 必须重新到达"
        "后端), 其余受影响项按任务书 §7.2.4 只复跑相关段; 121 条全部真实执行, 0 硬件动作",
        "⑤ 阈值/ACTIVE 覆盖/REL_IMPACT/load_means(仍 NOT_PROVIDED)/idle1024 带载最坏栈余量/"
        "现场精确线程交错与 0x00bd80b0 写入者的旧裁定全部保持; 四项装配前提仍 PROVIDED, 未重问"]
    plan["package_status"] = "R6B_DERIVED_NOT_EXECUTED"
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
        print("R6B_CHECK|%s|bytes=%d|sha=%s|源仍冻结=%s" %
              ("IDEMPOTENT" if same else "DIFFERS", len(body.encode("utf-8")), want[:16],
               sha(SRC) == FROZEN_SRC_SHA))
        return 0 if same else 1
    if OUT.exists() and sha(OUT) != want and not a.force:
        print("R6B_REFUSE_WRITE|%s 已存在且字节不同(sha %s), 不静默覆盖; 需要时显式 --force"
              % (OUT.name, sha(OUT)[:12]))
        return 1
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("R6B_WRITE|%s|bytes=%d|sha=%s" % (OUT.as_posix(), len(body.encode("utf-8")), want[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
