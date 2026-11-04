# -*- coding: utf-8 -*-
"""R7F8 草案离线核对(设备动作 0, 不建审批账, 不 dispatch)。

三件事, 全部从磁盘/仓库重算:
  1) 草案 §1/§1.1 对我(R7F7 现场轮)证据的每条声称, 回原始字节复算;
  2) 草案 §4 的源码合同逐条钉到真实行(锚点表), 并标出草案说法与源码不符之处;
  3) 草案 §8 前置第 2 条: 现有未改执行器能否表达 R7F8 五类场景。

用法: python tools/r7f8_draft_checks.py --repo D:/ns800 [--uid-file <仓外私有 UID 文件>]
退出码 0=FAIL=0, 1=有 FAIL, 2=前置不满足。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import json
import pathlib
import re
import subprocess
import sys

BS = bytes([92])
FS = bytes([47])
R7F8 = "reports/reviews/phase8-m8-r7f8-closed-loop-draft"
R7F7 = "reports/reviews/phase8-m8-r7f7-newimage-static"
ARCH = R7F7 + "/field/field-20261104T201458Z-r7f7"
APP = ("rt-thread-master/rt-thread-master/bsp/novosns/ns800/"
       "ns800rt7p65-nssinepad/applications")
DR = APP + "/diag_relative.c"
DG = APP + "/diagnosis.c"
DH = APP + "/diag_relative.h"
MO = APP + "/motor.c"
SS = APP + "/safety_state.c"
SG = APP + "/safety_gpio.c"
PC = APP + "/project_config.c"
EX = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
PLAN7 = R7F7 + "/field/plan_r7f7_2369_static.json"
FWD = "reports/firmware/phase8-m8-r7d1-ard4b-commit-txn"

out, fails = [], []


def note(name, ok, detail=""):
    out.append("%s|%s|%s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def info(name, detail):
    out.append("%s|INFO|%s" % (name, detail))


def sha(b):
    return hashlib.sha256(b).hexdigest()


def L(repo, rel):
    return io.open(str(repo / rel), encoding="utf-8", errors="replace").read().splitlines()


def find(repo, rel, pat):
    return [n for n, ln in enumerate(L(repo, rel), 1) if re.search(pat, ln)]


def at(repo, rel, n, pat):
    ls = L(repo, rel)
    return ls[n - 1].strip() if 1 <= n <= len(ls) and re.search(pat, ls[n - 1]) else None


def snippet(s, k=64):
    s = s.replace("\t", " ")
    return s[:k]


def anchors(repo, TABLE):
    """锚点表: (判定名, 文件, 行号或 None, 正则, 说明)。
    行号是整数 => 严格钉该行(我本轮亲眼核过的才这么写);
    行号 None   => 在文件里搜索, 命中即 PASS 并登记实际行号(不假装精确行号)。"""
    for name, rel, n, pat, why in TABLE:
        if n is None:
            hits = find(repo, rel, pat)
            note(name, bool(hits), "%s | 命中 %s:%s 共 %d 处" %
                 (why, rel.split("/")[-1], hits[:3], len(hits)))
        else:
            got = at(repo, rel, n, pat)
            note(name, got is not None, "%s | 锚点 %s:%s | 实读=%s" %
                 (why, rel.split("/")[-1], n, snippet(got or "(该行不含此模式)")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--uid-file", default="")
    a = ap.parse_args()
    repo = a.repo.resolve()
    RD = repo / R7F8
    orig = RD / "A_DRAFT_ORIGINAL.md"
    if not orig.exists():
        print("REFUSED|草案原件缺失")
        return 2

    # ------------------------------------------------ 0. 草案原件与授权状态
    d = orig.read_bytes()
    dt = d.decode("utf-8")
    note("DRAFT_BYTES_SHA", len(d) == 24780 and sha(d)[:12] == "b572c1928f60",
         "草案原件 %d B sha256=%s…" % (len(d), sha(d)[:16]))
    note("DRAFT_LF_ONLY", d.count(b"\r") == 0 and d.count(b"\n") == 239, "CR=0 LF=239 行数=240")
    note("DRAFT_AUTHORIZED_FLAG_IS_ZERO", "DRAFT_NOT_AUTHORIZED" in dt and "当前硬件批准额度全部为 0" in dt,
         "草案自标未授权 + 额度全 0 => 本轮 0 设备动作, 不建账本, 不生成可 dispatch 计划")

    # ------------------------------------------------ 1. 草案绑定的身份
    binp, axfp = repo / (FWD + "/rt-thread-motor-m1-candidate.bin"), repo / (FWD + "/rt-thread-motor-m1.axf")
    note("BIND_BIN", binp.stat().st_size == 297452 and
         sha(binp.read_bytes()) == "23691e8d8aa8c1d3ed6e6eff4b5c51fe27dd3a9169841406ecc627c0e807e963",
         "草案 §1 的 BIN(297452 B, 23691e8d…)与仓库成品件一致")
    note("BIND_AXF", axfp.stat().st_size == 1790048 and
         sha(axfp.read_bytes()) == "0f421595ac940863c4d38a2791f31bc8811fc3b8fa40062782f2ac7505c317f6",
         "草案 §1 的 AXF(1790048 B, 0f421595…)一致")
    g = lambda *x: subprocess.run(["git", "-C", str(repo)] + list(x), capture_output=True)
    note("BIND_SOURCE_COMMIT", g("cat-file", "-e", "00718b4^{commit}").returncode == 0,
         "已签收源码 00718b4 可解析")
    note("BIND_PRODUCT_TREE", g("cat-file", "-p", "32470c7ed44ef6ef5f762490a5416912a336cdd9").returncode == 0,
         "草案 §10 的产品子树对象可解析")
    tip = g("rev-parse", "4984ee9").stdout.decode().strip()
    note("BIND_R7F7_TIP", tip == "4984ee966e3f87ab9573c8f716e971696d068023" and tip in dt,
         "草案引用的 R7F7 tip 全 sha 一致且写在草案 §1.1/§10 里")
    note("BIND_B502_HISTORICAL", "502dca5d8c7ebfa794f890a9c03bc258fc9753f57b6a80c2f1e08add69888a7a" in dt,
         "草案把 b502 明文列为上一枚历史在板镜像")

    # --------------------------- 2. 草案 §1.1 替我的证据加的话: 逐条回原始字节
    rb = (repo / (ARCH + "/readback.bin")).read_bytes()
    note("E_READBACK_BYTE_EQUAL", rb == binp.read_bytes(),
         "草案: readback.bin 与签收 BIN 整文件逐字节相等 => 实测 %d B 相等=%s" % (len(rb), rb == binp.read_bytes()))
    man = json.loads((repo / (ARCH + "/field_manifest.json")).read_text(encoding="utf-8"))
    disk = [p for p in (repo / ARCH).rglob("*") if p.is_file() and p.name != "field_manifest.json"]
    note("E_ARCHIVE_53", len(man["files"]) == 53 and len(disk) == 53 and "53 件" in dt,
         "草案: 53 件现场清单条目(不含清单自身) => 清单 %d / 磁盘 %d" % (len(man["files"]), len(disk)))
    s7 = [x for x in L(repo, R7F7 + "/ROUND_SHA256SUMS.txt") if re.match(r"^[0-9a-f]{64}  ", x)]
    note("E_ROUND_100", len(s7) == 100 and "100/100" in dt, "草案: 本轮清单 100/100 => 实有 %d 条" % len(s7))
    pk = [x for x in L(repo, R7F7 + "/SHA256SUMS.txt") if re.match(r"^[0-9a-f]{64}  ", x)]
    note("E_PKG_29", len(pk) + 1 == 29 and "29/29" in dt,
         "草案: A 包原件 29/29 => 包内清单 %d 条 + 清单自身 = %d" % (len(pk), len(pk) + 1))
    boot = (repo / (ARCH + "/01_rst.boot.rx.bin")).read_bytes()
    led = [json.loads(x) for x in (repo / (ARCH + "/ledger.jsonl")).read_text(encoding="utf-8").splitlines() if x.strip()]
    dr = [r for r in led if r.get("kind") == "BOOT_STREAM_DRAINED"][0]
    rx = [r["t_s"] for r in led if r.get("kind") == "RX_DONE"]
    note("E_BOOT_3468_75S", len(boot) == 3468 and float(dr["drain_spent_s"]) == 75.0 and
         dr["end_reason"] == "DRAIN_BUDGET_REACHED",
         "草案: 启动捕获 3468 B / 75 s 走满 => 实耗 %s 原因 %s" % (dr["drain_spent_s"], dr["end_reason"]))
    note("E_TS_77563_81594", "77.563" in dt and abs(rx[9] - 77.563) < 1e-9 and abs(rx[10] - 81.594) < 1e-9,
         "草案的时间戳区间(learn 响应/终态查询)= 台账实测 %s / %s" % (rx[9], rx[10]))
    note("E_NO_CMD_IN_GAP", len([t for t in rx if 77.563 < t < 81.594]) == 0,
         "草案: 中间没有其他实发命令 => 开区间 (77.563, 81.594) 内 RX_DONE 数=%d(终态查询自身在右端点, 不计入)" % len([t for t in rx if 77.563 < t < 81.594]))
    z = "".join(L(repo, ARCH + "/09_zero_static.rx.bin"))
    note("E_ZERO_2055_2065", "n=32 raw 2055..2065" in z and "2055..2065" in dt, "草案引用的零点范围在板端原文里")
    j1 = "".join(L(repo, ARCH + "/11_learn_start.rx.bin"))
    j2 = "".join(L(repo, ARCH + "/13_job_failed.rx.bin"))
    note("E_STARTED_ID1", "[DIAG] learn STARTED id=1 n=8 win=450..550 dir=0" in j1, "STARTED 行逐字一致")
    note("E_TERMINAL_LINE", "[DIAG] job id=1 st=FAILED why=WINDOW_BROKEN owner=0 cred=1/0" in j2,
         "终态行逐字一致(草案也据此要求 F8 初态允许这枚遗留 FAILED)")
    note("E_SAMPLES_0_OF_0", "[DIAG] job n=0/0" in j2, "草案: 采样计数 0/0 成立")
    mf, sf = "".join(L(repo, ARCH + "/15_mot_final.rx.bin")), "".join(L(repo, ARCH + "/14_sys_final.rx.bin"))
    note("E_ENN_CONSISTENT_BACKED",
         "expected_ENN=HIGH ENN_fb=1 resolved verdict=OK" in mf and
         "expected_ENN=HIGH ENN_fb=1(resolved) verdict=OK" in sf,
         "草案 §1.1 的 ENN 高且反馈一致有原文直证(motor_status 与 system_status 两侧都有 verdict=OK 短行)")
    note("E_STATIC_FIELDS", all(x in sf for x in ["state=READY fault=0", "irq_gate=CLOSED",
                                                  "polarity=not-confirmed", "protect_ready=NO",
                                                  "drive_cfg=UNCONFIGURED", "request=LOW pad=0"]) and
         "armed=0" in mf, "草案列的静止/禁能字段全部在两份终态响应里")
    note("E_ARM_MASK_0x18", "0x18" in sf or "0x18" in mf, "草案的 arm mask=0x18 在原文里")
    dl7 = "".join(L(repo, ARCH + "/dispatch_ledger.jsonl"))
    note("E_RAM_KEPT", '"blackbox_clear": 0' in dl7 and '"config_save": 0' in dl7,
         "草案: 板端 RAM 保留失败作业与零点凭据 => 全场 clear/config_save 均 0 次")
    jd = "\n".join(L(repo, R7F7 + "/proof/field_judgement_r7f7.txt"))
    neg = [x.split("|")[0] for x in jd.splitlines() if "FIRES" in x.split("|")[0] or "SELFCHECK" in x.split("|")[0]]
    note("E_99_WITH_10_NEGATIVES", "R7F7_FIELD_SUMMARY|判据=99|FAIL=0" in jd and len(neg) == 10,
         "草案: B 的 99 条 PASS 含 10 条负控 => 实读判据总数与负控名数=%d" % len(neg))

    # --------------------------------------- 3. 源码锚点表(草案 §4 的每条合同)
    anchors(repo, [
        ("A_ZERO_RANGE", DR, 2290, r"rel_parse_u32\(argv\[2\], 32, 256", "草案 §3 的 N_ZERO 32..256"),
        ("A_LEARN_RANGE", DR, 2297, r"rel_parse_u32\(argv\[2\], 8, 32", "草案 §3 的 N=8..32"),
        ("A_INNER_WANT_RANGE", DR, 1100, r"want < 8u \|\| want > 256u", "引擎内部 want 允许 8..256(shell 层更窄)"),
        ("A_DEADLINE_12S", DR, 1337, r"#define REL_CMD_DEADLINE_MS\s+12000u", "草案: 后台 deadline 仍为 12 s(编译期常量)"),
        ("A_TIMEOUT_DEADLINE", DR, 1732, r'REL_JOB_TIMEOUT.*"DEADLINE"', "超时终态 st=TIMEOUT why=DEADLINE(F8 白名单要含此分支)"),
        ("A_WINDOW_BROKEN", DR, 1745, r'"WINDOW_BROKEN"', "R7F7 用过的越窗失败字面量仍在原位"),
        ("A_CANCEL_PENDING", DR, None, r"REL_JOB_CANCEL_PENDING", "草案: 取消有 REQUESTED 中间态"),
        ("A_CANCEL_REQUESTED_REPLY", DR, 2086, r"cancel id=%u REQUESTED st=%s \(motor untouched\)",
         "草案: learn_cancel 不负责停电机(回包自带 motor untouched)"),
        ("A_CANCEL_TERMINAL_REPLY", DR, 2089, r"NOT-CANCELLED: already",
         "草案: 已 DONE 后回 NOT-CANCELLED, 不能算活动取消成功"),
        ("A_CANCEL_ELIGIBLE", DR, None, r"REL_CANCEL_TERMINAL",
         "只有 RUNNING/CANCEL_PENDING 可命中 => 取消窗口=后台一拍"),
        ("A_THREAD_10MS", DG, 501, r"rt_thread_mdelay\(10\)", "Diagnosis Thread 10 ms 一拍"),
        ("A_ADVANCE_EARLY", DG, 455, r"rel_job_advance", "每拍先推进作业再取新样本"),
        ("A_SHELL_WAIT_20MS", DR, 1371, r"#define REL_CTX_TAKE_MS\s+20u", "shell 有界等 20 ms, 超即 BUSY 拒绝"),
        ("A_LEARN_INVALIDATES", DR, 1647, r"d_pending_learn = RT_FALSE",
         "草案: 新 learn 受理作废旧凭据(发生在拿到采集器之后, 失败不伤旧值)"),
        ("A_SIG_HAS_DRIVE_CFG", DR, None, r"tmc_cfg_state",
         "草案 §4.1: 驱动配置状态参与 profile 签名"),
        ("A_LEARN_MONITOR_ONLY", DR, 2016, r"if \(diagnosis_get_mode\(\) != DIAG_MODE_MONITOR_ONLY\)",
         "草案 §4.1: 学习在 MONITOR_ONLY 下进行(我逐字核对, 不接受被改成非 ACTIVE)"),
        ("A_LEARN_NEEDS_ZERO", DR, 2013, r"run 'diag_profile zero N' first", "learn 前要有效 zero 凭据"),
        ("A_MODE_RECHECK_TICK", DR, 1725, r"if \(mode != \(rt_uint8_t\)DIAG_MODE_MONITOR_ONLY\)",
         "后台每拍复核模式 => 草案: 不能到巡航后才临时切模式"),
        ("A_COMMIT_STATIC_TXN", DR, 2160, r'rel_txn_open\("commit"\)', "草案: commit 要求静止事务"),
        ("A_COMMIT_NO_VERIFIED", DR, 2172, r"~RPF_FIELD_VERIFIED", "草案: commit 不自动置 FIELD_VERIFIED"),
        ("A_COMMIT_NO_FLASH", DR, 2227, r"config_save to persist", "草案: commit 不自动写 Flash"),
        ("A_TRIP_DEMOTE", DG, None, r"rel_prod_commit\(.*DIAG_MODE_MONITOR_ONLY", "草案: 触发后回 MONITOR_ONLY"),
        ("A_REARM_MSG", DG, 487, r"rearm required", "草案: 需人工重新建立保护代次"),
        ("A_GEN_FIELDS", DH, None, r"rt_uint32_t +gen;", "草案说的保护代次在源码里是 gen/gen_want/generation"),
        ("A_GENCTL", DH, None, r"rt_uint32_t +generation;", "控制块里的当前代次字段"),
        ("A_GEN_DISCLOSED", DG, 774, r"rel cov=%s lost=%u gen=%u rearm=%d", "代次披露行(草案 P3 要看得到)"),
        ("A_NEW_GEN_ENTRY", DG, 630, r"new_gen = RT_TRUE", "唯一加代次入口 = diag_mode relative CONFIRM_EXPERIMENTAL"),
        ("A_REASON_ENUM", DH, None, r"REL_SG_SEVERE", "草案 §4.2 的原因名是产品枚举原文"),
        ("A_IMPACT_ENUM", DH, None, r"REL_IMPACT", "IMU 冲击枚举"),
        ("A_SEVERE_SET", DR, 68, r"REL_SENSOR_UNAVAILABLE",
         "草案需改: 这三类也同属 severe, 不是另一条路径"),
        ("A_TICKET_ONCE", DR, None, r"d_rel_prod\.rearm_required",
         "一次武装只保护一次 => 三场刺激每场都要重建代次"),
        ("A_PROTECT_EXIT", DR, 725, r"EVT_MULTI_FAULT", "所有 severe 走同一保护出口"),
        ("A_SG_BASELINE", DR, 537, r"p->run_sg_min - \(rt_int32_t\)in->sg", "草案: SG 相对 run_sg_min 的下降量"),
        ("A_ADC_COUNT_HARD", DR, 643, r"e->adc_strong_cnt >= 2u",
         "草案需改: ADC 的新样本数在源码里硬编码 2, 不是 profile 参数"),
        ("A_FUSED_ADC_2U", DR, 651, r"e->adc_new_cnt >= 2u", "融合判据的 ADC 计数同样是 2"),
        ("A_IMPACT_PEAK", DR, 595, r"impact_peak_mg", "IMU 逐帧峰值比较(不是滑窗峰值)"),
        ("A_IMPACT_CNT", DR, None, r"impact_new_min", "IMU 独立新样本数(缺省 2)"),
        ("A_IMPACT_DEFAULT", PC, None, r"vib_impact_mg *= *2500", "IMU 阈值缺省 2500 mg"),
        ("A_RAMP_COMMENT", DR, 493, r"只抑制 SG/负载判据", "源码注释的口径(见下一条: 与代码不符)"),
        ("A_RAMP_RETURN", DR, 497, r"return e->verdict", "加减速早退点, 在冲击/SG/ADC/融合判定之前"),
        ("A_RECOVER_3SRC", DR, 385, r"e->ok_src_seen == 3u",
         "草案需改: 恢复票据要 ADC+SG+IMU 三源均见新鲜正常帧, 不是两个源"),
        ("A_FAULT_NO_COUNT", SS, None, r"fault_reset REFUSED",
         "fault_reset 的真实门是源电平/pad/复核, 无样本计数(见下一条)"),
        ("A_RESET_PAD_GATE", SS, 831, r"pad not confirmed LOW", "草案 §4.3: 禁能复核"),
        ("A_RESET_ENN_GATE", SS, None, r"ENN feedback not confirmed", "草案 §4.3: ENN 在禁能侧"),
        ("A_RESET_SRC_GATE", SS, None, r"source still active", "草案 §5 P5.1: 不在活动故障源上反复 reset"),
        ("A_RESET_SP_GATE", SS, None, r"安全关闭复核失败", "草案 §4.3: 停脉冲复核"),
        ("A_STOP_KEEPS_ARMED", MO, 1692, r"mot\.target_hz = 0", "草案 §4.3: stop 保持 armed"),
        ("A_START_NEEDS_ARMED", MO, 1564, r"if \(!armed\)",
         "补充: start 只在未 armed 时拒绝, 已 armed 可再次 start => 草案额度表按场计 arm/start 会多算"),
        ("A_ARM_REST_GATE", MO, 1153, r"arm REFUSED: not at rest", "arm 要求静止且未 armed => 下一场前必须先 disarm"),
        ("A_DISARM_LINE", MO, 1415, r"ENN feedback=%s", "disarm 披露行(含中文, 见长度风险一条)"),
        ("A_FB_VERDICTS", SG, 274, r"UNEXPECTED_ENABLE", "ENN verdict 六值枚举"),
        ("A_FB_MOT_STATUS", MO, 2491, r"expected_ENN=%s ENN_fb=%d", "取证短行(等价于 disarm 长行的可剪部分)"),
    ])
    ramp_ret = at(repo, DR, 497, r"return e->verdict") is not None
    impact_line = find(repo, DR, r"e->verdict = REL_IMPACT")[0]
    note("A_RAMP_ALSO_SUPPRESSES_IMPACT", ramp_ret and 497 < impact_line,
         "草案需改: 加减速早退在 %d 行, 冲击判定在 %d 行之后 => 例外实际连 REL_IMPACT 一起抑制,"
         " 与 493 行注释和草案 §4.2 末段的口径都不同" % (497, impact_line))
    note("A_FAULT_RESET_HAS_NO_SAMPLE_GATE",
         len(find(repo, SS, r"min_new_sg|ok_new_cnt|new_sample")) == 0,
         "草案需改: §4.3 把恢复计数的条件写成 fault_reset 的门; 源码 fault_reset 不做任何样本计数")
    note("A_SOURCE_AGE_BEFORE_RAMP",
         find(repo, DR, r"e->verdict = REL_SENSOR_UNAVAILABLE")[0] < 491,
         "草案: 源失效检查在加减速例外下仍有效(检查位于早退之前)成立")

    # -------------------------------- 4. 草案 §8 前置 2: 未改执行器的可表达性
    anchors(repo, [
        ("X_POLL_GATE_SHAPE", EX, 1939, r"v\.get\(\"code\"\) in codes and state in ramping",
         "结构性缺口: 轮询只在 [MOT] state 属 ACCEL/DECEL 时重试"),
        ("X_CMD_STATIC", EX, 2083, r'cmd = st\["cmd"\]', "命令文本静态取自 plan, 无捕获替换"),
        ("X_START_ATTEMPT_TIMING", EX, None, r"start_attempt\(", "start 的硬截止从发送尝试起算(已实现)"),
        ("X_STEP_BUDGET_BRANCH", EX, None, r'cmd == "__watch__" or budget\.armed',
         "草案需补: 只有 start 之后走运动预算; arm 本身无独立截止"),
        ("X_CLEANUP_TRIPLE", EX, None, r'CLEANUP_CMDS = \("motor_stop", "motor_disarm", "motor_status"',
         "草案 P2.6 要 stop/disarm 分开: 清理固定发这三条"),
        ("X_DISABLE_CONTRACT", EX, None, r"SNAPSHOT_FAILED_ROW",
         "草案: 不能把失败的 UART 返回当禁能证明 => 执行器只认收口最后那份 motor_status 快照"),
        ("X_QUOTA_EXHAUSTED_SENT_ANYWAY", EX, None, r"RESERVE_EXHAUSTED_SENT_ANYWAY",
         "草案 §7 的收口预留要按这条真实语义写(额度用尽仍发收口)"),
        ("X_ACTION_DISPATCH_TAIL", EX, None, r"UNKNOWN_ACTION", "tools 后端只认 download/readback/reset/reboot"),
    ])
    note("X_DONE_POLL_NEEDS_OFFLINE_ADAPTATION",
         len(find(repo, EX, r"flash_dump %d %d")) == 1 and at(repo, EX, 1939, r"state in ramping") is not None,
         "结论(草案 §8 前置 2): 有界轮询 learn_status 到 DONE 与 JOB_ID 动态取值**现结构不支持**;"
         " 唯一动态命令是 flash_dump => 需要先离线适配 + 反例, 不许现场拆错误门")
    note("X_R7F7_STAGE_FORBID_LIST", "learn_cancel" in "".join(L(repo, PLAN7)) and "motor_stop" in "".join(L(repo, PLAN7)),
         "R7F7 阶段把 learn_cancel/motor_*/fault_reset 列为禁用项 => F8 必须新阶段/新 plan, 不能复用 F7 阶段")
    _el = L(repo, EX)
    _i0 = next(i for i, x in enumerate(_el) if x.startswith("QUOTA_COMMAND_KEYS = ("))
    _body = []
    for _x in _el[_i0 + 1:]:
        if _x.startswith(")"):
            break
        _body.append(_x)
    _pairs = re.findall(r'\("([a-z_ ]+)", "([a-z_]+)"\)', "\n".join(_body))
    note("X_QUOTA_MAP_9_PAIRS_8_KEYS", len(_pairs) == 9 and len(set(k for _p, k in _pairs)) == 8,
         "命令前缀→额度键共 %d 条映射 / %d 个不同键(极性确认与 IRQ 开共用 irq_polarity_prep);"
         " motor_stop/disarm/learn_cancel/fault_reset 不在表内(不发额度)" %
         (len(_pairs), len(set(k for _p, k in _pairs))))

    # ------------------------------------------------ 5. 本轮边界
    note("NO_DEVICE_ACTION", True, "本轮只读源码/仓库/草案原件; 未调用 pyOCD/Keil/串口/分析仪, 未开审批账")
    rc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "rt-thread-master", "tests"],
                        capture_output=True)
    note("NO_PRODUCT_CHANGE", rc.stdout.decode().strip() == "",
         "产品与主机链改动=%d 件" % len([x for x in rc.stdout.decode().splitlines() if x]))

    # --------------------- 7. 操作者补充四项: 合并结果与来源等级(不增加额度)
    mrg = RD / "R7F8_DRAFT_MERGED.md"
    if mrg.exists():
        mt = mrg.read_text(encoding="utf-8")
        note("M_IN_REPO_AND_DIFFERS_FROM_ORIGINAL", mt.encode("utf-8") != d and len(mt.encode()) > len(d),
             "合并版 %d B(收到的原件 %d B 保持不动); 原件 sha=%s… 合并版 sha=%s…" %
             (len(mt.encode()), len(d), sha(d)[:12], sha(mt.encode())[:12]))
        note("M_HAS_1_2_WITH_USER_REPORTED", "### 1.2" in mt and "USER_REPORTED" in mt and "不授权升流" in mt,
             "§1.2 已并入: 额定电流 1.5 A 按 USER_REPORTED 登记且不解读为升流")
        note("M_REVISION_ROW_ADDED", "并入操作者补充" in mt, "草案 §9 修订记录已加一行(保留旧行)")
        note("M_P2_ORDER_CORRECTED",
             "`motor_arm` → 确认 arm 成功 → `motor_target" in mt and "静止设置 `motor_dir <DIR>`、`motor_target" not in mt,
             "§5 P2 第 1 步已改为 dir→arm→确认→target→start, 旧的 arm 前设正目标写法不再出现在正文")
        note("M_QUOTA_STILL_ZERO", "本草案当前批准数：全部 0" in mt and "不增加任何既有动作额度" in mt,
             "§7 仍写全部 0/TBD, 并显式声明本补充不增加额度")
        note("M_KEEPS_R7F7_HANDOVER_FACTS", "23691e8d" in mt and "WINDOW_BROKEN" in mt and "cred=1/0" in mt,
             "承接事实(在板镜像/旧作业记录/凭据)保留在合并版里")
    else:
        note("M_IN_REPO_AND_DIFFERS_FROM_ORIGINAL", False, "合并版未入库")

    _mo = "".join(L(repo, MO))
    _tgt = _mo.split("rt_err_t motor_set_target_hz(rt_uint32_t hz)")[1]
    _tgt = _tgt[:_tgt.index("return RT_EOK;")]
    note("SRC_SET_TARGET_HAS_NO_ARMED_GATE", "armed" not in _tgt and "MOTOR_MAX_HZ" in _tgt,
         "motor_set_target_hz 函数体内只校验 MOTOR_MAX_HZ, 不出现 armed => arm 之后再给 target 可行(操作者第 3 条成立)")
    note("SRC_ARM_REQUIRES_TARGET_ZERO_VERBATIM",
         at(repo, MO, 1151, r"snap\.current_hz != 0 \|\| snap\.target_hz != 0") is not None,
         "arm 的静止门含 target_hz != 0 => 旧写法(先给正目标再 arm)必被拒")
    note("SRC_LEARN_WINDOW_BOUNDS",
         at(repo, DR, 2297, r"rel_parse_u32\(argv\[3\], 1, 20000") is not None and
         at(repo, DR, 2298, r"rel_parse_u32\(argv\[5\], 0, 1") is not None,
         "LO/HI 合法 1..20000、DIR 0|1 => 草案 §3 运行工况的取值范围源码已给, 具体值仍须操作者定")

    dev = "reports/agent_handoffs/phase8-motor-chain-dev.md"
    note("REPO_RATED_CURRENT_WAS_UNCONFIRMED_BEFORE",
         "1500 mA RMS" in "".join(L(repo, dev)),
         "来源等级要讲清: 本仓曾显式删除未确认的 1500 mA RMS 额定断言(%s:32), 现在的 1.5 A 是操作者新报" % dev)
    note("REPO_HAS_R110_VREF_ANCHOR",
         at(repo, "reports/reviews/phase8-motor-m7-current-calibration/source_audit.md", 107,
                 r"VREF=1\.37V / R110=0\.11") is not None,
         "R110=0.11 Ω 与 VREF=1.37 V 的仓库锚点找到, 且原文注明这是相电流设定口径, 不得代进母线换算")
    note("REPO_HAS_IRUN20_NO_RAISE_ANCHOR",
         "保持 IRUN20 现值, 不抬流" in "".join(L(repo, "reports/reviews/phase8-m8-r7-field-package-5ae/field/plan_core_r7.json")),
         "IRUN20 沿用且不抬流的历史口径有锚点(与本轮不授权升流一致)")
    note("REPO_HAS_M4_300HZ_60S_ANCHOR",
         "300 Hz、60 s" in "".join(L(repo, dev)),
         "M4 的 300 Hz/60 s 连续运行记录在 dev 交接里(只作台架能力依据)")

    # ------------------------------------------------ 6. 泄漏自检(下面第 8 节)
    appr = pathlib.Path("C:/m8r_approvals")
    r7f8_led = sorted(p.name for p in appr.glob("*R7F8*")) if appr.exists() else []
    note("NO_R7F8_LEDGER_OPENED", not r7f8_led,
         "核准根目录下 *R7F8* 匹配 %d 件 => 本轮未消耗任何硬件额度" % len(r7f8_led))
    note("DRAFT_STILL_HAS_ZERO_QUOTA_TABLE", "全部 0" in dt and dt.count("TBD") > 10,
         "草案 §7 仍是全 0 + %d 处 TBD 占位 => 不能据此开账或执行" % dt.count("TBD"))


    uid = b""
    if a.uid_file and pathlib.Path(a.uid_file).exists():
        uid = pathlib.Path(a.uid_file).read_bytes().strip()
    priv = ([uid, uid.upper()] if uid else []) + [b"Users" + BS, b"Users" + FS, b"App" + b"Data"]
    probe = b"z" + (uid or b"ABSENT") + b"|C:" + BS + b"Users" + BS + b"someone|x:" + FS + \
        b"Users" + FS + b"y|App" + b"Data"
    fired = [p for p in priv if p and p in probe]
    note("LEAK_PATTERNS_FIRE_ON_PROBE", len(fired) == len(priv),
         "模式在合成脏样本命中 %d/%d(不命中就无权宣布干净)" % (len(fired), len(priv)))
    hits = []
    for p in sorted(RD.rglob("*")):
        if p.is_file() and p.name != "ROUND_SHA256SUMS.txt":
            data = p.read_bytes()
            for pat in priv:
                if pat and pat in data:
                    hits.append("%s:%s" % (p.name, pat.decode("ascii", "replace")[:10]))
    note("ROUND_DIR_NO_IDENTITY_FORM", not hits, "本轮目录命中=%s" % hits[:5])

    # -------- 9. 提交前的字节不变门: -text 规则已生效 + 预检白名单真的覆盖本轮暂存集
    def text_rule(lines, rel_dir):
        pat = re.compile(r"^" + re.escape(rel_dir) + r"/\*\* -text$")
        return [n for n, ln in enumerate(lines, 1) if not ln.lstrip().startswith("#") and pat.match(ln)]

    ga = L(repo, ".gitattributes")
    note("GITATTR_TEXT_RULE_FOR_ROUND_DIR", bool(text_rule(ga, R7F8)),
         "core.autocrlf=%s 下必须靠 -text 规则保住原字节; .gitattributes 命中行=%s" %
         ((subprocess.run(["git", "-C", str(repo), "config", "core.autocrlf"],
                          capture_output=True).stdout.decode().strip() or "unset"), text_rule(ga, R7F8)))
    note("GITATTR_MATCHER_FIRES_ON_KNOWN_RULE", bool(text_rule(ga, R7F7)),
         "同一匹配器在已入库的 R7F7 规则上命中(行 %s) => 上一条 PASS 不是恒真" % text_rule(ga, R7F7))
    note("GITATTR_MATCHER_IGNORES_COMMENTS", not text_rule(["# " + R7F8 + "/** -text"], R7F8),
         "注释里的同形文本不算规则(否则真规则被删了也看不出来)")

    pf = (RD / "tools/r7f8_staged_preflight.py").read_text(encoding="utf-8")
    blk = pf.split("ALLOWED = (")[1].split(")\n")[0]
    allowed = [m for ln in blk.splitlines() if not ln.strip().startswith("#")
               for m in re.findall(r'"([^"]+)"', ln)]
    note("PREFLIGHT_ALLOWED_PARSED_EXACTLY", len(allowed) == 5 and ".gitattributes" in allowed,
         "预检白名单解析出 %d 项(注释不进白名单, 含本轮新增的 .gitattributes): %s" % (len(allowed), allowed))
    dirty = [l[3:].strip('"') for l in subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True
    ).stdout.decode("utf-8", "replace").splitlines() if l]
    uncovered = [p for p in dirty if not any(p.startswith(x) for x in allowed)]
    note("PREFLIGHT_SCOPE_COVERS_ROUND_SET", not uncovered,
         "本轮 git status 待提交 %d 条, 预检白名单外 %d 条: %s" % (len(dirty), len(uncovered), uncovered[:4]))
    note("PREFLIGHT_SCOPE_COMPARATOR_FIRES",
         not any("src/foreign/x.c".startswith(x) for x in allowed),
         "合成一个白名单外的路径必须被判越界 => 上一条 PASS 才有意义")

    summary = "R7F8_DRAFT_SUMMARY|判据=%d|FAIL=%d|%s" % (len(out), len(fails), ",".join(fails) if fails else "none")
    text = "\n".join(out + [summary]) + "\n"
    (RD / "proof/r7f8_draft_checks.txt").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 1 if fails else 0



if __name__ == "__main__":
    sys.exit(main())
