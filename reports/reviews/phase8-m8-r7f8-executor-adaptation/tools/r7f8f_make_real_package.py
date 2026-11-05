# -*- coding: utf-8 -*-
"""R7F8F 最终现场包作者: 产出 real plan + real rows(离线, 0 设备动作)。

来源纪律(任务书 §4/§5/§6, A 包 NS800_R7F8F_Repair_And_Field_20261105):
  - 每一个数字都带一句出处; 能在本仓库产品源码里核到的行形状才写字段判据。
  - 本轮**不下载/不回读/不复位/不动分析仪/不断送电**, 所以 plan 里只有一个 serial_session,
    download/readback/reset 额度全 0; 镜像身份只作**参照**(inherited), 不当成本轮动作结果。
  - 拿不准字段形状的查询步只断言"命令回显 + 拿到完整回包"(不造字段名), 这些步骤列在
    `field_assertion_gaps` 里, 交给 A 判是否足够 —— 宁缺勿造。

用法: python tools/r7f8f_make_real_package.py --repo <仓库根>
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
PLAN_REL = ROUND + "/field/plan_r7f8f_2369_real.json"
ROWS_REL = ROUND + "/field/rows_r7f8f_real.json"
# 在板镜像的**归档原件**(R7F7-F1 唯一一次定长回读已证逐字节在板; 摘要由本工具当场重算)
IMAGE_REL = ("reports/firmware/phase8-m8-r7d1-ard4b-commit-txn/"
             "rt-thread-motor-m1-candidate.bin")
# 镜像身份**只从盘上原字节算**; 算不出来就拒绝产出, 不留任何手写摘要。
# (R7F7 轮登记的短前缀 23691e8d… 只是人眼对照用, 完整值一律由本工具当场重算并打印。)
IMAGE_SHA = ""
IMAGE_BYTES = 0
AXF = {}                      # 由 _inherited_tools() 从 R7F7 定稿计划当场读取(见该函数)

# ---------------------------------------------------------------- 产品行形状(全部标出处)
def job(jid, st, adc=None, sg=None, want=8, lo=250, hi=350, d=1, owner=None, cred=None,
        why=None):
    """diag_relative.c:2042 两行形状; why/owner/cred 由状态推导:
    RUNNING  -> nj.why=REL_JOB_WHY_RUN(:1637) + d_cap_owner=REL_CAP_BY_JOB(:1643) => owner=2
    CANCEL_PENDING -> :1674 "CANCEL_REQUESTED"
    CANCELLED      -> :1719 终态 why="CANCELLED", owner 归还 0
    DONE          -> :1760 终态 why="COMPLETE", cred=1/1
    """
    shape = {"RUNNING": ("RUNNING", 2, "1/0"), "CANCEL_PENDING": ("CANCEL_REQUESTED", 2, "1/0"),
             "CANCELLED": ("CANCELLED", 0, "1/0"), "DONE": ("COMPLETE", 0, "1/1"),
             "FAILED": ("WINDOW_BROKEN", 0, "1/0"), "IDLE": ("IDLE", 0, "1/0")}[st]
    why = shape[0] if why is None else why
    owner = shape[1] if owner is None else owner
    cred = shape[2] if cred is None else cred
    adc = want if adc is None else adc
    sg = want if sg is None else sg
    return ["[DIAG] job id=%d st=%s why=%s owner=%d cred=%s" %
            (jid, st, why, owner, cred),
            "[DIAG] job n=%d/%d want=%d win=%d..%d dir=%d" % (adc, sg, want, lo, hi, d)]


def done_full(jid, want=8, lo=250, hi=350, d=1):
    """完整 DONE: 状态两行 + 候选统计/阈值/结果三行(diag_relative.c:2056/2068/2074 一带,
    只在 state==DONE 且 cand_valid 时打)。阈值行按 R7D1 已核对形状给 FIELD_UNVERIFIED。"""
    return job(jid, "DONE", want=want, lo=lo, hi=hi, d=d) + [
        "[DIAG] job data adc[280..320]n%d nz2 sg[60..80]n%d seq101..%d" % (want, want, 100 + want),
        "[DIAG] job thr tier=1 dev=30/60 drop=15/40 FIELD_UNVERIFIED",
        "[DIAG] job DONE result; cred zero=1 learn=1 commit is separate"]


# 静止那一行不再手拼: F21 起 motor_status 的行一律取归档真实字节(MOT_REAL_*),
# 只有"武装但零速"这种现网没抓过的形状才由下面的 MOT_ARMED_IDLE 声明形状 + 真实披露行拼出。
# arm 之后、start 之前: 已武装但零目标(产品行形状同 motor.c:2455-2504; 使能侧 ENN LOW/0)。
MOT_ARMED_IDLE = ["[MOT] state=0 name=OTHER",
                  "[MOT] cur_hz=0 tgt_hz=0 accel=2000 decel=2000 dir=1",
                  "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
                  "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
                  "[MOT] diag_off_confirmed=NO"]
# 爬坡中的那一行: **数值 state 必须是 ACCEL=1**(motor.c 的枚举), 上一版用
# MOT_CRUISE[0].replace("CRUISE","ACCEL") 得到的是 "state=2 name=ACCEL" —— 名字改了、数值没改,
# 于是轮询的 ramping 门(按 state 数值判"还在爬坡")看不到 ACCEL, 第一次尝试就直接
# PROFILE_NOT_RUNNING 停止。这是本工具自己的缺陷, 按枚举逐字重写。
MOT_ACCEL = ["[MOT] state=1 name=ACCEL",
             "[MOT] cur_hz=210 tgt_hz=300 accel=2000 decel=2000 dir=1",
             "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
             "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
             "[MOT] diag_off_confirmed=NO"]
MOT_CRUISE = ["[MOT] state=2 name=CRUISE", "[MOT] cur_hz=300 tgt_hz=300 accel=2000 decel=2000 dir=1",
              "[MOT] armed=1 drv_en_request=1 health=OK", "[MOT] drv_request=HIGH pad=1",
              "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK",
              "[MOT] diag_off_confirmed=NO"]
# motor.c:2455-2504 是字段清单; 1328/1415/1698 是三条成功/请求行(逐字取自主线代码)。
ARM_OK = ["[MOT] ARMED (drv_en request=HIGH readback verified, ENN feedback=LOW confirmed)"]
DISARM_OK = ["[MOT] DISARM (动作已做: STEP DONE / 许可 LOW verified / ENN feedback=OK)"]
STOP_OK = ["[MOT] stop requested: target_hz=0 (软件命令值), STEP 实际关断结果见 "
           "stop_result(当前=OFF_CONFIRMED)"]
DIR_OK = ["[MOT] dir=1 requested"]
TARGET_OK = ["[MOT] target=300 requested"]
START_OK = ["[MOT] step pwm enabled"]
# safety_thread.c:306 打的是 ST_INPUT_N 路数; 本项目 SAFETY_LIMIT_INPUTS_ENABLED=RT_FALSE
# (project_board.h:51) => st_inputs[] 只剩 TMC_DIAG 一路 => **1 路**, 不是上一版写的 0 路。
IRQ_CLOSED = ["[SAFETY] IRQ gate CLOSED - 活动安全输入(1 路)无软件响应路径"]
IRQ_OPEN = ["[SAFETY] IRQ gate OPEN: 1 active protection input(s) registered",
            "[SAFETY] IRQ enabled mask=0x00"]
IRQ_STILL = ["[SAFETY] IRQ gate stays CLOSED"]                                 # :290
# safety_thread.c:388-392 的**整行形状**(值按 §4 现场事实: 门 CLOSED / 极性未确认)。
# 上一版把 :392 的"值"和括号里的提示拼成了一条产品不会打印的行, 这轮按源码逐字拆开。
IRQ_STATUS_ARMED = ["[SAFETY] irq gate   = CLOSED (polled only)",
                    "[SAFETY] polarity   = NOT confirmed (HARDWARE-PENDING unless CONFIRMed)"]
# supervisor.c:85/90/128 三条是本轮实读到的形状(字段值按 §4 工况给合成值)。
# F11 现场首错之后的更正(2026-11-05): 前态 system_status 的行**改成板子真字节**, 不再由我照
# 源码形状手工拼 —— 上一版我拼的三格里有两格与现场实拍不符(irq_gate 大小写、protect_ready 的实值),
# 更要紧的是把 mask=0x18 记在了 **TMC 写门**那一行, 而产品把它打在 **Motor 门位评估**那一行
# (supervisor.c:126) —— 假端口因此永远测不到"披露行被判成业务拒绝"这件事。
_F11_CAP = pathlib.Path(__file__).resolve().parents[1] /     "proof/r7f8f_field_f11_sys_pre.rx.bin"


def _observed_sys_ready():
    """从 2026-11-05 现场那份 983 B 原始捕获里取响应行(去掉首行回显与结尾提示符)。"""
    if not _F11_CAP.is_file():
        raise SystemExit("REFUSED|现场捕获不在盘上(%s): 前态行只能照实拍写, 不许凭记忆重造"
                         % _F11_CAP.name)
    ls = [l for l in _F11_CAP.read_bytes().decode("utf-8").splitlines() if l.strip()]
    ls = [l for l in ls if l and l.strip() != "msh >" and l.strip() != "system_status"]
    if not (ls[0] == "=== SYSTEM STATUS ==="
            and any(l.startswith("[Motor] arm gate fail mask=") for l in ls)):
        raise SystemExit("REFUSED|捕获头/披露行与预期不符, 不敢当成前态形状写进行表")
    return ls


SYS_READY = _observed_sys_ready()
# 产品无条件打的"当前会被拒"门位评估行(supervisor.c:126)必须当**状态披露**而不是业务拒绝。
# F19 在这里写的是一条通配 hex 的整行模式(任意 mask 都放行) —— F21 按 A 的要求取消它,
# 改由下面的逐步契约表给"这一步那几个 mask"各一条整行锚定模式。


# ---- F21(A 第 1 点): 逐步 arm gate mask 契约的**归档真实字节**来源 --------------------------
# 上一版只对 system_status 前态做了"照实拍写", motor_status 的行还是我按源码形状手拼的六行 —— 于是
# 离线工装永远看不见板上那 24 行里的重复披露行, F20 的第二个首错(motor_status 的 0x18 被当业务拒绝)
# 就是这么漏掉的。这里把三种 mask 形状的**归档原件**读进来当行表内容, 取不到原件就拒绝生成。
_MASK_SAMPLES = {
    # mask -> (仓内归档路径, 字节数, sha256 前 12, 该形状在仓内的归档份数)
    "0x18": ("reports/reviews/phase8-m8-r7f7-newimage-static/field/"
             "field-20261104T201458Z-r7f7/15_mot_final.rx.bin", 1128, "f0550d313164",
             "同一份 1128 B/f0550d313164 在 5 次归档会话 + F20 本会话里逐字节相同"),
    "0x0": ("reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/"
            "field-20261104T101046Z/S_S1/12_mot_open.rx.bin", 648, "10807f63f02a",
            "同一份 648 B/10807f63f02a 在 9 次归档会话里逐字节相同"),
}


def _observed_mot_rows(mask):
    """取某个 mask 形状的归档真实响应行(去命令回显与 msh 提示符), 并核它确实是这个 mask。"""
    rel, nbytes, sha12, note = _MASK_SAMPLES[mask]
    p = pathlib.Path(__file__).resolve().parents[4] / rel
    if not p.is_file():
        raise SystemExit("REFUSED|归档真实字节不在盘上(%s): mask=%s 的行只能照实拍写, "
                         "不许凭记忆重造" % (rel, mask))
    b = p.read_bytes()
    if len(b) != nbytes or hashlib.sha256(b).hexdigest()[:12] != sha12:
        raise SystemExit("REFUSED|归档件身份与登记不符(%s: %d B sha=%s…)"
                         % (p.name, len(b), hashlib.sha256(b).hexdigest()[:12]))
    ls = [l for l in b.decode("utf-8").splitlines() if l.strip()]
    ls = [l for l in ls if l.strip() not in ("msh >", "motor_status")]
    hits = [l for l in ls if l.startswith("[MOT] arm_gate_mask=")]
    want = "[MOT] arm_gate_mask=%s" % mask
    if not any(h == want + " REFUSED" or h == want + " ALL-PASS" for h in hits):
        raise SystemExit("REFUSED|归档件里的披露行%s 与预期 mask=%s 形状不符, 不敢写进行表(%s)"
                         % (hits, mask, note))
    return ls


MOT_REAL_18 = _observed_mot_rows("0x18")     # 写前(apply 之前, IRQ 未开)的 24 行原件
MOT_REAL_00 = _observed_mot_rows("0x0")      # 保护就绪 + 配置已下发之后的 13 行原件
# mot_dir 那一步在产品上是 dir=1(motor_dir 1 已生效), 而这两份原件都是 dir=0 时抓的。
# 只改这一个数值, 其余逐字保留 —— 它是**在真实行上做一次已申报的值翻转**, 登记在 derived_shape_rows。
MOT_REAL_00_DIR1 = [(l.replace("decel=2000 dir=0", "decel=2000 dir=1")
                     if l.startswith("[MOT] cur_hz=") else l) for l in MOT_REAL_00]
if MOT_REAL_00_DIR1 == MOT_REAL_00:
    raise SystemExit("REFUSED|dir 值翻转没命中任何行: 归档行的频率形状变了, 不敢继续派生")
# 披露行的另一种 mask: 运行中(Safety=RUN => bit1)与 apply 之后(仅 bit3)。这两份只取**披露行本身**
# (其余字段按本包工况由既有声明形状给), 所以它们是"真实形状 + 本包工况值", 不是整包实拍。
MOT_MASK_LINE = {"0x0": "[MOT] arm_gate_mask=0x0 ALL-PASS",
                 "0x2": "[MOT] arm_gate_mask=0x2 REFUSED",
                 "0x8": "[MOT] arm_gate_mask=0x8 REFUSED",
                 "0x18": "[MOT] arm_gate_mask=0x18 REFUSED"}
MOTOR_MASK_02_SRC = ("reports/reviews/phase8-m8-idle-stack-r4/field/"
                     "field-20261101T154833Z/S_S4/07_running_proof.poll02.rx.bin")
MOTOR_MASK_08_SRC = ("reports/reviews/phase8-m8-r7f2b-safety-print-fix/field/"
                     "field-20261104T101046Z/S_S1/05_mot_cfg.rx.bin")
for _m, _rel in (("0x2", MOTOR_MASK_02_SRC), ("0x8", MOTOR_MASK_08_SRC)):
    _p = pathlib.Path(__file__).resolve().parents[4] / _rel
    if not _p.is_file() or MOT_MASK_LINE[_m] not in _p.read_bytes().decode("utf-8").splitlines():
        raise SystemExit("REFUSED|mask=%s 的披露行原件%s里取不到(只能照实拍写)" % (_m, _rel))

# apply 之后、safety_irq_enable(第 11 步)之前的 system_status 形状。三个字段一律回源码取值,
# 不沿用上一版的臆造值(supervisor.c:90 只打 OPEN/CLOSED 与 CONFIRMED/not-confirmed,
# safety_thread.c:315 的 protection_ready 要 st_irq_attached && st_polarity_validated 才 YES,
# 而这两件事分别发生在第 11/10 步 => 第 7 步只能是否): 旧件里写着 closed / MARKED-VALIDATED /
# protect_ready=YES, 既不是产品字面 token, 也和步序矛盾。
# 披露行的 0x8 是**推导值**而不是实测值: 现场前态实测 0x18 = bit3(NOT_PROTECTED)|bit4(NO_DRV_CONFIG),
# tmc_config_apply 成功后 motor.c:907 的 bit4 清零, bit3 仍置 => 0x8。工装用它, 板上真值以现场为准。
SYS_READY_APPLIED = ["=== SYSTEM STATUS ===",
                     "[Safety] state=READY fault=0 irq_gate=CLOSED polarity=not-confirmed "
                     "protect_ready=NO",
                     "[Motor] arm gate fail mask=0x8 (arm REFUSED)",
                     "[TMC]    runtime write gate mask=0x8 (0=运行态条件全通,"]
LEGACY_ROW = job(1, "FAILED", adc=0, sg=0, want=32, lo=250, hi=350)             # §5 行1 允许的旧终态


# ==== F21(A 第 1 点): 逐步 arm gate mask 契约表(8 处 motor_status + 2 处 system_status) ========
# 出处等级只有两个词: REAL = 这个 mask 形状在**本仓库归档的板子原始字节**里逐字存在;
# DERIVED = 由源码门位 + 前一步实测推出来, 板上这一步还没抓过这一行。两种都必须写明依据。
ARM_GATE_CONTRACT = [
    ("sys_pre", ["0x18"], "REAL",
     "supervisor.c:126 打的是同一个 motor_get_gate_fail_mask()(motor.c:894-917)。"
     "bit3 protection-not-ready | bit4 drive-config-not-applied: 极性确认在 10、开 IRQ 在 11、"
     "apply 在 6, 所以这一步四门里两门未过。原件 = 2026-11-05 F11/F20 现场两次逐字节相同的 "
     "983 B/73abf3012386 捕获(行表 system_status 就是它)。"),
    ("mot_pre", ["0x18"], "REAL",
     "motor.c:2479-2480 打 arm_gate_mask 与 REFUSED/ALL-PASS。原件 = 1128 B/f0550d313164, "
     "在 5 次归档会话 + F20 本会话逐字节相同(F20 的第二个首错正是这一行被当成业务拒绝)。"),
    ("sys_after_apply", ["0x8"], "DERIVED",
     "apply 成功清 bit4(motor.c:907 的 tmc2209_drive_config_ok_for_arm), 而 IRQ/极性仍未就绪 "
     "=> 只剩 bit3=0x8。0x8 这个值本身有真实原件(motor_status 的 1080 B/fa69aa5cf5a1, "
     "gate5 tmc_drive_cfg=CONFIGURED), 但**这一步(system_status)的 0x8 没在板上抓过**。"),
    ("mot_after_irq", ["0x0"], "REAL",
     "第 10 步极性 + 第 11 步开 IRQ => protection_ready(safety_thread.c:315 要 "
     "st_irq_attached && st_polarity_validated)成立, bit3 清零; bit4 已在第 6 步清。原件 = "
     "648 B/10807f63f02a(9 次归档一致, 头行就是 arm_gate_mask=0x0 ALL-PASS)。"),
    ("mot_dir", ["0x0"], "REAL",
     "motor.c:894-917 的七门里没有方向项, motor_dir 不置任何门位 => 仍 0x0。行表用同一份 "
     "648 B 原件, 只把频率行的 dir 值按本步工况改成 1(登记在 derived_shape_rows)。"),
    ("mot_armed", ["0x0"], "DERIVED",
     "arm 之后 Safety 仍是 READY(进 RUN 的是 start 那一提交, motor.c:1674/1935 "
     "mot_commit_run_start), 七门里没有 armed 项 => 预期全通。真实的 armed 件"
     "(0x2/9005a306999a)同时显示 armed=1 与 mask=0x2, 说明置位的是 RUN 而不是 armed。"
     "**这一行没有归档原件**: 现网没抓过 armed 且 READY 的 motor_status。"),
    ("cruise", ["0x2"], "REAL",
     "start 提交 SAFETY_RUN => motor.c:899 那条 `safety_state_get() != SAFETY_READY` 置 bit1。"
     "原件 = 1026 B/9005a306999a 与 1025 B/57efcc7c90d5(运行中两次归档, 披露行都是 "
     "arm_gate_mask=0x2 REFUSED + gate_reason=safety-not-READY)。"),
    ("mot_after_cancel", ["0x2"], "REAL",
     "取消学习作业不动电机也不动 Safety(diag_relative.c 的 cancel 只终结作业) => 仍 RUN, "
     "同上两份原件。"),
    ("stopped", ["0x0", "0x2"], "REAL",
     "唯一一条**边界**, 按 A 的要求显式申报成枚举而不是统一放行: motor_stop 归零之后 RUN->READY "
     "由 motor.c:1989 mot_commit_zero_ready 在 tick 里提交, 它要求 armed!=0 且 cur==tgt==0, "
     "与本查询的先后不确定 => 两种值都合法。两个形状各自都有真实原件(0x2: 1074 B/98abbf2d18df "
     "静止但 RUN 未落位的原件 + 上面两份运行中原件; 0x0: 648 B/10807f63f02a)。"
     "集合外的任何值(0x8/0x18/0x20/0x40)都算保护链掉了, 具名停止。"),
    ("mot_disabled", ["0x0"], "DERIVED",
     "成功 disarm 把 Safety 收回 READY 是 R6-2 的产品修复, 已在板镜像 23691e8d 的源码里 => "
     "这一步**必须** 0x0。归档里那份 post-disarm 仍打 0x2 的 1074 B/98abbf2d18df 属于 R6 之前的 "
     "idle-stack-r4 会话, 不能拿来当这一步的预期; 现场若真打 0x2, 判据按 FIELD_VALUE_MISMATCH "
     "停并把原字节留在盘上 —— 那正是要取证的事。"),
]
ARM_GATE_MASKS = {a: m for a, m, _g, _b in ARM_GATE_CONTRACT}
ARM_GATE_PROVENANCE = {a: (g, b) for a, _m, g, b in ARM_GATE_CONTRACT}


def _exe():
    """契约的行正则/豁免模式一律取执行器里的单点常量, 本工具不另写一份正则。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import importlib
    return importlib.import_module("m8r3_serial_chain_r7f8")


def _mask_row_prefix(exe, cmd):
    # 行正则在第一个通配结构(?P<mask>)之前的确定前缀, 与豁免模式用同一个取前缀函数 ——
    # 直接把正则文本切一半会把反斜杠也带进 expect_contains("\\[MOT\\]..."), 那种针正面要求不了任何行。
    return exe.status_allow_literal_prefix(exe.ARM_GATE_MASK_ROWS[cmd])


def apply_arm_gate_contract(st):
    """给一条状态查询步装上门位契约: 逐值判据 + 正面要求 + 恰好对应的整行豁免。"""
    exe = _exe()
    cmd = (st.get("cmd") or "").split(" ")[0]
    masks = ARM_GATE_MASKS[st.get("as")]
    row, (refused, passed) = exe.ARM_GATE_MASK_ROWS[cmd], exe.ARM_GATE_MASK_TAILS[cmd]
    ec = st.setdefault("expect_contains", [])
    if len(masks) == 1:
        needle = (exe.status_allow_literal_prefix(exe.arm_gate_refused_allow(cmd, masks[0]))
                  if masks[0] != "0x0" else _mask_row_prefix(exe, cmd) + masks[0])
        spec = {"row": row, "must": {"mask": masks[0],
                                     "tail": passed if masks[0] == "0x0" else refused}}
    else:
        # 边界步只能要求两种形状的**共同前缀**: 现场一次只会出现其中一条, 钉死任一条就成假红。
        needle = _mask_row_prefix(exe, cmd)
        tails = [passed, refused] if "0x0" in masks else [refused]
        spec = {"row": row, "must_one_of": {"mask": sorted(masks), "tail": sorted(tails)}}
    for w in (needle,):
        if w not in ec:
            ec.append(w)
    st["status_lines_allow"] = sorted(exe.arm_gate_refused_allow(cmd, m)
                                      for m in masks if m != "0x0")
    st["expect_values"] = (st.get("expect_values") or []) + [spec]
    return spec



def _inherited_tools():
    """从 R7F7 定稿计划继承 tools(download/readback/reset + provenance/substitutions)。

    只改 image 子块为本轮参照身份; 其余字节原样带过来, 避免把已在现场用过的命令行重新手写。
    """
    src = pathlib.Path(__file__).resolve()
    for _up in range(1, 8):
        cand = src.parents[_up] / ("reports/reviews/phase8-m8-r7f7-newimage-static/field/"
                                   "plan_r7f7_2369_static.json")
        if cand.is_file():
            r7 = json.loads(cand.read_text(encoding="utf-8"))
            break
    else:
        raise SystemExit("REFUSED|R7F7 定稿计划不在盘上, tools 块无法继承(不许手写 argv 占位)")
    tools = {k: v for k, v in (r7.get("tools") or {}).items()
             if k in ("provenance", "substitutions", "download", "readback", "reset")}
    ident = {"path": IMAGE_REL, "bytes": IMAGE_BYTES, "sha256": IMAGE_SHA}
    # 下载载荷的 AXF 三元组同样**原样继承** R7F7 登记值: identity 门要求 tools.download.argv 的
    # 末位 == tools.download.image.path, 且 S0/下载/回读三处同源。本轮不执行下载, 它只是身份参照。
    global AXF
    AXF = dict((r7.get("identity_binding") or {}).get("image", {}).get("axf") or {})
    if not (AXF.get("path") and AXF.get("sha256") and AXF.get("bytes")):
        raise SystemExit("REFUSED|R7F7 计划里没有 AXF 三元组, 无法继承身份(不许手写)")
    # 身份门要求的"三处同源"在这里是: S0 参照 image(BIN) == readback.image(BIN);
    # identity.image.axf == download.image == download.axf(下载载荷 AXF)。本轮两者都不执行。
    tools.setdefault("readback", {})["image"] = dict(ident, note=(
        "R7F8F 本轮 0 次回读: 这条 argv 是 R7F7 原样继承的登记值; image 子块给的是**在板 BIN**"
        "的参照身份, 不代表本轮执行过回读。"))
    tools.setdefault("download", {})["image"] = dict(AXF, note=(
        "R7F8F 本轮 0 次下载: image/axf 子块是下载载荷 AXF 的登记身份(与 argv 末位同源), "
        "不是本轮动作结果。"))
    tools["download"]["axf"] = dict(AXF)
    tools["reset"]["note"] = "R7F8F 本轮 0 次复位(§4: 不复位)。"
    return tools


def rows_doc():
    c = {
        "system_status": {"healthy": SYS_READY},
        "system_status_applied": {"healthy": SYS_READY_APPLIED},
        "motor_status_pre_apply": {"healthy": MOT_REAL_18},
        "motor_status_idle": {"healthy": MOT_REAL_00},
        "motor_status_ready_dir": {"healthy": MOT_REAL_00_DIR1},
        "motor_status_armed_ready": {"healthy": MOT_ARMED_IDLE + [MOT_MASK_LINE["0x0"]]},
        "motor_status_armed_idle": {"healthy": MOT_ARMED_IDLE + [MOT_MASK_LINE["0x2"]]},
        "motor_status_cruise": {"healthy": MOT_CRUISE + [MOT_MASK_LINE["0x2"]]},
        "motor_status_accel": {"healthy": MOT_ACCEL + [MOT_MASK_LINE["0x2"]]},
        "motor_arm": {"healthy": ARM_OK},
        "motor_disarm": {"healthy": DISARM_OK},
        "motor_stop": {"healthy": STOP_OK},
        "motor_dir": {"healthy": DIR_OK},
        "motor_target": {"healthy": TARGET_OK},
        "motor_start": {"healthy": START_OK},
        "safety_irq_status_closed": {"healthy": IRQ_STATUS_ARMED + IRQ_CLOSED},
        "safety_irq_status_open": {"healthy": [
            "[SAFETY] irq gate   = OPEN (EXTI registered)",
            "[SAFETY] polarity   = operator CONFIRMED (HARDWARE-PENDING unless CONFIRMed)"]},
        # 固定尾部的最后一条(执行器自己发, 不是步骤): 关掉之后再取一次状态。值按"已经 disable"
        # 给, 形状仍是 :388/:392 那两行。
        "safety_irq_status": {"healthy": [
            "[SAFETY] irq gate   = CLOSED (polled only)",
            "[SAFETY] polarity   = operator CONFIRMED (HARDWARE-PENDING unless CONFIRMed)"]},
        "safety_irq_enable": {"healthy": IRQ_OPEN},
        # 尾部第一条发的是 safety_irq_disable => 产品走 st_irq_detach_all(), 打的是
        # safety_thread.c:306 那行 CLOSED(**不是** :290 的 "stays CLOSED", 那行属于 attach
        # 失败回滚路径)。上一版填了 :290, 于是整包程序的收口尾巴判成 IRQ_TAIL_NOT_CLOSED。
        "safety_irq_disable": {"healthy": IRQ_CLOSED},
        # safety_thread.c:457/452: 只翻转软件标志, 不声称硬件极性已测。
        "safety_polarity_confirm": {"healthy": [
            "[SAFETY] input polarity MARKED VALIDATED (operator confirm)",
            "[SAFETY]       hardware chain; only the polarity flag changes here."]},
        # 未取到逐字打印形状的两条(见 _about.unverified_shapes): 只当回显占位, 步骤也只断言前缀。
        "tmc_config_apply": {"healthy": ["[TMC] apply CONFIRM"]},
        "tmc_config_show": {"healthy": ["[TMC] config show"]},
        "diag_profile_zero": {"healthy": ["[DIAG] zero OK n=32 raw 480..520 mean=500 "
                                          "(cal source untouched)"]},
        "ls_idle": {"healthy": job(0, "IDLE", want=0)},
        "ls_running32": {"healthy": job(41, "RUNNING", want=32, adc=1, sg=1)},
        "ls_cancelled": {"healthy": job(41, "CANCELLED", want=32)},
        "learn32": {"healthy": ["[DIAG] learn STARTED id=41 n=32 win=250..350 dir=1",
                                "[DIAG] learn async: query 'learn_status'; "
                                "NOT learn OK yet FIELD_UNVERIFIED"]},
        "learn8": {"healthy": ["[DIAG] learn STARTED id=57 n=8 win=250..350 dir=1",
                               "[DIAG] learn async: query 'learn_status'; "
                               "NOT learn OK yet FIELD_UNVERIFIED"]},
        "cancel_cmd": {"healthy": ["[DIAG] cancel id=41 REQUESTED st=CANCEL_PENDING "
                                   "(motor untouched)"]},
        "ls_run8a": {"healthy": job(57, "RUNNING", adc=4, sg=4)},
        "ls_done8": {"healthy": done_full(57)},
        "__watch__": {"healthy": ["[DIAG] quiet"]},
        # 固定清理三条按**命令名**查表, 与业务段同一张表但要各给一份(禁能快照 = 静止那份)。
        # 这一条改用 0x0 的归档原件(13 行逐字), 因为 disable_contract 要的六组字段它全都有;
        # 上一版用的是我手拼的六行声明形状。
        "motor_status": {"healthy": MOT_REAL_00},
        "diag_status": {"healthy": ["[DIAG] mode=MONITOR_ONLY", "[DIAG] live sig=0 owner=FREE"]},
        "diag_profile_show": {"healthy": ["[DIAG] profile UNCONFIGURED pending=VALID "
                                          "id=57 rev=1 FIELD_UNVERIFIED"]},
        "diag_profile_commit": {"healthy": ["[DIAG] commit OK id=57 rev=1 (RAM only; "
                                            "config_save NOT issued) FIELD_UNVERIFIED"]},
        "ps": {"healthy": ["thread    pri  status      sp     stack size max used left tick",
                           "main         10  suspend 0x00000000 0x00000800 06%   00000001"]},
        "free": {"healthy": ["total    : 65536", "used     : 20480", "maximum  : 24576",
                             "available: 45056"]},
    }
    return {
        "_about": {
            "kind": "SYNTHETIC_REAL_SHAPE_ROWS_NOT_BOARD_EVIDENCE",
            "shape_source": "逐字取自产品打印语句(motor.c / diag_relative.c / safety_thread.c / "
                            "supervisor.c), 数值为本包工装造的合成值; 板端实测值一律不在此文件。",
            "assertion_scope": "只给本轮**已回源码核到形状**的字段写判据; 其余查询步只断言"
                               "命令回显与完整回包(见 plan.field_assertion_gaps)。",
            "job_ids": "取消作业 41 / 完成作业 57 均来自各自 STARTED 行; 计划里只写 @引用。",
            "unverified_shapes": [
                "tmc_config_apply", "tmc_config_show",
                "diag_profile_show", "diag_profile_commit",
                "diag_status", "ps/free 的表头取自 R7F7 已用过的形状, 阈值不判"],
            "unverified_note": "这几条本轮**没有**回源码取到逐字打印形状, 因此行内容是回显占位, "
                               "对应步骤只断言前缀存在; §5 里对它们要求的字段判据一律没写 "
                               "(宁缺勿造), 已列进 plan.field_assertion_gaps 交 A 判。",
            "board_observed_rows": ["system_status —— 这份的 19 行逐字取自 2026-11-05 21:23 "
                                    "F11 现场捕获 proof/r7f8f_field_f11_sys_pre.rx.bin(983 B, "
                                    "sha256 73abf3012386…; 去掉首行命令回显与结尾 msh 提示符)。"
                                    "它不是工装造的: 上一版把 mask=0x18 记在 TMC 写门行、把 "
                                    "protect_ready 写成 YES, 现场实拍证明两处都不对, 而正是这个"
                                    "错位让离线 stub 测不到披露行被误判成业务拒绝。",
                                    "motor_status_pre_apply / motor_status(清理那一条) / "
                                    "motor_status_idle —— 行内容改成 F21 读进来的**归档原件**: "
                                    "1128 B/f0550d313164(R7F7 会话 15_mot_final.rx.bin, 与 R7F4 "
                                    "写前那份逐字节相同的归档共 5 次 + F20 本会话第 6 次)与 "
                                    "648 B/10807f63f02a(9 次归档一致)。口径按 A 的更正: "
                                    "0x18 那一份是**F20 本会话首次取得**, 但同一身份的字节早已"
                                    "归档在 5 次会话里, 上一版把历史样本漏了。取件与核验在 "
                                    "_observed_mot_rows() 里, 字节或散列与登记不符就直接拒绝生成。"],
            "derived_shape_rows": ["system_status_applied —— 第 7 步(apply 后、irq_enable 前)的切片。"
                                   "token 取 supervisor.c:90 的实际打印(OPEN/CLOSED、"
                                   "CONFIRMED/not-confirmed), protect_ready 按 safety_thread.c:315 "
                                   "取 NO(第 10/11 步都还没跑), 披露行 mask 由现场实测 0x18 减去 "
                                   "apply 成功的 bit4(motor.c:907) 推得 0x8 —— 全部是**源码+前态推导**,"
                                   " 不是这一步的板上实测。",
                                   "motor_status_ready_dir —— 第 15 步(motor_dir 1 之后): 用 "
                                   "648 B/10807f63f02a 的原件 13 行, 只把频率行的 dir 值改成 1 "
                                   "(归档原件抓于 dir=0 时)。除这一格外逐字节同原件。",
                                   "motor_status_armed_ready / motor_status_armed_idle —— "
                                   "第 17/29 步的 armed 形状: 字段来自本包既有的声明形状"
                                   "(现网没抓过 armed 的 motor_status), 披露行 arm_gate_mask="
                                   "0x0/0x2 逐字取归档原件的形状(648 B/10807f63f02a 与 "
                                   "1026 B/9005a306999a)。",
                                   "motor_status_cruise / motor_status_accel —— 第 20/25 步与轮询:"
                                   " 300 Hz/CRUISE/ACCEL 的字段是本包工况值(归档运行件是 100 Hz 的"
                                   "会话), 只有披露行 arm_gate_mask=0x2 REFUSED 逐字取归档原件 "
                                   "(1026 B/9005a306999a、1025 B/57efcc7c90d5)。",
                                   "行表里 motor_status 的披露行只给**那一条** —— 板上 mask≠0 时"
                                   "产品还会再打一条同名的裸行与 gate_reason/gate4..7 明细行"
                                   "(motor.c:935-960), 那些行不含 REFUSED, 不参与业务失败扫描; "
                                   "整段真实字节由矩阵的 MC 家族直接喂 assert_step 判, 不在这里仿造。"],
            "hardware_actions": 0},
        "commands": c}


def steps():
    """§5 的 16 行 => 15 个业务步骤 + finally 固定收口(执行器自己做, 不占步骤)。"""
    s = []

    def add(asname, cmd, **kw):
        d = {"as": asname, "cmd": cmd}
        d.update({k: v for k, v in kw.items() if v is not None})
        s.append(d)

    # 1) 前态核对(全只读)
    add("sys_pre", "system_status", key="system_status", seconds=1,
        expect_contains=["=== SYSTEM STATUS ==="])
    add("mot_pre", "motor_status", key="motor_status_pre_apply", seconds=1,
        expect_contains=["[MOT] state=0", "[MOT] armed=0 drv_en_request=0"])
    add("irq_pre", "safety_irq_status", key="safety_irq_status_closed", seconds=1,
        expect_contains=["[SAFETY] irq gate   = CLOSED"])
    add("diag_pre", "diag_status", seconds=1, expect_contains=["[DIAG]"])
    add("ls_pre", "diag_profile learn_status", key="ls_idle", seconds=1,
        expect_contains=["[DIAG] job id="])
    add("cfg_pre", "tmc_config_show", seconds=1, expect_contains=["[TMC]"])
    # 2) 一次配置应用(§4: 先核目标配置再 apply 一次)
    add("apply", "tmc_config_apply CONFIRM", key="tmc_config_apply", seconds=2,
        expect_contains=["[TMC]"], quota={"name": "tmc_config_apply", "n": 1})
    # 门由 0x18 -> 0x8 用**已核到形状**的 system_status 行来核(supervisor.c:128), 不靠 apply 自己话术。
    add("sys_after_apply", "system_status", key="system_status_applied", seconds=1,
        expect_contains=["=== SYSTEM STATUS ===", "runtime write gate mask=0x8"])
    # 3) 静止零点
    add("zero", "diag_profile zero 32", key="diag_profile_zero", seconds=13,
        expect_contains=["[DIAG] zero OK n=32"], quota={"name": "diag_profile_zero", "n": 1})
    add("zero_state", "diag_profile learn_status", key="ls_idle", seconds=1,
        expect_contains=["[DIAG] job id="])
    # 4) 极性确认 + 开 IRQ(本轮单独额度; confirm 不升级为 polarity_measured)
    add("pol", "safety_polarity_confirm CONFIRM", key="safety_polarity_confirm", seconds=1,
        expect_contains=["[SAFETY] input polarity MARKED VALIDATED",
                         "only the polarity flag changes here"],
        quota={"name": "irq_polarity_prep", "n": 1})
    add("irq_en", "safety_irq_enable", key="safety_irq_enable", seconds=1,
        expect_contains=["IRQ gate OPEN"], quota={"name": "irq_polarity_prep", "n": 1})
    add("irq_state", "safety_irq_status", key="safety_irq_status_open", seconds=1,
        expect_contains=["[SAFETY] irq gate   = OPEN"])
    add("mot_after_irq", "motor_status", key="motor_status_idle", seconds=1,
        expect_contains=["[MOT] armed=0 drv_en_request=0", "cur_hz=0 tgt_hz=0"])
    # 5) DIR1 与静止复核
    add("dir", "motor_dir 1", key="motor_dir", seconds=2, expect_contains=["[MOT] dir=1"],
        quota={"name": "motor_dir", "n": 1})
    add("mot_dir", "motor_status", key="motor_status_ready_dir", seconds=1,
        expect_contains=["[MOT] state=0", "dir=1", "armed=0"])
    # 6) arm + 相邻状态(F3: 一场一次 arm; 义务从写入尝试起算)
    add("arm", "motor_arm", key="motor_arm", seconds=1,
        expect_contains=["ARMED (drv_en request=HIGH readback verified"],
        quota={"name": "motor_arm", "n": 1})
    add("mot_armed", "motor_status", key="motor_status_armed_ready", seconds=1,
        expect_contains=["[MOT] armed=1 drv_en_request=1", "expected_ENN=LOW ENN_fb=0"])
    # 7) target + start + 有界轮询到 CRUISE(§4: start 尝试起 12 s)
    add("target", "motor_target 300", key="motor_target", seconds=2,
        expect_contains=["[MOT] target=300"], quota={"name": "motor_target", "n": 1})
    add("start", "motor_start", key="motor_start", seconds=2, expect_absent=["REFUSED"],
        quota={"name": "motor_run", "n": 1})
    s.append({"as": "cruise", "cmd": "motor_status", "key": "motor_status_cruise",
              "seconds": 1, "expect_profile": "RUNNING",
              "expect_running": {"dir": 1, "cur": 300, "tgt": 300, "accel": 2000, "decel": 2000},
              "poll": {"until_seconds": 1.0, "interval_seconds": 0.1,
                       "max_attempts": 4, "retry_codes": ["PROFILE_NOT_RUNNING"]},
              "poll_sequence": ["motor_status_accel", "motor_status_cruise"]})
    # 8) 取消试验作业(N=32)
    add("learn_cancel", "diag_profile learn 32 250 350 1", key="learn32", seconds=1,
        expect_contains=["STARTED"], quota={"name": "diag_profile_learn", "n": 1},
        job_bind={"name": "cancel_job", "params": {"n": 32, "lo": 250, "hi": 350, "dir": 1}})
    add("ls_cancel_bound", "diag_profile learn_status", key="ls_running32", seconds=1,
        expect_contains=["[DIAG] job id="])
    # 9) 取消 + 有界轮询到 CANCELLED
    add("cancel", "diag_profile learn_cancel @cancel_job", key="cancel_cmd", seconds=1,
        job_cmd="cancel_job", expect_contains=["REQUESTED"],
        quota={"name": "diag_profile_learn_cancel", "n": 1})
    s.append({"as": "cancel_state", "cmd": "diag_profile learn_status", "key": "ls_cancelled",
              "seconds": 1,
              "expect_values": [{"row": JOB_ROW, "must": {"st": "CANCELLED", "owner": "0",
                                                          "cred": "1/0"},
                                 "job_binding": "cancel_job"}],
              # 取消之后的这一行**必须申报 phase**: 不申报时 job_contract 按默认 "done" 判,
              # 而 done 阶段不允许 CANCELLED(A 的 §2 状态合同: CANCELLED 只属于取消那条链)。
              # 上一版没申报, 整包程序在第 26 步就 JOB_CONTRACT_VIOLATED 停止。
              "job_contract": {"binding": "cancel_job", "phase": "post_cancel"},
              "poll": {"until_seconds": 1.0, "interval_seconds": 0.1, "max_attempts": 3,
                       "retry_codes": ["FIELD_VALUE_MISMATCH"],
                       "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                       "job_binding": "cancel_job"}})
    # 10) 取消不代替停机: 仍巡航
    add("mot_after_cancel", "motor_status", key="motor_status_cruise", seconds=1,
        expect_profile="RUNNING",
        expect_running={"dir": 1, "cur": 300, "tgt": 300, "accel": 2000, "decel": 2000})
    # 11) 完成作业(N=8) + 有界轮询到完整 DONE
    add("learn_done", "diag_profile learn 8 250 350 1", key="learn8", seconds=1,
        expect_contains=["STARTED"], quota={"name": "diag_profile_learn", "n": 1},
        job_bind={"name": "done_job", "params": {"n": 8, "lo": 250, "hi": 350, "dir": 1}})
    s.append({"as": "wait_done", "cmd": "diag_profile learn_status", "key": "ls_done8",
              "seconds": 1,
              "expect_values": [{"row": JOB_ROW, "must": {"st": "DONE", "owner": "0",
                                                          "cred": "1/1"},
                                 "job_binding": "done_job"},
                                {"row": N_ROW, "must": {"want": 8},
                                 "must_min": {"adc": 8, "sg": 8}}],
              "poll": {"until_seconds": 2.0, "interval_seconds": 0.1, "max_attempts": 12,
                       "retry_codes": ["FIELD_VALUE_MISMATCH", "JOB_DONE_CANDIDATE_EVIDENCE_MISSING"],
                       "in_progress": {"row": JOB_ROW, "states": ["RUNNING", "CANCEL_PENDING"]},
                       "job_binding": "done_job"},
              "poll_sequence": ["ls_run8a", "ls_done8"]})
    # 12) 停机(start 后 8 s 内发起; 有界轮询到零速)
    add("stop", "motor_stop", key="motor_stop", seconds=2,
        expect_contains=["stop requested: target_hz=0"], quota={"name": "motor_stop", "n": 1})
    s.append({"as": "stopped", "cmd": "motor_status", "key": "motor_status_armed_idle",
              "seconds": 1,
              # 裁定一: 这一步就是**运动计时的结束点** —— 申报它承担那份可信零速快照,
              # 执行器只在这一步的实际回包通过 zero_speed_contract 时才解除运动 cap。
              "motion_end_evidence": True,
              # 裁定 §4 末条: post-stop 行要单列真实"零速但仍 armed"的完整反馈形状,
              # 不拿初态禁能快照(motor_status_idle: armed=0/LOW/pad0/ENN HIGH/1)顶替。
              "expect_contains": ["[MOT] state=0 name=", "[MOT] cur_hz=0 tgt_hz=0",
                                  "[MOT] armed=1 drv_en_request=1 health=OK",
                                  "[MOT] drv_request=HIGH pad=1",
                                  "[MOT] expected_ENN=LOW ENN_fb=0 resolved verdict=OK"],
              "poll": {"until_seconds": 1.0, "interval_seconds": 0.1, "max_attempts": 5,
                       "retry_codes": ["EXPECTATION_NOT_MET"]}})
    # 13) disarm + 相邻禁能快照(F3: 不凭 DISARM 子串解除义务)
    add("disarm", "motor_disarm", key="motor_disarm", seconds=2,
        expect_contains=["DISARM (动作已做"], quota={"name": "motor_disarm", "n": 1})
    add("mot_disabled", "motor_status", key="motor_status_idle", seconds=1,
        expect_contains=["[MOT] armed=0 drv_en_request=0", "expected_ENN=HIGH ENN_fb=1",
                        "verdict=OK"])
    # 14) 提交(只发 RAM; 不发 config_save)
    add("show_pre", "diag_profile show", key="diag_profile_show", seconds=1,
        expect_contains=["[DIAG] profile"])
    add("commit", "diag_profile commit", key="diag_profile_commit", seconds=1,
        expect_contains=["[DIAG] commit OK"], quota={"name": "diag_profile_commit", "n": 1})
    add("show_post", "diag_profile show", key="diag_profile_show", seconds=1,
        expect_contains=["[DIAG] profile"])
    add("ls_post", "diag_profile learn_status", key="ls_done8", seconds=1,
        expect_contains=["[DIAG] job id="])
    # 15) 栈/堆高水位(只报本工况)
    add("threads", "ps", key="ps", seconds=1,
        expect_contains=["stack size", "main"])
    add("heap", "free", key="free", seconds=1,
        expect_contains=["total    :", "used     :", "maximum  :", "available:"])

    # F19(现场 F11 暴露)+ F21(A 第 1 点): 查询步的"门位披露行"正面对策统一由门位契约表装配。
    # F19 只给 system_status 装了豁免, 而且用的是通配 hex 的形状(任意 REFUSED 都放行); F21 把它
    # 换成**逐步钉死预期 mask** —— 8 处 motor_status 全部进表, 预期 0x0 的步骤一条豁免都没有。
    # 起转/使能/学习类步骤**不会**被这个循环碰到: 循环按 cmd 精确匹配两条状态查询, 而且
    # validate-plan 的 R7F8F_ARM_GATE_MASK 会独立把每一条数出来(见契约表与判据行的 mask 列表)。
    for st in s:
        if st.get("as") in ARM_GATE_MASKS:
            apply_arm_gate_contract(st)
    # system_status 另有两个硬字段(Safety 状态/前态 motor 快照)与门位无关, 仍按源码形状申报。
    for st in s:
        if (st.get("cmd") or "").split(" ")[0] != "system_status":
            continue
        spec = [{"row": r"^(?P<head>\[Safety\] state=(?P<st>[A-Z_]+) fault=(?P<fault>[0-9])"
                          r" irq_gate=(?P<irq>[A-Z_]+) polarity=(?P<pol>[A-Za-z-]+)"
                          r" protect_ready=(?P<pr>[A-Z]+))$",
                 "must": {"st": "READY", "fault": "0"}}]
        if st.get("as") == "sys_pre":
            # 前态必须真的"没带电、没在转" —— 这四格是 2026-11-05 实拍值, 也是 §5 行1 要核的东西
            spec.append({"row": r"^\[Motor\] state=(?P<st>[0-9]) cur=(?P<cur>[0-9]+) "
                                 r"tgt=(?P<tgt>[0-9]+) armed=(?P<armed>[0-9]) "
                                 r"drv_en_verified=(?P<drv>[0-9]) health=(?P<health>[A-Z_]+)$",
                         "must": {"st": "0", "cur": "0", "tgt": "0", "armed": "0",
                                  "health": "OK"}})
        st["expect_values"] = (st.get("expect_values") or []) + spec
    return s


JOB_ROW = (r"^\[DIAG\] job id=(?P<id>[0-9]+) st=(?P<st>[A-Z_]+) why=(?P<why>[A-Z_]+) "
           r"owner=(?P<owner>[0-9]+) cred=(?P<cred>[0-9]/[0-9])$")
N_ROW = (r"^\[DIAG\] job n=(?P<adc>[0-9]+)/(?P<sg>[0-9]+) want=(?P<want>[0-9]+) "
         r"win=(?P<lo>[0-9]+)\.\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")


def plan_doc():
    st = steps()
    # tools 必须先取: 它会把 AXF 三元组读进全局 AXF, 下面 identity_binding 要用同一份值。
    tools_block = _inherited_tools()
    # F6(A 的补充审核): 三段预算**不在这个工具里另写一份算法** —— 直接 import 派生执行器里的
    # business_segments(), 由它算出申报窗合计与含每步 SLACK 的最坏上界, 再把结果原样写进计划的
    # motion_budget.business_segments 镜像。执行器计划门会逐字段比对这份镜像(PLAN_BUSINESS_
    # RESERVE_SPLIT 的 mirror= 字段), 所以"检查器与执行器使用同一值"是被机器判的, 不靠注释。
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import importlib
    exe = importlib.import_module("m8r3_serial_chain_r7f8")
    # G(裁定二)落表: 38 步的**单步总窗**逐个取 A 包里 step_total_windows.json 的申报值。
    # 逐条按 (as, cmd) 对齐才写入 —— 对不上就当场拒绝, 不"顺手"把窗口贴到别的步上。
    tbl_path = pathlib.Path(__file__).resolve().parents[1] / \
        "inputs/A_R7F8F_BUDGET_PKG/step_total_windows.json"
    tbl = json.loads(tbl_path.read_text(encoding="utf-8"))
    mis, ref_sha = [], tbl.get("reference_plan_sha256")
    for t in tbl["steps"]:
        i = t["index"]
        if i >= len(st) or (st[i].get("as"), st[i].get("cmd")) != (t["as"], t["cmd"]):
            mis.append("%d:表=%s/%s 计划=%s/%s" % (i, t["as"], t["cmd"],
                                                   st[i].get("as") if i < len(st) else "-",
                                                   st[i].get("cmd") if i < len(st) else "-"))
            continue
        st[i]["step_total_seconds"] = t["step_total_seconds"]
        st[i]["window_phase"] = t["phase"]
    if mis or len(tbl["steps"]) != len(st):
        raise SystemExit("WINDOW_TABLE_REFUSED|A 的总窗表与本计划对不上: %s" % mis[:4])
    bad_poll = [x.get("as") for x in st
                if exe.step_total_problem(x)]
    if bad_poll:
        raise SystemExit("WINDOW_TABLE_REFUSED|申报的总窗过不了执行器自己的校验: %s" % bad_poll[:4])
    bs = exe.business_segments_ruling(st, cap=12, reserve=7, total=90)
    # 门位契约表进计划: 每条都带预期 mask、出处等级(REAL/DERIVED)与依据, 复核者不需要信我手工数的
    # "7 处"。下面这条拒绝先证**覆盖**: 计划里凡是 motor_status / system_status 的步, 都必须在这张
    # 表里有一条; 少一步就不产出(契约表与步骤表分叉正是 F20 那两个首错的形状)。
    contract_as = [a for a, _m, _g, _b in ARM_GATE_CONTRACT]
    covered = [x.get("as") for x in st
               if (x.get("cmd") or "").split(" ")[0] in exe.ARM_GATE_MASK_ROWS]
    if sorted(covered) != sorted(contract_as):
        raise SystemExit("MASK_CONTRACT_REFUSED|契约没覆盖全部状态查询步: 计划里=%s 契约表=%s"
                         % (covered, contract_as))
    by_as = {x.get("as"): x for x in st}
    mask_table = []
    for a, masks, grade, basis in ARM_GATE_CONTRACT:
        step = by_as[a]
        mask_table.append({
            "as": a, "cmd": step["cmd"], "rows_key": step.get("key") or step["cmd"],
            "expect_arm_gate_mask": masks,
            "asserted_by": ("must_one_of" if len(masks) > 1 else "must"),
            "source_grade": grade, "basis": basis,
            "status_lines_allow": list(step.get("status_lines_allow") or []),
            "positive_requirement": [w for w in (step.get("expect_contains") or [])
                                     if "arm_gate_mask" in w or "arm gate fail mask" in w]})
    return {
        "package": "M8-R7F8F-FIELD-ONE-SESSION",
        "status": "OFFLINE_CANDIDATE_NOT_AUTHORIZED_NOT_EXECUTED",
        "task_id": "M8R7F8F-repair-and-field-20261105",
        "numbers_are_proposals_not_authorization": True,
        "hardware_actions_this_round": 0,
        "rows_file": "rows_r7f8f_real.json",
        exe.ARM_GATE_MASK_CONTRACT_KEY: exe.ARM_GATE_MASK_CONTRACT_VERSION,
        "arm_gate_mask_contract_steps": mask_table,
        "arm_gate_mask_contract_note": (
            "A F21 第 1 点: 原 38 步表里 motor_status 有 8 处(含 stopped / mot_disabled)、"
            "system_status 有 2 处, 这 10 处**逐条**申报预期 arm gate mask 与是否需要豁免; "
            "预期 0x0 的 5 处一条豁免都没有(所以现场打出 REFUSED 会按 BUSINESS_FAILED 停), "
            "唯一的边界步 stopped 用 must_one_of 枚举 0x0|0x2, 不给所有查询统一放行任意 REFUSED。"
            "动作被产品拒绝(motor_arm/motor_start/learn 的 REFUSED)与查询自身失败"
            "(diag_relative.c:2041 的 learn_status REFUSED)继续 STOP —— 豁免只给状态披露行。"),
        # A F21 第 2 点: "旧计划判据没删"不等于新计划**继承**了旧披露合同。旧冻结计划里凡是本包
        # 步序产生不出的披露形状, 都要在这里**点名不继承 + 给可按步序复算的理由**; 矩阵的 MC10
        # 会拿这个 window_check 去步骤表里查那段窗口, 查到同类查询步就判红。
        "arm_gate_mask_contract_not_inherited": [{
            "cmd": "motor_status", "mask": "0x8",
            "declared_by": ["R7F4 plan_r7f4_b502_configured_reviewed.json/mot_post",
                            "R7F5 plan_r7f5_b502_blind_irq_static.json/mot_cfg"],
            "reason": "本包在 tmc_config_apply(第 6 步)之后、safety_irq_enable(第 11 步)之前"
                      "**不发 motor_status** —— 那段 0x8 窗口里唯一的查询是第 7 步 system_status, "
                      "它已按预期 0x8 申报豁免。所以不是一条豁免被删掉, 而是这个形状在本计划里"
                      "没有对应的查询步; 现场若在别的时刻打出 motor_status 0x8, 按 BUSINESS_FAILED 停。",
            "window_check": {"after_step": "apply", "before_step": "irq_en",
                             "cmd": "motor_status"}}],

        "needles_file": None,
        "scope_this_package": (
            "在板固件 23691e8d… 身份连续(§4), 本轮唯一动作 = COM5 一次会话里跑 §5 的 15 步 + "
            "finally 固定收口。不下载、不回读、不复位、不动分析仪、不断送电、不重试。"),
        "field_boundary": {
            "polarity_measured": False,
            "core_protection_verified": False,
            "note": "safety_polarity_confirm 只是软件确认, 不升级为极性实测; 本轮 MONITOR_ONLY, "
                    "不造 SG/ADC/IMU 故障, 因此核心保护闭环未被本包证明。"},
        "identity_binding": {
            "note": "本轮没有 download/readback/reset 动作, 所以**不声明** board_image_before "
                    "字段(那条门的意思是「回读不许绑到旧镜像」; 无回读时它无对象)。旧镜像身份"
                    "只作参照写在 inherited_board_image。",
            "image": {"path": IMAGE_REL, "bytes": IMAGE_BYTES, "sha256": IMAGE_SHA,
                      "axf": dict(AXF),
                      "note": "参照身份: R7F7 已证在板的镜像(逐字节回读在 R7F7-F1/R7F8 轮已闭合)。"},
            "inherited_board_image": {"sha256": IMAGE_SHA, "bytes": IMAGE_BYTES,
                                      "proven_by": "M8-R7F7-F1 现场轮(唯一一次定长回读)"}},
        # tools 的三条 argv 从 R7F7 定稿计划**原样继承**(那些命令行已在现场成功用过), 不是新造;
        # image 子块换成本轮"参照身份"。本轮 download/readback/explicit_reset 额度全 0 且没有
        # 对应阶段, 所以这些 argv 是登记用, 不可能被执行。
        "tools": tools_block,
        "quota": {"port_open": 1, "dispatch": 1, "download": 0, "readback": 0,
                  "explicit_reset": 0, "approved_reboot": 0, "config_save": 0,
                  "tmc_config_apply": 1, "irq_polarity_prep": 2,
                  "diag_profile_zero": 1, "diag_profile_learn": 2,
                  "diag_profile_learn_cancel": 1, "diag_profile_commit": 1,
                  "motor_dir": 1, "motor_target": 1, "motor_arm": 1, "motor_run": 1,
                  "motor_stop": 1, "motor_disarm": 1,
                  "motor_run_seconds_total": 12, "motion_cap_seconds_per_run": 12,
                  "cleanup_motor_stop": 1, "cleanup_motor_disarm": 1,
                  "cleanup_motor_status": 1, "cleanup_safety_irq_disable": 1,
                  "cleanup_safety_irq_status": 1,
                  "retry": 0, "physical_collision": 0, "blackbox_clear": 0, "dmm_readings": 0},
        "motion_budget": {
            "start_deadline_seconds": 12, "arm_energize_deadline_seconds": 18,
            "session_total_seconds": 90, "cleanup_reserve_seconds": 7,
            "business_worst_declared_seconds": bs["worst_declared"],
            "business_segments": bs,
            "segments_source": "由 tools/m8r3_serial_chain_r7f8.py 的 "
                               "business_segments_ruling() 现算(cap=12/reserve=7/total=90 取"
                               "任务书 §4 固定值); 本工具不另写一份算术, 计划门再逐字段比对这份"
                               "镜像 => 检查器与执行器同一值。语义 = A 预算裁定 2026-11-05",
            "arithmetic": "会话绝对截止 = 90 s; 业务绝对截止 = 90 - 7 = 83 s(F5c 由执行器强制); "
                          "运动截止从 motor_start 写入尝试起算 12 s, **到本会话内那份通过 "
                          "zero_speed_contract 的可信零速快照为止**(裁定一: stop 的回话不结束计时, "
                          "零速确认后也不再靠清空 t0 放行第二次 start); 零速之后的 commit/show/ps/"
                          "free 受 83 s 业务截止与 arm 18 s 通电截止约束, 不再受 t0+12 夹; "
                          "三段互不重叠: 起转前 %s s(%d 步) + 活动(起转→零速) %s s(%d 步, cap %s s "
                          "只算一次) + 零速后 %s s(%d 步) = 总窗合计 %s s; 保守式(整段 cap 替活动窗) "
                          "= %s s <= 90 s" % (bs["pre_start_seconds"], bs["pre_start_steps"],
                                              bs["motion_active_seconds"],
                                              bs["motion_active_steps"], bs["window_seconds"],
                                              bs["post_zero_seconds"], bs["post_zero_steps"],
                                              bs["business_total_seconds"],
                                              bs["conservative_total_with_cap_s"]),
            "step_windows_source": "inputs/A_R7F8F_BUDGET_PKG/step_total_windows.json"
                                   "(38 步逐条按 (as, cmd) 对齐后写入; 对不上本工具直接拒产)"},
        "observation_bounds": {"cruise_poll": "最多 4 次 / 间隔 0.1 s / 整步 1 s(§5 行7)",
                               "cancel_poll": "最多 3 次 / 1 s(§5 行9)",
                               "done_poll": "最多 12 次 / 2 s(§5 行11)",
                               "stop_poll": "最多 5 次 / 1 s(§5 行12)"},
        # A 预算裁定(2026-11-05)之后: 上一版交给 A 定口径的两条静态冲突**由裁定给出出口**,
        # 本包按裁定落地 => 不再有"待 A 定口径"的未决项。旧口径的两个数(91.0 / 23.0)仍原样
        # 留在 business_segments.legacy_* 与 validate-plan 的 LEGACY_SEMANTICS 行里, 注明它们
        # 对应"每步再加 SLACK / stop 不解除运动计时", 转绿不是把数改掉。
        "static_budget_open_items": [
            "已按裁定一落地: 运动计时的结束点是本会话内 `as=stopped` 那份通过 zero_speed_"
            "contract 的可信零速快照(stop 的回话不算; 快照残缺/矛盾/迟到则 cap 继续生效), "
            "之后 commit/show/ps/free 只受 83 s 业务截止与 arm 18 s 通电截止约束。cap/reserve/"
            "total/arm 四个固定数一字未动。",
            "已按裁定二落地: 38 步逐条申报 step_total_seconds(含全部等待与余量), 由 step_window()"
            " 供执行器/计划门/镜像共同取值 => 不再每步叠 SLACK; 未申报的旧计划仍走 legacy。",
            "旧口径留档: 按「每步再叠 SLACK=%s s、stop 不解除运动计时」算, 起转前最坏合计 %s s、"
            "起转后申报内容 %s s —— 这两条就是上一版交 A 定口径的红, 现仅作为旧语义的对照数"
            "保留在本计划里。" % (bs["legacy_slack_seconds"], bs["legacy_pre_bound_seconds"],
                                  bs["legacy_after_spin_declared_seconds"])],
        "field_assertion_gaps": [
            "diag_status: 只断言 [DIAG] 前缀回显(本轮未回源码核到该命令的字段行)",
            "tmc_config_show: 只断言 [TMC] 前缀回显(档位实值核对留给 apply 后的 CONFIGURED 行)",
            "diag_profile show / commit: 只断言 [DIAG] profile / [DIAG] commit OK; §5 行14 要的"
            "pending=VALID、id/rev/统计一致、match=YES 需要产品那几条打印的逐字形状, 本轮未取到,"
            " 因此**没有**写成字段判据(宁缺勿造)",
            "ps/free: 按 R7F7 已用过的表头断言存在, 不设阈值(只报本工况, 不称带载最坏栈)"],
        "byte_integrity_policy": "所有产物按磁盘原字节算 sha256; 清单不哈希自身",
        "operator_power_policy": "正常结束保持 VM+MCU 供电(§4); 不为清 RAM 重启或断电; "
                                 "异常时操作者用现有 SW1 硬件禁能并如实记录",
        "mechanical_prerequisites": {"bench_continuity": {
            "status": "DECLARED_BY_OPERATOR_AT_APPROVAL",
            "means": "§4 的机械/电气连续性由操作者那句真实批准承载, 不由本计划自证",
            "not_board_evidence": True}},
        "prerequisite_gates": ["bench_continuity"],
        "approval": {"note": "批准账只在最终身份(plan/rows/执行器/BIN 四件)确定后开; "
                             "令牌取操作者真实原话(§8 那一句), 不借旧批准。"},
        "stages": [{
            "id": "S1", "kind": "serial_session", "scenario": "monitor_only",
            "title": "COM5 单会话: 前态核对→apply→零点→IRQ→DIR1→arm→起转→取消N32→完成N8→"
                     "停机→disarm→commit→栈高水位(§5 全序)",
            "port": "COM5",
            "prerequisite_gates": ["bench_continuity"],
            "total_deadline_seconds": 90,
            "motion_cap_seconds": 12,
            "arm_energize_deadline_seconds": 18,
            "cleanup_reserve_seconds": 7,
            # §4 "正常 stop 必须在 start 后 8 s 内发起"。A 的裁定把它保留为**业务规则**而不是
            # cap 的推论, 所以由计划自己申报这个数, 门只判"申报了才核"; 合成夹具不申报 => 不判。
            "normal_stop_send_by_seconds": 8,
            "forbidden_commands": ["config_save", "motor_run", "tmc_set", "fault_reset",
                                   "blackbox_clear", "diag_mode", "pwm_test", "safety_trip"],
            "steps": st}]}


# ---------------------------------------------------------------- DIR0 变体派生(操作者 2026-11-06 现场指令)
# 整包的"方向"不是一条运行期开关: 它写死在第 15/22/27 步的命令串里(motor_dir 1 /
# diag_profile learn N 250 350 **1**)、写死在第 15/16 步的判据里([MOT] dir=1 / dir=1)、
# 写死在行表 13 条 healthy 行与 4 处结构化 {"dir": 1} 里。执行器又在开口前比 plan 字节摘要
# (m8r3_serial_chain_r7f8.py:2442), 所以"换方向"必然产出**新的 plan/rows 身份**, 不能拿旧的
# 批准去跑。本函数只做 1=>0 这一个方向的值翻转, 并且:
#   * 逐条规则按"先具体后笼统"排序, 避免 "dir=1" 这种短串把长串吃掉(本轮踩过一次计数判据的坑);
#   * 翻完做**残留检查**——任何 dir=1 / motor_dir 1 / DIR1 / "dir": 1 / "350 1" 没翻掉就当场拒;
#   * 正向要求: 翻不动(dir=1 原本就 0 处)同样当场拒, 不允许"空翻也报成功";
#   * 说明性文字(_about.derived_shape_rows 与契约表 mot_dir 的 basis)不走文本替换, 显式整条换掉,
#     否则会出现"行已退回逐字节原件、句子还说改成 1"的假陈述;
#   * quota 里 "motor_dir": 1 是**次数**不是方向, 单独断言它没被误翻。
DIR_FLIPS = [("motor_dir 1", "motor_dir 0"),
             ("learn 32 250 350 1", "learn 32 250 350 0"),
             ("learn 8 250 350 1", "learn 8 250 350 0"),
             ("DIR1", "DIR0"),
             ("dir=1", "dir=0")]


def _flip_walk(node, stat):
    if isinstance(node, dict):
        for k in list(node.keys()):
            v = node[k]
            if k == "dir" and v == 1:
                node[k] = 0
                stat["params"] += 1
            elif isinstance(v, str):
                new = v
                for old, rep in DIR_FLIPS:
                    new = new.replace(old, rep)
                if new != v:
                    node[k] = new
                    stat["strings"] += 1
            else:
                _flip_walk(v, stat)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                new = v
                for old, rep in DIR_FLIPS:
                    new = new.replace(old, rep)
                if new != v:
                    node[i] = new
                    stat["strings"] += 1
            else:
                _flip_walk(v, stat)
    return node


MOT_DIR_BASIS_DIR0 = (
    "motor.c:894-917 的七门里没有方向项, motor_dir 不置任何门位 => 仍 0x0。DIR0 变体里这一步的"
    "行表用 **648 B/10807f63f02a 的逐字节原件**(那份就是 dir=0 时抓的), 所以这一格没有值翻转, "
    "比 DIR1 包少一处派生。")

DERIVED_DIR0_READY = (
    "motor_status_ready_dir —— 第 15 步(motor_dir 0 之后): 本包用 648 B/10807f63f02a 的"
    "**逐字节原件 13 行**, 不做任何翻转(归档原件就是抓于 dir=0 时)。DIR1 交付件里这一格才是"
    "翻转形状; 变体把它退回原件, provenance 更强。派生判据: 与 MOT_REAL_00 逐行相等, 不等就拒。")

DERIVED_DIR0_RUNNING = (
    "motor_status_cruise / motor_status_accel / motor_status_armed_ready / motor_status_armed_idle "
    "—— DIR0 变体把这四行的 dir 列由 1 翻成 0。**归档的运行中真实件(1026 B/9005a306999a、"
    "1025 B/57efcc7c90d5、1074 B/98abbf2d18df)全是 dir=1 抓的**, 所以 DIR0 包在运行态这几行是"
    "'声明形状 + 已申报的值翻转', 不是实拍; 与 DIR1 交付件相比这一处 provenance 更弱, 明写在此。")


def _dump(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def to_dir0_variant(plan, rows, repo):
    """DIR0 变体只能从**已交付的 DIR1 字节**派生: 先逐字节证明"本次未翻转的生成结果 == 盘上交付件",
    再翻值。这一步不是形式: 生成器若已漂移而变体照样出件, `derived_from_*_sha256` 就是在替一个
    并不存在的基线作证。证明不了就当场拒。"""
    lineage = {}
    for rel, doc in ((PLAN_REL, plan), (ROWS_REL, rows)):
        p = repo / rel
        if not p.is_file():
            raise SystemExit("REFUSED|找不到已交付的 DIR1 件, 变体没有可比的血统: %s" % rel)
        disk, got = p.read_bytes(), _dump(doc)
        if disk != got:
            raise SystemExit("REFUSED|未翻转的生成结果与交付件字节不同(%s: 盘上 %d B / 本次 %d B) "
                             "=> 先在 --dir 1 上把交付件收口, 不许从漂移的基线派生变体"
                             % (rel, len(disk), len(got)))
        lineage[rel] = hashlib.sha256(disk).hexdigest()
    before = ((repo / PLAN_REL).read_text(encoding="utf-8")
              + (repo / ROWS_REL).read_text(encoding="utf-8"))
    if before.count("dir=1") + before.count('"dir": 1') == 0:
        raise SystemExit("REFUSED|交付件里根本没有 dir=1: 值翻转无从谈起(空翻不许报成功)")
    stat = {"strings": 0, "params": 0}
    _flip_walk(plan, stat)
    _flip_walk(rows, stat)
    s_all = json.dumps(plan, ensure_ascii=False) + json.dumps(rows, ensure_ascii=False)
    for pat in ("dir=1", "motor_dir 1", "DIR1", '"dir": 1', "350 1"):
        if pat in s_all:
            raise SystemExit("REFUSED|DIR0 变体里仍残留 %r: 有落点没翻到, 不许出件" % pat)
    # 说明性文字: 显式整条替换(不走通用翻转), 且要求原句在位。
    dsr = rows.get("_about", {}).get("derived_shape_rows")
    if not isinstance(dsr, list) or not any(l.startswith("motor_status_ready_dir") for l in dsr):
        raise SystemExit("REFUSED|rows._about.derived_shape_rows 里没有 ready_dir 那条说明, "
                         "不敢凭空替换 provenance 文字")
    rows["_about"]["derived_shape_rows"] = [DERIVED_DIR0_READY] + [
        (DERIVED_DIR0_RUNNING if l.startswith("motor_status_armed_ready") else l)
        for l in dsr if not l.startswith("motor_status_ready_dir")]
    hit_basis = 0
    for c in plan.get("arm_gate_mask_contract_steps") or []:
        if c.get("as") == "mot_dir":
            c["basis"] = MOT_DIR_BASIS_DIR0
            hit_basis += 1
    if hit_basis != 1:
        raise SystemExit("REFUSED|契约表里 mot_dir 的 basis 命中 %d 条(应为 1); 不改这句就会留下"
                         "'行已退回逐字节原件、文字还说改成 1'的假陈述" % hit_basis)
    plan["package_variant"] = {"dir": 0,
                               "derived_from_plan_sha256": lineage[PLAN_REL],
                               "derived_from_rows_sha256": lineage[ROWS_REL],
                               "flipped_strings": stat["strings"], "flipped_params": stat["params"],
                               "unchanged_two": ["executor(派生件字节未变)",
                                                 "image(产品一字未动, 未重建)"],
                               "provenance_delta": "ready_dir 退回逐字节原件(更强); "
                                                   "cruise/accel/armed_ready/armed_idle 四行改由"
                                                   "已申报的值翻转给出(更弱, 归档运行中件都是 dir=1)",
                               "asked_by": "操作者 2026-11-06 现场指令: 本次整包方向取 DIR0"}
    if plan["quota"].get("motor_dir") != 1:
        raise SystemExit("REFUSED|quota['motor_dir'] 被翻成了 %r: 那是**次数**不是方向"
                         % plan["quota"].get("motor_dir"))
    if rows["commands"]["motor_status_ready_dir"]["healthy"] != MOT_REAL_00:
        raise SystemExit("REFUSED|ready_dir 翻完不等于 648 B 归档原件逐行: 这一格应当是零翻转, "
                         "不等说明翻转规则过宽")
    print("DIR0_FLIP|strings=%d|params=%d|残留=0|ready_dir==MOT_REAL_00=True|quota.motor_dir=1"
          "|血统=%s/%s" % (stat["strings"], stat["params"],
                           lineage[PLAN_REL][:12], lineage[ROWS_REL][:12]))
    return plan, rows


def main() -> int:
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--dir", choices=["1", "0"], default="1",
                    help="1=交付件(默认, 写原路径且字节必须不变); 0=派生 DIR0 变体(必须 --tag)")
    ap.add_argument("--tag", default="",
                    help="变体文件名后缀, 例如 _dir0; --dir 0 时必填, 且绝不写回交付件路径")
    a = ap.parse_args()
    repo = a.repo.resolve()
    img = repo / IMAGE_REL
    global IMAGE_SHA, IMAGE_BYTES
    if not img.is_file():
        raise SystemExit("REFUSED|在板镜像参照文件不在盘上: %s (镜像摘要只能当场重算, 不许手写占位值)" % img)
    b = img.read_bytes()
    IMAGE_SHA, IMAGE_BYTES = hashlib.sha256(b).hexdigest(), len(b)
    rows = rows_doc()
    plan = plan_doc()
    if a.dir == "0":
        if not a.tag:
            raise SystemExit("REFUSED|--dir 0 必须带 --tag(变体不许写回已交付的 plan/rows 路径)")
        plan, rows = to_dir0_variant(plan, rows, repo)
        out_rows = ROWS_REL.replace(".json", a.tag + ".json")
        out_plan = PLAN_REL.replace(".json", a.tag + ".json")
    else:
        out_rows, out_plan = ROWS_REL, PLAN_REL
    for rel, doc in ((out_rows, rows), (out_plan, plan)):
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        raw_new = _dump(doc)
        if p.is_file() and p.read_bytes() != raw_new:
            raise SystemExit("REFUSED|目标已存在且字节不同: 不覆盖已交付产物(要出新件请换 --tag) %s" % rel)
        p.write_bytes(raw_new)
        raw = p.read_bytes()
        print("WROTE|%s|bytes=%d|sha256=%s" % (rel, len(raw), hashlib.sha256(raw).hexdigest()))
    print("PLAN|dir=%s|steps=%d|business_worst=%s|quota_keys=%d" %
          (a.dir, len(plan["stages"][0]["steps"]), plan["motion_budget"]["business_worst_declared_seconds"],
           len(plan["quota"])))
    print("IMAGE|bytes=%d|sha256=%s|在盘=%s" % (IMAGE_BYTES, IMAGE_SHA, img.is_file()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
