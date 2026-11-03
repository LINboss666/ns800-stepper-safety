#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从**已执行且冻结**的 plan_core_r5c.json 派生 R6 离线候选计划(不覆写历史件)。

复审 M8-R5C §5.3/§7.3 的裁定: "字节损坏后由后面某步重确认"这条合同要退役 ——
它当时只核对了"存在更晚的步骤且它有 expect_values", 没有证明那些字段与受损动作逐项等价。
所以本版执行器改成"裸 NUL / 非法 UTF-8 / 危险文本一律即停", 计划里也就不再申报例外。

纪律(与 R5-C 同一套, 逐条可判):
  * 历史件只读: `field/plan_core_r5c.json` 与所有 `field-*/` 原件一个字节都不改; 本工具**拒绝**写它们。
  * 身份当场算: 执行器/镜像/上一版计划的 sha256 全部从磁盘字节计算, 不照抄 JSON 里登记的值。
  * 不发放新额度: 派生出来的包 approval.status = REQUIRES_NEW_APPROVAL, 旧批准令牌 4cb9dac 登记为
    已用尽且已超限; 本工具与执行器都不因"换了文件名/换了输出目录"而重新给预算。
  * 幂等: 同一输入两次派生必须同字节(--check 验证; 顺序敏感处一律用固定顺序, 不用 set)。

用法: python derive_plan_r6.py            # 写出 field/plan_core_r6.json
      python derive_plan_r6.py --check    # 只比对(已存在则要求逐字节相同), 不写盘
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                                   # 仓库根 D:/ns800
ROUND = HERE.parent
FIELD = ROUND / "field"
# 源 = 上一个轮次里**已执行并冻结**的那份计划(只读, 路径也如实写进派生件)
PREV = ROUND.parent / "phase8-m8-idle-stack-r4"
SRC = PREV / "field" / "plan_core_r5c.json"
OUT = FIELD / "plan_core_r6.json"
EXEC = (ROUND.parent / "phase8-m8-relative-protection-r3" / "field" / "m8r3_serial_chain.py")
# 本轮 R6-7 单目标构建出来的候选镜像(只登记身份, **不**绑进 image_binding: 见 r6_candidate_image)
CANDIDATE_FW = ROUND.parent.parent / "firmware/phase8-m8-r6-offline-recovery"
CANDIDATE_BIN = CANDIDATE_FW / "rt-thread-motor-m1-candidate.bin"
CANDIDATE_AXF = CANDIDATE_FW / "rt-thread-motor-m1.axf"
CANDIDATE_MANIFEST = CANDIDATE_FW / "build_manifest.json"
OLD_TOKEN = "4cb9dac0f2a47beb494a4fb41090cefe8da76075"
# 现场 26 场真正用过的那份计划(§15.7 已冻结; 本脚本绝不清改)
FROZEN_SRC_SHA = "05bcf96aa7a6bf89291dab9b140253dde74982f7c3907a49a51bb2972d3a3746"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build():
    if sha(SRC) != FROZEN_SRC_SHA:
        raise SystemExit("R6_DERIVE_REFUSE|%s 的字节不是已执行那份 %s…, 拒绝从被改过的源派生"
                         % (SRC.name, FROZEN_SRC_SHA[:12]))
    plan = json.loads(SRC.read_text(encoding="utf-8-sig"))
    removed = []
    for stg in plan["stages"]:
        for st in stg.get("steps", []):
            rec = st.pop("byte_corruption_recovery", None)
            if rec:
                removed.append("%s/%s->%s" % (stg["id"], st.get("as"), rec.get("reconfirmed_by")))
            st.pop("no_facts_from_corrupted_response", None)
    ex_sha, ex_bytes = sha(EXEC), EXEC.stat().st_size
    plan["executor_reused"] = ("%s(sha256 %s…, 逐字节内容见 identity_binding.executor)"
                               % (EXEC.as_posix(), ex_sha[:12]))
    plan["identity_binding"]["executor"] = {
        "path": EXEC.as_posix(), "bytes": ex_bytes, "sha256": ex_sha,
        "changes_in_this_round": [
            "read_until_prompt(): 原始字节先落盘 -> check_danger(未解码字节) -> 裸 NUL 计数 => "
            "NUL_BYTE_IN_RESPONSE 硬停止; 危险文本优先级高于解码/长度/协议判定",
            "byte_corruption_recovery 合同退役: 申报即 RETIRED_RECOVERY_CONTRACT 停止; "
            "exec_session 不再产生 BYTE_CORRUPT 判决",
            "validate_plan(): PLAN_BYTE_RECOVERY 改为\"申报即判红\"(不再检查其形状)",
            "ApprovalLedger + open_approval + --approval-id/--approval-root/--approval-init: "
            "额度**先落盘预占再动作**, 账本按批准身份命名且放在输出目录之外; 缺失/损坏/身份不符 => "
            "真实动作拒绝进入且不自建满额账; 会话结束只确认消耗, 没有退款这条路",
            "dispatch_plan(): 阶段级工具(download/readback/reset/reboot/人工重启门)同样先预占 "
            "(R5-C 的 26 场就是从这条只扣内存账的路把 apply 用到 14/2)",
            "s0_binding(): S0 当场算 plan/executor/image 三件的实际 sha256 并连批准身份一起记, "
            "不再照抄 JSON 里登记的值"]}
    candidate = {"bind_state": "NOT_BOUND_IN_THIS_PACKAGE",
                 "why": ("本包是离线派生件, approval.status=REQUIRES_NEW_APPROVAL 且 "
                         "hardware_actions_authorized_by_this_package=0 => 没有任何一次下载授权, "
                         "所以 image_binding 继续指向**已在板逐字节证过**的那件; 新候选镜像只在这里"
                         "登记身份, 下一个现场包若要烧它必须自己重绑并按新代码另批")}
    if CANDIDATE_BIN.is_file() and CANDIDATE_AXF.is_file():
        candidate.update({
            "bin_path": CANDIDATE_BIN.relative_to(REPO).as_posix(),
            "bin_bytes": CANDIDATE_BIN.stat().st_size, "bin_sha256": sha(CANDIDATE_BIN),
            "axf_path": CANDIDATE_AXF.relative_to(REPO).as_posix(),
            "axf_bytes": CANDIDATE_AXF.stat().st_size, "axf_sha256": sha(CANDIDATE_AXF),
            "built_from": ("本轮 R6-7 单目标 Rebuild All(0 Error/0 Warning), 逐字段见 "
                           + CANDIDATE_MANIFEST.relative_to(REPO).as_posix()),
            "manifest_sha256": (sha(CANDIDATE_MANIFEST) if CANDIDATE_MANIFEST.is_file() else None)})
    else:
        candidate["built"] = "NO_CANDIDATE_ON_DISK_AT_DERIVATION_TIME"
    plan["r6_candidate_image"] = candidate
    plan["approval"] = {
        "status": "REQUIRES_NEW_APPROVAL",
        "prior_token": OLD_TOKEN,
        "prior_token_state": ("EXHAUSTED_AND_OVERRUN: 该批准下的 26 场把 tmc_config_apply 用到 14/2、"
                              "irq_polarity_prep 18/4、port_open 32/12、diag_profile_zero 6/1, "
                              "learn 与两次起转额度已用尽; 换计划文件/换输出目录都不重新发额度"),
        "hardware_actions_authorized_by_this_package": 0,
        "why": ("本轮(R6)是离线修复轮: 产品状态收口与输出完整性已改码, 需要复审按**新代码 + 实际工况 + "
                "操作者设备状态**另给一次具体现场范围后才能执行")}
    plan["changes_this_round_r6"] = [
        "① 取消 cfgapply/cfgapply2 两处 byte_corruption_recovery(%d 处): %s => 恢复\"字节损坏即停\""
        % (len(removed), ",".join(removed) or "无"),
        "② 身份: 绑定 R6 执行器 %s/%d B; 已执行的 R5-C 计划(%s…)与其 26 场原件保持冻结不动"
        % (ex_sha[:12], ex_bytes, FROZEN_SRC_SHA[:12]),
        "③ 批准口径: approval.status=REQUIRES_NEW_APPROVAL, 本包不携带任何设备动作授权",
        "④ 依据的产品改动(不在本包内, 只记口径): motor.c mot_commit_gated 成功关闭同笔提交 "
        "Safety RUN->READY; tmc2209.c CONFIGURED 与 safety_thread.c LIMIT 长打印拆到每次调用 ≤120 B",
        "⑤ 额度控制(R6-6, 复审 §7.4 要的那件缺失件): 新增按**批准身份**命名且放在输出目录之外的"
        "持久预占账(先落盘再动作, 崩溃不退款, 双执行者共一本不超 cap, 缺失/损坏/身份不符 => 拒绝进入"
        "真实动作且不自建满额账); 撤能预留预约失败仍照发(安全动作优先于额度)并落成具名披露",
        "⑥ R6-7 单目标构建产物只登记身份(r6_candidate_image, bind_state=NOT_BOUND_IN_THIS_PACKAGE), "
        "本包不烧写、不下载; 要动它必须由下一个现场包重绑并另批"]
    plan["package_status"] = "R6_DERIVED_NOT_EXECUTED"
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
        print("R6_CHECK|%s|bytes=%d|sha=%s|源仍冻结=%s" %
              ("IDEMPOTENT" if same else "DIFFERS", len(body.encode("utf-8")), want[:16],
               sha(SRC) == FROZEN_SRC_SHA))
        return 0 if same else 1
    if OUT.exists() and sha(OUT) != want and not a.force:
        print("R6_REFUSE_WRITE|%s 已存在且字节不同(sha %s), 不静默覆盖; 需要时显式 --force"
              % (OUT.name, sha(OUT)[:12]))
        return 1
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print("R6_WRITE|%s|bytes=%d|sha=%s" % (OUT.as_posix(), len(body.encode("utf-8")), want[:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
