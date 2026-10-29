#!/usr/bin/env bash
# run_m7_reverse.sh - M7 / M7-R1 local reverse controls (outside-repo source copies)
#
#   REV1  motor static checks bypassed                       -> S1 PS19b/PS19c/PS19d FAIL
#   REV2  cal source published without new coefficients       -> CC14 FAIL
#   REV3  R1a: diag gate+reserve taken back out of mot_lock  -> S2 C22f FAIL
#   REV4  R2:  microamp export pushed back through int32      -> CC28/CC29 FAIL
#   REV5  R3:  meter limits root-sum-squared again + soft CLI -> calcalc M7C35/M7C50/M7C51 FAIL
#   REV6  R1b: cfg mutex take failure swallowed again         -> CC27/CC27b FAIL
#
# Every mutation is applied to a copy under the work root; the repository's own
# sources are never edited by this script. Anchors are matched exactly: a moved
# anchor aborts instead of silently reporting a pass.
set -u -o pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/env.sh"
REPO_ROOT="${REPO_ROOT:-$(env_repo_root)}"
SRC_APP="${SRC_APP:-$REPO_ROOT/$(env_app_rel)}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT_DIR="${REV_OUT_DIR-$(env_work_root)/m7-reverse}/$RUN_ID"
OUT_DIR="$(require_outside_repo "$OUT_DIR" "REV_OUT_DIR")" || exit 4
mkdir -p "$OUT_DIR" || exit 4
PY="${PY:-python}"
CALC="$REPO_ROOT/reports/reviews/phase8-motor-m7-current-calibration/m7_calcalc.py"
fail=0

# ------------------------------------------------------------- REV1
MOT="$OUT_DIR/app_motor"
cp -a "$SRC_APP" "$MOT" || { echo "FATAL copy app"; exit 4; }
"$PY" - "$MOT/motor.c" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = """    e = motor_lock_take_checked();
    if (e != RT_EOK) return e;

    e = -RT_EBUSY;"""
new = """    e = motor_lock_take_checked();
    if (e != RT_EOK) return e;

    return RT_EOK; /* REVERSE-1: bypass all static checks */
    e = -RT_EBUSY;"""
assert old in s, "REV1 anchor moved"
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
[ $? -eq 0 ] || { echo "REV1 patch failed"; exit 4; }
SRC_APP="$MOT" PS_OUT_DIR="$OUT_DIR/s1" bash "$HERE/run_stop_result_s1.sh" > "$OUT_DIR/s1_reverse.txt" 2>&1
s1rc=$?
hit1=0
for id in PS19b-static_armed_running_refused PS19c-static_diag_owner_refused PS19d-static_stop_unknown_refused; do
    grep -q "CASE|$id|FAIL|" "$OUT_DIR/s1_reverse.txt" && hit1=$((hit1+1))
done
echo "REV1 s1_rc=$s1rc hits=$hit1/3"
{ [ "$s1rc" -ne 0 ] && [ "$hit1" -eq 3 ]; } || { echo "  REV1 NOT DETECTED"; fail=1; }

# ------------------------------------------------------------- REV2
CFG="$OUT_DIR/app_cfg"
cp -a "$SRC_APP" "$CFG" || { echo "FATAL copy app2"; exit 4; }
"$PY" - "$CFG/project_config.c" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = """    e = cfg_apply_from(&tmp);
    if (e == RT_EOK)
    {
        cfg.cur_offset_mv = offset_mv;
        cfg.cur_gain_v_per_a = gain_v_per_a;
        cfg.cur_cal_source = source;
    }"""
new = """    /* REVERSE-2: publish the new source but keep the old runtime coefficients */
    e = current_adc_set_calibration_ex(cfg.cur_offset_mv, cfg.cur_gain_v_per_a,
                                       (current_adc_cal_source_t)source);
    if (e == RT_EOK)
    {
        cfg.cur_offset_mv = offset_mv;
        cfg.cur_gain_v_per_a = gain_v_per_a;
        cfg.cur_cal_source = source;
    }"""
assert old in s, "REV2 anchor moved"
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
[ $? -eq 0 ] || { echo "REV2 patch failed"; exit 4; }
SRC_APP="$CFG" CC_OUT_DIR="$OUT_DIR/cc" bash "$HERE/run_current_cal_m7.sh" > "$OUT_DIR/cc_reverse.txt" 2>&1
ccrc=$?
hit2=0
grep -q "^CASE|CC14-apply_success_measured|FAIL|" "$OUT_DIR/cc_reverse.txt" && hit2=1
echo "REV2 cc_rc=$ccrc hits=$hit2/1"
{ [ "$ccrc" -ne 0 ] && [ "$hit2" -eq 1 ]; } || { echo "  REV2 NOT DETECTED"; fail=1; }

# ------------------------------------------------------------- REV3
DG="$OUT_DIR/app_diag"
cp -a "$SRC_APP" "$DG" || { echo "FATAL copy app3"; exit 4; }
"$PY" - "$DG/motor.c" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
head = "rt_err_t motor_pwm_grant_to_diag(sp_token_t *out)\n{\n"
i = s.index(head)
j = s.index("\n}\n", i)
# Pre-M7R1 shape: judge the gate (which takes and releases mot_lock internally),
# then reserve outside of any hold.  That is the gap the static contract forbids.
#
# 实测更正(2026-10-29 首轮): 这个形状**不会**让 C22b/C22c 变红 —— 主线程持锁(或持标定
# 事务)时, 诊断线程只是碰巧卡在 gate 内部 motor_get_snapshot() 的那次取锁上, 预约仍然
# 出不来。所以"别人持锁时预约不可得"不能证明"检查与预约同一段被覆盖"。首轮 REV3 因此
# 报 NOT DETECTED(原文保留在 results/m7_reverse_run1.txt), 补的 C22f 直接把观测放到
# **预约那一刻**(最外层窗口认领 owner 时的本线程 mot_lock 持有层数), 才是这条缺口的
# 具名检出。C22b/C22c 保持通过是**正确**的, 不写成检出。
body = (head +
        "    /* REVERSE-3: gate judged outside the lock, reservation taken after it "
        "is gone */\n"
        "    if (out == RT_NULL) return -RT_EINVAL;\n"
        "    if (motor_diag_gate_check() != RT_EOK) return -RT_EPERM;\n"
        "    if (sp_reserve(SP_DIAG, out) != RT_EOK) return -RT_EPERM;\n"
        "    return RT_EOK;")
open(p, "w", encoding="utf-8").write(s[:i] + body + s[j:])
PY
[ $? -eq 0 ] || { echo "REV3 patch failed"; exit 4; }
SRC_APP="$DG" S2_OUT_DIR="$OUT_DIR/s2" bash "$HERE/run_stop_priority_s2.sh" > "$OUT_DIR/s2_reverse.txt" 2>&1
s2rc=$?
hit3=0
for id in C22f-qualification-and-reservation-are-one-hold; do
    grep -q "判 FAIL: CASE|$id|FAIL|" "$OUT_DIR/s2_reverse.txt" && hit3=$((hit3+1))
done
echo "REV3 s2_rc=$s2rc hits=$hit3/1"
{ [ "$s2rc" -ne 0 ] && [ "$hit3" -eq 1 ]; } || { echo "  REV3 NOT DETECTED"; fail=1; }

# ------------------------------------------------------------- REV4
EXP="$OUT_DIR/app_export"
cp -a "$SRC_APP" "$EXP" || { echo "FATAL copy app4"; exit 4; }
"$PY" - "$EXP/current_cal.c" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = """    neg = (d < 0.0);
    if (neg) d = -d;"""
new = """    /* REVERSE-4: push the value back through the 32-bit domain, as M7 did */
    d = (double)(int)d;
    neg = (d < 0.0);
    if (neg) d = -d;"""
assert old in s, "REV4 anchor moved"
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
[ $? -eq 0 ] || { echo "REV4 patch failed"; exit 4; }
SRC_APP="$EXP" CC_OUT_DIR="$OUT_DIR/cc4" bash "$HERE/run_current_cal_m7.sh" > "$OUT_DIR/cc4_reverse.txt" 2>&1
cc4rc=$?
hit4=0
for id in CC28-microamp_export_no_int32_wrap CC29-scaled_dec_range_marker; do
    grep -q "^CASE|$id|FAIL|" "$OUT_DIR/cc4_reverse.txt" && hit4=$((hit4+1))
done
echo "REV4 cc4_rc=$cc4rc hits=$hit4/2"
{ [ "$cc4rc" -ne 0 ] && [ "$hit4" -eq 2 ]; } || { echo "  REV4 NOT DETECTED"; fail=1; }

# ------------------------------------------------------------- REV5
R5="$OUT_DIR/calcalc"
mkdir -p "$R5" || exit 4
cp -f "$CALC" "$R5/m7_calcalc_rev5.py" || exit 4
"$PY" - "$R5/m7_calcalc_rev5.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = "        bound = total + float(p[\"reference_error_mA\"]) + unc"
new = ("        # REVERSE-5a: v1 shape - RSS the bounded meter limits and call that "
       "a bound\n        bound = (total ** 2 + float(p[\"reference_error_mA\"]) ** 2) "
       "** 0.5 + unc")
assert old in s, "REV5a anchor moved"
s = s.replace(old, new)
old2 = """    if not res["usable"]:
        print("REJECT %s: %s" % (res["status"], ", ".join(res["not_applicable_reason"])))
        return 1
    return 0"""
new2 = """    # REVERSE-5b: report the failure but still exit 0 with an applicable command
    return 0"""
assert old2 in s, "REV5b anchor moved"
open(p, "w", encoding="utf-8").write(s.replace(old2, new2))
PY
[ $? -eq 0 ] || { echo "REV5 patch failed"; exit 4; }
PYTHONIOENCODING=utf-8 "$PY" "$R5/m7_calcalc_rev5.py" --selftest > "$OUT_DIR/calcalc_reverse.txt" 2>&1
r5rc=$?
hit5=0
for id in M7C35-bound-exceeds-target-not-pass M7C50-cli-low-span-exits-nonzero M7C51-cli-bound-failure-exits-nonzero; do
    grep -q "^FAIL $id" "$OUT_DIR/calcalc_reverse.txt" && hit5=$((hit5+1))
done
echo "REV5 calcalc_rc=$r5rc hits=$hit5/3"
{ [ "$r5rc" -ne 0 ] && [ "$hit5" -eq 3 ]; } || { echo "  REV5 NOT DETECTED"; fail=1; }

# ------------------------------------------------------------- REV6
CF2="$OUT_DIR/app_cfglock"
cp -a "$SRC_APP" "$CF2" || { echo "FATAL copy app4b"; exit 4; }
"$PY" - "$CF2/project_config.c" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = ("    e = cfg_take_checked();\n"
       "    if (e != RT_EOK) return e;")
new = ("    cfg_take();\n"
       "    e = RT_EOK; /* REVERSE-6: take result swallowed again (v1 shape) */")
assert s.count(old) == 1, "REV6 anchor hits %d (want 1)" % s.count(old)
open(p, "w", encoding="utf-8").write(s.replace(old, new))
PY
[ $? -eq 0 ] || { echo "REV6 patch failed"; exit 4; }
SRC_APP="$CF2" CC_OUT_DIR="$OUT_DIR/cc6" bash "$HERE/run_current_cal_m7.sh" > "$OUT_DIR/cc6_reverse.txt" 2>&1
cc6rc=$?
hit6=0
# 具名检出 = CC27(必须把取锁失败报回调用方) + CC27b(拒绝时一个字段都不改)。
# CC27c(未取锁不 release)在这一形状下**仍然绿**, 这是如实登记不是漏检: v1 的 cfg_take()
# 在 cfg_lock_ok=FALSE 时本身就是空操作, 所以既不会取锁也不会释放, "没有多余 release"当然
# 成立。这条只在"锁存在但 take 返回错误"的真实形状上才有区分力, 由 CC27/CC27b 承担检出。
for id in CC27-cfg_lock_unavailable_rejects CC27b-cfg_lock_unavailable_no_mutation; do
    grep -q "^CASE|$id|FAIL|" "$OUT_DIR/cc6_reverse.txt" && hit6=$((hit6+1))
done
col6="$(grep -c "^CASE|CC27d-cfg_lock_available_publishes|FAIL|" "$OUT_DIR/cc6_reverse.txt")"
echo "REV6 cc6_rc=$cc6rc hits=$hit6/2 collateral_cc27d_fail=$col6"
{ [ "$cc6rc" -ne 0 ] && [ "$hit6" -eq 2 ]; } || { echo "  REV6 NOT DETECTED"; fail=1; }

echo "OUT_DIR=$OUT_DIR"
if [ "$fail" -eq 0 ]; then
    echo "M7 REVERSE RESULT: PASS (all six named failure families detected)"
    exit 0
fi
echo "M7 REVERSE RESULT: FAIL"
exit 1
