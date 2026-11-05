# -*- coding: utf-8 -*-
"""R7F8E 派生执行器生成器(任务书 M8R7F8E §3, 旧执行器一字节不动)。

从 `--repo` 里的旧执行器 `m8r3_serial_chain.py`(219491 B, sha256 33d3f0af8a4c9c33…)按 EDITS 逐条
**精确整串替换**, 每条 find 必须恰好命中一次(命中 0 次或 >1 次当场拒绝并指名哪一条), 产出:

  tools/m8r3_serial_chain_r7f8.py        R7F8 派生执行器(任务书 §3 指定的路径名)
  patches/r7f8e_executor.patch           old->new unified diff(给人读; 重放以本脚本为准)

四项窄适配(E1 有界状态轮询 / E2 命名 JOB_ID 绑定 / E3 取消显式额度 / E4 arm 通电截止)只碰这四件事,
不引入通用脚本语言、任意表达式或任意命令模板。真实设备动作: 0。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import pathlib
import sys

OLD_REL = "reports/reviews/phase8-m8-relative-protection-r3/field/m8r3_serial_chain.py"
ROUND = "reports/reviews/phase8-m8-r7f8-executor-adaptation"
NEW_REL = ROUND + "/tools/m8r3_serial_chain_r7f8.py"
PATCH_REL = ROUND + "/patches/r7f8e_executor.patch"
OLD_SHA = "33d3f0af8a4c9c33dcd4e013b395e4ba990a89e384130f9d0ffe50c71b34e8ca"
OLD_BYTES = 219491

EDITS = []


def edit(name, find, repl):
    EDITS.append((name, find, repl))


# ------------------------------------------------------------------ 0. 派生身份
edit("HEADER",
     '"""M8-R3 ',
     '"""M8-R7F8E 派生执行器(离线适配候选, 逐字节派生自 33d3f0af8a4c9c33 的 M8-R3; 旧文件未动) '
     )

# ------------------------------------------------------------------ E3. 取消的显式额度
edit("QUOTA_TABLE",
     '    ("blackbox_clear", "blackbox_clear"),\n)\n',
     '    ("blackbox_clear", "blackbox_clear"),\n'
     '    # E3: diag_profile learn_cancel <ID> 改作业状态(rel_job_cancel 置 CANCEL_PENDING), 不是只读查询。\n'
     '    # 旧映射漏列属工具覆盖缺口, 不能据此免记账(任务书 §2 learn_cancel 裁定)。\n'
     '    ("diag_profile learn_cancel", "diag_profile_learn_cancel"),\n)\n')

# ------------------------------------------------------------------ E4. arm 通电保持截止
edit("ENERGIZE_CLASS",
     "class ApprovalLedger:\n",
     'class EnergizeBudget(object):\n'
     '    """E4: 从 motor_arm 的**写入尝试之前**起算的通电保持截止, 到成功收到 motor_disarm 为止。\n'
     '\n'
     '    与 MotionBudget 分列且互不替换: 那一条从 motor_start 起算, 只管"转多久"; 这一条管"线圈带电最多\n'
     '    待多久", 所以 arm 后不起转、只查询、只等待同样在约束内, 正常 stop 而仍 armed 也不清除义务。\n'
     '    到期时执行器不假装能替产品断电: 它在下一条业务命令写之前具名停止, 撤能仍走既有固定清理三条;\n'
     '    成功 disarm 才把计时停住。主机软件截止不是物理实时停机保证(人工禁能仍是独立手段)。\n'
     '    """\n'
     '\n'
     '    def __init__(self, clock, cap_seconds):\n'
     '        self.clock = clock\n'
     '        self.cap = float(cap_seconds) if cap_seconds else None\n'
     '        self.t0 = None\n'
     '        self.actual = None\n'
     '\n'
     '    @property\n'
     '    def armed(self):\n'
     '        return self.cap is not None and self.t0 is not None and self.actual is None\n'
     '\n'
     '    def start_attempt(self):\n'
     '        if self.cap is not None and self.t0 is None:\n'
     '            self.t0 = self.clock.monotonic()\n'
     '\n'
     '    def deadline(self):\n'
     '        return self.t0 + self.cap\n'
     '\n'
     '    def remaining(self):\n'
     '        if not self.armed:\n'
     '            return None\n'
     '        return self.cap - (self.clock.monotonic() - self.t0)\n'
     '\n'
     '    def expired(self):\n'
     '        return self.armed and self.clock.monotonic() >= self.deadline()\n'
     '\n'
     '    def close(self):\n'
     '        if self.t0 is not None and self.actual is None:\n'
     '            self.actual = self.clock.monotonic() - self.t0\n'
     '        return self.actual\n'
     '\n'
     '\n'
     "class ApprovalLedger:\n")

# ------------------------------------------------- E1+E2: 轮询门形状与命名 JOB_ID 绑定
edit("R7F8E_HELPERS",
     "def poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture, silent,\n"
     "                      led, clock, budget, results):\n",
     'POLL_GATE_MOTOR = "motor_ramping"\n'
     'POLL_GATE_ROW = "in_progress_row"\n'
     'MOTOR_RAMPING_STATES = (MOTOR_STATE_ENUM["ACCEL"], MOTOR_STATE_ENUM["DECEL"])\n'
     '\n'
     '\n'
     'def poll_gate_of(p):\n'
     '    """没申报 in_progress => 沿用旧的 [MOT] state 门(R7F7 语义逐字节不变); 申报了 => 改用\n'
     '    "响应里那一行的状态字段是否仍在申报的进行中集合里"。E1 要的就是这个分离: learn_status\n'
     '    一行都不带 [MOT] state=, 旧门会把"还没到 DONE"直接判成不一致而当场停。"""\n'
     '    spec = p.get("in_progress")\n'
     '    if not spec:\n'
     '        return POLL_GATE_MOTOR, None, None\n'
     '    return POLL_GATE_ROW, spec["row"], tuple(spec["states"])\n'
     '\n'
     '\n'
     'def poll_observe(gate, in_row, in_states, txt):\n'
     '    """返回(观测到的进行中值, 该行命名组)。判不出/不在集合里 => 第一项 None => 立刻具名停止。\n'
     '    行缺失、同形行出现多条、值在集合外(DONE/FAILED/CANCELLED/TIMEOUT/IDLE)都算"不在进行中" ——\n'
     '    轮询绝不把一个业务终态等成窗口耗尽(那会把"失败了"报成"只是没等到")。"""\n'
     '    if gate == POLL_GATE_MOTOR:\n'
     '        s = ramping_state(txt)\n'
     '        return (s if s in MOTOR_RAMPING_STATES else None), {}\n'
     '    try:\n'
     '        rows = Path(str(txt)).read_text(encoding="utf-8", errors="replace").splitlines()\n'
     '    except OSError:\n'
     '        return None, {}\n'
     '    hits = [h for h in (re.match(in_row, r) for r in rows if r) if h]\n'
     '    if len(hits) != 1:\n'
     '        return None, {}\n'
     '    g = hits[0].groupdict()\n'
     '    val = g.get("st")\n'
     '    return (val if val in in_states else None), g\n'
     '\n'
     '\n'
     '# -------------------------------------------------- E2: 命名作业绑定(不是通用模板系统)\n'
     'JOB_ID_MIN = 1\n'
     'JOB_ID_MAX = 999999999      # 产品: "learn_cancel JOB_ID must be 1..999999999"(diag_relative.c)\n'
     'JOB_ROW_STARTED = (r"^\\[DIAG\\] learn STARTED id=(?P<job_id>[0-9]+) n=(?P<n>[0-9]+) "\n'
     '                   r"win=(?P<lo>[0-9]+)\\.\\.(?P<hi>[0-9]+) dir=(?P<dir>[0-9]+)$")\n'
     'JOB_REF_RE = re.compile(r"^@([a-z][a-z0-9_]{0,31})$")\n'
     'CANCEL_PREFIX_TOKENS = ("diag_profile", "learn_cancel")\n'
     'CANCEL_ROWS = (\n'
     '    ("REQUESTED", r"^\\[DIAG\\] cancel id=(?P<id>[0-9]+) REQUESTED st=(?P<st>[A-Z_]+)"),\n'
     '    ("ALREADY_TERMINAL", r"^\\[DIAG\\] cancel id=(?P<id>[0-9]+) NOT-CANCELLED: already "\n'
     '                         r"(?P<st>[A-Z_]+) why=(?P<why>\\S+)"),\n'
     ')\n'
     '\n'
     '\n'
     'def _rows_of(txt_path):\n'
     '    try:\n'
     '        return Path(str(txt_path)).read_text(encoding="utf-8", errors="replace").splitlines()\n'
     '    except OSError:\n'
     '        raise Stop("JOB_BIND_TEXT_MISSING", str(txt_path))\n'
     '\n'
     '\n'
     'def bind_job_id(st, txt, raw, bindings, led, asname):\n'
     '    """E2: 把**这一步自己**的完整响应里唯一整行的 STARTED 绑成命名作业 ID。\n'
     '    先过原字节门与步骤判据(调用点在 verdict OK 之后), 再发布绑定; 参数(n/lo/hi/dir)必须与\n'
     '    计划里申报的一致 —— 否则"取消的是另一枚作业"这种错绑会在写之前被拒。"""\n'
     '    spec = st.get("job_bind")\n'
     '    if not spec:\n'
     '        return\n'
     '    name = spec["name"]\n'
     '    row = spec.get("row", JOB_ROW_STARTED)\n'
     '    hits = [m for m in (re.match(row, r) for r in _rows_of(txt)) if m]\n'
     '    if len(hits) != 1:\n'
     '        raise Stop("JOB_BIND_NOT_UNIQUE",\n'
     '                   "as=%s name=%s STARTED 行命中=%d" % (asname, name, len(hits)))\n'
     '    g = hits[0].groupdict()\n'
     '    if "job_id" not in g:\n'
     '        raise Stop("JOB_BIND_GROUP_MISSING", "as=%s name=%s 正则里没有组 job_id" % (asname, name))\n'
     '    try:\n'
     '        jid = int(g["job_id"])\n'
     '    except ValueError:\n'
     '        raise Stop("JOB_ID_UNPARSEABLE", "as=%s raw=%r" % (asname, g["job_id"]))\n'
     '    if not JOB_ID_MIN <= jid <= JOB_ID_MAX:\n'
     '        raise Stop("JOB_ID_OUT_OF_RANGE",\n'
     '                   "as=%s id=%d 不在 %d..%d(0 或越界一律拒绝, 不默认 1)" %\n'
     '                   (asname, jid, JOB_ID_MIN, JOB_ID_MAX))\n'
     '    bad = {k: (g.get(k), v) for k, v in (spec.get("params") or {}).items()\n'
     '           if str(g.get(k)) != str(v)}\n'
     '    if bad:\n'
     '        raise Stop("JOB_PARAMS_MISMATCH",\n'
     '                   "as=%s name=%s 受理参数与计划申报不符 %s" % (asname, name, bad))\n'
     '    if name in bindings and bindings[name]["job_id"] != jid:\n'
     '        raise Stop("JOB_BIND_DUPLICATE",\n'
     '                   "name=%s 已绑到 %s(来自 %s), 本步又要绑成 %d => 不覆盖" %\n'
     '                   (name, bindings[name]["job_id"], bindings[name]["from_step"], jid))\n'
     '    bindings[name] = {"job_id": jid, "from_step": asname, "row": hits[0].string.strip(),\n'
     '                      "rx_sha256": sha_of(raw), "params": dict(g)}\n'
     '    led.ev("JOB_ID_BOUND", name=name, job_id=jid, source_step=asname,\n'
     '           rx_sha256=sha_of(raw), row=bindings[name]["row"])\n'
     '\n'
     '\n'
     'def resolve_job_cmd(st, cmd, bindings, asname):\n'
     '    """E2: 只接受固定参数位 `diag_profile learn_cancel @<绑定名>`; 其他任何形态都不生成。\n'
     '    没有 eval/exec/format/shell 展开, 也不从日志、旧会话或 IDLE 状态里捞 ID。"""\n'
     '    ref = st.get("job_cmd")\n'
     '    toks = cmd.split()\n'
     '    if ref is None:\n'
     '        if any(JOB_REF_RE.match(t) for t in toks):\n'
     '            raise Stop("JOB_REF_NOT_DECLARED",\n'
     '                       "as=%s cmd=%s 里有 @引用但没有 job_cmd 声明, 不发送" % (asname, cmd))\n'
     '        if cmd.split(" ")[0] == "diag_profile" and len(toks) > 1 and toks[1] == "learn_cancel":\n'
     '            raise Stop("CANCEL_NEEDS_JOB_REF",\n'
     '                       "as=%s cmd=%s 取消必须走 @绑定(不接受字面 ID, 避免把旧作业当本轮)" %\n'
     '                       (asname, cmd))\n'
     '        return cmd, None\n'
     '    if tuple(toks[:2]) != CANCEL_PREFIX_TOKENS or len(toks) != 3 or not JOB_REF_RE.match(toks[2]):\n'
     '        raise Stop("JOB_CMD_SHAPE",\n'
     '                   "as=%s cmd=%s 不符合固定形式 `diag_profile learn_cancel @<绑定名>`" % (asname, cmd))\n'
     '    name = JOB_REF_RE.match(toks[2]).group(1)\n'
     '    if name != ref:\n'
     '        raise Stop("JOB_CMD_NAME_MISMATCH",\n'
     '                   "as=%s 位置引用 @%s 与步骤申报的 job_cmd=%s 不一致" % (asname, name, ref))\n'
     '    if name not in bindings:\n'
     '        raise Stop("JOB_UNBOUND",\n'
     '                   "as=%s 绑定 %s 还没有来自 STARTED 的 ID => 一条命令都不发" % (asname, name))\n'
     '    b = bindings[name]\n'
     '    out = "%s %s %d" % (CANCEL_PREFIX_TOKENS[0], CANCEL_PREFIX_TOKENS[1], b["job_id"])\n'
     '    return out, {"binding": name, "job_id": b["job_id"], "source_step": b["from_step"],\n'
     '                "source_rx_sha256": b["rx_sha256"], "sent": out}\n'
     '\n'
     '\n'
     'def check_job_expect(st, txt, bindings, asname):\n'
     '    """E2: 同一作业的状态断言行(`id` 组)必须等于绑定的那枚 ID —— 禁止把旧 DONE/旧 FAILED 当本轮结果。"""\n'
     '    for spec in st.get("expect_values", []):\n'
     '        name = spec.get("job_binding")\n'
     '        if not name:\n'
     '            continue\n'
     '        if name not in bindings:\n'
     '            raise Stop("JOB_UNBOUND", "as=%s 状态断言引用未绑定的 %s" % (asname, name))\n'
     '        hits = [m for m in (re.match(spec["row"], r) for r in _rows_of(txt)) if m]\n'
     '        if len(hits) != 1:\n'
     '            raise Stop("JOB_ASSERT_ROW_NOT_UNIQUE",\n'
     '                       "as=%s 命中=%d row=%s" % (asname, len(hits), spec["row"]))\n'
     '        g = hits[0].groupdict()\n'
     '        if "id" not in g:\n'
     '            raise Stop("JOB_ASSERT_GROUP_MISSING", "as=%s 行里没有组 id" % asname)\n'
     '        if int(g["id"]) != bindings[name]["job_id"]:\n'
     '            raise Stop("JOB_ID_MISMATCH",\n'
     '                       "as=%s 行 id=%s 但本轮绑定=%s(来自 %s) => 不是同一枚作业" %\n'
     '                       (asname, g["id"], bindings[name]["job_id"], bindings[name]["from_step"]))\n'
     '\n'
     '\n'
     'def enforce_cancel_contract(st, txt, bindings, led, asname, verdict="OK"):\n'
     '    """E1/E2: 取消只接受 `cancel id=<绑定ID> REQUESTED`。已终态(NOT-CANCELLED: already DONE)\n'
     '    一律登记成"未捕获活动取消"并当场具名停止 —— allowed_outcomes 之类的事后豁免不能把它\n'
     '    洗成取消成功。调用点在通用判据**之前**: 否则这条事实会被降级成 EXPECTATION_NOT_MET。\n'
     '    verdict 非 OK 时不补 CANCEL_RESULT_UNPARSED(REFUSED/BUSINESS_FAILED 由业务层给准确身份)。"""\n'
     '    if not st.get("job_cmd"):\n'
     '        return\n'
     '    name = st["job_cmd"]\n'
     '    want = bindings[name]["job_id"]\n'
     '    rows = _rows_of(txt)\n'
     '    for row_kind, pat in CANCEL_ROWS:\n'
     '        hits = [m for m in (re.match(pat, r) for r in rows) if m]\n'
     '        if not hits:\n'
     '            continue\n'
     '        if len(hits) != 1:\n'
     '            raise Stop("CANCEL_ROW_NOT_UNIQUE",\n'
     '                       "as=%s %s 命中=%d" % (asname, row_kind, len(hits)))\n'
     '        g = hits[0].groupdict()\n'
     '        # 台账字段名不能叫 kind: Ledger.ev(kind, **kw) 的首个位置参数就叫 kind,\n'
     '        # 传 kind=... 会当场 TypeError(本轮第一版正是这样炸在取消步上)。\n'
     '        led.ev("CANCEL_RESULT", row=row_kind, id=int(g["id"]), st=g.get("st", ""),\n'
     '               binding=name, expected_id=want)\n'
     '        if row_kind == "ALREADY_TERMINAL":\n'
     '            raise Stop("CANCEL_NOT_CAPTURED",\n'
     '                       "as=%s id=%s 作业已是 %s(why=%s) => 未捕获活动取消, 不算取消成功, "\n'
     '                       "也不自动再开一枚" % (asname, g["id"], g["st"], g.get("why", "")))\n'
     '        if int(g["id"]) != want:\n'
     '            raise Stop("CANCEL_ID_MISMATCH",\n'
     '                       "as=%s 回包 id=%s 与本轮绑定 %s 不符" % (asname, g["id"], want))\n'
     '        return\n'
     '    if verdict != "OK":\n'
     '        return\n'
     '    raise Stop("CANCEL_RESULT_UNPARSED",\n'
     '               "as=%s 取消响应里没有可判定的受理/未捕获行(REFUSED 线在业务层已先停)" % asname)\n'
     '\n'
     '\n'
     "def poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture, silent,\n"
     "                      led, clock, budget, results, bindings=None):\n")

edit("POLL_GATE_USE",
     '    codes = tuple(p.get("retry_codes", ["PROFILE_NOT_RUNNING"]))\n'
     '    ramping = {MOTOR_STATE_ENUM["ACCEL"], MOTOR_STATE_ENUM["DECEL"]}\n',
     '    codes = tuple(p.get("retry_codes", ["PROFILE_NOT_RUNNING"]))\n'
     '    gate, in_row, in_states = poll_gate_of(p)\n'
     '    max_attempts = p.get("max_attempts")\n'
     '    bindings = bindings if bindings is not None else {}\n')

edit("POLL_ATTEMPT_RESULT",
     '        results.append(dict({"as": asname, "kind": "command_poll", "cmd": cmd, "attempt": n,\n'
     '                             "rx_bytes": nbytes, "rx_sha256": sha_of(raw), "txt": txt}, **v))\n',
     '        results.append(dict({"as": asname, "kind": "command_poll", "cmd": cmd, "attempt": n,\n'
     '                             "poll_gate": gate,\n'
     '                             "rx_bytes": nbytes, "rx_sha256": sha_of(raw), "txt": txt}, **v))\n')

edit("POLL_OK_RETURN_TXT",
     '            led.ev("POLL_OK", **{"as": asname, "attempts": n,\n'
     '                                 "elapsed_s": round(clock.monotonic() - led.t0, 3)})\n'
     '            return v\n',
     '            check_job_expect(st, txt, bindings, asname)\n'
     '            led.ev("POLL_OK", **{"as": asname, "attempts": n, "poll_gate": gate,\n'
     '                                 "elapsed_s": round(clock.monotonic() - led.t0, 3)})\n'
     '            return v, txt\n')

edit("POLL_INPROGRESS_GATE",
     '        state = ramping_state(txt)\n'
     '        if not (v["verdict"] == "STOP" and v.get("code") in codes and state in ramping):\n'
     '            raise Stop(v["code"], json.dumps(v, ensure_ascii=False))\n',
     '        state, groups = poll_observe(gate, in_row, in_states, txt)\n'
     '        jbn = p.get("job_binding")\n'
     '        if jbn and groups.get("id") is not None:\n'
     '            if jbn not in bindings:\n'
     '                raise Stop("JOB_UNBOUND", "as=%s poll 引用未绑定的 %s" % (asname, jbn))\n'
     '            if int(groups["id"]) != bindings[jbn]["job_id"]:\n'
     '                # 首份坏响应就停: 别人家的作业行不能等成"本轮还没到 DONE"。\n'
     '                raise Stop("POLL_JOB_MISMATCH", json.dumps(\n'
     '                    {"as": asname, "row_id": groups["id"],\n'
     '                     "bound_id": bindings[jbn]["job_id"],\n'
     '                     "bound_from": bindings[jbn]["from_step"], "attempt": n},\n'
     '                    ensure_ascii=False))\n'
     '        if not (v["verdict"] == "STOP" and v.get("code") in codes and state is not None):\n'
     '            raise Stop(v["code"], json.dumps(dict(v, poll_gate=gate, observed=state),\n'
     '                                              ensure_ascii=False))\n'
     '        if max_attempts and n >= int(max_attempts):\n'
     '            # E1: 上限同时受次数与时间窗约束; 次数到是另一种结束, 不借口"窗口还没到"继续发。\n'
     '            raise Stop("POLL_ATTEMPTS_EXHAUSTED", json.dumps(\n'
     '                {"as": asname, "attempts": n, "max_attempts": int(max_attempts),\n'
     '                 "last": v.get("code"), "state": state, "poll_gate": gate},\n'
     '                ensure_ascii=False))\n')

edit("POLL_WINDOW_DETAIL",
     '            raise Stop("POLL_WINDOW_EXPIRED", json.dumps(\n'
     '                {"as": asname, "attempts": n, "last": v.get("code"), "state": state,\n'
     '                 "bad": v.get("bad", []), "window_s": window}, ensure_ascii=False))\n',
     '            raise Stop("POLL_WINDOW_EXPIRED", json.dumps(\n'
     '                {"as": asname, "attempts": n, "last": v.get("code"), "state": state,\n'
     '                 "poll_gate": gate, "in_progress_states": list(in_states or []),\n'
     '                 "bad": v.get("bad", []), "window_s": window}, ensure_ascii=False))\n')

edit("POLL_RETRY_EVENT",
     '        led.ev("POLL_RETRY", **{"as": asname, "attempt": n, "code": v.get("code"),\n'
     '                                "state": state, "waiting_s": interval})\n',
     '        led.ev("POLL_RETRY", **{"as": asname, "attempt": n, "code": v.get("code"),\n'
     '                                "state": state, "poll_gate": gate, "waiting_s": interval,\n'
     '                                "rx_sha256": sha_of(raw)})\n')

# ------------------------------------------------------------------ exec_session 接线
edit("SESSION_SIGNATURE",
     "                 approved=None):\n",
     "                 approved=None, energize_cap_seconds=None):\n")

edit("SESSION_LOCALS",
     "    budget = MotionBudget(clock, motion_cap_seconds) if motion_cap_seconds else \\\n",
     "    bindings = {}\n"
     "    energize = EnergizeBudget(clock, energize_cap_seconds)\n"
     "    budget = MotionBudget(clock, motion_cap_seconds) if motion_cap_seconds else \\\n")

edit("SESSION_LOOP_TOP",
     '            asname = st.get("as") or ("step%02d" % idx)\n'
     '            raw = out / ("%02d_%s.rx.bin" % (idx, asname))\n',
     '            asname = st.get("as") or ("step%02d" % idx)\n'
     '            if energize.expired():\n'
     '                # 通电截止到了: 连这一步都不尝试(不是"发完再补"), 清理仍由 finally 走。\n'
     '                raise Stop("ARM_ENERGIZE_DEADLINE_EXCEEDED", json.dumps(\n'
     '                    {"as": asname, "cap_seconds": energize.cap,\n'
     '                     "elapsed_s": round(clock.monotonic() - energize.t0, 3),\n'
     '                     "checked_before_write": True}, ensure_ascii=False))\n'
     '            raw = out / ("%02d_%s.rx.bin" % (idx, asname))\n')

edit("SESSION_JOB_CMD",
     '                led.ev("NEW_LOG_WINDOW", **selected_window)\n',
     '                led.ev("NEW_LOG_WINDOW", **selected_window)\n'
     '            cmd, job_prov = resolve_job_cmd(st, cmd, bindings, asname)\n'
     '            if job_prov:\n'
     '                # 派生命令只进台账与实际发送, 不回写获批的 plan(批准绑定的是计划字节+生成规则)。\n'
     '                led.ev("JOB_CMD_RESOLVED", **job_prov)\n')

edit("SESSION_STEP_DL",
     '            if budget.armed and step_dl <= clock.monotonic():\n'
     '                raise Stop("MOTION_BUDGET_EXHAUSTED", "as=%s want=%ds" % (asname, want))\n',
     '            if energize.armed:\n'
     '                # 申报的等待窗要兑现, 但取会话/通电/运动三者最早的那个截止, 不在 read 或 sleep 里放宽。\n'
     '                step_dl = min(step_dl, energize.deadline())\n'
     '                if energize.expired():\n'
     '                    raise Stop("ARM_ENERGIZE_DEADLINE_EXCEEDED", json.dumps(\n'
     '                        {"as": asname, "want_seconds": want, "cap_seconds": energize.cap,\n'
     '                         "elapsed_s": round(clock.monotonic() - energize.t0, 3)},\n'
     '                        ensure_ascii=False))\n'
     '            if budget.armed and step_dl <= clock.monotonic():\n'
     '                raise Stop("MOTION_BUDGET_EXHAUSTED", "as=%s want=%ds" % (asname, want))\n')

edit("SESSION_ARM_ATTEMPT",
     '                if cmd == "motor_start":\n'
     '                    budget.start_attempt()\n',
     '                if cmd == "motor_start":\n'
     '                    budget.start_attempt()\n'
     '                if cmd == "motor_arm":\n'
     '                    # E4: arm 时限缺失/为 0 就在**真实写之前**具名拒绝, 不允许"arm 了但没人管通电时长"。\n'
     '                    if energize.cap is None:\n'
     '                        raise Stop("ARM_ENERGIZE_DEADLINE_UNDECLARED",\n'
     '                                   "as=%s 阶段没申报 arm_energize_deadline_seconds => 不发 motor_arm" %\n'
     '                                   asname)\n'
     '                    energize.start_attempt()\n'
     '                    led.ev("ENERGIZE_OBLIGATION_SET", cap_seconds=energize.cap,\n'
     '                           t_s=round(clock.monotonic() - led.t0, 3))\n')

edit("SESSION_POLL_CALL",
     '                poll_command_step(port, cmd, st, asname, idx, out, step_dl, transport, fixture,\n'
     '                                  silent, led, clock, budget, results)\n'
     '                continue\n',
     '                _polled, polled_txt = poll_command_step(\n'
     '                    port, cmd, st, asname, idx, out, step_dl, transport, fixture,\n'
     '                    silent, led, clock, budget, results, bindings)\n'
     '                continue\n')

edit("SESSION_TAIL_CAPTURE",
     '            if v["verdict"] != "OK":\n'
     '                raise Stop(v["code"], json.dumps(v, ensure_ascii=False))\n'
     '        if ret is None:\n',
     '            if v["verdict"] != "OK":\n'
     '                raise Stop(v["code"], json.dumps(v, ensure_ascii=False))\n'
     '            # 到这里这条命令是**判据通过**的: 才允许绑定 ID、核对同作业断言, 也才允许认定撤能成立。\n'
     '            bind_job_id(st, txt, raw, bindings, led, asname)\n'
     '            check_job_expect(st, txt, bindings, asname)\n'
     '            if cmd == "motor_disarm" and energize.armed:\n'
     '                led.ev("ENERGIZE_DEADLINE_CLOSED", actual_s=round(energize.close(), 3),\n'
     '                       cap_seconds=energize.cap)\n'
     '        if ret is None:\n')

edit("SESSION_CANCEL_EARLY",
     '            if v["verdict"] == "BYTE_CORRUPT":\n',
     '            if st.get("job_cmd"):\n'
     '                # 取消合同排在**通用判据之前**: "已终态"必须登记成未捕获活动取消(E1),\n'
     '                # 不能被 expect_contains 的 EXPECTATION_NOT_MET 掩盖掉身份; 而业务失败线\n'
     '                # (REFUSED/FAILED)仍由下面的 verdict 门给出准确码。\n'
     '                enforce_cancel_contract(st, txt, bindings, led, asname, v["verdict"])\n'
     '            if v["verdict"] == "BYTE_CORRUPT":\n')

edit("SESSION_CLEANUP_QUOTA",
     'hook, quota_box if plan else None,\n',
     'hook, quota if plan else None,\n')

edit("VALIDATOR_TOOL_DETAIL_NULLSAFE",
     '                                                " ".join(t.get("argv", []))[:70]))\n',
     '                                                " ".join((t or {}).get("argv", []))[:70]))\n')

edit("SESSION_REPORT",
     '                "budget_seconds": elapsed,\n',
     '                "budget_seconds": elapsed,\n'
     '                "energize_cap_seconds": energize.cap,\n'
     '                "energize_seconds_actual": energize.close(),\n'
     '                "job_bindings": {k: v["job_id"] for k, v in bindings.items()},\n')

# ------------------------------------------------------------------ 离线门
edit("PLAN_GATE_FUNCTION",
     "def validate_plan(plan, rows_doc, app_dir):\n",
     'R7F8E_JOB_ROW_GROUP = "st"\n'
     'R7F8E_CANCEL_CMD = "diag_profile learn_cancel"\n'
     '\n'
     '\n'
     'def job_state_names_from_source(app_dir):\n'
     '    """合法作业状态名只认产品源码 rel_job_state_name 的 case 表(注释与文档不算依据)。"""\n'
     '    p = Path(str(app_dir)) / "diag_relative.c"\n'
     '    src = p.read_text(encoding="utf-8", errors="replace")\n'
     '    if "rel_job_state_name" not in src:\n'
     '        raise Stop("R7F8E_JOB_STATE_SOURCE_MISSING", str(p))\n'
     '    body = src.split("rel_job_state_name", 1)[1]\n'
     '    body = body[:body.index("\\n}")] if "\\n}" in body else body\n'
     '    got = sorted(set(re.findall(r\'return "([A-Z_]+)";\', body)))\n'
     '    if not got:\n'
     '        raise Stop("R7F8E_JOB_STATE_SOURCE_EMPTY", str(p))\n'
     '    return got\n'
     '\n'
     '\n'
     'def validate_r7f8e(plan, rows_doc, app_dir):\n'
     '    """E1~E4 的离线门: 全部是"结构上就发不出去/等不到/记不上账"的拒绝, 不判定任何设备结论。"""\n'
     '    out, fails = [], []\n'
     '\n'
     '    def gate(name, sid, ok, detail):\n'
     '        out.append("%s|%s|%s|%s" % (name, sid, "PASS" if ok else "FAIL", detail))\n'
     '        if not ok:\n'
     '            fails.append(name)\n'
     '\n'
     '    names = msh_command_names(app_dir)\n'
     '    legal = job_state_names_from_source(app_dir)\n'
     '    pairs = list(QUOTA_COMMAND_KEYS)\n'
     '    keys = sorted({k for _p, k in pairs})\n'
     '    cancel_pair = (R7F8E_CANCEL_CMD, "diag_profile_learn_cancel")\n'
     '    gate("R7F8E_QUOTA_TABLE", "all",\n'
     '         len(pairs) == 10 and len(keys) == 9 and cancel_pair in pairs,\n'
     '         "pairs=%d keys=%d 取消键=%s" % (len(pairs), len(keys), cancel_pair in pairs))\n'
     '    ghost = [p for p, _k in pairs if p.split(" ")[0] not in names]\n'
     '    gate("R7F8E_QUOTA_PREFIX_REAL", "all", not ghost,\n'
     '         "产品里不存在的前缀=%s(写错会静默永不命中, 于是动作被当成只读)" % ghost)\n'
     '    gate("R7F8E_LEARN_STATUS_READONLY", "all",\n'
     '         quota_key_for_command("diag_profile learn_status") is None\n'
     '         and quota_key_for_command(R7F8E_CANCEL_CMD + " 17") == "diag_profile_learn_cancel",\n'
     '         "learn_status 不得算 learn/cancel; learn 请求与取消分别计数")\n'
     '    gate("R7F8E_JOB_STATE_NAMES", "all", "RUNNING" in legal and "DONE" in legal,\n'
     '         "源码 rel_job_state_name 状态名=%s" % ",".join(legal))\n'
     '    for stg in plan["stages"]:\n'
     '        sid = stg["id"]\n'
     '        steps = stg.get("steps", [])\n'
     '        seen_as = [st.get("as") for st in steps]\n'
     '        bound = {}\n'
     '        for st in steps:\n'
     '            for spec in st.get("expect_values", []):\n'
     '                jb = spec.get("job_binding")\n'
     '                if jb and jb not in bound:\n'
     '                    gate("R7F8E_JOB_REF_SPEC", sid, False,\n'
     '                         "%s 引用未绑定的 %s(绑定必须由更早步骤的 job_bind 产生)"\n'
     '                         % (st.get("as"), jb))\n'
     '            if not st.get("job_bind"):\n'
     '                continue\n'
     '            b = st["job_bind"]\n'
     '            nm = b.get("name")\n'
     '            bad = []\n'
     '            if not nm or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(nm)):\n'
     '                bad.append("绑定名不合法(%r)" % (nm,))\n'
     '            if seen_as.count(st.get("as")) != 1:\n'
     '                bad.append("as 在本阶段不唯一")\n'
     '            try:\n'
     '                rgx = re.compile(b.get("row", JOB_ROW_STARTED))\n'
     '                if "job_id" not in (rgx.groupindex or {}):\n'
     '                    bad.append("正则缺组 job_id")\n'
     '            except re.error as e:\n'
     '                bad.append("STARTED 正则不可编译:%s" % e)\n'
     '            for k, v in (b.get("params") or {}).items():\n'
     '                if not is_count(v) or v < 0:\n'
     '                    bad.append("参数 %s=%r 非非负整数" % (k, v))\n'
     '            if nm in bound:\n'
     '                bad.append("同一阶段里绑定名重复")\n'
     '            gate("R7F8E_JOB_BIND_SPEC", sid, not bad,\n'
     '                 ("%s name=%s" % (st.get("as"), nm)) + ("" if not bad else "|" + ";".join(bad)))\n'
     '            if not bad:\n'
     '                bound[nm] = st.get("as")\n'
     '        for st in steps:\n'
     '            asname = st.get("as") or "?"\n'
     '            cmd = st.get("cmd", "")\n'
     '            toks = cmd.split()\n'
     '            p = st.get("poll") or {}\n'
     '            if p:\n'
     '                bad = []\n'
     '                if quota_key_for_command(cmd) is not None:\n'
     '                    bad.append("被轮询的 cmd=%s 是修改状态的动作 => 轮询只能发预声明的只读查询" % cmd)\n'
     '                if st.get("job_bind"):\n'
     '                    bad.append("job_bind 与 poll 不能同步(STARTED 行只在这一步自己的响应里绑定)")\n'
     '                if "max_attempts" in p and (not is_count(p["max_attempts"]) or p["max_attempts"] < 1):\n'
     '                    bad.append("max_attempts=%r" % (p["max_attempts"],))\n'
     '                for f in ("until_seconds", "interval_seconds"):\n'
     '                    if f in p and (not isinstance(p[f], (int, float)) or p[f] <= 0):\n'
     '                        bad.append("%s=%r" % (f, p[f]))\n'
     '                if p.get("in_progress"):\n'
     '                    spec = p["in_progress"]\n'
     '                    try:\n'
     '                        rgx = re.compile(spec["row"])\n'
     '                        if R7F8E_JOB_ROW_GROUP not in (rgx.groupindex or {}):\n'
     '                            bad.append("缺组 %s" % R7F8E_JOB_ROW_GROUP)\n'
     '                    except (re.error, KeyError, TypeError) as e:\n'
     '                        bad.append("行正则不可用:%s" % e.__class__.__name__)\n'
     '                    states = list(spec.get("states") or [])\n'
     '                    if not states:\n'
     '                        bad.append("states 为空(空集合等于永不重试, 窗口形同不存在)")\n'
     '                    unknown = [x for x in states if x not in legal]\n'
     '                    if unknown:\n'
     '                        bad.append("状态不在源码集合里:%s" % unknown)\n'
     '                    if not p.get("retry_codes"):\n'
     '                        bad.append("retry_codes 为空")\n'
     '                    if not p.get("job_binding"):\n'
     '                        bad.append("in_progress 轮询必须声明 job_binding: 不绑就是"\n'
     '                                   "把别人家的终态等成本轮的结果")\n'
     '                    for spec2 in st.get("expect_values", []):\n'
     '                        if spec2.get("job_binding") and \\\n'
     '                                spec2["job_binding"] != p.get("job_binding"):\n'
     '                            bad.append("一条轮询只允许引用同一枚作业: expect_values=%s "\n'
     '                                       "poll=%s" % (spec2["job_binding"],\n'
     '                                                    p.get("job_binding")))\n'
     '                gate("R7F8E_POLL_SPEC", sid, not bad, "%s%s" % (asname, "" if not bad else "|" + ";".join(bad)))\n'
     '                if p.get("job_binding") and p["job_binding"] not in bound:\n'
     '                    gate("R7F8E_JOB_REF_SPEC", sid, False,\n'
     '                         "%s poll 引用未绑定的 %s" % (asname, p["job_binding"]))\n'
     '            if st.get("job_cmd"):\n'
     '                bad = []\n'
     '                if tuple(toks[:2]) != CANCEL_PREFIX_TOKENS or len(toks) != 3:\n'
     '                    bad.append("cmd=%s 不是固定形式 diag_profile learn_cancel @<绑定名>" % cmd)\n'
     '                else:\n'
     '                    m = JOB_REF_RE.match(toks[2])\n'
     '                    if not m:\n'
     '                        bad.append("参数位不是 @引用: %s" % toks[2])\n'
     '                    elif m.group(1) != st["job_cmd"]:\n'
     '                        bad.append("位置引用 @%s 与 job_cmd=%s 不一致" % (m.group(1), st["job_cmd"]))\n'
     '                    elif m.group(1) not in bound:\n'
     '                        bad.append("绑定 %s 没有由更早步骤的 job_bind 产生" % m.group(1))\n'
     '                    elif bound[m.group(1)] and seen_as.index(bound[m.group(1)]) >= seen_as.index(asname):\n'
     '                        bad.append("来源步骤 %s 不在本步之前" % bound[m.group(1)])\n'
     '                gate("R7F8E_JOB_REF_SPEC", sid, not bad, "%s%s" % (asname, "" if not bad else "|" + ";".join(bad)))\n'
     '        arms = [i for i, st in enumerate(steps) if st.get("cmd") == "motor_arm"]\n'
     '        disarms = [i for i, st in enumerate(steps) if st.get("cmd") == "motor_disarm"]\n'
     '        cap = stg.get("arm_energize_deadline_seconds")\n'
     '        if arms:\n'
     '            bad = []\n'
     '            if not is_count(cap) or cap <= 0:\n'
     '                bad.append("未申报正整数 arm_energize_deadline_seconds(=%r)" % (cap,))\n'
     '            else:\n'
     '                total = int(stg.get("total_deadline_seconds", 0) or 0)\n'
     '                if total and cap > total:\n'
     '                    bad.append("截止 %s 超过本阶段总窗 %s(到不了)" % (cap, total))\n'
     '            if disarms and max(disarms) < min(arms):\n'
     '                bad.append("阶段内的 disarm 排在 arm 之前")\n'
     '            gate("R7F8E_ARM_ENERGIZE", sid, not bad,\n'
     '                 "arm@%s cap=%s disarm@%s%s" % (arms, cap, disarms,\n'
     '                                                "" if not bad else "|" + ";".join(bad)))\n'
     '        elif cap:\n'
     '            gate("R7F8E_ARM_ENERGIZE", sid, False, "申报了 arm 通电截止却没有 motor_arm 步(死条款)")\n'
     '    for f in sorted(set(fails)):\n'
     '        out.append("R7F8E_GATE_SUMMARY|all|FAIL|%s" % f)\n'
     '    if not fails:\n'
     '        out.append("R7F8E_GATE_SUMMARY|all|PASS|fails=0")\n'
     '    return out, len(fails)\n'
     '\n'
     '\n'
     "def validate_plan(plan, rows_doc, app_dir):\n")

edit("PLAN_GATE_CALL",
     "    return out, bad, len(names)\n",
     "    e_out, e_bad = validate_r7f8e(plan, rows_doc, app_dir)\n"
     "    out.extend(e_out)\n"
     "    bad += e_bad\n"
     "    return out, bad, len(names)\n")

# ------------------------------------------------------------------ 调用点
edit("RUN_PLAN_CALL",
     "                                   motion_cap_seconds=cap,\n",
     "                                   motion_cap_seconds=cap,\n"
     "                                   energize_cap_seconds=stg.get(\"arm_energize_deadline_seconds\"),\n")

edit("READONLY_QUERY_CMDS",
     'STREAM_BYTE_DEFECTS = ("DANGER_TEXT_IN_STREAM", "NUL_BYTE_IN_RESPONSE",\n'
     '                       "ILLEGAL_BYTES_IN_RESPONSE")\n',
     'STREAM_BYTE_DEFECTS = ("DANGER_TEXT_IN_STREAM", "NUL_BYTE_IN_RESPONSE",\n'
     '                       "ILLEGAL_BYTES_IN_RESPONSE")\n'
     '# 状态行豁免的白名单原本只看命令首词, 而 `diag_profile` 下既有只读查询也有写动作;\n'
     '# 把首词放进 STATUS_QUERY_CMDS 会让 learn/zero/commit 的 REFUSED 线也能被洗成"安全态披露"。\n'
     '# 因此这里按**完整命令**放行两条真只读查询, 不放宽首词面。\n'
     'READONLY_QUERY_CMDS = ("diag_profile learn_status", "diag_profile show")\n')

edit("STATUS_ALLOW_READONLY_CMDS",
     '                elif head not in STATUS_QUERY_CMDS and st.get("expect_profile") != "RUNNING":\n'
     '                    sa_bad.append("%s/%s:豁免只给**状态查询步**(cmd=%s 不是查询, 也没断言 RUNNING)"\n'
     '                                  % (stg["id"], st.get("as"), head or "无 cmd"))\n',
     '                elif (head not in STATUS_QUERY_CMDS and\n'
     '                      " ".join((st.get("cmd") or "").split()) not in READONLY_QUERY_CMDS and\n'
     '                      st.get("expect_profile") != "RUNNING"):\n'
     '                    sa_bad.append("%s/%s:豁免只给**状态查询步**(cmd=%s 不是查询, 也没断言 RUNNING)"\n'
     '                                  % (stg["id"], st.get("as"), head or "无 cmd"))\n')

edit("CLI_ARG",
     '    ap.add_argument("--motion-cap-seconds", type=int, default=0)\n',
     '    ap.add_argument("--motion-cap-seconds", type=int, default=0)\n'
     '    ap.add_argument("--arm-energize-deadline-seconds", type=int, default=0,\n'
     '                    help="E4: motor_arm 之后的通电保持截止(单会话工装入口; 整包按阶段申报)")\n')

edit("CLI_CALL",
     "                           motion_cap_seconds=a.motion_cap_seconds or None,\n",
     "                           motion_cap_seconds=a.motion_cap_seconds or None,\n"
     "                           energize_cap_seconds=a.arm_energize_deadline_seconds or None,\n")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def build(old_text):
    t = old_text
    for name, find, repl in EDITS:
        n = t.count(find)
        if n != 1:
            raise SystemExit("EDIT_REFUSED|%s 在旧执行器里命中 %d 次(要求恰好 1 次) => 一条都不改" % (name, n))
        t = t.replace(find, repl, 1)
    return t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=pathlib.Path)
    ap.add_argument("--verify", action="store_true", help="重建并与仓库里的派生件逐字节比对(不写仓库)")
    a = ap.parse_args()
    root = a.repo.resolve()
    old_bytes = (root / OLD_REL).read_bytes()
    if len(old_bytes) != OLD_BYTES or sha(old_bytes) != OLD_SHA:
        print("OLD_EXECUTOR_IDENTITY_REFUSED|bytes=%d sha=%s 期望=%d/%s" %
              (len(old_bytes), sha(old_bytes)[:16], OLD_BYTES, OLD_SHA[:16]))
        return 2
    old_text = old_bytes.decode("utf-8")
    new_text = build(old_text)
    new_bytes = new_text.encode("utf-8")
    if a.verify:
        want = (root / NEW_REL).read_bytes()
        same = want == new_bytes
        print("VERIFY|%s|重建=%d B 仓库派生件=%d B|sha=%s" %
              ("PASS" if same else "FAIL", len(new_bytes), len(want), sha(new_bytes)[:16]))
        return 0 if same else 1
    d = root / ROUND
    (d / "tools").mkdir(parents=True, exist_ok=True)
    (d / "patches").mkdir(parents=True, exist_ok=True)
    with io.open(str(d / "tools/m8r3_serial_chain_r7f8.py"), "wb") as f:
        f.write(new_bytes)
    diff = list(difflib.unified_diff(old_text.splitlines(True), new_text.splitlines(True),
                                     fromfile="a/" + OLD_REL, tofile="b/" + NEW_REL))
    with io.open(str(d / "patches/r7f8e_executor.patch"), "wb") as f:
        f.write(("".join(diff)).encode("utf-8"))
    print("BUILT|%s|bytes=%d|sha=%s|edits=%d|+%d 行|patch=%d B" %
          (NEW_REL, len(new_bytes), sha(new_bytes)[:16], len(EDITS),
           new_text.count("\n") - old_text.count("\n"),
           (d / "patches/r7f8e_executor.patch").stat().st_size))
    print("EDITS|" + json.dumps([n for n, _f, _r in EDITS], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
