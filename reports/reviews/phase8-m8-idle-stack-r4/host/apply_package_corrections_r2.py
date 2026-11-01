#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R4 现场包返修(离线, 零设备, 不重编): 复审 2026-11-01 的 5 项包问题 + 2 项登记问题一次做完。

  ① S0/下载/回读的镜像与 AXF 身份统一到本轮新镜像(dab356c6…/0babe9f1…), 删除旧 00608ec9 绑定
  ② 顺序改成 S8→S9(取证)→S10(拒绝)→S11(恢复)→S8b→S9b→S10b→S11b; 两场各存自己的分支与事故基线
  ③ 额度按真实执行路径重算(12 次开口 / 5 组必要清理), 覆盖两场均触发与两种混合结果, 共用一本账
  ④ 首因合同改成可执行的四腿关联; 缺证具名 EVIDENCE_INCOMPLETE, 已观察到的触发不得降级
  ⑤ 机械前置更新为现有 T8 完整滑台(未连接、导程待确认); 缺加载方法在**起转前**实际拒绝
  R  登记修正: 不再把 48204fe 写成新镜像的构建输入; rtconfig 登记本机 CRLF/远端 LF 双身份
  C-02 停后帧事件码 9 -> 5(本计划里的两处 require_frames; 夹具与 R3 自检由
      apply_c02_event5_fixtures.py + m8r3_field_selftest.py 负责)
  R5-① "流程跑完"与"目标传感器通道验证成功"分开: 每场申报 target_channel(IMU 只认 REL_IMPACT 且
      imu 列非零; SG/ADC 认 REL_SG_SEVERE/REL_ADC_SEVERE/REL_FUSED_SEVERE 且 sg/cur 非零),
      其他来源触发仍撤能、仍取证, 但登记为未证, 不计入 core_protection_verified
  R5-② 机械事实更新为"T8 丝杆套件, 标称导程 8 mm; 已按照片装配, 紧固/螺母防转/导向尚待确认",
      并把 assembly_readiness 门挂到 S4/S8/S8b/S10/S10b 每一个起转阶段(拒绝沿 requires 传播)

幂等: 每次都从当前 plan_core_r4.json 出发重算派生字段(额度/执行器散列/夹具散列), 再跑一遍得同一份字节。
用法: python apply_package_corrections_r2.py [--check]
"""
import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../phase8-m8-idle-stack-r4/host
ROOT = HERE.parents[3]                          # 仓库根
FIELD = HERE.parent / "field"
PLAN = FIELD / "plan_core_r4.json"
CHAIN = (ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py")
FW = ROOT / "reports/firmware/phase8-m8-idle-stack-r4"
BSP = "rt-thread-master/rt-thread-master/bsp/novosns/ns800/ns800rt7p65-nssinepad"
R3_ROWS = ROOT / "reports/reviews/phase8-m8-relative-protection-r3/field/rows.json"

BIN_REL = "reports/firmware/phase8-m8-idle-stack-r4/rt-thread-motor-m1-candidate.bin"
AXF_REL = "reports/firmware/phase8-m8-idle-stack-r4/rt-thread-motor-m1.axf"
MAP_REL = "reports/firmware/phase8-m8-idle-stack-r4/rt-thread-motor-m1.map"

SC_A, SC_B = "SG_ADC_controlled_load", "IMU_non_destructive_excitation"
SUF = {SC_A: "sg", SC_B: "imu"}
# 目标通道各自认哪些产品规则名(取自 A/diag_relative.h 的 rel_reason_t + verdict_name() 的返回串,
# 由执行器 rel_rule_names() 两头核对)。IMU 那路只有 REL_IMPACT 才算它自己触发。
SG_RULES = ["REL_SG_SEVERE", "REL_ADC_SEVERE", "REL_FUSED_SEVERE"]
IMU_RULES = ["REL_IMPACT"]
ORDER = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7",
         "S8", "S9", "S10", "S11", "S8b", "S9b", "S10b", "S11b"]
CHAIN_OF = {"S8": SC_A, "S9": SC_A, "S10": SC_A, "S11": SC_A,
            "S8b": SC_B, "S9b": SC_B, "S10b": SC_B, "S11b": SC_B}
NON_SPEND_QUOTA = ("motor_run_seconds_total", "motion_cap_seconds_per_run")
ZERO_QUOTA = ("retry", "physical_collision", "blackbox_clear", "dmm_readings")

COMMIT_BUILD_HEAD = "48204fe37de3da6f8caeeff467256562f11e58e2"
COMMIT_INPUT = "fe3d9f5"
COMMIT_INPUT_FULL = "fe3d9f5cc37470d732acbbb222012a15d71573c9"
COMMIT_TIP = "58904e1045e275698a8c80f63943a1bd0afdb2eb"

EVENT_BASIS = ("停后帧的 event = FAULT_MULTI_SOURCE = 5u(A/safety_state.h:46, 由 "
               "A/safety_state.c:309 的 ss_code_for_event() 从 EVT_MULTI_FAULT 映射; 相对保护只在 "
               "A/diag_relative.c:725 投这一个事件)。9=FAULT_SOFT(A/safety_state.h:50)只由 "
               "EVT_SOFT_FAULT 产生, 而 safety_trip 在本场景 forbidden_commands 里 => 钉 9 会把一次"
               "成功的保护判成 NO_FRAME_WITH(C-02, 来自 C-01 交付回执 §4 待办 1)")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def nbytes(p):
    return len(Path(p).read_bytes())


def load_chain():
    spec = importlib.util.spec_from_file_location("chain_for_builder", str(CHAIN))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def rename_groups(pattern, suffix):
    """(?P<name>…) 的组名加链后缀: 记录/比较的键名必须与组名逐字一致(执行器按组名取值)。
    已带该后缀的组不再加(脚本必须幂等: 跑两遍得到同一份字节)。"""
    return re.sub(r"\(\?P<([a-z_]+)>",
                  lambda mo: "(?P<%s>" % (mo.group(1) if mo.group(1).endswith("_" + suffix)
                                          else "%s_%s" % (mo.group(1), suffix)), pattern)


def ns(keys, suffix):
    return [k if k.endswith("_" + suffix) else "%s_%s" % (k, suffix) for k in keys]


def trip_legs(suffix):
    """④ 四腿(运动会话 S8/S8b): 同一次的 TRIP 规则/代际 + 数值阈值 + 源样本 + 本会话 profile 身份,
    外加本场事故基线已经记录。record_as 把抓到的值按链存进整包账, 取证会话再用 must_equal_recorded 咬回来。"""
    def r(k):
        return "%s_%s" % (k, suffix)
    return {
        "applies_when_branch": "trip",
        "covers": ["trip_row", "source_sample", "profile_identity", "incident_session"],
        "on_missing": "EVIDENCE_INCOMPLETE",
        "never_downgrade": ("已经观察到的触发不得写成 NO_TRIGGER(降级由 response_branch 的 "
                            "TRIP_REWRITTEN_AS_NO_TRIGGER 具名拒绝); 缺腿只叫 EVIDENCE_INCOMPLETE, "
                            "既不叫 NO_TRIGGER 也不叫通过"),
        "legs": [
            {"leg": "trip_rule_and_generation", "step": "trip_window",
             "regex": (r"^\[DIAG\] REL TRIP r=(?P<trip_rule>\w+) id=(?P<trip_id>\d+) "
                       r"rev=(?P<trip_rev>\d+)"),
             "capture": ["trip_rule", "trip_id", "trip_rev"],
             "record_as": [r("trip_rule"), r("trip_id"), r("trip_rev")]},
            {"leg": "trip_value_and_threshold", "step": "trip_window",
             "regex": r"^\[DIAG\] REL TRIP val=(?P<trip_val>\d+) thr=(?P<trip_thr>\d+)",
             "capture": ["trip_val", "trip_thr"],
             "record_as": [r("trip_val"), r("trip_thr")]},
            {"leg": "source_sample", "step": "trip_window",
             "regex": (r"^\[DIAG\] REL TRIP sg=(?P<trip_sg>\d+) cur=(?P<trip_cur>\d+) "
                       r"imu=(?P<trip_imu>\d+)"),
             "capture": ["trip_sg", "trip_cur", "trip_imu"],
             "record_as": [r("trip_sg"), r("trip_cur"), r("trip_imu")],
             "why": "源样本值与阈值/持续同批出现, 才谈得上是哪一路越过界; 只有规则名不够"},
            {"leg": "profile_identity", "step": "prof_at_arm",
             "regex": (r"^\[DIAG\] profile id=(?P<pid>\d+) rev=(?P<prev>\d+) abi=(?P<pabi>\d+) "
                       r"flg=(?P<pflags>[0-9A-Fa-f]{4})"),
             "capture": ["pid", "prev"],
             "must_equal_recorded": {"pid": "id", "prev": "rev"},
             "record_as": [r("pid"), r("prev")],
             "why": "本会话看到的 profile id/rev 必须等于 S5 实际提交的那一代(recorded.id/rev), "
                    "否则触发用的不是本轮发布的基线"},
            {"leg": "trip_row_ties_to_profile",
             "equal_pairs": [["trip_id", "pid"], ["trip_rev", "prev"]],
             "why": "TRIP 行自带的 id/rev 与本会话 show 出来的 profile 同一代(同次关联, 不跨会话拼)"},
            {"leg": "incident_session_baseline",
             "recorded_keys": ["bb_session_%s" % suffix, "flash_used_before_%s" % suffix],
             "why": "事故前的黑匣子 session 与日志槽位基线必须已记录; 增大与新帧读回由取证会话判"}],
        "basis": [EVENT_BASIS]}


def latch_legs(suffix):
    """④ 取证会话(S9/S9b)的腿: 锁存视图的规则/代际必须与**本场**那次 TRIP 一致。"""
    def r(k):
        return "%s_%s" % (k, suffix)
    return {
        "applies_when_branch": "trip",
        "covers": ["latch_row", "source_sample", "profile_identity", "incident_session"],
        "on_missing": "EVIDENCE_INCOMPLETE",
        "never_downgrade": "同上: 缺腿 = EVIDENCE_INCOMPLETE, 不是 NO_TRIGGER, 也不是通过。",
        "legs": [
            {"leg": "latch_rule_and_generation", "step": "latch",
             "regex": (r"^\[DIAG\] rel LATCH r=(?P<latch_rule>\w+) id=(?P<latch_id>\d+) "
                       r"rev=(?P<latch_rev>\d+)"),
             "capture": ["latch_rule", "latch_id", "latch_rev"],
             "must_equal_recorded": {"latch_rule": r("trip_rule"), "latch_id": r("trip_id"),
                                     "latch_rev": r("trip_rev")},
             "why": "锁存行的规则/代际必须等于本场观察窗里那次 TRIP(不能拿另一场或上一轮的行拼)"},
            {"leg": "latch_source_sample", "step": "latch",
             "regex": r"^\[DIAG\] rel LATCH sg=(?P<lsg>\d+) cur=(?P<lcur>\d+) imu=(?P<limu>\d+)",
             "capture": ["lsg", "lcur", "limu"],
             "why": "锁存态回读的源样本通道值; 与本场 TRIP 的源样本同路才算同一事故"},
            {"leg": "incident_session_baseline", "recorded_keys": ["bb_session_%s" % suffix],
             "why": "本场事故基线 session 必须已在(增大与实际新帧读回由 compare_recorded_gt / "
                    "require_frames 判)"}]}


def target_channel_block(scenario, suffix, known):
    """把"流程跑完"与"目标传感器通道验证成功"的分工声明(执行器 target_channel_claim 判)。

    复审实测的缺陷: IMU 场喂 SG 的触发行(r=REL_SG_SEVERE、imu=0)也算通过 —— 因为旧版只查
    "有没有抓到一次 trip"。这里把每场**认哪些规则名、哪一列源样本必须非零**写进计划, 规则名还
    必须是产品枚举里的名字(不在枚举里 PLAN_TARGET_CHANNEL 直接离线拒绝)。"""
    is_imu = scenario == SC_B
    return {
        "scenario": scenario,
        "channel": "ADXL345 冲击/振动相对异常(IMU)" if is_imu else "SG2/ADC 电流相对异常",
        "rule_key": "trip_rule_%s" % suffix,
        "rule_names": list(IMU_RULES if is_imu else SG_RULES),
        "source_nonzero_keys": (["trip_imu_imu"] if is_imu else ["trip_sg_sg", "trip_cur_sg"]),
        "known_rule_names": sorted(known),
        "positive_control_fixture": ("__watch__@imu(r=REL_IMPACT, imu=3789)" if is_imu
                                     else "__watch__(r=REL_SG_SEVERE, sg/cur 非零)"),
        "meta_negative_fixture": ("__watch__@imu_zero(r=REL_IMPACT 但 imu=0) => "
                                  "TARGET_RULE_BUT_SOURCE_FIELD_ZERO" if is_imu else
                                  "把本场的观察窗键换成 __watch__@imu(REL_IMPACT) => "
                                  "OTHER_SOURCE_TRIGGERED"),
        "other_source_handling": (
            "规则名不是本场目标通道 => 登记 OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN): "
            "保护确实动作了(照常撤能、照常取证、照常用 event=5 帧读回), 但**这次触发不能算本通道的"
            "功能验证**, 也不计入 core_protection_verified"
            if not is_imu else
            "规则名不是 REL_IMPACT => 同上: 别的手段把保护碰响了不等于 IMU 通道已验证"),
        "why_source_nonzero": (
            "imu 列是冲击证据的源样本: 规则名对但 imu 仍是 0, 说明这次判定没有该通道的读数支撑, "
            "所以只登记规则命中、不登记通道验证" if is_imu else
            "sg=/cur= 是 SG/ADC 那一路的源样本: 任一列为 0 就说明越界判定没有本通道读数支撑"),
        "basis": ("规则名来自 A/diag_relative.h 的 rel_reason_t 与 A/diag_relative.c 的 "
                  "verdict_name()(两头不一致时 rel_rule_names() 报 REL_RULE_NAMES_SOURCE_MISMATCH); "
                  "三行打印形状来自 A/diagnosis.c:469-475")}


def prereq_block():
    """机械前置(复审 2026-11-01 第 2 项: 事实要更新, 而且**每个起转阶段**都要检查装配就绪)。
    status 只有 PROVIDED 才放行; 其余值一律在开端口之前具名跳过, 不扣额度、不发命令。
    本 Agent 不代操作者确认任何一项 —— 状态由操作者现场回报决定。"""
    return {
        "assembly_readiness": {
            "status": "NOT_CONFIRMED",
            "blocks_motion": True,
            "asset": "T8 丝杆套件(标称导程 8 mm)的装配就绪 + 支座/电机能否在不手扶时保持固定",
            "asset_state": ("现有 T8 丝杆套件, 标称导程 8 mm; 已按照片装配, 紧固、螺母防转及导向尚待确认。"
                            "轴承/底座目前只能手动固定(操作者 2026-11-01 原话), 松手保持固定未确认 => "
                            "门不放行, 所有含 motor_arm/motor_start 的阶段在起转之前跳过"),
            "provenance": ("操作者现场回报与照片(2026-11-01): 电机固定丝杆、轴承底座只能手动固定。"
                           "本 Agent 未实测, 只登记为操作者给出的实物事实, 不构成任何现场结论"),
            "open_until": ["支座/轴承在松手后保持固定(已由操作者确认)",
                           "丝杆与电机的连接紧固已确认", "螺母防转已确认", "导向已确认",
                           "累计运动距离按标称导程 8 mm 与实际细分/脉冲参数核算过"],
            "gates_stages": "S4(带起转的学习)、S8/S8b(两场激励)、S10/S10b(锁存态再 arm) —— 即计划里"
                            "每一个含 motor_arm 或 motor_start 的阶段",
            "propagation": ("S4 被挡之后, requires 里引用本次学习结果的 S5/S6/S7 一并跳过"
                            "(执行器沿计划申报的 requires 传播拒绝): 没有本轮 learn 就 tier/commit/save, "
                            "现场只会中途失败并烧掉唯一一次批准"),
            "would_be_used_by": "S4 巡航学习与 S8/S8b 两场激励的起转前提(也是整个运动路径的前提)",
            "fallback_registered": ("操作者给出的**备选**(本轮未采纳为默认): 不通电时脱开丝杆, 保留扩展板"
                                    "固定在电机上, 沿此前已验证的空载电机路径完成学习与 IMU 保护演示; "
                                    "SG/ADC 受控加载继续明确记为待验证。备选同样要先确认电机+扩展板在松手后"
                                    "保持固定, 因此也受本门控制")},
        "load_means": {
            "status": "NOT_PROVIDED",
            "blocks_motion": True,
            "asset": "现有实物: T8 丝杆套件(标称导程 8 mm) + 导轨滑台, 已按照片装配",
            "asset_state": ("装配已完成但紧固/螺母防转/导向待确认(见 assembly_readiness); "
                            "可从非旋转端操作的**加载作用点与档位/行程**仍未提供"),
            "provenance": ("操作者 2026-11-01 现场回报 + 复审原文。本 Agent 未实测。导程是**标称**值, "
                           "未做实测标定"),
            "constraints": ["现场只用中间短行程", "不得把标称 8 mm 导程当作实测标定值",
                            "滑台起点在左端且与末端限位留有间隙(操作者口径)",
                            "顺时针=向右, 须在**第一次被批准的短移动**里现场核实后才按此方向执行",
                            "软件限位输入仍为 DISABLED_BY_CONFIG, 不得把左侧起点当限位保护",
                            "不新增回零/全程定位功能", "不通过撞两端限位制造堵转",
                            "不手捏旋转轴、不为凑阈值加危险力"],
            "still_missing": ["可从非旋转端操作的加载作用点及档位/行程",
                              "电机与滑台的连接方式(装配照片之外可核的紧固状态)"],
            "gate": ("机械前置门在执行器里生效: 状态不是 PROVIDED 时该场景链在开端口之前就被具名跳过"
                     "(台账 STAGE_SKIPPED_BY_PREREQUISITE + 汇总 PREREQUISITE_REFUSAL 行), 不扣 "
                     "motor_run 额度、不发 motor_arm/motor_start —— 复审第 5 项要求的是这个, 不是说明字段"),
            "would_be_used_by": "S8/S10 的 SG/ADC 受控负载激励"},
        "instrument_probe": {
            "status": "NOT_PROVIDED",
            "blocks_motion": False,
            "asset": "示波器/电流钳(独立证 STEP 到关断, BUG-008)",
            "asset_state": "继续暂缓(复审本轮口径): 不新增高精度仪器前置, 也不因此卡住起转",
            "consequence": "停机事实只能登记为软件接口已接受, 不构成物理停机证明"},
        "imu_mounting": {
            "status": "MOUNTED_TAPE_BY_OPERATOR_RIGIDITY_UNMEASURED",
            "blocks_motion": False,
            "asset": "ADXL345(在扩展板上)的实际固定方式",
            "asset_state": ("已由操作者确认: 扩展板用绝缘胶带固定在电机上, 电机固定丝杆(2026-11-01)。"
                            "此前登记的缺失项\"IMU 实际固定方式\"已闭合"),
            "consequence": ("胶带固定可以按采样结果判定演示是否可行(操作者口径, 厂商提示 PCB 振动可能影响"
                            "读数), 但**不做精确振动标定**; 结论上限是本次未证伪, 不是刚度已验证"),
            "not_claimed": ["振动传递路径已知", "IMU 阈值可用", "胶带装配等价于可靠支架"]}}


def first_cause_contract(scenario, suffix):
    return {
        "required": ("同一次运行里四件必须咬合(执行器按 first_cause_legs 判, 不是说明字段): "
                     "① 本会话观察窗内的 [DIAG] REL TRIP r=/id=/rev= 行; "
                     "② 同批的 val=/thr= 与 sg=/cur=/imu=(源样本); "
                     "③ 本会话 diag_profile show 的 id/rev, 且等于 S5 实际提交的 recorded.id/rev; "
                     "④ 本场事故基线 bb_session_%s / flash_used_before_%s 已记录, 并在取证会话严格增大 "
                     "+ 读回到该 session 的实际停后帧(event=5, step_hz=0)" % (suffix, suffix)),
        "prohibited": ["用 safety_trip 注入代替真实越界(它在所有场景的 forbidden_commands 里)",
                       "把缺腿的取证写成通过, 或写成 NO_TRIGGER",
                       "跨会话/跨轮拼字段(TRIP 行与 profile 行必须来自同一场同一会话)"],
        "if_legs_missing": ("EVIDENCE_INCOMPLETE: 事件观察到了, 但谁引起这次停机还没证。阶段不算成功, "
                            "而必要撤能照常先完成(cleanup 在 exec_session 的 finally 里, 早于本判定)"),
        "if_not_observed": ("观察窗到时未触发 => OBSERVATION_NO_TRIGGER(成功终态), 该链的取证/拒绝/恢复"
                            "阶段按分支跳过并具名 STAGE_SKIPPED_BY_PROFILE; 这不等于保护已验证"),
        "operator_gate": ("加载手段属机械前置: 见 mechanical_prerequisites.load_means。NOT_PROVIDED 时"
                          "整条链在开端口之前就被拒绝, 人工门的答案不能绕过它"),
        "scenario": scenario}


def fix_identity(plan, ident, tools):
    tools["download"]["image"] = {
        "path": AXF_REL, "bytes": nbytes(FW / "rt-thread-motor-m1.axf"),
        "sha256": sha(FW / "rt-thread-motor-m1.axf"),
        "identity_source": "本轮构建产物逐字节复算; validate_plan 的 "
                           "PLAN_IMAGE_IDENTITY_CONSISTENCY 要求它等于 identity_binding.image.axf, "
                           "且 download argv 的最后一个参数就是这个路径"}
    tools["readback"]["image"] = {
        "path": BIN_REL, "bytes": nbytes(FW / "rt-thread-motor-m1-candidate.bin"),
        "sha256": sha(FW / "rt-thread-motor-m1-candidate.bin"),
        "identity_source": "与 identity_binding.image 同一件(S0 现场重算并与 build_manifest 相等)"}
    tools["readback"]["length_note"] = ("长度 = @IMAGE_BYTES = identity_binding.image.bytes; 比较对象就是"
                                        "本轮新镜像。上一轮在板镜像只登记为 identity_binding."
                                        "board_image_before, 不是回读目标")
    ident["image"] = {
        "path": BIN_REL, "bytes": nbytes(FW / "rt-thread-motor-m1-candidate.bin"),
        "sha256": sha(FW / "rt-thread-motor-m1-candidate.bin"),
        "sha256_source": "build_manifest.json targets[rt-thread-motor-m1-candidate]"
                         ".images[rt-thread-motor-m1-candidate.bin]; S0 现场重算并要求逐字节相同",
        "manifest": "reports/firmware/phase8-m8-idle-stack-r4/build_manifest.json",
        "axf": {"path": AXF_REL, "bytes": nbytes(FW / "rt-thread-motor-m1.axf"),
                "sha256": sha(FW / "rt-thread-motor-m1.axf")},
        "map": {"path": MAP_REL, "bytes": nbytes(FW / "rt-thread-motor-m1.map"),
                "sha256": sha(FW / "rt-thread-motor-m1.map")}}
    ident["readback_target_is_this_image"] = True
    if "board_image_before" in ident:
        ident["board_image_before"]["role"] = (
            "只登记下载之前板上是哪一件(上一轮唯一一次回读逐字节证过); 不得当回读比较对象。"
            "旧绑定就是把更旧的 R3 BIN 留在 tools.readback.image 上, 用新镜像原字节跑真实回读函数"
            "立刻 READBACK_HASH_MISMATCH —— 本轮由 PLAN_IMAGE_IDENTITY_CONSISTENCY 在离线拒绝")
    ident["executor"] = {
        "path": "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py",
        "bytes": nbytes(CHAIN), "sha256": sha(CHAIN),
        "changes_in_this_round": [
            "分支与档案按 stages[].scenario 分场(scenario_branches/scenario_profile), 混合结果不再互相推翻",
            "prerequisite_gates: 机械前置未 PROVIDED 的场景链在开端口之前具名跳过"
            "(STAGE_SKIPPED_BY_PREREQUISITE / PREREQUISITE_REFUSAL), 不扣额度、不发命令",
            "first_cause_legs + first_cause_verdict(): 缺腿具名 EVIDENCE_INCOMPLETE",
            "response_branch(): 窗内已见触发针却记成 OBSERVATION_NO_TRIGGER => TRIP_REWRITTEN_AS_NO_TRIGGER",
            "validate_plan(): PLAN_IMAGE_IDENTITY_CONSISTENCY / PLAN_QUOTA_NEED / PLAN_SCENARIO_CHAIN / "
            "PLAN_STUB_BRANCHES / PLAN_PREREQUISITE_GATE / PLAN_FIRST_CAUSE_LEGS",
            "R5: target_channel_claim() + rel_rule_names()(规则名两头从产品源码取, 造不出来就拒绝) + "
            "validate_plan 的 PLAN_SPINUP_GATE_COVERAGE / PLAN_TARGET_CHANNEL;  dispatch 结果新增 "
            "protection_claims / core_protection_verified / flow_complete(流程跑完与通道验证分开报), "
            "台账新增 TARGET_CHANNEL / PROTECTION_CLAIMS / PROTECTION_CLAIM_DOWNGRADED 事件",
            "R5: 机械前置拒绝沿**计划申报的 requires** 传播(S4 被装配门挡下 => S5/S6/S7 一并具名跳过), "
            "prerequisite_refusals 增 via_requires",
            "dispatch 结果新增 scenario_branches / prerequisite_skips / prerequisite_refusals, "
            "阶段条目新增 scenario / branch / detail / first_cause / target_claim / target_why",
            "plan.stub_branches: 离线按链申报分支, 让两场的触发组合能在零设备工装上真实跑四种"],
        "unchanged": "产品 C 源码、镜像与构建产物、下载/回读/复位 argv 语义、既有清理与禁能合同"}
    ident["build_inputs"] = {
        "note": ("镜像的**源码输入字节身份**, 不是哪个提交碰巧是 HEAD。构建时 git HEAD 是 %s, 而该提交的树里 "
                 "rtconfig.h 仍是 IDLE=256(blob 9fe0efdf…, 8689 B); 1024 版第一次进版本库是 %s, 本轮 tip %s "
                 "未再改这个 blob。所以不能把 %s 说成新镜像的构建输入。"
                 % (COMMIT_BUILD_HEAD[:7], COMMIT_INPUT, COMMIT_TIP[:7], COMMIT_BUILD_HEAD[:7])),
        "build_time_head_commit": COMMIT_BUILD_HEAD,
        "image_source_input_commit": COMMIT_TIP,
        "image_source_input_commit_reason": ("tip 的树里本轮镜像的全部源码输入都已入库且逐字节等于构建输入: "
                                             "rtconfig.h 的 blob 自 %s 起未变, diagnosis.c 未动" % COMMIT_INPUT),
        "rtconfig_h": {
            "path": "%s/rtconfig.h" % BSP,
            "local_worktree_crlf": {"sha256": "1927ed1c1cc7a1efebca50b910eb735228a926c218b3e39a1daf799b6fe9a65a",
                                    "bytes": 9304},
            "git_blob_lf": {"sha256": "b66753d6b50b9bf4ab8b46d4a1500e538513a7c872a3d70e84c551007ab4d209",
                            "bytes": 8850},
            "differ_only_by_line_endings": True,
            "verified_by": ("本机复算两条: 工作树字节 sha256=1927ed1c…/9304 B; 把工作树字节的 CRLF 归一成 LF 后 "
                            "sha256=b66753d6…/8850 B, 且等于 git show tip:该文件 的 sha256 => 仅行尾不同, "
                            "不需要因此重编"),
            "blob_at_build_head": {"commit": COMMIT_BUILD_HEAD, "idle_thread_stack_size": 256,
                                   "meaning": "构建时 HEAD 的树里这个文件还是 256, 这正是旧绑定的来源"},
            "line_79": ("#define IDLE_THREAD_STACK_SIZE 1024   /* 候选: armlink 调用图 idle_thread_entry "
                        "=> rt_defunct_execute => rt_object_delete => rt_free => rt_mutex_take… "
                        "Max Depth=668 B > 旧值 256 B */")},
        "diagnosis_c_unchanged": {"path": "%s/applications/diagnosis.c" % BSP,
                                  "sha256": "e1696ec92721c7df9da074c465ac3b705ad451029aa3608b1695c93c7466e557"},
        "config_sync": {"path": "%s/.config" % BSP, "commit": "58904e1",
                        "note": "CONFIG_IDLE_THREAD_STACK_SIZE 同步 256->1024(配置源一致性); 本轮未因此重编"},
        "evidence": "reports/firmware/phase8-m8-idle-stack-r4/build_manifest.json 的 build_inputs + "
                    "host/idle_learn_check.py 的复算行"}
    # s0_binding 要求这两个字段是**可在仓库里 rev-parse 的提交**(git cat-file 证得), 所以不能删;
    # 删掉会让 S0 在 s0_binding 里报 S0_COMMIT_NOT_IN_REPO(V1 实测就是这个)。这里把它们指向
    # 真实承担构建输入的两个提交, 并单独登记"构建时 HEAD"这个不同事实。
    ident["software_input_commit"] = COMMIT_TIP
    ident["tested_image_build_commit"] = COMMIT_INPUT_FULL
    ident["build_time_head_commit"] = COMMIT_BUILD_HEAD
    ident["note"] = ("镜像/AXF/MAP 三件都是本轮构建产物, 逐字节现算并与 build_manifest 相等。"
                     "构建发生时 git HEAD 是 %s, 而**该提交的树里 rtconfig.h 仍是 IDLE=256**"
                     "(blob 9fe0efdf…/8689 B), 1024 那版是工作树里未入库的一行改动; 这行改动在 %s "
                     "第一次入库(blob b66753d6…/8850 B), 本轮 tip %s 未再改它 => 本轮镜像的源码输入"
                     "登记为 tested_image_build_commit=%s(rtconfig 入库提交)+ software_input_commit=%s"
                     "(tip, 含 .config 同步), 而不是旧的 48204fe。已接受的 f5c2fd89… 镜像未被覆盖。"
                     % (COMMIT_BUILD_HEAD[:7], COMMIT_INPUT, COMMIT_TIP[:7], COMMIT_INPUT[:7],
                        COMMIT_TIP[:7]))


def fix_order_and_chains(plan, by_id, known):
    plan["stages"] = [by_id[s] for s in ORDER]
    for i, sid in enumerate(ORDER):
        by_id[sid]["order"] = i
        if sid in CHAIN_OF:
            by_id[sid]["scenario"] = CHAIN_OF[sid]
    req = {"S8": ["S7_restore_equal"], "S9": ["S8_branch_resolved"], "S10": ["S9"],
           "S11": ["S10_expected_refusal_recorded"], "S8b": ["S7_restore_equal"],
           "S9b": ["S8b_branch_resolved"], "S10b": ["S9b"],
           "S11b": ["S10b_expected_refusal_recorded"]}
    for sid, val in req.items():
        by_id[sid]["requires"] = val
    by_id["S8b"]["requires_before_this_round"] = ["S7_restore_equal(与 S8 并列, 不是接在 S8 之后)"]
    # ⑤' 装配就绪门要挂在**每一个**起转/带电阶段上(复审第 2 项: 只挡 S8 不够, S4 学习与 S8b 也起转)。
    SPIN_GATES = {"S4": ["assembly_readiness"],
                  "S8": ["load_means", "assembly_readiness"],
                  "S8b": ["assembly_readiness"],
                  "S10": ["load_means", "assembly_readiness"],
                  "S10b": ["assembly_readiness"]}
    spins = sorted({s["id"] for s in plan["stages"]
                    for st in s.get("steps", []) if st.get("cmd") in ("motor_arm", "motor_start")})
    if spins != sorted(SPIN_GATES):
        raise SystemExit("起转阶段集合变了: 计划里含 motor_arm/motor_start 的阶段是 %s, "
                         "但门只写到 %s —— 先核对再改" % (spins, sorted(SPIN_GATES)))
    for sid, gates in SPIN_GATES.items():
        st = by_id[sid]
        st["prerequisite_gates"] = gates
        st.setdefault("prerequisites", {})["assembly_readiness"] = \
            plan["mechanical_prerequisites"]["assembly_readiness"]
        st["spinup_gate_note"] = (
            "本阶段会发 %s, 所以在**开端口之前**检查 %s; 任一状态不是 PROVIDED 就具名跳过并记 "
            "STAGE_SKIPPED_BY_PREREQUISITE(不扣额度、不发命令)" %
            ("+".join(sorted({x["cmd"] for x in st["steps"] if x.get("cmd") in
                              ("motor_arm", "motor_start")})), "+".join(gates)))
    for sid in ("S8", "S8b"):
        st, suf = by_id[sid], SUF[by_id[sid]["scenario"]]
        for spec in st.get("record_from", []):
            spec["regex"] = rename_groups(spec["regex"], suf)
            spec["as"] = ns(spec["as"], suf)
        if not any(x.get("as") == "prof_at_arm" for x in st["steps"]):
            pos = next(i for i, x in enumerate(st["steps"]) if x.get("as") == "diag_before_arm")
            st["steps"].insert(pos + 1, {"cmd": "diag_profile show", "as": "prof_at_arm", "seconds": 3,
                                         "expect_contains": ["[DIAG] profile id="],
                                         "why": "首因的 profile 腿必须来自**同一会话**: 现场若只在 S5 看过, "
                                                "触发那一代基线就无法与本会话对齐"})
        st["first_cause_legs"] = trip_legs(suf)
        st["first_cause_contract"] = first_cause_contract(by_id[sid]["scenario"], suf)
        # 观察窗键按场分开: IMU 那场喂 SG 的行(REL_SG_SEVERE + imu=0)是复审抓到的实际缺陷
        for stp in st["steps"]:
            if stp.get("as") == "trip_window":
                stp["key"] = "__watch__@imu" if sid == "S8b" else "__watch__"
                stp["channel_key_note"] = (
                    "本场的观察窗夹具是 %s: 规则名与源样本列必须属于**本场目标通道**, "
                    "否则分类只登记 OTHER_SOURCE_TRIGGERED(目标通道未证)" % stp["key"])
        st["target_channel"] = target_channel_block(by_id[sid]["scenario"], suf, known)
        st["per_session_baseline_note"] = (
            "本场事故基线用后缀 _%s 独立记录(bb_session_%s / bb_written_%s / flash_used_before_%s), "
            "与另一场不共用键; 分支也按场存(执行器 scenario_branches) => 一场触发另一场未触发不是矛盾"
            % (suf, suf, suf, suf))
    for sid in ("S9", "S9b"):
        st, suf = by_id[sid], SUF[by_id[sid]["scenario"]]
        for spec in st.get("compare_recorded_gt", []):
            spec["regex"] = rename_groups(spec["regex"], suf)
            spec["keys"] = ns(spec["keys"], suf)
        for spec in st.get("require_frames", []):
            spec["session_key"] = "bb_session_%s" % suf
            for need in spec.get("must_include", []):
                need["event"], need["step_hz"] = "5", "0"
            spec["event_basis"] = EVENT_BASIS
        for step in st.get("steps", []):
            if step.get("new_log_window"):
                step["new_log_window"]["before_key"] = "flash_used_before_%s" % suf
            if step.get("as") == "latch":
                step["key"] = "diag_status@latch@imu" if sid == "S9b" else "diag_status@latch"
        st["first_cause_legs"] = latch_legs(suf)
    for sid in ("S11", "S11b"):
        by_id[sid]["prerequisite_gates"] = []
        by_id[sid]["no_spinup_note"] = "恢复会话内禁止 motor_start(在 forbidden_commands 里), 因此不需要起转门"
    by_id["S10"]["chain_note"] = ("锁存态再 arm 的演示仍属于 SG/ADC 那场: 它依赖**那场**触发后的锁存态, "
                                  "所以排在本场取证之后、下一场开始之前")
    by_id["S10b"]["chain_note"] = "同 S10, 属于 IMU 那场"
    for sid in ("S11", "S11b"):
        by_id[sid]["chain_note"] = "人工恢复只处理本场的锁存, 恢复完即结束该链"


def fix_quota(plan, chain, needs_out):
    needs = chain.derive_quota_needs(plan)
    q = dict(plan["quota"])
    for name, want in needs.items():
        q[name] = want
    for name in ZERO_QUOTA:
        q[name] = 0
    caps = sum(int(s["motion_cap_seconds"]) for s in plan["stages"] if s.get("motion_cap_seconds"))
    q["motor_run_seconds_total"] = max(caps, int(plan["quota"].get("motor_run_seconds_total", 0)))
    q.setdefault("motion_cap_seconds_per_run", 5)
    plan["quota"] = q
    needs_out.update(dict(sorted(needs.items())))
    plan["quota_accounting"] = {
        "derived_from_execution_path": dict(sorted(needs.items())),
        "how_derived": ("validate_plan 的 PLAN_QUOTA_NEED 逐项按真实执行路径推导: 每个 serial_session 阶段扣 1 "
                        "次 port_open; 步骤级 quota / action 的 quota_name / 阶段级 kind 各自累加; 每个含 "
                        "motor_arm 或 motor_start 的阶段各触发一次三条清理 => cleanup_* 各 5。"
                        "申报 < 推导 => 离线 FAIL, 不等现场 QUOTA_EXHAUSTED"),
        "both_chains_trip": {"port_open": needs["port_open"], "cleanup_groups": needs["cleanup_motor_stop"],
                             "motor_run": needs["motor_run"],
                             "note": ("%d 次开口 = S3,S4,S5,S7,S8,S9,S10,S11,S8b,S9b,S10b,S11b; %d 组必要清理 "
                                      "= S4, S8, S8b(arm+start) 与 S10, S10b(锁存态再 arm, 同样建立撤能义务)"
                                      % (needs["port_open"], needs["cleanup_motor_stop"]))},
        "mixed_outcomes": ("一场触发一场未触发也照常跑完: 未触发那条链的取证/拒绝/恢复按分支跳过并具名 "
                           "STAGE_SKIPPED_BY_PROFILE, 既不计 NOT_RUN 也不计通过; 触发那条链照常走自己四阶段"),
        "one_ledger": "dispatch_plan 只建一个 quota_box, 两条链共用; 结束逐键 QUOTA_AUDIT + "
                      "quota_left/quota_used 一起进结果, 用了没申报或余额为负 => 调度非成功",
        "not_expanded": "retry=0 / physical_collision=0 / blackbox_clear=0 / dmm_readings=0 不许运行中临时抬; "
                        "追加就是新的具体批准"}
    plan["quota_note_r4"] = ("本轮按复审第 3 项**重算**而非沿用申报值: port_open -> %d, "
                             "cleanup_motor_stop/disarm/status -> %d(每组三条各一), 其余由 "
                             "derive_quota_needs 推导并与 PLAN_QUOTA_NEED 对账; 申报值仍是上限不是预期用量, "
                             "机械缺项时实际用量会更低" % (needs["port_open"], needs["cleanup_motor_stop"]))


def fix_prose(plan, ident, needs):
    by_id = {s["id"]: s for s in plan["stages"]}
    by_id["S8"]["prerequisites"]["load_means"] = plan["mechanical_prerequisites"]["load_means"]
    by_id["S8"]["prerequisites"]["instrument"] = plan["mechanical_prerequisites"]["instrument_probe"]
    by_id["S8b"]["prerequisites"]["imu_mounting"] = \
        plan["mechanical_prerequisites"]["imu_mounting"]
    plan["load_step"] = {
        "verdict": "REFUSED_BEFORE_SPIN_UP_BY_EXECUTOR",
        "missing": ["可从非旋转端操作的加载作用点及档位/行程",
                    "装配就绪(紧固/螺母防转/导向 + 支座松手保持固定)尚未确认"],
        "asset_now": ("现有 T8 丝杆套件, 标称导程 8 mm; 已按照片装配, 紧固、螺母防转及导向尚待确认; "
                      "轴承/底座目前只能手动固定"),
        "policy": ("不宣称有机械加载方法; 不以允许自然触发代替约束; 不手捏旋转轴、不为凑阈值加危险力、"
                   "不撞两端限位制造堵转"),
        "gate_now_real": ("S8/S10 挂 [load_means, assembly_readiness], S4/S8b/S10b 挂 [assembly_readiness]: "
                          "状态不是 PROVIDED 时该阶段(及其场景链)在开端口之前跳过, 不扣 motor_run, "
                          "不发 arm/start —— 复审第 2 项要的就是每个起转阶段都挡, 不是只挡 S8"),
        "consequence_this_round": ("交付态(两项都不是 PROVIDED)下**没有任何一次起转**: S4 学习与两场激励"
                                   "全部在起转前跳过, 依赖本次学习结果的 S5/S6/S7 沿 requires 一并跳过; "
                                   "实际用量降到 port_open 1 次(只有 S3)、motor_run 0 次"),
        "instrument_deferred": "示波器/电流钳继续暂缓, 不作为起转前置, 也不新增高精度仪器前置(复审本轮口径)"}
    plan["not_provided_summary"] = [
        {"item": "起转装配就绪(紧固、螺母防转、导向 + 支座松手保持固定)", "status": "NOT_CONFIRMED",
         "effect": "S4/S8/S8b/S10/S10b 全部在开端口之前具名跳过 => 交付态零次起转; "
                   "S5/S6/S7 因依赖本次学习结果一并跳过",
         "mechanism": "plan.mechanical_prerequisites.assembly_readiness + 各起转阶段 prerequisite_gates + "
                      "执行器 STAGE_SKIPPED_BY_PREREQUISITE(沿 requires 传播)"},
        {"item": "SG/ADC 可控负载的施加手段与加载作用点", "status": "NOT_PROVIDED",
         "effect": "整条 SG_ADC_controlled_load 链在开端口之前被具名拒绝(不起转、不开口、不扣额度)",
         "mechanism": "plan.mechanical_prerequisites.load_means + stages[].prerequisite_gates"},
        {"item": "示波器/电流钳独立证 STEP 到关断(BUG-008)",
         "status": "NOT_PROVIDED(本轮暂缓, 不作为起转前置, 不新增仪器前置)",
         "effect": "停机事实只能登记为软件接口已接受"},
        {"item": "IRQ 极性实物确认", "status": "hardware-pending(对照表 polarity=not-confirmed)",
         "effect": "protect_ready=NO; 保护链不得称已用真实硬件中断验证"},
        {"item": "IMU(ADXL345 在扩展板上)的固定方式",
         "status": "MOUNTED_TAPE_BY_OPERATOR(操作者 2026-11-01 确认), 刚度未测",
         "effect": "可以做非破坏性冲击/振动演示, 按采样结果判定; 不做精确振动标定, 结论上限是本次未证伪",
         "closed_this_round": "此前登记的缺失项\"IMU 实际固定方式\"已闭合"},
        {"item": "阈值标定(SG 载荷/转速扫描)", "status": "NOT_ESTABLISHED(对照表)",
         "effect": "任何阈值已定的说法都不成立"},
        {"item": "T8 导程", "status": "NOMINAL_8MM_UNCALIBRATED",
         "effect": "标称 8 mm 是套件标称值, 未做实测标定; 不得当作已确认参数核算累计距离"}]
    dsp = plan.setdefault("dispatch_semantics", {})
    dsp["branch_scope"] = ("分支按**场景链**(stages[].scenario)存: scenario_branches/scenario_profile 各自决定, "
                           "真实传输由该链自己的观察窗响应决定(RESPONSE_BRANCH 事件带 scenario), 离线由 "
                           "plan.stub_branches 申报。同一链给出矛盾观察 => RESPONSE_BRANCH_CONFLICT; "
                           "不同链一个触发一个未触发 => 照常继续")
    dsp["chain_order"] = ("每场先取证(S9)、再锁存态拒绝(S10)、再人工恢复(S11), 全部走完才进入下一场"
                          "(S8b→S9b→S10b→S11b); 顺序由 stages 数组的物理顺序决定(执行器按数组迭代)")
    dsp["prerequisite_refusal"] = ("机械前置未到位时该链**整条**在开端口之前跳过并具名(不扣额度、不发命令); "
                                   "汇总行 PREREQUISITE_REFUSAL 与 prerequisite_skips 列出被跳过的阶段, "
                                   "因此保护已演示绝不成立")
    dsp["no_downgrade"] = ("窗内已见触发针却记成到时未触发 => TRIP_REWRITTEN_AS_NO_TRIGGER(具名停止); "
                           "四腿缺一 => EVIDENCE_INCOMPLETE。两者都不是 NO_TRIGGER, 也都不是通过; "
                           "而撤能清理在 exec_session 的 finally 里先于这两个判定完成")
    dsp["flow_vs_verification"] = (
        "两个字段分开报: flow_complete=计划里的阶段都按期望走完(身份核对、启停、清理、恢复都做了); "
        "core_protection_verified=**每一场都是目标传感器通道自己触发并取证齐了**。前者为真不蕴含后者: "
        "别的通道把保护碰响了、或者根本没起转, 都只算流程走完。交付态(装配就绪未确认)必然 "
        "core_protection_verified=False")
    dsp["target_channel"] = (
        "每场在 stages[].target_channel 里申报自己认哪些规则名(产品 rel_reason_t 里的名字)以及哪一列"
        "源样本必须非零。规则名不匹配 => OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN); 规则名对但"
        "该列为 0 => TARGET_RULE_BUT_SOURCE_FIELD_ZERO。两种都照常撤能、照常取证(含 event=5 停后帧读回), "
        "但 protection_claims 记为未证, 不计入 core_protection_verified")
    dsp["one_approval"] = dsp.get("one_approval", "")
    plan["claim_boundaries"] = {
        "delivered_state_skips": ("本包交付态会跳过 SG/ADC 激励链(加载手段未提供)以及所有起转阶段"
                                  "(装配就绪未确认)。因此整包完成**不等于**核心保护功能全部通过, "
                                  "也不等于学习已通过"),
        "flow_complete_true_means": "期望序列都跑到了各自的终态(含具名跳过), 不表示任何一次现场触发",
        "core_protection_verified_true_means": ("两场都抓到**本场目标通道**的规则名(REL_SG_SEVERE/"
                                                "REL_ADC_SEVERE/REL_FUSED_SEVERE 或 REL_IMPACT)、该通道"
                                                "源样本非零、首因四腿齐、且新 session 读回 event=5 停后帧"),
        "never_from_this_package": ["阈值的物理标定", "STEP 到关断的物理证明(BUG-008)",
                                    "IRQ 极性已用实物确认", "栈 1024 足够", "HardFault 根因已闭"],
        "operator_confirmation_needed": ["装配就绪 4 项逐条确认(本 Agent 不代填)",
                                         "第一次被批准的短移动里核实\"顺时针=向右\""]}
    plan["first_cause_overall"] = {
        "rule": ("每一路首因都要在同一次运行内四件对齐: 触发规则与代际 -> 源样本与阈值 -> 本轮提交的 profile "
                 "-> 本场新日志 session 与实际停后帧(event=5, step_hz=0)。缺任何一件 => "
                 "EVIDENCE_INCOMPLETE(具名), 既不降级成 NO_TRIGGER, 也不算通过"),
        "machine_checked_by": "m8r3_serial_chain.first_cause_verdict() + stages[].first_cause_legs",
        "not_a_substitute": ["safety_trip", "fault_reset 之后自动恢复的装态", "diag_mode 切换本身"],
        "manual_recovery": "人工恢复在 S11/S11b: fault_reset MANUAL_CLEAR + system_selftest + 就绪复核; "
                           "恢复会话内禁止 motor_start"}
    plan["status"] = "BOUND_NOT_EXECUTED"
    plan["executor_reused"] = ("%s(sha256 %s…, 完整字节身份见 identity_binding.executor)"
                               % (ident["executor"]["path"], ident["executor"]["sha256"][:12]))
    plan["changes_this_round"] = [
        "① S0/下载/回读统一到本轮新镜像 %s 与新 AXF %s; 删除更旧 R3 BIN(00608ec9…)的绑定"
        % (ident["image"]["sha256"][:8], ident["image"]["axf"]["sha256"][:8]),
        "② 顺序改为两场各走完整链(S8→S9→S10→S11 → S8b→S9b→S10b→S11b), 分支与事故基线按场存",
        "③ 额度按真实执行路径重算(port_open %d, 必要清理 %d 组), 一本总账, 覆盖四种触发组合"
        % (needs["port_open"], needs["cleanup_motor_stop"]),
        "④ 首因合同变成可执行四腿(first_cause_legs + first_cause_verdict), 缺证 EVIDENCE_INCOMPLETE, "
        "触发不得降级为 NO_TRIGGER",
        "⑤ 机械前置更新为现有 T8 完整滑台(未连接/导程待确认)并接成起转前的真门; 示波器继续暂缓, "
        "不新增高精度仪器前置",
        "R 登记: 不再把 48204fe 当新镜像构建输入; rtconfig.h 登记本机 CRLF 1927ed1c… 与远端 LF b66753d6… "
        "双身份(仅行尾不同, 不需重编)",
        "C-02 停后帧事件码 9 -> 5(FAULT_MULTI_SOURCE), 依据见取证会话的 event_basis; 夹具与 R3 自检由 "
        "host/apply_c02_event5_fixtures.py 与 m8r3_field_selftest.py 的 FS69/FS76..FS79 覆盖",
        "语言修正: 不称 1024-668=356 B 为已验证安全余量; 264 B 是可达路径固定帧下界, 668 B 是链接器给出的"
        "更深静态链, 两者口径不同且并存",
        "R5-① 目标通道分类: 每场申报 target_channel(规则名必须来自产品 rel_reason_t), IMU 场只认 "
        "REL_IMPACT 且 imu 列非零; SG/ADC 场认 REL_SG_SEVERE/REL_ADC_SEVERE/REL_FUSED_SEVERE 且 sg/cur "
        "非零。规则名不对或源样本为 0 => 具名 OTHER_SOURCE_TRIGGERED / TARGET_RULE_BUT_SOURCE_FIELD_ZERO, "
        "撤能取证照做但不计入 core_protection_verified; 观察窗与锁存夹具按场分开(__watch__@imu、"
        "diag_status@latch@imu)",
        "R5-② 机械事实更新 + 起转门覆盖: 改成\"现有 T8 丝杆套件, 标称导程 8 mm; 已按照片装配, 紧固、"
        "螺母防转及导向尚待确认\", 新增 assembly_readiness(blocks_motion) 并挂到 S4/S8/S8b/S10/S10b "
        "**每一个**起转阶段; 拒绝沿 requires 传播到 S5/S6/S7; 交付态因此零次起转"]
    plan["corrections_from_review_r4"] = {
        "package_5": ["回读仍认旧镜像(①)", "两场的取证/拒绝/恢复顺序与分场基线(②)",
                      "额度按真实路径与混合结果覆盖(③)", "首因四腿与不得降级(④)",
                      "机械前置真门 + 示波器暂缓(⑤)"],
        "registration_2": ["48204fe 不是新镜像的构建输入(该提交的树里 rtconfig 仍是 256)",
                           "rtconfig 本机 CRLF 1927ed1c… / 远端 LF b66753d6… 双身份"],
        "language_1": "264 B 与 668 B 不冲突; 356 B 不是已验证安全余量",
        "c02_from_c01_receipt": "停后帧事件码由 9(FAULT_SOFT) 更正为 5(FAULT_MULTI_SOURCE)"}
    plan["corrections_from_review_r5"] = {
        "flow_vs_target_channel": (
            "复审实测: IMU 场喂 REL_SG_SEVERE + imu=0 仍过首因判据, 而且现有 IMU 正控夹具就是这份数据。"
            "现在分两层: 流程(flow_complete)与目标通道(core_protection_verified), 后者要求每场"
            "抓到**本场规则名**且该通道源样本非零; 其他来源触发仍撤能、仍取证, 但登记为"
            "\"其他来源触发/目标通道未证\", 不混算成功"),
        "stale_mechanical_facts": (
            "包里的\"完整滑台、尚未连接、导程待确认\"已过期, 按操作者口径改为\"现有 T8 丝杆套件, "
            "标称导程 8 mm; 已按照片装配, 紧固、螺母防转及导向尚待确认\"; 并把装配就绪门放到每个"
            "起转阶段之前(S4 学习、S8/S8b 激励、S10/S10b 锁存态再 arm)"),
        "scope_of_this_round": "只改计划、结果分类与对应少量用例; 未改 C、未重编、未重跑历史清单、零设备动作"}
    plan["stack_margin_wording"] = {
        "metrics_are_different": True,
        "reachable_path_fixed_frame_lower_bound_bytes": 264,
        "armlink_static_max_depth_bytes": 668,
        "configured_idle_stack_bytes": 1024,
        "not_claimed": ["已验证安全余量(1024-668=356 B 只是算术差, 不是余量的证明)",
                        "1024 已足够(未上板取新水位, 未做栈填充实测)",
                        "668 或 264 是已验证上界(668 是链接器静态最深合计, 264 是可达路径的固定帧下界)"],
        "what_is_proven": ("静态调用图 idle_thread_entry ⇒ rt_defunct_execute ⇒ rt_object_delete ⇒ rt_free ⇒ "
                           "rt_mutex_take… 的 Max Depth=668 B > 旧配置 256 B => 旧值不足是已证的; 新镜像 "
                           "ZI +768 B 与 256->1024 的增量一致 => 改动确实进了这个 Target"),
        "still_open": "1024 是否够 = NOT_VERIFIED(需要板上水位或栈填充实测)"}
    plan["hardware_actions_this_round"] = []
    plan["device_actions_statement"] = ("本轮零设备动作、零构建: 只改计划数据与执行器/检查器; 产品 C 源码与 "
                                        "BIN/AXF/MAP 未动")
    plan["protection_status_still"] = {
        "ADC": "DEGRADED cal=THEORETICAL", "protect_ready": "NO",
        "threshold_validation": "NOT_ESTABLISHED", "rel_cov": "NOT-ACTIVE",
        "limit_inputs": "DISABLED_BY_CONFIG",
        "meaning": "本包只准备执行路径; 以上任一项都不因包的绑定而改变, 也不得以离线证明代替现场结论"}


def fix_fixtures(plan):
    rows = json.loads((FIELD / "rows.json").read_text(encoding="utf-8-sig"))
    plan["fixtures_provenance"] = [
        {"file": "reports/reviews/phase8-m8-idle-stack-r4/field/rows.json",
         "bytes": nbytes(FIELD / "rows.json"), "sha256": sha(FIELD / "rows.json"),
         "keys_total": len(rows["commands"]),
         "note": ("含本轮两处更正: flash_dump 停后帧 event 9->5 并补 source_shape 来源分级(没有板上采集); "
                  "新增 ADC68/SG8 过量采样健康键。都是夹具, 不构成设备证据")},
        {"file": "两份 rows.json 的 R5 目标通道键",
         "keys": ["__watch__@imu(r=REL_IMPACT, imu=3789)", "diag_status@latch@imu(同场锁存视图)",
                  "__watch__@imu_zero(元负控专用: REL_IMPACT 但 imu=0)"],
         "added_by": "host/apply_target_channel_fixtures.py",
         "kind": "source_shape",
         "note": ("形状来自 A/diagnosis.c:469-475 的 REL TRIP/rel LATCH 三行与 A/diag_relative.h 的 "
                  "rel_reason_t; 数值全部合成, **没有任何板上采集给出过 REL_IMPACT 的触发行**。"
                  "IMU 那场原来吃的是 SG 的行(r=REL_SG_SEVERE、imu=0) —— 复审抓到的就是这个")},
        {"file": "reports/reviews/phase8-m8-relative-protection-r3/field/rows.json",
         "bytes": nbytes(R3_ROWS), "sha256": sha(R3_ROWS),
         "source": "同一更正落在仍被引用的 R3 副本",
         "not_changed": "归档运行输出 reports/reviews/phase8-m8-relative-protection-r3/offline/psrun-*/** "
                        "与 M5 软件触发原件(event=9)原样保留 => 不是全局替换"},
        {"file": "reports/reviews/phase8-m8-idle-stack-r4/field/needles.json",
         "bytes": nbytes(FIELD / "needles.json"), "sha256": sha(FIELD / "needles.json"),
         "note": "本轮未改 needles.json"}]
    plan["c01_intake"] = {
        "dir": "reports/reviews/phase8-m8-parallel-protection-ready",
        "zip": "NS800_C01_Delivery_20261101.zip",
        "zip_sha256": "3eb4c1d335b08fe39bf83e35aa34865d85bb778521fe0cd9e73e1d331a1bc893",
        "files_byte_identical": [
            {"name": "C01_DELIVERY_RECEIPT.md", "bytes": 6689,
             "sha256": "3493908113760af0a124c5cc9983385ce05fc421dd56e8fb6b3a850856b4913f"},
            {"name": "SHA256SUMS.txt", "bytes": 255,
             "sha256": "1b9c5a92936ebd2d0b607fb61406cf4827d22fce16e334d8b5bf2c38685a0842"},
            {"name": "results_local.json", "bytes": 11478, "cr_bytes": 452,
             "sha256": "ecf9466e0e3b097b849936360aff83fbfd1cf7749b73303ff8f03c66bf620fb0"},
            {"name": "verify_c01.py", "bytes": 6845,
             "sha256": "ccc3db0a3cc1ff2903822c6d77987d2c32c97f45c529a442994079e2ac1711e2"}],
        "policy": ("原样纳入, 不覆盖、不重新生成原始结果、不改脚本内绝对路径(回执 §3 已声明复跑需按检出路径"
                   "改 FIELD/OUT 两行)"),
        "todo_items_taken_over": ["C-02 停后帧事件码(本轮已修, 见各会话 event_basis)",
                                  "must_min 并入已有自检(R3 自检 FS76, 34 例含 4 个元负控)",
                                  "ADC68/SG8 健康夹具(rows.json 新键, FS76 直接用它)"
                                  ]}


def fix_r3_status(plan, chain):
    """R5② 把每个起转阶段都挡、R5① 要求声明目标通道之后, **当前执行器**对 R3 那两份历史计划的
    离线结论变了。它们是已执行轮次的输入, 本轮不重跑也不回写; 但"回不去"这件事必须实测登记,
    不能靠一句"历史件"带过。数字由这份执行器当场重算, 不是抄来的。"""
    app = str(ROOT / BSP / "applications")
    rows_r3 = json.loads(R3_ROWS.read_text(encoding="utf-8"))
    items = []
    for fn in ("plan.json", "plan_continuation_s2.json"):
        path = R3_ROWS.parent / fn
        p = json.loads(path.read_text(encoding="utf-8"))
        lines, bad, _n = chain.validate_plan(p, rows_r3, app)
        names = sorted({l.split("|")[0] for l in lines if "|FAIL|" in l})
        detail = [l for l in lines if "|FAIL|" in l and l.split("|")[0] in
                  ("PLAN_SPINUP_GATE_COVERAGE", "PLAN_TARGET_CHANNEL")]
        items.append({"file": "reports/reviews/phase8-m8-relative-protection-r3/field/" + fn,
                      "sha256": sha(path), "bytes": nbytes(path),
                      "checks": len(lines), "fail": bad, "fail_names": names,
                      "this_rounds_two_gates": [d[:200] for d in detail]})
    plan["r3_historical_plans_under_current_executor"] = {
        "measured_by": "本构建脚本当场调用当前执行器的 validate_plan(数字是复算的, 不是登记来的)",
        "items": items,
        "meaning": ("R3 的两份计划是在 R5 两项补正**之前**定稿并执行的: 它们没有 assembly_readiness "
                    "起转门、也没有 target_channel 声明, 所以现在这套执行器会给它们 fail=%d。"
                    "本轮不重跑这两份计划、也不回写它们(它们绑定着已经发生过的轮次与已入库的原始记录); "
                    "如果以后要复用, 必须先按同样两项补正并重新绑定, 不能拿旧计划当成“现在这套判据也能跑”。"
                    % (items[0]["fail"] if items else -1)),
        "not_done_here": ["不修改 R3 计划字节", "不重跑 R3 轮次的清单", "不把 fail 说成 PASS"]}


def build(plan, chain):
    ident, tools = plan["identity_binding"], plan["tools"]
    by_id = {s["id"]: s for s in plan["stages"]}
    off = [s for s in ORDER if s not in by_id] + [x["id"] for x in plan["stages"]
                                                  if x["id"] not in ORDER]
    if off:
        raise SystemExit("阶段集合与目标顺序不一致: %s" % off)
    known = sorted(chain.rel_rule_names(ROOT / BSP / "applications"))
    fix_identity(plan, ident, tools)
    plan["mechanical_prerequisites"] = prereq_block()   # 门必须先有包级条目, 起转阶段才能挂
    fix_order_and_chains(plan, by_id, known)
    needs = {}
    fix_quota(plan, chain, needs)
    fix_prose(plan, ident, needs)
    fix_fixtures(plan)
    fix_r3_status(plan, chain)
    plan["download_payload_swap"] = {
        "swaps": 1,
        "payload_axf": AXF_REL,
        "assertion": ("下载 argv 的最后一个参数必须等于 identity_binding.image.axf.path(由 validate_plan 的 "
                      "PLAN_IMAGE_IDENTITY_CONSISTENCY 判), 载荷就是本轮新 AXF, 不是板上已接受的那一件"),
        "history": "相对 R3 轮换了载荷(同一 Target 名, 源码只差 rtconfig 的一行); 旧件路径与旧散列都不出现在"
                   "本包任何执行字段里"}
    plan["stub_branches"] = {SC_A: "trip", SC_B: "trip"}
    plan["verification_matrix_expected"] = {
        "run_by_host": "host/verify_four_branch_matrix.py(零设备工装, 真实执行器 + 真实计划与夹具)",
        "prerequisite_states": {
            "delivery_state": "assembly_readiness=NOT_CONFIRMED 且 load_means=NOT_PROVIDED(本包交付态)",
            "load_gap_only": "assembly_readiness=PROVIDED, load_means=NOT_PROVIDED",
            "all_provided": "两项都 PROVIDED(操作者逐条确认之后才允许写进现场单)"},
        "four_branch_combinations": [
            {"stub_branches": {SC_A: "trip", SC_B: "trip"},
             "prerequisite": "all_provided",
             "executed_chain_stages": ["S8", "S9", "S10", "S11", "S8b", "S9b", "S10b", "S11b"],
             "port_open_used": 12, "cleanup_groups_used": 5,
             "protection_claims": {SC_A: "VERIFIED", SC_B: "VERIFIED"},
             "core_protection_verified": True},
            {"stub_branches": {SC_A: "trip", SC_B: "no_trigger"}, "prerequisite": "all_provided",
             "skipped_by_profile": ["S9b", "S10b", "S11b"],
             "protection_claims": {SC_A: "VERIFIED", SC_B: "NO_TRIGGER"},
             "core_protection_verified": False,
             "note": "混合结果一: 不矛盾, 照常跑完; 未触发那条链不称已验证"},
            {"stub_branches": {SC_A: "no_trigger", SC_B: "trip"}, "prerequisite": "all_provided",
             "skipped_by_profile": ["S9", "S10", "S11"],
             "protection_claims": {SC_A: "NO_TRIGGER", SC_B: "VERIFIED"},
             "core_protection_verified": False, "note": "混合结果二"},
            {"stub_branches": {SC_A: "no_trigger", SC_B: "no_trigger"}, "prerequisite": "all_provided",
             "skipped_by_profile": ["S9", "S10", "S11", "S9b", "S10b", "S11b"],
             "core_protection_verified": False,
             "note": "都未触发: 取证链按分支跳过, 不得写成保护已验证"}],
        "classification_cases": [
            {"name": "imu_channel_positive", "setup": "S8b 观察窗键 __watch__@imu(REL_IMPACT, imu=3789)",
             "expect_claim": "VERIFIED"},
            {"name": "imu_fed_sg_rows", "setup": "把 S8b 的观察窗键换回 __watch__(REL_SG_SEVERE, imu=0)",
             "expect_claim": "OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)",
             "expect": ["撤能与取证照做(S9b 仍跑、仍读 event=5 停后帧)",
                        "core_protection_verified=False", "flow_complete 仍可为真"]},
            {"name": "imu_rule_ok_but_zero", "setup": "S8b 观察窗键 __watch__@imu_zero"
                                                       "(REL_IMPACT 但 imu=0)",
             "expect_claim": "TARGET_RULE_BUT_SOURCE_FIELD_ZERO(trip_imu_imu)",
             "expect": ["证明 source_nonzero_keys 这道腿在起作用(元负控)"]},
            {"name": "sg_fed_impact_rows", "setup": "把 S8 的观察窗键换成 __watch__@imu(REL_IMPACT)",
             "expect_claim": "OTHER_SOURCE_TRIGGERED(TARGET_CHANNEL_UNPROVEN)",
             "why": "SG 通道不因一次冲击判定而算已验证"},
            {"name": "gate_removed_still_admits_sg_data",
             "setup": "摘掉 target_channel_claim 的规则名判定(或删掉 target_channel 声明)",
             "expect": "IMU 场喂 SG 行重新被判成通过 => 说明判据确实存在(负控必须 fire)"}],
        "mechanical_gap_cases": [
            {"name": "load_means=NOT_PROVIDED(只缺加载, 装配已确认)",
             "expect": "SG_ADC 整链在开端口之前跳过, S4 学习与 S8b 照常起转: port_open 用量 8, "
                       "motor_run 用量 2(剩 1)"},
            {"name": "assembly_readiness=NOT_CONFIRMED(本包交付态)",
             "expect": "S4/S8/S8b/S10/S10b 全部在开端口之前跳过, 且 S5/S6/S7 沿 requires 一并跳过 => "
                       "port_open 用量 1(只有 S3), motor_run 用量 0, 台账里没有任何 motor_start"},
            {"name": "两场均缺(交付态实测)",
             "expect": "prerequisite_skips 覆盖 S4..S11b 全部运动与依赖阶段; "
                       "prerequisite_refusals 的 gates 分别具名 load_means/assembly_readiness; "
                       "protection_claims 对被跳过的场记 SKIPPED_BEFORE_SPIN_UP(PREREQUISITE)"}],
        "evidence_incomplete_case": "摘掉任一条腿(如源样本行) => code=EVIDENCE_INCOMPLETE 且 "
                                    "status!=OBSERVATION_NO_TRIGGER, 同时 disarm=CONFIRMED_DISABLED"}
    return needs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = PLAN.read_text(encoding="utf-8")
    doc = json.loads(text)
    m = re.match(r"\{[^\S\n]*\n([ \t]+)\"", text)
    indent = len(m.group(1)) if m else 1
    needs = build(doc, load_chain())
    body = json.dumps(doc, ensure_ascii=False, indent=indent) + ("\n" if text.endswith("\n") else "")
    print("DERIVED_QUOTA|%s" % json.dumps(needs, sort_keys=True))
    print("EXECUTOR|%s|%d" % (sha(CHAIN), nbytes(CHAIN)))
    print("IMAGE|%s|%d" % (doc["identity_binding"]["image"]["sha256"][:16],
                           doc["identity_binding"]["image"]["bytes"]))
    print("PLAN_BYTES|%d -> %d|indent=%d" % (len(text.encode("utf-8")), len(body.encode("utf-8")), indent))
    if a.check:
        print("APPLY|dry_run=yes|changed=%s" % (body != text))
    else:
        PLAN.write_text(body, encoding="utf-8", newline="\n")
        print("APPLY|written=yes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
